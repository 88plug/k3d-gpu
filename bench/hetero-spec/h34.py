#!/usr/bin/env python3
"""H34 verdict: CPU Fast Draft (B) vs target alone (A) under matched background load.

Applies the pre-registered H34 outcome table (EXPERIMENTS.md, "H34 pre-registration")
to the JSONL that bench/hetero-spec/h34.sh writes, one verdict per load level.

Input records (run.jsonl):
  {"kind": "p0", "ready": true}
  {"kind": "req", "level": "typical", "pair": 3, "arm": "B", "decode_tps": 16.1,
   "tokens_ok": true, "energy_j": 412.0, "text_head": "..."}
Load windows (load-<level>.jsonl, from bench/load/load.py sample --tag <arm>).

Per level: paired ratios B/A of decode tok/s and of package J/token; medians,
bootstrap 95% CIs (10k, seed 34), exact sign tests; load matched by
bench/load/load.py's pre-registered TOLERANCE.
  INCONCLUSIVE  server not Ready, < 8 complete pairs, or load unmatched (cause named)
  SUPPORTED     median tps ratio >= 1.20 and median J ratio <= 0.85, and the
                tps CI lower bound > 1.0 and the J CI upper bound < 1.0
  REFUTED       tps CI upper bound < 1.20, or J CI lower bound > 0.85
  INCONCLUSIVE  otherwise (effect size not resolved)
Stdlib only. Usage: h34.py run.jsonl [load-typical.jsonl load-heavy.jsonl ...]
"""

import json
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "load"))
import load  # noqa: E402

TOKENS = 256
MIN_PAIRS = 8
TPS_SUPPORT = 1.20
J_SUPPORT = 0.85
SEED = 34


def sign_test_p(wins, n):
    """Exact two-sided binomial sign test, p = 0.5."""
    if n == 0:
        return 1.0
    k = min(wins, n - wins)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def bootstrap_ci(xs, n=10000, seed=SEED):
    if not xs:
        return [None, None]
    rng = random.Random(seed)
    meds = sorted(statistics.median(rng.choices(xs, k=len(xs))) for _ in range(n))
    return [meds[int(0.025 * n)], meds[int(0.975 * n) - 1]]


def _r(x, d=4):
    return None if x is None else round(x, d)


def level_verdict(reqs, windows):
    """Verdict for one level. reqs: that level's req records; windows: its load windows."""
    pairs = {}
    dropped = 0
    for r in reqs:
        ok = r.get("decode_tps") and r.get("tokens_ok") and r.get("energy_j")
        if not ok:
            dropped += 1
            continue
        pairs.setdefault(r["pair"], {})[r["arm"]] = r
    full = [pairs[k] for k in sorted(pairs) if "A" in pairs[k] and "B" in pairs[k]]
    out = {"n_pairs": len(full), "dropped_requests": dropped}
    for arm in ("A", "B"):
        tps = sorted(p[arm]["decode_tps"] for p in full)
        jpt = sorted(p[arm]["energy_j"] / TOKENS for p in full)
        if tps:
            out["arm_" + arm] = {"tps_median": _r(statistics.median(tps), 3), "tps_min": _r(tps[0], 3),
                                 "tps_max": _r(tps[-1], 3), "j_per_token_median": _r(statistics.median(jpt), 3)}
    out["greedy_identical_pairs"] = sum(
        1 for p in full if p["A"].get("text_head") and p["A"].get("text_head") == p["B"].get("text_head"))
    m = load.matched(windows) if windows else {"matched": False,
                                                "verdict": "INCONCLUSIVE: load unmatched (no load windows)",
                                                "checks": []}
    out["load_matched"] = m["matched"]
    out["load_verdict"] = m["verdict"]
    out["load_per_arm"] = {c["metric"]: c["arms"] for c in m["checks"]}

    tr = [p["B"]["decode_tps"] / p["A"]["decode_tps"] for p in full]
    jr = [p["B"]["energy_j"] / p["A"]["energy_j"] for p in full]
    if tr:
        tw, jw = sum(x > 1 for x in tr), sum(x < 1 for x in jr)
        tp, jp = sign_test_p(tw, sum(x != 1 for x in tr)), sign_test_p(jw, sum(x != 1 for x in jr))
        out.update(tps_ratio_median=_r(statistics.median(tr)), tps_ratio_ci95=[_r(x) for x in bootstrap_ci(tr)],
                   j_ratio_median=_r(statistics.median(jr)), j_ratio_ci95=[_r(x) for x in bootstrap_ci(jr)],
                   b_faster_pairs=tw, b_cheaper_pairs=jw, sign_p_tps=_r(tp, 6), sign_p_j=_r(jp, 6),
                   confidence=_r(1 - max(tp, jp)))

    if len(full) < MIN_PAIRS:
        out.update(verdict="INCONCLUSIVE", outcome=f"gate: {len(full)} complete pairs < {MIN_PAIRS}", confidence=None)
        return out
    if not m["matched"]:
        out.update(verdict="INCONCLUSIVE", outcome=m["verdict"], confidence=None)
        return out
    tci, jci = out["tps_ratio_ci95"], out["j_ratio_ci95"]
    if (out["tps_ratio_median"] >= TPS_SUPPORT and out["j_ratio_median"] <= J_SUPPORT
            and tci[0] > 1.0 and jci[1] < 1.0):
        out.update(verdict="SUPPORTED", outcome="B >= +20% tok/s and <= -15% J/token, both directions resolved")
    elif tci[1] < TPS_SUPPORT or jci[0] > J_SUPPORT:
        out.update(verdict="REFUTED", outcome="predicted effect excluded by the CI (tok/s < +20% or J/token > -15%)")
    else:
        out.update(verdict="INCONCLUSIVE", outcome="effect size not resolved by the CIs", confidence=None)
    return out


def verdict(records, windows):
    p0 = next((r for r in records if r.get("kind") == "p0"), {})
    if not p0.get("ready", False):
        return {"verdict": "INCONCLUSIVE", "outcome": "BLOCKED: server not Ready", "levels": {}}
    levels = {}
    for lv in ("typical", "heavy", "none"):
        reqs = [r for r in records if r.get("kind") == "req" and r.get("level") == lv]
        if reqs:
            levels[lv] = level_verdict(reqs, [w for w in windows if w.get("level") == lv])
    head = levels.get("typical")
    return {"verdict": head["verdict"] if head else "INCONCLUSIVE",
            "outcome": head["outcome"] if head else "no typical-level run",
            "headline_level": "typical", "levels": levels}


def main(argv):
    def read(path):
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]
    records = read(argv[1])
    windows = [w for path in argv[2:] for w in read(path)]
    print(json.dumps(verdict(records, windows), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
