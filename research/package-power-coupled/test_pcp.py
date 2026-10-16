import copy
import os
import random
import unittest

import yaml

import pcp
import ppcsim as S
from pcp import Demoter, PowerModel, Role, Sample, admit, effective_cap, render_policy

HERE = os.path.dirname(os.path.abspath(__file__))
CHART = os.path.join(HERE, "..", "..", "share", "nri-balloons.yml")
HOST_IDLE = ["POLL", "C1_ACPI", "C2_ACPI", "C3_ACPI"]  # i9-13900T, read 2026-10-16


def chart_docs():
    with open(CHART) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def shipped_spec(docs):
    return next(d for d in docs if d.get("kind") == "BalloonsPolicy")["spec"]


def roles(dgpu_embed=True):
    return [Role("planner", 99, ["pcore"]),
            Role("drafter", 3, ["igpu", "dgpu", "pcore"]),
            Role("tools", 2, ["ecore"]),
            Role("embed", 1, ["dgpu", "ecore"] if dgpu_embed else ["ecore"], sustained=True)]


class PowerModelTest(unittest.TestCase):
    def traces(self, noise=0.0, g=True):
        rng = random.Random(1)
        true = dict(c_p=9.0, c_e=2.5, c_g=12.0, p_static=7.0)
        out = []
        for _ in range(40):
            n_p, n_e = rng.choice([0, 2, 4, 8]), rng.choice([0, 4, 8, 12])
            f_p, f_e, f_g = rng.uniform(1.5, 5.1), rng.uniform(1.2, 3.9), rng.uniform(0.6, 1.65)
            gb = rng.choice([0.0, 1.0]) if g else 0.0
            cw = true["c_p"] * n_p * (f_p / 5.1) ** 2.6 + true["c_e"] * n_e * (f_e / 3.9) ** 2.6
            gw = true["c_g"] * gb * (f_g / 1.65) ** 2.6
            cw *= 1 + rng.gauss(0, noise)
            gw *= 1 + rng.gauss(0, noise)
            out.append(Sample(n_p, f_p, n_e, f_e, gb, f_g, cw + gw + true["p_static"], cw, gw))
        return true, out

    def test_fit_recovers_coefficients(self):
        true, tr = self.traces(noise=0.03)
        m = PowerModel(5.1, 3.9, 1.65).fit(tr)
        self.assertAlmostEqual(m.c_p, true["c_p"], delta=0.1 * true["c_p"])
        self.assertAlmostEqual(m.c_e, true["c_e"], delta=0.15 * true["c_e"])
        self.assertAlmostEqual(m.c_g, true["c_g"], delta=0.1 * true["c_g"])
        self.assertAlmostEqual(m.p_static, true["p_static"], delta=0.5)

    def test_fit_refuses_when_igpu_never_busy(self):
        _, tr = self.traces(g=False)
        with self.assertRaises(ValueError):
            PowerModel(5.1, 3.9, 1.65).fit(tr)


class AdmissionTest(unittest.TestCase):
    def test_cap_is_min_of_pl1_and_thermal_not_tdp(self):
        # i9-13900T: TDP field 35 W, PL1 programmed 117 W, binding limit thermal
        self.assertEqual(effective_cap(117.0, 48.0), 48.0)
        self.assertEqual(effective_cap(30.0, 48.0), 30.0)

    def test_admission_margin(self):
        ok, _ = admit(roles(), lambda rs: 43.0, effective_cap(117, 48), True)
        self.assertTrue(ok)  # 43 <= 0.9 * 48
        ok, _ = admit(roles(), lambda rs: 44.0, effective_cap(117, 48), True)
        self.assertFalse(ok)

    def test_sustained_role_goes_off_package_only_with_dgpu(self):
        _, rs = admit(roles(), lambda rs: 0, 50, True)
        self.assertEqual({r.name: r.placed for r in rs}["embed"], "dgpu")
        _, rs = admit(roles(), lambda rs: 0, 50, False)
        placed = {r.name: r.placed for r in rs}
        self.assertEqual(placed["embed"], "ecore")
        self.assertNotIn("dgpu", placed.values())

    def test_bursty_roles_stay_on_package(self):
        _, rs = admit(roles(), lambda rs: 0, 50, True)
        placed = {r.name: r.placed for r in rs}
        self.assertEqual(placed["planner"], "pcore")
        self.assertEqual(placed["drafter"], "igpu")


class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = chart_docs()
        cls.schema = pcp.crd_spec_schema(cls.docs)
        cls.base = shipped_spec(cls.docs)
        _, cls.rs = admit(roles(), lambda rs: 0, 50, True)

    def render(self, cap=2000):
        return render_policy(copy.deepcopy(self.base), self.rs, {"idle_states": HOST_IDLE}, cap)

    def test_shipped_spec_validates(self):
        self.assertEqual(pcp.validate(self.base, self.schema), [])

    def test_rendered_spec_validates_against_pinned_crd(self):
        self.assertEqual(pcp.validate(self.render(), self.schema), [])

    def test_validator_is_not_vacuous(self):
        bad = self.render()
        bad["cpuClasses"][0]["turbo_priority"] = 10
        bad["cpuClasses"][1]["energyPerformancePreference"] = "power"
        errs = pcp.validate(bad, self.schema)
        self.assertTrue(any("turbo_priority: unknown field" in e for e in errs), errs)
        self.assertTrue(any("energyPerformancePreference: want integer" in e for e in errs), errs)

    def test_only_planner_has_turbo_priority_and_symbolic_turbo(self):
        spec = self.render()
        for c in spec["cpuClasses"]:
            if c["name"] == "planner":
                self.assertEqual(c["turboPriority"], 10)
                self.assertEqual(c["maxFreq"], "turbo")
            else:
                self.assertNotIn("turboPriority", c)
                self.assertNotIn("turbo", (c.get("minFreq"), c.get("maxFreq")))
        capped = next(c for c in spec["cpuClasses"] if c["name"] == "capped")
        self.assertEqual(capped["maxFreq"], "2000MHz")

    def test_symbolic_turbo_on_lower_class_is_rejected(self):
        spec = self.render()
        spec["cpuClasses"][1]["maxFreq"] = "turbo"
        with self.assertRaises(ValueError):
            pcp._check_turbo(spec)

    def test_disabled_cstates_come_from_host_names(self):
        planner = next(c for c in self.render()["cpuClasses"] if c["name"] == "planner")
        self.assertEqual(planner["disabledCstates"], ["C2_ACPI", "C3_ACPI"])

    def test_role_balloons_precede_shipped_catch_alls(self):
        spec = self.render()
        names = [t["name"] for t in spec["balloonTypes"]]
        self.assertEqual(names[:2], ["planner", "tools"])  # embed went to dgpu
        self.assertEqual(names[-1], "shared")
        self.assertEqual(spec["idleCPUClass"], "idle")

    def test_rejects_non_integer_cap(self):
        with self.assertRaises(ValueError):
            render_policy(copy.deepcopy(self.base), self.rs, {"idle_states": HOST_IDLE}, 2.0)


class DemoterTest(unittest.TestCase):
    def placed(self, have_dgpu):
        _, rs = admit(roles(), lambda rs: 0, 50, have_dgpu)
        return rs

    def test_waits_hold_seconds(self):
        rs, d = self.placed(True), Demoter()
        for _ in range(5):
            self.assertIsNone(d.tick(rs, 50, 50, True, True))
        self.assertEqual(d.tick(rs, 50, 50, True, True), "drafter")  # embed already off package

    def test_cool_or_unthrottled_resets(self):
        rs, d = self.placed(True), Demoter()
        for i in range(20):
            hot = i % 5 != 4
            self.assertIsNone(d.tick(rs, 50 if hot else 30, 50, True, True))
        for _ in range(20):
            self.assertIsNone(d.tick(rs, 50, 50, False, True))  # at cap, no throttle reason

    def test_never_demotes_planner(self):
        rs = [Role("planner", 0, ["pcore", "ecore"], placed="pcore"),
              Role("tools", 2, ["ecore"], placed="ecore")]
        d = Demoter()
        for _ in range(60):
            d.tick(rs, 50, 50, True, False)
        self.assertEqual(rs[0].placed, "pcore")
        self.assertEqual(d.log, [])

    def test_lowest_weight_with_fallback_first_and_no_dgpu_when_absent(self):
        rs, d = self.placed(False), Demoter()
        for _ in range(6):
            name = d.tick(rs, 50, 50, True, False)
        self.assertEqual(name, "drafter")  # embed/tools have no fallback without a dGPU
        self.assertEqual({r.name: r.placed for r in rs}["drafter"], "pcore")


class SimPlantTest(unittest.TestCase):
    def plant(self, **kw):
        p = {k: S.mid(k) for k in S.PRIORS}
        p.update(kw)
        return p

    def test_package_held_at_cap_when_throttled(self):
        p = self.plant(cap=40.0)
        s = S.resolve(p, 4, S.FMAX_P, 12, S.FMAX_E, 1.0, S.FMAX_G)
        self.assertTrue(s["throttled"])
        self.assertAlmostEqual(s["pkg"], 40.0, delta=0.05)

    def test_lower_e_cap_never_lowers_igpu_clock(self):
        p = self.plant(cap=40.0, pi=0.2)
        fg = [S.resolve(p, 4, S.FMAX_P, 12, c, 1.0, S.FMAX_G)["f_g"] for c in S.CAP_GRID]
        self.assertEqual(fg, sorted(fg))

    def test_full_pp1_priority_shields_igpu(self):
        p = self.plant(cap=40.0, pi=1.0)
        a = S.resolve(p, 4, S.FMAX_P, 12, S.FMAX_E, 1.0, S.FMAX_G)["f_g"]
        b = S.resolve(p, 4, S.FMAX_P, 12, 0.8, 1.0, S.FMAX_G)["f_g"]
        self.assertAlmostEqual(a, S.FMAX_G)
        self.assertAlmostEqual(b, S.FMAX_G)

    def test_dram_channel_vanishes_without_e_core_traffic(self):
        p = self.plant(cap=40.0, t_tool=0.0, t_emb=0.0)
        ig = {"drafter": "igpu", "embed": "ecore"}
        jit = [(i / 100, 1.0) for i in range(100)]
        base = S.evaluate(p, ig, S.FMAX_E, jit)["rate"]
        dram = S.evaluate(p, ig, S.FMAX_E, jit, traffic_e=1.2)["rate"]
        self.assertAlmostEqual(base, dram)

    def test_bandwidth_bound_drafter_ignores_clock(self):
        p = self.plant(rho=50.0, soft=50.0)
        r1 = S.drafter_rate(p, "igpu", 1.65, 10.0)
        r2 = S.drafter_rate(p, "igpu", 0.8, 10.0)
        self.assertAlmostEqual(r1, r2, delta=0.01 * r1)

    def test_compute_bound_drafter_tracks_clock(self):
        p = self.plant(rho=0.05)
        r1 = S.drafter_rate(p, "igpu", 1.65, 10.0)
        r2 = S.drafter_rate(p, "igpu", 0.825, 10.0)
        self.assertAlmostEqual(r1 / r2, 2.0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
