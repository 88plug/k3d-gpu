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
| [`litellm-gateway/`](litellm-gateway/) | Intel Enterprise Agent Toolkit GenAI gateway (LiteLLM) | `litellm==1.83.14` (see [the gateway section](#enterprise-agent-toolkit-gateway)) |

Each example asks "What is 6 times 7?", the model calls a `multiply` tool, and the
agent prints the answer. Each directory has its own `requirements.txt` (the direct
dependencies) and `constraints.txt` (every package, transitive ones included,
as resolved on Python 3.13 / Linux). Install them in separate virtualenvs: the
CrewAI, LlamaIndex and LiteLLM sets resolve `openai` 2.x, the Agents SDK and
LangGraph sets 3.x.

## Run against the cluster

`k3d-gpu agent up` runs OVMS in namespace `k3d-gpu-agent`, Service `ovms`
port 8000 (see `cmd_agent` in [`scripts/k3d-gpu`](../../scripts/k3d-gpu)).

```bash
k3d-gpu agent up                                     # OVMS on the best Intel engine
kubectl -n k3d-gpu-agent port-forward svc/ovms 8000:8000 &

cd examples/agents/langgraph
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -c constraints.txt
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
[`integrations/eat/`](../../integrations/eat/) brings the toolkit up on a k3d-gpu node.
[`litellm-gateway/model.json`](litellm-gateway/model.json) is that request body:
model `openai/Qwen3-4B-int4-ov`, `api_base` the in-cluster OVMS Service at `/v3`.

```bash
cd examples/agents/litellm-gateway
python agent.py                       # proves the entry through litellm.Router first
kubectl -n genai-gateway port-forward svc/genai-gateway-service 4000:4000 &
LITELLM_MASTER_KEY=... ./register-model.sh
```

`register-model.sh` needs `curl` and `python3`.

**Version.** The toolkit's image is built from LiteLLM tag
`v1.83.14-stable.patch.2`, three commits after `v1.83.14-stable`: two Docker
base-image pins and one MCP server-creation auth fix (BerriAI/litellm#27190).
Both tags carry package version 1.83.14 and PyPI has only that one, built from
`v1.83.14-stable`, so the example pins `litellm==1.83.14`. The three commits
touch Dockerfiles and the proxy's MCP management endpoint, none of the SDK
`Router` code the example runs.

**Advisories.** OSV lists four advisories against `litellm` 1.83.14, all in the
proxy server (the gateway), not the SDK `Router` this example runs:

| Advisory | Issue | Fixed in |
|---|---|---|
| [GHSA-3cv6-jpf6-8222](https://osv.dev/vulnerability/GHSA-3cv6-jpf6-8222) | Authenticated SSRF and provider-credential exfiltration via request-body routing parameters | 1.88.6 |
| [GHSA-4jcj-7x88-m979](https://osv.dev/vulnerability/GHSA-4jcj-7x88-m979) | MCP proxy improper authentication | 1.84.0 |
| [GHSA-4xpc-pv4p-pm3w](https://osv.dev/vulnerability/GHSA-4xpc-pv4p-pm3w) | Authentication bypass via Host header injection | 1.84.0 |
| [GHSA-7488-6r32-c95q](https://osv.dev/vulnerability/GHSA-7488-6r32-c95q) | MCP authentication bypass via OAuth2 passthrough fallback | 1.84.0 |

The image's extra MCP auth commit is not listed as a fix for any of them, so
treat all four as open on the toolkit's gateway: keep it off untrusted networks
and its master key private, or run a fixed LiteLLM image.

Clients then call the gateway (`http://localhost:4000/v1`, bearer = a gateway
key) with `model="Qwen3-4B-int4-ov"`. The toolkit's charts schedule on nodes
labelled `ei-infra-eligible=true` (or control-plane nodes).

## Tests

`stub_openai.py` is a stdlib stand-in for the endpoint: `/v3` only, the one
model name, a `multiply` tool call, then an answer from the tool's result,
streaming or not. `check.py <example>` runs an example against it and checks
the requests it sent (for `litellm-gateway`, also that `model.json` targets the
in-cluster agent Service, which `agent.py` swaps for the stub).
`tests/agent_examples_test.sh` does that for every example in a pinned `python:3.13.16-slim-trixie` container, which is where the
packages get installed from `requirements.txt` under `constraints.txt`; the
installed set must equal `constraints.txt`. It takes example names, so a CI matrix can run one
example per job.

```bash
tests/agent_examples_test.sh            # all
tests/agent_examples_test.sh crewai     # one
tests/register_model_test.sh            # register-model.sh against a stub gateway
```
