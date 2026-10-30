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
# create-pull-request resets an existing digest branch onto main and force-pushes
# it; GitHub refuses that GITHUB_TOKEN push when the branch's workflow files differ
# from main's, though the digest commit touches none. A step before the action,
# under the same if:, deletes such a branch so the action cuts a fresh one off main.
# Its script then runs against a local origin over file:// (no network).
T=$(mktemp -d "${TMPDIR:-/tmp}/ci_wiring.XXXXXX")
trap 'rm -rf "$T"' EXIT
if python3 - "${BP}" "${T}/drop.sh" <<'PY'
import sys, yaml
steps = yaml.safe_load(open(sys.argv[1]))["jobs"]["repin-images"]["steps"]
cpr = next(i for i, s in enumerate(steps)
           if str(s.get("uses", "")).startswith("peter-evans/create-pull-request@"))
branch = steps[cpr]["with"]["branch"]
drop = [s for s in steps[:cpr] if f"git push origin --delete {branch}" in s.get("run", "")]
if len(drop) != 1 or drop[0].get("if") != steps[cpr].get("if"):
    sys.exit(1)
open(sys.argv[2], "w").write(drop[0]["run"])
PY
then
    ok "repin-images deletes a stale digest branch before create-pull-request, under its if:"
else
    fail "repin-images has no step before create-pull-request, under its if:, that runs 'git push origin --delete autobump/image-digests'"
fi
# $1: absent (no digest branch), same (main moved, no workflow change), differ
# (main changed a workflow after the branch was cut). Prints the branch's state
# on origin after the step's script ran in a depth-1 checkout of main.
drop_case() {
    local d="${T}/$1" b=autobump/image-digests
    (
        set -e
        export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
        export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
        git init -q --bare -b main "$d/origin.git"
        git init -q -b main "$d/seed"
        cd "$d/seed"
        mkdir -p .github/workflows
        echo v1 > .github/workflows/ci.yml
        echo 1 > images.lock
        git add .github/workflows/ci.yml images.lock
        git commit -qm base
        git push -q "$d/origin.git" main
        if [ "$1" != absent ]; then
            git switch -qc "$b"
            echo 2 > images.lock
            git commit -qam digests
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
        bash --noprofile --norc -eo pipefail "${T}/drop.sh" >"$d/log" 2>&1
    ) || { echo "script-failed"; return; }
    if [ -n "$(git ls-remote --heads "$d/origin.git" "refs/heads/$b")" ]; then echo present; else echo gone; fi
}
for c in absent:gone same:present differ:gone; do
    got=$(drop_case "${c%%:*}")
    if [ "${got}" = "${c#*:}" ]; then
        ok "stale-branch step, ${c%%:*}: digest branch ${got}"
    else
        fail "stale-branch step, ${c%%:*}: digest branch ${got}, want ${c#*:}"
    fi
done
# The release job builds the 24.04 variant from the Dockerfile's pin, which the
# nightly moves; a literal pin in the workflow would never move.
if grep -qF "grep '^ARG UBUNTU_2404_IMAGE=' Dockerfile" "${BP}" \
   && grep -qF 'UBUNTU_IMAGE=${{ steps.current.outputs.ubuntu2404 }}' "${BP}"; then
    ok "release builds the 24.04 variant from the Dockerfile's UBUNTU_2404_IMAGE"
else
    fail "release does not read UBUNTU_2404_IMAGE from the Dockerfile for the 24.04 variant"
fi

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

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
