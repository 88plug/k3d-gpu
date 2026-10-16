#!/usr/bin/env python3
"""Unit + sim-integrity tests for scripts/k3d-gpu-numerics. No cluster, no hardware.

Usage: python3 tests/numerics_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numerics_sim as sim  # noqa: E402  (also loads the resolver module)

nx = sim.nx
TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "k3d-gpu-numerics")
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "llama-perplexity-kld.synthetic.txt")

D1, D2 = "sha256:" + "a" * 64, "sha256:" + "b" * 64


def rec(name, digest=D1, kl=0.001, top1=0.99, fmt="fp16", **kw):
    r = {"model": "m", "realisation": name, "format": fmt, "engine_digest": digest,
         "reference": "ref", "golden_set": "g", "n_tokens": 8192, "kl_method": "full",
         "teacher_forced": True, "kl_mean": kl, "top1": top1}
    r.update(kw)
    return r


def ev(*records):
    return {nx.evidence_key(r["model"], r["realisation"], r["engine_digest"]): r for r in records}


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def cand(name, fmt="fp16", digest=D1):
    return {"name": name, "format": fmt, "engine_digest": digest}


class Resolver(unittest.TestCase):
    def test_places_first_qualifying_in_preference_order(self):
        e = ev(rec("npu", kl=0.05, top1=0.9, fmt="int4"), rec("gpu"))
        d = nx.resolve("bf16-equivalent", "m", [cand("npu", "int4"), cand("gpu")], e)
        self.assertEqual(d["placed"], "gpu")
        self.assertIn("tolerance: kl_mean", d["skipped"]["npu"])

    def test_stale_digest_rejected(self):
        d = nx.resolve("exact", "m", [cand("gpu", digest=D2)], ev(rec("gpu", digest=D1)))
        self.assertIsNone(d["placed"])
        self.assertIn("stale", d["skipped"]["gpu"])

    def test_current_digest_record_used_when_older_also_present(self):
        e = ev(rec("gpu", digest=D2, kl=0.5), rec("gpu", digest=D1))
        self.assertEqual(nx.resolve("exact", "m", [cand("gpu", digest=D1)], e)["placed"], "gpu")

    def test_record_filed_under_wrong_digest_key_rejected(self):
        e = {nx.evidence_key("m", "gpu", D2): rec("gpu", digest=D1)}
        d = nx.resolve("exact", "m", [cand("gpu", digest=D2)], e)
        self.assertIn("record engine_digest does not match", d["skipped"]["gpu"])

    def test_missing_evidence_rejected_with_reason(self):
        d = nx.resolve("draft-ok", "m", [cand("gpu")], {})
        self.assertIsNone(d["placed"])
        self.assertIn("missing", d["skipped"]["gpu"])
        self.assertIn("no realisation of m meets numerics contract", d["reason"])

    def test_top1_below_min(self):
        d = nx.resolve("exact", "m", [cand("gpu")], ev(rec("gpu", kl=0.001, top1=0.97)))
        self.assertIn("top1", d["skipped"]["gpu"])

    def test_not_teacher_forced_refused(self):
        d = nx.resolve("draft-ok", "m", [cand("gpu")], ev(rec("gpu", teacher_forced=False)))
        self.assertIn("teacher-forced", d["skipped"]["gpu"])

    def test_topk_kl_refused_unless_spec_accepts(self):
        e = ev(rec("ovms", kl_method="topk:20"))
        self.assertIn("kl_method", nx.resolve("exact", "m", [cand("ovms")], e)["skipped"]["ovms"])
        spec = json.dumps({"class": "exact", "kl_methods": ["full", "topk:20"]})
        self.assertEqual(nx.resolve(spec, "m", [cand("ovms")], e)["placed"], "ovms")

    def test_tool_parse_gate(self):
        spec = json.dumps({"class": "draft-ok", "min_tool_parse": 0.99})
        self.assertIn("record has none", nx.resolve(spec, "m", [cand("g")], ev(rec("g")))["skipped"]["g"])
        e = ev(rec("g", tool_parse_rate=0.95))
        self.assertIn("tool_parse_rate", nx.resolve(spec, "m", [cand("g")], e)["skipped"]["g"])
        e = ev(rec("g", tool_parse_rate=0.995))
        self.assertEqual(nx.resolve(spec, "m", [cand("g")], e)["placed"], "g")

    def test_thin_evidence_refused(self):
        d = nx.resolve("draft-ok", "m", [cand("g")], ev(rec("g", n_tokens=100)))
        self.assertIn("thin", d["skipped"]["g"])

    def test_format_mismatch_refused(self):
        d = nx.resolve("draft-ok", "m", [cand("g", "int4")], ev(rec("g", fmt="fp16")))
        self.assertIn("mismatch", d["skipped"]["g"])

    def test_evidence_digest_stamped_and_deterministic(self):
        r = rec("g")
        d = nx.resolve("exact", "m", [cand("g")], ev(r))
        self.assertEqual(d["annotations"][nx.ANNOTATION_EVIDENCE], nx.evidence_digest(dict(reversed(list(r.items())))))
        self.assertNotEqual(nx.evidence_digest(r), nx.evidence_digest(dict(r, kl_mean=0.0011)))

    def test_unknown_class_errors(self):
        with self.assertRaises(ValueError):
            nx.parse_spec("bf16ish")

    def test_evidence_key_is_configmap_safe(self):
        self.assertEqual(nx.evidence_key("m", "g", D1), "m.g.sha256-" + "a" * 64)
        with self.assertRaises(ValueError):
            nx.evidence_key("m/x", "g", D1)


class Cli(unittest.TestCase):
    def test_parse_llama_perplexity_kld(self):
        with open(FIXTURE) as f:
            m = nx.parse_kld_log(f.read())
        self.assertAlmostEqual(m["kl_mean"], 0.0313)
        self.assertAlmostEqual(m["top1"], 0.919)
        self.assertAlmostEqual(m["kl_p99"], 0.412345)
        with self.assertRaises(ValueError):
            nx.parse_kld_log("perplexity: 7.9")

    def test_ingest_configmap_resolve_roundtrip(self):
        with tempfile.TemporaryDirectory() as t:
            r = subprocess.run([sys.executable, TOOL, "ingest-kld", "--model", "m", "--realisation",
                                "cpu-q4km", "--format", "q4_k_m", "--engine-digest", D1, "--reference",
                                "ref", "--golden-set", "g", "--n-tokens", "8192", FIXTURE],
                               capture_output=True, text=True, check=True)
            rp = os.path.join(t, "rec.json")
            write(rp, r.stdout)
            cm = subprocess.run([sys.executable, TOOL, "configmap", "--name", "numerics", rp],
                                capture_output=True, text=True, check=True).stdout
            cmp_ = os.path.join(t, "cm.json")
            write(cmp_, cm)
            rl = os.path.join(t, "r.json")
            write(rl, json.dumps([cand("cpu-q4km", "q4_k_m")]))
            ok = subprocess.run([sys.executable, TOOL, "resolve", "--spec", "draft-ok", "--model", "m",
                                 "--realisations", rl, "--evidence", cmp_], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertTrue(json.loads(ok.stdout)["annotations"][nx.ANNOTATION_EVIDENCE].startswith("sha256:"))
            bad = subprocess.run([sys.executable, TOOL, "resolve", "--spec", "bf16-equivalent", "--model", "m",
                                  "--realisations", rl, "--evidence", cmp_], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 3)
            self.assertIn("kl_mean 0.0313 > max_kl 0.01", json.loads(bad.stdout)["reason"])


class SimIntegrity(unittest.TestCase):
    """Controls that keep the sim honest; the headline claim is reported, not asserted."""
    N = 60

    def test_declared_only_equals_format_selector(self):  # AC1
        self.assertEqual(sim.run(dict(sim.DEFAULTS), self.N)["_declared_eq_b1"], 1.0)

    def test_no_win_when_format_table_is_exact(self):  # AC4
        r = sim.run(dict(sim.DEFAULTS, sigma=0.0, p_bug=0.0), self.N)
        self.assertLessEqual(abs(r["B1"]["viol"] - r["R"]["viol"]), 0.01)

    def test_win_vanishes_when_evidence_decorrelated(self):  # AC5
        r = sim.run(dict(sim.DEFAULTS), self.N, shuffle=True)
        self.assertLess(r["B1"]["viol"] - r["R"]["viol"], 0.02)

    def test_resolver_never_sees_truth(self):
        import inspect
        src = inspect.getsource(nx)
        self.assertNotIn("numerics_sim", src)
        self.assertNotIn("truth", src)


if __name__ == "__main__":
    unittest.main(verbosity=1)
