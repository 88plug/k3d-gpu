# Joule Ledger (prototype)

Per-span energy for agent and MCP traces, with error bars that come from the
counters themselves.

Energy counters do not update at the rate you poll them. RAPL publishes about
every millisecond. NVML total energy publishes on a slower, SKU-dependent
cadence, and nvidia-smi power samples cover only part of runtime on A100/H100
(Yang et al., arXiv 2312.02741, SC'24). A 10 ms poller that treats every poll
as a fresh reading reports short spans with false precision.

The ledger keeps only the polls where a counter's value changed (edges). Each
edge records the time window in which the reading was taken. From those edges
it returns, for any span:

- `window_lo_joules` / `window_hi_joules`: a bound on device energy over the
  span. It relies only on the counter being non-decreasing and on a configured
  publish-latency bound.
- `measured_fraction` = lo / hi.
- `joules`: the span's share of dynamic energy. Idle power goes to an idle
  bucket. The rest of each update interval is spread over the part where spans
  are in flight, split by weight. Energy with no span in flight goes to
  `unattributed`. Nothing is smeared silently.

CPU P/E split: P- and E-cores share one VccIA rail and one PP0 counter on
Alder/Raptor Lake, so joules-per-cycle per core type are not constants under
DVFS. `pe_split()` fits the constant-coefficient model on low- and
high-frequency strata of a window and refuses the split if the coefficients
move. Spans then carry `k3dgpu.energy.cpu.split=unidentified`.

## Files

| file | what |
|---|---|
| `ledger.py` | `FreshCounter` (edges, bounds, trim), `attribute`, `estimate_idle_power`, `pe_split` |
| `processor.py` | `enrich_span`: the logic of the collector-side `k3dgpu_energy` processor |
| `sampler.py` | RAPL sysfs zones, NVML energy via ctypes, DRM fdinfo, cgroup cpu.stat |
| `daemon.py` | 10 ms poll loop + HTTP (`/v1/domains`, `/v1/bounds`, `/v1/edges`) |
| `../../share/joule-ledger.yml` | DaemonSet + headless Service (not deployed by the launcher) |

Not built yet: the Go OTel Collector component (`processor.py` is its reference
logic), per-cgroup perf_event cpu_core/cpu_atom sampling, and the published image.

## Tests

```
python3 -m pytest -q tests/joule_ledger
python3 tests/joule_ledger/sim.py          # scored against tests/joule_ledger/prereg.py
```

## Live probes (run only on an idle host)

1. NVML energy publish period and jitter per GPU: poll
   `nvmlDeviceGetTotalEnergyConsumption` at 1 ms for 60 s idle and 60 s loaded;
   histogram the gaps between value changes.
2. Publish latency bound: drive a square-wave load with known edges, then find
   the largest delay between a load edge and the counter's slope change. The
   daemon's `--latency` must be at least this value. In the sim (true latency
   drawn in [0, 25] ms), worst-draw coverage is 1.000 with a 25 ms bound, 0.962
   with 5 ms and 0.941 with 0 (`sim.py --ledger-latency`).
3. RAPL zones present: `package-0`, `core`, `uncore`, `psys` on the i9-13900T
   (read from `/sys/class/powercap/*/name`; no DRAM zone).
4. P/E gate on real PP0: run balloons-pinned P-only, E-only and mixed loads
   under the default governor, then again with frequencies pinned. Record
   `pe_split()` accept/refuse and the coefficient drift.
5. Reconciliation: compare package + NVML against psys and a wall meter (none
   on this host yet) to see how much energy no counter attributes.
6. fdinfo on the UHD 770 (i915): `drm-engine-render` ns per OVMS pod, read
   from inside the k3d node's PID namespace.

## Sim result (seed 20261016, 40 draws): FAIL as pre-registered

Thresholds in `tests/joule_ledger/prereg.py` were committed before any run and
were not changed.

| criterion | result | value |
|---|---|---|
| C0 conservation (unit check, not evidence) | pass | max gap 7.8e-16 |
| C1 bound coverage >= 0.999, every draw | pass | worst draw 1.000 |
| C1 ablation (freshness off), short-span coverage < 0.80 | pass | 0.256 |
| C2 median per-span APE <= 0.20 and Kepler-5s >= 2x worse | pass | 0.193 vs 0.539 (Kepler-1s 0.699) |
| C3 Kepler double count >= 1.3 at concurrency >= 2; ledger 1.0 | pass | 2.65 (17 draws); ledger dev 4e-15 |
| C4 confidence means something (hi-conf APE <= 0.15, rho >= 0.3) | **fail** | 0.206, rho 0.14 |
| C5 P/E gate | **fail** | see below |
| C6 C1/C2/C4 each pass in >= 80% of draws | **fail** | 1.00 / 0.325 / 0.05 |

What survived: the counter-freshness bound. It contains true window energy in
every span of every draw, and removing it (ablation) drops short-span coverage
to 26%. Kepler-style "integrate the pod series over the span" double counts
2.65x under concurrency; the ledger conserves.

What failed, and why (exploratory diagnosis after the scored run):
- Per-span point error tracks the spread of per-request demand (Spearman 0.88
  across draws; every other perturbed parameter |rho| <= 0.2). Concurrent spans
  in one process are split by equal weight because no per-request activity
  signal exists. This is the GPU twin of the P/E objection: the share of a
  shared counter among concurrent consumers is not identifiable from the
  counter. `measured_fraction` bounds the window, not the share, so it cannot
  predict this error (C4).
- C5: the gate behaves as designed (refuses 92-100% of coupled-rail DVFS windows,
  accepts 100% of fixed-frequency per-type-rail windows, accepted error 0.009).
  But the pre-registered prediction that the naive constant-coefficient split
  errs >= 20% on a coupled rail was wrong: median 0.122 (0.104-0.156 over 10
  CPU seeds). The gate also refuses per-type rails under DVFS (error 0.124
  there too), so it detects DVFS, not rail coupling. Coupled rail with pinned
  frequency: accepted, error 0.020.

Span energy is therefore shippable only as a bound (`window_lo/hi_joules`) plus
the pod-level total. Per-span point joules for concurrent spans need a
per-request activity signal and stay experimental.
