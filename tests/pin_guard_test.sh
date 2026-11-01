#!/usr/bin/env bash
# hack/pin-guard against throwaway git repos. Each case branches off one base commit
# (a small ledger, a pins/classes file and a few runners), makes one change, commits
# it and runs the guard with --base main. Commit dates are fixed (the base is
# 2026-10-29), so the amendment date rule (no earlier than the base's date minus one
# day) is deterministic.
#
# Usage: tests/pin_guard_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GUARD=${GUARD:-${ROOT}/hack/pin-guard}
T=$(mktemp -d "${TMPDIR:-/tmp}/pin_guard_test.XXXXXX")
trap 'rm -rf "$T"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_AUTHOR_DATE=2026-10-29T12:00:00-07:00 GIT_COMMITTER_DATE=2026-10-29T12:00:00-07:00
unset GITHUB_ACTIONS GITHUB_HEAD_REF GITHUB_REF_NAME

D1=sha256:1111111111111111111111111111111111111111111111111111111111111111
D2=sha256:2222222222222222222222222222222222222222222222222222222222222222
OLD_REF="foo/old:1.0@${D1}"

R="$T/repo"
git init -q -b main "$R"
mkdir -p "$R/bench/h54/results" "$R/bench/wildct" "$R/bench/old" "$R/bench/refuted" "$R/bench/reuse" "$R/pins" "$R/scripts"
cat > "$R/EXPERIMENTS.md" <<'EOF'
# Experiments

## Hypotheses

| # | Hypothesis | Prediction | Verdict |
|---|------------|------------|---------|
| H54a | a | p | INCONCLUSIVE |
| H54q | q | p | PENDING |
| H18w | w | p | PENDING |
| H52b | b | p | REFUTED |
| H60 | f | p | PENDING |

## Runs

### H54a run (Xeon 6741P, 2026-10-21)

done
EOF
cat > "$R/pins/classes" <<EOF
bench/old/**            historical  a finished run
bench/h54/**            ledger H54a,H54q amend=H54  the H54 runner
bench/wildct/**         ledger H18w amend=Wildct  the wildct runner
bench/refuted/**        ledger H52b amend=Crescent  a finished row
bench/reuse/**          ledger H52b,H99 amend=Crescent  a finished row's runner a new row reuses
bench/tool.py           ours
historical-ref bench/h54/arm.sh ${OLD_REF}
EOF
printf '#!/bin/sh\nIMG=foo/bar:1.0@%s\nOLD=%s\necho run\n' "$D1" "$OLD_REF" > "$R/bench/h54/arm.sh"
printf 'image: foo/res:1.0@%s\n' "$D1" > "$R/bench/h54/results/r.txt"
printf '#!/bin/sh\nRAPL_IMAGE=python:3.13-slim@%s\n' "$D1" > "$R/bench/wildct/hold.sh"
printf '#!/bin/sh\nIMG=foo/old:1.0@%s\n' "$D1" > "$R/bench/old/run.sh"
printf 'echo\n' > "$R/bench/old/two words.sh"
printf '#!/bin/sh\nIMG=foo/ref:1.0@%s\necho a\n' "$D1" > "$R/bench/refuted/run.sh"
printf '#!/bin/sh\nIMG=foo/reuse:1.0@%s\n' "$D1" > "$R/bench/reuse/run.sh"
printf 'IMG = "foo/tool:1.0@%s"\n' "$D1" > "$R/bench/tool.py"
printf '#!/bin/sh\nIMG=foo/k:1.0@%s\n' "$D1" > "$R/scripts/k"
mkdir -p "$R/.github/workflows"; echo "name: CI" > "$R/.github/workflows/ci.yml"
git -C "$R" add -A && git -C "$R" commit -qm base

# case NAME WANT_RC [GUARD ARGS...]: run the guard on branch NAME (made by the caller).
guard() { (cd "$R" && "$GUARD" --base main "$@") > "$T/out" 2>&1; echo $?; }
branch() { git -C "$R" switch -q -C "$1" main; }
commit() { git -C "$R" add -A && git -C "$R" commit -qm "$1"; }
amend() {  # append a heading line to EXPERIMENTS.md
    printf '\n%s\n\nbody\n' "$1" >> "$R/EXPERIMENTS.md"
}
bump() { sed -i "s#$1#$2#" "$R/$3"; }
expect() {  # expect DESC WANT_RC [GREP] [GUARD ARGS...]
    local desc=$1 want=$2 pat=${3:-} rc
    shift 3
    rc=$(guard "$@")
    if [ "$rc" = "$want" ] && { [ -z "$pat" ] || grep -qE -- "$pat" "$T/out"; }; then
        ok "$desc"
    else
        fail "$desc (rc ${rc}, want ${want}${pat:+, /${pat}/})"; sed 's/^/    /' "$T/out"
    fi
}

branch hist-edit; bump "foo/old:1.0" "foo/old:2.0" bench/old/run.sh; commit x
expect "a historical edit fails" 1 "bench/old/run.sh: historical record modified"

branch hist-del; git -C "$R" rm -q bench/old/run.sh; commit x
expect "a historical deletion fails" 1 "historical record deleted"

branch hist-mv; git -C "$R" mv bench/old/run.sh bench/old/run2.sh; commit x
expect "a historical rename fails" 1 "historical record renamed"

branch hist-space; echo "echo again" >> "$R/bench/old/two words.sh"; commit x
expect "a historical file whose name has a space is still guarded" 1 "bench/old/two words.sh: historical record modified"

branch hist-ref; bump "OLD=foo/old:1.0" "OLD=foo/old:2.0" bench/h54/arm.sh
amend "### H54 amendment Q9: the old arm (2026-10-29)"; commit x
expect "a historical-ref change in a mixed file fails (even with an amendment)" 1 "historical-ref 'foo/old:1.0@"

branch ref-new; printf 'NEW=%s\n' "$OLD_REF" >> "$R/bench/tool.py"; commit x
expect "a historical-ref string in another file is not counted" 0 ""

branch arm-none; bump "foo/bar:1.0@${D1}" "foo/bar:1.1@${D2}" bench/h54/arm.sh; commit x
expect "an arm pin change without an amendment fails" 1 "bench/h54/arm.sh: arm pin change .* for PENDING H54q with no amendment"

branch arm-row; bump "foo/bar:1.0@${D1}" "foo/bar:1.1@${D2}" bench/h54/arm.sh
amend "### H54 amendment Q9: foo/bar 1.1 for every arm (2026-10-29)"; commit x
expect "an arm pin change with 'H54 amendment Q9: ... (today)' passes" 0 "^ok"

branch arm-row-id; bump "foo/bar:1.0@${D1}" "foo/bar:1.1@${D2}" bench/h54/arm.sh
amend "### H54q amendment Q9: foo/bar 1.1 (2026-10-28)"; commit x
expect "an amendment naming the PENDING row, dated the day before the base, passes" 0 "^ok"

branch arm-form; bump "foo/bar:1.0@${D1}" "foo/bar:1.1@${D2}" bench/h54/arm.sh
amend "### H54 Amendment Q9 (foo/bar 1.1, 2026-10-29)"; commit x
expect "a heading not in the '<names> amendment <ID>: <title> (date)' form is no amendment" 1 "no amendment"

branch arm-host; bump "foo/bar:1.0@${D1}" "foo/bar:1.1@${D2}" bench/h54/arm.sh
amend "### H54 Amendment Q9: foo/bar 1.1 (Xeon 6741P, 2026-10-29)"; commit x
expect "an amendment whose date follows a host in its parentheses, 'Amendment' capitalised, passes" 0 "^ok"

branch arm-alias; bump "python:3.13-slim@${D1}" "python:3.14-slim@${D2}" bench/wildct/hold.sh
amend "### Wildct amendment P2: python 3.14 for the RAPL reader (2026-10-29)"; commit x
expect "an alias heading (Wildct amendment P2) passes" 0 "^ok"

branch arm-other; bump "foo/bar:1.0@${D1}" "foo/bar:1.1@${D2}" bench/h54/arm.sh
amend "### H60 amendment V9: another row (2026-10-29)"; commit x
expect "an amendment naming a different row fails" 1 "arm pin change .* no amendment"

branch arm-stale; bump "foo/bar:1.0@${D1}" "foo/bar:1.1@${D2}" bench/h54/arm.sh
amend "### H54 amendment Q9: foo/bar 1.1 (2026-10-27)"; commit x
expect "a stale-dated amendment fails" 1 "is dated before 2026-10-28"

branch arm-refuted; bump "foo/ref:1.0@${D1}" "foo/ref:1.1@${D2}" bench/refuted/run.sh
amend "### Crescent amendment E9: a newer ref (2026-10-29)"; commit x
expect "an arm change for a row now REFUTED (now historical) fails" 1 "bench/refuted/run.sh: historical record modified"

branch arm-nonpin; bump "echo run" "echo run twice" bench/h54/arm.sh; commit x
expect "a non-pin runner edit in an arm path passes" 0 "^ok"

branch arm-results; bump "foo/res:1.0@${D1}" "foo/res:1.1@${D2}" bench/h54/results/r.txt; commit x
expect "a pin change under results/ passes" 0 "^ok"

branch arm-new; printf 'IMG=foo/new:1.0@%s\n' "$D1" > "$R/bench/h54/new.sh"; commit x
expect "a new file in an arm path passes (it moves no pin a row has)" 0 "^ok"

branch started; sed -i 's/^### H54a run/### H54q run/' "$R/EXPERIMENTS.md"; commit started
git -C "$R" switch -q -c started-pr
bump "foo/bar:1.0@${D1}" "foo/bar:1.1@${D2}" bench/h54/arm.sh
amend "### H54 amendment Q9: foo/bar 1.1 (2026-10-29)"; commit x
rc=$(cd "$R" && "$GUARD" --base started > "$T/out" 2>&1; echo $?)
if [ "$rc" = 0 ] && grep -q '^::warning::bench/h54/arm.sh: H54q has a run heading' "$T/out"; then
    ok "an amendment to a row that has started warns but passes"
else
    fail "an amendment to a row that has started warns but passes (rc ${rc})"; sed 's/^/    /' "$T/out"
fi

branch ours; bump "foo/tool:1.0" "foo/tool:2.0" bench/tool.py; bump "foo/k:1.0" "foo/k:2.0" scripts/k; commit x
expect "an ours edit passes" 0 "^ok"

branch landing; bump "| H52b | b | p | REFUTED |" "| H52b | b | p | INCONCLUSIVE |" EXPERIMENTS.md
bump "echo a" "echo b" bench/refuted/run.sh; commit x
expect "a verdict landing that edits its finished row's runner (no pin) passes" 0 "^ok"

branch prereg; printf '| H99 | n | p | PENDING |\n' > "$T/row"
sed -i "/^| H60 /r $T/row" "$R/EXPERIMENTS.md"
amend "### H99 pre-registration: a new row on the old runner (2026-10-29)"
bump "foo/reuse:1.0" "foo/reuse:2.0" bench/reuse/run.sh; commit x
expect "a pre-registration that reuses a finished row's runner sets its pins" 0 "^ok"

# A new row reusing a finished line's runner adds itself to that line in the same diff.
newrow() { printf '| %s | n | p | PENDING |\n' "$1" > "$T/row"; sed -i "/^| H60 /r $T/row" "$R/EXPERIMENTS.md"; }
branch prereg-line; newrow H98; amend "### H98 pre-registration: H52b's runner again (2026-10-29)"
sed -i 's|^bench/refuted/\*\* .*|bench/refuted/** ledger H52b,H98 amend=Crescent  a finished row, reused by H98|' "$R/pins/classes"
bump "foo/ref:1.0" "foo/ref:1.1" bench/refuted/run.sh; commit x
expect "a pre-registration that adds its row to a finished line in the same diff sets its pins" 0 "^ok"

branch prereg-noline; newrow H98; amend "### H98 pre-registration: H52b's runner again (2026-10-29)"
bump "foo/ref:1.0" "foo/ref:1.1" bench/refuted/run.sh; commit x
expect "a pre-registration whose row the line does not list leaves the record historical" 1 "bench/refuted/run.sh: historical record modified"

branch prereg-nohead; newrow H98
sed -i 's|^bench/refuted/\*\* .*|bench/refuted/** ledger H52b,H98 amend=Crescent  a finished row, reused by H98|' "$R/pins/classes"
bump "foo/ref:1.0" "foo/ref:1.1" bench/refuted/run.sh; commit x
expect "a new row on the line with no pre-registration heading leaves the record historical" 1 "bench/refuted/run.sh: historical record modified"

# A PENDING row's amendment that moves its arm onto a finished line's runner joins the line.
joined() {  # joined BRANCH [AMENDMENT HEADING]
    branch "$1"
    sed -i 's|^bench/refuted/\*\* .*|bench/refuted/** ledger H52b,H60 amend=Crescent  a finished row, reused by H60|' "$R/pins/classes"
    [ -z "${2:-}" ] || amend "$2"
    bump "foo/ref:1.0" "foo/ref:1.1" bench/refuted/run.sh; commit x
}
joined joined-amend "### H60 amendment V9: H52b's runner for the second arm (2026-10-29)"
expect "a PENDING row's amendment that adds it to a finished line in the same diff sets its pins" 0 "^ok"
joined joined-none
expect "a PENDING row added to a finished line with no amendment leaves the record historical" 1 "bench/refuted/run.sh: historical record modified"
joined joined-stale "### H60 amendment V9: H52b's runner for the second arm (2026-10-20)"
expect "a stale amendment that adds a PENDING row to a finished line fails" 1 "is dated before 2026-10-28"

branch reclass; sed -i 's|^bench/old/\*\* .*|bench/old/** ours now|' "$R/pins/classes"
bump "foo/old:1.0" "foo/old:2.0" bench/old/run.sh; commit x
expect "reclassifying a historical path in the same diff does not free it" 1 "bench/old/run.sh: historical record modified"

branch loose; printf '#!/bin/sh\nIMG=ghcr.io/foo/loose:1.0@%s\n' "$D1" > "$R/bench/loose.sh"; commit x
expect "an unclassified bench pin fails" 1 "bench/loose.sh: holds pin tokens .* classify it in pins/classes"

branch loose-nopin; printf '#!/bin/sh\necho hi\n' > "$R/bench/loose.sh"; commit x
expect "an unclassified bench file without pins passes" 0 "^ok"

branch bot-gh; echo "on: push" >> "$R/.github/workflows/ci.yml"; commit x
expect "--autobump touching .github fails" 1 "\.github/workflows/ci.yml: an autobump branch never touches .github/" --autobump --branch autobump/k3s

branch bot-exp; amend "### H54 amendment Q9: x (2026-10-29)"; commit x
expect "--autobump touching EXPERIMENTS.md fails" 1 "EXPERIMENTS.md: only an autobump/amend-\* branch" --autobump --branch autobump/k3s
expect "--autobump touching EXPERIMENTS.md passes on autobump/amend-*" 0 "^ok" --autobump --branch autobump/amend-H54q

branch bot-amend-gh; echo "on: push" >> "$R/.github/workflows/ci.yml"; amend "### H54 amendment Q9: x (2026-10-29)"; commit x
expect "--autobump touching .github fails on autobump/amend-* too" 1 "never touches .github/" --autobump --branch autobump/amend-H54q

branch bot-classes; echo "bench/x/** ours" >> "$R/pins/classes"; commit x
expect "--autobump touching pins/classes fails" 1 "never reclassifies pins" --autobump --branch autobump/k3s
expect "a human branch may touch .github and pins/classes" 0 "^ok"

# The ledger's real headings parse to the names the guard matches rows and aliases by.
if out=$(python3 - "${ROOT}/EXPERIMENTS.md" "${GUARD}" 2>&1 <<'PY'
import importlib.machinery, importlib.util, sys
text = open(sys.argv[1]).read().splitlines()
loader = importlib.machinery.SourceFileLoader("pin_guard", sys.argv[2])
g = importlib.util.module_from_spec(importlib.util.spec_from_loader("pin_guard", loader))
loader.exec_module(g)
want = {
    "### Crescent amendment E2:": {"crescent"},
    "### H54 amendment Q1:": {"h54"},
    "### Wildct amendment P1:": {"wildct"},
    "### H57dp amendment R2:": {"h57dp"},
    "### H60 amendment V1:": {"h60"},
    "### H47 and H49 amendment P1:": {"h47", "h49"},
    "### H30-H32 amendment A1:": {"h30", "h31", "h32"},
    "### H54b-H54c amendment G1:": {"h54b", "h54c"},
    "### Cross-host amendment K1:": {"cross-host"},
}
for prefix, names in want.items():
    line = next(l for l in text if l.startswith(prefix))
    m = g.AMEND_RE.match(line)
    assert m and g.DATE_RE.search(line), line
    assert names <= g.names_of(m.group("names")), (line, g.names_of(m.group("names")))
run = next(l for l in text if l.startswith("### H40-H48 run"))
assert "h43" in g.names_of(g.RUN_RE.match(run).group("names")), run
PY
); then ok "the ledger's real amendment and run headings parse to their rows and aliases"
else fail "real headings"; sed 's/^/    /' <<< "${out}"; fi

branch usage; rc=$(cd "$R" && "$GUARD" --base no-such-ref > "$T/out" 2>&1; echo $?)
if [ "$rc" = 2 ]; then ok "an unknown base is a usage error (exit 2)"; else fail "an unknown base exits ${rc}, want 2"; fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
