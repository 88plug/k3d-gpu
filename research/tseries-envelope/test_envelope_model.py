#!/usr/bin/env python3
"""Tests for research/tseries-envelope/envelope_model.py. Run: python3 -m unittest research/tseries-envelope/test_envelope_model.py"""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import envelope_model as em  # noqa: E402


def fake_zone(root, pl1_uw, pl2_uw, tau_us, tdp_uw, enabled=1):
    z = os.path.join(root, "intel-rapl:0")
    os.makedirs(z)
    files = {
        "name": "package-0",
        "enabled": str(enabled),
        "constraint_0_name": "long_term",
        "constraint_0_power_limit_uw": str(pl1_uw),
        "constraint_0_time_window_us": str(tau_us),
        "constraint_0_max_power_uw": str(tdp_uw),
        "constraint_1_name": "short_term",
        "constraint_1_power_limit_uw": str(pl2_uw),
        "constraint_1_time_window_us": "2440",
    }
    for k, v in files.items():
        with open(os.path.join(z, k), "w") as f:
            f.write(v + "\n")


class TimeToPl1(unittest.TestCase):
    def test_closed_form(self):
        # A(t) = PL2 + (a0 - PL2) e^{-t/tau}; reaches PL1 at tau ln((PL2-a0)/(PL2-PL1))
        t = em.time_to_pl1(pl1=35, pl2=106, tau=56, a0=0)
        self.assertAlmostEqual(t, 56 * math.log(106 / 71), places=6)

    def test_equal_limits_never_throttle(self):
        self.assertEqual(em.time_to_pl1(pl1=117, pl2=117, tau=56, a0=0), math.inf)

    def test_demand_below_pl1_never_throttles(self):
        self.assertEqual(em.time_to_pl1(pl1=35, pl2=106, tau=56, a0=0, demand=30), math.inf)

    def test_demand_between_limits_uses_demand(self):
        t = em.time_to_pl1(pl1=35, pl2=106, tau=56, a0=10, demand=60)
        self.assertAlmostEqual(t, 56 * math.log(50 / 25), places=6)

    def test_already_over_budget(self):
        self.assertEqual(em.time_to_pl1(pl1=35, pl2=106, tau=56, a0=40), 0.0)


class Simulate(unittest.TestCase):
    def test_matches_closed_form(self):
        tr = em.simulate(demand_w=106, pl1=35, pl2=106, tau=56, a0=0, seconds=60, dt=0.01)
        first_capped = next(t for t, p, _ in tr if p < 106)
        self.assertAlmostEqual(first_capped, em.time_to_pl1(35, 106, 56, 0), delta=0.05)

    def test_settles_at_pl1(self):
        tr = em.simulate(demand_w=106, pl1=35, pl2=106, tau=56, a0=0, seconds=600, dt=0.05)
        last = [p for t, p, _ in tr if t > 500]
        self.assertAlmostEqual(sum(last) / len(last), 35, delta=0.5)
        self.assertLessEqual(max(a for _, _, a in tr), 35 + 0.5)

    def test_equal_limits_flat(self):
        tr = em.simulate(demand_w=150, pl1=117, pl2=117, tau=56, a0=0, seconds=300, dt=0.1)
        self.assertTrue(all(abs(p - 117) < 1e-9 for _, p, _ in tr))

    def test_demand_below_limits_passes_through(self):
        tr = em.simulate(demand_w=20, pl1=35, pl2=106, tau=56, a0=0, seconds=200, dt=0.1)
        self.assertTrue(all(abs(p - 20) < 1e-9 for _, p, _ in tr))


class Steady(unittest.TestCase):
    def test_flat_is_steady(self):
        ok, drift = em.steady([10.0, 10.1, 9.9, 10.0] * 10, window=20, rel_tol=0.03)
        self.assertTrue(ok)
        self.assertLess(abs(drift), 0.03)

    def test_sag_is_not_steady(self):
        series = [20.0] * 20 + [14.0] * 20  # burst then sustained: 30 % sag
        ok, drift = em.steady(series, window=20, rel_tol=0.03)
        self.assertFalse(ok)
        self.assertAlmostEqual(drift, -0.30, places=6)

    def test_too_short_is_not_steady(self):
        ok, _ = em.steady([1.0] * 10, window=20, rel_tol=0.03)
        self.assertFalse(ok)


class Plan(unittest.TestCase):
    def test_stock_tseries(self):
        p = em.plan(pl1=35, pl2=106, tau=56)
        self.assertTrue(p["rapl_binding"])
        self.assertAlmostEqual(p["soak_s"], 3 * 56)
        self.assertAlmostEqual(p["measure_s"], 2 * 56)

    def test_equal_limits_soak_is_thermal_floor(self):
        p = em.plan(pl1=117, pl2=117, tau=56)
        self.assertFalse(p["rapl_binding"])
        self.assertEqual(p["soak_s"], em.THERMAL_SOAK_FLOOR_S)
        self.assertEqual(p["measure_s"], max(2 * 56, em.MIN_MEASURE_S))

    def test_thermal_tc_dominates(self):
        p = em.plan(pl1=35, pl2=106, tau=56, thermal_tc_s=120)
        self.assertAlmostEqual(p["soak_s"], 360)

    def test_short_tau_floor(self):
        p = em.plan(pl1=15, pl2=25, tau=8)
        self.assertEqual(p["measure_s"], em.MIN_MEASURE_S)


class ReadLimits(unittest.TestCase):
    def test_reads_fake_tree(self):
        with tempfile.TemporaryDirectory() as d:
            fake_zone(d, 117000000, 117000000, 55967744, 35000000)
            lim = em.read_limits(powercap=d)
        self.assertEqual(lim, {"zone": "intel-rapl:0", "name": "package-0", "enabled": True,
                               "pl1_w": 117.0, "pl2_w": 117.0, "tau_s": 55.967744, "tdp_w": 35.0})

    def test_missing_zone(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                em.read_limits(powercap=d)


class FreqRatio(unittest.TestCase):
    def test_cube_law(self):
        self.assertAlmostEqual(em.freq_ratio(p_avail=8, p_burst=64), 0.5)

    def test_more_power_than_needed_is_one(self):
        self.assertEqual(em.freq_ratio(p_avail=100, p_burst=50), 1.0)


if __name__ == "__main__":
    unittest.main()
