#!/usr/bin/env bash
# h52.sh OUT [CLUSTER]: H52 / H52b detection probe (EXPERIMENTS.md "H50-H52 pre-registration").
# doctor, a plain `k3d-gpu up` (no k3d-gpu variable but the cluster name), the node object,
# `k3d-gpu test`, one enumeration pod per Intel GPU unit, then `k3d-gpu down`.
set -uo pipefail
OUT=$1 CL=${2:-k3dgpu-h52}
B=${HOME}/k3dgpu-bench
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
K=${HERE}/../../../scripts/k3d-gpu
ENUM_IMAGE=vllm/vllm-openai-xpu:v0.28.0@sha256:4756b66a077627133cee653b551f6f5eaa1b9a981b5eea13edd33fcd3b0d3ca3
mkdir -p "${OUT}"
export PATH=${B}/bin:${PATH} KUBECONFIG=${OUT}/kubeconfig XDG_STATE_HOME=${OUT}/state K3D_GPU_CLUSTER=${CL}
env | grep -E '^K3D_GPU_' > "${OUT}/env.txt"
git -C "${HERE}" rev-parse HEAD > "${OUT}/commit.txt"
bash "${HERE}/inventory.sh" > "${OUT}/inventory.txt" 2>&1
"${K}" doctor > "${OUT}/doctor.txt" 2>&1; echo "doctor rc=$?" >> "${OUT}/rc.txt"
t0=$(date +%s)
timeout 600 "${K}" up > "${OUT}/up.txt" 2>&1; echo "up rc=$? in $(( $(date +%s) - t0 )) s" >> "${OUT}/rc.txt"
docker inspect --format '{{.Image}} {{.Config.Image}}' "k3d-${CL}-server-0" > "${OUT}/node-image.txt" 2>&1
for _ in $(seq 60); do
    kubectl get pods -A --no-headers 2>/dev/null | awk '$4 != "Running" && $4 != "Completed" {bad++} END {exit bad > 0}' && break
    sleep 5
done
kubectl get pods -A -o wide > "${OUT}/pods.txt" 2>&1
kubectl get ds -A > "${OUT}/daemonsets.txt" 2>&1
kubectl get node -o json > "${OUT}/node.json"
kubectl get node -o jsonpath='{.items[0].status.allocatable}' > "${OUT}/allocatable.json"
"${K}" test > "${OUT}/test.txt" 2>&1; echo "test rc=$?" >> "${OUT}/rc.txt"
n=$(kubectl get node -o jsonpath='{.items[0].status.allocatable.gpu\.intel\.com/xe}')
echo "gpu.intel.com/xe allocatable: ${n:-none}" >> "${OUT}/rc.txt"
for i in $(seq 1 "${n:-0}"); do
    kubectl apply -f - > /dev/null <<EOF
apiVersion: v1
kind: Pod
metadata: {name: gpu-enum-${i}}
spec:
  restartPolicy: Never
  containers:
  - name: enum
    image: ${ENUM_IMAGE}
    command: ["sh", "-c", "ls -l /dev/dri; xpu-smi discovery; echo ENUM-DONE; sleep 600"]
    resources: {limits: {gpu.intel.com/xe: 1}}
EOF
done
# H52 amendment D1 item 2: read each log once its marker is there (the pod then sleeps).
for i in $(seq 1 "${n:-0}"); do
    log=""
    for _ in $(seq 120); do
        log=$(kubectl logs "gpu-enum-${i}" 2>/dev/null)
        grep -q ENUM-DONE <<< "${log}" && break
        sleep 5
    done
    { echo "== gpu-enum-${i} $(kubectl get pod "gpu-enum-${i}" -o jsonpath='{.status.phase}')"; echo "${log}"; } >> "${OUT}/gpu-enum.txt"
done
kubectl get pods -o wide > "${OUT}/enum-pods.txt" 2>&1
kubectl -n default delete pod --all --wait=false > /dev/null 2>&1
"${K}" down > "${OUT}/down.txt" 2>&1; echo "down rc=$?" >> "${OUT}/rc.txt"
cat "${OUT}/rc.txt"
