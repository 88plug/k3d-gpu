#!/usr/bin/env bash
# lockrun.sh <k3d-gpu src> <cluster> <out> <cap min>
# Waits (up to 10 h each) for ~/k3dgpu-bench.lock, the desktop holds' lock, then the
# scratchpad live.lock, both with flock -o so no child holds a lock descriptor; then runs
# run.sh under a backstop timeout of the cap plus 5 min. run.sh deletes its cluster on exit.
set -uo pipefail
SRC=$1 CLUSTER=$2 OUT=$3 CAP=$4
BIN=$(cd "$(dirname "$0")" && pwd)
LIVE=/tmp/claude-1000/-home-andrew-k3d-gpu/86e13552-98b8-48e1-b1aa-9fbfea1a0978/scratchpad/live.lock
mkdir -p "${OUT}"
echo "waiting for locks $(date -Is) pid $$" >> "${OUT}/lock.txt"
exec flock -o -w 36000 "${HOME}/k3dgpu-bench.lock" \
    flock -o -w 36000 "${LIVE}" \
    bash -c 't=$(date +%s); echo "locks taken $(date -Is -d "@${t}")" >> "$3/lock.txt"
        timeout --signal=TERM --kill-after=120 $(( $4 * 60 + 300 )) bash "$5/run.sh" "$1" "$2" "$3" "${t}" "$4"
        rc=$?; echo "locks released $(date -Is) run rc ${rc}" >> "$3/lock.txt"; exit "${rc}"' \
    _ "${SRC}" "${CLUSTER}" "${OUT}" "${CAP}" "${BIN}"
