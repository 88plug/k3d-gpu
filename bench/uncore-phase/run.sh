#!/usr/bin/env bash
# H29 runner (EXPERIMENTS.md): one lock-held live window, at most 20 min of wall time,
# for bench/uncore-phase/probe.py.
#
# Usage: SUDO_ASKPASS=<helper> bench/uncore-phase/run.sh [OUT_DIR]   (or run as root)
#   run-locked.sh uses sudo -A for every root step (sudo -n without SUDO_ASKPASS).
# Env:   LOCK        flock file shared by every live experiment on the box
#        BUDGET_S    wall-time budget per load level while the lock is held (default 1200)
#        LEVELS      bench/load/load.py levels, in order (default "typical heavy")
#        K3D_GPU_CLUSTER (default k3dgpu-i-uncore-phase)
#
# Steps: wait until no k3dgpu-f-* cluster exists, take the lock, snapshot every knob
# the probe may write, create the cluster with a scratch KUBECONFIG, `agent up` on the
# CPU (P-core balloon), port-forward, run the probe with the time that is left. On
# exit (always): re-write the snapshot, delete the cluster, record wall_s.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
OUT=${1:-${PWD}/h29-out}
LOCK=${LOCK:-/tmp/k3d-gpu-live.lock}
mkdir -p "${OUT}"
OUT=$(cd "${OUT}" && pwd)

while k3d cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -q '^k3dgpu-f-'; do
    echo "waiting: a k3dgpu-f-* cluster exists"; sleep 30
done

exec flock -w 14400 "${LOCK}" "${HERE}/run-locked.sh" "${REPO}" "${OUT}"
