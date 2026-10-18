#!/usr/bin/env bash
# k3d-gpu installer: one pinned release, every download checked against a sha256
# written in this file.
#
#   curl -fsSLO https://raw.githubusercontent.com/88plug/k3d-gpu/main/packaging/install.sh
#   less install.sh          # read it first
#   bash install.sh          # then: k3d-gpu doctor, k3d-gpu quickstart
#
# What it does, on Linux and WSL2 (amd64, arm64; the node image `k3d-gpu up` pulls is
# amd64 only, so arm64 needs K3D_GPU_IMAGE set to a k3s node image built for arm64):
#   1. k3d-gpu ${K3D_GPU_VERSION} from its GitHub release: the .deb through apt-get, or
#      the .rpm through dnf or zypper (sudo asks for your password).
#   2. k3d ${K3D_VERSION} and kubectl ${KUBECTL_VERSION} into ~/.local/bin, when they are
#      not on PATH already.
#   3. Docker is never installed: it changes the host, so the command is printed.
# Other distros (Arch, NixOS, Homebrew on Linux) have their own packages; the
# installer names them and stops.
#
# Environment:
#   K3D_GPU_BIN_DIR       where k3d and kubectl go (default: ~/.local/bin)
#   K3D_GPU_INSTALL_BASE  download mirror for the k3d-gpu packages (default: the
#                         GitHub release); the pinned sha256 is checked either way
#   K3D_GPU_INSTALL_DRY_RUN=1  print the install commands instead of running them
#
# Bump: the k3d-gpu pin moves with every release (packages.yml's install-pin job
# reads its two sha256s from the aur-v<version> release's SHA256SUMS). k3d and
# kubectl are bumped by hand: set the version, then copy each sha256 from the
# release's own checksum file (k3d: checksums.txt; kubectl:
# https://dl.k8s.io/release/<version>/bin/linux/<arch>/kubectl.sha256).

set -euo pipefail

K3D_GPU_VERSION=0.1.59
K3D_GPU_DEB_SHA256=6ed59a8fb904ed5cd5b70da4bb252329debbd474eb404bb6772e09bf57e8ee53
K3D_GPU_RPM_SHA256=6305520ccc52f413689be9d1ae1116bc6345b6f8f052180f388586c64902fbe4
K3D_VERSION=v5.9.0
K3D_SHA256_AMD64=06d8f25bc3a971c4eb29e0ff08429b180402db0f4dec838c9eac427e296800a0
K3D_SHA256_ARM64=03cde5cf23e6e8e67de5a039ecf26e5b85aca82fba3e5d13dadf904cd218a250
KUBECTL_VERSION=v1.37.1
KUBECTL_SHA256_AMD64=65691ff77eb6fa44c908b77a1082c9f092c3b9733b5cefabec0d1104890e21a8
KUBECTL_SHA256_ARM64=ff749f4b78d9c4f1ec87307df9b50119ed819e2094aa9810cb9acffc3286c8c7
# First k3d-gpu release with the quickstart command; the closing hint follows it.
QUICKSTART_SINCE=0.1.33

BASE="${K3D_GPU_INSTALL_BASE:-https://github.com/88plug/k3d-gpu/releases/download/aur-v${K3D_GPU_VERSION}}"
BIN_DIR="${K3D_GPU_BIN_DIR:-${HOME}/.local/bin}"
DRY="${K3D_GPU_INSTALL_DRY_RUN:-}"
SYSROOT="${K3D_GPU_SYSROOT:-}"

say()  { echo "==> $*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }
run()  { if [ -n "${DRY}" ]; then echo "+ $*"; else "$@"; fi; }

# sha256_ok <file> <hex>: the file's sha256 is exactly <hex>.
sha256_ok() {
    local got
    [[ "$2" =~ ^[0-9a-f]{64}$ ]] || return 1
    if command -v sha256sum >/dev/null 2>&1; then
        got=$(sha256sum "$1" | cut -d' ' -f1)
    else
        got=$(shasum -a 256 "$1" | cut -d' ' -f1)
    fi
    [ "${got}" = "$2" ]
}

# fetch <url> <dest> <sha256>: download, check, and keep only a matching file.
fetch() {
    curl -fsSL --retry 3 -o "$2" "$1" || die "download failed: $1"
    if ! sha256_ok "$2" "$3"; then
        rm -f -- "$2"
        die "sha256 mismatch for $1 (want $3); nothing was installed"
    fi
}

# go_arch: amd64 or arm64 from uname -m; empty for anything else.
go_arch() {
    case "${1:-$(uname -m)}" in
        x86_64|amd64) echo amd64 ;;
        aarch64|arm64) echo arm64 ;;
    esac
}

# pkg_kind: deb (apt-get), rpm (dnf or zypper), or empty.
pkg_kind() {
    if command -v apt-get >/dev/null 2>&1; then echo deb
    elif command -v dnf >/dev/null 2>&1 || command -v zypper >/dev/null 2>&1; then echo rpm
    fi
}

# other_distro_hint: the package for a distro this installer does not handle.
other_distro_hint() {
    local id=""
    id=$(sed -n 's/^ID=//p' "${SYSROOT}/etc/os-release" 2>/dev/null | tr -d '"')
    case "${id}" in
        arch|manjaro|endeavouros) echo "Arch: yay -S k3d-gpu (AUR)" ;;
        nixos) echo "NixOS: nix run github:88plug/k3d-gpu" ;;
        *) echo "Homebrew on Linux: brew tap 88plug/k3d-gpu https://github.com/88plug/k3d-gpu && brew install k3d-gpu" ;;
    esac
}

is_wsl() { grep -qi microsoft "${SYSROOT}/proc/version" 2>/dev/null; }

# docker_hint: the Docker install suggestion for this host.
docker_hint() {
    if is_wsl; then
        echo "Docker Desktop for Windows with WSL integration enabled for this distro (https://docs.docker.com/desktop/features/wsl/), or Docker Engine inside the distro (https://docs.docker.com/engine/install/)"
    else
        echo "Docker Engine for your distro: https://docs.docker.com/engine/install/ (then: sudo usermod -aG docker \$USER, and log in again)"
    fi
}

install_k3d_gpu() {
    local kind=$1 dir=$2 file sha sudo=""
    [ "$(id -u)" -eq 0 ] || sudo=sudo
    if [ "${kind}" = deb ]; then
        file="k3d-gpu_${K3D_GPU_VERSION}-1_all.deb" sha=${K3D_GPU_DEB_SHA256}
    else
        file="k3d-gpu-${K3D_GPU_VERSION}-1.noarch.rpm" sha=${K3D_GPU_RPM_SHA256}
    fi
    say "k3d-gpu ${K3D_GPU_VERSION}: ${file}"
    fetch "${BASE}/${file}" "${dir}/${file}" "${sha}"
    say "sha256 ok: ${sha}"
    if [ "${kind}" = deb ]; then
        run ${sudo} apt-get install -y "${dir}/${file}"
    elif command -v dnf >/dev/null 2>&1; then
        run ${sudo} dnf install -y "${dir}/${file}"
    else
        run ${sudo} zypper --non-interactive install --allow-unsigned-rpm "${dir}/${file}"
    fi
}

# install_bin <name> <url> <sha256> <dir>: a single static binary into BIN_DIR.
install_bin() {
    if command -v "$1" >/dev/null 2>&1; then
        say "$1 already on PATH: $(command -v "$1")"
        return 0
    fi
    say "$1: $2"
    fetch "$2" "$4/$1" "$3"
    run mkdir -p "${BIN_DIR}"
    run install -m 0755 "$4/$1" "${BIN_DIR}/$1"
}

# version_ge <a> <b>: dotted version a is b or newer.
version_ge() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" = "$2" ]; }

# The download directory; global, so the EXIT trap still sees it after main returns.
DL_DIR=""
cleanup() { if [ -n "${DL_DIR}" ]; then rm -rf -- "${DL_DIR}"; fi; }

main() {
    local arch kind
    [ "$(uname -s)" = Linux ] || die "k3d-gpu runs on Linux and WSL2 only (on Windows: wsl --install, then run this inside the distro)"
    arch=$(go_arch) || true
    [ -n "${arch}" ] || die "unsupported CPU architecture $(uname -m) (amd64 and arm64 only)"
    command -v curl >/dev/null 2>&1 || die "curl is needed"
    if is_wsl; then
        say "WSL2 detected. The Intel GPU reaches containers through /dev/dxg; the NPU does not (intel/linux-npu-driver#56)."
    fi
    kind=$(pkg_kind)
    [ -n "${kind}" ] || die "no apt-get, dnf or zypper here. $(other_distro_hint)"
    DL_DIR=$(mktemp -d)
    trap cleanup EXIT
    install_k3d_gpu "${kind}" "${DL_DIR}"
    if [ "${arch}" = amd64 ]; then
        install_bin k3d "https://github.com/k3d-io/k3d/releases/download/${K3D_VERSION}/k3d-linux-amd64" "${K3D_SHA256_AMD64}" "${DL_DIR}"
        install_bin kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" "${KUBECTL_SHA256_AMD64}" "${DL_DIR}"
    else
        install_bin k3d "https://github.com/k3d-io/k3d/releases/download/${K3D_VERSION}/k3d-linux-arm64" "${K3D_SHA256_ARM64}" "${DL_DIR}"
        install_bin kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/arm64/kubectl" "${KUBECTL_SHA256_ARM64}" "${DL_DIR}"
    fi
    case ":${PATH}:" in *":${BIN_DIR}:"*) ;; *) say "add ${BIN_DIR} to PATH: export PATH=\"${BIN_DIR}:\$PATH\"" ;; esac
    command -v docker >/dev/null 2>&1 || say "Docker is not installed. Suggested: $(docker_hint)"
    if version_ge "${K3D_GPU_VERSION}" "${QUICKSTART_SINCE}"; then
        say "next: k3d-gpu doctor, then k3d-gpu quickstart"
    else
        say "next: k3d-gpu doctor. 'k3d-gpu quickstart' ships in ${QUICKSTART_SINCE}; until then run it from a checkout: git clone https://github.com/88plug/k3d-gpu && k3d-gpu/scripts/k3d-gpu quickstart"
    fi
}

[ -n "${K3D_GPU_INSTALL_SOURCE_ONLY:-}" ] || main "$@"
