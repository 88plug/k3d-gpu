#!/usr/bin/env python3
"""h59.py: the H59, H59d and H59w scorer (EXPERIMENTS.md "H59 pre-registration", "H59
amendment S1" and "H59 amendment S2").

  h59.py score RESULTS_DIR --load h57d [--arms L=NAME,O=NAME]
  h59.py score RESULTS_DIR --load GATE.json [--arms L=NAME,O=NAME]

The row's verdict from the recorded holds: the ratio of L's to O's median per-request decode
tok/s over the pooled N=4 requests of performance holds 1-3, its 95% bootstrap CI (H54's
boot_ratio: 10,000 resamples of each arm, seed 1), the per-hold N=4 ratios (SUPPORTED needs L
faster than O in each performance hold, amendment S2), the quality gate (L's GSM8K items
correct >= O's - 2) and the host's load rule. --load h57d applies H57d's load gate (H59d's rule, and
H59w's through H57w) to the per-arm mean foreign P-core load over the N=4 phases, read from
the H57d sampler's records; --load GATE.json reads {"matched": bool, "reason": str} that a
host runner wrote under its own rule (H59 on the Xeon: H54's). --arms maps the arm names L and
O to the record directories' names (default L=L,O=O).

Records, in H57d's layout: RESULTS_DIR/holdK/ARM/status.json for K = 1-4 (1-3 performance,
4 quality) and spare holds hold5 and hold6, each with a file `repeats` naming the hold it
replaces; ARM/load-n4.json (bench/llm_load.py), ARM/phases.jsonl and holdK/load.jsonl in a
performance hold; ARM/quality.json in the quality hold. Failed requests (ok false) are dropped,
as in H54's and H57d's scorers; each arm's report gives its completed (n4_requests) and sent
(n4_total) N=4 requests.
Python 3 standard library only."""

import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "h54"))
sys.path.insert(0, os.path.join(HERE, "..", "h57d"))
import h54  # noqa: E402  (boot_ratio, median)
import h57d  # noqa: E402  (the P-core foreign-load reading and the load gate)

BAR = 1.2           # SUPPORTED: CI lower bound >= 1.2; REFUTED: CI upper bound < 1.2
QUALITY_SLACK = 2   # L's GSM8K items correct >= O's - 2 (of 50)
PERF_HOLDS = (1, 2, 3)
QUALITY_HOLD = 4
SPARES = (5, 6)
PHASES = ("n1", "n4", "n16")   # n4 decides; n1 and n16 are reported


def jload(p):
    with open(p) as f:
        return json.load(f)


def status(d):
    p = os.path.join(d, "status.json")
    return jload(p).get("status") if os.path.exists(p) else None


def hold_dirs(results):
    """{1: dir, ..., 4: dir}, a spare hold (file `repeats` = k) in place of hold k, in order."""
    d = {k: os.path.join(results, f"hold{k}") for k in PERF_HOLDS + (QUALITY_HOLD,)}
    for s in SPARES:
        spare = os.path.join(results, f"hold{s}")
        rp = os.path.join(spare, "repeats")
        if os.path.exists(rp):
            with open(rp) as f:
                d[int(f.read().strip())] = spare
    return d


def phase_samples(arm_dir, phase):
    """Every request of a phase as bench/llm_load.py recorded it, failed ones included."""
    p = os.path.join(arm_dir, f"load-{phase}.json")
    return jload(p)["phases"][0]["samples"] if os.path.exists(p) else []


def decode_samples(arm_dir, phase):
    """The completed requests' decode tok/s; failed requests are dropped, as in H54 and H57d."""
    return [x["decode_tokens_per_s"] for x in phase_samples(arm_dir, phase)
            if x.get("ok") and x.get("decode_tokens_per_s")]


def collect(results, name):
    """One arm's pooled performance samples, per-hold N=4 medians, quality and deliverability."""
    hd = hold_dirs(results)
    rec = {"name": name, "pooled": {p: [] for p in PHASES}, "n4_per_hold": {}, "holds_measured": 0,
           "not_deliverable": 0, "quality": None, "n4_total": 0}
    for k in PERF_HOLDS + (QUALITY_HOLD,) + SPARES:
        if status(os.path.join(results, f"hold{k}", name)) == "not_deliverable":
            rec["not_deliverable"] += 1
    for k in PERF_HOLDS:
        ad = os.path.join(hd[k], name)
        s = decode_samples(ad, "n4")
        if status(ad) != "measured" or not s:
            continue
        rec["holds_measured"] += 1
        rec["n4_total"] += len(phase_samples(ad, "n4"))
        rec["n4_per_hold"][k] = h54.median(s)
        for p in PHASES:
            rec["pooled"][p] += decode_samples(ad, p)
    qd = os.path.join(hd[QUALITY_HOLD], name)
    if status(qd) == "measured" and os.path.exists(os.path.join(qd, "quality.json")):
        q = jload(os.path.join(qd, "quality.json"))
        rec["quality"] = {"correct": q["correct"], "n": q["n"]}
    rec["median"] = {p: h54.median(v) for p, v in rec["pooled"].items()}
    rec["n4_requests"] = len(rec["pooled"]["n4"])
    return rec


def h57d_load(results, names):
    """H57d's load gate over the per-arm mean foreign P-core load of the N=4 phases."""
    hd = hold_dirs(results)
    means = {}
    for a in names:
        f = []
        for k in PERF_HOLDS:
            ph = os.path.join(hd[k], a, "phases.jsonl")
            sp = os.path.join(hd[k], "load.jsonl")
            if status(os.path.join(hd[k], a)) != "measured" or not (os.path.exists(ph) and os.path.exists(sp)):
                continue
            with open(sp) as fh:
                samples = [json.loads(x) for x in fh if x.strip()]
            with open(ph) as fh:
                for r in (json.loads(x) for x in fh if x.strip()):
                    if r["phase"] == "n4":
                        v = h57d.foreign(samples, r["t0"], r["t1"])
                        if v is not None:
                            f.append(v)
        means[a] = statistics.mean(f) if f else None
    return dict(h57d.load_gate(means), means=means, rule="H57d's load gate over the N=4 phases")


def decide(lc, ov, load):
    """lc, ov: arms L and O as collect() reads them. The registered rule, in H57d's order:
    deliverability, quality record, quality gate, load, then the N=4 CI against 1.2, with
    SUPPORTED also needing L faster than O in each performance hold (amendment S2)."""
    out = {}
    for a in (lc, ov):
        if a["not_deliverable"] >= 2:
            return dict(out, verdict="INCONCLUSIVE", reason=f"{a['name']} not deliverable in two holds")
    for a in (lc, ov):
        if a["holds_measured"] < len(PERF_HOLDS):
            return dict(out, verdict="INCONCLUSIVE",
                        reason=f"{a['name']} measured in {a['holds_measured']} of 3 performance holds")
    for a in (lc, ov):
        if a["quality"] is None:
            return dict(out, verdict="INCONCLUSIVE", reason=f"{a['name']} has no quality record")
    need = ov["quality"]["correct"] - QUALITY_SLACK
    out["quality_gate"] = {"L": lc["quality"]["correct"], "O": ov["quality"]["correct"], "need": need,
                           "passed": lc["quality"]["correct"] >= need}
    if not out["quality_gate"]["passed"]:
        return dict(out, verdict="REFUTED", reason=f"quality gate failed: L {lc['quality']['correct']} < O's - 2 ({need})")
    if not load.get("matched"):
        return dict(out, verdict="INCONCLUSIVE", reason="load unmatched: " + str(load.get("reason", "no load verdict")))
    lo, hi = h54.boot_ratio(lc["pooled"]["n4"], ov["pooled"]["n4"])
    per = {k: lc["n4_per_hold"][k] / ov["n4_per_hold"][k] for k in PERF_HOLDS}
    out.update(ratio=lc["median"]["n4"] / ov["median"]["n4"], ci=[lo, hi], per_hold=per,
               all_holds_faster=all(v > 1 for v in per.values()))
    if lo >= BAR and out["all_holds_faster"]:
        return dict(out, verdict="SUPPORTED",
                    reason="N=4 CI lower bound >= 1.2, L faster in each performance hold, and the quality gate passed")
    if hi < BAR:
        return dict(out, verdict="REFUTED", reason="N=4 CI upper bound < 1.2")
    if lo >= BAR:
        return dict(out, verdict="INCONCLUSIVE",
                    reason="N=4 CI lower bound >= 1.2, but L not faster than O in each performance hold")
    return dict(out, verdict="INCONCLUSIVE", reason="N=4 CI not resolved against 1.2")


def score(results, load, arms=None):
    """load: "h57d" or a {"matched", "reason"} verdict from the host's runner."""
    arms = arms or {"L": "L", "O": "O"}
    lc, ov = collect(results, arms["L"]), collect(results, arms["O"])
    gate = h57d_load(results, [arms["L"], arms["O"]]) if load == "h57d" else load
    rep = {"arms": {r: {k: v for k, v in a.items() if k != "pooled"} for r, a in (("L", lc), ("O", ov))},
           "load_gate": gate}
    rep["reported"] = {p: (lc["median"][p] / ov["median"][p] if lc["median"][p] and ov["median"][p] else None)
                       for p in PHASES if p != "n4"}
    rep.update(decide(lc, ov, gate))
    return rep


def parse_arms(s):
    m = dict(kv.split("=", 1) for kv in s.split(","))
    if sorted(m) != ["L", "O"]:
        raise SystemExit("h59.py: --arms names exactly L and O, e.g. L=Ld-mtp,O=Od")
    return m


def main(argv):
    if len(argv) < 2 or argv[0] != "score":
        raise SystemExit(__doc__)
    results, rest = argv[1], argv[2:]
    opts = dict(zip(rest[::2], rest[1::2]))
    if len(rest) % 2 or set(opts) - {"--load", "--arms"} or "--load" not in opts:
        raise SystemExit("h59.py score RESULTS_DIR --load h57d|GATE.json [--arms L=NAME,O=NAME]")
    load = "h57d" if opts["--load"] == "h57d" else jload(opts["--load"])
    arms = parse_arms(opts["--arms"]) if "--arms" in opts else None
    print(json.dumps(score(results, load, arms), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
