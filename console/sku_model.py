#!/usr/bin/env python3
"""Cross-SKU roofline predictor for `k3d-gpu plan --predict <sku>` (H24, PENDING).

Maps public Intel spec-sheet fields (P/E core mix, memory channels x MT/s,
AVX-VNNI / AMX, iGPU EUs / XMX, NPU TOPS) to single-box LLM throughput (decode,
prefill, 4-in-flight) and a derived agents-per-box figure. Two efficiency
constants are fitted on one measured configuration of one host (the i9-13900T
P-core arm of [ledger:H6]); every other SKU is an extrapolation and is labelled so.

Models (pre-registered in EXPERIMENTS.md, H24):
  M0  decode_c1 = 1 / max(W / (eta_m * BW_spec), MAC / (eta_c * C_spec))
      c4 step   = max(W / (eta_m * BW_spec), 4 * MAC / (eta_c * C_spec))
      prefill   = eta_c * C_spec / MAC
  M1  decode_c1 = eta_m' * min(BW_spec, triad(cpuset)) / W, with triad measured
      per core set (needs a triad table per microarchitecture; none ships yet).

Nothing here is measured. H24 is PENDING: until it is SUPPORTED, every number
this prints is a model output, not a claim.

Usage:
  sku_model.py list [--json]
  sku_model.py predict <sku|auto> [--json] [--mts N] [--channels N] [--model M]
  sku_model.py arms [--json]          the H24 held-out arm predictions (frozen)
Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# ------------------------------------------------------------------ calibration
# Measured rows ([ledger:H6], SUPPORTED): i9-13900T, 8 P-cores (cpus 0-15,
# 8 inference threads), OVMS 2026.4.1, OpenVINO/Qwen3-4B-int4-ov.
# The UHD 770 rows are the same campaign's GPU arm.
CALIB = {
    "ledger": "H6",
    "host_sku": "i9-13900T",
    "host_mts": 3600,          # dmidecode, 2 DIMMs per channel (H6 run header / ceiling.py HOST)
    "host_channels": 2,
    "cpu_decode_tok_s": 15.66,
    "cpu_prefill_tok_s": 172.0,
    "cpu_cores": {"P": 8},     # the calibration arm's core set
    "igpu_decode_tok_s": 9.89,
    "igpu_prefill_tok_s": 66.0,
}

# Per-token work of the calibration model, OpenVINO/Qwen3-4B-int4-ov.
# W: openvino_model.bin bytes (HF tree API, research/agents-per-box-ceiling/ceiling.py).
# MAC: active params per decoded token from config.json shapes (ceiling.py shapes()).
MODELS = {
    "qwen3-4b": {"weight_bytes": 2263625445, "mac_per_token": 4.0e9,
                 "repo": "OpenVINO/Qwen3-4B-int4-ov"},
    "qwen3-1.7b": {"weight_bytes": 1181738708, "mac_per_token": 1.7e9,
                   "repo": "OpenVINO/Qwen3-1.7B-int4-ov"},
}

# int8 MACs per clock per core, by microarchitecture.
# Raptor/Golden Cove: VPDPBUSD ymm 2/clk x 32 MAC = 64 (uops.info, ADL-P).
# Gracemont: 0.5/clk x 32 = 16 (uops.info, ADL-E). Other cores reuse the nearest
# measured sibling and are marked "assumed" in the output.
MAC_PER_CLK = {
    "raptor-cove": (64, "uops.info VPDPBUSD_VEX_YMM (ADL-P)"),
    "golden-cove": (64, "uops.info VPDPBUSD_VEX_YMM (ADL-P)"),
    "gracemont": (16, "uops.info VPDPBUSD_VEX_YMM (ADL-E)"),
    "crestmont": (16, "assumed = gracemont"),
    "skymont": (16, "assumed = gracemont"),
    "redwood-cove": (64, "assumed = golden-cove"),
    "lion-cove": (64, "assumed = golden-cove"),
    # AVX-512 VNNI, two 512-bit ports: 2 x 64 = 128 (assumed from port count).
    "golden-cove-sp": (128, "assumed: 2 x zmm VPDPBUSD"),
    "redwood-cove-sp": (128, "assumed: 2 x zmm VPDPBUSD"),
}
# AMX TMUL: 2048 int8 ops = 1024 MAC per cycle per core (Intel AMX overview).
AMX_MAC_PER_CLK = 1024
# Sustained all-core clocks are not on the spec sheet. The host's assumed values
# (ceiling.py HOST, not measured) are reused for every SKU; a source of error.
CLK_GHZ = {"P": 4.0, "E": 3.0, "X": 2.0}

# ------------------------------------------------------------------ SKU table
# Public spec fields (ark.intel.com product pages). mts/channels = the highest
# supported memory config; a real box often runs slower (2 DIMMs per channel),
# so --mts/--channels override, and `auto` on this host uses the measured config.
# p/e: core counts; uarch: core microarchitectures; X = server core.
SKUS = {
    "n100": dict(name="Intel Processor N100", family="Alder Lake-N", e=4, e_uarch="gracemont",
                 mts=4800, channels=1, igpu_eu=24, xmx=False, npu_tops=0, amx=False),
    "i3-n305": dict(name="Core i3-N305", family="Alder Lake-N", e=8, e_uarch="gracemont",
                    mts=4800, channels=1, igpu_eu=32, xmx=False, npu_tops=0, amx=False),
    "i5-12400": dict(name="Core i5-12400", family="Alder Lake-S", p=6, p_uarch="golden-cove",
                     mts=4800, channels=2, igpu_eu=24, xmx=False, npu_tops=0, amx=False),
    "i5-13400": dict(name="Core i5-13400", family="Raptor Lake-S", p=6, e=4, p_uarch="golden-cove",
                     e_uarch="gracemont", mts=5600, channels=2, igpu_eu=24, xmx=False,
                     npu_tops=0, amx=False),
    "i7-14700k": dict(name="Core i7-14700K", family="Raptor Lake-S Refresh", p=8, e=12,
                      p_uarch="raptor-cove", e_uarch="gracemont", mts=5600, channels=2,
                      igpu_eu=32, xmx=False, npu_tops=0, amx=False),
    "i9-13900t": dict(name="Core i9-13900T", family="Raptor Lake-S", p=8, e=16,
                      p_uarch="raptor-cove", e_uarch="gracemont", mts=5600, channels=2,
                      igpu_eu=32, xmx=False, npu_tops=0, amx=False),
    "i9-13900k": dict(name="Core i9-13900K", family="Raptor Lake-S", p=8, e=16,
                      p_uarch="raptor-cove", e_uarch="gracemont", mts=5600, channels=2,
                      igpu_eu=32, xmx=False, npu_tops=0, amx=False),
    "ultra7-155h": dict(name="Core Ultra 7 155H", family="Meteor Lake-H", p=6, e=10,
                        p_uarch="redwood-cove", e_uarch="crestmont", mts=7467, channels=2,
                        igpu_eu=128, xmx=False, npu_tops=11, amx=False),
    "ultra7-258v": dict(name="Core Ultra 7 258V", family="Lunar Lake", p=4, e=4,
                        p_uarch="lion-cove", e_uarch="skymont", mts=8533, channels=2,
                        igpu_eu=64, xmx=True, npu_tops=47, amx=False),
    "ultra9-285k": dict(name="Core Ultra 9 285K", family="Arrow Lake-S", p=8, e=16,
                        p_uarch="lion-cove", e_uarch="skymont", mts=6400, channels=2,
                        igpu_eu=64, xmx=False, npu_tops=13, amx=False),
    "xeon-8480+": dict(name="Xeon Platinum 8480+", family="Sapphire Rapids", x=56,
                       x_uarch="golden-cove-sp", mts=4800, channels=8, igpu_eu=0, xmx=False,
                       npu_tops=0, amx=True),
    "xeon-6980p": dict(name="Xeon 6980P", family="Granite Rapids-AP", x=128,
                       x_uarch="redwood-cove-sp", mts=6400, channels=12, igpu_eu=0, xmx=False,
                       npu_tops=0, amx=True),
}
# LPDDR5(X) parts list a 128-bit bus; counted here as 2 x 64-bit channels.

# Agents-per-box (derived, never measured): one agent turn = PROMPT_NEW new prompt
# tokens + OUT_TOKENS generated, one turn every TURN_S seconds; the shared server
# is held at UTIL_MAX utilisation (output decoded at the 4-in-flight rate). The sandbox side packs SANDBOX_MCPU per agent on
# the E-cores (or all cores on a non-hybrid part) at 80% of the cores.
PROMPT_NEW, OUT_TOKENS, TURN_S, UTIL_MAX = 512, 128, 30.0, 0.7
SANDBOX_MCPU = 250


def bw_spec(mts, channels):
    return mts * 1e6 * channels * 8


def compute_spec(cores):
    """cores: {"P": n, "E": n, "X": n} with uarch keys -> (MAC/s, notes)."""
    total, notes = 0.0, []
    for kind, n, uarch, mac_override in cores:
        mac, src = MAC_PER_CLK[uarch]
        if mac_override:
            mac, src = mac_override, "AMX TMUL 1024 MAC/clk (uncalibrated)"
        total += n * mac * CLK_GHZ[kind] * 1e9
        notes.append(f"{n}x{uarch} {mac} MAC/clk @{CLK_GHZ[kind]} GHz ({src})")
    return total, notes


def host_core_set():
    return [("P", CALIB["cpu_cores"]["P"], "raptor-cove", None)]


def etas():
    m = MODELS["qwen3-4b"]
    bw = bw_spec(CALIB["host_mts"], CALIB["host_channels"])
    c, _ = compute_spec(host_core_set())
    eta_m = CALIB["cpu_decode_tok_s"] * m["weight_bytes"] / bw
    eta_c = CALIB["cpu_prefill_tok_s"] * m["mac_per_token"] / c
    return eta_m, eta_c


def m0(bw, c, model="qwen3-4b"):
    """M0 roofline for one core set: decode c=1, prefill, c=4 aggregate (tok/s)."""
    eta_m, eta_c = etas()
    m = MODELS[model]
    t_mem = m["weight_bytes"] / (eta_m * bw)
    t_cmp = m["mac_per_token"] / (eta_c * c)
    return {
        "decode_c1": 1.0 / max(t_mem, t_cmp),
        "prefill": eta_c * c / m["mac_per_token"],
        "c4_agg": 4.0 / max(t_mem, 4 * t_cmp),
        "bound_c1": "memory" if t_mem >= t_cmp else "compute",
    }


def uncalibrated_roofline(bw, model="qwen3-4b"):
    """Baseline: LLM-Viewer-style roofline, efficiency 1 (spec bandwidth / bytes)."""
    return bw / MODELS[model]["weight_bytes"]


def agents(c4_agg, prefill, cores_for_sandboxes):
    """Derived agents-per-box: min of the serving cap and the sandbox CPU cap.

    Server time per turn = prompt at the prefill rate + output at the 4-in-flight
    aggregate rate (decode is batched across agents)."""
    busy_per_turn = PROMPT_NEW / prefill + OUT_TOKENS / c4_agg
    serve = int(UTIL_MAX * TURN_S / busy_per_turn) if busy_per_turn > 0 else 0
    sandbox = int(cores_for_sandboxes * 1000 * 0.8 / SANDBOX_MCPU)
    return {"agents": min(serve, sandbox), "serving_cap": serve, "sandbox_cap": sandbox,
            "binding": "serving" if serve <= sandbox else "sandbox-cpu"}


def predict(key, mts=None, channels=None, model="qwen3-4b", host_measured=False):
    s = SKUS[key]
    mts = mts or s["mts"]
    channels = channels or s["channels"]
    bw = bw_spec(mts, channels)
    # Inference core set: P-cores (or server cores; E-only parts use E-cores).
    if s.get("x"):
        inf = [("X", s["x"], s["x_uarch"], AMX_MAC_PER_CLK if s["amx"] else None)]
        sandbox_cores = 0  # one socket shared; sandboxes take what inference leaves
    elif s.get("p"):
        inf = [("P", s["p"], s["p_uarch"], None)]
        sandbox_cores = s.get("e", 0)
    else:
        inf = [("E", s["e"], s["e_uarch"], None)]
        sandbox_cores = 0
    c, notes = compute_spec(inf)
    r = m0(bw, c, model)
    if not sandbox_cores:
        # Non-hybrid part: half the cores for sandboxes, half for inference
        # (inference compute halves; decode is memory-bound on paper anyway).
        n = s.get("x") or s.get("p") or s.get("e")
        sandbox_cores = n // 2
    calibrated = host_measured and key == CALIB["host_sku"].lower()
    label = "calibrated" if calibrated else "extrapolated"
    out = {
        "schema": "sku-predict/v1",
        "sku": key, "name": s["name"], "family": s["family"],
        "model": MODELS[model]["repo"],
        "status": label,
        "hypothesis": "H24 PENDING: model output, not a measured claim",
        "inputs": {"mts": mts, "channels": channels, "bw_spec_gbs": round(bw / 1e9, 1),
                   "inference_cores": notes, "sandbox_cores": sandbox_cores,
                   "amx": s["amx"], "igpu_eu": s["igpu_eu"], "xmx": s["xmx"],
                   "npu_tops": s["npu_tops"]},
        "cpu": {"decode_tok_s": round(r["decode_c1"], 1), "prefill_tok_s": round(r["prefill"], 1),
                "c4_agg_tok_s": round(r["c4_agg"], 1), "bound": r["bound_c1"]},
        "baseline_uncalibrated_decode_tok_s": round(uncalibrated_roofline(bw, model), 1),
        "agents_per_box": dict(agents(r["c4_agg"], r["prefill"], sandbox_cores),
                               status="derived"),
        "calibration": {"ledger": CALIB["ledger"], "eta_m": round(etas()[0], 3),
                        "eta_c": round(etas()[1], 3)},
        "caveats": [],
    }
    if s["amx"]:
        out["caveats"].append("AMX prefill is extrapolated: no AMX part was measured")
    if s.get("x"):
        out["caveats"].append("server part: spec-DRAM decode at c=1 is an upper bound "
                              "(single-stream decode is core-bound well below it)")
    if not s.get("p") and not s.get("x"):
        out["caveats"].append("E-core-only part: M0 ignores per-cluster bandwidth; "
                              "the H24 prior expects it to overpredict decode")
    if s["igpu_eu"]:
        # iGPU on the DP4a path, scaled from the UHD 770 rows by EU count and spec BW.
        eta_g = CALIB["igpu_decode_tok_s"] * MODELS["qwen3-4b"]["weight_bytes"] / bw_spec(
            CALIB["host_mts"], CALIB["host_channels"])
        dec = eta_g * bw / MODELS[model]["weight_bytes"]
        pre = CALIB["igpu_prefill_tok_s"] * s["igpu_eu"] / 32 * (
            MODELS["qwen3-4b"]["mac_per_token"] / MODELS[model]["mac_per_token"])
        out["igpu"] = {"decode_tok_s": round(dec, 1), "prefill_tok_s": round(pre, 1),
                       "path": "DP4a (UHD 770 calibration)"}
        if s["xmx"]:
            out["igpu"]["note"] = "XMX present: prefill not modelled (no XMX part measured)"
    if s["npu_tops"]:
        out["npu"] = {"tops": s["npu_tops"], "decode_tok_s": None,
                      "note": "no NPU calibration row: no number predicted"}
    return out


def h24_arms():
    """The frozen H24 arm predictions on this host (cpusets of the i9-13900T)."""
    bw = bw_spec(CALIB["host_mts"], CALIB["host_channels"])
    arms = {"A": [("P", 8, "raptor-cove", None)], "B": [("P", 4, "raptor-cove", None)],
            "C": [("E", 8, "gracemont", None)], "D": [("E", 16, "gracemont", None)]}
    res = {}
    for k, cs in arms.items():
        c, _ = compute_spec(cs)
        r = m0(bw, c)
        res[k] = {"decode_c1": round(r["decode_c1"], 1), "prefill": round(r["prefill"], 1),
                  "c4_agg": round(r["c4_agg"], 1)}
    return res


def detect_sku(cpuinfo="/proc/cpuinfo"):
    try:
        with open(cpuinfo) as f:
            txt = f.read()
    except OSError:
        return None
    m = re.search(r"^model name\s*:\s*(.+)$", txt, re.M)
    if not m:
        return None
    name = m.group(1).lower()
    for key, s in SKUS.items():
        tail = s["name"].lower().split()[-1]  # "i9-13900t", "n100", "155h", "8480+"
        if re.search(r"(?<![0-9a-z])" + re.escape(tail) + r"(?![0-9a-z])", name):
            return key
    return None


def render(p):
    lines = [f"{p['name']} ({p['family']}), {p['model']}: {p['status']}, {p['hypothesis']}",
             f"  memory: {p['inputs']['channels']} ch x {p['inputs']['mts']} MT/s = "
             f"{p['inputs']['bw_spec_gbs']} GB/s spec",
             f"  cpu:    decode {p['cpu']['decode_tok_s']} tok/s ({p['cpu']['bound']}-bound), "
             f"prefill {p['cpu']['prefill_tok_s']} tok/s, 4 in flight {p['cpu']['c4_agg_tok_s']} tok/s",
             f"  baseline (uncalibrated roofline, efficiency 1): decode "
             f"{p['baseline_uncalibrated_decode_tok_s']} tok/s"]
    if "igpu" in p:
        lines.append(f"  igpu:   decode {p['igpu']['decode_tok_s']} tok/s, prefill "
                     f"{p['igpu']['prefill_tok_s']} tok/s ({p['igpu']['path']})")
    if "npu" in p:
        lines.append(f"  npu:    {p['npu']['tops']} TOPS, {p['npu']['note']}")
    a = p["agents_per_box"]
    lines.append(f"  agents: {a['agents']} per box (derived; serving cap {a['serving_cap']}, "
                 f"sandbox cap {a['sandbox_cap']}, binds on {a['binding']})")
    for c in p["caveats"]:
        lines.append(f"  note:   {c}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sku_model.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list")
    pl.add_argument("--json", action="store_true")
    pp = sub.add_parser("predict")
    pp.add_argument("sku")
    pp.add_argument("--json", action="store_true")
    pp.add_argument("--mts", type=int)
    pp.add_argument("--channels", type=int)
    pp.add_argument("--model", default="qwen3-4b", choices=sorted(MODELS))
    pp.add_argument("--cpuinfo", default="/proc/cpuinfo")
    pa = sub.add_parser("arms")
    pa.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        rows = {k: v["name"] for k, v in SKUS.items()}
        print(json.dumps(rows, indent=1) if a.json else "\n".join(f"{k:14} {v}" for k, v in rows.items()))
        return 0
    if a.cmd == "arms":
        r = h24_arms()
        print(json.dumps(r, indent=1) if a.json else "\n".join(
            f"{k}: decode {v['decode_c1']}  prefill {v['prefill']}  c4 {v['c4_agg']}" for k, v in r.items()))
        return 0
    for name, v in (("--mts", a.mts), ("--channels", a.channels)):
        if v is not None and v <= 0:
            print(f"sku_model: {name} must be positive", file=sys.stderr)
            return 2
    key, host = a.sku.lower(), False
    if key == "auto":
        key = detect_sku(a.cpuinfo)
        if key is None:
            print("sku_model: this CPU is not in the SKU table (use `plan --predict list`)",
                  file=sys.stderr)
            return 2
        host = True
        if key == CALIB["host_sku"].lower() and a.mts is None and a.channels is None:
            a.mts, a.channels = CALIB["host_mts"], CALIB["host_channels"]
    if key not in SKUS:
        print(f"sku_model: unknown SKU {a.sku!r}; known: {', '.join(SKUS)}", file=sys.stderr)
        return 2
    calibrated_cfg = host and a.mts == CALIB["host_mts"] and a.channels == CALIB["host_channels"]
    p = predict(key, a.mts, a.channels, a.model, host_measured=calibrated_cfg)
    print(json.dumps(p, indent=1, sort_keys=True) if a.json else render(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
