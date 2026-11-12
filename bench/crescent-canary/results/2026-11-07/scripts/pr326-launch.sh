# Piped to `ssh crescent bash -s -- STAMP BASE_A BASE_B`. Starts one detached driver for the
# hardware canary of PR #326 (vllm/vllm-openai-xpu v0.33.0) from the detached checkout of
# autobump/vllm-xpu at 38abe6d8. Only run records are written to crescent (runs/canary-vllm-xpu/).
set -euo pipefail
STAMP=$1 BASE_A=$2 BASE_B=$3
B=${HOME}/k3dgpu-bench
CO=${B}/k3d-gpu-canary-vllm-xpu
OUT=${B}/runs/canary-vllm-xpu/${STAMP}
[ "$(git -C "${CO}" rev-parse HEAD)" = 38abe6d85b93cdd4cbc1b026801639621387232c ] || { echo "checkout not at 38abe6d8"; exit 3; }
[ -n "$(git -C "${CO}" status --porcelain)" ] && { echo "checkout is not clean"; exit 3; }
mkdir -p "${OUT}"
read -r -d '' DRIVER <<'EOF' || true
set -uo pipefail
CO=$1 OUT=$2 BASE_A=$3 BASE_B=$4
B=${HOME}/k3dgpu-bench
CL=k3dgpu-canary-vllm-xpu
K=${CO}/scripts/k3d-gpu
PAIRS="qwen38-a-hr qwen38-b-hr"
LOCK=${HOME}/k3dgpu-bench.lock
HOLD_OFF=${B}/runs/canary-vllm-xpu/hold-off
WORK_END=900     # s after the lock: the canary steps end by here, then teardown and restore
CAP=1170         # s after the lock: TERM if the steps are still running
PROM=http://127.0.0.1:9090
TO=$(command -v gnutimeout || command -v timeout)
NS=k3d-gpu-agent
PROXY=/api/v1/namespaces/${NS}/services/vllm:http/proxy
export PATH=${B}/bin:${PATH} KUBECONFIG=${OUT}/kubeconfig XDG_STATE_HOME=${OUT}/state K3D_GPU_CLUSTER=${CL}
ev() { echo "$(date -u +%FT%TZ) $*" | tee -a "${OUT}/events.log"; }
left() { echo $(( T0 + WORK_END - $(date +%s) )); }
nreq() { curl -s -m 8 "http://127.0.0.1:$1/metrics" | awk '/^sglang:num_requests_total/{s+=$2} END{print s+0}'; }
vram() { curl -s -G -m 10 "${PROM}/api/v1/query" --data-urlencode 'query=xe_vram_used_bytes' \
    | python3 -c 'import json,sys; [print(r["metric"]["bdf"], r["metric"]["card"], "%.2f" % (float(r["value"][1]) / 2**30)) for r in json.load(sys.stdin)["data"]["result"]]'; }
# A long step runs in the background so a TERM reaches the trap at once; fd 9 (the lock) stays here.
CH=""
bg() { "$@" 9>&- & CH=$!; wait "${CH}"; local r=$?; CH=""; return "${r}"; }
# Another lock user (not of this session) whose command or its parent's names H62r or the EAS run.
priority_waiter() {
    local p q cmd me
    # hold-off: written from the session that watches the H62r journal (h62r:run started, not finished)
    [ ! -e "${HOLD_OFF}" ] || { echo "hold-off file ${HOLD_OFF}"; return 0; }
    me=$(ps -o sid= -p $$ | tr -d ' ')
    for p in $(fuser "${LOCK}" 2>/dev/null); do
        [ "$(ps -o sid= -p "${p}" 2>/dev/null | tr -d ' ')" != "${me}" ] || continue
        q=$(ps -o ppid= -p "${p}" 2>/dev/null | tr -d ' ')
        cmd="$(tr '\0' ' ' < "/proc/${p}/cmdline" 2>/dev/null) $(tr '\0' ' ' < "/proc/${q:-0}/cmdline" 2>/dev/null)"
        if grep -qiE 'h62|eat2|k3d-gpu-f5|/f5/|eas-' <<< "${cmd}"; then echo "${p}: ${cmd:0:200}"; return 0; fi
    done
    return 1
}
echo $$ > "${OUT}/driver.pid"
git -C "${CO}" rev-parse HEAD > "${OUT}/commit.txt"
exec 9> "${LOCK}"
ev "waiting for the lock"
deadline=$(( $(date +%s) + 7200 ))
while :; do
    w=$(( deadline - $(date +%s) ))
    [ "${w}" -gt 0 ] && flock -w "${w}" 9 || { ev "lock not taken in 7200 s: skipped"; echo skipped > "${OUT}/result"; exit 4; }
    if pw=$(priority_waiter); then ev "priority user queued (${pw}): released, waiting again"; flock -u 9; sleep 90; continue; fi
    break
done
T0=$(date +%s)
ev "lock taken"
STOPPED="" SAMPLER="" WD=""
cleanup() {
    local rc=$? c p n r g
    trap - EXIT TERM INT HUP
    [ -z "${WD}" ] || { kill $(ps -o pid= --ppid "${WD}") "${WD}" 2>/dev/null; }
    [ -z "${CH}" ] || { kill -TERM "${CH}" 2>/dev/null; wait "${CH}" 2>/dev/null; }
    ev "teardown (rc ${rc})"
    "${TO}" -k 15 45 "${K}" agent down > "${OUT}/agent-down.txt" 2>&1 9>&-; ev "agent down rc=$?"
    "${TO}" -k 15 90 "${K}" down > "${OUT}/down.txt" 2>&1 9>&-; ev "down rc=$?"
    if k3d cluster get "${CL}" > /dev/null 2>&1; then "${TO}" -k 15 60 k3d cluster delete "${CL}" > "${OUT}/cluster-delete.txt" 2>&1 9>&-; ev "k3d cluster delete rc=$?"; fi
    k3d cluster list > "${OUT}/clusters-after.txt" 2>&1
    docker ps -a --format '{{.Names}}' | grep -E "^k3d-${CL}-" > "${OUT}/containers-after.txt"
    for g in $(find "${OUT}/state" -name 'agent-guard.pid' 2>/dev/null); do
        p=$(cat "${g}"); if kill -0 "${p}" 2>/dev/null; then ev "agent guard ${p} still running"; else ev "agent guard ${p} gone"; fi
    done
    [ -z "${SAMPLER}" ] || kill "${SAMPLER}" 2>/dev/null
    for c in ${STOPPED}; do docker start "${c}" > /dev/null 2>&1; ev "docker start ${c} rc=$?"; done
    t=$(date +%s)
    for c in ${STOPPED}; do
        p=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${c}" | sed -n 's/^PORT=//p' | head -1)
        r=""
        until [ "$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:${p}/health")" = 200 ] \
            && r=$(curl -s -m 60 "http://127.0.0.1:${p}/v1/chat/completions" -H 'Content-Type: application/json' \
                 -d '{"model":"Qwen3.8-27B","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16,"temperature":0}' \
                 | python3 -c 'import json,sys; print(json.load(sys.stdin)["choices"][0]["message"]["content"][:80])' 2>/dev/null) \
            && [ -n "${r}" ]; do
            [ "$(( $(date +%s) - t ))" -lt 165 ] || { ev "${c} :${p} NOT answering 165 s after start"; break; }
            sleep 5
        done
        [ -z "${r}" ] || ev "${c} :${p} health 200, 16-token chat after $(( $(date +%s) - t )) s: ${r//$'\n'/ }"
        docker inspect "${c}" > "${OUT}/inspect-${c}.after.json"
    done
    vram > "${OUT}/vram-after.txt" 2>&1
    ev "lock released"
    flock -u 9
    exec 9>&-
    if [ -n "${STOPPED}" ]; then
        # Through Dynamo :8100 (no GPU work of ours left): "OK" from each pair, its counter rising.
        for p in 30000 30001; do
            b=$(nreq "${p}") n=0 ok=0 r=""
            while [ "${n}" -lt 120 ]; do
                r=$(curl -s -m 60 http://127.0.0.1:8100/v1/chat/completions -H 'Content-Type: application/json' \
                    -d '{"model":"Qwen3.8-27B","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16,"temperature":0,"chat_template_kwargs":{"enable_thinking":false}}' \
                    | python3 -c 'import json,sys; print(json.load(sys.stdin)["choices"][0]["message"]["content"].strip())' 2>/dev/null)
                if [ -n "${r}" ] && [ "$(nreq "${p}")" -gt "${b}" ]; then ok=1; break; fi
                sleep 5; n=$(( n + 5 ))
            done
            ev "frontdoor :8100 -> :${p} pass=${ok} before=${b} after=$(nreq "${p}") content=${r:-none}"
        done
    fi
    ev "driver done"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP
( sleep "${CAP}"; ev "cap ${CAP} s reached: TERM"; kill -TERM $$ ) 9>&- &
WD=$!
{ date -u +%FT%TZ; uptime; k3d cluster list; docker ps --format '{{.Names}} {{.Status}}'; } > "${OUT}/pre.txt" 2>&1
vram > "${OUT}/vram-pre.txt" 2>&1
# 1. Free the cards: both serving pairs, after their inspect and a traffic check.
a=$(docker top aiperf010 -eo pid,args 2>/dev/null | awk 'NR>1 && $0 !~ /sleep infinity/' | wc -l)
na=$(nreq 30000) nb=$(nreq 30001)
ev "aiperf procs ${a}; num_requests_total :30000 ${na} (base ${BASE_A}), :30001 ${nb} (base ${BASE_B})"
if [ "${a}" -gt 0 ] || [ "${na}" -gt $(( BASE_A + 2 )) ] || [ "${nb}" -gt $(( BASE_B + 2 )) ]; then
    ev "serving pairs in use: skipped"; echo skipped > "${OUT}/result"; exit 5
fi
for c in ${PAIRS}; do
    [ "$(docker inspect -f '{{.State.Running}}' "${c}" 2>/dev/null)" = true ] || { ev "${c} not running: left as is"; continue; }
    docker inspect "${c}" > "${OUT}/inspect-${c}.before.json"
    docker stop "${c}" > /dev/null && { STOPPED="${STOPPED} ${c}"; ev "docker stop ${c}"; }
done
n=0
until vram | awk '{n++} $3 >= 2 {bad=1} END {exit (bad || n != 4)}'; do
    n=$(( n + 5 )); [ "${n}" -lt 90 ] || { ev "a card still holds >= 2 GiB 90 s after the stop"; vram >> "${OUT}/events.log"; echo skipped > "${OUT}/result"; exit 6; }
    sleep 5
done
vram > "${OUT}/vram-freed.txt"
ev "cards freed: $(tr '\n' ';' < "${OUT}/vram-freed.txt")"
( while :; do echo "$(date -u +%T) $(vram | awk '{printf "%s=%s ", $2, $3}')"; sleep 10; done ) > "${OUT}/vram-samples.txt" 2>&1 9>&- &
SAMPLER=$!
# 2. The cluster.
t=$(date +%s); bg "${TO}" -k 20 300 "${K}" up > "${OUT}/up.txt" 2>&1; ev "up rc=$? in $(( $(date +%s) - t )) s"
kubectl get node -o jsonpath='{.items[0].status.allocatable}' > "${OUT}/allocatable.json" 2>&1
ev "allocatable $(cat "${OUT}/allocatable.json")"
# 3. The agent: vLLM, its default model (Qwen/Qwen3-4B), the PR head's default image.
w=$(left)
t=$(date +%s)
if [ "${w}" -gt 120 ]; then
    K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_TIMEOUT=${w} bg "${TO}" -k 20 "${w}" "${K}" agent up > "${OUT}/agent-up.txt" 2>&1
    arc=$?
else
    ev "only ${w} s left for agent up: not run"; arc=99
fi
ev "agent up rc=${arc} in $(( $(date +%s) - t )) s"
kubectl get pods -A -o wide > "${OUT}/pods.txt" 2>&1
kubectl -n "${NS}" get deploy vllm -o yaml > "${OUT}/deploy-vllm.yaml" 2>&1
kubectl -n "${NS}" describe pods > "${OUT}/describe-pods.txt" 2>&1
kubectl get events -A --sort-by=.lastTimestamp > "${OUT}/events-k8s.txt" 2>&1
docker exec "k3d-${CL}-server-0" crictl images --digests > "${OUT}/node-images.txt" 2>&1
kubectl -n "${NS}" logs deploy/vllm --tail=-1 > "${OUT}/vllm.log" 2>&1
kubectl -n "${NS}" logs deploy/vllm --previous --tail=-1 > "${OUT}/vllm-previous.log" 2>&1
if [ "${arc}" -eq 0 ]; then
    # 4. One real request, to the name the server lists.
    kubectl get --raw "${PROXY}/v1/models" > "${OUT}/models.json" 2>&1
    m=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"][0]["id"])' "${OUT}/models.json" 2>/dev/null || echo Qwen3-4B)
    printf '{"model":"%s","messages":[{"role":"user","content":"What is 17 times 23? Answer with the number only."}],"max_tokens":64,"temperature":0,"chat_template_kwargs":{"enable_thinking":false}}' "${m}" > "${OUT}/req.json"
    t=$(date +%s%N)
    kubectl create --raw "${PROXY}/v1/chat/completions" -f "${OUT}/req.json" > "${OUT}/chat.json" 2>&1
    ev "chat rc=$? in $(( ($(date +%s%N) - t) / 1000000 )) ms: $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(repr(d["choices"][0]["message"]["content"]), d["usage"])' "${OUT}/chat.json" 2>&1)"
    vram > "${OUT}/vram-serving.txt" 2>&1
    # The pod's devices and the image's runtime packages.
    kubectl -n "${NS}" exec deploy/vllm -- sh -c 'ls -l /dev/dri; env | grep -E "^(ZE_|ONEAPI|SYCL|VLLM_TARGET)" | sort' > "${OUT}/pod-devices.txt" 2>&1
    kubectl -n "${NS}" exec deploy/vllm -- sh -c "dpkg-query -W -f='\${Package} \${Version}\n' | grep -iE 'ze|level|opencl|igc|gmm|intel|compute' ; echo ==; ldconfig -p | grep -iE 'libze|igdrcl|igc' ; echo ==; python3 -c 'import importlib.metadata as m; print(\"vllm\", m.version(\"vllm\")); print(\"torch\", m.version(\"torch\"))'" > "${OUT}/pod-packages.txt" 2>&1
    echo served > "${OUT}/result"
else
    echo agent-up-failed > "${OUT}/result"
fi
EOF
setsid nohup bash -c "${DRIVER}" canary-326 "${CO}" "${OUT}" "${BASE_A}" "${BASE_B}" > "${OUT}/driver.log" 2>&1 < /dev/null &
echo "launched pid $! at $(date -u +%FT%TZ) out ${OUT}"
