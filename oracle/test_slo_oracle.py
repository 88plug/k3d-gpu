#!/usr/bin/env python3
"""Unit tests for the SLO oracle. stdlib only: python3 oracle/test_slo_oracle.py"""
import ast
import io
import os
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import slo_oracle as so  # noqa: E402
from cards import EngineCard, Step, Task, dump_trace, gen_trace, load_trace, model_card, platform_card  # noqa: E402
from truth_sim import TruthWorld, draw_params, linear_params  # noqa: E402

M8 = model_card("llama-3.1-8b")
MOE = model_card("qwen3-30b-a3b")
XEON = platform_card("c8i-48xlarge")
ENG = EngineCard(32, 2048, 400_000)


def one(prefix, out, deps=(), sid="s"):
    return Step(sid, "llm", list(deps), list(prefix), out)


class Cache(unittest.TestCase):
    def test_longest_chain_hit(self):
        c = so.PrefixCache(10_000)
        c.insert([("sys", 100), ("u1", 50)])
        self.assertEqual(c.hit([("sys", 100), ("u1", 50), ("a", 10)]), 150)
        self.assertEqual(c.hit([("sys", 100), ("u2", 50)]), 100)
        self.assertEqual(c.hit([("other", 100)]), 0)

    def test_same_key_different_parent_is_a_miss(self):
        c = so.PrefixCache(10_000)
        c.insert([("a", 10), ("x", 10)])
        self.assertEqual(c.hit([("b", 10), ("x", 10)]), 0)

    def test_lru_eviction_by_tokens(self):
        c = so.PrefixCache(150)
        c.insert([("a", 100)])
        c.insert([("b", 100)])
        self.assertEqual(c.hit([("a", 100)]), 0)
        self.assertEqual(c.hit([("b", 100)]), 100)
        self.assertLessEqual(c.used, 150)


class Cards(unittest.TestCase):
    def test_moe_union_monotone_and_bounded(self):
        prev = 0
        for b in (1, 2, 4, 8, 16, 64, 512):
            w = MOE.weight_bytes(b)
            self.assertGreater(w, prev)
            prev = w
        self.assertLess(prev, MOE.bytes_per_param * (MOE.dense_params + MOE.expert_params) + 1)
        self.assertAlmostEqual(MOE.weight_bytes(1), MOE.bytes_per_param * MOE.active_params)

    def test_dense_weight_bytes_constant(self):
        self.assertEqual(M8.weight_bytes(1), M8.weight_bytes(32))

    def test_trace_roundtrip_and_validation(self):
        t = gen_trace("fanout", 3, 0.5, 1)
        self.assertEqual(dump_trace(load_trace(dump_trace(t))), dump_trace(t))
        bad = dump_trace(t)
        bad["tasks"][0]["steps"][1]["deps"] = ["nope"]
        with self.assertRaises(ValueError):
            load_trace(bad)
        with self.assertRaises(ValueError):
            load_trace({"version": 2, "tasks": []})


class Model(unittest.TestCase):
    def roof(self, ec=0.5, eb=0.6, s=0.004):
        return so.Roofline(M8, XEON, so.Calib(ec, eb, s))

    def test_solo_matches_engine_replay(self):
        r = self.roof()
        tasks = [Task("t", 0.0, [one([("p", 3000)], 50)])]
        rec = so.replay(tasks, so.Engine(r, ENG))["calls"][("t", "s")]
        ttft, e2e = r.solo(3000, 0, 50, ENG.chunk_tokens)
        self.assertAlmostEqual(rec["t_first"] - rec["t_sub"], ttft, places=9)
        self.assertAlmostEqual(rec["t_end"] - rec["t_sub"], e2e, places=9)

    def test_decode_is_bandwidth_bound_prefill_compute_bound(self):
        r = self.roof()
        m = r.m
        dec_c = 2 * m.active_params / (XEON.peak_flops * 0.5)
        dec_m = m.weight_bytes(1) / (XEON.mem_bw * 0.6)
        self.assertGreater(dec_m, 10 * dec_c)
        pf_c = 2 * m.active_params * 2048 / (XEON.peak_flops * 0.5)
        self.assertGreater(pf_c, m.weight_bytes(1) / (XEON.mem_bw * 0.6))

    def test_batching_contention_slows_calls(self):
        """16 concurrent calls must each take longer than one alone, but far less than 16x."""
        r = self.roof()
        tasks = [Task(f"t{i}", 0.0, [one([(f"p{i}", 1000)], 100)]) for i in range(16)]
        res = so.replay(tasks, so.Engine(r, ENG))
        solo = r.solo(1000, 0, 100, ENG.chunk_tokens)[1]
        worst = max(res["task"].values())
        self.assertGreater(worst, 1.2 * solo)
        self.assertLess(worst, 16 * solo / 2)

    def test_dag_parallel_branches_overlap(self):
        r = self.roof()
        steps = [one([("sys", 500)], 20, sid="plan")]
        for j in range(4):
            steps.append(one([("sys", 500), (f"q{j}", 50)], 60, deps=["plan"], sid=f"w{j}"))
        steps.append(Step("tool", "tool", ["w0", "w1", "w2", "w3"], dur_s=0.5))
        lat = so.replay([Task("t", 0.0, steps)], so.Engine(r, ENG))["task"]["t"]
        serial = sum(r.solo(s.in_tokens, 0, s.out, 2048)[1] for s in steps if s.kind == "llm") + 0.5
        crit = r.solo(500, 0, 20, 2048)[1] + r.solo(550, 0, 60, 2048)[1] + 0.5
        self.assertLess(lat, serial)
        self.assertGreater(lat, crit * 0.9)

    def test_cache_recompute_uses_target_engine(self):
        r = self.roof()
        steps = [one([("sys", 3000), ("u", 10)], 5, sid="a"),
                 one([("sys", 3000), ("u", 10), ("a", 5), ("r", 10)], 5, deps=["a"], sid="b")]
        big = so.replay([Task("t", 0.0, steps)], so.Engine(r, ENG))["calls"][("t", "b")]["hit"]
        none = so.replay([Task("t", 0.0, steps)], so.Engine(r, EngineCard(32, 2048, 0)))["calls"][("t", "b")]["hit"]
        self.assertEqual(big, 3010)
        self.assertEqual(none, 0)


class Independence(unittest.TestCase):
    def test_oracle_never_imports_truth(self):
        with open(os.path.join(HERE, "slo_oracle.py")) as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module)
        self.assertNotIn("truth_sim", names)
        self.assertNotIn("run_sim", names)

    def test_two_implementations_agree_in_linear_limit(self):
        """truth_sim with every unmodelled effect off == oracle with the same constants."""
        tp = linear_params()
        for fam in ("react", "fanout", "rag"):
            tasks = gen_trace(fam, 10, 0.3, 4)
            t = TruthWorld(M8, XEON, ENG, tp).run(tasks)["task"]
            o = so.replay(tasks, so.Engine(so.Roofline(M8, XEON, so.Calib(tp.ec, tp.eb, tp.step_s)), ENG))["task"]
            for k in t:
                self.assertAlmostEqual(t[k], o[k], delta=1e-6 * t[k], msg=f"{fam} {k}")


class Fit(unittest.TestCase):
    def test_recovers_own_parameters(self):
        true = so.Calib(0.37, 0.55, 0.006)
        anc = {k: so.measure(M8, XEON, ENG, true, k) for k in so.ANCHOR_SHAPES}
        cal, band = so.fit(M8, XEON, ENG, anc)
        self.assertAlmostEqual(cal.eff_c, 0.37, delta=0.01)
        self.assertAlmostEqual(cal.eff_b, 0.55, delta=0.01)
        self.assertAlmostEqual(cal.step_s, 0.006, delta=3e-4)
        self.assertEqual(band, so.BAND_FLOOR)

    def test_recovers_linear_truth(self):
        tp = linear_params()
        w = TruthWorld(M8, XEON, ENG, tp)
        anc = {k: w.measure(so.ANCHOR_SHAPES[k], so.ANCHOR_METRIC[k]) for k in so.ANCHOR_SHAPES}
        cal, _ = so.fit(M8, XEON, ENG, anc)
        self.assertAlmostEqual(cal.eff_b, tp.eb, delta=0.01)
        self.assertAlmostEqual(cal.step_s, tp.step_s, delta=3e-4)
        self.assertAlmostEqual(cal.eff_c, tp.ec, delta=0.01)

    def test_prediction_moves_only_through_anchors(self):
        """Different truth worlds -> different anchors -> different predictions."""
        rng = random.Random(1)
        tasks = gen_trace("react", 6, 0.08, 1)
        preds = []
        for _ in range(2):
            w = TruthWorld(M8, XEON, ENG, draw_params(rng))
            anc = {k: w.measure(so.ANCHOR_SHAPES[k], so.ANCHOR_METRIC[k]) for k in so.ANCHOR_SHAPES}
            cal, band = so.fit(M8, XEON, ENG, anc)
            preds.append(so.predict(tasks, M8, XEON, ENG, cal, band)["p95_s"])
            again = so.predict(tasks, M8, XEON, ENG, *so.fit(M8, XEON, ENG, anc))["p95_s"]
            self.assertEqual(preds[-1], again)
        self.assertNotAlmostEqual(preds[0], preds[1], delta=1e-3)

    def test_band_grows_with_validation_residual(self):
        cal = so.Calib(0.5, 0.6, 0.004)
        anc = {k: so.measure(M8, XEON, ENG, cal, k) for k in so.ANCHOR_SHAPES}
        anc["valid"] *= 1.3
        _, band = so.fit(M8, XEON, ENG, anc)
        self.assertGreater(band, 0.4)


class Gate(unittest.TestCase):
    def test_read_slo_forms(self):
        for s in ('k3d-gpu.io/xeon-slo-p95: "240s"', "k3d-gpu.io/xeon-slo-p95: 240",
                  "k3d-gpu.io/xeon-slo-p95: '240.0'"):
            self.assertEqual(so.read_slo(s), 240.0)
        with self.assertRaises(ValueError):
            so.read_slo("metadata: {}")

    def test_verdict(self):
        p = {"p95_lo_s": 90, "p95_hi_s": 110}
        self.assertEqual(so.verdict(120, p), ("PASS", 0))
        self.assertEqual(so.verdict(80, p), ("FAIL", 1))
        self.assertEqual(so.verdict(100, p), ("INCONCLUSIVE", 2))

    def test_cli_gate_on_examples(self):
        ex = os.path.join(HERE, "examples")
        args = ["gate", "--trace", os.path.join(ex, "trace.react.json"), "--model", "llama-3.1-8b",
                "--anchors", os.path.join(ex, "anchors.synthetic.json"),
                "--manifest", os.path.join(ex, "deployment.yaml")]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = so.main(args)
        self.assertIn('"verdict"', buf.getvalue())
        self.assertIn(rc, (0, 1, 2))
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write('metadata:\n  annotations:\n    k3d-gpu.io/xeon-slo-p95: "1s"\n')
        args[-1] = f.name
        with redirect_stdout(io.StringIO()):
            self.assertEqual(so.main(args), 1)
        os.unlink(f.name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
