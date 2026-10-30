#!/usr/bin/env bash
# gate_merge.sh <pr> <subject> <body>: keep the PR branch current with main
# (ledger/provenance conflicts resolved keep-both; anything else -> stop),
# then squash-merge the head SHA whose checks were read. Up to 5 sync rounds.
# The checks it reads live in hack/gate_lib.sh, which it sources from its own
# directory, else from the repository's hack/.
#
# Green means: every latest rollup entry is SUCCESS, SKIPPED or NEUTRAL (a
# check with any queued or running attempt is pending); ci.yml for that exact
# SHA succeeded, through a pull_request run or a workflow_dispatch run on the PR
# branch (the latest completed one decides); and the latest run of every other
# pull_request workflow for that SHA completed with success, skipped or neutral.
# A terminal non-green entry (FAILURE, ERROR, CANCELLED, TIMED_OUT,
# STARTUP_FAILURE, ACTION_REQUIRED, STALE), a failed workflow run or a failed
# ci.yml exits 1.
#
# Bot PRs: a PR that GITHUB_TOKEN opened or pushed gets pull_request runs held
# for approval (ACTION_REQUIRED). hack/bot-ci-dispatch runs ci.yml on its branch
# by workflow_dispatch instead; once that run is green, the held entries of 'CI'
# and 'Auto-merge Dependabot PRs' no longer block. Held runs alone still exit 1.
#
# Alarms: the gate first prints every open ci-alarm issue. While any of them
# lacks the label alarm-ack, it refuses (exit 4) a PR that changes .github/ or a
# shipped path (see gated_paths in gate_lib.sh), at the start and again right
# before merging; a PR that changes only EXPERIMENTS.md, docs/, bench/ or tests/
# is never refused. Read the alarm, then ack it: gh issue edit N --add-label alarm-ack.
#
# Derived file: console/ledger-rules.json is EXPERIMENTS.md's rule rows as packages
# ship them. A ledger-only PR (or base) merges without a CI re-run, so the gate
# itself checks the merged tree: when the file there is not what EXPERIMENTS.md
# generates, it syncs, and every sync regenerates the file and commits it.
#
# Moved base: if main is no longer an ancestor of the head, and both main
# (since the merge base) and the PR change files other than EXPERIMENTS.md and
# docs/provenance.md, the gate syncs the branch so CI re-runs on the merged
# head. A base that moved only in the ledger or by release-only bot commits
# ('release: ... [skip release]' on the release files), or under a ledger-only
# PR, is merged without a re-run. Another PR can still land between this check
# and the merge; that window stays open.
#
# The merge carries --match-head-commit, so GitHub merges only if the head is
# still that SHA. On a refusal the gate prints GitHub's reason and re-reads the
# head: if it moved, the next round re-reads its checks; if not, the gate
# sleeps, and the next round re-checks mergeable (sync on a conflict) and the
# base before merging again.
#
# Exit: 0 merged, 1 failed or gave up, 2 gh lacks --match-head-commit, 4 refused
# for an open alarm.
# Env: GATE_REPO (default: the caller's git toplevel), GATE_WT_ROOT (sync
# worktree parent, default $TMPDIR), GATE_POLLS (polls per round, default 60),
# GATE_RELEASE_PATHS (shipped-path patterns, default hack/release-paths),
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
for lib in "$HERE/gate_lib.sh" hack/gate_lib.sh ""; do [ -f "$lib" ] && break; done
[ -n "$lib" ] || { echo "gate_lib.sh not found next to $0 or in $PWD/hack; copy it beside the gate"; exit 1; }
# shellcheck source=hack/gate_lib.sh
. "$lib"
gh pr merge --help 2>/dev/null | grep -q -- --match-head-commit \
  || { echo "gh pr merge lacks --match-head-commit; refusing to merge unpinned"; exit 2; }
br=$(gh pr view "$pr" --json headRefName -q .headRefName) || exit 1
# alarm_check [list]: with `list`, print every open ci-alarm issue. Exit 4 when one
# is not acked and the PR changes a gated path (or its paths cannot be read).
alarm_check() {
  local a un paths gated e
  e=$(mktemp)
  if ! a=$(open_alarms 2>"$e"); then
    echo "WARNING: could not list the open ci-alarm issues ($(tr '\n' ' ' < "$e" | sed 's/ *$//')); the alarm check was skipped"
    rm -f "$e"; return 0
  fi
  rm -f "$e"
  [ "${1:-}" = list ] && jq -r '.[] | "open ci-alarm #\(.number): \(.title) \(.url)\(if any(.labels[]; .name == "alarm-ack") then " (acked)" else " (NOT acked)" end)"' <<<"$a"
  un=$(jq -r '.[] | select(any(.labels[]; .name == "alarm-ack") | not) | "  #\(.number) \(.title) \(.url)"' <<<"$a")
  [ -n "$un" ] || return 0
  if paths=$(gh api --paginate "repos/{owner}/{repo}/pulls/$pr/files" -q '.[].filename' 2>/dev/null); then
    gated=$(gated_paths <<<"$paths")
    [ -n "$gated" ] || return 0
    echo "PR$pr REFUSED: it changes $(head -5 <<<"$gated" | paste -sd, -)$([ "$(wc -l <<<"$gated")" -gt 5 ] && echo ', ...') while these ci-alarm issues are open and not acked:"
  else
    echo "PR$pr REFUSED: its changed files could not be listed, and these ci-alarm issues are open and not acked:"
  fi
  echo "$un"
  jq -r '.[] | select(any(.labels[]; .name == "alarm-ack") | not) | "Read #\(.number), then ack it: gh issue edit \(.number) --add-label alarm-ack"' <<<"$a"
  exit 4
}
alarm_check list
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
[ "$(gh pr view "$pr" --json isDraft -q .isDraft)" = true ] && gh pr ready "$pr" >/dev/null
merged() { [ "$(gh pr view "$pr" --json state -q .state)" = MERGED ]; }
for round in 1 2 3 4 5; do
  merged && { echo "PR$pr MERGED"; exit 0; }
  [ "$(gh pr view "$pr" --json mergeable -q .mergeable)" = CONFLICTING ] && { sync || exit 1; sleep 60; }
  for _ in $(seq 1 "${GATE_POLLS:-60}"); do
    pj=$(gh pr view "$pr" --json headRefOid,statusCheckRollup 2>/dev/null) && [ -n "$pj" ] || { sleep 60; continue; }
    sha=$(jq -r .headRefOid <<<"$pj")
    read -r ci held_ok ci_last <<<"$(ci_eval "$sha" "$br")"
    read -r sha g b n bad <<<"$(jq -r --argjson held_ok "${held_ok:-false}" "$READ" <<<"$pj")"
    [ "$b" -gt 0 ] && { echo "PR$pr FAIL at $sha: $bad"; exit 1; }
    if [ "$n" -gt 0 ] && [ "$g" -eq "$n" ]; then
      case $ci in
        green)
          read -r rs rbad <<<"$(gh run list -c "$sha" -e pull_request -L 100 \
               --json workflowName,status,conclusion,createdAt 2>/dev/null | jq -r --argjson held_ok "$held_ok" "$RUNS" 2>/dev/null)"
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
          alarm_check
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
        failed) echo "PR$pr FAIL: ci.yml for $sha did not succeed (latest $ci_last)"; exit 1;;
      esac   # no ci.yml run for this SHA yet, or still running: wait
    fi
    sleep 60
  done
done; echo "PR$pr GAVE UP"; exit 1
