# Agent framework quickstarts

Minimal tool-calling agents for five frameworks, all pointed at the k3d-gpu agent
endpoint: OpenVINO Model Server (OVMS) serving `OpenVINO/Qwen3-4B-int4-ov` as
`Qwen3-4B-int4-ov`, with OVMS's `hermes3` tool parser, on the OpenAI API.

| Directory | Framework | Pinned packages |
|---|---|---|
| [`openai-agents/`](openai-agents/) | OpenAI Agents SDK, tool served over MCP (stdio) | `openai-agents==0.24.0`, `openai==3.31.0`, `mcp==2.3.0` |
| [`langgraph/`](langgraph/) | LangGraph `StateGraph` + `ToolNode` | `langgraph==1.2.15`, `langchain-openai==1.7.2`, `langchain-core==1.6.11` |
| [`crewai/`](crewai/) | CrewAI agent + task | `crewai==1.15.28` |
| [`llamaindex/`](llamaindex/) | LlamaIndex `FunctionAgent` | `llama-index-core==0.14.25`, `llama-index-llms-openai-like==0.8.1` |
| [`litellm-gateway/`](litellm-gateway/) | Intel Enterprise Agent Toolkit GenAI gateway (LiteLLM) | `litellm==1.83.14` (the gateway image's version) |

Each example asks "What is 6 times 7?", the model calls a `multiply` tool, and the
agent prints the answer. Each directory has its own `requirements.txt`; install
them in separate virtualenvs (CrewAI and LiteLLM pin `openai` 2.x, the Agents SDK
needs 3.x).

## Run against the cluster

```bash
k3d-gpu agent up                                     # OVMS on the best Intel engine
kubectl -n k3d-gpu-agent port-forward svc/ovms 8000:8000 &

cd examples/agents/langgraph
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python agent.py
```

| Variable | Default | |
|---|---|---|
| `K3D_GPU_AGENT_BASE_URL` | `http://localhost:8000/v3` | In a pod: `http://ovms.k3d-gpu-agent.svc.cluster.local:8000/v3` |
| `K3D_GPU_AGENT_MODEL` | `Qwen3-4B-int4-ov` | The `--model_name` OVMS serves (`K3D_GPU_AGENT_MODEL_NAME`) |

## What each example has to get right

- **The `/v3` path.** OVMS serves the OpenAI API under `/v3`, not `/v1`. Every
  client takes a base URL ending in `/v3` and appends `/chat/completions`.
- **Chat Completions, not Responses.** The Agents SDK defaults to the Responses
  API; the example wraps the client in `OpenAIChatCompletionsModel`.
- **Tool calls.** OVMS parses Qwen3's tool-call output into standard
  `tool_calls`, so each framework's native function calling works unchanged.
  LlamaIndex's `OpenAILike` must be told `is_function_calling_model=True`.
- **Qwen3 thinking.** Qwen3 reasons before answering by default. Every example
  sends `chat_template_kwargs: {"enable_thinking": false}` through the
  framework's extra-body hook to keep tool loops short. Drop it to let it think.
- **No cloud calls.** The Agents SDK's trace export and CrewAI's telemetry and
  trace upload are switched off.

## Enterprise Agent Toolkit gateway

The toolkit's GenAI gateway is LiteLLM (`docker.litellm.ai/berriai/litellm:v1.83.14-stable.patch.2`
in `core/helm-charts/genai-gateway` at toolkit tag `release-1.0.3-hotfix`),
Service `genai-gateway-service` port 4000 in namespace `genai-gateway`, with
models stored in its database and added through `/model/new`.
[`litellm-gateway/model.json`](litellm-gateway/model.json) is that request body:
model `openai/Qwen3-4B-int4-ov`, `api_base` the in-cluster OVMS Service at `/v3`.

```bash
cd examples/agents/litellm-gateway
python agent.py                       # proves the entry through litellm.Router first
kubectl -n genai-gateway port-forward svc/genai-gateway-service 4000:4000 &
LITELLM_MASTER_KEY=... ./register-model.sh
```

Clients then call the gateway (`http://localhost:4000/v1`, bearer = a gateway
key) with `model="Qwen3-4B-int4-ov"`. The toolkit's charts schedule on nodes
labelled `ei-infra-eligible=true` (or control-plane nodes).

## Tests

`stub_openai.py` is a stdlib stand-in for the endpoint: `/v3` only, the one
model name, a `multiply` tool call, then an answer from the tool's result,
streaming or not. `check.py <example>` runs an example against it and checks
the requests it sent. `tests/agent_examples_test.sh` does that for every
example in a pinned `python:3.13.16-slim-trixie` container, which is where the
packages get installed. It takes example names, so a CI matrix can run one
example per job.

```bash
tests/agent_examples_test.sh            # all
tests/agent_examples_test.sh crewai     # one
```
