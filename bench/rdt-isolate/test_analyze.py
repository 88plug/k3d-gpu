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
        out = self.v(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.3, "MC4": 1.3})
        self.assertEqual(out["verdict"], "SUPPORTED", out)
        self.assertAlmostEqual(out["R"], 0.3, places=2)
        self.assertEqual(out["n"]["MC4"], 30)
        self.assertEqual(out["H26b"], "core count, not L2 topology")

    def test_refuted(self):
        out = self.v(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.9, "MC4": 1.9})
        self.assertEqual(out["verdict"], "REFUTED", out)

    def test_refuted_as_stated(self):
        out = self.v(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.65, "MC4": 1.65})
        self.assertEqual(out["verdict"], "REFUTED as stated", out)

    def test_blind_probe(self):
        out = self.v(2.3, {"Q": 1.0, "ME15": 1.1, "ME4": 1.0, "MC4": 1.0})
        self.assertEqual(out["verdict"], "INCONCLUSIVE")
        self.assertIn("positive control", out["reason"])

    def test_starved_hog(self):
        out = self.v(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.3, "MC4": 1.3},
                     imc={"Q": 2, "ME15": 40, "ME4": 30, "MC4": 5})
        self.assertIn("starved", out["reason"])

    def test_swap_flip_is_inconclusive(self):
        # Round 2's MC4 block is slow and swap-flagged: with it R > 0.5, without it R ~0.3.
        reqs, blocks = synth(2.3, {"Q": 1.0, "ME15": 2.0, "ME4": 1.3, "MC4": 1.3}, swap={(2, "MC4"): 9999})
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


class Client(unittest.TestCase):
    def test_prompts_are_unique(self):
        self.assertEqual(len({client.prompt(n) for n in range(1000)}), 1000)


if __name__ == "__main__":
    unittest.main()
