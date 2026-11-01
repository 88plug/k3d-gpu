#!/usr/bin/env bash
# run.sh OUT: EXPERIMENTS.md H58, the zero-touch thesis on crescent, as amendment R1 defines it.
# Run it inside the bench lock and the hold-start rule:
#   bench/eatflow/crescent/hold.sh H58-run1 14400 bash bench/h58/run.sh <out>
#
# The user steps, each the pre-registered command with no other option or variable:
#   k3d-gpu up; k3d-gpu detect; k3d-gpu agent up --model M for Gemma 4 26B-A4B, Qwen3.8-27B
#   and Qwen3.8-Flash-Next W4A16, in that order.
# When Gemma's server lands, EAT's banking flow runs against it (bench/eatflow/live.sh, solo arm
# Z, H50's harness). When an agent of this run still holds its model after the three, the
# squeeze runs (h58.py squeeze). Then k3d-gpu down. The GPU interlock (h58.py interlock) runs the
# whole time: any workload of the cluster that asks for an accelerator is deleted before it
# starts, since the four Arc Pro B70s are held by the serving pairs this row never touches.
# Records: OUT/steps.jsonl and the files amendment R1 lists; `h58.py score OUT` reads them.
#
# Exit 3: not started (P1 pin moved, a cluster exists, the host differs from H52b's revert, a
# K3D_GPU_* variable is set). Otherwise the run's records are complete and it exits 0, also
# when a user step failed: that failure is the measurement.
set -uo pipefail
OUT=${1:?usage: run.sh OUT}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
K=${H58_K3D_GPU:-${REPO}/scripts/k3d-gpu}     # the tests point it at a stand-in
LIVE=${H58_LIVE:-${REPO}/bench/eatflow/live.sh}
H="python3 ${HERE}/h58.py"
B=${H58_BENCH:-${HOME}/k3dgpu-bench}
CTX=k3d-gpu                # kubectl context of cluster 'gpu', k3d-gpu's default name
NS=k3d-gpu-agent
# k3d-gpu's state directory (the launcher's STATE_DIR and fit_state) and its cache. `up` writes
# the cluster's kubeconfig there (kubeconfig-gpu, mode 0600), not into ~/.kube/config.
SD=${XDG_STATE_HOME:-${HOME}/.local/state}/k3d-gpu
KCFG=${SD}/kubeconfig-gpu
kc() { kubectl --kubeconfig "${KCFG}" --context "${CTX}" "$@"; }
GEMMA=google/gemma-4-26B-A4B-it QWEN27=Qwen/Qwen3.8-27B FLASH=Intel/Qwen3.8-Flash-Next-W4A16-AutoRound
H52B_INV=${H58_INVENTORY_REF:-${REPO}/bench/eatflow/results/2026-10-29/H52b/inventory-3.txt}
CACHES=("${XDG_CACHE_HOME:-${HOME}/.cache}/k3d-gpu" "${SD}")
mkdir -p "${OUT}"
OUT=$(cd "${OUT}" && pwd)
LOG=${OUT}/run.log
log() { echo "== $* $(date -u +%FT%TZ)" | tee -a "${LOG}"; }
now() { date +%s.%N; }

# A step, recorded in steps.jsonl. KIND user: a pre-registered command (clause (a) reads its
# argv and the K3D_GPU_* variables set); record: a read-only k3d-gpu command for the records;
# teardown: k3d-gpu down.
kstep() {  # kstep KIND LOG CAP ARGS...: the checkout's launcher with ARGS, as `k3d-gpu ARGS`
    local kind=$1 out=$2 cap=$3 t0 t1 rc
    shift 3
    t0=$(now)
    timeout -k 30 "${cap}" "${K}" "$@" > "${out}" 2>&1 < /dev/null
    rc=$?
    t1=$(now)
    ${H} step "${OUT}/steps.jsonl" "${kind}" "${rc}" "${t0}" "${t1}" "${out#"${OUT}"/}" -- k3d-gpu "$@"
    log "${kind}: k3d-gpu $* rc=${rc}"
    return "${rc}"
}
not_started() { log "NOT STARTED: $*"; echo "$*" > "${OUT}/not-started.txt"; exit 3; }

frontdoor() {  # frontdoor TAG: a chat through Dynamo :8100 counted on each serving pair (E2 item 7)
    local f=${OUT}/frontdoor-$1.txt P b end ok c
    : > "${f}"
    n() { curl -s -m 8 "http://127.0.0.1:$1/metrics" | awk '/^sglang:num_requests_total/{s+=$2} END{print s+0}'; }
    for P in 30000 30001; do
        b=$(n "${P}") end=$(( SECONDS + 180 )) ok=0 c=""
        until [ "${SECONDS}" -ge "${end}" ]; do
            c=$(curl -s -m 60 http://127.0.0.1:8100/v1/chat/completions -H 'Content-Type: application/json' \
                -d '{"model":"Qwen3.8-27B","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16,"temperature":0,"chat_template_kwargs":{"enable_thinking":false}}' \
                | python3 -c 'import json,sys;print(json.load(sys.stdin)["choices"][0]["message"]["content"].strip())' 2>/dev/null)
            if [ -n "${c}" ] && [ "$(n "${P}")" -gt "${b}" ]; then ok=1; break; fi
            sleep 5
        done
        echo "$(date -u +%FT%TZ) :${P} pass=${ok} before=${b} after=$(n "${P}") content=${c:-none}" | tee -a "${f}"
    done
}
containers() {  # containers FILE: every running container's restart count, state and OOM flag
    docker ps -q | xargs -r docker inspect | python3 -c '
import json, sys
print(json.dumps([{"name": c["Name"].lstrip("/"), "id": c["Id"][:12], "image": c["Config"]["Image"],
                   "restarts": c["RestartCount"], "started_at": c["State"]["StartedAt"],
                   "running": c["State"]["Running"], "oom_killed": c["State"]["OOMKilled"]}
                  for c in json.load(sys.stdin)], indent=1))' > "$1"
}
dmesg_oom() { sudo -n dmesg -T 2>/dev/null | grep -iE 'oom|killed process' > "$1"; }
interlock_alive() {  # the interlock runs and read the cluster within the last 10 s
    kill -0 "${IL_PID:-0}" 2>/dev/null || return 1
    local t st
    read -r t st < "${OUT}/interlock/beat" 2>/dev/null || return 1
    [ "${st}" = ok ] && awk -v t="${t}" -v n="$(date +%s.%N)" 'BEGIN { exit !(n - t < 10) }'
}

# --- Before any step: the records, then the start checks (each one stops the run unstarted). --
git -C "${REPO}" rev-parse HEAD > "${OUT}/commit.txt"
git -C "${REPO}" status --porcelain > "${OUT}/dirty.txt"
{
    echo "k3d-gpu: $("${K}" --version 2>&1 | head -1)"
    for t in k3d kubectl helm uv stress-ng; do
        p=$(command -v "${t}") || { echo "${t}: not on PATH"; continue; }
        echo "${t}: ${p} -> $(readlink -f "${p}") sha256 $(sha256sum "$(readlink -f "${p}")" | cut -c1-64)"
    done
    k3d version 2>&1 | head -2
    kubectl version --client 2>&1 | head -1
    helm version 2>&1 | head -1
    uv --version 2>&1
    stress-ng --version 2>&1 | head -1
    docker version --format 'docker {{.Server.Version}}' 2>&1
    echo "kernel $(uname -r)"
    python3 --version
} > "${OUT}/tools.txt"
{ echo "PATH=${PATH}"; env | cut -d= -f1 | grep -E '^(K3D|KUBECONFIG|XDG_|HF_|HUGGING)' | sort; } > "${OUT}/env.txt"
${H} env > "${OUT}/k3d-gpu-env.txt" || not_started "K3D_GPU_* set: $(cat "${OUT}/k3d-gpu-env.txt")"
${H} pins "${OUT}/pins.json" > /dev/null
rc=$?
[ "${rc}" -eq 0 ] || not_started "P1 pin check rc=${rc} (3: a repo moved, 2: unread)"
k3d cluster list -o json 2>/dev/null | python3 -c 'import json,sys; print(" ".join(c["name"] for c in json.load(sys.stdin)))' > "${OUT}/clusters-pre.txt"
[ -z "$(tr -d ' \n' < "${OUT}/clusters-pre.txt")" ] || not_started "k3d clusters exist: $(cat "${OUT}/clusters-pre.txt")"
bash "${REPO}/bench/eatflow/crescent/inventory.sh" > "${OUT}/inventory.txt" 2>&1
diff <(tail -n +2 "${H52B_INV}") <(tail -n +2 "${OUT}/inventory.txt") > "${OUT}/inventory-diff.txt"
[ ! -s "${OUT}/inventory-diff.txt" ] || not_started "the host differs from H52b's revert (inventory-diff.txt)"
containers "${OUT}/containers-pre.json"
dmesg_oom "${OUT}/dmesg-oom-pre.txt"
cat /proc/meminfo > "${OUT}/meminfo-pre.txt"
{ [ -f "${HOME}/.kube/config" ] && kubectl config get-contexts -o name 2>&1; } > "${OUT}/kube-contexts-pre.txt"
log "start: commit $(cat "${OUT}/commit.txt")"

docker events --filter type=container --format '{{json .}}' > "${OUT}/docker-events.jsonl" 2>/dev/null &
EV_PID=$!
IL_PID=""
: > "${OUT}/cache-aside.txt"
teardown() {
    local rc=$?
    trap - EXIT
    log "teardown (rc ${rc})"
    if k3d cluster list -o json 2>/dev/null | grep -q '"name": *"gpu"'; then
        kc get pods -A -o wide > "${OUT}/pods-end.txt" 2>&1
        kstep teardown "${OUT}/agent-down.log" 300 agent down    # also stops the agent guard
        kstep teardown "${OUT}/down.log" 600 down
    fi
    [ -z "${IL_PID}" ] || { kill "${IL_PID}" 2>/dev/null; wait "${IL_PID}" 2>/dev/null; }
    k3d cluster list -o json 2>/dev/null | python3 -c 'import json,sys; print(" ".join(c["name"] for c in json.load(sys.stdin)))' > "${OUT}/clusters-post.txt"
    containers "${OUT}/containers-post.json"
    dmesg_oom "${OUT}/dmesg-oom-post.txt"
    cat /proc/meminfo > "${OUT}/meminfo-post.txt"
    kill "${EV_PID}" 2>/dev/null
    # The run's own k3d-gpu cache and state (metadata, fit records, guard logs) into OUT, then
    # the ones set aside before the run back in place.
    local d n=0
    for d in "${CACHES[@]}"; do
        n=$(( n + 1 ))
        if [ -e "${d}" ]; then mkdir -p "${OUT}/k3d-gpu-dirs"; mv "${d}" "${OUT}/k3d-gpu-dirs/${n}-$(basename "${d}")"; fi
        rm -f "${OUT}/k3d-gpu-dirs/${n}-$(basename "${d}")"/kubeconfig*    # credentials stay out of the records
        if [ -e "${d}.h58-aside" ]; then mv "${d}.h58-aside" "${d}" && echo "restored ${d}" >> "${OUT}/cache-aside.txt"; fi
    done
    frontdoor post
    git -C "${REPO}" rev-parse HEAD > "${OUT}/commit-end.txt"
    ${H} score "${OUT}" > "${OUT}/score.txt" 2>&1
    log "done: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["verdict"])' "${OUT}/score.json" 2>/dev/null)"
    exit "${rc}"
}
trap teardown EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
frontdoor pre
# Clean state (pre-registration): no k3d-gpu cache or state of earlier commands; set aside, put
# back by the teardown.
for d in "${CACHES[@]}"; do
    [ ! -e "${d}.h58-aside" ] || { echo "${d}.h58-aside exists"; exit 1; }
    if [ -e "${d}" ]; then mv "${d}" "${d}.h58-aside" && echo "set aside ${d} ($(du -sb "${d}.h58-aside" | cut -f1) bytes)" >> "${OUT}/cache-aside.txt"; fi
done
${H} interlock "${KCFG}" "${CTX}" "${OUT}/interlock" > "${OUT}/interlock.log" 2>&1 &
IL_PID=$!

# --- Clause (d) and the cluster: k3d-gpu up, then detect and the node as advertised. --------
if ! kstep user "${OUT}/up.log" 900 up; then
    log "k3d-gpu up failed: the flow stops"
    exit 0
fi
# The device plugins re-register after up returns (H52b: up printed intel=4, the node then read
# 2): the node is read once its gpu.intel.com/xe allocatable has held for 60 s, up to 300 s.
last="" same=0 SETTLE=${H58_SETTLE_S:-10}     # the tests shorten the poll
for _ in $(seq 30); do
    x=$(kc get node -o jsonpath='{.items[0].status.allocatable.gpu\.intel\.com/xe}' 2>/dev/null)
    echo "$(date -u +%FT%TZ) xe=${x:-none}" >> "${OUT}/node-settle.txt"
    if [ "${x}" = "${last}" ]; then same=$(( same + 1 )); else same=0 last=${x}; fi
    [ "${same}" -ge 6 ] && break
    sleep "${SETTLE}"
done
kc get node -o json > "${OUT}/node.json" 2>&1
kstep user "${OUT}/detect.txt" 300 detect
kstep record "${OUT}/detect.json" 300 detect --json
interlock_alive || { log "the GPU interlock is not reading the cluster: stop"; exit 1; }

# --- Clause (e), and (a) to (c) for Gemma: one agent up per model. ---------------------------
model_step() {  # model_step ID DIR: plan record, agent up, its placement
    local m=$1 d=${OUT}/$2 n0 n1 rc blocked="" dep
    mkdir -p "${d}"
    kstep record "${d}/plan.txt" 900 plan --model "${m}" --explain
    interlock_alive || { log "the GPU interlock is not reading the cluster: stop"; exit 1; }
    n0=$(cat "${OUT}/interlock/events.jsonl" 2>/dev/null | wc -l)
    kstep user "${d}/agent-up.log" 5400 agent up --model "${m}"
    rc=$?
    sleep 2
    n1=$(cat "${OUT}/interlock/events.jsonl" 2>/dev/null | wc -l)
    kc -n "${NS}" get deployments -o json > "${d}/deployments.json" 2>&1
    kc get pods -A -o wide > "${d}/pods.txt" 2>&1
    dep=-
    if [ "${n1}" -gt "${n0}" ]; then
        blocked=blocked
        dep=$(ls -t "${OUT}"/interlock/deployment-"${NS}"-*.json 2>/dev/null | head -1)
        [ -n "${dep}" ] || dep=-
        tail -n +"$(( n0 + 1 ))" "${OUT}/interlock/events.jsonl" > "${d}/interlock-events.jsonl"
    else
        python3 - "${d}/deployments.json" "${d}/deploy.json" <<'EOF'
import json, sys
try:
    items = json.load(open(sys.argv[1])).get("items") or []
except (OSError, ValueError):
    items = []
live = [i for i in items if (i.get("spec") or {}).get("replicas", 1) >= 1]
if live:
    json.dump(live[0], open(sys.argv[2], "w"), indent=1)
EOF
        [ -s "${d}/deploy.json" ] && dep=${d}/deploy.json
    fi
    ${H} placement "${d}/agent-up.log" "${rc}" "${dep}" ${blocked} > "${d}/placement.json"
    # The fit agent up recorded (it records one only for a server it applies).
    if [ "${rc}" -eq 0 ] || [ -n "${blocked}" ]; then
        cp "${SD}/agent-fit.json" "${d}/agent-fit.json" 2>/dev/null
    fi
    log "${m}: $(cat "${d}/placement.json")"
    return "${rc}"
}

model_step "${GEMMA}" m-gemma
mkdir -p "${OUT}/b24"
if python3 -c 'import json,sys; p=json.load(open(sys.argv[1])); sys.exit(0 if p["landed"] and not p["accel"] else 1)' "${OUT}/m-gemma/placement.json"; then
    # EAT's banking flow against the server agent up started (amendment R1: H50's harness, arm Z).
    app=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["name"])' "${OUT}/m-gemma/deploy.json")
    ( umask 077; kubectl --kubeconfig "${KCFG}" config view --minify --flatten --context "${CTX}" > "${OUT}/b24/kubeconfig" )
    t0=$(now)
    env LIVE_MODE=solo LIVE_SOLO=Z LIVE_MODEL_SET=gemma4 LIVE_HOLD=1 LIVE_PAIRS_DIR="${OUT}/b24/pairs" \
        LIVE_Z_KUBECONFIG="${OUT}/b24/kubeconfig" LIVE_Z_DEP="${app}" LIVE_CAP=3000 LIVE_LEVEL=typical \
        LIVE_MODELS="${B}/models" LIVE_BLUEPRINT="${B}/eab" LIVE_PY="${B}/mafvenv/bin/python" \
        timeout -k 30 3300 bash "${LIVE}" H58 "${OUT}/b24/hold" > "${OUT}/b24/live-launch.log" 2>&1
    rc=$?
    rm -f "${OUT}/b24/kubeconfig" "${OUT}/b24/hold/kubeconfig"    # cluster credentials stay out of the records
    ${H} step "${OUT}/steps.jsonl" harness "${rc}" "${t0}" "$(now)" b24/live-launch.log -- bench/eatflow/live.sh H58
    n=$(cat "${OUT}/b24/pairs/attempts.jsonl" 2>/dev/null | wc -l)
    python3 -c 'import json,sys; json.dump({"ran": sys.argv[1] == "0" and int(sys.argv[2]) > 0, "rc": int(sys.argv[1]), "attempts": int(sys.argv[2]), "arm": "Z", "why": None if sys.argv[1] == "0" else "live.sh exited " + sys.argv[1]}, open(sys.argv[3], "w"))' \
        "${rc}" "${n}" "${OUT}/b24/status.json"
    log "banking flow rc=${rc}, ${n} attempts"
else
    echo '{"ran": false, "why": "Gemma did not land on a server without an accelerator"}' > "${OUT}/b24/status.json"
fi
model_step "${QWEN27}" m-qwen27b
model_step "${FLASH}" m-flashnext

# --- Clause (f): the squeeze, when an agent of this run still holds its model. ----------------
mkdir -p "${OUT}/squeeze"
kc -n "${NS}" get deployments -o json > "${OUT}/squeeze/deployments.json" 2>&1
app=$(python3 - "${OUT}/squeeze/deployments.json" <<'EOF'
import json, sys
try:
    items = json.load(open(sys.argv[1])).get("items") or []
except (OSError, ValueError):
    items = []
for i in items:
    if ((i.get("status") or {}).get("readyReplicas") or 0) >= 1:
        print(i["metadata"]["name"])
        break
EOF
)
guard_pid=$(cat "${SD}/agent-guard.pid" 2>/dev/null)
if [ -z "${app}" ]; then
    echo '{"ran": false, "why": "no agent of this run holds its model"}' > "${OUT}/squeeze/status.json"
elif [ -z "${guard_pid}" ] || ! kill -0 "${guard_pid}" 2>/dev/null; then
    echo '{"ran": false, "why": "the agent guard is not running"}' > "${OUT}/squeeze/status.json"
elif ! bash "${HERE}/stress-ng.sh" check > "${OUT}/squeeze/stress-ng-check.txt" 2>&1; then
    echo '{"ran": false, "why": "the pinned stress-ng failed its check"}' > "${OUT}/squeeze/status.json"
else
    t0=$(now)
    timeout -k 30 1200 ${H} squeeze "${OUT}/squeeze" "${app}" "${OUT}/containers-pre.json" > "${OUT}/squeeze/launch.log" 2>&1
    rc=$?
    ${H} step "${OUT}/steps.jsonl" harness "${rc}" "${t0}" "$(now)" squeeze/launch.log -- bench/h58/h58.py squeeze "${app}"
fi
log "squeeze: $(cat "${OUT}/squeeze/status.json" 2>/dev/null)"
exit 0
