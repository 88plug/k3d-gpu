#!/usr/bin/env python3
"""report.py: the H59-family figures the pre-registration reports and never gates (EXPERIMENTS.md
"H59 pre-registration", Measurement 2 and 4), from the records h59.py scores.

  report.py RESULTS_DIR [--arms L=NAME,O=NAME]

Per arm, over the performance holds 1-3 (a spare standing in for the hold it repeats, measured
arms only):
  - decode tok/s medians per phase (n1, n4, n16) per hold and pooled;
  - aggregate tok/s per phase per hold (bench/llm_load.py's aggregate_tokens_per_s);
  - TTFT on the 1,000-token prompt: the median ttft_s of the 4 requests per hold, and pooled;
  - prefix reuse: the median first-chunk TTFT of agents 2-6 per hold (prefix.json), and the
    median over the holds;
  - RAPL J per output token per phase, pooled over the holds: gross (the phase's joules over its
    output tokens) and net of idle (each hold's phase joules less that arm's idle-window watts
    times the phase's seconds).
The verdict is h59.py's; nothing here gates. Python 3 standard library only."""

import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import h59  # noqa: E402  (hold_dirs, status, phase_samples, decode_samples)

PHASES = ("n1", "n4", "n16")


def jload(p):
    with open(p) as f:
        return json.load(f)


def med(v):
    return statistics.median(v) if v else None


def arm_report(results, name):
    hd = h59.hold_dirs(results)
    rep = {"holds": [], "decode": {}, "aggregate": {}, "ttft_s": {}, "prefix_added_ttft_s": {}, "j_per_token": {}}
    pooled = {p: [] for p in PHASES + ("ttft",)}
    energy = {p: {"j": 0.0, "net": 0.0, "tokens": 0} for p in PHASES}
    for k in h59.PERF_HOLDS:
        ad = os.path.join(hd[k], name)
        if h59.status(ad) != "measured":
            continue
        rep["holds"].append(k)
        for p in PHASES:
            s = h59.decode_samples(ad, p)
            if not s:
                continue
            pooled[p] += s
            rep["decode"].setdefault(p, {})[k] = med(s)
            ph = jload(os.path.join(ad, f"load-{p}.json"))["phases"][0]
            rep["aggregate"].setdefault(p, {})[k] = ph.get("aggregate_tokens_per_s")
            ef, idle = os.path.join(ad, f"energy-{p}.json"), os.path.join(ad, "energy-idle.json")
            if os.path.exists(ef) and os.path.exists(idle) and ph.get("output_tokens_total"):
                e, w = jload(ef), jload(idle)["avg_watts"]
                energy[p]["j"] += e["joules"]
                energy[p]["net"] += e["joules"] - w * e["seconds"]
                energy[p]["tokens"] += ph["output_tokens_total"]
        t = [x["ttft_s"] for x in h59.phase_samples(ad, "ttft") if x.get("ok") and x.get("ttft_s") is not None]
        if t:
            pooled["ttft"] += t
            rep["ttft_s"][k] = med(t)
        pf = os.path.join(ad, "prefix.json")
        if os.path.exists(pf):
            rep["prefix_added_ttft_s"][k] = jload(pf).get("added_ttft_median_s")
    rep["decode_pooled"] = {p: med(pooled[p]) for p in PHASES}
    rep["ttft_pooled_s"] = med(pooled["ttft"])
    rep["prefix_added_ttft_median_s"] = med([v for v in rep["prefix_added_ttft_s"].values() if v is not None])
    for p in PHASES:
        n = energy[p]["tokens"]
        rep["j_per_token"][p] = {"gross": energy[p]["j"] / n, "net": energy[p]["net"] / n, "tokens": n} if n else None
    return rep


def report(results, arms=None):
    arms = arms or {"L": "L", "O": "O"}
    out = {role: arm_report(results, name) for role, name in arms.items()}
    lo, oo = out["L"], out["O"]
    out["ratios_L_over_O"] = {
        "decode_pooled": {p: (lo["decode_pooled"][p] / oo["decode_pooled"][p]
                              if lo["decode_pooled"][p] and oo["decode_pooled"][p] else None) for p in PHASES},
        "ttft_pooled": (lo["ttft_pooled_s"] / oo["ttft_pooled_s"]
                        if lo["ttft_pooled_s"] and oo["ttft_pooled_s"] else None),
        "prefix_added_ttft": (lo["prefix_added_ttft_median_s"] / oo["prefix_added_ttft_median_s"]
                              if lo["prefix_added_ttft_median_s"] and oo["prefix_added_ttft_median_s"] else None),
    }
    return out


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    arms = h59.parse_arms(argv[2]) if len(argv) > 2 and argv[1] == "--arms" else None
    print(json.dumps(report(argv[0], arms), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
