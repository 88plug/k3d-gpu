#!/usr/bin/env bash
# hold-run.sh R ARM...: inside hold.sh's lock and timeout: create the hold's cluster with the host
# profile's vendors, node image and kubelet thresholds, start H57d's P-core load sampler (the load
# gate reads it), run each arm (arm.sh) until the profile's arms_end_s after the hold started.
# CLUSTER, KUBECONFIG, REPO, G, MODE, H63_PROFILE, H63_IMAGE come from hold.sh; hold.sh deletes
# the cluster.
set -uo pipefail
R=$1
shift
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
t0=$(date +%s)
pfield() { python3 "${HERE}/run.py" profile "${H63_PROFILE:?}" | sed -n "s/^$1=//p"; }
# The k3d shim in ${G}/bin adds the campaign's model directory at /var/apb-models in the node.
K3D_GPU_VENDORS=$(pfield vendors) K3D_GPU_IMAGE=${H63_IMAGE:?} K3D_GPU_K3S_ARGS=$(pfield k3s_args) \
    K3D_GPU_CLUSTER=${CLUSTER:?} timeout 300 "${REPO:?}/scripts/k3d-gpu" up > "${R}/up.log" 2>&1 \
    || { echo "up failed"; tail -20 "${R}/up.log"; exit 1; }
kubectl get node -o json > "${R}/node.json"
docker exec "k3d-${CLUSTER}-server-0" ls /var/apb-models > /dev/null || { echo "models volume missing in the node"; exit 1; }
python3 "${REPO}/bench/h57d/h57d.py" sample "${R}/load.jsonl" "k3d-${CLUSTER}-server-0" &
sp=$!
trap 'kill "${sp}" 2>/dev/null' EXIT
export DEADLINE=$(( t0 + $(pfield arms_end_s) ))
for arm in "$@"; do
    bash "${HERE}/arm.sh" "${arm}" "${R}/${arm}"
done
