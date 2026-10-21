#!/usr/bin/env bash
# install.sh K3D_GPU_VERSION: the wildct campaign's tools, pinned and checksum-checked, into
# ~/k3dgpu-bench-wildct (EXPERIMENTS.md "Wildct pre-registration", rule W7); nothing
# system-wide. k3d v5.9.0, helm v4.3.1 and kubectl v1.37.1 (each the newest release on
# 2026-10-20) into bin/; the k3d-gpu release's .deb, checked against the release's
# SHA256SUMS, unpacked into k3d-gpu/ (k3d-gpu/usr/bin/k3d-gpu with K3D_GPU_SHARE pointing at
# k3d-gpu/usr/share/k3d-gpu); huggingface_hub 1.21.0 (crescent's pin) in hfvenv/. Prints
# every version and checksum.
set -euo pipefail
V=${1:?usage: install.sh K3D_GPU_VERSION}
W=${HOME}/k3dgpu-bench-wildct
mkdir -p "${W}/bin" "${W}/dl"
cd "${W}/dl"
get() { curl -fsSL --retry 3 -o "$2" "$1"; }
check() { echo "$1  $2" | sha256sum -c - >/dev/null || { echo "checksum mismatch: $2"; exit 1; }; echo "ok $2 $1"; }

get https://github.com/k3d-io/k3d/releases/download/v5.9.0/k3d-linux-amd64 k3d
check 06d8f25bc3a971c4eb29e0ff08429b180402db0f4dec838c9eac427e296800a0 k3d
install -m 0755 k3d "${W}/bin/k3d"

get https://get.helm.sh/helm-v4.3.1-linux-amd64.tar.gz helm.tgz
check 0403a88ae20913d11b10a2467b50087721fea7581736da6b9d3992f5357fe54b helm.tgz
tar -xzf helm.tgz linux-amd64/helm
install -m 0755 linux-amd64/helm "${W}/bin/helm"

get https://dl.k8s.io/release/v1.37.1/bin/linux/amd64/kubectl kubectl
get https://dl.k8s.io/release/v1.37.1/bin/linux/amd64/kubectl.sha256 kubectl.sha256
check "$(cat kubectl.sha256)" kubectl
install -m 0755 kubectl "${W}/bin/kubectl"

base=https://github.com/88plug/k3d-gpu/releases/download/aur-v${V}
deb=k3d-gpu_${V}-1_all.deb
get "${base}/${deb}" "${deb}"
get "${base}/SHA256SUMS" SHA256SUMS
check "$(awk -v f="${deb}" '$2 == f {print $1}' SHA256SUMS)" "${deb}"
rm -rf "${W}/k3d-gpu" && mkdir -p "${W}/k3d-gpu"
( cd "${W}/k3d-gpu" && ar p "${W}/dl/${deb}" "$(ar t "${W}/dl/${deb}" | grep '^data.tar')" | bsdtar -xf - )
ln -sfn "${W}/k3d-gpu/usr/bin/k3d-gpu" "${W}/bin/k3d-gpu"

[ -x "${W}/hfvenv/bin/python" ] || uv venv -q "${W}/hfvenv"
VIRTUAL_ENV=${W}/hfvenv uv pip install -q huggingface_hub==1.21.0
export PATH=${W}/bin:${PATH} K3D_GPU_SHARE=${W}/k3d-gpu/usr/share/k3d-gpu
k3d version | head -1; helm version --short; kubectl version --client | head -1
k3d-gpu version
"${W}/hfvenv/bin/python" -c 'import huggingface_hub; print("huggingface_hub", huggingface_hub.__version__)'
rm -rf "${W}/dl"
