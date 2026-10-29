#!/usr/bin/env bash
# hack/gate_merge.sh against stubbed gh, git and sleep (no network).
# Each case writes a fixture directory: the PR rollup, the ci.yml run list for
# the head SHA, the run list of every pull_request workflow for that SHA
# (allruns.json, default: runs.json), the head-SHA sequence, the merge exit
# code sequence, and what git reports about the base (ancestor rc sequence,
# files changed on main and on the PR since the merge base). The stub gh answers
# both `gh pr view --json statusCheckRollup` and `gh pr checks --json bucket`
# from the same rollup (bucket mapping as in cli/cli pkg/cmd/pr/checks/aggregate.go).
# `gh run list -e/-b` filter runs that carry event/headBranch (bare runs match
# any). Alarms: `gh issue list` answers the next line of alarms_seq (default
# none; issues_off makes it fail), and the PR's changed files are pr_paths
# (pr_paths_fail makes that fail).
# Cases 18-19 and 24-25 run the gate with real git against a bare origin (gh and
# sleep still stubbed): the head the stub gh reports is the origin's feat ref.
# The ci_status and rules_stale cases source hack/gate_lib.sh directly.
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
    head=deadbeef
    case $fields in *headRefOid*)
      # real-git cases: the head is whatever the bare origin's feat points at now
      if [ -f "$F/origin_git" ]; then head=$("$REALGIT" --git-dir="$(cat "$F/origin_git")" rev-parse -q --verify refs/heads/feat || echo gone)
      else head=$(pop heads); fi;;
    esac
    mg=MERGEABLE; case $fields in *mergeable*) mg=$(pop mergeable);; esac
    obj=$(jq -n --arg h "$head" --arg m "$mg" --arg s "$(cat "$F/state")" --slurpfile r "$F/rollup.json" \
      '{headRefName:"feat", isDraft:false, state:$s, mergeable:$m, headRefOid:$h, statusCheckRollup:$r[0]}')
    if [ -n "$q" ]; then jq -r "$q" <<<"$obj"; else jq "{$fields}" <<<"$obj" 2>/dev/null || echo "$obj"; fi
    exit 0;;
  "run list")
    shift 2; sha= q= wf= ev= rb=
    while [ $# -gt 0 ]; do case $1 in -c|--commit) sha=$2; shift 2;; -w|--workflow) wf=$2; shift 2;; -e|--event) ev=$2; shift 2;; -b|--branch) rb=$2; shift 2;; -q) q=$2; shift 2;; *) shift;; esac; done
    echo "run-list $sha ${wf:-all} ${ev:-any}" >> "$F/calls"
    if [ -n "$wf" ] || [ ! -f "$F/allruns.json" ]; then out=$(cat "$F/runs.json"); else out=$(cat "$F/allruns.json"); fi
    # -e and -b filter like the API on runs that carry event / headBranch; a bare run matches any.
    out=$(jq --arg e "$ev" --arg b "$rb" \
      'map(select(($e == "" or (.event // $e) == $e) and ($b == "" or (.headBranch // $b) == $b)))' <<<"$out")
    if [ -n "$q" ]; then jq -r "$q" <<<"$out"; else echo "$out"; fi
    exit 0;;
  "issue list")
    # open ci-alarm issues: the next line of alarms_seq (one JSON array per call), default none
    [ -f "$F/issues_off" ] && { echo "the 'o/r' repository has disabled issues" >&2; exit 1; }
    if [ -f "$F/alarms_seq" ]; then pop alarms_seq; else echo '[]'; fi
    exit 0;;
  "api --paginate")
    case $* in */pulls/*/files*)
      [ -f "$F/pr_paths_fail" ] && { echo "HTTP 502: Bad Gateway" >&2; exit 1; }
      cat "$F/pr_paths"; exit 0;; esac
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
  *"merge-tree --write-tree"*) [ -f "$FIX/tree" ] && pop tree; exit 0;;
  "show "*:*) p=${2#*:}; [ -f "$FIX/show/$p" ] || exit 128; cat "$FIX/show/$p"; exit 0;;
  *"rev-parse --show-toplevel"*) echo "$FIX";;
esac
exit 0
STUB
printf '#!/bin/sh\necho "sleep $*" >> "$FIX/trace"\nexit 0\n' > "$T/bin/sleep"
chmod +x "$T/bin/"*

# Real-git cases run the gate's own git against a bare origin and a clone ($FIX/repo):
# gh and sleep stay stubbed, git only logs and passes through, and python3 passes
# through except that the keep-both resolver fails when the fixture holds kb_fail.
REALGIT=$(command -v git) REALPY=$(command -v python3); export REALGIT REALPY
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_AUTHOR_NAME=gate-test GIT_AUTHOR_EMAIL=gate-test@example.invalid \
  GIT_COMMITTER_NAME=gate-test GIT_COMMITTER_EMAIL=gate-test@example.invalid
mkdir -p "$T/rbin"; cp "$T/bin/gh" "$T/bin/sleep" "$T/rbin/"
cat > "$T/rbin/git" <<'STUB'
#!/usr/bin/env bash
echo "git $*" >> "$FIX/git"; echo "git $*" >> "$FIX/trace"
exec "$REALGIT" "$@"
STUB
cat > "$T/rbin/python3" <<'STUB'
#!/usr/bin/env bash
case ${1:-} in */merge_keep_both.py) [ -f "$FIX/kb_fail" ] && { echo "keep-both resolver failed on $2" >&2; exit 1; };; esac
exec "$REALPY" "$@"
STUB
chmod +x "$T/rbin/"*

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
  echo scripts/k3d-gpu > "$FIX/pr_paths"
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

# 17. the merged tree would carry a console/ledger-rules.json its EXPERIMENTS.md does
#     not generate (a ledger row changed, the file did not): sync before merging.
#     The stub tree is stale once, then the synced head is current.
fixture rules_stale_sync "[$SUCCESS]" "$CI_OK" abc123
mkdir -p "$FIX/show/console"
cp "$ROOT/console/model_opt.py" "$ROOT/console/convert-pins.json" "$FIX/show/console/"
printf '## Hypotheses\n\n| H34 | on the i9-13900T | p | m | SUPPORTED |\n' > "$FIX/show/EXPERIMENTS.md"
echo '{"schema": "k3d-gpu.ledger-rules/v1", "rows": []}' > "$FIX/show/console/ledger-rules.json"
printf 't1\n\n' > "$FIX/tree"; run_gate
first_merge=$(grep -n '^gh pr merge 1 ' "$FIX/trace" | head -1 | cut -d: -f1)
first_sync=$(grep -n 'worktree add' "$FIX/trace" | head -1 | cut -d: -f1)
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && [ -n "$first_sync" ] && [ "$first_sync" -lt "${first_merge:-0}" ] \
   && grep -q 'ledger-rules.json would not match' <<<"$OUT"; then
  ok "rules_stale_sync: synced (regenerating ledger-rules.json) before the merge"
else fail "rules_stale_sync: rc=$RC merges=$(merges) sync_line=${first_sync:-none} merge_line=${first_merge:-none} out=$(tr '\n' ';' <<<"$OUT")"; fi
# 17b. the merged tree's file matches: no sync.
fixture rules_current "[$SUCCESS]" "$CI_OK" abc123
mkdir -p "$FIX/show/console"
cp "$ROOT/console/model_opt.py" "$ROOT/console/convert-pins.json" "$FIX/show/console/"
printf '## Hypotheses\n\n| H34 | on the i9-13900T | p | m | SUPPORTED |\n' > "$FIX/show/EXPERIMENTS.md"
python3 "$ROOT/console/model_opt.py" ledger-rules --ledger "$FIX/show/EXPERIMENTS.md" --out "$FIX/show/console/ledger-rules.json"
echo t1 > "$FIX/tree"; run_gate
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && [ "$(syncs)" -eq 0 ]; then ok "rules_current: merged, no sync"
else fail "rules_current: rc=$RC merges=$(merges) syncs=$(syncs)"; fi

# Real-git fixtures. realgit_fixture NAME ROLLUP RUNS [MERGE_RC_SEQ] [MERGEABLE_SEQ]:
# fixture NAME plus a bare origin and a clone on branch feat, where main holds
# model_opt.py, convert-pins.json, a one-row EXPERIMENTS.md, the ledger-rules.json it
# generates, docs/provenance.md and a passing tests/claims_check.sh.
repo_git() { "$REALGIT" -C "$FIX/repo" "$@"; }
realgit_fixture() {
  fixture "$1" "$2" "$3" unused "${4:-0}" "${5:-MERGEABLE}"
  local R=$FIX/repo
  "$REALGIT" init -q --bare -b main "$FIX/origin.git"; "$REALGIT" init -q -b main "$R"
  mkdir -p "$R/console" "$R/tests" "$R/docs"
  cp "$ROOT/console/model_opt.py" "$ROOT/console/convert-pins.json" "$R/console/"
  printf '#!/bin/sh\nexit 0\n' > "$R/tests/claims_check.sh"
  printf '## Hypotheses\n\n| H34 | on the i9-13900T | p | m | SUPPORTED |\n' > "$R/EXPERIMENTS.md"
  echo base > "$R/docs/provenance.md"
  mkdir -p "$R/scripts" "$R/packaging/aur" "$R/Formula"
  printf '#!/usr/bin/env bash\nset -euo pipefail\n\nVERSION="0.1.0"  # kept in lockstep\nCLUSTER=gpu\n' > "$R/scripts/k3d-gpu"
  echo 'pkgver=0.1.0' > "$R/packaging/aur/PKGBUILD"; echo 'pkgver = 0.1.0' > "$R/packaging/aur/.SRCINFO"
  echo 'VERSION=0.1.0' > "$R/packaging/install.sh"; echo 'version "0.1.0"' > "$R/Formula/k3d-gpu.rb"
  "$REALPY" "$R/console/model_opt.py" ledger-rules --ledger "$R/EXPERIMENTS.md" --out "$R/console/ledger-rules.json"
  repo_git add console tests docs scripts packaging Formula EXPERIMENTS.md && repo_git commit -q -m base \
    && repo_git remote add origin "$FIX/origin.git" && repo_git push -q origin main && repo_git checkout -q -b feat
  echo "$FIX/origin.git" > "$FIX/origin_git"
}
# commit_to BRANCH FILE CONTENT: commit FILE=CONTENT on BRANCH in the clone and push it.
commit_to() {
  repo_git checkout -q "$1" && printf '%s' "$3" > "$FIX/repo/$2" && repo_git add "$2" \
    && repo_git commit -q -m "edit $2 on $1" && repo_git push -q origin "$1"
}
run_gate_real() {
  OUT=$(PATH="$T/rbin:$PATH" GATE_REPO="$FIX/repo" GATE_WT_ROOT="$FIX/wt" GATE_POLLS=3 \
        timeout 60 bash "$GATE" 1 subj body 2>&1); RC=$?
}
pinned() { grep -oE -- '--match-head-commit [0-9a-f]+' "$FIX/merges" 2>/dev/null | head -1 | cut -d' ' -f2; }
REGEN='console/ledger-rules.json: regenerate from EXPERIMENTS.md'

# 18. the PR adds a rule row and leaves console/ledger-rules.json stale: the gate syncs,
#     the sync commits the regenerated file (model_opt.py's output) on top of the head
#     and pushes it, and the gate merges that commit without another sync.
realgit_fixture sync_regenerates_rules "[$SUCCESS]" "$CI_OK"
commit_to feat EXPERIMENTS.md $'## Hypotheses\n\n| H34 | on the i9-13900T | p | m | SUPPORTED |\n| H34b | on the i5-12500T | q | n | SUPPORTED |\n'
stale_head=$(repo_git rev-parse feat); run_gate_real; pin=$(pinned); regen_ok=n
if [ -n "$pin" ]; then
  repo_git show "$pin:EXPERIMENTS.md" > "$FIX/merged-EXPERIMENTS.md"
  "$REALPY" "$ROOT/console/model_opt.py" ledger-rules --ledger "$FIX/merged-EXPERIMENTS.md" --out "$FIX/want.json"
  [ "$(repo_git log -1 --format=%s "$pin")" = "$REGEN" ] && [ "$(repo_git rev-parse "$pin^")" = "$stale_head" ] \
    && repo_git show "$pin:console/ledger-rules.json" | cmp -s - "$FIX/want.json" && ! cmp -s "$FIX/want.json" "$FIX/repo/console/ledger-rules.json" \
    && regen_ok=y
fi
regen_line=$(grep -nxF "git commit -q -m $REGEN" "$FIX/trace" | head -1 | cut -d: -f1)
push_line=$(grep -nE ' push -q origin HEAD:refs/heads/feat$' "$FIX/trace" | head -1 | cut -d: -f1)
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && [ "$(syncs)" -eq 1 ] && [ "$regen_ok" = y ] \
   && [ -n "$regen_line" ] && [ "$regen_line" -lt "${push_line:-0}" ] \
   && [ "$(grep -c 'ledger-rules.json would not match' <<<"$OUT")" -eq 1 ]; then
  ok "sync_regenerates_rules: regen commit pushed before the merge, file equals model_opt.py output"
else fail "sync_regenerates_rules: rc=$RC merges=$(merges) syncs=$(syncs) regen_ok=$regen_ok regen_line=${regen_line:-none} push_line=${push_line:-none} out=$(tr '\n' ';' <<<"$OUT")"; fi

# 18b. main moved under code and the PR changes code, with the rules file current on
#      both sides: the sync merges main and adds no regen commit.
realgit_fixture sync_rules_current "[$SUCCESS]" "$CI_OK"
commit_to feat scripts.txt $'pr\n'; commit_to main README.txt $'main\n'
run_gate_real; pin=$(pinned)
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && [ "$(syncs)" -eq 1 ] && grep -q 'main moved under code' <<<"$OUT" \
   && [ -n "$pin" ] && [ "$(repo_git rev-list --parents -n 1 "$pin" | wc -w)" -eq 3 ] \
   && ! repo_git log --format=%s "$pin" | grep -qxF "$REGEN" && ! grep -qF "commit -q -m $REGEN" "$FIX/git"; then
  ok "sync_rules_current: merged main, no regen commit"
else fail "sync_rules_current: rc=$RC merges=$(merges) syncs=$(syncs) pin=${pin:-none} out=$(tr '\n' ';' <<<"$OUT")"; fi

# 19. a conflicted ledger file the keep-both resolver cannot settle: the merge commit
#     fails, so the sync fails at once (exit 1), never prints "synced" and pushes nothing.
realgit_fixture sync_merge_commit_fails "[$SUCCESS]" "$CI_OK" 1 CONFLICTING
commit_to main docs/provenance.md $'from main\n'; commit_to feat docs/provenance.md $'from feat\n'
touch "$FIX/kb_fail"; before=$(repo_git rev-parse feat); run_gate_real
after=$("$REALGIT" --git-dir="$FIX/origin.git" rev-parse -q --verify refs/heads/feat)
if [ "$RC" -eq 1 ] && ! grep -q 'synced' <<<"$OUT" && [ "$(syncs)" -eq 1 ] && [ "$(merges)" -eq 0 ] \
   && ! grep -q ' push ' "$FIX/git" && [ "$before" = "$after" ]; then
  ok "sync_merge_commit_fails: exit 1 at once, no push"
else fail "sync_merge_commit_fails: rc=$RC syncs=$(syncs) merges=$(merges) origin=$before->${after:-gone} out=$(tr '\n' ';' <<<"$OUT")"; fi

# ─── CI on bot PRs: a ci.yml run dispatched on the PR branch is CI evidence ────
# A PR that GITHUB_TOKEN opened gets pull_request runs held for approval
# (conclusion action_required); hack/bot-ci-dispatch runs ci.yml on the branch
# by workflow_dispatch instead. The stub PR's branch is feat.
# run_json EVENT BRANCH STATUS CONCLUSION CREATED: one ci.yml run for the head SHA.
run_json() { printf '{"workflowName":"CI","event":"%s","headBranch":"%s","status":"%s","conclusion":"%s","createdAt":"%s"}' "$@"; }
HELD_PR=$(run_json pull_request feat completed action_required 2026-10-20T01:00:00Z)
DISP_OK=$(run_json workflow_dispatch feat completed success 2026-10-20T01:05:00Z)
HELD_CI_CHECK=$(concl ACTION_REQUIRED)
HELD_DEP_CHECK='{"__typename":"CheckRun","name":"automerge","workflowName":"Auto-merge Dependabot PRs","status":"COMPLETED","conclusion":"ACTION_REQUIRED","startedAt":"2026-10-20T01:00:00Z"}'
HELD_DEP_RUN='{"workflowName":"Auto-merge Dependabot PRs","event":"pull_request","headBranch":"feat","status":"completed","conclusion":"action_required","createdAt":"2026-10-20T01:00:00Z"}'

# 20. no pull_request run at all, a green ci.yml run dispatched on the PR branch: merge.
fixture dispatch_green "[$SUCCESS]" "[$DISP_OK]" abc123; run_gate
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && grep -q -- '--match-head-commit abc123' "$FIX/merges"; then
  ok "dispatch_green: a dispatched run on the branch counts as CI"
else fail "dispatch_green: rc=$RC merges=$(merges) out=$(tr '\n' ';' <<<"$OUT")"; fi

# 21. held pull_request runs (CI and the dependabot workflow, in the rollup and the
#     run list) plus a green dispatched run: the held entries do not block; merge.
fixture held_plus_dispatch "[$SUCCESS,$HELD_CI_CHECK,$HELD_DEP_CHECK]" "[$HELD_PR,$DISP_OK]" abc123
echo "[$HELD_PR,$HELD_DEP_RUN,$DISP_OK]" > "$FIX/allruns.json"; run_gate
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ]; then ok "held_plus_dispatch: held runs ignored once a dispatched run is green"
else fail "held_plus_dispatch: rc=$RC merges=$(merges) out=$(tr '\n' ';' <<<"$OUT")"; fi

# 22. held alone still fails at once, as before: a held CheckRun in the rollup, or
#     (no check runs for a held run) only the held ci.yml run.
fixture held_alone_rollup "[$SUCCESS,$HELD_CI_CHECK]" "[$HELD_PR]" abc123; run_gate
if [ "$RC" -eq 1 ] && [ "$(merges)" -eq 0 ] && grep -q FAIL <<<"$OUT"; then ok "held_alone_rollup: exit 1"
else fail "held_alone_rollup: rc=$RC merges=$(merges)"; fi
fixture held_alone_run "[$SUCCESS]" "[$HELD_PR]" abc123; run_gate
if [ "$RC" -eq 1 ] && [ "$(merges)" -eq 0 ] && grep -q FAIL <<<"$OUT"; then ok "held_alone_run: exit 1"
else fail "held_alone_run: rc=$RC merges=$(merges)"; fi

# 23. a green run dispatched on another branch is not this PR's CI: with nothing
#     else no merge (wait), and it does not excuse a held run (exit 1).
fixture dispatch_other_branch "[$SUCCESS]" "[$(run_json workflow_dispatch other completed success 2026-10-20T01:05:00Z)]" abc123; run_gate
if [ "$(merges)" -eq 0 ] && grep -q 'GAVE UP' <<<"$OUT"; then ok "dispatch_other_branch: not accepted, waits"
else fail "dispatch_other_branch: rc=$RC merges=$(merges)"; fi
fixture dispatch_other_branch_held "[$SUCCESS,$HELD_CI_CHECK]" "[$HELD_PR,$(run_json workflow_dispatch other completed success 2026-10-20T01:05:00Z)]" abc123; run_gate
if [ "$RC" -eq 1 ] && [ "$(merges)" -eq 0 ]; then ok "dispatch_other_branch_held: held run still fails"
else fail "dispatch_other_branch_held: rc=$RC merges=$(merges)"; fi

# 23b. the latest dispatched run failed after an earlier green one: exit 1.
fixture dispatch_latest_failed "[$SUCCESS]" "[$DISP_OK,$(run_json workflow_dispatch feat completed failure 2026-10-20T02:00:00Z)]" abc123; run_gate
if [ "$RC" -eq 1 ] && [ "$(merges)" -eq 0 ]; then ok "dispatch_latest_failed: exit 1"
else fail "dispatch_latest_failed: rc=$RC merges=$(merges)"; fi

# ─── ci_status and rules_stale, called from hack/gate_lib.sh directly ─────────
lib_ci() {  # lib_ci NAME RUNS WANT: ci_status abc123 feat over the ci.yml runs RUNS
  fixture "lib_$1" '[]' "$2" abc123
  local got; got=$(PATH="$T/bin:$PATH" bash -c '. "$1"; ci_status abc123 feat' _ "$ROOT/hack/gate_lib.sh" 2>&1)
  if [ "$got" = "$3" ]; then ok "ci_status $1: $got"; else fail "ci_status $1: got '$got', want $3"; fi
}
lib_ci none '[]' none
lib_ci pr_green "[$(run_json pull_request feat completed success 2026-10-20T01:00:00Z)]" green
lib_ci dispatch_green "[$DISP_OK]" green
lib_ci held_only "[$HELD_PR]" failed
lib_ci held_plus_dispatch "[$HELD_PR,$DISP_OK]" green
lib_ci held_plus_running "[$HELD_PR,$(run_json workflow_dispatch feat in_progress '' 2026-10-20T01:05:00Z)]" pending
lib_ci dispatch_queued "[$(run_json workflow_dispatch feat queued '' 2026-10-20T01:05:00Z)]" pending
lib_ci other_branch "[$(run_json workflow_dispatch other completed success 2026-10-20T01:05:00Z)]" none
lib_ci latest_failed "[$DISP_OK,$(run_json workflow_dispatch feat completed failure 2026-10-20T02:00:00Z)]" failed
lib_ci failed_then_green "[$(run_json workflow_dispatch feat completed failure 2026-10-20T01:00:00Z),$DISP_OK]" green
lib_ci failed_then_running "[$(run_json workflow_dispatch feat completed failure 2026-10-20T01:00:00Z),$(run_json workflow_dispatch feat in_progress '' 2026-10-20T01:05:00Z)]" pending

# rules_stale keeps its behaviour: stale tree -> 0, matching tree -> 1, no tree -> 1.
lib_rules() {  # lib_rules NAME WANT_RC [current]
  fixture "librules_$1" '[]' '[]' abc123
  mkdir -p "$FIX/show/console"; cp "$ROOT/console/model_opt.py" "$ROOT/console/convert-pins.json" "$FIX/show/console/"
  printf '## Hypotheses\n\n| H34 | on the i9-13900T | p | m | SUPPORTED |\n' > "$FIX/show/EXPERIMENTS.md"
  if [ "${3:-}" = current ]; then
    python3 "$ROOT/console/model_opt.py" ledger-rules --ledger "$FIX/show/EXPERIMENTS.md" --out "$FIX/show/console/ledger-rules.json"
  else echo '{"schema": "k3d-gpu.ledger-rules/v1", "rows": []}' > "$FIX/show/console/ledger-rules.json"; fi
  [ "$1" = notree ] || echo t1 > "$FIX/tree"
  PATH="$T/bin:$PATH" bash -c '. "$1"; rules_stale abc123' _ "$ROOT/hack/gate_lib.sh" >/dev/null 2>&1; local rc=$?
  if [ "$rc" -eq "$2" ]; then ok "rules_stale $1: rc $rc"; else fail "rules_stale $1: rc $rc, want $2"; fi
}
lib_rules stale 0; lib_rules current 1 current; lib_rules notree 1

# ─── base_ok: release-only bot commits on main do not count as a moved base ────
# release_to_main AUTHOR SUBJECT [code]: one commit on main that moves the version
# in scripts/k3d-gpu's VERSION line and the AUR, install.sh and Formula files;
# with `code` it also changes another line of scripts/k3d-gpu.
release_to_main() {
  local R=$FIX/repo
  repo_git checkout -q main
  sed -i 's/^VERSION="0\.1\.0"/VERSION="0.1.1"/' "$R/scripts/k3d-gpu"
  [ "${3:-}" = code ] && sed -i 's/^CLUSTER=gpu$/CLUSTER=cpu/' "$R/scripts/k3d-gpu"
  echo 'pkgver=0.1.1' > "$R/packaging/aur/PKGBUILD"; echo 'pkgver = 0.1.1' > "$R/packaging/aur/.SRCINFO"
  repo_git add scripts packaging/aur && GIT_AUTHOR_NAME=$1 "$REALGIT" -C "$R" commit -q -m "$2" && repo_git push -q origin main
}
# install_to_main AUTHOR: the install.sh and Homebrew release commits.
install_to_main() {
  local R=$FIX/repo
  repo_git checkout -q main
  echo 'VERSION=0.1.1' > "$R/packaging/install.sh"; repo_git add packaging/install.sh
  GIT_AUTHOR_NAME=$1 "$REALGIT" -C "$R" commit -q -m "release: install.sh k3d-gpu 0.1.1 [skip release]"
  echo 'version "0.1.1"' > "$R/Formula/k3d-gpu.rb"; repo_git add Formula/k3d-gpu.rb
  GIT_AUTHOR_NAME=$1 "$REALGIT" -C "$R" commit -q -m "release: homebrew k3d-gpu 0.1.1 [skip release]"
  repo_git push -q origin main
}

# 24. the PR changes code, and main moved only by release-only bot commits: merge, no sync.
realgit_fixture release_only_base "[$SUCCESS]" "$CI_OK"
commit_to feat scripts.txt $'pr\n'
release_to_main 88plug-bot "release: aur-v0.1.1 [skip release]"; install_to_main 'github-actions[bot]'
run_gate_real
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && [ "$(syncs)" -eq 0 ] && ! grep -q 'main moved under code' <<<"$OUT"; then
  ok "release_only_base: merged, no sync"
else fail "release_only_base: rc=$RC merges=$(merges) syncs=$(syncs) out=$(tr '\n' ';' <<<"$OUT")"; fi

# 25. a release commit that also changes another launcher line is a code move: sync first.
realgit_fixture release_with_code "[$SUCCESS]" "$CI_OK"
commit_to feat scripts.txt $'pr\n'
release_to_main 88plug-bot "release: aur-v0.1.1 [skip release]" code
run_gate_real
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && [ "$(syncs)" -eq 1 ] && grep -q 'main moved under code' <<<"$OUT"; then
  ok "release_with_code: synced before the merge"
else fail "release_with_code: rc=$RC merges=$(merges) syncs=$(syncs) out=$(tr '\n' ';' <<<"$OUT")"; fi

# 25b. the same release files under a release subject, but by a human: a code move.
realgit_fixture release_by_human "[$SUCCESS]" "$CI_OK"
commit_to feat scripts.txt $'pr\n'
release_to_main gate-test "release: aur-v0.1.1 [skip release]"
run_gate_real
if [ "$RC" -eq 0 ] && [ "$(syncs)" -eq 1 ] && grep -q 'main moved under code' <<<"$OUT"; then
  ok "release_by_human: synced before the merge"
else fail "release_by_human: rc=$RC merges=$(merges) syncs=$(syncs) out=$(tr '\n' ';' <<<"$OUT")"; fi

# 25c. a bot commit on release files without [skip release] in its subject: a code move.
realgit_fixture release_no_skip "[$SUCCESS]" "$CI_OK"
commit_to feat scripts.txt $'pr\n'
release_to_main 88plug-bot "release: aur-v0.1.1"
run_gate_real
if [ "$RC" -eq 0 ] && [ "$(syncs)" -eq 1 ]; then ok "release_no_skip: synced before the merge"
else fail "release_no_skip: rc=$RC merges=$(merges) syncs=$(syncs) out=$(tr '\n' ';' <<<"$OUT")"; fi

# ─── alarms: open ci-alarm issues are printed, and refuse shipped changes ──────
UNACKED='{"number":7,"title":"[ci-alarm] nightly: Auto-update red","url":"https://github.com/o/r/issues/7","labels":[{"name":"ci-alarm"}]}'
ACKED='{"number":8,"title":"[ci-alarm] release: 0.1.79 missing on GHCR","url":"https://github.com/o/r/issues/8","labels":[{"name":"ci-alarm"},{"name":"alarm-ack"}]}'

# 26. an un-acked alarm and a PR that changes the launcher: exit 4, URL and ack command shown.
fixture alarm_unacked "[$SUCCESS]" "$CI_OK" abc123; echo "[$UNACKED]" > "$FIX/alarms_seq"; run_gate
if [ "$RC" -eq 4 ] && [ "$(merges)" -eq 0 ] && grep -qF 'https://github.com/o/r/issues/7' <<<"$OUT" \
   && grep -qF 'gh issue edit 7 --add-label alarm-ack' <<<"$OUT"; then
  ok "alarm_unacked: refused with exit 4, URL and ack command printed"
else fail "alarm_unacked: rc=$RC merges=$(merges) out=$(tr '\n' ';' <<<"$OUT")"; fi

# 26b. a workflow-only PR is refused too; so is a PR whose files cannot be listed.
fixture alarm_workflow "[$SUCCESS]" "$CI_OK" abc123; echo "[$UNACKED]" > "$FIX/alarms_seq"
echo .github/workflows/ci.yml > "$FIX/pr_paths"; run_gate
if [ "$RC" -eq 4 ] && [ "$(merges)" -eq 0 ]; then ok "alarm_workflow: refused"
else fail "alarm_workflow: rc=$RC merges=$(merges)"; fi
fixture alarm_files_unknown "[$SUCCESS]" "$CI_OK" abc123; echo "[$UNACKED]" > "$FIX/alarms_seq"; touch "$FIX/pr_paths_fail"; run_gate
if [ "$RC" -eq 4 ] && [ "$(merges)" -eq 0 ]; then ok "alarm_files_unknown: refused"
else fail "alarm_files_unknown: rc=$RC merges=$(merges)"; fi

# 26c. GATE_RELEASE_PATHS adds shipped paths (hack/release-paths format: one
#      pattern per line, # comments, dir/** for a tree).
fixture alarm_release_paths "[$SUCCESS]" "$CI_OK" abc123; echo "[$UNACKED]" > "$FIX/alarms_seq"
printf '# shipped by nfpm\noracle/**\n' > "$FIX/release-paths"; echo oracle/bundle.py > "$FIX/pr_paths"
OUT=$(PATH="$T/bin:$PATH" GATE_REPO="$FIX" GATE_WT_ROOT="$FIX/wt" GATE_POLLS=3 GATE_RELEASE_PATHS="$FIX/release-paths" \
      timeout 60 bash "$GATE" 1 subj body 2>&1); RC=$?
if [ "$RC" -eq 4 ] && [ "$(merges)" -eq 0 ]; then ok "alarm_release_paths: a listed path is refused"
else fail "alarm_release_paths: rc=$RC merges=$(merges) out=$(tr '\n' ';' <<<"$OUT")"; fi

# 26d. no alarm at the start, an un-acked one by the merge: refused before merging.
fixture alarm_opens_later "[$SUCCESS]" "$CI_OK" abc123; printf '[]\n[%s]\n' "$UNACKED" > "$FIX/alarms_seq"; run_gate
if [ "$RC" -eq 4 ] && [ "$(merges)" -eq 0 ]; then ok "alarm_opens_later: re-checked and refused before the merge"
else fail "alarm_opens_later: rc=$RC merges=$(merges) out=$(tr '\n' ';' <<<"$OUT")"; fi

# 27. every open alarm acked: listed at the start, and the PR merges.
fixture alarm_acked "[$SUCCESS]" "$CI_OK" abc123; echo "[$ACKED]" > "$FIX/alarms_seq"; run_gate
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && grep -F '#8' <<<"$OUT" | grep -qF 'https://github.com/o/r/issues/8'; then
  ok "alarm_acked: listed, merged"
else fail "alarm_acked: rc=$RC merges=$(merges) out=$(tr '\n' ';' <<<"$OUT")"; fi

# 28. ledger, docs, bench and test changes are never refused; nor is a path outside
#     the shipped set.
fixture alarm_ledger_only "[$SUCCESS]" "$CI_OK" abc123; echo "[$UNACKED,$ACKED]" > "$FIX/alarms_seq"
printf 'EXPERIMENTS.md\ndocs/provenance.md\nbench/h54/run.sh\ntests/bench_test.sh\n' > "$FIX/pr_paths"; run_gate
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && grep -qF 'issues/7' <<<"$OUT"; then ok "alarm_ledger_only: listed, merged"
else fail "alarm_ledger_only: rc=$RC merges=$(merges) out=$(tr '\n' ';' <<<"$OUT")"; fi
fixture alarm_unshipped "[$SUCCESS]" "$CI_OK" abc123; echo "[$UNACKED]" > "$FIX/alarms_seq"
echo README.md > "$FIX/pr_paths"; run_gate
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ]; then ok "alarm_unshipped: merged"
else fail "alarm_unshipped: rc=$RC merges=$(merges)"; fi

# 30. the gate copied alone (a scratchpad shim) finds gate_lib.sh in the repository's
#     hack/; with neither copy it stops before merging.
fixture solo_gate "[$SUCCESS]" "$CI_OK" abc123; mkdir -p "$FIX/solo" "$FIX/hack"
cp "$GATE" "$FIX/solo/gate_merge.sh"; cp "$ROOT/hack/gate_lib.sh" "$FIX/hack/"
OUT=$(PATH="$T/bin:$PATH" GATE_REPO="$FIX" GATE_WT_ROOT="$FIX/wt" GATE_POLLS=3 timeout 60 bash "$FIX/solo/gate_merge.sh" 1 subj body 2>&1); RC=$?
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ]; then ok "solo_gate: gate_lib.sh read from the repository's hack/"
else fail "solo_gate: rc=$RC merges=$(merges) out=$(tr '\n' ';' <<<"$OUT")"; fi
rm "$FIX/hack/gate_lib.sh"; echo OPEN > "$FIX/state"; rm -f "$FIX/merges"
OUT=$(PATH="$T/bin:$PATH" GATE_REPO="$FIX" GATE_WT_ROOT="$FIX/wt" GATE_POLLS=3 timeout 60 bash "$FIX/solo/gate_merge.sh" 1 subj body 2>&1); RC=$?
if [ "$RC" -eq 1 ] && [ "$(merges)" -eq 0 ] && grep -q 'gate_lib.sh not found' <<<"$OUT"; then ok "solo_gate: no gate_lib.sh, exit 1"
else fail "solo_gate missing lib: rc=$RC merges=$(merges) out=$(tr '\n' ';' <<<"$OUT")"; fi

# 29. issues cannot be listed (the repository has them disabled): the gate says so,
#     with gh's reason, and goes on.
fixture alarm_issues_off "[$SUCCESS]" "$CI_OK" abc123; touch "$FIX/issues_off"; run_gate
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && grep -q 'WARNING: could not list the open ci-alarm issues.*disabled issues' <<<"$OUT"; then
  ok "alarm_issues_off: warned with the reason, merged"
else fail "alarm_issues_off: rc=$RC merges=$(merges) out=$(tr '\n' ';' <<<"$OUT")"; fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
