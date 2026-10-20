#!/usr/bin/env python3
"""analyze.py: score the H55 records with the pre-registered rule.

  bench/admission/analyze.py RESULTS_DIR [--out analysis.json]

RESULTS_DIR holds one directory per run, r<round>-p<position>-<arm>, written by
bench/admission/run.sh: run.json, load.jsonl (load.py), and for A_slo ctl.jsonl
(ctl.py). The rule is that of EXPERIMENTS.md "H55 pre-registration":

  requests        every request started inside the 900 s window
  penalty(arm)    median ttft_s over all the arm's requests / Q median - 1
  loss(arm)       1 - median neighbour bogo-ops/s (real) / N0's
  NB spread       (max - min of the five N0 per-run bogo-ops/s) / N0 median
  Q spread        (max - min of the five Q per-run medians) / Q median
  drift           |median of Q requests in rounds 1-2 - in rounds 4-5| > max - min
                  of the Q per-run medians (seconds)
  INCONCLUSIVE    a run missing or with a failed request; drift; N0 penalty
                  inside the Q spread; a neighbour run with no bogo-ops/s value; an
                  A_slo run with fewer than 4 windows, a stomp, ENOENT, a read or
                  write error inside the window, or ctl.py dead at the window end; a
                  neighbour that ended before the window; a static arm without both
                  cpu.max reads equal to its set value
  SUPPORTED       else, all of (a) A_slo penalty <= +10%, (b) A_slo loss < 72.4%
                  (H5 CMIN, matrixprod), (c) no static arm (N0, K50, K25) with
                  penalty <= +10% and loss <= A_slo's loss + NB spread
  REFUTED         else
Python 3 standard library only.
"""

import argparse
import json
import math
import os
import re
import statistics
import sys

ARMS = ("Q", "N0", "K50", "K25", "A_slo")
STATIC = ("N0", "K50", "K25")
ROUNDS = (1, 2, 3, 4, 5)
PENALTY_BAR = 0.10
H5_CMIN_LOSS = 0.724
MIN_WINDOWS = 4
RUN_DIR = re.compile(r"^r([1-5])-p([1-5])-(Q|N0|K50|K25|A_slo)$")


def p90(values):
    v = sorted(values)
    return v[max(0, math.ceil(0.9 * len(v)) - 1)]


def _jsonl(path):
    out = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if line.endswith("\n") and line.strip():
                    out.append(json.loads(line))
    return out


def load_runs(root):
    runs = []
    for name in sorted(os.listdir(root)):
        m = RUN_DIR.match(name)
        if not m:
            continue
        d = os.path.join(root, name)
        with open(os.path.join(d, "run.json")) as f:
            run = json.load(f)
        w0, w1 = run["window_start"], run["window_end"]
        reqs = [r for r in _jsonl(os.path.join(d, "load.jsonl")) if w0 <= r["t_start"] < w1]
        nb = run.get("neighbour") or {}
        rec = {"arm": m.group(3), "round": int(m.group(1)),
               "ttft": [r["ttft_s"] for r in reqs if r.get("ok")],
               "failed": sum(1 for r in reqs if not r.get("ok")),
               "nb_bogo_s": nb.get("bogo_ops_per_s_real"),
               "nb_ended_before_window_end": (m.group(3) != "Q" and
                                              run.get("neighbour_running_at_window_end") is not True),
               # Static arms: both reads (window start and end) present and equal to
               # the set value. A read that failed is absent, and fails this.
               "cpumax_ok": (run.get("cpumax_set") is None or
                             run.get("cpumax_reads") == [run["cpumax_set"]] * 2)}
        if rec["arm"] == "A_slo":
            ticks = _jsonl(os.path.join(d, "ctl.jsonl"))
            inside = [t for t in ticks if w0 <= t["t"] < w1]
            rec["ctl"] = {"windows": max([t.get("windows", 0) for t in ticks] or [0]),
                          "stomps": sum(1 for t in inside if t.get("stomp")),
                          "enoent": sum(1 for t in inside if t.get("event") == "no_neighbour"),
                          "read_errors": sum(1 for t in inside if t.get("event") == "read_error"),
                          "write_errors": sum(1 for t in inside if t.get("event") == "write_error"),
                          "alive_at_window_end": bool(run.get("ctl_alive_at_window_end"))}
        runs.append(rec)
    return runs


def analyze(runs):
    reasons = []
    by = {(r["arm"], r["round"]): r for r in runs}
    for arm in ARMS:
        for rnd in ROUNDS:
            r = by.get((arm, rnd))
            if r is None:
                reasons.append(f"{arm} round {rnd} missing")
                continue
            if r["failed"]:
                reasons.append(f"{arm} round {rnd}: {r['failed']} failed request(s)")
            if not r["ttft"]:
                reasons.append(f"{arm} round {rnd}: no completed request")
            if arm != "Q" and r["nb_bogo_s"] is None:
                reasons.append(f"{arm} round {rnd}: no neighbour bogo-ops/s (metrics-brief line missing)")
            if arm != "Q" and r["nb_ended_before_window_end"]:
                reasons.append(f"{arm} round {rnd}: neighbour ended before the window")
            if arm in STATIC and not r["cpumax_ok"]:
                reasons.append(f"{arm} round {rnd}: cpu.max read-back differs from its set value")
            if arm == "A_slo":
                c = r["ctl"]
                if c["windows"] < MIN_WINDOWS:
                    reasons.append(f"A_slo round {rnd}: {c['windows']} windows (< {MIN_WINDOWS})")
                for k in ("stomps", "enoent", "read_errors", "write_errors"):
                    if c[k]:
                        reasons.append(f"A_slo round {rnd}: {c[k]} {k} in the window")
                if not c["alive_at_window_end"]:
                    reasons.append(f"A_slo round {rnd}: ctl.py dead at the window end")
    if reasons:
        return {"verdict": "INCONCLUSIVE", "reasons": reasons, "failed_conditions": [], "arms": {}}

    def pooled(arm, rounds=ROUNDS):
        return [v for rnd in rounds for v in by[(arm, rnd)]["ttft"]]

    q_med = statistics.median(pooled("Q"))
    q_p90 = p90(pooled("Q"))
    q_runs = [statistics.median(by[("Q", rnd)]["ttft"]) for rnd in ROUNDS]
    spread_s = max(q_runs) - min(q_runs)
    drift_s = abs(statistics.median(pooled("Q", (1, 2))) - statistics.median(pooled("Q", (4, 5))))
    n0_runs = [by[("N0", rnd)]["nb_bogo_s"] for rnd in ROUNDS]
    n0_bogo = statistics.median(n0_runs)
    nb_spread = (max(n0_runs) - min(n0_runs)) / n0_bogo
    arms = {}
    for arm in ARMS[1:]:
        bogo = statistics.median(by[(arm, rnd)]["nb_bogo_s"] for rnd in ROUNDS)
        arms[arm] = {"penalty": statistics.median(pooled(arm)) / q_med - 1,
                     "p90_penalty": p90(pooled(arm)) / q_p90 - 1,
                     "loss": 1 - bogo / n0_bogo, "bogo_ops_per_s": bogo}
    out = {"q_median_s": q_med, "q_spread_s": spread_s, "q_spread": spread_s / q_med,
           "drift_s": drift_s, "nb_spread": nb_spread, "arms": arms, "failed_conditions": []}
    if arms["N0"]["penalty"] <= spread_s / q_med:
        reasons.append(f"N0 penalty {arms['N0']['penalty']:+.1%} inside the Q spread {spread_s / q_med:.1%}")
    if drift_s > spread_s:
        reasons.append(f"Q drift {drift_s:.3f} s exceeds the spread {spread_s:.3f} s")
    if reasons:
        return dict(out, verdict="INCONCLUSIVE", reasons=reasons)
    a = arms["A_slo"]
    failed = []
    if not a["penalty"] <= PENALTY_BAR:
        failed.append("a")
        reasons.append(f"(a) A_slo penalty {a['penalty']:+.1%} > +{PENALTY_BAR:.0%}")
    if not a["loss"] < H5_CMIN_LOSS:
        failed.append("b")
        reasons.append(f"(b) A_slo loss {a['loss']:.1%} >= {H5_CMIN_LOSS:.1%}")
    for arm in STATIC:
        s = arms[arm]
        # A static arm within the neighbour's own run-to-run spread of A_slo's loss
        # does as well: the controller has not been shown to beat it.
        if s["penalty"] <= PENALTY_BAR and s["loss"] <= a["loss"] + nb_spread:
            if "c" not in failed:
                failed.append("c")
            reasons.append(f"(c) static {arm} {s['penalty']:+.1%} at {s['loss']:.1%} loss does as well")
    return dict(out, verdict="REFUTED" if failed else "SUPPORTED", reasons=reasons, failed_conditions=failed)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("results")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    res = analyze(load_runs(a.results))
    text = json.dumps(res, indent=1, sort_keys=True)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
