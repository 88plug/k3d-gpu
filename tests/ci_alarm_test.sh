#!/usr/bin/env bash
# hack/ci_alarm against tests/ops/fake_gh.py on PATH (an issue store in a temp
# directory, no network). Checks: open files one labelled, assigned issue with an
# @mention per key; the same body within 24 h adds nothing; a changed body
# comments and replaces the body; the same body after 24 h comments once more;
# close comments and closes; list prints the open keys; a gh 5xx is retried. With
# has_issues=false the alarms live on branch ci-status (alarms.json, ALARMS.md),
# each notifies the owner by a commit comment on main's HEAD, status marks that
# commit red, and a concurrent write or a 502 on the branch is retried; issues that
# turn out disabled mid-run get a workflow error annotation and exit 3.
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
export PATH="$T/bin:$PATH" GH_REPO=88plug/k3d-gpu GH_RETRY_DELAY=0
unset GITHUB_STEP_SUMMARY ALARM_OWNER STATUS_BRANCH

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

# 11. the repository says it has issues, then the issue calls say they are disabled
#     (turned off mid-run): open prints a ::error annotation and the summary, exits 3.
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

# 12. any other gh failure is exit 1, with gh's error on stderr (after its retries).
fresh broken
printf '^issue list\t-\t1\tHTTP 502: Bad Gateway\n' > "$FAKE_GH/routes.tsv"
alarm open simulate "simulated red" "$(body b)"; rc=$?
if [ "$rc" -eq 1 ] && grep -q 'HTTP 502' "$FAKE_GH/err" && [ "$(grep -c '^issue list' "$FAKE_GH/calls.log")" = 4 ]; then
    ok "a gh error exits 1 with the error text, after 4 attempts"
else
    fail "gh error: rc=$rc err=$(cat "$FAKE_GH/err") calls=$(grep -c '^issue list' "$FAKE_GH/calls.log")"
fi

# 12b. one HTTP 502, then GitHub answers: the alarm is filed.
fresh flaky
printf '^issue list\t-\t1\tHTTP 502: Bad Gateway\t1\n' > "$FAKE_GH/routes.tsv"
alarm open simulate "simulated red" "$(body b)"; rc=$?
if [ "$rc" -eq 0 ] && [ "$(issues length)" = 1 ]; then
    ok "a 5xx is retried: the alarm is filed"
else
    fail "5xx retry: rc=$rc issues=$(cat "$FAKE_GH/issues.json" 2>/dev/null) err=$(cat "$FAKE_GH/err")"
fi

# --- has_issues=false: the alarms live on branch ci-status -------------------------
# git.json is the fake's object store; branch FILE prints FILE at ci-status's head.
branch() {
    python3 - "$FAKE_GH/git.json" "$1" <<'PY'
import json, sys
g = json.load(open(sys.argv[1]))
c = g["commits"][g["refs"]["heads/ci-status"]]
sys.stdout.write(g["trees"][c["tree"]].get(sys.argv[2], ""))
PY
}
head_of() { jq -r '.refs["heads/ci-status"] // "none"' "$FAKE_GH/git.json" 2>/dev/null || echo none; }
ncomments() { jq length "$FAKE_GH/commit_comments.json" 2>/dev/null || echo 0; }
nostore() { fresh "$1"; echo false > "$FAKE_GH/has_issues"; export FAKE_GH_HEAD=feedface00000000000000000000000000000000; }
URL=https://github.com/88plug/k3d-gpu/blob/ci-status/ALARMS.md

# 13. open: no issue; the alarm on ci-status (alarms.json and ALARMS.md); one commit
#     comment on main's HEAD that @mentions the owner, with the body and the page;
#     a ::error annotation and the step summary.
nostore store_open
GITHUB_STEP_SUMMARY="$FAKE_GH/summary" alarm open red:freshness.yml "scheduled run failure" "$(body "run 42280586432 failed")"; rc=$?
# shellcheck disable=SC2016 # the markdown backticks in the patterns are literal
if [ "$rc" -eq 0 ] && [ ! -s "$FAKE_GH/issues.json" ] \
   && [ "$(branch alarms.json | jq -r '."red:freshness.yml".title')" = "scheduled run failure" ] \
   && branch alarms.json | jq -r '."red:freshness.yml".body' | grep -q 'run 42280586432 failed' \
   && branch ALARMS.md | grep -qF '## `red:freshness.yml`: scheduled run failure' \
   && branch ALARMS.md | grep -q 'run 42280586432 failed' \
   && [ "$(ncomments)" = 1 ] \
   && [ "$(jq -r '.[0].commit_id' "$FAKE_GH/commit_comments.json")" = "$FAKE_GH_HEAD" ] \
   && jq -r '.[0].body' "$FAKE_GH/commit_comments.json" | grep -q '^@88plug ci-alarm `red:freshness.yml`' \
   && jq -r '.[0].body' "$FAKE_GH/commit_comments.json" | grep -q 'run 42280586432 failed' \
   && jq -r '.[0].body' "$FAKE_GH/commit_comments.json" | grep -qF "$URL" \
   && grep -q '^::error title=ci-alarm red:freshness.yml::scheduled run failure' "$FAKE_GH/out" \
   && grep -q 'run 42280586432 failed' "$FAKE_GH/summary"; then
    ok "issues disabled: the alarm is on ci-status, a commit comment @mentions the owner, annotation and summary"
else
    fail "store open: rc=$rc out=$(cat "$FAKE_GH/out") err=$(cat "$FAKE_GH/err") comments=$(ncomments) md=$(branch ALARMS.md 2>&1 | head -5)"
fi
# The root commit is the branch's only parentless commit: the branch shares no history with main.
if [ "$(jq -r --arg h "$(head_of)" '.commits[$h].parents | length' "$FAKE_GH/git.json")" = 0 ]; then
    ok "issues disabled: ci-status starts with a root commit"
else
    fail "store root: $(jq -c '.commits' "$FAKE_GH/git.json")"
fi

# 14. the same body within 24 h: no commit, no comment; a changed body: one commit, one comment.
clock 2026-10-29T20:00:00Z
before=$(head_of)
alarm open red:freshness.yml "scheduled run failure" "$(body "run 42280586432 failed")"; rc1=$?
same=$(head_of)
alarm open red:freshness.yml "scheduled run failure" "$(body "run 42290000000 failed")"; rc2=$?
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$same" = "$before" ] && [ "$(head_of)" != "$before" ] \
   && [ "$(ncomments)" = 2 ] && jq -r '.[1].body' "$FAKE_GH/commit_comments.json" | grep -q 'run 42290000000 failed' \
   && branch alarms.json | jq -r '."red:freshness.yml".body' | grep -q 'run 42290000000 failed' \
   && [ "$(branch alarms.json | jq -r '."red:freshness.yml".opened_at')" = 2026-10-29T13:00:00Z ]; then
    ok "issues disabled: the same body within 24 h adds nothing; a changed body commits and comments"
else
    fail "store same/changed: rc=$rc1/$rc2 heads=$before/$same/$(head_of) comments=$(ncomments)"
fi

# 15. the same body 24 h after the last comment: one reminder, then quiet again.
clock 2026-10-30T20:00:01Z
alarm open red:freshness.yml "scheduled run failure" "$(body "run 42290000000 failed")"; rc1=$?
alarm open red:freshness.yml "scheduled run failure" "$(body "run 42290000000 failed")"; rc2=$?
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$(ncomments)" = 3 ] \
   && jq -r '.[2].body' "$FAKE_GH/commit_comments.json" | grep -q '^@88plug still open: \[ci-alarm\] red:freshness.yml'; then
    ok "issues disabled: the same body after 24 h reminds once, with an @mention"
else
    fail "store reminder: rc=$rc1/$rc2 comments=$(ncomments)"
fi

# 16. list, list --json (the issue list shape), ack, and the commit status on main's HEAD.
alarm open bot-pr-stuck "bot PRs open over 72 h without green CI" "$(body "PR 296")"
alarm list; rc1=$?
keys=$(sort "$FAKE_GH/out" | tr '\n' ' ')
alarm ack bot-pr-stuck; rc2=$?
alarm list --json; rc3=$?
js=$FAKE_GH/out
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$rc3" -eq 0 ] && [ "$keys" = "bot-pr-stuck red:freshness.yml " ] \
   && [ "$(jq -r '[.[].title] | sort | join("|")' "$js")" = "[ci-alarm] bot-pr-stuck: bot PRs open over 72 h without green CI|[ci-alarm] red:freshness.yml: scheduled run failure" ] \
   && [ "$(jq -r '.[] | select(.title | contains("bot-pr-stuck")) | [.labels[].name] | join(",")' "$js")" = ci-alarm,alarm-ack ] \
   && [ "$(jq -r '.[] | select(.title | contains("red:freshness")) | [.labels[].name] | join(",")' "$js")" = ci-alarm ] \
   && [ "$(jq -r '[.[].number] | unique | join(",")' "$js")" = "" ] \
   && jq -r '.[] | select(.title | contains("red:freshness")) | .body' "$js" | grep -q 'run 42290000000 failed' \
   && [ "$(jq -r '.[0].url' "$js")" = "$URL" ]; then
    ok "issues disabled: list, list --json in the issue list shape, ack"
else
    fail "store list: rc=$rc1/$rc2/$rc3 keys=$keys json=$(cat "$js")"
fi
alarm status; rc=$?
st=$(jq -c '.[-1]' "$FAKE_GH/statuses.json" 2>/dev/null)
if [ "$rc" -eq 0 ] && [ "$(jq -r .sha <<< "$st")" = "$FAKE_GH_HEAD" ] && [ "$(jq -r .state <<< "$st")" = failure ] \
   && [ "$(jq -r .context <<< "$st")" = ci-alarm ] && [ "$(jq -r .target_url <<< "$st")" = "$URL" ] \
   && [ "$(jq -r .description <<< "$st")" = "2 open: bot-pr-stuck, red:freshness.yml" ]; then
    ok "issues disabled: status marks main's HEAD red, naming the open alarms"
else
    fail "store status: rc=$rc status=$st err=$(cat "$FAKE_GH/err")"
fi
# 16b. GitHub keeps at most 1000 statuses per SHA and context, and the Watchdog sets it
#      every run while main stays put: the same state and text again posts nothing.
n_before=$(jq length "$FAKE_GH/statuses.json")
alarm status; rc=$?
if [ "$rc" -eq 0 ] && [ "$(jq length "$FAKE_GH/statuses.json")" = "$n_before" ] && grep -q 'unchanged' "$FAKE_GH/out"; then
    ok "issues disabled: status unchanged on main's HEAD posts nothing"
else
    fail "store status again: rc=$rc posted=$(( $(jq length "$FAKE_GH/statuses.json") - n_before )) out=$(cat "$FAKE_GH/out") err=$(cat "$FAKE_GH/err")"
fi

# 17. close drops the key from the branch; a second close is a no-op; nothing open: status green.
alarm close red:freshness.yml "the scheduled run is green"; rc1=$?
alarm close red:freshness.yml "again"; rc2=$?
alarm close bot-pr-stuck "no PR is stuck"; rc3=$?
alarm status; rc4=$?
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$rc3" -eq 0 ] && [ "$rc4" -eq 0 ] \
   && [ "$(branch alarms.json | jq length)" = 0 ] && ! branch ALARMS.md | grep -q 'red:freshness' \
   && branch ALARMS.md | grep -q 'No open alarm' \
   && [ "$(jq -r '.[-1].state' "$FAKE_GH/statuses.json")" = success ] && [ "$(ncomments)" = 4 ]; then
    ok "issues disabled: close drops the alarm, a second close is a no-op, status turns green"
else
    fail "store close: rc=$rc1/$rc2/$rc3/$rc4 alarms=$(branch alarms.json) status=$(jq -c '.[-1]' "$FAKE_GH/statuses.json")"
fi

# 18. another writer moves the branch between the read and the write: re-read, re-apply,
#     and both writes stay (each update is a fast-forward).
nostore store_race
alarm open simulate "simulated red" "$(body s)"
echo 1 > "$FAKE_GH/ref_race"
alarm open watchdog-error "the watchdog could not complete its checks" "$(body e)"; rc=$?
if [ "$rc" -eq 0 ] && [ "$(branch alarms.json | jq -r 'keys | join(",")')" = simulate,watchdog-error ] \
   && jq -e '[.commits[] | select(.message == "another writer")] | length == 1' "$FAKE_GH/git.json" >/dev/null \
   && [ "$(cat "$FAKE_GH/ref_race")" = 0 ]; then
    ok "issues disabled: a concurrent write is re-read and re-applied, nothing lost"
else
    fail "store race: rc=$rc alarms=$(branch alarms.json) err=$(cat "$FAKE_GH/err")"
fi

# 19. an HTTP 502 on the ref update is retried; a write GitHub refuses is exit 1, an
#     ::error that the alarm was not filed, and the body in the step summary (the
#     owner's comment went out first, and the next open sends it again).
nostore store_502
printf '^api -X PATCH repos/88plug/k3d-gpu/git/refs/heads/ci-status\t-\t1\tgh: Server Error (HTTP 502)\t1\n' > "$FAKE_GH/routes.tsv"
alarm open simulate "simulated red" "$(body s)"
alarm open watchdog-error "the watchdog could not complete its checks" "$(body e)"; rc=$?
if [ "$rc" -eq 0 ] && [ "$(branch alarms.json | jq -r 'keys | join(",")')" = simulate,watchdog-error ]; then
    ok "issues disabled: a 502 on the branch update is retried"
else
    fail "store 502: rc=$rc alarms=$(branch alarms.json) err=$(cat "$FAKE_GH/err")"
fi
nostore store_refused
printf '^api -X POST repos/88plug/k3d-gpu/git/trees\t-\t1\tgh: Resource not accessible by integration (HTTP 403)\n' > "$FAKE_GH/routes.tsv"
GITHUB_STEP_SUMMARY="$FAKE_GH/summary" alarm open simulate "simulated red" "$(body "the simulated body")"; rc=$?
if [ "$rc" -eq 1 ] && grep -q '^::error title=ci-alarm simulate::simulated red (not filed' "$FAKE_GH/out" \
   && grep -q 'the simulated body' "$FAKE_GH/summary" && grep -q 'HTTP 403' "$FAKE_GH/err" && [ "$(ncomments)" = 1 ] \
   && [ "$(head_of)" = none ]; then
    ok "issues disabled, the branch write refused: exit 1, ::error not filed, the body in the summary"
else
    fail "store refused: rc=$rc out=$(cat "$FAKE_GH/out") err=$(cat "$FAKE_GH/err")"
fi

# 19b. the commit comment GitHub refuses: exit 1 and nothing written, so the next open
#      notifies (it does not read as already notified).
nostore store_comment_refused
printf '^api -X POST repos/88plug/k3d-gpu/commits/[0-9a-f]+/comments\t-\t1\tgh: Resource not accessible by integration (HTTP 403)\t1\n' \
    > "$FAKE_GH/routes.tsv"
alarm open simulate "simulated red" "$(body s)"; rc1=$?
written=$(head_of)
alarm open simulate "simulated red" "$(body s)"; rc2=$?
if [ "$rc1" -eq 1 ] && [ "$written" = none ] && [ "$rc2" -eq 0 ] && [ "$(ncomments)" = 1 ] \
   && [ "$(branch alarms.json | jq -r 'keys | join(",")')" = simulate ]; then
    ok "issues disabled, the comment refused: nothing written, the next open notifies"
else
    fail "store comment refused: rc=$rc1/$rc2 head=$written comments=$(ncomments) err=$(cat "$FAKE_GH/err")"
fi

# 20. with issues on: list --json is the issue list; status and ack leave main alone.
fresh issues_json
alarm open simulate "simulated red" "$(body s)"
alarm list --json; rc1=$?
cp "$FAKE_GH/out" "$FAKE_GH/list.json"
alarm status; rc2=$?
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$(jq -r '.[0].title' "$FAKE_GH/list.json")" = "[ci-alarm] simulate: simulated red" ] \
   && [ "$(jq -r '.[0].number' "$FAKE_GH/list.json")" = 1 ] && [ ! -f "$FAKE_GH/statuses.json" ]; then
    ok "issues on: list --json is the issue list; status posts nothing"
else
    fail "issues list --json: rc=$rc1/$rc2 out=$(cat "$FAKE_GH/list.json")"
fi

# 21. hack/status-branch publish (the Freshness dashboard): the file lands next to the
#     alarms, which stay; a concurrent writer is re-read; url names the page.
nostore publish
alarm open simulate "simulated red" "$(body s)"
printf '# Freshness dashboard\n\nall current\n' > "$T/dashboard.md"
echo 1 > "$FAKE_GH/ref_race"
"${ROOT}/hack/status-branch" publish freshness-dashboard.md "$T/dashboard.md" "Freshness dashboard, run 1" \
    > "$FAKE_GH/out" 2> "$FAKE_GH/err"; rc=$?
url=$("${ROOT}/hack/status-branch" url freshness-dashboard.md)
if [ "$rc" -eq 0 ] && [ "$(branch freshness-dashboard.md)" = "$(cat "$T/dashboard.md")" ] \
   && [ "$(branch alarms.json | jq -r 'keys | join(",")')" = simulate ] && [ "$(cat "$FAKE_GH/ref_race")" = 0 ] \
   && [ "$url" = https://github.com/88plug/k3d-gpu/blob/ci-status/freshness-dashboard.md ]; then
    ok "status-branch publish: the dashboard lands beside the alarms, a race is re-read"
else
    fail "publish: rc=$rc dashboard=$(branch freshness-dashboard.md) err=$(cat "$FAKE_GH/err") url=$url"
fi

# 22. hack/gh_retry.sh under set -e: two HTTP 502s then the answer (rc 0, the answer
#     printed); a 404 is not retried (rc 1, one call).
fresh gh_retry
printf '^api repos/88plug/k3d-gpu/pulls$\t-\t1\tgh: Server Error (HTTP 502)\t2\n^api repos/88plug/k3d-gpu/pulls$\tpulls.json\t0\n^api repos/88plug/k3d-gpu/nope$\t-\t1\tgh: Not Found (HTTP 404)\n' \
    > "$FAKE_GH/routes.tsv"
echo '[{"number": 1}]' > "$FAKE_GH/pulls.json"
out=$(bash -ec '. "$1"; gh_retry api repos/88plug/k3d-gpu/pulls' _ "${ROOT}/hack/gh_retry.sh" 2>/dev/null); rc1=$?
bash -ec '. "$1"; gh_retry api repos/88plug/k3d-gpu/nope' _ "${ROOT}/hack/gh_retry.sh" 2>/dev/null; rc2=$?
if [ "$rc1" -eq 0 ] && [ "$out" = '[{"number": 1}]' ] && [ "$(grep -c '^api repos/88plug/k3d-gpu/pulls$' "$FAKE_GH/calls.log")" = 3 ] \
   && [ "$rc2" -eq 1 ] && [ "$(grep -c '^api repos/88plug/k3d-gpu/nope$' "$FAKE_GH/calls.log")" = 1 ]; then
    ok "gh_retry: a 5xx is retried under set -e, a 404 is not"
else
    fail "gh_retry: rc=$rc1/$rc2 out=$out calls=$(tr '\n' ';' < "$FAKE_GH/calls.log")"
fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
