#!/usr/bin/env python3
"""Bandwidth ceiling for one model on this host: `k3d-gpu plan --predict --model`.

Decode at batch 1 reads every weight the token touches once. For a mixture of
experts that is the dense part (attention, dense MLP, router, shared experts,
the output head) plus top_k / num_experts of the expert weights. The ceiling is

    decode tok/s <= bandwidth / bytes read per token

per placement: all on the CPU (DRAM bandwidth), all on one Intel GPU or split
over N with tensor parallelism (VRAM bandwidth), or experts on the CPU and the
rest on a GPU (llama.cpp -ngl 99 -ot exps=CPU), which adds the two terms and a
per-layer hop latency.

Parameter counts come from config.json shapes (hidden sizes, heads, experts,
layer types) or, for a GGUF file, from its tensor table (exact bytes). Bytes per
parameter come from the format. DRAM bandwidth is a measured STREAM triad cached
by `k3d-gpu doctor --perf --bandwidth` when that cache is from this host, else
the SMBIOS type 17 peak (channels x MT/s x 8 B), labelled theoretical. GPU
bandwidth is the device's spec sheet value.

This is a predictor. It has no compute, KV-cache or kernel-efficiency term and
does not model speculative decoding; EXPERIMENTS.md H54c measures how close a
measurement comes to it. Nothing here is a measured claim.

  moe_predict.py (--config FILE|DIR | --gguf FILE) [--format F] [--placement P]
      [--engine E] [--gpus N] [--sysroot DIR] [--bandwidth-cache FILE]
      [--dram-gbs N] [--gpu-gbs N] [--hop-us N] [--model-id ID] [--revision R] [--json]

Stdlib only.
"""

import argparse
import glob
import json
import os
import re
import struct
import sys

SCHEMA = "k3d-gpu.moe-predict/v1"
BANDWIDTH_SCHEMA = "k3d-gpu.bandwidth/v1"
STATUS = "prediction"
STATUS_NOTE = "a bandwidth ceiling, not a measurement (EXPERIMENTS.md H54c measures it)"
FIT_FRAC = 0.9      # weights may take this share of RAM or of one GPU's VRAM
HOP_US = 30.0       # per hop; two hops per layer (H54c pre-registration term, assumed)

# Intel GPUs by PCI device id: name, VRAM GiB, memory bandwidth GB/s, source.
GPU_SPECS = {
    "0xe223": ("Arc Pro B70", 32, 608.0,
               "spec (ark.intel.com product 245797: Graphics Memory Bandwidth 608 GB/s)"),
    "0xe211": ("Arc Pro B60", 24, 456.0,
               "spec (Intel Arc Pro B-Series quick reference guide v1.0, 2026-03: 456 GB/s)"),
}

# Engines per weight container, as the H54 engine scout verified them at the
# pinned versions (EXPERIMENTS.md H54 pre-registration). Order is preference.
ENGINES = [
    {"name": "ovms", "container": "ov", "devices": ("cpu", "gpu"), "tp": False, "formats": None,
     "image": "openvino/model_server:2026.4.1 (CPU) / 2026.4.1-gpu"},
    {"name": "llm-scaler-vllm", "container": "safetensors", "devices": ("gpu",), "tp": True,
     "formats": ("bf16", "fp16", "fp8", "w4a16"), "image": "intel/llm-scaler-vllm:1.5"},
    {"name": "vllm-cpu", "container": "safetensors", "devices": ("cpu",), "tp": False,
     "formats": ("bf16", "fp16", "w4a16"), "image": "vllm/vllm-openai-cpu:v0.32.0"},
    {"name": "sglang-cpu", "container": "safetensors", "devices": ("cpu",), "tp": False,
     "formats": ("bf16", "fp8", "w4a16"), "image": "lmsysorg/sglang:v0.5.22-xeon"},
    {"name": "llama.cpp", "container": "gguf", "devices": ("cpu", "gpu", "hybrid"), "tp": False,
     "formats": None, "image": "ghcr.io/ggml-org/llama.cpp:server-b11749 / server-intel-b11749"},
]
# Architectures with a narrower engine set than their container suggests.
ARCH_ENGINES = {"Qwen4ExpForConditionalGeneration": ("llama.cpp",), "qwen4exp": ("llama.cpp",)}

# Source preference (H54 pre-registration): model author and Intel, then RedHatAI,
# then 88plug or built here, then third party.
SOURCE_RANKS = ((1, ("google", "qwen", "intel", "openvino"), "model author or Intel"),
                (2, ("redhatai",), "RedHatAI"),
                (3, ("88plug",), "88plug"))

# GGML block types: (elements per block, bytes per block), gguf-py constants.py @b11749.
GGML_TYPES = {
    0: ("f32", 1, 4), 1: ("f16", 1, 2), 2: ("q4_0", 32, 18), 3: ("q4_1", 32, 20),
    6: ("q5_0", 32, 22), 7: ("q5_1", 32, 24), 8: ("q8_0", 32, 34), 9: ("q8_1", 32, 36),
    10: ("q2_k", 256, 84), 11: ("q3_k", 256, 110), 12: ("q4_k", 256, 144), 13: ("q5_k", 256, 176),
    14: ("q6_k", 256, 210), 15: ("q8_k", 256, 292), 16: ("iq2_xxs", 256, 66), 17: ("iq2_xs", 256, 74),
    18: ("iq3_xxs", 256, 98), 19: ("iq1_s", 256, 50), 20: ("iq4_nl", 32, 18), 21: ("iq3_s", 256, 110),
    22: ("iq2_s", 256, 82), 23: ("iq4_xs", 256, 136), 24: ("i8", 1, 1), 25: ("i16", 1, 2),
    26: ("i32", 1, 4), 27: ("i64", 1, 8), 28: ("f64", 1, 8), 29: ("iq1_m", 256, 56),
    30: ("bf16", 1, 2), 34: ("tq1_0", 256, 54), 35: ("tq2_0", 256, 66), 39: ("mxfp4", 32, 17),
    40: ("nvfp4", 64, 36), 41: ("q1_0", 128, 18), 42: ("q2_0", 64, 18),
}
GGUF_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
                10: "<Q", 11: "<q", 12: "<d"}


# ------------------------------------------------------------------ parameters
def _get(d, *keys, default=None):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return default


def params_from_config(cfg):
    """Text-model parameter counts from a Hugging Face config.json.

    Vision/audio towers and MTP heads are left out: decode does not read them."""
    tc = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else cfg
    H, L, V = tc.get("hidden_size"), tc.get("num_hidden_layers"), tc.get("vocab_size")
    heads = tc.get("num_attention_heads")
    if not all(isinstance(x, int) and x > 0 for x in (H, L, V, heads)):
        raise ValueError("config.json needs hidden_size, num_hidden_layers, vocab_size and "
                         "num_attention_heads (in text_config for a multimodal model)")
    kv = _get(tc, "num_key_value_heads", default=heads)
    hd = _get(tc, "head_dim", default=H // heads)
    ghd, gkv = tc.get("global_head_dim"), _get(tc, "num_global_key_value_heads", default=kv)
    q_mult = 2 if tc.get("attn_output_gate") else 1
    types = tc.get("layer_types") or ["full_attention"] * L

    def attention(kind):
        if kind == "linear_attention":      # gated DeltaNet (qwen3_5 / qwen3_next)
            kh, kd = tc["linear_num_key_heads"], tc["linear_key_head_dim"]
            vh, vd = tc["linear_num_value_heads"], tc["linear_value_head_dim"]
            qkv = 2 * kh * kd + vh * vd
            return H * qkv + H * vh * vd + 2 * H * vh + vh * vd * H + qkv * _get(tc, "linear_conv_kernel_dim", default=4)
        if kind == "full_attention" and ghd:   # global layers with their own head dim (gemma4)
            nv = 0 if tc.get("attention_k_eq_v") else 1
            return H * heads * ghd * q_mult + H * gkv * ghd * (1 + nv) + heads * ghd * H
        return H * heads * hd * q_mult + 2 * H * kv * hd + heads * hd * H

    E = _get(tc, "num_experts", "num_local_experts", "n_routed_experts", default=0) or 0
    k = _get(tc, "num_experts_per_tok", "top_k_experts", "moe_top_k", default=0) or 0
    inter = tc.get("intermediate_size") or 0
    moe_inter = _get(tc, "moe_intermediate_size", default=inter)
    first_dense = tc.get("first_k_dense_replace") or 0
    mlp_only = set(tc.get("mlp_only_layers") or [])
    step = tc.get("decoder_sparse_step") or 1
    shared = 0
    if tc.get("n_shared_experts"):
        shared = tc["n_shared_experts"] * 3 * H * moe_inter
    elif tc.get("shared_expert_intermediate_size"):
        shared = 3 * H * tc["shared_expert_intermediate_size"] + H
    dense = experts = 0
    moe_layers = 0
    for i in range(L):
        dense += attention(types[i] if i < len(types) else "full_attention")
        is_moe = E > 0 and i >= first_dense and i not in mlp_only and (i + 1) % step == 0
        if is_moe:
            moe_layers += 1
            experts += E * 3 * H * moe_inter
            dense += E * H + shared                      # router + shared experts
            if tc.get("enable_moe_block"):               # dense MLP beside the MoE block
                dense += 3 * H * inter
        else:
            dense += 3 * H * inter
    tied = _get(cfg, "tie_word_embeddings", default=_get(tc, "tie_word_embeddings", default=True))
    embed = V * H
    head = 0 if tied else V * H
    active_experts = experts * k // E if E else 0
    return {"arch": (cfg.get("architectures") or [cfg.get("model_type") or "unknown"])[0],
            "moe": E > 0, "num_experts": E, "top_k": k, "layers": L, "moe_layers": moe_layers,
            "hidden": H, "vocab": V, "tied": bool(tied), "embed": embed, "lm_head": head,
            "dense": dense, "experts_total": experts, "experts_active": active_experts,
            "total": embed + head + dense + experts,
            "active": dense + active_experts + V * H}


# ------------------------------------------------------------------ formats
def format_info(fmt):
    """Bytes per parameter for a weight format: bits/8 plus an fp16 scale per group
    (and a zero point at the weight width for asymmetric groups)."""
    f = (fmt or "").lower()

    def grouped(bits, group, sym):
        return bits / 8 + (2 + (0 if sym else bits / 8)) / group

    if f in ("bf16", "fp16"):
        return {"bpp": 2.0, "container": "safetensors", "family": f, "source": f"{f}: 2 B per parameter"}
    if f == "fp8":
        return {"bpp": 1.0, "container": "safetensors", "family": "fp8",
                "source": "fp8: 1 B per parameter (per-channel scales not counted)"}
    m = re.fullmatch(r"w4a16-g(\d+)", f)
    if m:
        g = int(m.group(1))
        return {"bpp": grouped(4, g, False), "container": "safetensors", "family": "w4a16",
                "source": f"int4 weights, fp16 scale and 4-bit zero point per {g} (GPTQ/AutoRound layout)"}
    m = re.fullmatch(r"ov-int(4|8)(?:-(sym|asym)-g(\d+))?", f)
    if m:
        bits = int(m.group(1))
        if not m.group(3):
            return {"bpp": bits / 8, "container": "ov", "family": "ov",
                    "source": f"OpenVINO int{bits} per channel: {bits} bits per parameter"}
        g, sym = int(m.group(3)), m.group(2) == "sym"
        return {"bpp": grouped(bits, g, sym), "container": "ov", "family": "ov",
                "source": f"OpenVINO int{bits} {m.group(2)} group {g}: fp16 scale" +
                          ("" if sym else f" and {bits}-bit zero point") + f" per {g}"}
    if f == "q4_k_m":
        return {"bpp": 0.6, "container": "gguf", "family": "gguf",
                "source": "q4_k_m approximate (Q4_K 4.5 / Q6_K 6.56 bits mix, ~4.8 bits); pass the .gguf for exact bytes"}
    for name, per, size in GGML_TYPES.values():
        if f == name and name not in ("f32", "f16", "bf16", "f64", "i8", "i16", "i32", "i64"):
            return {"bpp": size / per, "container": "gguf", "family": "gguf",
                    "source": f"GGML {name}: {size} B per {per} weights"}
    raise ValueError(f"unknown format {fmt!r} (use bf16, fp16, fp8, w4a16-gN, ov-int4-sym-gN, "
                     "ov-int4-asym-gN, ov-int8, q4_0, q8_0, q4_k_m, or another GGML type)")


def format_from_dir(d, cfg):
    """The weights' format from an OpenVINO IR's openvino_config.json, else the
    config's quantization_config, else its dtype."""
    try:
        with open(os.path.join(d, "openvino_config.json")) as f:
            ov = json.load(f)
    except (OSError, ValueError):
        ov = None
    if isinstance(ov, dict):
        qc = ov.get("quantization_config") or {}
        q = (qc.get("quantization_configs") or {}).get("lm_model") or qc
        bits = q.get("bits")
        if bits in (4, 8):
            g = q.get("group_size") or -1
            if g <= 0:
                return f"ov-int{bits}"
            return f"ov-int{bits}-{'sym' if q.get('sym') else 'asym'}-g{g}"
        return "ov-int8"
    q = cfg.get("quantization_config") or {}
    method = str(q.get("quant_method", "")).lower()
    if q.get("bits") == 4 and q.get("group_size"):
        return f"w4a16-g{q['group_size']}"
    if "fp8" in method or q.get("fmt") in ("e4m3", "float8_e4m3fn"):
        return "fp8"
    tc = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else cfg
    dtype = str(_get(tc, "dtype", "torch_dtype", default=_get(cfg, "dtype", "torch_dtype", default="bfloat16")))
    return "fp16" if dtype in ("float16", "fp16") else "bf16"


def config_weights(p, fmt):
    fi = format_info(fmt)
    b = fi["bpp"]
    dense = round((p["dense"] + p["vocab"] * p["hidden"]) * b)
    active = round(p["experts_active"] * b)
    return {"source": "config.json shapes x bytes per parameter", "format": fmt.lower(),
            "container": fi["container"], "family": fi["family"], "bpp": round(b, 6),
            "format_source": fi["source"], "arch": p["arch"], "moe": p["moe"],
            "num_experts": p["num_experts"], "top_k": p["top_k"], "layers": p["layers"],
            "params_total": p["total"], "params_active": p["active"],
            "experts_total_bytes": round(p["experts_total"] * b), "experts_active_bytes": active,
            "dense_bytes": dense, "read_bytes": dense + active, "total_bytes": round(p["total"] * b)}


# ------------------------------------------------------------------ GGUF
def _rd(f, fmt):
    n = struct.calcsize(fmt)
    b = f.read(n)
    if len(b) != n:
        raise ValueError("GGUF truncated")
    return struct.unpack(fmt, b)[0]


def _rstr(f):
    n = _rd(f, "<Q")
    b = f.read(n)
    if len(b) != n:
        raise ValueError("GGUF truncated")
    return b.decode("utf-8", "replace")


def _rval(f, t):
    if t in GGUF_SCALARS:
        return _rd(f, GGUF_SCALARS[t])
    if t == 8:
        return _rstr(f)
    if t == 9:
        et, n = _rd(f, "<I"), _rd(f, "<Q")
        if et in GGUF_SCALARS:              # skip numeric arrays without decoding them
            f.seek(n * struct.calcsize(GGUF_SCALARS[et]), 1)
            return None
        for _ in range(n):
            _rval(f, et)
        return None
    raise ValueError(f"GGUF value type {t} unknown")


def gguf_read(path):
    """GGUF v2/v3 metadata (arrays dropped) and tensor table: name, dims, type, bytes."""
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError(f"{path}: not a GGUF file")
        version = _rd(f, "<I")
        if version not in (2, 3):
            raise ValueError(f"{path}: GGUF version {version} unsupported (2 or 3)")
        n_t, n_kv = _rd(f, "<Q"), _rd(f, "<Q")
        meta = {}
        for _ in range(n_kv):
            k = _rstr(f)
            meta[k] = _rval(f, _rd(f, "<I"))
        tensors = []
        for _ in range(n_t):
            name = _rstr(f)
            dims = [_rd(f, "<Q") for _ in range(_rd(f, "<I"))]
            typ = _rd(f, "<I")
            _rd(f, "<Q")
            if typ not in GGML_TYPES:
                raise ValueError(f"{path}: tensor {name} has GGML type {typ}, unknown here")
            tname, per, size = GGML_TYPES[typ]
            n = 1
            for d in dims:
                n *= d
            tensors.append({"name": name, "dims": dims, "type": tname, "bytes": n // per * size})
    return {"path": path, "version": version, "meta": meta, "tensors": tensors}


def gguf_weights(g):
    meta = g["meta"]
    arch = meta.get("general.architecture") or "unknown"
    E = meta.get(f"{arch}.expert_count") or 0
    k = meta.get(f"{arch}.expert_used_count") or 0
    L = meta.get(f"{arch}.block_count") or 0
    exp = dense = embed = head = 0
    by_type = {}
    for t in g["tensors"]:
        by_type[t["type"]] = by_type.get(t["type"], 0) + t["bytes"]
        if "_exps" in t["name"]:
            exp += t["bytes"]
        elif t["name"] == "token_embd.weight":
            embed += t["bytes"]
        elif t["name"] == "output.weight":
            head += t["bytes"]
        elif "token_embd" in t["name"]:     # per-layer embedding tables: a row gather
            pass
        else:
            dense += t["bytes"]
    tied = head == 0
    read_dense = dense + (embed if tied else head)
    active = round(exp * k / E) if E else 0
    total = sum(t["bytes"] for t in g["tensors"])
    fmt = max(by_type, key=by_type.get) if by_type else "unknown"
    return {"source": "GGUF tensor table (exact bytes)", "format": fmt, "container": "gguf",
            "family": "gguf", "bpp": None, "format_source": "bytes by GGML type: " +
            ", ".join(f"{t} {b / 1e9:.2f} GB" for t, b in sorted(by_type.items(), key=lambda x: -x[1])),
            "arch": arch, "moe": E > 0, "num_experts": E, "top_k": k, "layers": L, "tied": tied,
            "experts_total_bytes": exp, "experts_active_bytes": active, "dense_bytes": read_dense,
            "read_bytes": read_dense + active, "total_bytes": total}


# ------------------------------------------------------------------ host
def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def host_from_sysroot(root=""):
    cpuinfo = _read(f"{root}/proc/cpuinfo")
    blocks = [b for b in cpuinfo.split("\n\n") if b.strip()]

    def field(b, k):
        m = re.search(rf"^{re.escape(k)}\s*:\s*(.*)$", b, re.M)
        return m.group(1).strip() if m else None

    model = field(blocks[0], "model name") if blocks else None
    flags = (field(blocks[0], "flags") or "").split() if blocks else []
    pairs = {(field(b, "physical id"), field(b, "core id")) for b in blocks}
    per = int(field(blocks[0], "cpu cores") or 0) if blocks else 0
    pkgs = {field(b, "physical id") for b in blocks}
    cores = max(len(pairs), per * len(pkgs)) if blocks else None
    m = re.search(r"^MemTotal:\s+(\d+)", _read(f"{root}/proc/meminfo"), re.M)
    gpus = []
    for dev in sorted(glob.glob(f"{root}/sys/bus/pci/devices/*")):
        if _read(f"{dev}/vendor").strip() != "0x8086" or not _read(f"{dev}/class").startswith("0x03"):
            continue
        did = _read(f"{dev}/device").strip()
        name, spec_gib, bw, src = GPU_SPECS.get(did, (None, None, None, None))
        vram, vsrc = None, None
        res = _read(f"{dev}/resource").splitlines()
        if len(res) > 2:
            try:
                lo, hi = (int(x, 16) for x in res[2].split()[:2])
                if hi > lo and hi - lo + 1 >= 1 << 30:
                    vram, vsrc = hi - lo + 1, "bar"
            except ValueError:
                pass
        if vram is None and spec_gib:
            vram, vsrc = spec_gib << 30, "spec"
        gpus.append({"pci": os.path.basename(dev), "device": did, "name": name or f"Intel GPU {did}",
                     "vram_bytes": vram, "vram_source": vsrc, "bw_gbs": bw,
                     "bw_source": src or f"unknown device id {did}: pass --gpu-gbs"})
    return {"cpu_model": model, "amx": "amx_tile" in flags, "physical_cores": cores,
            "mem_kib": int(m.group(1)) if m else None, "gpus": gpus}


def _dmi17(raw):
    """(size MiB, configured MT/s, data width bits, bank/device locators) of one SMBIOS
    type 17 record (DSP0134 7.18 offsets, as the launcher's dmi17 reads them)."""
    if len(raw) < 34 or raw[0] != 17 or len(raw) < raw[1]:
        return None
    ln = raw[1]
    u16 = lambda o: raw[o] | raw[o + 1] << 8  # noqa: E731
    size = u16(12)
    if size == 0x7FFF and ln >= 32:
        size = raw[28] | raw[29] << 8 | raw[30] << 16 | (raw[31] & 127) << 24
    elif size == 0xFFFF:
        size = 0
    elif size & 0x8000:
        size = (size & 0x7FFF) // 1024
    cfg = u16(32)
    if cfg == 0xFFFF and ln >= 92:
        cfg = raw[88] | raw[89] << 8 | raw[90] << 16 | raw[91] << 24
    strings = raw[ln:].split(b"\0")

    def s(i):
        return strings[i - 1].decode("ascii", "replace") if 0 < i <= len(strings) else ""

    return size, cfg, u16(10), s(raw[16]) + " " + s(raw[17])


def dmi_theoretical(root=""):
    """Peak DRAM GB/s = populated channels x configured MT/s x 8 B from SMBIOS type 17
    (root-only on a live host). Channels are the distinct "...ChannelN" locator
    tokens; with none, each module's data width counts once."""
    mods, speeds, chans, widths = 0, [], set(), 0
    for e in sorted(glob.glob(f"{root}/sys/firmware/dmi/entries/17-*/raw")):
        try:
            with open(e, "rb") as f:
                r = _dmi17(f.read())
        except OSError:
            continue
        if not r or r[0] <= 0 or r[1] <= 0:
            continue
        size, cfg, dw, loc = r
        mods += 1
        speeds.append(cfg)
        if dw and dw != 0xFFFF:
            widths += dw // 8
        m = re.search(r"(.*?)Channel[\s_]*([A-Z0-9]+)", loc, re.I)
        if m:
            chans.add((m.group(1).split()[-1] if m.group(1).split() else "", m.group(2).upper()))
    if not mods:
        return None
    mts = min(speeds)
    data = len(chans) * 8 if chans else widths
    how = f"{len(chans)} channels x 8 B" if chans else f"{widths} B of module data width"
    return {"gbs": round(data * mts / 1000, 1), "modules": mods, "mts": mts, "data_bytes": data,
            "detail": f"{mods} modules, {how} x {mts} MT/s (SMBIOS type 17)"}


def dram_bandwidth(host, cache, given, dmi):
    """DRAM GB/s and where it came from: given > measured (this host's STREAM cache) >
    theoretical (SMBIOS) > none."""
    out = {"gbs": None, "source": "none", "detail": None, "note": None,
           "theoretical_gbs": dmi["gbs"] if dmi else None}
    if cache and (cache.get("cpu_model") != host.get("cpu_model") or cache.get("mem_kib") != host.get("mem_kib")):
        out["note"] = (f"bandwidth cache from another host ({cache.get('cpu_model')}, "
                       f"{cache.get('mem_kib')} kB) ignored: rerun doctor --perf --bandwidth")
        cache = None
    if given:
        out.update(gbs=float(given), source="given", detail="--dram-gbs")
    elif cache and cache.get("triad_gbs"):
        out.update(gbs=float(cache["triad_gbs"]), source="measured",
                   detail=f"STREAM triad, {cache.get('threads')} threads, {cache.get('measured_at')} "
                          f"(doctor --perf --bandwidth)")
    elif dmi:
        out.update(gbs=dmi["gbs"], source="theoretical", detail=dmi["detail"])
    else:
        out["note"] = out["note"] or ("no DRAM bandwidth: run 'k3d-gpu doctor --perf --bandwidth', "
                                      "or sudo for SMBIOS, or pass --dram-gbs")
    return out


def source_rank(model_id):
    if not model_id or model_id.startswith(("/", ".", "~")) or "/" not in model_id:
        return None, "local path: no source rank"
    org = model_id.split("/")[0].lower()
    for rank, orgs, text in SOURCE_RANKS:
        if org in orgs:
            return rank, text
    return 4, "third party"


# ------------------------------------------------------------------ predict
def _engines(w, device, tp=False):
    allowed = ARCH_ENGINES.get(w.get("arch"))
    out = []
    for e in ENGINES:
        if e["container"] != w["container"] or device not in e["devices"] or (tp and not e["tp"]):
            continue
        if e["formats"] is not None and w["family"] not in e["formats"] and w["format"] not in e["formats"]:
            continue
        if allowed and e["name"] not in allowed:
            continue
        out.append(e["name"])
    return out


def predict(w, host, dram, opts):
    hop = float(opts.get("hop_us") or HOP_US) * 1e-6
    want, eng = opts.get("placement") or "auto", opts.get("engine")
    gpus = [g for g in host["gpus"] if g["vram_bytes"]]
    if opts.get("gpu_gbs"):
        gpus = [dict(g, bw_gbs=float(opts["gpu_gbs"]), bw_source="given: --gpu-gbs") for g in gpus]
    ram = (host.get("mem_kib") or 0) * 1024 * FIT_FRAC
    rows, skipped = [], []
    dbw = dram["gbs"] * 1e9 if dram["gbs"] else None

    def add(placement, device, devices, terms, tp=False, fits=True, why=None, hops=0):
        if want != "auto" and not placement.startswith(want):
            return
        names = _engines(w, device, tp)
        if eng:
            names = [n for n in names if n == eng]
        if not names:
            skipped.append({"placement": placement, "reason": f"no engine for {w['container']} "
                            f"{w['format']} on {device}" + (f" with --engine {eng}" if eng else "")})
            return
        if not fits:
            skipped.append({"placement": placement, "reason": why})
            return
        t = None if any(bw is None for _, bw, _ in terms) else sum(b / bw for _, bw, b in terms)
        if t is not None:
            t += hops * hop
        rows.append({"placement": placement, "engine": names[0], "engines": names, "devices": devices,
                     "terms": [{"memory": m, "gbs": round(bw / 1e9, 1) if bw else None, "bytes": b}
                               for m, bw, b in terms],
                     "hops": hops, "hop_us": round(hop * 1e6, 3),
                     "decode_tok_s": round(1 / t, 1) if t else None})

    read = w["read_bytes"]
    add("cpu", "cpu", "CPU", [("dram", dbw, read)], fits=w["total_bytes"] <= ram,
        why=f"{w['total_bytes'] / 1e9:.1f} GB of weights over {FIT_FRAC:.0%} of RAM")
    if gpus:
        g = min(gpus, key=lambda x: (x["vram_bytes"], x["bw_gbs"] or 0))
        vram, gbw = g["vram_bytes"] * FIT_FRAC, g["bw_gbs"] * 1e9 if g["bw_gbs"] else None
        n_want = opts.get("gpus")
        if not n_want or n_want == 1:
            add("gpu", "gpu", f"1x {g['name']}", [("vram", gbw, read)], fits=w["total_bytes"] <= vram,
                why=f"{w['total_bytes'] / 1e9:.1f} GB of weights over {FIT_FRAC:.0%} of one "
                    f"{g['name']}'s {g['vram_bytes'] / 2**30:.0f} GiB")
        for n in ([n_want] if n_want and n_want > 1 else [2, 4, 8]):
            if n > len(gpus):
                if n_want:
                    skipped.append({"placement": f"gpu-tp{n}", "reason": f"{len(gpus)} GPU(s) on this host"})
                break
            if not n_want and w["total_bytes"] <= vram:
                break                                   # one card holds it: no TP row
            if w["total_bytes"] / n <= vram or n_want:
                add(f"gpu-tp{n}", "gpu", f"{n}x {g['name']} (tensor parallel)",
                    [("vram", gbw * n if gbw else None, read)], tp=True,
                    fits=w["total_bytes"] / n <= vram,
                    why=f"{w['total_bytes'] / n / 1e9:.1f} GB per card over {FIT_FRAC:.0%} of VRAM",
                    hops=2 * w["layers"])
                break
        if w["moe"]:
            add("hybrid", "hybrid", f"experts on CPU, rest on 1x {g['name']}",
                [("dram", dbw, w["experts_active_bytes"]), ("vram", gbw, w["dense_bytes"])],
                fits=w["dense_bytes"] <= vram and w["experts_total_bytes"] <= ram,
                why="dense weights over VRAM or experts over RAM", hops=2 * w["layers"])
    elif want in ("gpu", "hybrid"):
        skipped.append({"placement": want, "reason": "no Intel GPU with known VRAM on this host"})
    pick = max(rows, key=lambda r: r["decode_tok_s"] or -1) if rows else None
    return {"rows": rows, "skipped": skipped,
            "pick": {"placement": pick["placement"], "engine": pick["engine"],
                     "decode_tok_s": pick["decode_tok_s"]} if pick else None,
            "bandwidth": {"dram": dram, "gpu": [{"pci": g["pci"], "name": g["name"],
                                                 "vram_gib": round(g["vram_bytes"] / 2**30, 1),
                                                 "vram_source": g["vram_source"], "gbs": g["bw_gbs"],
                                                 "source": g["bw_source"]} for g in gpus]}}


ASSUMPTIONS = [
    "ceiling = bandwidth / weight bytes read per token: no compute, KV-cache, activation or kernel-efficiency term",
    "experts: top_k / num_experts of the expert bytes per token (uniform routing)",
    "tensor parallel and hybrid add 2 hops per layer x the hop latency (default 30 us, assumed)",
    "speculative decoding (MTP, EAGLE) is not modelled: these are per-forward ceilings",
    "vision/audio towers and MTP heads are not on the decode path and are not counted",
]


def gb(b):
    return f"{b / 1e9:.2f} GB"


def render(doc):
    w, m, bw = doc["weights"], doc["model"], doc["bandwidth"]
    lines = [f"{m['id'] or m['path']}{'@' + m['revision'] if m['revision'] else ''} ({w['arch']}): {doc['status']}, {doc['status_note']}"]
    if m["source_rank"] is not None:
        lines.append(f"  source:  rank {m['source_rank']} ({m['source_rank_text']})")
    kind = (f"MoE, {w['num_experts']} experts, {w['top_k']} per token" if w["moe"] else "dense")
    lines.append(f"  model:   {kind}, {w['layers']} layers" +
                 (f"; {w['params_total'] / 1e9:.2f}B parameters, {w['params_active'] / 1e9:.2f}B read per token"
                  if "params_total" in w else ""))
    lines.append(f"  weights: {w['format']} ({w['source']}): {gb(w['total_bytes'])} in total, "
                 f"{gb(w['read_bytes'])} read per token = dense {gb(w['dense_bytes'])}"
                 + (f" + experts {gb(w['experts_active_bytes'])} of {gb(w['experts_total_bytes'])}" if w["moe"] else ""))
    lines.append(f"           {w['format_source']}")
    d = bw["dram"]
    if d["gbs"]:
        lines.append(f"  dram:    {d['gbs']} GB/s {d['source']} ({d['detail']})" +
                     (f"; theoretical {d['theoretical_gbs']} GB/s" if d["source"] != "theoretical" and d["theoretical_gbs"] else ""))
    else:
        lines.append("  dram:    unknown")
    if d["note"]:
        lines.append(f"  note:    {d['note']}")
    for g in bw["gpu"]:
        lines.append(f"  gpu:     {g['pci']} {g['name']}: {g['vram_gib']} GiB ({g['vram_source']}), "
                     f"{g['gbs']} GB/s {g['source']}")
    lines.append("  decode ceiling, batch 1:")
    for r in doc["rows"]:
        tok = f"{r['decode_tok_s']} tok/s" if r["decode_tok_s"] else "unknown (no bandwidth)"
        terms = " + ".join(f"{gb(t['bytes'])} at {t['gbs'] or '?'} GB/s {t['memory']}" for t in r["terms"])
        if r["hops"]:
            terms += f" + {r['hops']} hops x {r['hop_us']:g} us"
        lines.append(f"    {r['placement']:<9} {r['engine']:<16} <= {tok}  ({terms}; {r['devices']})")
    for s in doc["skipped"]:
        lines.append(f"    {s['placement']:<9} skipped: {s['reason']}")
    p = doc["pick"]
    lines.append(f"  pick:    {p['placement']} on {p['engine']}, ceiling {p['decode_tok_s']} tok/s"
                 if p else "  pick:    none (no placement fits with these options)")
    for a in doc["assumptions"]:
        lines.append(f"  assume:  {a}")
    return "\n".join(lines)


def load_cache(path):
    if not path:
        return None
    try:
        with open(path) as f:
            c = json.load(f)
    except (OSError, ValueError):
        return None
    return c if isinstance(c, dict) and c.get("schema") == BANDWIDTH_SCHEMA else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="k3d-gpu plan --predict --model")
    ap.add_argument("--config")
    ap.add_argument("--gguf")
    ap.add_argument("--format")
    ap.add_argument("--placement", choices=("auto", "cpu", "gpu", "hybrid"), default="auto")
    ap.add_argument("--engine", choices=[e["name"] for e in ENGINES])
    ap.add_argument("--gpus", type=int)
    ap.add_argument("--sysroot", default="")
    ap.add_argument("--bandwidth-cache")
    ap.add_argument("--dram-gbs", type=float)
    ap.add_argument("--gpu-gbs", type=float)
    ap.add_argument("--hop-us", type=float)
    ap.add_argument("--model-id")
    ap.add_argument("--revision")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        for name, v in (("--dram-gbs", a.dram_gbs), ("--gpu-gbs", a.gpu_gbs), ("--gpus", a.gpus),
                        ("--hop-us", a.hop_us)):
            if v is not None and v <= 0:
                raise ValueError(f"{name} must be positive")
        if not a.config and not a.gguf:
            raise ValueError("pass --config FILE|DIR or --gguf FILE")
        if a.gguf:
            w = gguf_weights(gguf_read(a.gguf))
            if a.format:
                raise ValueError("--format does not apply to a GGUF file (its tensor table has the bytes)")
            path = a.gguf
        else:
            path = a.config
            cfile = os.path.join(path, "config.json") if os.path.isdir(path) else path
            try:
                with open(cfile) as f:
                    cfg = json.load(f)
            except (OSError, ValueError) as e:
                raise ValueError(f"cannot read {cfile}: {e}")
            fmt = a.format or format_from_dir(os.path.dirname(os.path.abspath(cfile)), cfg)
            w = config_weights(params_from_config(cfg), fmt)
        host = host_from_sysroot(a.sysroot)
        dram = dram_bandwidth(host, load_cache(a.bandwidth_cache), a.dram_gbs, dmi_theoretical(a.sysroot))
        res = predict(w, host, dram, {"placement": a.placement, "engine": a.engine, "gpus": a.gpus,
                                      "gpu_gbs": a.gpu_gbs, "hop_us": a.hop_us})
    except ValueError as e:
        print(f"moe_predict: {e}", file=sys.stderr)
        return 2
    rank, rank_text = source_rank(a.model_id)
    doc = {"schema": SCHEMA, "status": STATUS, "status_note": STATUS_NOTE,
           "model": {"id": a.model_id, "revision": a.revision, "path": path,
                     "source_rank": rank, "source_rank_text": rank_text},
           "weights": w,
           "host": {"cpu_model": host["cpu_model"], "amx": host["amx"], "physical_cores": host["physical_cores"],
                    "mem_gib": round((host["mem_kib"] or 0) / 2**20, 1)},
           "options": {"placement": a.placement, "engine": a.engine, "gpus": a.gpus,
                       "hop_us": a.hop_us or HOP_US},
           "assumptions": ASSUMPTIONS}
    doc.update(res)
    print(json.dumps(doc, indent=1, sort_keys=True) if a.json else render(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
