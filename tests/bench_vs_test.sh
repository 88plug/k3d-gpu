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
        && grep -q -- '--cpuset-cpus 0-15 ollama/ollama:0.40.13@sha256:b2f0122c' <<< "${o}" \
        && grep -q -- '--id H10 ' <<< "${o}" \
        && grep -q -- '--arm ollama=http://127.0.0.1:[0-9]*/v1,qwen3:4b-q4_K_M$' <<< "${o}" \
        && grep -q '^verify: sha256 /root/.ollama/models/blobs/sha256-163553aea1b1de62de7c5eb2ef5afb756b4b3133308d9ae7e42e951d8d696ef5 = 163553ae' <<< "${o}"
}
# llama.cpp fetches the GGUF at the pinned revision (-mu .../resolve/<rev>/...), not -hf's head.
dry_llamacpp() {
    local o
    o=$(dry --vs llamacpp) || return 1
    grep -q 'llama.cpp:server-b11960@sha256:8052c13e.* -mu https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf -m /root/.cache/llama.cpp/Qwen3-4B-Q4_K_M.gguf --alias Qwen3-4B --host 0.0.0.0 --port 8080$' <<< "${o}" \
        && ! grep -q -- ' -hf ' <<< "${o}" \
        && grep -q -- '--arm llamacpp=http://127.0.0.1:[0-9]*/v1,Qwen3-4B$' <<< "${o}" \
        && grep -q -- '--id H0 --out bench-H0-llamacpp/result.json' <<< "${o}"
}
# Plain OVMS: no --target_device, so OVMS picks its own device (its default).
dry_ovms() {
    local o
    o=$(dry --vs ovms) || return 1
    grep -q 'model_server:2026.4.1-gpu@sha256:a928903f.* --source_model OpenVINO/Qwen3-4B-int4-ov ' <<< "${o}" \
        && ! grep -q -- '--target_device' <<< "${o}" \
        && grep -q -- ' --user 0 ' <<< "${o}" \
        && grep -q -- '--arm ovms=http://127.0.0.1:[0-9]*/v3,OpenVINO/Qwen3-4B-int4-ov$' <<< "${o}"
}
# The container downloads its model, so the host proxy goes in, loopback exempt.
dry_proxy() {
    local o
    o=$(HTTPS_PROXY=http://proxy:3128 no_proxy=corp.lan dry --vs llamacpp) || return 1
    grep -q -- '-e HTTPS_PROXY=http://proxy:3128 ' <<< "${o}" \
        && grep -q -- '-e NO_PROXY=corp.lan,127.0.0.1,localhost -e no_proxy=corp.lan,127.0.0.1,localhost ' <<< "${o}"
}
dry_no_proxy_env() {
    local o
    o=$(env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy "${K}" bench --dry-run --vs ollama) || return 1
    ! grep -q -- 'PROXY=' <<< "${o}"
}

# Fake docker and curl on PATH: the competitor container exits at once (OOM).
FAKE=$(mktemp -d)
trap 'rm -f "${OUT}"; rm -rf "${FAKE}"' EXIT
cat > "${FAKE}/docker" <<'EOF'
#!/bin/bash
echo "docker $*" >> "${FAKE_LOG}"
case "$1 $2" in
    "inspect -f") echo "false 137 true" ;;
    inspect*) exit 1 ;;
    logs*) echo "killed: out of memory" ;;
esac
exit 0
EOF
cat > "${FAKE}/curl" <<'EOF'
#!/bin/bash
echo "curl $*" >> "${FAKE_LOG}"
exit 7
EOF
chmod +x "${FAKE}/docker" "${FAKE}/curl"
fake_bench() { FAKE_LOG="${FAKE}/log" PATH="${FAKE}:${PATH}" timeout 30 "${K}" bench --vs ollama --k3d-url http://127.0.0.1:9/v3 --k3d-model m --out "${FAKE}/out" "$@"; }
exited_container_fails_fast() {
    local o s=${SECONDS}
    : > "${FAKE}/log"
    if o=$(fake_bench 2>&1); then return 1; fi
    grep -q 'exited (exit code 137, OOMKilled true)' <<< "${o}" && grep -q 'out of memory' <<< "${o}" \
        && [ $((SECONDS - s)) -lt 10 ] \
        && grep -q "^curl --noproxy \* " "${FAKE}/log" \
        && grep -q '^docker volume rm k3dgpu-g-vs-ollama-cache' "${FAKE}/log" \
        && grep -q 'out of memory' "${FAKE}/out/competitor.log"
}
keep_cache_keeps_volume() {
    : > "${FAKE}/log"
    fake_bench --keep-cache >/dev/null 2>&1
    grep -q '^docker rm -f k3dgpu-g-vs-ollama' "${FAKE}/log" && ! grep -q 'volume rm' "${FAKE}/log"
}
dry_k3d_url() {
    dry --vs ollama --k3d-url http://127.0.0.1:9/v3 --k3d-model m | grep -q -- '--arm k3d-gpu=http://127.0.0.1:9/v3,m '
}
help_lists_bench() { "${K}" help | grep 'bench --vs ollama|llamacpp|ovms' >/dev/null; }

check "dry-run ollama: pinned image, cpuset, /v1 arm" dry_ollama
check "dry-run llamacpp: Q4_K_M GGUF, default id and out dir" dry_llamacpp
check "dry-run ovms: defaults, no --target_device, /v3 arm" dry_ovms
check "dry-run --k3d-url replaces the port-forward" dry_k3d_url
check "dry-run forwards host proxy settings, loopback in NO_PROXY" dry_proxy
check "dry-run adds no proxy settings when the host has none" dry_no_proxy_env
check "exited competitor fails fast with its logs; logs kept in competitor.log; cache volume removed" exited_container_fails_fast
check "--keep-cache keeps the cache volume" keep_cache_keeps_volume
check "help lists --keep-cache and K3D_GPU_SIGNOFF" grep -q 'K3D_GPU_SIGNOFF' <("${K}" help)
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
check "bench/tune-noise/analyze.py unit tests (H19)" python3 "${ROOT}/tests/tune_noise_test.py"
agent_plan() { "${K}" bench agent plan H42 | grep -q '"hypothesis": "H42"'; }
agent_missing() {
    local o
    if o=$(K3D_GPU_BENCH=/nonexistent "${K}" bench agent plan H42 2>&1); then return 1; fi
    grep -q 'bench agent: /nonexistent/eatflow/agentbench.py missing' <<< "${o}"
}
check "bench agent dispatches to bench/eatflow/agentbench.py" agent_plan
check "bench agent without bench/eatflow fails with a hint" agent_missing
check "help lists bench agent" grep -q 'bench agent plan|run|apb' <("${K}" help)

echo "bench_vs: ${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
