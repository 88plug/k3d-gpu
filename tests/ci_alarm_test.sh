#!/usr/bin/env bash
# hack/ci_alarm against tests/ops/fake_gh.py on PATH (an issue store in a temp
# directory, no network). Checks: open files one labelled, assigned issue with an
# @mention per key; the same body within 24 h adds nothing; a changed body
# comments and replaces the body; the same body after 24 h comments once more;
# close comments and closes; list prints the open keys; a repository with issues
# disabled gets a workflow error annotation and exit 3 instead.
#
# Usage: tests/ci_alarm_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ALARM=${ALARM:-${ROOT}/hack/ci_alarm}
T=$(mktemp -d "${TMPDIR:-/tmp}/ci_alarm_test.XXXXXX")
trap 'rm -rf "$T"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

mkdir -p "$T/bin"
printf '#!/bin/sh\nexec python3 %q "$@"\n' "${ROOT}/tests/ops/fake_gh.py" > "$T/bin/gh"
chmod +x "$T/bin/gh"
export PATH="$T/bin:$PATH" GH_REPO=88plug/k3d-gpu
unset GITHUB_STEP_SUMMARY ALARM_OWNER

# fresh NAME: a new empty issue store; the clock at 2026-10-29T13:00:00Z.
fresh() {
    export FAKE_GH="$T/$1"
    mkdir -p "$FAKE_GH"
    clock 2026-10-29T13:00:00Z
}
clock() { export FAKE_GH_NOW=$1; CI_ALARM_NOW=$(date -u -d "$1" +%s); export CI_ALARM_NOW; }
body() { printf '%s\n' "$@" > "$T/body"; echo "$T/body"; }
alarm() { bash "$ALARM" "$@" > "$FAKE_GH/out" 2> "$FAKE_GH/err"; }
issues() { jq "$@" "$FAKE_GH/issues.json"; }
calls() { grep -c "^issue $1" "$FAKE_GH/calls.log" 2>/dev/null || true; }

# 1. open: one issue, titled, labelled ci-alarm, assigned to 88plug, @mention in the body.
fresh open
alarm open red:build-and-push.yml "nightly is red" "$(body "run 39412124435 failed")"; rc=$?
if [ "$rc" -eq 0 ] \
   && [ "$(issues length)" = 1 ] \
   && [ "$(issues -r '.[0].title')" = "[ci-alarm] red:build-and-push.yml: nightly is red" ] \
   && [ "$(issues -r '.[0].labels[0].name')" = ci-alarm ] \
   && [ "$(issues -r '.[0].assignees[0].login')" = 88plug ] \
   && issues -r '.[0].body' | grep -q '@88plug' \
   && issues -r '.[0].body' | grep -q 'run 39412124435 failed' \
   && jq -e 'index("ci-alarm")' "$FAKE_GH/labels.json" >/dev/null; then
    ok "open files one labelled, assigned issue with an @mention"
else
    fail "open: rc=$rc issues=$(cat "$FAKE_GH/issues.json" 2>/dev/null) err=$(cat "$FAKE_GH/err")"
fi

# 2. the same body within 24 h: no comment, no edit, no second issue.
clock 2026-10-29T20:00:00Z
before=$(calls comment)
alarm open red:build-and-push.yml "nightly is red" "$(body "run 39412124435 failed")"; rc=$?
if [ "$rc" -eq 0 ] && [ "$(issues length)" = 1 ] && [ "$(issues '.[0].comments | length')" = 0 ] \
   && [ "$(calls comment)" = "$before" ] && [ "$(calls edit)" = 0 ]; then
    ok "the same body within 24 h adds nothing"
else
    fail "same body within 24 h: rc=$rc comments=$(issues '.[0].comments | length') edits=$(calls edit)"
fi

# 3. a changed body: one comment carrying it, and the issue body replaced.
alarm open red:build-and-push.yml "nightly is red" "$(body "run 39591066580 failed")"; rc=$?
if [ "$rc" -eq 0 ] && [ "$(issues length)" = 1 ] && [ "$(issues '.[0].comments | length')" = 1 ] \
   && issues -r '.[0].comments[0].body' | grep -q 'run 39591066580 failed' \
   && issues -r '.[0].body' | grep -q 'run 39591066580 failed' \
   && ! issues -r '.[0].body' | grep -q 'run 39412124435'; then
    ok "a changed body comments and replaces the issue body"
else
    fail "changed body: rc=$rc issues=$(cat "$FAKE_GH/issues.json")"
fi

# 4. the same body 24 h after the last comment: one reminder comment, then quiet again.
clock 2026-10-30T20:00:01Z
alarm open red:build-and-push.yml "nightly is red" "$(body "run 39591066580 failed")"; rc1=$?
alarm open red:build-and-push.yml "nightly is red" "$(body "run 39591066580 failed")"; rc2=$?
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$(issues '.[0].comments | length')" = 2 ] \
   && issues -r '.[0].comments[1].body' | grep -q '@88plug'; then
    ok "the same body after 24 h comments once, with an @mention"
else
    fail "24 h reminder: rc=$rc1/$rc2 comments=$(issues '.[0].comments | length')"
fi

# 5. a second key is its own issue; a key that extends another is not confused with it.
alarm open red:build-and-push.yml.bak "other" "$(body x)"; rc1=$?
alarm open release:0.1.76 "no image" "$(body y)"; rc2=$?
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$(issues length)" = 3 ]; then
    ok "each key gets its own issue"
else
    fail "keys: rc=$rc1/$rc2 issues=$(issues length)"
fi

# 6. list prints the open keys, one per line.
alarm list; rc=$?
if [ "$rc" -eq 0 ] && [ "$(sort "$FAKE_GH/out" | tr '\n' ' ')" = "red:build-and-push.yml red:build-and-push.yml.bak release:0.1.76 " ]; then
    ok "list prints the open keys"
else
    fail "list: rc=$rc out=$(tr '\n' ' ' < "$FAKE_GH/out")"
fi

# 7. close comments and closes; list no longer prints the key; closing again is a no-op.
alarm close red:build-and-push.yml "the nightly is green again"; rc1=$?
n=$(issues -r '.[] | select(.title | startswith("[ci-alarm] red:build-and-push.yml:")) | .number')
# shellcheck disable=SC2016 # $n is a jq variable
st=$(issues -r --argjson n "$n" '.[] | select(.number == $n) | .state')
# shellcheck disable=SC2016
last=$(issues -r --argjson n "$n" '.[] | select(.number == $n) | .comments[-1].body')
alarm close red:build-and-push.yml "again"; rc2=$?
alarm list
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$st" = CLOSED ] && [ "$last" = "the nightly is green again" ] \
   && ! grep -qx 'red:build-and-push.yml' "$FAKE_GH/out" && grep -qx 'release:0.1.76' "$FAKE_GH/out" \
   && [ "$(issues '[.[] | select(.state == "OPEN")] | length')" = 2 ]; then
    ok "close comments and closes; a second close is a no-op"
else
    fail "close: rc=$rc1/$rc2 state=$st last=$last"
fi

# 8. reopening a closed key files a new issue.
alarm open red:build-and-push.yml "red again" "$(body z)"; rc=$?
if [ "$rc" -eq 0 ] && [ "$(issues '[.[] | select(.title | startswith("[ci-alarm] red:build-and-push.yml:"))] | length')" = 2 ]; then
    ok "a key opened after close files a new issue"
else
    fail "reopen: rc=$rc"
fi

# 9. ALARM_OWNER moves the assignee and the mention.
fresh owner
ALARM_OWNER=someone-else alarm open simulate "simulated red" "$(body s)"; rc=$?
if [ "$rc" -eq 0 ] && [ "$(issues -r '.[0].assignees[0].login')" = someone-else ] \
   && issues -r '.[0].body' | grep -q '@someone-else'; then
    ok "ALARM_OWNER sets the assignee and the mention"
else
    fail "ALARM_OWNER: rc=$rc issues=$(cat "$FAKE_GH/issues.json")"
fi

# 10. a key with a space or an empty title is refused (exit 2), nothing filed.
fresh bad
alarm open "bad key" "t" "$(body b)"; rc1=$?
alarm open good "" "$(body b)"; rc2=$?
if [ "$rc1" -eq 2 ] && [ "$rc2" -eq 2 ] && [ ! -s "$FAKE_GH/issues.json" ]; then
    ok "a bad key or an empty title is refused"
else
    fail "bad input: rc=$rc1/$rc2"
fi

# 11. issues disabled: open prints a ::error annotation and the summary, exits 3.
fresh disabled
printf '^(issue|label) \t-\t1\tthe %s repository has disabled issues\n' "'88plug/k3d-gpu'" > "$FAKE_GH/routes.tsv"
GITHUB_STEP_SUMMARY="$FAKE_GH/summary" alarm open simulate "simulated red" "$(body "the simulated body")"; rc=$?
if [ "$rc" -eq 3 ] && grep -q '^::error title=ci-alarm simulate::' "$FAKE_GH/out" \
   && grep -q 'the simulated body' "$FAKE_GH/summary" && grep -qi 'disabled issues' "$FAKE_GH/err"; then
    ok "issues disabled: ::error annotation, step summary, exit 3"
else
    fail "issues disabled: rc=$rc out=$(cat "$FAKE_GH/out") err=$(cat "$FAKE_GH/err")"
fi
alarm list; rc=$?
if [ "$rc" -eq 3 ]; then ok "issues disabled: list exits 3"; else fail "issues disabled: list rc=$rc"; fi

# 12. any other gh failure is exit 1, with gh's error on stderr.
fresh broken
printf '^issue list\t-\t1\tHTTP 502: Bad Gateway\n' > "$FAKE_GH/routes.tsv"
alarm open simulate "simulated red" "$(body b)"; rc=$?
if [ "$rc" -eq 1 ] && grep -q 'HTTP 502' "$FAKE_GH/err"; then
    ok "a gh error exits 1 with the error text"
else
    fail "gh error: rc=$rc err=$(cat "$FAKE_GH/err")"
fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
