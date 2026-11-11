"""hack/vllm-recipes drift: k3d-gpu's own vLLM arguments next to the recipes'
hardware_overrides, per Intel generation, each difference labelled with the reason
k3d-gpu differs (DRIFT_REASONS) or UNEXPLAINED. A report: nothing here changes an
argument k3d-gpu emits.

  xpu  k3d-gpu: the Intel GPU branch of scripts/k3d-gpu vllm_manifest and its
       --tensor-parallel-size item, rendered with no K3D_GPU_* set, and VLLM_XPU_ENV.
       recipes: every hardware_overrides block keyed xpu or an xpu profile (arc_pro_b60,
       arc_pro_b70), at recipe, variant and single_node_tp level.
  cpu  k3d-gpu: the vllm-cpu engine of canary/engines.toml (vLLM on the CPU as the fit
       sizes it). recipes: the blocks keyed cpu or a cpu profile (xeon5, xeon6).

A flag is compared both ways: one k3d-gpu sets that no recipe block sets is a difference
(the recipes leave vLLM's default), and so is one a recipe sets that k3d-gpu does not. An
env name is compared where a recipe block sets it; k3d-gpu's other env is runtime plumbing
the recipes do not cover. Names and values are compared as vLLM reads them: --a_b is
--a-b, --no-X is X off and a bare --X is X on, and numbers compare by value (0.90 is 0.9).
Identity flags (model, served name, port, revision, host) are not compared.

Standard library only.
"""

import json
import os
import re
import tomllib

GENERATIONS = ("xpu", "cpu")
IDENTITY = {"--model", "--served-model-name", "--port", "--host", "--revision"}

# What a reason rests on:
#   ledger         an EXPERIMENTS.md row with a SUPPORTED or REFUTED verdict decides it
#   measured       a bench record in this tree
#   intel-runtime  what Intel's own runtime sets (EAT, or the Arc runtime the launcher follows)
#   fit            console/fit.py sizes it for the host and the model
#   policy         a k3d-gpu rule, stated at `source`
#   review         known and open: printed REVIEW, it needs a measured row before k3d-gpu moves
KINDS = ("ledger", "measured", "intel-runtime", "fit", "policy", "review")

# Why k3d-gpu's value differs from the recipes', per (generation, name). `source` starts
# with the file that states it. An entry no difference on the fixture uses fails
# tests/vllm_recipes_drift_test.py, so a stale reason is removed with the drift it explained.
DRIFT_REASONS = {
    ("xpu", "--dtype"): {
        "kind": "review", "source": "scripts/k3d-gpu vllm_manifest",
        "why": "float16 is the Intel Arc runtime's value the launcher follows; the recipes leave dtype to the "
               "checkpoint (bf16 on most Arc recipes); k3d-gpu changes it only on a measured bf16 against "
               "float16 row on the B70"},
    ("xpu", "--max-model-len"): {
        "kind": "fit", "source": "console/fit.py _flags",
        "why": "the fit sizes context for the host's cards and the model (K3D_GPU_AGENT_MAX_LEN, default 8192); "
               "a recipe's value sizes its own node"},
    ("xpu", "--tensor-parallel-size"): {
        "kind": "fit", "source": "console/fit.py _flags",
        "why": "one card per rank, as many as the fit picks (K3D_GPU_AGENT_GPUS, default 1); a recipe's value "
               "sizes its own node"},
    ("xpu", "--kv-cache-dtype"): {
        "kind": "fit", "source": "console/fit.py _flags",
        "why": "the fit sets --kv-cache-dtype to the KV element size it counted for the host and the model"},
    ("xpu", "--max-num-batched-tokens"): {
        "kind": "intel-runtime", "source": "scripts/k3d-gpu vllm_manifest",
        "why": "2048 is the Intel Arc runtime's value the launcher follows; the vLLM agent served Qwen3-4B with "
               "it on an Arc Pro B70 (bench/vllm-agent/results/2026-11-08)"},
    ("xpu", "--quantization"): {
        "kind": "policy", "source": "console/model_opt.py",
        "why": "the served precision is the optimizer's variant pick or K3D_GPU_AGENT_PRECISION, never an "
               "in-place quantization from a recipe"},
    ("xpu", "TORCH_LLM_ALLREDUCE"): {
        "kind": "policy", "source": "scripts/k3d-gpu VLLM_XPU_ENV",
        "why": "an IPEX setting; neither pinned XPU image (vllm-openai-xpu, llm-scaler) uses IPEX"},
    ("cpu", "--dtype"): {
        "kind": "intel-runtime", "source": "bench/eatflow/results/2026-10-20/H51/B1-hold1/values.yaml",
        "why": "EAT's own vLLM CPU defaults (defaultModelConfigs.extraCmdArgs) pass --dtype bfloat16, which "
               "k3d-gpu's EAT overlay keeps"},
    ("cpu", "--max-model-len"): {
        "kind": "fit", "source": "console/fit.py _flags",
        "why": "the fit sizes context for the host and the model"},
    ("cpu", "--max-num-seqs"): {
        "kind": "fit", "source": "console/fit.py _flags",
        "why": "the fit sizes concurrency for the host and the model"},
    ("cpu", "--gpu-memory-utilization"): {
        "kind": "fit", "source": "console/fit.py _flags",
        "why": "on the CPU the fit sizes the KV cache with VLLM_CPU_KVCACHE_SPACE (GiB)"},
}


class DriftError(Exception):
    pass


# ---- names and values ---------------------------------------------------------------------

def flag_name(name):
    return "--" + name[2:].replace("_", "-")


def norm_flag(name, value):
    """(name, value) as vLLM reads the flag: --no-X with no value is X off, a bare flag on."""
    name = flag_name(name)
    if value is None:
        return (name[:2] + name[5:], "off") if name.startswith("--no-") else (name, "on")
    return name, value


def tokens(args):
    """(flag, value) pairs of an argument list, as the recipes site dedupes them: a flag
    takes the next item as its value unless that item starts with -, --a=b is a and b, and
    a later occurrence of a flag wins."""
    out, i = {}, 0
    while i < len(args):
        a = args[i]
        if not a.startswith("-"):
            i += 1
            continue
        if a.startswith("--") and "=" in a:
            k, v = a.split("=", 1)
            i += 1
        elif i + 1 < len(args) and not args[i + 1].startswith("-"):
            k, v = a, args[i + 1]
            i += 2
        else:
            k, v = a, None
            i += 1
        k, v = norm_flag(k, v)
        out[k] = v
    return out


def same(a, b):
    if a == b:
        return True
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


# ---- k3d-gpu's side -----------------------------------------------------------------------

def _read(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise DriftError("%s is missing" % rel)


def _fn_body(text, name):
    m = re.search(r"(?ms)^%s\(\) \{\n(.*?)^\}" % re.escape(name), text)
    if not m:
        raise DriftError("scripts/k3d-gpu has no %s()" % name)
    return m.group(1)


def _expand(word, text):
    """A launcher item with each ${VAR} or ${VAR:-default} as it renders with no K3D_GPU_*
    set: the default, or the VAR="${K3D_GPU_...:-default}" assignment's."""
    def one(m):
        var, default = m.group(1), m.group(2)
        if default is not None:
            return default
        d = re.search(r'(?m)^%s="\$\{K3D_GPU_\w+:-([^}]*)\}"$' % re.escape(var), text)
        if not d:
            raise DriftError("scripts/k3d-gpu: no default for ${%s} (in %s)" % (var, word))
        return d.group(1)
    return re.sub(r"\$\{(\w+)(?::-([^}]*))?\}", one, word)


ITEM = re.compile(r'"\$\{item\}(--[^"]+)"')


def k3d_xpu(root):
    """k3d-gpu's flags and env for vLLM on an Intel GPU, with no K3D_GPU_* set: the GPU
    (else) branch of vllm_manifest's `if [ "${device}" = HPU ]` and the unconditional
    items after it, and VLLM_XPU_ENV."""
    text = _read(root, "scripts/k3d-gpu")
    body = _fn_body(text, "vllm_manifest")
    m = re.search(r'(?ms)^([ \t]*)if \[ "\$\{device\}" = HPU \]; then\n.*?^\1else\n(.*?)^\1fi\n(.*)', body)
    if not m:
        raise DriftError('scripts/k3d-gpu vllm_manifest: no if [ "${device}" = HPU ] ... else ... fi')
    items = ITEM.findall(m.group(2))
    items += [i for ln in m.group(3).splitlines() if ln.strip().startswith("args+=") for i in ITEM.findall(ln)]
    if not items:
        raise DriftError("scripts/k3d-gpu vllm_manifest: no ${item}--flag in its Intel GPU branch")
    flags = {}
    for it in items:
        k, sep, v = _expand(it, text).partition("=")
        k, v = norm_flag(k, v if sep else None)
        if k not in IDENTITY:
            flags[k] = v
    am = re.search(r"(?ms)^VLLM_XPU_ENV=\(\n(.*?)^\)", text)
    if not am:
        raise DriftError("scripts/k3d-gpu has no VLLM_XPU_ENV=(...)")
    env = {}
    for ln in am.group(1).splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            k, _, v = ln.strip('"').partition("=")
            env[k] = v
    return flags, env, "scripts/k3d-gpu vllm_manifest (Intel GPU, no K3D_GPU_* set) + VLLM_XPU_ENV"


def k3d_cpu(root):
    """k3d-gpu's flags and env for vLLM on the CPU: the vllm-cpu canary engine's args and
    env, which follow the fit's vLLM CPU flags (canary/engines.toml)."""
    try:
        doc = tomllib.loads(_read(root, "canary/engines.toml"))
    except tomllib.TOMLDecodeError as e:
        raise DriftError("canary/engines.toml: %s" % e)
    eng = next((e for e in doc.get("engine", []) if e.get("id") == "vllm-cpu"), None)
    if eng is None:
        raise DriftError("canary/engines.toml has no vllm-cpu engine")
    flags = {k: v for k, v in tokens(eng.get("args", [])).items() if k not in IDENTITY}
    env = dict(e.split("=", 1) for e in eng.get("env", []))
    return flags, env, "canary/engines.toml vllm-cpu (vLLM on the CPU as the fit sizes it)"


# ---- the recipes' side --------------------------------------------------------------------

def blocks(art, gen):
    """(label, block) of every hardware_overrides block keyed `gen` or one of its profiles."""
    keys = [gen] + sorted(p for p, d in (art.get("profiles") or {}).items() if d.get("generation") == gen)
    out = []
    for key in sorted(art.get("models") or {}):
        m = art["models"][key]
        scopes = [("", m.get("overrides") or {})]
        scopes += [(" variants.%s" % vk, v.get("overrides") or {})
                   for vk, v in sorted((m.get("variants") or {}).items())]
        scopes += [(" single_node_tp", (m.get("single_node_tp") or {}).get("overrides") or {})]
        for where, ovr in scopes:
            for k in keys:
                if k in ovr:
                    out.append(("%s%s%s" % (m["id"], where, "" if k == gen else " [%s]" % k), ovr[k]))
    return out


def _values(found):
    """[{value, count, models}] by count, then value."""
    vals = [{"value": v, "count": len(ids), "models": ids} for v, ids in found.items()]
    return sorted(vals, key=lambda x: (-x["count"], str(x["value"])))


def _merge(found, v, label):
    for seen in found:
        if same(seen, v):
            found[seen].append(label)
            return
    found[v] = [label]


# ---- the report ---------------------------------------------------------------------------

def compare(gen, k3d_flags, k3d_env, bl):
    rec_flags, rec_env = {}, {}
    for label, b in bl:
        for k, v in tokens(b.get("args") or []).items():
            _merge(rec_flags.setdefault(k, {}), v, label)
        for k, v in sorted((b.get("env") or {}).items()):
            _merge(rec_env.setdefault(k, {}), v, label)
    rows = []
    for kind, mine, theirs, names in (("flag", k3d_flags, rec_flags, set(k3d_flags) | set(rec_flags)),
                                      ("env", k3d_env, rec_env, set(rec_env))):
        for name in sorted(names):
            ours, vals = mine.get(name), _values(theirs.get(name, {}))
            agree = name in mine and vals and all(same(ours, x["value"]) for x in vals)
            reason = None if agree else DRIFT_REASONS.get((gen, name))
            status = "agree" if agree else ("unexplained" if reason is None else
                                            "review" if reason["kind"] == "review" else "explained")
            rows.append({"name": name, "kind": kind, "k3d_gpu": ours, "recipes": vals, "status": status,
                         "reason": reason})
    return rows


def report(art, root):
    gens = {}
    for gen, side in (("xpu", k3d_xpu), ("cpu", k3d_cpu)):
        flags, env, source = side(root)
        bl = blocks(art, gen)
        gens[gen] = {"k3d_gpu": {"source": source, "flags": flags, "env": env},
                     "blocks": [label for label, _ in bl], "rows": compare(gen, flags, env, bl)}
    return {"recipes_commit": art.get("recipes_commit"), "vllm_commit": art.get("vllm_commit"), "generations": gens}


def _models(ids):
    return ", ".join(ids[:5]) + (", +%d more" % (len(ids) - 5) if len(ids) > 5 else "")


def line(r):
    """One difference: name, k3d-gpu's value, each recipe value, and its label."""
    ours = "unset" if r["k3d_gpu"] is None else r["k3d_gpu"]
    theirs = ", ".join("%s x%d (%s)" % (x["value"], x["count"], _models(x["models"])) for x in r["recipes"]) or "unset"
    why = r["reason"]
    if r["status"] == "unexplained":
        label = "UNEXPLAINED"
    elif r["status"] == "review":
        label = "REVIEW [%s]: %s" % (why["source"], why["why"])
    else:
        label = "explained [%s, %s]: %s" % (why["kind"], why["source"], why["why"])
    return "%s: k3d-gpu %s; recipes %s -> %s" % (r["name"], ours, theirs, label)


def render(doc):
    out = ["vLLM recipes drift: k3d-gpu against the recipes' hardware_overrides (a report; nothing is applied)",
           "recipes %s, vllm@%s" % ((doc["recipes_commit"] or "none")[:12], (doc["vllm_commit"] or "none")[:12])]
    count = {"explained": 0, "review": 0, "unexplained": 0}
    for gen in GENERATIONS:
        g = doc["generations"][gen]
        out.append("%s: k3d-gpu %s; recipes %d hardware_overrides blocks"
                   % (gen, g["k3d_gpu"]["source"], len(g["blocks"])))
        agree = []
        for r in g["rows"]:
            if r["status"] == "agree":
                agree.append("%s %s x%d" % (r["name"], r["k3d_gpu"], sum(x["count"] for x in r["recipes"])))
                continue
            count[r["status"]] += 1
            out.append("  " + line(r))
        if agree:
            out.append("  agree: " + ", ".join(agree))
    out.append("%d differences: %d explained, %d REVIEW, %d UNEXPLAINED" % (
        sum(count.values()), count["explained"], count["review"], count["unexplained"]))
    return "\n".join(out) + "\n"


def moves(base, head):
    """Lines for a bump PR's notes: the differences new, changed or gone from base to head."""
    def diffs(doc):
        return {(gen, r["name"]): "%s %s" % (gen, line(r)) for gen in GENERATIONS
                for r in doc["generations"][gen]["rows"] if r["status"] != "agree"}
    b, h = diffs(base), diffs(head)
    out = []
    for key in sorted(set(b) | set(h), key=lambda k: (GENERATIONS.index(k[0]), k[1])):
        if key not in b:
            out.append("new: " + h[key])
        elif key not in h:
            out.append("gone: %s %s" % key)
        elif b[key] != h[key]:
            out.append("changed: " + h[key])
    return out


def dumps(doc):
    return json.dumps(doc, indent=1, sort_keys=True) + "\n"
