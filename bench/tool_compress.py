#!/usr/bin/env python3
"""Tool-result compression for agent traces, plus an evidence-retention oracle.

An agent appends every tool result to its context, and the serving engine must
prefill it. This module shrinks a tool result to a word budget before it is
returned to the agent, so the cost lands in the tool step (on E-cores, see
examples/tool-compress/) instead of in the engine's prefill. Stdlib only.

Methods (all honour the same budget: ceil(ratio * words_in), elision markers
included, so every method hands the engine the same number of words):

  none       identity.
  trunc      head + tail: keep lines alternately from both ends. The zero-cost
             tuned baseline.
  keeplines  trunc's ordering, with lines promoted by structure-aware salience:
             error/fail/assert/traceback lines, diff +/- and @@ lines, definition
             lines (def/class/func/fn/struct), file:line references, terms from the
             tool-call query, and one line of context around promoted lines. With
             no cue in the text it degrades to exactly trunc.
  randdrop   uniformly random line subset (seeded by the text). Ablation: a
             compressor that keeps the same budget with no structure.
  llmlingua2 LLMLingua-2 token classification (arXiv 2403.12968) through the
             `llmlingua` package (0.2.2, torch, CPU). Optional, not imported
             unless selected; no OpenVINO path yet.

Lines longer than UNIT_WORDS words are split into UNIT_WORDS-word units first, so
prose without newlines is still selectable.

Evidence retention (the quality oracle that runs with no LLM): `fixtures` builds
realistic tool outputs (pytest log, grep hits, a source file, a unified diff,
prose) with planted facts at seeded positions, each with a question and an
expected answer string. A fact is retained when its answer string survives
compression. Retention bounds task success from above: a fact the compressor
deleted cannot be answered from context.

Subcommands
  compress   compress stdin, print the result and a stats line on stderr
  retention  evidence retention per method x ratio x fixture kind (JSON)
  serve      HTTP service: POST /compress {"text","method","ratio","query"}
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import re
import sys
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UNIT_WORDS = 40
MARKER = "[... {n} lines omitted ...]"
MARKER_WORDS = len(MARKER.format(n=1).split())  # 5
METHODS = ("none", "trunc", "keeplines", "randdrop", "llmlingua2")

SALIENT = re.compile(
    r"error|fail|exception|traceback|assert|warning|panic|fatal|denied|"
    r"not found|undefined|segfault|timed? ?out|^E\s{2,}",
    re.I,
)
DIFF = re.compile(r"^(@@|[+-](?![+-]{2}\s))")
DEFN = re.compile(r"(^|^\S+:\d+:)\s*(async\s+def|def|class|func|fn|pub\s+fn|impl|struct|interface|function)\b")
FILELINE = re.compile(r"\S+:\d+")
TERM = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


# ---------------------------------------------------------------- units + budget
def nwords(s):
    return len(s.split())


def units(text):
    """Split into lines; break lines longer than UNIT_WORDS into word chunks."""
    out = []
    for line in text.split("\n"):
        w = line.split()
        if len(w) <= UNIT_WORDS:
            out.append(line)
        else:
            out += [" ".join(w[i : i + UNIT_WORDS]) for i in range(0, len(w), UNIT_WORDS)]
    return out


def budget_words(text, ratio):
    return max(1, math.ceil(ratio * nwords(text)))


def render(us, keep):
    """Kept units in order, each run of dropped units replaced by one marker."""
    out, n = [], len(us)
    prev = -1
    for k in sorted(keep):
        if k - prev > 1:
            out.append(MARKER.format(n=k - prev - 1))
        out.append(us[k])
        prev = k
    if n - 1 - prev > 0:
        out.append(MARKER.format(n=n - 1 - prev))
    return "\n".join(out)


def select(us, order, budget):
    """Greedy: take units in `order` while words + marker words fit the budget."""
    cost = [max(1, nwords(u)) for u in us]
    n = len(us)
    kept = []  # sorted indices
    used = MARKER_WORDS if n else 0  # everything dropped = one marker
    for i in order:
        j = bisect.bisect_left(kept, i)
        p = kept[j - 1] if j else -1
        q = kept[j] if j < len(kept) else n
        # The gap (p, q) held one marker; i splits it into (p, i) and (i, q).
        d_markers = (1 if i - p > 1 else 0) + (1 if q - i > 1 else 0) - 1
        add = cost[i] + d_markers * MARKER_WORDS
        if used + add > budget:
            continue
        used += add
        kept.insert(j, i)
    return kept, used


def ends_order(n):
    """Indices alternating from the two ends inward: 0, n-1, 1, n-2, ..."""
    return sorted(range(n), key=lambda i: (min(i, n - 1 - i), i))


# ---------------------------------------------------------------- methods
def query_terms(query):
    if not query:
        return set()
    if not isinstance(query, str):
        query = json.dumps(query)
    return {t.lower() for t in TERM.findall(query)} - {"path", "pattern", "target", "the", "and"}


def salience(us, query=None):
    terms = query_terms(query)
    s = [0] * len(us)
    for i, u in enumerate(us):
        if SALIENT.search(u):
            s[i] += 3
        if DIFF.match(u):
            s[i] += 3
        if DEFN.match(u):
            s[i] += 2
        if FILELINE.search(u):
            s[i] += 1
        if terms:
            s[i] += 2 * len(terms & {t.lower() for t in TERM.findall(u)})
    ctx = [0] * len(us)
    for i, v in enumerate(s):
        if v >= 3:
            for j in (i - 1, i + 1):
                if 0 <= j < len(us):
                    ctx[j] = 1
    return [a + b for a, b in zip(s, ctx)]


def _trunc(text, ratio, query=None):
    us = units(text)
    kept, _ = select(us, ends_order(len(us)), budget_words(text, ratio))
    return render(us, kept)


def _keeplines(text, ratio, query=None):
    us = units(text)
    n = len(us)
    sal = salience(us, query)
    rank = {i: r for r, i in enumerate(ends_order(n))}
    order = sorted(range(n), key=lambda i: (-sal[i], rank[i]))
    kept, _ = select(us, order, budget_words(text, ratio))
    return render(us, kept)


def _randdrop(text, ratio, query=None):
    us = units(text)
    order = list(range(len(us)))
    random.Random(zlib.crc32(text.encode())).shuffle(order)
    kept, _ = select(us, order, budget_words(text, ratio))
    return render(us, kept)


_LINGUA = {}


def _llmlingua2(text, ratio, query=None):
    """LLMLingua-2 via llmlingua==0.2.2 (PromptCompressor(..., use_llmlingua2=True))."""
    if "pc" not in _LINGUA:
        from llmlingua import PromptCompressor  # optional dependency

        _LINGUA["pc"] = PromptCompressor(
            model_name="microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
            use_llmlingua2=True,
            device_map="cpu",
        )
    res = _LINGUA["pc"].compress_prompt(text, rate=ratio, force_tokens=["\n"])
    return res["compressed_prompt"]


IMPL = {
    "none": lambda text, ratio, query=None: text,
    "trunc": _trunc,
    "keeplines": _keeplines,
    "randdrop": _randdrop,
    "llmlingua2": _llmlingua2,
}


def compress(text, method="none", ratio=0.5, query=None):
    """Return (compressed_text, stats). ratio >= 1 or method none is the identity."""
    if method not in IMPL:
        raise ValueError(f"unknown method {method!r}")
    t0 = time.perf_counter()
    out = text if (method == "none" or ratio >= 1.0 or not text) else IMPL[method](text, ratio, query)
    ms = (time.perf_counter() - t0) * 1e3
    return out, {
        "method": method,
        "ratio": ratio,
        "words_in": nwords(text),
        "words_out": nwords(out),
        "compress_ms": round(ms, 3),
    }


# ---------------------------------------------------------------- fixtures
MODS = "auth billing cache config driver index kernel parser queue router schema session store sync worker".split()
VERBS = "load parse build check flush merge open read reset resolve send split store sync write".split()
NOUNS = "buffer chunk entry frame header index item key node offset page record row slot token".split()


def _ident(rng):
    return f"{rng.choice(VERBS)}_{rng.choice(NOUNS)}"


def _py_line(rng, depth=1):
    pad = "    " * depth
    k = rng.randrange(6)
    a, b = rng.choice(NOUNS), rng.choice(NOUNS)
    return pad + [
        f"{a} = {b}.get({rng.randint(0, 99)})",
        f"if {a} is None:",
        f"    return {b}",
        f"for {a} in self.{b}s:",
        f"self.{a}_count += {rng.randint(1, 9)}",
        f"{a}, {b} = {_ident(rng)}({b}, {a})",
    ][k]


def fx_pytest(rng, words_target, pos):
    """pytest -v log. One failure: detail block in the body, summary line at the end."""
    mod = rng.choice(MODS)
    tests = []
    while sum(len(t.split()) for t in tests) < words_target * 0.7:
        tests.append(f"tests/test_{rng.choice(MODS)}.py::test_{_ident(rng)} PASSED [{rng.randint(1, 99):>3}%]")
    bad = f"test_{_ident(rng)}_{rng.randint(10, 99)}"
    got, want = rng.randint(100, 999), rng.randint(100, 999)
    lineno = rng.randint(20, 400)
    tid = f"tests/test_{mod}.py::{bad}"
    k = int(pos * len(tests))
    tests.insert(k, f"{tid} FAILED [{rng.randint(1, 99):>3}%]")
    detail = [
        f"_________________________________ {bad} _________________________________",
        f"    def {bad}():",
        f"        r = {_ident(rng)}({rng.randint(1, 9)})",
        f">       assert r.total == {want}",
        f"E       assert {got} == {want}",
        f"E        +  where {got} = <Result>.total",
        f"tests/test_{mod}.py:{lineno}: AssertionError",
    ]
    # pytest prints all progress lines, then FAILURES, then the summary. The planted
    # detail block sits at fraction `pos` of the log by padding progress on both sides.
    head = tests[:k + 1]
    tail = tests[k + 1:]
    body = ["============================= test session starts =============================",
            f"collected {len(tests)} items", ""] + head + tail
    split = int(pos * len(body))
    lines = body[:split] + ["", "=================================== FAILURES ==================================="] + detail + [""] + body[split:]
    lines += ["=========================== short test summary info ============================",
              f"FAILED {tid} - AssertionError: assert {got} == {want}",
              f"======================== 1 failed, {len(tests) - 1} passed in {rng.randint(2, 90)}.{rng.randint(10, 99)}s ========================"]
    return "\n".join(lines), [
        {"q": "Which test failed?", "answer": tid, "where": "tail summary + body"},
        {"q": "On which line of the test file did the failing assertion fail?",
         "answer": f"test_{mod}.py:{lineno}", "where": "body only"},
    ], {"target": f"tests/test_{mod}.py"}


def fx_grep(rng, words_target, pos):
    """grep -rn for a symbol: many call sites, one definition."""
    sym = _ident(rng)
    hits = []
    while sum(len(h.split()) for h in hits) < words_target:
        f = f"src/{rng.choice(MODS)}/{rng.choice(NOUNS)}.py"
        hits.append(f"{f}:{rng.randint(1, 900)}:        {rng.choice(NOUNS)} = {sym}(self.{rng.choice(NOUNS)}, {rng.randint(0, 64)})")
    df = f"src/{rng.choice(MODS)}/{rng.choice(NOUNS)}_impl.py"
    dl = rng.randint(1, 900)
    hits.insert(int(pos * len(hits)), f"{df}:{dl}:def {sym}(buf, n):")
    return "\n".join(hits), [
        {"q": f"Where is {sym} defined (file:line)?", "answer": f"{df}:{dl}", "where": "body"},
    ], {"pattern": sym}


def fx_source(rng, words_target, pos):
    """cat of a Python module; the planted fact is a constant inside a function body."""
    lines, planted = [f'"""{rng.choice(MODS)} module."""', "import os", "import time", ""], False
    const = f"{rng.choice(NOUNS).upper()}_LIMIT"
    val = rng.randint(1000, 9999)
    while sum(len(x.split()) for x in lines) < words_target:
        lines.append(f"def {_ident(rng)}(self, {rng.choice(NOUNS)}):")
        for _ in range(rng.randint(3, 8)):
            lines.append(_py_line(rng))
        if not planted and sum(len(x.split()) for x in lines) >= pos * words_target:
            lines.append(f"    {const} = {val}")
            planted = True
        lines.append("")
    if not planted:
        lines.insert(len(lines) - 1, f"    {const} = {val}")
    return "\n".join(lines), [
        {"q": f"What value is {const} set to?", "answer": f"{const} = {val}", "where": "body"},
    ], {"path": f"src/{rng.choice(MODS)}/{rng.choice(NOUNS)}.py"}


def fx_diff(rng, words_target, pos):
    """git diff with several hunks; one hunk changes a size constant."""
    out = ["diff --git a/src/app.py b/src/app.py", "--- a/src/app.py", "+++ b/src/app.py"]
    hunks = []
    while sum(len(h.split()) for h in hunks) < words_target:
        ln = rng.randint(1, 900)
        h = [f"@@ -{ln},7 +{ln},7 @@ def {_ident(rng)}(self):"]
        h += [_py_line(rng) for _ in range(rng.randint(1, 4))]
        for _ in range(rng.randint(1, 4)):  # 1..4 changed pairs: 2..8 of 4..17 lines
            h += ["-" + _py_line(rng)[1:], "+" + _py_line(rng)[1:]]
        h += [_py_line(rng) for _ in range(rng.randint(1, 4))]
        hunks.append("\n".join(h))
    old, new = rng.randint(5, 60), rng.randint(61, 300)
    var = f"{rng.choice(NOUNS)}_size"  # neutral name: no salience keyword
    ln = rng.randint(1, 900)
    special = "\n".join([f"@@ -{ln},5 +{ln},5 @@ def connect(self):", _py_line(rng), _py_line(rng),
                         f"-        {var} = {old}", f"+        {var} = {new}", _py_line(rng)])
    hunks.insert(int(pos * len(hunks)), special)
    return "\n".join(out + hunks), [
        {"q": f"What is the new value of {var}?", "answer": f"{var} = {new}", "where": "body"},
    ], {"path": "src/app.py"}


PROSE = ("the service handles each request in order and records the result in the "
         "shared store so later steps can read it without another round trip to the "
         "backend which keeps latency low under steady load").split()


def fx_prose(rng, words_target, pos):
    """A fetched web page: prose, no structure. Negative control for keeplines."""
    sents = []
    while sum(len(s.split()) for s in sents) < words_target:
        sents.append(" ".join(rng.choice(PROSE) for _ in range(rng.randint(12, 24))).capitalize() + ".")
    code = f"{rng.choice(NOUNS)}-{rng.randint(100, 999)}"
    sents.insert(int(pos * len(sents)), f"The release code name is {code}.")
    # Paragraphs of ~4 sentences, as a fetched page would have.
    paras = [" ".join(sents[i:i + 4]) for i in range(0, len(sents), 4)]
    return "\n\n".join(paras), [
        {"q": "What is the release code name?", "answer": code, "where": "body"},
    ], {"url": "https://example.invalid/notes"}


FIXTURES = {"pytest": fx_pytest, "grep": fx_grep, "source": fx_source, "diff": fx_diff, "prose": fx_prose}


def fixture(kind, seed, words_target=(400, 1600), pos=None):
    """One tool output with planted facts. pos: fact position in [0,1] (seeded if None)."""
    rng = random.Random(f"{kind}-{seed}")
    w = rng.randint(*words_target) if isinstance(words_target, tuple) else words_target
    p = rng.random() if pos is None else pos
    text, facts, query = FIXTURES[kind](rng, w, p)
    return {"kind": kind, "seed": seed, "pos": p, "text": text, "facts": facts, "query": query}


def retained(compressed, fact):
    return fact["answer"] in compressed


def retention(methods=("trunc", "keeplines", "randdrop"), ratios=(0.3, 0.5, 0.7),
              kinds=tuple(FIXTURES), n=200, use_query=True):
    """Fraction of planted facts whose answer survives, per kind x method x ratio."""
    out = {}
    for kind in kinds:
        fxs = [fixture(kind, s) for s in range(n)]
        for m in methods:
            for r in ratios:
                hit = tot = 0
                over = 0
                by_fact = {}
                for fx in fxs:
                    c, st = compress(fx["text"], m, r, fx["query"] if use_query else None)
                    over += st["words_out"] > budget_words(fx["text"], r)
                    for fi, f in enumerate(fx["facts"]):
                        ok = retained(c, f)
                        hit += ok
                        tot += 1
                        by_fact.setdefault(fi, []).append(ok)
                out[f"{kind}/{m}/{r}"] = {
                    "retained": round(hit / tot, 4),
                    "per_fact": {fxs[0]["facts"][fi]["where"]: round(sum(v) / len(v), 4)
                                 for fi, v in by_fact.items()},
                    "over_budget": over,
                }
    return out


# ---------------------------------------------------------------- service
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, {"ok": True, "methods": [m for m in METHODS if m != "llmlingua2"]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/compress":
            self._send(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(n) or b"{}")
            text, st = compress(req.get("text", ""), req.get("method", "keeplines"),
                                float(req.get("ratio", 0.5)), req.get("query"))
        except (ValueError, TypeError, ImportError) as e:
            self._send(400, {"error": f"{type(e).__name__}: {e}"})
            return
        self._send(200, {"text": text, **st})

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compress")
    c.add_argument("--method", choices=METHODS, default="keeplines")
    c.add_argument("--ratio", type=float, default=0.5)
    c.add_argument("--query", default=None)
    r = sub.add_parser("retention")
    r.add_argument("--n", type=int, default=200)
    r.add_argument("--ratios", default="0.3,0.5,0.7")
    r.add_argument("--methods", default="trunc,keeplines,randdrop")
    r.add_argument("--no-query", action="store_true", help="ablation: hide the tool-call query")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8090)
    a = ap.parse_args(argv)
    if a.cmd == "compress":
        out, st = compress(sys.stdin.read(), a.method, a.ratio, a.query)
        sys.stdout.write(out)
        print(json.dumps(st), file=sys.stderr)
    elif a.cmd == "retention":
        print(json.dumps(retention(tuple(a.methods.split(",")),
                                   tuple(float(x) for x in a.ratios.split(",")),
                                   n=a.n, use_query=not a.no_query), indent=1))
    else:
        ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
