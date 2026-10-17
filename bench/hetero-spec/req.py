#!/usr/bin/env python3
"""One streamed, greedy chat completion against OVMS; prints one JSON record.

Usage: req.py <base url> <graph name> <prompts.jsonl> <index>
decode_tps = (tokens - 1) / (t_last - t_first), tokens = streamed content chunks.
"""

import json
import sys
import time
import urllib.request


def main(argv):
    base, model, path, idx = argv[1], argv[2], argv[3], int(argv[4])
    with open(path) as fh:
        prompts = [json.loads(line) for line in fh if line.strip()]
    prompt = prompts[idx % len(prompts)]
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt["text"]}],
                       "temperature": 0, "max_tokens": 256, "ignore_eos": True,
                       "stream": True}).encode()
    req = urllib.request.Request(base + "/v3/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    times, text = [], []
    with urllib.request.urlopen(req, timeout=300) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            delta = json.loads(line[5:])["choices"][0].get("delta", {})
            piece = delta.get("content") or delta.get("reasoning_content")
            if piece:
                times.append(time.monotonic())
                text.append(piece)
    n = len(times)
    tps = (n - 1) / (times[-1] - times[0]) if n > 1 and times[-1] > times[0] else None
    print(json.dumps({"prompt": prompt["id"], "tokens": n, "ttft_s": (times[0] - t0) if n else None,
                      "decode_tps": tps, "text_head": "".join(text)[:200]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
