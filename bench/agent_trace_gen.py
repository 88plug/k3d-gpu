"""Synthetic agent-trace families for agent_trace.py (stdlib only, seeded).

Lengths are set in words, not tokens: no tokenizer is available without
third-party packages. English BPE tokenizers emit roughly 1.3 tokens per word
on this vocabulary; the run summary records the server's real prompt_tokens, so
treat word counts as the knob and prompt_tokens as the measurement.

Every family gives each session the byte-identical system prompt (shared prefix)
plus a recorded assistant message per turn, so prompts are deterministic for a
given seed.

support  (customer-support agent, tau-bench-shaped)
  sessions          16 * scale, Poisson arrivals, mean gap 2.0 s
  system prompt     2400 words policy, shared by all sessions
  tools             8 functions (lookup/modify order, refund, ...)
  turns/session     uniform 3..6; turn 0 user ask -> tool call; middle turns
                    tool result (40..120 words) -> tool call; last turn tool
                    result -> final text answer
  max_tokens        96 per turn; think_s 1.0 between turns
  stresses          cross-session system-prompt reuse, small decode, many
                    concurrent short sessions

coding  (software agent, SWE-style)
  sessions          4 * scale, arrivals every 5.0 s
  system prompt     900 words, shared
  tools             4 functions (read_file, search, run_tests, edit_file)
  turns/session     uniform 8..12; every turn but the last appends a tool
                    result of 400..1600 words (file bodies, test logs)
  max_tokens        192; think_s 0.5
  stresses          context growth to ~12k words (~16k tokens) by the last turn, intra-
                    session prefix reuse, long prefill per turn

fanout  (orchestrator spawning parallel sub-agents)
  bursts            3 * scale bursts, 10 s apart, 8 sessions per burst all
                    starting at the same instant
  system prompt     1200 words, shared; plus a per-burst task brief of 600
                    words that all 8 sub-agents in the burst share
  tools             3 functions (web_search, fetch, report)
  turns/session     2..3; first turn -> tool call, last turn -> text
  max_tokens        128; think_s 0.2
  stresses          bursty concurrent prefill, two-level shared prefix
                    (system + burst brief), queueing under arrival spikes

needle  (coding-shaped, with an answer oracle; for tool-result compression)
  sessions          4 * scale, arrivals every 5.0 s, turns uniform 6..10
  system prompt     900 words, shared
  tools             5 functions; each returns a realistic tool output from
                    tool_compress.fixture(): run_tests -> pytest log, search ->
                    grep -n hits, read_file -> Python source, git_diff -> unified
                    diff, fetch -> prose page. 400..1600 words each, one planted
                    fact at a seeded position, call arguments = the fixture query
  last turn         the final tool result, then a user question about one
                    planted fact drawn uniformly from the session's tool results;
                    "expect_answer" holds the answer string, max_tokens 64
  stresses          whether compression keeps the facts a later turn needs
"""

from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tool_compress  # noqa: E402

VOCAB = (
    "account address agent allow amount answer apply approve available balance "
    "before billing booking cancel card case change charge check claim confirm "
    "contact cost credit customer date deliver detail device discount document "
    "eligible email error exchange expire fee file flight follow handle history "
    "identity include invoice issue item limit locate manager member message "
    "method notice number offer order original package payment pending period "
    "permit plan policy price process product provide purchase reason receipt "
    "record refund reject request require reserve return review rule schedule "
    "service shipping status store submit support system ticket total transfer "
    "update user valid verify warranty window within written function module "
    "import return value buffer thread socket parse config driver kernel memory "
    "cache index query table schema commit branch merge patch build compile test "
    "assert expect mock fixture runtime stack frame trace log level debug"
).split()


def words(rng, n):
    return " ".join(rng.choice(VOCAB) for _ in range(n))


def tool(name, desc, props, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


STR = {"type": "string"}
INT = {"type": "integer"}


def call_msg(call_id, name, args):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def result_msg(call_id, text):
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def _session(
    rng,
    sid,
    start,
    system,
    tools,
    n_turns,
    user_words,
    result_words,
    max_tokens,
    think_s,
    args_for,
    brief=None,
):
    turns, prev_id = [], None
    for t in range(n_turns):
        last = t == n_turns - 1
        if t == 0:
            first = f"{brief}\n\n" if brief else ""
            append = [{"role": "user", "content": first + words(rng, user_words)}]
        else:
            append = [result_msg(prev_id, words(rng, rng.randint(*result_words)))]
        turn = {"append": append, "max_tokens": max_tokens, "think_s": think_s}
        if last:
            turn["expect_tool"] = None
            turn["recorded"] = {"role": "assistant", "content": words(rng, 40)}
        else:
            fn = rng.choice(tools)["function"]
            prev_id = f"call_{sid}_{t}"
            turn["expect_tool"] = fn["name"]
            turn["recorded"] = call_msg(prev_id, fn["name"], args_for(rng, fn))
        turns.append(turn)
    return {"id": sid, "start_s": round(start, 3), "system": system, "turns": turns}


def _args_for(rng, fn):
    props = fn["parameters"]["properties"]
    return {
        k: (rng.randint(1, 99999) if v["type"] == "integer" else words(rng, 3))
        for k, v in props.items()
    }


def support(seed=0, scale=1.0):
    rng = random.Random(seed)
    params = {
        "sessions": max(1, round(16 * scale)),
        "mean_gap_s": 2.0,
        "system_words": 2400,
        "turns": [3, 6],
        "user_words": 60,
        "result_words": [40, 120],
        "max_tokens": 96,
        "think_s": 1.0,
    }
    tools = [
        tool(n, d, p, r)
        for n, d, p, r in [
            ("get_user", "Look up a customer by id.", {"user_id": STR}, ["user_id"]),
            ("get_order", "Fetch an order.", {"order_id": STR}, ["order_id"]),
            (
                "list_orders",
                "List a user's orders.",
                {"user_id": STR, "limit": INT},
                ["user_id"],
            ),
            (
                "cancel_order",
                "Cancel a pending order.",
                {"order_id": STR, "reason": STR},
                ["order_id", "reason"],
            ),
            (
                "refund",
                "Refund an order.",
                {"order_id": STR, "amount_cents": INT},
                ["order_id", "amount_cents"],
            ),
            (
                "exchange_item",
                "Exchange an item.",
                {"order_id": STR, "item_id": STR, "new_item_id": STR},
                ["order_id", "item_id", "new_item_id"],
            ),
            (
                "update_address",
                "Change shipping address.",
                {"user_id": STR, "address": STR},
                ["user_id", "address"],
            ),
            (
                "transfer_to_human",
                "Escalate to a human agent.",
                {"summary": STR},
                ["summary"],
            ),
        ]
    ]
    system = "You are a customer support agent. Follow the policy.\n" + words(
        rng, params["system_words"]
    )
    t, sessions = 0.0, []
    for i in range(params["sessions"]):
        sessions.append(
            _session(
                rng,
                f"s{i}",
                t,
                system,
                tools,
                rng.randint(*params["turns"]),
                params["user_words"],
                params["result_words"],
                params["max_tokens"],
                params["think_s"],
                _args_for,
            )
        )
        t += rng.expovariate(1 / params["mean_gap_s"])
    return {
        "name": f"support-s{seed}",
        "family": "support",
        "params": params,
        "tools": tools,
        "sessions": sessions,
    }


def coding(seed=0, scale=1.0):
    rng = random.Random(seed)
    params = {
        "sessions": max(1, round(4 * scale)),
        "gap_s": 5.0,
        "system_words": 900,
        "turns": [8, 12],
        "user_words": 120,
        "result_words": [400, 1600],
        "max_tokens": 192,
        "think_s": 0.5,
    }
    tools = [
        tool(n, d, p, r)
        for n, d, p, r in [
            ("read_file", "Read a file.", {"path": STR, "start_line": INT}, ["path"]),
            (
                "search",
                "Grep the repository.",
                {"pattern": STR, "path": STR},
                ["pattern"],
            ),
            ("run_tests", "Run the test suite.", {"target": STR}, ["target"]),
            (
                "edit_file",
                "Replace text in a file.",
                {"path": STR, "old": STR, "new": STR},
                ["path", "old", "new"],
            ),
        ]
    ]
    system = "You are a software engineering agent working in a repository.\n" + words(
        rng, params["system_words"]
    )
    sessions = [
        _session(
            rng,
            f"c{i}",
            i * params["gap_s"],
            system,
            tools,
            rng.randint(*params["turns"]),
            params["user_words"],
            params["result_words"],
            params["max_tokens"],
            params["think_s"],
            _args_for,
        )
        for i in range(params["sessions"])
    ]
    return {
        "name": f"coding-s{seed}",
        "family": "coding",
        "params": params,
        "tools": tools,
        "sessions": sessions,
    }


def fanout(seed=0, scale=1.0):
    rng = random.Random(seed)
    params = {
        "bursts": max(1, round(3 * scale)),
        "burst_gap_s": 10.0,
        "fan": 8,
        "system_words": 1200,
        "brief_words": 600,
        "turns": [2, 3],
        "user_words": 40,
        "result_words": [150, 400],
        "max_tokens": 128,
        "think_s": 0.2,
    }
    tools = [
        tool(n, d, p, r)
        for n, d, p, r in [
            ("web_search", "Search the web.", {"query": STR, "k": INT}, ["query"]),
            ("fetch", "Fetch a URL.", {"url": STR}, ["url"]),
            (
                "report",
                "Report findings to the orchestrator.",
                {"summary": STR},
                ["summary"],
            ),
        ]
    ]
    system = "You are a research sub-agent. Use tools, then report.\n" + words(
        rng, params["system_words"]
    )
    sessions = []
    for b in range(params["bursts"]):
        brief = "Task brief: " + words(rng, params["brief_words"])
        for k in range(params["fan"]):
            sessions.append(
                _session(
                    rng,
                    f"b{b}k{k}",
                    b * params["burst_gap_s"],
                    system,
                    tools,
                    rng.randint(*params["turns"]),
                    params["user_words"],
                    params["result_words"],
                    params["max_tokens"],
                    params["think_s"],
                    _args_for,
                    brief=brief,
                )
            )
    return {
        "name": f"fanout-s{seed}",
        "family": "fanout",
        "params": params,
        "tools": tools,
        "sessions": sessions,
    }


NEEDLE_TOOLS = {
    "run_tests": ("pytest", "Run the test suite.", {"target": STR}),
    "search": ("grep", "Grep the repository.", {"pattern": STR}),
    "read_file": ("source", "Read a file.", {"path": STR}),
    "git_diff": ("diff", "Show the working-tree diff.", {"path": STR}),
    "fetch": ("prose", "Fetch a URL.", {"url": STR}),
}


def needle(seed=0, scale=1.0):
    rng = random.Random(seed)
    params = {
        "sessions": max(1, round(4 * scale)),
        "gap_s": 5.0,
        "system_words": 900,
        "turns": [6, 10],
        "user_words": 120,
        "result_words": [400, 1600],
        "max_tokens": 192,
        "answer_max_tokens": 64,
        "think_s": 0.5,
    }
    tools = [tool(n, d, props, list(props)) for n, (_, d, props) in NEEDLE_TOOLS.items()]
    system = "You are a software engineering agent working in a repository.\n" + words(
        rng, params["system_words"]
    )
    sessions = []
    for i in range(params["sessions"]):
        sid = f"n{i}"
        n_turns = rng.randint(*params["turns"])
        turns, facts, prev_id, fx = [], [], None, None  # fx: last turn's tool output
        for t in range(n_turns):
            if t == 0:
                append = [{"role": "user", "content": words(rng, params["user_words"])}]
            else:
                append = [result_msg(prev_id, fx["text"])]
                facts += [dict(f, kind=fx["kind"], pos=fx["pos"], turn=t) for f in fx["facts"]]
            turn = {"append": append, "max_tokens": params["max_tokens"], "think_s": params["think_s"]}
            if t == n_turns - 1:
                f = rng.choice(facts)
                append.append({"role": "user", "content": f["q"] + " Answer with the exact text only."})
                turn.update(max_tokens=params["answer_max_tokens"], expect_tool=None,
                            expect_answer=f["answer"],
                            answer_meta={k: f[k] for k in ("kind", "pos", "turn", "where")},
                            recorded={"role": "assistant", "content": f["answer"]})
            else:
                name = rng.choice(list(NEEDLE_TOOLS))
                kind = NEEDLE_TOOLS[name][0]
                fx = tool_compress.fixture(kind, f"{seed}-{sid}-{t}", tuple(params["result_words"]))
                args = {k: str(v) for k, v in fx["query"].items()}
                prev_id = f"call_{sid}_{t}"
                turn["expect_tool"] = name
                turn["recorded"] = call_msg(prev_id, name, args)
            turns.append(turn)
        sessions.append({"id": sid, "start_s": i * params["gap_s"], "system": system, "turns": turns})
    return {"name": f"needle-s{seed}", "family": "needle", "params": params, "tools": tools,
            "sessions": sessions}


FAMILIES = {"support": support, "coding": coding, "fanout": fanout, "needle": needle}


def describe(trace):
    """Word-count shape of a trace: sessions, turns, prompt words at the last turn."""

    def msg_words(m):
        n = len((m.get("content") or "").split())
        for tc in m.get("tool_calls") or []:
            n += len(tc["function"]["arguments"].split())
        return n

    last_prompt, turns, tool_turns = [], 0, 0
    for s in trace["sessions"]:
        total = len(s["system"].split())
        for t in s["turns"]:
            total += sum(msg_words(m) for m in t["append"])
            turns += 1
            tool_turns += bool(t.get("expect_tool"))
            if t is not s["turns"][-1]:
                total += msg_words(t["recorded"])
        last_prompt.append(total)
    last_prompt.sort()
    return {
        "name": trace["name"],
        "sessions": len(trace["sessions"]),
        "turns": turns,
        "tool_turns": tool_turns,
        "system_words": len(trace["sessions"][0]["system"].split()),
        "last_prompt_words_p50": last_prompt[len(last_prompt) // 2],
        "last_prompt_words_max": last_prompt[-1],
        "span_s": max(s["start_s"] for s in trace["sessions"]),
    }
