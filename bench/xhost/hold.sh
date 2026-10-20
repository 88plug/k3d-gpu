#!/usr/bin/env bash
# hold.sh NAME CAP_S CMD...: one cross-host measurement hold on crescent (EXPERIMENTS.md
# "Cross-host pre-registration"). The same hold rules as bench/eatflow/crescent/hold.sh,
# for this campaign's clusters (k3dgpu-x-*) and directory (~/k3dgpu-xhost):
# wait for the shared lock (flock -w 21600 ~/k3dgpu-bench.lock); hold-start rule (load1 <= 12
# and no aiperf process, polled every 2 min for at most 60 min, then the hold runs flagged);
# no hold above 97% disk; CMD under `timeout -k 30 CAP_S` with the serving sampler beside it
# and a disk watchdog that stops CMD above 97%; then delete every k3dgpu-x-* cluster and the
# bench-load containers. Records go to ~/k3dgpu-xhost/runs/NAME/.
set -uo pipefail
X=${HOME}/k3dgpu-xhost
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "${HERE}/../.." && pwd)
name=$1 cap=$2
shift 2
R=${X}/runs/${name}
mkdir -p "${R}"
export PATH=${X}/bin:${PATH}
log() { echo "== ${name} $* $(date -u +%FT%TZ)" | tee -a "${X}/runs/campaign.log"; }
aiperf_n() { docker top aiperf010 -eo pid,args 2>/dev/null | awk 'NR>1 && $0 !~ /sleep infinity/' | wc -l; }
disk() { df -P / | awk 'NR==2 {gsub("%", "", $5); print $5}'; }
snapshot() { date -u +%FT%TZ; uptime; ps -eo pcpu,pid,user,comm --sort=-pcpu | head -11; echo "aiperf_procs: $(aiperf_n)"; df -P / | tail -1; docker ps --format '{{.Names}}' | sort | tr '\n' ' '; echo; }
clusters() { k3d cluster list -o json 2>/dev/null | python3 "${HERE}/clusters.py"; }

log "waiting for the lock"
exec 9> "${HOME}/k3dgpu-bench.lock"
flock -w 21600 9 || { log "lock not taken in 21600 s"; exit 4; }
log "lock taken"
waited=0
while :; do
    l1=$(cut -d' ' -f1 /proc/loadavg) a=$(aiperf_n)
    if awk -v l="${l1}" 'BEGIN {exit !(l > 12)}' || [ "${a}" -gt 0 ]; then
        if [ "${waited}" -ge 3600 ]; then
            log "FLAGGED: still busy after 60 min (load1 ${l1}, aiperf ${a})"
            echo "load1 ${l1} aiperf ${a}" > "${R}/flagged"
            break
        fi
        log "busy (load1 ${l1}, aiperf ${a}): waiting 120 s"
        sleep 120
        waited=$(( waited + 120 ))
        continue
    fi
    break
done
echo "${waited}" > "${R}/wait_s"
snapshot > "${R}/pre.txt"
start_max=${XHOST_DISK_START:-97}   # a hold starts only at or below this disk use (%)
[ "$(disk)" -le "${start_max}" ] || { log "disk $(disk)% > ${start_max}%: no hold"; exit 5; }
if [ -n "$(clusters)" ]; then read -r -a cl <<< "$(clusters)"; log "leftover clusters ${cl[*]}: deleting first"; k3d cluster delete "${cl[@]}" >/dev/null 2>&1; fi
sp="" cp="" wd="" rc=1
# Cleanup runs on every exit, also when this wrapper itself is signalled (e.g. SSH hangup):
# stop CMD (TERM, so its own cleanup runs), the sampler and the watchdog, then delete this
# campaign's clusters and the bench-load containers.
cleanup() {
    [ -z "${cp}" ] || { kill -TERM "${cp}" 2>/dev/null; wait "${cp}" 2>/dev/null; }
    [ -z "${sp}${wd}" ] || kill ${sp} ${wd} 2>/dev/null
    local left
    left=$(clusters)
    [ -z "${left}" ] || k3d cluster delete ${left} >/dev/null 2>&1
    python3 "${ROOT}/bench/load/load.py" stop >/dev/null 2>&1
    snapshot > "${R}/post.txt"
    echo "${rc}" > "${R}/rc"
    log "rc=${rc}; clusters deleted: ${left:-none}; now: $(clusters)"
}
trap cleanup EXIT
trap 'rc=143; exit 143' TERM HUP INT
python3 "${ROOT}/bench/eatflow/crescent/serving.py" "${R}/serving.jsonl" 9>&- &
sp=$!
log "start: $*"
timeout -k 30 "${cap}" "$@" > "${R}/hold.log" 2>&1 &
cp=$!
( while kill -0 "${cp}" 2>/dev/null; do
      sleep 30
      if [ "$(disk)" -gt 97 ]; then echo "disk $(disk)% at $(date -u +%FT%TZ)" > "${R}/disk-abort"; kill -TERM "${cp}"; break; fi
  done ) 9>&- &
wd=$!
wait "${cp}"
rc=$?
cp=""
exit "${rc}"
