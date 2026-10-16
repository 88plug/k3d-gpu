#!/usr/bin/env bash
# Runs inside the bastion container (bastion.Dockerfile). Drives EAT's own
# brownfield path, brownfield_deployment() in core/lib/brownfield/, which no
# EAT menu reaches in release-1.0.3-hotfix. Its interactive prompts get fixed
# answers; every component toggle comes from core/inventory/agentic-config.cfg.
#
#   brownfield.sh               deploy the stack selected in agentic-config.cfg
#   brownfield.sh model <id>    deploy one model from EAT's vLLM playbook and register it in LiteLLM
#
#   EAT_DIR          EAT checkout (default: /home/ubuntu/eat)
#   EAT_KUBECONFIG   kubeconfig of the target cluster (default: /kube/config)
#   EAT_VLLM_VALUES  values merged over EAT's vllm/xeon-values.yaml (default: /vllm-values.yaml)
#   EAT_VLLM_KV_GIB  VLLM_CPU_KVCACHE_SPACE for every catalog model, in GiB (default: 6)
#
# Variables set below without a local use are read by EAT's sourced functions.
# shellcheck disable=SC1091,SC2034,SC2153,SC2154

set -euo pipefail

EAT_DIR="${EAT_DIR:-/home/ubuntu/eat}"
EAT_KUBECONFIG="${EAT_KUBECONFIG:-/kube/config}"
EAT_VLLM_VALUES="${EAT_VLLM_VALUES:-/vllm-values.yaml}"
EAT_VLLM_KV_GIB="${EAT_VLLM_KV_GIB:-6}"
CERT_DIR="${HOME}/certs"

mode="${1:-stack}"
model_id="${2:-}"
case "${mode}" in
    stack) ;;
    model) [ -n "${model_id}" ] || { echo "usage: brownfield.sh [stack | model <id>]" >&2; exit 2; } ;;
    *) echo "usage: brownfield.sh [stack | model <id>]" >&2; exit 2 ;;
esac

cluster_url=$(grep -E '^cluster_url=' "${EAT_DIR}/core/inventory/agentic-config.cfg" | cut -d= -f2-)

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
    # Answers, in prompt order:
    #   5    platform menu: EAT detects the platform before its bastion playbook
    #        installs kubectl, so a fresh bastion always asks (and k3s's
    #        v1.34.1+k3s1 would fail its bare vX.Y.Z "vanilla" match anyway); 5 = Vanilla
    #   1    brownfield menu: Deploy Inference Stack
    #   yes  "continue with Brownfield Deployment setup?"
    printf '5\n1\nyes\n' | brownfield_deployment
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
# EAT's own xeon-values.yaml with the k3d-gpu values merged on top, and every
# model's KV cache set to EAT_VLLM_KV_GIB, instead of editing the checkout.
merged="${HOME}/xeon-values.k3d-gpu.yaml"
python3 - "${HOMEDIR}/helm-charts/${xeon_values_file_path#"${REMOTEDIR}"/}" "${EAT_VLLM_VALUES}" "${EAT_VLLM_KV_GIB}" "${merged}" <<'EOF'
import sys, yaml
base_path, overlay_path, kv_gib, out = sys.argv[1:]
base = yaml.safe_load(open(base_path))
overlay = yaml.safe_load(open(overlay_path)) or {}
def merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = v
merge(base, overlay)
for cfg in [*(base.get("modelConfigs") or {}).values(), base.get("defaultModelConfigs")]:
    cmv = (cfg or {}).get("configMapValues") or {}
    if "VLLM_CPU_KVCACHE_SPACE" in cmv:
        cmv["VLLM_CPU_KVCACHE_SPACE"] = kv_gib
yaml.safe_dump(base, open(out, "w"), sort_keys=False)
EOF
echo "vLLM values: ${merged} (EAT xeon-values.yaml + ${EAT_VLLM_VALUES}, KV cache ${EAT_VLLM_KV_GIB} GiB)"
xeon_values_file_path="${merged}"

INVENTORY_PATH="${brownfield_deployment_host_file}"
model_name_list="${model_id}"
deploy_inference_llm_models_playbook
