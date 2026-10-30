#!/usr/bin/env bash
# arm.sh ARM OUT: one H59-family arm in the hold's cluster (EXPERIMENTS.md "H59 pre-registration"
# and "H59 amendment R1"): render it with `k3d-gpu agent manifest --model` in the hold's cluster,
# stage and check it (run.py render), wait for the model list and a 16-token chat, record the
# server's image, command line, CPUs and fit, run the hold's phases (MODE=perf: 60 s idle, 3
# warmup requests, N=1, N=4, N=16 on crescent, TTFT, prefix reuse; MODE=quality: GSM8K items
# 0-49), then delete the arm.
#   REPO  this repository;  G  the campaign dir (models under G/models)
#   CLUSTER  the hold's cluster;  KUBECONFIG  its scratch kubeconfig;  H59_PROFILE  the host
#   DEADLINE  epoch second the hold's arms must end by;  MODE  perf | quality
#   HOLDNUM  the hold's number (the prefix phase's seed);  SERVING  crescent's serving.jsonl
#   H59_PORT  local port (default random);  H59_IDLE_S  idle window (default 60)
# status.json: measured | not_deliverable | not_reached | failed | smoke, with the reason.
set -uo pipefail
ARM=$1 OUT=$2
mkdir -p "${OUT}"
D=${REPO:?}/bench/h59
H=${REPO}/bench/h54
PY=python3
RUN=${D}/run.py
LOAD=${REPO}/bench/llm_load.py
ENERGY=${REPO}/bench/energy.sh
BOOT=900
IDLE=${H59_IDLE_S:-60}
now() { date +%s; }
stamp() { echo "$(date -u +%FT%TZ) ${ARM} $*" | tee -a "${OUT}/arm.log"; }
status() { ${PY} -c 'import json,sys; json.dump({"status": sys.argv[1], "reason": sys.argv[2], "at": sys.argv[3]}, open(sys.argv[4], "w"))' \
    "$1" "$2" "$(date -u +%FT%TZ)" "${OUT}/status.json"; stamp "status $1: $2"; }
field() { ${PY} "${RUN}" field "${ARM}" "$1"; }
pfield() { ${PY} "${RUN}" profile "${H59_PROFILE:?}" | sed -n "s/^$1=//p"; }
NODE=k3d-${CLUSTER:?}-server-0
NSP=k3d-gpu-agent SVC=$(field svc) SPORT=$(field port) API=$(field api)
PHASES=$(pfield phases) RULE=$(pfield load_rule)
MODEL=gemma
PORT=${H59_PORT:-$(( 19000 + RANDOM % 1000 ))}
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

# ---- render, stage, check (run.py render): agent.yaml, deploy.yaml, check.json, fit.json --------
M=${OUT}/deploy.yaml
if ! why=$(${PY} "${RUN}" render "${ARM}" "${OUT}" 2>&1); then
    status failed "$(tail -1 <<< "${why}")"
    exit 1
fi
kubectl apply -f "${M}" > "${OUT}/apply.log" 2>&1 || { status failed "apply failed: $(tail -1 "${OUT}/apply.log")"; exit 1; }

# ---- readiness: the model list and a 16-token chat ------------------------------------------
t_apply=$(now)
limit=$(( BOOT < DEADLINE - t_apply - 300 ? BOOT : DEADLINE - t_apply - 300 ))
base="http://127.0.0.1:${PORT}${API}"
ready=""
while [ $(( $(now) - t_apply )) -lt "${limit}" ]; do
    if [ -z "${pf}" ] || ! kill -0 "${pf}" 2>/dev/null; then
        pf=""
        if kubectl -n "${NSP}" get pods -l "app=${SVC}" -o jsonpath='{.items[*].status.phase}' 2>/dev/null | grep -q Running; then
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
pod=$(kubectl -n "${NSP}" get pods -l "app=${SVC}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
ctr=$(kubectl -n "${NSP}" get pod "${pod}" -o jsonpath='{.spec.containers[0].name}' 2>/dev/null)
# cpu_usec: the k3d node container's cgroup CPU time, or nothing when it cannot be read.
cpu_usec() {
    local id
    id=$(docker inspect -f '{{.Id}}' "${NODE}" 2>/dev/null) || return 1
    awk '/^usage_usec/{print $2}' "/sys/fs/cgroup/system.slice/docker-${id}.scope/cpu.stat" 2>/dev/null
}
teardown() {
    cleanup_pf
    kubectl -n "${NSP}" logs "${pod}" --all-containers > "${OUT}/server.log" 2>&1
    kubectl -n "${NSP}" describe pod "${pod}" > "${OUT}/describe.txt" 2>&1
    kubectl delete -f "${M}" --wait=true --timeout=300s > "${OUT}/delete.log" 2>&1
    # OVMS's compiled-model cache leaves with the arm, so no later boot reads it (H54's teardown).
    [ "${ARM}" != O ] || docker exec "${NODE}" sh -c 'rm -rf /var/apb-models/.ov_cache/*' >> "${OUT}/delete.log" 2>&1
    # The node's CPU reads idle (< 1 CPU-s/s over 10 s) before the next arm, at most 180 s; a
    # cgroup that cannot be read ends the wait (recorded).
    local a b
    for _ in $(seq 18); do
        a=$(cpu_usec) && [ -n "${a}" ] || { echo "idle wait: node cgroup not readable" >> "${OUT}/delete.log"; break; }
        sleep 10
        b=$(cpu_usec) && [ -n "${b}" ] || break
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
# The server's command line, the CPU set it got (the P-core balloon on a hybrid host), the ISA it
# sees and its threads' affinity.
kubectl -n "${NSP}" exec "${pod}" -c "${ctr}" -- sh -c \
    'echo "cmdline $(tr "\0" " " < /proc/1/cmdline)"; echo "cpuset $(cat /sys/fs/cgroup/cpuset.cpus.effective)"; grep Cpus_allowed_list /proc/1/status; echo "avx512f $(grep -c avx512f /proc/cpuinfo) amx $(grep -c amx_tile /proc/cpuinfo) avx_vnni $(grep -c avx_vnni /proc/cpuinfo) nproc $(nproc)"; echo "server threads by affinity:"; for t in /proc/1/task/*; do grep -s Cpus_allowed_list "${t}/status" | cut -f2; done | sort | uniq -c' > "${OUT}/cpus.txt" 2>&1
# O: the graph.pbtxt the --pull init container wrote from this hold's arguments.
[ "${ARM}" != O ] || kubectl -n "${NSP}" exec "${pod}" -c "${ctr}" -- cat "/models/OpenVINO/gemma-4-26b-a4b-it-int4-ov/graph.pbtxt" > "${OUT}/graph.pbtxt" 2>&1

if [ -n "${SMOKE:-}" ]; then
    # Wiring smoke (discarded, disclosed): one streamed 16-token request with ignore_eos, one chat
    # with chat_template_kwargs and the prefix phase's first agent, then the arm is deleted.
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
W0="" W1=""
idle() {  # idle [SUFFIX]: the 60 s idle window (RAPL); on crescent also the void rule's CPU window
    local t0
    t0=$(date +%s.%N)
    bash "${ENERGY}" --zone intel-rapl:0 --interval 5 --json "${OUT}/energy-idle${1:+-$1}.json" -- sleep "${IDLE}" 2>> "${OUT}/arm.log"
    W0=${t0} W1=$(date +%s.%N)
    echo "{\"phase\": \"idle${1:+-$1}\", \"t0\": ${W0}, \"t1\": ${W1}}" >> "${OUT}/phases.jsonl"
}
load() {  # load NAME REQUESTS CONCURRENCY MAX_TOKENS [extra llm_load args]
    local name=$1 n=$2 c=$3 mt=$4
    shift 4
    bash "${ENERGY}" --zone intel-rapl:0 --interval 5 --json "${OUT}/energy-${name}.json" -- \
        ${PY} "${LOAD}" --base-url "${base}" --model "${MODEL}" --requests "${n}" --concurrency "${c}" --warmup 0 \
        --max-tokens "${mt}" --temperature 0 --unique-prompts --timeout 1800 --label "H59 ${ARM} ${name}" \
        --out "${OUT}/load-${name}.json" "$@" 2>> "${OUT}/arm.log"
}
phase() {  # phase NAME REQUESTS CONCURRENCY MAX_TOKENS [extra]: run and record; on crescent (H54's
    # rule, amendment R4) a void phase is re-run once after a fresh idle window
    local name=$1 t0 t1 v try
    for try in 1 2; do
        t0=$(date +%s.%N)
        load "$@"
        t1=$(date +%s.%N)
        if [ "${RULE}" != h54-void ]; then
            echo "{\"phase\": \"${name}\", \"t0\": ${t0}, \"t1\": ${t1}}" >> "${OUT}/phases.jsonl"
            return
        fi
        v=$(${PY} "${H}/h54.py" void "${SERVING:?}" "${t0}" "${t1}" "${W0}" "${W1}")
        echo "{\"phase\": \"${name}\", \"try\": ${try}, \"t0\": ${t0}, \"t1\": ${t1}, \"w0\": ${W0}, \"w1\": ${W1}, \"check\": ${v}}" >> "${OUT}/phases.jsonl"
        case ${v} in *'"void": true'*) [ "${try}" = 1 ] && { mv "${OUT}/load-${name}.json" "${OUT}/load-${name}-void1.json"; idle "${name}"; continue; } ;; esac
        return
    done
}
has() { [[ ",${PHASES}," == *",$1,"* ]]; }
if [ "${MODE}" = perf ]; then
    idle
    load warmup 3 1 256 --ignore-eos
    phase n1 8 1 256 --ignore-eos
    phase n4 16 4 256 --ignore-eos
    if has n16; then phase n16 32 16 256 --ignore-eos; fi
    phase ttft 4 1 16 --prompt "$(cat "${H}/ttft-prompt.txt")"
    t0=$(date +%s.%N)
    ${PY} "${RUN}" prefix "${base}" "${MODEL}" "$(( 5900 + ${HOLDNUM:-0} ))" "${OUT}/prefix.json" >> "${OUT}/arm.log" 2>&1
    echo "{\"phase\": \"prefix\", \"t0\": ${t0}, \"t1\": $(date +%s.%N)}" >> "${OUT}/phases.jsonl"
    # Measured when the N=4 phase completed a request (h59.py scores the completed ones); a server
    # that stopped answering after readiness is failed (H54 amendment R3).
    if ${PY} -c 'import json,sys; s=json.load(open(sys.argv[1]))["phases"][0]["samples"]; sys.exit(not any(x.get("ok") for x in s))' \
        "${OUT}/load-n4.json" 2>/dev/null; then status measured "perf phases recorded"
    else status failed "server stopped answering: the N=4 phase completed no request"; fi
else
    t0=$(date +%s.%N)
    ${PY} "${H}/h54.py" gsm8k "${base}" "${MODEL}" "${H}/gsm8k-test-0-199.jsonl" "${OUT}/quality.json" 0-49 >> "${OUT}/arm.log" 2>&1
    echo "{\"phase\": \"quality\", \"t0\": ${t0}, \"t1\": $(date +%s.%N)}" >> "${OUT}/phases.jsonl"
    if ${PY} -c 'import json,sys; q=json.load(open(sys.argv[1])); sys.exit(not (q.get("n") and q.get("errors", 0) < q["n"]))' \
        "${OUT}/quality.json" 2>/dev/null; then status measured "quality recorded"
    else status failed "quality not recorded, or every item errored"; fi
fi
teardown
exit 0
