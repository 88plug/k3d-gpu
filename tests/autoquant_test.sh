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
{"measured_at": "2026-10-17T00:00:00Z",
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

help=$("${K3D_GPU}" help)
grep -q 'autoquant \[--json\]' <<< "${help}" && ok "help lists autoquant" || fail "help lists autoquant"

if "${ROOT}/bench/amx-autoquant/probe.sh" --dry-run > "${WORK}/dry.out" 2>&1 \
    && grep -q 'k3dgpu-i-amx-autoquant-int4' "${WORK}/dry.out" && grep -q 'budget 1200 s' "${WORK}/dry.out"; then
    ok "probe.sh --dry-run plans the three containers inside the 20 min budget"
else
    fail "probe.sh --dry-run"; sed 's/^/  | /' "${WORK}/dry.out"
fi

if python3 "${ROOT}/tests/console/autoquant_summarize_check.py" "${ROOT}" > "${WORK}/sum.out" 2>&1; then
    ok "summarize.py: reference pick, 5/5 wins, 3/3 probe agreement, control ratio, dNLL on synthetic runs"
else
    fail "summarize.py"; sed 's/^/  | /' "${WORK}/sum.out"
fi

echo "autoquant_test: ${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
