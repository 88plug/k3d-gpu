#!/usr/bin/env python3
"""recipes: vLLM's model recipes as model serving facts for the vLLM agent.

console/vllm-recipes.json (hack/vllm-recipes, pinned in pins/registry.toml) carries
vllm-project/vllm's recipes/ at one commit, cut to what an Intel lookup can hit, plus
each pinned vLLM version's parser registries and flags. This module reads it offline:

  lookup      a model id (served, requested, optimizer target) -> its recipe
  target_for  the device -> (recipe hardware profile, generation)
  facts       the recipe's flags for that model and device, each classified:
                ADOPT    applied, subject to the flag's gate
                OWNED    k3d-gpu's own choice (fit, precision, thinking, speculative,
                         text-only, long context, plugins, performance): reported only
                otherwise unclassified: reported only
              Recipe env vars are reported, never applied.
  merge       folds the facts into the server arguments the launcher already built.
              Precedence per value: user env > SUPPORTED/REFUTED ledger row (MEASURED)
              > recipe > k3d-gpu default. A flag the base already sets (or its --no-
              pair) is never rewritten by a recipe.
  render      the recipe's own command for an Intel profile, with no k3d-gpu policy
              (what recipes.vllm.ai shows minus `vllm serve <id>` and the parallel
              flag), for tests and review.

Recipes give model facts. They never pick the engine, device, image, variant
precision, tensor parallelism, context or memory. OVMS and llama.cpp never read them.

  recipes.py facts   --model ID [--requested ID] [--target ID] --device GPU|HPU|CPU
                     [--pci-id 0xNNNN] [--cpu-brand S] [--engine-version X.Y.Z|unknown]
                     [--engine vllm-xpu|llm-scaler|gaudi-vllm] [--config FILE] [--file ART]
                     [--format json|explain]
  recipes.py merge   (facts options) --base-file FILE|- [--hw-class C] [--ledger FILE]
                     [--tool-parser P --tool-parser-from SRC] [--reasoning-parser P --reasoning-parser-from SRC]
                     [--title T --platform P] [--no-recipe WHY] [--format shell|json|explain]
                     base lines are "<arg><TAB><source>"; a parser source "env" is the user's
  recipes.py plan    --facts FILE [--plan FILE] [--backend B]   the device plan --explain's
                     vLLM block is for, as KEY=value lines (PV_DEVICE empty: not vLLM)
  recipes.py check   [--file ART]   load the artifact and print its pins

Python standard library only.
"""

import argparse
import json
import os
import re
import sys

SCHEMA = "k3d-gpu.vllm-recipes/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(HERE, "vllm-recipes.json")

# PCI device ids of the cards recipes.vllm.ai profiles (pci.ids, vendor 8086:
# e211 "Battlemage G21 [Arc Pro B60]", e223 "Battlemage G31 [Arc Pro B70]").
PCI_PROFILES = {"0xe211": "arc_pro_b60", "0xe223": "arc_pro_b70"}
XEON6 = re.compile(r"Xeon\(R\) 6\d{3}")

# Recipe flags k3d-gpu applies (bare name -> gate). Every name is a flag of every
# pinned vLLM version (tests/console/test_vllm_recipes.py), and canary/flags.sh
# emits them for the image surface check.
#   feature:X   applied only with recipe feature X (tool_calling, reasoning)
#   parser:K    the parser must be in the engine version's registry, else feature K is dropped
#   path:examples  a chat template from vLLM's examples/, only where EXAMPLES_DIR locates it
#   device      from the hardware layer, or the model layer on an Intel GPU; never
#               when the device base already sets the flag or its --no- pair
#   model       a model fact, any device
#   security    --trust-remote-code: only when config.json has auto_map
ADOPT = {
    "enable-auto-tool-choice": "feature:tool_calling", "tool-call-parser": "parser:tool",
    "reasoning-parser": "parser:reasoning", "chat-template": "path:examples",
    "tokenizer-mode": "feature:tool_calling", "exclude-tools-when-tool-choice-none": "feature:tool_calling",
    "reasoning-config": "feature:reasoning",
    "enforce-eager": "device", "disable-sliding-window": "device", "attention-backend": "device",
    "block-size": "device", "no-enable-prefix-caching": "device", "enable-prefix-caching": "device",
    "generation-config": "model", "mm-processor-cache-gb": "model",
    "trust-remote-code": "security",
}

# Recipe flags whose value is k3d-gpu's own decision: reported, never applied. A
# name ending in * matches by prefix.
OWNED = {
    "sizing": ("max-model-len", "max-num-batched-tokens", "max-num-seqs", "gpu-memory-utilization",
               "tensor-parallel-size", "tp", "data-parallel-size*", "pipeline-parallel-size", "kv-cache-dtype",
               "cpu-offload-gb", "swap-space", "enable-expert-parallel"),
    "precision": ("quantization", "dtype"),
    "thinking": ("default-chat-template-kwargs", "override-generation-config"),
    "speculative": ("speculative-config*",),
    "text_only": ("language-model-only",),
    "long_context": ("hf-overrides",),
    "plugin": ("tool-parser-plugin", "reasoning-parser-plugin", "logits-processors"),
    "performance": ("async-scheduling", "load-format", "compilation-config"),
}
OWNER = {
    "sizing": "fit-owned (k3d-gpu fit, K3D_GPU_AGENT_MAX_LEN/_GPUS/_MAX_NUM_SEQS)",
    "precision": "precision is k3d-gpu's variant policy (model optimizer, K3D_GPU_AGENT_PRECISION)",
    "thinking": "thinking is k3d-gpu's policy (model optimizer, K3D_GPU_AGENT_THINKING)",
    "speculative": "speculative decoding is the model optimizer's (K3D_GPU_AGENT_DRAFT)",
    "text_only": "text-only load is the model optimizer's",
    "long_context": "context is fit-owned",
    "plugin": "needs a file outside the image (k3d-gpu never fetches at boot)",
    "performance": "unmeasured on Intel devices here",
}

# vLLM's examples/ directory inside each engine's image, where the canary proved it
# (canary/surface.py). A recipe's --chat-template examples/<f> maps to <dir>/<f>; an
# engine with no entry keeps the model's own chat template.
EXAMPLES_DIR = {}

# Ledger rows that decide a recipe flag on a hardware class: {"family": "H6x", "match":
# {"model": recipe id, "class_token": "Battlemage"|"B70"|"Gaudi"},
# "flag": bare name, "value": v or None}. A SUPPORTED row sets the value, a REFUTED row
# keeps k3d-gpu's default; PENDING and INCONCLUSIVE rows decide nothing. A family
# added here is added to console/model_opt.py RULE_FAMILIES too.
MEASURED = []

# The recipe features k3d-gpu always applies (agents need parsed tool calls and
# reasoning), whatever the recipe's opt-in. spec_decoding and text_only are data for
# the model optimizer; long_context is never applied (the fit owns context).
APPLIED_FEATURES = ("tool_calling", "reasoning")
HW_STATES_OK = ("verified", "supported")


# ---------------------------------------------------------------- load ---------

def load(path=None):
    """(artifact, None) or (None, reason). $K3D_GPU_VLLM_RECIPES_FILE, else the
    vllm-recipes.json next to this module."""
    path = path or os.environ.get("K3D_GPU_VLLM_RECIPES_FILE") or DEFAULT_FILE
    try:
        with open(path, encoding="utf-8") as f:
            art = json.load(f)
    except FileNotFoundError:
        return None, "%s is missing" % path
    except (OSError, ValueError) as e:
        return None, "%s is unreadable (%s)" % (path, e)
    if not isinstance(art, dict) or art.get("schema") != SCHEMA:
        return None, "%s is not %s" % (path, SCHEMA)
    if not isinstance(art.get("models"), dict) or not isinstance(art.get("engines"), dict):
        return None, "%s has no models or engines" % path
    return art, None


def sha12(art):
    return (art.get("vllm_commit") or "")[:12]


def lookup(art, ids):
    """(entry, variant key, matched id, how) for the first id with a recipe, or None.

    ids are tried in order (served model id, requested id, optimizer target), each
    case-insensitively as a recipe id, then as a variant's model id."""
    models = art.get("models") or {}
    index = art.get("variant_index") or {}
    for n, mid in enumerate(ids):
        if not mid:
            continue
        key = mid.lower()
        if key in models:
            return models[key], "default", mid, "optimizer target" if n == 2 else "recipe"
        hit = index.get(key)
        if hit and hit[0] in models:
            return models[hit[0]], hit[1], mid, "variant of %s" % models[hit[0]]["id"]
    return None


def target_for(device, pci_id=None, cpu_brand=None):
    """(recipe hardware profile or None, generation or None) for a device."""
    if device == "GPU":
        return PCI_PROFILES.get((pci_id or "").lower()), "xpu"
    if device == "CPU":
        return ("xeon6" if cpu_brand and XEON6.search(cpu_brand) else None), "cpu"
    return None, None


def version(v):
    """(major, minor, patch); a pre-release ranks like its release (the site's maxVersion)."""
    out = []
    for p in str(v).split(".")[:3]:
        m = re.match(r"\d+", p)
        out.append(int(m.group()) if m else 0)
    return tuple(out + [0] * (3 - len(out)))


def engine_entry(art, engine_version):
    """(version used, its parser registries and flags, note or None)."""
    engines = art.get("engines") or {}
    if engine_version in engines:
        return engine_version, engines[engine_version], None
    newest = max(engines, key=version) if engines else None
    why = "the agent image's vLLM version is unknown (K3D_GPU_AGENT_IMAGE)" if engine_version in (None, "", "unknown") \
        else "vLLM %s is not a pinned version" % engine_version
    return newest, engines.get(newest) or {}, "%s: checked against vLLM %s, the newest pinned" % (why, newest)


# ---------------------------------------------------------------- args ---------

def units(args):
    """[(flag, value or None)] the way the site's dedupe reads an arg list: a token
    after a flag is its value unless it starts with '-'; the last occurrence wins."""
    out, i = [], 0
    args = [a for a in args or [] if isinstance(a, str) and a != ""]
    while i < len(args):
        cur = args[i]
        if cur.startswith("-"):
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                out.append((cur, args[i + 1]))
                i += 2
                continue
            out.append((cur, None))
        else:
            out.append((cur, None))
        i += 1
    last = {}
    for n, (f, _) in enumerate(out):
        last[f] = n
    return [u for n, u in enumerate(out) if last[u[0]] == n]


def bare(flag):
    return flag.lstrip("-").split("=", 1)[0]


def pair(name):
    return name[3:] if name.startswith("no-") else "no-" + name


def owned_class(name):
    for cls, names in OWNED.items():
        for n in names:
            if n == name or (n.endswith("*") and name.startswith(n[:-1])):
                return cls
    return None


def classify(name):
    """("adopt", gate) | ("owned", class) | ("unclassified", None)."""
    if name in ADOPT:
        return "adopt", ADOPT[name]
    cls = owned_class(name)
    return ("owned", cls) if cls else ("unclassified", None)


def arg_text(flag, value):
    return flag if value is None else "%s=%s" % (flag, value)


# ---------------------------------------------------------------- recipe layers -

def hwkeyed(m, profile, gen, brand=None):
    """The site's hardwareKeyedValue: exact id > generation > brand > default."""
    if not isinstance(m, dict):
        return None
    for k in (profile, gen, (brand or "").lower() or None, "default"):
        if k is not None and m.get(k) is not None:
            return m[k]
    return None


def _args(block):
    return list((block or {}).get("args") or []) if isinstance(block, dict) else []


def _env(block):
    return dict((block or {}).get("env") or {}) if isinstance(block, dict) else {}


def hardware_state(entry, profile, gen, art):
    """(state of the detected profile or None, sibling profile of its generation that the
    recipe lists verified/supported, or None)."""
    hw = entry.get("hardware") or {}
    state = hw.get(profile) if profile else None
    sibling = None
    for pid in sorted(hw):
        if hw[pid] in HW_STATES_OK and (art.get("profiles") or {}).get(pid, {}).get("generation") == gen:
            if pid == profile:
                return state, None
            sibling = sibling or pid
    return state, sibling


def _pick(block, base, key):
    """block[key] ?? base[key]: a hardware block replaces the feature's own list or map."""
    if isinstance(block, dict) and block.get(key) is not None:
        return block[key]
    return (base or {}).get(key)


def mode_ok(m, vkey, profile, gen, brand=None):
    vs = m.get("variants")
    if isinstance(vs, list) and vs and vkey not in vs:
        return False
    return hwkeyed(m.get("hardware"), profile, gen, brand) != "unsupported"


def mode_key(ft, name, v, vkey, profile, gen, brand=None, selected=None):
    """The site's resolveModeKey: the selected mode, the variant's default mode, the
    feature's default mode, else the first allowed (YAML order)."""
    modes = ft.get("modes") or {}
    allowed = [k for k in ft.get("mode_order") or sorted(modes) if k in modes and mode_ok(modes[k], vkey, profile, gen, brand)]
    for c in (selected, (v.get("default_modes") or {}).get(name), ft.get("default_mode")):
        if c and c in allowed:
            return c
    return allowed[0] if allowed else None


def feature_args(entry, name, vkey, profile, gen, brand=None, mode=None):
    """(args, env, yaml path, mode key) of one feature for this device, or None when the
    feature is absent, gated off for the hardware, or not for single_node_tp."""
    ft = (entry.get("features") or {}).get(name)
    if not isinstance(ft, dict) or not ft:
        return None
    strategies = ft.get("strategies")
    if isinstance(strategies, list) and strategies and "single_node_tp" not in strategies:
        return None
    if hwkeyed(ft.get("hardware"), profile, gen, brand) == "unsupported":
        return None
    path = "features.%s" % name
    if isinstance(ft.get("modes"), dict):
        v = (entry.get("variants") or {}).get(vkey) or (entry.get("variants") or {}).get("default") or {}
        mk = mode_key(ft, name, v, vkey, profile, gen, brand, mode)
        if not mk:
            return None
        m = ft["modes"][mk]
        ho = hwkeyed(m.get("hardware_overrides"), profile, gen, brand)
        return list(_pick(ho, m, "args") or []), dict(_pick(ho, m, "env") or {}), "%s.modes.%s" % (path, mk), mk
    ho = hwkeyed(ft.get("hardware_overrides"), profile, gen, brand)
    return list(_pick(ho, ft, "args") or []), dict(_pick(ho, ft, "env") or {}), path, None


def layers(entry, vkey, profile, gen, site=None):
    """[(layer, yaml path, args, env)] in the site's order for single_node_tp, minus the
    parallel flag and the features: "model" blocks, "hardware" blocks (an Intel profile
    or generation key), and with site (the artifact's own single_node_tp strategy, for
    render only) the strategy's args."""
    variants = entry.get("variants") or {}
    v = variants.get(vkey) or variants.get("default") or {}
    vpath = "variants.%s" % vkey
    vov = v.get("overrides") or {}
    so = entry.get("single_node_tp") or {}
    soo = so.get("overrides") or {}
    out = [("model", "model.base_args", list(entry.get("base_args") or []), dict(entry.get("base_env") or {})),
           ("model", vpath + ".extra_args", list(v.get("args") or []), dict(v.get("env") or {}))]
    if profile:
        out.append(("hardware", "%s.hardware_overrides.%s" % (vpath, profile), _args(vov.get(profile)), _env(vov.get(profile))))
    if site:
        out.append(("site", "strategies/single_node_tp.yaml", list(site.get("vllm_args") or []), dict(site.get("env") or {})))
    out.append(("model", "strategy_overrides.single_node_tp", list(so.get("args") or []) + list(so.get("vllm_args") or []),
                dict(so.get("env") or {})))
    if gen:
        if gen in soo:
            # The strategy's generation block replaces the recipe's and the variant's.
            out.append(("hardware", "strategy_overrides.single_node_tp.hardware_overrides.%s" % gen, _args(soo[gen]), _env(soo[gen])))
        else:
            rb = (entry.get("overrides") or {}).get(gen)
            out.append(("hardware", "hardware_overrides.%s" % gen, _args(rb), _env(rb)))
            out.append(("hardware", "%s.hardware_overrides.%s" % (vpath, gen), _args(vov.get(gen)), _env(vov.get(gen))))
    if profile:
        pb = soo.get(profile)
        out.append(("hardware", "strategy_overrides.single_node_tp.hardware_overrides.%s" % profile, _args(pb), _env(pb)))
    return [x for x in out if x[2] or x[3]]


def feature_names(entry):
    """The recipe's features in YAML order."""
    feats = entry.get("features") or {}
    return [f for f in entry.get("feature_order") or sorted(feats) if f in feats]


def default_features(entry, vkey, profile):
    """The features recipes.vllm.ai turns on by default for a profile."""
    opt = set(entry.get("opt_in") or []) | set((entry.get("hardware_opt_in") or {}).get(profile) or [])
    forced = set((((entry.get("variants") or {}).get(vkey) or {}).get("default_modes") or {}))
    return [f for f in feature_names(entry) if f in forced or f not in opt]


def render(art, entry, vkey, profile, features=None, frontend_env=None):
    """The recipe's own argv (no `vllm serve <id>`, no parallel flag) and env for an
    Intel profile, with no k3d-gpu policy: the site's command, for tests and review."""
    p = (art.get("profiles") or {}).get(profile) or {}
    gen, brand = p.get("generation"), p.get("brand")
    argv, env = [], {}
    for _, _, a, e in layers(entry, vkey, profile, gen, art.get("single_node_tp") or {}):
        argv += a
        env.update(e)
    for f in default_features(entry, vkey, profile) if features is None else features:
        got = feature_args(entry, f, vkey, profile, gen, brand)
        if got:
            argv += got[0]
            env.update(got[1])
    env.update(frontend_env or {})
    flat = []
    for f, val in units(argv):
        flat += [f] if val is None else [f, val]
    return flat, env


# ---------------------------------------------------------------- facts --------

def facts(art, model_id, *, requested=None, target=None, device, pci_id=None, cpu_brand=None,
          engine_version=None, engine="vllm", config=None):
    """What the recipe says about this model on this device, every flag classified and
    gated. None when no recipe matches. Pure."""
    hit = lookup(art, [model_id, requested, target])
    if not hit:
        return None
    entry, vkey, matched, how = hit
    profile, gen = target_for(device, pci_id, cpu_brand)
    brand = "intel" if gen in ("xpu", "cpu") else None
    ver, eng, ver_note = engine_entry(art, engine_version)
    ref = "vllm@%s" % sha12(art)
    res = {"id": entry["id"], "file": entry.get("file"), "ref": ref, "how": how, "matched": matched, "variant": vkey,
           "device": device, "profile": profile, "generation": gen, "engine": engine, "engine_version": ver,
           "requested_engine_version": engine_version, "state": None, "hardware_layer": False,
           "items": [], "env": [], "features": {}, "warnings": [], "notes": [], "opt_in": [],
           "tool_parser": None, "reasoning_parser": None, "spec_modes": {}, "text_only": None}
    if ver_note:
        res["notes"].append(ver_note)
    state, sibling = hardware_state(entry, profile, gen, art) if gen else (None, None)
    res["state"] = state
    hw_ok = gen is not None and state != "unsupported" and (state in HW_STATES_OK or sibling is not None)
    res["hardware_layer"] = hw_ok
    if gen and state == "unsupported":
        res["notes"].append("recipe marks %s unsupported: no %s facts applied" % (profile, gen))
    elif gen and not hw_ok:
        res["notes"].append("recipe lists no %s profile as verified or supported: no %s facts applied" % (gen, gen))
    elif gen and sibling:
        res["notes"].append("%s facts verified on %s; this card is %s" % (gen, sibling, profile or "not a profiled card"))
    if version(entry.get("min_vllm") or "0") > version(ver or "0"):
        res["warnings"].append("recipe needs vLLM >= %s; the agent image is %s: the model may not load" % (entry.get("min_vllm"), ver))
    v = (entry.get("variants") or {}).get(vkey) or {}
    if entry.get("nightly") or v.get("nightly"):
        res["warnings"].append("recipe needs a vLLM nightly")
    if v.get("min_vllm") and version(v["min_vllm"]) > version(ver or "0"):
        res["warnings"].append("recipe variant %s needs vLLM >= %s; the agent image is %s" % (vkey, v["min_vllm"], ver))

    # Features: tool calling and reasoning are applied; the rest is data.
    applied = {}
    for name in APPLIED_FEATURES:
        got = feature_args(entry, name, vkey, profile, gen, brand)
        if got:
            applied[name] = got
    for name in feature_names(entry):
        if name in APPLIED_FEATURES:
            continue
        ft = entry["features"][name]
        if name == "spec_decoding":
            if isinstance(ft.get("modes"), dict):
                for mk in ft.get("mode_order") or sorted(ft["modes"]):
                    m = ft["modes"].get(mk) or {}
                    if mode_ok(m, vkey, profile, gen, brand):
                        ho = hwkeyed(m.get("hardware_overrides"), profile, gen, brand)
                        res["spec_modes"][mk] = {"config": spec_config(_pick(ho, m, "args") or []),
                                                 "path": "features.spec_decoding.modes.%s" % mk}
            else:
                got = feature_args(entry, name, vkey, profile, gen, brand)
                if got:
                    cfg = spec_config(got[0])
                    if cfg:
                        res["spec_modes"][cfg.get("method") or "default"] = {"config": cfg, "path": got[2]}
        elif name == "text_only":
            got = feature_args(entry, name, vkey, profile, gen, brand)
            if got:
                res["text_only"] = {"path": got[2], "args": got[0]}
        if name in (entry.get("opt_in") or []) and name != "long_context":
            res["opt_in"].append(name)

    def item(layer, path, flag, value, feature=None):
        name = bare(flag)
        kind, gate = classify(name)
        it = {"flag": flag, "value": value, "name": name, "layer": layer, "path": path, "feature": feature,
              "class": gate if kind != "unclassified" else None, "kind": kind, "action": "report", "why": None}
        res["items"].append(it)
        return it

    for layer, path, args, env in layers(entry, vkey, profile, gen):
        if layer == "hardware" and not hw_ok:
            continue
        for k in sorted(env):
            res["env"].append({"name": k, "value": env[k], "path": path})
        for flag, value in units(args):
            item(layer, path, flag, value)
    if entry.get("default_frontend"):
        res["env"].append({"name": "frontend", "value": entry["default_frontend"], "path": "model.default_frontend"})

    for name, (args, env, path, _) in applied.items():
        feats = units(args)
        for k in sorted(env):
            res["env"].append({"name": k, "value": env[k], "path": path})
        items = [item("model", path, f, val, name) for f, val in feats]
        drop = None
        for it in items:
            if it["kind"] == "owned" and it["class"] == "plugin":
                drop = "%s needs %s outside the image (k3d-gpu never fetches at boot)" % (it["flag"], it["value"] or "a file")
                break
        for it in items if drop is None else []:
            if it["name"] in ("tool-call-parser", "reasoning-parser"):
                kind = "tool" if it["name"] == "tool-call-parser" else "reasoning"
                reg = eng.get("tool_parsers" if kind == "tool" else "reasoning_parsers") or []
                if it["value"] not in reg:
                    drop = "recipe names %s parser %s, absent from vLLM %s (the agent image)" % (kind, it["value"], ver)
        res["features"][name] = {"path": path, "applied": drop is None, "why": drop}
        if drop:
            res["warnings"].append("%s dropped: %s" % (path, drop))
            for it in items:
                it["action"], it["why"] = "dropped", drop
            continue
        for it in items:
            if it["name"] == "tool-call-parser":
                res["tool_parser"] = it["value"]
            elif it["name"] == "reasoning-parser":
                res["reasoning_parser"] = it["value"]

    # Gates.
    auto_map = isinstance(config, dict) and bool(config.get("auto_map"))
    for it in res["items"]:
        if it["action"] == "dropped":
            continue
        if it["kind"] == "owned":
            it["why"] = OWNER[it["class"]]
            continue
        if it["kind"] == "unclassified":
            it["why"] = "not in k3d-gpu's adopt list: reported, never applied"
            continue
        val = it["value"] or ""
        if "'" in val or "\n" in val or "'" in it["flag"]:
            it["action"], it["why"] = "refused", "has a quote or newline"
            continue
        gate = it["class"]
        if gate.startswith("feature:") or gate.startswith("parser:"):
            feat = gate.split(":", 1)[1]
            feat = {"tool": "tool_calling", "reasoning": "reasoning"}.get(feat, feat)
            if it["feature"] is None and not (res["features"].get(feat) or {}).get("applied"):
                it["why"] = "applies with features.%s only" % feat
                continue
            it["action"] = "adopt"
            if gate.startswith("parser:"):
                it["why"] = "in vLLM %s's registry" % ver
        elif gate == "path:examples":
            m = re.match(r"^examples/(.+)$", val)
            d = EXAMPLES_DIR.get(engine)
            if m and d:
                it["action"], it["value"], it["why"] = "adopt", "%s/%s" % (d, m.group(1)), "examples/ located in %s" % engine
            else:
                it["why"] = "tool calls use the model's own chat template; recipe's %s is not located in %s" % (val, engine)
        elif gate == "security":
            if auto_map:
                it["action"], it["why"] = "adopt", "config.json carries auto_map"
            else:
                it["why"] = ("config.json carries no auto_map: vLLM's built-in architecture loads it, not applied"
                             if isinstance(config, dict) else "no config.json available: not applied")
        elif gate == "device":
            if device == "HPU" or (it["layer"] == "model" and device != "GPU"):
                it["why"] = "a device flag, applied on an Intel GPU only"
            else:
                it["action"] = "adopt"
        else:
            it["action"] = "adopt"
    return res


def spec_config(args):
    for f, val in units(args):
        if bare(f) == "speculative-config" and val:
            try:
                cfg = json.loads(val)
            except ValueError:
                return None
            return cfg if isinstance(cfg, dict) else None
    return None


# ---------------------------------------------------------------- measured -----

def class_words(hw_class):
    kind, _, name = (hw_class or "").partition(":")
    if kind == "hpu":
        return "gaudi"
    return name.lower()


def measured(rows, hw_class, model_keys, table=None):
    """{flag: (value or None, row, verdict, class token)}: MEASURED entries a SUPPORTED or
    REFUTED ledger row decides for this hardware class and model."""
    out, words = {}, class_words(hw_class)
    keys = {k.lower() for k in model_keys if k}
    for m in MEASURED if table is None else table:
        match = m.get("match") or {}
        tok = match.get("class_token") or ""
        if (match.get("model") or "").lower() not in keys or not tok or tok.lower() not in words:
            continue
        fam = re.compile(r"^%s[a-z]*$" % re.escape(m["family"]))
        hits = sorted((rid, v) for rid, (v, text) in (rows or {}).items()
                      if fam.match(rid) and tok.lower() in (text or "").lower())
        for want in ("REFUTED", "SUPPORTED"):
            hit = next((h for h in hits if h[1] == want), None)
            if hit:
                out[m["flag"]] = (m.get("value") if want == "SUPPORTED" else None, hit[0], want, tok)
                break
    return out


# ---------------------------------------------------------------- merge --------

# The base source the launcher gives the optimizer's own arguments (scripts/k3d-gpu
# OPT_ARGS_SOURCE): listed after the parsers, as vllm_manifest renders them.
OPT_ARGS_SOURCE = "model optimizer (engine args)"


def parse_base(text):
    """[(arg, source)] from 'arg<TAB>source' lines."""
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        arg, _, src = line.partition("\t")
        out.append((arg, src or "k3d-gpu default"))
    return out


def merge(art, base, *, parsers, facts_result, ledger_rows=None, hw_class=None, table=None):
    """The server arguments with each value's source.

    base     [(arg, source)] the launcher built (model, revision, device args, tp, ...)
    parsers  {"tool": (value, source), "reasoning": (value, source)}; source "env"
             means the user set it (an empty value turns the parser off)
    Returns {"args": [(arg, source)], "add": [arg], "tool_parser", "reasoning_parser",
             "parser_sources", "ref", "summary", "warnings", "explain"}."""
    r = facts_result
    ref = r["ref"] if r else None
    base_names = {}
    for arg, src in base:
        if arg.startswith("-"):
            base_names[bare(arg)] = (arg, src)
    rows = measured(ledger_rows, hw_class, [r["id"]] if r else [], table) if r else {}
    add, out, explain_extra = [], list(base), []
    agree = {}
    if r:
        for it in r["items"]:
            src = "recipe %s %s" % (ref, it["path"])
            name = it["name"]
            if it["action"] != "adopt" or name in ("enable-auto-tool-choice", "tool-call-parser", "reasoning-parser"):
                if it["action"] != "adopt":
                    explain_extra.append("not applied: %s (%s) -> %s [recipe %s]" % (
                        arg_text(it["flag"], it["value"]), it["path"], it["why"], ref))
                continue
            if name in rows:
                val, rid, verdict, tok = rows[name]
                lsrc = "ledger EXPERIMENTS.md %s (%s, %s)" % (rid, verdict, tok)
                if verdict == "REFUTED":
                    explain_extra.append("not applied: %s (%s) -> k3d-gpu default kept [%s]" % (
                        arg_text(it["flag"], it["value"]), it["path"], lsrc))
                    continue
                it = dict(it, value=val)
                src = lsrc
            have = base_names.get(name) or base_names.get(pair(name))
            text = arg_text("--" + name, it["value"])
            if have:
                if have[0] == text:
                    agree[have[0]] = "recipe %s agrees (%s %s)" % (r["generation"] if it["layer"] == "hardware" else "model", ref, it["path"])
                else:
                    explain_extra.append("not applied: %s (%s) -> the device base sets %s [%s]" % (text, it["path"], have[0], have[1]))
                continue
            if any(a == text for a in add):
                continue
            add.append(text)
            base_names[name] = (text, src)
            out.append((text, src))
    # Parsers: env > ledger > recipe > k3d-gpu default.
    chosen, sources = {}, {}
    for kind, flag in (("tool", "tool-call-parser"), ("reasoning", "reasoning-parser")):
        val, src = parsers.get(kind) or ("", "")
        rv = (r or {}).get("%s_parser" % kind)
        feat = "tool_calling" if kind == "tool" else "reasoning"
        if src == "env":
            chosen[kind], sources[kind] = val, "user env K3D_GPU_AGENT_%s_PARSER" % kind.upper()
        elif flag in rows:
            # SUPPORTED sets the row's parser; REFUTED keeps k3d-gpu's default.
            verdict = rows[flag][2]
            chosen[kind] = (rows[flag][0] or "") if verdict == "SUPPORTED" else val
            sources[kind] = "ledger EXPERIMENTS.md %s (%s, %s)" % (rows[flag][1], verdict, rows[flag][3])
        elif rv:
            chosen[kind] = rv
            sources[kind] = "recipe %s features.%s" % (ref, feat) + (
                "; in vLLM %s's registry" % r["engine_version"] if kind == "tool" else "")
            if val and val == rv and src:
                sources[kind] += "; %s agrees" % src
        else:
            chosen[kind], sources[kind] = val, src or "k3d-gpu default"
    final = [x for x in out if x[1] != OPT_ARGS_SOURCE]
    if chosen["tool"]:
        final.append(("--enable-auto-tool-choice", sources["tool"].split("; in vLLM")[0]))
        final.append(("--tool-call-parser=%s" % chosen["tool"], sources["tool"]))
    if chosen["reasoning"]:
        final.append(("--reasoning-parser=%s" % chosen["reasoning"], sources["reasoning"]))
    final += [x for x in out if x[1] == OPT_ARGS_SOURCE]
    final = [(a, ("%s; %s" % (s, agree[a])) if a in agree else s) for a, s in final]
    warnings = list((r or {}).get("warnings") or [])
    summary = ""
    if r:
        summary = "vLLM recipe %s (%s %s): %s" % (r["id"], ref, r["file"], ", ".join(
            x for x in (("tool parser %s" % chosen["tool"]) if chosen["tool"] else "",
                        ("reasoning parser %s" % chosen["reasoning"]) if chosen["reasoning"] else "",
                        ("adds %s" % " ".join(add)) if add else "") if x) or "nothing to add")
    return {"args": final, "add": add, "tool_parser": chosen["tool"], "reasoning_parser": chosen["reasoning"],
            "parser_sources": sources, "ref": ("%s@vllm-%s" % (r["id"], sha12(art))) if r else "",
            "summary": summary, "warnings": warnings, "explain": explain_extra}


def explain_lines(r, m, header, no_recipe=None):
    """'vLLM server args' block: every argument with its source, then what was not applied."""
    out = [header]
    if no_recipe:
        out.append("  recipe: not consulted: %s" % no_recipe)
    elif r:
        st = r["state"] or "not listed"
        prof = r["profile"] or r["generation"] or "model layer only"
        out.append("  recipe: %s (%s %s; %s %s%s)" % (r["id"], r["ref"], r["file"], prof,
                                                     st if r["profile"] else "", "; " + r["how"] if r["how"] != "recipe" else ""))
    else:
        out.append("  recipe: none for this model")
    shown = []
    for a, s in m["args"]:
        # A flag's value given as its own argument (the optimizer's) prints on the flag's line.
        if shown and not a.startswith("-") and shown[-1][0].startswith("-") and " " not in shown[-1][0] and shown[-1][1] == s:
            shown[-1] = ("%s %s" % (shown[-1][0], a), s)
        else:
            shown.append((a, s))
    width = min(max([len(a) for a, _ in shown] + [30]), 34)
    for a, s in shown:
        out.append("  %s [%s]" % (a.ljust(width), s))
    out += ["  " + x for x in m["explain"]]
    if r:
        for e in r["env"]:
            if e["name"] == "frontend":
                out.append("  not applied: frontend %s (%s) -> recipe env is reported, never applied" % (e["value"], e["path"]))
            else:
                out.append("  not applied: %s=%s (%s) -> recipe env is reported, never applied" % (e["name"], e["value"], e["path"]))
        left = []
        for f in r["opt_in"]:
            if f == "spec_decoding" and r["spec_modes"]:
                left.append("spec_decoding (draft tokens: %s)" % ", ".join(
                    "%s %s" % (k, (v["config"] or {}).get("num_speculative_tokens", "?")) for k, v in r["spec_modes"].items()))
            else:
                left.append(f)
        if left:
            out.append("  recipe opt-in, left to the optimizer: %s" % ", ".join(left))
        out += ["  note: " + n for n in r["notes"]]
        out += ["  warning: " + w for w in r["warnings"]]
    return out


# ---------------------------------------------------------------- CLI ----------

def shell_quote(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def header(a, r):
    """vLLM server args: <model> on <engine> (GPU, Battlemage 0xe223 = arc_pro_b70, vLLM 0.33.0)."""
    profile = target_for(a.device, a.pci_id)[0]
    dev = a.device
    if a.pci_id:
        dev += ", %s%s%s" % ((a.platform + " ") if a.platform else "", a.pci_id, (" = " + profile) if profile else "")
    ver = (r or {}).get("engine_version") or a.engine_version
    return "vLLM server args: %s (%s, vLLM %s)" % (a.title or a.model, dev, ver)


def plan_lines(a):
    """KEY=value lines for plan --explain's vLLM block: the device the vLLM agent would
    serve on (PV_DEVICE empty: the agent's engine is not vLLM), its PCI id, platform and
    hardware class, and the model optimizer's vLLM choices when it made them."""
    with open(a.facts, encoding="utf-8") as f:
        facts_doc = json.load(f)
    plan_doc = {}
    if a.plan and os.path.exists(a.plan):
        try:
            with open(a.plan, encoding="utf-8") as f:
                plan_doc = json.load(f)
        except ValueError:
            plan_doc = {}
    opt = plan_doc.get("optimizer") or {}
    usable = [t for t in facts_doc.get("targets") or [] if t.get("usable")]
    hpu = next((t for t in usable if t.get("kind") == "hpu"), None)
    gpu = next((t for t in usable if t.get("kind") == "gpu" and t.get("vendor") == "intel"), None)
    if opt.get("engine") == "vllm":
        dev = opt.get("device")
    elif a.backend in ("vllm", "llm-scaler"):
        dev = "HPU" if hpu else "GPU" if gpu else ""
    elif not a.backend and hpu and not opt:
        dev = "HPU"  # agent auto serves a Gaudi with vLLM
    else:
        dev = ""
    t = hpu if dev == "HPU" else gpu if dev == "GPU" else None
    kv = {"PV_DEVICE": dev if t else "", "PV_PCI": (t or {}).get("device_id") or "" if dev == "GPU" else "",
          "PV_PLATFORM": (t or {}).get("platform") or "" if dev == "GPU" else "",
          "PV_HW_CLASS": ("hpu" if dev == "HPU" else "gpu:%s" % ((t or {}).get("platform") or (t or {}).get("device_id"))) if t else "",
          "PV_OPT": "", "PV_MODEL": "", "PV_REVISION": "", "PV_REQUESTED": "", "PV_TARGET": "",
          "PV_TOOL": "", "PV_REASONING": "", "PV_ARGS_JSON": "[]"}
    if t and opt.get("engine") == "vllm":
        sys.path.insert(0, HERE)
        import model_opt  # noqa: E402  (lazy: model_opt imports this module)
        conv = bool(opt.get("conversion"))
        kv.update({"PV_OPT": "1", "PV_MODEL": (opt.get("fallback_model") if conv else opt.get("model")) or "",
                   "PV_REVISION": (opt.get("fallback_revision") if conv else opt.get("revision")) or "",
                   "PV_REQUESTED": opt.get("requested") or "", "PV_TARGET": opt.get("target") or "",
                   "PV_TOOL": opt.get("tool_parser") or "", "PV_REASONING": opt.get("reasoning_parser") or "",
                   "PV_ARGS_JSON": json.dumps(model_opt.engine_args(opt))})
    return ["%s=%s" % (k, shell_quote(v)) for k, v in kv.items()]


def _facts_from_args(a, art):
    cfg = None
    if a.config:
        try:
            with open(a.config, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = None
    return facts(art, a.model, requested=a.requested, target=a.target, device=a.device, pci_id=a.pci_id,
                 cpu_brand=a.cpu_brand, engine_version=a.engine_version, engine=a.engine, config=cfg)


def _ledger(path):
    if not path or not os.path.exists(path):
        return {}
    sys.path.insert(0, HERE)
    import model_opt  # noqa: E402  (lazy: model_opt imports this module)
    return model_opt.read_ledger(path)


def main(argv):
    ap = argparse.ArgumentParser(prog="recipes.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("facts", "merge"):
        p = sub.add_parser(name)
        p.add_argument("--model", required=True)
        p.add_argument("--requested")
        p.add_argument("--target")
        p.add_argument("--device", required=True, choices=("GPU", "HPU", "CPU"))
        p.add_argument("--pci-id")
        p.add_argument("--cpu-brand")
        p.add_argument("--engine", default="vllm")
        p.add_argument("--engine-version")
        p.add_argument("--config")
        p.add_argument("--file")
        if name == "facts":
            p.add_argument("--format", choices=("json", "explain"), default="json")
        else:
            p.add_argument("--format", choices=("shell", "json", "explain"), default="shell")
            p.add_argument("--base-file", required=True)
            p.add_argument("--hw-class")
            p.add_argument("--ledger")
            p.add_argument("--title")
            p.add_argument("--platform")
            p.add_argument("--no-recipe")
            for k in ("tool", "reasoning"):
                p.add_argument("--%s-parser" % k, default="")
                p.add_argument("--%s-parser-from" % k, default="")
    c = sub.add_parser("check")
    c.add_argument("--file")
    pl = sub.add_parser("plan")
    pl.add_argument("--facts", required=True)
    pl.add_argument("--plan")
    pl.add_argument("--backend", default="")
    a = ap.parse_args(argv[1:])
    if a.cmd == "plan":
        print("\n".join(plan_lines(a)))
        return 0
    art, why = load(a.file)
    if a.cmd == "check":
        if not art:
            sys.stderr.write("recipes.py: %s\n" % why)
            return 1
        print("vllm_commit=%s recipes_commit=%s models=%d engines=%s" % (
            art.get("vllm_commit"), art.get("recipes_commit"), len(art["models"]), ",".join(sorted(art["engines"], key=version))))
        return 0
    if not art and not getattr(a, "no_recipe", None):
        sys.stderr.write("recipes.py: %s\n" % why)
        return 1
    r = None if getattr(a, "no_recipe", None) else _facts_from_args(a, art)
    if a.cmd == "facts":
        if a.format == "json":
            print(json.dumps(r, indent=1, sort_keys=True))
        else:
            print("\n".join(explain_lines(r, merge(art, [], parsers={}, facts_result=r), "recipe facts: %s" % a.model)))
        return 0
    if a.base_file == "-":
        base = parse_base(sys.stdin.read())
    else:
        with open(a.base_file, encoding="utf-8") as f:
            base = parse_base(f.read())
    m = merge(art or {}, base, parsers={"tool": (a.tool_parser, a.tool_parser_from), "reasoning": (a.reasoning_parser, a.reasoning_parser_from)},
              facts_result=r, ledger_rows=_ledger(a.ledger), hw_class=a.hw_class)
    if a.format == "json":
        print(json.dumps({"facts": r, "merge": m}, indent=1, sort_keys=True))
    elif a.format == "explain":
        print("\n".join(explain_lines(r, m, header(a, r), a.no_recipe)))
    else:
        kv = {"RECIPE_ARGS_JSON": json.dumps(m["add"]), "RECIPE_TOOL_PARSER": m["tool_parser"] or "",
              "RECIPE_REASONING_PARSER": m["reasoning_parser"] or "", "RECIPE_REF": m["ref"],
              "RECIPE_SUMMARY": m["summary"], "RECIPE_WARNINGS": "; ".join(m["warnings"])}
        print("\n".join("%s=%s" % (k, shell_quote(v)) for k, v in kv.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
