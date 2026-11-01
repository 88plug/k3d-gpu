#!/usr/bin/env bash
# arm.sh ARM OUT: deploy one H57d arm into the hold's cluster, wait for it, run the hold's
# phases (MODE=perf: idle, warmup, N=1, N=4, TTFT; MODE=quality: GSM8K items 0-49), record
# everything under OUT, then delete the arm (EXPERIMENTS.md "H57d pre-registration" and
# "H57d amendment R1").
#   REPO      this repository's checkout;  G  the campaign dir (models under G/models)
#   CLUSTER   the hold's cluster; KUBECONFIG its scratch kubeconfig
#   DEADLINE  epoch second the hold's arms must end by;  MODE  perf | quality
# status.json: measured | not_deliverable | not_reached | failed, with the reason.
set -uo pipefail
ARM=$1 OUT=$2
mkdir -p "${OUT}"
D=${REPO:?}/bench/h57d
H=${REPO}/bench/h54
PY=python3
LOAD=${REPO}/bench/llm_load.py
ENERGY=${REPO}/bench/energy.sh
BOOT=900
now() { date +%s; }
stamp() { echo "$(date -u +%FT%TZ) ${ARM} $*" | tee -a "${OUT}/arm.log"; }
status() { ${PY} -c 'import json,sys; json.dump({"status": sys.argv[1], "reason": sys.argv[2], "at": sys.argv[3]}, open(sys.argv[4], "w"))' \
    "$1" "$2" "$(date -u +%FT%TZ)" "${OUT}/status.json"; stamp "status $1: $2"; }
field() { ${PY} "${D}/h57d.py" field "${ARM}" "$1"; }
NODE=k3d-${CLUSTER:?}-server-0
NSP=$(field ns) SVC=$(field svc) SPORT=$(field port) API=$(field api) DEPLOY=$(field deploy)
MODEL=gemma
PORT=$(( 19000 + RANDOM % 1000 ))
pf=""
pf_loop() {  # kubectl port-forward can drop on a long phase: respawn it
    trap 'kill "${k}" 2>/dev/null; exit 0' TERM
    while :; do
        kubectl -n "${NSP}" port-forward "svc/${SVC}" "${PORT}:${SPORT}" >> "${OUT}/pf.log" 2>&1 &
        k=$!
        wait "${k}"
        echo "port-forward exited $(date -u +%FT%TZ)" >> "${OUT}/pf.log"
        sleep 1
    done
}
cleanup_pf() { [ -z "${pf}" ] || kill "${pf}" 2>/dev/null; pf=""; }
trap cleanup_pf EXIT

need=900
[ "${MODE:?}" = quality ] && need=600
left=$(( DEADLINE - $(now) ))
if [ "${left}" -lt "${need}" ]; then status not_reached "${left} s of the hold left (< ${need})"; exit 0; fi
stamp "start ${MODE} (${left} s of the hold left)"

# ---- render --------------------------------------------------------------------------------
M=${OUT}/deploy.yaml
case ${DEPLOY} in
    k3d-gpu)
        # K3D_GPU_CLUSTER: `agent manifest` reads the node's hybrid label and allocatable CPU
        # through context k3d-${K3D_GPU_CLUSTER}; unset, that is k3d-gpu, which the hold's
        # kubeconfig does not hold, and Od renders with no P-core label and 1 CPU (H57d
        # amendment R2).
        K3D_GPU_CLUSTER=${CLUSTER} K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_MODEL=$(field model) K3D_GPU_AGENT_MODEL_NAME=${MODEL} K3D_GPU_AGENT_DRAFT=off \
            K3D_GPU_AUTOQUANT=off "${REPO}/scripts/k3d-gpu" agent manifest > "${OUT}/agent.yaml" 2> "${OUT}/agent.err" \
            && ${PY} "${REPO}/bench/apb/stage.py" ovms "${OUT}/agent.yaml" "${OUT}/staged.yaml" \
            && ${PY} "${D}/h57d.py" od-ro "${OUT}/staged.yaml" "${M}" ;;
    render) ${PY} "${D}/h57d.py" render "${ARM}" "${M}" ;;
esac
[ -s "${M}" ] || { status failed "render failed"; exit 1; }
kubectl apply -f "${M}" > "${OUT}/apply.log" 2>&1 || { status failed "apply failed: $(tail -1 "${OUT}/apply.log")"; exit 1; }

# ---- readiness: the model list and a 16-token chat ------------------------------------------
t_apply=$(now)
limit=$(( BOOT < DEADLINE - t_apply - 300 ? BOOT : DEADLINE - t_apply - 300 ))
base="http://127.0.0.1:${PORT}${API}"
ready=""
while [ $(( $(now) - t_apply )) -lt "${limit}" ]; do
    if [ -z "${pf}" ] || ! kill -0 "${pf}" 2>/dev/null; then
        pf=""
        if kubectl -n "${NSP}" get pods -o jsonpath='{.items[*].status.phase}' 2>/dev/null | grep -q Running; then
            pf_loop &
            pf=$!
            sleep 3
        fi
    fi
    if [ -n "${pf}" ] && curl -fsS --max-time 10 "${base}/models" > "${OUT}/models.json" 2>/dev/null \
       && curl -fsS --max-time 120 "${base}/chat/completions" -H 'Content-Type: application/json' \
            -d "{\"model\":\"${MODEL}\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Say hello.\"}]}" \
            > "${OUT}/hello.json" 2>/dev/null; then
        ready=1
        break
    fi
    sleep 10
done
t_ready=$(now)
echo "{\"apply\": ${t_apply}, \"ready\": ${t_ready}, \"boot_s\": $(( t_ready - t_apply )), \"limit_s\": ${limit}}" > "${OUT}/boot.json"
kubectl get pods -A -o wide > "${OUT}/pods.txt" 2>&1
pod=$(kubectl -n "${NSP}" get pods -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
ctr=$(kubectl -n "${NSP}" get pod "${pod}" -o jsonpath='{.spec.containers[0].name}' 2>/dev/null)
teardown() {
    cleanup_pf
    kubectl -n "${NSP}" logs "${pod}" --all-containers > "${OUT}/server.log" 2>&1
    kubectl -n "${NSP}" describe pod "${pod}" > "${OUT}/describe.txt" 2>&1
    kubectl delete -f "${M}" --wait=true --timeout=300s > "${OUT}/delete.log" 2>&1
    # The node's CPU reads idle (< 1 CPU-s/s over 10 s) before the next arm, at most 180 s.
    cg=/sys/fs/cgroup/system.slice/docker-$(docker inspect -f '{{.Id}}' "${NODE}").scope/cpu.stat
    for _ in $(seq 18); do
        a=$(awk '/^usage_usec/{print $2}' "${cg}"); sleep 10; b=$(awk '/^usage_usec/{print $2}' "${cg}")
        [ $(( (b - a) / 10000000 )) -lt 1 ] && break
    done
}
if [ -z "${ready}" ]; then
    if [ "${limit}" -lt "${BOOT}" ]; then status not_reached "not ready within the ${limit} s the hold left (boot limit ${BOOT} s)"
    else status not_deliverable "no model list and 16-token chat within ${BOOT} s"; fi
    teardown
    exit 0
fi
stamp "ready after $(( t_ready - t_apply )) s"
kubectl -n "${NSP}" get pod "${pod}" -o jsonpath='{.spec.containers[0].image} {.status.containerStatuses[0].imageID}' > "${OUT}/image.txt"
kubectl -n "${NSP}" get pod "${pod}" -o json > "${OUT}/pod.json"
# The CPU set the server got (the P-core balloon) and the ISA it sees.
kubectl -n "${NSP}" exec "${pod}" -c "${ctr}" -- sh -c \
    'echo "cpuset $(cat /sys/fs/cgroup/cpuset.cpus.effective)"; grep Cpus_allowed_list /proc/self/status; echo "avx512f $(grep -c avx512f /proc/cpuinfo) amx $(grep -c amx_tile /proc/cpuinfo) nproc $(nproc)"; echo "server threads by affinity:"; for t in /proc/1/task/*; do grep -s Cpus_allowed_list "${t}/status" | cut -f2; done | sort | uniq -c' > "${OUT}/cpus.txt" 2>&1

if [ -n "${SMOKE:-}" ]; then
    # Wiring smoke (discarded, disclosed): one streamed 16-token request with ignore_eos and one
    # chat with chat_template_kwargs, then the arm is deleted. No scored request.
    ${PY} "${LOAD}" --base-url "${base}" --model "${MODEL}" --requests 1 --concurrency 1 --warmup 0 --max-tokens 16 \
        --ignore-eos --unique-prompts --out "${OUT}/smoke-load.json" >> "${OUT}/arm.log" 2>&1
    curl -sS --max-time 300 "${base}/chat/completions" -H 'Content-Type: application/json' \
        -d "{\"model\":\"${MODEL}\",\"max_tokens\":16,\"chat_template_kwargs\":{\"enable_thinking\":false},\"messages\":[{\"role\":\"user\",\"content\":\"Say hello.\"}]}" \
        > "${OUT}/smoke-kwargs.json" 2>&1
    status smoke "ready; wiring checks in smoke-*.json"
    teardown
    exit 0
fi

# ---- phases ----------------------------------------------------------------------------------
mark() { echo "{\"phase\": \"$1\", \"t0\": $2, \"t1\": $3}" >> "${OUT}/phases.jsonl"; }
load() {  # load NAME REQUESTS CONCURRENCY MAX_TOKENS [extra llm_load args]
    local name=$1 n=$2 c=$3 mt=$4 t0 t1
    shift 4
    t0=$(date +%s.%N)
    bash "${ENERGY}" --zone intel-rapl:0 --interval 5 --json "${OUT}/energy-${name}.json" -- \
        ${PY} "${LOAD}" --base-url "${base}" --model "${MODEL}" --requests "${n}" --concurrency "${c}" --warmup 0 \
        --max-tokens "${mt}" --temperature 0 --unique-prompts --timeout 1800 --label "H57d ${ARM} ${name}" \
        --out "${OUT}/load-${name}.json" "$@" 2>> "${OUT}/arm.log"
    t1=$(date +%s.%N)
    mark "${name}" "${t0}" "${t1}"
}
if [ "${MODE}" = perf ]; then
    t0=$(date +%s.%N)
    bash "${ENERGY}" --zone intel-rapl:0 --interval 5 --json "${OUT}/energy-idle.json" -- sleep 60 2>> "${OUT}/arm.log"
    mark idle "${t0}" "$(date +%s.%N)"
    load warmup 3 1 256 --ignore-eos
    load n1 8 1 256 --ignore-eos
    load n4 16 4 256 --ignore-eos
    load ttft 4 1 16 --prompt "$(cat "${H}/ttft-prompt.txt")"
    if [ -s "${OUT}/load-n1.json" ]; then status measured "perf phases recorded"; else status failed "n1 not recorded"; fi
else
    t0=$(date +%s.%N)
    ${PY} "${H}/h54.py" gsm8k "${base}" "${MODEL}" "${H}/gsm8k-test-0-199.jsonl" "${OUT}/quality.json" 0-49 >> "${OUT}/arm.log" 2>&1
    mark quality "${t0}" "$(date +%s.%N)"
    if [ -s "${OUT}/quality.json" ]; then status measured "quality recorded"; else status failed "quality not recorded"; fi
fi
teardown
exit 0
