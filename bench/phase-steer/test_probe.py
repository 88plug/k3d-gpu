#!/usr/bin/env python3
"""Unit tests for bench/phase-steer/probe.py: masks, RAPL wrap, percentiles,
placement proof and every row of the H20 outcome table. No hardware."""
import importlib.util
import json
import os
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("probe", os.path.join(os.path.dirname(__file__), "probe.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def cell(arm, jt, lat=10.0, ttft=1.0, tpot=0.08, tok=1000, e_busy=0.0, thr=0, swp=0, errors=()):
    if e_busy == 0.0 and arm == "PS":
        e_busy = 0.5
    turns = [{"latency": lat + i * 0.01, "ttft": ttft, "tpot": tpot, "tokens": 96} for i in range(20)]
    return {"kind": "cell", "arm": arm, "window_s": 60.0, "turns": turns, "tokens": tok,
            "j_per_tok_net": jt, "errors": list(errors), "busy": {"P": 0.5, "E": e_busy},
            "pre_busy": {"P": 0.01, "E": 0.01}, "throttle_delta": thr, "pswpin_delta": swp}


def table(sp=1.0, ps=0.8, al=0.9, neg_ttft=5.0, **kw):
    cells = []
    for _ in range(3):
        cells += [cell("SP", sp), cell("PS", ps, **kw), cell("ALL", al)]
    cells.append(cell("NEG", 1.0, ttft=neg_ttft))
    return probe.verdict(probe.summarize(cells))[0]


class Masks(unittest.TestCase):
    def test_hexmask(self):
        self.assertEqual(probe.hexmask("0-15"), "0xffff")
        self.assertEqual(probe.hexmask("16-31"), "0xffff0000")
        self.assertEqual(probe.hexmask("0,2,4,6"), "0x55")

    def test_arms(self):
        self.assertEqual(probe.PHASES["PS"], ("E", "P"))
        self.assertEqual(probe.PHASES["NEG"], ("P", "E"))
        self.assertEqual(probe.PHASES["ALL"], (None, None))

    def test_slices_disjoint_every_arm(self):
        for arm in probe.PHASES:
            sets = [probe.expected_cpus(arm, i) for i in range(4)]
            for a in range(4):
                for b in range(a + 1, 4):
                    self.assertFalse(sets[a] & sets[b], (arm, a, b))

    def test_one_thread_per_physical_core(self):
        for i in range(4):
            p = probe.slice_cpus("P", i)
            self.assertEqual(len({c // 2 for c in p}), len(p))  # no SMT pair
            self.assertEqual(len({(c - 16) // 4 for c in probe.slice_cpus("E", i)}), 1)  # one L2 cluster

    def test_server_args(self):
        self.assertEqual(probe.server_args("PS", 1),
                         ["-t", "4", "-tb", "2", "--cpu-mask", "0xf00000", "--cpu-mask-batch", "0x50",
                          "--cpu-strict", "1", "--cpu-strict-batch", "1"])
        self.assertNotIn("--cpu-mask", probe.server_args("ALL", 0))
        self.assertIn("0", probe.server_args("ALL", 0)[probe.server_args("ALL", 0).index("--cpu-strict") + 1])

    def test_overlap_detected(self):
        stacked = {"a0": ["0", "1", "0-31"], "a1": ["0", "1", "0-31"]}
        self.assertEqual(probe.overlap(stacked), [0, 1])
        self.assertFalse(probe.placement_ok("SP", stacked))
        good = {"a0": ["0", "2", "0-31"], "a1": ["4", "6", "0-31"]}
        self.assertTrue(probe.placement_ok("SP", good))
        self.assertFalse(probe.placement_ok("ALL", good))
        self.assertTrue(probe.placement_ok("ALL", {"a0": ["0-31"], "a1": ["0-31"]}))

    def test_order_latin_square(self):
        primary = [a for a in probe.ORDER if a != "NEG"]
        rows = [primary[i:i + 3] for i in (0, 3, 6)]
        for r in rows:
            self.assertEqual(sorted(r), sorted(probe.PRIMARY))
        for pos in range(3):
            self.assertEqual(sorted(r[pos] for r in rows), sorted(probe.PRIMARY))
        self.assertEqual(probe.ORDER.index("NEG"), 3)  # A2: NEG fourth
        self.assertEqual(probe.ORDER.count("NEG"), 1)

    def test_slot_flag_required(self):
        self.assertIn("--slot-save-path", probe.REQUIRED_FLAGS)


class Readings(unittest.TestCase):
    def test_energy_wrap(self):
        self.assertAlmostEqual(probe.energy_delta_j((900, 1000), (100, 1000)), 200 / 1e6)
        self.assertAlmostEqual(probe.energy_delta_j((100, 1000), (600, 1000)), 500 / 1e6)

    def test_pct(self):
        self.assertEqual(probe.pct(list(range(1, 101)), 0.95), 95)
        self.assertIsNone(probe.pct([], 0.95))

    def test_busy_frac(self):
        a, b = {0: (0, 100), 1: (0, 100)}, {0: (50, 200), 1: (0, 200)}
        self.assertEqual(probe.busy_frac(a, b, [0, 1]), 0.25)


class Outcomes(unittest.TestCase):
    def test_supported(self):
        self.assertEqual(table(), "SUPPORTED")

    def test_refuted_energy(self):
        self.assertEqual(table(ps=0.9), "REFUTED")

    def test_refuted_all_better(self):
        self.assertEqual(table(al=0.7), "REFUTED")

    def test_refuted_latency(self):
        self.assertEqual(table(lat=30.0), "REFUTED")

    def test_refuted_failed_requests(self):
        self.assertEqual(table(errors=("boom",)), "REFUTED")

    def test_inconclusive_neg(self):
        self.assertEqual(table(neg_ttft=0.5), "INCONCLUSIVE")

    def test_throttle_one_arm_no_longer_gates(self):
        # A2: in-cell throttle/pswpin are recorded; the background load match gates instead.
        self.assertEqual(table(thr=1, swp=3), "SUPPORTED")

    def test_inconclusive_cache_miss(self):
        cells = []
        for _ in range(3):
            cells += [cell("SP", 1.0), cell("PS", 0.8), cell("ALL", 0.9)]
        cells[0]["turns"][0]["prompt_n"] = 1420
        cells.append(cell("NEG", 1.0, ttft=5))
        self.assertTrue(probe.cache_miss(cells[0]))
        self.assertEqual(probe.verdict(probe.summarize(cells))[0], "INCONCLUSIVE")

    def test_cache_hit_within_slack(self):
        c = cell("SP", 1.0)
        c["turns"][0]["prompt_n"] = probe.TURN_TOK + probe.CACHE_SLACK
        self.assertFalse(probe.cache_miss(c))

    def test_saturated_e_cores_use_affinity_only(self):
        c = cell("PS", 0.8, e_busy=0.97)
        c["pre_busy"] = {"E": 0.96}
        self.assertFalse(probe.contaminated(c))
        c["pre_busy"] = {"E": 0.50}
        c["busy"]["E"] = 0.52
        self.assertTrue(probe.contaminated(c))

    def test_load_unmatched_is_inconclusive(self):
        cells = []
        for _ in range(3):
            for arm, jt in (("SP", 1.0), ("PS", 0.8), ("ALL", 0.9)):
                c = cell(arm, jt)
                c["load"] = {"tag": arm, "level": "typical", "busy_pct": 40.0 if arm != "PS" else 60.0,
                             "load1": 8.0, "pkg_w": 60.0, "throttle_per_min": 0.0,
                             "swap_used_mib": 100.0, "swap_delta_mib": 0.0}
                cells.append(c)
        cells.append(cell("NEG", 1.0, ttft=5))
        load = probe.load_match(cells)
        self.assertFalse(load["matched"])
        v, why = probe.verdict(probe.summarize(cells), load)
        self.assertEqual(v, "INCONCLUSIVE")
        self.assertIn("busy_pct", why)
        for c in cells:
            if c.get("load"):
                c["load"]["busy_pct"] = 40.0
        self.assertEqual(probe.verdict(probe.summarize(cells), probe.load_match(cells))[0], "SUPPORTED")

    def test_high_load_alone_never_voids(self):
        cells = []
        for _ in range(3):
            for arm, jt in (("SP", 1.0), ("PS", 0.8), ("ALL", 0.9)):
                c = cell(arm, jt)
                c["load"] = {"tag": arm, "level": "heavy", "busy_pct": 99.0, "load1": 40.0, "pkg_w": 110.0,
                             "throttle_per_min": 500.0, "swap_used_mib": 4000.0, "swap_delta_mib": 0.0}
                cells.append(c)
        cells.append(cell("NEG", 1.0, ttft=5))
        self.assertTrue(probe.load_match(cells)["matched"])

    def test_mann_whitney(self):
        self.assertAlmostEqual(probe.mann_whitney_p([1, 2, 3], [4, 5, 6]), 0.05)
        self.assertAlmostEqual(probe.mann_whitney_p([4, 5, 6], [1, 2, 3]), 1.0)

    def test_inconclusive_contaminated(self):
        self.assertEqual(table(e_busy=0.02), "INCONCLUSIVE")

    def test_inconclusive_overlap(self):
        cells = []
        for _ in range(3):
            cells += [cell("SP", 1.0), cell("PS", 0.8), cell("ALL", 0.9)]
        cells[1]["overlap"] = [16]
        cells.append(cell("NEG", 1.0, ttft=5))
        self.assertEqual(probe.verdict(probe.summarize(cells))[0], "INCONCLUSIVE")

    def test_inconclusive_short(self):
        cells = [cell("SP", 1.0), cell("PS", 0.8), cell("ALL", 0.9), cell("NEG", 1.0, ttft=5)]
        self.assertEqual(probe.verdict(probe.summarize(cells))[0], "INCONCLUSIVE")

    def test_analyze_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"kind": "header"}) + "\n")
            for _ in range(3):
                for arm, jt in (("SP", 1.0), ("PS", 0.8), ("ALL", 0.9)):
                    f.write(json.dumps(cell(arm, jt)) + "\n")
            f.write(json.dumps(cell("NEG", 1.0, ttft=5)) + "\n")
        try:
            self.assertEqual(probe.analyze(f.name)["verdict"], "SUPPORTED")
        finally:
            os.unlink(f.name)


class Cli(unittest.TestCase):
    def test_budget_capped(self):
        with self.assertRaises(SystemExit):
            probe.main(["--model", "x"])  # no image: refuses before any docker call


if __name__ == "__main__":
    unittest.main()
