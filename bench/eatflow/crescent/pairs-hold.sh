#!/usr/bin/env bash
# pairs-hold.sh HYP N: one H50 / H50o hold on crescent under hold.sh (LIVE_CAP 2370 inside a
# 2400 s timeout). Records: ~/k3dgpu-bench/results/<HYP>/hold<N>, pairs in .../<HYP>/pairs.
set -uo pipefail
HYP=$1 N=$2
B=${HOME}/k3dgpu-bench
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
R=${B}/results/${HYP}
mkdir -p "${R}/pairs"
kv=()
[ "${HYP}" = H50 ] && kv=(LIVE_EAT_KV_GIB=40)
exec "${HERE}/hold.sh" "${HYP}-hold${N}" 2400 env LIVE_MODE=pairs LIVE_CAP=2370 LIVE_HOLD="${N}" \
    LIVE_PAIRS_DIR="${R}/pairs" "${kv[@]}" LIVE_MODELS="${B}/models" LIVE_BLUEPRINT="${B}/eab" \
    LIVE_PY="${B}/mafvenv/bin/python" LIVE_EAT_CHART="${B}/eat-src/core/helm-charts/vllm" \
    LIVE_EAT_OVMS_CHART="${B}/eat-src/core/helm-charts/ovms" PATH="${B}/bin:${PATH}" \
    bash "${HERE}/../live.sh" "${HYP}" "${R}/hold${N}"
