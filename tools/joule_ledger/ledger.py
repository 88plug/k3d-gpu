"""Joule Ledger core: freshness-aware energy counters, span bounds, span shares.

Stdlib only, so the node DaemonSet runs on a plain python image.

An energy counter (RAPL energy_uj, NVML total energy in mJ) is polled every
10 ms, but its value only changes when the hardware publishes a new reading.
Between two published readings nothing is measured. The ledger therefore keeps
only the polls where the value changed ("edges"), and for each edge stores the
time window in which the reading must have been taken:

    edge k: cumulative value C_k, closing time tau_k in [a_k, b_k]
            a_k = previous poll time - latency, b_k = this poll time

`latency` is the largest delay between the hardware closing a reading and the
reading becoming visible. It is a configured bound; a live probe measures it.

From these edges the ledger computes, for any window [s, e]:
  * a rigorous [lo, hi] on the device energy E(e) - E(s), using only that the
    cumulative counter is non-decreasing;
  * measured_fraction = lo / hi;
  * a point estimate of each span's dynamic energy: idle power goes to an idle
    bucket, the rest of every update interval is spread over the part of the
    interval where spans are in flight and split among those spans by weight.
    Energy in intervals with no span in flight goes to an unattributed bucket.
"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from itertools import combinations


@dataclass
class Edge:
    a: float      # earliest possible closing time of this reading
    b: float      # latest possible closing time (the poll that saw it)
    value: float  # cumulative energy, joules, wrap-corrected


@dataclass
class FreshCounter:
    """Turns raw polls of a cumulative energy counter into edges."""

    latency: float = 0.0     # s, bound on reading-close -> visible delay
    quantum: float = 0.0     # J, counter resolution (floor quantisation)
    wrap: float = 0.0        # J, counter range (RAPL max_energy_range_uj), 0 = none
    track_freshness: bool = True
    edges: list = field(default_factory=list)
    _last_raw: float = None
    _last_t: float = None
    _offset: float = 0.0

    def observe(self, t, raw):
        if self._last_raw is None:
            # The first reading may be arbitrarily stale: no lower time bound.
            self._last_raw, self._last_t = raw, t
            self.edges.append(Edge(float("-inf"), t, raw))
            return
        if raw < self._last_raw and self.wrap > 0:
            self._offset += self.wrap
        changed = raw != self._last_raw
        if changed or not self.track_freshness:
            if self.track_freshness:
                a = self._last_t - self.latency
            else:
                a = t  # ablation: treat the poll instant as the reading instant
            self.edges.append(Edge(a, t, raw + self._offset))
        self._last_raw, self._last_t = raw, t

    def trim(self, before):
        """Drop edges older than `before`, keeping one so bounds stay defined."""
        cut = 0
        while cut + 1 < len(self.edges) and self.edges[cut + 1].b < before:
            cut += 1
        if cut:
            del self.edges[:cut]
            self._a = None

    def _index(self):
        """Refresh the a/b search lists if edges were added."""
        if getattr(self, "_a", None) is None or len(self._a) != len(self.edges):
            self._a = [ed.a for ed in self.edges]
            self._b = [ed.b for ed in self.edges]

    # E(t) bounds from monotonicity.
    def _upper(self, t):
        """Smallest edge value whose reading closed at or after t."""
        self._index()
        i = bisect_left(self._a, t)
        if i >= len(self.edges):
            return None
        return self.edges[i].value + self.quantum

    def _lower(self, t):
        """Largest edge value whose reading closed at or before t."""
        self._index()
        i = bisect_right(self._b, t) - 1
        if i < 0:
            return None
        return self.edges[i].value

    def bounds(self, s, e):
        """(lo, hi) on E(e) - E(s); None if [s, e] is not yet covered."""
        up_e, lo_e = self._upper(e), self._lower(e)
        up_s, lo_s = self._upper(s), self._lower(s)
        if None in (up_e, lo_e, up_s, lo_s):
            return None
        lo = max(0.0, lo_e - up_s)
        hi = max(lo, up_e - lo_s)
        return lo, hi

    def intervals(self):
        """Piecewise-constant power between edge midpoints: [(t0, t1, joules)]."""
        out = []
        for p, q in zip(self.edges[1:], self.edges[2:]):
            t0, t1 = (p.a + p.b) / 2, (q.a + q.b) / 2
            if t1 > t0:
                out.append((t0, t1, q.value - p.value))
        return out


@dataclass
class Span:
    span_id: str
    start: float
    end: float
    weight: float = 1.0   # share key inside the device (pod activity share, etc.)


def _active_segments(t0, t1, spans):
    """Split [t0, t1] at span edges; yield (u0, u1, [(span, weight)])."""
    cuts = {t0, t1}
    live = [s for s in spans if s.start < t1 and s.end > t0]
    for s in live:
        if t0 < s.start < t1:
            cuts.add(s.start)
        if t0 < s.end < t1:
            cuts.add(s.end)
    pts = sorted(cuts)
    for u0, u1 in zip(pts, pts[1:]):
        mid = (u0 + u1) / 2
        inflight = [s for s in live if s.start <= mid < s.end]
        yield u0, u1, inflight


def estimate_idle_power(intervals, spans, pct=0.05):
    """Low percentile of power over update intervals with no span in flight."""
    quiet = []
    for t0, t1, j in intervals:
        if not any(s.start < t1 and s.end > t0 for s in spans):
            quiet.append(j / (t1 - t0))
    pool = quiet or [j / (t1 - t0) for t0, t1, j in intervals]
    if not pool:
        return 0.0
    pool.sort()
    return pool[min(len(pool) - 1, int(pct * len(pool)))]


def attribute(counter, spans, idle_power):
    """Point estimate of each span's dynamic joules.

    Returns (per_span: {span_id: joules}, buckets: {idle, unattributed}, total).
    sum(per_span) + idle + unattributed == total exactly (conservation).
    """
    spans = sorted(spans, key=lambda s: s.start)
    per = {s.span_id: 0.0 for s in spans}
    idle = unattr = total = 0.0
    starts = [s.start for s in spans]
    max_len = max((s.end - s.start for s in spans), default=0.0)
    for t0, t1, joules in counter.intervals():
        total += joules
        length = t1 - t0
        idle_j = min(joules, idle_power * length)
        dyn = joules - idle_j
        idle += idle_j
        lo = bisect_left(starts, t0 - max_len)
        hi = bisect_left(starts, t1)
        cand = spans[lo:hi]
        segs = [(u0, u1, inf) for u0, u1, inf in _active_segments(t0, t1, cand) if inf]
        active = sum(u1 - u0 for u0, u1, _ in segs)
        if dyn <= 0:
            continue
        if active <= 0:
            unattr += dyn
            continue
        for u0, u1, inflight in segs:
            seg_j = dyn * (u1 - u0) / active
            wsum = sum(s.weight for s in inflight)
            for s in inflight:
                per[s.span_id] += seg_j * s.weight / wsum
    return per, {"idle": idle, "unattributed": unattr}, total


def span_record(counter, span, joules):
    """Attributes for one finished span; None until the counter covers it."""
    b = counter.bounds(span.start, span.end)
    if b is None:
        return None
    lo, hi = b
    return {
        "joules": joules,
        "device_window_lo_joules": lo,
        "device_window_hi_joules": hi,
        "measured_fraction": (lo / hi) if hi > 0 else 1.0,
    }


# ---------------------------------------------------------------------------
# P/E identifiability gate.
#
# On Alder/Raptor Lake client parts P- and E-cores share one VccIA rail and one
# RAPL PP0 counter, and the rail voltage follows the highest requested
# frequency. Joules per cycle per core type are then not constants. The gate
# fits E = aP*cycP + aE*cycE + b*dt separately on low- and high-frequency
# strata of a window; if the coefficients move, the split is refused and only
# the combined CPU energy is reported.


def _nnls_small(rows, ys):
    """Exact NNLS for <= 4 regressors by enumerating active sets."""
    k = len(rows[0])
    best, best_sse = [0.0] * k, sum(y * y for y in ys)
    for r in range(1, k + 1):
        for idx in combinations(range(k), r):
            x = _lstsq([[row[i] for i in idx] for row in rows], ys)
            if x is None or any(v < 0 for v in x):
                continue
            coef = [0.0] * k
            for i, v in zip(idx, x):
                coef[i] = v
            sse = sum((y - sum(c * v for c, v in zip(coef, row))) ** 2
                      for row, y in zip(rows, ys))
            if sse < best_sse:
                best, best_sse = coef, sse
    return best, best_sse


def _lstsq(rows, ys):
    k = len(rows[0])
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    aty = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(k)]
    # Gauss-Jordan with partial pivoting.
    m = [ata[i] + [aty[i]] for i in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda i: abs(m[i][c]))
        if abs(m[p][c]) < 1e-30:
            return None
        m[c], m[p] = m[p], m[c]
        for i in range(k):
            if i != c:
                f = m[i][c] / m[c][c]
                m[i] = [vi - f * vc for vi, vc in zip(m[i], m[c])]
    return [m[i][k] / m[i][i] for i in range(k)]


def pe_split(samples, tol=0.15, min_rows=20):
    """samples: [(dt, cycP, cycE, freq_max, joules)] for one window.

    Returns dict with accepted, aP, aE, b, reason. Coefficients are scaled to
    joules per 1e9 cycles and watts so they print readably.
    """
    if len(samples) < 2 * min_rows:
        return {"accepted": False, "reason": "too_few_samples"}

    def fit(rows):
        x = [(dt, cp / 1e9, ce / 1e9) for dt, cp, ce, _, _ in rows]
        y = [j for *_, j in rows]
        (b, ap, ae), _ = _nnls_small(x, y)
        return ap, ae, b

    ap, ae, b = fit(samples)
    freqs = sorted(f for _, _, _, f, _ in samples)
    lo_f, hi_f = freqs[len(freqs) // 3], freqs[(2 * len(freqs)) // 3]
    low = [s for s in samples if s[3] <= lo_f]
    high = [s for s in samples if s[3] >= hi_f]
    out = {"aP": ap, "aE": ae, "b": b}
    if len(low) < min_rows or len(high) < min_rows:
        return {**out, "accepted": False, "reason": "too_few_strata_samples"}
    if hi_f - lo_f < 1e-9:
        return {**out, "accepted": True, "reason": "single_frequency"}
    apl, ael, _ = fit(low)
    aph, aeh, _ = fit(high)

    def moved(x, y):
        m = (x + y) / 2
        return m <= 0 or abs(x - y) / m > tol

    if moved(apl, aph) or moved(ael, aeh):
        return {**out, "accepted": False, "reason": "coefficients_move_with_frequency"}
    return {**out, "accepted": True, "reason": "stable"}
