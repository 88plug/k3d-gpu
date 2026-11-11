#!/usr/bin/env python3
"""Tests for hack/vllm-recipes and console/vllm-recipes.json (needs PyYAML; run by
tests/vllm_recipes_test.sh). No network.

Generator, on the frozen fixture tests/fixtures/vllm-recipes (12 recipes copied verbatim
from vllm-project/vllm@960f5c1bb145 recipes/, the recipes repo's taxonomy, single_node_tp
strategy and LICENSE at d9862f5cba9e, and the parser registries of vLLM v0.26.0, v0.32.0
and v0.33.0 with each tag's serve flags frozen in flags.txt; its sources.lock.json names
the vllm commit with the tree id of that 12-recipe subset):
  - the output is byte-equal to artifact.json, and the same on a second run
  - YAML scalars parse as js-yaml 4.1.1 does (scalars.yaml -> scalars.js.json)
  - schema_version 2, a recipes tree that is not the locked one, a lock naming another
    repo, a non-Apache LICENSE, a recipe with no model.model_id, two ids equal in
    lowercase and a kept field of the wrong type are errors
  - a preset on Intel hardware is recorded in `unported`; an NVIDIA-only edit (overrides,
    feature overrides, a preset, an image) leaves the content as it was
  - no image, install, guide or description reaches the artifact; hardware-keyed maps
    hold Intel keys only
  - parser names and serve flags come out of synthetic registry and arg sources
The shipped artifact console/vllm-recipes.json: canonical bytes, the pin's sites, one
engine per vLLM version the tree pins, Apache-2.0 text, under 1 MiB, and Qwen/Qwen3-4B's
parsers equal to the launcher's default (hermes, qwen3).
The bump, offline: `hack/bump vllm-recipes --resolved` runs the hook on the fixture
(VLLM_RECIPES_SRC) and lands the resolved commit with the notes and a review verdict;
`resolve` keeps the pinned values when the content would not change.

Usage: python3 tests/vllm_recipes_gen_test.py
"""

import copy
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "vllm-recipes"
TOOL = ROOT / "hack" / "vllm-recipes"
ART = ROOT / "console" / "vllm-recipes.json"
FIX_COMMIT = "d9862f5cba9ee4e04c959aee595c0c4a410aeac0"
FIX_VERSIONS = "0.26.0,0.32.0,0.33.0"
NEW_COMMIT = "1" * 40
sys.path.insert(0, str(ROOT / "hack"))
sys.path.insert(0, str(ROOT / "canary"))
sys.dont_write_bytecode = True

import surface  # noqa: E402
from vllmrecipes import artifact, jsyaml, source  # noqa: E402

GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
               GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
GIT_ENV.pop("VLLM_RECIPES_PYTHON", None)


def run(*args, env=None, cwd=ROOT):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, cwd=cwd,
                          env=dict(GIT_ENV, **(env or {})))


def gen_cli(src=FIX, out="-"):
    return run(TOOL, "generate", "--recipes-src", src / "recipes", "--site-src", src / "site", "--vllm-src",
               src / "vllm", "--versions", FIX_VERSIONS, "--recipes-commit", FIX_COMMIT, "--out", out)


class Scratch:
    """A writable copy of the fixture whose lock follows its recipes tree."""

    def __init__(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="vllm_recipes_test."))
        shutil.copytree(FIX, self.d / "src")
        self.src = self.d / "src"

    def recipe(self, rel):
        return self.src / "recipes" / "models" / rel

    def edit(self, rel, fn):
        p = self.recipe(rel)
        p.write_text(fn(p.read_text()))

    def relock(self, **vllm):
        p = self.src / "site" / "sources.lock.json"
        lock = json.loads(p.read_text())
        lock["latest"]["vllm"]["tree_sha"] = artifact.git_tree_sha(str(self.src / "recipes"))
        lock["latest"]["vllm"].update(vllm)
        p.write_text(json.dumps(lock, indent=2) + "\n")

    def generate(self):
        s = str(self.src)
        return artifact.generate(s + "/recipes", s + "/site", s + "/vllm", FIX_VERSIONS.split(","), FIX_COMMIT)

    def close(self):
        shutil.rmtree(self.d, ignore_errors=True)


def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield path + "/" + k, k, v
            yield from walk(v, path + "/" + k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, "%s[%d]" % (path, i))


class Generate(unittest.TestCase):
    def setUp(self):
        self.s = Scratch()

    def tearDown(self):
        self.s.close()

    def test_output_is_the_golden_and_stable(self):
        a, b = gen_cli(), gen_cli()
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(a.stdout, (FIX / "artifact.json").read_text())
        self.assertEqual(a.stdout, b.stdout)
        self.assertEqual(artifact.dumps(self.s.generate()), a.stdout)

    def test_yaml_scalars_parse_as_js_yaml_4_1_1(self):
        def js(o):
            if isinstance(o, float) and not math.isfinite(o):
                return jsyaml.js_str(o)
            if isinstance(o, list):
                return [js(x) for x in o]
            if isinstance(o, dict):
                return {k: js(v) for k, v in o.items()}
            return o
        got = js(jsyaml.load(FIX / "scalars.yaml"))
        want = json.loads((FIX / "scalars.js.json").read_text())
        self.assertEqual(got, want)
        self.assertEqual(list(got["keys"]), list(want["keys"]), "JS own-key order: index keys first")
        self.assertIs(type(got["b"][6]), bool)
        self.assertEqual(got["b"][:6], ["yes", "no", "on", "off", "y", "n"], "YAML 1.1 booleans are strings")

    def test_schema_version_2_is_an_error(self):
        (self.s.src / "recipes" / "manifest.yaml").write_text("schema_version: 2\n")
        self.s.relock()
        with self.assertRaisesRegex(artifact.GenError, "schema_version 2"):
            self.s.generate()

    def test_a_tree_other_than_the_locked_one_is_an_error(self):
        self.s.edit("Qwen/Qwen3-4B.yaml", lambda t: t + "\n# edited\n")
        with self.assertRaisesRegex(artifact.GenError, "sources.lock.json locks"):
            self.s.generate()

    def test_a_lock_naming_another_repo_is_an_error(self):
        self.s.relock(repo="someone/vllm")
        with self.assertRaisesRegex(artifact.GenError, "not vllm-project/vllm:recipes"):
            self.s.generate()

    def test_a_license_other_than_apache_is_an_error(self):
        (self.s.src / "site" / "LICENSE").write_text("MIT License\n")
        with self.assertRaisesRegex(artifact.GenError, "not the Apache License"):
            self.s.generate()

    def test_bad_recipes_are_errors(self):
        cases = [
            ("Qwen/Qwen3-4B.yaml", lambda t: t.replace('  model_id: "Qwen/Qwen3-4B"\n', ""), "no model.model_id"),
            ("Qwen/Qwen3-4B.yaml", lambda t: t.replace("  base_args: []\n", "  base_args: \"--x\"\n"), "base_args: a list"),
            ("Qwen/Qwen3-4B.yaml", lambda t: t.replace("  base_env: {}\n", "  base_env: {A: [1]}\n"), "base_env.A: a scalar"),
        ]
        for rel, fn, msg in cases:
            with self.subTest(msg=msg):
                s = Scratch()
                try:
                    s.edit(rel, fn)
                    s.relock()
                    with self.assertRaisesRegex(artifact.GenError, msg):
                        s.generate()
                finally:
                    s.close()
        shutil.copy(self.s.recipe("Qwen/Qwen3-4B.yaml"), self.s.recipe("Qwen/qwen3-4b.yaml"))
        self.s.relock()
        with self.assertRaisesRegex(artifact.GenError, "same id in lowercase"):
            self.s.generate()

    def test_an_intel_preset_is_unported(self):
        self.s.edit("Qwen/Qwen3-4B.yaml", lambda t: t + (
            "\npresets:\n  b70_fast:\n    label: Fast\n    hardware: arc_pro_b70\n    args: [\"--max-num-seqs\", \"8\"]\n"))
        self.s.relock()
        doc = self.s.generate()
        self.assertEqual([(u["model"], u["scope"], u["hardware"]) for u in doc["unported"]],
                         [("Qwen/Qwen3-4B", "presets.b70_fast", "arc_pro_b70")])
        self.assertNotIn("--max-num-seqs", json.dumps(doc["models"]["qwen/qwen3-4b"]))

    def test_an_nvidia_only_edit_leaves_the_content(self):
        before = self.s.generate()

        def nvidia(t):
            t = t.replace("\nvariants:\n", "\nhardware_overrides:\n  hopper:\n    extra_args: [\"--async-scheduling\"]\n"
                          "\nvariants:\n", 1) if "\nhardware_overrides:" not in t else t
            t = t.replace("  default:\n", "  default:\n    docker_image: vllm/vllm-openai:nightly\n"
                          "    hardware_overrides:\n      h200:\n        extra_args: [\"--max-num-seqs\", \"64\"]\n", 1)
            return t + "\npresets:\n  h200_fast:\n    hardware: h200\n    args: [\"--max-num-seqs\", \"8\"]\n"
        self.s.edit("Qwen/Qwen3-4B.yaml", nvidia)
        self.s.edit("Qwen/Qwen3-8B.yaml", lambda t: t.replace(
            "  tool_calling:\n", "  tool_calling:\n    hardware_overrides:\n      b200:\n        args: [\"--x\"]\n"
            "    hardware:\n      gb200: unsupported\n", 1))
        self.s.relock()
        after = self.s.generate()
        self.assertNotEqual(before["vllm_tree"], after["vllm_tree"])
        self.assertEqual(artifact.content(before), artifact.content(after))

    def test_no_image_install_or_prose_reaches_the_artifact(self):
        doc = json.loads((FIX / "artifact.json").read_text())
        dropped = {"docker_image", "docker_serve_command", "install", "install_note", "guide", "description", "label",
                   "presets", "tp", "docker_source", "meta"}
        self.assertEqual(sorted({k for _, k, _ in walk(doc["models"]) if k in dropped}), [])
        self.assertTrue(any("docker_image" in p.read_text() for p in (FIX / "recipes" / "models").rglob("*.yaml")),
                        "the fixture holds docker_image keys to drop")
        text = json.dumps(doc)
        for s in ("vllm/vllm-openai", ":latest", "sha256:"):
            self.assertNotIn(s, text)

    def test_hardware_keyed_maps_hold_intel_keys_only(self):
        doc = json.loads((FIX / "artifact.json").read_text())
        ids = set(doc["profiles"])
        self.assertEqual(ids, {"arc_pro_b60", "arc_pro_b70", "xeon5", "xeon6"})
        self.assertEqual({p["generation"] for p in doc["profiles"].values()}, {"cpu", "xpu"})
        for m in doc["models"].values():
            self.assertLessEqual(set(m["hardware"]), ids, m["id"])
            self.assertLessEqual(set(m["overrides"]), {"cpu", "xpu"}, m["id"])
            for v in m["variants"].values():
                self.assertLessEqual(set(v["overrides"]), ids | {"cpu", "xpu"}, m["id"])
                self.assertLessEqual(set(v["supported"]), ids, m["id"])
        llama = doc["models"]["meta-llama/llama-3.3-70b-instruct"]
        self.assertEqual(llama["variants"]["default"]["overrides"],
                         {"xpu": {"args": ["--quantization", "fp8", "--kv-cache-dtype", "fp8"]}})
        self.assertEqual(doc["variant_index"]["redhatai/qwen3.5-4b-fp8-dynamic"], ["qwen/qwen3.5-4b", "fp8"])
        self.assertNotIn("qwen/qwen3.5-4b", doc["variant_index"])
        gemma = doc["models"]["google/gemma-4-26b-a4b-it"]["features"]["spec_decoding"]
        self.assertEqual(gemma["default_mode"], "mtp")
        self.assertEqual(gemma["mode_order"], ["mtp", "eagle3"])


class Sources(unittest.TestCase):
    def test_parser_names_from_a_registry_module(self):
        tree = surface.ast.parse('''
"""
    Manager.register_lazy_module(name="in_a_docstring", module_path="x", class_name="Y")
"""
_TOOL_PARSERS_TO_REGISTER = {"hermes": ("hermes_tool_parser", "Hermes"), "qwen3_coder": ("a", "B")}
OTHER = {"not_me": 1}
Manager.register_lazy_module(name="plugin_kw", module_path="m", class_name="C")
Manager.register_lazy_module("plugin_pos", "m", "C")
for name, (f, c) in _TOOL_PARSERS_TO_REGISTER.items():
    Manager.register_lazy_module(name, f, c)
''')
        self.assertEqual(surface.parser_names(tree, "_TOOL_PARSERS_TO_REGISTER"),
                         {"hermes", "qwen3_coder", "plugin_kw", "plugin_pos"})

    def test_engine_reads_parsers_and_serve_flags(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="vllm_recipes_engine."))
        try:
            files = {
                "vllm/tool_parsers/__init__.py": '_TOOL_PARSERS_TO_REGISTER = {"hermes": ("a", "B")}\n',
                "vllm/reasoning/__init__.py": '_REASONING_PARSERS_TO_REGISTER = {"qwen3": ("a", "B")}\n',
                "vllm/engine/arg_utils.py": 'p.add_argument("--max-model-len")\np.add_argument("--enable_prefix_caching")\n',
                "vllm/entrypoints/openai/cli_args.py": (
                    'p.add_argument("--tool-call-parser")\n'
                    'class FrontendArgs:\n    enable_auto_tool_choice: bool = False\n    _private: int = 0\n'),
            }
            for rel, text in files.items():
                (d / rel).parent.mkdir(parents=True, exist_ok=True)
                (d / rel).write_text(text)
            self.assertEqual(artifact.engine(str(d)), {
                "tool_parsers": ["hermes"], "reasoning_parsers": ["qwen3"],
                "flags": ["--enable-auto-tool-choice", "--enable-prefix-caching", "--max-model-len",
                          "--tool-call-parser"]})
            (d / "vllm/reasoning/__init__.py").write_text("_REASONING_PARSERS_TO_REGISTER = {}\n")
            with self.assertRaisesRegex(artifact.GenError, "registers no parsers"):
                artifact.engine(str(d))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_versions_of_this_tree(self):
        plan = (ROOT / "console" / "plan.py").read_text()
        agent = re.search(r'^AGENT_VLLM_VERSION = \{"HPU": "([0-9.]+)", "GPU": "([0-9.]+)"\}', plan, re.M).groups()
        got = source.versions(str(ROOT))
        self.assertLessEqual(set(agent), set(got))
        self.assertEqual(len(got), len(set(got)))
        self.assertEqual(run(TOOL, "versions").stdout.split(), got)


class ShippedArtifact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = ART.read_text(encoding="utf-8")
        cls.doc = json.loads(cls.text)

    def test_bytes_schema_license_size(self):
        self.assertEqual(artifact.dumps(self.doc), self.text, "canonical bytes (hack/vllm-recipes update)")
        self.assertEqual(self.doc["schema"], "k3d-gpu.vllm-recipes/v1")
        self.assertEqual(self.doc["license"]["spdx"], "Apache-2.0")
        self.assertRegex(self.doc["license"]["text"], artifact.LICENSE_HEAD)
        self.assertEqual(self.doc["license"]["sources"], [
            "https://github.com/vllm-project/vllm/tree/%s/recipes" % self.doc["vllm_commit"],
            "https://github.com/vllm-project/recipes/tree/%s" % self.doc["recipes_commit"]])
        self.assertLess(len(self.text.encode()), 1 << 20)

    def test_the_pin_sites_read_the_artifact(self):
        from pinlib import registry
        reg = registry.load(str(ROOT / "pins" / "registry.toml"))
        pin = next(p for p in reg["pin"] if p["id"] == "vllm-recipes")
        self.assertEqual((pin["family"], pin["writer"], pin["scheme"]), ("recipes", "hook", "hf-sha"))
        cur = registry.current(pin, registry.locate(registry.Tree(str(ROOT)), pin))
        self.assertEqual((cur["v"], cur["vllm_commit"], cur["vllm_tree"]),
                         (self.doc["recipes_commit"], self.doc["vllm_commit"], self.doc["vllm_tree"]))
        groups = [g for g, conf in reg["groups"].items() if "vllm-recipes" in conf.get("hooks", [])]
        for g in groups:
            self.assertIn("console/vllm-recipes.json", reg["groups"][g].get("outputs", []), g)
        self.assertLessEqual({"vllm-recipes", "vllm-xpu", "vllm-cpu", "llm-scaler", "gaudi-vllm", "eat-stock"},
                             set(groups))

    def test_one_engine_per_pinned_vllm_version(self):
        self.assertEqual(sorted(self.doc["engines"], key=lambda v: [int(x) for x in v.split(".")]),
                         source.versions(str(ROOT)), "regenerate: hack/vllm-recipes update")
        for v, e in self.doc["engines"].items():
            self.assertTrue(e["tool_parsers"] and e["reasoning_parsers"], v)
            self.assertLessEqual({"--tool-call-parser", "--reasoning-parser", "--enable-auto-tool-choice",
                                  "--chat-template", "--enforce-eager"}, set(e["flags"]), v)

    def test_default_model_parsers_agree_with_the_launcher(self):
        k3d = (ROOT / "scripts" / "k3d-gpu").read_text()
        default = re.search(r'^ +AGENT_DEFAULT_MODEL="([^"]+)"', k3d, re.M).group(1)
        m = self.doc["models"][default.lower()]
        self.assertEqual(m["features"]["tool_calling"]["args"], ["--enable-auto-tool-choice", "--tool-call-parser", "hermes"])
        self.assertEqual(m["features"]["reasoning"]["args"], ["--reasoning-parser", "qwen3"])
        self.assertIn("arc_pro_b70", self.doc["profiles"])
        self.assertEqual(self.doc["unported"], [u for u in self.doc["unported"] if u["hardware"] in self.doc["profiles"]])


class Bump(unittest.TestCase):
    """hack/bump and resolve in a scratch repo, the fixture standing in for git (VLLM_RECIPES_SRC)."""

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="vllm_recipes_bump."))
        r = self.r = self.d / "repo"
        for rel in ("hack/vllm-recipes", "canary/surface.py"):
            (r / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, r / rel)
        for pkg in ("vllmrecipes", "pinlib"):
            shutil.copytree(ROOT / "hack" / pkg, r / "hack" / pkg, ignore=shutil.ignore_patterns("__pycache__"))
        files = {
            "pins/registry.toml": (ROOT / "pins" / "registry.toml").read_text().split("# ---- groups")[0] + '''
[groups.vllm-recipes]
tests = []
hooks = ["vllm-recipes"]
outputs = ["console/vllm-recipes.json"]

''' + re.search(r'(?ms)^\[\[pin\]\]\nid = "vllm-recipes"\n.*?^\]\n', (ROOT / "pins" / "registry.toml").read_text()).group(0),
            "console/plan.py": 'AGENT_VLLM_VERSION = {"HPU": "0.32.0", "GPU": "0.33.0"}\n'
                               'v = "0.26.0" if backend == "llm-scaler" else None\n',
            "scripts/k3d-gpu": 'AMX_TEST_IMAGE="${K3D_GPU_AMX_TEST_IMAGE:-vllm/vllm-openai-cpu:v0.32.0@sha256:%s}"\n' % ("0" * 64),
            "integrations/eat/vllm-values.yaml": 'image:\n  repository: vllm/vllm-openai-cpu\n  tag: "v0.33.0@sha256:%s"\n'
                                                 % ("0" * 64),
            "console/vllm-recipes.json": (FIX / "artifact.json").read_text(),
            "EXPERIMENTS.md": "", "images.unpinned": "",
        }
        for rel, text in files.items():
            (r / rel).parent.mkdir(parents=True, exist_ok=True)
            (r / rel).write_text(text)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.env = {"VLLM_RECIPES_SRC": str(FIX)}

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.r), *args], check=True, capture_output=True, text=True,
                              env=GIT_ENV).stdout

    def test_bump_lands_the_resolved_pair_with_notes_and_review(self):
        cur = json.loads((FIX / "artifact.json").read_text())
        resolved = self.d / "resolved.json"
        resolved.write_text(json.dumps({"vllm-recipes": {"v": NEW_COMMIT, "vllm_commit": cur["vllm_commit"],
                                                         "vllm_tree": cur["vllm_tree"]}}))
        summary = self.d / "summary.json"
        p = run(ROOT / "hack" / "bump", "vllm-recipes", "--resolved", resolved, "--no-tests", "--summary", summary,
                "--root", self.r, env=self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        doc = json.loads((self.r / "console" / "vllm-recipes.json").read_text())
        self.assertEqual(doc["recipes_commit"], NEW_COMMIT)
        self.assertEqual(artifact.content(doc), artifact.content(cur))
        s = json.loads(summary.read_text())
        self.assertEqual(s["moved"], [{"id": "vllm-recipes", "from": FIX_COMMIT, "to": NEW_COMMIT}])
        self.assertIn("vllm-recipes: recipes_commit %s -> %s" % (FIX_COMMIT[:12], NEW_COMMIT[:12]), s["notes"])
        self.assertEqual(s["verdict"]["verdict"], "review")
        self.assertTrue(any("family recipes" in x for x in s["verdict"]["reasons"]), s["verdict"])
        self.git("add", "-A")
        self.git("commit", "-qm", "bump")
        p = run(ROOT / "hack" / "pin-only-diff", "--root", self.r, "--group", "vllm-recipes", "HEAD~1", "HEAD")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_resolve_keeps_the_pin_unless_the_content_moves(self):
        fixtures = self.d / "gh.json"
        fixtures.write_text(json.dumps({"https://api.github.com/repos/vllm-project/recipes/commits/main":
                                        {"status": 200, "json": {"sha": NEW_COMMIT}}}))
        env = dict(self.env, PINLIB_FIXTURES=str(fixtures))
        cur = json.loads((FIX / "artifact.json").read_text())
        want = "v=%s\nvllm_commit=%s\nvllm_tree=%s\n"
        p = run(self.r / "hack" / "vllm-recipes", "resolve", env=env)
        self.assertEqual((p.returncode, p.stdout), (0, want % (FIX_COMMIT, cur["vllm_commit"], cur["vllm_tree"])), p.stderr)
        moved = copy.deepcopy(cur)
        del moved["models"]["qwen/qwen3-4b"]
        (self.r / "console" / "vllm-recipes.json").write_text(artifact.dumps(moved))
        p = run(self.r / "hack" / "vllm-recipes", "resolve", env=env)
        self.assertEqual((p.returncode, p.stdout), (0, want % (NEW_COMMIT, cur["vllm_commit"], cur["vllm_tree"])), p.stderr)
        p = run(self.r / "hack" / "vllm-recipes", "diff", "--base-ref", "HEAD", env=env)
        self.assertEqual(p.stdout, "model removed: Qwen/Qwen3-4B\n")

    def test_family_recipes_is_always_review(self):
        from pinlib import policy
        self.assertEqual([t for t in policy.SEVERITY if policy.family_allows("recipes", t)], [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
