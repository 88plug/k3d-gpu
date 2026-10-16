#!/usr/bin/env python3
"""Tests for bench/witness.py and bench/witness_sim.py.

Run: python3 -m unittest bench/test_witness.py
"""
import copy
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import witness as wt  # noqa: E402
import witness_sim as sim  # noqa: E402

RULES = {"version": 1, "max_foreign_share": 0.02, "max_sibling_foreign_share": 0.05,
         "min_core_type_residency": 0.98, "min_coverage": 0.95}
# 2 P-cores with SMT (CPUs 0-3), 2 E-cores (4-5).
TOPO = {"p": "0-3", "e": "4-5",
        "siblings": {"0": "0-1", "1": "0-1", "2": "2-3", "3": "2-3", "4": "4", "5": "5"}}


def put(path, text):
    with open(path, "w") as f:
        f.write(text)


def stat_text(busy_total):
    lines = ["cpu 0 0 0 0 0 0 0 0 0 0"]
    for c, (b, t) in sorted(busy_total.items()):
        # user, nice, system, idle, iowait, irq, softirq, steal
        lines.append(f"cpu{c} {b - 2} 0 1 {t - b - 1} 1 1 0 0 0 0")
    return "\n".join(lines) + "\n"


def perf_line(cpu, ms, cgroup):
    return json.dumps({"cpu": str(cpu), "counter-value": f"{ms:f}", "unit": "msec",
                       "cgroup": cgroup, "event": "cpu-clock"})


def sample(own_ms, busy_ticks, usage_usec, win_ms=1000.0, cpus=range(6)):
    """One raw window: 100 ticks per CPU, given per-CPU busy ticks and pod msec."""
    s0 = {c: (1000, 5000) for c in cpus}
    s1 = {c: (1000 + busy_ticks.get(c, 0), 5100) for c in cpus}
    perf = []
    for c in (0, 2, 1, 3):
        perf.append(perf_line(c, own_ms.get(c, 0.0), "kubepods/pod-a"))
        perf.append(perf_line(c, win_ms, ""))
    return {"user_hz": 100, "perf_json": perf, "proc_stat_0": stat_text(s0),
            "proc_stat_1": stat_text(s1), "cpu_stat_0": "usage_usec 500\nuser_usec 1\n",
            "cpu_stat_1": f"usage_usec {500 + usage_usec}\nuser_usec 1\n"}


class Parsing(unittest.TestCase):
    def test_cpulist_roundtrip(self):
        self.assertEqual(wt.parse_cpulist("0-3,8,10-11\n"), [0, 1, 2, 3, 8, 10, 11])
        self.assertEqual(wt.format_cpulist([11, 0, 1, 2, 3, 8, 10]), "0-3,8,10-11")
        self.assertEqual(wt.parse_cpulist(""), [])

    def test_proc_stat_busy_excludes_idle_and_iowait(self):
        got = wt.parse_proc_stat("cpu 9 9 9 9 9 9 9 9\ncpu3 10 1 2 100 7 3 4 5 0 0\n")
        self.assertEqual(got, {3: (10 + 1 + 2 + 3 + 4 + 5, 25 + 107)})

    def test_perf_split_own_and_window(self):
        lines = ["", "# comment", perf_line(0, 400.5, "cg"), perf_line(0, 1000.0, ""),
                 json.dumps({"cpu": "1", "counter-value": "<not counted>", "cgroup": "cg",
                             "event": "cpu-clock"}),
                 json.dumps({"cpu": "1", "counter-value": "7", "cgroup": "", "event": "cycles"})]
        own, win = wt.parse_perf(lines)
        self.assertEqual(own, {0: 400.5, 1: 0.0})
        self.assertEqual(win, {0: 1000.0})

    def test_perf_unsupported_counter_fails(self):
        bad = json.dumps({"cpu": "0", "counter-value": "<not supported>", "cgroup": "cg",
                          "event": "cpu-clock"})
        with self.assertRaises(ValueError):
            wt.parse_perf([bad])

    def test_cpu_stat(self):
        self.assertEqual(wt.parse_cpu_stat("usage_usec 42\nuser_usec 1\n"), 42)
        with self.assertRaises(ValueError):
            wt.parse_cpu_stat("user_usec 1\n")


class Metrics(unittest.TestCase):
    def test_hand_computed_window(self):
        # Pod on S={0,2}. CPU 0: busy 100/100, pod 900 ms -> foreign 0.10.
        # CPU 2: busy 100, pod 1000 -> 0. Siblings 1,3 busy 40 and 0 -> 0.20.
        raw = sample({0: 900.0, 2: 1000.0}, {0: 100, 2: 100, 1: 40, 3: 0}, 1_900_000)
        m = wt.compute_metrics(raw, TOPO, [0, 2])
        self.assertEqual(m, {"foreign_share": 0.05, "sibling_share": 0.2,
                             "p_residency": 1.0, "coverage": 1.0})

    def test_window_length_mismatch_cancels(self):
        # perf window 990 ms against a 1 s /proc/stat window: fractions still agree.
        raw = sample({0: 990.0, 2: 990.0}, {0: 100, 2: 100}, 2_000_000, win_ms=990.0)
        m = wt.compute_metrics(raw, TOPO, [0, 2])
        self.assertEqual((m["foreign_share"], m["coverage"]), (0.0, 1.0))

    def test_escape_from_balloon_lowers_coverage(self):
        # cgroup used 3 CPUs' worth but only 2 ran on the monitored CPUs.
        raw = sample({0: 1000.0, 2: 1000.0}, {0: 100, 2: 100}, 3_000_000)
        self.assertAlmostEqual(wt.compute_metrics(raw, TOPO, [0, 2])["coverage"], 0.666667)

    def test_siblings(self):
        self.assertEqual(wt.sibling_cpus([0, 2], TOPO), [1, 3])
        self.assertEqual(wt.sibling_cpus([0, 1], TOPO), [])
        self.assertEqual(wt.sibling_cpus([4], TOPO), [])

    def test_judge(self):
        ok = {"foreign_share": 0.0, "sibling_share": 0.0, "p_residency": 1.0, "coverage": 1.0}
        self.assertEqual(wt.judge(ok, RULES, "performance"), {"accept": True, "reasons": []})
        self.assertEqual(wt.judge(ok, RULES, "efficient")["reasons"], ["core_type_residency"])
        bad = dict(ok, foreign_share=0.03, sibling_share=0.06, coverage=0.5)
        self.assertEqual(wt.judge(bad, RULES, "performance")["reasons"],
                         ["foreign_share", "sibling_share", "coverage"])


class Bundle(unittest.TestCase):
    def setUp(self):
        raws = [sample({0: 1000.0, 2: 1000.0}, {0: 100, 2: 100}, 2_000_000),
                sample({0: 700.0, 2: 1000.0}, {0: 100, 2: 100, 1: 90}, 1_700_000)]
        self.b = wt.build_bundle(raws, TOPO, [0, 2], "kubepods/pod-a", "performance", RULES)
        self.b = json.loads(json.dumps(self.b))  # through disk format

    def test_clean_bundle_verifies(self):
        self.assertEqual(wt.verify_bundle(self.b, wt.rules_sha256(RULES)), [])
        self.assertEqual([s["verdict"]["accept"] for s in self.b["samples"]], [True, False])

    def test_tampered_raw_detected(self):
        b = copy.deepcopy(self.b)
        b["samples"][1]["raw"]["perf_json"][0] = perf_line(0, 1000.0, "kubepods/pod-a")
        self.assertIn("sample 1: metrics do not recompute", wt.verify_bundle(b))

    def test_tampered_verdict_detected(self):
        b = copy.deepcopy(self.b)
        b["samples"][1]["verdict"] = {"accept": True, "reasons": []}
        self.assertEqual(wt.verify_bundle(b), ["sample 1: verdict does not recompute"])

    def test_relaxed_rule_detected(self):
        b = copy.deepcopy(self.b)
        b["rules"]["max_foreign_share"] = 0.5
        self.assertIn("rules_sha256 does not match rules", wt.verify_bundle(b))
        b["rules_sha256"] = wt.rules_sha256(b["rules"])  # re-hashed, but not pre-committed
        probs = wt.verify_bundle(b, wt.rules_sha256(RULES))
        self.assertIn("rules differ from the pre-committed hash", probs)

    def test_rules_must_be_complete(self):
        with self.assertRaises(ValueError):
            wt.build_bundle([], TOPO, [0], "cg", "performance", {"max_foreign_share": 1})

    def test_shipped_rules_hash_is_documented(self):
        here = os.path.dirname(os.path.abspath(__file__))
        rules = json.loads(wt._read(os.path.join(here, "witness-rules.json")))
        doc = wt._read(os.path.join(here, "..", "docs", "interference-witness.md"))
        self.assertIn(wt.rules_sha256(rules), doc)


class Record(unittest.TestCase):
    """record() against a fake sysroot and a fake perf that advances the counters."""

    def test_record_and_verify(self):
        with tempfile.TemporaryDirectory() as root:
            dev = os.path.join(root, "sys/devices")
            os.makedirs(os.path.join(dev, "cpu_core"))
            os.makedirs(os.path.join(dev, "cpu_atom"))
            put(os.path.join(dev, "cpu_core/cpus"), "0-3\n")
            put(os.path.join(dev, "cpu_atom/cpus"), "4-5\n")
            for c, s in TOPO["siblings"].items():
                d = os.path.join(dev, f"system/cpu/cpu{c}/topology")
                os.makedirs(d)
                put(os.path.join(d, "thread_siblings_list"), s + "\n")
            cg = os.path.join(root, "sys/fs/cgroup/kubepods/pod-a")
            os.makedirs(cg)
            os.makedirs(os.path.join(root, "proc"))
            hz = os.sysconf("SC_CLK_TCK")
            put(os.path.join(cg, "cpu.stat"), "usage_usec 0\n")
            put(os.path.join(root, "proc/stat"),
                stat_text({c: (1000, 5000) for c in range(6)}))
            fake = os.path.join(root, "perf")
            argv_log = os.path.join(root, "argv")
            with open(fake, "w") as f:
                f.write(f"""#!/usr/bin/env python3
import json, sys
sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
from test_witness import stat_text, perf_line, put
put({argv_log!r}, json.dumps(sys.argv[1:]))
put({os.path.join(root, 'proc/stat')!r},
    stat_text({{c: (1000 + ({hz} if c in (0, 2) else 0), 5000 + {hz}) for c in range(6)}}))
put({os.path.join(cg, 'cpu.stat')!r}, "usage_usec 2000000\\n")
for c in (0, 1, 2, 3):
    print(perf_line(c, 1000.0 if c in (0, 2) else 0.0, "kubepods/pod-a"), file=sys.stderr)
    print(perf_line(c, 1000.0, ""), file=sys.stderr)
""")
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            topo, raws = wt.record("kubepods/pod-a", [0, 2], 1, 1, sysroot=root, perf=fake)
            self.assertEqual(json.loads(wt._read(argv_log)),
                             ["stat", "-a", "-A", "-j", "-C", "0-3", "-e", "cpu-clock,cpu-clock",
                              "-G", "kubepods/pod-a,", "--", "sleep", "1"])
            self.assertEqual(topo, TOPO)
            b = wt.build_bundle(raws, topo, [0, 2], "kubepods/pod-a", "performance", RULES)
            self.assertEqual(b["samples"][0]["metrics"],
                             {"foreign_share": 0.0, "sibling_share": 0.0,
                              "p_residency": 1.0, "coverage": 1.0})
            self.assertEqual(wt.verify_bundle(b), [])


class Simulation(unittest.TestCase):
    """The pre-registered criteria A1-A6 on a smaller run, plus sim self-checks."""

    @classmethod
    def setUpClass(cls):
        cls.res = sim.run_all(n=800, seed=7)

    def test_criteria(self):
        for k in ("A1", "A2", "A3", "A3b", "A4", "A5", "A6"):
            self.assertTrue(self.res[f"{k}_pass"], k)

    def test_negative_control_is_blind(self):
        # Ring/LLC slowdown from E-core load is not CPU time on S: the witness
        # must not claim to see it. If it did, the sim would be leaking truth.
        nc = self.res["extra_ecore_ring_negative_control"]
        self.assertLess(nc["r2"]["witness"], 0.10)
        self.assertGreater(nc["max_deficit"], 0.03)

    def test_observations_carry_no_truth(self):
        rows = sim.generate(sim.MIX, 4, sim.Params(), seed=3)
        for r in rows:
            self.assertEqual(set(r["raw"]), {"user_hz", "perf_json", "proc_stat_0",
                                             "proc_stat_1", "cpu_stat_0", "cpu_stat_1"})
            for v in r["cgroups"].values():
                self.assertEqual(set(v), {"usage_usec", "cpuset"})


if __name__ == "__main__":
    unittest.main()
