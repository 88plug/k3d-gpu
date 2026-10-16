"""Unit tests for tools/joule_ledger. Run: python3 -m unittest discover tests/joule_ledger"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))

from joule_ledger.ledger import (  # noqa: E402
    FreshCounter, Span, attribute, estimate_idle_power, pe_split, span_record)
from joule_ledger.processor import enrich_span  # noqa: E402


def stale_counter(power, period, latency=0.0, poll=0.01, t_end=10.0, fresh=True):
    """Constant-power device whose counter publishes every `period` s."""
    c = FreshCounter(latency=latency, track_freshness=fresh)
    t = poll
    while t < t_end:
        published = int((t - latency) / period) * period
        c.observe(t, power * max(published, 0.0))
        t += poll
    return c


class FreshCounterTest(unittest.TestCase):
    def test_edges_only_on_change(self):
        c = stale_counter(10.0, 0.1, t_end=1.0)
        # 1 s at 100 ms publish period: ~10 edges, not ~100 polls.
        self.assertLessEqual(len(c.edges), 11)

    def test_bounds_contain_truth_constant_power(self):
        c = stale_counter(10.0, 0.1, latency=0.02, t_end=5.0)
        c.latency = 0.02
        for s, e in [(1.0, 1.03), (1.234, 1.5), (2.0, 4.0), (3.33, 3.34)]:
            lo, hi = c.bounds(s, e)
            self.assertLessEqual(lo, 10.0 * (e - s) + 1e-9)
            self.assertGreaterEqual(hi, 10.0 * (e - s) - 1e-9)

    def test_short_span_has_low_measured_fraction(self):
        c = stale_counter(10.0, 0.1, t_end=5.0)
        r = span_record(c, Span("x", 2.01, 2.04), 0.0)
        self.assertLess(r["measured_fraction"], 0.5)
        r = span_record(c, Span("y", 1.0, 4.0), 0.0)
        # Up to one publish period lost at each end: (3 - 0.2) / (3 + 0.2).
        self.assertGreater(r["measured_fraction"], 0.87)

    def test_uncovered_span_returns_none(self):
        c = stale_counter(10.0, 0.1, t_end=1.0)
        self.assertIsNone(c.bounds(0.9, 5.0))

    def test_ablation_bounds_are_falsely_tight(self):
        # Freshness off: a 30 ms span inside one 100 ms publish interval reads 0 J.
        c = stale_counter(10.0, 0.1, t_end=2.0, fresh=False)
        lo, hi = c.bounds(1.21, 1.24)
        self.assertEqual((lo, hi), (0.0, 0.0))

    def test_wraparound(self):
        c = FreshCounter(wrap=100.0)
        for t, raw in [(0.0, 90.0), (0.1, 95.0), (0.2, 3.0), (0.3, 8.0)]:
            c.observe(t, raw)
        self.assertEqual([e.value for e in c.edges], [90.0, 95.0, 103.0, 108.0])

    def test_random_monotone_counters_never_escape_bounds(self):
        rng = random.Random(7)
        for _ in range(50):
            # Random piecewise power, random publish period and latency <= bound.
            period, lat = rng.uniform(0.02, 0.2), rng.uniform(0.0, 0.03)
            steps = [(rng.uniform(0, 3), rng.uniform(0, 200)) for _ in range(40)]
            knots, t, e = [(0.0, 0.0)], 0.0, 0.0
            for d, pw in steps:
                t, e = t + d, e + d * pw
                knots.append((t, e))

            def E(x):
                for (t0, e0), (t1, e1) in zip(knots, knots[1:]):
                    if x <= t1:
                        return e0 + (e1 - e0) * (x - t0) / (t1 - t0)
                return knots[-1][1]

            c = FreshCounter(latency=0.03, quantum=1e-3)
            tp = 0.01
            while tp < t - 1:
                pub = int((tp - lat) / period) * period
                c.observe(tp, (E(pub) // 1e-3) * 1e-3 if pub > 0 else 0.0)
                tp += 0.01
            for _ in range(20):
                s = rng.uniform(1, t - 4)
                e2 = s + rng.uniform(0.001, 2)
                b = c.bounds(s, e2)
                self.assertIsNotNone(b)
                self.assertLessEqual(b[0], E(e2) - E(s) + 1e-9)
                self.assertGreaterEqual(b[1], E(e2) - E(s) - 1e-9)


class AttributeTest(unittest.TestCase):
    def test_conservation(self):
        c = stale_counter(50.0, 0.05, t_end=5.0)
        spans = [Span("a", 0.5, 1.5), Span("b", 1.0, 2.0, 3.0), Span("c", 3.0, 3.01)]
        per, buckets, total = attribute(c, spans, 20.0)
        self.assertAlmostEqual(sum(per.values()) + buckets["idle"] + buckets["unattributed"],
                               total, places=9)

    def test_overlap_split_by_weight(self):
        c = stale_counter(40.0, 0.01, t_end=5.0)
        per, _, _ = attribute(c, [Span("a", 1.0, 2.0, 1.0), Span("b", 1.0, 2.0, 3.0)], 0.0)
        self.assertAlmostEqual(per["b"] / per["a"], 3.0, places=6)

    def test_no_span_goes_unattributed_not_smeared(self):
        c = stale_counter(40.0, 0.01, t_end=5.0)
        per, buckets, total = attribute(c, [Span("a", 1.0, 2.0)], 10.0)
        # ~4.9 s of 30 W dynamic outside the 1 s span stays unattributed.
        self.assertGreater(buckets["unattributed"], 3 * per["a"])
        self.assertAlmostEqual(per["a"], 30.0, delta=1.0)

    def test_idle_estimate(self):
        c = stale_counter(25.0, 0.05, t_end=5.0)
        self.assertAlmostEqual(estimate_idle_power(c.intervals(), [Span("a", 1, 2)]), 25.0, 3)


class GateTest(unittest.TestCase):
    def rows(self, coupled, dvfs, seed=1):
        rng = random.Random(seed)
        out = []
        for _ in range(400):
            fP = rng.uniform(1.0, 4.4) if dvfs else 3.0
            fE = rng.uniform(1.0, 3.0) if dvfs else 2.2
            v = lambda f: 0.65 + 0.13 * (f - 0.8)  # noqa: E731
            vP, vE = (v(max(fP, fE)),) * 2 if coupled else (v(fP), v(fE))
            cp, ce = rng.uniform(0, 8) * fP * 1e7, rng.uniform(0, 16) * fE * 1e7
            j = 1.1 * cp / 1e9 * vP ** 2 + 0.35 * ce / 1e9 * vE ** 2 + 0.02
            out.append((0.01, cp, ce, max(fP, fE), j))
        return out

    def test_refuses_shared_rail_dvfs(self):
        self.assertFalse(pe_split(self.rows(True, True))["accepted"])

    def test_accepts_fixed_frequency(self):
        g = pe_split(self.rows(False, False))
        self.assertTrue(g["accepted"])
        self.assertAlmostEqual(g["aP"] / g["aE"], (1.1 * 0.936 ** 2) / (0.35 * 0.832 ** 2), delta=0.05)


class ProcessorTest(unittest.TestCase):
    def test_enrich_span_attributes(self):
        c = stale_counter(40.0, 0.02, t_end=5.0)
        span = {"trace_id": "t", "span_id": "s1", "start": 1.0, "end": 2.0,
                "attributes": {"k8s.pod.uid": "pod-1"}}
        out = enrich_span(span, {"nvidia.0": c}, {"pod-1": ["nvidia.0"]},
                          {"nvidia.0": {"s1": 12.5}})
        a = out["attributes"]
        self.assertEqual(a["k3dgpu.energy.nvidia.0.joules"], 12.5)
        self.assertEqual(a["k3dgpu.energy.method"], "fresh_edges_v1")
        self.assertIn("k3dgpu.energy.nvidia.0.measured_fraction", a)
        self.assertEqual(a["k3dgpu.energy.cpu.split"], "unidentified")

    def test_pending_until_covered(self):
        c = stale_counter(40.0, 0.02, t_end=1.5)
        span = {"span_id": "s1", "start": 1.0, "end": 2.0,
                "attributes": {"k8s.pod.uid": "pod-1"}}
        self.assertIsNone(enrich_span(span, {"nvidia.0": c}, {"pod-1": ["nvidia.0"]},
                                      {"nvidia.0": {"s1": 1.0}}))


class SamplerTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        r = self.tmp.name
        for d, name, val in [("intel-rapl:0", "package-0", 5_000_000),
                             ("intel-rapl:0:0", "core", 3_000_000),
                             ("intel-rapl:0:1", "uncore", 1_000_000),
                             ("intel-rapl:1", "psys", 9_000_000),
                             ("intel-rapl-mmio:0", "package-0", 1)]:
            p = os.path.join(r, "sys/class/powercap", d)
            os.makedirs(p)
            for f, v in [("name", name), ("energy_uj", val),
                         ("max_energy_range_uj", 262143328850)]:
                with open(os.path.join(p, f), "w") as fh:
                    fh.write(f"{v}\n")
        fd = os.path.join(r, "proc/4242/fdinfo")
        os.makedirs(fd)
        text = ("pos:\t0\nflags:\t02100002\ndrm-driver:\ti915\ndrm-pdev:\t0000:00:02.0\n"
                "drm-client-id:\t17\ndrm-engine-render:\t123456789 ns\n"
                "drm-engine-video:\t0 ns\ndrm-engine-capacity-video:\t2\n")
        for n in ("5", "6"):  # two fds to the same DRM client
            with open(os.path.join(fd, n), "w") as fh:
                fh.write(text)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rapl_zones_skip_mmio_duplicate(self):
        from joule_ledger.sampler import rapl_zones, read_rapl
        z = rapl_zones(self.tmp.name)
        self.assertEqual(sorted(z), ["rapl.core.0", "rapl.package-0", "rapl.psys",
                                     "rapl.uncore.0"])
        self.assertEqual(read_rapl(z["rapl.core.0"][0]), 3.0)

    def test_fdinfo_one_client_once(self):
        from joule_ledger.sampler import drm_busy_by_pid
        busy = drm_busy_by_pid(self.tmp.name)
        self.assertEqual(busy, {4242: {("0000:00:02.0", 17):
                                       {"drm-engine-render": 123456789,
                                        "drm-engine-video": 0}}})

    def test_daemon_ledger_without_nvml(self):
        from joule_ledger.daemon import Ledger
        led = Ledger(self.tmp.name, 0.0, "libdoes-not-exist.so")
        self.assertEqual(sorted(led.counters), ["rapl.core.0", "rapl.package-0",
                                                "rapl.psys", "rapl.uncore.0"])
        led.tick()
        self.assertEqual(len(led.counters["rapl.psys"].edges), 1)


class TrimTest(unittest.TestCase):
    def test_trim_invalidates_index(self):
        c = stale_counter(10.0, 0.1, t_end=3.0)
        c.bounds(1.0, 2.0)
        c.trim(2.0)
        for t in (3.05, 3.15, 3.25, 3.35):
            c.observe(t, 10.0 * t)
        lo, hi = c.bounds(2.2, 3.1)
        self.assertLessEqual(lo, 9.0 + 1e-9)
        self.assertGreaterEqual(hi, 9.0 - 1e-9)


if __name__ == "__main__":
    unittest.main()
