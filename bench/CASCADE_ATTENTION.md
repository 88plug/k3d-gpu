# M3: cascade (shared-prefix) decode attention on an Intel client CPU

Status: static prototype. No live measurement has been made. Every number below
is a model output or a prior unless marked **measured** (from the H2 campaign) or
**verified** (read from a primary source). All live claims are pre-registered
probes for after the wave-1 benchmarks (section 6).

Target metric: concurrent agents per box at a p95 agent-turn SLO (A@SLO), on
support-shaped agent traces, Qwen3-4B int4 on OVMS 2026.4.1, i9-13900T P-cores.

## 1. Verdict of the static stage

* **The bandwidth framing of the idea does not survive the cache model.** The
  idea assumed stock paged attention streams the shared prefix KV from DRAM
  once per sequence (B x P per step). The OpenVINO 2026.4.0 CPU kernel's loop
  orders (read from source, section 3) make every thread walk the same physical
  prefix blocks at nearly the same time. A shared-LRU cache simulation of both
  decode schedules gives DRAM KV bytes of exactly 1 x P for the shared prefix
  at every thread count and effective L3 size down to 2 MB on the `bhl`
  schedule, and at most 1.97x unique on the `mixed` schedule once the effective
  L3 drops to 8 MB (16k-token prefix). It never approaches B = 8x.
  Confidence 0.7 (not modelled: non-LRU replacement, hashed slices, prefetchers).
  The ablation arm `cascade_bw_only` (prefix read once, stock compute) gives
  1.00x in every configuration.
* **What remains is a compute lever.** On Raptor Lake (no AMX, no AVX-512) the
  stock u8 KV kernels dequantize every K and V element once per (sequence,
  query head): `cvtepu8_epi32` + `cvtepi32_ps` + `sub_ps` + `fmadd_ps` per 8
  MACs (verified in source), 3 p0/p1 uops per 8 MACs. A cascade kernel
  dequantizes each prefix block once for all B x G = 32 rows of a KV head and
  then runs pure FMA (or AVX-VNNI for q.K).
* **Model prediction (priors, c=8, shared 3.4k prefix, 64-token suffixes):**
  aggregate decode 1.09-1.80x stock for the fp32 cascade (prior 1.37x),
  1.24-1.97x for cascade + AVX-VNNI q.K (prior 1.52x). A@SLO(20 s):
  stock 5-7 agents, cascade 7-8, cascade_vnni 8-9. Prefill (172 tok/s,
  measured) binds the agent loop as much as decode, so the A@SLO gain is
  smaller than the decode gain.
* **Part of the gain is the ISA, not the cascade.** The ablation arm
  `vnni_gemv` (AVX-VNNI q.K, no prefix sharing) reaches 1.11-1.35x on its own.
  The cascade-specific share (cascade_vnni / vnni_gemv) is 1.12-1.46x.
* This is an engineering port of known work (Hydragen arXiv 2402.05099,
  ChunkAttention arXiv 2402.15220, FlashInfer cascade attention,
  RelayAttention). It is not a world-first.

## 2. What was built (all in `bench/`)

| File | What it is |
|---|---|
| `cascade_kernel.c` | AVX2 + AVX-VNNI microkernels for one decode-attention layer with the OpenVINO u8 KV layout: `stock` (mirrors the OpenVINO AVX2 u8 kernels), `cascade` (prefix dequantized once per block, 4-row x 2-token register-blocked fp32 GEMM, online softmax, log-sum-exp merge with the per-sequence suffix), `vnni_gemv` (ablation), `cascade_vnni`. `check` compares every arm to an fp64 reference on five shapes (empty prefix, empty suffix, B=1/G=1, B=8). `bench` times one or all arms. |
| `cascade_model.py` | Reference cascade math; cache simulation of the stock loop orders; step-time model calibrated on H2; agent discrete-event simulation (A@SLO); `aslo` over live `agent_trace.py` runs; `probes`. Accepts measured kernel rates (`--kernel`). |
| `cascade_probe.py` | Live stage-0 driver: B concurrent streams with shared / distinct / short prompts, prompts pre-warmed into the prefix cache, aggregate decode tok/s over the window where all B decode; `imc` turns `perf stat -I` IMC output into DRAM GB per step net of the idle background. |
| `cascade_live.sh` | The exact live protocol (stages s0, kernel, aslo). |
| `test_cascade.py` | 21 tests, no server: math exactness, splitter equivalence, cache-sim invariants, calibration, ablation sensitivity, DES monotonicity, A@SLO parsing, probe driver against a streaming stub, kernel `check` plus 5 planted-bug mutants that `check` must catch. |

```sh
python3 bench/test_cascade.py                 # ~10 s, builds the kernel if cc + AVX-VNNI present
python3 bench/cascade_model.py l3             # DRAM bytes per step, stock schedules
python3 bench/cascade_model.py step           # per-arm aggregate decode at c=8
python3 bench/cascade_model.py agents --slo 20,30
python3 bench/cascade_model.py probes
```

## 3. Facts and sources

| Fact | Tag | Source |
|---|---|---|
| Decode dispatch: `exec_loop_mixed` when `B >= nthr` or any prompt is in the batch, else `exec_loop_bhl` | verified | openvino 2026.4.0 `src/plugins/intel_cpu/src/nodes/kernels/scaled_attn/executor_pa.cpp` (`MHA::operator()`) |
| `exec_loop_bhl`: `parallel_for3d(B, kv_len_in_blocks, Hk or H, loop_qk)`, softmax, same split for `loop_wk`; static loop when B <= 32 and equal `past_lens` | verified | same file, `exec_loop_bhl` |
| `exec_loop_mixed`: `parallel_for2d_dynamic(attn_work_count, Hk or H)`, each item runs `exec_kernel_one_bh` (all K blocks, softmax, all V blocks) | verified | same file |
| `parallel_for3d` static split = contiguous chunks of the flattened index (`splitter`), TBB `static_partitioner`; `parallel_for2d_dynamic` = TBB `blocked_range2d` | verified | openvino 2026.4.0 `src/core/include/openvino/core/parallel.hpp` |
| AMX-only fast path (`_fastpath_valid_prec` needs `amx_bf16`/`amx_fp16`); otherwise per-query-head `dot_product_block_quantized` / `attn_acc_value_block_quantized` | verified | `executor_pa.cpp` lines ~780-795, `loop_qk`, `loop_wk` |
| u8 by-dim layout `|scale f32|zp f32|u8 x S|`; AVX2 inner loop cvt + sub zp + fma | verified | openvino 2026.4.0 `paged_attn_kernel.hpp` (`dot_product_block_quantized_by_dims`, `attn_acc_value_block_by_dim`) |
| No shared-prefix / cascade option in OVMS | verified | model_server v2026.4.1 `src/llm/llm_calculator.proto` (26 fields; nearest: `enable_prefix_caching`, `sparse_attention_config`) |
| OVMS flags `--cache_size` (GB), `--enable_prefix_caching` (default true), `--kv_cache_precision u8`, `--max_num_seqs`; chat `ignore_eos`, `stream_options.include_usage` | verified | model_server v2026.4.1 `docs/parameters.md`, `docs/model_server_rest_api_chat.md` |
| Host: 8P+16E, CPUs 2k/2k+1 = P-core k (k<8), `avx_vnni` present, no AVX-512/AMX, L3 36 MiB, DDR5 configured 3600 MT/s, 57.6 GB/s peak | verified | `lscpu`, `/proc/cpuinfo` on host; dmidecode per campaign brief |
| Qwen3-4B: 36 layers, 32 q heads, 8 kv heads, head_dim 128; u8 KV 78.3 KB/token | verified | HF config.json (via campaign brief); arithmetic |
| c=1 15.7 tok/s, c=4 56 tok/s, prefill 172 tok/s | measured (H2) | campaign brief |
| Peak MAC/clk/core: stock 16/3, fp32 FMA 16, VNNI ymm 64 | discovery, 0.7 | port counts from the instruction mix above; vpdpbusd 2/clk per the uops.info citation in `claude/invent-agents-per-box-ceiling:bench/ceiling.py` |
| Kernel efficiencies, sustained 3.5 GHz all-P-core clock | prior, unmeasured | replaced by `cascade_kernel bench` |

## 4. Static results

### 4.1 Cache simulation (falsifier for the bandwidth framing)

`cascade_model.py l3`, B=8, one layer, DRAM bytes / unique bytes (1.0 = the
shared prefix comes from DRAM once; the idea's B-fold model is 6.7 at 3.4k
context with 64-token suffixes).

| Schedule | threads | L3 eff. | 3.4k prefix | 16k prefix |
|---|---|---|---|---|
| bhl (static split) | 8, 16 | 36 / 8 / 2 MB | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 |
| mixed (2-D dynamic) | 8, 16 | 36 / 8 / 2 MB | 1.00 / 1.00 / 1.82 | 1.00 / 1.97 / 1.97 |
| bhl, 8 P + 8 E threads (E at half speed), 30% jitter | 16 | 36 / 8 / 2 MB | 1.00 / 1.00 / 1.50 | 1.00 / 1.66 / 1.89 |
| distinct prefixes (control) | any | any | 6.7 x the shared bytes (= the B-fold count) | |

Prediction carried to S0b: shared-prefix c=8 step reads 2.57 GB from DRAM
(weights 2.26 + KV 0.31), distinct reads 4.43 GB.

### 4.2 Step model and ablation, c=8, P=3400, S=64 per sequence

Step = 61.1 ms + 2.57 ms x B (fit to H2 c=1 and c=4) + max(attention compute,
KV DRAM time). Each cell is pessimistic / prior / optimistic for the gain
(pessimistic = fast stock kernel, slow cascade kernel).

| Arm | x stock aggregate decode | role |
|---|---|---|
| stock | 1.00 (57.4 / 51.7 / 41.8 tok/s) | tuned baseline |
| cascade_bw_only | 1.00 / 1.00 / 1.00 | ablation: bandwidth part alone |
| vnni_gemv | 1.11 / 1.22 / 1.35 | ablation: ISA part alone, no cascade |
| cascade | 1.09 / 1.37 / 1.80 | mechanism, fp32 |
| cascade_vnni | 1.24 / 1.52 / 1.97 | mechanism + VNNI q.K |

The stock long/short ratio at c=8 (S0a) is 0.44-0.60 across the band, and it
moves with the gain: the more attention-bound stock is, the more cascade buys.
S0a therefore calibrates the stock attention time; it does not discriminate
bandwidth from compute (both predict the same slowdown).

### 4.3 Agents at SLO (DES, support-shaped: 3-6 turns, 50-160 new tokens/turn, <=96 output, shared 3.4k prefix)

| Arm | A@20 s, wait 1 s | A@30 s, wait 1 s | A@20 s, wait 5 s | A@30 s, wait 5 s |
|---|---|---|---|---|
| stock | 5 / 6 / 7 | 9 / 10 / 12 | 6 / 8 / 9 | 10 / 12 / 13 |
| cascade_bw_only | = stock | = stock | = stock | = stock |
| vnni_gemv | 7 / 7 / 7 | 11 / 12 / 13 | 8 / 9 / 9 | 13 / 14 / 15 |
| cascade | 8 / 8 / 7 | 13 / 14 / 12 | 10 / 10 / 9 | 16 / 15 / 14 |
| cascade_vnni | 9 / 9 / 8 | 15 / 15 / 14 | 12 / 11 / 10 | 17 / 17 / 16 |

Each cell is optimistic / prior / pessimistic kernel efficiencies (optimistic
= slow stock kernel, fast cascade kernel). A p95 turn SLO of 10 s is
unreachable even at N=1 for 96-token turns at 3.4k context: a single stream
decodes about 13.7 tok/s there.

## 5. Pre-registered acceptance criteria

Primary claim (only a cascade-enabled OpenVINO build can SUPPORT it):
**on the agent_trace support family, cascade attention raises A@SLO(20 s) to at
least 1.2x the tuned stock baseline**, with tool-call validity within 2
percentage points of stock. The tuned baseline is OVMS CPU, P-cores,
`--enable_prefix_caching true --cache_size 16 --kv_cache_precision u8`, the best
chunk from P8/M1 if it has landed, prefix stored once in paged blocks.

The idea's original 1.4x bar at c=8 is retired: the objection showed the
mechanism has no DRAM saving to collect, and the model's prior for the fp32
cascade is 1.37x. Revised bars are below.

## 6. Live protocol (serial, after 17:00, nothing else benchmarking)

Precondition: one warm stock OVMS with the tuned-baseline flags above, serving
Qwen3-4B int4 on CPUs 0-15. Record the provenance header from `EXPERIMENTS.md`.
Run `sudo -v` once before s0.

```sh
OUT=$HOME/m3-$(date +%Y%m%d); URL=http://127.0.0.1:8000/v3/chat/completions; MODEL=qwen3-4b
bench/cascade_live.sh s0 "$OUT/s0" "$URL" "$MODEL"         # ~2 h, 5 reps ABBA + B sweep x3
bench/cascade_live.sh kernel "$OUT/kernel"                 # ~10 min, no server needed
bench/cascade_live.sh aslo "$OUT/aslo" "$URL" "$MODEL" /path/to/agent_trace.py  # ~3 h baseline A@SLO
python3 bench/cascade_model.py step --kernel "$OUT/kernel/kernel-8-3400-64.jsonl"
python3 bench/cascade_model.py agents --slo 20,30 --kernel "$OUT/kernel/kernel-8-3400-64.jsonl"
```

`agent_trace.py` is on `claude/invent-agent-trace-bench`. Stage s0 parameters:
B=8, shared prefix 2600 words (~3.4k tokens; the JSON records the server's
`prompt_tokens`), 45-word suffixes (~64 tokens), 160 output tokens with
`ignore_eos`, every prompt pre-warmed with `max_tokens=1`, run invalid if any
timed TTFT exceeds 3 s (cache miss). IMC: `uncore_imc_free_running_{0,1}/data_read/`
at 500 ms intervals, idle background measured for 20 s before each run and
subtracted. Kernel: 8 threads on CPUs 0,2,...,14, `OMP_PROC_BIND=true`, 30
timed reps per invocation, 5 invocations per arm in ABBA order, shapes
B in {4,8,16} at P=3400, P in {8192,16384} at B=8, and S=512 at B=8.
A@SLO: support family, N in {4,6,8,10,12,16}, 1.25N scale (~20 sessions per
slot), `--time-scale 1` (1 s think), 2 reps ABBA, warmup 120 s, A@SLO = largest
N before the first N whose median p95 exceeds the SLO.

### Pass / fail table

| Probe | Measured quantity | Prediction (model) | Pass | Kill / re-scope |
|---|---|---|---|---|
| S0a | c=8 aggregate decode, shared 3.4k / short | 0.44-0.60 (prior 0.54) | calibrates stock attention time | ratio > 0.85: attention is not a cost at 3.4k, stop M3 |
| S0b | DRAM GB/step, shared vs distinct | 2.57 vs 4.43 (+/-15%) | shared <= 3.2: L3 reuse confirmed, bandwidth framing refuted, compute framing stands | shared >= 3.8: stock does stream B x P, bandwidth framing stands; the microbench then understates the gain (its prefix sits in L3) |
| S0b' | distinct - shared GB/step | 1.86 = (B-1) x P x 78.3 KB | within 20% | under 50%: KV size or layout assumption wrong, recheck kv precision |
| S0d | attention-added ms/step at B=8 over B=1 (shared prefix) | ~8 | >= 5: attention compute scales with B (compute-bound) | <= 2: bound elsewhere (bandwidth or overhead); re-derive |
| K0 | `cascade_kernel check` on host | all ok | 20/20 ok | any mismatch: no timing is valid |
| K1 | prefix-attention layer time, stock / cascade, B=8 P=3392 | 2.4x (band 1.5-3.5) | >= 1.5 | < 1.3: the fp32 cascade is not worth a port |
| K2 | cascade_vnni / vnni_gemv layer time | 1.1-1.5 | >= 1.1: cascade structure adds beyond the ISA | < 1.1: port the per-row VNNI q.K kernel instead, no cascade |
| E1 | `step --kernel` projection, c=8 aggregate decode vs stock | 1.37 (fp32), 1.52 (vnni) | >= 1.2 for the arm chosen in K1/K2 | < 1.1: stop |
| E2 | model A@SLO(20 s) for stock vs live `aslo` baseline | 6 (band 5-7) | live within +/-1 agent or 25% | outside: the DES is not trusted; no A@SLO projection is reported |
| E3 | projected A@SLO(20 s) gain with measured kernel rates | 1.2-1.5x | >= 1.2 and E2 passed: build the OpenVINO port | < 1.2: decode is not the binding constraint for agents here (prefill is); move effort to prefill |

The primary claim stays PENDING until an OpenVINO CPU build with the cascade
kernel runs the same `aslo` stage and meets section 5. A VNNI arm additionally
needs a quality check (agent_trace `tool_valid` within 2 pp of stock, same seeds).

## 7. Confidence

| Statement | Confidence |
|---|---|
| Stock already reads the shared prefix from DRAM ~once per step (S0b shared <= 3.2 GB) | 0.7 |
| Decode attention at c=8, 3.4k is compute-bound on these cores (S0d >= 5) | 0.65 |
| fp32 cascade kernel >= 1.5x on the prefix attention op (K1) | 0.6 |
| End-to-end c=8 decode >= 1.2x (E1, best arm) | 0.55 |
| A@SLO(20 s) >= 1.2x (E3) | 0.4 (prefill binds the agent loop) |
| Cascade-specific share >= 1.1x beyond VNNI alone (K2) | 0.55 |

## 8. Not covered

u4 KV (the kernel handles u8 only); sliding-window, ALiBi, sinks, sparse
attention; multi-level prefixes (fanout family's system + burst brief); the
iGPU path; long shared prefixes above ~16k where the `mixed` schedule starts to
lose L3 reuse (section 4.1), which is where a cascade would also save DRAM bytes.
