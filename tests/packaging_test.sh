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
check "nfpm ships console/*.py to /usr/share/k3d-gpu/console/" \
    grep -Pzq 'src: console/\*\.py\n +dst: /usr/share/k3d-gpu/console/\n' "${NFPM}"
check "nfpm ships console/*.json to /usr/share/k3d-gpu/console/" \
    grep -Pzq 'src: console/\*\.json\n +dst: /usr/share/k3d-gpu/console/\n' "${NFPM}"
# The AUR recipe globs console/ like nfpm and nix, so a new console module
# needs no packaging edit (and no merge conflict between parallel PRs).
check "PKGBUILD installs console/*.py and console/*.json" \
    grep -Eq 'for f in console/\*\.py console/\*\.json; do' "${PKGBUILD}"
check "PKGBUILD installs console files to /usr/share/k3d-gpu/console/" \
    grep -Fq 'install -Dm644 "$f" "$pkgdir/usr/share/k3d-gpu/$f"' "${PKGBUILD}"
for f in llm_load.py energy.sh; do
    check "nfpm ships bench/${f} (k3d-gpu tune)" \
        grep -Pzq "src: bench/${f//./\\.}\n +dst: /usr/share/k3d-gpu/bench/${f//./\\.}\n" "${NFPM}"
    check "PKGBUILD installs bench/${f}" grep -qE "install -Dm755 .*bench/${f//./\\.}( |$)" "${PKGBUILD}"
    check "nix installs bench/${f}" grep -qE "install -Dm755 .*bench/${f//./\\.}( |$)" "${NIXPKG}"
done
for f in agent_trace.py agent_trace_gen.py apb/apb.py apb/capacity.py; do
    check "nfpm ships bench/${f} (k3d-gpu capacity)" \
        grep -Pzq "src: bench/${f//./\\.}\n +dst: /usr/share/k3d-gpu/bench/${f//./\\.}\n" "${NFPM}"
done
check "PKGBUILD installs bench/agent_trace*.py and apb/{apb,capacity}.py" \
    grep -qF 'for f in agent_trace.py agent_trace_gen.py apb/apb.py apb/capacity.py; do' "${PKGBUILD}"
check "nix installs bench/apb/apb.py and capacity.py" \
    grep -qF 'install -Dm644 -t "$out/share/k3d-gpu/bench/apb" bench/apb/apb.py bench/apb/capacity.py' "${NIXPKG}"
check "nfpm ships images.lock to /usr/share/k3d-gpu/" \
    grep -Pzq 'src: images\.lock\n +dst: /usr/share/k3d-gpu/images\.lock\n' "${NFPM}"
check "PKGBUILD installs images.lock" \
    grep -qF 'install -Dm644 images.lock "$pkgdir/usr/share/k3d-gpu/images.lock"' "${PKGBUILD}"
check "nix installs images.lock" grep -qF 'install -Dm644 images.lock "$out/share/k3d-gpu/images.lock"' "${NIXPKG}"
# 'k3d-gpu bench' reads vs.py, llm_load.py and competitors.lock from ${SHARE_DIR}/bench.
for f in vs.py llm_load.py competitors.lock; do
    check "nfpm ships bench/${f}" grep -Pzq "src: bench/${f//./\\.}\n +dst: /usr/share/k3d-gpu/bench/${f//./\\.}\n" "${NFPM}"
    check "PKGBUILD installs bench/${f}" grep -qE "install .*bench/${f//./\\.}( |$)" "${PKGBUILD}"
    check "nix installs bench/${f}" grep -qE "install .*bench/${f//./\\.}( |$)" "${NIXPKG}"
    check "nix source includes bench/${f}" grep -qF "(root + \"/bench/${f}\")" "${NIXPKG}"
    check "formula installs bench/${f}" grep -qF "\"bench/${f}\"" "${FORMULA}"
    check "packaging/check.sh hashes bench/${f}" grep -qE "for f in vs.py llm_load.py competitors.lock" "${ROOT}/packaging/check.sh"
done
check "nix installs console/*.py and console/*.json" \
    grep -qF 'install -Dm644 -t "$out/share/k3d-gpu/console" console/*.py console/*.json' "${NIXPKG}"
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

# Every recipe ships the same payload (README "Install"): the formula too, so a brew install
# runs plan, audit, autoquant, tune, capacity and share like the other packages.
check "formula installs console/*.py and console/*.json" \
    grep -qF '(pkgshare/"console").install Dir["console/*.py", "console/*.json"]' "${FORMULA}"
check "formula installs images.lock" grep -qF 'pkgshare.install "images.lock"' "${FORMULA}"
for f in energy.sh agent_trace.py agent_trace_gen.py; do
    check "formula installs bench/${f}" grep -qF "\"bench/${f}\"" "${FORMULA}"
done
check "formula installs bench/apb/{apb,capacity}.py" \
    grep -qF '(pkgshare/"bench/apb").install "bench/apb/apb.py", "bench/apb/capacity.py"' "${FORMULA}"
check "formula installs oracle/{bundle,project,validate,share}.py" \
    grep -qF '(pkgshare/"oracle").install "oracle/bundle.py", "oracle/project.py", "oracle/validate.py", "oracle/share.py"' "${FORMULA}"
check "formula installs oracle/schema/bundle-v1.json" \
    grep -qF '(pkgshare/"oracle/schema").install "oracle/schema/bundle-v1.json"' "${FORMULA}"
# README documents `k3d-gpu-mcp` as the MCP server command; every recipe puts it on PATH.
check "PKGBUILD installs k3d-gpu-mcp" grep -qF 'install -Dm755 scripts/k3d-gpu-mcp' "${PKGBUILD}"
check "nfpm ships k3d-gpu-mcp to /usr/bin" \
    grep -Pzq 'src: scripts/k3d-gpu-mcp\n +dst: /usr/bin/k3d-gpu-mcp\n +file_info:\n +mode: 0755' "${NFPM}"
check "formula installs k3d-gpu-mcp next to the launcher" grep -qF 'libexec.install "scripts/k3d-gpu", "scripts/k3d-gpu-mcp"' "${FORMULA}"
check "formula puts k3d-gpu-mcp on PATH with K3D_GPU_SHARE" \
    grep -qF '(bin/"k3d-gpu-mcp").write_env_script libexec/"k3d-gpu-mcp", K3D_GPU_SHARE: "${K3D_GPU_SHARE:-#{pkgshare}}"' "${FORMULA}"
check "nix source includes k3d-gpu-mcp" grep -qF '(root + "/scripts/k3d-gpu-mcp")' "${NIXPKG}"
check "nix installs k3d-gpu-mcp" grep -qF 'install -Dm755 scripts/k3d-gpu-mcp "$out/bin/k3d-gpu-mcp"' "${NIXPKG}"
check "packaging/check.sh asserts k3d-gpu-mcp is installed" grep -qF 'test -x /usr/bin/k3d-gpu-mcp' "${ROOT}/packaging/check.sh"

check "README Install names k3d-gpu-mcp in the shared payload" \
    grep -qF 'the launcher at `bin/k3d-gpu`, the MCP server at `bin/k3d-gpu-mcp`' "${ROOT}/README.md"
check "README Install says the node image is amd64 only" \
    grep -qF 'The node image `cryptoandcoffee/k3d-gpu` is published for linux/amd64 only' "${ROOT}/README.md"

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
# 'k3d-gpu eas' reads ${SHARE_DIR}/integrations/eas: the repo's integrations/ layout, since
# overlay.py finds its patch drafts (integrations/eat/upstream/patches) relative to the dir
# above integrations/, and eat-check.sh is ../eat/eat-check.sh from eas/.
check "nfpm ships integrations/eas as a tree to /usr/share/k3d-gpu/integrations/eas" \
    grep -Pzq 'src: integrations/eas/\n +dst: /usr/share/k3d-gpu/integrations/eas\n +type: tree\n' "${NFPM}"
check "nfpm ships the overlay's patch drafts to integrations/eat/upstream/patches/" \
    grep -Pzq 'src: integrations/eat/upstream/patches/\*\.patch\n +dst: /usr/share/k3d-gpu/integrations/eat/upstream/patches/\n' "${NFPM}"
check "nfpm ships eat-check.sh executable" \
    grep -Pzq 'src: integrations/eat/eat-check\.sh\n +dst: /usr/share/k3d-gpu/integrations/eat/eat-check\.sh\n +file_info:\n +mode: 0755' "${NFPM}"
for d in integrations integrations/eat integrations/eat/upstream integrations/eat/upstream/patches; do
    check "nfpm rpm owns /usr/share/k3d-gpu/${d}" \
        grep -Pzq -- "- dst: /usr/share/k3d-gpu/${d//\//\\/}\n +type: dir\n +packager: rpm\n" "${NFPM}"
done
check "formula installs integrations/eas" grep -qF '(pkgshare/"integrations/eas").install Dir["integrations/eas/*"]' "${FORMULA}"
check "formula installs the overlay's patch drafts" \
    grep -qF '(pkgshare/"integrations/eat/upstream/patches").install Dir["integrations/eat/upstream/patches/*.patch"]' "${FORMULA}"
check "formula installs eat-check.sh" grep -qF '(pkgshare/"integrations/eat").install "integrations/eat/eat-check.sh"' "${FORMULA}"
check "formula test asserts integrations/eas/eas.sh" grep -qF 'assert_path_exists pkgshare/"integrations/eas/eas.sh"' "${FORMULA}"
for f in integrations/eas integrations/eat/upstream/patches integrations/eat/eat-check.sh; do
    check "nix source includes ${f}" grep -qF "(root + \"/${f}\")" "${NIXPKG}"
done
check "nix installs integrations/eas" grep -qF 'cp -r integrations/eas "$out/share/k3d-gpu/integrations/eas"' "${NIXPKG}"
check "nix installs the overlay's patch drafts" \
    grep -qF 'install -Dm644 -t "$out/share/k3d-gpu/integrations/eat/upstream/patches" integrations/eat/upstream/patches/*.patch' "${NIXPKG}"
check "nix installs eat-check.sh" grep -qF 'install -Dm755 integrations/eat/eat-check.sh "$out/share/k3d-gpu/integrations/eat/eat-check.sh"' "${NIXPKG}"
check "PKGBUILD installs integrations/eas" grep -qF 'cp -r integrations/eas "$pkgdir/usr/share/k3d-gpu/integrations/eas"' "${PKGBUILD}"
check "PKGBUILD installs the overlay's patch drafts" \
    grep -qF 'install -Dm644 -t "$pkgdir/usr/share/k3d-gpu/integrations/eat/upstream/patches" integrations/eat/upstream/patches/*.patch' "${PKGBUILD}"
check "PKGBUILD installs eat-check.sh" grep -qF 'install -Dm755 integrations/eat/eat-check.sh "$pkgdir/usr/share/k3d-gpu/integrations/eat/eat-check.sh"' "${PKGBUILD}"
check "packaging/check.sh compares the installed integrations/ with the source" \
    grep -qF 'for f in /src/integrations/eas/* /src/integrations/eas/patches/* /src/integrations/eat/upstream/patches/*.patch; do' "${ROOT}/packaging/check.sh"
check "README Install names integrations/ in the shared payload" \
    grep -qF '`console/`, `bench/`, `oracle/`, `integrations/` (for `k3d-gpu eas`) and `images.lock`' "${ROOT}/README.md"
# The launcher finds the packaged copy: with no checkout next to it, eas_dir is
# ${SHARE_DIR}/integrations/eas, and overlay.json's patch_dirs resolve under ${SHARE_DIR}.
eas_pkg=$(mktemp -d)
mkdir -p "${eas_pkg}/bin" "${eas_pkg}/share/integrations/eas"
cp "${ROOT}/scripts/k3d-gpu" "${eas_pkg}/bin/k3d-gpu"
: > "${eas_pkg}/share/integrations/eas/eas.sh"
check "a packaged launcher finds K3D_GPU_SHARE/integrations/eas" test "$(K3D_GPU_SHARE="${eas_pkg}/share" HOME="${eas_pkg}" bash -c \
    'source <(sed "/^case \"\\\${1:-help}\" in\$/,\$d" "$1"); eas_dir' _ "${eas_pkg}/bin/k3d-gpu")" = "${eas_pkg}/share/integrations/eas"
rm -rf "${eas_pkg}"
check "overlay.json's patch_dirs are under integrations/, which the packages keep" python3 -c '
import json, sys
dirs = json.load(open(sys.argv[1]))["patch_dirs"]
sys.exit(not dirs or not all(d.startswith("integrations/") for d in dirs))' "${ROOT}/integrations/eas/overlay.json"
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
# The NVIDIA device-plugin step takes only a vX.Y.Z tag: its output lands in later run
# scripts and in share/nvidia-device-plugin.yml. Run the step with curl stubbed to a tag.
nvstep=$(python3 -c 'import sys, yaml
for s in yaml.safe_load(open(sys.argv[1]))["jobs"]["check"]["steps"]:
    if s.get("id") == "nvidia_plugin": print(s["run"])' "${BAP}")
check "build-and-push has the nvidia_plugin step" test -n "${nvstep}"
# nvstep_out <tag>: the step's GITHUB_OUTPUT when the release API answers <tag>; fails when the step fails.
nvstep_out() {
    local d rc=0
    d=$(mktemp -d)
    jq -n --arg t "$1" '{tag_name: $t}' > "${d}/release.json"
    printf '#!/bin/sh\ncat "%s/release.json"\n' "${d}" > "${d}/curl"
    chmod +x "${d}/curl"
    if PATH="${d}:${PATH}" GITHUB_OUTPUT="${d}/out" GH_TOKEN=x bash -euc "${nvstep}" >/dev/null 2>&1; then
        cat "${d}/out"
    else
        rc=1
    fi
    rm -rf "${d}"
    return "${rc}"
}
nvstep_refuses() { ! nvstep_out "$1" >/dev/null; }
check "nvidia_plugin step takes a vX.Y.Z tag" test "$(nvstep_out v0.21.0)" = "latest=v0.21.0"
check "nvidia_plugin step refuses a tag with shell characters" nvstep_refuses 'v0.21.0";id;"'
check "nvidia_plugin step refuses a tag with a newline" nvstep_refuses $'v0.21.0\nx'
check "nvidia_plugin step refuses a tag with \$( )" nvstep_refuses 'v1$(id)'
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

# packaging/install.sh pins one release. packages.yml's install-pin job moves it to each new
# release once its packages are published, so the pin is the launcher's VERSION or, while that
# release is still building, the release before it (auto-release.yml bumps the patch by one).
INSTALLER="${ROOT}/packaging/install.sh"
pin=$(sed -n 's/^K3D_GPU_VERSION=//p' "${INSTALLER}")
check "install.sh K3D_GPU_VERSION is x.y.z" grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+' <<<"${pin}"
for v in K3D_GPU_DEB_SHA256 K3D_GPU_RPM_SHA256; do
    check "install.sh ${v} is a sha256" grep -Eqx '[0-9a-f]{64}' <<<"$(sed -n "s/^${v}=//p" "${INSTALLER}")"
done
patch=${launcher_version##*.}
if [ "${patch}" -gt 0 ]; then
    prev_release="${launcher_version%.*}.$((patch - 1))"
else
    prev_release=$(git -C "${ROOT}" tag -l 'aur-v*' --sort=-v:refname 2>/dev/null \
        | sed 's/^aur-v//' | grep -vx "${launcher_version}" | head -n1)
    prev_release=${prev_release:-${launcher_version}}
fi
check "install.sh pin ${pin} is not older than ${prev_release} (launcher ${launcher_version})" \
    test "$(printf '%s\n%s\n' "${pin}" "${prev_release}" | sort -V | head -n1)" = "${prev_release}"
check "packages.yml pins install.sh after the packages are published" \
    grep -Pzq -- '  install-pin:\n.*\n    needs: deb-rpm\n    if: \$\{\{ !inputs\.dry_run \}\}\n' "${PKGS}"
check "packages.yml reads install.sh sha256s from the release's SHA256SUMS" \
    grep -q 'gh release download "aur-v${VER}" --dir "${SUMS_DIR}"' "${PKGS}"
check "install.sh commit skips the release trigger" \
    grep -q 'git commit -m "release: install.sh k3d-gpu ${VER} \[skip release\]"' "${PKGS}"

# Run the install.sh rewrite from packages.yml on a copy, fed a SHA256SUMS like deb-rpm writes.
cp "${INSTALLER}" "${work}/install.sh"
deb_sha=$(printf '%064d' 0 | tr 0 d) rpm_sha=$(printf '%064d' 0 | tr 0 e)
printf '%s  k3d-gpu-9.8.7-1.noarch.rpm\n%s  k3d-gpu_9.8.7-1_all.deb\n%s  k3d-gpu_9.8.6-1_all.deb\n' \
    "${rpm_sha}" "${deb_sha}" "$(printf '%064d' 0 | tr 0 f)" > "${work}/SHA256SUMS"
pin_rewrite=$(sed -n '/# install-pin-rewrite-begin/,/# install-pin-rewrite-end/p' "${PKGS}" | sed '1d;$d')
check "packages.yml has an install.sh rewrite block" test -n "${pin_rewrite}"
(
    cd "${work}" && VER=9.8.7 SUMS=SHA256SUMS INSTALLER=install.sh bash -euc "${pin_rewrite}"
) >/dev/null 2>&1
check "install rewrite sets the version" grep -qx 'K3D_GPU_VERSION=9.8.7' "${work}/install.sh"
check "install rewrite sets the .deb sha256" grep -qx "K3D_GPU_DEB_SHA256=${deb_sha}" "${work}/install.sh"
check "install rewrite sets the .rpm sha256" grep -qx "K3D_GPU_RPM_SHA256=${rpm_sha}" "${work}/install.sh"
check "install rewrite changes only those three lines" \
    test "$(diff "${INSTALLER}" "${work}/install.sh" | grep -c '^[<>]')" = 6
# A SHA256SUMS without the version's packages leaves install.sh alone and fails the step.
cp "${INSTALLER}" "${work}/install.sh"
(
    cd "${work}" && VER=9.8.8 SUMS=SHA256SUMS INSTALLER=install.sh bash -euc "${pin_rewrite}"
) >/dev/null 2>&1
check "install rewrite fails when SHA256SUMS lacks the version" test $? -ne 0
check "install rewrite leaves install.sh alone on failure" cmp -s "${INSTALLER}" "${work}/install.sh"

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
