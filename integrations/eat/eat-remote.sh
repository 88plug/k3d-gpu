#!/usr/bin/env bash
# eat-remote — serve EAT's LiteLLM gateway from a remote OpenAI-compatible endpoint
# instead of (or beside) EAT's own vLLM. Run after eat-up.sh, on the host.
#
#   eat-remote.sh [--remote URL] [--model M] [--api-key-file FILE]
#
# The endpoint resolves like every k3d-gpu command (console/remote.py): --remote,
# else OPENAI_BASE_URL / OPENAI_API_BASE / OPENAI_HOST; the key from
# --api-key-file, else OPENAI_API_KEY; the model from --model, else OPENAI_MODEL,
# else the only one served.
#
#   1. Secret k3d-gpu-llm (OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL) goes into
#      namespace genai-gateway, on stdin and server-side.
#   2. The gateway Deployment gets K3D_GPU_REMOTE_OPENAI_API_KEY as a secretKeyRef to
#      it (kubectl set env --from=secret). EAT's genai-gateway chart renders its env as
#      literals only (templates/deployment.yaml), so Helm values would hold the key in
#      clear text; a later `helm upgrade` drops this env, rerun this script then.
#   3. POST /model/new registers the model with api_key "os.environ/K3D_GPU_REMOTE_OPENAI_API_KEY",
#      which LiteLLM resolves from the pod's environment, so the key is in neither the
#      request nor LiteLLM's database. EAT's register playbook is not used: it fixes
#      api_key to "none". Skipped when GET /models already lists the model.
#
# Then check it with a sampled, short request (a shared endpoint may not serve greedy):
#   EAT_MODEL=<model> EAT_TEMPERATURE=0.6 EAT_MAX_TOKENS=64 integrations/eat/eat-check.sh
#
#   EAT_WORK        eat-up.sh work dir; its vault.yml holds the LiteLLM master key (default: ./eat-work)
#   EAT_KUBECONFIG  kubeconfig of the cluster (default: $KUBECONFIG, else ~/.kube/config)
#   K3D_GPU_CONSOLE directory with remote.py (default: the repo's console/, else /usr/share/k3d-gpu/console)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EAT_WORK="${EAT_WORK:-${PWD}/eat-work}"
export KUBECONFIG="${EAT_KUBECONFIG:-${KUBECONFIG:-${HOME}/.kube/config}}"
GW_NS=genai-gateway
GW_DEPLOY=genai-gateway-deployment
GW_SVC=genai-gateway-service
GW_PORT="${EAT_GATEWAY_PORT:-4000}"
KEY_ENV=K3D_GPU_REMOTE_OPENAI_API_KEY
LOCAL_PORT="${EAT_REMOTE_LOCAL_PORT:-14000}"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

console=""
for d in "${K3D_GPU_CONSOLE:-}" "${HERE}/../../console" /usr/share/k3d-gpu/console; do
    if [ -n "${d}" ] && [ -f "${d}/remote.py" ]; then console=${d}; break; fi
done
[ -n "${console}" ] || err "console/remote.py not found (set K3D_GPU_CONSOLE)"
remote_py() { python3 "${console}/remote.py" "$@"; }

args=()
while [ $# -gt 0 ]; do
    case "$1" in
        --remote|--model|--api-key-file) [ $# -ge 2 ] || err "$1 needs a value"; args+=("$1" "$2"); shift 2 ;;
        *) err "unknown option: $1 (use --remote URL, --model M, --api-key-file FILE)" ;;
    esac
done

core="${EAT_WORK}/eat/core"
master=$(grep -E '^litellm_master_key:' "${core}/inventory/metadata/vault.yml" 2>/dev/null | cut -d'"' -f2) || master=""
[ -n "${master}" ] || err "no litellm_master_key in ${core}/inventory/metadata/vault.yml (run eat-up.sh first, or set EAT_WORK)"
kubectl -n "${GW_NS}" get "deployment/${GW_DEPLOY}" >/dev/null || err "no ${GW_NS}/${GW_DEPLOY}: is EAT's gateway deployed?"

fields=$(remote_py resolve --probe ${args[@]+"${args[@]}"}) || err "remote endpoint not usable (above)"
IFS=$'\t' read -r base model origin <<< "${fields}"
info "remote endpoint ${base} (from ${origin}), model ${model}"
keyargs=()
for ((i = 0; i < ${#args[@]}; i += 2)); do [ "${args[i]}" != --api-key-file ] || keyargs=(--api-key-file "${args[i + 1]}"); done

info "Secret ${GW_NS}/k3d-gpu-llm..."
remote_py secret --namespace "${GW_NS}" --remote "${base}" --model "${model}" ${keyargs[@]+"${keyargs[@]}"} \
    | kubectl apply --server-side --force-conflicts --field-manager=k3d-gpu -f - >/dev/null

info "Gateway env ${KEY_ENV} from that Secret (secretKeyRef)..."
kubectl -n "${GW_NS}" set env "deployment/${GW_DEPLOY}" --from=secret/k3d-gpu-llm --keys=OPENAI_API_KEY \
    --prefix=K3D_GPU_REMOTE_ >/dev/null
kubectl -n "${GW_NS}" rollout status "deployment/${GW_DEPLOY}" --timeout=300s

kubectl -n "${GW_NS}" port-forward "svc/${GW_SVC}" "${LOCAL_PORT}:${GW_PORT}" >/dev/null 2>&1 &
pf=$!
trap 'kill "${pf}" 2>/dev/null || true' EXIT
gw="http://127.0.0.1:${LOCAL_PORT}"
# gw_curl ARGS...: curl to the gateway with the master key header read from stdin.
gw_curl() { curl -sS --noproxy '*' -H @- "$@" <<< "Authorization: Bearer ${master}"; }
listed=""
for _ in $(seq 1 30); do
    listed=$(gw_curl -f "${gw}/models?return_wildcard_routes=false" 2>/dev/null) && break
    sleep 1
done
[ -n "${listed}" ] || err "the gateway did not answer GET /models through the port-forward"
if jq -e --arg m "${model}" '.data[]? | select(.id == $m)' <<< "${listed}" >/dev/null; then
    info "LiteLLM already serves ${model}; nothing to register"
    exit 0
fi
body=$(jq -cn --arg m "${model}" --arg b "${base}" --arg k "os.environ/${KEY_ENV}" \
    '{model_name: $m, litellm_params: {model: ("openai/" + $m), api_base: $b, api_key: $k}, model_info: {mode: "chat"}}')
info "Registering ${model} in LiteLLM (POST /model/new)..."
gw_curl -f -X POST "${gw}/model/new" -H 'content-type: application/json' -d "${body}" >/dev/null \
    || err "LiteLLM refused /model/new"
info "Done. Check: EAT_MODEL=${model} EAT_TEMPERATURE=0.6 EAT_MAX_TOKENS=64 ${HERE}/eat-check.sh"
