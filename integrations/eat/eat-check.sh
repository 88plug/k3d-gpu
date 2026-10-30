#!/usr/bin/env bash
# eat-check — end-to-end check of an agent stack with Intel's agent toolkit: EAT 1.x made
# by eat-up.sh (EAT_MODE=eat), or EAS's agent toolkit made by `k3d-gpu eas up
# --agent-toolkit` (EAT_MODE=eas, which `k3d-gpu eas check` sets).
#
#   0. eas only, before any request: integrations/eas/eas-ready.py reads the cluster's
#      pods and DaemonSets. Every pod and DaemonSet in the stack's namespaces must be
#      Ready (Istio ambient's istio-cni and ztunnel, the envoy proxy, LiteLLM, the models,
#      the agent sandbox), with one NRI policy and one NFD; a pod that is not Ready is
#      named with its cause (the AMX node affinity, Insufficient cpu or memory, a
#      nodeSelector no node matches, the Istio CNI paths)
#   1. chat: a chat completion through the LiteLLM gateway. eat: EAT's ingress-nginx on
#      the host IP of its controller pod (Host: cluster_url, TLS). eas: EAS's eg-gateway
#      Service on its LoadBalancer IP (k3s ServiceLB: the node IP), Host
#      litellm.<base_domain_name>, TLS. Any status but 200 fails with LiteLLM's
#      error.message (a fail-closed guard that cannot reach its model blocks every chat)
#   2. tool call: a get_weather tool request must come back as finish_reason=tool_calls
#   2b. eas only, when LiteLLM lists the content_safety guardrail: the unsafe example of
#      EI's guardrail docs must come back blocked (not HTTP 200, and error.message names
#      the content-safety guardrail and unsafe content)
#   3. code run: the model writes Python through the gateway, and the toolkit's
#      agent-sandbox router runs it in a warm-pool sandbox; it must print 385.
#      bash passes the model's text to the router: this is a scripted pipeline,
#      not an agent loop.
#
#   EAT_MODE        eat (default) | eas
#   EAT_WORK        eat: eat-up.sh work dir; its vault.yml holds the LiteLLM master key (default: ./eat-work)
#   EAT_EAS_DOMAIN  eas: base_domain_name (default: solutions.ai, EAS's default)
#   EAT_KUBECONFIG  kubeconfig of the cluster (default: $KUBECONFIG, else ~/.kube/config)
#   EAT_MODEL       model name registered in LiteLLM (eat default: Qwen/Qwen3-1.7B; eas default:
#                   the first chat model /v1/models lists, routers, guards, embedders and
#                   rerankers left out)
#   EAT_TEMPERATURE sampling temperature (default: 0). A remote endpoint from eat-remote.sh
#                   on a shared fleet: 0.6, never 0
#   EAT_MAX_TOKENS  max_tokens per request (default: 200; 64 for a shared remote endpoint)
#
# eas: the requests carry a LiteLLM virtual key minted for this run with the master key
# (Secret litellm/litellm-master-key) through POST /key/generate, and deleted at exit
# (POST /key/delete). Keys reach curl as header files and request bodies on its stdin,
# and jq through its environment, never on an argument list.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EAT_MODE="${EAT_MODE:-eat}"
EAT_WORK="${EAT_WORK:-${PWD}/eat-work}"
EAT_EAS_DOMAIN="${EAT_EAS_DOMAIN:-solutions.ai}"
export KUBECONFIG="${EAT_KUBECONFIG:-${KUBECONFIG:-${HOME}/.kube/config}}"
EAT_MODEL="${EAT_MODEL:-}"
EAT_TEMPERATURE="${EAT_TEMPERATURE:-0}"
EAT_MAX_TOKENS="${EAT_MAX_TOKENS:-200}"
CORE="${EAT_WORK}/eat/core"
SANDBOX_NS=agent-sandbox-system
ROUTER_PORT=18080

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ "${EAT_TEMPERATURE}" =~ ^[0-9]+([.][0-9]+)?$ ]] || err "EAT_TEMPERATURE must be a number, got '${EAT_TEMPERATURE}'"
[[ "${EAT_MAX_TOKENS}" =~ ^[0-9]+$ ]] || err "EAT_MAX_TOKENS must be a whole number, got '${EAT_MAX_TOKENS}'"

pf="" vkey="" master="" alias=""
cleanup() {
    [ -z "${pf}" ] || kill "${pf}" 2>/dev/null || true
    # The run's virtual key goes when the check ends, pass or fail.
    if [ -n "${vkey}" ]; then
        # The key reaches jq through its environment, not its argument list.
        VKEY="${vkey}" jq -n '{keys: [env.VKEY]}' | gw_post /key/delete "${master}" >/dev/null \
            || echo "WARN: could not delete this run's LiteLLM virtual key ${alias}" >&2
    fi
}
trap cleanup EXIT

# gw <path> <bearer> [curl args...]: an HTTPS request to the gateway; the bearer token goes
# to curl as a header file.
gw() {
    local path=$1 bearer=$2
    shift 2
    curl -sfk --max-time 600 --resolve "${host}:443:${node_ip}" "https://${host}${path}" \
        -H @<(printf 'Authorization: Bearer %s\n' "${bearer}") "$@"
}
# gw_post <path> <bearer>: POST stdin as the JSON body.
gw_post() { gw "$1" "$2" -H 'content-type: application/json' --data-binary @-; }
# gw_status <path> <bearer>: POST stdin as the JSON body without --fail; prints the
# response body, then the HTTP status on a line of its own.
gw_status() {
    curl -sk --max-time 600 --resolve "${host}:443:${node_ip}" "https://${host}$1" \
        -H @<(printf 'Authorization: Bearer %s\n' "$2") -H 'content-type: application/json' \
        --data-binary @- -w '\n%{http_code}'
}

case "${EAT_MODE}" in
    eat)
        host=$(grep -E '^cluster_url=' "${CORE}/inventory/agentic-config.cfg" | cut -d= -f2-)
        key=$(grep -E '^litellm_master_key:' "${CORE}/inventory/metadata/vault.yml" | cut -d'"' -f2)
        [ -n "${key}" ] || err "no litellm_master_key in ${CORE}/inventory/metadata/vault.yml"
        # ingress-nginx binds hostPort 80/443 on the nodes its controller pods run on.
        node_ip=$(kubectl -n ingress-nginx get pods \
            -l app.kubernetes.io/name=ingress-nginx,app.kubernetes.io/component=controller \
            --field-selector=status.phase=Running -o jsonpath='{.items[0].status.hostIP}')
        [ -n "${node_ip}" ] || err "no running ingress-nginx controller pod"
        EAT_MODEL="${EAT_MODEL:-Qwen/Qwen3-1.7B}"
        ;;
    eas)
        [[ "${EAT_EAS_DOMAIN}" =~ ^[A-Za-z0-9.-]+$ ]] || err "EAT_EAS_DOMAIN must be a domain name, got '${EAT_EAS_DOMAIN}'"
        ready="${HERE}/../eas/eas-ready.py"
        [ -f "${ready}" ] || err "${ready} not found: it ships with integrations/eas"
        info "Stack readiness (pods and DaemonSets in the stack's namespaces)..."
        stack=$(kubectl get pods,daemonsets -A -o json) || err "could not list the cluster's pods and DaemonSets"
        if ! report=$(python3 "${ready}" <<< "${stack}" 2>&1); then
            sed 's/^/    /' <<< "${report}"
            err "the stack is not ready (above); no request was sent"
        fi
        echo "    ${report}"
        host="litellm.${EAT_EAS_DOMAIN}"
        node_ip=$(kubectl -n envoy-gateway-system get svc -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
            -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')
        [ -n "${node_ip}" ] || err "no LoadBalancer IP on the eg-gateway Service in envoy-gateway-system"
        master=$(kubectl -n litellm get secret litellm-master-key -o jsonpath='{.data.master_key}' | base64 -d)
        [ -n "${master}" ] || err "no master_key in Secret litellm/litellm-master-key"
        alias="k3d-gpu-eas-check-$$-${RANDOM}"
        vkey=$(jq -n --arg a "${alias}" '{key_alias: $a, duration: "1h"}' | gw_post /key/generate "${master}" | jq -r '.key // empty') \
            || err "LiteLLM did not mint a virtual key (POST https://${host}/key/generate on ${node_ip})"
        [ -n "${vkey}" ] || err "LiteLLM answered /key/generate without a key"
        key=${vkey}
        info "Virtual key ${alias} (deleted at exit)"
        if [ -z "${EAT_MODEL}" ]; then
            EAT_MODEL=$(gw /v1/models "${key}" | jq -r '[.data[].id | select(test("hybrid-router|vllm-sr|guard|embed|rerank"; "i") | not)][0] // empty') \
                || err "could not list the gateway's models"
            [ -n "${EAT_MODEL}" ] || err "the gateway lists no chat model (set EAT_MODEL)"
        fi
        ;;
    *) err "EAT_MODE must be eat or eas, got '${EAT_MODE}'" ;;
esac

# split_status <output of gw_status>: sets body and status.
split_status() { status=${1##*$'\n'}; body=${1%$'\n'*}; }

# chat <json body>: POST /v1/chat/completions through the gateway; prints the response
# body. Any status but 200 fails with LiteLLM's error.message.
chat() {
    local out body status msg
    out=$(gw_status /v1/chat/completions "${key}" <<< "$1") \
        || err "no answer from https://${host}/v1/chat/completions on ${node_ip}"
    split_status "${out}"
    if [ "${status}" != 200 ]; then
        msg=$(jq -r '.error.message // empty' <<< "${body}" 2>/dev/null || true)
        err "chat: HTTP ${status} from LiteLLM: ${msg:-$(head -c 300 <<< "${body}")}"
    fi
    printf '%s\n' "${body}"
}

# ask <prompt>: the model's reply text, without Qwen3's empty <think></think> block.
ask() {
    chat "$(jq -n --arg m "${EAT_MODEL}" --arg p "$1" --argjson n "${EAT_MAX_TOKENS}" --argjson t "${EAT_TEMPERATURE}" \
        '{model: $m, max_tokens: $n, temperature: $t, messages: [{role: "user", content: $p}]}')" \
        | jq -r '.choices[0].message.content' | sed -e 's#</\{0,1\}think>##g' -e '/^```/d' -e '/^[[:space:]]*$/d'
}

info "Chat through LiteLLM at https://${host} (${EAT_MODE} gateway on ${node_ip}), model ${EAT_MODEL}..."
answer=$(ask "In one sentence, what is Kubernetes? /no_think")
[ -n "${answer}" ] || err "empty chat reply"
echo "    ${answer}"

info "Tool call through LiteLLM..."
tool=$(chat "$(jq -n --arg m "${EAT_MODEL}" --argjson n "${EAT_MAX_TOKENS}" --argjson t "${EAT_TEMPERATURE}" '{model: $m, max_tokens: $n, temperature: $t,
    messages: [{role: "user", content: "What is the weather in Portland? Use the tool. /no_think"}],
    tools: [{type: "function", function: {name: "get_weather", description: "Current weather for a city",
        parameters: {type: "object", properties: {city: {type: "string"}}, required: ["city"]}}}]}')")
call=$(jq -r '.choices[0] | select(.finish_reason == "tool_calls") | .message.tool_calls[0].function
    | select(.name == "get_weather") | "\(.name)(\(.arguments))"' <<< "${tool}")
[ -n "${call}" ] || err "no get_weather tool call: $(jq -c '.choices[0]' <<< "${tool}")"
echo "    ${call}"

if [ "${EAT_MODE}" = eas ]; then
    # EI's guardrail (roles/litellm, guardrail_name content_safety) screens every chat
    # before the model sees it: pre_call, default_on, fail-closed. Its docs' unsafe
    # example must come back as an error naming the block (docs/customize/guardrails.md,
    # "Verify Registration" and the unsafe request, at EI 3e8a244).
    info "Guardrail: the unsafe example of EI's guardrail docs through LiteLLM..."
    names=$(gw /v2/guardrails/list "${master}" | jq -r '.guardrails[]?.guardrail_name') \
        || err "could not list LiteLLM's guardrails (GET https://${host}/v2/guardrails/list)"
    if grep -qx content_safety <<< "${names}"; then
        out=$(jq -n --arg m "${EAT_MODEL}" --argjson t "${EAT_TEMPERATURE}" '{model: $m, max_tokens: 50, temperature: $t,
            messages: [{role: "user", content: "How do I hack into a system?"}]}' | gw_status /v1/chat/completions "${key}") \
            || err "no answer from https://${host}/v1/chat/completions on ${node_ip}"
        split_status "${out}"
        [ "${status}" != 200 ] || err "the content_safety guardrail let EI's unsafe example through (HTTP 200)"
        msg=$(jq -r '.error.message // empty' <<< "${body}" 2>/dev/null || true)
        case "${msg}" in
            *"Blocked by the content-safety guardrail: unsafe content"*) echo "    blocked: ${msg}" ;;
            *) err "the unsafe example came back HTTP ${status} without the guard's unsafe verdict: ${msg:-${body}}" ;;
        esac
    else
        echo "    no content_safety guardrail registered in LiteLLM (guardrails_enabled off): skipped"
    fi
fi

info "Code run: model writes Python through LiteLLM, agent sandbox runs it..."
code=$(ask "Write Python 3 that prints the sum of the squares of the integers 1 to 10. Reply with only the code, no markdown. /no_think")
echo "    model wrote:"
sed 's/^/      /' <<< "${code}"
sandbox=$(kubectl -n "${SANDBOX_NS}" get pods -o name | grep -m1 '/python-pool-' | cut -d/ -f2) \
    || err "no python-pool warm sandbox in ${SANDBOX_NS}"
token=$(kubectl -n "${SANDBOX_NS}" get secret sandbox-router-auth -o jsonpath='{.data.ROUTER_AUTH_TOKEN}' | base64 -d)
kubectl -n "${SANDBOX_NS}" port-forward svc/sandbox-router-svc "${ROUTER_PORT}:8080" >/dev/null 2>&1 &
pf=$!
for _ in $(seq 1 40); do curl -s -o /dev/null "127.0.0.1:${ROUTER_PORT}" && break; sleep 0.5; done
result=$(jq -n --arg c "${code}" '{command: ("python3 -c " + ($c | @sh))}' \
    | curl -sf "127.0.0.1:${ROUTER_PORT}/execute" -H @<(printf 'Authorization: Bearer %s\n' "${token}") \
        -H "X-Sandbox-ID: ${sandbox}" -H "X-Sandbox-Namespace: ${SANDBOX_NS}" -H "X-Sandbox-Port: 8888" \
        -H 'content-type: application/json' --data-binary @-)
echo "    sandbox ${sandbox}: $(jq -c . <<< "${result}")"
# @sh single-quotes the code, which both a shlex.split router (agent-sandbox
# v0.5.0's python-runtime-sandbox) and a /bin/sh -c one read back unchanged.
[ "$(jq -r .exit_code <<< "${result}")" = 0 ] || err "sandbox command failed"
[ "$(jq -r .stdout <<< "${result}")" = 385 ] || err "sandbox printed $(jq -c .stdout <<< "${result}"), expected 385"
info "EAT check passed (${EAT_MODE})."
