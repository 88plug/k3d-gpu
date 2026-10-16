#!/usr/bin/env python3
"""Live probe: concurrent P/E speedup of one cgroup over one window.

Usage: probe_cgroup.py /sys/fs/cgroup/<pod cgroup> [window_s]
Opens, on every online CPU, instructions on that CPU's hybrid PMU and
task-clock, both scoped to the cgroup (PERF_FLAG_PID_CGROUP). Needs
CAP_PERFMON or perf_event_paranoid <= 0. Prints the window and the estimate.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coreplacer import perf  # noqa: E402
from coreplacer.estimate import Estimator, window_from_percpu  # noqa: E402


def main():
    cg = sys.argv[1]
    window = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    p_cpus = perf.parse_cpulist(open("/sys/devices/cpu_core/cpus").read())
    e_cpus = perf.parse_cpulist(open("/sys/devices/cpu_atom/cpus").read())
    types = {"core": perf.read_pmu_type("/", "cpu_core"), "atom": perf.read_pmu_type("/", "cpu_atom")}
    fds = {}
    for cpu in sorted(p_cpus | e_cpus):
        pmu = types["core"] if cpu in p_cpus else types["atom"]
        fds[cpu] = (perf.open_cgroup_counter(cg, cpu, *perf.hybrid_instructions(pmu)),
                    perf.open_cgroup_counter(cg, cpu, *perf.task_clock()))
    before = {c: (perf.read_counter(a), perf.read_counter(b)) for c, (a, b) in fds.items()}
    time.sleep(window)
    after = {c: (perf.read_counter(a), perf.read_counter(b)) for c, (a, b) in fds.items()}
    for a, b in fds.values():
        os.close(a)
        os.close(b)
    per_cpu = {c: (after[c][0] - before[c][0], (after[c][1] - before[c][1]) / 1e9) for c in fds}
    w = window_from_percpu(window, per_cpu, p_cpus)
    e = Estimator().observe(cg, w)
    print(json.dumps({"window": w.__dict__, "speedup": e.speedup if e.samples else None, "demand": e.demand}))


if __name__ == "__main__":
    main()
