#!/usr/bin/env python3
"""Unit tests for bench/joule-pack/joulepack.py. No root, no cluster, no hardware.

Detection runs against the repo's sysfs fixtures and synthetic trees; the
verdict runs against synthetic probe.sh result dirs built here, one per outcome
row of the H21 pre-registration.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import joulepack as jp  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def fake_sysroot(d, zones, pools):
    for zone, name in zones:
        write(os.path.join(d, "sys/class/powercap", zone, "name"), name + "\n")
        write(os.path.join(d, "sys/class/powercap", zone, "max_energy_range_uj"), "262143328850\n")
        write(os.path.join(d, "sys/class/powercap", zone, "energy_uj"), "1\n")
    for pmu, cpus in pools.items():
        write(os.path.join(d, "sys/devices/cpu_%s/cpus" % pmu), cpus + "\n")
    return d


class Detect(unittest.TestCase):
    def test_cpulist(self):
        self.assertEqual(jp.cpulist("0-3,8,10-11"), [0, 1, 2, 3, 8, 10, 11])
        self.assertEqual(jp.cpulist(""), [])
        self.assertEqual(jp.cpulist("5\n"), [5])

    def test_raptorlake_fixture_package_fallback(self):
        d = jp.discover(os.path.join(FIX, "raptorlake-nvidia", "sysroot"))
        self.assertTrue(d["hybrid"])
        self.assertEqual(d["attribution_domain"], "package")
        self.assertTrue(d["enabled"])
        self.assertEqual(d["pools"]["performance"], list(range(16)))
        self.assertEqual(d["pools"]["efficient"], list(range(16, 32)))
        self.assertEqual(d["default_agent_label"], "efficient")

    def test_hybrid_fixtures(self):
        for name, p_n in (("meteorlake", 12), ("arrowlake-s", 8), ("pantherlake", 4), ("arrowlake-h", 6)):
            d = jp.discover(os.path.join(FIX, name, "sysroot"))
            self.assertTrue(d["hybrid"], name)
            self.assertEqual(len(d["pools"]["performance"]), p_n, name)
        self.assertIn("lowpower", jp.discover(os.path.join(FIX, "arrowlake-h", "sysroot"))["pools"])

    def test_lp_ecores_match_launcher(self):
        # Pools equal scripts/k3d-gpu efficient_cpus/lowpower_cpus on every hybrid fixture.
        launcher = os.path.join(ROOT, "scripts", "k3d-gpu")
        prog = ('SYSROOT="$1"; CPULIST_MAX=65536; '
                'eval "$(sed -n -e "/^cpulist_expand()/,/^}/p" -e "/^cpulist_compress()/,/^}/p" '
                '-e "/^hybrid_cpus()/,/^efficient_cpus()/p" "$2")"; '
                'echo "$(efficient_cpus)"; echo "$(lowpower_cpus)"')
        for name in ("meteorlake", "pantherlake", "arrowlake-h", "arrowlake-s", "raptorlake-nvidia"):
            sysroot = os.path.join(FIX, name, "sysroot")
            out = subprocess.run(["bash", "-c", prog, "_", sysroot, launcher],
                                 capture_output=True, text=True, check=True).stdout.split("\n")
            d = jp.discover(sysroot)
            self.assertEqual(d["pools"].get("efficient", []), jp.cpulist(out[0]), name)
            self.assertEqual(d["pools"].get("lowpower", []), jp.cpulist(out[1]), name)

    def test_xeon_homogeneous(self):
        d = jp.discover(os.path.join(FIX, "xeon-gnr", "sysroot"))
        self.assertFalse(d["hybrid"])
        self.assertFalse(d["enabled"])
        self.assertEqual(d["default_agent_label"], "")

    def test_client_zones_by_name_not_index(self):
        with tempfile.TemporaryDirectory() as t:
            # Index order deliberately unusual: names decide the role.
            fake_sysroot(t, [("intel-rapl:0", "psys"), ("intel-rapl:1", "package-0"),
                             ("intel-rapl:1:0", "core"), ("intel-rapl:1:1", "uncore")],
                         {"core": "0-7", "atom": "8-15"})
            write(os.path.join(t, "sys/class/powercap/intel-rapl-mmio:0/name"), "package-0\n")
            d = jp.discover(t)
            self.assertEqual(d["rapl"]["package"][0]["zone"], "intel-rapl:1")
            self.assertEqual(d["rapl"]["core"][0]["zone"], "intel-rapl:1:0")
            self.assertEqual(len(d["rapl"]["package"]), 1, "mmio zone must not double count")
            self.assertEqual(d["attribution_domain"], "core")
            self.assertTrue(any("negative control" in n for n in d["notes"]))

    def test_empty_tree(self):
        with tempfile.TemporaryDirectory() as t:
            d = jp.discover(t)
            self.assertIsNone(d["attribution_domain"])
            self.assertFalse(d["enabled"])


class Attribute(unittest.TestCase):
    PODS = [{"name": "p", "cpu_s": 1.0, "type": "performance"}, {"name": "e", "cpu_s": 3.0, "type": "efficient"}]

    def test_cputime_is_kepler_share(self):
        j = jp.attribute(100.0, self.PODS)
        self.assertAlmostEqual(j["p"], 25.0)
        self.assertAlmostEqual(j["e"], 75.0)

    def test_typed_weights_correct_bias(self):
        # A P CPU-second costs 3x an E CPU-second: shares become 3:3.
        j = jp.attribute(100.0, self.PODS, "typed", {"performance": 3.0, "efficient": 1.0})
        self.assertAlmostEqual(j["p"], 50.0)
        self.assertAlmostEqual(sum(j.values()), 100.0)

    def test_zero_cpu_and_errors(self):
        self.assertEqual(jp.attribute(10.0, [{"name": "a", "cpu_s": 0.0, "type": "efficient"}]), {"a": 0.0})
        with self.assertRaises(ValueError):
            jp.attribute(1.0, self.PODS, "typed")
        with self.assertRaises(ValueError):
            jp.attribute(1.0, self.PODS, "bogus")

    def test_j_per_cpu_s(self):
        self.assertAlmostEqual(jp.j_per_cpu_s(10.0, 32.0, 64.0), 5.0)
        self.assertTrue(math.isnan(jp.j_per_cpu_s(10.0, 32.0, 0.0)))


class Place(unittest.TestCase):
    def test_keep_incumbent_below_gain(self):
        d = jp.place({"E": {"n": 12, "pkg_w": 10.0, "p95": 0.5}, "X": {"n": 12, "pkg_w": 9.0, "p95": 0.6}})
        self.assertEqual(d["decision"], "keep")
        self.assertEqual(d["label"], "efficient")

    def test_move_above_gain(self):
        d = jp.place({"E": {"n": 12, "pkg_w": 10.0, "p95": 0.5}, "X": {"n": 12, "pkg_w": 8.0, "p95": 0.6}})
        self.assertEqual((d["decision"], d["arm"], d["label"]), ("move", "X", "ecluster"))

    def test_slo_excludes_and_moves_off_failing_incumbent(self):
        d = jp.place({"E": {"n": 12, "pkg_w": 5.0, "p95": 1.4}, "P": {"n": 12, "pkg_w": 20.0, "p95": 0.3}})
        self.assertEqual((d["decision"], d["arm"]), ("move", "P"))

    def test_reject_when_nothing_meets_slo(self):
        d = jp.place({"E": {"n": 12, "pkg_w": 5.0, "p95": 2.0}})
        self.assertEqual(d["decision"], "reject")

    def test_nonpositive_marginal_is_not_feasible(self):
        d = jp.place({"E": {"n": 12, "pkg_w": -0.5, "p95": 0.2}})
        self.assertEqual(d["decision"], "reject")


def fake_run(d, arm_w, rounds=3, lat=None, uncore=0.0, idle_jitter=0.0, cpu=None, bad_cpuset=False,
             core_frac=0.8, turns=None):
    """Synthetic probe.sh output. arm_w: {arm: marginal package W}; idle floor 75 W."""
    lat = lat or {}
    turns = turns or {}
    cpu = cpu or {"performance": 10.0, "efficient": 10.0}
    write(os.path.join(d, "zones.txt"), "intel-rapl:0 package-0 262143328850\nintel-rapl:0:0 core 262143328850\n"
                                        "intel-rapl:0:1 uncore 262143328850\nintel-rapl:1 psys 262143328850\n")
    write(os.path.join(d, "pools.txt"), "performance 0-15\nefficient 16-31\n")
    order = ["UPEXM", "MXEPU", "EUMPX"][:rounds]
    t, e = 1000, [0, 0, 0, 0]
    rapl, wins = [], []

    def run(sec, pkg, unc):
        nonlocal t
        for _ in range(sec):
            core = pkg * core_frac
            for k, w in enumerate((pkg, core, unc, pkg + 20)):
                e[k] += int(w * 1e6)
            t += 1
            rapl.append("E %d.0 %s" % (t, " ".join(map(str, e))))

    idle_i = 0
    for r, arms in enumerate(order, 1):
        for a in arms:
            s = t
            run(12, 75.0 + (idle_jitter if idle_i % 2 else 0.0), 1.0)
            idle_i += 1
            wins.append("W idle %d %d" % (s, t))
            s = t
            run(40, 75.0 + arm_w[a], 1.0 + uncore)
            wins.append("W %s %d %d round=%d" % (a, s, t, r))
            for i in range(1, 13):
                lab = {"U": "none", "P": "performance", "E": "efficient", "X": "ecluster"}.get(a)
                if a == "M":
                    lab = "performance" if i <= 6 else "efficient"
                cs = "0-15" if lab == "performance" else "16-31" if lab != "none" else "0-31"
                if bad_cpuset and a == "E" and i == 1:
                    cs = "0-31"
                l95 = lat.get(a, 0.3)
                lines = ["CPUS %s LAB %s" % (cs, lab), "U0 %d 0" % (s + 1)]
                lines += ["T %d 0.0 %.3f" % (s + 2 + k % 36, l95) for k in range(turns.get(a, 20))]
                lines.append("U1 %d %d" % (t, int(cpu.get(lab, 1.0) / 6 * 1e6)))
                write(os.path.join(d, "r%d-%s-a-%s-%d.log" % (r, a, a.lower(), i)), "\n".join(lines) + "\n")
    s = t
    run(12, 75.0, 1.0)
    wins.append("W idle %d %d" % (s, t))
    write(os.path.join(d, "rapl.txt"), "\n".join(rapl) + "\n")
    write(os.path.join(d, "windows.txt"), "\n".join(wins) + "\n")
    return d


class Verdict(unittest.TestCase):
    BASE = {"U": 16.0, "P": 20.0, "E": 8.0, "X": 7.8, "M": 14.0}

    def v(self, **kw):
        with tempfile.TemporaryDirectory() as t:
            return jp.verdict(fake_run(t, kw.pop("arm_w", self.BASE), **kw))

    def test_predicted_outcome_refuted(self):
        # E and X tie; CPU split 50/50 but P carries 20/(20+8)=0.71 of the energy.
        r = self.v()
        self.assertEqual(r["a"]["verdict"], "REFUTED")
        self.assertEqual(r["a"]["confidence"], 0.875)
        self.assertEqual(r["a"]["rounds_best_over_U_ge_1.3"], 3)
        self.assertEqual(r["b"]["verdict"], "REFUTED")
        self.assertAlmostEqual(r["b"]["per_round"][0]["err"], 0.5 - 20 / 28, places=3)

    def test_supported_when_x_wins_big(self):
        arm_w = dict(self.BASE, X=6.0)
        r = self.v(arm_w=arm_w)
        self.assertEqual(r["a"]["verdict"], "SUPPORTED")
        self.assertEqual({p["best"] for p in r["a"]["per_round"]}, {"X"})

    def test_h21b_supported_when_shares_match(self):
        r = self.v(arm_w=dict(self.BASE, M=14.0), cpu={"performance": 20.0, "efficient": 8.0})
        self.assertEqual(r["b"]["verdict"], "SUPPORTED")

    def test_additivity_gap_inconclusive(self):
        r = self.v(arm_w=dict(self.BASE, M=25.0))
        self.assertEqual(r["b"]["verdict"], "INCONCLUSIVE")

    def test_uncore_negative_control(self):
        r = self.v(uncore=0.8)
        self.assertEqual(r["a"]["verdict"], "INCONCLUSIVE")
        self.assertIn("uncore marginal |dW| > 0.5", r["inconclusive_flags"])

    def test_floor_noise_inconclusive(self):
        r = self.v(idle_jitter=5.0)
        self.assertEqual(r["a"]["verdict"], "INCONCLUSIVE")

    def test_wrong_cpuset_voids(self):
        r = self.v(bad_cpuset=True)
        self.assertEqual(r["a"]["verdict"], "VOID")
        self.assertTrue(r["invalid"])

    def test_one_round_voids(self):
        r = self.v(rounds=1)
        self.assertEqual(r["a"]["verdict"], "VOID")

    def test_two_rounds_cannot_support(self):
        r = self.v(rounds=2, arm_w=dict(self.BASE, X=6.0))
        self.assertEqual(r["a"]["verdict"], "INCONCLUSIVE")

    def test_e_fails_slo(self):
        # E misses the SLO: the ratio still uses E's measured agents/W (U 16 W vs E 8 W
        # -> 0.5x), so an SLO miss is a flag, never an infinite energy win.
        r = self.v(lat={"E": 1.5, "X": 1.5})
        self.assertTrue(all(not p["E_meets_slo"] for p in r["a"]["per_round"]))
        self.assertEqual({p["best"] for p in r["a"]["per_round"]}, {"U"})
        for p in r["a"]["per_round"]:
            self.assertAlmostEqual(p["best_over_E"], 0.5, places=2)
            self.assertTrue(math.isfinite(p["E_over_best_j_per_turn"]))
        self.assertEqual(r["a"]["verdict"], "REFUTED")

    def test_j_per_turn_normalizes_work(self):
        # X draws 6 W (agents/W 1.33x E) but does half E's turns: per turn it costs more.
        r = self.v(arm_w=dict(self.BASE, X=6.0), turns={"X": 10})
        self.assertEqual(r["a"]["verdict_agents_per_w"], "SUPPORTED")
        self.assertEqual(r["a"]["verdict_j_per_turn"], "REFUTED")
        self.assertEqual(r["a"]["verdict"], "REFUTED")
        row = next(x for x in r["rows"] if x["arm"] == "X")
        self.assertAlmostEqual(row["j_per_turn"], 6.0 * 40 / (12 * 10), places=3)

    def test_j_per_turn_supports_when_both_win(self):
        r = self.v(arm_w=dict(self.BASE, X=6.0))
        self.assertEqual(r["a"]["verdict_j_per_turn"], "SUPPORTED")
        self.assertEqual(r["a"]["verdict"], "SUPPORTED")

    def test_malformed_zones_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as t:
            fake_run(t, self.BASE)
            write(os.path.join(t, "zones.txt"), "intel-rapl:0 package-0\n")
            with self.assertRaisesRegex(ValueError, "sudo failed"):
                jp.verdict(t)

    def test_empty_rapl_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as t:
            fake_run(t, self.BASE)
            write(os.path.join(t, "rapl.txt"), "")
            with self.assertRaisesRegex(ValueError, "no samples"):
                jp.verdict(t)

    def test_counter_wrap(self):
        zones = {"package": (0, 100)}
        samples = [(0.0, [90]), (1.0, [95]), (2.0, [5])]  # wraps at 100
        self.assertAlmostEqual(jp.window_watts(zones, samples, 0, 2)["package"], 15 / 1e6 / 2)

    def test_p95(self):
        self.assertEqual(jp.p95(list(range(1, 21))), 19)
        self.assertEqual(jp.p95(list(range(1, 101))), 95)
        self.assertEqual(jp.p95([3.0]), 3.0)
        self.assertTrue(math.isnan(jp.p95([])))


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(HERE, "joulepack.py")] + list(args),
                              capture_output=True, text=True, check=True).stdout

    def test_detect_cli_fixture(self):
        d = json.loads(self.run_cli("detect", "--sysroot", os.path.join(FIX, "meteorlake", "sysroot")))
        self.assertTrue(d["hybrid"])

    def test_verdict_cli_text_and_json(self):
        with tempfile.TemporaryDirectory() as t:
            fake_run(t, Verdict.BASE)
            self.assertIn("H21a verdict: REFUTED", self.run_cli("verdict", t))
            self.assertEqual(json.loads(self.run_cli("verdict", t, "--json"))["b"]["verdict"], "REFUTED")

    def test_place_and_attribute_cli(self):
        with tempfile.TemporaryDirectory() as t:
            write(os.path.join(t, "a.json"), json.dumps({"E": {"n": 12, "pkg_w": 10, "p95": 0.4}}))
            self.assertEqual(json.loads(self.run_cli("place", "--arms", os.path.join(t, "a.json")))["decision"], "keep")
            write(os.path.join(t, "p.json"), json.dumps(Attribute.PODS))
            out = json.loads(self.run_cli("attribute", "--core-j", "8", "--pods", os.path.join(t, "p.json"),
                                          "--model", "typed", "--weights", "performance=3,efficient=1"))
            self.assertAlmostEqual(out["p"], 4.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
