# shellcheck shell=bash
# gate_lib.sh: what the merge gate (hack/gate_merge.sh) reads to decide a PR is
# green and current, for sourcing. Run from inside the repository; gh and git do
# the reading.
#
#   READ               jq program over `gh pr view --json headRefOid,statusCheckRollup`
#                      (takes --argjson held_ok BOOL): "<sha> <green> <terminal> <total>
#                      <non-green names>", the latest entry per check.
#   RUNS               jq program over `gh run list -c SHA -e pull_request --json
#                      workflowName,status,conclusion,createdAt` (takes --argjson
#                      held_ok BOOL): "<green|pending|failed> <bad names>", the latest
#                      run per workflow.
#   ci_status SHA BR   green | pending | failed | none: ci.yml at SHA, through a
#                      pull_request run or a workflow_dispatch run on branch BR.
#   ci_eval SHA BR     "<ci_status> <held_ok> <latest run>", held_ok as below.
#   base_ok SHA        main is an ancestor of SHA, or main moved only in the ledger or
#                      by release-only bot commits, or SHA changes only the ledger.
#   rules_stale SHA    the tree main + SHA would merge to carries a
#                      console/ledger-rules.json its EXPERIMENTS.md does not generate.
#   open_alarms        the open ci-alarm issues as a JSON array (gh issue list).
#   gated_paths        reads paths on stdin, prints those a merge must not ship while
#                      an alarm is open and not acked (see below).
#
# held_ok: a PR that GITHUB_TOKEN opened or pushed gets pull_request runs held for
# approval (conclusion action_required, no jobs). hack/bot-ci-dispatch runs ci.yml
# on the PR branch by workflow_dispatch instead. Once the latest such dispatched
# run at the SHA succeeded, a held entry of workflow 'CI' or 'Auto-merge Dependabot
# PRs' no longer counts as terminal (READ) or failed (RUNS). Without it, a held
# entry stays terminal, as for a fork PR.

GATE_LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# The workflows whose held pull_request runs a green dispatched ci.yml run stands in for.
GATE_HELD_WORKFLOWS='["CI", "Auto-merge Dependabot PRs"]'

# One snapshot: "<sha> <green> <terminal> <total> <non-green names>", latest run per check.
# An in-progress CheckRun reports conclusion "", a legacy StatusContext only .state.
# READ and RUNS are jq programs ($names are jq's) for the gate that sources this file.
# shellcheck disable=SC2016,SC2034
READ='def green: IN("SUCCESS", "SKIPPED", "NEUTRAL");
  def terminal: IN("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED", "STALE");
  ('"$GATE_HELD_WORKFLOWS"') as $held
  | .headRefOid as $sha
  | [.statusCheckRollup[]
     | {k: [.__typename, (.name // .context), (.workflowName // "")], t: (.startedAt // ""), n: (.name // .context),
        w: (.workflowName // ""),
        v: ((if (.conclusion // "") != "" then .conclusion else (.state // "PENDING") end) | ascii_upcase)}
     | select(($held_ok and .v == "ACTION_REQUIRED" and (.w | IN($held[]))) | not)]
  | [group_by(.k)[] | if any(.v | (green or terminal) | not) then .[0] + {v: "PENDING"} else max_by(.t) end] as $c
  | ($c | map(select(.v | green)) | length) as $g
  | ($c | map(select(.v | terminal))) as $b
  | "\($sha) \($g) \($b | length) \($c | length) \($b | map("\(.n)=\(.v)") | join(","))"'
# Every pull_request workflow run for a SHA, latest per workflow: "<state> <bad names>",
# state = green | pending | failed.
# shellcheck disable=SC2016,SC2034
RUNS='('"$GATE_HELD_WORKFLOWS"') as $held
  | [.[] | select(($held_ok and .conclusion == "action_required" and (.workflowName | IN($held[]))) | not)]
  | [group_by(.workflowName)[] | max_by(.createdAt // "")] as $r
  | ($r | map(select(.status != "completed"))) as $p
  | ($r | map(select(.status == "completed" and (.conclusion | IN("success", "skipped", "neutral") | not)))) as $f
  | if ($f | length) > 0 then "failed \($f | map("\(.workflowName)=\(.conclusion)") | join(","))"
    elif ($p | length) > 0 then "pending -" else "green -" end'
# ci.yml runs at a SHA: $p its pull_request runs, $d its workflow_dispatch runs, $b the
# PR branch. Held runs (action_required) are not attempts: the latest completed other
# run decides, a queued or running one means pending, and held runs alone are failed.
# shellcheck disable=SC2016
CI_EVAL='def ok: .conclusion == "success";
  def word: if . == null or . == "" then "-" else . end;
  ($p | map(. + {via: "pull_request"})) + ($d | map(select(.headBranch == $b) | . + {via: "workflow_dispatch"})) as $all
  | ($all | map(select(.conclusion != "action_required"))) as $e
  | ($e | map(select(.status == "completed")) | max_by(.createdAt // "")) as $last
  | ($e | map(select(.via == "workflow_dispatch" and .status == "completed")) | max_by(.createdAt // "")) as $dl
  | (if $last != null and ($last | ok) then "green"
     elif any($e[]; .status != "completed") then "pending"
     elif $last != null or ($all | length) > 0 then "failed"
     else "none" end) as $s
  | ($s == "green" and $dl != null and ($dl | ok)) as $held_ok
  | ($last // ($all | max_by(.createdAt // "")) // {}) as $show
  | "\($s) \($held_ok) \($show.via | word):\($show.status | word)/\($show.conclusion | word)"'
ci_eval() {
  local p d
  p=$(gh run list -w ci.yml -c "$1" -e pull_request -L 100 --json event,headBranch,status,conclusion,createdAt 2>/dev/null) || p=
  d=$(gh run list -w ci.yml -c "$1" -e workflow_dispatch -b "$2" -L 100 --json event,headBranch,status,conclusion,createdAt 2>/dev/null) || d=
  jq -rn --arg b "$2" --argjson p "${p:-[]}" --argjson d "${d:-[]}" "$CI_EVAL"
}
ci_status() { local s; read -r s _ <<<"$(ci_eval "$1" "$2")"; echo "${s:-none}"; }

# base_ok SHA: main is an ancestor of SHA, or main moved only in the ledger (or by
# release-only bot commits), or the PR is ledger-only.
code() { git diff --name-only "$1" "$2" | grep -qvxE 'EXPERIMENTS\.md|docs/provenance\.md'; }
# release_only C: C is one of the release commits auto-release.yml and its followers
# push to main: a 'release: ... [skip release]' subject by 88plug-bot or
# github-actions[bot], touching only the release files, and in scripts/k3d-gpu only
# its VERSION="..." line.
release_only() {
  local c=$1 f
  case $(git log -1 --format=%s "$c") in 'release:'*'[skip release]'*) ;; *) return 1;; esac
  case $(git log -1 --format=%an "$c") in 88plug-bot|'github-actions[bot]') ;; *) return 1;; esac
  while IFS= read -r f; do
    case $f in
      packaging/aur/PKGBUILD|packaging/aur/.SRCINFO|Formula/k3d-gpu.rb|packaging/install.sh|console/ledger-rules.json) ;;
      scripts/k3d-gpu)
        git diff -U0 "$c^" "$c" -- scripts/k3d-gpu \
          | awk '/^@@/ {h = 1; next} h && /^[-+]/ && !/^[-+]VERSION="/ {bad = 1} END {exit bad ? 0 : 1}' && return 1;;
      *) return 1;;
    esac
  done < <(git diff --name-only "$c^" "$c")
  return 0
}
# main_code MB: since MB, main changed a file outside the ledger in a commit that is
# not release-only. When no commit can be listed, that counts as a change.
main_code() {
  code "$1" origin/main || return 1
  local c n=0
  while read -r c; do
    n=$((n + 1))
    release_only "$c" && continue
    code "$c^" "$c" && return 0
  done < <(git rev-list "$1..origin/main")
  [ "$n" -eq 0 ]
}
base_ok() {
  git fetch -q origin || return 1
  git merge-base --is-ancestor origin/main "$1" && return 0
  local mb; mb=$(git merge-base origin/main "$1") || return 1
  ! { main_code "$mb" && code "$mb" "$1"; }
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

# open_alarms: the open ci-alarm issues, [{number, title, url, labels}]; fails when gh does.
open_alarms() {
  local a
  a=$(gh issue list --label ci-alarm --state open --limit 100 --json number,title,url,labels) || return 1
  jq -c 'if type == "array" then . else error("not an array") end' <<<"${a:-[]}"
}
# gated_paths: of the paths on stdin, print those a merge must not ship while an
# alarm is open and not acked: .github/** and the shipped set. A PR that changes
# only EXPERIMENTS.md, docs/, bench/ or tests/ (the ledger work) prints nothing.
# Shipped set: packaging/, scripts/, share/, Dockerfile, console/, images.lock, plus
# every pattern in hack/release-paths (one per line, # comments, dir/ or dir/** for
# a tree, else a shell glob; an executable file is run for its lines). It is read
# from $GATE_RELEASE_PATHS, else next to this file, else the repository's hack/.
gated_paths() {
  local paths p f pats=(packaging/ scripts/ share/ Dockerfile console/ images.lock) rp
  paths=$(grep -v '^$')
  [ -n "$paths" ] || return 0
  grep -qvE '^(EXPERIMENTS\.md|(docs|bench|tests)/.*)$' <<<"$paths" || return 0
  rp=${GATE_RELEASE_PATHS:-}
  [ -n "$rp" ] || for f in "$GATE_LIB_DIR/release-paths" hack/release-paths; do [ -f "$f" ] && { rp=$f; break; }; done
  if [ -n "$rp" ] && [ -f "$rp" ]; then
    while IFS= read -r p; do
      p=${p%%#*}; p=${p#"${p%%[![:space:]]*}"}; p=${p%"${p##*[![:space:]]}"}; p=${p#- }; p=${p//[\"\']/}
      [ -n "$p" ] && pats+=("$p")
    done < <(if [ -x "$rp" ]; then "$rp"; else cat "$rp"; fi)
  fi
  while IFS= read -r f; do
    case $f in .github/*) echo "$f"; continue;; esac
    for p in "${pats[@]}"; do
      case $p in */\*\*) p=${p%\*\*};; esac
      # shellcheck disable=SC2254  # p is a glob on purpose
      case $p in
        */) case $f in "$p"*) echo "$f"; break;; esac;;
        *) case $f in $p|"$p"/*) echo "$f"; break;; esac;;
      esac
    done
  done <<<"$paths"
}
