#!/usr/bin/env python3
"""Tests for bench/h59/h59.py (EXPERIMENTS.md "H59 pre-registration", "H59 amendment S1"): the
N=4 statistic, every verdict branch on synthetic hold records, the spare holds, both load
modes, and the committed H57d records read through the H59 statistic. No cluster, no network."""
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import h59  # noqa: E402

H54 = os.path.join(HERE, "..", "h54")
sys.path.insert(0, H54)
import h54  # noqa: E402


def dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f)


def put(path, text):
    with open(path, "w") as f:
        f.write(text)


H57D_RECORDS = os.path.join(HERE, "..", "h57d", "results", "2026-10-21")
MATCHED = {"matched": True, "reason": "matched"}
ORDERS = {1: ["O", "L"], 2: ["L", "O"], 3: ["O", "L"], 4: ["L", "O"]}


def write_holds(root, n4, quality, n1=None, foreign=None, missing=(), spread=0.03, seed=7):
    """n4 / n1: {arm: decode tok/s centre}; quality: {arm: items correct of 50};
    foreign: {arm: foreign P-core CPU-s/s during its N=4 phase}; missing: {(hold, arm)} recorded
    not deliverable."""
    rng = random.Random(seed)
    n1 = n1 or n4
    foreign = foreign or {a: 3.0 for a in n4}
    for k in (1, 2, 3, 4):
        hd = os.path.join(root, f"hold{k}")
        os.makedirs(hd)
        t, samples = 1000.0 * k, []
        for arm in ORDERS[k]:
            ad = os.path.join(hd, arm)
            os.makedirs(ad)
            if (k, arm) in missing:
                dump({"status": "not_deliverable"}, os.path.join(ad, "status.json"))
                continue
            dump({"status": "measured"}, os.path.join(ad, "status.json"))
            if k == 4:
                dump({"correct": quality[arm], "n": 50, "score": quality[arm] / 50}, os.path.join(ad, "quality.json"))
                continue
            for phase, centre, count in (("n1", n1[arm], 8), ("n4", n4[arm], 16)):
                s = [{"ok": True, "decode_tokens_per_s": centre * (1 + rng.uniform(-spread, spread))} for _ in range(count)]
                dump({"phases": [{"samples": s}]}, os.path.join(ad, f"load-{phase}.json"))
            with open(os.path.join(ad, "phases.jsonl"), "w") as f:
                f.write(json.dumps({"phase": "n1", "t0": t, "t1": t + 50}) + "\n")
                f.write(json.dumps({"phase": "n4", "t0": t + 50, "t1": t + 150}) + "\n")
            for ts in range(int(t) + 55, int(t) + 151, 5):
                samples.append({"ts": ts, "seconds": 5, "pcore_busy": 8.0 + foreign[arm], "node_cpu": 8.0})
            t += 200
        with open(os.path.join(hd, "load.jsonl"), "w") as f:
            for s in samples:
                f.write(json.dumps(s) + "\n")


def n4_samples(root, arm):
    out = []
    for k in (1, 2, 3):
        with open(os.path.join(root, f"hold{k}", arm, "load-n4.json")) as f:
            j = json.load(f)
        out += [s["decode_tokens_per_s"] for s in j["phases"][0]["samples"]]
    return out


def set_n4(root, arm, samples):
    """Replace an arm's N=4 samples in performance holds 1-3 with the given sample dicts."""
    for k in (1, 2, 3):
        set_n4_hold(root, arm, k, samples)


def set_n4_hold(root, arm, k, samples):
    """Replace an arm's N=4 samples in performance hold k."""
    dump({"phases": [{"samples": samples}]}, os.path.join(root, f"hold{k}", arm, "load-n4.json"))


def at(value, n=16):
    """n completed requests, each decoding at value tok/s."""
    return [{"ok": True, "decode_tokens_per_s": value}] * n


class Statistic(unittest.TestCase):
    def test_pooled_48_per_arm_and_h54_bootstrap(self):
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 15.0, "O": 10.0}, {"L": 45, "O": 45}, spread=0.2)
            r = h59.score(d, MATCHED)
            ls, os_ = n4_samples(d, "L"), n4_samples(d, "O")
        self.assertEqual((r["arms"]["L"]["n4_requests"], r["arms"]["O"]["n4_requests"]), (48, 48))
        self.assertAlmostEqual(r["ratio"], statistics.median(ls) / statistics.median(os_), places=12)
        self.assertEqual(r["ci"], list(h54.boot_ratio(ls, os_, n=10000, seed=1)))

    def test_only_n4_decides(self):
        # 3x at N=1 is reported, not gated: N=4 at par is REFUTED.
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 10.0, "O": 10.0}, {"L": 45, "O": 45}, n1={"L": 30.0, "O": 10.0})
            r = h59.score(d, MATCHED)
        self.assertGreater(r["reported"]["n1"], 2.5)
        self.assertIsNone(r["reported"]["n16"])
        self.assertEqual(r["verdict"], "REFUTED")


class Verdict(unittest.TestCase):
    Q = {"L": 45, "O": 45}

    def run_score(self, n4, quality=None, load=MATCHED, **kw):
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, n4, quality or self.Q, **kw)
            return h59.score(d, load)

    def test_supported(self):
        r = self.run_score({"L": 15.0, "O": 10.0})
        self.assertGreaterEqual(r["ci"][0], 1.2)
        self.assertEqual(r["verdict"], "SUPPORTED")

    def test_refuted_ci_below_bar(self):
        r = self.run_score({"L": 11.0, "O": 10.0})
        self.assertLess(r["ci"][1], 1.2)
        self.assertEqual(r["verdict"], "REFUTED")

    def per_hold_score(self, hold3_l):
        """L 20.0 and O 10.0 in holds 1-2, L at hold3_l in hold 3: the pooled CI clears 1.2."""
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 20.0, "O": 10.0}, self.Q, spread=0)
            set_n4_hold(d, "L", 3, at(hold3_l))
            return h59.score(d, MATCHED)

    def test_supported_needs_l_faster_in_each_hold(self):
        # Amendment S2: the pooled CI clears the bar, but L is slower than O in hold 3.
        r = self.per_hold_score(9.0)
        self.assertGreaterEqual(r["ci"][0], 1.2)
        self.assertEqual(r["per_hold"], {1: 2.0, 2: 2.0, 3: 0.9})
        self.assertFalse(r["all_holds_faster"])
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("not faster than O in each performance hold", r["reason"])

    def test_a_hold_at_par_is_not_faster(self):
        # Faster means a per-hold ratio above 1, as in H57d: 1.0 is not, 1.05 is.
        r = self.per_hold_score(10.0)
        self.assertEqual(r["per_hold"][3], 1.0)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        r = self.per_hold_score(10.5)
        self.assertTrue(r["all_holds_faster"])
        self.assertEqual(r["verdict"], "SUPPORTED")
        self.assertIn("faster in each performance hold", r["reason"])

    def test_refuted_needs_no_per_hold_condition(self):
        # Amendment S2 adds the condition to SUPPORTED only: a hold where L is slower still
        # leaves a CI below the bar REFUTED.
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 11.0, "O": 10.0}, self.Q, spread=0)
            set_n4_hold(d, "L", 3, at(9.0))
            r = h59.score(d, MATCHED)
        self.assertLess(r["ci"][1], 1.2)
        self.assertFalse(r["all_holds_faster"])
        self.assertEqual(r["verdict"], "REFUTED")

    def test_inconclusive_ci_straddles_bar(self):
        r = self.run_score({"L": 12.0, "O": 10.0})
        self.assertLess(r["ci"][0], 1.2)
        self.assertGreaterEqual(r["ci"][1], 1.2)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_ci_lower_bound_exactly_at_bar_is_supported(self):
        # No spread: every resample's ratio is 12.0 / 10.0, so the CI is [1.2, 1.2].
        r = self.run_score({"L": 12.0, "O": 10.0}, spread=0)
        self.assertEqual(r["ci"], [1.2, 1.2])
        self.assertEqual(r["verdict"], "SUPPORTED")

    def test_ci_just_below_bar_is_refuted(self):
        r = self.run_score({"L": 11.95, "O": 10.0}, spread=0)
        self.assertEqual([round(x, 12) for x in r["ci"]], [1.195, 1.195])
        self.assertEqual(r["verdict"], "REFUTED")

    def test_ci_upper_bound_exactly_at_bar_is_inconclusive(self):
        # L is 12.0 throughout; O is half 10.0, half 11.0. The largest resampled ratio is
        # 12.0 / 10.0 = 1.2, so the CI's upper bound sits on the bar and its lower bound below.
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 12.0, "O": 10.0}, self.Q, spread=0)
            set_n4(d, "O", [{"ok": True, "decode_tokens_per_s": v} for v in [10.0] * 8 + [11.0] * 8])
            r = h59.score(d, MATCHED)
        self.assertLess(r["ci"][0], 1.2)
        self.assertEqual(r["ci"][1], 1.2)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("not resolved", r["reason"])

    def test_quality_gate_is_o_minus_two_items(self):
        r = self.run_score({"L": 15.0, "O": 10.0}, quality={"L": 43, "O": 45})
        self.assertTrue(r["quality_gate"]["passed"])
        self.assertEqual(r["verdict"], "SUPPORTED")
        r = self.run_score({"L": 15.0, "O": 10.0}, quality={"L": 42, "O": 45})
        self.assertFalse(r["quality_gate"]["passed"])
        self.assertEqual(r["verdict"], "REFUTED")
        self.assertIn("quality gate", r["reason"])

    def test_l_above_o_on_quality_passes(self):
        r = self.run_score({"L": 15.0, "O": 10.0}, quality={"L": 50, "O": 30})
        self.assertEqual(r["verdict"], "SUPPORTED")

    def test_not_deliverable_in_two_holds(self):
        r = self.run_score({"L": 15.0, "O": 10.0}, missing={(1, "L"), (2, "L")})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("not deliverable in two holds", r["reason"])

    def test_two_failures_count_even_when_spares_fill_the_holds(self):
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 15.0, "O": 10.0}, self.Q, missing={(1, "O"), (3, "O")})
            for s, k in ((5, 1), (6, 3)):
                shutil.copytree(os.path.join(d, "hold2"), os.path.join(d, f"hold{s}"))
                put(os.path.join(d, f"hold{s}", "repeats"), f"{k}\n")
            r = h59.score(d, MATCHED)
        self.assertEqual(r["arms"]["O"]["holds_measured"], 3)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("O not deliverable in two holds", r["reason"])

    def test_quality_hold_failure_counts_toward_two(self):
        # Not deliverable in the quality hold and in hold 2, which a spare fills: two holds.
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 15.0, "O": 10.0}, self.Q, missing={(2, "O"), (4, "O")})
            shutil.copytree(os.path.join(d, "hold1"), os.path.join(d, "hold5"))
            put(os.path.join(d, "hold5", "repeats"), "2\n")
            r = h59.score(d, MATCHED)
        self.assertEqual(r["arms"]["O"]["holds_measured"], 3)
        self.assertEqual(r["arms"]["O"]["not_deliverable"], 2)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("O not deliverable in two holds", r["reason"])

    def test_spare_hold_failure_counts_toward_two(self):
        # Not deliverable in hold 2 and in its first spare (hold5); the second spare (hold6) fills it.
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 15.0, "O": 10.0}, self.Q, missing={(2, "O")})
            shutil.copytree(os.path.join(d, "hold2"), os.path.join(d, "hold5"))
            put(os.path.join(d, "hold5", "repeats"), "2\n")
            shutil.copytree(os.path.join(d, "hold1"), os.path.join(d, "hold6"))
            put(os.path.join(d, "hold6", "repeats"), "2\n")
            r = h59.score(d, MATCHED)
        self.assertEqual(r["arms"]["O"]["holds_measured"], 3)
        self.assertEqual(r["arms"]["O"]["not_deliverable"], 2)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("O not deliverable in two holds", r["reason"])

    def test_failed_requests_are_dropped_and_counted(self):
        # A partly failed N=4 phase is scored on its completed requests, as H54's and H57d's
        # scorers do; the report gives completed and sent requests per arm. A failed request
        # is dropped on `ok` alone, even when it carries a decode rate.
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 15.0, "O": 10.0}, self.Q, spread=0)
            set_n4(d, "O", at(10.0, 15) + [{"ok": False, "error": "timeout", "decode_tokens_per_s": 1.0}])
            r = h59.score(d, MATCHED)
        self.assertEqual((r["arms"]["O"]["n4_requests"], r["arms"]["O"]["n4_total"]), (45, 48))
        self.assertEqual((r["arms"]["L"]["n4_requests"], r["arms"]["L"]["n4_total"]), (48, 48))
        self.assertEqual(r["ci"], [1.5, 1.5])
        self.assertEqual(r["verdict"], "SUPPORTED")

    def test_spare_replaces_one_failed_hold(self):
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 15.0, "O": 10.0}, self.Q, missing={(2, "O")})
            shutil.copytree(os.path.join(d, "hold1"), os.path.join(d, "hold5"))
            put(os.path.join(d, "hold5", "repeats"), "2\n")
            r = h59.score(d, MATCHED)
        self.assertEqual(r["arms"]["O"]["not_deliverable"], 1)
        self.assertEqual(r["arms"]["O"]["holds_measured"], 3)
        self.assertEqual(r["verdict"], "SUPPORTED")

    def test_one_failed_hold_without_a_spare(self):
        r = self.run_score({"L": 15.0, "O": 10.0}, missing={(2, "O")})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("2 of 3 performance holds", r["reason"])

    def test_no_quality_record(self):
        r = self.run_score({"L": 15.0, "O": 10.0}, missing={(4, "L")})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("no quality record", r["reason"])

    def test_load_unmatched_from_a_runner_verdict(self):
        r = self.run_score({"L": 15.0, "O": 10.0}, load={"matched": False, "reason": "void twice"})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("load unmatched: void twice", r["reason"])
        self.assertNotIn("ci", r)

    def test_quality_failure_wins_over_unmatched_load(self):
        r = self.run_score({"L": 15.0, "O": 10.0}, quality={"L": 40, "O": 45}, load={"matched": False, "reason": "x"})
        self.assertEqual(r["verdict"], "REFUTED")

    def test_h57d_load_gate(self):
        r = self.run_score({"L": 15.0, "O": 10.0}, load="h57d", foreign={"L": 3.0, "O": 4.5})
        self.assertTrue(r["load_gate"]["matched"])      # spread 1.5 <= 2.0
        self.assertAlmostEqual(r["load_gate"]["means"]["L"], 3.0)
        self.assertEqual(r["verdict"], "SUPPORTED")
        r = self.run_score({"L": 15.0, "O": 10.0}, load="h57d", foreign={"L": 6.0, "O": 3.0})
        self.assertFalse(r["load_gate"]["matched"])     # spread 3.0 > 2.0
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("load unmatched", r["reason"])


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(HERE, "h59.py"), *args], capture_output=True, text=True)

    def test_load_is_required(self):
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 15.0, "O": 10.0}, {"L": 45, "O": 45})
            r = self.run_cli("score", d)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("--load", r.stderr)
            gate = os.path.join(d, "gate.json")
            dump(MATCHED, gate)
            r = self.run_cli("score", d, "--load", gate)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["verdict"], "SUPPORTED")

    def test_gate_without_matched_is_unmatched(self):
        # A runner verdict that does not say matched is not a matched load.
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 15.0, "O": 10.0}, {"L": 45, "O": 45})
            gate = os.path.join(d, "gate.json")
            dump({"reason": "no verdict written"}, gate)
            r = self.run_cli("score", d, "--load", gate)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["verdict"], "INCONCLUSIVE")
        self.assertIn("load unmatched: no verdict written", out["reason"])

    def test_arms_names_l_and_o(self):
        r = self.run_cli("score", H57D_RECORDS, "--load", "h57d", "--arms", "L=Ld-mtp")
        self.assertNotEqual(r.returncode, 0)


class H57dRecords(unittest.TestCase):
    """The committed H57d run read through the H59 statistic, Ld-mtp as L and Od as O. These
    are the descriptive desktop numbers amendment S1 quotes; they are not an H59d measurement."""

    def test_h57d_n4_descriptive(self):
        r = h59.score(H57D_RECORDS, "h57d", {"L": "Ld-mtp", "O": "Od"})
        self.assertEqual((r["arms"]["L"]["n4_requests"], r["arms"]["O"]["n4_requests"]), (48, 48))
        self.assertEqual((r["arms"]["L"]["n4_total"], r["arms"]["O"]["n4_total"]), (48, 48))
        self.assertEqual(r["quality_gate"], {"L": 50, "O": 50, "need": 48, "passed": True})
        self.assertFalse(r["load_gate"]["matched"])
        self.assertEqual(round(r["load_gate"]["means"]["Ld-mtp"], 2), 5.58)
        self.assertEqual(round(r["load_gate"]["means"]["Od"], 2), 2.96)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("load unmatched", r["reason"])
        # Matched by fiat, the statistic itself: 0.661, CI 0.644-0.870, below 1.2 in every hold.
        r = h59.score(H57D_RECORDS, MATCHED, {"L": "Ld-mtp", "O": "Od"})
        self.assertEqual([round(x, 3) for x in [r["ratio"]] + r["ci"]], [0.661, 0.644, 0.870])
        self.assertEqual({k: round(v, 3) for k, v in r["per_hold"].items()}, {1: 0.969, 2: 0.612, 3: 1.104})
        self.assertEqual(r["verdict"], "REFUTED")


class Report(unittest.TestCase):
    """bench/h59/report.py: the reported, never gated figures (TTFT, prefix reuse, J/token, the
    per-phase medians) read from the same records."""

    def setUp(self):
        import report
        self.report = report
        self.d = tempfile.mkdtemp()
        write_holds(self.d, {"L": 20.0, "O": 8.0}, {"L": 48, "O": 47})
        # Per perf hold and arm: an idle window at W watts, each phase's energy and output tokens,
        # the TTFT phase's 4 requests and the prefix phase's record.
        self.watts = {"L": 100.0, "O": 120.0}
        for k in (1, 2, 3):
            for arm in ("L", "O"):
                ad = os.path.join(self.d, f"hold{k}", arm)
                dump({"avg_watts": self.watts[arm], "joules": self.watts[arm] * 60, "seconds": 60}, os.path.join(ad, "energy-idle.json"))
                for p, n in (("n1", 8), ("n4", 16)):
                    with open(os.path.join(ad, f"load-{p}.json")) as f:
                        j = json.load(f)
                    j["phases"][0].update(output_tokens_total=256 * n, aggregate_tokens_per_s=10.0 * k)
                    dump(j, os.path.join(ad, f"load-{p}.json"))
                    dump({"joules": 1000.0 * k, "seconds": 5.0}, os.path.join(ad, f"energy-{p}.json"))
                t = [{"ok": True, "ttft_s": 1.0 * k + i} for i in range(4)] + [{"ok": False, "ttft_s": 99.0}]
                dump({"phases": [{"samples": t}]}, os.path.join(ad, "load-ttft.json"))
                dump({"added_ttft_median_s": 0.5 * k + (0.1 if arm == "L" else 0.0)}, os.path.join(ad, "prefix.json"))

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_figures(self):
        r = self.report.report(self.d)
        for arm in ("L", "O"):
            a = r[arm]
            self.assertEqual(a["holds"], [1, 2, 3])
            self.assertAlmostEqual(a["decode_pooled"]["n4"], statistics.median(n4_samples(self.d, arm)))
            self.assertIsNone(a["decode_pooled"]["n16"])
            self.assertEqual(a["aggregate"]["n4"], {1: 10.0, 2: 20.0, 3: 30.0})
            # TTFT: failed requests dropped; per hold the median of 4, pooled over 12.
            self.assertEqual(a["ttft_s"], {1: 2.5, 2: 3.5, 3: 4.5})
            self.assertEqual(a["ttft_pooled_s"], statistics.median([k + i for k in (1, 2, 3) for i in range(4)]))
            # J/token pooled: 6,000 J over 3 x 4,096 tokens; net less W x 5 s per hold.
            j = a["j_per_token"]["n4"]
            self.assertAlmostEqual(j["gross"], 6000.0 / (3 * 4096))
            self.assertAlmostEqual(j["net"], (6000.0 - 3 * 5 * self.watts[arm]) / (3 * 4096))
            self.assertEqual(j["tokens"], 3 * 4096)
        self.assertAlmostEqual(r["L"]["prefix_added_ttft_median_s"], 1.1)
        self.assertAlmostEqual(r["ratios_L_over_O"]["prefix_added_ttft"], 1.1)
        self.assertAlmostEqual(r["ratios_L_over_O"]["decode_pooled"]["n4"],
                               r["L"]["decode_pooled"]["n4"] / r["O"]["decode_pooled"]["n4"])

    def test_spare_and_unmeasured_hold(self):
        # Hold 2's O failed; spare 5 repeats it and stands in for it.
        dump({"status": "failed"}, os.path.join(self.d, "hold2", "O", "status.json"))
        self.assertEqual(self.report.report(self.d)["O"]["holds"], [1, 3])
        shutil.copytree(os.path.join(self.d, "hold2"), os.path.join(self.d, "hold5"))
        dump({"status": "measured"}, os.path.join(self.d, "hold5", "O", "status.json"))
        put(os.path.join(self.d, "hold5", "repeats"), "2\n")
        self.assertEqual(self.report.report(self.d)["O"]["holds"], [1, 2, 3])

    def test_cli(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "report.py"), self.d, "--arms", "L=L,O=O"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["L"]["holds"], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
