#!/usr/bin/env bash
# Tests for `k3d-gpu phase-steer` (H20, experimental, read-only): the per-phase
# CPU split it detects from each simulated host in tests/fixtures/*/sysroot, the
# llama.cpp masks it prints, its overrides and its refusals. No cluster, no hardware.
#
# Usage: tests/phase_steer_test.sh

set -uo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
K3D_GPU="${REPO}/scripts/k3d-gpu"
FIX="${REPO}/tests/fixtures"
PASS=0 FAILS=0

run() { local root=$1; shift; NO_COLOR=1 K3D_GPU_SYSROOT="${FIX}/${root}/sysroot" "${K3D_GPU}" phase-steer "$@" 2>&1; }
field() { python3 -c 'import json,sys; v=json.load(sys.stdin)[sys.argv[1]]; print(" ".join(v) if isinstance(v, list) else ("null" if v is None else v))' "$1"; }

check() {
    local name=$1 want=$2 got=$3
    if [ "${got}" = "${want}" ]; then
        PASS=$((PASS + 1))
    else
        FAILS=$((FAILS + 1))
        printf 'FAIL %s\n  want: %s\n  got:  %s\n' "${name}" "${want}" "${got}"
    fi
}

# platform  state  prefill  decode  prefill_mask  decode_mask  lowpower
while read -r plat state pre dec pm dm lp; do
    json=$(run "${plat}" --json)
    check "${plat} state" "${state}" "$(field state <<< "${json}")"
    check "${plat} prefill" "${pre}" "$(field prefill_cpus <<< "${json}")"
    check "${plat} decode" "${dec}" "$(field decode_cpus <<< "${json}")"
    check "${plat} prefill mask" "${pm}" "$(field prefill_mask <<< "${json}")"
    check "${plat} decode mask" "${dm}" "$(field decode_mask <<< "${json}")"
    check "${plat} lowpower" "${lp}" "$(field lowpower_cpus <<< "${json}")"
done <<'EOF'
raptorlake-nvidia auto 0-15 16-31 0xffff 0xffff0000 null
arrowlake-h       auto 0-5  6-13  0x3f   0x3fc0     14-15
arrowlake-s       auto 0-7  8-23  0xff   0xffff00   null
meteorlake        auto 0-11 12-19 0xfff  0xff000    20-21
pantherlake       auto 0-3  4-11  0xf    0xff0      12-15
xeon-gnr          off  null null  null   null       null
xeon-srf          off  null null  null   null       null
alderlake-n       off  null null  null   null       null
EOF

# Meteor Lake lists its LP E-cores in cpu_atom; they are the atom CPUs with no
# L3, and decode must not land there.
json=$(run meteorlake --json)
check "meteorlake state" auto "$(field state <<< "${json}")"
lp=$(field lowpower_cpus <<< "${json}")
dec=$(field decode_cpus <<< "${json}")
check "meteorlake has LP E-cores" yes "$([ "${lp}" != null ] && echo yes || echo no)"
check "meteorlake decode excludes LP E-cores" "" "$(comm -12 <(python3 -c 'import sys
for p in sys.argv[1].split(","):
    a, _, b = p.partition("-"); print("\n".join(str(i) for i in range(int(a), int(b or a) + 1)))' "${dec}" | sort) \
    <(python3 -c 'import sys
for p in sys.argv[1].split(","):
    a, _, b = p.partition("-"); print("\n".join(str(i) for i in range(int(a), int(b or a) + 1)))' "${lp}" | sort))"

# llama-server args carry exactly the two masks, non-strict (strict stacks agents, H20 A1).
json=$(run raptorlake-nvidia --json)
check "llama args" "--cpu-mask-batch 0xffff --cpu-mask 0xffff0000" \
    "$(field llama_server_args <<< "${json}")"
check "no strict placement (agents would stack)" "" "$(field llama_server_args <<< "${json}" | grep -o strict)"
check "off has no args" "" "$(run xeon-gnr --json | field llama_server_args)"
check "schema" "phase-steer/v1" "$(field schema <<< "${json}")"
check "ledger status" "H20 INCONCLUSIVE" "$(field ledger <<< "${json}")"

# Text output.
out=$(run raptorlake-nvidia)
check "text decode line" "  decode  (E-cores): 16-31  mask 0xffff0000" "$(grep 'decode ' <<< "${out}")"
check "text off" "  off: no P-core and E-core PMUs (cpu_core, cpu_atom), one core type: nothing to steer" \
    "$(run xeon-gnr | grep 'off:')"

# Overrides: explicit lists win, on any host, and must be valid online CPUs.
json=$(run raptorlake-nvidia --json --prefill 0,2,4,6 --decode 16-23)
check "override state" override "$(field state <<< "${json}")"
check "override prefill mask" 0x55 "$(field prefill_mask <<< "${json}")"
check "override decode mask" 0xff0000 "$(field decode_mask <<< "${json}")"
check "override single high cpu" 0x80000000 "$(run raptorlake-nvidia --json --prefill 0 --decode 31 | field decode_mask)"

refuse() {
    local name=$1; shift
    if run raptorlake-nvidia "$@" >/dev/null; then
        FAILS=$((FAILS + 1)); echo "FAIL ${name}: accepted $*"
    else
        PASS=$((PASS + 1))
    fi
}
refuse "prefill without decode" --prefill 0-3
refuse "garbled list" --prefill 0-x --decode 16
refuse "reversed range" --prefill 3-1 --decode 16
refuse "offline cpu" --prefill 0 --decode 999
refuse "unknown option" --apply
refuse "missing value" --decode

# Read-only: phase-steer never calls docker, k3d or kubectl.
stub=$(mktemp -d)
trap 'rm -rf "${stub}"' EXIT
for t in docker k3d kubectl; do printf '#!/bin/sh\necho called >> %s/calls\n' "${stub}" > "${stub}/${t}"; chmod +x "${stub}/${t}"; done
PATH="${stub}:${PATH}" run raptorlake-nvidia >/dev/null
check "no docker/k3d/kubectl calls" "" "$(cat "${stub}/calls" 2>/dev/null)"

echo "phase_steer_test: ${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
