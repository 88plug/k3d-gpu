#!/usr/bin/env bash
# hold.sh ROW HOLD ARM...: one H54 hold on crescent (EXPERIMENTS.md "H54 pre-registration",
# shared box rules, and "H54 amendment R1"). Takes ~/k3dgpu-bench.lock (flock -w 21600), applies
# the H50 hold-start rule (load1 <= 12 and no aiperf process, 2-min polls, at most 60 min, then the
# hold runs flagged), refuses to start above 97% disk, creates cluster k3dgpu-g-h54<row>-<hold>
# with amendment C1's kubelet thresholds, runs each ARM in turn (bench/h54/arm.sh) with the serving
# sampler beside it and a disk watchdog, and deletes the cluster at the end, also on failure.
# Everything runs under `timeout -k 30 2400`; the arms must end 2370 s after the cluster is up.
# Records: ~/k3dgpu-gemma/results/H54<row>/hold<hold>/.
set -uo pipefail
ROW=$1 HOLD=$2
shift 2
G=${HOME}/k3dgpu-gemma
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export REPO=$(cd "${HERE}/../.." && pwd) G
case ${ROW} in smoke*) R=${G}/smoke/${ROW}/hold${HOLD} ;; *) R=${G}/results/H54${ROW}/hold${HOLD} ;; esac
mkdir -p "${R}"
export PATH=${G}/h54a/bin:${G}/bin:${PATH} EAT=${G}/h54a/eat-src
export CLUSTER=k3dgpu-g-h54${ROW}-${HOLD} KUBECONFIG=${R}/kubeconfig
log() { echo "== H54${ROW}-hold${HOLD} $* $(date -u +%FT%TZ)" | tee -a "${G}/results/campaign.log"; }
aiperf_n() { docker top aiperf010 -eo pid,args 2>/dev/null | awk 'NR>1 && $0 !~ /sleep infinity/' | wc -l; }
disk() { df -P / | awk 'NR==2 {gsub("%", "", $5); print $5}'; }
snapshot() { date -u +%FT%TZ; uptime; ps -eo pcpu,pid,user,comm --sort=-pcpu | head -11; echo "aiperf_procs: $(aiperf_n)"; df -P / | tail -1; }
# This campaign's clusters only (k3dgpu-g-*); k3dgpu-e-*, k3dgpu-a-*, k3dgpu-h5* and k3dgpu-x-* belong to others.
mine() { k3d cluster list -o json 2>/dev/null | python3 -c 'import json,sys; print(" ".join(c["name"] for c in json.load(sys.stdin) if c["name"].startswith("k3dgpu-g-")))'; }

log "waiting for the lock (arms: $*)"
exec 9> "${HOME}/k3dgpu-bench.lock"
flock -w 21600 9 || { log "lock not taken in 21600 s"; exit 4; }
log "lock taken"
waited=0
while :; do
    l1=$(cut -d' ' -f1 /proc/loadavg) a=$(aiperf_n)
    if awk -v l="${l1}" 'BEGIN {exit !(l > 12)}' || [ "${a}" -gt 0 ]; then
        if [ "${waited}" -ge 3600 ]; then log "FLAGGED: still busy after 60 min (load1 ${l1}, aiperf ${a})"; echo "load1 ${l1} aiperf ${a}" > "${R}/flagged"; break; fi
        log "busy (load1 ${l1}, aiperf ${a}): waiting 120 s"
        sleep 120
        waited=$(( waited + 120 ))
        continue
    fi
    break
done
echo "${waited}" > "${R}/wait_s"
snapshot > "${R}/pre.txt"
[ "$(disk)" -le 97 ] || { log "disk $(disk)% > 97%: no hold"; echo "disk $(disk)%" > "${R}/no-hold"; exit 5; }
left=$(mine)
[ -z "${left}" ] || { log "leftover clusters ${left}: deleting first"; k3d cluster delete ${left} > /dev/null 2>&1; }
bash "${HERE}/reproduce.sh" > "${R}/reproduce.txt" 2>&1
python3 "${REPO}/bench/eatflow/crescent/serving.py" "${R}/serving.jsonl" &
sp=$!
export SERVING=${R}/serving.jsonl
log "start: $*"
timeout -k 30 2400 bash "${HERE}/hold-run.sh" "$@" > "${R}/hold.log" 2>&1 &
cp=$!
# timeout(1) signals its whole process group; the disk watchdog stops that group above 97%.
( while kill -0 "${cp}" 2>/dev/null; do
      sleep 30
      if [ "$(disk)" -gt 97 ]; then echo "disk $(disk)% at $(date -u +%FT%TZ)" > "${R}/disk-abort"; kill -TERM "${cp}"; break; fi
  done ) &
wd=$!
wait "${cp}"
rc=$?
kill "${sp}" "${wd}" 2>/dev/null
wait "${sp}" 2>/dev/null
left=$(mine)
[ -z "${left}" ] || k3d cluster delete ${left} > /dev/null 2>&1
snapshot > "${R}/post.txt"
echo "${rc}" > "${R}/rc"
log "rc=${rc}; clusters deleted: ${left:-none}; now: $(mine)"
exit "${rc}"
