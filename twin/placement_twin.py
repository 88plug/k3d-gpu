#!/usr/bin/env python3
"""placement_twin.py — choose k3d-gpu.io/cores (and an E-module group) from counters and curves.

Parts (see docs/placement-twin.md):
  1. parse_perf_csv / features   `perf stat -x,` output (cpu_core/... events) -> TMA L1 + MPKI
  2. EPModel                      ridge regression for the CPU-time E/P factor; off-CPU time is
                                  carried through unscaled: factor = off + (1 - off) * cpu_factor
  3. PackingModel                 slowdown of pods sharing one 4-core E module (shared 4 MB L2)
  4. PCCSCurve / fit_pccs         three-region continuous piecewise-linear slowdown vs external
                                  bandwidth (the PCCS form, Xu et al., MICRO 2021)
  5. ClockModel                   turbo table x linear derate of the active set, fitted from probes
  6. fixed_point                  couples engines: external bandwidth, clocks, slowdowns
  7. enumerate_placements / emit  legal placements under an SLO; labels for a pod spec

Inputs are only what the host exposes: counters, off-CPU fractions, measured times and clocks.
"""
import argparse
import itertools
import json
import sys

import numpy as np

PEAK_BW = 57.6
# MSR_TURBO_RATIO_LIMIT / MSR_SECONDARY_TURBO_RATIO_LIMIT on the i9-13900T (docs/power-envelope.md).
P_TURBO = {1: 5.3, 2: 5.3, 3: 5.1, 4: 5.1, 5: 5.0, 6: 5.0, 7: 4.3, 8: 4.3}
E_TURBO = 3.9
E_MODULES = 4
E_MODULE_CORES = 4
P_CORES = 8

CORE_EVENTS = ["instructions", "cpu-cycles", "ref-cycles", "slots", "topdown-retiring",
               "topdown-bad-spec", "topdown-fe-bound", "topdown-be-bound", "topdown-mem-bound",
               "branch-misses", "l2_rqsts.miss", "longest_lat_cache.miss",
               "fp_arith_inst_retired.256b_packed_single", "fp_arith_inst_retired.256b_packed_double"]
FEATURES = ["ret", "bs", "fe", "be_core", "be_mem", "ipc", "l2_mpki", "llc_mpki", "br_mpki", "vec_frac"]


# ---------------------------------------------------------------- 1. perf parsing
def parse_perf_csv(text):
    """{pmu: {event: (value or None, pct_running)}} from `perf stat -x,` stderr."""
    out = {}
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 5 or "/" not in parts[2]:
            continue
        pmu, _, rest = parts[2].partition("/")
        event = rest.split("/")[0]
        val = None if parts[0].startswith("<") else float(parts[0])
        pct = float(parts[4]) if parts[4] else 0.0
        out.setdefault(pmu, {})[event] = (val, pct)
    return out


def features(perf, off_cpu, pmu="cpu_core"):
    """Model features from parsed counters of a P-core sample. Missing events -> KeyError."""
    ev = {k: v[0] for k, v in perf[pmu].items()}
    slots = ev["slots"]
    instr = ev["instructions"]
    be = ev["topdown-be-bound"]
    mem = ev.get("topdown-mem-bound", 0.0) or 0.0
    vec = (ev.get("fp_arith_inst_retired.256b_packed_single", 0.0) or 0.0) + \
          (ev.get("fp_arith_inst_retired.256b_packed_double", 0.0) or 0.0)
    return {
        "ret": ev["topdown-retiring"] / slots, "bs": ev["topdown-bad-spec"] / slots,
        "fe": ev["topdown-fe-bound"] / slots, "be_core": (be - mem) / slots, "be_mem": mem / slots,
        "ipc": instr / ev["cpu-cycles"], "l2_mpki": 1000 * ev.get("l2_rqsts.miss", 0.0) / instr,
        "llc_mpki": 1000 * ev.get("longest_lat_cache.miss", ev.get("cache-misses", 0.0)) / instr,
        "br_mpki": 1000 * ev["branch-misses"] / instr, "vec_frac": vec / instr + 1e-4,
        "off_cpu": off_cpu,
    }


def off_cpu_fraction(schedstat_before, schedstat_after, wall_ns):
    """Off-CPU share of wall time from two /proc/<pid>/schedstat reads (field 1 = on-CPU ns)."""
    on = int(schedstat_after.split()[0]) - int(schedstat_before.split()[0])
    return max(0.0, min(0.99, 1.0 - on / wall_ns))


# ---------------------------------------------------------------- 2. E/P factor
def _ridge(X, y, lam):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    A = Z.T @ Z + lam * np.eye(Z.shape[1])
    w = np.linalg.solve(A, Z.T @ (y - y.mean()))
    return mu, sd, w, y.mean()


class EPModel:
    """cpu_factor = ridge(features); wall factor = off + (1 - off) * cpu_factor.

    structured=False is the ablation: one ridge straight on the wall factor, off_cpu as a feature.
    """
    LAMBDAS = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0)

    def __init__(self, structured=True, features=FEATURES):
        self.structured = structured
        self.features = list(features) + ([] if structured else ["off_cpu"])

    def _xy(self, rows, factors):
        X = np.array([[r[f] for f in self.features] for r in rows])
        off = np.array([r["off_cpu"] for r in rows])
        y = np.asarray(factors, float)
        if self.structured:
            y = (y - off) / np.maximum(1e-3, 1 - off)
        return X, y

    def fit(self, rows, factors, lam=None):
        X, y = self._xy(rows, factors)
        if lam is None:  # inner leave-one-out over the lambda grid, on the training rows only
            best = None
            for cand in self.LAMBDAS:
                err = 0.0
                for i in range(len(y)):
                    m = np.ones(len(y), bool)
                    m[i] = False
                    mu, sd, w, b = _ridge(X[m], y[m], cand)
                    err += abs(((X[i] - mu) / sd) @ w + b - y[i])
                if best is None or err < best[0]:
                    best = (err, cand)
            lam = best[1]
        self.lam = lam
        self.mu, self.sd, self.w, self.b = _ridge(X, y, lam)
        return self

    def predict(self, row):
        x = np.array([row[f] for f in self.features])
        z = float(((x - self.mu) / self.sd) @ self.w + self.b)
        if self.structured:
            return row["off_cpu"] + (1 - row["off_cpu"]) * max(0.8, z)
        return max(0.8, z)


def td_class(row):
    """A stand-in for Thread Director's class from the instruction mix: 0 scalar, 1 vector, 2 spin-ish."""
    if row["vec_frac"] > 0.15:
        return 1
    if row["ipc"] > 2.5 and row["be_mem"] < 0.1:
        return 2
    return 0


# ---------------------------------------------------------------- 3. E-module packing
def pressure(row):
    """L2 traffic proxy of a pod: its memory-bound share, scaled by how much it misses L2."""
    return row["be_mem"] * (1.0 + row["l2_mpki"] / 10.0)


class PackingModel:
    """s_i = 1 + b1 * be_mem_i * sum_{j!=i} pressure_j + b2 * be_mem_i * (sum_j pressure_j)^2."""

    def _terms(self, rows):
        p = [pressure(r) for r in rows]
        tot = sum(p)
        return [(r["be_mem"] * (tot - p[i]), r["be_mem"] * tot * tot) for i, r in enumerate(rows)]

    def fit(self, mixes, slowdowns):
        X, y = [], []
        for rows, s in zip(mixes, slowdowns):
            X += self._terms(rows)
            y += [v - 1.0 for v in s]
        self.b = np.clip(np.linalg.lstsq(np.array(X), np.array(y), rcond=None)[0], 0, None)
        return self

    def predict(self, rows):
        return [1.0 + float(np.dot(self.b, t)) for t in self._terms(rows)]


# ---------------------------------------------------------------- 4. PCCS curves
class PCCSCurve:
    """s(x) = 1 + a x + b relu(x - t1) + c relu(x - t2): minor, normal, intensive regions."""

    def __init__(self, t1, t2, a, b, c):
        self.t1, self.t2, self.a, self.b, self.c = t1, t2, a, b, c

    def __call__(self, x):
        return 1.0 + self.a * x + self.b * max(0.0, x - self.t1) + self.c * max(0.0, x - self.t2)

    def as_tuple(self):
        return (self.t1, self.t2, self.a, self.b, self.c)


def _hinge_fit(x, y, t1, t2):
    A = np.stack([x, np.maximum(0, x - t1), np.maximum(0, x - t2)], 1)
    coef, *_ = np.linalg.lstsq(A, y - 1.0, rcond=None)
    return coef, float(np.sum((A @ coef - (y - 1.0)) ** 2))


def fit_pccs(x, s, step=0.5):
    """Grid search on the two breakpoints, least squares for slopes, then a fine refine."""
    x, s = np.asarray(x, float), np.asarray(s, float)
    lo, hi = x.min(), x.max()
    best = None
    grid = np.arange(lo + step, hi, step)
    for t1, t2 in itertools.combinations(grid, 2):
        coef, sse = _hinge_fit(x, s, t1, t2)
        if best is None or sse < best[0]:
            best = (sse, t1, t2, coef)
    _, t1, t2, coef = best
    for _ in range(3):
        fine = step / 10
        for d1 in np.arange(-step, step + fine, fine):
            for d2 in np.arange(-step, step + fine, fine):
                a, b = t1 + d1, t2 + d2
                if not (lo < a < b < hi):
                    continue
                c, sse = _hinge_fit(x, s, a, b)
                if sse < best[0]:
                    best = (sse, a, b, c)
        _, t1, t2, coef = best
        step = fine
    return PCCSCurve(t1, t2, *coef)


class EngineCurves:
    """PCCS curves at several target intensities; parameters interpolated on own demand."""

    def __init__(self, demands, curves):
        order = np.argsort(demands)
        self.d = np.asarray(demands, float)[order]
        self.curves = [curves[i] for i in order]

    def __call__(self, x, own_demand):
        vals = [c(x) for c in self.curves]
        return float(np.interp(own_demand, self.d, vals))


# ---------------------------------------------------------------- 5. clocks
class ClockModel:
    """Predicted clock of an engine for an active set (nP busy P-cores, nE busy E-cores, iGPU busy).

    mode "table":  bilinear interpolation, per iGPU state, of measured f / turbo(n_own) over a
                   swept (nP, nE) grid. Assumes no functional form (v2).
    mode "linear": turbo(n_own) * (w . [1, nP, nE, gt]), least squares (v1, pre-registered).
    mode "off":    turbo table only; the bandwidth-only ablation.
    """

    def __init__(self, mode="table"):
        self.mode = mode
        self.w = {e: np.array([1.0, 0, 0, 0]) for e in ("P", "E", "G")}
        self.grid = {}

    @staticmethod
    def table(engine, n):
        return P_TURBO[max(1, min(P_CORES, n))] if engine == "P" else (E_TURBO if engine == "E" else 1.65)

    def _own(self, engine, nP, nE):
        return nP if engine == "P" else nE

    def fit(self, samples):
        """samples: [(engine, nP, nE, gt, measured_GHz)]"""
        for eng in ("P", "E", "G"):
            rows = [s for s in samples if s[0] == eng]
            if not rows or self.mode == "off":
                continue
            y = np.array([s[4] / self.table(eng, self._own(eng, s[1], s[2])) for s in rows])
            if self.mode == "linear":
                X = np.array([[1.0, s[1], s[2], s[3]] for s in rows])
                self.w[eng] = np.linalg.lstsq(X, y, rcond=None)[0]
                continue
            acc = {}
            for s, v in zip(rows, y):
                acc.setdefault((s[3], s[1], s[2]), []).append(v)
            self.grid[eng] = {k: float(np.mean(v)) for k, v in acc.items()}
        return self

    def _interp(self, eng, nP, nE, gt):
        g = self.grid[eng]
        pts = {(p, e): v for (t, p, e), v in g.items() if t == gt} or {(p, e): v for (t, p, e), v in g.items()}
        ps = sorted({p for p, _ in pts})
        es = sorted({e for _, e in pts})

        def bracket(axis, x):
            x = min(max(x, axis[0]), axis[-1])
            i = max(0, min(len(axis) - 2, int(np.searchsorted(axis, x, side="right")) - 1))
            if len(axis) == 1:
                return [(axis[0], 1.0)]
            a, b = axis[i], axis[i + 1]
            t = (x - a) / (b - a)
            return [(a, 1 - t), (b, t)]
        num = den = 0.0
        for p, wp in bracket(ps, nP):
            for e, we in bracket(es, nE):
                if (p, e) in pts and wp * we > 0:
                    num += wp * we * pts[(p, e)]
                    den += wp * we
        if den == 0:  # off-grid corner: nearest measured point
            p, e = min(pts, key=lambda k: abs(k[0] - nP) + abs(k[1] - nE) / 2)
            return pts[(p, e)]
        return num / den

    def ghz(self, engine, nP, nE, gt):
        base = self.table(engine, self._own(engine, nP, nE))
        if self.mode == "off":
            return base
        if self.mode == "linear":
            return base * float(np.clip(self.w[engine] @ [1.0, nP, nE, gt], 0.2, 1.0))
        if engine not in self.grid:
            return base
        return base * float(np.clip(self._interp(engine, nP, nE, gt), 0.1, 1.0))


def kappa_from_scaling(t_hi, t_lo, f_hi, f_lo):
    """Clock-sensitive share of run time at f_hi, from two runs at clock caps f_hi > f_lo.

    Model t(f) = a / f + b: a = (t_lo - t_hi) / (1/f_lo - 1/f_hi), share = (a / f_hi) / t_hi.
    This is the kappa that fixed_point uses (s = 1 + kappa (r - 1))."""
    a = (t_lo - t_hi) / (1.0 / f_lo - 1.0 / f_hi)
    return float(np.clip(a / f_hi / t_hi, 0.0, 1.0))


def kappa_on_e(kappa_p, factor):
    """E-core clock sensitivity from the P-core one: the clock-insensitive time (DRAM, off-CPU)
    keeps its absolute length while the whole run stretches by the E/P factor."""
    return 1.0 - (1.0 - kappa_p) / max(1.0, factor)


# ---------------------------------------------------------------- 6. fixed point
def fixed_point(comps, curves, clock, iters=200, damp=0.5):
    """comps: [{engine P|E|G, n, iso (s/unit), bw (GB/s alone), kappa, iso_set (nP,nE,gt), pack}]

    Slowdown s_i = pack_i * (1 + kappa_i (r_i - 1)) * curve_engine(x_i, bw_i), where
    r_i = iso clock / co-run clock and x_i = external bandwidth actually delivered (bw_j / s_j).
    Returns per-component predicted seconds per unit.
    """
    nP = sum(c["n"] for c in comps if c["engine"] == "P")
    nE = sum(c["n"] for c in comps if c["engine"] == "E")
    gt = 1.0 if any(c["engine"] == "G" for c in comps) else 0.0
    r = []
    for c in comps:
        f_iso = clock.ghz(c["engine"], *c["iso_set"])
        r.append(f_iso / clock.ghz(c["engine"], nP, nE, gt))
    s = [1.0] * len(comps)
    for _ in range(iters):
        delivered = [c["bw"] / si for c, si in zip(comps, s)]
        tot = sum(delivered)
        new = []
        for i, c in enumerate(comps):
            x = tot - delivered[i]
            bw_term = curves[c["engine"]](x, c["bw"]) if curves else 1.0
            new.append(c.get("pack", 1.0) * (1 + c["kappa"] * (r[i] - 1)) * bw_term)
        if max(abs(a - b) for a, b in zip(s, new)) < 1e-7:
            s = new
            break
        s = [damp * a + (1 - damp) * b for a, b in zip(s, new)]
    return [c["iso"] * si for c, si in zip(comps, s)]


def roofline(comps):
    """Baseline: clocks fixed; if total demand exceeds DRAM peak, everyone slows by the ratio."""
    k = max(1.0, sum(c["bw"] for c in comps) / PEAK_BW)
    return [c["iso"] * k for c in comps]


# ---------------------------------------------------------------- 7. placement
def enumerate_placements(n_pods, p_free_min=1):
    """Yield tuples: entry i is 'P' or an E-module index 0..3. Modules are interchangeable, so
    pods are assigned to modules in canonical order (a new module only after the previous)."""
    def rec(i, cur, used, n_p):
        if i == n_pods:
            yield tuple(cur)
            return
        if n_p < P_CORES - p_free_min:
            yield from rec(i + 1, cur + ["P"], used, n_p + 1)
        for m in range(min(used + 1, E_MODULES)):
            if sum(1 for v in cur if v == m) < E_MODULE_CORES:
                yield from rec(i + 1, cur + [m], max(used, m + 1), n_p)
    yield from rec(0, [], 0, 0)


def choose(placements, evaluate):
    """evaluate(pl) -> (objective, n_slo_violations). Fewest violations first, then objective."""
    best = None
    for pl in placements:
        obj, viol = evaluate(pl)
        key = (-viol, obj)
        if best is None or key > best[0]:
            best = (key, pl)
    return best[1]


def emit(pod_names, placement):
    """Pod label patches for a placement.

    E pods get k3d-gpu.io/e-group=m<i>. In share/placement-twin/balloons-values.yaml the
    `efficient-module` balloon type groups pods by that label (groupBy: one balloon per
    group) and declares an l2cache load class, so the allocator keeps each group's CPUs
    inside one L2 block and keeps different groups on different L2 blocks. The module index
    is a group name, not a physical module id: modules are interchangeable.
    """
    out = {}
    for name, where in zip(pod_names, placement):
        if where == "P":
            out[name] = {"k3d-gpu.io/cores": "performance"}
        else:
            out[name] = {"k3d-gpu.io/cores": "efficient", "k3d-gpu.io/e-group": f"m{where}"}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("features", help="perf stat -x, file -> model features (JSON)")
    p.add_argument("perf_csv")
    p.add_argument("--off-cpu", type=float, default=0.0)
    p.add_argument("--pmu", default="cpu_core")
    sub.add_parser("events", help="print the perf -e list the profiler sidecar uses")
    args = ap.parse_args(argv)
    if args.cmd == "events":
        print(",".join(f"cpu_core/{e}/" for e in CORE_EVENTS))
        return 0
    with open(args.perf_csv) as f:
        perf = parse_perf_csv(f.read())
    try:
        print(json.dumps(features(perf, args.off_cpu, args.pmu), indent=2))
    except (KeyError, TypeError, ZeroDivisionError) as e:
        print(f"placement_twin.py: counters incomplete for {args.pmu}: {e!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
