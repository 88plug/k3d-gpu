#!/usr/bin/env python3
"""Heterogeneous agent tier: agents-at-SLO per engine on this host, stdlib only.

Reuses bench/ceiling.py (CPU engine calibrated on H2). The two NVIDIA engines are
ASSUMED, not calibrated: bandwidth = datasheet bus width x 14 Gbps x 0.62 (the
CPU's measured efficiency), effective compute 15 / 9.6 TMAC/s (~40% of FP16 dense
tensor peak), 3 ms fixed step overhead. Every number printed is a prediction for
a live probe, not a measurement.

Usage: python3 bench/hetero_tier.py [--quick]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ceiling as C  # noqa: E402

MODEL = "qwen3-4b"
SLOS = (10.0, 20.0, 30.0)
TOOL_WAIT = 5.0


def engines():
    cpu, _ = C.calibrate("cpu", "roofline")
    g4 = C.Engine("rtx4000sff", 280e9 * 0.62, 15e12, s0=1e-4, t0=3e-3, kv="u8")
    g2 = C.Engine("rtx2000e", 224e9 * 0.62, 9.6e12, s0=1e-4, t0=3e-3, kv="u8")
    return {"cpu": (cpu, 256), "rtx4000sff": (g4, 2048), "rtx2000e": (g2, 2048)}


def kv_tokens(gb):
    per = C.kv_elems_stored_per_token(MODEL) * C.kv_bytes_per_elem("u8", 128)
    return int(gb * 1e9 / per)


def a_at_slo(eng, budget, trace, horizon, **kw):
    return [C.agents_at_slo(eng, MODEL, trace, TOOL_WAIT, s, nmax=256, horizon=horizon,
                            budget=budget, **kw) for s in SLOS]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="horizon 300 s instead of 600 s")
    a = ap.parse_args(argv)
    h = 300.0 if a.quick else 600.0
    C.TRACES["coding_half"] = dict(C.TRACES["coding"], delta=(260, 1040))
    C.TRACES["fanout_half"] = dict(C.TRACES["fanout"], delta=(75, 300))
    eng = engines()
    out = {"slo_s": SLOS, "tool_wait_s": TOOL_WAIT, "per_engine": {}}
    for name, (e, b) in eng.items():
        for tr in ("support", "fanout", "coding", "coding_half", "fanout_half"):
            out["per_engine"][f"{name}/{tr}"] = a_at_slo(e, b, tr, h)
    g4, b4 = eng["rtx4000sff"]
    out["kv_pool_rtx4000sff_coding"] = {f"{gb}GB": a_at_slo(g4, b4, "coding", h, cache_tokens=kv_tokens(gb))
                                       for gb in (6, 14, 30)}
    out["t0_sensitivity_rtx4000sff_support"] = {}
    for t0 in (3e-3, 10e-3, 30e-3):
        g4.t0 = t0
        out["t0_sensitivity_rtx4000sff_support"][f"{t0*1e3:g}ms"] = a_at_slo(g4, b4, "support", h)
    g4.t0 = 3e-3
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
