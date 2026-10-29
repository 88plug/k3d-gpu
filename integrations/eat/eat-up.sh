#!/usr/bin/env bash
# eat-up — deploy Intel's Enterprise Agent Toolkit (EAT) onto a k3d-gpu cluster
# with EAT's own brownfield path, run from a throwaway Ubuntu bastion container.
#
#   eat-up.sh               deploy the stack selected in agentic-config.cfg
#   eat-up.sh model <id>    deploy one model from EAT's vLLM catalog and register it in LiteLLM
#
#   EAT_RELEASE     EAT release to deploy (default: release-1.0.3-hotfix):
#                     release-1.0.3-hotfix  commit 0d3438d; vLLM CPU v0.24.0 by digest (vllm-values.yaml)
#                     release-1.1.0         commit 0c726f5; vLLM CPU v0.28.0 by digest
#                                           (vllm-image-release-1.1.0.yaml), OpenShell, and the
#                                           Qwen3Guard guardrail on request (deploy_guardrails=on,
#                                           its image by digest too)
#                   Each release is checked out at its commit only, so an EAT_WORK cloned for
#                   one is refused for the other. The config is checked against the release
#                   before the cluster is touched: EAT's NRI balloons policy is never deployed
#                   (k3d-gpu's stays the only one), nor, on release-1.1.0, its XPU path (a
#                   second, unpinned GPU plugin), Kata (no /dev/kvm in a k3d node) or
#                   rack-scale mode
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
EAT_WORK="${EAT_WORK:-${PWD}/eat-work}"
EAT_KUBECONFIG="${EAT_KUBECONFIG:-${KUBECONFIG:-${HOME}/.kube/config}}"
EAT_CONFIG="${EAT_CONFIG:-${HERE}/agentic-config.cfg}"
EAT_VLLM_VALUES="${EAT_VLLM_VALUES:-${HERE}/vllm-values.yaml}"
CONSOLE_DIR="$(cd "${HERE}/../../console" 2>/dev/null && pwd || true)"
BASTION_IMAGE="k3d-gpu-eat-bastion:$(sha256sum "${HERE}/bastion.Dockerfile" | cut -c1-12)"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

# The releases eat-up.sh deploys: the tag's commit, the values overlay that pins the
# release's vLLM CPU image by digest, and the roles k3d-gpu checks the config against.
# release-1.0.3-hotfix needs no overlay: vllm-values.yaml pins v0.24.0, the tag that
# release names. EAT main is not offered: it is release-1.1.0 plus one picture
# (docs/pictures/Enterprise-Agent-Toolkit-Arch-Diagram.png, EAT #27).
EAT_RELEASE="${EAT_RELEASE:-release-1.0.3-hotfix}"
case "${EAT_RELEASE}" in
    release-1.0.3-hotfix)
        EAT_COMMIT=0d3438d7119de44cafc5903f66665c0b623e3333
        image_values=""
        roles="" ;;
    release-1.1.0)
        EAT_COMMIT=0c726f576595971c53cc45b94e08ba969beb4a4a
        image_values="${HERE}/vllm-image-release-1.1.0.yaml"
        roles="openshell guardrails xpu kata rack-scale" ;;
    *) err "EAT_RELEASE=${EAT_RELEASE} is not supported: use release-1.0.3-hotfix (the default) or release-1.1.0" ;;
esac
has_role() { case " ${roles} " in *" $1 "*) return 0 ;; esac; return 1; }

# config_setting <cfg> <key>: the value EAT's read_config_file gives <key>
# (core/lib/system/precheck/read-config-file.sh, the same at 0d3438d and 0c726f5):
# split on the first '=', key and value trimmed with xargs (which also drops quotes),
# comment lines skipped, and the last assignment wins because the result is sourced.
# EAT then reads on as yes and off as no.
config_setting() {
    local key value v=""
    while IFS='=' read -r key value || [ -n "${key}" ]; do
        key=$(echo "${key}" | xargs 2>/dev/null) || continue
        [ "${key}" = "$2" ] || continue
        v=$(echo "${value}" | xargs 2>/dev/null) || v="${value}"
    done < "$1"
    echo "${v}"
}
setting() { config_setting "${EAT_CONFIG}" "$1"; }

[ -f "${EAT_CONFIG}" ] || err "config ${EAT_CONFIG} not found"
# OpenShell (EAT PR #14) and the content-safety guardrail are roles of release-1.1.0.
# release-1.0.3-hotfix would skip either key without a word.
case "$(setting deploy_openshell)" in
    on|yes) has_role openshell \
        || err "deploy_openshell=on needs EAT_RELEASE=release-1.1.0: ${EAT_RELEASE} has no OpenShell role; set deploy_openshell=off" ;;
esac
# release-1.1.0 sets DEPLOY_GUARDRAILS=on at the top of deploy-agentic-stack.sh;
# brownfield.sh turns it off unless this key is on, and then deploys the guard
# from a chart copy with its image pinned by digest.
case "$(setting deploy_guardrails)" in
    on|yes) has_role guardrails \
        || err "deploy_guardrails=on needs EAT_RELEASE=release-1.1.0: ${EAT_RELEASE} has no guardrail role; set deploy_guardrails=off" ;;
esac
# EAT's NRI balloons policy installs as Helm release nri-resource-policy-balloons in
# kube-system, the DaemonSet k3d-gpu's own policy already is. Both releases turn it on
# when the key is absent (parse-user-prompts.sh:55-60 at 0d3438d, :55-66 at 0c726f5,
# with compute_platform's default, cpu).
balloons=$(setting deploy_nri_balloon_policy)
case "${balloons}" in
    off|no) ;;
    *) err "deploy_nri_balloon_policy=${balloons:-<not set>} deploys EAT's NRI balloons policy (EAT turns it on when the key is absent), and k3d-gpu's balloons policy must remain the only one; set deploy_nri_balloon_policy=off" ;;
esac
if has_role xpu; then
    # deploy-intel-gpu-plugin.yml applies intel-device-plugins' gpu_plugin from main
    # (intel_gpu_plugin_ref=main) beside the plugin k3d-gpu bakes and pins.
    platform=$(setting compute_platform)
    case "${platform}" in
        x|xpu) err "compute_platform=${platform} makes ${EAT_RELEASE} apply intel-device-plugins' GPU plugin from main, unpinned, beside k3d-gpu's own; for vLLM on an Intel GPU use k3d-gpu's: K3D_GPU_AGENT_BACKEND=vllm k3d-gpu agent up" ;;
    esac
fi
if has_role kata; then
    case "$(setting deploy_kata_containers)" in
        on|yes) err "deploy_kata_containers=on runs sandboxes in Kata microVMs, which need /dev/kvm, and k3d nodes have no /dev/kvm; set deploy_kata_containers=off" ;;
    esac
fi
if has_role rack-scale; then
    # rack-scale mode generates the inventory from rack-profile.yaml and pushes netplan,
    # BGP and, for GPU nodes in the profile, the NVIDIA GPU operator.
    case "$(setting rack_scale_mode)" in
        on|yes) err "rack_scale_mode=on builds a cluster from rack-profile.yaml (netplan, BGP, the NVIDIA GPU operator); a k3d-gpu cluster is brownfield: set rack_scale_mode=off" ;;
    esac
fi
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
    info "Cloning ${EAT_REPO} ${EAT_RELEASE} into ${EAT_WORK}/eat..."
    mkdir -p "${EAT_WORK}"
    git clone -q --depth 1 --branch "${EAT_RELEASE}" "${EAT_REPO}" "${EAT_WORK}/eat"
fi
found=$(git -C "${EAT_WORK}/eat" rev-parse HEAD)
[ "${found}" = "${EAT_COMMIT}" ] \
    || err "${EAT_WORK}/eat is not ${EAT_RELEASE}: expected commit ${EAT_COMMIT}, found HEAD ${found}; use a fresh EAT_WORK for each release"
cp "${EAT_CONFIG}" "${EAT_WORK}/eat/core/inventory/agentic-config.cfg"

info "Building bastion image ${BASTION_IMAGE}..."
docker build -q -t "${BASTION_IMAGE}" -f "${HERE}/bastion.Dockerfile" "${HERE}" >/dev/null

log="${EAT_WORK}/brownfield-${1:-stack}.log"
info "Running EAT ${EAT_RELEASE} brownfield ${*:-stack} (log: ${log})..."
mkdir -p "${EAT_WORK}/hf-cache"
console_mount=()
[ -z "${CONSOLE_DIR}" ] || console_mount=(-v "${CONSOLE_DIR}:/k3d-gpu-console:ro")
# The release's vLLM image overlay, merged last by brownfield.sh (vllm-merge.py).
image_mount=()
[ -z "${image_values}" ] || image_mount=(-v "${image_values}:/vllm-image-values.yaml:ro" -e EAT_VLLM_IMAGE_VALUES=/vllm-image-values.yaml)
docker run --rm --network host \
    -v "${EAT_WORK}/eat:/home/ubuntu/eat" \
    -v "${EAT_KUBECONFIG}:/kube/config:ro" \
    -v "${HERE}/brownfield.sh:/brownfield.sh:ro" \
    -v "${EAT_VLLM_VALUES}:/vllm-values.yaml:ro" \
    ${image_mount[@]+"${image_mount[@]}"} \
    -v "${HERE}:/k3d-gpu-eat:ro" \
    -v "${EAT_WORK}/hf-cache:/hf-cache" \
    ${console_mount[@]+"${console_mount[@]}"} \
    -e EAT_VLLM_KV_GIB="${EAT_VLLM_KV_GIB}" \
    -e EAT_MODEL_OPT="${EAT_MODEL_OPT:-auto}" \
    -e EAT_THINKING="${EAT_THINKING:-off}" \
    "${BASTION_IMAGE}" bash /brownfield.sh "$@" 2>&1 | tee "${log}"
