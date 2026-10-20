#!/usr/bin/env bash
# hack/gate_merge.sh against stubbed gh, git and sleep (no network).
# Each case writes a fixture directory: the PR rollup, the ci.yml run list for
# the head SHA, the run list of every pull_request workflow for that SHA
# (allruns.json, default: runs.json), the head-SHA sequence, the merge exit
# code sequence, and what git reports about the base (ancestor rc sequence,
# files changed on main and on the PR since the merge base). The stub gh answers
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
echo "gh $*" >> "$F/calls"; echo "gh $*" >> "$F/trace"
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
    rc=$(pop merge_rc)
    if [ "$rc" = 0 ]; then echo MERGED > "$F/state"
    else echo "GraphQL: Base branch was modified. Review and try the merge again. (mergePullRequest)" >&2; fi
    exit "$rc";;
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
    shift 2; sha= q= wf=
    while [ $# -gt 0 ]; do case $1 in -c|--commit) sha=$2; shift 2;; -w|--workflow) wf=$2; shift 2;; -q) q=$2; shift 2;; *) shift;; esac; done
    echo "run-list $sha ${wf:-all}" >> "$F/calls"
    if [ -n "$wf" ] || [ ! -f "$F/allruns.json" ]; then out=$(cat "$F/runs.json"); else out=$(cat "$F/allruns.json"); fi
    if [ -n "$q" ]; then jq -r "$q" <<<"$out"; else echo "$out"; fi
    exit 0;;
esac
exit 0
STUB
cat > "$T/bin/git" <<'STUB'
#!/usr/bin/env bash
echo "git $*" >> "$FIX/git"; echo "git $*" >> "$FIX/trace"
pop() { local n total; n=$(cat "$FIX/$1.i" 2>/dev/null || echo 1); total=$(wc -l < "$FIX/$1")
  [ "$n" -gt "$total" ] && n=$total; sed -n "${n}p" "$FIX/$1"; echo $((n + 1)) > "$FIX/$1.i"; }
case "$*" in
  *"merge-base --is-ancestor"*) exit "$(pop ancestor)";;
  "merge-base "*) echo mb000; exit 0;;
  *"diff --name-only mb000 origin/main"*) cat "$FIX/base_files"; exit 0;;
  *"diff --name-only mb000 "*) cat "$FIX/pr_files"; exit 0;;
  *"worktree add"*) for a; do last=$a; done; prev=; for a; do [ "$a" = "$last" ] && break; prev=$a; done; mkdir -p "$prev";;
  *"rev-parse --show-toplevel"*) echo "$FIX";;
esac
exit 0
STUB
printf '#!/bin/sh\necho "sleep $*" >> "$FIX/trace"\nexit 0\n' > "$T/bin/sleep"
chmod +x "$T/bin/"*

GOOD_HELP='      --match-head-commit SHA   Commit SHA that the pull request head must match to allow merge'
SUCCESS='{"__typename":"CheckRun","name":"Shellcheck","workflowName":"CI","status":"COMPLETED","conclusion":"SUCCESS","startedAt":"2026-10-20T01:00:00Z"}'
SKIPPED='{"__typename":"CheckRun","name":"automerge","workflowName":"Auto-merge Dependabot PRs","status":"COMPLETED","conclusion":"SKIPPED","startedAt":"2026-10-20T01:00:00Z"}'
INPROG='{"__typename":"CheckRun","name":"Detection tests","workflowName":"CI","status":"IN_PROGRESS","conclusion":"","startedAt":"2026-10-20T01:00:00Z"}'
concl() { printf '{"__typename":"CheckRun","name":"Detection tests","workflowName":"CI","status":"COMPLETED","conclusion":"%s","startedAt":"2026-10-20T01:00:00Z"}' "$1"; }
CI_OK='[{"status":"completed","conclusion":"success"}]'

# fixture NAME ROLLUP RUNS HEADS [MERGE_RC_SEQ] [MERGEABLE_SEQ] [HELP]
# Base defaults: main is an ancestor of the head (no moved base).
fixture() {
  FIX=$T/$1; export FIX; mkdir -p "$FIX"
  echo "$2" > "$FIX/rollup.json"; echo "$3" > "$FIX/runs.json"
  printf '%s\n' $4 > "$FIX/heads"; printf '%s\n' ${5:-0} > "$FIX/merge_rc"
  echo 0 > "$FIX/ancestor"; echo scripts/k3d-gpu > "$FIX/base_files"; echo scripts/k3d-gpu > "$FIX/pr_files"
  printf '%s\n' ${6:-MERGEABLE} > "$FIX/mergeable"; echo "${7:-$GOOD_HELP}" > "$FIX/merge_help"
  echo OPEN > "$FIX/state"; : > "$FIX/calls"; : > "$FIX/git"; : > "$FIX/trace"
}
run_gate() {
  OUT=$(PATH="$T/bin:$PATH" GATE_REPO="$FIX" GATE_WT_ROOT="$FIX/wt" GATE_POLLS=3 \
        timeout 60 bash "$GATE" 1 subj body 2>&1); RC=$?
}
merges() { [ -f "$FIX/merges" ] && wc -l < "$FIX/merges" || echo 0; }
syncs()  { grep -c 'worktree add' "$FIX/git"; }
# every refused merge is followed by a sleep before the next merge attempt
paced()  { awk '/^gh pr merge 1 /{if (pend) bad=1; pend=1} /^sleep /{pend=0} END{exit bad}' "$FIX/trace"; }

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

# 7. head moves between the read and the merge: refused, the next round re-reads
#    and pins the new head; nothing to sync (the branch already moved).
fixture head_moved_resync "[$SUCCESS]" "$CI_OK" "a b c d e f g h i j" 1; run_gate
pins=$(grep -oE -- '--match-head-commit [a-z]+' "$FIX/merges" | sort -u | wc -l)
if ! grep -q MERGED <<<"$OUT" && grep -q 'GAVE UP' <<<"$OUT" && [ "$pins" -ge 2 ] && [ "$(syncs)" -eq 0 ]; then
  ok "head_moved_resync: refused merges re-pin a new head, no sync"
else fail "head_moved_resync: rc=$RC pins=$pins syncs=$(syncs) merges=$(tr '\n' ';' < "$FIX/merges" 2>/dev/null)"; fi

# 7b. refused with the head unchanged (base moved, mergeability not computed,
#     API error): the reason is printed, the gate sleeps, and the next round
#     re-checks mergeable and the base before merging again.
fixture refused_same_head "[$SUCCESS]" "$CI_OK" abc123 "1 0"; run_gate
if [ "$RC" -eq 0 ] && grep -q 'MERGED' <<<"$OUT" && [ "$(merges)" -eq 2 ] \
   && grep -q 'Base branch was modified' <<<"$OUT" && paced \
   && [ "$(grep -c 'pr view 1 --json mergeable' "$FIX/calls")" -ge 2 ]; then
  ok "refused_same_head: reason shown, paced, re-checked, then merged"
else fail "refused_same_head: rc=$RC merges=$(merges) syncs=$(syncs) paced=$(paced && echo y || echo n) out=$(tr '\n' ';' <<<"$OUT")"; fi

# 7c. refused every round with the head unchanged: every refusal is paced,
#     never a tight spin to GAVE UP.
fixture refused_spin "[$SUCCESS]" "$CI_OK" abc123 1; run_gate
if [ "$RC" -eq 1 ] && grep -q 'GAVE UP' <<<"$OUT" && [ "$(merges)" -eq 5 ] && paced; then
  ok "refused_spin: every refusal paced"
else fail "refused_spin: rc=$RC merges=$(merges) syncs=$(syncs) paced=$(paced && echo y || echo n)"; fi

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

# 12. a queued re-run whose startedAt is unset (gh: Go zero time) still counts
#     as the latest run of that check: wait for it, do not fail on the old run.
fixture rerun_queued_zero_time "[$SUCCESS,$(concl FAILURE),{\"__typename\":\"CheckRun\",\"name\":\"Detection tests\",\"workflowName\":\"CI\",\"status\":\"QUEUED\",\"conclusion\":\"\",\"startedAt\":\"0001-01-01T00:00:00Z\"}]" "$CI_OK" abc123; run_gate
if [ "$(merges)" -eq 0 ] && ! grep -q 'FAIL' <<<"$OUT" && grep -q 'GAVE UP' <<<"$OUT"; then ok "rerun_queued_zero_time: waits for the re-run"
else fail "rerun_queued_zero_time: rc=$RC merges=$(merges) out=$(tail -1 <<<"$OUT")"; fi

# 13. another pull_request workflow for this SHA (docs.yml) whose checks are not
#     in the rollup yet: its run is still in progress, so wait.
RUN_CI='{"workflowName":"CI","status":"completed","conclusion":"success","createdAt":"2026-10-20T01:00:00Z"}'
RUN_DEP='{"workflowName":"Auto-merge Dependabot PRs","status":"completed","conclusion":"skipped","createdAt":"2026-10-20T01:00:00Z"}'
fixture other_workflow_pending "[$SUCCESS]" "$CI_OK" abc123
echo "[$RUN_CI,$RUN_DEP,{\"workflowName\":\"Docs\",\"status\":\"in_progress\",\"conclusion\":\"\",\"createdAt\":\"2026-10-20T01:00:00Z\"}]" > "$FIX/allruns.json"; run_gate
if [ "$(merges)" -eq 0 ]; then ok "other_workflow_pending: no merge"
else fail "other_workflow_pending: merged before Docs finished"; fi
fixture other_workflow_failed "[$SUCCESS]" "$CI_OK" abc123
echo "[$RUN_CI,$RUN_DEP,{\"workflowName\":\"Docs\",\"status\":\"completed\",\"conclusion\":\"failure\",\"createdAt\":\"2026-10-20T01:00:00Z\"}]" > "$FIX/allruns.json"; run_gate
if [ "$RC" -eq 1 ] && [ "$(merges)" -eq 0 ] && grep -q 'FAIL' <<<"$OUT"; then ok "other_workflow_failed: exit 1"
else fail "other_workflow_failed: rc=$RC merges=$(merges)"; fi
fixture other_workflow_skipped "[$SUCCESS]" "$CI_OK" abc123
echo "[$RUN_CI,$RUN_DEP]" > "$FIX/allruns.json"; run_gate
if [ "$(merges)" -eq 1 ]; then ok "other_workflow_skipped: merges"
else fail "other_workflow_skipped: rc=$RC merges=$(merges)"; fi

# 14. main moved under code since the head was tested, and the PR changes code:
#     sync (CI re-runs on the merged head) before merging.
fixture base_moved_code "[$SUCCESS]" "$CI_OK" abc123; printf '1\n0\n' > "$FIX/ancestor"; run_gate
first_merge=$(grep -n '^gh pr merge 1 ' "$FIX/trace" | head -1 | cut -d: -f1)
first_sync=$(grep -n 'worktree add' "$FIX/trace" | head -1 | cut -d: -f1)
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && [ -n "$first_sync" ] && [ "$first_sync" -lt "${first_merge:-0}" ]; then
  ok "base_moved_code: synced before the merge"
else fail "base_moved_code: rc=$RC merges=$(merges) sync_line=${first_sync:-none} merge_line=${first_merge:-none}"; fi

# 15. main moved only in ledger files: merge without a re-run.
fixture base_moved_ledger "[$SUCCESS]" "$CI_OK" abc123; echo 1 > "$FIX/ancestor"
printf 'EXPERIMENTS.md\ndocs/provenance.md\n' > "$FIX/base_files"; run_gate
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && [ "$(syncs)" -eq 0 ]; then ok "base_moved_ledger: merged, no sync"
else fail "base_moved_ledger: rc=$RC merges=$(merges) syncs=$(syncs)"; fi

# 16. main moved under code but the PR changes only the ledger: merge.
fixture base_moved_pr_ledger "[$SUCCESS]" "$CI_OK" abc123; echo 1 > "$FIX/ancestor"
echo EXPERIMENTS.md > "$FIX/pr_files"; run_gate
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && [ "$(syncs)" -eq 0 ]; then ok "base_moved_pr_ledger: merged, no sync"
else fail "base_moved_pr_ledger: rc=$RC merges=$(merges) syncs=$(syncs)"; fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
