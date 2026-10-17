#!/usr/bin/env python3
"""Tests for console/tune.py against a simulated server (no cluster, no hardware).

The simulated server (tune.SimEvaluator) answers like bench/llm_load.py and
bench/energy.sh would, with a speed set by the config through a known cost
model, so the true optimum is known before the search runs. Seeded noise makes
every run reproducible.

  python3 tests/tune_test.py            # all tests
"""

import contextlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.dont_write_bytecode = True  # keep console/ and tests/ free of __pycache__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "console"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import fake_ovms  # noqa: E402
import tune  # noqa: E402

# The host this was written on: i9-13900T (16 P-core CPUs, 16 E-core CPUs,
# AVX-VNNI, no AVX-512/AMX), UHD 770 iGPU.
FACTS = {
    "devices": ["CPU", "GPU", "HETERO:GPU,CPU"], "default_device": "GPU",
    "hybrid": {"p": 16, "e": 16}, "node_cpus": 32, "cpu_flags": ["avx_vnni"],
    "model": "OpenVINO/Qwen3-4B-int4-ov", "precisions": ["int4", "int8", "fp16"],
}
DEFAULT = tune.default_config(FACTS)
FAST = dict(eta=3, n_confirm=5)


def true_tps(cfg):
    """Ground-truth single-stream tokens/s of the simulated server."""
    if cfg.get("baseline"):
        cfg = DEFAULT
    dev = {"CPU": 8.0, "GPU": 12.0, "HETERO:GPU,CPU": 10.0}[cfg["device"]]
    prec = {"int4": 1.0, "int8": 0.7, "fp16": 0.45}[cfg["model"].rsplit("-", 2)[-2]]
    pc = cfg["plugin_config"]
    hint = {"THROUGHPUT": 1.12, "LATENCY": 1.0, None: 1.0}[pc.get("PERFORMANCE_HINT")]
    kv = 1.08 if pc.get("KV_CACHE_PRECISION") == "u8" else 1.0
    cores = 0.6 if (cfg["device"] == "CPU" and cfg["cores"] == "efficient") else 1.0
    cpus = (cfg["cpus"] / 16) ** 0.5 if cfg["device"] == "CPU" else 1.0
    seqs = {16: 0.9, 64: 1.0, 256: 1.0}[cfg["max_num_seqs"]]
    return dev * prec * hint * kv * cores * cpus * seqs


def cost_from(tps_fn, ttft_fn=None, watts_fn=None, quality_fn=None, cpu_fn=None):
    def cost(cfg):
        t = tps_fn(cfg)
        if t is None:
            return None
        return (t,
                ttft_fn(cfg) if ttft_fn else 2.0 / t,
                watts_fn(cfg) if watts_fn else 35.0,
                cpu_fn(cfg) if cpu_fn else 1.0,
                quality_fn(cfg) if quality_fn else 1.0)
    return cost


def picked():
    return tune.sample_space(FACTS, 27, 1)[0]


class SpaceTest(unittest.TestCase):
    def test_space_respects_hardware(self):
        space = tune.full_space(FACTS)
        self.assertTrue(space)
        for c in space:
            # No bf16/f16 CPU hint on an AVX2 client; P/E pods fit their core type.
            if c["device"] == "CPU":
                self.assertNotIn(c["plugin_config"].get("INFERENCE_PRECISION_HINT"), ("bf16", "f16"))
            self.assertLessEqual(c["cpus"], 16)
            self.assertIn(c["model"], {"OpenVINO/Qwen3-4B-int4-ov", "OpenVINO/Qwen3-4B-int8-ov", "OpenVINO/Qwen3-4B-fp16-ov"})

    def test_amx_host_gets_bf16(self):
        facts = dict(FACTS, hybrid=None, devices=["CPU"], default_device="CPU", node_cpus=64,
                     cpu_flags=["amx_bf16", "amx_int8", "amx_tile", "avx512_bf16"])
        hints = {c["plugin_config"].get("INFERENCE_PRECISION_HINT") for c in tune.full_space(facts)}
        self.assertEqual(hints, {None, "bf16"})
        self.assertEqual({c["cores"] for c in tune.full_space(facts)}, {None})

    def test_small_e_core_count_is_feasible_only(self):
        facts = dict(FACTS, hybrid={"p": 12, "e": 4}, node_cpus=16)
        for c in tune.full_space(facts):
            if c["cores"] == "efficient":
                self.assertLessEqual(c["cpus"], 4)

    def test_sample_covers_every_device_and_precision(self):
        p = picked()
        self.assertEqual(len(p), 27)
        self.assertEqual({c["device"] for c in p}, set(FACTS["devices"]))
        self.assertEqual(len({tune.config_key(c) for c in p}), 27)

    def test_sample_is_deterministic(self):
        self.assertEqual(tune.sample_space(FACTS, 27, 7), tune.sample_space(FACTS, 27, 7))

    def test_cpu_space_has_no_twin_of_the_default(self):
        # On x86 the CPU plugin's KV cache is already u8 (openvino e213a147,
        # src/plugins/intel_cpu/src/config.h:79), so KV_CACHE_PRECISION=u8 on
        # CPU is the default under another name: it may not be a separate arm.
        for c in tune.full_space(FACTS):
            if c["device"] == "CPU":
                self.assertNotIn("KV_CACHE_PRECISION", c["plugin_config"], c)

    def test_npu_does_not_vary_batching_knobs(self):
        facts = dict(FACTS, devices=["NPU"], default_device="NPU", hybrid=None, node_cpus=8)
        space = tune.full_space(facts)
        self.assertEqual({c["max_num_seqs"] for c in space}, {256})
        self.assertEqual({c["enable_prefix_caching"] for c in space}, {True})


class ValidateTest(unittest.TestCase):
    def test_rejects_unknown_and_injection(self):
        bad = [
            dict(DEFAULT, device="GPU; rm -rf /"),
            dict(DEFAULT, model="x/y\n  - --malicious"),
            dict(DEFAULT, model="../../etc"),
            dict(DEFAULT, plugin_config={"CACHE_DIR": "/"}),
            dict(DEFAULT, plugin_config={"NUM_STREAMS": "1'"}),
            dict(DEFAULT, cpus=0),
            dict(DEFAULT, cpus=True),
            dict(DEFAULT, enable_prefix_caching="true"),
            dict(DEFAULT, extra=1),
            {"baseline": True, "device": "CPU"},
        ]
        for cfg in bad:
            with self.assertRaises(tune.ConfigError, msg=repr(cfg)):
                tune.validate_config(cfg)

    def test_env_lines(self):
        cfg = dict(DEFAULT, plugin_config={"PERFORMANCE_HINT": "THROUGHPUT"}, cores="efficient")
        env = dict(tune.config_env(cfg))
        self.assertEqual(env["K3D_GPU_AGENT_PLUGIN_CONFIG"], '{"PERFORMANCE_HINT":"THROUGHPUT"}')
        self.assertEqual(env["K3D_GPU_AGENT_CORES"], "efficient")
        self.assertEqual(tune.config_env({"baseline": True}), [])


class StatsTest(unittest.TestCase):
    def test_mann_whitney_exact(self):
        self.assertAlmostEqual(tune.mann_whitney_p([6, 7, 8, 9, 10], [1, 2, 3, 4, 5]), 1 / 252)
        self.assertAlmostEqual(tune.mann_whitney_p([4, 5, 6], [1, 2, 3]), 1 / 20)
        self.assertAlmostEqual(tune.mann_whitney_p([1, 2, 3], [4, 5, 6]), 1.0)
        self.assertGreater(tune.mann_whitney_p([1, 3, 5, 7, 9], [2, 4, 6, 8, 10]), 0.5)

    def test_mann_whitney_ties_count_against_better(self):
        # A tie may not count as half a win, or a tied sample gets the p of a
        # cleaner separation. One tie: 24 strict wins -> P(U >= 24) = 2/252.
        self.assertAlmostEqual(tune.mann_whitney_p([5, 6, 7, 8, 9], [1, 2, 3, 4, 5]), 2 / 252)
        # Two ties: 23 strict wins -> 4/252.
        self.assertAlmostEqual(tune.mann_whitney_p([5, 5, 6, 7, 8], [1, 2, 3, 4, 5]), 4 / 252)

    def test_pareto(self):
        pts = [
            {"aggregate_tokens_per_s": 10, "ttft_s": 1.0, "joules_per_token": 2.0, "tokens_per_cpu_s": 5},
            {"aggregate_tokens_per_s": 20, "ttft_s": 2.0, "joules_per_token": 2.0, "tokens_per_cpu_s": 5},
            {"aggregate_tokens_per_s": 9, "ttft_s": 1.5, "joules_per_token": 2.5, "tokens_per_cpu_s": 4},  # dominated by 0
            {"aggregate_tokens_per_s": 15, "ttft_s": 0.5, "joules_per_token": None, "tokens_per_cpu_s": 5},
        ]
        self.assertEqual(tune.pareto_front(pts), [1, 3])


class ContractTest(unittest.TestCase):
    def test_metrics_from_sim_run(self):
        res = tune.SimEvaluator(cost_from(true_tps))(DEFAULT, {"requests": 4, "max_tokens": 64, "repeats": 1,
                                                              "concurrency": [1, 4]})
        m = tune.run_metrics(res["runs"][0], ttft_slo=10)
        self.assertEqual(m["errors"], 0)
        self.assertEqual(m["best_concurrency"], 4)
        self.assertAlmostEqual(m["tokens_per_s_c1"], true_tps(DEFAULT))
        self.assertIsNotNone(m["joules_per_token"])

    def test_slo_excludes_slow_phases(self):
        res = tune.SimEvaluator(cost_from(true_tps))(DEFAULT, {"requests": 4, "max_tokens": 64, "repeats": 1,
                                                              "concurrency": [1, 8]})
        m = tune.run_metrics(res["runs"][0], ttft_slo=0.2)  # c=8 p90 is above 0.2 s
        self.assertEqual(m["best_concurrency"], 1)

    def test_broken_documents(self):
        with self.assertRaises(tune.ContractError):
            tune.parse_llm_load({"tool": "other", "schema": 1, "phases": []})
        with self.assertRaises(tune.ContractError):
            tune.parse_llm_load({"tool": "k3d-gpu/bench/llm_load.py", "schema": 2, "phases": [{}]})
        with self.assertRaises(tune.ContractError):
            tune.parse_energy({"joules": 1.0, "seconds": 1.0, "exit_code": 1})


class SearchTest(unittest.TestCase):
    def winner_and_truth(self, noise, seed, objective="throughput"):
        configs = picked()
        ev = tune.SimEvaluator(cost_from(true_tps), noise=noise, seed=seed)
        res = tune.search(ev, configs, objective, **FAST)
        truth = max(configs, key=true_tps)
        return res, truth, ev

    def test_finds_optimum_without_noise(self):
        res, truth, _ = self.winner_and_truth(0.0, 0)
        self.assertEqual(res["decision"], "tuned")
        self.assertEqual(res["winner"], truth)

    def test_selects_optimum_with_noise(self):
        # The natural cost model's best sampled config is only ~8% faster than the
        # default: halving must still rank it first among the finalists, even
        # though the confirmation (correctly) may not call 8% at 5% noise a win.
        configs = picked()
        ranked = sorted(configs, key=true_tps, reverse=True)
        self.assertGreater(true_tps(ranked[0]) / true_tps(ranked[1]) - 1, 0.05,
                           "cost model must have a unique optimum for this test to mean anything")
        hits = 0
        for seed in range(20):
            res, truth, _ = self.winner_and_truth(0.05, seed)
            best = max(res["verdicts"], key=lambda v: v["summary"]["aggregate_tokens_per_s"]["median"])
            hits += best["config"] == truth
        self.assertGreaterEqual(hits, 18, f"optimum ranked first in {hits}/20 seeds")

    def test_crowns_planted_optimum_with_noise(self):
        # A config 40% faster than the default, everything else spread around it:
        # found and accepted in nearly every seed at 5% run-to-run noise.
        configs = picked()
        target = tune.config_key(configs[11])
        hits = 0
        for seed in range(20):
            ev = tune.SimEvaluator(cost_from(lambda c: 1.4 * true_tps(DEFAULT) if tune.config_key(c) == target
                                             else true_tps(c)), noise=0.05, seed=seed)
            res = tune.search(ev, configs, "throughput", **FAST)
            hits += res["winner"] is not None and tune.config_key(res["winner"]) == target
        self.assertGreaterEqual(hits, 18, f"planted optimum crowned in {hits}/20 seeds")

    def test_wide_search_can_still_accept(self):
        # n0=120 leaves ~14 finalists. With 5 runs each the smallest exact
        # Mann-Whitney p is 1/C(10,5) = 1/252; Bonferroni over 14 asks for
        # 0.05/14 < 1/252, which no result can meet. The search must confirm only
        # as many finalists as the test can resolve, and still crown a clear win.
        configs = tune.sample_space(FACTS, 120, 1)[0]
        target = tune.config_key(configs[40])
        ev = tune.SimEvaluator(cost_from(lambda c: 1.4 * true_tps(DEFAULT) if tune.config_key(c) == target
                                         else true_tps(c)), noise=0.0, seed=0)
        res = tune.search(ev, configs, "throughput", **FAST)
        floor = 1 / math.comb(2 * FAST["n_confirm"], FAST["n_confirm"])
        self.assertGreaterEqual(res["criteria"]["alpha_per_candidate"], floor)
        self.assertEqual(res["decision"], "tuned")
        self.assertEqual(tune.config_key(res["winner"]), target)

    def test_cheaper_than_grid(self):
        res, _, ev = self.winner_and_truth(0.0, 0)
        configs = picked()
        # Grid: every sampled config + default, n_confirm full runs each.
        final_units = tune.FINAL["requests"] * tune.FINAL["max_tokens"] * 3  # 3 concurrency levels
        grid = (len(configs) + 1) * 5 * final_units
        self.assertLess(ev.request_units, grid / 2, f"SH {ev.request_units} vs grid {grid}")

    def test_deterministic(self):
        a, _, _ = self.winner_and_truth(0.05, 3)
        b, _, _ = self.winner_and_truth(0.05, 3)
        self.assertEqual(json.dumps(a, sort_keys=True, default=str), json.dumps(b, sort_keys=True, default=str))

    def test_no_crown_on_pure_noise(self):
        # 100 null searches, at most 3 crowns: P(<= 3 | rate 5%) = 0.26 and
        # P(<= 3 | rate 10%) = 0.008, so a doubled false-positive rate fails.
        crowned = 0
        for seed in range(100):
            ev = tune.SimEvaluator(cost_from(lambda c: 10.0), noise=0.10, seed=seed)
            res = tune.search(ev, picked(), "throughput", **FAST)
            crowned += res["decision"] == "tuned"
        self.assertLessEqual(crowned, 3, f"crowned a no-effect config in {crowned}/100 seeds")

    def test_no_crown_for_lucky_survivor(self):
        configs = picked()
        lucky = configs[5]
        key = tune.config_key(lucky)
        # True speed equal to every other config; a +60% fluke on its first three
        # evaluations (all of successive halving), never again.
        ev = tune.SimEvaluator(cost_from(lambda c: 10.0), noise=0.02, seed=1,
                               spikes={key: {0: 1.6, 1: 1.6, 2: 1.6}})
        res = tune.search(ev, configs, "throughput", **FAST)
        self.assertIn(key, res["ledger"][-1]["survivors"], "the fluke must survive the rungs for this test to bite")
        self.assertEqual(res["decision"], "default")

    def test_no_crown_for_effect_inside_noise(self):
        configs = picked()
        target = tune.config_key(configs[3])
        crowned = 0
        for seed in range(10):
            ev = tune.SimEvaluator(cost_from(lambda c: 10.3 if tune.config_key(c) == target else 10.0),
                                   noise=0.10, seed=seed)
            crowned += tune.search(ev, configs, "throughput", **FAST)["decision"] == "tuned"
        self.assertLessEqual(crowned, 1)

    def test_guardrail_blocks_ttft_regression(self):
        configs = picked()
        target = tune.config_key(configs[4])
        ev = tune.SimEvaluator(cost_from(lambda c: 20.0 if tune.config_key(c) == target else 10.0,
                                         ttft_fn=lambda c: 1.0 if tune.config_key(c) == target else 0.2))
        res = tune.search(ev, configs, "throughput", **FAST)
        self.assertEqual(res["decision"], "default")
        v = next(v for v in res["verdicts"] if tune.config_key(v["config"]) == target)
        self.assertTrue(any("guardrail ttft_s" in r for r in v["reasons"]), v["reasons"])

    def test_guardrail_blocks_quality_loss(self):
        configs = picked()
        target = tune.config_key(configs[4])
        ev = tune.SimEvaluator(cost_from(lambda c: 20.0 if tune.config_key(c) == target else 10.0,
                                         quality_fn=lambda c: 0.6 if tune.config_key(c) == target else 1.0))
        res = tune.search(ev, configs, "throughput", **FAST)
        self.assertEqual(res["decision"], "default")

    def test_joules_objective(self):
        configs = picked()
        target = tune.config_key(configs[6])
        ev = tune.SimEvaluator(cost_from(lambda c: 10.0, watts_fn=lambda c: 20.0 if tune.config_key(c) == target else 35.0),
                               noise=0.02, seed=2)
        res = tune.search(ev, configs, "joules", **FAST)
        self.assertEqual(tune.config_key(res["winner"]), target)

    def test_cpu_offload_objective(self):
        configs = picked()
        target = tune.config_key(configs[2])
        ev = tune.SimEvaluator(cost_from(lambda c: 10.0, cpu_fn=lambda c: 0.2 if tune.config_key(c) == target else 1.0),
                               noise=0.02, seed=2)
        res = tune.search(ev, configs, "cpu-offload", **FAST)
        self.assertEqual(tune.config_key(res["winner"]), target)

    def test_ttft_objective(self):
        res, _, _ = self.winner_and_truth(0.02, 1, objective="ttft")
        truth = max(picked(), key=true_tps)  # ttft = 2 / tps in the model
        self.assertEqual(res["winner"], truth)

    def test_failing_configs_are_dropped(self):
        configs = picked()
        bad = {tune.config_key(c) for c in configs[:10]}
        ev = tune.SimEvaluator(cost_from(lambda c: None if tune.config_key(c) in bad else true_tps(c)))
        res = tune.search(ev, configs, "throughput", **FAST)
        self.assertNotIn(tune.config_key(res["winner"]), bad)

    def test_default_failure_aborts(self):
        ev = tune.SimEvaluator(cost_from(lambda c: None if c.get("baseline") else 10.0))
        with self.assertRaises(RuntimeError):
            tune.search(ev, picked(), "throughput", **FAST)

    def test_joules_without_energy_aborts(self):
        def no_energy(cfg, budget):
            r = tune.SimEvaluator(cost_from(true_tps))(cfg, budget)
            for run in r["runs"]:
                run["energy"] = None
            return r
        with self.assertRaises(RuntimeError):
            tune.search(no_energy, picked(), "joules", **FAST)


# ---- acceptance gates, one at a time ----------------------------------------------------
# A scripted server: each config answers its listed tokens/s values in order, one
# per call. With no rungs the confirmation sees exactly these samples. Each case
# below fails exactly one gate, so removing that gate makes it pass.

BASE5 = [100.0, 101.0, 102.0, 103.0, 104.0]


def scripted(values):
    """values: config_key -> tokens/s per call (the baseline under its own key)."""
    calls = {}

    def tps(cfg):
        k = tune.config_key(cfg)
        i = calls.get(k, 0)
        calls[k] = i + 1
        return values[k][i]
    return tune.SimEvaluator(cost_from(tps, ttft_fn=lambda c: 0.5))


def confirm(cand_runs, n_cands=1, base=BASE5):
    cands = picked()[:n_cands]
    values = {tune.config_key({"baseline": True}): base}
    values.update({tune.config_key(c): list(cand_runs) for c in cands})
    return tune.search(scripted(values), cands, "throughput", rungs=(), levels=(1,), **FAST)


class GateTest(unittest.TestCase):
    def only_reason(self, res, word):
        self.assertEqual(res["decision"], "default")
        for v in res["verdicts"]:
            self.assertEqual(len(v["reasons"]), 1, v["reasons"])
            self.assertIn(word, v["reasons"][0])

    def test_clear_win_is_accepted(self):
        res = confirm([110.0, 111.0, 112.0, 113.0, 114.0])
        self.assertEqual(res["decision"], "tuned", res["verdicts"][0]["reasons"])

    def test_margin_gate(self):
        # +3%, far outside the spread, perfectly separated: only the margin rejects.
        self.only_reason(confirm([103.0, 103.1, 103.2, 103.3, 103.4],
                                 base=[100.0, 100.1, 100.2, 100.3, 100.4]), "margin")

    def test_separation_gate(self):
        # +5.9% median and p = 1/252, but one wild run widens the spread to 34.
        self.only_reason(confirm([106.0, 107.0, 108.0, 109.0, 140.0]), "separation")

    def test_mann_whitney_gate(self):
        # +10%, separation 1.0, but one default run ties all five candidate
        # runs: 20 strict wins of 25 -> p = 19/252 > 0.05.
        self.only_reason(confirm([110.0] * 5, base=[100.0, 100.0, 100.0, 101.0, 110.0]), "Mann-Whitney")

    def test_bonferroni_over_finalists(self):
        # 24 of 25 wins -> p = 2/252 = 0.0079: enough for one finalist, not for
        # seven (0.05 / 7 = 0.0071).
        runs = [103.5, 110.0, 110.5, 111.0, 111.5]
        self.assertEqual(confirm(runs)["decision"], "tuned")
        res = confirm(runs, n_cands=7)
        self.assertAlmostEqual(res["criteria"]["alpha_per_candidate"], 0.05 / 7)
        self.only_reason(res, "Mann-Whitney")

    def test_confirmation_rotates_the_order(self):
        # Every finalist leads one round and fills every slot, so slow drift
        # (thermal state, page cache) does not favour one side.
        order = []
        sim = tune.SimEvaluator(cost_from(lambda c: 10.0))

        def ev(cfg, budget):
            order.append(tune.config_key(cfg))
            return sim(cfg, budget)
        tune.search(ev, picked()[:2], "throughput", rungs=(), levels=(1,), eta=3, n_confirm=6)
        rounds = [order[i:i + 3] for i in range(0, len(order), 3)]
        self.assertEqual(len(rounds), 6)
        for slot in range(3):
            self.assertEqual(len({r[slot] for r in rounds[:3]}), 3, rounds)


class ProfileTest(unittest.TestCase):
    def test_round_trip_and_fingerprint(self):
        d = tempfile.mkdtemp()
        try:
            ev = tune.SimEvaluator(cost_from(true_tps))
            res = tune.search(ev, picked(), "throughput", **FAST)
            path = os.path.join(d, "tune", "abc.json")
            tune.write_profile(path, res, "abc", {"cpu": "x"}, FACTS)
            self.assertEqual(tune.profile_env(path, "abc"), tune.config_env(res["winner"]))
            self.assertEqual(tune.profile_env(path, "other-host"), [])
            out = subprocess.run([sys.executable, os.path.join(ROOT, "console", "tune.py"), "env",
                                  "--profile", path, "--fingerprint", "abc"],
                                 capture_output=True, text=True, check=True).stdout
            self.assertIn("K3D_GPU_AGENT_DEVICE=", out)
            # A hand-edited profile cannot smuggle a value past the allow-list.
            prof = json.load(open(path))
            prof["winner"]["device"] = "GPU\n  - --evil"
            json.dump(prof, open(path, "w"))
            r = subprocess.run([sys.executable, os.path.join(ROOT, "console", "tune.py"), "env",
                                "--profile", path, "--fingerprint", "abc"], capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")
        finally:
            shutil.rmtree(d)


# ---- end to end: real llm_load.py against a fake OpenAI server ------------------------
# Runs only when bench/llm_load.py is available (or K3D_GPU_BENCH).

def find_llm_load():
    for d in (os.environ.get("K3D_GPU_BENCH"), os.path.join(ROOT, "bench")):
        if d and os.path.isfile(os.path.join(d, "llm_load.py")):
            return os.path.join(d, "llm_load.py")
    return None


@unittest.skipUnless(find_llm_load(), "bench/llm_load.py not present; set K3D_GPU_BENCH")
class EndToEndTest(unittest.TestCase):
    def test_real_llm_load_feeds_the_driver(self):
        results = {}
        for name, manifest in (("default", ""), ("tuned", '"PERFORMANCE_HINT":"THROUGHPUT"')):
            srv = fake_ovms.serve(0, fake_ovms.delay_for(manifest))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            fd, out = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            subprocess.run([sys.executable, find_llm_load(), "--base-url", f"http://127.0.0.1:{srv.server_address[1]}/v3",
                            "--model", "m", "--requests", "4", "--concurrency", "1,2", "--max-tokens", "32",
                            "--out", out], check=True, capture_output=True)
            srv.shutdown()
            srv.server_close()
            with open(out) as f:
                results[name] = tune.run_metrics({"llm_load": json.load(f)}, ttft_slo=10)
            os.unlink(out)
        self.assertGreater(results["tuned"]["tokens_per_s_c1"], 1.3 * results["default"]["tokens_per_s_c1"])
        self.assertEqual(results["tuned"]["errors"], 0)
        self.assertEqual(results["tuned"]["best_concurrency"], 2)
        self.assertTrue(math.isfinite(results["tuned"]["aggregate_tokens_per_s"]))


# ---- incremental tuning ---------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Timed:
    """Wraps an evaluator: every trial advances the fake clock."""

    def __init__(self, ev, clock, seconds):
        self.ev, self.clock, self.seconds, self.calls = ev, clock, seconds, 0

    def __call__(self, cfg, budget):
        self.calls += 1
        self.clock.t += self.seconds
        return self.ev(cfg, budget)


def knobs(cfg):
    dev, ch = tune.as_choice(FACTS, cfg)
    return dict(ch, device=dev)


class NeighbourTest(unittest.TestCase):
    def test_one_knob_at_a_time(self):
        inc = tune.validate_config({"baseline": True})
        base = knobs(inc)
        ns = tune.neighbours(FACTS, inc)
        self.assertTrue(ns)
        for c in ns:
            diff = {k for k in base if knobs(c).get(k) != base[k]}
            if c["device"] == base["device"]:
                self.assertEqual(len(diff), 1, (diff, c))
            else:  # a device switch lands on that device's own default
                self.assertEqual(knobs(c), dict(tune.centre(FACTS, c["device"]), device=c["device"]))
        self.assertEqual(len({tune.config_key(c) for c in ns}), len(ns))
        self.assertTrue(all(tune.feasible(FACTS, c) for c in ns))

    def test_far_fewer_than_the_space(self):
        self.assertLess(len(tune.neighbours(FACTS, {"baseline": True})), len(tune.full_space(FACTS)) / 10)


HINT = "THROUGHPUT"


def is_hint(c):
    return not c.get("baseline") and c["plugin_config"].get("PERFORMANCE_HINT") == HINT


class IncrementalTest(unittest.TestCase):
    def test_finds_the_better_neighbour(self):
        # true_tps: PERFORMANCE_HINT=THROUGHPUT on the default GPU is +12%.
        ev = tune.SimEvaluator(cost_from(true_tps))
        res = tune.incremental(ev, FACTS, {"baseline": True}, "throughput", budget_s=1e9)
        self.assertEqual(res["decision"], "challenger")
        self.assertTrue(is_hint(res["winner"]), res["winner"])
        self.assertTrue(res["confirm"]["verdicts"][0]["accepted"])

    def test_keeps_a_local_optimum(self):
        ev = tune.SimEvaluator(cost_from(lambda c: 20.0 if is_hint(c) else 10.0))
        inc = next(c for c in tune.neighbours(FACTS, {"baseline": True}) if is_hint(c))
        res = tune.incremental(ev, FACTS, inc, "throughput", budget_s=1e9)
        self.assertEqual(res["decision"], "incumbent")
        self.assertIsNone(res["challenger"])
        self.assertIsNone(res["confirm"])  # nothing beat it in screening: no confirmation spent

    def test_no_crown_on_pure_noise(self):
        crowned = 0
        for seed in range(20):
            ev = tune.SimEvaluator(cost_from(lambda c: 10.0), noise=0.05, seed=seed)
            res = tune.incremental(ev, FACTS, {"baseline": True}, "throughput", budget_s=1e9)
            crowned += res["decision"] == "challenger"
        self.assertLessEqual(crowned, 1, f"noise crowned in {crowned}/20 seeds")

    def test_budget_stops_screening_to_keep_time_to_confirm(self):
        clock = Clock()
        ev = Timed(tune.SimEvaluator(cost_from(true_tps)), clock, 30.0)
        # 30 s a trial; confirm reserve = 2 x 5 x 2 x 30 s = 600 s; 900 s for
        # trials (1020 s less the 120 s kept to redeploy the result).
        res = tune.incremental(ev, FACTS, {"baseline": True}, "throughput", budget_s=1020.0, clock=clock)
        self.assertLess(len(res["screened"]), len(tune.neighbours(FACTS, {"baseline": True})))
        self.assertLessEqual(clock.t, 900.0 + 30.0)  # overshoot at most one trial

    def test_budget_expiry_mid_confirmation_decides_nothing(self):
        clock = Clock()
        ev = Timed(tune.SimEvaluator(cost_from(lambda c: 20.0 if is_hint(c) else 10.0)), clock, 30.0)
        n = len(tune.neighbours(FACTS, {"baseline": True}))
        # Screens take 30 s, so all n fit with the 600 s confirm reserve; then the
        # confirmation trials run 4x slower than estimated and the budget ends mid-way.
        orig = ev.__call__
        ev.seconds_for = lambda b: 120.0 if b["requests"] >= 16 else 30.0

        def call(cfg, budget):
            ev.seconds = ev.seconds_for(budget)
            return orig(cfg, budget)
        res = tune.incremental(call, FACTS, {"baseline": True}, "throughput",
                               budget_s=30.0 * (n + 1) + 600.0 + 120.0, clock=clock)
        self.assertEqual(len(res["screened"]), n)
        self.assertTrue(is_hint(res["challenger"]))
        self.assertEqual(res["decision"], "budget")
        self.assertIsNone(res["winner"])

    def test_short_budget_screens_and_defers_confirmation(self):
        # 30 s trials: a confirmation needs ~600 s, more than 75% of a 300 s
        # budget. The run screens with the whole budget instead of nothing and
        # leaves the best screener pending for a later run.
        clock = Clock()
        ev = Timed(tune.SimEvaluator(cost_from(lambda c: 10.0 if c.get("baseline") else 20.0)), clock, 30.0)
        res = tune.incremental(ev, FACTS, {"baseline": True}, "throughput", budget_s=300.0, clock=clock)
        self.assertGreater(len(res["screened"]), 0)
        self.assertLessEqual(clock.t, 300.0 + 30.0)
        self.assertEqual(res["decision"], "pending")
        self.assertIsNone(res["confirm"])
        self.assertIsNotNone(res["pending"])
        self.assertEqual(res["pending"]["config"], res["challenger"])
        self.assertAlmostEqual(res["pending"]["screen_s"], 30.0)
        prof = tune.step_profile(None, res, "t1")
        self.assertEqual(prof["pending"]["config"], res["challenger"])
        self.assertEqual(prof["pending"]["incumbent"], tune.config_key({"baseline": True}))

    def test_screening_stays_inside_the_budget_when_redeploys_are_slow(self):
        # The incumbent is measured warm (20 s); every neighbour redeploys (100 s).
        clock = Clock()
        sim = tune.SimEvaluator(cost_from(lambda c: 10.0))

        def call(cfg, budget):
            clock.t += 20.0 if cfg.get("baseline") else 100.0
            return sim(cfg, budget)
        res = tune.incremental(call, FACTS, {"baseline": True}, "throughput", budget_s=300.0, clock=clock)
        self.assertGreater(len(res["screened"]), 0)
        self.assertLessEqual(clock.t, 300.0)

    def test_trials_get_a_deadline_that_leaves_time_to_restore(self):
        # A trial cut at the deadline (a model download, say) is not counted as
        # screened, and the deadline keeps the restore share of the budget free.
        clock = Clock()
        sim = tune.SimEvaluator(cost_from(lambda c: 10.0))

        class Capped:
            deadline = None

            def __call__(self, cfg, budget):
                if cfg.get("baseline"):
                    clock.t += 20.0
                    return sim(cfg, budget)
                clock.t = self.deadline
                return {"ok": False, "error": tune.TRIAL_DEADLINE, "runs": []}
        ev = Capped()
        res = tune.incremental(ev, FACTS, {"baseline": True}, "throughput", budget_s=300.0, clock=clock)
        self.assertEqual(ev.deadline, 300.0 - tune.restore_s(300.0))
        self.assertEqual(res["screened"], [])
        self.assertLessEqual(clock.t, 300.0 - tune.restore_s(300.0))

    def test_command_evaluator_stops_a_trial_at_its_deadline(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        ev = tune.CommandEvaluator(f"{sys.executable} -c 'import time; time.sleep(30)'", tmp)
        ev.deadline = time.monotonic() + 1.0
        t0 = time.monotonic()
        res = ev({"baseline": True}, {})
        self.assertLess(time.monotonic() - t0, 10.0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], tune.TRIAL_DEADLINE)

    def test_pending_is_confirmed_without_screening(self):
        ev = tune.SimEvaluator(cost_from(lambda c: 20.0 if is_hint(c) else 10.0))
        hint = next(c for c in tune.neighbours(FACTS, {"baseline": True}) if is_hint(c))
        pending = {"config": hint, "gain": 1.0, "screen_s": 1.0}
        res = tune.incremental(ev, FACTS, {"baseline": True}, "throughput", budget_s=1e9, pending=pending)
        self.assertEqual(res["screened"], [])
        self.assertEqual(res["decision"], "challenger")
        self.assertTrue(is_hint(res["winner"]))
        self.assertIsNone(res["pending"])
        prof = tune.step_profile({"pending": dict(pending, incumbent="x")}, res, "t2")
        self.assertNotIn("pending", prof)

    def test_pending_too_big_for_budget_keeps_screening(self):
        clock = Clock()
        ev = Timed(tune.SimEvaluator(cost_from(lambda c: 10.0)), clock, 30.0)
        first = tune.neighbours(FACTS, {"baseline": True})[0]
        pending = {"config": first, "gain": 0.5, "screen_s": 30.0}
        res = tune.incremental(ev, FACTS, {"baseline": True}, "throughput", budget_s=300.0, clock=clock,
                               tried=[tune.config_key(first)], pending=pending)
        self.assertGreater(len(res["screened"]), 0)
        self.assertEqual(res["decision"], "pending")
        self.assertEqual(res["pending"]["config"], first)  # nothing screened beats it

    def test_all_screened_with_pending_names_the_budget(self):
        inc = {"baseline": True}
        tried = [tune.config_key(c) for c in tune.neighbours(FACTS, inc)]
        first = tune.neighbours(FACTS, inc)[0]
        msgs = []
        res = tune.incremental(tune.SimEvaluator(cost_from(lambda c: 10.0)), FACTS, inc, "throughput",
                               budget_s=300.0, tried=tried, log=msgs.append,
                               pending={"config": first, "gain": 0.5, "screen_s": 30.0})
        self.assertEqual(res["decision"], "pending")
        self.assertTrue(any("--budget 10" in m for m in msgs), msgs)

    def test_resume_skips_screened_and_converges(self):
        ev = tune.SimEvaluator(cost_from(lambda c: 10.0))
        inc = {"baseline": True}
        all_n = tune.neighbours(FACTS, inc)
        res1 = tune.incremental(ev, FACTS, inc, "throughput", budget_s=1e9)
        prof = tune.step_profile(None, res1, "t1")
        self.assertEqual(prof["decision"], "default")
        tried = prof["screened"][tune.config_key(inc)]
        self.assertEqual(len(tried), len(all_n))
        res2 = tune.incremental(ev, FACTS, inc, "throughput", budget_s=1e9, tried=tried)
        self.assertEqual(res2["decision"], "converged")
        self.assertEqual(res2["screened"], [])

    def test_partial_screen_then_resume_tries_only_the_rest(self):
        ev = tune.SimEvaluator(cost_from(lambda c: 10.0))
        inc = {"baseline": True}
        all_keys = {tune.config_key(c) for c in tune.neighbours(FACTS, inc)}
        tried = sorted(all_keys)[:5]
        res = tune.incremental(ev, FACTS, inc, "throughput", budget_s=1e9, tried=tried)
        got = {tune.config_key(e["config"]) for e in res["screened"]}
        self.assertEqual(got, all_keys - set(tried))

    def test_winner_becomes_the_next_incumbent(self):
        ev = tune.SimEvaluator(cost_from(true_tps))
        res = tune.incremental(ev, FACTS, {"baseline": True}, "throughput", budget_s=1e9)
        prof = tune.step_profile(None, res, "t1")
        self.assertEqual(prof["decision"], "tuned")
        self.assertEqual(prof["winner"], res["winner"])
        self.assertEqual(prof["screened"][tune.config_key(res["winner"])], [])


class StepCliTest(unittest.TestCase):
    """tune.py step end to end: a trial command backed by SimEvaluator, the
    profile written, read back as the next run's incumbent."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.facts = os.path.join(self.tmp, "facts.json")
        with open(self.facts, "w") as f:
            json.dump(FACTS, f)
        self.trial = os.path.join(self.tmp, "trial.py")
        with open(self.trial, "w") as f:
            f.write(
                "import json, sys\n"
                f"sys.path.insert(0, {os.path.join(ROOT, 'console')!r})\n"
                "import tune\n"
                "cfg, budget, out = (json.load(open(p)) for p in sys.argv[1:3]), None, sys.argv[3]\n"
                "cfg, budget = cfg\n"
                "hint = not cfg.get('baseline') and cfg['plugin_config'].get('PERFORMANCE_HINT') == 'THROUGHPUT'\n"
                "ev = tune.SimEvaluator(lambda c: (20.0 if hint else 10.0, 0.5, 35.0, 1.0, 1.0))\n"
                "json.dump(ev(cfg, budget), open(out, 'w'))\n")
        self.prof = os.path.join(self.tmp, "prof.json")

    def step(self):
        argv = ["step", "--facts", self.facts, "--trial-cmd", f"{sys.executable} {self.trial}",
                "--out", self.prof, "--fingerprint", "fp", "--workdir", os.path.join(self.tmp, "w"),
                "--concurrency", "1"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = tune.main(argv)
        self.assertEqual(rc, 0)
        return out.getvalue(), tune.load(self.prof)

    def test_two_steps(self):
        out, prof = self.step()
        self.assertIn("decision: challenger", out)
        self.assertEqual(prof["decision"], "tuned")
        self.assertTrue(is_hint(prof["winner"]))
        self.assertEqual(tune.profile_env(self.prof, "fp"), tune.config_env(prof["winner"]))
        out, prof2 = self.step()  # starts from the winner: its neighbours are not faster
        self.assertIn("decision: incumbent", out)
        self.assertEqual(prof2["winner"], prof["winner"])
        self.assertEqual(len(prof2["history"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
