#!/usr/bin/env bash
# Schema-check every manifest the launcher applies from share/ (share/*.yml;
# share/telemetry has its own job) against the Kubernetes version the image
# ships (Dockerfile K3S_TAG). Custom resources (SandboxTemplate,
# SandboxWarmPool, NodeFeatureRule, BalloonsPolicy, ...) validate against JSON
# schemas built from the CRDs shipped in share/, with unknown fields rejected
# the way the API server's strict field validation rejects them. Then a copy of
# the sandbox profile with a misspelled field and a misspelled kind must fail.
#
# Needs python3 with PyYAML. Uses a kubeconform binary when one is on PATH,
# else the pinned container image. Built-in schemas come from
# github.com/yannh/kubernetes-json-schema (network access needed).
#
# Usage: tests/share_kubeconform.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# ghcr.io/yannh/kubeconform:v0.8.0
KUBECONFORM_IMAGE="ghcr.io/yannh/kubeconform@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e"
K8S_VERSION=$(sed -n 's/^ARG K3S_TAG="v\([0-9.]*\)-k3s.*/\1/p' "${ROOT}/Dockerfile")
[ -n "${K8S_VERSION}" ] || { echo "no K3S_TAG version in Dockerfile" >&2; exit 1; }
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
mkdir -p "${WORK}/schemas" "${WORK}/m" "${WORK}/bad"

cp "${ROOT}"/share/*.yml "${WORK}/m/"
# `k3d-gpu recipe npu-embeddings` fills in the host's render group, the same way.
sed -i '/^[[:space:]]*#/!s/K3D_GPU_NPU_RENDER_GID/992/g' "${WORK}/m/npu-embeddings.yml"

# One JSON schema per CRD version: <schemas>/<group>/<kind>_<version>.json.
python3 - "${WORK}/schemas" "${WORK}"/m/*.yml <<'EOF'
import json, os, sys
import yaml

def strict(node):
    """Convert an OpenAPI v3 CRD schema node to JSON schema, closing objects."""
    if isinstance(node, list):
        return [strict(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: strict(v) for k, v in node.items() if not k.startswith("x-kubernetes-")}
    if node.get("x-kubernetes-int-or-string"):
        out.pop("type", None)
        out["anyOf"] = [{"type": "integer"}, {"type": "string"}]
    if out.pop("nullable", False) and "type" in out:
        out["type"] = [out["type"], "null"]
    if ("properties" in out and "additionalProperties" not in out
            and not node.get("x-kubernetes-preserve-unknown-fields")):
        out["additionalProperties"] = False
    return out

out_dir, files = sys.argv[1], sys.argv[2:]
for path in files:
    with open(path) as f:
        docs = [d for d in yaml.safe_load_all(f) if d]
    for d in docs:
        if d.get("kind") != "CustomResourceDefinition":
            continue
        spec = d["spec"]
        for v in spec["versions"]:
            schema = strict(v["schema"]["openAPIV3Schema"])
            props = schema.setdefault("properties", {})
            props.setdefault("apiVersion", {"type": "string"})
            props.setdefault("kind", {"type": "string"})
            props.setdefault("metadata", {"type": "object"})
            os.makedirs(os.path.join(out_dir, spec["group"]), exist_ok=True)
            name = "%s_%s.json" % (spec["names"]["kind"].lower(), v["name"])
            with open(os.path.join(out_dir, spec["group"], name), "w") as f:
                json.dump(schema, f)
EOF

# The sandbox profile with its egress policy key and a kind misspelled.
sed -e 's/^\(\s*\)networkPolicy:/\1netPolicy:/' "${WORK}/m/agent-sandbox-k3d.yml" > "${WORK}/bad/field.yml"
sed -e 's/^kind: SandboxWarmPool$/kind: SandboxWarmPoool/' "${WORK}/m/agent-sandbox-k3d.yml" > "${WORK}/bad/kind.yml"
cmp -s "${WORK}/m/agent-sandbox-k3d.yml" "${WORK}/bad/field.yml" && { echo "FAIL field mutation did not apply" >&2; exit 1; }
cmp -s "${WORK}/m/agent-sandbox-k3d.yml" "${WORK}/bad/kind.yml" && { echo "FAIL kind mutation did not apply" >&2; exit 1; }

run_kubeconform() {
    local args=(-strict -summary -output text -kubernetes-version "${K8S_VERSION}"
        -skip apiextensions.k8s.io/v1/CustomResourceDefinition
        -schema-location default
        -schema-location "${WORK}/schemas/{{ .Group }}/{{ .ResourceKind }}_{{ .ResourceAPIVersion }}.json")
    if type -P kubeconform >/dev/null; then
        kubeconform "${args[@]}" "$@"
    else
        docker run --rm -v "${WORK}:${WORK}:ro" "${KUBECONFORM_IMAGE}" "${args[@]}" "$@"
    fi
}

rc=0
summary=$(run_kubeconform "${WORK}/m") || rc=$?
printf '%s\n' "${summary}"
[ "${rc}" -eq 0 ] || { echo "FAIL share/*.yml does not validate" >&2; exit 1; }
# Only the CRD objects themselves may be skipped.
crds=$(cat "${WORK}"/m/*.yml | grep -c '^kind: CustomResourceDefinition$')
grep -qE "Invalid: 0, Errors: 0, Skipped: ${crds}\$" <<< "${summary}" \
    || { echo "FAIL expected exactly ${crds} skipped CRDs" >&2; exit 1; }

# Each copy must fail on the misspelled token, not on something else.
for case in field:netPolicy kind:SandboxWarmPoool; do
    bad=${case%%:*} token=${case#*:}
    run_kubeconform "${WORK}/bad/${bad}.yml" > "${WORK}/bad/${bad}.out" 2>&1 && rc=0 || rc=$?
    if [ "${rc}" -eq 0 ] || ! grep -q "${token}" "${WORK}/bad/${bad}.out"; then
        echo "FAIL misspelled ${bad} (${token}) in agent-sandbox-k3d.yml was not rejected" >&2
        sed 's/^/  | /' "${WORK}/bad/${bad}.out" >&2
        exit 1
    fi
    echo "ok   misspelled ${bad} (${token}) in agent-sandbox-k3d.yml is rejected"
done
