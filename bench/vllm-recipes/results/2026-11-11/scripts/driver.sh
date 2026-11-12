#!/usr/bin/env bash
# B70 end-state proof of the vLLM recipes resolver PR (claude/vllm-recipes-resolver) on crescent.
# Run detached on crescent:  setsid nohup bash driver.sh STAMP MAIN_CO PR_CO > OUT/driver.log 2>&1 &
#   MAIN_CO / PR_CO: checkouts of origin/main and of the PR head (each a plain git worktree).
# Under ~/k3dgpu-bench.lock it takes the four B70s from the owner's servers (owner-approved:
# snapshot + Paris baseline, docker stop -t 120 aiperf010, qwen38-b-hr, qwen38-a-hr), runs the
# cases on one card in a throwaway k3d cluster, tears it down, starts the servers again in
# reverse order, checks both answer Paris and the per-card VRAM is back, then releases the lock.
# Never touches the telemetry stack, the dynamo/llmd routers or harvest processes.
set -uo pipefail
STAMP=$1 MAIN_CO=$2 PR_CO=$3
B=${HOME}/k3dgpu-bench
OUT=${B}/runs/b70-recipes/${STAMP}
TK=${HOME}/k3dgpu-takeover/b70-recipes-${STAMP}
CL=k3dgpu-recipes-b70
LOCK=${HOME}/k3dgpu-bench.lock
PROM=http://127.0.0.1:9090
NS=k3d-gpu-agent
LOCK_WAIT=${LOCK_WAIT:-28800}   # s to wait for the lock
WORK_CAP=${WORK_CAP:-9000}      # s after the lock: TERM (cleanup still tears down and restores)
CASES=${CASES:-"P4B:pr:Qwen/Qwen3.5-4B M4B:main:Qwen/Qwen3.5-4B P9B:pr:Qwen/Qwen3.5-9B M9B:main:Qwen/Qwen3.5-9B PDEF:pr:"}
BACKUP=${BACKUP:-"PQ8:pr:Qwen/Qwen3-8B MQ8:main:Qwen/Qwen3-8B PNEM:pr:nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 MNEM:main:nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"}
OWN=(aiperf010 qwen38-b-hr qwen38-a-hr)   # stop order; start is the reverse
PORTS=(30000 30001)                       # qwen38-a-hr, qwen38-b-hr
TO=$(command -v timeout)
export PATH=${B}/bin:${PATH} KUBECONFIG=${OUT}/kubeconfig XDG_STATE_HOME=${OUT}/state K3D_GPU_CLUSTER=${CL}
mkdir -p "${OUT}" "${TK}"
chmod 700 "${TK}"

ev() { echo "$(date -u +%FT%TZ) $*" | tee -a "${OUT}/events.log"; }
vram() { curl -s -G -m 10 "${PROM}/api/v1/query" --data-urlencode 'query=xe_vram_used_bytes' \
    | python3 -c 'import json,sys; [print(r["metric"]["bdf"], r["metric"]["card"], "%.2f" % (float(r["value"][1]) / 2**30)) for r in sorted(json.load(sys.stdin)["data"]["result"], key=lambda r: r["metric"]["bdf"])]'; }
vram_line() { vram 2>&1 | tr '\n' ';'; }
# own_ask PORT FILE: the baseline question to an owner server; prints "http time content".
own_ask() {
    local m
    m=$(curl -s -m 20 "http://127.0.0.1:$1/v1/models" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null) || m=Qwen3.8-27B
    curl -sS -m 180 -o "$2" -w '%{http_code} %{time_total}' "http://127.0.0.1:$1/v1/chat/completions" -H 'Content-Type: application/json' \
        -d "{\"model\":\"${m}\",\"messages\":[{\"role\":\"user\",\"content\":\"What is the capital of France? Answer with one word.\"}],\"max_tokens\":16,\"temperature\":0,\"chat_template_kwargs\":{\"enable_thinking\":false}}" 2>/dev/null
    echo " $(python3 -c 'import json,sys; print(repr(json.load(open(sys.argv[1]))["choices"][0]["message"]["content"]))' "$2" 2>/dev/null)"
}
own_paris() { grep -q '^200 .*Paris' <<< "$1"; }

CH=""
bg() { "$@" 9>&- & CH=$!; wait "${CH}"; local r=$?; CH=""; return "${r}"; }
records() {
    kubectl get pods -A -o wide > "${OUT}/$1-pods.txt" 2>&1
    kubectl -n "${NS}" get deploy vllm -o yaml > "${OUT}/$1-deploy-vllm.yaml" 2>&1
    kubectl -n "${NS}" describe pods > "${OUT}/$1-describe-pods.txt" 2>&1
    kubectl get events -A --sort-by=.lastTimestamp > "${OUT}/$1-events-k8s.txt" 2>&1
    kubectl -n "${NS}" logs deploy/vllm --tail=-1 > "${OUT}/$1-vllm.log" 2>&1
    kubectl -n "${NS}" logs deploy/vllm --previous --tail=-1 > "${OUT}/$1-vllm-previous.log" 2>&1
}

echo $$ > "${OUT}/driver.pid"
{ echo "main $(git -C "${MAIN_CO}" rev-parse HEAD)"; echo "pr $(git -C "${PR_CO}" rev-parse HEAD)"; } > "${OUT}/commits.txt"
exec 9> "${LOCK}"
ev "waiting for the lock (up to ${LOCK_WAIT} s); holders: $(fuser "${LOCK}" 2>/dev/null)"
flock -w "${LOCK_WAIT}" 9 || { ev "lock not taken in ${LOCK_WAIT} s"; echo lock-timeout > "${OUT}/result"; exit 4; }
T0=$(date +%s)
ev "lock taken"

STOPPED=() SAMPLER="" WD="" CLUSTER_UP=""
restore_owner() {
    local i c p r ok_a="" ok_b="" deadline
    [ "${#STOPPED[@]}" -gt 0 ] || { ev "restore: nothing was stopped"; return 0; }
    ev "restore begin: docker start, reverse order, of: ${STOPPED[*]}"
    for (( i=${#OWN[@]}-1; i>=0; i-- )); do
        c=${OWN[$i]}
        [[ " ${STOPPED[*]} " == *" ${c} "* ]] || continue
        docker start "${c}" > /dev/null 2>&1; ev "restore: ${c} start rc=$?"
    done
    docker inspect -f '{{.Name}} status={{.State.Status}} started={{.State.StartedAt}} restart={{.HostConfig.RestartPolicy.Name}}' "${OWN[@]}" > "${TK}/state-after.txt" 2>&1
    deadline=$(( $(date +%s) + 1500 ))
    while [ "$(date +%s)" -lt "${deadline}" ]; do
        [ -n "${ok_a}" ] || { r=$(own_ask 30000 "${TK}/verify-30000.json"); ! own_paris "${r}" || { ok_a=yes; ev "restore: :30000 ${r}"; }; }
        [ -n "${ok_b}" ] || { r=$(own_ask 30001 "${TK}/verify-30001.json"); ! own_paris "${r}" || { ok_b=yes; ev "restore: :30001 ${r}"; }; }
        [ -z "${ok_a}" ] || [ -z "${ok_b}" ] || break
        sleep 10
    done
    sleep 45   # one prometheus scrape after the load
    vram > "${TK}/vram-after-restore.txt" 2>&1
    ev "restore: vram $(tr '\n' ';' < "${TK}/vram-after-restore.txt")"
    if [ -n "${ok_a}" ] && [ -n "${ok_b}" ] && python3 - "${TK}/vram-before.txt" "${TK}/vram-after-restore.txt" <<'PY'
import sys
b = {l.split()[0]: float(l.split()[2]) for l in open(sys.argv[1]) if len(l.split()) == 3}
a = {l.split()[0]: float(l.split()[2]) for l in open(sys.argv[2]) if len(l.split()) == 3}
sys.exit(0 if b and set(a) == set(b) and all(a[k] >= b[k] - 1.5 for k in b) else 1)
PY
    then
        ev "restore verified: both servers answer Paris, per-card VRAM back"; echo verified > "${TK}/RESTORED"
    else
        ev "RESTORE NOT VERIFIED (a=${ok_a:-no} b=${ok_b:-no}); see ${TK}"; echo "not verified a=${ok_a:-no} b=${ok_b:-no}" > "${TK}/RESTORE-NOT-VERIFIED"
    fi
}
cleanup() {
    local rc=$?
    trap - EXIT TERM INT HUP
    [ -z "${WD}" ] || { kill $(ps -o pid= --ppid "${WD}") "${WD}" 2>/dev/null; }
    [ -z "${CH}" ] || { kill -TERM "${CH}" 2>/dev/null; wait "${CH}" 2>/dev/null; }
    ev "teardown (rc ${rc})"
    if [ -n "${CLUSTER_UP}" ]; then
        "${TO}" -k 15 60 "${PR_CO}/scripts/k3d-gpu" agent down > "${OUT}/agent-down-final.txt" 2>&1 9>&-; ev "agent down rc=$?"
        "${TO}" -k 15 120 "${PR_CO}/scripts/k3d-gpu" down > "${OUT}/down.txt" 2>&1 9>&-; ev "down rc=$?"
    fi
    if k3d cluster get "${CL}" > /dev/null 2>&1; then "${TO}" -k 15 90 k3d cluster delete "${CL}" > "${OUT}/cluster-delete.txt" 2>&1 9>&-; ev "k3d cluster delete rc=$?"; fi
    k3d cluster list > "${OUT}/clusters-after.txt" 2>&1
    docker ps -a --format '{{.Names}}' | grep -E "^k3d-${CL}-" > "${OUT}/containers-after.txt"
    ev "k3d containers left: $(wc -l < "${OUT}/containers-after.txt")"
    [ -z "${SAMPLER}" ] || kill "${SAMPLER}" 2>/dev/null
    sleep 30
    vram > "${OUT}/vram-after-teardown.txt" 2>&1
    ev "vram after teardown: $(tr '\n' ';' < "${OUT}/vram-after-teardown.txt")"
    restore_owner
    ev "lock released"
    flock -u 9
    exec 9>&-
    ev "driver done"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP
( sleep "${WORK_CAP}"; ev "cap ${WORK_CAP} s reached: TERM"; kill -TERM $$ ) 9>&- &
WD=$!

# ---- takeover ----
date -u +%FT%TZ > "${TK}/snapshot-time.txt"
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' > "${TK}/docker-ps-a.txt" 2>&1
for c in "${OWN[@]}"; do docker inspect "${c}" > "${TK}/inspect-${c}.json" 2>&1; done
docker inspect -f '{{.Name}} status={{.State.Status}} started={{.State.StartedAt}} restart={{.HostConfig.RestartPolicy.Name}}' "${OWN[@]}" > "${TK}/state-before.txt" 2>&1
vram > "${TK}/vram-before.txt" 2>&1
ev "vram before: $(tr '\n' ';' < "${TK}/vram-before.txt")"
for p in "${PORTS[@]}"; do r=$(own_ask "${p}" "${TK}/baseline-${p}.json"); echo "${p} ${r}" >> "${TK}/baseline.txt"; ev "baseline :${p} ${r}"; done
cat > "${TK}/RESTORE.txt" <<EOF
k3d-gpu b70-recipes takeover ${STAMP} (run records: ${OUT}).
Stopped with docker stop -t 120, in order: see stop-log.txt. Nothing removed; config, image, volumes, restart policy unchanged.
Restore, reverse order: docker start qwen38-a-hr qwen38-b-hr aiperf010 (only the ones stop-log.txt lists).
Verify: :30000 and :30001 answer the baseline question (baseline-*.json) with Paris; per-card VRAM as vram-before.txt.
EOF
for c in "${OWN[@]}"; do
    if [ "$(docker inspect -f '{{.State.Status}}' "${c}" 2>/dev/null)" = running ]; then
        s=$(date +%s); echo "${c} stop begin $(date -u +%FT%TZ)" >> "${TK}/stop-log.txt"
        STOPPED+=("${c}")
        bg docker stop -t 120 "${c}" > /dev/null 2>&1
        echo "${c} stop rc=$? took $(( $(date +%s) - s ))s; $(docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}}' "${c}")" >> "${TK}/stop-log.txt"
        ev "stopped ${c} in $(( $(date +%s) - s )) s"
    else
        echo "${c} not running at takeover: left as is" >> "${TK}/stop-log.txt"; ev "${c} not running: left as is"
    fi
done
free=""
for _ in $(seq 1 12); do
    sleep 15
    if vram | awk '{n++} $3 >= 2 {bad=1} END {exit (bad || n != 4)}'; then free=yes; break; fi
done
vram > "${OUT}/vram-pre.txt" 2>&1
[ -n "${free}" ] || { ev "a card still holds >= 2 GiB after the takeover: $(tr '\n' ';' < "${OUT}/vram-pre.txt")"; echo cards-busy > "${OUT}/result"; exit 5; }
echo "crescent 4x B70 freed by k3d-gpu b70-recipes ${STAMP} at $(date -u +%FT%TZ); vram $(tr '\n' ';' < "${OUT}/vram-pre.txt")" > "${TK}/FREED"
ev "cards free: $(tr '\n' ';' < "${OUT}/vram-pre.txt")"
( while :; do echo "$(date -u +%T) $(vram | awk '{printf "%s=%s ", $2, $3}')"; sleep 10; done ) > "${OUT}/vram-samples.txt" 2>&1 9>&- &
SAMPLER=$!

# ---- cluster ----
{ date -u +%FT%TZ; uptime; k3d cluster list; docker ps --format '{{.Names}} {{.Status}}'; } > "${OUT}/pre.txt" 2>&1
CLUSTER_UP=yes
t=$(date +%s); bg "${TO}" -k 20 600 "${PR_CO}/scripts/k3d-gpu" up > "${OUT}/up.txt" 2>&1; urc=$?
ev "up rc=${urc} in $(( $(date +%s) - t )) s"
kubectl get node -o jsonpath='{.items[0].status.allocatable}' > "${OUT}/allocatable.json" 2>&1
ev "allocatable $(cat "${OUT}/allocatable.json")"
[ "${urc}" -eq 0 ] || { echo up-failed > "${OUT}/result"; exit 6; }

TOOLS='[{"type":"function","function":{"name":"get_weather","description":"Current weather for a city","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}]'
# ask LABEL KIND BODY: a chat completion from inside the vllm pod; records the body, reply and time.
ask() {
    local t0 r
    printf '%s\n' "$3" > "${OUT}/$1-$2.req.json"
    t0=$(date +%s%N)
    bg "${TO}" -k 10 900 kubectl -n "${NS}" exec deploy/vllm -c vllm -- curl -sS --max-time 880 \
        http://127.0.0.1:8000/v1/chat/completions -H 'Content-Type: application/json' -d "$3" > "${OUT}/$1-$2.json" 2>&1
    r=$?
    echo "$1 $2 rc=${r} ms=$(( ($(date +%s%N) - t0) / 1000000 ))" >> "${OUT}/timings.txt"
    ev "$1 $2 rc=${r} $(( ($(date +%s%N) - t0) / 1000000 )) ms: $(python3 -c '
import json,sys
m=json.load(open(sys.argv[1]))["choices"][0]["message"]
tc=m.get("tool_calls") or []
print("tool_calls=%s content=%r reasoning_len=%d" % ([(c["function"]["name"], c["function"]["arguments"]) for c in tc], (m.get("content") or "")[:160], len(m.get("reasoning") or m.get("reasoning_content") or "")))' "${OUT}/$1-$2.json" 2>&1 | cut -c1-400)"
}
SERVED_FAIL=""
# run_case LABEL main|pr MODEL (empty: the default model)
run_case() {
    local L=$1 CO name t rc served=""
    [ "$2" = main ] && CO=${MAIN_CO} || CO=${PR_CO}
    local K=${CO}/scripts/k3d-gpu
    local -a e=(K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_TIMEOUT=1500)
    [ -z "$3" ] || e+=("K3D_GPU_AGENT_MODEL=$3")
    name=${3##*/}; [ -n "$3" ] || name=Qwen3-4B
    left=$(( T0 + WORK_CAP - 900 - $(date +%s) ))
    [ "${left}" -gt 600 ] || { ev "${L}: only ${left} s left before the cap: not run"; echo "${L} not-run" >> "${OUT}/cases.txt"; return 0; }
    ev "${L}: $2 launcher ($(git -C "${CO}" rev-parse --short=12 HEAD)), model ${3:-<default>}"
    t=$(date +%s); env "${e[@]}" "${TO}" -k 10 600 "${K}" plan --explain > "${OUT}/${L}-plan.txt" 2>&1 9>&-
    ev "${L} plan --explain rc=$? in $(( $(date +%s) - t )) s"
    t=$(date +%s); env "${e[@]}" "${TO}" -k 10 600 "${K}" agent manifest > "${OUT}/${L}-manifest.yaml" 2> "${OUT}/${L}-manifest.err" 9>&-
    ev "${L} agent manifest rc=$? in $(( $(date +%s) - t )) s"
    t=$(date +%s); bg env "${e[@]}" "${TO}" -k 20 1700 "${K}" agent up > "${OUT}/${L}-agent-up.txt" 2>&1; rc=$?
    ev "${L} agent up rc=${rc} in $(( $(date +%s) - t )) s: $(grep -m1 'warm-up reply' "${OUT}/${L}-agent-up.txt" | sed 's/\x1b\[[0-9;]*m//g')"
    echo "${L} agent-up rc=${rc} s=$(( $(date +%s) - t ))" >> "${OUT}/timings.txt"
    records "${L}"
    kubectl -n "${NS}" get deploy vllm -o jsonpath='{.spec.template.metadata.annotations}' > "${OUT}/${L}-annotations.json" 2>&1
    kubectl -n "${NS}" get deploy vllm -o jsonpath='{.spec.template.spec.containers[0].args}' > "${OUT}/${L}-args.json" 2>&1
    ev "${L} args: $(cat "${OUT}/${L}-args.json" | cut -c1-900)"
    if kubectl -n "${NS}" rollout status deploy/vllm --timeout=5s > /dev/null 2>&1; then
        served=yes
        ask "${L}" tool "{\"model\":\"${name}\",\"max_tokens\":512,\"temperature\":0,\"chat_template_kwargs\":{\"enable_thinking\":false},\"tool_choice\":\"auto\",\"tools\":${TOOLS},\"messages\":[{\"role\":\"user\",\"content\":\"What is the weather in Paris? Use the tool.\"}]}"
        ask "${L}" reason "{\"model\":\"${name}\",\"max_tokens\":4096,\"temperature\":0,\"chat_template_kwargs\":{\"enable_thinking\":true},\"messages\":[{\"role\":\"user\",\"content\":\"What is 17 times 23? Answer with the number only.\"}]}"
        ask "${L}" chat "{\"model\":\"${name}\",\"max_tokens\":64,\"temperature\":0,\"chat_template_kwargs\":{\"enable_thinking\":false},\"messages\":[{\"role\":\"user\",\"content\":\"What is the capital of France? Answer with one word.\"}]}"
        kubectl -n "${NS}" exec deploy/vllm -c vllm -- curl -sS --max-time 30 http://127.0.0.1:8000/v1/models > "${OUT}/${L}-models.json" 2>&1
        kubectl -n "${NS}" exec deploy/vllm -c vllm -- python3 -c 'import importlib.metadata as m; [print(p, m.version(p)) for p in ("vllm", "torch", "transformers")]' > "${OUT}/${L}-packages.txt" 2>&1
        vram > "${OUT}/${L}-vram-serving.txt" 2>&1
        ev "${L} vram serving: $(tr '\n' ';' < "${OUT}/${L}-vram-serving.txt")"
        kubectl -n "${NS}" logs deploy/vllm --tail=-1 > "${OUT}/${L}-vllm-final.log" 2>&1
    fi
    echo "${L} $2 ${3:-default} served=${served:-no}" >> "${OUT}/cases.txt"
    [ -n "${served}" ] || [ "$2" != pr ] || SERVED_FAIL+=" ${L}"
    t=$(date +%s); "${TO}" -k 10 120 "${K}" agent down > "${OUT}/${L}-agent-down.txt" 2>&1 9>&-
    ev "${L} agent down rc=$? in $(( $(date +%s) - t )) s"
    kubectl -n "${NS}" wait --for=delete pod -l app=vllm --timeout=180s > /dev/null 2>&1
}

# The default model's render under both launchers (the args must not change; only annotations may).
K3D_GPU_AGENT_BACKEND=vllm "${TO}" 600 "${MAIN_CO}/scripts/k3d-gpu" agent manifest > "${OUT}/DEF-manifest-main.yaml" 2> "${OUT}/DEF-manifest-main.err" 9>&-
K3D_GPU_AGENT_BACKEND=vllm "${TO}" 600 "${PR_CO}/scripts/k3d-gpu" agent manifest > "${OUT}/DEF-manifest-pr.yaml" 2> "${OUT}/DEF-manifest-pr.err" 9>&-
diff "${OUT}/DEF-manifest-main.yaml" "${OUT}/DEF-manifest-pr.yaml" > "${OUT}/DEF-manifest.diff" 2>&1
ev "default-model render diff main -> pr: $(wc -l < "${OUT}/DEF-manifest.diff") line(s): $(tr '\n' '|' < "${OUT}/DEF-manifest.diff" | cut -c1-400)"

for c in ${CASES}; do IFS=: read -r l w m <<< "${c}"; run_case "${l}" "${w}" "${m}"; done
if [ -n "${SERVED_FAIL}" ]; then
    ev "PR case(s) not served:${SERVED_FAIL}; running the backup model"
    for c in ${BACKUP}; do IFS=: read -r l w m <<< "${c}"; run_case "${l}" "${w}" "${m}"; done
fi
echo "done" > "${OUT}/result"
ev "cases done"
