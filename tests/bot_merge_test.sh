#!/usr/bin/env bash
# hack/bot-merge against a fake gh (tests/bot_merge_fake_gh.py) and real git.
# Each case builds a fixture: a bare origin whose main holds the ledger files
# rules_stale reads (console/model_opt.py, EXPERIMENTS.md, the ledger-rules.json
# it generates), scripts/k3d-gpu and tag aur-v0.1.79; a clone ($FIX/repo) on main
# whose hack/ holds bot-merge, gate_lib.sh and a fixture `pins` that prints
# $FIX/policy/<head>.json; and the fake GitHub's state under $FIX/gh (pulls,
# files, reviews, comments, workflow runs, canary artifacts, alarm issues). A
# successful fake merge writes the squash commit onto origin's main.
#
# Usage: tests/bot_merge_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BOT_MERGE=${BOT_MERGE:-${ROOT}/hack/bot-merge}
GATE_LIB=${GATE_LIB:-${ROOT}/hack/gate_lib.sh}
T=$(mktemp -d "${TMPDIR:-/tmp}/bot_merge_test.XXXXXX")
trap '[ -n "${KEEP:-}" ] || rm -rf "$T"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
mkdir -p "$T/bin"
cp "$ROOT/tests/bot_merge_fake_gh.py" "$T/bin/gh"; chmod +x "$T/bin/gh"

NOW=$(date -u -d 2026-10-29T14:13:00Z +%s)    # the pass runs at 14:13 UTC
iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
ago() { iso $((NOW - $1 * 60)); }             # ago MINUTES: an ISO time before the pass

# --- fixture builders ------------------------------------------------------
G() { echo "$FIX/gh/$1"; }
jqi() { local f; f=$(G "$1"); shift; jq "$@" "$f" > "$f.tmp" && mv "$f.tmp" "$f"; }
rgit() { git -C "$FIX/repo" "$@"; }
# commit_at MINUTES_AGO MSG [AUTHOR]: commit the clone's staged changes at that time.
commit_at() {
  local when; when="$(( NOW - $1 * 60 )) +0000"
  GIT_AUTHOR_DATE=$when GIT_COMMITTER_DATE=$when GIT_AUTHOR_NAME=${3:-t} \
    git -C "$FIX/repo" commit -q -m "$2"
}

# fx NAME [TAG_MINUTES_AGO]: fresh fixture; main's base commit (tagged aur-v0.1.79)
# was made TAG_MINUTES_AGO (default 60) and its ci.yml push run succeeded.
fx() {
  FIX=$T/$1; export FIX
  mkdir -p "$FIX/gh/files" "$FIX/gh/reviews" "$FIX/gh/comments" "$FIX/policy"
  echo '[]' > "$(G pulls.json)"; echo '[]' > "$(G runs.json)"; echo '[]' > "$(G issues.json)"
  : > "$(G dispatches)"; : > "$(G merges)"; : > "$(G labels)"; : > "$(G calls)"
  RUNID=1000
  git init -q --bare -b main "$FIX/origin.git"; git init -q -b main "$FIX/repo"
  local R=$FIX/repo
  mkdir -p "$R/console" "$R/scripts" "$R/docs" "$R/hack"
  cp "$ROOT/console/model_opt.py" "$ROOT/console/convert-pins.json" "$R/console/"
  printf '## Hypotheses\n\n| H34 | on the i9-13900T | p | m | SUPPORTED |\n' > "$R/EXPERIMENTS.md"
  python3 "$R/console/model_opt.py" ledger-rules --ledger "$R/EXPERIMENTS.md" --out "$R/console/ledger-rules.json"
  printf '#!/bin/sh\nVERSION="0.1.79"\n' > "$R/scripts/k3d-gpu"
  echo base > "$R/docs/provenance.md"; echo 'k3s v1.35.0' > "$R/pins.txt"
  rgit add console scripts docs EXPERIMENTS.md pins.txt
  commit_at "${2:-60}" base
  rgit tag aur-v0.1.79
  rgit remote add origin "$FIX/origin.git"; rgit push -q origin main aur-v0.1.79; rgit fetch -q origin
  # the code under test, plus the fixture policy (untracked in the clone)
  cp "$BOT_MERGE" "$R/hack/bot-merge"; cp "$GATE_LIB" "$R/hack/gate_lib.sh"
  cat > "$R/hack/pins" <<'PINS'
#!/usr/bin/env bash
# fixture: `pins policy BASE HEAD` prints $FIX/policy/HEAD.json once both refs resolve
[ "$1" = policy ] || exit 2
echo "pins $*" >> "$FIX/gh/trace"
git rev-parse -q --verify "$2^{commit}" >/dev/null || { echo "pins: no base $2" >&2; exit 1; }
git cat-file -e "$3^{commit}" 2>/dev/null || { echo "pins: head $3 not fetched" >&2; exit 1; }
cat "$FIX/policy/$3.json"
PINS
  chmod +x "$R/hack/pins" "$R/hack/bot-merge"
  run ci.yml push "$(rgit rev-parse HEAD)" main completed success "$(ago "${2:-60}")"
}

wfname() { case $1 in ci.yml) echo CI;; canary.yml) echo Canary;; freshness.yml) echo Freshness;;
  auto-release.yml) echo 'Auto-release AUR';; build-and-push.yml) echo 'Auto-update, Build & Release';; *) echo "$1";; esac; }
# run WORKFLOW EVENT SHA BRANCH STATUS CONCLUSION [CREATED] [TITLE]: add a workflow run; sets LASTRUN.
run() {
  RUNID=$((RUNID + 1)); LASTRUN=$RUNID
  jqi runs.json --argjson id "$RUNID" --arg wf "$1" --arg name "$(wfname "$1")" --arg ev "$2" --arg sha "$3" \
    --arg br "$4" --arg st "$5" --arg c "$6" --arg at "${7:-$(ago 30)}" --arg t "${8:-$(wfname "$1")}" \
    '. + [{databaseId: $id, workflow: $wf, workflowName: $name, event: $ev, headSha: $sha, headBranch: $br,
           status: $st, conclusion: $c, createdAt: $at, displayTitle: $t, attempt: 1,
           url: ("https://github.com/o/r/actions/runs/\($id)")}]'
}
# mkpr N [FILE] [CREATED_MINUTES_AGO]: bot PR #N on autobump/gN, one commit changing FILE
# (default pins.txt) off origin/main; policy auto with no canary engines, a green
# ci.yml dispatch run on the branch. Sets SHA.
mkpr() {
  local n=$1 f=${2:-pins.txt} br=autobump/g$1
  rgit fetch -q origin; rgit checkout -q -B "$br" origin/main
  mkdir -p "$(dirname "$FIX/repo/$f")"; echo "bump $n" >> "$FIX/repo/$f"; rgit add "$f"
  commit_at "${3:-50}" "chore(g$n): bump"; rgit push -q -f origin "$br"; SHA=$(rgit rev-parse HEAD)
  rgit checkout -q main
  jqi pulls.json --argjson n "$n" --arg br "$br" --arg sha "$SHA" --arg at "$(ago "${3:-50}")" \
    '. + [{number: $n, title: "chore(g\($n)): bump g\($n) to its newest patch", state: "open", draft: false,
           user: {login: "github-actions[bot]", type: "Bot"}, created_at: $at,
           head: {ref: $br, sha: $sha, repo: {full_name: "o/r"}}, base: {ref: "main", repo: {full_name: "o/r"}},
           labels: [{name: "autobump"}]}]'
  jq -n --arg f "$f" '[{filename: $f}]' > "$FIX/gh/files/$n.json"
  jq -n --arg g "g$n" '{verdict: "auto", reasons: [], group: $g, update_type: "patch", urgent: false, canary: []}' \
    > "$FIX/policy/$SHA.json"
  run ci.yml workflow_dispatch "$SHA" "$br" completed success "$(ago 20)" "CI"
}
no_ci()  { jqi runs.json --arg s "$1" 'map(select(.workflow != "ci.yml" or .headSha != $s))'; }  # drop SHA's ci.yml runs
pr_set() { jqi pulls.json --argjson n "$1" "map(if .number == \$n then $2 else . end)"; }
pol()    { local f=$FIX/policy/$1.json; jq "$2" "$f" > "$f.tmp" && mv "$f.tmp" "$f"; }
# review N LOGIN TYPE ASSOCIATION STATE COMMIT [SUBMITTED_MINUTES_AGO]
review() {
  local f=$FIX/gh/reviews/$1.json; [ -f "$f" ] || echo '[]' > "$f"
  jq --arg u "$2" --arg t "$3" --arg a "$4" --arg s "$5" --arg c "$6" --arg at "$(ago "${7:-10}")" \
    '. + [{user: {login: $u, type: $t}, author_association: $a, state: $s, commit_id: $c, submitted_at: $at}]' \
    "$f" > "$f.tmp" && mv "$f.tmp" "$f"
}
# canary SHA BRANCH ENGINES_JSON [STATUS] [CONCLUSION] [MINUTES_AGO]: a canary.yml run for SHA
# (dispatched on BRANCH) and its artifact.
canary() {
  run canary.yml workflow_dispatch "$1" "$2" "${4:-completed}" "${5:-success}" "$(ago "${6:-15}")" Canary
  mkdir -p "$FIX/gh/artifacts/$LASTRUN/canary-result"
  jq -n --arg sha "$1" --arg ref "$2" --argjson e "$3" '{sha: $sha, ref: $ref, engines: $e}' \
    > "$FIX/gh/artifacts/$LASTRUN/canary-result/canary-result.json"
}
alarm() { jqi issues.json --arg k "$1" '. + [{number: (length + 500), title: "[ci-alarm] \($k): see the run", state: "open", labels: [{name: "ci-alarm"}]}]'; }
# botcommit MINUTES_AGO URGENT [PR]: a bot-merge squash commit on origin's main, with
# a green ci.yml run for it.
botcommit() {
  rgit fetch -q origin; rgit checkout -q main; rgit reset -q --hard origin/main
  echo "merged $1" >> "$FIX/repo/pins.txt"; rgit add pins.txt
  local when; when="$(( NOW - $1 * 60 )) +0000"
  GIT_AUTHOR_DATE=$when GIT_COMMITTER_DATE=$when GIT_AUTHOR_NAME='github-actions[bot]' \
    git -C "$FIX/repo" commit -q -m "chore(g${3:-9}): bump (#${3:-9})" \
      -m "Bump of g${3:-9}." -m "Bot-merge: pr=${3:-9} group=g${3:-9} urgent=$2"
  rgit push -q origin main
  run ci.yml workflow_dispatch "$(rgit rev-parse HEAD)" main completed success "$(ago $(( $1 > 2 ? $1 - 2 : 0 )))"
}
# main_commit MINUTES_AGO FILE CONTENT [AUTHOR] [MSG]: a commit on origin's main by someone else.
main_commit() {
  rgit fetch -q origin; rgit checkout -q main; rgit reset -q --hard origin/main
  printf '%s' "$3" > "$FIX/repo/$2"; rgit add "$2"; commit_at "$1" "${5:-edit $2}" "${4:-t}"; rgit push -q origin main
}

# run_bm: one pass. CAP=N run_bm sets MAX_BOT_MERGES_PER_DAY (unset: the script's default).
run_bm() {
  OUT=$(cd "$FIX/repo" && PATH="$T/bin:$PATH" GITHUB_REPOSITORY=o/r BOT_MERGE_NOW=$NOW \
        GITHUB_SERVER_URL=https://github.com GITHUB_RUN_ID=777 env ${CAP:+MAX_BOT_MERGES_PER_DAY=$CAP} \
        timeout 120 bash hack/bot-merge 2>&1); RC=$?
}
merges()  { wc -l < "$(G merges)"; }
merged()  { grep -cE "^$1 " "$(G merges)"; }
pinned()  { grep -E "^$1 " "$(G merges)" | grep -oE -- '--match-head-commit [0-9a-f]+' | cut -d' ' -f2; }
disp()    { grep -cE "$1" "$(G dispatches)"; }
comment() { jq -r '.[].body' "$FIX/gh/comments/$1.json" 2>/dev/null; }
ncomments() { jq length "$FIX/gh/comments/$1.json" 2>/dev/null || echo 0; }
show() { echo "rc=$RC merges=$(merges) dispatches=[$(tr '\n' ';' < "$(G dispatches)")] out=$(tr '\n' ';' <<<"$OUT")"; }

# 1. auto verdict, all green: one merge pinned to the head, then ci.yml dispatched on main;
#    the squash subject is conventional and the body carries the Bot-merge line.
fx auto_green; mkpr 1; run_bm
main_head=$(git --git-dir="$FIX/origin.git" log -1 --format=%B main)
if [ "$RC" -eq 0 ] && [ "$(merges)" -eq 1 ] && [ "$(pinned 1)" = "$SHA" ] && grep -q -- '--squash' "$(G merges)" \
   && [ "$(disp '^ci.yml ref=main$')" -eq 1 ] && head -1 <<<"$main_head" | grep -qxE 'chore\(g1\): bump g1 to its newest patch \(#1\)' \
   && grep -qx 'Bot-merge: pr=1 group=g1 urgent=false' <<<"$main_head" \
   && comment 1 | grep -q 'Merged' && comment 1 | grep -qF 'actions/runs/'; then
  ok "auto_green: merged at the head, ci.yml dispatched on main, evidence comment"
else fail "auto_green: $(show) msg=$(tr '\n' '|' <<<"$main_head")"; fi

# 2. review verdict, no approval: no merge, a blocking comment that names the reason.
fx review_no_approval; mkpr 1; pol "$SHA" '.verdict = "review" | .reasons = ["minor update of a static-only engine"]'; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'needs review' && comment 1 | grep -q 'static-only engine' \
   && [ "$(disp ci.yml)" -eq 0 ]; then ok "review_no_approval: blocked with a comment"
else fail "review_no_approval: $(show) comment=$(comment 1)"; fi

# 3. review verdict, approved only by a bot account: no merge.
fx review_bot_approval; mkpr 1; pol "$SHA" '.verdict = "review"'
review 1 'github-actions[bot]' Bot NONE APPROVED "$SHA"; review 1 'some-app[bot]' Bot MEMBER APPROVED "$SHA"; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'needs review'; then ok "review_bot_approval: a bot approval never counts"
else fail "review_bot_approval: $(show)"; fi

# 4. review verdict with an OWNER approval at the head: merges.
fx review_owner_approval; mkpr 1; pol "$SHA" '.verdict = "review"'; review 1 88plug User OWNER APPROVED "$SHA"; run_bm
if [ "$(merged 1)" -eq 1 ] && [ "$(pinned 1)" = "$SHA" ] && comment 1 | grep -q '@88plug'; then
  ok "review_owner_approval: merged on the human approval at the head"
else fail "review_owner_approval: $(show)"; fi

# 4b. an approval from a user without write access (CONTRIBUTOR) does not count.
fx review_contributor; mkpr 1; pol "$SHA" '.verdict = "review"'; review 1 someone User CONTRIBUTOR APPROVED "$SHA"; run_bm
if [ "$(merges)" -eq 0 ]; then ok "review_contributor: no merge"; else fail "review_contributor: $(show)"; fi

# 5. an approval at an older SHA does not count for the new head.
fx approval_old_sha; mkpr 1; old=$SHA; mkpr 1 pins.txt 45; pol "$SHA" '.verdict = "review"'
review 1 88plug User OWNER APPROVED "$old"; jqi pulls.json 'map(select(.head.sha != "'"$old"'"))'; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'needs review'; then ok "approval_old_sha: no merge"
else fail "approval_old_sha: $(show)"; fi

# 5b. a human's later changes-requested review blocks even an auto verdict, and such a PR
#     is not regenerated when main moves.
fx changes_requested; mkpr 1; review 1 88plug User OWNER APPROVED "$SHA" 30; review 1 88plug User OWNER CHANGES_REQUESTED "$SHA" 5
main_commit 10 scripts/other.sh 'echo moved'; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'changes requested' && [ "$(disp freshness.yml)" -eq 0 ]; then ok "changes_requested: blocks, no regeneration"
else fail "changes_requested: $(show) comment=$(comment 1)"; fi

# 6. a held (action_required) pull_request run newer than a green dispatch run: merges.
fx held_plus_dispatch; mkpr 1; run ci.yml pull_request "$SHA" autobump/g1 completed action_required "$(ago 5)"; run_bm
if [ "$(merged 1)" -eq 1 ]; then ok "held_plus_dispatch: the dispatch run counts, the held run is ignored"
else fail "held_plus_dispatch: $(show)"; fi

# 7. CI still running at the head: skip.
fx ci_pending; mkpr 1; no_ci "$SHA"; run ci.yml workflow_dispatch "$SHA" autobump/g1 in_progress '' "$(ago 2)"; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'CI is pending'; then ok "ci_pending: skipped"
else fail "ci_pending: $(show) comment=$(comment 1)"; fi

# 7b. CI failed at the head: skip.
fx ci_failed; mkpr 1; run ci.yml workflow_dispatch "$SHA" autobump/g1 completed failure "$(ago 2)"; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'CI failed'; then ok "ci_failed: skipped"
else fail "ci_failed: $(show)"; fi

# 8. canary engines mapped but no canary run, or one still running: skip.
fx canary_missing; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu"]'; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -qi 'canary has not run'; then ok "canary_missing: skipped"
else fail "canary_missing: $(show) comment=$(comment 1)"; fi
fx canary_pending; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu"]'; canary "$SHA" autobump/g1 '{}' in_progress ''; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -qi 'canary is pending'; then ok "canary_pending: skipped"
else fail "canary_pending: $(show) comment=$(comment 1)"; fi

# 8b. canary green for every mapped engine: merges, and the comment links the canary run.
fx canary_pass; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu","node"]'
canary "$SHA" autobump/g1 '{"vllm-cpu":{"kind":"runtime","verdict":"pass","hard":true,"exact":true},"node":{"kind":"node","verdict":"pass","hard":true,"exact":true}}'
crun=$LASTRUN; run_bm
if [ "$(merged 1)" -eq 1 ] && comment 1 | grep -qF "actions/runs/$crun"; then ok "canary_pass: merged"
else fail "canary_pass: $(show)"; fi

# 8c. a canary result for another SHA, or one lacking a mapped engine, is not green.
fx canary_wrong_sha; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu"]'
canary "$SHA" autobump/g1 '{"vllm-cpu":{"verdict":"pass","hard":true,"exact":true}}'
jq '.sha = "0000000"' "$FIX/gh/artifacts/$LASTRUN/canary-result/canary-result.json" > "$T/x" && mv "$T/x" "$FIX/gh/artifacts/$LASTRUN/canary-result/canary-result.json"; run_bm
if [ "$(merges)" -eq 0 ]; then ok "canary_wrong_sha: no merge"; else fail "canary_wrong_sha: $(show)"; fi
fx canary_lacks_engine; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu","ovms-cpu"]'
canary "$SHA" autobump/g1 '{"vllm-cpu":{"verdict":"pass","hard":true,"exact":true}}'; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'ovms-cpu'; then ok "canary_lacks_engine: no merge"; else fail "canary_lacks_engine: $(show)"; fi
# an engine entry that is not a result object (null) is not a verdict.
fx canary_null_entry; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu","ovms-cpu"]'
canary "$SHA" autobump/g1 '{"vllm-cpu":{"verdict":"pass","hard":true,"exact":true},"ovms-cpu":null}'; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'ovms-cpu'; then ok "canary_null_entry: no merge"; else fail "canary_null_entry: $(show)"; fi

# 8d. a held (action_required) pull_request canary run newer than the green dispatched one
#     is not an attempt: merges.
fx canary_held_newer; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu"]'
canary "$SHA" autobump/g1 '{"vllm-cpu":{"verdict":"pass","hard":true,"exact":true}}' completed success 20
run canary.yml pull_request "$SHA" autobump/g1 completed action_required "$(ago 5)" Canary; run_bm
if [ "$(merged 1)" -eq 1 ]; then ok "canary_held_newer: the held run is ignored"
else fail "canary_held_newer: $(show) comment=$(comment 1)"; fi
# 8e. an isa-skip re-dispatch reruns only the skipped engine: each engine's newest result at
#     the head decides, so the first run's pass for vllm-cpu and the re-dispatch's pass for
#     ovms-cpu merge, and the comment links both runs.
fx canary_redispatch; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu","ovms-cpu"]'
canary "$SHA" autobump/g1 '{"vllm-cpu":{"verdict":"pass","hard":true,"exact":true},"ovms-cpu":{"verdict":"isa-skip","hard":false,"exact":false}}' completed failure 40
c1=$LASTRUN
canary "$SHA" autobump/g1 '{"ovms-cpu":{"verdict":"pass","hard":true,"exact":true}}' completed success 15
c2=$LASTRUN; run_bm
if [ "$(merged 1)" -eq 1 ] && comment 1 | grep -qF "actions/runs/$c1" && comment 1 | grep -qF "actions/runs/$c2"; then
  ok "canary_redispatch: per-engine newest result, merged"
else fail "canary_redispatch: $(show) comment=$(comment 1)"; fi
# ...but a re-dispatch that fails blocks...
fx canary_redispatch_fail; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu","ovms-cpu"]'
canary "$SHA" autobump/g1 '{"vllm-cpu":{"verdict":"pass","hard":true,"exact":true},"ovms-cpu":{"verdict":"isa-skip","hard":false,"exact":false}}' completed failure 40
canary "$SHA" autobump/g1 '{"ovms-cpu":{"verdict":"fail","hard":false,"exact":false}}' completed failure 15; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'ovms-cpu: fail'; then ok "canary_redispatch_fail: no merge"
else fail "canary_redispatch_fail: $(show) comment=$(comment 1)"; fi
# ...a newer result for the same engine supersedes an older pass...
fx canary_newer_fail; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu"]'
canary "$SHA" autobump/g1 '{"vllm-cpu":{"verdict":"pass","hard":true,"exact":true}}' completed success 40
canary "$SHA" autobump/g1 '{"vllm-cpu":{"verdict":"fail","hard":false,"exact":false}}' completed failure 15; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'vllm-cpu: fail'; then ok "canary_newer_fail: no merge"
else fail "canary_newer_fail: $(show) comment=$(comment 1)"; fi
# ...a newer completed run without a canary-result artifact blocks: what it found is unknown...
fx canary_newest_no_artifact; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu"]'
canary "$SHA" autobump/g1 '{"vllm-cpu":{"verdict":"pass","hard":true,"exact":true}}' completed success 40
run canary.yml workflow_dispatch "$SHA" autobump/g1 completed cancelled "$(ago 15)" Canary; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'no canary-result artifact'; then ok "canary_newest_no_artifact: no merge"
else fail "canary_newest_no_artifact: $(show) comment=$(comment 1)"; fi
# ...a newest run that is red on an engine the policy does not map still blocks...
fx canary_run_red; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu"]'
canary "$SHA" autobump/g1 '{"vllm-cpu":{"verdict":"pass","hard":true,"exact":true},"node":{"verdict":"fail","hard":false,"exact":false}}' completed failure; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'canary run concluded failure'; then ok "canary_run_red: no merge"
else fail "canary_run_red: $(show) comment=$(comment 1)"; fi
# ...and a canary dispatched on another branch at the same SHA is not this PR's run.
fx canary_other_branch; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu"]'
canary "$SHA" autobump/other '{"vllm-cpu":{"verdict":"pass","hard":true,"exact":true}}'; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -qi 'canary has not run'; then ok "canary_other_branch: no merge"
else fail "canary_other_branch: $(show) comment=$(comment 1)"; fi

# 9. canary hard pass with exact false: label canary-drift, no merge (review)...
fx canary_drift; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu"]'
canary "$SHA" autobump/g1 '{"vllm-cpu":{"verdict":"pass","hard":true,"exact":false,"detail":"golden differs"}}'; run_bm
if [ "$(merges)" -eq 0 ] && grep -qx '1 canary-drift' "$(G labels)" && comment 1 | grep -q 'canary-drift'; then
  ok "canary_drift: labelled canary-drift, no merge"
else fail "canary_drift: $(show) labels=$(cat "$(G labels)")"; fi
# ...and with an OWNER approval at the head it merges (drift is a review condition).
fx canary_drift_approved; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu"]'
canary "$SHA" autobump/g1 '{"vllm-cpu":{"verdict":"pass","hard":true,"exact":false}}'
review 1 88plug User OWNER APPROVED "$SHA"; run_bm
if [ "$(merged 1)" -eq 1 ]; then ok "canary_drift_approved: merged on the human approval"
else fail "canary_drift_approved: $(show)"; fi

# 10. canary isa-skip (and too-big) is never green, even with an approval.
for v in isa-skip too-big; do
  fx "canary_$v"; mkpr 1; pol "$SHA" '.canary = ["vllm-cpu"]'
  canary "$SHA" autobump/g1 "{\"vllm-cpu\":{\"verdict\":\"$v\",\"hard\":false,\"exact\":false}}"
  review 1 88plug User OWNER APPROVED "$SHA"; run_bm
  if [ "$(merges)" -eq 0 ] && comment 1 | grep -q -- "$v" && ! grep -q canary-drift "$(G labels)"; then ok "canary_$v: no merge"
  else fail "canary_$v: $(show)"; fi
done

# 11. main moved under code since the head was tested: regeneration dispatched (freshness.yml
#     group=g1); a second PR's group counts separately...
fx base_moved_code; mkpr 1; main_commit 10 scripts/other.sh 'echo moved'; run_bm
if [ "$(merges)" -eq 0 ] && [ "$(disp '^freshness.yml ref=main group=g1 reason=')" -eq 1 ] && comment 1 | grep -q 'main moved'; then
  ok "base_moved_code: regeneration dispatched"
else fail "base_moved_code: $(show)"; fi
# ...two of today's dispatched runs name g1 and one names g10: a third g1 regeneration goes out...
fx regen_under_cap; mkpr 1; main_commit 10 scripts/other.sh 'echo moved'
run freshness.yml workflow_dispatch "$(rgit rev-parse origin/main)" main completed success "$(ago 120)" "Freshness group=g1 reason=x"
run freshness.yml workflow_dispatch "$(rgit rev-parse origin/main)" main completed success "$(ago 100)" "Freshness group=g1 reason=x"
run freshness.yml workflow_dispatch "$(rgit rev-parse origin/main)" main completed success "$(ago 90)" "Freshness group=g10 reason=x"
run freshness.yml workflow_dispatch "$(rgit rev-parse origin/main)" main completed success "$(iso $((NOW - 15 * 3600)))" "Freshness group=g1 reason=x"
run_bm
if [ "$(disp '^freshness.yml ref=main group=g1 ')" -eq 1 ]; then ok "regen_under_cap: 2 of 3 used today, dispatched"
else fail "regen_under_cap: $(show)"; fi
# ...with three today the cap holds.
fx regen_cap; mkpr 1; main_commit 10 scripts/other.sh 'echo moved'
for m in 120 100 80; do run freshness.yml workflow_dispatch "$(rgit rev-parse origin/main)" main completed success "$(ago $m)" "Freshness group=g1 reason=x"; done
run_bm
if [ "$(disp freshness.yml)" -eq 0 ] && [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'regeneration cap'; then ok "regen_cap: 3 regenerations today, no fourth"
else fail "regen_cap: $(show) comment=$(comment 1)"; fi
# 11b. a freshness run still in flight: wait for it, dispatch nothing.
fx regen_in_flight; mkpr 1; main_commit 10 scripts/other.sh 'echo moved'
run freshness.yml schedule "$(rgit rev-parse origin/main)" main in_progress '' "$(ago 5)" Freshness; run_bm
if [ "$(disp freshness.yml)" -eq 0 ] && [ "$(merges)" -eq 0 ]; then ok "regen_in_flight: no second dispatch"
else fail "regen_in_flight: $(show)"; fi

# 12. main moved only by release commits (and the ledger): merges without regeneration.
fx base_moved_release; mkpr 1
main_commit 20 scripts/k3d-gpu $'#!/bin/sh\nVERSION="0.1.80"\n' 88plug-bot 'release: aur-v0.1.80 [skip release]'
main_commit 15 docs/provenance.md $'ledger\n'
run ci.yml push "$(rgit rev-parse origin/main)" main completed success "$(ago 12)"; run_bm
if [ "$(merged 1)" -eq 1 ] && [ "$(disp freshness.yml)" -eq 0 ]; then ok "base_moved_release: merged, no regeneration"
else fail "base_moved_release: $(show)"; fi

# 13. main's ledger moved (a new SUPPORTED row) without ledger-rules.json: the merged tree's
#     rules are stale, so regenerate.
fx rules_stale; mkpr 1
main_commit 10 EXPERIMENTS.md $'## Hypotheses\n\n| H34 | on the i9-13900T | p | m | SUPPORTED |\n| H34b | on the i5-12500T | q | n | SUPPORTED |\n'
run ci.yml push "$(rgit rev-parse origin/main)" main completed success "$(ago 8)"; run_bm
if [ "$(merges)" -eq 0 ] && [ "$(disp '^freshness.yml ref=main group=g1 ')" -eq 1 ] && comment 1 | grep -q 'ledger-rules.json'; then
  ok "rules_stale: regeneration dispatched"
else fail "rules_stale: $(show) comment=$(comment 1)"; fi

# 14. the latest completed ci.yml run on main failed: nothing merges, nothing regenerates.
fx main_ci_red; mkpr 1; mkpr 2; main_commit 10 scripts/other.sh 'echo moved'
run ci.yml push "$(rgit rev-parse origin/main)" main completed failure "$(ago 8)"; run_bm
if [ "$(merges)" -eq 0 ] && [ "$(disp freshness.yml)" -eq 0 ] && comment 1 | grep -q 'circuit breaker'; then ok "main_ci_red: nothing merges"
else fail "main_ci_red: $(show)"; fi
# a later green pull_request run whose head branch is named main (a fork's) does not close it.
fx main_ci_red_fork_main; mkpr 1
run ci.yml push "$(rgit rev-parse origin/main)" main completed failure "$(ago 8)"
run ci.yml pull_request deadbeef main completed success "$(ago 4)"; run_bm
if [ "$(merges)" -eq 0 ]; then ok "main_ci_red_fork_main: still blocked"; else fail "main_ci_red_fork_main: $(show)"; fi

# 15. an open ci-main alarm (and every other breaker key): nothing merges.
for k in ci-main ci-missing:freshness.yml release:aur-v0.1.79 red:canary; do
  fx "alarm_${k//[:.]/_}"; mkpr 1; alarm "$k"; run_bm
  if [ "$(merges)" -eq 0 ] && comment 1 | grep -qF "$k"; then ok "alarm $k: nothing merges"
  else fail "alarm $k: $(show)"; fi
done

# 16. an alarm whose key is not a breaker key: merges.
fx alarm_other; mkpr 1; alarm 'blind-spot:k3s'; run_bm
if [ "$(merged 1)" -eq 1 ]; then ok "alarm_other: merged"; else fail "alarm_other: $(show)"; fi

# 17. daily cap reached: the non-urgent PR waits, the urgent one merges. Yesterday's merge
#     does not count.
fx daily_cap; botcommit $((15 * 60)) false 7; botcommit 300 false 8; botcommit 200 false 9
mkpr 1 pins.txt 40; mkpr 2 other.txt 30; pol "$SHA" '.urgent = true'
CAP=2 run_bm
if [ "$(merged 1)" -eq 0 ] && [ "$(merged 2)" -eq 1 ] && comment 1 | grep -q 'daily cap'; then
  ok "daily_cap: non-urgent skipped, urgent merged"
else fail "daily_cap: $(show) comment=$(comment 1)"; fi

# 18. a PR touching .github/: never merged, and its comment says why.
fx touches_github; mkpr 1 .github/workflows/ci.yml; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -qF '.github/'; then ok "touches_github: never"
else fail "touches_github: $(show)"; fi
# a file renamed out of .github/ counts too.
fx renames_github; mkpr 1 ci-moved.yml
echo '[{"filename": "ci-moved.yml", "previous_filename": ".github/workflows/ci.yml", "status": "renamed"}]' > "$FIX/gh/files/1.json"; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -qF '.github/'; then ok "renames_github: never"
else fail "renames_github: $(show)"; fi

# 19. ledger amendments, dependabot and fork PRs are never bot-merged (and get no comment).
fx not_candidates; mkpr 1; mkpr 2; mkpr 3; mkpr 4
pr_set 1 '.head.ref = "autobump/amend-H54q" | .labels += [{name: "ledger-amendment"}]'
pr_set 2 '.labels += [{name: "ledger-amendment"}]'
pr_set 3 '.user = {login: "dependabot[bot]", type: "Bot"}'
pr_set 4 '.head.repo.full_name = "someone/r"'
mkpr 5; pr_set 5 '.labels = []'
mkpr 6; pr_set 6 '.head.repo = null'
run_bm
if [ "$(merges)" -eq 0 ] && [ "$(ncomments 1)$(ncomments 2)$(ncomments 3)$(ncomments 4)$(ncomments 5)$(ncomments 6)" = 000000 ]; then
  ok "not_candidates: amendment, dependabot, fork, unlabelled: untouched"
else fail "not_candidates: $(show)"; fi

# 20. two eligible PRs: one merge per pass, the urgent one first though it is newer...
fx two_urgent_first; mkpr 1 pins.txt 40; mkpr 2 other.txt 30; pol "$SHA" '.urgent = true'; run_bm
if [ "$(merges)" -eq 1 ] && [ "$(merged 2)" -eq 1 ] && comment 1 | grep -q 'one merge per pass'; then
  ok "two_urgent_first: only the urgent PR merged"
else fail "two_urgent_first: $(show)"; fi
# ...else the oldest.
fx two_oldest_first; mkpr 2 other.txt 30; mkpr 1 pins.txt 40; run_bm
if [ "$(merges)" -eq 1 ] && [ "$(merged 1)" -eq 1 ]; then ok "two_oldest_first: the older PR merged"
else fail "two_oldest_first: $(show)"; fi

# 21. a refused merge: GitHub's reason printed, the pass stops (no ci.yml dispatch, no
#     release), the comment says so.
fx merge_refused; mkpr 1; echo 1 > "$(G merge_rc)"; run_bm
if [ "$(merges)" -eq 1 ] && [ "$(disp ci.yml)" -eq 0 ] && grep -q 'Base branch was modified' <<<"$OUT" \
   && comment 1 | grep -q 'refused'; then ok "merge_refused: reason shown, pass stopped"
else fail "merge_refused: $(show)"; fi

# 22. a gh without --match-head-commit never merges.
fx old_gh; mkpr 1; echo '      --squash   Squash the commits' > "$(G merge_help)"; run_bm
if [ "$RC" -eq 2 ] && [ "$(merges)" -eq 0 ]; then ok "old_gh: exit 2"; else fail "old_gh: $(show)"; fi

# 23. a policy without a canary list fails closed; a failing policy blocks.
fx policy_no_engines; mkpr 1; pol "$SHA" 'del(.canary)'; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'no canary list'; then ok "policy_no_engines: blocked"
else fail "policy_no_engines: $(show)"; fi
fx policy_fails; mkpr 1; rm "$FIX/policy/$SHA.json"; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q 'policy'; then ok "policy_fails: blocked"
else fail "policy_fails: $(show)"; fi

# 24. one comment per PR, edited in place when the reason changes, untouched when not.
fx comment_in_place; mkpr 1; no_ci "$SHA"; run ci.yml workflow_dispatch "$SHA" autobump/g1 in_progress '' "$(ago 2)"; run_bm; run_bm
patches1=$(grep -c comment-patch "$(G calls)")
jqi runs.json --argjson id "$LASTRUN" 'map(if .databaseId == $id then .status = "completed" | .conclusion = "failure" else . end)'; run_bm
if [ "$(ncomments 1)" -eq 1 ] && [ "$patches1" -eq 0 ] && [ "$(grep -c comment-patch "$(G calls)")" -eq 1 ] \
   && comment 1 | grep -q 'CI failed'; then ok "comment_in_place: one comment, edited only on change"
else fail "comment_in_place: comments=$(ncomments 1) patches1=$patches1 patches=$(grep -c comment-patch "$(G calls)")"; fi

# 25. a draft PR is not merged.
fx draft; mkpr 1; pr_set 1 '.draft = true'; run_bm
if [ "$(merges)" -eq 0 ] && comment 1 | grep -q draft; then ok "draft: no merge"; else fail "draft: $(show)"; fi

# --- release batching ---------------------------------------------------------
REL='^auto-release.yml ref=main mode=changed$'
# R1. an urgent bot merge since the last tag: release at once, though the queue is not empty
#     (PR 1 is auto-eligible with green CI, held by the daily cap) and the tag is 1 h old.
fx rel_urgent; botcommit 20 true; mkpr 1; CAP=1 run_bm
if [ "$(merges)" -eq 0 ] && [ "$(disp "$REL")" -eq 1 ]; then ok "rel_urgent: dispatched"
else fail "rel_urgent: $(show)"; fi
# R2. queue empty: dispatch.
fx rel_queue_empty; botcommit 20 false; run_bm
if [ "$(disp "$REL")" -eq 1 ]; then ok "rel_queue_empty: dispatched"; else fail "rel_queue_empty: $(show)"; fi
# R3. queue non-empty, last tag under 6 h old: wait.
fx rel_queue_wait; botcommit 20 false; mkpr 1; CAP=1 run_bm
if [ "$(disp auto-release)" -eq 0 ] && grep -q 'release: wait' <<<"$OUT"; then ok "rel_queue_wait: waits"
else fail "rel_queue_wait: $(show)"; fi
# R3b. queue non-empty but the last tag is 7 h old: dispatch.
fx rel_six_hours 420; botcommit 20 false; mkpr 1; CAP=1 run_bm
if [ "$(merges)" -eq 0 ] && [ "$(disp "$REL")" -eq 1 ]; then ok "rel_six_hours: dispatched"; else fail "rel_six_hours: $(show)"; fi
# R4. main HEAD's ci.yml run has not finished green: wait.
fx rel_main_ci; botcommit 20 false
run ci.yml workflow_dispatch "$(rgit rev-parse origin/main)" main in_progress '' "$(ago 1)"; run_bm
if [ "$(disp auto-release)" -eq 0 ]; then ok "rel_main_ci: waits"; else fail "rel_main_ci: $(show)"; fi
# R5. a release run in progress: wait.
for wf in auto-release.yml build-and-push.yml; do
  fx "rel_in_progress_${wf%%.*}"; botcommit 20 false
  run "$wf" workflow_dispatch "$(rgit rev-parse origin/main)" main in_progress '' "$(ago 1)"; run_bm
  if [ "$(disp auto-release)" -eq 0 ]; then ok "rel_in_progress ($wf): waits"; else fail "rel_in_progress ($wf): $(show)"; fi
done
# R6. no bot merge above the last tag: nothing to release.
fx rel_nothing; run_bm
if [ "$(disp auto-release)" -eq 0 ]; then ok "rel_nothing: no dispatch"; else fail "rel_nothing: $(show)"; fi
# R7. a release run already succeeded after the newest bot merge (it found nothing to ship):
#     do not dispatch again every pass.
fx rel_already; botcommit 20 false
run auto-release.yml workflow_dispatch "$(rgit rev-parse origin/main)" main completed success "$(ago 10)"; run_bm
if [ "$(disp auto-release)" -eq 0 ]; then ok "rel_already: no repeat dispatch"; else fail "rel_already: $(show)"; fi
# R8. a merge in this pass leaves main's new head without CI: the release waits for it.
fx rel_after_merge; mkpr 1; run_bm
if [ "$(merged 1)" -eq 1 ] && [ "$(disp auto-release)" -eq 0 ]; then ok "rel_after_merge: waits for main CI"
else fail "rel_after_merge: $(show)"; fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
