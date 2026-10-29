#!/usr/bin/env bash
# arm.sh: one PROTOCOL.md arm on a fresh k3d-gpu cluster, measured by `k3d-gpu capacity`,
# inside a fixed wall-clock cap, then the cluster is deleted.
#
#   bench/apb/arm.sh ovms-cpu|ovms-igpu|eat-vllm-cpu|vllm-cuda
#
# The model is not downloaded in the cluster: a `hf download --local-dir` copy is
# copied into the node container and mounted with hostPath (bench/apb/stage.py), so
# bring-up is cluster + image pull + server start. capacity gets what is left of
# APB_CAP after bring-up, at most APB_BUDGET minutes, with --step-cap equal to its
# budget (one rep at N=1 can take longer than capacity's 180 s default).
#
# Environment:
#   APB_MODELS     dir with Qwen3-4B-int4-ov, Qwen3-4B-Instruct-2507 and Qwen3-4B-AWQ
#                  (hf download ... --local-dir; revisions in EXPERIMENTS.md H8 run) (required)
#   APB_EAT_CHART  EAT checkout's core/helm-charts/vllm at 0d3438d (eat-vllm-cpu only)
#   APB_OUT        run directory parent        (default ./apb-runs)
#   APB_CAP        wall-clock cap, s            (default 870)
#   APB_BUDGET     capacity budget cap, min     (default 12)
#   K3D_GPU_CLUSTER  cluster name               (default apb-<arm>)
# capacity's energy columns need its `sudo -n cat` RAPL reader to work for its own
# process (see capacity --help); without it J per turn has no CPU term.
set -uo pipefail

ARM=${1:?usage: arm.sh ovms-cpu|ovms-igpu|eat-vllm-cpu|vllm-cuda}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
K=${REPO}/scripts/k3d-gpu
M=${APB_MODELS:?set APB_MODELS to the dir of local model copies}
CAP=${APB_CAP:-870}
export K3D_GPU_CLUSTER=${K3D_GPU_CLUSTER:-apb-${ARM}}
OUT=${APB_OUT:-${PWD}/apb-runs}/${ARM}-$(date +%Y%m%d-%H%M%S)
mkdir -p "${OUT}"
export KUBECONFIG=${OUT}/kubeconfig XDG_STATE_HOME=${OUT}/state
t0=$(date +%s)
el() { echo $(( $(date +%s) - t0 )); }
stamp() { echo "== $1 $(date +%T) (+$(el)s)"; }
cleanup() { stamp cleanup; k3d cluster delete "${K3D_GPU_CLUSTER}" >/dev/null 2>&1; stamp deleted; }
trap cleanup EXIT

nodes() { docker ps --filter "label=k3d.cluster=${K3D_GPU_CLUSTER}" --filter "label=k3d.role=server" --format '{{.Names}}'; }
stage() {  # stage <local dir> <path under /var/apb-models> <uid>
    local n
    for n in $(nodes); do
        docker exec "${n}" mkdir -p "/var/apb-models/$2"
        docker cp "${M}/$1/." "${n}:/var/apb-models/$2/"
        docker exec "${n}" rm -rf "/var/apb-models/$2/.cache"
        docker exec "${n}" chown -R "$3:$3" /var/apb-models
    done
}

stamp up
[ "${ARM}" = eat-vllm-cpu ] && export K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true
timeout 240 "${K}" up > "${OUT}/up.log" 2>&1 || { echo "up failed"; tail -20 "${OUT}/up.log"; exit 1; }
stamp deploy
deadline=$(( t0 + CAP - 180 ))  # bring-up must leave capacity 2 min plus 1 min of cleanup
case ${ARM} in
    ovms-cpu|ovms-igpu)
        [ "${ARM}" = ovms-igpu ] && export K3D_GPU_AGENT_DEVICE=GPU
        "${K}" agent manifest > "${OUT}/agent.yaml" 2> "${OUT}/agent.err" || { cat "${OUT}/agent.err"; exit 1; }
        python3 "${HERE}/stage.py" ovms "${OUT}/agent.yaml" "${OUT}/deploy.yaml"
        stage Qwen3-4B-int4-ov OpenVINO/Qwen3-4B-int4-ov 5000
        NS=k3d-gpu-agent DEP=ovms EP=k3d-gpu-agent/ovms ;;
    eat-vllm-cpu)
        chart=${APB_EAT_CHART:?set APB_EAT_CHART to EAT core/helm-charts/vllm}
        python3 "${HERE}/stage.py" values "${chart}/xeon-values.yaml" "${REPO}/integrations/eat/vllm-values.yaml" 6 "${OUT}/values.yaml"
        # EAT's catalog entry cpu-qwen3-4b (core/playbooks/deploy-inference-models.yml), ingress off.
        # The image is the one H8 ran as arm A3, pinned here so that a later tag in
        # integrations/eat/vllm-values.yaml does not change what this arm reproduces.
        helm template vllm-qwen-3-4b-cpu "${chart}" --values "${OUT}/values.yaml" \
            --set image.tag="v0.24.0@sha256:b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1" \
            --set LLM_MODEL_ID=Qwen/Qwen3-4B-Instruct-2507 --set global.monitoring=false \
            --set svcmonitor.enabled=false --set ingress.enabled=false --set route.enabled=false > "${OUT}/eat.yaml"
        python3 "${HERE}/stage.py" vllm "${OUT}/eat.yaml" "${OUT}/deploy.yaml" Qwen3-4B-Instruct-2507 32768
        stage Qwen3-4B-Instruct-2507 Qwen3-4B-Instruct-2507 1001
        NS=default DEP=vllm-qwen-3-4b-cpu EP=default/vllm-qwen-3-4b-cpu-service ;;
    vllm-cuda)
        gpu=$(nvidia-smi --query-gpu=name,uuid --format=csv,noheader | awk -F', ' '/RTX 4000/ {print $2}')
        # vLLM CUDA v0.31.0 (agent.lock vllm_cuda), the image H8 ran as arm A4, kept so H8 reproduces.
        # No pending row runs this arm; a new CUDA arm pins vLLM's newest release.
        image=vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b
        sed -e "s|@VLLM_IMAGE@|${image}|" -e "s|@GPU_UUID@|${gpu}|" "${HERE}/vllm-cuda.yaml" > "${OUT}/cuda.yaml"
        python3 "${HERE}/stage.py" cuda "${OUT}/cuda.yaml" "${OUT}/deploy.yaml" Qwen3-4B-AWQ
        stage Qwen3-4B-AWQ Qwen3-4B-AWQ 0
        NS=apb-vllm DEP=vllm EP=apb-vllm/vllm ;;
    *) echo "unknown arm ${ARM}"; exit 2 ;;
esac
kubectl apply -f "${OUT}/deploy.yaml"
stamp wait-ready
left=$(( deadline - $(date +%s) ))
if [ "${left}" -le 0 ] || ! kubectl -n "${NS}" rollout status "deploy/${DEP}" --timeout="${left}s"; then
    echo "not ready within the cap"
    kubectl -n "${NS}" describe pods | tail -30
    kubectl -n "${NS}" logs "deploy/${DEP}" --all-containers --previous > "${OUT}/server-previous.log" 2>&1
    kubectl -n "${NS}" logs "deploy/${DEP}" --all-containers > "${OUT}/server.log" 2>&1
    grep -E 'Error:|Exception:' "${OUT}/server-previous.log" "${OUT}/server.log" | cut -c1-400 | head -10
    exit 3
fi
stamp ready
kubectl -n "${NS}" get pods -o jsonpath='{range .items[*]}{.spec.containers[0].image}{" "}{.status.containerStatuses[0].imageID}{"\n"}{end}' > "${OUT}/image.txt"
docker inspect --format '{{.Config.Image}} {{.Image}}' "$(nodes | head -1)" > "${OUT}/node-image.txt"
git -C "${REPO}" rev-parse HEAD > "${OUT}/commit.txt"
"${HERE}/run.sh" facts "${OUT}" >/dev/null 2>&1
left=$(( CAP - $(el) - 60 ))
bud=$(( left < ${APB_BUDGET:-12} * 60 ? left : ${APB_BUDGET:-12} * 60 ))
bmin=$(python3 -c "print(round(${bud} / 60, 2))")
stamp "capacity --budget ${bmin}"
timeout -s INT -k 20 $(( bud + 30 )) "${K}" capacity --budget "${bmin}" --step-cap "${bud}" --slo 10 --force \
    --fresh --endpoint "${EP}" --state-dir "${OUT}/capstate" --out "${OUT}/cap"
echo "capacity rc=$?"
kubectl -n "${NS}" get pods -o jsonpath='{range .items[*]}{.metadata.name}{" restarts="}{.status.containerStatuses[0].restartCount}{" last="}{.status.containerStatuses[0].lastState}{"\n"}{end}'
kubectl -n "${NS}" logs "deploy/${DEP}" --all-containers --previous > "${OUT}/server-previous.log" 2>&1
kubectl -n "${NS}" logs "deploy/${DEP}" --all-containers --tail=200 > "${OUT}/server.log" 2>&1
stamp finished
