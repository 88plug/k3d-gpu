#!/usr/bin/env bash
# hold.sh NAME CAP_S CMD...: one measurement hold on crescent (EXPERIMENTS.md "H50-H52
# pre-registration", shared rules 1-4). Waits for the campaign lock, applies the hold-start
# rule (load1 <= 12 and no aiperf process, polled every 2 min for at most 60 min, then the hold
# runs flagged), refuses to start above 97% disk, runs CMD under GNU `timeout -k 30 CAP_S` with
# the serving sampler beside it and a disk watchdog that stops CMD above 97%, then kills what is
# left of CMD's process tree, deletes every cluster and host container of this campaign
# (not k3dgpu-x-*/-g-*) and this tool's load containers. A TERM, INT or HUP stops CMD's process
# group with one TERM (CMD gets it once) and waits for CMD (timeout -k 30 bounds that), then
# cleans up the same way. Records go to
# ~/k3dgpu-bench/runs/NAME/; one start and one end record per hold go to
# ~/k3dgpu-bench/runs/holds.log (holds.py).
# HOLD_SLOT=0|1 (H56, off by default): one hold per cpuset slice, see slots.sh; every
# legacy line below runs unchanged in the else branches when HOLD_SLOT is unset.
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
# shellcheck source=bench/eatflow/crescent/slots.sh
. "${HERE}/slots.sh"
if [ -n "${HOLD_SLOT:-}" ]; then slot_init || exit 2; fi
# This campaign's clusters only: k3dgpu-e-* (pairs), k3dgpu-a-* (one arm), k3dgpu-h5* (H52, H53). Another
# campaign on this host (k3dgpu-x-*, ~/k3dgpu-xhost) shares the lock and owns its clusters.
# live.sh's host-side containers (rapl, litellm, redis, model servers) are named ${PFX}-*, PFX
# k3dgpu-a or k3dgpu-e; a hold killed by its timeout can leave them running. Same two prefixes
# only: k3dgpu-x-* and k3dgpu-g-* belong to the other campaigns.
if [ -n "${HOLD_SLOT:-}" ]; then
clusters() { slot_clusters; }
ours() { slot_containers; }
else
ours() { docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^k3dgpu-(e|a)-' | tr '\n' ' '; }
clusters() { k3d cluster list -o json 2>/dev/null | python3 -c 'import json,sys,re; print(" ".join(c["name"] for c in json.load(sys.stdin) if re.match(r"k3dgpu-(e|a)-|k3dgpu-h5[0-9]b?$", c["name"])))'; }
fi
# CMD runs under GNU's timeout, which exits with CMD's own status when it passes a signal on.
# uutils' timeout (the default on Ubuntu 25.10+, where GNU's is gnutimeout) exits 124 there
# once -k is set, so a signalled hold would read as one that hit its cap.
TO=$(command -v gnutimeout || command -v timeout)
case "$("${TO:-timeout}" --version 2>/dev/null)" in
    *"GNU coreutils"*) ;;
    *) log "no GNU timeout (${TO:-none}): no hold"; exit 2 ;;
esac

log "waiting for the lock"
if [ -n "${HOLD_SLOT:-}" ]; then
slot_lock || exit 4
else
exec 9> "${HOME}/k3dgpu-bench.lock"
flock -w 7200 9 || { log "lock not taken in 7200 s"; exit 4; }
fi
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
# A signal only records itself and asks CMD to stop, once; the main line acts on it.
on_sig() { [ -z "${sig:-}" ] || return 0; sig=$1; [ -z "${cp:-}" ] || kill -TERM "${cp}" 2>/dev/null; }
stop_if_signalled() { [ -z "${sig:-}" ] || exit $(( 128 + sig )); }
my_child() { ps -o ppid= -p "$1" 2>/dev/null | grep -qx " *$$"; }
trap finish EXIT
trap 'on_sig 15' TERM
trap 'on_sig 2' INT
trap 'on_sig 1' HUP
holds append "${HL}" ev=start tag="${TAG}" name="${name}" ${HOLD_SLOT:+slot="${HOLD_SLOT}"}
if [ -n "${HOLD_SLOT:-}" ]; then
slot_gate
else
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
fi
echo "${waited}" > "${R}/wait_s"
snapshot > "${R}/pre.txt"
sudo -n dmesg -T 2>/dev/null | grep -iE ' xe |drm|DEVICE_LOST|CAT error' | tail -200 > "${R}/dmesg-pre.txt"
[ "$(disk)" -le 97 ] || { log "disk $(disk)% > 97%: no hold"; exit 5; }
if [ -n "$(clusters)" ]; then read -r -a cl <<< "$(clusters)"; log "leftover clusters ${cl[*]}: deleting first"; k3d cluster delete "${cl[@]}" >/dev/null 2>&1; fi
if [ -n "$(ours)" ]; then read -r -a oc <<< "$(ours)"; log "leftover containers ${oc[*]}: removing first"; docker rm -f "${oc[@]}" >/dev/null 2>&1; fi
stop_if_signalled
# No child keeps a lock fd (9; 7 and 8 too under slots.sh): a lock lives as long as hold.sh.
python3 "${HERE}/serving.py" "${R}/serving.jsonl" 7>&- 8>&- 9>&- &
sp=$!
log "start: $*"
# setsid execs in place, so ${cp} leads CMD's own process group. No child keeps fd 9.
# A stop (a signal to hold.sh, the cap, the disk watchdog) is timeout's: TERM to its child and
# then to the whole group, KILL 30 s later. After that group send timeout ignores TERM, so a
# later stop sends nothing more. A bash CMD exits at once on a second TERM that lands while its
# EXIT trap runs (bash >= 5.2.14), and CMD as timeout's child got two whenever timeout was
# preempted between its sends. So timeout's child is hold_cmd, a shell that runs CMD and does
# nothing on TERM: CMD and its descendants get the group's TERM only, CMD's status passes on.
hold_cmd=(bash -c 'trap : TERM; "$@"; exit' hold-cmd)
if [ -n "${HOLD_SLOT:-}" ]; then
setsid env K3DGPU_HOLD="${TAG}" K3DGPU_HOLD_RUNS="${B}/runs" "${TO}" -k 30 "${cap}" "${hold_cmd[@]}" taskset -c "${SLOT_CPUS}" "$@" > "${R}/hold.log" 2>&1 7>&- 8>&- 9>&- &
else
setsid env K3DGPU_HOLD="${TAG}" K3DGPU_HOLD_RUNS="${B}/runs" "${TO}" -k 30 "${cap}" "${hold_cmd[@]}" "$@" > "${R}/hold.log" 2>&1 9>&- &
fi
cp=$!
[ -z "${sig:-}" ] || kill -TERM "${cp}"
# The disk stop goes through on_sig, so a hold already stopping does not TERM CMD again.
( while kill -0 "${cp}" 2>/dev/null; do
      sleep 30
      if [ "$(disk)" -gt 97 ]; then echo "disk $(disk)% at $(date -u +%FT%TZ)" > "${R}/disk-abort"; kill -TERM "$$"; break; fi
  done ) 7>&- 8>&- 9>&- &
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
