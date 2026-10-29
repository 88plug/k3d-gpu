#!/usr/bin/env python3
"""plan(facts, matrix, overrides): engine, model, placement, port and auth mode.

A pure function: no I/O, no clock, inputs left unchanged. It picks only from
green matrix cells and never interpolates: a box with no green cell gets
"unmeasured: no green cell for this box; quickstart and agent up still pick by device" and no engine.

A cell is green when all three hold (docs/console-design.md, L2):
  - a k3d-gpu report run exists for it (evidence.report_id),
  - it passed the tool-calling eval (tool_eval.result == "pass"),
  - its engine image is the one this release pins (facts.release.pins).
A community cell is amber; anything else is grey, with the reasons. A green
cell is eligible when it matches a usable target of this host and no override
excludes it. Among eligible cells the highest median decode tok/s wins; ties go
to the smaller cell id.

A target the facts mark not usable carries a one-line fix (the launcher's
firmware gatekeeper refuses a GPU with a stale or failed GuC; a Gaudi without its
driver has no engine). Its cells are never eligible, so the pick falls back to
the next green cell on a usable target: OVMS on the CPU, or llama.cpp on Vulkan
on another GPU. Every such fix is listed. With no pick and a fix to apply, the
decision is "fix", not an engine.

The plan hash is "sha256:" plus the hex SHA-256 of the --json output bytes. A
write (`k3d-gpu apply`, the MCP apply tool) runs only when the hash it is given
equals the hash of the plan recomputed at that moment.

With --model-meta (a k3d-gpu.hfmeta/v1 bundle, console/model_opt.py fetch), the
plan also carries the model optimizer's decision for the engine and device the
plan picked, or, with no green cell, the ones `agent up` picks by device (a
Gaudi: vLLM; an Intel GPU with XMX: OVMS on the GPU; else OVMS on the CPU).

  plan.py --mode text|explain|json|hash --matrix FILE [--engine E] [--device D]
          [--model M] [--bind ADDR] [--port N] [--model-meta FILE] [--ledger FILE]
          [--measurements FILE] [--thinking off|on|model] [--variant ID] < facts.json

Python standard library only.
"""

import argparse
import hashlib
import ipaddress
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import facts as factsmod  # noqa: E402  (stdlib-only sibling modules)
import model_opt  # noqa: E402
import schema_check  # noqa: E402

PLAN_SCHEMA = "k3d-gpu.plan/v1"
UNMEASURED = "unmeasured: no green cell for this box; quickstart and agent up still pick by device"
NO_GREEN_LEFT = "no green cell left on a usable target: apply a fix above, then plan again"
DEFAULT_BIND = "127.0.0.1"
ENGINE_PORTS = {"ovms": 8000, "vllm": 8000, "llama.cpp": 8080}  # each server's own default port
DEVICES = ("cpu", "gpu", "npu", "hpu")
CPU_MATCH_KEYS = ("family_model", "hybrid", "amx")
AUTH_TEXT = {
    "loopback-key": "no login, gateway key in a 0600 file",
    "oidc": "OIDC login required: the listener is off loopback",
}


def same(a, b):
    """JSON equality that keeps true apart from 1."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def is_loopback(bind):
    if bind == "localhost":
        return True
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


def check_overrides(overrides):
    """A copy of overrides with only known keys and valid values; ValueError otherwise."""
    out = {}
    for key, value in (overrides or {}).items():
        if value is None:
            continue
        if key == "device":
            if value not in DEVICES:
                raise ValueError("device must be one of %s, got %r" % (", ".join(DEVICES), value))
        elif key == "port":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
                raise ValueError("port must be an integer 1-65535, got %r" % (value,))
        elif key in ("engine", "model", "bind"):
            if not isinstance(value, str) or not value:
                raise ValueError("%s must be a non-empty string" % key)
        else:
            raise ValueError("unknown override %r" % key)
        out[key] = value
    return out


def cell_status(row, pins):
    """("green" | "amber" | "grey", reasons)."""
    if row["source"] == "community":
        return "amber", ["community pack, not reviewed"]
    reasons = []
    if not row["evidence"]["report_id"]:
        reasons.append("no k3d-gpu report run recorded")
    te = row["tool_eval"]
    if te is None:
        reasons.append("tool-calling eval not run")
    elif te["result"] != "pass":
        reasons.append("tool-calling eval failed (%s)" % te["suite"])
    pin = pins.get(row["engine"]["name"])
    if pin is None:
        reasons.append("this release pins no %s image" % row["engine"]["name"])
    elif pin != row["engine"]["image"]:
        reasons.append("engine pin moved since the measurement (cell %s, release %s)" % (row["engine"]["image"], pin))
    return ("grey" if reasons else "green"), reasons


def matches(match, t):
    for key, want in match.items():
        if key in CPU_MATCH_KEYS:
            if t["cpu"] is None or not same(t["cpu"][key], want):
                return False
        elif not same(t.get(key), want):
            return False
    return True


def placement_fits(placement, t):
    if placement == "p-cores":
        return t["kind"] == "cpu" and t["cpu"]["hybrid"]
    if placement == "cpu":
        return t["kind"] == "cpu"
    return t["kind"] != "cpu"


def resolve_placement(kind, t):
    p = {"kind": kind, "target": t["id"], "resource": None, "pod_label": None, "cpus": None, "cpu_count": None}
    if kind == "p-cores":
        p.update(pod_label="k3d-gpu.io/cores=performance", cpus=t["cpu"]["p_cpus"], cpu_count=t["cpu"]["p_count"])
    elif kind == "device":
        p["resource"] = t["resource"]
    return p


def placement_text(p):
    if p["kind"] == "p-cores":
        return "P-cores %s (%s CPUs), pod label %s" % (p["cpus"], p["cpu_count"], p["pod_label"])
    if p["kind"] == "cpu":
        return "CPU (%s)" % p["target"]
    return "%s via %s" % (p["target"], p["resource"])


def metric_text(row):
    return "decode %s tok/s" % row["metrics"]["decode_tok_s"]["median"]


def evidence_text(row):
    ev = row["evidence"]
    parts = [] if row["source"] == "report" and ev["report_id"] else [row["source"]]
    if ev["ledger"]:
        parts.append(ev["ledger"])
    if ev["report_id"]:
        parts.append("report " + ev["report_id"])
    return " ".join(parts)


def plan(facts, matrix, overrides=None):
    """The plan/v1 document for this host. Pure: reads its arguments only."""
    ov = check_overrides(overrides)
    targets = facts["targets"]
    pins = facts["release"]["pins"]
    explain = ["facts: " + facts["fingerprint"], "targets:"]
    for t in targets:
        state = "usable" if t["usable"] else "not usable: " + t["reason"]
        explain.append("  %s: %s; %s" % (t["id"], factsmod.describe(t), state))
    fixes = [{"target": t["id"], "reason": t["reason"], "fix": t["fix"]}
             for t in targets if not t["usable"] and t["fix"]]
    fix_lines = ["%s refused: %s; fix: %s" % (f["target"], f["reason"], f["fix"]) for f in fixes]

    refused = {f["target"] for f in fixes}
    considered, eligible, shown = [], [], []
    green_refused = False  # a green cell fits a refused target: its fix would unlock it
    for row in sorted(matrix["rows"], key=lambda r: r["id"]):
        status, reasons = cell_status(row, pins)
        fits = [t for t in targets if matches(row["match"], t) and placement_fits(row["placement"], t)]
        usable = [t for t in fits if t["usable"]]
        green_refused = green_refused or (status == "green" and any(t["id"] in refused for t in fits))
        tgt = (usable or fits or [None])[0]
        excluded = None
        if tgt is None:
            excluded = "no target on this host matches"
        elif not usable:
            excluded = "target %s not usable: %s" % (tgt["id"], tgt["reason"])
        elif "engine" in ov and row["engine"]["name"] != ov["engine"]:
            excluded = "--engine %s" % ov["engine"]
        elif "device" in ov and tgt["kind"] != ov["device"]:
            excluded = "--device %s" % ov["device"]
        elif "model" in ov and row["model"]["name"] != ov["model"]:
            excluded = "--model %s" % ov["model"]
        ok = status == "green" and excluded is None
        considered.append({"cell": row["id"], "status": status, "reasons": reasons,
                           "target": tgt["id"] if tgt else None, "eligible": ok, "excluded": excluded})
        if ok:
            eligible.append((row, tgt))
        if tgt is not None:
            line = "  %s on %s: %s (%s, %s)" % (row["id"], tgt["id"], status, evidence_text(row), metric_text(row))
            if reasons:
                line += ": " + "; ".join(reasons)
            if excluded:
                line += "; excluded: " + excluded
            shown.append(line)

    explain.append("matrix: %d cells, %d on a target of this host" % (len(matrix["rows"]), len(shown)))
    explain += shown or ["  no measured cell for these targets"]
    if ov:
        explain.append("overrides: " + ", ".join("%s=%s" % (k, ov[k]) for k in sorted(ov)))

    bind = ov.get("bind", DEFAULT_BIND)
    auth = "loopback-key" if is_loopback(bind) else "oidc"
    doc = {"schema": PLAN_SCHEMA, "fingerprint": facts["fingerprint"], "overrides": ov,
           "considered": considered, "bind": bind, "auth": auth, "fixes": fixes}
    if not eligible:
        if fixes:
            summary = fix_lines + [NO_GREEN_LEFT if green_refused else UNMEASURED]
            decision, message = "fix", fix_lines[0]
        else:
            summary = [UNMEASURED]
            decision, message = "unmeasured", UNMEASURED
        summary.append("bind %s, auth %s (%s)" % (bind, auth, AUTH_TEXT[auth]))
        doc.update(decision=decision, message=message, cell=None, engine=None, model=None,
                   placement=None, port=ov.get("port"))
    else:
        eligible.sort(key=lambda rt: (-rt[0]["metrics"]["decode_tok_s"]["median"], rt[0]["id"]))
        row, tgt = eligible[0]
        port = ov.get("port", ENGINE_PORTS.get(row["engine"]["name"], 8000))
        placement = resolve_placement(row["placement"], tgt)
        model = row["model"]
        summary = [
            "pick: %s (%s, highest of %d green cell(s) on this host)" % (row["id"], metric_text(row), len(eligible)),
            "engine: %s on %s (%s)" % (row["engine"]["name"], row["engine"]["device"], row["engine"]["image"]),
            "model: %s%s, %s" % (model["name"], "@" + model["revision"] if model["revision"] else "", model["precision"]),
            "placement: " + placement_text(placement),
            "endpoint: %s:%d, auth %s (%s)" % (bind, port, auth, AUTH_TEXT[auth]),
        ] + fix_lines
        doc.update(decision="green", message=None, cell=row["id"], engine=dict(row["engine"]),
                   model=dict(model), placement=placement, port=port)
    doc["summary"] = summary
    doc["explain"] = explain + ["decision:"] + ["  " + s for s in summary]
    return doc


AGENT_VLLM_VERSION = {"HPU": "0.30.0", "GPU": "0.31.0"}  # the agent's pinned vLLM images


def agent_target(facts, doc, backend=None):
    """(engine, device, target) the model optimizer works for: the green cell's,
    else what `agent up` picks by device (scripts/k3d-gpu agent_device). backend is
    K3D_GPU_AGENT_BACKEND: vllm or llm-scaler serve on an Intel GPU (or a Gaudi)."""
    targets = {t["id"]: t for t in facts["targets"]}
    if backend in ("vllm", "llm-scaler"):
        usable = [t for t in facts["targets"] if t["usable"]]
        for kind, dev in (("hpu", "HPU"), ("gpu", "GPU")):
            for t in usable:
                if t["kind"] == kind and (kind == "hpu" or t["vendor"] == "intel"):
                    return "vllm", dev, t
    if doc["decision"] == "green":
        t = targets[doc["placement"]["target"]]
        dev = {"cpu": "CPU", "gpu": "GPU", "npu": "NPU", "hpu": "HPU"}[t["kind"]]
        return doc["engine"]["name"], dev, t
    usable = [t for t in facts["targets"] if t["usable"]]
    for t in usable:
        if t["kind"] == "hpu":
            return "vllm", "HPU", t
    for t in usable:
        if t["kind"] == "gpu" and t["vendor"] == "intel" and t["xmx"] is True:
            return "ovms", "GPU", t
    return "ovms", "CPU", next(t for t in facts["targets"] if t["kind"] == "cpu")


def hw_class(t):
    """The hardware class a ledger row or a local measurement names."""
    if t["kind"] == "gpu":
        return "gpu:%s" % (t["platform"] or t["device_id"])
    if t["kind"] == "cpu":
        cpu = t["cpu"] or {}
        return "cpu:%s" % (cpu.get("model_name") or cpu.get("family_model") or "unknown")
    return t["kind"]


def host_text(facts):
    """"Intel(R) Xeon(R) 6960P (AMX) + 1x Battlemage 0xe223 (XMX)" for the one-line summary."""
    parts, accel = [], {}
    for t in facts["targets"]:
        if t["kind"] == "cpu":
            cpu = t["cpu"] or {}
            parts.append((cpu.get("model_name") or "CPU") + (" (AMX)" if cpu.get("amx") else ""))
        else:
            key = " ".join(x for x in (t["platform"] or t["vendor"] or t["kind"], t["device_id"], "(XMX)" if t["xmx"] else None) if x)
            accel[key] = accel.get(key, 0) + 1
    parts += ["%dx %s" % (n, k) for k, n in sorted(accel.items())]
    return " + ".join(parts)


def add_optimizer(doc, facts, meta, opts):
    """doc with the model optimizer's decision for its engine and device."""
    o = dict(opts or {})
    backend = o.pop("backend", None)
    engine, device, t = agent_target(facts, doc, backend)
    o.setdefault("vllm_version", "0.26.0" if backend == "llm-scaler" else AGENT_VLLM_VERSION.get(device, "0.31.0"))
    o["hw_class"] = hw_class(t)
    # No backend set: as `agent up`, the CPU engine follows the ledger (model_opt.py H59).
    o.setdefault("engine_from_ledger", backend is None)
    opt = model_opt.optimize(meta, engine, device, o)
    line = model_opt.summary_line(opt, host_text(facts))
    doc["optimizer"] = opt
    doc["summary"] = [line] + doc["summary"]
    doc["explain"] = doc["explain"] + model_opt.explain_lines(opt) + ["summary: " + line]
    return doc


def plan_hash(doc):
    """"sha256:<hex>" of the canonical plan/v1 bytes, the exact `plan --json` output."""
    return "sha256:" + hashlib.sha256(factsmod.dumps(doc)).hexdigest()


def main(argv):
    ap = argparse.ArgumentParser(prog="k3d-gpu plan", description="engine, model and placement from green matrix cells")
    ap.add_argument("--mode", choices=("text", "explain", "json", "hash"), default="text")
    ap.add_argument("--matrix", required=True)
    ap.add_argument("--engine")
    ap.add_argument("--device")
    ap.add_argument("--model")
    ap.add_argument("--bind")
    ap.add_argument("--port")
    ap.add_argument("--model-meta")
    ap.add_argument("--ledger")
    ap.add_argument("--measurements")
    ap.add_argument("--thinking", choices=("off", "on", "model"))
    ap.add_argument("--variant")
    ap.add_argument("--agent-backend")
    args = ap.parse_args(argv[1:])
    schema = factsmod.load_schema()
    try:
        port = None
        if args.port is not None:
            if not args.port.isdigit():
                raise ValueError("port must be an integer 1-65535, got %r" % args.port)
            port = int(args.port)
        facts = json.load(sys.stdin)
        schema_check.check(facts, schema)
        with open(args.matrix, encoding="utf-8") as f:
            matrix = json.load(f)
        schema_check.check(matrix, schema, "#/$defs/matrix")
        doc = plan(facts, matrix, {"engine": args.engine, "device": args.device, "model": args.model,
                                   "bind": args.bind, "port": port})
        if args.model_meta:
            with open(args.model_meta, encoding="utf-8") as f:
                meta = json.load(f)
            ledger = None
            if args.ledger and os.path.exists(args.ledger):
                with open(args.ledger, encoding="utf-8") as f:
                    ledger = f.read()
            meas = {}
            try:
                if args.measurements and os.path.exists(args.measurements):
                    with open(args.measurements, encoding="utf-8") as f:
                        meas = json.load(f)
            except ValueError as e:
                sys.stderr.write("plan: ignoring %s: %s\n" % (args.measurements, e))
            if not isinstance(meas, dict) or meas.get("schema") != model_opt.MEASURE_SCHEMA:
                meas = {}
            try:
                doc = add_optimizer(doc, facts, meta, {"ledger": ledger, "measurements": meas,
                                                       "thinking": args.thinking, "variant": args.variant,
                                                       "backend": args.agent_backend})
            except Exception as e:  # noqa: BLE001  the plan stands without the optimizer
                sys.stderr.write("plan: model optimizer failed (%s: %s); planning without it\n" % (type(e).__name__, e))
        schema_check.check(doc, schema, "#/$defs/plan")
    except (ValueError, OSError) as e:
        sys.stderr.write("plan: %s\n" % e)
        return 2
    if args.mode == "json":
        sys.stdout.buffer.write(factsmod.dumps(doc))
    elif args.mode == "hash":
        sys.stdout.write(plan_hash(doc) + "\n")
    else:
        lines = doc["explain"] if args.mode == "explain" else doc["summary"]
        sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
