#!/usr/bin/env python3
"""Score an H28 run (EXPERIMENTS.md, H28 pre-registration + amendments A1, A2).

Usage: summarize.py RUN.csv [LOAD.jsonl]
RUN.csv is probe.sh's output; LOAD.jsonl is the level's load.py windows (A2).
Prints one JSON object: per-round metrics, per-arm medians and spreads, the
one-sided exact Mann-Whitney p of T < C on round-median time to ready, the
load match, the pre-registered checks, the verdict and the derived confidence.
Python 3 standard library only.
"""
import csv
import itertools
import json
import os
import statistics
import subprocess
import sys


def mw_p_less(a, b):
    """Exact one-sided Mann-Whitney p of 'a tends to be smaller than b'."""
    def u(x, y):
        return sum((xi < yi) + 0.5 * (xi == yi) for xi in x for yi in y)
    obs = u(a, b)
    pool = list(a) + list(b)
    n = len(a)
    hits = total = 0
    for idx in itertools.combinations(range(len(pool)), n):
        s = set(idx)
        x = [pool[i] for i in idx]
        y = [pool[i] for i in range(len(pool)) if i not in s]
        total += 1
        hits += u(x, y) >= obs
    return hits / total


def spread_rel(xs):
    m = statistics.median(xs)
    return (max(xs) - min(xs)) / m if m else float("inf")


def main(argv):
    meta, pods, rounds, notes = {}, [], [], []
    with open(argv[1]) as f:
        for row in csv.reader(f):
            if not row or row[0].startswith("#"):
                continue
            if row[0] == "meta":
                meta = dict(kv.split("=", 1) for kv in row[1:] if "=" in kv)
            elif row[0] == "pod":
                pods.append(row)
            elif row[0] == "round":
                rounds.append(row)
            else:
                notes.append(",".join(row))
    n, mib = int(meta.get("N", 8)), int(meta.get("MiB", 1024))
    size_kb = mib * 1024
    order = list(meta.get("order", "CTTCCT"))
    per = []
    for r in rounds:
        if r[-1] != "scored":
            continue
        arm, rnd = r[1], int(r[2])
        if not r[4].lstrip("-").isdigit():
            per.append({"arm": arm, "round": rnd, "void": r[4]})
            continue
        if int(r[7]) > 256 * 1024:
            per.append({"arm": arm, "round": rnd, "void": f"VOID-swapout {int(r[7]) // 1024} MiB"})
            continue
        ps = [p for p in pods if p[1] == arm and int(p[2]) == rnd and p[-1] == "scored"]
        ready = [int(p[4]) for p in ps if int(p[4]) >= 0]
        per.append({
            "arm": arm, "round": rnd, "void": None,
            "ready_ms": statistics.median(ready) if len(ready) == n else None,
            "pss_mib": statistics.median(int(p[6]) for p in ps) / 1024,
            "shared_clean_mib": statistics.median(int(p[7]) for p in ps) / 1024,
            "resident_x_size": int(r[4]) / size_kb if int(r[4]) >= 0 else None,
            "dcached_x_size": int(r[5]) / size_kb,
            "podcg_file_mib": [round(int(p[10]) / 2**20, 1) if p[10].isdigit() else None for p in ps],
            "load1": float(r[8]), "throttle_count": r[10], "pkg_w": r[11],
        })
    for r in per:
        if not r["void"] and (r["ready_ms"] is None or r["resident_x_size"] is None):
            r["void"] = "VOID-incomplete"
    ok = {a: [r for r in per if r["arm"] == a and not r["void"]] for a in "TC"}
    planned = {a: order.count(a) for a in "TC"}
    out = {"meta": meta, "notes": notes, "rounds": per, "arms": {}}
    for a in "TC":
        rs = ok[a]
        if not rs:
            continue
        t = [r["ready_ms"] for r in rs]
        res = [r["resident_x_size"] for r in rs]
        out["arms"][a] = {
            "n_rounds": len(rs), "planned": planned[a],
            "ready_ms_median": statistics.median(t), "ready_ms_min_max": [min(t), max(t)],
            "ready_spread_rel": round(spread_rel(t), 3),
            "resident_x_size_median": round(statistics.median(res), 3),
            "resident_x_size_min_max": [round(min(res), 3), round(max(res), 3)],
            "resident_spread_rel": round(spread_rel(res), 3),
            "pss_mib_median": round(statistics.median(r["pss_mib"] for r in rs), 1),
            "shared_clean_mib_median": round(statistics.median(r["shared_clean_mib"] for r in rs), 1),
            "dcached_x_size_median": round(statistics.median(r["dcached_x_size"] for r in rs), 3),
        }

    gates = []
    for a in "TC":
        if planned[a] - len(ok[a]) >= 2:
            gates.append(f"arm {a}: {planned[a] - len(ok[a])} of {planned[a]} rounds void or missing")
    load = None
    if len(argv) > 2:
        here = os.path.dirname(os.path.abspath(__file__))
        p = subprocess.run([sys.executable, os.path.join(here, "..", "load", "load.py"), "matched", argv[2]],
                           capture_output=True, text=True)
        load = json.loads(p.stdout)
        if not load["matched"]:
            gates.append(load["verdict"])
    if any(n.startswith("ABORT,background-lost") for n in notes):
        gates.append("background lost")
    out["load"] = load

    verdict, checks, conf = "INCONCLUSIVE", {}, None
    if ok["T"] and ok["C"]:
        T, C = out["arms"]["T"], out["arms"]["C"]
        tm = [r["ready_ms"] for r in ok["T"]]
        cm = [r["ready_ms"] for r in ok["C"]]
        ratio = T["ready_ms_median"] / C["ready_ms_median"]
        p = mw_p_less(tm, cm)
        checks = {
            "T_pss_le_160MiB": T["pss_mib_median"] <= 160,
            "T_shared_clean_ge_0.9size": T["shared_clean_mib_median"] >= 0.9 * mib,
            "T_resident_le_1.1size": T["resident_x_size_median"] <= 1.1,
            "C_resident_ge_0.8(N+1)size": C["resident_x_size_median"] >= 0.8 * (n + 1),
            "C_pss_ge_0.9size": C["pss_mib_median"] >= 0.9 * mib,
            "time_ratio": round(ratio, 3),
            "time_ratio_le_0.5": ratio <= 0.5,
            "maxT_lt_minC": max(tm) < min(cm),
            "mw_p_one_sided": round(p, 4),
        }
        for a in "TC":
            for k in ("ready_spread_rel", "resident_spread_rel"):
                if out["arms"][a][k] > 0.30:
                    gates.append(f"arm {a}: {k} {out['arms'][a][k]} > 0.30")
        mem_t = (checks["T_pss_le_160MiB"] and checks["T_resident_le_1.1size"]
                 and checks["T_shared_clean_ge_0.9size"])
        blind = not (checks["C_resident_ge_0.8(N+1)size"] and checks["C_pss_ge_0.9size"])
        null_mem = T["pss_mib_median"] >= 0.5 * mib or T["resident_x_size_median"] >= 0.5 * n
        if blind:
            gates.append("probe blind: C shows no N-fold duplication")
        if gates:
            verdict = "INCONCLUSIVE"
        elif null_mem or ratio >= 0.8:
            verdict = "REFUTED"
        elif mem_t and checks["time_ratio_le_0.5"] and checks["maxT_lt_minC"]:
            verdict = "SUPPORTED"
        elif mem_t:
            verdict = "REFUTED (time part)"
        else:
            verdict = "INCONCLUSIVE"
            gates.append("memory between prediction and null")
        cmed = C["ready_ms_median"]
        meet = [r["pss_mib"] <= 160 and r["resident_x_size"] <= 1.1 and r["ready_ms"] <= 0.5 * cmed
                for r in ok["T"]]
        meet += [r["resident_x_size"] >= 0.8 * (n + 1) and r["pss_mib"] >= 0.9 * mib for r in ok["C"]]
        frac = sum(meet) / len(meet)
        conf = round(frac * (1 - p), 3)
        checks["fraction_rounds_meeting_prediction"] = round(frac, 3)
    out.update(checks=checks, gates=gates, verdict=verdict, confidence=conf)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
