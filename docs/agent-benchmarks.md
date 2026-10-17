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
| Task success | The task's deterministic grader passes. Wilson interval at the 0.95 level; arm vs arm is the paired difference on shared task ids (paired bootstrap, 2000 resamples, seed 40). |
| Strict success | Success, and every tool call the model made in the task was valid. |
| Tool-call validity | Valid calls over calls: tool name declared, arguments a JSON object, required keys present with their JSON types (`bench/agent_trace.py validate_calls`). `tool_valid_any` is the share of tool-requiring tasks whose first LLM turn made a valid call. |
| Turn latency | One LLM request, send to end of stream, at the recording proxy. Errors and timeouts count as +inf. p50 and p95 (nearest rank) with a bootstrap CI; TTFT p95 beside it, from the first byte of any delta, tool-call deltas included. |
| Time per task | Wall clock of one agent request, tool and sandbox time included. Median and p95. A task cut by the block budget is censored: reported as a lower bound, never as a failure. |
| Agents per box | Largest N on the ramp 1, 2, 4, 8 where every step up to N keeps pooled p95 turn latency within 10 s, success within 10 points of N=1, errors within 1 in 100, over at least 60 requests. |
| J per successful task | Differential energy: RAPL package energy over the block (plus integrated `nvidia-smi power.draw` for a dGPU arm), minus the mean power of a load-only control window at the same load times the block time, divided by the successful tasks. Failed work stays in the numerator. |

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
2. The recording proxy in the cluster, between the agents and LiteLLM:

   ```bash
   k3d-gpu bench agent manifest --upstream http://litellm.<eat-ns>:4000 --namespace agentbench \
     | kubectl apply -f -
   kubectl -n agentbench logs -f deploy/agentbench-proxy > run/proxy.jsonl &
   ```

   Point the blueprint's `OPENAI_BASE_URL` at `http://agentbench-proxy.agentbench:8080/v1`.
3. The BFCL venv (Python 3.11 or 3.12):

   ```bash
   uv venv --python 3.11 ~/.cache/k3d-gpu/bfcl
   uv pip install --python ~/.cache/k3d-gpu/bfcl/bin/python \
     --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
     -r bench/eatflow/bfcl-constraints.txt
   ```

4. The background load: `bench/load/load.py start --level typical --ttl 1500`.

## Running

```bash
k3d-gpu bench agent plan H42          # the pre-registered blocks, order and budgets

# one block: B24 against arm A1 at typical load, graded against the banking MCP end state
DEVUI_AUTH_TOKEN=... k3d-gpu bench agent run --arm A1 --level typical --out run/H42-A1-1 \
  --agent-url http://127.0.0.1:8090 --tasks B24 --proxy-log run/proxy.jsonl \
  --mcp-url http://127.0.0.1:8000/mcp \
  --reset "kubectl -n banking-assistant rollout restart deploy/banking-mcp" \
  --budget-s 600

k3d-gpu bench agent control --arm A1 --level typical --out run/H42-A1-1 --seconds 120
k3d-gpu bench agent apb --arm A1 --level typical --out run/H45-A1 --agent-url http://127.0.0.1:8090 \
  --proxy-log run/proxy.jsonl --step-s 300
k3d-gpu bench agent bfcl --mode server-fc --arm A1 --level typical --out run/H41-A1 \
  --upstream http://127.0.0.1:4000 --python ~/.cache/k3d-gpu/bfcl/bin/python --budget-s 600
k3d-gpu bench agent report run/H42-* --ref B1 --out run/H42-report
```

- A block stops issuing tasks when `--budget-s` runs out; the task in flight is censored.
- Every finished task is appended to `results.jsonl` at once, and its LLM turns (the proxy
  records with the same last user message, sent inside the task's window) to
  `turns.jsonl` beside it. Rerunning the same command
  skips the tasks already measured for that arm, load level and N, so a crash costs only
  the task in flight. BFCL resumes the same way from its result files.
- `--max-requests N` on the proxy is a hard cap on LLM requests (the remote arm in H48).
- `report` writes `report.json` and `report.md`: success with Wilson intervals, strict
  success, validity, turn and task latency, censored lower bounds, J per success,
  paired differences against `--ref`, BFCL accuracy per category beside the board-weighted
  expectation, agents per box, and the load-matching verdict per level.

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
