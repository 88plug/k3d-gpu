#!/usr/bin/env python3
"""Replay agent traces against an OpenAI-compatible chat endpoint (stdlib only).

Fixed-prompt LLM benchmarks miss what agent serving looks like: a long system
prompt shared by every session, tool schemas and tool-call JSON, context that
grows every turn, and sessions that arrive in bursts. This tool replays a trace
with those properties and measures, per turn:

  ttft_s          time from request send to the first content/tool-call delta
  decode_tps      (completion_tokens - 1) / (t_last_delta - t_first_delta)
  e2e_s           request send to end of stream
  prompt_tokens, completion_tokens, cached_tokens   (from the final usage chunk;
                  requested with stream_options.include_usage. cached_tokens is
                  usage.prompt_tokens_details.cached_tokens: vLLM reports it with
                  --enable-prompt-tokens-details, OVMS reports it on continuous
                  batching servables.)
  tool_source     structured | content | none   (where a tool call was found)
  tool_valid      name is a declared tool, arguments are a JSON object, and all
                  "required" parameters are present with the declared JSON types

Prefix-cache benefit is measured two ways:
  * server-reported cached_tokens / prompt_tokens, and
  * an A/B control: --cache-bust request prepends a unique nonce line to the
    system prompt of every request, so no request shares a prefix with any
    other. Token count changes by a few tokens only. `compare` divides the
    per-turn-index median TTFT of the busted run by the normal run.
    --cache-bust session uses one nonce per session: intra-session reuse stays,
    cross-session sharing of the system prompt is removed.

Conversation history is teacher-forced: after each turn the trace's recorded
assistant message (not the live reply) is appended, so every run sends the
byte-identical prompt sequence and runs are comparable across servers.

Trace format (JSON):
  {"name": str, "family": str, "params": {...},
   "tools": [OpenAI tool objects],
   "sessions": [{"id": str, "start_s": float, "system": str,
                 "turns": [{"append": [messages], "max_tokens": int,
                            "think_s": float, "expect_tool": str|null,
                            "recorded": assistant message}]}]}

Subcommands:
  gen      write a synthetic trace (see agent_trace_gen.py for the families)
  run      replay a trace, write per-turn JSONL + a summary JSON
  compare  prefix-cache benefit from a normal run vs a --cache-bust request run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}
HERMES_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


# ---------------------------------------------------------------- tool calls
def _type_ok(value, jtype):
    if jtype is None:
        return True
    py = JSON_TYPES.get(jtype)
    if py is None:
        return True
    if jtype in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, py)


def validate_calls(calls, tools):
    """Return (valid, reason) for a list of {"name", "arguments"} calls."""
    if not calls:
        return False, "no tool call"
    by_name = {t["function"]["name"]: t["function"] for t in tools}
    for call in calls:
        fn = by_name.get(call.get("name"))
        if fn is None:
            return False, f"unknown tool {call.get('name')!r}"
        args = call.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError as e:
                return False, f"arguments not JSON: {e.msg}"
        if not isinstance(args, dict):
            return False, "arguments not an object"
        schema = fn.get("parameters") or {}
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in args:
                return False, f"missing required {req!r}"
        for key, val in args.items():
            if key in props and not _type_ok(val, props[key].get("type")):
                return False, f"bad type for {key!r}"
    return True, "ok"


def calls_from_content(content):
    """Tool calls emitted as text: a bare JSON object or Hermes <tool_call> tags."""
    found = []
    for blob in HERMES_RE.findall(content or ""):
        try:
            found.append(json.loads(blob))
        except json.JSONDecodeError:
            found.append({"name": None, "arguments": blob})
    if not found:
        text = (content or "").strip()
        if text.startswith("{"):
            try:
                found.append(json.loads(text))
            except json.JSONDecodeError:
                return []
    calls = []
    for obj in found:
        if isinstance(obj, dict) and "name" in obj:
            args = obj.get("arguments", obj.get("parameters", {}))
            calls.append({"name": obj["name"], "arguments": args})
    return calls


# ---------------------------------------------------------------- one request
def stream_chat(url, payload, api_key=None, timeout=600.0):
    """POST a streaming chat request. Returns a dict of timings and parsed output."""
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers)
    out = {"error": None, "content": "", "tool_calls": [], "usage": None, "n_chunks": 0}
    acc = {}  # tool call index -> {"name", "arguments"}
    t0 = time.perf_counter()
    t_first = t_last = None
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
                    out["usage"] = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    got = False
                    if delta.get("content"):
                        out["content"] += delta["content"]
                        got = True
                    if delta.get("reasoning_content"):
                        got = True
                    for tc in delta.get("tool_calls") or []:
                        slot = acc.setdefault(
                            tc.get("index", 0), {"name": "", "arguments": ""}
                        )
                        fn = tc.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
                        got = True
                    if got:
                        now = time.perf_counter()
                        if t_first is None:
                            t_first = now
                        t_last = now
                        out["n_chunks"] += 1
    except (urllib.error.URLError, OSError, ValueError) as e:
        body = ""
        if isinstance(e, urllib.error.HTTPError):
            body = e.read().decode("utf-8", "replace")[:300]
            e.close()
        out["error"] = f"{type(e).__name__}: {e} {body}".strip()
    t_end = time.perf_counter()
    out["tool_calls"] = [acc[k] for k in sorted(acc)]
    out["ttft_s"] = None if t_first is None else t_first - t0
    out["e2e_s"] = t_end - t0
    out["decode_s"] = None if t_first is None else t_last - t_first
    return out


# ---------------------------------------------------------------- replay
def build_system(session, bust, run_nonce):
    if bust == "none":
        return session["system"]
    if bust == "session":
        nonce = f"{run_nonce}-{session['id']}"
    else:
        nonce = uuid.uuid4().hex
    return f"[nonce {nonce}]\n{session['system']}"


def replay_session(trace, session, args, t_start, results, lock, run_nonce):
    delay = session.get("start_s", 0.0) * args.time_scale - (
        time.perf_counter() - t_start
    )
    if delay > 0:
        time.sleep(delay)
    history = []
    for idx, turn in enumerate(session["turns"]):
        if turn.get("think_s") and idx > 0:
            time.sleep(turn["think_s"] * args.time_scale)
        history.extend(turn.get("append", []))
        messages = [
            {
                "role": "system",
                "content": build_system(session, args.cache_bust, run_nonce),
            }
        ]
        messages += history
        payload = {
            "model": args.model,
            "messages": messages,
            "max_tokens": turn.get("max_tokens", 128),
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": args.temperature,
        }
        if trace.get("tools"):
            payload["tools"] = trace["tools"]
            payload["tool_choice"] = args.tool_choice
        sent_at = time.perf_counter() - t_start
        r = stream_chat(args.url, payload, args.api_key, args.timeout)
        rec = summarize_turn(trace, session, idx, turn, r, sent_at)
        with lock:
            results.append(rec)
        if args.verbose:
            print(json.dumps(rec), file=sys.stderr)
        if turn.get("recorded"):
            history.append(turn["recorded"])


def summarize_turn(trace, session, idx, turn, r, sent_at):
    usage = r["usage"] or {}
    details = usage.get("prompt_tokens_details") or {}
    ctoks = usage.get("completion_tokens")
    if ctoks is None:
        ctoks = r["n_chunks"]  # one delta per token is the common streaming shape
    tps = None
    if r["decode_s"] and ctoks and ctoks > 1:
        tps = (ctoks - 1) / r["decode_s"]
    source, calls = "none", []
    if r["tool_calls"]:
        source, calls = "structured", r["tool_calls"]
    else:
        calls = calls_from_content(r["content"])
        if calls:
            source = "content"
    valid, reason = (
        validate_calls(calls, trace.get("tools", []))
        if calls
        else (False, "no tool call")
    )
    return {
        "session": session["id"],
        "turn": idx,
        "sent_at_s": round(sent_at, 4),
        "error": r["error"],
        "ttft_s": r["ttft_s"],
        "e2e_s": r["e2e_s"],
        "decode_tps": tps,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": ctoks,
        "cached_tokens": details.get("cached_tokens"),
        "expect_tool": turn.get("expect_tool"),
        "tool_source": source,
        "tool_valid": valid,
        "tool_reason": reason,
        "tool_names": [c.get("name") for c in calls],
    }


def run_trace(trace, args):
    results, lock = [], threading.Lock()
    sem = threading.Semaphore(args.max_concurrency) if args.max_concurrency else None
    run_nonce = uuid.uuid4().hex[:12]
    t_start = time.perf_counter()

    def worker(sess):
        if sem:
            with sem:
                replay_session(trace, sess, args, t_start, results, lock, run_nonce)
        else:
            replay_session(trace, sess, args, t_start, results, lock, run_nonce)

    threads = [
        threading.Thread(target=worker, args=(s,), daemon=True)
        for s in trace["sessions"]
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t_start
    results.sort(key=lambda r: (r["session"], r["turn"]))
    return results, wall


# ---------------------------------------------------------------- summary
def _pct(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(results, wall, meta):
    ok = [r for r in results if not r["error"]]
    expect = [r for r in ok if r["expect_tool"]]
    no_expect = [r for r in ok if not r["expect_tool"]]
    by_turn = {}
    for r in ok:
        by_turn.setdefault(r["turn"], []).append(r)
    cached = [
        (r["cached_tokens"], r["prompt_tokens"])
        for r in ok
        if r["cached_tokens"] is not None and r["prompt_tokens"]
    ]
    out_tokens = sum(r["completion_tokens"] or 0 for r in ok)
    return {
        **meta,
        "requests": len(results),
        "errors": len(results) - len(ok),
        "wall_s": round(wall, 3),
        "output_tokens_per_s": round(out_tokens / wall, 3) if wall else None,
        "ttft_s": {
            "p50": _pct([r["ttft_s"] for r in ok], 0.5),
            "p90": _pct([r["ttft_s"] for r in ok], 0.9),
            "p99": _pct([r["ttft_s"] for r in ok], 0.99),
        },
        "decode_tps_p50": _pct([r["decode_tps"] for r in ok], 0.5),
        "tool_turns": len(expect),
        "tool_valid_structured": (
            sum(r["tool_valid"] and r["tool_source"] == "structured" for r in expect)
            / len(expect)
        )
        if expect
        else None,
        "tool_valid_any": (sum(r["tool_valid"] for r in expect) / len(expect))
        if expect
        else None,
        "tool_wrong_name": sum(
            1
            for r in expect
            if r["tool_valid"] and r["expect_tool"] not in r["tool_names"]
        ),
        "tool_unexpected": sum(1 for r in no_expect if r["tool_source"] != "none"),
        "cached_fraction": (sum(c for c, _ in cached) / sum(p for _, p in cached))
        if cached
        else None,
        "per_turn": {
            str(t): {
                "n": len(rs),
                "ttft_p50": _pct([r["ttft_s"] for r in rs], 0.5),
                "prompt_tokens_p50": _pct([r["prompt_tokens"] for r in rs], 0.5),
                "cached_p50": _pct([r["cached_tokens"] for r in rs], 0.5),
            }
            for t, rs in sorted(by_turn.items())
        },
    }


def compare(base, busted):
    """Per-turn-index TTFT ratio busted/base. >1 means the prefix cache helped."""
    rows = {}
    for t, b in base["per_turn"].items():
        x = busted["per_turn"].get(t)
        if not x or not b["ttft_p50"] or not x["ttft_p50"]:
            continue
        rows[t] = {
            "ttft_p50_cached": b["ttft_p50"],
            "ttft_p50_busted": x["ttft_p50"],
            "speedup": x["ttft_p50"] / b["ttft_p50"],
        }
    overall = None
    if base["ttft_s"]["p50"] and busted["ttft_s"]["p50"]:
        overall = busted["ttft_s"]["p50"] / base["ttft_s"]["p50"]
    return {
        "base_cache_bust": base.get("cache_bust"),
        "busted_cache_bust": busted.get("cache_bust"),
        "ttft_p50_speedup": overall,
        "cached_fraction_base": base.get("cached_fraction"),
        "cached_fraction_busted": busted.get("cached_fraction"),
        "per_turn": rows,
    }


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="write a synthetic trace")
    g.add_argument("family", choices=["support", "coding", "fanout"])
    g.add_argument("-o", "--out", required=True)
    g.add_argument("--seed", type=int, default=0)
    g.add_argument(
        "--scale", type=float, default=1.0, help="multiplies session counts (min 1)"
    )

    r = sub.add_parser("run", help="replay a trace")
    r.add_argument("trace")
    r.add_argument("--url", default="http://127.0.0.1:8000/v3/chat/completions")
    r.add_argument("--model", required=True)
    r.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"))
    r.add_argument(
        "--out",
        required=True,
        help="prefix: writes PREFIX.jsonl and PREFIX.summary.json",
    )
    r.add_argument(
        "--cache-bust", choices=["none", "session", "request"], default="none"
    )
    r.add_argument(
        "--time-scale",
        type=float,
        default=1.0,
        help="multiplies start_s and think_s (0 = no waits)",
    )
    r.add_argument(
        "--max-concurrency", type=int, default=0, help="0 = one thread per session"
    )
    r.add_argument("--temperature", type=float, default=0.0)
    r.add_argument("--tool-choice", default="auto")
    r.add_argument("--timeout", type=float, default=600.0)
    r.add_argument("-v", "--verbose", action="store_true")

    c = sub.add_parser("compare", help="prefix-cache benefit: normal vs busted summary")
    c.add_argument("base")
    c.add_argument("busted")

    a = ap.parse_args(argv)
    if a.cmd == "gen":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import agent_trace_gen

        trace = agent_trace_gen.FAMILIES[a.family](seed=a.seed, scale=a.scale)
        with open(a.out, "w") as f:
            json.dump(trace, f)
        print(json.dumps(agent_trace_gen.describe(trace)))
        return 0
    if a.cmd == "compare":
        with open(a.base) as f:
            base = json.load(f)
        with open(a.busted) as f:
            busted = json.load(f)
        print(json.dumps(compare(base, busted), indent=2))
        return 0
    with open(a.trace) as f:
        trace = json.load(f)
    results, wall = run_trace(trace, a)
    meta = {
        "trace": trace.get("name"),
        "family": trace.get("family"),
        "url": a.url,
        "model": a.model,
        "cache_bust": a.cache_bust,
        "time_scale": a.time_scale,
    }
    summary = summarize(results, wall, meta)
    with open(a.out + ".jsonl", "w") as f:
        for rec in results:
            f.write(json.dumps(rec) + "\n")
    with open(a.out + ".summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "requests",
                    "errors",
                    "wall_s",
                    "ttft_s",
                    "decode_tps_p50",
                    "tool_valid_any",
                    "cached_fraction",
                )
            }
        )
    )
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
