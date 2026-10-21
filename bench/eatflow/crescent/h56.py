#!/usr/bin/env python3
"""h56.py score DIR | gate DIR [--smoke]: the H56 scorer and stage gate (EXPERIMENTS.md "H56
pre-registration: parallel slice holds on crescent"). DIR holds stage 0's lockwait.json, the
wiring smoke smoke-W/ and one directory per condition-hold written by h56.sh: hold<N>
(N = 1..8 in the order A W W A A W W A) or hold<N>-r<k> (a VOID replacement), each with
condition.txt, pl1.txt, s0/ (slice 0's live.sh OUT, t0, and hold/, hold.sh's records) and,
in W, s1/ (the sibling). Prints JSON.

gate (read by h56.sh before it takes any lock; exit 1 refuses): lockwait.json must say
stage1. For a condition-hold (not --smoke), smoke-W's slice 0 must also show a setup time
(t0 to its first task start) <= 900 s and >= 3 completed tasks in its 120 s budget.

Pre-registered score rule (not to be changed after a run):
  positions: every one of the 8 needs a non-VOID hold, else INCONCLUSIVE (position N missing).
  hold pairs: (1,2), (4,3), (5,6), (8,7), the A and W of each adjacent ABBA half.
  tasks: slice-0 cycle-0 rows by task id, in both holds of a pair. Time pairs: neither row
         censored; geometric-mean W/A ratio of duration_s. Success pairs: every common task;
         ok = passed, not censored and duration_s <= 120; mean W - A. Completion: completed
         (uncensored) slice-0 tasks, sum over W holds / sum over A holds.
  CIs: 90% (TOST at alpha 0.05), 10,000 two-stage bootstrap resamples (seed 56): the 4 hold
         pairs with replacement, then each drawn pair's tasks with replacement.
  covariates: own-slice MHz (serving.jsonl own_mhz over slice 0's task window), W mean / A
         mean; package W = sum energy_j / sum seconds of slice 0's blocks.jsonl, mean over W
         holds, against PL1 (pl1.txt, uW).
  VOID (replaced; more than 2 makes the row INCONCLUSIVE): rc != 0 or NOT DELIVERABLE,
         cpuset.txt not the slice (pod nproc included), fewer slot-tagged load containers than
         the level runs, slice 0's task window not inside the sibling's (W), gate flagged.
  REFUTED: time CI wholly outside [1/1.05, 1.05], success CI wholly outside [-1/24, +1/24],
         or the completion CI wholly below 1/1.05.
  SUPPORTED: time CI inside [1/1.05, 1.05], success CI inside [-1/24, +1/24], completion
         >= 1/1.05, >= 60 time pairs, own-slice MHz W/A >= 0.95, W package < 0.95 PL1, PL1 read.
  Else INCONCLUSIVE."""
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
PAIRS = ((1, 2), (4, 3), (5, 6), (8, 7))
TIME_BAND = (1 / 1.05, 1.05)
SUCC_BAND = (-1 / 24, 1 / 24)
COMPLETION_MIN = 1 / 1.05
MHZ_MIN = 0.95
MIN_PAIRS = 60
POWER_FRAC = 0.95
BUDGET_TASK_S = 120
RESAMPLES = 10000
SEED = 56
MAX_VOID = 2
LOAD_CONTAINERS = 3            # bench/load/load.py components("typical")
SETUP_MAX_S = 900
SMOKE_MIN_DONE = 3
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
    and pod_allowed must equal expect_node and pod_nproc its size; load lines expect_load,
    and at least LOAD_CONTAINERS of them, and load_end must count that many still running."""
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
    nprocs = seen.get("pod_nproc", [])
    if not nprocs:
        out.append("no pod_nproc line")
    for name, v in nprocs:
        if v != str(len(node)):
            out.append(f"pod_nproc {name} {v}, not the slice's {len(node)}")
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
    """Slice d's cycle-0 rows, first per task id, censored ones included."""
    out = {}
    for r in _jsonl(os.path.join(d, "run", "results.jsonl")):
        if (r.get("cycle") or 0) == 0:
            out.setdefault(r["task"], r)
    return out


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


def own_mhz(d):
    """Mean own_mhz of slice d's serving rows inside its task window."""
    w = window(d)
    if w is None:
        return None
    v = [r["own_mhz"] for r in _jsonl(os.path.join(d, "hold", "serving.jsonl"))
         if r.get("own_mhz") is not None and w[0] <= r.get("ts", -1) <= w[1]]
    return sum(v) / len(v) if v else None


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
    h["done"] = sum(1 for r in h["rows"].values() if not r.get("censored"))
    h["pkg_w"] = package_w(s0)
    h["mhz"] = own_mhz(s0)
    pl1 = _read(os.path.join(path, "pl1.txt"))
    h["pl1_w"] = int(pl1) / 1e6 if pl1 and pl1.isdigit() and int(pl1) > 0 else None
    return h


def _ok(r):
    return int(bool(r.get("passed")) and not r.get("censored") and r["duration_s"] <= BUDGET_TASK_S)


def cluster(a, w):
    """One hold pair: per common task (log time ratio or None, success W - A), and the
    completion counts."""
    tasks = []
    for t in sorted(set(a["rows"]) & set(w["rows"])):
        ra, rw = a["rows"][t], w["rows"][t]
        lr = None
        if not ra.get("censored") and not rw.get("censored"):
            lr = math.log(rw["duration_s"] / ra["duration_s"])
        tasks.append((lr, _ok(rw) - _ok(ra)))
    return {"tasks": tasks, "done_a": a["done"], "done_w": w["done"]}


def _stats(cs):
    lr = [x for c in cs for x, _ in c["tasks"] if x is not None]
    sd = [s for c in cs for _, s in c["tasks"]]
    da = sum(c["done_a"] for c in cs)
    return (math.exp(sum(lr) / len(lr)) if lr else None, sum(sd) / len(sd) if sd else None,
            sum(c["done_w"] for c in cs) / da if da else None)


def _ci(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[int(0.05 * (len(xs) - 1))], xs[int(math.ceil(0.95 * (len(xs) - 1)))]


def bootstrap(cs, resamples=RESAMPLES, seed=SEED):
    """Two-stage: hold pairs with replacement, then each drawn pair's tasks."""
    rng = random.Random(seed)
    tr, sr, cr = [], [], []
    for _ in range(resamples):
        draw = []
        for c in (rng.choice(cs) for _ in cs):
            ts = c["tasks"]
            draw.append({"tasks": [rng.choice(ts) for _ in ts], "done_a": c["done_a"], "done_w": c["done_w"]})
        t, s, comp = _stats(draw)
        tr.append(t)
        sr.append(s)
        cr.append(comp)
    return _ci(tr), _ci(sr), _ci(cr)


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
    out = {"used": used, "void": void}
    reasons = []
    if len(void) > MAX_VOID:
        reasons.append(f"{len(void)} VOID condition-holds > {MAX_VOID}")
    missing = [n for n in range(1, 9) if n not in chosen]
    reasons += [f"position {n} missing" for n in missing]
    if missing:
        out.update(verdict="INCONCLUSIVE", reason="; ".join(reasons))
        return out
    cs = [cluster(chosen[a], chosen[w]) for a, w in PAIRS]
    w_holds = [chosen[w] for _, w in PAIRS]
    a_holds = [chosen[a] for a, _ in PAIRS]
    npairs = sum(1 for c in cs for x, _ in c["tasks"] if x is not None)
    pw = [h["pkg_w"] for h in w_holds]
    pkg = sum(pw) / len(pw) if None not in pw else None
    pl1s = {h["pl1_w"] for h in w_holds}
    pl1 = pl1s.pop() if len(pl1s) == 1 else None
    mw, ma = [h["mhz"] for h in w_holds], [h["mhz"] for h in a_holds]
    mhz = (sum(mw) / len(mw)) / (sum(ma) / len(ma)) if None not in mw + ma and sum(ma) > 0 else None
    t, s, comp = _stats(cs)
    out.update(pairs=npairs, success_pairs=sum(len(c["tasks"]) for c in cs),
               done_a=[c["done_a"] for c in cs], done_w=[c["done_w"] for c in cs],
               completion_ratio=None if comp is None else round(comp, 4),
               mhz_w_over_a=None if mhz is None else round(mhz, 4),
               w_pkg_w=None if pkg is None else round(pkg, 2), pl1_w=pl1,
               pkg_w_over_pl1=None if pkg is None or pl1 is None else round(pkg / pl1, 4),
               a_pkg_w=[None if h["pkg_w"] is None else round(h["pkg_w"], 2) for h in a_holds])
    if not npairs:
        reasons.append("no matched time pairs")
        out.update(verdict="INCONCLUSIVE", reason="; ".join(reasons))
        return out
    tci, sci, cci = bootstrap(cs)
    out.update(time_ratio=round(t, 4), time_ci=[round(x, 4) for x in tci],
               success_diff=round(s, 4), success_ci=[round(x, 4) for x in sci],
               completion_ci=None if cci is None else [round(x, 4) for x in cci])
    outside_t = tci[1] < TIME_BAND[0] or tci[0] > TIME_BAND[1]
    outside_s = sci[1] < SUCC_BAND[0] or sci[0] > SUCC_BAND[1]
    below_c = cci is not None and cci[1] < COMPLETION_MIN
    if not reasons and (outside_t or outside_s or below_c):
        why = [w for w, x in (("time CI outside the band", outside_t), ("success CI outside the band", outside_s),
                              ("completion CI below 1/1.05", below_c)) if x]
        out.update(verdict="REFUTED", reason="; ".join(why))
        return out
    if not (TIME_BAND[0] <= tci[0] and tci[1] <= TIME_BAND[1]):
        reasons.append("time CI not inside the band")
    if not (SUCC_BAND[0] <= sci[0] and sci[1] <= SUCC_BAND[1]):
        reasons.append("success CI not inside the band")
    if comp is None or comp < COMPLETION_MIN:
        reasons.append(f"completion W/A {comp} < 1/1.05")
    if npairs < MIN_PAIRS:
        reasons.append(f"{npairs} time pairs < {MIN_PAIRS}")
    if mhz is None:
        reasons.append("own-slice MHz not recorded")
    elif mhz < MHZ_MIN:
        reasons.append(f"own-slice MHz W/A {mhz:.3f} < {MHZ_MIN}")
    if pl1 is None:
        reasons.append("PL1 not read")
    if pkg is None:
        reasons.append("W package power not measured")
    elif pl1 is not None and pkg >= POWER_FRAC * pl1:
        reasons.append(f"W package power {pkg:.1f} W >= {POWER_FRAC} PL1")
    out.update(verdict="INCONCLUSIVE" if reasons else "SUPPORTED", reason="; ".join(reasons))
    return out


def gate(d, smoke=False):
    """Whether h56.sh may run: stage 0 passed and, for a condition-hold, a feasible smoke."""
    reasons, out = [], {}
    try:
        with open(os.path.join(d, "lockwait.json")) as f:
            lw = json.load(f)
    except (OSError, ValueError):
        lw = None
    if not lw:
        reasons.append("no stage-0 lockwait.json")
    elif lw.get("stage1") is not True:
        reasons.append(f"stage 0: share {lw.get('share')} < 0.10, stage 1 is not run")
    if not smoke:
        s0 = os.path.join(d, "smoke-W", "s0")
        rs = [r for r in _jsonl(os.path.join(s0, "run", "results.jsonl")) if (r.get("cycle") or 0) == 0]
        t0 = _read(os.path.join(s0, "t0"))
        if not rs or t0 is None:
            reasons.append("no smoke-W slice-0 record")
        else:
            setup = min(r["t_start"] for r in rs) - float(t0)
            done = sum(1 for r in rs if not r.get("censored"))
            out.update(smoke_setup_s=round(setup, 1), smoke_done=done)
            if setup > SETUP_MAX_S:
                reasons.append(f"smoke slice-0 setup {setup:.0f} s > {SETUP_MAX_S} s")
            if done < SMOKE_MIN_DONE:
                reasons.append(f"smoke slice-0 completed {done} < {SMOKE_MIN_DONE} tasks in 120 s")
    out.update(ok=not reasons, reasons=reasons)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("score")
    p.add_argument("dir")
    g = sub.add_parser("gate")
    g.add_argument("dir")
    g.add_argument("--smoke", action="store_true")
    o = ap.parse_args(argv)
    if o.cmd == "gate":
        r = gate(o.dir, o.smoke)
        print(json.dumps(r))
        return 0 if r["ok"] else 1
    print(json.dumps(score(o.dir), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
