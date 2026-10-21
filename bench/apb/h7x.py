#!/usr/bin/env python3
"""h7x.py RUN_DIR: score an H7x run (EXPERIMENTS.md H7x) from bench/apb/h7x.sh's records.

Reads b<i>-<E>-{decode,prefill,conc4}.json (bench/llm_load.py) and the matching
b<i>-<E>-<w>-energy.json and b<i>-<E>-idle-energy.json (bench/energy.sh). E is A (OVMS fp16),
B (vLLM CPU bf16, EAT chart defaults) or C (B without --enforce-eager).

A block is void when its decode phase has fewer than 5 ok requests, any request stopped short
of 256 output tokens or has no decode rate, or the engine's pod restarted during the block
(restarts.txt: "b<i> <E> <restarts before> <restarts after>"). With >= 2 void blocks, or fewer than 4 valid blocks (a hold cut
short by its cap), in arm A or B the run is INCONCLUSIVE.

Decision (pre-registered in H7x): R = median A decode tok/s / median B decode tok/s over every
request of the valid blocks; gap(x) = max(x, 1/x) - 1. Blocks pair in run order: the k-th A
block with the k-th B block, void blocks included in the count (ABBAABBAAB gives 5 pairs); a pair
with a void block is dropped. r_k = ratio of their block medians.
  SUPPORTED     gap(R) < 0.15, gap(R_bw) < 0.15 and gap(r_k) < 0.15 for every pair
  REFUTED       gap(R) >= 0.15 and every r_k on R's side of 1 with gap(r_k) >= 0.15
  INCONCLUSIVE  otherwise
R_bw (effective weight bandwidth, weight bytes x decode tok/s) uses the bytes passed in.
Reported, never gating: C/B decode, prefill TTFT and conc4 per arm, and the H2 energy method
(idle W from the 20 s model-loaded idle; incremental J = E - idle W x t) per token.
Stdlib only.
"""
import glob
import json
import os
import re
import statistics as st
import sys

A_BYTES, B_BYTES = 8_044_936_594, 8_044_982_000   # H7's weights: fp16 IR .bin, bf16 safetensors


def gap(x):
    return max(x, 1 / x) - 1


def load(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def restarts(run):
    """{block: True} for blocks whose pod restarted (restarts.txt from h7x.sh)."""
    out = {}
    try:
        with open(os.path.join(run, "restarts.txt")) as f:
            for line in f:
                p = line.split()
                if len(p) == 4 and p[0].startswith("b"):
                    out[int(p[0][1:])] = p[2] != p[3]
    except OSError:
        pass
    return out


def blocks(run):
    out, rs = [], restarts(run)
    for p in glob.glob(os.path.join(run, "b*-*-decode.json")):
        m = re.match(r"b(\d+)-([ABC])-decode\.json$", os.path.basename(p))
        if not m:
            continue
        i, e = int(m.group(1)), m.group(2)
        rec = {"block": i, "engine": e}
        for w in ("decode", "prefill", "conc4"):
            d = load(os.path.join(run, f"b{i}-{e}-{w}.json"))
            ph = d["phases"][0] if d and d.get("phases") else None
            rec[w] = ph
            rec[w + "_energy"] = load(os.path.join(run, f"b{i}-{e}-{w}-energy.json"))
        rec["idle"] = load(os.path.join(run, f"b{i}-{e}-idle-energy.json"))
        dec = [s for s in (rec["decode"] or {}).get("samples", []) if s.get("ok")]
        rec["decode_tps"] = [s["decode_tokens_per_s"] for s in dec if s.get("decode_tokens_per_s")]
        rec["restarted"] = rs.get(i, False)
        rec["void"] = (len(dec) < 5 or len(rec["decode_tps"]) < len(dec) or rec["restarted"]
                       or any(s.get("output_tokens") != 256 for s in dec))
        out.append(rec)
    return sorted(out, key=lambda r: r["block"])


def incremental_j_per_token(rec, w):
    e, idle, ph = rec.get(w + "_energy"), rec.get("idle"), rec.get(w)
    if not (e and idle and ph and ph.get("output_tokens_total") and idle.get("seconds")):
        return None
    p_idle = idle["joules"] / idle["seconds"]
    return (e["joules"] - p_idle * e["seconds"]) / ph["output_tokens_total"]


def score(recs):
    arms = {e: [r for r in recs if r["engine"] == e and not r["void"]] for e in "ABC"}
    void = {e: sum(1 for r in recs if r["engine"] == e and r["void"]) for e in "ABC"}
    res = {"blocks": [{k: r[k] for k in ("block", "engine", "void", "decode_tps")} for r in recs], "void": void}
    if void["A"] >= 2 or void["B"] >= 2 or len(arms["A"]) < 4 or len(arms["B"]) < 4:
        return dict(res, verdict="INCONCLUSIVE", why=">= 2 void blocks, or < 4 valid blocks, in A or B")
    med = {e: st.median(t for r in arms[e] for t in r["decode_tps"]) for e in "ABC" if arms[e]}
    R = med["A"] / med["B"]
    R_bw = (A_BYTES * med["A"]) / (B_BYTES * med["B"])
    run_order = {e: [r for r in recs if r["engine"] == e] for e in "AB"}
    pairs = [st.median(a["decode_tps"]) / st.median(b["decode_tps"])
             for a, b in zip(run_order["A"], run_order["B"]) if not (a["void"] or b["void"])]
    side = all((r > 1) == (R > 1) for r in pairs)
    if gap(R) < 0.15 and gap(R_bw) < 0.15 and all(gap(r) < 0.15 for r in pairs):
        v = "SUPPORTED"
    elif gap(R) >= 0.15 and side and all(gap(r) >= 0.15 for r in pairs):
        v = "REFUTED"
    else:
        v = "INCONCLUSIVE"

    def reported(e):
        rs = arms.get(e) or []
        ttft = [s["ttft_s"] for r in rs for s in (r["prefill"] or {}).get("samples", []) if s.get("ok") and s.get("ttft_s")]
        c4 = [r["conc4"]["aggregate_tokens_per_s"] for r in rs if r["conc4"] and r["conc4"].get("aggregate_tokens_per_s")]
        jt = [x for x in (incremental_j_per_token(r, "decode") for r in rs) if x is not None]
        return {"decode_tps_median": med.get(e), "prefill_ttft_median_s": st.median(ttft) if ttft else None,
                "conc4_tps_median": st.median(c4) if c4 else None,
                "decode_incremental_j_per_token_median": st.median(jt) if jt else None, "blocks": len(rs)}

    return dict(res, verdict=v, R=round(R, 4), gap=round(gap(R), 4), R_bandwidth=round(R_bw, 4),
                pair_ratios=[round(r, 4) for r in pairs],
                C_over_B=round(med["C"] / med["B"], 4) if "C" in med else None,
                arms={e: reported(e) for e in "ABC"})


if __name__ == "__main__":
    json.dump(score(blocks(sys.argv[1])), sys.stdout, indent=1)
    print()
