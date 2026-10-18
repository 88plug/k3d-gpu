#!/usr/bin/env python3
"""live_table.py: the per-arm results table of a live campaign (bench/eatflow/live.sh).

  live_table.py RUNS_DIR [--ref A1]

One row per arm directory RUNS_DIR/<ARM>/run: completed tasks, success (k/n, Wilson
95%), censored, turn p95, task median, gross J per completed task, differential J per
success (block energy minus the arm's own control-window mean power times the block
time, over successes), and whether its background load matched the reference arm's
(load.py matched on the two arms' windows). Standard library only.
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "load"))
import agentbench as AB  # noqa: E402
import load  # noqa: E402


def arm_rows(runs):
    out = {}
    for d in sorted(glob.glob(os.path.join(runs, "*", "run"))):
        rep = AB.build_report([d])
        loads = AB.read_jsonl(os.path.join(d, "load.jsonl"))
        blocks = AB.read_jsonl(os.path.join(d, "blocks.jsonl"))
        for g in rep["groups"]:
            e = [b["energy"]["energy_j"] for b in blocks if (b.get("energy") or {}).get("energy_j") is not None]
            g["gross_j_per_task"] = sum(e) / g["tasks"] if e and g["tasks"] else None
            g["aborted"] = [b.get("aborted") for b in blocks if b.get("aborted")]
            g["loads"] = loads
            g["dir"] = d
            out[g["arm"]] = g
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("runs")
    ap.add_argument("--ref", default="A1")
    a = ap.parse_args(argv)
    rows = arm_rows(a.runs)
    ref = rows.get(a.ref)
    print("| Arm | Load | Completed / 24 | Success (k/n, Wilson 95%) | Censored | Turn p95 s | Task median s | "
          f"J / completed task (gross) | J / success (differential) | Load matched vs {a.ref} |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for arm, g in rows.items():
        lo, hi = g["success_wilson95"]
        if ref and arm != a.ref:
            m = load.matched([dict(w, tag=a.ref) for w in ref["loads"]] + [dict(w, tag=arm) for w in g["loads"]])
            matched = m["verdict"]
        else:
            matched = "reference" if ref else "-"
        print(f"| {arm} | {g['level']} | {g['tasks']} | {AB._f(g['success'])} ({g['successes']}/{g['tasks']}), "
              f"{AB._f(lo)}-{AB._f(hi)} | {g['censored']} | {AB._f(g['turn_p95_s'])} | {AB._f(g['task_median_s'])} | "
              f"{AB._f(g['gross_j_per_task'], 0)} | {AB._f(g.get('j_per_success'), 0)} | {matched} |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
