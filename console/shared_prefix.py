#!/usr/bin/env python3
"""Deploy-time shared-prefix detector for agent manifests (H25).

Reads agent manifests before any request is sent and reports how much of each
agent's prompt head (system text plus tool schemas) other agents share, as the
KV cache sees it:

  - The head is rendered the way the Qwen3 chat template does it: system text,
    then one `json.dumps(tool, ensure_ascii=False)` line per tool, in the order
    the client sends them. The template does not sort keys, so a tool schema
    with another key order is another prefix.
  - OVMS hashes KV blocks from token 0 (32 tokens per block on CPU, 16 on
    GPU), so only whole blocks of the common prefix are reusable.
  - Tokens are estimated as characters / 4 (no tokenizer in the stdlib). Every
    token count in the output is marked `_est`.

Output: groups of agents that share at least `min_tokens` reusable tokens, the
agents that would share more if their tool schemas were sent with sorted keys,
and a plan. The plan never turns prefix caching on: OVMS 2026.4.1 `--pull`
already enables it by default. It says whether one shared engine serves every
group, and gives the KV memory that the shared prefix would take per added
agent if it were not shared. That memory number is a prediction; whether the
dynamic pool turns sharing into resident memory saved is H25 (M), PENDING.

Manifest format (JSON, one agent per file, or a list of agents):
  {"name": "planner", "system": "...", "tools": [{"type": "function", ...}]}

Usage: shared_prefix.py [--device CPU|GPU] [--min-tokens N] [--kv-kib K] [--json] PATH...
       (a PATH that is a directory reads its *.json files, sorted)
Stdlib only.
"""

import argparse
import json
import os
import sys

# KV block size in tokens per device (OVMS 2026.4.1, kv_cache_manager.hpp).
BLOCK = {"CPU": 32, "GPU": 16}
# KV KiB per token of Qwen3-4B (36 layers x 8 KV heads x 128 x K and V): u8 on CPU, u4 on GPU.
KV_KIB = {"CPU": 72.0, "GPU": 36.0}
CHARS_PER_TOKEN = 4
TOOLS_HEADER = "\n\n# Tools\n\n<tools>\n"


class ManifestError(ValueError):
    pass


def render_head(agent, canonical=False):
    """The prompt head the chat template renders for this agent (system + tools)."""
    system = agent.get("system", "")
    tools = agent.get("tools")
    if tools is None:
        tools = []
    if not isinstance(system, str):
        raise ManifestError(f"agent {agent.get('name')!r}: system must be a string")
    if not isinstance(tools, list):
        raise ManifestError(f"agent {agent.get('name')!r}: tools must be a list")
    head = system
    if tools:
        head += TOOLS_HEADER
        head += "".join(json.dumps(t, ensure_ascii=False, sort_keys=canonical) + "\n" for t in tools)
        head += "</tools>"
    return head


def common_prefix(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def tokens_est(chars):
    return chars // CHARS_PER_TOKEN


def reusable_tokens(chars, block):
    """Whole KV blocks of a shared prefix: only those hash the same (F4)."""
    return (tokens_est(chars) // block) * block


def load(paths):
    agents = []
    for p in paths:
        files = [p]
        if os.path.isdir(p):
            files = sorted(os.path.join(p, f) for f in os.listdir(p) if f.endswith(".json"))
        for f in files:
            try:
                with open(f, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, json.JSONDecodeError) as e:
                raise ManifestError(f"{f}: {e}") from e
            for a in doc if isinstance(doc, list) else [doc]:
                if not isinstance(a, dict):
                    raise ManifestError(f"{f}: an agent must be a JSON object")
                a = dict(a)
                a.setdefault("name", os.path.splitext(os.path.basename(f))[0])
                agents.append(a)
    names = [a["name"] for a in agents]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ManifestError(f"duplicate agent names: {', '.join(map(str, dup))}")
    return agents


def group(heads, min_chars):
    """Greedy, input-ordered grouping: an agent joins the group whose common
    prefix with it is longest, if that is at least min_chars."""
    groups = []  # [prefix_len, [indices]]
    for i, h in enumerate(heads):
        best, best_len = None, -1
        for g in groups:
            rep = heads[g[1][0]]
            n = min(common_prefix(rep, h), g[0])
            if n > best_len:
                best, best_len = g, n
        if best is not None and best_len >= min_chars:
            best[0] = best_len
            best[1].append(i)
        else:
            groups.append([len(h), [i]])
    return groups


def analyse(agents, device="CPU", min_tokens=256, kv_kib=None):
    device = device.upper()
    if device not in BLOCK:
        raise ManifestError(f"device must be CPU or GPU, not {device!r}")
    if min_tokens < 1:
        raise ManifestError("min-tokens must be at least 1")
    block = BLOCK[device]
    kib = KV_KIB[device] if kv_kib is None else float(kv_kib)
    names = [str(a["name"]) for a in agents]
    raw = [render_head(a) for a in agents]
    canon = [render_head(a, canonical=True) for a in agents]
    min_chars = min_tokens * CHARS_PER_TOKEN

    out_groups = []
    for plen, idx in group(raw, min_chars):
        if len(idx) < 2:
            continue
        reuse = reusable_tokens(plen, block)
        out_groups.append({
            "agents": [names[i] for i in idx],
            "shared_chars": plen,
            "shared_tokens_est": tokens_est(plen),
            "reusable_tokens_est": reuse,
            "kv_mib_per_added_agent_unshared_est": round(reuse * kib / 1024, 1),
        })

    fixes = []
    for i in range(len(agents)):
        for j in range(i + 1, len(agents)):
            r = reusable_tokens(common_prefix(raw[i], raw[j]), block)
            c = reusable_tokens(common_prefix(canon[i], canon[j]), block)
            if c - r >= block and c >= min_tokens:
                fixes.append({"agents": [names[i], names[j]], "reusable_tokens_est": r,
                              "reusable_tokens_sorted_keys_est": c})

    shared = bool(out_groups)
    plan = {
        "engine": "shared" if shared else "per-agent-ok",
        "replicas": 1,
        "enable_prefix_caching": True,
        "prefix_caching_source": "OVMS 2026.4.1 --pull default (true); nothing to enable",
        "canonicalise_tool_keys": bool(fixes),
        "memory_claim": "PENDING H25 (M)",
    }
    return {
        "version": "shared-prefix/v1",
        "device": device,
        "block_tokens": block,
        "kv_kib_per_token": kib,
        "min_tokens": min_tokens,
        "agents": len(agents),
        "groups": out_groups,
        "key_order_fixes": fixes,
        "plan": plan,
    }


def human(r):
    lines = [f"{r['agents']} agent(s), {r['device']} KV blocks of {r['block_tokens']} tokens "
             f"(token counts are estimates, chars/4)"]
    if not r["groups"]:
        lines.append(f"no two agents share a prompt head of {r['min_tokens']}+ tokens; "
                     "one engine per agent loses nothing to prefix reuse")
    for g in r["groups"]:
        lines.append(f"shared head ~{g['reusable_tokens_est']} reusable tokens: {', '.join(g['agents'])} "
                     f"(~{g['kv_mib_per_added_agent_unshared_est']} MiB KV per added agent if not shared)")
    for f in r["key_order_fixes"]:
        lines.append(f"tool key order differs: {f['agents'][0]} / {f['agents'][1]} share "
                     f"~{f['reusable_tokens_est']} tokens as sent, ~{f['reusable_tokens_sorted_keys_est']} "
                     "with keys sorted; send tool schemas with one fixed key order")
    if r["groups"]:
        lines.append("plan: serve these agents from one engine; prefix caching is already on "
                     "(OVMS --pull default), no flag to add")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="k3d-gpu agent prefix", description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--device", default="CPU")
    ap.add_argument("--min-tokens", type=int, default=256)
    ap.add_argument("--kv-kib", type=float)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        r = analyse(load(a.paths), a.device, a.min_tokens, a.kv_kib)
    except ManifestError as e:
        print(f"shared_prefix: {e}", file=sys.stderr)
        return 2
    print(json.dumps(r, indent=2, sort_keys=True) if a.json else human(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
