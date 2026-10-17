#!/usr/bin/env bash
# Build and verify the non-AUR packages in pinned containers. Used by ci.yml and packages.yml,
# and runnable locally from any checkout (needs only docker):
#   packaging/check.sh nfpm   build dist/*.deb + dist/*.rpm with nfpm, list their contents,
#                             install, run and remove each in Debian / Fedora
#   packaging/check.sh brew   brew style + audit --strict + install + test Formula/k3d-gpu.rb
#   packaging/check.sh nix    nix fmt --check + nix flake check + nix build
#
# Environment: VERSION (default: the launcher's VERSION), K3D_GPU_DIST (default: <repo>/dist),
# SOURCE_DATE_EPOCH (default: last commit time), and the *_IMAGE pins below.
set -euo pipefail

NFPM_IMAGE="${NFPM_IMAGE:-goreleaser/nfpm:v2.48.0@sha256:d57a1452e7a019896b355f7b1ec67262278f6c503631f652d4fce7aef9ac8df7}"
DEBIAN_IMAGE="${DEBIAN_IMAGE:-debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f}"
FEDORA_IMAGE="${FEDORA_IMAGE:-fedora:44@sha256:43b29f65a41eb9c35e1cd5323e3bdf3b655c2357a9f4f1ff2f9c2798e5045d80}"
BREW_IMAGE="${BREW_IMAGE:-ghcr.io/homebrew/brew:7.0.9@sha256:00b643d72ab0ee27c64e3e552d08c66e1bb5272bdf4ed28964e27aa5764f2503}"
NIX_IMAGE="${NIX_IMAGE:-nixos/nix:2.35.3@sha256:ff480dbb23511aa1c10710aa068ceea5b93fed9bfce5f5379f3a6b07282cbc7b}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="${K3D_GPU_DIST:-${ROOT}/dist}"
VERSION="${VERSION:-$(sed -n 's/^VERSION="\([^"]*\)".*/\1/p' "${ROOT}/scripts/k3d-gpu")}"
[[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "bad VERSION '${VERSION}'" >&2; exit 1; }

# Run inside each distro container after the package is installed.
installed_checks='
set -euo pipefail
test "$(k3d-gpu version)" = "k3d-gpu ${VERSION}"
for f in /src/share/*.yml /src/share/telemetry/*.yml /src/share/telemetry/optional/*.yml; do
    test "$(sha256sum < "$f")" = "$(sha256sum < "/usr/share/k3d-gpu/${f#/src/share/}")"
done
for f in /src/console/*.py /src/console/*.json; do
    test "$(sha256sum < "$f")" = "$(sha256sum < "/usr/share/k3d-gpu/console/${f##*/}")"
done
k3d-gpu help | grep -F "/usr/share/k3d-gpu/nvidia-device-plugin.yml" >/dev/null
test -x /usr/bin/k3d-gpu
'

nfpm_build() {
    local epoch packager
    epoch="${SOURCE_DATE_EPOCH:-$(git -C "${ROOT}" log -1 --format=%ct 2>/dev/null || echo 0)}"
    mkdir -p "${DIST}"
    for packager in deb rpm; do
        docker run --rm -u "$(id -u):$(id -g)" \
            -e VERSION="${VERSION}" -e SOURCE_DATE_EPOCH="${epoch}" \
            -v "${ROOT}:/src:ro" -v "${DIST}:/dist" -w /src \
            "${NFPM_IMAGE}" package --config packaging/nfpm/nfpm.yaml --packager "${packager}" --target /dist/
    done
}

nfpm_verify() {
    local deb="k3d-gpu_${VERSION}-1_all.deb" rpm="k3d-gpu-${VERSION}-1.noarch.rpm"
    echo "==> ${deb}"
    docker run --rm -e VERSION="${VERSION}" -v "${ROOT}:/src:ro" -v "${DIST}:/dist:ro" "${DEBIAN_IMAGE}" bash -c "
        set -euo pipefail
        dpkg-deb --info /dist/${deb}
        dpkg -c /dist/${deb}
        dpkg -i /dist/${deb}
        ${installed_checks}
        dpkg -r k3d-gpu
        ! test -e /usr/bin/k3d-gpu && ! test -e /usr/share/k3d-gpu"
    echo "==> ${rpm}"
    docker run --rm -e VERSION="${VERSION}" -v "${ROOT}:/src:ro" -v "${DIST}:/dist:ro" "${FEDORA_IMAGE}" bash -c "
        set -euo pipefail
        rpm -qpi /dist/${rpm}
        rpm -qpR /dist/${rpm}
        rpm -qplv /dist/${rpm}
        rpm -K /dist/${rpm}
        rpm -i /dist/${rpm}
        rpm -V k3d-gpu
        ${installed_checks}
        rpm -e k3d-gpu
        ! test -e /usr/bin/k3d-gpu && ! test -e /usr/share/k3d-gpu && ! test -e /usr/share/doc/k3d-gpu"
}

case "${1:-}" in
    nfpm) nfpm_build; nfpm_verify; echo "deb + rpm OK in ${DIST}" ;;
    brew) docker run --rm -v "${ROOT}:/src:ro" "${BREW_IMAGE}" bash /src/packaging/homebrew-check.sh ;;
    nix)  docker run --rm -v "${ROOT}:/src:ro" "${NIX_IMAGE}" bash /src/packaging/nix-check.sh ;;
    *)    echo "usage: $0 nfpm|brew|nix" >&2; exit 2 ;;
esac
