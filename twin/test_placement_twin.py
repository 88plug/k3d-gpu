"""Unit tests for the placement twin mechanism. Run: python3 -m unittest discover -s twin"""
import ast
import itertools
import os
import random
import unittest

import numpy as np

import placement_twin as pt

HERE = os.path.dirname(os.path.abspath(__file__))


class PerfParse(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "fixtures", "perf_stat_pcore_raw.txt")) as f:
            self.perf = pt.parse_perf_csv(f.read())

    def test_real_host_capture(self):
        # Captured on the i9-13900T: `perf stat -x, -e cpu_core/...,cpu_atom/... -- taskset -c 2 ...`
        self.assertEqual(self.perf["cpu_core"]["instructions"], (95414330.0, 97.0))
        self.assertEqual(self.perf["cpu_atom"]["l2_request.miss"], (63815.0, 2.0))

    def test_features(self):
        f = pt.features(self.perf, off_cpu=0.1)
        self.assertAlmostEqual(f["ret"], 90519758 / 253654269)
        self.assertAlmostEqual(f["ipc"], 95414330 / 42283369)
        self.assertAlmostEqual(f["l2_mpki"], 1000 * 346976 / 95414330)
        td = f["ret"] + f["bs"] + f["fe"] + f["be_core"] + f["be_mem"]
        self.assertAlmostEqual(td, 1.0, delta=0.02)  # topdown slots sum to ~slots on this capture

    def test_not_counted(self):
        p = pt.parse_perf_csv("<not counted>,,cpu_core/instructions/u,0,0.00,,\n")
        self.assertEqual(p["cpu_core"]["instructions"], (None, 0.0))

    def test_schedstat(self):
        self.assertAlmostEqual(pt.off_cpu_fraction("1000 0 0", "501000 0 0", 2_000_000), 0.75)


class PCCS(unittest.TestCase):
    def test_recovers_planted_three_region_within_2pct(self):
        rng = random.Random(7)
        for _ in range(5):
            t1, t2 = rng.uniform(6, 14), rng.uniform(22, 38)
            a, b, c = rng.uniform(.001, .005), rng.uniform(.01, .03), rng.uniform(.03, .08)
            true = pt.PCCSCurve(t1, t2, a, b, c)
            x = np.linspace(0, 50, 12)
            got = pt.fit_pccs(x, [true(v) for v in x])
            for g, w in zip(got.as_tuple(), true.as_tuple()):
                self.assertLess(abs(g - w) / w, 0.02, (got.as_tuple(), true.as_tuple()))

    def test_engine_interpolation(self):
        lo, hi = pt.PCCSCurve(10, 30, 0, 0.01, 0), pt.PCCSCurve(10, 30, 0, 0.03, 0)
        ec = pt.EngineCurves([5, 25], [lo, hi])
        self.assertAlmostEqual(ec(20, 15), 1 + 0.02 * 10)


class Models(unittest.TestCase):
    def test_ep_structured_recovers_planted(self):
        rng = random.Random(1)
        rows, ys = [], []
        for _ in range(40):
            r = {k: rng.uniform(0.05, 0.4) for k in pt.FEATURES}
            r["off_cpu"] = rng.uniform(0, 0.8)
            cpu = 1.2 + 1.5 * r["be_core"] + 0.8 * r["bs"]
            rows.append(r)
            ys.append(r["off_cpu"] + (1 - r["off_cpu"]) * cpu)
        m = pt.EPModel().fit(rows[:30], ys[:30], lam=1e-6)
        err = max(abs(m.predict(r) - y) for r, y in zip(rows[30:], ys[30:]))
        self.assertLess(err, 1e-3)

    def test_packing_recovers_planted(self):
        rng = random.Random(2)
        truth = pt.PackingModel()
        truth.b = np.array([0.4, 0.05])
        mixes = [[{"be_mem": rng.uniform(.05, .5), "l2_mpki": rng.uniform(0, 20)} for _ in range(4)]
                 for _ in range(30)]
        fit = pt.PackingModel().fit(mixes, [truth.predict(m) for m in mixes])
        np.testing.assert_allclose(fit.b, truth.b, rtol=1e-6)

    def test_clock_recovers_planted(self):
        w = np.array([1.0, -0.02, -0.01, -0.05])
        samples = []
        for nP, nE, gt in itertools.product(range(1, 9), (0, 4, 8, 16), (0, 1)):
            samples.append(("P", nP, nE, gt, pt.P_TURBO[nP] * (w @ [1, nP, nE, gt])))
            samples.append(("E", nP, max(1, nE), gt, pt.E_TURBO * (w @ [1, nP, max(1, nE), gt])))
        c = pt.ClockModel("linear").fit(samples)
        np.testing.assert_allclose(c.w["P"], w, atol=1e-9)
        self.assertEqual(pt.ClockModel("off").fit(samples).ghz("P", 8, 16, 1), 4.3)

    def test_clock_table_interpolates_without_form(self):
        def truth(nP, nE, gt):  # a knee no linear model in counts can follow
            return 1.0 / (1.0 + max(0.0, nP + 0.4 * nE + 2 * gt - 6) / 4)
        samples = [("P", nP, nE, gt, pt.P_TURBO[nP] * truth(nP, nE, gt))
                   for nP in (1, 2, 4, 6, 8) for nE in (0, 4, 8, 12, 16) for gt in (0, 1)]
        tab, lin = pt.ClockModel("table").fit(samples), pt.ClockModel("linear").fit(samples)
        for nP, nE, gt in ((1, 0, 0), (8, 16, 1), (4, 8, 0), (6, 12, 1)):  # grid nodes: exact
            self.assertAlmostEqual(tab.ghz("P", nP, nE, gt), pt.P_TURBO[nP] * truth(nP, nE, gt))
        err_t = max(abs(tab.ghz("P", p, e, g) / (pt.P_TURBO[p] * truth(p, e, g)) - 1)
                    for p in range(1, 9) for e in range(17) for g in (0, 1))
        err_l = max(abs(lin.ghz("P", p, e, g) / (pt.P_TURBO[p] * truth(p, e, g)) - 1)
                    for p in range(1, 9) for e in range(17) for g in (0, 1))
        # Measured: table max 8.7 % (between nodes, across the knee), linear max 32 %.
        self.assertLess(err_t, 0.10)
        self.assertGreater(err_l, 3 * err_t)


class Kappa(unittest.TestCase):
    def test_scaling_recovers_compute_share(self):
        for share in (0.0, 0.3, 0.8, 1.0):
            t = lambda f: share * 5.3 / f + (1 - share)   # 1 s at 5.3 GHz, share scales with clock
            self.assertAlmostEqual(pt.kappa_from_scaling(t(5.3), t(3.0), 5.3, 3.0), share, places=12)

    def test_kappa_on_e(self):
        self.assertAlmostEqual(pt.kappa_on_e(0.6, 2.0), 0.8)
        self.assertAlmostEqual(pt.kappa_on_e(0.6, 1.0), 0.6)


class FixedPoint(unittest.TestCase):
    def comp(self, eng, bw, kappa=0.5, n=1):
        return {"engine": eng, "n": n, "iso": 1.0, "bw": bw, "kappa": kappa, "iso_set": (n if eng == "P" else 0, n if eng == "E" else 0, 0)}

    def test_alone_is_iso(self):
        curves = {"P": pt.EngineCurves([0, 50], [pt.PCCSCurve(10, 30, .01, .02, .05)] * 2)}
        self.assertAlmostEqual(pt.fixed_point([self.comp("P", 20)], curves, pt.ClockModel("off"))[0], 1.0)

    def test_monotone_in_external_bandwidth(self):
        c = pt.EngineCurves([0, 50], [pt.PCCSCurve(10, 30, .01, .02, .05)] * 2)
        curves = {"P": c, "E": c}
        prev = 0
        for bw in (0, 10, 20, 40):
            t = pt.fixed_point([self.comp("P", 20), self.comp("E", bw)], curves, pt.ClockModel("off"))[0]
            self.assertGreaterEqual(t, prev)
            prev = t

    def test_clock_term_applies_kappa(self):
        clk = pt.ClockModel("linear")
        clk.w["P"] = np.array([1.0, 0, -0.02, 0])
        clk.w["E"] = np.array([1.0, 0, 0, 0])
        a = self.comp("P", 0, kappa=1.0)
        b = self.comp("E", 0, kappa=0.0, n=10)
        t = pt.fixed_point([a, b], None, clk)
        self.assertAlmostEqual(t[0], 1 / 0.8)   # 10 E-cores busy -> P derate 0.8
        self.assertAlmostEqual(t[1], 1.0)       # kappa 0: clock-insensitive


class Placement(unittest.TestCase):
    def test_enumeration_matches_bruteforce(self):
        for n in range(1, 7):
            got = set(pt.enumerate_placements(n))
            brute = set()
            for pl in itertools.product(["P", 0, 1, 2, 3], repeat=n):
                if sum(v == "P" for v in pl) > pt.P_CORES - 1:
                    continue
                if any(sum(v == m for v in pl) > 4 for m in range(4)):
                    continue
                remap, out = {}, []
                for v in pl:
                    if v != "P":
                        remap.setdefault(v, len(remap))
                        v = remap[v]
                    out.append(v)
                brute.add(tuple(out))
            self.assertEqual(got, brute, n)

    def test_choose_prefers_feasible(self):
        ev = {("P",): (10, 1), (0,): (5, 0), (1,): (6, 0)}
        self.assertEqual(pt.choose(ev, lambda pl: ev[pl]), (1,))
        ev = {("P",): (10, 2), (0,): (5, 1)}
        self.assertEqual(pt.choose(ev, lambda pl: ev[pl]), (0,))

    def test_emit(self):
        self.assertEqual(pt.emit(["a", "b", "c"], ("P", 0, 1)),
                         {"a": {"k3d-gpu.io/cores": "performance"},
                          "b": {"k3d-gpu.io/cores": "efficient", "k3d-gpu.io/e-group": "m0"},
                          "c": {"k3d-gpu.io/cores": "efficient", "k3d-gpu.io/e-group": "m1"}})

    def test_balloon_values_consume_emitted_labels(self):
        with open(os.path.join(HERE, "..", "share", "placement-twin", "balloons-values.yaml")) as f:
            text = f.read()
        self.assertIn("{key: pod/labels/k3d-gpu.io/e-group, operator: Exists}", text)
        self.assertIn('groupBy: "${pod/labels/k3d-gpu.io/e-group}"', text)
        self.assertIn("level: l2cache", text)
        # the module type must come before the plain efficient type (first match wins)
        self.assertLess(text.index("name: efficient-module"), text.index("name: efficient\n"))

    def test_manifests_parse_and_profiler_uses_twin_events(self):
        yaml = __import__("yaml")
        d = os.path.join(HERE, "..", "share", "placement-twin")
        with open(os.path.join(d, "balloons-values.yaml")) as f:
            names = [b["name"] for b in yaml.safe_load(f)["config"]["balloonTypes"]]
        self.assertEqual(names, ["performance", "efficient-module", "efficient", "shared"])
        with open(os.path.join(d, "profiler-daemonset.yaml")) as f:
            ds = yaml.safe_load(f)
        c = ds["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(c["securityContext"]["capabilities"]["add"], ["PERFMON"])
        self.assertIn(",".join(f"cpu_core/{e}/" for e in pt.CORE_EVENTS), c["args"][0])


class SimWorldPerturbation(unittest.TestCase):
    def test_hetero_power_breaks_spin_calibration(self):
        import sim_world as sw
        # 8 P-cores that mostly stall on DRAM/IO plus 16 busy E-cores, under the 35 W envelope.
        comps = [{"engine": "P", "n": 1, "C": 0.5, "B": 0.0, "bw1": 1.0, "io_s": 1.0}] * 8 + \
                [{"engine": "E", "n": 1, "C": 4.0, "B": 0.0, "bw1": 1.0}] * 16
        flat = sw.World(3, envelope="spec35").corun(comps)
        het = sw.World(3, envelope="spec35", hetero=True).corun(comps)
        self.assertLess(het[-1], flat[-1] * 0.97)   # E-cores clock higher when P-cores idle


class Isolation(unittest.TestCase):
    def test_twin_does_not_import_simulator(self):
        with open(os.path.join(HERE, "placement_twin.py")) as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module)
        self.assertNotIn("sim_world", names)
        self.assertTrue(names <= {"argparse", "itertools", "json", "sys", "numpy"}, names)


if __name__ == "__main__":
    unittest.main()
