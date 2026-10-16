#!/usr/bin/env python3
"""residency_model.py — simulator for an energy-aware residency advisor and tail-ledger router.

Research prototype, stdlib only, no hardware access. See docs/residency-advisor.md for the
pre-registered acceptance criteria (A1..A6) and the results.

  python3 bench/residency_model.py report          # all experiments, JSON
  python3 bench/residency_model.py deploy --image "$IMAGE" --plan plan.json

Pieces:
  * Device + integrate_timeline(): ground-truth energy of one device from its busy intervals
    (floor, active excess, post-call power tail, wake). Routers never call this.
  * TailLedger: causal per-call energy charges. A call that starts inside the previous call's
    tail is charged its full tail and the unused part of the previous tail is refunded, so the
    charges telescope to the integrated timeline (no double counting).
  * route_sim(): closed-loop agents, per-call routing between devices (claim 3).
  * residency_energy(): iGPU- vs dGPU-resident platform J/task, plus closed-form T* (claim 2).
  * advise()/claim1_report(): what-if residency advisor vs tuned reactive keep_alive/LRU
    policies, fitted and scored on disjoint traces (claim 1).
  * tail_meter_sim(): can a 100 ms nvidia-smi poll resolve the tail energy at all (A6).
  * deployments(): residency plan -> pinned Deployments (JSON, valid YAML for kubectl).
"""
import argparse
import json
import math
import random
import sys
from dataclasses import dataclass, field, replace

# ---------------------------------------------------------------------------------------------
# Devices and ground-truth energy
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Device:
    name: str
    kind: str                 # dgpu | igpu
    p_floor: float            # W at idle with a model resident (P8 for the dGPUs)
    p_active: float           # W above floor while prefill/decode runs
    tail_w: float             # W above floor after work ends ...
    tail_s: float             # ... for this long, unless new work starts
    decode_tps: float         # tokens/s for the small (1.5-3B) model
    prefill_tps: float
    wake_s: float = 0.0       # extra latency when the device has idled longer than deep_after_s
    wake_j: float = 0.0
    deep_after_s: float = math.inf
    p_floor_empty: float = None   # W when no model is resident; None = same as p_floor (desktop)
    measured: bool = False    # True only for numbers measured on hardware

    def floor(self, resident):
        if resident or self.p_floor_empty is None:
            return self.p_floor
        return self.p_floor_empty

    def work_s(self, prompt_tok, out_tok):
        return prompt_tok / self.prefill_tps + out_tok / self.decode_tps


def integrate_timeline(dev, busy, t_end, resident=True):
    """Ground-truth energy (J) of one device over [0, t_end].

    busy: sorted, non-overlapping [(start, end)] work intervals. Each interval is followed by a
    tail at dev.tail_w for dev.tail_s, cut short by the next interval or by t_end. A wake is paid
    when the idle time before an interval exceeds dev.deep_after_s.
    """
    e = dev.floor(resident) * t_end
    prev_end = 0.0
    for i, (s, en) in enumerate(busy):
        if s - prev_end > dev.deep_after_s:
            e += dev.wake_j
        e += dev.p_active * (en - s)
        nxt = busy[i + 1][0] if i + 1 < len(busy) else t_end
        e += dev.tail_w * max(0.0, min(dev.tail_s, nxt - en))
        prev_end = en
    return e


class TailLedger:
    """Causal per-call charges whose sum equals integrate_timeline() exactly.

    charge() needs only the call's own start/end and the device's previous call, never the future.
    """

    def __init__(self, dev):
        self.dev = dev
        self.prev_end = 0.0
        self.charges = []

    def remaining_tail(self, start):
        if not self.charges:
            return 0.0
        return max(0.0, min(self.dev.tail_s, self.prev_end + self.dev.tail_s - start))

    def is_deep(self, start):
        return start - self.prev_end > self.dev.deep_after_s

    def charge(self, start, end):
        d = self.dev
        c = d.p_active * (end - start) + d.tail_w * d.tail_s
        c -= d.tail_w * self.remaining_tail(start)      # refund the previous call's unused tail
        if self.is_deep(start):
            c += d.wake_j
        self.prev_end = end
        self.charges.append(c)
        return c

    def total(self, t_end, resident=True):
        """Charges + floor, with the last tail truncated at t_end."""
        cut = self.dev.tail_w * self.remaining_tail(t_end) if self.charges else 0.0
        return sum(self.charges) - cut + self.dev.floor(resident) * t_end


def naive_tail_total(dev, busy, t_end, resident=True):
    """Every call charged its full tail: the double-counting accounting the refuter warned about."""
    e = dev.floor(resident) * t_end
    prev_end = 0.0
    for s, en in busy:
        if s - prev_end > dev.deep_after_s:
            e += dev.wake_j
        e += dev.p_active * (en - s) + dev.tail_w * dev.tail_s
        prev_end = en
    return e


# Measured on this host (one nvidia-smi query, idle): RTX 4000 SFF Ada P8 12.86 W, RTX 2000E Ada
# P8 6.94 W. Everything else below is a placeholder and is swept by the experiments.
RTX4000_FLOOR_W = 12.86
RTX2000E_FLOOR_W = 6.94
PKG_FLOOR_W = 6.0        # i9-13900T package idle, unmeasured placeholder


def nominal_devices(tail_w=10.0, tail_s=3.0, igpu_tps=18.0, igpu_w=10.0):
    dgpu = Device("rtx2000e", "dgpu", p_floor=RTX2000E_FLOOR_W, p_active=35.0, tail_w=tail_w,
                  tail_s=tail_s, decode_tps=110.0, prefill_tps=3000.0, wake_s=0.05, wake_j=0.5,
                  deep_after_s=30.0)
    # The iGPU's "floor" is the package floor; its active power is the package increment
    # (cores + ring + memory controller) while it decodes. Unmeasured.
    igpu = Device("uhd770", "igpu", p_floor=PKG_FLOOR_W, p_active=igpu_w, tail_w=0.0, tail_s=0.0,
                  decode_tps=igpu_tps, prefill_tps=250.0)
    return dgpu, igpu


def perturb(dev, rng, frac):
    """Router-side calibration: every continuous parameter off by up to +/-frac."""
    f = lambda v: v * (1.0 + rng.uniform(-frac, frac))  # noqa: E731
    return replace(dev, p_active=f(dev.p_active), tail_w=f(dev.tail_w), tail_s=f(dev.tail_s),
                   decode_tps=f(dev.decode_tps), prefill_tps=f(dev.prefill_tps),
                   wake_j=f(dev.wake_j))


def lognormal_median(rng, median, sigma):
    return median * math.exp(rng.gauss(0.0, sigma))


def pct(xs, q):
    s = sorted(xs)
    if not s:
        return 0.0
    k = min(len(s) - 1, max(0, int(math.ceil(q * len(s))) - 1))
    return s[k]


# ---------------------------------------------------------------------------------------------
# Claim 3: per-call routing, closed loop
# ---------------------------------------------------------------------------------------------


@dataclass
class Call:
    agent: int
    arrival: float
    prompt: int
    out: int


def gen_agent_steps(rng, n_steps, median_gap, sigma=1.0, max_out=256, prompt=300):
    """Per-step (prompt, out_tokens, think_gap) for one agent. ≤256-token calls."""
    return [(prompt, rng.randint(16, max_out), lognormal_median(rng, median_gap, sigma))
            for _ in range(n_steps)]


class RouterState:
    """What a router may see: its calibrated devices, busy-until, the ledgers' causal state and
    the gaps it has observed so far."""

    def __init__(self, cal_devs):
        self.cal = cal_devs
        self.busy_until = [0.0] * len(cal_devs)
        self.ledgers = [TailLedger(d) for d in cal_devs]
        self.gaps = []
        self.last_arrival = None

    def observe_arrival(self, t):
        if self.last_arrival is not None:
            self.gaps.append(t - self.last_arrival)
        self.last_arrival = t

    def finish(self, i, a, prompt, out):
        d = self.cal[i]
        s = max(a, self.busy_until[i])
        if self.ledgers[i].is_deep(s):
            s += d.wake_s
        return s, s + d.work_s(prompt, out)

    def expected_own_tail(self, i):
        d = self.cal[i]
        if not self.gaps:
            return d.tail_s
        recent = self.gaps[-200:]
        return sum(min(d.tail_s, g) for g in recent) / len(recent)


def policy_static(idx):
    def pick(st, a, prompt, out_pred, floor_w):
        return idx
    pick.__name__ = "static_%d" % idx
    return pick


def policy_latency(st, a, prompt, out_pred, floor_w):
    return min(range(len(st.cal)), key=lambda i: st.finish(i, a, prompt, out_pred)[1])


def make_ledger_policy(tail=True, floor_time=True, slack=1.5):
    """Lowest marginal joules: active + own expected tail - reclaimed tail + wake + the platform
    floor paid for the extra latency. tail=False is the active-energy-only ablation."""

    def pick(st, a, prompt, out_pred, floor_w):
        fins = [st.finish(i, a, prompt, out_pred) for i in range(len(st.cal))]
        best_lat = min(f[1] - a for f in fins)
        best, best_e = None, math.inf
        for i, (s, e) in enumerate(fins):
            if e - a > slack * best_lat + 1e-9:
                continue
            d = st.cal[i]
            de = d.p_active * (e - s)
            if tail:
                de += d.tail_w * (st.expected_own_tail(i) - st.ledgers[i].remaining_tail(s))
            if st.ledgers[i].is_deep(s):
                de += d.wake_j
            if floor_time:
                de += floor_w * (e - a)
            if de < best_e:
                best, best_e = i, de
        return best

    pick.__name__ = "ledger" if tail else "ledger_no_tail"
    return pick


def route_sim(true_devs, cal_devs, agents_steps, policy, floor_extra_w, len_noise=0.3, seed=0):
    """Closed-loop: each agent issues its next call one think-gap after the previous completes.

    true_devs drive time and energy; the policy only sees cal_devs and a noisy output length.
    floor_extra_w: idle floors of devices outside the routing set (always paid).
    Returns platform J/task, incremental J/task, p50/p95 latency, share per device.
    """
    rng = random.Random(seed)
    st = RouterState(cal_devs)
    true_busy = [[] for _ in true_devs]
    true_until = [0.0] * len(true_devs)
    true_prev_end = [0.0] * len(true_devs)
    floor_w = floor_extra_w + sum(d.p_floor for d in true_devs)
    pending = []  # (arrival, agent, step_idx)
    for ag in range(len(agents_steps)):
        pending.append((rng.uniform(0, 1.0), ag, 0))
    lat, counts = [], [0] * len(true_devs)
    t_end = 0.0
    while pending:
        pending.sort()
        a, ag, k = pending.pop(0)
        prompt, out, gap = agents_steps[ag][k]
        st.observe_arrival(a)
        out_pred = max(1, int(out * math.exp(rng.gauss(0.0, len_noise))))
        i = policy(st, a, prompt, out_pred, floor_w)
        d = true_devs[i]
        s = max(a, true_until[i])
        if true_busy[i] and s - true_prev_end[i] > d.deep_after_s or \
                not true_busy[i] and s > d.deep_after_s:
            s += d.wake_s
        e = s + d.work_s(prompt, out)
        true_busy[i].append((s, e))
        true_until[i] = e
        true_prev_end[i] = e
        # The router learns the actual start/end after the fact (it observes completion).
        st.busy_until[i] = e
        st.ledgers[i].charge(s, e)
        lat.append(e - a)
        counts[i] += 1
        t_end = max(t_end, e)
        if k + 1 < len(agents_steps[ag]):
            pending.append((e + gap, ag, k + 1))
    t_end += max(d.tail_s for d in true_devs)  # let the last tail finish
    energy = floor_extra_w * t_end + sum(integrate_timeline(d, b, t_end)
                                         for d, b in zip(true_devs, true_busy))
    n = len(lat)
    return {"platform_j_per_task": energy / n,
            "incremental_j_per_task": (energy - floor_w * t_end) / n,
            "p50_s": pct(lat, 0.50), "p95_s": pct(lat, 0.95),
            "share": [c / n for c in counts], "makespan_s": t_end}


def claim3_grid(perturb_frac=0.0, cal_seed=1, n_agents=2, n_steps=150, seed=7,
                tail_ws=(2, 5, 10, 20, 40), tail_ss=(0.5, 2, 5, 10),
                igpu_tps_grid=(10, 18, 30), igpu_w_grid=(6, 10, 15), gaps=(1.0, 3.0, 5.0, 10.0)):
    """Sweep tail power x tail length x iGPU speed/power x median gap. Per cell: every policy."""
    rows = []
    for tw in tail_ws:
        for ts in tail_ss:
            for itps in igpu_tps_grid:
                for iw in igpu_w_grid:
                    dgpu, igpu = nominal_devices(tw, ts, itps, iw)
                    true = [dgpu, igpu]
                    crng = random.Random(cal_seed)
                    cal = [perturb(d, crng, perturb_frac) for d in true] if perturb_frac else true
                    for g in gaps:
                        rng = random.Random(seed)
                        steps = [gen_agent_steps(rng, n_steps, g) for _ in range(n_agents)]
                        pols = {"static_dgpu": policy_static(0), "static_igpu": policy_static(1),
                                "latency": policy_latency,
                                "ledger": make_ledger_policy(),
                                "ledger_no_tail": make_ledger_policy(tail=False),
                                "ledger_unconstrained": make_ledger_policy(slack=math.inf),
                                "ledger_incremental": make_ledger_policy(floor_time=False,
                                                                         slack=math.inf),
                                "ledger_incremental_no_tail": make_ledger_policy(
                                    tail=False, floor_time=False, slack=math.inf)}
                        res = {k: route_sim(true, cal, steps, p, RTX4000_FLOOR_W, seed=seed)
                               for k, p in pols.items()}
                        # Whole incremental energy of one median (136-token) call on the iGPU.
                        igpu_call_j = igpu.p_active * igpu.work_s(300, 136)
                        rows.append({"tail_w": tw, "tail_s": ts, "igpu_tps": itps, "igpu_w": iw,
                                     "gap": g, "tail_j": tw * ts, "igpu_call_j": igpu_call_j,
                                     "res": res})
    return rows


def summarize_claim3(rows, metric="platform_j_per_task", router="ledger",
                     ablation="ledger_no_tail"):
    """Apply A3 to every cell where tail_j >= 1.5x the iGPU whole-call energy.

    metric="incremental_j_per_task" with router="ledger_incremental" is the secondary view that
    ignores the floor energy a slower call adds to the run."""
    eligible, passing, attrib = [], [], []
    for r in rows:
        if r["tail_j"] < 1.5 * r["igpu_call_j"]:
            continue
        res = r["res"]
        base = min(res["static_dgpu"][metric], res["static_igpu"][metric],
                   res["latency"][metric])
        led = res[router]
        saving = 1 - led[metric] / base
        saving_ab = 1 - res[ablation][metric] / base
        p95_ok = led["p95_s"] <= 1.5 * res["latency"]["p95_s"]
        eligible.append((r, saving, saving_ab, p95_ok))
        if saving >= 0.20 and p95_ok:
            passing.append(r)
            attrib.append(saving - saving_ab >= 0.10)
    best = max(eligible, key=lambda x: x[1], default=None)
    return {"metric": metric, "router": router, "cells": len(rows),
            "eligible_cells": len(eligible), "passing_cells": len(passing),
            "passing_with_attribution": sum(attrib),
            "max_saving_in_eligible": None if best is None else round(best[1], 4),
            "max_saving_ablation": None if best is None else round(best[2], 4),
            "max_saving_p95_ok": None if best is None else best[3],
            "max_saving_cell": None if best is None else
            {k: best[0][k] for k in ("tail_w", "tail_s", "igpu_tps", "igpu_w", "gap")}}


def summarize_claim3_both(rows):
    return {"primary": summarize_claim3(rows),
            "secondary": summarize_claim3(rows, "incremental_j_per_task", "ledger_incremental",
                                          "ledger_incremental_no_tail")}


# ---------------------------------------------------------------------------------------------
# Claim 2: iGPU residency vs dGPU residency, and the crossover gap T*
# ---------------------------------------------------------------------------------------------


def residency_energy(dgpu, igpu, other_floor_w, median_gap, sigma=1.0, n=400, seed=3,
                     prompt=300, out=128):
    """One sequential agent; every call on one device. Platform J/task for each placement.

    dGPU-resident: dGPU floor = p_floor. iGPU-resident: the dGPU's VRAM is empty, so its floor
    is floor(resident=False) (= p_floor on a desktop card, lower with RTD3).
    """
    rng = random.Random(seed)
    gaps = [lognormal_median(rng, median_gap, sigma) for _ in range(n)]
    out_ = {}
    for where in ("dgpu", "igpu"):
        dev = dgpu if where == "dgpu" else igpu
        t, busy = 0.0, []
        for g in gaps:
            s = t
            e = s + dev.work_s(prompt, out)
            busy.append((s, e))
            t = e + g
        t_end = t + dev.tail_s
        e_dev = integrate_timeline(dev, busy, t_end)
        if where == "dgpu":
            e_other = igpu.p_floor * t_end
        else:
            e_other = dgpu.floor(resident=False) * t_end
        out_[where] = (e_dev + e_other + other_floor_w * t_end) / n
    out_["igpu_saving"] = 1 - out_["igpu"] / out_["dgpu"]
    return out_


def lognormal_mean_min(tau, median, sigma):
    """E[min(tau, G)] for G lognormal(ln median, sigma). Closed form."""
    if tau <= 0:
        return 0.0
    mu = math.log(median)
    phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    z = (math.log(tau) - mu) / sigma
    return math.exp(mu + sigma ** 2 / 2) * phi(z - sigma) + tau * (1 - phi(z))


def predicted_crossover(dgpu, igpu, other_floor_w, sigma=1.0, prompt=300, out=128):
    """Closed-form median gap T* at which iGPU-resident and dGPU-resident cost the same.
    Returns None when the sign does not change with the gap (e.g. desktop dGPU floor)."""
    floors = igpu.p_floor + other_floor_w

    def diff(m):  # E_igpu - E_dgpu per task
        eg = m * math.exp(sigma ** 2 / 2)
        wd, wi = dgpu.work_s(prompt, out), igpu.work_s(prompt, out)
        ed = (floors + dgpu.p_floor) * (wd + eg) + dgpu.p_active * wd + \
            dgpu.tail_w * lognormal_mean_min(dgpu.tail_s, m, sigma)
        ei = (floors + dgpu.floor(False)) * (wi + eg) + igpu.p_active * wi
        return ei - ed

    lo, hi = 0.01, 3600.0
    if (diff(lo) > 0) == (diff(hi) > 0):
        return None
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        if (diff(mid) > 0) == (diff(lo) > 0):
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def simulated_crossover(dgpu, igpu, other_floor_w, sigma=1.0, seeds=(3, 4, 5)):
    def saving(m):
        return sum(residency_energy(dgpu, igpu, other_floor_w, m, sigma, seed=s)["igpu_saving"]
                   for s in seeds) / len(seeds)
    lo, hi = 0.01, 3600.0
    if (saving(lo) > 0) == (saving(hi) > 0):
        return None
    for _ in range(40):
        mid = math.sqrt(lo * hi)
        if (saving(mid) > 0) == (saving(lo) > 0):
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def claim2_report():
    rows = []
    for rtd3 in (False, True):
        for tw, ts in ((5, 2), (10, 3), (20, 5)):
            for itps, iw in ((10, 15), (18, 10), (30, 6)):
                dgpu, igpu = nominal_devices(tw, ts, itps, iw)
                if rtd3:
                    dgpu = replace(dgpu, p_floor_empty=0.5)   # laptop-style D3cold, placeholder
                r = {"rtd3": rtd3, "tail_w": tw, "tail_s": ts, "igpu_tps": itps, "igpu_w": iw}
                for g in (5.0, 10.0, 30.0):
                    r["saving_gap_%g" % g] = round(
                        residency_energy(dgpu, igpu, RTX4000_FLOOR_W, g)["igpu_saving"], 4)
                tp = predicted_crossover(dgpu, igpu, RTX4000_FLOOR_W)
                tsim = simulated_crossover(dgpu, igpu, RTX4000_FLOOR_W)
                r["t_star_pred"] = tp and round(tp, 3)
                r["t_star_sim"] = tsim and round(tsim, 3)
                rows.append(r)
    return rows


# ---------------------------------------------------------------------------------------------
# Claim 1: what-if residency advisor vs reactive keep_alive / LRU
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pool:
    name: str
    kind: str
    mem_gb: float
    mem_bw_gbs: float     # effective decode bandwidth
    load_gbs: float       # host -> device load bandwidth (disk/page cache + PCIe), placeholder


@dataclass(frozen=True)
class Model:
    name: str
    size_gb: float
    out_tok: int


def nominal_pools(load_gbs=3.0):
    return [Pool("rtx4000sff", "dgpu", 20.0, 280 * 0.6, load_gbs),
            Pool("rtx2000e", "dgpu", 16.0, 224 * 0.6, load_gbs)]


MODELS = [Model("router-1.5b", 1.2, 20), Model("embed", 0.6, 1),
          Model("coder-14b", 12.0, 400), Model("planner-32b", 19.0, 300),
          Model("vision-7b", 8.0, 100), Model("summarizer-8b", 16.0, 200)]

# Step -> next-step probabilities. "route" fans out; every other step returns to "route".
MIX_TRAIN = {"router-1.5b": {"coder-14b": 0.35, "planner-32b": 0.15, "vision-7b": 0.15,
                             "summarizer-8b": 0.15, "embed": 0.20}}
MIX_DRIFT = {"router-1.5b": {"coder-14b": 0.15, "planner-32b": 0.30, "vision-7b": 0.10,
                             "summarizer-8b": 0.30, "embed": 0.15}}


def gen_trace(seed, mix, n_agents=3, n_steps=120, median_gap=3.0, sigma=1.0):
    """One [(model, think_gap)] step list per agent; arrivals are closed loop in replay()."""
    rng = random.Random(seed)
    agents = []
    for _ in range(n_agents):
        cur, steps = "router-1.5b", []
        for _ in range(n_steps):
            steps.append((cur, lognormal_median(rng, median_gap, sigma)))
            if cur == "router-1.5b":
                r, acc = rng.random(), 0.0
                nxt = None
                for m, p in mix["router-1.5b"].items():
                    acc += p
                    if r <= acc:
                        nxt = m
                        break
                cur = nxt or "coder-14b"
            else:
                cur = "router-1.5b"
        agents.append(steps)
    return agents


@dataclass
class ReactivePolicy:
    evict: str = "lru"            # lru | cost   (cost = load-cost-weighted, GreedyDual-style)
    keep_alive: float = math.inf
    max_loaded: float = math.inf


@dataclass
class _PoolState:
    pool: Pool
    pinned: dict = field(default_factory=dict)     # model -> size
    loaded: dict = field(default_factory=dict)     # model -> last_used (non-pinned)
    credit: dict = field(default_factory=dict)     # GreedyDual credit
    busy_until: float = 0.0
    inflation: float = 0.0

    def free(self, models):
        used = sum(self.pinned.values()) + sum(models[m].size_gb for m in self.loaded)
        return self.pool.mem_gb - used


def replay(trace, pools, models, pins, policy, prompt=400):
    """Event replay of a closed-loop multi-agent trace. pins: {model: [pool_idx]}.

    Returns stall (sum of load time charged to calls), p95/p50 latency, n calls."""
    mdict = {m.name: m for m in models}
    ps = [_PoolState(p) for p in pools]
    for m, idxs in pins.items():
        for i in idxs:
            ps[i].pinned[m] = mdict[m].size_gb
    for st in ps:
        if sum(st.pinned.values()) > st.pool.mem_gb + 1e-9:
            raise ValueError("pins exceed %s" % st.pool.name)
    pending = [(0.01 * ag, ag, 0) for ag in range(len(trace))]
    lat, stall = [], 0.0

    def dec_s(st, m):
        return prompt / 3000.0 + mdict[m].out_tok * mdict[m].size_gb / st.pool.mem_bw_gbs

    while pending:
        pending.sort()
        a, ag, k = pending.pop(0)
        m, gap = trace[ag][k]
        size = mdict[m].size_gb
        for st in ps:  # keep_alive expiry, lazily
            for x in [x for x, lu in st.loaded.items() if a - lu > policy.keep_alive]:
                del st.loaded[x]
        resident = [i for i, st in enumerate(ps) if m in st.pinned or m in st.loaded]
        if resident:
            i = min(resident, key=lambda j: max(a, ps[j].busy_until) + dec_s(ps[j], m))
            load = 0.0
        else:
            cands = [i for i, st in enumerate(ps)
                     if st.pool.mem_gb - sum(st.pinned.values()) >= size]
            if not cands:
                raise ValueError("%s fits no pool" % m)
            i = min(cands, key=lambda j: max(a, ps[j].busy_until) + size / ps[j].pool.load_gbs)
            st = ps[i]
            while st.free(mdict) < size - 1e-9 or len(st.loaded) + 1 > policy.max_loaded:
                if policy.evict == "lru":
                    victim = min(st.loaded, key=lambda x: st.loaded[x])
                else:
                    victim = min(st.loaded, key=lambda x: st.credit.get(x, 0.0))
                    st.inflation = st.credit.get(victim, 0.0)
                del st.loaded[victim]
            load = size / st.pool.load_gbs
        st = ps[i]
        s = max(a, st.busy_until)
        e = s + load + dec_s(st, m)
        st.busy_until = e
        if m not in st.pinned:
            st.loaded[m] = e
            st.credit[m] = st.inflation + (size / st.pool.load_gbs) / size
        stall += load
        lat.append(e - a)
        if k + 1 < len(trace[ag]):
            pending.append((e + gap, ag, k + 1))
    return {"stall_s": stall, "p95_s": pct(lat, 0.95), "p50_s": pct(lat, 0.5), "n": len(lat)}


def reactive_grid():
    out = []
    for ev in ("lru", "cost"):
        for ka in (60.0, 300.0, 900.0, math.inf):
            for ml in (1, 2, 3, math.inf):
                out.append(ReactivePolicy(ev, ka, ml))
    return out


def objective(r, ref):
    return r["stall_s"] / max(ref["stall_s"], 1e-9) + r["p95_s"] / max(ref["p95_s"], 1e-9)


def _mean(rs):
    return {k: sum(r[k] for r in rs) / len(rs) for k in ("stall_s", "p95_s", "p50_s")}


def eval_cfg(traces, pools, models, pins, pol):
    return _mean([replay(t, pools, models, pins, pol) for t in traces])


def tune_reactive(traces, pools, models, pins=None):
    pins = pins or {}
    results = [(pol, eval_cfg(traces, pools, models, pins, pol)) for pol in reactive_grid()]
    ref = min((r for _, r in results), key=lambda r: r["stall_s"] + r["p95_s"])
    return min(results, key=lambda pr: objective(pr[1], ref))


def _pin_options(models, n_pools, dup_max_gb=2.0):
    opts = {}
    for m in models:
        o = [()] + [(i,) for i in range(n_pools)]
        if m.size_gb <= dup_max_gb and n_pools > 1:
            o.append(tuple(range(n_pools)))
        opts[m.name] = o
    return opts


def _feasible(pins, pools, models):
    mdict = {m.name: m for m in models}
    used = [0.0] * len(pools)
    for m, idxs in pins.items():
        for i in idxs:
            used[i] += mdict[m].size_gb
    if any(u > p.mem_gb + 1e-9 for u, p in zip(used, pools)):
        return False
    # every unpinned model must still fit in some pool's unpinned remainder
    for m in models:
        if not pins.get(m.name):
            if not any(p.mem_gb - u >= m.size_gb for u, p in zip(used, pools)):
                return False
    return True


def advise(traces, pools, models, rounds=2):
    """What-if search on training traces: greedy pin additions, then 1-move local refinement.

    The remainder (unpinned memory) runs the reactive policy tuned on the same traces with
    the current pins. Returns (pins, policy, train_result)."""
    opts = _pin_options(models, len(pools))
    pol0, base = tune_reactive(traces, pools, models)
    ref = base

    def score(pins, pol):
        r = eval_cfg(traces, pools, models, pins, pol)
        return objective(r, ref), r

    pins, pol = {}, pol0
    cur, _ = score(pins, pol)
    for _ in range(rounds):
        improved = False
        for m in models:                         # greedy / local moves: change one model's pins
            for o in opts[m.name]:
                cand = dict(pins)
                cand[m.name] = list(o)
                if cand[m.name] == pins.get(m.name, []):
                    continue
                if not _feasible(cand, pools, models):
                    continue
                v, _ = score(cand, pol)
                if v < cur - 1e-9:
                    pins, cur, improved = cand, v, True
        pol, _ = tune_reactive(traces, pools, models, pins)   # re-tune the remainder
        cur, _ = score(pins, pol)
        if not improved:
            break
    pins = {m: i for m, i in pins.items() if i}
    return pins, pol, eval_cfg(traces, pools, models, pins, pol)


def trace_blind_pins(pools, models):
    """Ablation: smallest-first first-fit pins (no trace statistics), keeping every unpinned
    model loadable somewhere."""
    pins = {}
    for m in sorted(models, key=lambda x: x.size_gb):
        for i in range(len(pools)):
            cand = dict(pins)
            cand[m.name] = [i]
            if _feasible(cand, pools, models):
                pins = cand
                break
    return pins


def claim1_report(load_gbs=3.0, train_seeds=(11, 12), test_seeds=(21, 22, 23)):
    pools, models = nominal_pools(load_gbs), MODELS
    footprint = sum(m.size_gb for m in models) / sum(p.mem_gb for p in pools)
    train = [gen_trace(s, MIX_TRAIN) for s in train_seeds]
    out = {"load_gbs": load_gbs, "footprint_over_memory": round(footprint, 3)}
    pins, pol, tr = advise(train, pools, models)
    base_pol, base_tr = tune_reactive(train, pools, models)
    blind = trace_blind_pins(pools, models)
    blind_pol, _ = tune_reactive(train, pools, models, blind)
    out["advisor_pins"] = pins
    out["advisor_remainder_policy"] = vars(pol)
    out["baseline_policy"] = vars(base_pol)
    for label, mix in (("test_same_mix", MIX_TRAIN), ("test_drifted_mix", MIX_DRIFT)):
        test = [gen_trace(s, mix) for s in test_seeds]
        adv = eval_cfg(test, pools, models, pins, pol)
        base = eval_cfg(test, pools, models, {}, base_pol)
        _, oracle = tune_reactive(test, pools, models)
        bl = eval_cfg(test, pools, models, blind, blind_pol)
        red = lambda x, y, k: 1 - x[k] / y[k] if y[k] > 0 else 0.0  # noqa: E731
        out[label] = {
            "advisor": adv, "baseline_train_tuned": base, "baseline_test_tuned": oracle,
            "trace_blind_pins": bl,
            "stall_reduction_vs_train_tuned": round(red(adv, base, "stall_s"), 4),
            "p95_reduction_vs_train_tuned": round(red(adv, base, "p95_s"), 4),
            "stall_reduction_vs_test_tuned": round(red(adv, oracle, "stall_s"), 4),
            "p95_reduction_vs_test_tuned": round(red(adv, oracle, "p95_s"), 4),
            "blind_stall_reduction_vs_train_tuned": round(red(bl, base, "stall_s"), 4),
            "blind_p95_reduction_vs_train_tuned": round(red(bl, base, "p95_s"), 4)}
    return out


# ---------------------------------------------------------------------------------------------
# A6: can a 100 ms nvidia-smi poll resolve the tail energy
# ---------------------------------------------------------------------------------------------


# Prefill burst at the start of a call: PREFILL_EXTRA x p_act extra for PREFILL_S. Placeholder;
# it exists so the estimators are tested against a non-flat active phase.
PREFILL_S = 0.3


def _true_power(t, floor, p_act, work_s, tail_w, tail_s, spike=0.0):
    if 0.0 <= t < work_s:
        return floor + p_act * (1.0 + (spike if t < PREFILL_S else 0.0))
    if work_s <= t < work_s + tail_s:
        return floor + tail_w
    return floor


def _cum_energy(t, floor, p_act, work_s, tail_w, tail_s, spike=0.0):
    """Integral of _true_power from 0 to t (negative for t < 0)."""
    e = floor * t
    e += p_act * min(max(t, 0.0), work_s)
    e += p_act * spike * min(max(t, 0.0), PREFILL_S)
    e += tail_w * min(max(t - work_s, 0.0), tail_s)
    return e


def tail_estimate(rng, meter, floor, p_act, work_s, tail_w, tail_s, poll=0.1, noise_w=0.3,
                  update_s=0.02, gain_err=0.05, offset_w=5.0, window_s=None, estimator="corrected",
                  spike=0.0):
    """One calibration call, one estimate of the tail energy (J) from polled readings.

    meter: 'instant' (sample-and-hold of instantaneous power refreshed every update_s, the
    power.draw.instant semantics) or 'avg1s' (trailing 1 s mean: power.draw on Ampere+).
    Readings carry a per-call gain error, an offset (nvidia-smi documents +/-5 W) and white noise.
    The host knows the work start (t=0) and end (t=work_s) from its own clock; the floor is the
    mean reading over the 2 s before the call.

    estimator='naive': sum (reading - floor) * poll over samples after work end.
    estimator='corrected':
      instant: skip samples within update_s of the end (they can still hold the active value),
               and stretch the first sample's interval back to the end.
      avg1s:   integrate (reading - floor) over the whole call and subtract plateau * work_s,
               where plateau is the mean reading over [1 s, work_s] (needs work_s > 1.5 s).
               The gain error then scales the estimate instead of leaking active energy into it.
    """
    window_s = window_s if window_s is not None else max(3.0, tail_s + 2.0)
    g = 1.0 + rng.uniform(-gain_err, gain_err)
    off = rng.uniform(-offset_w, offset_w)
    phase = rng.uniform(0, poll)
    upd_phase = rng.uniform(0, update_s)

    def reading(t):
        if meter == "instant":
            tu = math.floor((t - upd_phase) / update_s) * update_s + upd_phase
            p = _true_power(tu, floor, p_act, work_s, tail_w, tail_s, spike)
        else:
            p = _cum_energy(t, floor, p_act, work_s, tail_w, tail_s, spike) - \
                _cum_energy(t - 1.0, floor, p_act, work_s, tail_w, tail_s, spike)
        return g * p + off + rng.gauss(0.0, noise_w)

    pre = [reading(-2.0 + phase + k * poll) for k in range(int(2.0 / poll))]
    f_est = sum(pre) / len(pre)
    end = work_s + window_s
    if estimator == "naive":
        t, e = work_s + phase, 0.0
        while t < end:
            e += (reading(t) - f_est) * poll
            t += poll
        return e
    if meter == "instant":
        t = work_s + update_s + phase
        e = (reading(t) - f_est) * (t - work_s + poll / 2)
        t += poll
        while t < end:
            e += (reading(t) - f_est) * poll
            t += poll
        return e
    if work_s <= 1.5:
        raise ValueError("avg1s corrected estimator needs work_s > 1.5 s for a plateau")
    t, e, plat = phase, 0.0, []
    while t < end:
        r = reading(t)
        e += (r - f_est) * poll
        if 1.0 + poll <= t <= work_s:
            plat.append(r)
        t += poll
    return e - (sum(plat) / len(plat) - f_est) * work_s


def tail_meter_sim(n_calls=(1, 10, 50), seed=5, trials=20, work_s=3.0, spikes=(0.0, 0.5)):
    """p90 relative error of an N-call averaged tail estimate, over `trials` calibrations.
    spike=0.5: a 0.3 s prefill burst at 1.5x active power (non-flat active phase)."""
    rng = random.Random(seed)
    rows = []
    for meter, spike in [(m, sp) for m in ("instant", "avg1s") for sp in spikes]:
        for est_kind in ("naive", "corrected"):
            for noise in (0.1, 0.3, 1.0):
                for tail_w, tail_s in ((5.0, 0.1), (5.0, 0.2), (10.0, 0.5), (10.0, 2.0)):
                    true_j = tail_w * tail_s
                    for n in n_calls:
                        errs = []
                        for _ in range(trials):
                            est = sum(tail_estimate(rng, meter, RTX2000E_FLOOR_W, 35.0, work_s,
                                                    tail_w, tail_s, noise_w=noise,
                                                    estimator=est_kind, spike=spike)
                                      for _ in range(n)) / n
                            errs.append(abs(est - true_j) / true_j)
                        rows.append({"meter": meter, "estimator": est_kind, "spike": spike,
                                     "noise_w": noise,
                                     "tail_j": true_j, "n_calls": n,
                                     "p90_rel_err": round(pct(errs, 0.9), 3)})
    return rows


def meter_verdict(rows, target_j=0.5, max_n=50, tol=0.5, noise_w=0.3):
    """A6: per meter/estimator/spike, the smallest swept tail energy whose p90 relative error is
    <= tol with <= max_n calls at the given noise level, and whether that reaches target_j."""
    out = {}
    for meter, est, spike in sorted({(r["meter"], r["estimator"], r["spike"]) for r in rows}):
        ok = sorted({r["tail_j"] for r in rows
                     if (r["meter"], r["estimator"], r["spike"]) == (meter, est, spike)
                     and r["n_calls"] <= max_n and r["p90_rel_err"] <= tol
                     and r["noise_w"] == noise_w})
        out["%s/%s/spike=%g" % (meter, est, spike)] = {
            "min_resolvable_j": ok[0] if ok else None,
            "resolves_%gJ" % target_j: bool(ok) and ok[0] <= target_j}
    return out


# ---------------------------------------------------------------------------------------------
# Plan -> pinned Deployments
# ---------------------------------------------------------------------------------------------

POOL_RESOURCE = {"dgpu": "nvidia.com/gpu", "igpu": "gpu.intel.com/i915"}


def deployments(plan, image, namespace="default"):
    """plan: {"pools": {name: kind}, "pins": {model: [pool names]}} -> k8s List (JSON = YAML).

    One Deployment per (model, pool). The pool name is carried as a label and a nodeSelector-free
    annotation: a device-plugin request cannot choose WHICH dGPU on a multi-GPU node, so pinning
    to a specific card needs DRA; the annotation records the intent."""
    items = []
    for model, pool_names in sorted(plan["pins"].items()):
        for pn in pool_names:
            kind = plan["pools"][pn]
            name = ("%s-%s" % (model, pn)).lower().replace(".", "-")[:63]
            labels = {"app": name, "k3d-gpu.io/model": model.replace(".", "-"),
                      "k3d-gpu.io/residency-pool": pn}
            container = {"name": "server", "image": image,
                         "env": [{"name": "MODEL", "value": model}],
                         "resources": {"limits": {POOL_RESOURCE[kind]: 1}}}
            spec = {"replicas": 1, "selector": {"matchLabels": {"app": name}},
                    "template": {"metadata": {"labels": labels,
                                              "annotations": {"k3d-gpu.io/residency": "pinned"}},
                                 "spec": {"containers": [container]}}}
            if kind == "dgpu":
                spec["template"]["spec"]["runtimeClassName"] = "nvidia"
            items.append({"apiVersion": "apps/v1", "kind": "Deployment",
                          "metadata": {"name": name, "namespace": namespace, "labels": labels},
                          "spec": spec})
    return {"apiVersion": "v1", "kind": "List", "items": items}


# ---------------------------------------------------------------------------------------------


def full_report():
    rep = {}
    rows = claim3_grid()
    rep["claim3_nominal"] = summarize_claim3_both(rows)
    rep["claim3_perturbed"] = []
    for cs in (1, 2, 3):
        for frac in (0.3,):
            rep["claim3_perturbed"].append(
                dict(cal_seed=cs, frac=frac,
                     **summarize_claim3_both(claim3_grid(frac, cal_seed=cs))))
    rep["claim2"] = claim2_report()
    rep["claim1"] = [claim1_report(bw) for bw in (1.5, 3.0, 6.0)]
    rep["tail_meter"] = tail_meter_sim()
    rep["tail_meter_verdict"] = meter_verdict(rep["tail_meter"])
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report")
    for name in ("claim1", "claim2", "claim3", "meter"):
        sub.add_parser(name)
    d = sub.add_parser("deploy")
    d.add_argument("--image", required=True)
    d.add_argument("--plan", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "deploy":
        with open(a.plan) as f:
            print(json.dumps(deployments(json.load(f), a.image), indent=2))
        return 0
    out = {"report": full_report,
           "claim1": lambda: [claim1_report(bw) for bw in (1.5, 3.0, 6.0)],
           "claim2": claim2_report,
           "claim3": lambda: summarize_claim3_both(claim3_grid()),
           "meter": lambda: meter_verdict(tail_meter_sim())}[a.cmd]()
    print(json.dumps(out, indent=1, default=lambda x: "inf" if x == math.inf else str(x)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
