#!/usr/bin/env python3
"""describe.py: the H55 records beside the verdict, not gated.

  python3 bench/admission/results/2026-10-29/H55/describe.py [RESULTS_DIR]

The verdict is analyze.py's (analysis.json). This script reads the same records
with analyze.py's own loader and applies the formulas of its scoring branch
(pooled median and p90 TTFT, penalty against Q, loss against N0's median
bogo-ops/s, Q spread, drift, NB spread) without stopping at the INCONCLUSIVE gate
that analyze.py returns on. It also lists the per-run values the pre-registration
records but does not score: tool proxy, decode tok/s, package W, throttle delta,
load1 and package temperature at run start, and A_slo's level over the window.
Nothing here is a verdict. Python 3 standard library only.
"""

import glob
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..")))
import analyze  # noqa: E402  (bench/admission/analyze.py)

OUT = sys.argv[1] if len(sys.argv) > 1 else HERE
med = statistics.median
runs = analyze.load_runs(OUT)
by = {(r["arm"], r["round"]): r for r in runs}


def pooled(arm, rounds=analyze.ROUNDS):
    return [v for rnd in rounds for v in by[(arm, rnd)]["ttft"]]


extra = {}
for d in sorted(glob.glob(os.path.join(OUT, "r*-p*-*"))):
    m = analyze.RUN_DIR.match(os.path.basename(d))
    if not m:
        continue
    with open(os.path.join(d, "run.json")) as f:
        run = json.load(f)
    with open(os.path.join(d, "decode.json")) as f:
        dec = json.load(f)["phases"][0]["stats"]
    e = {"pos": int(m.group(2)), "ref": run["ref_s"], "tool": run["tool_proxy_s"],
         "decode": dec["decode_tokens_per_s"]["median"], "W": run["package_w"],
         "thr": run["package_throttle_delta"], "load1": run["load1_start"],
         "temp": run["pkg_temp_start_c"], "cpufreq_ok": run["cpufreq_matches_snapshot"]}
    if m.group(3) == "A_slo":
        w0, w1 = run["window_start"], run["window_end"]
        with open(os.path.join(d, "ctl.jsonl")) as f:
            ticks = [json.loads(x) for x in f if x.strip()]
        lv = [t["level"] for t in ticks if w0 <= t["t"] < w1 and "level" in t]
        e["level"] = (sum(lv) / len(lv), lv[-1], max(lv))
        e["windows"] = [round(t["median"][0], 3) for t in ticks if "median" in t]
    extra[(m.group(3), int(m.group(1)))] = e

q_med, q_p90 = med(pooled("Q")), analyze.p90(pooled("Q"))
q_runs = [med(by[("Q", r)]["ttft"]) for r in analyze.ROUNDS]
spread = max(q_runs) - min(q_runs)
drift = abs(med(pooled("Q", (1, 2))) - med(pooled("Q", (4, 5))))
n0_runs = [by[("N0", r)]["nb_bogo_s"] for r in analyze.ROUNDS]
n0 = med(n0_runs)
n0_tool = med(extra[("N0", r)]["tool"] for r in analyze.ROUNDS)
n0_dec = med(extra[("N0", r)]["decode"] for r in analyze.ROUNDS)
print(f"Q median {q_med:.3f} s, p90 {q_p90:.3f} s; Q spread {spread:.3f} s ({spread / q_med:.1%} of the Q median);"
      f" drift {drift:.3f} s; NB spread {(max(n0_runs) - min(n0_runs)) / n0:.1%}")
print("| Arm | Requests | TTFT median / p90 s | Run medians s (rounds 1-5) | Penalty median / p90 |"
      " Neighbour bogo-ops/s (loss) | Tool proxy s (x N0) | Decode tok/s (x N0) | Package W | Throttle |")
print("|---|---|---|---|---|---|---|---|---|---|")
for arm in analyze.ARMS:
    p = pooled(arm)
    rm = ", ".join(f"{med(by[(arm, r)]['ttft']):.2f}" for r in analyze.ROUNDS)
    col = {k: med(extra[(arm, r)][k] for r in analyze.ROUNDS) for k in ("tool", "decode", "W", "thr")}
    pen, nb = "-", "-"
    if arm != "Q":
        pen = f"{med(p) / q_med - 1:+.1%} / {analyze.p90(p) / q_p90 - 1:+.1%}"
        b = med(by[(arm, r)]["nb_bogo_s"] for r in analyze.ROUNDS)
        nb = f"{b:.0f} ({1 - b / n0:.1%})"
    print(f"| {arm} | {len(p)} | {med(p):.2f} / {analyze.p90(p):.2f} | {rm} | {pen} | {nb} |"
          f" {col['tool']:.2f} ({col['tool'] / n0_tool:.2f}) | {col['decode']:.2f} ({col['decode'] / n0_dec:.2f}) |"
          f" {col['W']:.1f} | {col['thr']:.0f} |")
print()
for (arm, rnd), e in sorted(extra.items(), key=lambda kv: (kv[0][1], kv[1]["pos"])):
    r = by[(arm, rnd)]
    line = (f"r{rnd}-p{e['pos']}-{arm}: requests {len(r['ttft'])} failed {r['failed']} median {med(r['ttft']):.3f} s"
            f" REF {e['ref']} bogo-ops/s {r['nb_bogo_s']} load1 {e['load1']} temp {e['temp']} C W {e['W']}"
            f" throttle {e['thr']} tool {e['tool']} decode {e['decode']:.2f} cpufreq_ok {e['cpufreq_ok']}")
    if arm == "A_slo":
        line += (f" level mean {e['level'][0]:.1f} end {e['level'][1]} max {e['level'][2]}"
                 f" window medians {e['windows']}")
    print(line)
l1 = [e["load1"] for e in extra.values()]
tp = [e["temp"] for e in extra.values()]
print(f"load1 at run start {min(l1)}-{max(l1)} (median {med(l1)}); package temperature {min(tp):.0f}-{max(tp):.0f} C")
