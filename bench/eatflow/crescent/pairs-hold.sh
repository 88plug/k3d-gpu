#!/usr/bin/env bash
# pairs-hold.sh HYP N: one H50 / H50o hold on crescent under hold.sh (LIVE_CAP 2370 inside a
# 2400 s timeout). Records: ~/k3dgpu-bench/results/<HYP>/hold<N>, pairs in .../<HYP>/pairs.
# Model set gemma4 (crescent amendment M1); B's weights mounted read-only into the node.
set -uo pipefail
# Crescent amendment C1: kubelet eviction thresholds for a 95%-full disk (K3D_GPU_K3S_ARGS below).
# Crescent amendment E2: the node image is the pre-registered 0.1.61 pin (shared rule 5), named here
# because k3d-gpu's own default moves with each release. Amendment E1: B1 is EAT release-1.1.0's
# stock (its vllm chart, the c86c42b tree in eat-pr21, and its v0.28.0 image); B3 is unchanged.
NODE_IMAGE=cryptoandcoffee/k3d-gpu:0.1.61@sha256:8cd264aa68c77681d7fa474693944de0db8d55de5881253e4029bf72a67e175c
B1_TAG=v0.28.0@sha256:197354e6475e638eb5ccca09beb078488046a82de0e204e7429bf1a804df9647
HYP=$1 N=$2
B=${HOME}/k3dgpu-bench
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
R=${B}/results/${HYP}
mkdir -p "${R}/pairs"
case ${HYP} in
    H50) extra=(LIVE_MODEL_SET=gemma4 LIVE_EAT_KV_GIB=40 LIVE_MOUNT_B=1) ;;
    H50o) extra=(LIVE_MODEL_SET=gemma4 LIVE_MOUNT_B=1) ;;
    *) echo "pairs-hold.sh: HYP must be H50 or H50o (H53: solo-hold.sh)"; exit 2 ;;
esac
exec "${HERE}/hold.sh" "${HYP}-hold${N}" 2400 env K3D_GPU_IMAGE="${NODE_IMAGE}" K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%" \
    LIVE_MODE=pairs LIVE_CAP=2370 LIVE_HOLD="${N}" \
    LIVE_PAIRS_DIR="${R}/pairs" "${extra[@]}" LIVE_MODELS="${B}/models" LIVE_BLUEPRINT="${B}/eab" \
    LIVE_PY="${B}/mafvenv/bin/python" LIVE_EAT_CHART="${B}/eat-pr21/core/helm-charts/vllm" LIVE_EAT_B1_TAG="${B1_TAG}" \
    LIVE_EAT_OVMS_CHART="${B}/eat-src/core/helm-charts/ovms" PATH="${B}/bin:${PATH}" \
    bash "${HERE}/../live.sh" "${HYP}" "${R}/hold${N}"
