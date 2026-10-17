#!/usr/bin/env bash
# eat-up — deploy Intel's Enterprise Agent Toolkit (EAT) onto a k3d-gpu cluster
# with EAT's own brownfield path, run from a throwaway Ubuntu bastion container.
#
#   eat-up.sh               deploy the stack selected in agentic-config.cfg
#   eat-up.sh model <id>    deploy one model from EAT's vLLM catalog and register it in LiteLLM
#
#   EAT_WORK        work dir for the EAT checkout and its state (default: ./eat-work)
#   EAT_KUBECONFIG  kubeconfig of the target cluster (default: $KUBECONFIG, else ~/.kube/config)
#   EAT_CONFIG      agentic-config.cfg to deploy with (default: the one next to this script)
#   EAT_VLLM_VALUES values merged over EAT's vllm/xeon-values.yaml (default: vllm-values.yaml here)
#   EAT_VLLM_KV_GIB vLLM CPU KV cache per model in GiB (default: 6). EAT's catalog asks 10-60;
#                   vLLM needs one full-length request to fit (Qwen3-1.7B at 40960 tokens: 4.38 GiB)
#
# The cluster must already carry EAT's node labels, e.g.:
#   K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true k3d-gpu up

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EAT_REPO="https://github.com/intel/enterprise-agent-toolkit"
EAT_TAG="release-1.0.3-hotfix"
EAT_COMMIT="0d3438d7119de44cafc5903f66665c0b623e3333"
EAT_WORK="${EAT_WORK:-${PWD}/eat-work}"
EAT_KUBECONFIG="${EAT_KUBECONFIG:-${KUBECONFIG:-${HOME}/.kube/config}}"
EAT_CONFIG="${EAT_CONFIG:-${HERE}/agentic-config.cfg}"
EAT_VLLM_VALUES="${EAT_VLLM_VALUES:-${HERE}/vllm-values.yaml}"
EAT_VLLM_KV_GIB="${EAT_VLLM_KV_GIB:-6}"
BASTION_IMAGE="k3d-gpu-eat-bastion:$(sha256sum "${HERE}/bastion.Dockerfile" | cut -c1-12)"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[ -f "${EAT_CONFIG}" ] || err "config ${EAT_CONFIG} not found"
# deploy_openshell arrived on EAT main after ${EAT_TAG} (PR #14); the pinned tag has no
# OpenShell role and would skip the key without a word.
! grep -Eq '^deploy_openshell=on([[:space:]]|$)' "${EAT_CONFIG}" \
    || err "deploy_openshell=on needs an EAT release newer than ${EAT_TAG}; set deploy_openshell=off"
[ -f "${EAT_KUBECONFIG}" ] || err "kubeconfig ${EAT_KUBECONFIG} not found"
for l in ei-infra-eligible ei-inference-eligible; do
    [ -n "$(KUBECONFIG="${EAT_KUBECONFIG}" kubectl get nodes -l "${l}=true" -o name)" ] \
        || err "no node labelled ${l}=true — recreate with K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true"
done

if [ ! -d "${EAT_WORK}/eat/.git" ]; then
    info "Cloning ${EAT_REPO} ${EAT_TAG} into ${EAT_WORK}/eat..."
    mkdir -p "${EAT_WORK}"
    git clone -q --depth 1 --branch "${EAT_TAG}" "${EAT_REPO}" "${EAT_WORK}/eat"
fi
[ "$(git -C "${EAT_WORK}/eat" rev-parse HEAD)" = "${EAT_COMMIT}" ] \
    || err "${EAT_WORK}/eat is not ${EAT_TAG} (${EAT_COMMIT})"
cp "${EAT_CONFIG}" "${EAT_WORK}/eat/core/inventory/agentic-config.cfg"

info "Building bastion image ${BASTION_IMAGE}..."
docker build -q -t "${BASTION_IMAGE}" -f "${HERE}/bastion.Dockerfile" "${HERE}" >/dev/null

log="${EAT_WORK}/brownfield-${1:-stack}.log"
info "Running EAT brownfield ${*:-stack} (log: ${log})..."
docker run --rm --network host \
    -v "${EAT_WORK}/eat:/home/ubuntu/eat" \
    -v "${EAT_KUBECONFIG}:/kube/config:ro" \
    -v "${HERE}/brownfield.sh:/brownfield.sh:ro" \
    -v "${EAT_VLLM_VALUES}:/vllm-values.yaml:ro" \
    -e EAT_VLLM_KV_GIB="${EAT_VLLM_KV_GIB}" \
    "${BASTION_IMAGE}" bash /brownfield.sh "$@" 2>&1 | tee "${log}"
