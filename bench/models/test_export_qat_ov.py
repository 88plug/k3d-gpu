#!/usr/bin/env python3
"""Tests for bench/models/export_qat_ov.sh (crescent amendment M1): the export arguments keep the
QAT scheme (symmetric INT4, group 32, every layer), the IR is written to a .partial dir, and a
revision that is not a full commit is refused. No export runs."""
import os
import subprocess
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "export_qat_ov.sh")
REV = "f1e06dc520982d9b9edd76859fdb7ab209449949"


def run(*args, env=None):
    return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True,
                          env=dict(os.environ, OV_VENV="/v", **(env or {})))


class ExportArgsTest(unittest.TestCase):
    def test_dry_run_prints_the_qat_matched_export(self):
        p = run("--dry-run", "/m/src", "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized", REV, "/m/out")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.split("\n")[:-1], [
            "/v/bin/optimum-cli", "export", "openvino", "--model", "/m/src", "--task", "text-generation-with-past",
            "--weight-format", "int4", "--sym", "--group-size", "32", "--ratio", "1.0", "--backup-precision", "none",
            "/m/out.partial"])

    def test_task_override(self):
        p = run("--dry-run", "/m/src", "r", REV, "/m/out", "image-text-to-text")
        self.assertIn("image-text-to-text", p.stdout.split("\n"))

    def test_short_revision_is_refused(self):
        p = run("--dry-run", "/m/src", "r", "f1e06dc5", "/m/out")
        self.assertEqual(p.returncode, 2)
        self.assertIn("full 40-hex commit", p.stdout)

    def test_rename_is_the_last_step(self):
        with open(SCRIPT) as f:
            text = f.read()
        self.assertTrue(text.rstrip().splitlines()[-2].startswith('mv -T "${OUT}.partial" "${OUT}"'))
        self.assertLess(text.index('"manifest.json"'), text.index('mv -T "${OUT}.partial" "${OUT}"'))


    def test_grid_gates_fail_the_export_before_the_rename(self):
        with open(SCRIPT) as f:
            text = f.read()
        self.assertIn('qat_grid.py" gate "$1" "$2" --max-skip "${QAT_MAX_SKIP:-0.02}"', text)
        self.assertIn('gate "${OUT}.partial/grid-${b}-export.json" matched || {', text)
        self.assertIn('gate "${OUT}.partial/snap-${b}.json" snapped || {', text)
        self.assertIn('[ "${matched_any}" = 1 ] || { echo "QAT grid: no model file matched the checkpoint"; exit 4; }', text)
        self.assertLess(text.index("matched_any}\" = 1"), text.index('mv -T "${OUT}.partial" "${OUT}"'))



class GreedyCompareTest(unittest.TestCase):
    def test_farm_swaps_only_the_named_files(self):
        import tempfile
        import shutil
        sys_path = os.path.dirname(SCRIPT)
        import sys
        sys.path.insert(0, sys_path)
        import greedy_compare as G
        a, s = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, a)
        self.addCleanup(shutil.rmtree, s)
        for d, names in ((a, ("openvino_model.xml", "openvino_model.bin", "config.json")), (s, ("openvino_model.xml", "openvino_model.bin"))):
            for n in names:
                with open(os.path.join(d, n), "w") as f:
                    f.write(d + n)
        b = G.farm(a, s)
        self.addCleanup(shutil.rmtree, b)
        self.assertEqual(os.path.realpath(os.path.join(b, "openvino_model.xml")), os.path.realpath(os.path.join(s, "openvino_model.xml")))
        self.assertEqual(os.path.realpath(os.path.join(b, "config.json")), os.path.realpath(os.path.join(a, "config.json")))
        self.assertEqual([G.first_diff([1, 2, 3], [1, 2, 3]), G.first_diff([1, 2], [1, 3]), G.first_diff([1], [1, 2])], [None, 1, 1])


if __name__ == "__main__":
    unittest.main(verbosity=1)
