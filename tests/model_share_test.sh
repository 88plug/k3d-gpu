#!/usr/bin/env bash
# `k3d-gpu model-share` and the H28 probe (bench/zero-copy-models/probe.sh), no
# cluster: a fake docker on PATH plays the server node, so each snapshotter the
# launcher can meet is covered. Rendered manifests are schema-checked when
# kubeconform is on PATH.
#
# Usage: tests/model_share_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
K8S_VERSION=$(sed -n 's/^ARG K3S_TAG="v\([0-9.]*\)-k3s.*/\1/p' "${ROOT}/Dockerfile")
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

# expect <name> <fixed string> <text> [negate]
expect() {
    if grep -qF -- "$2" <<< "$3"; then
        if [ "${4:-}" = negate ]; then fail "$1 (found: $2)"; else ok "$1"; fi
    elif [ "${4:-}" = negate ]; then ok "$1"
    else fail "$1 (missing: $2)"
    fi
}

# Fake docker: `docker exec <node> sh -c ...` prints a k3s containerd config with
# FAKE_SNAP as its snapshotter; with FAKE_SNAP empty the node does not exist.
mkdir -p "${WORK}/bin"
cat > "${WORK}/bin/docker" <<'EOF'
#!/usr/bin/env bash
[ "$1" = exec ] && [ -n "${FAKE_SNAP:-}" ] || exit 1
printf '[plugins."io.containerd.cri.v1.images"]\n  snapshotter = "%s"\n  disable_snapshot_annotations = true\n' "${FAKE_SNAP}"
EOF
chmod +x "${WORK}/bin/docker"

DIGEST="sha256:$(printf '%064d' 7)"
MODEL="registry.example/models/qwen3-0.6b@${DIGEST}"
AGENT="python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb"

# ms <FAKE_SNAP> <args...>: stdout and stderr of the launcher, then "rc=<code>".
ms() {
    local snap="$1"; shift
    local out rc
    out=$(PATH="${WORK}/bin:${PATH}" FAKE_SNAP="${snap}" K3D_GPU_SHARE="${ROOT}/share" \
        K3D_GPU_CLUSTER=ms-test NO_COLOR=1 "${ROOT}/scripts/k3d-gpu" model-share "$@" 2>&1)
    rc=$?
    printf '%s\nrc=%s\n' "${out}" "${rc}"
}

# ---- render ---------------------------------------------------------------------
out=$(ms overlayfs --image "${MODEL}" --agent-image "${AGENT}" --replicas 8 --name fleet)
expect "render: exit 0"               "rc=0" "${out}"
expect "render: replicas substituted" "  replicas: 8" "${out}"
expect "render: model image volume"   "reference: ${MODEL}" "${out}"
expect "render: agent image"          "image: ${AGENT}" "${out}"
expect "render: mount read-only"      "readOnly: true" "${out}"
expect "render: pull only if absent"  "pullPolicy: IfNotPresent" "${out}"
expect "render: name"                 "name: fleet" "${out}"
expect "render: no placeholder left"  "K3D_GPU_MS_" "${out}" negate
expect "render: no template comments" "# Zero-copy" "${out}" negate
expect "render: header names inputs"  "# k3d-gpu model-share: replicas=8 snapshotter=overlayfs" "${out}"
expect "overlayfs: no warning"        "WARN" "${out}" negate

# ---- auto-adapt -----------------------------------------------------------------
out=$(ms native --image "${MODEL}" --agent-image "${AGENT}" --replicas 4)
expect "native: warns per-pod copy"   "uses the native snapshotter" "${out}"
expect "native: budgets N x size"     "budget 4 x model size" "${out}"
expect "native: still renders"        "rc=0" "${out}"
out=$(ms "" --image "${MODEL}" --agent-image "${AGENT}")
expect "no cluster: says so"          "no running cluster 'ms-test'" "${out}"
expect "no cluster: snapshotter unknown" "snapshotter=unknown" "${out}"
expect "default replicas 1"           "  replicas: 1" "${out}"
out=$(ms overlayfs --image k3d-registry.localhost:5000/m:v1 --agent-image localhost:5000/agent)
expect "registry with port: renders"  "rc=0" "${out}"
expect "registry with port: ref kept" "reference: k3d-registry.localhost:5000/m:v1" "${out}"
expect "registry with port: agent"    "image: localhost:5000/agent" "${out}"
expect "port without path fails"      "rc=1" "$(ms overlayfs --image 'host:5000:v1' --agent-image "${AGENT}")"
expect "empty path segment fails"     "rc=1" "$(ms overlayfs --image 'host//m' --agent-image "${AGENT}")"
out=$(ms overlayfs --image registry.example/models/qwen3:0.6b --agent-image "${AGENT}")
expect "tag: warns not pinned"        "is not pinned by digest" "${out}"
out=$(ms overlayfs --image "${MODEL}" --agent-image "${AGENT}")
expect "digest: no pin warning"       "not pinned" "${out}" negate

# ---- input validation -----------------------------------------------------------
expect "missing --image fails"        "rc=1" "$(ms overlayfs --agent-image "${AGENT}")"
expect "missing --agent-image fails"  "rc=1" "$(ms overlayfs --image "${MODEL}")"
expect "replicas 0 fails"             "rc=1" "$(ms overlayfs --image "${MODEL}" --agent-image "${AGENT}" --replicas 0)"
expect "replicas text fails"          "rc=1" "$(ms overlayfs --image "${MODEL}" --agent-image "${AGENT}" --replicas 3x)"
expect "uppercase name fails"         "rc=1" "$(ms overlayfs --image "${MODEL}" --agent-image "${AGENT}" --name Fleet)"
expect "sed delimiter in image fails" "rc=1" "$(ms overlayfs --image 'a|b' --agent-image "${AGENT}")"
expect "newline in image fails"       "rc=1" "$(ms overlayfs --image "$(printf 'a\nb')" --agent-image "${AGENT}")"
expect "--device is not a flag"       "rc=1" "$(ms overlayfs --image "${MODEL}" --agent-image "${AGENT}" --device gpu)"
expect "unknown flag fails"           "rc=1" "$(ms overlayfs --image "${MODEL}" --agent-image "${AGENT}" --bogus)"

# ---- H28 probe ------------------------------------------------------------------
PROBE="${ROOT}/bench/zero-copy-models/probe.sh"
if bash -n "${PROBE}"; then ok "probe: parses"; else fail "probe: bash -n"; fi
pt=$(PYIMG="${AGENT}" "${PROBE}" render T 1 1)
pc=$(PYIMG="${AGENT}" "${PROBE}" render C 1 1)
expect "probe T: mmaps in place"      "/m/model/blob-r1" "${pt}"
expect "probe T: no init copy"        "initContainers" "${pt}" negate
expect "probe C: init copy"           "initContainers" "${pc}"
expect "probe C: mmaps the copy"      "/data/blob" "${pc}"
expect "probe: image volume ref"      'reference: "h28-model:r1"' "${pt}"
expect "probe: cluster name"          "k3dgpu-r-zero-copy-models" "$(cat "${PROBE}")"
# shellcheck disable=SC2016 # the literal text of the probe line
expect "probe: rounds killed at HOLD - 120 s" 'timeout -k 15 $(( HOLD - 120 ))' "$(cat "${PROBE}")"
expect "probe A2: load stopped under the lock" 'LOAD[@]}" stop' "$(sed -n "/^locked()/,/^}/p" "${PROBE}")"
expect "probe A2: settle gap"            'settle --baseline' "$(cat "${PROBE}")"
expect "probe A2: during windows"       '--phase during --treatment-cpus' "$(cat "${PROBE}")"
expect "probe A2: node pinned off the load CPUs" 'docker update --cpuset-cpus' "$(cat "${PROBE}")"
expect "probe: delete under the lock"  "k3d cluster delete" "$(sed -n "/^locked()/,/^}/p" "${PROBE}")"
expect "probe: re-check f-* in lock"  "f_cluster" "$(sed -n "/^locked()/,/^}/p" "${PROBE}")"
expect "probe: cold != 0 is VOID"     "VOID-cold" "$(cat "${PROBE}")"
expect "probe: warm-up round"         "round C 0 warmup" "$(cat "${PROBE}")"
expect "probe: waits for f- clusters" "k3dgpu-f-" "$(cat "${PROBE}")"
expect "probe run: needs LOCK"        "LOCK" "$("${PROBE}" run 2>&1)"
expect "probe: unknown verb fails"    "usage" "$("${PROBE}" bogus 2>&1)"

if command -v kubeconform >/dev/null 2>&1; then
    ms overlayfs --image "${MODEL}" --agent-image "${AGENT}" --replicas 8 | sed '/^rc=/d' > "${WORK}/ms.yaml"
    printf '%s\n' "${pt}" > "${WORK}/t.yaml"
    printf '%s\n' "${pc}" > "${WORK}/c.yaml"
    if r=$(kubeconform -strict -summary -kubernetes-version "${K8S_VERSION}" "${WORK}/ms.yaml" "${WORK}/t.yaml" "${WORK}/c.yaml" 2>&1); then
        ok "kubeconform (k8s ${K8S_VERSION}): ${r##*Summary: }"
    else
        fail "kubeconform (k8s ${K8S_VERSION}): ${r}"
    fi
else
    echo "skip kubeconform not on PATH"
fi

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
