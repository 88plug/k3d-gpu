"""Unit criteria U1-U5 from acceptance.json plus mechanism checks. Stdlib only, except
the sim smoke tests, which skip without numpy."""
import ast
import itertools
import json
import os
import random
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from coreplacer import amx, hfi, perf, plan, render  # noqa: E402
from coreplacer.estimate import DriftDetector, Estimator, Window, window_from_percpu  # noqa: E402
from coreplacer.placer import EFFICIENT, SHARED, Placer  # noqa: E402

with open(os.path.join(ROOT, "acceptance.json")) as _f:
    ACC = json.load(_f)["unit"]


class U1Netlink(unittest.TestCase):
    def test_decodes_kernel_format(self):
        caps = {c: (1020 if c < 16 else 408, 512 + c) for c in range(16)}
        msg = hfi.build_capability_event(caps)
        (mtype, payload), = hfi.iter_messages(msg)
        self.assertEqual(mtype, 0x20)
        self.assertEqual(hfi.parse_capability_event(payload), caps)

    def test_nested_flag_optional(self):
        msg = hfi.build_capability_event({3: (0, 4)}, nested_flag=False)
        (_t, payload), = hfi.iter_messages(msg)
        self.assertEqual(hfi.parse_capability_event(payload), {3: (0, 4)})

    def test_ignores_other_events(self):
        msg = bytearray(hfi.build_capability_event({0: (4, 4)}))
        msg[16] = 13  # THERMAL_GENL_EVENT_TZ_GOV_CHANGE
        (_t, payload), = hfi.iter_messages(bytes(msg))
        self.assertIsNone(hfi.parse_capability_event(payload))

    def test_rejects_truncated(self):
        msg = hfi.build_capability_event({0: (4, 4), 1: (8, 8)})
        payload = msg[16:-6]
        with self.assertRaises(ValueError):
            hfi.parse_capability_event(payload)

    def test_constants_match_v66_uapi(self):
        # include/uapi/linux/thermal.h at v6.6: attr enum index 20..23, event index 14.
        self.assertEqual((hfi.THERMAL_GENL_ATTR_CPU_CAPABILITY, hfi.THERMAL_GENL_ATTR_CPU_CAPABILITY_ID,
                          hfi.THERMAL_GENL_ATTR_CPU_CAPABILITY_PERFORMANCE,
                          hfi.THERMAL_GENL_ATTR_CPU_CAPABILITY_EFFICIENCY,
                          hfi.THERMAL_GENL_EVENT_CPU_CAPABILITY_CHANGE), (20, 21, 22, 23, 14))


def _tracker():
    a = ACC["U2_hfi_hysteresis"]
    return hfi.CapabilityTracker(range(32), set(range(16)), hold_s=a["hold_s"],
                                 max_per_min=a["max_rewrites_per_min"]), a


class U2Hysteresis(unittest.TestCase):
    def test_static_stream_no_rewrites(self):
        tr, a = _tracker()
        rewrites = sum(tr.tick(t / 10) is not None for t in range(600))
        self.assertEqual(rewrites, a["static_stream_rewrites"])

    def test_flapping_below_hold_never_rewrites(self):
        tr, a = _tracker()
        rewrites, t, low = 0, 0.0, False
        while t < a["flap_duration_s"]:
            low = not low
            rewrites += tr.update(t, {5: (0 if low else 1020, 512)}) is not None
            for k in range(1, 5):
                rewrites += tr.tick(t + k * a["flap_period_s"] / 5) is not None
            t += a["flap_period_s"]
        self.assertLessEqual(rewrites, a["flap_rewrites_max"])

    def test_zero_cap_cpu_dropped_after_hold(self):
        tr, a = _tracker()
        self.assertIsNone(tr.update(0.0, {7: (0, 0)}))
        got, t = None, 0.0
        while got is None and t < 10:
            t += 0.1
            got = tr.tick(t)
        self.assertIsNotNone(got)
        self.assertLessEqual(t, a["zero_cap_excluded_within_s"])
        avail, pool = got
        self.assertNotIn(7, avail)
        self.assertNotIn(7, pool)
        self.assertIn("cpuset:0-6,8-31", render.balloons_spec(avail)["availableResources"]["cpu"])

    def test_rate_limit(self):
        tr, a = _tracker()
        rewrites = 0
        for k in range(40):  # each change holds 3 s, so every one is eligible
            t0 = k * 3.0
            tr.update(t0, {k % 16: (0 if (k // 16) % 2 == 0 else 1020, 512)})
            rewrites += any(tr.tick(t0 + d / 10) is not None for d in range(1, 30))
            if t0 < 60:
                self.assertLessEqual(rewrites, a["max_rewrites_per_min"])
        self.assertLessEqual(rewrites, a["max_rewrites_per_min"] * 3)

    def test_live_p_pool_follows_perf_rank(self):
        tr, _ = _tracker()
        tr.update(0.0, {c: (400, 512) for c in range(4)})  # four P CPUs lose perf rank
        avail, pool = tr.tick(2.5)
        self.assertEqual(pool, set(range(4, 16)))
        self.assertEqual(avail, set(range(32)))


class U3Planner(unittest.TestCase):
    def test_exact_equals_bruteforce(self):
        rng = random.Random(0)
        for _ in range(ACC["U3_knapsack"]["random_instances"]):
            n = rng.randint(1, 8)
            pods = {i: (rng.choice([0.1, 0.5, 1, 2, 3, 4]), rng.uniform(1.0, 2.5)) for i in range(n)}
            cap = rng.randint(1, 10)
            best = max(plan.value(pods, set(c), cap) for k in range(n + 1)
                       for c in itertools.combinations(pods, k))
            self.assertAlmostEqual(plan.value(pods, plan.best_split(pods, cap), cap), best, places=9)

    def test_prefix_greedy_gap_is_bounded(self):
        """Above exhaustive_max the planner uses a density prefix; record its gap."""
        rng = random.Random(1)
        gaps = []
        for _ in range(200):
            n = rng.randint(2, 9)
            pods = {i: (rng.choice([0.1, 0.5, 1, 2, 3, 4]), rng.uniform(1.0, 2.5)) for i in range(n)}
            cap = rng.randint(1, 10)
            exact = plan.value(pods, plan.best_split(pods, cap), cap)
            greedy = plan.value(pods, plan.best_split(pods, cap, exhaustive_max=0), cap)
            gaps.append(1 - greedy / exact)
        self.assertLess(max(gaps), 0.05)

    def test_gpu_blocked_pod_stays_on_p(self):
        pods = {"agent": (0.2, 1.6), "batch-a": (4, 1.2), "batch-b": (4, 1.15), "embed": (4, 1.9)}
        shared = plan.best_split(pods, 8)
        self.assertIn("agent", shared)
        self.assertIn("embed", shared)

    def test_forced_shared(self):
        self.assertIn("x", plan.best_split({"x": (8, 1.01), "y": (8, 2.0)}, 8, forced_shared={"x"}))


class U4PerfEncoding(unittest.TestCase):
    def test_attr_size(self):
        self.assertEqual(len(perf.pack_attr(0, 0)), ACC["U4_perf_encoding"]["attr_size"])

    def test_attr_layout(self):
        raw = perf.pack_attr(perf.PERF_TYPE_RAW, perf.EXE_AMX_BUSY, disabled=True)
        ev_type, size, config = struct.unpack_from("<IIQ", raw)
        flags = struct.unpack_from("<Q", raw, 40)[0]
        self.assertEqual((ev_type, size, config, flags), (4, 112, 0x02B7, 1))

    def test_hybrid_and_amx_configs(self):
        self.assertEqual(perf.hybrid_instructions(8), (0, (8 << 32) | 1))
        self.assertEqual(perf.amx_busy(), (4, 0x02B7))
        self.assertEqual(perf.task_clock(), (1, 1))

    def test_cpulist_roundtrip(self):
        for text in ("0-15", "0-6,8-31", "3", "0,2,4-5"):
            self.assertEqual(perf.format_cpulist(perf.parse_cpulist(text)), text)

    def test_window_split_by_core_type(self):
        w = window_from_percpu(3.0, {0: (300, 1.0), 1: (200, 1.0), 16: (100, 1.0)}, set(range(16)))
        self.assertEqual((w.inst_p, w.time_p, w.inst_e, w.time_e), (500, 2.0, 100, 1.0))


class U5AmxFsm(unittest.TestCase):
    def test_promote_and_demote_counts(self):
        a = ACC["U5_amx_fsm"]
        f = amx.AmxFsm(promote_after=a["promote_after_windows"], demote_after=a["demote_after_windows"])
        out = [f.update("p", 0.3) for _ in range(a["promote_after_windows"])]
        self.assertEqual(out, [None] * (a["promote_after_windows"] - 1) + ["promote"])
        out = [f.update("p", 0.0) for _ in range(a["demote_after_windows"])]
        self.assertEqual(out, [None] * (a["demote_after_windows"] - 1) + ["demote"])

    def test_dither_never_moves(self):
        f = amx.AmxFsm()
        moves = [f.update("p", x) for x in [0.06, 0.03, 0.06, 0.03, 0.07, 0.02] * 20]
        self.assertEqual(sum(m is not None for m in moves), ACC["U5_amx_fsm"]["dither_between_thresholds_transitions"])

    def test_amx_balloon_render(self):
        spec = render.balloons_spec(range(32), amx=True)
        t = {b["name"]: b for b in spec["balloonTypes"]}
        self.assertTrue(t["amx"]["hideHyperthreads"])
        self.assertEqual(t["amx"]["loads"], ["amx-core"])
        self.assertEqual(spec["loadClasses"][0]["level"], "core")
        self.assertEqual(spec["balloonTypes"][-1]["name"], "shared")


class EstimatorAndDrift(unittest.TestCase):
    def test_concurrent_speedup(self):
        e = Estimator().observe("a", Window(3, inst_p=2.0e9, time_p=1.0, inst_e=1.0e9, time_e=1.0))
        self.assertAlmostEqual(e.speedup, 2.0)
        self.assertAlmostEqual(e.demand, 2 / 3)

    def test_needs_time_on_both_types(self):
        e = Estimator().observe("a", Window(3, inst_p=2e9, time_p=1.0, inst_e=1e7, time_e=0.01))
        self.assertEqual(e.samples, 0)

    def test_drift_needs_consecutive_windows(self):
        d = DriftDetector(0.25, 2)
        d.arm("a", 100)
        self.assertEqual([d.update("a", r) for r in (130, 100, 130, 131)], [False, False, False, True])


class PlacerIsolation(unittest.TestCase):
    def test_placer_never_imports_sim(self):
        for mod in ("placer.py", "plan.py", "estimate.py", "hfi.py", "amx.py", "render.py", "perf.py"):
            with open(os.path.join(ROOT, "coreplacer", mod)) as f:
                tree = ast.parse(f.read())
            names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
            names |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            self.assertNotIn("sim", names, mod)

    def test_fences_low_speedup_pod_on_e(self):
        p = Placer()
        mk = lambda s: Window(3, inst_p=s * 6, time_p=6, inst_e=6, time_e=6)  # noqa: E731
        changes = {}
        for _ in range(3):
            changes.update(p.step({"fast": mk(2.2), "slow": mk(1.05), "mid": mk(1.6)}, 8))
        self.assertEqual(changes, {"slow": EFFICIENT})
        self.assertEqual(p.labels["fast"], SHARED)


class SimSmoke(unittest.TestCase):
    def setUp(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy not installed")

    def test_light_mix_needs_no_moves(self):
        from coreplacer import sim
        r = sim.evaluate_mix(sim.Host(), "M1_agent_light", 1, T=120)
        self.assertEqual(r["moves"], 0)
        self.assertAlmostEqual(r["auto"][0], r["unpinned"][0], places=6)

    def test_oracle_dominates_every_static_baseline(self):
        from coreplacer import sim
        r = sim.evaluate_mix(sim.Host(), "M2_oversub_stationary", 1, T=120)
        for k in ("unpinned", "all_p"):
            self.assertGreaterEqual(r["exhaustive_static"][0], r[k][0])


if __name__ == "__main__":
    unittest.main()
