# Package-power-coupled placement (per-role turbo arbitration) — pre-registration

Status: **static code + simulation only.** Nothing here has touched cpufreq, EPP,
C-states or RAPL on any host. Live benchmarks were running on the probe host on
2026-10-16, so every live step is listed as a probe and none was run.

This is a controller **v3 delta** on top of `claude/invent-power-aware`
(RAPL PP0/PP1 coupled model, work-moving controller, no frequency writes) and
`claude/invent-agent-topology` (role split). The only new element is *active*
per-role frequency/EPP/turbo arbitration through nri-plugins balloons
`cpuClasses`, plus demotion to the next DRA fallback when the package sits at its
effective cap.

## 1. Idea under test

On one client package, P-cores, E-cores and the iGPU (and on Core Ultra the NPU)
share one package budget, ring and LLC. Proposal:

- (a) **Admission**: admit a joint agent plan only if predicted steady package power
  is `<= 0.9 x effective cap`. Sustained-heavy roles (embedder batches, ASR) go off
  package (dGPU) when a dGPU exists. Bursty latency-critical roles (planner, drafter)
  stay on package.
- (b) **Per-role cpuClasses**: planner balloon `turboPriority: 10`, deep C-states
  disabled; tool-exec and embed-feeder E-core balloons get an explicit `maxFreq` cap
  and a power-favouring EPP; idle CPUs get a powersave `idleCPUClass`. Freed package
  watts are meant to reach the iGPU drafter.
- (c) **1 Hz demotion loop**: when the package sits at the effective cap with a
  throttle reason set for > 5 s, demote the lowest-weight on-package non-planner role
  to its next DRA fallback. The planner is never demoted.

## 2. Corrections to the idea, made before any code

1. **The cap is not 35 W on this host.** `claude/invent-power-aware` read
   `intel-rapl:0/constraint_0_power_limit_uw = 117000000` (PL1 = PL2 = 117 W); 35 W
   is the TDP field. The binding limit is thermal. Admission and demotion key on
   `effective_cap = min(PL1, thermal_cap)`, never on TDP.
2. **"Over PL1 for > 5 s" cannot happen in steady state** — the PCU enforces the
   limit. The trigger is "package power >= 0.97 x effective cap AND a throttle
   reason (`gt/gt0/throttle_reason_pl1|thermal`, or core busy freq below max) for
   > 5 consecutive seconds".
3. **`turboPriority` demotes every other class's symbolic `"turbo"` to base.**
   From the CRD text in the pinned chart (`share/nri-balloons.yml`, v0.14.0):
   *"Among CPU classes with active CPU allocations, only the class with the highest
   turboPriority gets the symbolic frequency "turbo" resolved to the actual turbo
   frequency. All other classes get "turbo" resolved to the base frequency."* On the
   i9-13900T E-core base is 0.8 GHz (`cpu16/cpufreq/base_frequency = 800000`), so a
   tool class left at `maxFreq: turbo` would silently drop from 3.9 to 0.8 GHz. The
   renderer therefore gives every non-planner class an explicit numeric `maxFreq`
   and refuses symbolic `"turbo"` on them.
4. **"C6+ disabled" is not portable.** This host exposes ACPI idle states
   `POLL C1_ACPI C2_ACPI C3_ACPI` (read 2026-10-16 from `cpu0/cpuidle/state*/name`),
   not `C6`. The renderer takes the host's state names and disables every state
   deeper than the shallowest non-POLL state; a hard-coded `[C6, C8, C10]` would
   match nothing here.
5. **The pinned chart already carries the knobs.** `share/nri-balloons.yml` (chart
   v0.14.0, on `main`) has `spec.cpuClasses[]` with `minFreq`, `maxFreq`,
   `energyPerformancePreference`, `turboPriority`, `disabledCstates`,
   `uncoreMin/MaxFreq`, plus `idleCPUClass` and `balloonTypes[].cpuClass`. No chart
   bump. **Not proven:** the DaemonSet runs with `capabilities: drop: [ALL]` and
   `allowPrivilegeEscalation: false`, mounting host `/sys` at `/host/sys` from inside
   the k3d node container. Whether cpufreq/cpuidle writes land from there is probe P0.

## 3. The objection this pre-registration must survive

1. *Strawman topology.* With 2 NVIDIA dGPUs on the host, a tuned baseline puts the
   drafter on a dGPU; an iGPU-drafter gain improves a configuration nobody would pick.
2. *Decode is bandwidth-bound.* A small INT4 drafter on UHD 770 is limited by shared
   DRAM bandwidth; freeing package watts raises the iGPU clock but not tok/s.
   E-core caps also cut E-core DRAM traffic, so any tok/s gain is confounded.
   "iGPU act_freq rises" is not a valid falsifier.
3. *Prior art* (Cooperative Boosting ISCA 2013; SC13; SDM PP0/PP1 policy; Intel DTT /
   Deep Link Dynamic Power Share). Only the k8s/DRA wrapper is new.

The simulator is built to let each of these kill the idea.

## 4. Simulator (`ppcsim.py`)

Plant, drawn per Monte-Carlo trial from prior ranges (all unfitted priors, listed in
`PRIORS` in the code):

- Per-component power `c * n * (f/fmax)^alpha` for P-cores, E-cores, iGPU, plus
  `P_static`. Effective cap 35–65 W (thermal).
- **PCU arbitration**: the iGPU's demand is protected by a fraction `pi` in [0, 1]
  (SDM: default PP0/PP1 policy favours the non-IA plane, magnitude on this SKU
  unread); the remainder is shared by proportional power scaling with frequency floors.
- **Drafter tok/s** = soft roofline of a compute rate proportional to iGPU clock and a
  bandwidth rate `(BW - DRAM traffic of the other roles) / bytes_per_token`. The
  compute/bandwidth ratio `rho` at full clock is drawn log-uniform 0.7–6 (the objection
  says "several times" > 1; the prior spans both sides).
- **Planner step** latency = compute part scaling with `1/f_P` + memory part; p95 from
  bursty tool load (duty cycle) with lognormal jitter.
- **Tool-exec** throughput scales with `f_E^beta` and sits on the agent-turn critical
  path; an E-core cap that slows tools is charged to the turn.
- **Embed-feeder**: sustained E-core load + DRAM traffic.
- Energy per turn = package power x turn time.

Policies:

- **B0 tuned default**: no cpuClasses, best static assignment (drafter on iGPU or
  P-cores; with a dGPU, any role may go there).
- **B1 tuned static cap**: best single static E-core `maxFreq` cap, chosen on the
  *true* plant (an oracle static knob, no controller).
- **FULL**: admission + per-role cpuClasses chosen by the controller's *fitted,
  mis-specified* model (coefficients from noisy synthetic RAPL traces, exponent held
  at the prior midpoint, roofline `rho` at the prior midpoint).
- Ablations: **A-noTP** (caps, no turboPriority), **A-noCap** (turboPriority only),
  **A-cbound** (controller assumes compute-bound drafter, the idea as stated).
- Channel counterfactuals for FULL's chosen cap: **power-only** (cap's power effect,
  E-core DRAM traffic held at uncapped level — the "bandwidth-matched control") and
  **DRAM-only** (traffic cut, power held).

## 5. Pre-registered acceptance criteria (written before the first simulator run)

N = 300 plant draws, seed 11 (pre-registered) and seed 12 (held out).
Scenario D0 = no dGPU (Core-Ultra-laptop-like); D2 = dGPU available.

- **AC1 — claim as stated (D0).** FULL vs B0: median drafter tok/s gain >= +20 %,
  median planner p95 reduction >= 10 %, median energy/turn change <= 0 %.
- **AC2 — controller beats a static knob (D0).** Median turn rate FULL/B1 >= 1.03.
  Otherwise one static cap does the job and the loop is not needed.
- **AC3 — mechanism is power, not DRAM (D0).** Median power-only-channel drafter gain
  >= 0.5 x FULL's drafter gain. Otherwise the gain is DRAM relief and the coupling
  claim is withdrawn.
- **AC4 — scope check (D2).** Predicted: tuned B0 puts the drafter on the dGPU in
  >= 90 % of draws and median turn-rate FULL/B0 <= 1.02, i.e. the idea is irrelevant
  when a dGPU exists. If FULL/B0 > 1.02 in D2, the scope is wider than predicted.
- **AC5 — the idea's own falsifier is invalid if** in > 25 % of D0 draws the iGPU
  clock rises >= 10 % while drafter tok/s rises < 5 %. Then the live falsifier must be
  tok/s plus the bandwidth-matched control, not act_freq.
- **AC6 — safety (unit tests).** Planner never demoted; no demotion inside 5 s; no
  symbolic `"turbo"` on non-planner classes; admission uses `min(PL1, thermal)`;
  rendered policy validates strictly against the pinned CRD schema.
- **Regime rule.** If AC1 passes only in the sub-region `pi < 0.3 AND rho < 1.5`, the
  verdict is "regime-only", not a general win.

**Predictions recorded before running:** AC1 drafter leg fails on the median (the
objection is right for most of the prior); planner leg may pass because E-core caps
free watts for P-cores; AC3 fails; AC4 holds (irrelevant with a dGPU); AC5 triggers.

## 6. Results (simulation only, written after the runs)

Reproduce: `python3 ppcsim.py 300 <seed> <variant>` (stdlib, ~7 s each);
`python3 -m unittest test_pcp` (24 tests, needs PyYAML). Raw output:
`results-seed{11,12}-{variant}.json`.

Process disclosure: a 30-draw seed-1 smoke run came first (to debug the plant). The
five perturbation variants were added after it and before seeds 11/12 were run. They
were not in the pre-registration; they push the prior *toward* the idea so the sim
cannot hide a win in an unlucky prior. `favourable` stacks all four.

D0 (no dGPU) medians, seed 11 pre-registered / seed 12 held out:

| variant | drafter tok/s FULL vs B0 | planner p95 cut | energy/turn | turn FULL/B0 | turn FULL/B1 (static cap) | iGPU-drafter gain at best cap | of which power-only | of which DRAM-only | iGPU clock rise |
|---|---|---|---|---|---|---|---|---|---|
| base | -0.5 / -1.1 % | -4.8 / -5.2 % | +4.6 / +4.6 % | 0.959 / 0.957 | 0.958 / 0.957 | +14.7 / +15.2 % | -1.4 / -1.3 % | +15.3 / +16.0 % | +5.2 / +5.0 % |
| io_tools | +10.0 / +10.4 % | +4.1 / +4.7 % | -3.7 / -4.9 % | 1.025 / 1.033 | 0.968 / 0.967 | +14.7 / +15.2 % | -1.4 / -1.3 % | +15.3 / +16.0 % | +5.2 / +5.0 % |
| no_pp1 | 0.0 / 0.0 % | -6.3 / -7.0 % | +5.7 / +5.5 % | 0.948 / 0.950 | 0.947 / 0.949 | +16.8 / +17.1 % | -1.1 / -0.9 % | +15.6 / +16.5 % | +12.2 / +12.1 % |
| clock_bound | 0.0 / 0.0 % | 0.0 / -1.2 % | 0.0 / +0.1 % | 1.000 / 0.999 | 1.000 / 0.999 | +8.0 / +8.6 % | +2.8 / +2.7 % | +1.8 / +2.0 % | +5.2 / +5.0 % |
| low_alpha | -6.0 / -6.9 % | -7.7 / -8.3 % | +9.0 / +9.0 % | 0.919 / 0.919 | 0.919 / 0.919 | +12.0 / +12.4 % | -1.5 / -1.5 % | +11.4 / +12.8 % | +7.5 / +7.2 % |
| favourable | +7.4 / +7.7 % | +5.1 / +5.8 % | -2.8 / -3.2 % | 1.028 / 1.031 | 0.988 / 0.985 | +18.1 / +17.8 % | +14.3 / +13.7 % | +0.6 / +0.8 % | +18.0 / +17.7 % |

D2 (dGPU present), every variant, both seeds: tuned B0 puts the drafter on the dGPU in
100 % of draws; FULL/B0 turn rate 1.000–1.005. FULL *without* the demotion loop is
0.53–0.78 x B0, because the idea's own admission rule keeps the bursty drafter on the
iGPU; only the demotion loop moves it to the dGPU.

Ablations (D0 base, seed 11): turboPriority-only, as the idea literally wrote it
(lower classes' symbolic `"turbo"` -> base = 0.8 GHz E-cores): 0.54 x B0. Controller
assuming a compute-bound drafter: identical to FULL (it never chose a cap in base).
No-demotion: 1.00 x B0 (FULL's base loss comes entirely from demoting the drafter to
P-cores).

### Verdict against the pre-registered criteria

| AC | result | |
|---|---|---|
| AC1 claim as stated | **FAIL**, every variant, both seeds | best: favourable, drafter +7.7 %, p95 -5.8 %, energy -3.2 % |
| AC2 beats a static cap | **FAIL**, every variant | an oracle static E cap beats the controller by 1–3 % where caps help at all |
| AC3 power channel | **FAIL** in base, io_tools, no_pp1, low_alpha, clock_bound; pass only in favourable | power-only channel is *negative* in the base prior |
| AC4 dGPU scope | **HOLDS** (predicted) | idea irrelevant with a dGPU; harmful without its demotion loop |
| AC5 freq falsifier invalid | **NOT triggered** (prediction wrong) | the iGPU clock itself barely rises (median +5 %), so the idea's own falsifier ("act_freq does not rise") fires and refutes it in most draws |
| Regime rule | n/a | AC1 fails even in the stacked favourable corner |

Predictions vs outcome: AC1-drafter fail, AC3 fail, AC4 hold — as predicted. Planner
p95 leg was predicted to possibly pass; it fails (worse in base, +4–6 % at best).
AC5 was predicted to trigger and did not.

### What the simulator says the mechanism actually is

1. **Freed watts go to the P-cores, not the iGPU.** With proportional PCU sharing and
   any PP1 priority, an E-core cap mostly raises P-core clocks; the P-cores then pull
   more DRAM bandwidth, so the power-only channel is slightly *negative* for a
   bandwidth-bound drafter. Frequency moves as power^(1/alpha), so even a large watt
   transfer is a small clock change.
2. **The real gain from an E-core cap is DRAM relief** (+15 % drafter tok/s in base,
   almost all from cut E-core traffic). That is the objection's confound, confirmed in
   the model. It is a bandwidth-partitioning effect, not package-power coupling, and a
   static cap already captures it.
3. **Tools on the critical path eat the gain.** Capping E-cores slows tool-exec; unless
   tools are I/O-bound (`io_tools`), turn rate drops.
4. **`turboPriority` is a trap on this SKU.** Lower classes' symbolic `"turbo"`
   resolves to base, and E-core base is 0.8 GHz: -46 % turn rate.

**Decision: do not build the placer.** Keep only the pieces that are correct and cheap:
effective-cap math, strict CRD validation of rendered policies, the turbo-trap guard,
host-derived C-state names. The redirected (untested) hypothesis is "E-core caps as a
DRAM-bandwidth throttle for co-located bandwidth-bound decode"; that needs its own
pre-registration and is not claimed here.

## 7. Live probes (none run; benchmarks were live on 2026-10-16)

- **P0 write-path spike.** Apply a one-class `cpuClasses` policy (E cap only) to a
  throwaway k3d cluster; confirm `scaling_max_freq` and `energy_performance_preference`
  on the E-CPUs change on the host, and revert. Unknown: the DaemonSet runs with
  `capabilities: drop: [ALL]` writing the k3d node container's `/sys` via hostPath.
- **P1 the cheap research step (objection's design).** On the 13900T, dGPU unused:
  iGPU INT4 drafter alone vs with P/E load, each with and without an E-core maxFreq
  cap. Record RAPL `package-0`/`core`/`uncore` energy, `gt_act_freq_mhz`,
  `throttle_reason_*`, drafter tok/s. **Bandwidth-matched control:** an E-core load
  that is power-heavy and DRAM-light (L1-resident AVX2 loop) vs power-light and
  DRAM-heavy (STREAM triad on 4 E-cores). Build only if tok/s (not clock) rises
  >= 20 % under the cap with the DRAM-light load.
- **P2 PP1 priority.** Read `MSR_PP0_POLICY` 0x63A / `MSR_PP1_POLICY` 0x642 (root,
  `rdmsr`) to replace the `pi` prior with the programmed value.
- **P3 alpha.** Fit the per-class power exponent from P1's RAPL traces. It sets how
  far a watt transfer moves a clock; `low_alpha` alone did not rescue the claim in sim.
- **P4 Core Ultra.** NPU power sharing and a dGPU-less package: needs Core Ultra
  hardware; not available.

## 8. Provenance partition

- **Shipped:** nri-plugins balloons `cpuClasses` (min/maxFreq, EPP, `turboPriority`,
  `disabledCstates`, uncore freq), `idleCPUClass`, `balloonTypes[].cpuClass` — present
  in the CRD of the pinned chart v0.14.0 (`share/nri-balloons.yml` on `main`) and in the
  v0.14.0 stable docs (fetched 2026-10-16 by the provenance pass). Per-pod
  freq/EPP/C-state control in k8s: intel/kubernetes-power-manager (archived, last push
  2024-09-09), AMDEPYC/kubernetes-power-manager (pushed 2026-09-05). Firmware CPU/GPU
  power balancing: Intel SDM `MSR_PP0_POLICY`/`MSR_PP1_POLICY`; Intel Deep Link
  Dynamic Power Share (Intel support article 000090047).
- **Announced / in flight (not merged):** `claude/invent-power-aware` (RAPL coupled
  model + work-moving controller), `claude/invent-agent-topology` (role split), both
  2026-10-16 in this repo.
- **Paper-only:** throttling the CPU to give an integrated GPU power under a shared
  TDP — Paul et al., "Cooperative Boosting: Needy versus Greedy Power Management",
  ISCA 2013, doi:10.1145/2485922.2485947; Paul et al., "Coordinated energy management
  in heterogeneous processors", SC13, doi:10.1145/2503210.2503227.
- **This branch (sim + static code only):** per-agent-role cpuClasses rendering with a
  turbo-trap guard and strict CRD validation; effective-cap admission; 1 Hz
  hold-then-demote loop over DRA fallback chains; a falsifying simulator whose verdict
  is *refuted* for the stated claim.
- **Host facts used** (read-only, 2026-10-16): idle states `POLL C1_ACPI C2_ACPI
  C3_ACPI`; `intel_pstate` with EPP; P base 1.1 GHz, E base 0.8 GHz, E max 3.9 GHz;
  iGPU RP0 1650 MHz. PL1 = 117 W from `claude/invent-power-aware`, not re-read here.
