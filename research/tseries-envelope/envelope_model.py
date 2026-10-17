#!/usr/bin/env python3
"""envelope_model.py — model of the Intel package power envelope and the run-length rules it implies.

Reads the RAPL package limits from /sys/class/powercap (no root needed; only energy_uj is
root-only), and prints how long a benchmark must soak before it records and how long it must
then record, so it reports sustained rather than burst numbers. See research/tseries-envelope/README.md.

  python3 research/tseries-envelope/envelope_model.py [--powercap DIR] [--zone intel-rapl:0] [--idle-w W]
                                  [--thermal-tc-s S]

Model: Intel documents Tau as "an averaging constant used for PL1 exponential weighted moving
average (EWMA) power calculation" (13th-gen datasheet vol. 1, Package Power Control). The
package may draw up to PL2 while the EWMA is below PL1, then is held at PL1. With A(0)=a0 and a
constant draw P > PL1, A(t) = P + (a0 - P) e^(-t/Tau), so the cap lands at
t* = Tau ln((P - a0) / (P - PL1)). When PL1 >= PL2 there is no burst phase at all.
"""
import argparse
import json
import math
import os
import sys

# A soak of 3 Tau puts the PL1 EWMA within e^-3 (5 %) of its final value.
SOAK_TAUS = 3
# Record for at least 2 Tau so a steady-state check can compare two halves that each cover a
# full averaging constant, and never less than a minute.
MEASURE_TAUS = 2
MIN_MEASURE_S = 60.0
# Even with no RAPL burst the cooler warms up; "RAPL in Action" (Univ. Helsinki) recommends a
# two-minute warm-up before RAPL measurements. Replace with 3x the measured thermal time
# constant once it is known for the host (research/tseries-envelope/README.md, probe P3).
THERMAL_SOAK_FLOOR_S = 120.0


def time_to_pl1(pl1, pl2, tau, a0, demand=None):
    """Seconds of burst before the PL1 cap engages, for a constant demand starting from EWMA a0."""
    p = pl2 if demand is None else min(demand, pl2)
    if p <= pl1:
        return math.inf
    if a0 >= pl1:
        return 0.0
    return tau * math.log((p - a0) / (p - pl1))


def simulate(demand_w, pl1, pl2, tau, a0, seconds, dt=0.01):
    """Euler-step the EWMA controller; returns [(t, granted_watts, ewma_watts)]."""
    out = []
    a = a0
    t = 0.0
    while t <= seconds + 1e-12:
        p = min(demand_w, pl2) if a < pl1 else min(demand_w, pl1)
        out.append((t, p, a))
        a += (p - a) * dt / tau
        t += dt
    return out


def freq_ratio(p_avail, p_burst, exponent=3.0):
    """Sustained/burst clock ratio if dynamic power scales as f^exponent (f*V^2 with V ~ f)."""
    return min(1.0, (p_avail / p_burst) ** (1.0 / exponent))


def steady(series, window, rel_tol):
    """(ok, drift): drift is the relative change of the last window's mean vs the one before."""
    if len(series) < 2 * window:
        return False, math.nan
    prev = series[-2 * window:-window]
    last = series[-window:]
    m1 = sum(prev) / window
    m2 = sum(last) / window
    drift = (m2 - m1) / m1
    return abs(drift) <= rel_tol, drift


def plan(pl1, pl2, tau, thermal_tc_s=None):
    """Soak and record durations (seconds) for one benchmark phase on this envelope."""
    rapl_binding = pl2 > pl1
    tc = max(tau if rapl_binding else 0.0, thermal_tc_s or 0.0)
    return {
        "rapl_binding": rapl_binding,
        "soak_s": max(SOAK_TAUS * tc, THERMAL_SOAK_FLOOR_S),
        "measure_s": max(MEASURE_TAUS * tau, MIN_MEASURE_S),
    }


def _read(path):
    with open(path) as f:
        return f.read().strip()


def read_limits(powercap="/sys/class/powercap", zone="intel-rapl:0"):
    """PL1/PL2/Tau/TDP of a RAPL zone, in watts and seconds."""
    z = os.path.join(powercap, zone)
    names = {}
    for i in range(3):
        try:
            names[_read(os.path.join(z, f"constraint_{i}_name"))] = i
        except FileNotFoundError:
            pass
    lt, st = names.get("long_term"), names.get("short_term")
    if lt is None:
        raise FileNotFoundError(f"{z}: no long_term constraint")
    lim = {
        "zone": zone,
        "name": _read(os.path.join(z, "name")),
        "enabled": _read(os.path.join(z, "enabled")) == "1",
        "pl1_w": int(_read(os.path.join(z, f"constraint_{lt}_power_limit_uw"))) / 1e6,
        "pl2_w": (int(_read(os.path.join(z, f"constraint_{st}_power_limit_uw"))) / 1e6
                  if st is not None else math.inf),
        "tau_s": int(_read(os.path.join(z, f"constraint_{lt}_time_window_us"))) / 1e6,
        "tdp_w": None,
    }
    try:
        lim["tdp_w"] = int(_read(os.path.join(z, f"constraint_{lt}_max_power_uw"))) / 1e6
    except (FileNotFoundError, OSError, ValueError):
        pass  # the file reads ENODATA on some zones
    return lim


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--powercap", default=os.environ.get("ENERGY_POWERCAP", "/sys/class/powercap"))
    ap.add_argument("--zone", default="intel-rapl:0")
    ap.add_argument("--idle-w", type=float, default=0.0, help="package EWMA before the run starts")
    ap.add_argument("--thermal-tc-s", type=float, default=None,
                    help="measured cooler time constant (probe P3), if known")
    args = ap.parse_args(argv)
    try:
        lim = read_limits(args.powercap, args.zone)
    except FileNotFoundError as e:
        print(f"envelope_model.py: {e}", file=sys.stderr)
        return 2
    out = dict(lim)
    burst = time_to_pl1(lim["pl1_w"], lim["pl2_w"], lim["tau_s"], args.idle_w)
    out["burst_s_from_idle"] = None if math.isinf(burst) else round(burst, 3)
    out.update(plan(lim["pl1_w"], lim["pl2_w"], lim["tau_s"], args.thermal_tc_s))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
