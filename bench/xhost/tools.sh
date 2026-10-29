#!/usr/bin/env bash
# tools.sh: the cross-host rows' k3d and kubectl in ~/k3dgpu-xhost/bin (EXPERIMENTS.md
# "Cross-host pre-registration" rule 5, as cross-host amendment K1 moves it), each checked
# against its upstream checksum file. Installs nothing else and touches no other file. Run on
# crescent from a clean checkout of this repo, with the bench lock free. Prints both versions.
set -euo pipefail
X=${HOME}/k3dgpu-xhost
K3D_V=v5.9.0 KUBECTL_V=v1.37.2
mkdir -p "${X}/bin"
DL=$(mktemp -d "${X}/tools-dl.XXXXXX")
trap 'rm -rf "${DL}"' EXIT
cd "${DL}"
curl -fsSLo k3d "https://github.com/k3d-io/k3d/releases/download/${K3D_V}/k3d-linux-amd64"
curl -fsSLo k3d.sums "https://github.com/k3d-io/k3d/releases/download/${K3D_V}/checksums.txt"
curl -fsSLo kubectl "https://dl.k8s.io/release/${KUBECTL_V}/bin/linux/amd64/kubectl"
curl -fsSLo kubectl.sha256 "https://dl.k8s.io/release/${KUBECTL_V}/bin/linux/amd64/kubectl.sha256"
echo "$(grep 'k3d-linux-amd64$' k3d.sums | awk '{print $1}')  k3d" | sha256sum -c
echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c
install -m755 k3d kubectl "${X}/bin/"
"${X}/bin/k3d" version | head -1
"${X}/bin/kubectl" version --client | head -1
