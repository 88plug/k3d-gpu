#!/usr/bin/env bash
# `k3d-gpu tune` tests. No cluster, no hardware: a fake sysfs tree
# (K3D_GPU_SYSROOT), a stub kubectl on PATH and a fake model server
# (tests/fake_ovms.py) whose speed depends on the deployed config.
#
# Usage: tests/tune_test.sh
#   K3D_GPU_SCRIPT=<launcher>  test another build of scripts/k3d-gpu
#   K3D_GPU_BENCH=<dir>        bench/llm_load.py + energy.sh for the trial tests
#   TUNE_E2E_FULL=1            also run a whole (small) tune against the fake server

set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1

TESTS="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "${TESTS}")"
K3D_GPU="${K3D_GPU_SCRIPT:-${ROOT}/scripts/k3d-gpu}"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0 SKIPS=0

ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
bad()  { FAILS=$((FAILS + 1)); echo "FAIL $1"; [ -z "${2:-}" ] || printf '%s\n' "$2" | sed 's/^/     | /'; }
skip() { SKIPS=$((SKIPS + 1)); echo "skip $1"; }

echo "== tune.py unit tests (simulated server)"
if python3 "${TESTS}/tune_test.py" >"${WORK}/py.log" 2>&1; then
    ok "tune_test.py ($(grep -Eo 'Ran [0-9]+ tests' "${WORK}/py.log"))"
else
    bad "tune_test.py" "$(tail -30 "${WORK}/py.log")"
fi

# host <name> <cpu model> [p-cores e-cores]: fake /proc and /sys for the fingerprint.
host() {
    local r="${WORK}/$1"
    mkdir -p "${r}/proc/sys/kernel" "${r}/sys/devices/system/cpu"
    printf 'processor\t: 0\nmodel name\t: %s\nflags\t\t: fpu sse2 avx2 avx_vnni\n' "$2" > "${r}/proc/cpuinfo"
    echo "6.6.128-1-MANJARO" > "${r}/proc/sys/kernel/osrelease"
    echo "0-31" > "${r}/sys/devices/system/cpu/online"
    if [ -n "${3:-}" ]; then
        mkdir -p "${r}/sys/devices/cpu_core" "${r}/sys/devices/cpu_atom"
        echo "$3" > "${r}/sys/devices/cpu_core/cpus"
        echo "$4" > "${r}/sys/devices/cpu_atom/cpus"
    fi
    echo "${r}"
}

# Stub kubectl: a single hybrid node with an Intel iGPU. It saves applied
# manifests, answers exec for the warm-up and cpu.stat, and port-forward starts
# the fake server with the last applied manifest.
mkdir -p "${WORK}/bin"
cat > "${WORK}/bin/kubectl" <<EOF
#!/usr/bin/env bash
STATE="${WORK}/kube"
mkdir -p "\${STATE}"
args=" \$* "
case "\${args}" in
    # apply bumps the Deployment's generation only when the manifest changed,
    # as the API server does for an unchanged pod template.
    *" apply -f - "*) cat > "\${STATE}/new.yaml"
        if ! cmp -s "\${STATE}/new.yaml" "\${STATE}/applied.yaml"; then
            echo \$(( \$(cat "\${STATE}/generation" 2>/dev/null || echo 0) + 1 )) > "\${STATE}/generation"
        fi
        mv "\${STATE}/new.yaml" "\${STATE}/applied.yaml"; echo "applied" ;;
    *"jsonpath={.metadata.generation}"*) cat "\${STATE}/generation" 2>/dev/null || true ;;
    *" rollout restart "*) echo "\$*" >> "\${STATE}/restarts" ;;
    *" port-forward "*) port=\${args##* svc/ovms }; port=\${port%%:*}; exec python3 "${TESTS}/fake_ovms.py" "\${port}" "\${STATE}/applied.yaml" ;;
    *" exec "*"cpu.stat"*) n=\$(cat "\${STATE}/cpu" 2>/dev/null || echo 1000000); echo \$((n + 2000000)) > "\${STATE}/cpu"; echo "usage_usec \${n}" ;;
    *" exec "*) echo '{"choices":[{"message":{"content":"I am a test model."}}]}' ;;
    *" create --raw "*) echo '{"choices":[{"message":{"content":"I am a test model."}}]}' ;;
    *"allocatable.cpu"*) echo "32" ;;
    *'\$k, \$v := .status.allocatable'*) printf 'cpu 32\nmemory 65536Mi\ngpu.intel.com/i915 1\n' ;;
    *" get nodes -l "*"hybrid-cpu"*) echo "node/k3d-gpu-server-0" ;;
    *" get nodes -l "*) ;;
    *" get nodes"*) echo "k3d-gpu-server-0   Ready" ;;
    *) ;;
esac
exit 0
EOF
chmod +x "${WORK}/bin/kubectl"
STUB_PATH="${WORK}/bin:${PATH}"

run() { env PATH="${STUB_PATH}" K3D_GPU_TUNE_DIR="${WORK}/profiles" "$@"; }

echo "== fingerprint"
h1=$(host h1 "13th Gen Intel(R) Core(TM) i9-13900T" 0-15 16-31)
h2=$(host h2 "13th Gen Intel(R) Core(TM) i9-13900T" 0-15 16-31)
h3=$(host h3 "13th Gen Intel(R) Core(TM) i9-13900T" 0-15 16-23)
fp() { run env K3D_GPU_SYSROOT="$1" "${@:2}" "${K3D_GPU}" tune fingerprint | sed -n '1s/ .*//p'; }
a=$(fp "${h1}") b=$(fp "${h2}") c=$(fp "${h3}") d=$(fp "${h1}" K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-8B-int4-ov)
[[ "${a}" =~ ^[0-9a-f]{16}$ ]] && ok "fingerprint is 16 hex chars" || bad "fingerprint format" "${a}"
[ "${a}" = "${b}" ] && ok "same hardware, same fingerprint" || bad "same hardware, same fingerprint" "${a} vs ${b}"
[ "${a}" != "${c}" ] && ok "different E-core set, different fingerprint" || bad "E-core set ignored"
[ "${a}" != "${d}" ] && ok "different model, different fingerprint" || bad "model ignored"

echo "== profile application (what 'agent up' exports)"
mkdir -p "${WORK}/profiles"
python3 - "${ROOT}/console/tune.py" "${WORK}/profiles/${a}.json" "${a}" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("tune", sys.argv[1]); t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
winner = {"device": "GPU", "model": "OpenVINO/Qwen3-4B-int4-ov", "cores": "efficient", "cpus": 2,
          "plugin_config": {"PERFORMANCE_HINT": "THROUGHPUT"}, "max_num_seqs": 64, "cache_size": 0,
          "enable_prefix_caching": True}
t.write_profile(sys.argv[2], {"decision": "tuned", "winner": winner, "objective": "throughput"}, sys.argv[3], {}, {})
PY
envout() { run env K3D_GPU_SYSROOT="${h1}" "$@" "${K3D_GPU}" tune env; }
out=$(envout)
grep -qx 'K3D_GPU_AGENT_PLUGIN_CONFIG={"PERFORMANCE_HINT":"THROUGHPUT"}' <<< "${out}" \
    && grep -qx 'K3D_GPU_AGENT_CORES=efficient' <<< "${out}" && ok "profile exports its overrides" || bad "profile exports" "${out}"
out=$(envout K3D_GPU_AGENT_CORES=performance)
! grep -q 'K3D_GPU_AGENT_CORES' <<< "${out}" && grep -q 'K3D_GPU_AGENT_DEVICE=GPU' <<< "${out}" \
    && ok "a variable the user set wins over the profile" || bad "user override" "${out}"
out=$(envout K3D_GPU_TUNE=off)
[ -z "${out}" ] && ok "K3D_GPU_TUNE=off ignores the profile" || bad "K3D_GPU_TUNE=off" "${out}"
out=$(run env K3D_GPU_SYSROOT="${h3}" "${K3D_GPU}" tune env)
[ -z "${out}" ] && ok "another host's profile does not apply" || bad "fingerprint gate" "${out}"
sed -i 's/"device": "GPU"/"device": "GPU\\n  - --evil"/' "${WORK}/profiles/${a}.json"
out=$(envout)
[ -z "${out}" ] && ok "a tampered profile exports nothing" || bad "tampered profile" "${out}"

echo "== search space from cluster facts"
out=$(run env K3D_GPU_SYSROOT="${h1}" "${K3D_GPU}" tune space --n0 12 2>&1)
if grep -q '^search space: [0-9]* configs on this host; first rung tries 12 + the default' <<< "${out}" \
   && grep -q 'K3D_GPU_AGENT_DEVICE=GPU' <<< "${out}" && grep -q 'K3D_GPU_AGENT_DEVICE=CPU' <<< "${out}" \
   && grep -q 'K3D_GPU_AGENT_CORES=performance' <<< "${out}"; then
    ok "space covers CPU and the iGPU, pinned to core types"
else
    bad "space" "${out}"
fi
! grep -q 'INFERENCE_PRECISION_HINT":"bf16' <<< "${out}" && ok "no bf16 hint on an AVX2 host" || bad "bf16 on AVX2"

echo "== trials"
BENCH="${K3D_GPU_BENCH:-${ROOT}/bench}"
if [ ! -f "${BENCH}/llm_load.py" ]; then
    skip "trial and full tune (no bench/llm_load.py; set K3D_GPU_BENCH)"
else
    rm -f "${WORK}/profiles/"*.json
    out=$(run env K3D_GPU_SYSROOT="${h1}" "${K3D_GPU}" agent manifest 2>/dev/null)
    grep -q -- '--target_device=CPU' <<< "${out}" && ! grep -q -- '--plugin_config' <<< "${out}" \
        && ok "agent manifest without a profile is the default" || bad "default manifest" "${out}"
    out=$(run env K3D_GPU_SYSROOT="${h1}" K3D_GPU_AGENT_PLUGIN_CONFIG='{"PERFORMANCE_HINT":"THROUGHPUT"}' \
        K3D_GPU_AGENT_MAX_NUM_SEQS=64 K3D_GPU_AGENT_CORES=efficient K3D_GPU_AGENT_DEVICE=HETERO:GPU,CPU \
        "${K3D_GPU}" agent manifest 2>/dev/null)
    if grep -qF -- "- '--plugin_config={\"PERFORMANCE_HINT\":\"THROUGHPUT\"}'" <<< "${out}" \
       && grep -q -- '--max_num_seqs=64' <<< "${out}" && grep -q 'k3d-gpu.io/cores: efficient' <<< "${out}" \
       && grep -q -- '--target_device=HETERO:GPU,CPU' <<< "${out}" && grep -q 'gpu.intel.com/i915: 1' <<< "${out}"; then
        ok "agent manifest carries the serving knobs"
    else
        bad "manifest knobs" "${out}"
    fi
    if python3 -c 'import sys, yaml' 2>/dev/null; then
        python3 -c 'import sys, yaml; list(yaml.safe_load_all(sys.stdin))' <<< "${out}" \
            && ok "tuned manifest is valid YAML" || bad "tuned manifest YAML"
    fi
    out=$(run env K3D_GPU_SYSROOT="${h1}" K3D_GPU_AGENT_PLUGIN_CONFIG="{\"A\":\"1'\"}" "${K3D_GPU}" agent manifest 2>&1)
    grep -q 'must be a flat JSON object' <<< "${out}" && ok "a quote in plugin_config is refused" || bad "quote refused" "${out}"

    t="${WORK}/trial"
    mkdir -p "${t}/base" "${t}/tp"
    echo '{"baseline": true}' > "${t}/base/config.json"
    echo '{"device":"GPU","model":"OpenVINO/Qwen3-4B-int4-ov","cores":"performance","cpus":2,"plugin_config":{"PERFORMANCE_HINT":"THROUGHPUT"},"max_num_seqs":256,"cache_size":0,"enable_prefix_caching":true}' > "${t}/tp/config.json"
    for d in base tp; do
        echo '{"requests": 4, "max_tokens": 32, "repeats": 2, "concurrency": [1, 2]}' > "${t}/${d}/budget.json"
        run env K3D_GPU_SYSROOT="${h1}" K3D_GPU_BENCH="${BENCH}" "${K3D_GPU}" tune _trial \
            "${t}/${d}/config.json" "${t}/${d}/budget.json" "${t}/${d}/result.json" >/dev/null 2>&1
    done
    verdict=$(python3 - "${ROOT}/console/tune.py" "${t}" <<'PY'
import importlib.util, json, statistics, sys
spec = importlib.util.spec_from_file_location("tune", sys.argv[1]); t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
def tps(d):
    r = json.load(open(f"{sys.argv[2]}/{d}/result.json"))
    assert r["ok"], r
    ms = [t.run_metrics(run, 10) for run in r["runs"]]
    assert len(ms) == 2 and all(m["quality"] == 1.0 and m["tokens_per_cpu_s"] for m in ms), ms
    return statistics.median(m["tokens_per_s_c1"] for m in ms)
b, f = tps("base"), tps("tp")
print("ok" if f > 1.5 * b else f"slow {b:.0f} vs {f:.0f}")
PY
)
    [ "${verdict}" = ok ] && ok "trial deploys the config and the fake server's speed follows it" \
        || bad "trial" "${verdict}$(cat "${t}"/*/deploy.log "${t}"/*/load.log 2>/dev/null | tail -20)"

    # The default trial and a candidate that leaves a knob unset deploy
    # exactly that, whatever K3D_GPU_AGENT_* the user has exported.
    trial_with_user_env() {
        run env K3D_GPU_SYSROOT="${h1}" K3D_GPU_BENCH="${BENCH}" K3D_GPU_AGENT_CORES=efficient \
            K3D_GPU_AGENT_PLUGIN_CONFIG='{"PERFORMANCE_HINT":"LATENCY"}' K3D_GPU_AGENT_MAX_NUM_SEQS=8 \
            "${K3D_GPU}" tune _trial "${t}/$1/config.json" "${t}/$1/budget.json" "${t}/$1/result.json" >/dev/null 2>&1
    }
    mkdir -p "${t}/plain"
    cp "${t}/base/budget.json" "${t}/plain/"
    echo '{"device":"GPU","model":"OpenVINO/Qwen3-4B-int4-ov","cores":null,"cpus":2,"plugin_config":{},"max_num_seqs":256,"cache_size":0,"enable_prefix_caching":true}' > "${t}/plain/config.json"
    for d in base plain tp; do
        trial_with_user_env "${d}"
        m=$(cat "${WORK}/kube/applied.yaml")
        want_pc=""; [ "${d}" != tp ] || want_pc='--plugin_config={"PERFORMANCE_HINT":"THROUGHPUT"}'
        if ! grep -q 'LATENCY\|cores: efficient\|--max_num_seqs=8' <<< "${m}" \
           && { [ -n "${want_pc}" ] || ! grep -q -- '--plugin_config' <<< "${m}"; } \
           && { [ -z "${want_pc}" ] || grep -qF -- "${want_pc}" <<< "${m}"; }; then
            ok "trial '${d}' ignores the user's K3D_GPU_AGENT_* overrides"
        else
            bad "trial '${d}' inherits user env" "$(grep -n 'plugin_config\|cores:\|max_num_seqs' <<< "${m}")"
        fi
    done
    # ...but the model under tune is kept: the default trial of a tune of
    # another model deploys that model, not the agent's default one.
    run env K3D_GPU_SYSROOT="${h1}" K3D_GPU_BENCH="${BENCH}" K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-4B-int8-ov \
        "${K3D_GPU}" tune _trial "${t}/base/config.json" "${t}/base/budget.json" "${t}/base/result.json" >/dev/null 2>&1
    m=$(grep -- '--source_model=' "${WORK}/kube/applied.yaml")
    if grep -q -- '--source_model=OpenVINO/Qwen3-4B-int8-ov' <<< "${m}"; then
        ok "the default trial keeps the model under tune"
    else
        bad "default trial model" "${m}"
    fi

    # The same config twice in a row: apply changes nothing, so the trial
    # restarts the pod itself rather than measure a warm prefix cache.
    rm -f "${WORK}/kube/restarts"
    for d in base tp tp; do
        run env K3D_GPU_SYSROOT="${h1}" K3D_GPU_BENCH="${BENCH}" "${K3D_GPU}" tune _trial \
            "${t}/${d}/config.json" "${t}/${d}/budget.json" "${t}/${d}/result.json" >/dev/null 2>&1
    done
    n=$(grep -c 'rollout restart deployment/ovms' "${WORK}/kube/restarts" 2>/dev/null || true)
    if [ "${n:-0}" = 1 ]; then ok "a repeated config gets a fresh pod"; else bad "repeated config restarts" "restarts: ${n:-0}"; fi
    # ...unless the budget says warm is fine (incremental screening).
    mkdir -p "${t}/warm"
    cp "${t}/tp/config.json" "${t}/warm/"
    echo '{"requests": 4, "max_tokens": 32, "repeats": 1, "concurrency": [1], "cold": false}' > "${t}/warm/budget.json"
    rm -f "${WORK}/kube/restarts"
    run env K3D_GPU_SYSROOT="${h1}" K3D_GPU_BENCH="${BENCH}" "${K3D_GPU}" tune _trial \
        "${t}/warm/config.json" "${t}/warm/budget.json" "${t}/warm/result.json" >/dev/null 2>&1
    n=$(grep -c 'rollout restart deployment/ovms' "${WORK}/kube/restarts" 2>/dev/null || true)
    if [ "${n:-0}" = 0 ] && grep -q '"ok": true' "${t}/warm/result.json"; then
        ok "a warm-budget trial of the deployed config measures the running pod"
    else
        bad "warm trial restarts" "restarts: ${n:-0}; $(head -c 300 "${t}/warm/result.json" 2>/dev/null)"
    fi

    if [ "${TUNE_E2E_FULL:-}" = 1 ]; then
        # Null server: no config changes its speed, so nothing may be crowned.
        out=$(run env K3D_GPU_SYSROOT="${h1}" K3D_GPU_BENCH="${BENCH}" FAKE_OVMS_FLAT=1 "${K3D_GPU}" tune run --full \
            --objective throughput --precisions int4 --no-hetero --n0 6 --n 5 --concurrency 1,2 2>&1)
        [ -z "${TUNE_TEST_LOG:-}" ] || printf '%s\n' "${out}" > "${TUNE_TEST_LOG}.null"
        grep -q '^decision: default' <<< "${out}" && ok "full tune on a null server keeps the default" \
            || bad "full tune, null server" "$(tail -30 <<< "${out}")"
        out=$(run env K3D_GPU_SYSROOT="${h1}" "${K3D_GPU}" tune env)
        [ -z "${out}" ] && ok "a 'default' profile exports nothing" || bad "default profile exports" "${out}"
        # The fake server halves per-token time for PERFORMANCE_HINT=THROUGHPUT;
        # --n0 30 includes every one-knob change of the default, that one too.
        out=$(run env K3D_GPU_SYSROOT="${h1}" K3D_GPU_BENCH="${BENCH}" "${K3D_GPU}" tune run --full \
            --objective throughput --precisions int4 --no-hetero --n0 30 --n 5 --concurrency 1,2 2>&1)
        [ -z "${TUNE_TEST_LOG:-}" ] || printf '%s\n' "${out}" > "${TUNE_TEST_LOG}"
        if grep -q '^decision: tuned' <<< "${out}" && grep -q 'ACCEPT.*PERFORMANCE_HINT":"THROUGHPUT' <<< "${out}"; then
            ok "full tune crowns the config the fake server rewards"
        else
            bad "full tune" "$(tail -30 <<< "${out}")"
        fi
        out=$(run env K3D_GPU_SYSROOT="${h1}" "${K3D_GPU}" tune env)
        grep -q 'THROUGHPUT' <<< "${out}" && ok "the tuned profile is what 'agent up' applies next" || bad "profile after tune" "${out}"
        grep -q '"PERFORMANCE_HINT":"THROUGHPUT"' "${WORK}/kube/applied.yaml" \
            && ok "tune ends with the winner deployed" || bad "final deploy" "$(grep -n plugin "${WORK}/kube/applied.yaml")"
        # Incremental (the default 'tune run'): from a fresh default, the
        # THROUGHPUT hint is one knob away and must win; the next run starts
        # from it and keeps it.
        run env K3D_GPU_SYSROOT="${h1}" "${K3D_GPU}" tune reset >/dev/null 2>&1
        out=$(run env K3D_GPU_SYSROOT="${h1}" K3D_GPU_BENCH="${BENCH}" "${K3D_GPU}" tune run \
            --objective throughput --precisions int4 --no-hetero --n 5 --concurrency 1,2 --budget 30 2>&1)
        [ -z "${TUNE_TEST_LOG:-}" ] || printf '%s\n' "${out}" > "${TUNE_TEST_LOG}.step1"
        if grep -q '^decision: challenger' <<< "${out}" && grep -q 'ACCEPT.*PERFORMANCE_HINT":"THROUGHPUT' <<< "${out}"; then
            ok "incremental tune crowns the rewarded neighbour"
        else
            bad "incremental tune" "$(tail -30 <<< "${out}")"
        fi
        out=$(run env K3D_GPU_SYSROOT="${h1}" K3D_GPU_BENCH="${BENCH}" "${K3D_GPU}" tune run \
            --objective throughput --precisions int4 --no-hetero --n 5 --concurrency 1,2 --budget 30 2>&1)
        [ -z "${TUNE_TEST_LOG:-}" ] || printf '%s\n' "${out}" > "${TUNE_TEST_LOG}.step2"
        if grep -q 'incumbent: .*THROUGHPUT' <<< "${out}" && grep -Eq '^decision: (incumbent|converged)' <<< "${out}"; then
            ok "the next incremental run starts from the winner and keeps it"
        else
            bad "incremental resume" "$(tail -30 <<< "${out}")"
        fi
    else
        skip "full tune (TUNE_E2E_FULL=1)"
    fi
fi

echo ""
echo "${PASS} passed, ${FAILS} failed, ${SKIPS} skipped"
[ "${FAILS}" -eq 0 ]
