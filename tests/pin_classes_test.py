#!/usr/bin/env python3
"""Tests for hack/pin-classes: the pins/classes parser, first-match order, the
effective class against a fixture ledger, pin tokens, the CLI, and the real repo
(coverage passes, every historical-ref string is present, the finished runners
resolve to historical and the pending rows' runners to arm).

Usage: python3 tests/pin_classes_test.py
"""

import importlib.machinery
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "hack" / "pin-classes"
_loader = importlib.machinery.SourceFileLoader("pin_classes", str(TOOL))
pc = importlib.util.module_from_spec(importlib.util.spec_from_loader("pin_classes", _loader))
_loader.exec_module(pc)

LEDGER = """# Experiments

## Hypotheses

| # | Hypothesis | Prediction | Verdict |
|---|------------|------------|---------|
| H1 | one | p | PENDING |
| H2 | two | p | **REFUTED** |
| H3 | three | p | SUPPORTED |
"""

CLASSES = """# a comment
bench/x/old.sh          historical  the first run's runner
bench/x/**              ledger H1,H2 amend=Fam  H1 and H2's runner
bench/done/**           ledger H2,H3 amend=  finished rows
bench/new/**            ledger H7 amend=H7  a row not in the table yet
bench/tool.py           ours
integrations/eat/**     eat-stock  EAT's values
historical-ref bench/x/run.sh OLD=foo/bar:1@sha256:aaaa
"""

DIGEST = "sha256:" + "a" * 64


def write(root, files):
    for rel, text in files.items():
        p = pathlib.Path(root, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def cli(*args, root=ROOT):
    p = subprocess.run([sys.executable, str(TOOL), "--root", str(root)] + list(args), capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


class Parse(unittest.TestCase):
    def test_kinds_and_first_match(self):
        lines, refs = pc.parse(CLASSES)
        self.assertEqual([ln.kind for ln in lines], ["historical", "ledger", "ledger", "ledger", "ours", "eat-stock"])
        self.assertEqual(pc.match(lines, "bench/x/old.sh").kind, "historical")  # before bench/x/**
        self.assertEqual(pc.match(lines, "bench/x/run.sh").rows, ("H1", "H2"))
        self.assertEqual(pc.match(lines, "bench/x/run.sh").aliases, ("Fam",))
        self.assertEqual(pc.match(lines, "bench/done/a/b.py").aliases, ())
        self.assertEqual(pc.match(lines, "bench/x/deep/er.sh").glob, "bench/x/**")
        self.assertIsNone(pc.match(lines, "bench/xy/run.sh"))
        self.assertEqual([(r.glob, r.ref) for r in refs], [("bench/x/run.sh", "OLD=foo/bar:1@sha256:aaaa")])

    def test_errors(self):
        for bad in ("bench/a.sh frozen", "bench/a.sh ledger H1", "bench/a.sh ledger X1 amend=A",
                    "historical-ref bench/a.sh", "bench/a.sh"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                pc.parse(bad + "\n")

    def test_historical_ref_keeps_spaces(self):
        _, refs = pc.parse('historical-ref bench/h.py "k": "v w"\n')
        self.assertEqual(refs[0].ref, '"k": "v w"')


class Effective(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        write(self.d.name, {"EXPERIMENTS.md": LEDGER})
        self.ledger = pc.read_ledger(self.d.name)
        self.lines, _ = pc.parse(CLASSES)

    def tearDown(self):
        self.d.cleanup()

    def eff(self, path, text=None):
        return pc.effective(self.lines, self.ledger, path, text)[0]

    def test_pending_row_is_arm(self):
        self.assertEqual(self.eff("bench/x/run.sh"), "arm")

    def test_verdicts_only_is_historical(self):
        self.assertEqual(self.eff("bench/done/run.sh"), "historical")

    def test_verdict_landing_flips_to_historical(self):
        write(self.d.name, {"EXPERIMENTS.md": LEDGER.replace("| p | PENDING |", "| p | REFUTED |")})
        ledger = pc.read_ledger(self.d.name)
        self.assertEqual(pc.effective(self.lines, ledger, "bench/x/run.sh")[0], "historical")

    def test_rows_not_in_table_yet_are_ours(self):
        self.assertEqual(self.eff("bench/new/run.sh"), "ours")

    def test_plain_kinds_and_defaults(self):
        self.assertEqual(self.eff("bench/x/old.sh"), "historical")
        self.assertEqual(self.eff("bench/tool.py"), "ours")
        self.assertEqual(self.eff("integrations/eat/v.yaml"), "eat-stock")
        self.assertEqual(self.eff("scripts/k3d-gpu", "IMAGE=foo/bar:1@" + DIGEST), "ours")
        self.assertEqual(self.eff("bench/other/run.sh", "echo hello"), "ours")
        self.assertEqual(self.eff("bench/other/run.sh", "IMG=ghcr.io/foo/bar:1.2"), "unclassified")
        self.assertEqual(self.eff("oracle/x.py", 'P = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"'), "unclassified")


class Tokens(unittest.TestCase):
    def test_tokens(self):
        cases = {
            "image: ghcr.io/foo/bar:v1.2@" + DIGEST: ["ghcr.io/foo/bar:v1.2@" + DIGEST],
            "FROM python:3.14-slim AS b": ["python:3.14-slim"],
            "image: busybox:1.37": ["busybox:1.37"],
            "x = 'registry.ollama.ai/library/qwen3:4b-q4_K_M'": ["registry.ollama.ai/library/qwen3:4b-q4_K_M"],
            "pip install vllm==0.24.0 torch==2.9.0+cpu": ["vllm==0.24.0", "torch==2.9.0+cpu"],
            "REV = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678'": ["a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"],
            "KUBECTL_VERSION=v1.37.2  # newest": ["KUBECTL_VERSION=v1.37.2"],
            "B1_TAG=v0.28.0@" + DIGEST: ["B1_TAG=v0.28.0@" + DIGEST, "v0.28.0@" + DIGEST],
            "manifest " + DIGEST: [DIGEST],
            # not pins: comments, awk, sysfs, command substitutions, plain variables
            "# image: foo/bar:1.0": [],
            "awk 'NR==2'": [],
            'z = "class/powercap/intel-rapl:0"': [],
            "RAPL_IMAGE=$(sed -n 's/x//p' f)": [],
            'K3D_GPU_IMAGE="${IMAGE}" K3D_GPU_VENDORS=none': [],
            "IMAGE=${1:?usage: probe.sh IMAGE}": [],
            '-v "${OUT}/x.yaml:/app/config.yaml:ro"': [],
        }
        for line, want in cases.items():
            with self.subTest(line=line):
                self.assertEqual(pc.pin_tokens(line), want)

    def test_overridable_default_is_the_same_pin(self):
        ref = "cryptoandcoffee/k3d-gpu:0.1.63@" + DIGEST
        self.assertEqual(pc.pin_tokens("K3D_GPU_IMAGE=" + ref + " \\")[0],
                         pc.pin_tokens("    K3D_GPU_IMAGE=${H57D_IMAGE:-" + ref + "} \\")[0])

    def test_prose_holds_none(self):
        self.assertEqual(pc.text_tokens("bench/x/README.md", "image: foo/bar:1"), [])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        write(self.d.name, {"EXPERIMENTS.md": LEDGER, "pins/classes": CLASSES,
                            "bench/x/run.sh": "OLD=foo/bar:1@sha256:aaaa\n",
                            "bench/x/old.sh": "echo\n", "bench/done/run.sh": "echo\n",
                            "bench/new/run.sh": "echo\n", "bench/tool.py": "x = 1\n",
                            "integrations/eat/v.yaml": "tag: 1\n"})

    def tearDown(self):
        self.d.cleanup()

    def test_class_and_effective(self):
        self.assertEqual(cli("class", "bench/x/run.sh", root=self.d.name), (0, "ledger\n"))
        self.assertEqual(cli("effective", "bench/x/run.sh", root=self.d.name), (0, "arm\n"))
        self.assertEqual(cli("effective", "./bench/done/run.sh", root=self.d.name), (0, "historical\n"))

    def test_excludes(self):
        rc, out = cli("excludes", root=self.d.name)
        self.assertEqual((rc, out.split()), (0, ["bench/x/old.sh", "bench/x/**", "bench/done/**", "bench/new/**"]))

    def test_coverage(self):
        rc, out = cli("coverage", root=self.d.name)
        self.assertEqual(rc, 1, out)  # H7 is not in the table
        self.assertIn("bench/new/** names H7", out)
        write(self.d.name, {"pins/classes": CLASSES.replace("ledger H7 amend=H7", "ledger H1 amend=H7")})
        self.assertEqual(cli("coverage", root=self.d.name)[0], 0)
        write(self.d.name, {"bench/loose/run.sh": "IMG=ghcr.io/foo/bar:1.2@" + DIGEST + "\n"})
        rc, out = cli("coverage", root=self.d.name)
        self.assertEqual(rc, 1)
        self.assertIn("bench/loose/run.sh: holds pin tokens", out)
        write(self.d.name, {"bench/loose/run.sh": "echo\n", "bench/x/run.sh": "OLD=foo/bar:2\n"})
        rc, out = cli("coverage", root=self.d.name)
        self.assertEqual(rc, 1)
        self.assertIn("historical-ref 'OLD=foo/bar:1@sha256:aaaa' is missing from bench/x/run.sh", out)

    def test_usage(self):
        self.assertEqual(cli("class", root=self.d.name)[0], 2)


class RealRepo(unittest.TestCase):
    """The committed pins/classes against the committed ledger."""

    @classmethod
    def setUpClass(cls):
        cls.lines, cls.refs = pc.load(str(ROOT / "pins" / "classes"))
        cls.ledger = pc.read_ledger(ROOT)

    def eff(self, path):
        return pc.effective(self.lines, self.ledger, path, pc.read_text(str(ROOT), path))[0]

    def test_coverage_passes(self):
        rc, out = cli("coverage")
        self.assertEqual(rc, 0, out)

    def test_every_historical_ref_present(self):
        self.assertTrue(self.refs)
        for r in self.refs:
            with self.subTest(ref=r.ref):
                self.assertIn(r.ref, (ROOT / r.glob).read_text())

    def test_finished_records_are_historical(self):
        for path in ("console/matrix.v1.json", "oracle/seed/build.py"):
            with self.subTest(path=path):
                self.assertEqual(cli("effective", path)[1].strip(), "historical")

    # A runner or record and a row its ledger line must list. The class each resolves to
    # comes from today's ledger, so a verdict landing (or a row re-opened) moves it with
    # no change here: an arm while a listed row is PENDING, historical once none is.
    LEDGER_PATHS = {
        "bench/amx-autoquant/probe.sh": "H27", "bench/apb/arm.sh": "H8", "bench/apb/vllm-cuda.yaml": "H8",
        "bench/apb/h7.sh": "H7", "bench/dra_round.sh": "H16", "bench/noisy.sh": "H26",
        "bench/rdt-isolate/run.sh": "H26", "bench/thermal-resource/probe.sh": "H23",
        "bench/prefill-guard/run.sh": "H5", "bench/h54gpu/h54b.sh": "H54b", "bench/h54/diag/b1g-stall.sh": "H54a",
        "bench/eatflow/bfcl-constraints.txt": "H40", "bench/eatflow/coding-constraints.txt": "H40",
        "bench/eatflow/results/2026-10-17/A1/commit.txt": "H40", "bench/h57d/results/2026-10-21/verdict.json": "H57da",
        "bench/h54/h54.py": "H54q", "bench/h57d/h57d.py": "H57dp", "bench/wildct/hold.sh": "H18w",
        "bench/eatflow/live.sh": "H53", "bench/eatflow/crescent/setup.sh": "H53", "bench/eatflow/agent.lock": "H53",
        "bench/flashnext/h60.sh": "H60", "bench/admission/run.sh": "H55", "bench/h59/h59.py": "H59",
        "bench/apb/h7x.sh": "H7x", "bench/xhost/hold.sh": "H7x", "bench/load/load.py": "H18w",
        "bench/zero-copy-models/probe.sh": "H28w", "bench/hetero-spec/h34.sh": "H34w",
    }

    def test_ledger_paths_follow_the_ledger(self):
        for path, row in self.LEDGER_PATHS.items():
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file(), path)
                ln = pc.match(self.lines, path)
                self.assertEqual(ln.kind, "ledger")
                self.assertIn(row, ln.rows)
                want = "arm" if "PENDING" in [self.ledger[r] for r in ln.rows] else "historical"
                self.assertEqual(cli("effective", path)[1].strip(), want)

    def test_a_verdict_landing_flips_a_real_line_with_no_reclassification(self):
        # The real pins/classes against a copy of today's ledger: a line with a PENDING row
        # resolves to arm, and to historical once that row's verdict lands.
        ln = next((ln for ln in self.lines if ln.kind == "ledger" and "PENDING" in
                   [self.ledger.get(r) for r in ln.rows]), None)
        if ln is None:
            self.skipTest("no ledger line has a PENDING row today")
        self.assertEqual(pc.resolve(ln, self.ledger), "arm")
        landed = dict(self.ledger, **{r: "INCONCLUSIVE" for r in ln.rows if self.ledger.get(r) == "PENDING"})
        self.assertEqual(pc.resolve(ln, landed), "historical")

    def test_shipped_bench_files_and_eat_are_ours_or_eat_stock(self):
        for path in ("bench/vs.py", "bench/llm_load.py", "bench/energy.sh", "bench/agent_trace.py",
                     "bench/agent_trace_gen.py", "bench/apb/apb.py", "bench/apb/capacity.py", "bench/competitors.lock",
                     "oracle/bundle.py", "scripts/k3d-gpu", "share/agent-chat-ui.yml"):
            with self.subTest(path=path):
                self.assertEqual(self.eff(path), "ours")
        for path in ("integrations/eat/vllm-values.yaml", "examples/kuberay/raycluster-hybrid.yaml",
                     "examples/agents/litellm-gateway/agent.py", "contrib/eat/hybrid-values.yaml",
                     "integrations/eas/pins.json"):
            with self.subTest(path=path):
                self.assertEqual(self.eff(path), "eat-stock")

    def test_every_ledger_row_is_in_the_table(self):
        for ln in self.lines:
            for r in ln.rows:
                with self.subTest(glob=ln.glob, row=r):
                    self.assertIn(r, self.ledger)


if __name__ == "__main__":
    os.chdir(ROOT)
    unittest.main(verbosity=1)
