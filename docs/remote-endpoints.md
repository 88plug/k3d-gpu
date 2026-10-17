# Remote OpenAI-compatible endpoints

k3d-gpu can use any OpenAI-compatible endpoint instead of, or beside, a local
model: a hosted API, a team inference fleet, or a vLLM server elsewhere on your
network. With `--remote` there is no accelerator gate, no model download and no
local LLM server. The cluster gets the endpoint as a Kubernetes Secret, and the
chat UI, the agent sandbox and EAT's LiteLLM gateway read it from there.

## Configure the endpoint

The usual OpenAI variables work. The keys below are placeholders.

```bash
export OPENAI_BASE_URL=https://llm.example.com/v1
export OPENAI_API_KEY=sk-your-key-here
k3d-gpu remote check
```

| Field | Taken from, first match wins |
|---|---|
| Base URL | `--remote URL`, then `OPENAI_BASE_URL`, then `OPENAI_API_BASE` (legacy openai-python, aider), then `OPENAI_HOST` (goose; no `/v1`) |
| Key | `--api-key-file FILE`, then `OPENAI_API_KEY` |
| Model | `--model`, then `OPENAI_MODEL`, then the only id `GET /models` lists |

- **Base URL.** A bare host gets `/v1` (`https://llm.example.com` becomes
  `https://llm.example.com/v1`). A trailing `/chat/completions` is dropped with a
  warning. A URL with `user:password@`, a query or a fragment is refused. Plain
  `http://` is refused unless the host is loopback or in-cluster (`*.svc`), so the
  key never crosses a network in clear text.
- **Key.** There is no option that takes the key itself, so it never appears in
  a process list or shell history. `--api-key-file` takes either the bare key or
  an env file with an `OPENAI_API_KEY=` line. The file is parsed, never sourced,
  and must be mode 0600.
- **Model.** If the server lists several models, k3d-gpu stops and prints the
  list; pick one with `--model` or `OPENAI_MODEL`. A model the server routes but
  does not list (an alias) is tried with a warning.
- **Only `--remote` switches a command to the endpoint.** An `OPENAI_*` variable
  in your shell never replaces a local model by itself; `quickstart` only
  mentions it.

## Check it

```bash
k3d-gpu remote check            # GET /models + one 16-token chat (temperature 0.6)
k3d-gpu remote check --json
k3d-gpu remote show             # what resolves, no network
k3d-gpu remote models
```

`check` prints the endpoint and where it came from, the host, the key masked to
`sk-…` plus its last four characters, the auth result with the `GET /models`
latency, the served models, and the chat result. A reasoning model can spend
all 16 tokens thinking: `check` then reports `finish_reason=length` with an
empty reply as a warning, not as a pass. `--max-tokens` raises the limit, up to
64.

Exit codes: 0 reachable and accepted, 2 configuration, 3 key rejected
(401/403), 4 endpoint error or unreachable.

**Requests go to the configured host only.** Redirects are not followed at all,
so the `Authorization` header can never be replayed to a host a 3xx response
names. Fix the URL instead.

## Chat UI in one command

```bash
k3d-gpu quickstart --remote
k3d-gpu quickstart --remote https://llm.example.com --model my-org/my-model
k3d-gpu quickstart --remote --api-key-file ~/.config/llm.env --no-ui
```

1. The endpoint is resolved and checked with one `GET /models`.
2. The cluster is created. A host without a supported accelerator gets a plain
   cluster (`K3D_GPU_VENDORS=none k3d-gpu up`).
3. Secret `k3d-gpu-llm` is written to namespace `k3d-gpu-agent`.
4. Open WebUI starts with `OPENAI_API_BASE_URL` and `OPENAI_API_KEY` taken from
   that Secret through `secretKeyRef`, `DEFAULT_MODELS` set to the model, and
   `ENABLE_PERSISTENT_CONFIG=False`. Without that last setting, a connection
   saved in Open WebUI's data volume would win over the environment on the next
   start, and changing the endpoint would have no effect.
5. The port-forward on 127.0.0.1 and the generated admin account work the same
   as the local path.

## The Secret

`k3d-gpu agent up --remote` writes only the Secret; no model server is
deployed. A local server that is already running is left in place.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: k3d-gpu-llm
  namespace: k3d-gpu-agent
  labels: {app.kubernetes.io/managed-by: k3d-gpu}
  annotations:
    k3d-gpu.io/base-url: "https://llm.example.com/v1"
    k3d-gpu.io/model: "my-org/my-model"
type: Opaque
data:
  OPENAI_BASE_URL: <base64>
  OPENAI_API_KEY: <base64>
  OPENAI_MODEL: <base64>
```

The data keys are the variable names OpenAI SDKs read, so a pod takes all three
with one line:

```yaml
envFrom:
  - secretRef: {name: k3d-gpu-llm}
```

- **How the Secret is written.** It is sent to `kubectl apply --server-side` on
  stdin. A client-side apply would copy the whole object, key included, into
  the `last-applied-configuration` annotation, which `kubectl describe` prints.
- **Why the URL is the endpoint's own.** The Secret holds the endpoint's real
  URL, not an in-cluster alias. A `Service` of type `ExternalName` would make
  clients send the in-cluster name as TLS SNI and `Host`, which a CDN-fronted
  endpoint rejects.
- **Status and removal.** `agent status` reads the Secret and checks the
  endpoint with the Secret's own key. It fails if the sandbox's copy names a
  different endpoint. `agent down` deletes the Secret.

## Sandbox egress pinned to the endpoint

```bash
k3d-gpu sandbox up --remote
k3d-gpu sandbox status
```

The default sandbox profile allows cluster DNS and every public address. With
`--remote`, the SandboxTemplate's egress becomes exactly one rule:

- a `/32` (or `/128` for IPv6) for each address of the endpoint's host;
- on the URL's port (443 by default);
- TCP only;
- no DNS. The pod's `hostAliases` map the hostname to the same addresses, so TLS
  SNI and `Host` stay correct.

The sandbox gets its own copy of the Secret (Secrets are namespace-scoped)
through `envFrom`. An endpoint that resolves to a private address is refused:
the pin is for public endpoints.

A CDN can move a name to new addresses. `sandbox status` resolves the host again
and fails with the new addresses when they no longer match the pin. Run
`sandbox up --remote` again to re-pin. `K3D_GPU_REMOTE_ADDRS=a,b` pins
addresses you choose instead of resolving the host.

## Doctor and MCP

- **Doctor.** `k3d-gpu doctor --remote` adds the endpoint's reachability, one
  `GET /models` with the key, and does not fail on a host without accelerators.
  When `OPENAI_BASE_URL`, `OPENAI_API_BASE` or `OPENAI_HOST` is set, plain
  `doctor` shows the same check as a warning only.
- **MCP.** The `agent_endpoint` tool of `k3d-gpu-mcp` reports remote mode:
  `remote: true`, the base URL, the host, the model, the `envFrom` snippet, and
  `keyPresent`. It reads the Secret's annotations through a template that never
  prints a data value, so the key cannot reach the MCP client.

## Load and benchmarks against someone else's endpoint

Capacity ramps and benchmarks send load. Against a remote endpoint they are
refused unless you say the endpoint is yours, and even then they stay small:

| Command | Without `--i-own-this-endpoint` | With it |
|---|---|---|
| `capacity --remote` (or `--url` to a non-local host) | `--dry-run` only | `--budget 5 --max 4` unless you set them |
| `bench --vs remote` | `--dry-run` only | 1 round, 1 warmup, at most 64 tokens, four prompts, temperature 0.6, no `ignore_eos` |
| `tune --remote` | refused | refused: tune redeploys the local OVMS agent, and a remote endpoint has no setting k3d-gpu can change |

`capacity` sends its requests at temperature 0, so run it only against an
endpoint that is yours to load.

## EAT (Intel's Enterprise Agent Toolkit)

After `integrations/eat/eat-up.sh`:

```bash
integrations/eat/eat-remote.sh --remote https://llm.example.com --model my-org/my-model
EAT_MODEL=my-org/my-model EAT_TEMPERATURE=0.6 EAT_MAX_TOKENS=64 integrations/eat/eat-check.sh
```

`eat-remote.sh` does three things:

1. It writes the Secret into namespace `genai-gateway`.
2. It gives LiteLLM's Deployment the env var `K3D_GPU_REMOTE_OPENAI_API_KEY`
   from that Secret, as a `secretKeyRef`.
3. It registers the model through LiteLLM's `POST /model/new` with
   `api_key: os.environ/K3D_GPU_REMOTE_OPENAI_API_KEY`. The key is in neither
   the request nor LiteLLM's database.

It does not use EAT's own register playbook, which fixes `api_key` to `none`. A
`helm upgrade` of the gateway drops the env var; run `eat-remote.sh` again
after one.

This path is tested against stubs only. It has not run against a live EAT
gateway.

## Variables Open WebUI, SDKs and tools read

| Variable | Read by | Form |
|---|---|---|
| `OPENAI_API_KEY` | openai-python, openai-node, LiteLLM, aider, goose, Open WebUI | bare key |
| `OPENAI_BASE_URL` | openai-python, openai-node, LiteLLM, goose | with `/v1` |
| `OPENAI_API_BASE` | openai-python 0.x, aider, LiteLLM (fallback) | with `/v1` |
| `OPENAI_HOST` | goose | without `/v1` |
| `OPENAI_API_BASE_URL` | Open WebUI | with `/v1` |
