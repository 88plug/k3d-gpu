#!/bin/bash
# uninstall-release.sh <dir>: back to the state before install-release.sh: the k3d-gpu
# package, and the k3d and kubectl install.sh put in ~/.local/bin (neither was there).
set -u
D=$1
cd "$D" || exit 1
# shellcheck disable=SC2024 # the log is the user's file: only apt-get runs as root
sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get remove -y k3d-gpu > uninstall.log 2>&1
echo "apt-get remove rc=$?"
rm -f "$HOME/.local/bin/k3d" "$HOME/.local/bin/kubectl"
rmdir "$HOME/.local/state/k3d-gpu" 2>/dev/null
{ ls -la "$HOME/.local/bin"; dpkg -l k3d-gpu 2>&1; command -v k3d-gpu k3d kubectl; } > after-uninstall.txt 2>&1
cat after-uninstall.txt
