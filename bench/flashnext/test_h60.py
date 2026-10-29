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

    def test_tied_output_reads_token_embd(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "g.gguf")
            kv = {"general.architecture": "gemma4", "gemma4.expert_count": 128, "gemma4.expert_used_count": 8}
            t = [("token_embd.weight", 0), ("blk.0.ffn_up_exps.weight", 1000), ("blk.0.attn_q.weight", 13800)]
            write_gguf(p, kv, t, 14000)
            a = h60.active_bytes([p])
        self.assertTrue(a["tied_output"])
        self.assertEqual(a["active_bytes"], 1000 + 200 + 12800 * 8 // 128)


def arm_dir(d, name, tps, void=False, n=8):
    os.makedirs(os.path.join(d, name), exist_ok=True)
    s = [{"ok": True, "decode_tokens_per_s": t} for t in (tps * n)[:n]]
    json.dump({"phases": [{"samples": s}]}, open(os.path.join(d, name, "load-n1.json"), "w"))
    with open(os.path.join(d, name, "phases.jsonl"), "w") as f:
        f.write(json.dumps({"phase": "n1", "check": {"void": void, "reason": "x"}}) + "\n")


def run_dir(d, tps, lg=None, gsm=40, void=False, psi=False, n=8, lg_n=8):
    json.dump({"active_bytes": 6_000_000_000}, open(os.path.join(d, "active-bytes.json"), "w"))
    arm_dir(d, "fn", tps, void, n)
    json.dump({"correct": gsm}, open(os.path.join(d, "fn", "quality.json"), "w"))
    if lg is not None:
        json.dump({"active_bytes": 2_400_000_000}, open(os.path.join(d, "active-bytes-lg.json"), "w"))
        arm_dir(d, "lg", lg, n=lg_n)
    if psi:
        open(os.path.join(d, "psi-abort"), "w").write("full avg10 9")


def score(**kw):
    with tempfile.TemporaryDirectory() as d:
        run_dir(d, **kw)
        return h60.score(d)


class Rule(unittest.TestCase):
    # fn ceiling 305e9 / 6e9 = 50.83 tok/s; lg ceiling 305e9 / 2.4e9 = 127.1 tok/s.
    # Lg at 48 tok/s: e_lg 0.378; r band 0.7-1.3 -> fn 13.4-25.0 tok/s.
    def test_control_ratio_inside_supported(self):
        v = score(tps=[19.0, 19.2, 18.9, 19.1], lg=[48.0, 48.3, 47.9])
        self.assertEqual((v["verdict"], v["stat"]), ("SUPPORTED", "e_fn / e_lg"))

    def test_control_ratio_below_refuted(self):
        self.assertEqual(score(tps=[8.0, 8.1, 7.9], lg=[48.0, 48.3])["verdict"], "REFUTED")

    def test_control_moves_the_band(self):
        # e_fn 0.22 is below H54's fixed band, but Lg ran slow in the same hold (e_lg 0.24): ratio 0.92
        v = score(tps=[11.2, 11.3, 11.1], lg=[30.5, 30.6, 30.4])
        self.assertEqual(v["verdict"], "SUPPORTED")

    def test_without_control_fixed_band(self):
        v = score(tps=[17.0, 17.4, 16.8, 17.2])
        self.assertEqual((v["verdict"], v["stat"]), ("SUPPORTED", "e_fn"))
        self.assertTrue(v["control"].startswith("H54 fixed"))

    def test_short_control_falls_back(self):
        v = score(tps=[17.0, 17.2], lg=[48.0], lg_n=5)
        self.assertEqual(v["stat"], "e_fn")

    def test_far_above_refuted(self):
        self.assertEqual(score(tps=[30.0, 31.0])["verdict"], "REFUTED")

    def test_straddling_inconclusive(self):
        self.assertEqual(score(tps=[11.0, 14.0, 12.0, 15.0])["verdict"], "INCONCLUSIVE")

    def test_quality_gate(self):
        v = score(tps=[17.0], gsm=20)
        self.assertEqual(v["verdict"], "INCONCLUSIVE")
        self.assertTrue(any("GSM8K" in w for w in v["why"]))

    def test_void_and_psi(self):
        self.assertEqual(score(tps=[17.0], void=True)["verdict"], "INCONCLUSIVE")
        self.assertEqual(score(tps=[17.0], psi=True)["verdict"], "INCONCLUSIVE")

    def test_too_few_requests(self):
        self.assertEqual(score(tps=[17.0], n=5)["verdict"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
