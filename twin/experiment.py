#!/usr/bin/env python3
"""experiment.py — run the pre-registered experiments of docs/placement-twin.md on sim_world.

The twin gets only observations (noisy counters, measured times, measured clocks). Truth from
the world is used only to score. Run single-threaded and niced; it is light by design:

  nice -n 19 taskset -c 31 python3 twin/experiment.py [--seeds N] [--only A,B,C,D] [--out FILE]
"""
import argparse
import json
import math
import random
import statistics as st
import sys

import numpy as np

import placement_twin as pt
import sim_world as sw


def mae(a, b):
    return float(np.mean(np.abs(np.asarray(a) - np.asarray(b))))


def mape(pred, true):
    return float(np.mean(np.abs(np.asarray(pred) / np.asarray(true) - 1)))


def summarize(vals, higher_is_worse=True):
    """Median and the worst decile over seeds. For a margin (higher is better) pass False."""
    v = sorted(vals, reverse=not higher_is_worse)
    return {"median": round(st.median(v), 4), "worst_decile": round(v[min(len(v) - 1, int(math.ceil(0.9 * len(v))) - 1)], 4)}


# ------------------------------------------------------------------ A. E/P factor
def run_A(seed, null=False, shuffle=False):
    w = sw.World(seed, null_factor=null)
    W = w.make_workloads(24)
    truth = [w.ep_factor(x) for x in W]
    meas = [w.jitter(t, 0.02) for t in truth]
    img = [w.counters(x, "image") for x in W]
    start = [w.counters(x, "start2s") for x in W]
    if shuffle:
        perm = list(range(len(W)))
        random.Random(seed + 99).shuffle(perm)
        img = [dict(img[p], off_cpu=img[p]["off_cpu"]) for p in perm]
    res = {k: [] for k in ("twin", "twin_start", "twin_flat", "const", "td_class", "off_only")}
    for i in range(len(W)):
        tr = [j for j in range(len(W)) if j != i]
        rows = [img[j] for j in tr]
        ys = [meas[j] for j in tr]
        res["twin"].append(pt.EPModel().fit(rows, ys).predict(img[i]))
        res["twin_start"].append(pt.EPModel().fit([start[j] for j in tr], ys).predict(start[i]))
        res["twin_flat"].append(pt.EPModel(structured=False).fit(rows, ys).predict(img[i]))
        res["const"].append(float(np.median(ys)))
        cls = pt.td_class(img[i])
        same = [y for r, y in zip(rows, ys) if pt.td_class(r) == cls]
        res["td_class"].append(float(np.median(same)) if same else float(np.median(ys)))
        c = float(np.median([(y - r["off_cpu"]) / (1 - r["off_cpu"]) for r, y in zip(rows, ys)]))
        res["off_only"].append(img[i]["off_cpu"] + (1 - img[i]["off_cpu"]) * c)
    return {k: mae(v, truth) for k, v in res.items()}, float(np.std(truth))


# ------------------------------------------------------------------ B. packing
def run_B(seed, n_mix=40):
    w = sw.World(seed)
    W = w.make_workloads(24)
    img = [w.counters(x, "image") for x in W]
    fE = w.clocks(0, 4, 0)[1]
    rng = random.Random(seed + 7)

    def mixes(pool):
        out = []
        for _ in range(n_mix):
            idx = rng.sample(pool, 4)
            true = w.module_slowdowns([W[i]["main"]["ws"] for i in idx], [W[i]["main"] for i in idx], fE)
            out.append((idx, true))
        return out
    train, test = mixes(list(range(12))), mixes(list(range(12, 24)))
    model = pt.PackingModel().fit([[img[i] for i in idx] for idx, _ in train],
                                  [[w.jitter(v, 0.02) for v in s] for _, s in train])
    pred, none, true = [], [], []
    for idx, s in test:
        pred += model.predict([img[i] for i in idx])
        none += [1.0] * 4
        true += s
    return {"twin": mape(pred, true), "no_interference": mape(none, true)}


# ------------------------------------------------------------------ C. probes + pairs
BW1 = {"P": 22.0, "E": 18.0, "G": 40.0}
INTENSITY = {"P": (4.0, 10.0, 16.0), "E": (4.0, 10.0, 15.0), "G": (8.0, 20.0, 32.0)}
N_TARGET = {"P": 1, "E": 4, "G": 1}
EXT_LEVELS = np.linspace(0, 45, 8)
GEN = {"P": "E", "E": "P", "G": "P"}
PROBE_V2 = True     # --probe v1 reproduces the pre-registered grid and linear clock model
CLOCK_MODE = "table"


def kernel(w, engine, n, demand, iso_set):
    """Streaming probe on `engine` with alone-demand `demand` GB/s (1 GB per unit)."""
    f = w.clocks(*iso_set)[{"P": 0, "E": 1, "G": 2}[engine]]
    C = max(1e-3, (1.0 / demand - 1.0 / BW1[engine]) * f)
    return {"engine": engine, "n": n, "C": C, "B": 1.0, "bw1": BW1[engine]}


def iso_set_of(engine, n):
    return (n if engine == "P" else 0, n if engine == "E" else 0, 1.0 if engine == "G" else 0.0)


def generators(w, engine, total_bw, n_compute=0):
    """External load on `engine`: bandwidth streams (5 GB/s per E-core, 12 per P-core) or spin."""
    comps = []
    if total_bw > 0:
        per = 5.0 if engine == "E" else 12.0
        n = int(math.ceil(total_bw / per))
        for _ in range(n):
            comps.append(kernel(w, engine, 1, total_bw / n, iso_set_of(engine, n)))
    for _ in range(n_compute):
        comps.append({"engine": engine, "n": 1, "C": 5.0, "B": 0.0, "bw1": 1.0})
    return comps


def clock_sweep(w):
    """v2 (post-hoc, see docs): 71 spin-only runs over nP {0,1,2,4,6,8} x nE {0,1,4,8,12,16} x iGPU,
    each reading every active engine's clock. Covers the active sets placements actually use."""
    out = []
    for nP in (0, 1, 2, 4, 6, 8):
        for nE in (0, 1, 4, 8, 12, 16):
            for gt in (0.0, 1.0):
                if nP == nE == 0 and not gt:
                    continue
                f = w.clocks(nP, nE, gt)
                for eng, k, on in (("P", 0, nP), ("E", 1, nE), ("G", 2, gt)):
                    if on:
                        out.append((eng, nP, nE, gt, w.jitter(f[k], 0.005)))
    return out


def probe_grid(w, sweep=None):
    """72 bandwidth runs + 15 compute-only runs (+ the v2 clock sweep); observations only."""
    sweep = PROBE_V2 if sweep is None else sweep
    obs = {"bw": [], "clock": clock_sweep(w) if sweep else []}
    for T in ("P", "E", "G"):
        iso = iso_set_of(T, N_TARGET[T])
        for d in INTENSITY[T]:
            tgt = kernel(w, T, N_TARGET[T], d, iso)
            t_iso = w.corun([tgt])[0]
            kappa = w.jitter((tgt["C"] / w.clocks(*iso)[{"P": 0, "E": 1, "G": 2}[T]]) / t_iso, 0.03)
            for x in EXT_LEVELS:
                comps = [tgt] + generators(w, GEN[T], x)
                t = w.corun(comps)[0]
                nP = sum(c["n"] for c in comps if c["engine"] == "P")
                nE = sum(c["n"] for c in comps if c["engine"] == "E")
                gt = 1.0 if T == "G" else 0.0
                f = w.clocks(nP, nE, gt)
                obs["bw"].append(dict(T=T, d=d, x=x, s=w.jitter(t / t_iso, 0.01), kappa=kappa,
                                      r=w.clocks(*iso)[{"P": 0, "E": 1, "G": 2}[T]] / w.jitter(f[{"P": 0, "E": 1, "G": 2}[T]], 0.005)))
                for eng, k in (("P", 0), ("E", 1), ("G", 2)):
                    if (eng == "P" and nP) or (eng == "E" and nE) or (eng == "G" and gt):
                        obs["clock"].append((eng, nP, nE, gt, w.jitter(f[k], 0.005)))
        tgt = kernel(w, T, N_TARGET[T], INTENSITY[T][1], iso)
        for nc in (0, 2, 4, 6, 7 if GEN[T] == "P" else 8):
            comps = [tgt] + generators(w, GEN[T], 0, nc)
            nP = sum(c["n"] for c in comps if c["engine"] == "P")
            nE = sum(c["n"] for c in comps if c["engine"] == "E")
            gt = 1.0 if T == "G" else 0.0
            f = w.clocks(nP, nE, gt)
            for eng, k in (("P", 0), ("E", 1), ("G", 2)):
                if (eng == "P" and nP) or (eng == "E" and nE) or (eng == "G" and gt):
                    obs["clock"].append((eng, nP, nE, gt, w.jitter(f[k], 0.005)))
    return obs


def fit_twin(obs, clock_mode):
    """clock_mode: 'table' (v2), 'linear' (v1, pre-registered) or 'off' (bandwidth-only)."""
    clock_aware = clock_mode != "off"
    clock = pt.ClockModel(clock_mode).fit(obs["clock"])
    curves = {}
    for T in ("P", "E", "G"):
        ds, cs = [], []
        for d in INTENSITY[T]:
            pts = [o for o in obs["bw"] if o["T"] == T and o["d"] == d]
            s = [o["s"] / (1 + o["kappa"] * (o["r"] - 1)) if clock_aware else o["s"] for o in pts]
            cs.append(pt.fit_pccs([o["x"] for o in pts], s, step=1.0))
            ds.append(d)
        curves[T] = pt.EngineCurves(ds, cs)
    return curves, clock


def corunner(w, wl, engine):
    """Truth component for one copy of a tool workload (main phase, CPU only, 1e9 instr/unit)."""
    ph = wl["main"]
    f = w.clocks(*iso_set_of(engine, 1))[0 if engine == "P" else 1]
    tpi = w._tpi(ph, engine, f)
    dram_ns = ph["dram"] * (w.m["dram"] if engine == "E" else 1.0)
    C = (tpi - dram_ns) * f
    B = max(1e-3, ph["dram"] / 0.09 * 64 * 5.3 / 1000)   # GB per 1e9 instr, from DRAM misses
    return {"engine": engine, "n": 1, "C": C, "B": B, "bw1": B / max(1e-6, dram_ns)}


def kappa_probe(w, comp):
    """Two-point clock-scaling run of a P-core component, alone: caps 5.3 and 3.0 GHz."""
    t = lambda f: comp["C"] * comp.get("l2x", 1.0) / f + comp["B"] / comp["bw1"] + comp.get("io_s", 0.0)
    return pt.kappa_from_scaling(w.jitter(t(5.3), 0.01), w.jitter(t(3.0), 0.01), 5.3, 3.0)


def observe(w, comp, iso_set, kappa_true):
    t = w.corun([comp])[0]
    return {"engine": comp["engine"], "n": comp["n"], "iso": w.jitter(t, 0.01),
            "bw": w.jitter(comp["B"] / t, 0.03), "kappa": min(1, w.jitter(kappa_true, 0.05)), "iso_set": iso_set}


def run_C(seed, gamma, envelope, n_pairs=16, hetero=False):
    w = sw.World(seed, gamma=gamma, envelope=envelope, hetero=hetero)
    W = w.make_workloads(24)
    obs = probe_grid(w)
    twins = {"clock_aware": fit_twin(obs, CLOCK_MODE), "bw_only": fit_twin(obs, "off")}
    rng = random.Random(seed + 3)
    out = {k: {"dec": [], "co": []} for k in (*twins, "roofline")}
    for _ in range(n_pairs):
        wl = W[rng.randrange(len(W))]
        eng = rng.choice(["P", "E"])
        n = rng.choice([2, 4, 8] if eng == "P" else [4, 8, 16])
        # iGPU decode, 30 GB/s alone: 30 % of its alone time is GT compute, 70 % streaming weights.
        dec = {"engine": "G", "n": 1, "C": 0.3 / 30.0 * w.clocks(0, 0, 1.0)[2], "B": 1.0, "bw1": 30.0 / 0.7}
        cr = corunner(w, wl, eng)
        truth = w.corun([dec] + [cr] * n)
        # iGPU decode kappa: measured by a two-point GT clock-scaling run (rps max 1650 vs 1100 MHz).
        o_dec = observe(w, dec, (0, 0, 1.0), 0.3)
        # CPU co-runner kappa: measured on a P-core by clock scaling, converted for E-cores.
        o_cr = observe(w, cr, iso_set_of(eng, 1), 0.0)
        kp = kappa_probe(w, corunner(w, wl, "P"))
        o_cr["kappa"] = kp if eng == "P" else pt.kappa_on_e(kp, w.jitter(w.ep_factor(wl), 0.02))
        comps = [o_dec] + [o_cr] * n
        for k, (curves, clock) in twins.items():
            p = pt.fixed_point(comps, curves, clock)
            out[k]["dec"].append(p[0] / truth[0])
            out[k]["co"].append(p[1] / truth[1])
        p = pt.roofline(comps)
        out["roofline"]["dec"].append(p[0] / truth[0])
        out["roofline"]["co"].append(p[1] / truth[1])
    # time ratio r -> tok/s error |1/r - 1|; latency error |r - 1|
    return {k: {"dec_mape": float(np.mean([abs(1 / r - 1) for r in v["dec"]])),
                "co_mape": float(np.mean([abs(r - 1) for r in v["co"]]))} for k, v in out.items()}


# ------------------------------------------------------------------ D. placement
DEC_C1 = 2.4      # Gcycles per token, all threads together
DEC_B = 0.9       # GB per token (weights streamed)


def decode_comp(w, nP_threads, nE_threads=0):
    n = nP_threads + nE_threads
    fP, fE, _ = w.clocks(nP_threads, nE_threads, 0)
    slow = 1.0 if nE_threads == 0 else fP / (fE / 1.3)    # barrier: the slowest thread sets the pace
    return {"engine": "P", "n": nP_threads, "C": DEC_C1 / n * slow, "B": DEC_B, "bw1": min(45.0, 9.0 * n)}


def tool_comp(w, wl, engine, l2x=1.0):
    ph = wl["main"]
    c = corunner(w, wl, engine)
    # units of 1e9 instructions: seconds / (ns per instruction)
    instr = (wl["run_s"] - wl["start_s"]) * (1 - ph["io"]) / w._tpi(ph, "P", sw.P_TURBO[1] * w.derate(1, 0, 0)[0])
    return {"engine": engine, "n": 1, "C": c["C"] * instr, "B": c["B"] * instr, "bw1": c["bw1"],
            "io_s": (wl["run_s"] - wl["start_s"]) * ph["io"], "l2x": l2x}


def truth_eval(w, mix, pl, slo, unpinned_e_dec=0):
    t = truth_times(w, mix, pl, unpinned_e_dec)
    return 1.0 / t[0], sum(1 for i in range(len(mix)) if t[i + 1] > slo[i])


def truth_times(w, mix, pl, unpinned_e_dec=0):
    """Hidden-world seconds: [decoder s/token] + [tool run seconds]."""
    tools_p = sum(1 for v in pl if v == "P")
    comps = [decode_comp(w, 8 - tools_p - unpinned_e_dec, unpinned_e_dec)]
    fE = w.clocks(0, 4, 0)[1]
    l2x = {}
    for m in range(sw.E_MODULES):
        idx = [i for i, v in enumerate(pl) if v == m]
        if idx:
            for i, s in zip(idx, w.module_slowdowns([mix[i]["main"]["ws"] for i in idx], [mix[i]["main"] for i in idx], fE)):
                l2x[i] = s
    for i, v in enumerate(pl):
        comps.append(tool_comp(w, mix[i], "P" if v == "P" else "E", l2x.get(i, 1.0)))
    return w.corun(comps)


def run_D(seed, n_mixes=6, n_tools=6, slo_mult=1.5, hetero=False, envelope="host"):
    w = sw.World(seed, envelope=envelope, hetero=hetero)
    W = w.make_workloads(24)
    img = [w.counters(x, "image") for x in W]
    meas_f = [w.jitter(w.ep_factor(x), 0.02) for x in W]
    obs = probe_grid(w)
    curves, clock = fit_twin(obs, CLOCK_MODE)
    rng = random.Random(seed + 11)
    fE = w.clocks(0, 4, 0)[1]
    rows_out = []
    for mix_i in range(n_mixes):
        idx = rng.sample(range(24), n_tools)
        mix = [W[i] for i in idx]
        others = [j for j in range(24) if j not in idx]
        ep = pt.EPModel().fit([img[j] for j in others], [meas_f[j] for j in others])
        # The twin's own error estimate, from calibration data only: LOO relative error of the
        # E/P factor over the calibration pods. Used as the SLO guard band of "twin_margin".
        self_err = float(np.mean([abs(pt.EPModel().fit([img[j] for j in others if j != k],
                                                       [meas_f[j] for j in others if j != k]).predict(img[k])
                                      / meas_f[k] - 1) for k in others]))
        pmixes, psl = [], []
        for _ in range(30):
            s = rng.sample(others, 4)
            pmixes.append([img[j] for j in s])
            psl.append([w.jitter(v, 0.02) for v in w.module_slowdowns([W[j]["main"]["ws"] for j in s], [W[j]["main"] for j in s], fE)])
        pack = pt.PackingModel().fit(pmixes, psl)
        # Observations the twin may use for these pods: P-alone latency, demand, counters.
        # SLO (deviation 2, docs section 4): slo_mult x the pod's latency in the status-quo
        # all-P placement with the decoder running. The pre-registered "1.5 x isolated P latency"
        # was infeasible: the oracle itself violated it on 3-5 of 6 pods per mix.
        allp_truth = w.corun([decode_comp(w, 8 - n_tools)] + [tool_comp(w, wl, "P") for wl in mix])[1:]
        slo = [slo_mult * t for t in allp_truth]
        p_iso, tobs = [], []
        for k, wl in enumerate(mix):
            c = tool_comp(w, wl, "P")
            t = w.corun([c])[0]
            tm = w.jitter(t, 0.01)
            p_iso.append(tm)
            tobs.append({"bw": w.jitter(c["B"] / t, 0.03), "row": img[idx[k]], "kappa": kappa_probe(w, c)})
        dec_iso = {}
        for n in range(1, 9):
            c = decode_comp(w, n)
            t = w.corun([c])[0]
            dec_iso[n] = (w.jitter(t, 0.01), w.jitter(c["B"] / t, 0.03), kappa_probe(w, c))
        const = float(np.median([meas_f[j] for j in others]))

        def twin_eval(pl, use_pack=True, use_clock=True, margin=0.0):
            t = twin_times(pl, use_pack, use_clock)
            return 1.0 / t[0], sum(1 for i in range(len(mix)) if t[i + 1] * (1 + margin) > slo[i])

        def twin_times(pl, use_pack=True, use_clock=True):
            nP_tools = sum(1 for v in pl if v == "P")
            nd = 8 - nP_tools
            comps = [{"engine": "P", "n": nd, "iso": dec_iso[nd][0], "bw": dec_iso[nd][1], "kappa": dec_iso[nd][2], "iso_set": (nd, 0, 0)}]
            packs = {}
            for m in range(sw.E_MODULES):
                ii = [i for i, v in enumerate(pl) if v == m]
                if ii:
                    for i, s in zip(ii, pack.predict([tobs[i]["row"] for i in ii])):
                        packs[i] = s if use_pack else 1.0
            for i, v in enumerate(pl):
                r = tobs[i]["row"]
                kap = tobs[i]["kappa"]
                if v == "P":
                    comps.append({"engine": "P", "n": 1, "iso": p_iso[i], "bw": tobs[i]["bw"], "kappa": kap, "iso_set": (1, 0, 0)})
                else:
                    f = ep.predict(r)
                    comps.append({"engine": "E", "n": 1, "iso": p_iso[i] * f, "bw": tobs[i]["bw"] / f, "kappa": pt.kappa_on_e(kap, f),
                                  "iso_set": (0, 1, 0), "pack": packs[i]})
            return pt.fixed_point(comps, curves, clock if use_clock else pt.ClockModel("off"))

        pls = list(pt.enumerate_placements(n_tools))
        truth = {pl: truth_eval(w, mix, pl, slo) for pl in pls}
        oracle_pl = max(truth, key=lambda pl: (-truth[pl][1], truth[pl][0]))
        oracle, oracle_viol = truth[oracle_pl]

        def rr(pods):
            out, m = [], 0
            for e in pods:
                if e:
                    out.append(m % sw.E_MODULES)
                    m += 1
                else:
                    out.append("P")
            return canon(out)

        def canon(pl):
            remap, out = {}, []
            for v in pl:
                if v != "P":
                    remap.setdefault(v, len(remap))
                    v = remap[v]
                out.append(v)
            return tuple(out)

        picks = {
            "twin": pt.choose(pls, twin_eval),
            "twin_margin": pt.choose(pls, lambda pl: twin_eval(pl, margin=2 * self_err)),
            "twin_no_pack": pt.choose(pls, lambda pl: twin_eval(pl, use_pack=False)),
            "twin_no_clock": pt.choose(pls, lambda pl: twin_eval(pl, use_clock=False)),
            "all_P": tuple(["P"] * n_tools),
            "all_E": rr([True] * n_tools),
            "io_rule": rr([tobs[i]["row"]["off_cpu"] > 0.5 for i in range(n_tools)]),
            "const_ratio": rr([const * p_iso[i] <= slo[i] for i in range(n_tools)]),
        }
        row = {"mix": mix_i, "oracle_tok_s": oracle, "oracle_violations": oracle_viol,
               "moved_off_P": {}, "frac_oracle": {}, "violations": {}}
        # Twin prediction error (pred / truth - 1) over a sample of placements, by component kind.
        err = {"decode": [], "tool_P": [], "tool_E": []}
        for pl in random.Random(seed + mix_i).sample(pls, min(60, len(pls))):
            tp, tt = twin_times(pl), truth_times(w, mix, pl)
            err["decode"].append(tp[0] / tt[0] - 1)
            for i, v in enumerate(pl):
                err["tool_P" if v == "P" else "tool_E"].append(tp[i + 1] / tt[i + 1] - 1)
        row["self_err"] = self_err
        row["twin_err"] = {k: {"bias": float(np.mean(v)), "mape": float(np.mean(np.abs(v)))} for k, v in err.items() if v}
        # Score: foreground tok/s as a fraction of the oracle, and 0 when the placement violates
        # more SLOs than the oracle does (throughput bought with SLO misses does not count).
        def score(tok, v):
            return tok / oracle if v <= oracle_viol else 0.0
        for k, pl in picks.items():
            tok, v = truth[pl]
            row["frac_oracle"][k] = score(tok, v)
            row["violations"][k] = v
            row["moved_off_P"][k] = sum(1 for x in pl if x != "P") / n_tools
        # unpinned: 8 P slots among 8 decode + 6 tool threads, chosen at random; tools off P spread
        toks, vs, mv = [], [], []
        for _ in range(20):
            threads = ["d"] * 8 + list(range(n_tools))
            rng.shuffle(threads)
            onP = threads[:8]
            e_dec = sum(1 for x in onP if x != "d")   # decode threads displaced to E
            pl = rr([i not in onP for i in range(n_tools)])
            tok, v = truth_eval(w, mix, pl, slo, unpinned_e_dec=e_dec)
            toks.append(score(tok, v))
            vs.append(v)
            mv.append(sum(1 for x in pl if x != "P") / n_tools)
        row["frac_oracle"]["unpinned"] = float(np.mean(toks))
        row["violations"]["unpinned"] = float(np.mean(vs))
        row["moved_off_P"]["unpinned"] = float(np.mean(mv))
        rows_out.append(row)
    return rows_out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--only", default="A,B,C,D")
    ap.add_argument("--out", default=None)
    ap.add_argument("--probe", choices=("v1", "v2"), default="v2")
    a = ap.parse_args(argv)
    global PROBE_V2, CLOCK_MODE
    PROBE_V2 = a.probe == "v2"
    CLOCK_MODE = "table" if PROBE_V2 else "linear"
    parts = set(a.only.split(","))
    res = {}
    seeds = range(a.seeds)
    if "A" in parts:
        for tag, kw in (("A", {}), ("A_null", {"null": True}), ("A_shuffled", {"shuffle": True})):
            per = [run_A(s, **kw)[0] for s in seeds]
            res[tag] = {k: summarize([p[k] for p in per]) for k in per[0]}
            res[tag]["baseline_margin_min"] = summarize([min(p["const"], p["td_class"]) - p["twin"] for p in per], False)
            res[tag]["start_over_image"] = summarize([p["twin_start"] / p["twin"] for p in per], False)
        print(json.dumps({k: res[k] for k in res if k.startswith("A")}, indent=1), flush=True)
    if "B" in parts:
        per = [run_B(s) for s in seeds]
        res["B"] = {k: summarize([p[k] for p in per]) for k in per[0]}
        print(json.dumps({"B": res["B"]}, indent=1), flush=True)
    if "C" in parts:
        res["C"] = {}
        for env, g, het in (("host", 0.0, False), ("host", 0.3, False), ("host", 1.0, False),
                            ("spec35", 0.3, False), ("host", 0.3, True), ("spec35", 0.3, True)):
            per = [run_C(s, g, env, hetero=het) for s in range(min(a.seeds, 10))]
            res["C"][f"{env}_gamma{g}" + ("_hetero" if het else "")] = {
                k: {m: summarize([p[k][m] for p in per]) for m in per[0][k]} for k in per[0]}
        print(json.dumps({"C": res["C"]}, indent=1), flush=True)
    for slo_mult, env, het in ((1.5, "host", False), (2.0, "host", False),
                               (1.5, "host", True), (1.5, "spec35", True)):
        if "D" not in parts:
            break
        rows = []
        for s in range(min(a.seeds, 5)):
            for r in run_D(s, slo_mult=slo_mult, envelope=env, hetero=het):
                r["seed"] = s
                rows.append(r)
        methods = rows[0]["frac_oracle"].keys()
        tag = f"D_slo{slo_mult}_{env}" + ("_hetero" if het else "")
        res[tag] = {m: {"frac_oracle_min": round(min(r["frac_oracle"][m] for r in rows), 4),
                        "frac_oracle_median": round(st.median(r["frac_oracle"][m] for r in rows), 4),
                        "mixes_below_95pct": sum(1 for r in rows if r["frac_oracle"][m] < 0.95),
                        "violation_rate": round(sum(r["violations"][m] for r in rows) / (6 * len(rows)), 4),
                        "moved_off_P": round(st.mean(r["moved_off_P"][m] for r in rows), 4)} for m in methods}
        res[tag]["oracle"] = {"violation_rate": round(sum(r["oracle_violations"] for r in rows) / (6 * len(rows)), 4)}
        res[tag]["n_mixes"] = len(rows)
        res[tag]["twin_latency_error"] = {k: {m: round(st.median(r["twin_err"][k][m] for r in rows if k in r["twin_err"]), 4)
                                              for m in ("bias", "mape")} for k in ("decode", "tool_P", "tool_E")}
        res[tag]["self_err_median"] = round(st.median(r["self_err"] for r in rows), 4)
        print(json.dumps({tag: res[tag]}, indent=1), flush=True)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(res, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
