#!/usr/bin/env python3
"""Roofline / capacity model: concurrent agents per Intel client box.

Host: i9-13900T (8P+16E Raptor Lake), 4x32 GB DDR5 ECC UDIMM at 2 DIMMs per
channel, configured 3600 MT/s (dmidecode, 2026-10-16), UHD 770 (32 EU, 1.65 GHz).
Python stdlib only. No live measurement is made here; every live claim is a
pre-registered probe (`probes` subcommand).

Subcommands
  hw          host roofs (DRAM, VNNI, DP4a) with sources
  models      per-token weight bytes, MACs, KV bytes for the candidate models
  calibrate   fit both step-time hypotheses to the measured H2 points, residuals
  curve       aggregate decode tok/s vs batch B, binding constraint, knee
  box         CPU + iGPU running together on shared DRAM
  agents      agents per box at a p95 turn-latency SLO (discrete-event sim)
  probes      pre-registered live probes with predicted values
  selftest    invariant checks (shapes vs published counts, calibration, sim monotonicity)

Two step-time hypotheses (calibrate decides between them only with live data):
  roofline  step = max(T_mem, T_cmp) + s0*B      (compute overlaps memory)
  additive  step = T_mem + T_cmp                 (compute serial with memory)
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys

# ---------------------------------------------------------------- host facts
HOST = {
    # dmidecode -t memory: 4 DIMMs, Controller0/1, "Configured Memory Speed: 3600 MT/s",
    # Data Width 64 bits per channel. [verified, read on host 2026-10-16]
    "dram_mts": 3600e6,
    "channels": 2,
    "bytes_per_transfer": 8,
    # VPDPBUSD ymm (AVX-VNNI): reciprocal throughput 0.50 on Alder Lake-P (p01),
    # 2.00 on Alder Lake-E (Gracemont). uops.info VPDPBUSD_VEX_YMM_YMM_YMM.
    # Raptor Cove / Raptor Lake E-core assumed identical to ADL-P / ADL-E. [verified for ADL]
    # One ymm VPDPBUSD = 8 dword lanes x 4 int8 MACs = 32 MACs.
    "p_cores": 8,
    "e_cores": 16,
    "p_mac_per_clk": 32 * 2,  # 64
    "e_mac_per_clk": 32 * 0.5,  # 16
    # Sustained all-core clocks under PL1=PL2=117 W are NOT measured. Assumptions.
    "p_ghz": 4.0,
    "e_ghz": 3.0,
    # UHD 770: 32 EU (Xe-LP), gt_max_freq_mhz=1650 (sysfs, verified).
    # Xe-LP DP4a: 32 int8 MAC/clk/EU (oneAPI GPU guide lists INT8 32 ops/EU/clk;
    # Xe-HPG intro counts 64 ops = MAC+add). [verified]
    "igpu_eu": 32,
    "igpu_ghz": 1.65,
    "igpu_mac_per_eu_clk": 32,
    "llc_mb": 36,
}


def dram_peak():
    h = HOST
    return h["dram_mts"] * h["channels"] * h["bytes_per_transfer"]  # 57.6e9 B/s


def vnni_peak(cores="P"):
    h = HOST
    p = h["p_cores"] * h["p_mac_per_clk"] * h["p_ghz"] * 1e9
    e = h["e_cores"] * h["e_mac_per_clk"] * h["e_ghz"] * 1e9
    return {"P": p, "E": e, "PE": p + e}[cores]


def igpu_peak():
    h = HOST
    return h["igpu_eu"] * h["igpu_mac_per_eu_clk"] * h["igpu_ghz"] * 1e9


# ---------------------------------------------------------------- models
# Shapes from Hugging Face config.json (fetched 2026-10-16). OV file sizes from the
# HF tree API of the OpenVINO/*-int4-ov repos (openvino_model.bin bytes).
INT4_GS128 = 0.5 + 2 / 128  # int4 sym, fp16 scale per 128 (NNCF INT4_SYM gs128) = 0.5156 B
MODELS = {
    "qwen3-4b": dict(hidden=2560, inter=9728, layers=36, heads=32, kv_heads=8, head_dim=128,
                     vocab=151936, tied=True, experts=0, topk=0, moe_inter=0, window=None,
                     ov_bytes=2263625445, ov_repo="OpenVINO/Qwen3-4B-int4-ov"),
    "qwen3-1.7b": dict(hidden=2048, inter=6144, layers=28, heads=16, kv_heads=8, head_dim=128,
                       vocab=151936, tied=True, experts=0, topk=0, moe_inter=0, window=None,
                       ov_bytes=1181738708, ov_repo="OpenVINO/Qwen3-1.7B-int4-ov"),
    "qwen3-30b-a3b": dict(hidden=2048, inter=6144, layers=48, heads=32, kv_heads=4, head_dim=128,
                          vocab=151936, tied=False, experts=128, topk=8, moe_inter=768, window=None,
                          ov_bytes=16314043317, ov_repo="OpenVINO/Qwen3-30B-A3B-int4-ov"),
    # gpt-oss: attention q/o use heads*head_dim=4096; experts are SwiGLU with
    # gate_up [hidden, 2*inter] + down [inter, hidden]; half the layers are
    # sliding-window (128) attention.
    "gpt-oss-20b": dict(hidden=2880, inter=2880, layers=24, heads=64, kv_heads=8, head_dim=64,
                        vocab=201088, tied=False, experts=32, topk=4, moe_inter=2880, window=128,
                        sliding_layers=12, ov_bytes=12576651045, ov_repo="OpenVINO/gpt-oss-20b-int4-ov"),
}


def shapes(name):
    m = MODELS[name]
    h, L = m["hidden"], m["layers"]
    qd = m["heads"] * m["head_dim"]
    kvd = m["kv_heads"] * m["head_dim"]
    attn = h * qd * 2 + h * kvd * 2  # q,o + k,v
    embed = m["vocab"] * h
    if m["experts"]:
        expert = 3 * h * m["moe_inter"]
        router = h * m["experts"]
        mlp_total, mlp_active = expert * m["experts"] + router, expert * m["topk"] + router
    else:
        expert = 0
        mlp_total = mlp_active = 3 * h * m["inter"]
    total = L * (attn + mlp_total) + embed * (1 if m["tied"] else 2)
    active = L * (attn + mlp_active) + embed  # lm_head is streamed and multiplied every token
    # Weight bytes calibrated to the OV file: experts int4 gs128, everything else
    # scaled so the total matches openvino_model.bin.
    expert_bytes = L * expert * m["experts"] * INT4_GS128 if m["experts"] else 0.0
    other_bytes = m["ov_bytes"] - expert_bytes
    # Untied models also store the input embedding; one row is gathered per token.
    embed_store = 0.0 if m["tied"] else embed * 1.0  # NNCF keeps embeddings int8 (backup mode)
    shared_streamed = other_bytes - embed_store
    return dict(attn_per_layer=attn, expert_params=expert, total_params=total,
                active_params=active, expert_bytes=expert_bytes, shared_bytes=shared_streamed,
                embed=embed)


def kv_elems_per_token(name, ctx):
    """K+V elements read per decoded token at context length ctx (sum over layers)."""
    m = MODELS[name]
    per_layer = 2 * m["kv_heads"] * m["head_dim"]
    if m.get("window"):
        s = m["sliding_layers"]
        return per_layer * (s * min(ctx, m["window"]) + (m["layers"] - s) * ctx)
    return per_layer * m["layers"] * ctx


def kv_elems_stored_per_token(name):
    m = MODELS[name]
    return 2 * m["kv_heads"] * m["head_dim"] * m["layers"]


def attn_macs(name, ctx):
    """QK^T + PV MACs for one query token at context ctx."""
    m = MODELS[name]
    per_layer = 2 * m["heads"] * m["head_dim"]
    if m.get("window"):
        s = m["sliding_layers"]
        return per_layer * (s * min(ctx, m["window"]) + (m["layers"] - s) * ctx)
    return per_layer * m["layers"] * ctx


def unique_experts(E, k, tokens):
    """Expected distinct experts per layer touched by `tokens` tokens, uniform routing.
    Uniform is the pessimistic case for bytes; skewed real routing touches fewer."""
    if E == 0:
        return 0.0
    return E * (1 - (1 - k / E) ** tokens)


def weight_bytes_step(name, tokens):
    m = MODELS[name]
    s = shapes(name)
    if not m["experts"]:
        return s["shared_bytes"]
    frac = unique_experts(m["experts"], m["topk"], max(tokens, 1)) / m["experts"]
    return s["shared_bytes"] + s["expert_bytes"] * frac


# ---------------------------------------------------------------- engines
# KV bytes per element incl. per-token-per-head scale+zero-point (fp16 each):
# CPU default u8 (OpenVINO >= 2024.6 release notes; intel_cpu config.h:79-81 per
# docs/agent-prefix-reuse.md F9). GPU: u4 for a PagedAttention model with 4-bit
# weights (intel_gpu execution_config.cpp:286-301, same doc). [verified via that doc]
def kv_bytes_per_elem(kind, head_dim):
    if kind == "u8":
        return 1.0 + 4.0 / head_dim
    if kind == "u4":
        return 0.5 + 4.0 / head_dim
    if kind == "f16":
        return 2.0
    raise ValueError(kind)


# Measured H2 points (Qwen3-4B int4, OVMS 2026.4.1, bench/llm_load.py defaults:
# ~43-token prompt, max_tokens 128). Mean decode context ~ 43 + 64.
MEASURED = {
    "cpu": {"c1": 15.7, "c4_agg": 56.0, "prefill_tok_s": 172.0, "ttft_s": 0.25, "kv": "u8"},
    "igpu": {"c1": 9.9, "c4_agg": 30.0, "prefill_tok_s": 66.0, "kv": "u4"},
}
H2_CTX = 107
H2_PROMPT = 43


class Engine:
    def __init__(self, name, bw, c_mac, s0=0.0, t0=0.0, kv="u8", mode="roofline"):
        self.name, self.bw, self.c_mac, self.s0, self.t0 = name, bw, c_mac, s0, t0
        self.kv, self.mode = kv, mode
        self.shared_prefix_once = 0  # tokens of a shared prefix whose KV is read once per step

    def step(self, model, dec_ctx, prefill=(), detail=False):
        """dec_ctx: contexts of decoding sequences. prefill: (n_tokens, ctx_before) chunks."""
        m = MODELS[model]
        s = shapes(model)
        tokens = len(dec_ctx) + sum(n for n, _ in prefill)
        if tokens == 0:
            return (0.0, "idle") if detail else 0.0
        bkv = kv_bytes_per_elem(self.kv, m["head_dim"])
        sp = self.shared_prefix_once
        kv_read = 0.0
        for c in dec_ctx:
            kv_read += kv_elems_per_token(model, c - min(sp, c)) * bkv
        if sp and dec_ctx:
            kv_read += kv_elems_per_token(model, min(sp, max(dec_ctx))) * bkv
        for n, c0 in prefill:
            kv_read += kv_elems_per_token(model, c0) * bkv  # past KV read once per chunk
        wbytes = weight_bytes_step(model, tokens)
        tm = (wbytes + kv_read) / self.bw
        macs = tokens * s["active_params"]
        macs += sum(attn_macs(model, c) for c in dec_ctx)
        macs += sum(n * attn_macs(model, c0 + n / 2) for n, c0 in prefill)
        tc = macs / self.c_mac
        if self.mode == "roofline":
            t = max(tm, tc) + self.t0 + self.s0 * len(dec_ctx)
            bind = "compute" if tc > tm else ("kv-bw" if kv_read > wbytes else "weight-bw")
        else:
            t = tm + tc + self.t0
            bind = ("compute" if tc > tm else ("kv-bw" if kv_read > wbytes else "weight-bw"))
        if detail:
            return t, bind, dict(tm=tm, tc=tc, wbytes=wbytes, kv_read=kv_read)
        return t


def calibrate(dev="cpu", mode="roofline", model="qwen3-4b", c_mac=None):
    """Fit an engine to the measured H2 points. Returns (engine, report)."""
    meas = MEASURED[dev]
    m = MODELS[model]
    s = shapes(model)
    bkv = kv_bytes_per_elem(meas["kv"], m["head_dim"])
    W = weight_bytes_step(model, 1)
    kv1 = kv_elems_per_token(model, H2_CTX) * bkv
    M1 = s["active_params"] + attn_macs(model, H2_CTX)
    step1 = 1 / meas["c1"]
    step4 = 4 / meas["c4_agg"]
    rep = {"device": dev, "mode": mode, "step1_ms": step1 * 1e3, "step4_ms": step4 * 1e3}
    if mode == "additive":
        # step1 = (W+kv1)/bw + M1/C ; step4 = (W+4kv1)/bw + 4*M1/C   -> linear in x=1/bw, y=1/C
        a1, b1 = W + kv1, M1
        a4, b4 = W + 4 * kv1, 4 * M1
        det = a1 * b4 - a4 * b1
        x = (step1 * b4 - step4 * b1) / det
        y = (a1 * step4 - a4 * step1) / det
        bw, C = 1 / x, 1 / y
        eng = Engine(dev, bw, C, kv=meas["kv"], mode="additive")
        rep.update(bw_GBs=bw / 1e9, c_TMACs=C / 1e12,
                   implied_prefill_tok_s=C / (s["active_params"] + attn_macs(model, H2_PROMPT / 2)),
                   fitted_params=2, free_residual="none (2 params, 2 points)")
    else:
        # bw from c=1 (compute assumed hidden under memory at B=1), s0 from c=4.
        if c_mac is None:
            if dev == "cpu":
                # Lower bound from TTFT: TTFT = W/bw + n/C (+ HTTP/tokenize overhead, ignored).
                bw0 = (W + kv1) / step1
                c_tok = H2_PROMPT / (meas["ttft_s"] - W / bw0)
                c_mac = c_tok * (s["active_params"] + attn_macs(model, H2_PROMPT / 2))
            else:
                c_mac = meas["prefill_tok_s"] * (s["active_params"] + attn_macs(model, H2_PROMPT / 2))
        # Physics-only out-of-sample check: bw from c=1, s0 = 0, predict c=4.
        bw0 = (W + kv1) / step1
        pure = Engine(dev, bw0, c_mac, kv=meas["kv"]).step(model, [H2_CTX] * 4)
        rep["c4_physics_only_pred_agg"] = 4 / pure
        rep["c4_physics_only_residual_pct"] = 100 * (meas["c4_agg"] - 4 / pure) / (4 / pure)
        # Fit: step1 = (W+kv1)/bw + s0 ; step4 = (W+4kv1)/bw + 4*s0   (memory binds at B<=4)
        a1, a4 = W + kv1, W + 4 * kv1
        det = a1 * 4 - a4 * 1
        x = (step1 * 4 - step4 * 1) / det
        s0 = (a1 * step4 - a4 * step1) / det
        bw = 1 / x
        eng = Engine(dev, bw, c_mac, s0=s0, kv=meas["kv"], mode="roofline")
        rep.update(bw_GBs=bw / 1e9, c_TMACs=c_mac / 1e12, s0_ms=s0 * 1e3,
                   c_source="TTFT-derived lower bound" if dev == "cpu" else "measured prefill rate")
    peak = dram_peak()
    rep["bw_frac_of_dram_peak"] = rep["bw_GBs"] * 1e9 / peak
    cpk = vnni_peak("P") if dev == "cpu" else igpu_peak()
    rep["c_frac_of_int8_peak"] = rep["c_TMACs"] * 1e12 / cpk
    rep["c1_pred"] = 1 / eng.step(model, [H2_CTX])
    rep["c4_pred_agg"] = 4 / eng.step(model, [H2_CTX] * 4)
    rep["c1_residual_pct"] = 100 * (meas["c1"] - rep["c1_pred"]) / rep["c1_pred"]
    rep["c4_residual_pct"] = 100 * (meas["c4_agg"] - rep["c4_pred_agg"]) / rep["c4_pred_agg"]
    return eng, rep


def transfer(eng, model):
    """Same engine physics, different model (bw, C, s0 unchanged)."""
    e = Engine(eng.name, eng.bw, eng.c_mac, eng.s0, eng.t0, eng.kv, eng.mode)
    e.shared_prefix_once = eng.shared_prefix_once
    return e


def curve(eng, model, ctx, Bs):
    rows = []
    for B in Bs:
        t, bind, d = eng.step(model, [ctx] * B, detail=True)
        rows.append(dict(B=B, step_ms=t * 1e3, agg_tok_s=B / t, per_stream_tok_s=1 / t, binding=bind,
                         tm_ms=d["tm"] * 1e3, tc_ms=d["tc"] * 1e3, kv_GB=d["kv_read"] / 1e9))
    return rows


def knee(eng, model, ctx, bmax=1024):
    """Smallest B where compute time >= memory time (None if never within bmax)."""
    for B in range(1, bmax + 1):
        _, _, d = eng.step(model, [ctx] * B, detail=True)
        if d["tc"] >= d["tm"]:
            return B
    return None


def box_step(engines, loads, bw_sys):
    """engines: list of (Engine, model, dec_ctx). Shared DRAM: if summed demand
    exceeds bw_sys, every engine's memory time stretches by demand/bw_sys."""
    alone = []
    for eng, model, ctxs in engines:
        t, bind, d = eng.step(model, ctxs, detail=True)
        alone.append((t, d))
    demand = sum((d["wbytes"] + d["kv_read"]) / t for t, d in alone if t > 0)
    f = max(1.0, demand / bw_sys)
    out = []
    for (eng, model, ctxs), (t, d) in zip(engines, alone):
        tm = d["tm"] * f
        if eng.mode == "roofline":
            t2 = max(tm, d["tc"]) + eng.t0 + eng.s0 * len(ctxs)
        else:
            t2 = tm + d["tc"] + eng.t0
        out.append(dict(engine=eng.name, B=len(ctxs), agg_tok_s=len(ctxs) / t2 if t2 else 0.0))
    return out, demand, f


# ---------------------------------------------------------------- agent traces
# Token counts = words x 1.3 from bench/agent_trace_gen.py (claude/invent-agent-trace-bench).
TRACES = {
    # system prompt (shared across sessions) incl. tool schemas, turns range,
    # per-turn appended tokens range (tool result), decode tokens per turn.
    "support": dict(sys=3400, turns=(3, 6), first=60, delta=(52, 156), decode=96),
    "coding": dict(sys=1350, turns=(8, 12), first=80, delta=(520, 2080), decode=192),
    "fanout": dict(sys=2350, turns=(2, 3), first=80, delta=(150, 600), decode=128),
}


def simulate(eng, model, trace, n_agents, tool_wait, horizon=900.0, warmup=120.0, seed=1,
             budget=256, max_seqs=256, cache_tokens=None, prefix_cache=True, wait_dist="exp"):
    """Closed-loop discrete-event sim of continuous batching with dynamic split fuse.
    Each agent loops sessions forever: turn = prefill(delta) + decode(D); then a tool
    wait; after the last turn a new session starts (system prompt is shared).
    cache_tokens: KV pool size in tokens for prefix reuse (None = OVMS cache_size 0:
    the pool's high-water mark of live demand, idle sessions evicted LRU into it).
    Returns turn latency stats."""
    rng = random.Random(seed)
    tr = TRACES[trace]
    sysn = tr["sys"]

    def new_session(a):
        a["turns_left"] = rng.randint(*tr["turns"])
        a["ctx"] = sysn
        a["cached"] = sysn  # shared system prompt assumed resident (hot, used by all)
        a["delta"] = tr["first"]

    def wait():
        if wait_dist == "fixed":
            return tool_wait
        return rng.expovariate(1 / tool_wait) if tool_wait > 0 else 0.0

    agents = []
    for i in range(n_agents):
        a = {"id": i, "state": "wait", "ready": rng.uniform(0, max(tool_wait, 1.0) * 2), "last_use": 0.0}
        new_session(a)
        agents.append(a)
    t = 0.0
    lat = []
    pool_hw = 0  # high-water mark of live KV tokens (dynamic pool)
    tokens_out = 0
    busy = 0.0
    while t < horizon:
        for a in agents:
            if a["state"] == "wait" and a["ready"] <= t:
                # start a turn: decide prefill length from the prefix cache
                if not prefix_cache or a["cached"] < a["ctx"]:
                    reuse = sysn if prefix_cache else 0
                    a["pf"] = a["ctx"] - reuse + a["delta"]
                    a["pf_ctx0"] = reuse
                else:
                    a["pf"] = a["delta"]
                    a["pf_ctx0"] = a["ctx"]
                a["state"] = "prefill"
                a["arrive"] = a["ready"]
                a["dec_left"] = tr["decode"]
        live = [a for a in agents if a["state"] in ("prefill", "decode")]
        if not live:
            nxt = min(a["ready"] for a in agents)
            t = max(t, nxt)
            continue
        dec = [a for a in live if a["state"] == "decode"][:max_seqs]
        left = budget - len(dec)
        chunks = []
        for a in sorted((a for a in live if a["state"] == "prefill"), key=lambda a: a["arrive"]):
            if left <= 0:
                break
            n = min(left, a["pf"])
            chunks.append((a, n))
            left -= n
        dt = eng.step(model, [a["ctx"] for a in dec], [(n, a["pf_ctx0"]) for a, n in chunks])
        t += dt
        if t > warmup:
            busy += dt
        for a in dec:
            a["ctx"] += 1
            a["dec_left"] -= 1
            if t > warmup:
                tokens_out += 1
            if a["dec_left"] <= 0:
                if a["arrive"] > warmup:
                    lat.append(t - a["arrive"])
                a["turns_left"] -= 1
                a["last_use"] = t
                a["cached"] = a["ctx"]
                if a["turns_left"] <= 0:
                    new_session(a)
                else:
                    a["delta"] = rng.randint(*tr["delta"])
                a["state"] = "wait"
                a["ready"] = t + wait()
        for a, n in chunks:
            a["pf"] -= n
            a["pf_ctx0"] += n
            if a["pf"] <= 0:
                a["ctx"] = a["pf_ctx0"]
                a["state"] = "decode"
        # prefix-cache eviction (LRU over idle sessions' private KV)
        if prefix_cache:
            live_tok = sum(a["ctx"] for a in agents if a["state"] in ("prefill", "decode"))
            pool_hw = max(pool_hw, live_tok)
            cap = cache_tokens if cache_tokens is not None else pool_hw
            idle = sorted((a for a in agents if a["state"] == "wait" and a["cached"] > sysn),
                          key=lambda a: a["last_use"])
            used = live_tok + sysn + sum(a["cached"] - sysn for a in idle)
            for a in idle:
                if used <= cap:
                    break
                used -= a["cached"] - sysn
                a["cached"] = sysn
    lat.sort()
    span = max(horizon - warmup, 1e-9)
    if not lat:
        return dict(n=n_agents, turns=0, p50=float("inf"), p95=float("inf"), tok_s=0.0, util=busy / span)
    q = lambda p: lat[min(len(lat) - 1, int(p * len(lat)))]
    return dict(n=n_agents, turns=len(lat), p50=q(0.5), p95=q(0.95), tok_s=tokens_out / span,
                util=busy / span)


def agents_at_slo(eng, model, trace, tool_wait, slo, nmax=512, **kw):
    """Largest N with p95 turn latency <= slo (doubling then bisection)."""
    ok = lambda n: simulate(eng, model, trace, n, tool_wait, **kw)["p95"] <= slo
    if not ok(1):
        return 0
    lo, hi = 1, 2
    while hi <= nmax and ok(hi):
        lo, hi = hi, hi * 2
    if hi > nmax:
        return lo
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


# ---------------------------------------------------------------- CLI
def fmt_table(rows, keys):
    out = ["  ".join(f"{k:>12}" for k in keys)]
    for r in rows:
        out.append("  ".join(f"{r[k]:>12.3g}" if isinstance(r[k], float) else f"{str(r[k]):>12}" for k in keys))
    return "\n".join(out)


def build(dev, mode, model):
    eng, _ = calibrate(dev, mode)
    return transfer(eng, model) if model != "qwen3-4b" else eng


def cmd_hw(a):
    print(json.dumps({
        "dram_peak_GBs": dram_peak() / 1e9,
        "dram_note": "2 channels x 8 B x 3600 MT/s (2 DIMMs/channel, DDR5-4800 parts run at 3600)",
        "vnni_P_TMACs": vnni_peak("P") / 1e12, "vnni_E_TMACs": vnni_peak("E") / 1e12,
        "vnni_note": "VPDPBUSD ymm: ADL-P 0.5 CPI, ADL-E 2.0 CPI (uops.info); clocks P 4.0 / E 3.0 GHz assumed",
        "igpu_dp4a_TMACs": igpu_peak() / 1e12,
        "igpu_note": "32 EU x 32 int8 MAC/clk x 1.65 GHz",
        "llc_MB": HOST["llc_mb"],
    }, indent=2))


def cmd_models(a):
    rows = []
    for n in MODELS:
        s = shapes(n)
        m = MODELS[n]
        rows.append(dict(
            model=n, total_B=s["total_params"] / 1e9, active_B=s["active_params"] / 1e9,
            W1_GB=weight_bytes_step(n, 1) / 1e9, W8_GB=weight_bytes_step(n, 8) / 1e9,
            W32_GB=weight_bytes_step(n, 32) / 1e9, ov_GB=m["ov_bytes"] / 1e9,
            kv_u8_KB=kv_elems_stored_per_token(n) * kv_bytes_per_elem("u8", m["head_dim"]) / 1e3,
            kv_f16_KB=kv_elems_stored_per_token(n) * 2 / 1e3,
            ctx_kv_eq_W=weight_bytes_step(n, 1) / (kv_elems_per_token(n, 1_000_000) / 1_000_000
                                                   * kv_bytes_per_elem("u8", m["head_dim"])),
        ))
    print(fmt_table(rows, list(rows[0].keys())))
    print("\nW<B>_GB: weight bytes streamed per decode step with B tokens in the step "
          "(MoE: uniform routing, distinct experts grow with B).\nctx_kv_eq_W: context at which one "
          "sequence's u8 KV read equals the weight read (KV-bound beyond).")


def cmd_calibrate(a):
    out = []
    for dev in ("cpu", "igpu"):
        for mode in ("roofline", "additive"):
            _, rep = calibrate(dev, mode)
            out.append(rep)
    print(json.dumps(out, indent=2))


def cmd_curve(a):
    eng = build(a.device, a.mode, a.model)
    if a.shared_prefix:
        eng.shared_prefix_once = a.shared_prefix
    if a.kv:
        eng.kv = a.kv
    Bs = [int(x) for x in a.batches.split(",")]
    rows = curve(eng, a.model, a.ctx, Bs)
    print(f"# {a.device} {a.mode} {a.model} ctx={a.ctx} kv={eng.kv} bw={eng.bw/1e9:.1f} GB/s "
          f"C={eng.c_mac/1e12:.2f} TMAC/s s0={eng.s0*1e3:.2f} ms")
    print(fmt_table(rows, ["B", "step_ms", "agg_tok_s", "per_stream_tok_s", "binding", "tm_ms", "tc_ms", "kv_GB"]))
    k = knee(eng, a.model, a.ctx)
    best = max(curve(eng, a.model, a.ctx, range(1, 257)), key=lambda r: r["agg_tok_s"])
    print(f"knee (T_cmp >= T_mem): B={k}; max agg over B<=256: {best['agg_tok_s']:.1f} tok/s at B={best['B']} "
          f"({best['per_stream_tok_s']:.2f} tok/s/stream); headroom vs measured c=4 (56): "
          f"{best['agg_tok_s']/56:.2f}x")


def cmd_box(a):
    cpu = build("cpu", a.mode, a.model)
    gpu = build("igpu", a.mode, a.model)
    bw_sys = a.bw_sys * 1e9 if a.bw_sys else cpu.bw
    rows = []
    for bc, bg in [(1, 1), (4, 4), (8, 8), (16, 8), (32, 16)]:
        res, demand, f = box_step([(cpu, a.model, [a.ctx] * bc), (gpu, a.model, [a.ctx] * bg)], None, bw_sys)
        alone_c = bc / cpu.step(a.model, [a.ctx] * bc)
        alone_g = bg / gpu.step(a.model, [a.ctx] * bg)
        rows.append(dict(B_cpu=bc, B_igpu=bg, cpu_alone=alone_c, igpu_alone=alone_g,
                         cpu_shared=res[0]["agg_tok_s"], igpu_shared=res[1]["agg_tok_s"],
                         box_total=res[0]["agg_tok_s"] + res[1]["agg_tok_s"],
                         demand_GBs=demand / 1e9, stretch=f))
    print(f"# shared DRAM cap {bw_sys/1e9:.1f} GB/s, mode {a.mode}, ctx {a.ctx}")
    print(fmt_table(rows, list(rows[0].keys())))


def cmd_agents(a):
    eng = build(a.device, a.mode, a.model)
    if a.shared_prefix:
        eng.shared_prefix_once = TRACES[a.trace]["sys"]
    if a.kv:
        eng.kv = a.kv
    kw = dict(horizon=a.horizon, prefix_cache=not a.no_prefix_cache,
              cache_tokens=None if a.cache_gb is None else int(
                  a.cache_gb * 1e9 / (kv_elems_stored_per_token(a.model)
                                      * kv_bytes_per_elem(eng.kv, MODELS[a.model]["head_dim"]))))
    res = {}
    for w in [float(x) for x in a.tool_wait.split(",")]:
        for slo in [float(x) for x in a.slo.split(",")]:
            n = agents_at_slo(eng, a.model, a.trace, w, slo, nmax=a.nmax, **kw)
            st = simulate(eng, a.model, a.trace, max(n, 1), w, **kw)
            res[f"wait={w:g}s slo={slo:g}s"] = dict(agents=n, p50=round(st["p50"], 2), p95=round(st["p95"], 2),
                                                     tok_s=round(st["tok_s"], 1), util=round(st["util"], 2))
    print(json.dumps({"device": a.device, "mode": a.mode, "model": a.model, "trace": a.trace,
                      "prefix_cache": not a.no_prefix_cache, "cache_gb": a.cache_gb,
                      "shared_prefix_once": bool(a.shared_prefix), "kv": eng.kv, "results": res}, indent=2))


def cmd_probes(a):
    """Pre-registered live probes. Predictions printed for both hypotheses.
    Run serially, one at a time, on an otherwise quiet host (record idle W first)."""
    probes = [dict(probe="P0 DRAM triad 2 s (stream_probe.c, 16 threads P+E, then 8 P only)",
                   pred="P-only 33-41 GB/s; P+E within 10% of P-only if decode is DRAM-saturated "
                        "(H-sat); >= 44 GB/s refutes H-sat", peak_GBs=dram_peak() / 1e9)]
    for mode in ("roofline", "additive"):
        eng, _ = calibrate("cpu", mode)
        g, _ = calibrate("igpu", mode)
        for B in (8, 16, 32):
            t = eng.step("qwen3-4b", [H2_CTX] * B)
            probes.append(dict(probe=f"P1 cpu llm_load c={B} (H2 prompt, max_tokens 128, ignore_eos)",
                               hypothesis=mode, pred_agg=round(B / t, 1), pred_per_stream=round(1 / t, 2)))
        for B in (8, 16):
            t = g.step("qwen3-4b", [H2_CTX] * B)
            probes.append(dict(probe=f"P2 igpu llm_load c={B}", hypothesis=mode, pred_agg=round(B / t, 1),
                               pred_per_stream=round(1 / t, 2)))
        n = 2048
        tt = sum(eng.step("qwen3-4b", [], [(256, 256 * i)]) for i in range(n // 256))
        probes.append(dict(probe="P3 cpu one 2048-token prompt, max_tokens 1, c=1", hypothesis=mode,
                           pred_ttft_s=round(tt, 2), pred_prefill_tok_s=round(n / tt, 1)))
        for B in (1, 8):
            t = eng.step("qwen3-4b", [3500] * B)
            probes.append(dict(probe=f"P5 cpu c={B}, 3400-token distinct prompts (cache-bust), decode 128",
                               hypothesis=mode, pred_agg=round(B / t, 1), pred_per_stream=round(1 / t, 2),
                               note="KV-wall test: B=8 agg must stay <= ~60 tok/s (vs ~98 at short ctx)"))
        for cap in (eng.bw, 46e9):
            res, _, _ = box_step([(eng, "qwen3-4b", [H2_CTX] * 4), (g, "qwen3-4b", [H2_CTX] * 4)], None, cap)
            probes.append(dict(probe=f"P4 cpu c=4 + igpu c=4 concurrently (bw_sys {cap/1e9:.0f} GB/s)",
                               hypothesis=mode, pred_cpu=round(res[0]["agg_tok_s"], 1),
                               pred_igpu=round(res[1]["agg_tok_s"], 1),
                               pred_total=round(res[0]["agg_tok_s"] + res[1]["agg_tok_s"], 1),
                               naive_sum=86.0))
    probes.append(dict(probe="P6 agent_trace.py run support (scale 2), N in {4,8,16,24} closed-loop, "
                             "think_s 5, CPU, prefix caching on",
                       pred="p95 turn <= 20 s holds to N~8-11 and fails by N=16 (roofline/additive band); "
                            "see `agents --trace support --tool-wait 5 --slo 20,30`"))
    print(json.dumps(probes, indent=2))


def cmd_selftest(a):
    s = shapes("qwen3-30b-a3b")
    checks = {
        "dram_peak == 57.6 GB/s": abs(dram_peak() - 57.6e9) < 1e6,
        "qwen3-30b-a3b total ~30.5B": abs(s["total_params"] / 1e9 - 30.5) < 0.2,
        "qwen3-30b-a3b active incl. embed ~3.3B": abs((s["active_params"] + s["embed"]) / 1e9 - 3.35) < 0.1,
        "gpt-oss-20b total ~21B": abs(shapes("gpt-oss-20b")["total_params"] / 1e9 - 20.9) < 0.3,
        "gpt-oss-20b active ~3.6B": abs(shapes("gpt-oss-20b")["active_params"] / 1e9 - 3.61) < 0.1,
        "qwen3-4b int4 estimate within 2% of OV file": abs(
            (3.633e9 * INT4_GS128 + 0.389e9) - MODELS["qwen3-4b"]["ov_bytes"]) / MODELS["qwen3-4b"]["ov_bytes"] < 0.02,
        "unique_experts(B=1)==topk": abs(unique_experts(128, 8, 1) - 8) < 1e-9,
        "unique_experts monotone, <= E": unique_experts(128, 8, 1000) <= 128,
    }
    for mode in ("roofline", "additive"):
        for dev in ("cpu", "igpu"):
            _, r = calibrate(dev, mode)
            checks[f"{dev}/{mode} reproduces c1,c4"] = abs(r["c1_residual_pct"]) < 1e-6 and abs(r["c4_residual_pct"]) < 1e-6
    eng, _ = calibrate("cpu", "roofline")
    a1 = simulate(eng, "qwen3-4b", "support", 4, 5.0, horizon=400)["p95"]
    a2 = simulate(eng, "qwen3-4b", "support", 16, 5.0, horizon=400)["p95"]
    checks["more agents -> higher p95"] = a2 > a1
    no = simulate(eng, "qwen3-4b", "support", 4, 5.0, horizon=400, prefix_cache=False)["p95"]
    checks["prefix cache off -> higher p95"] = no > a1
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    sys.exit(0 if all(checks.values()) else 1)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hw").set_defaults(f=cmd_hw)
    sub.add_parser("models").set_defaults(f=cmd_models)
    sub.add_parser("calibrate").set_defaults(f=cmd_calibrate)
    c = sub.add_parser("curve")
    c.add_argument("--device", default="cpu", choices=["cpu", "igpu"])
    c.add_argument("--mode", default="roofline", choices=["roofline", "additive"])
    c.add_argument("--model", default="qwen3-4b", choices=list(MODELS))
    c.add_argument("--ctx", type=int, default=H2_CTX)
    c.add_argument("--batches", default="1,2,4,8,16,32,64,128")
    c.add_argument("--kv", choices=["u8", "u4", "f16"])
    c.add_argument("--shared-prefix", type=int, default=0, help="tokens of shared prefix read once per step")
    c.set_defaults(f=cmd_curve)
    b = sub.add_parser("box")
    b.add_argument("--mode", default="roofline", choices=["roofline", "additive"])
    b.add_argument("--model", default="qwen3-4b", choices=list(MODELS))
    b.add_argument("--ctx", type=int, default=H2_CTX)
    b.add_argument("--bw-sys", type=float, default=None, help="GB/s shared DRAM cap (default: CPU-fitted bw)")
    b.set_defaults(f=cmd_box)
    g = sub.add_parser("agents")
    g.add_argument("--device", default="cpu", choices=["cpu", "igpu"])
    g.add_argument("--mode", default="roofline", choices=["roofline", "additive"])
    g.add_argument("--model", default="qwen3-4b", choices=list(MODELS))
    g.add_argument("--trace", default="support", choices=list(TRACES))
    g.add_argument("--tool-wait", default="5", help="mean tool wait seconds, comma list")
    g.add_argument("--slo", default="10", help="p95 turn-latency SLO seconds, comma list")
    g.add_argument("--horizon", type=float, default=900.0)
    g.add_argument("--nmax", type=int, default=512)
    g.add_argument("--no-prefix-cache", action="store_true")
    g.add_argument("--cache-gb", type=float, default=None, help="fixed KV pool (default dynamic, OVMS cache_size 0)")
    g.add_argument("--shared-prefix", action="store_true", help="read shared system-prompt KV once per step")
    g.add_argument("--kv", choices=["u8", "u4", "f16"])
    g.set_defaults(f=cmd_agents)
    sub.add_parser("probes").set_defaults(f=cmd_probes)
    sub.add_parser("selftest").set_defaults(f=cmd_selftest)
    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
