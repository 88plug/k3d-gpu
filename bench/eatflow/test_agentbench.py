#!/usr/bin/env python3
"""Unit tests for bench/eatflow (stub servers only: no cluster, no docker, no root)."""
import http.server
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agentbench as AB  # noqa: E402
import bfcl  # noqa: E402
import energy  # noqa: E402
import proxy  # noqa: E402
import stats  # noqa: E402
import tasks as T  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
BAL_TOOL = {"type": "function", "function": {
    "name": "get_balances", "parameters": {"type": "object", "properties": {"user_id": {"type": "string"}},
                                           "required": ["user_id"]}}}


def serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def post(url, body, timeout=10, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


# ---- a scripted OpenAI-compatible upstream ------------------------------------------

class LLM(http.server.BaseHTTPRequestHandler):
    """Streams a tool call when the last message is the user's, else a final answer.
    Body knobs: answer (str), args (dict), delay (s before the first delta)."""
    protocol_version = "HTTP/1.1"
    seen = []
    answer = "Checking 4,250.75 and savings $18,200.00."
    args = {"user_id": "U1001"}
    delay = 0.0
    tool_name = "get_balances"

    def log_message(self, *a):
        pass

    def do_GET(self):
        data = b'{"data": []}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        LLM.seen.append(body)
        if "/fail/" in self.path:
            self.send_response(500)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        last = body["messages"][-1]["role"]
        if not body.get("stream"):
            msg = ({"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": LLM.tool_name, "arguments": json.dumps(LLM.args)}}]}
                if last == "user" else {"role": "assistant", "content": LLM.answer})
            data = json.dumps({"choices": [{"message": msg}],
                               "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                                         "prompt_tokens_details": {"cached_tokens": 64}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def ev(obj):
            line = b"data: " + (obj if isinstance(obj, bytes) else json.dumps(obj).encode()) + b"\n\n"
            self.wfile.write(b"%x\r\n%s\r\n" % (len(line), line))
            self.wfile.flush()

        ev({"choices": [{"delta": {"role": "assistant"}}]})   # role-only: not a TTFT byte
        time.sleep(LLM.delay)
        if last == "user":
            a = json.dumps(LLM.args)
            ev({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": LLM.tool_name, "arguments": ""}}]}}]})
            ev({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": a[:5]}}]}}]})
            ev({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": a[5:]}}]}}]})
        else:
            ev({"choices": [{"delta": {"content": LLM.answer}}]})
        ev({"choices": [{"delta": {}, "finish_reason": "tool_calls" if last == "user" else "stop"}]})
        if (body.get("stream_options") or {}).get("include_usage"):
            ev({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 7}})
        ev(b"[DONE]")
        self.wfile.write(b"0\r\n\r\n")


def reset_llm():
    LLM.seen = []
    LLM.answer = "Checking 4,250.75 and savings $18,200.00."
    LLM.args = {"user_id": "U1001"}
    LLM.delay = 0.0
    LLM.tool_name = "get_balances"


def make_devui(llm_base, sleep=0.0):
    """A stand-in for Agent Framework DevUI: two LLM turns per task through llm_base."""
    class DevUI(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        calls = 0

        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            assert self.headers.get("Authorization") == "Bearer tok"
            self._json({"entities": [{"id": "banking_agent"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["metadata"]["entity_id"] == "banking_agent"
            DevUI.calls += 1
            time.sleep(sleep)
            msgs = [{"role": "system", "content": "bank"}, {"role": "user", "content": body["input"]}]
            post(llm_base + "/v1/chat/completions", {"model": "m", "stream": True, "messages": msgs, "tools": [BAL_TOOL]})
            msgs.append({"role": "tool", "content": "{}"})
            _, raw = post(llm_base + "/v1/chat/completions", {"model": "m", "stream": False, "messages": msgs,
                                                               "tools": [BAL_TOOL]})
            text = json.loads(raw)["choices"][0]["message"]["content"]
            self._json({"status": "completed", "output": [
                {"type": "message", "content": [{"type": "output_text", "text": text}]}]})
    return DevUI


# ---- stats --------------------------------------------------------------------------

class StatsTest(unittest.TestCase):
    def test_wilson(self):
        lo, hi = stats.wilson(85, 100)
        self.assertAlmostEqual(lo, 0.7672, places=3)
        self.assertAlmostEqual(hi, 0.9069, places=3)
        self.assertEqual(stats.wilson(0, 0), (None, None))
        lo, hi = stats.wilson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertGreater(hi, 0.2)

    def test_nearest_rank_counts_errors_as_inf(self):
        xs = [1.0] * 94 + [float("inf")] * 6
        self.assertEqual(stats.nearest_rank(xs, 95), float("inf"))
        self.assertEqual(stats.nearest_rank([3, 1, 2], 50), 2)
        self.assertIsNone(stats.nearest_rank([], 95))

    def test_paired_diff_uses_shared_ids_and_is_seeded(self):
        a = {f"t{i}": 1 for i in range(80)}
        b = {f"t{i}": (1 if i % 4 else 0) for i in range(100)}
        d = stats.paired_diff(a, b)
        self.assertEqual(d["n"], 80)
        self.assertAlmostEqual(d["diff"], 0.25)
        self.assertEqual(d, stats.paired_diff(a, b))
        self.assertLessEqual(d["ci95"][0], d["lb95_one_sided"])
        self.assertLessEqual(d["lb95_one_sided"], d["diff"])
        self.assertEqual(stats.paired_diff({}, b)["n"], 0)

    def test_ratio_ci(self):
        p, (lo, hi) = stats.ratio_ci([2.0] * 10, [10.0] * 10)
        self.assertAlmostEqual(p, 0.2)
        self.assertAlmostEqual(lo, 0.2)
        self.assertAlmostEqual(hi, 0.2)


# ---- tasks and graders --------------------------------------------------------------

class TaskSetTest(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(len(T.B24), 24)
        self.assertEqual(len({t["id"] for t in T.B24}), 24)
        self.assertEqual(sum(t["mutating"] for t in T.B24), 6)
        self.assertEqual(len(T.B15_RO), 15)
        self.assertFalse(any(t["mutating"] or t.get("refuse") for t in T.B15_RO))
        self.assertEqual(len(T.C12), 12)
        self.assertEqual(sum(1 for t in T.C12 if t.get("install")), 3)

    def test_readme_prompts_verbatim(self):
        readme = [
            "My user id is U1001. What are the balances across all my accounts?",
            "Show me the 5 most recent transactions on account ACC-1001-CHK.",
            "For user U1001, when is the next mortgage payment due and how much is left on it?",
            "I'm user U1001. Transfer $200 from ACC-1001-CHK to ACC-1001-SAV. I confirm.",
        ]
        prompts = {t["prompt"] for t in T.B24}
        for p in readme:
            self.assertIn(p, prompts)
        self.assertIn("Compute the sum of all prime numbers below 50, run it in the sandbox, and print the result.",
                      {t["prompt"] for t in T.C12})

    def test_expected_values_from_seed(self):
        self.assertEqual(T.deposit_total("U1001"), 22450.75)
        self.assertEqual(T.deposit_total("U1002"), 6580.10)
        self.assertEqual(T.deposit_total("U1003"), 12750.42)
        self.assertEqual([t[2] for t in T.recent("ACC-1001-CHK", 2)], [-52.30, -12.99])
        t6 = next(t for t in T.B24 if t["id"] == "B-T6-U1001")
        self.assertEqual(t6["end_state"]["balances"], {"ACC-1001-CHK": 4050.75, "ACC-1001-SAV": 18400.00})

    def test_seed_copy_matches_upstream_shape(self):
        # every account belongs to a seeded user, every loan to a user; amounts to the cent
        for a, (u, _) in T.ACCOUNTS.items():
            self.assertIn(u, T.USERS)
            self.assertEqual(round(T.AVAILABLE[a], 2), T.AVAILABLE[a])
        self.assertEqual(len(T.TRANSACTIONS), 14)


def call(name, valid=True, **args):
    return {"name": name, "arguments": json.dumps(args), "valid": valid}


class GraderTest(unittest.TestCase):
    def t(self, tid):
        return next(t for t in T.B24 + T.C12 if t["id"] == tid)

    def test_balances_pass_and_mutations(self):
        t = self.t("B-T1-U1001")
        good = "Everyday Checking: $4,250.75. Rainy Day Savings: $18,200.00."
        self.assertEqual(T.grade_bank(t, good, [call("get_balances", user_id="U1001")]), (True, "ok"))
        self.assertFalse(T.grade_bank(t, good.replace("4,250.75", "4,250.70"), [call("get_balances")])[0])
        self.assertFalse(T.grade_bank(t, good, [])[0])
        self.assertFalse(T.grade_bank(t, good, [call("get_balances", valid=False)])[0])
        # a namespace prefix on the MCP tool name still matches
        self.assertTrue(T.grade_bank(t, good, [call("banking_get_balances")])[0])

    def test_alternative_tools(self):
        t = self.t("B-T2-U1002")
        self.assertTrue(T.grade_bank(t, "980.10 available", [call("get_balances")])[0])
        self.assertTrue(T.grade_bank(t, "980.10 available", [call("get_account_balance")])[0])

    def test_loan_due_date_formats(self):
        t = self.t("B-T4-U1001")
        calls = [call("get_loans")]
        self.assertTrue(T.grade_bank(t, "Due August 1, 2026; $268,400.55 left.", calls)[0])
        self.assertTrue(T.grade_bank(t, "Next due 2026-08-01, remaining 268400.55", calls)[0])
        self.assertFalse(T.grade_bank(t, "Due August 11; $268,400.55 left.", calls)[0])
        t3 = self.t("B-T4-U1003")
        self.assertTrue(T.grade_bank(t3, "You have no active loans.", calls)[0])
        self.assertFalse(T.grade_bank(t3, "Your loan is due soon.", calls)[0])

    def test_mutating_needs_end_state(self):
        t = self.t("B-T6-U1001")
        calls = [call("transfer_funds")]
        ok = {"balances": {"ACC-1001-CHK": 4050.75, "ACC-1001-SAV": 18400.00}, "loans": {}}
        self.assertEqual(T.grade_bank(t, "Done.", calls, ok), (True, "ok"))
        self.assertFalse(T.grade_bank(t, "Done.", calls, None)[0])
        bad = {"balances": {"ACC-1001-CHK": 4250.75, "ACC-1001-SAV": 18200.00}, "loans": {}}
        self.assertIn("ACC-1001-CHK", T.grade_bank(t, "Done.", calls, bad)[1])

    def test_overdraft_refusal(self):
        t = self.t("B-T8-U1001")
        read = [call("get_account_balance", account_id="ACC-1001-CHK")]
        self.assertTrue(T.grade_bank(t, "Sorry, insufficient funds for that transfer.", read)[0])
        self.assertTrue(T.grade_bank(t, "Insufficient funds.", [call("transfer_funds")])[0])
        self.assertTrue(T.grade_bank(t, "Insufficient funds.", [call("get_balances", user_id="U1001")])[0])
        self.assertFalse(T.grade_bank(t, "Transfer complete.", read)[0])
        moved = {"balances": {"ACC-1001-CHK": 0.0, "ACC-1001-SAV": 23200.0}}
        self.assertFalse(T.grade_bank(t, "insufficient funds", read, moved)[0])

    def test_code_grader(self):
        t = self.t("C01")
        ex = [call("execute_python_code", code="print(1)")]
        self.assertTrue(T.grade_code(t, "The sum is 328.", ex)[0])
        self.assertFalse(T.grade_code(t, "The sum is 3280.", ex)[0])
        self.assertFalse(T.grade_code(t, "The sum is 328.", [])[0])
        c3 = self.t("C03")
        both = ex + [call("install_package", package="sympy")]
        self.assertTrue(T.grade_code(c3, "Primes: [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]", both)[0])
        self.assertFalse(T.grade_code(c3, "Primes: [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]", ex)[0])
        self.assertFalse(T.grade_code(c3, "Primes: 2, 3, 5, 7, 11, 13, 17, 19, 23", both)[0])


# ---- recording proxy ---------------------------------------------------------------

class ProxyTest(unittest.TestCase):
    def setUp(self):
        reset_llm()
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "proxy.jsonl")
        self.up, self.up_url = serve(LLM)

    def tearDown(self):
        self.up.shutdown()
        shutil.rmtree(self.tmp)

    def px(self, **kw):
        p = proxy.Proxy(self.up_url, self.log, **kw)
        srv = proxy.serve(p)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def test_ttft_on_tool_only_deltas(self):
        """H25 regression: a reply made only of tool-call deltas still gets a TTFT."""
        LLM.delay = 0.3
        url = self.px()
        req = {"model": "m", "stream": True, "tools": [BAL_TOOL],
               "messages": [{"role": "user", "content": "hi"}]}
        status, raw = post(url + "/v1/chat/completions", req)
        self.assertEqual(status, 200)
        self.assertIn(b"[DONE]", raw)
        (r,) = proxy.read_log(self.log)
        self.assertIsNotNone(r["ttft_s"])
        self.assertGreaterEqual(r["ttft_s"], 0.25)
        self.assertGreaterEqual(r["e2e_s"], r["ttft_s"])
        self.assertEqual(r["tool_source"], "structured")
        self.assertTrue(r["tool_valid"])
        self.assertEqual(r["tool_calls"][0]["name"], "get_balances")
        self.assertEqual(r["completion_tokens"], 7)          # include_usage was added
        self.assertTrue(LLM.seen[-1]["stream_options"]["include_usage"])
        self.assertEqual(r["last_user"], "hi")
        self.assertEqual(r["tools"], ["get_balances"])

    def test_invalid_call_and_json_response(self):
        LLM.args = {"user": 3}
        url = self.px()
        post(url + "/v1/chat/completions", {"model": "m", "tools": [BAL_TOOL],
                                            "messages": [{"role": "user", "content": "x"}]})
        (r,) = proxy.read_log(self.log)
        self.assertFalse(r["tool_valid"])
        self.assertIn("missing required", r["tool_calls"][0]["reason"])
        self.assertIsNone(r["ttft_s"])
        self.assertEqual(r["cached_tokens"], 64)

    def test_request_cap(self):
        url = self.px(max_requests=1)
        body = {"model": "m", "messages": [{"role": "assistant", "content": "a"}, {"role": "tool", "content": "{}"}]}
        post(url + "/v1/chat/completions", body)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(url + "/v1/chat/completions", body)
        self.assertEqual(cm.exception.code, 429)
        recs = proxy.read_log(self.log)
        self.assertEqual([r.get("error") for r in recs], [None, "capped"])
        self.assertEqual(len(LLM.seen), 1)

    def test_upstream_error_is_recorded(self):
        url = self.px()
        with self.assertRaises(urllib.error.HTTPError):
            post(url + "/v1/chat/fail/x/completions", {"model": "m", "messages": []})
        (r,) = proxy.read_log(self.log)
        self.assertEqual(r["status"], 500)
        self.assertEqual(r["error"], "HTTP 500")
        self.assertEqual(r["error_body"], "{}")      # why the upstream refused

    def test_non_llm_requests_pass_through_unlogged(self):
        url = self.px()
        with urllib.request.urlopen(url + "/v1/models", timeout=5) as resp:
            self.assertEqual(json.loads(resp.read()), {"data": []})
        self.assertEqual(proxy.read_log(self.log), [])

    def test_read_log_skips_non_json_lines(self):
        with open(self.log, "w") as f:
            f.write('agentbench: proxy on 0.0.0.0:8080\n{"ts_send": 1, "e2e_s": 2}\n{"ts_send": 2, "e2')
        self.assertEqual(proxy.read_log(self.log), [{"ts_send": 1, "e2e_s": 2}])

    def test_content_tool_calls_are_scored(self):
        calls, src, ok = proxy.score_calls([], '<tool_call>{"name": "get_balances", "arguments": {"user_id": "U1"}}</tool_call>',
                                           [BAL_TOOL])
        self.assertEqual((src, ok), ("content", True))
        self.assertEqual(proxy.score_calls([], "plain answer", [BAL_TOOL])[1:], ("none", None))


# ---- end to end: DevUI stand-in -> proxy -> LLM stub -------------------------------

class MCP(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    balances = dict(T.AVAILABLE)

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        p = body["params"]
        if p["name"] == "get_account_balance":
            res = {"available_balance": MCP.balances[p["arguments"]["account_id"]]}
        else:
            res = {"payoff_amount": T.LOANS[p["arguments"]["loan_id"]][2]}
        msg = {"jsonrpc": "2.0", "id": body["id"], "result": {
            "content": [{"type": "text", "text": json.dumps(res)}], "structuredContent": res}}
        data = b"event: message\ndata: " + json.dumps(msg).encode() + b"\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class BlockTest(unittest.TestCase):
    def setUp(self):
        reset_llm()
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "run")
        os.makedirs(self.out)
        self.log = os.path.join(self.out, "proxy.jsonl")
        self.up, up_url = serve(LLM)
        p = proxy.Proxy(up_url, self.log)
        self.psrv = proxy.serve(p)
        self.purl = f"http://127.0.0.1:{self.psrv.server_address[1]}"

    def tearDown(self):
        self.up.shutdown()
        self.psrv.shutdown()
        shutil.rmtree(self.tmp)

    def agent(self, sleep=0.0):
        srv, url = serve(make_devui(self.purl, sleep))
        self.addCleanup(srv.shutdown)
        return AB.Agent(url, token="tok")

    def test_graded_block_and_resume(self):
        t1 = [t for t in T.B24 if t["id"] == "B-T1-U1001"]
        t2 = [t for t in T.B24 if t["id"] == "B-T2-U1001"]
        ag = self.agent()
        rows = AB.run_block(ag, t1 + t2, "A1", "typical", 1, self.out, 60, self.log, settle_s=0.05)
        by = {r["task"]: r for r in rows}
        self.assertTrue(by["B-T1-U1001"]["passed"], by["B-T1-U1001"]["reason"])
        self.assertTrue(by["B-T1-U1001"]["strict"])
        self.assertEqual(by["B-T1-U1001"]["llm_requests"], 2)
        self.assertTrue(by["B-T1-U1001"]["first_turn_valid"])
        self.assertTrue(by["B-T2-U1001"]["passed"])      # 4,250.75 is in the answer
        turns = AB.read_jsonl(os.path.join(self.out, "turns.jsonl"))
        self.assertEqual(sorted(t["task"] for t in turns), ["B-T1-U1001"] * 2 + ["B-T2-U1001"] * 2)
        self.assertTrue(all(t["arm"] == "A1" and t["level"] == "typical" for t in turns))
        rep = AB.build_report([self.out])
        self.assertEqual(rep["groups"][0]["llm_requests"], 4)
        self.assertIsNotNone(rep["groups"][0]["ttft_p95_s"])
        # resume: nothing left to run
        again = AB.run_block(ag, t1 + t2, "A1", "typical", 1, self.out, 60, self.log, settle_s=0.05)
        self.assertEqual(again, [])
        self.assertEqual(len(AB.read_jsonl(os.path.join(self.out, "results.jsonl"))), 2)
        # another arm on the same results file is not skipped
        other = AB.run_block(ag, t1, "C2", "typical", 1, self.out, 60, self.log, settle_s=0.05)
        self.assertEqual(len(other), 1)

    def test_wrong_answer_fails_and_strict_needs_valid_calls(self):
        LLM.answer = "You have about four thousand dollars."
        rows = AB.run_block(self.agent(), [T.B24[0]], "A1", "none", 1, self.out, 60, self.log, settle_s=0.05)
        self.assertFalse(rows[0]["passed"])
        self.assertIn("missing", rows[0]["reason"])
        reset_llm()
        LLM.args = {"user_id": 1001}
        rows = AB.run_block(self.agent(), [T.B24[0]], "A2", "none", 1, self.out, 60, self.log, settle_s=0.05)
        self.assertFalse(rows[0]["passed"])
        self.assertIn("no valid call", rows[0]["reason"])

    def test_budget_censors_in_flight_task(self):
        rows = AB.run_block(self.agent(sleep=1.5), [T.B24[0]], "B1", "typical", 1, self.out, 0.5, self.log,
                            settle_s=0.0)
        self.assertEqual(rows[0]["status"], "censored")
        self.assertTrue(rows[0]["censored"])
        # a censored task is measured again on resume
        res = AB.Results(os.path.join(self.out, "results.jsonl"))
        self.assertNotIn(T.B24[0]["id"], res.done("B1", "typical", 1))

    def test_mutating_task_reads_end_state(self):
        MCP.balances = dict(T.AVAILABLE)
        MCP.balances["ACC-1001-CHK"] -= 200
        MCP.balances["ACC-1001-SAV"] += 200
        msrv, murl = serve(MCP)
        self.addCleanup(msrv.shutdown)
        LLM.tool_name = "transfer_funds"
        LLM.args = {"from_account": "ACC-1001-CHK", "to_account": "ACC-1001-SAV", "amount": 200}
        t6 = next(t for t in T.B24 if t["id"] == "B-T6-U1001")
        tool = {"type": "function", "function": {"name": "transfer_funds", "parameters": {
            "type": "object", "properties": {"from_account": {"type": "string"}, "to_account": {"type": "string"},
                                             "amount": {"type": "number"}},
            "required": ["from_account", "to_account", "amount"]}}}
        old = BAL_TOOL["function"]
        BAL_TOOL["function"] = tool["function"]
        self.addCleanup(lambda: BAL_TOOL.__setitem__("function", old))
        rows = AB.run_block(self.agent(), [t6], "A1", "typical", 1, self.out, 60, self.log, mcp_url=murl + "/mcp",
                            settle_s=0.05)
        self.assertTrue(rows[0]["passed"], rows[0]["reason"])
        self.assertEqual(AB.mcp_state(murl + "/mcp", t6)["balances"]["ACC-1001-CHK"], 4050.75)

    def test_reset_restores_seed_before_next_task(self):
        state = os.path.join(self.tmp, "state.json")
        seed = os.path.join(self.tmp, "seed.json")
        with open(seed, "w") as f:
            json.dump(T.AVAILABLE, f)
        moved = dict(T.AVAILABLE, **{"ACC-1001-CHK": 4050.75, "ACC-1001-SAV": 18400.0})
        with open(state, "w") as f:
            json.dump(moved, f)

        class FileMCP(MCP):
            def do_POST(self):
                with open(state) as f:
                    MCP.balances = json.load(f)
                super().do_POST()

        msrv, murl = serve(FileMCP)
        self.addCleanup(msrv.shutdown)
        t6 = next(t for t in T.B24 if t["id"] == "B-T6-U1001")
        self.assertNotEqual(AB.mcp_state(murl, t6), AB.seed_state(t6))
        AB.reset_sidecar(f"cp {seed} {state}", murl, t6, tries=3)
        self.assertEqual(AB.mcp_state(murl, t6), AB.seed_state(t6))
        with self.assertRaises(AB.ResetFailed):
            with open(state, "w") as f:
                json.dump(moved, f)
            AB.reset_sidecar("true", murl, t6, tries=1)

    def test_cli_run_selects_ids(self):
        srv, url = serve(make_devui(self.purl))
        self.addCleanup(srv.shutdown)
        os.environ["DEVUI_AUTH_TOKEN"] = "tok"
        self.addCleanup(os.environ.pop, "DEVUI_AUTH_TOKEN")
        AB.main(["run", "--arm", "A1", "--level", "none", "--out", self.out, "--agent-url", url,
                 "--tasks", "B24", "--ids", "B-T1-U1001,B-T2-U1001", "--proxy-log", self.log,
                 "--no-energy", "--no-load-sample", "--budget-s", "60"])
        rows = AB.read_jsonl(os.path.join(self.out, "results.jsonl"))
        self.assertEqual(sorted(r["task"] for r in rows), ["B-T1-U1001", "B-T2-U1001"])
        with open(os.path.join(self.out, "facts.json")) as f:
            facts = json.load(f)
        self.assertNotIn('"tok"', json.dumps(facts))     # the DevUI token value
        self.assertEqual(facts["lock"]["schema"], "k3d-gpu.agent-bench-lock/v1")
        with self.assertRaises(SystemExit):
            AB.main(["run", "--arm", "A1", "--level", "none", "--out", self.out, "--agent-url", url,
                     "--tasks", "B24", "--ids", "B-T9-U1001", "--proxy-log", self.log, "--no-energy",
                     "--no-load-sample"])

    def test_concurrency_and_cycle(self):
        rows = AB.run_block(self.agent(sleep=0.05), T.B15_RO[:3], "A3", "typical", 2, self.out, 1.0, self.log,
                            cycle=True, settle_s=0.0)
        self.assertGreater(len(rows), 3)
        self.assertGreaterEqual(max(r["cycle"] for r in rows), 1)


# ---- APB gate ----------------------------------------------------------------------

class ApbTest(unittest.TestCase):
    def rec(self, e2e, err=None):
        return {"e2e_s": e2e, "error": err, "ts_send": 1}

    def test_gate(self):
        rows = [{"passed": True, "censored": False}] * 9 + [{"passed": False, "censored": False}]
        ok, c = AB.apb_gate(rows, [self.rec(2.0)] * 60, 0.95)
        self.assertTrue(ok, c)
        ok, c = AB.apb_gate(rows, [self.rec(2.0)] * 59, 0.95)
        self.assertFalse(c["n_ok"])
        ok, c = AB.apb_gate(rows, [self.rec(2.0)] * 56 + [self.rec(11.0)] * 4, 0.95)
        self.assertFalse(c["p95_ok"])
        ok, c = AB.apb_gate(rows, [self.rec(2.0)] * 98 + [self.rec(None, "boom")] * 2, 0.95)
        self.assertFalse(c["errors_ok"])
        ok, c = AB.apb_gate(rows, [self.rec(2.0)] * 60, 1.0)
        self.assertTrue(ok)
        ok, c = AB.apb_gate(rows[:8] + [{"passed": False, "censored": False}] * 2, [self.rec(2.0)] * 60, 1.0)
        self.assertFalse(c["success_ok"])

    def test_agents_per_box(self):
        self.assertEqual(AB.agents_per_box([{"n": 1, "pass": True}, {"n": 2, "pass": True}, {"n": 4, "pass": False}]), 2)
        self.assertEqual(AB.agents_per_box([{"n": 1, "pass": False}]), 0)
        self.assertEqual(AB.agents_per_box([{"n": n, "pass": True} for n in AB.RAMP]), 8)

    def test_ramp_stops_at_first_failure(self):
        """Steps N=1, 2 see 80 requests at 1 s; from N=4 on the proxy sees 20 s turns."""
        calls = []
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        log = os.path.join(tmp, "p.jsonl")

        def blk(agent, ts, arm, level, n, out, step_s, plog, **kw):
            calls.append(n)
            with open(log, "w") as f:
                f.writelines(json.dumps({"ts_send": time.time(), "e2e_s": 1.0 if n < 4 else 20.0}) + "\n"
                             for _ in range(80))
            return [{"passed": True, "censored": False}] * 10

        res = AB.run_apb(None, "A1", "typical", tmp, 1, log, warmup_s=-10, block=blk)
        self.assertEqual(calls, [1, 2, 4])
        self.assertEqual(res["apb"], 2)
        self.assertTrue(os.path.exists(os.path.join(tmp, "apb.json")))


# ---- energy ------------------------------------------------------------------------

class EnergyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for zone, name, rng in (("intel-rapl:0", "package-0", 262143328850), ("intel-rapl:1", "psys", 262143328850)):
            d = os.path.join(self.tmp, "class/powercap", zone)
            os.makedirs(d)
            with open(os.path.join(d, "name"), "w") as f:
                f.write(name)
            with open(os.path.join(d, "max_energy_range_uj"), "w") as f:
                f.write(str(rng))
        os.makedirs(os.path.join(self.tmp, "class/powercap/intel-rapl:0:0"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_meter_wraps_and_adds_gpu(self):
        vals = {"pkg": [262143328850 - 1_000_000, 9_000_000], "psys": [0, 15_000_000]}
        clock = iter([0.0, 10.0])

        def reader(z):
            return vals["psys" if z.endswith(":1") else "pkg"].pop(0)
        m = energy.Meter(self.tmp, reader=reader, clock=lambda: next(clock))
        m.start()
        e = m.stop()
        self.assertAlmostEqual(e["package_j"], 10.0)
        self.assertAlmostEqual(e["psys_j"], 15.0)
        self.assertAlmostEqual(e["energy_j"], 10.0)
        self.assertAlmostEqual(e["mean_w"], 1.0)

    def test_unreadable_rapl(self):
        m = energy.Meter(self.tmp, reader=lambda z: None)
        m.start()
        self.assertIsNone(m.stop()["energy_j"])

    def test_rapl_exec_prefix(self):
        reader = os.path.join(self.tmp, "reader")
        with open(reader, "w") as f:
            f.write("#!/bin/sh\necho 777\n")
        os.chmod(reader, 0o755)
        zone = os.path.join(self.tmp, "class/powercap/intel-rapl:0")
        os.environ["K3D_GPU_RAPL_EXEC"] = reader
        self.addCleanup(os.environ.pop, "K3D_GPU_RAPL_EXEC")
        self.assertEqual(energy.read_uj(zone), 777)

    def test_gpu_integration(self):
        g = energy.GpuPower(period=0.01, query=lambda: 50.0)
        g.start()
        time.sleep(0.2)
        g.stop()
        self.assertGreater(g.samples, 3)
        self.assertGreater(g.joules, 5.0)
        dead = energy.GpuPower(period=0.01, query=lambda: None)
        dead.start()
        dead.stop()
        self.assertFalse(dead.available)

    def test_j_per_success_is_differential(self):
        block = {"energy_j": 3000.0, "seconds": 100.0}
        ctl = {"mean_w": 20.0}
        self.assertEqual(energy.j_per_success(block, ctl, 10), (100.0, "ok"))
        self.assertIsNone(energy.j_per_success(block, ctl, 0)[0])
        self.assertIsNone(energy.j_per_success(block, None, 5)[0])
        self.assertIsNone(energy.j_per_success({"energy_j": None, "seconds": 1}, ctl, 5)[0])


# ---- BFCL --------------------------------------------------------------------------

class BfclTest(unittest.TestCase):
    def test_s100_file(self):
        s = bfcl.load_s100()
        self.assertEqual(s["bfcl_eval"], "2025.12.17")
        self.assertEqual(s["seed"], 40)
        self.assertEqual(len(s["run_order"]), 100)
        self.assertEqual(len(set(s["run_order"])), 100)
        self.assertEqual({c: len(v) for c, v in s["ids_by_category"].items()}, bfcl.STRATA)
        self.assertEqual(sorted(s["run_order"]), sorted(i for v in s["ids_by_category"].values() for i in v))
        for c, ids in s["ids_by_category"].items():
            self.assertTrue(all(bfcl.category_of(i) == c for i in ids), c)
        self.assertEqual(s["wheel_sha256"], bfcl.WHEEL_SHA256)

    def test_build_is_seeded(self):
        pools = {c: [f"{c}_{i}" for i in range(60)] for c in bfcl.STRATA}
        a, b = bfcl.build_s100(pools), bfcl.build_s100(pools)
        self.assertEqual(a, b)
        self.assertNotEqual(a["run_order"], bfcl.build_s100(pools, seed=41)["run_order"])
        with self.assertRaises(ValueError):
            bfcl.build_s100(dict(pools, multiple=["multiple_0"]))

    def test_expected_matches_design(self):
        self.assertAlmostEqual(bfcl.expected(), 84.941, places=2)

    def test_category_and_ids_file(self):
        self.assertEqual(bfcl.category_of("live_simple_228-119-0"), "live_simple")
        self.assertEqual(bfcl.category_of("simple_java_26"), "simple_java")
        self.assertEqual(bfcl.ids_file(["multiple_1", "irrelevance_2", "multiple_3"]),
                         {"multiple": ["multiple_1", "multiple_3"], "irrelevance": ["irrelevance_2"]})

    def test_env_and_argv(self):
        e = bfcl.env_for("board", "http://127.0.0.1:4321", root="/r")
        self.assertEqual((e["LOCAL_SERVER_ENDPOINT"], e["LOCAL_SERVER_PORT"], e["BFCL_PROJECT_ROOT"]),
                         ("127.0.0.1", "4321", "/r"))
        e = bfcl.env_for("server-fc", "http://127.0.0.1:4321", served_model="alias")
        self.assertEqual(e["OPENAI_BASE_URL"], "http://127.0.0.1:4321/v1")
        self.assertEqual(e["K3D_BFCL_SERVED_MODEL"], "alias")
        argv = bfcl.generate_argv("board")
        self.assertIn("Qwen/Qwen3-4B-Instruct-2507-FC", argv)
        self.assertIn("--run-ids", argv)
        self.assertIn("--partial-eval", bfcl.evaluate_argv("server-fc"))
        with self.assertRaises(ValueError):
            bfcl.env_for("prompt", "http://x:1")

    def fake_bfcl(self, root, fail=(), stop_after=None):
        """A stand-in for the bfcl CLI: writes result/score files like bfcl-eval does."""
        state = {"chunks": 0}

        def runner(argv, cwd, env, timeout=None, check=False):
            reg = argv[argv.index("--model") + 1].replace("/", "_")
            if argv[2] == "generate":
                state["chunks"] += 1
                if stop_after is not None and state["chunks"] > stop_after:
                    raise bfcl.subprocess.TimeoutExpired(argv, timeout)
                with open(os.path.join(cwd, "test_case_ids_to_generate.json")) as f:
                    ids = json.load(f)
                for cat, lst in ids.items():
                    group = "live" if cat.startswith("live") else "non_live"
                    d = os.path.join(cwd, "result", reg, group)
                    os.makedirs(d, exist_ok=True)
                    with open(os.path.join(d, f"BFCL_v4_{cat}_result.json"), "a") as f:
                        for i in lst:
                            f.write(json.dumps({"id": i, "result": "x"}) + "\n")
            else:
                for p in bfcl._files(cwd, "result", "board" if "Qwen" in reg else "server-fc"):
                    cat = os.path.basename(p)[len("BFCL_v4_"):-len("_result.json")]
                    ids = [r["id"] for r in bfcl._jsonl(p)]
                    bad = [i for i in ids if i in fail]
                    d = os.path.join(cwd, "score", reg, os.path.basename(os.path.dirname(p)))
                    os.makedirs(d, exist_ok=True)
                    with open(os.path.join(d, f"BFCL_v4_{cat}_score.json"), "w") as f:
                        f.write(json.dumps({"accuracy": 1 - len(bad) / len(ids)}) + "\n")
                        for i in bad:
                            f.write(json.dumps({"id": i, "valid": False}) + "\n")
            return None
        return runner, state

    def test_run_ingest_and_resume(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        order = bfcl.load_s100()["run_order"]
        runner, st = self.fake_bfcl(tmp, fail={order[0], order[5]})
        r = bfcl.run("board", tmp, "http://127.0.0.1:1", 1e6, runner=runner, log=lambda m: None)
        self.assertEqual(r["stopped"], "done")
        self.assertEqual(len(r["completed"]), 100)
        self.assertEqual(st["chunks"], 10)
        self.assertEqual(sum(r["passed"].values()), 98)
        self.assertEqual(r["passed"][order[0]], 0)
        s = bfcl.summarize(r["passed"])
        self.assertEqual(s["n"], 100)
        self.assertAlmostEqual(s["acc"], 98.0)
        runner2, st2 = self.fake_bfcl(tmp)
        bfcl.run("board", tmp, "http://127.0.0.1:1", 1e6, runner=runner2, log=lambda m: None)
        self.assertEqual(st2["chunks"], 0)   # resumable: nothing regenerated

    def test_limit_takes_a_run_order_prefix(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        runner, st = self.fake_bfcl(tmp)
        r = bfcl.run("server-fc", tmp, "http://127.0.0.1:1", 1e6, runner=runner, log=lambda m: None, limit=8)
        self.assertEqual(r["completed"], bfcl.load_s100()["run_order"][:8])
        self.assertEqual(st["chunks"], 1)

    def test_failed_generate_is_an_error_not_an_empty_score(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)

        class P:
            returncode = 1

        r = bfcl.run("server-fc", tmp, "http://127.0.0.1:1", 1e6, runner=lambda *a, **k: P(), log=lambda m: None)
        self.assertTrue(r["stopped"].startswith("error: bfcl generate exit 1"), r["stopped"])
        self.assertEqual(r["completed"], [])

    def test_budget_keeps_a_prefix(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        runner, _ = self.fake_bfcl(tmp, stop_after=3)
        r = bfcl.run("server-fc", tmp, "http://127.0.0.1:1", 1e6, runner=runner, log=lambda m: None)
        self.assertEqual(r["stopped"], "budget")
        self.assertEqual(r["completed"], bfcl.load_s100()["run_order"][:30])


# ---- report ------------------------------------------------------------------------

class ReportTest(unittest.TestCase):
    def mkrun(self, root, arm, passes, dur, e2e, level="typical"):
        d = os.path.join(root, arm)
        os.makedirs(d)
        rows, recs, now = [], [], 1000.0
        for i, ok in enumerate(passes):
            t0 = now + i * 100
            rows.append({"task": f"B-{i}", "arm": arm, "level": level, "n": 1, "status": "pass" if ok else "fail",
                         "passed": ok, "strict": ok, "censored": False, "duration_s": dur, "t_start": t0,
                         "t_end": t0 + dur, "calls": [{"name": "get_balances", "valid": True}],
                         "first_turn_valid": True})
            recs.append({"task": f"B-{i}", "arm": arm, "level": level, "n": 1, "ts_send": t0 + 0.5,
                         "e2e_s": e2e, "ttft_s": 0.2, "error": None,
                         "tool_calls": [{"name": "get_balances"}], "completion_tokens": 40,
                         "prompt_tokens": 1000, "cached_tokens": 800})
        rows.append({"task": "B-cut", "arm": arm, "level": level, "n": 1, "status": "censored", "passed": False,
                     "strict": False, "censored": True, "duration_s": 99.0, "t_start": 0, "t_end": 1, "calls": []})
        for name, items in (("results.jsonl", rows), ("turns.jsonl", recs)):
            with open(os.path.join(d, name), "w") as f:
                f.writelines(json.dumps(x) + "\n" for x in items)
        with open(os.path.join(d, "blocks.jsonl"), "w") as f:
            f.write(json.dumps({"arm": arm, "level": level, "n": 1, "energy": {"seconds": 100.0, "energy_j": 5000.0}}) + "\n")
        with open(os.path.join(d, "control.jsonl"), "w") as f:
            f.write(json.dumps({"arm": arm, "level": level, "energy": {"mean_w": 30.0}}) + "\n")
        with open(os.path.join(d, "load.jsonl"), "w") as f:
            f.write(json.dumps({"tag": arm, "level": level, "busy_pct": 37.0, "load1": 6.0, "pkg_w": 60.0}) + "\n")
        return d

    def test_report(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        a = self.mkrun(tmp, "A1", [True] * 9 + [False], 20.0, 2.0)
        b = self.mkrun(tmp, "B1", [True] * 5 + [False] * 5, 100.0, 9.0)
        rep = AB.build_report([a, b], ref="B1")
        g = {x["arm"]: x for x in rep["groups"]}
        self.assertEqual(g["A1"]["tasks"], 10)
        self.assertEqual(g["A1"]["censored"], 1)
        self.assertEqual(g["A1"]["censored_lower_bounds_s"], [99.0])
        self.assertAlmostEqual(g["A1"]["success"], 0.9)
        self.assertAlmostEqual(g["A1"]["j_per_success"], (5000 - 30 * 100) / 9)
        self.assertAlmostEqual(g["A1"]["cached_fraction"], 0.8)
        self.assertEqual(g["B1"]["turn_p95_s"], 9.0)
        (p,) = rep["pairs"]
        self.assertAlmostEqual(p["success_diff"]["diff"], 0.4)
        self.assertAlmostEqual(p["time_ratio_median"], 0.2)
        self.assertEqual(rep["load"]["typical"]["verdict"], "MATCHED")
        md = AB.render_md(rep)
        self.assertIn("| A1 | typical | 1 | 10 | 1 |", md)
        self.assertIn("MATCHED", md)
        out = os.path.join(tmp, "rep")
        AB.main(["report", a, b, "--ref", "B1", "--out", out])
        with open(os.path.join(out, "report.json")) as f:
            self.assertEqual(json.load(f)["schema"], "k3d-gpu.agent-bench-report/v1")

    def test_bfcl_pairs_need_60_shared_ids(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        order = bfcl.load_s100()["run_order"]
        for arm, n in (("A3ref", 100), ("A1", 50)):
            d = os.path.join(tmp, arm)
            os.makedirs(d)
            with open(os.path.join(d, "bfcl.json"), "w") as f:
                json.dump({"arm": arm, "mode": "server-fc", "stopped": "done" if n == 100 else "budget",
                           "passed": {i: 1 for i in order[:n]}}, f)
        rep = AB.build_report([os.path.join(tmp, "A3ref"), os.path.join(tmp, "A1")], ref="A3ref")
        a1 = next(b for b in rep["bfcl"] if b["arm"] == "A1")
        self.assertEqual(a1["vs_ref"]["n"], 50)
        self.assertFalse(a1["vs_ref"]["n_ok"])
        self.assertIn("INCONCLUSIVE", AB.render_md(rep))


# ---- pins, plan, ledger ------------------------------------------------------------

class PinTest(unittest.TestCase):
    def test_every_image_is_pinned_by_digest(self):
        for name, img in AB.lock()["images"].items():
            self.assertRegex(img["ref"], r"@sha256:[0-9a-f]{64}$", name)

    def test_model_revisions_are_full_shas(self):
        for name, m in AB.lock()["models"].items():
            if "revision" in m:
                self.assertRegex(m["revision"], r"^[0-9a-f]{40}$", name)

    def test_lock_and_launcher_agree(self):
        lk = AB.lock()["suites"]["bfcl_eval"]
        self.assertEqual((lk["version"], lk["wheel_sha256"], lk["gorilla_commit"]),
                         (bfcl.VERSION, bfcl.WHEEL_SHA256, bfcl.GORILLA))
        self.assertEqual(AB.lock()["suites"]["blueprints"]["commit"], T.BLUEPRINTS.split("@")[1])

    def test_plans(self):
        for h in AB.lock()["hypotheses"]:
            p = AB.plan(h)
            self.assertTrue(p["blocks"], h)
            for b in p["blocks"]:
                self.assertLessEqual(b["seconds"], AB.ARM_BUDGET_S)
        p = AB.plan("H42")
        self.assertEqual([b["arm"] for b in p["blocks"]], ["A1", "A2", "A3", "B1", "C1", "C2",
                                                           "C2", "C1", "B1", "A3", "A2", "A1"])
        per_arm = {}
        for b in AB.plan("H43")["blocks"]:
            per_arm[(b["arm"], b["level"])] = per_arm.get((b["arm"], b["level"]), 0) + b["seconds"]
        self.assertEqual(set(per_arm.values()), {AB.ARM_BUDGET_S})
        self.assertEqual([b["level"] for b in AB.plan("H44")["blocks"]][:4], ["none", "heavy", "none", "heavy"])
        self.assertEqual([b.get("n") for b in AB.plan("H45")["blocks"]][:4], [1, 2, 4, 8])
        with self.assertRaises(SystemExit):
            AB.plan("H999")

    def test_ledger_rows_pending_once(self):
        with open(os.path.join(ROOT, "EXPERIMENTS.md")) as f:
            text = f.read()
        table = text.split("## Hypotheses", 1)[1].split("\n## ", 1)[0]
        for h in AB.lock()["hypotheses"]:
            rows = re.findall(rf"^\| {h} \|.*\| *(\w+) *\|$", table, re.M)
            self.assertEqual(rows, ["PENDING"], h)

    def test_manifest(self):
        m = AB.manifest("http://litellm.eat:4000", "ab", max_requests=20)
        self.assertIn(AB.lock()["images"]["proxy_python"]["ref"], m)
        self.assertIn('"--max-requests", "20"', m)
        for name in ("agentbench.py:", "proxy.py:", "agent_trace.py:", "agent.lock:"):
            self.assertIn(f"\n  {name} |\n", m)


# ---- refuter regressions (2026-10-17) ----------------------------------------------

class GraderRefuterTest(unittest.TestCase):
    """Grader holes found by the refuters of the H40-H48 pre-registration."""

    def t(self, tid):
        return next(t for t in T.B24 + T.C12 if t["id"] == tid)

    def test_t8_blanket_refusal_without_a_balance_read_fails(self):
        t = self.t("B-T8-U1002")
        for answer in ("I cannot access accounts.", "I'm unable to help with that.",
                       "Sorry, insufficient funds."):
            self.assertFalse(T.grade_bank(t, answer, [])[0], answer)

    def test_t8_balance_read_must_be_on_the_source_account(self):
        t = self.t("B-T8-U1002")
        other = [call("get_account_balance", account_id="ACC-1002-SAV")]
        self.assertFalse(T.grade_bank(t, "Insufficient funds.", other)[0])
        src = [call("get_account_balance", account_id="ACC-1002-CHK")]
        self.assertTrue(T.grade_bank(t, "Insufficient funds.", src)[0])

    def test_t8_refusal_must_cite_insufficient_funds(self):
        t = self.t("B-T8-U1002")
        src = [call("get_account_balance", account_id="ACC-1002-CHK")]
        self.assertFalse(T.grade_bank(t, "I cannot do that.", src)[0])
        self.assertTrue(T.grade_bank(t, "That would exceed your available balance of $980.10.", src)[0])
        self.assertTrue(T.grade_bank(t, "You do not have enough funds in ACC-1002-CHK.", src)[0])

    def test_t8_live_refusals_that_compare_balance_and_amount(self):
        """Amendment 21: real A3 answers (2026-10-17) that the first regex graded as failures."""
        t = self.t("B-T8-U1002")
        att = [call("transfer_funds", from_account="ACC-1002-CHK", to_account="ACC-1002-SAV", amount=2000)]
        for answer in (
                "I'm sorry, but the transfer cannot proceed. The source account (ACC-1002-CHK) has only $980.10 "
                "available, which is less than the $2,000 you requested.",
                "The source account (ACC-1002-CHK) has an available balance of $980.10, which is less than the "
                "$2,000 you requested.",
                "The transfer cannot proceed: only $980.10 is available."):
            self.assertTrue(T.grade_bank(t, answer, att)[0], answer)
        for answer in ("The transfer cannot proceed.", "Transfer of $2,000 complete; your balance is $0.00.",
                       "I can't do that right now."):
            self.assertFalse(T.grade_bank(t, answer, att)[0], answer)

    def test_t7_u1003_needs_a_no_loan_answer(self):
        t = self.t("B-T7-U1003")
        calls = [call("get_loans", user_id="U1003")]
        seed = {"balances": {"ACC-1003-CHK": T.AVAILABLE["ACC-1003-CHK"]},
                "loans": {lid: lo[2] for lid, lo in T.LOANS.items()}}
        self.assertFalse(T.grade_bank(t, "Payment done: $100 paid toward your auto loan.", calls, seed)[0])
        self.assertTrue(T.grade_bank(t, "You don't have an auto loan, so no payment was made.", calls, seed)[0])
        self.assertTrue(T.grade_bank(t, "You have no loans on file.", calls, seed)[0])

    def test_credit_account_accepts_owed_or_available(self):
        t = self.t("B-T1-U1002")
        calls = [call("get_balances", user_id="U1002")]
        base = "Checking $980.10, savings $5,600.00, credit card "
        self.assertTrue(T.grade_bank(t, base + "available credit $3,200.00.", calls)[0])
        self.assertTrue(T.grade_bank(t, base + "balance owed $1,800.00.", calls)[0])
        self.assertFalse(T.grade_bank(t, base + "$2,000.00.", calls)[0])

    def test_code_answer_must_be_grounded_in_tool_output(self):
        t = self.t("C01")
        ex = [call("execute_python_code", code="print(sum(...))")]
        self.assertTrue(T.grade_code(t, "The sum is 328.", ex, evidence='{"stdout": "328\\n"}')[0])
        ok, why = T.grade_code(t, "The sum is 328.", ex, evidence='{"stdout": "", "error": "NameError"}')
        self.assertFalse(ok)
        self.assertIn("tool output", why)
        self.assertFalse(T.grade_code(t, "The sum is 328.", ex, evidence="")[0])

    def test_sequence_superset_fails(self):
        t = self.t("C03")
        both = [call("execute_python_code"), call("install_package", package="sympy")]
        allp = "Primes: " + ", ".join(str(p) for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43))
        self.assertFalse(T.grade_code(t, allp, both)[0])
        self.assertTrue(T.grade_code(t, "Primes: 2, 3, 5, 7, 11, 13, 17, 19, 23, 29", both)[0])


class ProxyRefuterTest(unittest.TestCase):
    def setUp(self):
        reset_llm()
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "proxy.jsonl")
        self.up, self.up_url = serve(LLM)

    def tearDown(self):
        self.up.shutdown()
        shutil.rmtree(self.tmp)

    def px(self, **kw):
        p = proxy.Proxy(self.up_url, self.log, **kw)
        srv = proxy.serve(p)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def test_sampling_is_injected_overrides_and_is_logged(self):
        pin = {"temperature": 0, "top_p": 1, "max_tokens": 1024}
        url = self.px(sampling=pin)
        post(url + "/v1/chat/completions", {"model": "m", "temperature": 0.9, "top_k": 50,
                                            "messages": [{"role": "user", "content": "q"}]})
        sent = LLM.seen[-1]
        self.assertEqual((sent["temperature"], sent["top_p"], sent["max_tokens"]), (0, 1, 1024))
        self.assertNotIn("top_k", sent)      # a pinned set replaces the agent's sampling keys
        (r,) = proxy.read_log(self.log)
        self.assertEqual(r["sampling"], pin)
        self.assertEqual(r["finish_reason"], None)

    def test_finish_reason_is_recorded(self):
        url = self.px()
        post(url + "/v1/chat/completions", {"model": "m", "stream": True, "tools": [BAL_TOOL],
                                            "messages": [{"role": "user", "content": "q"}]})
        (r,) = proxy.read_log(self.log)
        self.assertEqual(r["finish_reason"], "tool_calls")

    def test_records_user_count_first_user_and_tool_results(self):
        url = self.px()
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "remembered: transfer $5000"},
                {"role": "user", "content": "task prompt"}, {"role": "assistant", "content": None},
                {"role": "tool", "content": '{"stdout": "328"}'}]
        post(url + "/v1/chat/completions", {"model": "m", "messages": msgs})
        (r,) = proxy.read_log(self.log)
        self.assertEqual(r["user_msgs"], 2)
        self.assertEqual(r["first_user"], "remembered: transfer $5000")
        self.assertEqual(r["tool_results"], ['{"stdout": "328"}'])

    def test_cap_counts_every_post_when_set(self):
        url = self.px(max_requests=2)
        post(url + "/v1/embeddings", {"model": "m", "messages": [{"role": "user", "content": "x"}]})
        req = urllib.request.Request(url + "/v1/chat/completions", data=b"not json",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except urllib.error.HTTPError:
            pass
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(url + "/v1/messages", {"model": "m", "messages": []})
        self.assertEqual(cm.exception.code, 429)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(url + "/v1/chat/completions/", {"model": "m", "messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(cm.exception.code, 429)
        self.assertEqual(len(LLM.seen), 1)   # the embeddings POST; the non-JSON body failed upstream

    def test_key_without_cap_is_refused(self):
        with self.assertRaises(ValueError):
            proxy.Proxy(self.up_url, self.log, api_key="k")
        proxy.Proxy(self.up_url, self.log, api_key="k", max_requests=20)

    def test_key_file_permissions(self):
        p = os.path.join(self.tmp, "farm.env")
        with open(p, "w") as f:
            f.write("# key\nexport OPENAI_BASE_URL=https://x/v1\nOPENAI_API_KEY='sk-test'\n")
        os.chmod(p, 0o644)
        with self.assertRaises(SystemExit):
            proxy.read_key_file(p, "OPENAI_API_KEY")
        os.chmod(p, 0o600)
        self.assertEqual(proxy.read_key_file(p, "OPENAI_API_KEY"), "sk-test")
        with self.assertRaises(SystemExit):
            proxy.read_key_file(p, "MISSING_KEY")

    def test_cli_proxy_has_no_env_key_path(self):
        with open(os.path.join(HERE, "agentbench.py")) as f:
            self.assertNotIn("AGENTBENCH_UPSTREAM_KEY", f.read())
        with self.assertRaises(SystemExit):
            AB.main(["proxy", "--upstream", self.up_url, "--log", self.log,
                     "--key-file", os.path.join(self.tmp, "absent.env"), "--max-requests", "1"])

    def test_manifest_keeps_a_zero_cap(self):
        self.assertIn('"--max-requests", "0"', AB.manifest("http://l:4000", "ab", max_requests=0))
        m = AB.manifest("http://l:4000", "ab", sampling={"temperature": 0})
        self.assertIn('"--sampling"', m)


class BlockRefuterTest(unittest.TestCase):
    setUp = BlockTest.setUp
    tearDown = BlockTest.tearDown
    agent = BlockTest.agent

    def test_reset_failure_aborts_the_block_and_cli_exits_nonzero(self):
        """A failed --reset inside a worker thread used to be swallowed (SystemExit in a thread)."""
        MCP.balances = dict(T.AVAILABLE)
        msrv, murl = serve(MCP)
        self.addCleanup(msrv.shutdown)
        t6s = [t for t in T.B24 if t["template"] == "T6"]
        with self.assertRaises(AB.BlockAborted) as cm:
            AB.run_block(self.agent(), t6s, "A1", "typical", 1, self.out, 60, self.log, mcp_url=murl,
                         reset="false", settle_s=0.05)
        self.assertIn("reset", cm.exception.reason)
        self.assertEqual(len(cm.exception.rows), 1)          # no task ran after the failed reset
        blk = AB.read_jsonl(os.path.join(self.out, "blocks.jsonl"))[-1]
        self.assertIn("reset", blk["aborted"])
        srv, url = serve(make_devui(self.purl))
        self.addCleanup(srv.shutdown)
        os.environ["DEVUI_AUTH_TOKEN"] = "tok"
        self.addCleanup(os.environ.pop, "DEVUI_AUTH_TOKEN")
        rc = AB.main(["run", "--arm", "A9", "--level", "none", "--out", self.out, "--agent-url", url,
                      "--tasks", "B24", "--ids", "B-T6-U1001,B-T6-U1002", "--proxy-log", self.log,
                      "--mcp-url", murl, "--reset", "false", "--no-energy", "--no-load-sample", "--no-warmup"])
        self.assertNotEqual(rc, 0)

    def test_reset_timeout_is_fatal(self):
        t6 = next(t for t in T.B24 if t["id"] == "B-T6-U1001")
        with self.assertRaises(AB.ResetFailed):
            AB.reset_sidecar("sleep 5", None, t6, timeout=0.2)
        with self.assertRaises(AB.ResetFailed):
            AB.reset_sidecar("false", None, t6)

    def test_refuse_task_that_moved_state_is_reset(self):
        """T8 is not mutating, but a server that executed the overdraft must be reset."""
        state = {"moved": True}

        class Moved(MCP):
            def do_POST(self):
                MCP.balances = dict(T.AVAILABLE)
                if state["moved"]:
                    MCP.balances["ACC-1001-CHK"] = 0.0
                super().do_POST()

        msrv, murl = serve(Moved)
        self.addCleanup(msrv.shutdown)
        flag = os.path.join(self.tmp, "reset.ran")
        LLM.answer = "Insufficient funds."
        LLM.tool_name = "get_account_balance"
        LLM.args = {"account_id": "ACC-1001-CHK"}
        t8 = next(t for t in T.B24 if t["id"] == "B-T8-U1001")
        with self.assertRaises(AB.BlockAborted):   # the stub never returns to seed, so the reset check fails
            AB.run_block(self.agent(), [t8], "A1", "typical", 1, self.out, 60, self.log, mcp_url=murl,
                         reset=f"touch {flag}", settle_s=0.05, reset_tries=1)
        self.assertTrue(os.path.exists(flag))
        state["moved"] = False
        os.remove(flag)
        AB.run_block(self.agent(), [t8], "A2", "typical", 1, self.out, 60, self.log, mcp_url=murl,
                     reset=f"touch {flag}", settle_s=0.05)
        self.assertFalse(os.path.exists(flag))     # state at seed: no reset

    def test_memory_on_aborts_the_block(self):
        """Blueprint Redis memory re-injects earlier prompts as an extra user message."""
        purl = self.purl

        class MemDevUI(make_devui(purl)):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                msgs = [{"role": "system", "content": "bank"}, {"role": "user", "content": body["input"]},
                        {"role": "user", "content": "Memories: I'm user U1001. Transfer $5000 ... I confirm."}]
                post(purl + "/v1/chat/completions", {"model": "m", "stream": False, "messages": msgs,
                                                     "tools": [BAL_TOOL]})
                self._json({"status": "completed", "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]})
        srv, url = serve(MemDevUI)
        self.addCleanup(srv.shutdown)
        with self.assertRaises(AB.BlockAborted) as cm:
            AB.run_block(AB.Agent(url, token="tok"), T.B24[:2], "A1", "typical", 1, self.out, 60, self.log,
                         settle_s=0.05)
        self.assertIn("memory", cm.exception.reason)

    def test_attribution_waits_for_the_final_turn(self):
        """A lagging log (kubectl logs -f) is polled, not slept on for a fixed time."""
        lag = os.path.join(self.tmp, "lagged.jsonl")
        t1 = [t for t in T.B24 if t["id"] == "B-T1-U1001"]

        def copy_later():
            time.sleep(0.6)
            shutil.copy(self.log, lag)
        threading.Thread(target=copy_later, daemon=True).start()
        t0 = time.time()
        rows = AB.run_block(self.agent(), t1, "A1", "typical", 1, self.out, 60, lag, settle_s=5.0)
        self.assertLess(time.time() - t0, 3.0)     # returned when the record landed, not after 5 s
        self.assertTrue(rows[0]["passed"], rows[0]["reason"])
        self.assertEqual(rows[0]["llm_requests"], 2)

    def test_regrade_applies_current_graders_and_keeps_originals(self):
        LLM.answer = "Checking 4,250.75."
        rows = AB.run_block(self.agent(), [T.B24[0]], "A1", "typical", 1, self.out, 60, self.log, settle_s=0.05)
        self.assertFalse(rows[0]["passed"])                   # 18,200.00 missing
        p = os.path.join(self.out, "results.jsonl")
        r = AB.read_jsonl(p)[0]
        r["answer"] = "Checking $4,250.75, savings $18,200.00."
        with open(p, "w") as f:
            f.write(json.dumps(r) + "\n")
        self.assertEqual(AB.regrade(self.out), 1)
        (new,) = AB.read_jsonl(p)
        self.assertTrue(new["passed"])
        self.assertIn("18200.00", new["regraded"]["from"])
        self.assertTrue(os.path.exists(os.path.join(self.out, "results.raw.jsonl")))
        self.assertEqual(AB.regrade(self.out), 1)             # idempotent: always from the raw rows

    def test_warmup_is_unscored_and_outside_the_window(self):
        rows = AB.run_block(self.agent(), [T.B24[0]], "A1", "typical", 1, self.out, 60, self.log, settle_s=0.05,
                            warmup="My user id is U1001. Which account ids do I have?")
        self.assertEqual([r["task"] for r in rows], ["B-T1-U1001"])
        blk = AB.read_jsonl(os.path.join(self.out, "blocks.jsonl"))[-1]
        self.assertIsNone(blk["warmup"]["error"])
        self.assertLess(blk["warmup"]["t_end"], rows[0]["t_start"])
        turns = AB.read_jsonl(os.path.join(self.out, "turns.jsonl"))
        self.assertEqual({t["task"] for t in turns}, {"B-T1-U1001"})

    def test_cycle_prompts_carry_a_nonce(self):
        rows = AB.run_block(self.agent(sleep=0.02), T.B15_RO[:2], "A3", "typical", 1, self.out, 1.0, self.log,
                            cycle=True, settle_s=0.0)
        prompts = [m["content"] for b in LLM.seen for m in b["messages"] if m["role"] == "user"]
        counts = {p: prompts.count(p) for p in prompts}
        self.assertLessEqual(max(counts.values()), 2)            # a prompt is one task's 2 turns, never reused
        self.assertTrue(all(p.startswith("[ref b-") for p in counts))
        self.assertGreaterEqual(len(counts), len(rows) - 1)      # the cut last task may not reach the LLM
        self.assertGreater(len(rows), 2)
        self.assertTrue(all(r["llm_requests"] == 2 for r in rows if not r["censored"]))

    def test_run_records_sampling_and_memory_in_facts(self):
        srv, url = serve(make_devui(self.purl))
        self.addCleanup(srv.shutdown)
        os.environ["DEVUI_AUTH_TOKEN"] = "tok"
        self.addCleanup(os.environ.pop, "DEVUI_AUTH_TOKEN")
        AB.main(["run", "--arm", "A1", "--level", "none", "--out", self.out, "--agent-url", url,
                 "--tasks", "B24", "--ids", "B-T1-U1001", "--proxy-log", self.log,
                 "--no-energy", "--no-load-sample", "--no-warmup"])
        with open(os.path.join(self.out, "facts.json")) as f:
            facts = json.load(f)
        self.assertIn("sampling_seen", facts)
        self.assertTrue(facts["memory"].startswith("off"))


class ControlRefuterTest(unittest.TestCase):
    def test_control_rejects_no_energy(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        with self.assertRaises(SystemExit):
            AB.main(["control", "--arm", "A1", "--level", "typical", "--out", tmp, "--no-energy",
                     "--no-load-sample", "--seconds", "0"])

    def test_control_windows_do_not_enter_load_matching(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        ws = [{"tag": "A1", "level": "typical", "busy_pct": 37.0, "load1": 6.0, "pkg_w": 60.0},
              {"tag": "B1", "level": "typical", "busy_pct": 37.5, "load1": 6.1, "pkg_w": 61.0},
              {"tag": "A1-control", "level": "typical", "busy_pct": 5.0, "load1": 1.0, "pkg_w": 20.0}]
        with open(os.path.join(tmp, "load.jsonl"), "w") as f:
            f.writelines(json.dumps(w) + "\n" for w in ws)
        rep = AB.build_report([tmp])
        self.assertEqual(rep["load"]["typical"]["verdict"], "MATCHED")


class ReportRefuterTest(unittest.TestCase):
    mkrun = ReportTest.mkrun

    def test_time_ratio_is_paired_on_shared_completed_ids(self):
        """B1 finishes only an easy prefix; A1's slow tail must not enter the ratio."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        a = self.mkrun(tmp, "A1", [True] * 10, 10.0, 2.0)
        rows = AB.read_jsonl(os.path.join(a, "results.jsonl"))
        for r in rows:
            if r["task"] in ("B-6", "B-7", "B-8", "B-9"):
                r["duration_s"] = 1000.0         # slow tasks B1 never reached
        with open(os.path.join(a, "results.jsonl"), "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
        b = self.mkrun(tmp, "B1", [True] * 6, 100.0, 9.0)
        rep = AB.build_report([a, b], ref="B1")
        (p,) = rep["pairs"]
        self.assertEqual(p["shared_ids"], 6)
        self.assertAlmostEqual(p["time_ratio_median"], 0.1)
        self.assertAlmostEqual(p["time_ratio_ci95"][0], 0.1)
        self.assertEqual((p["arm_completed"], p["ref_completed"]), (10, 6))
        self.assertAlmostEqual(p["arm_success_shared"], 1.0)
        self.assertIn("1.00 (10/10)", AB.render_md(rep))

    def test_sampling_is_reported_per_group(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        a = self.mkrun(tmp, "A1", [True] * 2, 10.0, 2.0)
        rows = AB.read_jsonl(os.path.join(a, "turns.jsonl"))
        with open(os.path.join(a, "turns.jsonl"), "w") as f:
            f.writelines(json.dumps(dict(r, sampling={"temperature": 0})) + "\n" for r in rows)
        rep = AB.build_report([a])
        self.assertEqual(rep["groups"][0]["sampling"], [{"temperature": 0}])


class StatsRefuterTest(unittest.TestCase):
    def test_paired_ratio(self):
        a = {"x": 2.0, "y": 4.0, "z": 8.0, "only_a": 100.0}
        b = {"x": 10.0, "y": 20.0, "z": 40.0}
        r = stats.paired_ratio(a, b)
        self.assertEqual(r["n"], 3)
        self.assertAlmostEqual(r["ratio"], 0.2)
        self.assertAlmostEqual(r["ci95"][0], 0.2)
        self.assertEqual(stats.paired_ratio({}, b)["n"], 0)


class PinRefuterTest(unittest.TestCase):
    def test_bfcl_constraints_carry_hashes(self):
        with open(os.path.join(HERE, "bfcl-constraints.txt")) as f:
            text = f.read()
        reqs = [ln for ln in text.splitlines() if re.match(r"^[A-Za-z0-9]", ln)]
        self.assertGreater(len(reqs), 50)
        self.assertGreaterEqual(text.count("--hash=sha256:"), len(reqs))
        self.assertIn("bfcl-eval==2025.12.17", text)
        self.assertIn(bfcl.WHEEL_SHA256, text)

    def test_install_note_uses_require_hashes_on_the_file(self):
        note = AB.lock()["suites"]["bfcl_eval"]["install"]
        self.assertIn("--require-hashes -r bench/eatflow/bfcl-constraints.txt", note)
        self.assertNotRegex(note, r"pip install [^-]*--hash=")

    def test_sampling_profiles_pinned(self):
        s = AB.lock()["sampling"]
        self.assertEqual(s["agent_local"]["temperature"], 0)
        self.assertNotIn("temperature", s["agent_remote"])     # harvest rule: never temperature 0
        self.assertLessEqual(s["agent_remote"]["max_tokens"], 256)


if __name__ == "__main__":
    unittest.main(verbosity=1)
