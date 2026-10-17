#!/usr/bin/env python3
"""H22 hetero-spec verdict: applies the pre-registered outcome table to probe JSONL.

Input: one JSON object per line from bench/hetero-spec/probe.sh, e.g.
  {"kind": "req", "round": 1, "arm": "C", "decode_tps": 12.8, "flagged": false}
  {"kind": "p0", "ready": true, "gpu_busy_A": 0, "gpu_busy_C": 51234}
P0 rests only on the i915 render-busy delta of the OVMS pid: ~0 in A, > 0 in C.
A server that never got Ready is BLOCKED (mechanism untested): INCONCLUSIVE.
Requests whose usage.completion_tokens != 256 are dropped (count unknown).
Arms: A (target alone), B (P-core draft), C (iGPU draft), DP, DG (draft alone).
Output: one JSON verdict on stdout. Stdlib only. Usage: analyze.py run.jsonl
"""

import json
import math
import random
import statistics
import sys

CALIB_TPS = 15.7      # H6: Qwen3-4B int4 on 16 P-CPUs
CALIB_TOL = 0.15
SUPPORT_R = 1.05
REFUTE_R = 0.95
SUPPORT_CA = 1.10
GPU_BUSY_A_MAX_NS = 1_000_000   # "A ~ 0": under 1 ms of render-engine time per request


def sign_test_p(wins, n):
    """Exact two-sided binomial sign test, p = 0.5."""
    if n == 0:
        return 1.0
    k = min(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def bootstrap_ci(xs, n=10000, seed=22):
    if not xs:
        return [None, None]
    rng = random.Random(seed)
    meds = sorted(statistics.median(rng.choices(xs, k=len(xs))) for _ in range(n))
    return [meds[int(0.025 * n)], meds[int(0.975 * n) - 1]]


def paired(rounds, num, den):
    return [r[num] / r[den] for r in rounds if num in r and den in r and r[den] > 0]


def verdict(records):
    p0 = next((r for r in records if r.get("kind") == "p0"), {})
    reqs = [r for r in records if r.get("kind") == "req" and r.get("decode_tps")
            and r.get("tokens_ok", True)]
    dropped = sum(1 for r in records if r.get("kind") == "req") - len(reqs)
    by_round = {}
    flagged = set()
    for r in reqs:
        by_round.setdefault(r["round"], {})[r["arm"]] = float(r["decode_tps"])
        if r.get("flagged"):
            flagged.add(r["round"])
    rounds = [by_round[k] for k in sorted(by_round)]
    out = {"n_rounds": len(rounds), "flagged_rounds": sorted(flagged), "dropped_requests": dropped}
    for arm in ("A", "B", "C", "DP", "DG"):
        xs = sorted(r[arm] for r in rounds if arm in r)
        if xs:
            out["tps_" + arm] = {"median": statistics.median(xs), "min": xs[0], "max": xs[-1]}

    if not p0.get("ready", False):
        out.update(verdict="INCONCLUSIVE", outcome="BLOCKED: server not Ready (infra); mechanism untested",
                   confidence=None)
        return out
    if p0.get("gpu_busy_A", 0) >= GPU_BUSY_A_MAX_NS:
        out.update(verdict="INCONCLUSIVE", outcome="P0 uncontrolled: GPU busy during A request",
                   confidence=None)
        return out
    if p0.get("gpu_busy_C", 0) <= 0:
        out.update(verdict="REFUTED", outcome="P0 fail: no GPU busy in C (draft not on the iGPU)",
                   confidence=None)
        return out
    a = [r["A"] for r in rounds if "A" in r]
    cb, ca = paired(rounds, "C", "B"), paired(rounds, "C", "A")
    out["median_A_tps"] = statistics.median(a) if a else None
    if not cb or not ca:
        out.update(verdict="INCONCLUSIVE", outcome="no complete C/B or C/A pair", confidence=None)
        return out
    R, RA = statistics.median(cb), statistics.median(ca)
    wins = sum(1 for x in cb if x > 1.0)
    losses = sum(1 for x in cb if x < 1.0)
    p = sign_test_p(wins, wins + losses)
    out.update(R=R, CI95=bootstrap_ci(cb), median_CA=RA, c_wins=wins, c_losses=losses,
               sign_p=p, confidence=round(1 - p, 4))
    for arm in ("DP", "DG"):
        xs = [r[arm] for r in rounds if arm in r]
        out["t_d_ms_" + arm] = 1000 / statistics.median(xs) if xs else None

    calib_ok = out["median_A_tps"] is not None and abs(out["median_A_tps"] / CALIB_TPS - 1) <= CALIB_TOL
    n = len(cb)
    need = math.ceil(7 * n / 8)
    if not calib_ok or len(flagged) > 3 * len(rounds) / 8:
        out.update(verdict="INCONCLUSIVE", outcome="setup too noisy (calibration or flagged rounds)")
    elif R >= SUPPORT_R and wins >= need and RA >= SUPPORT_CA:
        out.update(verdict="SUPPORTED", outcome="iGPU draft beats P-core draft and target alone")
    elif (R <= REFUTE_R and losses >= need) or RA < 1.0:
        out.update(verdict="REFUTED", outcome="iGPU draft loses to P-core draft or to target alone")
    else:
        out.update(verdict="INCONCLUSIVE", outcome="no gain: R inside (0.95, 1.05) or rounds split")
    # Sensitivity: the verdict must hold without flagged rounds.
    if flagged and out["verdict"] != "INCONCLUSIVE":
        clean = [r for k, r in zip(sorted(by_round), rounds) if k not in flagged]
        sub = verdict([p0] + [{"kind": "req", "round": i, "arm": arm, "decode_tps": v}
                              for i, r in enumerate(clean) for arm, v in r.items()])
        out["sensitivity_verdict"] = sub["verdict"]
        if sub["verdict"] != out["verdict"]:
            out.update(verdict="INCONCLUSIVE", outcome="verdict does not survive dropping flagged rounds")
    return out


def main(argv):
    with open(argv[1]) as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    print(json.dumps(verdict(records), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
