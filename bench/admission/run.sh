#!/usr/bin/env bash
# run.sh OUT ROUND: one round of the H55 probe (host level, no cluster), as
# pre-registered in EXPERIMENTS.md "H55 pre-registration". Pre-registered, not run.
#
# Forked from bench/prefill-guard/run.sh (H5 stage 1): same OVMS server, model,
# prompt, pins and neighbour image. A round holds two locks (the bench lock, then
# the live lock, in that order), starts OVMS on CPUs 0-14, warms it, runs 10
# quiet pre-roll prefill requests (SLO = 1.10 x their median TTFT), then the five
# arms in the Latin-square order of ROUND (1-5). Each run: 60 s idle; the
# stress-ng neighbour on CPUs 17-31 with the arm's cpu.max (none for Q); for A_slo
# ctl.py takes cpu.max over; 15 s lead; a 900 s window of closed-loop prefill
# requests (load.py, 16 tokens); one 128-token decode request; the tool proxy on
# CPU 16; then it waits for the neighbour (--timeout 960s) and ctl.py to exit.
# H55 writes no cpufreq value; the campaign snapshot is checked, read only,
# before the round and after each run.
#
# Environment:
#   AD_LIVE_LOCK   the live lock (the scratchpad live.lock H5 used); required
#   AD_BENCH_LOCK  the bench lock; default ~/k3dgpu-bench.lock
#   AD_SNAPSHOT    campaign cpufreq snapshot (bench/prefill-guard/cpufreq.sh snapshot); required
#   AD_MODEL_DIR   OpenVINO/Qwen3-4B-int4-ov directory (with graph.pbtxt); required
#   AD_SUDO_PASS   file holding the sudo password (sudo -S); unset: sudo -n
#   AD_HOLD_S AD_IDLE_S AD_LEAD_S AD_WINDOW_S AD_NB_TIMEOUT_S AD_PORT
#                  7200, 60, 15, 900, 960, 18006
set -uo pipefail

OUT=${1:?usage: run.sh OUT ROUND}
ROUND=${2:?round}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
CPUFREQ=${REPO}/bench/prefill-guard/cpufreq.sh
PROMPT_FILE=${REPO}/bench/prefill-guard/prompt.txt
OVMS_IMAGE=${AD_OVMS_IMAGE:-openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58}
STRESS_IMAGE=${AD_STRESS_IMAGE:-ghcr.io/colinianking/stress-ng@sha256:2ea9a7c3828eda1b1ca3d0b0c5a669ec252f9caf97a6a33aedcea6b2a66d1cbe}
MODEL_NAME=Qwen3-4B-int4-ov
HOLD_S=${AD_HOLD_S:-7200}
IDLE_S=${AD_IDLE_S:-60}
LEAD_S=${AD_LEAD_S:-15}
WINDOW_S=${AD_WINDOW_S:-900}
NB_TIMEOUT_S=${AD_NB_TIMEOUT_S:-960}
PORT=${AD_PORT:-18006}
LIVE_LOCK=${AD_LIVE_LOCK:?AD_LIVE_LOCK is required}
BENCH_LOCK=${AD_BENCH_LOCK:-${HOME}/k3dgpu-bench.lock}
SNAP=${AD_SNAPSHOT:?AD_SNAPSHOT is required}
MODEL_DIR=${AD_MODEL_DIR:?AD_MODEL_DIR is required}
SERVER=k3dgpu-h55-ovms
NB=k3dgpu-h55-nb
INF_CPUS=0-14
NB_CPUS=17-31
TOOL_CPU=16
PERIOD=100000
BASE_URL="http://127.0.0.1:${PORT}/v3"
IMC_EVENTS=uncore_imc_free_running_0/data_total/,uncore_imc_free_running_1/data_total/

case ${ROUND} in 1|2|3|4|5) ;; *) echo "round must be 1-5" >&2; exit 2;; esac

# Latin square: round r starts at arm r.
ARMS=(Q N0 K50 K25 A_slo)
ORDER=()
for i in 0 1 2 3 4; do ORDER+=("${ARMS[$(((ROUND - 1 + i) % 5))]}"); done

# Neighbour cpu.max per arm ("" = no neighbour). A_slo starts at the 25% level.
quota_for() {
    case $1 in
        Q) echo "" ;;
        N0) echo "max ${PERIOD}" ;;
        K50) echo "750000 ${PERIOD}" ;;
        K25|A_slo) echo "375000 ${PERIOD}" ;;
    esac
}

# Children never inherit the lock descriptors (8, 9): a child that outlives the
# round must not keep either lock held.
nofd() { "$@" 8>&- 9>&-; }
as_root() {
    if [ -n "${AD_SUDO_PASS:-}" ]; then
        nofd sudo -S -p '' "$@" < "${AD_SUDO_PASS}"
    else
        nofd sudo -n "$@"
    fi
}

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "${OUT}/round${ROUND}.log" >&2; }
now() { date +%s.%N; }

mkdir -p "${OUT}"
use=$(df --output=pcent / | tail -1 | tr -dc 0-9)
if [ "${use}" -gt 97 ]; then log "abort: / at ${use}%"; exit 3; fi

exec 8>"${BENCH_LOCK}" 9>"${LIVE_LOCK}"
log "waiting for ${BENCH_LOCK} then ${LIVE_LOCK}"
flock -w 21600 8 || { log "bench lock timeout"; exit 4; }
flock -w 21600 9 || { log "live lock timeout"; exit 4; }
log "locks acquired; hold cap ${HOLD_S}s"

ctl_pid=""
cleanup() {
    [ -n "${watchdog:-}" ] && kill "${watchdog}" 2> /dev/null
    nofd docker rm -f "${NB}" "${SERVER}" > /dev/null 2>&1
    [ -n "${ctl_pid}" ] && as_root pkill -f "ctl.py --container ${NB} " > /dev/null 2>&1
    log "exit: containers removed"
}
trap cleanup EXIT
trap 'log "round ${ROUND} INCOMPLETE: hold cap or signal"; echo INCOMPLETE > "${OUT}/round${ROUND}.status"; exit 130' INT TERM
( sleep "${HOLD_S}"; kill -TERM $$ ) 8>&- 9>&- &
watchdog=$!

snap_check() {
    as_root bash "${CPUFREQ}" snapshot > "${OUT}/.now.snap" && cmp -s "${SNAP}" "${OUT}/.now.snap"
}
snap_check || { log "abort: host cpufreq differs from the campaign snapshot"; exit 5; }

PKG_ZONE=$(grep -lx x86_pkg_temp /sys/class/thermal/thermal_zone*/type | head -1 | xargs dirname)
throttle() { cat /sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_count; }
cgroup_cpumax() {
    local pid p
    pid=$(nofd docker inspect -f '{{.State.Pid}}' "${NB}" 2> /dev/null) || return 1
    p=$(sed -n 's/^0:://p' "/proc/${pid}/cgroup" 2> /dev/null) || return 1
    cat "/sys/fs/cgroup${p}/cpu.max" 2> /dev/null
}
prefill() {  # prefill N OUTFILE: N closed-loop 16-token requests through llm_load.py
    nofd python3 "${REPO}/bench/llm_load.py" --base-url "${BASE_URL}" --model "${MODEL_NAME}" \
        --prompt "$(cat "${PROMPT_FILE}")" --unique-prompts --max-tokens 16 \
        --requests "$1" --warmup 0 --concurrency 1 --out "$2" > /dev/null
}

log "round ${ROUND} order ${ORDER[*]}"
nofd docker rm -f "${SERVER}" "${NB}" > /dev/null 2>&1
nofd docker run -d --name "${SERVER}" --cpuset-cpus "${INF_CPUS}" \
    -v "${MODEL_DIR}:/model:ro" -p "127.0.0.1:${PORT}:8000" "${OVMS_IMAGE}" \
    --rest_port 8000 --model_path /model --model_name "${MODEL_NAME}" > "${OUT}/server-r${ROUND}.id" \
    || { log "abort: server start failed"; exit 6; }
ok=0
for _ in $(seq 1 120); do
    curl -fs "http://127.0.0.1:${PORT}/v2/health/ready" > /dev/null 2>&1 && { ok=1; break; }
    sleep 5
done
[ "${ok}" = 1 ] || { nofd docker logs "${SERVER}" > "${OUT}/server-r${ROUND}.log" 2>&1; log "abort: server not ready"; exit 6; }
prefill 2 "${OUT}/warmup-r${ROUND}.json" || { log "abort: warm-up failed"; exit 6; }
prefill 10 "${OUT}/preroll-r${ROUND}.json" || { log "abort: pre-roll failed"; exit 6; }
SLO=$(python3 -c 'import json,statistics,sys; s=[x["ttft_s"] for p in json.load(open(sys.argv[1]))["phases"] for x in p["samples"] if x["ok"]]; assert len(s)==10, len(s); print(round(1.10*statistics.median(s), 4))' "${OUT}/preroll-r${ROUND}.json") \
    || { log "abort: pre-roll incomplete"; exit 6; }
echo "${SLO}" > "${OUT}/slo-r${ROUND}.txt"
log "server ready; SLO ${SLO}s"

pos=0
for arm in "${ORDER[@]}"; do
    pos=$((pos + 1))
    d="${OUT}/r${ROUND}-p${pos}-${arm}"
    mkdir -p "${d}"
    log "run ${arm} (position ${pos}): idle ${IDLE_S}s"
    sleep "${IDLE_S}"
    q=$(quota_for "${arm}")
    temp0=$(cat "${PKG_ZONE}/temp"); load1=$(cut -d' ' -f1 /proc/loadavg); thr0=$(throttle)
    if [ -n "${q}" ]; then
        qa=(--cpu-period "${PERIOD}")
        [ "${arm}" = N0 ] || qa+=(--cpu-quota "${q%% *}")
        nofd docker run -d --name "${NB}" --cpuset-cpus "${NB_CPUS}" "${qa[@]}" "${STRESS_IMAGE}" \
            --cpu 15 --cpu-method matrixprod --timeout "${NB_TIMEOUT_S}s" --metrics-brief > /dev/null \
            || { log "abort: neighbour start failed"; exit 7; }
    fi
    w0=$(python3 -c "import time; print(time.time() + ${LEAD_S})")
    w1=$(python3 -c "print(${w0} + ${WINDOW_S})")
    if [ "${arm}" = A_slo ]; then
        as_root taskset -c "${TOOL_CPU}" python3 "${HERE}/ctl.py" --container "${NB}" --slo "${SLO}" \
            --jsonl "${d}/load.jsonl" --log "${d}/ctl.jsonl" --until "${w1}" &
        ctl_pid=$!
    fi
    sleep "${LEAD_S}"
    reads=()
    [ -n "${q}" ] && reads+=("$(cgroup_cpumax)")
    as_root perf stat -a -x, -I 1000 -e "${IMC_EVENTS}" -o "${d}/imc.csv" -- sleep "${WINDOW_S}" > /dev/null 2>&1 &
    imc_pid=$!
    rapl0=$(as_root bash "${CPUFREQ}" rapl); m0=$(now)
    nofd python3 "${HERE}/load.py" --base-url "${BASE_URL}" --model "${MODEL_NAME}" \
        --prompt-file "${PROMPT_FILE}" --until "${w1}" --out "${d}/load.jsonl"
    rc_load=$?
    rapl1=$(as_root bash "${CPUFREQ}" rapl); m1=$(now); thr1=$(throttle)
    [ -n "${q}" ] && reads+=("$(cgroup_cpumax)")
    nb_running=$(nofd docker inspect -f '{{.State.Running}}' "${NB}" 2> /dev/null) || nb_running=none
    ctl_alive=false
    [ -n "${ctl_pid}" ] && ps -p "${ctl_pid}" > /dev/null && ctl_alive=true
    wait "${imc_pid}"
    nofd python3 "${REPO}/bench/llm_load.py" --base-url "${BASE_URL}" --model "${MODEL_NAME}" \
        --prompt "$(cat "${PROMPT_FILE}")" --unique-prompts --max-tokens 128 --ignore-eos \
        --requests 1 --warmup 0 --concurrency 1 --out "${d}/decode.json" > /dev/null
    rc_decode=$?
    tp0=$(now)
    nofd taskset -c "${TOOL_CPU}" python3 -c 'import json; d=[{"k": i, "v": str(i) * 8} for i in range(200000)]; [json.loads(json.dumps(d)) for _ in range(3)]'
    rc_tool=$?
    tp1=$(now)
    if [ -n "${q}" ]; then
        nofd timeout $((NB_TIMEOUT_S + 60)) docker wait "${NB}" > "${d}/nb.exit" 2>&1
        nofd docker logs "${NB}" > "${d}/nb.log" 2>&1
        nofd docker rm -f "${NB}" > /dev/null 2>&1
    fi
    if [ -n "${ctl_pid}" ]; then wait "${ctl_pid}"; ctl_pid=""; fi
    snap_check && snap_ok=true || snap_ok=false
    ARM="${arm}" ROUND="${ROUND}" POS="${pos}" SLO="${SLO}" Q="${q}" W0="${w0}" W1="${w1}" \
    TEMP0="${temp0}" LOAD1="${load1}" THR0="${thr0}" THR1="${thr1}" RAPL0="${rapl0}" RAPL1="${rapl1}" \
    M0="${m0}" M1="${m1}" READS="$(printf '%s|' "${reads[@]}")" NB_RUNNING="${nb_running}" CTL_ALIVE="${ctl_alive}" \
    TP0="${tp0}" TP1="${tp1}" RC="${rc_load},${rc_decode},${rc_tool}" SNAP_OK="${snap_ok}" \
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
q = e["Q"] or None
rec = {
    "arm": e["ARM"], "round": int(e["ROUND"]), "position": int(e["POS"]), "slo_s": float(e["SLO"]),
    "window_start": float(e["W0"]), "window_end": float(e["W1"]),
    # A_slo: ctl.py owns cpu.max, so its trace (ctl.jsonl) replaces the read-back check.
    "cpumax_set": None if e["ARM"] == "A_slo" else q,
    "cpumax_reads": [x for x in e["READS"].split("|") if x],
    "pkg_temp_start_c": int(e["TEMP0"]) / 1000, "load1_start": float(e["LOAD1"]),
    "package_throttle_delta": int(e["THR1"]) - int(e["THR0"]),
    "rapl_energy_j": round(energy, 3), "measure_s": round(dur, 3), "package_w": round(energy / dur, 2),
    "neighbour_running_at_window_end": e["NB_RUNNING"] == "true",
    "ctl_alive_at_window_end": e["CTL_ALIVE"] == "true",
    "neighbour": bogo,
    "tool_proxy_s": round(float(e["TP1"]) - float(e["TP0"]), 3),
    "exit_codes_load_decode_tool": [int(x) for x in e["RC"].split(",")],
    "cpufreq_matches_snapshot": e["SNAP_OK"] == "true",
}
json.dump(rec, open(sys.argv[1], "w"), indent=1)
PY
    [ "${snap_ok}" = true ] || { log "abort: host cpufreq changed during ${arm}"; exit 8; }
    log "run ${arm} done"
done
nofd docker logs "${SERVER}" > "${OUT}/server-r${ROUND}.log" 2>&1
echo COMPLETE > "${OUT}/round${ROUND}.status"
log "round ${ROUND} done"
