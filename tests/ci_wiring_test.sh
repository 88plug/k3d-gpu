#!/usr/bin/env bash
# Every test suite in tests/ must run in CI. A suite counts as wired when a
# workflow names it, or when a suite a workflow names runs it (tune_test.sh
# runs tune_test.py). The fuzz properties and the share/ manifest schema check
# must also run from a workflow.
#
# Usage: tests/ci_wiring_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

# Scripts under tests/ that a workflow names.
direct=$(cat "${ROOT}"/.github/workflows/*.yml | grep -oE 'tests/[A-Za-z0-9_./-]+\.(sh|py)' | sort -u)

wired() {
    local rel=$1 runner
    grep -qxF "${rel}" <<< "${direct}" && return 0
    while read -r runner; do
        [ -f "${ROOT}/${runner}" ] && grep -qF "${rel##*/}" "${ROOT}/${runner}" && return 0
    done <<< "${direct}"
    return 1
}

for f in "${ROOT}"/tests/*_test.sh "${ROOT}"/tests/*_test.py "${ROOT}"/tests/fuzz/props.py \
         "${ROOT}"/tests/share_kubeconform.sh; do
    rel=${f#"${ROOT}/"}
    if [ -f "${f}" ] && wired "${rel}"; then ok "${rel} runs in CI"; else fail "${rel} is not run by any workflow"; fi
done

# share_kubeconform.sh validates share/*.yml as a whole (glob, not a file list).
if grep -qF '"${ROOT}"/share/*.yml' "${ROOT}/tests/share_kubeconform.sh" 2>/dev/null; then
    ok "share_kubeconform.sh covers every share/*.yml"
else
    fail "share_kubeconform.sh does not cover every share/*.yml"
fi

# auto-release.yml: main and the tag move in one atomic push from a fresh main.
AR="${ROOT}/.github/workflows/auto-release.yml" RC="${ROOT}/hack/release_cas.sh"
if grep -qE '^\s*group: auto-release-' "${AR}" && grep -qE '^\s*cancel-in-progress: false' "${AR}"; then
    ok "auto-release.yml has a concurrency group without cancel-in-progress"
else
    fail "auto-release.yml lacks concurrency group auto-release-* with cancel-in-progress: false"
fi
if grep -qE '^\s*ref: main' "${AR}"; then ok "auto-release.yml checks out main"; else fail "auto-release.yml does not check out ref: main"; fi
if grep -qF 'hack/release_cas.sh' "${AR}" && ! grep -qE 'git push' "${AR}"; then
    ok "auto-release.yml pushes only through hack/release_cas.sh"
else
    fail "auto-release.yml has an inline git push or does not run hack/release_cas.sh"
fi
if grep -E 'push --atomic' "${RC}" 2>/dev/null | grep -qF 'HEAD:refs/heads/main' \
   && grep -E 'push --atomic' "${RC}" | grep -qF 'refs/tags/'; then
    ok "release_cas.sh pushes main and the tag in one --atomic push"
else
    fail "release_cas.sh does not push HEAD:refs/heads/main and refs/tags/ in one --atomic push"
fi
if ! cat "${AR}" "${RC}" 2>/dev/null | grep -qF 'tracked files already at'; then
    ok "no tag-HEAD fallback for a release commit without its tag"
else
    fail "the 'tracked files already at' tag-HEAD fallback is still present"
fi

# build-and-push.yml repin-images: its GITHUB_TOKEN push cannot create or update
# .github/workflows/ files (no `workflows` permission), so the job never writes
# there, and it stops before the push when a workflow file changed anyway.
BP="${ROOT}/.github/workflows/build-and-push.yml"
if grep -qF "scripts/images-lock repin --exclude '.github/workflows/*'" "${BP}"; then
    ok "repin-images runs repin with --exclude '.github/workflows/*'"
else
    fail "repin-images does not run scripts/images-lock repin --exclude '.github/workflows/*'"
fi
if grep -qF 'git status --porcelain -- .github/workflows/' "${BP}"; then
    ok "repin-images checks that no workflow file changed before opening the PR"
else
    fail "repin-images has no 'git status --porcelain -- .github/workflows/' check"
fi
# Every create-pull-request step (a GITHUB_TOKEN PR) sits between two steps under
# its if:. Before it, hack/autobump-branch-reset deletes the branch when its
# workflow files differ from main's: create-pull-request resets the branch onto
# main and force-pushes it, and GitHub refuses that GITHUB_TOKEN push (no
# `workflows` permission) though the bump commit touches no workflow. After it,
# hack/bot-ci-dispatch dispatches ci.yml on the branch: the PR's own pull_request
# runs wait for approval, a workflow_dispatch run does not. The job grants
# actions: write for the dispatch. Each reset step's script then runs against a
# local origin over file:// (no network), for every such branch.
T=$(mktemp -d "${TMPDIR:-/tmp}/ci_wiring.XXXXXX")
trap 'rm -rf "$T"' EXIT
if python3 - "${ROOT}" "${T}" > "${T}/cases" 2> "${T}/cpr.err" <<'PY'
import glob, os, sys, yaml
root, out = sys.argv[1], sys.argv[2]
errs, n = [], 0
for path in sorted(glob.glob(os.path.join(root, ".github/workflows/*.yml"))):
    jobs = (yaml.safe_load(open(path)) or {}).get("jobs") or {}
    for jid, job in jobs.items():
        steps = job.get("steps") or []
        for i, s in enumerate(steps):
            if not str(s.get("uses", "")).startswith("peter-evans/create-pull-request@"):
                continue
            n += 1
            where = f"{os.path.basename(path)} {jid}"
            cond = s.get("if")
            reset = [p for p in steps[:i] if "hack/autobump-branch-reset" in p.get("run", "") and p.get("if") == cond]
            disp = [p for p in steps[i + 1:] if "hack/bot-ci-dispatch --branch" in p.get("run", "") and p.get("if") == cond]
            if len(reset) != 1:
                errs.append(f"{where}: no single step before create-pull-request, under its if:, that runs hack/autobump-branch-reset")
            if len(disp) != 1 or "GH_TOKEN" not in (disp[0].get("env") or {}):
                errs.append(f"{where}: no single step after create-pull-request, under its if:, that runs hack/bot-ci-dispatch --branch with GH_TOKEN")
            if (job.get("permissions") or {}).get("actions") != "write":
                errs.append(f"{where}: the job lacks permissions actions: write (bot-ci-dispatch dispatches)")
            if len(reset) != 1:
                continue
            # A matrix branch (autobump/${{ matrix.component }}) runs as component "comp".
            sub = lambda v: str(v).replace("${{ matrix.component }}", "comp")
            branch = sub(s["with"]["branch"])
            env = {k: sub(v) for k, v in {**(job.get("env") or {}), **(reset[0].get("env") or {})}.items()}
            script = sub(reset[0]["run"])
            if "${{" in branch + script + "".join(env.values()):
                errs.append(f"{where}: the reset step uses an expression this test cannot evaluate")
                continue
            d = os.path.join(out, f"case{n}")
            os.makedirs(d)
            open(os.path.join(d, "run.sh"), "w").write(script)
            open(os.path.join(d, "env"), "w").write("".join(f"export {k}='{v}'\n" for k, v in env.items()))
            print(f"{where}\t{branch}\t{d}")
if errs or n == 0:
    sys.stderr.write("\n".join(errs or ["no create-pull-request step found"]) + "\n")
    sys.exit(1)
PY
then
    ok "every create-pull-request step: autobump-branch-reset before, bot-ci-dispatch after, under its if:, actions: write"
else
    fail "create-pull-request wiring: $(tr '\n' ';' < "${T}/cpr.err")"
fi
for b in autobump/image-digests autobump/convert-pins autobump/comp; do
    if cut -f2 "${T}/cases" | grep -qxF "$b"; then ok "a reset step covers ${b}"; else fail "no reset step covers ${b}"; fi
done
# $1: absent (no branch), same (main moved, no workflow change), differ (main
# changed a workflow after the branch was cut); $2 the branch; $3 the case dir.
# Prints the branch's state on origin after the step's script ran in a depth-1
# checkout of main that carries hack/autobump-branch-reset.
drop_case() {
    local d="$3/$1" b=$2
    (
        set -e
        export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
        export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
        git init -q --bare -b main "$d/origin.git"
        git init -q -b main "$d/seed"
        cd "$d/seed"
        mkdir -p .github/workflows hack
        echo v1 > .github/workflows/ci.yml
        echo 1 > images.lock
        cp "${ROOT}/hack/autobump-branch-reset" hack/
        git add .github/workflows/ci.yml images.lock hack/autobump-branch-reset
        git commit -qm base
        git push -q "$d/origin.git" main
        if [ "$1" != absent ]; then
            git switch -qc "$b"
            echo 2 > images.lock
            git commit -qam bump
            git push -q "$d/origin.git" "$b"
            git switch -q main
        fi
        echo docs > README
        git add README
        git commit -qm docs
        if [ "$1" = differ ]; then
            echo v2 > .github/workflows/ci.yml
            git commit -qam workflow
        fi
        git push -q "$d/origin.git" main
        git clone -q --depth=1 --branch main "file://$d/origin.git" "$d/work"
        cd "$d/work"
        # shellcheck disable=SC1091
        . "$3/env"
        bash --noprofile --norc -eo pipefail "$3/run.sh" >"$d/log" 2>&1
    ) || { echo "script-failed"; return; }
    if [ -n "$(git ls-remote --heads "$d/origin.git" "refs/heads/$b")" ]; then echo present; else echo gone; fi
}
while IFS=$'\t' read -r where b dir; do
    for c in absent:gone same:present differ:gone; do
        got=$(drop_case "${c%%:*}" "$b" "$dir")
        if [ "${got}" = "${c#*:}" ]; then
            ok "${where} reset step, ${c%%:*}: ${b} ${got}"
        else
            fail "${where} reset step, ${c%%:*}: ${b} ${got}, want ${c#*:}"
        fi
    done
done < "${T}/cases"
# autobump-branch-reset deletes only autobump/ branches (usage error, exit 2).
for b in main claude/x autobump/ ''; do
    (cd "$T" && bash "${ROOT}/hack/autobump-branch-reset" "$b" >/dev/null 2>&1); rc=$?
    if [ "$rc" -eq 2 ]; then ok "autobump-branch-reset refuses '${b}'"
    else fail "autobump-branch-reset '${b}': exit ${rc}, want 2"; fi
done
# The release job builds the 24.04 variant from the Dockerfile's pin, which the
# nightly moves; a literal pin in the workflow would never move.
if grep -qF "grep '^ARG UBUNTU_2404_IMAGE=' Dockerfile" "${BP}" \
   && grep -qF 'UBUNTU_IMAGE=${{ steps.current.outputs.ubuntu2404 }}' "${BP}"; then
    ok "release builds the 24.04 variant from the Dockerfile's UBUNTU_2404_IMAGE"
else
    fail "release does not read UBUNTU_2404_IMAGE from the Dockerfile for the 24.04 variant"
fi

# ─── CI on bot PRs and alarm surfacing ───────────────────────────────────────
WF="${ROOT}/.github/workflows"
# ci.yml runs on workflow_dispatch: hack/bot-ci-dispatch runs it on bot PR branches.
# (PyYAML reads the bare key `on` as True.)
if python3 - "${WF}/ci.yml" <<'PY'
import sys, yaml
wf = yaml.safe_load(open(sys.argv[1]))
on = wf.get("on", wf.get(True))
sys.exit(0 if isinstance(on, dict) and "workflow_dispatch" in on else 1)
PY
then ok "ci.yml has a workflow_dispatch trigger"; else fail "ci.yml has no workflow_dispatch trigger"; fi
# The dependabot auto-merge workflow ran `gh pr merge --auto` on an unprotected main
# (no required checks, so nothing ever merged) and held a run on every bot PR.
if [ ! -e "${WF}/dependabot-auto-merge.yml" ]; then ok "dependabot-auto-merge.yml is gone"
else fail "dependabot-auto-merge.yml still exists"; fi
# bot-sweep.yml: hourly off the hour, never two at once, job ci-fill runs bot-ci-dispatch.
if python3 - "${WF}/bot-sweep.yml" <<'PY'
import sys, yaml
wf = yaml.safe_load(open(sys.argv[1]))
on = wf.get("on", wf.get(True)) or {}
crons = [c["cron"] for c in on.get("schedule") or []]
conc = wf.get("concurrency") or {}
job = (wf.get("jobs") or {}).get("ci-fill") or {}
perms = job.get("permissions") or {}
runs = " ".join(s.get("run", "") for s in job.get("steps") or [])
checks = [
    wf.get("name") == "Bot sweep",
    crons == ["13 * * * *"], "workflow_dispatch" in on,
    conc.get("group") == "bot-sweep", conc.get("cancel-in-progress") is False,
    isinstance(job.get("timeout-minutes"), int),
    perms == {"actions": "write", "contents": "read", "pull-requests": "read"},
    "hack/bot-ci-dispatch" in runs,
    all("timeout-minutes" in j and "permissions" in j for j in wf["jobs"].values()),
]
sys.exit(0 if all(checks) else 1)
PY
then ok "bot-sweep.yml: 'Bot sweep', cron 13 * * * *, group bot-sweep, job ci-fill runs hack/bot-ci-dispatch"
else fail "bot-sweep.yml is missing or its name, schedule, concurrency or ci-fill job is wrong"; fi

# ci.yml's last job, alarms: never fails, reads issues and actions only, and warns
# on every open ci-alarm issue and on a missing or stale Watchdog. Its script runs
# against a fake gh.
if python3 - "${WF}/ci.yml" "${T}/alarms.sh" <<'PY'
import sys, yaml
jobs = yaml.safe_load(open(sys.argv[1]))["jobs"]
job = jobs.get("alarms") or {}
ok = (list(jobs)[-1] == "alarms" and job.get("continue-on-error") is True
      and isinstance(job.get("timeout-minutes"), int)
      and job.get("permissions") == {"issues": "read", "actions": "read"}
      and len(job.get("steps") or []) == 1)
if ok:
    open(sys.argv[2], "w").write(job["steps"][0]["run"])
sys.exit(0 if ok else 1)
PY
then ok "ci.yml ends with job alarms: continue-on-error, timeout, issues+actions read"
else fail "ci.yml's last job is not alarms with continue-on-error, timeout-minutes, issues/actions read and one step"; fi
mkdir -p "${T}/abin"
cat > "${T}/abin/gh" <<'STUB'
#!/usr/bin/env bash
A=$AFIX
case "$1 $2" in
  "issue list") f=issues;; "workflow list") f=workflows;; "run list") f=runs;; *) exit 0;;
esac
[ -f "$A/$f.fail" ] && { echo "HTTP 502" >&2; exit 1; }
q=; while [ $# -gt 0 ]; do case $1 in -q|--jq) q=$2; shift 2;; *) shift;; esac; done
if [ -n "$q" ]; then jq -r "$q" "$A/$f.json"; else cat "$A/$f.json"; fi
STUB
chmod +x "${T}/abin/gh"
ALARM7='{"number":7,"title":"[ci-alarm] nightly: Auto-update red","url":"https://github.com/o/r/issues/7","labels":[{"name":"ci-alarm"}]}'
ALARM8='{"number":8,"title":"[ci-alarm] release: 100% missing","url":"https://github.com/o/r/issues/8","labels":[{"name":"ci-alarm"},{"name":"alarm-ack"}]}'
WATCHDOG='[{"name":"Watchdog","path":".github/workflows/watchdog.yml","state":"active"}]'
ago() { date -u -d "-$1 hours" +%Y-%m-%dT%H:%M:%SZ; }
# alarm_case NAME ISSUES WORKFLOWS RUNS [fail...]: runs the alarms script; sets AOUT, ARC.
alarm_case() {
    AFIX="${T}/alarm-$1"; export AFIX; mkdir -p "$AFIX"
    echo "$2" > "$AFIX/issues.json"; echo "$3" > "$AFIX/workflows.json"; echo "$4" > "$AFIX/runs.json"
    local f; for f in "${@:5}"; do touch "$AFIX/$f.fail"; done
    AOUT=$(PATH="${T}/abin:$PATH" bash --noprofile --norc -eo pipefail "${T}/alarms.sh" 2>&1); ARC=$?
}
alarm_case two "[$ALARM7,$ALARM8]" "$WATCHDOG" "[{\"updatedAt\":\"$(ago 1)\"}]"
if [ "$ARC" -eq 0 ] && [ "$(grep -c '^::warning::' <<<"$AOUT")" -eq 2 ] \
   && grep '^::warning::.*#7' <<<"$AOUT" | grep -F 'https://github.com/o/r/issues/7' | grep -qF 'gh issue edit 7 --add-label alarm-ack' \
   && grep '^::warning::.*#8' <<<"$AOUT" | grep -F 'https://github.com/o/r/issues/8' | grep -qF '100%25 missing' \
   && ! grep -q 'Watchdog' <<<"$AOUT"; then
    ok "alarms: one warning per open alarm with its URL and ack state; a fresh Watchdog is quiet"
else fail "alarms two: rc=$ARC out=$(tr '\n' ';' <<<"$AOUT")"; fi
alarm_case none '[]' '[]' '[]'
if [ "$ARC" -eq 0 ] && [ "$(grep -c '^::warning::' <<<"$AOUT")" -eq 1 ] && grep -q '^::warning::.*Watchdog' <<<"$AOUT"; then
    ok "alarms: a missing Watchdog workflow warns"
else fail "alarms none: rc=$ARC out=$(tr '\n' ';' <<<"$AOUT")"; fi
alarm_case stale '[]' "$WATCHDOG" "[{\"updatedAt\":\"$(ago 5)\"}]"
if [ "$ARC" -eq 0 ] && grep -q '^::warning::.*Watchdog.*3 h' <<<"$AOUT"; then ok "alarms: a Watchdog success over 3 h old warns"
else fail "alarms stale: rc=$ARC out=$(tr '\n' ';' <<<"$AOUT")"; fi
alarm_case never '[]' "$WATCHDOG" '[]'
if [ "$ARC" -eq 0 ] && grep -q '^::warning::.*Watchdog' <<<"$AOUT"; then ok "alarms: a Watchdog with no success warns"
else fail "alarms never: rc=$ARC out=$(tr '\n' ';' <<<"$AOUT")"; fi
alarm_case disabled '[]' '[{"name":"Watchdog","path":".github/workflows/watchdog.yml","state":"disabled_manually"}]' "[{\"updatedAt\":\"$(ago 1)\"}]"
if [ "$ARC" -eq 0 ] && grep -q '^::warning::.*Watchdog.*disabled_manually' <<<"$AOUT"; then ok "alarms: a disabled Watchdog warns"
else fail "alarms disabled: rc=$ARC out=$(tr '\n' ';' <<<"$AOUT")"; fi
alarm_case gh_down '[]' '[]' '[]' issues workflows runs
if [ "$ARC" -eq 0 ] && [ "$(grep -c '^::warning::' <<<"$AOUT")" -eq 2 ]; then ok "alarms: gh failing warns and still exits 0"
else fail "alarms gh_down: rc=$ARC out=$(tr '\n' ';' <<<"$AOUT")"; fi

# bot-sweep.yml job merge runs hack/bot-merge after ci-fill. Its token can merge,
# so it checks out main only (never a PR's code), and the script never checks out,
# switches to or resets onto another ref.
BS="${ROOT}/.github/workflows/bot-sweep.yml"
if python3 - "${BS}" 2>/dev/null <<'PY'
import sys, yaml
jobs = yaml.safe_load(open(sys.argv[1]))["jobs"]
needs = jobs["merge"].get("needs", [])
sys.exit(0 if "ci-fill" in jobs and "ci-fill" in ([needs] if isinstance(needs, str) else needs) else 1)
PY
then
    ok "bot-sweep.yml job merge needs ci-fill"
else
    fail "bot-sweep.yml has no job merge that needs ci-fill"
fi
if python3 - "${BS}" 2>/dev/null <<'PY'
import sys, yaml
steps = yaml.safe_load(open(sys.argv[1]))["jobs"]["merge"]["steps"]
co = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
text = yaml.safe_dump(steps)
sys.exit(0 if co and all((s.get("with") or {}).get("ref") == "main" for s in co)
         and not any(k in text for k in ("github.head_ref", "github.event", "refs/pull/", "pull_request"))
         and any("hack/bot-merge" in s.get("run", "") for s in steps) else 1)
PY
then
    ok "bot-sweep.yml job merge checks out main only and runs hack/bot-merge"
else
    fail "bot-sweep.yml job merge checks out a ref other than main, reads a PR ref, or does not run hack/bot-merge"
fi
if [ -f "${ROOT}/hack/bot-merge" ] && ! grep -qE 'git (checkout|switch|worktree|reset)|gh pr checkout' "${ROOT}/hack/bot-merge"; then
    ok "hack/bot-merge never checks out a ref"
else
    fail "hack/bot-merge is missing or runs git checkout/switch/worktree/reset or gh pr checkout"
fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
