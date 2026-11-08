#!/usr/bin/env bash
# H34 live probe (EXPERIMENTS.md, "H34 pre-registration"): OVMS Fast Draft with the
# 0.6B draft on the P-cores (B) vs the 4B target alone (A), both on the 16-CPU
# P-core balloon, under the SAME controlled background load (bench/load/load.py on
# the E-cores), interleaved A B B A per pair, one load level at a time.
# Before every scored request a 5 s load window is sampled and tagged with the arm
# about to run; a level is INCONCLUSIVE only when those windows are unmatched.
#
# Run it under the box-wide live lock:
#   SUDO_ASKPASS=<askpass> flock -w 14400 <scratchpad>/live.lock bench/hetero-spec/h34.sh [outdir]
# Budget: H34_LEVEL_S (1200 s) of wall time per level, the first level's budget
# includes cluster setup; a level stops scoring 120 s before its budget ends.
# Always deletes its cluster (k3dgpu-i-spec-decode-cpu) and the load containers on
# exit. Scratch KUBECONFIG, never ~/.kube/config. RAPL is only read (sudo -A).

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
OUT=${1:-"${ROOT}/bench/hetero-spec/out-h34-$(date +%Y%m%dT%H%M%S)"}
CLUSTER=${H34_CLUSTER:-k3dgpu-i-spec-decode-cpu}
# H34x (cross-host): K3D_GPU_VENDORS for `up` and an optional node-container cpuset
# (the CPUs outside the load set on a non-hybrid host). Defaults are the i9 run's.
VENDORS=${H34_VENDORS:-intel,hybrid}
NODE_CPUS=${H34_NODE_CPUS:-}
if [ "${H34_PRINT_CONFIG:-0}" = 1 ]; then echo "cluster=${CLUSTER} vendors=${VENDORS} node_cpus=${NODE_CPUS}"; exit 0; fi
NS=spec-decode-cpu
LEVELS=${H34_LEVELS:-"typical heavy"}
PAIRS=${H34_PAIRS:-12}
LEVEL_S=${H34_LEVEL_S:-1200}
SETTLE_S=${H34_SETTLE_S:-60}
WIN_S=${H34_WIN_S:-5}
READY_S=${H34_READY_S:-240}
MODELS=${H22_MODELS:-${HOME}/h22-models}
PORT=${H34_PORT:-18034}
LOAD=(python3 "${ROOT}/bench/load/load.py")
START=$(date +%s)
OVMS_IMAGE=$(sed -n 's/^AGENT_OVMS_IMAGE="\(.*\)"$/\1/p' "${ROOT}/scripts/k3d-gpu")
TARGET=OpenVINO/Qwen3-4B-int4-ov
DRAFT=OpenVINO/Qwen3-0.6B-int4-ov
declare -A GRAPH=([A]=a-4b-cpu [B]=b-spec-cpu)
ZONE=/sys/class/powercap/intel-rapl:0

# Package energy counter in uJ (root-only on current kernels: sudo -A / -n read).
rapl() {
    local v
    v=$(cat "${ZONE}/energy_uj" 2>/dev/null) || v=""
    if [ -z "${v}" ]; then
        if [ -n "${SUDO_ASKPASS:-}" ]; then v=$(sudo -A cat "${ZONE}/energy_uj" 2>/dev/null); else v=$(sudo -n cat "${ZONE}/energy_uj" 2>/dev/null); fi
    fi
    # shellcheck disable=SC2086 # a command prefix, split on purpose (as load.py splits it)
    [ -n "${v}" ] || [ -z "${K3D_GPU_RAPL_EXEC:-}" ] || v=$(${K3D_GPU_RAPL_EXEC} cat "${ZONE}/energy_uj" 2>/dev/null)
    echo "${v:-0}"
}
if [ "${H34_SELFTEST:-0}" = 1 ]; then
    [[ "$(rapl)" =~ ^[0-9]+$ ]] || exit 1
    "${LOAD[@]}" order A B --reps 1 >/dev/null || exit 1
    echo "selftest ok"; exit 0
fi

mkdir -p "${OUT}"
export KUBECONFIG="${OUT}/kubeconfig"
JSONL="${OUT}/run.jsonl"
LOG="${OUT}/probe.log"
PF_PID=""
log() { echo "$(date +%T) +$(( $(date +%s) - START ))s $*" | tee -a "${LOG}"; }
now() { date +%s; }

cleanup() {
    [ -z "${PF_PID}" ] || kill "${PF_PID}" 2>/dev/null || true
    "${LOAD[@]}" stop >>"${LOG}" 2>&1 || true
    log "deleting cluster ${CLUSTER}"
    k3d cluster delete "${CLUSTER}" >>"${LOG}" 2>&1 || true
}
NLEV=$(wc -w <<< "${LEVELS}")
HARD_S=$(( LEVEL_S * NLEV + 120 ))
( sleep "${HARD_S}"; log "HARD STOP at ${HARD_S}s"; kill -TERM $$; pkill -TERM -P $$ ) & WATCHDOG=$!
trap 'kill ${WATCHDOG} 2>/dev/null; cleanup' EXIT
trap 'exit 124' TERM
for m in "${TARGET}" "${DRAFT}"; do
    [ -f "${MODELS}/${m}/openvino_model.xml" ] || { log "ABORT: ${MODELS}/${m} not pre-staged"; exit 2; }
done
[[ "$(rapl)" =~ ^[1-9][0-9]*$ ]] || { log "ABORT: RAPL energy_uj unreadable (set SUDO_ASKPASS)"; exit 2; }

graph() {  # graph <name> [draft_device]
    local extra=""
    [ -z "${2:-}" ] || extra="
          draft_models_path: \"/models/${DRAFT}\",
          draft_device: \"$2\","
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
          models_path: "/models/${TARGET}",${extra}
          device: "CPU",
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
graph_entry() { printf '  %s.pbtxt: |\n' "$1"; graph "$@" | sed 's/^/    /'; }
manifest() {
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
      {"config": {"name": "a-4b-cpu", "base_path": "/graphs", "graph_path": "a-4b-cpu.pbtxt"}},
      {"config": {"name": "b-spec-cpu", "base_path": "/graphs", "graph_path": "b-spec-cpu.pbtxt"}}
    ]}
EOF
    graph_entry a-4b-cpu; graph_entry b-spec-cpu CPU
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
  containers:
    - name: ovms
      image: ${OVMS_IMAGE}
      args: [--rest_port=8000, --config_path=/graphs/config.json, --cache_dir=/cache, --log_level=INFO]
      readinessProbe: {httpGet: {path: /v2/health/ready, port: 8000}, periodSeconds: 5}
      resources:
        requests: {cpu: "16", memory: 10Gi}
      volumeMounts: [{name: models, mountPath: /models, readOnly: true}, {name: cache, mountPath: /cache}, {name: graphs, mountPath: /graphs}]
  volumes:
    - {name: models, hostPath: {path: /var/lib/h22-models, type: Directory}}
    - {name: cache, emptyDir: {}}
    - {name: graphs, configMap: {name: graphs}}
EOF
}

log "H34 probe -> ${OUT} (levels='${LEVELS}', pairs=${PAIRS}, level budget ${LEVEL_S}s)"
sha256sum "${HERE}/prompts.jsonl" | tee -a "${LOG}"
"${LOAD[@]}" stop >>"${LOG}" 2>&1
K3D_GPU_CLUSTER=${CLUSTER} K3D_GPU_VENDORS=${VENDORS} timeout 300 "${ROOT}/scripts/k3d-gpu" up >>"${LOG}" 2>&1 \
    || { log "ABORT: k3d-gpu up failed"; exit 4; }
if [ -n "${NODE_CPUS}" ]; then
    docker update --cpuset-cpus "${NODE_CPUS}" "k3d-${CLUSTER}-server-0" >>"${LOG}" 2>&1 \
        || { log "ABORT: node cpuset ${NODE_CPUS} not applied"; exit 4; }
    log "node k3d-${CLUSTER}-server-0 pinned to CPUs ${NODE_CPUS}"
fi
for node in $(docker ps --filter "label=k3d.cluster=${CLUSTER}" --format '{{.Names}}'); do
    case "${node}" in *-serverlb|*-tools) continue ;; esac
    timeout 180 docker cp "${MODELS}/." "${node}:/var/lib/h22-models" >>"${LOG}" 2>&1 \
        || { log "ABORT: model copy into ${node} failed"; exit 4; }
done
manifest > "${OUT}/manifest.yaml"
timeout 60 kubectl apply -f "${OUT}/manifest.yaml" >>"${LOG}" 2>&1 || { log "ABORT: apply failed"; exit 4; }
if ! kubectl -n "${NS}" wait --for=condition=Ready pod/ovms --timeout="${READY_S}s" >>"${LOG}" 2>&1; then
    kubectl -n "${NS}" logs ovms --tail=80 >>"${LOG}" 2>&1
    echo '{"kind":"p0","ready":false}' >> "${JSONL}"
    log "ABORT: server not Ready in ${READY_S}s (BLOCKED)"; exit 5
fi
echo '{"kind":"p0","ready":true}' >> "${JSONL}"
kubectl -n "${NS}" get pod ovms -o wide >>"${LOG}" 2>&1
kubectl -n "${NS}" port-forward pod/ovms "${PORT}:8000" >>"${LOG}" 2>&1 & PF_PID=$!
sleep 3
URL="http://127.0.0.1:${PORT}"

req() {  # req <level> <pair> <arm> <prompt index>: load window, then one scored request
    local e0 e1 rec t0 t1
    "${LOAD[@]}" sample --seconds "${WIN_S}" --tag "$3" --level "$1" --out "${OUT}/load-$1.jsonl"
    e0=$(rapl); t0=$(date +%s.%N)
    rec=$(timeout 150 python3 "${HERE}/req.py" "${URL}" "${GRAPH[$3]}" "${HERE}/prompts.jsonl" "$4")
    e1=$(rapl); t1=$(date +%s.%N)
    python3 - "$1" "$2" "$3" "${rec}" "${e0}" "${e1}" "${t0}" "${t1}" "$(cat "${ZONE}/max_energy_range_uj" 2>/dev/null || echo 0)" <<'PY' >> "${JSONL}"
import json, sys
lv, pair, arm, rec, e0, e1, t0, t1, rng = sys.argv[1:]
try:
    rec = json.loads(rec)
except ValueError as exc:
    rec = {"decode_tps": None, "error": repr(exc), "raw": rec[:300]}
d = int(e1) - int(e0)
if d < 0:
    d += int(rng)
ok_e = int(e0) > 0 and int(e1) > 0
print(json.dumps(dict(rec, kind="req", level=lv, pair=int(pair), arm=arm,
                      energy_j=(d / 1e6) if ok_e else None, wall_s=round(float(t1) - float(t0), 3),
                      load1=float(open("/proc/loadavg").read().split()[0]))))
PY
}

for p in A B; do timeout 150 python3 "${HERE}/req.py" "${URL}" "${GRAPH[$p]}" "${HERE}/prompts.jsonl" 0 >/dev/null; done
log "server warm"
budget_end=$(( START + LEVEL_S ))
for lv in ${LEVELS}; do
    [ "$(now)" -lt $(( budget_end - 300 )) ] || { log "level ${lv}: no budget left, skipped"; budget_end=$(( budget_end + LEVEL_S )); continue; }
    log "level ${lv}: start load ($("${LOAD[@]}" start --level "${lv}" --ttl $(( LEVEL_S + 300 )) 2>&1))"
    sleep "${SETTLE_S}"
    for p in A B; do timeout 150 python3 "${HERE}/req.py" "${URL}" "${GRAPH[$p]}" "${HERE}/prompts.jsonl" 1 >/dev/null; done
    for (( i = 0; i < PAIRS; i++ )); do
        [ "$(now)" -lt $(( budget_end - 120 )) ] || { log "level ${lv}: budget reached, ${i}/${PAIRS} pairs"; break; }
        if (( i % 2 == 0 )); then order="A B"; else order="B A"; fi
        for arm in ${order}; do req "${lv}" "${i}" "${arm}" $(( i % 8 )); done
        log "level ${lv}: pair $(( i + 1 )) done (${order})"
    done
    "${LOAD[@]}" stop >>"${LOG}" 2>&1
    log "level ${lv}: done, load stopped"
    budget_end=$(( $(now) + LEVEL_S ))
done
python3 "${HERE}/h34.py" "${JSONL}" "${OUT}"/load-*.jsonl | tee "${OUT}/verdict.json"
