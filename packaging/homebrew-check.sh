#!/usr/bin/env bash
# Lint, audit, install and test Formula/k3d-gpu.rb inside the Homebrew image.
# Runs as the image's linuxbrew user with the repo mounted read-only at /src:
#   docker run --rm -v "$PWD":/src:ro "$BREW_IMAGE" bash /src/packaging/homebrew-check.sh
# Set K3D_GPU_BREW_INSTALL=0 to skip the install + test (it downloads the release tarball).
set -euo pipefail

export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_FROM_API=1 HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_ENV_HINTS=1

# brew taps a git repo, so stage the formula in a fresh one (the mount may be dirty or shallow).
tap=$(mktemp -d)
mkdir -p "${tap}/Formula"
cp /src/Formula/k3d-gpu.rb "${tap}/Formula/"
git -C "${tap}" init -q
git -C "${tap}" -c user.name=ci -c user.email=ci@localhost add -A
git -C "${tap}" -c user.name=ci -c user.email=ci@localhost commit -qm formula
brew tap 88plug/k3d-gpu "${tap}"

brew style 88plug/k3d-gpu/k3d-gpu
brew audit --strict 88plug/k3d-gpu/k3d-gpu
if [ "${K3D_GPU_BREW_INSTALL:-1}" = 1 ]; then
    brew install --formula 88plug/k3d-gpu/k3d-gpu
    brew test 88plug/k3d-gpu/k3d-gpu
    brew linkage 88plug/k3d-gpu/k3d-gpu
    k3d-gpu version
fi
echo "homebrew formula OK"
