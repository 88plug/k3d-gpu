#!/usr/bin/env bash
# setup.sh: the crescent campaign's tools, venvs and checkouts under ~/k3dgpu-bench (nothing
# system-wide; EXPERIMENTS.md "H50-H52 pre-registration", shared rule 5, and crescent amendment E4
# for the tool versions). Each binary is checked against its upstream checksum. Run from a checkout
# of this repo on the host, under the bench lock with no hold running. A second run replaces the
# binaries and keeps the venv and checkouts that exist.
set -euo pipefail
B=${HOME}/k3dgpu-bench
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
K3D_V=v5.9.0 KUBECTL_V=v1.37.2 HELM_V=v4.3.1 UV_V=0.13.6
EAB_REV=758d4845520ca5456bf732264ef02d92a26d25a6       # enterprise-ai-blueprints (banking agent)
EAT_REV=0d3438d7119de44cafc5903f66665c0b623e3333       # EAT release-1.0.3-hotfix
EAT_PR21=c86c42b073ee533e9fb9e9b9330b9d1478e4bf47      # EAT PR #21 head (release 1.1.0 XPU path)
mkdir -p "${B}/bin" "${B}/dl"
cd "${B}/dl"
curl -fsSLo k3d "https://github.com/k3d-io/k3d/releases/download/${K3D_V}/k3d-linux-amd64"
curl -fsSLo k3d.sums "https://github.com/k3d-io/k3d/releases/download/${K3D_V}/checksums.txt"
curl -fsSLo kubectl "https://dl.k8s.io/release/${KUBECTL_V}/bin/linux/amd64/kubectl"
curl -fsSLo kubectl.sha256 "https://dl.k8s.io/release/${KUBECTL_V}/bin/linux/amd64/kubectl.sha256"
curl -fsSLo helm.tgz "https://get.helm.sh/helm-${HELM_V}-linux-amd64.tar.gz"
curl -fsSLo helm.tgz.sha256sum "https://get.helm.sh/helm-${HELM_V}-linux-amd64.tar.gz.sha256sum"
curl -fsSLo uv.tgz "https://github.com/astral-sh/uv/releases/download/${UV_V}/uv-x86_64-unknown-linux-gnu.tar.gz"
curl -fsSLo uv.tgz.sha256 "https://github.com/astral-sh/uv/releases/download/${UV_V}/uv-x86_64-unknown-linux-gnu.tar.gz.sha256"
echo "$(grep 'k3d-linux-amd64$' k3d.sums | awk '{print $1}')  k3d" | sha256sum -c
echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c
echo "$(awk '{print $1}' helm.tgz.sha256sum)  helm.tgz" | sha256sum -c
echo "$(awk '{print $1}' uv.tgz.sha256)  uv.tgz" | sha256sum -c
install -m755 k3d kubectl "${B}/bin/"
tar xzf helm.tgz && install -m755 linux-amd64/helm "${B}/bin/"
tar xzf uv.tgz && install -m755 uv-x86_64-unknown-linux-gnu/uv "${B}/bin/"
cd "${B}" && rm -rf dl
export PATH=${B}/bin:${PATH}
# The banking agent's venv: the desktop venv's `uv pip freeze`, rebuilt (Python 3.12.11). uv 0.13
# refuses to overwrite an existing venv, and an existing one is kept (amendment E4).
[ -d mafvenv ] || uv venv -q --python 3.12.11 mafvenv
uv pip install -q --python mafvenv/bin/python -r "${HERE}/banking-venv.txt"
[ -d eab ] || git clone -q https://github.com/intel/enterprise-ai-blueprints eab
git -C eab checkout -q "${EAB_REV}"
[ -d eat-src ] || git clone -q https://github.com/intel/enterprise-agent-toolkit eat-src
git -C eat-src checkout -q "${EAT_REV}"
git -C eat-src fetch -q origin pull/21/head
mkdir -p eat-pr21 && git -C eat-src archive "${EAT_PR21}" core/helm-charts/vllm | tar -x -C eat-pr21
k3d version && kubectl version --client | head -1 && helm version --short && uv --version
