#!/usr/bin/env python3
"""score.py CSV MATCH_JSON [--level typical] [--mib 1024] [--n 8]: the H28 outcome table
(EXPERIMENTS.md "H28 pre-registration", amendments A1 and A2) applied to one level of a
`probe.sh run` CSV and its `load.py matched` JSON. Prints one JSON object.

Per scored round (kind=scored, the level asked for): the median of the round's pods for
ready_ms, PSS and Shared_Clean, and the round row's model_resident_kB (A1 primary memory
metric). A round is void when its cold check failed (VOID-cold, VOID-timeout) or its
swap-out exceeds 256 MiB.

Rows, in the order the table reads them:
  >= 2 void rounds in an arm, or an arm's round-median ready spread > 30% of its median
                                                    -> INCONCLUSIVE (rounds)
  C resident < 0.8 x (N+1) x size or C PSS < 0.9 x size  -> INCONCLUSIVE (probe blind)
  T PSS >= 0.5 x size or T resident >= 0.5 x N x size     -> REFUTED (no page-cache sharing)
  T memory holds (PSS <= 160 MiB, Shared_Clean >= 0.9 x size, resident <= 1.1 x size) and
  T/C round-median ready <= 0.5 with max(T) < min(C)       -> SUPPORTED
  T memory holds, time misses                              -> REFUTED (time part)
  load not MATCHED                                         -> INCONCLUSIVE (load), numbers kept
Stdlib only.
"""
import argparse
import csv
import json
import statistics as st
import sys


def rounds(path, level):
    pods, rows = {}, {}
    with open(path) as f:
        for r in csv.reader(line for line in f if not line.startswith("#")):
            if not r or r[0] not in ("pod", "round"):
                continue
            if r[-1] != level or r[-2] != "scored":
                continue
            arm, rnd = r[1], int(r[2])
            if r[0] == "pod":
                pods.setdefault((arm, rnd), []).append(
                    {"ready_ms": float(r[4]), "pss_kB": float(r[6]), "shared_clean_kB": float(r[7])})
            else:
                void = r[4].startswith("VOID")
                rows[(arm, rnd)] = {"void": void, "resident_kB": None if void else float(r[4]),
                                    "swapout_kB": None if void else float(r[7])}
    out = []
    for (arm, rnd), row in sorted(rows.items()):
        ps = pods.get((arm, rnd), [])
        # A failed mincore read is written as -1: the round has no memory reading.
        void = (row["void"] or not ps or (row["swapout_kB"] or 0) > 256 * 1024 or row["resident_kB"] < 0
                or any(p["ready_ms"] < 0 for p in ps))
        rec = {"arm": arm, "round": rnd, "void": void, "resident_kB": row["resident_kB"]}
        if ps:
            rec.update({k: st.median(p[k] for p in ps) for k in ("ready_ms", "pss_kB", "shared_clean_kB")})
        out.append(rec)
    return out


def snapshotter(path):
    """The snapshotter the probe read from the k3s containerd config (its meta row)."""
    with open(path) as f:
        for line in f:
            if line.startswith("meta,"):
                for kv in line.strip().split(",")[1:]:
                    if kv.startswith("snapshotter="):
                        return kv.split("=", 1)[1]
    return "unknown"


def verdict(recs, matched, mib=1024, n=8, snap="unknown"):
    size = mib * 1024.0                      # kB
    arm = {a: [r for r in recs if r["arm"] == a and not r["void"]] for a in ("T", "C")}
    void = {a: sum(1 for r in recs if r["arm"] == a and r["void"]) for a in ("T", "C")}
    res = {"void_rounds": void, "rounds": recs, "load": matched}
    if void["T"] >= 2 or void["C"] >= 2 or not arm["T"] or not arm["C"]:
        return dict(res, verdict="INCONCLUSIVE", row="rounds: >= 2 void rounds in an arm")
    med = {a: {k: st.median(r[k] for r in arm[a]) for k in ("ready_ms", "pss_kB", "shared_clean_kB", "resident_kB")}
           for a in arm}
    ready = {a: [r["ready_ms"] for r in arm[a]] for a in arm}
    spread = {a: (max(ready[a]) - min(ready[a])) / med[a]["ready_ms"] for a in arm}
    ratio = med["T"]["ready_ms"] / med["C"]["ready_ms"]
    res.update(median=med, ready_spread=spread, ready_ratio=round(ratio, 4),
               separated=max(ready["T"]) < min(ready["C"]))
    if any(s > 0.30 for s in spread.values()):
        out = dict(res, verdict="INCONCLUSIVE", row="rounds: round-median ready spread > 30% of median")
    elif med["C"]["resident_kB"] < 0.8 * (n + 1) * size or med["C"]["pss_kB"] < 0.9 * size:
        out = dict(res, verdict="INCONCLUSIVE", row="probe blind: arm C shows no N-fold duplication")
    elif med["T"]["pss_kB"] >= 0.5 * size or med["T"]["resident_kB"] >= 0.5 * n * size:
        # The table's two REFUTED rows differ only by snapshotter (overlayfs vs native/fuse).
        out = dict(res, verdict="REFUTED", row=f"no page-cache sharing (snapshotter {snap})")
    else:
        mem = (med["T"]["pss_kB"] <= 160 * 1024 and med["T"]["shared_clean_kB"] >= 0.9 * size
               and med["T"]["resident_kB"] <= 1.1 * size)
        if mem and ratio <= 0.5 and res["separated"]:
            out = dict(res, verdict="SUPPORTED", row="T meets memory and time; C shows duplication")
        elif mem:
            out = dict(res, verdict="REFUTED", row="time part: T/C ready > 0.5 or rounds overlap")
        else:
            out = dict(res, verdict="INCONCLUSIVE", row="T memory between the SUPPORTED and REFUTED bounds")
    lv = (matched or {}).get("verdict")
    if lv == "VOID":   # A2: a window with fewer load containers than the level runs; re-run
        out = dict(out, verdict_if_matched=out["verdict"], verdict="VOID", row="load VOID: re-run the level")
    elif lv != "MATCHED":
        out = dict(out, verdict_if_matched=out["verdict"], verdict="INCONCLUSIVE", row="load not MATCHED")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("match")
    ap.add_argument("--level", default="typical")
    ap.add_argument("--mib", type=int, default=1024)
    ap.add_argument("--n", type=int, default=8)
    a = ap.parse_args(argv)
    try:
        matched = json.load(open(a.match))
    except (OSError, ValueError):
        matched = None
    json.dump(verdict(rounds(a.csv, a.level), matched, a.mib, a.n, snapshotter(a.csv)), sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
