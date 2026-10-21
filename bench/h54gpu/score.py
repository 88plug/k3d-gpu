#!/usr/bin/env python3
"""score.py ARMS.json: H54b's chosen arm and verdict (EXPERIMENTS.md "H54 pre-registration").

ARMS.json maps arm id -> {"run": llm_load JSON path or null, "correct": GSM8K 0-49 correct or
null, "rank": 1 author/Intel/OpenVINO, 2 RedHatAI, 3 88plug or built here, 4 third party,
"cards": cards used}. B2q is the stock arm; when it is missing, Sq stands in for the quality gate
only. Gate: score >= stock score - 5 (of 50). Chosen: the fastest gate-passing one-card arm of
the best rank; a lower-ranked arm replaces it only at >= 5% higher median N=1 decode and no more
than 2 items fewer correct. Statistic: chosen / B2q median N=1 decode, 95% bootstrap CI over
requests (10,000 resamples, seed 1). SUPPORTED: CI lo >= 2.0 and gate passed; REFUTED: CI hi <
2.0; otherwise INCONCLUSIVE (also when B2q or every one-card arm is missing). Prints JSON.
"""
import json
import sys

from stats import decodes, median, ratio_ci

STOCK, GATE_STANDIN, GATE_ITEMS = "B2q", "Sq", 5


def choose(arms):
    """arms: id -> {"dec": [..], "correct": int, "rank": int}, all gate-passing one-card arms."""
    by_rank = {}
    for k, a in arms.items():
        by_rank.setdefault(a["rank"], []).append(k)
    chosen, log = None, []
    for r in sorted(by_rank):
        best = max(by_rank[r], key=lambda k: median(arms[k]["dec"]))
        if chosen is None:
            chosen = best
            log.append(f"rank {r}: {best} fastest")
            continue
        c, b = arms[best], arms[chosen]
        if median(c["dec"]) >= 1.05 * median(b["dec"]) and c["correct"] >= b["correct"] - 2:
            log.append(f"rank {r}: {best} replaces {chosen} (>=5% faster, quality within 2)")
            chosen = best
        else:
            log.append(f"rank {r}: {best} does not replace {chosen}")
    return chosen, log


def score(spec):
    arms = {}
    for k, a in spec.items():
        dec = []
        if a.get("run"):
            try:
                with open(a["run"]) as f:
                    dec = decodes(json.load(f))
            except (OSError, ValueError, KeyError):
                dec = []
        arms[k] = {"dec": dec, "correct": a.get("correct"), "rank": a.get("rank", 4),
                   "cards": a.get("cards", 1)}
    out = {"medians": {k: (median(a["dec"]) if a["dec"] else None) for k, a in arms.items()},
           "correct": {k: a["correct"] for k, a in arms.items()}}
    stock = arms.get(STOCK)
    gate_ref = STOCK if stock and stock["dec"] and stock["correct"] is not None else GATE_STANDIN
    ref = arms.get(gate_ref)
    out["gate_ref"] = gate_ref
    if not ref or ref["correct"] is None:
        out.update(verdict="INCONCLUSIVE", why="no gate reference (B2q and Sq missing)")
        return out
    thr = ref["correct"] - GATE_ITEMS
    elig = {k: a for k, a in arms.items() if k != STOCK and a["cards"] == 1 and a["dec"]
            and a["correct"] is not None and a["correct"] >= thr}
    out["gate_threshold"] = thr
    out["eligible"] = sorted(elig)
    if not elig:
        out.update(verdict="INCONCLUSIVE", why="no one-card arm passed the gate")
        return out
    chosen, log = choose(elig)
    out.update(chosen=chosen, choice_log=log)
    if not stock or not stock["dec"]:
        out.update(verdict="INCONCLUSIVE", why="B2q not deliverable")
        return out
    p, lo, hi = ratio_ci(arms[chosen]["dec"], stock["dec"])
    out.update(ratio=p, ci=[lo, hi])
    if lo >= 2.0:
        out["verdict"] = "SUPPORTED"
    elif hi < 2.0:
        out["verdict"] = "REFUTED"
    else:
        out["verdict"] = "INCONCLUSIVE"
    return out


def main(argv):
    with open(argv[0]) as f:
        print(json.dumps(score(json.load(f)), indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
