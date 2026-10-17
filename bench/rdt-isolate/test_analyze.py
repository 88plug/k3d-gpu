#!/usr/bin/env python3
"""Unit tests for analyze.py and client.py (stdlib unittest, no network)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze  # noqa: E402
import client  # noqa: E402

ORDER = [("Q", "ME15", "ME4", "MC4"), ("MC4", "ME4", "ME15", "Q"), ("Q", "ME15", "ME4", "MC4")]


def synth(base, mult, imc=None, swap=None, jitter=0.01):
    """Requests and blocks for 3 rounds; latency = base * mult[arm] * (1 + small ramp)."""
    reqs, blocks, b = [], [], 0
    for rd, arms in enumerate(ORDER, 1):
        for arm in arms:
            b += 1
            for i in range(11):
                lat = base * mult[arm] * (1 + jitter * (i % 5))
                reqs.append({"arm": arm, "block": b, "round": rd, "i": i, "warmup": i == 0,
                             "ok": True, "latency_s": lat, "ttft_s": 0.1, "tokens": 32})
            blocks.append({"block": b, "round": rd, "arm": arm, "void": False,
                           "imc_gbs": (imc or {"Q": 2, "ME15": 40, "ME4": 30, "MC4": 30})[arm],
                           "pswpin": (swap or {}).get((rd, arm), 0)})
    return reqs, blocks


class P95(unittest.TestCase):
    def test_nearest_rank(self):
        self.assertEqual(analyze.p95(list(range(1, 31))), 29)
        self.assertEqual(analyze.p95([5]), 5)


class Verdicts(unittest.TestCase):
    def v(self, *a, **k):
        return analyze.verdict(*synth(*a, **k), n_boot=300)

    def test_supported(self):
        out = self.v(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.6, "MC4": 1.15})
        self.assertEqual(out["verdict"], "SUPPORTED", out)
        self.assertAlmostEqual(out["R"], 0.25, places=2)
        self.assertEqual(out["n"]["MC4"], 30)

    def test_core_count_only_is_refuted(self):
        # MC4 beats ME15 only by having 4 workers; at equal workers there is no gain.
        out = self.v(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.3, "MC4": 1.3})
        self.assertEqual(out["verdict"], "REFUTED", out)
        self.assertAlmostEqual(out["R"], 1.0, places=2)

    def test_throttled_hog_is_not_isolation(self):
        out = self.v(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.6, "MC4": 1.15},
                     imc={"Q": 2, "ME15": 40, "ME4": 30, "MC4": 20})
        self.assertEqual(out["verdict"], "REFUTED as stated", out)
        self.assertIn("throttling", out["reason"])

    def test_missing_imc_is_inconclusive(self):
        reqs, blocks = synth(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.6, "MC4": 1.15})
        for b in blocks:
            b["imc_gbs"] = None
        out = analyze.verdict(reqs, blocks, n_boot=100)
        self.assertEqual(out["verdict"], "INCONCLUSIVE", out)
        self.assertIn("IMC", out["reason"])

    def test_nothing_to_isolate(self):
        out = self.v(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.05, "MC4": 1.05})
        self.assertIn("nothing to isolate", out["reason"])

    def test_refuted(self):
        out = self.v(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.6, "MC4": 1.55})
        self.assertEqual(out["verdict"], "REFUTED", out)

    def test_refuted_as_stated(self):
        out = self.v(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.6, "MC4": 1.39})
        self.assertEqual(out["verdict"], "REFUTED as stated", out)

    def test_blind_probe(self):
        out = self.v(2.3, {"Q": 1.0, "ME15": 1.1, "ME4": 1.0, "MC4": 1.0})
        self.assertEqual(out["verdict"], "INCONCLUSIVE")
        self.assertIn("positive control", out["reason"])

    def test_swap_flip_is_inconclusive(self):
        # Round 2's MC4 block is slow and swap-flagged: with it R > 0.5, without it R ~0.3.
        reqs, blocks = synth(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.6, "MC4": 1.15}, swap={(2, "MC4"): 9999})
        for r in reqs:
            if r["round"] == 2 and r["arm"] == "MC4":
                r["latency_s"] *= 2
        out = analyze.verdict(reqs, blocks, n_boot=300)
        self.assertEqual(out["verdict"], "INCONCLUSIVE", out)
        # Dropping the block leaves round 2 without MC4, so 3/3 agreement cannot hold.
        self.assertEqual(out["verdict_without_swap_blocks"], "REFUTED as stated")
        self.assertIn("swap-flag sensitivity", out["reason"])

    def test_void_and_failed_requests_excluded(self):
        reqs, blocks = synth(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.3, "MC4": 1.3})
        blocks[3]["void"] = True
        reqs[1]["ok"] = False
        out = analyze.verdict(reqs, blocks, n_boot=100)
        self.assertEqual(out["n"]["MC4"], 20)
        self.assertEqual(out["n"]["Q"], 29)
        self.assertEqual(out["void_blocks"], [4])

    def test_no_arm_data(self):
        reqs, blocks = synth(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.3, "MC4": 1.3})
        reqs = [r for r in reqs if r["arm"] != "MC4"]
        self.assertEqual(analyze.verdict(reqs, blocks, n_boot=10)["verdict"], "INCONCLUSIVE")


def windows(pkg=None, level="typical"):
    """Background windows, 3 per block, the same load in every arm unless pkg overrides."""
    out = []
    for arm in ("Q", "ME15", "ME4", "MC4"):
        for _ in range(9):
            out.append({"tag": arm, "level": level, "busy_pct": 37.0, "load1": 20.0,
                        "pkg_w": (pkg or {}).get(arm, 60.0), "throttle_per_min": 5.0,
                        "swap_used_mib": 1000.0, "swap_delta_mib": 0.0})
    return out


class MatchedLoad(unittest.TestCase):
    """Amendment A2: unmatched background load makes the level INCONCLUSIVE."""

    def test_matched_keeps_the_a1_verdict(self):
        reqs, blocks = synth(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.6, "MC4": 1.55})
        out = analyze.verdict(reqs, blocks, n_boot=200, windows=windows())
        self.assertTrue(out["load_matched"])
        self.assertEqual(out["verdict"], "REFUTED", out)
        self.assertEqual(out["verdict_if_matched"], "REFUTED")
        self.assertEqual(out["load_by_arm"]["MC4"]["windows"], 9)
        self.assertGreater(out["P_R_ge_0.8"], 0.5)

    def test_unmatched_is_inconclusive_naming_the_metric(self):
        reqs, blocks = synth(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.6, "MC4": 1.55})
        out = analyze.verdict(reqs, blocks, n_boot=200, windows=windows(pkg={"MC4": 80.0}))
        self.assertFalse(out["load_matched"])
        self.assertEqual(out["verdict"], "INCONCLUSIVE")
        self.assertEqual(out["verdict_if_matched"], "REFUTED")
        self.assertIn("pkg_w", out["reason"])

    def test_high_but_matched_load_does_not_void(self):
        reqs, blocks = synth(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.6, "MC4": 1.55})
        w = windows(level="heavy")
        for x in w:
            x.update(busy_pct=100.0, load1=40.0, pkg_w=110.0)
        self.assertEqual(analyze.verdict(reqs, blocks, n_boot=100, windows=w)["verdict"], "REFUTED")


class Client(unittest.TestCase):
    def test_prompts_are_unique(self):
        self.assertEqual(len({client.prompt(n) for n in range(1000)}), 1000)


if __name__ == "__main__":
    unittest.main()
