#!/usr/bin/env python3
"""Evaluate the pre-registered simulation criteria (acceptance.json, section "sim").

Prints one JSON document with every number the verdicts rest on. Simulation
only: no counters, no cluster, one CPU thread.
"""
import json
import math
import os
import sys
from dataclasses import replace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coreplacer import sim  # noqa: E402
from coreplacer.estimate import Window  # noqa: E402
from coreplacer.placer import Placer  # noqa: E402

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "acceptance.json")) as _f:
    ACC = json.load(_f)["sim"]
BASE = ("unpinned", "all_p", "exhaustive_static")


def mix_table(host, seeds, T, mixes, speed_scale=1.0):
    rows = {}
    for m in mixes:
        acc = {k: [] for k in BASE + ("auto", "auto_const_s", "auto_shuffled", "auto_no_drift")}
        energy = {k: [] for k in BASE + ("auto",)}
        moves = []
        for seed in seeds:
            r = sim.evaluate_mix(host, m, seed, T, ACC["window_s"], ACC["move_cost_s"], speed_scale)
            for k in BASE + ("auto",):
                acc[k].append(float(r[k][0]))
                energy[k].append(float(r[k][1]))
            moves.append(r["moves"])
            wl = r["_wl"]
            ab_rng = lambda: np.random.default_rng(seed + 1000)  # noqa: E731
            acc["auto_const_s"].append(float(sim.run_auto(host, wl, ab_rng(), ACC["window_s"], ACC["move_cost_s"],
                                                          Placer(use_speedup=False))[0]))
            acc["auto_shuffled"].append(float(sim.run_auto(host, wl, ab_rng(), ACC["window_s"], ACC["move_cost_s"],
                                                           shuffle=True)[0]))
            acc["auto_no_drift"].append(float(sim.run_auto(host, wl, ab_rng(), ACC["window_s"], ACC["move_cost_s"],
                                                           Placer(drift=False))[0]))
        row = {k: float(np.mean(v)) for k, v in acc.items()}
        row["energy_kJ"] = {k: round(float(np.mean(v)) / 1000, 2) for k, v in energy.items()}
        row["moves"] = float(np.mean(moves))
        best = max(BASE, key=lambda k: row[k])
        row["best_baseline"] = best
        row["auto_over_best"] = row["auto"] / row[best] - 1
        row["auto_over_static"] = row["auto"] / row["exhaustive_static"] - 1
        rows[m] = row
    return rows


def verdict_s1(rows):
    win = any(r["auto_over_best"] >= 0.10 for r in rows.values())
    kill = all(r["auto_over_static"] < 0.03 for r in rows.values())
    return "win" if win else ("kill" if kill else "neither")


def s2(rows):
    out = {}
    for m, r in rows.items():
        margin = r["auto"] - r["unpinned"]
        if margin / r["unpinned"] < 0.05:
            continue
        out[m] = {k: 1 - (r[k] - r["unpinned"]) / margin for k in ("auto_const_s", "auto_shuffled")}
    ok = all(v >= 0.5 for d in out.values() for v in d.values())
    return {"margin_lost_fraction": out, "pass": ok, "applicable": bool(out)}


def s3(host, T=1800, probes=300, seed=7):
    """Concurrent (shared balloon, both core types at once, 6 s) vs sequential
    (3 s fenced on P then 3 s fenced on E) speedup estimates under M3."""
    rng = np.random.default_rng(seed)
    wl = sim.make_mix("M3_phased", T, rng)
    n = wl.demand.shape[1]
    err = {"concurrent": [], "sequential": []}
    shared = np.zeros((1, n), dtype=int)
    sizes = np.zeros((1, n))
    for _ in range(probes):
        t0, i = int(rng.integers(0, T - 6)), int(rng.integers(n))
        truth = float(np.mean(wl.rate_p[t0:t0 + 6, i] / wl.rate_e[t0:t0 + 6, i]))
        w = Window(6)
        for t in range(t0, t0 + 6):
            pt, et = sim._allocate(host, wl.demand[t], shared, sizes)
            w.inst_p += pt[0, i] * wl.rate_p[t, i] * rng.lognormal(0, host.noise)
            w.time_p += pt[0, i]
            w.inst_e += et[0, i] * wl.rate_e[t, i] * rng.lognormal(0, host.noise)
            w.time_e += et[0, i]
        err["concurrent"].append(abs(math.log((w.inst_p / w.time_p) / (w.inst_e / w.time_e) / truth)))
        d = wl.demand[t0:t0 + 6, i]
        ip = sum(d[k] * wl.rate_p[t0 + k, i] * rng.lognormal(0, host.noise) for k in range(3))
        ie = sum(d[k] * wl.rate_e[t0 + k, i] * rng.lognormal(0, host.noise) for k in range(3, 6))
        err["sequential"].append(abs(math.log((ip / d[:3].sum()) / (ie / d[3:].sum()) / truth)))
    stats = {k: {"median": float(np.median(v)), "p95": float(np.percentile(v, 95))} for k, v in err.items()}
    stats["pass"] = stats["concurrent"]["median"] < stats["sequential"]["median"]
    return stats


def s4(host, nominal, mixes, T):
    cfg = ACC["S4_robustness"]
    rng = np.random.default_rng(4242)
    runs = []
    for k in range(cfg["perturbations"]):
        u = lambda: float(rng.uniform(1 - cfg["scale"], 1 + cfg["scale"]))  # noqa: E731
        h = replace(host, pl1=host.pl1 * u(), uncore_w=host.uncore_w * u(), p_core_w=host.p_core_w * u(),
                    e_core_w=host.e_core_w * u(), cap_alpha=host.cap_alpha * u(), noise=host.noise * u())
        rows = mix_table(h, [100 + k], T, mixes, speed_scale=u())
        runs.append({"verdict": verdict_s1(rows),
                     "auto_over_best": {m: round(r["auto_over_best"], 4) for m, r in rows.items()},
                     "auto_over_static": {m: round(r["auto_over_static"], 4) for m, r in rows.items()}})
    stable = sum(r["verdict"] == nominal for r in runs) / len(runs)
    return {"runs": runs, "stable_fraction": stable, "pass": stable >= cfg["verdict_stable_fraction_min"]}


def main():
    host = sim.Host()
    T = ACC["horizon_s"]
    rows = mix_table(host, ACC["seeds"], T, ACC["mixes"])
    v = verdict_s1(rows)
    out = {"host": host.__dict__, "mixes": rows, "S1_verdict": v, "S2": s2(rows), "S3": s3(host),
           "S4": s4(host, v, ACC["mixes"], T)}
    print(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
