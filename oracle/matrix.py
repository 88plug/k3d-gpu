#!/usr/bin/env python3
"""matrix.py: accepted bundles -> the per-SKU capacity matrix (JSON + markdown).

Every bundle goes through validate.py first; only `accept` verdicts enter a
cell, `quarantine` ones are listed for review, `reject` ones are dropped.
Numbers come from the validator's recomputation, never from a claim.

A cell is (comparison key, CPU model, accelerator device ids, engine, PL1
bucket, DRAM bucket, lever states). Per cell: median, IQR and n, where n
counts submitters, one vote each (a submitter's several bundles in one cell
are first reduced to their median). Tiers: provisional (n = 1), community
(n >= 2, every vote within 15% of the median), verified (n >= 3 community, or
a maintainer-lab vote with a community vote that agrees). A vote more than 3
robust z from the median goes to review unless it is tagged throttled.

  matrix.py [--trusted DIR]... [--submitters FILE] [--url-base URL]
            [--md OUT.md] [--json OUT.json] BUNDLE_OR_DIR...

--submitters maps content_sha256 -> an opaque submitter token (the ingest
workflow knows who opened the issue; the token never enters a bundle). Without
it, every community bundle counts as its own submitter and every
maintainer-lab bundle as one shared submitter.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import validate as V  # noqa: E402

MATRIX_SCHEMA = "k3d-gpu.oracle.matrix/1"
DEFAULT_URL_BASE = "https://github.com/88plug/k3d-gpu/blob/main/"
AGREE = 0.15
Z_MAX = 3.0
HEADLINE = {"capacity": "agents_per_box", "single_stream": "decode_tok_s"}


def pl1_bucket(w):
    if w is None:
        return "unknown"
    for lim in (35, 65, 125):
        if w <= lim:
            return f"<={lim} W"
    return ">125 W"


def dram_bucket(mem):
    if mem["channels"] is None or mem["configured_mts"] is None:
        return "unknown"
    return f"{mem['channels']}x{int(round(mem['configured_mts'] / 400.0) * 400)}"


def accelerators(hw):
    ids = [f"intel:{g['device_id']}" for g in hw["intel_gpus"]]
    ids += [f"npu:{n['device_id']}" for n in hw["npus"]]
    ids += [f"nvidia:{g['device_id']}" for g in hw["nvidia"]["gpus"]]
    if hw["gaudi"]["devices"]:
        ids.append(f"gaudi:{hw['gaudi']['devices']}")
    return sorted(ids)


def sku(hw):
    names = [hw["cpu"]["model"] or "unknown CPU"]
    names += sorted({g["platform"] or g["device_id"] for g in hw["intel_gpus"]})
    names += sorted({g["model"] or g["device_id"] for g in hw["nvidia"]["gpus"]})
    return " + ".join(names)


def lever_vector(levers):
    return ",".join(f"{lv['id']}={lv['state']}" for lv in sorted(levers, key=lambda x: x["id"])
                    if lv["detected"] and lv["state"] != "unknown")


def metrics(b, rc):
    if b["kind"] == "capacity":
        steps = b["results"]["capacity"]["steps"]
        at = next((s for s in steps if s["agents"] == rc["agents_per_box"]), None)
        return {"agents_per_box": rc["agents_per_box"], "j_per_turn_pkg": at["j_per_turn_pkg"] if at else None}
    return {"decode_tok_s": rc["decode_tok_s"], "prefill_ttft_s": rc["prefill_ttft_s"]}


def vote(entries, metric):
    """One submitter's vote: the median of their bundles' values in the cell."""
    vs = [e["metrics"][metric] for e in entries if e["metrics"][metric] is not None]
    return statistics.median(vs) if vs else None


def summary(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        q1 = q3 = vals[0]
    else:
        q1, _m, q3 = statistics.quantiles(vals, n=4, method="inclusive")
    return {"median": statistics.median(vals), "q1": q1, "q3": q3}


def rnd(x, nd=3):
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: rnd(v, nd) for k, v in x.items()}
    if isinstance(x, list):
        return [rnd(v, nd) for v in x]
    return x


def collect(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += sorted(glob.glob(os.path.join(p, "**", "*.json"), recursive=True))
        else:
            out.append(p)
    return sorted(set(os.path.abspath(p) for p in out))


def build(paths, trusted_dirs=(), submitters=None, url_base=DEFAULT_URL_BASE, repo=REPO, pins=None):
    submitters = submitters or {}
    trusted_dirs = [os.path.abspath(d) + os.sep for d in trusted_dirs]
    cells, review, seen = {}, [], set()
    keys_meta = {}
    for path in collect(paths):
        rel = os.path.relpath(path, repo)
        with open(path, "rb") as f:
            raw = f.read()
        try:
            b = json.loads(raw)
        except ValueError:
            review.append({"file": rel, "verdict": "reject", "reasons": ["not JSON"]})
            continue
        res = V.validate(b, raw_size=len(raw), trusted=any(path.startswith(d) for d in trusted_dirs),
                         known_hashes=seen, repo=repo, pins=pins)
        if res.verdict != "accept":
            review.append({"file": rel, "verdict": res.verdict, "reasons": res.reasons})
            continue
        seen.add(b["content_sha256"])
        hw, key = b["hardware"], b["comparison_key"]
        w, t = b["workload"], b["tool"]
        keys_meta[key] = {"kind": b["kind"], "tool_version": t["version"], "k3s": t["k3s"],
                          "server_image": t["server_image"], "model": w["model"]["id"],
                          "model_revision": w["model"]["revision"], "protocol": w["protocol"],
                          "engine": b["results"]["engine"], "workload": w}
        cell_id = (key, hw["cpu"]["model"], tuple(accelerators(hw)), b["results"]["engine"],
                   pl1_bucket(b["power"]["pl1_w"]), dram_bucket(hw["memory"]), lever_vector(b["levers"]))
        sub = submitters.get(b["content_sha256"]) or (
            "maintainer-lab" if b["provenance"]["source"] == "maintainer-lab" else b["content_sha256"])
        c = cells.setdefault(cell_id, {"sku": sku(hw), "entries": []})
        c["entries"].append({"kind": b["kind"], "file": rel, "url": url_base + rel, "submitter": sub,
                             "maintainer": b["provenance"]["source"] == "maintainer-lab",
                             "note": b["provenance"]["note"], "tags": res.tags, "ab": b["ab"],
                             "week": b["week"], "metrics": metrics(b, res.recomputed)})
    rows = []
    for cell_id in sorted(cells, key=lambda k: (k[0], cells[k]["sku"], k[3], k[4], k[5], k[6])):
        key, cpu, accel, engine, pl1b, dramb, levers_on = cell_id
        c = cells[cell_id]
        plain = [e for e in c["entries"] if e["ab"] is None]
        if not plain:
            continue
        names = sorted(plain[0]["metrics"])
        head = HEADLINE[plain[0]["kind"]]
        votes = {}
        for e in plain:
            votes.setdefault(e["submitter"], []).append(e)
        per_metric = {m: summary([vote(es, m) for es in votes.values()]) for m in names}
        med = (per_metric[head] or {}).get("median")
        vals = {s: vote(es, head) for s, es in votes.items()}
        agree = {s for s, v in vals.items() if med and v is not None and abs(v - med) / med <= AGREE}
        maint = any(e["maintainer"] for e in plain)
        community_agree = {s for s in agree if not any(e["maintainer"] for e in votes[s])}
        n = len(votes)
        if (len(agree) == n and len(community_agree) >= 3) or (maint and community_agree):
            tier = "verified"
        elif n >= 2 and len(agree) == n:
            tier = "community"
        else:
            tier = "provisional"
        # Robust z per vote on the headline metric.
        if n >= 3 and med:
            vals = {s: v for s, v in vals.items() if v is not None}
            mad = statistics.median(abs(v - med) for v in vals.values())
            for s, v in vals.items():
                z = abs(v - med) / (1.4826 * mad) if mad else 0.0
                if z > Z_MAX and not any("throttled" in e["tags"] for e in votes[s]):
                    review += [{"file": e["file"], "verdict": "outlier", "reasons": [f"robust z {z:.1f} > {Z_MAX:g}"]}
                               for e in votes[s]]
        tags = sorted({t for e in plain for t in e["tags"]})
        rows.append({"comparison_key": key, "sku": c["sku"], "cpu": cpu, "accelerators": list(accel),
                     "engine": engine, "pl1_bucket": pl1b, "dram_bucket": dramb, "levers_on": levers_on,
                     "n": n, "tier": tier, "tags": tags, "metrics": per_metric,
                     "ledger": sorted({e["note"][len("ledger-"):] for e in plain if e["note"].startswith("ledger-")}),
                     "data": sorted(e["url"] for e in plain)})
    uplifts = pair_uplifts(cells)
    return {"schema": MATRIX_SCHEMA, "keys": {k: keys_meta[k] for k in sorted(keys_meta)},
            "cells": rnd(rows), "uplifts": rnd(uplifts), "review": sorted(review, key=lambda r: r["file"])}


def pair_uplifts(cells):
    """V12: an A/B arm counts only with its partner (same nonce, key, week, opposite arm)."""
    arms = {}
    for (key, *_rest), c in cells.items():
        for e in c["entries"]:
            if e["ab"]:
                arms.setdefault((key, e["ab"]["pair_nonce"], e["week"], e["ab"]["lever"]), {})[e["ab"]["arm"]] = e
    out = []
    for (key, _nonce, _week, lever), pair in sorted(arms.items()):
        if set(pair) != {"on", "off"}:
            continue
        on, off = pair["on"]["metrics"], pair["off"]["metrics"]
        ratio = {m: on[m] / off[m] for m in sorted(on) if on.get(m) and off.get(m)}
        out.append({"comparison_key": key, "lever": lever, "ratio_on_over_off": ratio,
                    "data": sorted([pair["on"]["url"], pair["off"]["url"]])})
    return out


# ---------------------------------------------------------------- markdown
def fmt(x, nd=2):
    return "-" if x is None else f"{x:.{nd}f}"


def stat_cell(s, nd=2):
    if not s:
        return "-"
    if s["q1"] == s["q3"]:
        return fmt(s["median"], nd)
    return f"{fmt(s['median'], nd)} [{fmt(s['q1'], nd)}-{fmt(s['q3'], nd)}]"


def markdown(m):
    L = ["# Capacity oracle matrix", "",
         "Generated by `oracle/matrix.py` from the bundles under `oracle/seed/`; do not edit by hand.",
         "Regenerate: `python3 oracle/matrix.py --trusted oracle/seed --md docs/oracle.md oracle/seed`.",
         "Design: [moat-design.md](moat-design.md). Every value is recomputed by `oracle/validate.py`",
         "from the raw per-request samples in the linked bundle; client-side summaries are never used.", "",
         "Cells compare only inside one comparison key (pinned tool build, server image, model",
         "revision, workload and engine). Each value is the median with its IQR in brackets when",
         "n > 1; n counts submitters, one vote each. Tier: provisional (n = 1), community, verified.", ""]
    for key, meta in m["keys"].items():
        rows = [r for r in m["cells"] if r["comparison_key"] == key]
        if not rows:
            continue
        L += [f"## Key `{key[:12]}`: {meta['protocol']}, {meta['engine']}", ""]
        img = (meta["server_image"] or "unpinned").split("@")
        L += [f"- tool: `{meta['tool_version'][:16]}`, k3s `{meta['k3s']}`, server `{img[0]}` "
              f"`{img[1][:19] if len(img) > 1 else ''}`",
              f"- model: `{meta['model']}` at `{(meta['model_revision'] or 'unpinned')[:12]}`"]
        w = meta["workload"]
        if w["single_stream"]:
            ss = w["single_stream"]
            L.append(f"- workload: decode {ss['decode']['prompt_tokens']} in / {ss['decode']['max_tokens']} out, "
                     f"prefill ~{ss['prefill']['prompt_tokens']} in / {ss['prefill']['max_tokens']} out, "
                     f"{ss['rounds']} rounds x {ss['per_round']} requests, greedy, ignore_eos")
        else:
            L.append(f"- workload: trace `{w['trace']}`, p95 turn SLO {w['slo_s']} s, {w['reps']} reps, "
                     f"up to {w['max_agents']} agents")
        L.append("")
        if meta["kind"] == "single_stream":
            L += ["| SKU | Engine | Decode tok/s | Prefill TTFT s | PL1 | DRAM | Levers set | n | Tier | Tags | Data |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
            for r in rows:
                ref = "".join(f" [ledger:{h}]" for h in r["ledger"])
                L.append(f"| {r['sku']} | {r['engine']} | {stat_cell(r['metrics']['decode_tok_s'])} | "
                         f"{stat_cell(r['metrics']['prefill_ttft_s'])} | {r['pl1_bucket']} | {r['dram_bucket']} | "
                         f"{r['levers_on'] or '-'} | {r['n']} | {r['tier']} | {', '.join(r['tags']) or '-'} | "
                         + " ".join(f"[bundle]({u})" for u in r["data"]) + ref + " |")
        else:
            L += ["| SKU | Engine | Agents per box | J/turn (package) | PL1 | DRAM | Levers set | n | Tier | Tags | Data |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
            for r in rows:
                L.append(f"| {r['sku']} | {r['engine']} | {stat_cell(r['metrics']['agents_per_box'], 0)} | "
                         f"{stat_cell(r['metrics']['j_per_turn_pkg'], 1)} | {r['pl1_bucket']} | {r['dram_bucket']} | "
                         f"{r['levers_on'] or '-'} | {r['n']} | {r['tier']} | {', '.join(r['tags']) or '-'} | "
                         + " ".join(f"[bundle]({u})" for u in r["data"]) + " |")
        L.append("")
    L += ["## Intel Advantage uplifts (paired A/B only)", ""]
    if m["uplifts"]:
        L += ["| Key | Lever | on / off | Data |", "|---|---|---|---|"]
        for u in m["uplifts"]:
            ratios = ", ".join(f"{k} {v:.2f}" for k, v in u["ratio_on_over_off"].items())
            L.append(f"| `{u['comparison_key'][:12]}` | {u['lever']} | {ratios} | "
                     + " ".join(f"[bundle]({x})" for x in u["data"]) + " |")
    else:
        L.append("None yet: no lever has a paired on/off run in one comparison key.")
    L += ["", "## Review queue", ""]
    if m["review"]:
        L += ["| File | Verdict | Reasons |", "|---|---|---|"]
        for r in m["review"]:
            L.append(f"| `{r['file']}` | {r['verdict']} | {'; '.join(r['reasons'])[:300]} |")
    else:
        L.append("Empty.")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="matrix.py", description="Build the capacity-oracle matrix.")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--trusted", action="append", default=[], help="dir whose bundles may carry maintainer-lab provenance")
    ap.add_argument("--submitters", help="JSON map content_sha256 -> submitter token")
    ap.add_argument("--url-base", default=DEFAULT_URL_BASE)
    ap.add_argument("--md")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    subs = None
    if a.submitters:
        with open(a.submitters) as f:
            subs = json.load(f)
    m = build(a.paths, a.trusted, subs, a.url_base)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(m, f, indent=1, sort_keys=True)
            f.write("\n")
    if a.md:
        with open(a.md, "w") as f:
            f.write(markdown(m))
    if not a.json and not a.md:
        print(json.dumps(m, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
