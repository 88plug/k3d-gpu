#!/usr/bin/env bash
# run.sh: agents-per-box ramp against one already-running OpenAI-compatible endpoint.
#
#   bench/apb/run.sh facts OUT_DIR
#   bench/apb/run.sh ramp  ARM URL MODEL
#   bench/apb/run.sh report RUN_DIR
#
# ramp: idle power window, one discarded warm-up, then for N in STEPS and rep in
# 1..REPS a fresh closed-loop trace (seed 1000*N+rep, identical across arms) is
# replayed by N agents (bench/apb/apb.py run-step). The ramp stops after the
# first step whose p95 turn latency exceeds STOP_SLO (steps 1, 2 and 4 always
# run). A rep whose own p95 exceeds 2 x STOP_SLO skips the step's remaining
# reps. After the ramp, one N=1 rep repeats as a drift check. Energy: RAPL
# package + psys sampled every second for the whole ramp (root reader via sudo
# unless energy_uj is readable), and nvidia-smi board power every 500 ms when
# nvidia-smi exists. See bench/apb/PROTOCOL.md.
#
# Environment (defaults are the pre-registered values):
#   OUT        run directory parent        (bench/apb/runs)
#   FAMILY     support | coding            (support)
#   STEPS      agent counts                ("1 2 4 8 12 16 24 32")
#   REPS       reps per step               (3)
#   SLO        headline p95 SLO, s         (10)
#   STOP_SLO   ramp stops above this, s    (30)
#   TIMEOUT    per-request timeout, s      (120)
#   TIME_SCALE multiplies tool waits       (1)
#   IDLE_S     idle window before ramp, s  (60)
#   EXTRA_BODY merged into every request   ({"chat_template_kwargs":{"enable_thinking":false}})
#   NVIDIA     1 = sample nvidia-smi       (1 when nvidia-smi exists)
#   POWERCAP   powercap root               (/sys/class/powercap)

set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
APB=(python3 "${HERE}/apb.py")
OUT=${OUT:-${HERE}/runs}
FAMILY=${FAMILY:-support}
STEPS=${STEPS:-"1 2 4 8 12 16 24 32"}
REPS=${REPS:-3}
SLO=${SLO:-10}
STOP_SLO=${STOP_SLO:-30}
TIMEOUT=${TIMEOUT:-120}
TIME_SCALE=${TIME_SCALE:-1}
IDLE_S=${IDLE_S:-60}
EXTRA_BODY=${EXTRA_BODY:-'{"chat_template_kwargs":{"enable_thinking":false}}'}
POWERCAP=${POWERCAP:-/sys/class/powercap}
if [ -z "${NVIDIA:-}" ]; then
    if command -v nvidia-smi >/dev/null 2>&1; then NVIDIA=1; else NVIDIA=0; fi
fi
ALWAYS_STEPS=" 1 2 4 "

die() { echo "run.sh: $*" >&2; exit 2; }
log() { echo "[apb $(date +%H:%M:%S)] $*" >&2; }

cmd_facts() {
    local d=${1:?usage: run.sh facts OUT_DIR}
    mkdir -p "${d}"
    {
        echo "## date"; date -Is
        echo "## uname"; uname -a
        echo "## cpu"; lscpu
        echo "## memory (DIMM speed: Configured Memory Speed)"
        sudo -n dmidecode -t memory 2>&1 | grep -E 'Locator:|Size:|Speed:|Type:' || echo "dmidecode unavailable"
        echo "## free / swap"; free -g; swapon --show || true
        echo "## loadavg"; cat /proc/loadavg
        echo "## running containers"; docker ps -q 2>/dev/null | wc -l || true
        echo "## governor / EPP / no_turbo"
        cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor \
            /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference \
            /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || true
        echo "## RAPL limits"
        for z in "${POWERCAP}"/intel-rapl:*; do
            [ -r "${z}/name" ] || continue
            echo "${z##*/} $(cat "${z}/name") $(cat "${z}"/constraint_*_power_limit_uw 2>/dev/null | tr '\n' ' ')"
        done
        echo "## nvidia"
        nvidia-smi --query-gpu=index,name,uuid,memory.total,power.limit,clocks.max.memory,driver_version --format=csv 2>&1 || true
        echo "## k8s"
        kubectl --request-timeout=5s get nodes -o wide 2>&1 || true
        kubectl --request-timeout=5s get pods -A -o wide 2>&1 | grep -Ei 'ovms|vllm' || true
        echo "## git"; git -C "${HERE}" rev-parse HEAD 2>/dev/null || true
    } > "${d}/facts.txt" 2>&1
    log "facts -> ${d}/facts.txt"
}

# RAPL reader: "<epoch> <package uj> <psys uj|->" every second, or at once when
# a line arrives on stdin; exits when stdin closes (same pattern as energy.sh).
# shellcheck disable=SC2016 # runs in the reader's own bash
READER_SCRIPT='
while :; do
    p=$(< "$1") || exit 1
    s=-; [ -r "$2" ] && s=$(< "$2")
    echo "${EPOCHREALTIME/,/.} ${p} ${s}"
    read -r -t 1 _; rc=$?
    [ "${rc}" -eq 0 ] || [ "${rc}" -gt 128 ] || exit 0
done'

start_samplers() {
    local d=$1 pkg="${POWERCAP}/intel-rapl:0/energy_uj" psys="${POWERCAP}/intel-rapl:1/energy_uj"
    [ -e "${pkg}" ] || die "no RAPL package counter at ${pkg}"
    [ "$(cat "${POWERCAP}/intel-rapl:1/name" 2>/dev/null)" = psys ] || psys=/nonexistent
    local elevate=()
    [ -r "${pkg}" ] || elevate=(sudo)
    exec 4> >(${elevate[@]+"${elevate[@]}"} bash -c "${READER_SCRIPT}" _ "${pkg}" "${psys}" > "${d}/rapl.log")
    RAPL_PID=$!
    local t=$((SECONDS + 120))
    until [ -s "${d}/rapl.log" ]; do
        [ "${SECONDS}" -lt "${t}" ] && kill -0 "${RAPL_PID}" 2>/dev/null \
            || die "RAPL reader wrote no sample (sudo refused or ${pkg} unreadable)"
        sleep 0.1
    done
    NV_PID=
    if [ "${NVIDIA}" = 1 ]; then
        nvidia-smi --query-gpu=index,power.draw --format=csv,noheader,nounits -lms 500 2>/dev/null \
            | while IFS= read -r line; do echo "${EPOCHREALTIME/,/.}, ${line}"; done > "${d}/nvidia.log" &
        NV_PID=$!
    fi
}

stop_samplers() {
    echo >&4 || true
    sleep 0.3
    exec 4>&- || true
    if [ -n "${NV_PID:-}" ]; then
        pkill -P "${NV_PID}" 2>/dev/null || true
        kill "${NV_PID}" 2>/dev/null || true
    fi
}

# run_rep DIR N REP: one closed-loop replay; prints run-step's JSON line.
run_rep() {
    local d=$1 n=$2 rep=$3 seed=$((1000 * $2 + $3))
    mkdir -p "${d}/steps" "${d}/traces"
    "${APB[@]}" trace --family "${FAMILY}" --agents "${n}" --seed "${seed}" \
        -o "${d}/traces/n${n}-r${rep}.json" > /dev/null
    "${APB[@]}" run-step "${d}/traces/n${n}-r${rep}.json" --url "${URL}" --model "${MODEL}" \
        --agents "${n}" --extra-body "${EXTRA_BODY}" --timeout "${TIMEOUT}" \
        --time-scale "${TIME_SCALE}" --slo "${STOP_SLO}" \
        --out "${d}/steps/n$(printf %03d "${n}")-r${rep}"
}

cmd_ramp() {
    ARM=${1:?usage: run.sh ramp ARM URL MODEL}; URL=${2:?URL}; MODEL=${3:?MODEL}
    [[ "${ARM}" =~ ^[A-Za-z0-9._-]+$ ]] || die "ARM must be [A-Za-z0-9._-]"
    local d
    d="${OUT}/${ARM}-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "${d}"
    cmd_facts "${d}"
    printf '{"arm":"%s","url":"%s","model":"%s","family":"%s","steps":"%s","reps":%s,"slo":%s,"stop_slo":%s,"timeout":%s,"time_scale":%s,"extra_body":%s}\n' \
        "${ARM}" "${URL}" "${MODEL}" "${FAMILY}" "${STEPS}" "${REPS}" "${SLO}" "${STOP_SLO}" \
        "${TIMEOUT}" "${TIME_SCALE}" "${EXTRA_BODY}" > "${d}/config.json"
    start_samplers "${d}"
    trap stop_samplers EXIT

    log "idle window ${IDLE_S}s"
    local t0; t0=${EPOCHREALTIME/,/.}
    sleep "${IDLE_S}"
    printf '{"epoch_start":%s,"epoch_end":%s}\n' "${t0}" "${EPOCHREALTIME/,/.}" > "${d}/idle.json"

    log "warm-up (discarded)"
    run_rep "${d}/warmup" 1 0 >&2 || true

    local n rep line
    for n in ${STEPS}; do
        for rep in $(seq 1 "${REPS}"); do
            log "N=${n} rep ${rep}/${REPS}"
            line=$(run_rep "${d}" "${n}" "${rep}")
            echo "${line}" >&2
            if [[ "${line}" == *'"early_fail": true'* ]]; then
                log "N=${n}: rep p95 > 2 x STOP_SLO, remaining reps skipped"
                break
            fi
        done
        if ! "${APB[@]}" verdict "${d}" --agents "${n}" --slo "${STOP_SLO}" >&2; then
            if [[ "${ALWAYS_STEPS}" != *" ${n} "* ]]; then
                log "N=${n} over STOP_SLO=${STOP_SLO}s: ramp ends"
                break
            fi
        fi
    done

    log "drift check: N=1 once more"
    run_rep "${d}/drift" 1 9 >&2 || true
    stop_samplers
    trap - EXIT
    "${APB[@]}" report "${d}" --slo "${SLO}" --arm "${ARM}"
    log "done: ${d}/report.md"
}

case "${1:-}" in
    facts)  shift; cmd_facts "$@" ;;
    ramp)   shift; cmd_ramp "$@" ;;
    report) shift; "${APB[@]}" report "${1:?RUN_DIR}" --slo "${SLO}" ;;
    *) sed -n '2,30p' "$0"; exit 2 ;;
esac
