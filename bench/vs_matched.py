#!/usr/bin/env python3
"""vs_matched.py: statistics for a `bench/vs.py run` under matched background load.

  bench/vs_matched.py RESULT.json LOAD.jsonl [--seed 1] [--resamples 10000]

Prints JSON: per arm the median decode tok/s of every timed request with a 95%
bootstrap CI, the k3d-gpu/competitor ratio of those medians with its 95% CI
(requests resampled within each arm), P_boot (the share of resampled ratios on
the verdict's side of the margin), the one-sided sign test over rounds, the
derived confidence min(P_boot, 1 - p_sign), and the load per arm with the
`bench/load/load.py matched` decision (EXPERIMENTS.md, H30-H32 amendment A1).
Python 3 standard library only.
"""
import argparse
import json
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "load"))
import load  # noqa: E402


def rates(res, side):
    return [s["decode_tokens_per_s"] for r in res["rounds"] for s in r[side]["samples"]
            if s.get("ok") and s.get("decode_tokens_per_s")]


def ci(xs, rng, n):
    meds = sorted(statistics.median(rng.choices(xs, k=len(xs))) for _ in range(n))
    return [meds[int(0.025 * n)], meds[int(0.975 * n) - 1]]


def side_of(ratio, margin):
    if ratio >= 1 + margin:
        return "SUPPORTED"
    if 1 / ratio >= 1 + margin:
        return "REFUTED"
    return "INCONCLUSIVE"


def sign_p(wins, n):
    """One-sided sign test: P(at least `wins` of n rounds by chance, p = 0.5)."""
    from math import comb
    return sum(comb(n, k) for k in range(wins, n + 1)) / 2 ** n


def analyse(res, windows, seed=1, resamples=10000):
    rng = random.Random(seed)
    margin = res["params"]["margin"]
    a, b = rates(res, "k3d"), rates(res, "vs")
    out = {"id": res["id"], "stack": res["stack"], "verdict": res["verdict"], "n": {"k3d-gpu": len(a), res["stack"]: len(b)}}
    if not a or not b:
        out["confidence"] = None
        return out
    ratio = statistics.median(a) / statistics.median(b)
    boot = sorted(statistics.median(rng.choices(a, k=len(a))) / statistics.median(rng.choices(b, k=len(b)))
                  for _ in range(resamples))
    rounds = [r["k3d"]["decode_tokens_per_s"] / r["vs"]["decode_tokens_per_s"] for r in res["rounds"]
              if r["k3d"].get("decode_tokens_per_s") and r["vs"].get("decode_tokens_per_s")]
    v = res["verdict"]
    p_boot = sum(side_of(x, margin) == v for x in boot) / resamples
    wins = sum(side_of(x, margin) == v for x in rounds)
    p_sign = sign_p(wins, len(rounds)) if rounds else 1.0
    out.update({
        "median_tok_s": {"k3d-gpu": statistics.median(a), res["stack"]: statistics.median(b)},
        "ci95_tok_s": {"k3d-gpu": ci(a, rng, resamples), res["stack"]: ci(b, rng, resamples)},
        "ratio": ratio, "ratio_ci95": [boot[int(0.025 * resamples)], boot[int(0.975 * resamples) - 1]],
        "round_ratios": rounds, "p_boot": p_boot, "round_wins": wins, "p_sign": p_sign,
        "confidence": min(p_boot, 1 - p_sign),
    })
    if windows:
        m = load.matched(windows)
        out["load_windows"] = {t: sum(w["tag"] == t for w in windows) for t in sorted({w["tag"] for w in windows})}
        out["load_checks"] = m["checks"]
        out["load_matched"] = m["matched"]
        out["load_verdict"] = m["verdict"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("result")
    ap.add_argument("load", nargs="?")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--resamples", type=int, default=10000)
    o = ap.parse_args(argv)
    with open(o.result) as f:
        res = json.load(f)
    windows = []
    if o.load:
        with open(o.load) as f:
            windows = [json.loads(line) for line in f if line.strip()]
    print(json.dumps(analyse(res, windows, o.seed, o.resamples), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
