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

# A GITHUB_TOKEN push cannot create or update .github/workflows/ files (no
# `workflows` permission), so no bumper writes there: hack/bump refuses a bump
# that changed .github/ (its digests hook repins only files a bumper may write),
# and freshness.yml's pr job stops before the push when the patch touches .github/.
BP="${ROOT}/.github/workflows/build-and-push.yml"
FR="${ROOT}/.github/workflows/freshness.yml"
if grep -qF 'the bump changed .github/: a bumper never writes a workflow' "${ROOT}/hack/bump" \
   && grep -qF 'not path.startswith(".github/") and classes.writable(root, path)' "${ROOT}/hack/bump"; then
    ok "hack/bump never writes .github/ and refuses a bump that changed it"
else
    fail "hack/bump lacks its .github/ refusal or its path_writable rule"
fi
if grep -qF 'git status --porcelain -- .github)' "${FR}"; then
    ok "freshness.yml's pr job checks that the patch touches no .github/ file before opening the PR"
else
    fail "freshness.yml has no 'git status --porcelain -- .github' check before the PR"
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
            # A branch built from an expression runs with a stand-in value: a matrix
            # group "grp", a known-issue follow-up "ki", an amendment row "row".
            stand_in = {"${{ matrix.component }}": "comp", "${{ matrix.group }}": "grp",
                        "${{ steps.s.outputs.followup }}": "ki", "${{ inputs.propose_amendment }}": "row"}

            def sub(v):
                v = str(v)
                for expr, val in stand_in.items():
                    v = v.replace(expr, val)
                return v
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
for b in autobump/grp autobump/ki-ki autobump/amend-row; do
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

# Alarm, watchdog and release hardening: tests/ops/workflow_checks.py reads every
# workflow with PyYAML (a second parser: hack/watchdog reads crons with its own
# regex) and prints one "ok <what>" or "FAIL <what>" line per check.
while IFS= read -r line; do
    case "${line}" in
        "ok "*) ok "${line#ok }" ;;
        *) fail "${line#FAIL }" ;;
    esac
done < <(python3 "${ROOT}/tests/ops/workflow_checks.py" "${ROOT}" 2>&1 || echo "FAIL tests/ops/workflow_checks.py exited non-zero")
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

# No version literal in a workflow: every tool pin lives in hack/ci-tools.env (or
# another file outside .github/workflows/), where a token without the `workflows`
# permission can move it. Outside `uses:` lines (action SHAs) and comments, a
# workflow names no version, ==version, checksum or digest, except the numbers below,
# which name no pin.
if out=$(python3 - "${ROOT}" 2>&1 <<'PY'
import glob, re, sys
root = sys.argv[1]
ALLOWED = [
    (r"(?i)ubuntu[ :]?2[46]\\?\.04|\*\*2[46]\.04\*\*", "the node image's Ubuntu variants this repo publishes"),
    (r"kubeconform 1\.34/1\.35", "the Kubernetes versions tests/kserve_test.sh validates, in a job name"),
    (r"e\.g\. 0\.1\.[0-9]+", "an example value in an input description"),
]
VERSION = re.compile(r"(?<![\w.-])v?[0-9]+\.[0-9]+(?:\.[0-9]+)*(?![\w.])|==\s*v?[0-9]|"
                     r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])|(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
bad = []
for path in sorted(glob.glob(root + "/.github/workflows/*.yml")):
    for n, line in enumerate(open(path), 1):
        s = re.sub(r"(^|\s)#.*$", "", line.rstrip("\n"))
        if re.match(r"^\s*(-\s+)?uses:", s):
            continue
        for a, _ in ALLOWED:
            s = re.sub(a, "", s)
        for m in VERSION.finditer(s):
            bad.append("%s:%d: %s" % (path[len(root) + 1:], n, m.group(0)))
print("\n".join(bad))
sys.exit(1 if bad else 0)
PY
); then
    ok "no version literal in .github/workflows/ outside uses: lines (tool pins live in hack/ci-tools.env)"
else
    fail "version literals in workflows; move them to hack/ci-tools.env"; sed 's/^/  | /' <<< "${out}"
fi

# A job that reads a hack/ci-tools.env key loads the file into GITHUB_ENV in a step
# before the first step that uses it, and nothing else defines the key; every key
# is used; the kubeconform test scripts read KUBECONFORM_IMAGE from the file.
if out=$(python3 - "${ROOT}" 2>&1 <<'PY'
import glob, re, sys, yaml
root = sys.argv[1]
keys = [l.split("=", 1)[0] for l in open(root + "/hack/ci-tools.env") if re.match(r"^[A-Z0-9_]+=", l)]
LOAD = re.compile(r"""grep -E '\^\[A-Z0-9_\]\+=' hack/ci-tools\.env >> "\$GITHUB_ENV\"""")
bad, used = [], set()
def uses(text, key):
    return re.search(r"\$\{\{\s*env\.%s\s*\}\}|\$\{?%s\b" % (key, key), text) is not None
for path in sorted(glob.glob(root + "/.github/workflows/*.yml")):
    doc = yaml.safe_load(open(path))
    rel = path[len(root) + 1:]
    for key in keys:
        if key in (doc.get("env") or {}):
            bad.append("%s: workflow env defines %s; load it from hack/ci-tools.env" % (rel, key))
    for job, spec in (doc.get("jobs") or {}).items():
        steps = spec.get("steps") or []
        loaded = next((i for i, st in enumerate(steps) if LOAD.search(st.get("run") or "")), None)
        for key in keys:
            if key in (spec.get("env") or {}):
                bad.append("%s %s: job env defines %s; load it from hack/ci-tools.env" % (rel, job, key))
            first = next((i for i, st in enumerate(steps) if uses(yaml.safe_dump(st), key)), None)
            if first is None:
                continue
            used.add(key)
            if loaded is None or loaded >= first:
                bad.append("%s %s: step %d uses %s before a step loads hack/ci-tools.env" % (rel, job, first, key))
for t in ("kubeconform.sh", "agent_kubeconform.sh", "share_kubeconform.sh"):
    text = open(root + "/tests/" + t).read()
    code = re.sub(r"(?m)^\s*#.*$", "", text)
    if "hack/ci-tools.env" not in code or re.search(r"KUBECONFORM_IMAGE=[\"']?[a-z]", code):
        bad.append("tests/%s: read KUBECONFORM_IMAGE from hack/ci-tools.env, with no literal of its own" % t)
    used.add("KUBECONFORM_IMAGE")
bad += ["hack/ci-tools.env: %s is used by no workflow" % k for k in keys if k not in used]
print("\n".join(bad))
sys.exit(1 if bad else 0)
PY
); then
    ok "jobs load hack/ci-tools.env before using its keys; every key is used; kubeconform scripts read it"
else
    fail "hack/ci-tools.env wiring"; sed 's/^/  | /' <<< "${out}"
fi

# ─── Freshness ───────────────────────────────────────────────────────────────
# Pins move only through PRs: build-and-push.yml is release-only (no schedule, no
# push to main), Dependabot keeps only github-actions (pins/registry.toml owns the
# rest), and neither freshness.yml nor bot-sweep.yml reads a secret.
if python3 - "${BP}" <<'PY'
import sys, yaml
wf = yaml.safe_load(open(sys.argv[1]))
on = wf.get("on", wf.get(True)) or {}
text = open(sys.argv[1]).read()
sys.exit(0 if "schedule" not in on and list(wf["jobs"]) == ["release"] and "HEAD:main" not in text else 1)
PY
then ok "build-and-push.yml is release-only: no schedule, only the release job, no push to main"
else fail "build-and-push.yml still has a schedule, a job besides release, or a push to main"; fi
if [ "$(sed -n 's/^ *- *package-ecosystem: *//p' "${ROOT}/.github/dependabot.yml" | tr -d '"'"'" | sort -u)" = github-actions ]; then
    ok "dependabot.yml holds only the github-actions ecosystem"
else fail "dependabot.yml has an ecosystem besides github-actions (one updater per pin)"; fi
for f in freshness.yml bot-sweep.yml; do
    if ! grep -q 'secrets\.' "${WF}/${f}"; then ok "${f} reads no secret"
    else fail "${f} references secrets.*: $(grep -n 'secrets\.' "${WF}/${f}" | head -3 | tr '\n' ' ')"; fi
done
# freshness.yml: 'Freshness', every 6 h at :23, one queue per group; the bump job
# is read-only with no persisted credentials; every PR job resets, opens, dispatches.
if python3 - "${FR}" <<'PY'
import sys, yaml
wf = yaml.safe_load(open(sys.argv[1]))
on = wf.get("on", wf.get(True)) or {}
jobs = wf["jobs"]
bump, pr = jobs["bump"], jobs["pr"]
co = [s for s in bump["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
checks = {
    "name": wf.get("name") == "Freshness",
    # hack/bot-merge counts a group's regenerations by group=<g> in the run title; a
    # run over every group names none (so it counts for every group)
    "run-name": "format('Freshness group={0}" in str(wf.get("run-name"))
                and "group=amend-{0}" in str(wf.get("run-name"))
                and str(wf.get("run-name")).rstrip().endswith("|| 'Freshness (every group)' }}"),
    "cron": [c["cron"] for c in on.get("schedule") or []] == ["23 */6 * * *"],
    "inputs": set((on.get("workflow_dispatch") or {}).get("inputs") or {}) == {"group", "reason", "propose_amendment"},
    "concurrency": wf.get("concurrency") == {"group": "freshness-${{ inputs.group || 'all' }}", "cancel-in-progress": False},
    "top permissions": wf.get("permissions") == {},
    "jobs": set(jobs) == {"plan", "bump", "pr", "report", "propose-amendment"},
    "timeouts and permissions": all(isinstance(j.get("timeout-minutes"), int) and "permissions" in j for j in jobs.values()),
    "bump read-only": bump["permissions"] == {"contents": "read"} and co and co[0]["with"].get("persist-credentials") is False,
    "bump runs hack/bump": any("hack/bump " in s.get("run", "") for s in bump["steps"]),
    "bump matrix": bump["strategy"].get("fail-fast") is False and bump["strategy"].get("max-parallel") == 2
                   and bump["timeout-minutes"] == 45,
    "no PR from the bump": not any("create-pull-request" in str(s.get("uses", "")) for s in bump["steps"]),
    "pr permissions": pr["permissions"] == {"contents": "write", "pull-requests": "write", "actions": "write"},
    "report always": "always()" in str(jobs["report"].get("if", "")),
    "amendment on dispatch only": jobs["propose-amendment"].get("if") == "${{ inputs.propose_amendment != '' }}",
}
bad = [k for k, v in checks.items() if not v]
print(", ".join(bad))
sys.exit(1 if bad else 0)
PY
then ok "freshness.yml: Freshness, cron 23 */6, queue per group, read-only bump, PR jobs with actions: write"
else fail "freshness.yml wiring"; fi
# The pr job dispatches CI, and the Canary only when canary.yml exists on the branch.
python3 - "${FR}" > "${T}/canary_step.sh" <<'PY'
import sys, yaml
steps = yaml.safe_load(open(sys.argv[1]))["jobs"]["pr"]["steps"]
print(next(s["run"] for s in steps if s.get("name") == "Dispatch CI (and the Canary) on the bump PR's branch"))
PY
canary_case() {  # canary_case <with|without>: the bot-ci-dispatch calls the step makes
    local d="${T}/canary-$1"
    mkdir -p "${d}/hack" "${d}/.github/workflows"
    printf '#!/bin/sh\necho "$*" >> "%s/calls"\n' "${d}" > "${d}/hack/bot-ci-dispatch"
    chmod +x "${d}/hack/bot-ci-dispatch"
    [ "$1" = with ] && : > "${d}/.github/workflows/canary.yml"
    (cd "${d}" && GROUP=grp bash -eo pipefail "${T}/canary_step.sh" > /dev/null 2>&1) || echo "step-failed"
    cat "${d}/calls" 2>/dev/null
}
if [ "$(canary_case with | tr '\n' ';')" = "--branch autobump/grp;--branch autobump/grp --workflow canary.yml;" ]; then
    ok "pr job: ci.yml and canary.yml dispatched when canary.yml exists"
else fail "pr job with canary.yml: $(canary_case with | tr '\n' ';')"; fi
if [ "$(canary_case without | tr '\n' ';')" = "--branch autobump/grp;" ]; then
    ok "pr job: only ci.yml dispatched when there is no canary.yml"
else fail "pr job without canary.yml: $(canary_case without | tr '\n' ';')"; fi
# bot-sweep's known-issues job: hourly with the sweep, actions write; for each newly
# fixed issue (hack/pins issue-groups) it dispatches Freshness once per group,
# unless an open autobump/<group> PR already records that issue. (KI is assembled so
# that this file carries no marker hack/known-issues would read.)
KI=known-issue
python3 - "${WF}/bot-sweep.yml" > "${T}/ki_step.sh" <<'PY'
import sys, yaml
job = yaml.safe_load(open(sys.argv[1]))["jobs"]["known-issues"]
ok = job.get("permissions") == {"actions": "write", "contents": "read", "pull-requests": "read"} \
    and isinstance(job.get("timeout-minutes"), int)
print([s for s in job["steps"] if "run" in s][-1]["run"] if ok else "exit 9")
PY
ki_case() {  # ki_case <name> <issue-groups output> <open PR bodies JSON>: the dispatches made
    local d="${T}/ki-$1"
    mkdir -p "${d}/hack" "${d}/bin"
    printf '#!/bin/sh\necho "[]"\n' > "${d}/hack/known-issues"
    printf '#!/bin/sh\nprintf "%s"\n' "$2" > "${d}/hack/pins"
    cat > "${d}/bin/gh" <<GH
#!/usr/bin/env bash
case "\$1 \$2" in
  "pr list") q=; while [ \$# -gt 0 ]; do case \$1 in --jq) q=\$2; shift 2;; *) shift;; esac; done
             echo '$3' | jq -r "\$q";;
  "workflow run") shift 2; echo "\$*" >> "${d}/dispatched";;
esac
GH
    chmod +x "${d}/hack/known-issues" "${d}/hack/pins" "${d}/bin/gh"
    (cd "${d}" && PATH="${d}/bin:${PATH}" RUNNER_TEMP="${d}" bash -eo pipefail "${T}/ki_step.sh" > "${d}/log" 2>&1) \
        || echo "step-failed: $(cat "${d}/log")"
    cat "${d}/dispatched" 2>/dev/null
}
got=$(ki_case new 'vllm-45844 vllm-cpu\nvllm-45844 llama-cpp\n' '[]' | tr '\n' ';')
if [ "${got}" = "freshness.yml --ref main -f group=vllm-cpu -f reason=${KI}:vllm-45844;freshness.yml --ref main -f group=llama-cpp -f reason=${KI}:vllm-45844;" ]; then
    ok "bot-sweep known-issues: one Freshness dispatch per group of a newly fixed issue"
else fail "bot-sweep known-issues dispatches: ${got}"; fi
got=$(ki_case open 'vllm-45844 vllm-cpu\n' "[{\"body\":\"Moves ... reason \`${KI}:vllm-45844\`\"}]" | tr '\n' ';')
if [ -z "${got}" ]; then ok "bot-sweep known-issues: no dispatch while an open PR records the issue"
else fail "bot-sweep known-issues dispatched despite an open PR: ${got}"; fi
got=$(ki_case none '' '[]' | tr '\n' ';')
if [ -z "${got}" ]; then ok "bot-sweep known-issues: nothing newly fixed, no dispatch"
else fail "bot-sweep known-issues with nothing to do: ${got}"; fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
