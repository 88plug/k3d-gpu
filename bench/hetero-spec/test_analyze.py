#!/usr/bin/env python3
"""Unit tests for bench/hetero-spec/analyze.py (H22 outcome table). Stdlib only."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze  # noqa: E402

P0_OK = {"kind": "p0", "draft_loaded": True, "gpu_busy_A": 0, "gpu_busy_C": 1000}


def run(a, b, c, n=8, p0=P0_OK, flag=()):
    recs = [p0]
    for i in range(n):
        for arm, v in (("A", a), ("B", b), ("C", c[i] if isinstance(c, list) else c),
                       ("DP", 66.0), ("DG", 33.0)):
            recs.append({"kind": "req", "round": i + 1, "arm": arm, "decode_tps": v,
                         "flagged": (i + 1) in flag})
    return analyze.verdict(recs)


class Verdicts(unittest.TestCase):
    def test_sign_test(self):
        self.assertAlmostEqual(analyze.sign_test_p(8, 8), 0.0078125)
        self.assertAlmostEqual(analyze.sign_test_p(4, 8), 1.0)
        self.assertEqual(analyze.sign_test_p(0, 0), 1.0)

    def test_predicted_refuted(self):
        v = run(15.7, 19.0, 12.8)
        self.assertEqual(v["verdict"], "REFUTED")
        self.assertAlmostEqual(v["R"], 12.8 / 19.0)
        self.assertAlmostEqual(v["confidence"], 1 - 0.0078125, places=4)

    def test_supported(self):
        v = run(15.7, 19.0, 21.0)
        self.assertEqual(v["verdict"], "SUPPORTED")

    def test_c_beats_b_but_not_a_enough(self):
        # C/B 1.06 but C/A 1.05 < 1.10: not supported, not refuted
        self.assertEqual(run(15.7, 15.55, 16.5)["verdict"], "INCONCLUSIVE")

    def test_tie_inconclusive(self):
        self.assertEqual(run(15.7, 19.0, [19.1, 18.9] * 4)["verdict"], "INCONCLUSIVE")

    def test_c_below_a_refuted(self):
        self.assertEqual(run(15.7, 14.0, 15.0)["verdict"], "REFUTED")

    def test_p0_fail(self):
        p0 = dict(P0_OK, gpu_busy_C=0)
        v = run(15.7, 19.0, 25.0, p0=p0)
        self.assertEqual(v["verdict"], "REFUTED")
        self.assertIn("P0", v["outcome"])

    def test_p0_missing_is_fail(self):
        self.assertIn("P0", run(15.7, 19.0, 25.0, p0={})["outcome"])

    def test_calibration_out(self):
        self.assertEqual(run(11.0, 19.0, 12.8)["verdict"], "INCONCLUSIVE")

    def test_too_many_flagged(self):
        self.assertEqual(run(15.7, 19.0, 12.8, flag=(1, 2, 3, 4))["verdict"], "INCONCLUSIVE")

    def test_flagged_sensitivity_holds(self):
        v = run(15.7, 19.0, 12.8, flag=(2,))
        self.assertEqual(v["verdict"], "REFUTED")
        self.assertEqual(v["sensitivity_verdict"], "REFUTED")

    def test_partial_rounds(self):
        v = run(15.7, 19.0, 12.8, n=3)
        self.assertEqual(v["n_rounds"], 3)
        self.assertEqual(v["verdict"], "REFUTED")

    def test_empty(self):
        self.assertEqual(analyze.verdict([P0_OK])["verdict"], "INCONCLUSIVE")

    def test_draft_latency(self):
        v = run(15.7, 19.0, 12.8)
        self.assertAlmostEqual(v["t_d_ms_DG"], 1000 / 33.0)


if __name__ == "__main__":
    unittest.main()
