#!/usr/bin/env bash
# convert.sh: the two H54 builds made on crescent from official weights, inside the campaign lock
# (EXPERIMENTS.md "H54 pre-registration", Models; "H54 amendment R1"). Conversions are not holds.
#   OVQ: optimum-cli export openvino from google/gemma-4-26B-A4B-it-qat-q4_0-unquantized @ f1e06dc5,
#        --task image-text-to-text (OVB's pipeline; a local directory needs a named task), --weight-format int4 --sym --group-size 32 --ratio 1.0, with the pinned export venv
#        (bench/h54/ov-export-venv.txt: openvino 2026.4.1, nncf 3.4.0, optimum-intel 2.2.0).
#   The QAT drafter as GGUF: b11749's convert_hf_to_gguf.py (bf16), then b11749's llama-quantize
#        to Q4_0, the target's precision (the model card asks for a same-precision QAT drafter),
#        both in ghcr.io/ggml-org/llama.cpp:full-b11749 pinned to its index digest; the conversion
#        with transformers 5.5.4 in that container (amendment R2).
# Each build is written to OUT.partial and renamed when it and its manifest.json are complete.
set -euo pipefail
G=${HOME}/k3dgpu-gemma
M=${G}/models
FULL_IMAGE=ghcr.io/ggml-org/llama.cpp:full-b11749@sha256:aefb375acf22d45551b0650af7e96361ad1a3abc65ad2bde215f23488da2711e
QAT=${M}/google_gemma-4-26B-A4B-it-qat-q4_0-unquantized
DRAFT=google_gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant
OVQ=${M}/k3dgpu/gemma-4-26b-a4b-it-qat-int4-sym-g32-ov
DG=${M}/k3dgpu/gemma-4-26B-A4B-it-qat-assistant-gguf
V=${G}/ovenv
TRANSFORMERS=5.5.4
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
exec 9> "${HOME}/k3dgpu-bench.lock"
echo "waiting for the lock $(date -u +%FT%TZ)"
flock -w 21600 9
echo "lock taken $(date -u +%FT%TZ); df $(df -P / | awk 'NR==2{print $5}')"
mkdir -p "${M}/k3dgpu"
if [ ! -e "${OVQ}" ]; then
    rm -rf "${OVQ}.partial"
    cmd=("${V}/bin/optimum-cli" export openvino --model "${QAT}" --task image-text-to-text --weight-format int4 --sym --group-size 32 --ratio 1.0 "${OVQ}.partial")
    t0=$(date +%s)
    "${cmd[@]}"
    secs=$(( $(date +%s) - t0 ))
    sha_manifest "${OVQ}.partial" "$(python3 -c 'import json,sys; print(json.dumps({"source": {"repo": "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized", "revision": open(sys.argv[1]).read().split()[-1]}, "weights": "int4 sym, group 32, ratio 1.0", "export_seconds": int(sys.argv[2]), "command": sys.argv[3:]}))' "${QAT}/.pinned-revision" "${secs}" "${cmd[@]/%.partial/}")"
    "${G}/bin/uv" pip freeze --python "${V}/bin/python" > "${OVQ}.partial/export-venv-freeze.txt"
    mv -T "${OVQ}.partial" "${OVQ}"
    echo "OVQ exported in ${secs} s"
fi
if [ ! -e "${DG}" ]; then
    rm -rf "${DG}.partial"
    mkdir -p "${DG}.partial"
    # The image's transformers 4.57.6 cannot read the checkpoint's tokenizer_config.json (v5 format,
    # `extra_special_tokens: []`: "AttributeError: 'list' object has no attribute 'keys'"), so the
    # same image's convert_hf_to_gguf.py runs with transformers 5.5.4, the OVQ export venv's pin,
    # installed in the throwaway container (amendment R2).
    docker run --rm -v "${M}:/models" --entrypoint sh "${FULL_IMAGE}" -c \
        "pip install -q --break-system-packages transformers==${TRANSFORMERS} && python3 /app/convert_hf_to_gguf.py --outtype bf16 /models/${DRAFT} --outfile /models/k3dgpu/$(basename "${DG}").partial/gemma-4-26B-A4B-it-qat-assistant-bf16.gguf"
    docker run --rm -v "${M}:/models" "${FULL_IMAGE}" --quantize \
        "/models/k3dgpu/$(basename "${DG}").partial/gemma-4-26B-A4B-it-qat-assistant-bf16.gguf" \
        "/models/k3dgpu/$(basename "${DG}").partial/gemma-4-26B-A4B-it-qat-assistant-q4_0.gguf" Q4_0
    sudo chown -R "$(id -u):$(id -g)" "${DG}.partial"
    sha_manifest "${DG}.partial" "$(python3 -c 'import json,sys; print(json.dumps({"source": {"repo": "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant", "revision": open(sys.argv[1]).read().split()[-1]}, "image": sys.argv[2], "steps": ["convert_hf_to_gguf.py --outtype bf16 (transformers " + sys.argv[3] + ")", "--quantize Q4_0"]}))' "${M}/${DRAFT}/.pinned-revision" "${FULL_IMAGE}" "${TRANSFORMERS}")"
    mv -T "${DG}.partial" "${DG}"
    echo "drafter GGUF built"
fi
echo "done $(date -u +%FT%TZ); df $(df -P / | awk 'NR==2{print $5}')"
