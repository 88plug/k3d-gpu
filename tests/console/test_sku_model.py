#!/usr/bin/env python3
"""Unit and property tests for console/sku_model.py (H24 `plan --predict`).

  - the frozen H24 arm predictions equal the pre-registered EXPERIMENTS.md numbers
    within 1%
  - the calibration arm reproduces its ledger row (H6)
  - `auto` maps /proc/cpuinfo model names to SKU keys, and only the measured
    host config is labelled "calibrated"
  - properties over random SKUs and memory configs (FUZZ_N cases, FUZZ_SEED):
    calibrated decode never exceeds the efficiency-1 roofline, decode is
    monotone in bandwidth, agents = min(serving cap, sandbox cap), pure
  - bad input exits 2 with a message
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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "console"))
import sku_model as sm  # noqa: E402

# Pre-registered H24 M0 predictions (EXPERIMENTS.md, H24 row): decode, prefill, c4.
PREREG = {"A": (15.7, 172, 62.6), "B": (15.7, 86, 62.6),
          "C": (15.7, 32.3, 32.3), "D": (15.7, 64.5, 62.6)}
TMP = tempfile.mkdtemp()


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = sm.main(argv)
    return rc, out.getvalue(), err.getvalue()


def cpuinfo(name):
    path = os.path.join(TMP, f"cpuinfo-{abs(hash(name))}")
    with open(path, "w") as f:
        f.write(f"processor\t: 0\nvendor_id\t: GenuineIntel\nmodel name\t: {name}\n")
    return path


I9 = None


def dmi(mts, channels, dpc=1):
    """Synthetic `dmidecode -t 17` text: dpc DIMMs per listed channel, plus one empty slot."""
    devs = []
    for ch in channels:
        for d in range(dpc):
            devs.append(f"Memory Device\n\tSize: 16 GB\n\tLocator: Controller0-Channel{ch}-DIMM{d}\n"
                        f"\tSpeed: 5600 MT/s\n\tConfigured Memory Speed: {mts} MT/s\n")
    devs.append("Memory Device\n\tSize: No Module Installed\n\tLocator: Controller1-ChannelZ-DIMM0\n")
    path = os.path.join(TMP, f"dmi-{mts}-{channels}-{dpc}")
    with open(path, "w") as f:
        f.write("# dmidecode 3.6\n" + "\n".join(devs))
    return path


class Arms(unittest.TestCase):
    def test_prereg_within_1pct(self):
        arms = sm.h24_arms()
        for k, (d, p, c4) in PREREG.items():
            got = arms[k]
            for want, have in ((d, got["decode_c1"]), (p, got["prefill"]), (c4, got["c4_agg"])):
                self.assertLessEqual(abs(have - want) / want, 0.01, (k, want, have))

    def test_calibration_reproduces_h6(self):
        eta_m, eta_c = sm.etas()
        self.assertAlmostEqual(eta_m, 0.615, places=3)
        self.assertAlmostEqual(eta_c, 0.336, places=3)
        a = sm.h24_arms()["A"]
        self.assertEqual(a["decode_c1"], round(sm.CALIB["cpu_decode_tok_s"], 1))
        self.assertEqual(a["prefill"], sm.CALIB["cpu_prefill_tok_s"])


class Detect(unittest.TestCase):
    def test_names(self):
        cases = {"13th Gen Intel(R) Core(TM) i9-13900T": "i9-13900t",
                 "Intel(R) N100": "n100",
                 "Intel(R) Core(TM) Ultra 7 155H": "ultra7-155h",
                 "Intel(R) Xeon(R) Platinum 8480+": "xeon-8480+",
                 "AMD Ryzen 9 7950X 16-Core Processor": None,
                 "Intel(R) Core(TM) i9-13900TE": None}
        for name, key in cases.items():
            self.assertEqual(sm.detect_sku(cpuinfo(name)), key, name)

    def test_missing_file(self):
        self.assertIsNone(sm.detect_sku("/nonexistent/cpuinfo"))

    def test_auto_host_is_calibrated(self):
        # Only a box whose detected memory equals the calibration config is calibrated.
        rc, out, _ = run(["predict", "auto", "--json", "--cpuinfo", I9,
                          "--dmidecode-file", dmi(3600, "AB", dpc=2)])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["status"], "calibrated")
        self.assertEqual(p["inputs"]["mts"], 3600)
        self.assertEqual(p["inputs"]["channels"], 2)
        self.assertEqual(p["cpu"]["decode_tok_s"], 15.7)
        self.assertEqual(p["fields_status"]["c4_agg_tok_s"], "derived")

    def test_auto_other_13900t_box_uses_its_own_memory(self):
        # A 1 DPC DDR5-5600 13900T box must not inherit this host's 3600 MT/s.
        rc, out, _ = run(["predict", "auto", "--json", "--cpuinfo", I9,
                          "--dmidecode-file", dmi(5600, "AB", dpc=1)])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["status"], "extrapolated")
        self.assertEqual(p["inputs"]["mts"], 5600)

    def test_auto_unread_memory_falls_back_to_spec(self):
        rc, out, _ = run(["predict", "auto", "--json", "--cpuinfo", I9,
                          "--dmidecode-file", "/nonexistent/dmi"])
        self.assertEqual(rc, 0)
        p = json.loads(out)
        self.assertEqual(p["status"], "extrapolated")
        self.assertEqual(p["inputs"]["mts"], sm.SKUS["i9-13900t"]["mts"])

    def test_auto_other_config_is_extrapolated(self):
        rc, out, _ = run(["predict", "auto", "--json", "--mts", "5600", "--cpuinfo", I9,
                          "--dmidecode-file", dmi(3600, "AB", dpc=2)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["status"], "extrapolated")

    def test_parse_dmidecode(self):
        with open(dmi(4800, "A", dpc=1)) as f:
            self.assertEqual(sm.parse_dmidecode(f.read()), (4800, 1))
        self.assertEqual(sm.parse_dmidecode(""), (None, None))


class NonHybrid(unittest.TestCase):
    def test_inference_gets_only_the_cores_sandboxes_leave(self):
        # i5-12400: 6 P-cores, 3 to sandboxes, 3 to inference.
        p = sm.predict("i5-12400")
        self.assertEqual(p["inputs"]["sandbox_cores"], 3)
        self.assertTrue(p["inputs"]["inference_cores"][0].startswith("3x"))
        c, _ = sm.compute_spec([("P", 3, "golden-cove", None)])
        want = sm.etas()[1] * c / sm.MODELS["qwen3-4b"]["mac_per_token"]
        self.assertAlmostEqual(p["cpu"]["prefill_tok_s"], round(want, 1))

    def test_named_sku_is_extrapolated(self):
        for key in sm.SKUS:
            rc, out, _ = run(["predict", key, "--json"])
            self.assertEqual(rc, 0, key)
            p = json.loads(out)
            self.assertEqual(p["status"], "extrapolated", key)
            self.assertIn("PENDING", p["hypothesis"])
            self.assertEqual(p["agents_per_box"]["status"], "derived")
            rc, out, _ = run(["predict", key])
            self.assertEqual(rc, 0, key)
            self.assertIn("extrapolated", out)


class BadInput(unittest.TestCase):
    def test_unknown_sku(self):
        rc, _, err = run(["predict", "i9-99999"])
        self.assertEqual(rc, 2)
        self.assertIn("unknown SKU", err)

    def test_auto_unknown_cpu(self):
        rc, _, err = run(["predict", "auto", "--cpuinfo", cpuinfo("AMD EPYC 9654")])
        self.assertEqual(rc, 2)
        self.assertIn("not in the SKU table", err)

    def test_nonpositive_config(self):
        for flag in ("--mts", "--channels"):
            rc, _, err = run(["predict", "n100", flag, "0"])
            self.assertEqual(rc, 2, flag)
            self.assertIn("positive", err)


class Properties(unittest.TestCase):
    def test_random(self):
        seed = int(os.environ.get("FUZZ_SEED", random.randrange(1 << 30)))
        n = int(os.environ.get("FUZZ_N", "100"))
        rng = random.Random(seed)
        keys = sorted(sm.SKUS)
        for _ in range(n):
            key = rng.choice(keys)
            mts = rng.choice([2133, 3200, 3600, 4800, 5600, 6400, 7467, 8533, 8800])
            ch = rng.randint(1, 12)
            model = rng.choice(sorted(sm.MODELS))
            msg = f"FUZZ_SEED={seed} {key} {mts} {ch} {model}"
            p = sm.predict(key, mts, ch, model)
            q = sm.predict(key, mts, ch + 1, model)
            cpu = p["cpu"]
            self.assertLessEqual(cpu["decode_tok_s"], p["baseline_uncalibrated_decode_tok_s"], msg)
            self.assertLessEqual(cpu["decode_tok_s"], q["cpu"]["decode_tok_s"], msg)
            self.assertGreater(cpu["prefill_tok_s"], 0, msg)
            self.assertGreaterEqual(cpu["c4_agg_tok_s"], cpu["decode_tok_s"] - 0.11, msg)
            a = p["agents_per_box"]
            self.assertEqual(a["agents"], min(a["serving_cap"], a["sandbox_cap"]), msg)
            self.assertGreaterEqual(a["agents"], 0, msg)
            self.assertEqual(p, sm.predict(key, mts, ch, model), msg)
            json.dumps(p)


I9 = cpuinfo("13th Gen Intel(R) Core(TM) i9-13900T")

if __name__ == "__main__":
    unittest.main(verbosity=1)
