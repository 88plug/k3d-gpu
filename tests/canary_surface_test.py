#!/usr/bin/env python3
"""Tests for canary/surface.py: the flag and env names an engine image accepts, read
from its own help output or, for vLLM, statically from its installed sources, and
compared with the names k3d-gpu emits (canary/flags.sh).

Fixtures (tests/fixtures/canary/README.md): a site-packages tree of vLLM v0.32.0's real
arg_utils.py, envs.py and launchers/cli_args.py, and the help output of the pinned
vLLM CPU, llama-server and OVMS images.

Usage: python3 tests/canary_surface_test.py
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures", "canary")
VLLM = os.path.join(FIX, "vllm-0.32.0")
SURFACE = os.path.join(ROOT, "canary", "surface.py")

spec = importlib.util.spec_from_file_location("surface", SURFACE)
surface = importlib.util.module_from_spec(spec)
spec.loader.exec_module(surface)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def flags_sh(*families):
    out = subprocess.run([os.path.join(ROOT, "canary", "flags.sh"), *families],
                         check=True, capture_output=True, text=True).stdout
    return [dict(zip(("family", "source", "kind", "name"), ln.split("\t"))) for ln in out.splitlines()]


def tree_copy():
    d = tempfile.mkdtemp()
    shutil.copytree(VLLM, os.path.join(d, "site"))
    return d, os.path.join(d, "site")


class StaticVllm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.found = surface.static_package(VLLM, "vllm")

    def test_version_from_dist_info(self):
        self.assertEqual(self.found["version"], "0.32.0+cpu")
        self.assertEqual(surface.release(self.found["version"]), "0.32.0")

    def test_engine_and_frontend_flags(self):
        flags = self.found["flags"]
        for f in ("--served-model-name", "--max-model-len", "--enable-prefix-caching", "--dtype",
                  "--speculative-config", "--kv-cache-dtype", "--tensor-parallel-size",
                  # FrontendArgs fields (launchers/cli_args.py), named only as dataclass fields
                  "--tool-call-parser", "--enable-auto-tool-choice", "--port", "--host",
                  # make_arg_parser's own literals
                  "--headless", "--api-server-count"):
            self.assertIn(f, flags, f)
        self.assertGreater(len(flags), 200)

    def test_envs_from_environment_variables(self):
        envs = self.found["envs"]
        for e in ("VLLM_WORKER_MULTIPROC_METHOD", "VLLM_CPU_KVCACHE_SPACE", "VLLM_CACHE_ROOT",
                  "VLLM_ALLOW_LONG_MAX_MODEL_LEN", "VLLM_TARGET_DEVICE", "VLLM_NO_USAGE_STATS"):
            self.assertIn(e, envs, e)
        # removed from vLLM (the launcher's comment in VLLM_XPU_ENV says why it sets neither)
        self.assertNotIn("VLLM_USE_V1", envs)
        self.assertNotIn("VLLM_ATTENTION_BACKEND", envs)

    def test_inside_mode_finds_the_package_on_sys_path(self):
        out = subprocess.run([sys.executable, SURFACE, "inside", "--pkg", "vllm"], capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=VLLM))
        self.assertEqual(out.returncode, 0, out.stderr)
        doc = json.loads(out.stdout)
        self.assertEqual(doc["version"], "0.32.0+cpu")
        self.assertIn("--served-model-name", doc["flags"])
        self.assertIn("VLLM_CPU_KVCACHE_SPACE", doc["envs"])
        self.assertTrue(doc["root"].startswith(VLLM))


class Compare(unittest.TestCase):
    """The launcher's vLLM names against v0.32.0: the real cross-check the canary runs."""

    @classmethod
    def setUpClass(cls):
        cls.found = surface.static_package(VLLM, "vllm")
        cls.want = flags_sh("vllm")

    def test_launcher_vllm_names_all_exist_in_v0_32_0(self):
        self.assertTrue(self.want)
        missing = surface.missing(self.want, self.found["flags"], self.found["envs"], driver="vllm", lenient_no=True)
        self.assertEqual(missing, [])

    def test_present_flag_passes(self):
        want = [{"source": "t", "kind": "flag", "name": "--served-model-name"}]
        self.assertEqual(surface.missing(want, self.found["flags"], set(), driver="vllm"), [])

    def test_underscore_spelling_is_the_same_vllm_flag(self):
        want = [{"source": "t", "kind": "flag", "name": "--max_model_len"}]
        self.assertEqual(surface.missing(want, self.found["flags"], set(), driver="vllm"), [])

    def test_unknown_flag_fails(self):
        want = [{"source": "t", "kind": "flag", "name": "--no-such-flag"}]
        self.assertEqual([m["name"] for m in surface.missing(want, self.found["flags"], set(), driver="vllm")],
                         ["--no-such-flag"])

    def test_no_prefix_needs_the_positive_flag_statically(self):
        want = [{"source": "t", "kind": "flag", "name": "--no-enable-prefix-caching"}]
        self.assertEqual(surface.missing(want, self.found["flags"], set(), driver="vllm", lenient_no=True), [])
        want = [{"source": "t", "kind": "flag", "name": "--no-such-thing"}]
        self.assertEqual(len(surface.missing(want, self.found["flags"], set(), driver="vllm", lenient_no=True)), 1)

    def test_vllm_use_v1_fails(self):
        want = [{"source": "t", "kind": "env", "name": "VLLM_USE_V1"}]
        self.assertEqual([m["name"] for m in surface.missing(want, set(), self.found["envs"], driver="vllm")],
                         ["VLLM_USE_V1"])

    def test_removed_flag_fails(self):
        d, site = tree_copy()
        try:
            p = os.path.join(site, "vllm", "engine", "arg_utils.py")
            text = read(p)
            self.assertIn('"--served-model-name"', text)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text.replace('"--served-model-name"', '"--served-name"'))
            found = surface.static_package(site, "vllm")
            want = [w for w in self.want if w["name"] == "--served-model-name"]
            self.assertTrue(want)
            self.assertEqual({m["name"] for m in surface.missing(want, found["flags"], found["envs"], driver="vllm")},
                             {"--served-model-name"})
        finally:
            shutil.rmtree(d)


class Unparseable(unittest.TestCase):
    def mutate(self, rel, text=None):
        d, site = tree_copy()
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(site, rel)
        if text is None:
            os.remove(p)
        else:
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        return site

    def test_syntax_error(self):
        site = self.mutate("vllm/engine/arg_utils.py", "def broken(:\n")
        with self.assertRaises(surface.Unparseable):
            surface.static_package(site, "vllm")

    def test_missing_envs(self):
        site = self.mutate("vllm/envs.py")
        with self.assertRaises(surface.Unparseable):
            surface.static_package(site, "vllm")

    def test_envs_without_environment_variables(self):
        site = self.mutate("vllm/envs.py", "VLLM_HOST_IP = ''\n")
        with self.assertRaises(surface.Unparseable):
            surface.static_package(site, "vllm")

    def test_no_cli_args_module(self):
        site = self.mutate("vllm/entrypoints/launchers/cli_args.py")
        with self.assertRaises(surface.Unparseable):
            surface.static_package(site, "vllm")

    def test_no_add_argument_in_arg_utils(self):
        site = self.mutate("vllm/engine/arg_utils.py", "x = 1\n")
        with self.assertRaises(surface.Unparseable):
            surface.static_package(site, "vllm")

    def test_no_package(self):
        with self.assertRaises(surface.Unparseable):
            surface.static_package(tempfile.gettempdir(), "vllm-not-here")

    def test_inside_mode_reports_unparseable(self):
        site = self.mutate("vllm/envs.py")
        out = subprocess.run([sys.executable, SURFACE, "inside", "--pkg", "vllm"], capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=site))
        self.assertEqual(out.returncode, 3)
        self.assertIn("error", json.loads(out.stdout))

    def test_empty_help_is_unparseable(self):
        with self.assertRaises(surface.Unparseable):
            surface.help_flags("usage: x\n\nno options here\n")


class Help(unittest.TestCase):
    def test_vllm_help_matches_the_static_parse(self):
        helped = surface.help_flags(read(os.path.join(FIX, "help", "vllm-0.32.0-cpu-serve-help-all.txt")))
        for f in ("--no-enable-prefix-caching", "--enable-prefix-caching", "--served-model-name", "-asc", "-sc",
                  "--speculative-config", "--tool-call-parser", "--enable-auto-tool-choice"):
            self.assertIn(f, helped, f)
        static = surface.static_package(VLLM, "vllm")["flags"]
        # every flag the static parse names is one the parser really has
        self.assertEqual(sorted(set(static) - helped), [])
        missing = surface.missing(flags_sh("vllm"), helped, static_envs(), driver="vllm")
        self.assertEqual(missing, [])

    def test_llama_server_help(self):
        helped = surface.help_flags(read(os.path.join(FIX, "help", "llama-server-b11960-help.txt")))
        for f in ("-ngl", "--gpu-layers", "--n-gpu-layers", "-np", "--parallel", "--load-mode", "-c",
                  "--spec-type", "-md", "--spec-draft-model", "--reasoning", "--jinja", "--no-jinja",
                  "-ctk", "--cpu-moe", "--n-cpu-moe", "--model-url", "-hf", "--threads-batch"):
            self.assertIn(f, helped, f)
        self.assertEqual(surface.missing(flags_sh("llama.cpp"), helped, set(), driver="llama-server"), [])

    def test_ovms_help(self):
        helped = surface.help_flags(read(os.path.join(FIX, "help", "ovms-2026.4.1-help.txt")))
        self.assertEqual(surface.missing(flags_sh("ovms"), helped, set(), driver="ovms"), [])
        self.assertEqual([m["name"] for m in surface.missing([{"source": "t", "kind": "flag", "name": "--max-num-seqs"}],
                                                              helped, set(), driver="ovms")], ["--max-num-seqs"])

    def test_help_line_shapes(self):
        text = ("usage: x [-h] [--a A]\n\noptions:\n  -h, --help            show\n"
                "  --enable-x, --no-enable-x\n  --size SIZE, -s SIZE  the size, see\n"
                "                        --not-a-flag-in-prose\n-ngl,  --gpu-layers, --n-gpu-layers N   layers\n"
                "--spec-type none,draft-mtp\n      --rest_port REST_PORT     REST port\n")
        self.assertEqual(surface.help_flags(text), {"-h", "--help", "--enable-x", "--no-enable-x", "--size", "-s",
                                                    "-ngl", "--gpu-layers", "--n-gpu-layers", "--spec-type",
                                                    "--rest_port"})


def static_envs():
    return surface.static_package(VLLM, "vllm")["envs"]


class Facts(unittest.TestCase):
    def test_release_segment(self):
        self.assertEqual(surface.release("0.32.0+cpu"), "0.32.0")
        self.assertEqual(surface.release("0.26.0b2"), "0.26.0")
        self.assertEqual(surface.release("v1.35.0"), "1.35.0")
        self.assertEqual(surface.release("1.35.0.20260722-29947505341~ubuntu24.04"), "1.35.0")
        self.assertIsNone(surface.release("nightly"))

    def test_firmware_build_date(self):
        self.assertEqual(surface.fw_date(b"\x00junk 20260630*MTL_CLIENT_SILICON-release"), "20260630")
        self.assertEqual(surface.fw_date(b"\x00Jun 30 2026*NPU40xx*more"), "20260630")
        self.assertEqual(surface.fw_date(b"\x00Jan  5 2026*NPU50xx*"), "20260105")
        self.assertIsNone(surface.fw_date(b"no date here"))

    def test_gst_properties(self):
        text = ("Factory Details:\n  Rank                     none (0)\n\nElement Properties:\n\n"
                "  device              : Target device for inference\n                        flags: readable\n"
                "  model-proc          : Path to JSON file\n  pre-process-backend : Preprocessing\n\n"
                "Element Signals:\n  \"handoff\" :  void user_function\n")
        self.assertEqual(surface.gst_properties(text), {"device", "model-proc", "pre-process-backend"})

    def test_derived_mismatch_is_named(self):
        derived = [{"file": "console/plan.py", "of": "package", "value": "0.31.0"},
                   {"file": "console/model_opt.py", "of": "package", "value": "0.32.0"}]
        bad = surface.derived_mismatch(derived, {"package": "0.32.0+cpu"})
        self.assertEqual([d["file"] for d in bad], ["console/plan.py"])
        self.assertEqual(surface.derived_mismatch(derived[1:], {"package": "0.32.0+cpu"}), [])
        # a fact the image did not give is a mismatch, never a pass
        self.assertEqual(len(surface.derived_mismatch(derived[1:], {})), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
