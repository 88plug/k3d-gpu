# Placement twin: choosing `k3d-gpu.io/cores` from counters and contention curves

Status: **prototype, simulation only.** Nothing in this document has been measured
on hardware except the items marked *measured*. This file was committed with
sections 1 and 2 only, **before** any experiment code existed (see `git log` on this
file). The results in section 5 were appended afterwards.

## 1. Question

k3d-gpu can pin a pod to P-cores or E-cores (`k3d-gpu.io/cores=performance|efficient`,
NRI balloons). Today a human picks the label. Can a small model pick it better than the
simple rules a developer would actually use?

The twin has three parts:

1. **E/P factor.** Predict how much slower a pod runs on an E-core than on a P-core, from
   a short `perf stat` sample of the pod on a P-core (TMA L1, IPC, L2/LLC/branch MPKI) and
   its off-CPU fraction (from `/proc/<pid>/schedstat`).
2. **E-module packing.** Predict the slowdown when k pods share one 4-core Gracemont
   module and its 4 MB L2.
3. **Shared-DRAM and clock coupling.** Per-engine (iGPU, P balloon, E balloon) contention
   curves from a calibration grid, plus a per-engine clock model. A fixed point couples the
   engines to predict co-run throughput and tool latency.

A solver enumerates legal placements against a per-pod latency SLO and emits the cores
label, an E-module group label and the device request.

## 2. Pre-registered acceptance criteria and predictions

Written before any code. All experiments run on a **simulated host** whose hidden physics
the twin cannot read (section 3). Thresholds come from the original idea, tightened where
the refuter's objection applied.

### Harness validity (must pass, or no other result counts)

| ID | Check | Pass |
|---|---|---|
| H1 | **Null world.** The true E/P factor is a constant plus noise, independent of counters. | Twin LOO MAE is **not** better than the best constant by more than 0.02. |
| H2 | **Shuffled features.** Counter rows are permuted across workloads before fitting. | Twin LOO MAE is within 0.03 of the best constant, or worse. |
| H3 | **Isolation.** `twin/placement_twin.py` does not import the simulator. The experiment passes the twin observation dicts only. | Enforced by a unit test (AST import scan). |
| H4 | **Perturbation.** Every headline number is reported over 20 world seeds. Each seed redraws the hidden multipliers. | Report the median and the worst decile, not the best seed. |

### A. E/P factor (24 agent-tool workloads, leave-one-out)

| ID | Criterion | Pass | Falsified if |
|---|---|---|---|
| A1 | Twin MAE on the wall-clock E/P factor | ≤ 0.12 | > 0.15 |
| A2 | Beats best constant ratio and the Thread-Director-class ratio | each baseline ≥ twin + 0.05 | any baseline within 0.05 |
| A3 | **Phase.** The 2 s sample at pod start (the original design) vs a per-image profile accumulated over earlier runs | **Prediction:** start-sample MAE ≥ 1.5 × per-image MAE | — (this tests the refuter's objection; either outcome is reported) |

### B. E-module packing (random 4-pod mixes on one module)

| ID | Criterion | Pass | Falsified if |
|---|---|---|---|
| B1 | MAPE of per-pod co-run time | ≤ 10 % | > 15 % |
| B2 | No-interference baseline MAPE | ≥ 20 % | — (if it is < 10 %, the packing term has nothing to do on this mix distribution, and B1 is moot) |

### C. Contention and clock coupling (iGPU decode × CPU co-runner pairs)

| ID | Criterion | Pass | Falsified if |
|---|---|---|---|
| C1 | Twin MAPE on decode tok/s, held-out pairs (≥ 12) | ≤ 10 % | > 15 % |
| C2 | Twin MAPE on co-runner latency | ≤ 8 % | > 15 % |
| C3 | Isolated-roofline baseline (fair share of 57.6 GB/s, clocks fixed) | MAPE ≥ 25 % | — |
| C4 | **Refuter test.** A bandwidth-only twin (no clock model) vs the clock-aware twin, swept over clock-coupling strength γ ∈ {0, mid, high} and two envelopes (this host's measured 117/117 W, and Intel spec 35/106 W) | **Prediction:** the bandwidth-only twin passes C1 at γ = 0 and fails it at high γ or on the 35 W envelope; the clock-aware twin passes everywhere | the clock-aware twin fails C1 at high γ |

Deviation from the idea, recorded before running: the idea used 5 external bandwidth
levels per target. A three-region continuous piecewise-linear curve has 5 free parameters
(two breakpoints and three slopes), so 5 noisy points identify nothing. The grid is
**8 levels × 3 intensities × 3 targets = 72 runs**. A second axis of **5 compute-only
(L1-resident) load levels × 3 targets = 15 runs** separates clock loss from bandwidth loss.
Each run also records its effective clock (cycles / ref-cycles).

### D. Placement (6 mixes, each 6 tool pods + 1 foreground engine)

Objective: maximise foreground throughput subject to every tool pod meeting its latency
SLO (1.5 × its isolated P-core latency). Baselines are the ones a developer would really use:

- **unpinned**: no label; tool pods take free P-cores first and spill onto E-cores;
- **all-P**: every tool pod labelled `performance`;
- **IO rule**: pods whose off-CPU fraction exceeds 0.5 go to E, the rest to P;
- **constant ratio**: E if (best constant × P latency) meets the SLO.

| ID | Criterion | Pass | Falsified if |
|---|---|---|---|
| D1 | Twin placement objective, scored by the hidden world, as a fraction of the oracle | ≥ 95 % on every mix | < 90 % on any mix |
| D2 | Twin SLO violations (true) | ≤ 5 % of tool pods | > 10 % |
| D3 | Tool pods moved off P by the twin | ≥ 30 % | — |
| D4 | Some baseline misses the oracle by more than 5 % in ≥ 2 of 6 mixes | yes | **if the IO rule is within 5 % of the oracle on every mix, the twin adds nothing on that distribution, and that is the reported result** |

## 3. Method

To be filled in after the code exists. The rule fixed now: the simulator's hidden
functions must not have the same form as the twin's models. The E/P truth is a nonlinear
CPI-stack with interactions, not a linear model. Contention truth is a queueing curve, not
a piecewise-linear one. Workloads have phases, and counters are sampled from a window with
noise.
