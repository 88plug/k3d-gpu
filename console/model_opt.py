#!/usr/bin/env python3
"""model_opt: the model-aware optimizer behind `agent up --model`, `plan --model`
and the EAT overlay.

Two halves:
  fetch     reads Hugging Face's public API for one model id and writes a
            k3d-gpu.hfmeta/v1 bundle: the target and every variant the trusted
            orgs publish of it (searched by name and by base_model link), each at
            the commit the API resolved, with the facts the optimizer reads
            (file list, tags, card front matter, config.json, openvino_config.json,
            generation_config.json, the chat template's enable_thinking lines,
            and the README lines that document serving flags). No HF access ->
            the small FALLBACK map below, marked as such.
  optimize  a pure function of (bundle, engine, device, options): which variant
            to serve, the engine's speculative decoding, thinking default, tool
            and reasoning parsers, text-only load, and every choice as
            "fact -> choice [source]". No I/O, no clock.

Variant ranking (the operator's rule): official first (the model's author, Intel/,
OpenVINO/; Intel's AutoRound counts as official), then RedHatAI/, then 88plug/ or
a self-built conversion. Within a tier a QAT checkpoint wins for an int4 target.
A lower-ranked variant is picked only when a measurement says it is faster at
equal-or-better quality on this hardware class: an EXPERIMENTS.md row marked
SUPPORTED that names the class (PROMOTION_ROWS), or a cached local measurement.
NVFP4/MXFP4 run on NVIDIA only and are never picked on an Intel device.

  model_opt.py fetch --model ID [--revision REV] [--out FILE] [--cache DIR] [--fixtures DIR]
  model_opt.py optimize --bundle FILE --engine ovms|vllm|llama.cpp --device CPU|GPU|HPU
                        [--vllm-version X.Y.Z] [--thinking off|on|model] [--variant ID|qat-convert]
                        [--draft auto|off|mtp] [--prefix-caching true|false] [--hw-class C]
                        [--ledger FILE] [--measurements FILE]
                        [--format json|shell|summary|explain|eat]

Python standard library only.
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

META_SCHEMA = "k3d-gpu.hfmeta/v1"
OPT_SCHEMA = "k3d-gpu.modelopt/v1"
MEASURE_SCHEMA = "k3d-gpu.variant-measurements/v1"
HF = "https://huggingface.co"
CACHE_TTL = 86400  # seconds a cached "main" bundle is reused

# Orgs searched for variants, by tier. The model's own author is tier 0 too.
TIER_ORGS = {"Intel": 0, "OpenVINO": 0, "RedHatAI": 1, "88plug": 2}
TIER_NAMES = {0: "official", 1: "RedHatAI", 2: "community/self-built"}

# Name tokens a variant may append to the target's name (format, precision,
# quantizer, drafter kind). A token outside this set marks a different model
# (a finetune such as "-Instruct-2507" or "-Longevity"), not a variant.
VARIANT_TOKEN = re.compile(
    r"^(qat|unquantized|gguf|assistant|ov|openvino|autoround|mixed|dynamic|quantized|speculator|"
    r"eagle\d*|dspark|dflash\d*|mtp|awq|gptq|cw|gq|inc|recipe|mlx|onnx|web|bf16|fp\d+|int\d+|"
    r"w\d+a\d+|(nv|mx)fp4|q\d+(_\d+|_?k_?[sml]?|k[sml])?|bpw\d+|\d+(bit)?)$")
DRAFTER_TOKENS = ("assistant", "speculator", "eagle", "dspark", "dflash")
NVIDIA_ONLY = ("nvfp4", "mxfp4")

# Ledger rows that, when SUPPORTED for this hardware class, promote a variant.
# H54q: the QAT-matched IR against the published OpenVINO/ IR on a fixed quality
# set (GSM8K items 0-199), on the hardware its protocol names.
PROMOTION_ROWS = {"qat-convert": "H54q"}

# Q4_0 (llama.cpp ggml-common.h QK4_0 = 32; quantize_row_q4_0_ref: one fp16 scale
# per 32 weights, no zero point) is symmetric int4 with groups of 32.
Q4_0 = {"sym": True, "group_size": 32,
        "source": "https://github.com/ggml-org/llama.cpp/blob/master/ggml/src/ggml-common.h (QK4_0 32), ggml-quants.c quantize_row_q4_0_ref"}
# The conversion toolchain, pinned in convert-pins.json (moved nightly by hack/bump-convert-pins).
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "convert-pins.json"), encoding="utf-8") as _f:
    CONVERT_PINS = json.load(_f)["pins"]
OPTIMUM_EXPORT = "https://github.com/huggingface/optimum-intel/blob/v2.2.0/docs/source/openvino/export.mdx"

# Engine capabilities, from each engine's own docs at the pinned release.
OVMS_DOC = "https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/"
OVMS_SPEC_DEMO = OVMS_DOC + "demos/continuous_batching/speculative_decoding/README.md"
OVMS_TOOL_PARSERS = ("llama3", "hermes3", "phi4", "mistral", "gptoss", "qwen3coder", "devstral", "lfm2", "gemma4", "onyx", "minicpm5")
OVMS_REASONING_PARSERS = ("qwen3", "gemma4", "gptoss", "minicpm5", "lfm2", "onyx")
VLLM_SPEC_DOC = "https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/config/speculative.py"
VLLM_MTP_DOC = "https://github.com/vllm-project/vllm/blob/v0.31.0/docs/features/speculative_decoding/mtp.md"
VLLM_HW_TABLE = "https://github.com/vllm-project/vllm/blob/v0.31.0/docs/features/README.md (hardware table, L67)"
VLLM_GAUDI_SD = "https://github.com/vllm-project/vllm-gaudi/blob/v0.30.0/docs/features/supported_features.md (L25, L27)"
LLAMACPP_ARGS = "https://github.com/ggml-org/llama.cpp/blob/v0.7.0/common/arg.cpp (L4287 -md)"

# Tool and reasoning parsers by model family (config model_type), per engine.
# OVMS detects both from the chat template when unset (docs/parameters.md
# L169-170), so it is set only where OVMS documents a name for the family.
FAMILY_PARSERS = {
    "qwen3": {"vllm": ("hermes", "qwen3"), "ovms": ("hermes3", "qwen3"),
              "source": OVMS_DOC + "docs/llm/reference.md (L282-301: hermes3 for Qwen3); vLLM tool_parsers/__init__.py"},
    "qwen3_moe": {"vllm": ("hermes", "qwen3"), "ovms": ("hermes3", "qwen3"),
                  "source": OVMS_DOC + "docs/llm/reference.md (L282-301: hermes3 for Qwen3); vLLM tool_parsers/__init__.py"},
    "qwen3_5": {"vllm": ("qwen3_coder", "qwen3"), "ovms": ("qwen3coder", "qwen3"),
                "source": "https://recipes.vllm.ai/Qwen/Qwen3.8-27B (--tool-call-parser qwen3_coder --reasoning-parser qwen3); "
                          "https://github.com/openvinotoolkit/model_server/blob/main/docs/llm/reference.md (qwen3coder + qwen3)"},
    "gemma4": {"vllm": ("gemma4", "gemma4"), "ovms": ("gemma4", "gemma4"),
               "source": "https://recipes.vllm.ai/Google/gemma-4-26B-A4B-it (--tool-call-parser gemma4 --reasoning-parser gemma4); "
                         "OVMS v2026.4.1 src/llm/io_processing/output_parser.cpp (gemma4 tool + reasoning)"},
}

# Architectures an engine documents as unsupported on Intel devices.
UNSUPPORTED = {
    ("vllm", "qwen4_exp"): "vLLM: NotImplementedError(\"Qwen4Exp currently supports CUDA and ROCm only\") "
                           "(vllm/models/qwen4_exp/__init__.py; https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next)",
    ("ovms", "qwen4_exp"): "OpenVINO: qwen4_exp is in no OpenVINO or OVMS release (https://github.com/openvinotoolkit/openvino/releases)",
}

# A small map of known-good variants, used only when Hugging Face cannot be
# reached. tests/console/test_model_opt.py proves each entry matches the HF
# metadata saved at these revisions.
FALLBACK = {
    "google/gemma-4-26B-A4B-it": {
        "revision": "4d7ae4984b7db7de8f8457170b3f1a419ee76d52",
        "model_type": "gemma4", "multimodal": True, "thinking_default": False,
        "variants": {
            "OpenVINO/gemma-4-26b-a4b-it-int4-ov": {"revision": "4e5885c3f03844f5887982def09708bdc9f53f7a", "format": "openvino-ir", "precision": "int4"},
            "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized": {"revision": "f1e06dc520982d9b9edd76859fdb7ab209449949", "format": "safetensors", "precision": "bf16", "qat": True},
            "google/gemma-4-26B-A4B-it-assistant": {"revision": "6e5aaaf4c42b98394530b8fda2e95cadd65c151c", "format": "safetensors", "precision": "bf16", "drafter": "assistant"},
        },
    },
    "Qwen/Qwen3.8-27B": {
        "revision": "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0",
        "model_type": "qwen3_5", "multimodal": True, "thinking_default": True, "mtp": True,
        "variants": {
            "OpenVINO/Qwen3.8-27B-int4-ov": {"revision": "7b20ea8c7df7223a85ff27bab5c473f0cb038943", "format": "openvino-ir", "precision": "int4", "mtp": True},
        },
    },
}


class FetchError(Exception):
    pass


# ---------------------------------------------------------------- fetch --------

def http_get(url, rng=None, timeout=20):
    """Bytes of url (rng: (first, last) byte), None on 404. FetchError on anything else."""
    headers = {"User-Agent": "k3d-gpu-model-opt"}
    if rng:
        headers["Range"] = "bytes=%d-%d" % rng
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            # A server that ignores Range sends the whole file: read only the asked bytes.
            return r.read(rng[1] - rng[0] + 1) if rng else r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            return None
        raise FetchError("%s: HTTP %d" % (url, e.code)) from e
    except (urllib.error.URLError, OSError) as e:
        raise FetchError("%s: %s" % (url, e)) from e


def get_json(get, url):
    raw = get(url)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError as e:
        raise FetchError("%s: not JSON" % url) from e


CARD_LINE = re.compile(
    r"vllm serve|--tool-call-parser|--reasoning-parser|--speculative[-_]config|enable_thinking|<\|think\|>|"
    r"language-model-only|limit-mm-per-prompt|default-chat-template-kwargs|\bQAT\b|assistant model|"
    r"multi-token prediction|\bMTP\b|--model-draft|draft_model|INT4_(A)?SYM|group_size|temperature\s*=", re.I)
SAMPLING_KEYS = ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty")
THINK_LINE = re.compile(r"enable_thinking|reasoning_effort")
# The start of a GGUF holds its key/value metadata: enough to see an MTP head.
GGUF_HEAD = 262144
GGUF_MTP_KEY = re.compile(rb"([a-z0-9_]+)\.nextn_predict_layers")


def card_lines(text, limit=40):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if CARD_LINE.search(line):
            out.append({"line": n, "text": line.strip()[:300]})
            if len(out) >= limit:
                break
    return out


def thinking_lines(text, limit=6):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if THINK_LINE.search(line):
            out.append({"line": n, "text": line.strip()[:200]})
            if len(out) >= limit:
                break
    return out


# Files the classifier reads: weight formats, OpenVINO graphs, configs, card.
KEEP_FILE = re.compile(r"(\.xml|\.gguf|\.onnx|00001-of-\d+\.safetensors|^[^/]*model\.safetensors|\.json|\.jinja|README\.md)$")


def trim(v, depth=0):
    """A config without its long lists (per-layer types, ignored module names)."""
    if isinstance(v, dict):
        return {k: trim(x, depth + 1) for k, x in v.items()
                if not (isinstance(x, list) and len(x) > 8) and not (isinstance(x, dict) and depth and len(x) > 40)}
    return v


def repo_record(get, rid, revision="main", target=False):
    q = urllib.parse.quote
    info = get_json(get, "%s/api/models/%s/revision/%s" % (HF, rid, q(revision, safe="")))
    if info is None:
        return None
    sha = info.get("sha") or revision
    files = sorted(s["rfilename"] for s in info.get("siblings") or [] if KEEP_FILE.search(s["rfilename"]))
    card = info.get("cardData") or {}
    rec = {
        "id": info.get("id") or rid, "sha": sha,
        "url": "%s/%s/tree/%s" % (HF, rid, sha),
        "pipeline_tag": info.get("pipeline_tag"), "library_name": info.get("library_name"),
        "tags": sorted(info.get("tags") or []),
        "card": {k: card[k] for k in ("base_model", "base_model_relation", "pipeline_tag", "license", "library_name") if k in card},
        "files": files,
        "params_total": (info.get("safetensors") or {}).get("total"),
    }

    def raw(name):
        return "%s/%s/resolve/%s/%s" % (HF, rid, sha, name)

    for name, key in (("config.json", "config"), ("openvino_config.json", "openvino_config"),
                      ("generation_config.json", "generation_config")):
        if name in files:
            rec[key] = trim(get_json(get, raw(name)))
    if "README.md" in files:
        text = (get(raw("README.md")) or b"").decode("utf-8", "replace")
        rec["card_lines"] = card_lines(text)
    tmpl = None
    if "tokenizer_config.json" in files:
        tc = get_json(get, raw("tokenizer_config.json")) or {}
        if isinstance(tc.get("model_max_length"), int):
            rec["tokenizer_max_length"] = tc["model_max_length"]
        tmpl = tc.get("chat_template") if isinstance(tc.get("chat_template"), str) else None
    if "chat_template.jinja" in files:
        tmpl = (get(raw("chat_template.jinja")) or b"").decode("utf-8", "replace")
    gguf = info.get("gguf") or {}
    if tmpl is None and isinstance(gguf.get("chat_template"), str):
        tmpl = gguf["chat_template"]
    if tmpl is not None:
        rec["chat_template_sha256"] = hashlib.sha256(tmpl.strip().encode("utf-8")).hexdigest()
        if target:
            rec["chat_template_thinking"] = thinking_lines(tmpl)
    weights = [f for f in files if f.endswith(".gguf") and "mmproj" not in f.lower()]
    if weights:
        rec["gguf_file"] = weights[0]
        head = get(raw(weights[0]), (0, GGUF_HEAD - 1)) or b""
        m = GGUF_MTP_KEY.search(head)
        rec["gguf_mtp_key"] = m.group(0).decode() if m else None
    return rec


def name_of(rid):
    return rid.split("/", 1)[1] if "/" in rid else rid


def org_of(rid):
    return rid.split("/", 1)[0] if "/" in rid else ""


def variant_suffix_ok(target, cand):
    """True when cand's name is target's name plus variant tokens only."""
    t, c = name_of(target).lower(), name_of(cand).lower()
    if c == t:
        return True
    if not c.startswith(t) or c[len(t)] not in "-._":
        return False
    return all(VARIANT_TOKEN.match(tok) for tok in re.split(r"[-.]", c[len(t) + 1:]) if tok)


def base_links(rec):
    """base_model ids the repo names, from its tags (base_model:<relation>:<id>)."""
    out = {}
    for tag in rec.get("tags") or []:
        parts = tag.split(":")
        if parts[0] == "base_model" and len(parts) == 3:
            out[parts[2]] = parts[1]
        elif parts[0] == "base_model" and len(parts) == 2:
            out.setdefault(parts[1], "base")
    return out


def fetch_bundle(target, revision="main", get=http_get):
    """The k3d-gpu.hfmeta/v1 bundle for target, read from Hugging Face."""
    trec = repo_record(get, target, revision, target=True)
    if trec is None:
        raise FetchError("%s: no such model on Hugging Face" % target)
    target = trec["id"]
    # A quantized repo named as the target stands for its base model.
    canonical = target
    for base, rel in base_links(trec).items():
        if rel == "quantized" and base != target:
            canonical = base
    orgs = [org_of(canonical)] + [o for o in TIER_ORGS if o != org_of(canonical)]
    searches, found = [], []
    for org in orgs:
        for kind, query in (("search", name_of(canonical)), ("filter", "base_model:" + canonical)):
            url = "%s/api/models?author=%s&%s=%s&limit=100" % (HF, org, kind, urllib.parse.quote(query, safe=""))
            ids = sorted(m["id"] for m in get_json(get, url) or [])
            searches.append({"url": url, "ids": ids})
            found += [i for i in ids if i not in found]
    repos = {target: trec}
    if canonical != target:
        crec = repo_record(get, canonical, "main", target=True)
        if crec:
            repos[canonical] = crec
    todo = [rid for rid in sorted(found) if rid not in repos and variant_suffix_ok(canonical, rid)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for rid, rec in zip(todo, pool.map(lambda r: repo_record(get, r), todo)):
            if rec is not None:
                repos[rid] = rec
    return {"schema": META_SCHEMA, "requested": target, "target": canonical, "revision": revision,
            "source": "huggingface", "searches": searches, "repos": repos}


def fallback_bundle(target, why):
    """A bundle from FALLBACK, or a bare one naming only the target."""
    fb = FALLBACK.get(target)
    if fb is None:
        return {"schema": META_SCHEMA, "requested": target, "target": target, "revision": "main",
                "source": "none", "error": why, "searches": [], "repos": {}}
    self_fb = dict({k: v for k, v in fb.items() if k != "variants"}, format="safetensors", precision="bf16")
    repos = {target: {"id": target, "sha": fb["revision"], "url": "%s/%s/tree/%s" % (HF, target, fb["revision"]),
                      "fallback": self_fb}}
    for rid, v in fb["variants"].items():
        repos[rid] = {"id": rid, "sha": v["revision"], "url": "%s/%s/tree/%s" % (HF, rid, v["revision"]), "fallback": v}
    return {"schema": META_SCHEMA, "requested": target, "target": target, "revision": fb["revision"],
            "source": "fallback", "error": why, "searches": [], "repos": repos}


def bundle_path(directory, target, revision="main"):
    tag = target.replace("/", "__") + ("" if revision == "main" else "@" + revision)
    return os.path.join(directory, tag + ".json")


def load_or_fetch(target, revision="main", cache=None, fixtures=None, get=http_get):
    """fixtures dir (tests) > cache > Hugging Face > FALLBACK. Never raises."""
    if fixtures:
        p = bundle_path(fixtures, target, revision)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return fallback_bundle(target, "no fixture %s" % p)
    if cache:
        # A pinned revision never changes; "main" is re-read once a day.
        p = bundle_path(cache, target, revision)
        if os.path.exists(p) and (revision != "main" or time.time() - os.path.getmtime(p) < CACHE_TTL):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    try:
        bundle = fetch_bundle(target, revision, get)
    except FetchError as e:
        if cache:
            p = bundle_path(cache, target, revision)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    b = json.load(f)
                b["stale"] = str(e)
                return b
        return fallback_bundle(target, str(e))
    if cache:
        os.makedirs(cache, exist_ok=True)
        for rev in sorted({revision, bundle["repos"][bundle["requested"]]["sha"]}):
            p = bundle_path(cache, target, rev)
            with open(p + ".tmp", "w", encoding="utf-8") as f:
                json.dump(bundle, f, indent=1, sort_keys=True)
            os.replace(p + ".tmp", p)
    return bundle


# ---------------------------------------------------------------- classify -----

def text_config(cfg):
    cfg = cfg or {}
    return cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else cfg


def classify(rec, target):
    """What one repo is, from its files, tags, name and configs."""
    rid = rec["id"]
    fb = rec.get("fallback") or {}
    org = org_of(rid)
    tier = 0 if org == org_of(target) or TIER_ORGS.get(org) == 0 else TIER_ORGS.get(org, 2)
    tokens = set(re.split(r"[-.]", name_of(rid).lower()))
    files = rec.get("files") or []
    cfg = rec.get("config") or {}
    qcfg = cfg.get("quantization_config") or text_config(cfg).get("quantization_config") or {}
    c = {"id": rid, "sha": rec.get("sha"), "url": rec.get("url"), "org": org, "tier": tier,
         "self": rid == target, "qat": "qat" in tokens or bool(fb.get("qat")), "mtp": False, "mtp_source": None,
         "drafter": fb.get("drafter"), "format": fb.get("format"), "precision": fb.get("precision"),
         "quant": None, "nvidia_only": any(t in tokens for t in NVIDIA_ONLY), "weights": True,
         "verifier": None}
    for t in DRAFTER_TOKENS:
        if any(tok.startswith(t) for tok in tokens):
            c["drafter"] = c["drafter"] or ("assistant" if t == "assistant" else None)
    arch = " ".join(cfg.get("architectures") or [])
    if "Eagle3" in arch or "eagle3" in tokens:
        c["drafter"] = "eagle3"
    elif "DSpark" in arch or "dspark" in tokens:
        c["drafter"] = "dspark"
    elif any(t.startswith("dflash") for t in tokens) or "DFlash" in arch:
        c["drafter"] = "dflash"
    elif "Assistant" in arch or (cfg.get("model_type") or "").endswith("_assistant"):
        c["drafter"] = "assistant"
    if c["drafter"] in ("eagle3", "dspark", "dflash"):
        c["verifier"] = ((cfg.get("speculators_config") or {}).get("verifier") or {}).get("name_or_path")
    if not fb:
        if any(f.endswith("openvino_model.xml") or f.endswith("openvino_language_model.xml") for f in files):
            c["format"] = "openvino-ir"
            oc = (rec.get("openvino_config") or {}).get("quantization_config") or {}
            lm = (oc.get("quantization_configs") or {}).get("lm_model") or oc.get("lm_model") or oc
            bits = lm.get("bits")
            c["precision"] = "int%d" % bits if bits else next((t for t in tokens if re.match(r"^(int\d+|fp16|bf16)$", t)), "unknown")
            c["quant"] = {"sym": lm.get("sym"), "group_size": lm.get("group_size"), "method": lm.get("quant_method"),
                          "source": "%s/blob/%s/openvino_config.json" % (HF + "/" + rid, rec.get("sha"))} if lm else None
        elif any(f.endswith(".gguf") for f in files):
            c["format"] = "gguf"
            q = next((t for t in tokens if re.match(r"^q\d", t)), None)
            c["precision"] = "int%s" % q[1] if q else "unknown"
            if "q4_0" in tokens:
                c["quant"] = dict(Q4_0, method="Q4_0")
        elif any(f.endswith(".safetensors") for f in files):
            c["format"] = "safetensors"
            if qcfg:
                method = qcfg.get("quant_method")
                bits = qcfg.get("bits")
                if bits is None:
                    grp = (((qcfg.get("config_groups") or {}).get("group_0") or {}).get("weights") or {})
                    bits = grp.get("num_bits")
                c["precision"] = ("fp8" if method == "fp8" or "fp8" in tokens else
                                  "fp4" if c["nvidia_only"] else "int%s" % bits if bits else "unknown")
                c["quant"] = {"method": method, "bits": bits, "sym": qcfg.get("sym"), "group_size": qcfg.get("group_size"),
                              "source": "%s/blob/%s/config.json" % (HF + "/" + rid, rec.get("sha"))}
            else:
                dt = text_config(cfg).get("dtype") or text_config(cfg).get("torch_dtype") or cfg.get("dtype") or cfg.get("torch_dtype")
                c["precision"] = {"bfloat16": "bf16", "float16": "fp16", "float32": "fp32"}.get(dt, "bf16")
        elif any(f.endswith(".onnx") for f in files) or "onnx" in tokens:
            c["format"] = "onnx"
        elif "mlx" in tokens or "mlx" in (rec.get("tags") or []):
            c["format"] = "mlx"
        else:
            c["format"], c["weights"] = "none", False
    if "openvino_mtp_model.xml" in files:
        c["mtp"], c["mtp_source"] = True, "%s/blob/%s/openvino_mtp_model.xml" % (HF + "/" + rid, rec.get("sha"))
    elif text_config(cfg).get("mtp_num_hidden_layers") or text_config(cfg).get("num_nextn_predict_layers"):
        key = "mtp_num_hidden_layers" if text_config(cfg).get("mtp_num_hidden_layers") else "num_nextn_predict_layers"
        c["mtp"], c["mtp_source"] = True, "%s/blob/%s/config.json (%s)" % (HF + "/" + rid, rec.get("sha"), key)
    elif rec.get("gguf_mtp_key"):
        c["mtp"], c["mtp_source"] = True, "%s/blob/%s/%s (GGUF key %s)" % (HF + "/" + rid, rec.get("sha"), rec.get("gguf_file"), rec["gguf_mtp_key"])
    elif fb.get("mtp"):
        c["mtp"], c["mtp_source"] = True, "fallback map"
    return c


def model_facts(rec, target):
    """Architecture facts of the target: MoE, params, context, modalities, thinking."""
    fb = rec.get("fallback") or {}
    cfg = rec.get("config") or {}
    tc = text_config(cfg)
    src = "%s/blob/%s/config.json" % (HF + "/" + target, rec.get("sha"))
    experts = tc.get("num_experts") or tc.get("num_local_experts") or tc.get("n_routed_experts")
    per_tok = tc.get("num_experts_per_tok") or tc.get("top_k_experts")
    m = re.search(r"-A(\d+(?:\.\d+)?)B", name_of(target))
    active = float(m.group(1)) if m else None
    vision = bool(cfg.get("vision_config")) or bool(fb.get("multimodal"))
    audio = bool(cfg.get("audio_config"))
    think = rec.get("chat_template_thinking")
    if think is not None:
        thinking = thinks_by_default(think)
        tsrc = "%s/blob/%s/chat_template (line %s)" % (HF + "/" + target, rec.get("sha"), think[0]["line"] if think else "-")
    else:
        thinking = fb.get("thinking_default")
        tsrc = "fallback map" if thinking is not None else None
    return {
        "model_type": cfg.get("model_type") or tc.get("model_type") or fb.get("model_type"),
        "architecture": (cfg.get("architectures") or [None])[0],
        "moe": bool(experts and experts > 1), "experts": experts, "experts_per_token": per_tok,
        "params_total": rec.get("params_total"), "params_active_b": active,
        "context": tc.get("max_position_embeddings"),
        "vision": vision, "audio": audio,
        "thinking_default": thinking, "thinking_source": tsrc,
        "reasoning_efforts": reasoning_efforts(think or []),
        "tokenizer_max_length": rec.get("tokenizer_max_length"),
        "card_sampling": card_sampling(rec.get("card_lines")),
        "generation_config": {k: v for k, v in (rec.get("generation_config") or {}).items()
                              if k in ("do_sample", "temperature", "top_p", "top_k", "min_p", "repetition_penalty")},
        "source": src if cfg else "fallback map" if fb else None,
    }


def reasoning_efforts(lines):
    """The reasoning_effort values a template accepts ("not in ('xhigh', 'medium', 'low')")."""
    for x in lines:
        m = re.search(r"reasoning_effort\w*\s+not in\s*\(([^)]*)\)", x["text"])
        if m:
            return re.findall(r"'([a-z]+)'", m.group(1))
    return []


def thinks_by_default(lines):
    """True when the chat template thinks unless enable_thinking=false is sent."""
    text = " ".join(x["text"] for x in lines)
    if re.search(r"enable_thinking\s*\|\s*default\(\s*(true|True)\s*\)", text):
        return True
    if re.search(r"enable_thinking\s*\|\s*default\(\s*(false|False)\s*\)", text):
        return False
    if re.search(r"enable_thinking is (undefined|not defined)", text) or \
            re.search(r"enable_thinking is defined and enable_thinking is false", text):
        return True
    return False if text else None


def card_sampling(lines):
    """{"thinking": {...}, "non_thinking": {...}} from the card's recommended sampling lines."""
    out = {}
    for x in lines or []:
        text = x["text"]
        vals = {}
        for k, v in re.findall(r"`?(%s)\s*=\s*([0-9.]+)`?" % "|".join(SAMPLING_KEYS), text):
            vals[k] = float(v) if "." in v else int(v)
        if len(vals) < 3:
            continue
        low = text.lower()
        mode = "non_thinking" if ("non-thinking" in low or "instruct" in low) else "thinking" if "thinking" in low else None
        if mode and mode not in out:
            out[mode] = {"values": vals, "line": x["line"]}
    return out


def moe_facts(facts):
    """The MoE shape a bandwidth predictor needs (bytes moved per token follow active params)."""
    if not facts["moe"]:
        return None
    return {"experts": facts["experts"], "experts_per_token": facts["experts_per_token"],
            "params_total": facts["params_total"], "params_active_b": facts["params_active_b"]}


# ---------------------------------------------------------------- promote ------

def ledger_rows(text):
    """{row id: (verdict, row text)} from EXPERIMENTS.md hypothesis table rows."""
    out, inh = {}, False
    for line in (text or "").splitlines():
        if line.startswith("## "):
            inh = line.startswith("## Hypotheses")
        if not inh or not line.startswith("| H"):
            continue
        cells = [c.strip() for c in line.replace("\\|", "").strip().strip("|").split("|")]
        if len(cells) >= 3:
            verdict = cells[-1].strip("* ").split()
            out[cells[0]] = (verdict[0].upper() if verdict else "", line)
    return out


# CPU model numbers as ledger rows write them: "i9-13900T", "6741P", "285K".
CPU_CLASS = re.compile(r"(?<![a-z0-9])(i[3579]-\d{4,5}[a-z]{0,2}|\d{3,5}[a-z]{1,2})(?![a-z0-9])")
# The row families whose verdicts set agent defaults: H27 (autoquant), H34 (CPU Fast
# Draft). console/ledger-rules.json carries them for installs without EXPERIMENTS.md.
RULE_FAMILIES = ("H27", "H34")
RULES_SCHEMA = "k3d-gpu.ledger-rules/v1"


def row_classes(text):
    """The CPU classes a ledger row names (lower case, exact tokens)."""
    return sorted(set(m.group(1) for m in CPU_CLASS.finditer((text or "").lower())))


def family_of(rid, family):
    return re.match(r"^%s[a-z]*$" % re.escape(family), rid) is not None


def ledger_rules_doc(rows):
    """console/ledger-rules.json from EXPERIMENTS.md rows: id, verdict, CPU classes."""
    out = [{"id": rid, "verdict": v, "classes": row_classes(text)}
           for rid, (v, text) in sorted(rows.items()) if any(family_of(rid, f) for f in RULE_FAMILIES)]
    return {"schema": RULES_SCHEMA, "source": "EXPERIMENTS.md, ## Hypotheses",
            "families": list(RULE_FAMILIES), "rows": out}


def read_ledger(path):
    """{row id: (verdict, text)} from EXPERIMENTS.md or from a ledger-rules.json."""
    text = read_text(path)
    if text is None:
        return {}
    if text.lstrip().startswith("{"):
        try:
            doc = json.loads(text)
        except ValueError:
            return {}
        if doc.get("schema") != RULES_SCHEMA:
            return {}
        return {r["id"]: (r["verdict"], " ".join(r["classes"])) for r in doc.get("rows") or []}
    return ledger_rows(text)


def ledger_rule(rows, family, hw_class):
    """("on" | "off" | "unknown", row id or None, verdict or None) for one hardware class.

    The family's rows are the row itself and its replications (H34, H34x, H34y...).
    A row counts for the class when it names the class's CPU model number exactly
    (row_classes). A REFUTED row for the class turns the default off, else a
    SUPPORTED one turns it on; PENDING and INCONCLUSIVE rows decide nothing, and a
    class no row names is unknown."""
    toks = {t.lower() for t in class_tokens(hw_class)}
    hits = sorted((rid, v) for rid, (v, text) in rows.items()
                  if family_of(rid, family) and toks and toks & set(row_classes(text)))
    for want, out in (("REFUTED", "off"), ("SUPPORTED", "on")):
        for rid, v in hits:
            if v == want:
                return out, rid, v
    return "unknown", None, None


def ledger_promotes(rows, variant_key, hw_class):
    """(True, why) when the variant's promotion row is SUPPORTED and names this hardware class."""
    row = PROMOTION_ROWS.get(variant_key)
    if not row or row not in rows:
        return False, None
    verdict, text = rows[row]
    if verdict != "SUPPORTED":
        return False, "EXPERIMENTS.md %s is %s" % (row, verdict or "unset")
    if not any(t.lower() in text.lower() for t in class_tokens(hw_class)):
        return False, "EXPERIMENTS.md %s is SUPPORTED, but for another hardware class" % row
    return True, "EXPERIMENTS.md %s SUPPORTED on %s" % (row, hw_class)


def class_tokens(hw_class):
    """Words a ledger row uses for a hardware class: the GPU platform ("Battlemage"),
    the CPU's model number ("6741P" of "Intel(R) Xeon(R) 6741P"), "Gaudi"."""
    if not hw_class:
        return []
    kind, _, name = hw_class.partition(":")
    if kind == "hpu":
        return ["Gaudi"]
    if kind == "cpu":
        words = [w for w in re.split(r"\s+", name) if re.search(r"\d", w)]
        return words[-1:] if words else ([name] if name else [])
    return [name] if name else []


def measured_better(measurements, target, variant, baseline, hw_class):
    """(True, why) when a cached local measurement shows variant faster at equal-or-better quality."""
    for m in (measurements or {}).get("rows") or []:
        if (m.get("model"), m.get("variant"), m.get("baseline"), m.get("hw_class")) != (target, variant, baseline, hw_class):
            continue
        if (m.get("speedup") or 0) > 1.0 and m.get("quality") in ("equal", "better"):
            return True, "local measurement: %.2fx vs %s at %s quality (%s)" % (m["speedup"], baseline, m["quality"], m.get("quality_set", "quality set"))
    return False, None


# ---------------------------------------------------------------- optimize -----

def want_precision(engine, device):
    if engine == "ovms":
        return "int4", "openvino-ir"
    if engine == "llama.cpp":
        return "int4", "gguf"
    return "bf16", "safetensors"


def rank_key(c, org_order):
    # tier, then QAT first, then a bundled MTP head, then the org order, then id.
    return (c["tier"], not c["qat"], not c["mtp"], org_order.get(c["org"], 9), c["id"].lower())


def drafter_matches(d, chosen, target):
    """A drafter fits when it serves the target and, for a QAT target, is QAT too."""
    if d["verifier"] and d["verifier"].lower() != target.lower():
        return False
    return d["qat"] == chosen["qat"]


def optimize(bundle, engine, device, opts=None):
    """The k3d-gpu.modelopt/v1 decision. Pure: reads its arguments only."""
    o = dict(opts or {})
    thinking_want = o.get("thinking") or "off"
    draft_want = o.get("draft") or "auto"
    vllm_version = tuple(int(x) for x in (o.get("vllm_version") or "0.31.0").split(".")[:3])
    prefix_caching = o.get("prefix_caching")
    hw_class = o.get("hw_class")
    target = bundle["target"]
    choices = []

    def choose(fact, choice, source):
        choices.append({"fact": fact, "choice": choice, "source": source})

    doc = {"schema": OPT_SCHEMA, "requested": bundle.get("requested", target), "target": target,
           "engine": engine, "device": device, "metadata": bundle.get("source"), "choices": choices,
           "model": None, "revision": None, "variant": None, "ranking": [], "conversion": None,
           "speculative": None, "thinking": None, "tool_parser": None, "reasoning_parser": None,
           "text_only": False, "engine_args": [], "prefix_caching": prefix_caching, "notes": [], "facts": None, "moe": None,
           "fallback_model": None, "fallback_revision": None, "warning": None}
    if bundle.get("source") == "fallback":
        choose("Hugging Face unreachable (%s)" % bundle.get("error"), "the built-in map of known-good variants", "console/model_opt.py FALLBACK")
    if target not in bundle["repos"]:
        why = bundle.get("error") or "not found"
        if engine == "ovms" and not name_of(target).lower().endswith("-ov"):
            doc["warning"] = "no metadata for %s (%s) and it is not an OpenVINO IR: OVMS keeps its default model" % (target, why)
            choose("no metadata for %s" % target, "keep the engine's default model", why)
        else:
            doc["warning"] = "no metadata for %s (%s): serving it as given with the engine's defaults" % (target, why)
            doc["model"] = target
            choose("no metadata for %s" % target, "serve it as given, engine defaults", why)
        return doc
    trec = bundle["repos"][target]
    facts = model_facts(trec, target)
    doc["facts"], doc["moe"] = facts, moe_facts(facts)
    blocked = UNSUPPORTED.get((engine, facts["model_type"]))
    if blocked:
        gb = (facts["params_total"] or 0) * 2 / 1e9
        doc["warning"] = "%s (%s) does not run on %s here: %s. The path that does: llama.cpp with a self-built GGUF and its weights (~%.0f GB bf16) in host RAM; keeping the engine's default model" % (
            target, facts["model_type"], engine, blocked, gb)
        choose("architecture %s" % facts["model_type"], "not served by %s on Intel devices: keep the engine's default model" % engine, blocked)
        return doc
    if facts["moe"]:
        choose("MoE: %s experts, %s per token, %s params, ~%sB active" % (facts["experts"], facts["experts_per_token"], facts["params_total"], facts["params_active_b"]),
               "recorded for the bandwidth model (decode moves the active experts' bytes)", facts["source"])
    elif facts["model_type"]:
        choose("dense %s, %s params" % (facts["model_type"], facts["params_total"]), "no MoE adjustment", facts["source"])
    if facts["context"]:
        choose("context %d tokens" % facts["context"], "kept at the agent's own --max-model-len / OVMS default", facts["source"])

    org_order = {org_of(target): 0, "OpenVINO": 1, "Intel": 2, "RedHatAI": 3, "88plug": 4}
    cands = [classify(r, target) for r in bundle["repos"].values()]
    drafters = [c for c in cands if c["drafter"]]
    models = [c for c in cands if not c["drafter"] and c["weights"]]
    precision, fmt = want_precision(engine, device)
    if engine == "vllm" and o.get("precision") == "int4":
        precision = "int4"

    # The QAT-matched OpenVINO conversion: offered when the author publishes
    # unquantized QAT weights and the engine serves OpenVINO IR.
    qat_src = next((c for c in models if c["qat"] and c["format"] == "safetensors" and c["tier"] == 0), None)
    if engine == "ovms" and qat_src:
        conv = {"id": "qat-convert", "sha": qat_src["sha"], "url": qat_src["url"], "org": "self-built", "tier": 2,
                "self": False, "qat": True, "mtp": False, "mtp_source": None, "drafter": None, "format": "openvino-ir",
                "precision": "int4", "quant": dict(Q4_0, method="INT4_SYM"), "nvidia_only": False, "weights": True,
                "verifier": None, "from": qat_src["id"]}
        models.append(conv)
        choose("%s is QAT for Q4_0 (symmetric int4, groups of 32)" % qat_src["id"],
               "candidate: self-built OpenVINO IR, INT4_SYM group_size 32, from the QAT weights", Q4_0["source"])

    gguf_qat = next((c for c in models if c["qat"] and c["format"] == "gguf" and c["tier"] == 0), None)
    if engine == "llama.cpp" and qat_src and gguf_qat:
        models.append(dict(gguf_qat, id="qat-gguf-exact", org="self-built", tier=2, sha=qat_src["sha"], url=qat_src["url"],
                           quant=dict(Q4_0, method="Q4_0, scale = QAT grid step"), **{"from": qat_src["id"]}))
        choose("%s re-quantizes the QAT weights with llama.cpp's absmax/-8 scales" % gguf_qat["id"],
               "candidate: self-built Q4_0 with the QAT grid step as the block scale (no stock tool writes it)",
               "llama.cpp ggml-quants.c quantize_row_q4_0_ref; research run REFERENCE.md 3.2 (weight-space probe)")

    ranked, excluded = [], []
    for c in models:
        why = None
        if c["nvidia_only"] and device in ("CPU", "GPU", "HPU"):
            why = "NVFP4/MXFP4: NVIDIA only, no Intel engine documents it"
        elif c["format"] != fmt:
            why = "%s, %s serves %s" % (c["format"], engine, fmt)
        elif engine != "vllm" and c["precision"] != precision:
            why = "%s, the %s target is %s" % (c["precision"], engine, precision)
        elif engine == "vllm" and c["precision"] != precision:
            why = "%s, the vLLM target on %s is %s" % (c["precision"], device, precision)
        elif engine == "vllm" and c["qat"] and precision == "bf16":
            why = "QAT weights serve an int4 target; this target is bf16"
        if why:
            excluded.append({"id": c["id"], "why": why})
        else:
            ranked.append(c)
    ranked.sort(key=lambda c: rank_key(c, org_order))

    pick = ranked[0] if ranked else None
    promoted = None
    if pick:
        rows = ledger_rows(o.get("ledger"))
        for c in ranked[1:]:
            key = c["id"]
            ok, why = ledger_promotes(rows, key, hw_class)
            if not ok:
                ok, why2 = measured_better(o.get("measurements"), target, c["id"], pick["id"], hw_class)
                why = why2 or why
            c["promotion"] = why
            if ok:
                promoted = c
                break
    forced = o.get("variant")
    if forced:
        hit = next((c for c in ranked if c["id"] == forced), None)
        if hit:
            choose("K3D_GPU_AGENT_VARIANT=%s" % forced, "serve it (override)", "environment")
            pick = hit
        else:
            doc["notes"].append("K3D_GPU_AGENT_VARIANT=%s is not a candidate for %s on %s; keeping the ranked pick" % (forced, engine, device))
    elif promoted:
        choose("%s ranks below %s, but %s" % (promoted["id"], pick["id"], promoted["promotion"]), "promote it", promoted["promotion"])
        pick = promoted
    built = next((c for c in ranked if c["id"] != "qat-convert"), None)
    if built:
        doc["fallback_model"], doc["fallback_revision"] = built["id"], built["sha"]
    doc["ranking"] = [{"rank": i + 1, "id": c["id"], "tier": TIER_NAMES[c["tier"]], "qat": c["qat"], "mtp": c["mtp"],
                       "quant": c["quant"], "sha": c["sha"], "promotion": c.get("promotion")} for i, c in enumerate(ranked)]
    doc["excluded"] = excluded

    searched = ("searched: " + ", ".join(x["url"] for x in bundle.get("searches", [])[:2])) if bundle.get("searches") else "-"
    if pick is None and fmt != "safetensors":
        doc["warning"] = "no %s %s variant of %s for %s: keeping the engine's default model (K3D_GPU_AGENT_BACKEND=vllm serves the safetensors)" % (precision, fmt, target, engine)
        choose("no %s %s variant of %s" % (precision, fmt, target), "keep the engine's default model", searched)
    elif pick is None:
        doc["model"] = target
        choose("no %s %s variant of %s" % (precision, fmt, target), "serve %s as given, engine defaults" % target, searched)
    elif pick["id"] == "qat-gguf-exact":
        doc["model"], doc["revision"] = None, None
        doc["warning"] = "qat-gguf-exact needs a quantizer that writes the QAT grid step as the Q4_0 scale; no stock tool does: keeping %s" % (doc["fallback_model"] or "the engine default")
        doc["model"], doc["revision"] = doc["fallback_model"], doc["fallback_revision"]
    elif pick["id"] == "qat-convert":
        out = "k3d-gpu/%s-int4-sym-g32-ov" % name_of(target)
        doc["conversion"] = conversion_job(pick["from"], pick["sha"], out)
        doc["model"], doc["revision"] = out, None
        choose("%s chosen" % ("QAT conversion " + ("forced" if forced else "promoted")),
               "one-time pinned conversion (optimum-intel %s, nncf %s, openvino %s); the quality effect is EXPERIMENTS.md H54q's to measure, not claimed" % (
                   CONVERT_PINS["optimum-intel"], CONVERT_PINS["nncf"], CONVERT_PINS["openvino"]), OPTIMUM_EXPORT)
    else:
        doc["model"], doc["revision"] = pick["id"], pick["sha"]
        q = pick["quant"] or {}
        qtext = (" (%s%s%s)" % (q.get("method") or "", ", sym" if q.get("sym") else ", asym" if q.get("sym") is False else "",
                                ", group %s" % q["group_size"] if q.get("group_size") else "")) if q else ""
        choose("%d %s candidate(s); top: %s [%s]%s" % (len(ranked), fmt, pick["id"], TIER_NAMES[pick["tier"]], qtext),
               "serve %s@%s" % (pick["id"], (pick["sha"] or "")[:12]), (q.get("source") if q else None) or pick["url"])
    if doc["variant"] is None and pick:
        doc["variant"] = pick["id"]
    if pick and pick["id"] != "qat-convert":
        variant_traps(doc, choose, bundle["repos"].get(pick["id"]) or {}, trec, facts, engine)
    for r in doc["ranking"][1:]:
        if r["id"] in ("qat-convert", "qat-gguf-exact") and doc["variant"] != r["id"]:
            row = PROMOTION_ROWS.get(r["id"])
            choose("%s ranks %d (self-built)" % (r["id"], r["rank"]),
                   "not picked until %sa local measurement says it is faster at equal-or-better quality (K3D_GPU_AGENT_VARIANT=%s forces it)" % (
                       "EXPERIMENTS.md %s is SUPPORTED for %s or " % (row, hw_class or "this hardware class") if row else "", r["id"]),
                   r.get("promotion") or ("EXPERIMENTS.md %s not run" % row if row else "no measurement yet"))

    # Parsers by model family.
    fam = FAMILY_PARSERS.get(facts["model_type"] or "")
    if fam and engine in ("vllm", "ovms"):
        tool, reasoning = fam[engine]
        doc["tool_parser"], doc["reasoning_parser"] = tool, reasoning
        if tool or reasoning:
            choose("model family %s" % facts["model_type"], "tool parser %s, reasoning parser %s" % (tool or "auto", reasoning or "auto"), fam["source"])
        else:
            choose("model family %s" % facts["model_type"], "%s detects tool and reasoning parsers from the chat template" % engine,
                   OVMS_DOC + "docs/parameters.md (L169-170)")
    elif engine == "ovms":
        choose("model family %s" % facts["model_type"], "OVMS detects tool and reasoning parsers from the chat template",
               OVMS_DOC + "docs/parameters.md (L169-170)")

    # Thinking default for agent workloads.
    think_on = facts["thinking_default"] if thinking_want == "model" else thinking_want == "on"
    doc["thinking"] = think_on
    kwargs = {"enable_thinking": think_on}
    if facts["reasoning_efforts"] and "medium" in facts["reasoning_efforts"]:
        # The template injects a system prompt for its default effort (xhigh) and
        # for low; medium injects nothing and is one it accepts.
        kwargs["reasoning_effort"] = "medium"
    doc["chat_template_kwargs"] = kwargs if facts["thinking_default"] is not None else None
    if facts["thinking_default"] is not None:
        default_text = "on" if facts["thinking_default"] else "off"
        if engine == "vllm" and vllm_version >= (0, 14, 0):
            doc["engine_args"] += ["--default-chat-template-kwargs", json.dumps(kwargs, sort_keys=True)]
            choose("chat template thinks %s by default%s" % (default_text, ", reasoning_effort in %s" % "/".join(facts["reasoning_efforts"]) if facts["reasoning_efforts"] else ""),
                   "thinking %s for agents, pinned server-side: --default-chat-template-kwargs '%s' (a request's own kwargs still win; K3D_GPU_AGENT_THINKING=on|off|model)" % (
                       "on" if think_on else "off", json.dumps(kwargs, sort_keys=True)),
                   "vllm entrypoints cli_args.py default_chat_template_kwargs (v0.14.0+); " + (facts["thinking_source"] or ""))
        elif engine == "vllm":
            doc["notes"].append("vLLM %s has no --default-chat-template-kwargs: clients send chat_template_kwargs %s" % (".".join(map(str, vllm_version)), json.dumps(kwargs, sort_keys=True)))
        elif engine == "llama.cpp":
            doc["notes"].append("thinking is %s by default for this model; clients send chat_template_kwargs %s" % (default_text, json.dumps(kwargs, sort_keys=True)))
        elif engine == "ovms":
            choose("chat template thinks %s by default" % default_text,
                   "OVMS has no server-side default: agents send chat_template_kwargs %s per request (k3d-gpu's own warm-up request sends enable_thinking false)" % json.dumps(kwargs, sort_keys=True),
                   OVMS_DOC + "docs/model_server_rest_api_chat.md (L223)")
            if facts["thinking_default"] != think_on:
                doc["notes"].append("thinking is %s by default for this model and OVMS cannot change that server-side: agents send chat_template_kwargs %s" % (default_text, json.dumps(kwargs, sort_keys=True)))

    # Sampling: the engines read generation_config.json themselves; a card that
    # recommends other values for non-thinking mode gets them when thinking is off.
    nothink = facts["card_sampling"].get("non_thinking")
    if nothink and not think_on:
        vals = nothink["values"]
        src = "%s/blob/%s/README.md (L%d)" % (HF + "/" + target, trec.get("sha"), nothink["line"])
        if engine == "vllm":
            doc["engine_args"] += ["--override-generation-config", json.dumps(vals, sort_keys=True)]
            choose("card: non-thinking sampling %s" % ", ".join("%s=%s" % (k, vals[k]) for k in sorted(vals)),
                   "--override-generation-config (thinking is off)", src + "; vLLM arg_utils.py --override-generation-config (v0.24.0+)")
        else:
            choose("card: non-thinking sampling %s" % ", ".join("%s=%s" % (k, vals[k]) for k in sorted(vals)),
                   "%s reads generation_config.json (the thinking-mode values) and has no server override: clients send these with thinking off" % engine, src)
            doc["notes"].append("thinking off: the card recommends %s; send them per request" % ", ".join("%s=%s" % (k, vals[k]) for k in sorted(vals)))
    elif facts["generation_config"]:
        gc = facts["generation_config"]
        choose("generation_config.json: %s" % ", ".join("%s=%s" % (k, gc[k]) for k in sorted(gc)),
               "the engine applies it as the request default", "%s/blob/%s/generation_config.json" % (HF + "/" + target, trec.get("sha")))

    # Text-only load.
    if facts["vision"] or facts["audio"]:
        if engine == "vllm" and vllm_version >= (0, 17, 0):
            doc["text_only"] = True
            doc["engine_args"].append("--language-model-only")
            choose("vision%s tower in config.json; agents send text" % ("/audio" if facts["audio"] else ""),
                   "--language-model-only (no encoder memory, larger KV pool)", "https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/engine/arg_utils.py (L1429, v0.17.0+)")
        elif engine == "ovms":
            choose("vision tower in the IR", "loaded as a VLM pipeline: OVMS refuses LM on a VLM directory",
                   OVMS_DOC + "src/llm/servable_initializer.cpp (L588-641)")

    # Speculative decoding.
    spec_decide(doc, choose, engine, device, pick, drafters, target, facts, draft_want, prefix_caching, vllm_version, o)
    return doc


def variant_traps(doc, choose, vrec, trec, facts, engine):
    """What the picked variant's own files say that the target's do not."""
    vid = vrec.get("id")
    if not vid or vid == trec.get("id"):
        return
    a, b = vrec.get("chat_template_sha256"), trec.get("chat_template_sha256")
    if a and b and a != b:
        doc["notes"].append("%s ships a chat template other than %s's: serve it with %s's chat_template.jinja (%s/blob/%s/chat_template.jinja)" % (
            vid, trec["id"], trec["id"], HF + "/" + trec["id"], trec.get("sha")))
    qc = (vrec.get("config") or {}).get("quantization_config") or {}
    if qc.get("kv_cache_scheme") and engine == "vllm":
        doc["notes"].append("%s's quantization_config carries a kv_cache_scheme: vLLM turns on an FP8 KV cache for it; pin --kv-cache-dtype to compare runs" % vid)
    cap = vrec.get("tokenizer_max_length")
    if cap and facts["context"] and cap < facts["context"]:
        doc["notes"].append("%s's tokenizer_config.json caps model_max_length at %d, below the model's %d-token context" % (vid, cap, facts["context"]))
    oc = (vrec.get("openvino_config") or {}).get("quantization_config") or {}
    lm = (oc.get("quantization_configs") or {}).get("lm_model") or {}
    for x in vrec.get("card_lines") or []:
        m = re.search(r"group_size\W+(\d+)", x["text"])
        if m and lm.get("group_size") and int(m.group(1)) != lm["group_size"]:
            choose("%s card says group_size %s; its openvino_config.json says %s (%s, sym=%s)" % (vid, m.group(1), lm["group_size"], lm.get("quant_method"), lm.get("sym")),
                   "trust the config: it is what the runtime loads", "%s/blob/%s/openvino_config.json" % (HF + "/" + vid, vrec.get("sha")))
            break


def spec_decide(doc, choose, engine, device, pick, drafters, target, facts, draft_want, prefix_caching, vllm_version, o):
    if draft_want == "off":
        choose("K3D_GPU_AGENT_DRAFT=off", "no speculative decoding", "environment")
        return
    chosen = pick or {"qat": False, "mtp": False, "id": target, "tier": 0}
    fit = sorted((d for d in drafters if drafter_matches(d, chosen, target)), key=lambda d: (d["tier"], d["id"].lower()))
    unfit = [d for d in drafters if d not in fit]
    for d in unfit:
        doc["notes"].append("drafter %s skipped: %s" % (d["id"], "trained for another verifier" if d["verifier"] and d["verifier"].lower() != target.lower()
                                                         else "a QAT target needs a QAT drafter of the same precision (model card)" if chosen["qat"]
                                                         else "QAT drafter, the target is not QAT"))
    if engine == "ovms":
        if chosen.get("mtp"):
            if prefix_caching is True and draft_want == "mtp":
                choose("K3D_GPU_AGENT_DRAFT=mtp with K3D_GPU_AGENT_PREFIX_CACHING=true",
                       "MTP off: OVMS MTP runs only with prefix caching off, and the explicit prefix-caching setting wins", OVMS_SPEC_DEMO + " (L30)")
                doc["notes"].append("MTP not enabled: K3D_GPU_AGENT_PREFIX_CACHING=true, and OVMS MTP needs it off")
                return
            if prefix_caching is False or draft_want == "mtp":
                # An explicit K3D_GPU_AGENT_PREFIX_CACHING=false already sets the flag.
                args = ["--draft_model_path=."] + ([] if prefix_caching is False else ["--enable_prefix_caching=false"])
                doc["speculative"] = {"kind": "mtp", "args": args}
                choose("%s bundles openvino_mtp_model.xml" % chosen["id"],
                       "MTP on: --draft_model_path . with prefix caching off; OVMS forces greedy decoding and requests must set max_tokens; num_assistant_tokens 2 measured best (1.78x)",
                       OVMS_SPEC_DEMO + " (L22-48, L30); https://github.com/openvinotoolkit/openvino_notebooks/blob/latest/notebooks/qwen3.8-mtp/qwen3.8-mtp.ipynb")
                doc["notes"].append("MTP on: OVMS decodes greedily, prefix caching is off, and every request must set max_tokens")
            else:
                choose("%s bundles openvino_mtp_model.xml; OVMS MTP needs prefix caching off and forces greedy" % chosen["id"],
                       "MTP off: prefix caching stays on (EXPERIMENTS.md H25 SUPPORTED: added-agent TTFT 9.4x faster with it); MTP (1.78x at n=2 in OpenVINO's notebook, greedy only, max_tokens required) is unmeasured on agent turns here. K3D_GPU_AGENT_DRAFT=mtp turns it on",
                       OVMS_SPEC_DEMO + " (L30); EXPERIMENTS.md H25")
            return
        if facts["vision"]:
            choose("VLM pipeline", "no Fast Draft: OVMS refuses it on VLM pipelines", OVMS_DOC + "src/llm/servable_initializer.cpp (L673-681)")
            return
        if fit:
            choose("drafter %s is not an OpenVINO IR" % fit[0]["id"], "no draft: OVMS drafts with an exported OpenVINO model", OVMS_SPEC_DEMO)
        else:
            choose("no OpenVINO drafter for %s" % target, "no draft (the H34 CPU Fast Draft rule covers OpenVINO Qwen3 only)", "EXPERIMENTS.md H34")
        return
    if engine == "llama.cpp":
        gg = [d for d in fit if d["format"] == "gguf"]
        if chosen.get("mtp"):
            doc["speculative"] = {"kind": "mtp", "args": ["--spec-type", "draft-mtp", "--spec-draft-n-max", "3"]}
            choose("%s carries an MTP head (%s)" % (chosen["id"], chosen.get("mtp_source")),
                   "--spec-type draft-mtp --spec-draft-n-max 3", "https://github.com/ggml-org/llama.cpp/blob/v0.7.0/common/arg.cpp (L4161, L4295); llama.cpp PR 22673")
        elif gg:
            doc["speculative"] = {"kind": "draft", "model": gg[0]["id"], "args": ["-md", gg[0]["id"]]}
            choose("GGUF drafter %s" % gg[0]["id"], "-md (draft model)", LLAMACPP_ARGS)
        else:
            choose("no GGUF drafter for %s" % target, "no draft", LLAMACPP_ARGS)
            if chosen.get("qat") and any(d["qat"] for d in drafters):
                doc["notes"].append("the QAT assistant exists only as safetensors: a GGUF of it (convert_hf_to_gguf.py, then Q4_0) would be the matching drafter; adopt only if measured faster (llama.cpp saw no MoE gain)")
        return
    # vLLM
    if device == "CPU" and vllm_version < (0, 31, 0):
        choose("vLLM %s on CPU" % ".".join(map(str, vllm_version)), "no speculative decoding: the hardware table marks it unsupported on CPU before v0.31.0",
               "https://github.com/vllm-project/vllm/blob/v0.24.0/docs/features/README.md (L67)")
        return
    if device == "HPU":
        choose("vLLM on Gaudi runs eager + torch.compile (PT_HPU_LAZY_MODE=0)",
               "no speculative decoding by default: vllm-gaudi marks it experimental and not fully supported with torch.compile (K3D_GPU_AGENT_DRAFT=mtp turns it on)", VLLM_GAUDI_SD)
        if draft_want != "mtp":
            return
    cfg = None
    if chosen.get("mtp"):
        cfg = {"method": "mtp", "num_speculative_tokens": 3}
        choose("%s has a built-in MTP head (%s)" % (chosen["id"], chosen.get("mtp_source")),
               "--speculative-config %s" % json.dumps(cfg), VLLM_SPEC_DOC + " (MTPModelTypes -> mtp); https://huggingface.co/RedHatAI/Qwen3.8-27B-INT4 README (num_speculative_tokens 3)")
    else:
        for d in fit:
            if d["drafter"] == "assistant":
                if facts and facts["moe"]:
                    doc["notes"].append("MoE target: Google says the 26B A4B drafter may not speed up at batch 1 without good parallelism (https://ai.google.dev/gemma/docs/mtp/overview); "
                                        "if vLLM's V1 runner fails on it, VLLM_USE_V2_MODEL_RUNNER=1 (https://github.com/vllm-project/vllm/pull/51493); K3D_GPU_AGENT_DRAFT=off turns the drafter off")
                cfg = {"method": "mtp", "model": d["id"], "num_speculative_tokens": 4}
                src = VLLM_MTP_DOC + " (L12-36: gemma4 assistant drafters, method mtp); https://github.com/vllm-project/recipes/blob/main/Google/Gemma4.md (num_speculative_tokens 4)"
            elif d["drafter"] in ("eagle3", "dspark") and not (d["drafter"] == "dspark" and vllm_version < (0, 26, 0)):
                cfg = {"method": d["drafter"], "model": d["id"], "num_speculative_tokens": 3 if d["drafter"] == "eagle3" else 7}
                src = d["url"] + " (README vllm serve command)"
            else:
                continue
            choose("drafter %s [%s]" % (d["id"], TIER_NAMES[d["tier"]]), "--speculative-config %s" % json.dumps(cfg), src)
            break
    if cfg is None:
        choose("no MTP head or drafter for %s" % target, "no speculative decoding", VLLM_SPEC_DOC)
        return
    doc["speculative"] = {"kind": cfg["method"], "model": cfg.get("model"), "config": cfg,
                          "args": ["--speculative-config", json.dumps(cfg, sort_keys=True)]}


def conversion_job(src, sha, out):
    """The pinned one-time conversion: what to run, where, and what it pins."""
    pins = " ".join("%s==%s" % kv for kv in sorted(CONVERT_PINS.items()))
    cmd = [
        "python3 -m venv /tmp/k3d-gpu-convert && . /tmp/k3d-gpu-convert/bin/activate",
        "pip install %s" % pins,
        "python3 -c 'import huggingface_hub as h; h.snapshot_download(\"%s\", revision=\"%s\", local_dir=\"src\")'" % (src, sha),
        "optimum-cli export openvino -m src --task image-text-to-text --weight-format int4 --sym --group-size 32 --ratio 1.0 --backup-precision none --trust-remote-code %s" % out,
        "# then check the INT4 scales equal the QAT grid step: no doc says NNCF's scale rule reproduces it",
    ]
    return {"from": src, "revision": sha, "output": out, "pins": CONVERT_PINS, "commands": cmd,
            "source": OPTIMUM_EXPORT + " (L48-60, L109: --sym; L116-118: --group-size; --backup-precision)"}


# ---------------------------------------------------------------- render -------

def summary_line(doc, host=None):
    parts = []
    if host:
        parts.append("Detected %s;" % host)
    model = doc["model"] or "the engine's default model"
    spec = doc["speculative"]
    spec_text = (" with %s" % ("MTP" if spec["kind"] == "mtp" and not spec.get("model") else
                               "the %s drafter" % (spec.get("model") or spec["kind"]))) if spec else ", no drafter"
    parts.append("picked %s on %s (%s)%s;" % (model, doc["engine"].upper() if doc["engine"] == "ovms" else doc["engine"], doc["device"], spec_text))
    if doc["thinking"] is not None:
        parts.append("thinking %s for agents;" % ("on" if doc["thinking"] else "off"))
    parts.append("why -> plan --model %s --explain" % doc["requested"])
    return " ".join(parts)


def explain_lines(doc):
    out = ["model optimizer: %s on %s (%s), metadata from %s" % (doc["requested"], doc["engine"], doc["device"], doc["metadata"])]
    if doc["requested"] != doc["target"]:
        out.append("  %s is a quantized repo of %s: optimizing for the base" % (doc["requested"], doc["target"]))
    for c in doc["choices"]:
        out.append("  %s -> %s [%s]" % (c["fact"], c["choice"], c["source"]))
    if doc["ranking"]:
        out.append("  variant ranking (official > RedHatAI > community/self-built; a lower rank wins only when measured better):")
        for r in doc["ranking"]:
            out.append("    %d. %s [%s]%s%s" % (r["rank"], r["id"], r["tier"], ", QAT" if r["qat"] else "", ", MTP head" if r["mtp"] else ""))
    for e in doc.get("excluded") or []:
        out.append("    excluded: %s (%s)" % (e["id"], e["why"]))
    if doc["conversion"]:
        out.append("  conversion (one time, cached; run where the model PVC is mounted):")
        out += ["    " + c for c in doc["conversion"]["commands"]]
    if doc["warning"]:
        out.append("  warning: " + doc["warning"])
    for n in doc["notes"]:
        out.append("  note: " + n)
    out.append("  engine args: %s" % (" ".join(engine_args(doc)) or "(none)"))
    return out


def engine_args(doc):
    """Extra server arguments, in the engine's own spelling."""
    args = list(doc["engine_args"])
    if doc["speculative"]:
        args += doc["speculative"]["args"]
    for a in args:
        if "'" in a or "\n" in a:
            raise ValueError("engine argument %r has a quote or newline" % a)
    return args


def shell_quote(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def shell_lines(doc, host=None):
    """KEY=value lines the launcher reads (values quoted for `eval`)."""
    args = engine_args(doc)
    kv = {
        "OPT_MODEL": doc["model"] or "",
        "OPT_REVISION": doc["revision"] or "",
        "OPT_TOOL_PARSER": doc["tool_parser"] or "",
        "OPT_REASONING_PARSER": doc["reasoning_parser"] or "",
        "OPT_SPEC": (doc["speculative"] or {}).get("kind", ""),
        "OPT_CONVERSION": "1" if doc["conversion"] else "",
        "OPT_FALLBACK_MODEL": doc["fallback_model"] or doc["target"],
        "OPT_FALLBACK_REVISION": doc["fallback_revision"] or "",
        "OPT_ARGS_JSON": json.dumps(args),
        "OPT_SUMMARY": summary_line(doc, host),
        "OPT_WARNING": doc["warning"] or "",
    }
    return ["%s=%s" % (k, shell_quote(v)) for k, v in kv.items()]


def eat_patch(doc):
    """Changes to one EAT vLLM modelConfigs entry: args added, never removed."""
    parsers = []
    if doc["tool_parser"]:
        parsers += ["--enable-auto-tool-choice", "--tool-call-parser", doc["tool_parser"]]
    if doc["reasoning_parser"]:
        parsers += ["--reasoning-parser", doc["reasoning_parser"]]
    return {"model": doc["model"], "requested": doc["requested"], "add_args": parsers + engine_args(doc),
            "speculative_config": json.dumps(doc["speculative"]["config"], sort_keys=True) if doc["speculative"] and doc["speculative"].get("config") else None,
            "reasoning_parser": doc["reasoning_parser"], "tool_parser": doc["tool_parser"],
            "explain": explain_lines(doc), "summary": summary_line(doc)}


def read_text(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def main(argv):
    ap = argparse.ArgumentParser(prog="model_opt.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--model", required=True)
    f.add_argument("--revision", default="main")
    f.add_argument("--out")
    f.add_argument("--cache")
    f.add_argument("--fixtures")
    lr = sub.add_parser("ledger-rule")
    lr.add_argument("--family", required=True)
    lr.add_argument("--hw-class", required=True)
    lr.add_argument("--ledger")
    lg = sub.add_parser("ledger-rules")
    lg.add_argument("--ledger", required=True)
    lg.add_argument("--out")
    p = sub.add_parser("optimize")
    p.add_argument("--bundle", required=True)
    p.add_argument("--engine", required=True, choices=("ovms", "vllm", "llama.cpp"))
    p.add_argument("--device", required=True, choices=("CPU", "GPU", "HPU", "NPU"))
    p.add_argument("--vllm-version")
    p.add_argument("--thinking", choices=("off", "on", "model"))
    p.add_argument("--variant")
    p.add_argument("--precision", choices=("int4", "bf16"))
    p.add_argument("--draft", choices=("auto", "off", "mtp"))
    p.add_argument("--prefix-caching", choices=("true", "false"))
    p.add_argument("--hw-class")
    p.add_argument("--host")
    p.add_argument("--ledger")
    p.add_argument("--measurements")
    p.add_argument("--format", default="json", choices=("json", "shell", "summary", "explain", "eat"))
    a = ap.parse_args(argv[1:])
    if a.cmd == "ledger-rule":
        out, rid, verdict = ledger_rule(read_ledger(a.ledger), a.family, a.hw_class)
        sys.stdout.write("%s %s %s\n" % (out, rid or "-", verdict or "-"))
        return 0
    if a.cmd == "ledger-rules":
        text = json.dumps(ledger_rules_doc(ledger_rows(read_text(a.ledger))), indent=1) + "\n"
        if a.out:
            with open(a.out + ".tmp", "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(a.out + ".tmp", a.out)
        else:
            sys.stdout.write(text)
        return 0
    if a.cmd == "fetch":
        if a.fixtures or a.cache or not a.out:
            b = load_or_fetch(a.model, a.revision, cache=a.cache, fixtures=a.fixtures)
        else:
            try:
                b = fetch_bundle(a.model, a.revision)
            except FetchError as e:
                sys.stderr.write("model_opt: %s\n" % e)
                return 2
        text = json.dumps(b, indent=1, sort_keys=True) + "\n"
        if a.out:
            with open(a.out, "w", encoding="utf-8") as fh:
                fh.write(text)
        else:
            sys.stdout.write(text)
        return 0
    try:
        with open(a.bundle, encoding="utf-8") as fh:
            bundle = json.load(fh)
    except (OSError, ValueError) as e:
        sys.stderr.write("model_opt: %s\n" % e)
        return 2
    try:
        meas = json.loads(read_text(a.measurements) or "{}")
    except (OSError, ValueError) as e:
        sys.stderr.write("model_opt: ignoring %s: %s\n" % (a.measurements, e))
        meas = {}
    if not isinstance(meas, dict) or meas.get("schema") != MEASURE_SCHEMA:
        meas = {}
    doc = optimize(bundle, a.engine, a.device, {
        "vllm_version": a.vllm_version, "thinking": a.thinking, "variant": a.variant, "precision": a.precision,
        "draft": a.draft, "prefix_caching": None if a.prefix_caching is None else a.prefix_caching == "true",
        "hw_class": a.hw_class, "ledger": read_text(a.ledger), "measurements": meas})
    if a.format == "json":
        sys.stdout.write(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    elif a.format == "shell":
        sys.stdout.write("\n".join(shell_lines(doc, a.host)) + "\n")
    elif a.format == "summary":
        sys.stdout.write(summary_line(doc, a.host) + "\n")
    elif a.format == "explain":
        sys.stdout.write("\n".join([summary_line(doc, a.host)] + explain_lines(doc)) + "\n")
    else:
        sys.stdout.write(json.dumps(eat_patch(doc), indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
