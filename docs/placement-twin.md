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

The rule fixed with the pre-registration: the simulator's hidden functions must not have
the same form as the twin's models. The E/P truth is a nonlinear CPI-stack with
interactions, not a linear model. Contention truth is a queueing curve, not a
piecewise-linear one. Workloads have phases, and counters are sampled from a window with
noise.

Files:

| File | Role |
|---|---|
| `twin/placement_twin.py` | The twin. Imports only `numpy` and the stdlib (test H3). |
| `twin/sim_world.py` | Hidden-physics host. Constants marked *measured* come from `docs/power-envelope.md` on `origin/claude/invent-tseries-envelope` (MSR/sysfs reads of this i9-13900T: PL1 = PL2 = 117 W, τ 56 s, P turbo 5.3/5.1/5.0/4.3 GHz by active count, E 3.9 GHz, iGPU 9.7 W at 1650 MHz). |
| `twin/experiment.py` | Experiments A–D. Hands the twin observations only; truth only scores. |
| `twin/test_placement_twin.py` | 22 unit tests: the perf parser on a real capture from this host, planted-parameter recovery for every model, enumerator vs brute force, isolation, manifests. |
| `share/placement-twin/balloons-values.yaml` | Unapplied balloons values that consume `k3d-gpu.io/e-group` (groupBy + an `l2cache` load class). |
| `share/placement-twin/profiler-daemonset.yaml` | Unapplied CAP_PERFMON profiler DaemonSet. The loop that discovers target cgroups is a stub. |

Twin parts:

- **E/P factor.** `factor = off + (1 - off) * ridge(TMA L1, IPC, L2/LLC/branch MPKI, vector share)`.
  Off-CPU time does not stretch on an E-core, so it is carried through unscaled. λ is
  chosen by inner leave-one-out on the training rows only.
- **Packing.** `s_i = 1 + b1 · be_mem_i · Σ_{j≠i} p_j + b2 · be_mem_i · (Σ p)^2`, with
  `p = be_mem · (1 + l2_mpki/10)`. Fit on 30 calibration 4-pod mixes.
- **Contention.** PCCS three-region curves per engine (iGPU, P, E) at three target
  intensities, from an 8-level × 3-intensity × 3-target grid. The clock-aware twin divides
  the measured slowdown by its clock part `1 + κ (r - 1)` before fitting, so the curve sees
  bandwidth loss only.
- **Clocks.** A measured table of each engine's clock over the active set
  (nP ∈ {0,1,2,4,6,8} × nE ∈ {0,1,4,8,12,16} × iGPU on/off, spin loops, 71 runs),
  bilinearly interpolated. κ (clock-sensitive share of run time) from two runs at clock caps
  5.3 and 3.0 GHz, `κ = (a / f_hi) / t_hi` with `a = (t_lo - t_hi) / (1/f_lo - 1/f_hi)`.
- **Fixed point.** `s_i = pack_i · (1 + κ_i (r_i - 1)) · curve_engine(x_i)` with
  `x_i` = delivered external bandwidth `Σ_{j≠i} bw_j / s_j`, damped iteration.
- **Solver.** Enumerates every legal placement of 6 pods (P with ≥ 1 P-core left for the
  decoder, or one of 4 interchangeable E modules of 4 cores), fewest predicted SLO
  violations first, then highest predicted decoder tok/s.

Hidden-world physics (`sim_world.py`): per-component E-core CPI multipliers redrawn per
seed; an L2 capacity step (2 MB P private vs 4 MB shared module); DRAM time that does not
scale with clock; an M/M/1-like DRAM latency curve plus a hard 57.6 GB/s cap; a package
power budget solved by bisection on `f^3` core power, then a thermal derate of strength γ;
workloads with a startup phase whose mix differs from the main phase. Envelopes: `host`
(117 W, measured) and `spec35` (35 W sustained, Intel spec; τ not modelled).

Perturbation added after pre-registration: **`hetero`**. A busy core's power scales with
its compute duty (`0.35 + 0.65 · duty`), so cores stalled on DRAM or blocked on IO draw less
than the spin loops the clock table was calibrated on. This attacks the twin's clock model
where the refuter aimed: calibration that does not match the power the real mix draws.

## 4. Deviations after pre-registration (all recorded before the final run)

1. **Probe v2.** The pre-registered clock model was a linear derate over (nP, nE, gt) fitted
   from the 87 contention/compute runs. Those runs cover too few active sets. v2 adds the
   71-run spin sweep and a table model. `--probe v1` reproduces the pre-registered version.
2. **D SLO.** "1.5 × isolated P latency" was infeasible: the oracle violated it on 3–5 of
   6 pods in every mix (a 6-pod mix plus an 8-thread decoder cannot give any pod its
   isolated latency). New SLO: 1.5 × (and 2.0 ×) the pod's latency in the status-quo all-P
   placement with the decoder running. The twin receives the SLO numbers as user input.
3. **D score.** A placement that violates more SLOs than the oracle scores 0. Throughput
   bought with SLO misses does not count. Raw violation rates are reported alongside.
4. **`twin_margin`.** Added after seeing the twin pick SLO-boundary placements. Guard band
   = 2 × the twin's own LOO relative error of the E/P factor on the calibration pods (no
   truth used). Reported next to the pre-registered `twin`, not instead of it.
5. **κ estimator.** The log-slope estimator was biased (0.36 for a true 0.30); replaced by
   the exact two-point estimator above. Found by a failing unit test.
6. **`hetero`** perturbation (section 3).

## 5. Results (simulation only)

Run: `nice -n 19 taskset -c 31 python3 twin/experiment.py --out twin/results.json` (one core,
about 30 min). Raw numbers: `twin/results.json`. A and B: 20 seeds. C: 10 seeds per
setting, 16 held-out pairs each. D: 5 seeds × 6 mixes = 30 mixes per setting. Values are
median / worst decile over seeds.

### Harness validity: passes

| ID | Result | Verdict |
|---|---|---|
| H1 null world | twin MAE 0.175 vs best constant 0.045. The structured model is *worse* when counters carry no signal. | pass |
| H2 shuffled rows | twin 0.315 vs constant 0.198 | pass |
| H3 isolation | AST test: the twin imports only numpy and the stdlib | pass |
| H4 perturbation | 20 seeds; worst decile reported | done |

The twin also loses experiment D (below), so the simulator does not hand it the win.

### A. E/P factor: survives, but most of the gain is the off-CPU term

| Model | MAE (median / worst decile) |
|---|---|
| **twin** (structured ridge, per-image profile) | **0.037 / 0.052** |
| twin, 2 s sample at pod start | 0.147 / 0.186 |
| twin, unstructured ridge | 0.043 / 0.051 |
| off-CPU structure + one constant CPU factor (no counters) | 0.086 / 0.135 |
| best constant | 0.198 / 0.237 |
| Thread-Director-class ratio | 0.192 / 0.252 |

- A1 pass (0.037 ≤ 0.12). A2 pass (margin over the better of the two pre-registered
  baselines: 0.147, worst decile 0.114 ≥ 0.05).
- **A3 confirms the refuter.** The 2 s start sample is 3.9× worse than the per-image
  profile (worst decile 3.1×), and its worst decile 0.186 crosses the A1 falsification
  line. The original design (sample at pod start) is falsified; the profiler must
  accumulate per image (`profiler-daemonset.yaml` is written that way).
- Not pre-registered, reported because it matters: the counter-free baseline "off-CPU
  share + one constant" already reaches 0.086. Counters halve that again. The IO rule a
  developer would use runs on the same off-CPU signal.

### B. E-module packing: falsified

Twin MAPE 14.9 % / 22.5 % vs no-interference 19.8 % / 23.4 %. B1 fails (worst decile >
15 %). B2 is not met either (no-interference < 20 %). The bilinear pressure term does not
capture the hidden L2-overflow step. Removing it in D (`twin_no_pack`) still raises SLO
violations from 25 % to 44 %, so the term carries signal, but not enough.

### C. Contention and clocks: survives; the refuter's power objection is confirmed

Decode tok/s MAPE and co-runner latency MAPE, median (worst decile):

| Setting | clock-aware twin: dec / co | bandwidth-only twin: dec / co | isolated roofline: dec / co |
|---|---|---|---|
| host 117 W, γ 0 | 1.1 (1.8) / 3.7 (4.3) % | 1.2 (1.8) / 3.5 (4.1) % | 3.1 (4.9) / 7.3 (10.8) % |
| host, γ 0.3 | 1.1 (1.5) / 2.8 (3.2) % | 1.1 (1.3) / 12.6 (17.1) % | 2.7 (3.9) / 21.5 (26.7) % |
| host, γ 1.0 | 1.0 (1.3) / 2.1 (2.5) % | 0.9 (1.2) / 30.9 (42.2) % | 2.1 (2.8) / 38.6 (45.6) % |
| spec 35 W, γ 0.3 | 4.1 (5.3) / 2.0 (2.8) % | 7.4 (102) / 16.0 (22.9) % | 10.8 (12.0) / 30.4 (35.2) % |
| host, γ 0.3, hetero | 1.0 (1.6) / 2.5 (4.4) % | 1.1 (1.3) / 11.8 (15.9) % | 2.7 (3.9) / 20.1 (25.1) % |
| spec 35 W, γ 0.3, hetero | 4.3 (4.5) / 3.0 (4.4) % | 7.0 (11.8) / 13.7 (17.4) % | 10.3 (11.5) / 29.3 (34.3) % |

- C1 and C2 pass in every setting for the clock-aware twin.
- C3 (roofline ≥ 25 %) **fails for decode** in every setting and for co-runner latency at
  γ 0. On the measured 117 W envelope the iGPU decoder barely contends, so there is little
  for any model to win on the iGPU branch. This supports the refuter's "little value" point.
- C4 prediction confirmed: the bandwidth-only PCCS twin passes at γ 0. It fails co-runner
  latency once clocks couple (12–31 %), and fails decode on the 35 W envelope (worst
  decile 102 %). A PCCS curve keyed on bandwidth alone fits frequency effects as noise.
  The clock model is required, and it must come first. The power-aware plant model the
  idea leaned on does not exist; this prototype carries its own measured clock table.
- Caveat on the win: the clock table is calibrated on the same active-set grid the truth
  depends on. `hetero` breaks that match (non-spin co-runners draw less power) and costs
  the twin little (co worst decile 3.2 → 4.4 %). Thermal history and the PL1 τ window
  are not modelled anywhere (live probe L6).

### D. Placement: falsified

Score = decoder tok/s / oracle, and 0 if the placement violates more SLOs than the oracle
(the oracle violates none). 30 mixes per row group.

| SLO, world | method | median score | mixes < 95 % | SLO violations | tool pods off P |
|---|---|---|---|---|---|
| 1.5×, host | **twin** | 0.00 | 24 | **25 %** | 67 % |
| | twin_margin | 0.70 | 25 | 13 % | 48 % |
| | IO rule | 0.66 | 27 | 0 % | 19 % |
| | all-P | 0.42 | 30 | 0 % | 0 % |
| | unpinned | 0.24 | 30 | 18 % | 43 % |
| | constant ratio | 0.00 | 30 | 65 % | 98 % |
| 2.0×, host | **twin** | 1.00 | 14 | 7 % | 98 % |
| | twin_margin | 1.00 | 9 | 4 % | 96 % |
| | constant ratio / all-E | 1.00 | 10 | 7 % | 98–100 % |
| | IO rule | 0.42 | 30 | 0 % | 19 % |
| 1.5×, host, hetero | twin | 0.00 | 24 | 20 % | 55 % |
| | IO rule | 0.75 | 24 | 0 % | 19 % |
| 1.5×, 35 W, hetero | twin | 0.00 | 27 | 38 % | 74 % |
| | IO rule | 0.75 | 25 | 0 % | 19 % |

- D1 fails (< 90 % on most mixes). D2 fails at 1.5× (25 % > 10 %). D3 passes trivially.
- At 2.0× the twin is no better than the constant ratio or "everything on E".
- At 1.5× the **IO rule** is the best safe policy: no violations and 0.66–0.75 of oracle.
  The twin moves more pods but breaks SLOs.
- Mechanism of the failure: the solver picks placements on the SLO boundary, so a modest
  prediction error flips SLOs. Twin co-run latency error is 9 % MAPE with a +5 % bias for
  E-pods, 1.7 % for P-pods and 5 % for the decoder. The twin's own calibration error
  estimate (E/P factor LOO, 2.9 %) understates that by 3×, because most of the error comes
  from packing (B) and composition, not from the factor. `twin_margin` halves violations
  but does not reach the IO rule's safety.

### Verdict

The predictive parts that are prior art work in simulation once a clock model is added, as
the refuter said they must: counter regression for the E/P factor, and PCCS curves. The
parts that would make this an invention are **falsified in simulation** against the honest
baseline, the IO rule: the E-module packing term, and the SLO-constrained solver driving
`k3d-gpu.io/cores`. Do not ship the solver. Two small things are worth keeping: the
per-image off-CPU share plus a counter E/P factor as an *advisory* number, and the finding
that a start-of-pod sample is the wrong window.

## 6. Live probes (after the benchmarks finish; this host unless named)

Read-only facts checked now: `cpu_core` = CPUs 0–15, `cpu_atom` = 16–31; E-module L2
groups 16–19, 20–23, 24–27, 28–31; P L2 is per core (0–1).

| ID | Probe | Decides |
|---|---|---|
| L0 | `cat /sys/class/powercap/intel-rapl:0/constraint_{0,1}_{power_limit_uw,time_window_us}` | Which envelope row of C/D applies (`docs/power-envelope.md` says 117/117 W, τ 56 s). |
| L1 | In the k3d server container: `perf stat -x, -a -e cpu_core/slots/,cpu_core/topdown-retiring/ -- sleep 1` | Whether the profiler DaemonSet can work inside k3d at all (seccomp, perf_event_paranoid). |
| L2 | 24 tool workloads (rg, pytest, gcc, git status, jq, sqlite, curl, tokenizer, small numpy, tsc, zstd, headless DOM; 2 inputs each), 3 runs pinned to CPU 0 and to CPU 16, with `perf stat -x,` and `/proc/<pid>/schedstat` | A1–A3 on hardware: twin vs constant vs TD class vs off-CPU + constant. |
| L3 | 30 random 4-pod mixes on CPUs 16–19 vs the same pods spread over 16, 20, 24, 28 | B1/B2 on hardware. |
| L4 | Spin sweep nP × nE × iGPU busy; clocks from `turbostat --show Bzy_MHz,GFXMHz,PkgWatt` | The clock table; whether the 117 W envelope ever binds on this box. |
| L5 | Re-render balloons with `share/placement-twin/balloons-values.yaml`; pods with `e-group` m0 (×3) and m1 (×1); read each container's `cpuset.cpus.effective` | Whether groupBy + the l2cache load class put each group on one L2 block and groups on different blocks. |
| L6 | RAPL energy over 30 s for 8 P threads spinning vs DRAM-streaming vs sleeping on IO, plus a 120 s run to cross τ | The `hetero` assumption, and the τ window the sim leaves out. |
| L7 | 6 mixes: llama.cpp CPU decode on the free P-cores + 6 tool pods via k3d labels; twin vs IO rule vs all-P vs unpinned | D on hardware. Only worth running if L2 and L3 pass. |

## 7. Provenance

**Known (cite, do not claim):**
- PCCS three-region slowdown curves and the calibrator grid: Xu, Belviranli, Shen, Vetter,
  MICRO 2021, doi 10.1145/3466752.3480101.
- Counter-based big/little performance prediction: PIE (Van Craeynest et al., ISCA 2012);
  WASH (Jibaja et al., CGO 2016). Hardware classification: Intel Thread Director / HFI.
- Multi-co-location contention prediction with SLO placement: Pythia (Middleware 2018).
  Kubernetes realizations: Marla, C-Koordinator (arXiv 2507.18005), Tzenetopoulos et al. 2021.
- Contention- and DVFS-aware device placement for LLM inference on a unified-memory SoC
  (CPU + iGPU + NPU): HeteroMosaic (arXiv 2607.12839, MICRO 2026; AMD Ryzen AI). Found in
  this pass; it narrows the iGPU branch further.
- L2-block-aware CPU selection: nri-plugins balloons `loadClasses` with `level: l2cache`,
  and `groupBy` (v0.14.0 docs). The packing *placement* mechanism is upstream; only the
  predictor that picks the groups would be new.

**No prior art found (in the searched corpus):** a per-image P→E factor predictor for
Raptor Lake that writes nri balloons labels; an explicit packing term for pods sharing a
Gracemont module L2; a coupled bandwidth-plus-clock fixed point over iGPU/P/E for Intel
client parts. The first two are falsified or modest in this sim, so the gap does not
support a claim yet.

**Search log (this pass, added to the provenance step's log):**
- searxng `search_many`, 5 queries (E-core slowdown prediction ADL/RPL; Thread Director
  IPC classes; iGPU/CPU contention placement Intel UHD; Kubernetes P/E placement
  interference; PCCS): only dictionary/junk results.
- arXiv API: `abs:"Alder Lake"` 1 hit (Asymmetry-aware Scalable Locking). `abs:"P-cores"`,
  `abs:"E-cores"`, `abs:"efficiency cores"`: nothing relevant beyond GC scheduling on AMPs
  and big/little web search. `abs:"Thread Director"`: 0. `abs:"integrated GPU" AND
  abs:"contention"`: 5, including HeteroMosaic and Leaky Buddies (covert channels).
- `gh search code preferCoreType`: only containers/nri-plugins and operatorhub mirrors;
  no third-party consumer sets the label from counters.
- exa: rate-limited in this pass, not used.

**Unsearched:** Google Patents; IEEE Xplore / ACM DL full text; 2023–2026 Linux
`sched/hfi` list evaluations of IPC classes; Intel-internal work.
