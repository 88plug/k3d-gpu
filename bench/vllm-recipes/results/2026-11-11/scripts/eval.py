#!/usr/bin/env python3
"""Verdicts from a b70-recipes run directory: eval.py OUT [LABEL ...]."""
import json
import os
import re
import sys

out = sys.argv[1]
labels = sys.argv[2:] or sorted({f.split("-")[0] for f in os.listdir(out) if re.match(r"^[A-Z0-9]+-agent-up\.txt$", f)})


def load(p):
    try:
        return json.load(open(os.path.join(out, p)))
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def msg(d):
    try:
        return d["choices"][0]["message"], d["choices"][0].get("finish_reason")
    except Exception:  # noqa: BLE001
        return None, None


def text(p):
    try:
        return open(os.path.join(out, p)).read()
    except OSError:
        return ""


res = {}
for L in labels:
    r = {}
    args = load(f"{L}-args.json")
    r["args"] = args if isinstance(args, list) else None
    if isinstance(args, list):
        r["tool_parser"] = next((a.split("=", 1)[1] for a in args if a.startswith("--tool-call-parser")), None)
        r["reasoning_parser"] = next((a.split("=", 1)[1] for a in args if a.startswith("--reasoning-parser")), None)
        r["auto_tool_choice"] = "--enable-auto-tool-choice" in args
        r["enforce_eager_count"] = args.count("--enforce-eager")
        r["trust_remote_code"] = "--trust-remote-code" in args
    ann = load(f"{L}-annotations.json")
    r["recipe_annotation"] = ann.get("k3d-gpu.io/vllm-recipe") if isinstance(ann, dict) else None
    plan = re.sub(r"\x1b\[[0-9;]*m", "", text(f"{L}-plan.txt"))
    r["plan_recipe_lines"] = [ln.strip() for ln in plan.splitlines() if re.search(r"recipe", ln, re.I)][:40]
    up = re.sub(r"\x1b\[[0-9;]*m", "", text(f"{L}-agent-up.txt"))
    r["warmup"] = next((ln.strip() for ln in up.splitlines() if "warm-up reply" in ln), None)
    # tool
    m, fin = msg(load(f"{L}-tool.json"))
    if m is not None:
        tc = m.get("tool_calls") or []
        city = None
        if tc:
            try:
                city = json.loads(tc[0]["function"]["arguments"]).get("city")
            except Exception:  # noqa: BLE001
                city = None
        content = m.get("content")
        r["tool"] = {
            "tool_calls": [(c["function"]["name"], c["function"]["arguments"]) for c in tc],
            "content": content, "finish": fin,
            "pass": bool(tc) and tc[0]["function"]["name"] == "get_weather" and bool(city and re.search("paris", city, re.I))
            and (not content or "<tool_call>" not in content),
        }
    # reasoning
    m, fin = msg(load(f"{L}-reason.json"))
    if m is not None:
        reasoning = m.get("reasoning") or m.get("reasoning_content") or ""
        content = m.get("content") or ""
        r["reason"] = {
            "reasoning_len": len(reasoning), "reasoning_head": reasoning[:200], "content": content[-300:], "finish": fin,
            "pass": bool(reasoning.strip()) and "391" in content and "<think>" not in content and "</think>" not in content,
        }
    m, fin = msg(load(f"{L}-chat.json"))
    if m is not None:
        content = m.get("content") or ""
        r["chat"] = {"content": content, "finish": fin, "pass": "paris" in content.lower()}
    res[L] = r
json.dump(res, sys.stdout, indent=1)
print()
