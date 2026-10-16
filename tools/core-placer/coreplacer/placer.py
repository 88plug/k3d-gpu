"""Node-agent control loop: counter windows in, pod balloon labels out.

Pods labelled k3d-gpu.io/cores=auto start in the shared balloon, where the
kernel runs them on both core types and the concurrent probe (estimate.py)
measures their speedup. Each window the placer re-plans which pods to fence on
E-cores (plan.py) for the P-cores the HFI tracker currently reports usable. An
E-fenced pod keeps a reference rate; a >25% drift sends it back to shared to be
re-measured (the NUMA-balancer pattern applied to core type).

The placer sees only counter windows and the P capacity; it never reads the
workload model that produced them.
"""
from . import plan
from .estimate import DriftDetector, Estimator

SHARED = "shared"
EFFICIENT = "efficient"


class Placer:
    def __init__(self, margin=0.03, min_samples=2, use_speedup=True, drift=True):
        self.margin = margin             # relative model gain needed to move any pod
        self.min_samples = min_samples
        self.use_speedup = use_speedup   # ablation: False plans with one constant S
        self.drift_enabled = drift       # ablation: False never re-probes
        self.est = Estimator()
        self.drift = DriftDetector()
        self.labels = {}

    def admit(self, pod):
        self.labels[pod] = SHARED

    def depart(self, pod):
        self.labels.pop(pod, None)
        self.est.forget(pod)
        self.drift.disarm(pod)

    def step(self, windows, p_capacity):
        """windows: {pod: Window} for pods that ran the whole window; p_capacity: usable
        P CPUs. Returns {pod: label} for every pod whose label changes."""
        released = set()
        for pod, w in windows.items():
            if pod not in self.labels:
                self.admit(pod)
            e = self.est.observe(pod, w)
            if self.labels[pod] != EFFICIENT or not self.drift_enabled:
                continue
            if pod not in self.drift.ref:
                if e.rate > 0:
                    self.drift.arm(pod, e.rate)
            elif self.drift.update(pod, e.rate):
                released.add(pod)
        pods, forced = {}, set(released)
        for p in self.labels:
            e = self.est.est.get(p)
            known = e is not None and (e.samples >= self.min_samples or self.labels[p] == EFFICIENT)
            if not known:
                forced.add(p)
            s = (e.speedup if self.use_speedup else 1.5) if known else 1.5
            pods[p] = (e.demand if e else 0.0, max(s, 1.0 + 1e-6))
        shared = plan.best_split(pods, p_capacity, forced)
        current = {p for p, lab in self.labels.items() if lab == SHARED} | released
        if plan.value(pods, shared, p_capacity) < (1 + self.margin) * plan.value(pods, current, p_capacity):
            shared = current
        changes = {}
        for p in self.labels:
            want = SHARED if p in shared else EFFICIENT
            if want != self.labels[p]:
                self.labels[p] = changes[p] = want
                self.drift.disarm(p)
                if p in released:
                    self.est.reset_speedup(p)
        return changes
