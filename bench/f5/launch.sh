#!/usr/bin/env bash
# Starts the scored F5 run on crescent, detached: step0.sh, then lockrun.sh (the bench lock,
# the disk gate, run.sh with a 180 min cap). The checkout is ~/k3dgpu-bench/k3d-gpu-f5 at the
# run commit; bench/f5 runs from that checkout, so the runner is the committed one.
set -uo pipefail
R="${HOME}/k3dgpu-bench/f5"
SRC="${HOME}/k3dgpu-bench/k3d-gpu-f5"
OUT="${R}/out"
export PATH="${HOME}/k3dgpu-bench/bin:${PATH}"
unset KUBECONFIG HF_TOKEN K3D_GPU_IMAGE
mkdir -p "${OUT}"
cd "${R}" || exit 1
bash "${SRC}/bench/f5/step0.sh" "${SRC}" "${OUT}"
setsid nohup bash "${SRC}/bench/f5/lockrun.sh" "${SRC}" k3dgpu-f5-eat "${OUT}" 180 > "${R}/lockrun.out" 2>&1 < /dev/null &
echo "launched pid $!"
