#!/usr/bin/env python3
"""Trace-replay simulator for receipt-driven MCP tool steering. See PREREG.md / DESIGN.md.

A single agent runs tasks back to back. Each task alternates LLM generation turns (NVIDIA,
never an MCP tools/call, so never steered) with 1-3 MCP tool calls. Every tool has P-balloon
and E-balloon variants; embed and rerank also have iGPU (OVMS) and NVIDIA variants.

Task joules = whole-host energy over the task: idle_watts x wall time + incremental power of
whatever runs. The host's state changes over time (thermal throttle episodes, NVIDIA
co-tenant contention); the amplitude is a perturbed parameter and 0 = stationary.

Policies run the real receipts.Steerer on noisy receipts (apportionment noise from
receipts.apportion_sigma). Every coefficient in TOOLS / POWER is an unfitted prior.
"""

from __future__ import annotations

import argparse
import bisect
import itertools
import json
import math
import multiprocessing as mp
import random
import statistics
import sys
from dataclasses import dataclass

import receipts

# ----------------------------------------------------------------- priors (unfitted)

# cpu = P-core-seconds of single-thread work at nominal clock; wait = I/O seconds.
# igpu / nv = (latency s, incremental watts) for the device variant at nominal.
TOOLS = {
    "http_fetch": dict(cpu=0.01, wait=0.25),
    "file_grep":  dict(cpu=0.04, wait=0.005),
    "json_xform": dict(cpu=0.08, wait=0.0),
    "code_exec":  dict(cpu=1.50, wait=0.05),
    "embed":      dict(cpu=0.60, wait=0.0, igpu=(0.25, 8.0), nv=(0.05, 60.0)),
    "rerank":     dict(cpu=0.30, wait=0.0, igpu=(0.12, 8.0), nv=(0.03, 60.0)),
}
MIX = {
    "research": {"http_fetch": .40, "embed": .25, "rerank": .20, "json_xform": .15},
    "coding":   {"file_grep": .35, "code_exec": .30, "json_xform": .20, "http_fetch": .15},
}
DEVICE_MIX = {"device": {"embed": .5, "rerank": .5}}  # positive control only
P_CORE_W = 9.0      # incremental package watts, one busy P core at nominal clock
E_SPEED = 0.60      # E-core single-thread speed relative to P
LLM_GEN_W = 220.0   # incremental watts while generating (both NVIDIA boards)


def variants_of(tool):
    return ["P", "E"] + (["igpu", "nv0"] if "igpu" in TOOLS[tool] else [])


# ----------------------------------------------------------------- host state over time

@dataclass
class Cfg:
    llm_share: float = 0.9
    ep_ratio: float = 1.0      # E joules-per-op / P joules-per-op at nominal
    idle_w: float = 25.0
    amp: float = 0.15          # non-stationarity amplitude, 0 = stationary
    bg_sigma: float = 2.0      # apportionment noise, watts
    slack: float = 0.10        # per-tool latency SLO: p95 <= (1 + slack) x best variant
    episode_scale: float = 1.0  # multiplies mean throttle/contention episode lengths
    mix: str = "default"       # "device" = embed/rerank only (positive control)
    seed: int = 0
    n_tasks: int = 400


class Regime:
    """Alternating on/off episodes for throttle and NVIDIA contention, fixed per seed."""

    def __init__(self, amp, rng, scale=1.0, horizon=1e6):
        self.amp = amp
        self.thr = self._episodes(rng, 60.0 * scale, 120.0 * scale, horizon) if amp > 0 else [0.0]
        self.con = self._episodes(rng, 45.0 * scale, 90.0 * scale, horizon) if amp > 0 else [0.0]

    @staticmethod
    def _episodes(rng, mean_on, mean_off, horizon):
        t, edges, on = 0.0, [0.0], False
        t += rng.expovariate(1 / mean_off)
        while t < horizon:
            edges.append(t)
            on = not on
            t += rng.expovariate(1 / (mean_on if on else mean_off))
        return edges

    @staticmethod
    def _on(edges, t):
        return (bisect.bisect_right(edges, t) - 1) % 2 == 1

    def state(self, t):
        if self.amp <= 0:
            return False, False
        return self._on(self.thr, t), self._on(self.con, t)


def true_cost(tool, v, cfg, throttled, contended, rng=None):
    """(latency s, incremental joules). rng=None gives the expected value."""
    a = cfg.amp
    spec = TOOLS[tool]
    if v in ("P", "E"):
        speed, watts = (1.0, P_CORE_W) if v == "P" else (E_SPEED, cfg.ep_ratio * P_CORE_W * E_SPEED)
        if throttled:  # package clock drops; P falls further than E. P ~ f^2.5
            th = 1 - 1.5 * a if v == "P" else 1 - a
            speed, watts = speed * th, watts * th ** 2.5
        busy = spec["cpu"] / speed
        return spec["wait"] + busy, busy * watts
    lat, watts = spec["igpu"] if v == "igpu" else spec["nv"]
    if v == "igpu" and throttled:  # PP1 gets priority over IA cores, small hit only
        lat, watts = lat / (1 - a / 2), watts * (1 - a / 2) ** 2
    queue = 0.0
    if v == "nv0" and contended:
        mean_q = 4.0 * a
        queue = rng.expovariate(1 / mean_q) if rng else mean_q
    return lat + queue, lat * watts


# ----------------------------------------------------------------- tasks

def make_tasks(cfg, rng):
    mix = DEVICE_MIX if cfg.mix == "device" else MIX
    tasks = []
    for _ in range(cfg.n_tasks):
        kind = rng.choice(list(mix))
        names, weights = zip(*mix[kind].items())
        steps = []
        for _ in range(rng.randint(4, 10)):
            steps.append(("llm", rng.lognormvariate(0, 0.5)))
            for _ in range(rng.randint(1, 3)):
                steps.append(("tool", rng.choices(names, weights)[0]))
        tasks.append(steps)
    return tasks


# ----------------------------------------------------------------- policies

class Static:
    now = 0.0

    def __init__(self, choice):
        self.choice = choice

    def pick(self, tool, *_):
        return self.choice[tool]

    def observe(self, *_):
        pass


class Steered:
    def __init__(self, cfg, rng, **kw):
        tools = list(TOOLS)
        self.now = 0.0
        self.s = receipts.Steerer({t: variants_of(t) for t in tools}, kw.pop("defaults"),
                                  idle_watts=cfg.idle_w, latency_slack=cfg.slack, rng=rng,
                                  clock=lambda: self.now, **kw)

    def pick(self, tool, pressure, *_):
        return self.s.choose(tool, pressure)

    def observe(self, tool, v, joules_obs, lat, conf):
        self.s.observe(tool, v, joules_obs, lat, conf)


class Oracle:
    """Knows the true current expected cost of every variant. slo=False drops the SLO."""

    now = 0.0

    def __init__(self, cfg, slo=True):
        self.cfg, self.slo = cfg, slo

    def pick(self, tool, pressure, thr, con):
        opts = {v: true_cost(tool, v, self.cfg, thr, con) for v in variants_of(tool)}
        pool = opts
        if self.slo:
            best = min(l for l, _ in opts.values())
            pool = {v: c for v, c in opts.items() if c[0] <= (1 + self.cfg.slack) * best}
        return min(pool, key=lambda v: pool[v][1] + self.cfg.idle_w * pool[v][0])

    def observe(self, *_):
        pass


def sweep(cfg, regime_seed, n=400):
    """Offline per-tool sweep over representative host conditions. Returns
    (best-latency choice, min-joule-under-SLO choice)."""
    rng = random.Random(regime_seed)
    reg = Regime(cfg.amp, rng, cfg.episode_scale)
    times = [rng.uniform(0, 20000) for _ in range(n)]
    bl, fair = {}, {}
    for tool in TOOLS:
        stats = {}
        for v in variants_of(tool):
            lats, js = [], []
            for t in times:
                thr, con = reg.state(t)
                lat, inc = true_cost(tool, v, cfg, thr, con, rng)
                lats.append(lat)
                js.append(inc + cfg.idle_w * lat)
            lats.sort()
            stats[v] = (statistics.fmean(lats), lats[int(0.95 * n)], statistics.fmean(js))
        bl[tool] = min(stats, key=lambda v: stats[v][0])
        best_p95 = min(s[1] for s in stats.values())
        ok = [v for v in stats if stats[v][1] <= (1 + cfg.slack) * best_p95]
        fair[tool] = min(ok, key=lambda v: stats[v][2])
    return bl, fair


# ----------------------------------------------------------------- replay

def replay(cfg, tasks, policy, llm_scale, regime_seed, noise_seed, budget=None):
    reg = Regime(cfg.amp, random.Random(regime_seed), cfg.episode_scale)
    rng = random.Random(noise_seed)
    clock = rng.uniform(0, 5000)  # random phase, so the run does not always start "off"
    task_j, task_lat, tool_j = [], [], 0.0
    ewma_task = None
    for steps in tasks:
        t0, spent = clock, 0.0
        for kind, x in steps:
            if kind == "llm":
                j = llm_scale * x
                lat = j / (LLM_GEN_W + cfg.idle_w)
                clock += lat
                spent += j
                continue
            thr, con = reg.state(clock)
            pressure = False
            if budget is not None and ewma_task is not None:
                pressure = budget - spent < ewma_task - spent
            policy.now = clock
            v = policy.pick(x, pressure, thr, con)
            lat, inc = true_cost(x, v, cfg, thr, con, rng)
            clock += lat
            total = inc + cfg.idle_w * lat
            spent += total
            tool_j += total
            sigma = receipts.apportion_sigma(cfg.idle_w + inc / max(lat, 1e-9), cfg.bg_sigma, lat)
            obs = max(0.0, inc + rng.gauss(0, sigma))
            policy.observe(x, v, obs, lat, receipts.confidence(obs, sigma))
        task_j.append(spent)
        task_lat.append(clock - t0)
        ewma_task = spent if ewma_task is None else 0.9 * ewma_task + 0.1 * spent
    return task_j, task_lat, tool_j


def summarize(task_j, task_lat, tool_j):
    lats = sorted(task_lat)
    return {"mean_j": statistics.fmean(task_j), "median_j": statistics.median(task_j),
            "p95_lat": lats[int(0.95 * (len(lats) - 1))], "tool_share": tool_j / sum(task_j)}


def task_level_fair(cfg, start, scale, seed, extra_starts=()):
    """Strongest static baseline: coordinate descent over per-tool variants on a calibration
    replay (own task set, regime and noise seeds), minimising mean task joules subject to
    calibration task p95 <= 1.10 x the best-latency assignment's (start). Descent begins from
    the cheapest feasible of start and extra_starts. Offline, free, no receipts."""
    ctasks = make_tasks(cfg, random.Random(seed))  # same size as eval, own seeds

    def ev(choice):
        tj, tl, _ = replay(cfg, ctasks, Static(choice), scale, seed + 1, seed + 2)
        return statistics.fmean(tj), sorted(tl)[int(0.95 * (len(tl) - 1))]

    best_j, p95_ref = ev(start)
    cur = dict(start)
    for alt in extra_starts:
        j, p = ev(alt)
        if j < best_j and p <= 1.10 * p95_ref:
            cur, best_j = dict(alt), j
    for _ in range(2):
        changed = False
        for tool in TOOLS:
            for v in variants_of(tool):
                if v == cur[tool]:
                    continue
                cand = {**cur, tool: v}
                j, p = ev(cand)
                if j < best_j * (1 - 1e-6) and p <= 1.10 * p95_ref:
                    cur, best_j, changed = cand, j, True
        if not changed:
            break
    return cur


def run(cfg: Cfg) -> dict:
    rng = random.Random(cfg.seed)
    tasks = make_tasks(cfg, rng)
    reg_seed, noise_seed = cfg.seed * 1009 + 1, cfg.seed * 7919 + 3
    bl, fair_tool = sweep(cfg, regime_seed=cfg.seed * 31 + 17)   # separate seed from eval
    _, fair_nom = sweep(Cfg(**{**cfg.__dict__, "amp": 0.0}), regime_seed=cfg.seed * 31 + 17)

    # Calibrate LLM joules so the best-latency static run has the target LLM share.
    _, _, tool_j = replay(cfg, tasks, Static(bl), 0.0, reg_seed, noise_seed)
    llm_units = sum(x for steps in tasks for k, x in steps if k == "llm")
    s = cfg.llm_share
    scale = s / (1 - s) * tool_j / llm_units
    fair = task_level_fair(cfg, bl, scale, seed=cfg.seed * 131 + 71, extra_starts=(fair_tool, fair_nom))

    def go(policy, budget=None):
        return replay(cfg, tasks, policy, scale, reg_seed, noise_seed, budget)

    steer_kw = dict(defaults=bl)
    pols = {
        "static_best_latency": lambda: Static(bl),
        "static_fair": lambda: Static(fair),
        "static_fair_per_tool": lambda: Static(fair_tool),
        "static_fair_nominal": lambda: Static(fair_nom),
        "steer": lambda: Steered(cfg, random.Random(cfg.seed + 5), **steer_kw),
        "steer_no_gate": lambda: Steered(cfg, random.Random(cfg.seed + 5), gate=False, **steer_kw),
        "steer_eps0": lambda: Steered(cfg, random.Random(cfg.seed + 5), epsilon=0.0, **steer_kw),
        "steer_incremental": lambda: Steered(cfg, random.Random(cfg.seed + 5),
                                             cost_mode="incremental", **steer_kw),
        "steer_from_fair": lambda: Steered(cfg, random.Random(cfg.seed + 5), defaults=fair),
        # post-hoc variant (not pre-registered): time-based re-probe instead of pure epsilon
        "steer_reprobe": lambda: Steered(cfg, random.Random(cfg.seed + 5), defaults=fair,
                                         alpha=0.5, epsilon=0.0, reprobe_s=20.0),
        "oracle": lambda: Oracle(cfg),
        "oracle_no_slo": lambda: Oracle(cfg, slo=False),
    }
    out = {"cfg": cfg.__dict__, "choices": {"best_latency": bl, "fair": fair, "fair_per_tool": fair_tool,
                                            "fair_nominal": fair_nom}}
    res = {k: summarize(*go(f())) for k, f in pols.items()}
    out["policies"] = res

    budget = 0.7 * res["static_fair"]["median_j"]
    tj, _, _ = go(Steered(cfg, random.Random(cfg.seed + 5), defaults=bl), budget=budget)
    floor_j, _, _ = go(Oracle(cfg, slo=False))   # min-joule floor per task (no SLO)
    out["budget"] = {
        "budget_j": budget,
        "within_frac": sum(j <= budget for j in tj) / len(tj),
        "floor_over_budget_frac": sum(j > budget for j in floor_j) / len(floor_j),
    }
    return out


# ----------------------------------------------------------------- grid + verdicts

GRID = dict(llm_share=[0.60, 0.75, 0.90, 0.95], ep_ratio=[0.7, 1.0, 1.3], idle_w=[12, 25, 45],
            amp=[0.0, 0.15, 0.30], bg_sigma=[0.5, 5.0], slack=[0.10, 1.00],
            episode_scale=[1.0, 4.0], seed=[0, 1])


def grid_cfgs(grid=GRID, n_tasks=400):
    keys = list(grid)
    for vals in itertools.product(*grid.values()):
        yield Cfg(**dict(zip(keys, vals)), n_tasks=n_tasks)


def saving(r, pol, base):
    return 1 - r["policies"][pol]["mean_j"] / r["policies"][base]["mean_j"]


def lat_ratio(r, pol):
    return r["policies"][pol]["p95_lat"] / r["policies"]["static_best_latency"]["p95_lat"]


def verdicts(results):
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))]
    v = {}
    # H1 arithmetic upper bound (oracle, no SLO) per LLM share
    v["H1"] = {}
    for s in GRID["llm_share"]:
        rs = [r for r in results if r["cfg"]["llm_share"] == s]
        b = [saving(r, "oracle_no_slo", "static_best_latency") for r in rs]
        bf = [saving(r, "oracle_no_slo", "static_fair") for r in rs]
        v["H1"][str(s)] = {"bound_vs_best_latency_max": max(b), "bound_vs_best_latency_median": q(b, .5),
                           "bound_vs_fair_max": max(bf), "bound_vs_fair_median": q(bf, .5)}
    llm_dom = [r for r in results if r["cfg"]["llm_share"] >= 0.75]
    v["H1"]["pass_llm_dominated"] = max(saving(r, "oracle_no_slo", "static_best_latency")
                                        for r in llm_dom) >= 0.20
    # H2 vs fair, H2b vs strawman
    for name, base in (("H2", "static_fair"), ("H2b", "static_best_latency")):
        v[name] = {}
        for s in GRID["llm_share"]:
            rs = [r for r in results if r["cfg"]["llm_share"] == s]
            sv = [saving(r, "steer", base) for r in rs]
            v[name][str(s)] = {"median": q(sv, .5), "max": max(sv), "min": min(sv)}
    sv_all = [saving(r, "steer", "static_fair") for r in results]
    v["H2"]["pass"] = q(sv_all, .5) >= 0.20 and q([lat_ratio(r, "steer") for r in results], .5) <= 1.10
    v["H2"]["refuted"] = max(sv_all) < 0.10
    # C1 stationary control
    stat = [r for r in results if r["cfg"]["amp"] == 0]
    c1 = [saving(r, "steer", "static_fair") for r in stat]
    v["C1"] = {"max_margin": max(c1), "median_margin": q(c1, .5), "pass": max(c1) <= 0.02}
    # H3 non-stationary weak claim
    ns = [r for r in results if r["cfg"]["amp"] > 0]
    hit = [saving(r, "steer", "static_fair") >= 0.05 and lat_ratio(r, "steer") <= 1.10 for r in ns]
    v["H3"] = {"frac_draws_pass": sum(hit) / len(hit), "pass": sum(hit) / len(hit) >= 0.80,
               "median_saving": q([saving(r, "steer", "static_fair") for r in ns], .5),
               "oracle_median_saving": q([saving(r, "oracle", "static_fair") for r in ns], .5)}
    # H4 budget
    w = [r["budget"]["within_frac"] for r in results]
    fl = [r["budget"]["floor_over_budget_frac"] for r in results]
    v["H4"] = {"median_within": q(w, .5), "frac_draws_ge_90": sum(x >= .9 for x in w) / len(w),
               "median_floor_over_budget": q(fl, .5)}
    by_s = {}
    for s in GRID["llm_share"]:
        rs = [r for r in results if r["cfg"]["llm_share"] == s]
        by_s[str(s)] = {"median_within": q([r["budget"]["within_frac"] for r in rs], .5),
                        "median_floor_over_budget": q([r["budget"]["floor_over_budget_frac"] for r in rs], .5)}
    v["H4"]["by_llm_share"] = by_s
    v["H4"]["pass"] = v["H4"]["frac_draws_ge_90"] >= 0.5
    # H6 gating
    worse = [r["policies"]["steer"]["mean_j"] > r["policies"]["steer_no_gate"]["mean_j"] for r in results]
    v["H6"] = {"frac_gated_worse": sum(worse) / len(worse), "pass": sum(worse) / len(worse) <= 0.5}
    # ablations, median saving vs fair over all draws
    v["ablations_median_saving_vs_fair"] = {
        p: q([saving(r, p, "static_fair") for r in results], .5)
        for p in ("steer", "steer_no_gate", "steer_eps0", "steer_incremental", "steer_from_fair",
                  "steer_reprobe", "static_fair_per_tool", "static_fair_nominal",
                  "static_best_latency", "oracle", "oracle_no_slo")}
    v["median_p95_latency_ratio_vs_best_latency"] = {
        p: q([lat_ratio(r, p) for r in results], .5)
        for p in ("steer", "steer_reprobe", "static_fair", "oracle", "steer_incremental")}
    return v


def positive_control():
    """C2 (added after pre-registration, before any results): can the sim show a win at all?
    Device-tool-only mix, strong, slow non-stationarity, low noise, loose per-tool SLO."""
    # device tasks are short: 6000 of them span ~4500 s, i.e. many contention episodes
    rs = [run(Cfg(mix="device", llm_share=0.2, amp=0.3, bg_sigma=0.5, idle_w=12, slack=1.0,
                  episode_scale=2.0, n_tasks=6000, seed=s)) for s in range(5)]
    sv = [saving(r, "steer", "static_fair") for r in rs]
    so = [saving(r, "oracle", "static_fair") for r in rs]
    sr = [saving(r, "steer_reprobe", "static_fair") for r in rs]
    return {"steer_saving_vs_fair": sv, "steer_reprobe_saving_vs_fair": sr,
            "oracle_saving_vs_fair": so, "fair_choice": rs[0]["choices"]["fair"],
            "lat_ratio": [lat_ratio(r, "steer") for r in rs]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--tasks", type=int, default=400)
    ap.add_argument("--procs", type=int, default=2, help="keep low: live benchmarks share this host")
    ap.add_argument("--quick", action="store_true", help="2 seeds instead of 5")
    a = ap.parse_args(argv)
    grid = dict(GRID)
    if a.quick:
        grid["seed"] = [0, 1]
    cfgs = list(grid_cfgs(grid, a.tasks))
    with mp.Pool(a.procs) as pool:
        results = pool.map(run, cfgs, chunksize=4)
    v = verdicts(results)
    v["C2_positive_control"] = positive_control()
    v["n_draws"] = len(results)
    json.dump({"verdicts": v, "sample_choices": results[0]["choices"]}, open(a.out, "w"), indent=1)
    json.dump(v, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
