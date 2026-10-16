#!/usr/bin/env python3
"""Unit tests for share/joulemeter: fit recovery (C1), conservation (C2),
apportioning edge cases, sysfs/procfs readers on fixture trees, the hybrid
perf event table, and Shapley efficiency of the simulator's truth.
No hardware, no cluster. Usage: tests/joulemeter_test.py"""

import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "share", "joulemeter"))
sys.path.insert(0, HERE)
import joulemeter as jm  # noqa: E402
import joulemeter_prereg as prereg  # noqa: E402
import joulemeter_sim as sim  # noqa: E402

C = prereg.CRITERIA


def write(root, path, text):
    full = os.path.join(root, path.lstrip("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(text)
    return full


class LinearWorld(unittest.TestCase):
    """C1/C2: planted coefficients, exactly linear energy, 1% RAPL noise."""

    BETA = np.array([2.0, 0.5, 0.6, 0.15])   # J per G: P-cyc, P-ins, E-cyc, E-ins
    IDLE_W = 3.0

    def trace(self, seed=0, T=900, dt=0.2):
        rng = np.random.default_rng(seed)
        rows = []
        for _ in range(T):
            pods, truth = {}, {}
            for n in ("A", "B", "C"):
                on = rng.random() > 0.2
                pc = rng.uniform(0, 4e9) * dt * on if n != "B" else 0.0
                ec = rng.uniform(0, 6e9) * dt * on if n != "A" else 0.0
                pi, ei = pc * rng.uniform(0.5, 3.0), ec * rng.uniform(0.3, 2.0)
                pods[n] = jm.PodSample(p_cyc=pc, p_ins=pi, p_ref=pc, e_cyc=ec, e_ins=ei, e_ref=ec,
                                       cpu_s=dt * on)
                truth[n] = float(np.array([pc, pi, ec, ei]) * 1e-9 @ self.BETA)
            core = (sum(truth.values()) + self.IDLE_W * dt) * (1 + 0.01 * rng.standard_normal())
            rows.append((pods, jm.SysSample(dt=dt, core_j=core, uncore_j=0.1 * dt,
                                            pkg_j=core + 0.5 * dt, tsc_hz=1e9), truth))
        return rows

    def test_c1_recovery_and_c2_conservation(self):
        m = jm.HybridMeter(model="hybrid", bucket="none")
        est = {n: 0.0 for n in "ABC"}
        ref = {n: 0.0 for n in "ABC"}
        raw_gap, worst_gap = [], 0.0
        for i, (pods, s, truth) in enumerate(self.trace()):
            out, idle = m.step(pods, s)
            # C2: pods + idle reproduce every measured domain exactly.
            meas = {"core": s.core_j, "uncore": s.uncore_j, "pkg_rest": s.pkg_j - s.core_j - s.uncore_j}
            pod_core = sum(o["core_p"] + o["core_e"] for o in out.values())
            for d, v in meas.items():
                got = idle[d] + (pod_core if d == "core" else sum(o[{"uncore": "uncore_gpu"}.get(d, d)]
                                                                for o in out.values()))
                worst_gap = max(worst_gap, abs(got - v) / abs(v))
            beta = m.fit["core"].beta
            if beta is not None:
                raw = sum(float(np.array([p.p_cyc, p.p_ins, p.e_cyc, p.e_ins]) * 1e-9 @ beta[:4])
                          for p in pods.values()) + beta[-1] * s.dt
                raw_gap.append(abs(raw - s.core_j) / s.core_j)
            if i >= 300:   # score after the window has filled once
                for n in "ABC":
                    est[n] += out[n]["core_p"] + out[n]["core_e"]
                    ref[n] += truth[n]
        beta = m.fit["core"].beta
        rel = np.abs(beta[:4] - self.BETA) / self.BETA
        self.assertLessEqual(rel.max(), C["C1_recovery_coef_rel_err_max"], f"beta={beta}")
        self.assertLessEqual(abs(beta[-1] - self.IDLE_W) / self.IDLE_W, C["C1_recovery_coef_rel_err_max"])
        self.assertLessEqual(sim.mape(est, ref), C["C1_recovery_pod_mape_max"])
        self.assertLessEqual(worst_gap, C["C2_renorm_gap_max"])
        self.assertLessEqual(float(np.mean(raw_gap)), C["C2_raw_gap_linear_max"])

    def test_summed_model_cannot_separate_p_and_e(self):
        """Ablation sanity: same world, summed counters, no buckets -> the
        P-pod (A) and E-pod (B) bills are visibly wrong."""
        m = jm.HybridMeter(model="summed", bucket="none")
        est = {n: 0.0 for n in "AB"}
        ref = {n: 0.0 for n in "AB"}
        for i, (pods, s, truth) in enumerate(self.trace(seed=1)):
            out, _ = m.step(pods, s)
            if i >= 300:
                for n in "AB":
                    est[n] += out[n]["core"]
                    ref[n] += truth[n]
        self.assertGreater(sim.mape(est, ref), 0.15)


class Apportion(unittest.TestCase):
    def test_exact_sum(self):
        share, idle = jm.apportion(10.0, 2.0, {"a": 1.0, "b": 3.0}, {"a": 1, "b": 1})
        self.assertAlmostEqual(share["a"], 2.0)
        self.assertAlmostEqual(share["b"], 6.0)
        self.assertEqual(idle, 2.0)

    def test_measured_below_idle_goes_to_idle(self):
        share, idle = jm.apportion(1.0, 2.0, {"a": 1.0}, {"a": 1.0})
        self.assertEqual(share, {"a": 0.0})
        self.assertEqual(idle, 1.0)

    def test_zero_model_uses_fallback(self):
        share, _ = jm.apportion(5.0, 1.0, {"a": 0.0, "b": 0.0}, {"a": 1.0, "b": 0.0})
        self.assertEqual(share, {"a": 4.0, "b": 0.0})

    def test_nothing_to_split(self):
        share, idle = jm.apportion(5.0, 1.0, {"a": 0.0}, {"a": 0.0})
        self.assertEqual((share, idle), ({"a": 0.0}, 5.0))

    def test_negative_measured(self):   # pkg - core - uncore can dip below 0 on noise
        share, idle = jm.apportion(-0.1, 1.0, {"a": 1.0}, {"a": 1.0})
        self.assertEqual(share, {"a": 0.0})
        self.assertEqual(idle, -0.1)


class Readers(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_rapl_zones_and_wrap(self):
        r = self.root
        for z, name in (("intel-rapl:0", "package-0"), ("intel-rapl:0:0", "core"),
                        ("intel-rapl:0:1", "uncore"), ("intel-rapl:1", "psys")):
            write(r, f"/sys/class/powercap/{z}/name", name + "\n")
            write(r, f"/sys/class/powercap/{z}/max_energy_range_uj", "262143328850\n")
            write(r, f"/sys/class/powercap/{z}/energy_uj", "262143000000\n")
        zones = jm.rapl_zones(r)
        self.assertEqual(sorted(zones), ["core", "package-0", "uncore"])   # psys is another package
        path, rng = zones["core"]
        ctr = jm.EnergyCounter(path, rng)
        self.assertEqual(ctr.delta_j(), 0.0)
        write(r, "/sys/class/powercap/intel-rapl:0:0/energy_uj", "671\n")   # wrapped
        self.assertAlmostEqual(ctr.delta_j(), (262143328850 + 1 - 262143000000 + 671) / 1e6)
        self.assertEqual(jm.rapl_zones(os.path.join(r, "nowhere")), {})

    I915 = ("pos:\t0\nflags:\t02100002\nmnt_id:\t26\nino:\t685\n"
            "drm-driver:\ti915\ndrm-client-id:\t18\ndrm-pdev:\t0000:00:02.0\n"
            "drm-engine-render:\t9288864723 ns\ndrm-engine-copy:\t2035071108 ns\n"
            "drm-engine-video:\t0 ns\ndrm-engine-capacity-video:\t2\n"
            "drm-engine-video-enhance:\t100 ns\n")

    def test_parse_fdinfo(self):
        drv, key, eng = jm.parse_fdinfo(self.I915)
        self.assertEqual(drv, "i915")
        self.assertEqual(key, ("0000:00:02.0", "18"))
        self.assertEqual(eng, {"render": 9288864723, "copy": 2035071108, "video": 0,
                               "video-enhance": 100})
        self.assertIsNone(jm.parse_fdinfo("pos:\t0\nflags:\t02\n"))

    def test_drm_clients_dedupe_and_cgroup(self):
        r = self.root
        os.makedirs(os.path.join(r, "dev/dri"))
        for pid, cg, fds in (("100", "/kubepods/pod-a/c1", ("3", "4")),
                             ("101", "/kubepods/pod-a/c1", ("5",)),
                             ("200", "/kubepods/pod-b/c2", ("3",))):
            write(r, f"/proc/{pid}/cgroup", f"0::{cg}\n")
            os.makedirs(os.path.join(r, f"proc/{pid}/fd"))
            for fd in fds:
                os.symlink("/dev/dri/renderD128", os.path.join(r, f"proc/{pid}/fd/{fd}"))
                text = self.I915 if pid != "200" else self.I915.replace("client-id:\t18", "client-id:\t19")
                write(r, f"/proc/{pid}/fdinfo/{fd}", text)
            os.symlink("/dev/null", os.path.join(r, f"proc/{pid}/fd/0"))
        out = jm.drm_clients(r)
        # pid 100 fds 3+4 and pid 101 share client 18: counted once.
        self.assertEqual(out["/kubepods/pod-a/c1"]["render"], 9288864723)
        self.assertEqual(out["/kubepods/pod-b/c2"]["render"], 9288864723)

    def test_monotonic_engines(self):
        m = jm.MonotonicEngines()
        self.assertEqual(m.delta_s("k", 1_000_000_000), 0.0)
        self.assertEqual(m.delta_s("k", 1_500_000_000), 0.5)
        self.assertEqual(m.delta_s("k", 1_400_000_000), 0.0)   # stepped back: hold
        self.assertAlmostEqual(m.delta_s("k", 1_600_000_000), 0.1)

    def test_igpu_freq(self):
        write(self.root, "/sys/class/drm/card0-DP-1/gt_act_freq_mhz", "999\n")
        write(self.root, "/sys/class/drm/card0/gt_act_freq_mhz", "1450\n")
        self.assertEqual(jm.igpu_act_mhz(self.root), 1450.0)
        self.assertEqual(jm.igpu_act_mhz(os.path.join(self.root, "x")), 0.0)

    def test_hybrid_perf_events(self):
        write(self.root, "/sys/bus/event_source/devices/cpu_core/type", "4\n")
        write(self.root, "/sys/bus/event_source/devices/cpu_atom/type", "10\n")
        ev = jm.hybrid_perf_events(self.root)
        self.assertEqual(sorted(ev), ["cpu_atom", "cpu_core"])
        core = dict((e, (t, c)) for e, t, c in ev["cpu_core"])
        self.assertEqual(core["cycles"], (0, (4 << 32) | 0))
        self.assertEqual(core["ref-cycles"], (0, (4 << 32) | 9))
        atom = dict((e, c) for e, _, c in ev["cpu_atom"])
        self.assertEqual(atom["instructions"], (10 << 32) | 1)
        self.assertEqual(jm.hybrid_perf_events(os.path.join(self.root, "x")), {})

    def test_exposition(self):
        txt = jm.exposition({("ml", "p1"): {"core_p": 1.5, "core_e": 0.25}}, {"core": 9.0}, node="n")
        self.assertIn('k3d_gpu_pod_joules_total{namespace="ml",pod="p1",domain="core_e",node="n"} 0.250000',
                      txt)
        self.assertIn('k3d_gpu_node_idle_joules_total{domain="core",node="n"} 9.000000', txt)


class Features(unittest.TestCase):
    def test_buckets_and_masks(self):
        cf = jm.CoreFeatures("hybrid", "own", edges=(2000.0, 3000.0, 4000.0))
        p = jm.PodSample(p_cyc=4.2e9, p_ins=8e9, p_ref=1e9, e_cyc=3.3e9, e_ins=3e9, e_ref=1e9)
        v, mp = cf(p, 1e9, rail=0.0)
        self.assertEqual(v.size, 16)
        self.assertAlmostEqual(v[3 * 4 + 0], 4.2)   # P in bucket 3 (>= 4 GHz)
        self.assertAlmostEqual(v[2 * 4 + 2], 3.3)   # E in bucket 2
        self.assertTrue(mp[12] and mp[13] and not mp[10])
        rail = jm.CoreFeatures.rail_mhz([p], 1e9)
        self.assertAlmostEqual(rail, 4200.0)
        v2, _ = jm.CoreFeatures("hybrid", "rail")(p, 1e9, rail)
        self.assertAlmostEqual(v2[3 * 4 + 2], 3.3)  # E keyed on the rail bucket
        vs, _ = jm.CoreFeatures("summed", "none")(p, 1e9, rail)
        self.assertEqual(list(np.round(vs, 6)), [7.5, 11.0])

    def test_uncore_alias(self):
        v = jm.uncore_features(jm.PodSample(gpu_s={"render": 0.1, "video-enhance": 0.2, "foo": 0.1}), 1500)
        np.testing.assert_allclose(v, [0.15, 0.3, 0.0, 0.15])


class SimTruth(unittest.TestCase):
    def test_shapley_efficiency_and_cap(self):
        rng = np.random.default_rng(3)
        T = 200
        W = sim.make_workload(rng, T, "pinned")
        bg = np.zeros(T)
        for ph in (sim.Physics(), sim.Physics(cap_w=20.0)):
            shap, marg, empty = sim.truth(ph, W, bg, 0.2)
            c, u, r, d = sim.physics(ph, W, {n: 1 for n in W}, bg)
            full = (c + u + r) * 0.2
            np.testing.assert_allclose(sum(shap.values()), full - empty, rtol=1e-9, atol=1e-9)
            np.testing.assert_allclose(sum(marg.values()), full - empty, rtol=1e-9, atol=1e-9)
            if ph.cap_w:
                self.assertTrue(np.all(c + u + r <= ph.cap_w + 1e-3) or np.any(d["s"] <= 0.3 + 1e-9))
                self.assertGreater(np.mean(d["s"] < 1), 0.1)

    def test_shared_rail_couples_e_to_p(self):
        """E-pod energy per cycle rises when the P-pod is busy (refuter 1)."""
        T = 2
        W = {"P": dict(nP=np.full(T, 4), nE=np.zeros(T), u=np.array([0.0, 1.0]), k=np.full(T, 1.5),
                       mr=np.full(T, .1), pf=1.0, g=np.zeros((T, 4))),
             "E": dict(nP=np.zeros(T), nE=np.full(T, 8), u=np.ones(T), k=np.full(T, 1.5),
                       mr=np.full(T, .1), pf=1.0, g=np.zeros((T, 4)))}
        for shared, rises in ((True, True), (False, False)):
            ph = sim.Physics(rail_shared=shared)
            e_only, _, _, _ = sim.physics(ph, W, {"P": 0, "E": 1}, np.zeros(T))
            none, _, _, _ = sim.physics(ph, W, {"P": 0, "E": 0}, np.zeros(T))
            both, _, _, _ = sim.physics(ph, W, {"P": 1, "E": 1}, np.zeros(T))
            p_only, _, _, _ = sim.physics(ph, W, {"P": 1, "E": 0}, np.zeros(T))
            interaction = (both - p_only) - (e_only - none)   # E's marginal with vs without P
            self.assertEqual(bool(interaction[1] > 1e-6), rises)


if __name__ == "__main__":
    unittest.main()
