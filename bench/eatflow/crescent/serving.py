#!/usr/bin/env python3
"""serving.py OUT.jsonl: the serving covariate of a crescent hold (EXPERIMENTS.md "H50-H52
pre-registration", shared rule 3). Every 10 s: load1, the CPU-s/s of every running container
this campaign did not create (cgroup cpu.stat), and the aiperf processes in aiperf010 other
than its idle `sleep infinity`. Read-only; stops on SIGTERM."""
import json
import os
import signal
import subprocess
import sys
import time

MINE = ("k3dgpu", "k3d-gpu-bench-load")   # name substrings of this campaign's containers
STEP = 10.0
stop = False


def _term(*_):
    global stop
    stop = True


def containers():
    out = subprocess.run(["docker", "ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}"],
                         capture_output=True, text=True).stdout
    rows = {}
    for line in out.splitlines():
        cid, name = line.split(" ", 1)
        if not any(m in name for m in MINE):
            rows[name] = f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/cpu.stat"
    return rows


def usage(path):
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("usage_usec"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def aiperf():
    p = subprocess.run(["docker", "top", "aiperf010", "-eo", "pid,args"], capture_output=True, text=True)
    if p.returncode != 0:
        return None
    return sum(1 for ln in p.stdout.splitlines()[1:] if "sleep infinity" not in ln)


def main(out):
    signal.signal(signal.SIGTERM, _term)
    cs, t_list = containers(), time.time()
    prev, t0 = {n: usage(p) for n, p in cs.items()}, time.time()
    with open(out, "a") as f:
        while not stop:
            time.sleep(STEP)
            t1 = time.time()
            cur = {n: usage(p) for n, p in cs.items()}
            cpu = {n: round((cur[n] - prev[n]) / 1e6 / (t1 - t0), 3)
                   for n in cs if cur.get(n) is not None and prev.get(n) is not None}
            with open("/proc/loadavg") as la:
                load1 = float(la.read().split()[0])
            f.write(json.dumps({"ts": round(t1, 3), "seconds": round(t1 - t0, 3), "load1": load1, "cpu": cpu,
                                "serving_cpu": round(sum(cpu.values()), 3), "aiperf_procs": aiperf()}) + "\n")
            f.flush()
            prev, t0 = cur, t1
            if t1 - t_list > 60:
                cs, t_list = containers(), t1
                prev = {n: (prev.get(n) if n in prev else usage(p)) for n, p in cs.items()}


if __name__ == "__main__":
    main(sys.argv[1])
