#!/usr/bin/env python3
"""Tests for console/model_opt.py, the model-aware optimizer. No network.

Fixtures: tests/fixtures/hf/<org>__<name>.json, Hugging Face metadata saved by
`model_opt.py fetch` on 2026-10-20 (each repo at the commit recorded in it):
  google/gemma-4-26B-A4B-it   MoE, vision, QAT siblings, -assistant drafters, eagle3
  Qwen/Qwen3.8-27B            dense, vision, built-in MTP head, OV IR with MTP, dspark
  microsoft/Phi-4-mini-instruct  dense control: OV IRs, no QAT, no MTP, no drafter
  LiquidAI/LFM2-1.2B          no variant in any trusted org

DECISIONS is the zero-flag decision table (no env, no options) per model and
engine, written from the model cards and engine docs collected for this feature
(model-cards/REFERENCE.md of the research run), not from model_opt.py.
"""

import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "console"))

import model_opt as mo  # noqa: E402
import plan as planmod  # noqa: E402
import facts as factsmod  # noqa: E402
import schema_check  # noqa: E402

HF_DIR = os.path.join(REPO, "tests", "fixtures", "hf")
GEMMA, QWEN, PHI, LFM = "google/gemma-4-26B-A4B-it", "Qwen/Qwen3.8-27B", "microsoft/Phi-4-mini-instruct", "LiquidAI/LFM2-1.2B"


def bundle(model):
    with open(mo.bundle_path(HF_DIR, model), encoding="utf-8") as f:
        return json.load(f)


def facts_of(name):
    with open(os.path.join(REPO, "tests", "fixtures", name, "facts.json"), encoding="utf-8") as f:
        return json.load(f)


# (model, engine, device) -> expected zero-flag decision, from section 4 of the
# research run's REFERENCE.md (hardware: Xeon 6 + Arc Pro B70; client CPUs).
#   model: variant served (None: the engine's default model stays, with a warning)
#   spec:  speculative decoding kind (None: off)
#   tool/reasoning: parsers
#   args:  engine arguments that must be present
# One deliberate difference: REFERENCE.md pairs OVMS Qwen3.8 with its bundled MTP.
# OVMS MTP needs prefix caching off, and the operator's rule keeps the measured
# default (EXPERIMENTS.md H25 SUPPORTED, prefix reuse), so MTP is opt-in there.
FLASH = "Qwen/Qwen3.8-Flash-Next"
DECISIONS = {
    (GEMMA, "ovms", "GPU"): {"model": "OpenVINO/gemma-4-26b-a4b-it-int4-ov", "spec": None, "tool": "gemma4", "reasoning": "gemma4", "args": []},
    (GEMMA, "ovms", "CPU"): {"model": "OpenVINO/gemma-4-26b-a4b-it-int4-ov", "spec": None, "tool": "gemma4", "reasoning": "gemma4", "args": []},
    (QWEN, "ovms", "GPU"): {"model": "OpenVINO/Qwen3.8-27B-int4-ov", "spec": None, "tool": "qwen3coder", "reasoning": "qwen3", "args": []},
    (PHI, "ovms", "CPU"): {"model": "OpenVINO/Phi-4-mini-instruct-int4-ov", "spec": None, "tool": None, "reasoning": None, "args": []},
    (LFM, "ovms", "GPU"): {"model": None, "spec": None, "tool": None, "reasoning": None, "args": []},
    (FLASH, "ovms", "GPU"): {"model": None, "spec": None, "tool": None, "reasoning": None, "args": []},
    # vLLM on a Gaudi: bf16 original, text-only, thinking pinned off; no speculative
    # decoding (vllm-gaudi: experimental, not with torch.compile).
    (GEMMA, "vllm", "HPU"): {"model": GEMMA, "spec": None, "tool": "gemma4", "reasoning": "gemma4",
                             "args": ["--language-model-only", "--default-chat-template-kwargs"]},
    (QWEN, "vllm", "HPU"): {"model": QWEN, "spec": None, "tool": "qwen3_coder", "reasoning": "qwen3",
                            "args": ["--language-model-only", "--default-chat-template-kwargs", "--override-generation-config"]},
    # vLLM-XPU: BF16 original, native MTP (Gemma: the -it-assistant, N=4; Qwen: N=3).
    (GEMMA, "vllm", "GPU"): {"model": GEMMA, "spec": "mtp", "tool": "gemma4", "reasoning": "gemma4",
                             "args": ["--language-model-only", "--default-chat-template-kwargs", "--speculative-config"]},
    (QWEN, "vllm", "GPU"): {"model": QWEN, "spec": "mtp", "tool": "qwen3_coder", "reasoning": "qwen3",
                            "args": ["--language-model-only", "--default-chat-template-kwargs", "--speculative-config"]},
    (FLASH, "vllm", "GPU"): {"model": None, "spec": None, "tool": None, "reasoning": None, "args": []},
    (PHI, "vllm", "GPU"): {"model": PHI, "spec": None, "tool": None, "reasoning": None, "args": []},
    (LFM, "vllm", "GPU"): {"model": LFM, "spec": None, "tool": None, "reasoning": None, "args": []},
    # llama.cpp: Google's QAT Q4_0 GGUF (no GGUF of the QAT drafter); Intel's q4km
    # AutoRound for Qwen, whose GGUF carries the MTP head.
    (GEMMA, "llama.cpp", "GPU"): {"model": "google/gemma-4-26B-A4B-it-qat-q4_0-gguf", "spec": None, "tool": None, "reasoning": None, "args": []},
    (QWEN, "llama.cpp", "GPU"): {"model": "Intel/Qwen3.8-27B-q4km-AutoRound", "spec": "mtp", "tool": None, "reasoning": None,
                                 "args": ["--spec-type", "draft-mtp", "--spec-draft-n-max"]},
    (FLASH, "llama.cpp", "CPU"): {"model": None, "spec": None, "tool": None, "reasoning": None, "args": []},
}


class Classify(unittest.TestCase):
    def setUp(self):
        self.g = {r: mo.classify(v, GEMMA) for r, v in bundle(GEMMA)["repos"].items()}
        self.q = {r: mo.classify(v, QWEN) for r, v in bundle(QWEN)["repos"].items()}

    def test_gemma_variants(self):
        g = self.g
        self.assertEqual((g["OpenVINO/gemma-4-26b-a4b-it-int4-ov"]["format"], g["OpenVINO/gemma-4-26b-a4b-it-int4-ov"]["precision"]), ("openvino-ir", "int4"))
        # openvino_config.json, not the README: AWQ, asymmetric, group 64.
        q = g["OpenVINO/gemma-4-26b-a4b-it-int4-ov"]["quant"]
        self.assertEqual((q["method"], q["sym"], q["group_size"]), ("awq", False, 64))
        self.assertTrue(g["google/gemma-4-26B-A4B-it-qat-q4_0-unquantized"]["qat"])
        self.assertEqual(g["google/gemma-4-26B-A4B-it-qat-q4_0-gguf"]["format"], "gguf")
        self.assertEqual(g["google/gemma-4-26B-A4B-it-qat-q4_0-gguf"]["quant"]["group_size"], 32)
        self.assertEqual(g["google/gemma-4-26B-A4B-it-assistant"]["drafter"], "assistant")
        self.assertFalse(g["google/gemma-4-26B-A4B-it-assistant"]["qat"])
        qa = g["google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant"]
        self.assertEqual((qa["drafter"], qa["qat"]), ("assistant", True))
        self.assertEqual(g["RedHatAI/gemma-4-26B-A4B-it-speculator.eagle3"]["drafter"], "eagle3")
        self.assertTrue(g["RedHatAI/gemma-4-26B-A4B-it-NVFP4"]["nvidia_only"])
        self.assertEqual(g["Intel/gemma-4-26B-A4B-it-int4-AutoRound"]["quant"]["method"], "auto-round")
        self.assertEqual(g["Intel/gemma-4-26B-A4B-it-int4-AutoRound"]["tier"], 0)
        self.assertEqual(g["RedHatAI/gemma-4-26B-A4B-it-FP8-dynamic"]["tier"], 1)

    def test_qwen_variants(self):
        q = self.q
        ov = q["OpenVINO/Qwen3.8-27B-int4-ov"]
        self.assertTrue(ov["mtp"])
        self.assertIn("openvino_mtp_model.xml", ov["mtp_source"])
        self.assertTrue(q[QWEN]["mtp"])
        self.assertIn("mtp_num_hidden_layers", q[QWEN]["mtp_source"])
        # Intel's int4-ov repo holds a download script, no IR.
        self.assertFalse(q["Intel/qwen3.8-27B-int4-ov"]["weights"])
        self.assertEqual(q["RedHatAI/Qwen3.8-27B-speculator.dspark"]["drafter"], "dspark")
        self.assertEqual(q["RedHatAI/Qwen3.8-27B-INT4"]["precision"], "int4")
        self.assertTrue(q["RedHatAI/Qwen3.8-27B-MXFP4"]["nvidia_only"])

    def test_suffix_rule(self):
        ok = ["Qwen/Qwen3-4B-int4-ov", "OpenVINO/gemma-4-26b-a4b-it-int4-ov", "Intel/Qwen3.8-27B-bpw2.8-AutoRound",
              "Intel/Qwen3.8-27B-q4km-AutoRound", "RedHatAI/Qwen3-4B-quantized.w4a16", "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant",
              "RedHatAI/Qwen3-4B-speculator.dflash2"]
        for c in ok:
            base = "Qwen/Qwen3-4B" if "Qwen3-4B" in c else QWEN if "Qwen3.8" in c else GEMMA
            self.assertTrue(mo.variant_suffix_ok(base, c), c)
        for base, c in [("Qwen/Qwen3-4B", "Qwen/Qwen3-4B-Instruct-2507"), ("Qwen/Qwen3-4B", "Qwen/Qwen3-4B-Thinking-2507-FP8"),
                        (LFM, "LiquidAI/LFM2-1.2B-Longevity"), ("Qwen/Qwen3-4B", "Qwen/Qwen3-4B0"), ("Qwen/Qwen3-4B", "Qwen/Qwen3-40B")]:
            self.assertFalse(mo.variant_suffix_ok(base, c), c)

    def test_thinking_detection(self):
        self.assertFalse(mo.model_facts(bundle(GEMMA)["repos"][GEMMA], GEMMA)["thinking_default"])
        self.assertTrue(mo.model_facts(bundle(QWEN)["repos"][QWEN], QWEN)["thinking_default"])
        line = lambda t: [{"line": 1, "text": t}]  # noqa: E731
        self.assertTrue(mo.thinks_by_default(line("{%- set enable_thinking = enable_thinking | default(true) -%}")))
        self.assertFalse(mo.thinks_by_default(line("{%- if enable_thinking -%}")))
        self.assertTrue(mo.thinks_by_default(line("{%- if enable_thinking is defined and enable_thinking is false %}")))
        self.assertIsNone(mo.thinks_by_default([]))

    def test_facts(self):
        f = mo.model_facts(bundle(GEMMA)["repos"][GEMMA], GEMMA)
        self.assertEqual((f["moe"], f["experts"], f["experts_per_token"], f["params_active_b"], f["context"], f["vision"]),
                         (True, 128, 8, 4.0, 262144, True))
        self.assertEqual(mo.moe_facts(f)["experts"], 128)
        f = mo.model_facts(bundle(QWEN)["repos"][QWEN], QWEN)
        self.assertEqual((f["moe"], f["model_type"], f["context"], f["vision"]), (False, "qwen3_5", 262144, True))
        self.assertIsNone(mo.moe_facts(f))
        self.assertEqual(f["card_sampling"]["non_thinking"]["values"]["presence_penalty"], 1.5)
        self.assertEqual(f["card_sampling"]["thinking"]["values"]["top_k"], 20)


class Decisions(unittest.TestCase):
    def test_zero_flag_table(self):
        for (model, engine, device), want in sorted(DECISIONS.items()):
            ctx = "%s on %s %s" % (model, engine, device)
            d = mo.optimize(bundle(model), engine, device)
            self.assertEqual(d["model"], want["model"], ctx)
            self.assertEqual((d["speculative"] or {}).get("kind"), want["spec"], ctx)
            self.assertEqual(d["tool_parser"], want["tool"], ctx)
            self.assertEqual(d["reasoning_parser"], want["reasoning"], ctx)
            self.assertFalse(d["thinking"], ctx)  # off for agents by default
            args = mo.engine_args(d)
            for a in want["args"]:
                self.assertIn(a, args, ctx)
            if want["model"] and want["model"] != model and engine != "llama.cpp":
                self.assertTrue(d["revision"], ctx)  # a pinned revision is recorded
            for c in d["choices"]:
                self.assertTrue(c["source"], "%s: choice without a source: %s" % (ctx, c))

    def test_qwen_vllm_args(self):
        d = mo.optimize(bundle(QWEN), "vllm", "GPU")
        args = mo.engine_args(d)
        cfg = json.loads(args[args.index("--speculative-config") + 1])
        self.assertEqual(cfg, {"method": "mtp", "num_speculative_tokens": 3})
        # medium: an effort the template accepts that injects no system prompt.
        self.assertEqual(json.loads(args[args.index("--default-chat-template-kwargs") + 1]),
                         {"enable_thinking": False, "reasoning_effort": "medium"})
        self.assertEqual(json.loads(args[args.index("--override-generation-config") + 1])["temperature"], 0.7)
        # Thinking on: pinned on, and the thinking-mode sampling of generation_config.json stays.
        args = mo.engine_args(mo.optimize(bundle(QWEN), "vllm", "GPU", {"thinking": "on"}))
        self.assertEqual(json.loads(args[args.index("--default-chat-template-kwargs") + 1])["enable_thinking"], True)
        self.assertNotIn("--override-generation-config", args)
        # Gemma: thinking explicitly off, and no reasoning_effort (its template has none).
        args = mo.engine_args(mo.optimize(bundle(GEMMA), "vllm", "GPU"))
        self.assertEqual(json.loads(args[args.index("--default-chat-template-kwargs") + 1]), {"enable_thinking": False})
        cfg = json.loads(args[args.index("--speculative-config") + 1])
        self.assertEqual(cfg, {"method": "mtp", "model": "google/gemma-4-26B-A4B-it-assistant", "num_speculative_tokens": 4})

    def test_old_vllm_drops_flags_it_lacks(self):
        d = mo.optimize(bundle(QWEN), "vllm", "CPU", {"vllm_version": "0.24.0"})
        self.assertIsNone(d["speculative"])  # no SD on CPU before 0.31.0
        d = mo.optimize(bundle(QWEN), "vllm", "GPU", {"vllm_version": "0.13.0"})
        args = mo.engine_args(d)
        self.assertNotIn("--default-chat-template-kwargs", args)
        self.assertNotIn("--language-model-only", args)

    def test_ovms_mtp_when_prefix_caching_off(self):
        d = mo.optimize(bundle(QWEN), "ovms", "GPU", {"draft": "mtp"})
        self.assertEqual(d["speculative"]["args"], ["--draft_model_path=.", "--enable_prefix_caching=false"])
        d = mo.optimize(bundle(QWEN), "ovms", "GPU", {"prefix_caching": False})
        self.assertEqual(d["speculative"]["args"], ["--draft_model_path=."])
        d = mo.optimize(bundle(QWEN), "ovms", "GPU", {"draft": "off", "prefix_caching": False})
        self.assertIsNone(d["speculative"])

    def test_variant_traps(self):
        d = mo.optimize(bundle(GEMMA), "ovms", "GPU")
        self.assertTrue(any("card says group_size 128; its openvino_config.json says 64 (awq, sym=False)" in c["fact"] for c in d["choices"]))
        d = mo.optimize(bundle(QWEN), "ovms", "GPU")
        self.assertTrue(any("caps model_max_length at 131072" in n for n in d["notes"]))
        d = mo.optimize(bundle(GEMMA), "vllm", "GPU", {"precision": "int4"})
        self.assertTrue(any("ships a chat template other than google/gemma-4-26B-A4B-it's" in n for n in d["notes"]))
        d = mo.optimize(bundle(QWEN), "vllm", "GPU", {"precision": "int4"})
        self.assertTrue(any("kv_cache_scheme" in n for n in d["notes"]))

    def test_flash_next_is_refused_with_the_reason(self):
        for engine in ("ovms", "vllm"):
            d = mo.optimize(bundle(FLASH), engine, "GPU")
            self.assertIsNone(d["model"])
            self.assertIn("qwen4_exp", d["warning"])
            self.assertIn("host RAM", d["warning"])
        d = mo.optimize(bundle(FLASH), "llama.cpp", "CPU")
        self.assertIsNone(d["model"])
        self.assertIn("no int4 gguf variant", d["warning"])

    def test_gaudi_mtp_on_request(self):
        d = mo.optimize(bundle(QWEN), "vllm", "HPU", {"draft": "mtp"})
        self.assertEqual(d["speculative"]["kind"], "mtp")
        d = mo.optimize(bundle(GEMMA), "vllm", "GPU", {"draft": "off"})
        self.assertIsNone(d["speculative"])

    def test_qat_drafter_pairing(self):
        # A QAT target needs the QAT drafter: llama.cpp serves the QAT GGUF, so the
        # non-QAT assistant and eagle3 are skipped with the card's reason.
        d = mo.optimize(bundle(GEMMA), "llama.cpp", "GPU")
        notes = " ".join(d["notes"])
        self.assertIn("a QAT target needs a QAT drafter", notes)
        self.assertIn("the QAT assistant exists only as safetensors", notes)
        # The grid-exact GGUF is a self-built candidate below Google's.
        self.assertEqual([r["id"] for r in d["ranking"]], ["google/gemma-4-26B-A4B-it-qat-q4_0-gguf", "qat-gguf-exact"])
        d = mo.optimize(bundle(GEMMA), "llama.cpp", "GPU", {"variant": "qat-gguf-exact"})
        self.assertEqual(d["model"], "google/gemma-4-26B-A4B-it-qat-q4_0-gguf")
        self.assertIn("no stock tool does", d["warning"])
        b = bundle(GEMMA)
        qa = mo.classify(b["repos"]["google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant"], GEMMA)
        self.assertTrue(mo.drafter_matches(qa, {"qat": True}, GEMMA))
        self.assertFalse(mo.drafter_matches(qa, {"qat": False}, GEMMA))

    def test_nvidia_formats_never_picked(self):
        for model in (GEMMA, QWEN):
            for engine, device in (("vllm", "GPU"), ("vllm", "HPU"), ("ovms", "GPU")):
                d = mo.optimize(bundle(model), engine, device, {"precision": "int4"} if engine == "vllm" else None)
                self.assertNotIn("FP4", (d["model"] or "").upper())
                self.assertTrue(any("NVIDIA only" in e["why"] for e in d["excluded"]))

    def test_vllm_int4_ranks_official_first(self):
        d = mo.optimize(bundle(QWEN), "vllm", "GPU", {"precision": "int4"})
        self.assertEqual(d["model"], "RedHatAI/Qwen3.8-27B-INT4")  # the only int4 safetensors
        d = mo.optimize(bundle(GEMMA), "vllm", "GPU", {"precision": "int4"})
        self.assertEqual(d["ranking"][0]["tier"], "official")
        self.assertTrue(d["model"].startswith("Intel/"))

    def test_pure(self):
        b = bundle(GEMMA)
        before = copy.deepcopy(b)
        one = mo.optimize(b, "ovms", "GPU")
        self.assertEqual(b, before)
        self.assertEqual(one, mo.optimize(b, "ovms", "GPU"))


class Ranking(unittest.TestCase):
    # Only the Hypotheses table counts: the log row below it never promotes.
    LEDGER = ("## Hypotheses\n\n| H54 | other row on Battlemage | p | m | SUPPORTED |\n"
              "| H54q | QAT-matched INT4_SYM g32 on Battlemage is non-inferior to the OpenVINO/ IR | p | m | %s |\n\n"
              "## Falsification log\n\n| H54q | Battlemage | SUPPORTED |\n")

    def test_qat_conversion_is_rank_two_by_default(self):
        d = mo.optimize(bundle(GEMMA), "ovms", "GPU", {"hw_class": "gpu:Battlemage"})
        self.assertEqual([r["id"] for r in d["ranking"]], ["OpenVINO/gemma-4-26b-a4b-it-int4-ov", "qat-convert"])
        self.assertIsNone(d["conversion"])
        self.assertTrue(any("qat-convert ranks 2" in c["fact"] for c in d["choices"]))

    def test_ledger_promotes_only_when_supported_for_this_class(self):
        for verdict, cls, promoted in (("SUPPORTED", "gpu:Battlemage", True), ("**SUPPORTED**", "gpu:Battlemage", True),
                                       ("INCONCLUSIVE", "gpu:Battlemage", False),
                                       ("REFUTED", "gpu:Battlemage", False), ("PENDING", "gpu:Battlemage", False),
                                       ("SUPPORTED", "gpu:Lunar Lake", False), ("SUPPORTED", None, False),
                                       ("SUPPORTED", "cpu:Intel(R) Xeon(R) 6741P", False)):
            d = mo.optimize(bundle(GEMMA), "ovms", "GPU", {"hw_class": cls, "ledger": self.LEDGER % verdict})
            self.assertEqual(d["variant"] == "qat-convert", promoted, (verdict, cls))
            if promoted:
                self.assertEqual(d["conversion"]["from"], "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized")
                self.assertIn("--sym --group-size 32", " ".join(d["conversion"]["commands"]))
                self.assertEqual(d["fallback_model"], "OpenVINO/gemma-4-26b-a4b-it-int4-ov")

    def test_class_tokens(self):
        self.assertEqual(mo.class_tokens("cpu:Intel(R) Xeon(R) 6741P"), ["6741P"])
        self.assertEqual(mo.class_tokens("gpu:Battlemage"), ["Battlemage"])
        self.assertEqual(mo.class_tokens("hpu"), ["Gaudi"])
        ledger = "## Hypotheses\n| H54q | on the Xeon 6741P, QAT IR | p | m | SUPPORTED |\n"
        d = mo.optimize(bundle(GEMMA), "ovms", "CPU", {"hw_class": "cpu:Intel(R) Xeon(R) 6741P", "ledger": ledger})
        self.assertEqual(d["variant"], "qat-convert")

    def test_measurement_promotes_only_faster_at_equal_or_better_quality(self):
        base = {"model": GEMMA, "variant": "qat-convert", "baseline": "OpenVINO/gemma-4-26b-a4b-it-int4-ov", "hw_class": "gpu:Battlemage"}
        for speedup, quality, promoted in ((1.2, "equal", True), (1.2, "better", True), (1.2, "worse", False), (0.9, "better", False)):
            meas = {"schema": mo.MEASURE_SCHEMA, "rows": [dict(base, speedup=speedup, quality=quality)]}
            d = mo.optimize(bundle(GEMMA), "ovms", "GPU", {"hw_class": "gpu:Battlemage", "measurements": meas})
            self.assertEqual(d["variant"] == "qat-convert", promoted, (speedup, quality))

    def test_forced_variant(self):
        d = mo.optimize(bundle(GEMMA), "ovms", "GPU", {"variant": "qat-convert"})
        self.assertEqual(d["variant"], "qat-convert")
        self.assertEqual(d["conversion"]["pins"]["optimum-intel"], "2.2.0")
        d = mo.optimize(bundle(GEMMA), "ovms", "GPU", {"variant": "nope/nope"})
        self.assertEqual(d["model"], "OpenVINO/gemma-4-26b-a4b-it-int4-ov")
        self.assertTrue(any("not a candidate" in n for n in d["notes"]))

    def test_tier_then_qat(self):
        def rec(rid, files, qat=False):
            return {"id": rid, "sha": "a" * 40, "url": "u", "tags": [], "files": files}
        b = {"schema": mo.META_SCHEMA, "target": "acme/M", "requested": "acme/M", "source": "huggingface", "searches": [],
             "repos": {"acme/M": rec("acme/M", ["model.safetensors"]),
                       "RedHatAI/M-int4-ov": rec("RedHatAI/M-int4-ov", ["openvino_model.xml"]),
                       "88plug/M-qat-int4-ov": rec("88plug/M-qat-int4-ov", ["openvino_model.xml"]),
                       "OpenVINO/M-int4-ov": rec("OpenVINO/M-int4-ov", ["openvino_model.xml"]),
                       "acme/M-qat-int4-ov": rec("acme/M-qat-int4-ov", ["openvino_model.xml"])}}
        d = mo.optimize(b, "ovms", "CPU")
        self.assertEqual([r["id"] for r in d["ranking"]],
                         ["acme/M-qat-int4-ov", "OpenVINO/M-int4-ov", "RedHatAI/M-int4-ov", "88plug/M-qat-int4-ov"])


class Fallback(unittest.TestCase):
    def test_map_matches_hf(self):
        """Every FALLBACK entry equals the saved HF metadata at its revision."""
        for target, fb in mo.FALLBACK.items():
            b = bundle(target)
            self.assertEqual(b["repos"][target]["sha"], fb["revision"], target)
            f = mo.model_facts(b["repos"][target], target)
            self.assertEqual((f["model_type"], f["vision"], f["thinking_default"]), (fb["model_type"], fb["multimodal"], fb["thinking_default"]), target)
            self.assertEqual(mo.classify(b["repos"][target], target)["mtp"], bool(fb.get("mtp")), target)
            for rid, v in fb["variants"].items():
                self.assertEqual(b["repos"][rid]["sha"], v["revision"], rid)
                c = mo.classify(b["repos"][rid], target)
                for k in ("format", "precision"):
                    self.assertEqual(c[k], v[k], (rid, k))
                self.assertEqual(c["qat"], bool(v.get("qat")), rid)
                self.assertEqual(c["drafter"], v.get("drafter"), rid)
                self.assertEqual(c["mtp"], bool(v.get("mtp")), rid)

    def test_map_gives_the_same_pick(self):
        for target in mo.FALLBACK:
            fb = mo.fallback_bundle(target, "offline")
            for engine, device in (("ovms", "GPU"), ("vllm", "HPU")):
                self.assertEqual(mo.optimize(fb, engine, device)["model"], mo.optimize(bundle(target), engine, device)["model"], (target, engine))
            self.assertTrue(any("Hugging Face unreachable" in c["fact"] for c in mo.optimize(fb, "ovms", "GPU")["choices"]))

    def test_unknown_model_degrades(self):
        b = mo.fallback_bundle("acme/never-heard-of", "offline")
        d = mo.optimize(b, "ovms", "GPU")
        self.assertIsNone(d["model"])
        self.assertIn("OVMS keeps its default model", d["warning"])
        d = mo.optimize(b, "vllm", "HPU")
        self.assertEqual(d["model"], "acme/never-heard-of")
        d = mo.optimize(mo.fallback_bundle("acme/thing-int4-ov", "offline"), "ovms", "GPU")
        self.assertEqual(d["model"], "acme/thing-int4-ov")

    def test_no_variants(self):
        d = mo.optimize(bundle(LFM), "ovms", "GPU")
        self.assertIsNone(d["model"])
        self.assertIn("keeping the engine's default model", d["warning"])
        self.assertEqual(mo.optimize(bundle(LFM), "vllm", "GPU")["model"], LFM)

    def test_load_or_fetch_never_raises(self):
        def down(url):
            raise mo.FetchError("%s: connection refused" % url)
        b = mo.load_or_fetch(GEMMA, get=down)
        self.assertEqual(b["source"], "fallback")
        b = mo.load_or_fetch("acme/x", get=down)
        self.assertEqual(b["source"], "none")
        b = mo.load_or_fetch(GEMMA, fixtures=HF_DIR)
        self.assertEqual(b["source"], "huggingface")


class Fetch(unittest.TestCase):
    def test_discovery_with_a_fake_hub(self):
        api = {
            "/api/models/acme/M/revision/main": {"id": "acme/M", "sha": "1" * 40, "tags": [], "siblings": [{"rfilename": "config.json"}, {"rfilename": "model.safetensors"}]},
            "/acme/M/resolve/" + "1" * 40 + "/config.json": {"model_type": "x", "layer_types": list(range(20)),
                                                            "quantization_config": {"bits": 4, "ignore": ["a"] * 30}},
            "/api/models/OpenVINO/M-int4-ov/revision/main": {"id": "OpenVINO/M-int4-ov", "sha": "2" * 40, "tags": [], "siblings": [{"rfilename": "openvino_model.xml"}, {"rfilename": "openvino_model.bin"}]},
        }
        searches = {("OpenVINO", "search"): [{"id": "OpenVINO/M-int4-ov"}, {"id": "OpenVINO/M-Instruct-2507-int4-ov"}]}

        def get(url):
            path = url[len(mo.HF):]
            if path.startswith("/api/models?"):
                q = dict(x.split("=", 1) for x in path.split("?", 1)[1].split("&"))
                kind = "search" if "search" in q else "filter"
                return json.dumps(searches.get((q["author"], kind), [])).encode()
            v = api.get(path)
            return None if v is None else json.dumps(v).encode()

        b = mo.fetch_bundle("acme/M", get=get)
        self.assertEqual(sorted(b["repos"]), ["OpenVINO/M-int4-ov", "acme/M"])
        cfg = b["repos"]["acme/M"]["config"]
        self.assertNotIn("layer_types", cfg)  # long lists trimmed
        self.assertNotIn("ignore", cfg["quantization_config"])
        self.assertEqual(b["repos"]["OpenVINO/M-int4-ov"]["files"], ["openvino_model.xml"])
        self.assertEqual(len(b["searches"]), 2 * (1 + len(mo.TIER_ORGS)))


class Render(unittest.TestCase):
    def test_shell_lines_are_eval_safe(self):
        d = mo.optimize(bundle(QWEN), "vllm", "GPU")
        d["notes"].append("it's")
        for line in mo.shell_lines(d):
            k, v = line.split("=", 1)
            self.assertTrue(v.startswith("'") and v.endswith("'"), line)
        bad = dict(d, engine_args=["--x", "a'b"])
        with self.assertRaises(ValueError):
            mo.engine_args(bad)

    def test_summary_names_what_was_picked(self):
        line = mo.summary_line(mo.optimize(bundle(QWEN), "vllm", "GPU"), "Xeon + 1x Battlemage")
        self.assertIn("Detected Xeon + 1x Battlemage; picked Qwen/Qwen3.8-27B on vllm (GPU) with MTP; thinking off for agents", line)

    def test_eat_patch(self):
        p = mo.eat_patch(mo.optimize(bundle(GEMMA), "vllm", "CPU", {"vllm_version": "0.24.0"}))
        self.assertIn("--language-model-only", p["add_args"])
        self.assertIsNone(p["speculative_config"])
        self.assertEqual(p["reasoning_parser"], "gemma4")


class PlanIntegration(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "model-opt", "xeon-b70.facts.json"), encoding="utf-8") as f:
            self.xb = json.load(f)
        with open(os.path.join(REPO, "console", "matrix.v1.json"), encoding="utf-8") as f:
            self.matrix = json.load(f)
        self.schema = factsmod.load_schema()

    def run_plan(self, facts, model):
        doc = planmod.plan(facts, self.matrix, {"model": model})
        doc = planmod.add_optimizer(doc, facts, bundle(model), {})
        schema_check.check(doc, self.schema, "#/$defs/plan")
        return doc

    def test_engine_follows_agent_logic(self):
        for facts, engine, device in ((self.xb, "ovms", "GPU"), (facts_of("gaudi3-hls"), "vllm", "HPU"),
                                      (facts_of("xeon-gnr"), "ovms", "CPU"), (facts_of("raptorlake-nvidia"), "ovms", "CPU")):
            d = self.run_plan(facts, QWEN)["optimizer"]
            self.assertEqual((d["engine"], d["device"]), (engine, device), facts["fingerprint"])

    def test_summary_first(self):
        doc = self.run_plan(self.xb, GEMMA)
        self.assertTrue(doc["summary"][0].startswith("Detected Intel(R) Xeon(R) 6960P (AMX) + 1x Battlemage 0xe223 (XMX); picked OpenVINO/gemma-4-26b-a4b-it-int4-ov"))
        self.assertEqual(doc["optimizer"]["choices"][0]["fact"][:4], "MoE:")

    def test_plan_without_meta_unchanged(self):
        doc = planmod.plan(self.xb, self.matrix, {"model": QWEN})
        self.assertNotIn("optimizer", doc)


if __name__ == "__main__":
    unittest.main(verbosity=1)
