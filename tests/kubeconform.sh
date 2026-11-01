#!/usr/bin/env bash
# Schema-check the Intel GPU DRA manifests against the Kubernetes version the
# image ships (Dockerfile K3S_TAG) and the version upstream supports the driver
# on: share/intel-gpu-dra.yml plus the ResourceClaimTemplate and Pod that
# `k3d-gpu test` creates in DRA mode. Uses a kubeconform binary when one is on
# PATH, else the pinned container image. Schemas come from
# github.com/yannh/kubernetes-json-schema (network access needed).
#
# Usage: tests/kubeconform.sh [k8s version...]   (default: K3S_TAG's version and 1.36.5)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# The pinned kubeconform image, where the CI jobs read it too.
KUBECONFORM_IMAGE=$(sed -n 's/^KUBECONFORM_IMAGE=//p' "${ROOT}/hack/ci-tools.env")
: "${KUBECONFORM_IMAGE:?hack/ci-tools.env has no KUBECONFORM_IMAGE}"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

k3s=$(sed -n 's/^ARG K3S_TAG="v\([0-9.]*\)-k3s.*/\1/p' "${ROOT}/Dockerfile")
[ -n "${k3s}" ] || { echo "no K3S_TAG version in Dockerfile" >&2; exit 1; }
versions=("$@")
[ "${#versions[@]}" -gt 0 ] || versions=("${k3s}" 1.36.5)

# The objects `k3d-gpu test` creates, from the launcher's own functions.
# shellcheck source=scripts/k3d-gpu
source "${ROOT}/scripts/k3d-gpu" help >/dev/null
set +e  # the launcher turns -e on; keep reporting every version
cp "${ROOT}/share/intel-gpu-dra.yml" "${WORK}/"
dra_claim_template > "${WORK}/dra-test-template.yaml"
overrides=$(dra_test_overrides)
# kubectl run adds apiVersion, kind, name and restartPolicy around the overrides.
echo "{\"apiVersion\":\"v1\",\"kind\":\"Pod\",\"metadata\":{\"name\":\"${DRA_TEST}\"},${overrides#\{}" \
    | sed 's/"spec":{/"spec":{"restartPolicy":"Never",/' > "${WORK}/dra-test-pod.json"

run_kubeconform() {
    if type -P kubeconform >/dev/null; then
        kubeconform "$@"
    else
        docker run --rm -v "${WORK}:${WORK}:ro" "${KUBECONFORM_IMAGE}" "$@"
    fi
}

rc=0
for v in "${versions[@]}"; do
    echo "== Kubernetes ${v}"
    run_kubeconform -strict -summary -output text -kubernetes-version "${v}" "${WORK}" || rc=1
done
exit "${rc}"
