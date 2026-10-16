#!/usr/bin/env python3
"""Closed-loop agents-per-box sim for "skip generation" mechanisms (stdlib only).

Mechanisms (flags):
  M1 --forced-frac/--forced-runs  grammar jump-forward: schema-forced tool-call tokens
                                  appended in one step instead of decoded one per step
  M2 --k [--adaptive]             lookup drafting (prompt lookup / suffix tree); --adaptive
                                  sizes k to the step's spare compute (SmartSpec-style)
  M3 --dedup                      append-only dedup of repeated tool output (prefill skipped)
  M4 --macro                      model emits macro id + slots; runtime expands canned prose
  M5 --skip                       deterministic turns executed by the runtime, no LLM call
Usage: python3 bench/skip_generation_sim.py --c-pf 5.8 --dedup 0.3 --slo 10,20,30
       python3 bench/skip_generation_sim.py --c-pf 1.7 --at 1,4,8,16 Token-level decode with per-step
attached compute for grammar-forced runs (M1) and speculative verify (M2, optional
batch-adaptive draft length). Step model calibrated on H2 Qwen3-4B int4 CPU:
  additive: T0 + C_DEC*B + c_pf*(prefill + attached)          T0=61.8, C_DEC=1.9
  roofline: max(61.4, c_pf*(prefill + attached + B)) + 2.3*B
c_pf = per-token compute: 5.8 ms (172 tok/s prefill figure) or 1.7 ms (TTFT 0.25s/107 tok).
"""
import random, math, argparse, statistics

def lognorm(rng, mean, cv):
    s2 = math.log(1 + cv * cv); mu = math.log(mean) - s2 / 2
    return rng.lognormvariate(mu, math.sqrt(s2))

def exp_accept(q, k):
    return (1 - q ** (k + 1)) / (1 - q) if q < 1 else k + 1

def new_turn(rng, a):
    tool = rng.random() < a.tool_frac
    new = a.new_tokens * (1 - a.dedup) + a.prose_tokens * a.macro * (1 - a.tool_frac)
    if tool:
        out = a.tool_tokens; forced = round(out * a.forced_frac)
        return dict(pf=new, free=out - forced, forced=forced, runs=a.forced_runs if forced else 0, q=a.q_tool)
    out = round(a.prose_tokens * (1 - a.macro))
    return dict(pf=new, free=out, forced=0, runs=0, q=a.q_prose)

def sim(N, a, seed):
    rng = random.Random(seed); now = 0.0; lat = []
    ag = [dict(ready=rng.uniform(0, 5000), st="wait") for _ in range(N)]
    while now < a.horizon * 1000:
        for g in ag:
            if g["st"] == "wait" and g["ready"] <= now and rng.random() < a.skip:
                g["carry"] = g.get("carry", 0) + a.new_tokens * (1 - a.dedup)  # skipped turn's tool result still enters KV next turn
                g["ready"] = now + (a.skip_ms + lognorm(rng, a.tool_wait, a.tool_cv) * 1000); continue
            if g["st"] == "wait" and g["ready"] <= now:
                g.update(new_turn(rng, a)); g["pf"] += g.pop("carry", 0); g["st"] = "pf"; g["t0"] = g["ready"]
        pfq = sorted([g for g in ag if g["st"] == "pf"], key=lambda g: g["t0"])
        dec = [g for g in ag if g["st"] == "dec"]
        if not pfq and not dec:
            now = min(g["ready"] for g in ag); continue
        budget = a.chunk; pft = 0
        for g in pfq:
            t = min(budget, g["pf"]); g["pf"] -= t; pft += t; budget -= t
            if g["pf"] <= 0: g["st"] = "next"
            if budget <= 0: break
        B = len(dec); k = a.k
        if a.adaptive and B:
            slack = max(0, int(61.4 / a.c_pf) - B - pft)
            k = min(a.k, slack // B)
        attached = 0
        for g in dec:
            run = 0
            if g["forced"] and g["runs"] and (rng.random() < g["runs"] / max(1, g["free"] + g["runs"])):
                run = math.ceil(g["forced"] / g["runs"]); g["forced"] -= run; g["runs"] -= 1
            g["_k"] = k if g["free"] > 1 else 0
            attached += run + g["_k"]
            g["_run"] = run
        if a.mode == "additive":
            step = 61.8 + 1.9 * B + a.c_pf * (pft + attached)
        else:
            step = max(61.4, a.c_pf * (pft + attached + B)) + 2.3 * B
        now += step
        for g in dec:
            if not g["_run"]:
                acc = exp_accept(g["q"], g["_k"]) if g["_k"] else 1.0
                g["free"] -= acc
            if g["free"] <= 0 and g["forced"] <= 0:
                if now > a.warm * 1000: lat.append((now - g["t0"]) / 1000)
                g["st"] = "wait"; g["ready"] = now + lognorm(rng, a.tool_wait, a.tool_cv) * 1000
            elif g["free"] <= 0 and g["forced"] > 0:  # flush remaining forced run
                g["free"] = 0.0; g["runs"] = max(1, g["runs"])
        for g in ag:
            if g["st"] == "next": g["st"] = "dec"
    lat.sort()
    return lat[int(0.95 * (len(lat) - 1))] if lat else float("inf")

def parser():
    ap = argparse.ArgumentParser()
    A = ap.add_argument
    A("--c-pf", dest="c_pf", type=float, default=5.8); A("--mode", default="roofline")
    A("--new-tokens", type=float, default=300); A("--tool-frac", type=float, default=0.6)
    A("--tool-tokens", type=float, default=60); A("--prose-tokens", type=float, default=96)
    A("--forced-frac", type=float, default=0); A("--forced-runs", type=int, default=5)
    A("--k", type=int, default=0); A("--adaptive", action="store_true")
    A("--q-tool", type=float, default=0.47); A("--q-prose", type=float, default=0.17)
    A("--dedup", type=float, default=0); A("--macro", type=float, default=0)
    A("--tool-wait", type=float, default=1.0); A("--tool-cv", type=float, default=2.0)
    A("--chunk", type=int, default=256); A("--horizon", type=float, default=900)
    A("--warm", type=float, default=60); A("--nmax", type=int, default=48); A("--seeds", type=int, default=5)
    A("--slo", default="10,20,30"); A("--skip", type=float, default=0); A("--skip-ms", type=float, default=5); A("--at", default="")
    return ap

def p95(N, a): return statistics.median(sim(N, a, s) for s in range(1, a.seeds + 1))

if __name__ == "__main__":
    a = parser().parse_args()
    if a.at:
        print(" ".join(f"N{n}:{p95(int(n), a):.1f}" for n in a.at.split(","))); raise SystemExit
    out = []
    for slo in map(float, a.slo.split(",")):
        best = 0
        for n in range(1, a.nmax + 1):
            if p95(n, a) <= slo: best = n
            elif n > best + 2: break
        out.append(f"slo{int(slo)}:{best}")
    print(" ".join(out))
