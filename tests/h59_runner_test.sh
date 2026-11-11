#!/usr/bin/env bash
# bench/h59/test_run.py, run in a scratch copy of the tree whose launcher carries the
# llama.cpp pins H59 amendment R1 registered.
#
# The H59-family runner renders each arm through scripts/k3d-gpu, and its check refuses a
# render whose llama.cpp server image or drafter conversion Job (image and pip set) differs
# from the registered one. H59, H59d and H59w are scored, so bench/h59 is historical
# (pins/classes) and the launcher's llama-cpp and gguf-convert-pip pins move without it.
# The copy's launcher takes the two llama.cpp images from bench/h59/run.py and the pip set
# from the conversion Job H59w's prep recorded; everything else is the tree's own.
#
# Usage: tests/h59_runner_test.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

tar -C "${ROOT}" --exclude=./.git -cf - . | tar -C "${WORK}" -xf -

python3 - "${WORK}" <<'PY'
import importlib.util
import json
import pathlib
import re
import sys

work = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(work / "bench" / "h59"))
spec = importlib.util.spec_from_file_location("h59_run", work / "bench" / "h59" / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

job = json.loads((work / "bench/h59/results/2026-11-10-wildct/prep/drafter-built/prep-job.json").read_text())
if job["image"] != run.LLAMA_FULL_IMAGE:
    sys.exit(f"h59_runner_test: the recorded conversion Job runs {job['image']}, run.py registers {run.LLAMA_FULL_IMAGE}")
pip = job["env"]["GGUF_CONVERT_PIP"]

launcher = work / "scripts" / "k3d-gpu"
text = launcher.read_text()
for pattern, line in (
    (r'^LLAMA_CPU_IMAGE="\$\{K3D_GPU_LLAMA_CPU_IMAGE:-[^}]*\}"$',
     f'LLAMA_CPU_IMAGE="${{K3D_GPU_LLAMA_CPU_IMAGE:-{run.LLAMA_CPU_IMAGE}}}"'),
    (r'^LLAMA_FULL_IMAGE="\$\{K3D_GPU_LLAMA_FULL_IMAGE:-[^}]*\}"$',
     f'LLAMA_FULL_IMAGE="${{K3D_GPU_LLAMA_FULL_IMAGE:-{run.LLAMA_FULL_IMAGE}}}"'),
    (r'^GGUF_CONVERT_PIP="[^"]*"$', f'GGUF_CONVERT_PIP="{pip}"'),
):
    text, n = re.subn(pattern, lambda _m: line, text, flags=re.M)
    if n != 1:
        sys.exit(f"h59_runner_test: scripts/k3d-gpu has {n} lines matching {pattern}, not 1")
launcher.write_text(text)
PY

cd "${WORK}"
python3 bench/h59/test_run.py
