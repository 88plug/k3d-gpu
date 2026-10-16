#!/usr/bin/env python3
"""Unit tests for scripts/k3d-gpu-sampler against fake sysfs/procfs trees.

Usage: python3 tests/sampler_test.py
"""

import importlib.machinery
import importlib.util
import io
import json
import math
import os
import random
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader("sampler", os.path.join(HERE, "..", "scripts", "k3d-gpu-sampler"))
_spec = importlib.util.spec_from_loader("sampler", _loader)
S = importlib.util.module_from_spec(_spec)
_loader.exec_module(S)

# Shape copied from a live i915 client on the reference host (gnome-shell):
# three fds share one drm-client-id, so de-duplication matters.
I915_FDINFO = """pos:\t0
flags:\t02100002
drm-driver:\ti915
drm-client-id:\t28
drm-pdev:\t0000:00:02.0
drm-engine-render:\t%d ns
drm-engine-copy:\t0 ns
drm-engine-video:\t0 ns
drm-engine-capacity-video:\t2
drm-engine-video-enhance:\t0 ns
"""

XE_FDINFO = """drm-driver:\txe
drm-client-id:\t7
drm-pdev:\t0000:03:00.0
drm-cycles-rcs:\t%d
drm-total-cycles-rcs:\t%d
drm-cycles-ccs:\t0
drm-total-cycles-ccs:\t%d
"""


def write(root, path, text):
    full = root + path
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(text)


class Readers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_rapl_wraparound(self):
        for i, (name, rng) in enumerate([("package-0", 1000), ("core", 1000)]):
            write(self.root, "/sys/class/powercap/intel-rapl:0:%d/name" % i, name + "\n")
            write(self.root, "/sys/class/powercap/intel-rapl:0:%d/max_energy_range_uj" % i, "%d\n" % rng)
            write(self.root, "/sys/class/powercap/intel-rapl:0:%d/energy_uj" % i, "900\n")
        doms = S.rapl_domains(self.root)
        before = S.rapl_read(doms)
        write(self.root, "/sys/class/powercap/intel-rapl:0:0/energy_uj", "100\n")   # wrapped
        write(self.root, "/sys/class/powercap/intel-rapl:0:1/energy_uj", "950\n")
        d = S.rapl_delta_j(before, S.rapl_read(doms), doms)
        self.assertAlmostEqual(d["package-0"], 201e-6)
        self.assertAlmostEqual(d["core"], 50e-6)

    def test_fdinfo_dedup_and_pod_scope(self):
        # pod pid 10 holds client 28 on three fds; neighbour pid 20 holds client 29.
        for fd in (42, 44, 46):
            write(self.root, "/proc/10/fdinfo/%d" % fd, I915_FDINFO % 5_000_000_000)
        write(self.root, "/proc/20/fdinfo/3", (I915_FDINFO % 9_000_000_000).replace("\t28", "\t29"))
        write(self.root, "/proc/30/fdinfo/1", "pos:\t0\nflags:\t0\n")   # not a DRM fd
        pod = S.drm_clients({10}, self.root)
        self.assertEqual(list(pod), [("0000:00:02.0", "28")])
        self.assertEqual(len(S.drm_clients(None, self.root)), 2)
        rec = pod[("0000:00:02.0", "28")]
        self.assertEqual(rec["engines_ns"], {"render": 5_000_000_000, "copy": 0, "video": 0, "video-enhance": 0})

    def test_gpu_busy_i915_and_xe(self):
        b = {("a", "1"): S.parse_fdinfo(I915_FDINFO % 1_000_000_000),
             ("b", "7"): S.parse_fdinfo(XE_FDINFO % (100, 1000, 1000))}
        a = {("a", "1"): S.parse_fdinfo(I915_FDINFO % 3_500_000_000),
             ("b", "7"): S.parse_fdinfo(XE_FDINFO % (600, 2000, 2000))}
        # i915: 2.5 s render; xe: 500/1000 of a 4 s window = 2 s.
        self.assertAlmostEqual(S.gpu_busy_s(b, a, 4.0), 4.5)

    def test_gpu_busy_ignores_backward_step_and_new_client(self):
        b = {("a", "1"): S.parse_fdinfo(I915_FDINFO % 2_000_000_000)}
        a = {("a", "1"): S.parse_fdinfo(I915_FDINFO % 1_000_000_000),
             ("a", "2"): S.parse_fdinfo(I915_FDINFO % 9_000_000_000)}
        self.assertEqual(S.gpu_busy_s(b, a, 1.0), 0.0)

    def test_cgroup_pids_recursive(self):
        write(self.root, "/cg/pod/cgroup.procs", "")
        write(self.root, "/cg/pod/c1/cgroup.procs", "10\n11\n")
        write(self.root, "/cg/pod/c2/cgroup.procs", "12\n")
        self.assertEqual(S.cgroup_pids(self.root + "/cg/pod"), {10, 11, 12})

    def test_npu_busy_and_holders(self):
        self.assertEqual(S.npu_nodes(self.root), {})     # reference host: no NPU, feature off
        write(self.root, "/sys/class/accel/accel0/device/npu_busy_time_us", "1234\n")
        self.assertIn("accel0", S.npu_nodes(self.root))
        os.makedirs(self.root + "/proc/10/fd")
        os.symlink("/dev/accel/accel0", self.root + "/proc/10/fd/5")
        os.makedirs(self.root + "/proc/11/fd")
        os.symlink("/dev/dri/renderD128", self.root + "/proc/11/fd/5")
        self.assertEqual(S.accel_holders("accel0", self.root), {10})

    def test_perf_csv(self):
        out = ("12345678;;cpu_core/cycles/;kubepods/pod1;1000;100.00;;\n"
               "<not counted>;;cpu_atom/cycles/;kubepods/pod1;0;0.00;;\n")
        self.assertEqual(S.parse_perf_csv(out), {"pcore": 12345678, "ecore": 0})
        cmd = S.perf_cmd("kubepods/pod1", 5)
        self.assertIn("cpu_core/cycles/,cpu_atom/cycles/", cmd)
        self.assertIn("kubepods/pod1,kubepods/pod1", cmd)

    def test_cpu_busy_split(self):
        # equal cycles, P clock twice E clock -> E took twice the time
        got = S.cpu_busy_s({"pcore": 4e9, "ecore": 4e9}, 3.0, {"pcore": 4.0, "ecore": 2.0})
        self.assertAlmostEqual(got["pcore"], 1.0)
        self.assertAlmostEqual(got["ecore"], 2.0)

    def test_type_fmax(self):
        write(self.root, "/sys/bus/event_source/devices/cpu_core/cpus", "0-15\n")
        write(self.root, "/sys/bus/event_source/devices/cpu_atom/cpus", "16-31\n")
        write(self.root, "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "5300000\n")
        write(self.root, "/sys/devices/system/cpu/cpu16/cpufreq/cpuinfo_max_freq", "3900000\n")
        self.assertEqual(S.type_fmax_ghz(self.root), {"pcore": 5.3, "ecore": 3.9})


class SampleCommand(unittest.TestCase):
    """`sample` end to end on a fake node: perf is stubbed and advances the counters."""

    def run_sample(self, claim, gpu_ns_after, perf_out):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        r = tmp.name
        write(r, "/sys/fs/cgroup/kubepods/pod1/c/cgroup.procs", "10\n")
        write(r, "/sys/fs/cgroup/kubepods/pod1/cpu.stat", "usage_usec 1000000\n")
        write(r, "/proc/10/fdinfo/3", I915_FDINFO % 1_000_000_000)
        write(r, "/sys/class/powercap/intel-rapl:0:1/name", "uncore\n")
        write(r, "/sys/class/powercap/intel-rapl:0:1/max_energy_range_uj", "262143328850\n")
        write(r, "/sys/class/powercap/intel-rapl:0:1/energy_uj", "5000000\n")
        write(r, "/sys/bus/event_source/devices/cpu_core/cpus", "0-15\n")
        write(r, "/sys/bus/event_source/devices/cpu_atom/cpus", "16-31\n")
        write(r, "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "5100000\n")
        write(r, "/sys/devices/system/cpu/cpu16/cpufreq/cpuinfo_max_freq", "3900000\n")

        def fake_run(cmd, **kw):
            self.assertEqual(cmd[:2], ["perf", "stat"])
            write(r, "/proc/10/fdinfo/3", I915_FDINFO % gpu_ns_after)
            write(r, "/sys/fs/cgroup/kubepods/pod1/cpu.stat", "usage_usec 3000000\n")
            write(r, "/sys/class/powercap/intel-rapl:0:1/energy_uj", "9000000\n")
            return type("P", (), {"stdout": "", "stderr": perf_out})()

        old_root, old_run = S.SYSROOT, S.subprocess.run
        S.SYSROOT, S.subprocess.run = r, fake_run
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = S.main(["sample", "--cgroup", "kubepods/pod1", "--claim", claim, "--window", "10"])
        finally:
            S.SYSROOT, S.subprocess.run = old_root, old_run
        return rc, json.loads(buf.getvalue())

    def test_gpu_stage_proven(self):
        rc, rep = self.run_sample("igpu", 7_000_000_000, "1000;;cpu_core/cycles/;kubepods/pod1;1;100;;\n")
        self.assertEqual((rc, rep["verdict"]), (0, "PROVEN"))
        self.assertAlmostEqual(rep["busy_s"]["igpu"], 6.0)
        self.assertAlmostEqual(rep["igpu_joules"]["pod_j"], 4.0)

    def test_gpu_stage_fallback_and_no_npu(self):
        rc, rep = self.run_sample("igpu", 1_000_000_000, "9000000000;;cpu_core/cycles/;kubepods/pod1;1;100;;\n")
        self.assertEqual((rc, rep["verdict"]), (3, "FALLBACK"))
        rc, rep = self.run_sample("npu", 1_000_000_000, "9000000000;;cpu_core/cycles/;kubepods/pod1;1;100;;\n")
        self.assertEqual(rep["verdict"], "FALLBACK")   # node has no NPU at all


class Verdict(unittest.TestCase):
    W = 10.0

    def v(self, claim, **busy):
        return S.verdict(claim, busy, self.W)

    def test_accel(self):
        self.assertEqual(self.v("igpu", igpu=6, pcore=1, ecore=0), "PROVEN")
        self.assertEqual(self.v("igpu", igpu=0, pcore=8, ecore=2), "FALLBACK")
        self.assertEqual(self.v("igpu", igpu=0.5, pcore=8, ecore=2), "FALLBACK")   # init blip only
        self.assertEqual(self.v("igpu", igpu=3, pcore=5, ecore=0), "AMBIGUOUS")    # partial offload
        self.assertEqual(self.v("igpu", igpu=0, pcore=0, ecore=0), "IDLE")

    def test_cores(self):
        self.assertEqual(self.v("ecore", pcore=0.1, ecore=4, igpu=0), "PROVEN")
        self.assertEqual(self.v("ecore", pcore=4, ecore=1, igpu=0), "FALLBACK")
        self.assertEqual(self.v("ecore", pcore=1.5, ecore=3, igpu=0), "AMBIGUOUS")

    def test_unverifiable_when_counter_missing(self):
        self.assertEqual(self.v("npu", pcore=1, ecore=1, igpu=0), "UNVERIFIABLE")

    def test_no_ceiling_ablation_passes_partial(self):
        self.assertEqual(S.verdict("igpu", {"igpu": 3, "pcore": 5, "ecore": 0}, self.W, ceiling=False), "PROVEN")

    def test_cli(self):
        import sys
        inp = io.StringIO(json.dumps({"claim": "igpu", "window_s": 10, "busy_s": {"igpu": 0, "pcore": 5, "ecore": 0}}))
        old, sys.stdin = sys.stdin, inp
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = S.main(["verdict"])
        finally:
            sys.stdin = old
        self.assertEqual((rc, buf.getvalue().strip()), (3, "FALLBACK"))


class Joules(unittest.TestCase):
    def test_igpu_split(self):
        got = S.igpu_joules(10.0, pod_busy_s=3.0, all_busy_s=4.0, floor_w=0.2, window_s=10.0)
        self.assertAlmostEqual(got["pod_j"], 6.0)
        self.assertAlmostEqual(got["unattributed_j"], 4.0)
        self.assertEqual(S.igpu_joules(1.0, 0, 0, 0.0, 1.0)["pod_j"], 0.0)

    def test_lstsq_exact(self):
        rows = [[1, x, x * x] for x in range(6)]
        y = [2 + 3 * x - 0.5 * x * x for x in range(6)]
        for got, want in zip(S.lstsq(rows, y), [2, 3, -0.5]):
            self.assertAlmostEqual(got, want, places=8)
        with self.assertRaises(ValueError):
            S.lstsq([[1, 2], [2, 4]], [1, 2])

    def test_vif(self):
        self.assertAlmostEqual(S.vif_pair([1, 2, 3, 4], [1, 3, 2, 4]), 1 / (1 - 0.64))
        self.assertEqual(S.vif_pair([1, 2, 3], [2, 4, 6]), math.inf)

    def _calib(self, a, bp, be, idle, collinear=False):
        rows = []
        for fp in (2.0, 3.0, 4.0, 5.0):
            for fe in (1.5, 2.5, 3.5):
                for cp, ce in ((8.0, 0.0), (0.0, 6.0), (4.0, 3.0)):
                    if collinear:   # always co-active at a fixed 4:3 ratio
                        cp, ce = 4.0 * fe, 3.0 * fe
                    w = {"window_s": 2.0, "cyc": {"pcore": cp, "ecore": ce}, "f": {"pcore": fp, "ecore": fe}}
                    g = S.rail_g(S.rail_freq(w), a)
                    w["pp0_j"] = idle * 2.0 + (bp * cp + be * ce) * g
                    rows.append(w)
        return rows

    def test_core_fit_recovers_and_attributes(self):
        m = S.fit_core_model(self._calib(a=1.0, bp=0.12, be=0.07, idle=0.8))
        self.assertAlmostEqual(m["a"], 1.0)
        self.assertAlmostEqual(m["beta"]["pcore"], 0.12, places=6)
        self.assertLess(m["vif"], 10)
        win = {"window_s": 1.0, "f": {"pcore": 5.0, "ecore": 3.0}, "cyc": {"pcore": 5.0, "ecore": 3.0}}
        win["pp0_j"] = 0.8 + (0.12 * 5 + 0.07 * 3) * S.rail_g(5.0, 1.0)
        out = S.attribute_cores(m, win, {"llm": {"pcore": 5.0}, "embed": {"ecore": 3.0}})
        self.assertAlmostEqual(out["embed"]["ecore_j"], 0.07 * 3 * 36, places=5)
        self.assertAlmostEqual(out["embed"]["ecore_rail_coupling_j"], 0.07 * 3 * (36 - 16), places=5)
        self.assertAlmostEqual(out["llm"]["pcore_rail_coupling_j"], 0.0, places=6)
        self.assertAlmostEqual(out["_unattributed_j"], 0.0, places=5)

    def test_core_fit_refuses_collinear(self):
        m = S.fit_core_model(self._calib(a=1.0, bp=0.12, be=0.07, idle=0.8, collinear=True))
        win = {"window_s": 1.0, "pp0_j": 10.0, "f": {"pcore": 5.0, "ecore": 3.0}, "cyc": {"pcore": 1.0, "ecore": 1.0}}
        out = S.attribute_cores(m, win, {"x": {"pcore": 1.0, "ecore": 1.0}})
        self.assertIn("refused", out)
        self.assertNotIn("x", out)


class Sentinel(unittest.TestCase):
    def test_tail_features_closed_form(self):
        # Poisson gaps at 1/s with a 0.5 s timer: E[min(gap, 0.5)] = 1 - e^-0.5, P(gap > 0.5) = e^-0.5
        got = S._tail_features(1.0, 0.5)
        for g, w in zip(got, [1.0, 1 - math.exp(-0.5), math.exp(-0.5)]):
            self.assertAlmostEqual(g, w)
        rng = random.Random(3)
        gaps = [rng.expovariate(1.0) for _ in range(200000)]
        self.assertAlmostEqual(sum(min(g, 0.5) for g in gaps) / len(gaps), got[1], places=2)

    def test_fit_recovers_poisson_model(self):
        truth = {"tail": True, "e_active": 0.006, "p_tail": 0.15, "e_wake": 0.010, "tau": S.TAU_GRID[20]}
        pts = [(r, S.predict(truth, r)) for r in (0.1, 0.2, 0.5, 1, 2, 5, 10)]
        fit = S.fit_sentinel(pts)
        for r in (0.1, 1, 10):
            self.assertAlmostEqual(S.predict(fit, r) / S.predict(truth, r), 1.0, places=3)

    def test_crossover_and_place(self):
        ecore = {"tail": True, "e_active": 0.020, "p_tail": 0.0, "e_wake": 0.0, "tau": 0.01, "rmse_rel": 0.02}
        igpu = {"tail": True, "e_active": 0.005, "p_tail": 0.1, "e_wake": 0.02, "tau": 0.3, "rmse_rel": 0.02}
        xs = S.crossover(ecore, igpu, 0.1, 10.0)
        self.assertEqual(len(xs), 1)
        self.assertAlmostEqual(S.predict(ecore, xs[0]), S.predict(igpu, xs[0]), places=6)
        self.assertEqual(S.place({"ecore": ecore, "igpu": igpu}, 0.1, "igpu")[0], "ecore")
        self.assertEqual(S.place({"ecore": ecore, "igpu": igpu}, 10.0, "ecore")[0], "igpu")
        noisy = dict(ecore, rmse_rel=0.9)
        self.assertEqual(S.place({"ecore": noisy, "igpu": igpu}, 0.1, "igpu"), ("igpu", "indistinguishable"))

    def test_measured_error_floors_gate(self):
        pts = [(r, 0.02, 0.006) for r in (0.1, 1, 10)]      # flat curve, 30% repeat error
        self.assertAlmostEqual(S.fit_sentinel(pts)["rmse_rel"], 0.3)

    def test_no_tail_ablation_is_rate_blind(self):
        fit = S.fit_sentinel([(0.1, 0.05), (10, 0.01)], tail=False)
        self.assertEqual(S.predict(fit, 0.1), S.predict(fit, 10))


if __name__ == "__main__":
    random.seed(0)
    unittest.main(verbosity=1)
