# Agent profile: one agent workload split across engines

`agent/` holds three tools, all Python 3 standard library:

| file | what it does |
|------|--------------|
| `agent_profile.py` | reads `k3d-gpu detect`, prints which engine each agent role runs on and the pod label / resource limit that puts it there |
| `sim.py` | discrete-event simulation of the agent loop on one package, all topologies, tuned |
| `regimes.py` | sweeps `sim.py` over the pre-registered regimes and the priors that decide the verdict |
| `params.json` | service-time, power and bandwidth priors (every service time UNMEASURED) |
| `PREREGISTRATION.md` | acceptance criteria, committed before the simulator existed |

```sh
scripts/k3d-gpu detect | python3 agent/agent_profile.py            # auto topology
python3 agent/agent_profile.py --topology CPU_SPLIT detect.txt
python3 agent/sim.py --agents 1 4 --set package.pl1_w=35            # any params.json key
python3 agent/regimes.py                                            # ~3 min
python3 -m unittest discover -s agent -p 'test_*.py'
```

## Roles and topologies

| role | BASE | CPU_SPLIT | SPLIT_P | SPLIT_G |
|------|------|-----------|---------|---------|
| LLM prefill + decode | P | P | P | iGPU |
| embeddings, rerank | P | E | NPU, else iGPU | NPU, else P |
| vector DB, gateway, tool sandbox | P | E | E | E |
| media decode | P (software) | E (software) | iGPU vcs | iGPU vcs |

`agent_profile.py --topology auto` picks the most split topology the host
supports: `SPLIT_P` with a hybrid CPU and an Intel GPU or NPU, `CPU_SPLIT` with
only a hybrid CPU, else `BASE`. Per role it emits:

- `labels`: `k3d-gpu.io/cores=performance` for roles on P-cores,
  `efficient` for everything else. Accelerator roles go to E-cores too, so their
  host threads (submission, tokenizing) stay off the P-cores. The NRI balloons
  policy that `k3d-gpu up` installs on hybrid hosts acts on this label.
- `limits`: `gpu.intel.com/i915` (or `/xe`, whatever detect reports) for iGPU
  and vcs roles, `npu.intel.com/accel` for NPU roles. The GPU plugin exposes the
  whole GPU; the video engines are not a separate resource, so the media pod
  holds the same resource as the embedding pod.
- `requires`: the intel-gpu-plugin flag `-shared-dev-num=<n>` when more than one
  role holds the iGPU. The plugin defaults to 1 (one container per GPU;
  `cmd/gpu_plugin/gpu_plugin.go` at v0.37.1), and `share/intel-gpu-plugin.yml`
  passes no flag, so on this host `SPLIT_P` needs `-shared-dev-num=3` before its
  three iGPU pods can schedule. This branch does not change the plugin manifest.

## Simulation model (`sim.py`)

Closed loop, N agents, zero think time. One turn: gateway, embed query, vector
search, rerank 20 docs, LLM prefill (800 new tokens, prefix-cached history),
decode 120 tokens ending in a tool call, gateway, tool run, media decode (30% of
turns), embed 4 chunks of tool output into the vector DB. Each stage's size is
lognormal (sigma 0.35, mean 1).

Fluid rates, recomputed at every job completion:

1. Engine share. CFS-like: an entity asking for `cap` threads gets
   `cap * min(1, capacity / sum of caps)`. The LLM server is one entity of
   `llm_threads`; a squeezed multi-thread entity runs at `share^(1+spin_penalty)`
   (busy-waiting barrier threads burn their share). iGPU and NPU are time-sliced
   between the processes using them; vcs0/vcs1 serve two jobs at once.
2. Package power. P, E and iGPU draw `watts_per_unit * phi^3` per busy unit; vcs
   and NPU draw a fixed amount. Above PL1 one common `phi` is solved exactly and
   scales every DVFS engine. Steady state only: PL2 / tau are not modelled (on this
   host PL1 = PL2 = 117 W, so the turbo window does not exist).
3. DRAM bandwidth. Each job wants `rate * bytes_per_unit`; over `dram_gbps` the
   bandwidth is water-filled max-min fair. Decodes in one LLM server share one
   weight read per step (continuous batching).

Tuning: every topology gets the same grid, LLM threads {2,4,6,8} x side-stage
thread cap {1,2,4,8}, best mean throughput over 3 seeds. Raising only the LLM
threads (the first version) left BASE ~8% weaker than its best; the side-stage
cap closes that.

Not modelled: LLC and ring contention between iGPU and cores, SMT, per-domain
power arbitration (pcode gives P, E and iGPU separate frequencies; the model uses
one `phi`), thermals, the NVIDIA GPUs.

## Results on the reference priors (prediction, not measurement)

`python3 agent/regimes.py --turns 200`, gain = throughput / tuned BASE:

| cell | best split | SPLIT_P | verdict |
|------|-----------|---------|---------|
| S1 N=4, PL1 117 W | CPU_SPLIT 1.35x, p95 0.78x | 1.31x, p95 0.81x | PASS (>= 1.20x, p95 not worse) |
| S2a N=1 | CPU_SPLIT 0.86x | 0.80x | confirmed: splitting loses 14-20% with no co-runners |
| S2b N=4, PL1 35 W | SPLIT_G 1.59x | 1.26x | prediction FALSIFIED: the split wins more under the tight limit |
| S2c N=4, DRAM 30 GB/s | CPU_SPLIT 1.36x | 1.33x | prediction FALSIFIED at the reference workload |
| S3 invariants | 20/20 unit tests | | PASS |

Run-to-run spread between `--turns 200` and `--turns 300` is about 2%.

What the model says, mechanism by mechanism:

- The gain is P-core relief. At N >= 2 BASE runs P-cores at 100% and its
  throughput stays flat with N; moving rerank, embedding, tools and media off
  them frees cores for prefill, which dominates the turn (6 GFLOP x 800 tokens).
- With one agent nothing runs alongside the LLM, so the split only moves
  stages to slower engines: E-cores at 0.55x a P-core, rerank on UHD 770 at
  0.24 s/doc against 0.09 s/doc on 8 P-cores. Turns get 14-20% slower. Split
  only when agents run concurrently.
- S2b was wrong because moving work off the P-cores cuts package power per
  unit of work (E-cores and the iGPU do the same stage for fewer watts), so the
  shared limit throttles the split about as hard as BASE (mean phi 0.65 vs 0.67)
  and `SPLIT_G`, with the LLM on a 12 W iGPU, wins most. Doubling iGPU power to
  25 W moves `SPLIT_P` from 1.26x to 1.24x.
- S2c was wrong because the reference turn is prefill-bound: it moves 13-20 GB/s,
  so 30 GB/s does not bind. Bandwidth does erase the split when decode dominates:
  7B-class weights (4.4 GB), 150 prefill / 600 decode tokens, N = 4 gives 1.00x at
  20 GB/s and 1.04x at 30 GB/s, rising to 1.22x at 55 and 1.41x at 89.6.
- Under these priors the iGPU adds nothing over the E-cores: 16 E-cores rerank
  faster than UHD 770, so `CPU_SPLIT` >= `SPLIT_P`. The break-even lies between
  0.12 and 0.24 s/doc on the iGPU; at 0.5 s/doc `SPLIT_P` falls to 1.02x.

Priors that move the verdict, `SPLIT_P` at N = 4 (others barely move it):

| prior | values -> gain |
|-------|----------------|
| iGPU rerank s/doc | 0.12 -> 1.38x, 0.24 -> 1.31x, 0.5 -> 1.02x, 1.0 -> 0.59x |
| spin_penalty | 0 -> 1.19x (S1 fails), 0.5 -> 1.31x, 1.0 -> 1.37x |
| e_core_perf | 0.35 -> 1.28x, 0.75 -> 1.31x |

## Provenance

| mechanism | status |
|-----------|--------|
| Splitting an agent / agentic-RAG workload across CPU, iGPU and NPU of one SoC, with DRAM contention | known: Agent.xpu (arXiv 2506.24045), HeRo (arXiv 2603.01661), HeteroInfer (arXiv 2501.14794) |
| Shared package power budget between CPU cores and the integrated GPU; f*V^2 dynamic power | known (RAPL power planes, DVFS power models) |
| Pinning LLM threads to P-cores on hybrid CPUs | known (common llama.cpp practice); k3d-gpu `main` already does it through nri-plugins balloons |
| Rendering the per-role placement as Kubernetes pod labels and extended-resource limits from `k3d-gpu detect`, including the `-shared-dev-num` the topology needs | no prior art found. Search log (2026-10-16, arXiv API): `abs:kubernetes AND abs:agentic AND abs:heterogeneous` (6 hits, all cluster schedulers or k8s ops agents), `abs:kubernetes AND abs:"hybrid CPU"` (1 hit, stream-processing scaling), `abs:kubernetes AND abs:NPU AND abs:iGPU` (0 hits), `abs:agentic AND abs:heterogeneous AND abs:SoC` (finds Agent.xpu, HeRo, TraceDSE 2610.07191: none emit k8s placement). Web search engine returned no relevant results. |
| One fluid model coupling CPU time-share, package power cap and DRAM bandwidth to predict where the split wins | unsearched as a combination; each part is known |

## Host facts used (verified 2026-10-16)

- `/sys/class/powercap/intel-rapl:0`: `constraint_0` long_term 117 W (window
  56 s), `constraint_1` short_term 117 W, `constraint_2` peak_power 230 W,
  `constraint_0_max_power_uw` 35 W. Firmware runs the 35 W T-series part at
  117 W PL1, above Intel's 106 W Maximum Turbo Power (ARK, i9-13900T).
- ARK: 8 P + 16 E cores, DDR5-5600 / DDR4-3200, 2 channels, 89.6 GB/s max, UHD
  770 with 32 EU at up to 1.65 GHz, 2 multi-format codec engines.
- `/sys/class/drm/card0/engine`: rcs0, bcs0, vcs0, vcs1, vecs0 (i915, no ccs).
  No `/sys/class/accel`: no NPU on Raptor Lake.
- `/sys/devices/cpu_core/cpus` 0-15, `cpu_atom/cpus` 16-31. 128 GB RAM; DIMM
  speed is root-only, so `dram_gbps` = 55 is a prior.

## Live probes (not run: live benchmarks occupy the host)

Fill `params.json` from these, re-run `regimes.py`, then judge L1-L3.

1. Rerank and embedding per engine (the prior that flips the verdict): one
   OVMS rerank/embeddings servable (claude/agent-stack) per device, `CPU` pinned
   to P (`k3d-gpu.io/cores=performance`), `CPU` pinned to E, `GPU`; 20 docs x 300
   tokens, 50 requests each, median s/doc.
2. LLM prefill and decode per engine, 3B Q4-class model:
   `bench/llm_load.py --base-url http://127.0.0.1:8000/v3 --model <servable>
   --max-tokens 120 --ignore-eos --requests 20 --concurrency 1,2,4 --out llm-<dev>.json`
   with a ~800-token prompt (`--prompt`), on P-cores and on the iGPU.
   `ttft_s / 800` -> prefill s/token, `1 / decode_tokens_per_s` -> decode.
3. spin_penalty: the same P-core LLM run with
   `bench/noisy.sh start --cores performance --workers 4` alongside; the
   decode-rate drop against the CFS share predicts the exponent.
4. Power per engine: `bench/energy.sh --interval 1 --json e-<role>.json -- <role load>`
   for idle (base_w), P-only, E-only, iGPU-only loads.
5. DRAM bandwidth: STREAM triad, 16 threads, `dram_gbps` = measured triad.
6. Turn-level acceptance L1/L2 needs an agent-turn driver (gateway -> embed ->
   search -> rerank -> LLM -> tool -> ingest) over the rendered pods. It does not
   exist yet; `llm_load.py` measures single LLM calls only.
