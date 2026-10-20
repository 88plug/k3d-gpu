#!/usr/bin/env python3
"""Unit tests for the H43 and H45 wiring (stub servers only: no cluster, no docker, no root).

H43: the coding agent's memory purge before every task (pre_task), the sandbox router,
the SDK compatibility shim, and the pre-registered stretch scorer.
H45: the ramp split across holds, the per-arm agents-per-box from several holds, the
phased load windows, and the pre-registered prediction scorer."""
import http.server
import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agentbench as AB  # noqa: E402
import sbrouter  # noqa: E402
import tasks as T  # noqa: E402
import test_agentbench as TA  # noqa: E402
from test_agentbench import serve  # noqa: E402


# ---- H43: memory purge before every task --------------------------------------------

class PreTaskTest(unittest.TestCase):
    setUp, tearDown, agent = TA.BlockTest.setUp, TA.BlockTest.tearDown, TA.BlockTest.agent

    def test_pre_task_runs_before_the_warmup_and_every_task_outside_its_time(self):
        seen = []
        rows = AB.run_block(self.agent(), T.B24[:2], "A1", "typical", 1, self.out, 60, self.log, settle_s=0.05,
                            warmup="warm", pre_task=lambda: seen.append(time.time()))
        self.assertEqual(len(seen), 3)                       # warm-up + 2 tasks
        self.assertLess(seen[1], rows[0]["t_start"])
        self.assertLess(seen[2], rows[1]["t_start"])
        self.assertGreater(seen[2], rows[0]["t_end"])

    def test_pre_task_failure_aborts_the_block(self):
        def boom():
            raise RuntimeError("redis down")
        with self.assertRaises(AB.BlockAborted) as cm:
            AB.run_block(self.agent(), T.B24[:2], "A1", "typical", 1, self.out, 60, self.log, settle_s=0.05,
                         pre_task=boom)
        self.assertIn("pre-task", cm.exception.reason)
        self.assertEqual(cm.exception.rows, [])

    def test_cli_pre_task_command_must_exit_zero(self):
        srv, url = serve(TA.make_devui(self.purl))
        self.addCleanup(srv.shutdown)
        os.environ["DEVUI_AUTH_TOKEN"] = "tok"
        self.addCleanup(os.environ.pop, "DEVUI_AUTH_TOKEN")
        mark = os.path.join(self.tmp, "purged")
        rc = AB.main(["run", "--arm", "A1", "--level", "none", "--out", self.out, "--agent-url", url,
                      "--tasks", "B24", "--ids", "B-T1-U1001", "--proxy-log", self.log, "--no-energy",
                      "--no-load-sample", "--no-warmup", "--pre-task", f"sh -c 'echo x >> {mark}'"])
        self.assertEqual(rc, 0)
        with open(mark) as f:
            self.assertEqual(f.read().count("x"), 1)
        rc = AB.main(["run", "--arm", "A2", "--level", "none", "--out", self.out, "--agent-url", url,
                      "--tasks", "B24", "--ids", "B-T1-U1001", "--proxy-log", self.log, "--no-energy",
                      "--no-load-sample", "--no-warmup", "--pre-task", "sh -c 'exit 3'"])
        self.assertEqual(rc, 1)


# ---- H43: stretch scorer -------------------------------------------------------------

def write_rows(d, rows):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "results.jsonl"), "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def row(task, level, dur, passed=True, status=None, arm="A1"):
    status = status or ("pass" if passed else "fail")
    return {"task": task, "arm": arm, "level": level, "n": 1, "status": status, "passed": passed,
            "censored": status == "censored", "duration_s": dur, "strict": passed, "calls": []}


class StretchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.typ = os.path.join(self.tmp, "typical")
        self.hvy = os.path.join(self.tmp, "heavy")

    def test_paired_on_ids_completed_at_both_levels(self):
        write_rows(self.typ, [row(f"C{i:02d}", "typical", 10.0) for i in range(1, 9)]
                   + [row("C09", "typical", 5.0, status="censored", passed=False)])
        write_rows(self.hvy, [row(f"C{i:02d}", "heavy", 12.0) for i in range(1, 7)]
                   + [row("C07", "heavy", 99.0, status="censored", passed=False),
                      row("C09", "heavy", 500.0), row("C01", "heavy", 1.0, arm="A3")])
        r = AB.stretch([self.typ, self.hvy], "A1")
        self.assertEqual(r["n"], 6)                          # C01-C06; C07 censored, C08 heavy missing, C09 typical censored
        self.assertAlmostEqual(r["ratio"], 1.2)
        self.assertAlmostEqual(r["delta_success"], 0.0)
        self.assertEqual(r["verdict"], "SUPPORTED")

    def test_a_timed_out_task_is_completed_and_failed(self):
        write_rows(self.typ, [row(f"C{i:02d}", "typical", 10.0) for i in range(1, 5)])
        write_rows(self.hvy, [row(f"C{i:02d}", "heavy", 30.0) for i in range(1, 4)]
                   + [row("C04", "heavy", 600.0, passed=False, status="fail")])
        r = AB.stretch([self.typ, self.hvy], "A1")
        self.assertEqual(r["n"], 4)
        self.assertAlmostEqual(r["delta_success"], -0.25)
        self.assertGreater(r["ci95"][0], 1.5)
        self.assertEqual(r["verdict"], "REFUTED")

    def test_success_drop_alone_is_not_supported(self):
        write_rows(self.typ, [row(f"C{i:02d}", "typical", 10.0) for i in range(1, 6)])
        write_rows(self.hvy, [row(f"C{i:02d}", "heavy", 11.0, passed=i > 1) for i in range(1, 6)])
        r = AB.stretch([self.typ, self.hvy], "A1")
        self.assertAlmostEqual(r["delta_success"], -0.2)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_ci_straddling_the_bar_is_inconclusive(self):
        write_rows(self.typ, [row(f"C{i:02d}", "typical", 10.0) for i in range(1, 7)])
        write_rows(self.hvy, [row(f"C{i:02d}", "heavy", d) for i, d in
                              zip(range(1, 7), (11.0, 13.0, 16.0, 17.0, 19.0, 25.0))])
        r = AB.stretch([self.typ, self.hvy], "A1")
        self.assertGreater(r["ratio"], 1.5)
        self.assertLessEqual(r["ci95"][0], 1.5)
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_minus_ten_points_exactly_still_holds(self):
        write_rows(self.typ, [row(f"C{i:02d}", "typical", 10.0) for i in range(1, 11)])
        write_rows(self.hvy, [row(f"C{i:02d}", "heavy", 10.0, passed=i > 1) for i in range(1, 11)])
        self.assertEqual(AB.stretch([self.typ, self.hvy], "A1")["verdict"], "SUPPORTED")

    def test_no_shared_completed_id(self):
        write_rows(self.typ, [row("C01", "typical", 10.0)])
        write_rows(self.hvy, [row("C01", "heavy", 10.0, status="censored", passed=False)])
        r = AB.stretch([self.typ, self.hvy], "A1")
        self.assertEqual(r["n"], 0)
        self.assertIsNone(r["ratio"])
        self.assertEqual(r["verdict"], "INCONCLUSIVE")


# ---- H43: sandbox router -------------------------------------------------------------

class Sandbox(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Sandbox.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        out = json.dumps({"stdout": "42\n", "stderr": "", "exit_code": 0}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class RouterTest(unittest.TestCase):
    def setUp(self):
        Sandbox.seen = []
        self.sb, self.sb_url = serve(Sandbox)
        self.addCleanup(self.sb.shutdown)
        self.targets = []
        port = self.sb.server_address[1]

        def resolve(headers):
            self.targets.append(sbrouter.target(headers))
            return f"http://127.0.0.1:{port}"
        self.rt = sbrouter.serve("127.0.0.1", 0, token="t0k", resolve=resolve)
        self.addCleanup(self.rt.shutdown)
        self.url = f"http://127.0.0.1:{self.rt.server_address[1]}"

    def req(self, headers, path="/execute", body=b'{"command": "python3 -c \'print(6*7)\'"}'):
        r = urllib.request.Request(self.url + path, data=body, method="POST",
                                   headers=dict({"Content-Type": "application/json"}, **headers))
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            e.close()
            return e.code, None

    def test_forwards_by_headers_and_strips_router_headers(self):
        code, out = self.req({"Authorization": "Bearer t0k", "X-Sandbox-ID": "py-abc12",
                              "X-Sandbox-Namespace": "agent-sandbox", "X-Sandbox-Port": "8888"})
        self.assertEqual((code, out["stdout"]), (200, "42\n"))
        self.assertEqual(self.targets, ["http://py-abc12.agent-sandbox.svc.cluster.local:8888"])
        fwd = {k.lower() for k in Sandbox.seen[0]["headers"]}
        self.assertFalse(fwd & {"x-sandbox-id", "x-sandbox-namespace", "x-sandbox-port", "authorization"})
        self.assertEqual(Sandbox.seen[0]["path"], "/execute")
        self.assertIn(b"print(6*7)", Sandbox.seen[0]["body"])

    def test_pod_ip_wins_over_the_service_name(self):
        self.req({"Authorization": "Bearer t0k", "X-Sandbox-ID": "py-abc12", "X-Sandbox-Pod-IP": "10.42.0.17"})
        self.assertEqual(self.targets, ["http://10.42.0.17:8888"])

    def test_auth_and_routing_errors(self):
        self.assertEqual(self.req({"X-Sandbox-ID": "py-abc12"})[0], 401)
        self.assertEqual(self.req({"Authorization": "Bearer nope", "X-Sandbox-ID": "py-abc12"})[0], 401)
        self.assertEqual(self.req({"Authorization": "Bearer t0k"})[0], 400)
        self.assertEqual(self.req({"Authorization": "Bearer t0k", "X-Sandbox-ID": "Bad_Name"})[0], 400)
        self.assertEqual(self.req({"Authorization": "Bearer t0k", "X-Sandbox-ID": "ok",
                                   "X-Sandbox-Pod-IP": "127.0.0.1"})[0], 400)
        self.assertEqual(Sandbox.seen, [])

    def test_unreachable_sandbox_is_502(self):
        rt = sbrouter.serve("127.0.0.1", 0, token=None, resolve=lambda h: "http://127.0.0.1:9")
        self.addCleanup(rt.shutdown)
        self.url = f"http://127.0.0.1:{rt.server_address[1]}"
        self.assertEqual(self.req({"X-Sandbox-ID": "py-abc12"})[0], 502)

    def test_manifest(self):
        m = sbrouter.manifest()
        self.assertIn(AB.lock()["images"]["proxy_python"]["ref"], m)
        for s in ("name: sandbox-router-svc", "namespace: agent-sandbox-system", "app: sandbox-router",
                  "sbrouter.py: |", "ROUTER_AUTH_TOKEN"):
            self.assertIn(s, m)


# ---- H43: SDK compatibility shim ----------------------------------------------------

class ShimTest(unittest.TestCase):
    def test_template_kwarg_maps_to_the_warm_pool(self):
        calls = []

        class SandboxClient:
            def create_sandbox(self, warmpool, namespace="default", sandbox_ready_timeout=180):
                calls.append((warmpool, namespace))
                return "sb"
        fake = types.ModuleType("k8s_agent_sandbox")
        fake.SandboxClient = SandboxClient
        sys.modules["k8s_agent_sandbox"] = fake
        self.addCleanup(sys.modules.pop, "k8s_agent_sandbox")
        spec = importlib.util.spec_from_file_location("h43_shim", os.path.join(HERE, "coding", "sitecustomize.py"))
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        c = SandboxClient()
        self.assertEqual(c.create_sandbox(template="python-ecores", namespace="agent-sandbox"), "sb")
        c.create_sandbox(template="tmpl", namespace="ns", warmpool="pool")
        c.create_sandbox("direct", namespace="ns")
        self.assertEqual(calls, [("python-ecores", "agent-sandbox"), ("pool", "ns"), ("direct", "ns")])


# ---- H45: ramp across holds ----------------------------------------------------------

class RampTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.log = os.path.join(self.tmp, "p.jsonl")

    def blk(self, e2e_for, success=1.0, calls=None):
        def blk(agent, ts, arm, level, n, out, step_s, plog, **kw):
            if calls is not None:
                calls.append((n, kw.get("warmup")))
            with open(self.log, "w") as f:
                f.writelines(json.dumps({"ts_send": time.time(), "e2e_s": e2e_for(n)}) + "\n" for _ in range(80))
            k = int(round(10 * success))
            return [{"passed": True, "censored": False}] * k + [{"passed": False, "censored": False}] * (10 - k)
        return blk

    def test_second_hold_uses_the_first_holds_base_success(self):
        calls = []
        h2 = os.path.join(self.tmp, "h2")
        os.makedirs(h2)
        res = AB.run_apb(None, "A3", "typical", h2, 1, self.log, ramp=(4, 8), base=1.0, warmup_s=-10,
                         block=self.blk(lambda n: 1.0, success=0.8, calls=calls), warmup="w")
        self.assertEqual(calls, [(4, "w")])                   # 0.8 < 1.0 - 0.10: N=4 fails, N=8 not run
        self.assertFalse(res["steps"][0]["checks"]["success_ok"])
        self.assertEqual(res["ramp"], [4, 8])
        res = AB.run_apb(None, "A3", "typical", h2, 1, self.log, ramp=(4, 8), base=0.85, warmup_s=-10,
                         block=self.blk(lambda n: 1.0, success=0.8))
        self.assertEqual([s["n"] for s in res["steps"]], [4, 8])

    def test_a_ramp_not_starting_at_one_needs_a_base(self):
        with self.assertRaises(ValueError):
            AB.run_apb(None, "A3", "typical", self.tmp, 1, self.log, ramp=(4, 8), warmup_s=-10,
                       block=self.blk(lambda n: 1.0))

    def hold(self, name, ramp, base=None, e2e=lambda n: 1.0):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        AB.run_apb(None, "A1", "typical", d, 1, self.log, ramp=ramp, base=base, warmup_s=-10,
                   block=self.blk(e2e))
        return d

    def test_apb_from_two_holds(self):
        h1 = self.hold("h1", (1, 2))
        self.assertEqual(AB.apb_base(h1), 1.0)
        h2 = self.hold("h2", (4, 8), base=AB.apb_base(h1), e2e=lambda n: 1.0 if n < 8 else 20.0)
        r = AB.apb_from_holds([h1, h2])
        self.assertEqual((r["apb"], r["bracket"], r["complete"]), (4, [4, 4], True))

    def test_first_hold_alone(self):
        r = AB.apb_from_holds([self.hold("h1", (1, 2), e2e=lambda n: 1.0 if n == 1 else 20.0)])
        self.assertEqual((r["apb"], r["bracket"], r["complete"]), (1, [1, 1], True))
        r = AB.apb_from_holds([self.hold("h1b", (1, 2))])       # hold 2 not run: [2, 8]
        self.assertEqual((r["apb"], r["bracket"], r["complete"]), (2, [2, 8], False))
        self.assertEqual(AB.apb_from_holds([])["bracket"], [0, 8])

    def test_a_hold_after_a_failed_step_is_refused(self):
        h1 = self.hold("h1", (1, 2), e2e=lambda n: 1.0 if n == 1 else 20.0)
        h2 = self.hold("h2", (4, 8), base=1.0)
        with self.assertRaises(ValueError):
            AB.apb_from_holds([h1, h2])

    def test_load_sampler_writes_phased_windows(self):
        seen = []

        def window(seconds, cpus, **kw):
            seen.append(kw)
            return {"tag": kw.get("tag"), "phase": kw.get("phase")}
        lm = types.SimpleNamespace(window=window, load_cpus=lambda: [16, 17])
        orig = AB._load_mod
        AB._load_mod = lambda: lm
        self.addCleanup(setattr, AB, "_load_mod", orig)
        s = AB.LoadSampler(os.path.join(self.tmp, "l.jsonl"), "C2", "typical", 0.01, phase="during")
        s.start()
        time.sleep(0.05)
        s.stop()
        s.join(1)
        self.assertTrue(seen and all(k["phase"] == "during" for k in seen))
        self.assertEqual(AB.read_jsonl(os.path.join(self.tmp, "l.jsonl"))[0]["phase"], "during")


# ---- H45: prediction scorer ----------------------------------------------------------

MATCHED = {"matched": True, "verdict": "MATCHED"}
UNMATCHED = {"matched": False, "verdict": "INCONCLUSIVE: load unmatched (pre busy_pct spread 9 > 5)"}


def br(lo, hi=None):
    return {"bracket": [lo, lo if hi is None else hi]}


class H45ScoreTest(unittest.TestCase):
    def test_all_predictions_hold(self):
        r = AB.h45_score({"A3": br(8), "A1": br(2), "C1": br(1), "C2": br(1)}, MATCHED)
        self.assertEqual(r["arms"]["A1"]["status"], "holds")
        self.assertEqual(r["c2_le_a1"]["status"], "holds")
        self.assertEqual(r["verdict"], "SUPPORTED")

    def test_one_arm_outside_its_range_refutes(self):
        r = AB.h45_score({"A3": br(8), "A1": br(0), "C1": br(0), "C2": br(0)}, MATCHED)
        self.assertEqual(r["arms"]["A1"]["status"], "refuted")
        self.assertEqual(r["verdict"], "REFUTED")

    def test_a_bracket_inside_holds_and_straddling_decides_nothing(self):
        r = AB.h45_score({"A3": br(4, 8), "A1": br(2, 8), "C1": br(0), "C2": br(0)}, MATCHED)
        self.assertEqual(r["arms"]["A3"]["status"], "undecided")
        self.assertEqual(r["arms"]["A1"]["status"], "undecided")
        self.assertEqual(r["c2_le_a1"]["status"], "holds")
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        r = AB.h45_score({"A3": br(2, 4), "A1": br(1), "C1": br(0), "C2": br(0)}, MATCHED)
        self.assertEqual(r["arms"]["A3"]["status"], "refuted")      # [2, 4] cannot reach 8

    def test_c2_vs_a1_needs_matched_load(self):
        r = AB.h45_score({"A3": br(8), "A1": br(2), "C1": br(1), "C2": br(1)}, UNMATCHED)
        self.assertEqual(r["c2_le_a1"]["status"], "unmatched")
        self.assertEqual(r["verdict"], "INCONCLUSIVE")
        r = AB.h45_score({"A3": br(8), "A1": br(1), "C1": br(1), "C2": br(2)}, MATCHED)
        self.assertEqual(r["c2_le_a1"]["status"], "refuted")
        self.assertEqual(r["verdict"], "REFUTED")
        r = AB.h45_score({"A3": br(8), "A1": br(1), "C1": br(1), "C2": br(2)}, None)
        self.assertEqual(r["c2_le_a1"]["status"], "unmatched")

    def test_cli_apb_score_reads_holds_and_load(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)

        def hold(name, arm, steps, phase_busy):
            d = os.path.join(tmp, name)
            os.makedirs(d)
            with open(os.path.join(d, "apb.json"), "w") as f:
                json.dump({"arm": arm, "steps": [{"n": n, "pass": p, "checks": {"success": 1.0}} for n, p in steps]}, f)
            with open(os.path.join(d, "load.jsonl"), "w") as f:
                for ph in ("pre", "during"):
                    f.write(json.dumps({"tag": arm, "level": "typical", "phase": ph, "busy_pct": phase_busy,
                                        "throttle_per_min": 0, "swap_used_mib": 100, "swap_delta_mib": 0,
                                        "load_expected": 3, "load_running": 3}) + "\n")
            return d
        a1 = hold("a1", "A1", [(1, True), (2, False)], 50)
        c2 = hold("c2", "C2", [(1, False)], 51)
        a3a, a3b = hold("a3a", "A3", [(1, True), (2, True)], 50), hold("a3b", "A3", [(4, True), (8, True)], 50)
        out = os.path.join(tmp, "score.json")
        rc = AB.main(["apb-score", "--arm", f"A1={a1}", "--arm", f"A3={a3a},{a3b}", "--arm", f"C2={c2}",
                      "--arm", f"C1={os.path.join(tmp, 'missing')}", "--out", out])
        self.assertEqual(rc, 0)
        with open(out) as f:
            r = json.load(f)
        self.assertEqual(r["arms"]["A3"]["status"], "holds")
        self.assertEqual(r["arms"]["A1"]["bracket"], [1, 1])
        self.assertEqual(r["arms"]["C1"]["bracket"], [0, 8])       # no hold ran: decides nothing
        self.assertTrue(r["load"]["matched"])
        self.assertEqual(r["c2_le_a1"]["status"], "holds")
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_cli_stretch(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        write_rows(os.path.join(tmp, "t"), [row(f"C{i:02d}", "typical", 10.0) for i in range(1, 4)])
        write_rows(os.path.join(tmp, "h"), [row(f"C{i:02d}", "heavy", 13.0) for i in range(1, 4)])
        out = os.path.join(tmp, "s.json")
        self.assertEqual(AB.main(["stretch", os.path.join(tmp, "t"), os.path.join(tmp, "h"), "--arm", "A1",
                                  "--out", out]), 0)
        with open(out) as f:
            self.assertEqual(json.load(f)["verdict"], "SUPPORTED")

    def test_missing_arm_is_undecided(self):
        r = AB.h45_score({"A3": br(8), "A1": br(2), "C1": br(1)}, MATCHED)
        self.assertEqual(r["arms"]["C2"]["status"], "undecided")
        self.assertEqual(r["c2_le_a1"]["status"], "undecided")
        self.assertEqual(r["verdict"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main(verbosity=1)
