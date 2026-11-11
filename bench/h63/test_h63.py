#!/usr/bin/env python3
"""Tests for bench/h63/h63.py (EXPERIMENTS.md "H63 pre-registration"): the registered arm orders,
the N=1 statistic, every verdict branch on synthetic hold records, the idle-window load gate over
the two gated arms, the spare holds, the reported comparisons, and the committed H57w, H59w, H57d
and H59d records read through H63's statistic. No cluster, no network."""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import h63  # noqa: E402

sys.path.insert(0, os.path.join(REPO, "bench", "h54"))
import h54  # noqa: E402

sys.path.insert(0, os.path.join(REPO, "console"))
import model_opt  # noqa: E402

H57W = os.path.join(REPO, "bench", "wildct", "results", "2026-11-11", "h57", "H57w")
H57W_VERDICT = os.path.join(REPO, "bench", "wildct", "results", "2026-11-11", "h57", "verdict.json")
H59W = os.path.join(REPO, "bench", "h59", "results", "2026-11-10-wildct", "H59w")
H59W_REPORT = os.path.join(REPO, "bench", "h59", "results", "2026-11-10-wildct", "report.json")
H57D = os.path.join(REPO, "bench", "h57d", "results", "2026-10-21")
H59D = os.path.join(REPO, "bench", "h59", "results", "2026-11-01-desktop", "H59d")

ARMS = ("Ls", "Od", "Ld-mtp")


def dump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f)


def jl(path):
    with open(path) as f:
        return json.load(f)


def write_holds(root, n1, quality, idle=None, n1_load=None, missing=(), per_hold=None, spread=0.03, seed=7,
                arms=ARMS, holds=(1, 2, 3, 4), n4=None):
    """Synthetic records in H57d's layout. n1: {arm: N=1 decode tok/s centre}; per_hold: {(hold, arm):
    centre} overrides; quality: {arm: GSM8K items correct of 50}; idle / n1_load: {arm: foreign P-core
    CPU-s/s during its idle window / its N=1 phase}; missing: {(hold, arm)} recorded not deliverable."""
    rng = random.Random(seed)
    idle = idle or {a: 1.0 for a in arms}
    n1_load = n1_load or idle
    n4 = n4 or {a: n1[a] / 3 for a in arms}
    for k in holds:
        hd = os.path.join(root, f"hold{k}")
        os.makedirs(hd, exist_ok=True)
        t, samples = 1000.0 * k, []
        order = [a for a in h63.ORDERS.get(k if k <= 4 else 1, list(arms)) if a in arms]
        for arm in order:
            ad = os.path.join(hd, arm)
            os.makedirs(ad, exist_ok=True)
            if (k, arm) in missing:
                dump({"status": "not_deliverable"}, os.path.join(ad, "status.json"))
                continue
            dump({"status": "measured"}, os.path.join(ad, "status.json"))
            if k == 4:
                dump({"correct": quality[arm], "n": 50, "score": quality[arm] / 50}, os.path.join(ad, "quality.json"))
                continue
            c1 = (per_hold or {}).get((k, arm), n1[arm])
            for phase, centre, count in (("n1", c1, 8), ("n4", n4[arm], 16), ("ttft", 1.0, 4)):
                s = [{"ok": True, "decode_tokens_per_s": centre * (1 + rng.uniform(-spread, spread)),
                      "ttft_s": 20.0 * (1 + rng.uniform(-spread, spread))} for _ in range(count)]
                dump({"phases": [{"samples": s, "aggregate_tokens_per_s": centre * 2}]}, os.path.join(ad, f"load-{phase}.json"))
            with open(os.path.join(ad, "phases.jsonl"), "w") as f:
                f.write(json.dumps({"phase": "idle", "t0": t, "t1": t + 60}) + "\n")
                f.write(json.dumps({"phase": "n1", "t0": t + 60, "t1": t + 120}) + "\n")
                f.write(json.dumps({"phase": "n4", "t0": t + 120, "t1": t + 180}) + "\n")
            for ts in range(int(t) + 5, int(t) + 61, 5):
                samples.append({"ts": ts, "seconds": 5, "pcore_busy": 1.0 + idle[arm], "node_cpu": 1.0})
            for ts in range(int(t) + 65, int(t) + 121, 5):
                samples.append({"ts": ts, "seconds": 5, "pcore_busy": 8.0 + n1_load[arm], "node_cpu": 8.0})
            t += 200
        with open(os.path.join(hd, "load.jsonl"), "w") as f:
            for s in samples:
                f.write(json.dumps(s) + "\n")


def n1_pool(root, arm, holds=(1, 2, 3)):
    out = []
    for k in holds:
        with open(os.path.join(root, f"hold{k}", arm, "load-n1.json")) as f:
            out += [s["decode_tokens_per_s"] for s in json.load(f)["phases"][0]["samples"]]
    return out


class Tables(unittest.TestCase):
    def test_performance_orders_are_a_latin_square(self):
        # Each arm once in each position over performance holds 1-3.
        for pos in range(3):
            self.assertEqual(sorted(h63.ORDERS[k][pos] for k in (1, 2, 3)), sorted(ARMS))
        for k in (1, 2, 3):
            self.assertEqual(sorted(h63.ORDERS[k]), sorted(ARMS))

    def test_registered_orders(self):
        self.assertEqual(h63.ORDERS, {1: ["Od", "Ld-mtp", "Ls"], 2: ["Ld-mtp", "Ls", "Od"],
                                      3: ["Ls", "Od", "Ld-mtp"], 4: ["Ls", "Od", "Ld-mtp"]})

    def test_bar_and_quality_slack(self):
        self.assertEqual((h63.BAR, h63.QUALITY_SLACK), (1.2, 2))
        self.assertEqual(h63.GATED, ("Ls", "Od"))

    def test_order_cli_takes_a_hold_by_its_own_number_only(self):
        ok = subprocess.run([sys.executable, os.path.join(HERE, "h63.py"), "order", "2"], capture_output=True, text=True)
        self.assertEqual((ok.returncode, ok.stdout.split()), (0, ["Ld-mtp", "Ls", "Od"]))
        for bad in ("01", "5", "0", "x", "4.0"):
            r = subprocess.run([sys.executable, os.path.join(HERE, "h63.py"), "order", bad], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, bad)
            self.assertEqual(r.stdout, "", bad)


class Verdicts(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="h63-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def score(self, **kw):
        write_holds(self.d, **kw)
        return h63.score(self.d)

    def test_supported(self):
        r = self.score(n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50})
        self.assertEqual(r["verdict"], "SUPPORTED", r["reason"])
        self.assertTrue(r["all_holds_faster"])
        self.assertEqual(sorted(r["per_hold"]), [1, 2, 3])
        # The statistic is H54's boot_ratio on the 24 pooled N=1 requests per arm.
        a, b = n1_pool(self.d, "Ls"), n1_pool(self.d, "Od")
        self.assertEqual((len(a), len(b)), (24, 24))
        self.assertEqual(r["ci"], list(h54.boot_ratio(a, b)))
        self.assertAlmostEqual(r["ratio"], h54.median(a) / h54.median(b))

    def test_refuted_on_the_ci(self):
        r = self.score(n1={"Ls": 10.5, "Od": 10.0, "Ld-mtp": 13.4}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50})
        self.assertEqual(r["verdict"], "REFUTED")
        self.assertIn("CI upper bound < 1.2", r["reason"])
        # Ld-mtp is reported, never gated: its 1.34x does not move the verdict.
        self.assertGreater(r["reported"]["Ld-mtp/Od"]["n1"]["ci"][0], 1.2)

    def test_quality_gate_boundary(self):
        r = self.score(n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 48, "Od": 50, "Ld-mtp": 40})
        self.assertEqual(r["verdict"], "SUPPORTED", r["reason"])
        self.assertEqual(r["quality_gate"], {"Ls": 48, "Od": 50, "need": 48, "passed": True})
        shutil.rmtree(self.d)
        os.makedirs(self.d)
        r = self.score(n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 47, "Od": 50, "Ld-mtp": 50})
        self.assertEqual(r["verdict"], "REFUTED")
        self.assertIn("quality gate failed", r["reason"])

    def test_ld_mtp_quality_is_reported_not_gated(self):
        r = self.score(n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 10})
        self.assertEqual(r["verdict"], "SUPPORTED")
        self.assertEqual(r["arms"]["Ld-mtp"]["quality"]["correct"], 10)

    @staticmethod
    def rec(role, pooled, per_hold, correct=50):
        # A pooled CI that clears the bar beside per-hold medians that do not: the pooled
        # bootstrap treats the 24 requests as independent, the per-hold condition does not.
        return {"role": role, "not_deliverable": 0, "holds_measured": 3, "quality": {"correct": correct, "n": 50},
                "pooled": {"n1": pooled}, "n1_median": h54.median(pooled), "n1_per_hold": per_hold}

    def test_one_slower_hold_is_inconclusive(self):
        ls = self.rec("Ls", [14.0, 14.2] * 12, {1: 14.0, 2: 9.0, 3: 14.0})
        od = self.rec("Od", [10.0, 10.1] * 12, {1: 10.0, 2: 10.0, 3: 10.0})
        r = h63.decide(ls, od, {"matched": True, "reason": "matched"})
        self.assertGreaterEqual(r["ci"][0], 1.2)
        self.assertFalse(r["all_holds_faster"])
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("not faster than Od in each performance hold", r["reason"])
        r = h63.decide(dict(ls, n1_per_hold={1: 14.0, 2: 10.5, 3: 14.0}), od, {"matched": True})
        self.assertEqual(r["verdict"], "SUPPORTED")

    def test_a_hold_exactly_even_is_not_faster(self):
        ls = self.rec("Ls", [14.0, 14.2] * 12, {1: 14.0, 2: 14.0, 3: 10.0})
        od = self.rec("Od", [10.0, 10.1] * 12, {1: 10.0, 2: 10.0, 3: 10.0})
        r = h63.decide(ls, od, {"matched": True})
        self.assertEqual(r["per_hold"][3], 1.0)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_a_ci_lower_bound_exactly_on_the_bar_supports(self):
        ls = self.rec("Ls", [12.0] * 24, {1: 12.0, 2: 12.0, 3: 12.0})
        od = self.rec("Od", [10.0] * 24, {1: 10.0, 2: 10.0, 3: 10.0})
        r = h63.decide(ls, od, {"matched": True})
        self.assertEqual(r["ci"], [1.2, 1.2])
        self.assertEqual(r["verdict"], "SUPPORTED")
        r = h63.decide(dict(ls, pooled={"n1": [11.99] * 24}), od, {"matched": True})
        self.assertEqual(r["verdict"], "REFUTED")

    def test_ci_across_the_bar_is_inconclusive(self):
        r = self.score(n1={"Ls": 12.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50}, spread=0.25)
        self.assertLess(r["ci"][0], 1.2)
        self.assertGreaterEqual(r["ci"][1], 1.2)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("not resolved", r["reason"])

    def test_not_deliverable_in_two_holds(self):
        # The quality hold and the spares count toward the two.
        r = self.score(n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50},
                       missing={(1, "Ls"), (4, "Ls")})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("Ls not deliverable in two holds", r["reason"])

    def test_ld_mtp_not_deliverable_does_not_decide(self):
        r = self.score(n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50},
                       missing={(1, "Ld-mtp"), (2, "Ld-mtp"), (3, "Ld-mtp")})
        self.assertEqual(r["verdict"], "SUPPORTED", r["reason"])
        self.assertIsNone(r["reported"]["Ld-mtp/Od"]["n1"])

    def test_fewer_than_three_performance_holds(self):
        r = self.score(n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50},
                       missing={(2, "Od")})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("Od measured in 2 of 3 performance holds", r["reason"])

    def test_a_hold_with_no_completed_n1_request_is_not_measured(self):
        write_holds(self.d, n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50})
        p = os.path.join(self.d, "hold3", "Ls", "load-n1.json")
        j = jl(p)
        for s in j["phases"][0]["samples"]:
            s["ok"] = False
        dump(j, p)
        r = h63.score(self.d)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("Ls measured in 2 of 3 performance holds", r["reason"])

    def test_failed_requests_are_dropped(self):
        write_holds(self.d, n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50})
        p = os.path.join(self.d, "hold1", "Ls", "load-n1.json")
        j = jl(p)
        j["phases"][0]["samples"][0]["ok"] = False
        dump(j, p)
        r = h63.score(self.d)
        self.assertEqual(r["arms"]["Ls"]["n1_requests"], 23)
        self.assertEqual(r["verdict"], "SUPPORTED")

    def test_no_quality_record(self):
        write_holds(self.d, n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50},
                    holds=(1, 2, 3))
        r = h63.score(self.d)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("no quality record", r["reason"])

    def test_spare_stands_in_for_the_hold_it_repeats(self):
        write_holds(self.d, n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50},
                    missing={(2, "Ls")})
        spare = os.path.join(self.d, "spare")
        write_holds(spare, n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={}, holds=(2,), seed=11)
        shutil.move(os.path.join(spare, "hold2"), os.path.join(self.d, "hold5"))
        with open(os.path.join(self.d, "hold5", "repeats"), "w") as f:
            f.write("2\n")
        r = h63.score(self.d)
        self.assertEqual(r["verdict"], "SUPPORTED", r["reason"])
        self.assertEqual(r["arms"]["Ls"]["holds_measured"], 3)
        self.assertEqual(r["arms"]["Ls"]["not_deliverable"], 1)


class LoadGate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="h63-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_idle_windows_gate_and_the_n1_reading_is_recorded_only(self):
        # Idle windows apart (5.0 vs 1.0, spread 4 > 2): unmatched, whatever the N=1 phases read.
        write_holds(self.d, n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50},
                    idle={"Ls": 5.0, "Od": 1.0, "Ld-mtp": 1.0}, n1_load={"Ls": 1.0, "Od": 1.0, "Ld-mtp": 1.0})
        r = h63.score(self.d)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("load unmatched", r["reason"])
        self.assertTrue(r["load_recorded"]["n1"]["matched"])
        self.assertFalse(r["load_recorded"]["n1"]["gates"])

    def test_n1_phases_apart_do_not_gate(self):
        # H57dp's pattern: OVMS's foreign load falls while it serves; the idle windows match.
        write_holds(self.d, n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50},
                    idle={"Ls": 3.4, "Od": 3.3, "Ld-mtp": 3.5}, n1_load={"Ls": 3.1, "Od": 0.45, "Ld-mtp": 3.4})
        r = h63.score(self.d)
        self.assertTrue(r["load_gate"]["matched"])
        self.assertFalse(r["load_recorded"]["n1"]["matched"])
        self.assertEqual(r["verdict"], "SUPPORTED", r["reason"])

    def test_the_gate_covers_ls_and_od_only(self):
        write_holds(self.d, n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50},
                    idle={"Ls": 1.0, "Od": 1.2, "Ld-mtp": 9.0})
        r = h63.score(self.d)
        self.assertEqual(sorted(r["load_gate"]["means"]), ["Ls", "Od"])
        self.assertTrue(r["load_gate"]["matched"])
        self.assertFalse(r["load_recorded"]["idle_all_arms"]["matched"])
        self.assertEqual(r["verdict"], "SUPPORTED", r["reason"])


class Reported(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="h63-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_reported_comparisons(self):
        write_holds(self.d, n1={"Ls": 11.0, "Od": 10.0, "Ld-mtp": 13.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50},
                    n4={"Ls": 11.0, "Od": 4.0, "Ld-mtp": 4.5}, spread=0.0)
        r = h63.score(self.d)
        rep = r["reported"]
        self.assertAlmostEqual(rep["Ld-mtp/Od"]["n1"]["ratio"], 1.3)
        self.assertAlmostEqual(rep["Ls/Ld-mtp"]["n1"]["ratio"], 11.0 / 13.0)
        self.assertEqual(sorted(rep["Ld-mtp/Od"]["n1"]["per_hold"]), [1, 2, 3])
        self.assertAlmostEqual(rep["Ls/Od"]["n4"]["ratio"], 11.0 / 4.0)
        for k in ("n1", "n4", "ttft"):
            self.assertIn(k, rep["Ls/Od"])
        self.assertIn("n4_aggregate", r["arms"]["Ls"])
        self.assertIn("n4_ttft_s", r["arms"]["Ls"])

    def test_without_ld_mtp_records(self):
        write_holds(self.d, n1={"Ls": 13.0, "Od": 10.0}, quality={"Ls": 50, "Od": 50}, arms=("Ls", "Od"))
        r = h63.score(self.d)
        self.assertEqual(r["verdict"], "SUPPORTED", r["reason"])
        self.assertIsNone(r["reported"]["Ld-mtp/Od"]["n1"])


class Cli(unittest.TestCase):
    def test_score_cli_and_arms(self):
        d = tempfile.mkdtemp(prefix="h63-")
        try:
            write_holds(d, n1={"Ls": 13.0, "Od": 10.0, "Ld-mtp": 12.0}, quality={"Ls": 50, "Od": 50, "Ld-mtp": 50})
            for n in ("Ls", "Od"):
                for k in (1, 2, 3, 4):
                    os.rename(os.path.join(d, f"hold{k}", n), os.path.join(d, f"hold{k}", n + "-x"))
            r = subprocess.run([sys.executable, os.path.join(HERE, "h63.py"), "score", d, "--arms", "Ls=Ls-x,Od=Od-x"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["verdict"], "SUPPORTED")
            for bad in (["--arms", "L=Ls,Od=Od"], ["--arms", "Ls=a"], ["--bogus", "x"], ["--arms"]):
                r = subprocess.run([sys.executable, os.path.join(HERE, "h63.py"), "score", d] + bad, capture_output=True, text=True)
                self.assertNotEqual(r.returncode, 0, bad)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class CommittedRecords(unittest.TestCase):
    """The committed records read through H63's statistic: descriptive, not H63 measurements (the
    H63 pre-registration's table). H57w's and H59w's arms stand in for Ls and Od by name."""

    def test_h57w_reproduces_h57wa_statistic(self):
        r = h63.score(H57W, {"Ls": "Ld-mtp", "Od": "Od", "Ld-mtp": "Ld"})
        v = jl(H57W_VERDICT)["H57da"]
        self.assertEqual(r["ratio"], v["ratio"])
        self.assertEqual(r["ci"], v["ci"])
        self.assertEqual({str(k): x for k, x in r["per_hold"].items()}, v["per_hold"])
        self.assertTrue(r["load_gate"]["matched"])
        self.assertEqual(r["verdict"], "SUPPORTED")

    def test_h59w_reads_refuted_at_one_stream(self):
        r = h63.score(H59W, {"Ls": "L", "Od": "O", "Ld-mtp": "none"})
        rep = jl(H59W_REPORT)
        self.assertAlmostEqual(r["arms"]["Ls"]["n1_median"], rep["L"]["decode_pooled"]["n1"])
        self.assertAlmostEqual(r["arms"]["Od"]["n1_median"], rep["O"]["decode_pooled"]["n1"])
        self.assertLess(r["ci"][1], 1.2)
        self.assertEqual(r["verdict"], "REFUTED")

    def test_h57d_desktop_reads_inconclusive(self):
        r = h63.score(H57D, {"Ls": "Ld-mtp", "Od": "Od", "Ld-mtp": "Ld"})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertLess(r["ci"][0], 1.2)
        self.assertGreater(r["ci"][1], 1.2)

    def test_h59d_desktop_reads_inconclusive_on_delivery(self):
        r = h63.score(H59D, {"Ls": "L", "Od": "O", "Ld-mtp": "none"})
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        self.assertIn("Ls not deliverable in two holds", r["reason"])


class LedgerRows(unittest.TestCase):
    """Each H63 sub-row names exactly its own CPU class, so a rule wired on the H63 family later
    (the pre-registration's "What a verdict changes") matches one class per row."""

    def rows(self):
        with open(os.path.join(REPO, "EXPERIMENTS.md")) as f:
            return model_opt.ledger_rows(f.read())

    def test_each_sub_row_names_its_class_only(self):
        rows = self.rows()
        self.assertEqual(model_opt.row_classes(rows["H63d"][1]), ["i9-13900t"])
        self.assertEqual(model_opt.row_classes(rows["H63w"][1]), ["i7-13700"])

    def test_no_other_h63_row(self):
        self.assertEqual(sorted(r for r in self.rows() if re.match(r"^H63[a-z]*$", r)), ["H63d", "H63w"])


if __name__ == "__main__":
    unittest.main()
