#!/usr/bin/env bash
# autoquant tests (EXPERIMENTS.md H27): console/autoquant.py unit and fuzz tests,
# `k3d-gpu autoquant` on fixtures (rule, --store, measured, stale), bad options,
# and bench/amx-autoquant/probe.sh --dry-run. No cluster, no network, no hardware.
#
# Usage: tests/autoquant_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
K3D_GPU="${ROOT}/scripts/k3d-gpu"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $*"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $*"; }

export K3D_GPU_AUTOQUANT_CACHE="${WORK}/autoquant.json"
RPL="${ROOT}/tests/fixtures/raptorlake-nvidia/facts.json"
SPR="${ROOT}/tests/fixtures/xeon-spr-accel/facts.json"

if FUZZ_N="${FUZZ_N:-100}" python3 "${ROOT}/tests/console/test_autoquant.py" > "${WORK}/unit.out" 2>&1; then
    ok "autoquant unit and fuzz tests"
else
    fail "autoquant unit and fuzz tests"; sed 's/^/  | /' "${WORK}/unit.out"
fi

# field <json> <key>
field() { python3 -c 'import json, sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$1" "$2"; }

out=$("${K3D_GPU}" autoquant --json --facts "${RPL}") || fail "autoquant --json on raptorlake"
[ "$(field "${out}" precision)/$(field "${out}" source)" = int4/rule ] \
    && ok "no cache: raptorlake serves the int4 rule" || fail "no cache: got ${out}"

txt=$("${K3D_GPU}" autoquant --facts "${SPR}")
grep -q 'arm int8  amx-int8' <<< "${txt}" && grep -q 'arm fp16  amx-bf16' <<< "${txt}" \
    && ok "SPR arms take the AMX paths" || fail "SPR arms: ${txt}"
txt=$("${K3D_GPU}" autoquant --facts "${RPL}")
grep -q 'arm int8  avx-vnni-int8' <<< "${txt}" && grep -q 'arm fp16  f32' <<< "${txt}" \
    && ok "raptorlake arms take AVX-VNNI and f32" || fail "raptorlake arms: ${txt}"

cat > "${WORK}/r.json" <<'EOF'
{"measured_at": "2026-10-17T00:00:00Z", "rounds": 5, "wins": 5, "notes": [],
 "arms": {"int4": {"turn_s": [3.1, 3.0, 3.2], "dnll": 0.09},
          "int8": {"turn_s": [4.0, 4.1, 3.9], "dnll": 0.004},
          "fp16": {"turn_s": [8.0, 8.2, 7.9], "dnll": 0.0}}}
EOF
if "${K3D_GPU}" autoquant --facts "${RPL}" --store "${WORK}/r.json" > "${WORK}/store.out" 2>&1; then
    ok "--store caches a gated winner"
else
    fail "--store"; sed 's/^/  | /' "${WORK}/store.out"
fi
out=$("${K3D_GPU}" autoquant --json --facts "${RPL}")
[ "$(field "${out}" precision)/$(field "${out}" source)/$(field "${out}" memory)" = int8/measured/8Gi ] \
    && ok "cached winner int8 (int4 over the gate) is served, 8Gi" || fail "measured: ${out}"
out=$("${K3D_GPU}" autoquant --json --facts "${SPR}")
[ "$(field "${out}" source)" = rule-stale-cache ] \
    && ok "another host fingerprint ignores the cache" || fail "stale: ${out}"

cat > "${WORK}/bad.json" <<'EOF'
{"arms": {"int4": {"turn_s": [1], "dnll": 0.5}}}
EOF
if "${K3D_GPU}" autoquant --facts "${SPR}" --store "${WORK}/bad.json" > /dev/null 2>&1; then
    fail "--store with no passing arm must fail"
else
    ok "--store with no passing arm fails and caches nothing"
fi

for args in "--bogus" "--facts" "--facts ${WORK}/nope.json" "--store ${WORK}/nope.json"; do
    # shellcheck disable=SC2086
    if "${K3D_GPU}" autoquant ${args} > /dev/null 2>&1; then fail "autoquant ${args} must fail"; else ok "autoquant ${args} fails"; fi
done

# `agent manifest` integration: a host-path fake sysroot (CPU only, no
# accelerator) and a kubectl stub that reports an empty cluster. The cache entry
# is written for the fingerprint `autoquant --json` computes on that same root.
HROOT="${WORK}/hostroot"
mkdir -p "${HROOT}/proc" "${WORK}/bin"
printf 'processor\t: 0\nmodel name\t: Intel(R) Core(TM) i9-13900T\nflags\t\t: avx2 avx_vnni\nmicrocode\t: 0x12f\n' \
    > "${HROOT}/proc/cpuinfo"
printf 'MemTotal:       131521428 kB\n' > "${HROOT}/proc/meminfo"
printf '#!/bin/sh\nexit 0\n' > "${WORK}/bin/kubectl"
chmod +x "${WORK}/bin/kubectl"
ICACHE="${WORK}/int.json"
# hostrun <env assignments...>: `agent manifest` on the fake root, stdout + stderr.
hostrun() { env K3D_GPU_SYSROOT="${HROOT}" PATH="${WORK}/bin:${PATH}" K3D_GPU_AUTOQUANT_CACHE="${ICACHE}" \
    "$@" bash "${K3D_GPU}" agent manifest 2>&1; }

m=$(hostrun)
grep -q -- '--source_model=OpenVINO/Qwen3-4B-int4-ov' <<< "${m}" && ! grep -q 'agent-precision-source' <<< "${m}" \
    && ok "agent manifest: no cache serves int4 with no autoquant annotation" || fail "no-cache manifest: ${m}"
fp=$(env K3D_GPU_SYSROOT="${HROOT}" K3D_GPU_AUTOQUANT_CACHE="${ICACHE}" bash "${K3D_GPU}" autoquant --json \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["fingerprint"])')
printf '{"schema": "k3d-gpu.autoquant/v1", "hosts": {"%s": {"winner": "int8"}}}\n' "${fp}" > "${ICACHE}"
m=$(hostrun)
grep -q -- '--source_model=OpenVINO/Qwen3-4B-int8-ov' <<< "${m}" \
    && grep -q 'k3d-gpu.io/agent-model: OpenVINO/Qwen3-4B-int8-ov' <<< "${m}" \
    && grep -q 'memory: 8Gi' <<< "${m}" && grep -q -- '--model_name=Qwen3-4B-int4-ov' <<< "${m}" \
    && ok "agent manifest: measured int8 swaps weights, 8Gi, annotation, served name kept" || fail "measured manifest: ${m}"
m=$(hostrun K3D_GPU_AUTOQUANT=off)
grep -q -- '--source_model=OpenVINO/Qwen3-4B-int4-ov' <<< "${m}" \
    && ok "agent manifest: K3D_GPU_AUTOQUANT=off ignores the cache" || fail "off: ${m}"
m=$(hostrun K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-4B-fp16-ov)
grep -q -- '--source_model=OpenVINO/Qwen3-4B-fp16-ov' <<< "${m}" && ! grep -q 'agent-precision-source' <<< "${m}" \
    && ok "agent manifest: K3D_GPU_AGENT_MODEL pins over the cache" || fail "model pin: ${m}"
# quickstart passes its RAM-tier model with K3D_GPU_AGENT_MODEL_TIER=1: not a user
# pin when it is the default model the probe arms measure, so the winner serves.
m=$(hostrun K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-4B-int4-ov K3D_GPU_AGENT_MODEL_TIER=1 K3D_GPU_AGENT_MEMORY=6Gi K3D_GPU_AGENT_MEMORY_TIER=1)
grep -q -- '--source_model=OpenVINO/Qwen3-4B-int8-ov' <<< "${m}" && grep -q 'memory: 8Gi' <<< "${m}" \
    && ok "agent manifest: a quickstart tier model takes the measured pick and its memory" || fail "tier model: ${m}"
m=$(hostrun K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-4B-int4-ov K3D_GPU_AGENT_MODEL_TIER=1 K3D_GPU_AGENT_MEMORY=5Gi)
grep -q -- '--source_model=OpenVINO/Qwen3-4B-int8-ov' <<< "${m}" && grep -q 'memory: 5Gi' <<< "${m}" \
    && ok "agent manifest: a tier model with a user memory keeps that memory" || fail "tier model, user memory: ${m}"
m=$(hostrun K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-8B-int4-ov K3D_GPU_AGENT_MODEL_TIER=1)
grep -q -- '--source_model=OpenVINO/Qwen3-8B-int4-ov' <<< "${m}" && ! grep -q 'agent-precision-source' <<< "${m}" \
    && ok "agent manifest: a tier model the probe did not measure is kept" || fail "8B tier: ${m}"
m=$(hostrun K3D_GPU_AGENT_MEMORY=5Gi)
grep -q 'memory: 5Gi' <<< "${m}" && grep -q 'below the 8Gi' <<< "${m}" \
    && ok "agent manifest: a memory override under the winner's need is honored with a warning" || fail "mem: ${m}"
printf '{"schema": "k3d-gpu.autoquant/v1", "hosts": []}\n' > "${ICACHE}"
m=$(hostrun)
grep -q -- '--source_model=OpenVINO/Qwen3-4B-int4-ov' <<< "${m}" && ! grep -q Traceback <<< "${m}" \
    && ok "agent manifest: malformed cache serves int4 with no traceback" || fail "malformed: ${m}"

help=$("${K3D_GPU}" help)
grep -q 'autoquant \[--json\]' <<< "${help}" && ok "help lists autoquant" || fail "help lists autoquant"

if "${ROOT}/bench/amx-autoquant/probe.sh" --dry-run > "${WORK}/dry.out" 2>&1 \
    && grep -q 'k3dgpu-i-amx-autoquant-int4' "${WORK}/dry.out" && grep -q 'budget 1200 s' "${WORK}/dry.out"; then
    ok "probe.sh --dry-run plans the three containers inside the 20 min budget"
else
    fail "probe.sh --dry-run"; sed 's/^/  | /' "${WORK}/dry.out"
fi

# An aborted live run must not leave its watchdog holding live.lock: preflight
# aborts (a stub k3d reports a k3dgpu-f-* cluster) under plain flock (no -o), and
# the lock must be free again within one watchdog tick (5 s), not after 19 min.
mkdir -p "${WORK}/aqdir/text" "${WORK}/kbin"
echo '{}' > "${WORK}/aqdir/text/prompts.json"
printf '#!/bin/sh\necho "NAME SERVERS"\necho "k3dgpu-f-other 1/1"\n' > "${WORK}/kbin/k3d"
chmod +x "${WORK}/kbin/k3d"
if command -v flock >/dev/null 2>&1; then
    env PATH="${WORK}/kbin:${PATH}" AUTOQUANT_DIR="${WORK}/aqdir" \
        flock -w 5 "${WORK}/live.lock" "${ROOT}/bench/amx-autoquant/probe.sh" > "${WORK}/abort.out" 2>&1
    rc=$?
    if [ "${rc}" -ne 0 ] && grep -q 'k3dgpu-f-\* cluster exists' "${WORK}/abort.out" \
        && flock -w 7 "${WORK}/live.lock" true; then
        ok "probe.sh preflight abort releases live.lock (watchdog stopped by the EXIT trap)"
    else
        fail "probe.sh abort path (rc ${rc})"; sed 's/^/  | /' "${WORK}/abort.out"
    fi
fi

if python3 "${ROOT}/tests/console/autoquant_summarize_check.py" "${ROOT}" > "${WORK}/sum.out" 2>&1; then
    ok "summarize.py: reference pick, 5/5 wins, 3/3 probe agreement, control ratio, dNLL on synthetic runs"
else
    fail "summarize.py"; sed 's/^/  | /' "${WORK}/sum.out"
fi

echo "autoquant_test: ${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
