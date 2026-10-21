#!/usr/bin/env bash
# campaign.sh FIRST_HOLD: H54a's holds from FIRST_HOLD to 3 (amendment R1's hold plan, R3's carry).
# Hold 1: B1g Lg Lg-mtp A1g A1o. Hold 2: the arms hold 1 carries, then Vq Vq-mtp. Hold 3: the arms
# hold 2 carries, then Vi Sg B3g. A hold carries its not-reached arms and, once per arm, an arm whose
# server stopped answering after readiness (h54.py carry). Then not-run.txt and score-a.json.
# shellcheck disable=SC2046  # arm lists split into words on purpose
set -u
G=${HOME}/k3dgpu-gemma
H=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
R=${G}/results/H54a
cd "${G}/h54a" || exit 1
plan() {  # plan HOLD: the hold's arm list
    case $1 in
        1) echo "B1g Lg Lg-mtp A1g A1o" ;;
        2) echo "$(python3 "${H}/h54.py" carry "${R}" 1 $(plan 1)) Vq Vq-mtp" ;;
        3) echo "$(python3 "${H}/h54.py" carry "${R}" 2 $(plan 2)) Vi Sg B3g" ;;
    esac
}
for h in $(seq "${1:-1}" 3); do
    python3 "${H}/h54.py" recheck "${R}" | tee -a "${G}/results/campaign.log"
    bash "${H}/hold.sh" a "${h}" $(plan "${h}")
done
python3 "${H}/h54.py" recheck "${R}" | tee -a "${G}/results/campaign.log"
echo "not run (carried out of hold 3): $(python3 "${H}/h54.py" carry "${R}" 3 $(plan 3))" > "${R}/not-run.txt"
python3 "${H}/h54.py" score-a "${R}" > "${R}/score-a.json" 2>&1
echo "== H54a campaign done $(date -u +%FT%TZ)" >> "${G}/results/campaign.log"
