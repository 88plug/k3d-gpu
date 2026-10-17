#!/usr/bin/env python3
"""Unit and property tests for console/shared_prefix.py (H25 detector).

Fixtures in tests/console/shared-prefix/: planner and researcher send the same
system text and tools, coder the same tools with another key order, translator
nothing in common. The property tests draw random agents and check against an
oracle written from the rules (common prefix from token 0, whole KV blocks,
chars/4 estimate), not from shared_prefix.py.

Usage: tests/console/test_shared_prefix.py   (FUZZ_SEED=<int> to replay, FUZZ_N=<int> cases)
Stdlib only.
"""

import io
import json
import os
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "console"))
import shared_prefix as sp  # noqa: E402

FIX = os.path.join(HERE, "shared-prefix")
SEED = int(os.environ.get("FUZZ_SEED", random.randrange(1 << 30)))
N = int(os.environ.get("FUZZ_N", "100"))


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.r = sp.analyse(sp.load([FIX]))

    def test_group(self):
        self.assertEqual(len(self.r["groups"]), 1)
        self.assertEqual(self.r["groups"][0]["agents"], ["coder", "planner", "researcher"])
        self.assertNotIn("translator", self.r["groups"][0]["agents"])

    def test_block_aligned(self):
        for g in self.r["groups"]:
            self.assertEqual(g["reusable_tokens_est"] % 32, 0)
            self.assertLessEqual(g["reusable_tokens_est"], g["shared_tokens_est"])

    def test_key_order_fix(self):
        pairs = {tuple(f["agents"]) for f in self.r["key_order_fixes"]}
        self.assertEqual(pairs, {("coder", "planner"), ("coder", "researcher")})
        for f in self.r["key_order_fixes"]:
            self.assertGreater(f["reusable_tokens_sorted_keys_est"], f["reusable_tokens_est"])

    def test_plan_never_toggles_caching(self):
        p = self.r["plan"]
        self.assertEqual(p["engine"], "shared")
        self.assertTrue(p["enable_prefix_caching"])
        self.assertTrue(p["canonicalise_tool_keys"])
        self.assertIn("PENDING", p["memory_claim"])

    def test_gpu_block(self):
        r = sp.analyse(sp.load([FIX]), device="gpu")
        self.assertEqual(r["block_tokens"], 16)
        self.assertEqual(r["kv_kib_per_token"], 36.0)
        self.assertEqual(r["groups"][0]["reusable_tokens_est"] % 16, 0)

    def test_memory_estimate(self):
        g = self.r["groups"][0]
        self.assertAlmostEqual(g["kv_mib_per_added_agent_unshared_est"],
                               round(g["reusable_tokens_est"] * 72 / 1024, 1))

    def test_alone_is_per_agent(self):
        r = sp.analyse(sp.load([os.path.join(FIX, "translator.json"), os.path.join(FIX, "planner.json")]))
        self.assertEqual(r["groups"], [])
        self.assertEqual(r["plan"]["engine"], "per-agent-ok")

    def test_byte_identical_full_share(self):
        a = sp.load([os.path.join(FIX, "planner.json")])[0]
        b = dict(a, name="twin")
        r = sp.analyse([a, b])
        self.assertEqual(r["groups"][0]["shared_chars"], len(sp.render_head(a)))
        self.assertEqual(r["key_order_fixes"], [])


class Errors(unittest.TestCase):
    def run_main(self, *args):
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            rc = sp.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def test_bad_device(self):
        rc, _, err = self.run_main("--device", "NPU", FIX)
        self.assertEqual(rc, 2)
        self.assertIn("CPU or GPU", err)

    def test_missing_file(self):
        rc, _, err = self.run_main(os.path.join(FIX, "nope.json"))
        self.assertEqual(rc, 2)

    def test_bad_types(self):
        for bad in ({"name": "x", "system": 3}, {"name": "x", "tools": {}}):
            with self.assertRaises(sp.ManifestError):
                sp.analyse([bad])

    def test_duplicate_names(self):
        p = os.path.join(FIX, "planner.json")
        with self.assertRaises(sp.ManifestError):
            sp.load([p, p])

    def test_min_tokens(self):
        with self.assertRaises(sp.ManifestError):
            sp.analyse([], min_tokens=0)

    def test_empty(self):
        r = sp.analyse([])
        self.assertEqual((r["agents"], r["groups"]), (0, []))

    def test_empty_dir_and_non_utf8(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = self.run_main(d)
            self.assertEqual(rc, 2)
            self.assertIn("no agent manifests", err)
            bad = os.path.join(d, "bad.json")
            with open(bad, "wb") as f:
                f.write(b'{"name": "\xff\xfe"}')
            rc, _, err = self.run_main(bad)
            self.assertEqual(rc, 2)

    def test_device_assumed_labelled(self):
        rc, out, _ = self.run_main("--device-assumed", FIX)
        self.assertEqual(rc, 0)
        self.assertIn("device assumed CPU", out)
        rc, out, _ = self.run_main("--json", "--device-assumed", FIX)
        self.assertTrue(json.loads(out)["device_assumed"])

    def test_counter_is_used(self):
        # A counter of one token per char gives 4x the chars/4 estimate.
        agents = sp.load([FIX])
        est = sp.analyse(agents)
        real = sp.analyse(agents, count=len)
        self.assertEqual(real["token_counts"], "tokenizer")
        g0, g1 = est["groups"][0], real["groups"][0]
        self.assertEqual(g1["shared_tokens_est"], g0["shared_chars"])
        self.assertEqual(g1["reusable_tokens_est"], g0["shared_chars"] // 32 * 32)

    @unittest.skipUnless(os.environ.get("H25_TOKENIZER_DIR"), "set H25_TOKENIZER_DIR to a Qwen3 model dir")
    def test_real_tokenizer(self):
        rc, out, _ = self.run_main("--json", "--tokenizer",
                                   os.path.join(os.environ["H25_TOKENIZER_DIR"], "tokenizer.json"), FIX)
        self.assertEqual(rc, 0)
        r = json.loads(out)
        est = sp.analyse(sp.load([FIX]))
        self.assertLess(r["groups"][0]["shared_tokens_est"], est["groups"][0]["shared_tokens_est"])

    def test_json_main(self):
        rc, out, _ = self.run_main("--json", FIX)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["version"], "shared-prefix/v1")


def rand_agent(rng, i, base_sys, base_tools):
    sys_txt = base_sys if rng.random() < 0.7 else base_sys[: rng.randrange(len(base_sys) + 1)] + rng.choice("xyz") * 3
    tools = list(base_tools) if rng.random() < 0.7 else base_tools[: rng.randrange(len(base_tools) + 1)]
    if rng.random() < 0.3:
        tools = [{k: t[k] for k in reversed(list(t))} for t in tools]
    return {"name": f"a{i}", "system": sys_txt, "tools": tools}


class Property(unittest.TestCase):
    def test_oracle(self):
        rng = random.Random(SEED)
        for _ in range(N):
            base_sys = "".join(rng.choice("ab c\n") for _ in range(rng.randrange(0, 3000)))
            base_tools = [{"type": "function", "function": {"name": f"t{k}", "d": "x" * rng.randrange(200)}}
                          for k in range(rng.randrange(0, 6))]
            agents = [rand_agent(rng, i, base_sys, base_tools) for i in range(rng.randrange(0, 7))]
            dev = rng.choice(["CPU", "GPU"])
            mt = rng.choice([1, 32, 256])
            r = sp.analyse(agents, device=dev, min_tokens=mt)
            blk = 32 if dev == "CPU" else 16
            heads = {a["name"]: sp.render_head(a) for a in agents}
            seen = []
            for g in r["groups"]:
                self.assertGreaterEqual(len(g["agents"]), 2, SEED)
                seen += g["agents"]
                hs = [heads[n] for n in g["agents"]]
                lcp = min(os.path.commonprefix([hs[0], h]).__len__() for h in hs[1:])
                # The group prefix is a prefix every member shares, and at most the pairwise LCP.
                self.assertLessEqual(g["shared_chars"], lcp, SEED)
                self.assertTrue(all(h[: g["shared_chars"]] == hs[0][: g["shared_chars"]] for h in hs), SEED)
                self.assertGreaterEqual(g["shared_chars"], mt * 4, SEED)
                self.assertEqual(g["reusable_tokens_est"], (g["shared_chars"] // 4) // blk * blk, SEED)
                self.assertGreaterEqual(g["reusable_tokens_est"], mt, SEED)
            self.assertEqual(len(seen), len(set(seen)), SEED)  # an agent is in at most one group
            self.assertEqual(r["plan"]["engine"], "shared" if r["groups"] else "per-agent-ok", SEED)
            self.assertTrue(r["plan"]["enable_prefix_caching"], SEED)
            # Pure: same input, same output.
            self.assertEqual(r, sp.analyse(agents, device=dev, min_tokens=mt), SEED)


if __name__ == "__main__":
    print(f"FUZZ_SEED={SEED} FUZZ_N={N}")
    unittest.main(verbosity=1)
