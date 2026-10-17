#!/usr/bin/env python3
"""analyze.py: the H26 verdict from run.sh output, by the pre-registered rules.

  analyze.py requests.jsonl blocks.jsonl [load.jsonl]     prints one JSON object

requests.jsonl: client.py lines. blocks.jsonl: one line per block from run.sh
with "block", "round", "arm", "void", "imc_gbs", "pkg_j", "pswpin". Rules
(EXPERIMENTS.md, H26 pre-registration, amendment A1):
  I(arm) = p95(arm) / p95(Q) - 1, p95 nearest-rank.
  Primary R = I(MC4) / I(ME4): the same 4 stream workers, inside one E-core L2
  cluster (MC4) or spread over all E-cores (ME4), so only placement differs.
  INCONCLUSIVE if I(ME15) < 0.20 (positive control), I(ME4) < 0.10 (nothing to
  isolate), an arm has no IMC reading, the Q block-median spread is > 25% of
  its median, or the verdict flips when swap-flagged blocks (pswpin > 2560
  pages) are dropped.
  SUPPORTED if R <= 0.5, bootstrap P(R <= 0.5) >= 0.8, 3/3 rounds R <= 0.5 and
  the MC4 hog keeps >= 85% of the ME4 hog's IMC bandwidth (else the gain is
  the hog being throttled, not isolation: REFUTED as stated).
  REFUTED if R >= 0.8, else REFUTED as stated.
Confidence = P(R <= 0.5) from a bootstrap of requests stratified by block;
P(R >= 0.8) is reported beside it.
Amendment A2: with load.jsonl (bench/load/load.py sample windows, tagged by arm,
taken with no hog running), a level whose background load is unmatched under
load.py's TOLERANCE is INCONCLUSIVE; "verdict_if_matched" keeps the A1 result.
Python 3 standard library only.
"""

import importlib.util
import json
import math
import os
import random
import statistics
import sys

ARMS = ("Q", "ME15", "ME4", "MC4")
SWAP_PAGES = 2560


def p95(xs):
    """Nearest-rank 95th percentile."""
    s = sorted(xs)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def ratio(lat, stat=p95):
    """R = I(MC4)/I(ME4) and the I per arm from {arm: [latency]}; None where undefined."""
    q = stat(lat["Q"])
    inf = {a: stat(lat[a]) / q - 1 for a in ARMS if lat.get(a)}
    me4 = inf.get("ME4")
    r = None if not me4 or me4 <= 0 or "MC4" not in inf else inf["MC4"] / me4
    return r, inf


def load(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def per_arm(blocks, bad, key):
    """{arm: [block value]} over valid blocks that recorded key."""
    return {a: [b[key] for b in blocks if b["arm"] == a and b["block"] not in bad
                and b.get(key) is not None] for a in ARMS}


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
    if not all(lat[a] for a in ARMS):
        out["verdict"] = "INCONCLUSIVE"
        out["reason"] = "an arm has no valid requests"
        return out
    r, inf = ratio(lat)
    out.update(R=r, I=inf, p95={a: p95(lat[a]) for a in ARMS},
               p50={a: statistics.median(lat[a]) for a in ARMS},
               I_p50=ratio(lat, statistics.median)[1])
    rounds = {}
    for rd in sorted({k[0] for k in by_block}):
        rl = {a: [x for (rr, arm, _), v in by_block.items() if rr == rd and arm == a for x in v] for a in ARMS}
        rounds[rd] = ratio(rl)[0] if all(rl[a] for a in ARMS) else None
    out["R_by_round"] = rounds
    qblocks = [statistics.median(v) for (_, arm, _), v in by_block.items() if arm == "Q"]
    out["q_block_p50"] = qblocks
    rng = random.Random(seed)
    hits = high = 0
    for _ in range(n_boot):
        bl = {a: [] for a in ARMS}
        for (_, arm, _), v in by_block.items():
            bl[arm].extend(rng.choice(v) for _ in v)
        rb, _ = ratio(bl)
        hits += rb is not None and rb <= 0.5
        high += rb is not None and rb >= 0.8
    conf = hits / n_boot
    out["P_R_le_0.5"] = conf
    out["P_R_ge_0.8"] = high / n_boot
    bw = per_arm(blocks, bad, "imc_gbs")
    hog = {a: statistics.mean(bw[a]) - statistics.mean(bw["Q"]) for a in ARMS if bw[a] and bw["Q"]}
    out["neighbour_imc_gbs"] = hog
    pj = per_arm(blocks, bad, "pkg_j")
    out["pkg_j_mean"] = {a: statistics.mean(pj[a]) for a in ARMS if pj[a]}
    spread = (max(qblocks) - min(qblocks)) / statistics.median(qblocks)
    throttled = hog.get("ME4", 0) > 0 and hog.get("MC4", 0) < 0.85 * hog["ME4"]
    if inf["ME15"] < 0.20:
        out["verdict"], out["reason"] = "INCONCLUSIVE", "positive control failed: I(ME15) < +20%"
    elif inf["ME4"] < 0.10:
        out["verdict"], out["reason"] = "INCONCLUSIVE", "I(ME4) < +10%: nothing to isolate"
    elif any(not bw[a] for a in ARMS):
        out["verdict"], out["reason"] = "INCONCLUSIVE", "an arm has no IMC reading: hog bandwidth unproven"
    elif spread > 0.25:
        out["verdict"], out["reason"] = "INCONCLUSIVE", "Q block-median spread > 25% of median"
    elif r is not None and r <= 0.5 and conf >= 0.8 and len(rounds) >= 3 \
            and all(x is not None and x <= 0.5 for x in rounds.values()):
        if throttled:
            out["verdict"], out["reason"] = ("REFUTED as stated",
                                             "MC4 hog < 85% of ME4 IMC bandwidth: throttling, not isolation")
        else:
            out["verdict"], out["reason"] = "SUPPORTED", "R <= 0.5, P >= 0.8, all rounds agree, hog not throttled"
    elif r is not None and r >= 0.8:
        out["verdict"], out["reason"] = "REFUTED", "R >= 0.8"
    else:
        out["verdict"], out["reason"] = "REFUTED as stated", "partial effect or low confidence"
    return out


def bgload():
    """bench/load/load.py as a module (its matched() and TOLERANCE)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "load", "load.py")
    spec = importlib.util.spec_from_file_location("bench_load", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_summary(windows, metrics):
    """Per-arm means of each matching metric over the background windows."""
    out = {}
    for a in ARMS:
        ws = [w for w in windows if w.get("tag") == a]
        out[a] = {"windows": len(ws)}
        for m in metrics:
            vals = [w[m] for w in ws if w.get(m) is not None]
            out[a][m] = round(statistics.mean(vals), 3) if vals else None
    return out


def verdict(reqs, blocks, n_boot=10000, windows=None):
    main = decide(reqs, blocks, n_boot)
    alt = decide(reqs, blocks, n_boot, drop_swap=True)
    main["verdict_without_swap_blocks"] = alt["verdict"]
    if alt["verdict"] != main["verdict"]:
        main["reason"] = "swap-flag sensitivity flips the verdict (was %s)" % main["verdict"]
        main["verdict"] = "INCONCLUSIVE"
    if windows is not None:
        bl = bgload()
        m = bl.matched(windows)
        main.update(load_matched=m["matched"], load_checks=m["checks"],
                    load_by_arm=load_summary(windows, bl.TOLERANCE),
                    verdict_if_matched=main["verdict"])
        if not m["matched"]:
            main["verdict"] = "INCONCLUSIVE"
            main["reason"] = m["verdict"].split(": ", 1)[1]
    return main


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    w = load(sys.argv[3]) if len(sys.argv) == 4 else None
    print(json.dumps(verdict(load(sys.argv[1]), load(sys.argv[2]), windows=w), indent=1, sort_keys=True))
