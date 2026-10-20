#!/usr/bin/env python3
"""Tests for bench/h54 (EXPERIMENTS.md "H54 amendment R1"): the arm renders and their flags, the
GSM8K grader, the void rule, the tensor-table byte counts, the bootstrap, the chosen-arm source
rule and the H54a verdicts. No cluster, no network."""
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import h54  # noqa: E402

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


class Grade(unittest.TestCase):
    def test_last_number_wins(self):
        self.assertTrue(h54.grade("16 - 3 - 4 = 9 eggs, 9 * 2 = 18\nAnswer: 18", "... #### 18"))
        self.assertFalse(h54.grade("Answer: 18\nActually 17", "#### 18"))

    def test_commas_and_decimals(self):
        self.assertTrue(h54.grade("Answer: 1,250", "#### 1,250"))
        self.assertTrue(h54.grade("Answer: $70,000.00", "#### 70000"))
        self.assertTrue(h54.grade("Answer: -3", "#### -3"))
        self.assertFalse(h54.grade("no number here", "#### 5"))
        self.assertFalse(h54.grade(None, "#### 5"))

    def test_trailing_period(self):
        self.assertTrue(h54.grade("So she makes 18.", "#### 18"))


class Data(unittest.TestCase):
    def test_gsm8k_file(self):
        rows = [json.loads(x) for x in open(os.path.join(HERE, "gsm8k-test-0-199.jsonl"))]
        self.assertEqual([r["idx"] for r in rows], list(range(200)))
        self.assertTrue(all("####" in r["answer"] for r in rows))
        self.assertEqual(h54.gold(rows[0]["answer"]), "18")   # Janet's ducks

    def test_pinned_files(self):
        def sha(n):
            return hashlib.sha256(open(os.path.join(HERE, n), "rb").read()).hexdigest()
        self.assertEqual(sha("gsm8k-test-0-199.jsonl"), "45e54be2df7426cb335956bbe7840ada1f22c2b7d16a97edbd97741eeaf564e0")
        self.assertEqual(sha("ttft-prompt.txt"), "b10cd3c79003d22e6170f53dd39879139ec473908b32dff6bc7361626e98f5a6")


class Void(unittest.TestCase):
    def s(self, ts, cpu, ai=0):
        return {"ts": ts, "seconds": 10.0, "serving_cpu": cpu, "aiperf_procs": ai}

    def test_rules(self):
        quiet = [self.s(t, 2.3) for t in range(10, 200, 10)]
        self.assertFalse(h54.void(quiet, 50, 120)[0])
        busy = [self.s(t, 5.0 if 60 <= t <= 120 else 2.0) for t in range(10, 200, 10)]
        self.assertTrue(h54.void(busy, 50, 120)[0])
        ai = [self.s(t, 1.0, 1 if t == 90 else 0) for t in range(10, 200, 10)]
        self.assertTrue(h54.void(ai, 50, 120)[0])
        self.assertFalse(h54.void(ai, 130, 190)[0])      # the aiperf sample is outside this phase
        self.assertEqual(h54.void([], 0, 10), (False, "no serving sample"))

    def test_sample_covering_phase_end(self):
        # A sample at ts=125 covers (115, 125]; a phase ending at 120 includes it.
        self.assertTrue(h54.void([self.s(125, 9.0)], 100, 120)[0])
        self.assertFalse(h54.void([self.s(135, 9.0)], 100, 120)[0])


def write_gguf(path, tensors, align=32):
    """A minimal GGUF v3 file: one string KV, then tensor infos; offsets laid out back to back."""
    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    hdr = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", len(tensors), 2)
    hdr += s("general.architecture") + struct.pack("<I", 8) + s("gemma4")
    hdr += s("general.alignment") + struct.pack("<I", 4) + struct.pack("<I", align)
    off = 0
    for name, n in tensors:
        hdr += s(name) + struct.pack("<I", 1) + struct.pack("<Q", n) + struct.pack("<IQ", 0, off)
        off += n
    pad = (-len(hdr)) % align
    with open(path, "wb") as f:
        f.write(hdr + b"\0" * pad + b"\1" * off)


class Bytes(unittest.TestCase):
    def test_gguf_experts_and_rest(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.gguf")
            write_gguf(p, [("token_embd.weight", 1000), ("blk.0.attn_q.weight", 64),
                           ("blk.0.ffn_gate_exps.weight", 12800), ("blk.0.ffn_down_exps.weight", 12800),
                           ("blk.0.ffn_gate_up_exps.weight", 128)])
            r = h54.active_bytes(p)
        self.assertEqual(r["expert_bytes"], 25728)
        self.assertEqual(r["rest_bytes"], 1064)
        self.assertAlmostEqual(r["active_bytes"], 1064 + 25728 * 8 / 128)

    def test_safetensors_skips_towers(self):
        with tempfile.TemporaryDirectory() as d:
            hdr = {"model.language_model.layers.0.experts.gate_up_proj": {"dtype": "BF16", "shape": [1], "data_offsets": [0, 1280]},
                   "model.language_model.layers.0.self_attn.q_proj.weight": {"dtype": "BF16", "shape": [1], "data_offsets": [1280, 1408]},
                   "model.vision_tower.x": {"dtype": "BF16", "shape": [1], "data_offsets": [1408, 9408]},
                   "__metadata__": {"format": "pt"}}
            b = json.dumps(hdr).encode()
            with open(os.path.join(d, "model-00001-of-00001.safetensors"), "wb") as f:
                f.write(struct.pack("<Q", len(b)) + b + b"\0" * 9408)
            r = h54.active_bytes(d)
        self.assertEqual((r["expert_bytes"], r["rest_bytes"]), (1280, 128))


class Choose(unittest.TestCase):
    def a(self, rank, decode, correct):
        return {"rank": rank, "decode": decode, "correct": correct, "n": 50}

    def test_official_first(self):
        self.assertEqual(h54.choose({"Lg": self.a(0, 40, 45), "Vq": self.a(0, 12, 46)}), "Lg")

    def test_lower_rank_needs_5pct_and_quality(self):
        self.assertEqual(h54.choose({"Lg": self.a(0, 40, 45), "Lg-mtp": self.a(2, 41.9, 45)}), "Lg")
        self.assertEqual(h54.choose({"Lg": self.a(0, 40, 45), "Lg-mtp": self.a(2, 42.0, 43)}), "Lg-mtp")
        self.assertEqual(h54.choose({"Lg": self.a(0, 40, 45), "Lg-mtp": self.a(2, 60, 42)}), "Lg")

    def test_only_lower_rank(self):
        self.assertEqual(h54.choose({"A1g": self.a(2, 30, 44)}), "A1g")
        self.assertIsNone(h54.choose({}))


class Bootstrap(unittest.TestCase):
    def test_deterministic_and_brackets(self):
        a, b = [30.0, 31, 32, 29, 30.5, 31.5, 30.2, 29.8], [10.0, 10.2, 9.9, 10.1, 10, 9.8, 10.3, 10.05]
        lo, hi = h54.boot_ratio(a, b)
        self.assertEqual((lo, hi), h54.boot_ratio(a, b))
        self.assertLess(lo, 30.25 / 10.025)
        self.assertGreater(hi, 30.25 / 10.025)
        self.assertGreater(lo, 2.8)


def arm_dir(root, hold, arm, status="measured", decode=None, correct=None, void=False):
    d = os.path.join(root, f"hold{hold}", arm)
    os.makedirs(d)
    json.dump({"status": status}, open(os.path.join(d, "status.json"), "w"))
    if decode is not None:
        samples = [{"ok": True, "decode_tokens_per_s": decode * f} for f in (0.98, 1.0, 1.02, 0.99, 1.01, 1.0, 0.97, 1.03)]
        n1 = {"void": void, "rerun_void": void, "result": {"phases": [{"samples": samples}]}}
        json.dump({"n1": n1}, open(os.path.join(d, "phases.json"), "w"))
    if correct is not None:
        json.dump({"correct": correct, "n": 50, "score": correct / 50}, open(os.path.join(d, "quality.json"), "w"))


class ScoreA(unittest.TestCase):
    def run_score(self, arms):
        with tempfile.TemporaryDirectory() as d:
            for a in arms:
                arm_dir(d, *a)
            return h54.score_a(d)

    def test_supported(self):
        r = self.run_score([(1, "B1g", "measured", 8, 45), (1, "Lg", "measured", 40, 44), (1, "Lg-mtp", "measured", 41, 44)])
        self.assertEqual((r["verdict"], r["chosen"]), ("SUPPORTED", "Lg"))

    def test_refuted_ci_below(self):
        r = self.run_score([(1, "B1g", "measured", 10, 45), (1, "Lg", "measured", 20, 45)])
        self.assertEqual(r["verdict"], "REFUTED")

    def test_refuted_fast_arm_fails_gate(self):
        r = self.run_score([(1, "B1g", "measured", 8, 45), (1, "Lg", "measured", 40, 30), (1, "Vq", "measured", 12, 45)])
        self.assertEqual(r["chosen"], "Vq")
        self.assertEqual(r["verdict"], "REFUTED")

    def test_inconclusive_without_b1g(self):
        r = self.run_score([(1, "B1g", "not_deliverable"), (1, "Lg", "measured", 40, 45)])
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_void_not_rerun_is_inconclusive(self):
        r = self.run_score([(1, "B1g", "measured", 8, 45), (1, "Lg", "measured", 40, 44, True)])
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_stock_arms_never_chosen(self):
        r = self.run_score([(1, "B1g", "measured", 8, 45), (1, "B3g", "measured", 80, 45), (1, "A1o", "measured", 30, 44)])
        self.assertEqual(r["chosen"], "A1o")

    def test_measured_hold_wins_over_not_reached(self):
        r = self.run_score([(1, "B1g", "measured", 8, 45), (1, "Vq", "not_reached"), (2, "Vq", "measured", 30, 45)])
        self.assertEqual(r["chosen"], "Vq")


@unittest.skipUnless(HAVE_YAML, "PyYAML")
class Renders(unittest.TestCase):
    def test_llamacpp(self):
        dep = h54.render("Lg")[1]
        c = dep["spec"]["template"]["spec"]["containers"][0]
        self.assertTrue(c["image"].endswith("@sha256:9fbaa06d7f4449e2ae0d02b40e0431f7c212dcf231c9e3a7777a6afc2cc8f312"))
        for f in ("-t", "48", "-Cr", "0-47", "--cpu-strict", "1", "--jinja"):
            self.assertIn(f, c["args"])
        self.assertNotIn("--spec-type", c["args"])
        m = h54.render("Lg-mtp")[1]["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertEqual(m[m.index("--spec-type") + 1], "draft-mtp")   # b11749's name for MTP
        self.assertIn(f"/models/{h54.DRAFTER_GGUF}", m)

    def test_sglang(self):
        c = h54.render("Sg")[1]["spec"]["template"]["spec"]["containers"][0]
        cmd = c["command"]
        self.assertEqual(cmd[cmd.index("--attention-backend") + 1], "intel_amx")
        self.assertEqual(cmd[cmd.index("--device") + 1], "cpu")
        self.assertEqual(cmd[cmd.index("--tp") + 1], "1")
        self.assertEqual(c["env"], [{"name": "SGLANG_CPU_OMP_THREADS_BIND", "value": "0-47"}])

    def b1g(self):
        cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "vllm-gemma4-config"},
              "data": {"VLLM_CPU_KVCACHE_SPACE": "40", "HF_HUB_OFFLINE": "1"}}
        svc = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "vllm-gemma4-service"},
               "spec": {"ports": [{"port": 80, "targetPort": 2080}], "selector": {"x": "y"}}}
        dep = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "vllm-gemma4"},
               "spec": {"selector": {"matchLabels": {"x": "y"}}, "template": {"metadata": {"labels": {"x": "y"}}, "spec": {
                   "containers": [{"name": "vllm", "image": h54.IMG["vllm024"], "envFrom": [{"configMapRef": {"name": "vllm-gemma4-config"}}],
                                   "args": ["--block-size", "128", "--enforce-eager", "--model", f"/data/{h54.QAT}",
                                            "--served-model-name", h54.SERVED, "--port", "2080", "--max-model-len", "8192"]}]}}}}
        tpl = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "vllm-gemma4-chat-templates"}, "data": {"t.jinja": "x"}}
        dep["spec"]["template"]["spec"]["volumes"] = [{"name": "chat-templates", "configMap": {"name": "vllm-gemma4-chat-templates"}}]
        return [cm, tpl, svc, dep]

    def test_derive_vllm(self):
        docs = self.b1g()
        out = h54.derive_vllm("Vq", docs)
        cm, tpl, svc, dep = out[1], out[2], out[3], out[4]
        self.assertEqual(tpl["metadata"]["name"], "vq-chat-templates")
        self.assertEqual(dep["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"], "vq-chat-templates")
        self.assertEqual(dep["spec"]["template"]["spec"]["containers"][0]["envFrom"][0]["configMapRef"]["name"], "vq-config")
        self.assertNotIn("VLLM_CPU_OMP_THREADS_BIND", tpl["data"])
        self.assertEqual(cm["data"]["VLLM_CPU_OMP_THREADS_BIND"], "0-47")
        self.assertEqual(cm["data"]["VLLM_CPU_KVCACHE_SPACE"], "40")
        c = dep["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(c["image"], h54.IMG["vllm032"])
        self.assertEqual(c["args"][c["args"].index("--attention-backend") + 1], "CPU_ATTN")
        self.assertIn("--enforce-eager", c["args"])                       # B1g's flags kept
        self.assertEqual(svc["spec"]["selector"], {"app": "vq"})
        self.assertEqual(dep["metadata"]["namespace"], "h54")
        self.assertEqual(docs[3]["spec"]["template"]["spec"]["containers"][0]["image"], h54.IMG["vllm024"])  # input untouched
        mtp = h54.derive_vllm("Vq-mtp", docs)[4]["spec"]["template"]["spec"]["containers"][0]["args"]
        spec = json.loads(mtp[mtp.index("--speculative-config") + 1])
        self.assertEqual(spec, {"method": "gemma4_mtp", "model": f"/data/{h54.DRAFTER}"})   # vLLM's default length
        vi = h54.derive_vllm("Vi", docs)[4]["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertEqual(vi[vi.index("--model") + 1], f"/data/{h54.AUTOROUND}")


class Tables(unittest.TestCase):
    def test_arm_table(self):
        self.assertEqual(set(h54.ARMS), {"B1g", "B3g", "A1o", "A1g", "Lg", "Lg-mtp", "Vq", "Vq-mtp", "Vi", "Sg"})
        self.assertEqual(set(h54.SERVED_NAME), set(h54.ARMS))
        self.assertEqual({k for k, a in h54.ARMS.items() if a["boot"] == 1500}, {"B1g", "Vq", "Vq-mtp"})
        self.assertEqual({k for k, a in h54.ARMS.items() if a["rank"] is None}, {"B1g", "B3g"})
        self.assertEqual({k for k, a in h54.ARMS.items() if a["rank"] == 2}, {"A1g", "Lg-mtp"})
        for ref in h54.IMG.values():
            self.assertRegex(ref, r"@sha256:[0-9a-f]{64}$")


class Shim(unittest.TestCase):
    def test_adds_volume_only_for_campaign_create(self):
        with tempfile.TemporaryDirectory() as d:
            real = os.path.join(d, "k3d.real")
            with open(real, "w") as f:
                f.write("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
            os.chmod(real, 0o755)
            shim = os.path.join(d, "k3d")
            with open(os.path.join(HERE, "k3d-shim.sh")) as s, open(shim, "w") as f:
                f.write(s.read())
            os.chmod(shim, 0o755)
            env = dict(os.environ, HOME="/home/u", K3D_REAL=real)
            out = subprocess.run([shim, "cluster", "create", "k3dgpu-g-h54a-1", "--x"], capture_output=True, text=True, env=env).stdout
            self.assertIn("--volume=/home/u/k3dgpu-gemma/models:/var/apb-models@server:*;agent:*", out)
            out = subprocess.run([shim, "cluster", "create", "k3dgpu-e-h50"], capture_output=True, text=True, env=env).stdout
            self.assertNotIn("--volume", out)
            out = subprocess.run([shim, "cluster", "list"], capture_output=True, text=True, env=env).stdout
            self.assertEqual(out.split(), ["cluster", "list"])


if __name__ == "__main__":
    unittest.main()
