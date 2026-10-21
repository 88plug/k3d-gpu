#!/usr/bin/env python3
"""lockwait.py CAMPAIGN_LOG --xlog X_CAMPAIGN_LOG [--runs DIR] [--last N]: H56 stage 0
(EXPERIMENTS.md "H56 pre-registration"), read-only. From hold.sh's campaign.log, per hold:
the lock wait ("waiting for the lock" to "lock taken", or to "lock not taken in 7200 s"), the
hold time ("lock taken" to "rc="), and the load-gate wait (runs/<NAME>/wait_s, the last hold
of that name). A hold with no end line (killed, timed out, or an exit after the lock such as
the disk refusal) is counted up to its last line and marked "unterminated". H56's own holds
(names H56-*) are not counted. Lock wait that overlaps a hold of the k3dgpu-x-* campaign
(--xlog, its own campaign.log; holding the lock from "lock taken" to its end) is wait_behind_x:
slots cannot remove it, as x holds stay exclusive.

Prints JSON. share = sum(lock wait - wait behind x) / sum(hold time); share_all counts every
second of lock wait. Both over every hold and over the last N (default 20); by_row splits
them by the name's first field. stage1 is share >= 0.10; adoption_share_met is share_last
>= 0.25."""
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


def parse(lines, skip=None):
    """Hold records in the order they began: name, t_wait, t_taken, t_end, rc, ended
    ("rc", "not taken" or "unterminated", t_end then the last line seen)."""
    holds, open_ = [], {}

    def close(h, how):
        h["ended"] = how
        if how == "unterminated":
            h["t_end"] = h["t_last"]
        del open_[h["name"]]

    for line in lines:
        m = LINE.match(line.rstrip("\n"))
        if not m:
            continue
        name, msg, t = m.group(1), m.group(2), _ts(m.group(3))
        if skip and skip.match(name):
            continue
        if msg == "waiting for the lock":
            if name in open_:
                close(open_[name], "unterminated")
            open_[name] = {"name": name, "t_wait": t, "t_taken": None, "t_end": None, "rc": None, "t_last": t}
            holds.append(open_[name])
            continue
        h = open_.get(name)
        if h is None:
            continue
        h["t_last"] = t
        if msg == "lock taken":
            h["t_taken"] = t
        elif msg.startswith("lock not taken"):
            h["t_end"] = t
            close(h, "not taken")
        elif msg.startswith("rc="):
            h["t_end"], h["rc"] = t, msg[3:].split(";")[0]
            close(h, "rc")
    for h in list(open_.values()):
        close(h, "unterminated")
    return holds


def _overlap(a, b, spans):
    return sum(max(0.0, min(b, y) - max(a, x)) for x, y in spans)


def _sums(hs):
    hold = sum(h["hold_s"] for h in hs)
    wait = sum(h["lock_wait_s"] for h in hs)
    addr = sum(h["lock_wait_s"] - h["wait_behind_x_s"] for h in hs)
    return {"holds": len(hs), "sum_lock_wait_s": wait, "sum_hold_s": hold,
            "share": round(addr / hold, 4) if hold > 0 else None,
            "share_all": round(wait / hold, 4) if hold > 0 else None}


def analyse(log, runs, last=20, xlog=None):
    with open(log) as f:
        raw = parse(f, skip=re.compile(r"^H56-"))
    xspans, xfound = [], False
    if xlog and os.path.exists(xlog):
        xfound = True
        with open(xlog) as f:
            xspans = [(h["t_taken"], h["t_end"]) for h in parse(f) if h["t_taken"] is not None]
    holds = []
    for h in raw:
        taken = h["t_taken"]
        w_end = taken if taken is not None else h["t_end"]
        holds.append({"name": h["name"], "rc": h["rc"], "ended": h["ended"],
                      "lock_wait_s": int(w_end - h["t_wait"]),
                      "wait_behind_x_s": int(_overlap(h["t_wait"], w_end, xspans)),
                      "hold_s": int(h["t_end"] - taken) if taken is not None else 0,
                      "gate_wait_s": _gate(runs, h["name"]) if taken is not None else None})
    tail = holds[-last:]
    a, t = _sums(holds), _sums(tail)
    rows = {}
    for h in holds:
        rows.setdefault(h["name"].split("-")[0], []).append(h)
    return {"holds": holds, "xlog": xlog, "xlog_found": xfound,
            "unterminated": sum(1 for h in holds if h["ended"] == "unterminated"),
            "sum_lock_wait_s": a["sum_lock_wait_s"],
            "sum_wait_behind_x_s": sum(h["wait_behind_x_s"] for h in holds),
            "sum_gate_wait_s": sum(h["gate_wait_s"] or 0 for h in holds),
            "sum_hold_s": a["sum_hold_s"], "share": a["share"], "share_all": a["share_all"],
            "last": len(tail), "share_last": t["share"],
            "by_row": {k: _sums(v) for k, v in sorted(rows.items())},
            "stage1": a["share"] is not None and a["share"] >= STAGE1_SHARE,
            "adoption_share_met": t["share"] is not None and t["share"] >= ADOPT_SHARE}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log")
    ap.add_argument("--xlog", required=True, help="the k3dgpu-x-* campaign's campaign.log (absent: no x holds)")
    ap.add_argument("--runs", default=None, help="hold record dirs (default: the log's directory)")
    ap.add_argument("--last", type=int, default=20)
    o = ap.parse_args(argv)
    print(json.dumps(analyse(o.log, o.runs or os.path.dirname(os.path.abspath(o.log)), o.last, o.xlog), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
