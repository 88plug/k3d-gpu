#!/usr/bin/env bash
# release_cas.sh: the auto-release.yml release body, runnable outside Actions.
# Run from a checkout of main. Env: EVENT (push|workflow_dispatch), REMOTE
# (default origin), RELEASE_CAS_BEFORE_PUSH (test hook run before the push).
set -eu
REMOTE=${REMOTE:-origin}
LATEST=$(git tag -l 'aur-v*' --sort=-v:refname | head -1)
if [ -z "$LATEST" ]; then
  NEXT="aur-v0.1.0"
else
  VER=${LATEST#aur-v}
  MAJ=$(echo "$VER" | cut -d. -f1)
  MIN=$(echo "$VER" | cut -d. -f2)
  PATCH=$(echo "$VER" | cut -d. -f3)
  NEXT="aur-v${MAJ}.${MIN}.$((PATCH+1))"
fi
VER=${NEXT#aur-v}
sed -i "s|^VERSION=\".*\"|VERSION=\"${VER}\"|" scripts/k3d-gpu
sed -i "s|^pkgver=.*|pkgver=${VER}|" packaging/aur/PKGBUILD
sed -i \
  -e "s|^\tpkgver = .*|\tpkgver = ${VER}|" \
  -e "s|#tag=aur-v.*|#tag=aur-v${VER}|" \
  packaging/aur/.SRCINFO
git add scripts/k3d-gpu packaging/aur/PKGBUILD packaging/aur/.SRCINFO
if git diff --cached --quiet; then
  echo "tracked files already at ${VER}; tagging HEAD"
else
  git commit -m "release: aur-v${VER} [skip release]"
  if [ -n "${RELEASE_CAS_BEFORE_PUSH:-}" ]; then "$RELEASE_CAS_BEFORE_PUSH"; fi
  git push "$REMOTE" HEAD:main
fi
git tag "$NEXT"
git push "$REMOTE" "$NEXT"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "tag=$NEXT" >> "$GITHUB_OUTPUT"
  echo "ver=$VER" >> "$GITHUB_OUTPUT"
fi
