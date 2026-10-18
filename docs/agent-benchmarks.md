# Agent benchmarks

`k3d-gpu bench agent` measures agent serving the way an agent uses it: real tool-calling
flows, graded for correctness, timed per LLM turn, on a box that is busy. It is the
harness for the pre-registered hypotheses H40-H48 in
[EXPERIMENTS.md](https://github.com/88plug/k3d-gpu/blob/main/EXPERIMENTS.md#h40-h48-pre-registration-agent-benchmark-2026-10-17).
All of them are PENDING: this page describes the method, and no result is quoted here.

The code lives in [`bench/eatflow/`](https://github.com/88plug/k3d-gpu/tree/main/bench/eatflow)
(Python 3 standard library only) and runs from a repo checkout.

## What it measures

| Metric | Definition |
|---|---|
| Task success | The task's deterministic grader passes. Wilson interval at the 0.95 level, always shown beside the completed-id count (`k/n`). Arm vs arm uses only the task ids both arms completed: each arm's success on those ids, and the paired difference (paired bootstrap, 2000 resamples, seed 40). |
| Strict success | Success, and every tool call the model made in the task was valid. |
| Tool-call validity | Valid calls over calls: tool name declared, arguments a JSON object, required keys present with their JSON types (`bench/agent_trace.py validate_calls`). `tool_valid_any` is the share of tool-requiring tasks whose first LLM turn made a valid call. |
| Turn latency | One LLM request, send to end of stream, at the recording proxy. Errors and timeouts count as +inf. p50 and p95 (nearest rank) with a bootstrap CI; TTFT p95 beside it, from the first byte of any delta, tool-call deltas included. |
| Time per task | Wall clock of one agent request, tool and sandbox time included. Median and p95. A task cut by the block budget is censored: reported as a lower bound, never as a failure. Arm vs arm: the paired time ratio, exp(median of per-id log ratios) over the shared completed ids, with a paired bootstrap CI; a task only one arm completed never enters it. |
| Agents per box | Largest N on the ramp 1, 2, 4, 8 where every step up to N keeps pooled p95 turn latency within 10 s, success within 10 points of N=1, errors within 1 in 100, over at least 60 requests. |
| J per successful task | Differential energy: RAPL package energy over the block (plus integrated `nvidia-smi power.draw` for a dGPU arm), minus the mean power of a load-only control window at the same load times the block time, divided by the successful tasks. Failed work stays in the numerator. |

Every agent LLM request carries one pinned sampling set (`sampling` in `agent.lock`): the
recording proxy replaces every sampling key the agent sent, so no engine default applies.
Local arms use `agent_local` (greedy: temperature 0, top_p 1, max_tokens 1024); the remote
arm uses `agent_remote` (top_k 1, max_tokens 256, never temperature 0 or logprobs). Each
proxy record and `facts.json` (`sampling_seen`) show the set that was sent. BFCL keeps its
own sampling (temperature 0.001, the board's setting).

Every block also records the background load the host carried (`bench/load/load.py sample`:
load-CPU busy share, load average, package watts, throttle events, swap), so two arms are
compared only when their load matched (`load.py matched`, exit 3 = INCONCLUSIVE).

## Workloads

| Set | Source | What |
|---|---|---|
| B24 | Intel's [banking-assistant](https://github.com/intel/enterprise-ai-blueprints/tree/v1.0.0/usecases/banking-assistant) blueprint, v1.0.0 | 3 demo users x 8 templates: balances, one balance, recent transactions, loan due date and remaining, lookup by email, a transfer, a loan payment, an overdraft that must be refused. Expected values come from the blueprint's seed data. Mutating tasks are graded by reading the MCP server's end state. |
| B15-RO | B24 | The 15 tasks that call no write tool; the closed-loop set for agents per box. |
| C12 | Intel's [coding-agent-maf](https://github.com/intel/enterprise-ai-blueprints/tree/v1.0.0/usecases/coding-agent-maf) blueprint | 12 tasks with one exact answer each, run in an agent-sandbox pod; three need `install_package`. |
| S100 | [BFCL v4](https://github.com/ShishirPatil/gorilla/tree/f7cf7359b7ac615a0b294831c5ba2bc95ee4a000/berkeley-function-call-leaderboard), `bfcl-eval==2025.12.17` | 100 fixed ids, stratified over nine single-turn categories, in a fixed seeded run order (`bench/eatflow/s100.json`). |

The README example prompts of both blueprints are in the task sets verbatim.
`k3d-gpu bench agent tasks B24` prints a set.

BFCL runs in one of two modes:

- `board`: the handler behind the leaderboard row for this model. BFCL formats the
  prompt itself and calls `/v1/completions`, so the server's tool parser is not used.
  This mode checks that the harness reproduces the board (H40).
- `server-fc`: BFCL's own chat-completions handler with `tools`, registered at launch
  as `k3d-gpu/server-fc`, so the serving engine parses the tool calls. Engines are
  compared in this mode (H41).

## Pins

Everything external is pinned in
[`bench/eatflow/agent.lock`](https://github.com/88plug/k3d-gpu/blob/main/bench/eatflow/agent.lock):
serving images by digest, weights by revision or sha256, the blueprints by commit
(`758d484`, with the sha256 of the files the graders depend on), BFCL by version and
wheel sha256, and the leaderboard CSV by its sha256 on the read date. The BFCL venv's
full package set is frozen in `bench/eatflow/bfcl-constraints.txt`.

## Setup

1. A cluster with EAT and the blueprints deployed (`integrations/eat/`), each arm's
   server deployed at replicas 0, and every arm behind one LiteLLM alias
   `Qwen/Qwen3-4B-Instruct-2507`, so the agent code is the same for every arm.
   Deploy the banking agent with `ENABLE_MEMORY=false`. The blueprint's default
   (`ENABLE_MEMORY` true, one shared Redis index, user `default_user`) re-injects earlier
   prompts into later tasks and arms. The harness checks it: a task request that carries
   more than one user message aborts the block, and `facts.json` records `memory`.
   Expose the banking MCP server on a stable address (a Service NodePort or an in-cluster
   URL), not a `kubectl port-forward`: a port-forward binds one pod and dies when `--reset`
   replaces it.
2. The recording proxy in the cluster, between the agents and LiteLLM:

   ```bash
   k3d-gpu bench agent manifest --upstream http://litellm.<eat-ns>:4000 --namespace agentbench \
     --sampling agent_local | kubectl apply -f -
   kubectl -n agentbench logs -f deploy/agentbench-proxy > run/proxy.jsonl &
   ```

   Point the blueprint's `OPENAI_BASE_URL` at `http://agentbench-proxy.agentbench:8080/v1`.
3. The BFCL venv (Python 3.11 or 3.12):

   ```bash
   uv venv --python 3.11 ~/.cache/k3d-gpu/bfcl
   uv pip install --python ~/.cache/k3d-gpu/bfcl/bin/python --require-hashes \
     --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
     -r bench/eatflow/bfcl-constraints.txt
   ```

   Every package in the file carries its sha256, so `--require-hashes` checks each wheel.

4. The background load: `bench/load/load.py start --level typical --ttl 1500`.

## Running

```bash
k3d-gpu bench agent plan H42          # the pre-registered blocks, order and budgets

# one block: B24 against arm A1 at typical load, graded against the banking MCP end state
DEVUI_AUTH_TOKEN=... k3d-gpu bench agent run --arm A1 --level typical --out run/H42-A1-1 \
  --agent-url http://127.0.0.1:8090 --tasks B24 --proxy-log run/proxy.jsonl \
  --mcp-url http://127.0.0.1:30800/mcp \
  --reset "kubectl -n banking-assistant rollout restart deploy/banking-mcp" \
  --budget-s 600

k3d-gpu bench agent control --arm A1 --level typical --out run/H46-A1-control --seconds 120
k3d-gpu bench agent apb --arm A1 --level typical --out run/H45-A1 --agent-url http://127.0.0.1:8090 \
  --proxy-log run/proxy.jsonl --step-s 300
k3d-gpu bench agent bfcl --mode server-fc --arm A1 --level typical --out run/H41-A1 \
  --upstream http://127.0.0.1:4000 --python ~/.cache/k3d-gpu/bfcl/bin/python --budget-s 600
k3d-gpu bench agent report run/H42-* --ref B1 --out run/H42-report
```

- Before the window opens, each block sends one unscored warm-up prompt (not in any task
  set) so first-inference compile and cache fill stay outside it; `--no-warmup` skips it.
- A block stops issuing tasks when `--budget-s` runs out; the task in flight is censored.
- After a mutating task, or any task whose end state reads back off the seed (a refused
  overdraft that went through), `--reset` runs and must exit 0 within 300 s, the MCP
  server must read back the seed, and the warm-up prompt must answer again. Any failure
  aborts the block: `blocks.jsonl` records `aborted`, and the command exits 1.
- A task's turns are read from the proxy log once its final turn is there (polled up to
  `--settle-s`, 30 s), so a lagging `kubectl logs -f` copy does not drop them.
- In `--cycle` and `apb` mode each sent prompt starts with a nonce (`[ref apb-2-17] `), so
  prefix and prompt caches see unique user turns, as with real traffic.
- `control` writes its load windows to `control-load.jsonl`; they never enter load matching.
- Every finished task is appended to `results.jsonl` at once, and its LLM turns (the proxy
  records with the same last user message, sent inside the task's window) to
  `turns.jsonl` beside it. Rerunning the same command
  skips the tasks already measured for that arm, load level and N, so a crash costs only
  the task in flight. BFCL resumes the same way from its result files.
- `--max-requests N` on the proxy is a hard cap: every forwarded POST takes a slot,
  whatever its path or body, and later ones get HTTP 429.
- The remote arm (H48) reads its key only from a file: `proxy --key-file FILE` (variable
  `--key-var`, default `OPENAI_API_KEY`) refuses a file with any group or other permission
  bit or owned by another user, and refuses to start without `--max-requests`. The key is
  sent upstream only, never logged or written to `facts.json`:

  ```bash
  chmod 600 farmgpu.env
  k3d-gpu bench agent proxy --upstream https://gaudi.farmgpu.net --key-file farmgpu.env \
    --max-requests 20 --sampling agent_remote --log run/H48/proxy.jsonl --port 18080
  ```
- `report` writes `report.json` and `report.md`: success with Wilson intervals, strict
  success, validity, turn and task latency, censored lower bounds, J per success,
  paired differences against `--ref`, BFCL accuracy per category beside the board-weighted
  expectation, agents per box, and the load-matching verdict per level.

## Live runs on one box

`bench/eatflow/live.sh ARM OUT` runs one arm inside one hold of a shared lock (at most 20
min): a fresh k3d-gpu cluster `k3dgpu-a-<arm>` for the k3d-gpu and EAT-stock servers (or a
host container for the Ollama and llama.cpp vendor stacks), then on the host EAT's LiteLLM
(the version EAT pins), the recording proxy, and the banking agent and MCP sidecar from the
pinned blueprint commit with `ENABLE_MEMORY=false`. It runs B24 under background load, a
120 s load-only control window, and tears everything down. `R` runs the remote arm through
one capped proxy. Root-only RAPL counters are read through `K3D_GPU_RAPL_EXEC` (a
read-only container with the powercap paths unmasked) when sudo is not available.

`k3d-gpu bench agent regrade RUN` applies the current graders to a recorded block (the
first rows stay in `results.raw.jsonl`); `bench/eatflow/live_table.py RUNS` prints the
per-arm table (completed, success, turn p95, J per task, load matched).

## How the harness itself was checked

Without a cluster, on 2026-10-17:

- `python3 bench/eatflow/test_agentbench.py`: stub servers for the LLM, the agent and
  the MCP server; grader mutations; the TTFT of a reply made only of tool-call deltas;
  the request cap; censoring; resume; the agents-per-box gate; energy wrap-around; report.
- BFCL S100 in both modes with the pinned wheel through the proxy against a stub endpoint:
  all 100 ids written and scored, and a stub that never calls a tool scores exactly the 15
  irrelevance ids.
- Intel's banking agent (DevUI and the FastMCP sidecar at the pinned commit) against a
  scripted stub LLM through the proxy: entity discovery, the Responses call, task
  attribution, grading, and a mutating transfer read back from the MCP server.

## Limits

- The harness never submits anything anywhere: no leaderboard, no vendor portal.
- External numbers from other vendors or suites are context only, with their URL and
  read date, and are never turned into a ratio against these runs.
- A claim in the README or these docs needs a SUPPORTED ledger row; until H40-H48 run,
  there is none.
