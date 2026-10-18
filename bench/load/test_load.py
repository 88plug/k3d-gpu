#!/usr/bin/env python3
"""Unit tests for bench/load/load.py (no docker, no root, fake sysfs/procfs)."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import load  # noqa: E402


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class FakeHost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sys = os.path.join(self.tmp, "sys")
        self.proc = os.path.join(self.tmp, "proc")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def cpu(self, c, siblings):
        write(self.sys, f"devices/system/cpu/cpu{c}/topology/thread_siblings_list", siblings)


class CpuListTest(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(load.parse_cpulist("0-3,8,10-11\n"), [0, 1, 2, 3, 8, 10, 11])
        self.assertEqual(load.format_cpulist([11, 10, 8, 3, 2, 1, 0]), "0-3,8,10-11")
        self.assertEqual(load.parse_cpulist(""), [])
        self.assertEqual(load.format_cpulist([5]), "5")


class LoadCpusTest(FakeHost):
    def test_hybrid_uses_e_cores(self):
        write(self.sys, "devices/system/cpu/online", "0-31")
        write(self.sys, "devices/cpu_atom/cpus", "16-31")
        write(self.sys, "devices/cpu_core/cpus", "0-15")
        self.assertEqual(load.load_cpus(self.sys), list(range(16, 32)))

    def test_hybrid_offline_e_cores_are_dropped(self):
        write(self.sys, "devices/system/cpu/online", "0-15,16-23")
        write(self.sys, "devices/cpu_atom/cpus", "16-31")
        self.assertEqual(load.load_cpus(self.sys), list(range(16, 24)))

    def test_non_hybrid_smt_takes_top_half_of_whole_cores(self):
        # 4 cores, SMT siblings n and n+4 (the usual Xeon/Core enumeration)
        write(self.sys, "devices/system/cpu/online", "0-7")
        for c in range(8):
            self.cpu(c, f"{c % 4},{c % 4 + 4}")
        self.assertEqual(load.load_cpus(self.sys), [2, 3, 6, 7])

    def test_non_hybrid_no_smt(self):
        write(self.sys, "devices/system/cpu/online", "0-5")
        for c in range(6):
            self.cpu(c, str(c))
        self.assertEqual(load.load_cpus(self.sys), [3, 4, 5])

    def test_single_core_still_gets_one(self):
        write(self.sys, "devices/system/cpu/online", "0-1")
        self.cpu(0, "0-1")
        self.cpu(1, "0-1")
        self.assertEqual(load.load_cpus(self.sys), [0, 1])

    def test_empty_cpu_atom_falls_back(self):
        write(self.sys, "devices/system/cpu/online", "0-3")
        write(self.sys, "devices/cpu_atom/cpus", "")
        for c in range(4):
            self.cpu(c, str(c))
        self.assertEqual(load.load_cpus(self.sys), [2, 3])


class PlanTest(unittest.TestCase):
    CPUS = list(range(16, 32))

    def test_none_runs_nothing(self):
        self.assertEqual(load.plan("none", self.CPUS), [])

    def test_every_container_is_pinned_labelled_and_bounded(self):
        for level in ("typical", "heavy"):
            argvs = load.plan(level, self.CPUS, ttl=600, now=1000)
            self.assertEqual(len(argvs), 3)
            for a in argvs:
                s = " ".join(a)
                self.assertEqual(a[:4], ["docker", "run", "-d", "--rm"])
                self.assertIn(f"--label {load.LABEL}=1", s)
                self.assertIn(f"--label {load.LABEL}.level={level}", s)
                self.assertIn(f"--label {load.LABEL}.expires=1600", s)
                self.assertIn("--cpuset-cpus 16-31", s)
                self.assertIn("--timeout 600s", s)
                self.assertIn(load.IMAGE, a)
                self.assertRegex(load.IMAGE, r"@sha256:[0-9a-f]{64}$")

    def test_typical_mix(self):
        s = [" ".join(a) for a in load.plan("typical", self.CPUS)]
        self.assertIn("--cpu 16 --cpu-load 30", s[0])
        self.assertNotIn("--cpus ", s[0])
        self.assertIn("--cpus 1 ", s[1])          # 2 stream workers at duty 0.5
        self.assertIn("--stream 2 --stream-l3-size 128m", s[1])
        self.assertIn("--cpus 0.25 ", s[2])
        self.assertIn("--hdd 1 --hdd-bytes 256m", s[2])

    def test_heavy_has_no_quota(self):
        s = [" ".join(a) for a in load.plan("heavy", self.CPUS)]
        self.assertIn("--cpu 16 --cpu-load 100", s[0])
        self.assertIn("--stream 4", s[1])
        self.assertFalse(any("--cpus " in x for x in s))

    def test_typical_duty_models_a_busy_box(self):
        for n in (8, 12, 16, 24, 32, 64):
            self.assertGreaterEqual(load.duty("typical", n), 0.30, n)
            self.assertLessEqual(load.duty("typical", n), 0.40, n)
        self.assertEqual(load.duty("none", 16), 0.0)
        self.assertEqual(load.duty("heavy", 16), 1.0)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            load.plan("typical", [])
        with self.assertRaises(ValueError):
            load.plan("loud", self.CPUS)
        for ttl in (0, load.MAX_TTL + 1):
            with self.assertRaises(ValueError):
                load.plan("typical", self.CPUS, ttl=ttl)


class StartStopTest(unittest.TestCase):
    """start/stop against a fake docker: idempotence and label-scoped cleanup."""

    def setUp(self):
        self.containers = {}   # id -> (level, expires)
        self.calls = []

    def fake_docker(self, *args, check=True):
        self.calls.append(args)
        if args[0] == "ps":
            self.assertIn(f"label={load.LABEL}=1", args)
            return "".join(f"{i} {lv} {ex}\n" for i, (lv, ex) in self.containers.items())
        if args[0] == "rm":
            for i in args[2:]:
                self.containers.pop(i, None)
        return ""

    def fake_run(self, argv, **kw):
        level = [a for a in argv if a.startswith(f"{load.LABEL}.level=")][0].split("=")[1]
        exp = int([a for a in argv if a.startswith(f"{load.LABEL}.expires=")][0].split("=")[1])
        self.containers[f"c{len(self.containers)}{level}"] = (level, exp)
        return mock.Mock(returncode=0, stdout="", stderr="")

    def test_idempotent_and_replaces_other_level(self):
        with mock.patch.object(load, "_docker", self.fake_docker), \
             mock.patch.object(load.subprocess, "run", self.fake_run):
            load.start("typical", [16, 17])
            self.assertEqual(len(self.containers), 3)
            self.assertEqual(load.start("typical", [16, 17]), "already running")
            self.assertEqual(len(self.containers), 3)
            load.start("heavy", [16, 17])
            self.assertEqual({v[0] for v in self.containers.values()}, {"heavy"})
            self.assertEqual(load.stop(), 3)
            self.assertEqual(self.containers, {})
            self.assertEqual(load.stop(), 0)

    def test_expired_leftover_is_reaped(self):
        self.containers = {"old": ("typical", 1)}
        with mock.patch.object(load, "_docker", self.fake_docker), \
             mock.patch.object(load.subprocess, "run", self.fake_run):
            load.start("typical", [16])
        self.assertNotIn("old", self.containers)
        self.assertEqual(len(self.containers), 3)


class SamplerTest(FakeHost):
    def test_cpu_times_counts_only_load_cpus(self):
        stat = ("cpu  9 9 9 9 9 0 0 0 0 0\n"
                "cpu0 100 0 0 900 0 0 0 0 0 0\n"
                "cpu1 50 0 50 800 100 0 0 0 0 0\n")
        self.assertEqual(load.cpu_times(stat, [1]), (100, 1000))
        self.assertEqual(load.cpu_times(stat, [0, 1]), (200, 2000))

    def test_swap(self):
        self.assertEqual(load.swap_used_mib("SwapTotal: 2048 kB\nSwapFree: 1024 kB\n"), 1.0)

    def test_energy_wraps(self):
        self.assertEqual(load.energy_delta(990, 10, 1000), 20)
        self.assertIsNone(load.energy_delta(None, 10, 1000))

    def test_window_on_fake_host(self):
        write(self.sys, "class/powercap/intel-rapl:0/name", "package-0")
        write(self.sys, "class/powercap/intel-rapl:0/energy_uj", "1000000")
        write(self.sys, "class/powercap/intel-rapl:0/max_energy_range_uj", "262143328850")
        write(self.sys, "devices/system/cpu/cpu1/thermal_throttle/core_throttle_count", "4")
        write(self.proc, "stat", "cpu1 10 0 0 90 0 0 0 0\n")
        write(self.proc, "meminfo", "SwapTotal: 0 kB\nSwapFree: 0 kB\n")
        write(self.proc, "loadavg", "3.50 2.00 1.00 1/100 42\n")
        stats = iter(["cpu1 10 0 0 90 0 0 0 0\n", "cpu1 60 0 0 140 0 0 0 0\n"])
        real_read = load._read

        def reader(path):
            if path.endswith("/stat") and path.startswith(self.proc):
                return next(stats)
            if path.endswith("energy_uj") and "max" not in path:
                return reader.e.pop(0)
            return real_read(path)
        reader.e = ["1000000", "3000000"]
        with mock.patch.object(load, "_read", reader), mock.patch.object(load.time, "sleep"):
            w = load.window(0.0, [1], sysfs=self.sys, proc=self.proc, tag="A", level="typical")
        self.assertEqual(w["busy_pct"], 50.0)
        self.assertEqual(w["load1"], 3.5)
        self.assertEqual((w["tag"], w["level"]), ("A", "typical"))
        self.assertEqual(w["swap_delta_mib"], 0.0)
        self.assertIsNotNone(w["pkg_w"])   # 2 J over a near-zero window
        self.assertEqual(w["throttle_per_min"], 0.0)


def win(tag, busy=60.0, load1=8.0, w=40.0, thr=0.0, swap=100.0, dswap=0.0, level="typical"):
    return {"tag": tag, "level": level, "busy_pct": busy, "load1": load1, "pkg_w": w,
            "throttle_per_min": thr, "swap_used_mib": swap, "swap_delta_mib": dswap}


class RaplExecTest(unittest.TestCase):
    def test_exec_prefix_reads_a_root_only_counter(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        zone = os.path.join(tmp, "intel-rapl:0")
        os.makedirs(zone)
        reader = os.path.join(tmp, "reader")
        with open(reader, "w") as f:
            f.write("#!/bin/sh\necho 4242\n")
        os.chmod(reader, 0o755)
        with mock.patch.dict(os.environ, {"K3D_GPU_RAPL_EXEC": reader}):
            self.assertEqual(load.rapl_elevation(), [reader])
            self.assertEqual(load.read_energy_uj(zone), 4242)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load.rapl_elevation(), ["sudo", "-n"])


class MatchedTest(unittest.TestCase):
    def test_high_but_matched_load_is_matched(self):
        ws = [win("A", busy=97, load1=40, w=95), win("B", busy=96, load1=38, w=92),
              win("A", busy=98, load1=41, w=96), win("B", busy=97, load1=39, w=93)]
        r = load.matched(ws)
        self.assertTrue(r["matched"], r)
        self.assertEqual(r["verdict"], "MATCHED")

    def test_drifting_load_is_inconclusive(self):
        r = load.matched([win("A", busy=35), win("B", busy=55)])
        self.assertFalse(r["matched"])
        self.assertIn("busy_pct", r["verdict"])
        self.assertTrue(r["verdict"].startswith("INCONCLUSIVE"))

    def test_boundaries(self):
        self.assertTrue(load.matched([win("A", busy=50), win("B", busy=55)])["matched"])
        self.assertFalse(load.matched([win("A", busy=50), win("B", busy=55.01)])["matched"])
        # pkg_w: max(3 W, 10%) -> 100 W arms may differ by 10 W
        self.assertTrue(load.matched([win("A", w=100), win("B", w=90)])["matched"])
        self.assertFalse(load.matched([win("A", w=100), win("B", w=89)])["matched"])
        self.assertTrue(load.matched([win("A", w=10), win("B", w=13)])["matched"])

    def test_unmeasured_metric_is_skipped_but_partial_fails(self):
        r = load.matched([win("A", w=None), win("B", w=None)])
        self.assertTrue(r["matched"])
        self.assertEqual([c for c in r["checks"] if c["metric"] == "pkg_w"][0]["status"], "unmeasured")
        self.assertFalse(load.matched([win("A", w=None), win("B", w=40)])["matched"])

    def test_mixed_levels_and_single_arm_fail(self):
        self.assertFalse(load.matched([win("A"), win("B", level="heavy")])["matched"])
        self.assertFalse(load.matched([win("A"), win("A")])["matched"])

    def test_three_arms_use_the_full_spread(self):
        self.assertFalse(load.matched([win("A", busy=50), win("B", busy=54), win("C", busy=58)])["matched"])

    def test_order_interleaves(self):
        self.assertEqual(load.order(["A", "B"], 3), ["A", "B", "A", "B", "A", "B"])


def pre(tag, busy=60.0, thr=50.0, swap=100.0, dswap=0.0, level="typical", load1=8.0, w=40.0, running=3):
    """An amendment-27 pre-window: arm idle, sampled at the end of the settle gap."""
    return {"tag": tag, "level": level, "phase": "pre", "busy_pct": busy, "throttle_per_min": thr,
            "swap_used_mib": swap, "swap_delta_mib": dswap, "load1": load1, "pkg_w": w,
            "load_expected": 3, "load_running": running, "settled": True}


def during(tag, busy=60.0, thr=50.0, w=40.0, load1=8.0, swap=100.0, dswap=0.0, level="typical", running=3):
    """An amendment-27 window sampled while the arm runs."""
    return {"tag": tag, "level": level, "phase": "during", "busy_pct": busy, "throttle_per_min": thr,
            "pkg_w": w, "load1": load1, "swap_used_mib": swap, "swap_delta_mib": dswap,
            "load_expected": 3, "load_running": running}


class Amendment27Test(unittest.TestCase):
    """Covariates are matched; post-treatment outcomes are reported, never gating."""

    def run_ab(self, a_pre, b_pre, a_dur, b_dur):
        return load.matched([a_pre, a_dur, b_pre, b_dur, dict(a_pre), dict(a_dur), dict(b_pre), dict(b_dur)])

    def test_treatment_that_raises_throttle_still_matches(self):
        # Cache OFF does ~10x the prefill: during the arm it throttles 10x, draws more
        # package power and raises load1. Those are outcomes of the treatment.
        r = self.run_ab(pre("ON", thr=40), pre("OFF", thr=44),
                        during("ON", thr=40, w=90, load1=18), during("OFF", thr=400, w=110, load1=26))
        self.assertTrue(r["matched"], r)
        self.assertEqual(r["verdict"], "MATCHED")
        self.assertEqual(r["rule"], "amendment-27")
        outs = {o["metric"]: o for o in r["outcomes"]}
        self.assertEqual(set(outs), {"pkg_w", "throttle_per_min", "load1"})
        self.assertEqual(outs["throttle_per_min"]["arms"], {"ON": 40.0, "OFF": 400.0})
        gated_during = {c["metric"] for c in r["checks"] if c["phase"] == "during"}
        self.assertFalse(gated_during & {"pkg_w", "throttle_per_min", "load1"})

    def test_background_drift_in_the_pre_window_busy_does_not_match(self):
        r = self.run_ab(pre("A", busy=40), pre("B", busy=60), during("A"), during("B"))
        self.assertFalse(r["matched"])
        self.assertTrue(r["verdict"].startswith("INCONCLUSIVE"), r["verdict"])
        self.assertIn("pre busy_pct", r["verdict"])

    def test_background_drift_in_the_pre_window_throttle_does_not_match(self):
        # Both arms idle, yet one arm's baseline throttles far more: the host drifted.
        r = self.run_ab(pre("A", thr=40), pre("B", thr=600), during("A", thr=40), during("B", thr=40))
        self.assertFalse(r["matched"])
        self.assertIn("pre throttle_per_min", r["verdict"])

    def test_load_cpu_busy_drift_during_the_arm_does_not_match(self):
        r = self.run_ab(pre("A"), pre("B"), during("A", busy=40), during("B", busy=70))
        self.assertFalse(r["matched"])
        self.assertIn("during busy_pct", r["verdict"])

    def test_swap_drift_does_not_match(self):
        r = self.run_ab(pre("A", swap=100), pre("B", swap=900), during("A", swap=100), during("B", swap=100))
        self.assertFalse(r["matched"])
        self.assertIn("swap_used_mib", r["verdict"])

    def test_missing_load_container_voids_the_run(self):
        r = self.run_ab(pre("A"), pre("B"), during("A"), during("B", running=0))
        self.assertFalse(r["matched"])
        self.assertTrue(r["void"])
        self.assertTrue(r["verdict"].startswith("VOID"), r["verdict"])

    def test_unverified_load_presence_voids_the_run(self):
        r = self.run_ab(pre("A"), pre("B"), during("A"), during("B", running=None))
        self.assertTrue(r["void"], r)

    def test_level_none_needs_no_containers(self):
        ws = [dict(pre(t, level="none", running=0), load_expected=0) for t in "AB"]
        ws += [dict(during(t, level="none", running=0), load_expected=0) for t in "AB"]
        self.assertTrue(load.matched(ws)["matched"])

    def test_every_arm_needs_a_pre_window(self):
        r = load.matched([pre("A"), during("A"), during("B")])
        self.assertFalse(r["matched"])
        self.assertIn("pre-window", r["verdict"])

    def test_during_windows_in_some_arms_only_fails(self):
        r = load.matched([pre("A"), pre("B"), during("A")])
        self.assertFalse(r["matched"])
        self.assertIn("during busy_pct measured in some arms only", r["verdict"])

    def test_baseline_windows_do_not_enter(self):
        base = dict(pre("A", busy=5, thr=900), phase="baseline")
        self.assertTrue(load.matched([base, pre("A"), pre("B"), during("A"), during("B")])["matched"])

    def test_window_without_phase_in_an_amendment_27_run_fails(self):
        r = load.matched([pre("A"), pre("B"), during("A"), win("B")])
        self.assertFalse(r["matched"])
        self.assertIn("phase", r["verdict"])

    def test_windows_without_phase_keep_the_legacy_rule(self):
        # Old runs are never regraded: no phase field -> the A1/A2 rule, throttle gating.
        r = load.matched([win("A", thr=40), win("B", thr=400)])
        self.assertFalse(r["matched"])
        self.assertEqual(r["rule"], "legacy")
        self.assertIn("throttle_per_min", r["verdict"])


class MatchedCliTest(unittest.TestCase):
    def exit_code(self, ws):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "w.jsonl")
        with open(path, "w") as f:
            f.write("".join(json.dumps(w) + "\n" for w in ws))
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            return load.main(["matched", path])

    def test_exit_codes(self):
        self.assertEqual(self.exit_code([pre("A"), pre("B")]), 0)
        self.assertEqual(self.exit_code([pre("A", busy=10), pre("B", busy=90)]), 3)
        self.assertEqual(self.exit_code([pre("A"), pre("B", running=2)]), 4)


class WindowPhaseTest(unittest.TestCase):
    def fake(self, stat_pairs):
        stats = iter(stat_pairs)
        real_read = load._read

        def reader(path):
            if path.endswith("/stat"):
                return next(stats)
            if path.endswith("meminfo"):
                return "SwapTotal: 0 kB\nSwapFree: 0 kB\n"
            if path.endswith("loadavg"):
                return "1.0 1.0 1.0 1/1 1\n"
            return real_read(path)
        return reader

    def test_treatment_cpus_are_left_out_of_busy(self):
        reader = self.fake(["cpu1 0 0 0 100 0 0 0 0\ncpu2 0 0 0 100 0 0 0 0\n",
                            "cpu1 100 0 0 100 0 0 0 0\ncpu2 0 0 0 200 0 0 0 0\n"])
        with mock.patch.object(load, "_read", reader), mock.patch.object(load.time, "sleep"), \
                mock.patch.object(load, "rapl_zone", return_value=None):
            w = load.window(1.0, [1, 2], tag="A", level="typical", phase="during",
                            exclude=[1], presence=lambda: 3)
        self.assertEqual(w["busy_pct"], 0.0)          # only cpu2, the core the treatment left alone
        self.assertEqual(w["busy_cpus"], "2")
        self.assertEqual(w["phase"], "during")
        self.assertEqual((w["load_expected"], w["load_running"]), (3, 3))

    def test_presence_is_the_minimum_of_start_and_end(self):
        counts = iter([3, 1])
        reader = self.fake(["cpu0 0 0 0 1 0 0 0 0\n", "cpu0 0 0 0 2 0 0 0 0\n"])
        with mock.patch.object(load, "_read", reader), mock.patch.object(load.time, "sleep"), \
                mock.patch.object(load, "rapl_zone", return_value=None):
            w = load.window(0.0, [0], level="typical", phase="pre", presence=lambda: next(counts))
        self.assertEqual(w["load_running"], 1)

    def test_all_load_cpus_excluded_leaves_busy_unmeasured(self):
        reader = self.fake(["cpu0 0 0 0 1 0 0 0 0\n", "cpu0 0 0 0 2 0 0 0 0\n"])
        with mock.patch.object(load, "_read", reader), mock.patch.object(load.time, "sleep"), \
                mock.patch.object(load, "rapl_zone", return_value=None):
            w = load.window(0.0, [0], level="none", phase="during", exclude=[0], presence=lambda: 0)
        self.assertIsNone(w["busy_pct"])
        self.assertEqual(w["load_expected"], 0)

    def test_legacy_window_has_no_phase(self):
        reader = self.fake(["cpu0 0 0 0 1 0 0 0 0\n", "cpu0 0 0 0 2 0 0 0 0\n"])
        with mock.patch.object(load, "_read", reader), mock.patch.object(load.time, "sleep"), \
                mock.patch.object(load, "rapl_zone", return_value=None):
            w = load.window(0.0, [0], level="typical")
        self.assertNotIn("phase", w)
        self.assertNotIn("load_running", w)


class SettleTest(unittest.TestCase):
    BASE = {"busy_pct": 37.0, "throttle_per_min": 40.0}

    def sampler(self, values):
        it = iter(values)

        def sample(seconds):
            busy, thr = next(it)
            return {"busy_pct": busy, "throttle_per_min": thr, "seconds": seconds}
        return sample

    def test_waits_for_carry_over_to_decay(self):
        # The previous arm's hog: busy and throttle stay high, then decay to the baseline.
        w = load.settle(self.BASE, self.sampler([(80, 900), (55, 300), (38, 45)]), window_s=5, max_s=60)
        self.assertTrue(w["settled"])
        self.assertEqual(w["settle_s"], 15)
        self.assertEqual(w["busy_pct"], 38)

    def test_gives_up_at_the_cap_and_says_so(self):
        w = load.settle(self.BASE, self.sampler([(80, 900)] * 20), window_s=5, max_s=60)
        self.assertFalse(w["settled"])
        self.assertEqual(w["settle_s"], 60)

    def test_tolerance_boundary(self):
        # busy within 5 points, throttle within max(10, 25% of 40) = 10
        w = load.settle(self.BASE, self.sampler([(42, 50)]), window_s=5, max_s=60)
        self.assertEqual((w["settled"], w["settle_s"]), (True, 5))
        w = load.settle(self.BASE, self.sampler([(42, 50.5), (42, 50)]), window_s=5, max_s=60)
        self.assertEqual(w["settle_s"], 10)
        w = load.settle(self.BASE, self.sampler([(42.5, 40), (42, 40)]), window_s=5, max_s=60)
        self.assertEqual(w["settle_s"], 10)

    def test_unmeasured_baseline_metric_is_not_waited_on(self):
        w = load.settle({"busy_pct": 37.0, "throttle_per_min": None}, self.sampler([(37, 999)]),
                        window_s=5, max_s=60)
        self.assertTrue(w["settled"])

    def test_matched_reports_unsettled_pre_windows(self):
        ws = [pre("A"), dict(pre("B"), settled=False), during("A"), during("B")]
        r = load.matched(ws)
        self.assertTrue(r["matched"], r)   # the covariates decide; a timed-out gap is reported
        self.assertEqual(r["unsettled"], {"B": 1})


if __name__ == "__main__":
    unittest.main()
