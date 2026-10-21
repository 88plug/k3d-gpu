#!/usr/bin/env python3
"""ctl.py: the H55 A_slo controller. The one writer of the neighbour's cpu.max.

  ctl.py --container NAME --ref SECONDS --jsonl LOAD.jsonl --log CTL.jsonl
         --until EPOCH [--start-level 25]

--ref is the round's quiet reference: the median TTFT of its 20 quiet pre-roll
requests (reference()). Control and scoring use the same statistic, the median.
Every 1 s tick it:
  1. resolves the neighbour's cgroup: docker State.Pid -> /proc/<pid>/cgroup ->
     /sys/fs/cgroup/<path>/cpu.max. No neighbour (ENOENT): no write, one log line;
  2. reads cpu.max back; a value other than its own last write is a stomp. Only a
     foreign value still there at this read is seen: one written and reverted
     between two reads, or written between this read and the write below, is
     overwritten without being counted. Another read error: no write, one log line;
  3. consumes the newline-terminated records load.py appended since the last
     tick; each 20 completed (ok) requests close one window, whose median moves
     the level: down one if it is above SLO = 1.10 x ref, up one if it is at most
     1.05 x ref, else hold, clamped to LEVELS;
  4. writes the absolute quota of the current level. The write(2) is the one
     linearization point; the value depends only on the level, never on the
     read-back, so a stomp is overwritten on the next tick.

The inference path never waits on this process: load.py only appends, and this
process only reads that file. Bounded loop, one write per tick; not wait-free.
It exits once the neighbour is gone after --until, or 120 s after --until.
Python 3 standard library only.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

LEVELS = (25.0, 37.5, 50.0, 62.5, 75.0, 87.5, 100.0)  # % of NB_CPUS
NB_CPUS = 15
PERIOD_US = 100000
WINDOW = 20
DOWN_ABOVE = 1.10  # the SLO, as a multiple of the quiet reference; the +10% pass bar
UP_AT_OR_BELOW = 1.05


def quota_line(level):
    return f"{round(NB_CPUS * PERIOD_US * level / 100)} {PERIOD_US}\n"


def window_stat(values):
    return statistics.median(values)


def reference(preroll):
    """The quiet reference: the window statistic over exactly WINDOW quiet requests."""
    if len(preroll) != WINDOW:
        raise ValueError(f"need {WINDOW} quiet pre-roll requests, got {len(preroll)}")
    return window_stat(preroll)


def step(idx, stat, ref):
    if stat > DOWN_ABOVE * ref:
        return max(0, idx - 1)
    if stat <= UP_AT_OR_BELOW * ref:
        return min(len(LEVELS) - 1, idx + 1)
    return idx


class Tail:
    """New complete lines of an append-only JSONL file. A trailing record without
    its newline is left for a later read."""

    def __init__(self, path):
        self.path = path
        self.offset = 0

    def records(self):
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except FileNotFoundError:
            return []
        end = data.rfind(b"\n")
        if end < 0:
            return []
        self.offset += end + 1
        out = []
        for line in data[:end].split(b"\n"):
            if line.strip():
                out.append(json.loads(line))
        return out


class Controller:
    def __init__(self, ref, tail, resolve, log, start_level=25.0):
        self.ref = ref
        self.tail = tail
        self.resolve = resolve
        self.log = log
        self.idx = LEVELS.index(start_level)
        self.pending = []
        self.windows = 0
        self.stomps = 0
        self.last_written = None

    @property
    def level(self):
        return LEVELS[self.idx]

    def tick(self, now):
        entry = {"t": now, "event": "tick", "level": self.level, "stomp": False, "windows": self.windows}
        try:
            path = self.resolve()
            with open(path) as f:
                readback = f.read()
        except FileNotFoundError:
            entry["event"] = "no_neighbour"
            self.log(entry)
            return entry
        except OSError as e:
            entry["event"] = "read_error"
            entry["error"] = f"{type(e).__name__}: {e}"
            self.log(entry)
            return entry
        entry["readback"] = readback.strip()
        if self.last_written is not None and readback.strip() != self.last_written.strip():
            self.stomps += 1
            entry["stomp"] = True
        for rec in self.tail.records():
            if rec.get("ok") and rec.get("ttft_s") is not None:
                self.pending.append(rec["ttft_s"])
        while len(self.pending) >= WINDOW:
            win, self.pending = self.pending[:WINDOW], self.pending[WINDOW:]
            m = window_stat(win)
            self.idx = step(self.idx, m, self.ref)
            self.windows += 1
            entry.setdefault("median", []).append(m)
        target = quota_line(self.level)
        entry.update(level=self.level, target=target.strip(), windows=self.windows)
        try:
            with open(path, "w") as f:
                f.write(target)
            self.last_written = target
        except FileNotFoundError:
            entry["event"] = "no_neighbour"
        except OSError as e:
            entry["event"] = "write_error"
            entry["error"] = f"{type(e).__name__}: {e}"
        self.log(entry)
        return entry


def docker_resolver(container):
    def resolve():
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Pid}}", container],
                           capture_output=True, text=True)
        pid = r.stdout.strip()
        if r.returncode != 0 or pid in ("", "0"):
            raise FileNotFoundError(container)
        with open(f"/proc/{pid}/cgroup") as f:
            for line in f:
                if line.startswith("0::"):
                    return os.path.join("/sys/fs/cgroup", line[3:].strip().lstrip("/"), "cpu.max")
        raise FileNotFoundError(f"/proc/{pid}/cgroup has no v2 entry")
    return resolve


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--container", required=True)
    ap.add_argument("--ref", type=float, required=True)
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--until", type=float, required=True)
    ap.add_argument("--start-level", type=float, default=25.0, choices=LEVELS)
    a = ap.parse_args(argv)
    fd = os.open(a.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)

    def log(entry):
        os.write(fd, (json.dumps(entry) + "\n").encode())

    c = Controller(a.ref, Tail(a.jsonl), docker_resolver(a.container), log, a.start_level)
    seen = False
    while True:
        now = time.time()
        e = c.tick(now)
        seen = seen or e["event"] != "no_neighbour"
        if now > a.until and (e["event"] == "no_neighbour" and seen or now > a.until + 120):
            break
        time.sleep(max(0.0, 1.0 - (time.time() - now)))
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
