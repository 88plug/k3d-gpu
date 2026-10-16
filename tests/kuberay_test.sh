#!/usr/bin/env bash
# Static checks for examples/kuberay: no cluster, no images pulled.
#   1. kubeconform -strict against the RayCluster CRD shipped in the pinned
#      kuberay-operator helm chart (plus a mutated copy that must fail, so a
#      skipped schema cannot pass silently).
#   2. The hybrid placement contract: head and cpu-e on E-cores, cpu-p on P-cores,
#      igpu requests gpu.intel.com/i915, CPU requests == limits in whole CPUs.
# Downloads are pinned by version and sha256. Needs curl, tar, python3 + PyYAML.
#
# Usage: tests/kuberay_test.sh [manifest]

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="${1:-${ROOT}/examples/kuberay/raycluster-hybrid.yaml}"

KUBECONFORM_VERSION=v0.8.0
KUBECONFORM_SHA256=9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
OPENAPI2JSONSCHEMA_SHA256=d145babfbb765004030764e1b4e518bfb7a4bd7f111691a08fa57983b81881f3
KUBERAY_CHART_VERSION=1.3.0
KUBERAY_CHART_SHA256=425fac6ad7c00d17d6488d4895b9329c1028806f6083028f79fd2b28b80eb0c1

WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

# fetch <url> <sha256> <dest>
fetch() {
    curl -fsSL --retry 3 -o "$3" "$1"
    echo "$2  $3" | sha256sum -c --quiet - || { echo "FAIL: sha256 mismatch for $1" >&2; exit 1; }
}

fetch "https://github.com/yannh/kubeconform/releases/download/${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" \
    "${KUBECONFORM_SHA256}" "${WORK}/kubeconform.tar.gz"
tar -xzf "${WORK}/kubeconform.tar.gz" -C "${WORK}" kubeconform
fetch "https://raw.githubusercontent.com/yannh/kubeconform/${KUBECONFORM_VERSION}/scripts/openapi2jsonschema.py" \
    "${OPENAPI2JSONSCHEMA_SHA256}" "${WORK}/openapi2jsonschema.py"
fetch "https://github.com/ray-project/kuberay-helm/releases/download/kuberay-operator-${KUBERAY_CHART_VERSION}/kuberay-operator-${KUBERAY_CHART_VERSION}.tgz" \
    "${KUBERAY_CHART_SHA256}" "${WORK}/kuberay-operator.tgz"
tar -xzf "${WORK}/kuberay-operator.tgz" -C "${WORK}" kuberay-operator/crds/ray.io_rayclusters.yaml

mkdir -p "${WORK}/schemas"
(cd "${WORK}/schemas" && FILENAME_FORMAT='{kind}_{version}' \
    python3 ../openapi2jsonschema.py ../kuberay-operator/crds/ray.io_rayclusters.yaml >/dev/null)

conform() {
    "${WORK}/kubeconform" -strict -summary \
        -schema-location "${WORK}/schemas/{{ .ResourceKind }}_{{ .ResourceAPIVersion }}.json" "$@"
}

echo "== kubeconform (KubeRay ${KUBERAY_CHART_VERSION} CRD) =="
conform "${MANIFEST}"

# Negative control: a misspelled field must be rejected by the same schema.
sed 's/groupName: cpu-e/groupNme: cpu-e/' "${MANIFEST}" > "${WORK}/bad.yaml"
if conform "${WORK}/bad.yaml" >/dev/null 2>&1; then
    echo "FAIL: kubeconform accepted an unknown field; the RayCluster schema is not applied" >&2
    exit 1
fi
echo "ok: mutated manifest rejected"

echo "== placement contract =="
python3 - "${MANIFEST}" <<'EOF'
import sys, yaml

doc = yaml.safe_load(open(sys.argv[1]))
spec = doc["spec"]
fails = []

def check(cond, msg):
    print(("ok: " if cond else "FAIL: ") + msg)
    if not cond:
        fails.append(msg)

def label(tmpl):
    return tmpl.get("metadata", {}).get("labels", {}).get("k3d-gpu.io/cores")

def container(tmpl):
    return tmpl["spec"]["containers"][0]

head = spec["headGroupSpec"]
groups = {g["groupName"]: g for g in spec["workerGroupSpecs"]}
check(set(groups) == {"cpu-p", "cpu-e", "igpu"}, "worker groups are cpu-p, cpu-e, igpu")
check(label(head["template"]) == "efficient", "head labelled k3d-gpu.io/cores=efficient")
check(head["rayStartParams"].get("num-cpus") == "0", "head runs no Ray tasks (num-cpus=0)")
check(label(groups["cpu-p"]["template"]) == "performance", "cpu-p labelled performance")
check(label(groups["cpu-e"]["template"]) == "efficient", "cpu-e labelled efficient")
check(label(groups["igpu"]["template"]) is None, "igpu has no core label (shared balloon)")

for name, tmpl in [("head", head["template"]), ("cpu-p", groups["cpu-p"]["template"]),
                   ("cpu-e", groups["cpu-e"]["template"])]:
    res = container(tmpl)["resources"]
    cpu = str(res["requests"]["cpu"])
    check(cpu.isdigit() and str(res["limits"]["cpu"]) == cpu,
          f"{name}: whole-CPU request equal to limit ({cpu})")
    check(tmpl["spec"].get("nodeSelector", {}).get("k3d-gpu.io/hybrid-cpu") == "true",
          f"{name}: nodeSelector k3d-gpu.io/hybrid-cpu=true")

igpu = container(groups["igpu"]["template"])["resources"]
check(igpu["limits"].get("gpu.intel.com/i915") == 1 and igpu["requests"].get("gpu.intel.com/i915") == 1,
      "igpu requests and limits gpu.intel.com/i915: 1")

for name, want in [("cpu-p", '{\\"p_core\\": 4}'), ("cpu-e", '{\\"e_core\\": 2}'), ("igpu", '{\\"intel_gpu\\": 1}')]:
    check(groups[name]["rayStartParams"].get("resources") == f'"{want}"', f"{name}: Ray resource {want}")

images = {container(head["template"])["image"]} | {container(g["template"])["image"] for g in groups.values()}
check(len(images) == 1, "one Ray image across head and workers")
image = images.pop()
tag = image.split("@")[0].rsplit(":", 1)[1]
check("@sha256:" in image, "Ray image pinned by digest")
check(tag.split("-")[0] == spec["rayVersion"], f"rayVersion {spec['rayVersion']} matches image tag {tag}")

sys.exit(1 if fails else 0)
EOF
echo "PASS"
