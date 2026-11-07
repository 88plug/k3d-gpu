#!/usr/bin/env bash
# campaign.sh: H62r end to end on crescent (EXPERIMENTS.md "H62r pre-registration"), from the H62r
# checkout at the deploy commit: setup.sh, smoke.sh, then holds (pairs-hold.sh 1, 2, ...) until
# B24's 24 pairs are recorded or 8 holds have run, then the score. teardown.sh runs at the end,
# also after an abort.
#   - at most 3 setup attempts in all (attempts.txt, one line per start); a fourth start exits 10
#     before anything is started
#   - a start after any hold has run (a hold<N> directory in the records) exits 11 before anything
#     is started, and is not an attempt
#   - the re-run trigger (upstream.sh, up to 3 reads 60 s apart) before setup and before each
#     hold, into upstream.txt. Fired or unreadable before setup: exit 12 before anything starts,
#     and no attempt is spent. Fired before hold 1: exit 12 with no hold, for the dated amendment
#     that re-pins B5. Unreadable before a hold, or fired after hold 1: recorded, and the holds go
#     on at these pins
#   - setup exit 6 (the host gate still failed after 60 min): the two qwen38 serving pairs are
#     stopped under the box owner's grant (gpu-serving.sh stop, never rm) and setup runs once more;
#     teardown.sh starts them again
#   - a hold that exits 3 (a server not ready with 900 s left) counts as one of the 8 and runs no
#     pair; after 2 such holds in a row no further hold runs (INCONCLUSIVE, not deliverable)
# Records: ~/k3dgpu-bench/results/H62r (campaign.log, attempts.txt, upstream.txt, setup/, pairs/,
# hold<N>/, report.txt, vmtax.json).
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
B=${HOME}/k3dgpu-bench
R=${B}/results/H62r
MAX_HOLDS=8 NOT_DELIVERABLE_MAX=2 ATTEMPTS_MAX=3
mkdir -p "${R}"
log() { echo "== campaign $* $(date -u +%FT%TZ)" | tee -a "${R}/campaign.log"; }
# trigger PHASE: the re-run trigger's line ("fired: ...", upstream.sh's stdout) into upstream.txt.
# Status 0 fired, 1 not, 2 unreadable (no such line in 3 reads; its errors are recorded).
trigger() {
    local out="" err="${R}/upstream.err" i
    for i in 1 2 3; do
        if out=$(bash "${HERE}/upstream.sh" 2> "${err}") && [[ "${out}" == "fired: "* ]]; then
            echo "$(date -u +%FT%TZ) $1 ${out}" >> "${R}/upstream.txt"
            [ "${out}" != "fired: none" ]
            return
        fi
        [ "${i}" = 3 ] || sleep "${H62R_TRIGGER_RETRY_S:-60}"
    done
    echo "$(date -u +%FT%TZ) $1 unreadable: $(paste -sd' ' "${err}")${out:+ (output: ${out//$'\n'/ })}" >> "${R}/upstream.txt"
    return 2
}
# Holds count toward the 8 in all and their records are measured data. This script numbers its
# holds from 1, so once any hold has run it refuses to start: a dated amendment says how the row
# goes on.
held=$(find "${R}" -mindepth 1 -maxdepth 1 -type d -name 'hold[0-9]*' -printf '%f\n' | sort -V | paste -sd' ')
if [ -n "${held}" ]; then
    log "holds already recorded (${held}): no start"
    exit 11
fi
tries=$(grep -c . "${R}/attempts.txt" 2>/dev/null)
if [ "${tries:-0}" -ge "${ATTEMPTS_MAX}" ]; then
    log "setup attempt bound reached (${tries:-0} of ${ATTEMPTS_MAX}): no further attempt"
    exit 10
fi
trigger setup
case $? in
    0) log "re-run trigger fired before setup ($(tail -1 "${R}/upstream.txt")): no setup until a dated amendment re-pins B5"; exit 12 ;;
    2) log "re-run trigger unreadable before setup: no setup"; exit 12 ;;
esac
echo "$(date -u +%FT%TZ) $(git -C "${REPO}" rev-parse HEAD)" >> "${R}/attempts.txt"
teardown() { bash "${HERE}/teardown.sh" >> "${R}/teardown.log" 2>&1; log "teardown exit $?"; }
trap teardown EXIT
log "start (attempt $(( ${tries:-0} + 1 )) of ${ATTEMPTS_MAX}) at $(git -C "${REPO}" rev-parse HEAD)"
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
    left=$(python3 "${HERE}/h62r.py" pairs-left "${R}/pairs")
    [ "${left}" -gt 0 ] || break
    trigger "hold${n}"
    case $? in
        0) if [ "${n}" = 1 ]; then
               log "re-run trigger fired before hold 1 ($(tail -1 "${R}/upstream.txt")): no hold until a dated amendment re-pins B5"
               exit 12
           fi
           log "re-run trigger fired after hold 1: H62r's holds finish at these pins" ;;
        2) log "re-run trigger unreadable before hold ${n}: recorded" ;;
    esac
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
python3 "${REPO}/bench/eatflow/agentbench.py" pairs-report "${R}/pairs" --hyp H62r --arm-a A1 --arm-b B5 \
    > "${R}/report.txt" 2>&1
log "pairs-report exit $?"
h50=${REPO}/bench/eatflow/results/2026-10-20/H50/pairs/analysis.json
if [ -f "${R}/pairs/analysis.json" ]; then
    python3 "${HERE}/h62r.py" vmtax "${h50}" "${R}/pairs/analysis.json" > "${R}/vmtax.json"
fi
