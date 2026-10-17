#!/usr/bin/env python3
"""Unit tests for bench/load/load.py (no docker, no root, fake sysfs/procfs)."""
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


if __name__ == "__main__":
    unittest.main()
