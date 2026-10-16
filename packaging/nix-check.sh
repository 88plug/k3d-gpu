#!/usr/bin/env bash
# Run `nix flake check` (evaluates both systems, builds the package and its smoke test) in the
# nixos/nix image with the repo mounted read-only at /src:
#   docker run --rm -v "$PWD":/src:ro "$NIX_IMAGE" bash /src/packaging/nix-check.sh
# Flakes only see git-tracked files, so the tree is staged into a fresh repo first. With
# K3D_GPU_NIX_LOCK=1 it writes a regenerated flake.lock to /out instead of using the pinned one.
set -euo pipefail

nix() { command nix --extra-experimental-features 'nix-command flakes' --print-build-logs "$@"; }

work=$(mktemp -d)
tar -C /src --exclude=./.git --exclude=./.claude -cf - . | tar -C "${work}" --no-same-owner -xf -
cd "${work}"
git init -q
git add -A

if [ "${K3D_GPU_NIX_LOCK:-0}" = 1 ]; then
    rm -f flake.lock
    nix flake lock
    cp flake.lock /out/flake.lock
fi

nix fmt -- --check flake.nix packaging/nix/package.nix
nix flake check --all-systems --no-build
nix flake check
nix build .#k3d-gpu
ls -lR result/
./result/bin/k3d-gpu version
echo "nix flake OK"
