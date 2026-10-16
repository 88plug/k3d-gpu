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

## 6. Results

(filled in after the run; see section 7)
