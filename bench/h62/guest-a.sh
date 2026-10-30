#!/usr/bin/env bash
# guest-a.sh [deploy]: VM a's setup for H62's A1 (EXPERIMENTS.md "H62 pre-registration"), run in
# the guest by setup.sh as user ubuntu, under its 45 min ceiling. A1 is H50's A1 unchanged:
#   1. Docker CE from Docker's apt repository at the pinned version (its key's fingerprint
#      checked first) and python3-yaml for bench/apb/stage.py; then this script runs again in a
#      shell with the docker group, as `deploy`
#   2. A1's IR from the host's read-only share (virtfs tag h62share), checked against its
#      manifest.json
#   3. `k3d-gpu up` at the deploy commit (~/h62/k3d-gpu, copied by setup.sh), with H50's node image
#      and crescent amendment C1's kubelet eviction thresholds, cluster k3dgpu-e-h62
#   4. `k3d-gpu agent manifest` with live.sh's gemma4 settings for A1 (CPU, the snapped QAT IR,
#      tool parser gemma4, no reasoning parser, no draft, no autoquant), staged and applied as
#      live.sh does, and rolled out
#   5. records: amendment A4's `k3d-gpu plan` line, versions, the node, the pod
# k3d and kubectl are setup.sh's checked binaries in ~/h62/bin. Records: ~/h62/records.
set -euo pipefail
G=${HOME}/h62
P() { python3 "${G}/h62.py" "$@"; }
R=${G}/records
REPO=${G}/k3d-gpu
mkdir -p "${R}"
export PATH=${G}/bin:${PATH} KUBECONFIG=${G}/kubeconfig XDG_STATE_HOME=${G}/state K3D_GPU_CLUSTER=k3dgpu-e-h62
step() { echo "== $1 $(date -u +%FT%TZ)" | tee -a "${R}/steps.txt"; }
if [ "${1:-}" != deploy ]; then
    step docker
    # ${PATH} expands when a command in the guest sources ~/h62/env (bench/h62/arm.sh).
    # shellcheck disable=SC2016
    printf 'export PATH=%s/bin:${PATH} KUBECONFIG=%s/kubeconfig\n' "${G}" "${G}" > "${G}/env"
    curl -fsSLo "${G}/docker.asc" https://download.docker.com/linux/ubuntu/gpg
    gpg --show-keys --with-colons "${G}/docker.asc" | awk -F: '/^fpr/ {print $10}' | grep -x "$(P pin docker_gpg_fpr)" > /dev/null \
        || { echo "Docker's apt key is not $(P pin docker_gpg_fpr)"; exit 1; }
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo install -m 0644 "${G}/docker.asc" /etc/apt/keyrings/docker.asc
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -q 2>&1 | tee "${R}/apt-update.log" > /dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
        docker-ce="$(P pin docker_ce)" docker-ce-cli="$(P pin docker_ce)" containerd.io="$(P pin containerd)" \
        python3-yaml="$(P pin python3_yaml)" 2>&1 | tee "${R}/apt-install.log" > /dev/null
    sudo usermod -aG docker "$(id -un)"
    exec sg docker -c "bash ${G}/guest-a.sh deploy"
fi
step ir
sudo mkdir -p /mnt/h62-ir
mountpoint -q /mnt/h62-ir || sudo mount -t 9p -o trans=virtio,version=9p2000.L,ro h62share /mnt/h62-ir
P ir-verify /mnt/h62-ir > "${R}/ir-verify.txt"
step up
K3D_GPU_IMAGE="$(P pin node_image)" \
    K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%" \
    timeout 900 "${REPO}/scripts/k3d-gpu" up > "${R}/up.log" 2>&1
step manifest
K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_MODEL=k3dgpu/gemma-4-26B-A4B-it-qat-int4-ov K3D_GPU_AGENT_MODEL_NAME=gemma-4-26b-a4b-it \
    K3D_GPU_AGENT_DRAFT=off K3D_GPU_AGENT_TOOL_PARSER=gemma4 K3D_GPU_AGENT_REASONING_PARSER='' K3D_GPU_AUTOQUANT=off \
    "${REPO}/scripts/k3d-gpu" agent manifest > "${R}/agent.yaml" 2> "${R}/agent.err"
python3 "${REPO}/bench/apb/stage.py" ovms "${R}/agent.yaml" "${R}/deploy-a.yaml"
step stage
# As live.sh's stage_path: the IR copied into the node at the path the staged manifest mounts.
node=$(docker ps --filter "label=k3d.cluster=${K3D_GPU_CLUSTER}" --filter label=k3d.role=server --format '{{.Names}}' | head -1)
[ -n "${node}" ] || { echo "no k3d server node"; exit 1; }
t=/var/apb-models/k3dgpu/gemma-4-26B-A4B-it-qat-int4-ov
docker exec "${node}" mkdir -p "${t}" /var/apb-models/.ov_cache
docker cp -L /mnt/h62-ir/. "${node}:${t}/"
docker exec "${node}" rm -rf "${t}/.cache" "${t}/.git"
docker exec "${node}" chown -R 5000:5000 "${t}"
docker exec "${node}" chown 5000:5000 /var/apb-models/.ov_cache
docker exec "${node}" chmod 1777 /var/apb-models
step apply
kubectl apply -f "${R}/deploy-a.yaml" > "${R}/apply.log"
kubectl -n k3d-gpu-agent rollout status deploy/ovms --timeout=1500s > "${R}/rollout.log" 2>&1
step records
{ timeout 600 "${REPO}/scripts/k3d-gpu" plan --device cpu --model google/gemma-4-26B-A4B-it --explain; echo "rc=$?"; } \
    > "${R}/plan.txt" 2>&1 || true
{ docker version; k3d version; kubectl version --client; uname -r; } > "${R}/versions.txt" 2>&1
kubectl get nodes -o json > "${R}/nodes.json"
kubectl -n k3d-gpu-agent get pods -o json > "${R}/pods.json"
docker exec "${node}" sh -c "cd ${t} && sha256sum ./*.xml ./*.bin" > "${R}/staged-ir.txt"
step finished
