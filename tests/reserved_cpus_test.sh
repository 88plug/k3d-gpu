#!/usr/bin/env bash
# Tests for contrib/eat/compute_reserved_cpus.sh, the hybrid-aware replacement for
# Intel Enterprise Agent Toolkit's core/roles/utils/files/compute_reserved_cpus.sh.
# Each case builds a fake CPU topology under SYSFS. No cluster, no hardware.
#
# Usage: tests/reserved_cpus_test.sh

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/contrib/eat/compute_reserved_cpus.sh"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

# Kernel CPU list ("0-3,8") -> one CPU number per line.
expand() {
    local part
    for part in ${1//,/ }; do
        if [[ "${part}" == *-* ]]; then seq "${part%-*}" "${part#*-}"; else echo "${part}"; fi
    done
}

newroot() { local r="${WORK}/$1"; mkdir -p "${r}/devices/system/cpu"; echo "${r}"; }

# numa <root> <cpulist>...: one NUMA node per list, node0 first.
numa() {
    local root=$1 i=0 list
    shift
    for list in "$@"; do
        mkdir -p "${root}/devices/system/node/node${i}"
        echo "${list}" > "${root}/devices/system/node/node${i}/cpulist"
        i=$((i + 1))
    done
}

# smt <root> <first cpu> <count> <sibling offset>: <count> cores, CPU c paired with c+offset
# (offset 1 = adjacent siblings like client CPUs, offset N = Xeon's second half).
smt() {
    local root=$1 first=$2 count=$3 off=$4 c
    for ((c = first; c < first + count; c++)); do
        if [ "${off}" -eq 1 ]; then
            [ $(((c - first) % 2)) -eq 0 ] || continue
        fi
        core "${root}" "${c},$((c + off))"
    done
}

# core <root> <cpulist>: one physical core whose threads are <cpulist>.
core() {
    local cpu
    for cpu in $(expand "$2"); do
        mkdir -p "$1/devices/system/cpu/cpu${cpu}/topology"
        echo "$2" > "$1/devices/system/cpu/cpu${cpu}/topology/thread_siblings_list"
    done
}

# hybrid <root> <P cpus> <E cpus>: the per-core-type PMUs of an Intel hybrid CPU.
hybrid() {
    mkdir -p "$1/devices/cpu_core" "$1/devices/cpu_atom"
    echo "$2" > "$1/devices/cpu_core/cpus"
    echo "$3" > "$1/devices/cpu_atom/cpus"
}

# expect <name> <root> <reserve> <want list>
expect() {
    local name=$1 root=$2 n=$3 want=$4 got rc
    got=$(SYSFS="${root}" bash "${SCRIPT}" "${n}" 2>&1)
    rc=$?
    if [ "${rc}" -eq 0 ] && [ "${got}" = "NRI_RESERVED_CPU_LIST=${want}" ]; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (rc=${rc})"
        echo "  want: NRI_RESERVED_CPU_LIST=${want}"
        echo "  got:  ${got}"
    fi
}

# expect_fail <name> <root> <reserve>
expect_fail() {
    if SYSFS="$2" bash "${SCRIPT}" "$3" >/dev/null 2>&1; then
        FAILS=$((FAILS + 1)); echo "FAIL $1 (expected non-zero exit)"
    else
        PASS=$((PASS + 1)); echo "ok   $1"
    fi
}

# ---- hybrid: i9-13900T, 8 P-cores with HT siblings 0-1..14-15, 16 E-cores 16-31 ----
r=$(newroot i9-13900t)
numa "${r}" 0-31
smt "${r}" 0 16 1
for c in $(seq 16 31); do core "${r}" "${c}"; done
hybrid "${r}" 0-15 16-31
expect "hybrid: 8 reserved CPUs are all E-cores" "${r}" 8 16,17,18,19,20,21,22,23
expect "hybrid: odd count stays on E-cores" "${r}" 3 16,17,18
expect "hybrid: all 16 E-cores before any P-core" "${r}" 16 16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
expect "hybrid: past the E-cores, whole P-cores (both siblings)" "${r}" 18 0,1,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
expect "hybrid: one CPU past the E-cores takes one P-core thread" "${r}" 17 0,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31

# Same topology without the hybrid PMUs (older kernel): whole cores, lowest ids first.
r=$(newroot i9-13900t-no-pmu)
numa "${r}" 0-31
smt "${r}" 0 16 1
for c in $(seq 16 31); do core "${r}" "${c}"; done
expect "no core-type PMUs: whole cores from CPU 0" "${r}" 8 0,1,2,3,4,5,6,7

# A hybrid PMU with no online E-cores is not hybrid.
r=$(newroot p-only)
numa "${r}" 0-15
smt "${r}" 0 16 1
hybrid "${r}" 0-15 ""
expect "cpu_atom with no CPUs: whole cores from CPU 0" "${r}" 4 0,1,2,3

# ---- Xeon: siblings in the second half. Expected lists are what upstream's
# lscpu-based script prints for the same layout (checked against EAT @0ecf67a). ----
r=$(newroot xeon-1numa)
numa "${r}" 0-63
smt "${r}" 0 32 32
expect "Xeon 1 NUMA, 8: four cores and their siblings (upstream: same)" "${r}" 8 0,1,2,3,32,33,34,35
expect "Xeon 1 NUMA, 7: upstream result" "${r}" 7 0,1,2,3,32,33,34
expect "Xeon 1 NUMA, 3: upstream result" "${r}" 3 0,1,32

r=$(newroot xeon-2numa)
numa "${r}" 0-31,64-95 32-63,96-127
smt "${r}" 0 64 64
expect "Xeon 2 NUMA, 8: four per node, node order (upstream: same)" "${r}" 8 0,1,64,65,32,33,96,97
expect "Xeon 2 NUMA, 7: rounds up per node (upstream: same)" "${r}" 7 0,1,64,65,32,33,96,97
expect "Xeon 2 NUMA, 3: one core per node (upstream: same)" "${r}" 3 0,64,32,96

r=$(newroot xeon-noht)
numa "${r}" 0-15 16-31
for c in $(seq 0 31); do core "${r}" "${c}"; done
expect "Xeon 2 NUMA without HT (upstream: same)" "${r}" 8 0,1,2,3,16,17,18,19

# HT on, node smaller than twice its share: upstream takes the lowest ids (0-5,
# half of cores 2 and 3); this script still takes whole cores. Deliberate difference.
r=$(newroot xeon-small-node)
numa "${r}" 0-7
smt "${r}" 0 4 4
expect "HT, small node, 6: whole cores (upstream: 0,1,2,3,4,5)" "${r}" 6 0,1,2,4,5,6

# ---- edge cases ----
r=$(newroot no-numa)
echo 0-7 > "${r}/devices/system/cpu/online"
smt "${r}" 0 8 1
expect "no NUMA sysfs: one node from cpu/online" "${r}" 4 0,1,2,3

r=$(newroot no-siblings-file)
numa "${r}" 0-3
mkdir -p "${r}/devices/system/cpu"
expect "missing thread_siblings_list: each CPU is its own core" "${r}" 2 0,1

r=$(newroot ten-nodes)
numa "${r}" 0 1 2 3 4 5 6 7 8 9 10
expect "node10 sorts after node9" "${r}" 11 0,1,2,3,4,5,6,7,8,9,10

r=$(newroot memory-only-node)
numa "${r}" 0-7 ""
smt "${r}" 0 8 1
expect "a memory-only NUMA node does not take a share" "${r}" 4 0,1,2,3

r=$(newroot no-topology)
expect_fail "no NUMA nodes and no cpu/online fails" "${r}" 2

r=$(newroot bad-args)
numa "${r}" 0-3
expect_fail "zero reserved CPUs is rejected" "${r}" 0
expect_fail "missing count is rejected" "${r}" ""
expect_fail "non-numeric count is rejected" "${r}" abc

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
