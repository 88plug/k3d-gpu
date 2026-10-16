# DRAM bandwidth as a schedulable UMA resource (prototype)

Status: **static prototype + simulation only.** Nothing here is wired into
`k3d-gpu up`. No cluster, server or benchmark was run to produce these results.

On client SoCs (Raptor Lake i9-13900T + UHD 770) the CPU cores and the iGPU share
one DDR5 controller. LLM decode on both is DRAM-bandwidth-bound. This prototype
treats DRAM GB/s as a node resource (`k3d-gpu.io/mem-bw-gbps`) and pairs it with a
cgroup-only runtime guard, because this part has no RDT/MBA (checked read-only:
no `rdt_a`/`mba` cpuinfo flags).

## Pre-registered acceptance criteria (written before any code)

Committed in its own commit, ahead of the implementation, so the criteria
cannot be fitted to the results. Sim criteria are judged on the simulator in
`sim.py`; live criteria (L*) are for the host probes listed below and are NOT
judged here.

Mechanism correctness (unit tests):
- U1. Bytes per decoded token = weight bytes + context_tokens x KV bytes per
  token (every decode step re-reads the whole KV cache, not one token's KV).
  Llama-3.1-8B fp16 KV = 2 x 32 layers x 8 kv-heads x 128 dim x 2 B = 131072 B/token.
- U2. The mutating-webhook handler adds `k3d-gpu.io/mem-bw-gbps` (integer GB/s,
  rounded up) to requests and limits from pod annotations, returns a valid
  JSONPatch AdmissionReview, and leaves pods without model annotations alone.
- U3. Node capacity = floor(0.85 x measured STREAM triad GB/s).
- U4. The guard reads PSI `memory.pressure` and `memory.stat` refaults from a
  cgroup tree and writes a `cpu.max` string ("<quota> <period>" or "max").

Simulation (S*), each with an ablation that must FAIL when the key element is removed:
- S1 Admission (greedy demand). With demand = the pod's unconstrained (greedy)
  draw, every pod admitted to the UMA node keeps p95 ITL within 15% of solo,
  while default co-location degrades at least one pod by >= 40%.
  Ablation S1a: demand = (bytes/token x declared target tok/s), the idea as
  originally written. Prediction: it admits the same set as default and does
  NOT protect (refuter objection: greedy decoders ignore the target).
- S2 Aggregate. On the UMA node alone (no dGPU steering), aggregate tok/s
  under greedy admission is within 5% of default co-location.
- S3 Guard. Bursty UHD 770 decode (duty cycle <= 0.5) next to a STREAM-like
  BestEffort sandbox: victim active-period throughput >= 90% of solo, with
  sandbox slowdown >= 25% lower than the best static cpu.max (oracle-tuned)
  that achieves the same 90%.
  Ablation S3a: duty cycle 1.0 -> the guard's advantage over static must fall
  below 25% (the win must come from temporal adaptivity, not from the sim).
  Ablation S3b: guard ranks sandboxes at random instead of by PSI/refaults ->
  must miss the 90% target or cost more sandbox throughput.
  Ablation S3c: sensor disabled (guard never sees ITL) -> victim < 90%.
- S4 Attribution. With DRAM contention removed (infinite bandwidth) and only
  the power limit active, naive co-location must NOT degrade the victim by
  >= 15% at PL1=117 W. If it does at PL1=35 W, that degradation is power, not
  bandwidth, and is reported separately.
- S5 Robustness. S1 and S3 are re-run over a perturbation grid (DRAM capacity,
  iGPU reach, arbitration weight, loaded-latency knee, cpu.max->bandwidth curve
  exponent, sensor noise). Report the fraction of the grid where each passes;
  do not report only the best point.
- Kill (sim). Report the region of the grid where naive co-location degrades
  the iGPU victim < 15%. If the plausible UHD 770 region (reach <= 25 GB/s) is
  entirely inside it, the idea is dead for this host before any live probe.

Live (not judged here):
- L1. UHD 770 OVMS decode p95 ITL solo vs next to a STREAM-like sandbox, PL1
  pinned (same value both runs). Kill if degradation < 15%.
- L2. Predicted bytes/token within +-20% of uncore IMC free-running traffic
  divided by tok/s, SOLO only (IMC counters are whole-socket; no per-pod
  attribution under co-run).

## Files

- `membw.py`: roofline demand, node capacity patch, mutating-webhook handler and
  TLS server, the AIMD `cpu.max` guard, cgroup v2 read/write helpers. Stdlib only.
- `manifests.yaml`: webhook Deployment/Service/MutatingWebhookConfiguration
  (`failurePolicy: Ignore`, opt-in label `k3d-gpu.io/mem-bw=auto`) plus an example
  annotated iGPU pod. Not applied by `k3d-gpu up`; `caBundle` is a placeholder.
- `sim.py`: fluid DRAM-contention simulator and the S1-S5 harness.
- `test_membw.py`: 27 unit tests (mechanism + simulator invariants).
- `sim_output.txt`, `s5_grid.tsv`: the full run reported below.
  `grid_breakdown.py` slices the grid by parameter.

```
cd prototypes/membw
python3 -m unittest -v test_membw
python3 sim.py            # full grid, ~15 min single core; --quick for 8 points
python3 grid_breakdown.py
```

### Node capacity

```
TRIAD=...   # GB/s from a solo STREAM triad run on the node
kubectl patch node "$NODE" --subresource=status --type=json \
  -p "$(python3 -c "import json,membw;print(json.dumps(membw.node_capacity_patch($TRIAD)))")"
```

## What changed after the criteria were registered

The criteria above were committed first and are unchanged. The mechanism was
revised once after the first sim run. Both versions are kept and both are reported.

- **Admission demand.** The idea priced a pod at bytes/token x *target* tok/s.
  The refuter is right that bandwidth-bound decoders ignore a target, so that
  reservation does not cap anything. The prototype prices each pod at its
  *greedy* draw, min(device reach, compute ceiling x bytes/token). That is what
  the pod takes when nothing caps it, so admitted pods need no runtime cap to
  stay inside the budget. The target-demand version is kept as ablation S1a.
- **Guard v1 -> v2.** v1 compared a 1 s window p95 to solo p95. At iGPU decode
  rates (2-6 tok/s) that window holds 2-6 tokens, and its p95 is biased high, so
  v1 throttled sandboxes with no contention to fix (iGPU reach 12, alpha 0:
  13% STREAM slowdown vs 4% unthrottled, victim already at 1.00).
  v2 changes three things. It compares the window's geometric-mean ITL to solo
  with a 2-standard-error floor. It only counts tokens produced after its last
  cut. It remembers the quota where the last burst ended and re-applies it the
  tick a new burst starts. The burst start comes from OVMS `ovms_current_requests`
  / `ovms_current_graphs`. Two v2 constants (active probe rate 1.02/tick,
  min 3 fresh tokens) were picked on the default point (s3 seed 7). The grid
  runs on different seeds (101/107).
- **Ranking signal.** The idea ranked sandboxes by PSI memory / refaults. Those
  measure capacity stalls, not bandwidth. The sim includes a PSI-heavy,
  bandwidth-light decoy (a build under a memory limit). PSI ranking throttles
  the decoy and misses the 90% target (0.88). The prototype ranks by per-cgroup
  LLC-miss rate (`perf stat -G`, `LLC-load-misses`) instead.
- **Sensor source.** OVMS exports no per-token latency metric. Its documented
  metrics are request-level only (`docs/metrics.md`, checked 2026-10-16). ITL
  must be timed from the token stream by the client or a sidecar.

## Results (simulation only; numbers are ILLUSTRATIVE until the live probes run)

Default point: DRAM 65 GB/s, iGPU reach 25 GB/s, duty 0.4.

| criterion | result | verdict |
|---|---|---|
| U1-U4 unit tests | 27/27 pass | pass |
| S1 greedy admission keeps ITL within 15%, default degrades one pod >= 40% | worst +11% vs +153% (default point); 66/81 grid points | pass |
| S1a target-demand admission does not protect | worst +76% | confirmed: refuter right |
| S2 aggregate tok/s within 5% of default | **-40%** (33.5 vs 55.6 tok/s) | **FAIL** |
| S3 guard holds >= 90% at >= 25% less sandbox slowdown than oracle static | default point: 0.95 victim, 0.30 vs 0.62 stream slowdown (52% less) | pass at default |
| S3 across the contended grid (naive victim < 0.9) | 50/180 (28%) | **mostly fails** |
| S3a duty 1.0: advantage must vanish | 60/60 contended points | pass (win is temporal, not a sim artifact) |
| S3b random ranking worse | 176/180 | pass |
| S3c blind guard misses 90% | 180/180; blind == naive exactly (unit test) | pass |
| memory ablation (v2 without burst-onset memory) | 1/180 | memory is the load-bearing element |
| v1 guard over the grid | 39/180 | v2 better, not sufficient |
| S4 power-only (infinite DRAM): PL1 117 W | victim 1.000 | no power confound at 117 W |
| S4 power-only: PL1 35 W | victim 0.688 | at 35 W a 31% drop is power alone; must be separated live |
| Kill: plausible UHD 770 region fully inside "<15% degradation" | reach 12: 27/81, 25: 21/81 | not killed in sim |

Where the guard fails, it fails by lag, not by throttling the wrong sandbox.
When it holds 90% (52 points), its advantage over the oracle static `cpu.max`
has median 47% and minimum 24%. The pass rate falls with victim speed. At
iGPU reach 40 it is 38/66, at 25 it is 12/60, and at **12 GB/s it is 0/54**. A
UHD 770 is most likely near the slow end. The ITL sensor's sample rate equals
the victim's token rate. At ~2.4 tok/s, three fresh tokens take ~1.25 s, and
bursts average 3 s. **The sensor is weakest exactly where the iGPU is slowest.**

### Answering the refuter

- "The admission is bookkeeping." With target demand, yes (S1a). With greedy
  demand, the budget holds without any runtime cap, but it holds by
  serialization. That costs 40% aggregate throughput on the UMA node alone (S2
  FAIL). The admission half only pays off when overflow can go to a dGPU with
  its own budget, and that is not tested here.
- "cpu.max doesn't govern iGPU DMA." Correct, and the guard never tries to. It
  throttles only CPU sandboxes to protect the iGPU. The cpu.max->bandwidth
  nonlinearity is swept as `gamma` in {0.5, 1, 2}: pass 8/60, 20/60, 22/60.
- "The guard is Heracles / Koordinator CPU Suppress." The control loop is the
  same family (see provenance). The sim says the part that matters is the
  burst-onset memory keyed on request activity (1/180 without it). That is what
  beats an oracle static cap on bursty victims, and it does nothing at duty 1.0
  (S3a). Koordinator CPU Suppress was not simulated as a baseline. The oracle
  static cap is an upper bound on any static policy, not on a dynamic one.
- "No RAPL DRAM domain; IMC counters are socket-wide." Accepted. The prototype
  has no reconciler. Bytes/token is checked solo only (L2).
- "PL1 is 117 W; power confounds." S4: no power-only degradation at 117 W in the
  sim. At 35 W the power cap alone explains a 31% drop. Live L1 pins PL1.

### What does not falsify itself here

The sim world is hand-built, so its contention numbers come from the
ILLUSTRATIVE constants in `sim.py` (reach, watts, arbitration weight,
loaded-latency knee). The checks against a sim that grants the win:
- Infinite DRAM gives exactly zero degradation (unit test).
- The guard's inputs are ITL samples, scores, activity and time. It never reads
  World state (signature test).
- The oracle static baseline is bisected per grid point.
- Duty 1.0 removes the advantage (S3a).
- Grid seeds differ from the tuning seed.
Not perturbed: solo-profile error beyond 3%, burst-length distribution (mean
3 s only), guard tick (0.1 s), and more than one victim.

## Live probes (after the running benchmarks finish; light, one at a time)

Host i9-13900T + UHD 770, 2x NVIDIA. No NPU/AMX/DSA.
1. Pin PL1/PL2 to one value for every run (`/sys/class/powercap/intel-rapl:0/
   constraint_0_power_limit_uw`, record before/after). Record `turbostat` package W.
2. Solo STREAM triad (`stream_c`, 4x LLC array, OMP on P-cores) -> node capacity (U3).
3. L1: UHD 770 OVMS decode (Llama-3.2-3B INT4, GPU plugin), streamed, client-timed
   ITL; solo vs next to a STREAM sandbox on E-cores. Kill if p95 ITL degradation < 15%.
   Repeat at sandbox `cpu.max` 25/50/75% for the real gamma curve.
4. L2: solo decode with `perf stat -a -e uncore_imc_free_running_0/data_read/,
   uncore_imc_free_running_1/data_read/` (both PMUs and `data_read` confirmed present
   in sysfs on this host, read-only).
   bytes/token = IMC read bytes / tokens vs `bytes_per_token()`; pass within +-20%.
5. Guard dry run: the guard on a recorded ITL trace from 3, printing the
   `cpu.max` strings it would write, before it writes any cgroup.
6. Steering: the same victim on an NVIDIA dGPU. If the dGPU is always free here,
   the admission half has no use case on this host. Say so.

## Provenance partition

- **Known (prior art):** software throttling of CPU co-runners to protect an
  integrated GPU without MBA (BWLOCK++, ECRTS 2018); victim-progress sensing with
  budget relaxation (Aghilinasab et al., TCAD 2020; BandWatch, RTCSA 2023); a
  victim metric triggering a CPU hard-cap on an antagonist (CPI2, EuroSys 2013);
  an SLO feedback loop throttling best-effort work (Heracles, ISCA 2015, cited
  from memory); BE CPU quota driven by LS load (Koordinator CPU Suppress);
  memory bandwidth as a Kubernetes-orchestrated resource (Monaco et al., ISORC
  2023); bandwidth as an extended resource at a capacity fraction (network
  bandwidth, KubeCon CN 2019); UMA bandwidth contention for on-device LLMs
  (Sereno, OSDI 2026, phones).
- **No prior art found (in the searches logged in the invention record):**
  the combination of LLM roofline demand from model metadata (greedy, not
  target) as a k8s extended-resource request on an x86 client UMA node; an LLM
  ITL sensor plus OVMS request-activity onset with burst-onset quota memory
  driving `cpu.max` on a part without MBA.
- **Unsearched / gaps:** GitHub code search (rate-limited 403), Google Patents,
  arXiv API (returned empty), Heracles re-check, any Koordinator variant with
  burst-onset memory, and Intel-internal tooling. Do not claim "first" until
  those are rerun.
