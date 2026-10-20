#!/usr/bin/env bash
# gate_merge.sh <pr> <subject> <body>: keep the PR branch current with main
# (ledger/provenance conflicts resolved keep-both; anything else -> stop),
# then squash-merge the head SHA whose checks were read. Up to 5 sync rounds.
#
# Green means every latest rollup entry is SUCCESS, SKIPPED or NEUTRAL, and the
# ci.yml pull_request run for that exact SHA completed with success. A
# terminal non-green entry (FAILURE, ERROR, CANCELLED, TIMED_OUT,
# STARTUP_FAILURE, ACTION_REQUIRED, STALE) or a failed ci.yml run exits 1.
# The merge carries --match-head-commit, so GitHub merges only if the head is
# still that SHA; a refused merge resyncs and re-reads. A base that moved but
# still merges cleanly is merged without a re-run of CI against it.
#
# Env: GATE_REPO (default: the caller's git toplevel), GATE_WT_ROOT (sync
# worktree parent, default $TMPDIR), GATE_POLLS (polls per round, default 60),
# GATE_SELF_DIR (a directory removed on exit; set by the scratchpad shim).
set -u; pr=$1; subj=$2; body=$3
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
KB=$HERE/merge_keep_both.py
W=${GATE_WT_ROOT:-${TMPDIR:-/tmp}}/gm-$pr
cd "${GATE_REPO:-$(git rev-parse --show-toplevel)}" || exit 1
cleanup() {
  git worktree remove --force "$W" 2>/dev/null; git branch -D "gm/$pr" >/dev/null 2>&1
  if [ -n "${GATE_SELF_DIR:-}" ]; then rm -rf "$GATE_SELF_DIR"; fi
}
trap cleanup EXIT
gh pr merge --help 2>/dev/null | grep -q -- --match-head-commit \
  || { echo "gh pr merge lacks --match-head-commit; refusing to merge unpinned"; exit 2; }
br=$(gh pr view "$pr" --json headRefName -q .headRefName) || exit 1
sync() {
  git fetch -q origin
  git worktree remove --force "$W" 2>/dev/null; git worktree add -q -B "gm/$pr" "$W" "origin/$br" || return 1
  ( cd "$W" || exit 1
    git merge -q --no-edit origin/main >/dev/null 2>&1 && exit 0
    for f in $(git diff --name-only --diff-filter=U); do
      case $f in
        EXPERIMENTS.md|docs/provenance.md) python3 "${KB}" "$f" >/dev/null && git add "$f";;
        tests/bench_test.sh) python3 "${KB}" "$f" >/dev/null && bash -n "$f" && git add "$f" || { echo "bench_test resolve failed"; git merge --abort; exit 1; };;
        *) echo "MANUAL conflict $f"; git merge --abort; exit 1;; esac
    done
    bash tests/claims_check.sh >/dev/null 2>&1 || { echo "claims_check failed after merge"; exit 1; }
    git commit -q --no-edit ) || return 1
  git -C "$W" push -q origin "HEAD:refs/heads/$br" && echo "synced $br"
}
# One snapshot: "<sha> <green> <terminal> <total> <non-green names>", latest run per check.
# An in-progress CheckRun reports conclusion "", a legacy StatusContext only .state.
READ='.headRefOid as $sha
  | [.statusCheckRollup | group_by([.__typename, (.name // .context), (.workflowName // "")])[]
     | max_by(.startedAt // "")
     | {n: (.name // .context),
        v: ((if (.conclusion // "") != "" then .conclusion else (.state // "PENDING") end) | ascii_upcase)}] as $c
  | ($c | map(select(.v == "SUCCESS" or .v == "SKIPPED" or .v == "NEUTRAL")) | length) as $g
  | ($c | map(select(.v | IN("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED", "STALE")))) as $b
  | "\($sha) \($g) \($b | length) \($c | length) \($b | map("\(.n)=\(.v)") | join(","))"'
[ "$(gh pr view "$pr" --json isDraft -q .isDraft)" = true ] && gh pr ready "$pr" >/dev/null
merged() { [ "$(gh pr view "$pr" --json state -q .state)" = MERGED ]; }
for round in 1 2 3 4 5; do
  merged && { echo "PR$pr MERGED"; exit 0; }
  [ "$(gh pr view "$pr" --json mergeable -q .mergeable)" = CONFLICTING ] && { sync || exit 1; sleep 60; }
  for _ in $(seq 1 "${GATE_POLLS:-60}"); do
    s=$(gh pr view "$pr" --json headRefOid,statusCheckRollup -q "$READ" 2>/dev/null) || { sleep 60; continue; }
    read -r sha g b n bad <<<"$s"
    [ "$b" -gt 0 ] && { echo "PR$pr FAIL at $sha: $bad"; exit 1; }
    if [ "$n" -gt 0 ] && [ "$g" -eq "$n" ]; then
      ci=$(gh run list -w ci.yml -c "$sha" -e pull_request -L 1 --json status,conclusion \
           -q '.[0] | "\(.status)/\(.conclusion)"' 2>/dev/null)
      case $ci in
        completed/success)
          gh pr merge "$pr" --squash --match-head-commit "$sha" --subject "$subj" --body "$body" >/dev/null 2>&1
          merged && { git push -q origin --delete "$br" 2>/dev/null; echo "PR$pr MERGED at $sha"; exit 0; }
          echo "PR$pr round $round: merge at $sha refused (head moved or new conflict): resync"
          break;;
        completed/*) echo "PR$pr FAIL: ci.yml run for $sha is $ci"; exit 1;;
      esac   # no ci.yml run for this SHA yet, or still running: wait
    fi
    sleep 60
  done
done; echo "PR$pr GAVE UP"; exit 1
