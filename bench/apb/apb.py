#!/usr/bin/env python3
"""apb.py: agents per box at a p95 agent-turn-latency SLO (Python stdlib only).

N closed-loop agents replay agent sessions back to back through
bench/agent_trace.py: each agent sends a turn, waits for the full streamed
reply, sleeps the trace's tool wait (think_s), sends the next turn, and starts
its next session when one ends. N is agent_trace's --max-concurrency; every
session starts at t=0 and waits for a free agent slot.

Subcommands
  trace     write a closed-loop trace: FAMILY with max(MIN, PER_AGENT*N) sessions,
            every start_s = 0
  run-step  replay one trace with N agents; merges --extra-body into every
            request (e.g. chat_template_kwargs); writes OUT.jsonl, OUT.meta.json
  verdict   score one ramp step (all reps of N in a run directory); exit 0 pass,
            3 fail, 4 not enough data
  report    score every step of a run directory, APB, energy; writes
            report.json and report.md
  predict   processor-sharing simulation of the ramp from single-stream and
            c=4 rates (a pre-registered prediction, not a measurement)

Definitions (pre-registered in PROTOCOL.md)
  turn latency     e2e_s of one LLM request: send to end of stream. A request
                   that errors or times out counts as +inf.
  steady window    per rep: from the start of the (N+1)-th session to start (drops
                   the synchronized first cohort's opening turns) to the first end
                   among sessions still running when the last session starts
                   (drops the ramp-down tail). A request counts when its send
                   time is inside the window.
  p95              nearest-rank percentile over the window requests of all reps
                   of the step, pooled.
  step passes      pooled n >= MIN_N, p95 <= SLO, error rate <= 1%, and tool-call
                   validity no more than 10 points below the N=1 step.
  APB              the largest N such that every ramp step <= N passed (0 if
                   N=1 failed).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # bench/: agent_trace, agent_trace_gen

import agent_trace as at  # noqa: E402
import agent_trace_gen as gen  # noqa: E402

INF = float("inf")
# Sessions at scale 1.0 per family (agent_trace_gen.py); closed-loop families only.
BASE_SESSIONS = {"support": 16, "coding": 4}
MIN_N_PER_REP = 20
MIN_N_POOLED = 60
MAX_ERROR_RATE = 0.01
MAX_TOOL_DROP = 0.10
EARLY_FAIL_FACTOR = 2.0
SECONDARY_SLOS = (5.0, 15.0, 20.0, 30.0)


# ---------------------------------------------------------------- trace
def build_trace(family, agents, seed, per_agent=5, min_sessions=10):
    if family not in BASE_SESSIONS:
        raise ValueError(f"family must be one of {sorted(BASE_SESSIONS)}")
    if agents < 1:
        raise ValueError("agents must be >= 1")
    n = max(min_sessions, per_agent * agents)
    trace = gen.FAMILIES[family](seed=seed, scale=n / BASE_SESSIONS[family])
    trace["sessions"] = trace["sessions"][:n]
    if len(trace["sessions"]) != n:
        raise RuntimeError(f"generator gave {len(trace['sessions'])} sessions, want {n}")
    for s in trace["sessions"]:
        s["start_s"] = 0.0
    trace["name"] = f"apb-{family}-n{agents}-s{seed}"
    trace["params"] = {**trace.get("params", {}), "apb_agents": agents, "apb_sessions": n}
    return trace


# ---------------------------------------------------------------- run
def run_step(trace, url, model, agents, extra_body=None, timeout=120.0,
             time_scale=1.0, api_key=None):
    """Replay trace with `agents` closed-loop agents. Returns (results, meta)."""
    extra = dict(extra_body or {})
    orig = at.stream_chat

    def stream_with_extra(u, payload, key=None, tmo=600.0):
        return orig(u, {**payload, **extra}, key, tmo)

    args = SimpleNamespace(
        url=url, model=model, api_key=api_key, cache_bust="none",
        time_scale=time_scale, max_concurrency=agents, temperature=0.0,
        tool_choice="auto", timeout=timeout, verbose=False,
    )
    at.stream_chat = stream_with_extra
    try:
        epoch_start = time.time()
        results, wall = at.run_trace(trace, args)
        epoch_end = time.time()
    finally:
        at.stream_chat = orig
    meta = {
        "trace": trace.get("name"), "family": trace.get("family"),
        "agents": agents, "sessions": len(trace["sessions"]), "url": url,
        "model": model, "extra_body": extra, "timeout_s": timeout,
        "time_scale": time_scale, "epoch_start": epoch_start,
        "epoch_end": epoch_end, "wall_s": wall,
    }
    return results, meta


# ---------------------------------------------------------------- scoring
def latency(rec):
    if rec.get("error") or rec.get("e2e_s") is None:
        return INF
    return rec["e2e_s"]


def pctl(xs, p):
    """Nearest-rank percentile (works with +inf). None for an empty list."""
    xs = sorted(xs)
    if not xs:
        return None
    k = max(1, math.ceil(p * len(xs)))
    return xs[k - 1]


def steady_window(results, agents):
    """(t_start, t_end) of the steady window in sent_at seconds, or None."""
    sessions = {}
    for r in results:
        end = r["sent_at_s"] + (r["e2e_s"] if r.get("e2e_s") is not None else 0.0)
        s = sessions.setdefault(r["session"], [INF, -INF])
        s[0] = min(s[0], r["sent_at_s"])
        s[1] = max(s[1], end)
    if len(sessions) <= agents:
        return None
    spans = sorted(sessions.values())
    t_start = spans[agents][0]
    last_start = spans[-1][0]
    t_end = min(e for st, e in spans if e > last_start)
    if t_end <= t_start:
        return None
    return t_start, t_end


def window_requests(results, agents):
    w = steady_window(results, agents)
    if w is None:
        return [], None
    return [r for r in results if w[0] <= r["sent_at_s"] < w[1]], w


def bootstrap_ci(xs, p=0.95, b=2000, seed=0, level=0.95):
    if not xs:
        return None, None
    rng = random.Random(seed)
    n = len(xs)
    ests = sorted(pctl([xs[rng.randrange(n)] for _ in range(n)], p) for _ in range(b))
    lo = ests[int((1 - level) / 2 * b)]
    hi = ests[min(b - 1, int((1 + level) / 2 * b))]
    return lo, hi


def _rate(num, den):
    return num / den if den else None


def score_step(reps, slo, base_tool_valid=None):
    """reps: list of (results, meta) for one N. Returns a step record."""
    agents = reps[0][1]["agents"]
    pooled, per_rep, out_tok, win_s = [], [], 0, 0.0
    for results, _meta in reps:
        rows, w = window_requests(results, agents)
        lats = [latency(r) for r in rows]
        per_rep.append({"n": len(rows), "p95_s": pctl(lats, 0.95),
                        "window_s": None if w is None else w[1] - w[0]})
        pooled.extend(rows)
        out_tok += sum(r.get("completion_tokens") or 0 for r in rows if not r.get("error"))
        win_s += 0.0 if w is None else w[1] - w[0]
    lats = [latency(r) for r in pooled]
    errors = sum(1 for r in pooled if r.get("error"))
    expect = [r for r in pooled if r.get("expect_tool") and not r.get("error")]
    tool_valid = _rate(sum(1 for r in expect if r.get("tool_valid")), len(expect))
    ttft = [r["ttft_s"] if r.get("ttft_s") is not None and not r.get("error") else INF
            for r in pooled]
    cached = [(r["cached_tokens"], r["prompt_tokens"]) for r in pooled
              if r.get("cached_tokens") is not None and r.get("prompt_tokens")]
    p95 = pctl(lats, 0.95)
    ci = bootstrap_ci(lats, seed=agents)
    reasons = []
    if len(pooled) < MIN_N_POOLED and not (p95 is not None and p95 > slo):
        verdict = "insufficient"
        reasons.append(f"pooled n {len(pooled)} < {MIN_N_POOLED}")
    else:
        if p95 is None or p95 > slo:
            reasons.append(f"p95 {p95} > SLO {slo}")
        if _rate(errors, len(pooled)) is not None and errors / len(pooled) > MAX_ERROR_RATE:
            reasons.append(f"error rate {errors}/{len(pooled)} > {MAX_ERROR_RATE}")
        if (base_tool_valid is not None and tool_valid is not None
                and tool_valid < base_tool_valid - MAX_TOOL_DROP):
            reasons.append(f"tool validity {tool_valid:.3f} < N=1 {base_tool_valid:.3f} - {MAX_TOOL_DROP}")
        verdict = "fail" if reasons else "pass"
    return {
        "agents": agents, "reps": len(reps), "verdict": verdict, "reasons": reasons,
        "n": len(pooled), "per_rep": per_rep,
        "p50_s": pctl(lats, 0.50), "p95_s": p95, "p99_s": pctl(lats, 0.99),
        "p95_ci95_s": list(ci), "ttft_p95_s": pctl(ttft, 0.95),
        "errors": errors, "error_rate": _rate(errors, len(pooled)),
        "tool_valid_any": tool_valid,
        "cached_fraction": _rate(sum(c for c, _ in cached), sum(p for _, p in cached)),
        "window_output_tok_per_s": _rate(out_tok, win_s),
    }


def apb(steps):
    """steps: step records sorted by agents. Returns (apb, interpolated)."""
    best, prev = 0, None
    for s in steps:
        if s["verdict"] != "pass":
            if prev is not None and s["p95_s"] not in (None, INF) and s["verdict"] == "fail" \
                    and s["p95_s"] > prev["p95_s"]:
                slo = s["_slo"]
                frac = (slo - prev["p95_s"]) / (s["p95_s"] - prev["p95_s"])
                return best, prev["agents"] + frac * (s["agents"] - prev["agents"])
            return best, None
        best, prev = s["agents"], s
    return best, None


def early_fail(results, meta, slo):
    """True when a single rep already shows p95 > EARLY_FAIL_FACTOR * SLO."""
    rows, _ = window_requests(results, meta["agents"])
    p = pctl([latency(r) for r in rows], 0.95)
    return p is not None and len(rows) >= MIN_N_PER_REP and p > EARLY_FAIL_FACTOR * slo


# ---------------------------------------------------------------- energy
def parse_rapl(path):
    """Lines 'epoch pkg_uj [psys_uj|-]' -> list of (t, pkg, psys or None)."""
    out = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                t, pkg = float(parts[0]), int(parts[1])
            except ValueError:
                continue
            psys = None
            if len(parts) > 2 and parts[2] != "-":
                try:
                    psys = int(parts[2])
                except ValueError:
                    psys = None
            out.append((t, pkg, psys))
    return out


def rapl_energy(samples, t0, t1, wrap_uj):
    """Joules per counter between the last sample <= t0 and the first >= t1.

    Returns {"pkg_j", "psys_j", "seconds"} or None when the samples do not
    bracket the interval. A smaller reading than the previous one is a wrap.
    """
    before = [s for s in samples if s[0] <= t0]
    after = [s for s in samples if s[0] >= t1]
    if not before or not after:
        return None
    i0, i1 = samples.index(before[-1]), samples.index(after[0])
    seg = samples[i0:i1 + 1]

    def total(col):
        if any(s[col] is None for s in seg):
            return None
        uj = 0
        for a, b in zip(seg, seg[1:]):
            d = b[col] - a[col]
            uj += d + wrap_uj if d < 0 else d
        return uj / 1e6

    return {"pkg_j": total(1), "psys_j": total(2), "seconds": seg[-1][0] - seg[0][0]}


def parse_nvidia(path):
    """Lines 'epoch, index, power_w' (nvidia-smi csv,noheader,nounits with an
    epoch prefix) -> {index: [(t, watts)]}. '[N/A]' readings are skipped."""
    out = {}
    with open(path) as f:
        for line in f:
            parts = [p.strip() for p in line.replace(",", " ").split()]
            if len(parts) < 3:
                continue
            try:
                t, idx, w = float(parts[0]), int(parts[1]), float(parts[2])
            except ValueError:
                continue
            out.setdefault(idx, []).append((t, w))
    return out


def trapezoid_j(series, t0, t1):
    """Integrate (t, watts) over [t0, t1], linear between samples, held flat
    beyond the first/last sample. None with no samples."""
    pts = sorted(series)
    if not pts:
        return None

    def at_t(t):
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for (ta, wa), (tb, wb) in zip(pts, pts[1:]):
            if ta <= t <= tb:
                return wa if tb == ta else wa + (wb - wa) * (t - ta) / (tb - ta)
        return pts[-1][1]

    knots = [t0] + [t for t, _ in pts if t0 < t < t1] + [t1]
    return sum((at_t(a) + at_t(b)) / 2 * (b - a) for a, b in zip(knots, knots[1:]))


# ---------------------------------------------------------------- run dir
def load_run(run_dir):
    """{agents: [(results, meta), ...]} from run_dir/steps/n<N>-r<rep>.*"""
    steps = {}
    sdir = os.path.join(run_dir, "steps")
    if not os.path.isdir(sdir):
        return steps
    for name in sorted(os.listdir(sdir)):
        if not name.endswith(".meta.json"):
            continue
        prefix = os.path.join(sdir, name[: -len(".meta.json")])
        with open(prefix + ".meta.json") as f:
            meta = json.load(f)
        with open(prefix + ".jsonl") as f:
            results = [json.loads(line) for line in f if line.strip()]
        steps.setdefault(meta["agents"], []).append((results, meta))
    return steps


def energy_for(run_dir, reps, idle):
    rapl_path = os.path.join(run_dir, "rapl.log")
    nv_path = os.path.join(run_dir, "nvidia.log")
    wrap = int(os.environ.get("APB_RAPL_WRAP_UJ", "262143328850"))
    rapl = parse_rapl(rapl_path) if os.path.exists(rapl_path) else []
    nv = parse_nvidia(nv_path) if os.path.exists(nv_path) else {}
    tot = {"pkg_j": 0.0, "psys_j": 0.0, "gpu_j": {}, "seconds": 0.0, "turns": 0, "out_tokens": 0}
    have_pkg = bool(rapl)
    for results, meta in reps:
        t0, t1 = meta["epoch_start"], meta["epoch_end"]
        tot["seconds"] += t1 - t0
        tot["turns"] += len(results)
        tot["out_tokens"] += sum(r.get("completion_tokens") or 0 for r in results)
        e = rapl_energy(rapl, t0, t1, wrap) if rapl else None
        if e is None or e["pkg_j"] is None:
            have_pkg = False
        else:
            tot["pkg_j"] += e["pkg_j"]
            tot["psys_j"] = None if (tot["psys_j"] is None or e["psys_j"] is None) else tot["psys_j"] + e["psys_j"]
        for idx, series in nv.items():
            j = trapezoid_j(series, t0, t1)
            tot["gpu_j"][str(idx)] = tot["gpu_j"].get(str(idx), 0.0) + (j or 0.0)
    if not have_pkg:
        tot["pkg_j"] = tot["psys_j"] = None
    out = {"seconds": tot["seconds"], "turns": tot["turns"], "out_tokens": tot["out_tokens"],
           "pkg_j": tot["pkg_j"], "psys_j": tot["psys_j"], "gpu_j": tot["gpu_j"]}
    gpu_sum = sum(tot["gpu_j"].values()) if tot["gpu_j"] else 0.0
    gross = None if tot["pkg_j"] is None else tot["pkg_j"] + gpu_sum
    out["gross_j"] = gross
    if gross is not None and idle and idle.get("watts") is not None:
        out["net_j"] = gross - idle["watts"] * tot["seconds"]
    else:
        out["net_j"] = None
    for k in ("gross_j", "net_j"):
        v = out[k]
        out[k + "_per_turn"] = None if v is None or not tot["turns"] else v / tot["turns"]
        out[k + "_per_out_token"] = None if v is None or not tot["out_tokens"] else v / tot["out_tokens"]
    return out


def idle_power(run_dir):
    path = os.path.join(run_dir, "idle.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        w = json.load(f)
    rapl_path = os.path.join(run_dir, "rapl.log")
    wrap = int(os.environ.get("APB_RAPL_WRAP_UJ", "262143328850"))
    if not os.path.exists(rapl_path):
        return None
    e = rapl_energy(parse_rapl(rapl_path), w["epoch_start"], w["epoch_end"], wrap)
    if e is None or e["pkg_j"] is None or e["seconds"] <= 0:
        return None
    gpu = 0.0
    nv_path = os.path.join(run_dir, "nvidia.log")
    if os.path.exists(nv_path):
        for series in parse_nvidia(nv_path).values():
            gpu += trapezoid_j(series, w["epoch_start"], w["epoch_end"]) or 0.0
    span = w["epoch_end"] - w["epoch_start"]
    return {"watts": e["pkg_j"] / e["seconds"] + (gpu / span if span > 0 else 0.0),
            "pkg_watts": e["pkg_j"] / e["seconds"], "seconds": e["seconds"]}


def score_run(run_dir, slo):
    data = load_run(run_dir)
    idle = idle_power(run_dir)
    steps, base_tool = [], None
    for n in sorted(data):
        s = score_step(data[n], slo, base_tool)
        if n == 1 and base_tool is None:
            base_tool = s["tool_valid_any"]
        s["_slo"] = slo
        s["energy"] = energy_for(run_dir, data[n], idle)
        steps.append(s)
    best, interp = apb(steps)
    for s in steps:
        s.pop("_slo", None)
    drift = None
    dpath = os.path.join(run_dir, "drift")
    if os.path.isdir(os.path.join(dpath, "steps")):
        d = load_run(dpath).get(1)
        first = next((s for s in steps if s["agents"] == 1), None)
        if d and first:
            ds = score_step(d, slo)
            drift = {"p95_start_s": first["p95_s"], "p95_end_s": ds["p95_s"],
                     "ratio": None if not first["p95_s"] or ds["p95_s"] in (None, INF)
                     else ds["p95_s"] / first["p95_s"]}
    curve = [(s["agents"], s["p95_s"] if s["verdict"] != "insufficient" else None) for s in steps]
    secondary = {str(x): apb_from_p95(curve, x) for x in SECONDARY_SLOS}
    return {"slo_s": slo, "apb": best, "apb_interpolated": interp,
            "apb_at_secondary_slo_p95_only": secondary, "idle": idle,
            "drift_check": drift, "steps": steps}


def finite(x):
    """Copy of x with +inf written as the string "inf" (strict JSON)."""
    if isinstance(x, float) and math.isinf(x):
        return "inf"
    if isinstance(x, dict):
        return {k: finite(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [finite(v) for v in x]
    return x


def _fmt(v, nd=2):
    if v is None:
        return "-"
    if v == INF:
        return "inf"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def report_md(rep, arm):
    lines = [f"# APB: {arm}", "",
             f"SLO: p95 turn latency <= {rep['slo_s']} s. **APB = {rep['apb']}**"
             f" (interpolated {_fmt(rep['apb_interpolated'], 1)}).", ""]
    lines.append("APB at other SLOs (p95 rule only, same data): " + ", ".join(
        f"{k} s: {v}" for k, v in rep["apb_at_secondary_slo_p95_only"].items()) + ".")
    if rep["idle"]:
        lines.append(f"Idle power before the ramp: {_fmt(rep['idle']['watts'], 1)} W.")
    if rep["drift_check"]:
        lines.append(f"Drift check (N=1 p95 end/start): {_fmt(rep['drift_check']['ratio'], 3)}.")
    lines += ["", "| N | verdict | n | p50 s | p95 s | p95 95% CI | p99 s | TTFT p95 s | err | tool valid | cached | out tok/s | J/turn gross | J/turn net |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in rep["steps"]:
        e = s["energy"]
        ci = s["p95_ci95_s"]
        lines.append(
            f"| {s['agents']} | {s['verdict']} | {s['n']} | {_fmt(s['p50_s'])} | {_fmt(s['p95_s'])} | "
            f"{_fmt(ci[0])}-{_fmt(ci[1])} | {_fmt(s['p99_s'])} | {_fmt(s['ttft_p95_s'])} | "
            f"{_fmt(s['error_rate'], 3)} | {_fmt(s['tool_valid_any'], 3)} | {_fmt(s['cached_fraction'], 3)} | "
            f"{_fmt(s['window_output_tok_per_s'], 1)} | {_fmt(e['gross_j_per_turn'], 1)} | {_fmt(e['net_j_per_turn'], 1)} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- predict
def g_curve(a1, a4, kind):
    """Aggregate speedup g(k) = A(k)/A(1) from measured c=1 and c=4 rates.
    'hyper': g = k/(1+alpha(k-1)) fitted through c=4 and extrapolated.
    'flat':  same through k=4, constant after (no gain past the last measurement)."""
    s4 = a4 / a1
    alpha = (4 / s4 - 1) / 3

    def g(k):
        if k <= 0:
            return 0.0
        kk = k if kind == "hyper" else min(k, 4)
        return kk / (1 + alpha * (kk - 1))

    return g


def turn_demands(trace, a1, prefill_tps, ttft0, out_tool, out_final, tok_per_word=1.3):
    """Per-session list of service demands in c=1 seconds. Uncached prompt per
    turn = words appended this turn + the previous recorded reply (prefix cache
    holds the rest); the very first turn of the trace also pays the system prompt."""
    sessions, first = [], True
    for s in trace["sessions"]:
        d, prev_rec = [], 0
        for t in s["turns"]:
            w = sum(len((m.get("content") or "").split()) for m in t.get("append", [])) + prev_rec
            if first:
                w += len(s["system"].split())
                first = False
            o = out_tool if t.get("expect_tool") else out_final
            d.append(ttft0 + w * tok_per_word / prefill_tps + o / a1)
            rec = t.get("recorded") or {}
            prev_rec = len((rec.get("content") or "").split()) + sum(
                len(tc["function"]["arguments"].split()) for tc in rec.get("tool_calls") or [])
        sessions.append((d, [t.get("think_s", 0.0) for t in s["turns"]]))
    return sessions


def simulate(sessions, agents, g):
    """Closed-loop processor sharing: with k requests in service each advances
    at g(k)/k c=1-seconds per second. Returns the list of turn latencies of the
    steady window (same window rule as the live analysis)."""
    queue = list(range(len(sessions)))
    now, active, sleeping, recs = 0.0, {}, [], []
    # agent state: [session, turn, sent_at]
    agents_state = {}

    def start_session(a):
        if not queue:
            agents_state.pop(a, None)
            return
        si = queue.pop(0)
        agents_state[a] = [si, 0, None]
        send(a)

    def send(a):
        si, ti, _ = agents_state[a]
        agents_state[a][2] = now
        active[a] = sessions[si][0][ti]

    for a in range(agents):
        start_session(a)
    while active or sleeping:
        k = len(active)
        rate = g(k) / k if k else 0.0
        t_done = now + min(active.values()) / rate if k else INF
        t_wake = min(sleeping)[0] if sleeping else INF
        t_next = min(t_done, t_wake)
        for a in active:
            active[a] -= (t_next - now) * rate
        now = t_next
        if t_done <= t_wake:
            for a in [a for a, w in active.items() if w <= 1e-9]:
                del active[a]
                si, ti, sent = agents_state[a]
                recs.append({"session": f"s{si}", "turn": ti, "sent_at_s": sent,
                             "e2e_s": now - sent, "error": None})
                if ti + 1 < len(sessions[si][0]):
                    agents_state[a][1] = ti + 1
                    sleeping.append((now + sessions[si][1][ti + 1], a))
                else:
                    start_session(a)
        else:
            sleeping.sort()
            _, a = sleeping.pop(0)
            send(a)
    rows, _ = window_requests(recs, agents)
    return [r["e2e_s"] for r in rows]


def apb_from_p95(p95_by_n, slo):
    """Largest N with every step <= N at p95 <= slo (0 if the first fails)."""
    best = 0
    for n, p in p95_by_n:
        if p is None or p > slo:
            break
        best = n
    return best


def predict(arms, steps, slo, family="support", seeds=(1, 2, 3), out_tool=40, out_final=96):
    out = {}
    for name, (a1, a4, prefill, ttft0) in arms.items():
        rows = {}
        for kind in ("hyper", "flat"):
            g = g_curve(a1, a4, kind)
            res = []
            for n in steps:
                lats = []
                for sd in seeds:
                    tr = build_trace(family, n, 1000 * n + sd)
                    lats += simulate(turn_demands(tr, a1, prefill, ttft0, out_tool, out_final), n, g)
                res.append((n, pctl(lats, 0.95)))
            rows[kind] = {"p95_by_n": res, "apb": apb_from_p95(res, slo)}
        out[name] = rows
    return out


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("trace")
    t.add_argument("--family", default="support", choices=sorted(BASE_SESSIONS))
    t.add_argument("--agents", type=int, required=True)
    t.add_argument("--seed", type=int, required=True)
    t.add_argument("--per-agent", type=int, default=5)
    t.add_argument("--min-sessions", type=int, default=10)
    t.add_argument("-o", "--out", required=True)

    r = sub.add_parser("run-step")
    r.add_argument("trace")
    r.add_argument("--url", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--agents", type=int, required=True)
    r.add_argument("--extra-body", default="{}")
    r.add_argument("--timeout", type=float, default=120.0)
    r.add_argument("--time-scale", type=float, default=1.0)
    r.add_argument("--slo", type=float, default=10.0)
    r.add_argument("--out", required=True, help="prefix: writes PREFIX.jsonl, PREFIX.meta.json")

    v = sub.add_parser("verdict")
    v.add_argument("run_dir")
    v.add_argument("--agents", type=int, required=True)
    v.add_argument("--slo", type=float, default=10.0)

    p = sub.add_parser("report")
    p.add_argument("run_dir")
    p.add_argument("--slo", type=float, default=10.0)
    p.add_argument("--arm", default="")

    q = sub.add_parser("predict")
    q.add_argument("--slo", type=float, default=10.0)
    q.add_argument("--steps", default="1,2,4,8,12,16,24,32")
    q.add_argument("--out-tool", type=int, default=40)
    q.add_argument("--out-final", type=int, default=96)

    a = ap.parse_args(argv)
    if a.cmd == "trace":
        tr = build_trace(a.family, a.agents, a.seed, a.per_agent, a.min_sessions)
        with open(a.out, "w") as f:
            json.dump(tr, f)
        print(json.dumps(gen.describe(tr)))
        return 0
    if a.cmd == "run-step":
        with open(a.trace) as f:
            tr = json.load(f)
        results, meta = run_step(tr, a.url, a.model, a.agents, json.loads(a.extra_body),
                                 a.timeout, a.time_scale, os.environ.get("OPENAI_API_KEY"))
        meta["early_fail"] = early_fail(results, meta, a.slo)
        with open(a.out + ".jsonl", "w") as f:
            for rec in results:
                f.write(json.dumps(rec) + "\n")
        with open(a.out + ".meta.json", "w") as f:
            json.dump(meta, f, indent=2)
        rows, _ = window_requests(results, a.agents)
        print(json.dumps({"agents": a.agents, "requests": len(results),
                          "errors": sum(1 for x in results if x["error"]),
                          "window_n": len(rows),
                          "window_p95_s": pctl([latency(x) for x in rows], 0.95),
                          "early_fail": meta["early_fail"]}))
        return 0
    if a.cmd == "verdict":
        reps = load_run(a.run_dir).get(a.agents)
        if not reps:
            print(json.dumps({"agents": a.agents, "verdict": "missing"}))
            return 4
        s = score_step(reps, a.slo)
        if any(m.get("early_fail") for _, m in reps) and s["verdict"] != "pass":
            s["verdict"] = "fail"
        print(json.dumps({k: s[k] for k in ("agents", "reps", "verdict", "n", "p95_s", "error_rate", "reasons")}))
        return {"pass": 0, "fail": 3}.get(s["verdict"], 4)
    if a.cmd == "report":
        rep = score_run(a.run_dir, a.slo)
        with open(os.path.join(a.run_dir, "report.json"), "w") as f:
            json.dump(finite(rep), f, indent=2)
        md = report_md(rep, a.arm or os.path.basename(os.path.normpath(a.run_dir)))
        with open(os.path.join(a.run_dir, "report.md"), "w") as f:
            f.write(md)
        print(md, end="")
        return 0
    if a.cmd == "predict":
        arms = {  # name: (decode c=1, aggregate c=4, prefill tok/s, ttft0 s), H2 measurements
            "ovms-cpu-pcores": (15.7, 56.0, 172.0, 0.25),
            "ovms-igpu": (9.9, 30.0, 66.0, 0.25),
            "eat-vllm-cpu-bf16": (4.6, 4.6 * 56.0 / 15.7, 15.0, 0.25),
        }
        steps = [int(x) for x in a.steps.split(",")]
        res = predict(arms, steps, a.slo, out_tool=a.out_tool, out_final=a.out_final)
        for name, kinds in res.items():
            for kind, r in kinds.items():
                apbs = {x: apb_from_p95(r["p95_by_n"], x) for x in (a.slo,) + SECONDARY_SLOS}
                p95s = " ".join(f"{n}:{p:.1f}" for n, p in r["p95_by_n"])
                print(f"{name:18} {kind:5} APB@SLO {apbs}  p95 by N {p95s}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
