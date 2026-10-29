#!/usr/bin/env bash
# gate_merge.sh <pr> <subject> <body>: keep the PR branch current with main
# (ledger/provenance conflicts resolved keep-both; anything else -> stop),
# then squash-merge the head SHA whose checks were read. Up to 5 sync rounds.
#
# Green means: every latest rollup entry is SUCCESS, SKIPPED or NEUTRAL (a
# check with any queued or running attempt is pending); the ci.yml
# pull_request run for that exact SHA completed with success; and the latest
# run of every other pull_request workflow for that SHA completed with
# success, skipped or neutral. A terminal non-green entry (FAILURE, ERROR,
# CANCELLED, TIMED_OUT, STARTUP_FAILURE, ACTION_REQUIRED, STALE) or a failed
# workflow run exits 1.
#
# Derived file: console/ledger-rules.json is EXPERIMENTS.md's rule rows as packages
# ship them. A ledger-only PR (or base) merges without a CI re-run, so the gate
# itself checks the merged tree: when the file there is not what EXPERIMENTS.md
# generates, it syncs, and every sync regenerates the file and commits it.
#
# Moved base: if main is no longer an ancestor of the head, and both main
# (since the merge base) and the PR change files other than EXPERIMENTS.md and
# docs/provenance.md, the gate syncs the branch so CI re-runs on the merged
# head. A base that moved only in the ledger, or under a ledger-only PR, is
# merged without a re-run. Another PR can still land between this check and
# the merge; that window stays open.
#
# The merge carries --match-head-commit, so GitHub merges only if the head is
# still that SHA. On a refusal the gate prints GitHub's reason and re-reads the
# head: if it moved, the next round re-reads its checks; if not, the gate
# sleeps, and the next round re-checks mergeable (sync on a conflict) and the
# base before merging again.
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
    if ! git merge -q --no-edit origin/main >/dev/null 2>&1; then
      for f in $(git diff --name-only --diff-filter=U); do
        case $f in
          EXPERIMENTS.md|docs/provenance.md) python3 "${KB}" "$f" >/dev/null && git add "$f";;
          tests/bench_test.sh) python3 "${KB}" "$f" >/dev/null && bash -n "$f" && git add "$f" || { echo "bench_test resolve failed"; git merge --abort; exit 1; };;
          *) echo "MANUAL conflict $f"; git merge --abort; exit 1;; esac
      done
      bash tests/claims_check.sh >/dev/null 2>&1 || { echo "claims_check failed after merge"; exit 1; }
      git commit -q --no-edit || { echo "merge commit failed"; git merge --abort; exit 1; }
    fi
    if [ -f console/model_opt.py ] && [ -f console/ledger-rules.json ]; then
      python3 console/model_opt.py ledger-rules --ledger EXPERIMENTS.md --out console/ledger-rules.json \
        || { echo "ledger-rules regeneration failed"; exit 1; }
      if ! git diff --quiet -- console/ledger-rules.json; then
        git add console/ledger-rules.json
        git commit -q -m "console/ledger-rules.json: regenerate from EXPERIMENTS.md" \
          || { echo "ledger-rules commit failed"; exit 1; }
      fi
    fi ) || return 1
  git -C "$W" push -q origin "HEAD:refs/heads/$br" && echo "synced $br"
}
# One snapshot: "<sha> <green> <terminal> <total> <non-green names>", latest run per check.
# An in-progress CheckRun reports conclusion "", a legacy StatusContext only .state.
READ='def green: IN("SUCCESS", "SKIPPED", "NEUTRAL");
  def terminal: IN("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED", "STALE");
  .headRefOid as $sha
  | [.statusCheckRollup[]
     | {k: [.__typename, (.name // .context), (.workflowName // "")], t: (.startedAt // ""), n: (.name // .context),
        v: ((if (.conclusion // "") != "" then .conclusion else (.state // "PENDING") end) | ascii_upcase)}]
  | [group_by(.k)[] | if any(.v | (green or terminal) | not) then .[0] + {v: "PENDING"} else max_by(.t) end] as $c
  | ($c | map(select(.v | green)) | length) as $g
  | ($c | map(select(.v | terminal))) as $b
  | "\($sha) \($g) \($b | length) \($c | length) \($b | map("\(.n)=\(.v)") | join(","))"'
# Every pull_request workflow run for a SHA, latest per workflow: "<state> <bad names>",
# state = green | pending | failed.
RUNS='[group_by(.workflowName)[] | max_by(.createdAt // "")] as $r
  | ($r | map(select(.status != "completed"))) as $p
  | ($r | map(select(.status == "completed" and (.conclusion | IN("success", "skipped", "neutral") | not)))) as $f
  | if ($f | length) > 0 then "failed \($f | map("\(.workflowName)=\(.conclusion)") | join(","))"
    elif ($p | length) > 0 then "pending -" else "green -" end'
# base_ok SHA: main is an ancestor of SHA, or main moved only in the ledger, or the PR is ledger-only.
code() { git diff --name-only "$1" "$2" | grep -qvxE 'EXPERIMENTS\.md|docs/provenance\.md'; }
base_ok() {
  git fetch -q origin || return 1
  git merge-base --is-ancestor origin/main "$1" && return 0
  local mb; mb=$(git merge-base origin/main "$1") || return 1
  ! { code "$mb" origin/main && code "$mb" "$1"; }
}
# rules_stale SHA: the tree main + SHA would merge to carries a console/ledger-rules.json
# that EXPERIMENTS.md there does not generate (read with that tree's own model_opt.py).
# Unknown (a conflict, no such files) is not stale: a conflict syncs anyway.
rules_stale() {
  local tree d rc=1
  tree=$(git merge-tree --write-tree origin/main "$1" 2>/dev/null | head -1)
  [ -n "$tree" ] || return 1
  d=$(mktemp -d)
  if git show "$tree:console/model_opt.py" > "$d/model_opt.py" 2>/dev/null \
     && git show "$tree:console/convert-pins.json" > "$d/convert-pins.json" 2>/dev/null \
     && git show "$tree:console/ledger-rules.json" > "$d/have.json" 2>/dev/null \
     && git show "$tree:EXPERIMENTS.md" > "$d/EXPERIMENTS.md" 2>/dev/null \
     && [ -s "$d/model_opt.py" ] && [ -s "$d/have.json" ] \
     && python3 "$d/model_opt.py" ledger-rules --ledger "$d/EXPERIMENTS.md" --out "$d/want.json" >/dev/null 2>&1 \
     && ! cmp -s "$d/have.json" "$d/want.json"; then
    rc=0
  fi
  rm -rf "$d"
  return "$rc"
}
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
          read -r rs rbad <<<"$(gh run list -c "$sha" -e pull_request -L 100 \
               --json workflowName,status,conclusion,createdAt -q "$RUNS" 2>/dev/null)"
          case $rs in
            failed) echo "PR$pr FAIL: workflow runs for $sha: $rbad"; exit 1;;
            green) ;;
            *) sleep 60; continue;;
          esac
          if ! base_ok "$sha"; then
            echo "PR$pr round $round: main moved under code since $sha was tested: sync to re-run CI"
            sync || exit 1; sleep 60; break
          fi
          if rules_stale "$sha"; then
            echo "PR$pr round $round: console/ledger-rules.json would not match EXPERIMENTS.md after the merge: sync regenerates it"
            sync || exit 1; sleep 60; break
          fi
          why=$(gh pr merge "$pr" --squash --match-head-commit "$sha" --subject "$subj" --body "$body" 2>&1 >/dev/null)
          merged && { git push -q origin --delete "$br" 2>/dev/null; echo "PR$pr MERGED at $sha"; exit 0; }
          now=$(gh pr view "$pr" --json headRefOid -q .headRefOid 2>/dev/null)
          if [ "$now" != "$sha" ]; then
            echo "PR$pr round $round: merge at $sha refused, head moved to ${now:-?}: re-read"
          else
            echo "PR$pr round $round: merge at $sha refused: $(tr '\n' ' ' <<<"${why:-no reason given}"): retry after a pause"
            sleep 60
          fi
          break;;
        completed/*) echo "PR$pr FAIL: ci.yml run for $sha is $ci"; exit 1;;
      esac   # no ci.yml run for this SHA yet, or still running: wait
    fi
    sleep 60
  done
done; echo "PR$pr GAVE UP"; exit 1
