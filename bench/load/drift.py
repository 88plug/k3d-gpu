#!/usr/bin/env python3
"""bench/load/drift.py: how far the host's load drifts with no arm effect at all.

For each recorded load.py window file (one hold, one series), the windows are given
null arm labels in A B B A order: the labels carry no treatment, so any spread
`load.matched` then reports is host drift that an interleaved design inside one hold
would see. Prints one row per file and, per metric, how many files fail the
tolerance under null labels. --pairs instead matches adjacent windows two at a time
(windows 1-2, 3-4, ...), the per-pair gate of a design that samples one window per
task, and counts the null pairs that fail.

Usage: bench/load/drift.py [--min-windows 8] [--pairs] FILE.jsonl [...]
Python 3 standard library only.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import load  # noqa: E402

PATTERN = "ABBA"


def null_labels(windows):
    """Windows sorted by time, tagged A B B A A B B A ..., one level."""
    ws = sorted(windows, key=lambda w: w["ts"])
    return [dict(w, tag=PATTERN[i % len(PATTERN)], level="null") for i, w in enumerate(ws)]


def spreads(windows):
    """{metric: (spread, limit, status)} of load.matched under null labels."""
    r = load.matched(null_labels(windows))
    return {c["metric"]: (c.get("spread"), c.get("limit"), c["status"]) for c in r["checks"]}


def null_pairs(windows):
    """Adjacent windows two at a time, tagged A and B: [[w1, w2], [w3, w4], ...]."""
    ws = sorted(windows, key=lambda w: w["ts"])
    return [[dict(ws[i], tag="A", level="null"), dict(ws[i + 1], tag="B", level="null")]
            for i in range(0, len(ws) - 1, 2)]


def pair_fails(windows):
    """(pairs, {metric: pairs failing it}, pairs failing any metric) under null labels."""
    fails = {m: 0 for m in load.TOLERANCE}
    anyfail = 0
    pairs = null_pairs(windows)
    for p in pairs:
        bad = [c["metric"] for c in load.matched(p)["checks"] if c["status"] == "fail"]
        for m in bad:
            fails[m] += 1
        anyfail += bool(bad)
    return len(pairs), fails, anyfail


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--min-windows", type=int, default=8)
    ap.add_argument("--pairs", action="store_true")
    ap.add_argument("files", nargs="+")
    o = ap.parse_args(argv)
    if o.pairs:
        tot, fails, anyfail = 0, {m: 0 for m in load.TOLERANCE}, 0
        for path in o.files:
            with open(path) as f:
                ws = [json.loads(line) for line in f if line.strip()]
            n, fl, a = pair_fails(ws)
            tot, anyfail = tot + n, anyfail + a
            for m in fl:
                fails[m] += fl[m]
            print(f"{path} pairs={n} fail_any={a} " + " ".join(f"{m}={v}" for m, v in fl.items() if v))
        print(json.dumps({"pairs": tot, "fail_any": anyfail, "null_pair_fails": fails}))
        return 0
    fails = {m: 0 for m in load.TOLERANCE}
    n = 0
    for path in o.files:
        with open(path) as f:
            ws = [json.loads(line) for line in f if line.strip()]
        if len(ws) < o.min_windows:
            continue
        n += 1
        sp = spreads(ws)
        cells = " ".join(f"{m}={v[0]}" for m, v in sp.items() if v[0] is not None)
        bad = [m for m, v in sp.items() if v[2] == "fail"]
        for m in bad:
            fails[m] += 1
        print(f"{path} n={len(ws)} {cells} fail={','.join(bad) or '-'}")
    print(json.dumps({"files": n, "null_label_fails": fails}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
