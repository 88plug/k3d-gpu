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
#   EAT_VLLM_KV_GIB vLLM CPU KV cache per model in GiB (default: 6, or from EAT_FIT_MODEL). EAT's
#                   catalog asks 10-60; vLLM needs one full-length request to fit (Qwen3-1.7B at
#                   40960 tokens: 4.38 GiB)
#   EAT_FIT_MODEL   Hugging Face id of the model 'eat-up.sh model' deploys: with EAT_VLLM_KV_GIB
#                   unset, its KV cache size comes from 'k3d-gpu fit' (vLLM on the CPU, against what
#                   this host has free), and a model that cannot fit stops here
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
BASTION_IMAGE="k3d-gpu-eat-bastion:$(sha256sum "${HERE}/bastion.Dockerfile" | cut -c1-12)"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

# openshell_setting <cfg>: the deploy_openshell value EAT main would use. Parsed the
# way its read_config_file does (core/lib/system/precheck/read-config-file.sh @0ecf67a):
# split on the first '=', trim key and value with xargs (which also drops quotes),
# skip comments, and the last assignment wins because the result is sourced.
openshell_setting() {
    local key value v=""
    while IFS='=' read -r key value || [ -n "${key}" ]; do
        key=$(echo "${key}" | xargs 2>/dev/null) || continue
        [ "${key}" = deploy_openshell ] || continue
        v=$(echo "${value}" | xargs 2>/dev/null) || v="${value}"
    done < "$1"
    echo "${v}"
}

[ -f "${EAT_CONFIG}" ] || err "config ${EAT_CONFIG} not found"
# deploy_openshell arrived on EAT main after ${EAT_TAG} (PR #14); the pinned tag has no
# OpenShell role and would skip the key without a word. EAT maps on to yes and
# installs OpenShell when the value is yes (fresh-install.sh).
case "$(openshell_setting "${EAT_CONFIG}")" in
    on|yes) err "deploy_openshell=on needs an EAT release newer than ${EAT_TAG}; set deploy_openshell=off" ;;
esac
# The KV cache from the fit of EAT_FIT_MODEL, unless EAT_VLLM_KV_GIB is set.
if [ -z "${EAT_VLLM_KV_GIB:-}" ] && [ -n "${EAT_FIT_MODEL:-}" ]; then
    command -v k3d-gpu >/dev/null 2>&1 || err "EAT_FIT_MODEL needs k3d-gpu on PATH (or set EAT_VLLM_KV_GIB)"
    fit_rc=0
    fit_doc=$(k3d-gpu fit --model "${EAT_FIT_MODEL}" --engine vllm --device cpu --json) || fit_rc=$?
    [ "${fit_rc}" -ne 2 ] && [ -n "${fit_doc}" ] || err "k3d-gpu fit could not size ${EAT_FIT_MODEL}"
    EAT_VLLM_KV_GIB=$(python3 -c 'import json, sys
d = json.loads(sys.argv[1])
if not d["fits"]:
    sys.exit(d["summary"])
print(next(f.split("=", 1)[1] for f in d["flags"] if f.startswith("VLLM_CPU_KVCACHE_SPACE=")))' "${fit_doc}") \
        || err "not deploying ${EAT_FIT_MODEL}: it does not fit this host ('k3d-gpu fit --model ${EAT_FIT_MODEL} --engine vllm --device cpu --explain')"
    info "KV cache ${EAT_VLLM_KV_GIB} GiB from k3d-gpu fit: $(python3 -c 'import json, sys; print(json.loads(sys.argv[1])["summary"])' "${fit_doc}")"
fi
EAT_VLLM_KV_GIB="${EAT_VLLM_KV_GIB:-6}"
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
    -v "${HERE}:/k3d-gpu-eat:ro" \
    -e EAT_VLLM_KV_GIB="${EAT_VLLM_KV_GIB}" \
    "${BASTION_IMAGE}" bash /brownfield.sh "$@" 2>&1 | tee "${log}"
