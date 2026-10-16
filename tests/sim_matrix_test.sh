#!/usr/bin/env bash
# Hardware simulation matrix. Each tests/fixtures/<platform>/sysroot is a fake
# /sys, /dev, /proc and firmware tree in the layout tests/fixtures/capture.sh
# writes. Per platform this runs `k3d-gpu detect`, `doctor` and `up` against it,
# with docker, k3d and kubectl stubbed and PATH limited to a fixed tool set, and
# compares each transcript (output, exit code, every k3d create argument and
# kubectl call) with the platform's *.golden files.
#
# Usage: tests/sim_matrix_test.sh [platform...]       run all or the named platforms
#        tests/sim_matrix_test.sh --update [platform...]  rewrite the golden files
#        tests/sim_matrix_test.sh --list-json         platform names as a JSON array
#
# Per-fixture files:
#   sysroot/      the fake host tree (K3D_GPU_SYSROOT)
#   SOURCES       where every id, driver, node name and file name comes from
#   allocatable   "<resource> <count>" lines the stubbed cluster reports per node
#   env           optional KEY=VALUE lines passed to k3d-gpu; HOST_TOOLS=<names>
#                 puts stub commands of those names on PATH (e.g. nvidia-ctk)
#   detect.golden doctor.golden up.golden   expected transcripts

set -uo pipefail

TESTS=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "${TESTS}")
FIXTURES="${TESTS}/fixtures"
K3D_GPU="${REPO}/scripts/k3d-gpu"
UPDATE=0

platforms() {
    local d
    for d in "${FIXTURES}"/*/sysroot; do
        [ -d "${d}" ] && basename "$(dirname "${d}")"
    done
}

case "${1:-}" in
    --list-json)
        platforms | awk 'BEGIN { printf "[" } { printf "%s\"%s\"", (NR > 1 ? "," : ""), $0 } END { print "]" }'
        exit 0 ;;
    --update) UPDATE=1; shift ;;
esac

WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

# Tools k3d-gpu may run. Nothing else from the host is on PATH, so host
# binaries (nvidia-ctk, a real kubectl) cannot change the result.
BASE_BIN="${WORK}/bin"
mkdir -p "${BASE_BIN}"
for tool in bash cat readlink basename dirname grep tr sort paste seq uname sed awk wc head sleep; do
    ln -s "$(command -v "${tool}")" "${BASE_BIN}/${tool}"
done

# k3d: logs every call with one argument per line; `k3d version` answers.
cat > "${BASE_BIN}/k3d" <<'EOF'
#!/bin/bash
[ "$1" = version ] && { echo "k3d version v0.0.0-stub"; exit 0; }
{ echo "k3d $1 $2 $3"; shift 3; printf '  %s\n' "$@"; } >> "${STUB_LOG}"
EOF
# kubectl: logs every call. Node allocatable comes from STUB_ALLOCATABLE; a
# label query finds the single server node; every other call succeeds silently
# (so baked plugin daemonsets "exist" and rollouts "finish").
cat > "${BASE_BIN}/kubectl" <<'EOF'
#!/bin/bash
echo "kubectl $*" >> "${STUB_LOG}"
case "$*" in
    "get nodes -o go-template="*) cat "${STUB_ALLOCATABLE}" ;;
    "get nodes -l "*" -o name") echo "node/k3d-gpu-server-0" ;;
esac
exit 0
EOF
# docker: `docker info` succeeds; runtimes and CDI devices come from
# STUB_DOCKER_RUNTIMES and STUB_DOCKER_CDI, as in tests/detect_test.sh.
cat > "${BASE_BIN}/docker" <<'EOF'
#!/bin/bash
[ "$1" = info ] || exit 0
case "$*" in
    *Runtimes*) for r in ${STUB_DOCKER_RUNTIMES:-}; do echo "${r}"; done ;;
    *DiscoveredDevices*) for id in ${STUB_DOCKER_CDI:-}; do echo "${id}"; done ;;
esac
exit 0
EOF
chmod +x "${BASE_BIN}/k3d" "${BASE_BIN}/kubectl" "${BASE_BIN}/docker"

# transcript <platform> <command>: run k3d-gpu <command> against the fixture and
# print output, exit code and (for up) the stub call log, with paths normalised.
transcript() {
    local name=$1 cmd=$2 fx="${FIXTURES}/$1" run="${WORK}/$1" bin rc out tool
    local -a extra=()
    bin="${run}/bin"
    rm -rf "${run}"
    mkdir -p "${run}/home"
    cp -a "${BASE_BIN}" "${bin}"
    : > "${run}/calls"
    if [ -f "${fx}/env" ]; then
        while IFS= read -r line; do
            case "${line}" in ''|'#'*) continue ;; esac
            if [[ "${line}" == HOST_TOOLS=* ]]; then
                for tool in ${line#HOST_TOOLS=}; do
                    printf '#!/bin/bash\nexit 0\n' > "${bin}/${tool}"; chmod +x "${bin}/${tool}"
                done
            else
                extra+=("${line}")
            fi
        done < "${fx}/env"
    fi
    out=$(cd "${run}" && env -i HOME="${run}/home" PATH="${bin}" \
        K3D_GPU_SYSROOT="${fx}/sysroot" K3D_GPU_SHARE="${REPO}/share" \
        STUB_LOG="${run}/calls" STUB_ALLOCATABLE="${fx}/allocatable" \
        ${extra[@]+"${extra[@]}"} "${bin}/bash" "${K3D_GPU}" "${cmd}" 2>&1)
    rc=$?
    {
        printf '%s\n' "${out}"
        echo "exit=${rc}"
        if [ -s "${run}/calls" ]; then
            echo "--- calls"
            cat "${run}/calls"
        fi
    } | sed -e 's/\x1b\[[0-9;]*m//g' -e "s#${fx}/sysroot#<sysroot>#g" -e "s#${REPO}#<repo>#g"
}

check() {
    local name=$1 cmd=$2 golden="${FIXTURES}/$1/$2.golden" got
    got=$(transcript "${name}" "${cmd}")
    if [ "${UPDATE}" -eq 1 ]; then
        printf '%s\n' "${got}" > "${golden}"
        echo "wrote ${golden#"${REPO}"/}"
        return
    fi
    if [ -f "${golden}" ] && diff -u "${golden}" <(printf '%s\n' "${got}") > "${WORK}/diff"; then
        PASS=$((PASS + 1)); echo "ok   ${name}: ${cmd}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name}: ${cmd} (golden ${golden#"${REPO}"/})"
        if [ -f "${golden}" ]; then sed 's/^/  | /' "${WORK}/diff"; else echo "  | missing golden; run with --update"; fi
    fi
}

# The fixture must be exactly what capture.sh writes from that tree: nothing a
# real capture could not produce, nothing it would add.
check_capture() {
    local name=$1 src="${FIXTURES}/$1/sysroot" out="${WORK}/$1-capture"
    rm -rf "${out}"
    if bash "${FIXTURES}/capture.sh" "${out}" "${src}" >/dev/null \
       && diff -r --no-dereference "${src}" "${out}" > "${WORK}/diff"; then
        PASS=$((PASS + 1)); echo "ok   ${name}: capture round-trip"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name}: capture round-trip"
        sed 's/^/  | /' "${WORK}/diff"
    fi
}

mapfile -t SELECTED < <(if [ "$#" -gt 0 ]; then printf '%s\n' "$@"; else platforms; fi)
[ "${#SELECTED[@]}" -gt 0 ] || { echo "no fixtures under ${FIXTURES}" >&2; exit 1; }
for p in "${SELECTED[@]}"; do
    [ -d "${FIXTURES}/${p}/sysroot" ] || { echo "unknown platform '${p}'" >&2; exit 1; }
    for f in SOURCES allocatable; do
        [ -f "${FIXTURES}/${p}/${f}" ] || { FAILS=$((FAILS + 1)); echo "FAIL ${p}: missing ${f}"; }
    done
    for c in detect doctor up; do check "${p}" "${c}"; done
    [ "${UPDATE}" -eq 1 ] || check_capture "${p}"
done

[ "${UPDATE}" -eq 1 ] && exit 0
echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
