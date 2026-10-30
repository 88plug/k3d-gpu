#!/usr/bin/env bash
# prehold.sh STEP: H53's window steps on crescent (EXPERIMENTS.md, H53 amendment R1), run from the
# H53 checkout ~/k3dgpu-bench/k3d-gpu-h53 at the deploy commit.
#   fetch           models.sh qwen38 under the bench lock: both models at their pinned revisions,
#                   every LFS file against the Hub's sha256, B2's listing (prehold/fetch.log)
#   setup           crescent amendment E4: setup.sh under the bench lock (prehold/setup.txt)
#   verify          both models again against the Hub's sha256, and B2's listing against setup's
#                   (prehold/verify.txt)
#   frontdoor TAG   E2 item 7: a 16-token chat through Dynamo :8100 per serving pair, passing when it
#                   answers with content and that pair's SGLang num_requests_total rises (180 s each)
#   stop            open the window: `docker inspect` of both pairs into prehold/, then
#                   gpu-serving.sh stop for each (never rm), the cards' VRAM, window/open and the deadman
#   smoke AG|B2 [RUNGS]
#                   a wiring smoke: solo-hold.sh's environment, except LIVE_PAIRS_GUARD=99999 (only the
#                   warm-up runs), LIVE_HOLD 1 (AG) or 2 (B2), B2's ladder RUNGS, output under smoke/H53/
#   podlog NAME     a running smoke's pods, events and server logs (current and previous) into its dir
#   holds FIRST LAST [RUNGS]
#                   the scored holds FIRST..LAST through solo-hold.sh, each on the four free cards and
#                   recorded from Prometheus; stops at the first hold that does not exit 0 (the window
#                   stays open), or once fewer than three blocks can still count (then restores);
#                   after hold 6, holds 7 and 8 run only when fewer than three blocks count; then `start`
#   start           close the window: gpu-serving.sh start for both pairs, the inspect comparison and
#                   frontdoor post
#   status          refresh the heartbeat; print the window's last events and the cards' VRAM
#   deadman         started by stop: restores both pairs once no window step is alive and none has
#                   run for 45 min (window/heartbeat), so a dead session cannot leave serving down
set -uo pipefail
B=${HOME}/k3dgpu-bench
REPO=${B}/k3d-gpu-h53
C=${REPO}/bench/eatflow
R=${B}/results/H53
O=${R}/prehold
W=${R}/window
S=${B}/smoke/H53
mkdir -p "${O}" "${W}"
NODE_IMAGE=cryptoandcoffee/k3d-gpu:0.1.61@sha256:8cd264aa68c77681d7fa474693944de0db8d55de5881253e4029bf72a67e175c
PAIRS="qwen38-a-hr qwen38-b-hr"
# All four Arc Pro B70s: both pairs are stopped for the window (H53 pre-registration).
FREE="0000:12:00.0 0000:48:00.0 0000:7c:00.0 0000:96:00.0"
PROM=http://127.0.0.1:9090
STALE_S=2700
FREE_GIB=2
head_of() { (cd "${REPO}" && git rev-parse HEAD); }
ev() { echo "$(date -u +%FT%TZ) $*" | tee -a "${W}/events.log"; }
beat() { touch "${W}/heartbeat"; }
running() { docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null; }
alive() { [ -f "${W}/active.pid" ] && kill -0 "$(cat "${W}/active.pid")" 2>/dev/null; }
lock() {  # lock: the bench lock on fd 9, as hold.sh takes it
    exec 9> "${HOME}/k3dgpu-bench.lock"
    flock -w 7200 9 || { echo "lock not taken in 7200 s"; exit 4; }
}
# vram: one "<bdf> <GiB>" line per card from Prometheus (xe-exporter's xe_vram_used_bytes).
vram() {
    curl -s -m 10 "${PROM}/api/v1/query?query=xe_vram_used_bytes" | python3 -c '
import json, sys
for r in sorted(json.load(sys.stdin)["data"]["result"], key=lambda r: r["metric"]["bdf"]):
    print(r["metric"]["bdf"], round(int(r["value"][1]) / 2**30, 2))'
}
# ready_cards: the window is open, both pairs are stopped and every card holds under FREE_GIB.
ready_cards() {
    local c v
    [ -e "${W}/open" ] || { ev "window not open"; return 1; }
    for c in ${PAIRS}; do
        [ "$(running "${c}")" = false ] || { ev "${c} is running"; return 1; }
    done
    v=$(vram) || return 1
    [ "$(wc -l <<< "${v}")" -eq 4 ] || { ev "Prometheus lists $(wc -l <<< "${v}") cards, not 4"; return 1; }
    if awk -v g="${FREE_GIB}" '$2 >= g {bad=1} END {exit !bad}' <<< "${v}"; then
        ev "a card holds ${FREE_GIB} GiB or more: $(tr '\n' ';' <<< "${v}")"; return 1
    fi
    ev "cards free: $(tr '\n' ';' <<< "${v}")"
}
# wait_cards: ready_cards, polled for up to 2 min (Prometheus scrapes every 15 s).
wait_cards() {
    local _
    for _ in $(seq 24); do ready_cards > /dev/null 2>&1 && break; sleep 5; done
    ready_cards
}
# prom T0 T1 OUT: per-card VRAM and engine utilisation over [T0, T1] (epoch s), 15 s steps.
prom() {
    python3 - "$1" "$2" "$3" "${PROM}" <<'PY'
import json, sys, urllib.parse, urllib.request
t0, t1, out, prom = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3], sys.argv[4]
res = {"t0": t0, "t1": t1}
for q in ("xe_vram_used_bytes", "xe_engine_utilization_percent"):
    u = f"{prom}/api/v1/query_range?" + urllib.parse.urlencode({"query": q, "start": t0, "end": t1, "step": 15})
    with urllib.request.urlopen(u, timeout=30) as r:
        res[q] = json.load(r)["data"]["result"]
peak = {}
for s in res["xe_vram_used_bytes"]:
    peak[s["metric"]["bdf"]] = round(max(int(v) for _, v in s["values"]) / 2**30, 2)
res["vram_peak_gib"] = peak
busy = {}
for s in res["xe_engine_utilization_percent"]:
    b = s["metric"].get("bdf", "?")
    busy[b] = max(busy.get(b, 0.0), max(float(v) for _, v in s["values"]))
res["engine_peak_pct"] = {k: round(v, 1) for k, v in sorted(busy.items())}
with open(out, "w") as f:
    json.dump(res, f)
print("vram peak GiB", peak, "engine peak %", res["engine_peak_pct"])
PY
}
frontdoor() {
    local f=${O}/frontdoor-${1:-check}.txt P b end ok c
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
# The parts of `docker inspect` a restart must keep: image, command, env, devices, mounts, ports.
inspect_key() {
    docker inspect "$1" | python3 -c '
import json, sys
d = json.load(sys.stdin)[0]
h = d["HostConfig"]
print(json.dumps({"image": d["Image"], "path": d["Path"], "args": d["Args"], "env": sorted(d["Config"]["Env"]),
                  "devices": h.get("Devices"), "binds": h.get("Binds"), "network": h.get("NetworkMode"),
                  "ports": h.get("PortBindings"), "restart": h.get("RestartPolicy")}, sort_keys=True))'
}
restore() {
    local c p pids=() rc=0
    rm -f "${W}/open"
    for c in ${PAIRS}; do
        if [ "$(running "${c}")" = true ]; then ev "${c} already running"; continue; fi
        bash "${C}/crescent/gpu-serving.sh" start "${c}" >> "${W}/restore.log" 2>&1 &
        pids+=($!)
    done
    for p in "${pids[@]}"; do wait "${p}" || rc=1; done
    for c in ${PAIRS}; do
        ev "${c} running=$(running "${c}"); $(bash "${C}/crescent/gpu-serving.sh" check "${c}" 2>&1 | head -1)"
        inspect_key "${c}" > "${O}/inspect-${c}.after.key.json"
        if cmp -s "${O}/inspect-${c}.before.key.json" "${O}/inspect-${c}.after.key.json"; then
            ev "${c}: image, command, env, devices, mounts and ports as before the stop"
        else
            ev "${c}: inspect DIFFERS from before the stop"; rc=1
        fi
    done
    frontdoor post
    ev "window closed (restore rc=${rc})"
    return "${rc}"
}
blocks() {  # the number of blocks that count so far (solo-report's block_level.n_blocks, on a copy)
    local t
    t=$(mktemp -d)
    cp "${R}/pairs/registration.json" "${R}/pairs/attempts.jsonl" "${R}/pairs/solo.jsonl" "${t}/" 2>/dev/null
    python3 "${C}/agentbench.py" solo-report "${t}" --hyp H53 --arm-a AG --arm-b B2 > /dev/null 2>&1
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["block_level"]["n_blocks"])' "${t}/analysis.json" 2>/dev/null || echo 0
    rm -rf "${t}"
}
alive_blocks() {  # the blocks that can still count (solo.alive_blocks); a hold has run once hold.sh wrote its rc
    local ran="" n
    for n in 1 2 3 4 5 6 7 8; do
        compgen -G "${B}/runs/H53-hold${n}-*/rc" > /dev/null && ran="${ran} ${n}"
    done
    python3 - "${C}" "${R}/pairs/solo.jsonl" "${ran}" <<'PY'
import os, sys
sys.path.insert(0, sys.argv[1])
import pairs as PR, solo
rows = PR.read_jsonl(sys.argv[2]) if os.path.exists(sys.argv[2]) else []
print(" ".join(map(str, solo.alive_blocks(rows, {int(x) for x in sys.argv[3].split()}))))
PY
}
one_hold() {  # one_hold N RUNGS
    local n=$1 arm t0 t1 rc
    arm=$(python3 "${C}/pairs.py" arm H53 "${n}") || return 2
    beat
    wait_cards || { ev "hold ${n}: cards not free, no hold"; return 6; }
    t0=$(date +%s)
    ev "hold ${n} (${arm}) start"
    LIVE_GPU_FREE="${FREE}" LIVE_B2_LADDER="$2" bash "${C}/crescent/solo-hold.sh" "${n}" "${arm}"
    rc=$?
    t1=$(date +%s)
    beat
    mkdir -p "${R}/hold${n}"
    prom "${t0}" "${t1}" "${R}/hold${n}/xe-prom.json" >> "${W}/events.log" 2>&1
    ev "hold ${n} (${arm}) rc=${rc} ($(( t1 - t0 )) s); $(grep -h 'complete attempt\|ABORTED\|NOT DELIVERABLE' "${R}/hold${n}/live.log" 2>/dev/null | tail -1)"
    return "${rc}"
}
case ${1:?usage: prehold.sh fetch|setup|verify|frontdoor|stop|smoke|podlog|holds|start|status|deadman} in
fetch)
    lock
    { echo "lock-taken $(date -u +%FT%TZ)"; head_of
      HF_HUB_DISABLE_PROGRESS_BARS=1 bash "${C}/crescent/models.sh" qwen38
      echo "rc=$? $(date -u +%FT%TZ)"; } > "${O}/fetch.log" 2>&1 ;;
setup)
    lock
    { echo "lock-taken $(date -u +%FT%TZ)"; head_of
      bash "${C}/crescent/setup.sh"
      echo "rc=$? $(date -u +%FT%TZ)"
      (cd "${B}/bin" && sha256sum k3d kubectl helm uv); } > "${O}/setup.txt" 2>&1
    tail -12 "${O}/setup.txt" ;;
verify)
    lock
    { echo "lock-taken $(date -u +%FT%TZ)"; head_of
      bash "${C}/crescent/models.sh" verify "${B}/models/Qwen3.8-27B" Qwen/Qwen3.8-27B 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0
      echo "Qwen3.8-27B rc=$?"
      bash "${C}/crescent/models.sh" verify "${B}/models/Qwen3.8-27B-int4-ov" OpenVINO/Qwen3.8-27B-int4-ov 7b20ea8c7df7223a85ff27bab5c473f0cb038943
      echo "Qwen3.8-27B-int4-ov rc=$?"
      bash "${C}/crescent/listing.sh" "${B}/models/Qwen3.8-27B" | cmp -s - "${B}/models/Qwen3.8-27B.listing"
      echo "listing equal to setup's: rc=$?"; date -u +%FT%TZ; } > "${O}/verify.txt" 2>&1
    grep -E 'rc=|BAD|MISSING' "${O}/verify.txt" ;;
frontdoor) frontdoor "${2:-check}" ;;
stop)
    [ ! -e "${W}/open" ] || { ev "window already open"; exit 1; }
    for c in ${PAIRS}; do
        docker inspect "${c}" > "${O}/inspect-${c}.before.json" || exit 1
        inspect_key "${c}" > "${O}/inspect-${c}.before.key.json" || exit 1
    done
    ev "VRAM before the stop: $(vram | tr '\n' ';')"
    date -u +%FT%TZ > "${W}/open"
    beat
    for c in ${PAIRS}; do
        if ! bash "${C}/crescent/gpu-serving.sh" stop "${c}" >> "${W}/stop.log" 2>&1; then
            ev "${c} NOT stopped (see stop.log): restoring"; restore; exit 1
        fi
        ev "${c} stopped"
    done
    setsid nohup bash "$0" deadman > "${W}/deadman.log" 2>&1 < /dev/null &
    echo $! > "${W}/deadman.pid"
    ev "window open; deadman pid $(cat "${W}/deadman.pid")"
    wait_cards ;;
smoke)
    ARM=${2:?usage: prehold.sh smoke AG|B2 [RUNGS]} RUNGS=${3:-}
    case ${ARM} in AG) H=1 ;; B2) H=2 ;; *) echo "smoke: ARM must be AG or B2"; exit 2 ;; esac
    name=H53-smoke-${ARM}${RUNGS:+-${RUNGS}}
    [ "${ARM}" = AG ] || name=H53-smoke-B2-${RUNGS:-a}
    beat
    wait_cards || exit 6
    mkdir -p "${S}/${name}/pairs"
    echo $$ > "${W}/active.pid"
    ev "smoke ${name} start"
    exec "${C}/crescent/hold.sh" "${name}" 2400 env K3D_GPU_IMAGE="${NODE_IMAGE}" K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%" \
        LIVE_MODE=solo LIVE_SOLO="${ARM}" LIVE_CAP=2370 LIVE_HOLD="${H}" LIVE_PAIRS_GUARD=99999 LIVE_PAIRS_DIR="${S}/${name}/pairs" \
        LIVE_MODEL_SET=qwen38 LIVE_MOUNT_B=1 LIVE_GPU_FREE="${FREE}" LIVE_B2_TP=4 LIVE_B2_LADDER="${RUNGS}" \
        LIVE_EAT_XPU_CHART="${B}/eat-pr21/core/helm-charts/vllm" LIVE_MODELS="${B}/models" LIVE_BLUEPRINT="${B}/eab" \
        LIVE_PY="${B}/mafvenv/bin/python" PATH="${B}/bin:${PATH}" \
        bash "${C}/live.sh" H53 "${S}/${name}/hold" ;;
podlog)
    d=${S}/${2:?usage: prehold.sh podlog NAME}/hold
    export KUBECONFIG=${d}/kubeconfig PATH=${B}/bin:${PATH}
    beat
    kubectl get pods -A -o wide > "${d}/pods.txt" 2>&1
    kubectl get events -A --sort-by=.lastTimestamp > "${d}/events.txt" 2>&1
    for ns in default k3d-gpu-agent; do
        for p in $(kubectl -n "${ns}" get pods -o name 2>/dev/null | grep -v gpu-hold); do
            kubectl -n "${ns}" describe "${p}" > "${d}/describe-${p##*/}.txt" 2>&1
            kubectl -n "${ns}" logs "${p}" --all-containers > "${d}/log-${p##*/}.txt" 2>&1
            kubectl -n "${ns}" logs "${p}" --all-containers --previous > "${d}/log-${p##*/}.previous.txt" 2>&1
        done
    done
    ls -l "${d}" ;;
holds)
    first=${2:?usage: prehold.sh holds FIRST LAST [RUNGS]} last=${3:?} rungs=${4:-}
    echo $$ > "${W}/active.pid"
    ev "holds ${first}-${last} (ladder '${rungs}') at $(head_of)"
    n=${first}
    while [ "${n}" -le "${last}" ]; do
        one_hold "${n}" "${rungs}" || { ev "hold ${n} did not exit 0: holds stop, the window stays open"; exit 1; }
        a=$(alive_blocks)
        ev "after hold ${n}: blocks that can still count: ${a:-none}"
        if [ "$(wc -w <<< "${a}")" -lt 3 ]; then
            ev "fewer than three blocks can still count: no further hold (futility, R1 item 5)"; break
        fi
        if [ "${n}" -eq 6 ] && [ "${last}" -eq 6 ]; then
            k=$(blocks)
            ev "after hold 6: ${k} block(s) count"
            [ "${k}" -ge 3 ] || { last=8; ev "fewer than three blocks count: holds 7 and 8 run"; }
        fi
        n=$(( n + 1 ))
    done
    restore ;;
start) beat; restore ;;
status)
    beat
    tail -n "${2:-8}" "${W}/events.log"
    vram | tr '\n' ';'; echo ;;
deadman)
    while [ -e "${W}/open" ]; do
        sleep 60
        [ -e "${W}/open" ] || break
        alive && continue
        age=$(( $(date +%s) - $(stat -c %Y "${W}/heartbeat" 2>/dev/null || echo 0) ))
        [ "${age}" -ge "${STALE_S}" ] || continue
        # No hold of any row runs while serving comes back: take the bench lock first.
        exec 9> "${HOME}/k3dgpu-bench.lock"
        flock -w 7200 9 || { ev "deadman: lock not taken"; continue; }
        if [ -e "${W}/open" ] && ! alive; then
            ev "deadman: no window step for ${age} s: restoring serving"
            restore
        fi
        flock -u 9
        break
    done ;;
*) echo "usage: prehold.sh fetch|setup|verify|frontdoor|stop|smoke|podlog|holds|start|status|deadman"; exit 2 ;;
esac
