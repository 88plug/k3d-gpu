#!/usr/bin/env bash
# Batch driver for tests/fuzz/props.py. Loads the launcher's function definitions
# (everything above its subcommand dispatch) and runs one function per input
# record. Records arrive NUL-separated on stdin; each result is written to stdout
# as "<exit code>|<output>" followed by a NUL.
#
# Usage: shim.sh expand               records: cpulist
#        shim.sh within               records: list-a, list-b (two records per case)
#        shim.sh vendors <sysroot>    records: K3D_GPU_VENDORS value
#        shim.sh overrides <sysroot>  records: image reference
#        shim.sh amx_labels <sysroot> no records
#        shim.sh cpu_isa_labels <sysroot> no records

# The globals assigned below are read by the sourced launcher functions.
# shellcheck disable=SC2034
LAUNCHER="$(cd "$(dirname "$0")/../.." && pwd)/scripts/k3d-gpu"
# shellcheck source=/dev/null
source <(sed '/^case "\${1:-help}" in$/,$d' "${LAUNCHER}")
set +e  # the launcher's errexit must not end the batch; each call reports its own code

mode=$1
SYSROOT=${2:-}

emit() { printf '%s|%s\0' "$1" "$2"; }

case "${mode}" in
    expand)
        while IFS= read -r -d '' a; do
            out=$(cpulist_expand "${a}" 2>/dev/null | paste -sd' ')
            emit "$?" "${out}"
        done ;;
    within)
        while IFS= read -r -d '' a && IFS= read -r -d '' b; do
            cpulist_within "${a}" "${b}" 2>/dev/null
            emit "$?" ""
        done ;;
    vendors)
        while IFS= read -r -d '' req; do
            # err() exits, so each request runs in its own subshell.
            out=$( (VENDORS_REQUEST=${req}; resolve_vendors && echo "${ACTIVE[*]}") 2>&1 )
            emit "$?" "${out}"
        done ;;
    overrides)
        # cmd_test with every vendor active; run_pod writes its overrides argument
        # to fd 3 instead of calling kubectl. Each record is used as both test images.
        require() { :; }
        resource_total() { echo 1; }
        resource_counts() { echo "${1%\*}x 1"; }
        run_pod() { printf '%s\n' "$3" >&3; printf 'Cpus_allowed_list:\t0\n'; }
        while IFS= read -r -d '' img; do
            out=$( (TEST_IMAGE=${img} DEVICE_TEST_IMAGE=${img}
                    VENDORS_REQUEST=nvidia,intel,npu,gaudi,dsa,iaa,qat,amx,hybrid,numa,wsl
                    cmd_test >/dev/null 2>&1) 3>&1 )
            emit "$?" "${out}"
        done ;;
    amx_labels)
        out=$(amx_labels)
        emit "$?" "${out}" ;;
    cpu_isa_labels)
        out=$(cpu_isa_labels)
        emit "$?" "${out}" ;;
    *) echo "unknown mode ${mode}" >&2; exit 2 ;;
esac
