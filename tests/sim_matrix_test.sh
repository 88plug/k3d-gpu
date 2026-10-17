#!/usr/bin/env bash
# Hardware simulation matrix. Each tests/fixtures/<platform>/sysroot is a fake
# /sys, /dev, /proc and firmware tree in the layout tests/fixtures/capture.sh
# writes. Per platform this runs `k3d-gpu detect`, `doctor`, `doctor --perf`,
# `up` and `plan --explain` against it, with docker, k3d and kubectl stubbed and
# PATH limited to a fixed tool set, and compares each transcript (output, exit
# code, every k3d create argument and kubectl call) with the platform's *.golden
# files. `facts --json` and `plan --json` are compared by stdout alone with
# facts.json and plan.json, and must give the same bytes on two runs.
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
#   detect.golden doctor.golden doctor-perf.golden up.golden plan-explain.golden
#                 expected transcripts; facts.json plan.json expected stdout

set -uo pipefail

TESTS=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "${TESTS}")
FIXTURES="${TESTS}/fixtures"
K3D_GPU="${REPO}/scripts/k3d-gpu"
# The default node image is tagged with the launcher version, which every
# release bumps; transcripts record it as <version>.
VERSION=$(sed -n 's/^VERSION="\(.*\)".*/\1/p' "${K3D_GPU}")
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
for tool in bash cat readlink basename dirname grep tr sort paste seq uname sed awk wc head sleep dd od stat cut python3; do
    ln -s "$(command -v "${tool}")" "${BASE_BIN}/${tool}"
done

# k3d: logs every call with one argument per line; `k3d version` answers.
cat > "${BASE_BIN}/k3d" <<'EOF'
#!/bin/bash
[ "$1" = version ] && { echo "k3d version v0.0.0-stub"; exit 0; }
{ echo "k3d $1 $2 $3"; shift 3; printf '  %s\n' "$@"; } >> "${STUB_LOG}"
EOF
# kubectl: logs every call. Node allocatable comes from STUB_ALLOCATABLE; a
# label query finds the single server node; the balloons policy lists the type
# names baked in share/nri-balloons.yml; every other call succeeds silently
# (so baked plugin daemonsets "exist" and rollouts "finish").
cat > "${BASE_BIN}/kubectl" <<'EOF'
#!/bin/bash
echo "kubectl $*" >> "${STUB_LOG}"
case "$*" in
    "get nodes -o go-template="*) cat "${STUB_ALLOCATABLE}" ;;
    "get nodes -l "*" -o name") echo "node/k3d-gpu-server-0" ;;
    *"get balloonspolicy default -o jsonpath={.spec.balloonTypes[*].name}") echo "replica performance efficient lowpower ecluster pcore-physical latency shared" ;;
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

# fixture_run <platform> <command> <stdout file> <stderr file>: run k3d-gpu
# <command> (split on spaces) against the fixture in a fresh ${WORK}/<platform>.
# When both files are the same path, stderr is interleaved into it. Returns
# k3d-gpu's exit code.
fixture_run() {
    local name=$1 fx="${FIXTURES}/$1" run="${WORK}/$1" bin tool
    local -a argv
    read -r -a argv <<< "$2"
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
    fixture_exec() {
        (cd "${run}" && env -i HOME="${run}/home" PATH="${bin}" \
            K3D_GPU_SYSROOT="${fx}/sysroot" K3D_GPU_SHARE="${REPO}/share" \
            STUB_LOG="${run}/calls" STUB_ALLOCATABLE="${fx}/allocatable" \
            ${extra[@]+"${extra[@]}"} "${bin}/bash" "${K3D_GPU}" "${argv[@]}")
    }
    if [ "$3" = "$4" ]; then fixture_exec > "$3" 2>&1; else fixture_exec > "$3" 2> "$4"; fi
}

# transcript <platform> <command>: run k3d-gpu <command> against the fixture and
# print output, exit code and (for up) the stub call log, with paths normalised.
transcript() {
    local fx="${FIXTURES}/$1" run="${WORK}/$1" out rc
    fixture_run "$1" "$2" "${WORK}/$1.out" "${WORK}/$1.out"
    rc=$?
    out=$(<"${WORK}/$1.out")
    {
        printf '%s\n' "${out}"
        echo "exit=${rc}"
        if [ -s "${run}/calls" ]; then
            echo "--- calls"
            cat "${run}/calls"
        fi
    } | sed -e 's/\x1b\[[0-9;]*m//g' -e "s#${fx}/sysroot#<sysroot>#g" -e "s#${REPO}#<repo>#g" \
        -e 's/npu-render-gid=[0-9]*/npu-render-gid=<gid>/g' -e 's/ is group [0-9]*, mode / is group <gid>, mode /g' \
        -e "s#k3d-gpu:${VERSION//./\\.}\([^0-9.]\|\.[^0-9]\|\.\?$\)#k3d-gpu:<version>\1#g"
}

check() {
    local name=$1 cmd=$2 golden="${FIXTURES}/$1/${2// --/-}.golden" got
    got=$(transcript "${name}" "${cmd}")
    if [ "${UPDATE}" -eq 1 ]; then
        printf '%s\n' "${got}" > "${golden}"
        echo "wrote ${golden#"${REPO}"/}"
        return
    fi
    # A shell error (a tool missing from the fixed PATH, a bad redirect) is a bug,
    # even when a golden recorded it.
    if grep -E '^<repo>/scripts/k3d-gpu: line [0-9]+: ' <<< "${got}" > "${WORK}/diff"; then
        FAILS=$((FAILS + 1)); echo "FAIL ${name}: ${cmd} printed a shell error"
        sed 's/^/  | /' "${WORK}/diff"
        return
    fi
    if [ -f "${golden}" ] && diff -u "${golden}" <(printf '%s\n' "${got}") > "${WORK}/diff"; then
        PASS=$((PASS + 1)); echo "ok   ${name}: ${cmd}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name}: ${cmd} (golden ${golden#"${REPO}"/})"
        if [ -f "${golden}" ]; then sed 's/^/  | /' "${WORK}/diff"; else echo "  | missing golden; run with --update"; fi
    fi
}

# check_stdout <platform> <command> <golden file>: a JSON command. Its stdout alone
# is the golden; it must exit 0, print no shell error, and give the same bytes on
# a second run (facts/v1 and plan/v1 take nothing from the clock). The launcher
# version reads "<version>" in the golden, so a release bump does not touch it, and
# a device node's group reads "<gid>", as in the transcripts: the fixture files
# belong to whoever checked the repo out.
check_stdout() {
    local name=$1 cmd=$2 golden="${FIXTURES}/$1/$3" a="${WORK}/$1.a" b="${WORK}/$1.b" e="${WORK}/$1.err" rc
    fixture_run "${name}" "${cmd}" "${WORK}/$1.raw" "${e}"
    rc=$?
    sed -e 's/^\(  *"k3d_gpu_version": \)"[^"]*"/\1"<version>"/' \
        -e 's/npu-render-gid=[0-9]*/npu-render-gid=<gid>/g' -e 's/ is group [0-9]*, mode / is group <gid>, mode /g' \
        "${WORK}/$1.raw" > "${a}"
    if [ "${UPDATE}" -eq 1 ]; then
        cp "${a}" "${golden}"
        echo "wrote ${golden#"${REPO}"/}"
        return
    fi
    if [ "${rc}" -ne 0 ] || grep -E 'k3d-gpu: line [0-9]+: |Traceback' "${e}" > "${WORK}/diff"; then
        FAILS=$((FAILS + 1)); echo "FAIL ${name}: ${cmd} exited ${rc} or printed a shell/python error"
        sed 's/^/  | /' "${e}"
        return
    fi
    fixture_run "${name}" "${cmd}" "${b}" "${e}"
    if ! cmp -s "${WORK}/$1.raw" "${b}"; then
        FAILS=$((FAILS + 1)); echo "FAIL ${name}: ${cmd} is not byte-stable across two runs"
        diff -u "${WORK}/$1.raw" "${b}" | sed 's/^/  | /'
        return
    fi
    if [ -f "${golden}" ] && diff -u "${golden}" "${a}" > "${WORK}/diff"; then
        PASS=$((PASS + 1)); echo "ok   ${name}: ${cmd} (byte-stable)"
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
    for c in detect doctor 'doctor --perf' up 'plan --explain'; do check "${p}" "${c}"; done
    check_stdout "${p}" 'facts --json' facts.json
    check_stdout "${p}" 'plan --json' plan.json
    [ "${UPDATE}" -eq 1 ] || check_capture "${p}"
done

[ "${UPDATE}" -eq 1 ] && exit 0
echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
