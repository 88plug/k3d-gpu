#!/usr/bin/env python3
"""ladder.py RUNG IN OUT: PR #157's H53 boot ladder on an EAT PR #21 XPU chart render
(bench/apb/stage.py `xpu` mode at PR #157 head 23c024dd), for B2q (amendment G1).

Rung a: drop the host Level Zero/OpenCL hostPath mounts and the LD_LIBRARY_PATH prepend (a k3d
node has no host GPU runtime there; the image ships its own). Rung b: also drop `source
/opt/intel/oneapi/setvars.sh --force`. Rung c: also drop `--disable-log-requests`. Rung d: also
add `--max-model-len 32768`. Nothing else in the render changes. Needs PyYAML.
"""
import sys

import yaml

RUNGS = "abcd"


def apply(docs, rung):
    if rung not in RUNGS:
        raise SystemExit(f"ladder.py: rung is one of {RUNGS}, got {rung!r}")
    steps = RUNGS[:RUNGS.index(rung) + 1]
    for d in docs:
        if not d or d.get("kind") != "Deployment":
            continue
        ps = d["spec"]["template"]["spec"]
        c = ps["containers"][0]
        host = {v["name"] for v in ps.get("volumes", [])
                if v.get("hostPath", {}).get("path", "").startswith(("/usr/lib/x86_64-linux-gnu", "/etc/OpenCL"))}
        ps["volumes"] = [v for v in ps.get("volumes", []) if v["name"] not in host]
        c["volumeMounts"] = [m for m in c.get("volumeMounts", []) if m["name"] not in host]
        c["command"] = [x.replace("export LD_LIBRARY_PATH=/host-gpu-libs:${LD_LIBRARY_PATH} && ", "")
                        for x in c.get("command", [])]
        if "b" in steps:
            c["command"] = [x.replace("source /opt/intel/oneapi/setvars.sh --force && ", "") for x in c["command"]]
        if "c" in steps:
            c["args"] = [x for x in c["args"] if x != "--disable-log-requests"]
        if "d" in steps:
            c["args"] += ["--max-model-len", "32768"]
    return docs


def main(argv):
    rung, src, dst = argv
    with open(src) as f:
        docs = [d for d in yaml.safe_load_all(f) if d]   # helm's empty documents dump as `--- null`, which kubectl rejects
    with open(dst, "w") as f:
        yaml.safe_dump_all(apply(docs, rung), f, sort_keys=False)


if __name__ == "__main__":
    main(sys.argv[1:])
