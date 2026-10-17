import json, random, subprocess, sys, os
import tempfile; d = tempfile.mkdtemp()
rng = random.Random(1)
base = {"int4": 3.0, "int8": 4.5, "fp16": 9.0, "int4'": 3.0}
with open(d + "/req.jsonl", "w") as f:
    for i in range(1, 6):
        for arm, t in base.items():
            f.write(json.dumps({"phase": "reference", "label": "R%d" % i, "arm": arm, "precision": arm.rstrip("'"),
                                "total_s": t * rng.uniform(0.97, 1.03), "pgmajfault": 0, "wall": i}) + "\n")
    for i in range(1, 4):
        for arm, (tt, dec) in {"int4": (0.7, 15), "int8": (0.6, 9), "fp16": (1.5, 5)}.items():
            f.write(json.dumps({"phase": "probe", "label": "P%d" % i, "arm": arm, "precision": arm, "ttft_s": tt,
                                "decode_tok_s": dec, "wall": 10 + i}) + "\n")
json.dump({"heldout": {"fp16": 2.0, "int8": 2.004, "int4": 2.04}, "wikitext2": {"fp16": 2.5, "int8": 2.5, "int4": 2.51}},
          open(d + "/nll.json", "w"))
here = sys.argv[1]
out = subprocess.run([sys.executable, here + "/bench/amx-autoquant/summarize.py", "--requests", d + "/req.jsonl",
                      "--nll", d + "/nll.json", "--out", d + "/res.json"], capture_output=True, text=True, check=True)
r = json.load(open(d + "/res.json"))
assert r["reference_pick"] == "int4" and r["wins"] == 5 and r["probe_agree"] == 3, r
assert all(0.9 <= v <= 1.1 for v in r["control_ratio"].values())
assert abs(r["arms"]["int4"]["dnll"] - 0.04) < 1e-9
print("summarize smoke ok")
# H27 A1: --load embeds the background-load match; an unmatched arm adds an INCONCLUSIVE note.
def windows(path, fp16_busy):
    with open(path, "w") as f:
        for arm in base:
            for _ in range(5):
                f.write(json.dumps({"tag": arm, "level": "typical", "busy_pct": fp16_busy if arm == "fp16" else 40.0,
                                    "load1": 25.0, "pkg_w": 60.0, "throttle_per_min": 100.0,
                                    "swap_used_mib": 1000.0, "swap_delta_mib": 0.0}) + "\n")
for busy, ok in ((42.0, True), (60.0, False)):
    windows(d + "/load.jsonl", busy)
    subprocess.run([sys.executable, here + "/bench/amx-autoquant/summarize.py", "--requests", d + "/req.jsonl",
                    "--nll", d + "/nll.json", "--out", d + "/res.json", "--load", d + "/load.jsonl"],
                   capture_output=True, text=True, check=True)
    r = json.load(open(d + "/res.json"))
    assert r["load"]["matched"] is ok, r["load"]
    assert any("load unmatched (busy_pct" in n for n in r["notes"]) is (not ok), r["notes"]
print("summarize load-match ok")
