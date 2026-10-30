#!/usr/bin/env python3
"""serving.py OUT.jsonl: the serving covariate of a crescent hold (EXPERIMENTS.md "H50-H52
pre-registration", shared rule 3). Every 10 s: load1, the CPU-s/s of every running container
this campaign did not create (cgroup cpu.stat), and the aiperf processes in aiperf010 other
than its idle `sleep infinity`. Read-only; stops on SIGTERM.

Slot mode (H56, SERVING_SLOT=i with SERVING_CPUS = the slice): only slot i's containers
are this hold's, so a sibling hold's cluster and load count as foreign; each row also
carries own_busy_pct / other_busy_pct (/proc/stat over the slice and the rest of the
online CPUs) and own_mhz (mean scaling_cur_freq over the slice).

VM mode (H62, SERVING_VMS = comma-separated container names): those containers run this
campaign's KVM guests, so each row records their CPU-s/s as vm_cpu and leaves them out of cpu
and serving_cpu. Their names carry no k3dgpu prefix, so another row's hold counts them as
foreign serving load."""
import json
import os
import signal
import subprocess
import sys
import time

MINE = ("k3dgpu", "k3d-gpu-bench-load")   # name substrings of this campaign's containers
STEP = float(os.environ.get("SERVING_STEP", "10"))   # sample period, s (tests shorten it)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "load"))
import load  # noqa: E402


def mine_for(slot):
    """Name substrings of one hold's containers: the legacy MINE without a slot, else
    slot `slot`'s clusters (k3d node, serverlb and tools names contain the cluster
    name) plus its own litellm/rapl and slot-tagged load containers."""
    if slot is None:
        return MINE
    return (f"k3dgpu-a-s{slot}-", f"k3dgpu-e-s{slot}-", f"k3d-gpu-bench-load-s{slot}-")


def is_mine(name, mine=None):
    return any(m in name for m in (MINE if mine is None else mine))


def _slot():
    v = os.environ.get("SERVING_SLOT", "")
    return int(v) if v != "" else None


SLOT = _slot()


def vms_from(value):
    """SERVING_VMS: the container names of this campaign's guests (H62), or ()."""
    return tuple(v.strip() for v in value.split(",") if v.strip())


def split_vms(cpu, vms):
    """(serving cpu, vm cpu): the readings of the containers named in vms move to the second."""
    return ({n: v for n, v in cpu.items() if n not in vms}, {n: v for n, v in cpu.items() if n in vms})


VMS = vms_from(os.environ.get("SERVING_VMS", ""))
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
        if not is_mine(name, mine_for(SLOT)):
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


def online(sysfs="/sys"):
    return load.parse_cpulist(load._read(f"{sysfs}/devices/system/cpu/online") or "0")


def mhz(cpus, sysfs="/sys"):
    v = [int(x) for x in (load._read(f"{sysfs}/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq") for c in cpus)
         if x.isdigit()]
    return round(sum(v) / len(v) / 1000, 1) if v else None


def busy(s0, s1, cpus):
    b0, t0 = load.cpu_times(s0, cpus)
    b1, t1 = load.cpu_times(s1, cpus)
    return round(100 * (b1 - b0) / (t1 - t0), 2) if t1 > t0 else None


def slot_fields(s0, s1, own, sysfs="/sys"):
    other = sorted(set(online(sysfs)) - set(own))
    return {"own_busy_pct": busy(s0, s1, own), "other_busy_pct": busy(s0, s1, other), "own_mhz": mhz(own, sysfs)}


def main(out):
    own = load.parse_cpulist(os.environ.get("SERVING_CPUS", "")) if SLOT is not None else []
    st = load._read("/proc/stat")
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
            cpu, vm_cpu = split_vms(cpu, VMS)
            row = {"ts": round(t1, 3), "seconds": round(t1 - t0, 3), "load1": load1, "cpu": cpu,
                   "serving_cpu": round(sum(cpu.values()), 3), "aiperf_procs": aiperf()}
            if VMS:
                row["vm_cpu"] = vm_cpu
            if SLOT is not None:
                st1 = load._read("/proc/stat")
                row.update(slot=SLOT, **slot_fields(st, st1, own))
                st = st1
            f.write(json.dumps(row) + "\n")
            f.flush()
            prev, t0 = cur, t1
            if t1 - t_list > 60:
                cs, t_list = containers(), t1
                prev = {n: (prev.get(n) if n in prev else usage(p)) for n, p in cs.items()}


if __name__ == "__main__":
    main(sys.argv[1])
