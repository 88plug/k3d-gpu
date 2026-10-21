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

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
