"""Statistics for the agent benchmark (Python standard library only).

Every interval here is fixed before a run: Wilson 95% for a success rate,
nearest-rank percentiles with a seeded bootstrap CI, and a paired bootstrap for
an arm-vs-arm difference on the task ids both arms completed.
"""
import math
import random

Z95 = 1.959963984540054
BOOT = 2000
SEED = 40


def wilson(k, n, z=Z95):
    """Wilson score interval for k successes out of n. (None, None) when n == 0."""
    if n <= 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def nearest_rank(xs, p):
    """Nearest-rank percentile (p in 0..100). Errors enter as float('inf')."""
    if not xs:
        return None
    s = sorted(xs)
    k = max(1, math.ceil(p / 100 * len(s)))
    return s[k - 1]


def median(xs):
    return nearest_rank(xs, 50)


def bootstrap_ci(xs, stat, boot=BOOT, seed=SEED, level=0.95):
    """Percentile bootstrap CI of stat(sample). (None, None) for an empty sample."""
    if not xs:
        return None, None
    rng = random.Random(seed)
    n = len(xs)
    vals = sorted(stat([xs[rng.randrange(n)] for _ in range(n)]) for _ in range(boot))
    lo = vals[int((1 - level) / 2 * boot)]
    hi = vals[min(boot - 1, int((1 + level) / 2 * boot))]
    return lo, hi


def paired_diff(a, b, boot=BOOT, seed=SEED):
    """Paired difference of two {task_id: 0/1 or number} maps on their shared ids.

    Returns {"n", "diff", "ci95": [lo, hi], "lb95_one_sided", "ub95_one_sided"}:
    diff = mean(a - b) over the shared ids, CIs from a paired bootstrap over ids."""
    ids = sorted(set(a) & set(b))
    out = {"n": len(ids), "diff": None, "ci95": [None, None],
           "lb95_one_sided": None, "ub95_one_sided": None}
    if not ids:
        return out
    d = [float(a[i]) - float(b[i]) for i in ids]
    out["diff"] = sum(d) / len(d)
    rng = random.Random(seed)
    n = len(d)
    means = sorted(sum(d[rng.randrange(n)] for _ in range(n)) / n for _ in range(boot))
    out["ci95"] = [means[int(0.025 * boot)], means[min(boot - 1, int(0.975 * boot))]]
    out["lb95_one_sided"] = means[int(0.05 * boot)]
    out["ub95_one_sided"] = means[min(boot - 1, int(0.95 * boot))]
    return out


def ratio_ci(xs, ys, stat=median, boot=BOOT, seed=SEED):
    """Point and bootstrap CI of stat(xs) / stat(ys), resampling both independently."""
    if not xs or not ys:
        return None, (None, None)
    sy = stat(ys)
    point = stat(xs) / sy if sy else None
    rng = random.Random(seed)
    vals = []
    for _ in range(boot):
        a = stat([xs[rng.randrange(len(xs))] for _ in xs])
        b = stat([ys[rng.randrange(len(ys))] for _ in ys])
        if b:
            vals.append(a / b)
    if not vals:
        return point, (None, None)
    vals.sort()
    return point, (vals[int(0.025 * len(vals))], vals[min(len(vals) - 1, int(0.975 * len(vals)))])
