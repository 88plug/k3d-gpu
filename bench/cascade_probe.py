#!/usr/bin/env python3
"""Live probe for cascade attention, stage 0: does a shared prompt prefix change
decode speed or DRAM traffic on a stock server? (stdlib only)

  run   B concurrent streamed chat requests whose prompts are
          shared    one common prefix + a distinct short suffix per request
          distinct  same lengths, a distinct nonce at the start of each prompt
          short     the suffix only
        Every prompt is first sent once with max_tokens=1 so its KV is in the
        prefix cache; the timed requests then prefill ~1 token and all B
        decode together. Output: aggregate decode tok/s over the window in
        which all B streams are decoding, step time = B / that rate, and the
        window's monotonic and wall-clock bounds (for the IMC counters).
  imc   DRAM read bytes per decode step from `perf stat -I ... -x,` output of
        uncore_imc_free_running_*/data_read/, restricted to a run's window,
        minus a background rate measured with the server idle.

  python3 bench/cascade_probe.py run --url http://127.0.0.1:8000/v3/chat/completions \\
      --model qwen3-4b --mode shared --batch 8 --out s0/shared-r1.json
  python3 bench/cascade_probe.py imc --perf s0/imc-r1.csv --run s0/shared-r1.json \\
      --idle s0/idle-r1.csv
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import urllib.request
import uuid

WORDS = (
    "account address agent allow amount answer apply approve balance billing booking cancel card case "
    "change charge check claim confirm contact cost credit customer date deliver detail device discount "
    "document eligible email error exchange expire fee file flight follow handle history identity invoice "
    "issue item limit manager member message method notice number offer order package payment pending "
    "period plan policy price process product purchase reason receipt record refund request require "
    "return review rule schedule service shipping status store submit support system ticket total update"
).split()


def text(seed, n):
    rng = random.Random(seed)
    return " ".join(rng.choice(WORDS) for _ in range(n))


def prompts(mode, batch, prefix_words, suffix_words, seed=0):
    """List of (system, user) message pairs, one per request."""
    prefix = text(seed, prefix_words)
    out = []
    for i in range(batch):
        suffix = f"Request {i}: " + text(seed + 1000 + i, suffix_words)
        if mode == "shared":
            out.append((prefix, suffix))
        elif mode == "distinct":
            out.append((f"Session {uuid.UUID(int=random.Random(seed + 7 + i).getrandbits(128))}. " + prefix, suffix))
        elif mode == "short":
            out.append(("You are a support agent.", suffix))
        else:
            raise ValueError(mode)
    return out


def stream(url, model, system, user, max_tokens, ignore_eos, timeout):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if ignore_eos:
        body["ignore_eos"] = True
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    t0 = time.perf_counter()
    stamps, usage, err = [], None, None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if delta.get("content") or delta.get("reasoning_content"):
                        stamps.append(time.perf_counter())
    except Exception as e:  # recorded, the run is then marked invalid
        err = f"{type(e).__name__}: {e}"
    return dict(t0=t0, stamps=stamps, usage=usage, error=err)


def window_rate(results):
    """Aggregate decode tok/s while every stream is decoding: from the latest
    first token to the earliest last token, count every stream's tokens."""
    if any(r["error"] or len(r["stamps"]) < 3 for r in results):
        return None
    lo = max(r["stamps"][0] for r in results)
    hi = min(r["stamps"][-1] for r in results)
    if hi <= lo:
        return None
    n = sum(sum(1 for s in r["stamps"] if lo < s <= hi) for r in results)
    return dict(lo=lo, hi=hi, tokens=n, tps=n / (hi - lo))


def run(a):
    ps = prompts(a.mode, a.batch, a.prefix_words, a.suffix_words, a.seed)
    for system, user in ps:  # warm the prefix cache, one prompt at a time
        stream(a.url, a.model, system, user, 1, False, a.timeout)
    res = [None] * len(ps)
    barrier = threading.Barrier(len(ps))

    def worker(i):
        barrier.wait()
        res[i] = stream(a.url, a.model, ps[i][0], ps[i][1], a.max_tokens, True, a.timeout)

    mono0, wall0 = time.perf_counter(), time.time()
    th = [threading.Thread(target=worker, args=(i,)) for i in range(len(ps))]
    for t in th:
        t.start()
    for t in th:
        t.join()
    w = window_rate(res)
    ttft = [r["stamps"][0] - r["t0"] for r in res if r["stamps"]]
    out = dict(
        mode=a.mode, batch=a.batch, prefix_words=a.prefix_words, suffix_words=a.suffix_words,
        max_tokens=a.max_tokens, url=a.url, model=a.model,
        prompt_tokens=[(r["usage"] or {}).get("prompt_tokens") for r in res],
        completion_tokens=[(r["usage"] or {}).get("completion_tokens") for r in res],
        errors=[r["error"] for r in res if r["error"]],
        ttft_max_s=max(ttft) if ttft else None,
        valid=bool(w) and max(ttft) <= a.max_ttft,
        agg_decode_tps=w and w["tps"],
        step_ms=w and 1000 * a.batch / w["tps"],
        window_tokens=w and w["tokens"],
        window_wall=w and [wall0 + (w["lo"] - mono0), wall0 + (w["hi"] - mono0)],
    )
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: out[k] for k in ("mode", "batch", "valid", "agg_decode_tps", "step_ms", "ttft_max_s")}))
    return 0 if out["valid"] else 1


UNIT = {"": 1.0, "B": 1.0, "bytes": 1.0, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "MB": 1e6, "GB": 1e9}


def perf_intervals(path, t_start):
    """[(t_begin_wall, t_end_wall, bytes)] summed over IMC events per interval
    from `perf stat -a -I <ms> -x,` output. t_start is the wall time perf began
    (perf -I prints seconds since start)."""
    by_t = {}
    with open(path) as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.strip().split(",")
            if len(parts) < 4 or "data_read" not in parts[3]:
                continue
            try:
                t, v = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            by_t[t] = by_t.get(t, 0.0) + v * UNIT.get(parts[2], 1.0)
    out, prev = [], 0.0
    for t in sorted(by_t):
        out.append((t_start + prev, t_start + t, by_t[t]))
        prev = t
    return out


def imc(a):
    with open(a.run) as f:
        r = json.load(f)
    lo, hi = r["window_wall"]
    iv = perf_intervals(a.perf, a.perf_start)
    got = sum(b * max(0.0, min(e, hi) - max(s, lo)) / (e - s) for s, e, b in iv if e > s)
    covered = sum(max(0.0, min(e, hi) - max(s, lo)) for s, e, _ in iv)
    idle = perf_intervals(a.idle, 0.0) if a.idle else []
    idle_rate = sum(b for _, _, b in idle) / max(1e-9, sum(e - s for s, e, _ in idle)) if idle else 0.0
    steps = r["window_tokens"] / r["batch"]
    net = got - idle_rate * (hi - lo)
    out = dict(run=a.run, window_s=hi - lo, covered_s=covered, steps=steps,
               gb_per_step=net / steps / 1e9, gb_per_step_gross=got / steps / 1e9, idle_gb_s=idle_rate / 1e9)
    print(json.dumps(out))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--url", default="http://127.0.0.1:8000/v3/chat/completions")
    r.add_argument("--model", required=True)
    r.add_argument("--mode", choices=["shared", "distinct", "short"], required=True)
    r.add_argument("--batch", type=int, default=8)
    r.add_argument("--prefix-words", type=int, default=2600, help="~3.4k tokens at ~1.3 tokens/word")
    r.add_argument("--suffix-words", type=int, default=45, help="~64 tokens")
    r.add_argument("--max-tokens", type=int, default=160)
    r.add_argument("--max-ttft", type=float, default=3.0, help="run invalid if any TTFT exceeds this (cache miss)")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--timeout", type=float, default=900.0)
    r.add_argument("--out", required=True)
    i = sub.add_parser("imc")
    i.add_argument("--perf", required=True, help="perf stat -a -I 500 -x, output covering the run")
    i.add_argument("--perf-start", type=float, required=True, help="wall time (date +%%s.%%N) perf started")
    i.add_argument("--run", required=True, help="JSON written by `run`")
    i.add_argument("--idle", help="perf output with the server idle (background rate)")
    a = ap.parse_args(argv)
    return {"run": run, "imc": imc}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
