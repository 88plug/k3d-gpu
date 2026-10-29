#!/usr/bin/env python3
"""gpu_scan.py: the pod GPU scan of H52w and the wildct wiring smoke (EXPERIMENTS.md "Wildct
pre-registration", W8). Reads `kubectl get pods -A -o json` on stdin; prints every pod's hostPath
volumes, then one line `GPU-SCAN clean` or `GPU-SCAN DIRTY: ...`: no pod mounts a hostPath
under /dev/dri, /dev/nvidia* or /dev itself, and no container or init container asks for an
nvidia.com/ or gpu.intel.com/ resource. Exit 0 when clean, 1 when dirty."""
import json
import sys


def scan(pods):
    """(hostPath lines, findings) for a pod list's items."""
    lines, bad = [], []
    for p in pods.get("items", []):
        n = p["metadata"]["namespace"] + "/" + p["metadata"]["name"]
        for v in p["spec"].get("volumes", []):
            hp = (v.get("hostPath") or {}).get("path", "")
            lines.append(f"{n} hostPath {hp}")
            if hp.startswith(("/dev/dri", "/dev/nvidia")) or hp == "/dev":
                bad.append(f"{n} mounts {hp}")
        for c in p["spec"].get("containers", []) + p["spec"].get("initContainers", []):
            res = {**(c.get("resources", {}).get("limits") or {}), **(c.get("resources", {}).get("requests") or {})}
            for k in res:
                if k.startswith(("nvidia.com/", "gpu.intel.com/")):
                    bad.append(f"{n} asks {k}")
    return lines, bad


def main():
    lines, bad = scan(json.load(sys.stdin))
    for line in lines:
        print(line)
    print("GPU-SCAN", "clean" if not bad else "DIRTY: " + "; ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
