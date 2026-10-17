#!/usr/bin/env python3
"""leaderboard.py: the static capacity leaderboard for the docs site.

Reads the maintainer-lab bundles (oracle/seed/) and the merged community
bundles (data/oracle/), runs each through validate.py by way of matrix.py, and
writes one row per matrix cell:

  agents per box   validator-recomputed APB at the bundle's p95 turn SLO
  decode tok/s     validator-recomputed single-stream decode median
  tok/s/W          output tokens per package joule (tok/s per W) at the
                   agents-per-box step (the largest agent count that met the
                   SLO), from that step's samples and its J per turn; "-" when
                   no energy was recorded
  basis            "measured" for every number above. "predicted" is reserved
                   for the bandwidth roofline (V8), printed in its own column
                   and never mixed into a measured column.

Only `accept` verdicts reach the board (matrix.py drops the rest). Rows are
sorted by SKU then engine; nothing is ranked, so the page makes no "fastest"
claim. Writes docs/leaderboard.md (rendered by mkdocs on GitHub Pages) and
docs/leaderboard.json (the same rows, machine-readable).

  leaderboard.py [--md OUT.md] [--json OUT.json] [--check]

--check regenerates both from the bundles the committed leaderboard.json lists
as its inputs (plus oracle/seed/) and exits 1 when the committed files differ
(CI). A data/oracle/ bundle that is not an input yet (a community pull request,
or one merged before the maintainer regenerated) is reported as pending and
does not fail the check; `validate.py --strict` is the gate for those files.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import matrix as M  # noqa: E402

SCHEMA = "k3d-gpu.oracle.leaderboard/1"
SEED = os.path.join(HERE, "seed")
DATA = os.path.join(REPO, "data", "oracle")
MD_OUT = os.path.join(REPO, "docs", "leaderboard.md")
JSON_OUT = os.path.join(REPO, "docs", "leaderboard.json")


def tokens_per_joule(bundle, agents):
    """Output tokens per package joule at step `agents`, or None."""
    cap = (bundle.get("results") or {}).get("capacity")
    if not cap:
        return None
    step = next((s for s in cap["steps"] if s["agents"] == agents), None)
    if not step or not step["j_per_turn_pkg"]:
        return None
    turns = sum(len(r["session"]) for r in step["reps"])
    toks = sum(sum(r["out_tok"]) for r in step["reps"])
    return toks / (step["j_per_turn_pkg"] * turns) if turns else None


def median(vals):
    return statistics.median(vals) if vals else None


def load(url_base_rel):
    path = os.path.join(REPO, url_base_rel)
    with open(path) as f:
        return json.load(f)


def data_files(data=DATA):
    """The bundles under data/, as paths relative to it, sorted."""
    if not os.path.isdir(data):
        return []
    return sorted(os.path.relpath(p, data) for p in M.collect([data]))


def build(seed=SEED, data=DATA, repo=REPO, inputs=None):
    """inputs: the data/ bundles to read (relative to `data`); None reads all."""
    inputs = data_files(data) if inputs is None else sorted(inputs)
    paths = ([seed] if os.path.isdir(seed) else []) + [os.path.join(data, f) for f in inputs]
    m = M.build(paths, trusted_dirs=[seed], repo=repo)
    rows = []
    for c in m["cells"]:
        meta = m["keys"][c["comparison_key"]]
        met = c["metrics"]
        apb = (met.get("agents_per_box") or {}).get("median")
        tpj, roof = [], []
        for u in c["data"]:
            rel = u[len(M.DEFAULT_URL_BASE):] if u.startswith(M.DEFAULT_URL_BASE) else None
            if not rel:
                continue
            b = load(rel)
            if apb is not None:
                v = tokens_per_joule(b, int(apb))
                if v is not None:
                    tpj.append(v)
            res = M.V.validate(b, trusted=rel.startswith("oracle/seed/"), repo=repo)
            if res.recomputed.get("decode_roofline_tok_s") is not None:
                roof.append(res.recomputed["decode_roofline_tok_s"])
        rows.append({
            "sku": c["sku"], "cpu": c["cpu"], "engine": c["engine"], "kind": meta["kind"],
            "model": meta["model"], "comparison_key": c["comparison_key"][:12],
            "agents_per_box": apb,
            "slo_s": meta["workload"].get("slo_s") if meta["kind"] == "capacity" else None,
            "decode_tok_s": (met.get("decode_tok_s") or {}).get("median"),
            "tok_s_per_w": M.rnd(median(tpj)),
            "predicted_decode_roofline_tok_s": M.rnd(median(roof)),
            "basis": "measured",
            "n": c["n"], "tier": c["tier"], "tags": c["tags"],
            "ledger": c["ledger"], "data": c["data"],
        })
    rows.sort(key=lambda r: (r["sku"], r["engine"], r["comparison_key"]))
    return {"schema": SCHEMA, "rows": rows, "review": len(m["review"]), "inputs": inputs}


def fmt(x, nd=2):
    return "-" if x is None else f"{x:.{nd}f}"


def markdown(board):
    L = ["# Capacity leaderboard", "",
         "Generated by `oracle/leaderboard.py` from the bundles under `oracle/seed/` (maintainer lab)",
         "and `data/oracle/` (merged community pull requests); do not edit by hand. Every number is",
         "recomputed by `oracle/validate.py` (rules V1-V12) from the raw per-request samples in the",
         "linked bundle. Machine-readable: [leaderboard.json](leaderboard.json). How to add a row:",
         "[Capacity oracle](oracle.md).", "",
         "**Measured** columns come from a run on that box. **Predicted** columns are the bandwidth",
         "roofline (memory bandwidth / weight bytes) and are never a measurement; `-` means the bundle",
         "does not carry the inputs. `k3d-gpu share` does not record memory channels, MT/s or weight",
         "bytes yet, so that column is not computed for any row it builds. The SKU is the CPU plus the",
         "device the engine ran on. Rows are sorted by SKU, not ranked.", "",
         "| SKU | Engine | Agents per box (measured) | Decode tok/s (measured) | tok/s/W (measured) "
         "| Decode roofline tok/s (predicted) | n | Tier | Tags | Data |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    if not board["rows"]:
        L.append("| - | - | - | - | - | - | 0 | - | - | - |")
    for r in board["rows"]:
        apb = "-" if r["agents_per_box"] is None else f"{r['agents_per_box']:.0f} at p95 {r['slo_s']} s"
        ref = "".join(f" [ledger:{h}]" for h in r["ledger"])
        L.append(f"| {r['sku']} | {r['engine']} | {apb} | {fmt(r['decode_tok_s'])} | {fmt(r['tok_s_per_w'], 3)} | "
                 f"{fmt(r['predicted_decode_roofline_tok_s'])} | {r['n']} | {r['tier']} | "
                 f"{', '.join(r['tags']) or '-'} | " + " ".join(f"[bundle]({u})" for u in r["data"]) + ref + " |")
    L += ["", f"Bundles in review (quarantined or outliers, not shown): {board['review']}.", ""]
    return "\n".join(L)


def render(board):
    return markdown(board), json.dumps(board, indent=1, sort_keys=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="leaderboard.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--md", default=MD_OUT)
    ap.add_argument("--json", default=JSON_OUT)
    ap.add_argument("--check", action="store_true", help="exit 1 when the committed files are stale")
    a = ap.parse_args(argv)
    if a.check:
        try:
            with open(a.json) as f:
                listed = json.load(f).get("inputs") or []
        except (OSError, ValueError):
            listed = []
        present = set(data_files(DATA))
        pending = sorted(present - set(listed))
        missing = sorted(set(listed) - present)
        md, js = render(build(data=DATA, inputs=[f for f in listed if f in present]))
        stale = []
        for path, text in ((a.md, md), (a.json, js)):
            try:
                with open(path) as f:
                    if f.read() != text:
                        stale.append(path)
            except OSError:
                stale.append(path)
        for p in stale:
            print(f"leaderboard: {os.path.relpath(p, REPO)} is stale; run python3 oracle/leaderboard.py")
        for p in missing:
            print(f"leaderboard: data/oracle/{p} is listed as an input but is gone")
        for p in pending:
            print(f"leaderboard: data/oracle/{p} is pending; a maintainer adds it by running "
                  "python3 oracle/leaderboard.py after merge")
        return 1 if stale else 0
    md, js = render(build(data=DATA))
    for path, text in ((a.md, md), (a.json, js)):
        with open(path, "w") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
