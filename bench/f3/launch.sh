#!/usr/bin/env bash
# Starts the scored F3 run detached: lockrun.sh takes ~/k3dgpu-bench.lock, then live.lock
# (amendment R1), then runs run.sh with a 180 min cap.
set -uo pipefail
R=/home/andrew/.cache/k3dgpu-f3/f3-scored
export K3D_GPU_IMAGE=k3d-gpu-f3node:48e1da90
unset KUBECONFIG HF_TOKEN
cd "${R}" || exit 1
setsid nohup bash "${R}/bin/lockrun.sh" "${R}/src" k3dgpu-f3-eas "${R}/out" 180 > "${R}/lockrun.out" 2>&1 < /dev/null &
echo "launched pid $!"
