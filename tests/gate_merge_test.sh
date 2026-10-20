#!/usr/bin/env bash
# hack/gate_merge.sh against stubbed gh, git and sleep (no network).
# Each case writes a fixture directory: the PR rollup, the ci.yml run list for
# the head SHA, the head-SHA sequence, the merge exit code. The stub gh answers
# both `gh pr view --json statusCheckRollup` and `gh pr checks --json bucket`
# from the same rollup (bucket mapping as in cli/cli pkg/cmd/pr/checks/aggregate.go).
#
# Usage: tests/gate_merge_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GATE=${GATE:-${ROOT}/hack/gate_merge.sh}
T=$(mktemp -d "${TMPDIR:-/tmp}/gate_test.XXXXXX")
trap 'rm -rf "$T"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

mkdir -p "$T/bin"
cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
F=$FIX
echo "gh $*" >> "$F/calls"
pop() {  # next line of $F/$1 (the last line repeats)
  local n; n=$(cat "$F/$1.i" 2>/dev/null || echo 1)
  local total; total=$(wc -l < "$F/$1")
  [ "$n" -gt "$total" ] && n=$total
  sed -n "${n}p" "$F/$1"; echo $((n + 1)) > "$F/$1.i"
}
case "$1 $2" in
  "pr merge")
    if [ "${3:-}" = --help ]; then cat "$F/merge_help"; exit 0; fi
    echo "$*" >> "$F/merges"
    rc=$(cat "$F/merge_rc"); [ "$rc" = 0 ] && echo MERGED > "$F/state"; exit "$rc";;
  "pr ready") exit 0;;
  "pr checks")
    # gh keeps only the latest run per check (eliminateDuplicates), as the gate does.
    jq '[group_by([.__typename, (.name // .context), (.workflowName // "")])[] | max_by(.startedAt // "")
        | ((if (.conclusion // "") != "" then .conclusion else (.state // "PENDING") end) | ascii_upcase) as $s
        | {bucket: (if $s == "SUCCESS" then "pass"
                    elif $s == "SKIPPED" or $s == "NEUTRAL" then "skipping"
                    elif $s == "ERROR" or $s == "FAILURE" or $s == "TIMED_OUT" or $s == "ACTION_REQUIRED" then "fail"
                    elif $s == "CANCELLED" then "cancel" else "pending" end)}]' "$F/rollup.json"; exit 0;;
  "pr view")
    shift 3; fields= q=
    while [ $# -gt 0 ]; do case $1 in --json) fields=$2; shift 2;; -q) q=$2; shift 2;; *) shift;; esac; done
    head=deadbeef; case $fields in *headRefOid*) head=$(pop heads);; esac
    mg=MERGEABLE; case $fields in *mergeable*) mg=$(pop mergeable);; esac
    obj=$(jq -n --arg h "$head" --arg m "$mg" --arg s "$(cat "$F/state")" --slurpfile r "$F/rollup.json" \
      '{headRefName:"feat", isDraft:false, state:$s, mergeable:$m, headRefOid:$h, statusCheckRollup:$r[0]}')
    if [ -n "$q" ]; then jq -r "$q" <<<"$obj"; else jq "{$fields}" <<<"$obj" 2>/dev/null || echo "$obj"; fi
    exit 0;;
  "run list")
    shift 2; sha= q=
    while [ $# -gt 0 ]; do case $1 in -c|--commit) sha=$2; shift 2;; -q) q=$2; shift 2;; *) shift;; esac; done
    echo "run-list $sha" >> "$F/calls"
    out=$(cat "$F/runs.json")
    if [ -n "$q" ]; then jq -r "$q" <<<"$out"; else echo "$out"; fi
    exit 0;;
esac
exit 0
STUB
cat > "$T/bin/git" <<'STUB'
#!/usr/bin/env bash
echo "git $*" >> "$FIX/git"
case "$*" in
  *"worktree add"*) for a; do last=$a; done; prev=; for a; do [ "$a" = "$last" ] && break; prev=$a; done; mkdir -p "$prev";;
  *"rev-parse --show-toplevel"*) echo "$FIX";;
esac
exit 0
STUB
printf '#!/bin/sh\nexit 0\n' > "$T/bin/sleep"
chmod +x "$T/bin/"*

GOOD_HELP='      --match-head-commit SHA   Commit SHA that the pull request head must match to allow merge'
SUCCESS='{"__typename":"CheckRun","name":"Shellcheck","workflowName":"CI","status":"COMPLETED","conclusion":"SUCCESS","startedAt":"2026-10-20T01:00:00Z"}'
SKIPPED='{"__typename":"CheckRun","name":"automerge","workflowName":"Auto-merge Dependabot PRs","status":"COMPLETED","conclusion":"SKIPPED","startedAt":"2026-10-20T01:00:00Z"}'
INPROG='{"__typename":"CheckRun","name":"Detection tests","workflowName":"CI","status":"IN_PROGRESS","conclusion":"","startedAt":"2026-10-20T01:00:00Z"}'
concl() { printf '{"__typename":"CheckRun","name":"Detection tests","workflowName":"CI","status":"COMPLETED","conclusion":"%s","startedAt":"2026-10-20T01:00:00Z"}' "$1"; }
CI_OK='[{"status":"completed","conclusion":"success"}]'

# fixture NAME ROLLUP RUNS HEADS [MERGE_RC] [MERGEABLE_SEQ] [HELP]
fixture() {
  FIX=$T/$1; export FIX; mkdir -p "$FIX"
  echo "$2" > "$FIX/rollup.json"; echo "$3" > "$FIX/runs.json"
  printf '%s\n' $4 > "$FIX/heads"; echo "${5:-0}" > "$FIX/merge_rc"
  printf '%s\n' ${6:-MERGEABLE} > "$FIX/mergeable"; echo "${7:-$GOOD_HELP}" > "$FIX/merge_help"
  echo OPEN > "$FIX/state"; : > "$FIX/calls"; : > "$FIX/git"
}
run_gate() {
  OUT=$(PATH="$T/bin:$PATH" GATE_REPO="$FIX" GATE_WT_ROOT="$FIX/wt" GATE_POLLS=3 \
        timeout 60 bash "$GATE" 1 subj body 2>&1); RC=$?
}
merges() { [ -f "$FIX/merges" ] && wc -l < "$FIX/merges" || echo 0; }

# 1. green: exactly one merge, pinned to the SHA whose checks were read.
fixture green_pinned "[$SUCCESS,$SKIPPED]" "$CI_OK" abc123; run_gate
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && grep -qE -- '--match-head-commit abc123( |$)' "$FIX/merges"; then
  ok "green_pinned: one merge pinned to abc123"
else fail "green_pinned: rc=$RC merges=$(merges) ($(cat "$FIX/merges" 2>/dev/null))"; fi

# 2. a cancelled check is not green.
fixture cancelled_not_green "[$SUCCESS,$(concl CANCELLED)]" "$CI_OK" abc123; run_gate
if [ "$RC" -eq 1 ] && [ "$(merges)" -eq 0 ] && grep -q FAIL <<<"$OUT"; then ok "cancelled_not_green"
else fail "cancelled_not_green: rc=$RC merges=$(merges)"; fi

# 3. every terminal non-green conclusion blocks at once (no polling to the bound).
for c in TIMED_OUT STARTUP_FAILURE ACTION_REQUIRED STALE; do
  fixture "terminal_$c" "[$SUCCESS,$(concl "$c")]" "$CI_OK" abc123; run_gate
  if [ "$RC" -eq 1 ] && [ "$(merges)" -eq 0 ] && grep -q FAIL <<<"$OUT"; then ok "terminal_conclusions: $c"
  else fail "terminal_conclusions: $c rc=$RC merges=$(merges)"; fi
done

# 4. a rollup holding only the dependabot skip, and no CI run for the SHA.
fixture only_skipping_ci_missing "[$SKIPPED]" '[]' abc123; run_gate
if [ "$(merges)" -eq 0 ]; then ok "only_skipping_ci_missing: no merge"
else fail "only_skipping_ci_missing: merged with no CI run"; fi

# 5. rollup green but the ci.yml run for this SHA is still in progress.
fixture ci_run_in_progress "[$SUCCESS]" '[{"status":"in_progress","conclusion":""}]' abc123; run_gate
if [ "$(merges)" -eq 0 ] && grep -q 'run-list abc123' "$FIX/calls"; then ok "ci_run_in_progress: no merge"
else fail "ci_run_in_progress: merges=$(merges)"; fi

# 5b. the ci.yml run for this SHA completed and failed.
fixture ci_run_failed "[$SUCCESS]" '[{"status":"completed","conclusion":"failure"}]' abc123; run_gate
if [ "$RC" -eq 1 ] && [ "$(merges)" -eq 0 ]; then ok "ci_run_failed: no merge, exit 1"
else fail "ci_run_failed: rc=$RC merges=$(merges)"; fi

# 6. legacy StatusContext entries read through .state.
fixture status_context_ok '[{"__typename":"StatusContext","context":"ext","state":"SUCCESS"}]' "$CI_OK" abc123; run_gate
if [ "$(merges)" -eq 1 ] && grep -q -- '--match-head-commit abc123' "$FIX/merges"; then ok "status_context_shape: SUCCESS merges pinned"
else fail "status_context_shape: SUCCESS merges=$(merges)"; fi
fixture status_context_pending '[{"__typename":"StatusContext","context":"ext","state":"PENDING"}]' "$CI_OK" abc123; run_gate
if [ "$(merges)" -eq 0 ]; then ok "status_context_shape: PENDING waits"
else fail "status_context_shape: PENDING merged"; fi

# 7. head moves between the read and the merge: refused, every round re-pins.
fixture head_moved_resync "[$SUCCESS]" "$CI_OK" "a b c d e f" 1; run_gate
if ! grep -q MERGED <<<"$OUT" && grep -q 'GAVE UP' <<<"$OUT" \
   && grep -q -- '--match-head-commit a' "$FIX/merges" && grep -q -- '--match-head-commit b' "$FIX/merges"; then
  ok "head_moved_resync: refused merges re-pin, ends GAVE UP"
else fail "head_moved_resync: rc=$RC out=$(tail -1 <<<"$OUT") merges=$(cat "$FIX/merges" 2>/dev/null | tr '\n' ';')"; fi

# 8. the failure path cleans up the sync worktree and branch.
fixture cleanup_on_fail_path "[$SUCCESS,$(concl FAILURE)]" "$CI_OK" abc123 0 "CONFLICTING MERGEABLE"; run_gate
if [ "$RC" -eq 1 ] && tail -2 "$FIX/git" | head -1 | grep -q "worktree remove --force $FIX/wt/gm-1" \
   && tail -1 "$FIX/git" | grep -q 'branch -D gm/1'; then ok "cleanup_on_fail_path"
else fail "cleanup_on_fail_path: rc=$RC git tail: $(tail -2 "$FIX/git" | tr '\n' ';')"; fi

# 9. a gh without --match-head-commit never merges unpinned.
fixture old_gh_refuses "[$SUCCESS]" "$CI_OK" abc123 0 MERGEABLE '      --squash   Squash the commits'; run_gate
if [ "$RC" -eq 2 ] && [ "$(merges)" -eq 0 ]; then ok "old_gh_refuses: exit 2"
else fail "old_gh_refuses: rc=$RC merges=$(merges)"; fi

# 10. a running CheckRun (live shape: conclusion "") waits.
fixture pending_running "[$SUCCESS,$INPROG]" "$CI_OK" abc123; run_gate
if [ "$(merges)" -eq 0 ]; then ok "pending_running: no merge"
else fail "pending_running: merged"; fi
fixture pending_null "[$SUCCESS,{\"__typename\":\"CheckRun\",\"name\":\"x\",\"status\":\"QUEUED\",\"conclusion\":null}]" "$CI_OK" abc123; run_gate
if [ "$(merges)" -eq 0 ]; then ok "pending_null: no merge"
else fail "pending_null: merged"; fi

# 11. a re-run that succeeded supersedes its earlier failure (latest per check).
fixture rerun_dedup "[$SUCCESS,$(concl FAILURE),{\"__typename\":\"CheckRun\",\"name\":\"Detection tests\",\"workflowName\":\"CI\",\"status\":\"COMPLETED\",\"conclusion\":\"SUCCESS\",\"startedAt\":\"2026-10-20T02:00:00Z\"}]" "$CI_OK" abc123; run_gate
if [ "$(merges)" -eq 1 ]; then ok "rerun_dedup: latest run per check counts"
else fail "rerun_dedup: rc=$RC merges=$(merges)"; fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
