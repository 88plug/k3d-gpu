"""Routes the gateway's model entry (model.json) to the k3d-gpu endpoint with LiteLLM.

model.json is the body register-model.sh posts to the Enterprise Agent Toolkit
gateway's /model/new. This script loads the same entry into litellm.Router (the
router the gateway runs) and drives a tool-calling loop through it, so the entry
is proven before it is registered.

    kubectl -n k3d-gpu-agent port-forward svc/ovms 8000:8000
    python agent.py
"""

import json
import os

import litellm
from litellm import Router
from litellm.types.router import Deployment

BASE_URL = os.environ.get("K3D_GPU_AGENT_BASE_URL", "http://localhost:8000/v3")
HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "model.json")) as f:
    entry = json.load(f)
Deployment(**entry)  # the schema /model/new validates against
# In the cluster the gateway reaches ovms.k3d-gpu-agent.svc; from here, the port-forward.
entry["litellm_params"]["api_base"] = BASE_URL
model = os.environ.get("K3D_GPU_AGENT_MODEL", entry["model_name"])
entry["model_name"] = model
entry["litellm_params"]["model"] = f"openai/{model}"

# The toolkit's gateway sets this in litellm_settings; extra_body must survive it.
litellm.drop_params = True
router = Router(model_list=[entry])

TOOLS = [{
    "type": "function",
    "function": {
        "name": "multiply",
        "description": "Multiply two integers.",
        "parameters": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
}]


def multiply(a: int, b: int) -> int:
    return a * b


def main():
    messages = [
        {"role": "system", "content": "Use the multiply tool for arithmetic. Answer in one sentence."},
        {"role": "user", "content": "What is 6 times 7?"},
    ]
    for _ in range(3):
        msg = router.completion(model=model, messages=messages, tools=TOOLS).choices[0].message
        if not msg.tool_calls:
            print(msg.content)
            return
        messages.append(msg.model_dump(exclude_none=True))
        for call in msg.tool_calls:
            result = multiply(**json.loads(call.function.arguments))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": str(result)})
    raise SystemExit("no final answer after 3 rounds")


if __name__ == "__main__":
    main()
