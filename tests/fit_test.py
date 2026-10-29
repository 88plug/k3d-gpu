#!/usr/bin/env python3
"""Tests for console/fit.py: the host probe, weight headers, the fit arithmetic on
fixture hosts and models, and the memory guard under simulated PSI pressure.

Fixtures (tests/fixtures/fit/SOURCES says where each came from):
  hosts/   desktop i9 with no GPU; Xeon 6741P, 512 GB, 4x Arc Pro B70 32 GB with two
           cards busy; a 16 GB laptop
  models/  per-repo config.json, tokenizer_config.json and weights.json (per tensor
           class bytes from the real safetensors/GGUF headers at a pinned revision),
           plus one synthetic model too large for any fixture host

Usage: python3 tests/fit_test.py
"""

import gzip
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "console"))

import fit  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "fit")
GIB = fit.GIB


def host(name):
    with open(os.path.join(FIX, "hosts", name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def mdir(name):
    return os.path.join(FIX, "models", name)


def plan(hname, model, variants=(), **kw):
    return fit.plan_with_variants(host(hname), mdir(model), [mdir(v) for v in variants], **kw)


def write(root, rel, text, mode="w"):
    p = os.path.join(root, rel.lstrip("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, mode) as f:
        f.write(text)
    return p


class Probe(unittest.TestCase):
    """probe() on a fake sysroot: every reader, with the VRAM query injected."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.root = self.tmp.name
        write(r, "/proc/meminfo", "MemTotal:       65536000 kB\nMemAvailable:   40000000 kB\n"
                                  "SwapTotal:       8388608 kB\nSwapFree:        4194304 kB\n")
        write(r, "/proc/cpuinfo", "processor\t: 0\nmodel name\t: Intel(R) Xeon(R) 6741P\n"
                                  "flags\t\t: fpu avx2 avx512f amx_bf16 amx_tile amx_int8\n\nprocessor\t: 1\n")
        write(r, "/proc/pressure/memory", "some avg10=12.50 avg60=3.00 avg300=1.00 total=123\n"
                                          "full avg10=0.50 avg60=0.10 avg300=0.00 total=45\n")
        write(r, "/proc/self/cgroup", "0::/user.slice/app.scope\n")
        write(r, "/sys/fs/cgroup/user.slice/memory.max", "17179869184\n")
        write(r, "/sys/fs/cgroup/user.slice/memory.current", "4294967296\n")
        write(r, "/sys/fs/cgroup/user.slice/app.scope/memory.max", "max\n")
        write(r, "/sys/devices/system/node/node0/meminfo", "Node 0 MemTotal:       32768000 kB\nNode 0 MemFree:        16384000 kB\n")
        write(r, "/sys/devices/system/node/node0/cpulist", "0-1\n")
        write(r, "/sys/devices/system/node/node1/meminfo", "Node 1 MemTotal:       32768000 kB\nNode 1 MemFree:        8192000 kB\n")
        write(r, "/sys/kernel/mm/transparent_hugepage/enabled", "always [madvise] never\n")
        write(r, "/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages", "16\n")
        write(r, "/sys/kernel/mm/hugepages/hugepages-2048kB/free_hugepages", "4\n")
        # One xe card (PCI 0000:03:00.0) and one integrated i915 GPU the query reports no VRAM for.
        dev = "/sys/devices/pci0000:00/0000:03:00.0"
        write(r, dev + "/vendor", "0x8086\n")
        os.makedirs(os.path.join(r, "sys/bus/pci/drivers/xe"))
        os.symlink(os.path.join(r, "sys/bus/pci/drivers/xe"), os.path.join(r, dev.lstrip("/"), "driver"))
        os.makedirs(os.path.join(r, dev.lstrip("/"), "drm/renderD129"))
        os.makedirs(os.path.join(r, "sys/class/drm"))
        os.makedirs(os.path.join(r, "sys/class/drm/card1"))
        os.symlink(os.path.join(r, dev.lstrip("/")), os.path.join(r, "sys/class/drm/card1/device"))
        igpu = "/sys/devices/pci0000:00/0000:00:02.0"
        write(r, igpu + "/vendor", "0x8086\n")
        os.makedirs(os.path.join(r, "sys/bus/pci/drivers/i915"))
        os.symlink(os.path.join(r, "sys/bus/pci/drivers/i915"), os.path.join(r, igpu.lstrip("/"), "driver"))
        os.makedirs(os.path.join(r, igpu.lstrip("/"), "drm/renderD128"))
        os.makedirs(os.path.join(r, "sys/class/drm/card0"))
        os.symlink(os.path.join(r, igpu.lstrip("/")), os.path.join(r, "sys/class/drm/card0/device"))
        os.makedirs(os.path.join(r, "sys/class/drm/card1-DP-1"))
        # DRM fdinfo: pid 100 holds the card through two fds of one client (counted once),
        # pid 200 through another client.
        info = "drm-driver:\txe\ndrm-pdev:\t0000:03:00.0\ndrm-client-id:\t%d\ndrm-total-vram0:\t%s\ndrm-resident-vram0:\t%s\n"
        write(r, "/proc/100/fdinfo/5", info % (7, "3145728 KiB", "2097152 KiB"))
        write(r, "/proc/100/fdinfo/6", info % (7, "3145728 KiB", "2097152 KiB"))
        write(r, "/proc/100/comm", "vllm\n")
        write(r, "/proc/200/fdinfo/3", info % (9, "1 GiB", "512 MiB"))
        write(r, "/proc/200/comm", "llama-server\n")
        write(r, "/proc/300/fdinfo/1", "pos:\t0\nflags:\t02\n")
        self.queries = []

    def tearDown(self):
        self.tmp.cleanup()

    def vram(self, node, driver):
        self.queries.append((node, driver))
        return (32 * GIB, 1 * GIB) if driver == "xe" else None

    def test_probe(self):
        h = fit.probe(self.root, vram_query=self.vram)
        self.assertEqual(h["schema"], "k3d-gpu.host/v1")
        self.assertEqual(h["ram"], {"total": 65536000 * 1024, "available": 40000000 * 1024,
                                    "swap_total": 8388608 * 1024, "swap_free": 4194304 * 1024})
        self.assertEqual(h["cgroup"], {"limit": 16 * GIB, "current": 4 * GIB, "path": "/user.slice"})
        self.assertEqual(h["cpu"]["model"], "Intel(R) Xeon(R) 6741P")
        self.assertEqual(h["cpu"]["cpus"], 2)
        self.assertTrue(h["cpu"]["amx"] and h["cpu"]["avx512"] and h["cpu"]["avx2"])
        self.assertEqual([n["node"] for n in h["numa"]], [0, 1])
        self.assertEqual(h["numa"][1]["free"], 8192000 * 1024)
        self.assertEqual(h["hugepages"], {"thp": "madvise", "pools": {str(2 * 1024 * 1024): {"total": 16, "free": 4}}})
        self.assertEqual(h["psi"]["some"]["avg10"], 12.5)
        self.assertEqual(h["psi"]["full"]["total"], 45)
        self.assertEqual(h["bandwidth"]["gbs"], None)
        self.assertIn("unknown", h["bandwidth"]["label"])
        # The integrated GPU has no device-local memory; the xe card does.
        self.assertEqual(sorted(self.queries), [("/dev/dri/renderD128", "i915"), ("/dev/dri/renderD129", "xe")])
        self.assertEqual(len(h["gpus"]), 1)
        g = h["gpus"][0]
        self.assertEqual((g["card"], g["bdf"], g["driver"], g["vram_total"]), ("card1", "0000:03:00.0", "xe", 32 * GIB))
        # In use: the larger of the driver's estimate (1 GiB) and the fdinfo sum (2 GiB + 512 MiB).
        self.assertEqual(g["vram_used"], 2 * GIB + 512 * fit.MIB)
        self.assertEqual([(u["comm"], u["bytes"]) for u in g["users"]], [("vllm", 2 * GIB), ("llama-server", 512 * fit.MIB)])

    def test_fdinfo_paths_on_the_real_root(self):
        # On "/" the fdinfo paths read proc/<pid>/fdinfo/<fd>, with no sysroot prefix.
        used = fit._fdinfo_vram(self.root + "/")
        self.assertEqual(sorted(p for p, _, _ in used["0000:03:00.0"].values()), [100, 200])

    def test_node_limit_caps_the_cgroup(self):
        h = fit.probe(self.root, vram_query=self.vram, node_limit=8 * GIB)
        self.assertEqual(h["cgroup"], {"limit": 8 * GIB, "current": 0, "path": "k3d node container"})
        h = fit.probe(self.root, vram_query=self.vram, node_limit=64 * GIB)
        self.assertEqual(h["cgroup"]["limit"], 16 * GIB)

    def test_no_vram_query_off_root(self):
        # A fake sysroot never reaches the real device nodes.
        self.assertEqual(fit.probe(self.root)["gpus"], [])

    def test_stream_cache_wins(self):
        s = write(self.root, "/stream.json", json.dumps({"triad_gbs": 301.5}))
        bw = fit.bandwidth(self.root, s)
        self.assertEqual((bw["gbs"], bw["label"]), (301.5, "measured (STREAM triad)"))

    def test_smbios_bandwidth(self):
        rec = bytearray(40)
        rec[0], rec[1] = 17, 40
        rec[10:12] = struct.pack("<H", 64)      # data width bits
        rec[12:14] = struct.pack("<H", 32768)   # size: 32 GB (MB units, top bit clear)
        rec[32:34] = struct.pack("<H", 6400)    # configured MT/s
        for i in range(8):
            write(self.root, "/sys/firmware/dmi/entries/17-%d/raw" % i, bytes(rec), "wb")
        empty = bytearray(rec)
        empty[12:14] = b"\0\0"                  # an empty slot
        write(self.root, "/sys/firmware/dmi/entries/17-9/raw", bytes(empty), "wb")
        bw = fit.bandwidth(self.root)
        self.assertEqual(bw["gbs"], 409.6)      # 8 x 6400 MT/s x 8 B
        self.assertTrue(bw["label"].startswith("theoretical"))


class Headers(unittest.TestCase):
    def test_safetensors(self):
        hdr = json.dumps({"__metadata__": {"format": "pt"},
                          "model.embed_tokens.weight": {"dtype": "BF16", "shape": [4, 2], "data_offsets": [0, 16]},
                          "model.layers.0.mlp.experts.0.down_proj.qweight": {"dtype": "I32", "shape": [2, 2], "data_offsets": [16, 32]},
                          "lm_head.weight": {"dtype": "BF16", "shape": [4, 2], "data_offsets": [32, 48]}}).encode()
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "model.safetensors"), "wb") as f:
                f.write(struct.pack("<Q", len(hdr)) + hdr + b"\0" * 48)
            with open(os.path.join(d, "config.json"), "w") as f:
                f.write("{}")
            w = fit.weights_local(d)
        self.assertEqual(w["format"], "safetensors")
        self.assertEqual({k: v["bytes"] for k, v in w["classes"].items()}, {"embed": 16, "experts": 16, "lm_head": 16})

    def test_gguf(self):
        def s(x):
            b = x.encode()
            return struct.pack("<Q", len(b)) + b
        kv = (s("general.architecture") + struct.pack("<I", 8) + s("gemma4")
              + s("tokenizer.ggml.tokens") + struct.pack("<IIQ", 9, 8, 2) + s("a") + s("bb")
              + s("general.alignment") + struct.pack("<II", 4, 64))
        tinfo = (s("token_embd.weight") + struct.pack("<I", 2) + struct.pack("<QQ", 8, 4) + struct.pack("<IQ", 0, 0)
                 + s("blk.0.ffn_gate_up_exps.weight") + struct.pack("<I", 1) + struct.pack("<Q", 100)
                 + struct.pack("<IQ", 1, 128))
        head = b"GGUF" + struct.pack("<IQQ", 3, 2, 3) + kv + tinfo
        pad = (-len(head)) % 64
        blob = head + b"\0" * pad + b"\x01" * 128 + b"\x02" * 200
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "m.gguf"), "wb") as f:
                f.write(blob)
            w = fit.weights_local(d)
        # Each tensor's bytes are the gap to the next offset; the last runs to end of file.
        self.assertEqual({k: v["bytes"] for k, v in w["classes"].items()}, {"embed": 128, "experts": 200})

    def test_corrupt_headers_are_clean_errors(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "model.safetensors"), "wb") as f:
                f.write(struct.pack("<Q", 7) + b"[1,2,3]")
            with open(os.path.join(d, "m.gguf"), "wb") as f:
                f.write(b"GGUF" + struct.pack("<IQQ", 3, 1, 1) + struct.pack("<Q", 1 << 40))
            r = subprocess.run([sys.executable, os.path.join(HERE, "..", "console", "fit.py"), "weights", d],
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertTrue(r.stderr.startswith("fit: "), r.stderr)
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "m.gguf"), "wb") as f:
                f.write(b"GGUF" + struct.pack("<IQQ", 3, 1, 1) + struct.pack("<Q", 1 << 40))
            with self.assertRaisesRegex(ValueError, "corrupt GGUF"):
                fit.weights_local(d)

    def test_card_base_model(self):
        self.assertEqual(fit.card_base_model("---\nlicense: x\nbase_model:\n- org/base-model\ntags: [a]\n---\n# Card"),
                         "org/base-model")
        self.assertEqual(fit.card_base_model("---\nbase_model: org/inline\n---\n"), "org/inline")
        self.assertIsNone(fit.card_base_model("# no front matter"))

    def test_tensor_classes(self):
        cases = {"model.language_model.layers.3.mlp.experts.gate_up_proj": "experts",
                 "blk.3.ffn_down_exps.weight": "experts",
                 "model.language_model.ple.ple_embedding.ngram_embedding.weight": "ngram",
                 "per_layer_token_embd.weight": "ngram",
                 "mtp.layers.0.mlp.gate.weight": "mtp", "openvino_mtp_model.bin": "mtp",
                 "openvino_text_embeddings_model.bin": "embed", "token_embd.weight": "embed",
                 "model.visual.blocks.0.attn.qkv.weight": "vision", "lm_head.weight": "lm_head",
                 "output.weight": "lm_head", "model.layers.0.self_attn.q_proj.weight": "other",
                 "model.layers.0.mlp.shared_expert.up_proj.weight": "other"}
        for name, cls in cases.items():
            self.assertEqual(fit.tensor_class(name), cls, name)


def config(model):
    with open(os.path.join(mdir(model), "config.json"), encoding="utf-8") as f:
        return json.load(f)


class KV(unittest.TestCase):
    """KV bytes from config.json, worked out by hand from each config's fields."""

    def test_gemma4_hybrid_swa(self):
        kv = fit.kv_profile(config("google__gemma-4-26B-A4B-it"), 2)
        # 5 full layers x 2 x 2 global kv heads x 512 global head dim x 2 B
        self.assertEqual(kv["full_per_token"], 5 * 2 * 2 * 512 * 2)
        # 25 sliding layers x 2 x 8 kv heads x 256 x 2 B, at most 1024 tokens
        self.assertEqual((kv["swa_per_token"], kv["window"]), (25 * 2 * 8 * 256 * 2, 1024))
        self.assertEqual(kv["state_per_seq"], 0)
        self.assertEqual(fit.per_seq(kv, 131072), 5 * 2 * 2 * 512 * 2 * 131072 + 25 * 2 * 8 * 256 * 2 * 1024)

    def test_qwen38_gated_deltanet(self):
        kv = fit.kv_profile(config("Qwen__Qwen3.8-27B"), 2)
        self.assertEqual(kv["full_per_token"], 16 * 2 * 4 * 256 * 2)
        # 48 linear layers x (48 x 128 x 128 x 4 B state + 3 x (2x16x128 + 48x128) x 2 B conv)
        self.assertEqual(kv["state_per_seq"], 48 * (48 * 128 * 128 * 4 + 3 * (2 * 16 * 128 + 48 * 128) * 2))
        self.assertEqual(kv["swa_per_token"], 0)

    def test_flash_next_sparse_attention(self):
        kv = fit.kv_profile(config("Qwen__Qwen3.8-Flash-Next"), 2)
        # 12 QSA layers x (2 x 2 kv heads x 256 x 2 B + indexer 1 head x 128 x 2 B)
        self.assertEqual(kv["full_per_token"], 12 * (2 * 2 * 256 * 2 + 128 * 2))
        self.assertEqual(kv["state_per_seq"], 36 * (48 * 128 * 128 * 4 + 3 * (2 * 16 * 128 + 48 * 128) * 2))

    def test_context_caps(self):
        cfg = config("OpenVINO__Qwen3.8-27B-int4-ov")
        with open(os.path.join(mdir("OpenVINO__Qwen3.8-27B-int4-ov"), "tokenizer_config.json")) as f:
            tok = json.load(f)
        self.assertEqual(fit.ctx_cap(cfg, tok), (131072, "tokenizer_config model_max_length"))
        self.assertEqual(fit.ctx_cap(config("Qwen__Qwen3.8-27B"), {"model_max_length": 1e30})[0], 262144)


class Engines(unittest.TestCase):
    def test_flash_next_has_no_intel_vllm(self):
        e = fit.engines_for("safetensors", "qwen4_exp")
        self.assertEqual([x[0] for x in e], [None, "llama.cpp"])
        self.assertIn("CUDA and ROCm only", e[0][1])
        self.assertIn("GGUF conversion", e[1][1])

    def test_engine_asked_keeps_the_architecture_check(self):
        d = plan("xeon6741p-4xb70", "Qwen__Qwen3.8-Flash-Next-FP8", engine="vllm")
        self.assertFalse(d["fits"])
        self.assertIn("CUDA and ROCm only", d["reason"])
        d = plan("xeon6741p-4xb70", "google__gemma-4-26B-A4B-it-qat-q4_0-gguf", engine="ovms")
        self.assertFalse(d["fits"])
        self.assertIn("ovms cannot serve these gguf weights", d["reason"])

    def test_native_engine(self):
        self.assertEqual([x[0] for x in fit.engines_for("safetensors", "qwen3_5")], ["vllm"])
        self.assertEqual([x[0] for x in fit.engines_for("gguf", "gemma4")], ["llama.cpp"])
        self.assertEqual([x[0] for x in fit.engines_for("openvino", "qwen3")], ["ovms"])


QWEN27_VARIANTS = ("Qwen__Qwen3.8-27B-FP8", "RedHatAI__Qwen3.8-27B-INT4", "OpenVINO__Qwen3.8-27B-int4-ov",
                   "OpenVINO__Qwen3.8-27B-int8-ov")


class FitXeon(unittest.TestCase):
    """Xeon 6741P, 512 GB, 4x Arc Pro B70 32 GB: card0 and card1 busy (29 and 30 GB held)."""

    def test_busy_cards_skipped(self):
        cards, lines = fit.gpu_budget(host("xeon6741p-4xb70"))
        self.assertEqual([c["card"] for c in cards], ["card3", "card2"])
        self.assertEqual(sum("BUSY" in s for s in lines), 2)

    def test_gemma_qat_gguf(self):
        d = plan("xeon6741p-4xb70", "google__gemma-4-26B-A4B-it-qat-q4_0-gguf")
        self.assertTrue(d["fits"], d["summary"])
        self.assertEqual((d["engine"], d["placement"], d["gpus"]), ("llama.cpp", "gpu", 1))
        self.assertEqual((d["ctx"], d["seqs"]), (131072, 6))
        self.assertFalse(d["degraded"])
        self.assertIn("-ngl", d["flags"])

    def test_vllm_host_side_per_rank(self):
        d = plan("xeon6741p-4xb70", "Qwen__Qwen3.8-27B")
        # one runtime per tensor-parallel rank, plus the /dev/shm emptyDir
        self.assertEqual(d["host_bytes"], fit.HOST_RUNTIME["vllm"] * 2 + fit.VLLM_SHM)

    def test_kv_dtype_flag_matches_the_count(self):
        d = plan("xeon6741p-4xb70", "RedHatAI__Qwen3.8-27B-INT4")
        self.assertIn("--kv-cache-dtype=fp8", d["flags"])
        self.assertNotIn("--kv-cache-dtype=fp8", plan("xeon6741p-4xb70", "Qwen__Qwen3.8-27B")["flags"])
        with self.assertRaisesRegex(ValueError, "not a KV cache type of vllm"):
            plan("xeon6741p-4xb70", "Qwen__Qwen3.8-27B", kv_dtype="q4_0")
        d = plan("xeon6741p-4xb70", "google__gemma-4-26B-A4B-it-qat-q4_0-gguf", kv_dtype="q8_0")
        self.assertEqual(d["flags"][-4:], ["-ctk", "q8_0", "-ctv", "q8_0"])

    def test_qwen27_bf16(self):
        d = plan("xeon6741p-4xb70", "Qwen__Qwen3.8-27B", QWEN27_VARIANTS)
        self.assertTrue(d["fits"])
        self.assertEqual((d["engine"], d["placement"], d["tp"]), ("vllm", "gpu-tp", 2))
        self.assertTrue(d["degraded"])  # BF16 on two free cards: under 4 x 32768
        self.assertEqual((d["ctx"], d["seqs"]), (16384, 5))
        self.assertEqual(d["smallest_fit"], "OpenVINO/Qwen3.8-27B-int4-ov@7b20ea8c7df7223a85ff27bab5c473f0cb038943")
        self.assertIn("--tensor-parallel-size=2", d["flags"])

    def test_flash_next_w4a16(self):
        d = plan("xeon6741p-4xb70", "Intel__Qwen3.8-Flash-Next-W4A16-AutoRound")
        self.assertTrue(d["fits"], d["summary"])
        # No Intel vLLM or OpenVINO path: llama.cpp, n-gram table and part of the experts in DRAM.
        self.assertEqual((d["engine"], d["placement"], d["gpus"]), ("llama.cpp", "moe-offload", 2))
        self.assertEqual((d["ctx"], d["seqs"]), (262144, 4))
        self.assertIn("--n-cpu-moe", d["flags"])
        self.assertGreater(d["host_bytes"], 102 * GIB)  # the bf16 n-gram table alone
        self.assertGreaterEqual(d["dram_tok_s"], fit.MIN_DRAM_TOK_S)
        self.assertIn("GGUF conversion", d["note"])

    def test_limits_follow_host_bytes(self):
        d = plan("xeon6741p-4xb70", "Intel__Qwen3.8-Flash-Next-W4A16-AutoRound")
        gi = -(-d["host_bytes"] * 11 // 10 // GIB)
        self.assertEqual(d["k8s"], {"memory_request": "%dGi" % gi, "memory_limit": "%dGi" % gi})


class FitDesktop(unittest.TestCase):
    """i9-13900K, 64 GB, no GPU, AVX2 only, 89.6 GB/s theoretical."""

    def test_ov_int4_cpu(self):
        d = plan("desktop-i9-nogpu", "OpenVINO__Qwen3.8-27B-int4-ov")
        self.assertTrue(d["fits"])
        self.assertEqual((d["engine"], d["placement"]), ("ovms", "cpu"))
        self.assertLessEqual(d["ctx"], 131072)  # the IR's tokenizer cap
        self.assertEqual((d["ctx"], d["seqs"]), (65536, 5))
        self.assertLessEqual(d["host_bytes"] * 1.1, host("desktop-i9-nogpu")["ram"]["available"] - 6.4 * GIB)

    def test_no_moe_offload_without_amx_or_avx512(self):
        d = plan("desktop-i9-nogpu", "Intel__Qwen3.8-Flash-Next-W4A16-AutoRound")
        self.assertFalse(d["fits"])
        self.assertIn("exceed the host budget", d["reason"])


class FitLaptop(unittest.TestCase):
    """Core Ultra 7 258V, 16 GB, 9 GB available, swap in use."""

    def test_27b_does_not_fit_and_names_none(self):
        d = plan("laptop-16g", "Qwen__Qwen3.8-27B", QWEN27_VARIANTS)
        self.assertFalse(d["fits"])
        self.assertIn("no listed variant fits either", d["summary"])

    def test_swap_reported(self):
        d = plan("laptop-16g", "OpenVINO__Qwen3.8-27B-int4-ov")
        self.assertTrue(any("swap in use" in s for s in d["explain"]))


class FitNowhere(unittest.TestCase):
    def test_synthetic_huge_moe_refused_everywhere(self):
        for h in ("desktop-i9-nogpu", "xeon6741p-4xb70", "laptop-16g"):
            d = plan(h, "synthetic__huge-moe")
            self.assertFalse(d["fits"], h)
            self.assertTrue(d["summary"].startswith("fit: synthetic/huge-moe does not fit this host"), d["summary"])


class Overrides(unittest.TestCase):
    def test_ctx_and_seqs_asked(self):
        d = plan("xeon6741p-4xb70", "RedHatAI__Qwen3.8-27B-INT4", ctx=65536, seqs=2)
        self.assertTrue(d["fits"])
        self.assertEqual((d["ctx"], d["seqs"]), (65536, 2))
        self.assertIn("--max-model-len=65536", d["flags"])
        self.assertIn("--max-num-seqs=2", d["flags"])

    def test_too_much_asked_is_refused(self):
        d = plan("laptop-16g", "OpenVINO__Qwen3.8-27B-int4-ov", seqs=200)
        self.assertFalse(d["fits"])

    def test_vllm_cpu_for_eat(self):
        # --engine vllm --device cpu (the EAT overlay): KV pool as VLLM_CPU_KVCACHE_SPACE GiB.
        d = plan("xeon6741p-4xb70", "Qwen__Qwen3.8-27B", engine="vllm", device="cpu")
        self.assertEqual((d["engine"], d["placement"]), ("vllm", "cpu"))
        kv = [f for f in d["flags"] if f.startswith("VLLM_CPU_KVCACHE_SPACE=")]
        self.assertEqual(kv, ["VLLM_CPU_KVCACHE_SPACE=%d" % (d["kv_bytes"] // GIB)])
        # Without --device cpu, vLLM is never placed on the CPU.
        self.assertNotEqual(plan("xeon6741p-4xb70", "Qwen__Qwen3.8-27B")["placement"], "cpu")

    def test_limit_within_budget(self):
        for h in ("desktop-i9-nogpu", "xeon6741p-4xb70"):
            for mname in ("OpenVINO__Qwen3.8-27B-int4-ov", "Intel__Qwen3.8-Flash-Next-W4A16-AutoRound", "Qwen__Qwen3.8-27B"):
                for dev in (None, "cpu"):
                    d = plan(h, mname, device=dev, engine="vllm" if dev and mname == "Qwen__Qwen3.8-27B" else None)
                    if not d["fits"]:
                        continue
                    budget, _, _ = fit.host_budget(host(h))
                    self.assertLessEqual(int(d["k8s"]["memory_limit"][:-2]) * GIB, budget, (h, mname, dev))

    def test_device_cpu_only(self):
        d = plan("xeon6741p-4xb70", "google__gemma-4-26B-A4B-it-qat-q4_0-gguf", device="cpu")
        self.assertEqual(d["placement"], "cpu")

    def test_kv_scheme_follows_checkpoint(self):
        # RedHat INT4 carries a kv_cache_scheme: vLLM serves an fp8 KV cache.
        d = plan("xeon6741p-4xb70", "RedHatAI__Qwen3.8-27B-INT4")
        self.assertTrue(any("kv_cache_scheme" in s for s in d["explain"]))


class OvmsOneGpu(unittest.TestCase):
    """OVMS serves one GPU: --gpus 2 is refused, never sized as a layer split of two cards."""

    def test_two_gpus_asked_is_refused(self):
        for mname in ("OpenVINO__gemma-4-26b-a4b-it-int4-ov", "OpenVINO__Qwen3.8-27B-int4-ov"):
            d = plan("xeon6741p-4xb70", mname, gpus=2, device="gpu")
            self.assertFalse(d["fits"], (mname, d["summary"]))
            self.assertIn("OVMS serves one GPU", d["reason"])

    def test_cache_size_within_one_card(self):
        one = max(c["budget"] for c in fit.gpu_budget(host("xeon6741p-4xb70"))[0])
        for gpus in (None, 1, 2):
            d = plan("xeon6741p-4xb70", "OpenVINO__gemma-4-26b-a4b-it-int4-ov", gpus=gpus, device="gpu")
            if not d["fits"]:
                continue
            self.assertEqual((d["placement"], d["gpus"]), ("gpu", 1), gpus)
            cache = [int(f.split("=")[1]) for f in d["flags"] if f.startswith("--cache_size=")]
            self.assertLessEqual(cache[0] * GIB, one, (gpus, d["flags"]))

    def test_no_free_gpu_says_so(self):
        # device gpu on a host with no Intel GPU of its own memory: no placement is tried at all
        d = plan("desktop-i9-nogpu", "OpenVINO__Qwen3.8-27B-int4-ov", device="gpu")
        self.assertFalse(d["fits"])
        self.assertIn("no Intel GPU with device-local memory is free", d["reason"])
        self.assertNotIn("release notes", d["reason"])


class Guard(unittest.TestCase):
    """guard_step under simulated PSI: a CPU-placed server sheds rungs; a VRAM one holds or stops."""

    def guard(self, placement):
        return {"rungs": fit.guard_rungs(65536, 8), "placement": placement, "some_avg10": 20.0, "full_avg10": 5.0,
                "calm_some_avg60": 5.0, "calm_samples": 3, "emergency_samples": 3, "mem_available_floor": 4 * GIB,
                "host_bytes": 40 * GIB}

    @staticmethod
    def sample(some10=0.0, some60=0.0, full10=0.0, avail=64 * GIB):
        return {"psi": {"some": {"avg10": some10, "avg60": some60}, "full": {"avg10": full10}}, "mem_available": avail}

    def run_seq(self, g, samples):
        st, out = {}, []
        for s in samples:
            a, st, _ = fit.guard_step(g, s, st)
            out.append((a, st["rung"]))
        return out

    def test_cooldown_after_a_step(self):
        g = dict(self.guard("cpu"), cooldown_samples=2)
        hot = self.sample(some10=30, some60=25)
        out = self.run_seq(g, [hot, hot, hot, hot])
        self.assertEqual(out, [("down", 1), ("hold", 1), ("hold", 1), ("down", 2)])

    def test_emergency_through_the_cooldown(self):
        g = dict(self.guard("cpu"), cooldown_samples=5)
        e = self.sample(full10=9)
        # down to the floor; the emergency persists through the cooldown: stop at its third sample
        self.assertEqual(self.run_seq(g, [e, e, e]), [("down", 7), ("hold", 7), ("stop", 7)])
        # an emergency that clears inside the cooldown resets the count
        self.assertEqual(self.run_seq(g, [e, e, self.sample(), e]), [("down", 7), ("hold", 7), ("hold", 7), ("hold", 7)])

    def test_small_vram_server_is_not_stopped(self):
        g = dict(self.guard("gpu"), host_bytes=2 * GIB)  # under the 4 GiB floor: stopping frees little
        e = self.sample(full10=9)
        self.assertEqual([a for a, _ in self.run_seq(g, [e] * 5)], ["hold"] * 5)

    def test_rung_out_of_range_is_clamped(self):
        a, st, _ = fit.guard_step(self.guard("cpu"), self.sample(), {"rung": 99})
        self.assertEqual((a, st["rung"]), ("hold", 7))

    def test_knobs_per_rung(self):
        d = plan("desktop-i9-nogpu", "OpenVINO__Qwen3.8-27B-int4-ov")
        k0 = dict(l.split("=", 1) for l in fit.knobs(d, 0).splitlines())
        k3 = dict(l.split("=", 1) for l in fit.knobs(d, 3).splitlines())
        self.assertEqual((k0["ctx"], k0["seqs"], k0["memory"]), (str(d["ctx"]), str(d["seqs"]), d["k8s"]["memory_limit"]))
        self.assertEqual((k3["seqs"], k3["ctx"]), tuple(str(x) for x in d["guard"]["rungs"][3]))
        # a CPU-placed rung holds less KV, so its pod limit and static pool are smaller
        self.assertLess(int(k3["memory"][:-2]), int(k0["memory"][:-2]))
        self.assertLess(int(k3["cache_gib"]), int(k0["cache_gib"]))

    def test_rungs(self):
        self.assertEqual(fit.guard_rungs(65536, 8), [(8, 65536), (4, 65536), (2, 65536), (1, 65536),
                                                     (1, 32768), (1, 16384), (1, 8192), (1, 4096)])

    def test_cpu_pressure_steps_down_then_recovers(self):
        g = self.guard("cpu")
        hot, calm = self.sample(some10=30, some60=25), self.sample()
        out = self.run_seq(g, [hot, hot, calm, calm, calm, calm, calm, calm])
        self.assertEqual(out, [("down", 1), ("down", 2), ("hold", 2), ("hold", 2), ("up", 1),
                               ("hold", 1), ("hold", 1), ("up", 0)])

    def test_cpu_emergency_goes_to_floor_then_stops(self):
        g = self.guard("cpu")
        out = self.run_seq(g, [self.sample(full10=8), self.sample(avail=1 * GIB)])
        self.assertEqual(out, [("down", 7), ("stop", 7)])

    def test_vram_server_holds_under_pressure(self):
        g = self.guard("gpu")
        out = self.run_seq(g, [self.sample(some10=40, some60=30)] * 3)
        self.assertEqual(out, [("hold", 0)] * 3)

    def test_vram_server_stops_after_sustained_emergency(self):
        g = self.guard("moe-offload")
        e = self.sample(full10=9)
        out = self.run_seq(g, [e, e, self.sample(), e, e, e])
        self.assertEqual([a for a, _ in out], ["hold", "hold", "hold", "hold", "hold", "stop"])

    def test_psi_file_parse(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "/proc/pressure/memory", "some avg10=25.10 avg60=9.00 avg300=1.00 total=1\n"
                                              "full avg10=6.00 avg60=2.00 avg300=0.10 total=2\n")
            p = fit.psi(d)
        a, st, why = fit.guard_step(self.guard("cpu"), {"psi": p, "mem_available": 64 * GIB}, {})
        self.assertEqual((a, st["rung"]), ("down", 7))
        self.assertIn("PSI full avg10 6.0", why)


HEADS = os.path.join(FIX, "gguf-heads")
FLASH_GGUF = "ggml-org__Qwen3.8-Flash-Next-GGUF"


def head_reader(name):
    """A reader over a saved GGUF header (tests/fixtures/fit/gguf-heads): the bytes up to
    the tensor data, which is all gguf_header reads; past them it fails loudly."""
    with gzip.open(os.path.join(HEADS, name + ".head.gz"), "rb") as f:
        blob = f.read()

    def read(off, n):
        if off >= len(blob):
            raise AssertionError("read past the saved header of %s at %d" % (name, off))
        return blob[off:off + n]
    return read


def head_size(name):
    with open(os.path.join(HEADS, "INDEX.json"), encoding="utf-8") as f:
        return json.load(f)["files"][name]["size"]


class SplitGGUF(unittest.TestCase):
    """ggml-org/Qwen3.8-Flash-Next-GGUF@91fb003: IQ4_NL in two gguf-split shards. Shard 1
    holds only metadata (0 tensors); shard 2 holds all 1224 tensors."""

    S1 = "Qwen3.8-Flash-Next-IQ4_NL-00001-of-00002.gguf"
    S2 = "Qwen3.8-Flash-Next-IQ4_NL-00002-of-00002.gguf"

    def test_each_shard_header(self):
        h1 = fit.gguf_header(head_reader(self.S1), head_size(self.S1))
        h2 = fit.gguf_header(head_reader(self.S2), head_size(self.S2))
        self.assertEqual(h1["tensors"], [])
        self.assertEqual((h1["kv"]["split.no"], h1["kv"]["split.count"], h1["kv"]["split.tensors.count"]), (0, 2, 1224))
        self.assertEqual(h1["kv"]["general.architecture"], "qwen4exp")
        self.assertEqual(len(h2["tensors"]), 1224)
        self.assertEqual(h2["kv"]["split.no"], 1)

    def test_split_sums_every_shard(self):
        reads = {n: (head_reader(n), head_size(n)) for n in (self.S1, self.S2)}
        w = fit.gguf_group_weights(sorted(reads), lambda n: reads[n], "test")
        cls = {k: v["bytes"] for k, v in w["classes"].items()}
        # qwen4exp: experts and the n-gram table (per_layer_token_embd) are counted and classed
        self.assertGreater(cls["experts"], 60 * GIB)
        self.assertGreater(cls["ngram"], 25 * GIB)
        self.assertGreater(w["total_bytes"], 90 * GIB)
        self.assertEqual(w["tensor_count"], 1224)
        # the PLE projection matrices are matmuls, not the lookup table
        self.assertEqual(fit.tensor_class("blk.1.ple_key.weight"), "other")
        self.assertEqual(fit.tensor_class("per_layer_token_embd.weight"), "ngram")

    def test_missing_shard_is_an_error(self):
        reads = {self.S1: (head_reader(self.S1), head_size(self.S1))}
        with self.assertRaisesRegex(ValueError, "split.count 2.*1 of them"):
            fit.gguf_group_weights([self.S1], lambda n: reads[n], "test")

    def test_groups_from_a_listing(self):
        names = [self.S1, self.S2, "Qwen3.8-Flash-Next-Q2_K-00001-of-00002.gguf",
                 "Qwen3.8-Flash-Next-Q2_K-00002-of-00002.gguf", "Qwen3.8-Flash-Next-Q8_0-00001-of-00002.gguf",
                 "Qwen3.8-Flash-Next-Q8_0-00002-of-00002.gguf", "mmproj-Qwen3.8-Flash-Next-Q8_0.gguf",
                 "mtp-Qwen3.8-Flash-Next-Q4_0.gguf", "dflash-Qwen3.8-Flash-Next-Q8_0.gguf", "README.md"]
        g = fit.gguf_groups(names)
        self.assertEqual(sorted(g), ["IQ4_NL", "Q2_K", "Q8_0"])
        self.assertEqual(g["IQ4_NL"], [self.S1, self.S2])
        # --file takes a shard name, a group label, or the group's file stem
        self.assertEqual(fit.pick_group(g, self.S1), "IQ4_NL")
        self.assertEqual(fit.pick_group(g, "q8_0"), "Q8_0")
        with self.assertRaisesRegex(ValueError, "no GGUF group matches"):
            fit.pick_group(g, "Q5_K_M")
        # the optimizer's llama.cpp rule: int4 first, then fewer bits, more bits last
        self.assertEqual(fit.group_order(g), ["IQ4_NL", "Q2_K", "Q8_0"])


class ZeroWeights(unittest.TestCase):
    def test_zero_bytes_refused(self):
        cfg = config("Qwen__Qwen3.8-Flash-Next")
        w = {"schema": fit.WEIGHTS_SCHEMA, "format": "gguf", "source": "x", "files": [], "classes": {}, "total_bytes": 0}
        d = fit.fit(host("xeon6741p-4xb70"), cfg, w, name="x")
        self.assertFalse(d["fits"])
        self.assertIn("weights read as 0", d["reason"])

    def test_moe_without_experts_refused(self):
        cfg = config("Qwen__Qwen3.8-Flash-Next")
        w = {"schema": fit.WEIGHTS_SCHEMA, "format": "gguf", "source": "x", "files": [],
             "classes": {"other": {"bytes": 4 * GIB, "tensors": 10}}, "total_bytes": 4 * GIB}
        d = fit.fit(host("xeon6741p-4xb70"), cfg, w, name="x")
        self.assertFalse(d["fits"])
        self.assertIn("no expert tensors", d["reason"])


class FlashNextGGUF(unittest.TestCase):
    """The ggml-org GGUF repo with three split groups, as `fetch` records it."""

    def test_auto_pick_fits_without_a_flag(self):
        d = plan("xeon6741p-4xb70", FLASH_GGUF)
        self.assertTrue(d["fits"], d["summary"])
        self.assertEqual(d["gguf_group"], "IQ4_NL")
        self.assertEqual((d["engine"], d["placement"]), ("llama.cpp", "moe-offload"))
        self.assertGreater(d["host_bytes"], 25 * GIB)  # the n-gram table stays in host RAM
        self.assertIn("IQ4_NL", d["summary"])

    def test_file_override(self):
        d = plan("xeon6741p-4xb70", FLASH_GGUF, file="Qwen3.8-Flash-Next-Q8_0-00001-of-00002.gguf")
        self.assertEqual(d["gguf_group"], "Q8_0")
        d = plan("xeon6741p-4xb70", FLASH_GGUF, file="Q2_K")
        self.assertEqual(d["gguf_group"], "Q2_K")

    def test_falls_to_fewer_bits_when_int4_does_not_fit(self):
        d = plan("desktop-i9-nogpu", FLASH_GGUF)
        self.assertFalse(d["fits"])
        self.assertTrue(any("IQ4_NL" in s for s in d["explain"]) and any("Q2_K" in s for s in d["explain"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
