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
- RAPL energy deltas for `package-0`, `core`, `uncore` (needs root or CAP_SYS_RAWIO-free
  read of `energy_uj`; run as a privileged DaemonSet, same as the NRI plugin).
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
30 s) and a 3 % hysteresis.

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

## 6. Results

(filled in after the run; see section 6 below once committed)
