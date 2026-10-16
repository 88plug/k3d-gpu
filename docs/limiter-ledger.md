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

Filled in after the run. See the results section of the commit that adds them.
