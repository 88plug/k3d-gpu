"""Hybrid-split joulemeter: per-pod energy on Intel hybrid client CPUs.

Per window (default 200 ms) the meter takes, for every pod (cgroup):
  * cpu_core PMU counters: cycles, instructions, ref-cycles, cache-misses
  * cpu_atom PMU counters: the same four events, opened as a separate group
    (perf cannot group events across PMUs)
  * GPU busy time per engine class from /proc/<pid>/fdinfo (drm-engine-*, i915)
and system-wide: RAPL core / uncore / package energy deltas, the iGPU actual
frequency, and memory-controller bytes (uncore_imc_free_running).

It fits one non-negative least-squares model per RAPL domain over a sliding
window of recent rows:
  core     ~ sum_pods [P-cycles, P-instr, E-cycles, E-instr] x freq-bucket + idle
  uncore   ~ sum_pods engine-seconds x GPU GHz, per engine class      + idle
  pkg_rest ~ IMC bytes                                                + idle
  (pkg_rest = package - core - uncore)
A pod's energy in a domain is its own feature vector times the fitted
coefficients, rescaled so that pods + fitted idle equal the measured delta.
`model="summed"` replaces the four core columns with [cycles, instructions]
summed over both PMUs: the SmartWatts-style per-frequency regression, kept here
as the ablation baseline.

Only numpy and scipy are needed. Hardware access is limited to sysfs/procfs
readers (fixture-testable through `sysroot`) and the perf event table; opening
the cgroup perf groups is left to the DaemonSet runtime.
"""

from __future__ import annotations

import os
import re
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import nnls

DOMAINS = ("core", "uncore", "pkg_rest")
ENGINE_CLASSES = ("render", "video", "compute", "copy")
# i915 drm-engine-<name> keys -> engine class (unknown names count as copy)
ENGINE_ALIAS = {"render": "render", "video": "video", "video-enhance": "video",
                "compute": "compute", "copy": "copy"}


# ---------------------------------------------------------------- samples ---

@dataclass
class PodSample:
    """Counter deltas for one pod over one window."""
    p_cyc: float = 0.0
    p_ins: float = 0.0
    p_ref: float = 0.0
    p_miss: float = 0.0
    e_cyc: float = 0.0
    e_ins: float = 0.0
    e_ref: float = 0.0
    e_miss: float = 0.0
    cpu_s: float = 0.0                       # cgroup cpu.stat usage delta, s
    gpu_s: dict = field(default_factory=dict)  # engine class -> busy seconds


@dataclass
class SysSample:
    """System-wide deltas for one window."""
    dt: float
    core_j: float
    uncore_j: float
    pkg_j: float
    gpu_mhz: float = 0.0
    imc_bytes: float = 0.0
    tsc_hz: float = 1.0e9                    # ref-cycles tick rate


# --------------------------------------------------------------- features ---

def own_mhz(cyc: float, ref: float, tsc_hz: float) -> float:
    """Average running frequency of a counter group (APERF/MPERF style)."""
    return cyc / ref * tsc_hz / 1e6 if ref > 0 else 0.0


def bucket_of(mhz: float, edges) -> int:
    return int(np.searchsorted(edges, mhz, side="right"))


class CoreFeatures:
    """Core-domain feature map for one pod: counters x frequency bucket."""

    def __init__(self, model: str = "hybrid", bucket: str = "rail",
                 edges=(2000.0, 3000.0, 4000.0)):
        if model not in ("hybrid", "summed"):
            raise ValueError(model)
        if bucket not in ("own", "rail", "none"):
            raise ValueError(bucket)
        self.model, self.bucket = model, bucket
        self.edges = np.asarray(edges if bucket != "none" else (), float)
        self.nb = len(self.edges) + 1
        self.ncol = 4 if model == "hybrid" else 2
        self.size = self.nb * self.ncol

    @staticmethod
    def rail_mhz(pods, tsc_hz) -> float:
        """Highest running frequency of any pod's counters: the shared rail's
        voltage follows the most demanding core, so this keys the bucket."""
        best = 0.0
        for p in pods:
            best = max(best, own_mhz(p.p_cyc, p.p_ref, tsc_hz),
                       own_mhz(p.e_cyc, p.e_ref, tsc_hz))
        return best

    def __call__(self, p: PodSample, tsc_hz: float, rail: float):
        """Returns (features, mask_p) where mask_p marks P-core columns."""
        v = np.zeros(self.size)
        mask_p = np.zeros(self.size, bool)
        g = 1e-9  # work in Gcycles / Ginstr
        if self.model == "hybrid":
            groups = ((p.p_cyc, p.p_ins, p.p_ref, 0, True),
                      (p.e_cyc, p.e_ins, p.e_ref, 2, False))
        else:
            groups = ((p.p_cyc + p.e_cyc, p.p_ins + p.e_ins,
                       p.p_ref + p.e_ref, 0, None),)
        for cyc, ins, ref, off, is_p in groups:
            if cyc <= 0:
                continue
            if self.bucket == "none":
                b = 0
            elif self.bucket == "rail":
                b = bucket_of(rail, self.edges)
            else:
                b = bucket_of(own_mhz(cyc, ref, tsc_hz), self.edges)
            i = b * self.ncol + off
            v[i], v[i + 1] = cyc * g, ins * g
            if is_p:
                mask_p[i:i + 2] = True
        return v, mask_p


def uncore_features(p: PodSample, gpu_mhz: float):
    """Engine busy seconds x GPU GHz per engine class."""
    v = np.zeros(len(ENGINE_CLASSES))
    for eng, s in p.gpu_s.items():
        v[ENGINE_CLASSES.index(ENGINE_ALIAS.get(eng, "copy"))] += s * gpu_mhz / 1e3
    return v


def pkg_rest_pod_feature(p: PodSample, gpu_mhz: float = 0.0, pods_mode: bool = False):
    """GB from LLC misses; with pods_mode also GPU engine-seconds x GHz (GPU
    memory traffic, invisible to CPU counters) and CPU-busy seconds (ring/LLC
    clocks run while any core is busy)."""
    gb = (p.p_miss + p.e_miss) * 64 / 1e9
    if not pods_mode:
        return np.array([gb])
    return np.array([gb, sum(p.gpu_s.values()) * gpu_mhz / 1e3, p.cpu_s])


# -------------------------------------------------------------------- fit ---

class OnlineNNLS:
    """Sliding-window NNLS. With intercept=True a trailing idle column
    (watts x dt) is fitted too; beta[-1] is then idle watts."""

    def __init__(self, nfeat: int, window: int = 300, ridge: float = 1e-3,
                 min_rows: int = 25, refit_every: int = 5, intercept: bool = True):
        self.nfeat, self.window, self.ridge = nfeat, window, ridge
        self.intercept = intercept
        self.min_rows, self.refit_every = min_rows, refit_every
        self.X = deque(maxlen=window)
        self.y = deque(maxlen=window)
        self.beta = None
        self._n = 0

    def add(self, x_sys: np.ndarray, dt: float, joules: float):
        self.X.append(np.append(x_sys, dt) if self.intercept else np.asarray(x_sys, float))
        self.y.append(joules)
        self._n += 1
        if len(self.y) >= self.min_rows and (
                self.beta is None or self._n % self.refit_every == 0):
            self.beta = self.solve(np.array(self.X), np.array(self.y), self.ridge)

    @staticmethod
    def solve(X: np.ndarray, y: np.ndarray, ridge: float):
        scale = np.sqrt((X ** 2).mean(axis=0))
        scale[scale == 0] = 1.0
        Xs = X / scale
        n = Xs.shape[1]
        A = np.vstack([Xs, np.sqrt(ridge * len(y)) * np.eye(n) * (y.std() + 1e-12)])
        b = np.concatenate([y, np.zeros(n)])
        coef, _ = nnls(A, b, maxiter=50 * n)
        return coef / scale


def apportion(measured: float, idle: float, model_j: dict, fallback: dict):
    """Split measured joules: idle first, the rest pro rata to model_j
    (or to fallback when every model value is zero). Sum is exact."""
    idle = min(max(idle, 0.0), max(measured, 0.0))
    rest = measured - idle
    w = model_j if sum(model_j.values()) > 0 else fallback
    tot = sum(w.values())
    if tot <= 0 or rest <= 0:
        return {k: 0.0 for k in model_j}, measured
    return {k: rest * v / tot for k, v in w.items()}, idle


# ------------------------------------------------------------------ meter ---

class HybridMeter:
    """Online per-pod energy meter. step() returns, per window,
    ({pod: {label: joules}}, {domain: idle joules}) with labels
    core_p / core_e (hybrid) or core (summed), uncore_gpu, pkg_rest."""

    def __init__(self, model="hybrid", bucket="rail", edges=(2000.0, 3000.0, 4000.0),
                 window=300, ridge=1e-3, min_rows=25, refit_every=5,
                 idle_w=None, pkg_features="imc"):
        """idle_w: {domain: watts} from a quiet-host measurement; when given,
        idle is fixed instead of fitted as an intercept.
        pkg_features: "imc" fits pkg_rest on system IMC bytes and splits it by
        LLC misses; "pods" fits it on the sum of the pods' own features
        (misses, GPU engine time, CPU-busy time)."""
        if pkg_features not in ("imc", "pods"):
            raise ValueError(pkg_features)
        self.cf = CoreFeatures(model, bucket, edges)
        self.idle_w = idle_w
        self.pods_mode = pkg_features == "pods"
        kw = dict(window=window, ridge=ridge, min_rows=min_rows, refit_every=refit_every,
                  intercept=idle_w is None)
        self.fit = {"core": OnlineNNLS(self.cf.size, **kw),
                    "uncore": OnlineNNLS(len(ENGINE_CLASSES), **kw),
                    "pkg_rest": OnlineNNLS(3 if self.pods_mode else 1, **kw)}
        self.min_w = {d: np.inf for d in DOMAINS}   # warm-up idle estimate

    def step(self, pods: dict, s: SysSample):
        rail = CoreFeatures.rail_mhz(pods.values(), s.tsc_hz)
        feats = {n: self.cf(p, s.tsc_hz, rail) for n, p in pods.items()}
        unc = {n: uncore_features(p, s.gpu_mhz) for n, p in pods.items()}
        mis = {n: pkg_rest_pod_feature(p, s.gpu_mhz, self.pods_mode) for n, p in pods.items()}
        measured = {"core": s.core_j, "uncore": s.uncore_j,
                    "pkg_rest": s.pkg_j - s.core_j - s.uncore_j}

        sys_x = {"core": sum((f for f, _ in feats.values()), np.zeros(self.cf.size)),
                 "uncore": sum(unc.values(), np.zeros(len(ENGINE_CLASSES))),
                 "pkg_rest": (sum(mis.values()) if self.pods_mode
                              else np.array([s.imc_bytes / 1e9]))}
        for d in DOMAINS:
            fixed = self.idle_w[d] * s.dt if self.idle_w else 0.0
            self.fit[d].add(sys_x[d], s.dt, measured[d] - fixed)
            self.min_w[d] = min(self.min_w[d], measured[d] / s.dt)

        out = {n: {} for n in pods}
        idle_out = {}
        fallbacks = {
            "core": {n: p.p_cyc + p.e_cyc for n, p in pods.items()},
            "uncore": {n: sum(p.gpu_s.values()) for n, p in pods.items()},
            "pkg_rest": {n: p.p_miss + p.e_miss for n, p in pods.items()},
        }
        pod_x = {"core": {n: f for n, (f, _) in feats.items()}, "uncore": unc, "pkg_rest": mis}
        for d in DOMAINS:
            beta = self.fit[d].beta
            if beta is not None and self.idle_w is None:
                idle, beta = beta[-1] * s.dt, beta[:-1]
            elif self.idle_w:
                idle = self.idle_w[d] * s.dt
            else:
                idle = self.min_w[d] * s.dt
            mj = ({n: 0.0 for n in pods} if beta is None
                  else {n: float(pod_x[d][n] @ beta) for n in pods})
            share, idle_out[d] = apportion(measured[d], idle, mj, fallbacks[d])
            for n in pods:
                if d == "core" and self.cf.model == "hybrid":
                    f, mp = feats[n]
                    if beta is not None and mj[n] > 0:
                        frac_p = float(f[mp] @ beta[mp]) / mj[n]
                    else:
                        c = pods[n].p_cyc + pods[n].e_cyc
                        frac_p = pods[n].p_cyc / c if c > 0 else 0.0
                    out[n]["core_p"] = share[n] * frac_p
                    out[n]["core_e"] = share[n] * (1 - frac_p)
                else:
                    out[n]["uncore_gpu" if d == "uncore" else d] = share[n]
        return out, idle_out


# ------------------------------------------------------------- exposition ---

def exposition(totals: dict, idle: dict, node: str = "") -> str:
    """Prometheus text for cumulative joules. totals: {(ns, pod): {domain: J}}."""
    lines = ["# HELP k3d_gpu_pod_joules_total Pod energy by RAPL sub-domain "
             "(core_p, core_e, uncore_gpu, pkg_rest), joules.",
             "# TYPE k3d_gpu_pod_joules_total counter"]
    for (ns, pod), doms in sorted(totals.items()):
        for d, j in sorted(doms.items()):
            lines.append(f'k3d_gpu_pod_joules_total{{namespace="{ns}",pod="{pod}",'
                         f'domain="{d}",node="{node}"}} {j:.6f}')
    lines += ["# HELP k3d_gpu_node_idle_joules_total Fitted idle energy per domain, joules.",
              "# TYPE k3d_gpu_node_idle_joules_total counter"]
    for d, j in sorted(idle.items()):
        lines.append(f'k3d_gpu_node_idle_joules_total{{domain="{d}",node="{node}"}} {j:.6f}')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- sources ---

def _p(sysroot: str, path: str) -> str:
    return os.path.join(sysroot or "/", path.lstrip("/"))


def rapl_zones(sysroot: str = "/") -> dict:
    """{zone name: (energy_uj path, max_energy_range_uj)} for the first
    package (intel-rapl:0 and its sub-zones). Names as the kernel reports
    them: package-0, core, uncore, dram."""
    base = _p(sysroot, "/sys/class/powercap")
    zones = {}
    if not os.path.isdir(base):
        return zones
    for z in sorted(os.listdir(base)):
        if not re.fullmatch(r"intel-rapl:0(:\d+)?", z):
            continue
        d = os.path.join(base, z)
        try:
            with open(os.path.join(d, "name")) as f:
                name = f.read().strip()
            with open(os.path.join(d, "max_energy_range_uj")) as f:
                rng = int(f.read())
        except OSError:
            continue
        zones[name] = (os.path.join(d, "energy_uj"), rng)
    return zones


class EnergyCounter:
    """Monotonic joules from a wrapping energy_uj file."""

    def __init__(self, path: str, max_range_uj: int):
        self.path, self.range = path, max_range_uj
        self.last = None

    def delta_j(self) -> float:
        with open(self.path) as f:
            cur = int(f.read())
        if self.last is None:
            self.last = cur
            return 0.0
        d = cur - self.last
        if d < 0:
            d += self.range + 1
        self.last = cur
        return d / 1e6


def parse_fdinfo(text: str):
    """Returns (driver, client key, {engine: busy ns}) or None for non-DRM
    fds. Reads drm-engine-<e> (i915). xe reports drm-cycles-<e> instead,
    which is not handled yet: xe clients come back with no engines."""
    kv = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        kv[k.strip()] = v.strip()
    if "drm-driver" not in kv:
        return None
    key = (kv.get("drm-pdev", ""), kv.get("drm-client-id", ""))
    eng = {}
    for k, v in kv.items():
        m = re.fullmatch(r"drm-engine-(?!capacity-)(\S+)", k)
        if m:
            eng[m.group(1)] = int(v.split()[0])
    return kv["drm-driver"], key, eng


def pid_cgroup(sysroot: str, pid: str) -> str:
    try:
        with open(_p(sysroot, f"/proc/{pid}/cgroup")) as f:
            for line in f:
                if line.startswith("0::"):
                    return line[3:].strip()
    except OSError:
        pass
    return ""


def drm_clients(sysroot: str = "/") -> dict:
    """{cgroup path: {engine: ns}} summed over DRM clients, each client
    counted once even when several fds or processes share it."""
    seen, out = set(), {}
    proc = _p(sysroot, "/proc")
    for pid in sorted(os.listdir(proc)) if os.path.isdir(proc) else ():
        if not pid.isdigit():
            continue
        fddir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fddir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fddir, fd))
            except OSError:
                continue
            if not target.startswith("/dev/dri/"):
                continue
            try:
                with open(os.path.join(proc, pid, "fdinfo", fd)) as f:
                    parsed = parse_fdinfo(f.read())
            except OSError:
                continue
            if not parsed:
                continue
            drv, key, eng = parsed
            if key in seen and key != ("", ""):
                continue
            seen.add(key)
            cg = out.setdefault(pid_cgroup(sysroot, pid), {})
            for e, ns in eng.items():
                cg[e] = cg.get(e, 0) + ns
    return out


class MonotonicEngines:
    """drm-engine values may step backwards; hold the previous maximum until
    the counter catches up (kernel drm-usage-stats rule)."""

    def __init__(self):
        self.last = {}

    def delta_s(self, key, ns: int) -> float:
        prev = self.last.get(key)
        if prev is None:
            self.last[key] = ns
            return 0.0
        if ns <= prev:
            return 0.0
        self.last[key] = ns
        return (ns - prev) / 1e9


def igpu_act_mhz(sysroot: str = "/") -> float:
    """Actual frequency of the first i915 GT (card*/gt_act_freq_mhz)."""
    base = _p(sysroot, "/sys/class/drm")
    for c in sorted(os.listdir(base)) if os.path.isdir(base) else ():
        f = os.path.join(base, c, "gt_act_freq_mhz")
        if re.fullmatch(r"card\d+", c) and os.path.exists(f):
            with open(f) as fh:
                return float(fh.read())
    return 0.0


PERF_TYPE_HARDWARE = 0
PERF_PMU_TYPE_SHIFT = 32
HW_EVENTS = {"cycles": 0, "instructions": 1, "cache-misses": 3, "ref-cycles": 9}


def hybrid_perf_events(sysroot: str = "/") -> dict:
    """{pmu: [(event, perf_event_attr.type, .config)]} for cpu_core and
    cpu_atom. On hybrid parts a generic hardware event targets one PMU by
    putting that PMU's type in config bits 32-63 (Linux >= 5.13). One group
    per PMU per cgroup; groups never span PMUs."""
    out = {}
    for pmu in ("cpu_core", "cpu_atom"):
        f = _p(sysroot, f"/sys/bus/event_source/devices/{pmu}/type")
        if not os.path.exists(f):
            continue
        with open(f) as fh:
            t = int(fh.read())
        out[pmu] = [(ev, PERF_TYPE_HARDWARE, (t << PERF_PMU_TYPE_SHIFT) | cfg)
                    for ev, cfg in HW_EVENTS.items()]
    return out
