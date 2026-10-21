#!/usr/bin/env python3
"""gsm8k.py: H54's quality spot-check (EXPERIMENTS.md "H54 pre-registration", protocol step 4).

  gsm8k.py run --base-url URL --model M --items ITEMS.jsonl --first 0 --last 49 --out OUT.json
           [--no-think]
  gsm8k.py grade OUT.json

ITEMS.jsonl: one {"question", "answer"} per line, `openai/gsm8k` main test in file order.
Zero-shot through the server's chat template, temperature 0, max_tokens 1024, 16 in flight.
Correct: the last number in the reply equals the number after `####`, commas removed.
"""
import argparse
import concurrent.futures
import json
import re
import sys
import urllib.request

PROMPT = "Solve the problem. End with a line 'Answer: <number>'."
NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def gold(answer):
    return answer.split("####")[-1].strip().replace(",", "")


def last_number(text):
    m = NUM.findall(text or "")
    if not m:
        return None
    v = m[-1].replace(",", "")
    return v[:-1] if v.endswith(".") else v


def same(a, b):
    if a is None:
        return False
    try:
        return float(a) == float(b)
    except ValueError:
        return False


def ask(args, q):
    body = {"model": args.model, "temperature": 0, "max_tokens": 1024,
            "messages": [{"role": "user", "content": f"{PROMPT}\n\n{q}"}]}
    if args.no_think:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(args.base_url.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            msg = json.load(r)["choices"][0]["message"]
        return msg.get("content") or "", None
    except Exception as e:  # recorded per item, scored wrong
        return "", f"{type(e).__name__}: {e}"


def run(args):
    with open(args.items) as f:
        items = [json.loads(line) for line in f][args.first:args.last + 1]
    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        replies = list(pool.map(lambda it: ask(args, it["question"]), items))
    rows = []
    for i, (it, (text, err)) in enumerate(zip(items, replies), start=args.first):
        g, p = gold(it["answer"]), last_number(text)
        rows.append({"item": i, "gold": g, "pred": p, "correct": same(p, g), "error": err,
                     "reply": text})
    out = {"model": args.model, "first": args.first, "last": args.last, "rows": rows,
           "correct": sum(r["correct"] for r in rows), "n": len(rows)}
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
    print(f"gsm8k {out['correct']}/{out['n']}")


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base-url", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--items", required=True)
    r.add_argument("--first", type=int, default=0)
    r.add_argument("--last", type=int, default=49)
    r.add_argument("--out", required=True)
    r.add_argument("--no-think", action="store_true")
    g = sub.add_parser("grade")
    g.add_argument("out")
    a = p.parse_args(argv)
    if a.cmd == "run":
        run(a)
    else:
        with open(a.out) as f:
            d = json.load(f)
        print(sum(same(r["pred"], r["gold"]) for r in d["rows"]))


if __name__ == "__main__":
    main()
