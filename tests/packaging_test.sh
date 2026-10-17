#!/usr/bin/env bash
# Static consistency tests for the package recipes: every one ships the launcher and every
# share/*.yml, and their versions track the launcher. No network, no containers; the real
# builds run in packaging/check.sh (ci.yml).
#
# Usage: tests/packaging_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKGBUILD="${ROOT}/packaging/aur/PKGBUILD"
SRCINFO="${ROOT}/packaging/aur/.SRCINFO"
NFPM="${ROOT}/packaging/nfpm/nfpm.yaml"
FORMULA="${ROOT}/Formula/k3d-gpu.rb"
NIXPKG="${ROOT}/packaging/nix/package.nix"
PASS=0 FAILS=0

ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }
check() { local name=$1; shift; if "$@"; then ok "${name}"; else fail "${name}"; fi; }

launcher_version=$(sed -n 's/^VERSION="\([^"]*\)".*/\1/p' "${ROOT}/scripts/k3d-gpu")
check "launcher VERSION is x.y.z" grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' <<<"${launcher_version}"
check "PKGBUILD pkgver matches launcher" grep -qx "pkgver=${launcher_version}" "${PKGBUILD}"
check ".SRCINFO pkgver matches launcher" grep -qx "	pkgver = ${launcher_version}" "${SRCINFO}"

# The AUR recipe lists manifests one by one, so a new share/*.yml must be added there too.
for f in "${ROOT}"/share/*.yml; do
    name=${f##*/}
    check "PKGBUILD installs share/${name}" \
        grep -Eq "install -Dm644 share/${name} +\"\\\$pkgdir/usr/share/k3d-gpu/${name}\"" "${PKGBUILD}"
done
check "PKGBUILD installs no missing manifest" bash -c '
    for n in $(grep -o "share/[a-z0-9-]*\.yml " "$1" | sed "s|share/||"); do
        [ -f "$2/share/${n}" ] || { echo "missing share/${n}"; exit 1; }
    done' _ "${PKGBUILD}" "${ROOT}"

# nfpm, Homebrew and Nix take every share/*.yml by glob.
check "nfpm ships the launcher to /usr/bin" grep -Pzq 'src: scripts/k3d-gpu\n +dst: /usr/bin/k3d-gpu\n +file_info:\n +mode: 0755' "${NFPM}"
check "nfpm ships share/*.yml to /usr/share/k3d-gpu/" grep -Pzq 'src: share/\*\.yml\n +dst: /usr/share/k3d-gpu/' "${NFPM}"
check "nfpm version comes from \$VERSION" grep -qx 'version: ${VERSION}' "${NFPM}"
check "nfpm is arch-independent" grep -qx 'arch: all' "${NFPM}"
check "formula installs share/*.yml and share/telemetry" \
    grep -qF 'pkgshare.install Dir["share/*.yml", "share/telemetry"]' "${FORMULA}"
check "nfpm ships share/telemetry/" \
    grep -Pzq 'src: share/telemetry/\*\.yml\n +dst: /usr/share/k3d-gpu/telemetry/\n' "${NFPM}"
check "nfpm ships share/telemetry/optional/" \
    grep -Pzq 'src: share/telemetry/optional/\*\.yml\n +dst: /usr/share/k3d-gpu/telemetry/optional/\n' "${NFPM}"
check "formula points K3D_GPU_SHARE at pkgshare" grep -qF 'K3D_GPU_SHARE: "${K3D_GPU_SHARE:-#{pkgshare}}"' "${FORMULA}"
check "formula test asserts a user K3D_GPU_SHARE wins" \
    grep -qF 'assert_match "/x/nvidia-device-plugin.yml", shell_output("K3D_GPU_SHARE=/x #{bin}/k3d-gpu help")' "${FORMULA}"

# Render bin/k3d-gpu the way Homebrew's Pathname#write_env_script does (brew 7.0.9,
# Library/Homebrew/extend/pathname.rb: KEY="value" exec "target" "$@") and run it.
share_value=$(sed -n 's/.*write_env_script libexec\/"k3d-gpu", K3D_GPU_SHARE: "\(.*\)"$/\1/p' "${FORMULA}")
check "formula wrapper value found" test -n "${share_value}"
brew_wrapper() {
    local value=${share_value//#\{pkgshare\}/\/opt\/pkgshare}
    printf '#!/bin/bash\nK3D_GPU_SHARE="%s" exec "%s" "$@"\n' "${value}" "${ROOT}/scripts/k3d-gpu"
}
wrap_dir=$(mktemp -d)
brew_wrapper > "${wrap_dir}/k3d-gpu" && chmod +x "${wrap_dir}/k3d-gpu"
check "brew wrapper defaults K3D_GPU_SHARE to pkgshare" \
    grep -qF /opt/pkgshare/nvidia-device-plugin.yml <("${wrap_dir}/k3d-gpu" help)
check "brew wrapper keeps a user K3D_GPU_SHARE" \
    grep -qF /x/nvidia-device-plugin.yml <(K3D_GPU_SHARE=/x "${wrap_dir}/k3d-gpu" help)
rm -rf "${wrap_dir}"

check "formula url is an aur-v release tarball" \
    grep -Eq '^  url "https://github.com/88plug/k3d-gpu/archive/refs/tags/aur-v[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz"$' "${FORMULA}"
check "formula sha256 is 64 hex" grep -Eq '^  sha256 "[0-9a-f]{64}"$' "${FORMULA}"
formula_version=$(sed -n 's|.*/aur-v\([0-9.]*\)\.tar\.gz".*|\1|p' "${FORMULA}")
check "formula version is not ahead of the launcher" \
    test "$(printf '%s\n%s\n' "${formula_version}" "${launcher_version}" | sort -V | tail -1)" = "${launcher_version}"
check "nix installs share/*.yml" grep -qF 'install -Dm644 -t "$out/share/k3d-gpu" share/*.yml' "${NIXPKG}"
check "nix installs share/telemetry/*.yml" \
    grep -qF 'install -Dm644 -t "$out/share/k3d-gpu/telemetry" share/telemetry/*.yml' "${NIXPKG}"
check "nix installs share/telemetry/optional/*.yml" \
    grep -qF 'install -Dm644 -t "$out/share/k3d-gpu/telemetry/optional" share/telemetry/optional/*.yml' "${NIXPKG}"
check "nix points K3D_GPU_SHARE at the store" grep -qF -- '--set-default K3D_GPU_SHARE "$out/share/k3d-gpu"' "${NIXPKG}"
check "flake.lock pins nixpkgs to a rev" \
    grep -Eq '"rev": "[0-9a-f]{40}"' "${ROOT}/flake.lock"

# Release wiring: the image release job dispatches packages.yml next to aur-deploy.yml, and
# the formula rewrite there produces lines the checks above accept.
BAP="${ROOT}/.github/workflows/build-and-push.yml"
PKGS="${ROOT}/.github/workflows/packages.yml"
check "image release dispatches aur-deploy.yml" grep -q 'gh workflow run aur-deploy.yml' "${BAP}"
check "image release dispatches packages.yml" grep -q 'gh workflow run packages.yml --ref main -f version=' "${BAP}"
check "packages.yml dry_run skips the upload" \
    grep -Pzq -- '- name: Publish to the GitHub release\n +if: \$\{\{ !inputs\.dry_run \}\}\n' "${PKGS}"
check "packages.yml dry_run skips the formula commit" \
    grep -Pzq -- '- name: Commit to main\n +if: \$\{\{ !inputs\.dry_run \}\}\n' "${PKGS}"
check "packages.yml builds with packaging/check.sh nfpm" grep -q 'packaging/check.sh nfpm' "${PKGS}"
check "packages.yml uploads to the aur-v release" grep -q 'gh release upload' "${PKGS}"
# gh uploads create-time assets to a draft and publishes only once they are all up, so a
# failed upload never leaves a published aur-v release without its .deb/.rpm.
check "packages.yml creates the release with its assets" \
    grep -Eq 'gh release create "\$\{TAG\}" k3d-gpu\* SHA256SUMS ' "${PKGS}"

# Run the formula rewrite from packages.yml on a copy and check the result.
work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT
cp "${FORMULA}" "${work}/k3d-gpu.rb"
rewrite=$(sed -n '/# formula-rewrite-begin/,/# formula-rewrite-end/p' "${PKGS}" | sed '1d;$d')
check "packages.yml has a formula rewrite block" test -n "${rewrite}"
(
    cd "${work}" && VER=9.8.7 SHA=$(printf '%064d' 0 | tr 0 a) FORMULA=k3d-gpu.rb bash -euc "${rewrite}"
) >/dev/null 2>&1
check "rewrite sets the url to the new tag" \
    grep -qx '  url "https://github.com/88plug/k3d-gpu/archive/refs/tags/aur-v9.8.7.tar.gz"' "${work}/k3d-gpu.rb"
check "rewrite sets the sha256" grep -qx "  sha256 \"$(printf '%064d' 0 | tr 0 a)\"" "${work}/k3d-gpu.rb"
check "rewrite changes only url and sha256" \
    test "$(diff "${FORMULA}" "${work}/k3d-gpu.rb" | grep -c '^[<>]')" = 4

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
