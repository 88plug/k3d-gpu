#!/usr/bin/env python3
"""Unit tests for research/prefix-reuse/prefix_model.py (stdlib unittest, no server needed)."""

import contextlib
import io
import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prefix_model as pm  # noqa: E402


class Sizing(unittest.TestCase):
    def test_kv_bytes_qwen3_4b(self):
        # 2 (K,V) * 36 layers * 8 KV heads * 128 dims = 73728 elements per token
        self.assertEqual(pm.kv_bytes_per_token("CPU"), 73728)      # u8
        self.assertEqual(pm.kv_bytes_per_token("GPU"), 36864)      # u4
        self.assertEqual(pm.kv_bytes_per_token("CPU", bits=16), 147456)

    def test_cache_size_rounds_up_whole_gb(self):
        self.assertEqual(pm.cache_size_gb(4, 32768, 73728), 9)     # 9.0 GiB exactly
        self.assertEqual(pm.cache_size_gb(4, 32768, 36864), 5)     # 4.5 -> 5
        self.assertEqual(pm.cache_size_gb(1, 10, 10), 1)           # never 0 (0 = dynamic)


class Ttft(unittest.TestCase):
    def test_cached_prefix_lowers_ttft_monotonically(self):
        vals = [pm.ttft(8192, c, 0.1, 1e-3, 1e-8) for c in range(0, 8192, 512)]
        self.assertEqual(vals, sorted(vals, reverse=True))

    def test_full_hit_still_recomputes_last_token(self):
        t0, a, b = 0.2, 1e-3, 0.0
        self.assertAlmostEqual(pm.ttft(100, 100, t0, a, b), t0 + a)
        self.assertEqual(pm.reused_tokens(4096, 4096, 32), 4095)

    def test_reuse_is_floored_to_blocks(self):
        self.assertEqual(pm.reused_tokens(100, 1000, 32), 96)
        self.assertEqual(pm.reused_tokens(100, 1000, 16), 96)
        self.assertEqual(pm.reused_tokens(31, 1000, 32), 0)

    def test_fit_recovers_parameters(self):
        t0, a, b = 0.15, 2e-3, 3e-8
        pts = [(n, pm.ttft(n, 0, t0, a, b)) for n in (256, 512, 1024, 2048, 4096, 8192)]
        f = pm.fit_cold(pts)
        for got, want in zip(f, (t0, a, b)):
            self.assertAlmostEqual(got, want, delta=abs(want) * 1e-6)

    def test_fit_never_negative(self):
        # concave data would fit b < 0; the fit must refit without the attention term
        pts = [(1000, 1.0), (2000, 1.9), (3000, 2.7), (4000, 3.4)]
        t0, a, b = pm.fit_cold(pts)
        self.assertGreaterEqual(b, 0)
        self.assertGreaterEqual(t0, 0)
        self.assertGreater(a, 0)

    def test_fit_needs_two_lengths(self):
        with self.assertRaises(ValueError):
            pm.fit_cold([(1000, 1.0), (1000, 1.1)])

    def test_invert_is_inverse(self):
        t0, a, b = 0.1, 1e-3, 2e-8
        for c in (0, 1000, 4000, 8000):
            t = pm.ttft(8192, c, t0, a, b)
            self.assertAlmostEqual(pm.invert_cached(8192, t, t0, a, b), c, delta=1e-6)
        self.assertAlmostEqual(pm.invert_cached(100, 0.1 + 100e-3, 0.1, 1e-3, 0.0), 0.0)

    def test_prior_bounds(self):
        # no reuse = 1.0; full reuse -> about the fixed share f0
        self.assertAlmostEqual(pm.ratio_prior(0.0, 8192, 0.05), 1.0)
        self.assertAlmostEqual(pm.ratio_prior(1.0, 8192, 0.05), 0.05, delta=0.001)
        # long contexts: the uncached tail attends to everything, so half the
        # prefix saves less than half the time
        self.assertGreater(pm.ratio_prior(0.5, 32768, 0.0), 0.5)


def toks(rng, n):
    return [rng.randrange(10 ** 9) for _ in range(n)]


class Simulator(unittest.TestCase):
    def test_identical_prompt_full_reuse(self):
        sim = pm.PrefixCache(32)
        p = toks(random.Random(1), 1000)
        self.assertEqual(sim.request(p, toks(random.Random(2), 8), 8), 0)
        self.assertEqual(sim.request(p, toks(random.Random(3), 8), 8), 992)  # 31 full blocks

    def test_one_token_change_caps_reuse(self):
        for block in (16, 32):
            for pos in (5, 100, 900):
                sim = pm.PrefixCache(block)
                p = toks(random.Random(1), 1000)
                sim.request(p, [], 8)
                q = p[:]
                q[pos] = -1
                self.assertEqual(sim.request(q, [], 8), (pos // block) * block, (block, pos))

    def test_dynamic_pool_thrashes_round_robin_static_keeps(self):
        """K agents, shared 2048-token prefix + private history, served in turn."""
        rng = random.Random(0)
        shared = toks(rng, 2048)
        hist = {k: [] for k in range(4)}
        dyn, sta = pm.PrefixCache(32), pm.PrefixCache(32, capacity_blocks=4 * 8192 // 32)
        got = {"dyn": [], "sta": []}
        for _turn in range(6):
            for k in range(4):
                hist[k] += toks(rng, 512)
                prompt = shared + hist[k]
                got["dyn"].append((len(prompt), dyn.request(prompt, toks(rng, 8), 8)))
                got["sta"].append((len(prompt), sta.request(prompt, toks(rng, 8), 8)))
        late = slice(8, None)  # after every agent has had two turns
        for n, c in got["sta"][late]:
            self.assertGreaterEqual(c, n - 512 - 32)       # whole previous prompt reused
        for n, c in got["dyn"][late]:
            self.assertLess(c, n - 512 - 32)               # private history lost
            self.assertGreaterEqual(c, 2048 - 32)          # hot shared prefix survives

    def test_single_agent_dynamic_is_enough(self):
        rng = random.Random(0)
        sim = pm.PrefixCache(32)
        prompt = toks(rng, 2048)
        for _ in range(6):
            prompt = prompt + toks(rng, 512)
            n_before = len(prompt) - 512
            c = sim.request(prompt, toks(rng, 8), 8)
        self.assertGreaterEqual(c, n_before - 32)

    def test_lru_evicts_oldest(self):
        sim = pm.PrefixCache(4, capacity_blocks=4)
        a, b, c = [1] * 8, [2] * 8, [3] * 8
        sim.request(a)          # 2 blocks
        sim.request(b)          # 2 blocks: pool full
        sim.request(a + [9])    # touch a
        sim.request(c)          # must evict b, not a
        self.assertEqual(sim.request(a + [9]), 8)
        self.assertEqual(sim.request(b + [9]), 0)


class Probes(unittest.TestCase):
    def test_sweep_shares_exact_prefix(self):
        parts = pm.gen_probe("sweep", 400, seed=3, reps=2, shares=[0.0, 0.5, 1.0])
        prime = parts["prime"]["sessions"][0]["system"].split()
        for s in parts["sweep"]["sessions"]:
            k = int(s["id"].split("-k")[1].split("-r")[0])
            words = s["system"].split()
            self.assertEqual(len(words), 400)
            self.assertEqual(words[:k], prime[:k])
            if k < 400:
                # the tail is drawn independently; it must not extend the shared run
                self.assertNotEqual(words[:k + 8], prime[:k + 8])

    def test_sweep_reps_have_distinct_tails(self):
        parts = pm.gen_probe("sweep", 400, seed=3, reps=3, shares=[0.5])
        systems = [s["system"] for s in parts["sweep"]["sessions"]]
        self.assertEqual(len(set(systems)), 3)

    def test_perturb_changes_one_word(self):
        parts = pm.gen_probe("perturb", 400, seed=1, reps=1, positions=[16, 200])
        prime = parts["prime"]["sessions"][0]["system"].split()
        for s in parts["perturb"]["sessions"]:
            p = int(s["id"].split("-p")[1].split("-r")[0])
            w = s["system"].split()
            diff = [i for i, (x, y) in enumerate(zip(w, prime)) if x != y]
            self.assertEqual(diff, [p])

    def test_trace_shape_matches_agent_trace(self):
        parts = pm.gen_probe("interleave", 100, agents=2, turns=3, history_words=20)
        t = parts["interleave"]
        self.assertEqual(t["tools"], [])
        self.assertEqual([s["id"] for s in t["sessions"]][:3],
                         ["agent0-t0", "agent1-t0", "agent0-t1"])
        for s in t["sessions"]:
            self.assertEqual(set(s), {"id", "start_s", "system", "turns"})
            self.assertEqual(len(s["turns"]), 1)
            turn = s["turns"][0]
            k = int(s["id"].split("-t")[1])
            self.assertEqual([m["role"] for m in turn["append"]],
                             ["user", "assistant"] * k + ["user"])
            self.assertEqual(turn["recorded"]["role"], "assistant")

    def test_interleave_history_grows_as_prefix(self):
        t = pm.gen_probe("interleave", 50, agents=2, turns=3, history_words=10)["interleave"]
        by = {s["id"]: s["turns"][0]["append"] for s in t["sessions"]}
        self.assertEqual(by["agent1-t2"][:4],
                         by["agent1-t1"] + [{"role": "assistant", "content": "OK."}])

    def test_template_prompt_is_prefix_of_next(self):
        h1 = [{"role": "user", "content": "a b c"}]
        p1 = pm.pseudo_tokens("sys words", h1)
        h2 = h1 + [{"role": "assistant", "content": "OK."}, {"role": "user", "content": "d"}]
        p2 = pm.pseudo_tokens("sys words", h2)
        self.assertEqual(p2[:len(p1)], p1)


def fake_server(runs, fit, capacity_blocks=None, cache=True, noise=0.0, seed=5):
    """agent_trace-style rows for traces served by a model server.

    The fake server is the model itself (word = token): this checks the
    plumbing and that the scoring separates a caching server from a cold one.
    """
    rng = random.Random(seed)
    sim = pm.PrefixCache(32, capacity_blocks)
    out, clock = [], 0.0
    for trace in runs:
        rows = []
        hist = {s["id"]: [] for s in trace["sessions"]}
        # round-robin by turn, like agent sessions running side by side
        for i in range(max(len(s["turns"]) for s in trace["sessions"])):
            for s in trace["sessions"]:
                if i >= len(s["turns"]):
                    continue
                turn = s["turns"][i]
                msgs = hist[s["id"]] + turn["append"]
                prompt = pm.pseudo_tokens(s["system"], msgs)
                gen = ["g%d" % rng.randrange(10 ** 12) for _ in range(turn["max_tokens"])]
                c = sim.request(prompt, gen, turn["max_tokens"]) if cache else 0
                n = len(prompt)
                t = pm.ttft(n, c, *fit) * (1 + rng.uniform(-noise, noise))
                rows.append({"session": s["id"], "turn": i, "sent_at_s": clock, "error": None,
                             "ttft_s": t, "prompt_tokens": n})
                clock += 1.0
                hist[s["id"]] = msgs + [turn["recorded"]]
        out.append((trace, rows))
    return out


class Analyze(unittest.TestCase):
    fit = (0.12, 1.5e-3, 4e-8)
    big = 10 ** 6

    def setUp(self):
        calib = pm.gen_probe("calib", 0, reps=2)["calib"]
        self.calib = fake_server([calib], self.fit, cache=False)[0][1]

    def runs(self, kind, reps=3):
        parts = pm.gen_probe(kind, 2000, reps=reps)
        return [parts[k] for k in ("prime", kind) if k in parts]

    def test_sweep_passes_when_server_matches_model(self):
        runs = fake_server(self.runs("sweep"), self.fit, self.big, noise=0.05)
        v = pm.analyze(self.calib, runs, "CPU", capacity_blocks=self.big)
        self.assertEqual(v["pass_rate"], 1.0, json.dumps(v["points"], indent=1))

    def test_sweep_fails_when_cache_is_off(self):
        runs = fake_server(self.runs("sweep"), self.fit, cache=False)
        v = pm.analyze(self.calib, runs, "CPU", capacity_blocks=self.big)
        high = [p for p in v["points"] if p["pred_cached"] > 1000]
        self.assertTrue(high and not any(p["pass"] for p in high))

    def test_perturb_dynamic_eviction_is_predicted(self):
        """A dynamic pool evicts the primed tail; only a replay that models it passes.

        One repetition per point: with more, later repetitions re-prime each other
        and the group median hides the eviction.
        """
        runs = fake_server(self.runs("perturb", reps=1), self.fit)  # server: cache_size=0
        dyn = pm.analyze(self.calib, runs, "CPU")
        self.assertEqual(dyn["pass_rate"], 1.0, json.dumps(dyn["points"], indent=1))
        naive = pm.analyze(self.calib, runs, "CPU", capacity_blocks=self.big)
        self.assertLess(naive["pass_rate"], 1.0)

    def test_interleave_static_vs_dynamic(self):
        tr = pm.gen_probe("interleave", 1000, agents=4, turns=4, history_words=300)["interleave"]
        runs = fake_server([tr], self.fit)
        self.assertEqual(pm.analyze(self.calib, runs, "CPU")["pass_rate"], 1.0)
        wrong = pm.analyze(self.calib, runs, "CPU", capacity_blocks=self.big)
        self.assertLess(wrong["pass_rate"], 1.0)

    def test_cli_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pm.main(["gen-probe", "sweep", "--out", f"{d}/p",
                                          "--words", "300", "--reps", "1"]), 0)
            traces = [pm.load_json(f"{d}/p.{k}.json") for k in ("prime", "sweep")]
            runs = fake_server(traces, self.fit)
            args = ["analyze", "--device", "CPU", "--calib", f"{d}/calib.jsonl"]
            for name, data in [("calib", self.calib)] + [(k, r) for k, (_, r) in
                                                          zip(("prime", "sweep"), runs)]:
                with open(f"{d}/{name}.jsonl", "w") as f:
                    f.writelines(json.dumps(r) + "\n" for r in data)
                if name != "calib":
                    args += ["--run", f"{d}/p.{name}.json:{d}/{name}.jsonl"]
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = pm.main(args)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out.getvalue())["pass_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
