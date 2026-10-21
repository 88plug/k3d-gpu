#!/usr/bin/env bash
# Schema-validates every manifest `k3d-gpu agent manifest` can render: the LLM
# alone and with each add-on, on GPU, NPU and CPU placements. The cluster is a
# stubbed kubectl; validation is kubeconform (from PATH, else its pinned image).
#
# Usage: tests/agent_kubeconform.sh

set -euo pipefail

K3D_GPU="$(cd "$(dirname "$0")/.." && pwd)/scripts/k3d-gpu"
# Kubernetes version of the k3s node image.
K8S_VERSION=$(sed -n 's/^ARG K3S_TAG="v\([0-9.]*\)-k3s.*/\1/p' "$(dirname "$0")/../Dockerfile")
# ghcr.io/yannh/kubeconform:v0.8.0
KUBECONFORM_IMAGE="ghcr.io/yannh/kubeconform@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e"

WORK=$(mktemp -d)
# A measured autoquant pick on this dev box (EXPERIMENTS.md H27) must not leak into goldens.
export K3D_GPU_AUTOQUANT_CACHE="${WORK}/no-autoquant.json"
trap 'rm -rf "${WORK}"' EXIT
mkdir -p "${WORK}/bin" "${WORK}/out" "${WORK}/root/sys" "${WORK}/root/dev"
cat > "${WORK}/bin/kubectl" <<'EOF'
#!/bin/sh
case "$*" in
    *allocatable*) printf '%s\n' "${STUB_ALLOC:-}" ;;
    "get nodes -l "*) [ -z "${STUB_HYBRID:-}" ] || echo node/k3d-agent-server-0 ;;
esac
exit 0
EOF
chmod +x "${WORK}/bin/kubectl"

# render <file> <resources> [env assignments...] -- [agent args...]: the render
# must succeed and hold exactly <resources> objects. A failed or partial render
# would otherwise reach kubeconform as a smaller, still valid, set.
TOTAL=0
render() {
    local file=$1 want=$2 got rc=0; shift 2
    local -a envs=()
    while [ "$1" != -- ]; do envs+=("$1"); shift; done
    shift
    env K3D_GPU_SYSROOT="${WORK}/root" PATH="${WORK}/bin:${PATH}" ${envs[@]+"${envs[@]}"} \
        bash "${K3D_GPU}" agent manifest "$@" > "${WORK}/out/${file}.yaml" 2> "${WORK}/${file}.err" || rc=$?
    got=$(grep -c '^kind: ' "${WORK}/out/${file}.yaml" || true)
    if [ "${rc}" -ne 0 ] || [ "${got}" -ne "${want}" ]; then
        echo "FAIL render ${file}: exit ${rc}, ${got} resources (want ${want})" >&2
        sed 's/^/  | /' "${WORK}/${file}.err" >&2
        exit 1
    fi
    TOTAL=$((TOTAL + want))
}

# The LLM is 4 resources (Namespace, PVC, Deployment, Service); vlm and stt
# add 3 each (PVC, Deployment, Service), video 2 (ConfigMap, Deployment).
ALL=$'cpu 32\ngpu.intel.com/i915 4\nnpu.intel.com/accel 1'
render llm-cpu       4  STUB_ALLOC='cpu 32' --
render llm-gpu       4  STUB_ALLOC="${ALL}" --
render stack-shared  12 STUB_ALLOC="${ALL}" -- --with vlm,stt,video
render stack-one-gpu 12 STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1\nnpu.intel.com/accel 1' -- --with vlm,stt,video
render stack-cpu     12 STUB_ALLOC='cpu 32' STUB_HYBRID=1 -- --with vlm,stt,video
render vlm-npu       7  STUB_ALLOC="${ALL}" K3D_GPU_AGENT_VLM_DEVICE=NPU K3D_GPU_AGENT_VLM_MEDIA_DOMAINS=example.com -- --with vlm
render video-rtsp    6  STUB_ALLOC="${ALL}" K3D_GPU_AGENT_VIDEO_SOURCE=rtsp://cam/stream -- --with video
render stack-vllm    9  STUB_ALLOC=$'cpu 32\ngpu.intel.com/xe 3' K3D_GPU_AGENT_BACKEND=vllm -- --with vlm,video
render stack-gaudi   6  STUB_ALLOC=$'cpu 32\nhabana.ai/gaudi 1\ngpu.intel.com/i915 1' -- --with video
# Sized by fit (tests/fixtures/fit): vLLM memory flags and an OVMS static KV pool,
# each with a pod memory limit.
FIT="$(dirname "$0")/fixtures/fit"
mkdir -p "${WORK}/fit-cache"
for d in "${FIT}"/models/*/; do ln -s "$(cd "${d}" && pwd)" "${WORK}/fit-cache/$(basename "${d}")@main"; done
render fit-vllm      4  STUB_ALLOC=$'cpu 32\ngpu.intel.com/xe 4' K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_FIT_CACHE="${WORK}/fit-cache" \
    K3D_GPU_FIT_HOST="${FIT}/hosts/xeon6741p-4xb70.json" XDG_STATE_HOME="${WORK}" -- --model RedHatAI/Qwen3.8-27B-INT4
render fit-ovms-cpu  4  STUB_ALLOC='cpu 32' K3D_GPU_FIT_CACHE="${WORK}/fit-cache" \
    K3D_GPU_FIT_HOST="${FIT}/hosts/desktop-i9-nogpu.json" XDG_STATE_HOME="${WORK}" -- --model OpenVINO/Qwen3.8-27B-int4-ov
# llama.cpp, the engine the fit picks for GGUF weights and for Flash-Next (no backend set):
# on a B70, on the CPU, converted once (ConfigMap and Job) and with the MTP drafter.
XEON_FIT=(K3D_GPU_FIT_CACHE="${WORK}/fit-cache" K3D_GPU_FIT_HOST="${FIT}/hosts/xeon6741p-4xb70.json" XDG_STATE_HOME="${WORK}"
    K3D_GPU_AGENT_DEVICE=GPU STUB_ALLOC=$'cpu 64\ngpu.intel.com/xe 2')
render fit-llama-gpu 4 "${XEON_FIT[@]}" -- --model google/gemma-4-26B-A4B-it-qat-q4_0-gguf
render fit-llama-cpu 4 STUB_ALLOC='cpu 32' K3D_GPU_FIT_CACHE="${WORK}/fit-cache" \
    K3D_GPU_FIT_HOST="${FIT}/hosts/desktop-i9-nogpu.json" XDG_STATE_HOME="${WORK}" -- --model google/gemma-4-26B-A4B-it-qat-q4_0-gguf
render fit-llama-convert 6 "${XEON_FIT[@]}" -- --model Qwen/Qwen3.8-Flash-Next
render fit-llama-mtp 6 "${XEON_FIT[@]}" K3D_GPU_AGENT_DRAFT=mtp -- --model google/gemma-4-26B-A4B-it-qat-q4_0-gguf

args=(-strict -summary -kubernetes-version "${K8S_VERSION}")
rc=0
if command -v kubeconform >/dev/null 2>&1; then
    summary=$(kubeconform "${args[@]}" "${WORK}"/out/*.yaml) || rc=$?
else
    summary=$(docker run --rm -v "${WORK}/out:/m:ro" "${KUBECONFORM_IMAGE}" "${args[@]}" /m) || rc=$?
fi
printf '%s\n' "${summary}"
[ "${rc}" -eq 0 ] || exit "${rc}"
# Every rendered resource must be validated, none skipped.
grep -q "Valid: ${TOTAL}, Invalid: 0, Errors: 0, Skipped: 0" <<< "${summary}" \
    || { echo "FAIL kubeconform did not validate all ${TOTAL} resources" >&2; exit 1; }
