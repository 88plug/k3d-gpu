#!/usr/bin/env bash
# H23 runner, Amendment A2 (EXPERIMENTS.md): the probe under a held background load.
#
# Usage: SUDO_ASKPASS=<helper> LOCK=<live lock> bench/thermal-resource/run.sh OUT_DIR
# Env:   LEVELS    bench/load/load.py levels, in order (default "typical heavy")
#        BUDGET_S  probe budget per level (default 1080; with the 30 s settle and the
#                  probe's 60 s watchdog grace a level stays under 20 min of wall time)
#        LEVEL_WALL_S  start a level only if the lock has been held < this (default 1500
#                  for the second level: heavy runs only when time remains)
#
# Per level, inside one lock hold: load.py start, 30 s settle, probe.sh with
# LOAD_LEVEL set (calibration, budget gate, cluster, runs; the probe deletes its
# cluster on exit), load.py stop. The load is stopped on any exit.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
OUT=${1:?usage: run.sh OUT_DIR}
LOCK=${LOCK:-/tmp/k3d-gpu-live.lock}
LEVELS=${LEVELS:-typical heavy}
BUDGET_S=${BUDGET_S:-1080}
LEVEL_WALL_S=${LEVEL_WALL_S:-1500}
mkdir -p "${OUT}"
OUT=$(cd "${OUT}" && pwd)
LOADPY="${REPO}/bench/load/load.py"

if [ -z "${H23_LOCKED:-}" ]; then
    exec env H23_LOCKED=1 flock -w 14400 "${LOCK}" "$0" "${OUT}"
fi

T0=$(date +%s)
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "${OUT}/runner.log" >&2; }
cleanup() {
    python3 "${LOADPY}" stop >> "${OUT}/runner.log" 2>&1 || true
    log "lock held $(( $(date +%s) - T0 )) s; load stopped"
}
trap cleanup EXIT
trap 'exit 143' TERM INT

log "lock taken; commit $(git -C "${REPO}" rev-parse HEAD); load CPUs $(python3 "${LOADPY}" cpus)"
first=1
for level in ${LEVELS}; do
    if [ "${first}" = 0 ] && [ $(( $(date +%s) - T0 )) -ge "${LEVEL_WALL_S}" ]; then
        log "skip ${level}: lock held $(( $(date +%s) - T0 )) s"
        continue
    fi
    first=0
    log "level ${level}: $(python3 "${LOADPY}" start --level "${level}" --ttl $(( BUDGET_S + 300 )))"
    sleep 30
    rc=0
    OUT="${OUT}/${level}" LOAD_LEVEL="${level}" BUDGET_S="${BUDGET_S}" \
        "${REPO}/bench/thermal-resource/probe.sh" || rc=$?
    log "level ${level}: probe rc=${rc}; $(python3 "${LOADPY}" stop)"
done
