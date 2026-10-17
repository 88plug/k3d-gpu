#!/usr/bin/env python3
"""Rebuild the seed bundles (ledger H6 and H8) from the committed raw files.

The H6 run (EXPERIMENTS.md, "H6 run (i9-13900T, 2026-10-16)") measured OVMS
2026.4.1 with OpenVINO/Qwen3-4B-int4-ov on the maintainers' i9-13900T: the
P-core CPU arm (label main-cpu) against the UHD 770 arm (main-gpu), 5 rounds
x 5 requests per workload. Its raw files are oracle/raw/h6/*.json, copied
unchanged from the run directory (bench/llm_load.py-style samples: timings
and token counts, no prompt or reply text).

Every constant below is from the H6 provenance block in EXPERIMENTS.md. The
hardware block comes from `k3d-gpu report --json --no-cluster` on that same
host, so this must run there:

  scripts/k3d-gpu report --json --no-cluster > /tmp/report.json
  python3 oracle/seed/build.py /tmp/report.json

Microcode and power state are left out (--run-time-only): they are read when
the bundle is built, not when H6 ran.

H8 (EXPERIMENTS.md, "H8 run (i9-13900T, 2026-10-17)") is the agents-per-box
run, `k3d-gpu capacity --slo 10` per arm on the same host. Only its scored
arms are seeded: A1 (OVMS int4 on the P-cores, two runs, APB 0 by the scoring
rule with N=1 on the SLO edge) and A4 (vLLM CUDA AWQ on the RTX 4000 SFF Ada,
APB >= 32, every step to --max passed). A2 and A3 scored no step and have no
bundle. oracle/raw/h8/<run>/ holds each run's capacity.json (run paths and the
port-forward URL removed) and its steps/nNNN-rR.jsonl, unchanged.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import project  # noqa: E402

H6 = {
    "version": "git-94f71e11d8a5a760010a028aaadcdb44df8df73e",
    "server_image": "openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58",
    "k3s": "v1.34.1+k3s1",
    "model_id": "OpenVINO/Qwen3-4B-int4-ov",
    "revision": "b467368d16b75df14055562fe927ae8e1f15f7ef",
    "weights_sha256": "074214fa29ab1b479536fc7184ef3036e7b355a5835e8590b61b70a8de1df2f9",
    "week": "2026-W42",  # 2026-10-16
}
# arm -> (raw label, engine, xmx_gate state). The CPU arm is what the XMX gate
# picks on this no-XMX iGPU; the GPU arm forces the iGPU past it. Both arms ran
# in the P-core balloon (pe_pinning on).
ARMS = {"cpu": ("main-cpu", "cpu", "on"), "gpu": ("main-gpu", "igpu", "off")}


def build(report, arm, root=""):
    label, engine, gate = ARMS[arm]
    return project.project_single_stream(
        report, root, os.path.join(os.path.dirname(HERE), "raw", "h6"), label, engine,
        version=H6["version"], server_image=H6["server_image"], k3s=H6["k3s"],
        model_id=H6["model_id"], revision=H6["revision"], weights_sha256=H6["weights_sha256"],
        week=H6["week"], lever_states={"pe_pinning": "on", "xmx_gate": gate},
        source="maintainer-lab", note="ledger-H6", run_time_only=True)


H8_VERSION = "git-1a54ffbc76476459102696bb9acccafce07fb63d"
# run dir -> (model id, revision, weights sha256, lever states). A1 is the H6
# model and image; A4's weights were not hashed in the run.
H8_RUNS = {
    "a1-run1": ("OpenVINO/Qwen3-4B-int4-ov", H6["revision"], H6["weights_sha256"],
                {"pe_pinning": "on", "xmx_gate": "on"}),
    "a1-run2": ("OpenVINO/Qwen3-4B-int4-ov", H6["revision"], H6["weights_sha256"],
                {"pe_pinning": "on", "xmx_gate": "on"}),
    "a4": ("Qwen/Qwen3-4B-AWQ", "74d4bd2bd4bff9cafc9345221320bffb08b406a3", None, {}),
}


def build_h8(report, run, root=""):
    model_id, revision, weights, lever_states = H8_RUNS[run]
    run_dir = os.path.join(os.path.dirname(HERE), "raw", "h8", run)
    with open(os.path.join(run_dir, "capacity.json")) as f:
        cap = json.load(f)
    return project.project_capacity(
        report, cap, run_dir, root, version=H8_VERSION, model_id=model_id, revision=revision,
        weights_sha256=weights, lever_states=lever_states, source="maintainer-lab", note="ledger-H8")


def write(b, name):
    path = os.path.join(HERE, name)
    with open(path, "w") as f:
        json.dump(b, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"{path}: {b['content_sha256']}")


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        report = json.load(f)
    for arm in ARMS:
        write(build(report, arm), f"maintainer-i9-13900T-h6-{arm}.json")
    for run in H8_RUNS:
        write(build_h8(report, run), f"maintainer-i9-13900T-h8-{run}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
