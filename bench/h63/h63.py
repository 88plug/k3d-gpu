#!/usr/bin/env python3
"""h63.py: the H63d and H63w scorer (EXPERIMENTS.md "H63 pre-registration").

  h63.py score RESULTS_DIR [--arms Ls=NAME,Od=NAME[,Ld-mtp=NAME]]
  h63.py order HOLD                     hold 1-4's registered arm order (for the runner)

The row's verdict from the recorded holds. Arms: Ls (k3d-gpu's own single-stream llama.cpp render
with the QAT MTP drafter, the rule's arm), Od (k3d-gpu's OVMS default) and Ld-mtp (H57w's
llama.cpp arm, reported and never gated). The statistic is the ratio of Ls's to Od's median
per-request N=1 decode tok/s over the pooled requests of performance holds 1-3, with H54's
bootstrap CI (boot_ratio: 10,000 resamples of each arm, seed 1, percentiles 2.5 and 97.5), and the
per-hold ratios. The checks run in H59's order: an arm not deliverable in two holds, an arm measured
in fewer than 3 performance holds or with no quality record (INCONCLUSIVE); the quality gate, Ls's
GSM8K items correct >= Od's - 2 (REFUTED when it fails); the load gate (INCONCLUSIVE when
unmatched); then SUPPORTED when the CI lower bound is >= 1.2 and Ls is faster than Od in each
performance hold, REFUTED when the CI upper bound is < 1.2, INCONCLUSIVE otherwise.

The load gate is H59 amendment R2's: H57d's gate over each arm's mean foreign P-core load in its
60 s idle windows (bench/h59/run.py idle_gate), over Ls and Od, the two arms the statistic compares.
The same gate over the N=1 phases and over all three arms' idle windows is recorded, not gated.

Records, in H57d's layout: RESULTS_DIR/holdK/ARM/status.json for K = 1-4 (1-3 performance, 4
quality) and spare holds hold5 and hold6, each with a file `repeats` naming the hold it replaces
(bench/h59/h59.py hold_dirs); ARM/load-n1.json, load-n4.json, load-ttft.json (bench/llm_load.py),
ARM/phases.jsonl and holdK/load.jsonl (H57d's sampler) in a performance hold; ARM/quality.json in
the quality hold. A performance hold counts as measured for an arm when its status is measured and
its N=1 phase completed a request. Failed requests (ok false) are dropped, as in H54's, H57d's and
H59's scorers.
Python 3 standard library only."""

import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "h59"))
import h59  # noqa: E402  (hold_dirs, status, decode_samples, phase_samples; puts h54 and h57d on the path)
import run as h59run  # noqa: E402  (idle_gate: H59 amendment R2's load gate)
import h54  # noqa: E402  (boot_ratio, median)
import h57d  # noqa: E402  (foreign, load_gate)

BAR = 1.2            # SUPPORTED: CI lower bound >= 1.2; REFUTED: CI upper bound < 1.2
QUALITY_SLACK = 2    # Ls's GSM8K items correct >= Od's - 2 (of 50)
PERF_HOLDS = h59.PERF_HOLDS
QUALITY_HOLD = h59.QUALITY_HOLD
SPARES = h59.SPARES
ROLES = ("Ls", "Od", "Ld-mtp")
GATED = ("Ls", "Od")
# Performance holds 1-3 rotate the three arms (each arm once in each position); hold 4 is the
# quality hold, the gated arms first.
ORDERS = {1: ["Od", "Ld-mtp", "Ls"], 2: ["Ld-mtp", "Ls", "Od"], 3: ["Ls", "Od", "Ld-mtp"], 4: ["Ls", "Od", "Ld-mtp"]}


def jload(p):
    with open(p) as f:
        return json.load(f)


def ok_values(arm_dir, phase, key):
    return [x[key] for x in h59.phase_samples(arm_dir, phase) if x.get("ok") and x.get(key)]


def phase_total(arm_dir, phase, key):
    p = os.path.join(arm_dir, f"load-{phase}.json")
    if not os.path.exists(p):
        return None
    return jload(p)["phases"][0].get(key)


def collect(results, role, name):
    """One arm's pooled N=1, N=4 and TTFT samples, per-hold N=1 medians, quality and delivery."""
    hd = h59.hold_dirs(results)
    rec = {"role": role, "name": name, "pooled": {"n1": [], "n4": [], "ttft": []}, "n1_per_hold": {},
           "holds_measured": 0, "not_deliverable": 0, "quality": None, "n4_aggregate": {}, "n4_ttft": []}
    for k in PERF_HOLDS + (QUALITY_HOLD,) + SPARES:
        if h59.status(os.path.join(results, f"hold{k}", name)) == "not_deliverable":
            rec["not_deliverable"] += 1
    for k in PERF_HOLDS:
        ad = os.path.join(hd[k], name)
        s = h59.decode_samples(ad, "n1")
        if h59.status(ad) != "measured" or not s:
            continue
        rec["holds_measured"] += 1
        rec["n1_per_hold"][k] = h54.median(s)
        rec["pooled"]["n1"] += s
        rec["pooled"]["n4"] += h59.decode_samples(ad, "n4")
        rec["pooled"]["ttft"] += ok_values(ad, "ttft", "ttft_s")
        rec["n4_ttft"] += ok_values(ad, "n4", "ttft_s")
        agg = phase_total(ad, "n4", "aggregate_tokens_per_s")
        if agg is not None:
            rec["n4_aggregate"][k] = agg
    qd = os.path.join(hd[QUALITY_HOLD], name)
    if h59.status(qd) == "measured" and os.path.exists(os.path.join(qd, "quality.json")):
        q = jload(os.path.join(qd, "quality.json"))
        rec["quality"] = {"correct": q["correct"], "n": q["n"]}
    rec["n1_median"] = h54.median(rec["pooled"]["n1"])
    rec["n4_median"] = h54.median(rec["pooled"]["n4"])
    rec["ttft_median_s"] = h54.median(rec["pooled"]["ttft"])
    rec["n4_ttft_s"] = h54.median(rec["n4_ttft"])
    rec["n1_requests"] = len(rec["pooled"]["n1"])
    return rec


def phase_load(results, names, phase):
    """H57d's load gate over the arms' mean foreign P-core load in one phase (recorded only)."""
    hd = h59.hold_dirs(results)
    means = {}
    for a in names:
        f = []
        for k in PERF_HOLDS:
            ph, sp = os.path.join(hd[k], a, "phases.jsonl"), os.path.join(hd[k], "load.jsonl")
            if h59.status(os.path.join(hd[k], a)) != "measured" or not (os.path.exists(ph) and os.path.exists(sp)):
                continue
            with open(sp) as fh:
                samples = [json.loads(x) for x in fh if x.strip()]
            with open(ph) as fh:
                for r in (json.loads(x) for x in fh if x.strip()):
                    if r.get("phase") == phase:
                        v = h57d.foreign(samples, r["t0"], r["t1"])
                        if v is not None:
                            f.append(v)
        means[a] = statistics.mean(f) if f else None
    return dict(h57d.load_gate(means), means=means, gates=False,
                rule=f"H57d's load gate over the {phase} phases (recorded, not gated)")


def pair(a, b, phase, ci=True):
    """Reported: a's pooled median over b's for one phase, its CI and (N=1) the per-hold ratios."""
    x, y = a["pooled"][phase], b["pooled"][phase]
    if not x or not y:
        return None
    out = {"ratio": h54.median(x) / h54.median(y)}
    if ci:
        out["ci"] = list(h54.boot_ratio(x, y))
    if phase == "n1":
        out["per_hold"] = {k: a["n1_per_hold"][k] / b["n1_per_hold"][k]
                           for k in PERF_HOLDS if k in a["n1_per_hold"] and k in b["n1_per_hold"]}
    return out


def decide(ls, od, load):
    """The registered rule, in H59's order: delivery, quality record, quality gate, load, then
    the N=1 CI against 1.2 with Ls faster than Od in each performance hold."""
    out = {}
    for a in (ls, od):
        if a["not_deliverable"] >= 2:
            return dict(out, verdict="INCONCLUSIVE", reason=f"{a['role']} not deliverable in two holds")
    for a in (ls, od):
        if a["holds_measured"] < len(PERF_HOLDS):
            return dict(out, verdict="INCONCLUSIVE",
                        reason=f"{a['role']} measured in {a['holds_measured']} of 3 performance holds")
    for a in (ls, od):
        if a["quality"] is None:
            return dict(out, verdict="INCONCLUSIVE", reason=f"{a['role']} has no quality record")
    need = od["quality"]["correct"] - QUALITY_SLACK
    out["quality_gate"] = {"Ls": ls["quality"]["correct"], "Od": od["quality"]["correct"], "need": need,
                           "passed": ls["quality"]["correct"] >= need}
    if not out["quality_gate"]["passed"]:
        return dict(out, verdict="REFUTED",
                    reason=f"quality gate failed: Ls {ls['quality']['correct']} < Od's - 2 ({need})")
    if not load.get("matched"):
        return dict(out, verdict="INCONCLUSIVE", reason="load unmatched: " + str(load.get("reason", "no load verdict")))
    lo, hi = h54.boot_ratio(ls["pooled"]["n1"], od["pooled"]["n1"])
    per = {k: ls["n1_per_hold"][k] / od["n1_per_hold"][k] for k in PERF_HOLDS}
    out.update(ratio=ls["n1_median"] / od["n1_median"], ci=[lo, hi], per_hold=per,
               all_holds_faster=all(v > 1 for v in per.values()))
    if lo >= BAR and out["all_holds_faster"]:
        return dict(out, verdict="SUPPORTED",
                    reason="N=1 CI lower bound >= 1.2, Ls faster than Od in each performance hold, and the quality gate passed")
    if hi < BAR:
        return dict(out, verdict="REFUTED", reason="N=1 CI upper bound < 1.2")
    if lo >= BAR:
        return dict(out, verdict="INCONCLUSIVE",
                    reason="N=1 CI lower bound >= 1.2, but Ls not faster than Od in each performance hold")
    return dict(out, verdict="INCONCLUSIVE", reason="N=1 CI not resolved against 1.2")


def score(results, arms=None):
    """arms: {role: record directory name}; default each role's own name."""
    names = dict(zip(ROLES, ROLES))
    names.update(arms or {})
    recs = {r: collect(results, r, names[r]) for r in ROLES}
    gate = h59run.idle_gate(results, {r: names[r] for r in GATED})
    gate.pop("n4", None)
    rep = {"arms": {r: {k: v for k, v in a.items() if k not in ("pooled", "n4_ttft")} for r, a in recs.items()},
           "load_gate": gate,
           "load_recorded": {"n1": phase_load(results, [names[r] for r in GATED], "n1"),
                             "idle_all_arms": dict(h59run.idle_gate(results, names), gates=False)}}
    rep["load_recorded"]["idle_all_arms"].pop("n4", None)
    ls, od, ld = recs["Ls"], recs["Od"], recs["Ld-mtp"]
    rep["reported"] = {
        "Ls/Od": {"n1": pair(ls, od, "n1", ci=False), "n4": pair(ls, od, "n4", ci=False), "ttft": pair(ls, od, "ttft", ci=False)},
        "Ld-mtp/Od": {"n1": pair(ld, od, "n1"), "n4": pair(ld, od, "n4", ci=False), "ttft": pair(ld, od, "ttft", ci=False)},
        "Ls/Ld-mtp": {"n1": pair(ls, ld, "n1"), "n4": pair(ls, ld, "n4", ci=False), "ttft": pair(ls, ld, "ttft", ci=False)},
    }
    rep.update(decide(ls, od, gate))
    return rep


def parse_arms(s):
    try:
        m = dict(kv.split("=", 1) for kv in s.split(","))
    except ValueError:
        raise SystemExit("h63.py: --arms takes ROLE=NAME pairs, e.g. Ls=L,Od=O")
    if set(m) - set(ROLES) or not set(GATED) <= set(m) or not all(m.values()):
        raise SystemExit("h63.py: --arms names Ls and Od (and optionally Ld-mtp), e.g. Ls=L,Od=O")
    return m


def main(argv):
    if len(argv) == 2 and argv[0] == "order":
        if not re.fullmatch(r"[1-4]", argv[1]):
            sys.stderr.write(f"h63.py order: hold {argv[1]!r} is not registered (holds 1-4)\n")
            return 2
        print(" ".join(ORDERS[int(argv[1])]))
        return 0
    if len(argv) < 2 or argv[0] != "score":
        raise SystemExit(__doc__)
    results, rest = argv[1], argv[2:]
    opts = dict(zip(rest[::2], rest[1::2]))
    if len(rest) % 2 or set(opts) - {"--arms"}:
        raise SystemExit("h63.py score RESULTS_DIR [--arms Ls=NAME,Od=NAME[,Ld-mtp=NAME]]")
    arms = parse_arms(opts["--arms"]) if "--arms" in opts else None
    print(json.dumps(score(results, arms), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
