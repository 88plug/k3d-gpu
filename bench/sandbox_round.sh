#!/usr/bin/env bash
# One H18 round (EXPERIMENTS.md): a fresh cluster, a timed `k3d-gpu sandbox up`,
# then from one sandbox pod and one control pod (same image, namespace
# sbx-control, no NetworkPolicy) a 3 s TCP connect to each target, interleaved
# sandbox/control. Then DNS, HOME and a `pip install --user` from the sandbox.
# Prints one block of key=value lines, then deletes the cluster.
#
# Usage: bench/sandbox_round.sh <run#>
# Env:   IMAGE    node image (docker build -t "$IMAGE" .)
#        OUT      results dir (scratch kubeconfig and logs are written there)
#        TARGETS  "ip:port ..." to probe (default: the host's tailscale0 and the
#                 first private IPv4 of the default route, port 22, then pypi.org:443)
#        PIP_PKG  package that is not in the runtime image (default tomli_w==1.2.0)
#        K3D_GPU_CLUSTER (default k3dgpu-r2-sbx)
# Package energy needs root to read RAPL (sudo -n); without it, W is reported as -.
set -uo pipefail
run=${1:?run number}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
: "${IMAGE:?set IMAGE}" "${OUT:?set OUT}"
name=${K3D_GPU_CLUSTER:-k3dgpu-r2-sbx}
export KUBECONFIG="${OUT}/kubeconfig-${run}" K3D_GPU_CLUSTER="${name}" K3D_GPU_IMAGE="${IMAGE}"
export K3D_GPU_SHARE="${K3D_GPU_SHARE:-${ROOT}/share}"
L="${ROOT}/scripts/k3d-gpu"
NS=agent-sandbox
PIP_PKG=${PIP_PKG:-tomli_w==1.2.0}
if [ -z "${TARGETS:-}" ]; then
    ts=$(ip -4 -o addr show tailscale0 2>/dev/null | awk '{sub(/\/.*/, "", $4); print $4; exit}')
    lan=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1); exit}')
    TARGETS="${ts:+${ts}:22 }${lan:+${lan}:22 }pypi.org:443"
fi
rapl() { sudo -n cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null || echo -; }
throttles() { cat /sys/devices/system/cpu/cpu*/thermal_throttle/package_throttle_count 2>/dev/null | awk '{s += $1} END {print s + 0}'; }
elapsed() { awk -v a="$1" -v b="$2" "BEGIN {printf \"%.${3:-2}f\", b - a}"; }
node() { docker exec "k3d-${name}-server-0" "$@"; }

# Confounds: package W over 5 s idle before the run, 1-min load, swap used.
e0=$(rapl); sleep 5; e1=$(rapl)
if [ "${e0}" = - ] || [ "${e1}" = - ]; then w=-; else
    w=$(awk -v a="${e0}" -v b="${e1}" 'BEGIN {printf "%.1f", (b - a) / 5e6}'); fi
echo "run=${run} date=$(date -u +%FT%TZ) idle_pkg_w=${w} load1=$(cut -d' ' -f1 /proc/loadavg) swap_used_mib=$(free -m | awk '/^Swap/ {print $3}')"
th0=$(throttles)

k3d cluster delete "${name}" >/dev/null 2>&1 || true
rm -f "${KUBECONFIG}"
"${L}" up > "${OUT}/up-${run}.log" 2>&1 || { echo "up_rc=fail (see ${OUT}/up-${run}.log)"; exit 1; }
t0=$(date +%s.%N)
"${L}" sandbox up > "${OUT}/sandbox-${run}.log" 2>&1; rc=$?
echo "sandbox_up_rc=${rc} sandbox_up_s=$(elapsed "${t0}" "$(date +%s.%N)" 1)"
grep -E '^  (ok|fail)' "${OUT}/sandbox-${run}.log" | sed 's/^ */status: /'
echo "warm_ready=$(kubectl -n "${NS}" get sandboxwarmpool python-ecores -o jsonpath='{.status.readyReplicas}')"

pod=$(kubectl -n "${NS}" get pods -l k3d-gpu.io/cores=efficient -o jsonpath='{.items[0].metadata.name}')
echo "pod=${pod} cpus=$(kubectl -n "${NS}" exec "${pod}" -- grep Cpus_allowed_list /proc/1/status | awk '{print $2}') host_atom=$(cat /sys/devices/cpu_atom/cpus 2>/dev/null) host_core=$(cat /sys/devices/cpu_core/cpus 2>/dev/null)"
img=$(kubectl -n "${NS}" get pod "${pod}" -o jsonpath='{.spec.containers[0].image}')
kubectl create namespace sbx-control >/dev/null
kubectl -n sbx-control run ctl --image="${img}" --restart=Never --command -- sleep 3600 >/dev/null
kubectl -n sbx-control wait --for=condition=Ready pod/ctl --timeout=120s >/dev/null

# kube-router logs each packet the sandbox policy drops through one NFLOG rule.
chain=$(node iptables-save | grep -o 'KUBE-NWPLCY-[A-Z0-9]* .*DROP by policy agent-sandbox/python-ecores-network-policy' | head -1 | cut -d' ' -f1)
droplog() { node iptables -nvxL "${chain}" | awk '/NFLOG/ {print $1; exit}'; }
d0=$(droplog)

# probe <ns> <pod> <host:port>: one 3 s TCP connect.
probe() {
    local t0 out rc
    t0=$(date +%s.%N)
    out=$(kubectl -n "$1" exec "$2" -- python3 -c '
import socket, sys
h, p = sys.argv[1].rsplit(":", 1)
try:
    socket.create_connection((h, int(p)), 3)
    print("CONNECT")
except Exception as e:
    print("NOCONNECT", type(e).__name__, e)
    sys.exit(1)' "$3" 2>&1 | tail -1); rc=$?
    echo "probe ns=$1 dst=$3 rc=${rc} s=$(elapsed "${t0}" "$(date +%s.%N)") ${out}"
}
for t in ${TARGETS}; do
    probe "${NS}" "${pod}" "${t}"
    probe sbx-control ctl "${t}"
done
echo "policy_chain=${chain} policy_drop_pkts=$(( $(droplog) - d0 ))"
echo "dns_pypi=$(kubectl -n "${NS}" exec "${pod}" -- python3 -c 'import socket; print(socket.gethostbyname("pypi.org"))' 2>&1)"
echo "env=$(kubectl -n "${NS}" exec "${pod}" -- sh -c 'echo HOME=$HOME uid=$(id -u)' 2>&1)"
t0=$(date +%s.%N)
out=$(kubectl -n "${NS}" exec "${pod}" -- sh -c "pip install --user --no-cache-dir ${PIP_PKG} 2>&1 | tail -1 && python3 -c 'import ${PIP_PKG%%==*} as m; print(\"IMPORT\", m.__file__)'" 2>&1); rc=$?
echo "pip_rc=${rc} pip_s=$(elapsed "${t0}" "$(date +%s.%N)" 1) $(tr '\n' ' ' <<< "${out}")"
echo "throttle_delta=$(( $(throttles) - th0 ))"
k3d cluster delete "${name}" >/dev/null 2>&1
rm -f "${KUBECONFIG}"
