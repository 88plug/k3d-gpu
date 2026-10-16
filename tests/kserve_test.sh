#!/usr/bin/env bash
# Tests for the optional KServe example (examples/kserve).
#  1. install.sh against stub helm/kubectl and a fake sysfs: pinned chart
#     versions, Standard-mode values, the Intel render node's driver and group
#     filled into the ISVC, the ovms alias Service, and the no-GPU error path.
#     No cluster.
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

# render_node <sysroot> <renderDN> <pci-vendor> <driver>: a DRM render node in
# /dev/dri plus its sysfs entry, laid out like the kernel's.
render_node() {
    local root=$1 node=$2 vendor=$3 drv=$4
    local pci="${root}/sys/devices/pci0000:00/0000:00:${node#renderD}.0"
    mkdir -p "${root}/dev/dri" "${root}/sys/class/drm/${node}" "${pci}" "${root}/sys/bus/pci/drivers/${drv}"
    touch "${root}/dev/dri/${node}"
    echo "${vendor}" > "${pci}/vendor"
    ln -sfn "${root}/sys/bus/pci/drivers/${drv}" "${pci}/driver"
    ln -sfn "${pci}" "${root}/sys/class/drm/${node}/device"
}
# doc_of <kind> <name> <file>: prints the YAML document of that kind and name.
doc_of() {
    awk -v k="kind: $1" -v n="  name: $2" '
        /^---$/ { if (hk && hn) printf "%s", d; d = ""; hk = hn = 0; next }
        { d = d $0 "\n"; if ($0 == k) hk = 1; if ($0 == n) hn = 1 }
        END { if (hk && hn) printf "%s", d }' "$3"
}

SYS="${WORK}/sys"
render_node "${SYS}" renderD128 0x8086 i915
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
# Agent-stack consumers call http://ovms.<ns>.svc:8000/v3 (port name "http").
doc_of Service ovms "${STDIN_OUT}" > "${WORK}/ovms-svc.yaml"
check "ovms alias Service in namespace llm" has '^  namespace: llm$' "${WORK}/ovms-svc.yaml"
check "ovms alias Service: port http 8000 -> OVMS rest port 8080" \
    grep -qzE -- $'- name: http\n +port: 8000\n +targetPort: 8080\n' "${WORK}/ovms-svc.yaml"
check "ovms selector names the ISVC" has '^    serving.kserve.io/inferenceservice: qwen3$' "${WORK}/ovms-svc.yaml"
check "ovms selector names the predictor" has '^    component: predictor$' "${WORK}/ovms-svc.yaml"
check "ISVC carries the hybrid-core label" has 'k3d-gpu.io/cores: performance' "${STDIN_OUT}"
check "waits for the ISVC" has \
    '^kubectl wait --namespace llm --for=condition=Ready inferenceservice/qwen3' "${CALLS}"

check "install.sh fails without a render node" is "$(run_install "${WORK}/empty")" 1
check "no-GPU error names the missing node" has 'no Intel render node (xe or i915)' "${WORK}/out"
check "nothing installed without a render node" [ ! -s "${CALLS}" ]

# xe-driver GPU (Lunar Lake, Battlemage, ...): the device plugin advertises
# gpu.intel.com/xe, so the ISVC must request that, not i915.
XE="${WORK}/xe"
render_node "${XE}" renderD128 0x8086 xe
check "install.sh exits 0 on an xe GPU" is "$(run_install "${XE}")" 0
check "ISVC requests gpu.intel.com/xe on an xe GPU" has 'gpu.intel.com/xe: "1"' "${STDIN_OUT}"
check "ISVC drops gpu.intel.com/i915 on an xe GPU" [ "$(grep -c 'gpu.intel.com/i915' "${STDIN_OUT}")" -eq 0 ]

# NVIDIA render node first (renderD128), Intel second: the Intel node's driver
# and group go into the ISVC.
MIX="${WORK}/mix"
render_node "${MIX}" renderD128 0x10de nvidia
render_node "${MIX}" renderD129 0x8086 xe
other=$(id -G | tr ' ' '\n' | grep -vx "$(id -g)" | head -n1)
[ -n "${other}" ] && chgrp "${other}" "${MIX}/dev/dri/renderD128"
mix_gid=$(stat -c %g "${MIX}/dev/dri/renderD129")
check "install.sh exits 0 with NVIDIA + Intel render nodes" is "$(run_install "${MIX}")" 0
check "ISVC gets the Intel node's gid, not NVIDIA's" has "supplementalGroups: \[${mix_gid}\]$" "${STDIN_OUT}"
check "ISVC requests the Intel node's driver" has 'gpu.intel.com/xe: "1"' "${STDIN_OUT}"

NV="${WORK}/nv"
render_node "${NV}" renderD128 0x10de nvidia
check "install.sh fails with only an NVIDIA render node" is "$(run_install "${NV}")" 1
check "nothing installed with only an NVIDIA render node" [ ! -s "${CALLS}" ]

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
    # rejects <k8s> <file>: kubeconform finds exactly one invalid resource, the
    # misspelt predictor field, and no errors (a missing file or bad YAML is an error).
    rejects() {
        ! validate "$@" > /dev/null \
            && has "at '/spec/predictor': additional properties 'lables' not allowed" "${WORK}/kc.out" \
            && has 'Invalid: 1, Errors: 0' "${WORK}/kc.out"
    }
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
