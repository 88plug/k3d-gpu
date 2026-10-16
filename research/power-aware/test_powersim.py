#!/usr/bin/env python3
"""Unit tests for powersim.py: plant physics, estimator, baselines. Run: python3 -m unittest"""
import random
import unittest

import powersim as ps


class Plant(unittest.TestCase):
    def setUp(self):
        self.p = ps.prior_midpoint()
        self.p["r_th"] = 1.0

    def test_grid_size_and_bounds(self):
        self.assertEqual(len(ps.CONFIGS), 45 * 15 * 2)
        for pL, pT, eL, eT, g in ps.CONFIGS:
            self.assertLessEqual(pL + pT, ps.N_P)
            self.assertLessEqual(eL + eT, ps.N_E)
            self.assertEqual(eL % 4 + eT % 4, 0)

    def test_package_power_never_exceeds_cap_above_floor(self):
        for cap in (35.0, 60.0, 117.0):
            for c in ps.CONFIGS[::7]:
                st = ps.steady(self.p, c, 0.5, cap)
                floor = self.p["p_static"] + ps.steady(self.p, c, 0.5, 0.0)["core"]
                self.assertLessEqual(st["pkg"], max(cap, floor) + 1e-6, (cap, c))

    def test_unconstrained_runs_at_max(self):
        st = ps.steady(self.p, (1, 0, 0, 0, 0), 0.5, 117.0)
        self.assertAlmostEqual(st["fP"], ps.FMAX["P"])
        self.assertFalse(st["throttled"])

    def test_floor_is_base_frequency(self):
        st = ps.steady(self.p, (8, 0, 8, 8, 0), 0.5, 1.0)
        self.assertEqual((st["fP"], st["fE"]), (ps.FMIN["P"], ps.FMIN["E"]))

    def test_igpu_on_lowers_core_clock_at_cap(self):
        off = ps.steady(self.p, (4, 4, 8, 8, 0), 0.5, 60.0)
        on = ps.steady(self.p, (4, 4, 8, 8, 1), 0.5, 60.0)
        self.assertLess(on["fP"], off["fP"])
        self.assertAlmostEqual(on["pkg"], off["pkg"], delta=1e-6)  # same cap, shifted budget

    def test_igpu_on_free_when_cap_slack(self):
        off = ps.steady(self.p, (1, 1, 0, 0, 0), 0.5, 117.0)
        on = ps.steady(self.p, (1, 1, 0, 0, 1), 0.5, 117.0)
        self.assertEqual(on["fP"], off["fP"])

    def test_ablated_model_ignores_coupling(self):
        on = ps.steady(self.p, (4, 4, 8, 8, 1), 0.5, 60.0, coupled=False)
        off = ps.steady(self.p, (4, 4, 8, 8, 0), 0.5, 60.0)
        self.assertAlmostEqual(on["fP"], off["fP"])

    def test_leontief(self):
        st = ps.steady(self.p, (0, 4, 0, 0, 0), 0.5, 117.0)  # no inference capacity
        self.assertEqual(st["X"], 0.0)
        st = ps.steady(self.p, (0, 4, 0, 0, 0), 0.0, 117.0)  # all tool work
        self.assertGreater(st["X"], 0.0)

    def test_bandwidth_clamp(self):
        p = dict(self.p, bw=1.0)
        st = ps.steady(p, (8, 0, 16, 0, 1), 1.0, 117.0)
        self.assertAlmostEqual(st["X"], 1.0)
        self.assertAlmostEqual(sum(st["pools"][k] for k in ("LP", "LE", "LG")), 1.0)

    def test_thermal_cap(self):
        self.assertAlmostEqual(ps.thermal_cap(self.p, 25.0), 75.0)
        self.assertEqual(ps.thermal_cap(dict(self.p, r_th=0.1), 25.0), ps.PL1)


class Estimation(unittest.TestCase):
    def test_estimator_recovers_kG_and_static_noise_free(self):
        plant = ps.draw_plant(random.Random(1))
        est = ps.Estimator(ps.prior_midpoint())
        cfg = (4, 4, 8, 8, 1)
        st = ps.steady(plant, cfg, 0.5, 60.0)
        o = {**st, "m": 0.5}
        for _ in range(100):
            est.update(o, cfg)
        self.assertAlmostEqual(est.p["kG"], plant["kG"], places=4)
        self.assertAlmostEqual(est.p["p_static"], plant["p_static"], places=4)
        self.assertAlmostEqual(est.cap, st["pkg"], places=3)

    def test_cap_estimate_rises_when_unthrottled(self):
        est = ps.Estimator(ps.prior_midpoint())
        est.cap = 40.0
        o = {"pkg": 30.0, "core": 15.0, "gfx": 0.0, "fP": 5.1, "fE": 3.9, "fG": 0.0,
             "pools": dict.fromkeys(("LP", "LE", "LG", "TP", "TE"), 0.0), "throttled": False, "m": 0.5}
        est.update(o, (0, 0, 0, 0, 0))
        self.assertAlmostEqual(est.cap, 40.5)

    def test_transitional_dead_time(self):
        old, new = (4, 4, 0, 0, 0), (2, 6, 0, 0, 1)
        self.assertEqual(ps.transitional(old, new, 3, 0, 5, 30), (2, 4, 0, 0, 0))
        self.assertEqual(ps.transitional(old, new, 10, 0, 5, 30), (2, 6, 0, 0, 0))
        self.assertEqual(ps.transitional(old, new, 30, 0, 5, 30), new)


class Baselines(unittest.TestCase):
    def test_oracle_bounds_tuned_bounds_naive(self):
        plant = ps.draw_plant(random.Random(3))
        sc = ps.SCENARIOS["S2_mix_shift"]
        scores, oracle = ps.static_scores(plant, sc)
        tuned = max(scores.values())
        self.assertGreaterEqual(oracle + 1e-9, tuned)
        self.assertGreaterEqual(tuned, scores[ps.NAIVE])

    def test_controller_deterministic_given_seed(self):
        plant = ps.draw_plant(random.Random(4))
        sc = [(200, 0.5, 25.0)]
        a = ps.run_controller(plant, sc, random.Random(9))
        b = ps.run_controller(plant, sc, random.Random(9))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
