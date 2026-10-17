# Speculative decoding across engines for `k3d-gpu agent`

> **Archived research** (`archive/invent-spec-decoding`). Refuter verdict: research.
> OVMS adds `ENABLE_CPU_PINNING` and `INFERENCE_NUM_THREADS` to a CPU target's config
> itself, and Fast Draft copies target properties to a draft that has none, so an iGPU
> draft likely fails to compile; the risk section below assumed otherwise. β and o in
> the speed model are unmeasured free parameters. Next probe and status:
> [Research leads](../../EXPERIMENTS.md#research-leads).

Status: design + model. Nothing here is reduced to practice; every speedup
below is a prediction until the probes in [Live probes](#live-probes) run.
Written while live benchmarks held the host, so no inference was run.

## Verdict

1. **Cross-engine drafting gives no overlap in the stack we ship.** OpenVINO
   GenAI 2026.4.1 (the exact GenAI commit inside OVMS 2026.4.1) runs one
   iteration as *draft multistep, then target step*, serially, under one mutex
   ([fast_draft_strategy.cpp:197-232](https://github.com/openvinotoolkit/openvino.genai/blob/2026.4.1.0/src/cpp/src/speculative_decoding/continuous_batching/fast_draft_strategy.cpp#L197-L232)).
   The engines never run at the same time. Putting the draft on another engine
   only changes `t_d`, the draft's own per-token latency. It is worth doing only
   if the draft is faster on that engine than on the target's engine.
2. **"Draft on E-cores, target on P-cores" cannot be configured in OVMS 2026.4.1.**
   Both are device `CPU`. OVMS passes the draft only a device string and a
   scheduler config. With no draft properties, GenAI copies the target's
   properties to the draft
   ([servable_initializer.cpp:213-215](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/src/llm/language_model/continuous_batching/servable_initializer.cpp#L213-L215),
   [pipeline.cpp:130-147](https://github.com/openvinotoolkit/openvino.genai/blob/2026.4.1.0/src/cpp/src/llm/pipeline.cpp#L130-L147),
   [fast_draft_strategy.cpp:133](https://github.com/openvinotoolkit/openvino.genai/blob/2026.4.1.0/src/cpp/src/speculative_decoding/continuous_batching/fast_draft_strategy.cpp#L133)).
   One process also has one cpuset (the P-core balloon). Even when the draft
   could be put on E-cores (GenAI Python, separate core-type hints), serial
   execution means an E-core draft wins only if its `t_d` beats the P-core
   `t_d`. Prediction H3: it will not.
3. **iGPU draft + P-core target is configurable** (`draft_device: "GPU"`) and
   is the only cross-engine split worth measuring. The model says it beats
   P-core-only drafting only if `t_d(iGPU) < t_d(P)`.
4. **Fast Draft forces greedy decoding** in OVMS 2026.4.1, and the client is
   not told
   ([base_generation_config_builder.cpp:30-45](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/src/llm/io_processing/base_generation_config_builder.cpp#L30-L45)).
   The Qwen3 model card says "DO NOT use greedy decoding" in thinking mode
   ([Qwen/Qwen3-4B README](https://huggingface.co/Qwen/Qwen3-4B), line 132).
   So a Fast Draft agent changes model behaviour, not just speed. It cannot be
   a default for the Qwen3 agent.
5. **Prompt-lookup decoding (PLD) is the better first option for an agent.**
   It needs no second model and no second engine. OVMS does not force greedy
   for it ([base_generation_config_builder.cpp:109-122](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/src/llm/io_processing/base_generation_config_builder.cpp#L109-L122)).
   Sampling stays exact: a prompt-lookup candidate is accepted only if the
   target's own sample equals it
   ([sampler.cpp:1404-1420](https://github.com/openvinotoolkit/openvino.genai/blob/2026.4.1.0/src/cpp/src/sampling/sampler.cpp#L1404-L1420)).
   For a point-mass draft that is the Leviathan accept rule, so the output
   distribution is unchanged. The payoff depends on how much the output copies
   the prompt. Tool calls that quote files, code edits and RAG copy a lot.

Ship order: `K3D_GPU_AGENT_SPEC=lookup` first (one engine, keeps sampling),
`draft` second (opt-in, greedy-only, documented), and the iGPU draft device
only if probe P4 passes.

## Host facts (read-only, 2026-10-16)

| Fact | Value | Source |
|---|---|---|
| CPU | i9-13900T, P-cores CPUs 0-15 (HT), E-cores 16-31 | `/proc/cpuinfo`, `/sys/devices/cpu_core/cpus`, `/sys/devices/cpu_atom/cpus` |
| Rated TDP | 35 W | `intel-rapl:0/constraint_0_max_power_uw` = 35000000 |
| **Programmed PL1 / PL2** | **117 W / 117 W**, PL1 window 56 s, PL4 230 W | `constraint_0_power_limit_uw`, `constraint_1_power_limit_uw`, `constraint_2_power_limit_uw` |
| MMIO RAPL | disabled, limits 0 | `intel-rapl-mmio:0/enabled` = 0 |
| RAM | 128 GB, one DRAM bus shared by P, E and iGPU | `/proc/meminfo` |
| iGPU | UHD 770 (8086:a780) | `lspci` |
| dGPUs | RTX 4000 SFF Ada, RTX 2000 Ada | `lspci` |

The board firmware sets PL1 to 117 W, not 35 W. A 35 W cap would have been a
reason to move work off the P-cores. At 117 W, power is not expected to bind
for a single decode stream. P5 checks this with RAPL. The NVIDIA GPUs are
outside OVMS: the OpenVINO GPU plugin drives Intel GPUs only. So the tuned
baseline is the best *Intel* engine. A CUDA target is a separate comparison
that this design does not make.

## What OVMS / GenAI 2026.4.1 supports (verified in source)

OVMS `v2026.4.1` (`6c25e51e`) builds GenAI `ab01d177`
([versions.mk:24](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/versions.mk#L24)),
which is the GenAI `2026.4.1.0` tag. Everything below was read at those two tags.

| Need | Key / flag | Where | Notes |
|---|---|---|---|
| Draft model | `draft_models_path` (graph.pbtxt, `LLMCalculatorOptions` field 11) | [llm_calculator.proto:107-125](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/src/llm/llm_calculator.proto#L107-L125) | Relative path resolves against the graph dir. Strategy (Fast Draft / EAGLE3 / DFlash / MTP) is auto-detected. |
| Draft device | `draft_device` (field 12) | same; [servable_initializer.cpp:213](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/src/llm/language_model/continuous_batching/servable_initializer.cpp#L213) | Defaults to the target device. Setting it without a draft path fails startup (line 245). |
| Draft scheduler | `draft_max_num_batched_tokens`, `draft_cache_size`, `draft_block_size`, `draft_max_num_seqs`, `draft_dynamic_split_fuse` | proto 13-17 | Inherit from the target when unset. |
| CLI for `--pull` | `--draft_source_model`, `--draft_model_path`, `--draft_device` | [graph_cli_parser.cpp:64-75](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/src/graph_export/graph_cli_parser.cpp#L64-L75); [docs/parameters.md:161-164](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/docs/parameters.md) | `--pull` also downloads the draft repo ([hf_pull_model_module.cpp:265-274](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/src/pull_module/hf_pull_model_module.cpp#L265-L274)). The draft must already be OpenVINO IR (the image does no conversion). |
| Prompt lookup | `plugin_config: '{"prompt_lookup": true}'` | [docs/llm/reference.md:331](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/docs/llm/reference.md); [servable.cpp:72-75](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/src/llm/servable.cpp#L72-L75) | `--plugin_config` reaches the generated graph in the HF flow ([cli_parser.cpp:747-751](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/src/cli_parser.cpp#L747-L751)). Docs recommend `dynamic_split_fuse: false` and `max_num_batched_tokens` above the context length. Continuous-batching only; not on the NPU stateful path (reference.md:446). Fixed at construction, not per request. |
| Per-request knobs | `num_assistant_tokens`, `assistant_confidence_threshold`, `max_ngram_size` | [openai_api_handler.cpp:803-805](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/src/llm/apis/openai_api_handler.cpp#L803-L805) | Default `num_assistant_tokens=5`, `max_ngram_size=3`. A Fast Draft request with `num_assistant_tokens: 0` is an error, not "draft off". The two drafting knobs are mutually exclusive. |
| Server-side defaults | `generation_config.json` in the main model dir | [speculative_decoding demo:351-364](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/demos/continuous_batching/speculative_decoding/README.md) | Resolution: request, then `generation_config.json`, then built-in. |
| Greedy enforcement | Fast Draft, EAGLE3, DFlash, MTP set `do_sample=false`, `num_beams=1` | base_generation_config_builder.cpp:40, 54, 78, 102 | PLD: not enforced. |
| Tokenizer match | encode one fixed string; compare length, bos, eos, pad | [fast_draft_strategy.cpp:18-28](https://github.com/openvinotoolkit/openvino.genai/blob/2026.4.1.0/src/cpp/src/speculative_decoding/continuous_batching/fast_draft_strategy.cpp#L18-L28) | Qwen3-0.6B / 4B OV repos both have `eos=<\|im_end\|>`, `pad=<\|endoftext\|>` (HF API). Their IR tokenizers were exported at different dates (2025-04-30 vs 2026-02-24); P0 checks the pair loads. |
| Acceptance metrics | `extended_perf_metrics.get_draft_acceptance_rate()` (accepted / offered) | [performance-metrics.mdx](https://github.com/openvinotoolkit/openvino.genai/blob/2026.4.1.0/site/docs/guides/performance-metrics.mdx) | GenAI API only. OVMS 2026.4.1 exports none of this: no "accept" counter anywhere in `src/llm`. |

Draft candidates on Hugging Face (sizes from the HF API, `openvino_model.bin`):
`OpenVINO/Qwen3-0.6B-int4-ov` 428 MB (sha `f864c610`),
`OpenVINO/Qwen3-1.7B-int4-ov` 1182 MB (`6f32d81e`), target
`OpenVINO/Qwen3-4B-int4-ov` 2264 MB (`b467368d`). The weight-byte ratio
0.6B/4B is 0.19. That is a floor for `c = t_d / t_t` only if decoding is
bandwidth-bound for both. Small models carry fixed per-token overhead, so the
real `c` will be higher.

## Speed model

`research/spec-decoding/specdec_model.py` (stdlib, tests in `research/spec-decoding/test_specdec_model.py`).

- Tokens per iteration (Leviathan et al. 2023, [arXiv:2211.17192](https://arxiv.org/abs/2211.17192)):
  `E = (1 - α^(γ+1)) / (1 - α)`
- Serial iteration, as GenAI 2026.4.1 runs it:
  `T = γ·t_d + t_t·(1 + β·γ) + o`
- Pipelined bound (draft of the next iteration overlaps the current verify;
  **not implemented upstream**): `T = max(γ·t_d, t_t·(1 + β·γ)) + o`
- Speedup `S = E·t_t / T`. GenAI's reported rate is `(E-1)/γ`, not α.
  `alpha_from_rate` inverts it.
- PLD: an n-gram hit proposes γ tokens with `t_d ≈ 0`. A miss is a plain
  step. `S = [h·E(α_hit) + (1-h)] / [h·(1+βγ+o/t_t) + (1-h)]`.

The tests check `E` and the rate against a Monte Carlo of the accept loop,
check the Leviathan closed form, and check the break-even and PLD edge cases.
Two mutants (wrong exponent; serial treated as pipelined) each fail the suite.

Example only: parameters are illustrative (`t_t = 40 ms`, `β = 0.05`,
`o = 1 ms`, best γ in 1..16). Not measurements.

| c = t_d/t_t | break-even α (γ=5, serial) | S at α=0.6 serial / pipelined | S at α=0.8 serial / pipelined |
|---|---|---|---|
| 0.10 | 0.44 | 1.48 / 1.88 | 2.08 / 3.04 |
| 0.19 | 0.57 | 1.30 / 1.88 | 1.69 / 3.03 |
| 0.30 | 0.67 | 1.16 / 1.88 | 1.42 / 2.74 |
| 0.50 | 0.81 | 1.02 / 1.74 | 1.15 / 2.17 |

Serial vs pipelined is the whole cross-engine question. Serial execution makes
the draft engine's speed a straight tax. The overlap is what a second engine
could buy, and it does not exist in this stack. Building it (PEARL
[arXiv:2408.11850](https://arxiv.org/abs/2408.11850), AHASD, Mirror-SD) means a
GenAI change, out of scope here.

PLD with the same illustrative parameters and `α_hit = 0.8`:
`h = 0.1 → 1.24`, `0.3 → 1.67`, `0.5 → 2.06`. With bad hits (`α_hit = 0.1`)
it loses a little: `h = 0.8 → 0.89`.

## Pre-registered hypotheses and acceptance criteria

Fixed before any measurement. "Decode tok/s" is `decode_tokens_per_s` from
`bench/llm_load.py` (claude/report-bench): median over ≥ 20 requests,
concurrency 1, `--max-tokens 256`, `--temperature 0`, one warm-up, same
prompts for every arm.

**Tuned baseline B\*** = target-only Qwen3-4B-int4, the faster of
(a) `CPU` on the P-core balloon with `K3D_GPU_AGENT_CPUS` swept over {8, 12, 16},
(b) `GPU` (UHD 770). Same OVMS image digest, prefix caching off in both arms,
model cache warm. Greedy, because Fast Draft forces it.

| ID | Hypothesis | Predicted | Accept if | Falsified if |
|---|---|---|---|---|
| H1 | Fast Draft (0.6B on P-cores, 4B on P-cores) beats B\* on chat prompts | `S_model` from the measured `t_t`, `t_d`, α (P2, P3) | measured S ≥ 1.15 **and** within ±15 % of `S_model` | S < 1.0, or off the model by > 15 % (then the model is wrong; fix the model before using it) |
| H2 | Greedy spec output equals greedy target-only output | lossless | ≥ 95 % of requests token-identical | < 95 %. Report the first divergence position. Batched verify numerics may diverge from one-token decode. |
| H3 | E-core draft is dominated by P-core draft | `t_d(E) > t_d(P)` | `t_d(E)/t_d(P) ≥ 1.0` | `t_d(E) < t_d(P)`: then build the E-core path through GenAI directly |
| H4 | iGPU draft beats P-core draft | only if `t_d(iGPU) < t_d(P)`; prior: no | measured S(iGPU draft) > S(P draft) by ≥ 5 % | otherwise drop `draft_device=GPU` from the option |
| H5 | PLD beats B\* on copy-heavy agent prompts (tool output / file quoting) | S ≈ 1.3-2.0 | S ≥ 1.2, sampling kept (temperature 0.6 run completes) | S < 1.05 on copy-heavy prompts |
| H6 | PLD costs < 3 % on non-copy chat prompts | S ≥ 0.97 | S ≥ 0.97 | S < 0.97: gate PLD per workload |
| H7 | Power does not bind at 117 W PL1 | package < 117 W during decode | RAPL mean < 100 W, no `core_throttle_count` increase | either fails: all speed numbers carry a power confound |

Quality guard for any shipped default: on the same prompts, the agent-task
pass rate with the option on is ≥ the baseline pass rate minus 2 points, and
the repetition rate (any 32-token span repeated ≥ 3 times) does not rise.
Greedy Fast Draft is expected to fail this for Qwen3 thinking mode, per the
model card. That is why `draft` stays opt-in.

## The agent option (design against claude/agent-stack)

New environment variables, read by `agent_manifest` in `scripts/k3d-gpu`:

| Variable | Values | Default |
|---|---|---|
| `K3D_GPU_AGENT_SPEC` | `off` \| `lookup` \| `draft` | `off` |
| `K3D_GPU_AGENT_DRAFT_MODEL` | HF OpenVINO IR repo | `OpenVINO/Qwen3-0.6B-int4-ov` |
| `K3D_GPU_AGENT_DRAFT_DEVICE` | `auto` \| `CPU` \| `GPU` | `auto` (= target device) |
| `K3D_GPU_AGENT_ASSIST_TOKENS` | integer ≥ 1 | unset (OVMS default 5) |

Extra pull-container args per mode (all flags verified above):

```text
lookup:  --plugin_config={"prompt_lookup": true}
         --dynamic_split_fuse=false
         --max_num_batched_tokens=<model max context, 40960 for Qwen3-4B>
draft:   --draft_source_model=${K3D_GPU_AGENT_DRAFT_MODEL}
         [--draft_device=GPU]          # only when DRAFT_DEVICE=GPU
```

Rules:

- `lookup` and `draft` are exclusive (GenAI: "The `draft_model` and
  `prompt_lookup` constructor options are mutually exclusive").
- `lookup` with device `NPU` is refused (stateful pipeline, no PLD).
- `draft` prints a warning that OVMS forces greedy decoding.
- `draft` with `DRAFT_DEVICE=GPU` on a `CPU` target: the pod keeps the P-core
  label and CPU count of a CPU agent, and also gets `gpu.intel.com/xe: 1` (or
  `i915`) plus the render-node group from `agent_device_gid GPU`. Today
  `agent_cpus` gives non-CPU targets 2 CPUs. This split must not take that
  branch, because the target is still on CPU.
- `K3D_GPU_AGENT_ASSIST_TOKENS` is written into the target's
  `generation_config.json` as `num_assistant_tokens` by the pull container,
  so clients need no extra request fields. The write merges one key. The file
  also holds Qwen3's sampling defaults (`do_sample: true`, `temperature: 0.6`,
  `top_k: 20`, `top_p: 0.95`), and those must survive.
- The device annotation becomes `k3d-gpu.io/agent-device: CPU+draft:GPU` so
  `agent status` shows the split.
- `tests/detect_test.sh` gets manifest cases for each mode: flags present,
  GPU resource present only for the iGPU split, refusal paths exit non-zero.

Not implemented on this branch. The agent code lives only on
claude/agent-stack, so the patch belongs on top of that branch once it lands.

## Live probes

Run only after the live benchmarks finish. One sandbox, sequential arms, tear
down after. `$BASE` = `http://127.0.0.1:8000/v3` through
`kubectl -n k3d-gpu-agent port-forward svc/ovms 8000:8000`.

- **P0 (loads).** Each mode starts. `kubectl -n k3d-gpu-agent logs deploy/ovms`
  shows `Draft model strategy: Fast Draft` for `draft`. For `lookup`, a request
  with `max_ngram_size: 3` returns 200.
- **P1 (baseline B\*).** For `dev` in CPU, GPU and `cpus` in 8, 12, 16 (CPU only):
  `K3D_GPU_AGENT_SPEC=off K3D_GPU_AGENT_DEVICE=$dev K3D_GPU_AGENT_CPUS=$cpus k3d-gpu agent up`, then
  `python3 bench/llm_load.py --base-url $BASE --model Qwen3-4B-int4-ov --max-tokens 256 --temperature 0 --ignore-eos --requests 20 --warmup 1`.
  B\* = the best median `decode_tokens_per_s`. `t_t = 1000 / that`.
- **P2 (draft engine latency).** The same harness with
  `K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-0.6B-int4-ov` on CPU (P-cores) and on GPU
  gives `t_d(P)` and `t_d(iGPU)`. For `t_d(E)`, the same pod is pinned with
  `k3d-gpu.io/cores=efficient` (needs an override knob in the agent).
- **P3 (acceptance α).** An offline GenAI run in a P-core pod
  (`pip install openvino-genai==2026.4.1.0`). Same prompts, greedy,
  `num_assistant_tokens` in {3, 5, 8}:
  `r = pipe.generate([p], cfg).extended_perf_metrics.get_draft_acceptance_rate()`.
  Convert with `alpha_from_rate`. The run also gives `t_v(γ)` → β.
- **P4 (Fast Draft end to end).** `K3D_GPU_AGENT_SPEC=draft` with
  `DRAFT_DEVICE` CPU, then GPU. Same llm_load command as P1. Compare against
  `python3 research/spec-decoding/specdec_model.py --t-target $t_t --t-draft $t_d --rate $r --gamma 5`.
  H1, H4.
- **P5 (power).** `bench/energy.sh` around P1 and P4. Also
  `/sys/devices/system/cpu/cpu*/thermal_throttle/{core,package}_throttle_count`
  before and after. H7.
- **P6 (identity).** Stream off, temperature 0, same 50 prompts with spec off
  and on. Diff the `content`. H2.
- **P7 (PLD).** Two prompt sets: copy-heavy (a 2-4 k token file pasted, then
  "return it with function X renamed") and plain chat. `K3D_GPU_AGENT_SPEC=lookup`
  against B\* at temperature 0, plus one temperature-0.6 run to confirm
  sampling is honoured. H5, H6.
- **P8 (quality).** The agent task set with each shipped default on and off.
  Pass rate and repetition rate.

## Provenance

| Mechanism | Status | Evidence |
|---|---|---|
| Draft/verify speculative decoding and the `E` formula | known | Leviathan et al. 2023 (arXiv:2211.17192); Chen et al. 2023 (arXiv:2302.01318) |
| Prompt-lookup decoding | known | apoorvumang/prompt-lookup-decoding, cited by OVMS reference.md:331 |
| Sample-and-compare is exact for a point-mass draft | known (Leviathan's accept rule specialised to `q = δ`) | GenAI sampler.cpp:1404-1420 implements it |
| Draft on GPU, target on CPU | known | Dovetail, arXiv:2412.18934 |
| Speculative decoding across heterogeneous units on unified memory | known | Ghidorah, arXiv:2505.23219; compiler-assisted SD on CPU+Mali, arXiv:2602.08060 |
| Parallel (overlapped) draft and verify across devices | known | PEARL arXiv:2408.11850; Mirror-SD arXiv:2510.13161; AHASD arXiv:2604.25326 |
| Serial-step finding for GenAI 2026.4.1, so cross-engine = `t_d` only | read from source, not measured | fast_draft_strategy.cpp:197-232 |
| k3d-gpu option wiring (balloon P-core target + iGPU draft in one pod) | engineering, no novelty claimed | — |
| P-core vs E-core draft under hybrid balloons | no prior art found | arXiv API `abs:"speculative decoding" AND abs:heterogeneous` (30 hits, 2026-10-16) and SearXNG queries for "speculative decoding hybrid CPU efficiency cores draft": no paper on P/E-core draft placement. The search is shallow; treat this as "not found", not "novel". |

## Risks and unknowns

- GenAI draft properties are inherited from the target. A CPU-only key in the
  target's `plugin_config` could then break a GPU draft. The agent sets no
  `plugin_config` today; `lookup` only adds `prompt_lookup`.
- Fast Draft with `num_assistant_tokens: 0` is an error. Clients cannot turn
  drafting off per request.
- OVMS exposes no acceptance or draft counters. Production monitoring has only
  tok/s, so a falling acceptance rate is invisible until P3 is re-run.
- At concurrency > 1 the verify batch grows and decode may stop being
  bandwidth-bound (β rises). All criteria here are for concurrency 1, the
  agent's case.
- The iGPU and the CPU share DRAM (and, by Intel's client design, the LLC;
  not checked on this part). Serial execution keeps draft and target from
  contending with each other. The live benchmarks
  running on the host will contend with every arm, so probes wait for them.
