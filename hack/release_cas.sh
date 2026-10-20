#!/usr/bin/env bash
# release_cas.sh: cut the next aur-vX.Y.Z release on main (auto-release.yml).
#
# Each attempt starts from a fresh origin/main, stamps VERSION, pkgver and
# .SRCINFO, commits, and pushes main and the tag in one `git push --atomic`.
# That push is the commit point: origin either fast-forwards main AND creates
# the tag, or changes neither. A lost race (main moved, or the tag exists)
# retries from the new tip, up to RELEASE_CAS_ATTEMPTS (default 5), then exits 1.
#
# EVENT=push releases only when a commit above the last tag touches packaging/,
# scripts/, share/ or Dockerfile without [skip release]; a run that finds an
# earlier run already released its commits exits 0 with no tag. Any other
# EVENT (workflow_dispatch) always releases.
#
# Env: EVENT, REMOTE (default origin), RELEASE_CAS_ATTEMPTS,
# RELEASE_CAS_BEFORE_PUSH (test hook run before each push), GITHUB_OUTPUT
# (receives tag= and ver= when a release is cut).
set -eu
REMOTE=${REMOTE:-origin}
EVENT=${EVENT:-push}
TAG=
for attempt in $(seq 1 "${RELEASE_CAS_ATTEMPTS:-5}"); do
  [ -n "$TAG" ] && git tag -d "$TAG" >/dev/null
  TAG=
  git fetch -q --tags --force "$REMOTE" main
  git reset -q --hard FETCH_HEAD
  LATEST=$(git tag -l 'aur-v*' --sort=-v:refname | head -1)
  if [ "$EVENT" = push ] && [ -n "$LATEST" ]; then
    n=$(git log --format=%s "$LATEST..HEAD" -- packaging scripts share Dockerfile | grep -vcF '[skip release]' || true)
    if [ "$n" -eq 0 ]; then echo "nothing to release above $LATEST"; exit 0; fi
  fi
  # Next patch above both the last tag and main's VERSION, so the stamp always changes the tree.
  FILE=$(sed -n 's/^VERSION="\([0-9.]*\)".*/\1/p' scripts/k3d-gpu)
  BASE=$(printf '%s\n' "${LATEST#aur-v}" "$FILE" | grep . | sort -V | tail -1)
  if [ -z "$BASE" ]; then VER=0.1.0; else VER=${BASE%.*}.$(( ${BASE##*.} + 1 )); fi
  sed -i "s|^VERSION=\".*\"|VERSION=\"${VER}\"|" scripts/k3d-gpu
  sed -i "s|^pkgver=.*|pkgver=${VER}|" packaging/aur/PKGBUILD
  sed -i \
    -e "s|^\tpkgver = .*|\tpkgver = ${VER}|" \
    -e "s|#tag=aur-v.*|#tag=aur-v${VER}|" \
    packaging/aur/.SRCINFO
  git add scripts/k3d-gpu packaging/aur/PKGBUILD packaging/aur/.SRCINFO
  git commit -q -m "release: aur-v${VER} [skip release]"
  TAG=aur-v${VER}
  git tag "$TAG"
  if [ -n "${RELEASE_CAS_BEFORE_PUSH:-}" ]; then "$RELEASE_CAS_BEFORE_PUSH"; fi
  if git push --atomic -q "$REMOTE" HEAD:refs/heads/main "refs/tags/$TAG"; then
    echo "released $TAG at $(git rev-parse HEAD) (attempt $attempt)"
    if [ -n "${GITHUB_OUTPUT:-}" ]; then
      echo "tag=$TAG" >> "$GITHUB_OUTPUT"
      echo "ver=$VER" >> "$GITHUB_OUTPUT"
    fi
    exit 0
  fi
  echo "attempt $attempt: $REMOTE moved main or holds $TAG; retrying from its tip"
done
[ -n "$TAG" ] && git tag -d "$TAG" >/dev/null
echo "no release after ${RELEASE_CAS_ATTEMPTS:-5} attempts"; exit 1
