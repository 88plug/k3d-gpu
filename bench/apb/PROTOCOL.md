# APB baseline: agents per box at a p95 turn-latency SLO

Status: **pre-registered, not run.** Nothing in this file is a measurement of
agents per box. Run it serially on the i9-13900T host after the live benchmark
window closes (after ~17:00) and after H3/H4 finish, one arm at a time.

## 1. Question and headline metric

How many concurrent tool-using agents can one Intel client box serve before
the agents' LLM turns get slow? No published number exists for Intel client
hardware (see `docs/benchmark-landscape.md` on `claude/bench-landscape`: no
third-party agent-serving run on a desktop, laptop, iGPU or k3s was found).

* **Agent**: one closed loop. It sends a turn, waits for the full streamed
  reply, waits the trace's tool time, sends the next turn, and starts its next
  session when one ends. N agents = `agent_trace.py --max-concurrency N` with
  every session's `start_s = 0`.
* **Turn latency**: `e2e_s` of one LLM request, from send to end of stream. An
  error or a timeout (120 s) counts as +inf.
* **SLO (headline)**: p95 turn latency <= **10 s**.
* **APB**: the largest N on the ramp such that every step <= N passes (section 5).
  APB = 0 when N = 1 already fails.
* Secondary, from the same data, no extra runs: APB at p95 <= 5, 15, 20 and
  30 s; p50/p99; TTFT p95; tool-call validity; window output tok/s; J per turn
  and J per output token (gross and net of idle).

## 2. What is known before the run

From the H2 campaign (Qwen3-4B int4, OVMS 2026.4.1, this host). Reported by
that campaign, not re-measured here:

| Engine | decode c=1 | aggregate c=4 | prefill | TTFT |
|---|---|---|---|---|
| OVMS CPU, P-cores | 15.7 tok/s | 56 tok/s (14.4/stream) | ~172 tok/s | 0.25 s |
| OVMS iGPU (UHD 770) | 9.9 tok/s | 30 tok/s | 66 tok/s | - |
| EAT default vLLM-CPU, bf16 | 4.6 tok/s | not measured | 15 tok/s | - |

Not measured: concurrency above 4, agents per box, real agent traces, the
NVIDIA arms.

Host facts read in this session (verified, read-only):

* `nvidia-smi`: GPU 0 = NVIDIA RTX 4000 SFF Ada Generation, 19195 MiB, 70 W
  limit; GPU 1 = NVIDIA RTX 2000E Ada Generation, 15356 MiB, 50 W; driver
  590.48.01.
* RAPL: `intel-rapl:0` = package-0 (`:0:0` core, `:0:1` uncore), `intel-rapl:1`
  = psys; `max_energy_range_uj` = 262143328850 on all; `energy_uj` is root-only
  (mode 0400).
* `nvidia-smi --help-query-gpu` on this driver lists `power.draw` ("average
  power draw over 1 sec" on Ampere or newer, "+/- 5 watts") and no
  `total_energy_consumption`, so GPU energy is integrated from `power.draw`.
* DIMM speed: **not read** here (needs root). `run.sh facts` records
  `sudo -n dmidecode -t memory` into every run directory; run `sudo -v` first.

## 3. Arms

Same model family (Qwen3-4B), same traces, same client, same access path
(`kubectl port-forward` to the serving pod) for all four arms. Thinking is off
on every request: `chat_template_kwargs: {"enable_thinking": false}` is merged
into every body (OVMS documents the field in `docs/model_server_rest_api_chat.md`;
vLLM v0.31.0 declares it at `vllm/entrypoints/openai/chat_completion/protocol.py:359`).

| Arm | Engine | Model | Precision |
|---|---|---|---|
| A1 `ovms-cpu` | OVMS 2026.4.1, `--target_device=CPU`, k3d-gpu P-core balloon | `OpenVINO/Qwen3-4B-int4-ov` | int4 weights |
| A2 `ovms-igpu` | OVMS 2026.4.1, `--target_device=GPU` (UHD 770) | same | int4 weights |
| A3 `eat-vllm-cpu` | EAT's vLLM CPU chart, catalog `cpu-qwen3-4b`, k3d-gpu values | `Qwen/Qwen3-4B` | bf16 |
| A4 `vllm-cuda-rtx4000` | vLLM v0.31.0 CUDA on the RTX 4000 SFF Ada via k3d-gpu's NVIDIA path | `Qwen/Qwen3-4B-AWQ` @ `74d4bd2b` | AWQ 4-bit (group 128) |

### NVIDIA engine choice (verified)

**vLLM CUDA, not OVMS.** OpenVINO's README lists CPU, GPU (Intel) and NPU
devices; the OVMS image k3d-gpu uses is the `-gpu` build with "CPU, GPU and NPU
plugins" (comment in `scripts/k3d-gpu`, `claude/agent-stack`). An NVIDIA plugin
exists only as a source module, `openvino_contrib/modules/nvidia_plugin`; it is
not in any OVMS image, and whether it supports OVMS continuous batching was not
checked. Confidence that OVMS cannot serve this arm out of the box: 0.9.

The comparable int4 checkpoint exists: `Qwen/Qwen3-4B-AWQ`, revision
`74d4bd2bd4bff9cafc9345221320bffb08b406a3`, `quantization_config` = AWQ, 4 bits,
group 128, `max_position_embeddings` 40960 (Hugging Face API + config.json,
read 2026-10-16). It is the vendor's own quantization; OVMS's int4 is OpenVINO
NNCF int4. Both are 4-bit weights with 16-bit activations; they are not the
same numerics.

Image: `vllm/vllm-openai:v0.31.0`, amd64 digest
`sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b`
(Docker Hub API, tag updated 2026-10-04; v0.31.0 is the latest vLLM on PyPI as
read 2026-10-16). Flags used, all checked in v0.31.0 source:
`--enable-auto-tool-choice`, `--tool-call-parser`, `--enable-prompt-tokens-details`
(`vllm/entrypoints/launchers/cli_args.py:113,119,145`), `--max-model-len`,
`--gpu-memory-utilization`, `--max-num-seqs`, `--revision`,
`--served-model-name` (`vllm/engine/arg_utils.py`). Prefix caching is left at
its default.

### Bring-up (one arm at a time; tear the previous one down first)

Variables, set once:

```sh
REPO=/home/andrew/k3d-gpu
K3DGPU=$REPO/scripts/k3d-gpu          # from claude/agent-stack @ 8385dc2, or the exact build H2 used (record it)
VLLM_IMAGE=vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b
sudo -v                               # RAPL reader and dmidecode
```

* **A1** (hybrid cluster, the H2 CPU configuration):
  `K3D_GPU_AGENT_DEVICE=CPU $K3DGPU agent up`, then
  `kubectl -n k3d-gpu-agent port-forward svc/ovms 8000:8000 &`.
  URL `http://127.0.0.1:8000/v3/chat/completions`, model `Qwen3-4B-int4-ov`.
  If H2's tuned settings differ from `agent up` defaults (threads, streams,
  `cache_size`), use H2's and record the pod's `graph.pbtxt` and
  `Cpus_allowed_list` in the run directory. OVMS defaults (v2026.4.1
  `docs/parameters.md`): `--enable_prefix_caching` true, `--max_num_seqs` 256,
  `--cache_size` 0 (dynamic).
* **A2**: `K3D_GPU_AGENT_DEVICE=GPU $K3DGPU agent up` on a cluster that
  advertises `gpu.intel.com/i915`; same port-forward, URL and model.
* **A3**: `integrations/eat` from `claude/eat-on-k3d`:
  `EAT_WORK=$PWD/eat-work integrations/eat/eat-up.sh model cpu-qwen3-4b`. Do not
  change EAT's defaults beyond what that integration already sets
  (`vllm-values.yaml`: memory 16Gi, P-core balloon, `cpu: "8"`;
  `EAT_VLLM_KV_GIB` default 6). Reach vLLM's own Service directly, not LiteLLM:
  `kubectl get svc -A | grep -i vllm`, port-forward it to 8200, URL
  `http://127.0.0.1:8200/v1/chat/completions`, model `Qwen/Qwen3-4B`. Record
  the vLLM version and args from the pod spec.
* **A4** (cluster created by k3d-gpu with NVIDIA active: `--gpus=all` plus
  `--default-runtime=nvidia`):

  ```sh
  GPU_UUID=$(nvidia-smi --query-gpu=name,uuid --format=csv,noheader | awk -F', ' '/RTX 4000/ {print $2}')
  sed -e "s|@VLLM_IMAGE@|$VLLM_IMAGE|" -e "s|@GPU_UUID@|$GPU_UUID|" \
      $REPO/bench/apb/vllm-cuda.yaml | kubectl apply -f -
  kubectl -n apb-vllm rollout status deploy/vllm --timeout=1800s
  kubectl -n apb-vllm exec deploy/vllm -- nvidia-smi -L   # must list both; vLLM uses only $GPU_UUID
  kubectl -n apb-vllm port-forward svc/vllm 8100:8000 &
  ```

  URL `http://127.0.0.1:8100/v1/chat/completions`, model `Qwen/Qwen3-4B-AWQ`.
  The pod requests both GPUs so nothing else is scheduled on them; vLLM sees
  only the RTX 4000 through `CUDA_VISIBLE_DEVICES`. A k3d-gpu cluster that
  cannot start this pod (an earlier probe found CDI registered but no nvidia
  runtime) means arm A4 is not deliverable through k3d-gpu yet: record that
  outcome, do not fall back to host Docker for the headline.

## 4. Workload

`bench/agent_trace.py` + `bench/agent_trace_gen.py`, byte-identical to
`5a1e541` on `claude/invent-agent-trace-bench` (copied into this branch
unchanged). `bench/apb/apb.py trace` builds each step's trace:

* Family **support** (tau-bench-shaped): 2409-word system prompt shared by
  every session, 8 tools, 3-6 turns per session, tool results 40-120 words,
  `max_tokens` 96, tool wait (`think_s`) 1.0 s, prompts ~2.8k words (~3.7k
  tokens) by the last turn. History is teacher-forced, so every arm receives the
  same prompts.
* Sessions per step: `max(10, 5 N)`, all `start_s = 0`, run by N agent slots.
* Seed `1000 N + rep`. The same seed set is used for every arm. Each seed gives
  a different system prompt, so no rep reuses another rep's prefix cache.
* Temperature 0, `tool_choice: auto`, tools sent on every turn, streaming with
  `stream_options.include_usage`.
* Secondary family **coding** (8-12 turns, ~9.4k words p50 at the last turn) is
  run only for an arm whose support APB >= 1, with `FAMILY=coding`.

## 5. Procedure

```sh
cd $REPO
python3 bench/apb/test_apb.py && python3 bench/test_agent_trace.py   # must pass first
bench/apb/run.sh ramp ovms-cpu          http://127.0.0.1:8000/v3/chat/completions Qwen3-4B-int4-ov
# tear down, bring up A2, then:
bench/apb/run.sh ramp ovms-igpu         http://127.0.0.1:8000/v3/chat/completions Qwen3-4B-int4-ov
bench/apb/run.sh ramp eat-vllm-cpu      http://127.0.0.1:8200/v1/chat/completions Qwen/Qwen3-4B
bench/apb/run.sh ramp vllm-cuda-rtx4000 http://127.0.0.1:8100/v1/chat/completions Qwen/Qwen3-4B-AWQ
```

Per arm, `run.sh ramp` does:

1. `facts.txt`: lscpu, DIMMs, swap, load, running container count, governor /
   EPP / no_turbo, RAPL limits, GPUs, pods, git revision. `config.json`: every
   parameter below.
2. Energy samplers for the whole ramp: RAPL package + psys every 1 s
   (`rapl.log`), and `nvidia-smi power.draw` for both GPUs every 500 ms
   (`nvidia.log`).
3. 60 s idle window with the server up and no load (`idle.json`): idle power.
4. One warm-up rep at N = 1 (discarded, `warmup/`).
5. Ramp N = 1, 2, 4, 8, 12, 16, 24, 32, **3 reps per step** (`steps/`). Steps 1,
   2 and 4 always run. After them, the ramp stops after the first step whose
   pooled p95 exceeds `STOP_SLO` = 30 s or whose error rate exceeds 1%. A rep
   whose own window p95 exceeds 60 s (2 x `STOP_SLO`) ends that step early;
   the step is scored on the reps it has.
6. Drift check: one more N = 1 rep (`drift/`).
7. `report.json` and `report.md`.

Host conditions: nothing else benchmarked on the host during a ramp; the 52
production containers stay as they are (recorded, not stopped), so idle power
is subtracted rather than avoided.

## 6. Analysis rules (fixed before the run)

All in `bench/apb/apb.py`, unit-tested in `bench/apb/test_apb.py`.

* **Steady window, per rep**: starts at the start of the (N+1)-th session to
  start (drops the first cohort's synchronized opening burst); ends at the first
  end among the sessions still running when the last session starts (drops the
  ramp-down tail). A request counts when its send time is inside the window.
* **p95**: nearest-rank, pooled over the window requests of all reps of a step.
  95% CI from 2000 bootstrap resamples of the pooled requests (seed = N).
  Per-rep p95 is reported next to it.
* **A step passes** when: pooled window n >= 60; p95 <= SLO; error rate <= 1%;
  tool-call validity (`tool_valid_any` on turns that expect a tool) is no more
  than 10 points below the same arm's N = 1 step. A step with n < 60 and p95
  <= SLO is "insufficient" and ends the monotone APB count.
* **APB** = largest N with every step <= N passing. **Interpolated APB**:
  linear in p95 between the last passing and the first failing step.
* **Energy per step**: RAPL package joules over each rep's `[epoch_start,
  epoch_end]` (wraps counted), plus each GPU's integrated `power.draw`; summed
  over reps; divided by all turns and all output tokens of those reps. Net =
  gross - idle W x seconds. RAPL package includes the iGPU; it excludes DRAM
  and the dGPUs.
* **Prefix cache**: `cached_fraction` from `usage.prompt_tokens_details`. EAT's
  vLLM may not run with `--enable-prompt-tokens-details`; then the field is
  null and per-turn TTFT is the only cache evidence.

## 7. Predictions (pre-registered)

`python3 bench/apb/apb.py predict` simulates the closed loop as processor
sharing over the H2 single-stream and c=4 rates. Each turn's demand at c=1 =
0.25 s + uncached prompt tokens / prefill rate + output tokens / decode rate;
the prefix cache is assumed to hold everything but the words appended since the
last turn. Output 40 tokens on tool turns, 96 on final turns, 1.3 tokens/word.
Two concurrency curves bracket the unknown: **hyper** extrapolates
`A(k) = A1 k / (1 + a(k-1))` fitted through c=4; **flat** freezes aggregate
throughput at its c=4 value. EAT's c=4 scaling was not measured; the CPU
arm's ratio is assumed.

| Arm | p95 at N=1 | APB @ 10 s (flat / hyper) | APB @ 20 s (flat / hyper) |
|---|---|---|---|
| A1 ovms-cpu | 7.2 s | **4 / 12** | 8 / 32 |
| A2 ovms-igpu | 12.1 s | **0 / 0** | 4 / 4 |
| A3 eat-vllm-cpu | 30.4 s | **0 / 0** | 0 / 0 |
| A4 vllm-cuda-rtx4000 | no anchor measured | no prediction | no prediction |

Read: at a 10 s SLO with 96-token final answers, a single iGPU agent already
misses (96 / 9.9 tok/s = 9.7 s of decode alone), and EAT's default misses at
N = 1 on prefill (15 tok/s). The CPU arm is predicted between 4 and 12
agents. Confidence that the measured A1 APB lands in [4, 12]: 0.6. That A2 and
A3 measure APB @ 10 s = 0: 0.8 and 0.9. A4 should beat A1 on APB @ 10 s
(discovery-level expectation from GPU bandwidth, no measured anchor): 0.75.

Falsifiers of the model, not the box: a measured N = 1 p95 more than 30% away
from the prediction for A1 or A2 means the demand model (output lengths,
prefill rates) is wrong; check `completion_tokens` in the JSONL before reading
anything into APB.

## 8. Validity checks (any failure is reported with the result)

1. Both unit suites pass on the host before the first ramp.
2. Every arm: N = 1 `tool_valid_any` >= 0.9 on the support trace. Lower means
   the tool parser is not set up (OVMS `hermes3`, vLLM `hermes`), and the arm's
   latencies are for a different output shape.
3. Median `completion_tokens` on tool turns within 2x across arms; otherwise
   the arms are generating different amounts, and APB compares output length,
   not serving.
4. Drift: end-of-ramp N = 1 p95 within +/-15% of the start; otherwise the host
   changed under the run (production containers, thermals, swap) and the arm
   is re-run.
5. Server-side concurrency: OVMS `--max_num_seqs` 256 and vLLM's defaults are
   above 32; a step that plateaus at a round number of in-flight requests is a
   server cap, not the box. EAT's KV cache is 6 GiB: Qwen3-4B bf16 needs 144
   KiB per token (36 layers x 8 KV heads x 128 x 2 x 2 bytes, from the model's
   config.json), so ~43.7k cached tokens. Check vLLM logs for preemption at
   high N.
6. Prefix cache active: A1, A2, A4 `cached_fraction` >= 0.5 on support at
   N = 1.

## 9. Threats to validity

* `kubectl port-forward` carries every stream. It is the same path for all arms,
  but it can add latency at 32 streams; its CPU use shows in `facts.txt`
  load, not separately.
* Precision differs: int4 (A1, A2), bf16 (A3, EAT's default), AWQ int4 (A4).
  A1 vs A3 is the "k3d-gpu tuned vs EAT out of the box" comparison, not an
  engine comparison at equal precision.
* Synthetic traces (random words) exercise the serving path, not task quality.
  Teacher forcing means a model that answers differently does not change the
  next prompt.
* The production containers on the host (~71 W idle package power reported
  by H2) add noise to both latency and energy. Net-of-idle energy assumes that
  background load is constant over the run.
* RAPL package power excludes DRAM; GPU `power.draw` is +/-5 W.
* p95 from pooled requests treats requests as independent; turns from one
  session are correlated. The per-rep p95 spread is reported for that reason.

## 10. Time budget (estimate from the simulation, not measured)

With the stop rule: A1 ~1 h, A2 ~1.5 h, A3 ~1 h (steps 1-4 at ~20 s per turn),
A4 under 1 h, plus model downloads and bring-up. Without a stop, the flat-curve
worst case is 2.5 h (A1), 5 h (A2), 11 h (A3).

## Files

* `bench/apb/run.sh`: facts, ramp, report.
* `bench/apb/apb.py`: trace builder, step runner, window/percentile/APB
  scoring, RAPL and nvidia-smi energy, prediction model.
* `bench/apb/test_apb.py`: 22 tests (stub server, fake powercap tree, end-to-end
  `run.sh ramp`), ~8 s. Mutation check: six hand-made bugs (extra body dropped,
  errors scored as 0 s, first cohort kept in the window, RAPL wrap ignored, no
  concurrency cap, floor instead of ceil in the percentile) each fail the suite.
* `bench/apb/vllm-cuda.yaml`: arm A4 Deployment + Service.
* `bench/agent_trace*.py`, `bench/AGENT_TRACE.md`: unchanged copy of `5a1e541`.

## Sources (read 2026-10-16)

* `bench/agent_trace.py`, `origin/claude/invent-agent-trace-bench` @ `5a1e541`.
* OVMS v2026.4.1 `docs/parameters.md` (prefix caching default true,
  `max_num_seqs` 256, `cache_size` 0); OVMS `docs/model_server_rest_api_chat.md`
  (`chat_template_kwargs`, `stream_options.include_usage`,
  `prompt_tokens_details.cached_tokens`).
* vLLM v0.31.0 source: `vllm/entrypoints/openai/chat_completion/protocol.py`,
  `vllm/entrypoints/launchers/cli_args.py`, `vllm/engine/arg_utils.py`.
* Docker Hub API, `vllm/vllm-openai` tag `v0.31.0`.
* Hugging Face: `Qwen/Qwen3-4B-AWQ` model API + config.json @ `74d4bd2b`;
  `Qwen/Qwen3-4B` config.json.
* `github.com/openvinotoolkit/openvino` README; `openvino_contrib/modules`
  listing.
* This repo: `scripts/k3d-gpu` on `origin/main` (NVIDIA cluster args) and
  `claude/agent-stack` (agent); `integrations/eat/` on `claude/eat-on-k3d`;
  `EXPERIMENTS.md` (H2-H4); `probes/sci_d7_box_capacity.sh` (k3d CUDA note).
