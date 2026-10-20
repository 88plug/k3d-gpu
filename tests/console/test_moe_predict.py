#!/usr/bin/env python3
"""Unit tests for console/moe_predict.py (`k3d-gpu plan --predict --model`).

Fixtures (tests/console/moe/, provenance in tests/console/moe/SOURCES):
  - gemma-4-26B-A4B-it-qat-q4_0-unquantized@f1e06dc5.config.json: the real MoE
    config. The expected parameter counts below are the sums of the tensor shapes
    in that revision's two safetensors headers (read with HTTP range requests).
  - Qwen3.8-27B@1d4bf0f2.config.json: the dense control, counted the same way.
  - xeon6741p-4xb70/: a sysroot captured on the Xeon 6741P host with four Arc Pro
    B70 (cpuinfo, meminfo, the GPUs' PCI vendor/device/class/resource, SMBIOS
    type 17 records).

Checked:
  - total and active parameters from config.json match the header counts (0.5%)
  - bytes per parameter per format, including GGUF block types
  - a synthetic GGUF's tensor table splits into expert, dense and embedding bytes
  - VRAM from the BAR, bandwidth spec per device id, RAM and cores from procfs
  - bandwidth precedence: measured cache > --dram-gbs > SMBIOS theoretical > none,
    and a cache from another host is ignored
  - ceilings equal bandwidth / bytes read per token; hybrid adds the hop term
  - placement: what fits where, the engine per container, the pick
  - bad input exits 2 with a message
Stdlib only.
"""

import io
import json
import os
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "console"))
import moe_predict as mp  # noqa: E402

FIX = os.path.join(HERE, "moe")
GEMMA = os.path.join(FIX, "gemma-4-26B-A4B-it-qat-q4_0-unquantized@f1e06dc5.config.json")
QWEN = os.path.join(FIX, "Qwen3.8-27B@1d4bf0f2.config.json")
HOST = os.path.join(FIX, "xeon6741p-4xb70")
TMP = tempfile.mkdtemp()

# Sums of tensor shapes in the safetensors headers (SOURCES).
GEMMA_TEXT_TOTAL = 25_233_141_790      # embed 738197504 + experts 22837985280 + rest 1656959006
GEMMA_EXPERTS = 22_837_985_280
GEMMA_ACTIVE = 1_656_959_006 + 738_197_504 + GEMMA_EXPERTS * 8 // 128   # tied head read every token
QWEN_TEXT_TOTAL = 26_895_998_464       # embed + lm_head + rest, MTP head excluded
QWEN_ACTIVE = 24_353_201_664 + 1_271_398_400                             # rest + untied lm_head


def load(path):
    with open(path) as f:
        return json.load(f)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = mp.main(argv)
    return rc, out.getvalue(), err.getvalue()


def close(a, b, rel):
    return abs(a - b) <= rel * abs(b)


# ---- a minimal GGUF v3 writer (header and tensor table only; no tensor data) ----
def _s(x):
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def gguf_bytes(kv, tensors):
    out = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(kv))
    for k, v in kv.items():
        out += _s(k)
        if isinstance(v, str):
            out += struct.pack("<I", 8) + _s(v)
        elif isinstance(v, list):
            out += struct.pack("<IIQ", 9, 8, len(v)) + b"".join(_s(x) for x in v)
        elif isinstance(v, float):
            out += struct.pack("<If", 6, v)
        else:
            out += struct.pack("<II", 4, v)
    off = 0
    for name, dims, typ in tensors:
        out += _s(name) + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
        out += struct.pack("<IQ", typ, off)
        off += 1
    return out


def write_gguf(name, kv, tensors):
    path = os.path.join(TMP, name)
    with open(path, "wb") as f:
        f.write(gguf_bytes(kv, tensors))
    return path


# Q4_0 = 2 (32 per 18-byte block), Q8_0 = 8 (32 per 34), F32 = 0, Q6_K = 14 (256 per 210).
TOY_KV = {"general.architecture": "gemma4", "gemma4.block_count": 2, "gemma4.expert_count": 4,
          "gemma4.expert_used_count": 1, "gemma4.embedding_length": 64,
          "tokenizer.ggml.tokens": ["a", "b"], "gemma4.rope.freq_base": 1e6}
TOY_TENSORS = [
    ("token_embd.weight", [64, 256], 14),          # 16384 el / 256 * 210 = 13440 B
    ("blk.0.attn_q.weight", [64, 64], 2),           # 4096 / 32 * 18 = 2304
    ("blk.0.ffn_gate_up_exps.weight", [64, 128, 4], 2),   # 32768 / 32 * 18 = 18432
    ("blk.0.ffn_down_exps.weight", [64, 64, 4], 2),       # 16384 / 32 * 18 = 9216
    ("blk.0.attn_norm.weight", [64], 0),            # 256
    ("blk.1.attn_q.weight", [64, 64], 8),           # 4096 / 32 * 34 = 4352
    ("blk.1.ffn_gate_up_exps.weight", [64, 128, 4], 2),   # 18432
    ("blk.1.ffn_down_exps.weight", [64, 64, 4], 2),       # 9216
]
TOY_EXPERTS = 18432 * 2 + 9216 * 2
TOY_DENSE = 2304 + 256 + 4352
TOY_EMBED = 13440


class Params(unittest.TestCase):
    def test_gemma_moe_counts(self):
        p = mp.params_from_config(load(GEMMA))
        self.assertTrue(p["moe"])
        self.assertEqual((p["num_experts"], p["top_k"], p["layers"]), (128, 8, 30))
        self.assertEqual(p["experts_total"], GEMMA_EXPERTS)          # exact: shapes are in config
        self.assertTrue(close(p["total"], GEMMA_TEXT_TOTAL, 0.005), p)
        self.assertTrue(close(p["active"], GEMMA_ACTIVE, 0.005), p)
        self.assertTrue(p["tied"])

    def test_qwen_dense_control(self):
        p = mp.params_from_config(load(QWEN))
        self.assertFalse(p["moe"])
        self.assertEqual((p["num_experts"], p["experts_total"], p["experts_active"]), (0, 0, 0))
        self.assertTrue(close(p["total"], QWEN_TEXT_TOTAL, 0.005), p)
        self.assertTrue(close(p["active"], QWEN_ACTIVE, 0.005), p)
        self.assertFalse(p["tied"])

    def test_standard_moe_keys(self):
        # Mixtral/Qwen3-MoE style: num_local_experts / num_experts_per_tok, no dense MLP
        # in MoE layers, untied head.
        cfg = {"architectures": ["X"], "hidden_size": 64, "num_hidden_layers": 2, "vocab_size": 100,
               "num_attention_heads": 4, "num_key_value_heads": 2, "intermediate_size": 256,
               "num_local_experts": 8, "num_experts_per_tok": 2, "moe_intermediate_size": 32}
        p = mp.params_from_config(cfg)
        self.assertEqual(p["experts_total"], 2 * 8 * 3 * 64 * 32)
        self.assertEqual(p["experts_active"], 2 * 2 * 3 * 64 * 32)
        attn = 64 * 64 + 2 * 64 * 32 + 64 * 64           # q, k, v (2 kv heads x 16), o
        self.assertEqual(p["dense"], 2 * (attn + 64 * 8))  # plus the router, no dense MLP
        self.assertEqual(p["active"], p["dense"] + p["experts_active"] + 100 * 64)

    def test_missing_fields(self):
        with self.assertRaises(ValueError):
            mp.params_from_config({"architectures": ["X"], "num_hidden_layers": 2})


class Formats(unittest.TestCase):
    def test_bytes_per_param(self):
        cases = {"bf16": 2.0, "fp16": 2.0, "fp8": 1.0, "q4_0": 18 / 32, "q8_0": 34 / 32,
                 "ov-int4-sym-g32": 0.5 + 2 / 32, "ov-int4-asym-g128": 0.5 + 2.5 / 128,
                 "ov-int8": 1.0, "w4a16-g128": 0.5 + 2.5 / 128}
        for fmt, want in cases.items():
            self.assertAlmostEqual(mp.format_info(fmt)["bpp"], want, places=6, msg=fmt)
        self.assertEqual(mp.format_info("q4_0")["container"], "gguf")
        self.assertEqual(mp.format_info("ov-int4-sym-g32")["container"], "ov")
        self.assertEqual(mp.format_info("bf16")["container"], "safetensors")
        self.assertIn("approximate", mp.format_info("q4_k_m")["source"])
        with self.assertRaises(ValueError):
            mp.format_info("int3-weird")

    def test_format_from_dir(self):
        d = os.path.join(TMP, "ovdir")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "openvino_config.json"), "w") as f:
            json.dump({"quantization_config": {"quantization_configs": {"lm_model": {
                "bits": 4, "group_size": 64, "sym": False}}}}, f)
        self.assertEqual(mp.format_from_dir(d, {}), "ov-int4-asym-g64")
        d2 = os.path.join(TMP, "ardir")
        os.makedirs(d2, exist_ok=True)
        cfg = {"quantization_config": {"quant_method": "auto-round", "bits": 4, "group_size": 128, "sym": True}}
        self.assertEqual(mp.format_from_dir(d2, cfg), "w4a16-g128")
        self.assertEqual(mp.format_from_dir(d2, {"text_config": {"dtype": "bfloat16"}}), "bf16")


class Gguf(unittest.TestCase):
    def test_split(self):
        g = mp.gguf_read(write_gguf("toy.gguf", TOY_KV, TOY_TENSORS))
        s = mp.gguf_weights(g)
        self.assertEqual((s["num_experts"], s["top_k"], s["layers"]), (4, 1, 2))
        self.assertEqual(s["experts_total_bytes"], TOY_EXPERTS)
        self.assertEqual(s["experts_active_bytes"], TOY_EXPERTS // 4)
        # No output.weight: the head is tied, so the whole embedding is read per token.
        self.assertEqual(s["dense_bytes"], TOY_DENSE + TOY_EMBED)
        self.assertEqual(s["total_bytes"], TOY_EXPERTS + TOY_DENSE + TOY_EMBED)
        self.assertEqual(s["format"], "q4_0")      # the most bytes are Q4_0

    def test_untied_head(self):
        t = TOY_TENSORS + [("output.weight", [64, 256], 8)]      # 16384 / 32 * 34 = 17408
        s = mp.gguf_weights(mp.gguf_read(write_gguf("toy2.gguf", TOY_KV, t)))
        self.assertEqual(s["dense_bytes"], TOY_DENSE + 17408)   # embedding is a row gather

    def test_bad_magic(self):
        path = os.path.join(TMP, "bad.gguf")
        with open(path, "wb") as f:
            f.write(b"NOPE" + b"\0" * 32)
        with self.assertRaises(ValueError):
            mp.gguf_read(path)


class Host(unittest.TestCase):
    def test_sysroot(self):
        h = mp.host_from_sysroot(HOST)
        self.assertEqual(h["cpu_model"], "Intel(R) Xeon(R) 6741P")
        self.assertTrue(h["amx"])
        self.assertEqual(h["physical_cores"], 48)
        self.assertEqual(h["mem_kib"], 527767144)
        self.assertEqual(len(h["gpus"]), 4)
        g = h["gpus"][0]
        self.assertEqual((g["device"], g["vram_bytes"], g["vram_source"]), ("0xe223", 32 << 30, "bar"))
        self.assertEqual((g["bw_gbs"], g["bw_source"].split()[0]), (608.0, "spec"))

    def test_dmi_theoretical(self):
        d = mp.dmi_theoretical(HOST)
        self.assertEqual((d["modules"], d["mts"], d["data_bytes"]), (8, 6400, 64))
        self.assertAlmostEqual(d["gbs"], 409.6)

    def test_bandwidth_precedence(self):
        h = mp.host_from_sysroot(HOST)
        dmi = mp.dmi_theoretical(HOST)
        cache = {"schema": "k3d-gpu.bandwidth/v1", "triad_gbs": 305.2, "threads": 48,
                 "cpu_model": h["cpu_model"], "mem_kib": h["mem_kib"], "measured_at": "2026-10-20T22:22:12Z"}
        self.assertEqual(mp.dram_bandwidth(h, cache, None, dmi)["source"], "measured")
        self.assertEqual(mp.dram_bandwidth(h, cache, None, dmi)["gbs"], 305.2)
        self.assertEqual(mp.dram_bandwidth(h, cache, 250.0, dmi)["source"], "given")
        other = dict(cache, cpu_model="Intel(R) Core(TM) i9-13900T")
        b = mp.dram_bandwidth(h, other, None, dmi)
        self.assertEqual((b["source"], b["gbs"]), ("theoretical", 409.6))
        self.assertIn("another host", b["note"])
        self.assertEqual(mp.dram_bandwidth(h, None, None, None)["source"], "none")


class Predict(unittest.TestCase):
    def setUp(self):
        self.host = mp.host_from_sysroot(HOST)
        self.bw = mp.dram_bandwidth(self.host, None, None, mp.dmi_theoretical(HOST))

    def rows(self, doc):
        return {r["placement"]: r for r in doc["rows"]}

    def test_gguf_ceilings(self):
        w = mp.gguf_weights(mp.gguf_read(write_gguf("toy3.gguf", TOY_KV, TOY_TENSORS)))
        doc = mp.predict(w, self.host, self.bw, {})
        r = self.rows(doc)
        read = TOY_DENSE + TOY_EMBED + TOY_EXPERTS // 4
        self.assertAlmostEqual(r["cpu"]["decode_tok_s"], round(409.6e9 / read, 1))
        self.assertAlmostEqual(r["gpu"]["decode_tok_s"], round(608e9 / read, 1))
        t = (TOY_EXPERTS // 4) / 409.6e9 + (TOY_DENSE + TOY_EMBED) / 608e9 + 2 * 2 * 30e-6
        self.assertAlmostEqual(r["hybrid"]["decode_tok_s"], round(1 / t, 1))
        self.assertEqual({x["engine"] for x in doc["rows"]}, {"llama.cpp"})
        self.assertEqual(doc["bandwidth"]["dram"]["source"], "theoretical")

    def test_gemma_bf16_cpu_vs_gpu(self):
        w = mp.config_weights(mp.params_from_config(load(GEMMA)), "bf16")
        doc = mp.predict(w, self.host, self.bw, {})
        r = self.rows(doc)
        self.assertAlmostEqual(r["cpu"]["decode_tok_s"], round(409.6e9 / (w["read_bytes"]), 1))
        self.assertEqual(r["cpu"]["engine"], "vllm-cpu")
        # 50 GB of bf16 does not fit one 32 GiB B70; two cards with TP do.
        self.assertNotIn("gpu", r)
        self.assertEqual(r["gpu-tp2"]["engine"], "llm-scaler-vllm")
        t = w["read_bytes"] / (2 * 608e9) + 2 * 30 * 30e-6
        self.assertAlmostEqual(r["gpu-tp2"]["decode_tok_s"], round(1 / t, 1))
        one = mp.predict(w, self.host, self.bw, {"gpus": 1})
        self.assertIn("gpu", {s["placement"] for s in one["skipped"]})
        four = self.rows(mp.predict(w, self.host, self.bw, {"gpus": 4}))
        self.assertIn("gpu-tp4", four)
        self.assertNotIn("hybrid", r)          # expert offload is a GGUF (llama.cpp) path only
        self.assertEqual(doc["pick"]["placement"], max(doc["rows"], key=lambda x: x["decode_tok_s"])["placement"])

    def test_dense_has_no_hybrid(self):
        w = mp.config_weights(mp.params_from_config(load(QWEN)), "q4_k_m")
        doc = mp.predict(w, self.host, self.bw, {})
        self.assertNotIn("hybrid", self.rows(doc))
        self.assertIn("gpu", self.rows(doc))

    def test_filters(self):
        w = mp.config_weights(mp.params_from_config(load(GEMMA)), "ov-int4-sym-g32")
        doc = mp.predict(w, self.host, self.bw, {"placement": "cpu"})
        self.assertEqual([r["placement"] for r in doc["rows"]], ["cpu"])
        self.assertEqual(doc["rows"][0]["engine"], "ovms")
        doc = mp.predict(w, self.host, self.bw, {"engine": "llama.cpp"})
        self.assertEqual(doc["rows"], [])
        self.assertIsNone(doc["pick"])

    def test_no_bandwidth(self):
        w = mp.config_weights(mp.params_from_config(load(GEMMA)), "bf16")
        doc = mp.predict(w, self.host, mp.dram_bandwidth(self.host, None, None, None), {"placement": "cpu"})
        self.assertIsNone(doc["rows"][0]["decode_tok_s"])

    def test_source_rank(self):
        self.assertEqual(mp.source_rank("google/gemma-4-26B-A4B-it-qat-q4_0-gguf")[0], 1)
        self.assertEqual(mp.source_rank("Intel/gemma-4-26B-A4B-it-int4-AutoRound")[0], 1)
        self.assertEqual(mp.source_rank("OpenVINO/Qwen3.8-27B-int4-ov")[0], 1)
        self.assertEqual(mp.source_rank("RedHatAI/Qwen3.8-27B-INT4")[0], 2)
        self.assertEqual(mp.source_rank("88plug/Qwen3.6-27B-W4A16")[0], 3)
        self.assertEqual(mp.source_rank("unsloth/Qwen3.8-Flash-Next-GGUF")[0], 4)
        self.assertIsNone(mp.source_rank("/home/x/model")[0])


class Cli(unittest.TestCase):
    def test_json_and_text(self):
        rc, out, err = run(["--config", GEMMA, "--format", "q4_0", "--sysroot", HOST, "--json",
                            "--model-id", "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized"])
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertEqual(doc["schema"], "k3d-gpu.moe-predict/v1")
        self.assertEqual(doc["status"], "prediction")
        self.assertEqual(doc["model"]["source_rank"], 1)
        self.assertEqual(doc["bandwidth"]["gpu"][0]["source"].split()[0], "spec")
        rc, out, err = run(["--config", GEMMA, "--format", "q4_0", "--sysroot", HOST])
        self.assertEqual(rc, 0, err)
        self.assertIn("theoretical", out)
        self.assertIn("ceiling", out)

    def test_bad_input(self):
        self.assertEqual(run(["--config", os.path.join(TMP, "none.json"), "--sysroot", HOST])[0], 2)
        self.assertEqual(run(["--config", GEMMA, "--format", "int3-weird", "--sysroot", HOST])[0], 2)
        self.assertEqual(run(["--config", GEMMA, "--sysroot", HOST, "--dram-gbs", "-1"])[0], 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
