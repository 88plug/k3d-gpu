#!/usr/bin/env bash
# h54b.sh HOLD ARM...: one H54b hold, run inside hold.sh (EXPERIMENTS.md "H54 pre-registration",
# H54b, and amendment G1). Cluster k3dgpu-g-h54b-HOLD made by `k3d-gpu up` (C1's kubelet
# thresholds, the model tree mounted by bin/k3d's wrapper). Arms, one at a time, in the order
# given: X1q X1q-mtp (k3d-gpu agent manifest, device plugin), Lsq (llama.cpp SYCL), Sq Sq-mtp Rq
# (llm-scaler 1.5), B2q (EAT #21 vLLM chart, TP 2, device plugin). Device-plugin arms get the
# free cards through placeholder pods on the busy ones (hold_busy); the others run in pods whose
# /dev/dri holds only a free card. CARDS: comma list of free card indices (renderD128+N). Thinking off for every arm.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "${HERE}/../.." && pwd)
export ROOT
hold=$1
shift
OUT=${HOME}/k3dgpu-gemma/results/H54b/${SMOKE:+smoke-}hold${hold}
mkdir -p "${OUT}"
export KUBECONFIG=${OUT}/kubeconfig PATH=${HOME}/k3dgpu-gemma/bin:${PATH}
. "${HERE}/lib.sh"
CL=k3dgpu-g-h54b-${hold}
NOTHINK='{"chat_template_kwargs": {"enable_thinking": false}}'
OVMS_GPU=${OVMS_GPU:-openvino/model_server:2026.4.1-gpu@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58}
LCPP_INTEL=${LCPP_INTEL:-ghcr.io/ggml-org/llama.cpp:server-intel-b11749@sha256:ae2d41ba73574a1e12cd237c88b512c8bb39ba6f703e873912b76cb8988a81bd}
BUSYBOX=${BUSYBOX:-busybox:1.37@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e}
SCALER=${SCALER:-intel/llm-scaler-vllm:1.5@sha256:52218ad85513ab6686d4c090c83c2bd8c5b02423c63aa4dabd41837fe641fe3b}
QWEN=hf/hub/models--Qwen--Qwen3.8-27B/snapshots/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0
REDHAT=RedHatAI_Qwen3.8-27B-INT4
LSQ_GGUF=Intel_Qwen3.8-27B-q4km-AutoRound/Qwen3.8-27B-Q4_K_M.gguf

reproduce "bash bench/h54gpu/h54b.sh ${hold} $*" "${OVMS_GPU}" "${LCPP_INTEL}" "${SCALER}" "${BUSYBOX}"
IFS=, read -r -a free <<< "${CARDS:?CARDS=comma list of free card indices}"
[ -n "${SMOKE:-}" ] || for c in "${free[@]}"; do card_free "${c}" || { say "card ${c} busy at hold start: no hold"; exit 6; }; done
say "H54b hold ${hold} cards ${CARDS} arms $*"
K3D_GPU_CLUSTER=${CL} K3D_GPU_IMAGE=${NODE_IMAGE} K3D_GPU_K3S_ARGS=${C1_ARGS} \
    K3D_GPU_NODE_LABELS=ei-inference-eligible=true H54_MODELS=${MODELS} \
    "${ROOT}/scripts/k3d-gpu" up >> "${OUT}/cluster.log" 2>&1 || { say "k3d-gpu up failed"; exit 7; }
k3d kubeconfig get "${CL}" > "${KUBECONFIG}"
wait_sa || { say "no default ServiceAccount"; exit 7; }

# hold_busy: device-plugin arms get only the free cards. One placeholder pod per card claims
# every gpu.intel.com/xe (the plugin hands each a whole card, -shared-dev-num 1); the ones
# that got a free card are deleted, so the plugin has only the free cards left to give. The
# placeholders run busybox `sleep` and never open the device. True when the cards still held
# are exactly the busy ones (amendment G1).
hold_busy() {
    local i rd keep="" want="" c
    for c in 0 1 2 3; do [[ ",${CARDS}," == *",${c},"* ]] || want+="renderD$((128 + c)) "; done
    for i in 0 1 2 3; do
        cat <<YAML
apiVersion: v1
kind: Pod
metadata: {name: gpu-hold-${i}}
spec:
  restartPolicy: Never
  containers:
  - {name: hold, image: ${BUSYBOX}, command: [sleep, "86400"], resources: {limits: {gpu.intel.com/xe: 1}}}
---
YAML
    done > "${OUT}/gpu-hold.yaml"
    kubectl apply -f "${OUT}/gpu-hold.yaml" >> "${OUT}/arms.log" 2>&1 || return 1
    kubectl wait --for=condition=Ready pod/gpu-hold-0 pod/gpu-hold-1 pod/gpu-hold-2 pod/gpu-hold-3 \
        --timeout=300s >> "${OUT}/arms.log" 2>&1 || return 1
    for i in 0 1 2 3; do
        rd=$(kubectl exec "gpu-hold-${i}" -- ls /dev/dri 2>/dev/null | grep '^renderD' | tr '\n' ' ')
        echo "gpu-hold-${i}: ${rd}" >> "${OUT}/gpu-hold.txt"
        if [[ " ${want}" == *" ${rd% } "* ]]; then keep+="${rd% } "; else
            kubectl delete pod "gpu-hold-${i}" --wait=true --timeout=120s >> "${OUT}/arms.log" 2>&1
        fi
    done
    [ "$(tr ' ' '\n' <<< "${keep}" | sort | xargs)" = "$(tr ' ' '\n' <<< "${want}" | sort | xargs)" ]
}
dp_ok=""
if [ "${#free[@]}" -eq 4 ]; then dp_ok=1; elif hold_busy; then dp_ok=1; fi
say "device-plugin arms: ${dp_ok:+allowed}${dp_ok:-not run} ($(xargs < "${OUT}/gpu-hold.txt" 2>/dev/null))"
# SMOKE=1: the wiring smoke (amendment G1): cluster, placeholders and X1q's manifest render only;
# no engine starts and no card is opened. Discarded.
if [ -n "${SMOKE:-}" ]; then
    K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3.8-27B-int4-ov K3D_GPU_AGENT_REVISION=7b20ea8c7c8ad1d81c2e6b1785ba7cd9ed80a26d \
        K3D_GPU_AGENT_STORAGE=40Gi K3D_GPU_AGENT_IMAGE=${OVMS_GPU} K3D_GPU_AGENT_MODEL_NAME=m K3D_GPU_CLUSTER=${CL} \
        "${ROOT}/scripts/k3d-gpu" agent manifest > "${OUT}/X1q.yaml" 2>> "${OUT}/arms.log"
    kubectl get node -o jsonpath='{.items[0].status.allocatable}' > "${OUT}/allocatable.json"
    say "smoke done: manifest rc=$? $(grep -c 'gpu.intel.com/xe' "${OUT}/X1q.yaml") xe lines"
    exit 0
fi

# pinned_pod NAME CARD IMAGE COMMAND_JSON ARGS_JSON ENV_JSON: a privileged pod with the model tree
# and only card CARD's render node under /dev/dri.
pinned_pod() {
    local rd="renderD$((128 + $2))"
    cat <<YAML
apiVersion: v1
kind: Pod
metadata: {name: $1}
spec:
  restartPolicy: Never
  containers:
  - name: server
    image: $3
    command: $4
    args: $5
    env: $6
    securityContext: {privileged: true}
    ports: [{containerPort: 8080}]
    volumeMounts: [{name: models, mountPath: /models}, {name: dri, mountPath: /dev/dri}, {name: card, mountPath: /dev/dri/${rd}}, {name: shm, mountPath: /dev/shm}]
  volumes:
  - {name: models, hostPath: {path: /models}}
  - {name: dri, emptyDir: {medium: Memory}}
  - {name: card, hostPath: {path: /dev/dri/${rd}, type: CharDevice}}
  - {name: shm, emptyDir: {medium: Memory, sizeLimit: 16Gi}}
YAML
}

scaler_args() {   # scaler_args MODEL_DIR EXTRA...: llm-scaler 1.5's documented Qwen 27B flags, TP 1
    local m=$1 a out=""
    shift
    for a in serve "/models/${m}" --served-model-name m --host 0.0.0.0 --port 8080 \
        --tensor-parallel-size 1 --dtype float16 --block-size 64 --gpu-memory-utilization 0.9 \
        --max-model-len 8192 --max-num-batched-tokens 8192 --mamba-ssm-cache-dtype float16 \
        --enforce-eager --no-enable-prefix-caching --trust-remote-code "$@"; do
        out+="$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "${a}"),"
    done
    echo "[${out%,}]"
}

run_pinned() {   # run_pinned NAME YAML
    if serve_pod "$1" "$2" 18080; then
        kubectl exec "$1" -- ls -l /dev/dri > "${OUT}/$1.dri" 2>&1
        full_phases "$1" "http://127.0.0.1:18080/v1" "${free[0]}" "${NOTHINK}"
        say "arm $1 done"
    else
        say "arm $1 not deliverable"
    fi
    stop_pod "$1"
}

run_agent() {   # run_agent NAME [mtp]: k3d-gpu's own OVMS manifest, auto device
    local name=$1 m=${OUT}/$1.yaml
    K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3.8-27B-int4-ov K3D_GPU_AGENT_REVISION=7b20ea8c7c8ad1d81c2e6b1785ba7cd9ed80a26d \
        K3D_GPU_AGENT_STORAGE=40Gi K3D_GPU_AGENT_IMAGE=${OVMS_GPU} K3D_GPU_AGENT_MODEL_NAME=m K3D_GPU_CLUSTER=${CL} \
        ${2:+K3D_GPU_AGENT_PREFIX_CACHING=false} "${ROOT}/scripts/k3d-gpu" agent manifest > "${m}" 2>> "${OUT}/arms.log" ||
        { say "arm ${name}: manifest failed"; return; }
    # MTP: the OVMS v2026.4.1 demo's --draft_model_path . (the bundled openvino_mtp_model.xml).
    [ -z "${2:-}" ] || sed -i 's|^\( *\)- --task=text_generation$|&\n\1- --draft_model_path=.|' "${m}"
    kubectl apply -f "${m}" >> "${OUT}/arms.log" 2>&1
    if kubectl rollout status deploy/ovms --timeout="${BOOT_S:-900}s" >> "${OUT}/arms.log" 2>&1; then
        kubectl port-forward deploy/ovms 18000:8000 > "${OUT}/${name}.pf.log" 2>&1 &
        PF=$!
        sleep 5
        full_phases "${name}" "http://127.0.0.1:18000/v3" "${CARDS}" "${NOTHINK}"
        say "arm ${name} done"
    else
        echo "not deliverable in ${BOOT_S:-900} s" > "${OUT}/${name}.not-deliverable"
        say "arm ${name} not deliverable"
    fi
    kubectl logs deploy/ovms --all-containers > "${OUT}/${name}.log" 2>&1
    [ -z "${PF:-}" ] || kill "${PF}" 2>/dev/null
    PF=""
    kubectl delete -f "${m}" --wait=true --timeout=300s >> "${OUT}/arms.log" 2>&1
}

# Disk each arm adds while it runs (GB): its image unpacked in the node's containerd, plus
# X1q's model PVC. The models on the host were downloaded before the hold.
declare -A NEED=([X1q]=20 [X1q-mtp]=20 [Lsq]=6 [Sq]=45 [Sq-mtp]=45 [Rq]=45 [B2q]=25)
for arm in "$@"; do
    say "arm ${arm}"
    if ! disk_ok "${NEED[${arm}]:-0}"; then
        echo "disk: ${NEED[${arm}]:-0} GB would take / above 96%" > "${OUT}/${arm}.not-deliverable"
        say "arm ${arm} not deliverable (disk)"
        continue
    fi
    case "${arm}" in
        X1q|X1q-mtp)
            if [ -z "${dp_ok}" ]; then echo "the plugin could not be held to the free cards" > "${OUT}/${arm}.not-run"; continue; fi
            run_agent "${arm}" "${arm#X1q}" ;;
        Lsq)
            pinned_pod lsq "${free[0]}" "${LCPP_INTEL}" '["/app/llama-server"]' \
                "[\"-m\",\"/models/${LSQ_GGUF}\",\"-ngl\",\"99\",\"--jinja\",\"-c\",\"8192\",\"--host\",\"0.0.0.0\",\"--port\",\"8080\"]" \
                '[]' > "${OUT}/lsq.yaml"
            run_pinned lsq "${OUT}/lsq.yaml" ;;
        Sq|Sq-mtp|Rq)
            n=$(tr 'A-Z' 'a-z' <<< "${arm}")
            case "${arm}" in
                Sq) a=$(scaler_args "${QWEN}" --quantization fp8) ;;
                Sq-mtp) a=$(scaler_args "${QWEN}" --quantization fp8 --speculative-config '{"method":"qwen3_5_mtp","num_speculative_tokens":4}') ;;
                Rq) a=$(scaler_args "${REDHAT}" --speculative-config '{"method":"qwen3_5_mtp","num_speculative_tokens":4}') ;;
            esac
            pinned_pod "${n}" "${free[0]}" "${SCALER}" '["vllm"]' "${a}" \
                '[{"name":"VLLM_WORKER_MULTIPROC_METHOD","value":"spawn"},{"name":"VLLM_ALLOW_LONG_MAX_MODEL_LEN","value":"1"}]' > "${OUT}/${n}.yaml"
            BOOT_S=1500 run_pinned "${n}" "${OUT}/${n}.yaml" ;;
        B2q)
            if [ -z "${dp_ok}" ] || [ "${#free[@]}" -lt 2 ]; then echo "needs two free cards held for the plugin" > "${OUT}/B2q.not-run"; continue; fi
            "${HERE}/b2q.sh" "${OUT}" ;;
        *) say "unknown arm ${arm}" ;;
    esac
    sleep 20
done
