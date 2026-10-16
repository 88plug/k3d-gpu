"""Discrete-time model of a hybrid client CPU running pods in NRI balloons.

The model owns the truth (per-pod phases: CPU demand, P rate, E rate). Policies
see only labels they set; the auto placer additionally sees counter windows
generated from the truth with noise, through `Placer.step`. Static policies are
evaluated in one vectorised batch (axis 0 = label configuration), so the
exhaustive-static oracle tries every labelling with the same truth.

Host defaults approximate an i9-13900T: 8 P cores, 16 E cores, 35 W PL1.
SMT on P cores is not modelled (8 P slots).
"""
import itertools
import math
from dataclasses import dataclass, field

import numpy as np

from .estimate import Window
from .placer import EFFICIENT, Placer

LABELS = ("shared", "performance", "efficient")
SH, PF, EF = 0, 1, 2


@dataclass
class Host:
    n_p: int = 8
    n_e: int = 16
    pl1: float = 35.0
    uncore_w: float = 6.0
    p_core_w: float = 3.5
    e_core_w: float = 1.0
    cap_alpha: float = 0.6     # rate scales as (PL1/power)**alpha above PL1
    noise: float = 0.10        # lognormal sigma of per-second counter rates


@dataclass
class Workload:
    """Per-pod truth sampled at 1 s: demand[t, i], rate_p[t, i], rate_e[t, i], alive[t, i]."""
    demand: np.ndarray
    rate_p: np.ndarray
    rate_e: np.ndarray
    alive: np.ndarray
    cls: np.ndarray             # class id per pod (static labelling is per class)
    n_cls: int
    names: list = field(default_factory=list)


def _phased(T, phases, offset):
    """phases: list of (duration_s, demand, speedup[, e_rate]). Returns arrays over T."""
    period = sum(p[0] for p in phases)
    d, s, r = np.empty(T), np.empty(T), np.empty(T)
    for t in range(T):
        x = (t + offset) % period
        for ph in phases:
            if x < ph[0]:
                d[t], s[t], r[t] = ph[1], ph[2], ph[3] if len(ph) > 3 else 1.0
                break
            x -= ph[0]
    return d, s, r


def make_mix(name, T, rng):
    """Pre-registered mixes (acceptance.json). Rates are work per core-second:
    rate_e = e_rate (default 1), rate_p = speedup * rate_e."""
    pods = []   # (class, phases, birth, death)
    if name == "M1_agent_light":
        for c, (dem, sp) in enumerate([(0.3, 1.8), (0.3, 1.6), (0.5, 2.0), (0.2, 1.4), (0.4, 1.7), (0.6, 1.5)]):
            pods.append((c, [(1, dem, sp)], 0, T))
    elif name == "M2_oversub_stationary":
        for c, (dem, sp) in enumerate([(4, 2.2), (4, 1.2), (3, 1.9), (3, 1.1), (2, 1.6), (2, 1.3)]):
            pods.append((c, [(1, dem, sp)], 0, T))
    elif name == "M3_phased":
        for c, per in enumerate([120, 180, 240, 300, 150, 200]):
            # compute phase vs memory-bound phase (low IPC on both core types)
            pods.append((c, [(per // 2, 3, 2.2), (per - per // 2, 3, 1.1, 0.6)], 0, T))
    elif name == "M4_churn":
        classes = [(4, 2.2), (4, 1.2), (3, 1.9), (3, 1.1), (2, 1.6), (1, 1.4)]
        t = 0.0
        while t < T:
            t += rng.exponential(T / 40)
            if t >= T:
                break
            c = int(rng.integers(len(classes)))
            life = int(rng.uniform(60, 600))
            pods.append((c, [(1, *classes[c])], int(t), min(T, int(t) + life)))
    elif name == "M5_mixed_agent":
        spec = [[(1, 4, 1.9)], [(1, 4, 1.9)], [(1, 3, 1.15)], [(1, 3, 1.15)],
                [(20, 0.1, 1.6), (10, 2.0, 1.6)], [(20, 0.1, 1.6), (10, 2.0, 1.6)]]
        for c, ph in enumerate(spec):
            pods.append((c, ph, 0, T))
    else:
        raise ValueError(name)
    n = len(pods)
    dem, rp, re_, alive = (np.zeros((T, n)) for _ in range(4))
    cls = np.zeros(n, dtype=int)
    for i, (c, phases, b, e) in enumerate(pods):
        off = int(rng.integers(0, sum(p[0] for p in phases)))
        d, s, r = _phased(T, phases, off)
        dem[:, i], rp[:, i], re_[:, i] = d, s * r, r
        alive[b:e, i] = 1.0
        cls[i] = c
    return Workload(dem * alive, rp, re_, alive.astype(bool), cls, int(cls.max()) + 1, [name])


def _balloon(d, labels, sizes, lab, free_own, free_other):
    """Exclusive balloons of one type: sized CPUs from the preferred type, spilling
    onto the other type when the preferred one runs out."""
    m = (labels == lab) & (d > 0)
    w = np.where(m, sizes, 0.0)
    W = w.sum(1)
    own = w * np.where(W > 0, np.minimum(1.0, free_own / np.maximum(W, 1e-9)), 0.0)[:, None]
    spill = w - own
    S = spill.sum(1)
    spill = spill * np.where(S > 0, np.minimum(1.0, free_other / np.maximum(S, 1e-9)), 0.0)[:, None]
    got_own = np.minimum(d, own) * m
    got_other = np.minimum(d - got_own, spill) * m
    return got_own, got_other, free_own - own.sum(1), free_other - spill.sum(1)


def _allocate(host, d, labels, sizes):
    """d: (n,) demand; labels, sizes: (B, n). Returns P-time and E-time per pod, (B, n)."""
    d = np.broadcast_to(d, labels.shape)
    B = labels.shape[0]
    free_p, free_e = np.full(B, float(host.n_p)), np.full(B, float(host.n_e))
    pp, pe, free_p, free_e = _balloon(d, labels, sizes, PF, free_p, free_e)
    ee, ep, free_e, free_p = _balloon(d, labels, sizes, EF, free_e, free_p)
    pt, et = pp + ep, pe + ee
    free_p, free_e = np.maximum(free_p, 0), np.maximum(free_e, 0)
    ds = np.where(labels == SH, d, 0.0)
    D = ds.sum(1)
    gp = ds * np.where(D > 0, np.minimum(1.0, free_p / np.maximum(D, 1e-9)), 0.0)[:, None]  # idle P first
    rest = ds - gp
    R = rest.sum(1)
    pt = pt + gp
    et = et + rest * np.where(R > 0, np.minimum(1.0, free_e / np.maximum(R, 1e-9)), 0.0)[:, None]
    return pt, et


def _power_factor(host, pt, et):
    power = host.uncore_w + host.p_core_w * pt.sum(1) + host.e_core_w * et.sum(1)
    f = np.where(power > host.pl1, (host.pl1 / power) ** host.cap_alpha, 1.0)
    return f, np.minimum(power, host.pl1)


def run_static(host, wl, class_labels, class_sizes=None):
    """class_labels: (B, n_cls) ints. Returns (weighted_throughput[B], energy_J[B])."""
    T, n = wl.demand.shape
    labels = class_labels[:, wl.cls]
    if class_sizes is None:
        sizes = np.array([math.ceil(m - 1e-9) for m in _mean_demand(wl)], dtype=float)[None, :] * np.ones_like(labels, dtype=float)
    else:
        sizes = class_sizes[:, wl.cls]
    prog = np.zeros(labels.shape)
    energy = np.zeros(labels.shape[0])
    for t in range(T):
        pt, et = _allocate(host, wl.demand[t], labels, sizes)
        f, pw = _power_factor(host, pt, et)
        prog += (pt * wl.rate_p[t] + et * wl.rate_e[t]) * f[:, None]
        energy += pw
    return _score(wl, prog), energy


def _mean_demand(wl):
    alive = wl.alive.sum(0)
    return np.where(alive > 0, wl.demand.sum(0) / np.maximum(alive, 1), 0)


def _score(wl, prog):
    ref = (wl.demand * wl.rate_p).sum(0)
    ok = ref > 0
    return (prog[:, ok] / ref[ok]).mean(1)


def run_auto(host, wl, rng, window_s=3, move_cost_s=5, placer=None, shuffle=False, p_capacity=None):
    """Closed loop: counters -> Placer -> labels. Returns (throughput, energy, n_moves)."""
    T, n = wl.demand.shape
    placer = placer or Placer()
    labels = np.zeros((1, n), dtype=int)
    sizes = np.zeros((1, n))
    blocked = np.zeros(n)                     # seconds left of a restart
    gap = np.zeros(n, dtype=bool)             # pod had no container during this window
    prog = np.zeros((1, n))
    energy, moves = 0.0, 0
    acc = np.zeros((4, n))                    # inst_p, time_p, inst_e, time_e
    cap = p_capacity if p_capacity is not None else host.n_p
    for t in range(T):
        alive = wl.alive[t] & (blocked <= 0)
        gap |= ~alive
        lab = np.where(alive, labels[0], SH)[None, :]
        pt, et = _allocate(host, np.where(alive, wl.demand[t], 0.0), lab, sizes)
        f, pw = _power_factor(host, pt, et)
        noise_p = rng.lognormal(0, host.noise, n)
        noise_e = rng.lognormal(0, host.noise, n)
        prog += (pt * wl.rate_p[t] + et * wl.rate_e[t]) * f[:, None]
        energy += pw[0]
        acc += np.stack([pt[0] * wl.rate_p[t] * f[0] * noise_p, pt[0],
                         et[0] * wl.rate_e[t] * f[0] * noise_e, et[0]])
        blocked -= 1
        if (t + 1) % window_s:
            continue
        # A restarting or not-yet-born pod has no cgroup for the whole window: no window.
        idx = [i for i in range(n) if wl.alive[t, i] and not gap[i]]
        for i in range(n):
            if not wl.alive[t, i] and i in placer.labels:
                placer.depart(i)
        src = list(idx)
        if shuffle:
            src = list(rng.permutation(idx))
        windows = {i: Window(window_s, *acc[:, j]) for i, j in zip(idx, src)}
        for i, lab_name in placer.step(windows, cap).items():
            labels[0, i] = EF if lab_name == EFFICIENT else SH
            sizes[0, i] = math.ceil(max(placer.est.est[i].demand, 1e-9) - 1e-9)
            blocked[i] = move_cost_s
            moves += 1
        acc[:] = 0
        gap[:] = False
    return _score(wl, prog)[0], energy, moves


def all_label_configs(n_cls, choices=(SH, PF, EF)):
    return np.array(list(itertools.product(choices, repeat=n_cls)), dtype=int)


def evaluate_mix(host, name, seed, T=1800, window_s=3, move_cost_s=5, speed_scale=1.0):
    """speed_scale stretches every pod's P/E speedup: S -> 1 + (S - 1) * speed_scale."""
    rng = np.random.default_rng(seed)
    wl = make_mix(name, T, rng)
    wl.rate_p = wl.rate_e * (1 + (wl.rate_p / wl.rate_e - 1) * speed_scale)
    k = wl.n_cls
    configs = all_label_configs(k)
    thr, en = run_static(host, wl, configs)
    unp = int(np.flatnonzero((configs == SH).all(1))[0])
    allp = int(np.flatnonzero((configs == PF).all(1))[0])
    best = int(np.argmax(thr))
    auto = run_auto(host, wl, np.random.default_rng(seed + 1000), window_s, move_cost_s)
    out = {"unpinned": (thr[unp], en[unp]), "all_p": (thr[allp], en[allp]),
           "exhaustive_static": (thr[best], en[best]), "auto": auto[:2], "moves": auto[2],
           "best_static_labels": [LABELS[c] for c in configs[best]]}
    out["_wl"] = wl
    return out
