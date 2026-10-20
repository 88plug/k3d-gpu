#!/usr/bin/env python3
"""Unit tests for bench/apb/h7x.py (the H7x decision rule). Stdlib only."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h7x  # noqa: E402

ORDER = "A B B A A B B A A B C".split()


def write_run(d, tps, tokens=256, n=5, skip=()):
    """tps: {engine: tok/s or [per-block tok/s in run order]}"""
    seen = {}
    for i, e in enumerate(ORDER, start=1):
        if i in skip:
            continue
        k = seen.get(e, 0)
        seen[e] = k + 1
        v = tps[e][k] if isinstance(tps[e], list) else tps[e]
        dec = {"phases": [{"samples": [{"ok": True, "output_tokens": tokens, "decode_tokens_per_s": v + j * 0.01}
                                        for j in range(n)], "output_tokens_total": tokens * n}]}
        pre = {"phases": [{"samples": [{"ok": True, "ttft_s": 2.0}] * 5}]}
        c4 = {"phases": [{"samples": [], "aggregate_tokens_per_s": 3 * v}]}
        for w, obj in (("decode", dec), ("prefill", pre), ("conc4", c4)):
            json.dump(obj, open(os.path.join(d, f"b{i}-{e}-{w}.json"), "w"))
        json.dump({"joules": 2000.0, "seconds": 20.0}, open(os.path.join(d, f"b{i}-{e}-idle-energy.json"), "w"))
        json.dump({"joules": 9000.0, "seconds": 60.0}, open(os.path.join(d, f"b{i}-{e}-decode-energy.json"), "w"))


def run(tps, **kw):
    with tempfile.TemporaryDirectory() as d:
        write_run(d, tps, **kw)
        return h7x.score(h7x.blocks(d))


class Rule(unittest.TestCase):
    def test_within_15_percent_is_supported(self):
        v = run({"A": 20.0, "B": 18.5, "C": 19.0})
        self.assertEqual(v["verdict"], "SUPPORTED")
        self.assertEqual(len(v["pair_ratios"]), 5)
        self.assertAlmostEqual(v["C_over_B"], 19.0 / 18.5, places=2)

    def test_consistent_gap_is_refuted(self):
        self.assertEqual(run({"A": 20.0, "B": 16.0, "C": 16.0})["verdict"], "REFUTED")

    def test_vllm_faster_is_refuted_too(self):
        self.assertEqual(run({"A": 14.0, "B": 18.0, "C": 18.0})["verdict"], "REFUTED")

    def test_h7_like_straddle_is_inconclusive(self):
        # pooled gap above 15% but one block pair below it, as in the i9 run
        v = run({"A": [20.0, 20.0, 20.0, 20.0, 18.0], "B": 17.0, "C": 17.0})
        self.assertEqual(v["verdict"], "INCONCLUSIVE")

    def test_short_outputs_void_blocks(self):
        v = run({"A": 20.0, "B": 18.5, "C": 18.5}, tokens=200)
        self.assertEqual(v["verdict"], "INCONCLUSIVE")
        self.assertEqual(v["void"]["A"], 5)

    def test_missing_c_block_is_reported_none(self):
        v = run({"A": 20.0, "B": 18.5, "C": 18.5}, skip=(11,))
        self.assertEqual((v["verdict"], v["C_over_B"]), ("SUPPORTED", None))

    def test_cap_cut_run_with_three_a_blocks_is_inconclusive(self):
        v = run({"A": 20.0, "B": 18.5, "C": 18.5}, skip=(8, 9, 10, 11))
        self.assertEqual(v["verdict"], "INCONCLUSIVE")

    def test_incremental_energy_per_token(self):
        v = run({"A": 20.0, "B": 18.5, "C": 18.5})
        # (9000 J - 100 W x 60 s) / 1280 tokens
        self.assertAlmostEqual(v["arms"]["A"]["decode_incremental_j_per_token_median"], 3000 / 1280)


if __name__ == "__main__":
    unittest.main()
