#!/usr/bin/env bash
# pairs-hold.sh N [smoke]: one H62 hold on crescent (EXPERIMENTS.md "H62 pre-registration") under
# bench/eatflow/crescent/hold.sh: the lock, the hold-start rule, the 97% disk guard, the serving
# sampler and the cleanup, LIVE_CAP 2370 inside a 2400 s timeout. live.sh runs H50's hold with both
# servers in their KVM guests (arm-A1, arm-B5): each hold restarts both serving pods first.
# Records: ~/k3dgpu-bench/results/H62/hold<N>, pairs in ~/k3dgpu-bench/results/H62/pairs, the
# hold's own records in ~/k3dgpu-bench/runs/H62-hold<N>.
# smoke: the wiring smoke instead (H62-smoke, the pair-start guard past the hold so no pair starts,
# records under ~/k3dgpu-bench/smoke/H62); it is not one of the 8 holds.
set -uo pipefail
N=${1:?usage: pairs-hold.sh N [smoke]}
B=${HOME}/k3dgpu-bench
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
case ${2:-hold} in
    hold) name=H62-hold${N} R=${B}/results/H62 guard=600 ;;
    smoke) name=H62-smoke R=${B}/smoke/H62 guard=99999 ;;
    *) echo "usage: pairs-hold.sh N [smoke]"; exit 2 ;;
esac
mkdir -p "${R}/pairs"
# The guests are this campaign's arms: the serving covariate records them as vm_cpu (serving.py).
export SERVING_VMS=h62-vm-a,h62-vm-b
exec "${HERE}/../eatflow/crescent/hold.sh" "${name}" 2400 env \
    LIVE_MODE=pairs LIVE_CAP=2370 LIVE_HOLD="${N}" LIVE_PAIRS_DIR="${R}/pairs" LIVE_PAIRS_GUARD="${guard}" \
    LIVE_MODEL_SET=gemma4 LIVE_MODELS="${B}/models" LIVE_BLUEPRINT="${B}/eab" LIVE_PY="${B}/mafvenv/bin/python" \
    LIVE_REMOTE_A="${HERE}/arm-A1" LIVE_REMOTE_B="${HERE}/arm-B5" H62_STATE="${H62_STATE:-${B}/h62}" \
    PATH="${B}/bin:${PATH}" \
    bash "${HERE}/../eatflow/live.sh" H62 "${R}/hold${N}"
