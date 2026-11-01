#!/usr/bin/env bash
# Optional: KServe in Standard mode (no Knative, no Istio) on a running k3d-gpu
# cluster, plus an OpenVINO Model Server InferenceService on the Intel GPU.
#
# Usage: examples/kserve/install.sh        (kubectl context = the k3d-gpu cluster)
# Needs: kubectl, helm 3.8+ (OCI charts), an Intel GPU on the xe or i915 driver.
set -euo pipefail

CERT_MANAGER_VERSION=v1.21.3   # supports Kubernetes 1.33-1.36
KSERVE_VERSION=v0.21.0
HERE="$(cd "$(dirname "$0")" && pwd)"
SYSROOT="${K3D_GPU_SYSROOT:-}"

# First Intel (PCI vendor 0x8086) render node bound to xe or i915. The device
# plugin advertises it as gpu.intel.com/<driver>, and the k3d node sees the
# host's /dev/dri, so the pod's render node carries the host's group id.
driver="" render=""
for dev in "${SYSROOT}"/sys/class/drm/renderD*/device; do
    [ "$(cat "${dev}/vendor" 2>/dev/null)" = "0x8086" ] || continue
    drv=$(readlink -f "${dev}/driver" 2>/dev/null) || continue
    drv=${drv##*/}
    case "${drv}" in xe|i915) ;; *) continue ;; esac
    node=${dev%/device}
    node="${SYSROOT}/dev/dri/${node##*/}"
    [ -e "${node}" ] || continue
    driver=${drv} render=${node}
    break
done
if [ -z "${render}" ]; then
    echo "error: no Intel render node (xe or i915) under ${SYSROOT}/sys/class/drm; the OVMS pod needs an Intel GPU" >&2
    exit 1
fi
render_gid=$(stat -c %g "${render}")

helm upgrade --install cert-manager oci://quay.io/jetstack/charts/cert-manager \
    --version "${CERT_MANAGER_VERSION}" --namespace cert-manager --create-namespace \
    --set crds.enabled=true --wait --timeout 10m

helm upgrade --install kserve-crd oci://ghcr.io/kserve/charts/kserve-crd \
    --version "${KSERVE_VERSION}" --namespace kserve --create-namespace \
    --wait --timeout 10m

helm upgrade --install kserve-resources oci://ghcr.io/kserve/charts/kserve-resources \
    --version "${KSERVE_VERSION}" --namespace kserve \
    --values "${HERE}/kserve-values.yaml" --wait --timeout 10m

# known-issue-begin: kserve-4592
kubectl apply --server-side -f "${HERE}/openvino-runtime.yaml"
# known-issue-end: kserve-4592

sed -e "s/supplementalGroups: \[\]/supplementalGroups: [${render_gid}]/" \
    -e "s|gpu.intel.com/i915:|gpu.intel.com/${driver}:|" "${HERE}/qwen3-isvc.yaml" \
    | kubectl apply --server-side -f -

# First start downloads ~2.5 GB and compiles the model for the GPU.
kubectl wait --namespace llm --for=condition=Ready inferenceservice/qwen3 --timeout=30m
echo "ready: http://qwen3-predictor.llm.svc/v3 and http://ovms.llm.svc:8000/v3 (model \"qwen3\")"
