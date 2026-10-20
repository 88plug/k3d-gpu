#!/usr/bin/env python3
"""Tests for the H55 scoring (analyze.py): the verdict rule on fixture tables and
the reader of a results tree."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyze  # noqa: E402

Q_RUN_MEDIANS = (7.00, 7.05, 7.10, 7.02, 7.08)  # spread 0.10 s, about 1.4% of the median
N0_BOGO = 20000.0
BASE = {"N0": (0.40, 0.0), "K50": (0.25, 0.50), "K25": (0.12, 0.75), "A_slo": (0.15, 0.70)}


def runs(table=None, a_slo_ctl=None, nb_early=(), n=20):
    """table: arm -> (penalty, loss). Every run of an arm has n requests at the
    arm's penalty over the Q median, so the pooled median is exact."""
    table = dict(BASE, **(table or {}))
    q_med = sorted(Q_RUN_MEDIANS)[2]
    out = []
    for rnd in range(1, 6):
        out.append({"arm": "Q", "round": rnd, "ttft": [Q_RUN_MEDIANS[rnd - 1]] * n, "failed": 0,
                    "nb_bogo_s": None, "nb_ended_before_window_end": False, "cpumax_ok": True})
        for arm, (pen, loss) in table.items():
            r = {"arm": arm, "round": rnd, "ttft": [q_med * (1 + pen)] * n, "failed": 0,
                 "nb_bogo_s": N0_BOGO * (1 - loss), "nb_ended_before_window_end": (arm, rnd) in nb_early,
                 "cpumax_ok": True}
            if arm == "A_slo":
                r["ctl"] = dict({"windows": 5, "stomps": 0, "enoent": 0, "write_errors": 0,
                                 "alive_at_window_end": True}, **(a_slo_ctl or {}))
            out.append(r)
    return out


class Verdict(unittest.TestCase):
    def v(self, **kw):
        return analyze.analyze(runs(**kw))

    def test_supported(self):
        r = self.v(table={"A_slo": (0.08, 0.40), "K25": (0.09, 0.74)})
        self.assertEqual(r["verdict"], "SUPPORTED", r["reasons"])
        self.assertAlmostEqual(r["arms"]["A_slo"]["penalty"], 0.08, places=6)
        self.assertAlmostEqual(r["arms"]["A_slo"]["loss"], 0.40, places=6)

    def test_refuted_penalty(self):
        r = self.v(table={"A_slo": (0.15, 0.40)})
        self.assertEqual(r["verdict"], "REFUTED")
        self.assertIn("a", r["failed_conditions"])

    def test_refuted_static_dominates(self):
        r = self.v(table={"A_slo": (0.08, 0.40), "K25": (0.09, 0.30)})
        self.assertEqual(r["verdict"], "REFUTED")
        self.assertEqual(r["failed_conditions"], ["c"])

    def test_refuted_loss(self):
        r = self.v(table={"A_slo": (0.08, 0.73), "K25": (0.09, 0.80)})
        self.assertEqual(r["verdict"], "REFUTED")
        self.assertEqual(r["failed_conditions"], ["b"])

    def test_inconclusive_n0_in_spread(self):
        r = self.v(table={"N0": (0.01, 0.0)})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("N0" in s for s in r["reasons"]))

    def test_inconclusive_windows(self):
        r = self.v(table={"A_slo": (0.08, 0.40)}, a_slo_ctl={"windows": 3})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_inconclusive_stomp(self):
        r = self.v(table={"A_slo": (0.08, 0.40)}, a_slo_ctl={"stomps": 1})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_inconclusive_nb_early(self):
        r = self.v(table={"A_slo": (0.08, 0.40)}, nb_early={("K50", 3)})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_inconclusive_ctl_dead_or_enoent(self):
        for bad in ({"alive_at_window_end": False}, {"enoent": 1}, {"write_errors": 1}):
            r = self.v(table={"A_slo": (0.08, 0.40)}, a_slo_ctl=bad)
            self.assertEqual(r["verdict"], "INCONCLUSIVE", bad)

    def test_inconclusive_missing_or_failed(self):
        rs = [r for r in runs() if not (r["arm"] == "K25" and r["round"] == 2)]
        self.assertEqual(analyze.analyze(rs)["verdict"], "INCONCLUSIVE")
        rs = runs()
        rs[3]["failed"] = 1
        self.assertEqual(analyze.analyze(rs)["verdict"], "INCONCLUSIVE")

    def test_inconclusive_static_cpumax(self):
        rs = runs(table={"A_slo": (0.08, 0.40)})
        next(r for r in rs if r["arm"] == "K50")["cpumax_ok"] = False
        self.assertEqual(analyze.analyze(rs)["verdict"], "INCONCLUSIVE")


class Reader(unittest.TestCase):
    """load_runs reads a results tree written in run.sh's layout."""

    def test_load_runs(self):
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "r1-p1-A_slo")
            os.makedirs(d)
            run = {"arm": "A_slo", "round": 1, "window_start": 100.0, "window_end": 1000.0,
                   "neighbour": {"bogo_ops_per_s_real": 9000.0}, "neighbour_running_at_window_end": True,
                   "cpumax_set": None, "cpumax_reads": ["375000 100000", "562500 100000"],
                   "ctl_alive_at_window_end": True}
            with open(os.path.join(d, "run.json"), "w") as f:
                json.dump(run, f)
            with open(os.path.join(d, "load.jsonl"), "w") as f:
                for i in range(41):
                    f.write(json.dumps({"ok": True, "ttft_s": 7.0 + i / 100, "t_start": 100 + i}) + "\n")
                f.write(json.dumps({"ok": False, "ttft_s": None, "t_start": 500}) + "\n")
                f.write(json.dumps({"ok": True, "ttft_s": 1.0, "t_start": 1001}) + "\n")  # after the window
            with open(os.path.join(d, "ctl.jsonl"), "w") as f:
                for t, ev, stomp, w in ((99, "no_neighbour", False, 0), (150, "tick", True, 1),
                                        (900, "tick", False, 2), (1100, "no_neighbour", False, 2)):
                    f.write(json.dumps({"t": t, "event": ev, "stomp": stomp, "windows": w}) + "\n")
            (r,) = analyze.load_runs(root)
        self.assertEqual(len(r["ttft"]), 41)
        self.assertEqual(r["failed"], 1)
        self.assertEqual(r["nb_bogo_s"], 9000.0)
        self.assertFalse(r["nb_ended_before_window_end"])
        self.assertTrue(r["cpumax_ok"])  # A_slo: ctl owns cpu.max, no static read-back check
        # ENOENT and stomps count only inside the window; windows is the last count.
        self.assertEqual(r["ctl"], {"windows": 2, "stomps": 1, "enoent": 0, "write_errors": 0,
                                    "alive_at_window_end": True})


if __name__ == "__main__":
    unittest.main()
