#!/usr/bin/env python3
"""Tests for bench/shared-prefix/probe.py: prompt generator and scorer (synthetic rounds)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe  # noqa: E402


def rnd(arm, seed, added, a1=9.6, c=9.5, drss=110.0, tokens=1650):
    req = lambda t: {"ttft": t, "total": t + 0.5, "text_len": 5, "prompt_tokens": tokens, "error": None}  # noqa: E731
    S = [dict(req(a1), agent="a1")] + [dict(req(t), agent=f"a{i + 2}") for i, t in enumerate(added)]
    S.append(dict(req(c), agent="c"))
    return {"arm": arm, "seed": seed, "S": S, "B": [dict(req(1.0), agent=f"b{k}") for k in range(6)],
            "drss_per_agent_mib": drss, "swap_in_burst": False, "graph_ok": True}


class Gen(unittest.TestCase):
    def test_deterministic_and_control(self):
        a, b = probe.gen(7), probe.gen(7)
        self.assertEqual(a, b)
        self.assertNotEqual(a["system"], a["control_system"])
        i = next(k for k, (x, y) in enumerate(zip(a["system"], a["control_system"])) if x != y)
        self.assertTrue(60 <= i <= 90, i)  # about token 16
        self.assertEqual(len(a["tools"]), 12)
        self.assertNotEqual(probe.gen(8)["system"], a["system"])


class Score(unittest.TestCase):
    def run_score(self, rounds):
        with tempfile.TemporaryDirectory() as d:
            files = []
            for i, r in enumerate(rounds):
                f = os.path.join(d, f"round-{i}.json")
                json.dump(r, open(f, "w"))
                files.append(f)
            return probe.score(files)

    def test_supported(self):
        on = [rnd("ON", s, [0.9, 0.85, 0.95, 0.9, 0.88], drss=12.0) for s in (1, 4, 5)]
        off = [rnd("OFF", s, [9.6, 9.4, 9.7, 9.5, 9.8]) for s in (2, 3, 6)]
        out = self.run_score(on + off)
        self.assertEqual(out["T"], "SUPPORTED")
        self.assertGreater(out["ci95"][0], 4)
        self.assertEqual(out["confidence_pairs_ge_4x"], 1.0)
        self.assertTrue(out["M"]["verdict"].startswith("SUPPORTED"))

    def test_refuted(self):
        on = [rnd("ON", s, [9.0, 9.1, 9.2, 9.0, 9.1]) for s in (1, 4, 5)]
        off = [rnd("OFF", s, [9.6, 9.4, 9.7, 9.5, 9.8]) for s in (2, 3, 6)]
        self.assertEqual(self.run_score(on + off)["T"], "REFUTED")

    def test_warm_control_is_void(self):
        on = [rnd("ON", s, [0.9] * 5, c=0.9) for s in (1, 4, 5)]
        off = [rnd("OFF", s, [9.6] * 5) for s in (2, 3, 6)]
        self.assertTrue(self.run_score(on + off)["T"].startswith("VOID"))

    def test_token_mismatch_inconclusive(self):
        on = [rnd("ON", s, [0.9] * 5, tokens=1600) for s in (1, 4, 5)]
        off = [rnd("OFF", s, [9.6] * 5) for s in (2, 3, 6)]
        self.assertEqual(self.run_score(on + off)["T"], "INCONCLUSIVE")

    def test_two_void_rounds_inconclusive(self):
        on = [rnd("ON", s, [0.9] * 5) for s in (1, 4, 5)]
        for r in on[:2]:
            r["graph_ok"] = False
        off = [rnd("OFF", s, [9.6] * 5) for s in (2, 3, 6)]
        self.assertTrue(self.run_score(on + off)["T"].startswith("INCONCLUSIVE"))

    def test_missing_arm(self):
        on = [rnd("ON", s, [0.9] * 5) for s in (1, 4, 5)]
        self.assertTrue(self.run_score(on)["T"].startswith("INCONCLUSIVE"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
