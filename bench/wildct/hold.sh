#!/usr/bin/env bash
# hold.sh NAME CAP_S NEED_GB CMD...: one measurement hold on wildct (EXPERIMENTS.md "Wildct
# pre-registration", rules W1-W8). Waits for the wildct lock (flock -w 21600
# ~/k3dgpu-bench-wildct/live.lock); hold-start rule: load1 <= 12, polled every 2 min for at
# most 60 min, then the hold runs flagged; a hold starts only when / has at least
# 15 + NEED_GB GB available. CMD runs under `timeout -k 30 CAP_S` with the covariate sampler
# (sample.py) beside it, a privileged RAPL reader container (no network, cpuset 20-23) (K3D_GPU_RAPL_EXEC; package energy
# is root-only and nothing here holds a password), and a disk watchdog that stops CMD when /
# drops below 15 GB available. On every exit it deletes every k3dgpu-w-* cluster, the
# bench-load containers and the RAPL reader, and nothing else. Records go to
# ~/k3dgpu-bench-wildct/runs/NAME/.
set -uo pipefail
W=${HOME}/k3dgpu-bench-wildct
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "${HERE}/../.." && pwd)
name=$1 cap=$2 need=$3
shift 3
R=${W}/runs/${name}
mkdir -p "${R}"
export PATH=${W}/bin:${PATH}
FLOOR=15
RAPL_C=k3dgpu-w-rapl
RAPL_IMAGE=python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb  # probe.sh's PYIMG; energy.sh's reader needs bash
log() { echo "== ${name} $* $(date -u +%FT%TZ)" | tee -a "${W}/runs/campaign.log"; }
avail() { df -P -BG / | awk 'NR==2 {gsub("G", "", $4); print $4}'; }
snapshot() { date -u +%FT%TZ; uptime; ps -eo pcpu,pid,user,comm --sort=-pcpu | head -11; free -g | head -2; df -P -BG / | tail -1; docker ps --format '{{.Names}}' | sort | tr '\n' ' '; echo; }
clusters() { k3d cluster list -o json 2>/dev/null | python3 "${HERE}/clusters.py"; }

log "waiting for the lock"
exec 9> "${W}/live.lock"
flock -w 21600 9 || { log "lock not taken in 21600 s"; exit 4; }
log "lock taken"
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
        sleep 120
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
if [ -n "$(clusters)" ]; then read -r -a cl <<< "$(clusters)"; log "leftover clusters ${cl[*]}: deleting first"; k3d cluster delete "${cl[@]}" >/dev/null 2>&1; fi
sp="" cp="" wd="" rc=1
cleanup() {
    [ -z "${cp}" ] || { kill -TERM "${cp}" 2>/dev/null; wait "${cp}" 2>/dev/null; }
    [ -z "${sp}${wd}" ] || kill ${sp} ${wd} 2>/dev/null
    local left
    left=$(clusters)
    [ -z "${left}" ] || k3d cluster delete ${left} >/dev/null 2>&1
    python3 "${ROOT}/bench/load/load.py" stop >/dev/null 2>&1
    docker rm -f "${RAPL_C}" >/dev/null 2>&1
    snapshot > "${R}/post.txt"
    echo "${rc}" > "${R}/rc"
    log "rc=${rc}; clusters deleted: ${left:-none}; now: $(clusters); / $(avail) GB available"
}
trap cleanup EXIT
trap 'rc=143; exit 143' TERM HUP INT
docker rm -f "${RAPL_C}" >/dev/null 2>&1
docker run -d --rm --name "${RAPL_C}" --privileged --network none --cpuset-cpus 20-23 "${RAPL_IMAGE}" sleep "$(( cap + 120 ))" >/dev/null \
    || { log "RAPL reader did not start"; exit 6; }
export K3D_GPU_RAPL_EXEC="docker exec ${RAPL_C}"
python3 "${HERE}/sample.py" "${R}/sample.jsonl" 9>&- &
sp=$!
log "start: $*"
timeout -k 30 "${cap}" "$@" > "${R}/hold.log" 2>&1 &
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
