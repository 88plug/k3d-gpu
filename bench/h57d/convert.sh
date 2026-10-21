#!/usr/bin/env bash
# convert.sh: the two H57d preparations made on this host, inside the host lock (EXPERIMENTS.md
# "H57d pre-registration", Models; "H57d amendment R1"). Conversions are not holds.
#   The QAT drafter as GGUF, as bench/h54/convert.sh builds it: b11749's convert_hf_to_gguf.py
#        (bf16), then b11749's llama-quantize to Q4_0, both in ghcr.io/ggml-org/llama.cpp:full-b11749.
#        The image's transformers (4.57.6, llama.cpp's pin) cannot read this checkpoint's
#        tokenizer_config.json (`extra_special_tokens` is a list, the transformers 5 format), so the
#        converter runs with transformers 5.20.2 (the latest stable on 2026-10-20) installed
#        into the container's /tmp; the converter code is b11749's.
#   Od's graph.pbtxt: the `--pull` init container of `k3d-gpu agent manifest` (same image, same
#        arguments) run once against the model directory, so Od serves from a read-only bind.
#   G  campaign dir (default ~/k3dgpu-h57d);  LOCK  the host lock file
set -euo pipefail
G=${G:-${HOME}/k3dgpu-h57d}
M=${G}/models
FULL_IMAGE=ghcr.io/ggml-org/llama.cpp:full-b11749@sha256:aefb375acf22d45551b0650af7e96361ad1a3abc65ad2bde215f23488da2711e
OVMS_IMAGE=openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58
DRAFT=google_gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant
DG=${M}/k3dgpu/gemma-4-26B-A4B-it-qat-assistant-gguf
OVB=OpenVINO/gemma-4-26b-a4b-it-int4-ov
exec 9> "${LOCK:?}"
echo "waiting for the lock $(date -u +%FT%TZ)"
flock -w 21600 9
echo "lock taken $(date -u +%FT%TZ); / free $(df -P -B1G / | awk 'NR==2{print $4}') GB"
sha_manifest() {  # sha_manifest DIR JSON_EXTRA: manifest.json with the sha256 of every file
    python3 - "$1" "$2" <<'EOF'
import hashlib, json, os, sys
d, extra = sys.argv[1], json.loads(sys.argv[2])
files = {}
for n in sorted(os.listdir(d)):
    p = os.path.join(d, n)
    if os.path.isfile(p) and n != "manifest.json":
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(1 << 24), b""):
                h.update(c)
        files[n] = {"sha256": h.hexdigest(), "bytes": os.path.getsize(p)}
extra["files"] = files
json.dump(extra, open(os.path.join(d, "manifest.json"), "w"), indent=1)
EOF
}
mkdir -p "${M}/k3dgpu"
if [ ! -e "${DG}" ]; then
    rm -rf "${DG}.partial"
    mkdir -p "${DG}.partial"
    p=/models/k3dgpu/$(basename "${DG}").partial
    docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "${M}:/models" --entrypoint sh "${FULL_IMAGE}" -c \
        "pip install -q --target /tmp/tf transformers==5.20.2 && pip freeze --path /tmp/tf > ${p}/convert-pip-freeze.txt \
         && PYTHONPATH=/tmp/tf python3 /app/convert_hf_to_gguf.py --outtype bf16 /models/${DRAFT} \
            --outfile ${p}/gemma-4-26B-A4B-it-qat-assistant-bf16.gguf"
    docker run --rm -u "$(id -u):$(id -g)" -v "${M}:/models" "${FULL_IMAGE}" --quantize \
        "${p}/gemma-4-26B-A4B-it-qat-assistant-bf16.gguf" "${p}/gemma-4-26B-A4B-it-qat-assistant-q4_0.gguf" Q4_0
    sha_manifest "${DG}.partial" "$(python3 -c 'import json,sys; print(json.dumps({"source": {"repo": "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant", "revision": open(sys.argv[1]).read().split()[-1]}, "image": sys.argv[2], "steps": ["--convert --outtype bf16", "--quantize Q4_0"]}))' "${M}/${DRAFT}/.pinned-revision" "${FULL_IMAGE}")"
    mv -T "${DG}.partial" "${DG}"
    echo "drafter GGUF built"
fi
if [ ! -e "${M}/${OVB}/graph.pbtxt" ]; then
    docker run --rm -u "$(id -u):$(id -g)" -v "${M}:/models" "${OVMS_IMAGE}" --pull --source_model="${OVB}" \
        --model_repository_path=/models --model_name=gemma --task=text_generation --target_device=CPU
    sha256sum "${M}/${OVB}/graph.pbtxt"
fi
echo "done $(date -u +%FT%TZ); / free $(df -P -B1G / | awk 'NR==2{print $4}') GB"
