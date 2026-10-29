#!/usr/bin/env bash
# Runs inside the bastion container (bastion.Dockerfile). Drives EAT's own
# brownfield path, brownfield_deployment() in core/lib/brownfield/, which no
# EAT menu reaches in release-1.0.3-hotfix or release-1.1.0. Its interactive
# prompts get fixed answers; every component toggle comes from
# core/inventory/agentic-config.cfg.
#
#   brownfield.sh               deploy the stack selected in agentic-config.cfg
#   brownfield.sh model <id>    deploy one model from EAT's vLLM playbook and register it in LiteLLM
#
#   EAT_DIR          EAT checkout (default: /home/ubuntu/eat)
#   EAT_KUBECONFIG   kubeconfig of the target cluster (default: /kube/config)
#   EAT_VLLM_VALUES  values merged over EAT's vllm/xeon-values.yaml (default: /vllm-values.yaml)
#   EAT_VLLM_IMAGE_VALUES  the release's vLLM image overlay, merged last (default: none;
#                    eat-up.sh mounts vllm-image-release-1.1.0.yaml for release-1.1.0). It
#                    also pins the guard image when deploy_guardrails=on
#   EAT_VLLM_KV_GIB  VLLM_CPU_KVCACHE_SPACE for every catalog model, in GiB (default: 6)
#   K3D_GPU_EAT_DIR  integrations/eat of k3d-gpu (default: /k3d-gpu-eat): gateway-charts.py,
#                    vllm-merge.py, eat-opt.py and the gateway values overlays
#   K3D_GPU_CONSOLE_DIR  console/ of k3d-gpu (default: /k3d-gpu-console): the model optimizer
#   EAT_MODEL_OPT    auto | off: apply the model optimizer to the model's values (default: auto)
#   EAT_THINKING     off | on | model: thinking default for the model (default: off)
#   EAT_HF_CACHE     model metadata cache (default: /hf-cache)
#
# Variables set below without a local use are read by EAT's sourced functions.
# shellcheck disable=SC1091,SC2034,SC2153,SC2154

set -euo pipefail

EAT_DIR="${EAT_DIR:-/home/ubuntu/eat}"
EAT_KUBECONFIG="${EAT_KUBECONFIG:-/kube/config}"
EAT_VLLM_VALUES="${EAT_VLLM_VALUES:-/vllm-values.yaml}"
EAT_VLLM_IMAGE_VALUES="${EAT_VLLM_IMAGE_VALUES:-}"
EAT_VLLM_KV_GIB="${EAT_VLLM_KV_GIB:-6}"
K3D_GPU_EAT_DIR="${K3D_GPU_EAT_DIR:-/k3d-gpu-eat}"
CERT_DIR="${HOME}/certs"
CONFIG="${EAT_DIR}/core/inventory/agentic-config.cfg"

mode="${1:-stack}"
model_id="${2:-}"
case "${mode}" in
    stack) ;;
    model) [ -n "${model_id}" ] || { echo "usage: brownfield.sh [stack | model <id>]" >&2; exit 2; } ;;
    *) echo "usage: brownfield.sh [stack | model <id>]" >&2; exit 2 ;;
esac

# config_setting <cfg> <key>: the value EAT's read_config_file gives <key>, parsed as
# eat-up.sh parses it: split on the first '=', key and value trimmed with xargs (which
# also drops quotes), comment lines skipped, the last assignment wins.
config_setting() {
    local key value v=""
    while IFS='=' read -r key value || [ -n "${key}" ]; do
        key=$(echo "${key}" | xargs 2>/dev/null) || continue
        [ "${key}" = "$2" ] || continue
        v=$(echo "${value}" | xargs 2>/dev/null) || v="${value}"
    done < "$1"
    echo "${v}"
}

cluster_url=$(grep -E '^cluster_url=' "${CONFIG}" | cut -d= -f2-)

# The content-safety guardrail (Qwen3Guard) follows the config's deploy_guardrails.
# release-1.1.0's deploy-agentic-stack.sh sets DEPLOY_GUARDRAILS="${DEPLOY_GUARDRAILS:-on}"
# at top level (line 195), which runs when it is sourced below; it reads the
# deploy_guardrails key only in main() (lines 1136-1140), which a sourced run skips,
# and fresh-install.sh (line 419) deploys the guard when the variable is on. So the
# key is applied here, whatever the environment says: on or yes deploys the guard,
# anything else (or no key) does not. release-1.0.3-hotfix never reads the variable.
case "$(config_setting "${CONFIG}" deploy_guardrails)" in
    on|yes) export DEPLOY_GUARDRAILS=on ;;
    *) export DEPLOY_GUARDRAILS=off ;;
esac
if [ "${mode}" = stack ] && [ "${DEPLOY_GUARDRAILS}" = on ] && [ -z "${EAT_VLLM_IMAGE_VALUES}" ]; then
    echo "ERROR: deploy_guardrails=on needs EAT_VLLM_IMAGE_VALUES, the image overlay that pins the guard's vLLM by digest (eat-up.sh EAT_RELEASE=release-1.1.0)" >&2
    exit 1
fi

# EAT's one-click script makes a self-signed cert and an /etc/hosts entry; the
# brownfield path expects both to exist. This is the container's /etc/hosts.
if [ ! -f "${CERT_DIR}/cert.pem" ]; then
    mkdir -p "${CERT_DIR}"
    openssl req -x509 -newkey rsa:4096 -nodes -days 365 \
        -keyout "${CERT_DIR}/key.pem" -out "${CERT_DIR}/cert.pem" \
        -subj "/CN=${cluster_url}" \
        -addext "subjectAltName=DNS:${cluster_url},DNS:trace-${cluster_url}" 2>/dev/null
fi

# Some EAT playbooks run with --become (deploy-cluster-config.yml via
# cluster-config-init.sh) and call kubectl as root. On a Kubespray node root has
# its own kubeconfig; brownfield only copies it to ~/.kube/config and sudo drops
# KUBECONFIG, so root's kubectl falls back to localhost:8080. Give root the same one.
sudo install -D -m 0600 "${EAT_KUBECONFIG}" /root/.kube/config

cd "${EAT_DIR}"
# deploy-agentic-stack.sh parses "$@" at top level even when sourced, and rejects
# "model" as an unknown argument, so it is sourced with no positional arguments.
# DEPLOY_SOURCED=1 loads its functions without running its main().
set --
DEPLOY_SOURCED=1 source ./deploy-agentic-stack.sh
_source_core_libs
cd "${CORE_DIR}"
kubeconfig_file="${EAT_KUBECONFIG}"

if [ "${mode}" = "stack" ]; then
    # GenAI Gateway: deploy-genai-gateway.yml installs from the chart directories in
    # helm_charts_base_genai{,_trace}, set by its vars_files. Extra vars take precedence
    # over those, so that playbook alone gets copies with exact dependency versions and
    # every image pinned by digest (gateway-charts.py; EAT's checkout is not edited).
    charts="${HOME}/k3d-gpu-charts"
    python3 "${K3D_GPU_EAT_DIR}/gateway-charts.py" "${CORE_DIR}/helm-charts" \
        "${K3D_GPU_EAT_DIR}/gateway-values.yaml" "${K3D_GPU_EAT_DIR}/gateway-trace-values.yaml" "${charts}"
    gateway_vars="${HOME}/k3d-gpu-gateway-vars.yml"
    printf 'helm_charts_base_genai: %s\nhelm_charts_base_genai_trace: %s\n' \
        "${charts}/genai-gateway" "${charts}/genai-gateway-trace" > "${gateway_vars}"
    # The guard: deploy-guardrails.yml (release-1.1.0) installs the chart
    # {{ remote_helm_charts_base }}/vllm with --values {{ remote_helm_charts_base }}/vllm/guardrail-values.yaml,
    # which names vllm/vllm-openai-cpu:v0.28.0 by tag only. In that playbook,
    # remote_helm_charts_base (from its vars_file inference_common.yml) serves nothing
    # else, so an extra var points it at a copy of EAT's vllm chart whose
    # guardrail-values.yaml carries the image overlay's digest (vllm-merge.py).
    guard_vars=""
    guard_marker="${HOME}/k3d-gpu-guard-pinned"
    rm -f "${guard_marker}"
    if [ "${DEPLOY_GUARDRAILS}" = on ]; then
        guard_charts="${HOME}/k3d-gpu-guard-charts"
        rm -rf "${guard_charts}"
        mkdir -p "${guard_charts}"
        cp -r "${CORE_DIR}/helm-charts/vllm" "${guard_charts}/vllm"
        python3 "${K3D_GPU_EAT_DIR}/vllm-merge.py" --require-digest "${CORE_DIR}/helm-charts/vllm/guardrail-values.yaml" \
            "${guard_charts}/vllm/guardrail-values.yaml" "${EAT_VLLM_IMAGE_VALUES}"
        guard_vars="${HOME}/k3d-gpu-guard-vars.yml"
        printf 'remote_helm_charts_base: %s\n' "${guard_charts}" > "${guard_vars}"
        echo "guard chart: ${guard_charts}/vllm (EAT's, guardrail-values.yaml image from ${EAT_VLLM_IMAGE_VALUES})"
    fi
    # EAT's functions run in this shell and call ansible-playbook by name. The
    # marker files record that the gateway and guard playbooks got the copies.
    gateway_marker="${HOME}/k3d-gpu-gateway-pinned"
    rm -f "${gateway_marker}"
    ansible-playbook() {
        case " $* " in
            *"deploy-genai-gateway.yml "*)
                : > "${gateway_marker}"
                command ansible-playbook "$@" --extra-vars "@${gateway_vars}" ;;
            *"deploy-guardrails.yml "*)
                [ -n "${guard_vars}" ] || { echo "ERROR: deploy-guardrails.yml called with deploy_guardrails off" >&2; return 1; }
                : > "${guard_marker}"
                command ansible-playbook "$@" --extra-vars "@${guard_vars}" ;;
            *) command ansible-playbook "$@" ;;
        esac
    }
    # Answers, in prompt order:
    #   5    platform menu: EAT detects the platform before its bastion playbook
    #        installs kubectl, so a fresh bastion always asks (and k3s's
    #        v1.34.1+k3s1 would fail its bare vX.Y.Z "vanilla" match anyway); 5 = Vanilla
    #   1    brownfield menu: Deploy Inference Stack
    #   yes  "continue with Brownfield Deployment setup?"
    printf '5\n1\nyes\n' | brownfield_deployment
    if grep -Eq '^deploy_genai_gateway=on' "${CONFIG}" && [ ! -f "${gateway_marker}" ]; then
        echo "ERROR: deploy-genai-gateway.yml never ran through the pinning wrapper" >&2
        exit 1
    fi
    if [ "${DEPLOY_GUARDRAILS}" = on ] && [ ! -f "${guard_marker}" ]; then
        echo "ERROR: deploy-guardrails.yml never ran through the pinning wrapper" >&2
        exit 1
    fi
    exit
fi

# model <catalog id>: the same calls brownfield "Manage Models" makes, made
# directly because that menu validates ids against a shorter list than the
# playbook serves (cpu-qwen3-1-7b has playbook tasks but is not in get_model_names).
manage_kubeconfig
kubernetes_platform=vanilla
brownfield_deployment=yes
skip_check=true
read_config_file
prompt_for_input </dev/null # no prompts left: every value is in agentic-config.cfg
setup_initial_env # leaves the shell in core/kubespray, where EAT's playbooks run from
setup_bastion

# EAT passes one values file to its vLLM chart (--values xeon_values_file). Give it
# EAT's own xeon-values.yaml with the k3d-gpu values merged on top (then the
# release's image overlay, when eat-up.sh mounts one), and every model's KV cache set
# to EAT_VLLM_KV_GIB, instead of editing the checkout. vllm-merge.py stops here when
# the merged vLLM tag is not the one EAT's file names.
merged="${HOME}/xeon-values.k3d-gpu.yaml"
overlays=("${EAT_VLLM_VALUES}")
[ -z "${EAT_VLLM_IMAGE_VALUES}" ] || overlays+=("${EAT_VLLM_IMAGE_VALUES}")
python3 "${K3D_GPU_EAT_DIR}/vllm-merge.py" --kv-gib "${EAT_VLLM_KV_GIB}" \
    "${HOMEDIR}/helm-charts/${xeon_values_file_path#"${REMOTEDIR}"/}" "${merged}" "${overlays[@]}"
echo "vLLM values: ${merged} (EAT xeon-values.yaml + ${overlays[*]}, KV cache ${EAT_VLLM_KV_GIB} GiB)"
# The k3d-gpu model optimizer, as values on the model's own entry (eat-opt.py):
# engine arguments EAT's entry lacks, a speculative config, the thinking default.
# It prints each change against EAT stock; any failure leaves the merged values.
python3 "${K3D_GPU_EAT_DIR}/eat-opt.py" "${EAT_DIR}/core/playbooks/deploy-inference-models.yml" "${merged}" \
    "${model_id}" "${K3D_GPU_CONSOLE_DIR:-/k3d-gpu-console}" --cache "${EAT_HF_CACHE:-/hf-cache}" \
    || echo "model optimizer failed: EAT's values as merged"
xeon_values_file_path="${merged}"

INVENTORY_PATH="${brownfield_deployment_host_file}"
model_name_list="${model_id}"
deploy_inference_llm_models_playbook
