#!/usr/bin/env python3
"""Tests for bench/cascade_model.py and bench/cascade_kernel.c (no server).

  python3 bench/test_cascade.py
"""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cascade_model as m  # noqa: E402
import cascade_probe  # noqa: E402

KERNEL = os.path.join(HERE, "cascade_kernel.c")
CFLAGS = ["-O3", "-mavx2", "-mfma", "-mavxvnni", "-fopenmp"]


def _vec(rng, n, s=1.0):
    return [rng.uniform(-s, s) for _ in range(n)]


class ReferenceMath(unittest.TestCase):
    def test_merge_is_split_invariant(self):
        rng = random.Random(0)
        q = _vec(rng, 8, 2)
        keys = [_vec(rng, 8) for _ in range(37)]
        vals = [_vec(rng, 8) for _ in range(37)]
        whole = m.attend(q, keys, vals, 0.3)
        for cuts in ([10], [1, 2, 36], [20, 21]):
            edges = [0] + cuts + [37]
            parts = [m.attend_partial(q, keys[a:b], vals[a:b], 0.3) for a, b in zip(edges, edges[1:])]
            for x, y in zip(m.merge(parts), whole):
                self.assertAlmostEqual(x, y, places=12)

    def test_cascade_equals_per_sequence_attention(self):
        rng = random.Random(1)
        B, Hk, G, D, P = 3, 2, 4, 6, 9
        pk = [[_vec(rng, D) for _ in range(P)] for _ in range(Hk)]
        pv = [[_vec(rng, D) for _ in range(P)] for _ in range(Hk)]
        lens = [0, 1, 5]  # empty, single-token and longer suffixes
        sk = [[[_vec(rng, D) for _ in range(n)] for _ in range(Hk)] for n in lens]
        sv = [[[_vec(rng, D) for _ in range(n)] for _ in range(Hk)] for n in lens]
        qs = [[_vec(rng, D, 3) for _ in range(Hk * G)] for _ in range(B)]
        got = m.cascade_decode(qs, pk, pv, sk, sv, G, 0.4)
        for b in range(B):
            for h in range(Hk * G):
                hk = h // G
                ref = m.attend(qs[b][h], pk[hk] + sk[b][hk], pv[hk] + sv[b][hk], 0.4)
                for x, y in zip(got[b][h], ref):
                    self.assertAlmostEqual(x, y, places=12)

    def test_fused_u8_dot_equals_dequant_dot(self):
        rng = random.Random(2)
        x, q = _vec(rng, 128), _vec(rng, 128, 2)
        codes, s, zp = m.quant_u8(x)
        deq = [(u - zp) * s for u in codes]
        self.assertAlmostEqual(m.dot_u8_fused(q, codes, s, zp), sum(a * b for a, b in zip(q, deq)), places=9)
        self.assertLess(max(abs(a - b) for a, b in zip(deq, x)), s)


class CacheSimulation(unittest.TestCase):
    def test_bhl_split_matches_openvino_splitter(self):
        # splitter(10, 4): sizes 3,3,2,2, contiguous
        (k,) = m.thread_traces("bhl", 10, 1, 0, 1, 4, False)[:1]
        self.assertEqual([len(t) for t in k], [3, 3, 2, 2])
        self.assertEqual([x[1][1] for x in k[1]], [3, 4, 5])

    def test_every_chunk_read_once_per_sequence_and_head(self):
        for path in ("bhl", "mixed"):
            for nthr in (1, 3, 8, 16):
                reads = [x for ph in m.thread_traces(path, 5, 7, 4, 3, nthr, True) for t in ph for x in t]
                self.assertEqual(len(reads), 5 * 7 * 3 * 2, (path, nthr))

    def test_shared_prefix_hits_l3_and_distinct_does_not(self):
        for path in ("bhl", "mixed"):
            sh = m.simulate_l3(path, 8, 3464, 3400, nthr=8, shared=True)
            di = m.simulate_l3(path, 8, 3464, 3400, nthr=8, shared=False)
            self.assertEqual(sh["dram_bytes"], sh["unique_bytes"], path)
            self.assertLess(sh["dram_bytes"], 0.2 * sh["naive_bytes"], path)
            self.assertEqual(di["dram_bytes"], di["naive_bytes"], path)

    def test_simulator_detects_lost_reuse(self):
        # A cache too small to hold one (sequence, head) stream must miss again
        # whenever no other thread reads the same block at the same time.
        r = m.simulate_l3("mixed", 8, 3464, 3400, nthr=8, shared=True, l3_bytes=4 * m.CHUNK_BYTES)
        self.assertGreater(r["dram_bytes"], 1.5 * r["unique_bytes"])
        r = m.simulate_l3("mixed", 8, 3464, 3400, nthr=1, shared=True, l3_bytes=4 * m.CHUNK_BYTES)
        self.assertEqual(r["dram_bytes"], r["naive_bytes"])


class StepModel(unittest.TestCase):
    def test_calibration_reproduces_h2_points(self):
        R = m.rates()
        self.assertAlmostEqual(m.agg_tps("stock", 1, 0, 0, R), 15.7, places=6)
        self.assertAlmostEqual(m.agg_tps("stock", 4, 0, 0, R), 56.0, places=6)

    def test_bandwidth_only_ablation(self):
        R = m.rates()
        stock = m.agg_tps("stock", 8, 3400, 64, R)
        # L3 reuse: reading the prefix once buys nothing over stock.
        self.assertAlmostEqual(m.agg_tps("cascade_bw_only", 8, 3400, 64, R), stock, places=9)
        # If DRAM traffic were serial and B-fold (the idea's framing), it would buy time.
        n_stock = m.agg_tps("stock", 8, 3400, 64, R, dram="naive", overlap=False)
        n_bw = m.agg_tps("cascade_bw_only", 8, 3400, 64, R, dram="naive", overlap=False)
        self.assertGreater(n_bw / n_stock, 1.1)

    def test_arm_ordering_and_long_context_penalty(self):
        R = m.rates()
        t = {a: m.agg_tps(a, 8, 3400, 64, R) for a in m.ARMS}
        self.assertGreater(t["cascade"], t["stock"])
        self.assertGreater(t["cascade_vnni"], t["cascade"])
        self.assertGreater(t["cascade_vnni"], t["vnni_gemv"])
        self.assertLess(t["stock"], 0.7 * m.agg_tps("stock", 8, 0, 64, R))
        # no shared prefix -> cascade has nothing to amortise
        self.assertAlmostEqual(m.agg_tps("cascade", 8, 0, 64, R), m.agg_tps("stock", 8, 0, 64, R), places=9)


class MeasuredRates(unittest.TestCase):
    def test_kernel_json_overrides_priors(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "k.jsonl")
            with open(path, "w") as f:
                for arm, g in (("stock", 100.0), ("stock", 120.0), ("stock", 110.0), ("cascade", 300.0)):
                    f.write(json.dumps({"arm": arm, "gmac_s": g}) + "\n")
            meas = m.load_kernel_rates(path)
        self.assertEqual(meas["stock"], 110e9)
        self.assertEqual(meas["cascade_bw_only"], 110e9)
        R = dict(m.rates(), measured=meas)
        t_stock = m.decode_attn_time("stock", 8, 3400, 512, R)
        t_casc = m.decode_attn_time("cascade", 8, 3400, 512, R)
        self.assertAlmostEqual(t_stock, m.attn_macs(8 * 3400 + 512) / 110e9)
        self.assertAlmostEqual(t_stock / t_casc, 300 / 110)


class AgentSimulation(unittest.TestCase):
    def test_p95_grows_with_agents(self):
        R = m.rates()
        p95 = [m.simulate_agents("stock", n, R, horizon=400, warmup=60)["p95"] for n in (2, 8, 20)]
        self.assertLess(p95[0], p95[1])
        self.assertLess(p95[1], p95[2])

    def test_faster_kernel_never_hosts_fewer_agents(self):
        R = m.rates()
        kw = dict(horizon=400, warmup=60, grid=list(range(1, 25)))
        self.assertGreaterEqual(m.agents_at_slo("cascade", 20, R, **kw), m.agents_at_slo("stock", 20, R, **kw))


class LiveAnalysis(unittest.TestCase):
    def _run(self, d, name, lat, errors=0):
        p = os.path.join(d, name)
        with open(p, "w") as f:
            for i, x in enumerate(lat):
                f.write(json.dumps({"sent_at_s": 200 + i, "e2e_s": x, "error": None}) + "\n")
            f.write(json.dumps({"sent_at_s": 10, "e2e_s": 999, "error": None}) + "\n")  # warmup, ignored
            for _ in range(errors):
                f.write(json.dumps({"sent_at_s": 300, "e2e_s": None, "error": "timeout"}) + "\n")
        return p

    def test_aslo_takes_largest_n_before_first_fail(self):
        with tempfile.TemporaryDirectory() as d:
            runs = {
                4: [self._run(d, "a", [5] * 40)],
                8: [self._run(d, "b", [10] * 40)],
                12: [self._run(d, "c", [25] * 40)],
                16: [self._run(d, "d", [12] * 40)],  # passes after a fail: not counted
            }
            best, table = m.aslo(runs, 20)
            self.assertEqual(best, 8)
            self.assertEqual(table[12], 25)
            p95, n, errs = m.p95_from_jsonl(self._run(d, "e", list(range(1, 101)), errors=2))
            self.assertEqual((p95, n, errs), (96, 100, 2))



class _Stub(http.server.BaseHTTPRequestHandler):
    """Streams `max_tokens` content deltas, one every DELAY s, then usage."""

    DELAY = 0.01
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Stub.seen.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        n = body["max_tokens"]
        for i in range(n):
            time.sleep(self.DELAY)
            chunk = {"choices": [{"delta": {"content": f"t{i} "}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
        usage = {"usage": {"prompt_tokens": 100, "completion_tokens": n}, "choices": []}
        self.wfile.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())

    def log_message(self, *a):
        pass


class Probe(unittest.TestCase):
    def test_prompt_modes(self):
        sh = cascade_probe.prompts("shared", 4, 50, 5)
        di = cascade_probe.prompts("distinct", 4, 50, 5)
        self.assertEqual(len({s for s, _ in sh}), 1)
        self.assertEqual(len({s for s, _ in di}), 4)
        self.assertEqual(len({u for _, u in sh}), 4)
        self.assertEqual([u for _, u in sh], [u for _, u in di])
        self.assertTrue(all(s.endswith(sh[0][0]) for s, _ in di))  # same prefix text, nonce in front

    def test_window_rate(self):
        rs = [dict(error=None, stamps=[0.0 + k, 1.0 + k, 2.0 + k, 3.0 + k]) for k in (0.0, 0.5)]
        w = cascade_probe.window_rate(rs)
        self.assertEqual((w["lo"], w["hi"]), (0.5, 3.0))
        self.assertEqual(w["tokens"], 3 + 2)  # (1,2,3] from stream 0 and (0.5..3] -> 1.5,2.5 from stream 1
        self.assertIsNone(cascade_probe.window_rate([dict(error="x", stamps=[1, 2, 3])]))

    def test_run_against_stub(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                out = os.path.join(d, "r.json")
                _Stub.seen.clear()
                rc = cascade_probe.main(["run", "--url", f"http://127.0.0.1:{srv.server_port}/v3/chat/completions",
                                         "--model", "m", "--mode", "shared", "--batch", "3",
                                         "--prefix-words", "20", "--max-tokens", "40", "--out", out])
                with open(out) as f:
                    r = json.load(f)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(rc, 0)
        self.assertTrue(r["valid"])
        warm, timed = _Stub.seen[:3], _Stub.seen[3:]
        self.assertTrue(all(b["max_tokens"] == 1 and "ignore_eos" not in b for b in warm))
        self.assertTrue(all(b["max_tokens"] == 40 and b["ignore_eos"] for b in timed))
        # 3 streams x 1 token per 10 ms -> ~300 tok/s while all decode (sleep overshoot lowers it)
        self.assertGreater(r["agg_decode_tps"], 120)
        self.assertLess(r["agg_decode_tps"], 330)

    def test_imc_bytes_per_step(self):
        with tempfile.TemporaryDirectory() as d:
            perf = os.path.join(d, "p.csv")
            with open(perf, "w") as f:
                f.write("# started\n")
                for k in range(1, 11):  # 1 s intervals, 2 channels x 1024 MiB each
                    for ch in (0, 1):
                        f.write(f"{k:.6f},1024.00,MiB,uncore_imc_free_running_{ch}/data_read/,1000,100.00,,\n")
            idle = os.path.join(d, "i.csv")
            with open(idle, "w") as f:
                for k in range(1, 5):
                    f.write(f"{k:.6f},256.00,MiB,uncore_imc_free_running_0/data_read/,1000,100.00,,\n")
            run = os.path.join(d, "r.json")
            with open(run, "w") as f:
                json.dump({"window_wall": [1002.0, 1006.0], "window_tokens": 160, "batch": 8}, f)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cascade_probe.main(["imc", "--perf", perf, "--perf-start", "1000", "--run", run, "--idle", idle])
            r = json.loads(buf.getvalue())
        self.assertAlmostEqual(r["steps"], 20)
        self.assertAlmostEqual(r["gb_per_step_gross"], 4 * 2048 * 2**20 / 20 / 1e9)
        self.assertAlmostEqual(r["gb_per_step"], 4 * (2048 - 256) * 2**20 / 20 / 1e9)


def _vnni_host():
    try:
        with open("/proc/cpuinfo") as f:
            return " avx_vnni" in f.read()
    except OSError:
        return False


@unittest.skipUnless(shutil.which("cc") and _vnni_host(), "needs a C compiler and an AVX-VNNI CPU")
class Kernel(unittest.TestCase):
    def _build(self, d, src_text, name, strict=True):
        src = os.path.join(d, name + ".c")
        with open(src, "w") as f:
            f.write(src_text)
        exe = os.path.join(d, name)
        warn = ["-Wall", "-Wextra", "-Werror"] if strict else []
        subprocess.run(["cc", *CFLAGS, *warn, "-o", exe, src, "-lm"], check=True)
        return exe

    def test_all_arms_match_fp64_reference(self):
        with tempfile.TemporaryDirectory() as d, open(KERNEL) as f:
            exe = self._build(d, f.read(), "ck")
            r = subprocess.run([exe, "check"], capture_output=True, text=True, timeout=120)
            rows = [json.loads(x) for x in r.stdout.splitlines()]
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertEqual(len(rows), 20)
            self.assertTrue(all(x["ok"] for x in rows))

    def test_bench_smoke_single_arm(self):
        with tempfile.TemporaryDirectory() as d, open(KERNEL) as f:
            exe = self._build(d, f.read(), "ck")
            r = subprocess.run([exe, "bench", "2", "64", "32", "2", "3", "cascade"],
                               capture_output=True, text=True, timeout=60, check=True)
        rows = [json.loads(x) for x in r.stdout.splitlines()]
        self.assertEqual([x["arm"] for x in rows], ["cascade"])
        self.assertGreater(rows[0]["gmac_s"], 0)

    def test_check_catches_planted_bugs(self):
        with open(KERNEL) as f:
            src = f.read()
        bugs = {
            "online softmax rescale dropped": ("float c = a->l ? expf(a->m - m) : 0, l", "float c = a->l ? 1.0f : 0, l"),
            "lse merge rescale dropped": ("ca = a->l ? expf(a->m - m) : 0", "ca = a->l ? 1.0f : 0"),
            "vnni zero point dropped": ("- f[1] * (float)qsum[r + u])", ")"),
            "prefix V tile read from K": ("const uint8_t *b = vr + t * ROWB;", "const uint8_t *b = kr + t * ROWB;"),
            "prefix rows mapped to the wrong head": ("qrow[r] = p->q + ((size_t)b * p->Hk * p->G + hk * p->G + g) * D;",
                                                     "qrow[r] = p->q + ((size_t)b * p->Hk * p->G + g) * D;"),
        }
        with tempfile.TemporaryDirectory() as d:
            for i, (name, (a, b)) in enumerate(bugs.items()):
                self.assertEqual(src.count(a), 1, name)
                exe = self._build(d, src.replace(a, b), f"mut{i}", strict=False)
                r = subprocess.run([exe, "check"], capture_output=True, text=True, timeout=120)
                self.assertNotEqual(r.returncode, 0, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
