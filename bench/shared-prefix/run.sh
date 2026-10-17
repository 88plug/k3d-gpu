#!/usr/bin/env bash
# H25 live run (EXPERIMENTS.md, "H25 pre-registration"): cross-agent shared
# prefix KV on one OVMS CPU server, arm ON (shipping default) vs arm OFF
# (--enable_prefix_caching=false on the pull container), order ON OFF OFF ON ON
# OFF, a fresh pod and a new prefix seed every round. Self-limits to 20 min:
# no new round starts after 18:30 elapsed, and the cluster is deleted on any
# exit. Run it under the shared live lock:
#
#   flock -w 10800 "$LOCK" bench/shared-prefix/run.sh
#
# Env:  IMAGE   k3d-gpu node image (docker build -t "$IMAGE" .)
#       MODELS  dir holding OpenVINO/Qwen3-4B-int4-ov at b467368d (staged
#               outside the lock; copied into the node, used as a hostPath)
#       OUT     results dir (scratch kubeconfig, logs, round-*.json, score.json)
# Package energy needs root to read RAPL (sudo -n); without it, energy is -.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
: "${IMAGE:?set IMAGE}" "${MODELS:?set MODELS}" "${OUT:?set OUT}"
name=k3dgpu-i-shared-prefix
export KUBECONFIG="${OUT}/kubeconfig" K3D_GPU_CLUSTER="${name}" K3D_GPU_IMAGE="${IMAGE}"
export K3D_GPU_SHARE="${K3D_GPU_SHARE:-${ROOT}/share}" K3D_GPU_AGENT_BACKEND=ovms K3D_GPU_AGENT_DEVICE=CPU
L="${ROOT}/scripts/k3d-gpu"
P="${ROOT}/bench/shared-prefix/probe.py"
NS=k3d-gpu-agent
NODE_MODELS=/var/lib/h25-models
GRAPH=/models/OpenVINO/Qwen3-4B-int4-ov/graph.pbtxt
CAP=1110      # 18:30 in seconds: no new round after this
START=${SECONDS}
ARMS=(ON OFF OFF ON ON OFF)
pf=""

[ -d "${MODELS}/OpenVINO/Qwen3-4B-int4-ov" ] || { echo "MODELS has no OpenVINO/Qwen3-4B-int4-ov"; exit 2; }
mkdir -p "${OUT}"
rapl() { sudo -n cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null || echo -; }
throttles() { cat /sys/devices/system/cpu/cpu*/thermal_throttle/package_throttle_count 2>/dev/null | awk '{s += $1} END {print s + 0}'; }
left() { echo $(( CAP - (SECONDS - START) )); }
cleanup() {
    [ -z "${pf}" ] || kill "${pf}" 2>/dev/null
    k3d cluster delete "${name}" >/dev/null 2>&1
    rm -f "${KUBECONFIG}"
    echo "cluster ${name} deleted at $(( SECONDS - START ))s"
}
trap cleanup EXIT

# Wait for the f-wave clusters to be gone (the lock serialises the rest).
while k3d cluster list -o json 2>/dev/null | grep -q '"name": *"k3dgpu-f-'; do
    [ "$(left)" -gt 900 ] || { echo "k3dgpu-f-* still present; aborting with no round run"; exit 3; }
    sleep 10
done

k3d cluster delete "${name}" >/dev/null 2>&1 || true
"${L}" up > "${OUT}/up.log" 2>&1 || { echo "up failed (see ${OUT}/up.log)"; exit 1; }
node="k3d-${name}-server-0"
docker exec "${node}" mkdir -p "${NODE_MODELS}"
docker cp "${MODELS}/." "${node}:${NODE_MODELS}/"
docker exec "${node}" chown -R 5000:5000 "${NODE_MODELS}"
ovms_image=$(sed -n 's/^AGENT_OVMS_IMAGE="\(.*\)"$/\1/p' "${L}")
k3d image import -c "${name}" "${ovms_image}" > "${OUT}/import.log" 2>&1 || echo "image import failed; the pod pulls it (see ${OUT}/import.log)"

# The agent manifest with the model PVC swapped for the staged hostPath.
"${L}" agent manifest 2> "${OUT}/manifest.err" | python3 -c '
import sys
docs = sys.stdin.read().split("\n---\n")
docs = [d for d in docs if "kind: PersistentVolumeClaim" not in d]
out = "\n---\n".join(docs).replace(
    "persistentVolumeClaim:\n            claimName: ovms-models",
    "hostPath:\n            path: '"${NODE_MODELS}"'\n            type: Directory")
sys.stdout.write(out)' > "${OUT}/on.yaml"
grep -q "path: ${NODE_MODELS}" "${OUT}/on.yaml" || { echo "manifest rewrite failed"; exit 1; }
# OFF: one extra pull arg, after --task.
sed 's/^\(\( *\)- --task=text_generation\)$/\1\n\2- --enable_prefix_caching=false/' "${OUT}/on.yaml" > "${OUT}/off.yaml"
[ "$(diff "${OUT}/on.yaml" "${OUT}/off.yaml" | grep -c '^>')" = 1 ] || { echo "OFF manifest differs by more than one line"; exit 1; }

seed=2500
for i in "${!ARMS[@]}"; do
    arm=${ARMS[$i]}; seed=$((seed + 1)); r=$((i + 1))
    [ "$(left)" -gt 120 ] || { echo "round ${r} (${arm}) not run: time cap"; continue; }
    [ "${arm}" = ON ] && f=on.yaml || f=off.yaml
    kubectl apply -f "${OUT}/${f}" > /dev/null
    kubectl -n "${NS}" rollout restart deployment/ovms > /dev/null
    kubectl -n "${NS}" rollout status deployment/ovms --timeout="$(left)s" > /dev/null \
        || { echo "round ${r} (${arm}) void: pod not ready"; continue; }
    want=true; [ "${arm}" = ON ] || want=false
    got=$(kubectl -n "${NS}" exec deploy/ovms -c ovms -- grep -o 'enable_prefix_caching: *[a-z]*' "${GRAPH}" | awk '{print $2}')
    echo "round=${r} arm=${arm} seed=${seed} graph_prefix_caching=${got:-missing} load1=$(cut -d' ' -f1 /proc/loadavg) swap_used_mib=$(free -m | awk '/^Swap/ {print $3}') containers=$(docker ps -q | wc -l)"
    e0=$(rapl); th0=$(throttles)
    [ -z "${pf}" ] || kill "${pf}" 2>/dev/null
    kubectl -n "${NS}" port-forward svc/ovms 18025:8000 > /dev/null 2>&1 & pf=$!
    sleep 2
    timeout "$(left)" python3 "${P}" round --seed "${seed}" --arm "${arm}" \
        --url http://127.0.0.1:18025/v3/chat/completions \
        --mem-cmd "kubectl -n ${NS} exec deploy/ovms -c ovms -- sh -c 'cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.swap.current'" \
        --out "${OUT}/round-${r}.json"
    e1=$(rapl); th1=$(throttles)
    python3 - "${OUT}/round-${r}.json" "${got}" "${want}" "${e0}" "${e1}" "${th0}" "${th1}" <<'EOF'
import json, sys
p, got, want, e0, e1, t0, t1 = sys.argv[1:]
try:
    r = json.load(open(p))
except OSError:
    sys.exit(0)
r["graph_ok"] = got == want
r["energy_uj"] = [e0, e1]
r["throttle_delta"] = int(t1) - int(t0)
json.dump(r, open(p, "w"))
EOF
    kubectl -n "${NS}" logs deploy/ovms -c ovms 2>/dev/null | grep -i 'cache usage' | tail -3 > "${OUT}/cache-usage-${r}.log"
done

ls "${OUT}"/round-*.json > /dev/null 2>&1 && python3 "${P}" score "${OUT}"/round-*.json | tee "${OUT}/score.json"
echo "elapsed=$(( SECONDS - START ))s"
