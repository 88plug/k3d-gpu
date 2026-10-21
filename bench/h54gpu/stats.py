"""Shared statistics for the H54b/H54c scorers: median and a seeded percentile bootstrap."""
import random
import statistics


def median(xs):
    return statistics.median(xs)


def ratio_ci(num, den, reps=10000, seed=1):
    """95% percentile bootstrap CI of median(num)/median(den), each arm resampled on its own
    (independent requests). Returns (point, lo, hi)."""
    rng = random.Random(seed)
    point = median(num) / median(den)
    rs = []
    for _ in range(reps):
        a = [rng.choice(num) for _ in num]
        b = [rng.choice(den) for _ in den]
        rs.append(median(a) / median(b))
    rs.sort()
    return point, rs[int(0.025 * reps)], rs[int(0.975 * reps) - 1]


def decodes(run):
    """N=1 per-request decode tok/s from an llm_load.py JSON (the concurrency-1 phase)."""
    for ph in run["phases"]:
        if ph["concurrency"] == 1:
            return [s["decode_tokens_per_s"] for s in ph["samples"]
                    if s.get("ok") and s.get("decode_tokens_per_s")]
    return []
