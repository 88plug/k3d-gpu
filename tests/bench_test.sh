#!/usr/bin/env bash
# Measurement harness tests: bench/energy.sh against a fake RAPL tree,
# bench/llm_load.py against tests/openai_stub.py, bench/noisy.sh manifests,
# bench/agent_trace.py through its own unit tests (in-process stub).
# research/ models through their own tests and selftest.
# No root, no cluster, no hardware.
#
# Usage: tests/bench_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d)
STUB_PIDS=()
cleanup() {
    local p
    for p in ${STUB_PIDS[@]+"${STUB_PIDS[@]}"}; do kill "${p}" 2>/dev/null || true; done
    rm -rf "${WORK}"
}
trap cleanup EXIT
PASS=0 FAILS=0

# check <name> <command...>: the command must succeed.
check() {
    local name=$1
    shift
    if "$@" > "${WORK}/check.out" 2>&1; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name}"
        sed 's/^/  | /' "${WORK}/check.out"
    fi
}

# pyassert <json file>: run the python on stdin with the parsed file as `j`.
pyassert() { python3 -c 'import json, sys; j = json.load(open(sys.argv[1])); exec(sys.stdin.read())' "$1"; }

# ---- energy.sh ------------------------------------------------------------------

PC="${WORK}/powercap"
ENERGY=(env ENERGY_POWERCAP="${PC}" "${ROOT}/bench/energy.sh")
EJ="${WORK}/energy.json"

# zone <max_energy_range_uj> <energy_uj>: a readable fake package zone.
zone() {
    mkdir -p "${PC}/intel-rapl:0"
    echo package-0 > "${PC}/intel-rapl:0/name"
    echo "$1" > "${PC}/intel-rapl:0/max_energy_range_uj"
    echo "$2" > "${PC}/intel-rapl:0/energy_uj"
}
# set_uj <value>: what the counter reads next (run by the measured command).
set_uj() { echo "$1" > "${PC}/intel-rapl:0/energy_uj"; }

energy_plain() {
    zone 1000000 100
    "${ENERGY[@]}" --interval 100 --json "${EJ}" -- bash -c "$(declare -f set_uj); PC=${PC}; set_uj 400100" \
        && grep -q '"energy_uj":400000' "${EJ}" && grep -q '"joules":0.400' "${EJ}"
}
energy_one_wrap() {
    zone 1000000 999000
    "${ENERGY[@]}" --interval 100 --json "${EJ}" -- bash -c "$(declare -f set_uj); PC=${PC}; set_uj 500" \
        && grep -q '"energy_uj":1500' "${EJ}"
}
# Two wraps, each inside its own sampling interval:
# 0 -> 900000 -> 100000 (wrap) -> 50000 (wrap) = 900000 + 200000 + 950000.
energy_two_wraps() {
    zone 1000000 0
    "${ENERGY[@]}" --interval 0.2 --json "${EJ}" -- \
        bash -c "$(declare -f set_uj); PC=${PC}; set_uj 900000; sleep 0.6; set_uj 100000; sleep 0.6; set_uj 50000" \
        && grep -q '"energy_uj":2050000' "${EJ}"
}
energy_exit_code() {
    zone 1000000 0
    "${ENERGY[@]}" -- false
    [ $? -eq 1 ]
}
energy_bad_zone() {
    "${ENERGY[@]}" --zone intel-rapl:9 -- true
    [ $? -eq 2 ]
}

check "energy: plain delta" energy_plain
check "energy: one wrap is added back" energy_one_wrap
check "energy: wraps across sampling intervals are all counted" energy_two_wraps
check "energy: exit code of the command is passed through" energy_exit_code
check "energy: unknown zone fails with 2" energy_bad_zone

# ---- llm_load.py ----------------------------------------------------------------

LOAD=(python3 "${ROOT}/bench/llm_load.py")
LJ="${WORK}/load.json"

# stub <args...>: start tests/openai_stub.py and set STUB_PORT.
stub() {
    local fifo="${WORK}/stub.fifo"
    mkfifo "${fifo}"
    python3 "${ROOT}/tests/openai_stub.py" "$@" > "${fifo}" &
    STUB_PIDS+=($!)
    read -r _ STUB_PORT < "${fifo}"
    rm -f "${fifo}"
}

load_ovms_v3() {
    "${LOAD[@]}" --base-url "http://127.0.0.1:${STUB_PORT}/v3" --model stub --requests 3 --warmup 0 --out "${LJ}" || return 1
    pyassert "${LJ}" <<'EOF'
ph = j["phases"][0]
s = ph["samples"]
assert ph["concurrency"] == 1 and ph["ok"] == 3 and ph["errors"] == 0, ph
assert all(x["output_tokens"] == 16 and x["ok"] and x["finished"] for x in s), s
assert all(0.04 < x["ttft_s"] < 1 and x["first_chunk_s"] <= x["ttft_s"] < x["latency_s"] for x in s), s
assert all(x["latency_s"] > 0.05 + 15 * 0.009 for x in s), s
st = ph["stats"]["tokens_per_s"]
assert st["n"] == 3 and st["min"] <= st["median"] <= st["p90"] <= st["max"], st
assert j["config"]["base_url"].endswith("/v3")
EOF
}
load_concurrent() {
    "${LOAD[@]}" --base-url "http://127.0.0.1:${STUB_PORT}/v3" --model stub --requests 4 --concurrency 1,4 --warmup 0 --out "${LJ}" || return 1
    pyassert "${LJ}" <<'EOF'
seq, par = j["phases"]
assert (seq["concurrency"], par["concurrency"]) == (1, 4)
assert par["ok"] == 4 and par["output_tokens_total"] == 64
assert par["wall_s"] < 0.7 * seq["wall_s"], (par["wall_s"], seq["wall_s"])
assert par["aggregate_tokens_per_s"] > seq["aggregate_tokens_per_s"]
EOF
}
load_max_tokens() {
    "${LOAD[@]}" --base-url "http://127.0.0.1:${STUB_PORT}/v1/" --model stub --requests 1 --max-tokens 5 --warmup 0 --out "${LJ}" || return 1
    pyassert "${LJ}" <<'EOF'
assert j["phases"][0]["samples"][0]["output_tokens"] == 5
EOF
}
load_wrong_path() {
    "${LOAD[@]}" --base-url "http://127.0.0.1:${STUB_PORT}/nope" --model stub --requests 1 --warmup 0 --out "${LJ}"
    [ $? -eq 1 ] && grep -q 'HTTP 404' "${LJ}"
}
load_no_usage() {
    "${LOAD[@]}" --base-url "http://127.0.0.1:${STUB_PORT}/v3" --model stub --requests 1 --warmup 0 --out "${LJ}" || return 1
    pyassert "${LJ}" <<'EOF'
s = j["phases"][0]["samples"][0]
assert s["ok"] and s["output_tokens"] is None and s["tokens_per_s"] is None and s["ttft_s"] and s["chunks"] == 5, s
EOF
}

stub --tokens 16 --ttft 0.05 --itl 0.01
check "llm_load: OVMS /v3 stream, usage tokens, TTFT after the delay" load_ovms_v3
check "llm_load: concurrent phase overlaps requests" load_concurrent
check "llm_load: max_tokens is sent" load_max_tokens
check "llm_load: wrong path is an error and a non-zero exit" load_wrong_path
stub --tokens 4 --no-usage --close
check "llm_load: no usage chunk leaves tokens null; non-chunked stream parses" load_no_usage

# ---- noisy.sh -------------------------------------------------------------------

NOISY=("${ROOT}/bench/noisy.sh")
NJ="${WORK}/noisy.json"

noisy_efficient() {
    "${NOISY[@]}" start --cores efficient --workers 6 --dry-run > "${NJ}" || return 1
    pyassert "${NJ}" <<'EOF'
c = j["spec"]["containers"][0]
assert j["metadata"]["labels"]["k3d-gpu.io/cores"] == "efficient", j
assert c["resources"]["requests"]["cpu"] == "6" == c["resources"]["limits"]["cpu"]
assert "--cpu 6 " in c["command"][2] and "@sha256:" in c["image"]
EOF
}
noisy_unlabelled() {
    "${NOISY[@]}" start --workers 8 --cpu 2 --dry-run > "${NJ}" || return 1
    pyassert "${NJ}" <<'EOF'
c = j["spec"]["containers"][0]
assert "k3d-gpu.io/cores" not in j["metadata"]["labels"]
assert c["resources"]["requests"]["cpu"] == "2" and "--cpu 8 " in c["command"][2]
EOF
}
noisy_bad_cores() {
    "${NOISY[@]}" start --cores turbo --dry-run
    [ $? -eq 2 ]
}

check "noisy: efficient pod is labelled, sized and pinned to the image digest" noisy_efficient
check "noisy: unlabelled pod has no cores label; --cpu overrides the request" noisy_unlabelled
check "noisy: bad --cores is rejected" noisy_bad_cores

# ---- agent_trace.py ---------------------------------------------------------------

check "agent_trace: unit tests against the in-process stub" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/test_agent_trace.py"

# ---- uncore-phase (H29) -----------------------------------------------------------

check "uncore-phase: probe unit tests (order, sign test, verdict rules, knob restore)" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/uncore-phase/test_probe.py"

check "phase-steer: H20 probe unit tests" \
    env -u IMAGE PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/phase-steer/test_probe.py"

# ---- hetero-spec (H22) ---------------------------------------------------------------

check "hetero-spec: analyze.py applies the H22 outcome table" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/hetero-spec/test_analyze.py"
check "hetero-spec: probe.sh parses" bash -n "${ROOT}/bench/hetero-spec/probe.sh"
check "hetero-spec: host sample is one JSON object and gpu_busy is a number" \
    env -u SUDO_PASS H22_SELFTEST=1 bash "${ROOT}/bench/hetero-spec/probe.sh"
check "hetero-spec: prompts.jsonl has 8 prompts with id and text" \
    python3 -c 'import json, sys; p = [json.loads(l) for l in open(sys.argv[1])]; assert len(p) == 8 and all(x["id"] and x["text"] for x in p)' \
    "${ROOT}/bench/hetero-spec/prompts.jsonl"

# ---- research/ models -------------------------------------------------------------

check "research: agents-per-box ceiling selftest" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/research/agents-per-box-ceiling/ceiling.py" selftest
check "research: prefix-reuse model unit tests" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/research/prefix-reuse/test_prefix_model.py"
check "research: spec-decoding model unit tests" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/research/spec-decoding/test_specdec_model.py"
check "research: power-envelope model unit tests" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/research/tseries-envelope/test_envelope_model.py"
check "bench: sku-model probe scorer, client and plan (H24)" \
    python3 "${ROOT}/bench/sku-model/test_probe.py"
if command -v cc >/dev/null; then
    check "research: stream_probe.c compiles" \
        cc -fsyntax-only -fopenmp "${ROOT}/research/agents-per-box-ceiling/stream_probe.c"
fi

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
