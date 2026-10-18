#!/usr/bin/env python3
"""vs.py: budgeted head-to-head of the k3d-gpu arm against one competitor stack.

  bench/vs.py run --arm k3d-gpu=http://127.0.0.1:8000/v3,OpenVINO/Qwen3-4B-int4-ov \\
      --arm ollama=http://127.0.0.1:11434/v1,qwen3:4b --id H10 --out h10.json \\
      --ledger h10.md
  bench/vs.py docs h10.json h11.json --out docs/benchmarks.md

`run` drives both OpenAI endpoints with one client (bench/llm_load.py), one
prompt set, the same max_tokens, temperature 0 and ignore_eos:
  - warmup: --warmup requests per arm, discarded (first-use kernel compiles, JIT)
  - rounds: --rounds rounds, interleaved; round r runs the arms in order
    A,B when r is even and B,A when r is odd, so drift hits both arms alike
  - one request in flight; each arm segment sends every prompt once
  - energy: the RAPL package counter (--powercap, zone --zone) is read at the
    start and end of every arm segment; J/token = segment joules / segment
    output tokens. Read directly when readable, else through `sudo -A cat`
    (SUDO_ASKPASS set) or `sudo -n cat`;
    with neither, energy is recorded as null.
  - budget: when --budget-s runs out, no new segment starts and the verdict is
    INCONCLUSIVE (budget).
  - background load (optional): with VS_LOAD_WINDOW_S and VS_LOAD_OUT set in the
    environment, one bench/load/load.py window of that many seconds is sampled
    before every timed segment, with no request in flight, tagged with the arm
    the segment is about to run, and appended to VS_LOAD_OUT as a JSON line
    (`load.py matched VS_LOAD_OUT` then compares the arms). The windows count
    against the budget.

A --remote-arm is a third-party OpenAI-compatible endpoint (k3d-gpu bench --vs
remote): its bearer key comes from OPENAI_API_KEY, it samples at temperature
REMOTE_TEMPERATURE without ignore_eos (a shared fleet may serve neither greedy
nor ignore_eos), and the run is held to REMOTE_BUDGET (one round, at most one
warmup, max_tokens <= 64), so it costs the endpoint a handful of requests.

Verdict for "the k3d-gpu arm decodes faster than <stack>": the per-round median
decode tok/s ratio (k3d-gpu / competitor) must be at least 1 + --margin in every
round for SUPPORTED, at most 1 - --margin in every round for REFUTED, and
anything else (mixed rounds, inside the margin, failed requests, budget) is
INCONCLUSIVE. J/token is reported next to it and does not change the verdict.

`run --ledger FILE` writes the EXPERIMENTS.md hypothesis row and its run section
(Reproduce header, sign-off with the raw-results sha256). `docs` renders
docs/benchmarks.md from result files. Python 3 standard library only.
"""

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm_load  # noqa: E402

SCHEMA = "k3d-gpu.bench-vs/v1"
REMOTE_TEMPERATURE = 0.6
REMOTE_BUDGET = {"rounds": 1, "warmup": 1, "max_tokens": 64}
DEFAULT_PROMPTS = (
    "Explain in three short paragraphs how a CPU cache hierarchy reduces memory latency.",
    "Write a Python function that returns the n-th Fibonacci number iteratively, with a docstring.",
    "List five checks an on-call engineer runs when a web service returns HTTP 502.",
    "Summarise what a Kubernetes Deployment does, for someone who knows Docker.",
)


# ---- energy ------------------------------------------------------------------

class Rapl:
    """Package RAPL counter; read() returns microjoules or None."""

    def __init__(self, powercap, zone):
        self.path = os.path.join(powercap, zone, "energy_uj")
        try:
            with open(os.path.join(powercap, zone, "max_energy_range_uj")) as f:
                self.range = int(f.read().strip())
        except (OSError, ValueError):
            self.range = None

    def read(self):
        if self.range is None:
            return None
        try:
            with open(self.path) as f:
                return int(f.read().strip())
        except PermissionError:
            pass
        except (OSError, ValueError):
            return None
        try:
            sudo = ["sudo", "-A"] if os.environ.get("SUDO_ASKPASS") else ["sudo", "-n"]
            out = subprocess.run(sudo + ["cat", self.path], capture_output=True, text=True, timeout=10)
            return int(out.stdout.strip()) if out.returncode == 0 else None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def delta_j(self, a, b):
        if a is None or b is None:
            return None
        d = b - a
        if d < 0:
            d += self.range
        return d / 1e6


# ---- run ---------------------------------------------------------------------

def parse_arm(spec):
    name, sep, rest = spec.partition("=")
    url, sep2, model = rest.partition(",")
    if not (sep and sep2 and name and url and model):
        raise SystemExit(f"vs: --arm must be name=base_url,model, got {spec!r}")
    return {"name": name, "base_url": url, "model": model}


def request(arm, prompt, opts, deadline=None):
    # The per-request timeout never reaches past the budget's deadline.
    timeout = opts.timeout if deadline is None else max(1.0, min(opts.timeout, deadline - time.monotonic()))
    remote = arm.get("name") in (getattr(opts, "remote_arm", None) or [])
    args = SimpleNamespace(base_url=arm["base_url"], model=arm["model"], prompt=prompt,
                           max_tokens=opts.max_tokens, temperature=REMOTE_TEMPERATURE if remote else 0.0,
                           ignore_eos=not remote, api_key=os.environ.get("OPENAI_API_KEY") if remote else None,
                           timeout=timeout)
    s = llm_load.one_request(args, 0)
    # A server that sends no usage: fall back to content chunks (one token each
    # on llama.cpp, Ollama and OVMS streaming) and say so.
    if s["ok"] and s["output_tokens"] is None and s["content_chunks"] > 1:
        n = s["content_chunks"]
        s["output_tokens"], s["tokens_source"] = n, "chunks"
        decode = s["latency_s"] - s["ttft_s"]
        if decode > 0:
            s["decode_tokens_per_s"] = (n - 1) / decode
    else:
        s.setdefault("tokens_source", "usage")
    return s


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def load_window(seconds, tag, out):
    """Sample one background-load window (bench/load/load.py) and append it to out."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "load"))
    try:
        import load  # noqa: E402
    except ImportError:
        raise SystemExit("vs: VS_LOAD_WINDOW_S needs bench/load/load.py next to vs.py (a repo checkout)")
    w = load.window(seconds, load.load_cpus(), tag=tag, level=os.environ.get("VS_LOAD_LEVEL"))
    with open(out, "a") as f:
        f.write(json.dumps(w, sort_keys=True) + "\n")
    return w


def segment(arm, prompts, opts, rapl, deadline=None):
    t_start = time.time()
    e0 = rapl.read()
    t0 = time.perf_counter()
    samples = [request(arm, p, opts, deadline) for p in prompts]
    wall = time.perf_counter() - t0
    e1 = rapl.read()
    ok = [s for s in samples if s["ok"]]
    toks = sum(s["output_tokens"] or 0 for s in ok)
    joules = rapl.delta_j(e0, e1)
    return {
        "arm": arm["name"], "t_start": t_start, "wall_s": wall, "joules": joules, "output_tokens": toks,
        "j_per_token": (joules / toks) if (joules is not None and toks) else None,
        "failed": len(samples) - len(ok),
        "decode_tokens_per_s": median(s["decode_tokens_per_s"] for s in ok),
        "ttft_s": median(s["ttft_s"] for s in ok),
        "median_output_tokens": median(s["output_tokens"] for s in ok),
        "tokens_sources": sorted({s.get("tokens_source", "usage") for s in ok}),
        "samples": samples,
    }


def verdict(rounds, margin, budget_hit, nrounds, max_tokens=None):
    """Ratios are k3d-gpu / competitor decode tok/s, per round. SUPPORTED needs
    k3d/vs >= 1+margin and REFUTED needs vs/k3d >= 1+margin in every round, so
    the margin means the same thing both ways. The `why` text carries no percent
    sign (docs/benchmarks.md renders it, and claims_check reads every percent)."""
    if budget_hit:
        return "INCONCLUSIVE", "budget ran out before every round finished"
    if len(rounds) < nrounds:
        return "INCONCLUSIVE", "not every round ran"
    ratios, sources = [], set()
    for r in rounds:
        a, b = r["k3d"], r["vs"]
        if a["failed"] or b["failed"] or not a["decode_tokens_per_s"] or not b["decode_tokens_per_s"]:
            return "INCONCLUSIVE", f"round {r['round']} had failed requests or no token counts"
        if max_tokens:
            for side, seg in (("k3d-gpu", a), (b.get("arm", "competitor"), b)):
                if (seg.get("median_output_tokens") or max_tokens) < max_tokens:
                    return "INCONCLUSIVE", (f"round {r['round']}: {side} stopped early (median "
                                            f"{seg['median_output_tokens']:.0f} of {max_tokens} tokens), so the arms did unequal work")
        for seg in (a, b):
            sources.update(seg.get("tokens_sources") or ["usage"])
        ratios.append(a["decode_tokens_per_s"] / b["decode_tokens_per_s"])
    if len(sources) > 1:
        return "INCONCLUSIVE", "the arms counted tokens differently (usage and stream chunks), so the rates use different rulers"
    shown = ", ".join(f"{x:.2f}" for x in ratios)
    if all(x >= 1 + margin for x in ratios):
        return "SUPPORTED", f"k3d-gpu ahead by the margin in every round (k3d-gpu/competitor ratios {shown})"
    if all(1 / x >= 1 + margin for x in ratios):
        return "REFUTED", f"competitor ahead by the margin in every round (k3d-gpu/competitor ratios {shown})"
    return "INCONCLUSIVE", f"rounds disagree or sit inside the pre-registered margin (k3d-gpu/competitor ratios {shown})"


def summary(rounds):
    out = {}
    for side in ("k3d", "vs"):
        out[side] = {m: median(r[side][m] for r in rounds) for m in ("decode_tokens_per_s", "ttft_s", "j_per_token")}
    return out


def cmd_run(opts):
    arms = [parse_arm(a) for a in opts.arm]
    if len(arms) != 2:
        raise SystemExit("vs: give exactly two --arm (the k3d-gpu arm first)")
    k3d, vs = arms
    prompts = list(DEFAULT_PROMPTS)
    if opts.prompts:
        with open(opts.prompts) as f:
            prompts = [line.strip() for line in f if line.strip()]
    rapl = Rapl(opts.powercap, opts.zone)
    start = time.monotonic()
    deadline = start + opts.budget_s
    budget_hit = False
    warm = {}
    for arm in (k3d, vs):
        warm[arm["name"]] = []
        for i in range(opts.warmup):
            if time.monotonic() >= deadline:
                budget_hit = True
                break
            warm[arm["name"]].append(request(arm, prompts[i % len(prompts)], opts, deadline)["ok"])
    load_s = float(os.environ.get("VS_LOAD_WINDOW_S") or 0)
    load_out = os.environ.get("VS_LOAD_OUT")
    rounds = []
    for r in range(0 if budget_hit else opts.rounds):
        order = [("k3d", k3d), ("vs", vs)] if r % 2 == 0 else [("vs", vs), ("k3d", k3d)]
        rec = {"round": r, "order": [a["name"] for _, a in order]}
        for side, arm in order:
            if time.monotonic() >= deadline:
                budget_hit = True
                break
            if load_s > 0 and load_out:
                load_window(load_s, arm["name"], load_out)
            rec[side] = segment(arm, prompts, opts, rapl, deadline)
        if budget_hit:
            break
        rounds.append(rec)
    v, why = verdict(rounds, opts.margin, budget_hit, opts.rounds, opts.max_tokens)
    result = {
        "schema": SCHEMA, "id": opts.id, "stack": vs["name"], "arms": arms,
        "params": {"rounds": opts.rounds, "warmup": opts.warmup, "max_tokens": opts.max_tokens,
                   "temperature": 0.0, "ignore_eos": True, "concurrency": 1, "margin": opts.margin,
                   "remote_arms": opts.remote_arm or [],
                   "remote_sampling": ({"temperature": REMOTE_TEMPERATURE, "ignore_eos": False}
                                       if opts.remote_arm else None),
                   "budget_s": opts.budget_s, "prompts": prompts,
                   "energy_zone": f"{opts.powercap}/{opts.zone}"},
        "context": json.loads(opts.context) if opts.context else {},
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - (time.monotonic() - start))),
        "elapsed_s": time.monotonic() - start,
        "warmup_ok": warm, "rounds": rounds, "summary": summary(rounds) if rounds else None,
        "verdict": v, "why": why,
    }
    raw = json.dumps(result, indent=2, sort_keys=True) + "\n"
    with open(opts.out, "w") as f:
        f.write(raw)
    if opts.ledger:
        with open(opts.ledger, "w") as f:
            f.write(ledger_md(result, hashlib.sha256(raw.encode()).hexdigest(), opts.signed_off_by))
    print(f"vs: {opts.id} k3d-gpu vs {vs['name']}: {v} ({why})")
    return 0


# ---- ledger and docs ---------------------------------------------------------

def fmt(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def ledger_md(res, sha, signer):
    """The run section for EXPERIMENTS.md plus the verdict for the existing,
    pre-registered row. The row's wording is never regenerated."""
    hid, stack, c, p = res["id"], res["stack"], res.get("context", {}), res["params"]
    s = res["summary"] or {"k3d": {}, "vs": {}}
    host = c.get("host_cpu_short") or c.get("host_cpu") or "host"
    lines = [
        f"<!-- set the Verdict column of the {hid} row in ## Hypotheses to: {res['verdict']} -->",
        f"### {hid} run ({host}, {res['started'][:10]})", "", "```text", "# provenance",
        f"date:            {res['started']} (elapsed {res['elapsed_s']:.0f} s of a {p['budget_s']:.0f} s budget)",
        f"hypothesis:      {hid}",
        f"host cpu:        {c.get('host_cpu', 'unknown')}",
        f"host kernel:     {c.get('host_kernel', 'unknown')}",
        f"k3d-gpu commit:  {c.get('commit', 'unknown')}",
        f"k3d-gpu arm:     {res['arms'][0]['base_url']} model {res['arms'][0]['model']} "
        f"(backend {c.get('k3d_backend', 'unknown')}, device {c.get('k3d_device', 'unknown')}, via {c.get('k3d_transport', 'unknown')})",
        f"competitor:      {stack} {c.get('image', '')}",
        f"competitor arm:  {res['arms'][1]['base_url']} model {res['arms'][1]['model']} ({c.get('quant_tier', '')}, "
        f"device {c.get('device', 'default')}, devices passed {c.get('vs_devices', 'unknown')}, via {c.get('vs_transport', 'unknown')})",
        f"weights:         {c.get('model_revision', 'unknown')}; served {c.get('artifact', 'unknown')}",
        f"energy source:   {p['energy_zone']}",
        f"runs per arm:    {p['warmup']} warmup + {p['rounds']} rounds x {len(p['prompts'])} prompts, max_tokens {p['max_tokens']}",
        f"sign-off:        {signer or 'unsigned'}; raw results sha256 {sha}",
        "```", "",
        f"| Metric (median over rounds) | k3d-gpu | {stack} |", "|---|---|---|",
        f"| decode tok/s | {fmt(s['k3d'].get('decode_tokens_per_s'))} | {fmt(s['vs'].get('decode_tokens_per_s'))} |",
        f"| time to first token (s) | {fmt(s['k3d'].get('ttft_s'), 3)} | {fmt(s['vs'].get('ttft_s'), 3)} |",
        f"| package J/token | {fmt(s['k3d'].get('j_per_token'))} | {fmt(s['vs'].get('j_per_token'))} |",
        "", f"Verdict: **{res['verdict']}**: {res['why']}.", "",
        "```bash", "# Reproduce (scratch kubeconfig, named cluster; sudo -v first for RAPL).",
        "export KUBECONFIG=$(mktemp)",
        "K3D_GPU_CLUSTER=k3dgpu-g-proof k3d-gpu up && k3d-gpu agent up",
        f"k3d-gpu bench --vs {stack} --id {hid} --budget {p['budget_s']:.0f} --out bench-{hid}",
        "```", "",
    ]
    return "\n".join(lines)


def pending_rows(ledger_path):
    """{id: stack} for every `k3d-gpu bench --vs` row in EXPERIMENTS.md."""
    rows = {}
    try:
        with open(ledger_path) as f:
            for line in f:
                m = re.match(r"\| (H\d+) \|.*`k3d-gpu bench --vs (\w+)", line)
                if m:
                    rows[m.group(1)] = m.group(2)
    except OSError:
        pass
    return rows


def hnum(hid):
    return int(hid.lstrip("H")) if hid.lstrip("H").isdigit() else 0


KEEP_MARKER = "<!-- bench/vs.py docs keeps everything below this line -->"


def cmd_docs(opts):
    results = {}
    for path in opts.results:
        with open(path) as f:
            r = json.load(f)
        if r.get("schema") != SCHEMA:
            raise SystemExit(f"vs: {path} is not a {SCHEMA} result")
        results[r["id"]] = r
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "competitors.lock")) as f:
            tiers = {k: v["quant_tier"] for k, v in json.load(f)["stacks"].items()}
    except (OSError, ValueError, KeyError):
        tiers = {}
    # Ledger rows with no result file stay on the page as PENDING.
    pending = {h: st for h, st in pending_rows(opts.ledger).items() if h not in results}
    out = [
        "# Benchmarks: k3d-gpu against other stacks", "",
        "Head-to-head runs made with `k3d-gpu bench --vs <stack>`. Each row is one ledger",
        "hypothesis in [`EXPERIMENTS.md`](../EXPERIMENTS.md); its run section there holds the",
        "Reproduce header, the raw-results sha256 and the exact command. A row backs a claim",
        "only when its verdict is SUPPORTED.", "",
        "Protocol: one client (`bench/llm_load.py`), one prompt set, temperature 0,",
        "`ignore_eos` sent to every arm, warmup requests discarded, interleaved rounds",
        "(A,B then B,A), one request in flight, energy from the RAPL package counter.",
        "Competitors run their pinned image and pinned weights (`bench/competitors.lock`;",
        "the served file's sha256 is checked before measuring) with vendor defaults, same",
        "model family and 4-bit tier.", "",
        "Known asymmetries, recorded in every run section: Ollama's OpenAI layer has no",
        "`ignore_eos`, so a round where either arm stops early is INCONCLUSIVE; the k3d-gpu",
        "arm is reached through `kubectl port-forward` (an API-server stream) while the",
        "competitor is reached through docker-proxy, so tunnel latency and buffering land",
        "on the k3d-gpu arm only.", "",
        "| Ledger | Competitor | Tier | Decode tok/s (k3d-gpu / competitor) | J/token (k3d-gpu / competitor) | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    for hid in sorted(set(results) | set(pending), key=hnum):
        if hid in results:
            r = results[hid]
            s = r["summary"] or {"k3d": {}, "vs": {}}
            out.append(
                f"| [ledger:{hid}] | {r['stack']} | {r.get('context', {}).get('quant_tier') or tiers.get(r['stack'], '4-bit')} | "
                f"{fmt(s['k3d'].get('decode_tokens_per_s'))} / {fmt(s['vs'].get('decode_tokens_per_s'))} | "
                f"{fmt(s['k3d'].get('j_per_token'))} / {fmt(s['vs'].get('j_per_token'))} | {r['verdict']} |")
        else:
            st = pending[hid]
            out.append(f"| [ledger:{hid}] | {st} | {tiers.get(st, '4-bit')} | not run | not run | PENDING |")
    out.append("")
    if results:
        out += ["Why each verdict:", ""]
        out += [f"- [ledger:{h}] ({results[h]['stack']}): {results[h]['why']}." for h in sorted(results, key=hnum)]
    else:
        out += ["No head-to-head has run yet, so this page makes no speed or energy claim. The goal",
                "of each row is a measured answer, whichever way it falls."]
    out += ["", "Run one and regenerate this page (rows without a result file stay PENDING):", "",
            "```bash", "k3d-gpu up && k3d-gpu agent up",
            "k3d-gpu bench --vs ollama --id H30 --budget 900 --out bench-H30",
            "bench/vs.py docs --out docs/benchmarks.md bench-H*/result.json", "```"]
    # Hand-written sections after KEEP_MARKER in the existing page survive a regenerate.
    try:
        with open(opts.out) as f:
            prev = f.read()
    except OSError:
        prev = ""
    kept = prev[prev.index(KEEP_MARKER):] if KEEP_MARKER in prev else ""
    with open(opts.out, "w") as f:
        f.write("\n".join(out) + "\n" + ("\n" + kept if kept else ""))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--arm", action="append", default=[], help="name=base_url,model (k3d-gpu arm first)")
    r.add_argument("--id", required=True, help="ledger id, e.g. H10")
    r.add_argument("--out", required=True)
    r.add_argument("--ledger")
    r.add_argument("--prompts", help="file, one prompt per line")
    r.add_argument("--rounds", type=int, default=3)
    r.add_argument("--warmup", type=int, default=3)
    r.add_argument("--max-tokens", type=int, default=128)
    r.add_argument("--margin", type=float, default=0.05)
    r.add_argument("--budget-s", type=float, default=900)
    r.add_argument("--timeout", type=float, default=300)
    r.add_argument("--powercap", default=os.environ.get("ENERGY_POWERCAP", "/sys/class/powercap"))
    r.add_argument("--zone", default="intel-rapl:0")
    r.add_argument("--context", help="JSON object recorded in the result (host, image, commit)")
    r.add_argument("--signed-off-by", default="")
    r.add_argument("--remote-arm", action="append", default=[],
                   help="arm NAME is a third-party endpoint: key from OPENAI_API_KEY, sampled, budgeted")
    d = sub.add_parser("docs")
    d.add_argument("results", nargs="*")
    d.add_argument("--ledger", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "EXPERIMENTS.md"),
                   help="EXPERIMENTS.md; its bench rows with no result file render as PENDING")
    d.add_argument("--out", required=True)
    opts = ap.parse_args(argv)
    if opts.cmd == "run":
        if opts.rounds < 1 or opts.warmup < 0 or opts.max_tokens < 2:
            raise SystemExit("vs: --rounds >= 1, --warmup >= 0, --max-tokens >= 2")
        names = [parse_arm(a)["name"] for a in opts.arm]
        for n in opts.remote_arm:
            if n not in names:
                raise SystemExit(f"vs: --remote-arm {n} names no --arm")
        if opts.remote_arm and any(getattr(opts, k) > v for k, v in REMOTE_BUDGET.items()):
            raise SystemExit("vs: a --remote-arm run is budgeted: " +
                             ", ".join(f"--{k.replace('_', '-')} <= {v}" for k, v in REMOTE_BUDGET.items()))
        return cmd_run(opts)
    return cmd_docs(opts)


if __name__ == "__main__":
    sys.exit(main())
