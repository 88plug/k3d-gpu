#!/usr/bin/env python3
"""predict.py BYTES.json CG.json GG.json HG.json GG2.json: H54c's byte-split prediction and
verdict (EXPERIMENTS.md "H54 pre-registration", H54c).

t_Hg = t_Cg * B_exp/B_tot + t_Gg * B_rest/B_tot + LAYERS * CROSSINGS * 30 us, with t = 1 /
median N=1 decode tok/s, Gg = the first Gg run (the second is the drift check). The measured
Hg tok/s over the predicted tok/s decides: SUPPORTED when within 25%, REFUTED when off by more
than 40%, otherwise INCONCLUSIVE; also INCONCLUSIVE when an arm is missing or the two Gg runs
differ by more than 10%. Prints JSON.
"""
import json
import sys

from stats import decodes, median

LAYERS, CROSSINGS, CROSS_S = 30, 2, 30e-6


def verdict(b, cg, gg, hg, gg2):
    out = {"B_exp": b["B_exp"], "B_rest": b["B_rest"], "B_tot": b["B_tot"]}
    meds = {}
    for k, v in (("Cg", cg), ("Gg", gg), ("Hg", hg), ("Gg2", gg2)):
        meds[k] = median(v) if v else None
    out["median_tps"] = meds
    if any(meds[k] is None for k in ("Cg", "Gg", "Hg", "Gg2")):
        out.update(verdict="INCONCLUSIVE", why="an arm was not delivered")
        return out
    t = (1 / meds["Cg"]) * b["B_exp"] / b["B_tot"] + (1 / meds["Gg"]) * b["B_rest"] / b["B_tot"] \
        + LAYERS * CROSSINGS * CROSS_S
    pred = 1 / t
    ratio = meds["Hg"] / pred
    drift = meds["Gg2"] / meds["Gg"] - 1
    out.update(pred_t_s=t, pred_tps=pred, measured_over_pred=ratio, gg_drift=drift)
    if abs(drift) > 0.10:
        out.update(verdict="INCONCLUSIVE", why="Gg runs differ by more than 10%")
    elif abs(ratio - 1) <= 0.25:
        out["verdict"] = "SUPPORTED"
    elif abs(ratio - 1) > 0.40:
        out["verdict"] = "REFUTED"
    else:
        out["verdict"] = "INCONCLUSIVE"
    return out


def load(p):
    try:
        with open(p) as f:
            return decodes(json.load(f))
    except (OSError, ValueError, KeyError):
        return []


def main(argv):
    with open(argv[0]) as f:
        b = json.load(f)
    print(json.dumps(verdict(b, *(load(p) for p in argv[1:5])), indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
