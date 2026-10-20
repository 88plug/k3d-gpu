#!/usr/bin/env bash
# cpufreq.sh: the H5 stage-1 root helper. It reads and writes cpufreq sysfs and
# reads the RAPL package counter; it touches nothing else.
#
#   cpufreq.sh snapshot              every CPU's scaling_max_freq, scaling_min_freq,
#                                    scaling_governor, energy_performance_preference
#                                    (where present), one "cpuN attr value" per line
#   cpufreq.sh cap KHZ FIRST LAST    write KHZ to scaling_max_freq of CPUs FIRST..LAST
#   cpufreq.sh restore FILE          write back every value in a snapshot FILE that
#                                    differs from the current one (max_freq first)
#   cpufreq.sh rapl                  RAPL package energy (uJ) and its wrap range
#
# PG_SYSFS and PG_RAPL point it at a fake tree in tests.
set -euo pipefail
SYS=${PG_SYSFS:-/sys/devices/system/cpu}
RAPL=${PG_RAPL:-/sys/class/powercap/intel-rapl:0}
ATTRS=(scaling_max_freq scaling_min_freq scaling_governor energy_performance_preference)

die() { echo "cpufreq.sh: $*" >&2; exit 2; }
cpus() { find "${SYS}" -maxdepth 1 -name 'cpu[0-9]*' -printf '%f\n' | sort -V; }

snapshot() {
    local c a
    for c in $(cpus); do
        [ -d "${SYS}/${c}/cpufreq" ] || continue
        for a in "${ATTRS[@]}"; do
            [ -r "${SYS}/${c}/cpufreq/${a}" ] && echo "${c} ${a} $(cat "${SYS}/${c}/cpufreq/${a}")"
        done
    done
}

cap() {
    local khz=$1 first=$2 last=$3 i
    [[ ${khz} =~ ^[0-9]+$ && ${first} =~ ^[0-9]+$ && ${last} =~ ^[0-9]+$ ]] || die "cap: integers only"
    [ "${first}" -le "${last}" ] || die "cap: FIRST > LAST"
    for ((i = first; i <= last; i++)); do
        [ -w "${SYS}/cpu${i}/cpufreq/scaling_max_freq" ] || die "cap: cpu${i} has no writable scaling_max_freq"
        echo "${khz}" > "${SYS}/cpu${i}/cpufreq/scaling_max_freq"
    done
}

restore() {
    local file=$1 c a v cur
    [ -r "${file}" ] || die "restore: no snapshot ${file}"
    # max before min: a lowered max is raised before any min goes back above it.
    while read -r c a v; do
        [[ ${c} =~ ^cpu[0-9]+$ ]] || die "restore: bad cpu '${c}'"
        case " ${ATTRS[*]} " in *" ${a} "*) ;; *) die "restore: bad attr '${a}'";; esac
        cur=$(cat "${SYS}/${c}/cpufreq/${a}")
        [ "${cur}" = "${v}" ] || echo "${v}" > "${SYS}/${c}/cpufreq/${a}"
    done < <(grep ' scaling_max_freq ' "${file}" || true; grep -v ' scaling_max_freq ' "${file}" || true)
}

case ${1:-} in
    snapshot) snapshot ;;
    cap) [ $# -eq 4 ] || die "usage: cap KHZ FIRST LAST"; cap "$2" "$3" "$4" ;;
    restore) [ $# -eq 2 ] || die "usage: restore FILE"; restore "$2" ;;
    rapl) echo "$(cat "${RAPL}/energy_uj") $(cat "${RAPL}/max_energy_range_uj")" ;;
    *) die "usage: snapshot | cap KHZ FIRST LAST | restore FILE | rapl" ;;
esac
