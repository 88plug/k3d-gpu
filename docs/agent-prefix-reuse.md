# Prefix (KV) reuse for k3d-gpu agents

Agents resend the same system prompt and tool schemas on every turn, plus a
history that only grows. OpenVINO Model Server (OVMS) can skip recomputing the
part of a prompt it has already seen. This document covers four things: what
OVMS 2026.4.1 actually does (read from source), the k3d-gpu agent defaults that
get the most reuse out of it, a time to first token (TTFT) model that predicts
the gain, and the live probes, with their pass criteria fixed before any
measurement.

Status: design and simulation only. Nothing here has been measured on hardware.
Every number marked *predicted* waits for the probes in
[Pre-registered live probes](#pre-registered-live-probes).

Tooling: `bench/prefix_model.py` (stdlib Python) holds the simulator, the TTFT
model, cache sizing, the probe trace generator and the scorer.
`bench/test_prefix_model.py` tests it. Replays go through `bench/agent_trace.py`
(branch `claude/invent-agent-trace-bench`).

## 1. What OVMS 2026.4.1 does

Sources, all pinned:
OVMS `v2026.4.1` (6c25e51e) · OpenVINO GenAI `ab01d177` (the commit OVMS
pins in `versions.mk:24`, tag `2026.4.1.0`) · OpenVINO `2026.4.1` plugin
sources · compute-runtime `26.18.38308.1` (the ubuntu24 driver in OVMS
`Makefile:172`) · `OpenVINO/Qwen3-4B-int4-ov` at b467368d.

| # | Fact | Where |
|---|------|-------|
| F1 | `--pull` writes `enable_prefix_caching: true` and `cache_size: 0` into graph.pbtxt by default. The `claude/agent-stack` manifest passes neither flag, so it **already serves with prefix caching on and a dynamic KV pool**. The proto default (`false`) applies only to hand-written graphs. | `graph_cli_parser.cpp:52-63`, `graph_export.cpp:188-191`, `llm_calculator.proto:92,106` |
| F2 | `cache_size` is a whole number of GB (×1024³). `0` means the pool grows on demand. | `graph_cli_parser.cpp:60-63`, `cache_orchestrator.hpp:867` |
| F3 | Block size is 32 tokens on CPU and 16 on GPU (256 with XAttention). It is not configurable for the main model. | `kv_cache_manager.hpp:97-109` |
| F4 | A block's hash covers its own tokens and the previous block's hash. Reuse is therefore the longest run of cached blocks starting at token 0, and **one changed token at position p limits reuse to floor(p/B)·B**. Reuse can stop part way into a block if that partial hash was stored. | `sequence_group.cpp:20-26, 84-100`, `block_manager.hpp:1703-1735` |
| F5 | A fully cached prompt still recomputes its last token. | `block_manager.hpp:1828-1830` |
| F6 | When a request finishes, its blocks go into an LRU store. **Those blocks count as free** (`num_free_blocks = free + overwritable`). New allocations take fresh blocks first, then overwrite the least recently used cached block. Restoring a block refreshes its timestamp. | `block_manager.hpp:144-155, 307-309, 522-556, 1689` |
| F7 | With `cache_size: 0`, the pool grows only to cover the live requests: Σ min(2·prompt, prompt + max_tokens), plus 256-token steps when a request needs more. Because cached blocks count as free (F6), the pool does not grow to keep them. **With one request in flight at a time, a dynamic pool holds about one context.** | `scheduler.hpp:56-57, 887-955, 960-990`, `block_manager.hpp:939-955` |
| F8 | Lookup runs when the request is added, before scheduling. | `pipeline_impl.cpp:341-343` |
| F9 | Default KV precision for Qwen3-4B-int4: **u8 on CPU** (CPU plugin default) and **u4 on GPU** (a PagedAttention model with 4-bit weights is given u4 KV unless `kv_cache_precision` is set; this model's rt_info does not set it). | OpenVINO `intel_cpu/src/config.h:79-81`, `intel_gpu/src/runtime/execution_config.cpp:286-301`; model `openvino_model.xml` rt_info |
| F10 | On the GPU, dynamic growth is capped by `device_total_mem_size − 1.1·used`, and a static `cache_size` must fit under that limit. For an integrated GPU, compute-runtime reports total memory as `min(RAM, GTT)·0.94 − 450 MB`. **On this 128 GB host, the UHD 770 reports about 117 GB, so a dynamic iGPU pool is bounded only by host RAM.** | GenAI `utils.cpp:1082-1110`; compute-runtime `device.cpp:886-901`, `drm_memory_manager.cpp:1639-1661` |
| F11 | The NPU (stateful pipeline) ignores `cache_size`, `enable_prefix_caching` and `dynamic_split_fuse`. When the device is NPU, `--pull` maps prefix caching to `NPUW_LLM_ENABLE_PREFIX_CACHING`. k3d-gpu's `auto` never chooses the NPU. | `docs/llm/reference.md:276`, `graph_cli_parser.cpp:155-157` |
| F12 | **OVMS 2026.4.1 does not report `cached_tokens`.** Chat/completions usage has only prompt/completion/total, and the Responses API has a TODO for it. A cache hit can only be seen through TTFT, or through the `llm_executor` log line `Cache type: dynamic|static, cache usage: X% of Y`. | `openai_completions.cpp:588-596`, `openai_json_response.hpp:190-195`, `openai_responses.cpp:1036,1168`, `llm_executor.hpp:65-71,104` |
| F13 | The chat template runs in Python Jinja. `tojson` is `json.dumps(ensure_ascii=False)` **without `sort_keys`**, so the key order of tool schemas reaches the prompt exactly as the client sends it. `strftime_now` is available, so templates that print dates change their prefix every day (Qwen3's does not). | `servable_initializer.cpp:390, 454` |
| F14 | Qwen3 template: system text comes first, then `# Tools` with one `tojson` line per tool, then the turns, all inside the system block. Reasoning is removed from assistant turns that come before the last real user query. | `chat_template.jinja` of the model |
| F15 | Prefill is chunked: `max_num_batched_tokens` defaults to 256, so a cold N-token prompt takes ≥ N/256 steps. The OVMS tuning guide suggests 4096 to 8192 for TTFT. | `scheduler_config.hpp:23`, `llm_calculator.proto:89`, docs.openvino.ai performance tuning |
| F16 | Linear-attention speculative decoding requires prefix caching off. Qwen3-4B has no linear attention, so this does not apply to the default model. | `pipeline_impl.cpp:388`, `scheduler.hpp:1056` |

KV bytes per token for Qwen3-4B (36 layers, 8 KV heads, head_dim 128):
`2·36·8·128 = 73,728` elements. u8 (CPU) = **72 KiB/token**, u4 (GPU) =
**36 KiB/token**, f16 = 144 KiB/token. Quantisation scales add a little on top.
Their size is unverified; read it from the log (see probe P0).

Host facts (read-only sysfs, this machine): `intel-rapl:0` long_term =
short_term = **117 W**, peak_power = 230 W. The firmware has raised the limits
above the 35 W base power of the 13900T. The P-cores, E-cores and UHD 770 share
that one package budget, so a CPU-side and an iGPU-side agent slow each other
down. RAM is 125 GiB.

## 2. Agent defaults (design)

The baseline is the `claude/agent-stack` manifest. Prefix caching is already on
there (F1), so these defaults add retention, stability and pre-warming. They do
not turn caching on.

### 2.1 Cache size per device

Use a static pool sized for the agents that run side by side:

```
cache_size_gb = ceil(SESSIONS × CONTEXT × kv_bytes_per_token / 2^30)
```

`bench/prefix_model.py size` computes it:

| device | KV/token | 1×8k | 1×32k | 4×8k | **4×32k (default)** | 8×32k |
|--------|----------|------|-------|------|------------------|-------|
| CPU (u8) | 72 KiB | 1 | 3 | 3 | **9** | 18 |
| GPU (u4) | 36 KiB | 1 | 2 | 2 | **5** | 9 |

Rules:
- Add env `K3D_GPU_AGENT_SESSIONS` (default 4) and `K3D_GPU_AGENT_CONTEXT`
  (default 32768, Qwen3's native length) to `agent_manifest`. Pass
  `--cache_size=<gb>` and an explicit `--enable_prefix_caching=true` to the
  `pull` init container.
- Cap `cache_size` at 25% of node allocatable memory. If one context does not
  fit under the cap, fall back to `0` (dynamic) and warn.
- Raise the pod `memory` request by `cache_size` Gi. On CPU the pool sits in the
  OVMS process. On the iGPU it is host RAM too (F10). Whether i915 GEM pages
  count against the pod's memcg is **unverified**, so the request is raised
  either way.
- Why static: under F7, a dynamic pool keeps only the shared prefix once two or
  more agents take turns (simulated, §3.3). On the iGPU, a dynamic pool can also
  grow until host RAM runs out (F10). The trade-off is that a static pool
  preempts requests once SESSIONS × CONTEXT is exceeded (OVMS reference,
  "Caching settings").
- NPU: no change (F11).

### 2.2 Prompt structure (for clients; goes in the README)

Reuse ends at the first token that differs (F4). Order every request from most
stable to least stable:

1. **System text: static only.** No dates, request IDs, cwd, git status,
   memory or user names. Anything dynamic goes in the first user message. A
   dynamic line in the system text also invalidates the tool block that follows
   it (F14).
2. **Tools: one frozen list for the whole session.** Keep the order fixed and
   the JSON key order fixed (F13, no key sorting). Adding or removing one tool
   invalidates everything after the system text. Agents that should share a
   prefix must send byte-identical system text and tools. One option is to give
   every agent the union tool set and put the role-specific text in the first
   user message.
3. **History: append only.** Editing or summarising an earlier turn in place
   discards everything after it. If context must be compacted, do it rarely and
   all at once.
4. **Qwen3 thinking: `chat_template_kwargs: {"enable_thinking": false}` in tool
   loops.** With thinking off, an assistant turn renders the same way
   (`<|im_start|>assistant\n` + content) whether or not it comes after the last
   user query. Each new request then reuses the previous prompt up to its final
   `assistant\n`. Only the 4-token empty-think scaffold and the previous reply
   are recomputed.
   With thinking on, every new user message changes how the assistant turns of
   the previous tool loop render, so that whole loop is prefilled again. The
   Qwen3 issue #1826 reports a full re-prefill. That happens in single-slot
   caches; in OVMS's paged cache the blocks of the earlier prompt survive, so
   the cost here is only what comes after that point.
5. Sampling parameters, `max_tokens` and `tool_choice` do not change the
   rendered prompt, so they do not affect reuse.

### 2.3 Pinned system prompt

GenAI 2026.4.1 cannot pin cache blocks: there is no pin or priority field in
`SchedulerConfig` or the block manager. The equivalent design has four parts:

- **Canonical prefix file:** `share/agent-prefix.json` holds
  `{"system": "...", "tools": [...]}`. `k3d-gpu agent prefix` prints it so
  clients send it byte for byte.
- **Pre-warm:** `agent_warmup` sends the canonical system and tools, with
  `max_tokens: 1` and `enable_thinking: false`. The first real request then
  restores the prefix instead of computing it. The thinking flag changes only
  the text after the prefix (F14), so warm-up and clients share it.
- **Kept hot:** every request that uses the prefix restores it and refreshes its
  LRU timestamp (F6). It is evicted only after everything else has gone. A
  static pool sized by §2.1 stops it from being evicted at all.
- **Warmed again after any restart.** The KV cache lives in memory only.
  `--cache_dir` caches compiled models, not KV.

### 2.4 Tuned baseline knob (separate from reuse)

`max_num_batched_tokens` (F15) sets the cost of a cold prefill. The probes
compare against a baseline with this knob tuned, so the reuse gain is not
inflated by a slow 256-token chunked cold path. P0 picks the best of
{256, 1024, 4096, 8192}.

## 3. TTFT model and predictions

### 3.1 Model

For an N-token prompt whose first c tokens are restored (c floored to blocks,
c ≤ N−1):

```
TTFT(N, c) = T0 + α·(N − c) + β·(N² − c²)/2
```

- T0 is the fixed cost: HTTP, Jinja rendering, tokenising, the first decode
  step and the reasoning parser.
- α is the weight cost per prefilled token.
- β is the attention cost: each new token attends to every earlier token,
  including the cached ones.

Dividing by the cold time gives the warm/cold ratio:

```
R(s) = f0 + (1 − f0)·[(1 − fa)(1 − s) + fa(1 − s²)]
```

Here s = c/N, f0 is the fixed share of cold TTFT and fa is the attention share.
Two consequences:
- The best possible speedup is 1/f0, so the fixed cost is the ceiling.
- At long context the uncached tail attends to the whole cached prefix, so
  reusing half the prompt saves less than half the time.

FLOP priors for Qwen3-4B:
- Weights: 2·3.63e9 FLOP/token (non-embedding parameters, computed from
  config).
- Attention: 4·36·32·128·N²/2.
- Attention share of a cold prefill: 0.08 at 2k, 0.25 at 8k, 0.57 at 32k.

*Predicted* warm/cold TTFT (`prefix_model.py predict`, CPU blocks):

| N | f0 | s=0.5 | 0.8 | 0.9 | 0.95 | 0.99 | 1.0 |
|---|----|-------|-----|-----|------|------|-----|
| 2048 | 0.01 | 0.52 | 0.22 | 0.13 | 0.076 | 0.027 | 0.011 |
| 8192 | 0.01 | 0.57 | 0.25 | 0.13 | 0.072 | 0.025 | 0.010 |
| 8192 | 0.05 | 0.58 | 0.28 | 0.17 | 0.110 | 0.064 | 0.050 |
| 32768 | 0.01 | 0.65 | 0.30 | 0.16 | 0.088 | 0.027 | 0.010 |

### 3.2 Agent turns

With thinking off and an append-only history (§2.2), turn n+1 reuses all of
turn n's prompt except the 4-token scaffold. So s = (P_n − 4)/P_{n+1}, and the
uncached part is the previous reply, the tool result and the new message.

### 3.3 Retention: dynamic vs static (simulated)

The simulator models F4 to F7. The workload is K agents taking turns one at a
time, a 2048-token shared prefix, +512 tokens of private history per turn,
128-token replies and FLOP-prior timing with f0 = 0.02. The table shows warm/cold
TTFT per turn. The CPU static pool comes from §2.1.

| K | pool | t1 | t2 | t4 | t7 |
|---|------|----|----|----|----|
| 1 | dynamic | 0.26 | 0.22 | 0.17 | 0.13 |
| 1 | static 3 GB | 0.26 | 0.22 | 0.17 | 0.13 |
| 4 | dynamic | 0.42 | 0.52 | 0.65 | 0.76 |
| 4 | static 9 GB | 0.26 | 0.22 | 0.17 | 0.13 |

- One agent: a dynamic pool is enough.
- Four agents taking turns: the dynamic pool keeps only the shared prefix, and
  each agent's history is computed again. The static pool keeps all of it.
- *Predicted* gain from the static pool: 1.6× at turn 1, 2.3× at turn 2, 3.8×
  at turn 4 and 5.8× at turn 7.

Requests that truly overlap enlarge the dynamic pool (F7). The thrashing above
is the case where agents take turns, with tool time in between, but their
requests do not overlap.

## 4. Pre-registered live probes

Not run. A LIVE benchmark currently holds this host. Run the probes only when
the host is otherwise idle, on a warm cluster, one device at a time.

### Protocol (identical for CPU and GPU)

```sh
export DEV=CPU                     # then GPU
K3D_GPU_AGENT_DEVICE=$DEV k3d-gpu agent up        # claude/agent-stack
kubectl -n k3d-gpu-agent port-forward svc/ovms 8000:8000 &
URL=http://127.0.0.1:8000/v3/chat/completions; M=Qwen3-4B-int4-ov
AT=bench/agent_trace.py; PM=bench/prefix_model.py; O=/tmp/prefix-$DEV; mkdir -p $O
# graph knobs go on the pull init container (agent-stack: it rewrites graph.pbtxt on every start)
knob() { kubectl -n k3d-gpu-agent patch deploy/ovms --type=json -p \
  "[{\"op\":\"add\",\"path\":\"/spec/template/spec/initContainers/0/args/-\",\"value\":\"$1\"}]"; }
fresh() { kubectl -n k3d-gpu-agent rollout restart deploy/ovms &&
          kubectl -n k3d-gpu-agent rollout status deploy/ovms --timeout=1800s; }
```

Every arm starts on a freshly restarted pod, so the KV cache is empty. Record
`bench/energy.sh` RAPL alongside each arm. Keep the `llm_executor` log lines for
F12.

**P0: cold calibration and tuned baseline.**
```sh
python3 $PM gen-probe calib --out $O/p                    # 8 lengths x 5 reps
for B in 256 1024 4096 8192; do     # tuned baseline: keep the best B for all arms
  knob --max_num_batched_tokens=$B; fresh
  python3 $AT run $O/p.calib.json --url $URL --model $M --out $O/calib-b$B \
      --cache-bust request --max-concurrency 1
done
```
Fit (T0, α, β) from the best-B run.

KV bytes per token (AC7): this needs one more request. On a fresh pod with
`cache_size: 0`, send one prompt of P tokens with `max_tokens` m. By F7 the pool
is then `ceil(min(2P, P+m)/B)` blocks. The `llm_executor` line reports the pool
as "… of Y". Bytes per token = Y / (blocks·B).

**P1: share sweep.** The cache is static (8 GB) and larger than everything P1
and P2 write: about 65k tokens, which is 4.5 GiB at u8. Nothing is evicted.
Control arm: caching off.
```sh
python3 $PM gen-probe sweep --out $O/p --words 4096       # 8 shares x 5 reps
knob --cache_size=8; fresh
python3 $AT run $O/p.prime.json --url $URL --model $M --out $O/prime
python3 $AT run $O/p.sweep.json --url $URL --model $M --out $O/sweep --max-concurrency 1
python3 $PM analyze --device $DEV --cache-gb 8 --calib $O/calib-b<best>.jsonl \
  --run $O/p.prime.json:$O/prime.jsonl --run $O/p.sweep.json:$O/sweep.jsonl
knob --enable_prefix_caching=false; fresh                 # control arm, same commands
```

**P2: one-token perturbation.** Same arm as P1. Positions are 16, N/2 and
N−64; the changed word is at p.
```sh
python3 $PM gen-probe perturb --out $O/q --words 4096 --reps 1
python3 $AT run $O/q.prime.json   --url $URL --model $M --out $O/qprime
python3 $AT run $O/q.perturb.json --url $URL --model $M --out $O/perturb --max-concurrency 1
python3 $PM analyze --device $DEV --cache-gb 8 --calib ... \
  --run $O/q.prime.json:$O/qprime.jsonl --run $O/q.perturb.json:$O/perturb.jsonl
```

**P3: K agents taking turns, dynamic vs static.** K = 4, 8 turns, a 2048-word
shared prefix and 400 words per turn. Requests run one at a time.
```sh
python3 $PM gen-probe interleave --out $O/i --words 2048 --agents 4 --turns 8 --history-words 400
# arm D: cache_size 0 (agent-stack default)   arm S: knob --cache_size=$(python3 $PM size --device $DEV | jq .cache_size_gb)
python3 $AT run $O/i.interleave.json --url $URL --model $M --out $O/inter-D --max-concurrency 1
python3 $PM analyze --device $DEV --calib ... --run $O/i.interleave.json:$O/inter-D.jsonl            # dynamic replay
python3 $PM analyze --device $DEV --calib ... --run ...:$O/inter-D.jsonl --cache-gb 1000             # "never evicts" replay
```

**P4: realistic agent trace, end to end.** The `coding` family from
`agent_trace.py gen`, with `--cache-bust none`, then `session`, then `request`,
each on a fresh pod, followed by `agent_trace.py compare`.

### Acceptance criteria (fixed before measurement)

Scoring is by `prefix_model.py analyze`. A group passes when
|measured − predicted| ≤ max(20%·predicted, 50 ms). The first request of each
run is replayed but not scored, because it also pays for connection setup.

| ID | Claim | Pass | Falsified if |
|----|-------|------|--------------|
| AC1 | TTFT(N, c) predicted from the P0 fit alone (no free parameters) holds across share s | P1 cache-on: ≥ 7/8 share groups pass | ≥ 2 groups fail |
| AC1c | The probe can tell cache-on from cache-off | P1 control: every group with s ≥ 0.5 fails | any s ≥ 0.5 group passes with caching off |
| AC2 | Chained-hash prefix: one token at p caps reuse at floor(p/B)·B (F4) | P2: 3/3 pass. p=16 measures as cold; p=N−64 measures as warm | p=16 measures warm, or p=N−64 measures cold |
| AC3 | Dynamic pool keeps about one context (F6/F7) | P3 arm D: dynamic replay ≥ 90% pass **and** never-evicts replay ≤ 60% pass; arm S: static replay ≥ 90% pass | arm D matches the never-evicts replay (≥ 90%) |
| AC4 | FLOP prior for the attention/weight split | P1 at N≈8k, s=0.95: measured warm/cold ∈ [0.05, 0.15] | outside that range. AC1 can still pass, because it uses the fit, not the prior. |
| AC5 | §2.1 static sizing beats the tuned baseline for agents taking turns | P3: arm S over arm D median TTFT ≥ 2× for turns ≥ 2; at K = 1, arms equal within ±10% | gain < 1.5× at turn ≥ 4, or K = 1 differs by > 10% |
| AC6 | Pre-warm (§2.3) | First request after warm-up: TTFT within AC1 tolerance of TTFT(N, c = canonical prefix) | measures as cold |
| AC7 | KV bytes per token (F9) | P0 log method: CPU 72 KiB, GPU 36 KiB, each −0/+15% (the + allows for scales) | outside that range. Re-derive §2.1 from the measured value. |

Run validity: ≤ 2% request errors and no `ttft_s = null` rows. Qwen3 thinking
must produce a content or reasoning delta within `max_tokens` = 8. If either
condition fails, the run is invalid; it is neither a pass nor a fail.

## 5. Provenance

| Mechanism | Partition | Search log |
|-----------|-----------|------------|
| Paged KV, block hashing, automatic prefix caching, LRU of free blocks | known: vLLM (Kwon et al., SOSP '23), SGLang RadixAttention, OpenVINO GenAI | not searched for novelty; standard |
| TTFT gain from prefix reuse vs share; fixed-cost ceiling | known: PrefixBench-H100 (arXiv 2609.19657) measures the 5–6.5× gain at 8k on H100; the Prompt Cache paper (arXiv 2311.04934) | exa 2026-10-16: "TTFT speedup prefix KV reuse cached fraction linear+quadratic". No exact closed form with the N² attention term was found in what was read. Treat it as standard algebra, not a contribution. |
| LRU thrash of per-session prefixes when agents take turns | known: KVFlow (arXiv 2507.07400), CacheWise (arXiv 2606.16824), RR-Evict (arXiv 2609.32278), vLLM RFC #52113 | exa 2026-10-16: "prefix cache thrashing multiple agents interleaved LRU" |
| Qwen3 template breaks prefix stability (thinking stripped from history) | known: QwenLM/Qwen3#1826, Qwen3.6#112/#131 (`preserve_thinking`) | exa 2026-10-16: "Qwen3 chat template strips reasoning breaks prefix caching" |
| GenAI dynamic pool counts cached blocks as free, so `cache_size: 0` keeps about one context with one request in flight (F6/F7) | **source-derived; no prior write-up found**. The OVMS tuning guide says only that static sizing "can improve prompt reuse under steady load". | exa 2026-10-16: "openvino genai dynamic KV cache allocation prefix caching evicts cached blocks instead of growing". Hits: GenAI SchedulerConfig docs, OVMS tuning guide. No statement of the mechanism. |
| iGPU `device_total_mem_size` ≈ 0.94·RAM leaves a dynamic pool bounded only by host RAM | **source-derived; unsearched for prior art** | — |
| `tojson` without `sort_keys` makes the client's key order matter | **source-derived; unsearched** | — |
| Pinned prefix as pre-warm plus LRU-hot plus static sizing | engineering composition of known parts; not claimed as novel | — |

Nothing in this document has been put into practice. AC1 to AC7 decide that.
