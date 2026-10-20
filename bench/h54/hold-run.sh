#!/usr/bin/env bash
# hold-run.sh ARM...: inside hold.sh's lock and timeout: create the hold's cluster, run each arm
# (arm.sh) until 2370 s after the cluster is up. CLUSTER, KUBECONFIG, REPO, G, EAT, SERVING come
# from hold.sh. The cluster is deleted by hold.sh.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
t0=$(date +%s)
# Amendment C1's eviction thresholds; AMX only (no GPU device plugin: H54a uses no card); EAT's
# inference-node labels for B1g's affinity; the node image k3d-gpu names, pinned to its digest.
# The k3d shim in ${G}/bin adds one volume, ${G}/models at /var/apb-models in the node.
K3D_GPU_VENDORS=amx K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true \
    K3D_GPU_IMAGE=cryptoandcoffee/k3d-gpu:0.1.63@sha256:b6fc46f60a47948eb1c4e6c5e80059c7f581427429c35a2627c58fc2307e4b76 \
    K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%" \
    K3D_GPU_CLUSTER=${CLUSTER:?} timeout 300 "${REPO:?}/scripts/k3d-gpu" up > "$(dirname "${KUBECONFIG}")/up.log" 2>&1 \
    || { echo "up failed"; tail -20 "$(dirname "${KUBECONFIG}")/up.log"; exit 1; }
kubectl get node -o json > "$(dirname "${KUBECONFIG}")/node.json"
docker exec "k3d-${CLUSTER}-server-0" ls /var/apb-models > /dev/null || { echo "models volume missing in the node"; exit 1; }
export DEADLINE=$(( t0 + 2370 ))
for arm in "$@"; do
    bash "${HERE}/arm.sh" "${arm}" "$(dirname "${KUBECONFIG}")/${arm}"
done
