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

# lock_image <stack>: the stack's image in bench/competitors.lock, cut to
# name:tag@sha256:<first 8 hex> as the plans print it (a bump moves the lock, not this test).
lock_image() {
    python3 -c 'import json, sys
i = json.load(open(sys.argv[1]))["stacks"][sys.argv[2]]["image"]
print(i[:i.index("@sha256:") + 16])' "${ROOT}/bench/competitors.lock" "$1"
}

dry_ollama() {
    local o
    o=$(dry --vs ollama --id H10 --cpuset 0-15) || return 1
    grep -q '^docker run -d --name k3dgpu-g-vs-ollama -p 127.0.0.1:[0-9]*:11434 ' <<< "${o}" \
        && grep -qF -- "--cpuset-cpus 0-15 $(lock_image ollama)" <<< "${o}" \
        && grep -q -- '--id H10 ' <<< "${o}" \
        && grep -q -- '--arm ollama=http://127.0.0.1:[0-9]*/v1,qwen3:4b-q4_K_M$' <<< "${o}" \
        && grep -q '^verify: sha256 /root/.ollama/models/blobs/sha256-163553aea1b1de62de7c5eb2ef5afb756b4b3133308d9ae7e42e951d8d696ef5 = 163553ae' <<< "${o}"
}
# llama.cpp fetches the GGUF at the pinned revision (-mu .../resolve/<rev>/...), not -hf's head.
dry_llamacpp() {
    local o
    o=$(dry --vs llamacpp) || return 1
    grep -qF "$(lock_image llamacpp)" <<< "${o}" \
        && grep -q ' -mu https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf -m /root/.cache/llama.cpp/Qwen3-4B-Q4_K_M.gguf --alias Qwen3-4B --host 0.0.0.0 --port 8080$' <<< "${o}" \
        && ! grep -q -- ' -hf ' <<< "${o}" \
        && grep -q -- '--arm llamacpp=http://127.0.0.1:[0-9]*/v1,Qwen3-4B$' <<< "${o}" \
        && grep -q -- '--id H0 --out bench-H0-llamacpp/result.json' <<< "${o}"
}
# Plain OVMS: no --target_device, so OVMS picks its own device (its default).
dry_ovms() {
    local o
    o=$(dry --vs ovms) || return 1
    grep -qF "$(lock_image ovms)" <<< "${o}" && grep -q ' --source_model OpenVINO/Qwen3-4B-int4-ov ' <<< "${o}" \
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

# Fake sysfs trees for --cpuset. HYB is an i9-13900T: P-cores 0-15 as eight
# hyperthread pairs (cpu_core), E-cores 16-31 one thread each (cpu_atom).
# NOHT is an Arrow Lake-S: P-cores 0-7 without hyperthreads, E-cores 8-23.
# FLAT has one core type: CPUs 0-7 as four hyperthread pairs, no hybrid PMUs.
# SPLIT has one core type with the pairs numbered apart, CPU c and c+4, as on many
# Xeon and EPYC hosts.
HYB=$(mktemp -d) NOHT=$(mktemp -d) FLAT=$(mktemp -d) SPLIT=$(mktemp -d)
mkdir -p "${HYB}/sys/devices/cpu_core" "${HYB}/sys/devices/cpu_atom"
echo 0-15 > "${HYB}/sys/devices/cpu_core/cpus"
echo 16-31 > "${HYB}/sys/devices/cpu_atom/cpus"
for c in $(seq 0 31); do
    mkdir -p "${HYB}/sys/devices/system/cpu/cpu${c}/topology"
    if [ "${c}" -lt 16 ]; then s="$((c - c % 2))-$((c - c % 2 + 1))"; else s=${c}; fi
    echo "${s}" > "${HYB}/sys/devices/system/cpu/cpu${c}/topology/thread_siblings_list"
done
mkdir -p "${NOHT}/sys/devices/cpu_core" "${NOHT}/sys/devices/cpu_atom"
echo 0-7 > "${NOHT}/sys/devices/cpu_core/cpus"
echo 8-23 > "${NOHT}/sys/devices/cpu_atom/cpus"
for c in $(seq 0 23); do
    mkdir -p "${NOHT}/sys/devices/system/cpu/cpu${c}/topology"
    echo "${c}" > "${NOHT}/sys/devices/system/cpu/cpu${c}/topology/thread_siblings_list"
done
for c in $(seq 0 7); do
    mkdir -p "${FLAT}/sys/devices/system/cpu/cpu${c}/topology"
    echo "$((c - c % 2))-$((c - c % 2 + 1))" > "${FLAT}/sys/devices/system/cpu/cpu${c}/topology/thread_siblings_list"
    mkdir -p "${SPLIT}/sys/devices/system/cpu/cpu${c}/topology"
    echo "$((c % 4)),$((c % 4 + 4))" > "${SPLIT}/sys/devices/system/cpu/cpu${c}/topology/thread_siblings_list"
done
# threads_env <sysroot> <stack> [bench args...]: the LLAMA_ARG_THREADS value the
# plan's docker run sets, or "none".
threads_env() {
    local root=$1 stack=$2 o
    shift 2
    o=$(K3D_GPU_SYSROOT="${root}" "${K}" bench --dry-run --vs "${stack}" "$@") || return 1
    o=$(grep '^docker run ' <<< "${o}" | grep -o -- '-e LLAMA_ARG_THREADS=[0-9]*' | cut -d= -f2)
    echo "${o:-none}"
}
# Ollama's llama-server takes llama.cpp's default thread count, which pins itself
# to every online CPU to find the P-cores (common_cpu_get_num_math); inside a
# cpuset the first pin outside it fails and the count falls back to the host's
# physical cores, 24 on the 13900T, so 24 spinning threads share 16 CPUs.
# --cpuset gives the ollama stack the count llama.cpp picks when it can see every
# CPU, restricted to the cpuset: on the 13900T one thread per physical P-core in it
# (one per physical core when the cpuset has no P-core or the CPU has one core type).
# llama.cpp's own image (OpenMP) and OVMS keep their defaults.
cpuset_threads() {
    [ "$(threads_env "${HYB}" ollama --cpuset 0-15)" = 8 ] \
        && [ "$(threads_env "${HYB}" ollama --cpuset 0-3,16-19)" = 2 ] \
        && [ "$(threads_env "${HYB}" ollama --cpuset 16-23)" = 8 ] \
        && [ "$(threads_env "${FLAT}" ollama --cpuset 0-7)" = 4 ] \
        && [ "$(threads_env "${FLAT}" ollama --cpuset 0,2,4)" = 3 ] \
        && [ "$(threads_env "${SPLIT}" ollama --cpuset 0-3)" = 4 ] \
        && [ "$(threads_env "${SPLIT}" ollama --cpuset 0-7)" = 4 ] \
        && [ "$(threads_env "${HYB}" ollama)" = none ] \
        && [ "$(threads_env "${HYB}" llamacpp --cpuset 0-15)" = none ] \
        && [ "$(threads_env "${HYB}" ovms --cpuset 0-15)" = none ] \
        && K3D_GPU_SYSROOT="${HYB}" dry --vs ollama --cpuset 0-15 \
            | grep -qF -- "-e LLAMA_ARG_THREADS=8 --cpuset-cpus 0-15 $(lock_image ollama)"
}
# llama.cpp finds the P-cores by walking the CPU numbers: it counts a non-E CPU and
# skips the next number as its hyperthread, so on P-cores without hyperthreads it
# counts every other one (4 of the 8 on an Arrow Lake-S). The env carries that
# count, not more: a cpuset of the whole host gets exactly the default llama.cpp
# picks unpinned (8 on the 13900T, 4 on the Arrow Lake-S). The walk goes up the
# CPU numbers whatever order the list names them in.
cpuset_threads_no_ht() {
    [ "$(threads_env "${NOHT}" ollama --cpuset 0-23)" = 4 ] \
        && [ "$(threads_env "${NOHT}" ollama --cpuset 0-7)" = 4 ] \
        && [ "$(threads_env "${NOHT}" ollama --cpuset 0-3,8-11)" = 2 ] \
        && [ "$(threads_env "${NOHT}" ollama --cpuset 8-15)" = 8 ] \
        && [ "$(threads_env "${HYB}" ollama --cpuset 0-31)" = 8 ] \
        && [ "$(threads_env "${HYB}" ollama --cpuset 1,0)" = 1 ] \
        && [ "$(threads_env "${HYB}" ollama --cpuset 2-3,0-1,0)" = 2 ]
}

# Fake docker and curl on PATH: the competitor container exits at once (OOM).
FAKE=$(mktemp -d)
trap 'rm -f "${OUT}"; rm -rf "${FAKE}" "${HYB}" "${NOHT}" "${FLAT}" "${SPLIT}"' EXIT
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
check "dry-run --cpuset gives ollama one thread per physical P-core in the cpuset" cpuset_threads
check "dry-run --cpuset gives ollama llama.cpp's own count (P-cores without hyperthreads, whole host, any list order)" cpuset_threads_no_ht
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
