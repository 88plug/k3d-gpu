#!/usr/bin/env bash
# The part of the H29 run that holds the live lock (called by run.sh through flock).
# Usage: run-locked.sh <repo> <out dir>
# Amendment A2: after setup, each level in LEVELS (default "typical heavy") runs the
# probe under bench/load/load.py at that level, with its own BUDGET_S of wall time
# (the first level's budget includes the cluster setup).
set -euo pipefail
REPO=$1 OUT=$2
T0=$(date +%s)
BUDGET=${BUDGET_S:-1200} TEARDOWN=90
UNC=/sys/devices/system/cpu/intel_uncore_frequency/package_00_die_00
PROBE="${REPO}/bench/uncore-phase/probe.py"
LOADPY="${REPO}/bench/load/load.py"
LEVELS=${LEVELS:-typical heavy}
export K3D_GPU_CLUSTER="${K3D_GPU_CLUSTER:-k3dgpu-i-uncore-phase}" KUBECONFIG="${OUT}/kubeconfig"
export K3D_GPU_SHARE="${K3D_GPU_SHARE:-${REPO}/share}"
export K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_TIMEOUT=540
L="${REPO}/scripts/k3d-gpu"
PF=""
left() { echo $(( BUDGET - ( $(date +%s) - T0 ) - TEARDOWN )); }

[ -d "${UNC}" ] || { echo "no ${UNC}: this host has no legacy intel_uncore_frequency die 0" >&2; exit 2; }
# sudo keeps its timestamp per parent process when there is no tty, so a `sudo -v`
# in the caller does not reach this script (a grandchild through flock). With
# SUDO_ASKPASS set every sudo here uses -A, so a timestamp that expires during a
# long multi-level hold is renewed from the helper; without it, sudo -n.
if [ -n "${SUDO_ASKPASS:-}" ]; then SUDO="sudo -A"; ${SUDO} -v; else SUDO="sudo -n"; fi
${SUDO} true || { echo "set SUDO_ASKPASS or run as root: RAPL and the uncore writes need root" >&2; exit 2; }

# Snapshot "<path> <value>" of every file the probe can write (every CPU, so the
# restore does not depend on knowing the pod's cpuset).
SNAP="${OUT}/knobs.before"
{
    echo "${UNC}/min_freq_khz $(cat "${UNC}/min_freq_khz")"
    echo "${UNC}/max_freq_khz $(cat "${UNC}/max_freq_khz")"
    for f in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_max_freq; do echo "${f} $(cat "${f}")"; done
} > "${SNAP}"

# Returns 1 if any write failed; each write's status is checked explicitly because
# `set -e` does not apply inside a function called from a `||` list.
restore_knobs() {
    local p v rc=0
    # Uncore min to the hardware floor first, so every max/min write order is valid.
    ${SUDO} tee "${UNC}/min_freq_khz" < "${UNC}/initial_min_freq_khz" >/dev/null || rc=1
    while read -r p v; do
        case "${p}" in */min_freq_khz) continue ;; esac
        ${SUDO} tee "${p}" <<< "${v}" >/dev/null || rc=1
    done < "${SNAP}"
    while read -r p v; do
        case "${p}" in */min_freq_khz) ${SUDO} tee "${p}" <<< "${v}" >/dev/null || rc=1 ;; esac
    done < "${SNAP}"
    # Verify against the snapshot, not just the write status.
    while read -r p v; do
        [ "$(cat "${p}")" = "${v}" ] || { echo "restore mismatch ${p}: $(cat "${p}") != ${v}" >&2; rc=1; }
    done < "${SNAP}"
    echo "$(date +%s.%N) runner restore from ${SNAP} rc=${rc}" >> "${OUT}/writes.log"
    return "${rc}"
}
cleanup() {
    restore_knobs || echo "RESTORE FAILED: re-apply ${SNAP} by hand" | tee -a "${OUT}/facts.txt"
    [ -z "${PF}" ] || kill "${PF}" 2>/dev/null || true
    python3 "${LOADPY}" stop >> "${OUT}/facts.txt" 2>&1 || true
    k3d cluster delete "${K3D_GPU_CLUSTER}" >/dev/null 2>&1 || true
    rm -f "${KUBECONFIG}"
    echo "wall_s=$(( $(date +%s) - T0 ))" | tee -a "${OUT}/facts.txt"
}
trap cleanup EXIT

{
    echo "# provenance"; date -u +%FT%TZ; lscpu | grep 'Model name'; uname -r
    git -C "${REPO}" rev-parse HEAD
    for f in initial_min_freq_khz initial_max_freq_khz min_freq_khz max_freq_khz; do
        echo "uncore ${f} $(cat "${UNC}/${f}")"
    done
    echo "epp $(cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference)"
    echo "governor $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
    echo "pcores $(cat /sys/devices/cpu_core/cpus 2>/dev/null)"
    echo "pl1_uw $(${SUDO} cat /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw)"
    ${SUDO} dmidecode -t memory | grep -E 'Configured Memory Speed' | sort | uniq -c
    echo "containers $(docker ps -q | wc -l)"; free -m | grep -i swap; cat /proc/loadavg
} > "${OUT}/facts.txt" 2>&1

k3d cluster delete "${K3D_GPU_CLUSTER}" >/dev/null 2>&1 || true
timeout "$(left)" "${L}" up > "${OUT}/up.log" 2>&1 \
    || { echo "SETUP_FAILED (up): INCONCLUSIVE-setup" | tee -a "${OUT}/facts.txt"; exit 1; }
timeout "$(left)" "${L}" agent up > "${OUT}/agent.log" 2>&1 \
    || { echo "SETUP_FAILED (agent up): INCONCLUSIVE-setup" | tee -a "${OUT}/facts.txt"; exit 1; }
kubectl -n k3d-gpu-agent get deploy ovms -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}' >> "${OUT}/facts.txt"
POD_CPUS=$(kubectl -n k3d-gpu-agent exec deploy/ovms -- grep Cpus_allowed_list /proc/1/status | awk '{print $2}')
echo "pod_cpus ${POD_CPUS}" >> "${OUT}/facts.txt"
kubectl -n k3d-gpu-agent port-forward svc/ovms 18029:8000 > "${OUT}/pf.log" 2>&1 &
PF=$!
sleep 3

echo "load_cpus $(python3 "${LOADPY}" cpus)" >> "${OUT}/facts.txt"
FIRST=${LEVELS%% *}
for LEVEL in ${LEVELS}; do
    [ "${LEVEL}" = "${FIRST}" ] || T0=$(date +%s)
    echo "level ${LEVEL} start $(date -u +%FT%TZ)" >> "${OUT}/facts.txt"
    python3 "${LOADPY}" start --level "${LEVEL}" --ttl "$(( BUDGET + 120 ))" >> "${OUT}/facts.txt"
    sleep 30
    LEFT=$(left)
    [ "${LEFT}" -ge 300 ] || { echo "only ${LEFT}s left at ${LEVEL}: INCONCLUSIVE-setup" | tee -a "${OUT}/facts.txt"; exit 1; }
    ${SUDO} timeout "${LEFT}" python3 "${PROBE}" --cpus "${POD_CPUS}" --level "${LEVEL}" \
        --url http://127.0.0.1:18029/v3 --model Qwen3-4B-int4-ov --deadline-s "$(( LEFT - 40 ))" \
        --out "${OUT}/h29.jsonl" --write-log "${OUT}/writes.log" 2>> "${OUT}/probe.log" || true
    python3 "${LOADPY}" stop >> "${OUT}/facts.txt" 2>&1
    echo "level ${LEVEL} end $(date -u +%FT%TZ)" >> "${OUT}/facts.txt"
    python3 "${PROBE}" --analyze "${OUT}/h29.jsonl" --level "${LEVEL}" | tee "${OUT}/verdict-${LEVEL}.txt"
done
