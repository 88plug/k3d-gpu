"""Falsifying simulation for the Joule Ledger. Scores against prereg.py.

Run:  python3 tests/joule_ledger/sim.py [--draws N] [--seed S] [--c5-seeds N]
Deps: tests/joule_ledger/requirements.txt (numpy and scipy pinned; the README
      numbers come from those versions).

Hidden physics (the ledger never sees it, and its model is deliberately NOT
the ledger's model):
  GPU domain, 1 ms grid, 120 s per draw:
    * requests arrive Poisson; each span opens with a queue/network phase that
      draws no GPU power, then a prefill burst, then decode;
    * per-request demand is lognormal (heterogeneous), lagged by a first-order
      filter (tau), so power keeps flowing after the span closes;
    * device dynamic power saturates: Pmax * (1 - exp(-sum/Pmax));
    * untraced background bursts add power no span owns;
    * the energy counter publishes every U ms with jitter, becomes visible
      L_true ms later, is floor-quantised to 1 mJ, and is polled every 10 ms.
  CPU domain (C5): shared-rail VccIA model, energy ~ C*cycles*V(fmax)^2 plus
  V-dependent leakage, DVFS phases, 1% counter noise.

Decisions fixed before the first run (prereg.py left them open):
  * Kepler comparator: documented default interval 5 s
    (kepler docs/user/configuration.md, --monitor.interval default 5s); the
    1 s variant is reported, not scored. It reads the same stale counter at
    its interval and is handed the TRUE idle power. One pod owns every span.
    It charges every in-flight span the full pod power, so it double counts
    under concurrency by construction. A split-aware variant (pod power at
    each instant divided equally among in-flight spans, the ledger's own
    weighting) is reported, not scored, at 5 s and 1 s.
  * C3 is scored on draws whose busy-time mean concurrency is >= 2, as
    sum(span estimates) / pod active energy over the whole run.
  * C5 "decoupled control" = per-type rails at fixed per-type frequency (the
    only regime where constant joules-per-cycle is physically true). Two more
    worlds are reported, not scored: coupled rail with pinned frequency, and
    per-type rails with DVFS.
  * C5 split error per window = mean of |P err| / P truth and |E err| / E truth.
"""

import argparse
import os
import sys

import numpy as np
from scipy.signal import lfilter
from scipy.stats import spearmanr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))

from joule_ledger.ledger import (  # noqa: E402
    FreshCounter, Span, attribute, estimate_idle_power, pe_split, span_record)
from prereg import CRITERIA, DRAWS, MIN_SPANS  # noqa: E402

DT = 1e-3
T = 120.0
POLL = 0.010
L_CONF = 0.025   # configured latency bound, s
QUANT = 1e-3     # NVML total energy is reported in mJ


def draw_params(rng):
    return {
        "U": float(np.exp(rng.uniform(np.log(0.020), np.log(0.200)))),
        "jitter": rng.uniform(0.0, 0.3),
        "L_true": rng.uniform(0.0, L_CONF),
        "tau": rng.uniform(0.010, 0.150),
        "sat": rng.uniform(1.5, 6.0),
        "qmax": rng.uniform(0.0, 0.4),
        "pf_mult": rng.uniform(1.5, 4.0),
        "bg_duty": rng.uniform(0.0, 0.15),
        "spread": rng.uniform(0.0, 0.8),
        "rate": rng.uniform(2.0, 6.0),
        "dur_med": float(np.exp(rng.uniform(np.log(0.080), np.log(0.800)))),
        "p_idle": rng.uniform(15.0, 60.0),
        "base": rng.uniform(40.0, 150.0),
    }


def gpu_world(rng, p):
    n = int(T / DT)
    # Requests.
    reqs, s = [], 0.5
    while s < T - 6.0:
        s += rng.exponential(1.0 / p["rate"])
        dur = float(np.clip(rng.lognormal(np.log(p["dur_med"]), 0.8), 0.02, 4.0))
        if s + dur < T - 2.0:
            reqs.append((s, s + dur))
    lin = np.zeros(n)
    own = []
    alpha = 1.0 - np.exp(-DT / p["tau"])
    for s, e in reqs:
        i0, i1 = int(s / DT), int(e / DT)
        tail = int(6 * p["tau"] / DT) + 1
        j1 = min(n, i1 + tail)
        dem = np.zeros(j1 - i0)
        d = p["base"] * rng.lognormal(0.0, p["spread"]) / 2.0
        q0 = int((i1 - i0) * rng.uniform(0.0, p["qmax"]))
        act = (i1 - i0) - q0
        pf = max(1, min(int(0.25 * act), int(0.15 / DT)))
        dem[q0:q0 + pf] = d * p["pf_mult"]
        dem[q0 + pf:i1 - i0] = d
        pw = lfilter([alpha], [1.0, alpha - 1.0], dem)
        lin[i0:j1] += pw
        own.append((i0, j1, pw))
    pmax = p["sat"] * p["base"]
    sat = pmax * (1.0 - np.exp(-lin / pmax))
    factor = np.where(lin > 1e-12, sat / np.maximum(lin, 1e-12), 1.0)
    truth_dyn = np.array([float(np.sum(pw * factor[i0:j1]) * DT) for i0, j1, pw in own])
    # Untraced background.
    bg = np.zeros(n)
    k = 0
    while p["bg_duty"] > 0 and k < n:
        k += int(rng.exponential(1.0 / p["bg_duty"]) / DT)
        ln = int(rng.uniform(0.2, 2.0) / DT)
        bg[k:k + ln] += rng.uniform(0.2, 1.0) * p["base"]
        k += ln
    power = p["p_idle"] + sat + bg
    cum = np.concatenate([[0.0], np.cumsum(power) * DT])   # E at t = i*DT
    tgrid = np.arange(n + 1) * DT

    def E(x):
        return np.interp(x, tgrid, cum)

    # Counter publish times and polls.
    m = int(T / p["U"])
    tau_k = np.arange(1, m) * p["U"] + p["U"] * p["jitter"] * rng.uniform(-0.5, 0.5, m - 1)
    tau_k = np.maximum.accumulate(tau_k)
    vis = tau_k + p["L_true"]
    vals = np.floor(E(tau_k) / QUANT) * QUANT
    polls = np.arange(1, int(T / POLL)) * POLL + rng.uniform(0, 1e-4, int(T / POLL) - 1)
    idx = np.searchsorted(vis, polls, side="right") - 1
    shown = np.where(idx >= 0, vals[np.maximum(idx, 0)], 0.0)
    return reqs, truth_dyn, E, polls, shown, power, sat


def kepler_estimates(spans, polls, shown, p_idle, interval):
    """Kepler-style: pod active power per interval, integrated over each span."""
    edges = np.arange(0.0, T + interval, interval)
    reads = np.interp(edges, polls, shown, left=0.0)
    act = np.maximum(np.diff(reads) - p_idle * interval, 0.0) / interval
    est = []
    for s in spans:
        a, b = s.start, s.end
        tot = 0.0
        for i in range(int(a // interval), int(b // interval) + 1):
            lo, hi = max(a, edges[i]), min(b, edges[i + 1]) if i + 1 < len(edges) else b
            if hi > lo and i < len(act):
                tot += act[i] * (hi - lo)
        est.append(tot)
    return np.array(est), float(np.sum(act) * interval)


def kepler_split_estimates(reqs, polls, shown, p_idle, interval):
    """Kepler-style pod power per interval, split equally among in-flight spans.

    Returns per-span estimates and the pod active energy over the run. Span
    estimates sum to at most the pod active energy (no double count).
    """
    edges = np.arange(0.0, T + interval, interval)
    reads = np.interp(edges, polls, shown, left=0.0)
    act = np.maximum(np.diff(reads) - p_idle * interval, 0.0) / interval
    n = int(T / DT)
    inflight = np.zeros(n)
    for s, e in reqs:
        inflight[int(s / DT):int(e / DT)] += 1
    cell = np.minimum((np.arange(n) * DT // interval).astype(int), len(act) - 1)
    share = np.where(inflight > 0, act[cell] / np.maximum(inflight, 1), 0.0) * DT
    cum = np.concatenate([[0.0], np.cumsum(share)])
    est = np.array([cum[int(e / DT)] - cum[int(s / DT)] for s, e in reqs])
    return est, float(np.sum(act) * interval)


def mape(est, truth):
    return float(np.median(np.abs(est - truth) / truth))


def run_draw(seed, ledger_latency=L_CONF):
    rng = np.random.default_rng(seed)
    p = draw_params(rng)
    reqs, truth_dyn, E, polls, shown, power, sat = gpu_world(rng, p)
    spans = [Span(str(i), s, e) for i, (s, e) in enumerate(reqs)]
    durs = np.array([e - s for s, e in reqs])

    full = FreshCounter(latency=ledger_latency, quantum=QUANT)
    abl = FreshCounter(latency=0.0, quantum=QUANT, track_freshness=False)
    for tp, v in zip(polls, shown):
        full.observe(float(tp), float(v))
        abl.observe(float(tp), float(v))

    p_idle = estimate_idle_power(full.intervals(), spans)
    per, buckets, total = attribute(full, spans, p_idle)
    gap = abs(sum(per.values()) + buckets["idle"] + buckets["unattributed"] - total) / total

    est = np.array([per[s.span_id] for s in spans])
    truth_win = np.array([E(e) - E(s) for s, e in reqs])
    recs = [span_record(full, s, per[s.span_id]) for s in spans]
    cov = np.array([r is not None and r["device_window_lo_joules"] - 1e-9 <= tw <=
                    r["device_window_hi_joules"] + 1e-9 for r, tw in zip(recs, truth_win)])
    mf = np.array([r["measured_fraction"] if r else 0.0 for r in recs])
    ab = [abl.bounds(s.start, s.end) for s in spans]
    ab_cov = np.array([b is not None and b[0] - 1e-9 <= tw <= b[1] + 1e-9
                       for b, tw in zip(ab, truth_win)])
    short = durs < 2 * p["U"]
    long_ = ~short

    ape = np.abs(est - truth_dyn) / truth_dyn
    k5, pod5 = kepler_estimates(spans, polls, shown, p["p_idle"], 5.0)
    k1, pod1 = kepler_estimates(spans, polls, shown, p["p_idle"], 1.0)
    ks5, _ = kepler_split_estimates(reqs, polls, shown, p["p_idle"], 5.0)
    ks1, _ = kepler_split_estimates(reqs, polls, shown, p["p_idle"], 1.0)
    # C1 tightness: coverage alone says nothing about how wide [lo, hi] is.
    lo = np.array([r["device_window_lo_joules"] if r else 0.0 for r in recs])
    hiw = np.array([r["device_window_hi_joules"] if r else np.inf for r in recs])

    # Busy-time mean concurrency.
    n = len(power)
    inflight = np.zeros(n)
    for s, e in reqs:
        inflight[int(s / DT):int(e / DT)] += 1
    busy = inflight > 0
    conc = float(inflight[busy].mean()) if busy.any() else 0.0

    hi = mf >= 0.8
    rho = spearmanr(1.0 - mf, ape).correlation if len(set(mf)) > 1 else float("nan")
    return {
        "seed": seed, "params": p, "n_spans": len(spans), "conc": conc,
        "c0_gap": gap,
        "c1_cov": float(cov.mean()),
        "c1_abl_short_cov": float(ab_cov[short].mean()) if short.any() else float("nan"),
        "n_short": int(short.sum()),
        "c2_ledger": mape(est[long_], truth_dyn[long_]) if long_.any() else float("nan"),
        "c2_kepler5": mape(k5[long_], truth_dyn[long_]) if long_.any() else float("nan"),
        "c2_kepler1": mape(k1[long_], truth_dyn[long_]) if long_.any() else float("nan"),
        "c2_kepler5_split": mape(ks5[long_], truth_dyn[long_]) if long_.any() else float("nan"),
        "c2_kepler1_split": mape(ks1[long_], truth_dyn[long_]) if long_.any() else float("nan"),
        "c1_hi_over_truth": float(np.median(hiw / truth_win)),
        "c1_width_over_truth": float(np.median((hiw - lo) / truth_win)),
        "c1_lo_zero_frac": float(np.mean(lo <= 0.0)),
        "c1_lo_zero_frac_short": float(np.mean(lo[short] <= 0.0)) if short.any() else float("nan"),
        "c1_mf": [float(x) for x in np.quantile(mf, [0.1, 0.5, 0.9])],
        "c3_kepler5_ratio": float(k5.sum() / pod5) if pod5 > 0 else float("nan"),
        "c3_ledger_ratio": float((est.sum() + buckets["unattributed"]) /
                                 (total - buckets["idle"])),
        "c4_hiconf": mape(est[hi], truth_dyn[hi]) if hi.any() else float("nan"),
        "c4_hiconf_n": int(hi.sum()),
        "c4_rho": float(rho),
        "idle_est": p_idle,
    }


# --------------------------------------------------------------------- CPU / C5

def cpu_world(rng, coupled, dvfs, windows=12, ticks=3000, dt=0.010):
    def volt(f):
        return 0.65 + 0.13 * (f - 0.8)

    cP, cE = rng.uniform(0.9, 1.3), rng.uniform(0.25, 0.45)   # J per 1e9 cyc at 1 V
    lkP, lkE = rng.uniform(0.6, 1.0), rng.uniform(0.15, 0.3)  # W per active core at 1 V^3
    out = []
    for _ in range(windows):
        rows, tP, tE = [], 0.0, 0.0
        k = 0
        while k < ticks:
            ln = int(rng.uniform(20, 200))
            nP, nE = int(rng.integers(0, 9)), int(rng.integers(0, 17))
            fP = rng.uniform(1.0, 4.4) if dvfs else 3.0
            fE = rng.uniform(1.0, 3.0) if dvfs else 2.2
            if coupled:
                vP = vE = volt(max(fP if nP else 0.8, fE if nE else 0.8))
            else:
                vP, vE = volt(fP), volt(fE)
            for _ in range(min(ln, ticks - k)):
                uP, uE = rng.uniform(0.7, 1.0), rng.uniform(0.7, 1.0)
                cycP = nP * uP * fP * 1e9 * dt
                cycE = nE * uE * fE * 1e9 * dt
                eP = cP * cycP / 1e9 * vP ** 2 + lkP * nP * vP ** 3 * dt
                eE = cE * cycE / 1e9 * vE ** 2 + lkE * nE * vE ** 3 * dt
                idle = 2.0 * dt
                meas = (eP + eE + idle) * (1 + rng.normal(0, 0.01))
                fmax = max(fP if nP else 0.0, fE if nE else 0.0)
                rows.append((dt, cycP, cycE, fmax, meas))
                tP += eP
                tE += eE
            k += ln
        g = pe_split(rows)
        sP = sum(r[1] for r in rows) / 1e9 * g["aP"] if "aP" in g else float("nan")
        sE = sum(r[2] for r in rows) / 1e9 * g["aE"] if "aE" in g else float("nan")
        err = (abs(sP - tP) / tP + abs(sE - tE) / tE) / 2
        out.append({"accepted": g["accepted"], "reason": g["reason"], "err": err})
    return out


def c5(seed):
    rng = np.random.default_rng(seed + 10_000)
    worlds = {
        "coupled_dvfs": cpu_world(rng, True, True),
        "decoupled_fixed": cpu_world(rng, False, False),
        "coupled_pinned": cpu_world(rng, True, False),
        "decoupled_dvfs": cpu_world(rng, False, True),
    }
    summ = {}
    for name, ws in worlds.items():
        acc = [w for w in ws if w["accepted"]]
        summ[name] = {
            "accept_frac": len(acc) / len(ws),
            "naive_err": float(np.mean([w["err"] for w in ws])),
            "accepted_err": float(np.mean([w["err"] for w in acc])) if acc else float("nan"),
        }
    return summ


def c5_seeds(seed, n):
    """C5 worlds over n CPU seeds (seed, seed+1, ...): min / median / max."""
    runs = [c5(seed + i) for i in range(n)]
    out = {}
    for name in runs[0]:
        acc = np.array([r[name]["accept_frac"] for r in runs])
        err = np.array([r[name]["naive_err"] for r in runs])
        out[name] = {"refuse": [float(f(1 - acc)) for f in (np.min, np.median, np.max)],
                     "naive_err": [float(f(err)) for f in (np.min, np.median, np.max)]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--seed", type=int, default=20261016)
    ap.add_argument("--ledger-latency", type=float, default=L_CONF,
                    help="latency bound handed to the ledger; true latency is drawn in "
                         "[0, %.3f] s. Set it lower to see coverage break." % L_CONF)
    ap.add_argument("--c5-seeds", type=int, default=0,
                    help="also report C5 min/median/max over this many CPU seeds "
                         "(seed, seed+1, ...); reported, not scored")
    args = ap.parse_args()
    C = CRITERIA
    rs = [run_draw(args.seed + i, args.ledger_latency) for i in range(args.draws)]
    for r in rs:
        p = r["params"]
        print(f"seed={r['seed']} n={r['n_spans']} U={p['U']*1e3:5.0f}ms conc={r['conc']:.2f} "
              f"cov={r['c1_cov']:.4f} ablS={r['c1_abl_short_cov']:.2f}({r['n_short']}) "
              f"led={r['c2_ledger']:.3f} k5={r['c2_kepler5']:.3f} k1={r['c2_kepler1']:.3f} "
              f"dbl5={r['c3_kepler5_ratio']:.2f} hiC={r['c4_hiconf']:.3f}({r['c4_hiconf_n']}) "
              f"rho={r['c4_rho']:.2f} gap={r['c0_gap']:.1e}")

    def frac(pred):
        return float(np.mean([bool(pred(r)) for r in rs]))

    res = {}
    res["C0"] = max(r["c0_gap"] for r in rs) <= C["C0_conservation_rel_gap_max"]
    res["min_spans"] = min(r["n_spans"] for r in rs) >= MIN_SPANS
    c1 = lambda r: r["c1_cov"] >= C["C1_coverage_min"]  # noqa: E731
    res["C1_coverage_all_draws"] = frac(c1) == 1.0
    res["C1_worst_draw_coverage"] = min(r["c1_cov"] for r in rs)
    pooled_short = [r["c1_abl_short_cov"] for r in rs if r["n_short"] > 0]
    res["C1_ablation_short_cov"] = float(np.mean(pooled_short))
    res["C1_ablation"] = res["C1_ablation_short_cov"] < C["C1_ablation_short_coverage_max"]
    c2 = lambda r: (r["c2_ledger"] <= C["C2_ledger_mape_max"] and  # noqa: E731
                    r["c2_kepler5"] >= C["C2_kepler_ratio_min"] * r["c2_ledger"])
    res["C2_median_ledger"] = float(np.median([r["c2_ledger"] for r in rs]))
    res["C2_median_kepler5"] = float(np.median([r["c2_kepler5"] for r in rs]))
    res["C2_median_kepler1"] = float(np.median([r["c2_kepler1"] for r in rs]))
    # Reported, not scored: the double-count-free comparator and the per-draw
    # C2 pass rate. The pooled C2 pass is marginal (see README).
    res["C2_median_kepler5_split"] = float(np.median([r["c2_kepler5_split"] for r in rs]))
    res["C2_median_kepler1_split"] = float(np.median([r["c2_kepler1_split"] for r in rs]))
    res["C2_ratio_kepler5_split"] = res["C2_median_kepler5_split"] / res["C2_median_ledger"]
    res["C2_ratio_kepler1_split"] = res["C2_median_kepler1_split"] / res["C2_median_ledger"]
    res["C2"] = (res["C2_median_ledger"] <= C["C2_ledger_mape_max"] and
                 res["C2_median_kepler5"] >= C["C2_kepler_ratio_min"] * res["C2_median_ledger"])
    res["C1_median_hi_over_truth"] = float(np.median([r["c1_hi_over_truth"] for r in rs]))
    res["C1_median_width_over_truth"] = float(np.median([r["c1_width_over_truth"] for r in rs]))
    res["C1_lo_zero_frac"] = float(np.median([r["c1_lo_zero_frac"] for r in rs]))
    res["C1_lo_zero_frac_short"] = float(np.nanmedian([r["c1_lo_zero_frac_short"] for r in rs]))
    res["C1_mf_p10_p50_p90"] = [round(float(np.median([r["c1_mf"][i] for r in rs])), 3)
                                for i in range(3)]
    hc = [r for r in rs if r["conc"] >= 2.0]
    res["C3_draws"] = len(hc)
    res["C3_kepler5_ratio_median"] = float(np.median([r["c3_kepler5_ratio"] for r in hc])) if hc else float("nan")
    res["C3_ledger_ratio_maxdev"] = float(max(abs(r["c3_ledger_ratio"] - 1) for r in rs))
    res["C3"] = bool(hc) and res["C3_kepler5_ratio_median"] >= C["C3_kepler_doublecount_min"] \
        and res["C3_ledger_ratio_maxdev"] <= 1e-9
    c4 = lambda r: (r["c4_hiconf"] <= C["C4_hiconf_mape_max"] and  # noqa: E731
                    r["c4_rho"] >= C["C4_spearman_min"])
    res["C4_median_hiconf"] = float(np.nanmedian([r["c4_hiconf"] for r in rs]))
    res["C4_median_rho"] = float(np.nanmedian([r["c4_rho"] for r in rs]))
    res["C4"] = (res["C4_median_hiconf"] <= C["C4_hiconf_mape_max"] and
                 res["C4_median_rho"] >= C["C4_spearman_min"])
    res["C6_frac_C1"], res["C6_frac_C2"], res["C6_frac_C4"] = frac(c1), frac(c2), frac(c4)
    res["C6"] = min(res["C6_frac_C1"], res["C6_frac_C2"], res["C6_frac_C4"]) >= C["C6_pass_frac_min"]

    w = c5(args.seed)
    for name, v in w.items():
        print(f"C5 {name:16s} accept={v['accept_frac']:.2f} naive_err={v['naive_err']:.3f} "
              f"accepted_err={v['accepted_err']:.3f}")
    if args.c5_seeds:
        for name, v in c5_seeds(args.seed, args.c5_seeds).items():
            print(f"C5x{args.c5_seeds} {name:16s} refuse min/med/max="
                  f"{v['refuse'][0]:.2f}/{v['refuse'][1]:.2f}/{v['refuse'][2]:.2f} "
                  f"naive_err min/med/max={v['naive_err'][0]:.3f}/{v['naive_err'][1]:.3f}/"
                  f"{v['naive_err'][2]:.3f}")
    res["C5"] = (w["coupled_dvfs"]["naive_err"] >= C["C5_coupled_naive_err_min"] and
                 1 - w["coupled_dvfs"]["accept_frac"] >= C["C5_coupled_refuse_min"] and
                 w["decoupled_fixed"]["accept_frac"] >= C["C5_decoupled_accept_min"] and
                 w["decoupled_fixed"]["accepted_err"] <= C["C5_decoupled_err_max"])

    print()
    for k, v in res.items():
        print(f"{k:28s} {v}")
    verdict = all(res[k] for k in ("C0", "min_spans", "C1_coverage_all_draws", "C1_ablation",
                                    "C2", "C3", "C4", "C5", "C6"))
    print("\nVERDICT:", "PASS" if verdict else "FAIL")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
