#!/usr/bin/env python3
"""Tests for hack/bump-convert-pins on recorded PyPI JSON (no network).

optimum-intel, nncf and openvino go to their newest stable release (nncf and
openvino held to what optimum-intel's openvino extra allows), openvino-tokenizers
to the newest build for that openvino, every other pin to the newest stable
release optimum-intel's requirements allow, and torchvision to the newest release
whose torch requirement the chosen torch meets. Prereleases and fully yanked
releases never count. --dry-run prints the moves and writes nothing.

Usage: python3 tests/bump_convert_pins_test.py (needs the `packaging` module,
PACKAGING_VERSION in hack/ci-tools.env)
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import shutil
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "hack" / "bump-convert-pins"


def load():
    loader = importlib.machinery.SourceFileLoader("bump_convert_pins", str(TOOL))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("bump_convert_pins", loader))
    loader.exec_module(mod)
    return mod


F = [{"yanked": False}]
Y = [{"yanked": True}]


def rel(*names, yanked=(), pre=()):
    out = {n: F for n in names}
    out.update({n: Y for n in yanked})
    out.update({n: F for n in pre})
    return {"releases": out}


PYPI = {
    ("optimum-intel", None): rel("2.2.0", "2.3.0", yanked=["2.3.1"], pre=["2.4.0rc1"]),
    ("optimum-intel", "2.3.0"): {"info": {"requires_dist": [
        "optimum>=2.3,<2.4", "transformers>=5.5,<5.7", "torch>=2.14", "huggingface-hub>=1.20,<2",
        "openvino>=2026.4,<2026.6; extra == 'openvino'", "nncf>=3.4; extra == 'openvino'",
        "openvino-tokenizers; extra == 'openvino'", "neural-compressor; extra == 'neural-compressor'"]}},
    ("nncf", None): rel("3.4.0", "3.5.0", pre=["3.6.0.dev1"]),
    ("openvino", None): rel("2026.4.1", "2026.5.0", "2026.6.0"),
    ("openvino-tokenizers", None): rel("2026.4.1.0", "2026.5.0.0", "2026.5.0.1", "2026.6.0.0"),
    ("optimum", None): rel("2.3.0", "2.3.2", "2.4.0"),
    ("transformers", None): rel("5.5.4", "5.6.3", "5.7.0", pre=["5.6.4rc1"]),
    ("torch", None): rel("2.15.0", "2.16.1", yanked=["2.17.0"]),
    ("huggingface-hub", None): rel("1.21.0", "1.22.0", "2.4.0"),
    ("torchvision", None): rel("0.29.2", "0.30.1", "0.31.0"),
    ("torchvision", "0.31.0"): {"info": {"requires_dist": ["torch==2.17.0"]}},
    ("torchvision", "0.30.1"): {"info": {"requires_dist": ["torch==2.16.1", "numpy"]}},
    ("torchvision", "0.29.2"): {"info": {"requires_dist": ["torch==2.15.0"]}},
}
PINS = {"huggingface-hub": "1.21.0", "nncf": "3.4.0", "openvino": "2026.4.1", "openvino-tokenizers": "2026.4.1.0",
        "optimum": "2.3.0", "optimum-intel": "2.2.0", "torch": "2.15.0", "torchvision": "0.29.2",
        "transformers": "5.5.4"}
WANT = {"huggingface-hub": "1.22.0", "nncf": "3.5.0", "openvino": "2026.5.0", "openvino-tokenizers": "2026.5.0.1",
        "optimum": "2.3.2", "optimum-intel": "2.3.0", "torch": "2.16.1", "torchvision": "0.30.1",
        "transformers": "5.6.3"}


class BumpConvertPins(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.mod.pypi = lambda name, version=None: PYPI[(name, version)]
        self.d = tempfile.mkdtemp(prefix="bcp_test.")
        self.path = os.path.join(self.d, "convert-pins.json")
        with open(self.path, "w") as f:
            json.dump({"schema": "k3d-gpu.convert-pins/v1", "checked": "2026-01-01", "pins": PINS}, f, indent=2)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_tool(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.mod.main(["bump-convert-pins", "--pins", self.path] + list(args))
        with open(self.path) as f:
            return rc, out.getvalue(), json.load(f)

    def test_moves_to_the_newest_compatible_set(self):
        rc, out, doc = self.run_tool()
        self.assertEqual(rc, 0)
        self.assertEqual(doc["pins"], WANT)
        self.assertNotEqual(doc["checked"], "2026-01-01")
        self.assertIn("torchvision 0.29.2 -> 0.30.1", out.splitlines())
        self.assertIn("optimum-intel 2.2.0 -> 2.3.0", out.splitlines())

    def test_dry_run_writes_nothing(self):
        rc, out, doc = self.run_tool("--dry-run")
        self.assertEqual((rc, doc["pins"]), (0, PINS))
        self.assertEqual(len(out.splitlines()), sum(PINS[k] != WANT[k] for k in PINS))

    def test_current_set_is_a_no_op(self):
        with open(self.path, "w") as f:
            json.dump({"schema": "k3d-gpu.convert-pins/v1", "checked": "2026-01-01", "pins": WANT}, f)
        rc, out, doc = self.run_tool()
        self.assertEqual((rc, out, doc["checked"], doc["pins"]), (0, "", "2026-01-01", WANT))

    def test_no_allowed_release_fails(self):
        PYPI[("optimum", None)] = rel("2.4.0")
        try:
            with self.assertRaises(SystemExit) as cm:
                self.run_tool()
            self.assertIn("no optimum release satisfies", str(cm.exception))
        finally:
            PYPI[("optimum", None)] = rel("2.3.0", "2.3.2", "2.4.0")


if __name__ == "__main__":
    unittest.main(verbosity=1)
