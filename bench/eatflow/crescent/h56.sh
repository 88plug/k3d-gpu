#!/usr/bin/env bash
# h56.sh N: H56 condition-hold N (1..8) on crescent (EXPERIMENTS.md "H56 pre-registration:
# parallel slice holds on crescent"). Order A W W A A W W A.
#   A: slice 0 runs one A1 B24 agent hold (cap 2400, LIVE_CAP 2370, LIVE_BUDGET 720); slice 1 idle.
#   W: a sibling A1 hold in slice 1 starts first (cap 3000, LIVE_CAP 2970, LIVE_BUDGET 1800);
#      slice 0 starts the A command once the sibling's live.log reads "== run budget"
#      (at most 840 s, so the condition-hold fits 3300 s).
# Locks: flock -w 21600 on ~/k3dgpu-bench.lock.d/slot-0, then slot-1, then ~/k3dgpu-bench.lock
# exclusive (slots first: the global order of slots.sh). The holds run in HOLD_LOCKS_HELD=1
# mode on these file descriptions (fds 7, 8, 9). Records: ~/k3dgpu-bench/results/H56/hold<N>
# (a rerun of N writes hold<N>-r<k>, the VOID replacement), scored by `h56.py score`.
# H56_SMOKE=1: the one disclosed wiring smoke of W (both LIVE_BUDGET=120) into .../H56/smoke-W.
# H56_PRINT_CONFIG=1: print the order and caps, change nothing.
set -uo pipefail
N=${1:?usage: h56.sh N (1..8)}
ORDER=AWWAAWWA
A_CAP=2400 A_LIVE_CAP=2370 A_BUDGET=720
S_CAP=3000 S_LIVE_CAP=2970 S_BUDGET=1800
SYNC_MAX=840
if [ "${H56_PRINT_CONFIG:-0}" = 1 ]; then
    echo "order=${ORDER} a=${A_CAP}/${A_LIVE_CAP}/${A_BUDGET} sibling=${S_CAP}/${S_LIVE_CAP}/${S_BUDGET} sync_max=${SYNC_MAX}"
    exit 0
fi
[[ "${N}" =~ ^[1-8]$ ]] || { echo "h56: N must be 1..8"; exit 2; }
COND=${ORDER:$(( N - 1 )):1}
B=${HOME}/k3dgpu-bench
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RES=${B}/results/H56
if [ "${H56_SMOKE:-0}" = 1 ]; then
    COND=W A_BUDGET=120 S_BUDGET=120 D=${RES}/smoke-W
    rm -rf "${D}"
else
    D=${RES}/hold${N}
    k=0
    while [ -e "${D}" ]; do k=$(( k + 1 )); D=${RES}/hold${N}-r${k}; done
fi
mkdir -p "${D}"
mkdir -p "${HOME}/k3dgpu-bench.lock.d"
tag=$(basename "${D}")
log() { echo "== H56-${tag} $* $(date -u +%FT%TZ)" | tee -a "${B}/runs/campaign.log"; }
mkdir -p "${B}/runs"
log "waiting for slot-0, slot-1, main"
exec 7> "${HOME}/k3dgpu-bench.lock.d/slot-0" 8> "${HOME}/k3dgpu-bench.lock.d/slot-1" 9> "${HOME}/k3dgpu-bench.lock"
flock -w 21600 7 && flock -w 21600 8 && flock -x -w 21600 9 || { log "locks not taken in 21600 s"; exit 4; }
log "locks taken: condition ${COND}"
echo "cond=${COND} n=${N}" > "${D}/condition.txt"
git -C "${HERE}" rev-parse HEAD > "${D}/commit.txt"
disk=$(df -P / | awk 'NR==2 {gsub("%", "", $5); print $5}')
if [ "${COND}" = W ] && [ "${disk}" -gt 95 ]; then log "disk ${disk}% > 95%: no W hold"; exit 5; fi

# PL1: the package-0 long-term limit, read directly or through a throwaway RAPL container.
RAPL_IMAGE=$(sed -n 's/^RAPL_IMAGE=//p' "${HERE}/../live.sh")
ZONE=/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw
pl1=$(cat "${ZONE}" 2>/dev/null)
[[ "${pl1}" =~ ^[0-9]+$ ]] || pl1=$(docker run --rm --security-opt systempaths=unconfined --security-opt apparmor=unconfined \
    --read-only --network none "${RAPL_IMAGE}" cat "${ZONE}" 2>/dev/null)
[[ "${pl1}" =~ ^[0-9]+$ ]] || pl1=unreadable
echo "${pl1}" > "${D}/pl1.txt"

# IMC CAS counts, descriptive only: the event names this host's `perf list` reports.
perf_pid=
if plist=$(sudo -n perf list --no-desc 2>&1); then
    printf '%s\n' "${plist}" > "${D}/perf-list.txt"
    ev=$(grep -oE 'uncore_imc[_0-9]*/cas_count_(read|write)/' "${D}/perf-list.txt" | sort -u | paste -sd, -)
    if [ -n "${ev}" ]; then
        sudo -n perf stat -a -x, -e "${ev}" -o "${D}/imc.csv" -- sleep $(( S_CAP + SYNC_MAX )) > /dev/null 2>&1 &
        perf_pid=$!
    fi
fi
[ -n "${perf_pid}" ] || echo "no IMC cas_count events or no sudo -n perf" > "${D}/imc.csv"

hold() {  # hold SLOT CAP LIVE_CAP BUDGET: one A1 agent hold in a slice, records to ${D}/s<SLOT>
    local s=$1 out=${D}/s$1
    mkdir -p "${out}"
    HOLD_LOCKS_HELD=1 HOLD_SLOT=${s} HOLD_SLOTS=2 "${HERE}/hold.sh" "H56-${tag}-s${s}" "$2" \
        env K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%" \
        LIVE_MODE=agent LIVE_LEVEL=typical LIVE_CAP="$3" LIVE_BUDGET="$4" LIVE_MODELS="${B}/models" \
        LIVE_BLUEPRINT="${B}/eab" LIVE_PY="${B}/mafvenv/bin/python" PATH="${B}/bin:${PATH}" \
        bash "${HERE}/../live.sh" A1 "${out}"
    local rc=$?
    mkdir -p "${out}/hold"
    for f in rc wait_s flagged pre.txt post.txt serving.jsonl disk-abort slot.txt hold.log; do
        [ -e "${B}/runs/H56-${tag}-s${s}/${f}" ] && cp "${B}/runs/H56-${tag}-s${s}/${f}" "${out}/hold/"
    done
    return "${rc}"
}

if [ "${COND}" = A ]; then
    hold 0 "${A_CAP}" "${A_LIVE_CAP}" "${A_BUDGET}"
    rc=$?
else
    hold 1 "${S_CAP}" "${S_LIVE_CAP}" "${S_BUDGET}" &
    sib=$!
    t0=$(date +%s)
    until grep -q '^== run budget' "${D}/s1/live.log" 2>/dev/null; do
        if ! kill -0 "${sib}" 2>/dev/null || [ $(( $(date +%s) - t0 )) -ge "${SYNC_MAX}" ]; then
            log "sibling not at its run budget in ${SYNC_MAX} s: no slice-0 hold"
            echo "sibling not at run budget" > "${D}/sync-failed"
            wait "${sib}"
            [ -z "${perf_pid}" ] || sudo -n kill -INT "${perf_pid}" 2>/dev/null
            exit 6
        fi
        sleep 10
    done
    echo "$(( $(date +%s) - t0 ))" > "${D}/sync_s"
    hold 0 "${A_CAP}" "${A_LIVE_CAP}" "${A_BUDGET}"
    rc=$?
    wait "${sib}"
    src=$?
    [ "${rc}" != 0 ] || rc=${src}
fi
[ -z "${perf_pid}" ] || sudo -n kill -INT "${perf_pid}" 2>/dev/null
log "rc=${rc}"
exit "${rc}"
