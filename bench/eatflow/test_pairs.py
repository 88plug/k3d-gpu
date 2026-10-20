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

    def test_restart_after_cap_resolves_the_new_entity(self):
        """H49 hold 1: a restarted DevUI serves its entity under a new id; the cached id
        then drew HTTP 404 on every later attempt of that arm."""
        purl = "http://127.0.0.1:%d" % proxy.serve(proxy.Proxy(
            "http://127.0.0.1:%d" % self.up.server_address[1], self.arms["B1"]["proxy_log"])).server_address[1]
        flag = os.path.join(self.tmp, "restarted")
        base = make_devui(purl)

        class Restartable(base):
            def _eid(self):
                return "banking_agent_2" if os.path.exists(flag) else "banking_agent"

            def do_GET(self):
                self._json({"entities": [{"id": self._eid()}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if body["metadata"]["entity_id"] != self._eid():
                    return self._json({"error": "entity not found"}, 404)
                if not os.path.exists(flag):
                    import time
                    time.sleep(1.0)                      # first attempt runs past the cap
                body["metadata"]["entity_id"] = "banking_agent"
                data = json.dumps(body).encode()
                self.rfile = __import__("io").BytesIO(data)
                self.headers.replace_header("Content-Length", str(len(data)))
                return base.do_POST(self)
        srv, url = serve(Restartable)
        self.addCleanup(srv.shutdown)
        self.arms["B1"].update(agent=AB.Agent(url, token="tok"), restart=f"touch {flag}", entity=None)
        PR.run_hold(AB, self.arms, "A1", "B1", T.B24[:2], self.out, deadline=1e12, probe=FakeProbe(),
                    mcp_url=self.murl, reset="true", hold=1, settle_s=0.05, cap_s=0.5)
        at = {(r["j"], r["arm"]): r for r in PR.read_jsonl(os.path.join(self.out, "attempts.jsonl"))}
        self.assertEqual(at[(0, "B1")]["outcome"], "over_task")
        self.assertIsNone(at[(1, "B1")]["error"], at[(1, "B1")]["error"])
        self.assertEqual(at[(1, "B1")]["llm_requests"], 2)

    def test_http_404_from_the_agent_is_void(self):
        self.arms["B1"]["agent"].entity = "gone"
        self.arms["B1"]["agent"].opener = lambda req, timeout: (_ for _ in ()).throw(
            __import__("urllib.error").error.HTTPError(req.full_url, 404, "Not Found", {}, None))
        PR.run_hold(AB, self.arms, "A1", "B1", T.B24[:1], self.out, deadline=1e12, probe=FakeProbe(),
                    mcp_url=self.murl, reset="true", hold=1, settle_s=0.05)
        ps = PR.read_jsonl(os.path.join(self.out, "pairs.jsonl"))
        self.assertEqual(ps[0]["status"], "void")
        self.assertIn("404", ps[0]["void_reason"])

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


def pre(busy=37.0, thr=200.0, swap=136000.0, dswap=0.0, running=3, expected=3, settled=True):
    """One pre-window (rule L2): the load.py window with both arms idle, after the settle gap."""
    return {"phase": "pre", "level": "typical", "seconds": 10.0, "busy_pct": busy, "throttle_per_min": thr,
            "swap_used_mib": swap, "swap_delta_mib": dswap, "load_expected": expected, "load_running": running,
            "settled": settled, "settle_s": 5, "pod_cpu": {"A1": 0.01, "B1": 0.01}}


def att2(j, arm, bg, p, **kw):
    r = att(j, arm, bg=bg, **kw)
    r["pre"] = p
    return r


class L2Test(unittest.TestCase):
    """Rule L2 (H47b, H49b): pairs are matched on their pre-windows, taken with both arms
    idle; bg_cpu, package W and throttle during the attempt are outcomes, never gates."""

    def h49_like(self, n=24, b_pre=None):
        # The H49 run: B1's unpinned vLLM displaced the E-core load, so bg_cpu during B1 read
        # 0.3-4.3 CPU-s/s lower than during A1 (22 of 24 pairs). The idle pre-windows agree.
        at = []
        for j in range(n):
            at += [att2(j, "A1", 10.5, pre(busy=37.0 + 0.5 * (j % 3)), swap=150000.0),
                   att2(j, "B1", 10.5 - 0.3 - 0.17 * j, (b_pre or pre)(busy=38.0 - 0.5 * (j % 2)),
                        outcome="over_turn" if j % 6 else "pass_budget", swap=152000.0)]
        return at

    def test_unpinned_arm_depressing_during_bg_still_matches(self):
        at = self.h49_like()
        table, _ = PR.pair_table(at, pairs_of(24), "A1", "B1")
        self.assertEqual(len(PR.l1(table, "A1", "B1")["matched"]), 3)   # L1 drops 21 of 24 here
        r = PR.l2(table, "A1", "B1")
        self.assertEqual(len(r["matched"]), 24)
        self.assertTrue(r["arm_ok"], r["reasons"])
        res = PR.analyze(at, pairs_of(24), "H49b", "A1", "B1")
        self.assertEqual(res["rule"], "L2")
        self.assertEqual(res["matched"], 24)
        self.assertEqual(res["gates"], [])
        self.assertEqual(res["verdict"], "SUPPORTED")
        # during-attempt load is reported per arm, never gated
        out = res["load_during_per_arm"]
        self.assertGreater(out["A1"]["bg_cpu"] - out["B1"]["bg_cpu"], 2.0)
        self.assertIn("busy_pct", res["load_pre_per_arm"]["B1"])

    def test_background_drift_between_pre_windows_does_not_match(self):
        cases = {
            "busy_pct": dict(busy=45.1),            # 8.1 pp > 5 pp
            "throttle_per_min": dict(thr=300.0),    # 100 > max(10, 0.25 x 300)
            "swap_used_mib": dict(swap=136300.0),   # 300 MiB > 256
            "swap_delta_mib": dict(dswap=80.0),     # 80 MiB > 64
        }
        for metric, kw in cases.items():
            with self.subTest(metric=metric):
                at = [att2(0, "A1", 10.0, pre()), att2(0, "B1", 10.0, pre(**kw)),   # identical during
                      att2(1, "A1", 10.0, pre()), att2(1, "B1", 10.0, pre())]
                r = PR.l2(PR.pair_table(at, pairs_of(2), "A1", "B1")[0], "A1", "B1")
                self.assertEqual(r["matched"], [1])
                self.assertEqual(r["unmatched"], [0])
                self.assertEqual(r["unmatched_by"], {metric: 1})

    def test_tolerance_edges_match(self):
        at = [att2(0, "A1", 10.0, pre(busy=37.0, thr=8.0, swap=1000.0, dswap=0.0)),
              att2(0, "B1", 10.0, pre(busy=42.0, thr=18.0, swap=1256.0, dswap=64.0))]
        r = PR.l2(PR.pair_table(at, pairs_of(1), "A1", "B1")[0], "A1", "B1")
        self.assertEqual(r["matched"], [0])

    def test_missing_pre_or_missing_load_is_unmatched(self):
        no_pre = att(0, "B1")
        at = [att2(0, "A1", 10.0, pre()), no_pre,
              att2(1, "A1", 10.0, pre()), att2(1, "B1", 10.0, pre(running=2)),
              att2(2, "A1", 10.0, pre()), att2(2, "B1", 10.0, pre(running=None)),
              att2(3, "A1", 10.0, pre()), att2(3, "B1", 10.0, dict(pre(), busy_pct=None))]
        r = PR.l2(PR.pair_table(at, pairs_of(4), "A1", "B1")[0], "A1", "B1")
        self.assertEqual(r["matched"], [])
        self.assertEqual(r["unmatched_by"], {"pre-window missing": 1, "load missing": 2, "busy_pct": 1})

    def test_arm_level_reads_pre_windows_only(self):
        # During-attempt swap and bg_cpu differ by far more than L1 allows; pre-windows agree.
        at = []
        for j in range(20):
            at += [att2(j, "A1", 11.0, pre(), swap=100000.0), att2(j, "B1", 7.0, pre(), swap=110000.0)]
        r = PR.l2(PR.pair_table(at, pairs_of(20), "A1", "B1")[0], "A1", "B1")
        self.assertEqual(len(r["matched"]), 20)
        self.assertTrue(r["arm_ok"], r["reasons"])
        self.assertEqual(r["arm_check"]["rule"], "amendment-27")

    def test_h49b_gates_unchanged(self):
        at = self.h49_like(n=15)
        res = PR.analyze(at, pairs_of(15), "H49b", "A1", "B1")
        self.assertEqual(res["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("matched pairs < 16" in g for g in res["gates"]))
        # 18 of 24 matched (75%) passes; 17 of 24 does not
        for k, ok in ((18, True), (17, False)):
            at = self.h49_like()
            for r in at:
                if r["arm"] == "B1" and r["j"] >= k:
                    r["pre"] = pre(busy=60.0)
            res = PR.analyze(at, pairs_of(24), "H49b", "A1", "B1")
            self.assertEqual(res["matched"], k)
            self.assertEqual(not any("pairs matched <" in g for g in res["gates"]), ok, res["gates"])
        at = self.h49_like()
        res = PR.analyze(at, pairs_of(24, void=(21, 22, 23)), "H49b", "A1", "B1")
        self.assertTrue(any("void" in g for g in res["gates"]))

    def test_h47b_uses_l2(self):
        at = []
        for j in range(24):
            at += [att2(j, "A1", 11.0, pre(), dur=20.0), att2(j, "A4", 9.0, pre(), dur=15.0 + (j % 3) * 0.5)]
        res = PR.analyze(at, pairs_of(24), "H47b", "A1", "A4")
        self.assertEqual(res["rule"], "L2")
        self.assertEqual(res["verdict"], "SUPPORTED")
        self.assertEqual(PR.analyze(at, pairs_of(24), "H47", "A1", "A4")["rule"], "L1")

    def test_hold_takes_a_pre_window_before_each_attempt(self):
        calls = []

        def take(arm):
            calls.append(arm)
            return pre()
        h = HoldTest("test_cli_report")
        h.setUp()
        try:
            PR.run_hold(AB, h.arms, "A1", "B1", T.B24[:2], h.out, deadline=1e12, probe=FakeProbe(),
                        mcp_url=h.murl, reset="true", hold=1, settle_s=0.05, pre=take)
            at = PR.read_jsonl(os.path.join(h.out, "attempts.jsonl"))
        finally:
            h.tearDown()
        self.assertEqual(calls, ["A1", "B1", "B1", "A1"])
        self.assertEqual([r["arm"] for r in at], calls)
        for r in at:
            self.assertEqual(r["pre"]["phase"], "pre")
            self.assertIn("A1", r["pre"]["pod_cpu"])      # both arms' pods over the pre-window
            self.assertLessEqual(r["pre"]["t_end"], r["t_start"])

    def test_pre_window_settles_then_samples(self):
        """agentbench's L2 pre(arm): settle against the hold's baseline, then one pre_s window."""
        import types
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        base = os.path.join(tmp, "baseline.jsonl")
        with open(base, "w") as f:
            for _ in range(2):
                f.write(json.dumps({"phase": "baseline", "busy_pct": 37.0, "throttle_per_min": 100.0}) + "\n")
        seen = []
        busy = iter([60.0, 50.0, 38.0, 39.0])             # hog decay: settles on the third 5 s window

        def window(sec, cpus, tag=None, level=None, phase=None):
            seen.append((sec, tag, level, phase))
            return pre(busy=next(busy), thr=100.0)
        lm = types.SimpleNamespace(settle=PR.LOAD.settle, window=window, load_cpus=lambda: [16, 17])
        w = AB.pre_window(lm, base, "typical", 10.0)("B1")
        self.assertEqual(seen, [(5, "B1", "typical", "pre")] * 3 + [(10.0, "B1", "typical", "pre")])
        self.assertEqual((w["busy_pct"], w["settled"], w["settle_s"]), (39.0, True, 15))

    def test_cli_report_h49b(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for name, rows in (("attempts.jsonl", self.h49_like()), ("pairs.jsonl", pairs_of(24))):
            with open(os.path.join(tmp, name), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
        self.assertEqual(AB.main(["pairs-report", tmp, "--hyp", "H49b", "--arm-a", "A1", "--arm-b", "B1"]), 0)
        with open(os.path.join(tmp, "analysis.json")) as f:
            res = json.load(f)
        self.assertEqual((res["rule"], res["verdict"]), ("L2", "SUPPORTED"))


class H50Test(unittest.TestCase):
    """H50 (EXPERIMENTS.md "H50-H52 pre-registration"): H49b's design, statistic and gates on
    the Xeon 6741P. The hypothesis id names the design and the load rule, not its suffix."""

    def test_h50_is_the_h49_design_under_l2(self):
        self.assertEqual(PR.design("H50"), ("H49", "L2"))
        self.assertEqual(PR.design("H49b"), ("H49", "L2"))
        self.assertEqual(PR.design("H47"), ("H47", "L1"))
        with self.assertRaises(ValueError):
            PR.design("H51")
        at = L2Test.h49_like(L2Test())
        res = PR.analyze(at, pairs_of(24), "H50", "A1", "B1")
        ref = PR.analyze(at, pairs_of(24), "H49b", "A1", "B1")
        self.assertEqual(res["hypothesis"], "H50")
        self.assertEqual((res["rule"], res["verdict"]), ("L2", "SUPPORTED"))
        for k in ("matched", "paired_diff", "within_budget_success", "gates", "confidence"):
            self.assertEqual(res[k], ref[k], k)
        res = PR.analyze(at, pairs_of(24, void=(21, 22, 23)), "H50", "A1", "B1")
        self.assertTrue(any("void" in g for g in res["gates"]))       # the H49 void gate applies

    def test_cli_pairs_and_report_accept_h50(self):
        self.assertIn("H50", AB.PAIRS_HYPS)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for name, rows in (("attempts.jsonl", L2Test.h49_like(L2Test())), ("pairs.jsonl", pairs_of(24))):
            with open(os.path.join(tmp, name), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
        self.assertEqual(AB.main(["pairs-report", tmp, "--hyp", "H50", "--arm-a", "A1", "--arm-b", "B1"]), 0)
        with open(os.path.join(tmp, "analysis.json")) as f:
            res = json.load(f)
        self.assertEqual((res["hypothesis"], res["rule"], res["verdict"]), ("H50", "L2", "SUPPORTED"))

    def test_live_sh_maps_h50_and_sizes_the_eat_kv_cache(self):
        import re
        with open(os.path.join(HERE, "live.sh")) as f:
            live = f.read()
        # H50 runs the H49 arms (A1 vs B1) under rule L2, in its own cluster.
        self.assertIn("H50) DESIGN=H49 RULE=L2 KV_GIB=${LIVE_EAT_KV_GIB:-40} ;;", live)   # 40 GiB unless set
        self.assertRegex(live, r"\n\s*H49b\) DESIGN=H49 RULE=L2 ;;")
        self.assertIn('if [ "${RULE}" = L2 ]; then', live)
        # A1 is the CPU stack even where the auto device would pick an XMX Intel GPU (crescent).
        self.assertIn("    draft=${A_DRAFT} dev=CPU\n", live)
        self.assertIn("K3D_GPU_AGENT_DEVICE=${dev} K3D_GPU_AGENT_MODEL=${TGT}", live)
        # B1's KV cache: 6 GiB by default (H49, H49b on the 125 GiB desktop); H50 runs EAT's
        # shipped Qwen3-4B value (40 GiB) through LIVE_EAT_KV_GIB.
        self.assertIn("KV_GIB=${LIVE_EAT_KV_GIB:-6}", live)
        self.assertEqual(len(re.findall(r'stage.py" values "\$\{chart\}/xeon-values.yaml" "\$\{OUT\}/pin.yaml" "\$\{KV_GIB\}"', live)), 2)
        self.assertNotRegex(live, r'pin.yaml" 6 ')

    def test_h50o_runs_eat_ovms_chart_as_arm_b3(self):
        self.assertEqual(PR.design("H50o"), ("H49", "L2"))
        self.assertIn("H50o", AB.PAIRS_HYPS)
        at = [dict(r, arm="B3") if r["arm"] == "B1" else r for r in L2Test.h49_like(L2Test())]
        res = PR.analyze(at, pairs_of(24), "H50o", "A1", "B3")
        self.assertEqual((res["rule"], res["arms"], res["verdict"]), ("L2", ["A1", "B3"], "SUPPORTED"))
        self.assertIn("paired_diff", res)
        with open(os.path.join(HERE, "live.sh")) as f:
            live = f.read()
        self.assertRegex(live, r"\n\s*H50o\) DESIGN=H50o RULE=L2 ;;")
        self.assertRegex(live, r"\n\s*H50o\) A=A1 B=B3 ;;")
        # EAT's OVMS chart: its image tag pinned to a digest, the OpenVINO model it pulls,
        # OVMS's OpenAI path /v3 for LiteLLM and the model list.
        self.assertIn("B3_TAG=2025.4@sha256:c370720007564bbdcbca13b9b714e4537068542cda512e8095259e66b617962a", live)
        self.assertIn("B3_MODEL=OpenVINO/Qwen3-4B-int4-ov\n", live)
        self.assertIn('helm template ovms-model-server "${LIVE_EAT_OVMS_CHART:?}"', live)
        self.assertIn("B_DEP=ovms-model-server B_SVC=ovms-model-server B_SPORT=8080 B_API=/v3", live)
        self.assertIn('litellm b "${SERVED_B}" "${BPORT}" "$(( P_LLM + 1 ))" "${API_B}"', live)
        self.assertIn('wait_http "http://127.0.0.1:${BPORT}${API_B}/models" 300', live)


class ModelSetTest(unittest.TestCase):
    """Crescent amendment M1: LIVE_MODEL_SET picks every arm's model; qwen3-4b (the default)
    keeps the H47-H49b values, gemma4 is EAT's cpu-gemma4-26b-a4b with QAT weights."""

    def sets(self):
        import re
        with open(os.path.join(HERE, "live.sh")) as f:
            live = f.read()
        block = live[live.index("case ${MODEL_SET} in"):live.index("SAMPLING=${LIVE_SAMPLING:-${SAMPLING}}")]
        out = {}
        for name, body in re.findall(r"\n    ([a-z0-9-]+)\)\n(.*?);;", block, re.S):
            out[name] = dict(re.findall(r"([A-Z][A-Z0-9_]*)=(\S+)", body))
        return live, out

    def test_default_keeps_the_desktop_values(self):
        live, sets = self.sets()
        self.assertIn("MODEL_SET=${LIVE_MODEL_SET:-qwen3-4b}", live)
        q = sets["qwen3-4b"]
        self.assertEqual((q["ALIAS"], q["TGT"], q["A_TOOL"], q["A_DRAFT"], q["B_DIR"], q["B_REL"], q["B_CHAT"],
                          q["SAMPLING"]),
                         ("Qwen/Qwen3-4B-Instruct-2507", "k3dgpu/Qwen3-4B-Instruct-2507-int4-ov", "hermes3", "CPU",
                          "Qwen3-4B-Instruct-2507", "vllm-qwen-3-4b-cpu", "false", "agent_local"))

    def test_gemma4(self):
        live, sets = self.sets()
        g = sets["gemma4"]
        self.assertEqual((g["ALIAS"], g["A_TOOL"], g["A_DRAFT"], g["B_DIR"], g["B_REL"], g["B_CHAT"], g["SAMPLING"]),
                         ("google/gemma-4-26B-A4B-it", "gemma4", "off", "gemma-4-26B-A4B-it-qat-q4_0-unquantized",
                          "vllm-gemma4-26b-a4b-cpu", "true", "agent_gemma4"))
        self.assertIn('[ "${MODEL_SET}" = gemma4 ] && B3_MODEL=OpenVINO/gemma-4-26b-a4b-it-int4-ov', live)
        self.assertIn("K3D_GPU_AGENT_TOOL_PARSER=${A_TOOL}", live)
        self.assertNotIn("--sampling agent_local", live)
        self.assertEqual(live.count('--sampling "${SAMPLING}"'), 5)
        self.assertEqual(live.count("--set chatTemplateConfigMap.enabled=${B_CHAT}"), 2)
        # The card's sampling (README "Sampling Parameters", generation_config.json), pinned.
        self.assertEqual(AB.sampling_set("agent_gemma4"),
                         {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "max_tokens": 1024})
        self.assertEqual(AB.sampling_set("agent_local"), {"temperature": 0, "top_p": 1, "max_tokens": 1024})


class H53Test(unittest.TestCase):
    """H53 (EXPERIMENTS.md "H53 pre-registration"): H50's design on Arc Pro B70s with Qwen3.8-27B:
    AG (k3d-gpu auto device, one card) vs B2 (EAT PR #21 XPU path, LIVE_B2_TP cards) and B2h
    (EAT's nearest profile, xpu-qwen36-27b, on the same weights)."""

    def live(self):
        with open(os.path.join(HERE, "live.sh")) as f:
            return f.read()

    def test_h53_reads_as_h49b(self):
        for hyp, b in (("H53", "B2"), ("H53h", "B2h")):
            self.assertEqual(PR.design(hyp), ("H49", "L2"))
            self.assertIn(hyp, AB.PAIRS_HYPS)
            at = [dict(r, arm={"A1": "AG", "B1": b}[r["arm"]]) for r in L2Test.h49_like(L2Test())]
            res = PR.analyze(at, pairs_of(24), hyp, "AG", b)
            self.assertEqual((res["rule"], res["arms"], res["verdict"]), ("L2", ["AG", b], "SUPPORTED"))

    def test_live_sh_arms(self):
        live = self.live()
        self.assertRegex(live, r"\n\s*H53\) DESIGN=H53 RULE=L2 ;;")
        self.assertRegex(live, r"\n\s*H53h\) DESIGN=H53h RULE=L2 ;;")
        self.assertIn("H53) A=AG B=B2; export K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true", live)
        self.assertIn("H53h) A=AG B=B2h;", live)
        # AG: k3d-gpu's own choice (auto device, default draft); A1 stays on the CPU.
        self.assertIn('[ -n "${GPU}" ] && dev=auto draft=default', live)
        self.assertIn("K3D_GPU_AGENT_DEVICE=${dev} K3D_GPU_AGENT_MODEL=${TGT}", live)
        # B2: PR #21's values file, LIVE_B2_TP cards (2 by default), the pinned tag, stage.py xpu
        # with the boot ladder; B2h renders EAT's nearest profile for the same weights.
        self.assertIn("B2_TAG=v0.28.0@sha256:4756b66a077627133cee653b551f6f5eaa1b9a981b5eea13edd33fcd3b0d3ca3", live)
        self.assertIn('--values "${chart}/xpu-values.yaml" --values "${OUT}/pin.yaml"', live)
        self.assertIn('--set LLM_MODEL_ID="${mid}" --set tensor_parallel_size="${LIVE_B2_TP:-2}"', live)
        self.assertIn('[ "${DESIGN}" = H53h ] && mid=${B2H_PROFILE:?}', live)
        self.assertIn('stage.py" xpu "${OUT}/eat.yaml" "${OUT}/deploy-b.yaml" "${B_DIR}" 32768', live)
        self.assertIn('"ladder=${LIVE_B2_LADDER:-}" "${flags[@]}"', live)
        self.assertIn("need=$(( 1 + ${LIVE_B2_TP:-2} ))", live)
        _, sets = ModelSetTest.sets(ModelSetTest())
        q = sets["qwen38"]
        self.assertEqual((q["ALIAS"], q["TGT"], q["A_TOOL"], q["B_DIR"], q["SAMPLING"], q["B2H_PROFILE"]),
                         ("Qwen/Qwen3.8-27B", "OpenVINO/Qwen3.8-27B-int4-ov", "qwen3coder", "Qwen3.8-27B",
                          "agent_qwen38", "Qwen/Qwen3.6-27B"))
        # The card's instruct (non-thinking) sampling with thinking off, the same for every arm.
        self.assertEqual(AB.sampling_set("agent_qwen38"),
                         {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5, "max_tokens": 1024,
                          "chat_template_kwargs": {"enable_thinking": False}})
        body = {"temperature": 0, "top_p": 1, "messages": []}
        proxy.pin_sampling(body, AB.sampling_set("agent_qwen38"))
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(body["temperature"], 0.7)

    def test_live_sh_keeps_the_arms_off_the_serving_cards(self):
        live = self.live()
        # No free-card list, no hold; placeholders on every card, freed only on LIVE_GPU_FREE;
        # each GPU arm's card checked before any request.
        self.assertIn("free=${LIVE_GPU_FREE:?", live)
        self.assertIn("resources: {limits: {gpu.intel.com/xe: 1}}", live)
        self.assertIn('if [[ " ${free} " == *" ${bdf} "* ]]; then kubectl -n default delete pod "gpu-hold-${i}"', live)
        self.assertIn('[[ " ${free} " == *" ${bdf} "* ]] || { echo "ABORTED: ${obj} holds ${bdf}, not a free card"; exit 3; }', live)
        self.assertIn('for rd in $(kubectl -n "${ns}" exec "${obj}" -c "${ctr}" -- ls /dev/dri | grep renderD); do', live)
        i_hold, i_apply, i_check, i_pairs = (live.index("gpu-hold-%s"), live.index('kubectl apply -f "${OUT}/deploy-a.yaml"'),
                                            live.index("not a free card"), live.index("${AB} pairs --hyp"))
        self.assertLess(i_hold, i_apply)
        self.assertLess(i_check, i_pairs)


if __name__ == "__main__":
    unittest.main(verbosity=1)
