# Energy-aware residency advisor and tail-ledger router (research prototype)

Status: **research, simulation only**. Nothing here has been measured on hardware except the two
dGPU idle floors quoted below. Every iGPU number is an unmeasured placeholder and is swept, not
assumed.

Code: `bench/residency_model.py` (stdlib only). Tests: `python3 -m unittest bench/test_residency_model.py`.

## 1. Pre-registered acceptance criteria

Written and committed before any simulation result was produced (see `git log` for this file).
"In-sim" means the criterion is judged on the simulator; a pass is a statement about the model,
not about hardware. Section 5 lists the live probes that would decide the hardware question.

The refuter objection was accepted as the starting point, so four criteria test the objection
itself rather than the original claims.

| ID | Criterion | Pass condition |
|----|-----------|----------------|
| A1 | Tail accounting is additive (refuter: "charging E_tail per call double-counts") | For any arrival sequence on a device, the sum of the ledger's causal per-call charges equals the independently integrated power timeline to within 1e-6 relative. A naive per-call full-tail charge must over-count whenever gaps < tail length (demonstrated, not assumed). |
| A2 | No god's-eye state | Routers see only: perturbed calibration, predicted (noisy) output length, and their own causal history. Ground truth energy comes from integrating a separate power timeline. Headline numbers are re-run with every calibration parameter perturbed by +/-30 % and the sign of each conclusion is reported for every perturbation. |
| A3 | Claim (3), ledger router, in-sim | Over the swept tail grid, in cells where the dGPU tail energy is >= 1.5x the iGPU's whole incremental call energy: ledger router uses >= 20 % fewer platform J/task than both the best static device and the latency-greedy router, with p95 latency <= 1.5x latency-greedy. Attribution: removing the tail term from the ledger (an active-energy-only router, the closest prior-art analogue) must lose >= 10 percentage points of that saving. Also reported: the fraction of the plausible grid in which the condition cell exists at all. |
| A4 | Claim (2), iGPU residency, in-sim | Pre-registered expectation (refuter physics): with a desktop dGPU whose P8 floor is paid whether or not a model is resident, iGPU residency does NOT give >= 30 % fewer platform J/task at median gap >= 5 s. Pass = the simulator shows this (claim 2 killed for desktop cards). Under an RTD3 variant (floor drops when VRAM is empty, laptop dGPU), the closed-form crossover gap T* must land within +/-25 % of the simulated crossover. |
| A5 | Claim (1), advisor out-of-sample, in-sim | Advisor fitted on training traces, scored on disjoint test traces (different seeds; also a drifted step mix). Baseline = best reactive policy (LRU keep_alive in {60,300,900,inf} x max_loaded sweep, plus a load-cost-weighted eviction) tuned on the SAME training traces. Pass = stall >= 40 % lower and p95 per-call latency >= 15 % lower on test. Also reported against the baseline tuned on the test traces (oracle baseline). Ablation: a trace-blind pin set with the same remainder policy must lose most of the gain. |
| A6 | Tail calibration is resolvable | Using the nvidia-smi meter semantics (`power.draw` = 1 s average on Ampere+, `power.draw.instant`, documented accuracy +/-5 W), simulate a 100 ms poll and report the smallest tail energy estimable to within +/-50 % with N repeated calls. Pass = the original kill threshold (0.5 J) is resolvable with `power.draw.instant` and N <= 50; otherwise the kill threshold is declared unmeasurable and replaced. |

Primary energy metric everywhere: **platform J/task**, i.e. every device's power integrated over
the whole run (floors included) divided by tasks. A slower device lengthens the run and pays more
floor energy; this charges race-to-idle correctly. Incremental (above-floor) J/task is reported as
a secondary metric.

## 2. Measured inputs

| Quantity | Value | Source |
|---|---|---|
| RTX 4000 SFF Ada idle | P8, 12.86 W, 2011 MiB resident | one `nvidia-smi` query on this host (refuter) |
| RTX 2000E Ada idle | P8, 6.94 W, 8 MiB resident | same |
| `power.draw` semantics | 1 s average on Ampere or newer; accuracy +/-5 W | `nvidia-smi --help-query-gpu`, driver 590.48.01 |
| `power.draw.instant` | exists on this host | same |

Everything else (dGPU active/tail power and duration, iGPU decode speed and package increment,
load bandwidth, package floor) is a placeholder and is swept.

## 3. Model

`bench/residency_model.py`, stdlib only. Reproduce everything with
`python3 bench/residency_model.py report > bench/residency_results.json` (about 75 s on one core;
the committed `bench/residency_results.json` is that output).

- **Ground truth** is a per-device power timeline: floor (P8 for the dGPUs, package idle for the
  iGPU) + active excess while a call runs + a post-call tail (`tail_w` for `tail_s`, cut short by
  the next call) + a wake charge after `deep_after_s` of idle. `integrate_timeline()` is the only
  place energy is counted for scoring. Routers never call it.
- **Tail ledger** (`TailLedger`). Each call is charged its active energy plus its full tail, and
  the unused part of the previous call's tail on that device is refunded at the moment the new
  call starts. That refund is known causally (the new start time is now), so the charges telescope
  exactly to the integrated timeline. This answers the refuter's double-counting objection: the
  per-call marginal cost is "my tail minus the tail I reclaim", and the only unknown is whether
  *my* tail will be reclaimed, which the router estimates as `E[min(tail_s, G)]` over the gaps it
  has observed.
- **Router** (`make_ledger_policy`): picks the device with the lowest
  `active + tail_w*(E[min(tail_s,G)] - remaining_tail) + wake + floor_w*latency`, among devices
  whose predicted finish is within `slack` x the fastest. The last term charges the whole
  platform floor for the extra time a slower device keeps the agent waiting (race-to-idle).
  Variants: `ledger_no_tail` (tail term removed: an active-energy-only dispatcher, the closest
  prior-art analogue), `ledger_unconstrained` (`slack=inf`), `ledger_incremental` (no floor-time
  term, `slack=inf`). Routers see a +/-30 %-perturbed calibration and a log-normally noisy
  output-length prediction.
- **Residency (claim 2)**: one agent, every call on one device. On a desktop card the dGPU floor
  is the same with or without a model resident (`p_floor_empty=None`); the RTD3 variant drops it
  to 0.5 W when VRAM is empty. `predicted_crossover()` is the closed form using `E[G]` and
  `E[min(tau,G)]` for a log-normal gap; `simulated_crossover()` bisects on sampled traces.
- **Advisor (claim 1)**: two dGPU pools (20 GB, 16 GB), six models totalling 56.8 GB (1.58x
  memory), three closed-loop agents, log-normal think gaps (median 3 s). The advisor searches pin
  sets (single-model moves plus re-tuning of the reactive remainder, two rounds; models <= 2 GB
  may be duplicated on both pools). This is local search, **not** the ILP the idea named.
  Baseline: LRU or load-cost-weighted (GreedyDual-style) eviction x keep_alive {60,300,900,inf} x
  max_loaded {1,2,3,inf}. The GreedyDual policy stands in for Lemonade's eviction; Lemonade's
  actual policy was not checked. Fit on seeds 11-12, scored on seeds 21-23 (same mix) and on a
  drifted step mix.
- **Meter (A6)**: true power per call = floor + active (optionally a 0.3 s prefill burst at
  1.5x) + a step tail. Meters: `instant` (sample-and-hold, 20 ms internal refresh, placeholder)
  and `avg1s` (trailing 1 s mean, the documented `power.draw` behaviour on Ampere+). Every
  reading has a per-call gain error (+/-5 %), offset (+/-5 W) and white noise. Polled at 100 ms.
- **Deployments**: `python3 bench/residency_model.py deploy --image "$IMAGE" --plan
  bench/residency_plan.example.json` writes a `List` of one Deployment per (model, pool) with
  `nvidia.com/gpu` or `gpu.intel.com/i915` limits. A device-plugin request cannot choose *which*
  of two NVIDIA cards a pod gets, so the pool is only recorded as a label; enforcing it needs DRA.
  The output was not schema-validated against an API server (no clusters while benchmarks run).

## 4. Results against the pre-registered criteria

All numbers are in-sim; every iGPU parameter is unmeasured.

| ID | Verdict | Evidence |
|----|---------|----------|
| A1 | **PASS** | 300 random sequences (gaps inside and outside the tail, random wake thresholds): ledger total equals the integrated timeline to 1e-6. With 2 s gaps and a 5 s tail, naive per-call charging over-counts by exactly 9 x 10 W x 3 s = 270 J on 10 calls (`test_naive_full_tail_overcounts...`). |
| A2 | **PASS** | Scoring uses the true devices only (`test_energy_comes_from_truth_not_calibration`); the first 30 routing decisions are unchanged when every later call is rewritten (`test_decisions_are_causal`). Claim-3 verdicts are identical under three +/-30 % calibration perturbations (0 passing cells in every run). |
| A3 | **FAIL** (claim 3 killed in-sim) | 720 cells (tail 2-40 W x 0.5-10 s, iGPU 10-30 tok/s at 6-15 W, median gap 1-10 s). 124 cells meet the eligibility condition (tail >= 1.5x iGPU call energy). Passing cells: **0** on platform J/task and **0** on incremental J/task. Best saving vs the best static device: 0.0 % (platform), 2.9 % (incremental). Two mechanisms kill it: (a) the p95 <= 1.5x guard forbids the iGPU, which is 4-11x slower per call across the swept speeds, so the guarded ledger router collapses to latency-greedy; (b) without the guard the router converges to one device, so it matches the best static choice rather than beating it by 20 %. |
| A4 | **PASS as pre-registered** (claim 2 killed for desktop cards) | Desktop floor: iGPU residency saves -175 % to +0.2 % platform J/task over 9 parameter sets at gaps 5/10/30 s; never >= 30 %. The absolute J/task difference between placements does not change with gap once the tail saturates (`test_floor_difference_is_the_only_gap_lever`), confirming the refuter: residency does not move the dGPU floor. RTD3 variant: best saving 24.5 % (gap 30 s, fast iGPU), still under 30 %. Predicted T* is within 5 % of simulated T* in all 10 cells that have a crossover. That agreement is a consistency check of the closed form against sampling, not evidence about hardware: both share one power model. |
| A5 | **FAIL** (claim 1 killed in-sim) | Out-of-sample stall reduction vs the train-tuned reactive baseline: 3.9-14.1 % across load bandwidth {1.5, 3, 6} GB/s and both test mixes (kill threshold was < 15 %). p95 got worse in 5 of 6 cases (range -41 % to +5.8 %). Against the test-tuned (oracle) baseline: 4.7-13.3 % stall reduction. The trace-blind pin ablation does worse than the baseline (stall -11 % to +3 %, p95 -33 % to -49 %), so the trace statistics carry what little gain exists. The searched pins are almost always just the router and/or embed model. |
| A6 | **PASS with conditions** | With `power.draw.instant` and a corrected estimator (skip readings within one meter refresh of the work end, stretch the first interval back to the end), a 0.5 J tail is resolved to p90 error <= 50 % with <= 50 calls at 0.3 W noise, with or without a prefill burst; with an internal refresh of 20, 50 or 100 ms the p90 error at 0.5 J, N=50 is 12 %, 19 %, 20 %. The naive estimator needs 1-5 J. `power.draw` (1 s average) cannot resolve tails naively at all; plateau subtraction works only if active power is flat, and a 0.3 s prefill burst raises its minimum resolvable tail to 20 J. So tail calibration must use `power.draw.instant`, long (>= 3 s) calibration calls and ~50 repeats. Meter noise and refresh period are unmeasured (probe P0). |

### Exploratory (post-hoc; not pre-registered, needs its own pre-registration)

The router's one robust property in the sim is regret, not savings. Across all 720 cells, against
the hindsight-best static device on incremental J/task:

| policy | median regret | p90 | max |
|---|---|---|---|
| `ledger_incremental` | 0.0 % | 1.0 % | 32.7 % |
| latency-greedy | 0.0 % | 84.5 % | 730 % |
| static dGPU | 0.0 % | 84.5 % | 730 % |
| static iGPU | 35.8 % | 201 % | 371 % |

The tail-aware ledger finds whichever single device suits the tail and gap regime without being
told; it does not beat that device. On platform J/task (floor-time term on, no latency guard) its
max regret is 13.9 % vs 46.7 % for latency-greedy.

## 5. Verdict

The refuter was right on the physics, and the sim agrees on every count it could test:

1. Residency does not change a desktop dGPU's floor, so the gap-distribution term is inert there.
   Claim 2 is dead for desktop cards; it needs RTD3-capable laptop dGPUs, and even then the sim's
   best case misses 30 %.
2. Per-call tail charging double-counts; a causal refund ledger fixes it exactly.
3. Scored out-of-sample against a tuned reactive baseline, the what-if advisor gives 4-14 % stall
   reduction and worse p95. The original 40 %/15 % numbers were not anchored and do not survive.
4. The p95 <= 1.5x constraint and a 4-11x slower iGPU rule out routing short calls to the iGPU;
   the claimed 20 % saving over the best static device is not reachable in the swept space.

What survives: (i) the causal tail ledger as an accounting primitive; (ii) the measurement
design for the dGPU post-call tail (instant meter, corrected estimator, ~50 repeats); (iii) a
regret-minimizing device chooser, as a new hypothesis. Status: **research**. Do not build the
advisor. Run probe P1 first, and continue only if the measured dGPU tail is >= 1.5x the iGPU's
whole-call energy at the measured iGPU speed.

## 6. Live probes (after the running benchmarks finish)

Host: i9-13900T, UHD 770, RTX 4000 SFF Ada, RTX 2000E Ada. No NPU, so NPU pools stay unmodelled.

| # | Probe | Method | Decides |
|---|---|---|---|
| P0 | Meter characterization | Idle each dGPU and poll `nvidia-smi --query-gpu=timestamp,power.draw.instant,power.draw,pstate --format=csv -lms 20` for 60 s. Noise sigma = std of `power.draw.instant`; internal refresh = median interval between value changes. | `noise_w`, `update_s` in A6 |
| P1 | dGPU post-call tail | 50x: a fixed 3 s decode on the RTX 2000E (then the 4000 SFF), then 10 s idle, polling as in P0. Estimate the tail with the corrected `tail_estimate` logic; record the pstate trace back to P8. | Kill gate: tail < 0.5 J, or P8 within 0.5 s, kills the router |
| P2 | Residency vs floor | Same card: P8 power with ~8 MiB vs with a 2 GB model resident (ollama `keep_alive=-1` vs unloaded), 120 s each. | Floor is residency-invariant (expected, per refuter) |
| P3 | iGPU whole-call energy | 1.5-3B model on UHD 770 (OpenVINO GPU or llama.cpp SYCL), 50 calls x {64, 128, 256} output tokens; package energy from `/sys/class/powercap/intel-rapl:0/energy_uj` (root) minus an idle baseline; tok/s from the server. | `igpu_tps`, `igpu_w`, the A3 eligibility ratio |
| P4 | Cold-load bandwidth | First-token time per model from cold and warm page cache. | `load_gbs` for A5 |
| P5 | Replay | Only if P1 and P3 pass the gate: replay agent traces through both devices with the ledger router vs static, integrating RAPL + `power.draw.instant`. | Hardware A3 |

Named hardware where claim 2 could hold: a notebook dGPU with working RTD3/D3cold (NVIDIA
`NVreg_DynamicPowerManagement` on a supported laptop), where empty VRAM lets the card power off.
Not available on this host.

## 7. Provenance partition

- **Known** (search log in the idea's provenance record): length-based GPU/NPU dispatch on a
  calibrated energy-latency crossover (IEEE 11627900); keep-alive plus placement co-optimization
  for carbon (EcoLife, arXiv 2409.02085); tail-energy-aware scheduling (TailEnder, Bartendr,
  MobiCom 2010, cellular radio); agent idle-energy profiling (arXiv 2609.29707); NPU/iGPU agent
  scheduling for latency (Agent.xpu, arXiv 2506.24045). Model swapping and placement under memory
  limits (AlpaServe, ServerlessLLM, Melange, Clockwork, DynamoLLM) are cited from memory by the
  refuter and were **not** search-verified. The refund ledger is standard interval accounting;
  no novelty is claimed for it alone.
- **No prior art found** (SearXNG general + science, 2026-10-16): a causal tail-refund ledger with
  a gap-distribution tail expectation, used for per-call choice between a dGPU and an iGPU on one
  PC.
- **Unsearched**: GitHub (rate-limited; llama-swap, Lemonade, LiteLLM energy plugins not
  checked), arXiv API (HTTP 429), Google Patents (one patent surfaced, abstract unread), DB
  index-advisor literature (AutoAdmin/DTA, from memory), and nvidia-smi/RAPL meter
  characterization literature behind the A6 estimator. No public novelty claim before these run.
