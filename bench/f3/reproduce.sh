#!/usr/bin/env bash
# reproduce.sh <k3d-gpu src> <cluster> <out> <cap min>
# Runs F3's run.sh as lockrun.sh did, on a lock path that outlives a session (EXPERIMENTS.md,
# "F3 note N1"). lockrun.sh is the record of the 2026-11-01 run (runner.sha256) and keeps
# its second lock, a file in one session's scratchpad directory that is gone after a reboot.
# Waits (up to 10 h) for K3DGPU_LIVE_LOCK, default ~/k3dgpu-bench.lock (amendment R1's first
# lock, the desktop holds' lock), with flock -o so no child holds a lock descriptor; then runs
# run.sh under a backstop timeout of the cap plus 5 min. run.sh deletes its cluster on exit.
set -uo pipefail
if [ $# -ne 4 ]; then
    echo "usage: reproduce.sh <k3d-gpu src> <cluster> <out> <cap min>" >&2
    exit 2
fi
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
