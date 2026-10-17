#!/usr/bin/env bash
# `k3d-gpu bench --vs` tests: CLI parsing and the --dry-run plan for every stack
# in bench/competitors.lock, then bench/vs.py's unit tests against stub servers.
# No docker, no cluster, no hardware.
#
# Usage: tests/bench_vs_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
K="${ROOT}/scripts/k3d-gpu"
PASS=0 FAILS=0
OUT=$(mktemp)
trap 'rm -f "${OUT}"' EXIT

# check <name> <command...>: the command must succeed.
check() {
    local name=$1
    shift
    if "$@" > "${OUT}" 2>&1; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name}"
        sed 's/^/  | /' "${OUT}"
    fi
}

# refuses <name> <pattern> <args...>: bench must fail and print the pattern.
refuses() {
    local name=$1 pat=$2 out
    shift 2
    if out=$("${K}" bench "$@" 2>&1); then
        FAILS=$((FAILS + 1)); echo "FAIL ${name} (exit 0)"; return
    fi
    if grep -q -- "${pat}" <<< "${out}"; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name}"; sed 's/^/  | /' <<< "${out}"
    fi
}

dry() { "${K}" bench --dry-run "$@"; }

dry_ollama() {
    local o
    o=$(dry --vs ollama --id H10 --cpuset 0-15) || return 1
    grep -q '^docker run -d --name k3dgpu-g-vs-ollama -p 127.0.0.1:[0-9]*:11434 ' <<< "${o}" \
        && grep -q -- '--cpuset-cpus 0-15 ollama/ollama:0.40.5@sha256:5d3a2f25' <<< "${o}" \
        && grep -q -- '--id H10 ' <<< "${o}" \
        && grep -q -- '--arm ollama=http://127.0.0.1:[0-9]*/v1,qwen3:4b$' <<< "${o}"
}
dry_llamacpp() {
    local o
    o=$(dry --vs llamacpp) || return 1
    grep -q 'llama.cpp:server-b11706@sha256:f41d47ee.* -hf Qwen/Qwen3-4B-GGUF:Q4_K_M --host 0.0.0.0 --port 8080$' <<< "${o}" \
        && grep -q -- '--arm llamacpp=http://127.0.0.1:[0-9]*/v1,Qwen3-4B$' <<< "${o}" \
        && grep -q -- '--id H0 --out bench-H0-llamacpp/result.json' <<< "${o}"
}
# Plain OVMS: no --target_device, so OVMS picks its own device (its default).
dry_ovms() {
    local o
    o=$(dry --vs ovms) || return 1
    grep -q 'model_server:2026.4.1-gpu@sha256:a928903f.* --source_model OpenVINO/Qwen3-4B-int4-ov ' <<< "${o}" \
        && ! grep -q -- '--target_device' <<< "${o}" \
        && grep -q -- '--arm ovms=http://127.0.0.1:[0-9]*/v3,OpenVINO/Qwen3-4B-int4-ov$' <<< "${o}"
}
dry_k3d_url() {
    dry --vs ollama --k3d-url http://127.0.0.1:9/v3 --k3d-model m | grep -q -- '--arm k3d-gpu=http://127.0.0.1:9/v3,m '
}
help_lists_bench() { "${K}" help | grep -q 'bench --vs ollama|llamacpp|ovms'; }

check "dry-run ollama: pinned image, cpuset, /v1 arm" dry_ollama
check "dry-run llamacpp: Q4_K_M GGUF, default id and out dir" dry_llamacpp
check "dry-run ovms: defaults, no --target_device, /v3 arm" dry_ovms
check "dry-run --k3d-url replaces the port-forward" dry_k3d_url
check "help lists bench" help_lists_bench
refuses "no --vs" "--vs <stack> is required"
refuses "unknown stack" "unknown stack 'vllm'" --vs vllm --dry-run
refuses "non-numeric rounds" "whole numbers" --vs ollama --rounds x --dry-run
refuses "bad id" "--id must look like H10" --vs ollama --id 10 --dry-run
refuses "bad cpuset" "--cpuset takes a CPU list" --vs ollama --cpuset 'a;b' --dry-run
refuses "k3d-url without model" "--k3d-url needs --k3d-model" --vs ollama --k3d-url http://x/v3 --dry-run
refuses "missing value" "--budget needs a value" --vs ollama --budget
refuses "unknown option" "unknown bench option: --fast" --vs ollama --fast
check "bench/vs.py unit tests (stub servers)" python3 "${ROOT}/tests/bench_vs_test.py"

echo "bench_vs: ${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
