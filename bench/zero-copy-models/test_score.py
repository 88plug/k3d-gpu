#!/usr/bin/env python3
"""Unit tests for bench/zero-copy-models/score.py (the H28 outcome table). Stdlib only."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score  # noqa: E402

MIB = 1024
SIZE = MIB * 1024  # kB
MATCHED = {"verdict": "MATCHED"}


def csv_text(t_ready=2980, c_ready=6333, t_pss=132.5 * 1024, c_pss=1028.5 * 1024,
             t_res=1.0 * SIZE, c_res=9.0 * SIZE, void=(), level="typical", n=8):
    lines = ["# header", "meta,k3s=v1.37.1"]
    for k, arm in enumerate("CTTCCT", start=1):
        ready, pss, res = (t_ready, t_pss, t_res) if arm == "T" else (c_ready, c_pss, c_res)
        for i in range(1, n + 1):
            lines.append(f"pod,{arm},{k},{i},{ready + i},{pss + 100},{pss},{0.95 * SIZE},1,2,3,scored,{level}")
        if k in void:
            lines.append(f"round,{arm},{k},5,VOID-cold,scored,{level}")
        else:
            lines.append(f"round,{arm},{k},0,{res},1,2,-5,1.0,10,0,130,scored,{level}")
    lines.append("pod,C,0,1,9999,1,1,1,1,2,3,warmup,typical")
    return "\n".join(lines) + "\n"


def score_text(text, matched=MATCHED, level="typical"):
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write(text)
    try:
        return score.verdict(score.rounds(f.name, level), matched, MIB, 8)
    finally:
        os.unlink(f.name)


class Table(unittest.TestCase):
    def test_h28_a2_numbers_are_supported(self):
        v = score_text(csv_text())
        self.assertEqual(v["verdict"], "SUPPORTED")
        self.assertAlmostEqual(v["ready_ratio"], 2984.5 / 6337.5, places=3)
        self.assertTrue(v["separated"])

    def test_time_part_refuted(self):
        v = score_text(csv_text(t_ready=4000, c_ready=6000))
        self.assertEqual((v["verdict"], v["row"].split(":")[0]), ("REFUTED", "time part"))

    def test_no_sharing_refuted(self):
        v = score_text(csv_text(t_pss=0.9 * SIZE, t_res=8.0 * SIZE))
        self.assertEqual((v["verdict"], v["row"]), ("REFUTED", "no page-cache sharing"))

    def test_probe_blind(self):
        v = score_text(csv_text(c_res=2.0 * SIZE))
        self.assertEqual(v["verdict"], "INCONCLUSIVE")
        self.assertTrue(v["row"].startswith("probe blind"))

    def test_two_void_rounds(self):
        v = score_text(csv_text(void=(2, 3)))
        self.assertEqual(v["verdict"], "INCONCLUSIVE")
        self.assertEqual(v["void_rounds"]["T"], 2)

    def test_unmatched_load_keeps_the_numbers(self):
        v = score_text(csv_text(), matched={"verdict": "INCONCLUSIVE"})
        self.assertEqual((v["verdict"], v["verdict_if_matched"]), ("INCONCLUSIVE", "SUPPORTED"))

    def test_other_level_and_warmup_ignored(self):
        v = score_text(csv_text(level="heavy") + csv_text(), level="typical")
        self.assertEqual(len(v["rounds"]), 6)


if __name__ == "__main__":
    unittest.main()
