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
        dump({"phases": [{"samples": samples}]}, os.path.join(root, f"hold{k}", arm, "load-n4.json"))


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
        # scorers do; the report gives completed and sent requests per arm.
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"L": 15.0, "O": 10.0}, self.Q, spread=0)
            set_n4(d, "O", [{"ok": True, "decode_tokens_per_s": 10.0}] * 15 + [{"ok": False, "error": "timeout"}])
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


if __name__ == "__main__":
    unittest.main()
