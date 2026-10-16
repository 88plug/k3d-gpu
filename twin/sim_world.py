"""sim_world.py — hidden-physics stand-in for an i9-13900T + UHD 770 host.

The twin (placement_twin.py) never imports this module. The experiment hands the twin
only what a real host would expose: noisy perf counters, off-CPU fractions, measured run
times and measured clocks. Truth is used only for scoring.

The functional forms here are deliberately not the twin's:
  * E/P truth is a CPI stack with per-component E-core multipliers, a vector interaction,
    an L2-capacity step (P has 2 MB private L2, an E module has 4 MB shared) and DRAM time
    that does not scale with clock. The twin fits a ridge regression.
  * Shared-DRAM truth is a queueing curve plus a hard bandwidth cap. The twin fits PCCS
    three-region piecewise-linear curves.
  * Clock truth solves a package power budget (f^3 core power, bisection to the cap) and a
    smooth thermal derate. The twin fits a linear derate.
  * Workloads have a startup phase whose mix differs from the main phase.

Constants marked (measured) come from docs/power-envelope.md on branch
claude/invent-tseries-envelope (read-only MSR and sysfs reads of this host).
"""
import math
import random

PEAK_BW = 57.6                      # GB/s, DDR5 at 3600 MT/s x 2 channels (measured config)
P_TURBO = {1: 5.3, 2: 5.3, 3: 5.1, 4: 5.1, 5: 5.0, 6: 5.0, 7: 4.3, 8: 4.3}  # GHz (measured MSR)
E_TURBO = 3.9                       # GHz (measured MSR)
GT_MAX = 1.65                       # GHz, UHD 770 RP0 (measured)
UNCORE_W = 14.0                     # W, non-core non-GT floor (measured passively)
GT_W = 9.7                          # W, iGPU at RP0 (measured passively)
ENVELOPES = {"host": 117.0, "spec35": 35.0}  # sustained package limit, W
E_MODULES = 4
E_MODULE_CORES = 4
E_MODULE_L2_MB = 4.0
P_L2_MB = 2.0

# Agent-tool archetypes: (name, ranges for the main phase). Values are cycles per
# instruction on a P-core for each CPI component; dram is ns per instruction.
ARCHETYPES = [
    ("ripgrep",      dict(ret=(.22, .3), bs=(.03, .06), fe=(.05, .1), core=(.05, .1), l2=(.05, .1), llc=(.05, .15), dram=(.01, .04), vec=(.2, .4), ws=(1, 8), io=(.1, .3))),
    ("pytest",       dict(ret=(.25, .3), bs=(.1, .2), fe=(.3, .5), core=(.05, .1), l2=(.05, .1), llc=(.02, .06), dram=(.005, .02), vec=(0, .02), ws=(1, 3), io=(.2, .5))),
    ("gcc",          dict(ret=(.25, .3), bs=(.15, .25), fe=(.25, .4), core=(.08, .15), l2=(.08, .15), llc=(.05, .1), dram=(.01, .03), vec=(0, .02), ws=(2, 6), io=(.05, .15))),
    ("git-status",   dict(ret=(.2, .25), bs=(.05, .1), fe=(.1, .2), core=(.05, .1), l2=(.05, .1), llc=(.05, .1), dram=(.02, .05), vec=(0, .05), ws=(1, 4), io=(.5, .85))),
    ("jq",           dict(ret=(.25, .3), bs=(.08, .15), fe=(.1, .2), core=(.15, .3), l2=(.03, .06), llc=(.01, .03), dram=(.002, .01), vec=(0, .02), ws=(.2, 1), io=(.05, .2))),
    ("sqlite",       dict(ret=(.2, .25), bs=(.05, .1), fe=(.1, .2), core=(.05, .1), l2=(.1, .2), llc=(.1, .25), dram=(.04, .1), vec=(0, .02), ws=(3, 30), io=(.1, .4))),
    ("http-fetch",   dict(ret=(.2, .25), bs=(.05, .1), fe=(.15, .25), core=(.05, .1), l2=(.03, .06), llc=(.02, .05), dram=(.01, .02), vec=(0, .05), ws=(.5, 2), io=(.75, .95))),
    ("tokenizer",    dict(ret=(.25, .3), bs=(.05, .1), fe=(.05, .1), core=(.2, .35), l2=(.03, .06), llc=(.01, .03), dram=(.002, .01), vec=(.1, .3), ws=(.3, 1.5), io=(0, .1))),
    ("numpy-small",  dict(ret=(.25, .3), bs=(.01, .03), fe=(.02, .05), core=(.2, .4), l2=(.05, .1), llc=(.05, .1), dram=(.02, .06), vec=(.5, .9), ws=(1, 16), io=(0, .1))),
    ("tsc",          dict(ret=(.22, .28), bs=(.1, .18), fe=(.3, .45), core=(.05, .1), l2=(.1, .15), llc=(.05, .12), dram=(.02, .04), vec=(0, .02), ws=(4, 12), io=(.05, .2))),
    ("zstd",         dict(ret=(.25, .3), bs=(.05, .12), fe=(.03, .06), core=(.15, .3), l2=(.08, .15), llc=(.03, .08), dram=(.01, .03), vec=(.1, .3), ws=(1, 8), io=(.1, .3))),
    ("headless-dom", dict(ret=(.2, .25), bs=(.08, .15), fe=(.3, .5), core=(.05, .1), l2=(.1, .2), llc=(.08, .15), dram=(.03, .06), vec=(0, .05), ws=(8, 40), io=(.2, .5))),
]
STARTUP = dict(ret=(.2, .25), bs=(.12, .2), fe=(.4, .6), core=(.03, .06), l2=(.08, .15), llc=(.05, .1), dram=(.02, .04), vec=(0, .02), ws=(2, 6), io=(0, .05))


def _draw(rng, ranges):
    return {k: rng.uniform(*v) for k, v in ranges.items()}


class World:
    """One simulated host. `seed` redraws every hidden multiplier (perturbation, H4)."""

    def __init__(self, seed, gamma=0.3, envelope="host", null_factor=False, noise=0.05, hetero=False):
        self.rng = random.Random(seed)
        # hetero: a busy core's power scales with its compute duty (0.35 + 0.65 * duty), so cores
        # stalled on DRAM or waiting on IO draw less than the spin loops the twin calibrates on.
        self.hetero = hetero
        r = self.rng
        self.noise = noise
        self.gamma = gamma
        self.plim = ENVELOPES[envelope]
        self.null_factor = null_factor
        self.null_c = r.uniform(1.3, 1.6)
        # E-core CPI multipliers per component (hidden).
        self.m = dict(ret=r.uniform(1.1, 1.35), bs=r.uniform(1.2, 1.7), fe=r.uniform(1.05, 1.5),
                      core=r.uniform(1.3, 1.8), vec=r.uniform(0.5, 1.3), l2=r.uniform(1.3, 1.9),
                      llc=r.uniform(1.05, 1.4), dram=r.uniform(1.0, 1.25))
        self.lat_alpha = r.uniform(0.5, 1.2)       # DRAM queueing strength
        self.cP = r.uniform(0.11, 0.17)            # W / GHz^3, busy P-core
        self.cE = r.uniform(0.06, 0.09)            # W / GHz^3, busy E-core
        self.l2_delta = r.uniform(0.15, 0.45)      # E-module L2 bandwidth sharing
        self.overflow_pen = r.uniform(1.8, 3.2)    # L2 miss -> LLC cost multiplier

    def jitter(self, x, sd=None):
        sd = self.noise if sd is None else sd
        return x * math.exp(self.rng.gauss(0.0, sd))

    # ---------------- workloads ----------------
    def make_workloads(self, n=24):
        """n tool workloads (2 per archetype by default), each with a startup and main phase."""
        out = []
        for i in range(n):
            name, rng_main = ARCHETYPES[i % len(ARCHETYPES)]
            main = _draw(self.rng, rng_main)
            start = _draw(self.rng, STARTUP)
            out.append(dict(name=f"{name}-{i // len(ARCHETYPES)}", main=main, start=start,
                            start_s=self.rng.uniform(0.8, 2.5), run_s=self.rng.uniform(6, 40)))
        return out

    @staticmethod
    def _cycles(ph):
        return ph["ret"] + ph["bs"] + ph["fe"] + ph["core"] + ph["l2"] + ph["llc"]

    def _tpi(self, ph, engine, f, l2_scale=1.0, extra_llc=0.0):
        """ns per instruction for one phase on one engine at clock f (GHz)."""
        if engine == "P":
            cyc = self._cycles(ph) + extra_llc
            return cyc / f + ph["dram"]
        m = self.m
        llc = ph["llc"] * m["llc"]
        if P_L2_MB < ph["ws"] <= E_MODULE_L2_MB and l2_scale == 1.0:
            llc *= 0.55      # the 4 MB module L2 catches what spilled out of 2 MB
        cyc = (ph["ret"] * m["ret"] + ph["bs"] * m["bs"] + ph["fe"] * m["fe"]
               + ph["core"] * m["core"] * (1 + m["vec"] * ph["vec"])
               + ph["l2"] * m["l2"] * l2_scale + llc + extra_llc * m["llc"])
        return cyc / f + ph["dram"] * m["dram"]

    def _phase_wall(self, w, engine, f, **kw):
        """(start, main) wall seconds; off-CPU time is fixed absolute time measured on P."""
        res = []
        for key, dur in (("start", w["start_s"]), ("main", w["run_s"] - w["start_s"])):
            ph = w[key]
            on_p = dur * (1 - ph["io"])
            instr = on_p / self._tpi(ph, "P", P_TURBO[1] * self.derate(1, 0, 0)[0])
            res.append(instr * self._tpi(ph, engine, f, **kw) + dur * ph["io"])
        return res

    def ep_factor(self, w):
        """True wall-clock E/P factor of a whole run, alone on one core."""
        if self.null_factor:
            return self.null_c * math.exp(self.rng.gauss(0, 0.04))
        fP, fE = self.derate(1, 0, 0)[0] * P_TURBO[1], self.derate(0, 1, 0)[1] * E_TURBO
        return sum(self._phase_wall(w, "E", fE)) / sum(self._phase_wall(w, "P", fP))

    def counters(self, w, window):
        """Noisy TMA/MPKI/off-CPU features from a P-core sample. window: 'start2s' or 'image'."""
        if window == "start2s":
            mix = [("start", min(2.0, w["start_s"])), ("main", max(0.0, 2.0 - w["start_s"]))]
        else:
            mix = [("start", w["start_s"]), ("main", w["run_s"] - w["start_s"])]
        f = P_TURBO[1]
        agg = dict(cyc=0, instr=0, ret=0, bs=0, fe=0, core=0, mem=0, l2m=0, llcm=0, brm=0, vec=0, off=0, wall=0)
        for key, dur in mix:
            if dur <= 0:
                continue
            ph = w[key]
            on = dur * (1 - ph["io"])
            tpi = self._tpi(ph, "P", f)
            instr = on / tpi
            dram_cyc = ph["dram"] * f
            agg["instr"] += instr
            agg["cyc"] += instr * (self._cycles(ph) + dram_cyc)
            for k in ("ret", "bs", "fe", "core"):
                agg[k] += instr * ph[k]
            agg["mem"] += instr * (ph["l2"] + ph["llc"] + dram_cyc)
            agg["l2m"] += instr * (ph["llc"] / 0.04 + dram_cyc / 0.09) * min(1.0, ph["ws"] / P_L2_MB) / 1000
            agg["llcm"] += instr * dram_cyc / 0.09 / 1000
            agg["brm"] += instr * ph["bs"] / 15.0
            agg["vec"] += instr * ph["vec"]
            agg["off"] += dur * ph["io"]
            agg["wall"] += dur
        c, n, j = agg["cyc"], agg["instr"], self.jitter
        return {
            "ret": j(agg["ret"] / c), "bs": j(agg["bs"] / c), "fe": j(agg["fe"] / c),
            "be_core": j(agg["core"] / c), "be_mem": j(agg["mem"] / c), "ipc": j(n / c),
            "l2_mpki": j(1000 * agg["l2m"] / n), "llc_mpki": j(1000 * agg["llcm"] / n),
            "br_mpki": j(1000 * agg["brm"] / n), "vec_frac": j(agg["vec"] / n + 1e-4),
            "off_cpu": min(0.99, j(agg["off"] / agg["wall"])),
        }

    def measure_factor(self, w):
        """What a calibration run measures: pinned P and pinned E wall times, with noise."""
        return self.jitter(self.ep_factor(w), 0.02)

    # ---------------- clocks ----------------
    def derate(self, nP, nE, gt_busy, act=1.0, actE=None):
        """(fP_scale, fE_scale, gt_scale) for an active set. Power-budget bisection, then thermal."""
        fP0 = P_TURBO[max(1, min(8, nP))]

        def power(k):
            return (UNCORE_W + GT_W * gt_busy * k ** 3 + (act * self.cP * nP * (fP0 * k) ** 3
                                                  + (act if actE is None else actE) * self.cE * nE * (E_TURBO * k) ** 3))
        k = 1.0
        if power(1.0) > self.plim:
            lo, hi = 0.15, 1.0
            for _ in range(40):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if power(mid) <= self.plim else (lo, mid)
            k = lo
        th = 1.0 / (1.0 + self.gamma * max(0.0, power(k) - 45.0) / 45.0)
        return k * th, k * th, k

    def clocks(self, nP, nE, gt_busy, act=1.0, actE=None):
        sP, sE, sG = self.derate(nP, nE, gt_busy, act, actE)
        return P_TURBO[max(1, min(8, nP))] * sP, E_TURBO * sE, GT_MAX * sG

    # ---------------- shared DRAM ----------------
    def corun(self, comps, iters=400):
        """comps: list of dicts {engine, n, C (Gcycles/unit), B (GB/unit), bw1 (GB/s alone), io_s, l2x}.
        Returns per-component seconds per unit of work, under contention and the clock solve."""
        nP = sum(c["n"] for c in comps if c["engine"] == "P")
        nE = sum(c["n"] for c in comps if c["engine"] == "E")
        gt = 1.0 if any(c["engine"] == "G" for c in comps) else 0.0
        fP, fE, fG = self.clocks(nP, nE, gt)
        f = {"P": fP, "E": fE, "G": fG}
        t = [c["C"] / f[c["engine"]] + c["B"] / c["bw1"] + c.get("io_s", 0.0) for c in comps]
        for _ in range(iters):
            if self.hetero:
                act = {}
                for eng in ("P", "E"):
                    cs = [(c, ti) for c, ti in zip(comps, t) if c["engine"] == eng]
                    n = sum(c["n"] for c, _ in cs)
                    duty = sum(c["n"] * (0.35 + 0.65 * min(1.0, c["C"] * c.get("l2x", 1.0) / f[eng] / ti))
                               for c, ti in cs)
                    act[eng] = duty / n if n else 1.0
                fP, fE, fG = self.clocks(nP, nE, gt, act["P"], act["E"])
                f = {"P": fP, "E": fE, "G": fG}
            d = [c["B"] / ti for c, ti in zip(comps, t)]
            u = min(0.97, sum(d) / PEAK_BW)
            lat = 1.0 + self.lat_alpha * u * u / (1.0 - u)
            new = [c["C"] * c.get("l2x", 1.0) / f[c["engine"]] + c["B"] / c["bw1"] * lat + c.get("io_s", 0.0)
                   for c in comps]
            tot = sum(c["B"] / ti for c, ti in zip(comps, new))
            if tot > PEAK_BW:
                new = [ti * tot / PEAK_BW if c["B"] > 0 else ti for c, ti in zip(comps, new)]
            nxt = [0.7 * a + 0.3 * b for a, b in zip(t, new)]
            if max(abs(a - b) / a for a, b in zip(t, nxt)) < 1e-9:
                return nxt
            t = nxt
        return t

    def module_slowdowns(self, ws, comps_ph, f):
        """Per-pod E-module co-run slowdown for pods sharing one 4 MB L2 (single-phase params)."""
        tot = sum(ws)
        out = []
        for i, ph in enumerate(comps_ph):
            share = E_MODULE_L2_MB * ws[i] / tot if tot > E_MODULE_L2_MB else ws[i]
            over = max(0.0, ws[i] - share) / ws[i]
            traffic = sum((p["l2"] + p["llc"]) / (self._cycles(p) + 1e-9) for j, p in enumerate(comps_ph) if j != i)
            base = self._tpi(ph, "E", f)
            shared = self._tpi(ph, "E", f, l2_scale=1 - over, extra_llc=over * ph["l2"] * self.overflow_pen)
            out.append(shared / base * (1 + self.l2_delta * traffic))
        return out
