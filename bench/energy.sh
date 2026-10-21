#!/usr/bin/env bash
# energy.sh — energy a command used, from a RAPL counter (default: CPU package,
# which on client parts includes the iGPU).
#
# Usage: bench/energy.sh [--zone intel-rapl:0] [--interval 5] [--json FILE] -- <command> [args...]
#
# Prints "joules=... seconds=... avg_watts=..." on stderr when the command ends,
# optionally writes the same as JSON, and exits with the command's exit code.
# The command runs as the calling user, in the foreground. energy_uj is root-only,
# so one `sudo` reader process samples it for the whole run. sudo prompts on the
# terminal; with no terminal, set SUDO_ASKPASS to a program that prints the
# password (sudo -A). A ticket from an earlier `sudo -v` only counts when sudo
# keys tickets by terminal; with no terminal it is keyed to the parent process.
#
# The counter counts microjoules and wraps at max_energy_range_uj (~262 kJ on
# this class of CPU: ~40 min at 100 W). The reader samples every --interval
# seconds and every step's delta is summed, so wraps are counted as long as the
# counter wraps at most once per interval.

set -euo pipefail

ZONE="intel-rapl:0"
INTERVAL=5
JSON_OUT=""
POWERCAP="${ENERGY_POWERCAP:-/sys/class/powercap}" # tests point it at a fake tree

die() { echo "energy.sh: $*" >&2; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --zone)     ZONE=${2:?--zone needs a value}; shift 2 ;;
        --interval) INTERVAL=${2:?--interval needs a value}; shift 2 ;;
        --json)     JSON_OUT=${2:?--json needs a file}; shift 2 ;;
        --)         shift; break ;;
        -h|--help)  sed -n '2,16p' "$0"; exit 0 ;;
        *)          break ;;
    esac
done
[ $# -gt 0 ] || die "no command given (usage: energy.sh [options] -- <command>)"
[[ "${INTERVAL}" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "--interval must be a number of seconds"

ZDIR="${POWERCAP}/${ZONE}"
[ -r "${ZDIR}/name" ] || die "no RAPL zone ${ZDIR} (see 'k3d-gpu report' for this host's zones)"
NAME=$(cat "${ZDIR}/name")
RANGE=$(cat "${ZDIR}/max_energy_range_uj")

# The reader prints "<epoch seconds> <energy_uj>" now, then every INTERVAL
# seconds or as soon as a line arrives on its stdin, and exits when its stdin
# closes. Joules and seconds both come from its samples, so the average power is
# over exactly the window the counter covered.
# shellcheck disable=SC2016 # runs in the reader's own bash
READER_SCRIPT='
while :; do
    uj=$(< "$1") || exit 1
    echo "${EPOCHREALTIME/,/.} ${uj}"  # "," in some locales
    read -r -t "$2" _; rc=$?
    [ "${rc}" -eq 0 ] || [ "${rc}" -gt 128 ] || exit 0
done'
ELEVATE=()
if [ ! -r "${ZDIR}/energy_uj" ] && [ -n "${K3D_GPU_RAPL_EXEC:-}" ]; then
    # The prefix load.py also reads (e.g. `docker exec <privileged container>`, on a host where
    # no password is kept); the reader needs bash there and its stdin, so docker exec gets -i.
    read -r -a ELEVATE <<< "${K3D_GPU_RAPL_EXEC/#docker exec /docker exec -i }"
elif [ ! -r "${ZDIR}/energy_uj" ]; then
    ELEVATE=(sudo)
    if [ -n "${SUDO_ASKPASS:-}" ]; then ELEVATE+=(-A); fi
fi

SAMPLES=$(mktemp)
trap 'rm -f "${SAMPLES}"' EXIT
samples() { wc -l < "${SAMPLES}"; }

# wait_samples <n> <seconds>: block until the reader has written n samples.
wait_samples() {
    local deadline=$((SECONDS + $2))
    while [ "$(samples)" -lt "$1" ]; do
        if [ "${SECONDS}" -ge "${deadline}" ] || [ ! -d "/proc/${READER}" ]; then
            [ "$(samples)" -ge "$1" ] && return 0
            die "RAPL reader wrote no sample (sudo refused, or ${ZDIR}/energy_uj unreadable)"
        fi
        sleep 0.01
    done
}

exec 4> >(${ELEVATE[@]+"${ELEVATE[@]}"} bash -c "${READER_SCRIPT}" _ "${ZDIR}/energy_uj" "${INTERVAL}" > "${SAMPLES}")
READER=$!
wait_samples 1 120  # long enough to type a sudo password
t0=${EPOCHREALTIME/,/.}

rc=0
"$@" || rc=$?

t1=${EPOCHREALTIME/,/.}
n=$(samples)
echo >&4          # take a final sample now
wait_samples $((n + 1)) 10
exec 4>&-         # reader exits
# A periodic sample can land just before the trigger, so the trigger's own sample
# may still be in flight: wait for the reader to exit, then read a settled file.
wait "${READER}" 2>/dev/null || true
nsamples=$(samples)

# Sum consecutive deltas; a smaller reading than the previous one is a wrap.
read -r total_uj joules seconds watts < <(awk -v range="${RANGE}" '
    NR == 1 { t0 = $1 }
    NR > 1  { d = $2 - prev; if (d < 0) d += range; uj += d }
    { prev = $2; t1 = $1 }
    END { s = t1 - t0; printf "%.0f %.3f %.3f %.3f\n", uj, uj / 1e6, s, (s > 0 ? uj / 1e6 / s : 0) }' "${SAMPLES}")
wall=$(awk -v a="${t0}" -v b="${t1}" 'BEGIN { printf "%.3f", b - a }')

echo "energy: zone=${ZONE} (${NAME}) joules=${joules} seconds=${seconds} avg_watts=${watts} command_seconds=${wall} samples=${nsamples} exit=${rc}" >&2
if [ -n "${JSON_OUT}" ]; then
    printf '{"zone":"%s","name":"%s","joules":%s,"seconds":%s,"avg_watts":%s,"command_seconds":%s,"energy_uj":%s,"max_energy_range_uj":%s,"samples":%s,"exit_code":%s}\n' \
        "${ZONE}" "${NAME}" "${joules}" "${seconds}" "${watts}" "${wall}" "${total_uj}" "${RANGE}" "${nsamples}" "${rc}" > "${JSON_OUT}"
fi
exit "${rc}"
