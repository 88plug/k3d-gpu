# Limiter ledger and fixed-cap A/B

Status: research prototype. Static code, simulation and unit tests only. Nothing here has run on
hardware yet. The live probes are listed at the end.

## What it is

Two parts.

1. **Limiter ledger** (`bench/limiter_ledger.py`). A sidecar in the same pod (and so the same
   NRI balloon) as an LLM decode loop. The client sends one byte-line per decode step over a unix
   socket. At each mark, the sidecar reads the sticky *log* bits of the package limit-reason MSRs,
   clears the ones it saw, and reads APERF/MPERF on the pod's CPUs and the package RAPL energy
   counter. Each decode step therefore carries a limiter bitmask, a busy MHz, a package
   temperature and an energy figure.
2. **Fixed-cap A/B** (`bench/limiter_sim.py`, design `D4`). This compares two configurations
   (P-cores vs E-cores, INT4 vs INT8, and so on). The package power cap is set by the
   experimenter (`PL1 = PL2 = cap`) and treated as a controlled factor. Within one cap the arms
   run in randomized ABBA/BAAB quads. The ledger serves as the manipulation check: it confirms
   that the cap, not some uncontrolled limiter, was what limited each arm.

Part 2 replaces the design this branch was first asked to build, which estimated effects within
strata of the observed limiter state. A refuter objected that the limiter state is *caused* by
the treatment, so stratifying on it conditions on a mediator. The simulation (E4 below) checks
that objection.

## Register facts (primary sources, not paraphrase)

All bit positions are from the *Intel 64 and IA-32 Architectures SDM, Combined Volumes*,
September 2026 edition (`cdrdv2.intel.com/v1/dl/getContent/671200`). The kernel facts are from
`torvalds/linux` master, fetched 2026-10-16.

| MSR | Name | Scope | Bits used here | Source |
|---|---|---|---|---|
| 0x64F | MSR_CORE_PERF_LIMIT_REASONS | **Package** | status 0,1,4-8,10-13; log 16,17,20-24,26-29 | SDM vol. 4 Table 2-39 (6th-13th gen Core) |
| 0x6B0 | MSR_GRAPHICS_PERF_LIMIT_REASONS | **Package** | status 0,1,5-8,10-12; log 16,17,21-24,26-28 | SDM vol. 4 Table 2-39 |
| 0x1B1 | IA32_PACKAGE_THERM_STATUS | Package | 22:16 digital readout (read only) | SDM vol. 4 Table 2-2 |
| 0x606 | MSR_RAPL_POWER_UNIT | Package | 12:8 energy status unit, J = 1/2^ESU | SDM; turbostat.c `rapl_energy_units` |
| 0x611 | MSR_PKG_ENERGY_STATUS | Package | 31:0 energy counter (wraps) | kernel msr-index.h |
| 0xE7/0xE8 | IA32_MPERF / IA32_APERF | Thread | busy MHz = base MHz x dAPERF/dMPERF | SDM Table 2-2 |

Consequences that shape the design:

- **Every limit-reason bit is package scope.** The ledger reports package events while the pod
  ran. It does not attribute them to the pod. The only per-pod quantity is busy MHz, which comes
  from APERF/MPERF on the pod's own CPUs.
- **The iGPU has its own limit-reason MSR (0x6B0).** The ledger reads it, so an iGPU arm has a
  limiter source. An NVIDIA arm does not: its reasons come from NVML
  `nvmlDeviceGetCurrentClocksEventReasons`, which is not implemented here (gap G2).
- **The ledger never writes 0x1B1.** The kernel's `drivers/thermal/intel/therm_throt.c` clears
  0x1B1 bit 1 (and DPTI_ACK, bit 25) from `throttle_active_work`. A second clearer would race
  with it. Thermal events come from 0x64F bit 17 instead. Neither `therm_throt.c` nor
  `turbostat.c` writes 0x64F or 0x6B0. On Alder Lake and Raptor Lake, turbostat does not read
  them at all: `adl_features` sets no `plr_msrs`.
- **Every write through `/dev/cpu/N/msr` taints the kernel.** `arch/x86/kernel/msr.c` calls
  `add_taint(TAINT_CPU_OUT_OF_SPEC)` unconditionally in `msr_write`, and kernel lockdown blocks
  the write entirely. Clearing the log bits is therefore opt-in (`--clear`). Without it the
  ledger samples the *status* bits at each boundary, which needs no write. E1 measures what
  that costs.

## Pre-registered acceptance criteria

These criteria were written and committed before any simulation result existed. The original
claims (a) and (b) are kept as written so they can fail. The refuter's predictions are recorded
next to them.

**E1. Ledger implementation check.** This is not evidence of value; sticky bits catch events by
construction.
- E1.1 With `--clear` and sticky log bits, recall is >= 95% over tokens whose step overlapped
  any limiter event (an injected PL1 drop or sub-step transients). Uninjected runs flag <= 2% of
  tokens.
- E1.2 Ablation, boundary-sampled status bits (no writes): recall on sub-step transients is
  < 50%. If it is >= 50%, the write and taint cost buys nothing.
- E1.3 Ablation, 1 s interval tagging (turbostat style): token-level false-flag rate in the
  injected run is > 20%. If not, per-token alignment adds nothing over interval logging.

**E2. Original claim (a): the ledger as a correction.** Across 10 runs x 512 tokens of P-core
decode, the CV of run medians over clean tokens is <= 0.5x the CV of run medians over all
tokens (warm-up discarded), with >= 50% of tokens kept. Swept over compute fraction
c in {0.2, 0.5, 0.8} and BIOS power-limit configs.
- Refuter's prediction: fails for memory-bound decode (c <= 0.3).
- Decision rule: if E2 fails at c = 0.2, the ledger ships as a diagnostic only.

**E3. Original claim (b): A/A false positives,** 200 trials with a thermal ramp, alpha = 0.05.
- D1 (fixed ABAB, Welch on block means): > 15% false significance.
- D3 (limiter-stratified, Mantel-Haenszel, stratified permutation): <= 5% at >= 70% coverage.
- D2 (randomized ABBA/BAAB, paired sign-flip test) is the refuter's fair baseline. Refuter's
  prediction: D2 <= ~6.5%, which would make D3's advantage over D1 an artifact of an unpaired
  baseline.

**E4. The mediator objection,** with treatments that change package power.
- Refuter's prediction: D3's estimate is biased against the alone-sustained total effect by
  > 2 points, and/or D3's coverage falls below 70% on power-differential pairs.
- Claim for D4: on every scenario where the ledger manipulation check passes,
  |D4 estimate - alone-sustained effect at that cap| <= 1 point, 95% CI coverage >= 90%, A/A
  false significance <= 8% (5% plus Monte Carlo slack over 200 trials), and power >= 0.8 for an
  injected +5% effect.
- D4 ablation (cap PL1 only, PL2 left at stock): bias or CI miss rate increases. If the
  ablation does as well as D4, the `PL2 := PL1` element is not doing the work.
- D4 portability: the D4 estimate is identical (within 1 point) under the stock 35 W and the
  observed 117 W BIOS power-limit configs. D2's default-settings estimate differs by
  > 1 point between them.

Anti-cheat rules for the simulation:
- The estimators see only the observed ledger stream: step wall time, tag bitmask, quantized
  temperature and noisy energy.
- The scoring estimands come from a separate noise-free run of each arm alone and never reach an
  estimator.
- Each conclusion is rechecked under perturbed thermal resistance, Tau, noise and seeds.

## Results

Simulation only. Reproduce with:

    python3 -B bench/limiter_sim.py --trials 200 --seed 1 --table   # E3/E4 + perturbed
    python3 -B bench/limiter_sim.py --only e1                       # E1 (JSON)
    python3 -B bench/limiter_sim.py --only e2                       # E2 (JSON)
    python3 -m pytest -q -p no:cacheprovider bench/                 # 20 tests

Seeds 1 and 2 were both run, with 200 trials per cell (100 per perturbed cell). Seed 1 numbers
are quoted unless a seed-2 number differs in a way that changes a verdict. The model parameters
are illustrative, not measured, so these results show whether the mechanism can work. They do
not show how well it works on a real 13900T.

### Verdicts against the pre-registered criteria

| Criterion | Result | Verdict |
|---|---|---|
| E1.1 sticky + clear recall >= 95%, uninjected <= 2% | recall 1.00, uninjected power/thermal flags 0.0% | PASS (by construction) |
| E1.2 status-only transient recall < 50% | 0.11 (overall recall 0.79) | PASS: the clear buys something |
| E1.3 1 s interval false-flag > 20% | 0.95 | PASS: per-step alignment buys something |
| E2 clean CV <= 0.5x all-token CV with >= 50% kept | host config: ratio 0.84-0.95; stock config: ratio 0.01-0.07 but only 42% kept | **FAIL** at every c |
| E3 D1 (ABAB + Welch) A/A > 15% | 4.0% (seed 2: 3.5-4.0%); 0-5% even with no idle gaps | **FAIL**: the premise of claim (b) does not hold |
| E3 D3 A/A <= 5% at >= 70% coverage | coverage 1.00, rejection 8.0% (seed 2: 5.0-6.0%) | **FAIL** at seed 1 |
| E3 refuter: paired ABBA <= ~6.5% | 4.5-5.5% | Refuter confirmed |
| E4 refuter: D3 coverage < 70% on power-differential pairs | 0.00 on P-vs-E and INT4-vs-INT8, in both configs | Refuter confirmed. D3 never returns a verdict |
| E4 D4 abs(bias) <= 1 point | AA, AA+5, INT4-vs-INT8: <= 0.3. P-vs-E: 0.14 at 25 W, 0.17 at 30 W, **1.21 at 35 W** | Mostly pass. Fails at 35 W |
| E4 D4 95% CI coverage >= 90% | AA: 0.94-0.98. P-vs-E: 0.83-0.90 at 25/30 W, 0.00 at 35 W. INT4-vs-INT8: 0.17-0.47 | **FAIL** |
| E4 D4 A/A false significance <= 8% | 3.0-6.0% default model; up to 9.0% perturbed | Marginal FAIL under perturbation |
| E4 D4 power for +5% >= 0.8 | 1.00 | PASS |
| E4 ablation PL1-only cap is worse | P-vs-E bias 14-18 points (vs 0.1-0.2 for D4) | PASS: `PL2 := PL1` does the work |
| E4 portability, D4 config-independent | D4 identical under stock and host configs at every cap | PASS |
| E4 portability, D2 default differs > 1 point between configs | INT4-vs-INT8: 7.4 points. P-vs-E: 0.0 points | Partial |
| Perturbed R_th, Tau, leakage: D4 bias <= 1 point | leak 0: 0.02; leak 0.3: 0.87; **leak 0.5: 2.72** | **FAIL** at high leakage |

### What survives

1. **The ledger works as a diagnostic and fails as a correction.** Per-step read-and-clear of
   the sticky log bits catches events that boundary status sampling misses (transient recall
   1.00 vs 0.11). It does not false-flag the way 1 s interval tagging does (0% vs 95%). But
   dropping flagged tokens does not stabilise tok/s (E2). On the stock config the "clean" tokens
   are the early PL2 turbo tokens. Their median is stable because it measures a different
   regime, not because it is corrected. Per the pre-registered decision rule, the ledger ships as
   a diagnostic only. This matches the refuter's salvage.
2. **Limiter-stratified estimation (D3) is dead.** The refuter was right. When the arms draw
   different power, they land in different limiter strata, and coverage is 0. In the A/A case,
   where strata match by construction, D3 still does no better than a paired ABBA test.
3. **Claim (b)'s motivating baseline was wrong.** An ABAB order with Welch on a thermal ramp is
   conservative, not anti-conservative, in this model: the ramp inflates the variance more than
   it biases the mean. The 38% false difference in Abedi and Brecht (ICPE'17) comes from
   non-interleaved runs, which nobody here proposed.
4. **The error that matters is regime mismatch, and the ledger exposes it.** This finding was
   not anticipated. Interleaved short blocks with idle gaps keep the PL1 running average below
   35 W, so every block runs in PL2/turbo. On the stock config, paired ABBA reports P-vs-E as
   -25.0% while the sustained effect is -10.5%: the test is precise and 14.5 points wrong, with
   CI coverage 0.00. The ledger shows it directly, because the PL1-tagged fraction in the
   session is 0.00 while sustained use is PL1-bound. A/A tests cannot reveal this, because both
   arms share the regime.
5. **Fixed cap (D4) removes the regime mismatch but leaves a temperature-carryover residual.**
   Setting `PL1 = PL2 = cap` makes the interleaved session and the sustained run share a power
   regime. At 35 W (the stock PL1) D4 gives -11.7% against a sustained -10.5%: 1.21 points off,
   down from 14.5. The residual comes from temperature-dependent leakage. Interleaved blocks with
   idle gaps run cooler than the arm alone, so leakage costs less. With leakage set to 0 the
   residual disappears (`test_d4_residual_is_leakage`: under 0.2 points). The bias is not
   counted in the CI, which is why CI coverage fails. The ledger's manipulation check (no
   thermal or electrical bits) passes in these cases, so it does not catch this residual. A
   live A/B therefore also needs the per-arm package temperature compared against a solo
   sustained run.

The cap-35 W comparison (item 5) and `test_cap_at_stock_pl1_recovers_stock_sustained_effect` were
added after the pre-registered run, so they are exploratory and not confirmatory.

### Known gaps

- G1. The thermal, PL1 and leakage model is illustrative, not fitted to this host. All live
  conclusions wait on probes P3-P5.
- G2. There is no NVML clocks-event-reason source. An NVIDIA arm has no limiter tags.
- G3. The 0x6B0 iGPU limit reasons are decoded but not exercised in the simulation.
- G4. Limit-reason bits are package scope. Two pods on one package see each other's events.

## Live probes (after the running benchmarks finish)

Host: i9-13900T, 8P+16E, UHD 770. Run one at a time.

- P1. `sudo modprobe msr; sudo rdmsr -p0 0x64F; sudo rdmsr -p0 0x6B0; sudo rdmsr -p0 0x1B1`.
  Confirms that the registers exist and decode (`limiter_ledger.py decode <value>`). Then
  confirms, inside the pod, whether `CAP_SYS_RAWIO` without `privileged` can open
  `/dev/cpu/0/msr`.
- P2. Write semantics. `wrmsr -p0 0x64F <clear_value>` after a forced PL1 event, then re-read and
  confirm only the observed log bits cleared. Record the taint (`/proc/sys/kernel/tainted` bit
  for TAINT_CPU_OUT_OF_SPEC).
- P3. Regime check, the central live test of finding 4. Run 10 x 512-token P-core decode of a
  1-3B INT4 model with the ledger, once as a sustained run and once as ABBA blocks with 2 s gaps
  against E-cores. Prediction: PL1-tagged fraction near 0 in the ABBA session and high in the
  sustained run (the host BIOS PL1 of 117 W may prevent this; then repeat at a 35 W cap via
  `/sys/class/powercap/intel-rapl:0/constraint_{0,1}_power_limit_uw`).
- P4. D4 at 35 W and 25 W caps (PL1 = PL2 through powercap, restored afterwards): compare the
  ABBA effect with a sustained solo run of each arm at the same cap. Prediction: within ~1-2
  points, with per-arm package temperature lower in the interleaved session.
- P5. E2 on real hardware: clean-token vs all-token CV. Prediction: fails (memory-bound decode,
  c <= 0.3).
- Named hardware with no probe here: an NPU arm (Core Ultra), a Gaudi or NVIDIA arm (needs the
  G2 NVML source), and Xeon with AMX (limit reasons on server parts use different MSRs or TPMI;
  the bit table above covers 6th-13th gen Core only).

## Provenance

- **Known** (cited above): Intel limit-reason MSRs and their decode (SDM; turbostat; PCM;
  VTune/SoC Watch; the Linux PLR TPMI driver). Per-run throttle-reason rejection
  (pjt222/bare-metal on NVIDIA; asiai, aetherengine-bench and Pipette on Apple/Android).
  Randomized interleaved trials and A/A tests (Abedi and Brecht ICPE'17; zenbench; Google
  Benchmark random interleaving; tango-bench). Turbo history bias (UPenn WDDD'14).
  Experimenter-set power caps through RAPL are routine.
- **No prior art found** (searches in the branch idea's search log, several rate-limited):
  read-and-clear of the 0x64F sticky log bits aligned to individual LLM decode steps over a
  client socket, and the use of a per-step limiter ledger as a regime check on interleaved A/B
  sessions (finding 4). The second is a combination of known pieces, so the obviousness risk is
  high.
- **Unsearched**: GitHub code search (HTTP 403), arXiv (HTTP 429), Google Patents (no usable
  results), ThrottleStop and HWiNFO limit logging, and the crossover-design literature on
  carryover effects. That literature likely already covers the temperature-carryover residual
  in item 5. Re-run these searches before any public novelty claim.
