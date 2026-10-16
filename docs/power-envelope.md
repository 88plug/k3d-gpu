# Power envelope: i9-13900T on this host

How much CPU and iGPU throughput the package can sustain, and how long every
benchmark in this repo has to run before it reports a number that holds.

**Main finding.** This host does not run the T-series 35 W envelope. Firmware sets
PL1 = PL2 = 117 W with a 56 s window. A 35 W PL1 with a burst above it would sag
after about 20 s, but with PL1 = PL2 RAPL never forces that drop. Throughput on
this host is limited by something else:

- At 73–100 W package power the package already reads 79–91 °C. The thermal
  throttle point (TCC) is 95 °C.
- The perf-limit-reasons MSR shows a non-power limiter as active: bit 8, which
  turbostat calls "Amps".
- Since boot (23 days), the same MSR has logged PROCHOT and thermal events.

So sustained numbers on this host fall off over the cooler's thermal time
constant, which is not yet measured. They do not fall off at τ. If the firmware
limits are ever reset to Intel's specification (35 W / 106 W), the τ cliff comes
back, and a 30 s benchmark would overstate sustained compute-bound throughput by
about 1.5×.

The duration rules in [section 5](#5-measurement-duration-rules) cover both
cases. [`bench/envelope_model.py`](../bench/envelope_model.py) computes them from
whatever limits the host has.

Status: the envelope values in section 1 are **measured** (read-only register
and sysfs reads). The predictions in section 4 are **pre-registered and
unmeasured**: the host was running live benchmarks, so no load could be applied.
Section 7 lists the probes that will confirm or falsify them.

## 1. Measured envelope (read-only, 2026-10-16)

Host: Lenovo ThinkStation P3 Ultra (`30HACTO1WW`, BIOS S0JKT1BA 2024-03-07),
i9-13900T (family 6, model 0xB7, stepping 1, microcode 0x133), kernel 6.6, `intel_pstate`
active, HWP on, EPP 0 (power-profiles-daemon `performance`), turbo enabled.

| Quantity | Value | Source |
|---|---|---|
| Processor Base Power (TDP) | 35 W | `MSR_PKG_POWER_INFO` 0x614 = 0x118 × 1/8 W; `constraint_0_max_power_uw` = 35000000; Intel ARK |
| Intel Maximum Turbo Power | 106 W | Intel ARK (i9-13900T specifications) |
| **PL1 (long_term)** | **117 W**, enabled, clamp on | 0x610 low = 0x00df83a8; `intel-rapl:0/constraint_0_power_limit_uw` = 117000000 |
| **τ (PL1 window)** | **55.97 s** | 0x610 bits 23:17 → 2^15 × 1.75 × 1/1024 s; `constraint_0_time_window_us` = 55967744 |
| **PL2 (short_term)** | **117 W**, enabled, clamp off, 2.44 ms | 0x610 high = 0x004283a8; `constraint_1_*` |
| PL4 / VR current | 230 (turbostat "PKG Limit #4: 230 W"); 0x601 = 0x730 | turbostat decode of `MSR_VR_CURRENT_CONFIG` |
| Limit lock | **unlocked** (bit 63 = 0) | 0x610 |
| MMIO (MCHBAR) package limit | disabled (0 W) | `intel-rapl-mmio:0`, enabled = 0 |
| PP0 (core) / PP1 (GT) / DRAM limits | disabled | 0x638/0x640/0x618 = 0; core/uncore zones `enabled=0` |
| psys (platform) | disabled; short_term 223 W | `intel-rapl:1`; 0x65C |
| TjMax / TCC offset / throttle point | 100 °C / 5 °C / **95 °C** | `MSR_IA32_TEMPERATURE_TARGET` 0x1A2 = 0x05641400 |
| P-core turbo by active cores | 1–2: 5.3, 3–4: 5.1, 5–6: 5.0, **7–8: 4.3 GHz** | `MSR_TURBO_RATIO_LIMIT` 0x1AD = 0x2b2b323233333535, groups 0x1AE = 0x0807060504030201 |
| Favoured (TBMT3) cores | CPUs 8–11 (cores 16, 20): 5.3 GHz; the rest 5.1 GHz | `cpuinfo_max_freq`, `acpi_cppc/highest_perf` 68 vs 65 |
| E-core turbo | **3.9 GHz** (single value) | `MSR_SECONDARY_TURBO_RATIO_LIMIT` = 0x27 |
| Base / min-efficiency clock | P 1.1 GHz, E 0.8 GHz / 0.8 GHz | `MSR_PLATFORM_INFO` 0xCE; `base_frequency` |
| cTDP levels | none (`CONFIG_TDP_LEVEL_1/2` = 0, control locked) | turbostat |
| Uncore clock range | 0.8–4.7 GHz | turbostat |
| iGPU (UHD 770, 32 EU) RPn / RP1 / RP0 | 300 / 700 / **1650 MHz**; min/max/boost = 300/1650/1650 | `/sys/class/drm/card0/gt/gt0/rps_*` |
| iGPU throttle reasons | all 0 (also sampled while the iGPU ran at 1650 MHz) | `gt/gt0/throttle_reason_*` |
| DRAM | 4 × 32 GB DDR5-4800, 2 DIMMs per channel, **configured 3600 MT/s** → 57.6 GB/s peak | `dmidecode -t memory` |
| RAPL domains with energy counters | package-0 (includes iGPU), core, uncore, psys; no DRAM domain (turbostat RAMWatt 0.00) | powercap tree |
| dGPUs (separate power budgets) | RTX 4000 SFF Ada 70 W, RTX 2000E Ada 50 W | `nvidia-smi` |

`MSR_CORE_PERF_LIMIT_REASONS` (0x64F) reads 0x19030100 on every read: once by
`rdmsr` and 20 times by turbostat over 40 s. Decoded with the bit layout
turbostat uses:

- **Active:** bit 8 ("Amps").
- **Logged since boot:** PROCHOT, ThermStatus, Amps, PkgPwrL2, MultiCoreTurbo.
- The PkgPwrL1 bit is not set, either active or logged.

Caveat: turbostat enables this decode only on Haswell-era platforms. The same
layout on Raptor Lake is assumed, not verified against the SDM. Clearing the
log bits needs an MSR write, so none was done.

### Passive observation under the live load

The host was already running other sessions' benchmarks: load average 3–15 and
package power 73–100 W. turbostat was sampled 20 × 2 s with per-CPU busy-weighted
clocks
(`/tmp/claude-1000/wave6/tseries/passive.txt`):

| Phase | PkgWatt | CorWatt | GFXWatt | PkgTmp | P busy (thread-eq) / clock | E busy / clock | iGPU |
|---|---|---|---|---|---|---|---|
| iGPU busy (12 samples) | 89.0–99.5 | 64–74 | 9.7–9.9 | 82–87 °C | 4.2–6.5 / 3.61–3.87 GHz | 2.2–6.9 / 3.01–3.13 GHz | 1650 MHz, RC6 0 % |
| iGPU idle (7 samples, after 1 transition sample) | 73.5–83.6 | 60–70 | 0.07–0.12 | 79–91 °C | 2.6–3.5 / 3.79–4.12 GHz | 1.5–5.2 / 3.14–3.29 GHz | 700 MHz, RC6 92–95 % |

What these samples show:

- **Non-core, non-GT power** (system agent, ring, memory controller, IO) is
  PkgWatt − CorWatt − GFXWatt = **13.5–15.6 W** under load. This is the floor
  any core or iGPU budget sits on top of.
- **iGPU at RP0 costs about 9.7 W.** It held 1650 MHz in all 12 samples, with
  the package at up to 99.5 W and every GT throttle reason at 0.
- **Clocks stayed far below their ceilings** even though package power never
  came near 117 W:
  - P-cores: 3.6–4.1 GHz (ceiling 4.3–5.3 GHz).
  - E-cores: 3.0–3.3 GHz (ceiling 3.9 GHz).
  - `IA32_PERF_STATUS` sampled 40 × 32 CPUs (`vf.txt`): no E-core above
    3.2 GHz, no P-core above 4.1 GHz. Core voltage read 1.03–1.17 V.
  - **The limiter on this host is not RAPL.** Candidates, in order:
    1. the bit-8 electrical / "other" limit;
    2. thermal headroom near the 95 °C TCC, because the hottest core is what
       sets the limit;
    3. HWP choosing lower clocks for partly busy threads.

  Probe P1 tells these apart.

### Negative results

These were tried and are reported so nobody repeats them:

1. **Fit of core power vs Σ busy × f³ per core type:** R² = 0.38 and an
   intercept of 57 W. This is not identifiable. Busy% is not activity: the load
   mixes spin-waiting with SIMD work. Per-type power coefficients stay
   **unmeasured**, so the model below takes them as inputs and does not invent
   them.
2. **Fit of PkgTmp vs PkgWatt over 2 s samples:** r = 0.32. PkgTmp is the
   hottest-core sensor and follows local power density, not package power. The
   cooler's thermal resistance and time constant cannot be identified
   passively. That is probe P3.
3. **Shared core-voltage rail:** the reads cannot test it. Reading 0x198 one CPU
   at a time is not simultaneous across CPUs, so the claim that the E-cores
   inherit the P-cores' voltage stays a hypothesis (E4).

## 2. Model

**RAPL (Intel-documented mechanism).** τ is "an averaging constant used for
PL1 exponential weighted moving average (EWMA) power calculation" (13th-gen
datasheet vol. 1, Package Power Control). The package may draw up to PL2 while
the EWMA A(t) is below PL1, and is held to PL1 after that. Take a constant
demand P > PL1, starting from A(0) = a₀:

    A(t) = P + (a₀ − P)·e^(−t/τ)      →   t* = τ·ln((P − a₀)/(P − PL1))

So:

- When PL1 ≥ PL2, or the demand stays at or below PL1, there is **no burst
  phase and no τ sag** (t* = ∞).
- A soak of 3τ puts the EWMA within e⁻³ ≈ 5 % of steady state.
- An idle gap g at idle power aᵢ refills burst credit:
  A = aᵢ + (PL1 − aᵢ)·e^(−g/τ), and the next t* follows from that A.

**Clock from power (assumption, labelled).** Dynamic power ∝ f·V² with V
roughly proportional to f gives f_sustained / f_burst ≈ (P_avail / P_burst)^(1/3).
Here P_avail is the budget left after the measured 14 W non-core floor and the
iGPU's 9.7 W, if the iGPU is running. This is a textbook first-order model and
is not fitted to this silicon (negative result 1). Use it only for the size of
a sag, and replace it with probe P1/P2 data.

**Thermal.** The package is held to 95 °C. The time to reach it depends on the
cooler's R_th·C_th, which is not yet known (P3). The 2.44 ms PL2 window has no
effect on any benchmark here.

**Throughput ceilings**, which are the upper bounds any measurement must fall
under:

- P-cores: 8 × 4.3 GHz all-core, 5.0–5.3 GHz with 1–6 active.
- E-cores: 16 × 3.9 GHz.
- iGPU: 32 EU × 1.65 GHz.
  - Intel lists Xe-LP throughput per EU per clock as FP32 8, FP16 16,
    INT8 32 (DP4A) ops, counting an FMA as one op.
  - That gives 32 × 8 × 2 × 1.65 GHz = **845 GFLOPS FP32**, 1.69 TFLOPS FP16
    and 3.4 TOPS INT8 (counting MACs as 2 ops).
- DRAM: 57.6 GB/s peak, the same in every power state.

Memory-bound phases, such as LLM token decode, are capped by DRAM bandwidth and
not by clock. That holds for both the CPU and the iGPU, which shares the same
DRAM.

### Envelope numbers the model gives

These are computed by `bench/envelope_model.py` and the snippets in section 7.

| Scenario | Burst before cap (t*) | Sustained / burst clock (compute-bound) | Notes |
|---|---|---|---|
| **This host** (117/117 W, τ 56 s) | ∞ (no RAPL burst) | 1.0 from RAPL; thermal unknown (P3) | `rapl_binding: false` |
| Intel spec (35/106 W, τ 56 s), start idle a₀ = 0 / 5 / 15 W | 22.4 / 19.7 / 13.9 s | CPU-only 0.61 | burst credit 1593 / 1401 / 987 J |
| Intel spec, CPU + iGPU at RP0 | same | **0.52** for the cores | the iGPU's 9.7 W comes out of the 21 W left for cores at PL1 |
| Intel spec, 10 s idle gap after a sustained phase (aᵢ = 5 W) | 3.7 s | — | 2 s gap: 0.8 s; 60 s gap: 13.7 s |

Cold-start bias in the Intel-spec case (compute-bound, reporting the whole run
from idle, with the clock ratio of 0.61). This is the overstatement that a
fixed-count benchmark would report:

| Run length | 10 s | 30 s | 60 s | 120 s | 300 s | 600 s |
|---|---|---|---|---|---|---|
| Reported ÷ sustained | 1.64× | 1.48× | 1.24× | 1.12× | 1.05× | 1.02× |

## 3. Pre-registered acceptance criteria (this study)

| ID | Criterion | Status |
|---|---|---|
| AC1 | Every limit in section 1 traces to a captured raw read (hex value or sysfs file), with no value taken from memory | **met**: raw values are in the table and in the appendix |
| AC2 | Model unit tests pass: the closed-form t* matches the Euler simulation within 0.05 s; PL1 = PL2 never caps; the EWMA settles at PL1; demand below PL1 passes through; the steady-state detector flags a 30 % sag and passes a flat series; the plan floors and maxima hold; sysfs parsing works on a fake tree | **met**: 20/20 (`python3 -m unittest bench/test_envelope_model.py`), ruff clean |
| AC3 | Every prediction in section 4 has a numeric falsifier and an exact probe in section 7 | **met** |
| AC4 | The predictions are confirmed or refuted on the quiet host | **open**: no load allowed during this wave |

**Tuned baseline** for the duration rule:

- MLPerf Inference uses a fixed `min_duration` of 600 s in every scenario
  (`mlperf.conf`: `*.Offline.min_duration = 600000`).
- "RAPL in Action" recommends a 2-minute warm-up.

On this host 600 s already exceeds 10τ, so MLPerf's rule is sufficient for
RAPL. It differs from the rule here in two ways:

- It does not detect thermal drift inside the window.
- It does not adapt to a host whose τ, or thermal time constant, is longer.

The rule in section 5 runs shorter where that is safe: 120 s soak + 112 s
record here. It also adds a steady-state gate that the fixed 600 s lacks.
Whether that gate rejects runs the 600 s rule would accept is what probe P5
measures. Until then the claim "more correct than MLPerf's rule" is **not**
established.

## 4. Predictions for the throughput studies (H2: P/E-pinned CPU, H3: iGPU)

Each prediction is falsifiable and unmeasured until section 7 runs.

| ID | Prediction | Falsified if |
|---|---|---|
| E1 | With PL1 = PL2 = 117 W, a compute-bound P-pinned run (16 threads, CPUs 0–15) shows **no step near τ = 56 s** | mean Bzy_MHz over [τ, 3τ] is >3 % below [10 s, τ) **while** PkgWatt < 115 W and PLR bits 10/11 (PkgPwrL1/L2) are clear |
| E2 | An all-core run (32 threads) is held by **temperature, not RAPL**: PkgTmp reaches 95 °C, PLR ThermStatus (bit 1) goes active, PkgWatt settles **below** 117 W, and throughput drifts down over minutes | the run holds PkgWatt ≥ 115 W with PkgTmp < 95 °C for 600 s |
| E3 | P-only, 8 threads (one per core), clock ≤ 4.3 GHz; P-only on 2 favoured cores (CPUs 8, 10) reaches 5.3 GHz in the burst | any sustained Bzy_MHz > 4.3 GHz with 8 cores active (the MSR ceiling would be wrong) |
| E4 | The E-core clock depends on P-core activity: E-only (CPUs 16–31) with P idle reaches ≥ 3.5 GHz, but drops toward the 3.0–3.2 GHz seen in the mixed load once P-cores are busy (shared-rail / electrical-limit hypothesis) | E-only clock with P idle is within 3 % of the clock with P busy |
| E5 | **iGPU holds RP0 (1650 MHz) at full CPU co-load** on this host (passively seen at ≤ 99.5 W). Throttle starts only if PkgTmp hits 95 °C | the iGPU act freq falls below 1650 MHz with PkgTmp < 95 °C and package < 117 W |
| E6 | Memory-bound LLM **decode** tok/s moves < 5 % between the burst window and the sustained window. Compute-bound **prefill / TTFT** carries any sag | decode sags > 5 % while prefill does not |
| E7 | Counterfactual (only if limits are set to Intel spec 35/106 W, probe P6): a cold-start 30 s compute-bound run overstates sustained throughput by ≈ 1.48×, and the cap lands at t* ≈ 20 s from idle | measured t* is outside 10–35 s, or the bias is outside 1.3–1.65× |

What H2 and H3 must report: the **sustained** number, from the measure window
after a soak, as the headline. The burst number (first 10 s, from idle) goes in
a separate field. Both studies run on the same package. Any P-vs-E or CPU-vs-iGPU
ratio taken from short runs mixes regimes: a 10 s E-core run can sit in burst
while a 10 s P-core run on a hot package does not. Such a ratio is invalid
unless both sides are soaked.

## 5. Measurement-duration rules

These apply to every benchmark in the repo. `python3 bench/envelope_model.py`
prints `soak_s` and `measure_s` for the host it runs on.

| Envelope | soak_s | measure_s |
|---|---|---|
| This host | 120 s (thermal floor; RAPL not binding) | 112 s |
| Intel 35/106 W spec | 168 s | 112 s |

1. **Soak before recording.** Run the same workload, unrecorded, for at least
   `soak_s = max(3·τ if PL2 > PL1, 3·τ_thermal, 120 s)`. Set
   `--thermal-tc-s` once probe P3 has measured τ_thermal.
2. **Record for a time, not a count.** Record for at least
   `measure_s = max(2·τ, 60 s)` **and** at least the requested sample count.
   The headline is the mean over this window. Report the first 10 s from a
   cold start separately as `burst_*`, and never as the headline.
3. **Steady-state gate.** Split the record window into halves. If the throughput
   of the second half differs from the first by more than 3 %
   (`steady(series, window, 0.03)`), extend by one more `measure_s`. Stop at
   600 s total, the MLPerf `min_duration`. If the run is still not steady at
   that point, mark it `steady: false`.
4. **No idle gaps between configs.** On a host where PL2 > PL1, an idle gap of
   g seconds restores burst credit: 10 s of idle buys about 3.7 s of burst on
   the Intel spec. Thermal mass refills the same way. Keep the load continuous
   between configs, or soak again after each gap.
5. **Record the envelope with every result:**
   - the `envelope_model.py` JSON;
   - `MSR_CORE_PERF_LIMIT_REASONS` before and after the run
     (`sudo rdmsr -p 0 0x64F`);
   - PkgTmp minimum and maximum;
   - PkgWatt mean over the record window;
   - iGPU act freq (`gt/gt0/rps_act_freq_mhz`);
   - the 1-minute loadavg at the start.

   A result taken while other work is drawing package power is a
   shared-envelope result, and has to say so. During this study, other sessions
   alone held the package at 73–100 W.
6. **Quote decode and prefill separately.** Decode is DRAM-bound, so it changes
   little with the envelope (E6). Prefill and TTFT are compute-bound and carry
   the sag.

### Per-benchmark application

| Benchmark (branch) | Today | Required change |
|---|---|---|
| `bench/llm_load.py` (`claude/report-bench`) | count-based: 8 requests per phase, 1 warm-up request | Add `--soak-s` (unrecorded requests until `soak_s` has elapsed) and `--min-seconds` (keep issuing until `measure_s` and `--requests` are both met). Apply the steady gate to per-request tok/s. Emit `burst_*` from a cold first request separately. A 1-request warm-up is a JIT/cache warm-up, not a power soak |
| `bench/energy.sh` (`claude/report-bench`) | averages the whole wrapped command | Wrap only the record phase, or compute J/token from the record-window samples only. A whole-run average folds the soak (or burst) power into the efficiency figure. The 5 s interval is fine: the counter wraps after ~40 min at 100 W |
| `bench/noisy.sh` (`claude/report-bench`) | starts a stress-ng pod pinned to P or E cores | The neighbour heats the shared package, so the interference it causes includes power and thermal coupling, not only cache and memory contention. Soak **both** arms: baseline with no neighbour, and with the neighbour running for `soak_s` before recording. Log PkgWatt and PkgTmp in both arms |
| successive-halving tuner (`claude/autotune`, not readable from this worktree) | short early rungs | Early rungs shorter than `measure_s` favour configs that burst. Make the minimum rung ≥ `measure_s` on a soaked package, keep the load continuous across configs (rule 4), and randomise config order per rung, so that thermal drift is not confounded with config identity |
| H2 / H3 throughput studies | — | Rules 1–6. Headline = sustained. Compare P vs E and CPU vs iGPU only between soaked runs |

## 6. Provenance partition

| Mechanism / claim | Partition | Evidence |
|---|---|---|
| PL1 EWMA over τ, PL2 short limit | **known** | Intel 13th-gen datasheet vol. 1 (743844), "Package Power Control" and "Turbo Time Parameter (Tau)" |
| Time-window encoding 2^Y·(1+F/4)·unit | **known** | Linux `drivers/powercap/intel_rapl_common.c` `rapl_compute_time_window_core` (cites SDM vol. 3B 14.9.3). Hand decode of 0x610 matches sysfs to the µs |
| 35 W / 106 W / 5.1 / 3.9 / 5.3 GHz / UHD 770 1.65 GHz | **known** | Intel ARK, i9-13900T specifications |
| Xe-LP ops per EU per clock | **known** | Intel oneAPI GPU optimization guide, "Intel Xe GPU Architecture" |
| 0x64F bit names | **known for Haswell-era; assumed for RPL** | turbostat.c `print_perf_limit()` (enabled only on platforms with `plr_msrs`) |
| Warm-up ≥ 2 min for RAPL measurements | **known** | "RAPL in Action: Experiences in Using RAPL for Power Measurements" (Univ. Helsinki) |
| Fixed 600 s min run | **known** | MLCommons `inference_policies/inference_rules.adoc`; `mlcommons/inference` `mlperf.conf` |
| Steady-state detection by comparing consecutive windows | **known** (general benchmarking practice) | e.g. Georges et al., OOPSLA 2007; not re-searched in this wave |
| f ∝ (P)^(1/3) clock model | **known textbook first-order model; not fitted here** | negative result 1 |
| **Deriving soak and record lengths at run time from the host's own RAPL τ / PL1 / PL2 (plus a thermal floor), with an idle-gap burst-credit rule** | **no prior art found** | Search log: Exa query "benchmark harness reads RAPL PL1 time window tau from powercap to set warm-up duration before measuring sustained throughput" (2026-10-16) returned the kernel RAPL driver, raplcap, Weaver's RAPL page, Raffin & Trystram (TPDS 2024) and "RAPL in Action", none of which derives run length from τ. One search engine, one query: weak evidence of absence |
| E-cores inheriting P-core voltage (shared rail) | **unsearched** | hypothesis E4 only |

## 7. Live probes (run on a quiet host only)

**Gate for every probe:**

- No other benchmark is running.
- `cat /proc/loadavg` 1-minute value < 1.
- 60 s of idle PkgWatt has been recorded as a₀.
- One probe at a time. Every probe here is read-only on the platform, except P6.

```bash
IMAGE=ghcr.io/colinianking/stress-ng:72a9d0cae0f420a887991d4024d3101c4af7a4fc@sha256:2ea9a7c3828eda1b1ca3d0b0c5a669ec252f9caf97a6a33aedcea6b2a66d1cbe  # pin from bench/noisy.sh
TS="turbostat --quiet -i 1 --add msr0x64F,u64,package,raw,PLR --show Package,Core,CPU,Busy%,Bzy_MHz,PkgWatt,CorWatt,GFXWatt,PkgTmp,GFXMHz,GFX%rc6"

# P0 idle baseline (a0)
sudo $TS -n 60 > p0-idle.tsv

# P1 (E1, E3) P-cores, 16 threads, 300 s
sudo $TS -n 320 > p1.tsv & sleep 10
docker run --rm --cpuset-cpus 0-15 --entrypoint stress-ng "$IMAGE" --cpu 16 --cpu-method matrixprod --timeout 300 --metrics-brief
wait
#   then: 8 threads on even CPUs only (one per core), and 2 threads on CPUs 8,10
docker run --rm --cpuset-cpus 0,2,4,6,8,10,12,14 --entrypoint stress-ng "$IMAGE" --cpu 8 --cpu-method matrixprod --timeout 120 --metrics-brief

# P2 (E4) E-cores alone, then E-cores with P-cores loaded
sudo $TS -n 320 > p2.tsv & sleep 10
docker run --rm --cpuset-cpus 16-31 --entrypoint stress-ng "$IMAGE" --cpu 16 --cpu-method matrixprod --timeout 300 --metrics-brief
wait

# P3 (E2, thermal time constant) all 32 threads for 600 s; fit PkgTmp(t) = Tinf - dT*exp(-t/tc)
sudo $TS -n 640 > p3.tsv & sleep 10
docker run --rm --entrypoint stress-ng "$IMAGE" --cpu 32 --cpu-method matrixprod --timeout 600 --metrics-brief
wait
python3 bench/envelope_model.py --thermal-tc-s <fitted tc>

# P4 (E5) iGPU act freq and GFXWatt during an iGPU inference run, alone and with P3's load
sudo intel_gpu_top -J -s 1000 -o p4-gpu.json &   # stop with kill %1 after the run
#   run bench/llm_load.py against the OVMS GPU endpoint with --soak-s/--min-seconds (rule 1-3)

# P5 (E6, tuned baseline) llm_load.py on the OVMS CPU device: cold 30 s run vs soaked run vs fixed 600 s;
#   compare the decode and TTFT means, and whether the steady gate rejects any 600 s run

# P6 (E7) NEEDS OPERATOR APPROVAL - writes the platform power limits; restore 117 W / 117 W after
#   echo 35000000  | sudo tee /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw
#   echo 106000000 | sudo tee /sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw
#   repeat P1 from idle; expect the cap near t* = 20 s
```

**Analysis per probe** (all stdlib):

- Throughput proxy = busy-weighted Bzy_MHz per core type. stress-ng
  `matrixprod` is compute-bound, so ops ∝ clock.
- E1: split the record into [10 s, τ) and [τ, 3τ) and apply E1's falsifier.
- E2: report the first time PkgTmp ≥ 95 °C, the PLR ThermStatus bit, and the
  settled PkgWatt.

## Appendix: raw reads

Read with `sudo rdmsr -0` on CPU 0 / CPU 16. The values were identical on both
CPUs unless both are shown.

```
0x606 RAPL_POWER_UNIT      00000000000a0e03   (1/8 W, 61 uJ, 1/1024 s)
0x610 PKG_POWER_LIMIT      004283a800df83a8
0x614 PKG_POWER_INFO       0000000000000118
0x0CE PLATFORM_INFO        08080838f0810b00
0x1A2 TEMPERATURE_TARGET   0000000005641400
0x1AD TURBO_RATIO_LIMIT    2b2b323233333535
0x1AE TURBO_RATIO_CORES    0807060504030201
0x650 SECONDARY_TURBO      00000027           (turbostat)
0x601 VR_CURRENT_CONFIG    0000000000000730
0x638/0x640/0x618          0 / 0 / 0
0x65C PLATFORM_POWER_LIMIT 000086f800dd0000
0x64F CORE_PERF_LIMIT_RSN  0000000019030100
0x771 HWP_CAPABILITIES     cpu0 01120e41 (later 010e0e41) / cpu16 010e0827
0x774 HWP_REQUEST          cpu0 410b / cpu16 2708   (EPP 0)
sysfs intel-rapl:0  long_term 117000000 uW / 55967744 us, max 35000000 uW; short_term 117000000 uW / 2440 us; peak_power 230000000 uW
sysfs intel-rapl-mmio:0 enabled=0, limits 0
```

Scratch captures are in `/tmp/claude-1000/wave6/tseries/`: `msr.txt`,
`turbostat-debug.txt`, `passive.txt`, `vf.txt`.
