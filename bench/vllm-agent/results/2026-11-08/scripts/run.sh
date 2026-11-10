#!/bin/bash
# run.sh <label> <launcher> <out dir>: the unpatched end state of the vLLM agent on one
# Arc Pro B70. 'k3d-gpu up', then 'K3D_GPU_AGENT_BACKEND=vllm k3d-gpu agent up' with the
# default image and model, then two requests through the endpoint agent up prints (its
# port-forward): "What is 17 times 23?" and one tool call. Tears the cluster down after.
set -u
L=$1 K=$2 O=$3
mkdir -p "$O" && cd "$O" || exit 1
export PATH="$HOME/.local/bin:$PATH"
NS=k3d-gpu-agent
log() { echo "$(date -u +%FT%TZ) [$L] $*" | tee -a "$O/driver.log"; }
vram() { curl -s -m 10 localhost:9101/metrics | awk '/^xe_vram_used_bytes/ { match($0, /bdf="[^"]+"/); b = substr($0, RSTART + 5, RLENGTH - 6); match($0, /card="[^"]+"/); printf "%s %s %.2f;", b, substr($0, RSTART + 6, RLENGTH - 7), $2 / 2^30 }'; }
strip() { sed 's/\x1b\[[0-9;]*m//g'; }
PF=""
teardown() {
    [ -z "${PF}" ] || { kill "${PF}" 2>/dev/null; wait "${PF}" 2>/dev/null; }
    if [ -n "${KC:-}" ] && [ -f "${KC}" ]; then
        kubectl --kubeconfig "${KC}" -n "${NS}" logs deploy/vllm -c vllm > vllm.log 2>&1
        kubectl --kubeconfig "${KC}" -n "${NS}" logs deploy/vllm -c vllm --previous > vllm-previous.log 2>&1
        kubectl --kubeconfig "${KC}" get events -A --sort-by=.lastTimestamp > events.txt 2>&1
    fi
    "$K" agent down > agent-down.txt 2>&1; log "agent down rc=$?"
    "$K" down > down.txt 2>&1; log "down rc=$?"
    sleep 5
    log "vram after: $(vram)"
    docker ps -a --format '{{.Names}} {{.Status}}' | grep -i k3d > k3d-containers-after.txt
    log "k3d containers after: $(wc -l < k3d-containers-after.txt)"
}

log "launcher $K: $("$K" version 2>&1)"
command -v k3d kubectl > tools.txt; k3d version >> tools.txt 2>&1; kubectl version --client >> tools.txt 2>&1
log "vram pre: $(vram)"
t=${SECONDS}; "$K" up > up.txt 2>&1; rc=$?
log "up rc=${rc} in $((SECONDS - t)) s"
KC=$(strip < up.txt | sed -n 's/.*export KUBECONFIG=\([^ ]*\).*/\1/p' | tail -1)
log "kubeconfig ${KC:-none}"
if [ "${rc}" -ne 0 ] || [ -z "${KC}" ]; then teardown; log "RESULT FAIL (up)"; exit 1; fi
kubectl --kubeconfig "${KC}" get nodes -o jsonpath='{.items[0].status.allocatable}' > allocatable.json
log "allocatable $(cat allocatable.json)"

t=${SECONDS}; K3D_GPU_AGENT_BACKEND=vllm "$K" agent up > agent-up.txt 2>&1; arc=$?
log "agent up rc=${arc} in $((SECONDS - t)) s"
strip < agent-up.txt > agent-up.clean.txt
WARM=$(grep -a 'warm-up reply' agent-up.clean.txt | sed 's/^==> //')
log "launcher warm-up line: ${WARM:-none}"
kubectl --kubeconfig "${KC}" -n "${NS}" get deploy,pods,svc -o wide > pods.txt 2>&1
kubectl --kubeconfig "${KC}" -n "${NS}" get deploy vllm -o yaml > deploy-vllm.yaml 2>&1
kubectl --kubeconfig "${KC}" -n "${NS}" describe pods > describe-pods.txt 2>&1
READY=$(kubectl --kubeconfig "${KC}" -n "${NS}" get pod -l app=vllm -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>&1)
LIM=$(kubectl --kubeconfig "${KC}" -n "${NS}" get pod -l app=vllm -o jsonpath='{.items[0].spec.containers[0].resources.limits}' 2>&1)
IMG=$(kubectl --kubeconfig "${KC}" -n "${NS}" get pod -l app=vllm -o jsonpath='{.items[0].spec.containers[0].image} {.items[0].status.containerStatuses[0].imageID} restarts={.items[0].status.containerStatuses[0].restartCount}' 2>&1)
log "pod Ready=${READY} limits=${LIM}"
log "pod image ${IMG}"
log "pod enableServiceLinks=$(kubectl --kubeconfig "${KC}" -n "${NS}" get deploy vllm -o jsonpath='{.spec.template.spec.enableServiceLinks}')"
log "pod render nodes: $(kubectl --kubeconfig "${KC}" -n "${NS}" exec deploy/vllm -c vllm -- ls /dev/dri 2>&1 | tr '\n' ' ')"
log "pod VLLM_PORT lines: $(kubectl --kubeconfig "${KC}" -n "${NS}" exec deploy/vllm -c vllm -- env 2>&1 | grep -c '^VLLM_PORT')"
log "vram serving: $(vram)"
if [ "${arc}" -ne 0 ]; then teardown; log "RESULT FAIL (agent up rc ${arc})"; exit 1; fi

# The endpoint agent up prints: "<KUBECONFIG=...> kubectl -n k3d-gpu-agent port-forward svc/vllm 8000:8000".
PFCMD=$(grep -a 'port-forward svc/vllm' agent-up.clean.txt | tail -1 | sed 's/^ *//')
log "endpoint command: ${PFCMD}"
# shellcheck disable=SC2086 # the printed command's words
env ${PFCMD} > pf.log 2>&1 &
PF=$!
for _ in $(seq 1 30); do curl -s -m 5 localhost:8000/v1/models > models.json 2>/dev/null && break; sleep 2; done
log "models: $(jq -c '[.data[].id]' models.json 2>&1)"

MODEL=$(jq -r '.data[0].id' models.json)
jq -n --arg m "${MODEL}" '{model:$m,messages:[{role:"user",content:"What is 17 times 23?"}],temperature:0,max_tokens:4096}' > chat-req.json
m=$(curl -s -m 600 -o chat.json -w 'http=%{http_code} total_s=%{time_total}' localhost:8000/v1/chat/completions -H 'Content-Type: application/json' -d @chat-req.json)
CONTENT=$(jq -r '.choices[0].message.content' chat.json 2>/dev/null)
log "chat ${m} finish=$(jq -r '.choices[0].finish_reason' chat.json 2>/dev/null) usage=$(jq -c .usage chat.json 2>/dev/null)"
log "chat content: $(tr '\n' ' ' <<< "${CONTENT}")"
log "chat reasoning chars: $(jq -r '(.choices[0].message.reasoning_content // .choices[0].message.reasoning // "") | length' chat.json 2>/dev/null)"

jq -n --arg m "${MODEL}" '{model:$m,messages:[{role:"user",content:"What is the weather in Paris right now?"}],
  tools:[{type:"function",function:{name:"get_weather",description:"Get the current weather for a city",
  parameters:{type:"object",properties:{city:{type:"string",description:"City name"}},required:["city"]}}}],
  tool_choice:"auto",temperature:0,max_tokens:2048}' > tool-req.json
m=$(curl -s -m 600 -o tool.json -w 'http=%{http_code} total_s=%{time_total}' localhost:8000/v1/chat/completions -H 'Content-Type: application/json' -d @tool-req.json)
TOOL=$(jq -c '.choices[0].message.tool_calls' tool.json 2>/dev/null)
ARGS=$(jq -r '.choices[0].message.tool_calls[0].function.arguments' tool.json 2>/dev/null)
log "tool ${m} finish=$(jq -r '.choices[0].finish_reason' tool.json 2>/dev/null) tool_calls=${TOOL}"
log "tool arguments parse: $(jq -c . <<< "${ARGS}" 2>&1)"

pass=1
[ "${READY}" = True ] || pass=0
grep -q '"gpu.intel.com/xe":"1"' <<< "${LIM}" || pass=0
[ -n "${WARM}" ] || pass=0
grep -qw 391 <<< "${CONTENT}" || pass=0
[ "$(jq -r '.choices[0].message.tool_calls[0].function.name' tool.json 2>/dev/null)" = get_weather ] || pass=0
[ "$(jq -r '.city' <<< "${ARGS}" 2>/dev/null)" = Paris ] || pass=0
teardown
if [ "${pass}" = 1 ]; then log "RESULT PASS"; exit 0; fi
log "RESULT FAIL (Ready=${READY}, warm-up=${WARM:+yes}, content has 391: $(grep -qw 391 <<< "${CONTENT}" && echo yes || echo no), tool=${TOOL})"
exit 1
