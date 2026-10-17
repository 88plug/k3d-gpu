#!/usr/bin/env python3
"""project.py: build a capacity-oracle bundle by allowlist projection.

This file is the authority on what a bundle can contain. Each field is copied
out of `k3d-gpu report --json`, a `k3d-gpu capacity` run, a few sysfs/procfs
files or single-stream bench files, one known-safe field at a time. Nothing is
redacted out of a larger blob, and no input key is ever copied as a map.
oracle/schema/bundle-v1.json closes every object and pins every string to an
enum or a pattern, so a value that is not on the allowlist cannot pass V1.

Never copied: hostname, generated_at, the cluster section (names, contexts,
labels), PCI addresses, card/render/accel node names, CPU lists, firmware
paths, RAPL zone names, run directories, URLs, namespaces, service names,
loadavg, start/finish times, request content and session ids.

  project.py capacity  --report R.json --capacity RUN/capacity.json [options]
  project.py single-stream --report R.json --raw DIR --label L --engine E [options]

Prints the sealed bundle (canonical content hash filled in) as JSON.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bundle as B  # noqa: E402

DEFAULT_MODEL = "OpenVINO/Qwen3-4B-int4-ov"
STD_GIB = (4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024, 1536, 2048,
           3072, 4096, 6144, 8192, 12288, 16384)
EPP = ("default", "performance", "balance_performance", "balance_power", "power")
PROFILES = ("low-power", "cool", "quiet", "balanced", "balanced-performance", "performance", "custom")
ENGINE_CLASS = {"rcs": "render", "bcs": "copy", "ccs": "compute", "vcs": "video", "vecs": "video_enhance"}
CPU_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ()@.,+_-]{0,95}$")
PLATFORM_RE = re.compile(r"^[A-Z][A-Za-z0-9 /-]{1,31}$")
FW_RE = re.compile(r"^[0-9]{1,3}(\.[0-9]{1,4}){1,3}$")
PCI_ID_RE = re.compile(r"^0x[0-9a-f]{4}$")
NV_MODEL_RE = re.compile(r"^NVIDIA [A-Za-z0-9 ()._-]{1,80}$")
ISA_RE = re.compile(r"^(avx[a-z0-9_]{0,20}|fma|f16c)$")
AMX_RE = re.compile(r"^amx_[a-z0-9_]{1,16}$")
DIGEST_IMAGE_RE = re.compile(r"^[a-z0-9]+([._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$")
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
STEP_FILE_RE = re.compile(r"^n(\d{3})-r(\d+)\.jsonl$")


class ProjectionError(Exception):
    pass


# ---------------------------------------------------------------- small readers
def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _first_line(path):
    v = _read(path)
    return None if v is None else (v.splitlines() or [""])[0].strip()


def _pick(value, rx):
    """value if it is a str matching rx, else None."""
    return value if isinstance(value, str) and rx.match(value) else None


def _int(v):
    try:
        return int(v, 0) if isinstance(v, str) else (int(v) if v is not None else None)
    except (TypeError, ValueError):
        return None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _us(seconds):
    v = _num(seconds)
    return None if v is None or v < 0 else int(round(v * 1e6))


def std_gib(kib):
    """MemTotal (KiB, below the installed size) -> nearest standard size by ratio."""
    if not isinstance(kib, int) or kib <= 0:
        return None
    gib = kib / 1048576
    return min(STD_GIB, key=lambda s: abs(math.log(s / gib)))


def kernel_mm(release):
    m = re.match(r"^(\d{1,2})\.(\d{1,3})", release or "")
    return f"{m.group(1)}.{m.group(2)}" if m else None


# ---------------------------------------------------------------- hardware
def cpuinfo_facts(root):
    """family, model, stepping, microcode of the first CPU; socket count."""
    text = _read(f"{root}/proc/cpuinfo") or ""
    first = text.split("\n\n", 1)[0]
    kv = {}
    for line in first.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv.setdefault(k.strip(), v.strip())
    ids = {line.split(":", 1)[1].strip() for line in text.splitlines()
           if line.startswith("physical id") and ":" in line}
    if not ids:
        ids = {(_first_line(p) or "") for p in
               glob.glob(f"{root}/sys/devices/system/cpu/cpu[0-9]*/topology/physical_package_id")}
        ids.discard("")
    micro = kv.get("microcode", "").lower()
    return {
        "family": _int(kv.get("cpu family")), "model_id": _int(kv.get("model")),
        "stepping": _int(kv.get("stepping")),
        "microcode": micro if re.match(r"^0x[0-9a-f]{1,8}$", micro) else None,
        "sockets": len(ids) or None,
    }


def numa_nodes(root):
    """NUMA nodes that have CPUs (HBM/CXL memory-only nodes do not count)."""
    nodes = glob.glob(f"{root}/sys/devices/system/node/node[0-9]*")
    if not nodes:
        return None
    with_cpus = [n for n in nodes if (_first_line(f"{n}/cpulist") or "")]
    return len(with_cpus) or None


def engines_of(gpu):
    counts = {v: 0 for v in ENGINE_CLASS.values()}
    for e in gpu.get("engines") or []:
        m = re.match(r"^([a-z]+?)\d*$", str(e.get("name", "")))
        if m and m.group(1) in ENGINE_CLASS:
            counts[ENGINE_CLASS[m.group(1)]] += 1
    return counts


def fw_version(uc):
    return _pick((uc or {}).get("version"), FW_RE)


def hardware(report, root):
    host = report.get("host") or {}
    c = host.get("cpu") or {}
    ci = cpuinfo_facts(root)
    nn = numa_nodes(root)
    snc = None
    if nn and ci["sockets"] and nn > ci["sockets"] and nn % ci["sockets"] == 0 and 2 <= nn // ci["sockets"] <= 8:
        snc = nn // ci["sockets"]
    hybrid = c.get("hybrid") is True
    gpus = []
    for g in host.get("intel_gpus") or []:
        dev = _pick(g.get("device_id"), PCI_ID_RE)
        if dev is None:
            continue
        drv = g.get("driver") if g.get("driver") in ("i915", "xe") else None
        xmx = g.get("xmx") if isinstance(g.get("xmx"), bool) else None
        gpus.append({"device_id": dev, "platform": _pick(g.get("platform"), PLATFORM_RE), "driver": drv,
                     "xmx": xmx, "engines": engines_of(g), "guc": fw_version(g.get("guc")),
                     "huc": fw_version(g.get("huc"))})
    npus = [{"device_id": n["device_id"], "platform": _pick(n.get("platform"), PLATFORM_RE)}
            for n in host.get("npus") or [] if _pick(n.get("device_id"), PCI_ID_RE)]
    gd = host.get("gaudi") or {}
    qat = [{"device_id": q["device_id"], "vfs": max(0, _int(q.get("vfs")) or 0)}
           for q in host.get("qat") or [] if _pick(q.get("device_id"), PCI_ID_RE)]
    nv = host.get("nvidia") or {}
    m = re.match(r"^(\d{2,4})\.(\d{1,3})", nv.get("driver") or "")
    nv_gpus = [{"device_id": g["device_id"], "model": _pick(g.get("model"), NV_MODEL_RE)}
               for g in nv.get("gpus") or [] if _pick(g.get("device_id"), PCI_ID_RE)]

    def idxd(k):
        d = host.get(k) or {}
        return {"devices": max(0, _int(d.get("devices")) or 0), "work_queues": max(0, _int(d.get("work_queues")) or 0)}

    def pos(v):
        v = _int(v)
        return v if v and v > 0 else None

    return {
        "cpu": {
            "model": _pick(c.get("model"), CPU_MODEL_RE), **{k: ci[k] for k in ("family", "model_id", "stepping", "microcode", "sockets")},
            "numa_nodes": nn, "snc": snc, "hybrid": hybrid,
            "p_core_cpus": pos(c.get("p_core_cpus")) if hybrid else None,
            "e_core_cpus": pos(c.get("e_core_cpus")) if hybrid else None,
            "logical_cpus": pos(c.get("logical_cpus")),
            "isa": sorted({x for x in c.get("isa") or [] if isinstance(x, str) and ISA_RE.match(x)}),
            "amx": sorted({x for x in c.get("amx") or [] if isinstance(x, str) and AMX_RE.match(x)}),
        },
        "memory": {"total_gib": std_gib((host.get("memory") or {}).get("total_kib")),
                   "type": None, "channels": None, "configured_mts": None},
        "intel_gpus": gpus, "npus": npus,
        "gaudi": {"devices": max(_int(gd.get("pci_devices")) or 0, _int(gd.get("accel_nodes")) or 0)},
        "dsa": idxd("dsa"), "iaa": idxd("iaa"), "qat": qat,
        "nvidia": {"driver": f"{m.group(1)}.{m.group(2)}" if m else None, "gpus": nv_gpus},
        "kernel": kernel_mm(host.get("kernel")),
    }


def power(root):
    """PL1/PL2 of the first package zone, EPP of cpu0 and the ACPI platform profile."""
    z = f"{root}/sys/class/powercap/intel-rapl:0"
    pl = {}
    for i in range(4):
        name = _first_line(f"{z}/constraint_{i}_name")
        uw = _int(_first_line(f"{z}/constraint_{i}_power_limit_uw"))
        if name in ("long_term", "short_term") and uw:
            w = int(round(uw / 1e6))
            if w > 0:
                pl[name] = w
    epp = _first_line(f"{root}/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")
    prof = _first_line(f"{root}/sys/firmware/acpi/platform_profile")
    return {"pl1_w": pl.get("long_term"), "pl2_w": pl.get("short_term"),
            "epp": epp if epp in EPP else None, "platform_profile": prof if prof in PROFILES else None}


def levers(hw, server, quant, overrides):
    """Lever entries the projection can evaluate from the box itself. The state
    is only known when the caller says so (--lever id=on|off)."""
    gpus = hw["intel_gpus"]
    detected = {
        "pe_pinning": hw["cpu"]["hybrid"],
        "xmx_gate": bool(gpus),
        "amx": bool(hw["cpu"]["amx"]),
        "numa_replica": (hw["cpu"]["numa_nodes"] or 1) > 1,
        "openvino_int4": server == "ovms" and quant == "int4",
        "npu_offload": bool(hw["npus"]),
        "media_engines": any(g["engines"]["video"] > 0 for g in gpus),
        "qat": bool(hw["qat"]),
        "iaa_zswap": hw["iaa"]["devices"] > 0,
        "dsa_dto": hw["dsa"]["devices"] > 0,
    }
    for k in overrides:
        if k not in detected:
            raise ProjectionError(f"lever {k} is not one the projection evaluates: {sorted(detected)}")
    out = []
    for k in sorted(detected):
        state = overrides.get(k, "on" if k == "openvino_int4" and detected[k] else "unknown")
        out.append({"id": k, "detected": bool(detected[k]), "state": state})
    return out


# ---------------------------------------------------------------- workload pieces
def quant_of(model_id):
    low = model_id.lower()
    for q in ("int4", "int8", "fp8", "fp16", "bf16", "fp32"):
        if q in low:
            return q
    return "unknown"


def thinking_of(extra):
    """The only extra_body the bundle can carry is the Qwen3 thinking toggle."""
    if not extra:
        return "default"
    if set(extra) == {"chat_template_kwargs"} and set(extra["chat_template_kwargs"] or {}) == {"enable_thinking"}:
        return "on" if extra["chat_template_kwargs"]["enable_thinking"] else "off"
    raise ProjectionError("extra_body other than chat_template_kwargs.enable_thinking cannot be represented in a bundle")


def model_block(model_id, revision, weights_sha256, weights_bytes, quant):
    if not MODEL_ID_RE.match(model_id or ""):
        raise ProjectionError(f"model id {model_id!r} is not an org/name Hugging Face id (use --model-id)")
    if revision is not None and not re.match(r"^[0-9a-f]{40}$", revision):
        raise ProjectionError("--model-revision must be a 40-hex commit")
    if weights_sha256 is not None and not re.match(r"^[0-9a-f]{64}$", weights_sha256):
        raise ProjectionError("--weights-sha256 must be 64 hex")
    return {"id": model_id, "revision": revision, "weights_sha256": weights_sha256,
            "weights_bytes": weights_bytes, "quant": quant or quant_of(model_id)}


def tool_block(version, server_image, k3s=None):
    if not re.match(r"^([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|git-[0-9a-f]{40})$", version or ""):
        raise ProjectionError(f"tool version {version!r} is neither a release (x.y.z) nor git-<sha>")
    return {"version": version, "k3s": k3s,
            "server_image": server_image if DIGEST_IMAGE_RE.match(server_image or "")
        and B.public_image(server_image) else None,
            "node_image_digest": None}


# ---------------------------------------------------------------- capacity
def rep_columns(rows):
    """Rows of one rep (agent_trace records) -> column dict. Sessions are
    renumbered by first appearance; their names never leave the box."""
    rows = sorted(rows, key=lambda r: (r["sent_at_s"], str(r["session"]), r.get("turn", 0)))
    ids = {}
    cols = {k: [] for k in ("session", "sent_us", "e2e_us", "ttft_us", "out_tok", "err", "expect_tool", "tool_valid")}
    for r in rows:
        cols["session"].append(ids.setdefault(str(r["session"]), len(ids)))
        cols["sent_us"].append(_us(r["sent_at_s"]))
        cols["e2e_us"].append(_us(r.get("e2e_s")))
        cols["ttft_us"].append(_us(r.get("ttft_s")))
        ct = r.get("completion_tokens")
        cols["out_tok"].append(ct if isinstance(ct, int) and ct >= 0 else None)
        cols["err"].append(1 if r.get("error") else 0)
        cols["expect_tool"].append(1 if r.get("expect_tool") else 0)
        cols["tool_valid"].append(1 if r.get("tool_valid") else 0)
    return cols


def load_step_reps(run_dir):
    """{agents: [rows per rep, in rep order]} from RUN/steps/nNNN-rR.jsonl (not warmup/drift)."""
    out = {}
    for path in sorted(glob.glob(os.path.join(run_dir, "steps", "n*-r*.jsonl"))):
        m = STEP_FILE_RE.match(os.path.basename(path))
        if not m:
            continue
        with open(path) as f:
            rows = [json.loads(line) for line in f if line.strip()]
        out.setdefault(int(m.group(1)), []).append((int(m.group(2)), rows))
    return {n: [rows for _r, rows in sorted(v)] for n, v in out.items()}


def capacity_results(cap, run_dir):
    if cap.get("schema") != "k3d-gpu.capacity/1":
        raise ProjectionError("not a k3d-gpu capacity report (schema k3d-gpu.capacity/1)")
    if cap.get("status") != "ok":
        raise ProjectionError(f"capacity run status is {cap.get('status')!r}, not 'ok': nothing to share")
    reps = load_step_reps(run_dir)
    steps = []
    for s in cap["steps"]:
        n = s["agents"]
        if n not in reps:
            raise ProjectionError(f"steps/n{n:03d}-r*.jsonl missing next to capacity.json")
        hw = s.get("hardware") or {}
        en = s.get("energy") or {}
        busy = _num(hw.get("cpu_busy_mean"))
        steps.append({
            "agents": n,
            "claimed_p95_us": _us(s.get("p95_s")),
            "claimed_verdict": s["verdict"],
            "j_per_turn_pkg": _num((s.get("j_per_turn") or {}).get("cpu_package")),
            "pkg_w_mean": _num(hw.get("pkg_w_mean")),
            "energy_seconds": _num(en.get("seconds")),
            "energy_turns": _int(en.get("turns")),
            "throttle_events": _int(hw.get("package_throttle_events")),
            "cpu_busy_mean": None if busy is None else min(1.0, max(0.0, busy)),
            "reps": [rep_columns(rows) for rows in reps[n]],
        })
    idle = cap.get("idle") or {}
    drift = (cap.get("drift_check") or {}).get("ratio")
    return {
        "agents_per_box": int(cap["agents_per_box"]),
        "binding_limit": (cap.get("binding_limit") or {}).get("kind") or "ramp_incomplete",
        "idle_pkg_w": _num(idle.get("pkg_watts")),
        "drift_ratio": _num(drift),
        "quiet_check_ok": bool((cap.get("quiet_check") or {}).get("ok")),
        "steps": steps,
    }


def served_model_id(served):
    """Only the default model is named without --model-id: any other served
    name can be a private org, user or path, so the submitter types it."""
    if served in (DEFAULT_MODEL, DEFAULT_MODEL.split("/")[-1]):
        return DEFAULT_MODEL
    return None


def project_capacity(report, cap, run_dir, root, *, version=None, server_image=None, model_id=None,
                     revision=None, weights_sha256=None, weights_bytes=None, quant=None,
                     lever_states=None, source="community", note="none", ab=None):
    ep = cap.get("endpoint") or {}
    mid = model_id or served_model_id(ep.get("model"))
    if mid is None:
        raise ProjectionError(f"served model {ep.get('model')!r} is not the default model: pass --model-id "
                              "with its public Hugging Face org/name")
    model = model_block(mid, revision, weights_sha256, weights_bytes, quant)
    engine = ep.get("engine") if ep.get("engine") in ("cpu", "igpu", "npu", "gaudi", "nvidia") else "unspecified"
    server = ep.get("server") if ep.get("server") in ("ovms", "vllm") else "unknown"
    hw = hardware(report, root)
    b = {
        "schema": B.SCHEMA_ID, "kind": "capacity",
        "week": B.iso_week(_num(cap.get("started_at"))),
        "content_sha256": "0" * 64, "client_signature": {"kind": "none"},
        "provenance": {"source": source, "note": note},
        "tool": tool_block(version or report.get("k3d_gpu_version"), server_image or ep.get("image")),
        "comparison_key": "0" * 64,
        "workload": {"protocol": "apb-v1", "trace": cap.get("trace"), "slo_s": _num(cap.get("slo_s")),
                     "stop_slo_s": _num(cap.get("stop_slo_s")), "reps": _int(cap.get("reps")),
                     "max_agents": _int(cap.get("max_agents")), "time_scale": _num(cap.get("time_scale")),
                     "thinking": thinking_of(cap.get("extra_body")), "model": model, "single_stream": None},
        "hardware": hw, "power": power(root),
        "levers": levers(hw, server, model["quant"], lever_states or {}),
        "ab": ab,
        "results": {"engine": engine, "server": server,
                    "capacity": capacity_results(cap, run_dir), "single_stream": None},
    }
    return B.seal(b)


# ---------------------------------------------------------------- single stream
def ss_columns(files):
    """bench llm_load / H6 per-workload files, one per round -> column dict."""
    cols = {k: [] for k in ("round", "e2e_us", "ttft_us", "out_tok", "prompt_tok")}
    for rnd, path in files:
        with open(path) as f:
            d = json.load(f)
        for s in d["samples"]:
            if not s.get("ok"):
                continue
            cols["round"].append(rnd)
            cols["e2e_us"].append(_us(s.get("latency_s")))
            cols["ttft_us"].append(_us(s.get("ttft_s")))
            cols["out_tok"].append(_int(s.get("output_tokens")))
            cols["prompt_tok"].append(_int(s.get("prompt_tokens")))
    if not cols["round"]:
        raise ProjectionError("no successful requests in the single-stream files")
    return cols


def raw_rounds(raw_dir, label, workload):
    """[(round, path)] for LABEL-rR-WORKLOAD.json."""
    rx = re.compile(rf"^{re.escape(label)}-r(\d+)-{re.escape(workload)}\.json$")
    found = sorted((int(m.group(1)), os.path.join(raw_dir, f)) for f in os.listdir(raw_dir)
                   if (m := rx.match(f)))
    if not found:
        raise ProjectionError(f"no {label}-rN-{workload}.json files in {raw_dir}")
    return found


def project_single_stream(report, root, raw_dir, label, engine, *, version, server_image, k3s=None,
                          model_id=DEFAULT_MODEL, revision=None, weights_sha256=None, weights_bytes=None,
                          quant=None, week, decode_shape=(30, 256), prefill_shape=(1936, 16),
                          per_round=5, thinking="default", lever_states=None, source="community",
                          note="none", run_time_only=False):
    dec, pre = raw_rounds(raw_dir, label, "decode"), raw_rounds(raw_dir, label, "prefill")
    model = model_block(model_id, revision, weights_sha256, weights_bytes, quant)
    hw = hardware(report, root)
    pw = power(root)
    if run_time_only:
        # Facts that can drift between the run and the projection are left out.
        hw["cpu"]["microcode"] = None
        pw = {"pl1_w": None, "pl2_w": None, "epp": None, "platform_profile": None}
    b = {
        "schema": B.SCHEMA_ID, "kind": "single_stream", "week": week,
        "content_sha256": "0" * 64, "client_signature": {"kind": "none"},
        "provenance": {"source": source, "note": note},
        "tool": tool_block(version, server_image, k3s), "comparison_key": "0" * 64,
        "workload": {"protocol": "single-stream-v1", "trace": None, "slo_s": None, "stop_slo_s": None,
                     "reps": None, "max_agents": None, "time_scale": None, "thinking": thinking, "model": model,
                     "single_stream": {"rounds": len({r for r, _ in dec}), "per_round": per_round, "ignore_eos": True,
                                       "decode": {"prompt_tokens": decode_shape[0], "max_tokens": decode_shape[1]},
                                       "prefill": {"prompt_tokens": prefill_shape[0], "max_tokens": prefill_shape[1]}}},
        "hardware": hw, "power": pw,
        "levers": levers(hw, "ovms" if "model_server" in (server_image or "") else "unknown",
                         model["quant"], lever_states or {}),
        "ab": None,
        "results": {"engine": engine, "server": "ovms" if "model_server" in (server_image or "") else "unknown",
                    "capacity": None,
                    "single_stream": {"decode": ss_columns(dec), "prefill": ss_columns(pre)}},
    }
    return B.seal(b)


def median_of_round_medians(values, rounds):
    by = {}
    for v, r in zip(values, rounds):
        if v is not None:
            by.setdefault(r, []).append(v)
    return statistics.median(statistics.median(v) for _r, v in sorted(by.items())) if by else None


# ---------------------------------------------------------------- CLI
def parse_levers(items):
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        if v not in ("on", "off", "unknown"):
            raise ProjectionError(f"--lever {it}: want id=on|off|unknown")
        out[k] = v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="project.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("capacity", "single-stream"):
        p = sub.add_parser(name)
        p.add_argument("--report", required=True, help="k3d-gpu report --json output")
        p.add_argument("--root", default=os.environ.get("K3D_GPU_SYSROOT", ""), help="sysroot (default /)")
        p.add_argument("--version")
        p.add_argument("--server-image")
        p.add_argument("--model-id")
        p.add_argument("--model-revision")
        p.add_argument("--weights-sha256")
        p.add_argument("--quant")
        p.add_argument("--lever", action="append", help="id=on|off|unknown")
        p.add_argument("--provenance", default="community", choices=("community", "maintainer-lab"))
        p.add_argument("--note", default="none", choices=("none", "ledger-H6"))
    sub.choices["capacity"].add_argument("--capacity", required=True)
    s = sub.choices["single-stream"]
    s.add_argument("--raw", required=True)
    s.add_argument("--label", required=True)
    s.add_argument("--engine", required=True, choices=("cpu", "igpu", "npu", "gaudi", "nvidia"))
    s.add_argument("--k3s")
    s.add_argument("--week", required=True)
    s.add_argument("--run-time-only", action="store_true",
                   help="leave out microcode and power state (read now, not at run time)")
    a = ap.parse_args(argv)
    with open(a.report) as f:
        report = json.load(f)
    try:
        lv = parse_levers(a.lever)
        if a.cmd == "capacity":
            with open(a.capacity) as f:
                cap = json.load(f)
            b = project_capacity(report, cap, os.path.dirname(os.path.abspath(a.capacity)), a.root,
                                 version=a.version, server_image=a.server_image, model_id=a.model_id,
                                 revision=a.model_revision, weights_sha256=a.weights_sha256, quant=a.quant,
                                 lever_states=lv, source=a.provenance, note=a.note)
        else:
            b = project_single_stream(report, a.root, a.raw, a.label, a.engine, version=a.version,
                                      server_image=a.server_image, k3s=a.k3s,
                                      model_id=a.model_id or DEFAULT_MODEL, revision=a.model_revision,
                                      weights_sha256=a.weights_sha256, quant=a.quant, week=a.week,
                                      lever_states=lv, source=a.provenance, note=a.note,
                                      run_time_only=a.run_time_only)
    except ProjectionError as e:
        print(f"project: {e}", file=sys.stderr)
        return 2
    print(json.dumps(b, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
