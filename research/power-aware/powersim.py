#!/usr/bin/env python3
"""Power-cap-aware placement simulator (stdlib only, no hardware access).

Plant: one hybrid package (8 P-cores, 16 E-cores in 4-core clusters, one iGPU) under an
effective power cap min(PL1, thermal cap). The PCU gives the iGPU its power first
(SDM default PP1 priority), the cores share the rest at one common frequency ratio,
never below base frequency. Agent throughput is Leontief over LLM-inference work L and
tool work T: X = min(L/m, T/(1-m)).

Controller: fits the plant model online from the signals RAPL/sysfs expose, searches the
placement grid every `period` seconds and switches when the predicted gain beats the
switching cost. Baselines: naive static, tuned static (best single placement on the true
plant), phase oracle.

Every coefficient below is an unfitted prior. See DESIGN.md section 8 for the probes that
fit them.
"""
import json
import math
import random
import statistics
import sys

FMAX = {"P": 5.1, "E": 3.9, "G": 1.65}  # GHz: cpuinfo_max_freq cpu0/cpu16, gt_RP0_freq_mhz
FMIN = {"P": 1.1, "E": 0.8, "G": 0.3}   # GHz: base_frequency cpu0/cpu16, gt_RPn_freq_mhz
N_P, N_E, E_STEP = 8, 16, 4             # E-cores move one 4-core cluster at a time
PL1 = 117.0                             # W, intel-rapl:0/constraint_0_power_limit_uw
T_LIM = 100.0                           # C, believed TjMax; probe 4 reads the real TCC target

# Prior ranges for plant coefficients (uniform). Units: W, W/GHz^3, K/W, work units/s.
PRIOR = {
    "p_static": (6.0, 15.0),
    "kP": (0.08, 0.20),
    "kE": (0.03, 0.10),
    "kG": (2.0, 5.0),
    "r_th": (0.6, 1.6),
    "aLP": (0.8, 1.2),
    "aLE_ratio": (0.35, 0.60),
    "aLG": (2.0, 8.0),
    "aTP": (0.8, 1.2),
    "aTE_ratio": (0.40, 0.65),
    "bL": (0.2, 0.7),
    "bT": (0.7, 1.0),
    "bG": (0.5, 1.0),
    "bw": (6.0, 15.0),
}

NAIVE = (4, 4, 8, 8, 1)  # (P infer, P tool, E infer, E tool, iGPU on)


def configs():
    out = []
    for pL in range(N_P + 1):
        for pT in range(N_P + 1 - pL):
            for eL in range(0, N_E + 1, E_STEP):
                for eT in range(0, N_E + 1 - eL, E_STEP):
                    for g in (0, 1):
                        out.append((pL, pT, eL, eT, g))
    return out


CONFIGS = configs()


def _complete(raw):
    p = dict(raw)
    p["aLE"] = p["aLP"] * p.pop("aLE_ratio")
    p["aTE"] = p["aTP"] * p.pop("aTE_ratio")
    return p


def draw_plant(rng):
    return _complete({k: rng.uniform(*v) for k, v in PRIOR.items()})


def prior_midpoint():
    return _complete({k: (lo + hi) / 2 for k, (lo, hi) in PRIOR.items()})


def thermal_cap(p, t_amb):
    return min(PL1, (T_LIM - t_amb) / p["r_th"])


def cpu_freqs(cP, cE, budget):
    """Common-ratio frequencies that fit `budget` W. cP, cE = k * active core count."""
    full = cP * FMAX["P"] ** 3 + cE * FMAX["E"] ** 3
    if budget >= full:
        return FMAX["P"], FMAX["E"]
    floor = cP * FMIN["P"] ** 3 + cE * FMIN["E"] ** 3
    if budget <= floor:
        return FMIN["P"], FMIN["E"]
    s = (budget / full) ** (1 / 3)
    if s * FMAX["P"] >= FMIN["P"] and s * FMAX["E"] >= FMIN["E"]:
        return s * FMAX["P"], s * FMAX["E"]
    # P-cores hit base first (1.1/5.1 > 0.8/3.9): pin them, give the rest to E.
    rest = budget - cP * FMIN["P"] ** 3
    fE = (rest / cE) ** (1 / 3) if cE else FMIN["E"]
    return FMIN["P"], max(FMIN["E"], min(FMAX["E"], fE))


def steady(p, cfg, m, cap, coupled=True):
    """Steady state of placement `cfg` under `cap` W. coupled=False is the ablated model
    that forgets the iGPU draws from the same budget as the cores."""
    pL, pT, eL, eT, g = cfg
    cP, cE = p["kP"] * (pL + pT), p["kE"] * (eL + eT)
    budget = cap - p["p_static"]
    floor_cpu = cP * FMIN["P"] ** 3 + cE * FMIN["E"] ** 3
    fG = pg = 0.0
    if g:
        fG = FMAX["G"]
        if p["kG"] * fG ** 3 > budget - floor_cpu:  # GPU has priority, cores keep base
            fG = max(FMIN["G"], (max(budget - floor_cpu, 0.0) / p["kG"]) ** (1 / 3))
        pg = p["kG"] * fG ** 3
    fP, fE = cpu_freqs(cP, cE, budget - pg if coupled else budget)
    core = cP * fP ** 3 + cE * fE ** 3
    rP, rE, rG = fP / FMAX["P"], fE / FMAX["E"], fG / FMAX["G"]
    pools = {
        "LP": pL * p["aLP"] * rP ** p["bL"],
        "LE": eL * p["aLE"] * rE ** p["bL"],
        "LG": g * p["aLG"] * rG ** p["bG"],
        "TP": pT * p["aTP"] * rP ** p["bT"],
        "TE": eT * p["aTE"] * rE ** p["bT"],
    }
    l_raw = pools["LP"] + pools["LE"] + pools["LG"]
    if l_raw > p["bw"]:  # shared DRAM bandwidth clamps inference proportionally
        for k in ("LP", "LE", "LG"):
            pools[k] *= p["bw"] / l_raw
    L = min(l_raw, p["bw"])
    T = pools["TP"] + pools["TE"]
    X = T if m <= 0 else L if m >= 1 else min(L / m, T / (1 - m))
    throttled = ((pL + pT) and fP < FMAX["P"] * 0.999) or ((eL + eT) and fE < FMAX["E"] * 0.999) \
        or (g and fG < FMAX["G"] * 0.999)
    return {"X": X, "fP": fP, "fE": fE, "fG": fG, "core": core, "gfx": pg,
            "pkg": p["p_static"] + core + pg, "pools": pools, "l_raw": l_raw,
            "throttled": bool(throttled)}


def best_config(p, m, cap, coupled=True):
    best, xb = None, -1.0
    for c in CONFIGS:
        x = steady(p, c, m, cap, coupled)["X"]
        if x > xb:
            best, xb = c, x
    return best, xb


class Estimator:
    """Online fit of the controller's plant model from observable signals."""

    def __init__(self, prior, alpha=0.2):
        self.p = dict(prior)
        self.rho = prior["kE"] / prior["kP"]  # not identifiable online; probe 2 fits it
        self.cap = PL1
        self.m = 0.5
        self.a = alpha

    def _ew(self, key, v):
        self.p[key] += self.a * (v - self.p[key])

    def update(self, o, cfg):
        pL, pT, eL, eT, g = cfg
        p = self.p
        self._ew("p_static", o["pkg"] - o["core"] - o["gfx"])
        if g and o["fG"] > 0:
            self._ew("kG", o["gfx"] / o["fG"] ** 3)
        den = (pL + pT) * o["fP"] ** 3 + self.rho * (eL + eT) * o["fE"] ** 3
        if den > 0:
            self._ew("kP", o["core"] / den)
            p["kE"] = self.rho * p["kP"]
        rP, rE, rG = o["fP"] / FMAX["P"], o["fE"] / FMAX["E"], o["fG"] / FMAX["G"]
        for key, n, r, b in (("aTP", pT, rP, "bT"), ("aTE", eT, rE, "bT")):
            if n:
                self._ew(key, o["pools"][key[1:]] / (n * r ** p[b]))
        l_obs = o["pools"]["LP"] + o["pools"]["LE"] + o["pools"]["LG"]
        l_pred = (pL * p["aLP"] * rP ** p["bL"] + eL * p["aLE"] * rE ** p["bL"]
                  + g * p["aLG"] * (rG ** p["bG"] if g else 0))
        if l_obs > p["bw"]:
            p["bw"] = l_obs
        if l_pred > 1.1 * l_obs and l_obs > 0.8 * p["bw"]:
            self._ew("bw", l_obs)  # saturated: the ceiling, not the coefficients, explains it
        elif l_obs < 0.9 * p["bw"]:
            for key, n, r, b in (("aLP", pL, rP, "bL"), ("aLE", eL, rE, "bL"), ("aLG", g, rG, "bG")):
                if n and r > 0:
                    self._ew(key, o["pools"]["L" + key[2]] / (n * r ** p[b]))
        if o["throttled"]:
            self.cap += self.a * (o["pkg"] - self.cap)
        else:
            self.cap = min(PL1, max(self.cap + 0.5, o["pkg"]))
        self.m += self.a * (o["m"] - self.m)


def observe(st, m, rng):
    n = rng.gauss
    return {
        "pkg": st["pkg"] * (1 + n(0, 0.01)), "core": st["core"] * (1 + n(0, 0.01)),
        "gfx": st["gfx"] * (1 + n(0, 0.01)), "fP": st["fP"] * (1 + n(0, 0.005)),
        "fE": st["fE"] * (1 + n(0, 0.005)), "fG": st["fG"],
        "pools": {k: v * (1 + n(0, 0.03)) for k, v in st["pools"].items()},
        "throttled": st["throttled"], "m": min(1.0, max(0.0, m + n(0, 0.05))),
    }


def transitional(old, new, t, since, d_cpu, d_gpu):
    """Placement actually serving at time t after a switch at `since`: moved CPU units are
    dead for d_cpu s, an iGPU that is being brought up is dead for d_gpu s."""
    cpu = tuple(min(a, b) for a, b in zip(old[:4], new[:4])) if t - since < d_cpu else new[:4]
    g = new[4]
    if new[4] and not old[4] and t - since < d_gpu:
        g = 0
    return cpu + (g,)


def phase_at(scenario, t):
    for dur, m, t_amb in scenario:
        if t < dur:
            return m, t_amb
        t -= dur
    return scenario[-1][1:]


def run_controller(plant, scenario, rng, coupled=True, period=20, hyst=0.03,
                   d_cpu=5, d_gpu=30, horizon=100):
    est = Estimator(prior_midpoint())
    cfg, old, since = NAIVE, NAIVE, -10 ** 9
    total, switches = 0.0, []
    length = sum(d for d, _, _ in scenario)
    for t in range(length):
        m, t_amb = phase_at(scenario, t)
        eff = transitional(old, cfg, t, since, d_cpu, d_gpu)
        st = steady(plant, eff, m, thermal_cap(plant, t_amb))
        total += st["X"]
        est.update(observe(st, m, rng), eff)
        warm = t - since >= max(d_cpu, d_gpu)
        if t % period == period - 1 and warm:
            x_cur = steady(est.p, cfg, est.m, est.cap, coupled)["X"]
            cand, x_new = best_config(est.p, est.m, est.cap, coupled)
            moved = sum(abs(a - b) for a, b in zip(cfg[:4], cand[:4])) / (N_P + N_E)
            cost = x_cur * (moved * d_cpu + (d_gpu if cand[4] > cfg[4] else 0) / 5)
            if cand != cfg and x_new > x_cur * (1 + hyst) and (x_new - x_cur) * horizon > cost:
                old, cfg, since = cfg, cand, t
                switches.append(t)
    return total / length, switches


def static_scores(plant, scenario):
    """Time-averaged X of every placement on the true plant, and the per-phase oracle."""
    length = sum(d for d, _, _ in scenario)
    acc = dict.fromkeys(CONFIGS, 0.0)
    oracle = 0.0
    for dur, m, t_amb in scenario:
        cap = thermal_cap(plant, t_amb)
        best = 0.0
        for c in CONFIGS:
            x = steady(plant, c, m, cap)["X"]
            acc[c] += dur * x
            best = max(best, x)
        oracle += dur * best
    return {c: v / length for c, v in acc.items()}, oracle / length


SCENARIOS = {
    "S1_stationary": [(1800, 0.5, 25.0)],
    "S2_mix_shift": [(300, 0.8, 25.0), (300, 0.2, 25.0)] * 3,
    "S3_thermal_drift": [(300, 0.5, t) for t in (20.0, 24.0, 28.0, 32.0, 36.0, 40.0)],
    "S4_both": [(300, m, t) for m, t in zip((0.8, 0.2) * 3, (20.0, 24.0, 28.0, 32.0, 36.0, 40.0))],
}


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))]


def campaign(n_plants=40, seed=6):
    out = {}
    for name, sc in SCENARIOS.items():
        rows = []
        for i in range(n_plants):
            plant = draw_plant(random.Random(seed * 1000 + i))
            scores, oracle = static_scores(plant, sc)
            tuned = max(scores.values())
            x_full, sw = run_controller(plant, sc, random.Random(i))
            x_abl, _ = run_controller(plant, sc, random.Random(i), coupled=False)
            rows.append({"tuned": tuned, "naive": scores[NAIVE], "oracle": oracle,
                         "ctrl": x_full, "ablated": x_abl,
                         "switches_after_300": sum(1 for s in sw if s >= 300)})
        r = lambda k, base: [row[k] / row[base] for row in rows]
        out[name] = {
            "ctrl/tuned_median": statistics.median(r("ctrl", "tuned")),
            "ctrl/tuned_p10": pct(r("ctrl", "tuned"), 0.10),
            "ctrl/oracle_median": statistics.median(r("ctrl", "oracle")),
            "ctrl/naive_median": statistics.median(r("ctrl", "naive")),
            "ctrl/ablated_median": statistics.median(r("ctrl", "ablated")),
            "tuned/oracle_median": statistics.median(r("tuned", "oracle")),
            "switches_after_300_median": statistics.median(row["switches_after_300"] for row in rows),
            "n": n_plants,
        }
    return out


def verdict(res):
    s1, gain = res["S1_stationary"], ["S2_mix_shift", "S3_thermal_drift", "S4_both"]
    return {
        "AC1": s1["ctrl/tuned_median"] >= 0.97 and s1["ctrl/tuned_p10"] >= 0.93,
        "AC2": sum(res[s]["ctrl/tuned_median"] >= 1.05 for s in gain) >= 2
        and all(res[s]["ctrl/oracle_median"] >= 0.85 for s in gain),
        "AC3": all(v["ctrl/naive_median"] >= 1.0 for v in res.values()),
        "AC4": s1["switches_after_300_median"] <= 3,
        "AC5": max(res["S2_mix_shift"]["ctrl/ablated_median"], res["S4_both"]["ctrl/ablated_median"]) >= 1.02,
    }


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    res = campaign(n)
    print(json.dumps({"results": res, "verdict": verdict(res)}, indent=2))
