#!/usr/bin/env python3
"""Unit tests for agent_trace.py against an in-process OpenAI-compatible stub.

The stub streams SSE like vLLM/OVMS, splits tool-call arguments across deltas,
and simulates a message-granular prefix cache: prefill cost is a sleep per
uncached word, and usage.prompt_tokens_details.cached_tokens counts the words
of the longest previously seen message prefix.

Run: python3 bench/test_agent_trace.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_trace as at  # noqa: E402
import agent_trace_gen as gen  # noqa: E402


def _msg_words(m):
    words = (m.get("content") or "").split()
    for tc in m.get("tool_calls") or []:
        words += tc["function"]["arguments"].split()
    return [m["role"]] + words


class Stub:
    """Configurable fake server. mode picks what the 'model' emits."""

    def __init__(
        self, mode="structured", per_word_s=0.0, per_token_s=0.0, report_cached=True
    ):
        self.mode, self.per_word_s, self.per_token_s = mode, per_word_s, per_token_s
        self.report_cached = report_cached
        self.seen, self.lock, self.requests = set(), threading.Lock(), []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append(body)
                if stub.mode == "error500":
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b'{"error":"boom"}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in stub.respond(body):
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = (
            f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/chat/completions"
        )
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def prefill(self, messages, block=16):
        """Block-granular prefix cache over the flattened word stream (like vLLM's
        hashed KV blocks). Returns (total_words, cached_words)."""
        stream = [w for m in messages for w in _msg_words(m)]
        h, cached, hit = hashlib.sha256(), 0, True
        with self.lock:
            for i in range(0, len(stream) - len(stream) % block, block):
                h.update(json.dumps(stream[i : i + block]).encode())
                key = h.hexdigest()
                if hit and key in self.seen:
                    cached += block
                else:
                    hit = False
                self.seen.add(key)
        return len(stream), cached

    def respond(self, body):
        total, cached = self.prefill(body["messages"])
        time.sleep(self.per_word_s * (total - cached))
        tools = body.get("tools") or []
        out = []
        if tools and self.mode != "text":
            fn = tools[0]["function"]
            props = fn["parameters"]["properties"]
            args = {k: (7 if v["type"] == "integer" else "x") for k, v in props.items()}
            if self.mode == "missing_arg":
                args.pop(fn["parameters"]["required"][0])
            text = json.dumps(args)
            if self.mode == "bad_json":
                text = text[:-1]
            call = {"name": fn["name"], "arguments": args}
            if self.mode == "content_json":
                out = [{"content": json.dumps(call)}]
            elif self.mode == "hermes":
                out = [{"content": f"<tool_call>{json.dumps(call)}</tool_call>"}]
            else:
                half = len(text) // 2
                out = [
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c0",
                                "type": "function",
                                "function": {
                                    "name": fn["name"],
                                    "arguments": text[:half],
                                },
                            }
                        ]
                    },
                    {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": text[half:]}}
                        ]
                    },
                ]
        else:
            out = [
                {"content": f"w{i} "} for i in range(min(body.get("max_tokens", 8), 8))
            ]
        for d in out:
            time.sleep(self.per_token_s)
            yield {"choices": [{"index": 0, "delta": d}], "usage": None}
        usage = {
            "prompt_tokens": total,
            "completion_tokens": len(out),
            "total_tokens": total + len(out),
        }
        if self.report_cached:
            usage["prompt_tokens_details"] = {"cached_tokens": cached}
        yield {"choices": [], "usage": usage}


def run_args(url, **kw):
    base = dict(
        url=url,
        model="stub",
        api_key=None,
        cache_bust="none",
        time_scale=0.0,
        max_concurrency=0,
        temperature=0.0,
        tool_choice="auto",
        timeout=30.0,
        verbose=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


TOOLS = [gen.tool("lookup", "find", {"id": gen.STR, "n": gen.INT}, ["id", "n"])]


def tiny_trace(expect=("lookup", None)):
    turns = []
    for i, e in enumerate(expect):
        turns.append(
            {
                "append": [{"role": "user", "content": f"question {i}"}],
                "max_tokens": 8,
                "expect_tool": e,
                "recorded": {"role": "assistant", "content": f"answer {i}"},
            }
        )
    return {
        "name": "tiny",
        "tools": TOOLS,
        "sessions": [{"id": "a", "start_s": 0, "system": "sys prompt", "turns": turns}],
    }


class ValidateCalls(unittest.TestCase):
    def check(self, calls, ok, reason_part=""):
        valid, reason = at.validate_calls(calls, TOOLS)
        self.assertEqual(valid, ok, reason)
        self.assertIn(reason_part, reason)

    def test_ok(self):
        self.check([{"name": "lookup", "arguments": '{"id": "a", "n": 3}'}], True, "ok")

    def test_dict_arguments(self):
        self.check([{"name": "lookup", "arguments": {"id": "a", "n": 3}}], True)

    def test_unknown(self):
        self.check([{"name": "nope", "arguments": "{}"}], False, "unknown")

    def test_bad_json(self):
        self.check([{"name": "lookup", "arguments": '{"id": "a"'}], False, "not JSON")

    def test_not_object(self):
        self.check([{"name": "lookup", "arguments": "[1]"}], False, "not an object")

    def test_missing(self):
        self.check([{"name": "lookup", "arguments": '{"id": "a"}'}], False, "missing")

    def test_bad_type(self):
        self.check(
            [{"name": "lookup", "arguments": '{"id": "a", "n": "3"}'}], False, "type"
        )

    def test_bool_is_not_integer(self):
        self.check(
            [{"name": "lookup", "arguments": '{"id": "a", "n": true}'}], False, "type"
        )

    def test_empty(self):
        self.check([], False, "no tool call")

    def test_one_bad_of_two(self):
        self.check(
            [
                {"name": "lookup", "arguments": '{"id":"a","n":1}'},
                {"name": "lookup", "arguments": "{"},
            ],
            False,
        )


class ContentCalls(unittest.TestCase):
    def test_bare_json(self):
        c = at.calls_from_content('{"name": "lookup", "arguments": {"id": "a"}}')
        self.assertEqual(c, [{"name": "lookup", "arguments": {"id": "a"}}])

    def test_parameters_key(self):
        c = at.calls_from_content('{"name": "lookup", "parameters": {"id": "a"}}')
        self.assertEqual(c[0]["arguments"], {"id": "a"})

    def test_hermes_two(self):
        txt = (
            'x <tool_call>{"name":"a","arguments":{}}</tool_call>'
            '<tool_call>{"name":"b","arguments":{}}</tool_call>'
        )
        self.assertEqual([c["name"] for c in at.calls_from_content(txt)], ["a", "b"])

    def test_hermes_broken(self):
        c = at.calls_from_content("<tool_call>{oops}</tool_call>")
        self.assertEqual(c, [{"name": None, "arguments": "{oops}"}])

    def test_plain_text(self):
        self.assertEqual(at.calls_from_content("hello {not json"), [])
        self.assertEqual(at.calls_from_content(""), [])
        self.assertEqual(at.calls_from_content(None), [])


class AgainstStub(unittest.TestCase):
    def stub(self, **kw):
        s = Stub(**kw)
        self.addCleanup(s.close)
        return s

    def test_stream_assembles_fragmented_tool_call(self):
        s = self.stub(per_token_s=0.01)
        r = at.stream_chat(
            s.url,
            {
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": TOOLS,
                "stream": True,
            },
        )
        self.assertIsNone(r["error"])
        self.assertEqual(
            r["tool_calls"], [{"name": "lookup", "arguments": '{"id": "x", "n": 7}'}]
        )
        self.assertGreater(r["ttft_s"], 0)
        self.assertGreaterEqual(r["decode_s"], 0.009)
        self.assertEqual(r["usage"]["completion_tokens"], 2)

    def test_decode_tps(self):
        # 6 prompt words * 50 ms prefill must land in TTFT, not in decode rate
        s = self.stub(mode="text", per_token_s=0.02, per_word_s=0.05)
        res, _ = at.run_trace(tiny_trace((None,)), run_args(s.url))
        self.assertGreater(res[0]["ttft_s"], 0.3)
        # 8 deltas, 7 gaps of ~20 ms -> ~50 tok/s
        self.assertTrue(40 < res[0]["decode_tps"] < 55, res[0]["decode_tps"])

    def test_request_shape(self):
        s = self.stub()
        at.run_trace(tiny_trace(), run_args(s.url))
        first, second = s.requests
        self.assertTrue(first["stream"])
        self.assertEqual(first["stream_options"], {"include_usage": True})
        self.assertEqual(first["tools"], TOOLS)
        self.assertEqual(
            first["messages"][0], {"role": "system", "content": "sys prompt"}
        )
        # teacher forcing: turn 1 carries turn 0's recorded answer, not the live reply
        self.assertEqual(
            [m["content"] for m in second["messages"]],
            ["sys prompt", "question 0", "answer 0", "question 1"],
        )

    def modes(self, mode):
        s = self.stub(mode=mode)
        res, _ = at.run_trace(tiny_trace(), run_args(s.url))
        return res

    def test_structured_valid(self):
        res = self.modes("structured")
        self.assertEqual(
            (res[0]["tool_source"], res[0]["tool_valid"]), ("structured", True)
        )
        s = at.summarize(res, 1.0, {})
        self.assertEqual(s["tool_valid_structured"], 1.0)
        self.assertEqual(s["tool_unexpected"], 1)  # stub also calls on the text turn

    def test_content_json(self):
        res = self.modes("content_json")
        self.assertEqual(
            (res[0]["tool_source"], res[0]["tool_valid"]), ("content", True)
        )
        s = at.summarize(res, 1.0, {})
        self.assertEqual((s["tool_valid_structured"], s["tool_valid_any"]), (0.0, 1.0))

    def test_hermes(self):
        self.assertEqual(self.modes("hermes")[0]["tool_source"], "content")

    def test_bad_json(self):
        r = self.modes("bad_json")[0]
        self.assertEqual((r["tool_source"], r["tool_valid"]), ("structured", False))
        self.assertIn("not JSON", r["tool_reason"])

    def test_missing_arg(self):
        self.assertIn("missing", self.modes("missing_arg")[0]["tool_reason"])

    def test_text_only(self):
        r = self.modes("text")[0]
        self.assertEqual((r["tool_source"], r["tool_valid"]), ("none", False))

    def test_http_error_recorded(self):
        res = self.modes("error500")
        self.assertEqual(len(res), 2)
        self.assertTrue(
            all("HTTPError" in r["error"] and "boom" in r["error"] for r in res)
        )
        self.assertEqual(at.summarize(res, 1.0, {})["errors"], 2)

    def test_connection_refused(self):
        res, _ = at.run_trace(
            tiny_trace(), run_args("http://127.0.0.1:9/v1/chat/completions")
        )
        self.assertTrue(all(r["error"] for r in res))

    def test_no_usage_falls_back_to_chunks(self):
        s = self.stub(mode="text", report_cached=False)
        r = at.run_trace(tiny_trace((None,)), run_args(s.url))[0][0]
        self.assertIsNone(r["cached_tokens"])
        self.assertEqual(r["completion_tokens"], 8)

    def test_concurrency_cap(self):
        t = tiny_trace((None,))
        t["sessions"] = [dict(t["sessions"][0], id=f"s{i}") for i in range(4)]
        s = self.stub(mode="text", per_token_s=0.02)  # ~0.16 s per request
        t0 = time.perf_counter()
        at.run_trace(t, run_args(s.url, max_concurrency=1))
        serial = time.perf_counter() - t0
        t0 = time.perf_counter()
        at.run_trace(t, run_args(s.url, max_concurrency=0))
        parallel = time.perf_counter() - t0
        self.assertGreater(serial, 2.5 * parallel)

    def test_arrival_schedule(self):
        t = tiny_trace((None,))
        t["sessions"] = [
            dict(t["sessions"][0], id="a", start_s=0.0),
            dict(t["sessions"][0], id="b", start_s=0.3),
        ]
        s = self.stub(mode="text")
        res, _ = at.run_trace(t, run_args(s.url, time_scale=1.0))
        b = next(r for r in res if r["session"] == "b")
        self.assertGreaterEqual(b["sent_at_s"], 0.29)


class PrefixCache(unittest.TestCase):
    """Cache-bust control vs normal run on the stub's simulated prefix cache."""

    def run_family(self, bust, family="support"):
        s = Stub(mode="text", per_word_s=0.0001)
        try:
            trace = gen.FAMILIES[family](seed=1, scale=0.25)
            res, wall = at.run_trace(
                trace, run_args(s.url, cache_bust=bust, max_concurrency=1)
            )
            return at.summarize(res, wall, {"cache_bust": bust})
        finally:
            s.close()

    def test_bust_modes(self):
        none, sess, req = (self.run_family(b) for b in ("none", "session", "request"))
        self.assertEqual(req["cached_fraction"], 0)
        self.assertGreater(none["cached_fraction"], sess["cached_fraction"])
        self.assertGreater(sess["cached_fraction"], 0.5)
        # turn 0 of sessions after the first reuses only the shared system prompt
        self.assertGreater(none["per_turn"]["0"]["cached_p50"], 2000)
        self.assertEqual(sess["per_turn"]["0"]["cached_p50"], 0)
        cmp = at.compare(none, req)
        self.assertGreater(cmp["ttft_p50_speedup"], 3)
        self.assertTrue(all(r["speedup"] > 1 for r in cmp["per_turn"].values()))

    def test_fanout_shares_brief(self):
        s = self.run_family("none", "fanout")
        # turn 0 reuses system prompt (1200 words) and the burst brief (600 words)
        self.assertGreater(s["per_turn"]["0"]["cached_p50"], 1200 + 600)


class Generator(unittest.TestCase):
    def test_families(self):
        for name, fn in gen.FAMILIES.items():
            with self.subTest(name):
                a, b = fn(seed=3), fn(seed=3)
                self.assertEqual(json.dumps(a), json.dumps(b))
                self.assertNotEqual(json.dumps(a), json.dumps(fn(seed=4)))
                systems = {s["system"] for s in a["sessions"]}
                self.assertEqual(len(systems), 1)
                self.assertEqual(
                    a["params"]["system_words"],
                    len(a["sessions"][0]["system"].splitlines()[1].split()),
                )
                for s in a["sessions"]:
                    pending = None
                    for t in s["turns"]:
                        for m in t["append"]:
                            if m["role"] == "tool":
                                self.assertEqual(m["tool_call_id"], pending)
                        rec = t["recorded"]
                        if t["expect_tool"]:
                            calls = [tc["function"] for tc in rec["tool_calls"]]
                            self.assertEqual(
                                at.validate_calls(calls, a["tools"]), (True, "ok")
                            )
                            self.assertEqual(calls[0]["name"], t["expect_tool"])
                            pending = rec["tool_calls"][0]["id"]
                        else:
                            self.assertIs(t, s["turns"][-1])

    def test_shapes(self):
        d = {n: gen.describe(fn(seed=0)) for n, fn in gen.FAMILIES.items()}
        self.assertEqual(d["support"]["sessions"], 16)
        self.assertEqual(d["coding"]["sessions"], 4)
        self.assertEqual(d["fanout"]["sessions"], 24)
        self.assertGreater(d["coding"]["last_prompt_words_p50"], 5000)
        self.assertGreater(d["support"]["system_words"], 2400)
        f = gen.fanout(seed=0)
        starts = [s["start_s"] for s in f["sessions"]]
        self.assertEqual(sorted(set(starts)), [0.0, 10.0, 20.0])

    def test_scale(self):
        self.assertEqual(len(gen.support(scale=0.01)["sessions"]), 1)
        self.assertEqual(len(gen.support(scale=2)["sessions"]), 32)


class Cli(unittest.TestCase):
    def test_gen_run_compare(self):
        s = Stub(mode="structured", per_word_s=0.0001)
        self.addCleanup(s.close)
        with tempfile.TemporaryDirectory() as d:
            tr = os.path.join(d, "t.json")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    at.main(["gen", "fanout", "-o", tr, "--scale", "0.34"]), 0
                )
                for bust in ("none", "request"):
                    rc = at.main(
                        [
                            "run",
                            tr,
                            "--url",
                            s.url,
                            "--model",
                            "m",
                            "--time-scale",
                            "0",
                            "--cache-bust",
                            bust,
                            "--out",
                            os.path.join(d, bust),
                        ]
                    )
                    self.assertEqual(rc, 0)
            buf = io.StringIO()
            with redirect_stdout(buf):
                at.main(
                    [
                        "compare",
                        os.path.join(d, "none.summary.json"),
                        os.path.join(d, "request.summary.json"),
                    ]
                )
            cmp = json.loads(buf.getvalue())
            self.assertGreater(cmp["ttft_p50_speedup"], 1)
            with open(os.path.join(d, "none.jsonl")) as f:
                recs = [json.loads(line) for line in f]
            with open(tr) as f:
                n_turns = sum(len(x["turns"]) for x in json.load(f)["sessions"])
            self.assertEqual(len(recs), n_turns)
            with open(os.path.join(d, "none.summary.json")) as f:
                summ = json.load(f)
            self.assertEqual(summ["tool_valid_structured"], 1.0)
            self.assertEqual(summ["family"], "fanout")


if __name__ == "__main__":
    unittest.main(verbosity=1)
