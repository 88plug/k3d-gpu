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
#   probe.sh --nll RUN   under the lock: the NLL pass alone into RUN/nll.json
#                        (10 min cap), then summarize RUN
#   probe.sh --summarize RUN [NLL_JSON]   re-summarize a run (no containers)
#   probe.sh --dry-run   print the plan, touch nothing
#
# Run the live part only as:
#   flock -o -w 10800 <scratchpad>/live.lock bench/amx-autoquant/probe.sh
# (-o: the lock fd is not inherited, so only flock itself holds the lock).
# after `k3d cluster list` shows no k3dgpu-f-* cluster. No k3d cluster is made.
# Only containers named k3dgpu-i-amx-autoquant-* are created and removed. No
# sysfs writes; RAPL is read with `sudo -n cat`.
#
# Env: AUTOQUANT_DIR (default ${XDG_CACHE_HOME:-~/.cache}/k3d-gpu/amx-autoquant),
#      AUTOQUANT_CPUS (default 0-15, the i9-13900T P-cores),
#      LOAD_LEVEL none|typical|heavy (default none; H27 amendment A1): run every arm
#        under bench/load/load.py at that level, with a 10 s idle gap and a 5 s load
#        window before each reference request, then `load.py matched`,
#      AUTOQUANT_NLL run|defer (default run): defer skips the NLL pass; run it
#        later with --nll.

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
# Checked on PyPI / download.pytorch.org 2026-10-17. openvino 2026.4.1 matches the
# OVMS release. transformers and huggingface_hub are the newest inside optimum-intel
# 2.2.0's bounds (transformers<5.6, huggingface-hub<1.22). torch/torchvision come
# from the CPU wheel index so no CUDA wheels are pulled. --prepare still records
# the full resolved set in pip-freeze.txt.
PIP_PINS="openvino==2026.4.1 openvino-tokenizers==2026.4.1 optimum-intel==2.2.0 optimum==2.3.0 transformers==5.5.4 huggingface-hub==1.21.0 torch==2.14.1+cpu torchvision==0.29.1+cpu pyarrow==26.0.0"
PIP_INDEX="--extra-index-url https://download.pytorch.org/whl/cpu"
# Containers run as the invoking user so the cache stays user-owned.
UIDGID="$(id -u):$(id -g)"
PRECS="int4 int8 fp16"
LOAD_LEVEL="${LOAD_LEVEL:-none}"
AUTOQUANT_NLL="${AUTOQUANT_NLL:-run}"
LOAD="${ROOT}/bench/load/load.py"
GAP=10 WINDOW=5 NLL_CAP=600
declare -A PORT=([int4]=18101 [int8]=18102 [fp16]=18103)

log() { printf '[%4ds] %s\n' "$((SECONDS))" "$*" | tee -a "${RUN:-/dev/null}/probe.log" >&2; }
die() { log "ABORT: $*"; exit 1; }

plan() {
    echo "H27 plan: budget ${BUDGET} s (abort at ${ABORT_AT} s), cpus ${CPUS}, dir ${DIR}"
    echo "  background load: ${LOAD_LEVEL}; before each reference request ${GAP} s idle + ${WINDOW} s load window; NLL ${AUTOQUANT_NLL}"
    [ "${LOAD_LEVEL}" = none ] || python3 "${LOAD}" plan --level "${LOAD_LEVEL}" --ttl 1500 | sed 's/^/  /'
    for p in ${PRECS}; do echo "  container ${PREFIX}-${p} ${OVMS_IMAGE} port ${PORT[${p}]} model OpenVINO/Qwen3-4B-${p}-ov"; done
    echo "  container ${PREFIX}-nll ${PY_IMAGE} (${PIP_PINS})"
    echo "  schedule: warmup, P1, R1 ABCD, R2 DCBA, P2, R3 ABCD, R4 DCBA, R5 ABCD, P3; probe order P1 int4,int8,fp16, P2 reversed, P3 forward; stop OVMS, NLL"
}

prepare() {
    mkdir -p "${DIR}/models" "${DIR}/venv" "${DIR}/text"
    docker run --rm --name "${PREFIX}-prep" --user "${UIDGID}" -v "${DIR}:/w" -e HOME=/w "${PY_IMAGE}" sh -ec "
        python -m venv /w/venv && /w/venv/bin/pip install -q ${PIP_INDEX} ${PIP_PINS}
        /w/venv/bin/pip freeze > /w/pip-freeze.txt
        for p in ${PRECS}; do
            /w/venv/bin/python -c 'import sys; from huggingface_hub import snapshot_download, HfApi
r = sys.argv[1]; snapshot_download(r, local_dir=\"/w/models/\" + r)
print(r, HfApi().model_info(r).sha)' OpenVINO/Qwen3-4B-\$p-ov
        done > /w/model-shas.txt"
    # Held-out text: this repo's docs written in 2026 (after Qwen3's training data), frozen by commit.
    git -C "${ROOT}" rev-parse HEAD > "${DIR}/text/heldout.commit"
    cat "${ROOT}/EXPERIMENTS.md" "${ROOT}"/docs/*.md > "${DIR}/text/heldout.txt"
    docker run --rm --name "${PREFIX}-prep" --user "${UIDGID}" -v "${DIR}:/w" -e HOME=/w "${PY_IMAGE}" /w/venv/bin/python -c '
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
    [ "${LOAD_LEVEL}" = none ] || python3 "${LOAD}" stop >/dev/null 2>&1 || true
    log "cleanup: removed ${PREFIX}-* containers and the background load"
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

# load_running: number of running bench-load containers of this level.
load_running() {
    docker ps --filter label=io.k3d-gpu.bench-load=1 --filter "label=io.k3d-gpu.bench-load.level=${LOAD_LEVEL}" \
        --filter status=running -q | wc -l
}

# reference <label> <order>: one reference request per arm. Under a background level,
# each request follows a GAP s idle gap and a WINDOW s load window tagged with the arm.
reference() {
    if [ "${LOAD_LEVEL}" = none ]; then turn --phase reference --label "$1" --order "$2"; return; fi
    local arm n
    for arm in ${2//,/ }; do
        n=$(load_running)
        [ "${n}" -eq 3 ] || die "INCONCLUSIVE: background lost (${n} of 3 load containers running before $1 ${arm})"
        sleep "${GAP}"
        python3 "${LOAD}" sample --seconds "${WINDOW}" --tag "${arm}" --level "${LOAD_LEVEL}" --out "${RUN}/load.jsonl"
        turn --phase reference --label "$1" --order "${arm}"
    done
}

# probe_repeat <label> <order>: one probe request per arm; P1/P3 forward, P2 reversed.
probe_repeat() {
    local t0=${SECONDS}
    turn --phase probe --label "$1" --order "$2"
    log "probe $1 wall $((SECONDS - t0)) s (budget 60 s)"
}

run() {
    [ -f "${DIR}/text/prompts.json" ] || die "run --prepare first"
    RUN="${DIR}/run-$(date -u +%Y%m%dT%H%M%SZ)-${LOAD_LEVEL}"
    mkdir -p "${RUN}"
    WD=""
    # The EXIT trap stops the watchdog on every path (abort, set -e, success), so
    # no background child outlives the run.
    trap '[ -z "${WD}" ] || kill "${WD}" 2>/dev/null || true; cleanup' EXIT
    trap 'exit 1' TERM INT
    SECONDS=0
    watchdog & WD=$!
    log "preflight"
    if k3d cluster list 2>/dev/null | grep -q '^k3dgpu-f-'; then die "a k3dgpu-f-* cluster exists"; fi
    if [ "${LOAD_LEVEL}" != none ]; then
        python3 "${LOAD}" start --level "${LOAD_LEVEL}" --ttl 1500 | tee -a "${RUN}/probe.log"
        python3 "${LOAD}" status >> "${RUN}/probe.log"
    fi
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
    probe_repeat P1 int4,int8,fp16
    reference R1 "int4,int8,fp16,int4'"
    reference R2 "int4',fp16,int8,int4"
    probe_repeat P2 fp16,int8,int4
    reference R3 "int4,int8,fp16,int4'"
    reference R4 "int4',fp16,int8,int4"
    reference R5 "int4,int8,fp16,int4'"
    probe_repeat P3 int4,int8,fp16
    for p in ${PRECS}; do docker stats --no-stream --format "${p} {{.MemUsage}}" "${PREFIX}-${p}" >> "${RUN}/rss.txt" || true; done
    for p in ${PRECS}; do docker rm -f "${PREFIX}-${p}" >/dev/null; done
    if [ "${LOAD_LEVEL}" != none ]; then
        log "background at the end of the timed part: $(load_running) of 3 load containers running"
        python3 "${LOAD}" stop | tee -a "${RUN}/probe.log"
        python3 "${LOAD}" matched "${RUN}/load.jsonl" > "${RUN}/load-matched.json" || true
        log "load: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["verdict"])' "${RUN}/load-matched.json")"
    fi
    if [ "${AUTOQUANT_NLL}" = run ]; then
        nll_pass
    else
        log "NLL pass deferred (AUTOQUANT_NLL=${AUTOQUANT_NLL}); run probe.sh --nll ${RUN}"
    fi
    summarize "${RUN}" "${RUN}/nll.json"
    log "done in ${SECONDS} s: ${RUN}/results.json"
}

# nll_pass: teacher-forced NLL of every precision into ${RUN}/nll.json, capped at NLL_CAP s.
nll_pass() {
    log "NLL pass"
    local tn=${SECONDS}
    timeout "${NLL_CAP}" docker run --rm --name "${PREFIX}-nll" --user "${UIDGID}" --cpuset-cpus "${CPUS}" \
        -v "${DIR}:/w" -v "${HERE}:/b:ro" -e HOME=/w \
        "${PY_IMAGE}" /w/venv/bin/python /b/nll.py --models /w/models --heldout /w/text/heldout.txt \
        --wikitext /w/text/wikitext2.txt --out "/w/${RUN##*/}/nll.json" || log "NLL pass failed: dNLL will be null"
    docker rm -f "${PREFIX}-nll" >/dev/null 2>&1 || true
    log "NLL pass wall $((SECONDS - tn)) s (quality cost, outside the 60 s probe budget)"
}

# summarize <run dir> <nll.json>: results.json (+ the load match when the run has windows).
summarize() {
    local args=(--requests "$1/results.jsonl" --nll "$2" --out "$1/results.json")
    [ ! -f "$1/load.jsonl" ] || args+=(--load "$1/load.jsonl")
    python3 "${HERE}/summarize.py" "${args[@]}" | tee -a "$1/probe.log"
}

case "${1:-}" in
    --dry-run) plan ;;
    --prepare) prepare ;;
    --store)
        last=$(find "${DIR}" -maxdepth 2 -name results.json -path '*/run-*' | sort | tail -1)
        [ -n "${last}" ] || { echo "no run results under ${DIR}" >&2; exit 1; }
        "${ROOT}/scripts/k3d-gpu" autoquant --store "${last}" ;;
    --nll)
        RUN="${2:?usage: probe.sh --nll RUN_DIR}"; RUN="${RUN%/}"
        [ -f "${RUN}/results.jsonl" ] && [ "${RUN%/*}" = "${DIR}" ] || { echo "not a run dir under ${DIR}: ${RUN}" >&2; exit 2; }
        trap 'docker rm -f "${PREFIX}-nll" >/dev/null 2>&1 || true' EXIT
        SECONDS=0; nll_pass; summarize "${RUN}" "${RUN}/nll.json" ;;
    --summarize)
        RUN="${2:?usage: probe.sh --summarize RUN_DIR [NLL_JSON]}"; RUN="${RUN%/}"
        summarize "${RUN}" "${3:-${RUN}/nll.json}" ;;
    "") run ;;
    *) echo "usage: probe.sh [--dry-run|--prepare|--store|--nll RUN|--summarize RUN [NLL]]" >&2; exit 2 ;;
esac
