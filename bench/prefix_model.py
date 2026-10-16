#!/usr/bin/env python3
"""prefix_model.py: predict and test OVMS prefix (KV block) reuse for agents.

Python 3 standard library only. Models what OpenVINO Model Server 2026.4.1 does
with enable_prefix_caching (OpenVINO GenAI commit ab01d177, the one OVMS pins),
predicts time to first token (TTFT) as a function of prefix share, sizes
cache_size per device, and writes and scores the pre-registered live probes
that run through bench/agent_trace.py. See docs/agent-prefix-reuse.md.

  predict    TTFT ratio warm/cold vs prefix share, from FLOP priors or a fit
  size       cache_size (GB) for S sessions x C context tokens on a device
  gen-probe  write agent_trace.py traces for probes P0..P3
  analyze    fit P0 (cold), score P1..P3 against the pre-registered criteria

Mechanism (verified in source, see the doc for file:line):
  * KV lives in blocks of 32 tokens on CPU, 16 on GPU.
  * A block's hash covers its tokens plus the hash of the block before it, so
    a prompt reuses the longest run of cached blocks starting at token 0. One
    changed token at position p caps reuse at floor(p / block) * block.
  * A fully cached prompt still recomputes its last token.
  * Finished requests leave their blocks in an LRU store. Those blocks count as
    free, so with cache_size=0 (dynamic) the pool only grows to what the live
    requests need, and new requests overwrite the least recently used cached
    blocks before the pool grows. A static cache_size keeps that many GB.
"""

import argparse
import json
import math
import random
import statistics
import sys

# ---------------------------------------------------------------- constants
BLOCK = {"CPU": 32, "GPU": 16}  # kv_cache_manager.hpp: cpu_block_size=32, gpu_block_size=16
GROWTH_TOKENS = 256             # scheduler.hpp: m_cache_growth_num_tokens
INITIAL_MULTIPLIER = 2          # scheduler.hpp: m_kv_blocks_initial_multiplier
GIB = 1024 ** 3                 # cache_orchestrator.hpp: cache_size * 1024^3

# Qwen3-4B (OpenVINO/Qwen3-4B-int4-ov config.json): 36 layers, 8 KV heads, head_dim 128.
QWEN3_4B = {"layers": 36, "kv_heads": 8, "head_dim": 128, "q_heads": 32,
            "nonemb_params": 3.63e9}
# Default KV element width per device for this model (bits). CPU plugin config.h:
# kvCachePrecision = u8. GPU execution_config.cpp: PagedAttention model with
# 4-bit weights -> u4 KV. Quantization scales add a little on top (see doc).
KV_BITS = {"CPU": 8, "GPU": 4}


def kv_bytes_per_token(device="CPU", model=QWEN3_4B, bits=None, overhead=0.0):
    """K and V bytes per token across all layers, plus a fractional overhead."""
    b = KV_BITS[device] if bits is None else bits
    raw = 2 * model["layers"] * model["kv_heads"] * model["head_dim"] * b / 8
    return raw * (1 + overhead)


def cache_size_gb(sessions, context_tokens, bytes_per_token, headroom=1.0):
    """Smallest whole-GB cache_size holding `sessions` contexts of `context_tokens`."""
    need = sessions * context_tokens * bytes_per_token * headroom
    return max(1, math.ceil(need / GIB))


# ---------------------------------------------------------------- TTFT model
def ttft(n, c, t0, a, b):
    """TTFT for an n-token prompt whose first c tokens are cached.

    t0: fixed cost (HTTP, template, tokenize, first decode step).
    a:  per prefilled token (weights). b: per prefilled token per context token
    (attention), so prefilling tokens c..n-1 costs a*(n-c) + b*(n^2-c^2)/2.
    """
    c = min(max(c, 0), n - 1)  # the last prompt token is always recomputed
    return t0 + a * (n - c) + b * (n * n - c * c) / 2


def reused_tokens(shared, n, block):
    """Tokens restored when the first `shared` of n tokens match the cache."""
    return min((shared // block) * block, n - 1)


def flop_priors(n, model=QWEN3_4B):
    """(weight FLOPs, attention FLOPs) to prefill n tokens from scratch."""
    w = 2 * model["nonemb_params"] * n
    att = 4 * model["layers"] * model["q_heads"] * model["head_dim"] * n * n / 2
    return w, att


def ratio_prior(share, n, f0, block=32, model=QWEN3_4B):
    """warm/cold TTFT with compute split by FLOPs and fixed share f0 of cold TTFT."""
    w, att = flop_priors(n, model)
    a = w / n
    b = 2 * att / (n * n)
    compute_cold = a * n + b * n * n / 2
    t0 = f0 / (1 - f0) * compute_cold
    c = reused_tokens(int(share * n), n, block)
    return ttft(n, c, t0, a, b) / ttft(n, 0, t0, a, b)


def _solve(m, v):
    """Solve a small square linear system by Gaussian elimination with pivoting."""
    k = len(v)
    m = [row[:] + [v[i]] for i, row in enumerate(m)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-300:
            raise ValueError("singular fit: need more distinct prompt lengths")
        m[col], m[piv] = m[piv], m[col]
        for r in range(k):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][k] / m[i][i] for i in range(k)]


def _lsq(pts, cols):
    ata = [[sum(ci(n) * cj(n) for n, _ in pts) for cj in cols] for ci in cols]
    aty = [sum(ci(n) * t for n, t in pts) for ci in cols]
    return _solve(ata, aty)


def fit_cold(samples):
    """Least-squares (t0, a, b) from cold samples [(n, ttft_s)], all >= 0."""
    pts = [(float(n), float(t)) for n, t in samples]
    distinct = len({n for n, _ in pts})
    if distinct < 2:
        raise ValueError("fit needs at least 2 distinct prompt lengths")
    one, lin, quad = (lambda n: 1.0), (lambda n: n), (lambda n: n * n / 2)
    if distinct >= 3:
        t0, a, b = _lsq(pts, [one, lin, quad])
    else:
        (t0, a), b = _lsq(pts, [one, lin]), 0.0
    if b < 0:  # attention cost cannot be negative: refit without it
        (t0, a), b = _lsq(pts, [one, lin]), 0.0
    if t0 < 0:  # fixed cost cannot be negative: refit through the origin
        t0 = 0.0
        a, b = _lsq(pts, [lin, quad]) if distinct >= 2 and b > 0 else (_lsq(pts, [lin])[0], 0.0)
        if b < 0:
            a, b = _lsq(pts, [lin])[0], 0.0
    return t0, a, b


def invert_cached(n, t, t0, a, b):
    """Cached tokens c that make ttft(n, c) == t (clamped to [0, n])."""
    rhs = t - t0 - a * n - b * n * n / 2  # = -a*c - b*c^2/2
    if b == 0:
        c = -rhs / a if a else 0.0
    else:
        disc = a * a - 2 * b * rhs
        c = (-a + math.sqrt(max(disc, 0.0))) / b
    return min(max(c, 0.0), float(n))


# ---------------------------------------------------------------- simulator
class PrefixCache:
    """Serial model of GenAI's block manager with prefix caching on.

    capacity_blocks=None models cache_size=0 (dynamic growth); an int models a
    static pool. One request is live at a time (worst case for dynamic: the
    pool only grows to one request's target).
    """

    def __init__(self, block, capacity_blocks=None):
        self.block = block
        self.dynamic = capacity_blocks is None
        self.total = 0 if self.dynamic else capacity_blocks
        self.store = {}   # hash -> last-use tick (finished, overwritable)
        self.clock = 0
        self.preempted = 0

    def _hashes(self, tokens):
        out, h = [], 0
        for i in range(0, len(tokens) - len(tokens) % self.block, self.block):
            h = hash((h, tuple(tokens[i:i + self.block])))
            out.append(h)
        return out

    def request(self, prompt, generated=(), max_new=None):
        """Serve one request; return the number of prompt tokens restored."""
        self.clock += 1
        hashes = self._hashes(prompt)
        hit = 0
        while hit < len(hashes) and hashes[hit] in self.store:
            hit += 1
        restored = min(hit * self.block, len(prompt) - 1)
        live = set(hashes[:hit])
        for h in live:
            self.store.pop(h)  # in use: not overwritable while live
        seq = list(prompt) + list(generated)
        need = math.ceil(len(seq) / self.block) - hit
        if self.dynamic:
            budget = len(generated) if max_new is None else max_new
            target = min(INITIAL_MULTIPLIER * len(prompt), len(prompt) + budget)
            self.total = max(self.total, math.ceil(target / self.block))
        free_fresh = self.total - len(self.store) - hit
        for _ in range(need):
            if free_fresh > 0:
                free_fresh -= 1
            elif self.store:
                lru = min(self.store, key=self.store.get)
                self.store.pop(lru)
            elif self.dynamic:
                grow = math.ceil(GROWTH_TOKENS / self.block)
                self.total += grow
                free_fresh += grow - 1
            else:
                self.preempted += 1
        for h in self._hashes(seq):
            self.store[h] = self.clock
        # never hold more cached blocks than the pool
        while len(self.store) > self.total:
            self.store.pop(min(self.store, key=self.store.get))
        return restored


# ---------------------------------------------------------------- probe traces
WORDS = ("cluster node pod device engine kernel memory cache block token prefix "
         "graph model server agent tool schema request reply stream batch queue "
         "core thread socket package power limit sensor metric label volume claim "
         "image layer driver plugin runtime compile export tensor weight scale").split()


def text_words(rng, n):
    return " ".join(rng.choice(WORDS) for _ in range(n))


def _session(sid, system, user, max_tokens, recorded="OK."):
    return {"id": sid, "start_s": 0.0, "system": system,
            "turns": [{"append": [{"role": "user", "content": user}],
                       "max_tokens": max_tokens, "think_s": 0.0, "expect_tool": None,
                       "recorded": {"role": "assistant", "content": recorded}}]}


def _trace(name, sessions, probe):
    return {"name": name, "family": "prefix-probe", "params": probe,
            "tools": [], "sessions": sessions, "probe": probe}


USER = "Reply with the single word OK."


def gen_probe(kind, words, seed=0, reps=5, shares=None, positions=None,
              lengths=None, agents=4, turns=8, history_words=400, max_tokens=8):
    """Return {file suffix: trace} for one probe kind."""
    rng = random.Random(seed)
    shared = text_words(random.Random(seed + 1), words)
    if kind == "calib":
        lengths = lengths or [64, 128, 256, 512, 1024, 2048, 4096, 8192]
        ss = [_session(f"calib-w{w}-r{r}", text_words(rng, w), USER, max_tokens)
              for w in lengths for r in range(reps)]
        return {"calib": _trace("P0-calib", ss, {"kind": "calib"})}
    prime = _trace("prime", [_session("prime", shared, USER, max_tokens)],
                   {"kind": "prime"})
    if kind == "sweep":
        shares = shares or [0.0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0]
        ss = []
        for s in shares:
            k = int(round(s * words))
            for r in range(reps):
                system = " ".join(shared.split()[:k] + text_words(rng, words - k).split())
                ss.append(_session(f"sweep-s{s:.2f}-k{k}-r{r}", system, USER, max_tokens))
        return {"prime": prime, "sweep": _trace("P1-sweep", ss,
                {"kind": "sweep", "words": words, "shares": shares})}
    if kind == "perturb":
        positions = positions or [16, words // 2, words - 64]
        base = shared.split()
        ss = []
        for p in positions:
            for r in range(reps):
                w = base[:]
                w[p] = "zebra%d" % rng.randrange(10 ** 9)  # one changed word at p
                ss.append(_session(f"perturb-p{p}-r{r}", " ".join(w), USER, max_tokens))
        return {"prime": prime, "perturb": _trace("P2-perturb", ss,
                {"kind": "perturb", "words": words, "positions": positions})}
    if kind == "interleave":
        # One single-turn session per (turn, agent), listed round-robin, each
        # carrying that agent's whole history. Run with --max-concurrency 1:
        # requests never queue behind each other, and analyze replays the order
        # they were actually sent in, so turn order need not be strict.
        hist = {a: [] for a in range(agents)}
        ss = []
        for t in range(turns):
            for a in range(agents):
                if t:
                    hist[a].append({"role": "assistant", "content": "OK."})
                hist[a].append({"role": "user", "content": text_words(rng, history_words)})
                sess = _session(f"agent{a}-t{t}", shared, "", max_tokens)
                sess["turns"][0]["append"] = [dict(m) for m in hist[a]]
                ss.append(sess)
        return {"interleave": _trace("P3-interleave", ss,
                {"kind": "interleave", "words": words, "agents": agents,
                 "turns": turns, "history_words": history_words})}
    raise ValueError(f"unknown probe kind {kind!r}")


# ---------------------------------------------------------------- analysis
def pseudo_tokens(system, history, assistant_prefix=True):
    """Qwen3 template (no tools, thinking flag unset) as word-level tokens."""
    toks = ["<|im_start|>", "system"] + system.split() + ["<|im_end|>"]
    for m in history:
        toks += ["<|im_start|>", m["role"]] + m["content"].split() + ["<|im_end|>"]
    if assistant_prefix:
        toks += ["<|im_start|>", "assistant"]
    return toks


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def replay(runs, device, fit, capacity_blocks=None, seed=7):
    """Predict every request of the given runs, in the order they were sent.

    runs: [(trace, rows)] in the order the runs were made against one server
    (prime runs included). Each request's prompt is rendered with the Qwen3
    template at word level and served by a PrefixCache, so eviction caused by
    the run itself is part of the prediction. Words are scaled to real tokens
    with one ratio (median server-reported prompt_tokens / words).
    """
    reqs = []  # (trace kind, row, prompt words, max_tokens, first request of its run)
    for trace, rows in runs:
        first = True
        sessions = {s["id"]: s for s in trace["sessions"]}
        hist = {sid: [] for sid in sessions}
        for r in sorted(rows, key=lambda r: (r["sent_at_s"], r["session"], r["turn"])):
            s = sessions[r["session"]]
            turn = s["turns"][r["turn"]]
            msgs = hist[r["session"]] + list(turn.get("append", []))
            hist[r["session"]] = msgs + ([turn["recorded"]] if turn.get("recorded") else [])
            if r.get("error") or r.get("ttft_s") is None or not r.get("prompt_tokens"):
                continue
            reqs.append((trace.get("probe", {}).get("kind"), r,
                         pseudo_tokens(s["system"], msgs), turn.get("max_tokens", 1), first))
            first = False
    if not reqs:
        return []
    # one words->tokens ratio for the whole replay keeps block hashes consistent
    scale = statistics.median(q[1]["prompt_tokens"] / len(q[2]) for q in reqs)
    sim = PrefixCache(max(1, round(BLOCK[device] / scale)), capacity_blocks)
    rng = random.Random(seed)
    out = []
    for kind, r, prompt, max_tokens, first in reqs:
        gen = ["<gen%d>" % rng.randrange(10 ** 12) for _ in range(max_tokens)]
        n = r["prompt_tokens"]
        c = min(round(sim.request(prompt, gen, max_tokens) * scale), n - 1)
        out.append({"kind": kind, "session": r["session"], "turn": r["turn"], "n": n,
                    "first_in_run": first,
                    "pred_cached": c, "pred_ttft_s": ttft(n, c, *fit),
                    "cold_ttft_s": ttft(n, 0, *fit), "meas_ttft_s": r["ttft_s"],
                    "meas_cached": round(invert_cached(n, r["ttft_s"], *fit))})
    return out


def analyze(calib_rows, runs, device, tol=0.20, capacity_blocks=None, abs_tol=0.05):
    """Fit the cold P0 run, replay the probe runs, score each group.

    A group is every repetition of one probe point (session id minus "-rN");
    interleave sessions score per request. A group passes when its median
    measured TTFT is within max(tol * predicted, abs_tol seconds) of the
    predicted TTFT. The first request of each run is replayed (it changes the
    cache) but not scored: it also pays the client's connection setup.
    """
    cal = [(r["prompt_tokens"], r["ttft_s"]) for r in calib_rows
           if not r.get("error") and r.get("ttft_s") and r.get("prompt_tokens")]
    fit = fit_cold(cal)
    preds = replay(runs, device, fit, capacity_blocks)
    groups = {}
    for p in preds:
        if p["kind"] in (None, "prime") or p["first_in_run"]:
            continue
        key = p["session"].rsplit("-r", 1)[0] if p["kind"] != "interleave" else p["session"]
        groups.setdefault(key, []).append(p)
    points = []
    for key, ps in sorted(groups.items()):
        pred = _median([p["pred_ttft_s"] for p in ps])
        meas = _median([p["meas_ttft_s"] for p in ps])
        cold = _median([p["cold_ttft_s"] for p in ps])
        points.append({"group": key, "n": _median([p["n"] for p in ps]),
                       "pred_cached": _median([p["pred_cached"] for p in ps]),
                       "meas_cached": _median([p["meas_cached"] for p in ps]),
                       "pred_ttft_s": pred, "meas_ttft_s": meas, "cold_ttft_s": cold,
                       "rel_err": (meas - pred) / pred, "rel_err_if_no_cache": (meas - cold) / cold,
                       "pass": abs(meas - pred) <= max(tol * pred, abs_tol)})
    return {"fit": {"t0_s": fit[0], "a_s_per_tok": fit[1], "b_s_per_tok2": fit[2],
                    "n": len(cal)},
            "device": device, "block": BLOCK[device], "tolerance": tol, "abs_tolerance_s": abs_tol,
            "cache": "dynamic" if capacity_blocks is None else f"static {capacity_blocks} blocks",
            "points": points,
            "pass_rate": sum(p["pass"] for p in points) / len(points) if points else None}


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("predict", help="warm/cold TTFT ratio vs prefix share")
    p.add_argument("--device", choices=sorted(BLOCK), default="CPU")
    p.add_argument("--tokens", type=int, nargs="+", default=[2048, 8192, 32768])
    p.add_argument("--shares", type=float, nargs="+",
                   default=[0.0, 0.5, 0.8, 0.9, 0.95, 0.99, 1.0])
    p.add_argument("--f0", type=float, nargs="+", default=[0.01, 0.05],
                   help="fixed cost as a fraction of cold TTFT (prior)")
    p.add_argument("--fit", help="JSON {t0_s, a_s_per_tok, b_s_per_tok2} from analyze; replaces priors")

    s = sub.add_parser("size", help="cache_size in GB for a device")
    s.add_argument("--device", choices=sorted(BLOCK), default="CPU")
    s.add_argument("--sessions", type=int, default=4)
    s.add_argument("--context", type=int, default=32768)
    s.add_argument("--overhead", type=float, default=0.0,
                   help="fractional KV overhead for quantization scales")

    g = sub.add_parser("gen-probe", help="write agent_trace.py traces for a probe")
    g.add_argument("kind", choices=["calib", "sweep", "perturb", "interleave"])
    g.add_argument("--out", required=True, help="prefix: writes PREFIX.<part>.json")
    g.add_argument("--words", type=int, default=4096, help="shared system prompt length (words)")
    g.add_argument("--reps", type=int, default=5)
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--agents", type=int, default=4)
    g.add_argument("--turns", type=int, default=8)
    g.add_argument("--history-words", type=int, default=400)
    g.add_argument("--lengths", type=int, nargs="+")

    z = sub.add_parser("analyze", help="score probe runs against their prediction")
    z.add_argument("--calib", required=True, help="agent_trace JSONL of the P0 (cold) run")
    z.add_argument("--run", required=True, action="append", metavar="TRACE.json:RUN.jsonl",
                   help="a trace and its agent_trace JSONL; repeat in the order the runs were made")
    z.add_argument("--device", choices=sorted(BLOCK), required=True)
    z.add_argument("--cache-gb", type=float, default=0,
                   help="cache_size the server ran with (0 = dynamic)")
    z.add_argument("--kv-overhead", type=float, default=0.0)
    z.add_argument("--tol", type=float, default=0.20)
    z.add_argument("--abs-tol", type=float, default=0.05, help="seconds")

    a = ap.parse_args(argv)
    if a.cmd == "predict":
        rows = []
        fit = load_json(a.fit) if a.fit else None
        for n in a.tokens:
            for f0 in ([None] if fit else a.f0):
                row = {"tokens": n, "f0": f0}
                for sh in a.shares:
                    if fit:
                        c = reused_tokens(int(sh * n), n, BLOCK[a.device])
                        r = ttft(n, c, fit["t0_s"], fit["a_s_per_tok"], fit["b_s_per_tok2"]) / \
                            ttft(n, 0, fit["t0_s"], fit["a_s_per_tok"], fit["b_s_per_tok2"])
                    else:
                        r = ratio_prior(sh, n, f0, BLOCK[a.device])
                    row[f"s={sh:g}"] = round(r, 4)
                rows.append(row)
        print(json.dumps(rows, indent=1))
    elif a.cmd == "size":
        bpt = kv_bytes_per_token(a.device, overhead=a.overhead)
        print(json.dumps({"device": a.device, "kv_bytes_per_token": bpt,
                          "sessions": a.sessions, "context": a.context,
                          "cache_size_gb": cache_size_gb(a.sessions, a.context, bpt)}))
    elif a.cmd == "gen-probe":
        parts = gen_probe(a.kind, a.words, a.seed, a.reps, lengths=a.lengths,
                          agents=a.agents, turns=a.turns, history_words=a.history_words)
        for part, trace in parts.items():
            with open(f"{a.out}.{part}.json", "w") as f:
                json.dump(trace, f)
            print(f"{a.out}.{part}.json: {len(trace['sessions'])} sessions")
    elif a.cmd == "analyze":
        runs = []
        for spec in a.run:
            trace_path, rows_path = spec.split(":", 1)
            runs.append((load_json(trace_path), load_jsonl(rows_path)))
        cap = None
        if a.cache_gb:
            bpt = kv_bytes_per_token(a.device, overhead=a.kv_overhead)
            cap = int(a.cache_gb * GIB / (bpt * BLOCK[a.device]))
        print(json.dumps(analyze(load_jsonl(a.calib), runs, a.device, a.tol, cap, a.abs_tol), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
