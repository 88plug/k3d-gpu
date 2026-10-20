#!/usr/bin/env python3
"""h56.py score DIR: the H56 scorer (EXPERIMENTS.md "H56 pre-registration: parallel slice
holds on crescent"). DIR holds one directory per condition-hold, written by h56.sh:
hold<N> (N = 1..8 in the order A W W A A W W A) or hold<N>-r<k> (a VOID replacement),
each with condition.txt, pl1.txt, s0/ (slice 0's live.sh OUT plus hold/, hold.sh's
records) and, in W, s1/ (the sibling). Prints JSON with the verdict.

Pre-registered rule (not to be changed after a run):
  pairs: A_k with W_k (k = 1..4, k-th A and k-th W condition-hold), by task id over
         slice-0 cycle-0 rows that are not censored.
  time: geometric-mean W/A ratio of duration_s; success: passed and duration_s <= 120,
         the mean paired W - A difference. 90% CIs from 10,000 bootstrap resamples over
         task ids (seed 56), i.e. TOST at alpha 0.05.
  power: package W = sum energy_j / sum seconds of slice 0's blocks.jsonl, mean over W
         holds, against PL1 (pl1.txt, uW).
  VOID (replaced; more than 2 makes the row INCONCLUSIVE): rc != 0 or NOT DELIVERABLE,
         cpuset.txt not the slice, slice 0's block window not inside the sibling's (W),
         fewer slot-tagged load containers than the level runs, the gate flagged the hold.
  SUPPORTED: time CI in [0.909, 1.10], success CI in [-0.10, 0.10], >= 60 time pairs,
         W package < 0.95 PL1 and PL1 read. REFUTED: the time CI wholly outside
         [0.909, 1.10] or the success CI wholly outside [-0.10, 0.10]. Else INCONCLUSIVE."""
import argparse
import json
import math
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "load"))
from load import parse_cpulist  # noqa: E402

ORDER = "AWWAAWWA"
TIME_BAND = (0.909, 1.10)
SUCC_BAND = (-0.10, 0.10)
MIN_PAIRS = 60
POWER_FRAC = 0.95
BUDGET_TASK_S = 120
RESAMPLES = 10000
SEED = 56
MAX_VOID = 2
LOAD_CONTAINERS = 3            # bench/load/load.py components("typical")
NAME = re.compile(r"^hold([1-8])(?:-r([0-9]+))?$")


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _jsonl(path):
    try:
        with open(path) as f:
            return [json.loads(x) for x in f if x.strip()]
    except OSError:
        return []


def cpuset_problems(path):
    """cpuset.txt lines '<role> <name> <cpus|count>': node, node_effective, litellm, rapl
    and pod_allowed must equal expect_node; load lines expect_load, and at least
    LOAD_CONTAINERS of them, and load_end must count that many still running."""
    text = _read(path)
    if text is None:
        return ["cpuset.txt missing"]
    exp, seen, out = {}, {}, []
    for line in text.splitlines():
        f = line.split()
        if len(f) == 2 and f[0].startswith("expect_"):
            exp[f[0]] = parse_cpulist(f[1])
        elif len(f) == 3:
            seen.setdefault(f[0], []).append((f[1], f[2]))
    node, ld = exp.get("expect_node"), exp.get("expect_load")
    if not node or not ld:
        return ["cpuset.txt has no expect_node / expect_load"]
    for role in ("node", "node_effective", "litellm", "rapl", "pod_allowed"):
        rows = seen.get(role, [])
        if not rows:
            out.append(f"no {role} line")
        for name, v in rows:
            if parse_cpulist(v) != node:
                out.append(f"{role} {name} on {v}, not the slice")
    loads = seen.get("load", [])
    if len(loads) < LOAD_CONTAINERS:
        out.append(f"{len(loads)} of {LOAD_CONTAINERS} load containers")
    for name, v in loads:
        if parse_cpulist(v) != ld:
            out.append(f"load {name} on {v}, not the slice's load CPUs")
    end = [int(v) for _, v in seen.get("load_end", []) if v.isdigit()]
    if not end or end[-1] < LOAD_CONTAINERS:
        out.append("slot load missing at block end")
    return out


def rows_of(d):
    return [r for r in _jsonl(os.path.join(d, "run", "results.jsonl"))
            if not r.get("censored") and (r.get("cycle") or 0) == 0]


def window(d):
    rs = _jsonl(os.path.join(d, "run", "results.jsonl"))
    if not rs:
        return None
    return min(r["t_start"] for r in rs), max(r["t_end"] for r in rs)


def package_w(d):
    bl = _jsonl(os.path.join(d, "run", "blocks.jsonl"))
    if not bl or any((b.get("energy") or {}).get("energy_j") is None for b in bl):
        return None
    secs = sum(b["seconds"] for b in bl)
    return sum(b["energy"]["energy_j"] for b in bl) / secs if secs > 0 else None


def slice_void(d):
    out = []
    rc = _read(os.path.join(d, "hold", "rc"))
    if rc != "0":
        out.append(f"rc {rc}")
    if "NOT DELIVERABLE" in (_read(os.path.join(d, "live.log")) or ""):
        out.append("NOT DELIVERABLE")
    if os.path.exists(os.path.join(d, "hold", "flagged")):
        out.append("gate flagged")
    out += cpuset_problems(os.path.join(d, "cpuset.txt"))
    return out


def load_hold(path):
    cond = dict(kv.split("=", 1) for kv in (_read(os.path.join(path, "condition.txt")) or "").split() if "=" in kv)
    h = {"name": os.path.basename(path), "cond": cond.get("cond"), "void": []}
    s0 = os.path.join(path, "s0")
    h["void"] += [f"s0 {x}" for x in slice_void(s0)]
    if h["cond"] == "W":
        s1 = os.path.join(path, "s1")
        h["void"] += [f"s1 {x}" for x in slice_void(s1)]
        w0, w1 = window(s0), window(s1)
        if w0 is None or w1 is None or not (w1[0] <= w0[0] and w0[1] <= w1[1]):
            h["void"].append("slice-0 block window not inside the sibling's")
    elif h["cond"] != "A":
        h["void"].append(f"condition {h['cond']}")
    h["rows"] = rows_of(s0)
    h["pkg_w"] = package_w(s0)
    pl1 = _read(os.path.join(path, "pl1.txt"))
    h["pl1_w"] = int(pl1) / 1e6 if pl1 and pl1.isdigit() and int(pl1) > 0 else None
    return h


def _stats(pairs):
    lr = [math.log(w["duration_s"] / a["duration_s"]) for a, w in pairs]

    def ok(r):
        return int(bool(r["passed"]) and r["duration_s"] <= BUDGET_TASK_S)
    sd = [ok(w) - ok(a) for a, w in pairs]
    return math.exp(sum(lr) / len(lr)), sum(sd) / len(sd)


def _ci(xs):
    xs = sorted(xs)
    return xs[int(0.05 * (len(xs) - 1))], xs[int(math.ceil(0.95 * (len(xs) - 1)))]


def bootstrap(by_task, resamples=RESAMPLES, seed=SEED):
    rng = random.Random(seed)
    ids = sorted(by_task)
    tr, sr = [], []
    for _ in range(resamples):
        ps = [p for t in (rng.choice(ids) for _ in ids) for p in by_task[t]]
        t, s = _stats(ps)
        tr.append(t)
        sr.append(s)
    return _ci(tr), _ci(sr)


def score(d):
    attempts = {}
    for name in sorted(os.listdir(d)):
        m = NAME.match(name)
        if m and os.path.isdir(os.path.join(d, name)):
            attempts.setdefault(int(m.group(1)), []).append((int(m.group(2) or 0), name))
    void, used, chosen = {}, {}, {}
    for n in sorted(attempts):
        for _, name in sorted(attempts[n]):
            h = load_hold(os.path.join(d, name))
            if h["cond"] != ORDER[n - 1]:
                h["void"].append(f"condition {h['cond']} where the order has {ORDER[n - 1]}")
            if h["void"]:
                void[name] = h["void"]
                continue
            used[str(n)], chosen[n] = name, h
            break
    a_holds = [chosen[n] for n in range(1, 9) if ORDER[n - 1] == "A" and n in chosen]
    w_holds = [chosen[n] for n in range(1, 9) if ORDER[n - 1] == "W" and n in chosen]
    by_task = {}
    for a, w in zip(a_holds, w_holds):
        ra = {}
        for r in a["rows"]:
            ra.setdefault(r["task"], r)
        rw = {}
        for r in w["rows"]:
            rw.setdefault(r["task"], r)
        for t in sorted(set(ra) & set(rw)):
            by_task.setdefault(t, []).append((ra[t], rw[t]))
    npairs = sum(len(v) for v in by_task.values())
    pw = [h["pkg_w"] for h in w_holds]
    pkg = sum(pw) / len(pw) if pw and None not in pw else None
    pl1s = {h["pl1_w"] for h in w_holds}
    pl1 = pl1s.pop() if len(pl1s) == 1 else None
    out = {"pairs": npairs, "tasks": len(by_task), "used": used, "void": void,
           "w_pkg_w": None if pkg is None else round(pkg, 2), "pl1_w": pl1,
           "pkg_w_over_pl1": None if pkg is None or pl1 is None else round(pkg / pl1, 4),
           "a_pkg_w": [None if h["pkg_w"] is None else round(h["pkg_w"], 2) for h in a_holds]}
    if npairs:
        t, s = _stats([p for v in by_task.values() for p in v])
        tci, sci = bootstrap(by_task)
        out.update(time_ratio=round(t, 4), time_ci=[round(x, 4) for x in tci],
                   success_diff=round(s, 4), success_ci=[round(x, 4) for x in sci])
    reasons = []
    if len(void) > MAX_VOID:
        reasons.append(f"{len(void)} VOID condition-holds > {MAX_VOID}")
    if not npairs:
        reasons.append("no matched pairs")
        out.update(verdict="INCONCLUSIVE", reason="; ".join(reasons))
        return out
    tci, sci = out["time_ci"], out["success_ci"]
    outside_t = tci[1] < TIME_BAND[0] or tci[0] > TIME_BAND[1]
    outside_s = sci[1] < SUCC_BAND[0] or sci[0] > SUCC_BAND[1]
    if not reasons and (outside_t or outside_s):
        out.update(verdict="REFUTED", reason="time CI outside the band" if outside_t else "success CI outside the band")
        return out
    if not (TIME_BAND[0] <= tci[0] and tci[1] <= TIME_BAND[1]):
        reasons.append("time CI not inside the band")
    if not (SUCC_BAND[0] <= sci[0] and sci[1] <= SUCC_BAND[1]):
        reasons.append("success CI not inside the band")
    if npairs < MIN_PAIRS:
        reasons.append(f"{npairs} time pairs < {MIN_PAIRS}")
    if pl1 is None:
        reasons.append("PL1 not read")
    if pkg is None:
        reasons.append("W package power not measured")
    elif pl1 is not None and pkg >= POWER_FRAC * pl1:
        reasons.append(f"W package power {pkg:.1f} W >= {POWER_FRAC} PL1")
    out.update(verdict="INCONCLUSIVE" if reasons else "SUPPORTED", reason="; ".join(reasons))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("score")
    p.add_argument("dir")
    o = ap.parse_args(argv)
    print(json.dumps(score(o.dir), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
