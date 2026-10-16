#!/usr/bin/env bash
# Tests for the optional KServe example (examples/kserve).
#  1. install.sh against stub helm/kubectl: pinned chart versions, Standard-mode
#     values, render-group substitution, and the no-GPU error path. No cluster.
#  2. kubeconform at Kubernetes 1.34 and 1.35 over the example manifests and the
#     rendered cert-manager/KServe charts, with KServe CRD schemas generated from
#     the pinned kserve-crd chart. Runs when kubeconform and helm are on PATH
#     (needs network to pull the OCI charts); skipped otherwise.
#
# Usage: tests/kserve_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EX="${ROOT}/examples/kserve"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

# check <name> <command...>: passes when the command succeeds.
check() {
    local name=$1; shift
    if "$@"; then PASS=$((PASS + 1)); echo "ok   ${name}"
    else FAILS=$((FAILS + 1)); echo "FAIL ${name}"; fi
}
has()      { grep -q -- "$1" "$2"; }      # has <regex> <file>
has_ere()  { grep -qE -- "$1" "$2"; }     # has_ere <ERE> <file>
is()       { [ "$1" = "$2" ]; }
line_of()  { grep -n -- "$1" "$2" | head -n1 | cut -d: -f1; }

# --- 1. install.sh with stub binaries --------------------------------------
STUB="${WORK}/bin"
mkdir -p "${STUB}"
cat > "${STUB}/helm" <<'EOF'
#!/usr/bin/env bash
echo "helm $*" >> "${CALLS}"
EOF
cat > "${STUB}/kubectl" <<'EOF'
#!/usr/bin/env bash
echo "kubectl $*" >> "${CALLS}"
if [ "${*: -1}" = "-" ]; then cat > "${STDIN_OUT}"; fi
EOF
chmod +x "${STUB}/helm" "${STUB}/kubectl"

export CALLS="${WORK}/calls" STDIN_OUT="${WORK}/isvc.yaml"
run_install() { # run_install <sysroot>: prints install.sh's exit code
    : > "${CALLS}"; : > "${STDIN_OUT}"
    PATH="${STUB}:${PATH}" K3D_GPU_SYSROOT="$1" bash "${EX}/install.sh" > "${WORK}/out" 2>&1
    echo $?
}

SYS="${WORK}/sys"
mkdir -p "${SYS}/dev/dri"
touch "${SYS}/dev/dri/renderD128"
gid=$(stat -c %g "${SYS}/dev/dri/renderD128")

check "install.sh exits 0 with a render node" is "$(run_install "${SYS}")" 0
check "cert-manager chart pinned to v1.21.2 with CRDs" has \
    '^helm upgrade --install cert-manager oci://quay.io/jetstack/charts/cert-manager --version v1.21.2 .*--set crds.enabled=true' "${CALLS}"
check "kserve-crd chart pinned to v0.21.0" has \
    '^helm upgrade --install kserve-crd oci://ghcr.io/kserve/charts/kserve-crd --version v0.21.0 ' "${CALLS}"
check "kserve-resources chart pinned to v0.21.0 with example values" has \
    "^helm upgrade --install kserve-resources oci://ghcr.io/kserve/charts/kserve-resources --version v0.21.0 .*--values ${EX}/kserve-values.yaml" "${CALLS}"
check "cert-manager installs before KServe" \
    [ "$(line_of cert-manager "${CALLS}")" -lt "$(line_of kserve-crd "${CALLS}")" ]
check "runtime applied" has "^kubectl apply --server-side -f ${EX}/openvino-runtime.yaml$" "${CALLS}"
check "ISVC gets the host render gid" has "supplementalGroups: \[${gid}\]$" "${STDIN_OUT}"
check "ISVC requests the Intel GPU" has 'gpu.intel.com/i915: "1"' "${STDIN_OUT}"
check "ISVC carries the hybrid-core label" has 'k3d-gpu.io/cores: performance' "${STDIN_OUT}"
check "waits for the ISVC" has \
    '^kubectl wait --namespace llm --for=condition=Ready inferenceservice/qwen3' "${CALLS}"

check "install.sh fails without a render node" is "$(run_install "${WORK}/empty")" 1
check "no-GPU error names the missing node" has 'no .*/dev/dri/renderD\*' "${WORK}/out"
check "nothing installed without a render node" [ ! -s "${CALLS}" ]

check "values select Standard mode" has 'deploymentMode: Standard' "${EX}/kserve-values.yaml"
check "values disable Ingress creation" has 'disableIngressCreation: true' "${EX}/kserve-values.yaml"
check "OVMS image pinned to tag and digest" has_ere \
    'image: openvino/model_server:2026\.4\.1-gpu@sha256:[0-9a-f]{64}$' "${EX}/openvino-runtime.yaml"
check "model pinned to a Hugging Face revision" has_ere \
    'storageUri: hf://OpenVINO/Qwen3-4B-int4-ov:[0-9a-f]{40}$' "${EX}/qwen3-isvc.yaml"

# --- 2. kubeconform ---------------------------------------------------------
if ! command -v kubeconform >/dev/null || ! command -v helm >/dev/null; then
    echo "skip kubeconform (needs kubeconform and helm on PATH)"
else
    CH="${WORK}/charts" SC="${WORK}/schemas"
    mkdir -p "${CH}" "${SC}"
    pull() { helm pull "$1" --version "$2" --untar --untardir "${CH}" >/dev/null; }
    check "pull kserve-crd v0.21.0" pull oci://ghcr.io/kserve/charts/kserve-crd v0.21.0
    check "pull kserve-resources v0.21.0" pull oci://ghcr.io/kserve/charts/kserve-resources v0.21.0
    check "pull cert-manager v1.21.2" pull oci://quay.io/jetstack/charts/cert-manager v1.21.2

    helm template kserve-crd "${CH}/kserve-crd" -n kserve --include-crds > "${WORK}/crds.yaml"
    helm template kserve-resources "${CH}/kserve-resources" -n kserve \
        --values "${EX}/kserve-values.yaml" > "${WORK}/kserve.yaml"
    helm template cert-manager "${CH}/cert-manager" -n cert-manager \
        --set crds.enabled=true > "${WORK}/cert-manager.yaml"
    check "rendered KServe config defaults to Standard" \
        has '"defaultDeploymentMode": "Standard"' "${WORK}/kserve.yaml"
    check "rendered KServe config disables Ingress" \
        has '"disableIngressCreation": true' "${WORK}/kserve.yaml"

    # CRD openAPI schemas -> <kind>_<version>.json, closed objects so unknown
    # fields fail (except where the CRD preserves unknown fields).
    python3 - "${WORK}/crds.yaml" "${SC}" <<'EOF'
import json, sys, yaml
def close(s):
    if isinstance(s, dict):
        if s.get("type") == "object" and "properties" in s \
                and "additionalProperties" not in s \
                and not s.get("x-kubernetes-preserve-unknown-fields"):
            s["additionalProperties"] = False
        for v in s.values():
            close(v)
    elif isinstance(s, list):
        for v in s:
            close(v)
for doc in yaml.safe_load_all(open(sys.argv[1])):
    if not doc or doc.get("kind") != "CustomResourceDefinition":
        continue
    kind = doc["spec"]["names"]["kind"].lower()
    for ver in doc["spec"]["versions"]:
        schema = ver["schema"]["openAPIV3Schema"]
        close(schema)
        with open(f"{sys.argv[2]}/{kind}_{ver['name']}.json", "w") as f:
            json.dump(schema, f)
EOF
    check "InferenceService schema generated" [ -s "${SC}/inferenceservice_v1beta1.json" ]
    check "ClusterServingRuntime schema generated" [ -s "${SC}/clusterservingruntime_v1alpha1.json" ]

    sed "s/supplementalGroups: \[\]/supplementalGroups: [985]/" "${EX}/qwen3-isvc.yaml" > "${WORK}/isvc-filled.yaml"
    sed "s/^    labels:/    lables:/" "${WORK}/isvc-filled.yaml" > "${WORK}/isvc-typo.yaml"
    validate() { # validate <k8s version> [kubeconform args...]
        local k8s=$1; shift
        kubeconform -strict -summary -kubernetes-version "${k8s}" -schema-location default \
            -schema-location "${SC}/{{ .ResourceKind }}_{{ .ResourceAPIVersion }}.json" "$@" \
            > "${WORK}/kc.out" 2>&1 || { cat "${WORK}/kc.out"; return 1; }
    }
    rejects() { ! validate "$@" > /dev/null; }
    for k8s in 1.34.0 1.35.0; do
        check "kubeconform ${k8s}: example manifests" validate "${k8s}" \
            "${EX}/openvino-runtime.yaml" "${EX}/qwen3-isvc.yaml" "${WORK}/isvc-filled.yaml"
        # cert-manager's own CRs (Certificate, Issuer) have no schema here.
        check "kubeconform ${k8s}: rendered cert-manager + KServe charts" validate "${k8s}" \
            -ignore-missing-schemas "${WORK}/cert-manager.yaml" "${WORK}/kserve.yaml" "${WORK}/crds.yaml"
        # The generated schemas must reject a misspelt field, or the checks above prove nothing.
        check "kubeconform ${k8s}: rejects an unknown ISVC field" rejects "${k8s}" "${WORK}/isvc-typo.yaml"
    done
fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
