#!/usr/bin/env bash
# Claims guard for the docs. Fails when a line of README.md, docs/*.md or
# docs/intel/*.md makes a performance claim without a reference that backs it.
#
# A claim is a number with a performance unit (tok/s, tok/sec, t/s, TPS, tokens
# per second, J/token, "3x faster", "10 times faster", "30%"), a "doubles
# throughput" style multiplier, or a superlative about the project ("fastest",
# "first-ever", "the only tool", "world's first", "first to"). A claim split over
# two lines of one paragraph counts too.
#
# A claim is backed when the same line (for a split claim, either of its two lines)
# carries at least one of:
#   an http/https URL  a primary source, bare or as a markdown link (a cited table row)
#   [ledger:H2]        a row of the Hypotheses table in EXPERIMENTS.md whose verdict is exactly SUPPORTED
#   [provenance:P1]    a row of docs/provenance.md with a non-empty Source column
# A URL inside inline code or an HTML comment does not count.
#
# Every [ledger:..] and [provenance:..] reference must resolve, claim or not.
# Fenced code blocks (CommonMark rules: a fence closes only on the same character
# at least as long), inline code, HTML comments and URLs are not scanned.
# docs/provenance.md is the source of truth for cited numbers and is not scanned
# either. A file the guard cannot read is an error.
#
# Usage: tests/claims_check.sh [repo-root]

set -uo pipefail

ROOT=${1:-"$(cd "$(dirname "$0")/.." && pwd)"}
LEDGER="${ROOT}/EXPERIMENTS.md"
PROVENANCE="${ROOT}/docs/provenance.md"

# read_error <file>: report a file the guard cannot read and stop.
read_error() {
    echo "claims_check: cannot read ${1}"
    exit 2
}

if [ ! -f "${LEDGER}" ]; then
    echo "claims_check: EXPERIMENTS.md missing at ${LEDGER}"
    exit 1
fi

# "H1=PENDING;H2=SUPPORTED;" from the rows of the "## Hypotheses" section. A
# verdict cell that is not exactly one of the four verdicts (bold allowed) reads
# as INVALID. GFM escaped pipes (\|) inside a cell are dropped before splitting.
[ -r "${LEDGER}" ] || read_error "${LEDGER}"
ledger=$(awk -F'|' '
    { gsub(/\\\|/, "") }
    /^## / { inh = ($0 ~ /^## Hypotheses/) }
    inh && /^\| *H[0-9]+ *\|/ {
        id = $2; gsub(/ /, "", id)
        v = ($NF ~ /^[ \t]*$/) ? $(NF - 1) : $NF
        gsub(/\*\*/, "", v); gsub(/^[ \t]+|[ \t]+$/, "", v); v = toupper(v)
        if (v !~ /^(PENDING|SUPPORTED|REFUTED|INCONCLUSIVE)$/) v = "INVALID"
        printf "%s=%s;", id, v
    }' "${LEDGER}") || read_error "${LEDGER}"

# One row per id: a merge that keeps both sides of an edited row leaves two,
# and the lookup below would silently read only one of them.
dups=$(tr ';' '\n' <<< "${ledger}" | cut -d= -f1 | grep . | sort | uniq -d | tr '\n' ' ')
if [ -n "${dups}" ]; then
    for id in ${dups}; do echo "EXPERIMENTS.md: duplicate hypothesis id ${id}"; done
    echo "claims_check: keep one row per hypothesis id in EXPERIMENTS.md"
    exit 1
fi

# "P1=OK;P2=NOSOURCE;" from the rows of docs/provenance.md (columns: # | Claim | Source | Retrieved).
provenance=""
if [ -f "${PROVENANCE}" ]; then
    [ -r "${PROVENANCE}" ] || read_error "${PROVENANCE}"
    provenance=$(awk -F'|' '
        { gsub(/\\\|/, "") }
        /^\| *P[0-9]+ *\|/ {
            id = $2; gsub(/ /, "", id)
            src = $4; gsub(/[ \t]/, "", src)
            printf "%s=%s;", id, (src == "" ? "NOSOURCE" : "OK")
        }' "${PROVENANCE}") || read_error "${PROVENANCE}"
fi

files=()
[ -f "${ROOT}/README.md" ] && files+=("${ROOT}/README.md")
for f in "${ROOT}"/docs/*.md "${ROOT}"/docs/intel/*.md; do
    [ -f "${f}" ] && [ "${f}" != "${PROVENANCE}" ] && files+=("${f}")
done

report=""
for f in "${files[@]}"; do
    rel=${f#"${ROOT}/"}
    [ -r "${f}" ] || read_error "${f}"
    out=$(awk -v rel="${rel}" -v ledger="${ledger}" -v prov="${provenance}" '
        function load(s, m,    n, i, kv, p) {
            n = split(s, kv, ";")
            for (i = 1; i <= n; i++) if ((p = index(kv[i], "=")) > 0) m[substr(kv[i], 1, p - 1)] = substr(kv[i], p + 1)
        }
        function is_claim(t,    i) {
            for (i = 1; i <= NRE; i++) if (t ~ claim_re[i]) return 1
            return 0
        }
        # run(t, c): length of the run of character c at the start of t.
        function run(t, c,    n) {
            n = 0
            while (substr(t, n + 1, 1) == c) n++
            return n
        }
        BEGIN {
            load(ledger, L); load(prov, P)
            num = "[0-9][0-9.,]*"
            NRE = 10
            claim_re[1] = num " *(tok/s(ec)?|t/s(ec)?|tokens?/s(ec)?|tokens? per second|tps)([^a-z]|$)"
            claim_re[2] = num " *(j/tok|j per token|joules? per token)"
            claim_re[3] = num " *(x|×|times) *(faster|slower|quicker|speed-?ups?|throughput|more|less|higher|lower|better)"
            claim_re[4] = num " *%"
            claim_re[5] = "fastest|quickest"
            claim_re[6] = "first[- ]ever"
            claim_re[7] = "(world|industry)(\047|\342\200\231)?s (first|only|fastest)"
            claim_re[8] = "(^|[^a-z])(first|only) +(tool|launcher|project|solution|way|cluster|distribution|distro|platform|k3d|k3s|kubernetes|open[- ]source)([^a-z]|$)"
            claim_re[9] = "(^|[^a-z])first to [a-z]"
            claim_re[10] = "(^|[^a-z])(doubl|tripl|quadrupl|halv)(es|ed|ing) +(the +)?(throughput|speed|performance|tok|tokens|latency|energy)"
        }
        {
            t = $0; sub(/^ */, "", t)
            if (fence) {
                # A closing fence: the opening character, at least as many, then only blanks.
                n = run(t, fch)
                if (n >= flen && substr(t, n + 1) ~ /^[ \t]*$/) fence = 0
                next
            }
            c = substr(t, 1, 1)
            if (c == "`" || c == "~") {
                n = run(t, c)
                # A backtick fence info string may not hold a backtick; otherwise it is inline code.
                if (n >= 3 && !(c == "`" && index(substr(t, n + 1), "`") > 0)) {
                    fence = 1; fch = c; flen = n; prev = ""
                    next
                }
            }

            s = $0
            if (incomment) {
                if ((p = index(s, "-->")) == 0) next
                s = substr(s, p + 3); incomment = 0
            }
            while ((p = index(s, "<!--")) > 0) {
                q = index(substr(s, p + 4), "-->")
                if (q == 0) { s = substr(s, 1, p - 1); incomment = 1; break }
                s = substr(s, 1, p - 1) substr(s, p + q + 6)
            }
            gsub(/`[^`]*`/, "", s)
            # A primary-source link (http/https URL, bare or in a markdown link) backs the line.
            linked = (s ~ /https?:\/\/[^ )>]/)
            gsub(/\]\([^)]*\)/, "]", s)
            gsub(/https?:\/\/[^ )>]+/, "", s)
            low = tolower(s)

            claim = is_claim(low)

            backed = linked; notes = ""; rest = s
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

            # A claim split over this line and the previous line of the same paragraph.
            split_claim = 0
            if (!claim && prev != "" && !prevclaim && is_claim(prev " " low)) {
                split_claim = 1
                if (prevbacked) backed = 1
            }
            if ((claim || split_claim) && !backed)
                printf "%s:%d: unbacked claim (no ledger or provenance reference that backs it%s): %s\n", rel, NR, notes, $0

            if (low ~ /^[ \t]*$/) prev = ""
            else { prev = low; prevclaim = claim || split_claim; prevbacked = backed }
        }' "${f}") || read_error "${f}"
    report+=${out}$'\n'
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
echo "Back each claim with a primary-source URL on the line, a SUPPORTED [ledger:Hn] row in EXPERIMENTS.md or a sourced [provenance:Pn] row in docs/provenance.md."
exit 1
