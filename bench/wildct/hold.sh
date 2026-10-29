#!/usr/bin/env bash
# hold.sh NAME CAP_S NEED_GB CMD...: one measurement hold on wildct (EXPERIMENTS.md "Wildct
# pre-registration", rules W1-W8, and "Wildct amendment R1"). Waits for the wildct lock (flock
# -w 21600 ~/k3dgpu-bench-wildct/live.lock); then the host health check: systemd must answer for
# docker (`systemctl show -p ActiveState --value docker` = active) and the docker daemon must
# answer (`docker info`), or no hold (exit 8); hold-start rule: load1 <= 12, polled every 2 min
# for at most 60 min, then the hold runs flagged; a hold starts only when / has at least
# 15 + NEED_GB GB available. Just before the hold starts, the health check runs again, and
# `k3d cluster list` and the deletion of leftover k3dgpu-w-* clusters must answer, or no hold
# (exit 8). CMD runs under `timeout -k 30 CAP_S` with the covariate sampler
# (sample.py) beside it, a privileged RAPL reader container (no network, cpuset 20-23) (K3D_GPU_RAPL_EXEC; package energy
# is root-only and nothing here holds a password), which must start and return a reading of
# energy_uj or no hold (exit 6), and a disk watchdog that stops CMD when / drops below 15 GB
# available; CMD does not inherit the lock's fd. Every docker and k3d call made here is bounded:
# WILDCT_CALL_T s (default 120), cluster deletes WILDCT_DELETE_T s (default 600), then SIGKILL
# 10 s later. A signal ends the lock wait and the load wait at once; during the cleanup further
# signals are ignored (every cleanup step is bounded). On every exit after the start it deletes every k3dgpu-w-*
# cluster, the bench-load containers and the RAPL reader, and nothing else; a cleanup step that
# does not finish in its bound (or a cluster list that does not answer) is named in
# runs/NAME/cleanup-incomplete. Records go to ~/k3dgpu-bench-wildct/runs/NAME/ (an earlier
# attempt's directory under the same NAME is kept, renamed NAME.<UTC time>): pid (this script's
# PID) at the start, rc last, on every exit, each written whole (bench/wildct/wait_hold.sh waits
# on them).
set -uo pipefail
W=${HOME}/k3dgpu-bench-wildct
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "${HERE}/../.." && pwd)
name=$1 cap=$2 need=$3
shift 3
R=${W}/runs/${name}
[ ! -e "${R}" ] || mv "${R}" "${R}.$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${R}"
# put FILE VALUE: a reader sees the old file or the whole new one, never a half-written one.
put() { echo "$2" > "${R}/$1.tmp" && mv -f "${R}/$1.tmp" "${R}/$1"; }
put pid $$
export PATH=${W}/bin:${PATH} WILDCT_HOLD=${name}
FLOOR=15
T=${WILDCT_CALL_T:-120} TD=${WILDCT_DELETE_T:-600}
RAPL_C=k3dgpu-w-rapl
RAPL_IMAGE=python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f  # probe.sh's PYIMG; energy.sh's reader needs bash
log() { echo "== ${name} $* $(date -u +%FT%TZ)" | tee -a "${W}/runs/campaign.log"; }
avail() { df -P -BG / | awk 'NR==2 {gsub("G", "", $4); print $4}'; }
snapshot() { date -u +%FT%TZ; uptime; ps -eo pcpu,pid,user,comm --sort=-pcpu | head -11; free -g | head -2; df -P -BG / | tail -1; timeout -k 10 "${T}" docker ps --format '{{.Names}}' | sort | tr '\n' ' '; echo; }
# clusters: this campaign's clusters; fails when `k3d cluster list` does not answer in T s.
clusters() { timeout -k 10 "${T}" k3d cluster list -o json 2>/dev/null | python3 "${HERE}/clusters.py" 2>/dev/null; }
incomplete() { echo "$*" >> "${R}/cleanup-incomplete"; }
# health: systemd answers for docker (runc's systemd cgroup driver asks it for every container's
# scope) and dockerd answers; prints what did not.
health() {
    local s
    s=$(timeout -k 10 "${T}" systemctl show -p ActiveState --value docker 2>&1)
    [ "${s}" = active ] || { echo "systemd: docker ActiveState '${s:-no answer in ${T} s}'"; return 1; }
    timeout -k 10 "${T}" docker info --format '{{.ServerVersion}}' > /dev/null 2>&1 || { echo "docker info: no answer in ${T} s"; return 1; }
}
unhealthy() { echo "$1" > "${R}/unhealthy"; log "host not healthy ($1): no hold"; exit 8; }

started=0 sp="" cp="" wd="" bp=""
finish() {
    local rc=$? left="" now st
    trap '' TERM HUP INT
    [ -z "${bp}" ] || kill "${bp}" 2>/dev/null
    if [ "${started}" = 1 ]; then
        [ -z "${cp}" ] || { kill -TERM "${cp}" 2>/dev/null; wait "${cp}" 2>/dev/null; }
        [ -z "${sp}${wd}" ] || kill ${sp} ${wd} 2>/dev/null
        if left=$(clusters); then
            if [ -n "${left}" ]; then
                # shellcheck disable=SC2086 # cluster names, split on purpose
                timeout -k 10 "${TD}" k3d cluster delete ${left} > /dev/null 2>&1 || incomplete "k3d cluster delete ${left}"
            fi
        else
            incomplete "k3d cluster list"
        fi
        timeout -k 10 "${T}" python3 "${ROOT}/bench/load/load.py" stop > /dev/null 2>&1; st=$?
        [ "${st}" -ne 124 ] && [ "${st}" -ne 137 ] || incomplete "load.py stop"
        timeout -k 10 "${T}" docker rm -f "${RAPL_C}" > /dev/null 2>&1; st=$?
        [ "${st}" -ne 124 ] && [ "${st}" -ne 137 ] || incomplete "docker rm -f ${RAPL_C}"
        snapshot > "${R}/post.txt"
        now=$(clusters) || now="unknown (k3d cluster list did not answer)"
        log "rc=${rc}; clusters deleted: ${left:-none}; now: ${now}; / $(avail) GB available$([ -e "${R}/cleanup-incomplete" ] && echo "; CLEANUP INCOMPLETE: $(paste -sd';' "${R}/cleanup-incomplete")")"
    else
        log "rc=${rc} (no hold)"
    fi
    put rc "${rc}"
}
trap finish EXIT
trap 'exit 143' TERM HUP INT

log "waiting for the lock"
exec 9> "${W}/live.lock"
# The lock and load waits run in the background: bash runs a trap only once a foreground command
# returns, and `wait` returns at once on a signal.
flock -w 21600 9 & bp=$!
wait "${bp}" || { bp=""; log "lock not taken in 21600 s"; exit 4; }
bp=""
log "lock taken"
why=$(health) || unhealthy "${why}"
waited=0
while :; do
    l1=$(cut -d' ' -f1 /proc/loadavg)
    if awk -v l="${l1}" 'BEGIN {exit !(l > 12)}'; then
        if [ "${waited}" -ge 3600 ]; then
            log "FLAGGED: load1 ${l1} after 60 min"
            echo "load1 ${l1}" > "${R}/flagged"
            break
        fi
        log "busy (load1 ${l1}): waiting 120 s"
        sleep 120 9>&- & bp=$!
        wait "${bp}"; bp=""
        waited=$(( waited + 120 ))
        continue
    fi
    break
done
echo "${waited}" > "${R}/wait_s"
snapshot > "${R}/pre.txt"
# Rule W7: the repo's launcher (which the bench scripts call) is the installed release's, apart
# from its version line.
if ! diff <(grep -v '^VERSION=' "${ROOT}/scripts/k3d-gpu") <(grep -v '^VERSION=' "${W}/k3d-gpu/usr/bin/k3d-gpu") > "${R}/launcher.diff"; then
    log "launcher differs from the installed release (see launcher.diff): no hold"; exit 7
fi
[ "$(avail)" -ge $(( FLOOR + need )) ] || { log "disk: $(avail) GB available < ${FLOOR} + ${need}: no hold"; exit 5; }
why=$(health) || unhealthy "${why}"
started=1
pre=$(clusters) || unhealthy "k3d cluster list: no answer in ${T} s"
if [ -n "${pre}" ]; then
    read -r -a cl <<< "${pre}"
    log "leftover clusters ${cl[*]}: deleting first"
    timeout -k 10 "${TD}" k3d cluster delete "${cl[@]}" > /dev/null 2>&1 || unhealthy "k3d cluster delete ${cl[*]}: rc $? (bound ${TD} s)"
fi
timeout -k 10 "${T}" docker rm -f "${RAPL_C}" > /dev/null 2>&1
timeout -k 10 "${T}" docker run -d --rm --name "${RAPL_C}" --privileged --network none --cpuset-cpus 20-23 "${RAPL_IMAGE}" sleep "$(( cap + 120 ))" > /dev/null 2>&1
st=$?
[ "${st}" = 0 ] || { log "RAPL reader did not start (docker run rc=${st}; bound ${T} s)"; exit 6; }
e=$(timeout -k 10 "${T}" docker exec "${RAPL_C}" cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null)
st=$?
[[ "${e}" =~ ^[0-9]+$ ]] || { log "RAPL reader gave no energy_uj (docker exec rc=${st}; bound ${T} s)"; exit 6; }
export K3D_GPU_RAPL_EXEC="docker exec ${RAPL_C}"
python3 "${HERE}/sample.py" "${R}/sample.jsonl" 9>&- &
sp=$!
log "start: $*"
timeout -k 30 "${cap}" "$@" > "${R}/hold.log" 2>&1 9>&- &
cp=$!
( while kill -0 "${cp}" 2>/dev/null; do
      sleep 30
      if [ "$(avail)" -lt "${FLOOR}" ]; then echo "disk $(avail) GB at $(date -u +%FT%TZ)" > "${R}/disk-abort"; kill -TERM "${cp}"; break; fi
  done ) 9>&- &
wd=$!
wait "${cp}"
rc=$?
cp=""
exit "${rc}"
