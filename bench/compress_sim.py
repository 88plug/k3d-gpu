#!/usr/bin/env python3
"""Falsification model for E-core tool-result compression (stdlib only, no live calls).

Question: does compressing tool results on the E-cores raise agents-at-SLO (A@SLO)
beyond what zero-cost head+tail truncation to the same budget already gives, and
does the E-core placement itself carry any of the gain?

Arms (every compressing arm hands the engine the same token count):
  none        uncompressed
  trunc       delta x ratio, zero cost (the tuned baseline)
  ecore       delta x ratio, compressor on E-cores inside the tool wait (absorbed:
              offered load unchanged while compress time < tool wait)
  ecore_add   same, but compress time ADDED to the tool wait. Ablation that shows
              how a longer tool step lowers offered load and inflates A@SLO with no
              engine gain; turns/s exposes it
  pcore       compressor on the serving engine (CPU tier only): its MACs are
              charged to the engine as extra prefill-equivalent tokens, in-turn
  policy      ecore, but only on log/grep results (file reads pass through, since
              edits need exact text): half the tool results compressed

Engines: cpu = ceiling.calibrate("cpu") (fitted to H2); rtx4000sff ASSUMED exactly
as bench/hetero_tier.py (claude/invent-hetero-agent-tier): datasheet bandwidth x
0.62, 15 TMAC/s, 3 ms step overhead. Not calibrated.

Compressor cost model: LLMLingua-2 small = mBERT-base encoder, ~86M non-embedding
params -> ~0.086 GMAC/token (+ attention, ignored at 512-token windows). E-core
int8 peak from ceiling.HOST (16 cores x 16 MAC/clk x 3.0 GHz = 0.77 TMAC/s),
efficiency 15% / 30% (assumed). Tokens = words x 1.3.

Usage: python3 bench/compress_sim.py [--quick] [--seeds 1,2,3]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ceiling as C  # noqa: E402

MODEL = "qwen3-4b"
TOOL_WAIT = 5.0
SLOS = (10.0, 20.0, 30.0)
BERT_MAC_PER_TOK = 86e6  # mBERT-base non-embedding params (assumed ~86M), 1 MAC each
ECORE_PEAK = C.vnni_peak("E")


def engines():
    cpu, _ = C.calibrate("cpu", "roofline")
    g4 = C.Engine("rtx4000sff", 280e9 * 0.62, 15e12, s0=1e-4, t0=3e-3, kv="u8")
    return {"cpu": (cpu, 256), "rtx4000sff": (g4, 2048)}


def scaled_trace(base, f, name):
    tr = dict(C.TRACES[base])
    lo, hi = tr["delta"]
    tr["delta"] = (max(1, round(lo * f)), max(1, round(hi * f)))
    C.TRACES[name] = tr
    return name


def mean_delta(trace):
    lo, hi = C.TRACES[trace]["delta"]
    return (lo + hi) / 2


def compress_s(tokens, eff, cores=1):
    """Seconds for one tool result on `cores` E-cores (one request is not split)."""
    return tokens * BERT_MAC_PER_TOK / (ECORE_PEAK / 16 * cores * eff)


def erlang_c_wait(lam, mu, c):
    """Mean queueing delay of M/M/c (inf if rho >= 1)."""
    rho = lam / (c * mu)
    if rho >= 1:
        return math.inf
    a = lam / mu
    s = sum(a**k / math.factorial(k) for k in range(c))
    top = a**c / math.factorial(c) / (1 - rho)
    pw = top / (s + top)
    return pw / (c * mu - lam)


def run(eng, budget, trace, n, horizon, seed, tool_wait=TOOL_WAIT):
    return C.simulate(eng, MODEL, trace, n, tool_wait, horizon=horizon, budget=budget,
                      seed=seed, warmup=min(120.0, horizon / 5))


def a_at_slo(eng, budget, trace, slo, horizon, seed, tool_wait=TOOL_WAIT, nmax=256):
    ok = lambda n: run(eng, budget, trace, n, horizon, seed, tool_wait)["p95"] <= slo  # noqa: E731
    if not ok(1):
        return 0
    lo, hi = 1, 2
    while hi <= nmax and ok(hi):
        lo, hi = hi, hi * 2
    if hi > nmax:
        return lo
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return lo


def arm_traces(base, ratio, eng_name):
    """Per arm: (trace name, extra tool wait in s, note)."""
    d = mean_delta(base)
    out = {
        "none": (base, 0.0),
        "trunc": (scaled_trace(base, ratio, f"{base}_r{ratio}"), 0.0),
        "ecore": (scaled_trace(base, ratio, f"{base}_r{ratio}"), 0.0),
        # One request spread over all 16 E-cores (the latency-optimal layout).
        "ecore_add": (scaled_trace(base, ratio, f"{base}_r{ratio}"), compress_s(d, 0.30, cores=16)),
        "policy": (scaled_trace(base, 0.5 + 0.5 * ratio, f"{base}_p{ratio}"), 0.0),
    }
    if eng_name == "cpu":
        # Compressor MACs on the engine, as prefill-equivalent tokens of the served model.
        f = ratio + BERT_MAC_PER_TOK / C.shapes(MODEL)["active_params"]
        out["pcore"] = (scaled_trace(base, f, f"{base}_pc{ratio}"), 0.0)
    return out


def ecore_load(turns_per_s, trace_base, frac, eff):
    """E-cluster utilization and M/M/16 queue delay for the compressor at a turn rate."""
    tok = mean_delta(trace_base) * frac
    if tok == 0 or turns_per_s == 0:
        return {"util": 0.0, "queue_s": 0.0, "service_s": 0.0}
    svc = compress_s(tok, eff)
    lam = turns_per_s
    util = lam * svc / 16
    return {"util": round(util, 3), "service_s": round(svc, 3),
            "queue_s": round(erlang_c_wait(lam, 1 / svc, 16), 3) if util < 1 else math.inf}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="horizon 300 s instead of 600 s")
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--ratio", type=float, default=0.5)
    a = ap.parse_args(argv)
    h = 300.0 if a.quick else 600.0
    seeds = [int(s) for s in a.seeds.split(",")]
    out = {"model": MODEL, "tool_wait_s": TOOL_WAIT, "slo_s": SLOS, "ratio": a.ratio,
           "horizon_s": h, "seeds": seeds, "engines_assumed": ["rtx4000sff"], "cells": {}}
    eng = engines()
    for name, (e, budget) in eng.items():
        for base in ("coding", "fanout"):
            for arm, (tr, extra) in arm_traces(base, a.ratio, name).items():
                row = {}
                for slo in SLOS:
                    ns = sorted(a_at_slo(e, budget, tr, slo, h, s, TOOL_WAIT + extra) for s in seeds)
                    n_med = ns[len(ns) // 2]
                    tps = 0.0
                    if n_med:
                        r = run(e, budget, tr, n_med, h, seeds[0], TOOL_WAIT + extra)
                        tps = r["turns"] / (h - min(120.0, h / 5))
                    row[f"{slo:g}s"] = {"A": ns, "turns_per_s": round(tps, 3)}
                    if arm in ("ecore", "policy") and n_med:
                        frac = 1.0 if arm == "ecore" else 0.5
                        row[f"{slo:g}s"]["ecore"] = {
                            f"eff{int(eff * 100)}": ecore_load(tps, base, frac, eff) for eff in (0.15, 0.30)}
                out["cells"][f"{name}/{base}/{arm}"] = row
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
