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

## 3. Prototype

| File | What it does |
|---|---|
| [`bench/witness.py`](../bench/witness.py) | collector (`record`), metrics, rule judge, bundle builder, verifier (`verify`), `plan` (prints the perf command), `rules-hash` |
| [`bench/witness-rules.json`](../bench/witness-rules.json) | the shipped rule set. SHA-256 `2dafbb60d30dfec2aceac5ecf1d6c6301a355a7ce9b7efe6780d0ed96507d42e` |
| [`bench/witness_sim.py`](../bench/witness_sim.py) | hidden-placement simulator, two baselines, ablations, negative control |
| [`bench/test_witness.py`](../bench/test_witness.py) | 20 unit tests: parsers, hand-computed metrics, tamper detection, `record` against a fake sysroot and fake perf, and the sim criteria |

Per window the collector runs one command (from `witness.py plan` on this host,
S = 0,2,4,6):

```
perf stat -a -A -j -C 0-7 -e cpu-clock,cpu-clock -G <pod-cgroup>, -- sleep 1.0
```

The first `cpu-clock` is scoped to the pod cgroup and gives the pod's on-CPU msec
per CPU. The second has an empty cgroup (the trailing comma) and counts the whole
window per CPU. `/proc/stat` and the cgroup's `cpu.stat` are read just before and
just after. Every share is a per-CPU fraction over its own clock (busy ticks /
total ticks; pod msec / window msec), so the few ms of perf start-up that the
`/proc/stat` window also contains cancel out.

Flag and format facts checked against primary sources, not guessed: `-G` binds
cgroups to events by position and accepts an empty entry
(`tools/perf/util/cgroup.c` `parse_cgroups`, perf-stat(1) on this host, perf 7.2.3);
`-j` lines carry `"cpu"`, `"counter-value"`, `"event"` and `"cgroup"` (empty when the
event has none) (`tools/perf/util/stat-display.c` `print_cgroup_json`,
`print_aggr_id_json`); perf stat writes its counters to stderr. The `perf -j` JSON
keys have not yet been seen on this host's perf binary: that is live probe L0.

## 4. Results (simulation, 2026-10-16)

`python3 bench/witness_sim.py`, n = 2000 windows, seed 1, nominal cell β = 0.30,
weight ratio 1, jitter 1%. Same pass/fail result for seeds 2, 3 and 4.

Deviations from section 2: every scenario used S = 0,2,4,6 (one hyperthread per
core). The two-SMT-pair variant was not simulated, because its siblings set is
empty and the siblings leg does nothing there. The mixed scenario uses a 6-CPU
cpuset (0,2,4,6,16,17) with 4 pod threads. Baseline B2 and the extra runs were
added after pre-registration and are labelled as such.

| ID | Result | Pass |
|---|---|---|
| A1 | max abs error 6.8e-7 over 200 noiseless windows (all five scenario kinds) | yes |
| A2 | holdout R²: witness **0.998**, B1 cpuset-overlap **0.700** | yes |
| A3 | per-CPU busy permuted across CPUs: witness R² 0.998 → **0.179** (B1 0.700) | yes |
| A3b | SMT-spinner windows: witness 0.993, without the siblings feature **−0.006** | yes |
| A4 | same-balloon pod only: witness 0.999, B1 1.000 | yes |
| A5 | 27/27 grid cells pass (witness 0.985–0.999, B1 0.627–0.706) | yes |
| A6 | throughput CoV 0.118 all → 0.0031 accepted; TPR 1.00, FPR 0.018 (B1 at the same rules: TPR 0.88, FPR 0.13) | yes |
| A7 | tampered raw, verdict and relaxed rule (with and without re-hash) all caught; clean bundle recomputes | yes (unit tests) |

**What these numbers do and do not show.**

1. **Most of the win is from per-CPU `/proc/stat`, not from perf.** B2 is a stronger
   baseline added after pre-registration. It takes per-CPU `/proc/stat` busy time on
   S and on the siblings and subtracts the pod's total `cpu.stat` usage, with no perf.
   It scores R² 0.923 overall. Within each scenario kind it ties the witness on SMT
   spinner (0.993 vs 0.993), host spill (0.998 vs 0.997) and same-balloon (1.000 vs
   0.999), and it gates as well (TPR 1.00, FPR 0.054). The witness pulls ahead only in
   the **mixed** scenario, where the balloon cpuset spans P- and E-cores: witness
   0.998, B2 −0.023, B1 −0.026. That is the one place where per-CPU pod time from
   `perf -G` carries information that nothing else provides. So perf earns its place
   for residency and coverage only. The co-tenant legs can run on `/proc/stat` alone
   and need no perf privileges.
2. **B1 loses because cgroup v2 has no per-CPU usage.** It can only prorate a
   co-tenant's usage over that co-tenant's cpuset. Under skewed placement this gives
   R² 0.70. Under uniform placement (extra run) it reaches 0.86, and the witness lead
   falls to 0.14, below the pre-registered 0.20 margin. The A2 pass therefore depends
   on co-tenants actually landing unevenly. Whether they do on this host is live probe
   L3.
3. **The simulator assumes the physics.** The CFS share cap, the SMT slowdown β and
   the E/P speed ratio are all inputs. The sim shows that the arithmetic and the
   information flow are right. It does not show that the deficits are as large as
   assumed. With 2% throughput noise instead of 0.3%, witness R² is 0.973, B1 0.684,
   B2 0.905.
4. **Negative control.** An E-core co-tenant that slows P-core threads only through
   the shared ring/LLC (Chips and Cheese report about 20% L3 bandwidth loss from E-core
   activity) produced deficits up to 4.8% (mean 1.9%). The witness accepted 100% of
   those windows, and its R² was −0.004. This is the expected blind spot. It also
   checks that the sim does not leak truth into the witness. The deferred DRAM/uncore
   leg is what would cover this case.
5. **No shared state.** The witness receives only the six raw text fields, and B1/B2
   receive only `usage_usec` and `cpuset` per cgroup. A unit test asserts both key sets.
   The placement, the per-CPU truth and the throughput never leave the generator.

## 5. Where it plugs in

The witness reads one cgroup path and a CPU list, so it needs no k3d-gpu changes:

- `kubectl get pod <p> -o jsonpath='{.metadata.uid}'` gives the pod UID. On the host,
  the pod's cgroup is below the k3d node container's cgroup (cgroup v2), e.g.
  `system.slice/docker-<node-id>.scope/kubepods/.../pod<uid>`.
- S is that cgroup's `cpuset.cpus.effective`.
- Run `witness.py record ... --expect performance` for the length of the benchmark
  and keep the bundle next to the result. `witness.py verify --rules-sha256 2dafbb60…`
  checks it later.

Balloons fact that makes the siblings leg matter: the shipped policy
(`share/nri-balloons.yml`) gives the `shared` balloon `shareIdleCPUsInSame: system`.
A performance pod that asks for an odd CPU count, or that gets one hyperthread per
core, can therefore have its SMT sibling in the shared balloon. Whether balloons
allocates whole cores on this host is live probe L2.

## 6. Live probes (not run: live benchmarks on the host)

Host: i9-13900T, P CPUs 0–15 (SMT pairs 0-1 … 14-15), E CPUs 16–31, cgroup v2,
`perf_event_paranoid` = 2 (so `record` needs root or CAP_PERFMON).

| ID | Probe | Confirms / kills |
|---|---|---|
| L0 | `sudo perf stat -a -A -j -C 0-3 -e cpu-clock,cpu-clock -G <any-cgroup>, -- sleep 1` | `-j` keys and per-CPU lines as parsed; empty-cgroup event counts about 1000 ms per CPU |
| L1 | k3d-gpu up; pod `k3d-gpu.io/cores=performance` requesting 4 CPUs running a CPU decode (llama.cpp `-t 4`) or `stress-ng --cpu 4 --cpu-method matrixprod`; `witness.py record --windows 60` while idle | baseline: all windows accepted, coverage ≥ 0.95, P-residency 1.0 |
| L2 | `cat cpuset.cpus.effective` of that pod | does balloons hand out whole cores, or single hyperthreads with the sibling in `shared`? |
| L3 | SMT spinner: `taskset -c <sibling of S> stress-ng --cpu 1` on the host, toggled on/off every 10 s | sibling_share flags on-windows; **kill criterion**: throughput loss < 3% means the siblings leg has no value for this workload |
| L4 | host spill: unpinned `stress-ng --cpu 8` toggled | foreign_share flags the windows where it lands on S; compare against B1 and B2 computed from the same host's cgroup files |
| L5 | same-balloon second pod with the same label | A4 live: B1 is exact; witness agrees |
| L6 | E-core load: pod `cores=efficient` running `stress-ng --cpu 16 --stream` | negative control live: the witness should accept, and any throughput loss measured is the ring/LLC effect it cannot see |
| L7 | mixed cpuset: lower the reserved P-cores so the performance balloon spills onto E-cores | residency below 0.98 flags; the throughput deficit should track (1 − P-residency) × (1 − E/P speed) |

## 7. Provenance

**Known (prior art found):**
- Per-CPU busy time on pinned CPUs minus the container's cgroup usage as a
  noisy-neighbour check: Incus forum thread (2026), which is baseline B2 as practice.
  [discuss.linuxcontainers.org/t/27309](https://discuss.linuxcontainers.org/t/container-cpu-shows-high-sy-us-in-proc-stat-zabbix-alert-while-its-cgroup-shows-1-usage-pinned-cores-shared-with-a-noisy-neighbor-recommended-isolation-and-monitoring-setup/27309)
- SMT siblings in a benchmark idle gate: tensor4all/tprims-rs issue #79 / PR #80
  (siblings from `thread_siblings_list`, checked before and after each measurement);
  orieg/expanse #659 / #680 (one sibling per core vs both).
- Culprit attribution of CPU contention per cgroup: SchedBlame, arXiv 2609.02052
  (eBPF, 2026); "Detecting Noisy Neighbors in CPU-Isolated Cgroups Environments",
  ICPE 2025, doi:10.1145/3676151.3719376 (eBPF scheduling latency); CPI2 (EuroSys
  2013); Heracles (ISCA 2015).
- Hybrid residency from cpu_core/cpu_atom PMUs with `perf -G`: perf Alder Lake hybrid
  series (Jin Yao / Kan Liang, 2021); perf-stat(1).
- Pre-registered, hashed acceptance rules recomputed by a verifier: common practice
  (e.g. Mathews-Tom/archex PR #683; arXiv 2608.26183 on evidence-bundle verifiers).
- PL1 budget theft (dropped leg): Guliani & Swift, EuroSys 2019.

**No prior art found:** one per-sample validity gate for a balloon-pinned pod that
combines per-CPU foreign time on the pod's CPUs and on their SMT siblings (outside
the cpuset) with pod residency by core type, measured per CPU by `perf -G` within a
mixed P/E cpuset, plus coverage, all recomputable from a stored bundle. Every part
has prior art. What remains is integration for k3d-gpu benchmark bundles, and the
sim says only the residency part adds information beyond `/proc/stat`.

**Unsearched or failed channels:** Google Patents (not searched); GitHub code search
(HTTP 403 in the earlier provenance pass); arXiv API (empty responses); exa
(rate-limited in this pass). Searches that did run (WebSearch, 2026-10-16):
"detect noisy neighbor on pinned CPUs per-CPU /proc/stat busy minus cgroup usage
benchmark validity"; "SMT sibling hyperthread co-tenant interference detection pinned
container cpuset benchmark harness"; "perf stat -G cgroup per-CPU time P-core E-core
residency hybrid Alder Lake container"; "benchmark result bundle pre-registered
exclusion rules hash verifier recompute sample rejection"; "SchedBlame …";
"verify pod actually ran on P-cores E-cores hybrid Kubernetes nri balloons
preferCoreType residency measurement". These results were read only as search
summaries; the papers were not read in full.
