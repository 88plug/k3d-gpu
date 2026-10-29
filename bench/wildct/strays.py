#!/usr/bin/env python3
"""strays.py NAME [PID...]: the processes left from wildct hold NAME (EXPERIMENTS.md "Wildct
amendment R1"): every process whose environment holds WILDCT_HOLD=NAME, which bench/wildct/hold.sh
exports to its command and every descendant inherits, one that runs in a process group of its own
(a `timeout` inside the command) included. This process and the PIDs given are left out. Reads
/proc only; prints the PIDs space-separated (nothing when none)."""
import os
import sys


def strays(name, exclude):
    """Sorted PIDs whose /proc/PID/environ holds WILDCT_HOLD=name, minus `exclude`."""
    want = f"WILDCT_HOLD={name}".encode()
    found = []
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) in exclude:
            continue
        try:
            with open(f"/proc/{d}/environ", "rb") as f:
                env = f.read().split(b"\0")
        except OSError:
            continue
        if want in env:
            found.append(int(d))
    return sorted(found)


if __name__ == "__main__":
    skip = {os.getpid(), os.getppid(), *(int(p) for p in sys.argv[2:])}
    print(" ".join(str(p) for p in strays(sys.argv[1], skip)))
