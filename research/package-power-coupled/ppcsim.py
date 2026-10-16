"""Falsifying simulator for package-power-coupled placement (see DESIGN.md).

    python3 ppcsim.py [N] [seed]      -> JSON summary on stdout

Stdlib only. Every coefficient is an unfitted prior (PRIORS). The controller under
test (FULL) sees only noisy synthetic RAPL traces and prior midpoints; baselines B0/B1
are tuned on the true plant.
"""
import json
import math
import random
import sys

from pcp import PowerModel, Role, Sample, admit, Demoter

FMAX_P, FMIN_P = 5.1, 0.8
FMAX_E, FMIN_E, BASE_E = 3.9, 0.8, 0.8
FMAX_G, FMIN_G = 1.65, 0.3
N_PLAN, N_DRAFT_CPU, N_TOOL, N_EMB = 4, 4, 8, 4
K_STEPS, N_TOK = 4, 200
CAP_GRID = [3.9, 3.0, 2.5, 2.0, 1.6, 1.2, 0.8]  # E-core maxFreq, GHz
BURST_S = 10  # tool burst length; > Demoter.hold_s

PRIORS = {
    "c_p": (6.0, 12.0),     # W per P-core at fmax
    "c_e": (1.8, 3.5),      # W per E-core at fmax
    "c_g": (8.0, 16.0),     # W iGPU at fmax
    "p_static": (5.0, 10.0),
    "alpha": (2.2, 3.0),
    "cap": (35.0, 65.0),    # effective (thermal) package cap, W
    "pi": (0.0, 1.0),       # fraction of iGPU demand the PCU protects (PP1 priority)
    "bw": (45.0, 75.0),     # usable DRAM GB/s
    "bytes_tok": (0.15, 0.5),  # GB read per drafted token
    "rho": (0.7, 6.0),      # iGPU compute rate / bandwidth rate at fmax (log-uniform)
    "kappa": (0.5, 2.0),    # CPU-drafter compute rate / bw rate at fmax (4 P-cores)
    "soft": (2.0, 8.0),     # roofline knee sharpness
    "t_tool": (0.3, 1.5),   # GB/s per busy tool E-core at fmax
    "t_emb": (1.0, 4.0),    # GB/s per embed E-core at fmax
    "t_plan": (0.5, 3.0),   # GB/s per planner P-core at fmax
    "chi": (0.3, 0.9),      # planner step compute fraction
    "step_s": (0.08, 0.25),  # planner step at fmax, s
    "tool_s": (0.5, 3.0),   # tool time per turn at fmax, s
    "beta": (0.6, 1.0),     # tool rate ~ f^beta
    "duty": (0.3, 0.8),     # fraction of time tools are bursting
    "dgpu_x": (2.0, 6.0),   # dGPU drafter rate / iGPU bw-bound rate
}
LOG_UNIFORM = {"rho"}

# Perturbations (added after the seed-1 smoke run, before the pre-registered seeds):
# each pushes the prior toward the idea's premise so the sim cannot secretly deny the
# win either. "favourable" stacks all of them.
VARIANTS = {
    "base": {},
    "io_tools": {"tool_s": (0.05, 0.4), "beta": (0.1, 0.4)},   # tools barely freq-bound
    "no_pp1": {"pi": (0.0, 0.1)},                               # PCU gives iGPU no priority
    "clock_bound": {"rho": (0.3, 1.0), "bytes_tok": (0.05, 0.15)},  # small compute-bound drafter
    "low_alpha": {"alpha": (1.3, 2.0)},                         # near-linear f vs power
}
VARIANTS["favourable"] = {k: v for d in list(VARIANTS.values()) for k, v in d.items()}


def mid(k):
    lo, hi = PRIORS[k]
    return math.sqrt(lo * hi) if k in LOG_UNIFORM else (lo + hi) / 2


def draw(rng):
    p = {}
    for k, (lo, hi) in PRIORS.items():
        p[k] = math.exp(rng.uniform(math.log(lo), math.log(hi))) if k in LOG_UNIFORM \
            else rng.uniform(lo, hi)
    return p


# ---------------------------------------------------------------- plant

def _scaled(f_req, frac, a, fmin):
    return max(fmin, f_req * frac ** (1 / a))


def resolve(p, n_p, f_p, n_e, f_e, g_busy, f_g):
    """PCU: iGPU demand protected by pi, rest shared by proportional power scaling."""
    a = p["alpha"]
    dp = p["c_p"] * n_p * (f_p / FMAX_P) ** a
    de = p["c_e"] * n_e * (f_e / FMAX_E) ** a
    dg = p["c_g"] * g_busy * (f_g / FMAX_G) ** a
    budget = p["cap"] - p["p_static"]
    if dp + de + dg <= budget:
        return dict(f_p=f_p, f_e=f_e, f_g=f_g, pkg=p["p_static"] + dp + de + dg,
                    throttled=False)
    prot = min(p["pi"] * dg, budget)

    def power(s):
        fp_, fe_ = _scaled(f_p, s, a, FMIN_P), _scaled(f_e, s, a, FMIN_E)
        gfrac = (prot + s * (1 - p["pi"]) * dg) / dg if dg > 0 else 0
        fg_ = _scaled(f_g, min(1.0, gfrac), a, FMIN_G) if dg > 0 else f_g
        w = (p["c_p"] * n_p * (fp_ / FMAX_P) ** a + p["c_e"] * n_e * (fe_ / FMAX_E) ** a
             + p["c_g"] * g_busy * (fg_ / FMAX_G) ** a)
        return w, fp_, fe_, fg_

    lo, hi = 0.0, 1.0
    for _ in range(50):
        m = (lo + hi) / 2
        if power(m)[0] > budget:
            hi = m
        else:
            lo = m
    w, fp_, fe_, fg_ = power(lo)
    return dict(f_p=fp_, f_e=fe_, f_g=fg_, pkg=p["p_static"] + w, throttled=True)


def drafter_rate(p, dev, f, other_traffic, cbound=False):
    """tok/s. Soft roofline of compute (~f) and bandwidth left over by other roles."""
    if dev == "dgpu":
        return p["dgpu_x"] * p["bw"] / p["bytes_tok"]
    r_bw0 = p["bw"] / p["bytes_tok"]
    if dev == "igpu":
        comp = p["rho"] * r_bw0 * f / FMAX_G
    else:  # pcore
        comp = p["kappa"] * r_bw0 * f / FMAX_P
    if cbound:
        return comp
    bw_rate = max(1e-6, p["bw"] - other_traffic) / p["bytes_tok"]
    s = p["soft"]
    return (comp ** -s + bw_rate ** -s) ** (-1 / s)


def state(p, plan, e_cap, burst, traffic_e=None):
    """Steady state of one PCU phase. plan: dict role->device.
    traffic_e: E-core maxFreq used for DRAM traffic only (channel counterfactuals);
    power and tool speed always use e_cap."""
    draft_cpu = plan["drafter"] == "pcore"
    n_p = N_PLAN + (N_DRAFT_CPU if draft_cpu else 0)
    emb_on_e = plan["embed"] == "ecore"
    n_e = (N_TOOL if burst else 0) + (N_EMB if emb_on_e else 0)
    g_busy = 1.0 if plan["drafter"] == "igpu" else 0.0
    s = resolve(p, n_p, FMAX_P, n_e, e_cap, g_busy, FMAX_G)
    f_tr = s["f_e"]
    if traffic_e is not None and traffic_e != e_cap:
        f_tr = resolve(p, n_p, FMAX_P, n_e, traffic_e, g_busy, FMAX_G)["f_e"]
    traffic = (((N_TOOL * p["t_tool"]) if burst else 0)
               + (N_EMB * p["t_emb"] if emb_on_e else 0)) * f_tr / FMAX_E
    traffic += N_PLAN * p["t_plan"] * s["f_p"] / FMAX_P
    f_d = s["f_g"] if plan["drafter"] == "igpu" else s["f_p"]
    rate = drafter_rate(p, plan["drafter"], f_d, traffic, cbound=p.get("_cbound", False))
    step = p["step_s"] * (p["chi"] * FMAX_P / s["f_p"] + 1 - p["chi"])
    return dict(s, rate=rate, step=step, traffic=traffic)


def evaluate(p, plan, e_cap, jitter, **kw):
    b = state(p, plan, e_cap, True, **kw)
    c = state(p, plan, e_cap, False, **kw)
    d = p["duty"]
    rate = d * b["rate"] + (1 - d) * c["rate"]
    step = d * b["step"] + (1 - d) * c["step"]
    tool = p["tool_s"] * (FMAX_E / b["f_e"]) ** p["beta"]
    turn = K_STEPS * step + N_TOK / rate + tool
    pkg = d * b["pkg"] + (1 - d) * c["pkg"]
    lat = sorted((b["step"] if u < d else c["step"]) * z for u, z in jitter)
    return dict(rate=rate, p95=lat[int(0.95 * len(lat))], turn_rate=1 / turn,
                energy=pkg * turn, f_g=d * b["f_g"] + (1 - d) * c["f_g"],
                burst_throttled=b["throttled"], burst_pkg=b["pkg"])


# ---------------------------------------------------------------- policies

def plans(have_dgpu):
    out = []
    for dr in (["igpu", "pcore"] + (["dgpu"] if have_dgpu else [])):
        for em in (["ecore"] + (["dgpu"] if have_dgpu else [])):
            out.append({"drafter": dr, "embed": em})
    return out


def tuned_default(p, have_dgpu, jitter):
    best = None
    for pl in plans(have_dgpu):
        r = evaluate(p, pl, FMAX_E, jitter)
        if best is None or r["turn_rate"] > best[1]["turn_rate"]:
            best = (pl, r)
    return best


def tuned_static_cap(p, plan, base, jitter):
    """Oracle static knob: best E cap on the true plant, energy/turn not above B0."""
    best = (FMAX_E, base)
    for cap in CAP_GRID:
        r = evaluate(p, plan, cap, jitter)
        if r["energy"] <= base["energy"] * 1.0 + 1e-9 and r["turn_rate"] > best[1]["turn_rate"]:
            best = (cap, r)
    return best


def synth_traces(p, rng, noise=0.03):
    """What the existing RAPL benchmark would have recorded: varied loads, noisy."""
    out = []
    a = p["alpha"]
    for _ in range(40):
        n_p = rng.choice([0, 2, 4, 8])
        n_e = rng.choice([0, 4, 8, 12])
        g = rng.choice([0.0, 1.0])
        f_p = rng.uniform(1.5, FMAX_P)
        f_e = rng.uniform(1.2, FMAX_E)
        f_g = rng.uniform(0.6, FMAX_G)
        cw = p["c_p"] * n_p * (f_p / FMAX_P) ** a + p["c_e"] * n_e * (f_e / FMAX_E) ** a
        gw = p["c_g"] * g * (f_g / FMAX_G) ** a
        def nz(x):
            return x * (1 + rng.gauss(0, noise))
        cw, gw = nz(cw) + 0.01, nz(gw) + 0.01
        out.append(Sample(n_p, f_p, n_e, f_e, g, f_g, cw + gw + nz(p["p_static"]), cw, gw))
    return out


def controller_view(p, fit, rng, cbound):
    """The controller's belief: fitted power, noisy cap, prior-midpoint perf params."""
    q = {k: mid(k) for k in PRIORS}
    q.update(c_p=fit.c_p, c_e=fit.c_e, c_g=fit.c_g, p_static=fit.p_static + fit.g0,
             alpha=fit.alpha, cap=p["cap"] * (1 + rng.gauss(0, 0.05)))
    q["_cbound"] = cbound  # the idea as stated: drafter rate tracks iGPU clock only
    return q


def oracle(p, have_dgpu, base, jitter):
    """Upper bound on the true plant, energy/turn not above B0: the turn-rate-best
    (plan, E cap) and, separately, the highest drafter tok/s any such pair reaches."""
    best, max_rate = None, 0.0
    for pl in plans(have_dgpu):
        for cap in [FMAX_E] + CAP_GRID:
            r = evaluate(p, pl, cap, jitter)
            if r["energy"] > base["energy"] + 1e-9:
                continue
            max_rate = max(max_rate, r["rate"])
            if best is None or r["turn_rate"] > best[2]["turn_rate"]:
                best = (pl, cap, r)
    return best + (max_rate,)


def full(p, rng, have_dgpu, jitter, cbound=False, demote=True, use_cap=True, tp_only=False,
         literal_demote=False):
    fit = PowerModel(FMAX_P, FMAX_E, FMAX_G).fit(synth_traces(p, rng))
    q = controller_view(p, fit, rng, cbound)
    roles = [Role("planner", 99, ["pcore"]),
             Role("drafter", 3, ["igpu", "dgpu", "pcore"]),
             Role("tools", 2, ["ecore"], cores=N_TOOL),
             Role("embed", 1, ["dgpu", "ecore"], sustained=True, cores=N_EMB)]

    def pred_w(rs):
        pl = {r.name: r.placed for r in rs}
        return evaluate(q, {"drafter": pl["drafter"], "embed": pl["embed"]},
                        FMAX_E, jitter[:200])["burst_pkg"]

    admitted, _ = admit(roles, pred_w, q["cap"], have_dgpu)
    plan = {"drafter": roles[1].placed, "embed": roles[3].placed}
    if tp_only:
        cap = BASE_E  # nri-plugins: lower classes' symbolic "turbo" -> base
    elif use_cap:
        base_q = evaluate(q, plan, FMAX_E, jitter[:200])
        cap, best = FMAX_E, base_q["turn_rate"]
        for c in CAP_GRID:
            r = evaluate(q, plan, c, jitter[:200])
            if r["energy"] <= base_q["energy"] and r["turn_rate"] > best * 1.01:
                cap, best = c, r["turn_rate"]
    else:
        cap = FMAX_E
    res = evaluate(p, plan, cap, jitter)
    if demote and res["burst_throttled"] and BURST_S > Demoter.hold_s:
        d = Demoter(off_package_only=not literal_demote)
        for _ in range(BURST_S):
            name = d.tick(roles, res["burst_pkg"], p["cap"], True, have_dgpu)
            if name:
                break
        plan = {"drafter": roles[1].placed, "embed": roles[3].placed}
        res = evaluate(p, plan, cap, jitter)
    return plan, cap, res, admitted


# ---------------------------------------------------------------- experiment

def median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def run(n, seed):
    rng = random.Random(seed)
    rows = {"D0": [], "D2": []}
    for _ in range(n):
        p = draw(rng)
        jitter = [(rng.random(), math.exp(rng.gauss(0, 0.15))) for _ in range(1000)]
        for scen, dg in (("D0", False), ("D2", True)):
            sub = random.Random(rng.random())
            b0_plan, b0 = tuned_default(p, dg, jitter)
            b1_cap, b1 = tuned_static_cap(p, b0_plan, b0, jitter)
            st = sub.getstate()
            _, _, o, o_max_rate = oracle(p, dg, b0, jitter)
            f_plan, f_cap, f, f_ok = full(p, sub, dg, jitter)
            sub.setstate(st)
            _, _, a_cb, _ = full(p, sub, dg, jitter, cbound=True)
            sub.setstate(st)
            _, _, a_nd, _ = full(p, sub, dg, jitter, demote=False)
            sub.setstate(st)
            _, _, a_tp, _ = full(p, sub, dg, jitter, tp_only=True)
            sub.setstate(st)
            _, _, a_lit, _ = full(p, sub, dg, jitter, literal_demote=True)
            row = dict(pi=p["pi"], rho=p["rho"], b0_drafter=b0_plan["drafter"],
                       f_drafter=f_plan["drafter"], f_cap=f_cap, b1_cap=b1_cap,
                       drafter_gain=f["rate"] / b0["rate"] - 1,
                       p95_cut=1 - f["p95"] / b0["p95"],
                       energy_chg=f["energy"] / b0["energy"] - 1,
                       turn_vs_b0=f["turn_rate"] / b0["turn_rate"],
                       turn_vs_b1=f["turn_rate"] / b1["turn_rate"],
                       turn_cbound_vs_b0=a_cb["turn_rate"] / b0["turn_rate"],
                       turn_nodemote_vs_b0=a_nd["turn_rate"] / b0["turn_rate"],
                       turn_tponly_vs_b0=a_tp["turn_rate"] / b0["turn_rate"],
                       turn_literal_demote_vs_b0=a_lit["turn_rate"] / b0["turn_rate"],
                       f_admitted=f_ok,
                       oracle_drafter_gain=o["rate"] / b0["rate"] - 1,
                       oracle_max_drafter_gain=o_max_rate / b0["rate"] - 1,
                       oracle_p95_cut=1 - o["p95"] / b0["p95"],
                       oracle_energy_chg=o["energy"] / b0["energy"] - 1,
                       oracle_turn_vs_b0=o["turn_rate"] / b0["turn_rate"])
            # channel decomposition on a fixed iGPU-drafter plan, at the best static
            # cap for that plan on the true plant (so the channel test does not depend
            # on whether FULL happened to pick a cap)
            ig = {"drafter": "igpu", "embed": "ecore"}
            base = evaluate(p, ig, FMAX_E, jitter)
            caps = [c for c in CAP_GRID if c < FMAX_E]
            c_star = max(caps, key=lambda c: evaluate(p, ig, c, jitter)["rate"])
            both = evaluate(p, ig, c_star, jitter)
            pw = evaluate(p, ig, c_star, jitter, traffic_e=FMAX_E)
            dram = evaluate(p, ig, FMAX_E, jitter, traffic_e=c_star)
            row.update(ig_gain=both["rate"] / base["rate"] - 1,
                       ig_power_only=pw["rate"] / base["rate"] - 1,
                       ig_dram_only=dram["rate"] / base["rate"] - 1,
                       ig_fg_rise=both["f_g"] / base["f_g"] - 1)
            rows[scen].append(row)
    return rows


def summarize(rows):
    out = {}
    for scen, rs in rows.items():
        def m(k, rs=rs):
            return round(median([r[k] for r in rs]), 4)
        s = {k: m(k) for k in ("drafter_gain", "p95_cut", "energy_chg", "turn_vs_b0",
                               "turn_vs_b1", "turn_cbound_vs_b0", "turn_nodemote_vs_b0",
                               "turn_tponly_vs_b0", "turn_literal_demote_vs_b0",
                               "oracle_drafter_gain", "oracle_max_drafter_gain",
                               "oracle_p95_cut",
                               "oracle_energy_chg", "oracle_turn_vs_b0", "ig_gain",
                               "ig_power_only", "ig_dram_only", "ig_fg_rise")}
        s["b0_drafter_dgpu_frac"] = round(sum(r["b0_drafter"] == "dgpu" for r in rs) / len(rs), 3)
        s["b0_drafter_igpu_frac"] = round(sum(r["b0_drafter"] == "igpu" for r in rs) / len(rs), 3)
        s["full_admitted_frac"] = round(sum(r["f_admitted"] for r in rs) / len(rs), 3)
        s["full_capped_frac"] = round(sum(r["f_cap"] < FMAX_E for r in rs) / len(rs), 3)
        s["clock_up_tok_flat_frac"] = round(sum(
            r["ig_fg_rise"] >= 0.10 and r["ig_gain"] < 0.05 for r in rs) / len(rs), 3)
        s["ig_gain_ge20_frac"] = round(sum(r["ig_gain"] >= 0.20 for r in rs) / len(rs), 3)
        s["ig_power_ge20_frac"] = round(sum(r["ig_power_only"] >= 0.20 for r in rs) / len(rs), 3)
        reg = [r for r in rs if r["pi"] < 0.3 and r["rho"] < 1.5]
        if reg:
            s["regime_n"] = len(reg)
            s["regime_drafter_gain"] = round(median([r["drafter_gain"] for r in reg]), 4)
            s["regime_ig_power_only"] = round(median([r["ig_power_only"] for r in reg]), 4)
        out[scen] = s
    d0, d2 = out["D0"], out["D2"]
    out["verdict"] = {
        "AC1_drafter": d0["drafter_gain"] >= 0.20,
        "AC1_p95": d0["p95_cut"] >= 0.10,
        "AC1_energy": d0["energy_chg"] <= 0.0,
        "AC2_beats_static_cap": d0["turn_vs_b1"] >= 1.03,
        "AC3_power_channel": d0["ig_power_only"] >= 0.5 * d0["ig_gain"] and d0["ig_gain"] > 0,
        "AC4_dgpu_irrelevant": d2["b0_drafter_dgpu_frac"] >= 0.90 and d2["turn_vs_b0"] <= 1.02,
        "AC5_freq_falsifier_invalid": d0["clock_up_tok_flat_frac"] > 0.25,
    }
    return out


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 11
    variant = sys.argv[3] if len(sys.argv) > 3 else "base"
    PRIORS.update(VARIANTS[variant])
    print(json.dumps({"n": n, "seed": seed, "variant": variant,
                      **summarize(run(n, seed))}, indent=1))
