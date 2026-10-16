#!/usr/bin/env python3
"""Score the oracle against the truth world on the pre-registered grid (PREREG.md).

  python3 oracle/run_sim.py                 # full grid + stress sweep, writes oracle/sim_results.json
  python3 oracle/run_sim.py --seeds 2 --quick

The oracle only receives what a real user would have: cards, the engine
config, the trace, and anchor numbers measured in the truth world.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import slo_oracle as so  # noqa: E402
from cards import EngineCard, Task, gen_trace, model_card, platform_card  # noqa: E402
from truth_sim import TruthWorld, draw_params  # noqa: E402

MODELS = ["llama-3.2-1b", "llama-3.1-8b", "qwen3-30b-a3b"]
FAMILIES = ["react", "fanout", "rag"]
# high-load arrival rate (tasks/s), chosen from solo task time so several tasks overlap
HIGH_RATE = {"llama-3.2-1b": 0.5, "llama-3.1-8b": 0.08, "qwen3-30b-a3b": 0.2}
XEON_ENG = EngineCard(max_batch=32, chunk_tokens=2048, cache_tokens=400_000)
LAPTOP_ENG = EngineCard(max_batch=4, chunk_tokens=512, cache_tokens=32_768)


def anchors_from(world: TruthWorld):
    return {k: world.measure(so.ANCHOR_SHAPES[k], so.ANCHOR_METRIC[k]) for k in so.ANCHOR_SHAPES}


def sequential(tasks):
    return [Task(t.id, 1e6 * i, t.steps) for i, t in enumerate(tasks)]


def rel(pred, true):
    return abs(pred - true) / true


def cell(model_name, family, load, seed, stress=1.0, truth_eng=None, oracle_eng=None):
    te, oe = truth_eng or XEON_ENG, oracle_eng or XEON_ENG
    m = model_card(model_name)
    xeon, lap = platform_card("c8i-48xlarge"), platform_card("i9-13900t")
    rng = random.Random(zlib.crc32(repr((model_name, family, load, seed, stress)).encode()))
    truth = TruthWorld(m, xeon, te, draw_params(rng, "xeon", stress), seed=seed)
    laptop = TruthWorld(m, lap, LAPTOP_ENG, draw_params(rng, "laptop"), seed=seed + 7)

    n, rate = (24, HIGH_RATE[model_name]) if load == "high" else (8, None)
    tasks = gen_trace(family, n, rate, seed)

    # what a user would measure: anchors on Xeon, anchors + a dev-loop recording on the laptop
    anc = anchors_from(truth)
    lap_anc = anchors_from(laptop)
    lap_run = laptop.run(sequential(tasks))
    rec_hits = {k: r["cached"] for k, r in lap_run["calls"].items() if "cached" in r}
    timeline = {k: (r["start"], r["end"]) for k, r in lap_run["calls"].items()}

    true = truth.run(tasks)
    tl = list(true["task"].values())
    t50, t95 = so.pct(tl, .5), so.pct(tl, .95)

    cal, band = so.fit(m, xeon, oe, anc)
    roof = so.Roofline(m, xeon, cal)
    out = {"model": model_name, "family": family, "load": load, "seed": seed, "stress": stress,
           "truth_p50": t50, "truth_p95": t95, "band": band, "calib": vars(cal)}

    def score(name, res):
        lat = list(res["task"].values())
        out[name] = {"p50": so.pct(lat, .5), "p95": so.pct(lat, .95)}
        out[name]["e50"] = rel(out[name]["p50"], t50)
        out[name]["e95"] = rel(out[name]["p95"], t95)
        return res

    full = score("oracle", so.replay(tasks, so.Engine(roof, oe)))
    # per-call TPOT error (calls with >= 8 output tokens)
    errs = []
    for k, r in true["calls"].items():
        if "t_first" in r and r["out"] >= 8 and k in full["calls"]:
            errs.append(rel(so.tpot(full["calls"][k]), (r["t_end"] - r["t_first"]) / (r["out"] - 1)))
    out["tpot_mape"] = statistics.fmean(errs)

    # fair baseline: same anchors, per-phase ratio scaling, laptop-recorded cache hits, critical path
    # TTFT scales with uncached tokens; TPOT is linear in context through both decode anchors.
    pf_in = so.ANCHOR_SHAPES["prefill"]["in"]
    c_s = so.ANCHOR_SHAPES["decode"]["in"] + so.ANCHOR_SHAPES["decode"]["out"] / 2
    c_l = so.ANCHOR_SHAPES["decode_long"]["in"] + so.ANCHOR_SHAPES["decode_long"]["out"] / 2
    slope = (anc["decode_long"] - anc["decode"]) / (c_l - c_s)

    def ratio_price(s, hit):
        ttft = max(anc["decode"], anc["prefill"] * (s.in_tokens - hit) / pf_in)
        tp = anc["decode"] + slope * (s.in_tokens + s.out / 2 - c_s)
        return ttft, ttft + (s.out - 1) * tp
    score("baseline", so.replay(tasks, price=ratio_price, hits=rec_hits))

    # ablations
    score("abl_solo", so.replay(tasks, price=lambda s, h: roof.solo(s.in_tokens, h, s.out, oe.chunk_tokens),
                                cache_tokens=oe.cache_tokens))
    score("abl_reccache", so.replay(tasks, so.Engine(roof, oe, hits=rec_hits)))
    llm = [(k, s) for t in tasks for s in t.steps if s.kind == "llm" for k in [(t.id, s.id)]]
    lap_llm = sum(lap_run["calls"][k]["t_end"] - lap_run["calls"][k]["t_sub"] for k, _ in llm)
    ora_llm = sum(roof.solo(s.in_tokens, rec_hits[k], s.out, oe.chunk_tokens)[1] for k, s in llm)
    sc = ora_llm / lap_llm
    scaled = {k: (a * sc, b * sc) for k, (a, b) in timeline.items()}
    score("abl_timeline", so.replay(tasks, so.Engine(roof, oe), timeline=scaled))

    # transfer: calibrate on the laptop, predict the Xeon with no Xeon anchor
    lcal, _ = so.fit(m, lap, LAPTOP_ENG, lap_anc)
    score("transfer", so.replay(tasks, so.Engine(so.Roofline(m, xeon, lcal), oe)))
    out["covered"] = (out["oracle"]["p95"] * (1 - band) <= t95 <= out["oracle"]["p95"] * (1 + band))
    return out


def mape(rows, key, err="e95"):
    return 100 * statistics.fmean(r[key][err] for r in rows) if rows else float("nan")


def summarize(rows, stress_rows):
    hi = [r for r in rows if r["load"] == "high"]
    lo = [r for r in rows if r["load"] == "low"]
    s = {"cells": len(rows)}
    for name in ("oracle", "baseline", "abl_solo", "abl_reccache", "abl_timeline", "transfer"):
        s[name] = {"p50_all": mape(rows, name, "e50"), "p95_all": mape(rows, name),
                   "p95_high": mape(hi, name), "p95_low": mape(lo, name),
                   "p50_high": mape(hi, name, "e50"), "p50_low": mape(lo, name, "e50")}
    s["tpot_mape"] = 100 * statistics.fmean(r["tpot_mape"] for r in rows)
    s["coverage"] = 100 * statistics.fmean(1.0 if r["covered"] else 0.0 for r in rows)
    s["per_model_high_p95"] = {m: {k: mape([r for r in hi if r["model"] == m], k)
                                   for k in ("oracle", "baseline")} for m in MODELS}
    s["per_family_high_p95"] = {f: {k: mape([r for r in hi if r["family"] == f], k)
                                    for k in ("oracle", "baseline")} for f in FAMILIES}
    o, b = s["oracle"], s["baseline"]
    s["criteria"] = {
        "C1_p50<=25": o["p50_all"] <= 25, "C1_p95<=30": o["p95_all"] <= 30, "C1_tpot<=20": s["tpot_mape"] <= 20,
        "C2_high_ratio<=0.5": o["p95_high"] <= 0.5 * b["p95_high"],
        "C2_low_no_big_win": o["p95_low"] >= 0.85 * b["p95_low"] - 5,
        "C3_solo>=1.5x": s["abl_solo"]["p95_high"] >= 1.5 * o["p95_high"],
        "C3_reccache_worse": s["abl_reccache"]["p95_high"] > o["p95_high"],
        "C3_timeline_worse": s["abl_timeline"]["p95_high"] > o["p95_high"],
        "C4_transfer_p50<=25 (predicted FAIL)": s["transfer"]["p50_all"] <= 25,
        "C5_coverage>=80": s["coverage"] >= 80,
    }
    s["stress"] = {}
    for st in sorted({r["stress"] for r in stress_rows}):
        rr = [r for r in stress_rows if r["stress"] == st]
        s["stress"][str(st)] = {"oracle_p50": mape(rr, "oracle", "e50"), "oracle_p95": mape(rr, "oracle"),
                                "baseline_p95": mape(rr, "baseline"),
                                "tpot": 100 * statistics.fmean(r["tpot_mape"] for r in rr)}
    return s


# Probes beyond the pre-registered grid: take away what the grid hands the oracle.
PROBES = {
    # the oracle is told 32/2048, the server actually runs 16/512
    "sched_mismatch": {"truth_eng": EngineCard(16, 512, 400_000)},
    # prefix caching off on the Xeon (e.g. an engine without APC): recorded laptop hits are wrong
    "apc_off": {"truth_eng": EngineCard(32, 2048, 0), "oracle_eng": EngineCard(32, 2048, 0)},
    # small Xeon prefix cache under many concurrent sessions
    "small_cache": {"truth_eng": EngineCard(32, 2048, 6_000), "oracle_eng": EngineCard(32, 2048, 6_000)},
}


def summarize_probe(rr):
    return {k: mape(rr, k) for k in ("oracle", "baseline", "abl_solo", "abl_reccache", "abl_timeline")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--quick", action="store_true", help="1 model, 1 family, no stress sweep")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "sim_results.json"))
    a = ap.parse_args()
    models, fams = (MODELS[1:2], FAMILIES[:1]) if a.quick else (MODELS, FAMILIES)
    t0, rows = time.time(), []
    for mn in models:
        for fam in fams:
            for load in ("low", "high"):
                for sd in range(a.seeds):
                    rows.append(cell(mn, fam, load, sd))
        print(f"[{time.time() - t0:6.1f}s] {mn} done", file=sys.stderr)
    stress_rows = []
    if not a.quick:
        for st in (1.0, 2.0, 4.0, 8.0):
            for fam in ("react", "fanout"):
                for sd in range(4):
                    stress_rows.append(cell("llama-3.1-8b", fam, "high", 100 + sd, stress=st))
        print(f"[{time.time() - t0:6.1f}s] stress done", file=sys.stderr)
    probe_rows = {}
    for name, kw in PROBES.items():
        probe_rows[name] = [cell("llama-3.1-8b", fam, "high", 200 + sd, **kw)
                            for fam in ("react", "fanout") for sd in range(2 if a.quick else 4)]
    s = summarize(rows, stress_rows) if stress_rows else summarize(rows, rows)
    s["probes_high_p95"] = {k: summarize_probe(v) for k, v in probe_rows.items()}
    with open(a.out, "w") as f:
        json.dump({"summary": s, "rows": rows, "stress_rows": stress_rows, "probe_rows": probe_rows},
                  f, indent=1, default=str)
    print(json.dumps({k: v for k, v in s.items()}, indent=1))


if __name__ == "__main__":
    main()
