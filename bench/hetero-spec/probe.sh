#!/usr/bin/env bash
# H22 hetero-spec live probe (EXPERIMENTS.md, "H22 pre-registration").
# One OVMS 2026.4.1 pod, five graphs: A target alone (CPU), B Fast Draft on CPU,
# C Fast Draft on the iGPU (draft_device GPU), DP and DG the draft alone on CPU
# and GPU. n=8 rounds, arm order rotated per round (Latin square), greedy,
# 256 tokens, ignore_eos, streamed, concurrency 1. Writes JSONL and runs analyze.py.
#
# Run it under the box-wide live lock, with no k3dgpu-f-* cluster present:
#   flock -w 10800 <scratchpad>/live.lock bench/hetero-spec/probe.sh [outdir]
# It self-limits to 20 min (HARD_S): it stops scoring at 18:00 and always deletes
# its cluster (k3dgpu-i-hetero-spec) on exit. It uses a scratch KUBECONFIG, never
# ~/.kube/config. Sysfs and /proc/<pid>/fdinfo are only read (sudo, SUDO_PASS env).

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
OUT=${1:-"${ROOT}/bench/hetero-spec/out-$(date +%Y%m%dT%H%M%S)"}
CLUSTER=k3dgpu-i-hetero-spec
NS=hetero-spec
ROUNDS=${H22_ROUNDS:-8}
HARD_S=${H22_HARD_S:-1200}     # 20 min wall budget under the lock
SCORE_S=${H22_SCORE_S:-1080}   # stop scoring at 18:00
READY_S=${H22_READY_S:-360}
PORT=${H22_PORT:-18022}
START=$(date +%s)
# Pinned in scripts/k3d-gpu (AGENT_OVMS_IMAGE): OVMS 2026.4.1.
OVMS_IMAGE=$(sed -n 's/^AGENT_OVMS_IMAGE="\(.*\)"$/\1/p' "${ROOT}/scripts/k3d-gpu")
TARGET=OpenVINO/Qwen3-4B-int4-ov
DRAFT=OpenVINO/Qwen3-0.6B-int4-ov
ARMS=(A B C DP DG)
declare -A GRAPH=([A]=a-4b-cpu [B]=b-spec-cpu [C]=c-spec-igpu [DP]=dp-06b-cpu [DG]=dg-06b-gpu)

mkdir -p "${OUT}"
export KUBECONFIG="${OUT}/kubeconfig"
JSONL="${OUT}/run.jsonl"
LOG="${OUT}/probe.log"
PF_PID=""
log() { echo "$(date +%T) +$(( $(date +%s) - START ))s $*" | tee -a "${LOG}"; }
elapsed() { echo $(( $(date +%s) - START )); }

cleanup() {
    [ -z "${PF_PID}" ] || kill "${PF_PID}" 2>/dev/null || true
    log "deleting cluster ${CLUSTER}"
    k3d cluster delete "${CLUSTER}" >>"${LOG}" 2>&1 || true
}
trap cleanup EXIT
# Hard wall: the whole probe ends at HARD_S even if a step hangs.
( sleep "${HARD_S}"; log "HARD STOP at ${HARD_S}s"; kill -TERM $$ ) & WATCHDOG=$!
trap 'kill ${WATCHDOG} 2>/dev/null; cleanup' EXIT

if k3d cluster list -o json 2>/dev/null | grep -q '"name": *"k3dgpu-f-'; then
    log "ABORT: a k3dgpu-f-* cluster exists; one live experiment at a time"; exit 3
fi

sread() { if [ -n "${SUDO_PASS:-}" ]; then sudo -S -p '' cat "$1" <<< "${SUDO_PASS}" 2>/dev/null; else cat "$1" 2>/dev/null; fi; }
# Host context for one request: throttle counts, package temp, iGPU freq, swap, load, RAPL.
host_sample() {
    local thr=0 f
    for f in /sys/devices/system/cpu/cpu*/thermal_throttle/package_throttle_count; do
        thr=$(( thr + $(cat "${f}" 2>/dev/null || echo 0) ))
    done
    printf '{"throttle":%s,"temp_mc":%s,"igpu_mhz":%s,"swap":"%s","load1":%s,"rapl_uj":%s}' \
        "${thr}" "$(cat /sys/class/hwmon/hwmon*/temp1_input 2>/dev/null | sort -n | tail -1 || echo 0)" \
        "$(cat /sys/class/drm/card*/gt/gt0/rps_act_freq_mhz 2>/dev/null | head -1 || echo 0)" \
        "$(awk '/^pswpin|^pswpout/ {s += $2} END {print s + 0}' /proc/vmstat)" \
        "$(cut -d' ' -f1 /proc/loadavg)" "$(sread /sys/class/powercap/intel-rapl:0/energy_uj || echo 0)"
}
# i915 render-engine busy ns summed over the OVMS process's DRM fds (sudo read).
gpu_busy() {
    local pid=$1 f s=0 v
    for f in /proc/"${pid}"/fdinfo/*; do
        v=$(sread "${f}" | awk '/^drm-engine-render:/ {print $2}')
        [ -z "${v}" ] || s=$(( s + v ))
    done
    echo "${s}"
}

# graph <name> <device> [draft_device]: one LLM graph.pbtxt (OVMS docs/llm/reference.md template).
graph() {
    local models=/models/${TARGET} extra=""
    case "$1" in dp-*|dg-*) models=/models/${DRAFT} ;; esac
    [ -z "${3:-}" ] || extra="
          draft_models_path: \"/models/${DRAFT}\",
          draft_device: \"$3\","
    cat <<EOF
input_stream: "HTTP_REQUEST_PAYLOAD:input"
output_stream: "HTTP_RESPONSE_PAYLOAD:output"
node: {
  name: "LLMExecutor"
  calculator: "HttpLLMCalculator"
  input_stream: "LOOPBACK:loopback"
  input_stream: "HTTP_REQUEST_PAYLOAD:input"
  input_side_packet: "LLM_NODE_RESOURCES:llm"
  output_stream: "LOOPBACK:loopback"
  output_stream: "HTTP_RESPONSE_PAYLOAD:output"
  input_stream_info: { tag_index: 'LOOPBACK:0', back_edge: true }
  node_options: {
      [type.googleapis.com / mediapipe.LLMCalculatorOptions]: {
          models_path: "${models}",${extra}
          device: "$2",
          cache_size: 2,
          enable_prefix_caching: false
      }
  }
  input_stream_handler {
    input_stream_handler: "SyncSetInputStreamHandler",
    options { [mediapipe.SyncSetInputStreamHandlerOptions.ext] { sync_set { tag_index: "LOOPBACK:0" } } }
  }
}
EOF
}

manifest() {
    local g
    cat <<EOF
apiVersion: v1
kind: Namespace
metadata: {name: ${NS}}
---
apiVersion: v1
kind: ConfigMap
metadata: {name: graphs, namespace: ${NS}}
data:
  config.json: |
    {"model_config_list": [
EOF
    local sep=""
    for g in a-4b-cpu b-spec-cpu c-spec-igpu dp-06b-cpu dg-06b-gpu; do
        printf '      %s{"config": {"name": "%s", "base_path": "/graphs", "graph_path": "%s.pbtxt"}}\n' "${sep}" "${g}" "${g}"
        sep=","
    done
    echo "    ]}"
    graph_entry a-4b-cpu CPU; graph_entry b-spec-cpu CPU CPU; graph_entry c-spec-igpu CPU GPU
    graph_entry dp-06b-cpu CPU; graph_entry dg-06b-gpu GPU
    cat <<EOF
---
apiVersion: v1
kind: Pod
metadata:
  name: ovms
  namespace: ${NS}
  labels: {k3d-gpu.io/cores: performance}
spec:
  restartPolicy: Never
  initContainers:
EOF
    for g in "${TARGET}" "${DRAFT}"; do
        cat <<EOF
    - name: pull-$(tr 'A-Z./' 'a-z--' <<< "${g##*/}")
      image: ${OVMS_IMAGE}
      args: [--pull, --source_model=${g}, --model_repository_path=/models, --task=text_generation]
      volumeMounts: [{name: models, mountPath: /models}]
EOF
    done
    cat <<EOF
  containers:
    - name: ovms
      image: ${OVMS_IMAGE}
      args: [--rest_port=8000, --config_path=/graphs/config.json, --cache_dir=/models/.ov_cache, --log_level=DEBUG]
      readinessProbe: {httpGet: {path: /v2/health/ready, port: 8000}, periodSeconds: 5}
      resources:
        requests: {cpu: "16", memory: 10Gi}
        limits: {gpu.intel.com/i915: 1}
      volumeMounts: [{name: models, mountPath: /models}, {name: graphs, mountPath: /graphs}]
  volumes:
    - {name: models, emptyDir: {}}
    - {name: graphs, configMap: {name: graphs}}
EOF
}
graph_entry() { printf '  %s.pbtxt: |\n' "$1"; graph "$@" | sed 's/^/    /'; }

log "H22 probe -> ${OUT} (rounds=${ROUNDS}, hard=${HARD_S}s)"
sha256sum "${HERE}/prompts.jsonl" | tee -a "${LOG}"
K3D_GPU_CLUSTER=${CLUSTER} K3D_GPU_VENDORS=intel,hybrid "${ROOT}/scripts/k3d-gpu" up >>"${LOG}" 2>&1 \
    || { log "ABORT: k3d-gpu up failed"; exit 4; }
manifest > "${OUT}/manifest.yaml"
kubectl apply -f "${OUT}/manifest.yaml" >>"${LOG}" 2>&1 || { log "ABORT: apply failed"; exit 4; }
if ! kubectl -n "${NS}" wait --for=condition=Ready pod/ovms --timeout="${READY_S}s" >>"${LOG}" 2>&1; then
    kubectl -n "${NS}" logs ovms --tail=80 >>"${LOG}" 2>&1
    echo '{"kind":"p0","draft_loaded":false,"gpu_busy_A":0,"gpu_busy_C":0,"why":"not ready"}' >> "${JSONL}"
    log "ABORT: server not Ready in ${READY_S}s (P0 fail)"; python3 "${HERE}/analyze.py" "${JSONL}" | tee "${OUT}/verdict.json"; exit 5
fi
kubectl -n "${NS}" port-forward pod/ovms "${PORT}:8000" >>"${LOG}" 2>&1 & PF_PID=$!
sleep 3
PID=$(pgrep -n -f -- '--config_path=/graphs/config.json' || true)
kubectl -n "${NS}" logs ovms > "${OUT}/ovms.log" 2>&1

req() {  # req <round> <arm> <prompt index>
    local before after b0 b1 rec
    before=$(host_sample); b0=$( [ -n "${PID}" ] && gpu_busy "${PID}" || echo 0 )
    rec=$(python3 "${HERE}/req.py" "http://127.0.0.1:${PORT}" "${GRAPH[$2]}" "${HERE}/prompts.jsonl" "$3")
    b1=$( [ -n "${PID}" ] && gpu_busy "${PID}" || echo 0 ); after=$(host_sample)
    python3 - "$1" "$2" "${rec}" "${before}" "${after}" "$(( b1 - b0 ))" <<'PY' >> "${JSONL}"
import json, sys
r, arm, rec, b, a, busy = sys.argv[1:]
rec, b, a = json.loads(rec), json.loads(b), json.loads(a)
flag = a["throttle"] > b["throttle"] or int(a["swap"]) > int(b["swap"])
print(json.dumps(dict(rec, kind="req", round=int(r), arm=arm, gpu_busy_ns=int(busy), flagged=flag,
                      rapl_uj=a["rapl_uj"] - b["rapl_uj"], temp_mc=a["temp_mc"], igpu_mhz=a["igpu_mhz"],
                      load1=a["load1"])))
PY
}

# P0 gate: GPU busy during A must be ~0, during C > 0 (else silent CPU fallback).
python3 "${HERE}/req.py" "http://127.0.0.1:${PORT}" a-4b-cpu "${HERE}/prompts.jsonl" 0 >/dev/null  # warm A
ba0=$(gpu_busy "${PID}"); python3 "${HERE}/req.py" "http://127.0.0.1:${PORT}" a-4b-cpu "${HERE}/prompts.jsonl" 0 >/dev/null; ba1=$(gpu_busy "${PID}")
for g in b-spec-cpu c-spec-igpu dp-06b-cpu dg-06b-gpu; do
    python3 "${HERE}/req.py" "http://127.0.0.1:${PORT}" "${g}" "${HERE}/prompts.jsonl" 0 >/dev/null  # warm-up, not scored
done
bc0=$(gpu_busy "${PID}"); python3 "${HERE}/req.py" "http://127.0.0.1:${PORT}" c-spec-igpu "${HERE}/prompts.jsonl" 0 >/dev/null; bc1=$(gpu_busy "${PID}")
loaded=false; grep -qi 'draft' "${OUT}/ovms.log" && loaded=true
printf '{"kind":"p0","draft_loaded":%s,"gpu_busy_A":%s,"gpu_busy_C":%s,"pid":"%s"}\n' \
    "${loaded}" "$(( ba1 - ba0 ))" "$(( bc1 - bc0 ))" "${PID}" >> "${JSONL}"
log "P0: draft_loaded=${loaded} gpu_busy A=$(( ba1 - ba0 )) C=$(( bc1 - bc0 ))"

for (( r = 0; r < ROUNDS; r++ )); do
    [ "$(elapsed)" -lt "${SCORE_S}" ] || { log "18:00 reached: partial, ${r}/${ROUNDS} rounds"; break; }
    for (( k = 0; k < ${#ARMS[@]}; k++ )); do
        arm=${ARMS[$(( (r + k) % ${#ARMS[@]} ))]}
        req $(( r + 1 )) "${arm}" $(( r % 8 ))
    done
    log "round $(( r + 1 )) done"
done
python3 "${HERE}/analyze.py" "${JSONL}" | tee "${OUT}/verdict.json"
