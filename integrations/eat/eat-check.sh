#!/usr/bin/env bash
# eat-check — end-to-end check of an EAT deployment made by eat-up.sh.
#
#   1. chat: a chat completion through EAT's LiteLLM gateway, reached through
#      EAT's ingress-nginx on the host IP of its controller pod (Host: cluster_url, TLS)
#   2. tool call: a get_weather tool request must come back as finish_reason=tool_calls
#   3. code run: the model writes Python through the gateway, and EAT's
#      agent-sandbox router runs it in a warm-pool sandbox; it must print 385.
#      bash passes the model's text to the router: this is a scripted pipeline,
#      not an agent loop.
#
#   EAT_WORK        eat-up.sh work dir; its vault.yml holds the LiteLLM master key (default: ./eat-work)
#   EAT_KUBECONFIG  kubeconfig of the cluster (default: $KUBECONFIG, else ~/.kube/config)
#   EAT_MODEL       model name registered in LiteLLM (default: Qwen/Qwen3-1.7B)

set -euo pipefail

EAT_WORK="${EAT_WORK:-${PWD}/eat-work}"
export KUBECONFIG="${EAT_KUBECONFIG:-${KUBECONFIG:-${HOME}/.kube/config}}"
EAT_MODEL="${EAT_MODEL:-Qwen/Qwen3-1.7B}"
CORE="${EAT_WORK}/eat/core"
SANDBOX_NS=agent-sandbox-system
ROUTER_PORT=18080

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

host=$(grep -E '^cluster_url=' "${CORE}/inventory/agentic-config.cfg" | cut -d= -f2-)
key=$(grep -E '^litellm_master_key:' "${CORE}/inventory/metadata/vault.yml" | cut -d'"' -f2)
[ -n "${key}" ] || err "no litellm_master_key in ${CORE}/inventory/metadata/vault.yml"
# ingress-nginx binds hostPort 80/443 on the nodes its controller pods run on.
node_ip=$(kubectl -n ingress-nginx get pods \
    -l app.kubernetes.io/name=ingress-nginx,app.kubernetes.io/component=controller \
    --field-selector=status.phase=Running -o jsonpath='{.items[0].status.hostIP}')
[ -n "${node_ip}" ] || err "no running ingress-nginx controller pod"

# chat <json body>: POST /v1/chat/completions through the ingress; prints the response body.
chat() {
    curl -sfk --max-time 600 --resolve "${host}:443:${node_ip}" "https://${host}/v1/chat/completions" \
        -H "Authorization: Bearer ${key}" -H 'content-type: application/json' -d "$1"
}

# ask <prompt>: the model's reply text, without Qwen3's empty <think></think> block.
ask() {
    chat "$(jq -n --arg m "${EAT_MODEL}" --arg p "$1" \
        '{model: $m, max_tokens: 200, temperature: 0, messages: [{role: "user", content: $p}]}')" \
        | jq -r '.choices[0].message.content' | sed -e 's#</\{0,1\}think>##g' -e '/^```/d' -e '/^[[:space:]]*$/d'
}

info "Chat through LiteLLM at https://${host} (ingress on ${node_ip})..."
answer=$(ask "In one sentence, what is Kubernetes? /no_think")
[ -n "${answer}" ] || err "empty chat reply"
echo "    ${answer}"

info "Tool call through LiteLLM..."
tool=$(chat "$(jq -n --arg m "${EAT_MODEL}" '{model: $m, max_tokens: 200, temperature: 0,
    messages: [{role: "user", content: "What is the weather in Portland? Use the tool. /no_think"}],
    tools: [{type: "function", function: {name: "get_weather", description: "Current weather for a city",
        parameters: {type: "object", properties: {city: {type: "string"}}, required: ["city"]}}}]}')")
call=$(jq -r '.choices[0] | select(.finish_reason == "tool_calls") | .message.tool_calls[0].function
    | select(.name == "get_weather") | "\(.name)(\(.arguments))"' <<< "${tool}")
[ -n "${call}" ] || err "no get_weather tool call: $(jq -c '.choices[0]' <<< "${tool}")"
echo "    ${call}"

info "Code run: model writes Python through LiteLLM, agent sandbox runs it..."
code=$(ask "Write Python 3 that prints the sum of the squares of the integers 1 to 10. Reply with only the code, no markdown. /no_think")
echo "    model wrote:"
sed 's/^/      /' <<< "${code}"
sandbox=$(kubectl -n "${SANDBOX_NS}" get pods -o name | grep -m1 '/python-pool-' | cut -d/ -f2) \
    || err "no python-pool warm sandbox in ${SANDBOX_NS}"
token=$(kubectl -n "${SANDBOX_NS}" get secret sandbox-router-auth -o jsonpath='{.data.ROUTER_AUTH_TOKEN}' | base64 -d)
kubectl -n "${SANDBOX_NS}" port-forward svc/sandbox-router-svc "${ROUTER_PORT}:8080" >/dev/null 2>&1 &
pf=$!
trap 'kill "${pf}" 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do curl -s -o /dev/null "127.0.0.1:${ROUTER_PORT}" && break; sleep 0.5; done
result=$(curl -sf "127.0.0.1:${ROUTER_PORT}/execute" -H "Authorization: Bearer ${token}" \
    -H "X-Sandbox-ID: ${sandbox}" -H "X-Sandbox-Namespace: ${SANDBOX_NS}" -H "X-Sandbox-Port: 8888" \
    -H 'content-type: application/json' \
    -d "$(jq -n --arg c "${code}" '{command: ("python3 -c " + ($c | @sh))}')")
echo "    sandbox ${sandbox}: $(jq -c . <<< "${result}")"
# @sh single-quotes the code, which both a shlex.split router (agent-sandbox
# v0.5.0's python-runtime-sandbox) and a /bin/sh -c one read back unchanged.
[ "$(jq -r .exit_code <<< "${result}")" = 0 ] || err "sandbox command failed"
[ "$(jq -r .stdout <<< "${result}")" = 385 ] || err "sandbox printed $(jq -c .stdout <<< "${result}"), expected 385"
info "EAT check passed."
