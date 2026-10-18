#!/usr/bin/env python3
"""Tests for bench/eatflow/pairs.py: the H47/H49 within-hold interleaved runner.

Stub servers only (no cluster, no docker, no root). Regression tests required by the
H47/H49 pre-registration: pair order, the per-task bg_cpu sample, rule L1, void handling
and the 600 s pair-start guard."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agentbench as AB  # noqa: E402
import pairs as PR  # noqa: E402
import proxy  # noqa: E402
import tasks as T  # noqa: E402
from test_agentbench import LLM, MCP, make_devui, reset_llm, serve  # noqa: E402


def snap(t, busy, pods, uj=0, swap=1000.0, thr=0, ebusy=0, etot=0, load1=1.0):
    return {"t": t, "busy": busy, "ebusy": ebusy, "etot": etot, "pods": pods, "uj": [uj],
            "swap": swap, "thr": thr, "load1": load1}


def att(j, arm, outcome="pass_budget", bg=4.0, dur=10.0, swap=1000.0, idle=0.1, pkg=100.0, turns=(3.0, 4.0)):
    return {"j": j, "arm": arm, "task": T.B24[j]["id"], "outcome": outcome, "duration_s": dur,
            "idle_cpu": idle, "turns_s": list(turns),
            "window": {"bg_cpu": bg, "swap_used_mib": swap, "pkg_j": pkg}}


def pairs_of(n, hold=1, void=()):
    return [{"j": j, "task": T.B24[j]["id"], "hold": hold, "status": "void" if j in void else "complete"}
            for j in range(n)]


class OrderGuardTest(unittest.TestCase):
    def test_pair_order_is_abba(self):
        seq = [x for j in range(4) for x in PR.order(j, "A", "B")]
        self.assertEqual(seq, list("ABBAABBA"))

    def test_pair_start_guard_600s(self):
        self.assertTrue(PR.can_start(0, 600))
        self.assertFalse(PR.can_start(0, 599.9))
        self.assertEqual(PR.PAIR_GUARD_S, 600.0)
        self.assertEqual(PR.TASK_CAP_S, 240.0)
        self.assertEqual(PR.TURN_S, 120.0)


class SampleTest(unittest.TestCase):
    def test_bg_cpu_is_host_minus_both_pods(self):
        # 10 s window, host busy 6000 jiffies at 100 Hz = 6 CPU-s/s; pods 1.5 and 0.5 CPU-s/s.
        s0 = snap(0, 0, {"/a": 0, "/b": 0}, uj=0)
        s1 = snap(10, 6000, {"/a": 15_000_000, "/b": 5_000_000}, uj=500_000_000, swap=1100.0, thr=5)
        w = PR.window(s0, s1, {"A1": "/a", "B1": "/b"}, 100, [0])
        self.assertAlmostEqual(w["host_cpu"], 6.0)
        self.assertEqual(w["pod_cpu"], {"A1": 1.5, "B1": 0.5})
        self.assertAlmostEqual(w["bg_cpu"], 4.0)
        self.assertAlmostEqual(w["pkg_j"], 500.0)
        self.assertAlmostEqual(w["pkg_w"], 50.0)
        self.assertAlmostEqual(w["swap_growth_mib_per_min"], 600.0)
        self.assertAlmostEqual(w["throttle_per_min"], 30.0)

    def test_shared_pod_is_subtracted_once(self):
        s0 = snap(0, 0, {"/p": 0})
        s1 = snap(10, 6000, {"/p": 20_000_000})
        w = PR.window(s0, s1, {"A1": "/p", "A4": "/p"}, 100, [0])
        self.assertAlmostEqual(w["bg_cpu"], 4.0)
        self.assertEqual(w["pod_cpu"], {"A1": 2.0, "A4": 2.0})

    def test_unreadable_pod_leaves_bg_cpu_unmeasured(self):
        w = PR.window(snap(0, 0, {"/a": None, "/b": 0}), snap(10, 600, {"/a": None, "/b": 0}),
                      {"A1": "/a", "B1": "/b"}, 100, [0])
        self.assertIsNone(w["bg_cpu"])

    def test_busy_jiffies_skips_aggregate_and_idle(self):
        st = "cpu  9 9 9 9 9 9 9 9\ncpu0 10 0 5 100 7 1 1 0 0 0\ncpu1 1 0 0 50 0 0 0 0\n"
        self.assertEqual(PR.busy_jiffies(st), 17 + 1)
        self.assertEqual(PR.busy_jiffies(st, {1}), 1)
        self.assertEqual(PR.total_jiffies(st, {1}), 51)

    def test_cgroup_lookup_both_drivers(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        uid = "1234abcd-0000-1111-2222-333344445555"
        p1 = os.path.join(root, "system.slice", "docker-x.scope", "kubepods", "burstable", "pod" + uid)
        os.makedirs(p1)
        with open(os.path.join(p1, "cpu.stat"), "w") as f:
            f.write("usage_usec 4242\nuser_usec 1\n")
        self.assertEqual(PR.find_pod_cgroup(uid, root), p1)
        self.assertEqual(PR.cgroup_usage_usec(p1), 4242)
        uid2 = "aaaa-bbbb"
        p2 = os.path.join(root, "kubepods.slice", "kubepods-burstable.slice", "kubepods-burstable-podaaaa_bbbb.slice")
        os.makedirs(p2)
        self.assertEqual(PR.find_pod_cgroup(uid2, root), p2)
        self.assertIsNone(PR.find_pod_cgroup("nope", root))
        self.assertIsNone(PR.cgroup_usage_usec(os.path.join(root, "missing")))


class OutcomeTest(unittest.TestCase):
    def test_outcomes(self):
        self.assertEqual(PR.outcome(True, None, 30, [{"e2e_s": 10}]), "pass_budget")
        self.assertEqual(PR.outcome(False, None, 30, [{"e2e_s": 10}]), "fail_graded")
        self.assertEqual(PR.outcome(True, None, 130, [{"e2e_s": 120.5}]), "over_turn")
        self.assertEqual(PR.outcome(False, None, 130, [{"error": "TimeoutError: timed out", "e2e_s": 120.0}]),
                         "over_turn")
        self.assertEqual(PR.outcome(False, "TimeoutError: timed out", 240.1, [{"e2e_s": 100}]), "over_task")
        # a turn timeout wins over the cap: it failed the turn budget first
        self.assertEqual(PR.outcome(False, "timed out", 240.1, [{"e2e_s": 121}]), "over_turn")
        self.assertEqual(PR.outcome(True, None, 10, [], void_reason="sidecar down"), "void")


class L1Test(unittest.TestCase):
    def test_pair_matched_within_08_cpu(self):
        table, void = PR.pair_table([att(0, "A1", bg=4.0), att(0, "B1", bg=4.8),
                                     att(1, "A1", bg=4.0), att(1, "B1", bg=4.81)], pairs_of(2), "A1", "B1")
        self.assertEqual(void, 0)
        r = PR.l1(table, "A1", "B1")
        self.assertEqual(r["matched"], [0])
        self.assertEqual(r["unmatched"], [1])
        self.assertTrue(r["arm_ok"])

    def test_arm_level_swap_and_bg_spread(self):
        at = []
        for j in range(4):
            at += [att(j, "A1", bg=4.0 + 0.7 * (j % 2), swap=1000), att(j, "B1", bg=4.7 - 0.7 * (j % 2), swap=1300)]
        r = PR.l1(PR.pair_table(at, pairs_of(4), "A1", "B1")[0], "A1", "B1")
        self.assertEqual(len(r["matched"]), 4)
        self.assertFalse(r["arm_ok"])
        self.assertIn("swap", r["reasons"][0])
        at = []
        for j in range(4):
            at += [att(j, "A1", bg=4.0), att(j, "B1", bg=4.79)]
        self.assertTrue(PR.l1(PR.pair_table(at, pairs_of(4), "A1", "B1")[0], "A1", "B1")["arm_ok"])

    def test_missing_bg_is_unmatched(self):
        a = att(0, "B1")
        a["window"]["bg_cpu"] = None
        r = PR.l1(PR.pair_table([att(0, "A1"), a], pairs_of(1), "A1", "B1")[0], "A1", "B1")
        self.assertEqual(r["unmatched"], [0])


class VoidTest(unittest.TestCase):
    def test_void_and_half_pairs_are_dropped_and_counted(self):
        at = [att(0, "A1"), att(0, "B1"), att(1, "A1"), att(1, "B1"), att(2, "A1")]
        ps = pairs_of(2, void=(1,))      # pair 2 has no pairs.jsonl row: the hold was killed mid-pair
        table, void = PR.pair_table(at, ps, "A1", "B1")
        self.assertEqual(sorted(table), [0])
        self.assertEqual(void, 2)

    def test_h49_more_than_two_void_is_inconclusive(self):
        at, ps = [], pairs_of(23, void=(20, 21, 22))
        for j in range(20):
            at += [att(j, "A1"), att(j, "B1", outcome="over_turn")]
        res = PR.analyze(at, ps, "H49", "A1", "B1")
        self.assertEqual(res["void_pairs"], 3)
        self.assertEqual(res["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("void" in g for g in res["gates"]))


class AnalyzeTest(unittest.TestCase):
    def h49(self, b_outcome, n=24, a_fail=0, idle=0.1):
        at = []
        for j in range(n):
            at += [att(j, "A1", outcome="fail_graded" if j < a_fail else "pass_budget", idle=idle),
                   att(j, "B1", outcome=b_outcome(j), idle=idle)]
        return PR.analyze(at, pairs_of(n), "H49", "A1", "B1")

    def test_h49_supported(self):
        res = self.h49(lambda j: "over_turn" if j % 6 else "pass_budget", a_fail=3)
        self.assertEqual(res["matched"], 24)
        self.assertEqual(res["verdict"], "SUPPORTED", res)
        self.assertEqual(res["within_budget_success"]["A1"]["k"], 21)
        self.assertEqual(res["outcomes_matched"]["B1"]["over_turn"], 20)
        self.assertGreater(res["confidence"]["support_share"], 0.95)

    def test_h49_refuted(self):
        res = self.h49(lambda j: "pass_budget" if j > 1 else "fail_graded", a_fail=4)
        self.assertEqual(res["verdict"], "REFUTED", res["paired_diff"])

    def test_h49_idle_arm_busy_is_inconclusive(self):
        res = self.h49(lambda j: "over_turn", idle=0.9)
        self.assertEqual(res["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("idle" in g for g in res["gates"]))

    def test_h49_too_few_matched(self):
        res = self.h49(lambda j: "over_turn", n=15)
        self.assertEqual(res["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("matched pairs" in g for g in res["gates"]))

    def test_h47_gain_and_refute(self):
        at = []
        for j in range(24):
            at += [att(j, "A1", dur=20.0), att(j, "A4", dur=15.0 + (j % 3) * 0.5)]
        res = PR.analyze(at, pairs_of(24), "H47", "A1", "A4")
        self.assertEqual(res["verdict"], "SUPPORTED")
        self.assertGreater(res["gain_ci95"][0], 0.10)
        self.assertEqual(res["delta_success"]["diff"], 0.0)
        at = []
        for j in range(24):
            at += [att(j, "A1", dur=10.0), att(j, "A4", dur=12.0 + (j % 3) * 0.1)]
        self.assertEqual(PR.analyze(at, pairs_of(24), "H47", "A1", "A4")["verdict"], "REFUTED")

    def test_turn_errors_count_as_inf(self):
        at = []
        for j in range(20):
            at += [att(j, "A1"), att(j, "B1", turns=(None, 3.0))]
        res = PR.analyze(at, pairs_of(20), "H49", "A1", "B1")
        self.assertEqual(res["turn_p95_s"]["B1"], float("inf"))


class FakeProbe:
    """Every window: bg 4 CPU, the attempting arm's pod busy, the other idle."""

    def __init__(self):
        self.t = 0.0

    def snap(self):
        self.t += 1
        return {"t": self.t}

    def window(self, s0, s1):
        return {"bg_cpu": 4.0, "pod_cpu": {"A1": 0.05, "B1": 0.05}, "swap_used_mib": 1000.0, "pkg_j": 50.0}


class QuiesceTest(unittest.TestCase):
    def test_waits_until_the_pod_idles(self):
        class Busy:
            def __init__(self):
                self.cpus = [3.0, 2.0, 0.4]

            def snap(self):
                return {}

            def window(self, s0, s1):
                return {"pod_cpu": {"B1": self.cpus.pop(0) if len(self.cpus) > 1 else self.cpus[0]}}
        waited, settled = PR.quiesce(Busy(), "B1", 10, step_s=0.01)
        self.assertTrue(settled)
        b = Busy()
        b.cpus = [3.0]
        self.assertFalse(PR.quiesce(b, "B1", 0.03, step_s=0.01)[1])


class HoldTest(unittest.TestCase):
    def setUp(self):
        reset_llm()
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "pairs")
        self.up, up_url = serve(LLM)
        self.arms = {}
        for arm in ("A1", "B1"):
            log = os.path.join(self.tmp, f"proxy-{arm}.jsonl")
            srv = proxy.serve(proxy.Proxy(up_url, log))
            self.addCleanup(srv.shutdown)
            dsrv, durl = serve(make_devui(f"http://127.0.0.1:{srv.server_address[1]}"))
            self.addCleanup(dsrv.shutdown)
            self.arms[arm] = {"agent": AB.Agent(durl, token="tok"), "proxy_log": log, "restart": None}
        MCP.balances = dict(T.AVAILABLE)
        self.msrv, self.murl = serve(MCP)

    def tearDown(self):
        self.up.shutdown()
        self.msrv.shutdown()
        shutil.rmtree(self.tmp)

    def clock(self, start=1000.0):
        st = {"t": start}

        def c():
            st["t"] += 1.0
            return st["t"]
        return c, st

    def test_hold_runs_abba_resumes_and_respects_guard(self):
        clock, st = self.clock()
        ts = T.B24[:3]
        # the first pair starts with exactly 600 s left; every later clock read is past the guard
        n = PR.run_hold(AB, self.arms, "A1", "B1", ts, self.out, deadline=1000 + 1 + 600, probe=FakeProbe(),
                        mcp_url=self.murl, reset="true", hold=1, settle_s=0.05, clock=clock)
        self.assertEqual(n, 1)
        at = PR.read_jsonl(os.path.join(self.out, "attempts.jsonl"))
        ps = PR.read_jsonl(os.path.join(self.out, "pairs.jsonl"))
        self.assertEqual(len(ps), 1)                         # the guard stopped the hold after pair 0
        self.assertEqual(len(at), 2)
        self.assertTrue(all(r["window"]["bg_cpu"] == 4.0 for r in at))
        first = PR.next_pair(self.out)
        self.assertEqual(first, len(ps))
        n2 = PR.run_hold(AB, self.arms, "A1", "B1", ts, self.out, deadline=1e12, probe=FakeProbe(),
                         mcp_url=self.murl, reset="true", hold=2, settle_s=0.05)
        ps = PR.read_jsonl(os.path.join(self.out, "pairs.jsonl"))
        at = PR.read_jsonl(os.path.join(self.out, "attempts.jsonl"))
        self.assertEqual([r["arm"] for r in at], ["A1", "B1", "B1", "A1", "A1", "B1"])   # A B B A A B
        for r in at[2:]:                                     # real clock: the proxy records attribute
            self.assertEqual(r["llm_requests"], 2)
            self.assertIn(r["outcome"], ("pass_budget", "fail_graded"))
        self.assertEqual([p["j"] for p in ps], [0, 1, 2])   # resumed, never repeated
        self.assertEqual(ps[-1]["hold"], 2)
        self.assertEqual(n + n2, 3)

    def test_mutating_task_resets_and_failed_reset_voids_and_aborts(self):
        t6 = [t for t in T.B24 if t["template"] == "T6"][:1]
        with self.assertRaises(PR.HoldAborted):
            PR.run_hold(AB, self.arms, "A1", "B1", t6, self.out, deadline=1e12, probe=FakeProbe(),
                        mcp_url=self.murl, reset="false", hold=1, settle_s=0.05)
        ps = PR.read_jsonl(os.path.join(self.out, "pairs.jsonl"))
        self.assertEqual(ps[0]["status"], "void")
        self.assertIn("reset", ps[0]["void_reason"])

    def test_unreachable_agent_is_void(self):
        self.arms["B1"]["agent"] = AB.Agent("http://127.0.0.1:9", token="tok")
        n = PR.run_hold(AB, self.arms, "A1", "B1", T.B24[:1], self.out, deadline=1e12, probe=FakeProbe(),
                        mcp_url=self.murl, reset="true", hold=1, settle_s=0.05)
        self.assertEqual(n, 0)
        ps = PR.read_jsonl(os.path.join(self.out, "pairs.jsonl"))
        self.assertEqual(ps[0]["status"], "void")

    def test_cli_report(self):
        at = []
        for j in range(24):
            at += [att(j, "A1"), att(j, "B1", outcome="over_task")]
        os.makedirs(self.out)
        for name, rows in (("attempts.jsonl", at), ("pairs.jsonl", pairs_of(24))):
            with open(os.path.join(self.out, name), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
        self.assertEqual(AB.main(["pairs-report", self.out, "--hyp", "H49", "--arm-a", "A1", "--arm-b", "B1"]), 0)
        with open(os.path.join(self.out, "analysis.json")) as f:
            self.assertEqual(json.load(f)["verdict"], "SUPPORTED")


if __name__ == "__main__":
    unittest.main(verbosity=1)
