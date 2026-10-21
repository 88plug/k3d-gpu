#!/usr/bin/env bash
# Telemetry manifest tests. Runs the node-exporter core-type script from
# share/telemetry/node-exporter.yml against fake sysfs trees, and the recording rules
# from share/telemetry/prometheus.yml through `promtool test rules` when promtool is
# on PATH. No cluster, no hardware.
#
# Usage: tests/telemetry_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

# configmap_key <file> <key>: the block-scalar value of a top-level ConfigMap data key
# (`  <key>: |` followed by lines indented four spaces), with the indent stripped.
configmap_key() {
    local file=$1 key=$2 line on=0
    while IFS= read -r line; do
        if [ "${on}" -eq 0 ]; then
            [ "${line}" = "  ${key}: |" ] && on=1
            continue
        fi
        if [ -z "${line}" ]; then
            echo
        elif [ "${line:0:4}" = "    " ]; then
            echo "${line:4}"
        else
            break
        fi
    done < "${file}"
}

check() {
    local name=$1 want=$2 got=$3
    if [ "${got}" = "${want}" ]; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name}"; echo "  want: ${want}"; echo "  got:  ${got}"
    fi
}

SCRIPT="${WORK}/core-type.sh"
configmap_key "${ROOT}/share/telemetry/node-exporter.yml" core-type.sh > "${SCRIPT}"
check "core-type script extracted" "#!/bin/sh" "$(head -n1 "${SCRIPT}")"

# core_type <name> <cpu_core list> <cpu_atom list>: run the script on a fake sysfs and
# print its output file ("<none>" when it wrote none). "-" leaves a PMU directory out.
core_type() {
    local root="${WORK}/$1" out pmu list f
    mkdir -p "${root}/sys" "${root}/textfile"
    for pmu in cpu_core cpu_atom; do
        if [ "${pmu}" = cpu_core ]; then list=$2; else list=$3; fi
        [ "${list}" = "-" ] && continue
        mkdir -p "${root}/sys/devices/${pmu}"
        echo "${list}" > "${root}/sys/devices/${pmu}/cpus"
    done
    out="${root}/textfile/k3d_gpu_core_type.prom"
    SYSFS="${root}/sys" OUT="${out}" sh "${SCRIPT}" || echo "<exit $?>"
    if [ -e "${out}" ]; then cat "${out}"; else echo "<none>"; fi
    for f in "${root}"/textfile/*; do
        [ -e "${f}" ] && [ "${f}" != "${out}" ] && echo "<leftover> ${f##*/}"
    done
}

# i9-13900T: P-cores 0-15 (with HT siblings), E-cores 16-31.
out=$(core_type rpl 0-15 16-31)
check "hybrid: 32 series" 32 "$(grep -c '^k3d_gpu_cpu_core_type{' <<< "${out}")"
check "hybrid: cpu 0 performance" 1 "$(grep -c '^k3d_gpu_cpu_core_type{cpu="0",type="performance"} 1$' <<< "${out}")"
check "hybrid: cpu 15 performance" 1 "$(grep -c '^k3d_gpu_cpu_core_type{cpu="15",type="performance"} 1$' <<< "${out}")"
check "hybrid: cpu 16 efficient" 1 "$(grep -c '^k3d_gpu_cpu_core_type{cpu="16",type="efficient"} 1$' <<< "${out}")"
check "hybrid: cpu 31 efficient" 1 "$(grep -c '^k3d_gpu_cpu_core_type{cpu="31",type="efficient"} 1$' <<< "${out}")"
check "hybrid: TYPE header" 1 "$(grep -c '^# TYPE k3d_gpu_cpu_core_type gauge$' <<< "${out}")"
check "hybrid: no temp file left" "" "$(grep '^<' <<< "${out}")"

# Comma lists and single CPUs, as when some CPUs are offline.
out=$(core_type gaps 0-1,4 6,8-9)
check "list: cpus" "0:performance 1:performance 4:performance 6:efficient 8:efficient 9:efficient" \
    "$(sed -n 's/^k3d_gpu_cpu_core_type{cpu="\([0-9]*\)",type="\([a-z]*\)"} 1$/\1:\2/p' <<< "${out}" | tr '\n' ' ' | sed 's/ $//')"

# Every E-core offline: the atom PMU lists no CPUs.
out=$(core_type no-atom 0-7 "")
check "empty atom list: performance only" 8 "$(grep -c 'type="performance"' <<< "${out}")"
check "empty atom list: no efficient" 0 "$(grep -c 'type="efficient"' <<< "${out}")"

# Non-hybrid CPU: neither PMU directory exists.
check "non-hybrid: no file" "<none>" "$(core_type flat - -)"

# resctrl MBM: one pass of the sidecar loop (ONCE=1) over a fake /sys/fs/resctrl with
# the root group, a control group with a monitor group, and an unreadable counter.
MBM="${WORK}/resctrl-mbm.sh"
configmap_key "${ROOT}/share/telemetry/node-exporter.yml" resctrl-mbm.sh > "${MBM}"
check "resctrl-mbm script extracted" "#!/bin/sh" "$(head -n1 "${MBM}")"
# mbm <name> [setup]: run one pass on a fake tree; print the output file or "<none>".
mbm() {
    local root="${WORK}/$1" out f
    mkdir -p "${root}/sys" "${root}/textfile"
    out="${root}/textfile/k3d_gpu_resctrl_mbm.prom"
    SYSFS="${root}/sys" OUT="${out}" ONCE=1 sh "${MBM}" || echo "<exit $?>"
    if [ -e "${out}" ]; then cat "${out}"; else echo "<none>"; fi
    for f in "${root}"/textfile/*; do
        [ -e "${f}" ] && [ "${f}" != "${out}" ] && echo "<leftover> ${f##*/}"
    done
    return 0  # the loop's last test must not become the status under pipefail
}
check "resctrl not mounted: no file" "<none>" "$(mbm unmounted)"
R="${WORK}/rdt/sys/fs/resctrl"
mkdir -p "${R}/info/L3_MON" "${R}/mon_data/mon_L3_00" "${R}/mon_data/mon_L3_01" \
    "${R}/BestEffort/mon_data/mon_L3_00" "${R}/BestEffort/mon_groups/pod1/mon_data/mon_L3_00"
echo 1000 > "${R}/mon_data/mon_L3_00/mbm_total_bytes"; echo 400 > "${R}/mon_data/mon_L3_00/mbm_local_bytes"
echo 2000 > "${R}/mon_data/mon_L3_01/mbm_total_bytes"; echo Unavailable > "${R}/mon_data/mon_L3_01/mbm_local_bytes"
echo 30 > "${R}/BestEffort/mon_data/mon_L3_00/mbm_total_bytes"
echo 7 > "${R}/BestEffort/mon_groups/pod1/mon_data/mon_L3_00/mbm_total_bytes"
out=$(mbm rdt)
check "mbm: root group total, domain 00" 1 "$(grep -c '^k3d_gpu_resctrl_mbm_bytes_total{group="/",domain="00",scope="total"} 1000$' <<< "${out}")"
check "mbm: root group local, domain 00" 1 "$(grep -c '^k3d_gpu_resctrl_mbm_bytes_total{group="/",domain="00",scope="local"} 400$' <<< "${out}")"
check "mbm: domain 01 total" 1 "$(grep -c '^k3d_gpu_resctrl_mbm_bytes_total{group="/",domain="01",scope="total"} 2000$' <<< "${out}")"
check "mbm: an Unavailable counter is skipped" 0 "$(grep -c 'domain="01",scope="local"' <<< "${out}")"
check "mbm: control group" 1 "$(grep -c '^k3d_gpu_resctrl_mbm_bytes_total{group="BestEffort",domain="00",scope="total"} 30$' <<< "${out}")"
check "mbm: monitor group inside a control group" 1 "$(grep -c '^k3d_gpu_resctrl_mbm_bytes_total{group="BestEffort/pod1",domain="00",scope="total"} 7$' <<< "${out}")"
check "mbm: TYPE counter" 1 "$(grep -c '^# TYPE k3d_gpu_resctrl_mbm_bytes_total counter$' <<< "${out}")"
check "mbm: no temp file left" "" "$(grep '^<' <<< "${out}")"
# The sidecar only reads: the script never mounts, mkdirs or writes under resctrl.
check "mbm: script writes nothing under resctrl" 0 "$(grep -cE '^ *(mount|mkdir|umount) |> *"\$\{r[}/]' "${MBM}")"

# The AUR package must ship every telemetry manifest under /usr/share/k3d-gpu/telemetry.
# Runs the PKGBUILD package() function against this checkout into a scratch pkgdir,
# under errexit as makepkg does (outside an `if`, where errexit would be ignored).
mkdir -p "${WORK}/src" "${WORK}/pkg"
ln -s "${ROOT}" "${WORK}/src/k3d-gpu"
# shellcheck source=/dev/null
(export srcdir="${WORK}/src" pkgdir="${WORK}/pkg"; . "${ROOT}/packaging/aur/PKGBUILD"; set -e; package) >/dev/null 2>&1
pkg_rc=$?
if [ "${pkg_rc}" -eq 0 ]; then
    missing=""
    for f in "${ROOT}"/share/telemetry/*.yml "${ROOT}"/share/telemetry/optional/*.yml; do
        rel=${f#"${ROOT}/share/"}
        [ -f "${WORK}/pkg/usr/share/k3d-gpu/${rel}" ] || missing+=" ${rel}"
    done
    check "PKGBUILD installs every telemetry manifest" "" "${missing# }"
else
    FAILS=$((FAILS + 1)); echo "FAIL PKGBUILD package() runs (exit ${pkg_rc})"
fi

# The output must parse as Prometheus text exposition when promtool is available.
if command -v promtool >/dev/null 2>&1; then
    core_type lint 0-15 16-31 | grep -v '^<' > "${WORK}/lint.prom"
    if promtool check metrics < "${WORK}/lint.prom" >/dev/null 2>&1; then
        PASS=$((PASS + 1)); echo "ok   core-type output passes promtool check metrics"
    else
        FAILS=$((FAILS + 1)); echo "FAIL core-type output passes promtool check metrics"
        promtool check metrics < "${WORK}/lint.prom"
    fi
    mbm rdt | grep -v '^<' > "${WORK}/mbm.prom"
    if promtool check metrics < "${WORK}/mbm.prom" >/dev/null 2>&1; then
        PASS=$((PASS + 1)); echo "ok   resctrl-mbm output passes promtool check metrics"
    else
        FAILS=$((FAILS + 1)); echo "FAIL resctrl-mbm output passes promtool check metrics"
        promtool check metrics < "${WORK}/mbm.prom"
    fi
    configmap_key "${ROOT}/share/telemetry/prometheus.yml" k3d-gpu.rules.yml > "${WORK}/k3d-gpu.rules.yml"
    cp "${ROOT}/tests/telemetry-rules.test.yml" "${WORK}/"
    if (cd "${WORK}" && promtool test rules telemetry-rules.test.yml); then
        PASS=$((PASS + 1)); echo "ok   recording rules (promtool test rules)"
    else
        FAILS=$((FAILS + 1)); echo "FAIL recording rules (promtool test rules)"
    fi
else
    echo "skip promtool checks (promtool not on PATH)"
fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
