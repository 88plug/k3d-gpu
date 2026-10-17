#!/usr/bin/env python3
"""client.py: H26 agent requests, one JSON line per request.

Each request is a unique prompt (a nonce defeats prefix caching), streamed,
greedy, ignore_eos, so every request does the same work. Prints one line per
request: {"arm", "block", "round", "i", "warmup", "ok", "latency_s", "ttft_s",
"tokens"}. Python 3 standard library only.

  client.py --base-url http://127.0.0.1:18080/v3 --model Qwen3-4B-int4-ov \\
      --arm MC4 --block 4 --round 1 --requests 10 --warmup 1
"""

import argparse
import json
import secrets
import sys
import time
import urllib.request

WORDS = ("cache", "ring", "core", "memory", "agent", "token", "queue", "latency",
         "socket", "thread", "buffer", "page", "kernel", "driver", "balloon")


def prompt(nonce):
    """About 30 tokens: a nonce, then a fixed instruction."""
    tail = " ".join(WORDS[(nonce + k) % len(WORDS)] for k in range(8))
    return f"[{nonce:08x}] List facts about {tail}. Answer in plain sentences."


def one(base_url, model, max_tokens, timeout, nonce):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt(nonce)}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "ignore_eos": True,
        "stream": True,
        "stream_options": {"include_usage": True},
    }).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    ttft = None
    tokens = None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if chunk.get("usage"):
                tokens = chunk["usage"].get("completion_tokens")
            for ch in chunk.get("choices") or []:
                d = ch.get("delta") or {}
                if ttft is None and (d.get("content") or d.get("reasoning_content")):
                    ttft = time.monotonic() - t0
    return time.monotonic() - t0, ttft, tokens


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--arm", required=True)
    p.add_argument("--block", type=int, required=True)
    p.add_argument("--round", type=int, required=True)
    p.add_argument("--requests", type=int, default=10)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--max-tokens", type=int, default=32)
    p.add_argument("--timeout", type=float, default=120.0)
    a = p.parse_args(argv)
    base = secrets.randbits(31)
    for i in range(a.warmup + a.requests):
        rec = {"arm": a.arm, "block": a.block, "round": a.round, "i": i,
               "warmup": i < a.warmup, "ok": False, "latency_s": None, "ttft_s": None, "tokens": None}
        try:
            lat, ttft, tok = one(a.base_url, a.model, a.max_tokens, a.timeout, base + i)
            rec.update(ok=tok == a.max_tokens, latency_s=round(lat, 4),
                       ttft_s=None if ttft is None else round(ttft, 4), tokens=tok)
        except Exception as e:  # recorded, not raised: a failed request is a guardrail breach
            rec["error"] = str(e)[:200]
        print(json.dumps(rec), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
