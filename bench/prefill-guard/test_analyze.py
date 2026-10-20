#!/usr/bin/env python3
"""Tests for the H5 stage-1 scoring (analyze.py) and root helper (cpufreq.sh)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyze  # noqa: E402

ORDER = {1: ("Q", "N0", "C75", "C50", "CMIN"), 2: ("N0", "C75", "C50", "CMIN", "Q"),
         3: ("C75", "C50", "CMIN", "Q", "N0"), 4: ("C50", "CMIN", "Q", "N0", "C75"),
         5: ("CMIN", "Q", "N0", "C75", "C50")}
CAP = {"Q": None, "N0": None, "C75": 2925000, "C50": 1950000, "CMIN": 800000}


def llm(values, key="ttft_s"):
    return {"phases": [{"samples": [{"ok": True, key: v} for v in values]}]}


def write_tree(root, ttft, method="matrixprod", bogo=None, cap_read=None, skip=(), fail=()):
    """ttft: arm -> (round -> [3 ttft]) or arm -> [3 ttft] for every round."""
    for rnd, arms in ORDER.items():
        for pos, arm in enumerate(arms, 1):
            if (arm, rnd) in skip:
                continue
            d = os.path.join(root, f"{method}-r{rnd}-p{pos}-{arm}")
            os.makedirs(d)
            t = ttft[arm]
            t = t[rnd] if isinstance(t, dict) else t
            pre = llm(t)
            if (arm, rnd) in fail:
                pre["phases"][0]["samples"][0] = {"ok": False, "ttft_s": None, "error": "boom"}
            reads = [CAP[arm] or 3900000] * 16
            if cap_read and (arm, rnd) in cap_read:
                reads[3] = 3900000
            run = {"cap_khz": CAP[arm], "cap_read_khz": reads, "tool_proxy_s": 2.0,
                   "package_w": 100.0, "package_throttle_delta": 10, "pkg_temp_start_c": 80,
                   "neighbour": None if arm == "Q" else {"bogo_ops_per_s_real": (bogo or {}).get(arm, 1000.0)},
                   "exit_codes_prefill_decode_tool": [0, 0, 0], "cpufreq_restored_identical": True}
            for name, doc in (("prefill", pre), ("decode", llm([15.0], "decode_tokens_per_s")), ("run", run)):
                with open(os.path.join(d, name + ".json"), "w") as f:
                    json.dump(doc, f)


QUIET = [7.0, 7.05, 6.95]


def base(**over):
    t = {"Q": QUIET, "N0": [9.8, 10.0, 10.2], "C75": [9.0, 9.1, 9.2],
         "C50": [8.0, 8.1, 8.2], "CMIN": [7.5, 7.6, 7.7]}
    t.update(over)
    return t


class VerdictTest(unittest.TestCase):
    def score(self, ttft, **kw):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, ttft, **kw)
            return analyze.analyze(root)

    def test_supported_and_chosen_level_is_smallest_neighbour_loss(self):
        bogo = {"N0": 1000.0, "C75": 900.0, "C50": 600.0, "CMIN": 300.0}
        r = self.score(base(), bogo=bogo)
        m = r["methods"]["matrixprod"]
        self.assertEqual(m["verdict"], "SUPPORTED")
        self.assertEqual(r["h5_verdict"], "SUPPORTED")
        # N0 penalty 10/7-1 = 0.43; C50 8.1/7-1 = 0.157 <= 0.21; CMIN 0.086; C75 0.3 no
        self.assertEqual(m["passing_levels"], ["C50", "CMIN"])
        self.assertEqual(m["chosen_level"], "C50")
        self.assertAlmostEqual(m["arms"]["CMIN"]["neighbour_loss"], 0.7)

    def test_refuted_when_no_level_reaches_half(self):
        r = self.score(base(C75=[9.5, 9.6, 9.7], C50=[9.0, 9.1, 9.2], CMIN=[8.8, 8.9, 9.0]))
        self.assertEqual(r["methods"]["matrixprod"]["verdict"], "REFUTED")

    def test_half_without_lower_p90_is_inconclusive(self):
        # CMIN median at half, but two slow requests lift its p90 above N0's
        cmin = {1: [7.5, 7.6, 12.0], 2: [7.5, 7.6, 12.0], 3: [7.5, 7.6, 7.7], 4: [7.5, 7.6, 7.7], 5: [7.5, 7.6, 7.7]}
        r = self.score(base(C50=[9.0, 9.1, 9.2], C75=[9.5, 9.6, 9.7], CMIN=cmin))
        m = r["methods"]["matrixprod"]
        self.assertTrue(m["arms"]["CMIN"]["half"])
        self.assertFalse(m["arms"]["CMIN"]["p90_below_n0"])
        self.assertEqual(m["verdict"], "INCONCLUSIVE")

    def test_n0_inside_q_spread_is_inconclusive(self):
        q = {1: [6.0, 6.0, 6.0], 2: [7.0, 7.0, 7.0], 3: [8.0, 8.0, 8.0], 4: [6.5, 6.5, 6.5], 5: [7.5, 7.5, 7.5]}
        r = self.score(base(Q=q, N0=[7.5, 7.6, 7.7]))
        m = r["methods"]["matrixprod"]
        self.assertEqual(m["verdict"], "INCONCLUSIVE")
        self.assertIn("N0 penalty", m["reason"])

    def test_q_drift_is_computed_from_request_medians(self):
        # Rounds 1-2 vs 4-5: request medians 7.0 vs 6.0; Q run medians range 6.0-7.0.
        q = {1: [6.0, 7.0, 7.5], 2: [6.0, 7.0, 7.5], 3: [6.5] * 3, 4: [5.5, 6.0, 7.0], 5: [5.5, 6.0, 7.0]}
        m = self.score(base(Q=q))["methods"]["matrixprod"]
        self.assertAlmostEqual(m["q_spread_s"], 1.0)
        self.assertAlmostEqual(m["q_drift_s"], 1.0)
        self.assertNotIn("drift", m["reason"])

    def test_monotone_q_trend_within_the_run_range_is_not_drift(self):
        # The registered drift test compares against the range of the Q run medians,
        # which a steady trend also widens: 6.0 -> 7.0 reads as spread, not drift.
        q = {1: [6.0] * 3, 2: [6.1] * 3, 3: [6.5] * 3, 4: [6.9] * 3, 5: [7.0] * 3}
        m = self.score(base(Q=q))["methods"]["matrixprod"]
        self.assertAlmostEqual(m["q_drift_s"], 0.9)
        self.assertNotIn("drift", m["reason"])

    def test_cap_read_without_cap_is_inconclusive(self):
        r = self.score(base(), cap_read={("C50", 3)})
        m = r["methods"]["matrixprod"]
        self.assertEqual(m["verdict"], "INCONCLUSIVE")
        self.assertEqual(m["cap_read_failures"], ["matrixprod-r3-p2-C50"])

    def test_missing_run_and_failed_request_are_inconclusive(self):
        self.assertEqual(self.score(base(), skip={("N0", 2)})["methods"]["matrixprod"]["verdict"], "INCONCLUSIVE")
        self.assertEqual(self.score(base(), fail={("Q", 4)})["methods"]["matrixprod"]["verdict"], "INCONCLUSIVE")

    def test_h5_verdict_is_the_primary_method(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, base(), method="loop")
            r = analyze.analyze(root)
        self.assertEqual(r["methods"]["loop"]["verdict"], "SUPPORTED")
        self.assertIsNone(r["h5_verdict"])

    def test_p90_is_nearest_rank(self):
        self.assertEqual(analyze.p90(list(range(1, 16))), 14)


class HelperTest(unittest.TestCase):
    """cpufreq.sh against a fake sysfs tree: cap, then restore to the snapshot."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sys = os.path.join(self.tmp.name, "cpu")
        for c in range(4):
            d = os.path.join(self.sys, f"cpu{c}", "cpufreq")
            os.makedirs(d)
            vals = {"scaling_max_freq": "3900000", "scaling_min_freq": "800000",
                    "scaling_governor": "powersave", "energy_performance_preference": "performance"}
            if c == 3:
                del vals["energy_performance_preference"]
            for k, v in vals.items():
                with open(os.path.join(d, k), "w") as f:
                    f.write(v + "\n")
        self.env = dict(os.environ, PG_SYSFS=self.sys)

    def tearDown(self):
        self.tmp.cleanup()

    def sh(self, *args):
        return subprocess.run(["bash", os.path.join(HERE, "cpufreq.sh"), *args], env=self.env,
                              capture_output=True, text=True)

    def test_cap_then_restore_reads_back_identical(self):
        snap = self.sh("snapshot").stdout
        self.assertEqual(len(snap.splitlines()), 15)
        path = os.path.join(self.tmp.name, "snap")
        with open(path, "w") as f:
            f.write(snap)
        self.assertEqual(self.sh("cap", "800000", "1", "2").returncode, 0)
        now = self.sh("snapshot").stdout
        self.assertIn("cpu1 scaling_max_freq 800000", now)
        self.assertIn("cpu0 scaling_max_freq 3900000", now)
        self.assertEqual(self.sh("restore", path).returncode, 0)
        self.assertEqual(self.sh("snapshot").stdout, snap)

    def test_cap_rejects_non_integers_and_restore_rejects_foreign_attrs(self):
        self.assertNotEqual(self.sh("cap", "50%", "1", "2").returncode, 0)
        bad = os.path.join(self.tmp.name, "bad")
        with open(bad, "w") as f:
            f.write("cpu0 cpuinfo_max_freq 1\n")
        self.assertNotEqual(self.sh("restore", bad).returncode, 0)


if __name__ == "__main__":
    unittest.main()
