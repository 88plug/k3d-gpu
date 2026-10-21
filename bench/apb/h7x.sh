#!/usr/bin/env bash
# h7x.sh: EXPERIMENTS.md H7x, H7 (OVMS vs EAT's vLLM CPU engine at matched 16-bit weights)
# run to its pre-registered protocol on a second host: H2's workloads and energy method,
# blocks A B B A A B B A A B (A = OVMS, B = vLLM with EAT's chart defaults), then one block C
# (B with --enforce-eager dropped). One server runs at a time. Per block, after 1 decode and
# 1 prefill warm-up (excluded): bench/energy.sh over a 20 s model-loaded idle, then over each
# workload through bench/llm_load.py: decode 5 x (default ~30-token prompt, 256 out),
# prefill 5 x (unique ~1936-token prompt, 16 out), conc4 8 x 128 out at 4 in flight; all
# greedy, ignore_eos, unique prompts. bench/apb/h7x.py scores the run.
#
# Environment:
#   APB_MODELS     dir with OpenVINO/Qwen3-4B-fp16-ov and Qwen/Qwen3-4B (bench/xhost/fetch_models.py) (required)
#   APB_EAT_CHART  EAT checkout's core/helm-charts/vllm at 0d3438d (required)
#   APB_OUT        run directory parent (default ./apb-runs)
#   H7X_CPUS       CPUs each engine is sized to (default 16, H7's P-core balloon)
#   H7X_NODE_CPUS  cpuset for the node container after `up` (default none)
#   H7X_ORDER      block order (default "A B B A A B B A A B C")
#   H7X_VLLM_MEM   memory limit for B and C (default: the overlay's 16Gi)
#   K3D_GPU_CLUSTER  cluster name (default apb-h7x); K3D_GPU_* pass through to `up`
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
K=${REPO}/scripts/k3d-gpu
LOAD=${REPO}/bench/llm_load.py
ENERGY=${REPO}/bench/energy.sh
CPUS=${H7X_CPUS:-16}
NODE_CPUS=${H7X_NODE_CPUS:-}
ORDER=${H7X_ORDER:-"A B B A A B B A A B C"}
READY_S=${H7X_READY_S:-900}   # per engine start (the first vLLM start pulls its image)
VLLM_MEM=${H7X_VLLM_MEM:-}    # B/C memory limit; empty keeps integrations/eat/vllm-values.yaml (16Gi)
export K3D_GPU_CLUSTER=${K3D_GPU_CLUSTER:-apb-h7x}
if [ "${H7X_PRINT_CONFIG:-0}" = 1 ]; then echo "cluster=${K3D_GPU_CLUSTER} cpus=${CPUS} node_cpus=${NODE_CPUS} order=${ORDER} vllm_mem=${VLLM_MEM}"; exit 0; fi
M=${APB_MODELS:?set APB_MODELS to the dir of local model copies}
CHART=${APB_EAT_CHART:?set APB_EAT_CHART to EAT core/helm-charts/vllm}
OUT=${APB_OUT:-${PWD}/apb-runs}/h7x-$(date +%Y%m%d-%H%M%S)
mkdir -p "${OUT}"
export KUBECONFIG=${OUT}/kubeconfig
t0=$(date +%s)
stamp() { echo "== $1 $(date -u +%FT%TZ) (+$(( $(date +%s) - t0 ))s)"; }
PF=
cleanup() { [ -n "${PF}" ] && kill "${PF}" 2>/dev/null; stamp cleanup; k3d cluster delete "${K3D_GPU_CLUSTER}" >/dev/null 2>&1; stamp deleted; }
trap cleanup EXIT
trap 'exit 143' TERM INT HUP
nodes() { docker ps --filter "label=k3d.cluster=${K3D_GPU_CLUSTER}" --filter "label=k3d.role=server" --format '{{.Names}}'; }
stage() {  # stage <local dir> <path under /var/apb-models>
    local n
    for n in $(nodes); do
        docker exec "${n}" mkdir -p "/var/apb-models/$2"
        docker cp "${M}/$1/." "${n}:/var/apb-models/$2/"
        docker exec "${n}" rm -rf "/var/apb-models/$2/.cache"
        docker exec "${n}" chown -R 5000:5000 /var/apb-models
        docker exec "${n}" chmod -R a+rX /var/apb-models
    done
}

stamp render
# H7's OVMS arm has no draft (it predates the CPU Fast Draft default of #116).
K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-4B-fp16-ov K3D_GPU_AGENT_CPUS=${CPUS} K3D_GPU_AGENT_DRAFT=off \
    "${K}" agent manifest > "${OUT}/agent.yaml" 2> "${OUT}/agent.err" || { cat "${OUT}/agent.err"; exit 1; }
python3 "${HERE}/stage.py" ovms "${OUT}/agent.yaml" "${OUT}/ovms.yaml"
python3 "${HERE}/stage.py" values "${CHART}/xeon-values.yaml" "${REPO}/integrations/eat/vllm-values.yaml" 6 "${OUT}/values.yaml"
helm template vllm-qwen-3-4b-cpu "${CHART}" --values "${OUT}/values.yaml" --set LLM_MODEL_ID=Qwen/Qwen3-4B --set cpu="${CPUS}" ${VLLM_MEM:+--set memory="${VLLM_MEM}"} \
    --set global.monitoring=false --set svcmonitor.enabled=false --set ingress.enabled=false --set route.enabled=false > "${OUT}/eat.yaml"
python3 "${HERE}/stage.py" vllm "${OUT}/eat.yaml" "${OUT}/vllm.yaml" Qwen3-4B 32768
# C: the same Deployment and Service, renamed, with --enforce-eager removed (the only change).
python3 - "${OUT}/vllm.yaml" "${OUT}/vllm-c.yaml" <<'EOF'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
n = 0
for d in docs:
    d["metadata"]["name"] = d["metadata"]["name"].replace("vllm-qwen-3-4b-cpu", "vllm-c")
    if d.get("kind") == "Deployment":
        for c in d["spec"]["template"]["spec"]["containers"]:
            for key in ("args", "command"):
                if c.get(key) and "--enforce-eager" in c[key]:
                    c[key] = [a for a in c[key] if a != "--enforce-eager"]; n += 1
            # With compilation on, vLLM writes its compile cache under VLLM_CACHE_ROOT, by default
            # $HOME/.cache/vllm, on the chart's read-only root; point it at the chart's own
            # writable /root/.cache/vllm volume.
            c.setdefault("env", []).append({"name": "VLLM_CACHE_ROOT", "value": "/root/.cache/vllm"})
        # C's own selector label, so the two Deployments never share pods.
        d["spec"]["selector"].setdefault("matchLabels", {})["h7x-arm"] = "c"
        d["spec"]["template"]["metadata"].setdefault("labels", {})["h7x-arm"] = "c"
    elif d.get("kind") == "Service":
        d["spec"].setdefault("selector", {})["h7x-arm"] = "c"
assert n == 1, f"--enforce-eager found {n} times"
yaml.safe_dump_all(docs, open(sys.argv[2], "w"), sort_keys=False)
EOF
[ -s "${OUT}/vllm-c.yaml" ] || { echo "C manifest not rendered"; exit 1; }
diff <(grep -c -- --enforce-eager "${OUT}/vllm.yaml") <(echo 1) >/dev/null || { echo "B has no --enforce-eager"; exit 1; }
if [ "${H7X_RENDER_ONLY:-0}" = 1 ]; then trap - EXIT; ls "${OUT}"; exit 0; fi
stamp up
K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true timeout 300 "${K}" up > "${OUT}/up.log" 2>&1 \
    || { echo "up failed"; tail -20 "${OUT}/up.log"; exit 1; }
if [ -n "${NODE_CPUS}" ]; then
    for n in $(nodes); do docker update --cpuset-cpus "${NODE_CPUS}" "${n}" >/dev/null || { echo "node cpuset not applied"; exit 1; }; done
    echo "node cpuset ${NODE_CPUS}" | tee "${OUT}/node-cpus.txt"
fi
stamp stage
stage OpenVINO/Qwen3-4B-fp16-ov OpenVINO/Qwen3-4B-fp16-ov
stage Qwen/Qwen3-4B Qwen3-4B
kubectl apply -f "${OUT}/ovms.yaml" -f "${OUT}/vllm.yaml" >/dev/null
kubectl -n default scale deploy/vllm-qwen-3-4b-cpu --replicas=0 >/dev/null
kubectl -n k3d-gpu-agent scale deploy/ovms --replicas=0 >/dev/null
c_ok=1
kubectl apply -f "${OUT}/vllm-c.yaml" >/dev/null 2>"${OUT}/vllm-c.err" || { c_ok=0; cat "${OUT}/vllm-c.err"; }
kubectl -n default scale deploy/vllm-c --replicas=0 >/dev/null 2>&1

SENT="The harbour master logged each ship that entered the bay, noting its flag, its cargo, the depth of water under its keel and the hour the pilot boarded. "
PREFILL=$(python3 -c 'import sys; print(sys.argv[1] * 56)' "${SENT}")
cur=
down_all() {
    kubectl -n default scale deploy/vllm-qwen-3-4b-cpu --replicas=0 >/dev/null 2>&1
    kubectl -n default scale deploy/vllm-c --replicas=0 >/dev/null 2>&1
    kubectl -n k3d-gpu-agent scale deploy/ovms --replicas=0 >/dev/null 2>&1
    kubectl -n default wait --for=delete pod -l app.kubernetes.io/name=vllm --timeout=180s >/dev/null 2>&1
    kubectl -n k3d-gpu-agent wait --for=delete pod -l app=ovms --timeout=180s >/dev/null 2>&1
}
up_engine() {  # up_engine A|B|C: every other server scaled to 0 first, so only one runs
    [ "${cur}" = "$1" ] && return 0
    [ -n "${PF}" ] && { kill "${PF}" 2>/dev/null; PF=; }
    down_all
    case $1 in
        A) kubectl -n k3d-gpu-agent scale deploy/ovms --replicas=1 >/dev/null
           kubectl -n k3d-gpu-agent rollout status deploy/ovms --timeout="${READY_S}s" >/dev/null || return 1
           kubectl -n k3d-gpu-agent port-forward svc/ovms 18000:8000 >/dev/null 2>&1 &
           PF=$! BASE=http://127.0.0.1:18000/v3 MODEL=Qwen3-4B-fp16-ov NS=k3d-gpu-agent ;;
        B|C) d=vllm-qwen-3-4b-cpu; s=vllm-qwen-3-4b-cpu-service
           [ "$1" = C ] && { d=vllm-c; s=vllm-c-service; }
           kubectl -n default scale "deploy/${d}" --replicas=1 >/dev/null
           kubectl -n default rollout status "deploy/${d}" --timeout="${READY_S}s" >/dev/null || return 1
           kubectl -n default port-forward "svc/${s}" 18001:80 >/dev/null 2>&1 &
           PF=$! BASE=http://127.0.0.1:18001/v1 MODEL=Qwen/Qwen3-4B NS=default ;;
    esac
    sleep 3
    cur=$1
    local n pid
    n=$(nodes | head -1)
    # shellcheck disable=SC2016 # runs in the node's shell
    pid=$(docker exec "${n}" sh -c 'for p in /proc/[0-9]*; do c=$(tr "\0" " " < $p/cmdline 2>/dev/null); case $c in *ovms*--rest_port*|*vllm*--port*) echo ${p#/proc/}; break;; esac; done')
    [ -n "${pid}" ] && echo "$1 $(docker exec "${n}" grep Cpus_allowed_list "/proc/${pid}/status")" >> "${OUT}/cpus.txt"
    kubectl -n "${NS}" get pods -o jsonpath='{range .items[*]}{.spec.containers[0].image}{" "}{.status.containerStatuses[0].imageID}{" "}{.spec.containers[0].args}{"\n"}{end}' >> "${OUT}/image.txt"
}
load() {  # load <block> <engine> <workload> llm_load args...
    local b=$1 e=$2 w=$3; shift 3
    "${ENERGY}" --json "${OUT}/b${b}-${e}-${w}-energy.json" -- \
        python3 "${LOAD}" --base-url "${BASE}" --model "${MODEL}" --temperature 0 --ignore-eos --unique-prompts \
        --warmup 0 --label "h7x-${e}-b${b}-${w}" --out "${OUT}/b${b}-${e}-${w}.json" "$@" >/dev/null 2>>"${OUT}/energy.log"
}
i=0
for e in ${ORDER}; do
    i=$((i + 1))
    if [ "${e}" = C ] && [ "${c_ok}" = 0 ]; then echo "block ${i} C not run: manifest refused" | tee -a "${OUT}/notes.txt"; continue; fi
    stamp "block ${i} engine ${e} start"
    if ! up_engine "${e}"; then
        echo "block ${i} engine ${e} did not come up" | tee -a "${OUT}/notes.txt"
        { kubectl get pods -A -o wide; kubectl describe pods -n default; kubectl describe pods -n k3d-gpu-agent
          for p in $(kubectl get pods -n default -o name; kubectl get pods -n k3d-gpu-agent -o name); do
              for prev in --previous ""; do
                  echo "--- logs ${p} ${prev}"; kubectl logs -n default "${p}" --all-containers --tail=80 ${prev} 2>/dev/null \
                      || kubectl logs -n k3d-gpu-agent "${p}" --all-containers --tail=80 ${prev}; done; done
        } >> "${OUT}/notes.txt" 2>&1
        [ "${e}" = C ] && continue
        exit 2
    fi
    stamp "block ${i} engine ${e} ready"
    # H7X_SMOKE=1: bring each engine up once and send no request (setup check, never scored).
    [ "${H7X_SMOKE:-0}" = 1 ] && continue
    python3 "${LOAD}" --base-url "${BASE}" --model "${MODEL}" --temperature 0 --max-tokens 256 --ignore-eos --unique-prompts \
        --requests 1 --warmup 0 --label warm >/dev/null 2>&1
    python3 "${LOAD}" --base-url "${BASE}" --model "${MODEL}" --temperature 0 --prompt "${PREFILL}" --max-tokens 16 --ignore-eos \
        --unique-prompts --requests 1 --warmup 0 --label warm >/dev/null 2>&1
    "${ENERGY}" --json "${OUT}/b${i}-${e}-idle-energy.json" -- sleep 20 2>>"${OUT}/energy.log"
    load "${i}" "${e}" decode --max-tokens 256 --requests 5 --concurrency 1
    load "${i}" "${e}" prefill --prompt "${PREFILL}" --max-tokens 16 --requests 5 --concurrency 1
    load "${i}" "${e}" conc4 --max-tokens 128 --requests 8 --concurrency 4
    stamp "block ${i} engine ${e} done"
done
git -C "${REPO}" rev-parse HEAD > "${OUT}/commit.txt"
docker inspect --format '{{.Config.Image}} {{.Image}}' "$(nodes | head -1)" > "${OUT}/node-image.txt"
[ "${H7X_SMOKE:-0}" = 1 ] || python3 "${HERE}/h7x.py" "${OUT}" | tee "${OUT}/verdict.json"
stamp finished
