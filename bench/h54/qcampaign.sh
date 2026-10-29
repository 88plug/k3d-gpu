#!/usr/bin/env bash
# qcampaign.sh [FIRST_HOLD]: H54q's holds from FIRST_HOLD (default 1) to 3 (amendment Q1's cap,
# runner amendment Q2). Before each hold, `h54.py q-plan` reads the records so far and names the
# arms with items left, in Q1's order R, OVQ, OVB, Lg, Vi; a hold runs them with hold.sh q. Items
# an arm has not answered stay at the front of its next turn. Stops early when no arm is left.
# Then not-run.txt (arms with items left after the cap) and score-q.json.
# shellcheck disable=SC2046  # the arm list splits into words on purpose
set -u
G=${HOME}/k3dgpu-gemma
H=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
R=${G}/results/H54q
mkdir -p "${R}"
for h in $(seq "${1:-1}" 3); do
    arms=$(python3 "${H}/h54.py" q-plan "${R}")
    if [ -z "${arms}" ]; then
        echo "== H54q: no arm has items left before hold ${h} $(date -u +%FT%TZ)" | tee -a "${G}/results/campaign.log"
        break
    fi
    echo "${arms}" > "${R}/plan-hold${h}.txt"
    bash "${H}/hold.sh" q "${h}" ${arms}
done
echo "arms with items left after the cap: $(python3 "${H}/h54.py" q-plan "${R}")" > "${R}/not-run.txt"
for a in R OVQ OVB Lg Vi; do echo "${a}: $(python3 "${H}/h54.py" q-todo "${R}" "${a}" | wc -w) items not answered" >> "${R}/not-run.txt"; done
python3 "${H}/h54.py" score-q "${R}" > "${R}/score-q.json" 2>&1
echo "== H54q campaign done $(date -u +%FT%TZ)" | tee -a "${G}/results/campaign.log"
