#!/usr/bin/env bash
# run.sh <k3d-gpu checkout> <cluster> <out dir> <cap min> <model-add args...>
# B70 end state through EAS on crescent, inside the bench lock the caller holds, with the
# owner's B70 servers already stopped by the caller: fresh 'k3d-gpu up', 'k3d-gpu eat up'
# (EAT 2.0 on EAS v1.2.0 GA, the release profile), the Ready wait, 'k3d-gpu eas model add
# <args>', then the evidence: the model pod on one B70 (gpu.intel.com/xe in its limits, the
# node's allocation, XPU lines in the engine log, per-card VRAM), the gateway's /v1/models,
# "What is 17 times 23?" through the gateway (HTTP 200, 391), and 'k3d-gpu eat check --model'
# (chat, tool call, guard, sandbox) with the added model. Then 'eat down' and 'k3d-gpu down'.
# Each step runs once, timed, rc recorded in steps.tsv. The cluster is deleted and inotify
# put back on every exit. Secrets: the LiteLLM master key goes to curl as a 0600 header
# file only, never on an argument list or into a log.
set -uo pipefail
WT=$1 CLUSTER=$2 OUT=$3 CAP_MIN=$4
shift 4
MODEL_ADD=("$@")
K="${WT}/scripts/k3d-gpu"
export PATH="${HOME}/k3dgpu-bench/bin:${HOME}/.local/bin:${PATH}"
mkdir -p "${OUT}/steps" && cd "${OUT}" || exit 1
umask 077
export K3D_GPU_CLUSTER="${CLUSTER}"
export K3D_GPU_STATE_DIR="${OUT}/state"
unset KUBECONFIG K3D_GPU_KUBECONFIG K3D_GPU_K3S_ARGS K3D_GPU_BALLOONS K3D_GPU_NODE_LABELS K3D_GPU_VENDORS HF_TOKEN
KCFG="${K3D_GPU_STATE_DIR}/kubeconfig-${CLUSTER}"
EAS_STATE="${K3D_GPU_STATE_DIR}/eas-${CLUSTER}"
T0=$(date +%s)
DEADLINE=$(( T0 + CAP_MIN * 60 ))
STEPS="${OUT}/steps.tsv"
TO=$(command -v gnutimeout || command -v timeout)  # GNU timeout: uutils exits 124 when it passes a signal on
SEC="${OUT}/.sec"
INOTIFY_WANT=(8192 1048576)
: > "${STEPS}"
kc() { KUBECONFIG="${KCFG}" kubectl "$@"; }
now() { date +%s; }
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "${OUT}/run.log"; }
vram() { curl -s -m 10 localhost:9101/metrics | awk '/^xe_vram_used_bytes/ { match($0, /bdf="[^"]+"/); b = substr($0, RSTART + 5, RLENGTH - 6); match($0, /card="[^"]+"/); printf "%s %s %.2f;", b, substr($0, RSTART + 6, RLENGTH - 7), $2 / 2^30 }'; }
strip() { sed 's/\x1b\[[0-9;]*m//g'; }
declare -A RES=()
res() { RES[$1]=$2; echo "$1=$2" >> "${OUT}/results.txt"; }
: > "${OUT}/results.txt"

kill_bastions() {
    local id
    for id in $(docker ps -q 2>/dev/null); do
        if docker inspect -f '{{range .Mounts}}{{.Source}} {{end}}' "${id}" 2>/dev/null | grep -qF "${EAS_STATE}"; then
            log "stopping bastion container ${id} (mounts ${EAS_STATE})"
            docker rm -f "${id}" >/dev/null 2>&1 || true
        fi
    done
}

# step <name> <ceiling s> <reserve s> <cmd...>
step() {
    local name=$1 ceil=$2 reserve=$3 t0 rc left lim
    shift 3
    left=$(( DEADLINE - $(now) - reserve ))
    if [ "${left}" -le 60 ]; then
        log "step ${name}: not started, the cap leaves ${left}s after the teardown reserve"
        printf '%s\tNOT-RUN\t0\tcap\n' "${name}" >> "${STEPS}"
        return 125
    fi
    lim=$(( ceil < left ? ceil : left ))
    log "step ${name}: start (timeout ${lim}s): $*"
    t0=$(now)
    "${TO}" --kill-after=30 "${lim}" "$@" > "${OUT}/steps/${name}.log" 2>&1
    rc=$?
    log "step ${name}: rc=${rc} wall=$(( $(now) - t0 ))s"
    printf '%s\t%s\t%s\n' "${name}" "${rc}" "$(( $(now) - t0 ))" >> "${STEPS}"
    [ "${rc}" != 124 ] && [ "${rc}" != 137 ] || kill_bastions
    return "${rc}"
}

INOTIFY_WAS=""
PF=""
finish() {
    local rc=$?
    [ -z "${SAMPLER:-}" ] || kill "${SAMPLER}" 2>/dev/null
    rm -rf "${SEC}"
    kill_bastions
    if k3d cluster get "${CLUSTER}" >/dev/null 2>&1; then
        log "exit trap: deleting cluster ${CLUSTER}"
        "${TO}" 600 k3d cluster delete "${CLUSTER}" >> "${OUT}/run.log" 2>&1 || true
    fi
    if [ -n "${INOTIFY_WAS}" ]; then
        set -- ${INOTIFY_WAS}
        sudo -n sysctl -q -w "fs.inotify.max_user_instances=$1" "fs.inotify.max_user_watches=$2" \
            && log "inotify put back to $1 $2" || log "could not put inotify back to $1 $2"
    fi
    log "vram at exit: $(vram)"
    log "run.sh done (rc ${rc})"
}
trap finish EXIT
trap 'exit 143' TERM INT

k3d cluster get "${CLUSTER}" >/dev/null 2>&1 && { log "cluster ${CLUSTER} exists already: not starting"; exit 3; }
{
    echo "k3d-gpu commit: $(git -C "${WT}" rev-parse HEAD) ($(git -C "${WT}" status --porcelain | wc -l) local changes)"
    echo "k3d-gpu version: $("${K}" version 2>/dev/null | strip | head -1)"
    echo "tools: $(k3d version | head -1); $(kubectl version --client 2>/dev/null | head -1); docker $(docker version --format '{{.Server.Version}}')"
    echo "host: $(hostname) $(uname -r); $(lscpu | sed -n 's/^Model name: *//p')"
    echo "model add: ${MODEL_ADD[*]}"
    echo "start: $(date -u +%FT%TZ) cap ${CAP_MIN} min"
} > "${OUT}/header.txt"
( while :; do
      printf '%s load1=%s mem_avail_kib=%s vram=%s\n' "$(date -u +%FT%TZ)" "$(cut -d' ' -f1 /proc/loadavg)" \
          "$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)" "$(vram)" >> "${OUT}/samples.txt"
      sleep 60
  done ) & SAMPLER=$!
log "vram before up: $(vram)"

INOTIFY_WAS="$(cat /proc/sys/fs/inotify/max_user_instances) $(cat /proc/sys/fs/inotify/max_user_watches)"
step 0b-inotify 60 2400 sudo -n sysctl -w "fs.inotify.max_user_instances=${INOTIFY_WANT[0]}" \
    "fs.inotify.max_user_watches=${INOTIFY_WANT[1]}"

step 1-up 1800 2400 "${K}" up || { log "RESULT FAIL (k3d-gpu up)"; exit 1; }
strip < steps/1-up.log | grep -a 'Accelerators schedulable' | tail -1 | tee accelerators.txt
kc get nodes -o jsonpath='{.items[0].status.allocatable}' > allocatable.json
log "allocatable $(cat allocatable.json)"

step 2-eat-up 6000 1500 "${K}" eat up || { log "RESULT FAIL (eat up)"; exit 1; }
grep -a 'PLAY RECAP' -A3 steps/2-eat-up.log | strip | grep -a 'failed=' > play-recap.txt
cp -f "${EAS_STATE}/choices.txt" "${EAS_STATE}/overlay.txt" "${EAS_STATE}/pins.resolved.json" "${OUT}/" 2>/dev/null

step 3-wait 1800 1500 env KUBECONFIG="${KCFG}" kubectl wait --for=condition=Ready pod -A \
    --field-selector=status.phase!=Succeeded --timeout=30m
kc get pods -A -o wide > pods-after-eat-up.txt 2>&1

# The gateway: eg-gateway's LoadBalancer IP (k3s ServiceLB), Host litellm.<base_domain_name>.
DOMAIN=$(sed -n 's/^base_domain_name:[[:space:]]*["'\'']\{0,1\}\([A-Za-z0-9.-]*\).*$/\1/p' \
    "${EAS_STATE}/k3d-gpu-overrides.yaml" "${EAS_STATE}/eas/env/k3d-gpu/global_config.yaml" 2>/dev/null | tail -1)
DOMAIN=${DOMAIN:-solutions.ai}
GWIP=$(kc -n envoy-gateway-system get svc -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
    -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}' 2>/dev/null)
HOST="litellm.${DOMAIN}"
log "gateway ${GWIP:-none} host ${HOST}"
mkdir -p "${SEC}" && chmod 700 "${SEC}"
kc -n litellm get secret litellm-master-key -o jsonpath='{.data.master_key}' | base64 -d \
    | awk '{printf "Authorization: Bearer %s\n", $0}' > "${SEC}/auth.h"
[ -s "${SEC}/auth.h" ] || log "no LiteLLM master key read"
gw() { curl -sk -m "${2:-60}" --resolve "${HOST}:443:${GWIP}" -H @"${SEC}/auth.h" "https://${HOST}$1" "${@:3}"; }
gw /v1/models 30 -o models-before.json -w '%{http_code}\n' > models-before.status
log "models before add: $(jq -c '[.data[].id]' models-before.json 2>/dev/null) (http $(cat models-before.status))"

log "vram before model add: $(vram)"
step 4a-model-add-dry-run 900 1200 "${K}" eas model add "${MODEL_ADD[@]}" --dry-run
res model_add_dry_rc "$?"
step 4b-model-add 3600 1200 "${K}" eas model add "${MODEL_ADD[@]}"
res model_add_rc "$?"
step 4c-model-list 300 1200 "${K}" eas model list
res model_list_rc "$?"
PODWAIT=120; [ "${RES[model_add_rc]}" = 0 ] || PODWAIT=300

# The model pod: the pod whose containers ask for gpu.intel.com/xe.
gpu_pods() { kc get pods -A -o json | jq -r '.items[] | select([.spec.containers[].resources.limits // {} | has("gpu.intel.com/xe")] | any) | "\(.metadata.namespace) \(.metadata.name)"'; }
t=$(now)
while :; do
    read -r PNS PNAME <<< "$(gpu_pods | head -1)"
    if [ -n "${PNAME:-}" ] && [ "$(kc -n "${PNS}" get pod "${PNAME}" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)" = True ]; then break; fi
    [ $(( $(now) - t )) -lt "${PODWAIT}" ] && [ "$(now)" -lt $(( DEADLINE - 1200 )) ] || break
    sleep 20
done
log "model pod ${PNS:-none}/${PNAME:-none} ready-wait $(( $(now) - t ))s"
kc get pods -A -o wide > pods-after-model-add.txt 2>&1
gpu_pods > gpu-pods.txt
if [ -n "${PNAME:-}" ]; then
    kc -n "${PNS}" get pod "${PNAME}" -o json > model-pod.json
    kc -n "${PNS}" describe pod "${PNAME}" > model-pod-describe.txt 2>&1
    READY=$(jq -r '.status.conditions[]? | select(.type=="Ready") | .status' model-pod.json)
    LIM=$(jq -c '[.spec.containers[] | {name, limits: .resources.limits, requests: .resources.requests}]' model-pod.json)
    IMG=$(jq -r '[.status.containerStatuses[]? | "\(.name)=\(.image) \(.imageID) restarts=\(.restartCount)"] | join("; ")' model-pod.json)
    XE=$(jq -r '[.spec.containers[].resources.limits["gpu.intel.com/xe"] // empty] | join(",")' model-pod.json)
    log "model pod Ready=${READY} xe=${XE} limits=${LIM}"
    log "model pod images: ${IMG}"
    for c in $(jq -r '.spec.containers[].name' model-pod.json); do
        kc -n "${PNS}" logs "${PNAME}" -c "${c}" > "engine-${c}.log" 2>&1
    done
    grep -a -i -h -E 'xpu|level.zero|intel.*arc|arc.*pro|B70|0xe2|bmg|battlemage|ipex|oneccl|sycl' engine-*.log | head -40 > engine-xpu-lines.txt
    log "engine XPU lines: $(wc -l < engine-xpu-lines.txt)"
    kc -n "${PNS}" exec "${PNAME}" -- sh -c 'ls /dev/dri; env | grep -E "^(ZE_|ONEAPI|SYCL|VLLM_TARGET|CCL_)" ' > model-pod-devs.txt 2>&1
    res pod_ready "${READY}"; res pod_xe "${XE}"
fi
kc describe node > node-describe.txt 2>&1
sed -n '/Allocated resources/,/Events/p' node-describe.txt > node-allocated.txt
log "node allocated xe: $(grep -a 'gpu.intel.com/xe' node-allocated.txt | tr -s ' ' | tr '\n' ';')"
log "vram serving: $(vram)"
vram > vram-serving.txt
kc get events -A --sort-by=.lastTimestamp > events-after-model-add.txt 2>&1

# Gateway: /v1/models lists the new model; "What is 17 times 23?" answers 391.
gw /v1/models 30 -o models-after.json -w '%{http_code}\n' > models-after.status
NEW=$(jq -r --slurpfile b models-before.json '[.data[].id] - [$b[0].data[]?.id] | .[]' models-after.json 2>/dev/null | head -1)
MODEL_ID=${MODEL_ID:-${NEW}}
# max_tokens: 4096, or less when the served context is shorter (--max-model-len in the pod's args).
MML=$(jq -r '[.spec.containers[].args[]? | select(startswith("--max-model-len=")) | ltrimstr("--max-model-len=")][0] // empty' model-pod.json 2>/dev/null)
CHAT_MAX_TOKENS=4096
[ -z "${MML}" ] || [ "${MML}" -ge 4352 ] || CHAT_MAX_TOKENS=$(( MML - 256 ))
log "served max-model-len ${MML:-unknown}; chat max_tokens ${CHAT_MAX_TOKENS}"
log "models after add: $(jq -c '[.data[].id]' models-after.json 2>/dev/null) (http $(cat models-after.status)); new: ${NEW:-none}; using ${MODEL_ID:-none}"
res models_http "$(cat models-after.status)"
res model_id "${MODEL_ID:-}"
res model_listed "$(jq -r --arg m "${MODEL_ID:-}" '[.data[].id] | index($m) != null' models-after.json 2>/dev/null)"

jq -n --arg m "${MODEL_ID:-}" --argjson n "${CHAT_MAX_TOKENS:-2048}" \
    '{model:$m,messages:[{role:"user",content:"What is 17 times 23?"}],temperature:0,max_tokens:$n}' > chat-req.json
t=$(now)
m=$(gw /v1/chat/completions 900 -H 'Content-Type: application/json' -d @chat-req.json -o chat.json \
    -w 'http=%{http_code} total_s=%{time_total} ttfb_s=%{time_starttransfer}')
CONTENT=$(jq -r '.choices[0].message.content // empty' chat.json 2>/dev/null)
log "chat ${m} model=$(jq -r .model chat.json 2>/dev/null) finish=$(jq -r '.choices[0].finish_reason' chat.json 2>/dev/null) usage=$(jq -c .usage chat.json 2>/dev/null)"
log "chat content: $(tr '\n' ' ' <<< "${CONTENT}" | cut -c1-600)"
res chat_http "$(sed -n 's/^http=\([0-9]*\).*/\1/p' <<< "${m}")"
res chat_391 "$(grep -qw 391 <<< "${CONTENT}" && echo yes || echo no)"

# The agent toolkit with the added model: eat check (Intel's validate, chat, tool call, guard, sandbox).
step 5-eas-check 1800 900 "${K}" eas check --model "${MODEL_ID:-}"
res eas_check_rc "$?"
kc -n "${PNS:-default}" logs "${PNAME:-none}" --all-containers --since=40m > engine-after-check.log 2>&1
log "vram after check: $(vram)"
step 5b-model-remove 600 600 "${K}" eas model remove "${MODEL_ID:-}"
res model_remove_rc "$?"
sleep 10
log "vram after model remove: $(vram)"

step 6-eat-down 1200 300 "${K}" eat down
res eat_down_rc "$?"
step 7-down 600 0 "${K}" down
res down_rc "$?"
sleep 5
docker ps -a --format '{{.Names}} {{.Status}}' | grep -i "k3d-${CLUSTER}" > k3d-containers-after.txt
log "k3d containers after: $(wc -l < k3d-containers-after.txt)"

pass=1
[ "${RES[model_add_rc]:-x}" = 0 ] || pass=0
[ "${RES[pod_ready]:-x}" = True ] || pass=0
[ "${RES[pod_xe]:-x}" = 1 ] || pass=0
[ -s engine-xpu-lines.txt ] || pass=0
[ "${RES[model_listed]:-x}" = true ] || pass=0
[ "${RES[chat_http]:-x}" = 200 ] || pass=0
[ "${RES[chat_391]:-x}" = yes ] || pass=0
[ "${RES[eas_check_rc]:-x}" = 0 ] || pass=0
log "results: $(tr '\n' ' ' < results.txt)"
if [ "${pass}" = 1 ]; then log "RESULT PASS"; exit 0; fi
log "RESULT FAIL"
exit 1
