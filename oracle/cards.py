"""Inputs shared by the oracle and the truth world: cards and agent traces.

Nothing in here describes efficiency, noise or timing. Cards hold datasheet
facts (parameters, bytes, peaks); traces hold structure (DAG, prefix
segments, output tokens, tool durations).

Trace schema (JSON, version 1):
  {"version": 1, "tasks": [
     {"id": "t0", "arrival_s": 0.0, "steps": [
        {"id": "s0", "kind": "llm", "deps": [],
         "prefix": [["sys", 1500], ["tools", 800], ["u0", 120]], "out": 90},
        {"id": "x0", "kind": "tool", "deps": ["s0"], "dur_s": 0.4}, ...]}]}

`prefix` is the ordered list of message segments (content key, tokens) that
make up the prompt. A gateway (LiteLLM callback) produces it by hashing each
message. Cached tokens are NOT part of the trace: the hit length belongs to
the serving engine and its cache size, so each world recomputes it.
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class ModelCard:
    name: str
    dense_params: float          # params read every token (attention, embeddings, shared)
    expert_params: float         # total routed-expert params (0 for dense models)
    n_experts: int
    top_k: int
    n_layers: int
    d_model: int
    kv_bytes_per_token: int
    bytes_per_param: float

    @property
    def active_params(self) -> float:
        if not self.n_experts:
            return self.dense_params
        return self.dense_params + self.expert_params * self.top_k / self.n_experts

    def weight_bytes(self, batch: int) -> float:
        """Weight bytes streamed by one forward step over `batch` sequences.

        MoE: expected union of experts touched by `batch` independent tokens.
        """
        b = self.bytes_per_param
        if not self.n_experts:
            return b * self.dense_params
        frac = 1.0 - (1.0 - self.top_k / self.n_experts) ** max(batch, 1)
        return b * (self.dense_params + self.expert_params * frac)


@dataclass(frozen=True)
class PlatformCard:
    name: str
    peak_flops: float            # dense bf16 FLOP/s
    mem_bw: float                # bytes/s
    notes: str = ""


@dataclass(frozen=True)
class EngineCard:
    max_batch: int               # vLLM --max-num-seqs
    chunk_tokens: int            # vLLM --max-num-batched-tokens
    cache_tokens: int            # prefix-cache capacity in tokens


def _load(path):
    with open(path) as f:
        return json.load(f)


def model_card(name: str) -> ModelCard:
    d = _load(os.path.join(HERE, "cards", "models.json"))[name]
    d = {k: v for k, v in d.items() if not k.startswith("_")}
    return ModelCard(name=name, **d)


def platform_card(name: str) -> PlatformCard:
    d = _load(os.path.join(HERE, "cards", f"{name}.json"))
    return PlatformCard(name=name, peak_flops=d["peak_flops"], mem_bw=d["mem_bw"],
                        notes=d.get("notes", ""))


# ---------------------------------------------------------------- traces

@dataclass
class Step:
    id: str
    kind: str                    # "llm" | "tool"
    deps: list
    prefix: list = field(default_factory=list)   # [(key, tokens)]
    out: int = 0
    dur_s: float = 0.0

    @property
    def in_tokens(self) -> int:
        return sum(t for _, t in self.prefix)


@dataclass
class Task:
    id: str
    arrival_s: float
    steps: list


def load_trace(path_or_obj) -> list:
    obj = _load(path_or_obj) if isinstance(path_or_obj, str) else path_or_obj
    if obj.get("version") != 1:
        raise ValueError("trace version must be 1")
    tasks = []
    for t in obj["tasks"]:
        steps = [Step(id=s["id"], kind=s["kind"], deps=list(s.get("deps", [])),
                      prefix=[tuple(p) for p in s.get("prefix", [])],
                      out=int(s.get("out", 0)), dur_s=float(s.get("dur_s", 0.0)))
                 for s in t["steps"]]
        ids = {s.id for s in steps}
        for s in steps:
            if s.kind not in ("llm", "tool"):
                raise ValueError(f"{t['id']}/{s.id}: kind {s.kind!r}")
            if s.kind == "llm" and (s.out < 1 or not s.prefix):
                raise ValueError(f"{t['id']}/{s.id}: llm step needs prefix and out>=1")
            for d in s.deps:
                if d not in ids:
                    raise ValueError(f"{t['id']}/{s.id}: unknown dep {d}")
        tasks.append(Task(id=t["id"], arrival_s=float(t["arrival_s"]), steps=steps))
    return tasks


def dump_trace(tasks) -> dict:
    return {"version": 1, "tasks": [
        {"id": t.id, "arrival_s": t.arrival_s, "steps": [
            {"id": s.id, "kind": s.kind, "deps": s.deps, "prefix": [list(p) for p in s.prefix],
             "out": s.out, "dur_s": s.dur_s} for s in t.steps]} for t in tasks]}


def gen_trace(family: str, n_tasks: int, rate_per_s: float | None, seed: int) -> list:
    """Synthetic agent traces. rate None = one task at a time (spaced 1e6 s)."""
    rng = random.Random(seed)
    sys_seg = [("sys", 1400), ("tools", 900)]          # shared by every session
    tasks, t_arr = [], 0.0
    for i in range(n_tasks):
        t_arr = t_arr + (rng.expovariate(rate_per_s) if rate_per_s else 1e6) if i else 0.0
        tid, steps = f"t{i}", []

        def llm(sid, deps, prefix, out):
            steps.append(Step(sid, "llm", deps, list(prefix), out))

        def tool(sid, deps, lo, hi):
            steps.append(Step(sid, "tool", deps, dur_s=rng.uniform(lo, hi)))

        user = (f"{tid}.u", rng.randint(60, 300))
        if family == "react":
            ctx, prev = sys_seg + [user], []
            for k in range(rng.randint(5, 9)):
                sid, out = f"s{k}", rng.randint(40, 220)
                llm(sid, prev, ctx, out)
                tool(f"x{k}", [sid], 0.05, 1.2)
                ctx = ctx + [(f"{tid}.a{k}", out), (f"{tid}.r{k}", rng.randint(80, 900))]
                prev = [f"x{k}"]
            llm("final", prev, ctx, rng.randint(120, 400))
        elif family == "fanout":
            plan_out = rng.randint(100, 250)
            llm("plan", [], sys_seg + [user], plan_out)
            base = sys_seg + [user, (f"{tid}.plan", plan_out)]
            leaves = []
            for j in range(rng.randint(4, 8)):
                sub = base + [(f"{tid}.q{j}", rng.randint(30, 80))]
                prev = ["plan"]
                for k in range(rng.randint(1, 3)):
                    sid, out = f"w{j}.{k}", rng.randint(60, 200)
                    llm(sid, prev, sub, out)
                    tool(f"y{j}.{k}", [sid], 0.1, 0.8)
                    sub = sub + [(f"{tid}.w{j}.{k}", out), (f"{tid}.z{j}.{k}", rng.randint(100, 600))]
                    prev = [f"y{j}.{k}"]
                leaves.append((prev[0], sub[-2:]))
            agg = base + [seg for _, segs in leaves for seg in segs]
            llm("agg", [lid for lid, _ in leaves], agg, rng.randint(200, 450))
        elif family == "rag":
            tool("retrieve", [], 0.2, 0.6)
            docs = (f"{tid}.docs", rng.randint(5000, 11000))
            out1 = rng.randint(150, 400)
            llm("answer", ["retrieve"], sys_seg + [user, docs], out1)
            tool("check", ["answer"], 0.05, 0.3)
            llm("refine", ["check"], sys_seg + [user, docs, (f"{tid}.a", out1),
                                                  (f"{tid}.c", rng.randint(40, 120))],
                rng.randint(60, 200))
        else:
            raise ValueError(f"unknown family {family}")
        tasks.append(Task(tid, t_arr, steps))
    return tasks
