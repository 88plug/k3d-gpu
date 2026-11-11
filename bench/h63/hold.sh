#!/usr/bin/env bash
# hold.sh HOST HOLD [REPEATS]: one H63 hold (EXPERIMENTS.md "H63 pre-registration", "H63
# amendment R1" and "H63w amendment R1") on HOST = desktop (H63d, i9-13900T) or wildct (H63w,
# i7-13700, run as bench/wildct/hold.sh's command: bench/wildct/row.sh h63w). HOLD: 1-3 perf in
# the orders (Od, Ld-mtp, Ls), (Ld-mtp, Ls, Od), (Ls, Od, Ld-mtp); 4 quality in (Ls, Od, Ld-mtp);
# a spare 5 or 6 repeats hold REPEATS in its order, when the record standing for it (the hold, or
# spare 5's repeat of it) left an arm not measured; spare 6 runs only after spare 5. Smoke runs
# hold 1's arms with no scored request (discarded, disclosed). Each hold runs once: a hold that
# started (its `started` file, written once the floors pass) counts, failed or interrupted.
#
# Before the lock it refuses (exit 2) a host whose CPU class is not the profile's (the model
# number the optimizer matches, exactly: run.py cpu), a node image not pinned by digest or not
# the one the row's first hold ran, a ${G}/bin/k3d that is not bench/h63/k3d-shim.sh, and model
# files prep.sh has not left in ${G}/models (both drafters and Od's graph.pbtxt checked by
# sha256). Then the host's rules (run.py profile): its lock (LOCK, flock -w 21600); the
# hold-start rule (load1 <= load1_max, 2-min polls, at most 60 min, then the hold runs flagged);
# the floors (disk_gb free on / and MemAvailable >= mem_gib), or no hold (exit 5). It reads the
# co-tenants (every container's state and restart count, and, where the profile's gpus is read,
# the GPUs' memory in use; wildct reads no GPU, rule W8) before and after, creates cluster
# <prefix><HOLD>, runs the arms (hold-run.sh) under `timeout -k 30 <cap_s>` with a disk watchdog,
# and deletes this campaign's clusters at the end, also on failure.
# Records: ${G}/results/<row>/hold<HOLD>/ (smoke: ${G}/smoke/<row>-<UTC>/).
#   G  campaign dir (default from the profile);  H63_IMAGE  the k3d-gpu node image, name:tag@sha256
#   LOCK  the host lock (desktop ~/k3dgpu-bench.lock; wildct's private inner lock, inside rule W1's)
#   K3D_REAL  the shim's real k3d
#   SUDO_ASKPASS or K3D_GPU_RAPL_EXEC  for energy.sh's RAPL reader
#   H63_CPUINFO  the cpuinfo file the host check reads (default /proc/cpuinfo)
set -uo pipefail
HOST=${1:?usage: hold.sh HOST HOLD [REPEATS]} HOLD=${2:?usage: hold.sh HOST HOLD [REPEATS]} REPEATS=${3:-}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
RUN=${HERE}/run.py
python3 "${RUN}" profile "${HOST}" > /dev/null || exit 2
pfield() { python3 "${RUN}" profile "${HOST}" | sed -n "s/^$1=//p"; }
ROW=$(pfield row) PREFIX=$(pfield prefix) CPU=$(pfield cpu) L1MAX=$(pfield load1_max)
DISKMIN=$(pfield disk_gb) MEMMIN=$(pfield mem_gib) CAP=$(pfield cap_s) GPUS=$(pfield gpus)
G=${G:-$(pfield g)}
G=${G/#\~/${HOME}}
order=$(python3 "${RUN}" hold "${HOLD}" ${REPEATS:+"${REPEATS}"}) || exit 2
read -r MODE ARMS_RUN <<< "${order}"
read -r -a ARMS_RUN <<< "${ARMS_RUN}"
got=$(python3 "${RUN}" cpu "${HOST}" "${H63_CPUINFO:-/proc/cpuinfo}") \
    || { echo "hold.sh: this host's CPU class is ${got:-unknown}, not the ${CPU} that ${ROW} registers" >&2; exit 2; }
[[ "${H63_IMAGE:-}" =~ ^[^@]+:[^@]+@sha256:[0-9a-f]{64}$ ]] \
    || { echo "hold.sh: set H63_IMAGE to the k3d-gpu node image as name:tag@sha256:<digest>" >&2; exit 2; }
cmp -s "${HERE}/k3d-shim.sh" "${G}/bin/k3d" || { echo "hold.sh: ${G}/bin/k3d is not bench/h63/k3d-shim.sh (install it first)" >&2; exit 2; }
python3 "${RUN}" models "${G}" >&2 || { echo "hold.sh: run bench/h63/prep.sh ${HOST} first" >&2; exit 2; }
RES=${G}/results/${ROW}
case ${HOLD} in smoke) R=${G}/smoke/${ROW}-$(date -u +%Y%m%dT%H%M%SZ) ;; *) R=${RES}/hold${HOLD} ;; esac
[ ! -e "${R}/rc" ] && [ ! -e "${R}/started" ] \
    || { echo "hold.sh: hold ${HOLD} has run (${R}); a failed hold counts, interrupted or not, and a spare repeats it" >&2; exit 2; }
if [ -n "${REPEATS}" ]; then
    why=$(python3 "${RUN}" spare-ok "${RES}" "${REPEATS}" "${HOLD}") || { echo "hold.sh: spare ${HOLD} cannot repeat hold ${REPEATS}: ${why}" >&2; exit 2; }
fi
if [ "${HOLD}" != smoke ] && [ -f "${RES}/node-image" ] && [ "$(cat "${RES}/node-image")" != "${H63_IMAGE}" ]; then
    echo "hold.sh: ${ROW}'s holds run node image $(cat "${RES}/node-image"), not ${H63_IMAGE}" >&2
    exit 2
fi
LOCKF=${LOCK:?set LOCK to this host\'s lock file}
mkdir -p "${R}" "${G}/results"
export REPO G MODE H63_IMAGE H63_PROFILE=${HOST} H63_G=${G} H63_PREFIX=${PREFIX}
export PATH=${G}/bin:${PATH} CLUSTER=${PREFIX}${HOLD} KUBECONFIG=${R}/kubeconfig
[ "${HOLD}" != smoke ] || export SMOKE=1
log() { echo "== ${ROW}-hold${HOLD} $* $(date -u +%FT%TZ)" | tee -a "${G}/results/campaign.log"; }
free_gb() { df -P -B1G / | awk 'NR==2 {print $4}'; }
memavail_gib() { awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo; }
snapshot() { date -u +%FT%TZ; uptime; free -m; ps -eo pcpu,pid,user,comm --sort=-pcpu | head -11; df -P / "$(readlink -f "${G}")" | tail -2; }
# The co-tenants, read only: every container's state, start time and restart count, and, where
# the profile reads them, the GPUs' memory in use (no arm uses a GPU; the node has no GPU device
# plugin). On wildct no GPU is read (rule W8).
cotenants() {
    date -u +%FT%TZ
    docker ps -aq | xargs -r docker inspect -f '{{.Name}} {{.State.Status}} {{.State.StartedAt}} restarts={{.RestartCount}}' 2>/dev/null | sort
    if [ "${GPUS}" = read ]; then
        nvidia-smi --query-gpu=index,name,memory.used --format=csv,noheader 2>/dev/null || echo "nvidia-smi: not available"
    else
        echo "GPUs: not read (${ROW}, rule W8)"
    fi
}
# This campaign's clusters only (${PREFIX}*).
mine() { k3d cluster list -o json 2>/dev/null | python3 -c 'import json,sys; print(" ".join(c["name"] for c in json.load(sys.stdin) if c["name"].startswith(sys.argv[1])))' "${PREFIX}"; }

log "waiting for the lock (mode ${MODE}, arms: ${ARMS_RUN[*]}${REPEATS:+, repeats hold ${REPEATS}})"
exec 9> "${LOCKF}"
flock -w 21600 9 || { log "lock not taken in 21600 s"; exit 4; }
log "lock taken"
waited=0
while :; do
    l1=$(cut -d' ' -f1 /proc/loadavg)
    if awk -v l="${l1}" -v m="${L1MAX}" 'BEGIN {exit !(l > m)}'; then
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
if [ "$(free_gb)" -lt "${DISKMIN}" ]; then log "/ has $(free_gb) GB free (< ${DISKMIN}): no hold"; echo "free $(free_gb) GB" > "${R}/no-hold"; exit 5; fi
if [ "$(memavail_gib)" -lt "${MEMMIN}" ]; then
    log "MemAvailable $(memavail_gib) GiB (< ${MEMMIN}): no hold"; echo "memavail $(memavail_gib) GiB" > "${R}/no-hold"; exit 5
fi
[ "${HOLD}" = smoke ] || [ -f "${RES}/node-image" ] || echo "${H63_IMAGE}" > "${RES}/node-image"
# From here the hold counts, also if it is interrupted before it records its rc.
date -u +%FT%TZ > "${R}/started"
# A spare names the hold it stands in for only once it runs (h63.py reads `repeats`).
[ -z "${REPEATS}" ] || echo "${REPEATS}" > "${R}/repeats"
cotenants > "${R}/cotenants-pre.txt" 2>&1
left=$(mine)
[ -z "${left}" ] || { log "leftover clusters ${left}: deleting first"; k3d cluster delete ${left} > /dev/null 2>&1; }
bash "${HERE}/reproduce.sh" > "${R}/reproduce.txt" 2>&1
log "start: ${ARMS_RUN[*]}"
timeout -k 30 "${CAP}" bash "${HERE}/hold-run.sh" "${R}" "${ARMS_RUN[@]}" > "${R}/hold.log" 2>&1 &
cp=$!
# timeout(1) signals its whole process group; the watchdog stops that group below the disk floor.
( while kill -0 "${cp}" 2>/dev/null; do
      sleep 30
      if [ "$(free_gb)" -lt "${DISKMIN}" ]; then echo "/ free $(free_gb) GB at $(date -u +%FT%TZ)" > "${R}/disk-abort"; kill -TERM "${cp}"; break; fi
  done ) &
wd=$!
wait "${cp}"
rc=$?
kill "${wd}" 2>/dev/null
left=$(mine)
[ -z "${left}" ] || k3d cluster delete ${left} > /dev/null 2>&1
snapshot > "${R}/post.txt"
cotenants > "${R}/cotenants-post.txt" 2>&1
echo "${rc}" > "${R}/rc"
log "rc=${rc}; clusters deleted: ${left:-none}; now: $(mine)"
exit "${rc}"
