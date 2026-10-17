#!/usr/bin/env bash
# h7.sh: EXPERIMENTS.md H7, OVMS vs EAT's vLLM CPU engine at matched 16-bit weights.
#
#   bench/apb/h7.sh
#
# One checkpoint, Qwen/Qwen3-4B: OVMS serves OpenVINO/Qwen3-4B-fp16-ov (an fp16 IR
# exported from it), vLLM CPU serves it in bf16 with EAT's chart defaults
# (vllm/xeon-values.yaml at 0d3438d + integrations/eat/vllm-values.yaml, KV 6 GiB),
# both with 16 CPUs of the P-core balloon. Only one server runs at a time, in
# blocks A B B A A B (A = OVMS). Per block: bench/llm_load.py with 1 warm-up and 2
# decode requests (128 tokens, ignore_eos, unique prompts), then 1 TTFT request
# (~417-token unique prompt, 4 tokens). Models come from local copies staged
# into the node (bench/apb/stage.py), as in bench/apb/arm.sh.
#
# Environment:
#   APB_MODELS     dir with Qwen3-4B-fp16-ov and Qwen3-4B (hf download --local-dir) (required)
#   APB_EAT_CHART  EAT checkout's core/helm-charts/vllm at 0d3438d (required)
#   APB_OUT        run directory parent (default ./apb-runs)
#   K3D_GPU_CLUSTER  cluster name (default apb-h7)
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
K=${REPO}/scripts/k3d-gpu
LOAD=${REPO}/bench/llm_load.py
M=${APB_MODELS:?set APB_MODELS to the dir of local model copies}
CHART=${APB_EAT_CHART:?set APB_EAT_CHART to EAT core/helm-charts/vllm}
export K3D_GPU_CLUSTER=${K3D_GPU_CLUSTER:-apb-h7}
OUT=${APB_OUT:-${PWD}/apb-runs}/h7-$(date +%Y%m%d-%H%M%S)
mkdir -p "${OUT}"
export KUBECONFIG=${OUT}/kubeconfig
t0=$(date +%s)
stamp() { echo "== $1 $(date +%T) (+$(( $(date +%s) - t0 ))s)"; }
PF=
cleanup() { [ -n "${PF}" ] && kill "${PF}" 2>/dev/null; stamp cleanup; k3d cluster delete "${K3D_GPU_CLUSTER}" >/dev/null 2>&1; stamp deleted; }
trap cleanup EXIT
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

stamp up
K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true timeout 240 "${K}" up > "${OUT}/up.log" 2>&1 \
    || { echo "up failed"; tail -20 "${OUT}/up.log"; exit 1; }
stamp stage
K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-4B-fp16-ov K3D_GPU_AGENT_CPUS=16 \
    "${K}" agent manifest > "${OUT}/agent.yaml" 2> "${OUT}/agent.err" || { cat "${OUT}/agent.err"; exit 1; }
python3 "${HERE}/stage.py" ovms "${OUT}/agent.yaml" "${OUT}/ovms.yaml"
python3 "${HERE}/stage.py" values "${CHART}/xeon-values.yaml" "${REPO}/integrations/eat/vllm-values.yaml" 6 "${OUT}/values.yaml"
helm template vllm-qwen-3-4b-cpu "${CHART}" --values "${OUT}/values.yaml" --set LLM_MODEL_ID=Qwen/Qwen3-4B --set cpu=16 \
    --set global.monitoring=false --set svcmonitor.enabled=false --set ingress.enabled=false --set route.enabled=false > "${OUT}/eat.yaml"
python3 "${HERE}/stage.py" vllm "${OUT}/eat.yaml" "${OUT}/vllm.yaml" Qwen3-4B 32768
stage Qwen3-4B-fp16-ov OpenVINO/Qwen3-4B-fp16-ov
stage Qwen3-4B Qwen3-4B
kubectl apply -f "${OUT}/ovms.yaml" -f "${OUT}/vllm.yaml" >/dev/null
kubectl -n default scale deploy/vllm-qwen-3-4b-cpu --replicas=0
kubectl -n k3d-gpu-agent scale deploy/ovms --replicas=0

PROMPT=$(python3 -c 'print("The harbour master logged each ship that entered the bay, noting its flag, its cargo, the depth of water under its keel and the hour the pilot boarded. " * 12)')
cur=
up_engine() {  # up_engine A|B: scale the other server to 0 first, so only one runs
    [ "${cur}" = "$1" ] && return 0
    [ -n "${PF}" ] && { kill "${PF}" 2>/dev/null; PF=; }
    if [ "$1" = A ]; then
        kubectl -n default scale deploy/vllm-qwen-3-4b-cpu --replicas=0 >/dev/null
        kubectl -n default wait --for=delete pod -l app.kubernetes.io/name=vllm --timeout=180s >/dev/null 2>&1
        kubectl -n k3d-gpu-agent scale deploy/ovms --replicas=1 >/dev/null
        kubectl -n k3d-gpu-agent rollout status deploy/ovms --timeout=600s >/dev/null || return 1
        kubectl -n k3d-gpu-agent port-forward svc/ovms 18000:8000 >/dev/null 2>&1 &
        PF=$! BASE=http://127.0.0.1:18000/v3 MODEL=Qwen3-4B-fp16-ov NS=k3d-gpu-agent
    else
        kubectl -n k3d-gpu-agent scale deploy/ovms --replicas=0 >/dev/null
        kubectl -n k3d-gpu-agent wait --for=delete pod -l app=ovms --timeout=180s >/dev/null 2>&1
        kubectl -n default scale deploy/vllm-qwen-3-4b-cpu --replicas=1 >/dev/null
        kubectl -n default rollout status deploy/vllm-qwen-3-4b-cpu --timeout=600s >/dev/null || return 1
        kubectl -n default port-forward svc/vllm-qwen-3-4b-cpu-service 18001:80 >/dev/null 2>&1 &
        PF=$! BASE=http://127.0.0.1:18001/v1 MODEL=Qwen/Qwen3-4B NS=default
    fi
    sleep 3
    cur=$1
    local n pid
    n=$(nodes | head -1)
    # shellcheck disable=SC2016 # runs in the node's shell
    pid=$(docker exec "${n}" sh -c 'for p in /proc/[0-9]*; do c=$(tr "\0" " " < $p/cmdline 2>/dev/null); case $c in *ovms*--rest_port*|*vllm*--port*) echo ${p#/proc/}; break;; esac; done')
    [ -n "${pid}" ] && echo "$1 $(docker exec "${n}" grep Cpus_allowed_list "/proc/${pid}/status")" >> "${OUT}/cpus.txt"
    kubectl -n "${NS}" get pods -o jsonpath='{range .items[*]}{.spec.containers[0].image}{" "}{.status.containerStatuses[0].imageID}{"\n"}{end}' >> "${OUT}/image.txt"
}
i=0
for e in A B B A A B; do
    i=$((i + 1))
    stamp "block ${i} engine ${e} start"
    up_engine "${e}" || { echo "engine ${e} did not come up"; kubectl get pods -A | grep -v kube-system; exit 2; }
    stamp "block ${i} engine ${e} ready"
    python3 "${LOAD}" --base-url "${BASE}" --model "${MODEL}" --max-tokens 128 --ignore-eos --unique-prompts \
        --requests 2 --warmup 1 --label "h7-${e}-b${i}-decode" --out "${OUT}/b${i}-${e}-decode.json" >/dev/null
    python3 "${LOAD}" --base-url "${BASE}" --model "${MODEL}" --prompt "${PROMPT}" --max-tokens 4 --unique-prompts \
        --requests 1 --warmup 0 --label "h7-${e}-b${i}-ttft" --out "${OUT}/b${i}-${e}-ttft.json" >/dev/null
    python3 - "${OUT}/b${i}-${e}-decode.json" "${OUT}/b${i}-${e}-ttft.json" <<'EOF'
import json, sys
d, t = (json.load(open(p)) for p in sys.argv[1:3])
ds, ts = ([s for ph in x["phases"] for s in ph["samples"]] for x in (d, t))
print("  decode tok/s", [round(s["decode_tokens_per_s"] or 0, 2) for s in ds], "out", [s["output_tokens"] for s in ds],
      "| ttft s", [round(s["ttft_s"] or 0, 3) for s in ts], "prompt", [s.get("prompt_tokens") for s in ts],
      "errors", [s["error"] for s in ds + ts if s["error"]])
EOF
done
git -C "${REPO}" rev-parse HEAD > "${OUT}/commit.txt"
docker inspect --format '{{.Config.Image}} {{.Image}}' "$(nodes | head -1)" > "${OUT}/node-image.txt"
stamp finished
