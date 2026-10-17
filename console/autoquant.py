#!/usr/bin/env python3
"""autoquant: per-host weight-precision pick for the CPU agent (EXPERIMENTS.md H27).

The agent's static default is OpenVINO/Qwen3-4B-int4-ov. A probe
(bench/amx-autoquant/probe.sh) measures each precision arm on this host: turn
latency (TTFT plus decode) and a quality proxy (teacher-forced NLL delta against
fp16 on held-out text). `pick` keeps the arms whose NLL delta is within the gate
and returns the one with the lowest median turn latency; ties go to the static
default. `store` caches the winner under a host fingerprint; `select` returns the
cached winner while the fingerprint still matches, otherwise the static rule.

The fingerprint hashes what changes kernel choice and bandwidth: CPU model, ISA
and AMX flags, P/E-core topology, microcode and memory size. A new CPU, BIOS
microcode or DIMM set gives a new fingerprint, so a stale winner is ignored.

  autoquant.py fingerprint [--microcode X] [--mem-kib N] < facts.json
  autoquant.py arms < facts.json
  autoquant.py pick --results FILE [--gate NATS]
  autoquant.py store --cache FILE --results FILE [--gate NATS] [--microcode X] [--mem-kib N] < facts.json
  autoquant.py select [--cache FILE] [--microcode X] [--mem-kib N] < facts.json
"""

import argparse
import hashlib
import json
import os
import statistics
import sys

SCHEMA = "k3d-gpu.autoquant/v1"
DEFAULT_PRECISION = "int4"
MODELS = {
    "int4": "OpenVINO/Qwen3-4B-int4-ov",
    "int8": "OpenVINO/Qwen3-4B-int8-ov",
    "fp16": "OpenVINO/Qwen3-4B-fp16-ov",
}
# Pod memory per precision: weights (4B params x bytes/weight) plus KV cache and runtime.
MEMORY = {"int4": "6Gi", "int8": "8Gi", "fp16": "12Gi"}
# Pre-registered gate (H27): +0.05 nats/token over fp16, about +5% perplexity.
GATE_NATS = 0.05


def cpu_of(facts):
    cpu = ((facts or {}).get("host") or {}).get("cpu") or {}
    return {
        "model": cpu.get("model"),
        "isa": sorted(cpu.get("isa") or []),
        "amx": sorted(cpu.get("amx") or []),
        "hybrid": bool(cpu.get("hybrid")),
        "p_cores": cpu.get("p_cores"),
        "e_cores": cpu.get("e_cores"),
        "logical_cpus": cpu.get("logical_cpus"),
    }


def fingerprint(facts, microcode=None, mem_kib=None):
    ident = dict(cpu_of(facts), microcode=microcode or None,
                 mem_gib=(int(mem_kib) // (1024 * 1024)) if mem_kib else None)
    blob = json.dumps(ident, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def arms(facts):
    """Probe arms with the compute path each takes on this CPU.

    OpenVINO's CPU plugin runs f16 weights in bf16 on AMX/AVX512_BF16 hardware
    and in f32 otherwise; int8 weights take AMX-INT8, AVX512-VNNI or AVX-VNNI
    kernels when present. The path is what the ISA allows, not a measured fact:
    the probe records the compiled model's precision next to it.
    """
    cpu = cpu_of(facts)
    isa, amx = set(cpu["isa"]), set(cpu["amx"])
    if "amx_int8" in amx:
        i8 = "amx-int8"
    elif "avx512_vnni" in isa:
        i8 = "avx512-vnni-int8"
    elif "avx_vnni" in isa:
        i8 = "avx-vnni-int8"
    else:
        i8 = "avx2-int8"
    if "amx_bf16" in amx:
        f16 = "amx-bf16"
    elif "avx512_bf16" in isa:
        f16 = "avx512-bf16"
    else:
        f16 = "f32"
    return [
        {"precision": "int4", "model": MODELS["int4"], "path": "int4-weight-only"},
        {"precision": "int8", "model": MODELS["int8"], "path": i8},
        {"precision": "fp16", "model": MODELS["fp16"], "path": f16},
    ]


def pick(results, gate=GATE_NATS):
    """Winner of probe results: {"arms": {prec: {"turn_s": [..], "dnll": x, "flagged": bool}}}.

    Returns (winner or None, reasons). An arm without an NLL delta or with no
    latency samples cannot pass. None means no arm passed: keep the default.
    """
    passing, reasons = {}, []
    for prec, a in sorted((results.get("arms") or {}).items()):
        if prec not in MODELS:
            reasons.append("%s: unknown precision, ignored" % prec)
            continue
        turns = [t for t in (a.get("turn_s") or []) if isinstance(t, (int, float)) and t > 0]
        dnll = a.get("dnll")
        if not turns:
            reasons.append("%s: no latency samples" % prec)
        elif not isinstance(dnll, (int, float)):
            reasons.append("%s: no NLL delta" % prec)
        elif a.get("flagged"):
            reasons.append("%s: flagged (%s)" % (prec, a.get("flag_reason") or "confound"))
        elif dnll > gate:
            reasons.append("%s: dNLL %.4f > gate %.4f" % (prec, dnll, gate))
        else:
            passing[prec] = statistics.median(turns)
            reasons.append("%s: pass, median turn %.3f s, dNLL %.4f" % (prec, passing[prec], dnll))
    if not passing:
        return None, reasons
    best = min(passing.values())
    tied = sorted(p for p, v in passing.items() if v == best)
    winner = DEFAULT_PRECISION if DEFAULT_PRECISION in tied else tied[0]
    return winner, reasons


def load_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) and doc.get("schema") == SCHEMA else {}


def select(facts, cache, microcode=None, mem_kib=None):
    fp = fingerprint(facts, microcode, mem_kib)
    out = {"schema": SCHEMA, "fingerprint": fp, "arms": arms(facts)}
    ent = (cache.get("hosts") or {}).get(fp)
    if ent and ent.get("winner") in MODELS:
        w = ent["winner"]
        out.update(precision=w, model=MODELS[w], memory=MEMORY[w], source="measured",
                   measured_at=ent.get("measured_at"))
    else:
        stale = bool(cache.get("hosts"))
        out.update(precision=DEFAULT_PRECISION, model=MODELS[DEFAULT_PRECISION],
                   memory=MEMORY[DEFAULT_PRECISION],
                   source="rule-stale-cache" if stale else "rule", measured_at=None)
    return out


def store(facts, cache_path, results, gate=GATE_NATS, microcode=None, mem_kib=None):
    winner, reasons = pick(results, gate)
    fp = fingerprint(facts, microcode, mem_kib)
    if winner is None:
        return None, fp, reasons
    cache = load_cache(cache_path) or {"schema": SCHEMA, "hosts": {}}
    cache.setdefault("hosts", {})[fp] = {
        "winner": winner, "gate_nats": gate, "measured_at": results.get("measured_at"),
        "cpu": cpu_of(facts), "reasons": reasons,
    }
    d = os.path.dirname(cache_path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, cache_path)
    return winner, fp, reasons


def main(argv=None):
    ap = argparse.ArgumentParser(prog="autoquant.py")
    ap.add_argument("cmd", choices=["fingerprint", "arms", "pick", "store", "select"])
    ap.add_argument("--cache")
    ap.add_argument("--results")
    ap.add_argument("--gate", type=float, default=GATE_NATS)
    ap.add_argument("--microcode")
    ap.add_argument("--mem-kib")
    a = ap.parse_args(argv)
    if a.gate < 0:
        ap.error("--gate must be >= 0")
    if a.mem_kib is not None and not a.mem_kib.isdigit():
        ap.error("--mem-kib must be a non-negative integer")
    results = None
    if a.cmd in ("pick", "store"):
        if not a.results:
            ap.error("%s needs --results" % a.cmd)
        with open(a.results, encoding="utf-8") as f:
            results = json.load(f)
        if not isinstance(results, dict):
            ap.error("--results must hold a JSON object")
    if a.cmd == "pick":
        winner, reasons = pick(results, a.gate)
        print(json.dumps({"winner": winner, "reasons": reasons}, indent=2))
        return 0 if winner else 3
    facts = json.load(sys.stdin)
    if a.cmd == "fingerprint":
        print(fingerprint(facts, a.microcode, a.mem_kib))
    elif a.cmd == "arms":
        print(json.dumps(arms(facts), indent=2))
    elif a.cmd == "store":
        if not a.cache:
            ap.error("store needs --cache")
        winner, fp, reasons = store(facts, a.cache, results, a.gate, a.microcode, a.mem_kib)
        print(json.dumps({"winner": winner, "fingerprint": fp, "reasons": reasons}, indent=2))
        return 0 if winner else 3
    else:
        print(json.dumps(select(facts, load_cache(a.cache) if a.cache else {}, a.microcode, a.mem_kib),
                         indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
