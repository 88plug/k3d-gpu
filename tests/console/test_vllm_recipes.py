#!/usr/bin/env python3
"""Tests for console/recipes.py, the offline reader of vLLM's model recipes. No network.

Fixtures (tests/fixtures/vllm-recipes/, frozen, so a recipes bump moves no golden):
  artifact.json    hack/vllm-recipes generate over the fixture recipes, site and vLLM
                   registries (tests/vllm_recipes_gen_test.py proves the bytes)
  site-intel.json  recipes.vllm.ai's own command for every fixture model x Intel profile
                   it renders (single_node_tp, one node, default features and modes)

The real console/vllm-recipes.json is checked only for the facts every pin must keep:
every adoptable flag exists in every pinned vLLM version.
"""

import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "console"))

import recipes  # noqa: E402

FIX = os.path.join(REPO, "tests", "fixtures", "vllm-recipes")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


ART = load("artifact.json")
SITE = load("site-intel.json")
V = max(ART["engines"], key=recipes.version)  # the newest pinned vLLM in the fixture

# The device base vllm_manifest renders on an Intel GPU (scripts/k3d-gpu vllm_device_args).
GPU_BASE = [("Qwen/Qwen3-8B", "user env K3D_GPU_AGENT_MODEL"), ("--served-model-name=Qwen3-8B", "k3d-gpu default"),
            ("--port=8000", "k3d-gpu default"), ("--dtype=float16", "k3d-gpu default (vllm_device_args)"),
            ("--block-size=64", "k3d-gpu default (vllm_device_args)"), ("--max-model-len=8192", "k3d-gpu default"),
            ("--max-num-batched-tokens=2048", "k3d-gpu default (vllm_device_args)"),
            ("--gpu-memory-utilization=0.9", "k3d-gpu default (vllm_device_args)"),
            ("--enforce-eager", "k3d-gpu default (vllm_device_args)"),
            ("--no-enable-prefix-caching", "k3d-gpu default (vllm_device_args)"),
            ("--tensor-parallel-size=1", "k3d-gpu default")]
HPU_BASE = [("Qwen/Qwen3.5-4B", "user env K3D_GPU_AGENT_MODEL"), ("--dtype=bfloat16", "k3d-gpu default (vllm_device_args)"),
            ("--max-model-len=8192", "k3d-gpu default"), ("--tensor-parallel-size=1", "k3d-gpu default")]
NO_PARSERS = {"tool": ("", ""), "reasoning": ("", "")}


def facts(model, device="GPU", pci="0xe223", version=V, **kw):
    return recipes.facts(ART, model, device=device, pci_id=pci, engine_version=version, **kw)


def merged(model, device="GPU", pci="0xe223", base=GPU_BASE, parsers=None, version=V, art=ART, **kw):
    r = recipes.facts(art, model, device=device, pci_id=pci, engine_version=version, config=kw.pop("config", None))
    return r, recipes.merge(art, base, parsers=parsers or NO_PARSERS, facts_result=r, **kw)


def strip_tp(argv):
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--tensor-parallel-size":
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


class Faithful(unittest.TestCase):
    def test_site_commands_reproduced(self):
        # With no k3d-gpu policy, the artifact gives back what recipes.vllm.ai renders.
        self.assertGreater(len(SITE["rows"]), 20)
        for row in SITE["rows"]:
            e = ART["models"][row["model"].lower()]
            argv, env = recipes.render(ART, e, row["variant"], row["profile"])
            with self.subTest(model=row["model"], profile=row["profile"], variant=row["variant"]):
                self.assertEqual(strip_tp(argv), row["argv"])
                self.assertEqual(env, row["env"])


class Lookup(unittest.TestCase):
    def test_recipe_id_any_case(self):
        e, vk, matched, how = recipes.lookup(ART, ["qwen/QWEN3.5-4b"])
        self.assertEqual((e["id"], vk, how), ("Qwen/Qwen3.5-4B", "default", "recipe"))

    def test_variant_index(self):
        e, vk, matched, how = recipes.lookup(ART, ["RedHatAI/Qwen3.5-4B-FP8-dynamic"])
        self.assertEqual((e["id"], vk, how), ("Qwen/Qwen3.5-4B", "fp8", "variant of Qwen/Qwen3.5-4B"))

    def test_optimizer_target_last(self):
        e, vk, matched, how = recipes.lookup(ART, ["acme/Qwen3.8-27B-int4-custom", None, "Qwen/Qwen3.8-27B"])
        self.assertEqual((e["id"], vk, matched, how), ("Qwen/Qwen3.8-27B", "default", "Qwen/Qwen3.8-27B", "optimizer target"))

    def test_no_recipe(self):
        self.assertIsNone(recipes.lookup(ART, ["acme/never-heard-of"]))
        self.assertIsNone(facts("acme/never-heard-of"))


class Target(unittest.TestCase):
    def test_pci_profiles(self):
        self.assertEqual(recipes.target_for("GPU", "0xe223"), ("arc_pro_b70", "xpu"))
        self.assertEqual(recipes.target_for("GPU", "0xE211"), ("arc_pro_b60", "xpu"))
        self.assertEqual(recipes.target_for("GPU", "0xe20b"), (None, "xpu"))
        self.assertEqual(recipes.target_for("GPU"), (None, "xpu"))
        self.assertEqual(recipes.target_for("HPU"), (None, None))
        self.assertEqual(recipes.target_for("CPU", cpu_brand="Intel(R) Xeon(R) 6960P"), ("xeon6", "cpu"))
        self.assertEqual(recipes.target_for("CPU", cpu_brand="Intel(R) Core(TM) i9-13900T"), (None, "cpu"))


class Policy(unittest.TestCase):
    def test_sizing_never_adopted(self):
        # Gemma's xpu block sizes a 4-card node: fit-owned, reported only.
        r, m = merged("Google/gemma-4-12B-it", config={"auto_map": {}})
        self.assertNotIn("--max-model-len=5120", m["add"])
        self.assertFalse(any(a.startswith(("--tensor-parallel-size", "--max-num-batched-tokens", "--gpu-memory-utilization"))
                             for a in m["add"]))
        owned = {it["name"]: it for it in r["items"] if it["kind"] == "owned"}
        self.assertEqual(owned["max-model-len"]["class"], "sizing")
        self.assertEqual(owned["tensor-parallel-size"]["action"], "report")
        self.assertTrue(any("max-model-len=5120" in x and "fit-owned" in x for x in m["explain"]))

    def test_disable_sliding_window_only(self):
        r, m = merged("Qwen/Qwen3-30B-A3B")
        self.assertEqual(m["add"], ["--disable-sliding-window"])
        self.assertEqual((m["tool_parser"], m["reasoning_parser"]), ("hermes", "qwen3"))

    def test_base_flag_not_duplicated(self):
        r, m = merged("Qwen/Qwen3-30B-A3B")
        args = [a for a, _ in m["args"]]
        self.assertEqual(args.count("--block-size=64"), 1)
        self.assertEqual(args.count("--enforce-eager"), 1)
        src = dict(m["args"])["--enforce-eager"]
        self.assertIn("k3d-gpu default", src)
        self.assertIn("recipe xpu agrees", src)

    def test_no_pair_rewrites_base(self):
        # Ling's --enable-prefix-caching meets the base's --no-enable-prefix-caching: the base stays.
        r, m = merged("inclusionAI/Ling-3.0-flash")
        self.assertNotIn("--enable-prefix-caching", m["add"])
        self.assertTrue(any("--enable-prefix-caching" in x and "the device base sets --no-enable-prefix-caching" in x
                            for x in m["explain"]))

    def test_device_flag_added(self):
        r, m = merged("Google/gemma-4-12B-it")
        self.assertIn("--attention-backend=flash_attn", m["add"])

    def test_plugin_drops_reasoning_keeps_tool(self):
        r, m = merged("nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16")
        self.assertEqual(m["tool_parser"], "qwen3_coder")
        self.assertEqual(m["reasoning_parser"], "")
        self.assertFalse(r["features"]["reasoning"]["applied"])
        self.assertIn("never fetches at boot", r["features"]["reasoning"]["why"])

    def test_parser_absent_from_registry(self):
        self.assertNotIn("ernie5", ART["engines"]["0.32.0"]["tool_parsers"])
        r, m = merged("baidu/ERNIE-5.5-Flash", version="0.32.0")
        self.assertEqual((m["tool_parser"], m["reasoning_parser"]), ("", ""))
        self.assertTrue(any("absent from vLLM 0.32.0" in w for w in r["warnings"]))

    def test_chat_template_unmapped_drops_only_the_pair(self):
        r, m = merged("Google/gemma-4-12B-it")
        self.assertEqual(m["tool_parser"], "gemma4")
        self.assertFalse(any(a.startswith("--chat-template") for a in m["add"]))
        self.assertTrue(any("--chat-template=examples/tool_chat_template_gemma4.jinja" in x and "model's own chat template" in x
                            for x in m["explain"]))

    def test_chat_template_mapped(self):
        saved = dict(recipes.EXAMPLES_DIR)
        try:
            recipes.EXAMPLES_DIR["vllm-xpu"] = "/workspace/vllm/examples"
            r = facts("Google/gemma-4-12B-it", engine="vllm-xpu")
            it = next(i for i in r["items"] if i["name"] == "chat-template")
            self.assertEqual((it["action"], it["value"]), ("adopt", "/workspace/vllm/examples/tool_chat_template_gemma4.jinja"))
            m = recipes.merge(ART, GPU_BASE, parsers=NO_PARSERS, facts_result=r)
            self.assertIn("--chat-template=/workspace/vllm/examples/tool_chat_template_gemma4.jinja", m["add"])
            self.assertIsNone(facts("Google/gemma-4-12B-it", engine="llm-scaler")["items"][-1].get("nothing"))
            other = next(i for i in facts("Google/gemma-4-12B-it", engine="llm-scaler")["items"] if i["name"] == "chat-template")
            self.assertEqual(other["action"], "report")
        finally:
            recipes.EXAMPLES_DIR.clear()
            recipes.EXAMPLES_DIR.update(saved)

    def test_trust_remote_code_needs_auto_map(self):
        r, m = merged("Qwen/Qwen3.5-4B", config={"architectures": ["Qwen3_5ForConditionalGeneration"]})
        self.assertNotIn("--trust-remote-code", m["add"])
        self.assertTrue(any("carries no auto_map" in x for x in m["explain"]))
        r, m = merged("Qwen/Qwen3.5-4B")
        self.assertTrue(any("no config.json available" in x for x in m["explain"]))
        r, m = merged("Qwen/Qwen3.5-4B", config={"auto_map": {"AutoConfig": "x.Config"}})
        self.assertIn("--trust-remote-code", m["add"])

    def test_env_wins_and_empty_turns_off(self):
        r, m = merged("Qwen/Qwen3-8B", parsers={"tool": ("llama3_json", "env"), "reasoning": ("", "env")})
        self.assertEqual((m["tool_parser"], m["reasoning_parser"]), ("llama3_json", ""))
        self.assertEqual(m["parser_sources"]["tool"], "user env K3D_GPU_AGENT_TOOL_PARSER")

    def test_recipe_beats_k3d_default(self):
        r, m = merged("Qwen/Qwen3-8B", parsers={"tool": ("", "model optimizer"), "reasoning": ("", "model optimizer")})
        self.assertEqual((m["tool_parser"], m["reasoning_parser"]), ("hermes", "qwen3"))
        self.assertTrue(m["parser_sources"]["tool"].startswith("recipe vllm@%s features.tool_calling" % recipes.sha12(ART)))

    def test_ledger_row(self):
        table = [{"family": "H99", "match": {"model": "Qwen/Qwen3-30B-A3B", "class_token": "Battlemage"},
                  "flag": "disable-sliding-window", "value": None},
                 {"family": "H98", "match": {"model": "Qwen/Qwen3-30B-A3B", "class_token": "Battlemage"},
                  "flag": "tool-call-parser", "value": "qwen3_xml"}]
        rows = {"H99": ("REFUTED", "| H99 | Battlemage B70 ... | REFUTED |"),
                "H98": ("SUPPORTED", "| H98 | Battlemage B70 ... | SUPPORTED |")}
        r, m = merged("Qwen/Qwen3-30B-A3B", ledger_rows=rows, hw_class="gpu:Battlemage", table=table)
        self.assertEqual(m["add"], [])
        self.assertTrue(any("ledger EXPERIMENTS.md H99 (REFUTED, Battlemage)" in x for x in m["explain"]))
        self.assertEqual(m["tool_parser"], "qwen3_xml")
        self.assertEqual(m["parser_sources"]["tool"], "ledger EXPERIMENTS.md H98 (SUPPORTED, Battlemage)")
        # INCONCLUSIVE and PENDING decide nothing; another hardware class is not this one.
        for verdict in ("INCONCLUSIVE", "PENDING"):
            r, m = merged("Qwen/Qwen3-30B-A3B", ledger_rows={"H98": (verdict, "| H98 | Battlemage |")},
                          hw_class="gpu:Battlemage", table=table)
            self.assertEqual(m["tool_parser"], "hermes")
        r, m = merged("Qwen/Qwen3-30B-A3B", ledger_rows=rows, hw_class="hpu", table=table)
        self.assertEqual((m["add"], m["tool_parser"]), (["--disable-sliding-window"], "hermes"))
        # A REFUTED parser row keeps k3d-gpu's default over the recipe.
        r, m = merged("Qwen/Qwen3-30B-A3B", ledger_rows={"H98": ("REFUTED", "| H98 | Battlemage |")},
                      hw_class="gpu:Battlemage", table=table, parsers={"tool": ("", "model optimizer"), "reasoning": ("", "")})
        self.assertEqual(m["tool_parser"], "")
        self.assertEqual(m["parser_sources"]["tool"], "ledger EXPERIMENTS.md H98 (REFUTED, Battlemage)")

    def test_min_vllm_and_nightly_warn(self):
        r = facts("baidu/ERNIE-5.5-Flash", version="0.26.0")
        self.assertTrue(any("needs vLLM >= 0.32.0; the agent image is 0.26.0" in w for w in r["warnings"]))
        e = copy.deepcopy(ART)
        e["models"]["qwen/qwen3-8b"]["nightly"] = True
        r = recipes.facts(e, "Qwen/Qwen3-8B", device="GPU", engine_version=V)
        self.assertIn("recipe needs a vLLM nightly", r["warnings"])

    def test_unknown_engine_version_uses_newest(self):
        r = facts("Qwen/Qwen3-8B", version="unknown")
        self.assertEqual(r["engine_version"], V)
        self.assertTrue(any("newest pinned" in n for n in r["notes"]))

    def test_unsupported_profile_gets_no_hardware_layer(self):
        e = copy.deepcopy(ART)
        e["models"]["qwen/qwen3-30b-a3b"]["hardware"]["arc_pro_b60"] = "unsupported"
        r, m = merged("Qwen/Qwen3-30B-A3B", pci="0xe211", art=e)
        self.assertFalse(r["hardware_layer"])
        self.assertEqual(m["add"], [])
        self.assertTrue(any("unsupported" in n for n in r["notes"]))

    def test_sibling_profile_note(self):
        # Qwen3-30B-A3B is verified on the B70 only; a B60 gets its xpu facts, with a note.
        r, m = merged("Qwen/Qwen3-30B-A3B", pci="0xe211")
        self.assertTrue(r["hardware_layer"])
        self.assertEqual(m["add"], ["--disable-sliding-window"])
        self.assertIn("xpu facts verified on arc_pro_b70; this card is arc_pro_b60", r["notes"])

    def test_no_intel_gpu_in_recipe(self):
        r, m = merged("Qwen/Qwen3-8B")
        self.assertFalse(r["hardware_layer"])
        self.assertEqual(m["add"], [])
        self.assertEqual((m["tool_parser"], m["reasoning_parser"]), ("hermes", "qwen3"))

    def test_hpu_model_layer_only(self):
        r, m = merged("Qwen/Qwen3.5-4B", device="HPU", pci=None, base=HPU_BASE)
        self.assertIsNone(r["generation"])
        self.assertEqual((m["tool_parser"], m["reasoning_parser"]), ("qwen3_coder", "qwen3"))
        self.assertEqual(m["add"], [])
        r, m = merged("Google/gemma-4-12B-it", device="HPU", pci=None, base=HPU_BASE)
        self.assertFalse(any(a.startswith(("--enforce-eager", "--attention-backend")) for a in m["add"]))

    def test_quote_refused(self):
        e = copy.deepcopy(ART)
        e["models"]["qwen/qwen3-8b"]["base_args"] = ["--generation-config", "it's"]
        r = recipes.facts(e, "Qwen/Qwen3-8B", device="GPU", engine_version=V)
        it = next(i for i in r["items"] if i["name"] == "generation-config")
        self.assertEqual(it["action"], "refused")

    def test_env_reported_never_applied(self):
        r, m = merged("meta-llama/Llama-3.3-70B-Instruct")
        names = {e["name"] for e in r["env"]}
        self.assertIn("TORCH_LLM_ALLREDUCE", names)
        self.assertFalse(any("TORCH_LLM_ALLREDUCE" in a for a in m["add"]))
        self.assertFalse(any(a.startswith("--quantization") for a in m["add"]))

    def test_no_tool_calling_feature(self):
        r, m = merged("meta-llama/Llama-3.1-8B-Instruct")
        self.assertEqual((m["tool_parser"], m["reasoning_parser"]), ("", ""))

    def test_explain_block(self):
        r, m = merged("Qwen/Qwen3.5-4B")
        lines = recipes.explain_lines(r, m, "vLLM server args: Qwen/Qwen3.5-4B on vllm")
        text = "\n".join(lines)
        self.assertIn("recipe: Qwen/Qwen3.5-4B (vllm@%s recipes/models/Qwen/Qwen3.5-4B.yaml; arc_pro_b70 verified)" % recipes.sha12(ART), text)
        self.assertRegex(text, r"--tool-call-parser=qwen3_coder +\[recipe vllm@[0-9a-f]{12} features\.tool_calling; in vLLM [0-9.]+'s registry\]")
        self.assertIn("recipe opt-in, left to the optimizer: spec_decoding", text)


class Real(unittest.TestCase):
    """The shipped console/vllm-recipes.json, whatever its pin."""

    def test_adopt_flags_exist_in_every_pinned_vllm(self):
        art, why = recipes.load(os.path.join(REPO, "console", "vllm-recipes.json"))
        self.assertIsNotNone(art, why)
        for name, eng in [("fixture", ART["engines"])] + [("console", art["engines"])]:
            for ver, e in eng.items():
                # --no-X is there when --X is (argparse BooleanOptionalAction; canary/surface.py).
                have = set(e["flags"]) | {"--no-" + f[2:] for f in e["flags"]}
                missing = sorted(set("--" + k for k in recipes.ADOPT) - have)
                self.assertEqual(missing, [], "%s vLLM %s lacks %s" % (name, ver, missing))

    def test_default_model_parsers_agree(self):
        art, _ = recipes.load(os.path.join(REPO, "console", "vllm-recipes.json"))
        r = recipes.facts(art, "Qwen/Qwen3-4B", device="GPU", engine_version="unknown")
        self.assertEqual((r["tool_parser"], r["reasoning_parser"]), ("hermes", "qwen3"))

    def test_load_failures(self):
        self.assertEqual(recipes.load("/nonexistent/vllm-recipes.json")[0], None)
        self.assertIn("missing", recipes.load("/nonexistent/vllm-recipes.json")[1])


if __name__ == "__main__":
    unittest.main()
