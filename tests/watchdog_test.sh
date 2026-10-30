#!/usr/bin/env bash
# hack/watchdog against tests/ops/fake_gh.py (routes plus an issue store), a stub
# registry (tests/ops/stub_registry.py), a file:// AUR answer and a local git
# repository holding the aur-v* tags. No network. The real hack/ci_alarm files the
# alarms into the fake issue store. Run lists, jobs, the failed log and the
# workflow list are the real gh shapes (tests/ops/fixtures/, from the October red
# nights of build-and-push.yml); each case moves the clock (WATCHDOG_NOW) and the
# fixtures the fake gh answers with.
#
# Usage: tests/watchdog_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WATCHDOG=${WATCHDOG:-${ROOT}/hack/watchdog}
FIX="${ROOT}/tests/ops/fixtures"
T=$(mktemp -d "${TMPDIR:-/tmp}/watchdog_test.XXXXXX")
trap '[ -n "${KEEP:-}" ] || rm -rf "$T"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

mkdir -p "$T/bin"
printf '#!/bin/sh\nexec python3 %q "$@"\n' "${ROOT}/tests/ops/fake_gh.py" > "$T/bin/gh"
chmod +x "$T/bin/gh"
export PATH="$T/bin:$PATH" GH_REPO=88plug/k3d-gpu
export WATCHDOG_IMAGES_LOCK="${ROOT}/tests/ops/stub_registry.py"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
unset GITHUB_STEP_SUMMARY SIMULATE_RED ALARM_OWNER
HEAD_SHA=890d1d16790ed656ae5aa86a8319e578310b0f80
PR_SHA=1bf784c727bc3c2a051d5022681b0fa51b7d7ccf

# iso OFFSET: $NOW moved by OFFSET (a GNU date relative item, e.g. "-73 hours").
iso() { date -u -d "$NOW ${1:-}" +%Y-%m-%dT%H:%M:%SZ; }
at() {  # at ISO: set the clock for the watchdog, ci_alarm and the fake gh
    NOW=$1
    export FAKE_GH_NOW=$NOW
    WATCHDOG_NOW=$(date -u -d "$NOW" +%s); CI_ALARM_NOW=$WATCHDOG_NOW
    export WATCHDOG_NOW CI_ALARM_NOW
}
# route REGEX FILE [RC] [STDERR]: answered before every earlier route.
route() {
    { printf '%s\t%s\t%s' "$1" "$2" "${3:-0}"; [ -n "${4:-}" ] && printf '\t%s' "$4"; printf '\n'
      cat "$C/routes.tsv" 2>/dev/null; } > "$C/routes.tmp"
    mv "$C/routes.tmp" "$C/routes.tsv"
}
put() { cat > "$C/$1"; }   # put FILE: write a fixture file from stdin
# tags "aur-vX@OFFSET ...": the case root's git history, one commit per tag.
tags() {
    local spec t
    rm -rf "$C/root/.git"
    git -C "$C/root" init -q -b main
    for spec in $1; do
        t=${spec%%@*}
        GIT_AUTHOR_DATE=$(iso "${spec#*@}") GIT_COMMITTER_DATE=$(iso "${spec#*@}") \
            git -C "$C/root" -c user.name=t -c user.email=t@t commit -q --allow-empty -m "release: $t"
        git -C "$C/root" tag "$t"
    done
}
registry() { printf '%s\n' "$@" > "$C/registry"; }
aur() { printf '{"resultcount":1,"results":[{"Name":"k3d-gpu","Version":"%s"}],"type":"multiinfo","version":5}\n' "$1" > "$C/aur.json"; }
runs() {  # runs FILE: a ci.yml run list; each argument "event:status:conclusion[:sha[:createdAt]]"
    local f=$1; shift
    printf '%s\n' "$@" | jq -R 'split(":") | {databaseId: 4100, event: .[0], status: .[1], conclusion: .[2],
        headSha: (.[3] // ""), createdAt: (.[4] // ""), url: "https://github.com/88plug/k3d-gpu/actions/runs/4100"}' \
        | jq -s . > "$C/$f"
}

# setup NAME [ISO]: a healthy repository at ISO (default 2026-10-29T13:00:00Z):
# every scheduled workflow green, main's HEAD an hour old with green CI, release
# 0.1.79 complete everywhere, no bot PR, no open alarm.
setup() {
    C=$T/$1
    export FAKE_GH=$C
    mkdir -p "$C/root/.github"
    cp -r "${ROOT}/.github/workflows" "$C/root/.github/"
    at "${2:-2026-10-29T13:00:00Z}"
    # The workflow list: the real one, plus every workflow file the repo has now
    # (watchdog.yml registered an hour ago).
    python3 - "$FIX/workflows.json" "$C/root/.github/workflows" "$(iso '-1 hour')" > "$C/workflows.json" <<'PY'
import json, os, re, sys
doc = json.load(open(sys.argv[1]))
have = {w["path"] for w in doc["workflows"]}
for f in sorted(os.listdir(sys.argv[2])):
    p = ".github/workflows/" + f
    if p not in have:
        name = re.search(r"^name:\s*(.+)$", open(os.path.join(sys.argv[2], f)).read(), re.M).group(1).strip("'\"")
        doc["workflows"].append({"id": 900000 + len(doc["workflows"]), "name": name, "path": p, "state": "active",
                                 "created_at": sys.argv[3], "updated_at": sys.argv[3]})
doc["total_count"] = len(doc["workflows"])
print(json.dumps(doc))
PY
    jq --arg t "$(iso '-1 hour')" '[.[0] | .createdAt = $t | .startedAt = $t | .updatedAt = $t]' \
        "$FIX/build-and-push_schedule_runs.json" > "$C/bp_sched.json"
    jq -n --arg t "$(iso '-13 minutes')" '[{databaseId: 4200, conclusion: "success", status: "completed",
        createdAt: $t, url: "https://github.com/88plug/k3d-gpu/actions/runs/4200", headSha: "x",
        workflowName: "Watchdog", event: "schedule"}]' > "$C/wd_sched.json"
    jq -n --arg s "$HEAD_SHA" --arg t "$(iso '-1 hour')" \
        '{sha: $s, commit: {committer: {name: "88plug", date: $t}, message: "bench(wildct): smoke3"}}' > "$C/head.json"
    runs ci_head.json "push:completed:success:$HEAD_SHA:$(iso '-58 minutes')"
    runs ci_main.json "push:completed:success:$HEAD_SHA:$(iso '-58 minutes')"
    echo '[]' > "$C/busy.json"
    echo '[]' > "$C/prs.json"
    jq -n '{assets: [{name: "k3d-gpu-0.1.79-1.noarch.rpm", state: "uploaded"},
                     {name: "k3d-gpu_0.1.79-1_all.deb", state: "uploaded"},
                     {name: "SHA256SUMS", state: "uploaded"}]}' > "$C/rel79.json"
    for v in 75 77 78; do sed "s/0\.1\.79/0.1.$v/g" "$C/rel79.json" > "$C/rel$v.json"; done
    cp "$FIX/run_39412124435_jobs.json" "$C/jobs.json"
    cp "$FIX/run_39412124435_log_failed.txt" "$C/log.txt"
    tags "aur-v0.1.78@-6hours aur-v0.1.79@-2hours"
    registry docker.io/cryptoandcoffee/k3d-gpu:0.1.78 docker.io/cryptoandcoffee/k3d-gpu:0.1.79 \
             ghcr.io/88plug/k3d-gpu:0.1.78 ghcr.io/88plug/k3d-gpu:0.1.79
    aur 0.1.79-1
    export STUB_REGISTRY="$C/registry" WATCHDOG_AUR_URL="file://$C/aur.json"
    rm -f "$C/routes.tsv"
    route '^api repos/88plug/k3d-gpu/actions/workflows' workflows.json
    route '^run list .*--workflow build-and-push\.yml .*--event schedule' bp_sched.json
    route '^run list .*--workflow watchdog\.yml .*--event schedule' wd_sched.json
    route '^api repos/88plug/k3d-gpu/commits/main' head.json
    route '^run list .*--workflow ci\.yml .*--commit ' ci_head.json
    route '^run list .*--workflow ci\.yml .*--branch main' ci_main.json
    route '^run list -R \S+ --limit \d+ --json workflowName' busy.json
    route '^release view ' - 1 'release not found'
    route '^release view aur-v0\.1\.79 ' rel79.json
    route '^release view aur-v0\.1\.78 ' rel78.json
    route '^pr list ' prs.json
    route '^run view \d+ .*--json jobs' jobs.json
    route '^run view \d+ .*--log-failed' log.txt
}
watch() { python3 "$WATCHDOG" --root "$C/root" "$@" > "$C/out" 2> "$C/err"; }
issue() {  # issue KEY: the newest issue for the key, as JSON
    jq -c --arg p "[ci-alarm] $1: " '[.[] | select(.title | startswith($p))] | max_by(.number) // empty' "$C/issues.json" 2>/dev/null
}
state() { local i; i=$(issue "$1"); if [ -z "$i" ]; then echo none; else jq -r .state <<< "$i"; fi; }
nopen() { jq '[.[] | select(.state == "OPEN")] | length' "$C/issues.json" 2>/dev/null || echo 0; }
dispatched() { grep -cxF "$1" "$C/dispatches.log" 2>/dev/null || true; }
ndispatch() { if [ -f "$C/dispatches.log" ]; then wc -l < "$C/dispatches.log"; else echo 0; fi; }
why() { echo "rc=$rc open=$(nopen) dispatches=$(ndispatch) err=$(tail -3 "$C/err" | tr '\n' ' ') keys=$(jq -r '[.[] | select(.state == "OPEN") | .title] | join(" | ")' "$C/issues.json" 2>/dev/null)"; }

# 1. A healthy repository: no alarm, no dispatch, exit 0.
setup healthy
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(nopen)" = 0 ] && [ "$(ndispatch)" = 0 ] && ! grep -q 'no route' "$C/err"; then
    ok "healthy: no alarm, no dispatch"
else
    fail "healthy: $(why)"
fi

# 2. The October red nights of build-and-push.yml, replayed from the real run list.
# Night 1 (run 39412124435 red at 11:02, the watchdog at 11:47): red alarm and one
# re-dispatch; the hourly run after it adds neither.
setup october 2026-10-20T11:47:00Z
night() {  # night ISO: the scheduled runs created up to ISO, newest first
    at "$1"
    jq --arg t "$1" '[.[] | select(.createdAt <= $t)]' "$FIX/build-and-push_schedule_runs.json" > "$C/bp_sched.json"
    jq -n --arg s "$HEAD_SHA" --arg t "$(iso '-1 hour')" '{sha: $s, commit: {committer: {date: $t}}}' > "$C/head.json"
    jq -n --arg t "$(iso '-13 minutes')" '[{databaseId: 4200, conclusion: "success", status: "completed", createdAt: $t,
        url: "https://github.com/88plug/k3d-gpu/actions/runs/4200", headSha: "x"}]' > "$C/wd_sched.json"
}
night 2026-10-20T11:47:00Z
watch; rc=$?
red=$(issue red:build-and-push.yml)
if [ "$rc" -eq 0 ] && [ "$(state red:build-and-push.yml)" = OPEN ] \
   && [ "$(dispatched 'build-and-push.yml ref=main')" = 1 ] && [ "$(ndispatch)" = 1 ] \
   && jq -r .body <<< "$red" | grep -q 'actions/runs/39412124435' \
   && jq -r .body <<< "$red" | grep -q 'repin-images' \
   && jq -r .body <<< "$red" | grep -q 'refusing to allow a GitHub App' \
   && jq -r .body <<< "$red" | grep -q 'bot push touched .github/workflows' \
   && [ "$(jq -r '.assignees[0].login' <<< "$red")" = 88plug ] \
   && [ "$(state stale:build-and-push.yml)" = none ]; then
    ok "October night 1: red:build-and-push.yml (run, failed job, log, hint) and one re-dispatch"
else
    fail "October night 1: $(why) body=$(jq -r .body <<< "$red" | head -c 600)"
fi
night 2026-10-20T12:47:00Z
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(ndispatch)" = 1 ] && [ "$(jq '.comments | length' <<< "$(issue red:build-and-push.yml)")" = 0 ] \
   && [ "$(jq '[.[] | select(.title | startswith("[ci-alarm] red:"))] | length' "$C/issues.json")" = 1 ] \
   && [ "$(state stale:build-and-push.yml)" = none ]; then
    ok "October night 1, next hour: no second re-dispatch for the same run, no new comment, not stale at 24.4 h"
else
    fail "October night 1, next hour: $(why)"
fi
# Night 2: a new red run; a new re-dispatch and a comment; 47 h since the last
# green scheduled run (threshold 24 h + 6 h): stale too.
night 2026-10-21T11:47:00Z
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(ndispatch)" = 2 ] \
   && jq -r '.comments[-1].body' <<< "$(issue red:build-and-push.yml)" | grep -q 'actions/runs/39591066580' \
   && [ "$(state stale:build-and-push.yml)" = OPEN ]; then
    ok "October night 2: the new red run re-dispatches once more and comments; stale opens"
else
    fail "October night 2: $(why)"
fi
# 2026-10-29: the scheduled run is green again: red and stale close.
night 2026-10-29T13:00:00Z
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(state red:build-and-push.yml)" = CLOSED ] && [ "$(state stale:build-and-push.yml)" = CLOSED ] \
   && [ "$(ndispatch)" = 2 ]; then
    ok "green scheduled run closes red and stale"
else
    fail "green closes: $(why)"
fi

# 3. Stale: the last green scheduled run is 31 h old and nothing ran since.
setup stale
jq --arg t "$(iso '-31 hours')" '[.[0] | .createdAt = $t]' "$FIX/build-and-push_schedule_runs.json" > "$C/bp_sched.json"
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(state stale:build-and-push.yml)" = OPEN ] && [ "$(state red:build-and-push.yml)" = none ] \
   && [ "$(ndispatch)" = 0 ]; then
    ok "stale: a last success older than period + 6 h opens stale:build-and-push.yml"
else
    fail "stale: $(why)"
fi
# No green scheduled run at all, and the workflow is old: stale.
echo '[]' > "$C/bp_sched.json"
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(state stale:build-and-push.yml)" = OPEN ] && [ "$(nopen)" = 1 ]; then
    ok "stale: no green scheduled run in 30 days keeps stale open"
else
    fail "stale, none: $(why)"
fi

# 4. A disabled workflow; a dynamic workflow or a deleted file is not alarmed.
setup disabled
jq '.workflows |= map(if .path == ".github/workflows/docs.yml" then .state = "disabled_inactivity" else . end)
    | .workflows |= map(if .path | startswith("dynamic/") then .state = "disabled_manually" else . end)
    | .workflows += [{id: 1, name: "Gone", path: ".github/workflows/gone.yml", state: "deleted",
                      created_at: "2025-01-01T00:00:00Z"}]' "$C/workflows.json" > "$C/w.json"
mv "$C/w.json" "$C/workflows.json"
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(state disabled:docs.yml)" = OPEN ] && [ "$(nopen)" = 1 ] \
   && jq -r .body <<< "$(issue disabled:docs.yml)" | grep -q 'disabled_inactivity'; then
    ok "disabled workflow: disabled:docs.yml (dynamic and deleted workflows ignored)"
else
    fail "disabled: $(why)"
fi

# 5. main's HEAD has no ci.yml run: at 10 min nothing yet (a push run may still
# appear); at 40 min one dispatch; at 2 h 10 min ci-missing.
setup cimissing
jq -n --arg s "$HEAD_SHA" --arg t "$(iso '-10 minutes')" '{sha: $s, commit: {committer: {date: $t}}}' > "$C/head.json"
runs ci_head.json "pull_request:completed:success:$HEAD_SHA"
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(ndispatch)" = 0 ] && [ "$(nopen)" = 0 ]; then
    ok "main HEAD 10 min old without CI: no dispatch yet, no alarm"
else
    fail "young HEAD: $(why)"
fi
at "$(iso '+30 minutes')"
watch; rc1=$?
d1=$(dispatched 'ci.yml ref=main'); m1=$(state ci-missing)
at "$(iso '+90 minutes')"
watch; rc2=$?
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$d1" = 1 ] && [ "$m1" = none ] \
   && [ "$(dispatched 'ci.yml ref=main')" = 1 ] && [ "$(state ci-missing)" = OPEN ] \
   && jq -r .body <<< "$(issue ci-missing)" | grep -q "$HEAD_SHA"; then
    ok "main HEAD without CI: one ci.yml dispatch, then ci-missing"
else
    rc=$rc2; fail "ci-missing: d1=$d1 m1=$m1 $(why)"
fi
runs ci_head.json "workflow_dispatch:in_progress::$HEAD_SHA"
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(state ci-missing)" = CLOSED ]; then ok "a ci.yml run for HEAD closes ci-missing"; else fail "ci-missing close: $(why)"; fi

# 6. main CI red: ci-main; green closes it.
setup cimain
runs ci_main.json "push:completed:failure:$HEAD_SHA:$(iso '-30 minutes')" "push:completed:success:abc:$(iso '-3 hours')"
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(state ci-main)" = OPEN ] && jq -r .body <<< "$(issue ci-main)" | grep -q 'repin-images'; then
    ok "red main CI opens ci-main with the failed jobs"
else
    fail "ci-main: $(why)"
fi
runs ci_main.json "workflow_dispatch:completed:cancelled:$HEAD_SHA" "push:completed:success:$HEAD_SHA:$(iso '-10 minutes')"
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(state ci-main)" = CLOSED ]; then ok "green main CI closes ci-main (a cancelled run is skipped)"; else fail "ci-main close: $(why)"; fi

# 7. Release 0.1.76's shape: the tag exists, Docker Hub and GHCR have no image, no
# GitHub release, AUR at 0.1.75. One release re-dispatch, then the alarm; the next
# run neither re-dispatches nor comments.
setup rel76
tags "aur-v0.1.75@-5hours aur-v0.1.76@-1hours"
registry docker.io/cryptoandcoffee/k3d-gpu:0.1.75 ghcr.io/88plug/k3d-gpu:0.1.75
aur 0.1.75-1
route '^release view aur-v0\.1\.75 ' rel75.json
watch; rc1=$?
at "$(iso '+1 hour')"
watch; rc2=$?
rel=$(issue release:0.1.76)
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$(dispatched 'build-and-push.yml ref=main release=0.1.76')" = 1 ] \
   && [ "$(ndispatch)" = 1 ] && [ "$(state release:0.1.76)" = OPEN ] && [ "$(jq '.comments | length' <<< "$rel")" = 0 ] \
   && jq -r .body <<< "$rel" | grep -q 'cryptoandcoffee/k3d-gpu:0.1.76' \
   && jq -r .body <<< "$rel" | grep -q 'ghcr.io/88plug/k3d-gpu:0.1.76' \
   && jq -r .body <<< "$rel" | grep -qi 'SHA256SUMS' \
   && jq -r .body <<< "$rel" | grep -q 'AUR'; then
    ok "0.1.76 shape: exactly one release re-dispatch, then release:0.1.76"
else
    rc=$rc2; fail "0.1.76: $(why) body=$(jq -r .body <<< "$rel" | head -c 400)"
fi
# 0.1.77 then ships complete: release:0.1.76 closes as superseded; no dispatch.
tags "aur-v0.1.75@-6hours aur-v0.1.76@-2hours aur-v0.1.77@-1hours"
registry docker.io/cryptoandcoffee/k3d-gpu:0.1.75 ghcr.io/88plug/k3d-gpu:0.1.75 \
         docker.io/cryptoandcoffee/k3d-gpu:0.1.77 ghcr.io/88plug/k3d-gpu:0.1.77
aur 0.1.77-1
route '^release view aur-v0\.1\.77 ' rel77.json
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(state release:0.1.76)" = CLOSED ] && [ "$(state release:0.1.77)" = none ] && [ "$(ndispatch)" = 1 ]; then
    ok "a complete newer release closes the superseded release:0.1.76"
else
    fail "superseded: $(why)"
fi

# 8. A newer broken release lists the older broken one as information only, and a
# tag younger than 45 min is left alone. Only Docker Hub missing triggers the
# re-dispatch; GHCR alone does not (the build refuses to overwrite a published tag).
setup relinfo
tags "aur-v0.1.75@-9hours aur-v0.1.76@-8hours aur-v0.1.77@-2hours aur-v0.1.78@-20minutes"
registry docker.io/cryptoandcoffee/k3d-gpu:0.1.75 ghcr.io/88plug/k3d-gpu:0.1.75 docker.io/cryptoandcoffee/k3d-gpu:0.1.77
aur 0.1.77-1
route '^release view aur-v0\.1\.77 ' rel77.json
route '^release view aur-v0\.1\.75 ' rel75.json
watch; rc=$?
rel=$(issue release:0.1.77)
if [ "$rc" -eq 0 ] && [ "$(state release:0.1.77)" = OPEN ] && [ "$(ndispatch)" = 0 ] && [ "$(state release:0.1.78)" = none ] \
   && jq -r .body <<< "$rel" | grep -q 'ghcr.io/88plug/k3d-gpu:0.1.77' \
   && jq -r .body <<< "$rel" | grep -q '0\.1\.76' \
   && ! jq -r .body <<< "$rel" | grep -q 'cryptoandcoffee/k3d-gpu:0.1.77 '; then
    ok "GHCR-only gap: alarm, no re-dispatch; older 0.1.76 listed; a 20 min old tag skipped"
else
    fail "relinfo: $(why) body=$(jq -r .body <<< "$rel" | head -c 600)"
fi

# 9. A release workflow still running: the release check waits (no dispatch, no alarm).
setup busy
tags "aur-v0.1.75@-5hours aur-v0.1.76@-1hours"
registry docker.io/cryptoandcoffee/k3d-gpu:0.1.75 ghcr.io/88plug/k3d-gpu:0.1.75
jq -n '[{databaseId: 1, workflowName: "Auto-update, Build & Release", status: "in_progress"}]' > "$C/busy.json"
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(ndispatch)" = 0 ] && [ "$(state release:0.1.76)" = none ] && grep -q 'release check deferred' "$C/out"; then
    ok "a running release workflow defers the release check"
else
    fail "busy: $(why)"
fi

# 10. Bot PRs: an autobump PR open 73 h whose head has no green ci.yml run.
setup botpr
jq -n --arg old "$(iso '-73 hours')" --arg young "$(iso '-10 hours')" --arg s "$PR_SHA" '[
  {number: 240, headRefName: "autobump/convert-pins", headRefOid: $s, createdAt: $old,
   url: "https://github.com/88plug/k3d-gpu/pull/240", title: "chore(convert-pins): bump",
   author: {is_bot: true, login: "app/github-actions"}},
  {number: 241, headRefName: "autobump/image-digests", headRefOid: "feed", createdAt: $young,
   url: "https://github.com/88plug/k3d-gpu/pull/241", title: "young", author: {login: "app/github-actions"}},
  {number: 242, headRefName: "autobump/nri-balloons", headRefOid: "beef", createdAt: $old,
   url: "https://github.com/88plug/k3d-gpu/pull/242", title: "green", author: {login: "app/github-actions"}},
  {number: 243, headRefName: "claude/h54q-run", headRefOid: "cafe", createdAt: $old,
   url: "https://github.com/88plug/k3d-gpu/pull/243", title: "human", author: {login: "88plug"}}]' > "$C/prs.json"
runs ci_pr.json "pull_request:completed:action_required:$PR_SHA"
runs ci_green.json "workflow_dispatch:completed:success:beef"
route "^run list .*--workflow ci\\.yml .*--commit $PR_SHA" ci_pr.json
route '^run list .*--workflow ci\.yml .*--commit beef' ci_green.json
route '^run list .*--workflow ci\.yml .*--commit cafe' - 1 'must not be asked'
watch; rc=$?
b=$(issue bot-pr-stuck)
if [ "$rc" -eq 0 ] && [ "$(state bot-pr-stuck)" = OPEN ] && jq -r .body <<< "$b" | grep -q 'pull/240' \
   && ! jq -r .body <<< "$b" | grep -qE 'pull/24[123]' && ! grep -q 'must not be asked' "$C/err"; then
    ok "a 73 h autobump PR without green CI opens bot-pr-stuck (young, green and human PRs left out)"
else
    fail "bot-pr-stuck: $(why) body=$(jq -r .body <<< "$b" | head -c 400)"
fi
echo '[]' > "$C/prs.json"
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(state bot-pr-stuck)" = CLOSED ]; then ok "no stuck bot PR closes bot-pr-stuck"; else fail "bot-pr-stuck close: $(why)"; fi

# 11. simulate_red: opens simulate (assigned to 88plug); the next run without it closes it.
setup simulate
SIMULATE_RED=true watch; rc1=$?
s1=$(state simulate); who=$(jq -r '.assignees[0].login' <<< "$(issue simulate)")
SIMULATE_RED=false watch; rc2=$?
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$s1" = OPEN ] && [ "$who" = 88plug ] && [ "$(state simulate)" = CLOSED ]; then
    ok "simulate_red opens [ci-alarm] simulate; the next run closes it"
else
    rc=$rc2; fail "simulate: s1=$s1 who=$who $(why)"
fi

# 12. A gh error: watchdog-error carries the error text, the other checks still run,
# exit 0; the next clean run closes it.
setup gherror
route '^api repos/88plug/k3d-gpu/actions/workflows' - 1 'HTTP 502: Bad Gateway (https://api.github.com/repos/88plug/k3d-gpu/actions/workflows)'
runs ci_main.json "push:completed:failure:$HEAD_SHA:$(iso '-30 minutes')"
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(state watchdog-error)" = OPEN ] && jq -r .body <<< "$(issue watchdog-error)" | grep -q 'HTTP 502' \
   && [ "$(state ci-main)" = OPEN ]; then
    ok "a gh error opens watchdog-error with its text; the other checks still run"
else
    fail "watchdog-error: $(why)"
fi
route '^api repos/88plug/k3d-gpu/actions/workflows' workflows.json
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(state watchdog-error)" = CLOSED ]; then ok "a clean run closes watchdog-error"; else fail "watchdog-error close: $(why)"; fi

# 13. Issues disabled: no alarm can be filed; the run prints ::error and exits 1.
setup noissues
route '^(issue|label) ' - 1 "the '88plug/k3d-gpu' repository has disabled issues"
SIMULATE_RED=true watch; rc=$?
if [ "$rc" -eq 1 ] && grep -q '^::error title=ci-alarm simulate::' "$C/out" && grep -qi 'issues' "$C/err"; then
    ok "issues disabled: ::error annotation and exit 1"
else
    fail "issues disabled: rc=$rc out=$(tail -3 "$C/out" | tr '\n' ' ') err=$(tail -3 "$C/err" | tr '\n' ' ')"
fi

# 13b. Re-dispatches happen only once the alarm records them. Two runs in a row
# (the second stands for the run a re-dispatch of watchdog.yml starts) with red
# scheduled runs of watchdog.yml and build-and-push.yml and a newest release
# without its Docker Hub image: with issues disabled (this repository today) and
# with alarm writes failing, nothing is dispatched. Dispatching first and
# recording after let every red watchdog run re-dispatch watchdog.yml, and each
# dispatched run do the same, without end.
redsetup() {
    setup "$1"
    jq '[.[] | select(.databaseId <= 40686436167)]' "$FIX/build-and-push_schedule_runs.json" > "$C/bp_sched.json"
    jq -n --arg t "$(iso '-13 minutes')" '[{databaseId: 4201, conclusion: "failure", status: "completed",
        createdAt: $t, url: "https://github.com/88plug/k3d-gpu/actions/runs/4201", headSha: "x",
        workflowName: "Watchdog", event: "schedule"}]' > "$C/wd_sched.json"
    tags "aur-v0.1.79@-3hours aur-v0.1.80@-1hours"
}
redsetup noissues_red
route '^(issue|label) ' - 1 "the '88plug/k3d-gpu' repository has disabled issues"
watch; rc1=$?
watch; rc2=$?
if [ "$rc1" -eq 1 ] && [ "$rc2" -eq 1 ] && [ "$(ndispatch)" = 0 ] && grep -q '^::error title=ci-alarm red:watchdog.yml::' "$C/out"; then
    ok "issues disabled, red scheduled runs and a release gap: ::error, and no re-dispatch it cannot record"
else
    rc=$rc2; fail "issues disabled, red: $(why) dispatched=$(tr '\n' ',' < "$C/dispatches.log" 2>/dev/null)"
fi
redsetup alarmwrite_red
route '^(issue (create|edit|comment)|label create) ' - 1 'HTTP 502: Bad Gateway (https://api.github.com/graphql)'
watch; rc1=$?
watch; rc2=$?
if [ "$(ndispatch)" = 0 ] && [ "$rc2" -eq 1 ] && grep -q 'HTTP 502' "$C/err"; then
    ok "alarm writes failing: no re-dispatch, the error is reported"
else
    rc=$rc2; fail "alarm writes failing: rc1=$rc1 $(why) dispatched=$(tr '\n' ',' < "$C/dispatches.log" 2>/dev/null)"
fi
# Issues on: the same state re-dispatches watchdog.yml, build-and-push.yml and
# the release once each, and the second run adds no dispatch.
redsetup issues_red
watch; rc1=$?
watch; rc2=$?
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$(ndispatch)" = 3 ] && [ "$(dispatched 'watchdog.yml ref=main')" = 1 ] \
   && [ "$(dispatched 'build-and-push.yml ref=main')" = 1 ] && [ "$(dispatched 'build-and-push.yml ref=main release=0.1.80')" = 1 ] \
   && jq -r .body <<< "$(issue red:watchdog.yml)" | grep -q 'watchdog:dispatched=4201' \
   && jq -r .body <<< "$(issue release:0.1.80)" | grep -q 'watchdog:dispatched=0.1.80'; then
    ok "issues on: one re-dispatch each for the red workflows and the release, none on the next run"
else
    rc=$rc2; fail "issues on, red: rc1=$rc1 $(why) dispatched=$(tr '\n' ',' < "$C/dispatches.log" 2>/dev/null)"
fi
# A re-dispatch that fails leaves no record: the alarm says so, the next run tries again.
redsetup dispatchfail
route '^workflow run watchdog\.yml ' - 1 'HTTP 422: Workflow does not have workflow_dispatch trigger'
watch; rc1=$?
b1=$(jq -r .body <<< "$(issue red:watchdog.yml)"); e1=$(state watchdog-error)
route '^workflow run watchdog\.yml ' - 0
watch; rc2=$?
if [ "$rc1" -eq 0 ] && grep -q 'Re-dispatch failed: .*HTTP 422' <<< "$b1" && ! grep -q 'watchdog:dispatched' <<< "$b1" \
   && [ "$e1" = OPEN ] && [ "$(state watchdog-error)" = CLOSED ] \
   && jq -r .body <<< "$(issue red:watchdog.yml)" | grep -q 'watchdog:dispatched=4201'; then
    ok "a failed re-dispatch is not recorded: the alarm says so and the next run dispatches"
else
    rc=$rc2; fail "dispatch failure: rc1=$rc1 $(why) body1=$(head -c 300 <<< "$b1")"
fi
# A run that cannot list the open alarms (a transient 502 on the watchdog's own
# listing; ci_alarm still works) leaves the dispatching alarms as they are: their
# records survive, so the run after it dispatches nothing new.
redsetup listfail
watch; rc1=$?
route '^issue list .*--json number,title,body$' - 1 'HTTP 502: Bad Gateway (https://api.github.com/graphql)'
watch; rc2=$?
b2=$(jq -r .body <<< "$(issue red:watchdog.yml)")
sed -i '1d' "$C/routes.tsv"
watch; rc3=$?
if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$rc3" -eq 0 ] && [ "$(ndispatch)" = 3 ] \
   && grep -q 'watchdog:dispatched=4201' <<< "$b2" \
   && jq -r .body <<< "$(issue release:0.1.80)" | grep -q 'watchdog:dispatched=0.1.80'; then
    ok "a run that cannot list the alarms keeps their dispatch records: no second dispatch"
else
    rc=$rc3; fail "listing failure: rc1=$rc1 rc2=$rc2 $(why) dispatched=$(tr '\n' ',' < "$C/dispatches.log" 2>/dev/null)"
fi

# 13c. An autobump/* branch on a fork is not a bot PR: a fork could otherwise hold
# bot-pr-stuck open for as long as it liked.
setup forkpr
jq -n --arg old "$(iso '-73 hours')" '[
  {number: 244, headRefName: "autobump/convert-pins", headRefOid: "f0f0", createdAt: $old, isCrossRepository: true,
   url: "https://github.com/88plug/k3d-gpu/pull/244", title: "fork", author: {login: "someone"}}]' > "$C/prs.json"
route '^run list .*--workflow ci\.yml .*--commit f0f0' - 1 'must not be asked'
watch; rc=$?
if [ "$rc" -eq 0 ] && [ "$(state bot-pr-stuck)" = none ] && ! grep -q 'must not be asked' "$C/err" \
   && grep -q '^pr list .*--json [^ ]*isCrossRepository' "$C/calls.log"; then
    ok "a fork's autobump/* PR is not a stuck bot PR"
else
    fail "fork PR: $(why) prlist=$(grep '^pr list' "$C/calls.log" | head -1)"
fi

# 14. --list-scheduled: every workflow with on.schedule, with its cron period in hours.
setup listsched
watch --list-scheduled; rc=$?
if [ "$rc" -eq 0 ] && grep -qx 'build-and-push.yml 24' "$C/out" && grep -qx 'watchdog.yml 1' "$C/out"; then
    ok "--list-scheduled: build-and-push.yml 24, watchdog.yml 1"
else
    fail "--list-scheduled: rc=$rc out=$(tr '\n' ' ' < "$C/out")"
fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
