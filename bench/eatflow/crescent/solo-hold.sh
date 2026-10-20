#!/usr/bin/env bash
# solo-hold.sh N ARM: one H53 solo hold on crescent under hold.sh (LIVE_CAP 2370 inside a 2400 s
# timeout): ARM (AG or B2) alone on the Arc Pro B70s, Qwen3.8-27B (model set qwen38). Holds go
# AG, B2, B2, AG (EXPERIMENTS.md "H53 pre-registration"). The caller sets LIVE_GPU_FREE (the
# cards the hold may use) and, after the wiring smoke, LIVE_B2_LADDER. Records:
# ~/k3dgpu-bench/results/H53/hold<N>, the shared solo directory .../H53/pairs.
set -uo pipefail
# Crescent amendment C1: kubelet eviction thresholds for a 95%-full disk (K3D_GPU_K3S_ARGS below).
N=$1 ARM=$2
B=${HOME}/k3dgpu-bench
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
R=${B}/results/H53
mkdir -p "${R}/pairs"
case ${ARM} in AG|B2) ;; *) echo "solo-hold.sh: ARM must be AG or B2"; exit 2 ;; esac
exec "${HERE}/hold.sh" "H53-hold${N}-${ARM}" 2400 env K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%" \
    LIVE_MODE=solo LIVE_SOLO="${ARM}" LIVE_CAP=2370 LIVE_HOLD="${N}" LIVE_PAIRS_DIR="${R}/pairs" \
    LIVE_MODEL_SET=qwen38 LIVE_MOUNT_B=1 LIVE_GPU_FREE="${LIVE_GPU_FREE:?set LIVE_GPU_FREE}" \
    LIVE_B2_TP="${LIVE_B2_TP:-4}" LIVE_B2_LADDER="${LIVE_B2_LADDER:-}" \
    LIVE_EAT_XPU_CHART="${B}/eat-pr21/core/helm-charts/vllm" LIVE_MODELS="${B}/models" LIVE_BLUEPRINT="${B}/eab" \
    LIVE_PY="${B}/mafvenv/bin/python" PATH="${B}/bin:${PATH}" \
    bash "${HERE}/../live.sh" H53 "${R}/hold${N}"
