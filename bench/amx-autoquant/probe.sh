#!/usr/bin/env bash
# H27 amx-autoquant live run (EXPERIMENTS.md H27, pre-registered before any run).
#
#   probe.sh --prepare   outside the lock: download the three Qwen3-4B OpenVINO
#                        weights, record their HF commit SHAs, build the pinned
#                        NLL venv, freeze prompts and the held-out text
#   probe.sh             under the lock: 3 OVMS containers on P-cores 0-15,
#                        warm-up, probe P1, R1-R2, P2, R3-R5, P3, stop OVMS, NLL
#                        pass, summarize; self-limits to 20 min (abort at 19:00)
#   probe.sh --store     after a run: cache the gated winner for this host
#                        (`k3d-gpu autoquant --store`)
#   probe.sh --dry-run   print the plan, touch nothing
#
# Run the live part only as:
#   flock -w 10800 <scratchpad>/live.lock bench/amx-autoquant/probe.sh
# after `k3d cluster list` shows no k3dgpu-f-* cluster. No k3d cluster is made.
# Only containers named k3dgpu-i-amx-autoquant-* are created and removed. No
# sysfs writes; RAPL is read with `sudo -n cat`.
#
# Env: AUTOQUANT_DIR (default ${XDG_CACHE_HOME:-~/.cache}/k3d-gpu/amx-autoquant),
#      AUTOQUANT_CPUS (default 0-15, the i9-13900T P-cores).

set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "${HERE}/../.." && pwd)
DIR="${AUTOQUANT_DIR:-${XDG_CACHE_HOME:-${HOME}/.cache}/k3d-gpu/amx-autoquant}"
CPUS="${AUTOQUANT_CPUS:-0-15}"
BUDGET=1200 ABORT_AT=1140
PREFIX=k3dgpu-i-amx-autoquant
# openvino/model_server:2026.4.1-gpu, the agent's pin (scripts/k3d-gpu AGENT_OVMS_IMAGE).
OVMS_IMAGE="openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58"
# python:3.12-slim (PYTHON_VERSION=3.12.15) by digest.
PY_IMAGE="python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1"
# Latest on PyPI 2026-10-17; openvino 2026.4.1 matches the OVMS release. pip
# resolves torch/transformers/huggingface_hub from optimum-intel's own bounds, and
# --prepare records the full resolved set in pip-freeze.txt.
PIP_PINS="openvino==2026.4.1 optimum-intel==2.2.0 pyarrow==26.0.0"
PRECS="int4 int8 fp16"
declare -A PORT=([int4]=18101 [int8]=18102 [fp16]=18103)

log() { printf '[%4ds] %s\n' "$((SECONDS))" "$*" | tee -a "${RUN:-/dev/null}/probe.log" >&2; }
die() { log "ABORT: $*"; exit 1; }

plan() {
    echo "H27 plan: budget ${BUDGET} s (abort at ${ABORT_AT} s), cpus ${CPUS}, dir ${DIR}"
    for p in ${PRECS}; do echo "  container ${PREFIX}-${p} ${OVMS_IMAGE} port ${PORT[${p}]} model OpenVINO/Qwen3-4B-${p}-ov"; done
    echo "  container ${PREFIX}-nll ${PY_IMAGE} (${PIP_PINS})"
    echo "  schedule: warmup, P1, R1 ABCD, R2 DCBA, P2, R3 ABCD, R4 DCBA, R5 ABCD, P3, stop OVMS, NLL"
}

prepare() {
    mkdir -p "${DIR}/models" "${DIR}/venv" "${DIR}/text"
    docker run --rm --name "${PREFIX}-prep" -v "${DIR}:/w" -e HOME=/w "${PY_IMAGE}" sh -ec "
        python -m venv /w/venv && /w/venv/bin/pip install -q ${PIP_PINS}
        /w/venv/bin/pip freeze > /w/pip-freeze.txt
        for p in ${PRECS}; do
            /w/venv/bin/python -c 'import sys; from huggingface_hub import snapshot_download, HfApi
r = sys.argv[1]; snapshot_download(r, local_dir=\"/w/models/\" + r)
print(r, HfApi().model_info(r).sha)' OpenVINO/Qwen3-4B-\$p-ov
        done > /w/model-shas.txt"
    # Held-out text: this repo's docs written in 2026 (after Qwen3's training data), frozen by commit.
    git -C "${ROOT}" rev-parse HEAD > "${DIR}/text/heldout.commit"
    cat "${ROOT}/EXPERIMENTS.md" "${ROOT}"/docs/*.md > "${DIR}/text/heldout.txt"
    docker run --rm --name "${PREFIX}-prep" -v "${DIR}:/w" -e HOME=/w "${PY_IMAGE}" /w/venv/bin/python -c '
import json
from huggingface_hub import hf_hub_download
from transformers import AutoTokenizer
p = hf_hub_download("Salesforce/wikitext", "wikitext-2-raw-v1/test-00000-of-00001.parquet", repo_type="dataset")
import pyarrow.parquet as pq
open("/w/text/wikitext2.txt", "w").write("".join(pq.read_table(p)["text"].to_pylist()))
tok = AutoTokenizer.from_pretrained("/w/models/OpenVINO/Qwen3-4B-fp16-ov")
ids = tok(open("/w/text/heldout.txt").read())["input_ids"]
json.dump({"turn": tok.decode(ids[1000:1396]), "probe": tok.decode(ids[2000:2128])}, open("/w/text/prompts.json", "w"))'
    cat "${DIR}/model-shas.txt"
}

cleanup() {
    local c
    for c in ${PRECS} nll; do docker rm -f "${PREFIX}-${c}" >/dev/null 2>&1 || true; done
    log "cleanup: removed ${PREFIX}-* containers"
}

watchdog() {
    while sleep 5; do
        [ "${SECONDS}" -lt "${ABORT_AT}" ] || { log "watchdog: ${ABORT_AT} s reached"; kill -TERM $$; return; }
        local avail
        avail=$(awk '/^MemAvailable:/ {print $2; exit}' /proc/meminfo)
        if [ "${avail}" -lt $((8 * 1024 * 1024)) ] && docker inspect "${PREFIX}-fp16" >/dev/null 2>&1; then
            log "guard: MemAvailable ${avail} kB < 8 GiB, stopping fp16 first"
            docker rm -f "${PREFIX}-fp16" >/dev/null 2>&1 || true
        fi
    done
}

turn() { python3 "${HERE}/turn.py" --ports "int4=${PORT[int4]},int8=${PORT[int8]},fp16=${PORT[fp16]}" \
    --prompts "${DIR}/text/prompts.json" --out "${RUN}/results.jsonl" --cpus "${CPUS}" "$@" | tee -a "${RUN}/probe.log"; }

probe_repeat() {
    local t0=${SECONDS}
    turn --phase probe --label "$1" --order int4,int8,fp16
    log "probe $1 wall $((SECONDS - t0)) s (budget 60 s)"
}

run() {
    [ -f "${DIR}/text/prompts.json" ] || die "run --prepare first"
    RUN="${DIR}/run-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "${RUN}"
    trap cleanup EXIT
    trap 'exit 1' TERM INT
    SECONDS=0
    watchdog & WD=$!
    log "preflight"
    if k3d cluster list 2>/dev/null | grep -q '^k3dgpu-f-'; then die "a k3dgpu-f-* cluster exists"; fi
    local avail sw0 sw1
    avail=$(awk '/^MemAvailable:/ {print $2; exit}' /proc/meminfo)
    [ "${avail}" -ge $((16 * 1024 * 1024)) ] || die "MemAvailable ${avail} kB < 16 GiB"
    sw0=$(awk '/^pswpin/ {print $2}' /proc/vmstat); sleep 60; sw1=$(awk '/^pswpin/ {print $2}' /proc/vmstat)
    [ "${sw1}" -eq "${sw0}" ] || die "swap-in over 60 s: $((sw1 - sw0)) pages"
    { uptime; docker ps --format '{{.Names}}' | wc -l; cat "${DIR}/model-shas.txt"; } > "${RUN}/baseline.txt"
    local p t
    for p in ${PRECS}; do
        docker run -d --name "${PREFIX}-${p}" --cpuset-cpus "${CPUS}" -p "127.0.0.1:${PORT[${p}]}:8000" \
            -v "${DIR}/models:/models" "${OVMS_IMAGE}" --rest_port 8000 \
            --model_path "/models/OpenVINO/Qwen3-4B-${p}-ov" --model_name "${p}" --log_level INFO >/dev/null
        t=${SECONDS}
        until curl -fs "http://127.0.0.1:${PORT[${p}]}/v2/health/ready" >/dev/null; do
            [ $((SECONDS - t)) -lt 300 ] || die "${p} not ready in 300 s"
            sleep 2
        done
        log "${p} ready (cold load $((SECONDS - t)) s)"
        docker logs "${PREFIX}-${p}" 2>&1 | grep -iE 'INFERENCE_PRECISION|KV_CACHE_PRECISION|DYNAMIC_QUANT' \
            > "${RUN}/precision-${p}.txt" || echo "not exposed" > "${RUN}/precision-${p}.txt"
    done
    turn --phase warmup --label W --order int4,int8,fp16
    probe_repeat P1
    turn --phase reference --label R1 --order "int4,int8,fp16,int4'"
    turn --phase reference --label R2 --order "int4',fp16,int8,int4"
    probe_repeat P2
    turn --phase reference --label R3 --order "int4,int8,fp16,int4'"
    turn --phase reference --label R4 --order "int4',fp16,int8,int4"
    turn --phase reference --label R5 --order "int4,int8,fp16,int4'"
    probe_repeat P3
    for p in ${PRECS}; do docker stats --no-stream --format "${p} {{.MemUsage}}" "${PREFIX}-${p}" >> "${RUN}/rss.txt" || true; done
    for p in ${PRECS}; do docker rm -f "${PREFIX}-${p}" >/dev/null; done
    log "NLL pass"
    docker run --rm --name "${PREFIX}-nll" --cpuset-cpus "${CPUS}" -v "${DIR}:/w" -v "${HERE}:/b:ro" -e HOME=/w \
        "${PY_IMAGE}" /w/venv/bin/python /b/nll.py --models /w/models --heldout /w/text/heldout.txt \
        --wikitext /w/text/wikitext2.txt --out "/w/${RUN##*/}/nll.json" || log "NLL pass failed: dNLL will be null"
    python3 "${HERE}/summarize.py" --requests "${RUN}/results.jsonl" --nll "${RUN}/nll.json" --out "${RUN}/results.json" \
        | tee -a "${RUN}/probe.log"
    kill "${WD}" 2>/dev/null || true
    log "done in ${SECONDS} s: ${RUN}/results.json"
}

case "${1:-}" in
    --dry-run) plan ;;
    --prepare) prepare ;;
    --store)
        last=$(find "${DIR}" -maxdepth 2 -name results.json -path '*/run-*' | sort | tail -1)
        [ -n "${last}" ] || { echo "no run results under ${DIR}" >&2; exit 1; }
        "${ROOT}/scripts/k3d-gpu" autoquant --store "${last}" ;;
    "") run ;;
    *) echo "usage: probe.sh [--dry-run|--prepare|--store]" >&2; exit 2 ;;
esac
