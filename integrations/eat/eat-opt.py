#!/usr/bin/env python3
"""The k3d-gpu model optimizer applied to one EAT vLLM catalog model, as values.

Runs in the bastion (brownfield.sh model <id>) after the k3d-gpu values are
merged over EAT's xeon-values.yaml. It finds the Hugging Face id EAT's playbook
deploys for the catalog id (its `--set LLM_MODEL_ID="..."` line), asks
console/model_opt.py what that model wants on vLLM on the CPU at the chart's
vLLM version, and edits the model's modelConfigs entry in the merged values
only: engine arguments the entry does not already set are appended (EAT's own
are never removed or changed), and a speculative config is set when the
optimizer chose one. It prints every change against EAT stock. EAT's files are
not edited.

The model id itself is EAT's: the playbook passes it with --set, which wins over
any values file, so a different variant is reported, not applied.

  eat-opt.py PLAYBOOK VALUES CATALOG_ID CONSOLE_DIR [--cache DIR] [--thinking off|on|model]

EAT_MODEL_OPT=off skips it. Any failure prints one line and leaves the values as they are.
"""

import argparse
import json
import os
import re
import sys

import yaml


def catalog_model_id(playbook_text, catalog_id):
    """The LLM_MODEL_ID the playbook sets in the block gated on install-<catalog_id>."""
    tag = "install-" + catalog_id
    blocks = re.split(r"\n    - name: ", playbook_text)
    for b in blocks:
        if ("tags: " + tag) in b or ("'%s' in" % catalog_id) in b:
            m = re.search(r'--set LLM_MODEL_ID="([^"]+)"', b)
            if m:
                return m.group(1)
    return None


def vllm_version(values):
    tag = str(((values.get("image") or {}).get("tag")) or "")
    m = re.match(r"v?(\d+\.\d+\.\d+)", tag)
    return m.group(1) if m else None


def flags_of(args):
    return {a.split("=", 1)[0] for a in args if isinstance(a, str) and a.startswith("--")}


def apply(values, model_id, patch):
    """(changes, values): the patch merged into modelConfigs[model_id], additive only."""
    changes = []
    configs = values.setdefault("modelConfigs", {}) or {}
    values["modelConfigs"] = configs
    entry = configs.get(model_id)
    if entry is None:
        entry = json.loads(json.dumps(values.get("defaultModelConfigs") or {}))
        configs[model_id] = entry
        changes.append("modelConfigs[%s]: new entry, copied from defaultModelConfigs (EAT has none for this id)" % model_id)
    args = list(entry.get("extraCmdArgs") or [])
    have = flags_of(args)
    add = patch["add_args"]
    i = 0
    while i < len(add):
        flag = add[i]
        value = add[i + 1] if i + 1 < len(add) and not add[i + 1].startswith("--") else None
        i += 2 if value is not None else 1
        if flag == "--speculative-config":
            continue  # the chart writes it from speculativeConfig
        if flag in have:
            changes.append("kept EAT's %s (optimizer: %s)" % (flag, value if value is not None else "on"))
            continue
        args += [flag] + ([value] if value is not None else [])
        changes.append("+ %s%s" % (flag, " " + value if value is not None else ""))
    entry["extraCmdArgs"] = args
    if patch.get("speculative_config"):
        if entry.get("speculativeConfig"):
            changes.append("kept EAT's speculativeConfig %s (optimizer: %s)" % (entry["speculativeConfig"], patch["speculative_config"]))
        else:
            entry["speculativeConfig"] = patch["speculative_config"]
            values.setdefault("speculative", {})["enabled"] = True
            changes.append("+ speculativeConfig %s, speculative.enabled true" % patch["speculative_config"])
    if patch["model"] and patch["model"] != model_id:
        changes.append("not applied: the optimizer would serve %s, but EAT's playbook sets LLM_MODEL_ID=%s with --set" % (patch["model"], model_id))
    return changes, values


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("playbook")
    ap.add_argument("values")
    ap.add_argument("catalog_id")
    ap.add_argument("console")
    ap.add_argument("--cache")
    ap.add_argument("--thinking", default=os.environ.get("EAT_THINKING", "off"))
    a = ap.parse_args(argv[1:])
    if os.environ.get("EAT_MODEL_OPT", "auto") == "off":
        print("model optimizer off (EAT_MODEL_OPT=off): EAT's values as merged")
        return 0
    try:
        with open(a.playbook, encoding="utf-8") as f:
            model_id = catalog_model_id(f.read(), a.catalog_id)
        with open(a.values, encoding="utf-8") as f:
            values = yaml.safe_load(f) or {}
    except OSError as e:
        print("model optimizer skipped: %s" % e)
        return 0
    if not model_id:
        print("model optimizer skipped: no LLM_MODEL_ID for %s in %s" % (a.catalog_id, a.playbook))
        return 0
    sys.path.insert(0, a.console)
    try:
        import model_opt
    except ImportError as e:
        print("model optimizer skipped: %s" % e)
        return 0
    bundle = model_opt.load_or_fetch(model_id, cache=a.cache)
    if bundle.get("source") in ("fallback", "none"):
        print("model optimizer: Hugging Face unreachable (%s), using %s" % (bundle.get("error"), "the built-in map" if bundle["source"] == "fallback" else "no metadata"))
    # EAT's playbook pins LLM_MODEL_ID, so every argument is computed for that id.
    doc = model_opt.optimize(bundle, "vllm", "CPU", {"vllm_version": vllm_version(values) or "0.24.0",
                                                     "thinking": a.thinking, "variant": model_id})
    if bundle["source"] != "none" and doc["model"] != model_id:
        print("model optimizer skipped: %s is not a vLLM bf16 candidate here (the optimizer would serve %s)" % (model_id, doc["model"]))
        return 0
    try:
        patch = model_opt.eat_patch(doc)
    except ValueError as e:
        print("model optimizer skipped: %s" % e)
        return 0
    changes, values = apply(values, model_id, patch)
    with open(a.values + ".tmp", "w", encoding="utf-8") as f:
        yaml.safe_dump(values, f, sort_keys=False)
    os.replace(a.values + ".tmp", a.values)
    print(patch["summary"])
    print("EAT overlay vs stock for %s (modelConfigs[%s]):" % (a.catalog_id, model_id))
    for c in changes or ["no change: EAT's entry already matches"]:
        print("  " + c)
    for line in patch["explain"][1:]:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
