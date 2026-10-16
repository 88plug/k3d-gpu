#!/usr/bin/env bash
# Optional: KServe in Standard mode (no Knative, no Istio) on a running k3d-gpu
# cluster, plus an OpenVINO Model Server InferenceService on the Intel GPU.
#
# Usage: examples/kserve/install.sh        (kubectl context = the k3d-gpu cluster)
# Needs: kubectl, helm 3.8+ (OCI charts), an Intel render node in /dev/dri.
set -euo pipefail

CERT_MANAGER_VERSION=v1.21.2   # supports Kubernetes 1.33-1.36
KSERVE_VERSION=v0.21.0
HERE="$(cd "$(dirname "$0")" && pwd)"
SYSROOT="${K3D_GPU_SYSROOT:-}"

# The k3d node sees the host's /dev/dri, so the pod's render node carries the
# host's group id.
render=$(compgen -G "${SYSROOT}/dev/dri/renderD*" | head -n1 || true)
if [ -z "${render}" ]; then
    echo "error: no ${SYSROOT}/dev/dri/renderD* on this host; the OVMS pod needs an Intel GPU" >&2
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

kubectl apply --server-side -f "${HERE}/openvino-runtime.yaml"

sed "s/supplementalGroups: \[\]/supplementalGroups: [${render_gid}]/" "${HERE}/qwen3-isvc.yaml" \
    | kubectl apply --server-side -f -

# First start downloads ~2.5 GB and compiles the model for the GPU.
kubectl wait --namespace llm --for=condition=Ready inferenceservice/qwen3 --timeout=30m
echo "ready: http://qwen3-predictor.llm.svc/v3 (model \"qwen3\")"
