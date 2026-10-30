#!/usr/bin/env python3
"""Tests for bench/h54 (EXPERIMENTS.md "H54 amendment R1"): the arm renders and their flags, the
GSM8K grader, the void rule, the tensor-table byte counts, the bootstrap, the chosen-arm source
rule and the H54a verdicts; and H54q's runner (amendments Q1 and Q2): its renders, item records,
hold plan, item client (against fakes and a local stub server) and scorer. No cluster, no network
beyond 127.0.0.1."""
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

    def test_idle_window_r4(self):
        # Amendment R4: the arm's own load raises the idle SGLang pair from ~2.4 to ~4.7 CPU-s/s
        # (hold 1); with an idle window the CPU part is read there, the phase value only reported.
        induced = [self.s(t, 2.4 if t <= 60 else 4.7) for t in range(10, 300, 10)]
        self.assertTrue(h54.void(induced, 100, 200)[0])                  # R1's rule
        v, why = h54.void(induced, 100, 200, w0=0, w1=60)
        self.assertFalse(v)
        self.assertIn("idle window 2.40", why)
        self.assertIn("phase 4.70", why)
        busy_idle = [self.s(t, 5.0) for t in range(10, 300, 10)]
        self.assertTrue(h54.void(busy_idle, 100, 200, w0=0, w1=60)[0])
        ai = [self.s(t, 1.0, 1 if t == 150 else 0) for t in range(10, 300, 10)]
        self.assertEqual(h54.void(ai, 100, 200, w0=0, w1=60), (True, "aiperf ran"))
        self.assertEqual(h54.void(induced, 100, 200, w0=1000, w1=1060)[1], "no serving sample in the idle window")

    def test_cli_window(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "s.jsonl")
            with open(f, "w") as fh:
                for t in range(10, 300, 10):
                    fh.write(json.dumps(self.s(t, 2.4 if t <= 60 else 4.7)) + "\n")
            run = lambda *a: subprocess.run([sys.executable, os.path.join(HERE, "h54.py"), "void", f, *a], capture_output=True)  # noqa: E731
            self.assertEqual(run("100", "200").returncode, 1)
            self.assertEqual(run("100", "200", "0", "60").returncode, 0)

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
class ServedCarry(unittest.TestCase):
    """Amendment R3: an arm whose server stopped answering after readiness is failed, and is
    carried to the next hold's front once."""
    def arm(self, root, hold, name, status="measured", reason="", n1_ok=True, q_errors=0):
        d = os.path.join(root, f"hold{hold}", name)
        os.makedirs(d, exist_ok=True)
        json.dump({"status": status, "reason": reason}, open(os.path.join(d, "status.json"), "w"))
        if status == "measured":
            s = [{"ok": True, "decode_tokens_per_s": 10.0}] if n1_ok else [{"ok": False, "decode_tokens_per_s": None}]
            json.dump({"phases": [{"samples": s}]}, open(os.path.join(d, "load-n1.json"), "w"))
            json.dump({"n": 50, "correct": 40, "score": 0.8, "errors": q_errors}, open(os.path.join(d, "quality.json"), "w"))
        return d

    def test_served(self):
        with tempfile.TemporaryDirectory() as r:
            self.assertTrue(h54.served(self.arm(r, 1, "Lg"))[0])
            ok, why = h54.served(self.arm(r, 1, "B1g", n1_ok=False))
            self.assertFalse(ok)
            self.assertTrue(why.startswith(h54.STOPPED))
            self.assertFalse(h54.served(self.arm(r, 1, "Vq", q_errors=50))[0])
            self.assertTrue(h54.served(self.arm(r, 1, "Sg", q_errors=3))[0])
            self.assertFalse(h54.served(os.path.join(r, "nothing"))[0])

    def test_recheck_and_carry(self):
        with tempfile.TemporaryDirectory() as r:
            self.arm(r, 1, "B1g", n1_ok=False)                 # engine died after readiness
            self.arm(r, 1, "Lg")
            self.arm(r, 1, "A1g", status="not_reached", reason="680 s of the hold left (< 900)")
            self.arm(r, 1, "A1o", status="not_deliverable", reason="no model list")
            changed = h54.recheck(r)
            self.assertEqual([os.path.basename(d) for d in changed], ["B1g"])
            st = json.load(open(os.path.join(r, "hold1", "B1g", "status.json")))
            self.assertEqual((st["status"], st["was"]), ("failed", "measured"))
            self.assertEqual(h54.recheck(r), [])               # idempotent
            self.assertEqual(h54.carry(r, 1, ["B1g", "Lg", "Lg-mtp", "A1g", "A1o"]), ["B1g", "Lg-mtp", "A1g"])
            # a second stop in hold 2 is not carried again; a render failure is never carried
            self.arm(r, 2, "B1g", status="failed", reason=h54.STOPPED + ": the N=1 phase completed no request")
            self.arm(r, 2, "Vq", status="failed", reason="render failed")
            self.assertEqual(h54.carry(r, 2, ["B1g", "Vq", "Vq-mtp"]), ["Vq-mtp"])

    def test_carry_n1_void_r4(self):
        with tempfile.TemporaryDirectory() as r:
            for h in (1, 2):
                d = self.arm(r, h, "Lg")
                json.dump({"n1": {"void": True, "rerun_void": True}}, open(os.path.join(d, "phases.json"), "w"))
            d = self.arm(r, 1, "Lg-mtp")
            json.dump({"n1": {"void": True, "rerun_void": False}}, open(os.path.join(d, "phases.json"), "w"))
            self.assertEqual(h54.carry(r, 1, ["Lg", "Lg-mtp"]), ["Lg"])   # re-run not void: kept
            self.assertEqual(h54.carry(r, 2, ["Lg"]), [])                 # once per arm

    def test_score_ignores_failed(self):
        with tempfile.TemporaryDirectory() as r:
            self.arm(r, 1, "B1g", n1_ok=False)
            h54.recheck(r)
            self.assertEqual(h54.score_a(r)["verdict"], "INCONCLUSIVE")


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
        self.assertEqual(spec, {"method": "gemma4_mtp", "model": f"/data/{h54.DRAFTER}",
                               "num_speculative_tokens": 1})   # vLLM's n_predict default, named (R2)
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


# ---- H54q (amendments Q1 and Q2) ----------------------------------------------------------------

def q_row(seq, idx, ok=True, error=None, prior=0):
    return {"seq": seq, "idx": idx, "ok": ok and error is None, "gold": "1", "reply": "", "usage": {},
            "error": error, "prior_errors": prior, "t0": 0, "t1": 1, "s": 1}


def q_write(root, hold, arm, rows, status="measured", reason=""):
    d = os.path.join(root, f"hold{hold}", arm)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "items.jsonl"), "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    with open(os.path.join(d, "status.json"), "w") as f:
        json.dump({"status": status, "reason": reason}, f)
    return d


def fake_h54a(root, correct=None):
    """H54a records for the three imported arms: items 0-49, all correct unless listed wrong."""
    for arm in ("OVQ", "OVB", "Lg"):
        d = os.path.join(root, h54.Q_ARMS[arm]["h54a"])
        os.makedirs(d, exist_ok=True)
        wrong = (correct or {}).get(arm, set())
        with open(os.path.join(d, "quality.json"), "w") as f:
            json.dump({"rows": [{"idx": i, "ok": i not in wrong, "error": None} for i in range(50)]}, f)


def q_full(root, arm, wrong=(), hold=1):
    first = h54.Q_ARMS[arm]["first"]
    q_write(root, hold, arm, [q_row(n, i, ok=i not in wrong) for n, i in enumerate(range(first, h54.Q_N), 1)])


class QRecords(unittest.TestCase):
    def test_h54a_items_are_score_a_records(self):
        # Q1 item 2: OVQ = A1g hold 3 (49/50), OVB = A1o hold 3 (49/50), Lg = Lg hold 2 (50/50).
        with tempfile.TemporaryDirectory() as d:
            arms = h54.score_q(d)["arms"]
        self.assertEqual({a: (arms[a]["answered"], arms[a]["correct"], arms[a]["from_h54a"]) for a in ("OVQ", "OVB", "Lg")},
                         {"OVQ": (50, 49, 50), "OVB": (50, 49, 50), "Lg": (50, 50, 50)})
        self.assertEqual(arms["R"]["answered"], 0)
        self.assertEqual(arms["Vi"]["answered"], 0)

    @unittest.skipUnless(HAVE_YAML, "PyYAML")
    def test_renders_are_h54as(self):
        import yaml
        with tempfile.TemporaryDirectory() as d:
            for arm, rec in (("OVQ", "hold3/A1g"), ("OVB", "hold3/A1o"), ("Lg", "hold2/Lg")):
                out = os.path.join(d, arm + ".yaml")
                h54.q_render(arm, out)
                with open(out, "rb") as a, open(os.path.join(h54.H54A, rec, "deploy.yaml"), "rb") as b:
                    self.assertEqual(hashlib.sha256(a.read()).hexdigest(), hashlib.sha256(b.read()).hexdigest())
            with open(os.path.join(h54.H54A, "hold2/B1g/deploy.yaml")) as f:
                b1g = [x for x in yaml.safe_load_all(f) if x]
            for arm, base in (("R", "Vq"), ("Vi", "Vi")):
                out = os.path.join(d, arm + ".yaml")
                h54.q_render(arm, out)
                with open(out) as f:
                    got = [x for x in yaml.safe_load_all(f) if x]
                self.assertEqual(got, h54.derive_vllm(base, b1g))
                c = [x for x in got if x["kind"] == "Deployment"][0]["spec"]["template"]["spec"]["containers"][0]
                self.assertEqual(c["image"], h54.IMG["vllm032"])
                model = c["args"][c["args"].index("--model") + 1]
                self.assertEqual(model, f"/data/{h54.AUTOROUND if arm == 'Vi' else h54.QAT}")
                self.assertNotIn("--speculative-config", c["args"])

    def test_bases(self):
        self.assertEqual({a: h54.Q_ARMS[a]["base"] for a in h54.Q_ORDER},
                         {"R": "Vq", "OVQ": "A1g", "OVB": "A1o", "Lg": "Lg", "Vi": "Vi"})
        self.assertEqual(h54.Q_ORDER, ["R", "OVQ", "OVB", "Lg", "Vi"])
        for a in h54.Q_ORDER:
            self.assertIn(h54.Q_ARMS[a]["base"], h54.ARMS)
        self.assertEqual(h54.ARMS["Vq"]["boot"], 1500)   # R is a bf16 vLLM load


class QState(unittest.TestCase):
    def test_retry_first_second_error_wrong_and_discard(self):
        with tempfile.TemporaryDirectory() as r, tempfile.TemporaryDirectory() as a:
            fake_h54a(a)
            q_write(r, 1, "OVQ", [q_row(1, 50), q_row(2, 51, error="HTTPError"), q_row(3, 52, ok=False),
                                  q_row(4, 53, error="x"), q_row(5, 53, error="x", prior=1),
                                  q_row(6, 54, error="x"), {"event": "stopped", "discard": [6], "reason": "r"}])
            st = h54.q_state(r, "OVQ", a)
            self.assertTrue(st[50]["ok"])
            self.assertIsNone(st[51]["ok"])
            self.assertEqual(st[51]["errors"], 1)
            self.assertFalse(st[52]["ok"])
            self.assertFalse(st[53]["ok"])
            self.assertTrue(st[53]["final_error"])
            self.assertNotIn(54, st)                       # discarded by the stopped event
            todo = h54.q_todo(r, "OVQ", a)
            self.assertEqual(todo[0], (51, 1))             # errored once: front of the next turn
            self.assertEqual(todo[1], (54, 0))
            self.assertEqual(len(todo), 150 - 3)
            # hold 2 answers 51 on its second ask: the answer counts, not the error
            q_write(r, 2, "OVQ", [q_row(1, 51, ok=True, prior=1)])
            self.assertTrue(h54.q_state(r, "OVQ", a)[51]["ok"])

    def test_first_answer_wins_and_range(self):
        with tempfile.TemporaryDirectory() as r, tempfile.TemporaryDirectory() as a:
            fake_h54a(a)
            q_write(r, 1, "OVQ", [q_row(1, 60, ok=False), q_row(2, 7, ok=False)])   # 7 is H54a's
            q_write(r, 2, "OVQ", [q_row(1, 60, ok=True)])
            st = h54.q_state(r, "OVQ", a)
            self.assertFalse(st[60]["ok"])
            self.assertTrue(st[7]["ok"])
            self.assertEqual(st[7]["src"], "H54a/hold3/A1g")
            self.assertEqual(h54.q_todo(r, "R", a)[0], (0, 0))   # R answers 0-199

    def test_cut_line_is_unanswered(self):
        with tempfile.TemporaryDirectory() as r, tempfile.TemporaryDirectory() as a:
            fake_h54a(a)
            d = q_write(r, 1, "Lg", [q_row(1, 50)])
            with open(os.path.join(d, "items.jsonl"), "a") as f:
                f.write('{"seq": 2, "idx": 51, "ok": tr')        # killed mid-write
            self.assertEqual(h54.q_todo(r, "Lg", a)[0], (51, 0))


class QPlan(unittest.TestCase):
    def test_order_done_and_lost(self):
        with tempfile.TemporaryDirectory() as r, tempfile.TemporaryDirectory() as a:
            fake_h54a(a)
            self.assertEqual(h54.q_plan(r, a), ["R", "OVQ", "OVB", "Lg", "Vi"])
            q_full(r, "R")
            q_write(r, 1, "OVQ", [q_row(1, 50)])
            self.assertEqual(h54.q_plan(r, a), ["OVQ", "OVB", "Lg", "Vi"])
            # not deliverable or stopped once: carried; twice: dropped (R3 carries once)
            q_write(r, 1, "OVB", [], status="failed", reason="server stopped answering: x")
            self.assertIn("OVB", h54.q_plan(r, a))
            q_write(r, 2, "OVB", [], status="not_deliverable")
            self.assertNotIn("OVB", h54.q_plan(r, a))
            # not reached costs nothing
            q_write(r, 1, "Lg", [], status="not_reached")
            q_write(r, 2, "Lg", [], status="not_reached")
            self.assertIn("Lg", h54.q_plan(r, a))
            # the probe-first Vi gets one boot attempt
            q_write(r, 2, "Vi", [], status="not_deliverable")
            self.assertEqual(h54.q_plan(r, a), ["OVQ", "Lg"])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as r:
            run = lambda *a: subprocess.run([sys.executable, os.path.join(HERE, "h54.py"), *a], capture_output=True, text=True)  # noqa: E731
            self.assertEqual(run("q-plan", r).stdout.split(), ["R", "OVQ", "OVB", "Lg", "Vi"])
            self.assertEqual(run("q-base", "OVB").stdout.strip(), "A1o")
            self.assertEqual(run("q-todo", r, "OVQ").stdout.split()[:2], ["50", "51"])


def item(i):
    return {"idx": i, "question": f"q{i}", "answer": f"#### {i}"}


class QAsk(unittest.TestCase):
    def ask_with(self, todo, ask, probe=lambda t: True, stop=None, **kw):
        import time
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "items.jsonl")
            s = h54.q_ask(todo, out, stop or time.time() + 60, ask, probe, tick=0.01, **kw)
            rows, discard = h54.q_rows(out)
        return s, rows, discard

    def test_answers_and_one_retry(self):
        seen = {}

        def ask(it, t):
            seen[it["idx"]] = seen.get(it["idx"], 0) + 1
            if it["idx"] == 3 and seen[3] == 1:
                raise OSError("reset")
            if it["idx"] == 4:
                raise OSError("always")
            return f"Answer: {it['idx'] if it['idx'] != 2 else 99}", {"completion_tokens": 5}
        s, rows, _ = self.ask_with([(item(i), 0) for i in range(5)] + [(item(5), 1)], ask, workers=2)
        self.assertEqual(seen, {0: 1, 1: 1, 2: 1, 3: 2, 4: 2, 5: 1})   # an error is asked once more
        self.assertIsNone(s["stopped"])
        self.assertEqual((s["answered"], s["errors"], s["cut"], s["not_asked"]), (5, 3, 0, 0))
        by = {}
        for r in rows:
            by.setdefault(r["idx"], []).append(r)
        self.assertEqual([r["ok"] for r in by[2]], [False])
        self.assertEqual([bool(r["error"]) for r in by[3]], [True, False])
        self.assertEqual([r["prior_errors"] for r in by[4]], [0, 1])
        self.assertEqual(len(by[5]), 1)                     # errored before: its retry is this ask

    def test_streak_stops_and_discards(self):
        def ask(it, t):
            raise ConnectionRefusedError("down")
        probes = []

        def probe(t):
            probes.append(t)
            return False
        s, rows, discard = self.ask_with([(item(i), 0) for i in range(40)], ask, probe=probe, workers=4, streak_max=4)
        self.assertTrue(s["stopped"].startswith(h54.STOPPED))
        self.assertEqual(len(probes), 1)
        self.assertTrue(30 <= probes[0] <= 300)            # bounded by probe_s and by the stop
        self.assertEqual(discard, {r["seq"] for r in rows})   # every error of the run is discarded
        self.assertGreater(s["not_asked"], 0)

    def test_no_probe_near_the_item_stop(self):
        import time

        def ask(it, t):
            raise ConnectionRefusedError("down")
        probes = []
        s, rows, discard = self.ask_with([(item(i), 1) for i in range(8)], ask, probe=lambda t: probes.append(t),
                                         stop=time.time() + 20, workers=4, streak_max=4)
        self.assertEqual(probes, [])                       # under 30 s before the stop: no probe
        self.assertIsNone(s["stopped"])
        self.assertEqual((s["errors"], discard), (8, set()))

    def test_streak_with_answering_server_continues(self):
        def ask(it, t):
            if it["idx"] < 8:
                raise OSError("bad request")
            return f"Answer: {it['idx']}", {}
        s, rows, discard = self.ask_with([(item(i), 1) for i in range(8)] + [(item(i), 0) for i in range(8, 12)],
                                         ask, workers=4, streak_max=4)
        self.assertIsNone(s["stopped"])
        self.assertEqual(discard, set())
        self.assertEqual((s["answered"], s["errors"]), (4, 8))

    def test_trailing_error_probe(self):
        def ask(it, t):
            if it["idx"] == 2:
                raise OSError("down")
            return "Answer: 1", {}
        s, _, discard = self.ask_with([(item(i), 0) for i in range(3)], ask, probe=lambda t: False, workers=1)
        self.assertIn("ended on an error", s["stopped"])
        self.assertEqual(len(discard), 2)                  # the item's ask and its retry

    def test_item_stop_cuts(self):
        import time

        def ask(it, t):
            time.sleep(min(t, 0.5) if it["idx"] else 0.0)
            return f"Answer: {it['idx']}", {}
        s, rows, _ = self.ask_with([(item(i), 0) for i in range(10)], ask, stop=time.time() + 0.3, workers=2)
        self.assertEqual(s["end"], "item stop")
        self.assertEqual(s["cut"], 2)
        self.assertEqual(s["not_asked"], 7)
        self.assertEqual([r["idx"] for r in rows], [0])

    def test_stall_probe(self):
        import time

        def ask(it, t):
            time.sleep(2)
            return "x", {}
        s, rows, _ = self.ask_with([(item(i), 0) for i in range(3)], ask, probe=lambda t: False, workers=3, stall_s=0.2)
        self.assertIn("no request finished", s["stopped"])
        self.assertEqual(rows, [])

    def test_against_a_stub_server(self):
        import http.server
        import threading

        class Stub(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, body):
                b = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self.reply({"data": [{"id": "gemma"}]})

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                q = req["messages"][0]["content"]
                assert req.get("chat_template_kwargs") == {"enable_thinking": False} or req["max_tokens"] == 16
                self.reply({"choices": [{"message": {"content": "Answer: 7" if "pens" in q else "Answer: 5"}}],
                            "usage": {"completion_tokens": 3}})
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        try:
            with tempfile.TemporaryDirectory() as d:
                self.assertTrue(h54.ready(url, "gemma"))
                p = subprocess.run([sys.executable, os.path.join(HERE, "h54.py"), "q-smoke", url, "gemma", d,
                                    str(__import__("time").time() + 60)], capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                rows, _ = h54.q_rows(os.path.join(d, "smoke-items.jsonl"))
                self.assertEqual(sorted((r["idx"], r["ok"]) for r in rows), [(1000, True), (1001, False)])
                res, out = os.path.join(d, "res"), os.path.join(d, "res", "hold1", "Lg")
                os.makedirs(out)
                p = subprocess.run([sys.executable, os.path.join(HERE, "h54.py"), "q-items", url, "gemma", res, "Lg", out,
                                    str(__import__("time").time() + 60)], capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                q = json.load(open(os.path.join(out, "q.json")))
                self.assertEqual((q["status"], q["todo"], q["answered"], q["first_items"][0]), ("measured", 150, 150, 50))
                self.assertEqual(h54.q_todo(res, "Lg"), [])
        finally:
            srv.shutdown()
        self.assertFalse(h54.ready("http://127.0.0.1:9/v1", "gemma", timeout=1))


class ScoreQ(unittest.TestCase):
    def score(self, wrong, h54a_wrong=None):
        with tempfile.TemporaryDirectory() as r, tempfile.TemporaryDirectory() as a:
            fake_h54a(a, h54a_wrong)
            for arm, w in wrong.items():
                q_full(r, arm, w)
            return h54.score_q(r, a, n=2000)

    def test_supported(self):
        # OVQ = R; OVB misses 20 items OVQ answers
        rep = self.score({"R": set(range(50, 200, 25)), "OVQ": set(range(50, 200, 25)), "OVB": set(range(51, 71))})
        self.assertEqual(rep["verdict"], "SUPPORTED", rep)
        self.assertEqual(rep["tests"]["OVQ-R"]["diff"], 0.0)
        self.assertLess(rep["tests"]["OVB-OVQ"]["ci95"][1], 0)

    def test_refuted_ovq_below_r(self):
        rep = self.score({"R": set(), "OVQ": set(range(60, 100)), "OVB": set(range(60, 100))})
        self.assertEqual(rep["verdict"], "REFUTED")
        self.assertIn("OVQ - R", rep["reason"])

    def test_refuted_ovb_above_ovq_with_one_test(self):
        # R did not answer every item: OVQ - R is missing, and the OVB - OVQ test alone refutes
        with tempfile.TemporaryDirectory() as r, tempfile.TemporaryDirectory() as a:
            fake_h54a(a)
            q_full(r, "OVQ", set(range(60, 90)))
            q_full(r, "OVB", set())
            q_write(r, 1, "R", [q_row(1, 0)])
            rep = h54.score_q(r, a, n=2000)
        self.assertIn("missing", rep["tests"]["OVQ-R"])
        self.assertEqual(rep["verdict"], "REFUTED")
        self.assertIn("OVB - OVQ", rep["reason"])

    def test_missing_is_inconclusive(self):
        rep = self.score({"OVQ": set(), "OVB": set(range(60, 90))})
        self.assertEqual(rep["verdict"], "INCONCLUSIVE")
        self.assertIn("OVQ-R", rep["reason"])
        self.assertEqual(rep["tests"]["OVQ-R"]["missing"], "R did not answer all 200 items")

    def test_neither(self):
        rep = self.score({"R": set(), "OVQ": set(), "OVB": set()})
        self.assertEqual(rep["verdict"], "INCONCLUSIVE")
        self.assertEqual(rep["tests"]["OVB-OVQ"]["ci95"], [0.0, 0.0])

    def test_imported_items_count(self):
        rep = self.score({"R": set(), "OVQ": set(), "OVB": set()}, h54a_wrong={"OVQ": {3}})
        self.assertEqual(rep["arms"]["OVQ"]["correct"], 199)
        self.assertEqual(rep["tests"]["OVQ-R"]["b_only"], 1)

    def test_paired_boot(self):
        d = [1] * 10 + [0] * 180 + [-1] * 10
        self.assertEqual(h54.paired_boot(d, 2000), h54.paired_boot(d, 2000))
        lo, hi = h54.paired_boot(d, 2000)
        self.assertLess(lo, 0)
        self.assertGreater(hi, 0)
        self.assertEqual(h54.paired_boot([0.5] * 7, 100), (0.5, 0.5))


if __name__ == "__main__":
    unittest.main()
