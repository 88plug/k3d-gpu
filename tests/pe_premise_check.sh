#!/usr/bin/env bash
# P/E premise guard: a phrase check, not a semantic one. P/E placement puts
# inference and its neighbours on different cores; it does not isolate inference
# latency, because P- and E-cores share package power, the LLC and DRAM bandwidth
# (EXPERIMENTS.md H3 and H5, both REFUTED).
#
# It flags a line that states the split as a latency benefit in one of the known
# phrasings (the benefit pattern below: "stay free for inference", "leaves the
# P-cores to inference", "reserved for inference", "off/away from the P-cores",
# "away from ... agent tools", "never slow", "unaffected", "keeps more of the
# budget", "isolates ... inference", ...), and a line that still calls H5 unmeasured. A flagged line passes only when
#   - it, or the next or previous non-blank line, says "not isolation", "does not
#     isolate" or "not isolated", or
#   - the line itself says package power, bandwidth, the LLC or the cache
#     "stay(s) shared".
# A [ledger:..] citation alone never passes. A phrasing outside the pattern is not
# caught, so this does not prove the claim cannot come back.
#
# Scanned: README.md, docs/**/*.md, scripts/*, share/*.yml|yaml, contrib/eat and
# console. Not scanned: EXPERIMENTS.md, whose rows state hypotheses (refuted ones
# included) in their own words.
# Exit 0 clean, 1 on an unscoped line, 2 when a scanned file cannot be read.
# Usage: tests/pe_premise_check.sh [repo-root]
set -uo pipefail
ROOT=${1:-"$(cd "$(dirname "$0")/.." && pwd)"}
cd "${ROOT}" || exit 2
benefit='P-cores? (stay|stays|remain|remains|are kept|kept) (free|clear|quiet)|leaves? the P-cores (to|for)|(free|clear|reserved) for inference|(off|away from) the P-cores|away from [a-z -]*(neighbou?rs?|agents?|sandbox(es)?|tools?)|never slows?|(latency|inference|prefill|decode|P-cores?)[^.]*unaffected|keeps? more of the budget|isolat(e|es|ed|ing|ion)[^.]*(P-core|E-core|inference)|(P-core|E-core)[^.]*isolat|on fast cores with [a-z ]*on (many )?small|protects? (P-core )?inference|until (EXPERIMENTS[.]md )?H5 (is measured|runs)'
near='not isolation|does not isolate|not isolated'
same='(power|bandwidth|llc|cache)[^.]*stays? shared'
fail=0
while IFS= read -r f; do
    [ -r "${f}" ] || { echo "pe_premise_check: cannot read ${f}"; exit 2; }
    out=$(awk -v f="${f}" -v b="${benefit}" -v n="${near}" -v s="${same}" '
        { l[NR] = $0; low[NR] = tolower($0) }
        END {
            b = tolower(b); n = tolower(n); s = tolower(s)
            for (i = 1; i <= NR; i++) {
                if (low[i] !~ b) continue
                if (low[i] ~ n || low[i] ~ s) continue
                if (i > 1 && l[i-1] !~ /^[ \t#]*$/ && low[i-1] ~ n) continue
                if (i < NR && l[i+1] !~ /^[ \t#]*$/ && low[i+1] ~ n) continue
                printf "%s:%d: P/E split stated as a latency benefit, not scoped: %s\n", f, i, substr(l[i], 1, 140)
            }
        }' "${f}") || { echo "pe_premise_check: cannot read ${f}"; exit 2; }
    [ -n "${out}" ] && { echo "${out}"; fail=1; }
done < <({ ls README.md 2>/dev/null
           find docs -type f -name '*.md' 2>/dev/null
           find scripts -maxdepth 1 -type f 2>/dev/null
           ls share/*.yml share/*.yaml 2>/dev/null
           find contrib/eat console -type f \( -name '*.md' -o -name '*.py' -o -name '*.y*ml' -o -name '*.patch' \) 2>/dev/null; } | sort -u)
[ "${fail}" = 0 ] && echo "pe_premise_check: ok"
exit "${fail}"
