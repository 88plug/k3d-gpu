#!/usr/bin/env python3
"""Unit tests for bench/apb (stdlib only, no real server).

Run: python3 bench/apb/test_apb.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import apb  # noqa: E402

INF = float("inf")


class Stub:
    """OpenAI-compatible SSE stub. Answers every request with one structured
    tool call (the first declared tool, required args filled) or, for requests
    without tools, plain text. Tracks peak in-flight requests and bodies."""

    def __init__(self, delay_s=0.0):
        self.delay_s, self.bodies, self.inflight, self.peak = delay_s, [], 0, 0
        self.lock = threading.Lock()
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with stub.lock:
                    stub.bodies.append(body)
                    stub.inflight += 1
                    stub.peak = max(stub.peak, stub.inflight)
                try:
                    time.sleep(stub.delay_s)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    fn = body["tools"][0]["function"]
                    args = {k: ("x" if v["type"] == "string" else 1)
                            for k, v in fn["parameters"]["properties"].items()}
                    chunks = [
                        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": fn["name"], "arguments": ""}}]}}]},
                        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": json.dumps(args)}}]}}]},
                        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 5,
                                                  "prompt_tokens_details": {"cached_tokens": 80}}},
                    ]
                    for c in chunks:
                        self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                finally:
                    with stub.lock:
                        stub.inflight -= 1

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v3/chat/completions"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def rec(session, turn, sent, e2e, error=None, expect="get_user", valid=True, ctoks=10):
    return {"session": session, "turn": turn, "sent_at_s": sent, "e2e_s": e2e,
            "error": error, "ttft_s": 0.1, "completion_tokens": ctoks,
            "expect_tool": expect, "tool_valid": valid, "prompt_tokens": 100,
            "cached_tokens": 50}


def closed_loop_results(n_agents, n_sessions, lat, turns=4):
    """Back-to-back sessions on n_agents slots, each turn `lat` seconds."""
    out, free = [], [0.0] * n_agents
    for s in range(n_sessions):
        a = min(range(n_agents), key=lambda i: free[i])
        t = free[a]
        for k in range(turns):
            out.append(rec(f"s{s}", k, t, lat))
            t += lat
        free[a] = t
    return out


class TraceTest(unittest.TestCase):
    def test_closed_loop_trace(self):
        for fam, n, want in (("support", 1, 10), ("support", 8, 40), ("coding", 3, 15)):
            tr = apb.build_trace(fam, n, seed=7)
            self.assertEqual(len(tr["sessions"]), want)
            self.assertTrue(all(s["start_s"] == 0.0 for s in tr["sessions"]))
            self.assertEqual(tr["params"]["apb_agents"], n)

    def test_trace_deterministic_and_seeded(self):
        a = apb.build_trace("support", 4, seed=1004)
        b = apb.build_trace("support", 4, seed=1004)
        c = apb.build_trace("support", 4, seed=1005)
        self.assertEqual(a, b)
        self.assertNotEqual(a["sessions"][0]["system"], c["sessions"][0]["system"])

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            apb.build_trace("fanout", 2, 0)
        with self.assertRaises(ValueError):
            apb.build_trace("support", 0, 0)


class StatsTest(unittest.TestCase):
    def test_pctl_nearest_rank(self):
        xs = list(range(1, 101))
        self.assertEqual(apb.pctl(xs, 0.95), 95)
        self.assertEqual(apb.pctl(xs, 0.5), 50)
        self.assertEqual(apb.pctl([3.0], 0.95), 3.0)
        self.assertIsNone(apb.pctl([], 0.95))
        self.assertEqual(apb.pctl([1.0] * 19 + [INF], 0.95), 1.0)
        self.assertEqual(apb.pctl([1.0] * 18 + [INF] * 2, 0.95), INF)

    def test_latency_error_is_inf(self):
        self.assertEqual(apb.latency(rec("s", 0, 0, 3.0)), 3.0)
        self.assertEqual(apb.latency(rec("s", 0, 0, 3.0, error="timeout")), INF)
        self.assertEqual(apb.latency(rec("s", 0, 0, None)), INF)

    def test_bootstrap_ci_brackets_and_is_seeded(self):
        xs = [float(i) for i in range(200)]
        lo, hi = apb.bootstrap_ci(xs, seed=3)
        self.assertLessEqual(lo, apb.pctl(xs, 0.95))
        self.assertGreaterEqual(hi, apb.pctl(xs, 0.95))
        self.assertEqual((lo, hi), apb.bootstrap_ci(xs, seed=3))
        self.assertEqual(apb.bootstrap_ci([]), (None, None))


class WindowTest(unittest.TestCase):
    def test_window_bounds(self):
        # 2 agents, sessions of 4 turns x 1 s: s0,s1 start 0; s2,s3 start 4; s4,s5 start 8.
        res = closed_loop_results(2, 6, 1.0)
        w = apb.steady_window(res, 2)
        self.assertEqual(w, (4.0, 12.0))
        rows, _ = apb.window_requests(res, 2)
        self.assertTrue(all(4.0 <= r["sent_at_s"] < 12.0 for r in rows))
        self.assertEqual(len(rows), 16)

    def test_single_agent_drops_first_session_only(self):
        res = closed_loop_results(1, 3, 2.0, turns=2)
        rows, w = apb.window_requests(res, 1)
        self.assertEqual(w, (4.0, 12.0))
        self.assertEqual({r["session"] for r in rows}, {"s1", "s2"})

    def test_tail_excluded(self):
        # 2 agents; last session long, so one slot idles: window ends at the
        # first end among sessions running when the last one starts.
        res = [rec("s0", 0, 0, 1), rec("s1", 0, 0, 5), rec("s2", 0, 1, 1), rec("s3", 0, 2, 10)]
        self.assertEqual(apb.steady_window(res, 2), (1, 5))

    def test_too_few_sessions(self):
        self.assertIsNone(apb.steady_window(closed_loop_results(4, 4, 1.0), 4))
        self.assertEqual(apb.window_requests(closed_loop_results(4, 4, 1.0), 4), ([], None))


def step(agents, lat, sessions=10, errors=0, valid=True):
    res = closed_loop_results(agents, sessions, lat)
    for r in res[-errors:] if errors else []:
        r["error"] = "TimeoutError"
    for r in res:
        r["tool_valid"] = valid
    meta = {"agents": agents, "epoch_start": 0.0, "epoch_end": 100.0}
    return [(res, meta)] * 3


class ScoreTest(unittest.TestCase):
    def test_pass_fail_insufficient(self):
        self.assertEqual(apb.score_step(step(1, 2.0), slo=10)["verdict"], "pass")
        self.assertEqual(apb.score_step(step(1, 12.0), slo=10)["verdict"], "fail")
        few = step(1, 2.0, sessions=2)
        self.assertEqual(apb.score_step(few, slo=10)["verdict"], "insufficient")
        # too few requests but already over the SLO -> fail, not insufficient
        self.assertEqual(apb.score_step(step(1, 20.0, sessions=2), slo=10)["verdict"], "fail")

    def test_errors_and_tool_drop_fail(self):
        s = apb.score_step(step(2, 1.0, sessions=20, errors=8), slo=10)
        self.assertEqual(s["verdict"], "fail")
        self.assertTrue(any("error rate" in r for r in s["reasons"]))
        s = apb.score_step(step(2, 1.0, sessions=20, valid=False), slo=10, base_tool_valid=1.0)
        self.assertEqual(s["verdict"], "fail")
        self.assertTrue(any("tool validity" in r for r in s["reasons"]))

    def test_apb_rule(self):
        def s(n, p, v):
            return {"agents": n, "p95_s": p, "verdict": v, "_slo": 10}
        self.assertEqual(apb.apb([s(1, 5, "pass"), s(2, 8, "pass"), s(4, 12, "fail")]), (2, 2 + 2 / 4 * 2))
        self.assertEqual(apb.apb([s(1, 12, "fail")]), (0, None))
        # a pass after a fail does not count
        self.assertEqual(apb.apb([s(1, 5, "pass"), s(2, 12, "fail"), s(4, 9, "pass")])[0], 1)
        self.assertEqual(apb.apb([s(1, 5, "pass"), s(2, INF, "fail")]), (1, None))
        self.assertEqual(apb.apb([s(1, 5, "pass"), s(2, 6, "pass")]), (2, None))
        self.assertEqual(apb.apb_from_p95([(1, 5), (2, 11), (4, 9)], 10), 1)

    def test_finite_json(self):
        self.assertEqual(json.loads(json.dumps(apb.finite({"a": [INF, 1.0]}))), {"a": ["inf", 1.0]})


class EnergyTest(unittest.TestCase):
    def write(self, d, name, text):
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_rapl_wrap_and_bracket(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, "rapl.log", "0.0 900 10\n1.0 950 20\nbad line\n2.0 50 30\n3.0 150 -\n")
            s = apb.parse_rapl(p)
            self.assertEqual(len(s), 4)
            e = apb.rapl_energy(s, 0.5, 1.5, wrap_uj=1000)
            self.assertAlmostEqual(e["pkg_j"], (50 + 100) / 1e6)  # 900 -> 950 -> 50 (wrap)
            self.assertAlmostEqual(e["psys_j"], 20 / 1e6)
            self.assertEqual(e["seconds"], 2.0)
            self.assertIsNone(apb.rapl_energy(s, 1.0, 3.0, 1000)["psys_j"])  # '-' sample
            self.assertIsNone(apb.rapl_energy(s, -1.0, 1.0, 1000))
            self.assertIsNone(apb.rapl_energy(s, 1.0, 9.0, 1000))

    def test_nvidia_parse_and_integrate(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, "nvidia.log", "10.0, 0, 20.00\n10.0, 1, [N/A]\n12.0, 0, 40.00\n14.0, 0, 40.00\n")
            nv = apb.parse_nvidia(p)
            self.assertEqual(list(nv), [0])
            self.assertAlmostEqual(apb.trapezoid_j(nv[0], 10.0, 14.0), 30 * 2 + 40 * 2)
            self.assertAlmostEqual(apb.trapezoid_j(nv[0], 11.0, 13.0), (30 + 40) / 2 + 40)
            self.assertAlmostEqual(apb.trapezoid_j(nv[0], 14.0, 16.0), 80)  # held flat
            self.assertIsNone(apb.trapezoid_j([], 0, 1))

    def test_run_dir_energy_and_idle(self):
        with tempfile.TemporaryDirectory() as d:
            # package counter: 10 W during idle (t 0..10), 30 W after; GPU 0: 5 W flat
            lines = []
            uj = 0
            for t in range(0, 41):
                lines.append(f"{t}.0 {uj} -")
                uj += (10 if t < 10 else 30) * 1_000_000
            self.write(d, "rapl.log", "\n".join(lines) + "\n")
            self.write(d, "nvidia.log", "".join(f"{t}.0, 0, 5.0\n" for t in range(41)))
            self.write(d, "idle.json", json.dumps({"epoch_start": 0.0, "epoch_end": 10.0}))
            idle = apb.idle_power(d)
            self.assertAlmostEqual(idle["pkg_watts"], 10.0)
            self.assertAlmostEqual(idle["watts"], 15.0)
            res = [rec("s0", 0, 0, 1.0, ctoks=50), rec("s0", 1, 1, 1.0, ctoks=50)]
            e = apb.energy_for(d, [(res, {"epoch_start": 20.0, "epoch_end": 30.0})], idle)
            self.assertAlmostEqual(e["pkg_j"], 300.0)
            self.assertAlmostEqual(e["gpu_j"]["0"], 50.0)
            self.assertAlmostEqual(e["gross_j"], 350.0)
            self.assertAlmostEqual(e["net_j"], 350.0 - 15.0 * 10)
            self.assertAlmostEqual(e["gross_j_per_turn"], 175.0)
            self.assertAlmostEqual(e["net_j_per_out_token"], 2.0)


class SimTest(unittest.TestCase):
    def test_single_agent_latency_equals_demand(self):
        sessions = [([2.0, 3.0], [0.0, 1.0])] * 3
        lats = apb.simulate(sessions, 1, lambda k: k)
        self.assertEqual(sorted(set(round(x, 9) for x in lats)), [2.0, 3.0])

    def test_perfect_scaling_is_flat_and_flat_curve_slows(self):
        sessions = [([2.0, 2.0, 2.0], [0.0, 0.5, 0.5])] * 40
        perfect = apb.simulate(sessions, 8, lambda k: k)
        self.assertAlmostEqual(max(perfect), 2.0, places=6)
        capped = apb.simulate(sessions, 8, lambda k: min(k, 2))
        self.assertGreater(apb.pctl(capped, 0.95), 2.0 * 3)

    def test_g_curve_fits_c4(self):
        g = apb.g_curve(15.7, 56.0, "hyper")
        self.assertAlmostEqual(g(1), 1.0)
        self.assertAlmostEqual(g(4) * 15.7, 56.0)
        f = apb.g_curve(15.7, 56.0, "flat")
        self.assertAlmostEqual(f(32), f(4))
        self.assertGreater(g(32), f(32))


class RunStepTest(unittest.TestCase):
    def test_extra_body_and_concurrency_cap(self):
        stub = Stub(delay_s=0.02)
        try:
            tr = apb.build_trace("support", 3, seed=5)
            results, meta = apb.run_step(tr, stub.url, "m", 3,
                                         {"chat_template_kwargs": {"enable_thinking": False}},
                                         timeout=10, time_scale=0)
        finally:
            stub.close()
        self.assertEqual(len(results), sum(len(s["turns"]) for s in tr["sessions"]))
        self.assertTrue(all(r["error"] is None for r in results))
        self.assertTrue(all(b["chat_template_kwargs"] == {"enable_thinking": False} for b in stub.bodies))
        self.assertTrue(all(b["model"] == "m" and b["stream"] for b in stub.bodies))
        self.assertLessEqual(stub.peak, 3)
        self.assertEqual(stub.peak, 3)
        self.assertLess(meta["epoch_start"], meta["epoch_end"])
        import agent_trace
        self.assertIs(agent_trace.stream_chat.__name__, "stream_chat")  # patch undone


class RunShTest(unittest.TestCase):
    def test_ramp_end_to_end(self):
        stub = Stub(delay_s=0.01)
        with tempfile.TemporaryDirectory() as d:
            pc = os.path.join(d, "powercap")
            for z, name in (("intel-rapl:0", "package-0"), ("intel-rapl:1", "psys")):
                os.makedirs(os.path.join(pc, z))
                for f, v in (("name", name), ("energy_uj", "1000"), ("max_energy_range_uj", "262143328850")):
                    with open(os.path.join(pc, z, f), "w") as fh:
                        fh.write(v + "\n")
            env = {**os.environ, "OUT": os.path.join(d, "runs"), "STEPS": "1 2", "REPS": "2",
                   "IDLE_S": "1", "TIME_SCALE": "0", "NVIDIA": "0", "POWERCAP": pc,
                   "PATH": os.environ["PATH"]}
            try:
                p = subprocess.run([os.path.join(HERE, "run.sh"), "ramp", "stub", stub.url, "m"],
                                   env=env, capture_output=True, text=True, timeout=300)
            finally:
                stub.close()
            self.assertEqual(p.returncode, 0, p.stderr[-2000:])
            run = os.path.join(d, "runs", os.listdir(os.path.join(d, "runs"))[0])
            with open(os.path.join(run, "report.json")) as f:
                rep = json.load(f)
            self.assertEqual(rep["apb"], 2)
            self.assertEqual([s["agents"] for s in rep["steps"]], [1, 2])
            self.assertTrue(all(s["reps"] == 2 for s in rep["steps"]))
            self.assertIsNotNone(rep["drift_check"])
            self.assertTrue(os.path.exists(os.path.join(run, "facts.txt")))
            with open(os.path.join(run, "report.md")) as f:
                self.assertIn("APB = 2", f.read())
            names = sorted(os.listdir(os.path.join(run, "steps")))
            self.assertIn("n001-r1.jsonl", names)
            self.assertIn("n002-r2.meta.json", names)


if __name__ == "__main__":
    unittest.main(verbosity=1)
