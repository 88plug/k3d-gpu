#!/usr/bin/env bash
# release_cas.sh: cut the next aur-vX.Y.Z release on main (auto-release.yml).
#
# Each attempt starts from a fresh origin/main, stamps VERSION, pkgver and
# .SRCINFO, regenerates console/ledger-rules.json (committing only if that
# changes the tree), and pushes main and the
# tag in one `git push --atomic`. That push is the commit point. A lost race
# (main moved, or the tag exists since the fetch) is a ref-lock or
# non-fast-forward rejection; on that class GitHub updated neither ref in a
# branch-ref spike (not yet observed on a live release push). The run retries
# from the new tip, up to RELEASE_CAS_ATTEMPTS (default 5), then exits 1.
# GitHub does not apply --atomic to every rejection class (a hidden-ref
# rejection still let a sibling branch ref land), so a push that lands main
# without the tag stays possible; the next run then finds main's VERSION above
# the last tag and tags that commit in place.
#
# Version: main's VERSION when it is above the last tag (a manual bump, or a
# release commit whose tag did not land), else one patch above the last tag.
#
# EVENT=push releases when main's VERSION is above the last tag, or when a
# commit above the last tag touches a path main's hack/release-paths lists (what
# nfpm, the AUR package, the formula and the image ship) and carries no
# [skip release] in its message; otherwise it prints "nothing to release" and
# exits 0 with no tag. auto-release.yml passes EVENT=push for pushes and for
# mode=changed dispatches. Any other EVENT (a forced workflow_dispatch) always
# releases.
#
# Env: EVENT, REMOTE (default origin), RELEASE_CAS_ATTEMPTS, GITHUB_OUTPUT
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
  LAST=${LATEST#aur-v}
  FILE=$(sed -n 's/^VERSION="\([0-9.]*\)".*/\1/p' scripts/k3d-gpu)
  if [ -n "$FILE" ] && [ "$FILE" != "$LAST" ] \
     && [ "$(printf '%s\n' "$LAST" "$FILE" | sort -V | tail -1)" = "$FILE" ]; then
    VER=$FILE
  elif [ -z "$LAST" ]; then
    VER=0.1.0
  else
    if [ "$EVENT" = push ]; then
      # hack/release-paths entries: a file, or dir/** (the pathspec dir).
      [ -f hack/release-paths ] || { echo "no hack/release-paths on $REMOTE/main"; exit 1; }
      mapfile -t paths < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' -e 's|/\*\*$||' hack/release-paths)
      [ "${#paths[@]}" -gt 0 ] || { echo "hack/release-paths lists no path"; exit 1; }
      n=$(git log -F --invert-grep --grep='[skip release]' --format=%H "$LATEST..HEAD" \
            -- "${paths[@]}" | wc -l)
      if [ "$n" -eq 0 ]; then echo "nothing to release above $LATEST"; exit 0; fi
    fi
    VER=${LAST%.*}.$(( ${LAST##*.} + 1 ))
  fi
  sed -i "s|^VERSION=\".*\"|VERSION=\"${VER}\"|" scripts/k3d-gpu
  # Packages carry no EXPERIMENTS.md: the ledger rows that set agent defaults
  # ship as console/ledger-rules.json, regenerated here for the release.
  python3 console/model_opt.py ledger-rules --ledger EXPERIMENTS.md --out console/ledger-rules.json
  sed -i "s|^pkgver=.*|pkgver=${VER}|" packaging/aur/PKGBUILD
  sed -i \
    -e "s|^\tpkgver = .*|\tpkgver = ${VER}|" \
    -e "s|#tag=aur-v.*|#tag=aur-v${VER}|" \
    packaging/aur/.SRCINFO
  git add scripts/k3d-gpu packaging/aur/PKGBUILD packaging/aur/.SRCINFO console/ledger-rules.json
  git diff --cached --quiet || git commit -q -m "release: aur-v${VER} [skip release]"
  TAG=aur-v${VER}
  git tag "$TAG"
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
