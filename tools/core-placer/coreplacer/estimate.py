"""Per-pod P/E speedup from one window of per-CPU cgroup counters.

A pod that sits in the shared balloon is migrated by the kernel across P and E
CPUs. Splitting its per-CPU counters by core type gives instructions and
on-CPU time on each type over the same window, so both rates come from the
same program phase (concurrent probe). Nothing is moved to measure it.
"""
import math
from dataclasses import dataclass


@dataclass
class Window:
    """Counter deltas for one pod over one window, already summed per core type."""
    wall_s: float
    inst_p: float = 0.0
    time_p: float = 0.0   # on-CPU seconds on P CPUs (task-clock)
    inst_e: float = 0.0
    time_e: float = 0.0


@dataclass
class Estimate:
    speedup: float = 1.0      # rate on P / rate on E
    demand: float = 0.0       # CPUs actually consumed
    rate: float = 0.0         # instructions per on-CPU second on the current placement
    samples: int = 0


def window_from_percpu(wall_s, per_cpu, p_cpus):
    """per_cpu: {cpu: (instructions, task_clock_seconds)}."""
    w = Window(wall_s)
    for cpu, (inst, t) in per_cpu.items():
        if cpu in p_cpus:
            w.inst_p += inst
            w.time_p += t
        else:
            w.inst_e += inst
            w.time_e += t
    return w


class Estimator:
    """EWMA of log-speedup over windows that saw enough time on both core types."""

    def __init__(self, min_time_s=0.3, alpha=0.4):
        self.min_time_s = min_time_s
        self.alpha = alpha
        self.est = {}

    def observe(self, pod, w):
        e = self.est.setdefault(pod, Estimate())
        busy = w.time_p + w.time_e
        e.demand = busy / w.wall_s if w.wall_s > 0 else 0.0
        e.rate = (w.inst_p + w.inst_e) / busy if busy > 0 else 0.0
        if w.time_p >= self.min_time_s and w.time_e >= self.min_time_s and w.inst_e > 0:
            s = (w.inst_p / w.time_p) / (w.inst_e / w.time_e)
            if s > 0:
                if e.samples == 0:
                    e.speedup = s
                else:
                    e.speedup = math.exp((1 - self.alpha) * math.log(e.speedup) + self.alpha * math.log(s))
                e.samples += 1
        return e

    def forget(self, pod):
        self.est.pop(pod, None)

    def reset_speedup(self, pod):
        if pod in self.est:
            self.est[pod].samples = 0


class DriftDetector:
    """Flags a confined pod whose rate moves more than `threshold` from its reference
    for `windows` consecutive windows (re-probe trigger)."""

    def __init__(self, threshold=0.25, windows=2):
        self.threshold = threshold
        self.windows = windows
        self.ref = {}
        self.count = {}

    def arm(self, pod, rate):
        self.ref[pod] = rate
        self.count[pod] = 0

    def disarm(self, pod):
        self.ref.pop(pod, None)
        self.count.pop(pod, None)

    def update(self, pod, rate):
        ref = self.ref.get(pod)
        if not ref:
            return False
        if abs(rate - ref) / ref > self.threshold:
            self.count[pod] += 1
        else:
            self.count[pod] = 0
        return self.count[pod] >= self.windows
