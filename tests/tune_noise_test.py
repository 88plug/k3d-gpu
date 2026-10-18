#!/usr/bin/env python3
"""Unit tests for bench/tune-noise/analyze.py (H19 amendment A1). No cluster, no hardware."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bench", "tune-noise"))
sys.path.insert(0, os.path.join(ROOT, "console"))
import analyze  # noqa: E402
import tune  # noqa: E402

CAND = {"device": "CPU", "model": "OpenVINO/Qwen3-4B-int4-ov", "cores": "performance", "cpus": 8,
        "max_num_seqs": 256, "cache_size": 0, "enable_prefix_caching": True, "plugin_config": {}}
BUDGET = {"requests": 4, "max_tokens": 64, "repeats": 1, "concurrency": [1, 4, 8]}


def window(tag, busy):
    return {"tag": tag, "level": "typical", "busy_pct": busy, "load1": 10.0, "pkg_w": 90.0,
            "throttle_per_min": 100.0, "swap_used_mib": 1000.0, "swap_delta_mib": 0.0}


def write_run(d, crowned, cand_tps, order="A B B A A B", busy_b=50.0):
    sim = tune.SimEvaluator(lambda c: (cand_tps if not c.get("baseline") else 10.0, 1.0, 90.0, 0.5, 1.0),
                            noise=0.01, seed=3)
    with open(os.path.join(d, "candidate.json"), "w") as f:
        json.dump({"candidate": CAND, "crowned": crowned}, f)
    with open(os.path.join(d, "load.jsonl"), "w") as lf:
        for i, arm in enumerate(order.split(), 1):
            r = os.path.join(d, f"rep-{i:02d}")
            os.makedirs(r)
            with open(os.path.join(r, "arm"), "w") as f:
                f.write(arm + "\n")
            cfg = {"baseline": True} if arm == "A" else CAND
            with open(os.path.join(r, "result.json"), "w") as f:
                json.dump(sim(cfg, BUDGET), f)
            lf.write(json.dumps(window(arm, 50.0 if arm == "A" else busy_b)) + "\n")


class Verdict(unittest.TestCase):
    def run_case(self, **kw):
        with tempfile.TemporaryDirectory() as d:
            write_run(d, **kw)
            return analyze.verdict(d, resamples=200)

    def test_no_crown_and_no_gain_is_supported(self):
        v = self.run_case(crowned=False, cand_tps=10.0)
        self.assertFalse(v["gates_pass"])
        self.assertEqual(v["verdict"], "SUPPORTED")
        self.assertEqual(v["n"]["paired"], 3)
        self.assertGreater(v["confidence"], 0.9)

    def test_crown_that_fails_replication_is_refuted(self):
        v = self.run_case(crowned=True, cand_tps=10.0)
        self.assertEqual(v["verdict"], "REFUTED")

    def test_missed_real_gain_is_inconclusive(self):
        v = self.run_case(crowned=False, cand_tps=13.0)
        self.assertTrue(v["gates_pass"], v.get("gate_reasons"))
        self.assertEqual(v["verdict"], "INCONCLUSIVE")

    def test_unmatched_load_is_inconclusive_with_would_be(self):
        v = self.run_case(crowned=False, cand_tps=10.0, busy_b=70.0)
        self.assertFalse(v["load_matched"])
        self.assertEqual(v["verdict"], "INCONCLUSIVE")
        self.assertEqual(v["verdict_if_matched"], "SUPPORTED")

    def test_too_few_trials_is_inconclusive(self):
        v = self.run_case(crowned=False, cand_tps=10.0, order="A B B A")
        self.assertEqual(v["verdict"], "INCONCLUSIVE")


class Candidate(unittest.TestCase):
    def test_winner_then_pending_then_best_screened(self):
        other = dict(CAND, cpus=4)
        self.assertEqual(analyze.candidate({"decision": "tuned", "winner": CAND}),
                         {"candidate": tune.validate_config(CAND), "crowned": True})
        self.assertEqual(analyze.candidate({"decision": "default", "pending": {"config": CAND}})["candidate"],
                         tune.validate_config(CAND))
        prof = {"decision": "default", "objective": "throughput", "last_step": {"screened": [
            {"config": other, "ok": True, "score": 40.0}, {"config": CAND, "ok": True, "score": 45.0},
            {"config": dict(CAND, cpus=2), "ok": False, "score": None}]}}
        self.assertEqual(analyze.candidate(prof), {"candidate": tune.validate_config(CAND), "crowned": False})
        self.assertIsNone(analyze.candidate({"decision": "default", "last_step": {"screened": []}})["candidate"])


if __name__ == "__main__":
    unittest.main()
