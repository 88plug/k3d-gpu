#!/usr/bin/env bash
# hack/bot-ci-dispatch against a fake gh (no network).
# The fake keeps open PRs in prs.json and workflow runs in runs.json. `gh run
# list` filters runs like the API (-w workflow file, -c head SHA, -b branch,
# -e event, -L count, newest first). `gh workflow run` appends a queued
# workflow_dispatch run at the branch's head, unless the branch is listed in
# no_dispatch (its workflow file lacks the trigger: HTTP 422) or dispatch_fail
# exists (HTTP 500). `gh run rerun ID --failed` queues attempt 2 of that run.
# Every dispatch and rerun is logged to actions.
#
# Usage: tests/bot_ci_dispatch_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BCD=${BCD:-${ROOT}/hack/bot-ci-dispatch}
T=$(mktemp -d "${TMPDIR:-/tmp}/bot_ci_dispatch.XXXXXX")
trap 'rm -rf "$T"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

mkdir -p "$T/bin"
cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
F=$FIX
echo "gh $*" >> "$F/calls"
case "$1 $2" in
  "pr list") cat "$F/prs.json"; exit 0;;
  "run list")
    shift 2; wf= c= b= e= q= L=1000
    while [ $# -gt 0 ]; do case $1 in
      -w|--workflow) wf=$2; shift 2;; -c|--commit) c=$2; shift 2;; -b|--branch) b=$2; shift 2;;
      -e|--event) e=$2; shift 2;; -q|--jq) q=$2; shift 2;; -L|--limit) L=$2; shift 2;; --json) shift 2;; *) shift;; esac; done
    out=$(jq --arg w "$wf" --arg c "$c" --arg b "$b" --arg e "$e" --argjson L "$L" \
      '[.[] | select(($w == "" or .workflow == $w) and ($c == "" or .headSha == $c)
                     and ($b == "" or .headBranch == $b) and ($e == "" or .event == $e))]
       | sort_by(.createdAt) | reverse | .[:$L]' "$F/runs.json")
    if [ -n "$q" ]; then jq -r "$q" <<<"$out"; else echo "$out"; fi
    exit 0;;
  "workflow run")
    wf=$3; shift 3; ref=
    while [ $# -gt 0 ]; do case $1 in -r|--ref) ref=$2; shift 2;; *) shift;; esac; done
    if grep -qxF "$ref" "$F/no_dispatch" 2>/dev/null; then
      echo "could not create workflow dispatch event: HTTP 422: Workflow does not have 'workflow_dispatch' trigger (https://api.github.com/repos/o/r/actions/workflows/1/dispatches)" >&2
      exit 1
    fi
    [ -f "$F/dispatch_fail" ] && { echo "could not create workflow dispatch event: HTTP 500: Server Error" >&2; exit 1; }
    sha=$(jq -r --arg b "$ref" '[.[] | select(.headRefName == $b)][0].headRefOid // "unknown"' "$F/prs.json")
    n=$(( $(jq length "$F/runs.json") + 900 ))
    jq --arg w "$wf" --arg b "$ref" --arg s "$sha" --argjson id "$n" \
      '. + [{databaseId: $id, workflow: $w, event: "workflow_dispatch", headBranch: $b, headSha: $s,
             status: "queued", conclusion: "", attempt: 1, createdAt: "2026-10-29T23:00:00Z"}]' \
      "$F/runs.json" > "$F/runs.tmp" && mv "$F/runs.tmp" "$F/runs.json"
    echo "dispatch $wf $ref" >> "$F/actions"
    exit 0;;
  "run rerun")
    id=$3; echo "rerun $id ${*:4}" >> "$F/actions"
    jq --argjson id "$id" 'map(if .databaseId == $id then .attempt += 1 | .status = "queued" | .conclusion = "" else . end)' \
      "$F/runs.json" > "$F/runs.tmp" && mv "$F/runs.tmp" "$F/runs.json"
    exit 0;;
esac
exit 0
STUB
printf '#!/bin/sh\nexit 0\n' > "$T/bin/sleep"
chmod +x "$T/bin/"*

BOT='{"login":"app/github-actions","is_bot":true}'
HUMAN='{"login":"88plug","is_bot":false}'
DEPENDABOT='{"login":"app/dependabot","is_bot":true}'
# pr NUMBER BRANCH SHA [AUTHOR] [CROSS]: one open PR into main.
pr() {
  printf '{"number":%s,"headRefName":"%s","headRefOid":"%s","baseRefName":"main","isCrossRepository":%s,"author":%s}' \
    "$1" "$2" "$3" "${5:-false}" "${4:-$BOT}"
}
# run ID WORKFLOW EVENT BRANCH SHA STATUS CONCLUSION [ATTEMPT] [CREATED]
run() {
  printf '{"databaseId":%s,"workflow":"%s","event":"%s","headBranch":"%s","headSha":"%s","status":"%s","conclusion":"%s","attempt":%s,"createdAt":"%s"}' \
    "$1" "$2" "$3" "$4" "$5" "$6" "$7" "${8:-1}" "${9:-2026-10-29T20:00:00Z}"
}
# fixture NAME PRS RUNS
fixture() {
  FIX=$T/$1; export FIX; mkdir -p "$FIX"
  echo "$2" > "$FIX/prs.json"; echo "$3" > "$FIX/runs.json"; : > "$FIX/calls"; : > "$FIX/actions"
}
bcd() { OUT=$(PATH="$T/bin:$PATH" timeout 60 bash "$BCD" "$@" 2>&1); RC=$?; }
acts() { grep -c "^$1 " "$FIX/actions"; }
dispatched() { grep -qxF "dispatch ${2:-ci.yml} $1" "$FIX/actions"; }

# 1. scope: only same-repo autobump/* PRs by github-actions[bot] with no run at the
#    head (a held run does not count) get a dispatch; forks, human branches, a
#    human's autobump/ branch and dependabot never do.
fixture scope "[$(pr 1 autobump/a a111),$(pr 2 autobump/fork f222 "$BOT" true),$(pr 3 claude/x c333 "$HUMAN"),$(pr 4 autobump/human h444 "$HUMAN"),$(pr 5 dependabot/pip/x d555 "$DEPENDABOT"),$(pr 6 autobump/done e666),$(pr 7 autobump/held g777)]" \
  "[$(run 1 ci.yml pull_request autobump/done e666 completed success),$(run 2 ci.yml pull_request autobump/held g777 completed action_required)]"
BOT_CI_MAX_INFLIGHT=10 bcd
if [ "$RC" -eq 0 ] && [ "$(acts dispatch)" -eq 2 ] && dispatched autobump/a && dispatched autobump/held; then
  ok "scope: dispatched autobump/a and the held autobump/held only"
else fail "scope: rc=$RC actions=$(tr '\n' ';' < "$FIX/actions") out=$(tr '\n' ';' <<<"$OUT")"; fi
if grep -q '#6 autobump/done' <<<"$OUT" && ! grep -qE '#(2|3|4|5) ' <<<"$OUT"; then
  ok "scope: a line for the PR with a run at its head, none for the out-of-scope PRs"
else fail "scope: decision lines: $(tr '\n' ';' <<<"$OUT")"; fi

# 2. the in-flight cap (default 1): two PRs, nothing running -> one dispatch, one deferred.
fixture cap "[$(pr 1 autobump/a a111),$(pr 2 autobump/b b222)]" '[]'
bcd
if [ "$RC" -eq 0 ] && [ "$(acts dispatch)" -eq 1 ] && dispatched autobump/a && grep -q '#2 autobump/b.*deferred' <<<"$OUT"; then
  ok "cap: one dispatch, the second deferred"
else fail "cap: rc=$RC actions=$(tr '\n' ';' < "$FIX/actions") out=$(tr '\n' ';' <<<"$OUT")"; fi
# a bot run already in flight on another autobump branch fills the cap.
fixture cap_full "[$(pr 1 autobump/a a111)]" "[$(run 5 ci.yml workflow_dispatch autobump/z z999 in_progress '')]"
bcd
if [ "$RC" -eq 0 ] && [ "$(acts dispatch)" -eq 0 ] && grep -q 'deferred' <<<"$OUT"; then ok "cap_full: deferred"
else fail "cap_full: rc=$RC actions=$(tr '\n' ';' < "$FIX/actions")"; fi

# 3. a queued ci.yml run of a human PR defers every bot dispatch; a running one does not.
fixture human_queued "[$(pr 1 autobump/a a111)]" "[$(run 5 ci.yml pull_request claude/x c333 queued '')]"
BOT_CI_MAX_INFLIGHT=10 bcd
if [ "$RC" -eq 0 ] && [ "$(acts dispatch)" -eq 0 ] && grep -q 'human' <<<"$OUT"; then ok "human_queued: deferred"
else fail "human_queued: rc=$RC actions=$(tr '\n' ';' < "$FIX/actions") out=$(tr '\n' ';' <<<"$OUT")"; fi
# a queued run whose head branch is gone (headBranch null) counts as a human run.
fixture human_null_branch "[$(pr 1 autobump/a a111)]" \
  "[{\"databaseId\":5,\"workflow\":\"ci.yml\",\"event\":\"pull_request\",\"headBranch\":null,\"headSha\":\"c333\",\"status\":\"queued\",\"conclusion\":\"\",\"attempt\":1,\"createdAt\":\"2026-10-29T20:00:00Z\"}]"
BOT_CI_MAX_INFLIGHT=10 bcd
if [ "$RC" -eq 0 ] && [ "$(acts dispatch)" -eq 0 ] && grep -q 'deferred, 1 ci.yml run(s) of human PRs queued' <<<"$OUT"; then
  ok "human_null_branch: deferred"
else fail "human_null_branch: rc=$RC actions=$(tr '\n' ';' < "$FIX/actions") out=$(tr '\n' ';' <<<"$OUT")"; fi
fixture human_running "[$(pr 1 autobump/a a111)]" "[$(run 5 ci.yml pull_request claude/x c333 in_progress '')]"
bcd
if [ "$RC" -eq 0 ] && [ "$(acts dispatch)" -eq 1 ]; then ok "human_running: dispatched"
else fail "human_running: rc=$RC actions=$(tr '\n' ';' < "$FIX/actions")"; fi

# 4. idempotent: a second call dispatches nothing more.
fixture idempotent "[$(pr 1 autobump/a a111),$(pr 2 autobump/b b222)]" '[]'
BOT_CI_MAX_INFLIGHT=10 bcd; first=$(acts dispatch); BOT_CI_MAX_INFLIGHT=10 bcd
if [ "$first" -eq 2 ] && [ "$(acts dispatch)" -eq 2 ] && [ "$RC" -eq 0 ]; then ok "idempotent: 2 dispatches, none on the second call"
else fail "idempotent: first=$first total=$(acts dispatch) rc=$RC"; fi

# 5. a run that failed on its first attempt gets its failed jobs re-run once.
fixture rerun "[$(pr 1 autobump/a a111)]" "[$(run 41 ci.yml workflow_dispatch autobump/a a111 completed failure)]"
bcd
if [ "$RC" -eq 0 ] && grep -qxF 'rerun 41 --failed' "$FIX/actions" && [ "$(acts dispatch)" -eq 0 ]; then ok "rerun: failed jobs of run 41 re-run"
else fail "rerun: rc=$RC actions=$(tr '\n' ';' < "$FIX/actions") out=$(tr '\n' ';' <<<"$OUT")"; fi
jq 'map(if .databaseId == 41 then .status = "completed" | .conclusion = "failure" else . end)' "$FIX/runs.json" > "$FIX/r" && mv "$FIX/r" "$FIX/runs.json"
bcd
if [ "$RC" -eq 0 ] && [ "$(acts rerun)" -eq 1 ] && [ "$(acts dispatch)" -eq 0 ]; then ok "rerun: not again after the re-run failed"
else fail "rerun: second call rc=$RC actions=$(tr '\n' ';' < "$FIX/actions") out=$(tr '\n' ';' <<<"$OUT")"; fi
# a cancelled run is someone's decision: no re-run.
fixture rerun_cancelled "[$(pr 1 autobump/a a111)]" "[$(run 41 ci.yml workflow_dispatch autobump/a a111 completed cancelled)]"
bcd
if [ "$RC" -eq 0 ] && [ "$(acts rerun)" -eq 0 ] && [ "$(acts dispatch)" -eq 0 ]; then ok "rerun_cancelled: left alone"
else fail "rerun_cancelled: rc=$RC actions=$(tr '\n' ';' < "$FIX/actions")"; fi

# 6. a branch whose ci.yml has no workflow_dispatch: skipped with a reason, exit 0.
fixture no_trigger "[$(pr 1 autobump/old o111),$(pr 2 autobump/b b222)]" '[]'; echo autobump/old > "$FIX/no_dispatch"
BOT_CI_MAX_INFLIGHT=10 bcd
if [ "$RC" -eq 0 ] && grep -q '#1 autobump/old.*workflow_dispatch' <<<"$OUT" && [ "$(acts dispatch)" -eq 1 ] && dispatched autobump/b; then
  ok "no_trigger: skipped, the next PR still dispatched"
else fail "no_trigger: rc=$RC actions=$(tr '\n' ';' < "$FIX/actions") out=$(tr '\n' ';' <<<"$OUT")"; fi
# any other dispatch failure is an error: exit 1.
fixture dispatch_error "[$(pr 1 autobump/a a111)]" '[]'; touch "$FIX/dispatch_fail"
bcd
if [ "$RC" -eq 1 ] && grep -q 'HTTP 500' <<<"$OUT"; then ok "dispatch_error: exit 1"
else fail "dispatch_error: rc=$RC out=$(tr '\n' ';' <<<"$OUT")"; fi

# 7. --branch limits it to one PR; --workflow picks the workflow and its own runs.
fixture one_branch "[$(pr 1 autobump/a a111),$(pr 2 autobump/b b222)]" '[]'
BOT_CI_MAX_INFLIGHT=10 bcd --branch autobump/b
if [ "$RC" -eq 0 ] && [ "$(acts dispatch)" -eq 1 ] && dispatched autobump/b; then ok "one_branch: only autobump/b"
else fail "one_branch: rc=$RC actions=$(tr '\n' ';' < "$FIX/actions")"; fi
fixture canary "[$(pr 1 autobump/a a111)]" "[$(run 5 ci.yml workflow_dispatch autobump/a a111 completed success)]"
bcd --workflow canary.yml
if [ "$RC" -eq 0 ] && [ "$(acts dispatch)" -eq 1 ] && dispatched autobump/a canary.yml; then ok "workflow: canary.yml dispatched beside a green ci.yml run"
else fail "workflow: rc=$RC actions=$(tr '\n' ';' < "$FIX/actions")"; fi
bcd --bogus
if [ "$RC" -eq 2 ]; then ok "usage: unknown flag exits 2"; else fail "usage: rc=$RC"; fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
