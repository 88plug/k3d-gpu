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

    def test_arms_only_masks_differ(self):
        self.assertEqual(probe.ARMS["PS"], ("16-31", "0-15"))
        self.assertEqual(probe.ARMS["NEG"], ("0-15", "16-31"))

    def test_order_latin_square(self):
        rows = [probe.ORDER[i:i + 3] for i in (0, 3, 6)]
        for r in rows:
            self.assertEqual(sorted(r), sorted(probe.PRIMARY))
        for pos in range(3):
            self.assertEqual(sorted(r[pos] for r in rows), sorted(probe.PRIMARY))
        self.assertEqual(probe.ORDER[-1], "NEG")


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

    def test_inconclusive_throttle_one_arm(self):
        self.assertEqual(table(thr=1), "INCONCLUSIVE")

    def test_inconclusive_contaminated(self):
        self.assertEqual(table(e_busy=0.02), "INCONCLUSIVE")

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
