#!/usr/bin/env python3
"""Falsification simulation for scripts/k3d-gpu-sampler.

Each part has a GENERATOR (the synthetic world) and an OBSERVER (the sampler).
The observer only sees counters the generator emits: pod busy seconds, RAPL
domain joules, measured joules per event. Physics parameters, true placements
and true energies stay inside the generator. Generator forms differ on purpose
from the sampler's model forms:
  * the rail voltage curve has a Vmin floor and curvature, while the sampler
    fits (a + f)^2;
  * leakage scales with rail voltage, while the sampler fits a constant idle term;
  * iGPU clients draw different power per busy second, while the sampler splits
    by busy time;
  * sentinel arrivals are bursty clusters with a jittered idle timer, while the
    sampler fits a Poisson single-timer form.

Every parameter is redrawn per seed, and the verdict thresholds are perturbed
by +-30%. Ablations remove the element each claim rests on.

Usage: python3 tests/sampler_sim.py [--seeds N]   (exit 1 if a criterion fails)
"""

import argparse
import importlib.machinery
import importlib.util
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sampler_criteria as C  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("sampler", os.path.join(HERE, "..", "scripts", "k3d-gpu-sampler"))
_spec = importlib.util.spec_from_loader("sampler", _loader)
S = importlib.util.module_from_spec(_spec)
_loader.exec_module(S)

RESULTS = []


def record(name, ok, detail):
    RESULTS.append((name, ok, detail))
    print("%-4s %-46s %s" % ("PASS" if ok else "FAIL", name, detail))


def frac(xs):
    xs = list(xs)
    return sum(1 for x in xs if x) / len(xs)


# =========================================================================== (a) engine proof

FAULTS = {
    # name: (claim, expected class, how the work actually lands)
    "no /dev/dri in container":        ("igpu", "FALLBACK", "cpu"),
    "OpenVINO GPU plugin missing":     ("igpu", "FALLBACK", "cpu"),
    "NPU driver absent":               ("npu",  "FALLBACK", "cpu"),
    "wrong cores label":               ("ecore", "FALLBACK", "pcore_heavy"),
    "llama.cpp built without SYCL":    ("igpu", "FALLBACK", "cpu_blip"),
    "Ollama without Intel GPU backend": ("igpu", "FALLBACK", "cpu_blip"),
    "partial offload (layers on CPU)": ("igpu", "NOT_PROVEN", "partial"),
}

# What a baseline sees. 'naive' = device-plugin allocation + configured device
# label. 'competent' adds the runtime's own report of where it ran
# (ov::execution_devices, llama.cpp backend log line, Ollama compute log line).
# The 'competent' column follows the review's description and is NOT verified
# against primary sources; it is listed so the oracle is not compared against
# a strawman.
BASELINE_CATCHES = {
    "no /dev/dri in container":         {"naive": True,  "competent": True},   # no allocation
    "OpenVINO GPU plugin missing":      {"naive": False, "competent": True},
    "NPU driver absent":                {"naive": True,  "competent": True},   # no allocation
    "wrong cores label":                {"naive": False, "competent": False},  # runtimes do not report core type
    "llama.cpp built without SYCL":     {"naive": False, "competent": True},
    "Ollama without Intel GPU backend": {"naive": False, "competent": True},
    "partial offload (layers on CPU)":  {"naive": False, "competent": False},  # self-report says GPU
}

CLEAN = ("igpu", "npu", "pcore", "ecore")


def gen_stage(rng, claim, landing, W, neighbour_gpu_s, alpha_hi=0.4):
    """True busy seconds for one window, then the counters an observer sees."""
    d = rng.uniform(0.4, 0.9) * W          # compute the stage needs on its intended engine
    alpha = rng.uniform(0.05, alpha_hi)    # host-thread CPU per accelerator second
    slow = rng.uniform(1.5, 3.0)           # CPU takes longer than the accelerator
    mix = rng.uniform(0.3, 0.8)            # unpinned CPU work: share landing on P-cores
    t = {"pcore": 0.0, "ecore": 0.0, "igpu": 0.0, "npu": 0.0}
    if landing == "clean":
        if claim in ("pcore", "ecore"):
            t[claim] = d
            t["ecore" if claim == "pcore" else "pcore"] = d * rng.uniform(0, 0.04)
        else:
            t[claim] = d
            t["pcore"], t["ecore"] = alpha * d * mix, alpha * d * (1 - mix)
    elif landing in ("cpu", "cpu_blip"):
        t["pcore"], t["ecore"] = d * slow * mix, d * slow * (1 - mix)
        if landing == "cpu_blip":           # device enumeration touches the GPU briefly
            t["igpu"] = rng.uniform(0, 0.01) * W
    elif landing == "pcore_heavy":
        p = rng.uniform(0.6, 0.9)
        t["pcore"], t["ecore"] = d * p, d * (1 - p)
    elif landing == "partial":
        f = rng.uniform(0.2, 0.5)
        t["igpu"] = f * d
        cpu = (1 - f) * d * slow + alpha * f * d
        t["pcore"], t["ecore"] = cpu * mix, cpu * (1 - mix)
    noisy = {k: v * max(0.0, rng.gauss(1, 0.05)) for k, v in t.items()}
    pod_view = dict(noisy)                                   # fdinfo + cgroup perf + exclusive NPU
    pmu_view = dict(noisy, igpu=noisy["igpu"] + neighbour_gpu_s)  # i915 PMU is system-wide
    return pod_view, pmu_view


def perturbed_thresholds(rng):
    """+-30% on each threshold; cores_proven sits near 1, so its margin (1 - x)
    is perturbed instead of the value itself."""
    th = {k: v * rng.uniform(0.7, 1.3) for k, v in S.DEFAULT_THRESHOLDS.items()}
    th["cores_proven"] = 1 - (1 - S.DEFAULT_THRESHOLDS["cores_proven"]) * rng.uniform(0.7, 1.3)
    return th


def expected(v, cls):
    return v == "FALLBACK" if cls == "FALLBACK" else v != "PROVEN"


def part_a(seeds):
    print("\n(a) engine-proof verdict: %d seeds, %d faults + %d clean runs each" % (seeds, len(FAULTS), C.CLEAN_RUNS))
    W = 10.0
    seed_ok, pmu_misses, noceil_misses, clean_fa, nb_load = [], [], [], [], []
    fault_hits = {f: 0 for f in FAULTS}
    for s in range(seeds):
        rng = random.Random(1000 + s)
        th = perturbed_thresholds(rng)
        neighbour = rng.uniform(0, 0.8) * W
        ok, pm, nm = True, 0, 0
        for name, (claim, cls, landing) in FAULTS.items():
            pod, pmu = gen_stage(rng, claim, landing, W, neighbour)
            v = S.verdict(claim, pod, W, th)
            hit = expected(v, cls)
            fault_hits[name] += hit
            ok &= hit
            pm += not expected(S.verdict(claim, pmu, W, th), cls)
            nm += not expected(S.verdict(claim, pod, W, th, ceiling=False), cls)
        fa = 0
        for _ in range(C.CLEAN_RUNS):
            claim = rng.choice(CLEAN)
            pod, _ = gen_stage(rng, claim, "clean", W, neighbour)
            fa += S.verdict(claim, pod, W, th) != "PROVEN"
        clean_fa.append(fa)
        seed_ok.append(ok and fa <= C.CLEAN_FALSE_ALARMS_MAX)
        pmu_misses.append(pm)
        nb_load.append(neighbour / W)
        noceil_misses.append(nm)
    record("A1 oracle 7/7 faults, 0 clean false alarms", frac(seed_ok) >= C.SEED_PASS_FRACTION,
           "held on %.0f%% of seeds; worst clean false alarms %d/%d" % (100 * frac(seed_ok), max(clean_fa), C.CLEAN_RUNS))
    for name, hits in fault_hits.items():
        print("       %-34s caught %3d/%d   naive=%-5s competent=%s (assumed)" % (
            name, hits, seeds, BASELINE_CATCHES[name]["naive"], BASELINE_CATCHES[name]["competent"]))
    print("       baselines: naive catches %d/7, competent self-report catches %d/7 (assumed coverage)" % (
        sum(b["naive"] for b in BASELINE_CATCHES.values()), sum(b["competent"] for b in BASELINE_CATCHES.values())))
    # Where it breaks: runtimes whose host thread spin-waits on the accelerator.
    rng = random.Random(4242)
    spin = []
    for _ in range(2000):
        pod, _ = gen_stage(rng, "igpu", "clean", W, 0.0, alpha_hi=1.5)
        spin.append(S.verdict("igpu", pod, W) != "PROVEN")
    print("       stress (report only): clean iGPU runs with host-thread CPU up to 1.5x GPU time: "
          "%.0f%% not PROVEN" % (100 * frac(spin)))
    record("A2 ablate: system-wide i915 PMU for pod GPU",
           frac(m >= C.ABLATE_SYSTEMWIDE_PMU_MIN_MISSES for m in pmu_misses) >= C.SEED_PASS_FRACTION,
           "seeds with >=1 miss %.0f%%, mean misses %.2f" % (100 * frac(m >= 1 for m in pmu_misses), sum(pmu_misses) / seeds))
    for lo, hi in ((0, 0.1), (0.1, 0.3), (0.3, 0.8)):
        sel = [m for m, x in zip(pmu_misses, nb_load) if lo <= x < hi]
        if sel:
            print("       PMU ablation, neighbour GPU load %2.0f-%2.0f%%: seeds with >=1 miss %3.0f%% (n=%d)" % (
                100 * lo, 100 * hi, 100 * frac(m >= 1 for m in sel), len(sel)))
    record("A3 ablate: no cross-engine ceiling",
           frac(m >= C.ABLATE_NO_CEILING_MIN_MISSES for m in noceil_misses) >= C.SEED_PASS_FRACTION,
           "seeds with >=1 miss %.0f%%, mean misses %.2f" % (
               100 * frac(m >= 1 for m in noceil_misses), sum(noceil_misses) / seeds))


# =========================================================================== (b) joules per engine

class Chip:
    """Synthetic hybrid client die: shared VccIA rail, P/E cores, iGPU on PP1."""

    def __init__(self, rng):
        self.vmin = rng.uniform(0.65, 0.80)
        self.v0 = rng.uniform(0.40, 0.60)
        self.k = rng.uniform(0.08, 0.14)
        self.q = rng.uniform(0.0, 0.012)
        self.c = {"pcore": rng.uniform(2.2, 3.2), "ecore": rng.uniform(0.9, 1.6)}  # J / (Gcycle V^2)
        self.leak = rng.uniform(0.5, 2.0)        # W / V^2 uncore-side, while any core is awake
        self.core_leak = {"pcore": rng.uniform(0.4, 1.2), "ecore": rng.uniform(0.1, 0.4)}  # W / V^2 per busy core
        self.core_idle = rng.uniform(0.3, 1.0)   # W
        self.gt_idle = rng.uniform(0.05, 0.3)    # W on PP1
        self.rng = rng

    def v(self, f):
        return max(self.vmin, self.v0 + self.k * f + self.q * f * f)

    def pp0(self, window_s, f, cyc):
        """Core-domain joules; returns (measured, per-type dynamic truth)."""
        act = [f[t] for t in cyc if cyc[t] > 0]
        fr = max(act) if act else 0.0
        v2 = self.v(fr) ** 2
        # per type: switching energy per cycle plus leakage per busy core-second,
        # both at the shared rail voltage
        dyn = {t: (self.c[t] * cyc[t] + self.core_leak[t] * cyc[t] / f[t]) * v2 if cyc[t] else 0.0 for t in cyc}
        e = sum(dyn.values()) + (self.leak * v2 if act else 0.0) * window_s + self.core_idle * window_s
        e = e * self.rng.gauss(1, 0.005)
        return round(e / 61e-6) * 61e-6, dyn


def calibration(chip, rng):
    rows = []
    for _ in range(60):
        kind = rng.choice(("p", "e", "both"))
        f = {"pcore": rng.uniform(1.0, 5.3), "ecore": rng.uniform(0.8, 3.9)}
        cyc = {"pcore": 0.0, "ecore": 0.0}
        if kind in ("p", "both"):
            cyc["pcore"] = f["pcore"] * rng.randint(1, 8) * rng.uniform(0.5, 1.0)
        if kind in ("e", "both"):
            cyc["ecore"] = f["ecore"] * rng.randint(1, 16) * rng.uniform(0.5, 1.0)
        e, _ = chip.pp0(1.0, f, cyc)
        rows.append({"window_s": 1.0, "pp0_j": e, "f": seen_clock(rng, f), "cyc": cyc})
    return rows


def seen_clock(rng, f):
    """Observer's clock reading: window average with 3% error (turbo jitter)."""
    return {t: v * rng.gauss(1, 0.03) for t, v in f.items()}


def part_b(seeds):
    print("\n(b) joules per engine: %d seeds, concurrent turn = LLM on P-cores + embedder on E-cores"
          " + iGPU stage + GPU neighbour" % seeds)
    igpu_err, cts_err, op_err_p, op_err_e, cb_err_e, iso_gap, coupling, vif_ok = [], [], [], [], [], [], [], []
    for s in range(seeds):
        rng = random.Random(5000 + s)
        chip = Chip(rng)
        model = S.fit_core_model(calibration(chip, rng))
        W = 5.0
        f = {"pcore": rng.uniform(3.0, 5.3), "ecore": rng.uniform(1.8, 3.2)}
        llm = {"pcore": f["pcore"] * 6 * W * rng.uniform(0.6, 1.0), "ecore": 0.0}
        emb = {"pcore": 0.0, "ecore": f["ecore"] * 8 * W * rng.uniform(0.5, 1.0)}
        gpu_host = {"pcore": 0.0, "ecore": f["ecore"] * 1 * W * rng.uniform(0.05, 0.3)}
        tot = {t: llm[t] + emb[t] + gpu_host[t] for t in ("pcore", "ecore")}
        pp0, _ = chip.pp0(W, f, tot)
        v2 = chip.v(max(f.values())) ** 2

        def stage(t, cyc, v2):
            return (chip.c[t] * cyc + chip.core_leak[t] * cyc / f[t]) * v2
        truth = {"llm": stage("pcore", llm["pcore"], v2), "emb": stage("ecore", emb["ecore"], v2)}
        est = S.attribute_cores(model, {"window_s": W, "pp0_j": pp0, "f": seen_clock(rng, f), "cyc": tot},
                                {"llm": llm, "emb": emb, "gpu_host": gpu_host}, C.VIF_REFUSE)
        if "refused" in est:
            op_err_p.append(1.0)
            op_err_e.append(1.0)
        else:
            op_err_p.append(abs(est["llm"]["pcore_j"] - truth["llm"]) / truth["llm"])
            op_err_e.append(abs(est["emb"]["ecore_j"] - truth["emb"]) / truth["emb"])
            coupling.append(est["emb"]["ecore_rail_coupling_j"] / est["emb"]["ecore_j"])

        # Constant-beta model fitted on isolated single-stage runs of these very stages.
        iso_p, _ = chip.pp0(W, f, {"pcore": llm["pcore"], "ecore": 0.0})
        iso_e, _ = chip.pp0(W, f, {"pcore": 0.0, "ecore": emb["ecore"]})
        idle_j = chip.core_idle * W
        be = (iso_e - idle_j) / emb["ecore"]
        cb_err_e.append(abs(be * emb["ecore"] - truth["emb"]) / truth["emb"])
        iso_truth_e = stage("ecore", emb["ecore"], chip.v(f["ecore"]) ** 2)
        iso_gap.append(truth["emb"] / iso_truth_e - 1)

        # iGPU: PP1 split by fdinfo busy share; clients draw different power per busy second.
        pod_busy, nb_busy = rng.uniform(1.0, 4.0), rng.uniform(0.0, 0.5) * W
        p_pod, p_nb = rng.uniform(3, 8), rng.uniform(3, 8)
        pp1 = chip.gt_idle * W + pod_busy * p_pod + nb_busy * p_nb
        floor_cal = chip.gt_idle * rng.uniform(0.9, 1.1)
        got = S.igpu_joules(pp1, pod_busy, pod_busy + nb_busy, floor_cal, W)["pod_j"]
        igpu_err.append(abs(got - pod_busy * p_pod) / (pod_busy * p_pod))

        # Baseline: package joules split by CPU time (Scaphandre / Kepler ratio style).
        pkg = pp0 + pp1
        cpu_s = {"llm": llm["pcore"] / f["pcore"], "emb": emb["ecore"] / f["ecore"],
                 "gpu_host": gpu_host["ecore"] / f["ecore"]}
        split = S.cpu_time_split(pkg, cpu_s)
        gpu_stage_truth = pod_busy * p_pod + stage("ecore", gpu_host["ecore"], v2)
        cts_err.append(abs(split["gpu_host"] - gpu_stage_truth) / gpu_stage_truth)

        # Identifiability gate: a calibration where P and E are always co-active at a fixed ratio.
        col = []
        for _ in range(40):
            ff = {"pcore": rng.uniform(1.0, 5.3), "ecore": rng.uniform(0.8, 3.9)}
            sc = rng.uniform(0.5, 4.0)
            cyc = {"pcore": 4 * sc * rng.gauss(1, 0.01), "ecore": 3 * sc * rng.gauss(1, 0.01)}
            ff["ecore"] = ff["pcore"]          # same clock: one rail state, fixed ratio
            e, _ = chip.pp0(1.0, ff, cyc)
            col.append({"window_s": 1.0, "pp0_j": e, "f": ff, "cyc": cyc})
        m2 = S.fit_core_model(col)
        r2 = S.attribute_cores(m2, {"window_s": 1.0, "pp0_j": 5.0, "f": f, "cyc": tot}, {"x": llm}, C.VIF_REFUSE)
        vif_ok.append("refused" in r2)

    def q(xs, p):
        return sorted(xs)[int(p * (len(xs) - 1))]
    record("B1 iGPU = PP1 x fdinfo share, busy neighbour",
           frac(e <= C.IGPU_PP1_SPLIT_MAX_ERR for e in igpu_err) >= C.SEED_PASS_FRACTION,
           "within %.0f%% on %.0f%% of seeds; median err %.1f%%, p90 %.1f%%" % (
               100 * C.IGPU_PP1_SPLIT_MAX_ERR, 100 * frac(e <= C.IGPU_PP1_SPLIT_MAX_ERR for e in igpu_err),
               100 * q(igpu_err, .5), 100 * q(igpu_err, .9)))
    record("B1b CPU-time split misattributes iGPU stage",
           frac(e >= C.CPU_TIME_SPLIT_MIN_ERR_IGPU for e in cts_err) >= C.SEED_PASS_FRACTION,
           "median err %.0f%%" % (100 * q(cts_err, .5)))
    for name, errs in (("B2 operating-point P stage (LLM)", op_err_p), ("B2 operating-point E stage (embedder)", op_err_e)):
        record(name, frac(e <= C.OPPOINT_MAX_ERR for e in errs) >= C.SEED_PASS_FRACTION,
               "within %.0f%% on %.0f%% of seeds; median %.1f%%, p90 %.1f%%" % (
                   100 * C.OPPOINT_MAX_ERR, 100 * frac(e <= C.OPPOINT_MAX_ERR for e in errs),
                   100 * q(errs, .5), 100 * q(errs, .9)))
    record("B2b isolated constant-beta misses E stage",
           frac(e > C.CONST_BETA_MIN_ERR_E for e in cb_err_e) >= C.SEED_PASS_FRACTION,
           "median err %.0f%%; isolated-vs-concurrent truth gap for E stage median %+.0f%%" % (
               100 * q(cb_err_e, .5), 100 * q(iso_gap, .5)))
    record("B3 refuses P/E split when collinear", frac(vif_ok) >= C.SEED_PASS_FRACTION,
           "refused on %.0f%% of seeds" % (100 * frac(vif_ok)))
    record("B4 rail-coupling line reported (>0)", bool(coupling) and frac(c > 0 for c in coupling) >= C.SEED_PASS_FRACTION,
           "E-stage coupling share median %.0f%%" % (100 * q(coupling, .5)) if coupling else "no estimates")


# =========================================================================== (c) sentinel placement

def draw_devices(rng):
    """Synthetic 13900T-like candidates. Assumed ranges, not measurements."""
    return {
        "ecore": {"e_active": rng.uniform(0.015, 0.050), "p_tail": rng.uniform(0.02, 0.10),
                  "tau": rng.uniform(0.002, 0.02), "e_wake": rng.uniform(0.0001, 0.001)},
        "igpu": {"e_active": rng.uniform(0.002, 0.008), "p_tail": rng.uniform(0.05, 0.30),
                 "tau": rng.uniform(0.03, 0.30), "e_wake": rng.uniform(0.003, 0.020)},
    }


def run_timeline(rng, dev, rate, n, burst):
    """Joules and seconds for n events: clustered arrivals, jittered idle timer."""
    t, times = 0.0, []
    while len(times) < n:
        t += rng.expovariate(rate / burst)
        k = 1
        while rng.random() > 1 / burst:
            k += 1
        for i in range(k):
            times.append(t + 0.02 * i)
    times = times[:n]
    e = 0.0
    for i, t0 in enumerate(times):
        e += dev["e_active"] * rng.gauss(1, 0.03)
        if i + 1 < len(times):
            gap = times[i + 1] - t0
            tau = dev["tau"] * rng.uniform(0.8, 1.2)
            e += dev["p_tail"] * min(gap, tau)
            if gap > tau:
                e += dev["e_wake"]
    return e, times[-1] - times[0] + 1e-9


SWEEP = (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)
TRUTH_GRID = [0.1 * 100 ** (i / 30) for i in range(31)]


def measure(rng, dev, burst, sigma_w):
    pts = []
    for r in SWEEP:
        reps = []
        for _ in range(3):
            e, dur = run_timeline(rng, dev, r, 300, burst)
            e += rng.gauss(0, sigma_w * math.sqrt(dur))   # other activity moves package power
            reps.append(max(e, 1e-6) / 300)
        m = sum(reps) / 3
        se = math.sqrt(sum((x - m) ** 2 for x in reps) / 2 / 3)
        pts.append((r, m, se))
    return pts


def true_curve(dev, burst, seed):
    rng = random.Random(seed)
    return {r: run_timeline(rng, dev, r, 2000, burst)[0] / 2000 for r in TRUTH_GRID}


def true_crossovers(ta, tb):
    out = []
    for r0, r1 in zip(TRUTH_GRID, TRUTH_GRID[1:]):
        d0, d1 = ta[r0] - tb[r0], ta[r1] - tb[r1]
        if (d0 > 0) != (d1 > 0):
            w = d0 / (d0 - d1)
            out.append(r0 * (r1 / r0) ** w)
    return out


DAY = [0.1] * 7 + [1.0, 3.0, 5.0, 5.0, 2.0, 1.0, 3.0, 5.0, 8.0, 2.0, 1.0, 0.5, 0.5, 0.3, 0.2, 0.1, 0.1]


def nearest(curve, r):
    return curve[min(curve, key=lambda g: abs(math.log(g / r)))]


def day_energy(curves, plan, rates):
    return sum(r * 3600 * nearest(curves[d], r) for d, r in zip(plan, rates))


def part_c(seeds):
    print("\n(c) sentinel placement: %d seeds, E-core vs UHD iGPU, quiet host (sigma 5-30 mW) and busy host (100-500 mW)" % seeds)
    xo_err, xo_seeds, never_worse, saving, saving_pkg, wrong_notail, notail_loss = [], 0, [], [], [], [], []
    busy_never_worse, busy_indist = [], []
    for s in range(seeds):
        rng = random.Random(9000 + s)
        devs = draw_devices(rng)
        burst = rng.uniform(1.0, 2.5)
        truth = {d: true_curve(p, burst, 77 + s) for d, p in devs.items()}
        rates = [r * rng.uniform(0.5, 2.0) for r in DAY]
        for regime, sigma in (("quiet", rng.uniform(0.005, 0.03)), ("busy", rng.uniform(0.1, 0.5))):
            fits = {d: S.fit_sentinel(measure(rng, p, burst, sigma)) for d, p in devs.items()}
            plan, inc, why = [], "igpu", []
            for r in rates:
                inc, reason = S.place(fits, r, inc)
                plan.append(inc)
                why.append(reason)
            static = day_energy(truth, ["igpu"] * 24, rates)
            auto = day_energy(truth, plan, rates)
            if regime == "busy":
                busy_never_worse.append(auto <= static * (1 + C.AUTO_VS_STATIC_MAX_LOSS))
                busy_indist.append(why.count("indistinguishable") / 24)
                continue
            never_worse.append(auto <= static * (1 + C.AUTO_VS_STATIC_MAX_LOSS))
            saving.append(1 - auto / static)
            saving_pkg.append(1 - (auto + 2.0 * 86400) / (static + 2.0 * 86400))   # add a 2 W package floor
            tx = [x for x in true_crossovers(truth["ecore"], truth["igpu"]) if C.RATE_MIN <= x <= C.RATE_MAX]
            if len(tx) == 1:
                xo_seeds += 1
                fx = S.crossover(fits["ecore"], fits["igpu"], C.RATE_MIN, C.RATE_MAX)
                xo_err.append(min((abs(math.log(x / tx[0])) for x in fx), default=math.inf))
            # Ablation: one benchmark-rate measurement per device, no rate dependence.
            nt = {d: S.fit_sentinel([p for p in measure(rng, devs[d], burst, sigma) if p[0] == 5.0], tail=False)
                  for d in devs}
            pick_nt = min(nt, key=lambda d: S.predict(nt[d], 0.1))
            pick_true = min(truth, key=lambda d: truth[d][0.1])
            wrong_notail.append(pick_nt != pick_true)
            plan_nt = [min(nt, key=lambda d: S.predict(nt[d], r)) for r in rates]
            notail_loss.append(day_energy(truth, plan_nt, rates) / auto - 1)

    lim = math.log(1 + C.CROSSOVER_MAX_REL_ERR)
    print("       seeds with exactly one true crossover in %.1f-%.0f/s: %d/%d" % (C.RATE_MIN, C.RATE_MAX, xo_seeds, seeds))
    record("C1 fitted crossover within 30% of true",
           bool(xo_err) and frac(e <= lim for e in xo_err) >= C.SEED_PASS_FRACTION,
           "within on %.0f%% of crossover seeds" % (100 * frac(e <= lim for e in xo_err)) if xo_err else "no crossover seeds")
    record("C2 auto never >2% worse than static iGPU (quiet)", frac(never_worse) == 1.0,
           "held on %.0f%% of seeds" % (100 * frac(never_worse)))
    record("C2b same on busy host (noise gate)", frac(busy_never_worse) == 1.0,
           "held on %.0f%%; hours kept on incumbent as indistinguishable: median %.0f%%" % (
               100 * frac(busy_never_worse), 100 * sorted(busy_indist)[len(busy_indist) // 2]))
    record("C3 ablate tail term: wrong device at 0.1/s", frac(wrong_notail) >= C.ABLATE_NO_TAIL_MIN_WRONG,
           "wrong on %.0f%% of seeds; day energy vs full fit median %+.0f%%" % (
               100 * frac(wrong_notail), 100 * sorted(notail_loss)[len(notail_loss) // 2]))
    sv = sorted(saving)
    sp = sorted(saving_pkg)
    print("       (report only, synthetic devices) sentinel saving vs static iGPU: median %.0f%%, p10 %.0f%%, p90 %.0f%%;"
          " with a 2 W package floor counted: median %.1f%%" % (
              100 * sv[len(sv) // 2], 100 * sv[len(sv) // 10], 100 * sv[9 * len(sv) // 10], 100 * sp[len(sp) // 2]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=C.SEEDS)
    ap.add_argument("--parts", default="abc")
    a = ap.parse_args()
    if "a" in a.parts:
        part_a(a.seeds)
    if "b" in a.parts:
        part_b(a.seeds)
    if "c" in a.parts:
        part_c(a.seeds)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n%d/%d criteria held%s" % (len(RESULTS) - len(failed), len(RESULTS),
                                       "; FAILED: " + ", ".join(failed) if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
