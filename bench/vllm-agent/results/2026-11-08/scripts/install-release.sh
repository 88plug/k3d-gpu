#!/bin/bash
# install-release.sh <dir>: the public installer from main, as the README gives it.
set -u
D=$1
mkdir -p "$D" && cd "$D" || exit 1
{ ls -la "$HOME/.local/bin"; dpkg -l k3d-gpu 2>&1; command -v k3d kubectl k3d-gpu; } > before.txt 2>&1
curl -fsSLO https://raw.githubusercontent.com/88plug/k3d-gpu/main/packaging/install.sh || exit 1
sha256sum install.sh > install.sh.sha256
grep -m1 '^K3D_GPU_VERSION=' install.sh
bash install.sh > install.log 2>&1
echo "install rc=$?"
tail -n 12 install.log
{ ls -la "$HOME/.local/bin"; dpkg -l k3d-gpu 2>&1; } > after.txt 2>&1
bash -lc 'command -v k3d-gpu k3d kubectl; k3d-gpu version; k3d version | head -1; kubectl version --client | head -1'
