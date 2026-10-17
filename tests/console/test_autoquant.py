#!/usr/bin/env python3
"""Unit tests for console/autoquant.py (EXPERIMENTS.md H27). No hardware."""

import json
import os
import random
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "console"))
import autoquant  # noqa: E402

FIX = os.path.join(REPO, "tests", "fixtures")


def facts(name):
    with open(os.path.join(FIX, name, "facts.json"), encoding="utf-8") as f:
        return json.load(f)


def res(**arms):
    return {"measured_at": "2026-10-17T00:00:00Z", "arms": arms}


def strong(**arms):
    """Results whose pick won 5 of 5 reference rounds with no INCONCLUSIVE note."""
    return dict(res(**arms), rounds=5, wins=5, notes=[])


class Arms(unittest.TestCase):
    def paths(self, name):
        return {a["precision"]: a["path"] for a in autoquant.arms(facts(name))}

    def test_spr_takes_amx(self):
        self.assertEqual(self.paths("xeon-spr-accel"),
                         {"int4": "int4-weight-only", "int8": "amx-int8", "fp16": "amx-bf16"})

    def test_raptorlake_takes_avx_vnni_and_f32(self):
        self.assertEqual(self.paths("raptorlake-nvidia"),
                         {"int4": "int4-weight-only", "int8": "avx-vnni-int8", "fp16": "f32"})

    def test_every_fixture_has_three_arms(self):
        for name in sorted(os.listdir(FIX)):
            if os.path.isfile(os.path.join(FIX, name, "facts.json")):
                got = autoquant.arms(facts(name))
                self.assertEqual([a["precision"] for a in got], ["int4", "int8", "fp16"], name)

    def test_no_cpu_section(self):
        self.assertEqual(self.paths_of({}), {"int4": "int4-weight-only", "int8": "avx2-int8", "fp16": "f32"})

    def paths_of(self, f):
        return {a["precision"]: a["path"] for a in autoquant.arms(f)}


class Fingerprint(unittest.TestCase):
    def test_stable_and_isa_order_free(self):
        f = facts("raptorlake-nvidia")
        g = json.loads(json.dumps(f))
        g["host"]["cpu"]["isa"] = list(reversed(g["host"]["cpu"]["isa"]))
        self.assertEqual(autoquant.fingerprint(f), autoquant.fingerprint(g))

    def test_changes_on_microcode_memory_and_cpu(self):
        f = facts("raptorlake-nvidia")
        base = autoquant.fingerprint(f, "0x12f", 65 * 1024 * 1024)
        self.assertNotEqual(base, autoquant.fingerprint(f, "0x130", 65 * 1024 * 1024))
        self.assertNotEqual(base, autoquant.fingerprint(f, "0x12f", 129 * 1024 * 1024))
        self.assertNotEqual(base, autoquant.fingerprint(facts("xeon-spr-accel"), "0x12f", 65 * 1024 * 1024))

    def test_fixtures_distinct(self):
        names = [n for n in os.listdir(FIX) if os.path.isfile(os.path.join(FIX, n, "facts.json"))]
        fps = {autoquant.fingerprint(facts(n)) for n in names}
        cpus = {json.dumps(autoquant.cpu_of(facts(n)), sort_keys=True) for n in names}
        self.assertEqual(len(fps), len(cpus))


class Pick(unittest.TestCase):
    def test_fastest_passing_wins(self):
        w, _ = autoquant.pick(res(int4={"turn_s": [3, 3, 3], "dnll": 0.04},
                                  int8={"turn_s": [2, 2, 2], "dnll": 0.005},
                                  fp16={"turn_s": [5], "dnll": 0.0}))
        self.assertEqual(w, "int8")

    def test_gate_excludes(self):
        w, why = autoquant.pick(res(int4={"turn_s": [1], "dnll": 0.08}, int8={"turn_s": [2], "dnll": 0.01}))
        self.assertEqual(w, "int8")
        self.assertTrue(any("gate" in r for r in why))

    def test_gate_is_inclusive(self):
        w, _ = autoquant.pick(res(int4={"turn_s": [1], "dnll": 0.05}, int8={"turn_s": [2], "dnll": 0.0}))
        self.assertEqual(w, "int4")

    def test_tie_goes_to_default(self):
        w, _ = autoquant.pick(res(int8={"turn_s": [2], "dnll": 0.0}, int4={"turn_s": [2], "dnll": 0.01}))
        self.assertEqual(w, "int4")

    def test_flagged_missing_and_unknown_never_win(self):
        w, why = autoquant.pick(res(int4={"turn_s": [1], "dnll": 0.0, "flagged": True, "flag_reason": "swap"},
                                    int8={"turn_s": [], "dnll": 0.0},
                                    fp16={"turn_s": [3]},
                                    bf16={"turn_s": [0.1], "dnll": 0.0}))
        self.assertIsNone(w)
        self.assertEqual(len(why), 4)

    def test_win_within_default_spread_keeps_default(self):
        w, why = autoquant.pick(res(int4={"turn_s": [2.0, 2.4, 2.6], "dnll": 0.01},
                                    int8={"turn_s": [2.0, 2.1, 2.2], "dnll": 0.0}))
        self.assertEqual(w, "int4")
        self.assertTrue(any("within its spread" in r for r in why))

    def test_win_beyond_default_spread_switches(self):
        w, _ = autoquant.pick(res(int4={"turn_s": [3.0, 3.1, 3.2], "dnll": 0.01},
                                  int8={"turn_s": [2.0, 2.1, 2.2], "dnll": 0.0}))
        self.assertEqual(w, "int8")

    def test_median_not_mean(self):
        w, _ = autoquant.pick(res(int4={"turn_s": [2, 2, 50], "dnll": 0.0}, int8={"turn_s": [3, 3, 3], "dnll": 0.0}))
        self.assertEqual(w, "int4")

    def test_empty(self):
        self.assertEqual(autoquant.pick({})[0], None)

    def test_fuzz(self):
        n = int(os.environ.get("FUZZ_N", "100"))
        rng = random.Random(27)
        for _ in range(n):
            arms = {}
            for p in rng.sample(["int4", "int8", "fp16", "x"], rng.randint(0, 4)):
                arms[p] = {"turn_s": [rng.choice([rng.uniform(-1, 9), "a", None]) for _ in range(rng.randint(0, 4))],
                           "dnll": rng.choice([rng.uniform(-0.1, 0.2), None, "z"]),
                           "flagged": rng.random() < 0.2}
            w, why = autoquant.pick({"arms": arms})
            self.assertIn(w, (None, "int4", "int8", "fp16"))
            if w is not None:
                a = arms[w]
                self.assertFalse(a["flagged"])
                self.assertLessEqual(a["dnll"], autoquant.GATE_NATS)


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, "sub", "autoquant.json")

    def test_rule_without_cache(self):
        d = autoquant.select(facts("raptorlake-nvidia"), {})
        self.assertEqual((d["precision"], d["source"], d["memory"]), ("int4", "rule", "6Gi"))

    def test_store_then_select_same_host(self):
        f = facts("raptorlake-nvidia")
        w, fp, _ = autoquant.store(f, self.cache, strong(int8={"turn_s": [1], "dnll": 0.0}), microcode="0x12f")
        self.assertEqual(w, "int8")
        d = autoquant.select(f, autoquant.load_cache(self.cache), microcode="0x12f")
        self.assertEqual((d["precision"], d["model"], d["memory"], d["source"], d["fingerprint"]),
                         ("int8", "OpenVINO/Qwen3-4B-int8-ov", "8Gi", "measured", fp))

    def test_microcode_change_invalidates(self):
        f = facts("raptorlake-nvidia")
        autoquant.store(f, self.cache, strong(fp16={"turn_s": [1], "dnll": 0.0}), microcode="0x12f")
        d = autoquant.select(f, autoquant.load_cache(self.cache), microcode="0x130")
        self.assertEqual((d["precision"], d["source"]), ("int4", "rule-stale-cache"))

    def test_weak_evidence_caches_nothing(self):
        f = facts("raptorlake-nvidia")
        arms = {"int8": {"turn_s": [1], "dnll": 0.0}}
        for extra in ({}, {"rounds": 5, "wins": 4}, {"rounds": 3, "wins": 3},
                      {"rounds": 5, "wins": 5, "notes": ["INCONCLUSIVE: control"]},
                      {"rounds": "5", "wins": 5}):
            w, _, why = autoquant.store(f, self.cache, dict(res(**arms), **extra))
            self.assertIsNone(w, extra)
            self.assertTrue(any(r.startswith("evidence:") for r in why))
        self.assertFalse(os.path.exists(self.cache))

    def test_default_pick_needs_no_rounds(self):
        w, _, _ = autoquant.store(facts("raptorlake-nvidia"), self.cache, res(int4={"turn_s": [1], "dnll": 0.0}))
        self.assertEqual(w, "int4")

    def test_runtime_change_invalidates(self):
        f = facts("raptorlake-nvidia")
        autoquant.store(f, self.cache, strong(int8={"turn_s": [1], "dnll": 0.0}), runtime="ovms@sha256:a")
        c = autoquant.load_cache(self.cache)
        self.assertEqual(autoquant.select(f, c, runtime="ovms@sha256:a")["source"], "measured")
        self.assertEqual(autoquant.select(f, c, runtime="ovms@sha256:b")["source"], "rule-stale-cache")

    def test_malformed_hosts_is_safe(self):
        f = facts("raptorlake-nvidia")
        for hosts in ([], "x", 3, {autoquant.fingerprint(f): "int8"}):
            d = autoquant.select(f, {"schema": autoquant.SCHEMA, "hosts": hosts})
            self.assertEqual(d["precision"], "int4")
            os.makedirs(os.path.dirname(self.cache), exist_ok=True)
            with open(self.cache, "w", encoding="utf-8") as fh:
                json.dump({"schema": autoquant.SCHEMA, "hosts": hosts}, fh)
            w, fp, _ = autoquant.store(f, self.cache, strong(int8={"turn_s": [1], "dnll": 0.0}))
            self.assertEqual(w, "int8")
            self.assertEqual(autoquant.select(f, autoquant.load_cache(self.cache))["source"], "measured")

    def test_nothing_passes_writes_nothing(self):
        w, _, _ = autoquant.store(facts("raptorlake-nvidia"), self.cache, res(int4={"turn_s": [1], "dnll": 0.9}))
        self.assertIsNone(w)
        self.assertFalse(os.path.exists(self.cache))

    def test_corrupt_or_foreign_cache_reads_empty(self):
        os.makedirs(os.path.dirname(self.cache))
        for body in ("{", "[]", '{"schema": "other", "hosts": {}}'):
            with open(self.cache, "w", encoding="utf-8") as f:
                f.write(body)
            self.assertEqual(autoquant.load_cache(self.cache), {})

    def test_cached_unknown_winner_is_ignored(self):
        f = facts("raptorlake-nvidia")
        cache = {"schema": autoquant.SCHEMA, "hosts": {autoquant.fingerprint(f): {"winner": "q2"}}}
        self.assertEqual(autoquant.select(f, cache)["source"], "rule-stale-cache")


class Cli(unittest.TestCase):
    def test_pick_exit_codes(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "r.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(res(int4={"turn_s": [1], "dnll": 0.9}), f)
        self.assertEqual(autoquant.main(["pick", "--results", p]), 3)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(res(int4={"turn_s": [1], "dnll": 0.0}), f)
        self.assertEqual(autoquant.main(["pick", "--results", p]), 0)

    def test_bad_args(self):
        for argv in (["pick"], ["select", "--gate", "-1"], ["select", "--mem-kib", "x"], ["nope"]):
            with self.assertRaises(SystemExit):
                autoquant.main(argv)


if __name__ == "__main__":
    unittest.main(verbosity=1)
