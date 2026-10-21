#!/usr/bin/env python3
"""Tests for bench/h57d (EXPERIMENTS.md "H57d amendment R1"): the arm table and renders, Od's
read-only transform, the P-core sampler's arithmetic, the load gate, and every H57da and H57dm
verdict branch on synthetic hold records. No cluster, no network."""
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import h57d  # noqa: E402

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


class Arms(unittest.TestCase):
    def test_llamacpp_flags(self):
        a = h57d.llamacpp_args(False)
        self.assertEqual(a[a.index("-t") + 1], "8")
        self.assertEqual(a[a.index("-C") + 1], "0x5555")
        self.assertEqual(a[a.index("--cpu-strict") + 1], "1")
        self.assertIn("--jinja", a)
        self.assertNotIn("--spec-type", a)
        m = h57d.llamacpp_args(True)
        self.assertEqual(m[m.index("--spec-type") + 1], "draft-mtp")
        self.assertTrue(m[m.index("-md") + 1].endswith("qat-assistant-q4_0.gguf"))
        self.assertNotIn("--spec-draft-n-max", m)   # the default draft length

    def test_mask_is_one_thread_per_physical_pcore(self):
        cpus = [i for i in range(32) if int(h57d.MASK, 16) >> i & 1]
        self.assertEqual(cpus, list(range(0, 16, 2)))

    def test_orders_rotate(self):
        o = h57d.ORDERS
        for k in (1, 2, 3, 4):
            self.assertEqual(sorted(o[k]), sorted(h57d.ARMS))
        self.assertEqual({o[k][0] for k in (1, 2, 3)}, set(h57d.ARMS))   # each arm first once
        self.assertEqual({o[k][2] for k in (1, 2, 3)}, set(h57d.ARMS))   # and last once

    @unittest.skipUnless(HAVE_YAML, "PyYAML")
    def test_render_pcores(self):
        for arm in ("Ld", "Ld-mtp"):
            ns, dep, svc = h57d.render(arm)
            t = dep["spec"]["template"]
            self.assertEqual(t["metadata"]["labels"]["k3d-gpu.io/cores"], "performance")
            c = t["spec"]["containers"][0]
            self.assertEqual(c["resources"]["requests"]["cpu"], "16")
            self.assertTrue(c["volumeMounts"][0]["readOnly"])
            self.assertEqual(t["spec"]["volumes"][0]["hostPath"]["path"], "/var/apb-models")
            self.assertEqual(c["image"], h57d.LLAMACPP)
            self.assertEqual(svc["metadata"]["name"], h57d.ARMS[arm]["svc"])
        with self.assertRaises(SystemExit):
            h57d.render("Od")

    def test_od_readonly(self):
        docs = [{"kind": "Namespace", "metadata": {"name": "k3d-gpu-agent"}},
                {"kind": "Deployment", "spec": {"template": {"metadata": {"labels": {"app": "ovms", "k3d-gpu.io/cores": "performance"}},
                 "spec": {"initContainers": [{"name": "pull"}],
                          "containers": [{"name": "ovms", "image": "img", "args": ["--rest_port=8000", "--model_path=/models/x", "--cache_dir=/models/.ov_cache"],
                                          "resources": {"requests": {"cpu": "16"}},
                                          "volumeMounts": [{"name": "models", "mountPath": "/models"}]}],
                          "volumes": [{"name": "models", "hostPath": {"path": "/var/apb-models"}}]}}}}]
        out = h57d.od_readonly(docs)
        ps = out[1]["spec"]["template"]["spec"]
        self.assertNotIn("initContainers", ps)
        c = ps["containers"][0]
        self.assertEqual(c["args"], ["--rest_port=8000", "--model_path=/models/x", "--cache_dir=/ov-cache"])
        self.assertTrue([m for m in c["volumeMounts"] if m["name"] == "models"][0]["readOnly"])
        self.assertIn({"name": "ov-cache", "emptyDir": {}}, ps["volumes"])
        self.assertEqual(c["resources"], {"requests": {"cpu": "16"}})
        self.assertEqual(out[1]["spec"]["template"]["metadata"]["labels"]["k3d-gpu.io/cores"], "performance")
        self.assertIn("initContainers", docs[1]["spec"]["template"]["spec"])   # input untouched

    def test_cli_arms(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "h57d.py"), "field", "Od", "model"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), h57d.OVB)


class Sampler(unittest.TestCase):
    def test_cpu_ticks(self):
        stat = "cpu  9 9 9 9 9 9 9 9 0 0\ncpu0 10 0 5 80 5 0 0 0 0 0\ncpu1 20 0 0 70 10 0 0 0 0 0\ncpu16 50 0 0 50 0 0 0 0 0 0\n"
        busy, total = h57d.cpu_ticks(stat, [0, 1])
        self.assertEqual((busy, total), (35, 200))

    def test_foreign(self):
        s = [{"ts": t, "seconds": 5, "pcore_busy": 10.0, "node_cpu": 7.0} for t in range(5, 60, 5)]
        self.assertAlmostEqual(h57d.foreign(s, 10, 30), 3.0)
        self.assertIsNone(h57d.foreign(s, 100, 120))
        s2 = [{"ts": 5, "seconds": 5, "pcore_busy": 2.0, "node_cpu": 7.0}]
        self.assertEqual(h57d.foreign(s2, 0, 5), 0.0)   # never below 0

    def test_load_gate(self):
        self.assertTrue(h57d.load_gate({"a": 3.0, "b": 4.5, "c": 4.9})["matched"])    # spread 1.9 <= 2.0
        self.assertFalse(h57d.load_gate({"a": 3.0, "b": 5.5})["matched"])             # 2.5 > 2.0
        self.assertTrue(h57d.load_gate({"a": 10.0, "b": 12.5})["matched"])            # 2.5 <= 25% of 11.25
        self.assertFalse(h57d.load_gate({"a": 3.0})["matched"])


def write_holds(root, decode, quality, foreign=None, missing=()):
    """decode: {arm: tok/s centre}; quality: {arm: correct of 50}; foreign: {arm: CPU-s/s}."""
    rng = random.Random(7)
    foreign = foreign or {a: 3.0 for a in decode}
    for k in (1, 2, 3, 4):
        hd = os.path.join(root, f"hold{k}")
        os.makedirs(hd)
        t = 1000.0 * k
        samples = []
        for arm in h57d.ORDERS[k]:
            ad = os.path.join(hd, arm)
            os.makedirs(ad)
            if (k, arm) in missing:
                json.dump({"status": "not_deliverable"}, open(os.path.join(ad, "status.json"), "w"))
                continue
            json.dump({"status": "measured"}, open(os.path.join(ad, "status.json"), "w"))
            if k == 4:
                json.dump({"correct": quality[arm], "n": 50, "score": quality[arm] / 50},
                          open(os.path.join(ad, "quality.json"), "w"))
                continue
            s = [{"ok": True, "decode_tokens_per_s": decode[arm] * (1 + rng.uniform(-0.03, 0.03))} for _ in range(8)]
            json.dump({"phases": [{"samples": s}]}, open(os.path.join(ad, "load-n1.json"), "w"))
            with open(os.path.join(ad, "phases.jsonl"), "w") as f:
                f.write(json.dumps({"phase": "n1", "t0": t, "t1": t + 100}) + "\n")
            for ts in range(int(t) + 5, int(t) + 101, 5):
                samples.append({"ts": ts, "seconds": 5, "pcore_busy": 8.0 + foreign[arm], "node_cpu": 8.0})
            t += 200
        with open(os.path.join(hd, "load.jsonl"), "w") as f:
            for s in samples:
                f.write(json.dumps(s) + "\n")


class Score(unittest.TestCase):
    Q = {"Od": 44, "Ld": 45, "Ld-mtp": 45}

    def run_score(self, decode, quality=None, **kw):
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, decode, quality or self.Q, **kw)
            return h57d.score(d)

    def test_refuted_llamacpp_faster(self):
        r = self.run_score({"Od": 10.0, "Ld": 15.0, "Ld-mtp": 14.0})
        self.assertEqual(r["H57da"]["verdict"], "REFUTED")
        self.assertEqual(r["H57da"]["x"], "Ld")
        self.assertGreater(r["H57da"]["ci"][0], 1.05)
        self.assertEqual(r["H57dm"]["verdict"], "REFUTED")   # 14/15, CI high < 1.20

    def test_supported_od_fastest(self):
        r = self.run_score({"Od": 15.0, "Ld": 12.0, "Ld-mtp": 11.0})
        self.assertEqual(r["H57da"]["verdict"], "SUPPORTED")

    def test_inconclusive_near_threshold(self):
        r = self.run_score({"Od": 10.0, "Ld": 10.5, "Ld-mtp": 9.0})
        self.assertEqual(r["H57da"]["verdict"], "INCONCLUSIVE")

    def test_od_fails_gate(self):
        r = self.run_score({"Od": 20.0, "Ld": 10.0, "Ld-mtp": 10.0}, quality={"Od": 30, "Ld": 45, "Ld-mtp": 45})
        self.assertEqual(r["H57da"]["verdict"], "REFUTED")
        self.assertIn("gate", r["H57da"]["reason"])

    def test_gate_is_best_minus_five_items(self):
        r = self.run_score({"Od": 10.0, "Ld": 15.0, "Ld-mtp": 14.0}, quality={"Od": 40, "Ld": 45, "Ld-mtp": 39})
        self.assertTrue(r["gate"]["Od"])          # 40 >= 45 - 5
        self.assertFalse(r["gate"]["Ld-mtp"])     # 39 < 40
        self.assertEqual(r["H57dm"]["verdict"], "REFUTED")
        self.assertIn("gate", r["H57dm"]["reason"])

    def test_mtp_supported(self):
        r = self.run_score({"Od": 10.0, "Ld": 12.0, "Ld-mtp": 16.0})
        self.assertEqual(r["H57dm"]["verdict"], "SUPPORTED")
        self.assertEqual(r["H57da"]["x"], "Ld-mtp")

    def test_mtp_inconclusive(self):
        # 1.33x in holds 1 and 2, slower in hold 3: pooled median >= 1.20, not faster in every hold.
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"Od": 10.0, "Ld": 12.0, "Ld-mtp": 16.0}, self.Q)
            p = os.path.join(d, "hold3", "Ld-mtp", "load-n1.json")
            j = json.load(open(p))
            for s in j["phases"][0]["samples"]:
                s["decode_tokens_per_s"] = 11.0
            json.dump(j, open(p, "w"))
            r = h57d.score(d)
        self.assertGreaterEqual(r["H57dm"]["ci"][1], 1.20)
        self.assertFalse(r["H57dm"]["all_holds_faster"])
        self.assertEqual(r["H57dm"]["verdict"], "INCONCLUSIVE")

    def test_od_not_delivered(self):
        r = self.run_score({"Od": 10.0, "Ld": 15.0, "Ld-mtp": 14.0}, missing={(2, "Od")})
        self.assertEqual(r["H57da"]["verdict"], "INCONCLUSIVE")
        self.assertEqual(r["H57dm"]["verdict"], "REFUTED")

    def test_load_unmatched(self):
        r = self.run_score({"Od": 10.0, "Ld": 15.0, "Ld-mtp": 14.0}, foreign={"Od": 1.0, "Ld": 5.0, "Ld-mtp": 1.0})
        self.assertEqual(r["H57da"]["verdict"], "INCONCLUSIVE")
        self.assertIn("load unmatched", r["H57da"]["reason"])
        self.assertEqual(r["H57dm"]["verdict"], "INCONCLUSIVE")

    def test_spare_hold_replaces(self):
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"Od": 10.0, "Ld": 15.0, "Ld-mtp": 14.0}, self.Q, missing={(2, "Od")})
            spare = os.path.join(d, "hold5")
            src = os.path.join(d, "hold1")
            subprocess.run(["cp", "-r", src, spare], check=True)
            open(os.path.join(spare, "repeats"), "w").write("2\n")
            r = h57d.score(d)
        self.assertEqual(r["arms"]["Od"]["holds_measured"], 3)
        self.assertEqual(r["H57da"]["verdict"], "REFUTED")

    def test_not_faster_in_every_hold(self):
        with tempfile.TemporaryDirectory() as d:
            write_holds(d, {"Od": 10.0, "Ld": 15.0, "Ld-mtp": 14.0}, self.Q)
            # hold 3: Ld slower than Od
            p = os.path.join(d, "hold3", "Ld", "load-n1.json")
            j = json.load(open(p))
            for s in j["phases"][0]["samples"]:
                s["decode_tokens_per_s"] = 8.0
            json.dump(j, open(p, "w"))
            r = h57d.score(d)
        self.assertFalse(r["H57da"]["all_holds_faster"])
        self.assertNotEqual(r["H57da"]["verdict"], "REFUTED")


if __name__ == "__main__":
    unittest.main()
