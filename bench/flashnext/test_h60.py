#!/usr/bin/env python3
"""Unit tests for bench/flashnext/h60.py: the GGUF byte classes and the H60 decision rule."""
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h60  # noqa: E402


def write_gguf(path, kv, tensors, data_bytes):
    """A minimal GGUF v3: kv (str or uint32 values), tensors [(name, offset)], then data_bytes."""
    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    out = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", len(tensors), len(kv))
    for k, v in kv.items():
        out += s(k) + (struct.pack("<I", 8) + s(v) if isinstance(v, str) else struct.pack("<II", 4, v))
    for name, off in tensors:
        out += s(name) + struct.pack("<I", 1) + struct.pack("<Q", 1) + struct.pack("<IQ", 0, off)
    out += b"\0" * ((32 - len(out) % 32) % 32)
    with open(path, "wb") as f:
        f.write(out + b"\0" * data_bytes)


class ActiveBytes(unittest.TestCase):
    def test_classes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.gguf")
            kv = {"general.architecture": "qwen4exp", "qwen4exp.expert_count": 512, "qwen4exp.expert_used_count": 10}
            # token_embd 1000, per_layer_token_embd 5000, blk.0.ffn_up_exps 51200, blk.0.attn_q 300, output 700
            t = [("token_embd.weight", 0), ("per_layer_token_embd.weight", 1000), ("blk.0.ffn_up_exps.weight", 6000),
                 ("blk.0.attn_q.weight", 57200), ("output.weight", 57500)]
            write_gguf(p, kv, t, 58200)
            a = h60.active_bytes([p])
        self.assertEqual(a["bytes"], {"routed_experts": 51200, "lookup_tables": 6000, "rest": 1000})
        self.assertEqual(a["active_bytes"], 1000 + 1000)   # 51200 x 10 / 512 = 1000


def run_dir(d, tps, gsm=40, void=False, psi=False, n=8):
    json.dump({"active_bytes": 6_000_000_000}, open(os.path.join(d, "active-bytes.json"), "w"))
    s = [{"ok": True, "decode_tokens_per_s": t} for t in (tps * n)[:n]]
    json.dump({"phases": [{"samples": s}]}, open(os.path.join(d, "load-n1.json"), "w"))
    with open(os.path.join(d, "phases.jsonl"), "w") as f:
        f.write(json.dumps({"phase": "n1", "check": {"void": void, "reason": "x"}}) + "\n")
    json.dump({"correct": gsm}, open(os.path.join(d, "quality.json"), "w"))
    if psi:
        open(os.path.join(d, "psi-abort"), "w").write("full avg10 9")


def score(**kw):
    with tempfile.TemporaryDirectory() as d:
        run_dir(d, **kw)
        return h60.score(d)


class Rule(unittest.TestCase):
    # ceiling = 305e9 / 6e9 = 50.83 tok/s; band 0.266-0.494 = 13.5-25.1 tok/s
    def test_inside_band_supported(self):
        self.assertEqual(score(tps=[17.0, 17.4, 16.8, 17.2])["verdict"], "SUPPORTED")

    def test_far_below_refuted(self):
        self.assertEqual(score(tps=[6.0, 6.1, 5.9])["verdict"], "REFUTED")

    def test_far_above_refuted(self):
        self.assertEqual(score(tps=[30.0, 31.0])["verdict"], "REFUTED")

    def test_straddling_inconclusive(self):
        self.assertEqual(score(tps=[11.0, 14.0, 12.0, 15.0])["verdict"], "INCONCLUSIVE")

    def test_quality_gate(self):
        v = score(tps=[17.0], gsm=20)
        self.assertEqual(v["verdict"], "INCONCLUSIVE")
        self.assertIn("GSM8K", v["why"][0])

    def test_void_and_psi(self):
        self.assertEqual(score(tps=[17.0], void=True)["verdict"], "INCONCLUSIVE")
        self.assertEqual(score(tps=[17.0], psi=True)["verdict"], "INCONCLUSIVE")

    def test_too_few_requests(self):
        self.assertEqual(score(tps=[17.0], n=5)["verdict"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
