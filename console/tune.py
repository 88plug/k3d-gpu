#!/usr/bin/env python3
"""k3d-gpu tune: search driver for the agent's serving configuration.

Python 3 standard library only. The launcher (scripts/k3d-gpu tune) owns every
cluster action; this file only decides what to try and whether a result counts:

  space   facts about the host and cluster -> the configurations worth trying
  run     successive halving over that space, then a fresh interleaved
          confirmation of the survivors against the default config, then the
          Pareto front and an accept/reject decision written as a profile
  env     a profile -> validated K3D_GPU_AGENT_* overrides for `k3d-gpu agent up`

A trial is one call of an external command (--trial-cmd):
  <trial-cmd> <config.json> <budget.json> <out.json>
It deploys the config, loads it with bench/llm_load.py (optionally under
bench/energy.sh) and writes {"ok": bool, "error": str|null, "runs": [run, ...]}.
A run is {"llm_load": <llm_load.py JSON>, "energy": <energy.sh JSON>|null,
"cpu_seconds": float|null, "quality": {"correct": int, "total": int}|null}.
parse_llm_load() and parse_energy() below are the contract with bench/.

Why successive halving and not a grid: one trial redeploys the model server
(minutes when a GPU compiles the model), and the space on one hybrid host with
an iGPU is about a thousand configs. Successive halving (Karnin et al., ICML
2013; Jamieson & Talwalkar, AISTATS 2016; the inner loop of Hyperband, Li et
al., JMLR 2018) spends short, cheap runs on many configs and long runs only on
the few that survive, so the budget goes where the ranking is still uncertain.
"""

import argparse
import hashlib
import itertools
import json
import math
import os
import random
import re
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

SCHEMA = 1

# objective -> (metric, direction): +1 higher is better, -1 lower is better.
OBJECTIVES = {
    "throughput": ("aggregate_tokens_per_s", +1),
    "ttft": ("ttft_s", -1),
    "joules": ("joules_per_token", -1),
    "cpu-offload": ("tokens_per_cpu_s", +1),
}
# Metrics on the Pareto front (all that a run can report).
FRONT_METRICS = (("aggregate_tokens_per_s", +1), ("ttft_s", -1),
                 ("joules_per_token", -1), ("tokens_per_cpu_s", +1))
# Guardrail per objective: a metric that must not get worse than the default by
# more than --guard-tol (throughput may not buy itself with first-token latency;
# the rest may not buy themselves with single-stream speed).
GUARDRAIL = {
    "throughput": ("ttft_s", -1),
    "ttft": ("tokens_per_s_c1", +1),
    "joules": ("tokens_per_s_c1", +1),
    "cpu-offload": ("tokens_per_s_c1", +1),
}

# ---- configuration: allow-lists ------------------------------------------------
# Names verified against OpenVINO Model Server v2026.4.1 (Makefile:199
# PRODUCT_VERSION) and the OpenVINO commit its versions.mk pins (e213a147):
#   --plugin_config (JSON)        model_server src/cli_parser.cpp:318-321,747-750
#   --max_num_seqs  (default 256) model_server src/graph_export/graph_cli_parser.cpp:44-47
#   --enable_prefix_caching (CLI default "true")                  graph_cli_parser.cpp:52-55
#   --cache_size (GB, 0 = dynamic)                               graph_cli_parser.cpp:60-63
#   PERFORMANCE_HINT          openvino src/inference/include/openvino/runtime/properties.hpp:378
#   INFERENCE_PRECISION_HINT  properties.hpp:280
#   KV_CACHE_PRECISION        properties.hpp:630 (OVMS --kv_cache_precision: "u8 or empty")
#   NUM_STREAMS               properties.hpp:1391
# HETERO:GPU,CPU for text generation: model_server docs/parameters.md:97.
DEVICES = ("CPU", "GPU", "NPU", "HETERO:GPU,CPU")
CORES = (None, "performance", "efficient")
PLUGIN_KEYS = {
    "PERFORMANCE_HINT": ("LATENCY", "THROUGHPUT"),
    "INFERENCE_PRECISION_HINT": ("f32", "f16", "bf16"),
    "KV_CACHE_PRECISION": ("u8", "f16"),
    "NUM_STREAMS": tuple(str(i) for i in range(1, 65)),
}
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
PRECISION_RE = re.compile(r"-(int4|int8|fp16)-ov$")
CONFIG_KEYS = ("device", "model", "cores", "cpus", "plugin_config",
               "max_num_seqs", "cache_size", "enable_prefix_caching")


class ConfigError(ValueError):
    pass


def validate_config(cfg):
    """Return a normalized copy of cfg, or raise ConfigError.

    Every value that reaches a manifest passes through here, so the profile file
    can only ever produce values from the allow-lists above.
    """
    if not isinstance(cfg, dict):
        raise ConfigError("config must be an object")
    if cfg.get("baseline") is True:
        if set(cfg) != {"baseline"}:
            raise ConfigError("the baseline config carries no other keys")
        return {"baseline": True}
    extra = set(cfg) - set(CONFIG_KEYS)
    if extra:
        raise ConfigError(f"unknown config keys: {sorted(extra)}")
    out = {}
    if cfg.get("device") not in DEVICES:
        raise ConfigError(f"device must be one of {DEVICES}")
    out["device"] = cfg["device"]
    model = cfg.get("model")
    if not isinstance(model, str) or not MODEL_RE.match(model):
        raise ConfigError("model must be <org>/<name> using [A-Za-z0-9._-]")
    out["model"] = model
    if cfg.get("cores") not in CORES:
        raise ConfigError("cores must be performance, efficient or null")
    out["cores"] = cfg.get("cores")
    for key, lo, hi in (("cpus", 1, 1024), ("max_num_seqs", 1, 1024), ("cache_size", 0, 512)):
        v = cfg.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise ConfigError(f"{key} must be an integer in [{lo}, {hi}]")
        out[key] = v
    if not isinstance(cfg.get("enable_prefix_caching"), bool):
        raise ConfigError("enable_prefix_caching must be true or false")
    out["enable_prefix_caching"] = cfg["enable_prefix_caching"]
    pc = cfg.get("plugin_config", {})
    if not isinstance(pc, dict):
        raise ConfigError("plugin_config must be an object")
    for k, v in pc.items():
        if k not in PLUGIN_KEYS:
            raise ConfigError(f"plugin_config key {k!r} not allowed (allowed: {sorted(PLUGIN_KEYS)})")
        if v not in PLUGIN_KEYS[k]:
            raise ConfigError(f"plugin_config {k}={v!r} not allowed")
    out["plugin_config"] = dict(sorted(pc.items()))
    return out


def config_key(cfg):
    return json.dumps(cfg, sort_keys=True, separators=(",", ":"))


def config_env(cfg):
    """K3D_GPU_AGENT_* overrides for a validated config ([] for the baseline)."""
    cfg = validate_config(cfg)
    if cfg.get("baseline"):
        return []
    env = [
        ("K3D_GPU_AGENT_DEVICE", cfg["device"]),
        ("K3D_GPU_AGENT_MODEL", cfg["model"]),
        ("K3D_GPU_AGENT_CPUS", str(cfg["cpus"])),
        ("K3D_GPU_AGENT_MAX_NUM_SEQS", str(cfg["max_num_seqs"])),
        ("K3D_GPU_AGENT_CACHE_SIZE", str(cfg["cache_size"])),
        ("K3D_GPU_AGENT_PREFIX_CACHING", "true" if cfg["enable_prefix_caching"] else "false"),
    ]
    if cfg["cores"]:
        env.append(("K3D_GPU_AGENT_CORES", cfg["cores"]))
    if cfg["plugin_config"]:
        env.append(("K3D_GPU_AGENT_PLUGIN_CONFIG", json.dumps(cfg["plugin_config"], separators=(",", ":"))))
    return env


# ---- search space ------------------------------------------------------------------

def model_variants(model, precisions):
    """Sibling repos of an OpenVINO/<name>-<int4|int8|fp16>-ov model, by precision."""
    m = PRECISION_RE.search(model)
    if not m:
        return {"model": model}
    stem = model[: m.start()]
    return {p: f"{stem}-{p}-ov" for p in precisions}


def default_config(facts, device=None):
    """Explicit form of what `k3d-gpu agent up` deploys with no overrides
    (agent_cpus/agent_manifest in scripts/k3d-gpu), optionally on another device.

    Used only to centre the one-factor neighbours; the baseline trial itself is
    {"baseline": true} (no overrides at all), so it is the real default.
    """
    dev = device or facts["default_device"]
    hybrid = facts.get("hybrid")
    if dev == "CPU":
        cpus = hybrid["p"] if hybrid else max(1, facts["node_cpus"] // 2)
    else:
        cpus = 2
    return {
        "device": dev,
        "model": facts["model"],
        "cores": "performance" if hybrid else None,
        "cpus": cpus,
        "plugin_config": {},
        "max_num_seqs": 256,
        "cache_size": 0,
        "enable_prefix_caching": True,
    }


def axes(facts, device):
    """Values each knob may take on a device, from what the host advertises."""
    hybrid = facts.get("hybrid")
    flags = set(facts.get("cpu_flags", []))
    variants = model_variants(facts["model"], facts.get("precisions", ["int4"]))
    a = {"model": sorted(set(variants.values()))}
    if hybrid:
        a["cores"] = ["performance", "efficient"]
    else:
        a["cores"] = [None]
    if device == "CPU":
        if hybrid:
            a["cpus"] = sorted({max(1, hybrid["p"] // 2), hybrid["p"], max(1, hybrid["e"] // 2), hybrid["e"]})
        else:
            n = facts["node_cpus"]
            a["cpus"] = sorted({max(1, n // 4), max(1, n // 2), max(1, n - 2)})
        prec = [None]
        # The CPU plugin runs bf16 on AVX512_BF16/AMX and f16 on AMX-FP16; on
        # anything else (e.g. AVX2 + AVX-VNNI clients) only the f32 default exists.
        if flags & {"avx512_bf16", "amx_bf16"}:
            prec.append("bf16")
        if "amx_fp16" in flags:
            prec.append("f16")
        a["INFERENCE_PRECISION_HINT"] = prec
        a["NUM_STREAMS"] = [None, "1", "2", "4"]
    elif device == "NPU":
        a["cpus"] = [2, 4]
        a["INFERENCE_PRECISION_HINT"] = [None]
        a["NUM_STREAMS"] = [None]
    else:  # GPU, HETERO:GPU,CPU
        a["cpus"] = [2, 4]
        a["INFERENCE_PRECISION_HINT"] = [None, "f32"]
        a["NUM_STREAMS"] = [None]
    a["PERFORMANCE_HINT"] = [None, "LATENCY", "THROUGHPUT"]
    # The x86 CPU plugin already defaults the KV cache to u8 (openvino e213a147,
    # src/plugins/intel_cpu/src/config.h:79), so u8 there is the default under
    # another name and is not an arm. The GPU plugin defaults a PagedAttention
    # model to i8, or u4 with 4-bit weights (src/plugins/intel_gpu/src/runtime/
    # execution_config.cpp:286-300); u8 is a different setting there.
    a["KV_CACHE_PRECISION"] = [None, "u8"] if device in ("GPU", "HETERO:GPU,CPU") else [None]
    # OVMS ignores the continuous-batching knobs on the NPU's stateful pipeline
    # (model_server docs/llm/reference.md:276), so they are not varied there.
    a["max_num_seqs"] = [256] if device == "NPU" else [16, 64, 256]
    a["enable_prefix_caching"] = [True] if device == "NPU" else [True, False]
    a["cache_size"] = [0]
    return a


PLUGIN_AXES = ("PERFORMANCE_HINT", "INFERENCE_PRECISION_HINT", "KV_CACHE_PRECISION", "NUM_STREAMS")


def build(device, choice):
    pc = {k: choice[k] for k in PLUGIN_AXES if choice.get(k) is not None}
    return validate_config({
        "device": device, "model": choice["model"], "cores": choice["cores"],
        "cpus": choice["cpus"], "plugin_config": pc, "max_num_seqs": choice["max_num_seqs"],
        "cache_size": choice["cache_size"], "enable_prefix_caching": choice["enable_prefix_caching"],
    })


def feasible(facts, cfg):
    """A pinned pod must fit its core type: balloons cannot grow past it."""
    hybrid = facts.get("hybrid")
    if not hybrid or cfg["cores"] is None:
        return cfg["cpus"] <= facts["node_cpus"]
    return cfg["cpus"] <= (hybrid["p"] if cfg["cores"] == "performance" else hybrid["e"])


def full_space(facts):
    """Every feasible config in the space (for counting and random sampling)."""
    out = []
    for device in facts["devices"]:
        a = axes(facts, device)
        names = list(a)
        for combo in itertools.product(*(a[n] for n in names)):
            c = build(device, dict(zip(names, combo)))
            if feasible(facts, c):
                out.append(c)
    return out


def sample_space(facts, n0, seed):
    """n0 configs: every one-knob change of the default first, then random fill.

    One-knob neighbours guarantee each device, precision and knob value is tried
    at least once; the random fill finds interactions between knobs.
    """
    space = full_space(facts)
    keys = {config_key(c) for c in space}
    picked, seen = [], set()

    def add(c):
        k = config_key(c)
        if k in keys and k not in seen:
            seen.add(k)
            picked.append(c)

    for device in facts["devices"]:
        a = axes(facts, device)
        dev_default = default_config(facts, "GPU" if device.startswith("HETERO") else device)
        choice = dict(dev_default, **{k: None for k in PLUGIN_AXES})
        centre = {n: (choice.get(n) if choice.get(n) in a[n] else a[n][0]) for n in a}
        add(build(device, centre))
        for n in a:
            for v in a[n]:
                if v != centre[n]:
                    c = dict(centre)
                    c[n] = v
                    add(build(device, c))
    rng = random.Random(seed)
    if len(picked) > n0:
        # Keep the device centres (first of each device), sample the rest.
        centres = [c for i, c in enumerate(picked) if i == 0 or c["device"] != picked[i - 1]["device"]]
        rest = [c for c in picked if c not in centres]
        picked = centres + rng.sample(rest, max(0, n0 - len(centres)))
    else:
        pool = [c for c in space if config_key(c) not in seen]
        picked += rng.sample(pool, min(len(pool), n0 - len(picked)))
    return picked, len(space)


# ---- measurement contract ---------------------------------------------------------

class ContractError(ValueError):
    pass


def parse_llm_load(doc):
    """Validate a bench/llm_load.py JSON document (schema 1) and return its phases."""
    if not isinstance(doc, dict) or not str(doc.get("tool", "")).endswith("bench/llm_load.py"):
        raise ContractError("not an llm_load.py result (tool field)")
    if doc.get("schema") != 1:
        raise ContractError(f"llm_load.py schema {doc.get('schema')!r}, expected 1")
    phases = doc.get("phases")
    if not isinstance(phases, list) or not phases:
        raise ContractError("llm_load.py result has no phases")
    for ph in phases:
        for k in ("concurrency", "requests", "ok", "errors", "wall_s", "output_tokens_total",
                  "aggregate_tokens_per_s", "stats"):
            if k not in ph:
                raise ContractError(f"llm_load.py phase missing {k!r}")
        for m in ("ttft_s", "tokens_per_s"):
            if not isinstance(ph["stats"].get(m), dict) or "median" not in ph["stats"][m] or "p90" not in ph["stats"][m]:
                raise ContractError(f"llm_load.py phase stats missing {m}.median/p90")
    return phases


def parse_energy(doc):
    """Validate a bench/energy.sh --json document; returns joules."""
    if not isinstance(doc, dict):
        raise ContractError("energy.sh result is not an object")
    for k in ("joules", "seconds", "exit_code"):
        if not isinstance(doc.get(k), (int, float)) or isinstance(doc.get(k), bool):
            raise ContractError(f"energy.sh result missing numeric {k!r}")
    if doc["exit_code"] != 0:
        raise ContractError(f"command under energy.sh exited {doc['exit_code']}")
    return float(doc["joules"])


def run_metrics(run, ttft_slo):
    """Scalar metrics of one run. Missing inputs give None, never a guess."""
    phases = parse_llm_load(run["llm_load"])
    errors = sum(int(ph["errors"]) for ph in phases)
    c1 = min(phases, key=lambda ph: ph["concurrency"])
    tokens = sum(ph["output_tokens_total"] or 0 for ph in phases)
    best, best_c = None, None
    for ph in phases:
        tp, p90 = ph["aggregate_tokens_per_s"], ph["stats"]["ttft_s"]["p90"]
        if tp is None or p90 is None or p90 > ttft_slo:
            continue
        if best is None or tp > best:
            best, best_c = tp, ph["concurrency"]
    m = {
        "errors": errors,
        "aggregate_tokens_per_s": best,
        "best_concurrency": best_c,
        "ttft_s": c1["stats"]["ttft_s"]["median"],
        "tokens_per_s_c1": c1["stats"]["tokens_per_s"]["median"],
        "output_tokens": tokens,
        "joules_per_token": None,
        "tokens_per_cpu_s": None,
        "quality": None,
    }
    if run.get("energy") is not None and tokens > 0:
        m["joules_per_token"] = parse_energy(run["energy"]) / tokens
    cpu = run.get("cpu_seconds")
    if isinstance(cpu, (int, float)) and cpu > 0 and tokens > 0:
        m["tokens_per_cpu_s"] = tokens / cpu
    q = run.get("quality")
    if isinstance(q, dict) and isinstance(q.get("total"), int) and q["total"] > 0:
        m["quality"] = q["correct"] / q["total"]
    return m


# ---- statistics ---------------------------------------------------------------------

def spread(values):
    """Range (max - min) of the samples: the conservative noise width with n ~ 5."""
    return max(values) - min(values) if values else math.inf


def mann_whitney_p(better, worse):
    """Exact one-sided Mann-Whitney U p-value that `better` is stochastically larger.

    U counts strict wins only, so a tie counts against `better`. Against the
    exact no-ties null distribution (counted by recursion) that is the
    worst-case tie-breaking, so with ties the p-value errs toward rejecting.
    """
    m, n = len(better), len(worse)
    if not m or not n:
        return 1.0
    u = sum(1 for b in better for w in worse if b > w)
    memo = {}

    def count(u_, m_, n_):  # arrangements of m_ x's and n_ y's with exactly u_ (x > y) pairs
        if u_ < 0:
            return 0
        if m_ == 0 or n_ == 0:
            return 1 if u_ == 0 else 0
        key = (u_, m_, n_)
        if key not in memo:
            memo[key] = count(u_ - n_, m_ - 1, n_) + count(u_, m_, n_ - 1)
        return memo[key]

    total = math.comb(m + n, m)
    # P(U >= u) under H0; U ranges over 0..m*n.
    return sum(count(k, m, n) for k in range(u, m * n + 1)) / total


def summarize(samples):
    v = [x for x in samples if x is not None]
    if not v:
        return {"n": 0, "median": None, "min": None, "max": None, "spread": None}
    return {"n": len(v), "median": statistics.median(v), "min": min(v), "max": max(v), "spread": spread(v)}


def dominates(a, b):
    """a dominates b: no worse on every shared metric, strictly better on one."""
    better = False
    for name, sign in FRONT_METRICS:
        x, y = a.get(name), b.get(name)
        if x is None or y is None:
            continue
        if sign * x < sign * y:
            return False
        if sign * x > sign * y:
            better = True
    return better


def pareto_front(points):
    """Indices of the non-dominated points (each a dict metric -> median)."""
    return [i for i, p in enumerate(points)
            if not any(dominates(q, p) for j, q in enumerate(points) if j != i)]


# ---- evaluators -------------------------------------------------------------------------

class CommandEvaluator:
    """Runs --trial-cmd once per evaluation (see the module docstring)."""

    def __init__(self, cmd, workdir, log=sys.stderr):
        self.cmd = shlex.split(cmd)
        self.workdir = workdir
        self.log = log
        self.calls = 0

    def __call__(self, cfg, budget):
        self.calls += 1
        d = os.path.join(self.workdir, f"trial-{self.calls:04d}")
        os.makedirs(d, exist_ok=True)
        paths = [os.path.join(d, n) for n in ("config.json", "budget.json", "result.json")]
        with open(paths[0], "w") as f:
            json.dump(cfg, f, indent=2)
        with open(paths[1], "w") as f:
            json.dump(budget, f, indent=2)
        rc = subprocess.call(self.cmd + paths)
        try:
            with open(paths[2]) as f:
                res = json.load(f)
        except (OSError, ValueError) as e:
            return {"ok": False, "error": f"trial exited {rc}, no result: {e}", "runs": []}
        if rc != 0 and res.get("ok"):
            res = {"ok": False, "error": f"trial exited {rc}", "runs": res.get("runs", [])}
        return res


class SimEvaluator:
    """Deterministic fake model server for tests: the config sets its speed.

    cost(cfg) -> (tokens_per_s, ttft_s, watts, cpu_fraction, quality); noise is a
    seeded multiplicative factor, so a given (seed, config, call) always repeats.
    Output is shaped exactly like llm_load.py / energy.sh output so the same
    parsing code runs as in a real tune.
    """

    def __init__(self, cost, noise=0.0, seed=0, spikes=None):
        self.cost, self.noise, self.seed = cost, noise, seed
        self.spikes = spikes or {}  # config_key -> {call_index: factor}
        self.calls = {}
        self.trials = 0
        self.request_units = 0

    def _factor(self, key, i, r):
        h = hashlib.sha256(f"{self.seed}|{key}|{i}|{r}".encode()).digest()
        rng = random.Random(h)
        return math.exp(rng.gauss(0.0, self.noise)) if self.noise else 1.0

    def __call__(self, cfg, budget):
        key = config_key(cfg)
        i = self.calls.get(key, 0)
        self.calls[key] = i + 1
        self.trials += 1
        c = self.cost(cfg)
        if c is None:
            return {"ok": False, "error": "server failed to start", "runs": []}
        tps, ttft, watts, cpu_frac, quality = c
        runs = []
        for r in range(budget["repeats"]):
            f = self._factor(key, i, r) * self.spikes.get(key, {}).get(i, 1.0)
            phases = []
            for conc in budget["concurrency"]:
                self.request_units += budget["requests"] * budget["max_tokens"]
                agg = tps * f * min(conc, 4) ** 0.5
                t1 = ttft / f * (1 + 0.1 * (conc - 1))
                ntok = budget["requests"] * budget["max_tokens"]
                phases.append({
                    "concurrency": conc, "requests": budget["requests"], "ok": budget["requests"],
                    "errors": 0, "wall_s": ntok / agg, "output_tokens_total": ntok,
                    "aggregate_tokens_per_s": agg, "requests_per_s": budget["requests"] * agg / ntok,
                    "stats": {"ttft_s": {"median": t1, "p90": t1 * 1.2},
                              "tokens_per_s": {"median": tps * f, "p90": tps * f}},
                })
            secs = sum(p["wall_s"] for p in phases)
            runs.append({
                "llm_load": {"tool": "k3d-gpu/bench/llm_load.py", "schema": 1, "phases": phases},
                "energy": {"joules": watts * secs, "seconds": secs, "exit_code": 0},
                "cpu_seconds": cpu_frac * secs,
                "quality": {"correct": round(quality * 5), "total": 5},
            })
        return {"ok": True, "error": None, "runs": runs}


# ---- the search -------------------------------------------------------------------------

DEFAULT_RUNGS = (
    {"requests": 4, "max_tokens": 64, "repeats": 1},
    {"requests": 8, "max_tokens": 128, "repeats": 2},
    {"requests": 16, "max_tokens": 128, "repeats": 3},
)
FINAL = {"requests": 16, "max_tokens": 128, "repeats": 1}
NO_SPREAD = 1e9


def concurrency_for(objective, levels):
    return list(levels) if objective == "throughput" else [1]


def evaluate(evaluator, cfg, budget, ttft_slo):
    """-> (ok, error, list of per-run metric dicts)."""
    res = evaluator(cfg, budget)
    if not res.get("ok"):
        return False, res.get("error") or "trial failed", []
    try:
        ms = [run_metrics(r, ttft_slo) for r in res.get("runs", [])]
    except (ContractError, KeyError, TypeError) as e:
        return False, f"result breaks the bench contract: {e}", []
    if not ms:
        return False, "trial returned no runs", []
    if any(m["errors"] for m in ms):
        return False, f"{sum(m['errors'] for m in ms)} failed request(s)", ms
    return True, None, ms


def score(ms, metric, sign):
    v = [m[metric] for m in ms if m.get(metric) is not None]
    return sign * statistics.median(v) if v else None


def search(evaluator, configs, objective, *, eta=3, n_confirm=5, margin=0.05, k=1.0,
           alpha=0.05, guard_tol=0.25, ttft_slo=10.0, levels=(1, 4, 8), rungs=DEFAULT_RUNGS,
           log=None):
    """Successive halving, then interleaved confirmation against the baseline.

    Returns a result dict (see write_profile for the persisted shape).
    """
    metric, sign = OBJECTIVES[objective]
    say = log or (lambda *_: None)
    conc = concurrency_for(objective, levels)
    baseline = {"baseline": True}
    alive = [validate_config(c) for c in configs if not validate_config(c).get("baseline")]
    ledger = []

    for r, rung in enumerate(rungs):
        budget = dict(rung, concurrency=conc)
        scored, entries = [], []
        for cfg in [baseline] + alive:
            ok, err, ms = evaluate(evaluator, cfg, budget, ttft_slo)
            s = score(ms, metric, sign) if ok else None
            entries.append({"config": cfg, "ok": ok and s is not None, "error": err,
                            "score": None if s is None else sign * s})
            if cfg is baseline:
                if not ok or s is None:
                    raise RuntimeError(f"the default config failed in rung {r}: {err or 'no ' + metric}"
                                       " — nothing to compare against")
                continue
            if ok and s is not None:
                scored.append((s, cfg))
        scored.sort(key=lambda t: -t[0])
        last = r == len(rungs) - 1
        keep = len(scored) if last else max(1, math.ceil(len(scored) / eta))
        alive = [c for _, c in scored[:keep]]
        ledger.append({"rung": r, "budget": budget, "entries": entries,
                       "survivors": [config_key(c) for c in alive]})
        say(f"rung {r}: {len(entries) - 1} configs + default, {len(scored)} ran, {len(alive)} survive")
        if len(alive) <= 1 and not last:
            # Nothing left to rank; the remaining rungs would only re-measure.
            break

    res = _confirm(evaluator, baseline, alive, objective, n_confirm=n_confirm, margin=margin, k=k,
                   alpha=alpha, guard_tol=guard_tol, ttft_slo=ttft_slo, conc=conc, say=say)
    res["criteria"]["eta"] = eta
    res["ledger"] = ledger
    return res


def _confirm(evaluator, baseline, alive, objective, *, n_confirm, margin, k, alpha, guard_tol,
             ttft_slo, conc, say, deadline=None, clock=time.monotonic):
    """Fresh interleaved runs of baseline + alive, then the acceptance gates.

    baseline is the config every candidate must beat ({"baseline": true} for
    the real default, or the incumbent). With a deadline, no trial starts after
    it; a confirmation cut short returns None (nothing is decided)."""
    metric, sign = OBJECTIVES[objective]
    gmetric, gsign = GUARDRAIL[objective]
    # Bonferroni over m finalists asks each for p <= alpha/m, but n runs against
    # n give an exact Mann-Whitney p no smaller than 1/C(2n, n). Confirm only the
    # best m the test can resolve; the rest could never be accepted.
    max_finalists = max(1, math.floor(alpha * math.comb(2 * n_confirm, n_confirm)))
    if len(alive) > max_finalists:
        say(f"confirming the top {max_finalists} of {len(alive)} survivors "
            f"(more cannot pass p <= {alpha}/m with {n_confirm} runs each)")
        alive = alive[:max_finalists]

    # Confirmation: fresh runs only (no rung data: the survivors were selected on
    # it, so reusing it would bias them upward), interleaved and rotated so drift
    # and thermal state hit every config alike.
    finalists = [baseline] + alive
    samples = {config_key(c): [] for c in finalists}
    fails = {config_key(c): None for c in finalists}
    budget = dict(FINAL, concurrency=conc)
    for i in range(n_confirm):
        order = finalists[i % len(finalists):] + finalists[: i % len(finalists)]
        for cfg in order:
            key = config_key(cfg)
            if fails[key]:
                continue
            if deadline is not None and clock() >= deadline:
                say("budget ran out during confirmation: nothing decided")
                return None
            ok, err, ms = evaluate(evaluator, cfg, budget, ttft_slo)
            if not ok:
                fails[key] = err
                if cfg is baseline:
                    raise RuntimeError(f"the {'default' if cfg.get('baseline') else 'incumbent'} config "
                                       f"failed during confirmation: {err}")
                continue
            samples[key].extend(ms)

    def summary(key):
        ms = samples[key]
        names = ("aggregate_tokens_per_s", "ttft_s", "tokens_per_s_c1", "joules_per_token",
                 "tokens_per_cpu_s", "quality")
        return {n: summarize([m[n] for m in ms]) for n in names} | {
            "best_concurrency": statistics.mode([m["best_concurrency"] for m in ms if m["best_concurrency"]] or [None])}

    bkey = config_key(baseline)
    base_sum = summary(bkey)
    base_obj = [m[metric] for m in samples[bkey] if m[metric] is not None]
    if len(base_obj) < n_confirm:
        raise RuntimeError(f"the default config produced {len(base_obj)}/{n_confirm} {metric} samples"
                           + (" (joules needs --energy; cpu-offload needs pod cpu.stat)" if not base_obj else ""))
    candidates = [c for c in alive if not fails[config_key(c)]]
    alpha_each = alpha / max(1, len(candidates))  # Bonferroni over the finalists
    verdicts = []
    for cfg in candidates:
        key = config_key(cfg)
        s = summary(key)
        obj = [m[metric] for m in samples[key] if m[metric] is not None]
        v = {"config": cfg, "summary": s, "reasons": []}
        if len(obj) < n_confirm:
            v["reasons"].append(f"only {len(obj)}/{n_confirm} {metric} samples")
            verdicts.append(v)
            continue
        mb, mc = statistics.median(base_obj), statistics.median(obj)
        diff = sign * (mc - mb)
        width = max(spread(obj), spread(base_obj))
        # NO_SPREAD stands in for "infinite" so the profile stays strict JSON.
        v["gain"] = diff / abs(mb) if mb else NO_SPREAD
        v["separation"] = NO_SPREAD if width == 0 and diff > 0 else (diff / width if width else 0.0)
        v["p_value"] = mann_whitney_p([sign * x for x in obj], [sign * x for x in base_obj])
        if v["gain"] < margin:
            v["reasons"].append(f"gain {v['gain']:+.1%} < margin {margin:.0%}")
        if v["separation"] < k:
            v["reasons"].append(f"separation {v['separation']:.2f} < k={k} (difference within run-to-run spread)")
        if v["p_value"] > alpha_each:
            v["reasons"].append(f"Mann-Whitney p={v['p_value']:.4f} > {alpha_each:.4f}")
        gb, gc = base_sum[gmetric]["median"], s[gmetric]["median"]
        if gb is not None and gc is not None and gsign * (gc - gb) < -guard_tol * abs(gb):
            v["reasons"].append(f"guardrail {gmetric}: {gc:.4g} vs default {gb:.4g} (worse by > {guard_tol:.0%})")
        qb, qc = base_sum["quality"]["median"], s["quality"]["median"]
        if qb is not None and (qc is None or qc < qb):
            v["reasons"].append(f"guardrail quality: {qc} < default {qb}")
        v["accepted"] = not v["reasons"]
        verdicts.append(v)

    points = [{n: base_sum[n]["median"] for n, _ in FRONT_METRICS}] + [
        {n: v["summary"][n]["median"] for n, _ in FRONT_METRICS} for v in verdicts]
    front_idx = pareto_front(points)
    labelled = [baseline] + [v["config"] for v in verdicts]
    front = [{"config": labelled[i], "medians": points[i]} for i in front_idx]
    accepted = [v for v in verdicts if v.get("accepted")]
    winner = max(accepted, key=lambda v: sign * v["summary"][metric]["median"]) if accepted else None
    return {
        "objective": objective, "metric": metric,
        "criteria": {"margin": margin, "k": k, "alpha": alpha, "alpha_per_candidate": alpha_each,
                     "n_confirm": n_confirm, "guardrail": gmetric, "guard_tol": guard_tol,
                     "ttft_slo_s": ttft_slo},
        "decision": "tuned" if winner else "default",
        "winner": winner["config"] if winner else None,
        "baseline": base_sum,
        "verdicts": verdicts,
        "pareto": front,
        "confirm_failures": {k_: e for k_, e in fails.items() if e},
    }


# ---- incremental tuning: neighbours of the incumbent ----------------------------------
# A run starts from the host's current best (the profile's winner, else the
# default), screens every untried one-knob neighbour with one short trial, and
# confirms only the best screener against the incumbent with the same gates as
# the full search. The incumbent stays unless a challenger passes them.

SCREEN = DEFAULT_RUNGS[0]


def centre(facts, device):
    """Explicit form of the default on a device, restricted to its axes."""
    a = axes(facts, device)
    dev_default = default_config(facts, "GPU" if device.startswith("HETERO") else device)
    choice = dict(dev_default, **{k: None for k in PLUGIN_AXES})
    return {n: (choice.get(n) if choice.get(n) in a[n] else a[n][0]) for n in a}


def as_choice(facts, cfg):
    """A validated config -> (device, axis choice) on that device's axes."""
    if cfg.get("baseline"):
        dev = facts["default_device"]
        return dev, centre(facts, dev)
    ch = {n: cfg.get(n) for n in ("model", "cores", "cpus", "max_num_seqs", "cache_size",
                                  "enable_prefix_caching")}
    ch.update({k: cfg["plugin_config"].get(k) for k in PLUGIN_AXES})
    return cfg["device"], ch


def neighbours(facts, incumbent):
    """Feasible configs one knob away from the incumbent: each other value of
    each axis on its device, and each other device at its default."""
    device, ch = as_choice(facts, incumbent)
    a = axes(facts, device)
    out, seen = [], {config_key(build(device, ch))}

    def add(c):
        k = config_key(c)
        if feasible(facts, c) and k not in seen:
            seen.add(k)
            out.append(c)

    for n in a:
        for v in a[n]:
            if v != ch.get(n):
                add(build(device, dict(ch, **{n: v})))
    for d in facts["devices"]:
        if d != device:
            add(build(d, centre(facts, d)))
    return out


def incremental(evaluator, facts, incumbent, objective, *, budget_s=600.0, tried=(), n_confirm=5,
                margin=0.05, k=1.0, alpha=0.05, guard_tol=0.25, ttft_slo=10.0, levels=(1, 4, 8),
                clock=time.monotonic, log=None):
    """One incremental step. Returns a result dict; "decision" is
    "challenger" (a neighbour passed every gate: it is the new incumbent),
    "incumbent" (kept) or "converged" (every neighbour already screened)."""
    metric, sign = OBJECTIVES[objective]
    say = log or (lambda *_: None)
    deadline = clock() + budget_s
    conc = concurrency_for(objective, levels)
    tried = set(tried)
    cands = [c for c in neighbours(facts, incumbent) if config_key(c) not in tried]
    out = {"objective": objective, "metric": metric, "incumbent": incumbent, "decision": "incumbent",
           "winner": None, "screened": [], "challenger": None, "confirm": None, "budget_s": budget_s}
    if not cands:
        say("every neighbour of the incumbent was already screened: converged")
        out["decision"] = "converged"
        return out
    # cold=False: a trial whose manifest is already deployed (the incumbent, as a
    # rule) is measured on the running pod instead of after a restart.
    budget = dict(SCREEN, concurrency=conc, cold=False)
    t0 = clock()
    ok, err, ms = evaluate(evaluator, incumbent, budget, ttft_slo)
    inc_score = score(ms, metric, sign) if ok else None
    if inc_score is None:
        raise RuntimeError(f"the incumbent failed its screening trial: {err or 'no ' + metric}")
    trial_s = max(clock() - t0, 1e-9)
    # A confirmation trial is FINAL-sized (8x the tokens of a screen) plus a
    # deploy; reserve 2 x n_confirm of them at twice a screen's wall time.
    reserve = 2 * n_confirm * 2 * trial_s
    best = None
    for c in cands:
        if clock() + trial_s > deadline - reserve:
            say(f"screening stopped at {len(out['screened'])}/{len(cands)} neighbours to keep time to confirm")
            break
        ok, err, ms = evaluate(evaluator, c, budget, ttft_slo)
        sc = score(ms, metric, sign) if ok else None
        out["screened"].append({"config": c, "ok": sc is not None, "error": err,
                                "score": None if sc is None else sign * sc})
        # score() is sign * median, so higher is better for every objective.
        if sc is not None and sc > inc_score + margin * abs(inc_score):
            if best is None or sc > best[0]:
                best = (sc, c)
    out["incumbent_screen"] = sign * inc_score
    if best is None:
        say(f"no neighbour beat the incumbent by {margin:.0%} in screening: incumbent kept")
        return out
    out["challenger"] = best[1]
    say("confirming the best screener against the incumbent: "
        + " ".join(f"{k_}={v}" for k_, v in config_env(best[1])))
    res = _confirm(evaluator, incumbent, [best[1]], objective, n_confirm=n_confirm, margin=margin, k=k,
                   alpha=alpha, guard_tol=guard_tol, ttft_slo=ttft_slo, conc=conc, say=say,
                   deadline=deadline, clock=clock)
    if res is None:
        out["decision"] = "budget"
        return out
    out["confirm"] = res
    if res["winner"] is not None:
        out["decision"], out["winner"] = "challenger", res["winner"]
    return out


# ---- profiles -------------------------------------------------------------------------

def write_profile(path, result, fingerprint, inputs, facts):
    prof = dict(result, schema=SCHEMA, fingerprint=fingerprint, fingerprint_inputs=inputs,
                facts=facts, created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".tune-")
    with os.fdopen(fd, "w") as f:
        json.dump(prof, f, indent=2, default=str, allow_nan=False)
        f.write("\n")
    os.replace(tmp, path)  # atomic: a crash mid-write never leaves half a profile


def profile_env(path, fingerprint):
    """Overrides from a profile, or [] when it does not apply to this host."""
    with open(path) as f:
        prof = json.load(f)
    if prof.get("schema") != SCHEMA or prof.get("fingerprint") != fingerprint:
        return []
    if prof.get("decision") != "tuned" or not prof.get("winner"):
        return []
    return config_env(prof["winner"])


# ---- quality probe ----------------------------------------------------------------------
# Exact-answer prompts at temperature 0. A coarse check, but enough to stop a
# lower-precision model sibling or precision hint from winning on speed while
# answering wrong.
QUALITY_PROBES = (
    ("What is 17 multiplied by 23? Reply with the number only.", "391"),
    ("What is the capital of Australia? Reply with one word.", "canberra"),
    ("How many days are in a leap year? Reply with the number only.", "366"),
    ("What is the chemical symbol for gold? Reply with the symbol only.", "au"),
    ("What is 1000 minus 357? Reply with the number only.", "643"),
)


def quality_probe(base_url, model, timeout=300):
    correct = 0
    for prompt, want in QUALITY_PROBES:
        body = json.dumps({
            "model": model, "temperature": 0, "max_tokens": 32,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = json.load(resp)["choices"][0]["message"].get("content") or ""
        except (OSError, ValueError, KeyError, IndexError):
            text = ""
        correct += want in text.lower()
    return {"correct": correct, "total": len(QUALITY_PROBES)}


# ---- CLI -------------------------------------------------------------------------------

def load(path):
    with open(path) as f:
        return json.load(f)


def cmd_facts(a):
    hybrid = {"p": a.p_cpus, "e": a.e_cpus} if a.p_cpus and a.e_cpus else None
    devices = [d for d in a.devices.split(",") if d]
    if "GPU" in devices and a.hetero:
        devices.append("HETERO:GPU,CPU")
    precisions = [p for p in a.precisions.split(",") if p]
    for d in devices:
        if d not in DEVICES:
            raise SystemExit(f"tune.py: unknown device {d!r}")
    if not MODEL_RE.match(a.model):
        raise SystemExit(f"tune.py: bad model id {a.model!r}")
    print(json.dumps({
        "devices": devices, "default_device": a.default_device, "hybrid": hybrid,
        "node_cpus": a.node_cpus, "cpu_flags": sorted(f for f in a.cpu_flags.split(",") if f),
        "model": a.model, "precisions": precisions,
    }, indent=2))


def cmd_space(a):
    facts = load(a.facts)
    picked, total = sample_space(facts, a.n0, a.seed)
    if a.json:
        print(json.dumps(picked, indent=2))
        return
    print(f"search space: {total} configs on this host; first rung tries {len(picked)} + the default")
    for c in picked:
        print("  " + " ".join(f"{k}={v}" for k, v in config_env(c)))


def cmd_run(a):
    facts = load(a.facts)
    picked, total = sample_space(facts, a.n0, a.seed)
    say = lambda s: print(f"tune: {s}", file=sys.stderr)  # noqa: E731
    say(f"{total} configs in the space; successive halving from {len(picked)} (eta={a.eta})")
    workdir = a.workdir or tempfile.mkdtemp(prefix="k3d-gpu-tune-")
    ev = CommandEvaluator(a.trial_cmd, workdir)
    res = search(ev, picked, a.objective, eta=a.eta, n_confirm=a.n, margin=a.margin, k=a.k,
                 alpha=a.alpha, guard_tol=a.guard_tol, ttft_slo=a.ttft_slo,
                 levels=[int(x) for x in a.concurrency.split(",")], log=say)
    res["space_size"], res["trials"], res["workdir"] = total, ev.calls, workdir
    write_profile(a.out, res, a.fingerprint, json.loads(a.fingerprint_inputs), facts)
    print_summary(res)
    return 0


def load_profile(path, fingerprint):
    """The stored profile for this host, or None (missing, other host, old schema)."""
    try:
        prof = load(path)
    except (OSError, ValueError):
        return None
    if prof.get("schema") != SCHEMA or prof.get("fingerprint") != fingerprint:
        return None
    return prof


def step_profile(prev, res, now):
    """Profile after one incremental step: the incumbent (winner), and per
    incumbent the neighbours already screened against it."""
    prof = dict(prev or {})
    inc = res["incumbent"]
    screened = dict(prof.get("screened") or {})
    done = screened.setdefault(config_key(inc), [])
    for e in res["screened"]:
        k = config_key(e["config"])
        if k not in done:
            done.append(k)
    history = list(prof.get("history") or [])
    history.append({"at": now, "incumbent": inc, "decision": res["decision"],
                    "challenger": res["challenger"], "screened": len(res["screened"]),
                    "incumbent_screen": res.get("incumbent_screen")})
    if res["decision"] == "challenger":
        prof["winner"], prof["decision"] = res["winner"], "tuned"
        screened.setdefault(config_key(res["winner"]), [])
    elif not prof.get("decision"):
        prof["decision"], prof["winner"] = ("default", None) if inc.get("baseline") else ("tuned", inc)
    prof.update(objective=res["objective"], metric=res["metric"], screened=screened,
                history=history[-50:], last_step=res)
    return prof


def cmd_step(a):
    facts = load(a.facts)
    say = lambda s: print(f"tune: {s}", file=sys.stderr)  # noqa: E731
    prev = load_profile(a.out, a.fingerprint)
    if prev and prev.get("objective") not in (None, a.objective):
        say(f"stored profile was tuned for {prev['objective']}: starting from the default for {a.objective}")
        prev = None
    inc = prev["winner"] if prev and prev.get("decision") == "tuned" and prev.get("winner") else {"baseline": True}
    inc = validate_config(inc)
    tried = ((prev or {}).get("screened") or {}).get(config_key(inc), [])
    say("incumbent: " + (" ".join(f"{k}={v}" for k, v in config_env(inc)) or "the default")
        + f"; {len(tried)} neighbour(s) already screened; budget {a.budget:g} min")
    workdir = a.workdir or tempfile.mkdtemp(prefix="k3d-gpu-tune-")
    ev = CommandEvaluator(a.trial_cmd, workdir)
    res = incremental(ev, facts, inc, a.objective, budget_s=a.budget * 60, tried=tried, n_confirm=a.n,
                      margin=a.margin, k=a.k, alpha=a.alpha, guard_tol=a.guard_tol, ttft_slo=a.ttft_slo,
                      levels=[int(x) for x in a.concurrency.split(",")], log=say)
    res["trials"], res["workdir"] = ev.calls, workdir
    prof = step_profile(prev, res, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    write_profile(a.out, prof, a.fingerprint, json.loads(a.fingerprint_inputs), facts)
    print_step(res)
    return 0


def print_step(res):
    m = res["metric"]
    inc = " ".join(f"{k}={v}" for k, v in config_env(res["incumbent"])) or "default"
    print(f"objective {res['objective']} ({m}); incumbent {inc}; screen {fmt(res.get('incumbent_screen'))}")
    for e in res["screened"]:
        name = " ".join(f"{k}={v}" for k, v in config_env(e["config"]))
        print(f"  screen {fmt(e['score']) if e['ok'] else 'failed'}  {name}")
    if res.get("confirm"):
        print_summary(res["confirm"], ref="incumbent", final=False)
    print(f"decision: {res['decision']} ({res['trials']} trials)")


def fmt(x):
    return "-" if x is None else f"{x:.4g}"


def print_summary(res, ref="default", final=True):
    m = res["metric"]
    b = res["baseline"][m]
    print(f"objective {res['objective']} ({m}); {ref} median {fmt(b['median'])} "
          f"[{fmt(b['min'])}..{fmt(b['max'])}] n={b['n']}")
    for v in res["verdicts"]:
        s = v["summary"][m]
        tag = "ACCEPT" if v.get("accepted") else "reject"
        print(f"  {tag} median {fmt(s['median'])} [{fmt(s['min'])}..{fmt(s['max'])}] "
              f"gain {v.get('gain', 0):+.1%} sep {v.get('separation', 0):.2f} p {v.get('p_value', 1):.4f}  "
              + " ".join(f"{k}={x}" for k, x in config_env(v["config"])))
        for r in v["reasons"]:
            print(f"      - {r}")
    print("pareto front (aggregate tok/s, ttft s, J/token, tokens/CPU-s):")
    for p in res["pareto"]:
        md = p["medians"]
        name = "default" if p["config"].get("baseline") else " ".join(f"{k}={x}" for k, x in config_env(p["config"]))
        print(f"  {fmt(md['aggregate_tokens_per_s'])} {fmt(md['ttft_s'])} {fmt(md['joules_per_token'])} "
              f"{fmt(md['tokens_per_cpu_s'])}  {name}")
    if final:
        why = "" if res["winner"] else " (no config beat the default by the pre-registered criteria)"
        print(f"decision: {res['decision']}{why}")


def cmd_env(a):
    pairs = profile_env(a.profile, a.fingerprint) if a.profile else config_env(load(a.config))
    for k, v in pairs:
        print(f"{k}={v}")


def cmd_show(a):
    prof = load(a.profile)
    print(f"profile {a.profile}\nfingerprint {prof.get('fingerprint')} created {prof.get('created_at')}")
    if "verdicts" in prof:
        print_summary(prof)
    else:
        win = prof.get("winner")
        print(f"decision: {prof.get('decision')}; incumbent "
              + (" ".join(f"{k}={v}" for k, v in config_env(win)) if win else "the default"))
        for h in prof.get("history") or []:
            print(f"  {h['at']} {h['decision']}: {h['screened']} neighbour(s) screened")


def cmd_quality(a):
    print(json.dumps(quality_probe(a.base_url, a.model)))


def cmd_assemble(a):
    """One run record from the files a trial wrote (missing optional files -> null)."""
    def opt(p):
        return load(p) if p and os.path.exists(p) else None
    cpu = None
    if a.cpu_before is not None and a.cpu_after is not None:
        cpu = (a.cpu_after - a.cpu_before) / 1e6  # cpu.stat usage_usec
    print(json.dumps({"llm_load": load(a.llm_load), "energy": opt(a.energy),
                      "cpu_seconds": cpu, "quality": opt(a.quality)}))


def cmd_budget(a):
    """Budget JSON -> "<requests> <max_tokens> <repeats> <concurrency csv> <cold 1|0>" for the shell."""
    b = load(a.budget)
    vals = [b["requests"], b["max_tokens"], b["repeats"]]
    if not all(isinstance(v, int) and 0 < v <= 100000 for v in vals) or \
            not all(isinstance(c, int) and 0 < c <= 1024 for c in b["concurrency"]):
        raise ContractError("budget values must be positive integers")
    if not isinstance(b.get("cold", True), bool):
        raise ContractError("budget cold must be true or false")
    print(*vals, ",".join(str(c) for c in b["concurrency"]), 1 if b.get("cold", True) else 0)


def cmd_trial_result(a):
    """Trial result file from the run records a trial collected (one JSON per line)."""
    runs = []
    if a.runs and os.path.exists(a.runs):
        with open(a.runs) as f:
            runs = [json.loads(line) for line in f if line.strip()]
    res = {"ok": a.error is None and bool(runs), "error": a.error, "runs": runs}
    if a.error is None and not runs:
        res["error"] = "no runs recorded"
    with open(a.out, "w") as f:
        json.dump(res, f)


def main(argv=None):
    p = argparse.ArgumentParser(prog="tune.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("facts", help="host/cluster facts -> JSON")
    f.add_argument("--devices", required=True)
    f.add_argument("--default-device", required=True, choices=["CPU", "GPU", "NPU"])
    f.add_argument("--hetero", action="store_true")
    f.add_argument("--p-cpus", type=int, default=0)
    f.add_argument("--e-cpus", type=int, default=0)
    f.add_argument("--node-cpus", type=int, required=True)
    f.add_argument("--cpu-flags", default="")
    f.add_argument("--model", required=True)
    f.add_argument("--precisions", default="int4,int8,fp16")
    f.set_defaults(fn=cmd_facts)

    s = sub.add_parser("space", help="print the configs the first rung would try")
    s.add_argument("--facts", required=True)
    s.add_argument("--n0", type=int, default=27)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_space)

    r = sub.add_parser("run", help="successive halving + confirmation; writes a profile")
    r.add_argument("--facts", required=True)
    r.add_argument("--trial-cmd", required=True)
    r.add_argument("--objective", choices=sorted(OBJECTIVES), default="throughput")
    r.add_argument("--out", required=True)
    r.add_argument("--fingerprint", required=True)
    r.add_argument("--fingerprint-inputs", default="{}")
    r.add_argument("--workdir", default=None)
    r.add_argument("--n0", type=int, default=27)
    r.add_argument("--eta", type=int, default=3)
    r.add_argument("--n", type=int, default=5, help="confirmation runs per finalist (>= 5)")
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--margin", type=float, default=0.05)
    r.add_argument("--k", type=float, default=1.0)
    r.add_argument("--alpha", type=float, default=0.05)
    r.add_argument("--guard-tol", type=float, default=0.25)
    r.add_argument("--ttft-slo", type=float, default=10.0)
    r.add_argument("--concurrency", default="1,4,8")
    r.set_defaults(fn=cmd_run)

    st = sub.add_parser("step", help="incremental: screen the incumbent's neighbours, confirm the best")
    st.add_argument("--facts", required=True)
    st.add_argument("--trial-cmd", required=True)
    st.add_argument("--objective", choices=sorted(OBJECTIVES), default="throughput")
    st.add_argument("--out", required=True, help="profile: read for the incumbent, rewritten after")
    st.add_argument("--fingerprint", required=True)
    st.add_argument("--fingerprint-inputs", default="{}")
    st.add_argument("--workdir", default=None)
    st.add_argument("--budget", type=float, default=10.0, help="minutes (default 10)")
    st.add_argument("--n", type=int, default=5, help="confirmation runs per side (>= 5)")
    st.add_argument("--margin", type=float, default=0.05)
    st.add_argument("--k", type=float, default=1.0)
    st.add_argument("--alpha", type=float, default=0.05)
    st.add_argument("--guard-tol", type=float, default=0.25)
    st.add_argument("--ttft-slo", type=float, default=10.0)
    st.add_argument("--concurrency", default="1,4,8")
    st.set_defaults(fn=cmd_step)

    e = sub.add_parser("env", help="K3D_GPU_AGENT_* lines for a profile or a config")
    g = e.add_mutually_exclusive_group(required=True)
    g.add_argument("--profile")
    g.add_argument("--config")
    e.add_argument("--fingerprint", default="")
    e.set_defaults(fn=cmd_env)

    sh = sub.add_parser("show")
    sh.add_argument("profile")
    sh.set_defaults(fn=cmd_show)

    q = sub.add_parser("quality", help="exact-answer probe against /chat/completions")
    q.add_argument("--base-url", required=True)
    q.add_argument("--model", required=True)
    q.set_defaults(fn=cmd_quality)

    asm = sub.add_parser("assemble-run")
    asm.add_argument("--llm-load", required=True)
    asm.add_argument("--energy")
    asm.add_argument("--quality")
    asm.add_argument("--cpu-before", type=float)
    asm.add_argument("--cpu-after", type=float)
    asm.set_defaults(fn=cmd_assemble)

    b = sub.add_parser("budget")
    b.add_argument("budget")
    b.set_defaults(fn=cmd_budget)

    tr = sub.add_parser("trial-result")
    tr.add_argument("--out", required=True)
    tr.add_argument("--runs")
    tr.add_argument("--error")
    tr.set_defaults(fn=cmd_trial_result)

    a = p.parse_args(argv)
    if a.cmd in ("run", "step") and a.n < 5:
        p.error("--n must be >= 5 (the acceptance criteria need at least five runs per side)")
    try:
        return a.fn(a) or 0
    except (ConfigError, ContractError, RuntimeError) as err:
        print(f"tune.py: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
