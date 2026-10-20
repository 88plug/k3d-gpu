#!/usr/bin/env python3
"""analyze.py: score the H5 stage-1 records with the pre-registered rule.

  bench/prefill-guard/analyze.py RESULTS_DIR [--out analysis.json]

RESULTS_DIR holds one directory per run, <method>-r<round>-p<position>-<arm>, each
with run.json (bench/prefill-guard/run.sh), prefill.json and decode.json
(bench/llm_load.py). The rule is that of docs/prefill-guard.md "Pre-registered
A/B test (H5)" with the definitions of EXPERIMENTS.md "H5 amendment A1":

  penalty(arm)      median ttft_s over all the arm's requests / Q median - 1
  p90 penalty(arm)  the same with p90 (nearest rank, as bench/llm_load.py)
  Q spread          (max - min of the five Q per-run median ttft_s) / Q median
  drift             |median of Q requests in rounds 1-2 - in rounds 4-5| > max - min
                    of the Q per-run medians (seconds)
  INCONCLUSIVE      a run missing or with a failed request, a capped run whose
                    cap read differs from its cap on any of CPUs 16-31, drift, or
                    the N0 penalty below the Q spread
  SUPPORTED         else, a cap level with penalty <= N0 penalty / 2 and p90
                    penalty < N0 p90 penalty
  REFUTED           else, when no cap level has penalty <= N0 penalty / 2
  INCONCLUSIVE      else (a level reaches half but not the p90 condition)

Each method gets its own verdict; the H5 verdict is that of matrixprod.
Python 3 standard library only.
"""

import argparse
import json
import math
import os
import re
import statistics
import sys

ARMS = ("Q", "N0", "C75", "C50", "CMIN")
CAPS = ("C75", "C50", "CMIN")
METHODS = ("matrixprod", "loop")
PRIMARY = "matrixprod"
RUN_DIR = re.compile(r"^(matrixprod|loop)-r([1-5])-p([1-5])-(Q|N0|C75|C50|CMIN)$")


def p90(values):
    v = sorted(values)
    return v[max(0, math.ceil(0.9 * len(v)) - 1)]


def load_runs(root):
    runs = []
    for name in sorted(os.listdir(root)):
        m = RUN_DIR.match(name)
        if not m:
            continue
        d = os.path.join(root, name)
        rec = {"dir": name, "method": m.group(1), "round": int(m.group(2)),
               "position": int(m.group(3)), "arm": m.group(4), "problems": []}
        for part in ("run", "prefill", "decode"):
            path = os.path.join(d, part + ".json")
            if os.path.exists(path):
                with open(path) as f:
                    rec[part] = json.load(f)
            else:
                rec[part] = None
                rec["problems"].append("missing " + part + ".json")
        runs.append(rec)
    return runs


def samples(doc):
    """Recorded samples of an llm_load.py result (every phase)."""
    if not doc:
        return []
    out = []
    for phase in doc.get("phases", []):
        out.extend(phase.get("samples", []))
    return out


def run_values(rec):
    """ttft list, decode tok/s and run-level problems of one run."""
    pre = samples(rec["prefill"])
    ttft = [s.get("ttft_s") for s in pre]
    if len(ttft) != 3 or any(t is None for t in ttft) or any(not s.get("ok") for s in pre):
        rec["problems"].append("prefill: want 3 ok requests")
    dec = samples(rec["decode"])
    tps = dec[0].get("decode_tokens_per_s") if len(dec) == 1 else None
    if tps is None:
        rec["problems"].append("decode: no decode_tokens_per_s")
    run = rec["run"] or {}
    if run and any(run.get("exit_codes_prefill_decode_tool", [1])):
        rec["problems"].append("non-zero exit code")
    if run and not run.get("cpufreq_restored_identical"):
        rec["problems"].append("cpufreq not restored")
    return [t for t in ttft if t is not None], tps


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def ratio(a, b):
    return None if a is None or not b else a / b


def score_method(runs, method):
    mr = [r for r in runs if r["method"] == method]
    res = {"method": method, "runs": len(mr), "problems": [], "arms": {}}
    if not mr:
        res["verdict"] = None
        return res
    per_arm = {a: [] for a in ARMS}
    for r in mr:
        r["ttft"], r["decode_tps"] = run_values(r)
        per_arm[r["arm"]].append(r)
        for p in r["problems"]:
            res["problems"].append(f"{r['dir']}: {p}")
    for a in ARMS:
        rounds = sorted(r["round"] for r in per_arm[a])
        if rounds != [1, 2, 3, 4, 5]:
            res["problems"].append(f"{a}: rounds {rounds}, want 1-5")
    cap_fail = []
    for a in CAPS:
        for r in per_arm[a]:
            run = r["run"] or {}
            reads = run.get("cap_read_khz") or []
            if len(reads) != 16 or any(x != run.get("cap_khz") for x in reads):
                cap_fail.append(r["dir"])
    for a in ARMS:
        rs = per_arm[a]
        ttft = [t for r in rs for t in r["ttft"]]
        runs_json = [r["run"] or {} for r in rs]
        bogo = [((j.get("neighbour") or {}).get("bogo_ops_per_s_real")) for j in runs_json]
        res["arms"][a] = {
            "n_requests": len(ttft),
            "ttft_median_s": med(ttft),
            "ttft_p90_s": p90(ttft) if ttft else None,
            "run_medians_s": [statistics.median(r["ttft"]) if r["ttft"] else None
                              for r in sorted(rs, key=lambda r: r["round"])],
            "decode_tps_median": med([r["decode_tps"] for r in rs]),
            "tool_proxy_s_median": med([j.get("tool_proxy_s") for j in runs_json]),
            "neighbour_bogo_ops_s_median": med(bogo),
            "package_w_median": med([j.get("package_w") for j in runs_json]),
            "throttle_delta_median": med([j.get("package_throttle_delta") for j in runs_json]),
            "pkg_temp_start_c_median": med([j.get("pkg_temp_start_c") for j in runs_json]),
        }
    q = res["arms"]["Q"]
    qmeds = [x for x in q["run_medians_s"] if x is not None]
    qmed, qp90 = q["ttft_median_s"], q["ttft_p90_s"]
    if not qmeds or not qmed:
        res["verdict"] = "INCONCLUSIVE"
        res["reason"] = "no Q data"
        return res
    spread_abs = max(qmeds) - min(qmeds)
    early = [t for r in per_arm["Q"] if r["round"] in (1, 2) for t in r["ttft"]]
    late = [t for r in per_arm["Q"] if r["round"] in (4, 5) for t in r["ttft"]]
    drift = abs(med(early) - med(late)) if early and late else None
    res["q_spread_s"] = spread_abs
    res["q_spread_rel"] = spread_abs / qmed
    res["q_drift_s"] = drift
    for a in ARMS:
        s = res["arms"][a]
        s["penalty"] = ratio(s["ttft_median_s"], qmed) - 1 if s["ttft_median_s"] else None
        s["p90_penalty"] = ratio(s["ttft_p90_s"], qp90) - 1 if s["ttft_p90_s"] else None
    n0 = res["arms"]["N0"]
    for a in CAPS:
        s = res["arms"][a]
        s["neighbour_loss"] = (1 - ratio(s["neighbour_bogo_ops_s_median"], n0["neighbour_bogo_ops_s_median"])
                               if s["neighbour_bogo_ops_s_median"] is not None and n0["neighbour_bogo_ops_s_median"] else None)
        s["tool_proxy_ratio_n0"] = ratio(s["tool_proxy_s_median"], n0["tool_proxy_s_median"])
        s["decode_ratio_n0"] = ratio(s["decode_tps_median"], n0["decode_tps_median"])
        s["half"] = (s["penalty"] is not None and n0["penalty"] is not None
                     and s["penalty"] <= n0["penalty"] / 2)
        s["p90_below_n0"] = (s["p90_penalty"] is not None and n0["p90_penalty"] is not None
                             and s["p90_penalty"] < n0["p90_penalty"])
        s["passes"] = s["half"] and s["p90_below_n0"]
    res["cap_read_failures"] = cap_fail
    passing = [a for a in CAPS if res["arms"][a]["passes"]]
    res["passing_levels"] = passing
    if res["problems"]:
        res["verdict"], res["reason"] = "INCONCLUSIVE", "missing or failed runs"
    elif cap_fail:
        res["verdict"], res["reason"] = "INCONCLUSIVE", "a cap read did not show the cap"
    elif drift is None or drift > spread_abs:
        res["verdict"], res["reason"] = "INCONCLUSIVE", "Q drift between rounds 1-2 and 4-5 exceeds the Q spread"
    elif n0["penalty"] is None or n0["penalty"] < res["q_spread_rel"]:
        res["verdict"], res["reason"] = "INCONCLUSIVE", "N0 penalty below the Q spread"
    elif passing:
        res["verdict"], res["reason"] = "SUPPORTED", "cap level(s) at half or less with lower p90: " + ", ".join(passing)
        res["chosen_level"] = min(passing, key=lambda a: res["arms"][a]["neighbour_loss"]
                                  if res["arms"][a]["neighbour_loss"] is not None else 9)
    elif not any(res["arms"][a]["half"] for a in CAPS):
        res["verdict"], res["reason"] = "REFUTED", "no cap level reaches half the N0 penalty"
    else:
        res["verdict"], res["reason"] = "INCONCLUSIVE", "a level reaches half but its p90 penalty is not below N0's"
    return res


def analyze(root):
    runs = load_runs(root)
    out = {"schema": "k3d-gpu.h5-stage1/v1", "methods": {}}
    for m in METHODS:
        out["methods"][m] = score_method(runs, m)
    out["h5_verdict"] = out["methods"][PRIMARY].get("verdict")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("--out")
    a = ap.parse_args()
    res = analyze(a.root)
    text = json.dumps(res, indent=1, sort_keys=True)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")
    for m, r in res["methods"].items():
        print(f"{m}: {r.get('verdict')} ({r.get('reason')})")
    print(f"H5: {res['h5_verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
