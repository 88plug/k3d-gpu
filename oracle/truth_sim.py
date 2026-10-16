"""Ground-truth world for scoring the oracle. The oracle never imports this.

It is written separately from slo_oracle.py (its own scheduler, cache and DAG
loop) and adds effects that the oracle does not model:
  * compute efficiency saturates with tokens per step: ec * n / (n + n_half)
    (AMX tiles are under-filled by small batches), floored at ec * avx_rel
    (small-M GEMMs fall back to AVX-512, so decode stays bandwidth-bound as
    on real CPUs);
  * bandwidth efficiency falls as the batch grows: eb * (1 - numa_d * min(B, 64) / 64);
  * compute and memory overlap imperfectly: + overlap_loss * min(t_c, t_m);
  * attention runs at its own, lower efficiency;
  * per-sequence sampler cost, per-request front-end delay, lognormal noise
    on every step;
  * the prefix cache works in blocks: hits are floored to whole blocks;
  * tool durations jitter;
  * true peak and bandwidth differ from the platform card.
"""
from __future__ import annotations

import heapq
import math
import random
from collections import OrderedDict
from dataclasses import dataclass, replace

from cards import EngineCard, ModelCard, PlatformCard, Step, Task


@dataclass
class TruthParams:
    ec: float
    n_half: float
    avx_rel: float
    eb: float
    numa_d: float
    overlap_loss: float
    attn_rel: float
    step_s: float
    sampler_s: float
    req_s: float
    noise: float
    peak_mult: float
    bw_mult: float
    block: int
    tool_jitter: float


def draw_params(rng: random.Random, kind="xeon", stress=1.0) -> TruthParams:
    """kind 'xeon' or 'laptop'. stress scales the effects the oracle does not model."""
    if kind == "laptop":
        return TruthParams(
            ec=rng.uniform(0.2, 0.6), n_half=rng.uniform(4, 64), avx_rel=1 / 16, eb=rng.uniform(0.5, 0.85),
            numa_d=0.0, overlap_loss=rng.uniform(0.0, 0.3), attn_rel=rng.uniform(0.3, 0.9),
            step_s=rng.uniform(0.002, 0.02), sampler_s=rng.uniform(1e-4, 1e-3),
            req_s=rng.uniform(0.005, 0.04), noise=rng.uniform(0.03, 0.12),
            peak_mult=rng.uniform(0.3, 0.8),      # 35 W cap throttles all-core clocks
            bw_mult=rng.uniform(0.6, 0.95), block=16, tool_jitter=0.0)
    return TruthParams(
        ec=rng.uniform(0.3, 0.7), n_half=rng.uniform(16, 512) * stress, avx_rel=rng.uniform(1 / 16, 1 / 8),
        eb=rng.uniform(0.5, 0.85), numa_d=min(0.9, rng.uniform(0.0, 0.3) * stress),
        overlap_loss=rng.uniform(0.0, 0.3) * stress, attn_rel=rng.uniform(0.3, 0.9),
        step_s=rng.uniform(0.002, 0.01), sampler_s=rng.uniform(5e-5, 5e-4),
        req_s=rng.uniform(0.005, 0.03), noise=rng.uniform(0.02, 0.10),
        peak_mult=rng.uniform(0.7, 1.3), bw_mult=rng.uniform(0.7, 1.3),
        block=16, tool_jitter=rng.uniform(0.05, 0.2))


def linear_params() -> TruthParams:
    """Every unmodelled effect off: the truth world collapses to the oracle's model."""
    return TruthParams(ec=0.5, n_half=0.0, avx_rel=0.0, eb=0.7, numa_d=0.0, overlap_loss=0.0, attn_rel=1.0,
                       step_s=0.004, sampler_s=0.0, req_s=0.0, noise=0.0, peak_mult=1.0,
                       bw_mult=1.0, block=1, tool_jitter=0.0)


class _Blocks:
    def __init__(self, cap_tokens, block):
        self.block, self.cap = block, max(cap_tokens // block, 1)
        self.used, self.map = 0, OrderedDict()

    def lookup(self, prefix):
        got, key = 0, ()
        for seg, tok in prefix:
            key = key + (seg,)
            if key not in self.map:
                break
            self.map.move_to_end(key)
            got += tok
        return (got // self.block) * self.block

    def store(self, prefix):
        key = ()
        for seg, tok in prefix:
            key = key + (seg,)
            if key in self.map:
                self.map.move_to_end(key)
                continue
            nb = -(-tok // self.block)
            self.map[key] = nb
            self.used += nb
        while self.used > self.cap and self.map:
            self.used -= self.map.popitem(last=False)[1]


class TruthWorld:
    def __init__(self, model: ModelCard, plat: PlatformCard, eng: EngineCard, tp: TruthParams, seed=0):
        self.m, self.eng, self.tp = model, eng, tp
        self.peak = plat.peak_flops * tp.peak_mult
        self.bw = plat.mem_bw * tp.bw_mult
        self.seed = seed

    # one engine iteration
    def _step(self, rng, pre, dec):
        m, tp = self.m, self.tp
        a = 4.0 * m.n_layers * m.d_model
        n_tok = sum(n for n, _ in pre) + len(dec)
        lin = 2.0 * m.active_params * n_tok
        att = sum(a * (s * n + n * n / 2.0) for n, s in pre) + sum(a * c for c in dec)
        eff = tp.ec * max(n_tok / (n_tok + tp.n_half), tp.avx_rel)
        t_c = lin / (self.peak * eff) + att / (self.peak * eff * tp.attn_rel)
        bsz = len(pre) + len(dec)
        if m.n_experts:   # sample the experts actually touched
            touched = set()
            for _ in range(bsz):
                touched.update(rng.sample(range(m.n_experts), m.top_k))
            wbytes = m.bytes_per_param * (m.dense_params + m.expert_params * len(touched) / m.n_experts)
        else:
            wbytes = m.bytes_per_param * m.dense_params
        kv = m.kv_bytes_per_token * (sum(s + n for n, s in pre) + sum(c + 1 for c in dec))
        t_m = (wbytes + kv) / (self.bw * tp.eb * (1 - tp.numa_d * min(bsz, 64) / 64))
        t = max(t_c, t_m) + tp.overlap_loss * min(t_c, t_m) + tp.step_s + tp.sampler_s * bsz
        return t * math.exp(rng.gauss(0.0, tp.noise)) if tp.noise else t

    def run(self, tasks):
        """Returns {"task": {tid: latency}, "calls": {key: rec}} with rec start/first/end offsets."""
        rng = random.Random(self.seed)
        tp, eng = self.tp, self.eng
        cache = _Blocks(eng.cache_tokens, tp.block)
        heap, n = [], [0]
        wait, run = [], []
        recs, lat, todo = {}, {}, {}
        steps = {(t.id, s.id): (t, s) for t in tasks for s in t.steps}

        def at(t, what, obj):
            n[0] += 1
            heapq.heappush(heap, (t, n[0], what, obj))

        def launch(t, s, now):
            if s.kind == "tool":
                d = s.dur_s * (math.exp(rng.gauss(0, tp.tool_jitter)) if tp.tool_jitter else 1.0)
                recs[(t.id, s.id)] = {"start": now - t.arrival_s, "end": now + d - t.arrival_s}
                at(now + d, "end", (t, s))
            else:
                recs[(t.id, s.id)] = {"start": now - t.arrival_s, "t_sub": now, "out": s.out}
                at(now + tp.req_s, "queue", (t, s))

        def end(t, s, now):
            td = todo[t.id]
            td["done"].add(s.id)
            if len(td["done"]) == len(t.steps):
                lat[t.id] = now - t.arrival_s
                return
            for x in t.steps:
                if x.id not in td["go"] and all(d in td["done"] for d in x.deps):
                    td["go"].add(x.id)
                    launch(t, x, now)

        for t in tasks:
            todo[t.id] = {"done": set(), "go": set()}
            at(t.arrival_s, "arrive", t)
        now = 0.0
        while heap or wait or run:
            while heap and heap[0][0] <= now:
                _, _, what, obj = heapq.heappop(heap)
                if what == "arrive":
                    for s in obj.steps:
                        if not s.deps:
                            todo[obj.id]["go"].add(s.id)
                            launch(obj, s, now)
                elif what == "queue":
                    t, s = obj
                    wait.append({"key": (t.id, s.id), "t": t, "s": s, "inp": s.in_tokens, "gen": 0})
                else:
                    end(*obj, now)
            if not (wait or run):
                if heap:
                    now = heap[0][0]
                continue
            while wait and len(run) < eng.max_batch:
                q = wait.pop(0)
                q["cached"] = min(cache.lookup(q["s"].prefix), q["inp"] - 1)
                q["left"] = q["inp"] - q["cached"]
                recs[q["key"]]["cached"] = q["cached"]
                run.append(q)
            dec = [q for q in run if q["left"] == 0]
            budget = eng.chunk_tokens - len(dec)
            pre = []
            for q in run:
                if q["left"] and budget > 0:
                    k = min(q["left"], budget)
                    pre.append((q, k))
                    budget -= k
            now += self._step(rng, [(k, q["inp"] - q["left"]) for q, k in pre],
                              [q["inp"] + q["gen"] for q in dec])
            for q in dec:
                q["gen"] += 1
            for q, k in pre:
                q["left"] -= k
                if not q["left"]:
                    q["gen"] = 1
                    recs[q["key"]]["t_first"] = now
                    cache.store(q["s"].prefix)
            keep = []
            for q in run:
                if q["left"] == 0 and q["gen"] >= q["s"].out:
                    r = recs[q["key"]]
                    r["t_end"], r["end"] = now, now - q["t"].arrival_s
                    end(q["t"], q["s"], now)
                else:
                    keep.append(q)
            run = keep
        return {"task": lat, "calls": recs}

    def measure(self, shape, metric):
        """Run one anchor microbenchmark (unique prefixes, all at t=0)."""
        tasks = [Task(f"m{i}", 0.0, [Step("s", "llm", [], [(f"m{i}.p", shape["in"])], shape["out"])])
                 for i in range(shape["n"])]
        recs = list(self.run(tasks)["calls"].values())
        if metric == "ttft":
            return sum(r["t_first"] - r["t_sub"] for r in recs) / len(recs)
        return sum((r["t_end"] - r["t_first"]) / (r["out"] - 1) for r in recs) / len(recs)

    def with_seed(self, seed):
        w = TruthWorld.__new__(TruthWorld)
        w.__dict__.update(self.__dict__)
        w.seed = seed
        return w


__all__ = ["TruthParams", "TruthWorld", "draw_params", "linear_params", "replace"]
