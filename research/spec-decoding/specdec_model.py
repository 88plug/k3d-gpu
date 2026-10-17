#!/usr/bin/env python3
"""Speculative-decoding speedup model for the k3d-gpu agent (stdlib only).

Predicts decode speedup over target-only decoding from per-engine token
latencies and the draft acceptance rate, for the execution order OpenVINO
GenAI 2026.4.1 actually uses, plus a pipelined bound it does not implement.

Symbols (all latencies in milliseconds, all measured, never assumed):
  t_t    target decode latency for one token, target-only (the baseline)
  t_d    draft decode latency for one token on the draft engine
  beta   extra target cost per extra verified token: t_v(k) = t_t*(1+beta*k)
  o      fixed per-iteration overhead (scheduler, KV bookkeeping, device sync)
  alpha  per-token acceptance probability (i.i.d. assumption, Leviathan 2023)
  gamma  num_assistant_tokens (draft tokens proposed per iteration)

Sequential (GenAI 2026.4.1 fast_draft_strategy.cpp step(): draft multistep,
then main step, under one mutex):
  T_iter = gamma*t_d + t_t*(1 + beta*gamma) + o
Pipelined bound (draft of iteration i+1 overlaps verify of i; NOT upstream):
  T_iter = max(gamma*t_d, t_t*(1 + beta*gamma)) + o
Tokens per iteration (Leviathan et al. 2023, eq. 1):
  E = (1 - alpha**(gamma+1)) / (1 - alpha)
Speedup = E * t_t / T_iter.

GenAI reports get_draft_acceptance_rate() = accepted / offered candidates,
not alpha. rate_from_alpha / alpha_from_rate convert between the two.
"""

import argparse
import json
import sys


def tokens_per_iter(alpha, gamma):
    """Expected tokens emitted per target iteration (accepted + 1 bonus/correction)."""
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("alpha must be in [0, 1]")
    if gamma < 0:
        raise ValueError("gamma must be >= 0")
    if alpha == 1.0:
        return float(gamma + 1)
    return (1.0 - alpha ** (gamma + 1)) / (1.0 - alpha)


def iter_time(t_t, t_d, gamma, beta=0.0, o=0.0, pipelined=False):
    """Wall time of one draft+verify iteration, ms."""
    if t_t <= 0 or t_d < 0 or beta < 0 or o < 0:
        raise ValueError("latencies must be positive (t_t) or non-negative")
    verify = t_t * (1.0 + beta * gamma)
    draft = gamma * t_d
    return (max(draft, verify) if pipelined else draft + verify) + o


def speedup(alpha, gamma, t_t, t_d, beta=0.0, o=0.0, pipelined=False):
    """Decode-throughput ratio speculative / target-only. 1.0 = break even."""
    return tokens_per_iter(alpha, gamma) * t_t / iter_time(t_t, t_d, gamma, beta, o, pipelined)


def rate_from_alpha(alpha, gamma):
    """GenAI acceptance rate (accepted / offered) implied by per-token alpha."""
    if gamma <= 0:
        raise ValueError("gamma must be >= 1")
    # E counts the accepted draft tokens plus the target's own token.
    return (tokens_per_iter(alpha, gamma) - 1.0) / gamma


def alpha_from_rate(rate, gamma, tol=1e-9):
    """Invert rate_from_alpha by bisection (it is monotonic in alpha)."""
    if not 0.0 <= rate <= 1.0:
        raise ValueError("rate must be in [0, 1]")
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if rate_from_alpha(mid, gamma) < rate:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2.0


def best_gamma(alpha, t_t, t_d, beta=0.0, o=0.0, pipelined=False, max_gamma=16):
    """(gamma, speedup) maximising speedup for gamma in 1..max_gamma."""
    return max(((g, speedup(alpha, g, t_t, t_d, beta, o, pipelined)) for g in range(1, max_gamma + 1)),
               key=lambda x: x[1])


def break_even_alpha(gamma, t_t, t_d, beta=0.0, o=0.0, pipelined=False, tol=1e-9):
    """Smallest alpha with speedup >= 1, or None if even alpha=1 loses."""
    if speedup(1.0, gamma, t_t, t_d, beta, o, pipelined) < 1.0:
        return None
    if speedup(0.0, gamma, t_t, t_d, beta, o, pipelined) >= 1.0:
        return 0.0
    lo, hi = 0.0, 1.0
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        if speedup(mid, gamma, t_t, t_d, beta, o, pipelined) < 1.0:
            lo = mid
        else:
            hi = mid
    return hi


def prompt_lookup_speedup(hit, alpha_hit, gamma, t_t, beta=0.0, o=0.0):
    """Prompt-lookup decoding: an n-gram hit proposes gamma tokens at ~zero
    draft cost; a miss proposes none and is a plain decode step (cost t_t).
    hit = fraction of iterations with a match; alpha_hit = acceptance on a hit."""
    if not 0.0 <= hit <= 1.0:
        raise ValueError("hit must be in [0, 1]")
    tokens = hit * tokens_per_iter(alpha_hit, gamma) + (1.0 - hit) * 1.0
    time = hit * (t_t * (1.0 + beta * gamma) + o) + (1.0 - hit) * t_t
    return tokens * t_t / time


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--t-target", type=float, required=True, help="target-only ms/token on its best engine (tuned baseline)")
    p.add_argument("--t-draft", type=float, required=True, help="draft ms/token on the draft engine")
    p.add_argument("--beta", type=float, default=0.0, help="verify cost per extra token, fraction of t_target")
    p.add_argument("--overhead", type=float, default=0.0, help="per-iteration overhead, ms")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--alpha", type=float, help="per-token acceptance probability")
    g.add_argument("--rate", type=float, help="GenAI get_draft_acceptance_rate() value")
    p.add_argument("--gamma", type=int, default=5, help="num_assistant_tokens (OVMS default 5)")
    a = p.parse_args(argv)
    alpha = a.alpha if a.alpha is not None else alpha_from_rate(a.rate, a.gamma)
    kw = dict(t_t=a.t_target, t_d=a.t_draft, beta=a.beta, o=a.overhead)
    out = {
        "alpha": round(alpha, 4),
        "c": round(a.t_draft / a.t_target, 4),
        "gamma": a.gamma,
        "tokens_per_iter": round(tokens_per_iter(alpha, a.gamma), 4),
        "speedup_sequential": round(speedup(alpha, a.gamma, **kw), 4),
        "speedup_pipelined_bound": round(speedup(alpha, a.gamma, pipelined=True, **kw), 4),
        "best_gamma_sequential": best_gamma(alpha, **kw)[0],
        "break_even_alpha_sequential": break_even_alpha(a.gamma, **kw),
    }
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
