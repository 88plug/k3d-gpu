"""Pre-registered acceptance criteria for the Joule Ledger simulation.

Committed BEFORE the ledger, the simulator or any experiment ran.
tests/joule_ledger/sim.py imports this file and scores against it; any
threshold change after seeing results shows up in git history.

Scope: simulation only. A pass means "the mechanism survives a hidden physics
model that includes the refuter's failure modes". It is not a hardware claim.

The design responds to a written objection (2026-10-16):
  * P/E joules-per-cycle are not constants on a shared VccIA rail, so a
    constant-coefficient NNLS split is not identifiable.            -> C5
  * "attributed + residual == total" holds by construction.          -> C0 is a
    unit test only and is NOT counted as evidence.
  * The real comparator is Kepler (node DaemonSet, RAPL + NVML, per-pod
    shares), and the delta is "integrate a pod series over a span".  -> C2, C3
  * NVML energy updates every ~20-100 ms or slower and samples only part of
    runtime, so 10 ms polling of a short span is interpolation.      -> C1, C4
  * The original concurrency claim (concurrent == isolated within 15%) is
    physics-violated on a coupled rail. It is WITHDRAWN, not tested.

Key element under test: counter-freshness tracking. The ledger stores only the
instants where an energy counter's value changed (fresh edges), integrates a
span over those update intervals, and returns a rigorous [lo, hi] bound plus a
measured_fraction, instead of a point estimate that hides interpolation.

Truth convention (stated up front): device dynamic power is split among
in-flight requests in proportion to each request's own instantaneous demand,
after the hidden saturation, lag and phase model. Idle power is NOT attributed
to spans. Untraced background load is NOT attributed to spans.

Sizes: 40 perturbation draws x 1 run each, >= 200 spans per run.
"""

DRAWS = 40
MIN_SPANS = 200

CRITERIA = {
    # C0 conservation, unit test only (by construction, not evidence).
    "C0_conservation_rel_gap_max": 1e-9,
    # C1 bound coverage. Truth = device energy over [span.start, span.end].
    # The ledger's [lo, hi] must contain it in >= 99.9% of spans, given true
    # counter latency <= configured latency.
    "C1_coverage_min": 0.999,
    # C1 ablation: freshness tracking off (every 10 ms poll treated as a fresh
    # sample). For spans shorter than 2x the counter update period its
    # coverage must fall below this, else freshness is not doing the work.
    "C1_ablation_short_coverage_max": 0.80,
    # C2 per-span point error, spans >= 2x update period: median APE of the
    # span's attributed dynamic energy.
    "C2_ledger_mape_max": 0.20,
    # Kepler-style comparator (pod series at Kepler's interval, linear interp,
    # integrated over each span window, same true idle handed to it) must be
    # at least this many times worse.
    "C2_kepler_ratio_min": 2.0,
    # C3 double counting at concurrency >= 2 within one pod: Kepler-style
    # integration sum(span) / pod dynamic energy must be >= this (prediction);
    # the ledger must stay at 1.0 within C0 tolerance.
    "C3_kepler_doublecount_min": 1.3,
    # C4 the confidence field must mean something:
    #   spans with measured_fraction >= 0.8 -> median APE <= 0.15
    #   Spearman(1 - measured_fraction, abs error) >= 0.3
    "C4_hiconf_mape_max": 0.15,
    "C4_spearman_min": 0.3,
    # C5 P/E identifiability gate on a shared rail.
    #   coupled-rail world: constant-coefficient NNLS P/E split error >= 0.20
    #     (the objection holds) AND the gate refuses >= 90% of windows.
    #   decoupled world (per-type rails, control): gate accepts >= 90% of
    #     windows and accepted-window split error <= 0.05.
    "C5_coupled_naive_err_min": 0.20,
    "C5_coupled_refuse_min": 0.90,
    "C5_decoupled_accept_min": 0.90,
    "C5_decoupled_err_max": 0.05,
    # C6 robustness: C1, C2 and C4 must each pass in >= 80% of the DRAWS
    # perturbation draws (update period, jitter, latency inside the configured
    # bound, lag, saturation, phase mix, background load, demand spread).
    "C6_pass_frac_min": 0.80,
}
