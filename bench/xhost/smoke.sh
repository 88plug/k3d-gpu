#!/usr/bin/env bash
# smoke.sh MODELS: setup check for the cross-host rows, run under bench/xhost/hold.sh, before
# any measurement. Sends no inference request. One cluster k3dgpu-x-smoke with
# K3D_GPU_VENDORS=none, the node container pinned to the CPUs outside load.py's load set,
# the agent CPU manifest with the staged models as a hostPath (as bench/shared-prefix/run.sh
# does), then reads back: node cpuset, pod Cpus_allowed_list and CPU request, graph.pbtxt,
# the OVMS device/precision log lines and readiness. Deletes the cluster on exit.
set -uo pipefail
M=${1:?usage: smoke.sh MODELS_DIR}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
name=k3dgpu-x-smoke
OUT=$(mktemp -d)
export KUBECONFIG=${OUT}/kubeconfig K3D_GPU_CLUSTER=${name} K3D_GPU_VENDORS=none
export K3D_GPU_AGENT_BACKEND=ovms K3D_GPU_AGENT_DEVICE=CPU
L=${ROOT}/scripts/k3d-gpu
trap 'k3d cluster delete "${name}" >/dev/null 2>&1; echo "cluster ${name} deleted"; rm -rf "${OUT}"' EXIT
load=$(python3 "${ROOT}/bench/load/load.py" cpus)
node_cpus=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import load
a = set(load.parse_cpulist(open("/sys/devices/system/cpu/online").read())) - set(load.parse_cpulist(sys.argv[2]))
print(load.format_cpulist(a))' "${ROOT}/bench/load" "${load}")
echo "load cpus ${load}; node cpus ${node_cpus}"
t0=${SECONDS}
"${L}" up > "${OUT}/up.log" 2>&1 || { tail -30 "${OUT}/up.log"; exit 1; }
echo "up: $((SECONDS - t0)) s"; grep -E 'Accelerators|node image|k3s' "${OUT}/up.log" | head -5
node=k3d-${name}-server-0
docker update --cpuset-cpus "${node_cpus}" "${node}" >/dev/null
echo "node cpuset: $(docker inspect -f '{{.HostConfig.CpusetCpus}}' "${node}")"
kubectl get node -o jsonpath='{.items[0].status.allocatable.cpu} {.items[0].metadata.labels}{"\n"}' | cut -c1-400
docker exec "${node}" mkdir -p /var/lib/xh-models
docker cp "${M}/." "${node}:/var/lib/xh-models/"
docker exec "${node}" chown -R 5000:5000 /var/lib/xh-models
"${L}" agent manifest 2> "${OUT}/manifest.err" | python3 -c '
import sys
docs = [d for d in sys.stdin.read().split("\n---\n") if "kind: PersistentVolumeClaim" not in d]
sys.stdout.write("\n---\n".join(docs).replace("persistentVolumeClaim:\n            claimName: ovms-models",
    "hostPath:\n            path: /var/lib/xh-models\n            type: Directory"))' > "${OUT}/on.yaml"
cat "${OUT}/manifest.err"
grep -nE 'cpu:|draft|target_device' "${OUT}/on.yaml"
kubectl apply -f "${OUT}/on.yaml" >/dev/null
kubectl -n k3d-gpu-agent rollout status deployment/ovms --timeout=600s || { kubectl -n k3d-gpu-agent describe pod | tail -30; exit 1; }
echo "pod ready: $((SECONDS - t0)) s"
kubectl -n k3d-gpu-agent exec deploy/ovms -c ovms -- grep Cpus_allowed_list /proc/1/status
kubectl -n k3d-gpu-agent exec deploy/ovms -c ovms -- cat /models/OpenVINO/Qwen3-4B-int4-ov/graph.pbtxt | grep -vE '^\s*$' | head -40
kubectl -n k3d-gpu-agent logs deploy/ovms -c ovms 2>/dev/null | grep -iE 'PRECISION|NUM_STREAMS|INFERENCE_NUM_THREADS|device|AMX|draft' | head -20
