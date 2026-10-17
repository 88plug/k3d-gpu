#!/usr/bin/env python3
"""Tests for bench/vs.py against two tests/openai_stub.py servers. No hardware.

  python3 tests/bench_vs_test.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bench"))
import vs  # noqa: E402

STUB = os.path.join(ROOT, "tests", "openai_stub.py")


def start_stub(*args):
    p = subprocess.Popen([sys.executable, STUB, "--port", "0", *args], stdout=subprocess.PIPE, text=True)
    line = p.stdout.readline().split()
    assert line[0] == "listening", line
    return p, int(line[1])


class StubPair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.procs = []
        self.pc = os.path.join(self.tmp, "powercap")
        z = os.path.join(self.pc, "intel-rapl:0")
        os.makedirs(z)
        for name, val in (("name", "package-0"), ("max_energy_range_uj", "1000000"), ("energy_uj", "500")):
            with open(os.path.join(z, name), "w") as f:
                f.write(val + "\n")

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()

    def stub(self, *args):
        p, port = start_stub(*args)
        self.procs.append(p)
        return port

    def run_vs(self, fast_args, slow_args, *extra):
        a = self.stub("--tokens", "16", "--ttft", "0.005", *fast_args)
        b = self.stub("--tokens", "16", "--ttft", "0.005", *slow_args)
        out = os.path.join(self.tmp, "r.json")
        led = os.path.join(self.tmp, "r.md")
        rc = vs.main(["run", "--arm", f"k3d-gpu=http://127.0.0.1:{a}/v3,m", "--arm", f"ollama=http://127.0.0.1:{b}/v1,m",
                      "--id", "H10", "--out", out, "--ledger", led, "--warmup", "1", "--rounds", "3",
                      "--max-tokens", "16", "--powercap", self.pc, "--signed-off-by", "Tester <t@example.com>", *extra])
        self.assertEqual(rc, 0)
        with open(out) as f:
            res = json.load(f)
        with open(led) as f:
            md = f.read()
        return res, md, out

    def test_supported_when_k3d_faster_every_round(self):
        res, md, _ = self.run_vs(["--itl", "0.002"], ["--itl", "0.008"])
        self.assertEqual(res["verdict"], "SUPPORTED", res["why"])
        self.assertEqual(len(res["rounds"]), 3)
        self.assertGreater(res["summary"]["k3d"]["decode_tokens_per_s"], res["summary"]["vs"]["decode_tokens_per_s"])
        self.assertIn("| H10 |", md)
        self.assertTrue(md.splitlines()[1].rstrip().endswith("| SUPPORTED |"))
        self.assertIn("sign-off:        Tester <t@example.com>; raw results sha256 ", md)
        self.assertIn("# Reproduce", md)

    def test_refuted_when_competitor_faster(self):
        res, md, _ = self.run_vs(["--itl", "0.008"], ["--itl", "0.002"])
        self.assertEqual(res["verdict"], "REFUTED", res["why"])

    def test_inconclusive_inside_margin(self):
        res, _, _ = self.run_vs(["--itl", "0.004"], ["--itl", "0.004"], "--margin", "0.5")
        self.assertEqual(res["verdict"], "INCONCLUSIVE")

    def test_rounds_interleave(self):
        res, _, _ = self.run_vs(["--itl", "0.001"], ["--itl", "0.001"])
        self.assertEqual([r["order"] for r in res["rounds"]],
                         [["k3d-gpu", "ollama"], ["ollama", "k3d-gpu"], ["k3d-gpu", "ollama"]])
        self.assertEqual(res["params"]["temperature"], 0.0)
        self.assertTrue(res["params"]["ignore_eos"])

    def test_budget_exhausted_is_inconclusive(self):
        res, _, _ = self.run_vs(["--itl", "0.002"], ["--itl", "0.008"], "--budget-s", "0")
        self.assertEqual(res["verdict"], "INCONCLUSIVE")
        self.assertIn("budget", res["why"])
        self.assertEqual(res["rounds"], [])

    def test_no_usage_falls_back_to_content_chunks(self):
        res, _, _ = self.run_vs(["--itl", "0.002"], ["--itl", "0.008", "--no-usage"])
        s = res["rounds"][0]["vs"]["samples"][0]
        self.assertEqual(s["tokens_source"], "chunks")
        self.assertEqual(s["output_tokens"], 16)
        self.assertEqual(res["verdict"], "SUPPORTED", res["why"])

    def test_failed_server_is_inconclusive(self):
        a = self.stub("--tokens", "8", "--itl", "0.001")
        out = os.path.join(self.tmp, "f.json")
        vs.main(["run", "--arm", f"k3d-gpu=http://127.0.0.1:{a}/v3,m", "--arm", "ollama=http://127.0.0.1:9/v1,m",
                 "--id", "H11", "--out", out, "--warmup", "0", "--rounds", "1", "--powercap", self.pc, "--timeout", "2"])
        with open(out) as f:
            res = json.load(f)
        self.assertEqual(res["verdict"], "INCONCLUSIVE")
        self.assertGreater(res["rounds"][0]["vs"]["failed"], 0)

    def test_docs_page_cites_ledger_on_every_number_line(self):
        _, _, out = self.run_vs(["--itl", "0.002"], ["--itl", "0.008"])
        page = os.path.join(self.tmp, "benchmarks.md")
        vs.main(["docs", out, "--out", page])
        with open(page) as f:
            text = f.read()
        rows = [ln for ln in text.splitlines() if ln.startswith("| [ledger:")]
        self.assertEqual(len(rows), 1)
        self.assertIn("[ledger:H10] | ollama", rows[0])
        self.assertIn("SUPPORTED", rows[0])

    def test_docs_rejects_foreign_json(self):
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w") as f:
            json.dump({"schema": "other"}, f)
        with self.assertRaises(SystemExit):
            vs.main(["docs", bad, "--out", os.path.join(self.tmp, "x.md")])


class Units(unittest.TestCase):
    def test_parse_arm(self):
        self.assertEqual(vs.parse_arm("ovms=http://h:1/v3,OpenVINO/Qwen3-4B-int4-ov"),
                         {"name": "ovms", "base_url": "http://h:1/v3", "model": "OpenVINO/Qwen3-4B-int4-ov"})
        for bad in ("ovms", "ovms=http://h", "=http://h,m", "a=,m"):
            with self.assertRaises(SystemExit):
                vs.parse_arm(bad)

    def test_rapl_wrap_and_missing(self):
        r = vs.Rapl("/nonexistent", "intel-rapl:0")
        self.assertIsNone(r.read())
        self.assertIsNone(r.delta_j(None, 5))
        r.range = 1_000_000
        self.assertAlmostEqual(r.delta_j(999_000, 500), 0.0015)
        self.assertAlmostEqual(r.delta_j(100, 400_100), 0.4)

    def test_verdict_rules(self):
        def rnd(a, b, failed=0):
            return {"round": 0, "k3d": {"decode_tokens_per_s": a, "failed": failed}, "vs": {"decode_tokens_per_s": b, "failed": 0}}
        self.assertEqual(vs.verdict([rnd(12, 10)] * 3, 0.05, False, 3)[0], "SUPPORTED")
        self.assertEqual(vs.verdict([rnd(8, 10)] * 3, 0.05, False, 3)[0], "REFUTED")
        self.assertEqual(vs.verdict([rnd(12, 10), rnd(8, 10), rnd(12, 10)], 0.05, False, 3)[0], "INCONCLUSIVE")
        self.assertEqual(vs.verdict([rnd(10.2, 10)] * 3, 0.05, False, 3)[0], "INCONCLUSIVE")
        self.assertEqual(vs.verdict([rnd(12, 10)] * 2, 0.05, False, 3)[0], "INCONCLUSIVE")
        self.assertEqual(vs.verdict([rnd(12, 10, failed=1)] * 3, 0.05, False, 3)[0], "INCONCLUSIVE")
        self.assertEqual(vs.verdict([rnd(None, 10)] * 3, 0.05, False, 3)[0], "INCONCLUSIVE")

    def test_competitors_lock_pins_every_image_by_digest(self):
        with open(os.path.join(ROOT, "bench", "competitors.lock")) as f:
            lock = json.load(f)
        self.assertEqual(set(lock["stacks"]), {"ollama", "llamacpp", "ovms"})
        for name, s in lock["stacks"].items():
            self.assertRegex(s["image"], r"^[a-z0-9./_-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$", name)
            self.assertTrue(s["api"] in ("/v1", "/v3") and s["served_model"] and s["docs"].startswith("https://"), name)
            self.assertIn("4-bit", s["quant_tier"], name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
