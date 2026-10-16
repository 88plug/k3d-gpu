#!/usr/bin/env python3
"""Unit tests for scripts/k3d-gpu-witness. No cluster, no GPU, no root.

Usage: tests/witness_test.py
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "k3d-gpu-witness")
W = SourceFileLoader("witness", SCRIPT).load_module()
FIX = os.path.join(HERE, "fixtures", "witness")
RULE = json.load(open(os.path.join(HERE, "..", "share", "witness", "rule.json")))
TCK = W.CLK_TCK


def fx(name):
    return open(os.path.join(FIX, name)).read()


def i915(cid, ns, pdev="0000:00:02.0"):
    return ("drm-driver:\ti915\ndrm-client-id:\t%d\ndrm-pdev:\t%s\n"
            "drm-engine-render:\t%d ns\ndrm-engine-copy:\t0 ns\n" % (cid, pdev, ns))


def stat(pid, ticks):
    return "%d (x y) S " % pid + " ".join(["0"] * 10 + [str(ticks), "0"] + ["0"] * 30)


def snap(t_s, fdinfo, cpu):
    return {"t_ns": int(t_s * 1e9), "fdinfo": fdinfo, "nvfd": {}, "pmon": None,
            "stat": {str(p): stat(p, int(c * TCK)) for p, c in cpu.items()}}


def gpu_run(busy_frac=0.8, engine_cores=1.0, tool_cores=0.0, n=20, dt=0.1):
    """Engine pid 10 on 0000:00:02.0 (two dup'd fds), tool pid 20 without a GPU fd."""
    snaps = []
    for i in range(n + 1):
        t = i * dt
        ns = int(busy_frac * t * 1e9)
        snaps.append(snap(t, {"10/5": i915(7, ns), "10/6": i915(7, ns)},
                          {10: engine_cores * t, 20: tool_cores * t}))
    return snaps


class Parsers(unittest.TestCase):
    def test_i915_host_fixture(self):
        c = W.parse_fdinfo(fx("i915-host-client28-fd42.txt"))
        self.assertEqual((c["driver"], c["client"], c["pdev"]), ("i915", 28, "0000:00:02.0"))
        self.assertEqual(c["engines"]["render"]["ns"], 823660187741612)
        self.assertEqual(c["engines"]["video"]["cap"], 2)
        self.assertNotIn("compute", c["engines"])  # UHD 770 on i915 has no compute engine

    def test_xe_kernel_doc_example(self):
        c = W.parse_fdinfo(fx("xe-kernel-doc-example.txt"))
        self.assertEqual(c["driver"], "xe")
        self.assertEqual(c["engines"]["rcs"], {"cycles": 28257900, "total": 7655183225})
        self.assertEqual(c["engines"]["ccs"], {"cycles": 0, "total": 7655183225, "cap": 4})

    def test_non_drm_fdinfo(self):
        self.assertIsNone(W.parse_fdinfo("pos:\t0\nflags:\t02\nmnt_id:\t25\n"))

    def test_pmon_host_fixture(self):
        rows = W.parse_pmon(fx("nvidia-pmon-host.txt"))
        self.assertEqual(rows[(0, 3812011)], None)  # "-": no sample in the window
        self.assertIn((1, 2465), rows)

    def test_proc_stat_host_fixture(self):
        line = fx("proc-stat-host.txt")
        rest = line[line.rindex(")") + 2:].split()
        self.assertEqual(W.parse_stat(line), int(rest[11]) + int(rest[12]))

    def test_proc_stat_comm_with_spaces_and_parens(self):
        self.assertEqual(W.parse_stat("5 (a) b) S " + " ".join(["0"] * 10 + ["7", "3"] + ["0"] * 30)), 10)


class Reduce(unittest.TestCase):
    def test_dup_fds_counted_once(self):
        iv, pids = W.intervals(gpu_run(busy_frac=0.6), "0000:00:02.0", RULE)
        self.assertEqual(pids, ["10"])
        for x in iv:
            self.assertAlmostEqual(x["busy"] / x["dt"], 0.6, places=3)

    def test_counter_regression_is_held(self):
        s = gpu_run(busy_frac=0.2, n=4)
        s[2]["fdinfo"] = {"10/5": i915(7, 1)}  # driver reports a lower value once
        iv, _ = W.intervals(s, "0000:00:02.0", RULE)
        self.assertAlmostEqual(sum(x["busy"] for x in iv), 0.2 * 0.4, places=4)

    def test_xe_cycles_to_busy(self):
        def xe(cyc, tot):
            return ("drm-driver:\txe\ndrm-client-id:\t3\ndrm-pdev:\t0000:03:00.0\n"
                    "drm-cycles-ccs:\t%d\ndrm-total-cycles-ccs:\t%d\ndrm-engine-capacity-ccs:\t4\n" % (cyc, tot))
        s = [snap(0, {"1/3": xe(0, 1000)}, {1: 0}), snap(0.1, {"1/3": xe(300, 2000)}, {1: 0})]
        iv, _ = W.intervals(s, "0000:03:00.0", RULE)
        self.assertAlmostEqual(iv[0]["busy"], 0.3 * 0.1, places=6)


class Rule(unittest.TestCase):
    def verdict(self, snaps, rule=RULE, claim="0000:00:02.0"):
        return W.evaluate(snaps, claim, rule)

    def test_clean_memory_bound_passes(self):
        self.assertEqual(self.verdict(gpu_run(busy_frac=0.3))["verdict"], "PASS")

    def test_full_fallback_fails(self):
        v = self.verdict(gpu_run(busy_frac=0.0, engine_cores=6.0))
        self.assertEqual(v["verdict"], "FAIL")
        self.assertTrue(any("engine busy" in r for r in v["reasons"]))

    def test_partial_fallback_fails(self):
        v = self.verdict(gpu_run(busy_frac=0.5, engine_cores=4.0))
        self.assertEqual(v["verdict"], "FAIL")
        self.assertTrue(any("CPU compute share" in r for r in v["reasons"]))

    def test_tool_process_cpu_not_counted(self):
        self.assertEqual(self.verdict(gpu_run(busy_frac=0.5, tool_cores=5.0))["verdict"], "PASS")
        pod = dict(RULE, cpu_scope="pod")
        self.assertEqual(self.verdict(gpu_run(busy_frac=0.5, tool_cores=5.0), pod)["verdict"], "FAIL")

    def test_busy_on_other_device_fails(self):
        s = gpu_run(busy_frac=0.0)
        for i, x in enumerate(s):  # holds the claimed device, works on another one
            x["fdinfo"]["10/9"] = i915(8, int(0.7 * i * 0.1 * 1e9), pdev="0000:03:00.0")
        v = self.verdict(s)
        self.assertEqual(v["verdict"], "FAIL")
        self.assertTrue(any("another device" in r for r in v["reasons"]), v)

    def test_wrong_device_fails(self):
        s = gpu_run(busy_frac=0.7)
        v = self.verdict(s, claim="0000:03:00.0")
        self.assertEqual(v["verdict"], "FAIL")
        self.assertIn("no process holds a client on 0000:03:00.0", v["reasons"])

    def test_nvidia_needs_pidmap(self):
        s = gpu_run()
        for x in s:
            x["fdinfo"] = {}
            x["pmon"] = "    0     900010     C     50      -      -      -      -      -    llama"
            x["nvfd"] = {"10": [0]}
        self.assertEqual(self.verdict(s, claim="nvidia:0")["verdict"], "INCONCLUSIVE")
        v = W.evaluate(s, "nvidia:0", RULE, {"900010": "10"})
        self.assertEqual(v["verdict"], "PASS", v)


class Verify(unittest.TestCase):
    def bundle(self):
        return W.make_bundle(RULE, "0000:00:02.0", gpu_run(busy_frac=0.6))

    def test_untouched_bundle(self):
        code, rep = W.verify(self.bundle(), W.rule_sha256(RULE))
        self.assertEqual((code, rep["verdict"]), (0, "PASS"))

    def test_bundle_owns_its_rule(self):
        r = copy.deepcopy(RULE)
        b = W.make_bundle(r, "0000:00:02.0", gpu_run(busy_frac=0.6))
        r["s_max"] = 0.9
        self.assertEqual(W.verify(b)[0], 0)

    def test_rule_edited_after_run(self):
        b = self.bundle()
        b["rule"]["s_max"] = 0.9
        self.assertEqual(W.verify(b)[0], 3)

    def test_rule_and_hash_both_replaced(self):
        b = self.bundle()
        b["rule"] = dict(RULE, s_max=0.9)
        b["rule_sha256"] = W.rule_sha256(b["rule"])
        self.assertEqual(W.verify(b)[0], 0)  # self-consistent, so only the pre-registered hash catches it
        self.assertEqual(W.verify(b, W.rule_sha256(RULE))[0], 3)

    def test_recorded_verdict_edited(self):
        b = W.make_bundle(RULE, "0000:00:02.0", gpu_run(busy_frac=0.0, engine_cores=6))
        b["verdict"] = {"verdict": "PASS", "reasons": [], "stats": {}}
        self.assertEqual(W.verify(b)[0], 3)

    def test_raw_counters_edited(self):
        b = self.bundle()
        b["snapshots"][-1]["fdinfo"] = {}
        for s in b["snapshots"]:
            s["stat"]["10"] = stat(10, int(s["t_ns"] / 1e9 * 6 * TCK))
        self.assertEqual(W.verify(b)[0], 3)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.json")
            b = self.bundle()
            json.dump(b, open(p, "w"))
            r = subprocess.run([sys.executable, SCRIPT, "verify", p, "--expect-rule-sha256", W.rule_sha256(RULE)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            b["rule"]["c0_cores"] = 8
            json.dump(b, open(p, "w"))
            r = subprocess.run([sys.executable, SCRIPT, "verify", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 3)


class Manifest(unittest.TestCase):
    def test_sidecar_pins_the_shipped_rule(self):
        text = open(os.path.join(HERE, "..", "share", "witness", "witness-sidecar.yml")).read()
        self.assertIn(W.rule_sha256(RULE), text)


class Collect(unittest.TestCase):
    def test_fake_proc_tree(self):
        with tempfile.TemporaryDirectory() as root:
            def proc(pid, ticks, fds):
                base = os.path.join(root, str(pid))
                os.makedirs(os.path.join(base, "fd"))
                os.makedirs(os.path.join(base, "fdinfo"))
                open(os.path.join(base, "stat"), "w").write(stat(pid, ticks))
                for fd, (target, info) in fds.items():
                    os.symlink(target, os.path.join(base, "fd", str(fd)))
                    open(os.path.join(base, "fdinfo", str(fd)), "w").write(info)
            proc(10, 50, {5: ("/dev/dri/renderD128", fx("i915-host-client28-fd42.txt")),
                          6: ("/dev/dri/renderD128", fx("i915-host-client28-fd44.txt")),
                          7: ("/dev/nvidia1", "pos:\t0\n"),
                          8: ("/tmp/x", "pos:\t0\n")})
            proc(20, 9, {})
            os.makedirs(os.path.join(root, "self"))
            s = W.collect(root)
            self.assertEqual(sorted(s["fdinfo"]), ["10/5", "10/6"])
            self.assertEqual(s["nvfd"], {"10": [1]})
            self.assertEqual(sorted(s["stat"]), ["10", "20"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
