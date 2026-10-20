#!/usr/bin/env bash
# run.sh OUT METHOD ROUND: one round of the H5 stage-1 probe (host level, no
# cluster), as pre-registered in docs/prefill-guard.md "Pre-registered A/B test
# (H5)" and amended in EXPERIMENTS.md "H5 amendment A1".
#
# A round starts an OVMS server pinned to CPUs 0-14, warms it up, then runs the
# five arms in the Latin-square order of ROUND (1-5). Each run: 60 s idle; the
# arm's E-core cap on CPUs 16-31 (none for Q and N0); the stress-ng neighbour on
# CPUs 17-31 (none for Q); 15 s lead; 1 warm-up + 3 prefill requests (16 tokens),
# a read of scaling_max_freq on CPUs 16-31, one 128-token decode request, the
# tool proxy on CPU 16; then it waits for the neighbour's 150 s run to end,
# restores the cpufreq snapshot and checks it reads back identical.
#
# Environment:
#   PG_SNAPSHOT    campaign cpufreq snapshot (cpufreq.sh snapshot); required. A
#                  round refuses to start if the host differs from it.
#   PG_MODEL_DIR   OpenVINO/Qwen3-4B-int4-ov directory (with graph.pbtxt); required
#   PG_SUDO_PASS   file holding the sudo password (sudo -S); unset: sudo -n
#   PG_LOCK        lock file; when set, the round runs under flock -w 21600
#   PG_IDLE_S PG_LEAD_S PG_NB_TIMEOUT_S PG_PORT   60, 15, 150, 18005
set -uo pipefail

OUT=${1:?usage: run.sh OUT METHOD ROUND}
METHOD=${2:?method}
ROUND=${3:?round}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
HELPER=${HERE}/cpufreq.sh
PROMPT_FILE=${HERE}/prompt.txt
OVMS_IMAGE=${PG_OVMS_IMAGE:-openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58}
STRESS_IMAGE=${PG_STRESS_IMAGE:-ghcr.io/colinianking/stress-ng@sha256:2ea9a7c3828eda1b1ca3d0b0c5a669ec252f9caf97a6a33aedcea6b2a66d1cbe}
MODEL_NAME=Qwen3-4B-int4-ov
IDLE_S=${PG_IDLE_S:-60}
LEAD_S=${PG_LEAD_S:-15}
NB_TIMEOUT_S=${PG_NB_TIMEOUT_S:-150}
PORT=${PG_PORT:-18005}
SNAP=${PG_SNAPSHOT:?PG_SNAPSHOT is required}
MODEL_DIR=${PG_MODEL_DIR:?PG_MODEL_DIR is required}
SERVER=k3dgpu-h5-ovms
NB=k3dgpu-h5-nb
INF_CPUS=0-14
NB_CPUS=17-31
CAP_FIRST=16
CAP_LAST=31
TOOL_CPU=16
BASE_URL="http://127.0.0.1:${PORT}/v3"

case ${METHOD} in matrixprod|loop) ;; *) echo "method must be matrixprod or loop" >&2; exit 2;; esac
case ${ROUND} in 1|2|3|4|5) ;; *) echo "round must be 1-5" >&2; exit 2;; esac

# Latin square of the pre-registration: round r starts at arm r.
ARMS=(Q N0 C75 C50 CMIN)
ORDER=()
for i in 0 1 2 3 4; do ORDER+=("${ARMS[$(((ROUND - 1 + i) % 5))]}"); done

root() {
    if [ -n "${PG_SUDO_PASS:-}" ]; then
        sudo -S -p '' bash "${HELPER}" "$@" < "${PG_SUDO_PASS}"
    else
        sudo -n bash "${HELPER}" "$@"
    fi
}

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "${OUT}/round${ROUND}.log" >&2; }
utc() { date -u +%FT%T.%3NZ; }
now() { date +%s.%N; }

mkdir -p "${OUT}"
use=$(df --output=pcent / | tail -1 | tr -dc 0-9)
if [ "${use}" -gt 97 ]; then log "abort: / at ${use}%"; exit 3; fi

if [ -n "${PG_LOCK:-}" ]; then
    exec 9>"${PG_LOCK}"
    log "waiting for ${PG_LOCK}"
    flock -w 21600 9 || { log "lock timeout"; exit 4; }
    log "lock acquired"
fi

restore_and_check() {
    root restore "${SNAP}" || return 1
    root snapshot > "${OUT}/.now.snap" || return 1
    cmp -s "${SNAP}" "${OUT}/.now.snap"
}

cleanup() {
    docker rm -f "${NB}" "${SERVER}" > /dev/null 2>&1
    if restore_and_check; then log "exit: cpufreq restored, identical to snapshot"
    else log "exit: CPUFREQ RESTORE CHECK FAILED"; fi
    rm -f "${OUT}/.now.snap"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

root snapshot > "${OUT}/.now.snap" || { log "abort: snapshot read failed"; exit 5; }
if ! cmp -s "${SNAP}" "${OUT}/.now.snap"; then
    log "abort: host cpufreq differs from the campaign snapshot; not touching it"
    trap - EXIT
    rm -f "${OUT}/.now.snap"
    exit 5
fi

E_MAX=$(cat /sys/devices/system/cpu/cpu${CAP_FIRST}/cpufreq/cpuinfo_max_freq)
E_MIN=$(cat /sys/devices/system/cpu/cpu${CAP_FIRST}/cpufreq/cpuinfo_min_freq)
cap_for() {
    case $1 in
        Q|N0) echo "" ;;
        C75) echo $((E_MAX * 75 / 100 / 1000 * 1000)) ;;
        C50) echo $((E_MAX * 50 / 100 / 1000 * 1000)) ;;
        CMIN) echo "${E_MIN}" ;;
    esac
}
PKG_ZONE=$(grep -lx x86_pkg_temp /sys/class/thermal/thermal_zone*/type | head -1 | xargs dirname)
throttle() { cat /sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_count; }
caps_read() { local i; for ((i = CAP_FIRST; i <= CAP_LAST; i++)); do cat /sys/devices/system/cpu/cpu${i}/cpufreq/scaling_max_freq; done | paste -sd,; }

log "round ${ROUND} method ${METHOD} order ${ORDER[*]}"
docker rm -f "${SERVER}" "${NB}" > /dev/null 2>&1
docker run -d --name "${SERVER}" --cpuset-cpus "${INF_CPUS}" \
    -v "${MODEL_DIR}:/model:ro" -p "127.0.0.1:${PORT}:8000" "${OVMS_IMAGE}" \
    --rest_port 8000 --model_path /model --model_name "${MODEL_NAME}" 9>&- > "${OUT}/server-r${ROUND}.id" \
    || { log "abort: server start failed"; exit 6; }
ok=0
for _ in $(seq 1 120); do
    curl -fs "http://127.0.0.1:${PORT}/v2/health/ready" > /dev/null 2>&1 && { ok=1; break; }
    sleep 5
done
[ "${ok}" = 1 ] || { docker logs "${SERVER}" > "${OUT}/server-r${ROUND}.log" 2>&1; log "abort: server not ready"; exit 6; }
log "server ready"
python3 "${REPO}/bench/llm_load.py" --base-url "${BASE_URL}" --model "${MODEL_NAME}" \
    --prompt "$(cat "${PROMPT_FILE}")" --unique-prompts --max-tokens 16 \
    --requests 2 --warmup 0 --concurrency 1 --out "${OUT}/warmup-r${ROUND}.json" > /dev/null \
    || { log "abort: warm-up failed"; exit 6; }

pos=0
for arm in "${ORDER[@]}"; do
    pos=$((pos + 1))
    d="${OUT}/${METHOD}-r${ROUND}-p${pos}-${arm}"
    mkdir -p "${d}"
    log "run ${arm} (position ${pos}): idle ${IDLE_S}s"
    sleep "${IDLE_S}"
    cap=$(cap_for "${arm}")
    t_start=$(utc)
    temp0=$(cat "${PKG_ZONE}/temp")
    load1=$(cut -d' ' -f1 /proc/loadavg)
    thr0=$(throttle)
    [ -n "${cap}" ] && { root cap "${cap}" "${CAP_FIRST}" "${CAP_LAST}" || { log "abort: cap write failed"; exit 7; }; }
    nb_start=""
    if [ "${arm}" != Q ]; then
        docker run -d --name "${NB}" --cpuset-cpus "${NB_CPUS}" "${STRESS_IMAGE}" \
            --cpu 15 --cpu-method "${METHOD}" --timeout "${NB_TIMEOUT_S}s" --metrics-brief 9>&- > /dev/null \
            || { log "abort: neighbour start failed"; exit 7; }
        nb_start=$(now)
    fi
    sleep "${LEAD_S}"
    rapl0=$(root rapl); m0=$(now)
    python3 "${REPO}/bench/llm_load.py" --base-url "${BASE_URL}" --model "${MODEL_NAME}" \
        --prompt "$(cat "${PROMPT_FILE}")" --unique-prompts --max-tokens 16 \
        --requests 3 --warmup 1 --concurrency 1 --out "${d}/prefill.json" > /dev/null
    rc_prefill=$?
    capread=$(caps_read)
    nb_alive_capread=$(docker inspect -f '{{.State.Running}}' "${NB}" 2>/dev/null) || nb_alive_capread=none
    python3 "${REPO}/bench/llm_load.py" --base-url "${BASE_URL}" --model "${MODEL_NAME}" \
        --prompt "$(cat "${PROMPT_FILE}")" --unique-prompts --max-tokens 128 --ignore-eos \
        --requests 1 --warmup 0 --concurrency 1 --out "${d}/decode.json" > /dev/null
    rc_decode=$?
    tp0=$(now)
    taskset -c "${TOOL_CPU}" python3 -c 'import json; d=[{"k": i, "v": str(i) * 8} for i in range(200000)]; [json.loads(json.dumps(d)) for _ in range(3)]'
    rc_tool=$?
    tp1=$(now)
    rapl1=$(root rapl); m1=$(now)
    thr1=$(throttle)
    nb_alive_end=$(docker inspect -f '{{.State.Running}}' "${NB}" 2>/dev/null) || nb_alive_end=none
    if [ "${arm}" != Q ]; then
        timeout $((NB_TIMEOUT_S + 60)) docker wait "${NB}" > "${d}/nb.exit" 2>&1
        docker logs "${NB}" > "${d}/nb.log" 2>&1
        docker rm -f "${NB}" > /dev/null 2>&1
    fi
    nb_end=$(now)
    restore_and_check && restored=true || restored=false
    t_end=$(utc)
    [ "${restored}" = true ] || { log "abort: cpufreq restore check failed after ${arm}"; exit 8; }
    ARM="${arm}" METHOD="${METHOD}" ROUND="${ROUND}" POS="${pos}" CAP="${cap}" T_START="${t_start}" T_END="${t_end}" \
    TEMP0="${temp0}" LOAD1="${load1}" THR0="${thr0}" THR1="${thr1}" RAPL0="${rapl0}" RAPL1="${rapl1}" M0="${m0}" M1="${m1}" \
    CAPREAD="${capread}" NB_ALIVE_CAPREAD="${nb_alive_capread}" NB_ALIVE_END="${nb_alive_end}" NB_START="${nb_start}" \
    NB_END="${nb_end}" TP0="${tp0}" TP1="${tp1}" RC="${rc_prefill},${rc_decode},${rc_tool}" RESTORED="${restored}" \
    python3 - "${d}/run.json" <<'PY'
import json, os, re, sys
e = os.environ
r0, rng = (int(x) for x in e["RAPL0"].split())
r1 = int(e["RAPL1"].split()[0])
energy = ((r1 - r0) % rng) / 1e6
dur = float(e["M1"]) - float(e["M0"])
d = os.path.dirname(sys.argv[1])
bogo = None
log = os.path.join(d, "nb.log")
if os.path.exists(log):
    for line in open(log):
        m = re.search(r"metrc: \[\d+\] cpu\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", line)
        if m:
            bogo = {"bogo_ops": int(m.group(1)), "real_s": float(m.group(2)), "bogo_ops_per_s_real": float(m.group(5))}
rec = {
    "arm": e["ARM"], "method": e["METHOD"], "round": int(e["ROUND"]), "position": int(e["POS"]),
    "cap_khz": int(e["CAP"]) if e["CAP"] else None,
    "t_start": e["T_START"], "t_end": e["T_END"],
    "pkg_temp_start_c": int(e["TEMP0"]) / 1000, "load1_start": float(e["LOAD1"]),
    "package_throttle_delta": int(e["THR1"]) - int(e["THR0"]),
    "rapl_energy_j": round(energy, 3), "measure_s": round(dur, 3), "package_w": round(energy / dur, 2),
    "cap_read_khz": [int(x) for x in e["CAPREAD"].split(",")],
    "neighbour_running_at_cap_read": e["NB_ALIVE_CAPREAD"], "neighbour_running_at_end": e["NB_ALIVE_END"],
    "neighbour": bogo,
    "tool_proxy_s": round(float(e["TP1"]) - float(e["TP0"]), 3),
    "exit_codes_prefill_decode_tool": [int(x) for x in e["RC"].split(",")],
    "cpufreq_restored_identical": e["RESTORED"] == "true",
}
json.dump(rec, open(sys.argv[1], "w"), indent=1)
PY
    log "run ${arm} done"
done
docker logs "${SERVER}" > "${OUT}/server-r${ROUND}.log" 2>&1
log "round ${ROUND} done"
