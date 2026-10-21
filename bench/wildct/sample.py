#!/usr/bin/env python3
"""sample.py OUT.jsonl: the covariate sampler of a wildct hold (EXPERIMENTS.md "Wildct
pre-registration", rule W5). Every 5 s, read-only: load1; busy CPU-s/s of the P-cores
(CPUs 0-15), the kept-free E-cores (16-19) and the load E-cores (20-23) from /proc/stat; the
CPU-s/s of this campaign's containers (names starting k3d-k3dgpu-w-, k3dgpu-w- or
k3d-gpu-bench-load) and of every other running container, from their cgroup cpu.stat;
"foreign" CPU-s/s = all busy CPU-s/s minus this campaign's; MemAvailable; package temperature
(x86_pkg_temp); / available GB. Stops on SIGTERM. Python 3 standard library only."""
import json
import os
import signal
import subprocess
import sys
import time

STEP = 5.0
SETS = {"p": range(0, 16), "e_free": range(16, 20), "e_load": range(20, 24)}
MINE = ("k3d-k3dgpu-w-", "k3dgpu-w-", "k3d-gpu-bench-load")
stop = False


def _term(*_):
    global stop
    stop = True


def cpu_times(text):
    """{cpu: (busy_jiffies, total_jiffies)} from /proc/stat text (per-CPU lines only)."""
    out = {}
    for line in text.splitlines():
        f = line.split()
        if not f or not f[0].startswith("cpu") or f[0] == "cpu":
            continue
        v = [int(x) for x in f[1:]]
        idle = v[3] + (v[4] if len(v) > 4 else 0)
        out[int(f[0][3:])] = (sum(v[:8]) - idle, sum(v[:8]))
    return out


def busy(prev, cur, cpus, hz=100.0, dt=STEP):
    """Busy CPU-s/s of a CPU set between two cpu_times readings."""
    j = sum(cur[c][0] - prev[c][0] for c in cpus if c in cur and c in prev)
    return round(j / hz / dt, 3)


def containers():
    p = subprocess.run(["docker", "ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}"],
                       capture_output=True, text=True)
    rows = {}
    for line in p.stdout.splitlines():
        cid, name = line.split(" ", 1)
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


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def pkg_temp():
    for z in sorted(os.listdir("/sys/class/thermal")):
        if read(f"/sys/class/thermal/{z}/type").strip() == "x86_pkg_temp":
            t = read(f"/sys/class/thermal/{z}/temp").strip()
            return int(t) / 1000 if t else None
    return None


def mem_available_gib(text):
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            return round(int(line.split()[1]) / 1048576, 2)
    return None


def main(out):
    signal.signal(signal.SIGTERM, _term)
    hz = float(os.sysconf("SC_CLK_TCK"))
    cs, t_list = containers(), time.time()
    prev_c, prev_s, t0 = {n: usage(p) for n, p in cs.items()}, cpu_times(read("/proc/stat")), time.time()
    with open(out, "a") as f:
        while not stop:
            time.sleep(STEP)
            t1 = time.time()
            dt = t1 - t0
            cur_s = cpu_times(read("/proc/stat"))
            cur_c = {n: usage(p) for n, p in cs.items()}
            cpu = {n: round((cur_c[n] - prev_c[n]) / 1e6 / dt, 3)
                   for n in cs if cur_c.get(n) is not None and prev_c.get(n) is not None}
            mine = round(sum(v for n, v in cpu.items() if n.startswith(MINE)), 3)
            total = busy(prev_s, cur_s, cur_s.keys(), hz, dt)
            st = os.statvfs("/")
            row = {"ts": round(t1, 3), "seconds": round(dt, 3), "load1": float(read("/proc/loadavg").split()[0]),
                   **{f"busy_{k}": busy(prev_s, cur_s, v, hz, dt) for k, v in SETS.items()},
                   "busy_all": total, "mine_cpu": mine, "foreign_cpu": round(total - mine, 3),
                   "other_containers_cpu": round(sum(v for n, v in cpu.items() if not n.startswith(MINE)), 3),
                   "mem_available_gib": mem_available_gib(read("/proc/meminfo")), "pkg_temp_c": pkg_temp(),
                   "root_avail_gb": round(st.f_bavail * st.f_frsize / 1e9, 1)}
            f.write(json.dumps(row) + "\n")
            f.flush()
            prev_s, prev_c, t0 = cur_s, cur_c, t1
            if t1 - t_list > 30:
                cs, t_list = containers(), t1
                prev_c = {n: (prev_c.get(n) if n in prev_c else usage(p)) for n, p in cs.items()}


if __name__ == "__main__":
    main(sys.argv[1])
