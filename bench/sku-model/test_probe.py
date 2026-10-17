#!/usr/bin/env python3
"""Tests for bench/sku-model/probe.py: the scorer applies the pre-registered H24
outcome table to synthetic raw.jsonl files (no docker, no hardware), the stub
OVMS client parses a streamed reply, and --dry-run plans the frozen visit order.
Stdlib only."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe  # noqa: E402

PRED = probe.sku_model.h24_arms()


def raw_file(factor=None, triad=None, drift=1.0, flag_arm=None, drop=None):
    """Visits that measure PRED x factor[arm][metric] (default 1)."""
    factor = factor or {}
    rows = [{"kind": "header", "predictions_m0": PRED}]
    for arm, g in (triad or {}).items():
        rows += [{"kind": "triad", "arm": arm, "rep": r, "gbs": g} for r in range(2)]
    for i, arm in enumerate(probe.ORDER):
        if drop and arm in drop:
            continue
        f = factor.get(arm, {})
        last_a = arm == "A" and i == len(probe.ORDER) - 1
        d = PRED[arm]["decode_c1"] * f.get("decode_c1", 1) * (drift if last_a else 1)
        p = PRED[arm]["prefill"] * f.get("prefill", 1)
        c = PRED[arm]["c4_agg"] * f.get("c4_agg", 1)
        samples = [["decode", {"decode_tok_s": d * (1 + 0.01 * j), "completion_tokens": 128}] for j in range(3)]
        samples += [["prefill", {"prompt_tokens": 500, "ttft_s": 500 / p, "completion_tokens": 16}]
                    for _ in range(2)]
        samples.append(["c4", {"agg_tok_s": c, "completion_tokens": 512}])
        rows.append({"kind": "visit", "arm": arm, "visit": i, "void": False, "samples": samples,
                     "flag_busy": arm == flag_arm, "flag_swap": False, "throttle_delta": 0})
    f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".jsonl")
    f.write("\n".join(json.dumps(r) for r in rows) + "\n")
    f.close()
    return f.name


def scored(path):
    with contextlib.redirect_stdout(io.StringIO()):
        return probe.score(path)


class Score(unittest.TestCase):
    def test_exact_model_supported(self):
        self.assertTrue(scored(raw_file())["verdict"].startswith("SUPPORTED"))

    def test_prior_decode_refuted_m1_holds(self):
        # The author prior: E-core decode at about half, triad explains it.
        fac = {"B": {"decode_c1": 0.8}, "C": {"decode_c1": 0.5}, "D": {"decode_c1": 0.75}}
        tri = {"A": 50.0, "B": 40.0, "C": 25.0, "D": 37.5}
        v = scored(raw_file(fac, tri))
        self.assertEqual(v["verdict"], "REFUTED (row 2: M1 holds)")
        self.assertAlmostEqual(v["m1_decode"]["C"]["err"], 0.0, places=2)

    def test_decode_fails_no_triad(self):
        v = scored(raw_file({"C": {"decode_c1": 0.5}}))
        self.assertEqual(v["verdict"], "REFUTED (row 3: M0 and M1 decode fail)")

    def test_prefill_only(self):
        v = scored(raw_file({"D": {"prefill": 0.6}}))
        self.assertEqual(v["verdict"], "REFUTED (row 4: prefill does not transfer)")

    def test_drift_inconclusive(self):
        self.assertIn("row 6", scored(raw_file(drift=1.2))["verdict"])

    def test_flagged_visits_inconclusive(self):
        self.assertIn("row 6", scored(raw_file(flag_arm="C"))["verdict"])

    def test_partial_inconclusive(self):
        self.assertIn("partial", scored(raw_file(drop="D"))["verdict"])

    def test_decode_and_prefill_fail_m1_holds_is_mixed(self):
        # Formerly fell through to "c4 only, row 5"; no pre-registered row covers it.
        fac = {"C": {"decode_c1": 0.5, "prefill": 0.5}}
        tri = {"A": 50.0, "B": 50.0, "C": 25.0, "D": 50.0}
        v = scored(raw_file(fac, tri))
        self.assertEqual(v["verdict"], "REFUTED (mixed: no pre-registered row)")

    def test_c4_only_on_c_is_row5(self):
        v = scored(raw_file({"C": {"c4_agg": 0.5}}))
        self.assertEqual(v["verdict"], "REFUTED (row 5: c4 fails only on C)")

    def test_c4_on_b_is_mixed(self):
        v = scored(raw_file({"B": {"c4_agg": 0.5}}))
        self.assertEqual(v["verdict"], "REFUTED (mixed: no pre-registered row)")

    def test_baseline_scored(self):
        # Exact-M0 data: M0 absolute error 0, the efficiency-1 baseline overpredicts.
        v = scored(raw_file())
        self.assertIsNotNone(v["absolute_decode_mape"])
        self.assertLess(v["absolute_decode_mape"]["m0"], 0.02)
        self.assertGreater(v["absolute_decode_mape"]["baseline"], 0.3)

    def test_calibrates_on_run_a(self):
        # A whole-run offset (every arm 10% slow) is absorbed by arm A's calibration.
        fac = {a: {"decode_c1": 0.9, "prefill": 0.9, "c4_agg": 0.9} for a in "ABCD"}
        v = scored(raw_file(fac))
        self.assertTrue(v["verdict"].startswith("SUPPORTED"), v)
        self.assertAlmostEqual(v["a_vs_h6"], 0.9 * 1.01 * PRED["A"]["decode_c1"] / 15.66 - 1, places=3)  # median sample = 1.01x


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for _ in range(5):
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n')
        self.wfile.write(b'data: {"choices":[],"usage":{"completion_tokens":5,"prompt_tokens":9}}\n\n')
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


class ReasoningStub(Stub):
    # OVMS 2026.4.1 streams Qwen3 thinking as delta.reasoning_content (H24 run 1:
    # no delta.content in 128 tokens, so every TTFT was null).
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for _ in range(5):
            self.wfile.write(b'data: {"choices":[{"delta":{"reasoning_content":"x"}}]}\n\n')
        self.wfile.write(b'data: {"choices":[],"usage":{"completion_tokens":5,"prompt_tokens":9}}\n\n')
        self.wfile.write(b"data: [DONE]\n\n")


class Client(unittest.TestCase):
    def _chat(self, stub):
        srv = HTTPServer(("127.0.0.1", 0), stub)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            return probe.chat(srv.server_address[1], "hi", 5)
        finally:
            srv.shutdown()

    def test_chat_parses_stream(self):
        r = self._chat(Stub)
        self.assertEqual(r["completion_tokens"], 5)
        self.assertEqual(r["prompt_tokens"], 9)
        self.assertIsNotNone(r["ttft_s"])

    def test_chat_counts_reasoning_tokens(self):
        r = self._chat(ReasoningStub)
        self.assertIsNotNone(r["ttft_s"])
        self.assertIsNotNone(r["decode_tok_s"])

    def test_prefill_prompt_near_512_tokens(self):
        # "w123456" is ~7 Qwen3 tokens; 420 words gave ~2920 prompt tokens in run 1.
        n = len(probe.prefill_prompt().split())
        self.assertTrue(60 <= n <= 90, n)

    def test_rapl_delta_handles_wrap(self):
        top = 262143328850
        self.assertEqual(probe.rapl_delta_uj(top - 10, 5, top), 15)
        self.assertEqual(probe.rapl_delta_uj(100, 250, top), 150)

    def test_nonce_prompts_unique(self):
        self.assertNotEqual(probe.prefill_prompt(), probe.prefill_prompt())
        self.assertNotEqual(probe.decode_prompt()[:16], probe.decode_prompt()[:16])


class Plan(unittest.TestCase):
    def test_dry_run(self):
        out = subprocess.run([sys.executable, os.path.join(HERE, "probe.py"), "--dry-run"],
                             capture_output=True, text=True, check=True).stdout
        d = json.loads(out)
        self.assertEqual(d["order"], "ABCDDCBA")
        # >= 90 s under the outer `timeout 1140`, so cleanup runs before SIGTERM.
        self.assertLessEqual(d["deadline_s"], 1140 - 90)
        for cmd in d["launch"]:
            self.assertIn("--enable_prefix_caching", cmd)
            self.assertTrue(cmd[cmd.index("--name") + 1].startswith("sci-h24-"))

    def test_launch_binds_loopback(self):
        cmd = probe.launch("img", "/m", "C")
        self.assertIn("127.0.0.1:8243:8000", cmd)
        self.assertEqual(cmd[cmd.index("--cpuset-cpus") + 1], "16-23")
        self.assertIn('"INFERENCE_NUM_THREADS": "8"', cmd[-1])

    def test_each_arm_mounts_its_own_repository(self):
        # OVMS --source_model rewrites graph.pbtxt (which carries plugin_config) on
        # every start; a shared mount lets concurrent starts load each other's config.
        mounts = set()
        for k in probe.ARMS:
            cmd = probe.launch("img", "/m", k)
            mounts.add(cmd[cmd.index("-v") + 1])
        self.assertEqual(mounts, {f"/m/{k}:/models" for k in probe.ARMS})

    def test_graph_plugin_config_parsed(self):
        txt = "x\n  plugin_config: '{\"INFERENCE_NUM_THREADS\": \"4\"}',\n"
        self.assertEqual(probe.graph_plugin_config(txt), {"INFERENCE_NUM_THREADS": "4"})
        self.assertIsNone(probe.graph_plugin_config("no config"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
