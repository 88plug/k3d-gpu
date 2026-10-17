#!/usr/bin/env bash
# H25 live run (EXPERIMENTS.md, "H25 pre-registration", amendment A2):
# cross-agent shared prefix KV on one OVMS CPU server, arm ON (shipping default)
# vs arm OFF (--enable_prefix_caching=false on the pull container), interleaved
# ON OFF ON OFF ON OFF under the same controlled background load
# (bench/load/load.py on the E-cores), a fresh pod and a new prefix seed every
# round. Per level: 20 min cap (no new round after 18:30 of the level; the first
# level includes cluster setup). The cluster and the load containers are
# removed on any exit. Run it under the shared live lock:
#
#   flock -w 14400 "$LOCK" bench/shared-prefix/run.sh
#
# Env:  IMAGE   k3d-gpu node image (docker build -t "$IMAGE" .)
#       MODELS  dir holding OpenVINO/Qwen3-4B-int4-ov at b467368d (staged
#               outside the lock; copied into the node, used as a hostPath)
#       OUT     results dir (scratch kubeconfig, logs, LEVEL/round-*.json,
#               LEVEL/load.jsonl, LEVEL/score.json)
#       LEVELS  load levels in order (default "typical heavy"; "none" = no load)
# Package energy needs root to read RAPL (sudo -A with SUDO_ASKPASS, else
# sudo -n); without it, energy and package W are null.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
: "${IMAGE:?set IMAGE}" "${MODELS:?set MODELS}" "${OUT:?set OUT}"
name=k3dgpu-i-shared-prefix
export KUBECONFIG="${OUT}/kubeconfig" K3D_GPU_CLUSTER="${name}" K3D_GPU_IMAGE="${IMAGE}"
export K3D_GPU_SHARE="${K3D_GPU_SHARE:-${ROOT}/share}" K3D_GPU_AGENT_BACKEND=ovms K3D_GPU_AGENT_DEVICE=CPU
L="${ROOT}/scripts/k3d-gpu"
P="${ROOT}/bench/shared-prefix/probe.py"
LOAD=(python3 "${ROOT}/bench/load/load.py")
LEVELS=${LEVELS:-typical heavy}
NS=k3d-gpu-agent
NODE_MODELS=/var/lib/h25-models
GRAPH=/models/OpenVINO/Qwen3-4B-int4-ov/graph.pbtxt
CAP=1110      # 18:30 in seconds: no new round after this
START=${SECONDS}
read -ra ARMS <<< "$("${LOAD[@]}" order ON OFF --reps 3)"
pf=""

[ -d "${MODELS}/OpenVINO/Qwen3-4B-int4-ov" ] || { echo "MODELS has no OpenVINO/Qwen3-4B-int4-ov"; exit 2; }
mkdir -p "${OUT}"
sudo_() { if [ -n "${SUDO_ASKPASS:-}" ]; then sudo -A "$@"; else sudo -n "$@"; fi; }
rapl() { sudo_ cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null || echo -; }
throttles() { cat /sys/devices/system/cpu/cpu*/thermal_throttle/package_throttle_count 2>/dev/null | awk '{s += $1} END {print s + 0}'; }
left() { echo $(( CAP - (SECONDS - START) )); }
cleanup() {
    [ -z "${pf}" ] || kill "${pf}" 2>/dev/null
    k3d cluster delete "${name}" >/dev/null 2>&1
    "${LOAD[@]}" stop >/dev/null 2>&1
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
first=${LEVELS%% *}
echo "load ${first}: $("${LOAD[@]}" start --level "${first}" --ttl 1500)"
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

base=2510
for level in ${LEVELS}; do
if [ "${level}" != "${first}" ]; then
    START=${SECONDS}
    echo "load ${level}: $("${LOAD[@]}" start --level "${level}" --ttl 1500)"
    sleep 30
fi
D="${OUT}/${level}"; mkdir -p "${D}"
seed=${base}; base=$((base + 10))
for i in "${!ARMS[@]}"; do
    arm=${ARMS[$i]}; seed=$((seed + 1)); r=$((i + 1))
    [ "$(left)" -gt 120 ] || { echo "${level} round ${r} (${arm}) not run: time cap"; continue; }
    [ "${arm}" = ON ] && f=on.yaml || f=off.yaml
    kubectl apply -f "${OUT}/${f}" > /dev/null
    kubectl -n "${NS}" rollout restart deployment/ovms > /dev/null
    kubectl -n "${NS}" rollout status deployment/ovms --timeout="$(left)s" > /dev/null \
        || { echo "${level} round ${r} (${arm}) void: pod not ready"; continue; }
    want=true; [ "${arm}" = ON ] || want=false
    got=$(kubectl -n "${NS}" exec deploy/ovms -c ovms -- grep -o 'enable_prefix_caching: *[a-z]*' "${GRAPH}" | awk '{print $2}')
    "${LOAD[@]}" sample --seconds 10 --windows 2 --tag "${arm}" --level "${level}" --out "${D}/load.jsonl"
    echo "level=${level} round=${r} arm=${arm} seed=${seed} graph_prefix_caching=${got:-missing} load1=$(cut -d' ' -f1 /proc/loadavg) swap_used_mib=$(free -m | awk '/^Swap/ {print $3}') containers=$(docker ps -q | wc -l)"
    e0=$(rapl); th0=$(throttles)
    [ -z "${pf}" ] || kill "${pf}" 2>/dev/null
    kubectl -n "${NS}" port-forward svc/ovms 18025:8000 > /dev/null 2>&1 & pf=$!
    sleep 2
    timeout "$(left)" python3 "${P}" round --seed "${seed}" --arm "${arm}" \
        --url http://127.0.0.1:18025/v3/chat/completions \
        --mem-cmd "kubectl -n ${NS} exec deploy/ovms -c ovms -- sh -c 'cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.swap.current'" \
        --out "${D}/round-${r}.json"
    e1=$(rapl); th1=$(throttles)
    python3 - "${D}/round-${r}.json" "${got}" "${want}" "${e0}" "${e1}" "${th0}" "${th1}" <<'EOF'
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
    kubectl -n "${NS}" logs deploy/ovms -c ovms 2>/dev/null | grep -i 'cache usage' | tail -3 > "${D}/cache-usage-${r}.log"
done
if ls "${D}"/round-*.json > /dev/null 2>&1; then
    python3 "${P}" score --load "${D}/load.jsonl" "${D}"/round-*.json | tee "${D}/score.json"
fi
done

echo "elapsed=$(( SECONDS - START ))s"
