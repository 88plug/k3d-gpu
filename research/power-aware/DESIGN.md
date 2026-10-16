# Power-cap-aware placement across the P balloon, the E balloon and the iGPU

Status: **design + simulation only. Nothing here has been run on hardware under load.**
Every number in "Results" comes from `powersim.py`, a model whose coefficients are
unfitted priors. The live probes at the end are what turn this into a measured claim.

## 1. Host facts (read-only, measured 2026-10-16 on this box)

| Fact | Value | Source |
|---|---|---|
| CPU | i9-13900T | `/proc/cpuinfo` |
| TDP (`PKG_POWER_INFO`) | 35 W | `intel-rapl:0/constraint_0_max_power_uw = 35000000` |
| **PL1 actually programmed** | **117 W**, tau 56 s | `intel-rapl:0/constraint_0_power_limit_uw = 117000000`, `constraint_0_time_window_us = 55967744` |
| PL2 | 117 W | `constraint_1_power_limit_uw` |
| PL4 (peak) | 230 W | `constraint_2_power_limit_uw` |
| MMIO PL1/PL2 | 0 (unset) | `intel-rapl-mmio:0/constraint_{0,1}_power_limit_uw` |
| RAPL domains | `package-0`, `core` (PP0), `uncore` (PP1 = iGPU on client parts), `psys` | `intel-rapl:0:*/name` |
| P-core freq | 0.8–5.1 GHz, base 1.1 | `cpu0/cpufreq/cpuinfo_{min,max}_freq`, `base_frequency` |
| E-core freq | 0.8–3.9 GHz, base 0.8 | `cpu16/cpufreq/...` |
| E-core clusters | 4 cores each (`16-19`, `20-23`, ...) | `cpu16/topology/cluster_cpus_list` |
| iGPU | UHD 770 (0xa780), i915, 300–1650 MHz | `card0/gt_RPn_freq_mhz`, `gt_RP0_freq_mhz` |
| iGPU throttle reasons | readable without root: `pl1 pl2 pl4 prochot ratl thermal vr_tdc vr_thermalert` | `card0/gt/gt0/throttle_reason_*` |
| Package temp under the current benchmark load | 89 °C | `x86_pkg_temp` zone |
| **Package thermal-throttle time since boot** | **98 939 s (27.5 h) in 23.7 days uptime** | `cpu0/thermal_throttle/package_throttle_total_time_ms` |
| `energy_uj` | root only (0400) | `ls -l intel-rapl:0/energy_uj` |
| `perf_event_paranoid` | 2 (RAPL perf events need CAP_PERFMON) | `/proc/sys/kernel/perf_event_paranoid` |
| thermald | inactive | `systemctl is-active thermald` |
| kernel | 6.6.128 | `uname -r` |

**The task premise is half wrong for this box.** The part is a 35 W-TDP T-series, but the
board programs PL1 = PL2 = 117 W. 35 W is the TDP field, not an enforced limit. The
binding constraint here is thermal: the package has spent ~4.8 % of its uptime in
thermal throttling. So the controller must not assume "cap = 35 W" or "cap = PL1".
It must estimate the *effective* cap online as `min(PL1 EWMA, thermal cap)`, where the
thermal cap is `(T_lim - T_amb) / R_th` and moves with ambient and chassis airflow.
The design below does that.

## 2. Mechanism being exploited

Intel SDM (RAPL chapter, `MSR_PP0_POLICY` 0x63A / `MSR_PP1_POLICY` 0x642): *"On platforms
that support PP0 (IA cores) and PP1 (uncore graphic device), the default values give
priority to the non-IA power plane. These MSRs enable the PCU to balance power consumption
between the IA cores and uncore graphic device."* (text verified via a verbatim
reproduction of the SDM in the PRACE/IT4I RAPL training slides, see provenance; not yet
re-read in the SDM PDF itself, and the value of these MSRs on this part is unread because
`/dev/cpu/*/msr` needs root.)

Consequence: when the package is at its effective cap, turning the iGPU on takes its
power first and every busy core loses frequency. The marginal value of an iGPU replica is
therefore *its own throughput minus the throughput all CPU replicas lose to the lower
clock*. A static placement that was tuned with the iGPU idle, or tuned at a different
ambient, or for a different work mix, ignores that cross term. The controller prices it.

## 3. Controller

Inputs (every 1 s):
- RAPL energy deltas for `package-0`, `core`, `uncore` (`energy_uj` is mode 0400 and the
  RAPL perf events need CAP_PERFMON at paranoid 2, so the controller runs as a privileged
  DaemonSet, like the NRI plugin).
- Average busy frequency of the P balloon CPUs and of the E balloon CPUs
  (`scaling_cur_freq`, or APERF/MPERF via turbostat `Bzy_MHz`).
- `gt_act_freq_mhz` and `throttle_reason_pl1`/`throttle_reason_thermal`.
- Per-pool work rates from the agent stack (OVMS request metrics for inference, tool
  worker completions for CPU work) and the arriving work mix `m` (fraction of agent
  work that is LLM inference).

Plant model the controller fits (same functional form as the simulator, coefficients
unknown and learned online):
- core power `kP·nP·fP³ + kE·nE·fE³`, iGPU power `kG·fG³`, package `= P_static + core + gpu`.
- PCU behaviour: iGPU gets its demand first (PP1 priority), cores share the remainder at
  one common ratio, never below base frequency.
- Work rate per core / per iGPU `a[class,engine]·(f/fmax)^β`; LLM rate capped by a shared
  DRAM bandwidth ceiling `BW`.
- Agent throughput is Leontief: `X = min(L/m, T/(1-m))` (an agent step needs inference
  and tool work in ratio `m : 1-m`).

Online fitting:
- `kG = GFXWatt / fG³` (EWMA) when the iGPU is busy.
- core scale `kP` from `CorWatt / (nP·fP³ + ρ·nE·fE³)`, with `ρ = kE/kP` held at its prior
  (identifiable only from probe 2; documented weakness).
- `P_static = PkgWatt - CorWatt - GFXWatt` (EWMA).
- `a[class,engine]` from observed per-pool rate divided by `n·(f/fmax)^β` when the pool
  is not bandwidth-saturated; `BW` re-estimated when observed LLM rate falls >10 % short
  of the unsaturated prediction.
- Effective cap: when throttled (busy freq below max, or a gt throttle reason set) the cap
  estimate tracks `PkgWatt`; when not throttled it drifts up 0.5 W/s toward PL1, so a
  cooler room is rediscovered.

Decision (every 20 s): exhaustively search the placement grid (P cores 0–8 split into
inference/tool, E cores in steps of one 4-core cluster split the same way, iGPU on/off:
1 350 configs) with the fitted model; switch only when the predicted gain over the
remaining horizon beats the switching cost (CPU pod move 5 s dead time, iGPU model load
30 s) and a 3 % hysteresis. (This is v1, as pre-registered. v2, added after v1 failed AC4,
is described in section 6.)

Actuation in k3d-gpu: five Deployments (`agent-infer-p`, `agent-infer-e`,
`agent-tool-p`, `agent-tool-e`, `agent-infer-igpu`) whose replica counts the controller
sets; pods carry `k3d-gpu.io/cores=performance|efficient` so the existing NRI balloons
policy pins them; the iGPU pod requests `gpu.intel.com/i915`. No cpufreq, EPP or RAPL
writes: the controller only moves work. (Writing `PP0/PP1_POLICY` or PL1 is a possible
second actuator; left out because it needs MSR writes and is outside "move work".)

## 4. Baselines

1. **Naive static**: P 4 inference + 4 tool, E 8 + 8, iGPU on.
2. **Tuned static (the real baseline)**: the single placement that maximises the
   time-averaged throughput over the whole trace, found by exhaustive search *on the true
   plant*. It knows coefficients the controller never sees.
3. **Phase oracle (upper bound)**: best placement per phase on the true plant, free
   switching.

## 5. Pre-registered acceptance criteria (written before the first simulation run)

Monte Carlo over 40 plant draws (seeded, coefficients drawn from the prior ranges in
`powersim.py`); the controller starts from the prior midpoints, so it is mis-specified
against every draw. Scenarios, 1 800 s each: S1 stationary (m 0.5, ambient 25 °C);
S2 mix shift (m alternates 0.8/0.2 every 300 s); S3 thermal drift (ambient 20→40 °C in
steps); S4 both.

- **AC1 no harm**: S1 median controller/tuned ≥ 0.97 and 10th percentile ≥ 0.93.
- **AC2 gain**: in at least 2 of S2–S4, median controller/tuned ≥ 1.05; in all of S2–S4,
  median controller/oracle ≥ 0.85.
- **AC3 beats naive**: median controller/naive ≥ 1.00 in every scenario.
- **AC4 stable**: S1 median reconfigurations after t = 300 s ≤ 3.
- **AC5 mechanism (falsification test)**: an ablated controller whose model ignores the
  iGPU's draw on the shared budget (assumes cores get `cap - P_static`) must be worse than
  the full controller by ≥ 2 % median in S2 or S4. If it is not, the cross-term is not what
  produces the gain and the mechanism claim is withdrawn.

## 6. Results (simulation only)

Reproduce: `python3 powersim.py 40 <seed> <v1|v2>` (stdlib, ~2.5 min on one core);
`python3 -m unittest` (16 tests). Ratios are medians over 40 plant draws. Raw output:
`results-v1-seed6.json`, `results-v1-seed7.json`, `results-v2-seed7.json`.

| run | scenario | ctrl/tuned | p10 | ctrl/oracle | ctrl/naive | ctrl/ablated | tuned/oracle | switches after 300 s |
|---|---|---|---|---|---|---|---|---|
| v1, seed 6 (pre-registered) | S1 stationary | 0.979 | 0.970 | 0.979 | 1.46 | 1.017 | 1.000 | 13.5 |
| | S2 mix shift | 1.152 | 1.021 | 0.945 | 1.30 | 1.001 | 0.816 | 9.5 |
| | S3 thermal drift | 0.983 | 0.972 | 0.980 | 1.49 | 1.016 | 0.996 | 14.0 |
| | S4 both | 1.157 | 1.015 | 0.943 | 1.29 | 1.005 | 0.813 | 9.5 |
| v1, seed 7 (held out) | S1 | 0.982 | 0.972 | 0.982 | 1.40 | 1.016 | 1.000 | 12.5 |
| | S2 | 1.176 | 0.969 | 0.932 | 1.29 | 1.002 | 0.793 | 10.5 |
| | S3 | 0.985 | 0.975 | 0.980 | 1.42 | 1.022 | 0.999 | 12.5 |
| | S4 | 1.169 | 0.965 | 0.934 | 1.29 | 1.004 | 0.788 | 11.0 |
| v2, seed 7 (held out) | S1 | 0.980 | **0.927** | 0.980 | 1.39 | 1.002 | 1.000 | 0 |
| | S2 | 1.157 | 0.949 | 0.912 | 1.28 | 1.004 | 0.793 | 5.5 |
| | S3 | 0.975 | 0.926 | 0.965 | 1.39 | 1.021 | 0.999 | 0 |
| | S4 | 1.151 | 0.967 | 0.916 | 1.27 | 1.007 | 0.788 | 6.0 |

Verdicts against the pre-registered criteria:

| | AC1 no harm | AC2 gain | AC3 vs naive | AC4 stable | AC5 mechanism |
|---|---|---|---|---|---|
| v1 seed 6 (pre-registered) | pass | pass | pass | **fail** (13.5) | **fail** |
| v1 seed 7 | pass | pass | pass | **fail** | **fail** |
| v2 seed 7 | **fail** (p10 0.927 < 0.93) | pass | pass | pass | **fail** |

v2 was designed after v1 failed AC4, tuned on seed 6, then judged on fresh seed 7. It
fixes the churn (a model-mismatch limit cycle: v1 jumped between near-equal placements its
biased model ranked differently) by keeping measured throughput for visited placements and
demanding that an unmeasured one beat its prediction by twice the model's observed error.
The price is lock-in: in the worst decile it settles 7 % below tuned static. **No version
passes all five criteria.** Not iterated further on seed 7, to keep it held out.

What the simulation says, read plainly:

1. **The gain is mix tracking, not power awareness.** Against tuned static the controller
   wins +15–18 % median only when the inference/tool mix moves (S2, S4), because the best
   split of cores between inference and tool work moves with it (tuned static reaches only
   79–82 % of the phase oracle there). Under ambient drift alone (S3) the best placement
   barely changes with the cap (tuned/oracle 0.996–0.999), so the controller can only lose
   (−1.5 to −2.5 %).
2. **AC5 fails: the iGPU cross term is not what produces the gain.** Ablating it costs
   0.1–0.7 % in S2/S4. It costs 1.6–2.2 % in S1/S3, which AC5 did not cover. Exploratory
   (not pre-registered): across 760 phase optima the iGPU is off in only 30 (4 %), and
   those 30 are exactly where the ablated model decides differently. Under these priors the
   iGPU's work per watt (`aLG/kG`) is high enough that "iGPU on" is nearly always right,
   so pricing its draw rarely changes a decision. Probe 3 measures `aLG/kG` for real; if
   the UHD 770 is weaker per watt than the prior, the cross term becomes decisive.
3. **Naive "everything on, split evenly" is 27–49 % worse** than the controller and also
   worse than tuned static. Most of that is the even split, not power.

So: the controller is a mix-tracking placer with an online power model. The power-cap part
is a ~2 % refinement under the priors used. Nothing here is reduced to practice.

Candidate v3 (untested, not run): age out measured entries after N periods so a locked-in
placement is re-challenged; targets v2's AC1 p10 failure. Must be judged on a third fresh
seed, then on hardware.

## 7. Provenance partition

**Known (prior art found and read):**
- Joint partitioning of work *and* power budget between CPU and iGPU on one chip under a
  shared cap; +13 % over work-only partitioning. Wang, Sathish, Singh, Schulte, Kim, PACT
  2012, doi:10.1145/2370816.2370873; extended by Jang et al., IEEE TPDS 2015,
  doi:10.1109/TPDS.2015.2453965.
- Co-run slowdown on integrated CPU-GPU from both power-cap and memory contention, and
  co-scheduling to recover it (9–46 %). Zhu, Wu, Shen, Shen, Wang, IPDPS 2017,
  doi:10.1109/IPDPS.2017.124. The plant's shared-DRAM clamp is this effect.
- Utilisation-driven power shifting between devices under a node cap (APS). Ortega et al.,
  IEEE TC 2022, doi:10.1109/TC.2022.3174545.
- Frequency-sensitivity-driven CPU/GPU power coordination on an AMD APU (DynaCo). Paul et
  al., 2014, doi:10.1155/2014/210762.
- ML-predicted co-scheduling + partitioning + power capping, arXiv:2405.03831 (2024);
  online CPU-GPU power-setting prediction (OPEN), doi:10.1145/3797905.3800526.
- PCU budget split between IA cores and graphics: `MSR_PP0_POLICY` / `MSR_PP1_POLICY`,
  "default values give priority to the non-IA power plane" (Intel SDM text as reproduced
  in PRACE/IT4I 2020 RAPL slides; SDM PDF itself not re-read).
- Linux thermal `power_allocator` governor (IPA): splits a sustainable power budget among
  cooling devices (CPU, GPU) by weights with a PID on temperature
  (docs.kernel.org/driver-api/thermal/power_allocator.html; headings read, body not). It
  actuates frequency, not work placement.
- Intel HFI: per-CPU performance/efficiency capability, updated on power/thermal limit
  changes, relayed by thermal netlink (docs.kernel.org/arch/x86/intel-hfi.html).
- EAS on Intel hybrid: intel_pstate registers an energy model in passive mode with
  schedutil, hybrid platforms without SMT only, merged for 6.16 (linux-pm pull for
  v6.16-rc1; LWN 1020291). **Not usable here**: kernel 6.6, SMT on.
- intel-lpmd: moves work onto the most efficient CPUs via cgroup cpusets by utilisation,
  HFI and, in newer releases, graphics-utilisation detection and a "graphics IA bias" knob
  (github.com/intel/intel-lpmd). Closest host-level relative; it targets idle power, not
  throughput under a cap, and does not place work on the iGPU.
- thermald: DPTF-table-driven power-limit adjustment (github.com/intel/thermal_daemon);
  inactive on this host.
- kubernetes-power-manager: per-pool freq/EPP/C-state profiles; archived 2024-09-09; no
  RAPL budget logic, no iGPU (github.com/intel/kubernetes-power-manager). The
  cluster-power-manager fork adds uncore frequency.
- Telemetry Aware Scheduling power guide: node-level placement away from nodes above 80 %
  TDP using RAPL via collectd (github.com/intel/platform-aware-scheduling). Chooses nodes,
  not engines inside a node.
- Kepler: RAPL energy attribution to pods by CPU-time share; exporter only
  (github.com/sustainable-computing-io/kepler).
- v2's measured memory plus an uncertainty margin for unvisited placements is standard
  bandit / model-based-control practice; no specific citation searched.

**No prior art found (search log below):** a pod-level controller that moves agent
replicas among NRI P/E balloons and the iGPU on one client package, prices the iGPU's
PP1-priority draw as a frequency loss on every CPU replica, and estimates the effective cap
online as min(PL1 EWMA, thermal). Given the list above this is a composition of known
parts, and the simulation says its power-specific part is worth ~2 %.

**Unsearched:** mobile big.LITTLE + GPU scheduling under thermal budgets (Android EAS + IPA
studies) beyond the IPA doc; Intel DTT on Windows/ChromeOS; TjMax of the 13900T (believed
100 °C, not verified; probe 0 reads it).

Search log (2026-10-16): searxng multi-query, 7 queries (power-capped CPU-iGPU scheduling
papers, HFI, kubernetes-power-manager, DTT/DPTF, Kepler, APU power capping, EAS hybrid),
results generic and discarded. Exa: "power-capped CPU-GPU heterogeneous processor ...
workload placement and power shifting"; "kubernetes-power-manager intel github archived";
"Linux kernel intel_hfi ... thermal netlink"; "intel_pstate energy aware scheduling hybrid
... 6.16"; "intel_lpmd ... thermald DPTF"; "MSR_PP0_POLICY MSR_PP1_POLICY priority";
"Kubernetes controller power-cap-aware pod placement between P-cores E-cores and
integrated GPU"; "Kepler ... RAPL". Fetched the kernel power_allocator doc.

## 8. Live probes (not run: live benchmarks occupy the host). Idle host, root.

Common sampler, 1 s, summary plus one P-core and one E-core (columns from `man turbostat`,
turbostat 2026.04.21 installed):

    sudo turbostat --quiet --interval 1 --cpu 0,16 \
      --show CPU,Busy%,Bzy_MHz,PkgWatt,CorWatt,GFXWatt,GFXAMHz,PkgTmp,PKG_%,CoreThr \
      --out "$OUT/turbostat.txt"
    # alongside, every 1 s: /sys/class/drm/card0/gt/gt0/throttle_reason_{pl1,thermal}
    # and /sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_total_time_ms

| # | Probe | Fits | Method |
|---|---|---|---|
| 0 | Thermal target, PCU policy | `T_LIM`, PP0/PP1 priority | `sudo rdmsr -p0 -f 23:16 -d 0x1a2` (temperature target), `sudo rdmsr -p0 -f 29:24 -d 0x1a2` (TCC offset), `sudo rdmsr -p0 0x63a`, `sudo rdmsr -p0 0x642` (may fault if unimplemented; record that). Check the 0x1A2 bit fields in SDM vol. 4 first. |
| 1 | **iGPU load vs CPU frequency under the cap** | sign and size of the cross term; GPU-priority claim | All 24 cores busy (one `taskset -c N` spinner per core), then iGPU load 0 → 1 → 2 → 4 concurrent OVMS GPU decode streams (claude/agent-stack, device GPU), 180 s per step (> 3× PL1 tau). Prediction: once `PKG_%` or a gt throttle reason is set, `Bzy_MHz` on CPUs 0 and 16 falls as `GFXWatt` rises while `GFXAMHz` stays at 1650. If `GFXAMHz` falls first, PP1 priority is not in effect and the plant's PCU rule is wrong. Fit `d f_P / d GFXWatt`. |
| 2 | Per-core-type power | `kP`, `kE`, exponent, `rho` | Spinners on 1, 2, 4, 8 P-cores alone, then 1–4 E-clusters alone, 120 s each; regress `CorWatt` on `n·Bzy_MHz^k`. Gives `rho = kE/kP`, which the online estimator cannot identify. |
| 3 | Work rates | `aL*`, `aT*`, `bL`, `bT`, `bG`, `bw`, `aLG/kG` | `bench/llm_load.py` (claude/report-bench) against inference pinned to P only, E only, iGPU only, then all three (plateau = `bw`); tool-work benchmark the same way; exponents from probe 1's frequency spread. `aLG/kG` decides whether AC5's mechanism can matter on this box. |
| 4 | Effective cap, `R_th` | thermal cap vs PL1 | All-core load 10 min at known ambient; steady `PkgWatt` with `PkgTmp` at target and the throttle counter rising = effective cap; `R_th = (T_target − T_amb)/PkgWatt`. Repeat at a second fan/ambient setting. Expect cap < 117 W given 27.5 h throttled since boot. |
| 5 | End-to-end A/B | AC1–AC5 on hardware | Replay the S2 mix with the real agent stack: tuned static vs controller, 30 min each, ABAB, agent steps/s from the stack's metrics, same thresholds. |

Until probe 5 runs, the claim is: designed and simulated, not reduced to practice.
