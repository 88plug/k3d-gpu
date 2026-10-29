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

load_unique_prompts() {
    python3 -B - "${ROOT}/bench" <<'EOF'
import sys, types
sys.path.insert(0, sys.argv[1])
import llm_load as L
plain = types.SimpleNamespace(unique_prompts=False, prompt="p", run_id="r")
assert {L.prompt_for(plain) for _ in range(3)} == {"p"}
uniq = types.SimpleNamespace(unique_prompts=True, prompt="p", run_id="ab12")
got = [L.prompt_for(uniq) for _ in range(50)]
assert len(set(got)) == 50, got
assert all(g.startswith("[request ab12-") and g.endswith("] p") for g in got), got
EOF
}

load_extra_body() {
    python3 -B - "${ROOT}/bench" <<'EOF'
import sys, types
sys.path.insert(0, sys.argv[1])
import llm_load as L
a = types.SimpleNamespace(unique_prompts=False, prompt="p", run_id="r", model="m", max_tokens=4,
                          temperature=0, ignore_eos=True,
                          extra_body={"chat_template_kwargs": {"enable_thinking": False}})
b = L.request_body(a)
assert b["chat_template_kwargs"] == {"enable_thinking": False} and b["ignore_eos"] and b["max_tokens"] == 4, b
a.extra_body = None
assert "chat_template_kwargs" not in L.request_body(a)
EOF
}

stub --tokens 16 --ttft 0.05 --itl 0.01
check "llm_load: --unique-prompts gives every request its own prefix" load_unique_prompts
check "llm_load: --extra-body keys reach the request body" load_extra_body
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

noisy_stream_ecluster() {
    "${NOISY[@]}" start --cores ecluster --stressor stream --workers 4 --dry-run > "${NJ}" || return 1
    pyassert "${NJ}" <<'EOF'
c = j["spec"]["containers"][0]
assert j["metadata"]["labels"]["k3d-gpu.io/cores"] == "ecluster", j
assert "--stream 4 --stream-l3-size 128m " in c["command"][2] and "--cpu " not in c["command"][2]
assert c["resources"]["limits"] == {"cpu": "4", "memory": "1Gi"} == c["resources"]["requests"]
EOF
}
noisy_cpu_default_mem() {
    "${NOISY[@]}" start --workers 2 --dry-run > "${NJ}" || return 1
    pyassert "${NJ}" <<'EOF'
c = j["spec"]["containers"][0]
assert c["resources"]["limits"]["memory"] == "64Mi" and "--stream" not in c["command"][2]
EOF
}
noisy_bad_stream() {
    local rc
    "${NOISY[@]}" start --stressor bogus --dry-run; rc=$?
    [ "${rc}" -eq 2 ] || return 1
    "${NOISY[@]}" start --stressor stream --stream-bytes '1;x' --dry-run; rc=$?
    [ "${rc}" -eq 2 ]
}

check "noisy: stream neighbour on ecluster has a fixed footprint and 1Gi" noisy_stream_ecluster
check "noisy: cpu stressor keeps 64Mi and no stream args" noisy_cpu_default_mem
check "noisy: bad --stressor and --stream-bytes are rejected" noisy_bad_stream
check "noisy: efficient pod is labelled, sized and pinned to the image digest" noisy_efficient
check "noisy: unlabelled pod has no cores label; --cpu overrides the request" noisy_unlabelled
check "noisy: bad --cores is rejected" noisy_bad_cores

# ---- agent_trace.py ---------------------------------------------------------------

check "rdt-isolate: H26 analysis and client unit tests" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/rdt-isolate/test_analyze.py"
check "rdt-isolate: run.sh parses" bash -n "${ROOT}/bench/rdt-isolate/run.sh"

check "agent_trace: unit tests against the in-process stub" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/test_agent_trace.py"

check "bench: joule-pack (H21) unit tests" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/joule-pack/test_joulepack.py"
check "shared-prefix (H25): prompt generator and scorer" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/shared-prefix/test_probe.py"
check "flashnext (H60): h60.py byte classes and decision rule" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/flashnext/test_h60.py"
check "flashnext (H60): h60.sh parses" bash -n "${ROOT}/bench/flashnext/h60.sh"

# h60_void_rerun: h60.sh's own check() and phase() (lifted from the script) against a serving
# sampler with aiperf in the try-1 window of each arm's N=1 phase only. h54.py void exits 1 on a
# void window; the record must still be one JSON line, the re-run's record must win, and the
# score must use the same-hold Lg control.
h60_void_rerun() {
    local d="${WORK}/h60-void"
    rm -rf "${d}"
    mkdir -p "${d}/fn" "${d}/lg"
    : > "${d}/serving.jsonl"
    (
        set -uo pipefail
        # shellcheck disable=SC2034  # read by the check() eval'd from h60.sh
        PY=python3 H54="${ROOT}/bench/h54" SERVING="${d}/serving.jsonl" OUT="${d}"
        eval "$(sed -n '/^check() {/,/^}/p; /^phase() {/,/^}/p' "${ROOT}/bench/flashnext/h60.sh")"
        declare -F check phase >/dev/null || { echo "check()/phase() not found in h60.sh"; exit 1; }
        sample() { echo "{\"ts\": $1, \"seconds\": $2, \"aiperf_procs\": $3, \"serving_cpu\": 0.5}" >> "${SERVING}"; }
        idle() {  # a clean idle window holding one serving sample
            W0=$(date +%s.%N); sleep 0.3
            sample "$(date +%s.%N)" 0.2 0; sleep 0.1
            W1=$(date +%s.%N)
        }
        load() {  # load ARM NAME ...: try 1 overlaps an aiperf sample and decodes far off the band
            local f="${OUT}/$1/load-$2.json" tps
            if [ -e "${OUT}/$1/tried-$2" ]; then
                tps=$([ "$1" = fn ] && echo 19.0 || echo 48.0)
            else
                touch "${OUT}/$1/tried-$2"
                tps=1.0
                sample "$(python3 -c 'import time; print(time.time() + 0.4)')" 0.3 1
            fi
            sleep 0.8
            python3 -c 'import json, sys; json.dump({"phases": [{"samples": [{"ok": True, "decode_tokens_per_s": float(sys.argv[2])}] * 8}]}, open(sys.argv[1], "w"))' "${f}" "${tps}"
        }
        for a in lg fn; do
            idle "${a}"
            phase "${a}" n1 8 1 256 --ignore-eos
        done
        # check() on a void window prints exactly one JSON line (the GSM8K record embeds it)
        q0=$(date +%s.%N); sample "$(python3 -c 'import time; print(time.time() + 0.2)')" 0.1 1; sleep 0.4; q1=$(date +%s.%N)
        echo "{\"phase\": \"quality\", \"t0\": ${q0}, \"t1\": ${q1}, \"w0\": ${W0}, \"w1\": ${W1}, \"check\": $(check "${q0}" "${q1}")}" >> "${OUT}/fn/phases.jsonl"
        # a check that cannot run reads void, still one JSON line
        SERVING="${d}/missing.jsonl"
        check 1 2 > "${d}/check-missing.txt"
    ) || return 1
    echo '{"correct": 40}' > "${d}/fn/quality.json"
    echo '{"active_bytes": 6000000000}' > "${d}/active-bytes.json"
    echo '{"active_bytes": 2400000000}' > "${d}/active-bytes-lg.json"
    python3 "${ROOT}/bench/flashnext/h60.py" score "${d}" > "${d}/verdict.json" || return 1
    python3 - "${d}" <<'PY'
import json, os, sys
d = sys.argv[1]
for a in ("fn", "lg"):
    recs = [json.loads(x) for x in open(os.path.join(d, a, "phases.jsonl"))]
    n1 = [r for r in recs if r["phase"] == "n1"]
    assert [(r["try"], r["check"]["void"]) for r in n1] == [(1, True), (2, False)], (a, n1)
    assert n1[0]["check"]["reason"] == "aiperf ran", n1[0]
    assert os.path.exists(os.path.join(d, a, "load-n1-void1.json")), a
q = [json.loads(x) for x in open(os.path.join(d, "fn", "phases.jsonl"))][-1]
assert q["phase"] == "quality" and q["check"]["void"] is True, q
miss = open(os.path.join(d, "check-missing.txt")).read().splitlines()
assert [json.loads(x) for x in miss] == [{"void": True, "reason": "void check failed"}], miss
v = json.load(open(os.path.join(d, "verdict.json")))
assert v["why"] == [], v
assert (v["verdict"], v["stat"], v["control"]) == ("SUPPORTED", "e_fn / e_lg", "same-hold Lg"), v
assert v["fn_n1_decode_median"] == 19.0 and v["lg_n1_decode_median"] == 48.0, v
PY
}
check "flashnext (H60): a void try 1 then a clean re-run scores the re-run against the same-hold Lg" h60_void_rerun

check "apb (H7x): h7x.py applies the H7x decision rule" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/apb/test_h7x.py"
check "apb (H7x): h7x.sh parses" bash -n "${ROOT}/bench/apb/h7x.sh"
check "apb (H7x): h7x.sh defaults are H7's (16 CPUs, ABBAABBAAB then C, no node cpuset)" \
    sh -c 'test "$(env -u K3D_GPU_CLUSTER H7X_PRINT_CONFIG=1 bash "$1")" = "cluster=apb-h7x cpus=16 node_cpus= order=A B B A A B B A A B C vllm_mem="' _ "${ROOT}/bench/apb/h7x.sh"
# h7x_records_on_early_exit: a run whose first engine never comes up (exit 2, as smokes 1-3)
# still records the k3d-gpu commit and the node image. h7x.sh runs from a copy of the repo
# layout with scripts/k3d-gpu, helm, kubectl, docker, k3d and git as shims; the rendered
# manifests are smoke 6's records.
h7x_records_on_early_exit() {
    local w=${WORK}/h7x rec=${ROOT}/bench/xhost/results/2026-10-21/H7x-smokes/smoke6/h7x-20261021-065424 out rc
    mkdir -p "${w}/bench/apb" "${w}/scripts" "${w}/integrations/eat" "${w}/bin" "${w}/chart" "${w}/models" "${w}/out"
    cp "${ROOT}/bench/apb/h7x.sh" "${ROOT}/bench/apb/stage.py" "${ROOT}/bench/apb/h7x.py" "${w}/bench/apb/"
    cp "${ROOT}/integrations/eat/vllm-values.yaml" "${w}/integrations/eat/"
    cp "${rec}/values.yaml" "${w}/chart/xeon-values.yaml"
    printf '#!/bin/sh\ncase $1 in agent) cat "%s" ;; up) exit 0 ;; *) exit 1 ;; esac\n' "${rec}/agent.yaml" > "${w}/scripts/k3d-gpu"
    printf '#!/bin/sh\ncat "%s"\n' "${rec}/eat.yaml" > "${w}/bin/helm"
    printf '#!/bin/sh\ncase "$*" in *"rollout status"*) exit 1 ;; esac\nexit 0\n' > "${w}/bin/kubectl"
    printf '#!/bin/sh\ncase $1 in ps) echo k3d-t-server-0 ;; inspect) echo "node:img sha256:feed" ;; esac\nexit 0\n' > "${w}/bin/docker"
    printf '#!/bin/sh\nexit 0\n' > "${w}/bin/k3d"
    printf '#!/bin/sh\ncase "$*" in *"rev-parse HEAD"*) echo c0ffee ;; *) exit 1 ;; esac\n' > "${w}/bin/git"
    chmod +x "${w}/scripts/k3d-gpu" "${w}/bin/"*
    PATH="${w}/bin:${PATH}" H7X_SMOKE=1 H7X_ORDER=A H7X_READY_S=1 H7X_NODE_CPUS=0-1 K3D_GPU_CLUSTER=t H7X_CHART_CHECK=0 \
        APB_MODELS="${w}/models" APB_EAT_CHART="${w}/chart" APB_OUT="${w}/out" bash "${w}/bench/apb/h7x.sh"
    rc=$?
    out=$(echo "${w}"/out/h7x-*)
    [ "${rc}" = 2 ] || { echo "exit ${rc}, want 2 (engine not up)"; return 1; }
    grep -qx c0ffee "${out}/commit.txt" || { echo "commit.txt missing or wrong"; return 1; }
    grep -qx "node:img sha256:feed" "${out}/node-image.txt" || { echo "node-image.txt missing or wrong"; return 1; }
}
if python3 -c 'import yaml' 2>/dev/null; then
    check "apb (H7x): a run cut before its blocks still records the commit and node image" h7x_records_on_early_exit
fi
# h7x_refuses_other_chart: B and C are EAT release-1.1.0's stock (H7x amendment V1), so h7x.sh
# stops before rendering when the chart's files do not have that release's sha256.
h7x_refuses_other_chart() {
    local w=${WORK}/h7x-chart out rc
    mkdir -p "${w}/bench/apb" "${w}/chart/templates" "${w}/out"
    cp "${ROOT}/bench/apb/h7x.sh" "${w}/bench/apb/"
    printf 'image:\n  tag: "v0.24.0"\n' > "${w}/chart/xeon-values.yaml"
    : > "${w}/chart/values.yaml"
    : > "${w}/chart/templates/deployment.yaml"
    out=$(H7X_RENDER_ONLY=1 K3D_GPU_CLUSTER=t APB_MODELS="${w}" APB_EAT_CHART="${w}/chart" APB_OUT="${w}/out" \
        bash "${w}/bench/apb/h7x.sh" 2>&1)
    rc=$?
    [ "${rc}" = 1 ] || { echo "exit ${rc}, want 1"; return 1; }
    echo "${out}" | grep -q "is not EAT release-1.1.0's chart" || { echo "no chart message: ${out}"; return 1; }
    [ ! -e "$(echo "${w}"/out/h7x-*)/eat.yaml" ] || { echo "rendered B from the wrong chart"; return 1; }
}
check "apb (H7x): h7x.sh refuses a chart that is not EAT release-1.1.0's" h7x_refuses_other_chart
check "apb (H7x): B and C run EAT release-1.1.0's v0.28.0, pinned by digest in h7x.sh" \
    sh -c 'grep -qx "B_TAG=v0.28.0@sha256:197354e6475e638eb5ccca09beb078488046a82de0e204e7429bf1a804df9647" "$1" && grep -q -- "--set image.tag=\"\${B_TAG}\"" "$1"' _ "${ROOT}/bench/apb/h7x.sh"
check "shared-prefix (H25): run.sh parses" bash -n "${ROOT}/bench/shared-prefix/run.sh"
check "shared-prefix (H25): run.sh pins no node cpuset by default" \
    sh -c 'test "$(env -u CLUSTER -u NODE_CPUS SHARED_PREFIX_PRINT_CONFIG=1 bash "$1")" = "cluster=k3dgpu-i-shared-prefix node_cpus="' _ "${ROOT}/bench/shared-prefix/run.sh"
check "shared-prefix (H25x): run.sh takes a node cpuset" \
    sh -c 'test "$(SHARED_PREFIX_PRINT_CONFIG=1 CLUSTER=k3dgpu-x-h25 NODE_CPUS=0-23,48-71 bash "$1")" = "cluster=k3dgpu-x-h25 node_cpus=0-23,48-71"' _ "${ROOT}/bench/shared-prefix/run.sh"
check "amx-autoquant (H27): --dry-run names the pinned weight revisions" \
    sh -c 'o=$(bash "$1" --dry-run) && echo "$o" | grep -qx "  revision int4 b467368d16b75df14055562fe927ae8e1f15f7ef" && echo "$o" | grep -qx "  revision int8 fd9c3cc8adf942151e48904b7341a57eae58d8d5" && echo "$o" | grep -qx "  revision fp16 00298f1c614ffdc000814d8cbe1b7613aa73cdca"' _ "${ROOT}/bench/amx-autoquant/probe.sh"
check "zero-copy-models (H28): score.py applies the H28 outcome table" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/zero-copy-models/test_score.py"
check "zero-copy-models (H28): plain cluster create adds no k3s flag by default" \
    sh -c 'test "$(env -u K3D_GPU_K3S_ARGS bash "$1" create-args)" = ""' _ "${ROOT}/bench/zero-copy-models/probe.sh"
check "zero-copy-models (H28x): K3D_GPU_K3S_ARGS reaches the server as --k3s-arg (crescent C1)" \
    sh -c 'test "$(K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=nodefs.available<1% --disable=traefik" bash "$1" create-args)" = "--k3s-arg --kubelet-arg=eviction-hard=nodefs.available<1%@server:* --k3s-arg --disable=traefik@server:*"' _ "${ROOT}/bench/zero-copy-models/probe.sh"
check "zero-copy-models (H28x R1): run creates WORK; a WORK it cannot write stops before the lock, load and cluster" \
    "${ROOT}/tests/zero_copy_workdir_test.sh"
check "crescent (H56): hold.sh locks, slotted gate and scoped cleanup (no skips)" \
    env CRESCENT_HOLD_STRICT=1 "${ROOT}/tests/crescent_hold_test.sh"
check "crescent (H56): serving slot covariate, lockwait and the H56 scorer" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/eatflow/crescent/test_crescent.py"
check "crescent (H56): h56.sh parses and prints its order, caps, gate maxima and bounds" \
    sh -c 'bash -n "$1" && test "$(H56_PRINT_CONFIG=1 bash "$1" 1)" = "order=AWWAAWWA a=2400/2370/720 sibling=3000/2970/1800 sync_max=840 gate_max=600/600/120 bound_s=A:3390,W:3990"' _ "${ROOT}/bench/eatflow/crescent/h56.sh"
check "crescent (H56): live.sh without a slot keeps its names, ports and DevUI" \
    sh -c 'test "$(env -u LIVE_SLOT -u LIVE_NODE_CPUS LIVE_PRINT_SLOT=1 LIVE_BLUEPRINT=/nonexistent LIVE_PY=python3 bash "$1" A1 "$2/live-s")" = "cluster=k3dgpu-a-a1 ports=18630,18640,18620,18600,18650,18660 devui=127.0.0.1 node_cpus="' _ "${ROOT}/bench/eatflow/live.sh" "${WORK}"
check "crescent (H56): live.sh slot 1 moves the cluster, ports and DevUI" \
    sh -c 'test "$(env -u LIVE_NODE_CPUS LIVE_SLOT=1 LIVE_PRINT_SLOT=1 LIVE_BLUEPRINT=/nonexistent LIVE_PY=python3 bash "$1" A1 "$2/live-s")" = "cluster=k3dgpu-a-s1-a1 ports=18730,18740,18720,18700,18750,18760 devui=127.0.0.3 node_cpus="' _ "${ROOT}/bench/eatflow/live.sh" "${WORK}"
check "crescent (H56): live.sh refuses a slot outside agent mode and arm A1" \
    sh -c 'LIVE_SLOT=1 LIVE_MODE=pairs LIVE_PRINT_SLOT=1 LIVE_BLUEPRINT=/nonexistent LIVE_PY=python3 bash "$1" H50 "$2/live-s"; test $? = 2 && { LIVE_SLOT=0 LIVE_PRINT_SLOT=1 LIVE_BLUEPRINT=/nonexistent LIVE_PY=python3 bash "$1" B1 "$2/live-s"; test $? = 2; }' _ "${ROOT}/bench/eatflow/live.sh" "${WORK}"
check "xhost: hold.sh parses" bash -n "${ROOT}/bench/xhost/hold.sh"
check "xhost: clusters.py selects only k3dgpu-x-* clusters" \
    sh -c 'test "$(printf "%s" "[{\"name\":\"k3dgpu-x-h25\"},{\"name\":\"k3dgpu-e-h50\"},{\"name\":\"k3dgpu-x-h28\"},{\"name\":\"k3dgpu-h52\"},{\"name\":\"xk3dgpu-x-a\"}]" | python3 "$1")" = "k3dgpu-x-h25 k3dgpu-x-h28"' _ "${ROOT}/bench/xhost/clusters.py"

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
check "hetero-spec: h34.py applies the H34 outcome table" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/bench/hetero-spec/test_h34.py"
check "hetero-spec: h34.sh parses" bash -n "${ROOT}/bench/hetero-spec/h34.sh"
check "hetero-spec: h34.sh keeps the i9 defaults (cluster, vendors, no node cpuset)" \
    sh -c 'test "$(H34_PRINT_CONFIG=1 bash "$1")" = "cluster=k3dgpu-i-spec-decode-cpu vendors=intel,hybrid node_cpus="' _ "${ROOT}/bench/hetero-spec/h34.sh"
check "hetero-spec: h34.sh takes the cross-host cluster, vendors and node cpuset (H34x)" \
    sh -c 'test "$(H34_PRINT_CONFIG=1 H34_CLUSTER=k3dgpu-x-h34 H34_VENDORS=none H34_NODE_CPUS=0-23,48-71 bash "$1")" = "cluster=k3dgpu-x-h34 vendors=none node_cpus=0-23,48-71"' _ "${ROOT}/bench/hetero-spec/h34.sh"
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
check "research: thermal-resource headroom model unit tests" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/research/thermal-resource/test_headroom_model.py"
check "research: thermal-resource headroom model selftest" \
    env PYTHONDONTWRITEBYTECODE=1 python3 "${ROOT}/research/thermal-resource/headroom_model.py" selftest

# ---- thermal-resource probe (H23) ---------------------------------------------------

check "thermal-resource probe: DRY_RUN prints the three arms" \
    env DRY_RUN=1 OUT="${WORK}/h23" bash -c '"$1/bench/thermal-resource/probe.sh" > "$2"' _ "${ROOT}" "${WORK}/h23.yaml"
# h23_arms: B, T, C lists of 24 pods each; 12 hot without --cpu-load, 12 cool with it;
# T requests per-type watts, C one slot, B neither; requests equal limits.
h23_arms() {
    python3 - "${WORK}/h23.yaml" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
assert len(docs) == 3, len(docs)
for arm, d in zip("BTC", docs):
    pods = d["items"]
    assert len(pods) == 24, (arm, len(pods))
    hot = [p for p in pods if p["metadata"]["labels"]["h23-kind"] == "hot"]
    cool = [p for p in pods if p["metadata"]["labels"]["h23-kind"] == "cool"]
    assert len(hot) == 12 and len(cool) == 12
    for p in pods:
        c = p["spec"]["containers"][0]
        req, lim = c["resources"]["requests"], c["resources"]["limits"]
        assert req == lim and req["cpu"] == "1", req
        assert ("--cpu-load" in c["command"]) == (p in cool)
        assert c["imagePullPolicy"] == "Never" and p["spec"]["restartPolicy"] == "Never"
        if arm == "B":
            assert set(req) == {"cpu"}
        elif arm == "T":
            assert set(req) == {"cpu", "power.k3d-gpu.io/watts"}
            assert req["power.k3d-gpu.io/watts"] == ("6" if p in hot else "3")
        else:
            assert set(req) == {"cpu", "k3d-gpu.io/slots"} and req["k3d-gpu.io/slots"] == "1"
PY
}
if python3 -c 'import yaml' 2>/dev/null; then
    check "thermal-resource probe: arms B/T/C request what H23 pre-registered" h23_arms
fi
# H7 and H8 ran EAT's vLLM CPU arm on v0.24.0 (their run headers). Their scripts pin that image
# themselves, so a newer tag in the shared overlay integrations/eat/vllm-values.yaml does not
# change what they reproduce.
check "apb (H7, H8): h7.sh and arm.sh eat-vllm-cpu pin the vLLM CPU image those rows ran" \
    sh -c 'for f in "$@"; do grep -q -- "--set image.tag=\"v0.24.0@sha256:b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1\"" "$f" || { echo "$f"; exit 1; }; done' \
    _ "${ROOT}/bench/apb/h7.sh" "${ROOT}/bench/apb/arm.sh"
if command -v cc >/dev/null; then
    check "research: stream_probe.c compiles" \
        cc -fsyntax-only -fopenmp "${ROOT}/research/agents-per-box-ceiling/stream_probe.c"
fi

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
