#!/usr/bin/env python3
"""lockwait.py CAMPAIGN_LOG [--runs DIR] [--last N]: H56 stage 0 (EXPERIMENTS.md "H56
pre-registration"), read-only. From hold.sh's campaign.log, per hold: the lock wait
("waiting for the lock" to "lock taken", or to "lock not taken in 7200 s"), the hold
time ("lock taken" to "rc="), and the load-gate wait (runs/<NAME>/wait_s, the last hold
of that name). Prints JSON with share = sum(lock wait) / sum(hold time), over every hold
and over the last N (default 20). stage1 is share >= 0.10; adoption_share_met is
share_last >= 0.25."""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

LINE = re.compile(r"^== (\S+) (.*) (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)$")
STAGE1_SHARE = 0.10
ADOPT_SHARE = 0.25


def _ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def _gate(runs, name):
    try:
        with open(os.path.join(runs, name, "wait_s")) as f:
            return int(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def parse(lines):
    """Hold records in the order they began: name, t_wait, t_taken, t_end, rc."""
    holds, open_ = [], {}
    for line in lines:
        m = LINE.match(line.rstrip("\n"))
        if not m:
            continue
        name, msg, t = m.group(1), m.group(2), _ts(m.group(3))
        if msg == "waiting for the lock":
            open_[name] = {"name": name, "t_wait": t, "t_taken": None, "t_end": None, "rc": None}
            holds.append(open_[name])
            continue
        h = open_.get(name)
        if h is None:
            continue
        if msg == "lock taken":
            h["t_taken"] = t
        elif msg.startswith("lock not taken"):
            h["t_end"] = t
            del open_[name]
        elif msg.startswith("rc="):
            h["t_end"], h["rc"] = t, msg[3:].split(";")[0]
            del open_[name]
    return holds


def _share(hs):
    hold = sum(h["hold_s"] for h in hs)
    return round(sum(h["lock_wait_s"] for h in hs) / hold, 4) if hold > 0 else None


def analyse(log, runs, last=20):
    with open(log) as f:
        raw = parse(f)
    holds = []
    for h in raw:
        if h["t_end"] is None:
            continue                       # still running or cut off: not counted
        taken = h["t_taken"]
        holds.append({"name": h["name"], "rc": h["rc"],
                      "lock_wait_s": int((taken if taken is not None else h["t_end"]) - h["t_wait"]),
                      "hold_s": int(h["t_end"] - taken) if taken is not None else 0,
                      "gate_wait_s": _gate(runs, h["name"]) if taken is not None else None})
    tail = holds[-last:]
    share, share_last = _share(holds), _share(tail)
    return {"holds": holds, "sum_lock_wait_s": sum(h["lock_wait_s"] for h in holds),
            "sum_gate_wait_s": sum(h["gate_wait_s"] or 0 for h in holds),
            "sum_hold_s": sum(h["hold_s"] for h in holds), "share": share, "last": len(tail),
            "share_last": share_last, "stage1": share is not None and share >= STAGE1_SHARE,
            "adoption_share_met": share_last is not None and share_last >= ADOPT_SHARE}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log")
    ap.add_argument("--runs", default=None, help="hold record dirs (default: the log's directory)")
    ap.add_argument("--last", type=int, default=20)
    o = ap.parse_args(argv)
    print(json.dumps(analyse(o.log, o.runs or os.path.dirname(os.path.abspath(o.log)), o.last), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
