#!/usr/bin/env bash
# H26 live probe (EXPERIMENTS.md): agent p95 under a memory-bandwidth neighbour,
# arms Q (none), ME15 (stream 15 on efficient), ME4 (stream 4 on efficient with a
# 15-CPU balloon, so the 4 workers spread over every E-core L2 cluster), MC4
# (stream 4 on ecluster: one L2 cluster), order Q ME15 ME4 MC4 | MC4 ME4 ME15 Q | Q ME15 ME4 MC4,
# 10 timed requests per block (n=30 per arm). Run it under the live lock:
#
#   flock -w 10800 "$LOCK" bench/rdt-isolate/run.sh
#
# It aborts at 18 min of its own wall time (ABORT_S), marks unrun blocks
# INCONCLUSIVE, and always deletes its cluster. Reads only (sudo): RAPL
# energy_uj and perf stat on the uncore IMC free-running counters. No sysfs writes.
# sudo is non-interactive: sudo -A when SUDO_ASKPASS is set, else sudo -n; a read
# that fails records null and the analysis then says INCONCLUSIVE.
# Env: OUT results dir (default ./h26-out), K3D_GPU_IMAGE node image (optional),
#      K3D_GPU_CLUSTER (default k3dgpu-i-rdt-isolate), ABORT_S (default 1080).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "${HERE}/../.." && pwd)
OUT=${OUT:-${PWD}/h26-out}
ABORT_S=${ABORT_S:-1080}
NAME=${K3D_GPU_CLUSTER:-k3dgpu-i-rdt-isolate}
mkdir -p "${OUT}"
KUBECONFIG="${OUT}/kubeconfig-$$"
export KUBECONFIG K3D_GPU_CLUSTER="${NAME}" K3D_GPU_SHARE="${K3D_GPU_SHARE:-${ROOT}/share}"
L=("${ROOT}/scripts/k3d-gpu")
NOISY=("${ROOT}/bench/noisy.sh")
PORT=18080 PF=""
REQ="${OUT}/requests.jsonl" BLK="${OUT}/blocks.jsonl" LOG="${OUT}/run.log"
: > "${REQ}"; : > "${BLK}"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "${LOG}"; }
cleanup() {
    [ -z "${PF}" ] || kill "${PF}" 2>/dev/null
    "${NOISY[@]}" stop >/dev/null 2>&1
    log "deleting cluster ${NAME}"
    k3d cluster delete "${NAME}" >> "${LOG}" 2>&1
    rm -f "${KUBECONFIG}"
}
trap cleanup EXIT
left() { [ "${SECONDS}" -lt "${ABORT_S}" ]; }

if k3d cluster list -o json | python3 -c 'import json,sys; sys.exit(any(c["name"].startswith("k3dgpu-f-") for c in json.load(sys.stdin)))'; then :; else
    log "a k3dgpu-f-* cluster exists; refusing to run"; exit 3
fi

SUDO=(sudo -n)
[ -z "${SUDO_ASKPASS:-}" ] || SUDO=(sudo -A)
ECPUS=$(cat /sys/devices/cpu_atom/cpus 2>/dev/null || true)
# E-core L2 groups, one shared_cpu_list per line.
EGROUPS=$(for c in /sys/devices/system/cpu/cpu[0-9]*; do
    n=${c##*cpu}
    [ -n "${ECPUS}" ] && python3 -c 'import sys; lo_hi=[p.split("-") for p in sys.argv[2].split(",")]; sys.exit(not any(int(a[0]) <= int(sys.argv[1]) <= int(a[-1]) for a in lo_hi))' "${n}" "${ECPUS}" \
        && cat "${c}/cache/index2/shared_cpu_list" 2>/dev/null
done | sort -u | paste -sd' ')
rapl() { "${SUDO[@]}" cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null || echo -; }
throttles() { cat /sys/devices/system/cpu/cpu*/thermal_throttle/package_throttle_count 2>/dev/null | awk '{s += $1} END {print s + 0}'; }
pswpin() { awk '$1 == "pswpin" {print $2}' /proc/vmstat; }
# busy jiffies and total jiffies of CPUs 0-15 (host spill onto the agent's CPUs)
pbusy() { awk '$1 ~ /^cpu([0-9]|1[0-5])$/ {t += $2+$3+$4+$5+$6+$7+$8+$9; i += $5+$6} END {print t - i, t}' /proc/stat; }
# IMC read+write MiB over a 15 s window, as GB/s; - without sudo or the PMU.
imc() {
    local f="${OUT}/imc-$1.csv"
    if "${SUDO[@]}" perf stat -a -x, -o "${f}" -e uncore_imc_free_running_0/data_read/,uncore_imc_free_running_0/data_write/,uncore_imc_free_running_1/data_read/,uncore_imc_free_running_1/data_write/ -- sleep 15 2>/dev/null; then
        awk -F, '$1 ~ /^[0-9.]+$/ {s += $1} END {printf "%.2f", s * 1.048576 / 1000 / 15}' "${f}"
    else echo -; fi
}

log "H26 start: cluster ${NAME}, out ${OUT}, abort at ${ABORT_S}s"
"${L[@]}" up >> "${LOG}" 2>&1 || { log "up failed"; exit 1; }
K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_CPUS=15 "${L[@]}" agent up >> "${LOG}" 2>&1 || { log "agent up failed"; exit 1; }
kubectl -n k3d-gpu-agent wait --for=condition=Ready pod -l app=ovms --timeout=300s >> "${LOG}" 2>&1 \
    || kubectl -n k3d-gpu-agent wait --for=condition=Ready pod --all --timeout=300s >> "${LOG}" 2>&1 \
    || { log "agent not Ready"; exit 1; }
agent_pod=$(kubectl -n k3d-gpu-agent get pod -l app=ovms -o name | head -1)
[ -n "${agent_pod}" ] || { log "no pod with app=ovms in k3d-gpu-agent"; kubectl -n k3d-gpu-agent get pod --show-labels >> "${LOG}" 2>&1; exit 1; }
agent_cpus=$(kubectl -n k3d-gpu-agent exec "${agent_pod#pod/}" -- grep Cpus_allowed_list /proc/1/status 2>/dev/null | awk '{print $2}')
log "agent ${agent_pod} Cpus_allowed_list=${agent_cpus} E-cores=${ECPUS} E-core L2 groups=${EGROUPS}"
if [ -n "${ECPUS}" ] && python3 -c '
import sys
def s(x):
    o = set()
    for p in x.split(","):
        a = p.split("-")
        if a[0].isdigit(): o |= set(range(int(a[0]), int(a[-1]) + 1))
    return o
sys.exit(not (s(sys.argv[1]) & s(sys.argv[2])))' "${agent_cpus:-}" "${ECPUS}"; then
    log "agent CPUs overlap the E-cores; the probe needs the agent on P-cores only"; exit 1
fi
kubectl -n k3d-gpu-agent port-forward svc/ovms "${PORT}:8000" >> "${LOG}" 2>&1 &
PF=$!
sleep 3
MODEL=${K3D_GPU_AGENT_MODEL_NAME:-Qwen3-4B-int4-ov}
BASE="http://127.0.0.1:${PORT}/v3"

# Untimed placement check: where 4-worker neighbours land on each label.
for c in ecluster efficient; do
    "${NOISY[@]}" start --cores "${c}" --stressor stream --workers 4 >> "${LOG}" 2>&1
    "${NOISY[@]}" stop >> "${LOG}" 2>&1
done

restarts() { kubectl -n k3d-gpu-agent get "${agent_pod}" -o jsonpath='{.status.containerStatuses[0].restartCount}'; }
r0=$(restarts)
block=0
for spec in "1 Q" "1 ME15" "1 ME4" "1 MC4" "2 MC4" "2 ME4" "2 ME15" "2 Q" "3 Q" "3 ME15" "3 ME4" "3 MC4"; do
    read -r rd arm <<< "${spec}"
    block=$((block + 1))
    if ! left; then
        log "block ${block} ${arm}: not run (abort budget), INCONCLUSIVE"
        echo "{\"block\": ${block}, \"round\": ${rd}, \"arm\": \"${arm}\", \"void\": true, \"reason\": \"abort budget\"}" >> "${BLK}"
        continue
    fi
    ncpus=-
    case "${arm}" in
        ME15) "${NOISY[@]}" start --cores efficient --stressor stream --workers 15 > "${OUT}/n.txt" 2>&1 ;;
        ME4)  "${NOISY[@]}" start --cores efficient --stressor stream --workers 4 --cpu 15 > "${OUT}/n.txt" 2>&1 ;;
        MC4)  "${NOISY[@]}" start --cores ecluster --stressor stream --workers 4 > "${OUT}/n.txt" 2>&1 ;;
        *)    : > "${OUT}/n.txt" ;;
    esac
    [ "${arm}" = Q ] || ncpus=$(grep -o 'Cpus_allowed_list:[[:space:]]*[0-9,-]*' "${OUT}/n.txt" | awk '{print $2}')
    sleep 5
    e0=$(rapl); t0=$(throttles); s0=$(pswpin); read -r b0 j0 <<< "$(pbusy)"
    imc "${block}" > "${OUT}/imc-${block}.gbs" &
    ip=$!
    # CPUs the stream workers run on (host view), sampled once inside the window.
    ( sleep 8; ps -eLo psr,comm | awk '$2 ~ /^stress-ng-strea/ {print $1}' | sort -n | paste -sd, - ) > "${OUT}/psr-${block}.txt" &
    pp=$!
    python3 "${HERE}/client.py" --base-url "${BASE}" --model "${MODEL}" --arm "${arm}" --block "${block}" --round "${rd}" >> "${REQ}"
    wait "${ip}" "${pp}"
    e1=$(rapl); t1=$(throttles); s1=$(pswpin); read -r b1 j1 <<< "$(pbusy)"
    [ "${arm}" = Q ] || "${NOISY[@]}" stop >> "${LOG}" 2>&1
    python3 - "${block}" "${rd}" "${arm}" "${ncpus:--}" "$(cat "${OUT}/psr-${block}.txt")" "${EGROUPS}" "$(cat "${OUT}/imc-${block}.gbs")" "${e0}" "${e1}" "$((t1 - t0))" "$((s1 - s0))" \
        "$(( (b1 - b0) * 100 / ((j1 - j0) > 0 ? (j1 - j0) : 1) ))" "$(cut -d' ' -f1 /proc/loadavg)" >> "${BLK}" <<'EOF'
import json, sys
b, rd, arm, nc, psr, eg, gbs, e0, e1, thr, sw, busy, load = sys.argv[1:]
def cpus(s):
    out = set()
    for part in s.split(","):
        if "-" in part:
            lo, hi = part.split("-"); out |= set(range(int(lo), int(hi) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out
groups = [cpus(g) for g in eg.split()]
workers = cpus(psr)
hit = [g for g in groups if workers & g]
# MC4 must sit in one L2 group; ME4 must spread over >= 2, else the arms coincide.
void = (arm == "MC4" and not any(cpus(nc) <= g for g in groups)) or \
       (arm == "ME4" and len(hit) < 2)
print(json.dumps({"block": int(b), "round": int(rd), "arm": arm, "neighbour_cpus": nc,
    "worker_psr": psr, "worker_l2_groups": len(hit), "void": void, "imc_gbs": None if gbs == "-" else float(gbs),
    "pkg_j": None if "-" in (e0, e1) else (int(e1) - int(e0)) / 1e6,
    "throttle_delta": int(thr), "pswpin": int(sw), "cpu0_15_busy_pct": int(busy), "load1": float(load)}))
EOF
    log "block ${block} round ${rd} ${arm} neighbour=${ncpus} $(tail -1 "${BLK}")"
done
log "agent restarts during run: $(( $(restarts) - r0 ))"
python3 "${HERE}/analyze.py" "${REQ}" "${BLK}" | tee "${OUT}/verdict.json"
log "H26 done in ${SECONDS}s"
