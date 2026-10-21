#!/usr/bin/env python3
"""llm_load.py: load an OpenAI-compatible chat endpoint and record per-request latency.

Streams every request, so time to first token is measured on the client. Python
3 standard library only.

  bench/llm_load.py --base-url http://127.0.0.1:8000/v3 --model qwen3-8b \\
      --requests 8 --concurrency 1,4 --max-tokens 128 --out result.json

--base-url is the API root that /chat/completions hangs off: .../v3 for OpenVINO
Model Server, .../v1 for vLLM, llama.cpp, Ollama and most others.

Per request it records:
  first_chunk_s    first SSE data chunk (OVMS continuous batching sends a role-only
                   chunk the moment the first token exists)
  ttft_s           first chunk carrying content, reasoning_content or reasoning
  latency_s        until [DONE] / end of stream
  output_tokens    usage.completion_tokens (stream_options.include_usage); null
                   when the server sends no usage
  tokens_per_s     output_tokens / latency_s
  decode_tokens_per_s  (output_tokens - 1) / (latency_s - ttft_s)
Each concurrency level is one phase: --requests requests, at most --concurrency
in flight. The JSON has every sample plus median/p90/min/max per metric.
"""

import argparse
import concurrent.futures
import http.client
import itertools
import json
import math
import secrets
import statistics
import sys
import time
import urllib.parse

DEFAULT_PROMPT = (
    "Explain in three short paragraphs how a CPU cache hierarchy reduces memory "
    "latency, and what a cache miss costs."
)
METRICS = ("first_chunk_s", "ttft_s", "latency_s", "output_tokens", "tokens_per_s", "decode_tokens_per_s")


def chat_url(base_url):
    """Split base URL + /chat/completions into (scheme, host, port, path)."""
    u = urllib.parse.urlsplit(base_url.rstrip("/") + "/chat/completions")
    if u.scheme not in ("http", "https") or not u.hostname:
        raise SystemExit(f"llm_load: --base-url must be http(s)://host[:port]/<api root>, got {base_url!r}")
    return u.scheme, u.hostname, u.port, u.path + (f"?{u.query}" if u.query else "")


PROMPT_SEQ = itertools.count()  # next() is atomic under the GIL: safe across worker threads


def prompt_for(args):
    """The prompt of the next request. --unique-prompts puts a tag no other request
    has first (a random run id and a sequence number across every phase and
    warm-up), so no two requests share a prefix and none is a prefix-cache hit."""
    if not getattr(args, "unique_prompts", False):
        return args.prompt
    return f"[request {args.run_id}-{next(PROMPT_SEQ)}] {args.prompt}"


def request_body(args):
    """The JSON body of the next request; --extra-body keys are added last."""
    body = {
        "model": args.model,
        "messages": [{"role": "user", "content": prompt_for(args)}],
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if args.ignore_eos:
        body["ignore_eos"] = True
    body.update(getattr(args, "extra_body", None) or {})
    return body


def one_request(args, idx):
    """Run one streamed chat completion; return its sample dict (never raises)."""
    scheme, host, port, path = chat_url(args.base_url)
    body = request_body(args)
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    conn_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    sample = {"index": idx, "ok": False, "error": None, "chunks": 0, "content_chunks": 0, "text_chars": 0}
    for m in METRICS:
        sample[m] = None
    t0 = time.perf_counter()
    try:
        conn = conn_cls(host, port, timeout=args.timeout)
        conn.request("POST", path, body=json.dumps(body), headers=headers)
        resp = conn.getresponse()
        if resp.status != 200:
            sample["error"] = f"HTTP {resp.status}: {resp.read(500).decode('utf-8', 'replace')}"
            return sample
        usage = None
        done = False
        while not done:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue  # blank separators, comments, event:/id: fields
            data = line[5:].strip()
            if data == "[DONE]":
                done = True
                break
            now = time.perf_counter() - t0
            chunk = json.loads(data)
            if sample["first_chunk_s"] is None:
                sample["first_chunk_s"] = now
            sample["chunks"] += 1
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                # reasoning_content: vLLM/OVMS; reasoning: Ollama's OpenAI layer
                # (openai/openai.go, v0.40.5), which streams Qwen3 thinking there.
                text = ((delta.get("content") or "") + (delta.get("reasoning_content") or "")
                        + (delta.get("reasoning") or ""))
                if text:
                    sample["content_chunks"] += 1
                    sample["text_chars"] += len(text)
                    if sample["ttft_s"] is None:
                        sample["ttft_s"] = now
        sample["latency_s"] = time.perf_counter() - t0
        resp.read()  # the rest of the body (chunked terminator), so close() is clean
        conn.close()
        if sample["ttft_s"] is None:
            sample["error"] = "stream ended without any content"
            return sample
        if usage and isinstance(usage.get("completion_tokens"), int):
            n = usage["completion_tokens"]
            sample["output_tokens"] = n
            sample["prompt_tokens"] = usage.get("prompt_tokens")
            sample["tokens_per_s"] = n / sample["latency_s"]
            decode = sample["latency_s"] - sample["ttft_s"]
            if n > 1 and decode > 0:
                sample["decode_tokens_per_s"] = (n - 1) / decode
        sample["ok"] = True
        sample["finished"] = done
    except (OSError, http.client.HTTPException, json.JSONDecodeError, ValueError) as e:
        sample["error"] = f"{type(e).__name__}: {e}"
        sample["latency_s"] = time.perf_counter() - t0
    return sample


def summarize(values):
    """median/p90/min/max (p90 = nearest rank) of the non-null values."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return {"n": 0, "median": None, "p90": None, "min": None, "max": None}
    return {
        "n": len(v),
        "median": statistics.median(v),
        "p90": v[max(0, math.ceil(0.9 * len(v)) - 1)],
        "min": v[0],
        "max": v[-1],
    }


def fmt(x, digits=3):
    return "-" if x is None else f"{x:.{digits}f}"


def run_phase(args, concurrency):
    for i in range(args.warmup):
        one_request(args, -1 - i)
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        samples = list(pool.map(lambda i: one_request(args, i), range(args.requests)))
    wall = time.perf_counter() - t0
    ok = [s for s in samples if s["ok"]]
    tokens = [s["output_tokens"] for s in ok if s["output_tokens"] is not None]
    return {
        "concurrency": concurrency,
        "requests": args.requests,
        "ok": len(ok),
        "errors": len(samples) - len(ok),
        "wall_s": wall,
        "output_tokens_total": sum(tokens) if tokens else None,
        "aggregate_tokens_per_s": (sum(tokens) / wall) if tokens and wall > 0 else None,
        "requests_per_s": len(ok) / wall if wall > 0 else None,
        "stats": {m: summarize(s[m] for s in ok) for m in METRICS},
        "samples": samples,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base-url", required=True, help="API root, e.g. http://127.0.0.1:8000/v3 (OVMS) or .../v1")
    p.add_argument("--model", required=True)
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--max-tokens", type=int, default=128)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--ignore-eos", action="store_true", help="ask the server to always generate max_tokens (OVMS, vLLM)")
    p.add_argument("--extra-body", type=json.loads, default=None,
                   help='JSON object merged into every request body, e.g. {"chat_template_kwargs": {"enable_thinking": false}}')
    p.add_argument("--unique-prompts", action="store_true",
                   help="tag each prompt so no request is a prefix-cache hit (first-token latency then includes prefill)")
    p.add_argument("--requests", type=int, default=8, help="requests per phase")
    p.add_argument("--concurrency", default="1", help="comma list of in-flight limits, one phase each; 1 = sequential")
    p.add_argument("--warmup", type=int, default=1, help="unrecorded requests before each phase")
    p.add_argument("--timeout", type=float, default=600.0, help="per-request socket timeout, seconds")
    p.add_argument("--api-key", default=None)
    p.add_argument("--label", default=None, help="free-form tag stored in the JSON (engine, device, ...)")
    p.add_argument("--out", default=None, help="write the JSON here (default: stdout)")
    args = p.parse_args()
    args.run_id = secrets.token_hex(4)
    chat_url(args.base_url)  # validate early
    levels = [int(c) for c in args.concurrency.split(",") if c.strip()]
    if not levels or min(levels) < 1 or args.requests < 1:
        p.error("--concurrency needs positive integers and --requests >= 1")

    result = {
        "tool": "k3d-gpu/bench/llm_load.py",
        "schema": 1,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {k: v for k, v in vars(args).items() if k != "api_key"},
        "phases": [],
    }
    for c in levels:
        phase = run_phase(args, c)
        result["phases"].append(phase)
        st = phase["stats"]
        print(
            f"concurrency={c} ok={phase['ok']}/{phase['requests']} "
            f"ttft_median={fmt(st['ttft_s']['median'])}s ttft_p90={fmt(st['ttft_s']['p90'])}s "
            f"latency_median={fmt(st['latency_s']['median'])}s "
            f"tok/s_median={fmt(st['tokens_per_s']['median'], 1)} "
            f"aggregate_tok/s={fmt(phase['aggregate_tokens_per_s'], 1)}",
            file=sys.stderr,
        )
        for s in phase["samples"]:
            if s["error"]:
                print(f"  request {s['index']}: {s['error']}", file=sys.stderr)
    text = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text + "\n")
    else:
        print(text)
    return 0 if all(ph["errors"] == 0 for ph in result["phases"]) else 1


if __name__ == "__main__":
    sys.exit(main())
