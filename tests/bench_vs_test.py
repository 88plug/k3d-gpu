#!/usr/bin/env python3
"""Tests for bench/vs.py against two tests/openai_stub.py servers. No hardware.

  python3 tests/bench_vs_test.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse

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
        self.assertIn("### H10 run (", md)
        self.assertIn("H10 row in ## Hypotheses to: SUPPORTED", md.splitlines()[0])
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
        res, _, _ = self.run_vs(["--itl", "0.002", "--no-usage"], ["--itl", "0.008", "--no-usage"])
        s = res["rounds"][0]["vs"]["samples"][0]
        self.assertEqual(s["tokens_source"], "chunks")
        self.assertEqual(s["output_tokens"], 16)
        self.assertEqual(res["verdict"], "SUPPORTED", res["why"])

    def test_mixed_token_rulers_are_inconclusive(self):
        res, _, _ = self.run_vs(["--itl", "0.002"], ["--itl", "0.008", "--no-usage"])
        self.assertEqual(res["verdict"], "INCONCLUSIVE")
        self.assertIn("counted tokens differently", res["why"])

    # Ollama v0.40.5 streams Qwen3 thinking in delta.reasoning (openai/openai.go).
    def test_ollama_reasoning_only_stream_counts(self):
        res, _, _ = self.run_vs(["--itl", "0.002"], ["--itl", "0.008", "--reasoning", "16"])
        s = res["rounds"][0]["vs"]["samples"][0]
        self.assertTrue(s["ok"], s["error"])
        self.assertEqual(s["content_chunks"], 16)
        self.assertEqual(s["output_tokens"], 16)
        self.assertEqual(res["verdict"], "SUPPORTED", res["why"])

    def test_ollama_reasoning_then_content_ttft_at_first_reasoning_chunk(self):
        port = self.stub("--tokens", "16", "--ttft", "0.005", "--itl", "0.05", "--reasoning", "8")
        opts = vs.argparse.Namespace(max_tokens=16, timeout=10)
        s = vs.request({"base_url": f"http://127.0.0.1:{port}/v1", "model": "m"}, "p", opts)
        self.assertTrue(s["ok"], s["error"])
        self.assertEqual(s["content_chunks"], 16)
        self.assertEqual(s["output_tokens"], 16)
        self.assertLess(s["ttft_s"], 0.2)  # first reasoning chunk, not the 9th chunk at ~0.4 s

    def test_early_stop_without_ignore_eos_is_inconclusive(self):
        res, _, _ = self.run_vs(["--itl", "0.002"], ["--itl", "0.008", "--eos-at", "8"])
        self.assertEqual(res["verdict"], "INCONCLUSIVE")
        self.assertIn("stopped early", res["why"])

    def test_budget_bounds_warmup(self):
        a = self.stub("--tokens", "16", "--ttft", "0.3", "--itl", "0.001")
        out = os.path.join(self.tmp, "w.json")
        vs.main(["run", "--arm", f"k3d-gpu=http://127.0.0.1:{a}/v3,m", "--arm", f"ollama=http://127.0.0.1:{a}/v1,m",
                 "--id", "H30", "--out", out, "--warmup", "50", "--rounds", "3", "--max-tokens", "16",
                 "--powercap", self.pc, "--budget-s", "1"])
        with open(out) as f:
            res = json.load(f)
        self.assertLess(res["elapsed_s"], 5)
        self.assertEqual(res["verdict"], "INCONCLUSIVE")

    def render_and_check(self, res_path, verdict):
        """Render docs from one result into a copy of the repo docs whose ledger
        row carries that verdict, then run claims_check on the copy."""
        root = os.path.join(self.tmp, "root")
        shutil.copytree(os.path.join(ROOT, "docs"), os.path.join(root, "docs"))
        shutil.copy(os.path.join(ROOT, "README.md"), root)
        with open(os.path.join(ROOT, "EXPERIMENTS.md")) as f:
            led = re.sub(r"^(\| H30 \|.*\| )PENDING \|$", rf"\g<1>{verdict} |", f.read(), flags=re.M)
        with open(os.path.join(root, "EXPERIMENTS.md"), "w") as f:
            f.write(led)
        vs.main(["docs", res_path, "--ledger", os.path.join(root, "EXPERIMENTS.md"),
                 "--out", os.path.join(root, "docs", "benchmarks.md")])
        p = subprocess.run(["bash", os.path.join(ROOT, "tests", "claims_check.sh"), root], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(root, "docs", "benchmarks.md")) as f:
            page = f.read()
        self.assertIn("[ledger:H31] | llamacpp", page)  # rows without a result stay PENDING
        self.assertIn(f"| {verdict} |", page)

    def test_docs_pass_claims_check_for_every_verdict(self):
        for fast, slow, extra, want in ((["--itl", "0.004"], ["--itl", "0.004"], ["--margin", "0.5"], "INCONCLUSIVE"),
                                        (["--itl", "0.008"], ["--itl", "0.002"], [], "REFUTED")):
            res, _, out = self.run_vs(fast, slow, *extra)
            self.assertEqual(res["verdict"], want)
            renamed = os.path.join(self.tmp, f"{want}.json")
            res["id"] = "H30"
            with open(renamed, "w") as f:
                json.dump(res, f)
            self.render_and_check(renamed, want)
            shutil.rmtree(os.path.join(self.tmp, "root"))

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
        vs.main(["docs", out, "--ledger", "/nonexistent", "--out", page])
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

    def test_margin_is_symmetric(self):
        def rnd(a, b):
            return {"round": 0, "k3d": {"decode_tokens_per_s": a, "failed": 0}, "vs": {"decode_tokens_per_s": b, "failed": 0}}
        # k3d at 95.5% of the competitor: the competitor is only 4.7% ahead, so not REFUTED.
        self.assertEqual(vs.verdict([rnd(9.55, 10)] * 3, 0.05, False, 3)[0], "INCONCLUSIVE")
        self.assertEqual(vs.verdict([rnd(10, 10.5)] * 3, 0.05, False, 3)[0], "REFUTED")
        self.assertNotIn("%", vs.verdict([rnd(10, 10)] * 3, 0.05, False, 3)[1])

    def test_ledger_heading_falls_back_to_full_cpu_name(self):
        res = {"id": "H30", "stack": "ollama", "summary": None, "verdict": "INCONCLUSIVE", "why": "x",
               "started": "2026-10-17T00:00:00Z", "elapsed_s": 1, "arms": [{"base_url": "u", "model": "m"}] * 2,
               "context": {"host_cpu_short": "", "host_cpu": "Intel(R) N100"},
               "params": {"budget_s": 9, "prompts": ["p"], "warmup": 0, "rounds": 1, "max_tokens": 2, "energy_zone": "z"}}
        self.assertIn("### H30 run (Intel(R) N100, 2026-10-17)", vs.ledger_md(res, "0" * 64, ""))
        self.assertIn("sign-off:        unsigned;", vs.ledger_md(res, "0" * 64, ""))
        res["context"] = {}
        self.assertIn("### H30 run (host, ", vs.ledger_md(res, "0" * 64, ""))

    def test_competitors_lock_pins_every_model_by_sha256(self):
        with open(os.path.join(ROOT, "bench", "competitors.lock")) as f:
            lock = json.load(f)["stacks"]
        for name, s in lock.items():
            self.assertRegex(s["artifact_sha256"], r"^[0-9a-f]{64}$", name)
            self.assertTrue(s["artifact_path"].startswith(s["cache"] + "/"), name)
        self.assertIn("/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf", " ".join(lock["llamacpp"]["args"]))
        self.assertNotIn("-hf", lock["llamacpp"]["args"])
        self.assertTrue(lock["ollama"]["artifact_path"].endswith("/" + lock["ollama"]["served_model"].replace(":", "/")))
        self.assertEqual(lock["ovms"]["user"], "0")

    def test_competitors_lock_pins_every_image_by_digest(self):
        with open(os.path.join(ROOT, "bench", "competitors.lock")) as f:
            lock = json.load(f)
        self.assertEqual(set(lock["stacks"]), {"ollama", "llamacpp", "ovms"})
        for name, s in lock["stacks"].items():
            self.assertRegex(s["image"], r"^[a-z0-9./_-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$", name)
            self.assertTrue(s["api"] in ("/v1", "/v3") and s["served_model"] and s["docs"].startswith("https://"), name)
            self.assertIn("4-bit", s["quant_tier"], name)


class RemoteArm(unittest.TestCase):
    """--remote-arm: a third-party endpoint is sampled (never greedy), carries the
    key from OPENAI_API_KEY, and the run is held to one round."""

    def test_remote_arm_requests_and_budget(self):
        seen = []

        def fake(args, idx):
            seen.append(args)
            return {"ok": True, "output_tokens": 8, "content_chunks": 8, "latency_s": 1.0, "ttft_s": 0.1,
                    "decode_tokens_per_s": 8.0, "tokens_source": "usage"}

        tmp = tempfile.mkdtemp()
        orig, env = vs.llm_load.one_request, os.environ.get("OPENAI_API_KEY")
        vs.llm_load.one_request, os.environ["OPENAI_API_KEY"] = fake, "sk-test-vs-0000"
        try:
            base = ["run", "--arm", "k3d-gpu=http://127.0.0.1:1/v3,m", "--arm", "remote=https://llm.example/v1,r",
                    "--remote-arm", "remote", "--id", "H0", "--out", os.path.join(tmp, "r.json"),
                    "--powercap", tmp]
            with self.assertRaises(SystemExit):
                vs.main(base + ["--rounds", "3", "--warmup", "0", "--max-tokens", "16"])
            with self.assertRaises(SystemExit):
                vs.main(base + ["--rounds", "1", "--warmup", "0", "--max-tokens", "128"])
            self.assertEqual(seen, [], "a refused run sends nothing")
            self.assertEqual(vs.main(base + ["--rounds", "1", "--warmup", "0", "--max-tokens", "16"]), 0)
        finally:
            vs.llm_load.one_request = orig
            if env is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = env
            shutil.rmtree(tmp)
        host = lambda a: urllib.parse.urlsplit(a.base_url).hostname  # noqa: E731
        remote = [a for a in seen if host(a) == "llm.example"]
        local = [a for a in seen if host(a) == "127.0.0.1"]
        self.assertTrue(remote and local)
        self.assertTrue(all(a.temperature > 0 and not a.ignore_eos and a.api_key == "sk-test-vs-0000" for a in remote))
        self.assertTrue(all(a.temperature == 0.0 and a.ignore_eos and a.api_key is None for a in local))

    def test_remote_arm_must_name_an_arm(self):
        with self.assertRaises(SystemExit):
            vs.main(["run", "--arm", "k3d-gpu=http://h:1/v3,m", "--arm", "x=https://h/v1,m", "--remote-arm", "y",
                     "--id", "H0", "--out", "/dev/null", "--rounds", "1", "--warmup", "0", "--max-tokens", "8"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
