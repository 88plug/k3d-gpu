#!/usr/bin/env bash
# campaign.sh: H62 end to end on crescent (EXPERIMENTS.md "H62 pre-registration"), from the H62
# checkout at the deploy commit: setup.sh, smoke.sh, then holds (pairs-hold.sh 1, 2, ...) until
# B24's 24 pairs are recorded or 8 holds have run, then the score. teardown.sh runs at the end,
# also after an abort.
#   - setup exit 6 (the host gate still failed after 60 min): the two qwen38 serving pairs are
#     stopped under the box owner's grant (gpu-serving.sh stop, never rm) and setup runs once more;
#     teardown.sh starts them again
#   - a hold that exits 3 (a server not ready with 900 s left) counts as one of the 8 and runs no
#     pair; after 2 such holds in a row no further hold runs (INCONCLUSIVE, not deliverable)
# Records: ~/k3dgpu-bench/results/H62 (campaign.log, setup/, pairs/, hold<N>/, report.txt, vmtax.json).
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
B=${HOME}/k3dgpu-bench
R=${B}/results/H62
MAX_HOLDS=8 NOT_DELIVERABLE_MAX=2
mkdir -p "${R}"
log() { echo "== campaign $* $(date -u +%FT%TZ)" | tee -a "${R}/campaign.log"; }
teardown() { bash "${HERE}/teardown.sh" >> "${R}/teardown.log" 2>&1; log "teardown exit $?"; }
trap teardown EXIT
log "start at $(git -C "${REPO}" rev-parse HEAD)"
bash "${HERE}/setup.sh"
rc=$?
if [ "${rc}" = 6 ]; then
    log "host gate failed: stopping the qwen38 serving pairs (owner's grant; docker stop, never rm)"
    for c in qwen38-a-hr qwen38-b-hr; do
        if bash "${REPO}/bench/eatflow/crescent/gpu-serving.sh" stop "${c}" >> "${R}/serving.log" 2>&1; then
            echo "${c}" >> "${R}/serving-stopped.txt"
        fi
    done
    bash "${HERE}/setup.sh"
    rc=$?
fi
[ "${rc}" = 0 ] || { log "setup exit ${rc}: no hold"; exit "${rc}"; }
bash "${HERE}/smoke.sh"
rc=$?
[ "${rc}" = 0 ] || { log "wiring smoke failed (exit ${rc}): no hold"; exit 9; }
nd=0
for n in $(seq 1 "${MAX_HOLDS}"); do
    left=$(python3 "${HERE}/h62.py" pairs-left "${R}/pairs")
    [ "${left}" -gt 0 ] || break
    log "hold ${n}: ${left} pair(s) left"
    bash "${HERE}/pairs-hold.sh" "${n}"
    rc=$?
    log "hold ${n} exit ${rc}"
    if [ "${rc}" = 3 ]; then
        nd=$(( nd + 1 ))
        [ "${nd}" -lt "${NOT_DELIVERABLE_MAX}" ] || { log "${nd} holds in a row not deliverable: no further hold"; break; }
    else
        nd=0
    fi
done
python3 "${REPO}/bench/eatflow/agentbench.py" pairs-report "${R}/pairs" --hyp H62 --arm-a A1 --arm-b B5 \
    > "${R}/report.txt" 2>&1
log "pairs-report exit $?"
h50=${REPO}/bench/eatflow/results/2026-10-20/H50/pairs/analysis.json
if [ -f "${R}/pairs/analysis.json" ]; then
    python3 "${HERE}/h62.py" vmtax "${h50}" "${R}/pairs/analysis.json" > "${R}/vmtax.json"
fi
