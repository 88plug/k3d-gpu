#!/usr/bin/env python3
"""Falsification simulator for the hybrid-split joulemeter.

A hidden physics model of an 8P+16E client package produces RAPL deltas and
the counters a node agent can read. Meters see ONLY those observables; truth
is computed from the hidden model (Shapley over pod subsets, see
tests/joulemeter_prereg.py), never from the meter's linear form.

Physics deliberately contains the refuter's failure modes:
  * shared VccIA rail: every active core runs at V(max frequency of any active
    core), so an E-pod's joules per cycle depend on the P-pod (non-separable);
  * leakage ~ V^2 per busy core (not proportional to cycles);
  * IPC-dependent switched capacitance (cycles and instructions collinear but
    not identical);
  * turbo bins: frequency falls as more cores of a type are busy;
  * optional package power cap (PL1) re-solved per window by scaling every
    clock, so co-runner removal changes the others' frequency;
  * unobserved background load (absorbed into idle by the meter);
  * memory bytes that LLC-miss counts only approximate (prefetch factor), and
    GPU memory traffic invisible to CPU counters;
  * ring/LLC power that follows the rail (unattributable interaction).

Usage: tests/joulemeter_sim.py [--quick]   (prints the results table and the
verdict for every pre-registered criterion). Pure numpy/scipy, one core.
"""

from __future__ import annotations

import argparse
import itertools
import math
import os
import sys
from dataclasses import dataclass, replace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "share", "joulemeter"))
sys.path.insert(0, HERE)
import joulemeter as jm  # noqa: E402
import joulemeter_prereg as prereg  # noqa: E402

TSC_HZ = 1.0e9
N_P, N_E = 8, 16


@dataclass(frozen=True)
class Physics:
    c_p: float = 1.00       # W / (V^2 GHz) per busy P-core at IPC 1.5
    c_e: float = 0.30
    l_p: float = 0.60       # leakage W / V^2 per busy P-core
    l_e: float = 0.15
    a_ipc: float = 0.30     # capacitance swing with IPC
    ipc_e: float = 0.60     # E-core IPC relative to P
    fp1: float = 4.4        # GHz, one busy P-core
    dp: float = 0.08        # GHz lost per extra busy P-core
    fe1: float = 3.5
    de: float = 0.04
    v0: float = 0.55        # V(f) = v0 + kv f
    kv: float = 0.14
    rail_shared: bool = True
    core_idle: float = 0.40
    g_idle: float = 0.20
    c_gpu: tuple = (4.0, 2.5, 4.0, 1.0)   # render, video, compute, copy
    fg_max: float = 1.6
    fg_min: float = 0.3
    gpu_gbps: float = 3.0   # GPU memory GB/s at full render busy
    pkg_floor: float = 1.50
    e_byte: float = 0.20    # J / GB
    ring: float = 0.25      # W / (V^2 GHz)
    cap_w: float = 0.0      # 0 = uncapped
    rapl_noise: float = 0.003
    ctr_noise: float = 0.001
    bg_w: float = 0.4       # max unobserved background W


def vf(ph, f):
    return ph.v0 + ph.kv * f


# ------------------------------------------------------------- workloads ---

def phases(rng, T, mean_len, draw):
    """Piecewise-constant series: phases of exponential length."""
    out, t = [], 0
    while t < T:
        n = max(1, int(rng.exponential(mean_len)))
        out.extend([draw()] * n)
        t += n
    return np.array(out[:T])


def make_workload(rng, T, scenario):
    """Exogenous pod activity. Returns dict name -> dict of (T,) arrays:
    nP, nE (cores occupied), u (utilisation), k (IPC), mr (bytes/instr),
    pf (prefetch factor, constant), g (T, 4) engine busy fraction."""
    pods = {}

    def pod(n_threads, placement, gpu=None, u_rng=(0.0, 1.0), p_off=0.25):
        u = phases(rng, T, 15, lambda: 0.0 if rng.random() < p_off else rng.uniform(*u_rng))
        k = phases(rng, T, 15, lambda: rng.uniform(0.6, 2.5))
        mr = phases(rng, T, 15, lambda: rng.uniform(0.01, 0.3))
        if placement == "P":
            nP, nE = np.full(T, n_threads), np.zeros(T)
        elif placement == "E":
            nP, nE = np.zeros(T), np.full(T, n_threads)
        else:  # shared balloon: the scheduler moves threads between types
            nP = phases(rng, T, 10, lambda: rng.integers(0, n_threads + 1)).astype(float)
            nE = n_threads - nP
        g = np.zeros((T, 4))
        if gpu is not None:
            g[:, gpu] = phases(rng, T, 15, lambda: 0.0 if rng.random() < 0.25 else rng.uniform(0.2, 1.0))
        return dict(nP=nP, nE=nE, u=u, k=k, mr=mr, pf=rng.uniform(0.8, 1.25), g=g)

    if scenario == "pinned":
        pods["A"] = pod(4, "P")
        pods["B"] = pod(8, "E")
        pods["C"] = pod(1, "S", gpu=0, u_rng=(0.1, 0.4), p_off=0.1)
    elif scenario == "floating":
        pods["A"] = pod(4, "S")
        pods["B"] = pod(4, "S")
        pods["C"] = pod(1, "S", gpu=0, u_rng=(0.1, 0.4), p_off=0.1)
    elif scenario == "gpu2":
        pods["A"] = pod(4, "P")
        pods["B"] = pod(8, "E")
        pods["C"] = pod(1, "S", gpu=0, u_rng=(0.1, 0.4), p_off=0.1)
        pods["D"] = pod(1, "S", gpu=1, u_rng=(0.05, 0.2), p_off=0.1)
    else:
        raise ValueError(scenario)
    if scenario == "floating":   # at most 8 P-cores between them
        over = pods["A"]["nP"] + pods["B"]["nP"] + pods["C"]["nP"] - N_P
        cut = np.clip(over, 0, None)
        pods["B"]["nP"] -= np.minimum(cut, pods["B"]["nP"])
        pods["B"]["nE"] = 4 - pods["B"]["nP"]
    return pods


# --------------------------------------------------------------- physics ---

def physics(ph, W, mask, bg, s=None):
    """Per-window domain powers (W) for the pods in `mask`. Solves the power
    cap by bisection on a common clock scale unless s is given. Returns
    (core, uncore, pkg_rest, detail)."""
    names = list(W)
    T = len(bg)
    on = {n: (mask[n] * (W[n]["u"] > 0)) for n in names}
    nP_act = sum(on[n] * W[n]["nP"] for n in names)
    nE_act = sum(on[n] * W[n]["nE"] for n in names)
    busy_g = sum(mask[n] * W[n]["g"] for n in names) if names else np.zeros((T, 4))

    def at(scale):
        fP = np.clip(ph.fp1 - ph.dp * np.maximum(nP_act - 1, 0), 0.8, None) * scale
        fE = np.clip(ph.fe1 - ph.de * np.maximum(nE_act - 1, 0), 0.8, None) * scale
        fmax = np.maximum(np.where(nP_act > 0, fP, 0), np.where(nE_act > 0, fE, 0))
        if ph.rail_shared:
            VP = VE = vf(ph, fmax)
        else:
            VP, VE = vf(ph, fP), vf(ph, fE)
        core = ph.core_idle + bg.copy()
        instr = np.zeros(T)
        per = {}
        for n in names:
            w, m = W[n], mask[n]
            cap = 1 + ph.a_ipc * (w["k"] - 1.5) / 1.5
            pP = m * w["nP"] * w["u"] * (ph.c_p * cap * VP ** 2 * fP + ph.l_p * VP ** 2)
            pE = m * w["nE"] * w["u"] * (ph.c_e * cap * VE ** 2 * fE + ph.l_e * VE ** 2)
            core = core + pP + pE
            iP = m * w["nP"] * w["u"] * fP * 1e9 * w["k"]
            iE = m * w["nE"] * w["u"] * fE * 1e9 * w["k"] * ph.ipc_e
            instr = instr + (iP + iE) * w["mr"]
            per[n] = (fP, fE, iP, iE)
        anyg = busy_g.sum(axis=1) > 0
        fg = np.maximum(np.where(anyg, ph.fg_max, ph.fg_min) * scale, ph.fg_min)
        vg = 0.6 + 0.25 * fg
        uncore = ph.g_idle + (busy_g * np.array(ph.c_gpu)).sum(axis=1) * fg * vg ** 2
        gbytes = busy_g[:, 0] * ph.gpu_gbps * 1e9 * fg / ph.fg_max
        mem = instr + gbytes
        anyc = (nP_act + nE_act) > 0
        rest = ph.pkg_floor + ph.e_byte * mem / 1e9 + anyc * ph.ring * vf(ph, fmax) ** 2 * fmax
        return core, uncore, rest, dict(per=per, fg=fg, mem=mem)

    if s is None:
        s = np.ones(T)
        if ph.cap_w > 0:
            c, u, r, _ = at(s)
            hot = (c + u + r) > ph.cap_w
            lo, hi = np.full(T, 0.3), np.ones(T)
            for _ in range(30):
                mid = (lo + hi) / 2
                c, u, r, _ = at(mid)
                over = (c + u + r) > ph.cap_w
                hi = np.where(over, mid, hi)
                lo = np.where(over, lo, mid)
            s = np.where(hot, lo, 1.0)
    out = at(s)
    return out[0], out[1], out[2], dict(out[3], s=s)


def truth(ph, W, bg, dt, domain="all"):
    """Shapley and rescaled-marginal per-pod energy (J per window) for one
    domain ("all", "core", "uncore" or "pkg_rest")."""
    names = list(W)
    n = len(names)
    pick = {"all": lambda c, u, r: c + u + r, "core": lambda c, u, r: c,
            "uncore": lambda c, u, r: u, "pkg_rest": lambda c, u, r: r}[domain]
    cache = {}
    for bits in itertools.product((0, 1), repeat=n):
        c, u, r, _ = physics(ph, W, dict(zip(names, bits)), bg)
        cache[bits] = pick(c, u, r) * dt
    empty = cache[(0,) * n]
    full = cache[(1,) * n]
    shap = {}
    for i, nm in enumerate(names):
        acc = 0.0
        for bits, e in cache.items():
            if bits[i]:
                continue
            k = sum(bits)
            wgt = math.factorial(k) * math.factorial(n - k - 1) / math.factorial(n)
            with_i = list(bits)
            with_i[i] = 1
            acc = acc + wgt * (cache[tuple(with_i)] - e)
        shap[nm] = acc
    marg = {}
    for i, nm in enumerate(names):
        wo = [1] * n
        wo[i] = 0
        marg[nm] = full - cache[tuple(wo)]
    tot_m = sum(marg.values())
    attributable = full - empty
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(np.abs(tot_m) > 1e-12, attributable / tot_m, 0.0)
    marg = {k: v * scale for k, v in marg.items()}
    return shap, marg, empty


def observe(ph, W, bg, dt, rng):
    """What the node agent sees: per-pod PodSamples and SysSamples."""
    names = list(W)
    mask = {n: 1 for n in names}
    c, u, r, d = physics(ph, W, mask, bg)
    T = len(bg)
    nz = lambda: 1 + ph.ctr_noise * rng.standard_normal(T)  # noqa: E731
    rz = lambda: 1 + ph.rapl_noise * rng.standard_normal(T)  # noqa: E731
    cols = {}
    for n in names:
        w = W[n]
        fP, fE, iP, iE = d["per"][n]
        pc = w["nP"] * w["u"] * fP * 1e9 * dt * nz()
        ec = w["nE"] * w["u"] * fE * 1e9 * dt * nz()
        cols[n] = dict(
            p_cyc=pc, e_cyc=ec,
            p_ref=w["nP"] * w["u"] * TSC_HZ * dt * nz(),
            e_ref=w["nE"] * w["u"] * TSC_HZ * dt * nz(),
            p_ins=iP * dt * nz(), e_ins=iE * dt * nz(),
            p_miss=iP * w["mr"] * dt / 64 * w["pf"] * nz(),
            e_miss=iE * w["mr"] * dt / 64 * w["pf"] * nz(),
            cpu_s=(w["nP"] + w["nE"]) * w["u"] * dt,
            g=w["g"] * dt)
    core, unc = c * dt * rz(), u * dt * rz()
    pkg = core + unc + r * dt * rz()
    sysd = dict(core=core, unc=unc, pkg=pkg, gpu_mhz=d["fg"] * 1e3,
                imc=d["mem"] * dt * nz(), s=d["s"])
    return cols, sysd


def samples(cols, sysd, t, dt):
    pods = {}
    for n, c in cols.items():
        pods[n] = jm.PodSample(
            p_cyc=c["p_cyc"][t], p_ins=c["p_ins"][t], p_ref=c["p_ref"][t], p_miss=c["p_miss"][t],
            e_cyc=c["e_cyc"][t], e_ins=c["e_ins"][t], e_ref=c["e_ref"][t], e_miss=c["e_miss"][t],
            cpu_s=c["cpu_s"][t],
            gpu_s={e: c["g"][t, i] for i, e in enumerate(jm.ENGINE_CLASSES) if c["g"][t, i] > 0})
    s = jm.SysSample(dt=dt, core_j=sysd["core"][t], uncore_j=sysd["unc"][t], pkg_j=sysd["pkg"][t],
                     gpu_mhz=sysd["gpu_mhz"][t], imc_bytes=sysd["imc"][t], tsc_hz=TSC_HZ)
    return pods, s


# ---------------------------------------------------------------- meters ---

def _zero(names):
    return {n: {d: 0.0 for d in jm.DOMAINS} for n in names}


def run_meter(meter, cols, sysd, dt):
    names = list(cols)
    tot = _zero(names)
    for t in range(len(sysd["core"])):
        pods, s = samples(cols, sysd, t, dt)
        out, _ = meter.step(pods, s)
        for n in names:
            for lab, j in out[n].items():
                tot[n]["core" if lab.startswith("core") else
                       "uncore" if lab == "uncore_gpu" else lab] += j
    return tot


def run_split(cols, sysd, idle, weights):
    """Proportional splitter: idle[d] is a (T,) joule array and
    weights(d, t) -> {pod: weight}."""
    names = list(cols)
    tot = _zero(names)
    meas = {"core": sysd["core"], "uncore": sysd["unc"],
            "pkg_rest": sysd["pkg"] - sysd["core"] - sysd["unc"]}
    for t in range(len(sysd["core"])):
        for d in jm.DOMAINS:
            w = weights(d, t)
            share, _ = jm.apportion(meas[d][t], idle[d][t], w, w)
            for n in names:
                tot[n][d] += share[n]
    return tot


def kepler_weights(cols):
    """Kepler power-attribution-guide: every zone split by CPU time."""
    return lambda d, t: {n: c["cpu_s"][t] for n, c in cols.items()}


def calibrate_gamma(ph, dt):
    """Host calibration a la EnergAt/E-Mapper: solo full-load runs on P and
    on E give one energy-per-cycle constant per core type."""
    T = 50
    bg = np.zeros(T)
    out = {}
    for typ, nP, nE in (("P", 4, 0), ("E", 0, 8)):
        W = {"x": dict(nP=np.full(T, nP), nE=np.full(T, nE), u=np.ones(T), k=np.full(T, 1.5),
                       mr=np.full(T, 0.1), pf=1.0, g=np.zeros((T, 4)))}
        c1, _, _, d = physics(ph, W, {"x": 1}, bg)
        c0, _, _, _ = physics(ph, W, {"x": 0}, bg)
        fP, fE, _, _ = d["per"]["x"]
        cyc = (nP * fP + nE * fE) * 1e9 * dt
        out[typ] = float(np.mean((c1 - c0) * dt / cyc))
    return out


def gamma_weights(cols, gamma):
    """Static per-core-type energy/cycle for core; drm engine time for
    uncore (the proportional fdinfo split); LLC misses for pkg_rest."""
    def w(d, t):
        if d == "core":
            return {n: gamma["P"] * c["p_cyc"][t] + gamma["E"] * c["e_cyc"][t] for n, c in cols.items()}
        if d == "uncore":
            return {n: c["g"][t].sum() for n, c in cols.items()}
        return {n: c["p_miss"][t] + c["e_miss"][t] for n, c in cols.items()}
    return w


# ------------------------------------------------------------ experiment ---

def mape(est, ref):
    return float(np.mean([abs(est[n] - ref[n]) / abs(ref[n]) for n in ref if abs(ref[n]) > 1e-9]))


def one_run(ph, scenario, seed, T, dt, methods, ctrl=None):
    """Returns ({method: (MAPE total vs Shapley, MAPE total vs marginal,
    MAPE core domain vs core Shapley)}, fraction of windows at the cap)."""
    rng = np.random.default_rng(seed)
    W = make_workload(rng, T, scenario)
    bg = phases(rng, T, 50, lambda: rng.uniform(0, ph.bg_w))
    shap, marg, _ = truth(ph, W, bg, dt)
    shap_core, _, _ = truth(ph, W, bg, dt, "core")
    ref_s = {n: float(v.sum()) for n, v in shap.items()}
    ref_m = {n: float(v.sum()) for n, v in marg.items()}
    ref_c = {n: float(v.sum()) for n, v in shap_core.items()}
    cols, sysd = observe(ph, W, bg, dt, rng)

    if ctrl == "permute_e":       # break E-counters' link to RAPL
        perm = np.random.default_rng(seed + 999).permutation(T)
        for c in cols.values():
            for k in ("e_cyc", "e_ins", "e_ref"):
                c[k] = c[k][perm]
    elif ctrl == "cross_seed":    # counters from an unrelated run
        rng2 = np.random.default_rng(seed + 7777)
        W2 = make_workload(rng2, T, scenario)
        bg2 = phases(rng2, T, 50, lambda: rng2.uniform(0, ph.bg_w))
        cols, _ = observe(ph, W2, bg2, dt, rng2)

    zero = {n: 0 for n in W}
    # "true" idle (the pre-registered gift to Kepler and static-gamma):
    # per-window no-pod energy, unobserved background included.
    c0, u0, r0, _ = physics(ph, W, zero, bg)
    idle_true = {"core": c0 * dt, "uncore": u0 * dt, "pkg_rest": r0 * dt}
    # "cal" idle (symmetric, available to any method): one quiet-host
    # measurement, background at its mean.
    c1, u1, r1, _ = physics(ph, W, zero, np.full(T, ph.bg_w / 2))
    idle_w = {"core": float(c1[0]), "uncore": float(u1[0]), "pkg_rest": float(r1[0])}
    idle_cal = {d: np.full(T, w * dt) for d, w in idle_w.items()}
    res = {}
    for m in methods:
        kind, _, opt = m.partition(":")
        if kind == "kepler":
            est = run_split(cols, sysd, idle_cal if opt == "cal" else idle_true, kepler_weights(cols))
        elif kind == "static_gamma":
            est = run_split(cols, sysd, idle_cal if opt == "cal" else idle_true,
                            gamma_weights(cols, calibrate_gamma(ph, dt)))
        else:
            model, bucket, *v2 = m.split(":")
            kw = dict(idle_w=idle_w, pkg_features="pods") if v2 == ["v2"] else {}
            est = run_meter(jm.HybridMeter(model=model, bucket=bucket, **kw), cols, sysd, dt)
        tot = {n: sum(est[n].values()) for n in est}
        res[m] = (mape(tot, ref_s), mape(tot, ref_m), mape({n: est[n]["core"] for n in est}, ref_c))
    return res, float(np.mean(sysd["s"] < 0.999))


METER = "hybrid:rail"           # the pre-registered meter configuration
SW = ("summed:rail", "summed:own", "summed:none")
ALL = (METER, "hybrid:own", "hybrid:none") + SW + ("kepler", "static_gamma")
# Post-hoc (designed after the dev runs on seeds 0-2, scored on held-out
# seeds): quiet-host idle for every method, pkg_rest fitted on pod features.
V2_HYB = ("hybrid:rail:v2", "hybrid:own:v2", "hybrid:none:v2")
V2_SW = ("summed:rail:v2", "summed:own:v2", "summed:none:v2")
V2 = V2_HYB + V2_SW + ("kepler:cal", "static_gamma:cal")
HELD_OUT = range(100, 100 + prereg.SEEDS)
COLS = "total/Shapley  total/marginal  core/Shapley"


def campaign(ph, scenario, seeds, T, dt, methods=ALL, ctrl=None):
    acc = {m: [] for m in methods}
    capped = []
    for sd in seeds:
        r, cf = one_run(ph, scenario, sd, T, dt, methods, ctrl)
        capped.append(cf)
        for m in methods:
            acc[m].append(r[m])
    return {m: tuple(np.mean(v, axis=0)) for m, v in acc.items()}, float(np.mean(capped))


def best(res, group, j):
    return min(res[m][j] for m in group if m in res)


def best_sw(res, j):
    return best(res, SW, j)


def table(label, res, cf):
    print(f"\n## {label}   (windows at the power cap: {cf:.0%})")
    print(f"  {'method':18s} {COLS}")
    for m, v in res.items():
        print(f"  {m:18s} " + "  ".join(f"{x:13.1%}" for x in v))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="3 seeds, 600 windows, 10 draws")
    a = ap.parse_args(argv)
    C = prereg.CRITERIA
    nseed = 3 if a.quick else prereg.SEEDS
    seeds, held = range(nseed), HELD_OUT[:nseed]
    T = 600 if a.quick else prereg.WINDOWS
    dt = prereg.DT
    draws = 10 if a.quick else C["C7_perturb_draws"]
    base = Physics()
    cap35 = replace(base, cap_w=35.0)
    verdict = {}

    print(f"# hybrid-split joulemeter sim  seeds={nseed} windows={T} dt={dt}s")
    print("# MAPE of per-pod run-total energy (idle excluded)")
    print("\n# PART 1 -- pre-registered methods, seeds 0..N-1")
    tables = {}
    for label, ph, sc in (("pinned", base, "pinned"), ("floating", base, "floating"),
                          ("gpu2", base, "gpu2"),
                          ("pinned,cap35W", cap35, "pinned"),
                          ("floating,cap35W", cap35, "floating"),
                          ("pinned,separate-rails", replace(base, rail_shared=False), "pinned")):
        res, cf = campaign(ph, sc, seeds, T, dt)
        tables[label] = res
        table(label, res, cf)

    def c3(res):
        ok = True
        for j in (0, 1):
            ok &= res[METER][j] <= C["C3_meter_mape_max"]
            ok &= res[METER][j] <= C["C3_ratio_vs_smartwatts_max"] * best_sw(res, j)
        return bool(ok)

    p = tables["pinned"]
    verdict["C3 pinned: meter <=10% and <=0.5x best SmartWatts-style"] = c3(p)
    verdict["C3 floating: same"] = c3(tables["floating"])
    verdict["claim survives its own refutation line (meter <=15% and <=0.5x static-gamma), pinned"] = bool(
        p[METER][0] <= C["refute_meter_mape_above"]
        and p[METER][0] <= C["refute_ratio_vs_static_gamma_above"] * p["static_gamma"][0])
    verdict["C4 Kepler >= 35% (pinned)"] = bool(p["kepler"][0] >= C["C4_kepler_mape_min"])
    verdict["C4 static-gamma >= 20% (pinned)"] = bool(p["static_gamma"][0] >= C["C4_static_gamma_mape_min"])
    verdict["C5 capped meter <= 15% (pinned and floating)"] = bool(
        max(tables["pinned,cap35W"][METER][0], tables["floating,cap35W"][METER][0])
        <= C["C5_capped_meter_mape_max"])

    print("\n## falsification controls (pinned, meter, total/Shapley)")
    ctl = {}
    for ctrl in ("permute_e", "cross_seed"):
        res, _ = campaign(base, "pinned", seeds, T, dt, methods=(METER,), ctrl=ctrl)
        ctl[ctrl] = res[METER][0]
        print(f"  {ctrl:12s} {ctl[ctrl]:7.1%}   (unperturbed {p[METER][0]:.1%})")
    verdict["C6 permuting E-counters degrades >= 2x"] = bool(
        ctl["permute_e"] >= C["C6_permute_degrade_min"] * p[METER][0])
    verdict["C6 counters from an unrelated run fail C3"] = bool(ctl["cross_seed"] > C["C3_meter_mape_max"])

    print(f"\n## C7 perturbation sweep ({draws} draws, +-30%, pinned/floating alternating, 1 seed each)")
    rng = np.random.default_rng(12345)
    passes = []
    for i in range(draws):
        f = lambda: rng.uniform(0.7, 1.3)  # noqa: E731
        ph = replace(base, c_p=base.c_p * f(), c_e=base.c_e * f(), kv=base.kv * f(),
                     l_p=base.l_p * f(), l_e=base.l_e * f(), a_ipc=base.a_ipc * f(),
                     rapl_noise=base.rapl_noise * f() * 2, ctr_noise=base.ctr_noise * f() * 2)
        sc = ("pinned", "floating")[i % 2]
        res, _ = campaign(ph, sc, [1000 + i], T, dt, methods=(METER,) + SW)
        ratio = res[METER][0] / best_sw(res, 0)
        passes.append(ratio <= C["C3_ratio_vs_smartwatts_max"])
        print(f"  draw {i:2d} {sc:8s} meter {res[METER][0]:6.1%}  best SmartWatts-style "
              f"{best_sw(res, 0):6.1%}  ratio {ratio:.2f}")
    frac = float(np.mean(passes))
    verdict[f"C7 ratio <= 0.5 in >= 80% of draws (got {frac:.0%})"] = frac >= C["C7_perturb_pass_frac_min"]

    print("\n## pre-registered verdicts")
    for k, v in verdict.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")

    print(f"\n# PART 2 -- post-hoc v2 (quiet-host idle for all, pkg_rest on pod features),"
          f" held-out seeds {held.start}..{held.stop - 1}; descriptive, no verdicts")
    for label, ph, sc in (("pinned", base, "pinned"), ("floating", base, "floating"),
                          ("gpu2", base, "gpu2"),
                          ("pinned,cap35W", cap35, "pinned"),
                          ("pinned,cap20W", replace(base, cap_w=20.0), "pinned"),
                          ("pinned,separate-rails", replace(base, rail_shared=False), "pinned")):
        res, cf = campaign(ph, sc, held, T, dt, methods=V2)
        table(label + " [v2]", res, cf)
        print("  best hybrid / best summed (core/Shapley): "
              f"{best(res, V2_HYB, 2):.1%} / {best(res, V2_SW, 2):.1%} = "
              f"{best(res, V2_HYB, 2) / best(res, V2_SW, 2):.2f}")
    return verdict


if __name__ == "__main__":
    main()
