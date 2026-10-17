#!/usr/bin/env python3
"""analyze.py: the H26 verdict from run.sh output, by the pre-registered rules.

  analyze.py requests.jsonl blocks.jsonl     prints one JSON object

requests.jsonl: client.py lines. blocks.jsonl: one line per block from run.sh
with "block", "round", "arm", "void", "imc_gbs", "pswpin". Rules (EXPERIMENTS.md,
H26 pre-registration):
  I(arm) = p95(arm) / p95(Q) - 1, p95 nearest-rank; R = I(MC4) / I(ME15).
  INCONCLUSIVE if I(ME15) < 0.20, the Q block-p95 spread is > 25% of its median,
  the MC4 neighbour's IMC bandwidth (above Q) is < 15% of ME15's, or the verdict
  flips when swap-flagged blocks (pswpin > 2560 pages) are dropped.
  SUPPORTED if R <= 0.5, bootstrap P(R <= 0.5) >= 0.8 and 3/3 rounds have R <= 0.5.
  REFUTED if R >= 0.8, else REFUTED as stated.
Confidence = P(R <= 0.5) from a bootstrap of requests stratified by block.
Python 3 standard library only.
"""

import json
import math
import random
import statistics
import sys

ARMS = ("Q", "ME15", "ME4", "MC4")
SWAP_PAGES = 2560


def p95(xs):
    """Nearest-rank 95th percentile."""
    s = sorted(xs)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def ratio(lat):
    """R and the I per arm from {arm: [latency]}; None where undefined."""
    q = p95(lat["Q"])
    inf = {a: p95(lat[a]) / q - 1 for a in ARMS if lat.get(a)}
    me15 = inf.get("ME15")
    r = None if not me15 or me15 <= 0 or "MC4" not in inf else inf["MC4"] / me15
    return r, inf


def load(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def decide(reqs, blocks, n_boot=10000, seed=26, drop_swap=False):
    bad = {b["block"] for b in blocks if b.get("void")}
    if drop_swap:
        bad |= {b["block"] for b in blocks if (b.get("pswpin") or 0) > SWAP_PAGES}
    timed = [r for r in reqs if not r["warmup"] and r["ok"] and r["block"] not in bad]
    by_block = {}
    for r in timed:
        by_block.setdefault((r["round"], r["arm"], r["block"]), []).append(r["latency_s"])
    lat = {a: [x for (_, arm, _), v in by_block.items() if arm == a for x in v] for a in ARMS}
    out = {"n": {a: len(lat[a]) for a in ARMS}, "void_blocks": sorted(bad)}
    if not lat["Q"] or not lat["ME15"] or not lat["MC4"]:
        out["verdict"] = "INCONCLUSIVE"
        out["reason"] = "an arm has no valid requests"
        return out
    r, inf = ratio(lat)
    out.update(R=r, I=inf, p95={a: p95(lat[a]) for a in ARMS if lat[a]},
               p50={a: statistics.median(lat[a]) for a in ARMS if lat[a]})
    rounds = {}
    for rd in sorted({k[0] for k in by_block}):
        rl = {a: [x for (rr, arm, _), v in by_block.items() if rr == rd and arm == a for x in v] for a in ARMS}
        rounds[rd] = ratio(rl)[0] if rl["Q"] and rl["ME15"] and rl["MC4"] else None
    out["R_by_round"] = rounds
    qblocks = [p95(v) for (_, arm, _), v in by_block.items() if arm == "Q"]
    out["q_block_p95"] = qblocks
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_boot):
        bl = {a: [] for a in ARMS}
        for (_, arm, _), v in by_block.items():
            bl[arm].extend(rng.choice(v) for _ in v)
        rb, _ = ratio(bl)
        hits += rb is not None and rb <= 0.5
    conf = hits / n_boot
    out["P_R_le_0.5"] = conf
    bw = {a: [b["imc_gbs"] for b in blocks if b["arm"] == a and b["block"] not in bad
              and b.get("imc_gbs") is not None] for a in ARMS}
    hog = {a: statistics.mean(bw[a]) - statistics.mean(bw["Q"]) for a in ARMS if bw[a] and bw["Q"]}
    out["neighbour_imc_gbs"] = hog
    spread = (max(qblocks) - min(qblocks)) / statistics.median(qblocks) if qblocks else 0
    if inf["ME15"] < 0.20:
        out["verdict"], out["reason"] = "INCONCLUSIVE", "positive control failed: I(ME15) < +20%"
    elif spread > 0.25:
        out["verdict"], out["reason"] = "INCONCLUSIVE", "Q block-p95 spread > 25% of median"
    elif "MC4" in hog and "ME15" in hog and hog["ME15"] > 0 and hog["MC4"] < 0.15 * hog["ME15"]:
        out["verdict"], out["reason"] = "INCONCLUSIVE", "MC4 neighbour starved: < 15% of ME15 IMC bandwidth"
    elif r is not None and r <= 0.5 and conf >= 0.8 and all(x is not None and x <= 0.5 for x in rounds.values()) \
            and len(rounds) >= 3:
        out["verdict"], out["reason"] = "SUPPORTED", "R <= 0.5, P >= 0.8, all rounds agree"
    elif r is not None and r >= 0.8:
        out["verdict"], out["reason"] = "REFUTED", "R >= 0.8"
    else:
        out["verdict"], out["reason"] = "REFUTED as stated", "partial effect or low confidence"
    if "ME4" in inf:
        me4_mc4 = [p95(v) for (_, arm, _), v in by_block.items() if arm in ("ME4", "MC4")]
        bspread = (max(me4_mc4) - min(me4_mc4)) / p95(lat["Q"]) if me4_mc4 else 0
        out["H26b"] = ("L2 topology adds isolation" if inf["ME4"] - inf["MC4"] > bspread
                       else "core count, not L2 topology")
    return out


def verdict(reqs, blocks, n_boot=10000):
    main = decide(reqs, blocks, n_boot)
    alt = decide(reqs, blocks, n_boot, drop_swap=True)
    main["verdict_without_swap_blocks"] = alt["verdict"]
    if alt["verdict"] != main["verdict"]:
        main["reason"] = "swap-flag sensitivity flips the verdict (was %s)" % main["verdict"]
        main["verdict"] = "INCONCLUSIVE"
    return main


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(json.dumps(verdict(load(sys.argv[1]), load(sys.argv[2])), indent=1, sort_keys=True))
