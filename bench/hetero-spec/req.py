#!/usr/bin/env python3
"""One streamed, greedy chat completion against OVMS; prints one JSON record.

Usage: req.py <base url> <graph name> <prompts.jsonl> <index>
decode_tps = (tokens - 1) / (t_last - t_first). tokens = usage.completion_tokens from
stream_options.include_usage (OVMS docs/model_server_rest_api_chat.md), not the
streamed chunk count: under speculation one SSE chunk can carry several tokens.
"""

import json
import sys
import time
import urllib.request

MAX_TOKENS = 256
TIMEOUT_S = 120


def main(argv):
    base, model, path, idx = argv[1], argv[2], argv[3], int(argv[4])
    with open(path) as fh:
        prompts = [json.loads(line) for line in fh if line.strip()]
    prompt = prompts[idx % len(prompts)]
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt["text"]}],
                       "temperature": 0, "max_tokens": MAX_TOKENS, "ignore_eos": True,
                       "stream": True, "stream_options": {"include_usage": True}}).encode()
    req = urllib.request.Request(base + "/v3/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    times, text, usage = [], [], None
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            msg = json.loads(line[5:])
            if msg.get("usage"):
                usage = msg["usage"]
            if not msg.get("choices"):
                continue
            delta = msg["choices"][0].get("delta", {})
            piece = delta.get("content") or delta.get("reasoning_content")
            if piece:
                times.append(time.monotonic())
                text.append(piece)
    chunks = len(times)
    n = usage.get("completion_tokens") if usage else None
    tps = (n - 1) / (times[-1] - times[0]) if n and chunks > 1 and times[-1] > times[0] else None
    print(json.dumps({"prompt": prompt["id"], "tokens": n, "chunks": chunks, "tokens_ok": n == MAX_TOKENS,
                      "ttft_s": (times[0] - t0) if chunks else None,
                      "decode_tps": tps, "e2e_tps": (n / (times[-1] - t0)) if n and chunks else None, "text_head": "".join(text)[:200]}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except (OSError, ValueError, KeyError) as exc:  # one failed request is a record, not a crash
        print(json.dumps({"prompt": None, "tokens": None, "decode_tps": None, "error": repr(exc)[:200]}))
        sys.exit(0)
