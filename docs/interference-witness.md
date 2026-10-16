# Interference witness for balloon-pinned pods

A per-sample check that a benchmark pod pinned by the NRI balloons policy
(`k3d-gpu.io/cores=performance|efficient`) actually ran alone on its cores. For
each sample window it reports:

- **P-residency**: the share of the pod's CPU time that ran on `cpu_core` CPUs.
- **Foreign share on S**: CPU time on the balloon CPUs S that belonged to anything
  other than the pod's cgroup.
- **Foreign share on siblings**: the same quantity on the SMT siblings of S that
  are outside S.
- **Coverage**: the share of the pod's total CPU time (cgroup `cpu.stat`) that ran
  on the monitored CPUs. A low value means the pod ran outside its balloon.

Each sample is then accepted or rejected by a rule set that is fixed before the
run. The rule set's SHA-256 goes into the result bundle, and a verifier recomputes
every metric and verdict from the raw counter text.

Status: **prototype, simulation only.** Live benchmarks were running on the host,
so nothing in this document was measured under load. Section 6 lists the live
probes.

## 1. Scope cut (refuter objection accepted)

The merged idea had three legs. Two are deferred:

- **PL1 budget theft — dropped for this host.** Read-only sysfs check on
  2026-10-16: `intel-rapl:0` long_term = short_term = 117000000 µW, window
  55967744 µs; `intel-rapl-mmio:0` limits are 0. The `claude/invent-tseries-envelope`
  branch measured the same values and found the active limiter is current ("Amps")
  or thermal, not PL1. The "PL1 limit bit set" trigger would never fire here. The
  per-core-type power split also cannot be identified from client RAPL, which gives
  one PP0 domain for P- and E-cores together, so the counterfactual "solo-PL1
  frequency" would have no ground truth.
- **Foreign DRAM bandwidth — deferred.** `uncore_imc_free_running_{0,1}` exist on
  this host. But own traffic estimated as LLC/offcore misses × 64 B leaves out
  hardware-prefetch fills, writebacks and non-temporal stores, and the difference
  of two error-carrying numbers has no ground truth here.

What is left is leg 1 (core-type residency plus per-CPU foreign time on S and its
SMT siblings), tested against the **fair baseline** the refuter named: a
cpuset-overlap scan that prorates every other cgroup's `cpu.stat` usage over the
CPUs its `cpuset.cpus.effective` shares with S and with S's siblings.

The witness has information the baseline lacks in one case only: when a
co-tenant's cpuset is wider than the CPUs it actually used. With balloons this is
the usual case, not an edge case:

- the `shared` balloon uses `shareIdleCPUsInSame: system`, so unlabelled pods get a
  cpuset of all idle CPUs, and that can include the SMT sibling of a pinned P-core
  CPU;
- host processes outside k3d have a cpuset of every CPU.

When a co-tenant's cpuset equals S (a second pod in the same balloon), the
baseline is exact, and the witness should gain nothing. The simulation checks
both cases.

## 2. Pre-registered acceptance criteria

Written and committed before any witness or simulation code. Thresholds are not
tuned afterwards. A failure is reported as a failure.

Simulation setup: one benchmark pod with |S| = 4 P-core CPUs (two SMT pairs, or
four CPUs from four cores, by scenario), 1 s windows. The witness gets only text
in the formats the live collector reads: `perf stat -j` lines, `/proc/stat`
snapshots (10 ms ticks) and the cgroup `cpu.stat`. The baseline gets only
`cpu.stat` and `cpuset.cpus.effective` text for every cgroup. The ground-truth
throughput comes from hidden per-CPU placement that neither of them sees.

| ID | Criterion | Pass if |
|---|---|---|
| A1 | Attribution arithmetic | noiseless streams: foreign share on S and on siblings, P-residency and coverage recovered within 1e-6 |
| A2 | Explains the throughput deficit, skewed placement | holdout R² (OLS on witness features) ≥ 0.80 **and** ≥ baseline R² + 0.20 |
| A3 | Ablation: localisation does the work | witness with the per-CPU busy vector permuted across all host CPUs (same totals) loses ≥ 0.20 R², ending ≤ baseline + 0.10 |
| A3b | Ablation: siblings term | SMT-spinner scenario: dropping the siblings feature costs ≥ 0.20 R² |
| A4 | Honest null | co-tenant cpuset = S (same-balloon pod): \|witness R² − baseline R²\| ≤ 0.10 |
| A5 | Robustness | A2 holds in ≥ 90% of a grid over SMT slowdown β ∈ {0.15, 0.30, 0.45}, cpu.weight ratio ∈ {0.5, 1, 2}, measurement jitter ∈ {0, 1, 3}% |
| A6 | Gate quality | accepted-sample throughput CoV ≤ 0.5 × CoV of all samples; flagging of interfered windows (true deficit ≥ 3%) TPR ≥ 0.90 at FPR ≤ 0.05, baseline gate reported at the same rules |
| A7 | Verifier | any change to a raw sample, a rule threshold or a stored verdict is detected; recomputation from raw is byte-identical |

Kill criterion (live, section 6): if an SMT-sibling spinner on the sibling of a
pinned P-core costs < 3% of the pinned workload's throughput on this host, the
siblings leg has no value for that workload. The witness then ships only as a
residency and coverage check.
