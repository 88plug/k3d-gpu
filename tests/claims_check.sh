#!/usr/bin/env bash
# Claims guard for the docs. Fails when a line of README.md or docs/*.md makes a
# performance claim without a reference that backs it.
#
# A claim is a number with a performance unit (tok/s, tokens per second, J/token,
# "3x faster", "30%") or a superlative about the project ("fastest", "first-ever",
# "the only tool", "world's first", "first to").
#
# A claim is backed when the same line carries at least one of:
#   [ledger:H2]      a row of the Hypotheses table in EXPERIMENTS.md whose verdict is SUPPORTED
#   [provenance:P1]  a row of docs/provenance.md with a non-empty Source column
#
# Every [ledger:..] and [provenance:..] reference must resolve, claim or not.
# Fenced code blocks, inline code and URLs are not scanned. docs/provenance.md is
# the source of truth for cited numbers and is not scanned either.
#
# Usage: tests/claims_check.sh [repo-root]

set -uo pipefail

ROOT=${1:-"$(cd "$(dirname "$0")/.." && pwd)"}
LEDGER="${ROOT}/EXPERIMENTS.md"
PROVENANCE="${ROOT}/docs/provenance.md"

if [ ! -f "${LEDGER}" ]; then
    echo "claims_check: EXPERIMENTS.md missing at ${LEDGER}"
    exit 1
fi

# "H1=PENDING;H2=SUPPORTED;" from the rows of the "## Hypotheses" section.
ledger=$(awk -F'|' '
    /^## / { inh = ($0 ~ /^## Hypotheses/) }
    inh && /^\| *H[0-9]+ *\|/ {
        id = $2; gsub(/ /, "", id)
        v = ($NF ~ /^ *$/) ? $(NF - 1) : $NF
        gsub(/[^A-Za-z]+/, " ", v); split(v, w, " ")
        printf "%s=%s;", id, toupper(w[1])
    }' "${LEDGER}")

# "P1=OK;P2=NOSOURCE;" from the rows of docs/provenance.md (columns: # | Claim | Source | Retrieved).
provenance=""
if [ -f "${PROVENANCE}" ]; then
    provenance=$(awk -F'|' '
        /^\| *P[0-9]+ *\|/ {
            id = $2; gsub(/ /, "", id)
            src = $4; gsub(/[ \t]/, "", src)
            printf "%s=%s;", id, (src == "" ? "NOSOURCE" : "OK")
        }' "${PROVENANCE}")
fi

files=()
[ -f "${ROOT}/README.md" ] && files+=("${ROOT}/README.md")
for f in "${ROOT}"/docs/*.md; do
    [ -f "${f}" ] && [ "${f}" != "${PROVENANCE}" ] && files+=("${f}")
done

report=""
for f in "${files[@]}"; do
    rel=${f#"${ROOT}/"}
    report+=$(awk -v rel="${rel}" -v ledger="${ledger}" -v prov="${provenance}" '
        function load(s, m,    n, i, kv, p) {
            n = split(s, kv, ";")
            for (i = 1; i <= n; i++) if ((p = index(kv[i], "=")) > 0) m[substr(kv[i], 1, p - 1)] = substr(kv[i], p + 1)
        }
        BEGIN {
            load(ledger, L); load(prov, P)
            num = "[0-9][0-9.,]*"
            claim_re[1] = num " *(tok/s|tokens?/s|tokens? per second)"
            claim_re[2] = num " *(j/tok|j per token|joules? per token)"
            claim_re[3] = num " *(x|×) *(faster|slower|quicker|speed-?ups?|throughput|more|less|higher|lower|better)"
            claim_re[4] = num " *%"
            claim_re[5] = "fastest|quickest"
            claim_re[6] = "first[- ]ever"
            claim_re[7] = "(world|industry)(\047|\342\200\231)?s (first|only|fastest)"
            claim_re[8] = "(^|[^a-z])(first|only) +(tool|launcher|project|solution|way|cluster|distribution|distro|platform|k3d|k3s|kubernetes|open[- ]source)([^a-z]|$)"
            claim_re[9] = "(^|[^a-z])first to [a-z]"
        }
        /^ *(```|~~~)/ { fence = !fence; next }
        fence { next }
        {
            s = $0
            gsub(/`[^`]*`/, "", s)
            gsub(/\]\([^)]*\)/, "]", s)
            gsub(/https?:\/\/[^ )>]+/, "", s)
            low = tolower(s)

            claim = 0
            for (i = 1; i <= 9; i++) if (low ~ claim_re[i]) { claim = 1; break }

            backed = 0; notes = ""; rest = s
            while (match(rest, /\[(ledger|provenance):[A-Za-z0-9]+\]/)) {
                ref = substr(rest, RSTART, RLENGTH)
                rest = substr(rest, RSTART + RLENGTH)
                kind = substr(ref, 2, index(ref, ":") - 2)
                id = substr(ref, index(ref, ":") + 1); sub(/\]$/, "", id)
                if (kind == "ledger") {
                    if (!(id in L)) printf "%s:%d: %s not in EXPERIMENTS.md hypothesis table\n", rel, NR, ref
                    else if (L[id] == "SUPPORTED") backed = 1
                    else notes = notes sprintf("; %s is %s", ref, L[id])
                } else {
                    if (!(id in P)) printf "%s:%d: %s not in docs/provenance.md\n", rel, NR, ref
                    else if (P[id] == "NOSOURCE") printf "%s:%d: %s has no source in docs/provenance.md\n", rel, NR, ref
                    else backed = 1
                }
            }
            if (claim && !backed)
                printf "%s:%d: unbacked claim (no ledger or provenance reference that backs it%s): %s\n", rel, NR, notes, $0
        }' "${f}")$'\n'
done

report=$(sed '/^$/d' <<< "${report}")
if [ -z "${report}" ]; then
    echo "claims_check: ${#files[@]} file(s) clean"
    exit 0
fi
echo "${report}"
unbacked=$(grep -c ': unbacked claim' <<< "${report}")
refs=$(( $(wc -l <<< "${report}") - unbacked ))
echo "claims_check: ${unbacked} unbacked claim(s), ${refs} bad reference(s)"
echo "Back each claim with a SUPPORTED [ledger:Hn] row in EXPERIMENTS.md or a sourced [provenance:Pn] row in docs/provenance.md."
exit 1
