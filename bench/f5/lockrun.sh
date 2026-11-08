#!/usr/bin/env bash
# lockrun.sh <k3d-gpu checkout> <cluster> <out> <cap min>
# Waits (up to 6 h) for crescent's bench lock, ~/k3dgpu-bench.lock, with flock -o so no
# child holds the lock descriptor; checks the disk gate (/ under 90% used); then runs
# run.sh under a backstop timeout of the cap plus 5 min. run.sh deletes its cluster on exit.
set -uo pipefail
SRC=$1 CLUSTER=$2 OUT=$3 CAP=$4
BIN=$(cd "$(dirname "$0")" && pwd)
mkdir -p "${OUT}"
echo "waiting for the bench lock $(date -Is) pid $$" >> "${OUT}/lock.txt"
exec flock -o -w 21600 "${HOME}/k3dgpu-bench.lock" \
    bash -c 't=$(date +%s); echo "lock taken $(date -Is -d "@${t}")" >> "$3/lock.txt"
        used=$(df --output=pcent / | tail -1 | tr -dc 0-9)
        if [ "${used}" -ge 90 ]; then echo "disk gate: / ${used}% used, not starting" >> "$3/lock.txt"; exit 4; fi
        echo "disk gate: / ${used}% used" >> "$3/lock.txt"
        timeout --signal=TERM --kill-after=120 $(( $4 * 60 + 300 )) bash "$5/run.sh" "$1" "$2" "$3" "${t}" "$4"
        rc=$?; echo "lock released $(date -Is) run rc ${rc}" >> "$3/lock.txt"; exit "${rc}"' \
    _ "${SRC}" "${CLUSTER}" "${OUT}" "${CAP}" "${BIN}"
