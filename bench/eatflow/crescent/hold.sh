#!/usr/bin/env bash
# hold.sh NAME CAP_S CMD...: one measurement hold on crescent (EXPERIMENTS.md "H50-H52
# pre-registration", shared rules 1-4). Waits for the campaign lock, applies the hold-start
# rule (load1 <= 12 and no aiperf process, polled every 2 min for at most 60 min, then the hold
# runs flagged), refuses to start above 97% disk, runs CMD under `timeout -k 30 CAP_S` with the
# serving sampler beside it and a disk watchdog that stops CMD above 97%, then kills what is left
# of CMD's process tree, deletes every cluster and host container of this campaign
# (not k3dgpu-x-*/-g-*) and this tool's load containers. A TERM, INT or HUP stops CMD with TERM
# and waits for it (timeout -k 30 bounds that), then cleans up the same way. Records go to
# ~/k3dgpu-bench/runs/NAME/; one start and one end record per hold go to
# ~/k3dgpu-bench/runs/holds.log (holds.py).
set -uo pipefail
B=${HOME}/k3dgpu-bench
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
name=$1 cap=$2
shift 2
R=${B}/runs/${name}
mkdir -p "${R}"
export PATH=${B}/bin:${PATH}
log() { echo "== ${name} $* $(date -u +%FT%TZ)" | tee -a "${B}/runs/campaign.log"; }
aiperf_n() { docker top aiperf010 -eo pid,args 2>/dev/null | awk 'NR>1 && $0 !~ /sleep infinity/' | wc -l; }
disk() { df -P / | awk 'NR==2 {gsub("%", "", $5); print $5}'; }
snapshot() { date -u +%FT%TZ; uptime; ps -eo pcpu,pid,user,comm --sort=-pcpu | head -11; echo "aiperf_procs: $(aiperf_n)"; df -P / | tail -1; }
# This campaign's clusters only: k3dgpu-e-* (pairs), k3dgpu-a-* (one arm), k3dgpu-h5* (H52, H53). Another
# campaign on this host (k3dgpu-x-*, ~/k3dgpu-xhost) shares the lock and owns its clusters.
# live.sh's host-side containers (rapl, litellm, redis, model servers) are named ${PFX}-*, PFX
# k3dgpu-a or k3dgpu-e; a hold killed by its timeout can leave them running. Same two prefixes
# only: k3dgpu-x-* and k3dgpu-g-* belong to the other campaigns.
ours() { docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^k3dgpu-(e|a)-' | tr '\n' ' '; }
clusters() { k3d cluster list -o json 2>/dev/null | python3 -c 'import json,sys,re; print(" ".join(c["name"] for c in json.load(sys.stdin) if re.match(r"k3dgpu-(e|a)-|k3dgpu-h5[0-9]b?$", c["name"])))'; }

log "waiting for the lock"
exec 9> "${HOME}/k3dgpu-bench.lock"
flock -w 7200 9 || { log "lock not taken in 7200 s"; exit 4; }
log "lock taken"
# This hold owns every process whose environment has K3DGPU_HOLD=${TAG}: CMD and everything it
# starts, setsid and nohup children included. TAG = name:pid:start time of this shell.
holds() { python3 "${HERE}/holds.py" "$@"; }
st=$(awk '{sub(/.*\) /, ""); print $20}' "/proc/$$/stat")
TAG=${name}:$$:${st} HL=${B}/runs/holds.log
holds sweep "${TAG}" "${B}/runs" > "${R}/stale_reaped"
reap() {  # SIGKILL CMD's group, the watchdog, the sampler, then every process with this hold's tag
    [ -n "${cp:-}" ] || return 0
    kill -KILL -- "-${cp}" 2>/dev/null
    for _ in $(seq 50); do kill -0 -- "-${cp}" 2>/dev/null || break; sleep 0.1; done
    # shellcheck disable=SC2046
    [ -z "${wd:-}" ] || kill -KILL "${wd}" $(pgrep -P "${wd}") 2>/dev/null
    [ -z "${sp:-}" ] || { kill "${sp}" 2>/dev/null; wait "${sp}" 2>/dev/null; }
    holds reap "${TAG}" > "${R}/orphans_killed" || { echo "tagged processes still running" > "${R}/reap_residue"; log "reap residue: see ${R}/reap_residue"; }
    ps -o pid=,user=,comm= -s "${cp}" > "${R}/unreaped" 2>/dev/null
    cp=""
}
finish() {
    local x=$?
    reap
    holds append "${HL}" ev=end tag="${TAG}" rc="${x}" stale_reaped="$(cat "${R}/stale_reaped")" \
        orphans_killed="$(cat "${R}/orphans_killed" 2>/dev/null || echo 0)" reap_residue="$([ -f "${R}/reap_residue" ] && echo 1 || echo 0)"
}
# A signal only records itself and asks CMD to stop; the main line acts on it.
on_sig() { sig=$1; [ -z "${cp:-}" ] || kill -TERM "${cp}" 2>/dev/null; }
stop_if_signalled() { [ -z "${sig:-}" ] || exit $(( 128 + sig )); }
my_child() { ps -o ppid= -p "$1" 2>/dev/null | grep -qx " *$$"; }
trap finish EXIT
trap 'on_sig 15' TERM
trap 'on_sig 2' INT
trap 'on_sig 1' HUP
holds append "${HL}" ev=start tag="${TAG}" name="${name}"
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
        sleep 120 9>&- &
        wait "$!"; kill "$!" 2>/dev/null
        stop_if_signalled
        waited=$(( waited + 120 ))
        continue
    fi
    break
done
echo "${waited}" > "${R}/wait_s"
snapshot > "${R}/pre.txt"
sudo -n dmesg -T 2>/dev/null | grep -iE ' xe |drm|DEVICE_LOST|CAT error' | tail -200 > "${R}/dmesg-pre.txt"
[ "$(disk)" -le 97 ] || { log "disk $(disk)% > 97%: no hold"; exit 5; }
if [ -n "$(clusters)" ]; then read -r -a cl <<< "$(clusters)"; log "leftover clusters ${cl[*]}: deleting first"; k3d cluster delete "${cl[@]}" >/dev/null 2>&1; fi
if [ -n "$(ours)" ]; then read -r -a oc <<< "$(ours)"; log "leftover containers ${oc[*]}: removing first"; docker rm -f "${oc[@]}" >/dev/null 2>&1; fi
stop_if_signalled
python3 "${HERE}/serving.py" "${R}/serving.jsonl" 9>&- &
sp=$!
log "start: $*"
# setsid execs in place, so ${cp} leads CMD's own process group. No child keeps fd 9.
setsid env K3DGPU_HOLD="${TAG}" K3DGPU_HOLD_RUNS="${B}/runs" timeout -k 30 "${cap}" "$@" > "${R}/hold.log" 2>&1 9>&- &
cp=$!
[ -z "${sig:-}" ] || kill -TERM "${cp}"
( while kill -0 "${cp}" 2>/dev/null; do
      sleep 30
      if [ "$(disk)" -gt 97 ]; then echo "disk $(disk)% at $(date -u +%FT%TZ)" > "${R}/disk-abort"; kill -TERM "${cp}"; break; fi
  done ) 9>&- &
wd=$!
# A trapped signal returns wait early; wait again until CMD is gone, then read its status.
wait "${cp}"
while kill -0 "${cp}" 2>/dev/null && my_child "${cp}"; do wait "${cp}"; done
wait "${cp}"
rc=$?
reap
left=$(clusters)
[ -z "${left}" ] || k3d cluster delete ${left} >/dev/null 2>&1
lc=$(ours)
[ -z "${lc}" ] || { read -r -a oc <<< "${lc}"; docker rm -f "${oc[@]}" >/dev/null 2>&1; }
python3 "${HERE}/../../load/load.py" stop >/dev/null 2>&1
snapshot > "${R}/post.txt"
sudo -n dmesg -T 2>/dev/null | grep -iE ' xe |drm|DEVICE_LOST|CAT error' | tail -200 > "${R}/dmesg-post.txt"
echo "${rc}" > "${R}/rc"
log "rc=${rc}; clusters deleted: ${left:-none}; containers removed: ${lc:-none}; now: $(clusters) $(ours)"
exit "${rc}"
