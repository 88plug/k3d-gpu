#!/usr/bin/env python3
"""H23 headroom model: package power a node can still admit before it throttles.

Two limits, the lower one wins:
- thermal: a first-order fit T_pkg = T_idle + R_th * (P - P_idle). Throttling
  starts at T_tcc = TjMax - tcc_offset (coretemp tempN_crit, int340x
  tcc_offset_degree_celsius, [provenance:P238]). The admissible package power is
  P_th = P_idle + (T_tcc - margin - T_idle) / R_th.
- RAPL: PL2 while the PL1 moving average (time window Tau) is below PL1, else
  PL1 ([provenance:P235]). turbo_seconds() is the closed form for how long a
  load P > PL1 can stay at PL2.

The headroom in whole watts is floor(limit - P_now), never negative; extended
resources are integers ([provenance:P232]).

Usage:
  headroom_model.py host [--sysroot DIR]       read limits and temps (JSON)
  headroom_model.py fit SAMPLES.csv            fit R_th from "watts,celsius" lines (JSON)
  headroom_model.py budget --p-idle W --t-idle C --r-th C_PER_W --t-tcc C
                           [--p-now W] [--pl1 W] [--pl2 W] [--ewma W] [--margin C]
                           [--w-hot W --w-cool W [--npods N]]
  headroom_model.py selftest
"""
import argparse
import glob
import json
import math
import os
import sys


def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def num(text, scale=1.0):
    try:
        return int(text) / scale
    except (TypeError, ValueError):
        return None


def coretemp_package(sysroot):
    """(temp C, TjMax C) of the coretemp 'Package id 0' sensor, or (None, None)."""
    for hw in sorted(glob.glob(os.path.join(sysroot, "sys/class/hwmon/hwmon*"))):
        if read(os.path.join(hw, "name")) != "coretemp":
            continue
        for label in sorted(glob.glob(os.path.join(hw, "temp*_label"))):
            if read(label) == "Package id 0":
                base = label[: -len("_label")]
                return num(read(base + "_input"), 1000), num(read(base + "_crit"), 1000)
    return None, None


def rapl_package(sysroot):
    """{pl1_w, pl2_w, tau_s, tdp_w} of the first package RAPL zone; missing values are None."""
    for zone in sorted(glob.glob(os.path.join(sysroot, "sys/class/powercap/intel-rapl:[0-9]"))):
        if not (read(os.path.join(zone, "name")) or "").startswith("package"):
            continue
        out = {"pl1_w": None, "pl2_w": None, "tau_s": None, "tdp_w": None}
        for i in range(3):
            name = read(os.path.join(zone, "constraint_%d_name" % i))
            lim = num(read(os.path.join(zone, "constraint_%d_power_limit_uw" % i)), 1e6)
            if name == "long_term":
                out["pl1_w"] = lim
                out["tau_s"] = num(read(os.path.join(zone, "constraint_%d_time_window_us" % i)), 1e6)
                out["tdp_w"] = num(read(os.path.join(zone, "constraint_%d_max_power_uw" % i)), 1e6)
            elif name == "short_term":
                out["pl2_w"] = lim
        return out
    return {"pl1_w": None, "pl2_w": None, "tau_s": None, "tdp_w": None}


def tcc_offset(sysroot):
    """(offset C, source): int340x proc_thermal sysfs, else (0, "none").

    "none" means the offset is unknown, not zero: a BIOS can still program
    MSR 0x1A2 (bits 29:24) on hosts without the int340x device, and then
    t_tcc_c is too high by that offset.
    """
    for p in sorted(glob.glob(os.path.join(sysroot, "sys/devices/pci*/*/tcc_offset_degree_celsius"))):
        v = num(read(p))
        if v is not None:
            return v, "int340x"
    return 0, "none"


def packages(sysroot):
    """Number of top-level package RAPL zones (sockets)."""
    n = 0
    for zone in glob.glob(os.path.join(sysroot, "sys/class/powercap/intel-rapl:[0-9]")):
        if (read(os.path.join(zone, "name")) or "").startswith("package"):
            n += 1
    return n


def host(sysroot="/"):
    temp, tjmax = coretemp_package(sysroot)
    off, src = tcc_offset(sysroot)
    out = {"t_pkg_c": temp, "tjmax_c": tjmax, "tcc_offset_c": off, "tcc_offset_source": src,
           "t_tcc_c": None if tjmax is None else tjmax - off, "packages": packages(sysroot)}
    out.update(rapl_package(sysroot))
    return out


def fit(samples):
    """Least-squares (T_idle at P_idle, R_th) from (watts, celsius) pairs.

    Returns {p_idle_w, t_idle_c, r_th_c_per_w, n, residual_max_c}; p_idle is the
    lowest sampled power and t_idle the fitted temperature there. Needs two
    distinct power values and a positive slope.
    """
    if len(samples) < 2:
        raise ValueError("fit needs at least 2 samples")
    n = len(samples)
    mp = sum(p for p, _ in samples) / n
    mt = sum(t for _, t in samples) / n
    sxx = sum((p - mp) ** 2 for p, _ in samples)
    if sxx == 0:
        raise ValueError("fit needs at least 2 distinct power values")
    r = sum((p - mp) * (t - mt) for p, t in samples) / sxx
    if r <= 0:
        raise ValueError("fit slope %.4f C/W is not positive" % r)
    p_idle = min(p for p, _ in samples)
    t_idle = mt + r * (p_idle - mp)
    res = max(abs(t - (t_idle + r * (p - p_idle))) for p, t in samples)
    return {"p_idle_w": p_idle, "t_idle_c": t_idle, "r_th_c_per_w": r, "n": n, "residual_max_c": res}


def p_thermal(p_idle, t_idle, r_th, t_tcc, margin=10.0):
    """Package power at which the fit reaches T_tcc - margin."""
    return p_idle + (t_tcc - margin - t_idle) / r_th


def rapl_limit(pl1, pl2, ewma):
    """PL2 while the PL1 moving average is below PL1, else PL1; None when unknown."""
    if pl1 is None:
        return pl2
    if pl2 is None or ewma is None:
        return pl1
    return pl2 if ewma < pl1 else pl1


def ewma_step(avg, p, dt, tau):
    """One step of the PL1 moving average (time window tau seconds)."""
    return avg + (p - avg) * (1.0 - math.exp(-dt / tau))


def turbo_seconds(pl1, pl2, p0, tau):
    """Seconds a load at PL2 runs before the PL1 average reaches PL1, from average p0.

    t = tau * ln((PL2 - p0) / (PL2 - PL1)); 0 when p0 >= PL1, inf when PL2 <= PL1.
    """
    if p0 >= pl1:
        return 0.0
    if pl2 <= pl1:
        return math.inf
    return tau * math.log((pl2 - p0) / (pl2 - pl1))


def headroom(p_now, p_th, pl1=None, pl2=None, ewma=None):
    """{limit_w, binding, watts}: whole watts admissible above p_now."""
    lim_r = rapl_limit(pl1, pl2, ewma)
    if lim_r is not None and lim_r < p_th:
        limit, binding = lim_r, "rapl"
    else:
        limit, binding = p_th, "thermal"
    return {"limit_w": limit, "binding": binding, "watts": max(0, math.floor(limit - p_now))}


def slots(budget, w_hot, w_cool):
    """Arm C: pod-count cap with the same budget, tuned to the mix average."""
    mean = (w_hot + w_cool) / 2.0
    if mean <= 0:
        raise ValueError("mean pod watts must be positive")
    return max(0, math.floor(budget / mean))


def degenerate(watts, n_slots, w_hot, w_cool, npods, min_pods=4):
    """Why a budget cannot separate arms T/C from B, or None.

    A budget that admits fewer than min_pods pods (or less than two of the
    largest pod) only measures an empty node; one that admits all npods makes
    T and C equal to B.
    """
    if watts < 2 * max(w_hot, w_cool):
        return "watts %d < 2 x max pod watts %g" % (watts, max(w_hot, w_cool))
    if n_slots < min_pods:
        return "slots %d < %d" % (n_slots, min_pods)
    if n_slots >= npods:
        return "slots %d >= %d pods (no cap)" % (n_slots, npods)
    return None


def load_samples(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p, t = line.split(",")[:2]
            out.append((float(p), float(t)))
    return out


def selftest():
    s = [(10.0, 40.0), (50.0, 60.0), (90.0, 80.0)]
    f = fit(s)
    assert abs(f["r_th_c_per_w"] - 0.5) < 1e-9 and abs(f["t_idle_c"] - 40.0) < 1e-9
    assert abs(p_thermal(10, 40, 0.5, 100) - 110.0) < 1e-9
    assert headroom(30, 110, 117, 117, 0)["watts"] == 80
    assert headroom(30, 200, 65, 120, 70) == {"limit_w": 65, "binding": "rapl", "watts": 35}
    assert slots(80, 6, 4) == 16
    assert abs(turbo_seconds(65, 120, 0, 28) - 28 * math.log(120 / 55)) < 1e-9
    assert degenerate(0, 0, 6, 3, 24) is not None and degenerate(60, 13, 6, 3, 24) is None
    print("selftest ok")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("host")
    h.add_argument("--sysroot", default="/")
    fp = sub.add_parser("fit")
    fp.add_argument("samples")
    b = sub.add_parser("budget")
    for name in ("p-idle", "t-idle", "r-th", "t-tcc"):
        b.add_argument("--" + name, type=float, required=True)
    for name in ("p-now", "pl1", "pl2", "ewma", "w-hot", "w-cool"):
        b.add_argument("--" + name, type=float)
    b.add_argument("--npods", type=int)
    b.add_argument("--margin", type=float, default=10.0)
    sub.add_parser("selftest")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        selftest()
        return 0
    if a.cmd == "host":
        out = host(a.sysroot)
    elif a.cmd == "fit":
        out = fit(load_samples(a.samples))
    else:
        pth = p_thermal(a.p_idle, a.t_idle, a.r_th, a.t_tcc, a.margin)
        p_now = a.p_idle if a.p_now is None else a.p_now
        out = {"p_th_w": pth}
        out.update(headroom(p_now, pth, a.pl1, a.pl2, a.ewma))
        if a.w_hot is not None and a.w_cool is not None:
            out["slots"] = slots(out["watts"], a.w_hot, a.w_cool)
            if a.npods is not None:
                out["degenerate"] = degenerate(out["watts"], out["slots"], a.w_hot, a.w_cool, a.npods)
    json.dump(out, sys.stdout, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ValueError as e:
        print("headroom_model: %s" % e, file=sys.stderr)
        sys.exit(2)
