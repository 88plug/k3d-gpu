# Hybrid-split joulemeter (simulation prototype)

Per-pod energy meter for Intel hybrid client CPUs: RAPL core fitted online
(windowed NNLS) on separate `cpu_core` / `cpu_atom` cycle and instruction
columns, RAPL uncore on i915 `drm-engine-*` time x GPU clock, and pkg_rest on
memory traffic. Each domain is rescaled to its measured delta. `joulemeter.py`
holds the meter and the sysfs/procfs readers; nothing here runs on a cluster.

Status: **simulation only. The pre-registered headline claim is refuted.**
Not deployed, and there is no DaemonSet manifest. The perf-group runtime is
not written, so a manifest would have nothing to run.

## Reproduce

    python tests/joulemeter_test.py -v      # 18 unit tests, < 1 s
    python tests/joulemeter_sim.py          # full campaign, one core, ~3 min
    python tests/joulemeter_sim.py --quick  # 3 seeds, ~25 s

Acceptance criteria: `tests/joulemeter_prereg.py`, committed before the meter
and the simulator.

## Result (10 seeds x 1500 windows of 200 ms; MAPE of per-pod total vs Shapley truth)

| scenario         | meter (hybrid:rail) | best SmartWatts-style | SW, package-freq buckets | static-gamma | Kepler |
|------------------|--------------------:|----------------------:|-------------------------:|-------------:|-------:|
| pinned P/E/iGPU  | 10.8%               | 9.4% (own-freq)       | 27.8%                    | 7.4%         | 72.3%  |
| floating         | 10.2%               | 8.0%                  | 9.7%                     | 5.7%         | 38.5%  |
| pinned, 35 W cap | 10.4%               | 18.7%                 | 28.6%                    | 7.2%         | 73.2%  |

Pre-registered verdicts: C3 (<=10% and <=0.5x SmartWatts) FAIL in both
scenarios. C7 sweep: ratio <=0.5 in 0/40 draws. Static-gamma comes in below
the 20% it was predicted to exceed (FAIL). Kepler >=35% PASS. Capped meter
<=15% PASS. Falsification controls PASS: permuting the E-counters raises MAPE
to 36.7%, and counters from an unrelated run give 56.0%, so the fit does use
the counters.

What the numbers say:
* The hybrid PMU split beats SmartWatts only when SmartWatts buckets by
  package frequency (about 3x). Bucketing each cgroup by its own
  APERF/MPERF-style frequency, computed from summed cycles / ref-cycles,
  matches the split exactly when pods are pinned. A pod pinned to one core
  type exposes its type through its frequency.
* The hybrid split clearly helps only under the power cap and with two iGPU
  clients (about 1.5-1.9x over the best SmartWatts variant), still short of
  the 2x bar.
* A solo-calibrated static energy-per-cycle per core type (static-gamma)
  beats every online fit on run totals.
