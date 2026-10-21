#!/usr/bin/env bash
# h52w.sh OUT: H52w detection probe on wildct (EXPERIMENTS.md "Wildct pre-registration").
# The installed k3d-gpu release (~/k3dgpu-bench-wildct/k3d-gpu, from its .deb) with the box
# owner's GPU opt-out (K3D_GPU_VENDORS=auto,-nvidia,-intel) and rule W3's kubelet thresholds,
# nothing else: inventory, `detect` as found (sysfs reads only), doctor, up, the node object,
# a scan of every pod for GPU device mounts and GPU resources, `k3d-gpu test`, down, inventory
# again. Opens no GPU device node.
set -uo pipefail
OUT=$1
W=${HOME}/k3dgpu-bench-wildct
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
K=${W}/k3d-gpu/usr/bin/k3d-gpu
mkdir -p "${OUT}"
export PATH=${W}/bin:${PATH} KUBECONFIG=${OUT}/kubeconfig XDG_STATE_HOME=${OUT}/state
export K3D_GPU_SHARE=${W}/k3d-gpu/usr/share/k3d-gpu K3D_GPU_CLUSTER=k3dgpu-w-h52
bash "${HERE}/inventory.sh" > "${OUT}/inventory-1.txt" 2>&1
"${K}" version > "${OUT}/version.txt" 2>&1
# As found: what auto detection sees, before the opt-out (sysfs and /proc reads only).
"${K}" detect > "${OUT}/detect-auto.txt" 2>&1; echo "detect(auto) rc=$?" >> "${OUT}/rc.txt"
export K3D_GPU_VENDORS=auto,-nvidia,-intel
export K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<2Gi,imagefs.available<2Gi,nodefs.inodesFree<1%"
env | grep -E '^K3D_GPU_' | sort > "${OUT}/env.txt"
"${K}" detect > "${OUT}/detect.txt" 2>&1; echo "detect rc=$?" >> "${OUT}/rc.txt"
"${K}" doctor > "${OUT}/doctor.txt" 2>&1; echo "doctor rc=$?" >> "${OUT}/rc.txt"
t0=$(date +%s)
timeout 600 "${K}" up > "${OUT}/up.txt" 2>&1; echo "up rc=$? in $(( $(date +%s) - t0 )) s" >> "${OUT}/rc.txt"
docker inspect --format '{{.Image}} {{.Config.Image}} privileged={{.HostConfig.Privileged}} devices={{.HostConfig.Devices}} runtime={{.HostConfig.Runtime}} gpus={{.HostConfig.DeviceRequests}}' \
    "k3d-${K3D_GPU_CLUSTER}-server-0" > "${OUT}/node-container.txt" 2>&1
for _ in $(seq 60); do
    kubectl get pods -A --no-headers 2>/dev/null | awk '$4 != "Running" && $4 != "Completed" {bad++} END {exit bad > 0}' && break
    sleep 5
done
kubectl get pods -A -o wide > "${OUT}/pods.txt" 2>&1
kubectl get ds -A > "${OUT}/daemonsets.txt" 2>&1
kubectl get addons.k3s.cattle.io -A > "${OUT}/addons.txt" 2>&1
kubectl get node -o json > "${OUT}/node.json"
kubectl get node -o jsonpath='{.items[0].status.allocatable}' > "${OUT}/allocatable.json"
# Every pod's hostPath volumes and resource requests: no GPU device directory, no GPU resource.
kubectl get pods -A -o json | python3 -c '
import json, sys
bad = []
for p in json.load(sys.stdin)["items"]:
    n = p["metadata"]["namespace"] + "/" + p["metadata"]["name"]
    for v in p["spec"].get("volumes", []):
        hp = (v.get("hostPath") or {}).get("path", "")
        print(n, "hostPath", hp)
        if hp.startswith(("/dev/dri", "/dev/nvidia")) or hp == "/dev":
            bad.append(f"{n} mounts {hp}")
    for c in p["spec"].get("containers", []) + p["spec"].get("initContainers", []):
        res = {**(c.get("resources", {}).get("limits") or {}), **(c.get("resources", {}).get("requests") or {})}
        for k in res:
            if k.startswith(("nvidia.com/", "gpu.intel.com/")):
                bad.append(f"{n} asks {k}")
print("GPU-SCAN", "clean" if not bad else "DIRTY: " + "; ".join(bad))' > "${OUT}/gpu-scan.txt" 2>&1
tail -1 "${OUT}/gpu-scan.txt" >> "${OUT}/rc.txt"
"${K}" test > "${OUT}/test.txt" 2>&1; echo "test rc=$?" >> "${OUT}/rc.txt"
"${K}" down > "${OUT}/down.txt" 2>&1; echo "down rc=$?" >> "${OUT}/rc.txt"
bash "${HERE}/inventory.sh" > "${OUT}/inventory-2.txt" 2>&1
if diff <(tail -n +2 "${OUT}/inventory-1.txt") <(tail -n +2 "${OUT}/inventory-2.txt") > "${OUT}/inventory.diff"; then
    echo "inventory unchanged" >> "${OUT}/rc.txt"
else
    echo "inventory CHANGED" >> "${OUT}/rc.txt"
fi
cat "${OUT}/rc.txt"
