#!/usr/bin/env python3
"""Unit tests for headroom_model.py (no hardware; sim fixtures and temp trees)."""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import headroom_model as hm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
RPL = os.path.join(ROOT, "tests/fixtures/raptorlake-nvidia/sysroot")


def run(*args):
    return subprocess.run([sys.executable, os.path.join(HERE, "headroom_model.py")] + list(args),
                          capture_output=True, text=True)


class Fit(unittest.TestCase):
    def test_exact_line(self):
        f = hm.fit([(20, 45), (60, 65), (100, 85)])
        self.assertAlmostEqual(f["r_th_c_per_w"], 0.5)
        self.assertAlmostEqual(f["t_idle_c"], 45)
        self.assertEqual(f["p_idle_w"], 20)
        self.assertAlmostEqual(f["residual_max_c"], 0)

    def test_noise_residual(self):
        f = hm.fit([(20, 45), (20, 47), (100, 85), (100, 87)])
        self.assertAlmostEqual(f["r_th_c_per_w"], 0.5)
        self.assertAlmostEqual(f["residual_max_c"], 1)

    def test_rejects_one_sample(self):
        with self.assertRaises(ValueError):
            hm.fit([(20, 45)])

    def test_rejects_one_power(self):
        with self.assertRaises(ValueError):
            hm.fit([(20, 45), (20, 50)])

    def test_rejects_flat_or_negative_slope(self):
        with self.assertRaises(ValueError):
            hm.fit([(20, 50), (80, 50)])
        with self.assertRaises(ValueError):
            hm.fit([(20, 60), (80, 50)])


class Limits(unittest.TestCase):
    def test_p_thermal(self):
        # T_tcc 95, margin 10, idle 45 C at 20 W, 0.5 C/W -> 20 + 40/0.5 = 100 W
        self.assertAlmostEqual(hm.p_thermal(20, 45, 0.5, 95), 100)

    def test_p_thermal_hot_start_below_idle_power(self):
        self.assertLess(hm.p_thermal(20, 90, 0.5, 95), 20)

    def test_rapl_limit(self):
        self.assertEqual(hm.rapl_limit(65, 120, 10), 120)
        self.assertEqual(hm.rapl_limit(65, 120, 65), 65)
        self.assertEqual(hm.rapl_limit(65, None, 10), 65)
        self.assertEqual(hm.rapl_limit(None, 120, None), 120)
        self.assertIsNone(hm.rapl_limit(None, None, None))

    def test_headroom_thermal_binds_when_pl_high(self):
        # i9-13900T shape: PL1 = PL2 = 117 W above the thermal limit
        h = hm.headroom(30, 100, 117, 117, 0)
        self.assertEqual(h, {"limit_w": 100, "binding": "thermal", "watts": 70})

    def test_headroom_rapl_binds_after_turbo(self):
        self.assertEqual(hm.headroom(30, 200, 65, 120, 70)["binding"], "rapl")
        self.assertEqual(hm.headroom(30, 200, 65, 120, 70)["watts"], 35)
        self.assertEqual(hm.headroom(30, 200, 65, 120, 10)["watts"], 90)

    def test_headroom_never_negative_and_integer(self):
        self.assertEqual(hm.headroom(150, 100)["watts"], 0)
        self.assertIsInstance(hm.headroom(30.4, 100.9)["watts"], int)
        self.assertEqual(hm.headroom(30.4, 100.9)["watts"], 70)

    def test_ewma_converges(self):
        a = 0.0
        for _ in range(1000):
            a = hm.ewma_step(a, 100, 1, 28)
        self.assertAlmostEqual(a, 100, places=6)
        self.assertAlmostEqual(hm.ewma_step(0, 100, 28, 28), 100 * (1 - math.exp(-1)))

    def test_turbo_seconds_matches_ewma(self):
        # simulate a load at PL2 from p0 until the average reaches PL1
        pl1, pl2, tau, p0 = 65.0, 120.0, 28.0, 10.0
        a, t, dt = p0, 0.0, 0.001
        while a < pl1:
            a = hm.ewma_step(a, pl2, dt, tau)
            t += dt
        self.assertAlmostEqual(t, hm.turbo_seconds(pl1, pl2, p0, tau), delta=0.01)

    def test_turbo_seconds_edges(self):
        self.assertEqual(hm.turbo_seconds(65, 120, 65, 28), 0)
        self.assertEqual(hm.turbo_seconds(117, 117, 0, 28), math.inf)

    def test_slots(self):
        self.assertEqual(hm.slots(70, 6, 3), 15)
        self.assertEqual(hm.slots(0, 6, 3), 0)
        with self.assertRaises(ValueError):
            hm.slots(70, 0, 0)

    def test_watts_equals_slots_for_identical_pods(self):
        # with one pod type the watts budget and the count cap admit the same number
        for budget in range(0, 120, 7):
            self.assertEqual(budget // 5, hm.slots(budget, 5, 5))


class Host(unittest.TestCase):
    def test_raptorlake_fixture(self):
        h = hm.host(RPL)
        self.assertEqual(h["tjmax_c"], 100)
        self.assertEqual(h["t_pkg_c"], 95)
        self.assertEqual(h["tcc_offset_c"], 5)
        self.assertEqual(h["t_tcc_c"], 95)
        self.assertEqual(h["pl1_w"], 117)
        self.assertEqual(h["pl2_w"], 117)
        self.assertEqual(h["tdp_w"], 35)

    def test_empty_sysroot(self):
        with tempfile.TemporaryDirectory() as d:
            h = hm.host(d)
        self.assertIsNone(h["t_tcc_c"])
        self.assertIsNone(h["pl1_w"])
        self.assertEqual(h["tcc_offset_c"], 0)

    def test_every_fixture_reads(self):
        fx = os.path.join(ROOT, "tests/fixtures")
        for name in sorted(os.listdir(fx)):
            sr = os.path.join(fx, name, "sysroot")
            if os.path.isdir(sr):
                h = hm.host(sr)
                if h["tjmax_c"] is not None:
                    self.assertGreater(h["tjmax_c"], 50, name)
                if h["pl1_w"] is not None:
                    self.assertGreater(h["pl1_w"], 0, name)


class Cli(unittest.TestCase):
    def test_selftest(self):
        r = run("selftest")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("selftest ok", r.stdout)

    def test_host_json(self):
        r = run("host", "--sysroot", RPL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["t_tcc_c"], 95)

    def test_fit_csv(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("# watts,celsius\n20,45\n60,65\n\n100,85\n")
        try:
            r = run("fit", f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertAlmostEqual(json.loads(r.stdout)["r_th_c_per_w"], 0.5)

    def test_fit_bad_csv_exit_2(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("20,45\n20,46\n")
        try:
            r = run("fit", f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(r.returncode, 2)
        self.assertIn("distinct", r.stderr)

    def test_budget(self):
        r = run("budget", "--p-idle", "20", "--t-idle", "45", "--r-th", "0.5", "--t-tcc", "95",
                "--p-now", "25", "--pl1", "117", "--pl2", "117", "--w-hot", "6", "--w-cool", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        j = json.loads(r.stdout)
        self.assertEqual(j["watts"], 75)
        self.assertEqual(j["binding"], "thermal")
        self.assertEqual(j["slots"], 16)


if __name__ == "__main__":
    unittest.main(verbosity=1)
