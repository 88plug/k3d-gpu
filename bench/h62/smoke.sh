#!/usr/bin/env bash
# smoke.sh: H62's wiring smoke (EXPERIMENTS.md "H62 pre-registration", "Wiring smoke"), after
# setup.sh and before hold 1. It scores nothing, is not one of the 8 holds, and is disclosed in
# the run section.
#   1. B5 through EAS's own stack, in VM b, inside the bench lock: `es_auto_installer.sh validate
#      agent-toolkit`, then integrations/eat/eat-check.sh in EAS mode (PR #260's interface) through
#      EAS's gateway on gemma-4-26b: a chat, a get_weather tool call, and a sandbox run that
#      prints 385
#   2. one H62 hold with the pair-start guard past its end (pairs-hold.sh 0 smoke): both serving
#      pods restart and answer through the harness's LiteLLM, both warm-ups answer, no pair starts
# Exit 0 when both pass; 1 otherwise; 4 when the lock is not taken. Records: ~/k3dgpu-bench/smoke/H62.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
P() { python3 "${HERE}/h62.py" "$@"; }
B=${HOME}/k3dgpu-bench
export H62_STATE=${H62_STATE:-${B}/h62}
S=${B}/smoke/H62
MODEL=$(P pin catalog_name)
CHECK=${REPO}/integrations/eat/eat-check.sh
mkdir -p "${S}"
log() { echo "== smoke $* $(date -u +%FT%TZ)" | tee -a "${S}/smoke.log"; }
G() { ssh -F "${H62_STATE}/ssh_config" h62-b "$@" 9>&-; }
grep -qE '^[[:space:]]+eas\)' "${CHECK}" || { log "eat-check.sh at this commit has no EAS mode (PR #260): no smoke"; exit 1; }
exec 9> "${HOME}/k3dgpu-bench.lock"
flock -w 7200 9 || { log "lock not taken in 7200 s"; exit 4; }
log "lock taken; step 1 in VM b"
G "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q jq=$(P pin jq)" > "${S}/jq.log" 2>&1 \
    || { log "jq not installed in VM b"; exit 1; }
scp -q -F "${H62_STATE}/ssh_config" "${CHECK}" h62-b:h62/eat-check.sh 9>&-
G "cd ~/enterprise-ai-solutions && ./es_auto_installer.sh validate agent-toolkit" > "${S}/validate.log" 2>&1
xv=$?
G ". ~/h62/env && EAT_MODE=eas EAT_MODEL=${MODEL} bash ~/h62/eat-check.sh" > "${S}/eat-check.log" 2>&1
xc=$?
log "validate exit ${xv}, eat-check exit ${xc}"
exec 9>&-
log "step 2: H62-smoke"
bash "${HERE}/pairs-hold.sh" 0 smoke > "${S}/hold.log" 2>&1
xh=$?
warm=$(python3 -c 'import json, sys
rows = [json.loads(x) for x in open(sys.argv[1]) if x.strip()]
print(sum(1 for r in rows if not r.get("error")))' "${S}/pairs/warmups.jsonl" 2>/dev/null || echo 0)
pairs=$(grep -c . "${S}/pairs/pairs.jsonl" 2>/dev/null || true)
log "hold exit ${xh}, warm-ups answered ${warm} of 2, pairs ${pairs:-0}"
[ "${xv}" = 0 ] && [ "${xc}" = 0 ] && [ "${xh}" = 0 ] && [ "${warm}" = 2 ] && [ "${pairs:-0}" = 0 ] || exit 1
log "wiring smoke passed"
