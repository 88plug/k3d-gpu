#!/usr/bin/env python3
"""facts/v1: normalize the launcher's raw facts and render a summary.

  facts.py normalize < raw.json   facts/v1 on stdout (validated, canonical bytes)
  facts.py summary < facts.json   short text view of a facts/v1 document

The launcher (`k3d-gpu facts`) gathers the raw document: the report/v1 members,
the node labels `up` sets, lookups from its own tables, and the doctor and
doctor --perf transcripts. This file only reshapes that; it reads no host state.
Canonical form: keys sorted, two-space indent, UTF-8, one trailing newline, and
nothing taken from the clock, so one host gives the same bytes on every run.

Python standard library only.
"""

import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import schema_check  # noqa: E402  (stdlib-only sibling module)

SCHEMA_PATH = os.path.join(HERE, "facts.v1.schema.json")
FACTS_SCHEMA = "k3d-gpu.facts/v1"
KIND_ORDER = {"cpu": 0, "gpu": 1, "npu": 2, "hpu": 3}
LEVELS = ("ok", "info", "note", "warn", "fail")
DETAIL_KEYS = ("from", "why", "apply", "revert")
INTEL_GPU_DRIVERS = ("xe", "i915")

FINDING_RE = re.compile(r"^  (ok|info|note|warn|fail|good|src)(?::\s*(.*))?$")
DETAIL_RE = re.compile(r"^ {8}(from|why|apply|revert):\s*(.*)$")
CONT_RE = re.compile(r"^ {4,}\S")


def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def dumps(doc):
    """Canonical JSON bytes."""
    return (json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def parse_transcript(text, code):
    """doctor / doctor --perf output -> {exit, sections, findings}."""
    sections, findings = [], []
    section, cur, field = None, None, "text"
    for line in text.splitlines():
        if line.startswith("==> "):
            section = line[4:].strip()
            sections.append({"name": section, "good": None, "src": None})
            cur = None
            continue
        m = FINDING_RE.match(line)
        if m:
            level, body = m.group(1), (m.group(2) or "").strip()
            if level in ("good", "src"):
                if sections:
                    sections[-1][level] = body
                cur = None
            else:
                cur = {"section": section, "level": level, "text": body,
                       "from": None, "why": None, "apply": None, "revert": None}
                field = "text"
                findings.append(cur)
            continue
        m = DETAIL_RE.match(line)
        if m and cur is not None:
            field = m.group(1)
            cur[field] = m.group(2).strip()
            continue
        if cur is not None and CONT_RE.match(line):
            # An indented line under a finding continues its text, or the detail
            # line above it.
            cur[field] = (cur[field] + " " + line.strip()).strip()
            continue
        if line.strip():
            # warn() and err() lines (stderr): "WARN: ..." and "ERROR: ...".
            level, body = "other", line.strip()
            for prefix, lvl in (("WARN: ", "warn"), ("ERROR: ", "fail")):
                if body.startswith(prefix):
                    level, body = lvl, body[len(prefix):]
            cur = None
            findings.append({"section": section, "level": level, "text": body,
                             "from": None, "why": None, "apply": None, "revert": None})
    return {"exit": code, "sections": sections, "findings": findings}


def target(**kw):
    t = {"id": None, "kind": None, "vendor": None, "device_id": None, "pci": None, "platform": None,
         "driver": None, "resource": None, "xmx": None, "generation": None, "usable": True,
         "reason": None, "cpu": None}
    t.update(kw)
    return t


def build_targets(host, lookups, detected):
    """Where plan() can place an engine, from report/v1 host data plus the launcher's lookups."""
    cpu = host["cpu"]
    vendor = lookups.get("cpu_vendor")
    out = [target(
        id="cpu", kind="cpu",
        vendor={"GenuineIntel": "intel", "AuthenticAMD": "amd"}.get(vendor, vendor),
        cpu={
            "model_name": cpu["model"],
            "family_model": lookups.get("cpu_family_model"),
            "logical_cpus": cpu["logical_cpus"],
            "hybrid": cpu["hybrid"],
            "p_cpus": cpu["p_cores"],
            "e_cpus": cpu["e_cores"],
            "p_count": cpu["p_core_cpus"],
            "e_count": cpu["e_core_cpus"],
            "amx": bool(cpu["amx"]),
            "numa_nodes": lookups.get("numa_nodes") or 0,
        })]
    gpu_info = lookups.get("intel_gpu", {})
    for g in host["intel_gpus"]:
        drv = g["driver"]
        usable = drv in INTEL_GPU_DRIVERS
        out.append(target(
            id="gpu:" + g["pci"], kind="gpu", vendor="intel", device_id=g["device_id"], pci=g["pci"],
            platform=g["platform"], driver=drv,
            resource="gpu.intel.com/" + drv if usable else None,
            xmx=gpu_info.get(g["device_id"] or "", {}).get("xmx"),
            usable=usable,
            reason=None if usable else "no xe or i915 driver bound (driver: %s)" % (drv or "none")))
    for n in host["npus"]:
        out.append(target(
            id="npu:" + n["pci"], kind="npu", vendor="intel", device_id=n["device_id"], pci=n["pci"],
            platform=n["platform"], driver=n["driver"], resource="npu.intel.com/accel",
            generation=lookups.get("npu_generation", {}).get(n["device_id"] or "")))
    for n in host["nvidia"]["gpus"]:
        out.append(target(
            id="gpu:" + n["pci"], kind="gpu", vendor="nvidia", device_id=n["device_id"], pci=n["pci"],
            platform=n["model"], driver="nvidia", resource="nvidia.com/gpu"))
    # detect found NVIDIA (device nodes or the module) but /proc/driver/nvidia
    # listed no GPU: one target for "the NVIDIA GPUs", identity unknown.
    if not host["nvidia"]["gpus"] and any(d["vendor"] == "nvidia" for d in detected):
        out.append(target(id="gpu:nvidia", kind="gpu", vendor="nvidia", driver="nvidia", resource="nvidia.com/gpu"))
    ga = host["gaudi"]
    if ga["pci_devices"] or ga["accel_nodes"]:
        reason = None
        if not ga["driver_loaded"]:
            reason = "habanalabs driver not loaded"
        elif not ga["accel_nodes"]:
            reason = "no /dev/accel nodes"
        out.append(target(id="hpu", kind="hpu", vendor="habana", driver="habanalabs" if ga["driver_loaded"] else None,
                          resource="habana.ai/gaudi", usable=reason is None, reason=reason))
    out.sort(key=lambda t: (KIND_ORDER[t["kind"]], t["id"]))
    return out


def fingerprint(targets):
    """sha256 over the hardware identity only (no kernel, driver or firmware state)."""
    cpu = next(t for t in targets if t["kind"] == "cpu")
    ident = {
        "cpu": [cpu["vendor"], cpu["cpu"]["model_name"], cpu["cpu"]["family_model"], cpu["cpu"]["logical_cpus"]],
        "devices": sorted([t["kind"], t["vendor"] or "", t["device_id"] or "", t["pci"] or ""]
                          for t in targets if t["kind"] != "cpu"),
    }
    blob = json.dumps(ident, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def normalize(raw):
    report = raw["report"]
    targets = build_targets(report["host"], raw["lookups"], report["detected"])
    labels = {}
    for item in raw["labels"]:
        key, _, value = item.partition("=")
        labels[key] = value
    doc = {
        "schema": FACTS_SCHEMA,
        "fingerprint": fingerprint(targets),
        "release": {"k3d_gpu_version": raw["k3d_gpu_version"], "pins": raw["pins"]},
        "hostname": report["hostname"],
        "host": report["host"],
        "detected": report["detected"],
        "labels": labels,
        "targets": targets,
        "doctor": parse_transcript(raw["doctor"]["text"], raw["doctor"]["exit"]),
        "perf": parse_transcript(raw["perf"]["text"], raw["perf"]["exit"]),
        "cluster": report["cluster"],
        "capacity": raw.get("capacity", []),
    }
    schema_check.check(doc, load_schema())
    return doc


def describe(t):
    """One line for a target."""
    if t["kind"] == "cpu":
        c = t["cpu"]
        parts = [c["model_name"] or "unknown CPU"]
        if c["family_model"]:
            parts.append("family:model " + c["family_model"])
        if c["hybrid"]:
            parts.append("P-cores %s (%s), E-cores %s (%s)" % (c["p_cpus"], c["p_count"], c["e_cpus"], c["e_count"]))
        elif c["logical_cpus"] is not None:
            parts.append("%d CPUs" % c["logical_cpus"])
        if c["amx"]:
            parts.append("AMX")
        if c["numa_nodes"] > 1:
            parts.append("%d NUMA nodes" % c["numa_nodes"])
        return ", ".join(parts)
    parts = [" ".join(x for x in (t["vendor"], t["platform"]) if x) or t["kind"]]
    if t["device_id"]:
        parts.append(t["device_id"])
    if t["driver"]:
        parts.append("on " + t["driver"])
    if t["xmx"] is not None:
        parts.append("XMX" if t["xmx"] else "no XMX")
    if t["generation"]:
        parts.append("NPU " + t["generation"])
    return ", ".join(parts)


def summary(doc):
    schema_check.check(doc, load_schema())
    lines = ["facts/v1 for %s (k3d-gpu %s)" % (doc["fingerprint"], doc["release"]["k3d_gpu_version"])]
    for t in doc["targets"]:
        state = "usable" if t["usable"] else "not usable: " + t["reason"]
        lines.append("  %-18s %s — %s" % (t["id"], describe(t), state))
    for name in ("doctor", "perf"):
        counts = {}
        for f in doc[name]["findings"]:
            counts[f["level"]] = counts.get(f["level"], 0) + 1
        shown = ", ".join("%d %s" % (counts[k], k) for k in LEVELS + ("other",) if counts.get(k))
        lines.append("  %-18s %s" % ("doctor --perf" if name == "perf" else "doctor", shown or "no findings"))
    lines.append("  %-18s %d" % ("node labels", len(doc["labels"])))
    return "\n".join(lines) + "\n"


def main(argv):
    if len(argv) != 2 or argv[1] not in ("normalize", "summary"):
        sys.stderr.write("usage: facts.py normalize|summary < input.json\n")
        return 2
    data = json.load(sys.stdin)
    try:
        if argv[1] == "normalize":
            sys.stdout.buffer.write(dumps(normalize(data)))
        else:
            sys.stdout.buffer.write(summary(data).encode("utf-8"))
    except ValueError as e:
        sys.stderr.write("facts: %s\n" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
