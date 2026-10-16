# Agent-trace benchmark

`bench/agent_trace.py` replays an agent trace against an OpenAI-compatible
`/chat/completions` endpoint (OVMS `/v3`, vLLM `/v1`, ...) and records, per
turn: TTFT, decode tokens/s, prompt/completion/cached tokens, and whether the
reply contains a valid tool call. `bench/agent_trace_gen.py` synthesizes three
trace families. Python stdlib only; tests run against an in-process stub.

```sh
python3 bench/test_agent_trace.py                  # 35 tests, ~20 s, no server
python3 bench/agent_trace.py gen support -o support.json
python3 bench/agent_trace.py run support.json --model MODEL \
    --url http://127.0.0.1:8000/v3/chat/completions --out runs/support-none
python3 bench/agent_trace.py run support.json --model MODEL --cache-bust request \
    --url http://127.0.0.1:8000/v3/chat/completions --out runs/support-req
python3 bench/agent_trace.py compare runs/support-none.summary.json runs/support-req.summary.json
```

## Why

Fixed-prompt benchmarks (one prompt, N copies, fixed in/out lengths) do not
exercise what agent serving does: a long system prompt and tool schema shared
by every session, tool-call JSON that must parse, context that grows each turn,
and sessions that arrive in bursts (an orchestrator fanning out sub-agents).

## Survey (primary sources read 2026-10-16)

| Harness | Measures | Agent shape covered | Gap for this repo |
|---|---|---|---|
| vLLM `benchmarks/multi_turn/benchmark_serving_multi_turn.py` | TTFT, TPOT, latency per request; `--num-clients`, `--max-active-conversations`, `--request-rate` | multi-turn growth; synthetic or ShareGPT | `prefix_num_tokens` is unique per conversation (no shared system prompt); no tools (0 matches for "tool" in the script) |
| SGLang `benchmark/hicache/bench_multiturn.py`, `bench_serving.py --enable-multiturn` | TTFT, latency, per-round `cached_tokens` | multi-turn rounds, shared-prefix (loogle) | no tool schemas or tool-call parsing |
| BFCL (gorilla) | function-call accuracy (AST / executable), multi-turn since v3 | tools | accuracy, not serving performance (README documents no latency metrics) |
| tau2-bench (now tau3) | task success with an LLM user simulator; domains mock, airline, retail, telecom, banking_knowledge | tools + multi-turn | accuracy; needs a second LLM as user |
| AgentBench FC (2025-10) | task success; alfworld, dbbench, knowledgegraph, os_interaction, webshop via docker-compose workers | function-calling agents | accuracy; no serving variant found in the README |
| Applied-Compute `trie` | TTFT, TTFAT, decode TPS, server cache hit rate, "eligible" cache hit rate | tool-use turns from production-derived distributions | its README says cross-trace shared prefixes (system prompt, tool block) are not modeled; no tool-call validity |
| kubernetes-sigs `inference-perf` OTel trace replay (PR #372) | per-request + session metrics | call graphs with fan-out and shared prefixes from OTel traces | tool-call validity not mentioned in the PR description |
| `callanjfox/kv-cache-tester` `trace_replay_tester.py` | TTFT/TTLT/ITL vs cache rate | 739 Claude Code traces, sub-agents | ships coding-agent traces only; tool-call validity not mentioned |
| SemiAnalysis AgentX / AIPerf | throughput vs TTFT under concurrency | Claude Code sessions as DAGs; per-replay cache-bust markers | states it does not support model-quality evaluation |

Server support for the cache metric, checked in source/docs:
vLLM emits `usage.prompt_tokens_details.cached_tokens` only with
`--enable-prompt-tokens-details` (`vllm/entrypoints/launchers/cli_args.py`,
`PromptTokenUsageInfo` in `vllm/entrypoints/serve/engine/protocol.py`); OVMS
documents `stream_options.include_usage` and
`prompt_tokens_details.cached_tokens` for continuous-batching servables
(`docs/model_server_rest_api_chat.md`). trie documents SGLang's flag as
`--enable-cache-report` (not checked in SGLang source).

## Design

* **Teacher-forced history.** After every turn the trace's recorded assistant
  message is appended, not the live reply, so every run sends byte-identical
  prompts and results compare across servers and settings. Cost: a model that
  answers differently does not change the conversation.
* **Prefix-cache benefit, two ways.** Server-reported `cached_tokens /
  prompt_tokens`, and a server-agnostic A/B: `--cache-bust request` prepends a
  unique nonce line to every request's system prompt, so no request shares a
  prefix with any other while token counts change by a few tokens.
  `--cache-bust session` keeps intra-session reuse and removes cross-session
  sharing. `compare` reports the busted/normal ratio of per-turn-index median
  TTFT.
* **Tool-call validity.** A call is valid when its name is a declared tool, its
  arguments parse to a JSON object, all `required` parameters are present and
  top-level types match the schema (`true` is not an integer). Calls are taken
  from structured `delta.tool_calls` (fragments joined per index) or, if none,
  from the content as a bare JSON object or Hermes `<tool_call>` tags.
  `tool_valid_structured` counts only the first source; `tool_valid_any` both.
  The gap between them shows a missing server-side tool parser.
* **Arrivals.** One thread per session, started at `start_s * --time-scale`;
  `think_s` sleeps between turns; `--max-concurrency` caps live sessions.
* **Lengths in words.** No tokenizer in the stdlib. The summary carries the
  server's `prompt_tokens`; treat word counts as the knob.

## Trace families (seed 0, scale 1)

Parameters live in each trace's `params` and in `agent_trace_gen.py`'s docstring.

| Family | Shape | Sessions | Turns (tool turns) | System words | Last-turn prompt words p50 / max | Arrival span |
|---|---|---|---|---|---|---|
| support | tau-bench-like support agent, 8 tools, Poisson arrivals (mean gap 2 s) | 16 | 72 (56) | 2409 | 2780 / 2946 | 40.8 s |
| coding | SWE-style agent, 4 tools, 400-1600-word tool results | 4 | 40 (36) | 910 | 12132 / 12558 | 15 s |
| fanout | 3 bursts x 8 sub-agents starting together; system prompt + 600-word burst brief shared | 24 | 58 (34) | 1209 | 2180 / 2588 | 20 s |

The synthetic text is random words from a fixed vocabulary. It exercises the
serving path (prefill, cache, tool parser, concurrency); it says nothing about
task quality, and tool-call validity on it is a parser/format measurement only.

## Verification done (no server, no heavy compute)

* 35 unit tests against a stub that streams SSE, splits tool-call arguments
  across deltas, and models a 16-word-block prefix cache with a sleep per
  uncached word.
* On the stub the cache-bust control behaves as designed: `request` bust gives
  `cached_fraction` 0; `session` bust gives 0 cached on turn 0 but keeps
  intra-session reuse; normal mode caches the shared system prompt on turn 0 of
  later sessions; fanout turn 0 reuses system prompt + burst brief.
* Mutation check: five hand-made bugs (bust ignored, tool-argument fragments
  overwritten, teacher forcing dropped, bool accepted as integer, decode rate
  computed over e2e time) each fail the suite.

None of this is a measurement of a real server. Everything below is.

## Pre-registered acceptance criteria (live, not yet run)

Run on one model with prefix caching enabled, after the live benchmarks on
this host finish.

1. **Harness runs clean.** `errors == 0` for all three families at `--time-scale 1`.
2. **Token approximation.** Server `prompt_tokens` / prompt words between 1.1 and 1.6.
3. **Cache visible.** support, normal run: `cached_fraction >= 0.7`; request-bust run: `<= 0.05`.
4. **Benefit measurable.** support `ttft_p50_speedup >= 2.0`; coding per-turn
   speedup rises with turn index (prediction from growing prompt length).
   Falsifier: speedup < 1.2 while criterion 3 holds means TTFT at these lengths
   is not prefill-dominated, or the cache counter does not reflect reuse.
5. **Timing agrees with a tuned baseline.** A support-shaped config for vLLM's
   `benchmark_serving_multi_turn.py` (no tools, same lengths, same clients)
   gives a TTFT p50 within 10% of `agent_trace.py --cache-bust session` on the
   same server. A larger gap is a client-timing bug in one of the two.
6. **Validity verdicts are right.** 20 replies hand-labelled valid/invalid: the
   harness agrees on 20/20.

## Provenance

* **Known (prior art read):** streaming TTFT/TPOT, multi-turn replay with growing
  history (vLLM, SGLang, trie, inference-perf, kv-cache-tester, Dynamo
  `multiturn_bench`); server-reported `cached_tokens` as the cache metric
  (SGLang bench_multiturn, trie); cache-bust markers between replays (AgentX);
  server-side cache isolation (vLLM `cache_salt`); bursty fan-out sub-agents
  (inference-perf, AgentX, kv-cache-tester); Hermes `<tool_call>` tag format
  (widely used convention; its origin was not checked in this wave).
* **No prior art found (weak search):** scoring tool-call validity per turn
  inside the serving-performance replay (structured vs content-parsed), next to
  TTFT and cache hit for the same request. Search log: 4 SearXNG queries (no
  relevant hits; only general-web results came back), 1 Exa query (returned
  trie, inference-perf PR #372, kv-cache-tester, netpreme/coding_agents,
  llm-inference-bench, Dynamo bench, AgentX), plus the READMEs above.
  llm-inference-bench runs tool-eval-bench as a separate phase, not per replayed
  request. Treat this as unconfirmed.
* **Not searched:** a stdlib-only implementation choice has no prior-art claim.

## Live probes (exact)

```sh
M=MODEL; U=http://127.0.0.1:8000/v3/chat/completions; mkdir -p runs
for f in support coding fanout; do
  python3 bench/agent_trace.py gen $f -o runs/$f.json
  for b in none session request; do
    python3 bench/agent_trace.py run runs/$f.json --model $M --url $U \
      --cache-bust $b --out runs/$f-$b
  done
  python3 bench/agent_trace.py compare runs/$f-none.summary.json runs/$f-request.summary.json
done
```

For vLLM, start the server with `--enable-prompt-tokens-details` (and a tool
parser, e.g. `--enable-auto-tool-choice --tool-call-parser ...`) and use
`--url http://127.0.0.1:8000/v1/chat/completions`.

## Limits

* History is teacher-forced; live-reply agents (where the model's own output
  drives the next turn) are not modeled.
* Lengths are words, not tokens.
* `decode_tps` assumes the final usage chunk is present; without it the count
  of content deltas stands in for completion tokens.
* One process, threads: thousands of concurrent sessions need an async client.
