#!/usr/bin/env python3
"""H24 live probe: does the spec-only roofline (console/sku_model.py, M0) transfer
from the 8-P-core calibration arm to core sets it was not fitted on?

Pre-registered in EXPERIMENTS.md ("H24 pre-registration") before any run. This
script measures; it never edits the predictions. It self-limits to DEADLINE_S
(17 min) from start and keeps partial data.

  run:    sudo -E python3 bench/sku-model/probe.py --out DIR
          (env IMAGE = the H6 OVMS image by digest, MODELS = dir with one repository
          per arm, MODELS/{A,B,C,D}/OpenVINO/Qwen3-4B-int4-ov; run under the live-work flock)
  score:  python3 bench/sku-model/probe.py --score DIR/raw.jsonl
  plan:   python3 bench/sku-model/probe.py --dry-run   (prints the visit plan, no docker)

Arms on an i9-13900T (cpus 0-15 = 8 P-cores x 2 threads, 16-31 = 16 E-cores):
  A 8P  cpus 0-15,  8 threads, PCORE_ONLY  (calibration, first and last)
  B 4P  cpus 0-7,   4 threads, PCORE_ONLY
  C 8E  cpus 16-23, 8 threads, ECORE_ONLY  (two whole Gracemont clusters)
  D 16E cpus 16-31, 16 threads, ECORE_ONLY
Only containers named sci-h24-* are started or removed. No sysfs writes.
Stdlib only.

Amendment A1 (matched background load, EXPERIMENTS.md "H24 Amendment A1"): with
--level, the caller holds bench/load/load.py at that level for the whole run. Each
visit's 60 s idle wait is replaced by one load.py window (--load-window-s, the arm
idle) tagged with the arm. The scorer calls a level INCONCLUSIVE when those windows
are unmatched under load.py TOLERANCE; busy and throttle flags are recorded, not
gates (the load match covers them); the swap flag stays a gate.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import secrets
import signal
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "console"))
import sku_model  # noqa: E402

_spec = importlib.util.spec_from_file_location("loadtool", os.path.join(REPO, "bench", "load", "load.py"))
loadtool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loadtool)

# Internal deadline sits 120 s under the outer `timeout 1140` so cleanup and
# scoring finish before the outer SIGTERM.
DEADLINE_S = 17 * 60
IDLE_WAIT_S = 60
READY_S = 5 * 60
MODEL = "qwen3-4b"
SOURCE_MODEL = "OpenVINO/Qwen3-4B-int4-ov"
ARMS = {
    "A": dict(cpus="0-15", threads=8, core="PCORE_ONLY", port=8241, cpu_list=range(0, 16)),
    "B": dict(cpus="0-7", threads=4, core="PCORE_ONLY", port=8242, cpu_list=range(0, 8)),
    "C": dict(cpus="16-23", threads=8, core="ECORE_ONLY", port=8243, cpu_list=range(16, 24)),
    "D": dict(cpus="16-31", threads=16, core="ECORE_ONLY", port=8244, cpu_list=range(16, 32)),
}
ORDER = "ABCDDCBA"
FLAGS = ("--enable_prefix_caching", "--cache_size", "--max_num_seqs", "--plugin_config",
         "--source_model", "--model_repository_path", "--target_device", "--rest_port")
T0 = time.monotonic()


def left():
    return DEADLINE_S - (time.monotonic() - T0)


def sh(*cmd, check=True, timeout=120):
    return subprocess.run(cmd, check=check, capture_output=True, text=True, timeout=timeout)


def name(arm):
    return f"sci-h24-{arm}"


# ------------------------------------------------------------- host readings
def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def mem_available_gb():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1e6
    return 0.0


def vmstat():
    out = {}
    for line in open("/proc/vmstat"):
        k, v = line.split()
        if k in ("pswpin", "pswpout"):
            out[k] = int(v)
    return out


def cpu_times():
    t = {}
    for line in open("/proc/stat"):
        if line.startswith("cpu") and line[3].isdigit():
            f = line.split()
            vals = list(map(int, f[1:9]))
            t[int(f[0][3:])] = (sum(vals) - vals[3] - vals[4], sum(vals))
    return t


def busy(t0, t1, cpus):
    b = sum(t1[c][0] - t0[c][0] for c in cpus)
    a = sum(t1[c][1] - t0[c][1] for c in cpus)
    return b / a if a else 0.0


def rapl_uj():
    # energy_uj is root-only: load.py reads it directly when allowed, else via sudo for that read only.
    return loadtool.read_energy_uj("/sys/class/powercap/intel-rapl:0") or 0


def rapl_max_uj():
    return int(read("/sys/class/powercap/intel-rapl:0/max_energy_range_uj", "0") or 0)


def rapl_delta_uj(e0, e1, top):
    """Counter delta; energy_uj wraps at max_energy_range_uj."""
    return e1 - e0 if e1 >= e0 or not top else e1 + top - e0


def throttle_count():
    # One package counter, exposed identically under every cpu; read it once.
    return int(read("/sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_count", "0") or 0)


def coretemp_max_c():
    vals = [int(read(p, "0") or 0) for p in glob.glob("/sys/class/hwmon/hwmon*/temp*_input")
            if "coretemp" in (read(os.path.join(os.path.dirname(p), "name"), "") or "")]
    return max(vals) / 1000 if vals else None


def freq_mhz(cpus):
    v = [int(read(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq", "0") or 0)
         for c in cpus]
    return statistics.mean(v) / 1000 if v else None


def container_usage_usec(arm):
    try:
        cid = sh("docker", "inspect", "--format", "{{.Id}}", name(arm)).stdout.strip()
    except subprocess.SubprocessError:
        return None
    for p in (f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/cpu.stat",
              f"/sys/fs/cgroup/docker/{cid}/cpu.stat"):
        txt = read(p)
        if txt:
            for line in txt.splitlines():
                if line.startswith("usage_usec"):
                    return int(line.split()[1])
    return None


# ------------------------------------------------------------- topology check
def topology_ok():
    """cpus 0-15 are P-core threads, 16-31 E-cores, and C's cpuset is whole clusters."""
    p = read("/sys/devices/cpu_core/cpus")
    e = read("/sys/devices/cpu_atom/cpus")
    if p != "0-15" or e != "16-31":
        return f"expected cpu_core=0-15 cpu_atom=16-31, got {p} / {e}"
    clusters = {read(f"/sys/devices/system/cpu/cpu{c}/topology/cluster_cpus_list") for c in range(16, 24)}
    if clusters != {"16-19", "20-23"}:
        return f"cpus 16-23 are not two whole E-clusters: {sorted(map(str, clusters))}"
    for c in range(0, 8, 2):
        if read(f"/sys/devices/system/cpu/cpu{c}/topology/thread_siblings_list") != f"{c}-{c + 1}":
            return f"cpu{c} siblings are not {c}-{c + 1}"
    return None


# ------------------------------------------------------------- OVMS
def launch(image, models, arm):
    # One repository per arm: --source_model rewrites graph.pbtxt (it carries
    # plugin_config) on every start, so a shared mount races between arms.
    a = ARMS[arm]
    plugin = json.dumps({"SCHEDULING_CORE_TYPE": a["core"], "INFERENCE_NUM_THREADS": str(a["threads"])})
    return ["docker", "run", "-d", "--name", name(arm), "--cpuset-cpus", a["cpus"],
            "-p", f"127.0.0.1:{a['port']}:8000", "-v", f"{models}/{arm}:/models", image,
            "--source_model", SOURCE_MODEL, "--model_repository_path", "/models",
            "--model_name", MODEL, "--task", "text_generation", "--target_device", "CPU",
            "--rest_port", "8000", "--max_num_seqs", "8", "--cache_size", "2",
            "--enable_prefix_caching", "false", "--plugin_config", plugin]


def graph_plugin_config(pbtxt):
    """plugin_config dict from an OVMS graph.pbtxt, or None."""
    for ln in pbtxt.splitlines():
        ln = ln.strip()
        if ln.startswith("plugin_config:"):
            return json.loads(ln.split("'")[1])
    return None


def chat(port, prompt, max_tokens, ignore_eos=True):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0, "stream": True,
            "stream_options": {"include_usage": True}}
    if ignore_eos:
        body["ignore_eos"] = True
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v3/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    ttft = t_last = usage = None
    chunks = 0
    with urllib.request.urlopen(req, timeout=max(30, left())) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            ev = json.loads(line[5:])
            if ev.get("usage"):
                usage = ev["usage"]
            for ch in ev.get("choices", []):
                d = ch.get("delta") or {}
                # OVMS streams Qwen3 thinking as reasoning_content, not content.
                if d.get("content") or d.get("reasoning_content"):
                    now = time.perf_counter()
                    ttft = ttft if ttft is not None else now - t0
                    t_last = now
                    chunks += 1
    ctoks = (usage or {}).get("completion_tokens", chunks)
    dec_s = (t_last - t0 - ttft) if (ttft is not None and t_last) else None
    return {"ttft_s": ttft, "e2e_s": time.perf_counter() - t0, "completion_tokens": ctoks,
            "prompt_tokens": (usage or {}).get("prompt_tokens"),
            "cached_tokens": ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens"),
            "decode_tok_s": (ctoks - 1) / dec_s if dec_s and ctoks > 1 else None}


def nonce():
    return secrets.token_hex(8)


def decode_prompt():
    return f"{nonce()} Write a long story about a lighthouse keeper and the sea."


def prefill_prompt():
    words = " ".join(f"w{secrets.randbelow(10**6)}" for _ in range(72))  # ~7 tokens per word -> ~512
    return f"{nonce()} Summarise these tokens in one word: {words}"


def wait_ready(arms):
    end = time.monotonic() + READY_S
    pending = set(arms)
    while pending and time.monotonic() < end and left() > 0:
        for arm in sorted(pending):
            try:
                chat(ARMS[arm]["port"], "warmup", 4, False)
                pending.discard(arm)
            except Exception:  # noqa: BLE001 - not ready yet
                pass
        time.sleep(3)
    return pending


# ------------------------------------------------------------- visit
def visit(arm, idx, level=None, load_window_s=10.0, load_cpus=None):
    a = ARMS[arm]
    port, cpus = a["port"], list(a["cpu_list"])
    rec = {"arm": arm, "visit": idx, "t": time.time(), "level": level}
    waited = time.monotonic()
    if level:
        # A1: one load.py window with the arm idle, tagged with the arm; no idle wait.
        s0 = cpu_times()
        rec["load"] = loadtool.window(load_window_s, load_cpus or loadtool.load_cpus(), tag=arm, level=level)
        rec["idle_busy"] = busy(s0, cpu_times(), cpus)
    else:
        # Wait (up to IDLE_WAIT_S) for the arm cpuset to go quiet; flag if it never does.
        while True:
            s0 = cpu_times()
            time.sleep(5)
            s1 = cpu_times()
            rec["idle_busy"] = busy(s0, s1, cpus)
            if rec["idle_busy"] <= 0.10 or time.monotonic() - waited >= IDLE_WAIT_S or left() < 200:
                break
    rec["idle_wait_s"] = round(time.monotonic() - waited, 1)
    rec["flag_busy"] = rec["idle_busy"] > 0.10
    others = {o: container_usage_usec(o) for o in ARMS if o != arm}
    v0, th0, e0, w0 = vmstat(), throttle_count(), rapl_uj(), time.monotonic()
    freqs, temps = [], []
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            freqs.append(freq_mhz(cpus))
            temps.append(coretemp_max_c())
            stop.wait(1.0)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    s2 = cpu_times()
    samples = []
    try:
        for _ in range(3):
            samples.append(("decode", chat(port, decode_prompt(), 128)))
        for _ in range(2):
            samples.append(("prefill", chat(port, prefill_prompt(), 16)))
        res, t0 = [None] * 4, time.perf_counter()

        def one(i):
            res[i] = chat(port, decode_prompt(), 128)

        ts = [threading.Thread(target=one, args=(i,)) for i in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        wall = time.perf_counter() - t0
        toks = sum(r["completion_tokens"] for r in res if r)
        samples.append(("c4", {"agg_tok_s": toks / wall, "completion_tokens": toks,
                               "requests": res}))
        rec["error"] = None
    except Exception as exc:  # noqa: BLE001 - a failed visit is void and counted
        rec["error"] = repr(exc)
    finally:
        stop.set()
        th.join()
    s3 = cpu_times()
    v1 = vmstat()
    rec["samples"] = samples
    rec["arm_busy"] = busy(s2, s3, cpus)
    rec["rest_busy"] = busy(s2, s3, [c for c in s3 if c not in cpus])
    rec["throttle_delta"] = throttle_count() - th0
    secs = time.monotonic() - w0
    rec["package_w"] = rapl_delta_uj(e0, rapl_uj(), rapl_max_uj()) / 1e6 / secs if secs > 0 else None
    rec["freq_mhz_mean"] = statistics.mean([f for f in freqs if f]) if any(freqs) else None
    rec["coretemp_max_c"] = max([t for t in temps if t], default=None)
    rec["pswpin_delta"] = v1.get("pswpin", 0) - v0.get("pswpin", 0)
    rec["flag_swap"] = rec["pswpin_delta"] > 1000
    rec["idle_other_usage_core_frac"] = {
        o: ((container_usage_usec(o) or 0) - u) / 1e6 / secs if u is not None else None
        for o, u in others.items()}
    rec["void"] = bool(rec["error"]) or any(
        (k == "decode" and s["completion_tokens"] != 128) or (k == "c4" and s["completion_tokens"] != 512)
        or (k != "c4" and s.get("cached_tokens"))
        for k, s in samples)
    return rec


def triad(cpus, threads, binary):
    out = subprocess.run(["taskset", "-c", cpus, binary], capture_output=True, text=True,
                         timeout=60, env=dict(os.environ, OMP_NUM_THREADS=str(threads)))
    parts = out.stdout.split("triad_best_GBs")
    return float(parts[1].split()[0]) if len(parts) > 1 else None


# ------------------------------------------------------------- score
def score(path):
    with open(path) as f:
        rows = [json.loads(line) for line in f]
    head = next((r for r in rows if r.get("kind") == "header"), {})
    visits = [r for r in rows if r.get("kind") == "visit" and not r.get("void")]
    triads = {}
    for r in rows:
        if r.get("kind") == "triad" and r.get("gbs"):
            triads.setdefault(r["arm"], []).append(r["gbs"])
    pred = sku_model.h24_arms()
    meas = {}
    for arm in ARMS:
        vs = [v for v in visits if v["arm"] == arm]
        d = [s["decode_tok_s"] for v in vs for k, s in v["samples"] if k == "decode" and s["decode_tok_s"]]
        p = [s["prompt_tokens"] / s["ttft_s"] for v in vs for k, s in v["samples"]
             if k == "prefill" and s.get("prompt_tokens") and s.get("ttft_s")]
        c = [s["agg_tok_s"] for v in vs for k, s in v["samples"] if k == "c4"]
        meas[arm] = {m: (statistics.median(x) if x else None,
                         (max(x) - min(x)) / statistics.median(x) if len(x) > 1 else None, len(x))
                     for m, x in (("decode_c1", d), ("prefill", p), ("c4_agg", c))}
        # A1: under matched load busy and throttle are recorded, not gates.
        meas[arm]["flagged_visits"] = sum(1 for v in vs if v["flag_swap"] or (
            not v.get("load") and (v["flag_busy"] or v["throttle_delta"] > 0)))
    # Calibration from this run's arm A (pre-registered), not from H6.
    k = {m: (meas["A"][m][0] / pred["A"][m]) if meas["A"][m][0] else None
         for m in ("decode_c1", "prefill", "c4_agg")}
    out = {"header": head, "cells": [], "triad_gbs": {a: statistics.median(v) for a, v in triads.items()}}
    errs = []
    for arm in "BCD":
        for m in ("decode_c1", "prefill", "c4_agg"):
            med, spread, n = meas[arm][m]
            if med is None or k[m] is None:
                out["cells"].append({"arm": arm, "metric": m, "n": n, "measured": None})
                continue
            want = pred[arm][m] * k[m]
            e = (med - want) / want
            errs.append(abs(e))
            out["cells"].append({"arm": arm, "metric": m, "n": n, "measured": round(med, 2),
                                 "m0": round(want, 2), "err": round(e, 3),
                                 "spread": round(spread, 3) if spread is not None else None})
    # M1 decode: 15.7-equivalent scaled by triad(arm)/triad(A), min with spec.
    m1 = {}
    ta = out["triad_gbs"].get("A")
    if ta and meas["A"]["decode_c1"][0]:
        for arm in "BCD":
            tb = out["triad_gbs"].get(arm)
            med = meas[arm]["decode_c1"][0]
            if tb and med:
                want = meas["A"]["decode_c1"][0] * min(tb, 57.6) / min(ta, 57.6)
                m1[arm] = {"measured": round(med, 2), "m1": round(want, 2),
                           "err": round((med - want) / want, 3)}
    out["m1_decode"] = m1
    # Baseline (pre-registered): the uncalibrated efficiency-1 roofline, scored on
    # absolute decode with no rescale, next to M0 absolute (H6 calibration, no rescale).
    bw = sku_model.bw_spec(sku_model.CALIB["host_mts"], sku_model.CALIB["host_channels"])
    base = sku_model.uncalibrated_roofline(bw)
    out["absolute_decode"] = {}
    for arm in "ABCD":
        med = meas[arm]["decode_c1"][0]
        if med:
            m0a = pred[arm]["decode_c1"]
            out["absolute_decode"][arm] = {
                "measured": round(med, 2), "m0_abs": m0a, "m0_abs_err": round((med - m0a) / m0a, 3),
                "baseline": round(base, 2), "baseline_err": round((med - base) / base, 3)}
    ab = [v for k_, v in out["absolute_decode"].items() if k_ != "A"]
    out["absolute_decode_mape"] = (
        {"m0": round(statistics.mean(abs(v["m0_abs_err"]) for v in ab), 3),
         "baseline": round(statistics.mean(abs(v["baseline_err"]) for v in ab), 3)} if ab else None)
    a_drift = None
    av = [v for v in visits if v["arm"] == "A"]
    if len(av) >= 2:
        f = [statistics.median([s["decode_tok_s"] for kk, s in v["samples"] if kk == "decode" and s["decode_tok_s"]] or [0])
             for v in (av[0], av[-1])]
        a_drift = abs(f[1] - f[0]) / f[0] if f[0] else None
    out["a_vs_h6"] = (meas["A"]["decode_c1"][0] / sku_model.CALIB["cpu_decode_tok_s"] - 1
                      if meas["A"]["decode_c1"][0] else None)
    out["a_drift"] = a_drift
    out["mape"] = round(statistics.mean(errs), 3) if errs else None
    windows = [v["load"] for v in visits if v.get("load")]
    out["load"] = loadtool.matched(windows) if windows else None
    out["verdict"] = verdict(out, meas)
    if out["load"] and not out["load"]["matched"]:
        out["verdict_if_matched"] = out["verdict"]
        out["verdict"] = out["load"]["verdict"]
    print(json.dumps(out, indent=1))
    return out


def verdict(out, meas):
    """Pre-registered outcome table (EXPERIMENTS.md, H24) -> label."""
    cells = [c for c in out["cells"] if c.get("measured") is not None]
    if len(cells) < 9:
        return "INCONCLUSIVE (missing cells: partial run)"
    spreads = [meas[a][m][1] for a in ARMS for m in ("decode_c1", "prefill", "c4_agg")
               if meas[a][m][1] is not None]
    if out["a_drift"] is None:
        return "INCONCLUSIVE (row 6: no drift sentinel, A measured once)"
    if out["a_drift"] > 0.10 or any(s > 0.20 for s in spreads) or any(
            meas[a]["flagged_visits"] >= 2 for a in ARMS):
        return "INCONCLUSIVE (row 6: noise floor)"
    if not any(abs(c["err"]) > 0.25 for c in cells) and out["mape"] <= 0.25:
        return "SUPPORTED (row 1)"
    dec = [c for c in cells if c["metric"] == "decode_c1"]
    pre = [c for c in cells if c["metric"] == "prefill"]
    c4 = [c for c in cells if c["metric"] == "c4_agg"]
    # Row 2 requires a failing decode gap to exceed 2x that arm's spread.
    dec_any = any(abs(c["err"]) > 0.25 for c in dec)
    dec_fail = any(abs(c["err"]) > 0.25 and abs(c["err"]) > 2 * (c["spread"] or 0) for c in dec)
    pre_fail = any(abs(c["err"]) > 0.25 for c in pre)
    c4_fail = {c["arm"] for c in c4 if abs(c["err"]) > 0.25}
    m1 = out["m1_decode"]
    m1_ok = len(m1) == 3 and all(abs(v["err"]) <= 0.20 for v in m1.values())
    if dec_fail and m1_ok and not pre_fail:
        return "REFUTED (row 2: M1 holds)"
    if dec_any and not m1_ok:
        return "REFUTED (row 3: M0 and M1 decode fail)"
    if pre_fail and not dec_any:
        return "REFUTED (row 4: prefill does not transfer)"
    if not dec_any and not pre_fail and c4_fail == {"C"}:
        return "REFUTED (row 5: c4 fails only on C)"
    return "REFUTED (mixed: no pre-registered row)"


# ------------------------------------------------------------- main
def preflight(image):
    if shutil.which("k3d"):
        r = sh("k3d", "cluster", "list", "-o", "json", check=False)
        try:
            busy_clusters = [c["name"] for c in json.loads(r.stdout or "[]") if c["name"].startswith("k3dgpu-f-")]
        except ValueError:
            busy_clusters = []
        if busy_clusters:
            return f"k3d clusters {busy_clusters} exist; wait for them"
    err = topology_ok()
    if err:
        return err
    helptxt = sh("docker", "run", "--rm", image, "--help", check=False, timeout=120).stdout
    missing = [f for f in FLAGS if f not in helptxt]
    if missing:
        return f"OVMS image lacks flags {missing}"
    if sh("docker", "ps", "-aq", "--filter", "name=^sci-h24-", check=False).stdout.strip():
        return "sci-h24-* containers already exist; remove them first"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--score")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--level", choices=loadtool.LEVELS,
                    help="A1: load.py level the caller holds (recorded; one load window per visit)")
    ap.add_argument("--load-window-s", type=float, default=10.0)
    ap.add_argument("--load-cpus", help="load.py window CPUs (default: load.py load_cpus())")
    a = ap.parse_args()
    # SIGTERM (outer timeout) -> SystemExit, so the `finally` below removes sci-h24-*.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    if a.score:
        score(a.score)
        return 0
    if a.dry_run:
        print(json.dumps({"order": ORDER, "arms": {k: {kk: (list(v) if kk == "cpu_list" else v)
                                                       for kk, v in d.items()} for k, d in ARMS.items()},
                          "predictions_m0": sku_model.h24_arms(), "deadline_s": DEADLINE_S,
                          "launch": [launch("$IMAGE", "$MODELS", k) for k in ARMS]}, indent=1))
        return 0
    if not a.out:
        ap.error("--out is required")
    image, models = os.environ.get("IMAGE"), os.environ.get("MODELS")
    if not image or not models:
        ap.error("set IMAGE (OVMS 2026.4.1 by digest) and MODELS")
    os.makedirs(a.out, exist_ok=True)
    raw = open(os.path.join(a.out, "raw.jsonl"), "a")

    def emit(rec):
        raw.write(json.dumps(rec) + "\n")
        raw.flush()

    err = preflight(image)
    if err:
        emit({"kind": "abort", "reason": err})
        print(f"probe: abort: {err}", file=sys.stderr)
        return 2
    emit({"kind": "header", "image": sh("docker", "image", "inspect", "--format",
                                         "{{index .RepoDigests 0}}", image, check=False).stdout.strip(),
          "kernel": os.uname().release, "start": time.time(),
          "cpu": next((ln.split(":", 1)[1].strip() for ln in open("/proc/cpuinfo")
                       if ln.startswith("model name")), None),
          "predictions_m0": sku_model.h24_arms(), "level": a.level})
    try:
        for arm in ARMS:
            sh(*launch(image, models, arm))
        pending = wait_ready(ARMS)
        if pending:
            emit({"kind": "abort", "reason": f"not ready in {READY_S}s: {sorted(pending)}"})
            return 3
        for arm in ARMS:
            cfg = graph_plugin_config(read(os.path.join(models, arm, SOURCE_MODEL, "graph.pbtxt"), ""))
            emit({"kind": "startup_log", "arm": arm,
                  "log": sh("docker", "logs", name(arm), check=False).stderr[-4000:],
                  "graph_plugin_config": cfg})
            if cfg != json.loads(launch(image, models, arm)[-1]):
                emit({"kind": "abort", "reason": f"arm {arm} graph plugin_config {cfg}"})
                return 3
        if mem_available_gb() < 20:
            emit({"kind": "abort", "reason": f"MemAvailable {mem_available_gb():.1f} GB < 20"})
            return 3
        src = os.path.join(REPO, "research", "agents-per-box-ceiling", "stream_probe.c")
        binary = os.path.join(a.out, "stream_probe")
        if shutil.which("gcc") and sh("gcc", "-O3", "-march=native", "-fopenmp", src, "-o", binary,
                                      check=False).returncode == 0:
            for rep in range(2):
                for arm, d in ARMS.items():
                    emit({"kind": "triad", "arm": arm, "rep": rep,
                          "gbs": triad(d["cpus"], d["threads"], binary)})
        else:
            emit({"kind": "triad_skipped", "reason": "gcc or build failed"})
        for i, arm in enumerate(ORDER):
            if left() < 150:
                emit({"kind": "stopped", "reason": "deadline", "visits_done": i})
                break
            if mem_available_gb() < 16:
                emit({"kind": "abort", "reason": "MemAvailable < 16 GB"})
                break
            t = coretemp_max_c()
            if t is not None and t >= 100:
                emit({"kind": "visit_skipped", "arm": arm, "reason": f"coretemp {t}"})
                continue
            emit(dict(visit(arm, i, a.level, a.load_window_s,
                            loadtool.parse_cpulist(a.load_cpus) if a.load_cpus else None), kind="visit"))
    finally:
        for arm in ARMS:
            sh("docker", "rm", "-f", name(arm), check=False)
        emit({"kind": "end", "elapsed_s": time.monotonic() - T0})
        raw.close()
    score(os.path.join(a.out, "raw.jsonl"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
