#!/usr/bin/env bash
# lockrun.sh <k3d-gpu src> <cluster> <out> <cap min>
# Waits (up to 10 h) for the desktop bench lock, K3DGPU_LIVE_LOCK (default
# ~/k3dgpu-bench.lock, the lock the H54, H55 and H57dp holds and the documented probes
# take), with flock -o so no child holds a lock descriptor; then runs run.sh under a
# backstop timeout of the cap plus 5 min. run.sh deletes its cluster on exit.
# EXPERIMENTS.md, "F3 note N1", records why this is one lock on a lasting path.
set -uo pipefail
SRC=$1 CLUSTER=$2 OUT=$3 CAP=$4
BIN=$(cd "$(dirname "$0")" && pwd)
LOCK=${K3DGPU_LIVE_LOCK:-${HOME}/k3dgpu-bench.lock}
mkdir -p "${OUT}"
echo "waiting for ${LOCK} $(date -Is) pid $$" >> "${OUT}/lock.txt"
exec flock -o -w 36000 "${LOCK}" \
    bash -c 't=$(date +%s); echo "lock taken $(date -Is -d "@${t}")" >> "$3/lock.txt"
        timeout --signal=TERM --kill-after=120 $(( $4 * 60 + 300 )) bash "$5/run.sh" "$1" "$2" "$3" "${t}" "$4"
        rc=$?; echo "lock released $(date -Is) run rc ${rc}" >> "$3/lock.txt"; exit "${rc}"' \
    _ "${SRC}" "${CLUSTER}" "${OUT}" "${CAP}" "${BIN}"
