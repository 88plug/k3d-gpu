#!/usr/bin/env bash
# P/E premise guard. P/E placement puts inference and its neighbours on different
# cores; it does not isolate inference latency: P- and E-cores share package power,
# the LLC and DRAM bandwidth (EXPERIMENTS.md H3 and H5, both REFUTED). A line of a
# doc, script, manifest or the EAT patch that states the split as a latency benefit
# fails unless that line or the line next to it (same paragraph) scopes it:
# "placement, not isolation", "not isolation", "stay(s) shared", "does not isolate",
# "not isolated". A [ledger:..] citation alone never passes.
# Exit 0 clean, 1 on an unscoped line, 2 when a scanned file cannot be read.
# Usage: tests/pe_premise_check.sh [repo-root]
set -uo pipefail
ROOT=${1:-"$(cd "$(dirname "$0")/.." && pwd)"}
cd "${ROOT}" || exit 2
benefit='P-cores? (stay|stays|remain|remains|are kept|kept) (free|clear|quiet)|leaves? the P-cores (to|for)|P-cores (free|clear) for inference|isolat(e|es|ed|ing|ion)[^.]*(P-core|E-core|inference)|(P-core|E-core)[^.]*isolat|on fast cores with [a-z ]*on (many )?small|protects? (P-core )?inference'
scoped='not isolation|stays? shared|does not isolate|not isolated'
fail=0
while IFS= read -r f; do
    [ -r "${f}" ] || { echo "pe_premise_check: cannot read ${f}"; exit 2; }
    out=$(awk -v f="${f}" -v b="${benefit}" -v s="${scoped}" '
        { l[NR] = $0; low[NR] = tolower($0) }
        END {
            b = tolower(b); s = tolower(s)
            for (i = 1; i <= NR; i++) {
                if (low[i] !~ b) continue
                if (low[i] ~ s) continue
                if (i > 1 && l[i-1] !~ /^[ \t#]*$/ && low[i-1] ~ s) continue
                if (i < NR && l[i+1] !~ /^[ \t#]*$/ && low[i+1] ~ s) continue
                printf "%s:%d: P/E split stated as a latency benefit, not scoped: %s\n", f, i, substr(l[i], 1, 140)
            }
        }' "${f}") || { echo "pe_premise_check: cannot read ${f}"; exit 2; }
    [ -n "${out}" ] && { echo "${out}"; fail=1; }
done < <({ ls README.md docs/*.md docs/intel/*.md scripts/k3d-gpu share/*.yml 2>/dev/null
           find contrib/eat console -type f \( -name '*.md' -o -name '*.py' -o -name '*.y*ml' -o -name '*.patch' \) 2>/dev/null; } | sort -u)
[ "${fail}" = 0 ] && echo "pe_premise_check: ok"
exit "${fail}"
