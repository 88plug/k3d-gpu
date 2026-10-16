#!/usr/bin/env python3
"""Render the k3d-gpu agent profile: which engine each agent role runs on, and
the pod label / extended-resource request that puts it there, from the
accelerators `k3d-gpu detect` reports.

  k3d-gpu detect | agent/agent_profile.py [--topology auto|BASE|CPU_SPLIT|SPLIT_P|SPLIT_G]

Input lines are `<vendor> <resource> [<resource>...]`, e.g.
  intel gpu.intel.com/i915
  hybrid k3d-gpu.io/hybrid-cpu=true

Output (JSON): per role the engine, the pod labels and the resource limits to
merge into that role's pod template, plus cluster requirements the topology
needs (for example the Intel GPU plugin's -shared-dev-num when several roles
hold the iGPU at once).
"""
import argparse
import json
import sys

from sim import TOPOLOGIES, topology

CORES_LABEL = "k3d-gpu.io/cores"
ROLES = ("llm", "embed", "rerank", "vdb", "gateway", "tool", "media")


def parse_detect(text):
    caps = {}
    for line in text.splitlines():
        parts = line.split()
        if parts:
            caps[parts[0]] = parts[1:]
    return caps


def intel_gpu_resource(caps):
    for r in caps.get("intel", []):
        if r.startswith("gpu.intel.com/"):
            return r
    return None


def auto_topology(caps):
    """Most-split topology the detected engines support."""
    if "hybrid" not in caps:
        return "BASE"
    if intel_gpu_resource(caps) or "npu" in caps:
        return "SPLIT_P"
    return "CPU_SPLIT"


def render(caps, name="auto"):
    hybrid = "hybrid" in caps
    gpu = intel_gpu_resource(caps)
    has_npu = "npu" in caps
    if name == "auto":
        name = auto_topology(caps)
    place = topology(name, has_npu)

    need = set(place.values())
    if not hybrid and need & {"E"}:
        raise ValueError(f"{name} needs E-cores; host has no hybrid CPU")
    if not gpu and need & {"igpu", "vcs"}:
        raise ValueError(f"{name} needs an Intel GPU; detect reported none")
    if not has_npu and "npu" in need:
        raise ValueError(f"{name} needs an Intel NPU; detect reported none")

    roles = {}
    for role in ROLES:
        eng = place[role]
        labels, limits = {}, {}
        if hybrid:
            # Accelerator roles keep their host threads off the P-cores.
            labels[CORES_LABEL] = "performance" if eng == "P" else "efficient"
        if eng in ("igpu", "vcs"):
            limits[gpu] = 1
        elif eng == "npu":
            limits["npu.intel.com/accel"] = 1
        roles[role] = {"engine": eng, "labels": labels, "limits": limits}

    gpu_holders = sum(1 for r in roles.values() if gpu in r["limits"])
    requires = {}
    if gpu_holders > 1:
        # intel-gpu-plugin -shared-dev-num defaults to 1: one container per GPU.
        requires["intel-gpu-plugin"] = f"-shared-dev-num={gpu_holders}"
    if hybrid:
        requires["nri-balloons"] = f"pods labelled {CORES_LABEL} (k3d-gpu hybrid support)"
    return {"topology": name, "roles": roles, "requires": requires}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--topology", default="auto", choices=("auto",) + TOPOLOGIES)
    ap.add_argument("detect", nargs="?", help="file with `k3d-gpu detect` output (default: stdin)")
    a = ap.parse_args(argv)
    text = open(a.detect).read() if a.detect else sys.stdin.read()
    try:
        out = render(parse_detect(text), a.topology)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
