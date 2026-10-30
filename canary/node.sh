#!/usr/bin/env bash
# The node canary: build the node image from the root Dockerfile (Ubuntu 26.04, the
# default variant), start a k3d cluster on it with the k3d release packaging/install.sh
# pins (sha256-checked) and the arguments the launcher's cluster create passes on a host
# without NVIDIA (NODE_ARGS), and within CANARY_NODE_TIMEOUT seconds (default 300) see:
#   - the node Ready;
#   - every kube-system pod Running with its containers ready, or Succeeded (a device
#     plugin's pod may be Pending: a hosted runner has no devices);
#   - every manifest the Dockerfile bakes into /var/lib/rancher/k3s/server/manifests;
#   - every DaemonSet those baked manifests define (the device plugins, NFD).
# Then delete the cluster. kubectl runs inside the node (k3s ships it), so nothing but
# docker and curl is needed on the host.
#
# Writes ${CANARY_OUT_DIR:-./canary-out}/node.json (kind node; pinned_ref the k3s pin)
# and node.log, and exits non-zero unless the verdict is pass.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
OUT_DIR=${CANARY_OUT_DIR:-${PWD}/canary-out}
TIMEOUT=${CANARY_NODE_TIMEOUT:-300}
CLUSTER=canary
NODE=k3d-${CLUSTER}-server-0
IMAGE_TAG=k3d-gpu-canary:node
# scripts/k3d-gpu's cluster create on a host without NVIDIA, as a hosted runner is: no
# load balancer, no traefik, and the node labelled nvidia.com/gpu.present=false, which
# keeps the baked NVIDIA plugin off it (with no driver it crash-loops). Both Intel stacks
# stay enabled, so every baked manifest is applied. tests/canary_spec_test.sh checks that
# the launcher still passes each of these.
NODE_ARGS=(--no-lb "--k3s-arg=--disable=traefik@server:0" "--k3s-node-label=nvidia.com/gpu.present=false@server:*;agent:*")
mkdir -p "${OUT_DIR}"
LOG=${OUT_DIR}/node.log
WORK=$(mktemp -d "${TMPDIR:-/tmp}/canary-node.XXXXXX")
: > "${LOG}"
K3D=""
cleanup() {
    [ -z "${K3D}" ] || "${K3D}" cluster delete "${CLUSTER}" >> "${LOG}" 2>&1
    rm -rf "${WORK}"
}
trap cleanup EXIT
say() { echo "canary node: $*" >&2; }

R_CPU=""
cpuflags=" $(grep -m1 '^flags' "${CANARY_CPUINFO:-/proc/cpuinfo}" | cut -d: -f2) "
for f in avx2 avx512f amx_tile; do [[ "${cpuflags}" == *" ${f} "* ]] && R_CPU+="${f} "; done
REF=$(python3 "${HERE}/plan.py" ref node 2>> "${LOG}")

finish() { # finish <pass|fail> <detail>
    R_VERDICT=$1 R_DETAIL=$2 R_REF=${REF} R_CPU=${R_CPU} python3 - "${OUT_DIR}/node.json" <<'PY'
import json, os, sys
e = os.environ
ok = e["R_VERDICT"] == "pass"
res = {"kind": "node", "pinned_ref": e["R_REF"], "verdict": e["R_VERDICT"], "hard": ok, "exact": ok,
       "cpu_flags": e["R_CPU"].split(), "detail": e["R_DETAIL"]}
with open(sys.argv[1], "w") as f:
    json.dump(res, f, indent=1)
    f.write("\n")
print("canary node: %s: %s" % (e["R_VERDICT"], e["R_DETAIL"]))
PY
    [ "$1" = pass ]
    exit
}
[ -n "${REF}" ] || finish fail "plan: the node's k3s pin does not resolve (see node.log)"

# The node image, as the release job builds its default variant.
start=${SECONDS}
say "docker buildx build --load -t ${IMAGE_TAG} ."
docker buildx build --load -t "${IMAGE_TAG}" "${ROOT}" >> "${LOG}" 2>&1 \
    || finish fail "the node image did not build: $(grep -E 'ERROR|error:' "${LOG}" | tail -3 | tr '\n' ' ')"
built=$((SECONDS - start))

# k3d at the version and sha256 packaging/install.sh pins.
ver=$(sed -n 's/^K3D_VERSION=//p' "${ROOT}/packaging/install.sh")
sha=$(sed -n 's/^K3D_SHA256_AMD64=//p' "${ROOT}/packaging/install.sh")
[[ "${ver}" =~ ^v[0-9.]+$ && "${sha}" =~ ^[0-9a-f]{64}$ ]] || finish fail "packaging/install.sh pins no K3D_VERSION and K3D_SHA256_AMD64"
K3D=${WORK}/k3d
curl -fsSL --retry 3 -o "${K3D}" "https://github.com/k3d-io/k3d/releases/download/${ver}/k3d-linux-amd64" 2>> "${LOG}" \
    || { K3D=""; finish fail "k3d ${ver} did not download"; }
echo "${sha}  ${K3D}" | sha256sum -c - >> "${LOG}" 2>&1 || { K3D=""; finish fail "k3d ${ver}: sha256 mismatch"; }
chmod +x "${K3D}"

start=${SECONDS}
say "k3d ${ver} cluster create ${CLUSTER} --image ${IMAGE_TAG} --wait ${NODE_ARGS[*]}"
"${K3D}" cluster create "${CLUSTER}" --image "${IMAGE_TAG}" --wait --timeout "${TIMEOUT}s" "${NODE_ARGS[@]}" \
    --kubeconfig-update-default=false >> "${LOG}" 2>&1 \
    || finish fail "k3d cluster create failed: $(tail -3 "${LOG}" | tr '\n' ' ')"
created=$((SECONDS - start))

kc() { docker exec "${NODE}" kubectl "$@"; }
# The manifests the Dockerfile bakes, and the DaemonSets they define (namespace/name).
manifests=$(sed -nE 's#^COPY share/[^ ]+ /var/lib/rancher/k3s/server/manifests/([^ ]+)$#\1#p' "${ROOT}/Dockerfile" | sort)
daemonsets=$(sed -nE 's#^COPY (share/[^ ]+) /var/lib/rancher/k3s/server/manifests/[^ ]+$#\1#p' "${ROOT}/Dockerfile" \
    | (cd "${ROOT}" && xargs python3 -c '
import re, sys
for path in sys.argv[1:]:
    for doc in re.split(r"^---\s*$", open(path).read(), flags=re.M):
        if re.search(r"^kind: DaemonSet\s*$", doc, re.M):
            meta = re.search(r"^metadata:\n((?:[ ]+.*\n)+)", doc, re.M).group(1)
            name = re.search(r"^  name: (\S+)", meta, re.M).group(1)
            ns = re.search(r"^  namespace: (\S+)", meta, re.M)
            print("%s/%s" % (ns.group(1) if ns else "default", name))
') | sort -u)

problems=""
deadline=$((SECONDS + TIMEOUT))
while :; do
    problems=""
    ready=$(kc get nodes -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' 2>> "${LOG}")
    [ "${ready}" = True ] || problems+="node not Ready (${ready:-no node}); "
    have=$(docker exec "${NODE}" ls /var/lib/rancher/k3s/server/manifests 2>> "${LOG}")
    for m in ${manifests}; do
        grep -qx "${m}" <<< "${have}" || problems+="manifest ${m} not baked; "
    done
    ds=$(kc get ds -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}' 2>> "${LOG}")
    for d in ${daemonsets}; do
        grep -qx "${d}" <<< "${ds}" || problems+="DaemonSet ${d} missing; "
    done
    # kube-system pods: Running with every container ready, or Succeeded; a pod a baked
    # DaemonSet owns may also be Pending.
    pods=$(kc get pods -n kube-system -o jsonpath='{range .items[*]}{.metadata.name} {.status.phase} {.metadata.ownerReferences[0].kind}/{.metadata.ownerReferences[0].name} {range .status.containerStatuses[*]}{.ready},{end}{"\n"}{end}' 2>> "${LOG}")
    [ -n "${pods}" ] || problems+="no kube-system pods; "
    while read -r name phase owner readies; do
        [ -n "${name}" ] || continue
        case "${phase}" in
            Succeeded) continue ;;
            Running) [[ "${readies}" != *false* ]] && continue ;;
            Pending) [ "${owner%%/*}" = DaemonSet ] && grep -qx "kube-system/${owner#*/}" <<< "${daemonsets}" && continue ;;
        esac
        problems+="pod ${name} ${phase}${readies:+ ready=${readies%,}}; "
    done <<< "${pods}"
    [ -n "${problems}" ] || break
    [ "${SECONDS}" -lt "${deadline}" ] || break
    sleep 5
done
waited=$((SECONDS - start))
{ kc get nodes -o wide; kc get pods -A -o wide; kc get ds -A; } >> "${LOG}" 2>&1
if [ -n "${problems}" ]; then
    finish fail "after ${waited}s: ${problems%; }"
fi
finish pass "image built in ${built}s, cluster created in ${created}s, ready in ${waited}s; $(wc -w <<< "${manifests}") baked manifests, $(wc -w <<< "${daemonsets}") DaemonSets, every kube-system pod up"
