#!/usr/bin/env bash
# apb-hold.sh ARM N RAMP [BASE_DIR]: one H51 hold on crescent under hold.sh (EXPERIMENTS.md
# "H50-H52 pre-registration", H51): `live.sh ARM` in LIVE_MODE=apb at typical load, the ramp
# steps RAMP (e.g. 1,2,4,8,16 or 8,16 with BASE_DIR = the hold that ran N=1), top step 16,
# Gemma 4 QAT (amendment M1), B1's KV cache as shipped (40 GiB). Records: ~/k3dgpu-bench/results/H51/<ARM>-hold<N>.
set -uo pipefail
# Crescent amendment C1: kubelet eviction thresholds for a 95%-full disk (K3D_GPU_K3S_ARGS below).
ARM=$1 N=$2 RAMP=$3 BASE=${4:-}
B=${HOME}/k3dgpu-bench
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
R=${B}/results/H51
mkdir -p "${R}"
base=()
[ -z "${BASE}" ] || base=(LIVE_APB_BASE="${BASE}")
exec "${HERE}/hold.sh" "H51-${ARM}-hold${N}" 2400 env K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%" \
    LIVE_MODE=apb LIVE_CAP=2370 LIVE_LEVEL=typical \
    LIVE_APB_RAMP="${RAMP}" LIVE_APB_TOP=16 "${base[@]}" LIVE_MODEL_SET=gemma4 LIVE_EAT_KV_GIB=40 LIVE_MODELS="${B}/models" \
    LIVE_BLUEPRINT="${B}/eab" LIVE_PY="${B}/mafvenv/bin/python" \
    LIVE_EAT_CHART="${B}/eat-src/core/helm-charts/vllm" PATH="${B}/bin:${PATH}" \
    bash "${HERE}/../live.sh" "${ARM}" "${R}/${ARM}-hold${N}"
