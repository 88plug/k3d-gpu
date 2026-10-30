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
#   EAT_MODEL_OPT   auto | off: model <id> gets k3d-gpu's model optimizer as values (parsers,
#                   thinking default, text-only load, speculative config; printed against
#                   EAT stock) (default: auto)
#   EAT_THINKING    off | on | model: the model's thinking default (default: off, for agents)
#
# The cluster must already carry EAT's node labels, e.g.:
#   K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true k3d-gpu up

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EAT_REPO="https://github.com/intel/enterprise-agent-toolkit"
EAT_TAG="release-1.1.0"
EAT_COMMIT="0c726f576595971c53cc45b94e08ba969beb4a4a"
EAT_WORK="${EAT_WORK:-${PWD}/eat-work}"
EAT_KUBECONFIG="${EAT_KUBECONFIG:-${KUBECONFIG:-${HOME}/.kube/config}}"
EAT_CONFIG="${EAT_CONFIG:-${HERE}/agentic-config.cfg}"
EAT_VLLM_VALUES="${EAT_VLLM_VALUES:-${HERE}/vllm-values.yaml}"
CONSOLE_DIR="$(cd "${HERE}/../../console" 2>/dev/null && pwd || true)"
BASTION_IMAGE="k3d-gpu-eat-bastion:$(sha256sum "${HERE}/bastion.Dockerfile" | cut -c1-12)"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

# cfg_value <cfg> <key>: the value EAT would use for <key>. Parsed the way its
# read_config_file does (core/lib/system/precheck/read-config-file.sh at ${EAT_TAG}):
# split on the first '=', trim key and value with xargs (which also drops quotes),
# skip comments, and the last assignment wins because the result is sourced.
cfg_value() {
    local key value v=""
    while IFS='=' read -r key value || [ -n "${key}" ]; do
        key=$(echo "${key}" | xargs 2>/dev/null) || continue
        [ "${key}" = "$2" ] || continue
        v=$(echo "${value}" | xargs 2>/dev/null) || v="${value}"
    done < "$1"
    echo "${v}"
}

[ -f "${EAT_CONFIG}" ] || err "config ${EAT_CONFIG} not found"
# ${EAT_TAG} ships NVIDIA OpenShell (PR #14) and installs it when the value is yes, after
# mapping on to yes (fresh-install.sh). It has never run on a k3d-gpu cluster: upstream
# lists a CNI that enforces NetworkPolicy and best-effort Landlock (docs/openshell.md).
case "$(cfg_value "${EAT_CONFIG}" deploy_openshell)" in
    on|yes) err "deploy_openshell=on is unverified on k3d-gpu (${EAT_TAG} ships OpenShell, but it has not run on a k3d-gpu cluster); set deploy_openshell=off" ;;
esac
# With no deploy_guardrails value, ${EAT_TAG} deploys its Qwen3Guard guardrail:
# deploy-agentic-stack.sh:195 defaults DEPLOY_GUARDRAILS to on and brownfield.sh keeps
# that default. A config written for release-1.0.3-hotfix has no such key, so the
# choice is asked for here rather than made for the user.
[ -n "$(cfg_value "${EAT_CONFIG}" deploy_guardrails)" ] \
    || err "${EAT_CONFIG} does not set deploy_guardrails, so ${EAT_TAG} would deploy its Qwen3Guard guardrail, which has not run on a k3d-gpu cluster; set deploy_guardrails=off (as the shipped agentic-config.cfg does) or deploy_guardrails=on"
# Settings with which ${EAT_TAG} puts a second copy of what a k3d-gpu cluster already
# runs next to it, or needs what a k3d node lacks (core/lib/cluster/deployment/fresh-install.sh):
# - its NRI balloons policy on yes (:247). brownfield.sh model mode's prompt_for_input
#   turns an absent key on for compute_platform c (parse-user-prompts.sh:55-66).
# - intel-device-plugins' gpu_plugin from its main branch, unpinned, on xpu (:219,
#   playbooks/deploy-intel-gpu-plugin.yml:12-14).
# - Kata Containers, which needs /dev/kvm on the node (playbooks/deploy-kata-containers.yml).
# - rack-scale mode, which builds the cluster from rack-profile.yaml: netplan, BGP and the
#   NVIDIA GPU operator (:195-210).
case "$(cfg_value "${EAT_CONFIG}" deploy_nri_balloon_policy)" in
    off|no) ;;
    *) err "${EAT_CONFIG}: deploy_nri_balloon_policy must be off: ${EAT_TAG} deploys its own NRI balloons policy when it is on, and in model mode when it is absent, beside k3d-gpu's, which must remain the only one" ;;
esac
case "$(cfg_value "${EAT_CONFIG}" compute_platform)" in
    x|xpu) err "compute_platform=xpu: ${EAT_TAG} then applies intel-device-plugins' gpu_plugin from its main branch, unpinned, beside the GPU plugin k3d-gpu bakes; for vLLM on an Intel GPU run K3D_GPU_AGENT_BACKEND=vllm k3d-gpu agent up" ;;
esac
case "$(cfg_value "${EAT_CONFIG}" deploy_kata_containers)" in
    on|yes) err "deploy_kata_containers=on: Kata needs /dev/kvm on the node, and a k3d node is a container without it; set deploy_kata_containers=off" ;;
esac
case "$(cfg_value "${EAT_CONFIG}" rack_scale_mode)" in
    on|yes) err "rack_scale_mode=on: ${EAT_TAG} then builds a cluster from rack-profile.yaml (netplan, BGP, the NVIDIA GPU operator), and a k3d-gpu cluster is brownfield; set rack_scale_mode=off" ;;
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
mkdir -p "${EAT_WORK}/hf-cache"
console_mount=()
[ -z "${CONSOLE_DIR}" ] || console_mount=(-v "${CONSOLE_DIR}:/k3d-gpu-console:ro")
docker run --rm --network host \
    -v "${EAT_WORK}/eat:/home/ubuntu/eat" \
    -v "${EAT_KUBECONFIG}:/kube/config:ro" \
    -v "${HERE}/brownfield.sh:/brownfield.sh:ro" \
    -v "${EAT_VLLM_VALUES}:/vllm-values.yaml:ro" \
    -v "${HERE}:/k3d-gpu-eat:ro" \
    -v "${EAT_WORK}/hf-cache:/hf-cache" \
    ${console_mount[@]+"${console_mount[@]}"} \
    -e EAT_VLLM_KV_GIB="${EAT_VLLM_KV_GIB}" \
    -e EAT_MODEL_OPT="${EAT_MODEL_OPT:-auto}" \
    -e EAT_THINKING="${EAT_THINKING:-off}" \
    "${BASTION_IMAGE}" bash /brownfield.sh "$@" 2>&1 | tee "${log}"
