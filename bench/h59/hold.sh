#!/usr/bin/env bash
# hold.sh HOST HOLD [REPEATS]: one H59-family hold (EXPERIMENTS.md "H59 pre-registration" and "H59
# amendment R1") on HOST = crescent (H59, Xeon 6741P), desktop (H59d, i9-13900T) or wildct (H59w,
# i7-13700, run as bench/wildct/hold.sh's command: bench/wildct/row.sh h59w). HOLD: 1-3 perf in
# the orders (O, L), (L, O), (O, L); 4 quality in (L, O); a spare 5 or 6 repeats hold REPEATS
# (one that ran and left an arm not measured) in its order; smoke runs hold 1's arms with no
# scored request (discarded, disclosed). Each hold runs once: a failed hold counts.
#
# Before the lock it refuses (exit 2) a host whose CPU is not the profile's, a node image not
# pinned by digest or not the one the row's first hold ran, a ${G}/bin/k3d that is not
# bench/h59/k3d-shim.sh, and models prep.sh has not left in ${G}/models. Then the host's own rules
# (run.py profile): its lock (flock -w 21600; crescent ~/k3dgpu-bench.lock, else LOCK); the
# hold-start rule (load1 <= load1_max, and on crescent no aiperf process; 2-min polls, at most 60
# min, then the hold runs flagged); the floors (crescent: / at most 97% used; desktop and wildct:
# 15 GB free on / and MemAvailable >= 20 GiB), or no hold (exit 5). It creates cluster
# <prefix><HOLD>, runs the arms (hold-run.sh) under `timeout -k 30 <cap_s>` with a disk watchdog
# (and crescent's serving sampler), and deletes this campaign's clusters at the end, also on
# failure. Records: ${G}/results/<row>/hold<HOLD>/ (smoke: ${G}/smoke/<row>-<UTC>/).
#   G  campaign dir (default from the profile);  H59_IMAGE  the k3d-gpu node image, name:tag@sha256
#   LOCK  the host lock (desktop; wildct's private inner lock);  K3D_REAL  the shim's real k3d
#   SUDO_ASKPASS or K3D_GPU_RAPL_EXEC  for energy.sh's RAPL reader
#   H59_CPUINFO  the cpuinfo file the host check reads (default /proc/cpuinfo)
set -uo pipefail
HOST=${1:?usage: hold.sh HOST HOLD [REPEATS]} HOLD=${2:?usage: hold.sh HOST HOLD [REPEATS]} REPEATS=${3:-}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
RUN=${HERE}/run.py
python3 "${RUN}" profile "${HOST}" > /dev/null || exit 2
pfield() { python3 "${RUN}" profile "${HOST}" | sed -n "s/^$1=//p"; }
ROW=$(pfield row) PREFIX=$(pfield prefix) CPU=$(pfield cpu) L1MAX=$(pfield load1_max) AIPERF=$(pfield aiperf)
DISK=$(pfield disk) MEMMIN=$(pfield mem_gib) CAP=$(pfield cap_s) SAMPLER=$(pfield sampler)
G=${G:-$(pfield g)}
G=${G/#\~/${HOME}}
order=$(python3 "${RUN}" hold "${HOLD}" ${REPEATS:+"${REPEATS}"}) || exit 2
read -r MODE ARMS_RUN <<< "${order}"
read -r -a ARMS_RUN <<< "${ARMS_RUN}"
grep -m1 '^model name' "${H59_CPUINFO:-/proc/cpuinfo}" | grep -qF -- "${CPU}" \
    || { echo "hold.sh: this host's CPU is not the ${CPU} that ${ROW} registers" >&2; exit 2; }
[[ "${H59_IMAGE:-}" =~ ^[^@]+:[^@]+@sha256:[0-9a-f]{64}$ ]] \
    || { echo "hold.sh: set H59_IMAGE to the k3d-gpu node image as name:tag@sha256:<digest>" >&2; exit 2; }
cmp -s "${HERE}/k3d-shim.sh" "${G}/bin/k3d" || { echo "hold.sh: ${G}/bin/k3d is not bench/h59/k3d-shim.sh (install it first)" >&2; exit 2; }
python3 "${RUN}" models "${G}" >&2 || { echo "hold.sh: run bench/h59/prep.sh ${HOST} first" >&2; exit 2; }
RES=${G}/results/${ROW}
case ${HOLD} in smoke) R=${G}/smoke/${ROW}-$(date -u +%Y%m%dT%H%M%SZ) ;; *) R=${RES}/hold${HOLD} ;; esac
[ ! -e "${R}/rc" ] || { echo "hold.sh: hold ${HOLD} has run (${R}/rc); a failed hold counts, a spare repeats it" >&2; exit 2; }
if [ -n "${REPEATS}" ]; then
    why=$(python3 "${RUN}" spare-ok "${RES}" "${REPEATS}") || { echo "hold.sh: spare ${HOLD} cannot repeat hold ${REPEATS}: ${why}" >&2; exit 2; }
fi
if [ "${HOLD}" != smoke ] && [ -f "${RES}/node-image" ] && [ "$(cat "${RES}/node-image")" != "${H59_IMAGE}" ]; then
    echo "hold.sh: ${ROW}'s holds run node image $(cat "${RES}/node-image"), not ${H59_IMAGE}" >&2
    exit 2
fi
case ${HOST} in crescent) LOCKF=${HOME}/k3dgpu-bench.lock ;; *) LOCKF=${LOCK:?set LOCK to this host\'s lock file} ;; esac
mkdir -p "${R}" "${G}/results"
export REPO G MODE H59_IMAGE H59_PROFILE=${HOST} H59_G=${G} H59_PREFIX=${PREFIX} HOLDNUM=${HOLD//[^0-9]/}
export PATH=${G}/bin:${PATH} CLUSTER=${PREFIX}${HOLD} KUBECONFIG=${R}/kubeconfig
[ "${HOLD}" != smoke ] || export SMOKE=1
log() { echo "== ${ROW}-hold${HOLD} $* $(date -u +%FT%TZ)" | tee -a "${G}/results/campaign.log"; }
free_gb() { df -P -B1G / | awk 'NR==2 {print $4}'; }
used_pct() { df -P / | awk 'NR==2 {gsub("%", "", $5); print $5}'; }
memavail_gib() { awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo; }
aiperf_n() { docker top aiperf010 -eo pid,args 2>/dev/null | awk 'NR>1 && $0 !~ /sleep infinity/' | wc -l; }
low_disk() { if [ "${DISK}" = pct97 ]; then [ "$(used_pct)" -gt 97 ]; else [ "$(free_gb)" -lt 15 ]; fi; }
snapshot() { date -u +%FT%TZ; uptime; free -m; ps -eo pcpu,pid,user,comm --sort=-pcpu | head -11; df -P / "$(readlink -f "${G}")" | tail -2; }
# This campaign's clusters only (${PREFIX}*).
mine() { k3d cluster list -o json 2>/dev/null | python3 -c 'import json,sys; print(" ".join(c["name"] for c in json.load(sys.stdin) if c["name"].startswith(sys.argv[1])))' "${PREFIX}"; }

log "waiting for the lock (mode ${MODE}, arms: ${ARMS_RUN[*]}${REPEATS:+, repeats hold ${REPEATS}})"
exec 9> "${LOCKF}"
flock -w 21600 9 || { log "lock not taken in 21600 s"; exit 4; }
log "lock taken"
waited=0
while :; do
    l1=$(cut -d' ' -f1 /proc/loadavg) a=0
    [ "${AIPERF}" = 0 ] || a=$(aiperf_n)
    if awk -v l="${l1}" -v m="${L1MAX}" 'BEGIN {exit !(l > m)}' || [ "${a}" -gt 0 ]; then
        if [ "${waited}" -ge 3600 ]; then log "FLAGGED: load1 ${l1}, aiperf ${a} after 60 min"; echo "load1 ${l1} aiperf ${a}" > "${R}/flagged"; break; fi
        log "busy (load1 ${l1}, aiperf ${a}): waiting 120 s"
        sleep 120
        waited=$(( waited + 120 ))
        continue
    fi
    break
done
echo "${waited}" > "${R}/wait_s"
snapshot > "${R}/pre.txt"
if low_disk; then log "/ below the disk floor: no hold"; echo "disk: $(used_pct)% used, $(free_gb) GB free" > "${R}/no-hold"; exit 5; fi
if [ "${MEMMIN}" -gt 0 ] && [ "$(memavail_gib)" -lt "${MEMMIN}" ]; then
    log "MemAvailable $(memavail_gib) GiB (< ${MEMMIN}): no hold"; echo "memavail $(memavail_gib) GiB" > "${R}/no-hold"; exit 5
fi
[ "${HOLD}" = smoke ] || [ -f "${RES}/node-image" ] || echo "${H59_IMAGE}" > "${RES}/node-image"
# A spare names the hold it stands in for only once it runs (h59.py reads `repeats`).
[ -z "${REPEATS}" ] || echo "${REPEATS}" > "${R}/repeats"
left=$(mine)
[ -z "${left}" ] || { log "leftover clusters ${left}: deleting first"; k3d cluster delete ${left} > /dev/null 2>&1; }
bash "${HERE}/reproduce.sh" > "${R}/reproduce.txt" 2>&1
sp=""
if [ "${SAMPLER}" = serving ]; then
    python3 "${REPO}/bench/eatflow/crescent/serving.py" "${R}/serving.jsonl" &
    sp=$!
    export SERVING=${R}/serving.jsonl
fi
log "start: ${ARMS_RUN[*]}"
timeout -k 30 "${CAP}" bash "${HERE}/hold-run.sh" "${R}" "${ARMS_RUN[@]}" > "${R}/hold.log" 2>&1 &
cp=$!
# timeout(1) signals its whole process group; the watchdog stops that group below the disk floor.
( while kill -0 "${cp}" 2>/dev/null; do
      sleep 30
      if low_disk; then echo "disk $(used_pct)% used, $(free_gb) GB free at $(date -u +%FT%TZ)" > "${R}/disk-abort"; kill -TERM "${cp}"; break; fi
  done ) &
wd=$!
wait "${cp}"
rc=$?
kill "${wd}" ${sp} 2>/dev/null
[ -z "${sp}" ] || wait "${sp}" 2>/dev/null
left=$(mine)
[ -z "${left}" ] || k3d cluster delete ${left} > /dev/null 2>&1
snapshot > "${R}/post.txt"
echo "${rc}" > "${R}/rc"
log "rc=${rc}; clusters deleted: ${left:-none}; now: $(mine)"
exit "${rc}"
