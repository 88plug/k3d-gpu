#!/usr/bin/env python3
"""H27 summary: requests (results.jsonl) + NLL (nll.json) -> results.json + verdict inputs.

results.json has the shape console/autoquant.py `pick` reads: per arm the
reference turn latencies (total_s of the 396+96 request), dNLL vs fp16 on the
held-out set, and a flag. Also reported, never used to drop data:
  - per-round int4'/int4 ratio (negative control; outside [0.9, 1.1] is flagged)
  - per-probe-repeat pick (extrapolated turn = ttft*396/128 + 95/decode)
  - per-arm spread (min, median, max), confounds per request
  - wins of the reference pick over every other passing arm, per round

  summarize.py --requests results.jsonl --nll nll.json --out results.json [--gate 0.05]
"""

import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "console"))
import autoquant  # noqa: E402

MAJFAULT_FLAG = 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", required=True)
    ap.add_argument("--nll")
    ap.add_argument("--out", required=True)
    ap.add_argument("--gate", type=float, default=autoquant.GATE_NATS)
    a = ap.parse_args()
    reqs = [json.loads(line) for line in open(a.requests, encoding="utf-8") if line.strip()]
    nll = json.load(open(a.nll, encoding="utf-8")) if a.nll and os.path.exists(a.nll) else {}
    held, wiki = nll.get("heldout") or {}, nll.get("wikitext2") or {}
    ref = [r for r in reqs if r["phase"] == "reference" and not r.get("error")]
    arms, notes = {}, []
    for prec in autoquant.MODELS:
        rows = [r for r in ref if r["arm"] == prec]
        turns = [r["total_s"] for r in rows]
        faults = sum(1 for r in rows if r.get("pgmajfault", 0) > MAJFAULT_FLAG)
        base = held.get("fp16")
        dnll = held[prec] - base if isinstance(held.get(prec), float) and isinstance(base, float) else None
        flagged = prec == "fp16" and faults >= 2
        arms[prec] = {"turn_s": turns, "dnll": dnll, "flagged": flagged,
                      "flag_reason": "%d requests with >%d major faults" % (faults, MAJFAULT_FLAG) if flagged else None,
                      "spread": [min(turns), statistics.median(turns), max(turns)] if turns else None,
                      "dnll_wikitext2": (wiki[prec] - wiki["fp16"])
                      if isinstance(wiki.get(prec), float) and isinstance(wiki.get("fp16"), float) else None}
    rounds = sorted({r["label"] for r in ref})
    ctrl = {}
    for lab in rounds:
        by = {r["arm"]: r["total_s"] for r in ref if r["label"] == lab}
        if "int4" in by and "int4'" in by:
            ctrl[lab] = by["int4'"] / by["int4"]
    bad_ctrl = [lab for lab, v in ctrl.items() if not 0.9 <= v <= 1.1]
    if len(bad_ctrl) >= 2:
        notes.append("INCONCLUSIVE: int4'/int4 outside [0.9, 1.1] in rounds %s" % bad_ctrl)
    if held and all(isinstance(held.get(p), float) for p in held) and len({held[p] for p in held}) == 1:
        notes.append("INCONCLUSIVE: every NLL equal, the quality proxy is broken")
    winner, reasons = autoquant.pick({"arms": arms}, a.gate)
    wins = {}
    if winner:
        for lab in rounds:
            by = {r["arm"]: r["total_s"] for r in ref if r["label"] == lab}
            others = [p for p in arms if p != winner and p in by and arms[p]["dnll"] is not None
                      and arms[p]["dnll"] <= a.gate and not arms[p]["flagged"]]
            wins[lab] = winner in by and all(by[winner] < by[p] for p in others)
    probe_picks = {}
    for lab in sorted({r["label"] for r in reqs if r["phase"] == "probe"}):
        est = {}
        for r in reqs:
            if r["phase"] == "probe" and r["label"] == lab and r.get("ttft_s") and r.get("decode_tok_s"):
                est[r["precision"]] = r["ttft_s"] * 396 / 128 + 95 / r["decode_tok_s"]
        pa = {p: {"turn_s": [v], "dnll": arms[p]["dnll"], "flagged": arms[p]["flagged"]} for p, v in est.items()}
        probe_picks[lab] = {"pick": autoquant.pick({"arms": pa}, a.gate)[0], "est_turn_s": est}
    probe_t = [r["wall"] for r in reqs if r["phase"] == "probe"]
    doc = {"measured_at": max((r["wall"] for r in reqs), default=None), "arms": arms, "gate_nats": a.gate,
           "reference_pick": winner, "reasons": reasons, "control_ratio": ctrl,
           "reference_wins": wins, "wins": sum(wins.values()), "rounds": len(rounds),
           "probe_picks": probe_picks,
           "probe_agree": sum(1 for v in probe_picks.values() if v["pick"] == winner),
           "probe_repeats": len(probe_picks),
           "errors": [r for r in reqs if r.get("error")], "notes": notes,
           "probe_span_note": "probe wall per repeat is in probe.log (budget 60 s)" if probe_t else None}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    print(json.dumps({k: doc[k] for k in ("reference_pick", "wins", "rounds", "probe_agree", "probe_repeats",
                                          "control_ratio", "notes")}, indent=2))


if __name__ == "__main__":
    main()
