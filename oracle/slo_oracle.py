#!/usr/bin/env python3
"""Xeon SLO oracle: predict agent task p50/p95 on a Xeon serving pod.

Mechanism, in order:
  1. Fit three numbers from four microbenchmarks run on the TARGET Xeon
     (ANCHOR_SHAPES): a per-step overhead, a prefill compute efficiency
     (fraction of the platform card's AMX peak) and a decode bandwidth
     efficiency (fraction of the card's memory bandwidth). A fourth,
     concurrent shape is held out and sets the error bar.
  2. Replay an engine-agnostic agent trace (cards.py) as an event-driven DAG.
     LLM steps go through an iteration-level continuous-batching scheduler
     (decode first, then chunked prefill fills the token budget, as in vLLM).
     Each iteration costs max(compute, memory) under the per-op roofline. MoE
     weight traffic is the expected union of the experts the batch touches.
     Prefix-cache hits are recomputed from the trace's segment keys against
     the target engine's cache size; they are not copied from the recording.
  3. Report task p50/p95 with a band, and gate a Deployment on the
     annotation k3d-gpu.io/xeon-slo-p95 (seconds).

stdlib only. `python3 oracle/slo_oracle.py --help`.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import re
import sys
from collections import OrderedDict, deque
from dataclasses import dataclass

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from cards import (EngineCard, ModelCard, PlatformCard, Step, load_trace,  # noqa: E402
                   model_card, platform_card)

ANNOTATION = "k3d-gpu.io/xeon-slo-p95"

# Measurement protocol for the target host. Every request has a unique prefix.
# Two decode contexts: TPOT = bytes / (bw * eff_b) + step_s is linear in bytes,
# so the slope gives eff_b and the intercept gives step_s. (A tiny prefill
# cannot isolate step_s on a CPU: it is dominated by streaming the weights.)
ANCHOR_SHAPES = {
    "prefill":     {"n": 1, "in": 4096, "out": 2},    # -> TTFT: compute term
    "decode":      {"n": 1, "in": 256, "out": 129},   # -> TPOT at short context
    "decode_long": {"n": 1, "in": 8192, "out": 65},   # -> TPOT at long context
    "valid":       {"n": 16, "in": 1024, "out": 128},  # held out -> TPOT: error bar
}
ANCHOR_METRIC = {"prefill": "ttft", "decode": "tpot", "decode_long": "tpot", "valid": "tpot"}
BAND_FLOOR = 0.10


@dataclass
class Calib:
    eff_c: float
    eff_b: float
    step_s: float


# ------------------------------------------------------------ prefix cache

class PrefixCache:
    """LRU over prefix chains. Entry i of a prompt is keyed by segments[0..i]."""

    def __init__(self, cap_tokens: int):
        self.cap, self.used, self.lru = cap_tokens, 0, OrderedDict()

    @staticmethod
    def _chain(prefix):
        h = 0
        for key, tok in prefix:
            h = hash((h, key))
            yield h, tok

    def hit(self, prefix) -> int:
        got = 0
        for h, tok in self._chain(prefix):
            if h not in self.lru:
                break
            self.lru.move_to_end(h)
            got += tok
        return got

    def insert(self, prefix):
        for h, tok in self._chain(prefix):
            if h in self.lru:
                self.lru.move_to_end(h)
                continue
            self.lru[h] = tok
            self.used += tok
        while self.used > self.cap and self.lru:
            _, tok = self.lru.popitem(last=False)
            self.used -= tok


# ------------------------------------------------------------ cost model

class Roofline:
    def __init__(self, model: ModelCard, plat: PlatformCard, cal: Calib):
        self.m, self.p, self.c = model, plat, cal
        self.attn = 4.0 * model.n_layers * model.d_model   # FLOP per (token x context)

    def step_time(self, prefill, decode_ctx) -> float:
        """prefill: [(n_new_tokens, ctx_start)], decode_ctx: [context length]."""
        m = self.m
        n_tok = sum(n for n, _ in prefill) + len(decode_ctx)
        flops = 2.0 * m.active_params * n_tok
        flops += sum(self.attn * (s * n + n * n / 2.0) for n, s in prefill)
        flops += sum(self.attn * c for c in decode_ctx)
        seqs = len(prefill) + len(decode_ctx)
        byts = m.weight_bytes(seqs)
        byts += m.kv_bytes_per_token * (sum(s + n for n, s in prefill) + sum(c + 1 for c in decode_ctx))
        t_c = flops / (self.p.peak_flops * self.c.eff_c)
        t_m = byts / (self.p.mem_bw * self.c.eff_b)
        return max(t_c, t_m) + self.c.step_s

    def solo(self, in_tok: int, hit: int, out: int, chunk: int):
        """(ttft, e2e) for one request alone on the engine."""
        left, t = max(in_tok - hit, 1), 0.0
        while left > 0:
            n = min(left, chunk)
            t += self.step_time([(n, in_tok - left)], [])
            left -= n
        ttft = t
        for g in range(1, out):
            t += self.step_time([], [in_tok + g])
        return ttft, t


# ------------------------------------------------------------ engine

class _Req:
    __slots__ = ("key", "prefix", "inp", "out", "hit", "left", "gen", "t_sub", "t_first", "t_end")

    def __init__(self, key, prefix, out, t_sub):
        self.key, self.prefix, self.out, self.t_sub = key, prefix, out, t_sub
        self.inp = sum(t for _, t in prefix)
        self.hit = self.left = self.gen = 0
        self.t_first = self.t_end = None


class Engine:
    """Iteration-level continuous batching (decode first, chunked prefill)."""

    def __init__(self, roof: Roofline, eng: EngineCard, hits=None):
        self.r, self.e = roof, eng
        self.cache = PrefixCache(eng.cache_tokens)
        self.hits = hits                     # optional {key: cached tokens} override
        self.wait, self.run = deque(), []

    def submit(self, key, prefix, out, now):
        self.wait.append(_Req(key, prefix, out, now))

    def busy(self):
        return bool(self.wait or self.run)

    def iterate(self, now):
        while self.wait and len(self.run) < self.e.max_batch:
            q = self.wait.popleft()
            q.hit = self.hits[q.key] if self.hits is not None else self.cache.hit(q.prefix)
            q.hit = min(q.hit, q.inp - 1)
            q.left = q.inp - q.hit
            self.run.append(q)
        dec = [q for q in self.run if q.left == 0]
        budget = self.e.chunk_tokens - len(dec)
        pf = []
        for q in self.run:
            if q.left > 0 and budget > 0:
                n = min(q.left, budget)
                pf.append((q, n))
                budget -= n
        dt = self.r.step_time([(n, q.inp - q.left) for q, n in pf], [q.inp + q.gen for q in dec])
        t = now + dt
        for q in dec:
            q.gen += 1
        for q, n in pf:
            q.left -= n
            if q.left == 0:
                q.gen, q.t_first = 1, t
                self.cache.insert(q.prefix)
        done = [q for q in self.run if q.left == 0 and q.gen >= q.out]
        for q in done:
            q.t_end = t
        self.run = [q for q in self.run if q not in done]
        return dt, done


# ------------------------------------------------------------ DAG replay

def replay(tasks, engine: Engine | None = None, *, price=None, hits=None, cache_tokens=0,
           timeline=None):
    """Run the trace. Returns {"task": {tid: latency}, "calls": {key: rec}}.

    engine: full batching replay. price(step, hit) -> (ttft, e2e): each call
    priced alone (no contention), hits from `hits` or a shared PrefixCache.
    timeline: {key: (start_off, end_off)} -> submit LLM calls at recorded
    offsets and end tools at recorded offsets, ignoring dependencies.
    """
    ev, seq = [], [0]
    calls, task_lat = {}, {}
    pcache = PrefixCache(cache_tokens) if price else None
    state = {}

    def push(t, kind, payload):
        seq[0] += 1
        heapq.heappush(ev, (t, seq[0], kind, payload))

    def start(task, s, now):
        key = (task.id, s.id)
        if s.kind == "tool":
            push(now + s.dur_s, "done", (task, s))
        elif engine is not None:
            engine.submit(key, s.prefix, s.out, now)
        else:
            hit = hits[key] if hits is not None else pcache.hit(s.prefix)
            hit = min(hit, s.in_tokens - 1)
            ttft, e2e = price(s, hit)
            calls[key] = {"t_sub": now, "t_first": now + ttft, "t_end": now + e2e, "out": s.out,
                          "hit": hit}
            push(now + ttft, "cache", s.prefix)
            push(now + e2e, "done", (task, s))

    def finish(task, s, now):
        st = state[task.id]
        st["done"].add(s.id)
        st["end"] = max(st["end"], now)
        if len(st["done"]) == len(task.steps):
            task_lat[task.id] = st["end"] - task.arrival_s
            return
        if timeline is not None:
            return
        for d in task.steps:
            if d.id not in st["started"] and all(x in st["done"] for x in d.deps):
                st["started"].add(d.id)
                start(task, d, now)

    for task in tasks:
        state[task.id] = {"done": set(), "started": set(), "end": task.arrival_s}
        if timeline is None:
            push(task.arrival_s, "arrive", task)
        else:
            for s in task.steps:
                a, b = timeline[(task.id, s.id)]
                if s.kind == "tool":
                    push(task.arrival_s + b, "done", (task, s))
                else:
                    push(task.arrival_s + a, "submit", (task, s))
    by_key = {}
    now = 0.0
    while ev or (engine is not None and engine.busy()):
        while ev and ev[0][0] <= now + 1e-12:
            _, _, kind, p = heapq.heappop(ev)
            if kind == "arrive":
                for s in p.steps:
                    if not s.deps:
                        state[p.id]["started"].add(s.id)
                        start(p, s, now)
            elif kind == "submit":
                task, s = p
                start(task, s, now)
            elif kind == "cache":
                pcache.insert(p)
            else:
                finish(*p, now)
        if engine is not None and engine.busy():
            dt, done = engine.iterate(now)
            now += dt
            for q in done:
                calls[q.key] = {"t_sub": q.t_sub, "t_first": q.t_first, "t_end": q.t_end,
                                "out": q.out, "hit": q.hit}
                task, s = by_key.setdefault(q.key, _lookup(tasks, q.key))
                finish(task, s, now)
        elif ev:
            now = max(now, ev[0][0])
    return {"task": task_lat, "calls": calls}


def _lookup(tasks, key):
    for t in tasks:
        if t.id == key[0]:
            for s in t.steps:
                if s.id == key[1]:
                    return t, s
    raise KeyError(key)


def tpot(rec):
    return (rec["t_end"] - rec["t_first"]) / (rec["out"] - 1) if rec["out"] > 1 else None


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


# ------------------------------------------------------------ calibration

def anchor_trace(shape, tag="a"):
    from cards import Task
    return [Task(f"{tag}{i}", 0.0, [Step("s", "llm", [], [(f"{tag}{i}.p", shape["in"])], shape["out"])])
            for i in range(shape["n"])]


def measure(model, plat, eng, cal, name):
    """The oracle's own prediction of an anchor measurement."""
    shape = ANCHOR_SHAPES[name]
    res = replay(anchor_trace(shape), Engine(Roofline(model, plat, cal), eng))
    recs = list(res["calls"].values())
    if ANCHOR_METRIC[name] == "ttft":
        return sum(r["t_first"] - r["t_sub"] for r in recs) / len(recs)
    return sum(tpot(r) for r in recs) / len(recs)


def _solve(f, target, lo, hi, log=True, iters=40):
    """Find x in [lo, hi] with f(x) == target. f must be monotone."""
    flo, fhi = f(lo), f(hi)
    inc = fhi > flo
    for _ in range(iters):
        mid = math.sqrt(lo * hi) if log else (lo + hi) / 2
        if (f(mid) < target) == inc:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi) if log else (lo + hi) / 2


def _decode_bytes(model, shape):
    """Mean bytes streamed per decode step of a batch-1 anchor."""
    i, o = shape["in"], shape["out"]
    ctx = [i + g for g in range(1, o)]
    return model.weight_bytes(1) + model.kv_bytes_per_token * (sum(ctx) / len(ctx) + 1)


def fit(model, plat, eng, anchors) -> tuple:
    """Fit Calib from anchors {"prefill","decode","decode_long"}; returns (Calib, band)."""
    b_s = _decode_bytes(model, ANCHOR_SHAPES["decode"])
    b_l = _decode_bytes(model, ANCHOR_SHAPES["decode_long"])
    t_s, t_l = anchors["decode"], anchors["decode_long"]
    x = (t_l - t_s) / (b_l - b_s)                 # seconds per byte
    step_s = t_s - b_s * x
    if x <= 0 or step_s < 0:                      # noise swamped the slope: no overhead term
        x, step_s = t_s / b_s, 0.0
    cal = Calib(eff_c=0.5, eff_b=1.0 / (plat.mem_bw * x), step_s=step_s)
    cal.eff_c = _solve(lambda e: measure(model, plat, eng, Calib(e, cal.eff_b, cal.step_s), "prefill"),
                       anchors["prefill"], 1e-3, 10.0)
    band = BAND_FLOOR
    if "valid" in anchors:
        pv = measure(model, plat, eng, cal, "valid")
        band = max(BAND_FLOOR, 2.0 * abs(pv - anchors["valid"]) / anchors["valid"])
    return cal, band


# ------------------------------------------------------------ predict / gate

def predict(tasks, model, plat, eng, cal, band):
    res = replay(tasks, Engine(Roofline(model, plat, cal), eng))
    lat = list(res["task"].values())
    p50, p95 = pct(lat, 0.5), pct(lat, 0.95)
    return {"p50_s": p50, "p95_s": p95, "band": band,
            "p95_lo_s": p95 * (1 - band), "p95_hi_s": p95 * (1 + band), "calls": res["calls"]}


def read_slo(manifest_text: str) -> float:
    m = re.search(re.escape(ANNOTATION) + r"""\s*:\s*["']?([0-9]*\.?[0-9]+)\s*s?["']?""", manifest_text)
    if not m:
        raise ValueError(f"annotation {ANNOTATION} not found")
    return float(m.group(1))


def verdict(slo_s, pred):
    if pred["p95_hi_s"] <= slo_s:
        return "PASS", 0
    if pred["p95_lo_s"] > slo_s:
        return "FAIL", 1
    return "INCONCLUSIVE", 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("anchors", help="print the microbenchmark shapes to run on the target")
    for name in ("predict", "gate"):
        p = sub.add_parser(name)
        p.add_argument("--trace", required=True)
        p.add_argument("--model", required=True, help="key in cards/models.json")
        p.add_argument("--platform", default="c8i-48xlarge", help="cards/<name>.json")
        p.add_argument("--anchors", required=True,
                       help='JSON {"tiny":s,"prefill":s,"decode":s,"valid":s} measured on target')
        p.add_argument("--max-batch", type=int, default=32)
        p.add_argument("--chunk", type=int, default=2048)
        p.add_argument("--cache-tokens", type=int, default=400000)
        if name == "gate":
            p.add_argument("--manifest", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "anchors":
        print(json.dumps({k: dict(v, metric=ANCHOR_METRIC[k]) for k, v in ANCHOR_SHAPES.items()}, indent=2))
        return 0
    model, plat = model_card(a.model), platform_card(a.platform)
    eng = EngineCard(a.max_batch, a.chunk, a.cache_tokens)
    with open(a.anchors) as f:
        anchors = json.load(f)
    cal, band = fit(model, plat, eng, anchors)
    pred = predict(load_trace(a.trace), model, plat, eng, cal, band)
    out = {k: v for k, v in pred.items() if k != "calls"}
    out["calib"] = vars(cal)
    if a.cmd == "gate":
        with open(a.manifest) as f:
            slo = read_slo(f.read())
        out["slo_p95_s"] = slo
        out["verdict"], rc = verdict(slo, pred)
        print(json.dumps(out, indent=2))
        return rc
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
