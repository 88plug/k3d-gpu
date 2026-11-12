#!/usr/bin/env bash
# eas-up — install Intel's agent toolkit (EAT 2.0, the `agent-toolkit` layer) on a
# k3d-gpu cluster through Intel's own installer: es_auto_installer.sh from AI for
# Enterprise Solutions (EAS) tag v1.2.0-rc.1, bring-your-own-cluster path, run from a
# throwaway Ubuntu bastion container.
#
#   eas-up.sh               preflight, then clone, pin, patch, init, render and install
#   eas-up.sh preflight     the preflight only: writes $EAS_WORK/facts.json, installs nothing
#   eas-up.sh render CHECKOUT FACTS
#                           write k3d-gpu's settings into CHECKOUT/env/<env>/ (after init);
#                           needs only yq and the two paths, no cluster and no docker
#
#   EAS_WORK        work dir for the EAS checkout, the facts file and logs (default: ./eas-work)
#   EAS_ENV         the installer's env name, env/<name>/ (default: local)
#   EAS_KUBECONFIG  kubeconfig of the target cluster (default: $KUBECONFIG, else ~/.kube/config)
# Overrides of what the preflight detects (see eas-facts.py): EAS_REQUIRE_AMX,
# EAS_ISTIO_PLATFORM, EAS_CLUSTER_CIDR, EAS_MODEL with EAS_MODEL_ID, EAS_MODEL_CPU.
#
# Every refusal happens before anything is installed: a foreign agent-sandbox
# controller, an unknown CNI layout, a checkout or ext repo not at its pinned commit,
# an upstream key that moved, or a model that does not fit. Then check the install:
#   integrations/eas/eas-check.sh
#
# Internal steps, also run by the tests: pin-revs, check-head, check-ext and patch
# (each takes CHECKOUT), and bastion (inside the container).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EAS_REPO="https://github.com/intel/enterprise-ai-solutions"
EAS_TAG="v1.2.0-rc.1"
EAS_COMMIT="5c27fb39a95eecec41aa4e29a1188984e9c726f3"
# The two repos configs/repos/repos.agent-toolkit.yaml lists at that tag, by branch
# name (rev: release-1.1 / release-2.0.0), with these commits only in comments.
# es_auto_installer.sh accepts a commit as rev (_checkout_rev: checkout --detach).
EI_LAYER="inference"
EI_URL="https://github.com/intel/enterprise-inference"
EI_DEST="enterprise.ai-inference"
EI_COMMIT="170eb3abb1956de0787727f21e356aef2b13ff24"
EAT_LAYER="agent-toolkit"
EAT_URL="https://github.com/intel/enterprise-agent-toolkit"
EAT_DEST="enterprise.agent-toolkit"
EAT_COMMIT="036b66234801e9bd950afd90830821859780d668"
MANIFEST="configs/repos/repos.agent-toolkit.yaml"
ENVOY_PATCH="${HERE}/patches/0006-envoy-control-plane-affinity-rc1.patch"
ENVOY_TASK="roles/envoy_gateway/tasks/install.yaml"
ISTIO_VALUES="istio-values.k3d-gpu.yaml"
BASTION_EAS="/home/ubuntu/eas"
BASTION_FACTS="/eas-facts.json"
FACTS_SCHEMA="k3d-gpu.eas-facts/v1"

EAS_WORK="${EAS_WORK:-${PWD}/eas-work}"
EAS_ENV="${EAS_ENV:-local}"
EAS_KUBECONFIG="${EAS_KUBECONFIG:-${KUBECONFIG:-${HOME}/.kube/config}}"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

require_yq() {
    command -v yq >/dev/null 2>&1 || err "yq not found (mikefarah yq v4; the bastion image has it)"
    yq --version 2>&1 | grep -q 'mikefarah' || err "the yq on PATH is not mikefarah yq v4"
}

# ---- pins ---------------------------------------------------------------------

# check-head CHECKOUT: the EAS checkout is the pinned tag's commit, and no tracked
# file differs from it except the two this script edits.
check_head() {
    local dir=$1 head changed
    head=$(git -C "${dir}" rev-parse HEAD 2>/dev/null) || err "${dir} is not a git checkout"
    [ "${head}" = "${EAS_COMMIT}" ] \
        || err "${dir} is at ${head}, not EAS ${EAS_TAG} (${EAS_COMMIT}); delete it and re-run"
    changed=$(git -C "${dir}" status --porcelain -uno | awk '{print $2}' \
        | grep -vxF -e "${MANIFEST}" -e "${ENVOY_TASK}" || true)
    [ -z "${changed}" ] || err "${dir} has edits eas-up.sh did not make: ${changed//$'\n'/ }; delete it and re-run"
}

# check-ext CHECKOUT: after init, each layer repo under ext/ is at its pinned commit.
check_ext() {
    local dir=$1 dest want head
    while read -r dest want; do
        head=$(git -C "${dir}/ext/${dest}" rev-parse HEAD 2>/dev/null) \
            || err "ext/${dest} is missing in ${dir}: did init run?"
        [ "${head}" = "${want}" ] || err "ext/${dest} is at ${head}, not the pinned ${want}"
        info "ext/${dest} at ${want}"
    done <<< "${EI_DEST} ${EI_COMMIT}
${EAT_DEST} ${EAT_COMMIT}"
}

# pin-revs CHECKOUT: rewrite the layer manifest's branch revs to the pinned commits,
# after checking it lists exactly the two repos these pins are for.
pin_revs() {
    local f="$1/${MANIFEST}" layer url dest want old
    require_yq
    [ -f "${f}" ] || err "${MANIFEST} not found in $1"
    [ "$(yq '.repos | length' "${f}")" = 2 ] || err "${MANIFEST} lists $(yq '.repos | length' "${f}") repos, not the 2 pinned here"
    while read -r layer url dest want; do
        L="${layer}" U="${url}" D="${dest}" yq -e \
            '.repos[] | select(.layer == strenv(L) and .url == strenv(U) and .dest == strenv(D))' "${f}" >/dev/null 2>&1 \
            || err "${MANIFEST} has no repo layer=${layer} url=${url} dest=${dest}"
        old=$(L="${layer}" yq '.repos[] | select(.layer == strenv(L)) | .rev' "${f}")
        if [ "${old}" = "${want}" ]; then
            info "${MANIFEST}: ${layer} rev ${want} (unchanged)"
        else
            L="${layer}" W="${want}" yq -i '(.repos[] | select(.layer == strenv(L)) | .rev) = strenv(W)' "${f}"
            info "${MANIFEST}: ${layer} rev ${old} -> ${want}"
        fi
    done <<< "${EI_LAYER} ${EI_URL} ${EI_DEST} ${EI_COMMIT}
${EAT_LAYER} ${EAT_URL} ${EAT_DEST} ${EAT_COMMIT}"
}

# patch CHECKOUT: the rebased envoy affinity patch, applied once.
apply_patch() {
    local dir=$1
    if git -C "${dir}" apply --reverse --check "${ENVOY_PATCH}" 2>/dev/null; then
        info "${ENVOY_TASK}: $(basename "${ENVOY_PATCH}") already applied"
    elif git -C "${dir}" apply --check "${ENVOY_PATCH}" 2>/dev/null; then
        git -C "${dir}" apply "${ENVOY_PATCH}"
        info "${ENVOY_TASK}: applied $(basename "${ENVOY_PATCH}") (control-plane affinity Exists, nodeSelector null)"
    else
        err "$(basename "${ENVOY_PATCH}") does not apply to ${dir}/${ENVOY_TASK}; the EnvoyProxy task moved upstream"
    fi
}

# ---- render -------------------------------------------------------------------

# fact <expr>: a value from the facts file.
fact() { yq -p json -o yaml "$1" "${FACTS}"; }

# need <file> <yq boolean expr> <what>: refuse unless the upstream reader still has the key.
need() {
    local f=$1 expr=$2 what=$3
    [ -f "${CO}/${f}" ] || err "upstream file ${f} not found in ${CO}"
    [ "$(yq "${expr}" "${CO}/${f}" 2>/dev/null)" = true ] \
        || err "upstream key ${what} not found in ${f} (Intel renamed or moved it; eas-up.sh is written for EAS ${EAS_TAG})"
}

# set_key <file> <key> <str|bool|int> <value>: one top-level key, printed against its old value.
set_key() {
    local f=$1 k=$2 t=$3 v=$4 old new
    old=$(K="${k}" yq -o=json -I=0 '.[strenv(K)]' "${f}")
    case "${t}" in
        str) K="${k}" V="${v}" yq -i '.[strenv(K)] = strenv(V)' "${f}" ;;
        *)   K="${k}" V="${v}" yq -i '.[strenv(K)] = env(V)' "${f}" ;;
    esac
    new=$(K="${k}" yq -o=json -I=0 '.[strenv(K)]' "${f}")
    if [ "${old}" = "${new}" ]; then echo "    ${k}: ${new} (unchanged)"; else echo "    ${k}: ${old} -> ${new}"; fi
}

render() {
    CO=$(cd "$1" && pwd) || err "checkout $1 not found"
    FACTS=$2
    require_yq
    [ -f "${FACTS}" ] || err "facts file ${FACTS} not found (eas-up.sh preflight writes it)"
    [ "$(fact .schema)" = "${FACTS_SCHEMA}" ] || err "${FACTS} is not a ${FACTS_SCHEMA} facts file"
    local env model guard ei="ext/${EI_DEST}" eat="ext/${EAT_DEST}" e gc cfg models ivalues
    env=$(fact .env)
    model=$(fact .model.name)
    guard=$(fact .guard.name)
    e="env/${env}"
    gc="${CO}/${e}/global_config.yaml"
    cfg="${CO}/${e}/config.agent-toolkit.yaml"
    models="${CO}/${e}/models.yaml"
    ivalues="${CO}/${e}/${ISTIO_VALUES}"
    for f in "${gc}" "${cfg}" "${models}"; do
        [ -f "${f}" ] || err "${f#"${CO}/"} not found: run 'es_auto_installer.sh init agent-toolkit --env ${env}' first"
    done

    # Every key this writes, where the code that reads it defines it.
    need configs/defaults/global_config.yaml 'has("metallb_enabled")' metallb_enabled
    need configs/defaults/global_config.yaml 'has("storage_backend")' storage_backend
    need configs/defaults/global_config.yaml 'has("kata_containers_enabled")' kata_containers_enabled
    need configs/defaults/global_config.yaml 'has("kubernetes_cpu_policy")' kubernetes_cpu_policy
    need configs/defaults/global_config.yaml 'has("istio_enabled")' istio_enabled
    need configs/defaults/global_config.yaml 'has("auth_provider")' auth_provider
    need "${MANIFEST}" '.global_config.auth_provider == "litellm"' "global_config.auth_provider: litellm"
    need roles/kubernetes/defaults/main.yaml 'has("existing_kubernetes")' existing_kubernetes
    need roles/kubernetes/defaults/main.yaml 'has("kubernetes_cluster_cidr")' kubernetes_cluster_cidr
    need roles/istio/defaults/main.yaml 'has("istio_helm_default_values_file")' istio_helm_default_values_file
    need roles/istio/defaults/main.yaml \
        '.istio_helm_default_values_file == "{{ istio_helm_chart_directory }}/values.yaml" and .istio_helm_chart_directory == "{{ playbook_dir }}/../charts/istio"' \
        "istio_helm_default_values_file at charts/istio/values.yaml"
    need charts/istio/values.yaml 'has("cni") and has("ztunnel")' "cni and ztunnel (Istio ambient)"
    need "${ei}/roles/nfd/defaults/main.yaml" 'has("nfd_enabled")' nfd_enabled
    need "${ei}/model_manager/models.yaml" '.defaults | has("require_amx")' defaults.require_amx
    need "${ei}/model_manager/models.yaml" '.runtimes.vllm.categories.llm.defaults.cpu == 8' \
        "runtimes.vllm.categories.llm.defaults.cpu: 8"
    M="${model}" I="$(fact .model.model_id)" need "${ei}/model_manager/models.yaml" \
        '[.models[] | select(.name == strenv(M) and .model_id == strenv(I) and has("cpu") and has("memory") and (.env | has("VLLM_CPU_KVCACHE_SPACE")))] | length == 1' \
        "models[${model}] with model_id $(fact .model.model_id), cpu, memory and env.VLLM_CPU_KVCACHE_SPACE"
    M="${guard}" C="$(fact .guard.cpu)" G="$(fact .guard.memory)" need "${ei}/model_manager/models.yaml" \
        '[.models[] | select(.name == strenv(M) and (.cpu | tostring) == strenv(C) and .memory == strenv(G))] | length == 1' \
        "models[${guard}] with cpu $(fact .guard.cpu) and memory $(fact .guard.memory)"
    need "${eat}/config.yaml" 'has("llm_services_autodeploy_models")' llm_services_autodeploy_models
    need "${eat}/roles/agent_sandbox/defaults/main.yaml" 'has("agent_sandbox_router_image")' agent_sandbox_router_image
    need "${eat}/roles/agent_sandbox/defaults/main.yaml" '.agent_sandbox_version == "v0.5.0"' "agent_sandbox_version: v0.5.0"
    M="${model}" need "${e}/models.yaml" \
        '(.defaults | has("require_amx")) and ([.models[] | select(.name == strenv(M) and (.env | has("VLLM_CPU_KVCACHE_SPACE")))] | length == 1)' \
        "defaults.require_amx and models[${model}] in the seeded ${e}/models.yaml"
    [ "$(yq '.auth_provider' "${gc}")" = litellm ] \
        || err "${e}/global_config.yaml has auth_provider $(yq '.auth_provider' "${gc}"), not the litellm that 'init agent-toolkit' seeds"

    info "${e}/global_config.yaml:"
    set_key "${gc}" existing_kubernetes str "$(fact .existing_kubernetes)"
    set_key "${gc}" metallb_enabled bool false
    set_key "${gc}" storage_backend str local-path
    set_key "${gc}" kubernetes_cluster_cidr str "$(fact .cluster_cidr)"
    set_key "${gc}" kata_containers_enabled bool false
    set_key "${gc}" kubernetes_cpu_policy str best-effort
    set_key "${gc}" nfd_enabled bool false
    set_key "${gc}" agent_sandbox_router_image str "$(fact .agent_sandbox_router_image)"
    set_key "${gc}" istio_enabled bool true
    set_key "${gc}" istio_helm_default_values_file str "${ivalues}"
    echo "    auth_provider: \"$(yq '.auth_provider' "${gc}")\" (kept as init seeded it)"

    info "${e}/${ISTIO_VALUES}: charts/istio/values.yaml with global.platform $(fact .istio_platform) ($(fact .istio_platform_from))"
    P="$(fact .istio_platform)" yq '.global.platform = strenv(P)' "${CO}/charts/istio/values.yaml" > "${ivalues}.tmp"
    mv "${ivalues}.tmp" "${ivalues}"

    info "${e}/config.agent-toolkit.yaml:"
    local old
    old=$(yq -o=json -I=0 '.llm_services_autodeploy_models' "${cfg}")
    M="${model}" yq -i '.llm_services_autodeploy_models = [strenv(M)]' "${cfg}"
    echo "    llm_services_autodeploy_models: ${old} -> $(yq -o=json -I=0 '.llm_services_autodeploy_models' "${cfg}")$(
        [ "${old}" != "$(yq -o=json -I=0 '.llm_services_autodeploy_models' "${cfg}")" ] || echo ' (unchanged)')"

    info "${e}/models.yaml (catalog: ${ei}/model_manager/models.yaml):"
    local cat="${CO}/${ei}/model_manager/models.yaml" sel='.models[] | select(.name == strenv(M))' k v src
    V="$(fact .require_amx)" yq -i '.defaults.require_amx = env(V)' "${models}"
    echo "    defaults.require_amx: $(yq '.defaults.require_amx' "${cat}") -> $(yq '.defaults.require_amx' "${models}") ($(fact .require_amx_from))"
    while read -r k v src; do
        case "${k}" in
            cpu) M="${model}" V="${v}" yq -i "(${sel}).cpu = env(V)" "${models}" ;;
            *)   M="${model}" V="${v}" yq -i "(${sel}).${k} = strenv(V)" "${models}" ;;
        esac
        echo "    ${model}.${k}: $(M="${model}" yq "${sel} | .${k}" "${cat}") -> $(M="${model}" yq "${sel} | .${k}" "${models}") (${src})"
    done <<< "env.VLLM_CPU_KVCACHE_SPACE $(fact .model.kv_cache_gib) k3d-gpu fit
cpu $(fact .model.cpu) fits node $(fact .node.name) beside ${guard} and the requests already there
memory $(fact .model.memory) k3d-gpu fit"
    info "kubernetes_cpu_policy best-effort: EAS calls nri-balloons the only supported production value (configs/defaults/global_config.yaml:91-96); here the node's NRI balloons policy is k3d-gpu's, so EAS installs none (nri_cpu_balloons is skipped)"
}

# ---- run ----------------------------------------------------------------------

# Inside the bastion: init (clones ext/ at the pinned commits), verify, render, install.
bastion() {
    FACTS="${BASTION_FACTS}"
    [ "$(fact .env)" = "${EAS_ENV}" ] || err "the facts are for env $(fact .env), this run is for ${EAS_ENV}"
    cd "${BASTION_EAS}"
    info "es_auto_installer.sh init agent-toolkit --env ${EAS_ENV}"
    ./es_auto_installer.sh init agent-toolkit --env "${EAS_ENV}"
    check_head "${BASTION_EAS}"
    check_ext "${BASTION_EAS}"
    render "${BASTION_EAS}" "${BASTION_FACTS}"
    info "es_auto_installer.sh install agent-toolkit --env ${EAS_ENV}"
    ./es_auto_installer.sh install agent-toolkit --env "${EAS_ENV}"
}

preflight() {
    mkdir -p "${EAS_WORK}"
    python3 "${HERE}/eas-facts.py" --kubeconfig "${EAS_KUBECONFIG}" --images "${HERE}/images.yaml" \
        --env "${EAS_ENV}" --out "${EAS_WORK}/facts.json" || exit 1
    info "facts: ${EAS_WORK}/facts.json"
}

up() {
    local co image log
    preflight
    command -v docker >/dev/null 2>&1 || err "docker not found"
    # docker -v needs absolute paths.
    EAS_WORK=$(cd "${EAS_WORK}" && pwd)
    EAS_KUBECONFIG="$(cd "$(dirname "${EAS_KUBECONFIG}")" && pwd)/$(basename "${EAS_KUBECONFIG}")"
    co="${EAS_WORK}/eas"
    if [ ! -d "${co}/.git" ]; then
        info "Cloning ${EAS_REPO} ${EAS_TAG} into ${co}..."
        git clone -q --depth 1 --branch "${EAS_TAG}" "${EAS_REPO}" "${co}"
    fi
    check_head "${co}"
    image="k3d-gpu-eas-bastion:$(sha256sum "${HERE}/bastion.Dockerfile" | cut -c1-12)"
    info "Building bastion image ${image}..."
    docker build -q -t "${image}" -f "${HERE}/bastion.Dockerfile" "${HERE}" >/dev/null
    # pin-revs needs yq, which the host may lack: run it in the bastion.
    docker run --rm -v "${co}:${BASTION_EAS}" -v "${HERE}:/k3d-gpu-eas:ro" "${image}" \
        bash /k3d-gpu-eas/eas-up.sh pin-revs "${BASTION_EAS}"
    apply_patch "${co}"
    check_head "${co}"
    log="${EAS_WORK}/eas-up-$(date -u +%Y%m%dT%H%M%SZ).log"
    info "Running init, render and install agent-toolkit in the bastion (log: ${log})..."
    docker run --rm --network host \
        -v "${co}:${BASTION_EAS}" \
        -v "${EAS_KUBECONFIG}:/kube/config:ro" \
        -v "${HERE}:/k3d-gpu-eas:ro" \
        -v "${EAS_WORK}/facts.json:${BASTION_FACTS}:ro" \
        -e KUBECONFIG=/kube/config \
        -e PIP_CONSTRAINT=/k3d-gpu-eas/constraints.txt \
        -e EAS_ENV="${EAS_ENV}" \
        -e ES_LOG_LEVEL="${ES_LOG_LEVEL:-info}" \
        "${image}" bash /k3d-gpu-eas/eas-up.sh bastion 2>&1 | tee "${log}"
    info "Installed. Check it: EAS_KUBECONFIG=${EAS_KUBECONFIG} ${HERE}/eas-check.sh"
}

case "${1:-up}" in
    up)         up ;;
    preflight)  preflight ;;
    render)     [ $# -eq 3 ] || err "usage: eas-up.sh render CHECKOUT FACTS"; render "$2" "$3" ;;
    pin-revs)   [ $# -eq 2 ] || err "usage: eas-up.sh pin-revs CHECKOUT"; pin_revs "$2" ;;
    check-head) [ $# -eq 2 ] || err "usage: eas-up.sh check-head CHECKOUT"; check_head "$2" ;;
    check-ext)  [ $# -eq 2 ] || err "usage: eas-up.sh check-ext CHECKOUT"; check_ext "$2" ;;
    patch)      [ $# -eq 2 ] || err "usage: eas-up.sh patch CHECKOUT"; apply_patch "$2" ;;
    bastion)    [ -d "${BASTION_EAS}/.git" ] || err "bastion runs inside the eas-up.sh container only"; bastion ;;
    *)          err "unknown command: $1 (up, preflight, render, pin-revs, check-head, check-ext, patch)" ;;
esac
