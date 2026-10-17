#!/usr/bin/env python3
"""H25 client: shared-prefix TTFT and burst memory against one OVMS server.

  probe.py gen   --seed S                       prints the round's prompts (JSON)
  probe.py round --seed S --url URL --arm ARM --mem-cmd CMD --out FILE
                                                warm-up, phase S (a1..a6, c), phase B (b1..b6)
  probe.py score [--load LOAD.jsonl] FILE...    T ratio, bootstrap CI, spread, M, verdict;
                                                with --load, unmatched background load
                                                (bench/load/load.py) makes T and M INCONCLUSIVE

Prompts follow the H25 pre-registration in EXPERIMENTS.md (amendment A1):
system text plus 12 EAT-style tool schemas with frozen key order, a shared
prefix of about 1536 Qwen3 tokens after the chat template, a 96-token private
message per agent, and a control c with one word changed at about token 16 of
the system text. Token counts are seed-invariant by construction; the server's
usage.prompt_tokens must still be identical across agents and arms.
Stdlib only.
"""

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

MODEL = os.environ.get("H25_MODEL", "Qwen3-4B-int4-ov")  # the agent stack's AGENT_MODEL_NAME
# Token-exact prompts. Every WORDS entry is one Qwen3 token after a space (and
# two after a quote), so a text of n words has the same token count for every
# seed; tests/.../test_probe.py checks this against the Qwen3 tokenizer.
DESC_WORDS = 31    # per tool description
ARG_WORDS = 9      # per "id" argument description
SYSTEM_WORDS = 140
PRIVATE_WORDS = 93  # + "a1:" (3 tokens) = 96-token private message
WORDS = ("record order invoice ticket asset vendor policy account region status "
         "priority owner window budget metric report service queue").split()
CONTROL_WORD = "customer"  # one token, not in WORDS


def words(rng, n):
    return " ".join(rng.choice(WORDS) for _ in range(n))


def gen(seed):
    rng = random.Random(seed)
    tools = []
    for k in range(12):
        tools.append({"type": "function", "function": {
            "name": f"eat_tool_{k:02d}",
            "description": words(rng, DESC_WORDS),
            "parameters": {"type": "object",
                           "properties": {"id": {"type": "string", "description": words(rng, ARG_WORDS)},
                                          "limit": {"type": "integer"}},
                           "required": ["id"]}}})
    sw = words(rng, SYSTEM_WORDS).split()
    system = f"seed {seed:04d}. " + " ".join(sw)
    # Control c: the word at about token 16 of the system text replaced by
    # another one-token word, so c has the same token count as the agents.
    control = f"seed {seed:04d}. " + " ".join(sw[:10] + [CONTROL_WORD] + sw[11:])

    def private(tag):
        return f"{tag}: " + words(rng, PRIVATE_WORDS)

    return {"seed": seed, "system": system, "control_system": control, "tools": tools,
            "a": [private(f"a{n}") for n in range(1, 7)], "c": private("c1"),
            "b": [private(f"b{n}") for n in range(1, 7)], "warmup": f"warm {seed} {rng.random()}"}


def request(url, system, user, tools, max_tokens=8):
    body = {"model": MODEL, "stream": True, "stream_options": {"include_usage": True},
            "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False},
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": user}]}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    t0 = time.monotonic()
    ttft, text, usage, err = None, "", None, None
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            for raw in r:
                line = raw.decode().strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                ev = json.loads(line[5:])
                usage = ev.get("usage") or usage
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or {}
                    # With tools in the request the reply can stream as tool_calls
                    # deltas only (H25 run 1: every request had empty content).
                    piece = ((d.get("content") or "") + (d.get("reasoning_content") or "")
                             + "".join(json.dumps(tc) for tc in d.get("tool_calls") or []))
                    if piece and ttft is None:
                        ttft = time.monotonic() - t0
                    text += piece
    except Exception as e:  # recorded, scored as a guardrail breach
        err = repr(e)
    return {"ttft": ttft, "total": time.monotonic() - t0, "text_len": len(text),
            "prompt_tokens": (usage or {}).get("prompt_tokens"), "error": err}


def mem_sampler(cmd, stop, out):
    while not stop.is_set():
        t = time.monotonic()
        try:
            v = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5).stdout.split()
            out.append({"t": t, "current": int(v[0]), "swap": int(v[1]) if len(v) > 1 else 0})
        except Exception as e:
            out.append({"t": t, "error": repr(e)})
        stop.wait(max(0.0, 1.0 - (time.monotonic() - t)))


def do_round(a):
    p = gen(a.seed)
    rec = {"seed": a.seed, "arm": a.arm, "warmup": request(a.url, None, p["warmup"], None, 4)}
    rec["S"] = [dict(request(a.url, p["system"], u, p["tools"]), agent=f"a{n + 1}") for n, u in enumerate(p["a"])]
    rec["S"].append(dict(request(a.url, p["control_system"], p["c"], p["tools"]), agent="c"))
    samples, stop = [], threading.Event()
    th = threading.Thread(target=mem_sampler, args=(a.mem_cmd, stop, samples), daemon=True)
    th.start()
    time.sleep(5)
    release = time.monotonic()
    barrier, res = threading.Barrier(6), [None] * 6

    def one(k):
        barrier.wait()
        res[k] = dict(request(a.url, p["system"], p["b"][k], p["tools"]), agent=f"b{k + 1}")

    ts = [threading.Thread(target=one, args=(k,)) for k in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    time.sleep(1)
    stop.set()
    th.join()
    pre = [s["current"] for s in samples if "current" in s and s["t"] < release]
    peak = [s["current"] for s in samples if "current" in s]
    rec["B"] = res
    rec["mem"] = samples
    rec["drss_per_agent_mib"] = ((max(peak) - max(pre)) / 6 / 2**20) if pre and peak else None
    rec["swap_in_burst"] = any(s.get("swap", 0) > 0 for s in samples)
    with open(a.out, "w") as f:
        json.dump(rec, f)
    print(json.dumps({"arm": a.arm, "seed": a.seed,
                      "added_ttft": [r["ttft"] for r in rec["S"][1:6]], "a1": rec["S"][0]["ttft"],
                      "c": rec["S"][6]["ttft"], "drss_per_agent_mib": rec["drss_per_agent_mib"]}))


def void_reasons(rec, tokens):
    why = []
    reqs = rec["S"] + rec["B"]
    if sum(1 for r in reqs if r["error"] or r["ttft"] is None) > 0.02 * len(reqs):
        why.append("errors or null TTFT")
    if any(r["text_len"] == 0 for r in reqs if not r["error"]):
        why.append("empty output")
    pts = {r["prompt_tokens"] for r in rec["S"][:6] + rec["B"]}
    if len(pts) != 1:
        why.append(f"prompt_tokens differ within round {sorted(map(str, pts))}")
    tokens.update(pts)
    if not rec.get("graph_ok", True):
        why.append("graph.pbtxt readback mismatch")
    return why


def bootstrap_ratio(off, on, n=10000, seed=25):
    rng = random.Random(seed)
    rs = sorted(statistics.median(rng.choices(off, k=len(off))) / statistics.median(rng.choices(on, k=len(on)))
                for _ in range(n))
    return rs[int(0.025 * n)], rs[int(0.975 * n) - 1]


def load_check(path):
    """bench/load/load.py matched() over one level's per-arm sample windows."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bench_load", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "load", "load.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with open(path) as f:
        return mod.matched([json.loads(line) for line in f if line.strip()])


def score(files, load=None):
    out = _score(files)
    if load:
        lc = load_check(load)
        out["load"] = lc
        if not lc["matched"]:
            out["T"] = out["M"] = lc["verdict"]
    return out


def _score(files):
    rounds = [json.load(open(f)) for f in files]
    tokens, arms = set(), {"ON": [], "OFF": []}
    for r in rounds:
        r["void"] = void_reasons(r, tokens)
        arms[r["arm"]].append(r)
    out = {"n_rounds": {k: len(v) for k, v in arms.items()},
           "void": {f"{r['arm']}/{r['seed']}": r["void"] for r in rounds if r["void"]}}
    if len(tokens) != 1:
        out["void_cross_arm"] = f"prompt_tokens differ across arms {sorted(map(str, tokens))}"
    ok = {k: [r for r in v if not r["void"]] for k, v in arms.items()}
    added = {k: [r["S"][i]["ttft"] for r in v for i in range(1, 6)] for k, v in ok.items()}
    per_round = {k: [statistics.median(r["S"][i]["ttft"] for i in range(1, 6)) for r in v] for k, v in ok.items()}
    inconc = [k for k in arms if len(arms[k]) - len(ok[k]) > 1 or not ok[k]]
    for k, v in per_round.items():
        if v and (max(v) - min(v)) > 0.3 * statistics.median(v):
            inconc.append(f"{k} spread")
    if not added["ON"] or not added["OFF"]:
        out["T"] = "INCONCLUSIVE (an arm has no valid round)"
        return out
    ratio = statistics.median(added["OFF"]) / statistics.median(added["ON"])
    lo, hi = bootstrap_ratio(added["OFF"], added["ON"])
    pairs = [off / on for off in per_round["OFF"] for on in per_round["ON"]]
    c_on = statistics.median(r["S"][6]["ttft"] for r in ok["ON"])
    a1 = {k: statistics.median(r["S"][0]["ttft"] for r in v) for k, v in ok.items()}
    off_med = statistics.median(added["OFF"])
    out.update({"median_added_ttft_s": {k: statistics.median(v) for k, v in added.items()},
                "n_added": {k: len(v) for k, v in added.items()},
                "per_round_median_s": per_round, "spread_s": {k: max(v) - min(v) for k, v in per_round.items()},
                "ratio": ratio, "ci95": [lo, hi], "confidence_pairs_ge_4x": sum(p >= 4 for p in pairs) / len(pairs),
                "c_on_over_off": c_on / off_med, "a1_on_over_off": a1["ON"] / a1["OFF"],
                "prompt_tokens": sorted(map(str, tokens)), "inconclusive_parts": inconc})
    if inconc or "void_cross_arm" in out:
        t = "INCONCLUSIVE"
    elif c_on / off_med < 0.5:
        t = "VOID (control measured warm: probe defect)"
    elif lo >= 4 and c_on / off_med >= 0.8 and abs(a1["ON"] / a1["OFF"] - 1) <= 0.15:
        t = "SUPPORTED"
    elif ratio < 2:
        t = "REFUTED"
    else:
        t = "INCONCLUSIVE (2x <= ratio < 4x or CI straddles 4x, or a control failed)"
    out["T"] = t
    m = {k: [r["drss_per_agent_mib"] for r in v if r["drss_per_agent_mib"] is not None and not r["swap_in_burst"]]
         for k, v in ok.items()}
    if m["ON"] and m["OFF"]:
        mr = statistics.median(m["ON"]) / statistics.median(m["OFF"])
        out["M"] = {"drss_per_agent_mib": {k: statistics.median(v) for k, v in m.items()}, "on_over_off": mr,
                    "verdict": "SUPPORTED (<= 0.5x)" if mr <= 0.5 else "no memory benefit (>= 0.8x)" if mr >= 0.8
                    else "INCONCLUSIVE (0.5x-0.8x)"}
    else:
        out["M"] = "INCONCLUSIVE (no swap-free burst in an arm)"
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen")
    g.add_argument("--seed", type=int, required=True)
    r = sub.add_parser("round")
    r.add_argument("--seed", type=int, required=True)
    r.add_argument("--url", required=True)
    r.add_argument("--arm", choices=["ON", "OFF"], required=True)
    r.add_argument("--mem-cmd", required=True)
    r.add_argument("--out", required=True)
    s = sub.add_parser("score")
    s.add_argument("--load", help="load.jsonl from bench/load/load.py sample, tagged by arm")
    s.add_argument("files", nargs="+")
    a = ap.parse_args()
    if a.cmd == "gen":
        print(json.dumps(gen(a.seed), indent=1))
    elif a.cmd == "round":
        do_round(a)
    else:
        print(json.dumps(score(a.files, a.load), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
