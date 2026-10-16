#!/usr/bin/env python3
"""Tests for tool_compress.py, its agent_trace.py hook and the needle family.

    python3 bench/test_tool_compress.py      # stdlib only, no server
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_trace as at  # noqa: E402
import agent_trace_gen as gen  # noqa: E402
import compress_sim as sim  # noqa: E402
import tool_compress as tc  # noqa: E402
from test_agent_trace import Stub, run_args  # noqa: E402

SAMPLE_LOG = "\n".join(
    [f"tests/test_a.py::test_{i} PASSED" for i in range(60)]
    + ["E       assert 1 == 2", "tests/test_a.py:77: AssertionError"]
    + [f"tests/test_b.py::test_{i} PASSED" for i in range(60)]
)


class Budget(unittest.TestCase):
    def test_every_method_kind_ratio_fits_budget(self):
        for kind in tc.FIXTURES:
            for seed in range(8):
                fx = tc.fixture(kind, seed)
                for m in ("trunc", "keeplines", "randdrop"):
                    for r in (0.1, 0.3, 0.5, 0.7, 0.9):
                        out, st = tc.compress(fx["text"], m, r, fx["query"])
                        self.assertLessEqual(st["words_out"], tc.budget_words(fx["text"], r),
                                             (kind, seed, m, r))
                        self.assertEqual(st["words_out"], tc.nwords(out))

    def test_marker_words_matches_marker(self):
        self.assertEqual(tc.MARKER_WORDS, tc.nwords(tc.MARKER.format(n=12345)))

    def test_identity(self):
        for m in tc.METHODS:
            self.assertEqual(tc.compress(SAMPLE_LOG, m, 1.0)[0], SAMPLE_LOG)
        self.assertEqual(tc.compress(SAMPLE_LOG, "none", 0.1)[0], SAMPLE_LOG)
        self.assertEqual(tc.compress("", "keeplines", 0.5)[0], "")

    def test_unknown_method(self):
        with self.assertRaises(ValueError):
            tc.compress("x", "zip", 0.5)

    def test_markers_count_dropped_units(self):
        us = tc.units(SAMPLE_LOG)
        out, _ = tc.compress(SAMPLE_LOG, "keeplines", 0.3)
        kept = [x for x in out.split("\n") if not x.startswith("[... ")]
        dropped = sum(int(x.split()[1]) for x in out.split("\n") if x.startswith("[... "))
        self.assertEqual(len(kept) + dropped, len(us))


class Methods(unittest.TestCase):
    def test_trunc_keeps_both_ends(self):
        out, _ = tc.compress(SAMPLE_LOG, "trunc", 0.3)
        lines = out.split("\n")
        self.assertEqual(lines[0], "tests/test_a.py::test_0 PASSED")
        self.assertEqual(lines[-1], "tests/test_b.py::test_59 PASSED")
        self.assertNotIn("assert 1 == 2", out)

    def test_keeplines_keeps_the_failure(self):
        out, _ = tc.compress(SAMPLE_LOG, "keeplines", 0.1)
        self.assertIn("E       assert 1 == 2", out)
        self.assertIn("tests/test_a.py:77: AssertionError", out)

    def test_keeplines_is_trunc_without_cues(self):
        for seed in range(10):
            fx = tc.fixture("prose", seed)
            for r in (0.3, 0.5):
                self.assertEqual(tc.compress(fx["text"], "keeplines", r)[0],
                                 tc.compress(fx["text"], "trunc", r)[0])

    def test_query_promotes_matching_line(self):
        text = "\n".join([f"line {i} alpha" for i in range(50)] + ["needle_symbol here"]
                         + [f"line {i} beta" for i in range(50)])
        self.assertNotIn("needle_symbol", tc.compress(text, "keeplines", 0.2)[0])
        self.assertIn("needle_symbol", tc.compress(text, "keeplines", 0.2, '{"pattern": "needle_symbol"}')[0])

    def test_grep_definition_line_is_salient(self):
        self.assertTrue(tc.DEFN.search("src/a/b.py:12:def read_page(buf, n):"))
        self.assertFalse(tc.DEFN.search("src/a/b.py:12:    x = read_page(buf, n)"))

    def test_randdrop_deterministic(self):
        a = tc.compress(SAMPLE_LOG, "randdrop", 0.4)[0]
        self.assertEqual(a, tc.compress(SAMPLE_LOG, "randdrop", 0.4)[0])

    def test_long_line_is_split_into_units(self):
        us = tc.units(" ".join(["w"] * 100))
        self.assertEqual([tc.nwords(u) for u in us], [40, 40, 20])

    @unittest.skipIf(importlib.util.find_spec("llmlingua") is not None, "llmlingua installed")
    def test_llmlingua2_needs_its_package(self):
        with self.assertRaises(ImportError):
            tc.compress(SAMPLE_LOG, "llmlingua2", 0.5)


class Fixtures(unittest.TestCase):
    def test_deterministic_and_answer_present(self):
        for kind in tc.FIXTURES:
            for seed in range(20):
                a, b = tc.fixture(kind, seed), tc.fixture(kind, seed)
                self.assertEqual(a["text"], b["text"])
                for f in a["facts"]:
                    self.assertIn(f["answer"], a["text"], (kind, seed))

    def test_position_controls_truncation(self):
        # A fact at the very start survives head+tail truncation; one at the middle does not.
        for kind in ("grep", "diff", "prose"):
            head = tc.fixture(kind, 3, 800, pos=0.0)
            mid = tc.fixture(kind, 3, 800, pos=0.5)
            self.assertTrue(tc.retained(tc.compress(head["text"], "trunc", 0.3)[0], head["facts"][0]), kind)
            self.assertFalse(tc.retained(tc.compress(mid["text"], "trunc", 0.3)[0], mid["facts"][0]), kind)

    def test_retention_shape(self):
        r = tc.retention(("trunc", "keeplines"), (0.5,), kinds=("pytest", "prose"), n=20)
        self.assertEqual(set(r), {"pytest/trunc/0.5", "pytest/keeplines/0.5",
                                  "prose/trunc/0.5", "prose/keeplines/0.5"})
        self.assertEqual(r["prose/trunc/0.5"]["retained"], r["prose/keeplines/0.5"]["retained"])
        self.assertTrue(all(v["over_budget"] == 0 for v in r.values()))


class Service(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), tc.Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, obj):
        req = urllib.request.Request(self.base + "/compress", json.dumps(obj).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def test_compress_and_health(self):
        out = self.post({"text": SAMPLE_LOG, "method": "keeplines", "ratio": 0.2})
        self.assertEqual(out["text"], tc.compress(SAMPLE_LOG, "keeplines", 0.2)[0])
        self.assertLess(out["words_out"], out["words_in"])
        with urllib.request.urlopen(self.base + "/healthz", timeout=10) as r:
            self.assertTrue(json.loads(r.read())["ok"])

    def test_bad_method_is_400(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post({"text": "x", "method": "zip"})
        self.assertEqual(cm.exception.code, 400)
        cm.exception.close()


def needle_args(url, **kw):
    base = dict(tool_compress="none", compress_ratio=0.5, compress_url=None, compress_tools="")
    base.update(kw)
    return run_args(url, **base)


class Hook(unittest.TestCase):
    def history(self):
        return [{"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "run_tests", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]}]

    def test_compress_append_only_tool_messages(self):
        app = [{"role": "tool", "tool_call_id": "c1", "content": SAMPLE_LOG},
               {"role": "user", "content": SAMPLE_LOG}]
        out, st = at.compress_append(app, self.history(), needle_args("", tool_compress="keeplines"))
        self.assertLess(tc.nwords(out[0]["content"]), tc.nwords(SAMPLE_LOG))
        self.assertEqual(out[1]["content"], SAMPLE_LOG)
        self.assertEqual(st["tool_words_in"], tc.nwords(SAMPLE_LOG))
        self.assertEqual(st["tool_words_out"], tc.nwords(out[0]["content"]))
        self.assertEqual(app[0]["content"], SAMPLE_LOG)  # input not mutated

    def test_compress_tools_filter(self):
        app = [{"role": "tool", "tool_call_id": "c1", "content": SAMPLE_LOG},
               {"role": "tool", "tool_call_id": "c2", "content": SAMPLE_LOG}]
        out, _ = at.compress_append(app, self.history(),
                                    needle_args("", tool_compress="trunc", compress_tools="run_tests"))
        self.assertNotEqual(out[0]["content"], SAMPLE_LOG)
        self.assertEqual(out[1]["content"], SAMPLE_LOG)

    def test_answer_ok(self):
        self.assertTrue(at.answer_ok("It is  tests/x.py::t\n", "tests/x.py::t"))
        self.assertFalse(at.answer_ok("no", "tests/x.py::t"))
        self.assertIsNone(at.answer_ok("x", None))


class NeedleFamily(unittest.TestCase):
    def test_shape(self):
        tr = gen.needle(seed=1)
        names = {t["function"]["name"] for t in tr["tools"]}
        for s in tr["sessions"]:
            last = s["turns"][-1]
            self.assertIn(last["expect_answer"], json.dumps([t["append"] for t in s["turns"]]))
            self.assertEqual(last["append"][-1]["role"], "user")
            for t in s["turns"][:-1]:
                self.assertIn(t["expect_tool"], names)
                ok, why = at.validate_calls([t["recorded"]["tool_calls"][0]["function"]], tr["tools"])
                self.assertTrue(ok, why)

    def test_replay_compresses_history_and_grades(self):
        s = Stub(mode="text")
        self.addCleanup(s.close)
        tr = gen.needle(seed=2, scale=0.25)
        res, _ = at.run_trace(tr, needle_args(s.url, tool_compress="keeplines", compress_ratio=0.3))
        self.assertTrue(all(r["error"] is None for r in res))
        self.assertTrue(all(r["tool_words_out"] <= r["tool_words_in"] for r in res))
        self.assertLess(sum(r["tool_words_out"] for r in res), 0.35 * sum(r["tool_words_in"] for r in res))
        # The last request's history carries the compressed tool results.
        tool_msgs = [m for m in s.requests[-1]["messages"] if m["role"] == "tool"]
        self.assertTrue(any("lines omitted" in m["content"] for m in tool_msgs))
        summ = at.summarize(res, 1.0, {})
        self.assertEqual(summ["answer_graded"], 1)
        self.assertEqual(summ["answer_ok_rate"], 0.0)  # the stub never answers

    def test_only_graded_sends_one_request_with_full_history(self):
        s = Stub(mode="text")
        self.addCleanup(s.close)
        tr = gen.needle(seed=3, scale=0.5)
        res, _ = at.run_trace(tr, needle_args(s.url, only_graded=True))
        self.assertEqual(len(res), len(tr["sessions"]))
        self.assertEqual(len(s.requests), len(tr["sessions"]))
        by_len = sorted(len(r["messages"]) for r in s.requests)
        want = sorted(1 + sum(len(t["append"]) for t in x["turns"]) + len(x["turns"]) - 1
                      for x in tr["sessions"])
        self.assertEqual(by_len, want)

    def test_cli_flags(self):
        s = Stub(mode="text")
        self.addCleanup(s.close)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.json")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(at.main(["gen", "needle", "-o", p, "--scale", "0.25"]), 0)
                self.assertEqual(at.main(["run", p, "--url", s.url, "--model", "m", "--time-scale", "0",
                                          "--tool-compress", "trunc", "--compress-ratio", "0.5",
                                          "--out", os.path.join(d, "r")]), 0)
            with open(os.path.join(d, "r.summary.json")) as f:
                summ = json.load(f)
            self.assertEqual(summ["tool_compress"], "trunc")
            self.assertLessEqual(summ["tool_words_out"], 0.5 * summ["tool_words_in"] + 10)


class Sim(unittest.TestCase):
    def test_erlang_c(self):
        self.assertEqual(sim.erlang_c_wait(1.0, 1.0, 1), float("inf"))
        # M/M/1 at rho=0.5: Wq = rho / (mu - lam) = 1.0
        self.assertAlmostEqual(sim.erlang_c_wait(0.5, 1.0, 1), 1.0)
        self.assertLess(sim.erlang_c_wait(8.0, 1.0, 16), 0.05)

    def test_trunc_equals_compression_by_construction(self):
        # Same token budget -> same trace -> same A@SLO: the engine cannot tell them apart.
        arms = sim.arm_traces("fanout", 0.5, "cpu")
        self.assertEqual(arms["trunc"], arms["ecore"])

    def test_compressor_cost_scale(self):
        # 2000 tokens at 30% of one E-core's share of the int8 peak.
        s = sim.compress_s(2000, 0.30)
        self.assertTrue(10 < s < 20, s)


if __name__ == "__main__":
    unittest.main()
