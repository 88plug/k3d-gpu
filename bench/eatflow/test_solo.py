#!/usr/bin/env python3
"""Tests for bench/eatflow/solo.py (H53): seeded task order, solo holds that resume and stop at
the guard, block pairing across holds, and the pairs.analyze reading of the block pairs."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pairs as PR  # noqa: E402
import solo as S  # noqa: E402
import tasks as T  # noqa: E402

SEED = PR.ORDER_SEEDS["H53"]


def setUpModule():
    """A ledger with an H53 row: pairs.design refuses H53 while EXPERIMENTS.md has none."""
    global _LEDGER_DIR
    _LEDGER_DIR = tempfile.mkdtemp()
    path = os.path.join(_LEDGER_DIR, "EXPERIMENTS.md")
    with open(path, "w") as f:
        f.write("| # | Hypothesis |\n|---|---|\n| H53 | test row | PENDING |\n")
    _saved.append(PR.LEDGER)
    PR.LEDGER = path


def tearDownModule():
    PR.LEDGER = _saved.pop()
    shutil.rmtree(_LEDGER_DIR)


_saved = []


def pre(busy=48.0, thr=300.0):
    return {"phase": "pre", "level": "typical", "seconds": 10.0, "busy_pct": busy, "throttle_per_min": thr,
            "swap_used_mib": 0.0, "swap_delta_mib": 0.0, "load_expected": 3, "load_running": 3}


def att(hold, j, arm, outcome="pass_budget", thr=300.0):
    return {"hold": hold, "j": j, "task": T.B24[j]["id"], "arm": arm, "outcome": outcome, "duration_s": 10.0,
            "idle_cpu": None, "turns_s": [3.0], "window": {"pkg_j": 100.0}, "pre": pre(thr=thr)}


def solo_row(hold, j, arm, status="complete"):
    return {"hold": hold, "arm": arm, "j": j, "task": T.B24[j]["id"], "status": status, "void_reason": None}


def two_blocks(n=24, b_pass=lambda j: j % 5 == 0):
    at, rows = [], []
    for hold, arm in ((1, "AG"), (2, "B2"), (3, "B2"), (4, "AG")):
        for j in range(n):
            ok = arm == "AG" or b_pass(j)
            at.append(att(hold, j, arm, "pass_budget" if ok else "over_turn", thr=[12, 300, 3342][(j + hold) % 3]))
            rows.append(solo_row(hold, j, arm))
    return at, rows


class OrderTest(unittest.TestCase):
    def test_task_order_is_a_seeded_permutation(self):
        o = S.task_order(24, SEED)
        self.assertEqual(sorted(o), list(range(24)))
        self.assertEqual(o, S.task_order(24, SEED))
        self.assertNotEqual(o, list(range(24)))
        self.assertNotEqual(o, S.task_order(24, "other"))


class BlockTest(unittest.TestCase):
    def test_blocks_pair_each_task_across_the_two_holds(self):
        at, rows = two_blocks()
        a2, p2 = S.block_pairs(at, rows, "AG", "B2")
        self.assertEqual(len(p2), 48)
        self.assertEqual(len(a2), 96)
        self.assertEqual(len({p["task"] for p in p2}), 48)             # a task once per block
        self.assertTrue(all(r["idle_cpu"] is None for r in a2))       # recorded, never synthesized
        self.assertEqual({tuple(p["order"]) for p in p2}, {("AG", "B2"), ("B2", "AG")})
        res = PR.analyze(a2, p2, "H53", "AG", "B2", registration={"hyp": "H53", "rule": "L3", "seed": SEED},
                         solo=True)
        self.assertEqual((res["rule"], res["matched"], res["void_pairs"]), ("L3", 48, 0))
        self.assertEqual(res["idle_cpu_median"], "not applicable: solo holds")
        self.assertEqual(res["period_balanced"]["AG_first"]["n"], 24)
        self.assertEqual(res["period_balanced"]["B2_first"]["n"], 24)
        self.assertEqual(res["within_budget_success"]["AG"]["k"], 48)
        self.assertEqual(res["verdict"], "SUPPORTED")

    def test_unrun_is_no_pair_and_void_is_a_void_pair(self):
        at, rows = two_blocks()
        rows = [r for r in rows if not (r["hold"] == 2 and r["j"] >= 20)]     # hold 2 ended early
        rows = [dict(r, status="void") if (r["hold"] == 4 and r["j"] == 3) else r for r in rows]
        a2, p2 = S.block_pairs(at, rows, "AG", "B2")
        self.assertEqual(len(p2), 20 + 24)
        self.assertEqual(sum(p["status"] == "void" for p in p2), 1)

    def test_without_solo_the_unmeasured_idle_check_gates(self):
        at, rows = two_blocks()
        a2, p2 = S.block_pairs(at, rows, "AG", "B2")
        res = PR.analyze(a2, p2, "H53", "AG", "B2", registration={"hyp": "H53", "rule": "L3", "seed": SEED})
        self.assertIn("idle-arm pod CPU unmeasured", res["gates"])

    def test_blocks_are_hold_numbers_not_positions(self):
        at, rows = two_blocks()
        drop2 = [r for r in rows if r["hold"] != 2]                    # hold 2 never ran
        a2, p2 = S.block_pairs(at, drop2, "AG", "B2")
        self.assertEqual({p["hold"] for p in p2}, {2})                 # block 1 has no pairs; 3 and 4 are block 2
        self.assertEqual(len(p2), 24)
        only34 = [r for r in rows if r["hold"] in (3, 4)]
        self.assertEqual({p["hold"] for p in S.block_pairs(at, only34, "AG", "B2")[1]}, {2})

    def test_a_hold_with_two_arms_is_refused(self):
        at, rows = two_blocks()
        rows[0] = dict(rows[0], arm="B2")
        with self.assertRaises(ValueError):
            S.block_pairs(at, rows, "AG", "B2")

    def test_a_block_needs_one_hold_of_each_arm(self):
        at, rows = two_blocks()
        rows = [dict(r, arm="AG") if r["hold"] == 2 else r for r in rows]
        with self.assertRaises(ValueError):
            S.block_pairs(at, rows, "AG", "B2")


class SequenceTest(unittest.TestCase):
    def test_expected_arm(self):
        self.assertEqual([PR.expected_arm("H53", h) for h in range(1, 9)],
                         ["AG", "B2", "B2", "AG", "AG", "B2", "B2", "AG"])
        with self.assertRaises(ValueError):
            PR.expected_arm("H53", 0)

    def test_cli_solo_refuses_the_wrong_arm_and_an_unregistered_row(self):
        import agentbench as AB
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        out = os.path.join(tmp, "solo")
        args = lambda hold, arm: ["solo", "--hyp", "H53", "--hold", str(hold), "--arm", arm, "--out", out,  # noqa: E731
                                  "--deadline", "0", "--agent", "http://x", "--upstream", "http://x",
                                  "--proxy-port", "0", "--pod", "p", "--mcp-url", "http://x", "--reset", "r",
                                  "--baseline", "b", "--level", "typical"]
        with self.assertRaises(SystemExit) as cm:
            AB.main(args(2, "AG"))
        self.assertIn("runs B2, not AG", str(cm.exception))
        saved = PR.LEDGER
        PR.LEDGER = os.path.join(tmp, "none.md")
        self.addCleanup(setattr, PR, "LEDGER", saved)
        with self.assertRaises(SystemExit) as cm:
            AB.main(args(1, "AG"))
        self.assertIn("pre-register it first", str(cm.exception))
        self.assertFalse(os.path.exists(out))                          # refused before anything is written


class ReportTest(unittest.TestCase):
    def test_cli_solo_report(self):
        import agentbench as AB
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        at, rows = two_blocks()
        for name, rs in (("attempts.jsonl", at), ("solo.jsonl", rows)):
            with open(os.path.join(tmp, name), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rs)
        PR.register(tmp, "H53")
        self.assertEqual(AB.main(["solo-report", tmp, "--hyp", "H53", "--arm-a", "AG", "--arm-b", "B2"]), 0)
        with open(os.path.join(tmp, "analysis.json")) as f:
            res = json.load(f)
        self.assertEqual((res["rule"], res["matched"], res["holds"], res["verdict"]), ("L3", 48, 4, "SUPPORTED"))


class RunSoloTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.real = PR.attempt
        self.addCleanup(setattr, PR, "attempt", self.real)
        self.calls = []

        def fake(ab, arms, arm, t, j, hold, *a, **kw):
            self.calls.append(j)
            row = att(hold, j, arm)
            PR.append(os.path.join(self.tmp, "attempts.jsonl"), row)
            return row, False
        PR.attempt = fake

    def test_runs_in_seeded_order_resumes_and_respects_the_guard(self):
        now = [0.0]
        clock = lambda: now[0]                                         # noqa: E731
        ts = T.B24[:6]
        n = S.run_solo(None, {"AG": {}}, "AG", ts, self.tmp, deadline=1e9, probe=None, mcp_url=None, reset=None,
                       hold=1, seed=SEED, clock=clock)
        self.assertEqual((n, self.calls), (6, S.task_order(6, SEED)))
        self.calls.clear()
        self.assertEqual(S.run_solo(None, {"AG": {}}, "AG", ts, self.tmp, 1e9, None, None, None, 1, SEED, clock=clock), 0)
        self.assertEqual(self.calls, [])                               # a resumed hold skips what ran
        n = S.run_solo(None, {"B2": {}}, "B2", ts, self.tmp, deadline=100.0, probe=None, mcp_url=None, reset=None,
                       hold=2, seed=SEED, clock=clock)
        self.assertEqual(n, 0)                                         # 100 s < the 360 s guard
        rows = PR.read_jsonl(os.path.join(self.tmp, "solo.jsonl"))
        self.assertEqual([r["hold"] for r in rows], [1] * 6)


if __name__ == "__main__":
    unittest.main(verbosity=1)
