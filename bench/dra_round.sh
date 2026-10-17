#!/usr/bin/env bash
# One H16/H17 round (EXPERIMENTS.md): a fresh cluster in mode D (K3D_GPU_DRA=1,
# Intel GPU DRA driver) or P (intel-gpu-plugin), a timed `k3d-gpu up`, three pods
# timed from `kubectl apply` to Succeeded, then two pods created together and their
# phases read 20 s later. Appends one RESULT line to $OUT/results.txt, then deletes
# the cluster.
#
# Usage: bench/dra_round.sh <D|P> <run#>
# Env:   IMAGE  node image (docker build -t "$IMAGE" .)
#        OUT    results dir (a scratch kubeconfig is written there)
#        K3D_GPU_CLUSTER (default k3dgpu-dra)
# Package energy needs root to read RAPL (sudo -n); without it, W is reported as -.
set -euo pipefail
mode=${1:?D or P} run=${2:?run number}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
: "${IMAGE:?set IMAGE}" "${OUT:?set OUT}"
name=${K3D_GPU_CLUSTER:-k3dgpu-dra}
export KUBECONFIG="${OUT}/kubeconfig-${run}" K3D_GPU_CLUSTER="${name}" K3D_GPU_IMAGE="${IMAGE}"
case "${mode}" in
    D) export K3D_GPU_DRA=1 ;;
    P) export K3D_GPU_DRA=0 ;;
    *) echo "mode must be D or P" >&2; exit 2 ;;
esac
log="${OUT}/run-${run}-${mode}.log"
rapl() { sudo -n cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null || echo -; }
throttles() { cat /sys/devices/system/cpu/cpu*/thermal_throttle/package_throttle_count 2>/dev/null | awk '{s += $1} END {print s + 0}'; }
elapsed() { awk -v a="$1" -v b="$2" "BEGIN {printf \"%.${3:-2}f\", b - a}"; }

e0=$(rapl); th0=$(throttles); t0=$(date +%s.%N)
"${ROOT}/scripts/k3d-gpu" up > "${log}" 2>&1
t1=$(date +%s.%N); e1=$(rapl)
up_s=$(elapsed "${t0}" "${t1}" 1)
if [ "${e0}" = - ] || [ "${e1}" = - ]; then w=-; else
    w=$(awk -v a="${e0}" -v b="${e1}" -v s="${up_s}" 'BEGIN {printf "%.1f", (b - a) / 1e6 / s}'); fi
k3d image import busybox:1.37 -c "${name}" >/dev/null 2>&1

# pod <name> <command as a JSON list>: one GPU pod for this mode.
pod() {
    local res
    if [ "${mode}" = D ]; then
        res='resources: {claims: [{name: gpu}]}'
        printf 'apiVersion: v1\nkind: Pod\nmetadata: {name: %s}\nspec:\n  restartPolicy: Never\n  resourceClaims: [{name: gpu, resourceClaimTemplateName: h16-gpu}]\n' "$1"
    else
        res='resources: {limits: {gpu.intel.com/i915: 1}}'
        printf 'apiVersion: v1\nkind: Pod\nmetadata: {name: %s}\nspec:\n  restartPolicy: Never\n' "$1"
    fi
    printf '  containers:\n  - {name: c, image: "busybox:1.37", imagePullPolicy: IfNotPresent, command: %s, %s}\n' "$2" "${res}"
}
if [ "${mode}" = D ]; then
    kubectl apply -f - >/dev/null <<'EOF'
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata: {name: h16-gpu}
spec:
  spec:
    devices:
      requests:
      - name: gpu
        exactly: {deviceClassName: gpu.intel.com}
EOF
fi
{
    echo "== facts"
    kubectl get ds -A --no-headers | awk '{print $1 "/" $2}'
    kubectl get resourceslices --no-headers \
        -o custom-columns=NAME:.metadata.name,DRIVER:.spec.driver,DEVICES:.spec.devices[*].name 2>&1 || true
    kubectl get node -o jsonpath='{.items[0].status.allocatable}'; echo
} >> "${log}"

lat=()
for i in 1 2 3; do
    p=h16-lat-${i}
    s=$(date +%s.%N)
    pod "${p}" '["ls","/dev/dri"]' | kubectl apply -f - >/dev/null
    until ph=$(kubectl get pod "${p}" -o jsonpath='{.status.phase}' 2>/dev/null) && [[ "${ph}" =~ Succeeded|Failed ]]; do
        sleep 0.2
    done
    lat+=("$(elapsed "${s}" "$(date +%s.%N)")")
    echo "== ${p} ${ph}: $(kubectl logs "${p}" | tr '\n' ' ')" >> "${log}"
    kubectl delete pod "${p}" --wait >/dev/null
done

pod h16-a '["sh","-c","ls /dev/dri; sleep 40"]' | kubectl apply -f - >/dev/null
pod h16-b '["sh","-c","ls /dev/dri; sleep 40"]' | kubectl apply -f - >/dev/null
sleep 20
conc=$(kubectl get pod h16-a h16-b -o jsonpath='{range .items[*]}{.status.phase},{end}')
{
    echo "== concurrency at 20s: ${conc}"
    kubectl get pod h16-b -o jsonpath='{.status.conditions[?(@.type=="PodScheduled")].message}'; echo
    if [ "${mode}" = D ]; then kubectl get resourceclaims; fi
} >> "${log}" 2>&1

th1=$(throttles)
load=$(cut -d' ' -f1 /proc/loadavg)
swap=$(free -m | awk '/^Swap/ {print $3}')
echo "RESULT run=${run} mode=${mode} up_s=${up_s} up_W=${w} lat=${lat[*]} conc=${conc} load=${load} swapMiB=${swap} thr_delta=$((th1 - th0))" \
    | tee -a "${OUT}/results.txt"
"${ROOT}/scripts/k3d-gpu" down >> "${log}" 2>&1
rm -f "${KUBECONFIG}"
