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
import stats  # noqa: E402
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
    """H50 (EXPERIMENTS.md "H50-H52 pre-registration", amendments M1 and A3): the H49 design on
    the Xeon 6741P under rule L3. The hypothesis id names the design and the load rule."""

    REG = {"hyp": "H50", "rule": "L3", "seed": PR.ORDER_SEEDS["H50"]}

    def test_h50_is_the_h49_design_under_l3(self):
        self.assertEqual(PR.design("H50"), ("H49", "L3"))
        self.assertEqual(PR.design("H49b"), ("H49", "L2"))
        self.assertEqual(PR.design("H47"), ("H47", "L1"))
        with self.assertRaises(ValueError):
            PR.design("H51")
        at = L2Test.h49_like(L2Test())
        res = PR.analyze(at, pairs_of(24), "H50", "A1", "B1", registration=self.REG)
        ref = PR.analyze(at, pairs_of(24), "H49b", "A1", "B1")
        self.assertEqual(res["hypothesis"], "H50")
        self.assertEqual((res["rule"], res["verdict"]), ("L3", "SUPPORTED"))
        for k in ("paired_diff", "within_budget_success"):             # all 24 pairs match there too
            self.assertEqual(res[k], ref[k], k)
        res = PR.analyze(at, pairs_of(24, void=(21, 22, 23)), "H50", "A1", "B1", registration=self.REG)
        self.assertTrue(any("void" in g for g in res["gates"]))       # the H49 void gate applies

    def test_cli_pairs_and_report_accept_h50(self):
        self.assertIn("H50", AB.PAIRS_HYPS)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for name, rows in (("attempts.jsonl", L2Test.h49_like(L2Test())), ("pairs.jsonl", pairs_of(24))):
            with open(os.path.join(tmp, name), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
        with self.assertRaises(ValueError):                           # an L3 directory needs its registration
            AB.main(["pairs-report", tmp, "--hyp", "H50", "--arm-a", "A1", "--arm-b", "B1"])
        PR.register(tmp, "H50")
        self.assertEqual(AB.main(["pairs-report", tmp, "--hyp", "H50", "--arm-a", "A1", "--arm-b", "B1"]), 0)
        with open(os.path.join(tmp, "analysis.json")) as f:
            res = json.load(f)
        self.assertEqual((res["hypothesis"], res["rule"], res["verdict"]), ("H50", "L3", "SUPPORTED"))

    def test_live_sh_maps_h50_and_sizes_the_eat_kv_cache(self):
        import re
        with open(os.path.join(HERE, "live.sh")) as f:
            live = f.read()
        # H50 runs the H49 arms (A1 vs B1); the rule comes from pairs.py.
        self.assertIn('[ "${ARM}" = H50 ] && KV_GIB=${LIVE_EAT_KV_GIB:-40}', live)   # 40 GiB unless set
        self.assertIn('[ "${ARM}" = H50o ] && DESIGN=H50o', live)
        self.assertIn('if [ "${RULE}" != L1 ]; then', live)
        # A1 is the CPU stack even where the auto device would pick an XMX Intel GPU (crescent).
        self.assertIn("    draft=${A_DRAFT} dev=CPU\n", live)
        self.assertIn("K3D_GPU_AGENT_DEVICE=${dev} K3D_GPU_AGENT_MODEL=${TGT}", live)
        # B1's KV cache: 6 GiB by default (H49, H49b on the 125 GiB desktop); H50 runs EAT's
        # shipped value (40 GiB) through LIVE_EAT_KV_GIB.
        self.assertIn("KV_GIB=${LIVE_EAT_KV_GIB:-6}", live)
        self.assertEqual(len(re.findall(r'stage.py" values "\$\{chart\}/xeon-values.yaml" "\$\{OUT\}/pin.yaml" "\$\{KV_GIB\}"', live)), 2)
        self.assertNotRegex(live, r'pin.yaml" 6 ')

    def test_h50o_runs_eat_ovms_chart_as_arm_b3(self):
        self.assertEqual(PR.design("H50o"), ("H49", "L3"))
        self.assertIn("H50o", AB.PAIRS_HYPS)
        at = [dict(r, arm="B3") if r["arm"] == "B1" else r for r in L2Test.h49_like(L2Test())]
        res = PR.analyze(at, pairs_of(24), "H50o", "A1", "B3",
                         registration={"hyp": "H50o", "rule": "L3", "seed": PR.ORDER_SEEDS["H50o"]})
        self.assertEqual((res["rule"], res["arms"], res["verdict"]), ("L3", ["A1", "B3"], "SUPPORTED"))
        self.assertIn("paired_diff", res)
        with open(os.path.join(HERE, "live.sh")) as f:
            live = f.read()
        self.assertRegex(live, r"\n\s*H50o\) A=A1 B=B3 ;;")
        # EAT's OVMS chart: its image tag pinned to a digest, the OpenVINO model it pulls,
        # OVMS's OpenAI path /v3 for LiteLLM and the model list.
        self.assertIn("B3_TAG=2025.4@sha256:c370720007564bbdcbca13b9b714e4537068542cda512e8095259e66b617962a", live)
        self.assertIn("B3_MODEL=OpenVINO/Qwen3-4B-int4-ov\n", live)
        self.assertIn('helm template ovms-model-server "${LIVE_EAT_OVMS_CHART:?}"', live)
        self.assertIn("B_DEP=ovms-model-server B_SVC=ovms-model-server B_SPORT=8080 B_API=/v3", live)
        self.assertIn('litellm b "${SERVED_B}" "${BPORT}" "$(( P_LLM + 1 ))" "${API_B}"', live)
        self.assertIn('wait_http "http://127.0.0.1:${BPORT}${API_B}/models" 300', live)


class L3Test(unittest.TestCase):
    """Rule L3 (EXPERIMENTS.md "Crescent amendment A3"): every complete pair enters the
    statistic; a balance gate (arm must not predict a pre-window covariate) replaces the match
    filter; a two-regressor adjustment and L2's matched subset are reported, never gated; the arm
    order of pair j comes from a registered seed; one registration file per pairs directory."""

    def noisy(self, n=24, b_busy=0.0, seed=7):
        import random
        rng = random.Random(seed)
        at = []
        for j in range(n):
            # Idle-time throttle and busy noise that L2's tolerances reject most of the time,
            # drawn independently of the arm (H49b: 12-3342 events/min between idle windows).
            pa = pre(busy=48 + rng.uniform(-6, 6), thr=rng.choice([12, 155, 298, 1063, 3342]))
            pb = pre(busy=48 + b_busy + rng.uniform(-6, 6), thr=rng.choice([12, 155, 298, 1063, 3342]))
            at += [att2(j, "A1", 10.5, pa), att2(j, "B1", 10.5, pb, outcome="pass_budget" if j % 5 == 0 else "over_turn")]
        return at

    def reg(self):
        return {"hyp": "H50", "rule": "L3", "seed": PR.ORDER_SEEDS["H50"]}

    def test_arm_independent_pre_noise_keeps_every_pair(self):
        at = self.noisy()
        self.assertLess(len(PR.l2(PR.pair_table(at, pairs_of(24), "A1", "B1")[0], "A1", "B1")["matched"]), 16)
        res = PR.analyze(at, pairs_of(24), "H50", "A1", "B1", registration=self.reg())
        self.assertEqual((res["rule"], res["matched"]), ("L3", 24))
        self.assertEqual(res["gates"], [])
        self.assertTrue(res["load"]["balance_ok"], res["load"]["balance"])
        self.assertEqual(res["verdict"], "SUPPORTED")
        self.assertIn("adjusted", res["load"])                      # reported, never gated
        self.assertIn("l2_secondary", res)
        self.assertLess(res["load"]["sign_flip_p"], 0.01)

    def test_covariate_tracking_arm_is_inconclusive(self):
        res = PR.analyze(self.noisy(b_busy=8.0), pairs_of(24), "H50", "A1", "B1", registration=self.reg())
        self.assertFalse(res["load"]["balance_ok"])
        self.assertEqual(res["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("covariate tracks arm: busy_pct" in g for g in res["gates"]), res["gates"])

    def test_too_few_pairs_and_voids_still_gate(self):
        res = PR.analyze(self.noisy(n=15), pairs_of(15), "H50", "A1", "B1", registration=self.reg())
        self.assertTrue(any("complete pairs < 16" in g for g in res["gates"]), res["gates"])
        res = PR.analyze(self.noisy(), pairs_of(24, void=(21, 22, 23)), "H50", "A1", "B1", registration=self.reg())
        self.assertTrue(any("void" in g for g in res["gates"]))

    def test_registration_is_required_and_must_match(self):
        with self.assertRaises(ValueError):
            PR.analyze(self.noisy(), pairs_of(24), "H50", "A1", "B1")
        with self.assertRaises(ValueError):
            PR.analyze(self.noisy(), pairs_of(24), "H50", "A1", "B1", registration=dict(self.reg(), rule="L2"))
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        r = PR.register(tmp, "H50")
        self.assertEqual(r, self.reg())
        self.assertEqual(PR.register(tmp, "H50"), r)               # a later hold: same registration
        with self.assertRaises(PR.HoldAborted):
            PR.register(tmp, "H50o")                                 # another hypothesis in the same dir
        self.assertIsNone(PR.register(tempfile.mkdtemp(dir=tmp), "H49b"))   # L1/L2 rows: none
        self.assertFalse([n for n in os.listdir(tmp) if n.endswith(".tmp")])

    def test_an_empty_registration_is_a_clear_abort(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        open(os.path.join(tmp, "registration.json"), "w").close()      # a crash mid-write (pre-S3 code)
        with self.assertRaisesRegex(PR.HoldAborted, "is not a registration"):
            PR.register(tmp, "H50")

    def test_balance_tests_log1p_throttle(self):
        # One huge throttle reading on arm B: raw differences hand it all the weight; log1p
        # keeps it one pair among many.
        at = self.noisy()
        for r in at:
            if r["arm"] == "B1" and r["j"] == 0:
                r["pre"] = dict(r["pre"], throttle_per_min=1e9)
        table = PR.pair_table(at, pairs_of(24), "A1", "B1")[0]
        import math
        raw = [table[j]["A1"]["pre"]["throttle_per_min"] - table[j]["B1"]["pre"]["throttle_per_min"] for j in table]
        logd = [math.log1p(table[j]["A1"]["pre"]["throttle_per_min"]) - math.log1p(table[j]["B1"]["pre"]["throttle_per_min"])
                for j in table]
        got = PR.l3(table, "A1", "B1")["balance"]["throttle_per_min"]
        self.assertAlmostEqual(got["mean_diff"], round(sum(logd) / len(logd), 4))
        self.assertNotAlmostEqual(got["mean_diff"], sum(raw) / len(raw))

    def test_period_balanced_estimate_is_reported_never_gated(self):
        ps = [dict(p, order=["A1", "B1"] if p["j"] % 3 else ["B1", "A1"]) for p in pairs_of(24)]
        res = PR.analyze(self.noisy(), ps, "H50", "A1", "B1", registration=self.reg())
        pb = res["period_balanced"]
        self.assertEqual((pb["A1_first"]["n"], pb["B1_first"]["n"]), (16, 8))
        self.assertAlmostEqual(pb["estimate"], round((pb["A1_first"]["mean_d"] + pb["B1_first"]["mean_d"]) / 2, 4))
        self.assertEqual(res["gates"], [])

    def test_an_l3_row_needs_its_ledger_row(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        saved = PR.LEDGER
        self.addCleanup(setattr, PR, "LEDGER", saved)
        PR.LEDGER = os.path.join(tmp, "EXPERIMENTS.md")
        with self.assertRaisesRegex(ValueError, "pre-register it first"):
            PR.design("H50")
        self.assertEqual(PR.design("H49b"), ("H49", "L2"))           # L1/L2 rows predate the rule
        with open(PR.LEDGER, "w") as f:
            f.write("| H50 | x | PENDING |\n| H50o-extra | x |\n")
        self.assertEqual(PR.design("H50"), ("H49", "L3"))
        with self.assertRaises(ValueError):
            PR.design("H50o")                                        # "| H50o-extra |" is not "| H50o |"

    def test_seeded_order_is_deterministic_and_balanced(self):
        s = PR.ORDER_SEEDS["H50"]
        self.assertEqual([PR.order(j, "A", "B", s) for j in range(50)], [PR.order(j, "A", "B", s) for j in range(50)])
        first = sum(PR.order(j, "A", "B", s) == ("A", "B") for j in range(1000))
        self.assertTrue(450 <= first <= 550, first)
        self.assertEqual([PR.order(j, "A", "B") for j in range(4)], [("A", "B"), ("B", "A")] * 2)   # parity

    def test_replay_committed_h49b(self):
        d = os.path.join(HERE, "results", "2026-10-18", "H49b", "pairs")
        table, void = PR.pair_table(PR.read_jsonl(os.path.join(d, "attempts.jsonl")),
                                    PR.read_jsonl(os.path.join(d, "pairs.jsonl")), "A1", "B1")
        r = PR.l3(table, "A1", "B1")
        self.assertEqual(len(table), 23)
        self.assertTrue(r["balance_ok"], r["balance"])
        ok = {arm: {table[j]["task"]: int(table[j][arm]["outcome"] == "pass_budget") for j in table} for arm in ("A1", "B1")}
        pd = stats.paired_diff(ok["A1"], ok["B1"])
        self.assertAlmostEqual(pd["diff"], 0.609, places=3)
        self.assertGreaterEqual(pd["ci95"][0], 0.30)

    def test_live_sh_reads_the_rule_from_pairs_py(self):
        with open(os.path.join(HERE, "live.sh")) as f:
            live = f.read()
        self.assertEqual(live.count("RULE=L"), 0)
        self.assertIn('read -r FAMILY RULE <<< "$(python3 "${HERE}/pairs.py" design "${ARM}")"', live)
        self.assertIn('if [ "${RULE}" != L1 ]; then', live)


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
        self.assertEqual(live.count('--sampling "${SAMPLING}"'), 6)
        self.assertEqual(live.count("--set chatTemplateConfigMap.enabled=${B_CHAT}"), 2)
        # LIVE_MOUNT_B: B's weights mounted read-only (k3d-gpu K3D_GPU_VOLUMES), not copied.
        self.assertIn('K3D_GPU_VOLUMES="$(readlink -f "${LIVE_MODELS:?}/${B_DIR}"):${B_HOST}/${B_DIR}:ro"', live)
        self.assertEqual(live.count("STAGE_HOST_DIR=${B_HOST} python3"), 3)
        self.assertEqual(live.count('[ "${B_HOST}" = /var/apb-models ] && stage'), 3)
        # ...and the mounted dir must match the listing models.sh wrote after its sha256 check.
        self.assertIn('cmp -s "${OUT}/b-listing.txt" "${LIVE_MODELS}/${B_DIR}.listing"', live)
        self.assertIn('-- touch "${mnt}/${B_DIR}/.k3dgpu-rw-probe"', live)
        self.assertIn('volumeMounts[?(@.name=="model-volume")].mountPath', live)
        self.assertIn("grep -q 'Read-only file system' \"${OUT}/ro-probe.txt\"", live)
        with open(os.path.join(HERE, "crescent", "models.sh")) as f:
            models = f.read()
        self.assertIn('"${HERE}/listing.sh" "${M}/gemma-4-26B-A4B-it-qat-q4_0-unquantized" > '
                      '"${M}/gemma-4-26B-A4B-it-qat-q4_0-unquantized.listing"', models)
        self.assertIn('"${HERE}/listing.sh" "${M}/Qwen3.8-27B" > "${M}/Qwen3.8-27B.listing"', models)

    def test_listing_sees_a_changed_file(self):
        import subprocess
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for n, body in (("config.json", "{}"), ("model-00001.safetensors", "x" * 10)):
            with open(os.path.join(tmp, n), "w") as f:
                f.write(body)
        ls = lambda: subprocess.run(["bash", os.path.join(HERE, "crescent", "listing.sh"), tmp],  # noqa: E731
                                    capture_output=True, text=True, check=True).stdout
        a = ls()
        self.assertEqual(a, ls())
        self.assertIn("model-00001.safetensors 10 ", a)
        with open(os.path.join(tmp, "model-00001.safetensors"), "a") as f:
            f.write("y")
        self.assertNotEqual(a, ls())
        # The card's sampling (README "Sampling Parameters", generation_config.json), pinned.
        self.assertEqual(AB.sampling_set("agent_gemma4"),
                         {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "max_tokens": 1024,
                          "chat_template_kwargs": {"enable_thinking": False}})
        self.assertEqual(AB.sampling_set("agent_local"), {"temperature": 0, "top_p": 1, "max_tokens": 1024})


class H53Test(unittest.TestCase):
    """H53 (EXPERIMENTS.md "H53 pre-registration"): Qwen3.8-27B on the Arc Pro B70s in solo holds,
    AG (k3d-gpu auto device, one card) and B2 (EAT PR #21's xpu-qwen36-27b profile, TP 4), scored
    as block pairs under rule L3 (solo.py; test_solo.py covers the pairing)."""

    def live(self):
        with open(os.path.join(HERE, "live.sh")) as f:
            return f.read()

    def test_h53_is_an_l3_row(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        saved = PR.LEDGER
        self.addCleanup(setattr, PR, "LEDGER", saved)
        self.assertEqual(PR.design("H53"), ("H49", "L3"))           # the ledger's H53 row
        PR.LEDGER = os.path.join(tmp, "EXPERIMENTS.md")
        with self.assertRaisesRegex(ValueError, "pre-register it first"):
            PR.design("H53")                                         # a ledger without it
        with open(PR.LEDGER, "w") as f:
            f.write("| H53 | x | PENDING |\n")
        self.assertEqual(PR.design("H53"), ("H49", "L3"))
        self.assertIn("H53", AB.PAIRS_HYPS)
        with self.assertRaises(ValueError):
            PR.design("H53h")

    def test_live_sh_solo_mode(self):
        live = self.live()
        blk = live[live.index('if [ "${MODE}" = solo ]; then'):live.index('if [ "${MODE}" = pairs ]; then')]
        self.assertIn('[ "${ARM}" = H53 ] && [ "${RULE:-}" = L3 ]', blk)
        self.assertIn("case ${SOLO} in AG) need=1 ;; B2) need=${TP} ;;", blk)
        self.assertIn("TP=${LIVE_B2_TP:-4}", blk)
        # AG: k3d-gpu's own choice (auto device, default draft), and it must be on the GPU.
        self.assertIn("K3D_GPU_AGENT_DEVICE=auto K3D_GPU_AGENT_MODEL=${TGT}", blk)
        self.assertIn("K3D_GPU_AGENT_DRAFT=default", blk)
        self.assertIn("grep -q -- '--target_device=GPU' \"${OUT}/agent.yaml\" || { echo \"NOT DELIVERABLE: AG is not on the GPU\"", blk)
        # B2: PR #21's values with its catalog profile for this size, the pinned tag, TP, the ladder.
        self.assertIn("B2_TAG=v0.28.0@sha256:4756b66a077627133cee653b551f6f5eaa1b9a981b5eea13edd33fcd3b0d3ca3", live)
        self.assertIn('--set LLM_MODEL_ID="${B2_PROFILE:?}" --set tensor_parallel_size="${TP}"', blk)
        self.assertIn('"${B_DIR}" - "ladder=${LIVE_B2_LADDER:-}"', blk)
        self.assertIn('${AB} solo --hyp "${ARM}" --hold "${HOLD}" --arm "${SOLO}"', blk)
        _, sets = ModelSetTest.sets(ModelSetTest())
        q = sets["qwen38"]
        self.assertEqual((q["ALIAS"], q["TGT"], q["A_TOOL"], q["B_DIR"], q["SAMPLING"], q["B2_PROFILE"]),
                         ("Qwen/Qwen3.8-27B", "OpenVINO/Qwen3.8-27B-int4-ov", "qwen3coder", "Qwen3.8-27B",
                          "agent_qwen38", "Qwen/Qwen3.6-27B"))
        # The card's instruct (non-thinking) sampling with thinking off, the same for every arm.
        self.assertEqual(AB.sampling_set("agent_qwen38"),
                         {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5, "max_tokens": 1024,
                          "chat_template_kwargs": {"enable_thinking": False}})
        body = {"temperature": 0, "top_p": 1, "messages": []}
        proxy.pin_sampling(body, AB.sampling_set("agent_qwen38"))
        self.assertEqual((body["chat_template_kwargs"], body["temperature"]), ({"enable_thinking": False}, 0.7))

    def test_live_sh_keeps_the_arm_off_the_serving_cards(self):
        live = self.live()
        blk = live[live.index('if [ "${MODE}" = solo ]; then'):live.index('if [ "${MODE}" = pairs ]; then')]
        self.assertIn("free=${LIVE_GPU_FREE:?", blk)
        self.assertIn("resources: {limits: {gpu.intel.com/xe: 1}}", blk)
        self.assertIn('if [[ " ${free} " == *" ${bdf} "* ]]; then kubectl -n default delete pod "gpu-hold-${i}"', blk)
        self.assertIn('[[ " ${free} " == *" ${bdf} "* ]] || { echo "ABORTED: ${DEP} holds ${bdf}, not a free card"; exit 3; }', blk)
        self.assertIn('[ "${got}" -eq "${need}" ] || { echo "ABORTED: ${DEP} holds ${got} card(s), not ${need}"; exit 3; }', blk)
        i_hold, i_apply, i_check, i_run = (blk.index("gpu-hold-%s"), blk.index('kubectl apply -f "${OUT}/deploy.yaml"'),
                                          blk.index("not a free card"), blk.index("${AB} solo --hyp"))
        self.assertLess(i_hold, i_apply)
        self.assertLess(i_check, i_run)


if __name__ == "__main__":
    unittest.main(verbosity=1)
