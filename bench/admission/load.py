#!/usr/bin/env python3
"""load.py: the H55 closed-loop prefill client.

  load.py --base-url URL --model NAME --prompt-file FILE --until EPOCH --out LOAD.jsonl
          [--max-tokens 16]

One request at a time (concurrency 1), each with a unique prompt tag
(bench/llm_load.py --unique-prompts), started until --until. Each finished
request is appended to --out as one JSON line by a single write(2) on an
O_APPEND descriptor, so a reader (ctl.py) sees whole records or a trailing
partial line it skips. This process reads nothing back. Python 3 standard
library only.
"""

import argparse
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from llm_load import one_request  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--until", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=16)
    ap.add_argument("--timeout", type=float, default=600.0)
    a = ap.parse_args(argv)
    with open(a.prompt_file) as f:
        prompt = f.read().rstrip("\n")  # as run.sh's "$(cat FILE)" in H5
    req = argparse.Namespace(base_url=a.base_url, model=a.model, prompt=prompt, unique_prompts=True,
                             run_id=secrets.token_hex(4), max_tokens=a.max_tokens, temperature=0.0,
                             ignore_eos=False, api_key=None, timeout=a.timeout)
    fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    idx = 0
    while time.time() < a.until:
        t_start = time.time()
        s = one_request(req, idx)
        rec = {"index": idx, "t_start": t_start, "t_end": time.time(), "ok": s["ok"],
               "ttft_s": s["ttft_s"], "prompt_tokens": s.get("prompt_tokens"), "error": s["error"]}
        os.write(fd, (json.dumps(rec) + "\n").encode())
        idx += 1
    os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
