#!/usr/bin/env bash
# hold.sh HOLD MODE [ARM...]: one H57d hold on the i9-13900T (EXPERIMENTS.md "H57d
# pre-registration" and "H57d amendment R1"). Takes the host's live.lock (flock -w 21600),
# waits while load1 > 24 (2-min polls, at most 60 min, then runs flagged), refuses to start when
# / would have < 15 GB free or MemAvailable < 20 GiB, creates cluster k3dgpu-d-h57d-<hold>,
# runs each ARM (default: the hold's registered order) with the load sampler beside it and a
# disk watchdog, and deletes the cluster at the end, also on failure. Everything runs under
# `timeout -k 30 2700`. MODE: perf | quality. Records: ${G}/results/H57d/hold<HOLD>/.
#   G     campaign dir (default ~/k3dgpu-h57d), models under G/models
#   LOCK  the host lock file;  SUDO_ASKPASS  for energy.sh's RAPL reader
set -uo pipefail
HOLD=$1 MODE=$2
shift 2
G=${G:-${HOME}/k3dgpu-h57d}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
export REPO G MODE
case ${HOLD} in smoke*) R=${G}/smoke/${HOLD} ;; *) R=${G}/results/H57d/hold${HOLD} ;; esac
mkdir -p "${R}" "${G}/results"
if [ $# -eq 0 ]; then
    read -r -a ARMS_RUN <<< "$(python3 "${HERE}/h57d.py" arms | python3 -c 'import json,sys; o=json.load(sys.stdin)["orders"]; print(" ".join(o[sys.argv[1]]))' "${HOLD}")"
else
    ARMS_RUN=("$@")
fi
export PATH=${G}/bin:${PATH} CLUSTER=k3dgpu-d-h57d-${HOLD//[^a-z0-9]/} KUBECONFIG=${R}/kubeconfig
log() { echo "== H57d-hold${HOLD} $* $(date -u +%FT%TZ)" | tee -a "${G}/results/campaign.log"; }
free_gb() { df -P -B1G / | awk 'NR==2 {print $4}'; }
memavail_gib() { awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo; }
snapshot() { date -u +%FT%TZ; uptime; free -m; ps -eo pcpu,pid,user,comm --sort=-pcpu | head -11; df -P / "$(readlink -f "${G}")" | tail -2; }
# This campaign's clusters only (k3dgpu-d-*).
mine() { k3d cluster list -o json 2>/dev/null | python3 -c 'import json,sys; print(" ".join(c["name"] for c in json.load(sys.stdin) if c["name"].startswith("k3dgpu-d-")))'; }

log "waiting for the lock (mode ${MODE}, arms: ${ARMS_RUN[*]})"
exec 9> "${LOCK:?}"
flock -w 21600 9 || { log "lock not taken in 21600 s"; exit 4; }
log "lock taken"
waited=0
while :; do
    l1=$(cut -d' ' -f1 /proc/loadavg)
    if awk -v l="${l1}" 'BEGIN {exit !(l > 24)}'; then
        if [ "${waited}" -ge 3600 ]; then log "FLAGGED: load1 ${l1} after 60 min"; echo "load1 ${l1}" > "${R}/flagged"; break; fi
        log "busy (load1 ${l1}): waiting 120 s"
        sleep 120
        waited=$(( waited + 120 ))
        continue
    fi
    break
done
echo "${waited}" > "${R}/wait_s"
snapshot > "${R}/pre.txt"
[ "$(free_gb)" -ge 15 ] || { log "/ has $(free_gb) GB free (< 15): no hold"; echo "free $(free_gb) GB" > "${R}/no-hold"; exit 5; }
[ "$(memavail_gib)" -ge 20 ] || { log "MemAvailable $(memavail_gib) GiB (< 20): no hold"; echo "memavail $(memavail_gib) GiB" > "${R}/no-hold"; exit 5; }
left=$(mine)
[ -z "${left}" ] || { log "leftover clusters ${left}: deleting first"; k3d cluster delete ${left} > /dev/null 2>&1; }
bash "${HERE}/reproduce.sh" > "${R}/reproduce.txt" 2>&1
log "start: ${ARMS_RUN[*]}"
timeout -k 30 2700 bash "${HERE}/hold-run.sh" "${R}" "${ARMS_RUN[@]}" > "${R}/hold.log" 2>&1 &
cp=$!
# timeout(1) signals its whole process group; the watchdog stops that group below 15 GB free on /.
( while kill -0 "${cp}" 2>/dev/null; do
      sleep 30
      if [ "$(free_gb)" -lt 15 ]; then echo "/ free $(free_gb) GB at $(date -u +%FT%TZ)" > "${R}/disk-abort"; kill -TERM "${cp}"; break; fi
  done ) &
wd=$!
wait "${cp}"
rc=$?
kill "${wd}" 2>/dev/null
left=$(mine)
[ -z "${left}" ] || k3d cluster delete ${left} > /dev/null 2>&1
snapshot > "${R}/post.txt"
echo "${rc}" > "${R}/rc"
log "rc=${rc}; clusters deleted: ${left:-none}; now: $(mine)"
exit "${rc}"
