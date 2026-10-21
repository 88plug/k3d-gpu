#!/usr/bin/env python3
"""Tests for the H54b/H54c runner pieces: python3 bench/h54gpu/test_h54gpu.py"""
import json
import os
import struct
import sys
import tempfile
import subprocess
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gguf_bytes  # noqa: E402
import gsm8k  # noqa: E402
import predict  # noqa: E402
import score  # noqa: E402


def s(x):
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def write_gguf(path, tensors, kv):
    """tensors: [(name, dims, ggml_type)]; kv: {key: (type, value)} with u32 (4) / str (8)."""
    out = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(kv))
    for k, (t, v) in kv.items():
        out += s(k) + struct.pack("<I", t) + (s(v) if t == 8 else struct.pack("<I", v))
    for name, dims, t in tensors:
        out += s(name) + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
        out += struct.pack("<IQ", t, 0)
    with open(path, "wb") as f:
        f.write(out)


def run_json(path, decs):
    with open(path, "w") as f:
        json.dump({"phases": [{"concurrency": 1, "samples": [
            {"ok": True, "decode_tokens_per_s": d} for d in decs]}]}, f)


class GGUF(unittest.TestCase):
    def test_split(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.gguf")
            write_gguf(p, [
                ("token_embd.weight", [64, 10], 2),            # Q4_0: 640/32*18 = 360
                ("blk.0.attn_q.weight", [64, 64], 8),          # Q8_0: 4096/32*34 = 4352
                ("blk.0.ffn_gate_exps.weight", [64, 32, 4], 2),  # 8192/32*18 = 4608
                ("blk.0.ffn_down_exps.weight", [32, 64, 4], 2),  # 4608
                ("blk.0.attn_norm.weight", [64], 0),           # F32: 256
            ], {"general.architecture": (8, "gemma4"), "gemma4.expert_count": (4, 4),
                "gemma4.expert_used_count": (4, 1)})
            kv, t = gguf_bytes.tensors(p)
            r = gguf_bytes.split(t, kv["gemma4.expert_count"], kv["gemma4.expert_used_count"])
            self.assertEqual(r["exps_all"], 9216)
            self.assertEqual(r["B_exp"], 9216 / 4)
            self.assertEqual(r["B_rest"], 4352 + 256 + 360)   # tied embedding counted once
            self.assertEqual(r["output_tensor"], "token_embd.weight")

    def test_untied_output_preferred(self):
        t = [("token_embd.weight", 0, 100), ("output.weight", 0, 7), ("blk.0.x.weight", 0, 1)]
        self.assertEqual(gguf_bytes.split(t, 8, 1)["B_rest"], 8)

    def test_not_gguf(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"NOPE")
            f.flush()
            with self.assertRaises(ValueError):
                gguf_bytes.tensors(f.name)


B = {"B_exp": 1.0, "B_rest": 1.0, "B_tot": 2.0}


class Predict(unittest.TestCase):
    def pred(self, cg, gg):
        return 1 / (0.5 / cg + 0.5 / gg + 60 * 30e-6)

    def test_supported_and_refuted(self):
        p = self.pred(20, 60)
        self.assertEqual(predict.verdict(B, [20] * 8, [60] * 8, [p * 1.2] * 8, [60] * 8)["verdict"], "SUPPORTED")
        self.assertEqual(predict.verdict(B, [20] * 8, [60] * 8, [p * 0.7] * 8, [60] * 8)["verdict"], "INCONCLUSIVE")
        self.assertEqual(predict.verdict(B, [20] * 8, [60] * 8, [p * 0.5] * 8, [60] * 8)["verdict"], "REFUTED")
        self.assertEqual(predict.verdict(B, [20] * 8, [60] * 8, [p * 1.5] * 8, [60] * 8)["verdict"], "REFUTED")

    def test_drift_and_missing(self):
        p = self.pred(20, 60)
        self.assertEqual(predict.verdict(B, [20] * 8, [60] * 8, [p] * 8, [70] * 8)["verdict"], "INCONCLUSIVE")
        self.assertEqual(predict.verdict(B, [], [60] * 8, [p] * 8, [60] * 8)["verdict"], "INCONCLUSIVE")


class Score(unittest.TestCase):
    def spec(self, d, arms):
        out = {}
        for k, (decs, correct, rank, cards) in arms.items():
            p = None
            if decs:
                p = os.path.join(d, k + ".json")
                run_json(p, decs)
            out[k] = {"run": p, "correct": correct, "rank": rank, "cards": cards}
        return out

    def test_supported_and_source_rule(self):
        with tempfile.TemporaryDirectory() as d:
            sp = self.spec(d, {"B2q": ([8.0] * 8, 40, 1, 2), "X1q": ([20.0] * 8, 40, 1, 1),
                               "Rq": ([20.5] * 8, 40, 2, 1), "Lsq": ([30.0] * 8, 30, 1, 1)})
            r = score.score(sp)
            self.assertEqual(r["chosen"], "X1q")          # Rq < 5% faster; Lsq fails the gate
            self.assertEqual(r["verdict"], "SUPPORTED")
            self.assertNotIn("Lsq", r["eligible"])

    def test_lower_rank_replaces(self):
        arms = {"X1q": {"dec": [20.0] * 8, "correct": 40, "rank": 1},
                "Rq": {"dec": [21.0] * 8, "correct": 38, "rank": 2}}
        self.assertEqual(score.choose(arms)[0], "Rq")
        arms["Rq"]["correct"] = 37
        self.assertEqual(score.choose(arms)[0], "X1q")

    def test_refuted_and_no_stock(self):
        with tempfile.TemporaryDirectory() as d:
            sp = self.spec(d, {"B2q": ([10.0] * 8, 40, 1, 2), "X1q": ([15.0] * 8, 40, 1, 1)})
            self.assertEqual(score.score(sp)["verdict"], "REFUTED")
            sp = self.spec(d, {"B2q": (None, None, 1, 2), "Sq": ([15.0] * 8, 40, 1, 1)})
            r = score.score(sp)
            self.assertEqual((r["gate_ref"], r["verdict"]), ("Sq", "INCONCLUSIVE"))
            self.assertEqual(r["chosen"], "Sq")

    def test_two_card_arm_not_eligible(self):
        with tempfile.TemporaryDirectory() as d:
            sp = self.spec(d, {"B2q": ([5.0] * 8, 40, 1, 2), "Sq": ([30.0] * 8, 40, 1, 2)})
            self.assertEqual(score.score(sp)["verdict"], "INCONCLUSIVE")


class Grader(unittest.TestCase):
    def test_grade(self):
        self.assertEqual(gsm8k.gold("blah\n#### 1,234"), "1234")
        self.assertEqual(gsm8k.last_number("so 3 apples.\nAnswer: 1,234."), "1234")
        self.assertTrue(gsm8k.same("18.0", "18"))
        self.assertFalse(gsm8k.same(None, "18"))
        self.assertEqual(gsm8k.last_number("Answer: -7"), "-7")
        self.assertIsNone(gsm8k.last_number("no digits"))


try:
    import yaml  # noqa: F401
    import ladder
except ImportError:  # PyYAML missing: the ladder test is skipped, the rest still run
    ladder = None


@unittest.skipIf(ladder is None, "PyYAML not installed")
class Ladder(unittest.TestCase):
    def doc(self):
        return [{"kind": "ConfigMap"}, {"kind": "Deployment", "spec": {"template": {"spec": {
            "volumes": [{"name": "model-volume", "hostPath": {"path": "/models/hf"}},
                        {"name": "host-gpu-libs", "hostPath": {"path": "/usr/lib/x86_64-linux-gnu"}},
                        {"name": "host-opencl-vendors", "hostPath": {"path": "/etc/OpenCL/vendors"}}],
            "containers": [{
                "command": ["bash", "-c", "source /opt/intel/oneapi/setvars.sh --force && export "
                            "LD_LIBRARY_PATH=/host-gpu-libs:${LD_LIBRARY_PATH} && python3 -m x \"$@\"", "--"],
                "args": ["--disable-log-requests", "--port", "8000"],
                "volumeMounts": [{"name": "model-volume"}, {"name": "host-gpu-libs"},
                                 {"name": "host-opencl-vendors"}]}]}}}}]

    def dep(self, docs):
        return docs[1]["spec"]["template"]["spec"]

    def test_rung_a(self):
        ps = self.dep(ladder.apply(self.doc(), "a"))
        self.assertEqual([v["name"] for v in ps["volumes"]], ["model-volume"])
        c = ps["containers"][0]
        self.assertEqual([m["name"] for m in c["volumeMounts"]], ["model-volume"])
        self.assertNotIn("LD_LIBRARY_PATH", c["command"][2])
        self.assertIn("setvars.sh", c["command"][2])
        self.assertIn("--disable-log-requests", c["args"])

    def test_rung_d(self):
        c = self.dep(ladder.apply(self.doc(), "d"))["containers"][0]
        self.assertNotIn("setvars.sh", c["command"][2])
        self.assertEqual(c["args"], ["--port", "8000", "--max-model-len", "32768"])

    def test_main_drops_empty_docs(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "in.yaml"), os.path.join(d, "out.yaml")
            with open(src, "w") as f:
                f.write("---\n" + yaml.safe_dump_all(self.doc()) + "---\n")
            ladder.main(["a", src, dst])
            text = open(dst).read()
        self.assertNotIn("null", text)
        self.assertEqual(len([x for x in yaml.safe_load_all(text)]), 2)

    def test_bad_rung(self):
        with self.assertRaises(SystemExit):
            ladder.apply(self.doc(), "e")


class CardParse(unittest.TestCase):
    """lib.sh's card_free parsers on crescent's real `xpu-smi ps` and `fuser -v` output."""
    XPU = """
  PID         Command                   DeviceID     SHR          MEM
  3900003     sglang::scheduler_TP0     0            15496768     17114748
  3900352     sglang::scheduler_TP1     1            15496768     17097060
"""
    FUSER_MASKED = """                     USER        PID ACCESS COMMAND
/dev/dri/renderD130: root      3899938 F.... sglang::schedul
                     root      3900003 F.... sglang::schedul
"""
    FUSER_MAPPED = FUSER_MASKED + "                     root      3898661 F...m python\n"

    def sh(self, fn, stdin):
        lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib.sh")
        r = subprocess.run(["bash", "-c", f'OUT=/tmp . "{lib}"; {fn}'], input=stdin, text=True)
        return r.returncode == 0

    def test_xpu_listed(self):
        self.assertTrue(self.sh("xpu_listed 0", self.XPU))
        self.assertTrue(self.sh("xpu_listed 1", self.XPU))
        self.assertFalse(self.sh("xpu_listed 2", self.XPU))
        self.assertFalse(self.sh("xpu_listed 3", "\n  No data\n"))

    def test_fuser_mapped(self):
        self.assertFalse(self.sh("fuser_mapped", self.FUSER_MASKED))
        self.assertTrue(self.sh("fuser_mapped", self.FUSER_MAPPED))
        self.assertFalse(self.sh("fuser_mapped", ""))


if __name__ == "__main__":
    unittest.main()
