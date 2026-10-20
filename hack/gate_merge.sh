#!/usr/bin/env bash
# gate_merge.sh <pr> <subject> <body>: keep the PR branch current with main
# (ledger/provenance conflicts resolved keep-both; anything else -> stop),
# then squash-merge only at zero fail + zero pending. Up to 5 sync rounds.
set -u; pr=$1; subj=$2; body=$3
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "${GATE_REPO:-$(git rev-parse --show-toplevel)}" || exit 1
br=$(gh pr view "$pr" --json headRefName -q .headRefName) || exit 1
KB=$HERE/merge_keep_both.py
W=${GATE_WT_ROOT:-${TMPDIR:-/tmp}}/gm-$pr
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
[ "$(gh pr view "$pr" --json isDraft -q .isDraft)" = true ] && gh pr ready "$pr" >/dev/null
merged() { [ "$(gh pr view "$pr" --json state -q .state)" = MERGED ]; }
for round in 1 2 3 4 5; do
  merged && { echo "PR$pr MERGED"; exit 0; }
  [ "$(gh pr view "$pr" --json mergeable -q .mergeable)" = CONFLICTING ] && { sync || exit 1; sleep 60; }
  for _ in $(seq 1 60); do
    j=$(gh pr checks "$pr" --json bucket 2>/dev/null) || { sleep 60; continue; }
    f=$(jq '[.[]|select(.bucket=="fail")]|length' <<<"$j"); p=$(jq '[.[]|select(.bucket=="pending")]|length' <<<"$j")
    [ "$f" -gt 0 ] && { echo "PR$pr FAIL=$f"; gh pr checks "$pr" | grep -i fail; exit 1; }
    if [ "$p" -eq 0 ] && [ "$(jq length <<<"$j")" -gt 0 ]; then
      git worktree remove --force "$W" 2>/dev/null; git branch -D "gm/$pr" >/dev/null 2>&1
      gh pr merge "$pr" --squash --subject "$subj" --body "$body" >/dev/null 2>&1
      merged && { git push -q origin --delete "$br" 2>/dev/null; echo "PR$pr MERGED"; exit 0; }
      break   # merge refused (likely new conflict): resync
    fi
    sleep 60
  done
done; echo "PR$pr GAVE UP"; exit 1
