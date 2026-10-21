#!/usr/bin/env python3
"""holds.py: the processes a crescent hold (hold.sh) owns, and the hold interval record.

  append LOG k=v...      one JSON line, one os.write on an O_APPEND fd (a record never tears)
  check LOG              pair start/end records by tag per host; report overlaps and
                         unterminated holds. Two holds in different cpuset slots (start
                         records with slot=0 and slot=1, H56) do not void each other; a hold
                         without a slot owns the whole host. Exit 0 clean, 1 any void, 2
                         malformed log.
  reap TAG               SIGKILL every process whose environment has K3DGPU_HOLD=TAG (exact
                         string), rescanning until a scan finds none (at most 50 rounds).
  sweep TAG RUNS         the same for processes of this campaign (K3DGPU_HOLD_RUNS=RUNS) whose
                         tag K3DGPU_HOLD=name:pid:start names an owner that is gone; never TAG
                         itself, never a tag that does not parse.
  reap and sweep print how many distinct processes got a SIGKILL; exit 3 if one still runs.

A tag carries the owner's start time (field 22 of /proc/PID/stat), so a reused pid never
makes a dead owner look alive."""
import glob
import json
import os
import signal
import socket
import sys
import time


def starttime(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def alive(pid, start):
    return str(pid).isdigit() and starttime(pid) == str(start)


def append(log, pairs):
    rec = {"t": round(time.time(), 3), "host": socket.gethostname()}
    for p in pairs:
        k, sep, v = p.partition("=")
        if not sep or not k:
            raise SystemExit(f"holds.py append: not k=v: {p!r}")
        rec[k] = v
    data = (json.dumps(rec, sort_keys=True) + "\n").encode()
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        if os.write(fd, data) != len(data):
            raise SystemExit("holds.py append: short write")
    finally:
        os.close(fd)


def check(log):
    """Returns (report, exit code). A start with no end is unterminated: its interval runs
    to the end of the next hold on that host, whose start sweep reaped its tagged orphans but
    whose window may still have shared the host with its untagged leftovers."""
    holds, order, slot = {}, [], {}
    with open(log) as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                ev, tag, host, t = r["ev"], r["tag"], r["host"], float(r["t"])
            except (ValueError, KeyError, TypeError):
                return {"error": f"line {n}: malformed"}, 2
            key = (host, tag)
            if ev == "start":
                if key in holds:
                    return {"error": f"line {n}: second start for {tag}"}, 2
                holds[key] = [t, None]
                slot[key] = r.get("slot") or None
                order.append(key)
            elif ev == "end":
                if key not in holds or holds[key][1] is not None:
                    return {"error": f"line {n}: end without an open start for {tag}"}, 2
                holds[key][1] = t
            else:
                return {"error": f"line {n}: unknown ev {ev!r}"}, 2
    void, unterminated = set(), []
    for host in sorted({h for h, _ in order}):
        ks = sorted((k for k in order if k[0] == host), key=lambda k: holds[k][0])
        end = {}
        for i in range(len(ks) - 1, -1, -1):
            s, e = holds[ks[i]]
            if e is None:
                unterminated.append(ks[i][1])
                void.add(ks[i][1])
                e = end[ks[i + 1]] if i + 1 < len(ks) else float("inf")
            end[ks[i]] = e
        for i, a in enumerate(ks):
            for b in ks[i + 1:]:
                if slot[a] and slot[b] and slot[a] != slot[b]:
                    continue
                if holds[b][0] < end[a] and holds[a][0] < end[b]:
                    void.update((a[1], b[1]))
    rep = {"holds": len(order), "void": sorted(void), "unterminated": sorted(unterminated)}
    return rep, 1 if void else 0


def tagged():
    """(pid, env) for every readable process environment carrying K3DGPU_HOLD. A zombie's
    environ reads empty, so a process that has died is never listed."""
    for path in glob.glob("/proc/[0-9]*/environ"):
        try:
            with open(path, "rb") as f:
                env = dict(kv.split(b"=", 1) for kv in f.read().split(b"\0") if b"=" in kv)
        except OSError:
            continue
        if b"K3DGPU_HOLD" in env:
            yield int(path.split("/")[2]), {k.decode(errors="replace"): v.decode(errors="replace")
                                            for k, v in env.items() if k.startswith(b"K3DGPU_HOLD")}


def kill_until_clear(match, rounds=50, pause=0.05):
    """SIGKILL every process whose tagged env matches, rescanning until a scan finds none.
    Returns (distinct pids a SIGKILL was delivered to, True if the last scan found none).
    A process stuck in uninterruptible sleep outlives all rounds and gives False."""
    killed = set()
    for _ in range(rounds):
        found = [pid for pid, env in tagged() if pid != os.getpid() and match(env)]
        if not found:
            return len(killed), True
        for pid in found:
            try:
                os.kill(pid, signal.SIGKILL)
                killed.add(pid)
            except OSError:
                pass
        time.sleep(pause)
    return len(killed), False


def stale(own, runs):
    """Matches a tag of this campaign (K3DGPU_HOLD_RUNS == runs) whose owner is gone; never
    own, never a tag that does not parse."""
    def match(env):
        tag = env.get("K3DGPU_HOLD", "")
        if tag == own or env.get("K3DGPU_HOLD_RUNS") != runs:
            return False
        parts = tag.rsplit(":", 2)
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            return False
        return not alive(parts[1], parts[2])
    return match


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    cmd, args = argv[1], argv[2:]
    if cmd == "append" and args:
        append(args[0], args[1:])
    elif cmd == "check" and len(args) == 1:
        rep, rc = check(args[0])
        print(json.dumps(rep))
        return rc
    elif cmd == "sweep" and len(args) == 2:
        n, clear = kill_until_clear(stale(args[0], args[1]))
        print(n)
        return 0 if clear else 3
    elif cmd == "reap" and len(args) == 1:
        n, clear = kill_until_clear(lambda env: env.get("K3DGPU_HOLD") == args[0])
        print(n)
        return 0 if clear else 3
    else:
        raise SystemExit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
