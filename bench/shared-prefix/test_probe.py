#!/usr/bin/env python3
"""Tests for bench/shared-prefix/probe.py: prompt generator and scorer (synthetic rounds)."""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe  # noqa: E402


def rnd(arm, seed, added, a1=9.6, c=9.5, drss=110.0, tokens=1650, text_len=5, completion=None):
    req = lambda t: {"ttft": t, "total": t + 0.5, "text_len": text_len, "prompt_tokens": tokens,  # noqa: E731
                     "completion_tokens": completion, "error": None}
    S = [dict(req(a1), agent="a1")] + [dict(req(t), agent=f"a{i + 2}") for i, t in enumerate(added)]
    S.append(dict(req(c), agent="c"))
    return {"arm": arm, "seed": seed, "S": S, "B": [dict(req(1.0), agent=f"b{k}") for k in range(6)],
            "drss_per_agent_mib": drss, "swap_in_burst": False, "graph_ok": True}


class Gen(unittest.TestCase):
    def test_deterministic_and_control(self):
        a, b = probe.gen(7), probe.gen(7)
        self.assertEqual(a, b)
        self.assertNotEqual(a["system"], a["control_system"])
        i = next(k for k, (x, y) in enumerate(zip(a["system"], a["control_system"])) if x != y)
        self.assertTrue(60 <= i <= 90, i)  # about token 16
        self.assertEqual(len(a["tools"]), 12)
        self.assertNotEqual(probe.gen(8)["system"], a["system"])

    def test_word_counts_are_seed_invariant(self):
        # Token counts are fixed by word counts (one token per word); check the
        # word and character-class shape is identical for every seed and agent.
        shape = lambda s: [len(s.split()), s.count('"')]  # noqa: E731
        ref = None
        for seed in range(2501, 2507):
            g = probe.gen(seed)
            privs = {len(u.split()) for u in g["a"] + g["b"] + [g["c"]]}
            self.assertEqual(privs, {probe.PRIVATE_WORDS + 1})
            self.assertEqual(len(g["system"].split()), len(g["control_system"].split()))
            got = [shape(g["system"])] + [shape(json.dumps(t)) for t in g["tools"]]
            ref = ref or got
            self.assertEqual(got, ref)

    @unittest.skipUnless(os.environ.get("H25_TOKENIZER_DIR"), "set H25_TOKENIZER_DIR to the staged Qwen3 model dir")
    def test_qwen3_token_counts_identical(self):
        from tokenizers import Tokenizer  # tokenizers 0.22.2
        t = Tokenizer.from_file(os.path.join(os.environ["H25_TOKENIZER_DIR"], "tokenizer.json"))
        n = lambda s: len(t.encode(s, add_special_tokens=False).ids)  # noqa: E731
        counts = set()
        for seed in range(2501, 2507):
            g = probe.gen(seed)
            self.assertEqual({n(u) for u in g["a"] + g["b"] + [g["c"]]}, {96})
            counts.add((n(g["system"]), n(g["control_system"]),
                        tuple(n(json.dumps(x, ensure_ascii=False)) for x in g["tools"])))
        self.assertEqual(len(counts), 1, counts)
        self.assertEqual(next(iter(counts))[0], next(iter(counts))[1])


class Request(unittest.TestCase):
    def test_tool_call_delta_counts_as_first_token(self):
        import http.server
        import threading
        events = [{"choices": [{"delta": {"role": "assistant"}}]},
                  {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "eat_tool_00"}}]}}]},
                  {"choices": [], "usage": {"prompt_tokens": 1634}}]

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()
                for e in events:
                    self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.handle_request, daemon=True).start()
        r = probe.request(f"http://127.0.0.1:{srv.server_port}/v3/chat/completions", "s", "u", [{}])
        srv.server_close()
        self.assertIsNone(r["error"])
        self.assertIsNotNone(r["ttft"])
        self.assertGreater(r["text_len"], 0)
        self.assertEqual(r["prompt_tokens"], 1634)

    def test_role_chunk_is_first_token_when_parser_holds_text(self):
        # The OVMS stream seen live (H25 run 2): a role delta with content null
        # after prefill, then only the finish chunk and usage.
        import http.server
        import threading
        events = [(0.3, {"choices": [{"index": 0, "delta": {"role": "assistant", "content": None}}]}),
                  (0.2, {"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]}),
                  (0.0, {"choices": [], "usage": {"prompt_tokens": 1634, "completion_tokens": 8}})]

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()
                for delay, e in events:
                    time.sleep(delay)
                    self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.handle_request, daemon=True).start()
        r = probe.request(f"http://127.0.0.1:{srv.server_port}/v3/chat/completions", "s", "u", [{}])
        srv.server_close()
        self.assertIsNone(r["error"])
        self.assertTrue(0.25 <= r["ttft"] < 0.45, r["ttft"])
        self.assertGreater(r["total"], r["ttft"] + 0.15)
        self.assertEqual(r["text_len"], 0)
        self.assertEqual(r["completion_tokens"], 8)


class Score(unittest.TestCase):
    def run_score(self, rounds):
        with tempfile.TemporaryDirectory() as d:
            files = []
            for i, r in enumerate(rounds):
                f = os.path.join(d, f"round-{i}.json")
                json.dump(r, open(f, "w"))
                files.append(f)
            return probe.score(files)

    def test_supported(self):
        on = [rnd("ON", s, [0.9, 0.85, 0.95, 0.9, 0.88], drss=12.0) for s in (1, 4, 5)]
        off = [rnd("OFF", s, [9.6, 9.4, 9.7, 9.5, 9.8]) for s in (2, 3, 6)]
        out = self.run_score(on + off)
        self.assertEqual(out["T"], "SUPPORTED")
        self.assertGreater(out["ci95"][0], 4)
        self.assertEqual(out["confidence_pairs_ge_4x"], 1.0)
        self.assertTrue(out["M"]["verdict"].startswith("SUPPORTED"))

    def test_refuted(self):
        on = [rnd("ON", s, [9.0, 9.1, 9.2, 9.0, 9.1]) for s in (1, 4, 5)]
        off = [rnd("OFF", s, [9.6, 9.4, 9.7, 9.5, 9.8]) for s in (2, 3, 6)]
        self.assertEqual(self.run_score(on + off)["T"], "REFUTED")

    def test_warm_control_is_void(self):
        on = [rnd("ON", s, [0.9] * 5, c=0.9) for s in (1, 4, 5)]
        off = [rnd("OFF", s, [9.6] * 5) for s in (2, 3, 6)]
        self.assertTrue(self.run_score(on + off)["T"].startswith("VOID"))

    def test_token_mismatch_inconclusive(self):
        on = [rnd("ON", s, [0.9] * 5, tokens=1600) for s in (1, 4, 5)]
        off = [rnd("OFF", s, [9.6] * 5) for s in (2, 3, 6)]
        self.assertEqual(self.run_score(on + off)["T"], "INCONCLUSIVE")

    def test_two_void_rounds_inconclusive(self):
        on = [rnd("ON", s, [0.9] * 5) for s in (1, 4, 5)]
        for r in on[:2]:
            r["graph_ok"] = False
        off = [rnd("OFF", s, [9.6] * 5) for s in (2, 3, 6)]
        self.assertTrue(self.run_score(on + off)["T"].startswith("INCONCLUSIVE"))

    def load_file(self, d, on_load1, off_load1):
        f = os.path.join(d, "load.jsonl")
        with open(f, "w") as fh:
            for arm, l1 in (("ON", on_load1), ("OFF", off_load1)):
                for _ in range(2):
                    fh.write(json.dumps({"tag": arm, "level": "typical", "busy_pct": 37.0, "load1": l1,
                                         "pkg_w": 40.0, "throttle_per_min": 0.0, "swap_used_mib": 100.0,
                                         "swap_delta_mib": 0.0}) + "\n")
        return f

    def score_with_load(self, rounds, on_load1, off_load1):
        with tempfile.TemporaryDirectory() as d:
            files = []
            for i, r in enumerate(rounds):
                files.append(os.path.join(d, f"round-{i}.json"))
                json.dump(r, open(files[-1], "w"))
            return probe.score(files, self.load_file(d, on_load1, off_load1))

    def test_matched_high_load_keeps_verdict(self):
        on = [rnd("ON", s, [0.9, 0.85, 0.95, 0.9, 0.88]) for s in (1, 3, 5)]
        off = [rnd("OFF", s, [9.6, 9.4, 9.7, 9.5, 9.8]) for s in (2, 4, 6)]
        out = self.score_with_load(on + off, 24.0, 25.0)  # high but matched
        self.assertTrue(out["load"]["matched"])
        self.assertEqual(out["T"], "SUPPORTED")

    def test_unmatched_load_is_inconclusive(self):
        on = [rnd("ON", s, [0.9, 0.85, 0.95, 0.9, 0.88]) for s in (1, 3, 5)]
        off = [rnd("OFF", s, [9.6, 9.4, 9.7, 9.5, 9.8]) for s in (2, 4, 6)]
        out = self.score_with_load(on + off, 5.0, 20.0)
        self.assertFalse(out["load"]["matched"])
        self.assertTrue(out["T"].startswith("INCONCLUSIVE: load unmatched (load1"), out["T"])
        self.assertEqual(out["M"], out["T"])

    def score_with_a27(self, rounds, off_during_throttle, off_pre_busy=37.0, off_running=4):
        """Amendment 27 windows (as run.sh A4 writes them): one pre and two during per arm."""
        with tempfile.TemporaryDirectory() as d:
            files = []
            for i, r in enumerate(rounds):
                files.append(os.path.join(d, f"round-{i}.json"))
                json.dump(r, open(files[-1], "w"))
            f = os.path.join(d, "load.jsonl")
            with open(f, "w") as fh:
                for arm in ("ON", "OFF"):
                    off = arm == "OFF"
                    base = {"tag": arm, "level": "typical", "load_expected": 4, "swap_used_mib": 100.0,
                            "swap_delta_mib": 0.0, "load1": 20.0, "pkg_w": 90.0}
                    fh.write(json.dumps(dict(base, phase="pre", busy_pct=off_pre_busy if off else 37.0,
                                             throttle_per_min=10.0, settled=True, load_running=4)) + "\n")
                    for _ in range(2):
                        fh.write(json.dumps(dict(base, phase="during", busy_pct=37.0,
                                                 throttle_per_min=off_during_throttle if off else 10.0,
                                                 load_running=off_running if off else 4)) + "\n")
            return probe.score(files, f)

    def a27_rounds(self):
        return ([rnd("ON", s, [0.9, 0.85, 0.95, 0.9, 0.88]) for s in (1, 3, 5)]
                + [rnd("OFF", s, [9.6, 9.4, 9.7, 9.5, 9.8]) for s in (2, 4, 6)])

    def test_a27_treatment_throttle_does_not_gate(self):
        out = self.score_with_a27(self.a27_rounds(), off_during_throttle=1400.0)
        self.assertEqual(out["load"]["rule"], "amendment-27")
        self.assertTrue(out["load"]["matched"], out["load"])
        self.assertEqual(out["T"], "SUPPORTED")

    def test_a27_pre_window_drift_is_inconclusive(self):
        out = self.score_with_a27(self.a27_rounds(), off_during_throttle=10.0, off_pre_busy=60.0)
        self.assertTrue(out["T"].startswith("INCONCLUSIVE: load unmatched (pre busy_pct"), out["T"])

    def test_a27_missing_load_is_void(self):
        out = self.score_with_a27(self.a27_rounds(), off_during_throttle=10.0, off_running=3)
        self.assertTrue(out["T"].startswith("VOID: load missing"), out["T"])

    def test_no_burst_rounds_score_t_and_leave_m_open(self):
        def nb(r):
            r.update(B=[], drss_per_agent_mib=None)
            return r
        on = [nb(rnd("ON", s, [0.9, 0.85, 0.95, 0.9, 0.88], text_len=0, completion=8)) for s in (1, 3, 5)]
        off = [nb(rnd("OFF", s, [9.6, 9.4, 9.7, 9.5, 9.8], text_len=0, completion=8)) for s in (2, 4, 6)]
        out = self.run_score(on + off)
        self.assertEqual(out["void"], {})
        self.assertEqual(out["T"], "SUPPORTED")
        self.assertTrue(out["M"].startswith("INCONCLUSIVE"))

    def test_zero_completion_tokens_is_empty_output(self):
        on = [rnd("ON", s, [0.9] * 5, text_len=0, completion=0) for s in (1, 3, 5)]
        off = [rnd("OFF", s, [9.6] * 5) for s in (2, 4, 6)]
        out = self.run_score(on + off)
        self.assertIn("empty output", out["void"]["ON/1"])
        self.assertTrue(out["T"].startswith("INCONCLUSIVE"))

    def test_missing_arm(self):
        on = [rnd("ON", s, [0.9] * 5) for s in (1, 4, 5)]
        self.assertTrue(self.run_score(on)["T"].startswith("INCONCLUSIVE"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
