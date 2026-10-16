#!/usr/bin/env bash
# Registers model.json with the Enterprise Agent Toolkit's GenAI gateway (LiteLLM)
# through its /model/new API, as the toolkit's own register-model-genai-gateway.yml
# does: skip if /models already lists it, else POST the entry.
#
#   kubectl -n genai-gateway port-forward svc/genai-gateway-service 4000:4000
#   LITELLM_MASTER_KEY=... ./register-model.sh
#
# GATEWAY defaults to that port-forward. The entry's api_base is the in-cluster
# k3d-gpu agent Service (http://ovms.k3d-gpu-agent.svc.cluster.local:8000/v3).

set -euo pipefail

GATEWAY="${GATEWAY:-http://localhost:4000}"
: "${LITELLM_MASTER_KEY:?set LITELLM_MASTER_KEY to the gateway master key}"
ENTRY="$(dirname "$0")/model.json"
NAME=$(sed -n 's/^  "model_name": "\(.*\)",$/\1/p' "${ENTRY}")

if curl -fsS -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
        "${GATEWAY}/models?return_wildcard_routes=false" | grep -q "\"id\":\"${NAME}\""; then
    echo "${NAME} already registered"
    exit 0
fi
curl -fsS -X POST "${GATEWAY}/model/new" \
    -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary "@${ENTRY}"
echo
echo "${NAME} registered; call it at ${GATEWAY}/v1/chat/completions"
