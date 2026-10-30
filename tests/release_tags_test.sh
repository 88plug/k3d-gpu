#!/usr/bin/env bash
# hack/release-tags against a local bare origin over file:// (no network): the
# release build pushes the versioned tags of every release, and copies the
# floating tags (latest, ubuntu26.04, <k3s>-ubuntu26.04 and the 24.04 ones) only
# for the highest aur-v* tag on the remote at push time.
#
# Usage: tests/release_tags_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RT=${RT:-${ROOT}/hack/release-tags}
T=$(mktemp -d "${TMPDIR:-/tmp}/release_tags_test.XXXXXX")
trap 'rm -rf "$T"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
unset RELEASE_REPOS

# origin.git with tags aur-v0.1.9 and aur-v0.1.10 (sort -V, not sort), plus an
# annotated tag (its peeled ^{} line must not count) and a non-release tag.
git init -q --bare -b main "$T/origin.git"
git init -q -b main "$T/w"
( cd "$T/w" && git commit -q --allow-empty -m one && git tag aur-v0.1.9 \
  && git commit -q --allow-empty -m two && git tag aur-v0.1.10 \
  && git tag -a -m note aur-v0.1.8 HEAD~1 && git tag v9.9.9 \
  && git push -q "$T/origin.git" main --tags )
K3S=v1.37.1-k3s1
run() { ( cd "$T/w" && REMOTE="file://$T/origin.git" bash "$RT" "$@" ) > "$T/out" 2> "$T/err"; }

want_versioned="26.04 versioned cryptoandcoffee/k3d-gpu:0.1.10
26.04 versioned cryptoandcoffee/k3d-gpu:0.1.10-ubuntu26.04
24.04 versioned cryptoandcoffee/k3d-gpu:0.1.10-ubuntu24.04
26.04 versioned ghcr.io/88plug/k3d-gpu:0.1.10
26.04 versioned ghcr.io/88plug/k3d-gpu:0.1.10-ubuntu26.04
24.04 versioned ghcr.io/88plug/k3d-gpu:0.1.10-ubuntu24.04"
want_floating="26.04 floating cryptoandcoffee/k3d-gpu:latest cryptoandcoffee/k3d-gpu:0.1.10
26.04 floating cryptoandcoffee/k3d-gpu:ubuntu26.04 cryptoandcoffee/k3d-gpu:0.1.10
26.04 floating cryptoandcoffee/k3d-gpu:${K3S}-ubuntu26.04 cryptoandcoffee/k3d-gpu:0.1.10
24.04 floating cryptoandcoffee/k3d-gpu:ubuntu24.04 cryptoandcoffee/k3d-gpu:0.1.10-ubuntu24.04
24.04 floating cryptoandcoffee/k3d-gpu:${K3S}-ubuntu24.04 cryptoandcoffee/k3d-gpu:0.1.10-ubuntu24.04
26.04 floating ghcr.io/88plug/k3d-gpu:latest ghcr.io/88plug/k3d-gpu:0.1.10
26.04 floating ghcr.io/88plug/k3d-gpu:ubuntu26.04 ghcr.io/88plug/k3d-gpu:0.1.10
26.04 floating ghcr.io/88plug/k3d-gpu:${K3S}-ubuntu26.04 ghcr.io/88plug/k3d-gpu:0.1.10
24.04 floating ghcr.io/88plug/k3d-gpu:ubuntu24.04 ghcr.io/88plug/k3d-gpu:0.1.10-ubuntu24.04
24.04 floating ghcr.io/88plug/k3d-gpu:${K3S}-ubuntu24.04 ghcr.io/88plug/k3d-gpu:0.1.10-ubuntu24.04"

# 1. The highest release: versioned and floating tags.
run 0.1.10 "$K3S"; rc=$?
if [ "$rc" -eq 0 ] && diff <(sort "$T/out") <(printf '%s\n%s\n' "$want_versioned" "$want_floating" | sort) >/dev/null; then
    ok "the highest aur-v* tag gets its versioned and floating tags"
else
    fail "highest: rc=$rc $(diff <(sort "$T/out") <(printf '%s\n%s\n' "$want_versioned" "$want_floating" | sort) | head -5 | tr '\n' ' ') $(cat "$T/err")"
fi

# 2. An older release (a newer tag exists on the remote): versioned tags only.
run 0.1.9 "$K3S"; rc=$?
if [ "$rc" -eq 0 ] && diff "$T/out" <(printf '%s\n' "${want_versioned//0.1.10/0.1.9}") >/dev/null; then
    ok "an older release gets versioned tags only"
else
    fail "older: rc=$rc out=$(tr '\n' ' ' < "$T/out")"
fi

# 3. A version above every tag on the remote is not the highest tag: versioned only.
run 0.1.11 "$K3S"; rc=$?
if [ "$rc" -eq 0 ] && ! grep -q floating "$T/out" && [ "$(wc -l < "$T/out")" -eq 6 ]; then
    ok "a version that is not a tag on the remote moves no floating tag"
else
    fail "untagged: rc=$rc out=$(tr '\n' ' ' < "$T/out")"
fi

# 4. RELEASE_REPOS picks the repositories.
( cd "$T/w" && RELEASE_REPOS="example.org/a/b" REMOTE="file://$T/origin.git" bash "$RT" 0.1.10 "$K3S" ) > "$T/out" 2>&1; rc=$?
if [ "$rc" -eq 0 ] && [ "$(grep -c . "$T/out")" -eq 8 ] && ! grep -qv ' example.org/a/b:' "$T/out"; then
    ok "RELEASE_REPOS sets the repositories"
else
    fail "RELEASE_REPOS: rc=$rc out=$(tr '\n' ' ' < "$T/out")"
fi

# 5. The remote cannot be read: fail, print no tag (never fall back to floating).
( cd "$T/w" && REMOTE="file://$T/missing.git" bash "$RT" 0.1.10 "$K3S" ) > "$T/out" 2> "$T/err"; rc=$?
if [ "$rc" -ne 0 ] && [ ! -s "$T/out" ]; then
    ok "an unreadable remote fails with no tag printed"
else
    fail "unreadable remote: rc=$rc out=$(tr '\n' ' ' < "$T/out")"
fi

# 6. Bad arguments exit 2.
run 0.1 "$K3S"; rc1=$?
run 0.1.10 "1.37.1"; rc2=$?
run 0.1.10; rc3=$?
if [ "$rc1" -eq 2 ] && [ "$rc2" -eq 2 ] && [ "$rc3" -eq 2 ]; then
    ok "a bad version, k3s tag or argument count exits 2"
else
    fail "bad args: rc=$rc1/$rc2/$rc3"
fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
