"""Break the S5 guard grid (s5_grid.tsv, written by sim.py) down by parameter.

Usage: python3 grid_breakdown.py [s5_grid.tsv]
"""

import collections
import csv
import statistics
import sys


def verdict(r, v="guard_v", s="guard_slow"):
    """None for uncontended points; else S3 pass (holds 90% and >= 25% less
    sandbox slowdown than the oracle static cpu.max)."""
    if float(r["naive"]) >= 0.9:
        return None
    gv, gs = float(r[v]), float(r[s])
    if float(r["static_q"]) == 0:
        return gv >= 0.9
    return gv >= 0.9 and 1 - gs / float(r["static_slow"]) >= 0.25


def main(path):
    rows = list(csv.DictReader(open(path), delimiter="\t"))
    cont = [r for r in rows if float(r["naive"]) < 0.9]
    for key in ("alpha", "gamma", "reach", "cap", "w"):
        d = collections.defaultdict(lambda: [0, 0, 0])
        for r in cont:
            d[r[key]][0] += verdict(r)
            d[r[key]][1] += 1
            d[r[key]][2] += float(r["guard_v"]) >= 0.9
        print(f"{key:6s} " + "  ".join(f"{k}: pass {a}/{n} holds90 {h}" for k, (a, n, h) in sorted(d.items())))
    print(f"contended points {len(cont)}; median naive victim {statistics.median(float(r['naive']) for r in cont):.3f}, "
          f"median guard victim {statistics.median(float(r['guard_v']) for r in cont):.3f}")
    adv = [1 - float(r["guard_slow"]) / float(r["static_slow"]) for r in cont
           if float(r["static_q"]) > 0 and float(r["guard_v"]) >= 0.9]
    if adv:
        print(f"where the guard holds 90% (n={len(adv)}): advantage over oracle static "
              f"median {statistics.median(adv):.2f}, min {min(adv):.2f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "s5_grid.tsv")
