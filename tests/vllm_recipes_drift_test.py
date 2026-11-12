#!/usr/bin/env python3
"""Tests for `hack/vllm-recipes drift` (hack/vllmrecipes/drift.py): k3d-gpu's own vLLM
arguments on an Intel GPU (xpu) and a Xeon (cpu) next to the recipes' hardware_overrides
for that generation, each difference labelled with its reason or UNEXPLAINED. It reports
and applies nothing. Run by tests/vllm_recipes_test.sh. No network.

  - on the frozen fixture artifact (tests/fixtures/vllm-recipes/artifact.json) the text
    report is drift.golden, byte for byte, twice
  - k3d-gpu's side is read from the tree: the Intel GPU branch of vllm_device_args, the
    tensor-parallel item of vllm_base_lines and VLLM_XPU_ENV, rendered with no K3D_GPU_* set,
    and the vllm-cpu canary engine's arguments
  - names and values compare as vLLM reads them: --a_b is --a-b, --no-X is X off, 0.90 is 0.9
  - a flag differs both ways; an env name only where a recipe block sets it
  - every DRIFT_REASONS entry is used by a difference on the fixture (a stale reason fails),
    cites a file in the tree that holds each name the source gives after it (a ledger
    reason: a SUPPORTED or REFUTED EXPERIMENTS.md row), and has a known kind; review
    prints REVIEW
  - --base/--base-ref prints only the differences that moved, for a bump PR's notes
  - the shipped artifact's drift is printed into the log and never fails the run

Usage: python3 tests/vllm_recipes_drift_test.py
"""

import copy
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "vllm-recipes"
TOOL = ROOT / "hack" / "vllm-recipes"
sys.path.insert(0, str(ROOT / "hack"))
sys.dont_write_bytecode = True

from vllmrecipes import drift  # noqa: E402

_spec = importlib.util.spec_from_file_location("ledger_drift_check", ROOT / "tests" / "ledger_drift_check.py")
LEDGER = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(LEDGER)


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True, text=True, cwd=ROOT)


def fixture():
    return json.loads((FIX / "artifact.json").read_text())


def one_model(gen, args=None, env=None, variant=None):
    """An artifact holding one recipe whose `gen` block is args and env (or, with variant,
    that variant's block)."""
    art = fixture()
    m = copy.deepcopy(art["models"]["qwen/qwen3-4b"])
    m["overrides"] = {}
    block = {"args": args or []}
    if env:
        block["env"] = env
    if variant:
        m["variants"][variant]["overrides"] = {gen: block}
    else:
        m["overrides"] = {gen: block}
    art["models"] = {"qwen/qwen3-4b": m}
    return art


def rows(art, gen):
    return {r["name"]: r for r in drift.report(art, ROOT)["generations"][gen]["rows"]}


def citation_problems(root, reason):
    """What a reason's `source` cites that the tree does not hold: the file, each name
    after it (a function or variable the file must contain), and for a ledger reason a
    SUPPORTED or REFUTED row of EXPERIMENTS.md's Hypotheses table (read as
    tests/ledger_drift_check.py reads it)."""
    path, *names = reason["source"].split()
    if not (root / path).is_file():
        return ["%s is not a file in the tree" % path]
    if reason["kind"] == "ledger":
        if path != "EXPERIMENTS.md" or len(names) != 1:
            return ["a ledger reason cites EXPERIMENTS.md <row>, not %r" % reason["source"]]
        verdict = LEDGER.ledger(root).get(names[0])
        return [] if verdict in ("SUPPORTED", "REFUTED") else ["EXPERIMENTS.md row %s is %s" % (names[0], verdict)]
    text = (root / path).read_text(encoding="utf-8")
    return ["%s does not contain %s" % (path, n) for n in names if n not in text]


class Golden(unittest.TestCase):
    def test_fixture_report_is_the_golden_and_stable(self):
        a = run("drift", "--artifact", FIX / "artifact.json")
        b = run("drift", "--artifact", FIX / "artifact.json")
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(a.stdout, (FIX / "drift.golden").read_text())
        self.assertEqual(a.stdout, b.stdout)

    def test_json_carries_the_same_rows(self):
        p = run("drift", "--json", "--artifact", FIX / "artifact.json")
        self.assertEqual(p.returncode, 0, p.stderr)
        doc = json.loads(p.stdout)
        self.assertEqual(doc, json.loads(json.dumps(drift.report(fixture(), ROOT))))
        statuses = {r["status"] for g in doc["generations"].values() for r in g["rows"]}
        self.assertEqual(statuses, {"agree", "explained", "review", "unexplained"})


class K3dGpuSide(unittest.TestCase):
    def test_xpu_is_the_intel_gpu_branch_rendered_with_no_env(self):
        flags, env, _ = drift.k3d_xpu(ROOT)
        self.assertEqual(flags, {"--dtype": "float16", "--block-size": "64", "--max-model-len": "8192",
                                 "--max-num-batched-tokens": "2048", "--gpu-memory-utilization": "0.9",
                                 "--enforce-eager": "on", "--enable-prefix-caching": "off",
                                 "--tensor-parallel-size": "1"})
        self.assertEqual(env["VLLM_WORKER_MULTIPROC_METHOD"], "spawn")
        self.assertEqual(env["ONEAPI_DEVICE_SELECTOR"], "level_zero:*")
        self.assertEqual(env["CCL_WORKER_AFFINITY"], "")
        self.assertNotIn("TORCH_LLM_ALLREDUCE", env)

    def test_cpu_is_the_vllm_cpu_canary_engine(self):
        flags, env, _ = drift.k3d_cpu(ROOT)
        self.assertEqual(flags, {"--dtype": "bfloat16", "--max-model-len": "2048", "--max-num-seqs": "1"})
        self.assertEqual(env, {"VLLM_CPU_KVCACHE_SPACE": "1"})

    def test_a_launcher_without_the_gpu_branch_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "scripts").mkdir()
            (pathlib.Path(d) / "scripts" / "k3d-gpu").write_text("vllm_manifest() {\n    :\n}\n")
            with self.assertRaises(drift.DriftError):
                drift.k3d_xpu(d)


class Compare(unittest.TestCase):
    def test_values_compare_as_vllm_reads_them(self):
        r = rows(one_model("xpu", ["--gpu-memory-utilization", "0.90", "--no-enable-prefix-caching",
                                   "--max_model_len", "8192", "--block-size", "32"]), "xpu")
        self.assertEqual(r["--gpu-memory-utilization"]["status"], "agree")
        self.assertEqual(r["--enable-prefix-caching"]["status"], "agree")
        self.assertEqual(r["--max-model-len"]["status"], "agree")
        self.assertEqual(r["--block-size"]["status"], "unexplained")
        self.assertEqual(r["--block-size"]["k3d_gpu"], "64")
        self.assertEqual(r["--block-size"]["recipes"], [{"value": "32", "count": 1, "models": ["Qwen/Qwen3-4B"]}])

    def test_a_flag_differs_both_ways(self):
        r = rows(one_model("xpu", ["--enable-prefix-caching", "--attention-backend", "TRITON_ATTN"]), "xpu")
        self.assertEqual((r["--enable-prefix-caching"]["k3d_gpu"], r["--enable-prefix-caching"]["status"]),
                         ("off", "unexplained"))
        self.assertEqual((r["--attention-backend"]["k3d_gpu"], r["--attention-backend"]["status"]),
                         (None, "unexplained"))
        self.assertEqual((r["--dtype"]["recipes"], r["--dtype"]["status"]), ([], "review"))

    def test_env_only_where_a_recipe_sets_it(self):
        r = rows(one_model("xpu", [], {"VLLM_WORKER_MULTIPROC_METHOD": "fork", "PYTHONUNBUFFERED": "1"}), "xpu")
        self.assertEqual((r["VLLM_WORKER_MULTIPROC_METHOD"]["k3d_gpu"], r["VLLM_WORKER_MULTIPROC_METHOD"]["status"]),
                         ("spawn", "unexplained"))
        self.assertEqual((r["PYTHONUNBUFFERED"]["k3d_gpu"], r["PYTHONUNBUFFERED"]["kind"]), (None, "env"))
        self.assertNotIn("CCL_ALLREDUCE", r)

    def test_variant_and_profile_blocks_count_and_other_generations_do_not(self):
        art = one_model("xpu", ["--quantization", "fp8"], variant="default")
        art["models"]["qwen/qwen3-4b"]["overrides"] = {"arc_pro_b70": {"args": ["--block-size", "16"]},
                                                       "cpu": {"args": ["--block-size", "8"]}}
        r = rows(art, "xpu")
        self.assertEqual(r["--quantization"]["recipes"][0]["models"], ["Qwen/Qwen3-4B variants.default"])
        self.assertEqual(r["--block-size"]["recipes"], [{"value": "16", "count": 1,
                                                         "models": ["Qwen/Qwen3-4B [arc_pro_b70]"]}])

    def test_a_reason_labels_the_difference(self):
        r = rows(one_model("xpu", ["--max-model-len", "5120"]), "xpu")
        self.assertEqual(r["--max-model-len"]["status"], "explained")
        self.assertEqual(r["--max-model-len"]["reason"], drift.DRIFT_REASONS[("xpu", "--max-model-len")])
        text = drift.render(drift.report(one_model("xpu", ["--max-model-len", "5120"]), ROOT))
        self.assertIn("\n  --max-model-len: k3d-gpu 8192; recipes 5120 x1 (Qwen/Qwen3-4B) -> explained [fit, ", text)
        self.assertRegex(text, r"(?m)^  --dtype: k3d-gpu float16; recipes unset -> REVIEW \[")
        self.assertIn("\n  --block-size: k3d-gpu 64; recipes unset -> UNEXPLAINED\n", text)


class Reasons(unittest.TestCase):
    def test_no_reason_is_stale_on_the_fixture(self):
        used = {(g, r["name"]) for g, doc in drift.report(fixture(), ROOT)["generations"].items()
                for r in doc["rows"] if r["status"] in ("explained", "review")}
        self.assertEqual(sorted(set(drift.DRIFT_REASONS) - used), [])

    def test_each_reason_cites_a_file_in_the_tree_and_a_known_kind(self):
        for key, r in drift.DRIFT_REASONS.items():
            self.assertIn(r["kind"], drift.KINDS, key)
            self.assertTrue(r["why"], key)
            self.assertEqual(citation_problems(ROOT, r), [], key)

    def test_a_citation_is_checked_against_what_it_names(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "console").mkdir()
            (root / "console" / "fit.py").write_text("def _flags():\n    pass\n")
            (root / "EXPERIMENTS.md").write_text("## Hypotheses\n\n| # | Claim | Verdict |\n|---|---|---|\n"
                                                 "| H1 | a | REFUTED |\n| H2 | b | INCONCLUSIVE |\n")
            for source, kind, ok in (("console/fit.py _flags", "fit", True),
                                     ("console/fit.py", "fit", True),
                                     ("console/fit.py _sizes", "fit", False),
                                     ("console/gone.py", "fit", False),
                                     ("EXPERIMENTS.md H1", "ledger", True),
                                     ("EXPERIMENTS.md H2", "ledger", False),
                                     ("EXPERIMENTS.md H3", "ledger", False),
                                     ("console/fit.py _flags", "ledger", False)):
                got = citation_problems(root, {"kind": kind, "source": source})
                self.assertEqual(got == [], ok, (source, kind, got))


class Moves(unittest.TestCase):
    def test_base_prints_only_what_moved(self):
        with tempfile.TemporaryDirectory() as d:
            base, head = pathlib.Path(d) / "base.json", pathlib.Path(d) / "head.json"
            art = fixture()
            base.write_text(json.dumps(art))
            p = run("drift", "--base", base, "--artifact", base)
            self.assertEqual((p.returncode, p.stdout), (0, "no drift change\n"), p.stderr)
            m = art["models"]["qwen/qwen3-4b"]
            m["overrides"] = {"xpu": {"args": ["--attention-backend", "TRITON_ATTN", "--block-size", "32"]}}
            del art["models"]["meta-llama/llama-3.3-70b-instruct"]
            head.write_text(json.dumps(art))
            p = run("drift", "--base", base, "--artifact", head)
            self.assertEqual(p.returncode, 0, p.stderr)
            lines = p.stdout.splitlines()
            self.assertIn("gone: xpu --kv-cache-dtype", lines)
            self.assertIn("gone: xpu TORCH_LLM_ALLREDUCE", lines)
            self.assertTrue(any(ln.startswith("new: xpu --block-size: k3d-gpu 64; recipes 32 x1 (Qwen/Qwen3-4B)")
                                for ln in lines), lines)
            self.assertTrue(any(ln.startswith("changed: xpu --attention-backend: k3d-gpu unset; recipes")
                                and "TRITON_ATTN x1" in ln for ln in lines), lines)
            self.assertFalse([ln for ln in lines if not re.match(r"(new|changed|gone): (xpu|cpu) ", ln)], lines)


class Shipped(unittest.TestCase):
    def test_the_shipped_artifact_is_printed_never_failed(self):
        p = run("drift")
        sys.stdout.write("\n---- hack/vllm-recipes drift on console/vllm-recipes.json (printed only) ----\n")
        sys.stdout.write(p.stdout + p.stderr)
        sys.stdout.write("---- rc %d ----\n" % p.returncode)


if __name__ == "__main__":
    unittest.main(verbosity=1)
