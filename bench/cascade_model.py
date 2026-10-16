#!/usr/bin/env python3
"""Cascade (shared-prefix) decode attention for agent serving on an Intel client CPU.

Idea M3: agents that share one system prompt + tool schema hold the same KV
prefix. Stock paged attention computes q.K and p.V for that prefix once per
sequence and per query head. A cascade kernel reads the prefix once for the
whole batch, computes it as a matrix-matrix product, computes each sequence's
unique suffix separately, and merges the two partial softmaxes with
log-sum-exp. Prior art: Hydragen (arXiv 2402.05099), ChunkAttention
(arXiv 2402.15220), FlashInfer cascade attention, RelayAttention.

This file is stdlib-only and makes no live measurement. Subcommands:

  l3      cache simulation of the OpenVINO 2026.4.0 CPU paged-attention loop
          orders (exec_loop_bhl static split, exec_loop_mixed 2-D split) over
          shared vs distinct prefixes: DRAM KV bytes per step. Falsifier for the
          "prefix KV is streamed from DRAM B times" framing.
  step    per-step time and aggregate decode tok/s per kernel arm (ablation).
  agents  agents per box at a p95 turn-latency SLO (discrete-event simulation).
  aslo    A@SLO from agent_trace.py per-turn JSONL runs (live data, later).
  probes  the pre-registered live probes with predicted values, as JSON.

Kernel arms (prefix part; the per-sequence suffix always uses the stock kernel
except where noted):
  stock            per (sequence, query head) GEMV, u8 dequant fused per row
  cascade_bw_only  ABLATION: prefix read once, stock compute rate
  cascade          prefix dequantized once per block, 32-row fp32 GEMM
  vnni_gemv        ABLATION, no cascade: per-row q.K with int8 q on AVX-VNNI
  cascade_vnni     cascade + AVX-VNNI q.K for the 32-row prefix GEMM
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import random
import statistics
import sys
from collections import OrderedDict

# ---------------------------------------------------------------- facts
# Qwen3-4B config.json (HF): 36 layers, 32 q heads, 8 kv heads, head_dim 128.
MODEL = dict(L=36, Hq=32, Hk=8, D=128, weights_bytes=2.26e9)
# OpenVINO CPU u8 KV: 128 u8 + f32 scale + f32 zero point per token per kv head.
KV_BYTES_TOK_HEAD = 128 + 8
KV_BYTES_TOK = MODEL["L"] * MODEL["Hk"] * 2 * KV_BYTES_TOK_HEAD  # 78336 B
BLOCK = 32  # OpenVINO CPU paged-attention block size (tokens)
CHUNK_BYTES = BLOCK * KV_BYTES_TOK_HEAD  # one K (or V) block of one kv head

HOST = dict(
    dram_peak=57.6e9,  # 2 ch x 8 B x 3600 MT/s (dmidecode, verified 2026-10-16)
    bw_eff=35.5e9,  # measured: 15.7 tok/s x 2.26 GB at c=1 (H2)
    l3_bytes=36 * 2**20,  # lscpu
    p_cores=8,
)

# Step-time calibration from H2 (Qwen3-4B int4, OVMS 2026.4.1, CPU P-cores,
# short context): c=1 15.7 tok/s -> 63.7 ms; c=4 56 tok/s -> 71.4 ms.
# step(B) = T_W + S0*B  =>  S0 = (71.4-63.7)/3, T_W = 63.7 - S0.
_STEP1, _STEP4 = 1 / 15.7, 4 / 56.0
S0 = (_STEP4 - _STEP1) / 3
T_W = _STEP1 - S0
PREFILL_S_PER_TOK = 1 / 172.0  # H2 measured prefill rate, compute bound

# Kernel MAC rates: PRIORS, not measurements. The C microbench
# (bench/cascade_kernel.c) measures them; pass --rates to override.
# Peak MAC/clk/core on Raptor Cove (FP ports p0/p1, shuffles p5):
#   stock: the u8 kernels (dot_product_block_quantized_by_dims,
#     attn_acc_value_block_by_dim, AVX2 branch, openvino 2026.4.0) issue
#     cvtepu8_epi32 (p5) + cvtepi32_ps + sub_ps(zp) + fmadd_ps per 8 MACs:
#     3 p01 uops per 8 MACs -> 16/3 MAC/clk.
#   cascade fp32: dequant amortised over B*G rows, then FMA only: 16 MAC/clk.
#   AVX-VNNI vpdpbusd ymm: 32 MACs, 2 per clk (p01) -> 64 MAC/clk.
# Efficiencies are guesses; the C microbench replaces them.
PRIOR = dict(
    ghz=3.5,
    stock=(16 / 3, 0.75),
    cascade=(16, 0.6),
    vnni_gemv=(64, 0.25),
    cascade_vnni=(64, 0.5),
    prefill_attn=4.0e11,  # MAC/s for prefill attention (brgemm), all arms
)


def rates(prior=None):
    p = dict(PRIOR, **(prior or {}))
    f = p["ghz"] * 1e9 * HOST["p_cores"]
    return {k: p[k][0] * p[k][1] * f for k in ("stock", "cascade", "vnni_gemv", "cascade_vnni")} | {
        "prefill_attn": p["prefill_attn"]
    }


# ---------------------------------------------------------------- reference math
def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def attend_partial(q, keys, vals, scale):
    """Unnormalised partial attention: (o, m, l) with o = sum_j e^(s_j - m) v_j."""
    if not keys:
        return [0.0] * len(q), -math.inf, 0.0
    s = [scale * _dot(q, k) for k in keys]
    m = max(s)
    w = [math.exp(x - m) for x in s]
    o = [sum(wj * v[d] for wj, v in zip(w, vals)) for d in range(len(vals[0]))]
    return o, m, sum(w)


def merge(parts):
    """Log-sum-exp merge of partial attentions into the normalised output."""
    m = max(p[1] for p in parts)
    denom = sum(p[2] * math.exp(p[1] - m) for p in parts if p[2])
    d = len(parts[0][0])
    o = [0.0] * d
    for po, pm, pl in parts:
        if pl:
            c = math.exp(pm - m)
            for i in range(d):
                o[i] += po[i] * c
    return [x / denom for x in o]


def attend(q, keys, vals, scale):
    return merge([attend_partial(q, keys, vals, scale)])


def cascade_decode(queries, prefix_k, prefix_v, suffix_k, suffix_v, group, scale):
    """queries[b][h] for B sequences x Hq heads; prefix_k/v[hk] shared;
    suffix_k/v[b][hk] per sequence. Returns out[b][h]. The prefix part runs as
    one pass over all (b, h) rows of a kv head: the block is read once."""
    B, Hq = len(queries), len(queries[0])
    out = [[None] * Hq for _ in range(B)]
    for hk in range(len(prefix_k)):
        rows = [(b, h) for b in range(B) for h in range(hk * group, (hk + 1) * group)]
        pre = {r: attend_partial(queries[r[0]][r[1]], prefix_k[hk], prefix_v[hk], scale) for r in rows}
        for b, h in rows:
            suf = attend_partial(queries[b][h], suffix_k[b][hk], suffix_v[b][hk], scale)
            out[b][h] = merge([pre[(b, h)], suf])
    return out


def quant_u8(x):
    """Per-token asymmetric u8: (codes, scale, zero point), x ~= (u - zp) * s."""
    lo, hi = min(x), max(x)
    s = (hi - lo) / 255 or 1.0
    zp = round(-lo / s)
    return [min(255, max(0, round(v / s) + zp)) for v in x], s, zp


def dot_u8_fused(q, codes, s, zp):
    """q . dequant(codes) without materialising the dequantised vector."""
    return s * (_dot(q, codes) - zp * sum(q))


# ---------------------------------------------------------------- L3 simulation
def _bisect_2d(r0, r1, c0, c1, n):
    """Split rows x cols into n rectangles, halving the longer side (models TBB
    blocked_range2d with the default partitioner on an idle pool)."""
    if n <= 1 or (r1 - r0) * (c1 - c0) <= 1:
        return [(r0, r1, c0, c1)]
    n1 = n // 2
    if r1 - r0 >= c1 - c0:
        mid = r0 + (r1 - r0) * n1 // n or r0 + 1
        return _bisect_2d(r0, mid, c0, c1, n1) + _bisect_2d(mid, r1, c0, c1, n - n1)
    mid = c0 + (c1 - c0) * n1 // n or c0 + 1
    return _bisect_2d(r0, r1, c0, mid, n1) + _bisect_2d(r0, r1, mid, c1, n - n1)


def _phys(b, j, shared, prefix_blocks):
    return ("p", j) if shared and j < prefix_blocks else ("s", b, j)


def thread_traces(path, B, ctx_blocks, prefix_blocks, Hk, nthr, shared):
    """Per-thread ordered KV chunk reads for one layer, split into phases that
    end in a barrier (the next parallel_for starts after the previous joins).
    Loop orders read from openvino 2026.4.0 executor_pa.cpp:
      bhl:   parallel_for3d(B, kv_len_in_blocks, Hk, loop_qk) [static split of
             the flattened space], softmax, parallel_for3d(..., loop_wk)
      mixed: parallel_for2d_dynamic(attn_work_count, Hk, ...) -> each item runs
             exec_kernel_one_bh: all K blocks, softmax, all V blocks."""
    if path == "bhl":
        work = [(b, j, h) for b in range(B) for j in range(ctx_blocks) for h in range(Hk)]
        phases = []
        for kind in ("K", "V"):
            per = []
            n = len(work)
            for t in range(nthr):  # openvino splitter(): contiguous, sizes differ by <= 1
                n1 = -(-n // nthr)
                n2 = n1 - 1
                t1 = n - n2 * nthr
                size = n1 if t < t1 else n2
                start = t * n1 if t <= t1 else t1 * n1 + (t - t1) * n2
                per.append([(kind, _phys(b, j, shared, prefix_blocks), h) for b, j, h in work[start:start + size]])
            phases.append(per)
        return phases
    if path == "mixed":
        per = []
        for r0, r1, c0, c1 in _bisect_2d(0, B, 0, Hk, nthr):
            seq = []
            for b in range(r0, r1):
                for h in range(c0, c1):
                    for kind in ("K", "V"):
                        seq += [(kind, _phys(b, j, shared, prefix_blocks), h) for j in range(ctx_blocks)]
            per.append(seq)
        return [per]
    raise ValueError(path)


def simulate_l3(path, B, ctx, prefix, Hk=8, nthr=8, shared=True, l3_bytes=None,
                speeds=None, jitter=0.0, seed=0):
    """DRAM KV bytes for one layer's decode attention under an LRU model of the
    shared L3. Threads interleave by simulated time; speeds[t] scales a thread's
    access rate (E-core threads ~0.5); jitter adds per-access noise. The layer
    starts cold: the matmuls between attention ops stream ~63 MB of weights."""
    cap = (l3_bytes or HOST["l3_bytes"]) // CHUNK_BYTES
    ctx_blocks = -(-ctx // BLOCK)
    prefix_blocks = prefix // BLOCK  # only full blocks are shared by prefix caching
    speeds = speeds or [1.0] * nthr
    rng = random.Random(seed)
    lru = OrderedDict()
    misses = reads = 0
    clock = 0.0
    for phase in thread_traces(path, B, ctx_blocks, prefix_blocks, Hk, nthr, shared):
        heap = [(clock, t, 0) for t in range(len(phase)) if phase[t]]
        heapq.heapify(heap)
        end = clock
        while heap:
            now, t, i = heapq.heappop(heap)
            key = phase[t][i]
            reads += 1
            if key in lru:
                lru.move_to_end(key)
            else:
                misses += 1
                lru[key] = True
                if len(lru) > cap:
                    lru.popitem(last=False)
            dt = (1.0 + (rng.random() * 2 - 1) * jitter) / speeds[t % len(speeds)]
            end = max(end, now + dt)
            if i + 1 < len(phase[t]):
                heapq.heappush(heap, (now + dt, t, i + 1))
        clock = end  # barrier
    return dict(dram_bytes=misses * CHUNK_BYTES, read_bytes=reads * CHUNK_BYTES,
                unique_bytes=_unique_bytes(B, ctx_blocks, prefix_blocks, Hk, shared),
                naive_bytes=B * ctx_blocks * Hk * 2 * CHUNK_BYTES)


def _unique_bytes(B, ctx_blocks, prefix_blocks, Hk, shared):
    blocks = (prefix_blocks + B * (ctx_blocks - prefix_blocks)) if shared else B * ctx_blocks
    return blocks * Hk * 2 * CHUNK_BYTES


# ---------------------------------------------------------------- step model
ARMS = ("stock", "cascade_bw_only", "cascade", "vnni_gemv", "cascade_vnni")


def attn_macs(tokens):
    """q.K + p.V MACs for one decode token over `tokens` context, all layers."""
    m = MODEL
    return tokens * m["Hq"] * m["D"] * 2 * m["L"]


def decode_attn_time(arm, B, prefix, suffix_sum, R, shared=True, dram="l3", overlap=True):
    """Seconds spent in decode attention for one step with B sequences each
    holding `prefix` shared tokens plus their own suffix (total suffix_sum).
    dram='l3': stock already reads the shared prefix from DRAM once (lockstep
    L3 reuse); dram='naive': stock reads it B times (the idea's framing)."""
    if B == 0:
        return 0.0
    pre_macs, suf_macs = attn_macs(B * prefix), attn_macs(suffix_sum)
    half = 0.5  # q.K and p.V carry equal MACs
    measured = R.get("measured", {})
    if arm in measured:  # whole-op MAC/s from the C microbench
        cmp = (pre_macs + suf_macs) / measured[arm]
    elif arm == "stock":
        cmp = (pre_macs + suf_macs) / R["stock"]
    elif arm == "cascade_bw_only":
        cmp = (pre_macs + suf_macs) / R["stock"]
    elif arm == "cascade":
        cmp = pre_macs / R["cascade"] + suf_macs / R["stock"]
    elif arm == "vnni_gemv":
        tot = pre_macs + suf_macs
        cmp = tot * half / R["vnni_gemv"] + tot * half / R["stock"]
    elif arm == "cascade_vnni":
        cmp = (pre_macs * half / R["cascade_vnni"] + pre_macs * half / R["cascade"]
               + suf_macs * half / R["vnni_gemv"] + suf_macs * half / R["stock"])
    else:
        raise ValueError(arm)
    cascaded = arm.startswith("cascade")
    if not shared:
        kv = B * prefix + suffix_sum
    elif cascaded or dram == "l3":
        kv = prefix + suffix_sum
    else:
        kv = B * prefix + suffix_sum
    t_kv = kv * KV_BYTES_TOK / HOST["bw_eff"]
    return max(cmp, t_kv) if overlap else cmp + t_kv


def step_time(arm, B, prefix, suffix_sum, R, **kw):
    if B == 0:
        return 0.0
    return T_W + S0 * B + decode_attn_time(arm, B, prefix, suffix_sum, R, **kw)


def agg_tps(arm, B, prefix, suffix, R, **kw):
    return B / step_time(arm, B, prefix, B * suffix, R, **kw)


# ---------------------------------------------------------------- agent DES
def simulate_agents(arm, n_agents, R, prefix=3400, turns=(3, 6), new_tok=(50, 160),
                    first_tok=(40, 110), out_tok=(40, 96), tool_wait=1.0, chunk=256,
                    horizon=900.0, warmup=120.0, seed=1, prefill_s_per_tok=PREFILL_S_PER_TOK, **kw):
    """N closed-loop agents (support-trace shape: shared prefix, 3-6 turns,
    tool result appended per turn, <=96 output tokens, think/tool wait between
    turns). Continuous batching: each step decodes one token for every agent in
    decode and prefills up to `chunk` queued tokens (FIFO). Turn latency = turn
    submit to last output token. Returns p50/p95 turn latency and decode tok/s."""
    rng = random.Random(seed)
    A = []
    for i in range(n_agents):
        A.append(dict(state="wait", ready=rng.uniform(0, 10.0), suffix=0, turns_left=0,
                      pf=0, out=0, t0=0.0))
    t = 0.0
    lat, toks = [], 0
    pfq = []

    def finish(a):
        if a["t0"] >= warmup:
            lat.append(t - a["t0"])
        a["turns_left"] -= 1
        a["state"], a["ready"] = "wait", t + tool_wait

    while t < horizon:
        for a in A:
            if a["state"] == "wait" and a["ready"] <= t:
                if a["turns_left"] == 0:
                    a["turns_left"], a["suffix"] = rng.randint(*turns), 0
                    a["pf"] = rng.randint(*first_tok)
                else:
                    a["pf"] = rng.randint(*new_tok)
                a["out"], a["t0"], a["state"] = rng.randint(*out_tok), t, "pf"
                pfq.append(a)
        dec = [a for a in A if a["state"] == "dec"]
        budget, n_pf, pf_attn = chunk, 0, 0.0
        done_pf = []
        for a in pfq:
            if budget == 0:
                break
            take = min(budget, a["pf"])
            pf_attn += attn_macs(take * (prefix + a["suffix"]))
            a["pf"] -= take
            a["suffix"] += take
            budget -= take
            n_pf += take
            if a["pf"] == 0:
                done_pf.append(a)
        if not dec and n_pf == 0:
            nxt = min(a["ready"] for a in A if a["state"] == "wait")
            t = max(t, nxt)
            continue
        dt = step_time(arm, len(dec), prefix, sum(a["suffix"] for a in dec), R, **kw) if dec else T_W
        dt += n_pf * prefill_s_per_tok + pf_attn / R["prefill_attn"]
        t += dt
        for a in dec:
            a["out"] -= 1
            a["suffix"] += 1
            if t >= warmup:
                toks += 1
            if a["out"] <= 0:
                finish(a)
        for a in done_pf:
            pfq.remove(a)
            a["out"] -= 1  # the step that finishes prefill emits the first token
            a["suffix"] += 1
            if a["out"] <= 0:
                finish(a)
            else:
                a["state"] = "dec"
    if not lat:
        return dict(p50=math.inf, p95=math.inf, turns=0, tps=0.0)
    lat.sort()
    return dict(p50=lat[len(lat) // 2], p95=lat[min(len(lat) - 1, int(0.95 * len(lat)))],
                turns=len(lat), tps=toks / max(1e-9, horizon - warmup))


def agents_at_slo(arm, slo, R, grid=None, seeds=(1, 2), **kw):
    """Largest N on the grid whose p95 turn latency (median over seeds) <= slo.
    Scans upward and stops after two consecutive failures."""
    grid = grid or list(range(1, 65))
    best, fails = 0, 0
    for n in grid:
        p95 = statistics.median(simulate_agents(arm, n, R, seed=s, **kw)["p95"] for s in seeds)
        if p95 <= slo:
            best, fails = n, 0
        else:
            fails += 1
            if fails >= 2:
                break
    return best


# ---------------------------------------------------------------- live data
def p95_from_jsonl(path, warmup=120.0):
    """p95 of per-turn e2e_s from an agent_trace.py run (sent_at_s >= warmup,
    errors excluded). Returns (p95, n_turns, n_errors)."""
    xs, errs = [], 0
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("error"):
                errs += 1
            elif r.get("sent_at_s", 0) >= warmup and r.get("e2e_s") is not None:
                xs.append(r["e2e_s"])
    if not xs:
        return math.inf, 0, errs
    xs.sort()
    return xs[min(len(xs) - 1, int(0.95 * len(xs)))], len(xs), errs


def aslo(runs, slo, warmup=120.0):
    """runs: {N: [jsonl paths]}. A@SLO = largest N such that every N' <= N on
    the grid has median-over-repeats p95 <= slo (a later pass after a fail does
    not count: the curve must be monotone to be trusted)."""
    table, best = {}, 0
    for n in sorted(runs):
        p95s = [p95_from_jsonl(p, warmup)[0] for p in runs[n]]
        table[n] = statistics.median(p95s)
    for n in sorted(table):
        if table[n] <= slo:
            best = n
        else:
            break
    return best, table


# ---------------------------------------------------------------- CLI
def cmd_l3(a):
    rows = []
    for path in ("bhl", "mixed"):
        for nthr in (8, 16):
            for shared in (True, False):
                for cap_mb in (36, 8, 2):
                    r = simulate_l3(path, a.batch, a.prefix + a.suffix, a.prefix, nthr=nthr, shared=shared,
                                    l3_bytes=cap_mb * 2**20)
                    rows.append(dict(path=path, nthr=nthr, shared=shared, l3_mb=cap_mb,
                                     dram_over_unique=round(r["dram_bytes"] / r["unique_bytes"], 3),
                                     dram_over_naive=round(r["dram_bytes"] / r["naive_bytes"], 3),
                                     dram_gb_step=round(r["dram_bytes"] * MODEL["L"] / 1e9, 3)))
    # E-core threads in the pool and per-access jitter break lockstep.
    for label, speeds, jit in (("P+E speeds, jitter 0.3", [1.0] * 8 + [0.5] * 8, 0.3),):
        for cap_mb in (36, 8, 2):
            r = simulate_l3("bhl", a.batch, a.prefix + a.suffix, a.prefix, nthr=16, shared=True,
                            l3_bytes=cap_mb * 2**20, speeds=speeds, jitter=jit)
            rows.append(dict(path="bhl " + label, nthr=16, shared=True, l3_mb=cap_mb,
                             dram_over_unique=round(r["dram_bytes"] / r["unique_bytes"], 3),
                             dram_over_naive=round(r["dram_bytes"] / r["naive_bytes"], 3),
                             dram_gb_step=round(r["dram_bytes"] * MODEL["L"] / 1e9, 3)))
    print(json.dumps(rows, indent=1))


def load_kernel_rates(path):
    """Median whole-op MAC/s per arm from `cascade_kernel bench` JSON lines.
    cascade_bw_only has no kernel of its own: it computes at the stock rate."""
    by = {}
    with open(path) as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                by.setdefault(r["arm"], []).append(r["gmac_s"] * 1e9)
    out = {k: statistics.median(v) for k, v in by.items()}
    if "stock" in out:
        out["cascade_bw_only"] = out["stock"]
    return out


def _rates(a):
    R = rates(json.loads(a.rates) if a.rates else None)
    if a.kernel:
        R["measured"] = load_kernel_rates(a.kernel)
    return R


def cmd_step(a):
    R = _rates(a)
    out = {}
    for dram in ("l3", "naive"):
        base_short = agg_tps("stock", a.batch, 0, 64, R, dram=dram)
        row = {"stock_short_ctx": round(base_short, 1)}
        stock = agg_tps("stock", a.batch, a.prefix, a.suffix, R, dram=dram)
        for arm in ARMS:
            v = agg_tps(arm, a.batch, a.prefix, a.suffix, R, dram=dram)
            row[arm] = dict(tps=round(v, 1), x_stock=round(v / stock, 3))
        row["stock_long_over_short"] = round(stock / base_short, 3)
        row["distinct_prefix_stock"] = round(agg_tps("stock", a.batch, a.prefix, a.suffix, R,
                                                     dram=dram, shared=False), 1)
        out[f"dram={dram}"] = row
    out["rates_GMACs"] = {k: round(v / 1e9, 1) for k, v in R.items()}
    print(json.dumps(out, indent=1))


def cmd_agents(a):
    R = _rates(a)
    res = {}
    for arm in a.arms.split(","):
        res[arm] = {f"slo={s:g}s": agents_at_slo(arm, s, R, tool_wait=a.tool_wait, horizon=a.horizon,
                                                  prefill_s_per_tok=1 / a.prefill_tps,
                                                  dram=a.dram)
                    for s in (float(x) for x in a.slo.split(","))}
    print(json.dumps(res, indent=1))


def cmd_aslo(a):
    runs = {}
    for spec in a.run:
        n, path = spec.split("=", 1)
        runs.setdefault(int(n), []).append(path)
    best, table = aslo(runs, a.slo, a.warmup)
    print(json.dumps({"slo_s": a.slo, "a_at_slo": best, "p95_by_n": table}, indent=1))


def cmd_probes(a):
    R = rates()
    pre, suf, B = 3400, 64, 8
    w = MODEL["weights_bytes"] / 1e9
    probes = [
        dict(id="M3-S0a", what="aggregate decode tok/s, c=8, shared 3.4k prefix vs short context (stock OVMS)",
             predict_ratio=round(agg_tps("stock", B, pre, suf, R) / agg_tps("stock", B, 0, suf, R), 2),
             note="both the bandwidth and the compute hypothesis predict ~0.5-0.65; not discriminating alone"),
        dict(id="M3-S0b", what="IMC data_read GB per decode step, c=8, 3.4k ctx",
             predict_shared_gb=round(w + (pre + B * suf) * KV_BYTES_TOK / 1e9, 2),
             predict_distinct_gb=round(w + B * (pre + suf) * KV_BYTES_TOK / 1e9, 2),
             naive_predicts_shared_gb=round(w + B * (pre + suf) * KV_BYTES_TOK / 1e9, 2)),
        dict(id="M3-S0d", what="attention-added ms per step vs B in {1,2,4,8}, shared 3.4k prefix",
             predict_compute_ratio_8_over_1=round(
                 decode_attn_time("stock", 8, pre, 8 * suf, R) / decode_attn_time("stock", 1, pre, suf, R), 2),
             l3_bandwidth_bound_would_give="~1-2"),
    ]
    print(json.dumps(probes, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("l3", "step", "agents"):
        p = sub.add_parser(name)
        p.add_argument("--batch", type=int, default=8)
        p.add_argument("--prefix", type=int, default=3400)
        p.add_argument("--suffix", type=int, default=64)
        p.add_argument("--rates", help='JSON overriding PRIOR, e.g. {"cascade": [16, 0.45]}')
        p.add_argument("--kernel", help="cascade_kernel bench JSON lines: use measured per-arm rates")
    ag = sub.choices["agents"]
    ag.add_argument("--arms", default=",".join(ARMS))
    ag.add_argument("--slo", default="10,20,30")
    ag.add_argument("--tool-wait", type=float, default=1.0)
    ag.add_argument("--prefill-tps", type=float, default=172.0, help="prefill tok/s (H2: 172)")
    ag.add_argument("--horizon", type=float, default=900.0)
    ag.add_argument("--dram", choices=["l3", "naive"], default="l3")
    s = sub.add_parser("aslo")
    s.add_argument("--slo", type=float, required=True)
    s.add_argument("--warmup", type=float, default=120.0)
    s.add_argument("run", nargs="+", help="N=path.jsonl (repeat N for repeats)")
    sub.add_parser("probes")
    a = ap.parse_args(argv)
    {"l3": cmd_l3, "step": cmd_step, "agents": cmd_agents, "aslo": cmd_aslo, "probes": cmd_probes}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
