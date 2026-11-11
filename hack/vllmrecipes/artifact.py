"""console/vllm-recipes.json: the Intel-relevant facts of vLLM's model recipes, offline.

generate() reads three local trees and returns the artifact; nothing here touches
the network.

  recipes_dir  vllm-project/vllm `recipes/` at the commit sources.lock.json names:
               manifest.yaml and models/<org>/<repo>.yaml
  site_dir     vllm-project/recipes at the pinned commit: taxonomy.yaml,
               sources.lock.json, LICENSE, strategies/single_node_tp.yaml
  vllm_src     one directory per pinned vLLM version, v<version>/, holding that
               tag's vllm/tool_parsers/__init__.py, vllm/reasoning/__init__.py,
               vllm/engine/arg_utils.py and vllm/entrypoints/**/cli_args.py
               (a flags.txt there, one flag per line, stands in for the last two)

What the artifact keeps, and why the rest is dropped:
  - per model: base args and env, parsers and every other feature, opt-ins,
    variants, the single_node_tp overrides and the recipe's own Intel hardware
    states. Hardware-keyed maps keep only the keys an Intel lookup can hit (the
    Intel profile ids, their generations cpu and xpu, the brand intel, default),
    so an NVIDIA-only edit upstream leaves the artifact as it was.
  - never: docker images, serve commands, install notes (k3d-gpu pins its own
    images), guides and descriptions, presets (an Intel one is listed in
    `unported`), tp maps, pd, kv store and kv offload.
  - per pinned vLLM version: the tool and reasoning parser registries and the
    serve flags, so a consumer can gate a recipe's parser on the image's vLLM.
  - the Apache-2.0 text of the recipes, with the two source trees it came from.
The bytes are json.dumps(indent=1, sort_keys=True) plus a newline: the same
inputs always give the same file.
"""

import glob
import hashlib
import json
import os
import re
import sys

from . import jsyaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "canary"))
import surface  # noqa: E402  (canary/surface.py: the static vLLM flag and parser parse)

SCHEMA = "k3d-gpu.vllm-recipes/v1"
SUPPORTED_SCHEMA_VERSION = 1  # the recipes renderer's (scripts/assemble-content.mjs)
COMMIT_KEYS = ("recipes_commit", "vllm_commit", "vllm_tree")
RECIPES_REPO = "vllm-project/recipes"
VLLM_REPO = "vllm-project/vllm"
LICENSE_HEAD = re.compile(r"^\s*Apache License\s+Version 2\.0, January 2004\b")
REGISTRIES = {"tool_parsers": ("vllm/tool_parsers/__init__.py", "_TOOL_PARSERS_TO_REGISTER"),
              "reasoning_parsers": ("vllm/reasoning/__init__.py", "_REASONING_PARSERS_TO_REGISTER")}


class GenError(Exception):
    pass


# ---- inputs -------------------------------------------------------------------------

def git_tree_sha(path):
    """The git tree id of a directory of regular files (what `git rev-parse <commit>:<path>`
    prints for the tree it was checked out from)."""
    entries = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.islink(full):
            raise GenError("%s is a symlink: the recipes tree holds regular files only" % full)
        if os.path.isdir(full):
            entries.append((name + "/", b"40000 " + name.encode() + b"\0" + bytes.fromhex(git_tree_sha(full))))
        else:
            with open(full, "rb") as f:
                data = f.read()
            mode = b"100755" if os.access(full, os.X_OK) else b"100644"
            blob = hashlib.sha1(b"blob %d\0" % len(data) + data).digest()
            entries.append((name, mode + b" " + name.encode() + b"\0" + blob))
    body = b"".join(e for _, e in sorted(entries))
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()


def read_lock(site_dir):
    """(vllm commit, recipes tree id) from the recipes repo's sources.lock.json."""
    try:
        with open(os.path.join(site_dir, "sources.lock.json"), encoding="utf-8") as f:
            v = (json.load(f).get("latest") or {}).get("vllm") or {}
    except (OSError, ValueError) as e:
        raise GenError("sources.lock.json: %s" % e) from None
    if v.get("repo") != VLLM_REPO or v.get("path") != "recipes":
        raise GenError("sources.lock.json latest.vllm is %s:%s, not %s:recipes"
                       % (v.get("repo"), v.get("path"), VLLM_REPO))
    for k in ("sha", "tree_sha"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(v.get(k, ""))):
            raise GenError("sources.lock.json latest.vllm.%s is not a commit id: %r" % (k, v.get(k)))
    return v["sha"], v["tree_sha"]


def engine(vdir):
    """{tool_parsers, reasoning_parsers, flags} of one vLLM tag's sources."""
    out = {}
    for key, (rel, var) in REGISTRIES.items():
        names = surface.parser_names(surface.parse(os.path.join(vdir, rel)), var)
        if not names:
            raise GenError("%s registers no parsers (%s)" % (os.path.join(vdir, rel), var))
        out[key] = sorted(names)
    frozen = os.path.join(vdir, "flags.txt")
    if os.path.exists(frozen):
        with open(frozen, encoding="utf-8") as f:
            flags = {ln.strip() for ln in f if ln.strip()}
    else:
        flags = surface.add_argument_flags(surface.parse(os.path.join(vdir, "vllm", "engine", "arg_utils.py")))
        clis = sorted(glob.glob(os.path.join(vdir, "vllm", "entrypoints", "**", "cli_args.py"), recursive=True))
        if not clis:
            raise GenError("%s has no vllm/entrypoints/**/cli_args.py" % vdir)
        for p in clis:
            t = surface.parse(p)
            flags |= surface.add_argument_flags(t) | surface.args_class_fields(t)
    if not flags:
        raise GenError("%s: no serve flags" % vdir)
    out["flags"] = sorted({surface.norm_flag(f, "vllm") for f in flags})
    return out


# ---- typed reads (a field the artifact keeps, of the wrong type, is an error) ---------

def _err(where, what, v):
    raise GenError("%s: %s, not %s" % (where, what, type(v).__name__))


def _str(v, where, optional=True):
    if v is None and optional:
        return None
    if not isinstance(v, str):
        _err(where, "a string", v)
    return v


def _args(v, where):
    """An arg list as the renderer emits it: falsy items dropped (filter(Boolean)) and the
    rest String()ed."""
    if v is None:
        return []
    if not isinstance(v, list):
        _err(where, "a list", v)
    out = []
    for i, x in enumerate(v):
        if isinstance(x, (list, dict)):
            _err("%s[%d]" % (where, i), "a scalar", x)
        if x is None or x is False or x == "" or (isinstance(x, (int, float)) and (x == 0 or x != x)):
            continue
        out.append(x if isinstance(x, str) else jsyaml.js_str(x))
    return out


def _env(*maps, where):
    """Env maps merged in order (Object.assign), values String()ed."""
    out = {}
    for m in maps:
        if m is None:
            continue
        if not isinstance(m, dict):
            _err(where, "a map", m)
        for k, x in m.items():
            if isinstance(x, (list, dict)):
                _err("%s.%s" % (where, k), "a scalar", x)
            out[k] = x if isinstance(x, str) else jsyaml.js_str(x)
    return out


def _strs(v, where):
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        _err(where, "a list of strings", v)
    return list(v)


def _map(v, where):
    if v is None:
        return {}
    if not isinstance(v, dict):
        _err(where, "a map", v)
    return v


def _states(v, keys, where):
    """A hardware-keyed map of states (supported, unsupported, verified, ...), Intel keys only."""
    out = {}
    for k, x in _map(v, where).items():
        if k in keys:
            out[k] = _str(x, "%s.%s" % (where, k), optional=False)
    return out


def _block(v, where, args_key, env_key):
    """One hardware_overrides entry as {args, env}; a key the entry does not have stays
    absent (a feature's `args ?? feature.args` falls back only then)."""
    v = _map(v, where)
    out = {}
    if args_key in v:
        out["args"] = _args(v[args_key], "%s.%s" % (where, args_key))
    if env_key in v:
        out["env"] = _env(v[env_key], where="%s.%s" % (where, env_key))
    return out


def _overrides(v, keys, where, args_key="extra_args", env_key="extra_env"):
    return {k: _block(x, "%s.%s" % (where, k), args_key, env_key) for k, x in _map(v, where).items() if k in keys}


# ---- one recipe ----------------------------------------------------------------------

def _part(f, keys, where, lists):
    """A feature or a feature mode: args and env as written (an absent key stays absent),
    the string lists named, and the hardware gate and overrides when an Intel key is in
    them (an absent map and an empty one read the same)."""
    f = _map(f, where)
    out = {}
    if "args" in f:
        out["args"] = _args(f["args"], where + ".args")
    if "env" in f:
        out["env"] = _env(f["env"], where=where + ".env")
    for k in lists:
        if k in f:
            out[k] = _strs(f[k], "%s.%s" % (where, k))
    hw = _states(f.get("hardware"), keys, where + ".hardware")
    if hw:
        out["hardware"] = hw
    ho = _overrides(f.get("hardware_overrides"), keys, where + ".hardware_overrides", "args", "env")
    if ho:
        out["hardware_overrides"] = ho
    return out


def _feature(f, keys, where):
    out = _part(f, keys, where, ("strategies",))
    if "default_mode" in f:
        out["default_mode"] = _str(f["default_mode"], where + ".default_mode")
    if "modes" in f:
        modes = _map(f["modes"], where + ".modes")
        out["mode_order"] = list(modes)
        out["modes"] = {mk: _part(m, keys, "%s.modes.%s" % (where, mk), ("variants",)) for mk, m in modes.items()}
    return out


def _variant(v, ids, gens, where):
    v = _map(v, where)
    sup = _strs(v.get("supported_hardware"), where + ".supported_hardware")
    ph = v.get("precision_hardware")
    if ph is not None and not isinstance(ph, dict):
        _err(where + ".precision_hardware", "a map", ph)
    vram = v.get("vram_minimum_gb")
    if vram is not None and (isinstance(vram, bool) or not isinstance(vram, (int, float))):
        _err(where + ".vram_minimum_gb", "a number", vram)
    dm = _map(v.get("default_modes"), where + ".default_modes")
    out = {
        "precision": _str(v.get("precision"), where + ".precision"),
        "vram_min": vram,
        "args": _args(v.get("extra_args"), where + ".extra_args"),
        "env": _env(v.get("extra_env"), where=where + ".extra_env"),
        # exact id (CS:1751) and generation (CS:2006) blocks
        "overrides": _overrides(v.get("hardware_overrides"), ids | gens, where + ".hardware_overrides"),
        "min_vllm": _str(v.get("min_vllm_version"), where + ".min_vllm_version"),
        "nightly": v.get("nightly_required") is True,
        "default_modes": {k: _str(x, "%s.default_modes.%s" % (where, k), optional=False) for k, x in dm.items()},
        "supported": [x for x in sup if x in ids],
        "listed": bool(sup),
        "precision_hardware": ph,
    }
    if "model_id" in v:
        out["model_id"] = _str(v["model_id"], where + ".model_id", optional=False)
    return out


def recipe_entry(r, rel, ids, gens, unported):
    """The artifact entry of one parsed recipe file (rel: recipes/models/<org>/<repo>.yaml).
    ids: the Intel profile ids; gens: their generations. A hardware-keyed lookup
    (CS:70-78) reads id, generation, brand, default; the recipe, variant and strategy
    hardware_overrides blocks are read by exact id and generation only."""
    keys = ids | gens | {"intel", "default"}
    hf_id = re.sub(r"\.ya?ml$", "", "/".join(rel.split("/")[-2:]))
    where = rel
    if not isinstance(r, dict):
        _err(where, "a map", r)
    model = _map(r.get("model"), where + ".model")
    if not model.get("model_id"):
        raise GenError("%s: no model.model_id" % where)
    meta = _map(r.get("meta"), where + ".meta")
    ctx = model.get("context_length")
    if ctx is not None and (isinstance(ctx, bool) or not isinstance(ctx, int)):
        _err(where + ".model.context_length", "an integer", ctx)
    feats = _map(r.get("features"), where + ".features")
    so = _map(r.get("strategy_overrides"), where + ".strategy_overrides")
    entry = {
        "id": hf_id,
        "model_id": _str(model["model_id"], where + ".model.model_id", optional=False),
        "file": rel,
        "min_vllm": _str(model.get("min_vllm_version"), where + ".model.min_vllm_version"),
        "nightly": model.get("nightly_required") is True,
        "context_length": ctx,
        "default_frontend": _str(model.get("default_frontend"), where + ".model.default_frontend"),
        "base_args": _args(model.get("base_args"), where + ".model.base_args"),
        "base_env": _env(model.get("base_env"), where=where + ".model.base_env"),
        "hardware": _states(meta.get("hardware"), ids, where + ".meta.hardware"),
        "feature_order": list(feats),
        "features": {k: _feature(f, keys, "%s.features.%s" % (where, k)) for k, f in feats.items()},
        "opt_in": _strs(r.get("opt_in_features"), where + ".opt_in_features"),
        "hardware_opt_in": {k: _strs(x, "%s.hardware_opt_in_features.%s" % (where, k))
                            for k, x in _map(r.get("hardware_opt_in_features"),
                                             where + ".hardware_opt_in_features").items()
                            if k in ids},
        # recipe-level hardware_overrides are read by generation only (CS:1997-2009)
        "overrides": _overrides(r.get("hardware_overrides"), gens, where + ".hardware_overrides"),
        "variants": {k: _variant(v, ids, gens, "%s.variants.%s" % (where, k))
                     for k, v in _map(r.get("variants"), where + ".variants").items()},
        "strategies": _strs(r.get("compatible_strategies"), where + ".compatible_strategies"),
    }
    if "single_node_tp" in so:
        s = _map(so["single_node_tp"], where + ".strategy_overrides.single_node_tp")
        sw = where + ".strategy_overrides.single_node_tp"
        entry["single_node_tp"] = {
            "args": _args(s.get("extra_args"), sw + ".extra_args"),
            "vllm_args": _args(s.get("vllm_args"), sw + ".vllm_args"),
            "env": _env(s.get("env"), s.get("extra_env"), where=sw + ".env"),
            "overrides": _overrides(s.get("hardware_overrides"), ids | gens, sw + ".hardware_overrides"),
        }
    sh = _map(_map(r.get("strategy_hardware"), where + ".strategy_hardware").get("single_node_tp"),
              where + ".strategy_hardware.single_node_tp")
    sh = _states(sh, keys, where + ".strategy_hardware.single_node_tp")
    if sh:
        entry["single_node_tp_hardware"] = sh
    for pk, p in _map(r.get("presets"), where + ".presets").items():
        hw = _map(p, "%s.presets.%s" % (where, pk)).get("hardware")
        if hw in ids:
            unported.append({"model": hf_id, "scope": "presets.%s" % pk, "hardware": hw,
                             "why": "a preset on Intel hardware: the resolver applies no presets"})
    return entry


# ---- the artifact ---------------------------------------------------------------------

def intel_profiles(taxonomy):
    profiles = _map(_map(taxonomy, "taxonomy.yaml").get("hardware_profiles"), "taxonomy.yaml hardware_profiles")
    out = {}
    for pid, p in profiles.items():
        if not isinstance(p, dict) or p.get("brand") != "Intel":
            continue
        gen = p.get("generation")
        gen = gen[0] if isinstance(gen, list) else gen
        out[pid] = {"brand": "Intel", "generation": _str(gen, "taxonomy.yaml %s.generation" % pid, optional=False),
                    "gpu_count": p.get("gpu_count"), "vram_gb": p.get("vram_gb"),
                    "restricted": p.get("restricted") is True}
    if not out:
        raise GenError("taxonomy.yaml has no Intel hardware profile")
    return out


def generate(recipes_dir, site_dir, vllm_src, versions, recipes_commit):
    """The artifact (a dict) from local trees; GenError when an input breaks a rule."""
    if not re.fullmatch(r"[0-9a-f]{40}", recipes_commit or ""):
        raise GenError("recipes commit %r is not a commit id" % recipes_commit)
    vllm_commit, tree = read_lock(site_dir)
    got = git_tree_sha(recipes_dir)
    if got != tree:
        raise GenError("%s is tree %s; sources.lock.json locks %s" % (recipes_dir, got, tree))
    with open(os.path.join(site_dir, "LICENSE"), encoding="utf-8") as f:
        license_text = f.read()
    if not LICENSE_HEAD.match(license_text):
        raise GenError("%s/LICENSE is not the Apache License 2.0" % site_dir)
    manifest = os.path.join(recipes_dir, "manifest.yaml")
    sv = (jsyaml.load(manifest) if os.path.exists(manifest) else None) or {}
    sv = sv.get("schema_version") if isinstance(sv, dict) else None
    sv = 1 if sv is None else sv  # schema_version ?? 1 (assemble-content.mjs)
    if not isinstance(sv, int) or isinstance(sv, bool) or sv > SUPPORTED_SCHEMA_VERSION:
        raise GenError("manifest.yaml schema_version %r: this generator reads %d" % (sv, SUPPORTED_SCHEMA_VERSION))
    profiles = intel_profiles(jsyaml.load(os.path.join(site_dir, "taxonomy.yaml")))
    ids = set(profiles)
    gens = {p["generation"] for p in profiles.values()}
    strat = _map(jsyaml.load(os.path.join(site_dir, "strategies", "single_node_tp.yaml")), "single_node_tp.yaml")

    models, unported, order = {}, [], []
    mdir = os.path.join(recipes_dir, "models")
    for root, dirs, files in os.walk(mdir):
        dirs.sort()
        for fn in sorted(files):
            if not fn.endswith((".yaml", ".yml")):
                continue
            relm = os.path.relpath(os.path.join(root, fn), mdir).split(os.sep)
            if len(relm) != 2:
                continue
            rel = "recipes/models/" + "/".join(relm)
            r = jsyaml.load(os.path.join(root, fn))
            e = recipe_entry(r, rel, ids, gens, unported)
            key = e["id"].lower()
            if key in models:
                raise GenError("%s and %s are the same id in lowercase" % (models[key]["file"], rel))
            models[key] = e
            date = (r.get("meta") or {}).get("date_updated")
            order.append((str(date or "")[:10], key))
    if not models:
        raise GenError("%s holds no recipes" % mdir)
    # findVariantRedirect (recipes.js) walks the recipes newest date_updated first: the
    # first recipe that claims a variant model id serves it
    index = {}
    for _, key in sorted(order, key=lambda o: o[0], reverse=True):
        for vk, v in models[key]["variants"].items():
            mid = (v.get("model_id") or "").lower()
            if mid and mid != key and mid not in models and mid not in index:
                index[mid] = [key, vk]

    engines = {}
    for ver in versions:
        vdir = os.path.join(vllm_src, "v" + ver)
        if not os.path.isdir(vdir):
            raise GenError("no vLLM sources for %s (%s)" % (ver, vdir))
        engines[ver] = engine(vdir)

    return {
        "schema": SCHEMA,
        "recipes_commit": recipes_commit,
        "vllm_commit": vllm_commit,
        "vllm_tree": tree,
        "schema_version": sv,
        "license": {"spdx": "Apache-2.0", "text": license_text,
                    "sources": ["https://github.com/%s/tree/%s/recipes" % (VLLM_REPO, vllm_commit),
                                "https://github.com/%s/tree/%s" % (RECIPES_REPO, recipes_commit)]},
        "profiles": profiles,
        "single_node_tp": {"vllm_args": _args(strat.get("vllm_args"), "single_node_tp.yaml vllm_args"),
                           "env": _env(strat.get("env"), where="single_node_tp.yaml env")},
        "engines": engines,
        "models": models,
        "variant_index": index,
        "unported": sorted(unported, key=lambda u: (u["model"], u["scope"])),
    }


def dumps(doc):
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def content(doc):
    """The artifact without its commit ids: what a recipes move changes, if anything."""
    return {k: v for k, v in doc.items() if k not in COMMIT_KEYS and k != "license"} | {
        "license": {k: v for k, v in doc.get("license", {}).items() if k != "sources"}}


# ---- review diff ------------------------------------------------------------------------

def _changed_fields(a, b):
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def diff(base, head):
    """Lines naming what moved between two artifacts (either may be None)."""
    base, head, out = base or {}, head or {}, []
    for k in COMMIT_KEYS:
        if base.get(k) != head.get(k):
            out.append("%s %s -> %s" % (k, (base.get(k) or "none")[:12], (head.get(k) or "none")[:12]))
    bm, hm = base.get("models", {}), head.get("models", {})
    for key in sorted(set(bm) | set(hm)):
        if key not in bm:
            out.append("model added: %s" % hm[key]["id"])
        elif key not in hm:
            out.append("model removed: %s" % bm[key]["id"])
        elif bm[key] != hm[key]:
            out.append("model changed: %s (%s)" % (hm[key]["id"], ", ".join(_changed_fields(bm[key], hm[key]))))
    be, he = base.get("engines", {}), head.get("engines", {})
    for ver in sorted(set(be) | set(he), key=lambda v: [int(x) for x in re.findall(r"\d+", v)]):
        if ver not in be:
            out.append("vLLM %s added: %d tool parsers, %d reasoning parsers, %d flags" % (
                ver, len(he[ver]["tool_parsers"]), len(he[ver]["reasoning_parsers"]), len(he[ver]["flags"])))
            continue
        if ver not in he:
            out.append("vLLM %s removed" % ver)
            continue
        for k in ("tool_parsers", "reasoning_parsers", "flags"):
            add = sorted(set(he[ver][k]) - set(be[ver][k]))
            rm = sorted(set(be[ver][k]) - set(he[ver][k]))
            if add or rm:
                out.append("vLLM %s %s:%s%s" % (ver, k, "".join(" +" + x for x in add), "".join(" -" + x for x in rm)))
    for k in ("profiles", "single_node_tp", "variant_index", "schema_version"):
        if base.get(k) != head.get(k):
            out.append("%s changed" % k)
    bu = {json.dumps(u, sort_keys=True) for u in base.get("unported", [])}
    hu = {json.dumps(u, sort_keys=True) for u in head.get("unported", [])}
    out += ["unported added: %s" % u for u in sorted(hu - bu)] + ["unported removed: %s" % u for u in sorted(bu - hu)]
    if (base.get("license") or {}).get("text") != (head.get("license") or {}).get("text"):
        out.append("license text changed")
    return out

