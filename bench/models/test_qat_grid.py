#!/usr/bin/env python3
"""Tests for bench/models/qat_grid.py. The grid arithmetic and the safetensors reader run with
numpy only; the IR check and snap run where the openvino package is importable (the export
venv) and are skipped elsewhere."""
import json
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import numpy as np
    import qat_grid as G
except ImportError:          # CI runners without numpy skip every case
    np = G = None

try:
    import openvino as ov
except ImportError:
    ov = None


def qat_weight(rng, out=8, inp=64):
    """A QAT-like weight: each 32-block holds k * d, k in [-8, 7], one step d per block."""
    d = rng.uniform(0.001, 0.02, size=(out * inp // 32, 1))
    k = rng.integers(-8, 8, size=(out * inp // 32, 32))
    k[:, 0] = 7                                    # a positive extreme: where absmax/8 re-rounds
    return (k * d).reshape(out, inp).astype(np.float32)


def bf16(x):
    """Round to bf16 and back, as a bf16 checkpoint stores it."""
    u = np.asarray(x, dtype=np.float32).view(np.uint32)
    return ((u + 0x8000) & 0xFFFF0000).view(np.float32)


def write_st(path, tensors):
    hdr, blob, off = {}, b"", 0
    for name, w in tensors.items():
        raw = (bf16(w).view(np.uint32) >> 16).astype(np.uint16).tobytes()
        hdr[name] = {"dtype": "BF16", "shape": list(w.shape), "data_offsets": [off, off + len(raw)]}
        blob += raw
        off += len(raw)
    h = json.dumps(hdr).encode()
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(h)) + h + blob)


@unittest.skipIf(np is None, "numpy not installed")
class GridTest(unittest.TestCase):
    def test_grid_step_snap_reproduces_qat_blocks(self):
        w = bf16(qat_weight(np.random.default_rng(1)))
        q, d = G.snap_blocks(G.blocks(w.astype(np.float64)))
        back = (q * d[:, None].astype(np.float64)).reshape(w.shape)
        self.assertLess(G.rel_rms(back, w), 0.01)          # fp16 scale rounding only
        # absmax/8 (an absmax-based symmetric scale) re-rounds a block whose extreme is +7 d
        b = G.blocks(w.astype(np.float64))
        s = np.abs(b).max(1, keepdims=True) / 8
        absmax = (np.clip(np.rint(b / s), -8, 7) * s).reshape(w.shape)
        self.assertGreater(G.rel_rms(absmax, w), 0.02)

    def test_gate(self):
        self.assertTrue(G.gate({"matched": 98, "skipped": 2}, "matched", 0.02)[0])
        self.assertFalse(G.gate({"matched": 97, "skipped": 3}, "matched", 0.02)[0])
        self.assertFalse(G.gate({"matched": 0, "skipped": 0}, "matched", 0.02)[0])
        snap = {"snapped": 10, "skipped": {"no_match": 0, "not_i4": 0, "size": 0, "far": 1}}
        self.assertFalse(G.gate(snap, "snapped", 0.02)[0])
        self.assertTrue(G.gate(snap, "snapped", 0.1)[0])

    def test_zero_block_and_kinds(self):
        q, d = G.snap_blocks(np.zeros((2, 32)))
        self.assertTrue((q == 0).all())
        self.assertEqual(G.kind("model.language_model.layers.3.experts.gate_up_proj"), "experts")
        self.assertEqual(G.kind("model.language_model.layers.3.self_attn.q_proj.weight"), "attention")
        self.assertEqual(G.kind("model.language_model.embed_tokens.weight"), "other")

    def test_safetensors_reader_and_name_match(self):
        w = qat_weight(np.random.default_rng(2))
        with tempfile.TemporaryDirectory() as d:
            write_st(os.path.join(d, "model-00001-of-00001.safetensors"),
                     {"model.language_model.layers.0.self_attn.q_proj.weight": w})
            idx = G.st_index(d)
            n = G.match_name("self.model.language_model.layers.0.self_attn.q_proj.weight", idx)
            self.assertEqual(n, "model.language_model.layers.0.self_attn.q_proj.weight")
            self.assertLess(G.rel_rms(G.st_read(idx[n]), w), 0.01)
            self.assertIsNone(G.match_name("self.lm_head.weight", idx))

    def test_real_export_names(self):
        # The names of a real optimum-intel 2.2.0 Gemma 4 export (crescent, 2026-10-20).
        rng = np.random.default_rng(5)
        gu = rng.standard_normal((2, 6, 4)).astype(np.float32)          # [E, 2I, H]
        q = rng.standard_normal((3, 4)).astype(np.float32)
        with tempfile.TemporaryDirectory() as d:
            write_st(os.path.join(d, "w.safetensors"), {
                "model.language_model.layers.0.experts.gate_up_proj": bf16(gu),
                "model.language_model.layers.0.self_attn.q_proj.weight": bf16(q)})
            idx = G.st_index(d)
            n, a = G.qat_tensor("self.model.language_model.layers.0.self_attn.q_proj._openvino_orig_weight", idx)
            self.assertEqual(n, "model.language_model.layers.0.self_attn.q_proj.weight")
            self.assertEqual(a.shape, (3, 4))
            for part in (0, 1):
                n, a = G.qat_tensor("__module.model.language_model.layers.0.experts/prim::ListUnpack/VariadicSplit."
                                    + str(part), idx)
                self.assertEqual(n, f"model.language_model.layers.0.experts.gate_up_proj[{part}]")
                self.assertEqual(a.shape, (2, 3, 4))
                self.assertLess(G.rel_rms(a, gu[:, 3 * part:3 * part + 3]), 0.01)
            self.assertEqual(G.qat_tensor("__module.model.language_model.layers.0.experts/prim::ListUnpack/"
                                          "VariadicSplit.2", idx), (None, None))


@unittest.skipIf(ov is None or np is None, "openvino not installed")
class IRTest(unittest.TestCase):
    """An NNCF-shaped INT4_SYM g32 weight: Constant(i4) -> Convert -> Multiply(scale) -> Reshape."""

    def ir(self, d, w, scale_rule):
        from openvino import opset13 as ops
        b = G.blocks(w.astype(np.float64))
        if scale_rule == "absmax":
            s = (np.abs(b).max(1) / 8).astype(np.float16)
        else:
            s = G.grid_step(b).astype(np.float16)
        q = np.clip(np.rint(b / s[:, None].astype(np.float64)), -8, 7).astype(np.int8)
        out, inp = w.shape
        c = ops.constant(q.reshape(out, inp // 32, 32), dtype=ov.Type.i4)
        c.set_friendly_name("self.model.language_model.layers.0.self_attn.q_proj.weight")
        m = ops.multiply(ops.convert(c, ov.Type.f16), ops.constant(s.reshape(out, inp // 32, 1), dtype=ov.Type.f16))
        r = ops.reshape(m, ops.constant(np.array([out, inp], dtype=np.int64)), False)
        x = ops.parameter([1, inp], ov.Type.f32)
        y = ops.matmul(x, ops.convert(r, ov.Type.f32), False, True)
        p = os.path.join(d, "m.xml")
        ov.save_model(ov.Model([y], [x]), p, compress_to_fp16=False)
        return p

    def test_check_then_snap(self):
        w = bf16(qat_weight(np.random.default_rng(3)))
        with tempfile.TemporaryDirectory() as d:
            write_st(os.path.join(d, "w.safetensors"), {"model.language_model.layers.0.self_attn.q_proj.weight": w})
            ir = self.ir(d, w, "absmax")
            out = os.path.join(d, "c.json")
            self.assertEqual(G.main(["check", ir, d, "--out", out]), 0)
            before = json.load(open(out))
            self.assertEqual(before["matched"], 1)
            self.assertGreater(before["summary"]["attention"]["median"], 0.02)
            snapped = os.path.join(d, "s.xml")
            rep = os.path.join(d, "snap.json")
            self.assertEqual(G.main(["snap", ir, d, snapped, "--report", rep]), 0)
            r = json.load(open(rep))
            self.assertEqual((r["weights"], r["snapped"]), (1, 1))
            t = r["tensors"][0]
            self.assertAlmostEqual(t["pre_rel_rms"], before["tensors"][0]["rel_rms"], places=4)
            self.assertLess(t["snapped_vs_qat_rel_rms"], 0.01)
            self.assertGreater(t["snapped_vs_export_rel_rms"], 0.02)
            self.assertEqual(G.main(["check", snapped, d, "--out", out]), 0)
            after = json.load(open(out))
            self.assertLess(after["summary"]["attention"]["median"], 0.01)

    def test_snap_leaves_a_far_match_alone(self):
        rng = np.random.default_rng(4)
        w = bf16(qat_weight(rng))
        other = bf16(qat_weight(rng))      # same shape, unrelated values: a wrong match
        with tempfile.TemporaryDirectory() as d:
            write_st(os.path.join(d, "w.safetensors"), {"model.language_model.layers.0.self_attn.q_proj.weight": other})
            ir = self.ir(d, w, "absmax")
            rep = os.path.join(d, "snap.json")
            self.assertEqual(G.main(["snap", ir, d, os.path.join(d, "s.xml"), "--report", rep]), 0)
            r = json.load(open(rep))
            self.assertEqual((r["snapped"], r["skipped"]["far"]), (0, 1))
            self.assertEqual(G.main(["gate", rep, "snapped"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
