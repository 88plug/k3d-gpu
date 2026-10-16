#!/usr/bin/env python3
"""Where does splitting beat tuned all-on-P-cores? Sweeps agent/sim.py over the
pre-registered regimes (agent/PREREGISTRATION.md, S1/S2) and over the priors
that decide the verdict, and prints best-split / BASE throughput per cell.

Usage: regimes.py [--params FILE] [--turns N]
"""
import argparse
import copy

import sim

SPLITS = ("CPU_SPLIT", "SPLIT_P", "SPLIT_G")


def cell(params, agents, turns, **overrides):
    p = copy.deepcopy(params)
    for k, v in overrides.items():
        sim.set_param(p, k.replace("__", "."), str(v))
    rows = sim.compare(p, agents, turns)
    best = max(SPLITS, key=lambda t: rows[t]["gain"])
    return rows, best


def fmt(rows, best):
    r = rows[best]
    return f"{best:<9} x{r['gain']:.2f} p95 x{r['p95_vs_base']:.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--params")
    ap.add_argument("--turns", type=int, default=200)
    a = ap.parse_args()
    base = sim.load_params(a.params)
    t = a.turns

    print("== pre-registered cells (best split vs tuned BASE; gain = throughput ratio)")
    cells = [
        ("S1  N=4 PL1=117W", 4, {}),
        ("S2a N=1 PL1=117W", 1, {}),
        ("S2b N=4 PL1=35W", 4, {"package__pl1_w": 35}),
        ("S2c N=4 DRAM=30GB/s", 4, {"package__dram_gbps": 30}),
    ]
    for label, n, ov in cells:
        rows, best = cell(base, n, t, **ov)
        sp = rows["SPLIT_P"]
        print(f"{label:<22} best {fmt(rows, best)}   SPLIT_P x{sp['gain']:.2f}")

    print("\n== concurrency x PL1 (best split gain)")
    pls = (35, 65, 117)
    print("N  " + "".join(f"{p:>8}W" for p in pls))
    for n in (1, 2, 4, 8):
        gains = [cell(base, n, t, package__pl1_w=p)[0] for p in pls]
        print(f"{n:<2} " + "".join(f"{max(g[s]['gain'] for s in SPLITS):>9.2f}" for g in gains))

    print("\n== bandwidth-bound decode (7B-class 4.4 GB weights, 150 prefill / 600 decode tokens), N=4")
    for bw in (20, 30, 55, 89.6):
        rows, best = cell(base, 4, t, package__dram_gbps=bw, workload__prefill_tokens=150,
                          workload__decode_tokens=600, stages__llm_decode__shared_bytes=4.4)
        print(f"DRAM {bw:>5} GB/s  {fmt(rows, best)}")

    print("\n== sensitivity of SPLIT_P at N=4 to the priors H2/H3 must measure")
    sweeps = [
        ("e_core_perf", (0.35, 0.55, 0.75)),
        ("stages__rerank__compute__igpu", (0.12, 0.24, 0.5, 1.0)),
        ("spin_penalty", (0.0, 0.5, 1.0)),
        ("engines__igpu__watts_per_unit", (12, 25)),
    ]
    for key, vals in sweeps:
        out = []
        for v in vals:
            rows, _ = cell(base, 4, t, **{key: v})
            out.append(f"{v}: x{rows['SPLIT_P']['gain']:.2f}")
        print(f"{key.replace('__', '.'):<34} " + "  ".join(out))
    rows, _ = cell(base, 4, t, package__pl1_w=35, engines__igpu__watts_per_unit=25)
    print(f"{'igpu 25 W at PL1=35W':<34} SPLIT_P x{rows['SPLIT_P']['gain']:.2f}  SPLIT_G x{rows['SPLIT_G']['gain']:.2f}")


if __name__ == "__main__":
    main()
