#!/usr/bin/env python3
"""limiter_sim.py: simulation that can falsify the limiter-ledger and fixed-cap A/B claims.

Pure stdlib. Models one package: PL1 as an EWMA of power with time constant Tau, PL2, a
first-order thermal RC with a TjMax clamp, frequency from a power law, and decode step time from
a roofline split (compute fraction c scales with 1/f; the rest is memory time that does not).
Sub-step turbo-transition transients and untagged OS hiccups add noise.

Estimators see only the observed stream: step wall time, tag bitmask, 1 C temperature readout
and noisy energy. Estimands come from separate noise-free runs of each arm alone and are used
only to score. Pre-registered criteria: docs/limiter-ledger.md.

  python3 bench/limiter_sim.py [--trials 200] [--seed 1] [--quick]
"""
import argparse
import json
import math
import random
import statistics

# Log-bit numbers in MSR 0x64F (SDM vol. 4 Table 2-39); see limiter_ledger.CORE_PLR.
BIT = {"THERMAL": 17, "PL1": 26, "PL2": 27, "TURBO_ATTEN": 29}
UNCONTROLLED_BITS = 1 << BIT["THERMAL"]


# --------------------------------------------------------------------------- package model

# Illustrative parameters, not measured. "stock" is the 13900T datasheet envelope (35 W base,
# 106 W PL2). "host" is what this host's BIOS sets: PL1 = PL2 = 117 W, Tau 56 s
# (/sys/class/powercap/intel-rapl:0, read 2026-10-16).
# leak: static power rises with temperature (W per C above 50 C). It gives a capped package a
# memory (hotter -> less dynamic power -> lower frequency), so it is the adversarial knob for D4.
SCENARIOS = {
    "stock": dict(pl1=35.0, pl2=106.0, tau=28.0, r_th=0.9, c_th=60.0, t_amb=35.0, leak=0.1),
    "host": dict(pl1=117.0, pl2=117.0, tau=56.0, r_th=0.9, c_th=60.0, t_amb=35.0, leak=0.1),
}
TJMAX = 100.0
P_IDLE = 6.0


class Package:
    def __init__(self, pl1, pl2, tau, r_th, c_th, t_amb, leak=0.0):
        self.pl1, self.pl2, self.tau = pl1, pl2, tau
        self.r_th, self.c_th, self.t_amb, self.leak = r_th, c_th, t_amb, leak
        self.ewma = P_IDLE
        self.temp = t_amb + P_IDLE * r_th
        self.pl1_override = None  # (start_s, end_s, watts): an external PL1 drop
        self.now = 0.0

    def cap(self):
        """(watts, limiter bit) the package allows right now."""
        pl1 = self.pl1
        o = self.pl1_override
        if o and o[0] <= self.now < o[1]:
            pl1 = o[2]
        if self.ewma >= pl1 or pl1 >= self.pl2:
            watts, bit = pl1, BIT["PL1"]
        else:
            watts, bit = self.pl2, BIT["PL2"]
        if self.temp >= TJMAX - 0.25:
            hold = (TJMAX - self.t_amb) / self.r_th
            if hold < watts:
                watts, bit = hold, BIT["THERMAL"]
        return watts, bit

    def advance(self, dt, watts):
        self.ewma += (watts - self.ewma) * (1.0 - math.exp(-dt / self.tau))
        t_inf = self.t_amb + watts * self.r_th
        self.temp = t_inf + (self.temp - t_inf) * math.exp(-dt / (self.r_th * self.c_th))
        self.now += dt

    def static_w(self):
        return P_IDLE + self.leak * max(self.temp - 50.0, 0.0)

    def idle(self, seconds):
        self.advance(seconds, self.static_w())


class Arm:
    """A decode configuration: dynamic power at f_max, step time at f_max, compute fraction."""

    def __init__(self, name, p_dyn, t0, c, f_max=4.0, alpha=2.4):
        self.name, self.p_dyn, self.t0, self.c = name, p_dyn, t0, c
        self.f_max, self.alpha = f_max, alpha

    def operating_point(self, pkg):
        watts, bit = pkg.cap()
        static = pkg.static_w()
        if static + self.p_dyn <= watts:
            return self.f_max, static + self.p_dyn, 0
        f = self.f_max * max((watts - static) / self.p_dyn, 1e-3) ** (1.0 / self.alpha)
        f = max(f, 0.8)
        return f, static + self.p_dyn * (f / self.f_max) ** self.alpha, 1 << bit

    def step_time(self, f):
        return self.t0 * (self.c * self.f_max / f + 1.0 - self.c)


class Noise:
    def __init__(self, sigma=0.02, hiccup_p=0.003, transient_hz=3.0, transient_ms=4.0):
        self.sigma, self.hiccup_p = sigma, hiccup_p
        self.transient_hz, self.transient_ms = transient_hz, transient_ms


QUIET = Noise(0.0, 0.0, 0.0)


def decode_step(pkg, arm, rng, noise):
    """Advance one decode step. Returns the observation and the ground truth for scoring."""
    f, watts, steady_bits = arm.operating_point(pkg)
    t = arm.step_time(f)
    # Sub-step turbo-transition attenuation: -30% frequency for a few ms; logged in bit 29.
    trans_bits, trans_at_end, n = 0, False, 0
    if noise.transient_hz:
        n = _poisson(rng, noise.transient_hz * t)
        for _ in range(n):
            d = min(rng.expovariate(1000.0 / noise.transient_ms), t)
            start = rng.uniform(0.0, t)
            t += d * 0.3 * arm.c
            trans_bits = 1 << BIT["TURBO_ATTEN"]
            trans_at_end |= start + d >= t
    if noise.sigma:
        t *= math.exp(rng.gauss(0.0, noise.sigma))
    if noise.hiccup_p and rng.random() < noise.hiccup_p:
        t *= 1.5  # OS hiccup: slow, invisible to the MSRs
    pkg.advance(t, watts)
    log_bits = steady_bits | trans_bits
    status_bits = steady_bits | ((1 << BIT["TURBO_ATTEN"]) if trans_at_end else 0)
    return {
        "dt": t,
        "log": log_bits,
        "status": status_bits,
        "temp": TJMAX - math.floor(TJMAX - pkg.temp),
        "joules": watts * t * (1.0 + rng.gauss(0.0, 0.005) if noise.sigma else 1.0),
        "t_end": pkg.now,
        "truth_limited": bool(log_bits),
        "truth_transient": n > 0,
    }


# What a real ledger can report. Estimators only ever get these keys.
OBSERVED = ("dt", "log", "temp", "joules")


def _poisson(rng, lam):
    k, p, floor = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p < floor:
            return k
        k += 1


# --------------------------------------------------------------------------- statistics


def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c if abs(c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c if abs(c) > 1e-300 else 1e-300
        de = d * c
        h *= de
        if abs(de - 1.0) < 3e-12:
            break
    return h


def _ibeta(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbt) * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t, df):
    return _ibeta(df / 2.0, 0.5, df / (df + t * t))


def t_quantile(p, df):
    """Upper quantile: P(T > q) = 1 - p."""
    lo, hi = 0.0, 100.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if 1.0 - t_two_sided_p(mid, df) / 2.0 < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def welch(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    se = math.sqrt(va + vb)
    if se == 0:
        return 1.0
    df = (va + vb) ** 2 / (va * va / (len(a) - 1) + vb * vb / (len(b) - 1))
    return t_two_sided_p((mb - ma) / se, df)


def sign_flip_p(diffs):
    """Exact two-sided sign-flip permutation test on paired differences."""
    obs = abs(sum(diffs))
    n = len(diffs)
    hits = 0
    for mask in range(1 << n):
        s = sum(-d if mask >> i & 1 else d for i, d in enumerate(diffs))
        hits += abs(s) >= obs - 1e-12
    return hits / (1 << n)


def paired_ci(diffs, level=0.95):
    m = statistics.fmean(diffs)
    se = statistics.stdev(diffs) / math.sqrt(len(diffs))
    q = t_quantile(1 - (1 - level) / 2, len(diffs) - 1)
    return m, m - q * se, m + q * se


# --------------------------------------------------------------------------- sessions


def run_block(pkg, arm, rng, noise, tokens, settle):
    """One block: `settle` tokens discarded, then `tokens` observed steps."""
    for _ in range(settle):
        decode_step(pkg, arm, rng, noise)
    return [decode_step(pkg, arm, rng, noise) for _ in range(tokens)]


def block_tps(obs):
    return len(obs) / sum(o["dt"] for o in obs)


def schedule(design, quads, rng):
    if design == "ABAB":
        return ["A", "B"] * (2 * quads)
    out = []
    for _ in range(quads):
        out += rng.choice((["A", "B", "B", "A"], ["B", "A", "A", "B"]))
    return out


def session(scn, arms, order, rng, noise, tokens=48, settle=8, gap_s=2.0, cap=None,
            cap_pl1_only=False, warm_s=0.0):
    """Interleaved blocks on one package. The package starts at idle (thermal ramp)."""
    p = dict(SCENARIOS[scn])
    if cap is not None:
        p["pl1"] = cap
        if not cap_pl1_only:
            p["pl2"] = cap
    pkg = Package(**p)
    if warm_s:
        # warm-up on arm A, as a tuned harness would do
        run_block(pkg, arms["A"], rng, noise, int(warm_s / arms["A"].t0), 0)
    blocks = []
    for label in order:
        obs = run_block(pkg, arms[label], rng, noise, tokens, settle)
        blocks.append((label, [{k: o[k] for k in OBSERVED} for o in obs]))
        pkg.idle(gap_s)
    return blocks


def alone_tps(scn, arm, cap=None, cap_pl1_only=False, seconds=900.0):
    """Estimand: sustained tok/s of one arm running alone, noise-free, last third of the run."""
    p = dict(SCENARIOS[scn])
    if cap is not None:
        p["pl1"] = cap
        if not cap_pl1_only:
            p["pl2"] = cap
    pkg = Package(**p)
    rng = random.Random(0)
    keep = []
    while pkg.now < seconds:
        o = decode_step(pkg, arm, rng, QUIET)
        if pkg.now > seconds * 2 / 3:
            keep.append(o["dt"])
    return len(keep) / sum(keep)


def true_effect(scn, arms, cap=None, cap_pl1_only=False):
    return alone_tps(scn, arms["B"], cap, cap_pl1_only) / alone_tps(scn, arms["A"], cap, cap_pl1_only) - 1


# --------------------------------------------------------------------------- estimators


def est_welch(blocks):
    a = [block_tps(o) for lab, o in blocks if lab == "A"]
    b = [block_tps(o) for lab, o in blocks if lab == "B"]
    return statistics.fmean(b) / statistics.fmean(a) - 1, welch(a, b)


def quad_diffs(blocks):
    out = []
    for i in range(0, len(blocks), 4):
        q = blocks[i:i + 4]
        a = statistics.fmean(block_tps(o) for lab, o in q if lab == "A")
        b = statistics.fmean(block_tps(o) for lab, o in q if lab == "B")
        out.append(b / a - 1)
    return out


def est_paired(blocks):
    d = quad_diffs(blocks)
    m, lo, hi = paired_ci(d)
    return m, sign_flip_p(d), lo, hi


def stratum(obs, tau):
    """Observed stratum: majority limiter class, 5 C bin at block start, PL1-EWMA energy bin.

    The EWMA is rebuilt from the observed energy stream only (Tau is readable from RAPL).
    """
    counts = {}
    for o in obs:
        if o["log"] & UNCONTROLLED_BITS:
            k = "therm"
        elif o["log"] & (1 << BIT["PL1"]):
            k = "pl1"
        elif o["log"] & (1 << BIT["PL2"]):
            k = "pl2"
        else:
            k = "none"
        counts[k] = counts.get(k, 0) + 1
    cls = max(counts, key=counts.get)
    tbin = int(obs[0]["temp"] // 5)
    ewma = None
    for o in obs:
        w = o["joules"] / o["dt"]
        ewma = w if ewma is None else ewma + (w - ewma) * (1 - math.exp(-o["dt"] / tau))
    return cls, tbin, int(ewma // 10)


def est_stratified(blocks, tau, rng, perms=400):
    """Mantel-Haenszel-weighted difference within strata + stratified permutation test."""
    keyed = [(stratum(o, tau), lab, block_tps(o)) for lab, o in blocks]
    by = {}
    for k, lab, v in keyed:
        by.setdefault(k, []).append((lab, v))

    def stat(groups):
        num = den = 0.0
        for g in groups.values():
            a = [v for lab, v in g if lab == "A"]
            b = [v for lab, v in g if lab == "B"]
            if a and b:
                w = len(a) * len(b) / (len(a) + len(b))
                num += w * (statistics.fmean(b) / statistics.fmean(a) - 1)
                den += w
        return num / den if den else None

    matched = sum(len(g) for g in by.values()
                  if any(lab == "A" for lab, _ in g) and any(lab == "B" for lab, _ in g))
    coverage = matched / len(keyed)
    obs = stat(by)
    if obs is None:
        return None, 1.0, coverage
    hits = 0
    for _ in range(perms):
        shuffled = {}
        for k, g in by.items():
            labels = [lab for lab, _ in g]
            rng.shuffle(labels)
            shuffled[k] = [(labels[i], v) for i, (_, v) in enumerate(g)]
        s = stat(shuffled)
        hits += s is not None and abs(s) >= abs(obs) - 1e-12
    return obs, (hits + 1) / (perms + 1), coverage


def manipulation_check(blocks, limit=0.05):
    """D4 validity: uncontrolled limiters (thermal/electrical) on <= 5% of steps in each arm."""
    frac, pl1 = {}, {}
    for arm in ("A", "B"):
        steps = [o for lab, obs in blocks if lab == arm for o in obs]
        frac[arm] = sum(bool(o["log"] & UNCONTROLLED_BITS) for o in steps) / len(steps)
        pl1[arm] = sum(bool(o["log"] & (1 << BIT["PL1"])) for o in steps) / len(steps)
    return all(v <= limit for v in frac.values()), frac, pl1


# --------------------------------------------------------------------------- arm pairs

def arm_pairs():
    # Illustrative 13900T-like decode of a 1-3B INT4 model. P: 8 P-cores; E: 16 E-cores.
    return {
        "AA": {"A": Arm("P", 60.0, 0.030, 0.5), "B": Arm("P", 60.0, 0.030, 0.5)},
        "AA+5": {"A": Arm("P", 60.0, 0.030, 0.5), "B": Arm("P+5%", 60.0, 0.030 / 1.05, 0.5)},
        "PvsE": {"A": Arm("P", 60.0, 0.030, 0.5), "B": Arm("E", 25.0, 0.040, 0.5, f_max=3.0)},
        "INT4vsINT8": {"A": Arm("INT4", 60.0, 0.030, 0.3), "B": Arm("INT8", 75.0, 0.050, 0.6)},
    }


# --------------------------------------------------------------------------- experiments


def e1_ledger(seed, runs=10, tokens=512):
    """Tag recall/false-flag: sticky log + clear vs boundary status vs 1 s intervals."""
    rng = random.Random(seed)
    arm = Arm("P", 60.0, 0.030, 0.5)
    noise = Noise()
    res = {"sticky": [0, 0, 0, 0], "status": [0, 0, 0, 0], "interval_1s": [0, 0, 0, 0]}
    trans_hit = {"sticky": [0, 0], "status": [0, 0]}
    uninjected_flags = {"sticky": [0, 0]}
    for _ in range(runs):
        for injected in (True, False):
            pkg = Package(**SCENARIOS["host"])  # PL1 117 W: nothing limits unless injected
            # Warm steady state, so the PL1 EWMA is above the injected limit when it drops.
            pkg.ewma = P_IDLE + arm.p_dyn
            pkg.temp = 85.0
            if injected:
                pkg.pl1_override = (5.0, 9.0, 30.0)
            obs = [decode_step(pkg, arm, rng, noise) for _ in range(tokens)]
            # 1 s interval tagging: flag every token in an interval with any logged event
            ivl = {}
            for o in obs:
                k = int(o["t_end"])
                ivl[k] = ivl.get(k, 0) | o["log"]
            for o in obs:
                tags = {"sticky": o["log"], "status": o["status"], "interval_1s": ivl[int(o["t_end"])]}
                for name, bits in tags.items():
                    tp = o["truth_limited"] and bool(bits)
                    fn = o["truth_limited"] and not bits
                    fp = (not o["truth_limited"]) and bool(bits)
                    tn = (not o["truth_limited"]) and not bits
                    if injected:
                        acc = res[name]
                        for j, v in enumerate((tp, fn, fp, tn)):
                            acc[j] += v
                if o["truth_transient"]:
                    for name in ("sticky", "status"):
                        trans_hit[name][0] += bool(tags[name] & (1 << BIT["TURBO_ATTEN"]))
                        trans_hit[name][1] += 1
                if not injected:
                    uninjected_flags["sticky"][0] += bool(o["log"] & ~(1 << BIT["TURBO_ATTEN"]))
                    uninjected_flags["sticky"][1] += 1
    out = {}
    for name, (tp, fn, fp, tn) in res.items():
        out[name] = {"recall": tp / (tp + fn), "false_flag_rate": fp / (fp + tn)}
    for name, (h, n) in trans_hit.items():
        out[name]["transient_recall"] = h / n
    out["sticky"]["uninjected_power_thermal_flag_rate"] = (
        uninjected_flags["sticky"][0] / uninjected_flags["sticky"][1])
    return out


def cv(xs):
    return statistics.stdev(xs) / statistics.fmean(xs)


def e2_cv(seed, runs=10, tokens=512, warm=32):
    """Original claim (a): CV of clean-token run medians vs all-token run medians."""
    out = {}
    for scn in ("stock", "host"):
        for c in (0.2, 0.5, 0.8):
            rng = random.Random(seed)
            arm = Arm("P", 60.0, 0.030, c)
            pkg = Package(**SCENARIOS[scn])
            all_m, clean_m, kept = [], [], []
            for _ in range(runs):
                obs = [decode_step(pkg, arm, rng, Noise()) for _ in range(tokens)][warm:]
                all_m.append(1 / statistics.median(o["dt"] for o in obs))
                clean = [o["dt"] for o in obs if not o["log"]]
                kept.append(len(clean) / len(obs))
                clean_m.append(1 / statistics.median(clean) if len(clean) >= 16 else float("nan"))
                pkg.idle(10.0)
            ok = all(not math.isnan(x) for x in clean_m)
            out[f"{scn}/c={c}"] = {
                "cv_all": cv(all_m),
                "cv_clean": cv(clean_m) if ok else None,
                "ratio": cv(clean_m) / cv(all_m) if ok else None,
                "kept_fraction": statistics.fmean(kept),
            }
    return out


def ab_trials(scn, pair, design, trials, seed, cap=None, cap_pl1_only=False, quads=8,
              estimators=("welch", "paired", "strat")):
    arms = arm_pairs()[pair]
    tau = SCENARIOS[scn]["tau"]
    rows = []
    for i in range(trials):
        # Separate streams: the randomization must not share a generator with the physics.
        order = schedule(design, quads, random.Random(f"order-{seed}-{i}"))
        rng = random.Random(f"physics-{seed}-{i}")
        blocks = session(scn, arms, order, rng, Noise(), cap=cap, cap_pl1_only=cap_pl1_only)
        row = {}
        if "welch" in estimators:
            row["welch"] = est_welch(blocks)
        if "paired" in estimators:
            row["paired"] = est_paired(blocks)
        if "strat" in estimators:
            row["strat"] = est_stratified(blocks, tau, rng)
        row["manip"] = manipulation_check(blocks)
        rows.append(row)
    return rows


def summarize_trials(rows, truth, alpha=0.05):
    out = {}
    if "welch" in rows[0]:
        out["D1_welch_ABAB"] = {
            "reject_rate": statistics.fmean(r["welch"][1] < alpha for r in rows),
            "mean_est": statistics.fmean(r["welch"][0] for r in rows),
        }
    if "paired" in rows[0]:
        out["paired_ABBA"] = {
            "reject_rate": statistics.fmean(r["paired"][1] < alpha for r in rows),
            "mean_est": statistics.fmean(r["paired"][0] for r in rows),
            "ci_coverage": statistics.fmean(r["paired"][2] <= truth <= r["paired"][3] for r in rows),
            "ci_halfwidth": statistics.fmean((r["paired"][3] - r["paired"][2]) / 2 for r in rows),
        }
    if "strat" in rows[0]:
        ok = [r for r in rows if r["strat"][2] >= 0.7 and r["strat"][0] is not None]
        out["D3_stratified"] = {
            "mean_coverage": statistics.fmean(r["strat"][2] for r in rows),
            "verdict_rate": len(ok) / len(rows),
            "reject_rate_among_verdicts": (statistics.fmean(r["strat"][1] < alpha for r in ok)
                                           if ok else None),
            "mean_est_among_verdicts": statistics.fmean(r["strat"][0] for r in ok) if ok else None,
        }
    out["manipulation_check_pass_rate"] = statistics.fmean(r["manip"][0] for r in rows)
    out["uncontrolled_fraction"] = {
        k: statistics.fmean(r["manip"][1][k] for r in rows) for k in ("A", "B")}
    out["pl1_tagged_fraction"] = {
        k: statistics.fmean(r["manip"][2][k] for r in rows) for k in ("A", "B")}
    out["truth"] = truth
    return out


def e3_e4(trials, seed, caps=(25.0, 30.0, 35.0)):
    # cap 35 W = the stock PL1: a post-hoc check of whether D4 recovers the stock sustained
    # (default-settings) effect. Not one of the pre-registered criteria.
    out = {}
    for scn in ("stock", "host"):
        for pair in ("AA", "AA+5", "PvsE", "INT4vsINT8"):
            arms = arm_pairs()[pair]
            key = f"{scn}/{pair}"
            res = {}
            # Default BIOS settings: D1 (ABAB) and D2/D3 (same ABBA sessions, two estimators).
            truth = true_effect(scn, arms)
            rows = ab_trials(scn, pair, "ABAB", trials, seed, estimators=("welch",))
            res["default"] = summarize_trials(rows, truth)
            rows = ab_trials(scn, pair, "ABBA", trials, seed + 1, estimators=("paired", "strat"))
            res["default"].update(summarize_trials(rows, truth))
            for cap in caps:
                t_cap = true_effect(scn, arms, cap=cap)
                rows = ab_trials(scn, pair, "ABBA", trials, seed + 2, cap=cap, estimators=("paired",))
                res[f"D4_cap{cap:g}"] = summarize_trials(rows, t_cap)
                t_abl = true_effect(scn, arms, cap=cap, cap_pl1_only=True)
                rows = ab_trials(scn, pair, "ABBA", trials, seed + 3, cap=cap, cap_pl1_only=True,
                                 estimators=("paired",))
                res[f"D4abl_pl1only_cap{cap:g}"] = summarize_trials(rows, t_abl)
            out[key] = res
    return out


def perturbed(trials, seed):
    """Recheck the D4 and D3 conclusions with perturbed thermal R, Tau and noise."""
    saved = {k: dict(v) for k, v in SCENARIOS.items()}
    out = {}
    try:
        for name, tweak in (("r_th=0.7,tau/2,leak=0", dict(r_th=0.7, tau_scale=0.5, leak=0.0)),
                            ("r_th=1.2,tau*2,leak=0.3", dict(r_th=1.2, tau_scale=2.0, leak=0.3)),
                            ("r_th=1.5,leak=0.5", dict(r_th=1.5, tau_scale=1.0, leak=0.5))):
            for k in SCENARIOS:
                SCENARIOS[k]["r_th"] = tweak["r_th"]
                SCENARIOS[k]["tau"] = saved[k]["tau"] * tweak["tau_scale"]
                SCENARIOS[k]["leak"] = tweak["leak"]
            res = {}
            for pair in ("AA", "PvsE"):
                arms = arm_pairs()[pair]
                truth = true_effect("stock", arms)
                rows = ab_trials("stock", pair, "ABBA", trials, seed + 7,
                                 estimators=("paired", "strat"))
                res[f"default/{pair}"] = summarize_trials(rows, truth)
                t_cap = true_effect("stock", arms, cap=25.0)
                rows = ab_trials("stock", pair, "ABBA", trials, seed + 8, cap=25.0,
                                 estimators=("paired",))
                res[f"D4_cap25/{pair}"] = summarize_trials(rows, t_cap)
            out[name] = res
    finally:
        for k, v in saved.items():
            SCENARIOS[k].clear()
            SCENARIOS[k].update(v)
    return out


def table(out):
    """Condensed one-line-per-cell view of the E3/E4 and perturbed results."""
    lines = []
    for key, res in out.get("E3_E4", {}).items():
        for cfg, r in res.items():
            line = f"{key:18s} {cfg:22s} truth={r['truth']:+.4f}"
            if "D1_welch_ABAB" in r:
                d1 = r["D1_welch_ABAB"]
                line += f" D1 rej={d1['reject_rate']:.3f} est={d1['mean_est']:+.4f}"
            p = r.get("paired_ABBA")
            if p:
                line += (f" paired rej={p['reject_rate']:.3f} est={p['mean_est']:+.4f}"
                         f" ci_cov={p['ci_coverage']:.2f}")
            d3 = r.get("D3_stratified")
            if d3:
                line += f" D3 strata_cov={d3['mean_coverage']:.2f} verdicts={d3['verdict_rate']:.2f}"
                if d3["reject_rate_among_verdicts"] is not None:
                    line += (f" rej={d3['reject_rate_among_verdicts']:.3f}"
                             f" est={d3['mean_est_among_verdicts']:+.4f}")
            line += f" manip={r['manipulation_check_pass_rate']:.2f}"
            lines.append(line)
    for name, res in out.get("perturbed", {}).items():
        for cfg, r in res.items():
            p = r["paired_ABBA"]
            line = (f"[{name}] {cfg:16s} truth={r['truth']:+.4f} paired est={p['mean_est']:+.4f}"
                    f" rej={p['reject_rate']:.3f} ci_cov={p['ci_coverage']:.2f}")
            d3 = r.get("D3_stratified")
            if d3:
                line += f" D3 strata_cov={d3['mean_coverage']:.2f}"
            lines.append(line)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--quick", action="store_true", help="fewer trials, for CI")
    ap.add_argument("--only", choices=("e1", "e2", "e34", "perturb"))
    ap.add_argument("--table", action="store_true", help="print a condensed table, not JSON")
    a = ap.parse_args(argv)
    trials = 20 if a.quick else a.trials
    out = {}
    if a.only in (None, "e1"):
        out["E1"] = e1_ledger(a.seed)
    if a.only in (None, "e2"):
        out["E2"] = e2_cv(a.seed)
    if a.only in (None, "e34"):
        out["E3_E4"] = e3_e4(trials, a.seed)
    if a.only in (None, "perturb"):
        out["perturbed"] = perturbed(max(trials // 2, 10), a.seed)
    print(table(out) if a.table else json.dumps(out, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
