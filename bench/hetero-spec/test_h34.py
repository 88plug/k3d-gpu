#!/usr/bin/env python3
"""Unit tests for bench/hetero-spec/h34.py (H34 outcome table). Stdlib only."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h34  # noqa: E402

P0 = {"kind": "p0", "ready": True}


def win(arm, level="typical", busy=37.0, pkg=90.0):
    return {"tag": arm, "level": level, "busy_pct": busy, "load1": 10.0, "pkg_w": pkg,
            "throttle_per_min": 0.0, "swap_used_mib": 100.0, "swap_delta_mib": 0.0}


def run(a_tps, b_tps, a_j, b_j, n=10, level="typical", windows=None, b_jitter=0.0):
    recs = [P0]
    for i in range(n):
        j = b_jitter * (i % 3 - 1)
        recs += [{"kind": "req", "level": level, "pair": i, "arm": "A", "decode_tps": a_tps,
                  "tokens_ok": True, "energy_j": a_j, "text_head": "x"},
                 {"kind": "req", "level": level, "pair": i, "arm": "B", "decode_tps": b_tps + j,
                  "tokens_ok": True, "energy_j": b_j, "text_head": "x"}]
    if windows is None:
        windows = [win("A", level), win("B", level)] * n
    return h34.verdict(recs, windows)


class Verdicts(unittest.TestCase):
    def test_supported(self):
        v = run(12.0, 16.0, 2000.0, 1500.0)
        self.assertEqual(v["verdict"], "SUPPORTED")
        lv = v["levels"]["typical"]
        self.assertTrue(lv["load_matched"])
        self.assertEqual(lv["b_faster_pairs"], 10)
        self.assertEqual(lv["greedy_identical_pairs"], 10)
        self.assertGreater(lv["confidence"], 0.99)

    def test_refuted_small_gain(self):
        v = run(12.0, 13.0, 2000.0, 1900.0)
        self.assertEqual(v["verdict"], "REFUTED")

    def test_refuted_energy_leg(self):
        v = run(12.0, 16.0, 2000.0, 1900.0)  # fast but J ratio 0.95 > 0.85
        self.assertEqual(v["verdict"], "REFUTED")

    def test_high_but_matched_load_is_not_void(self):
        ws = [win("A", busy=99.0, pkg=150.0), win("B", busy=99.5, pkg=151.0)] * 10
        self.assertEqual(run(12.0, 16.0, 2000.0, 1500.0, windows=ws)["verdict"], "SUPPORTED")

    def test_unmatched_load_is_inconclusive(self):
        ws = [win("A", busy=30.0), win("B", busy=60.0)] * 10
        v = run(12.0, 16.0, 2000.0, 1500.0, windows=ws)
        self.assertEqual(v["verdict"], "INCONCLUSIVE")
        self.assertIn("load unmatched", v["outcome"])
        self.assertIn("busy_pct", v["outcome"])

    def test_too_few_pairs(self):
        v = run(12.0, 16.0, 2000.0, 1500.0, n=7)
        self.assertEqual(v["verdict"], "INCONCLUSIVE")
        self.assertIn("< 8", v["outcome"])

    def test_bad_token_count_dropped(self):
        recs = [P0] + [{"kind": "req", "level": "typical", "pair": i, "arm": arm, "decode_tps": 12.0,
                        "tokens_ok": False, "energy_j": 1.0} for i in range(10) for arm in "AB"]
        v = h34.verdict(recs, [win("A"), win("B")])
        self.assertEqual(v["levels"]["typical"]["dropped_requests"], 20)
        self.assertEqual(v["verdict"], "INCONCLUSIVE")

    def test_not_ready(self):
        self.assertIn("BLOCKED", h34.verdict([{"kind": "p0", "ready": False}], [])["outcome"])

    def test_levels_separate_and_headline_typical(self):
        recs = run(12.0, 16.0, 2000.0, 1500.0)
        heavy = run(12.0, 12.5, 2000.0, 1950.0, level="heavy")
        self.assertEqual(recs["levels"]["typical"]["verdict"], "SUPPORTED")
        self.assertEqual(heavy["levels"]["heavy"]["verdict"], "REFUTED")
        self.assertEqual(heavy["verdict"], "INCONCLUSIVE")  # no typical level in that run

    def test_unresolved(self):
        v = run(12.0, 14.6, 2000.0, 1700.0, b_jitter=4.0)  # median 1.22, CI straddles 1.0
        self.assertEqual(v["verdict"], "INCONCLUSIVE")
        self.assertIn("not resolved", v["outcome"])


if __name__ == "__main__":
    unittest.main()
