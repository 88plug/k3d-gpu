#!/usr/bin/env bash
# canary/node.sh against a docker, k3d and curl shim: the cluster's pods come from
# scenario files, one per `kubectl get pods` poll (the last repeats), so each case reads
# the verdict node.sh writes for a cluster whose pods behave a known way. No image is
# built, no cluster is started, nothing is downloaded.
#
# Usage: tests/canary_node_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T=$(mktemp -d "${TMPDIR:-/tmp}/canary_node_test.XXXXXX")
trap 'rm -rf "$T"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

# A tree node.sh reads: the canary, the Dockerfile and the manifests it bakes, and an
# install.sh whose k3d sha256 is the shim's.
R=${T}/root
mkdir -p "${R}/packaging" "${T}/bin" "${T}/state"
cp -r "${ROOT}/canary" "${ROOT}/share" "${ROOT}/Dockerfile" "${R}/"
printf '#!/bin/sh\necho "k3d $*" >> "${STUB_STATE}/calls"\ncase "$1 $2" in "cluster create") exit "${STUB_CREATE_RC:-0}" ;; esac\nexit 0\n' \
    > "${T}/k3d.src"
printf 'K3D_VERSION=v5.9.0\nK3D_SHA256_AMD64=%s\n' "$(sha256sum "${T}/k3d.src" | cut -d' ' -f1)" > "${R}/packaging/install.sh"

# What the node holds when everything applied: the baked manifests and their
# DaemonSets, read here with a YAML parser (node.sh reads them its own way).
grep -oE '/var/lib/rancher/k3s/server/manifests/[^ ]+$' "${ROOT}/Dockerfile" | xargs -n1 basename > "${T}/manifests"
sed -nE 's#^COPY (share/[^ ]+) /var/lib/rancher/k3s/server/manifests/.*#\1#p' "${ROOT}/Dockerfile" \
    | (cd "${ROOT}" && xargs python3 -c '
import sys, yaml
for p in sys.argv[1:]:
    for d in yaml.safe_load_all(open(p)):
        if d and d.get("kind") == "DaemonSet":
            print("%s/%s" % (d["metadata"].get("namespace", "default"), d["metadata"]["name"]))
') | sort -u > "${T}/daemonsets"
[ -s "${T}/daemonsets" ] && grep -qx 'node-feature-discovery/nfd-worker' "${T}/daemonsets" \
    && ok "fixture: the baked manifests define $(wc -l < "${T}/daemonsets") DaemonSets, NFD's among them" \
    || fail "fixture: no DaemonSets read from the baked manifests"

cat > "${T}/bin/curl" <<'SHIM'
#!/usr/bin/env bash
out=""
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2 ;; *) shift ;; esac; done
cp "${STUB_K3D_SRC:?}" "${out:?}"
SHIM
cat > "${T}/bin/docker" <<'SHIM'
#!/usr/bin/env bash
S=${STUB_STATE:?}
echo "docker $*" >> "${S}/calls"
case "$1" in
    buildx) exit "${STUB_BUILD_RC:-0}" ;;
    exec) shift 2 ;;
    *) exit 0 ;;
esac
case "$*" in
    "ls /var/lib/rancher/k3s/server/manifests") cat "${STUB_MANIFESTS:?}" ;;
    "kubectl get nodes -o jsonpath="*) echo True ;;
    "kubectl get ds -A -o jsonpath="*) grep -vxF "${STUB_DS_DROP:-none}" "${STUB_DAEMONSETS:?}" ;;
    "kubectl get pods -A -o jsonpath="*)
        n=$(( $(cat "${S}/polls" 2>/dev/null || echo 0) + 1 ))
        echo "${n}" > "${S}/polls"
        f=$(ls "${STUB_PODS:?}" | sort -n | awk -v n="${n}" '$1 <= n' | tail -1)
        cat "${STUB_PODS}/${f}" ;;
    "kubectl get pods"*) echo "kubectl get pods: node.sh asks for all namespaces with -A" >&2; exit 1 ;;
esac
exit 0
SHIM
chmod +x "${T}/bin/curl" "${T}/bin/docker"
export PATH="${T}/bin:${PATH}" STUB_STATE="${T}/state" STUB_K3D_SRC="${T}/k3d.src"
export STUB_MANIFESTS="${T}/manifests" STUB_DAEMONSETS="${T}/daemonsets"
export CANARY_NODE_TIMEOUT=4 CANARY_NODE_SETTLE=2 CANARY_NODE_POLL=0.2

UP=$'kube-system/coredns-1 Running coredns=true:0,\nkube-system/helm-install-x Succeeded helm=false:0,\nkube-system/intel-gpu-plugin-a Running intel-gpu-plugin=true:0,\nnode-feature-discovery/nfd-master-1 Running nfd-master=true:0,\nnode-feature-discovery/nfd-worker-a Running nfd-worker=true:0,'

# node_case <label> <pod states per poll...> [-- VAR=value...]: node.sh in a fresh state;
# leaves its node.json in RESULT and its exit status in RC.
node_case() {
    local label=$1 i=0
    shift
    rm -rf "${T}/state" "${T}/out" "${T}/pods"
    mkdir -p "${T}/state" "${T}/pods"
    while [ $# -gt 0 ] && [ "$1" != -- ]; do
        i=$((i + 1))
        printf '%s\n' "$1" > "${T}/pods/${i}"
        shift
    done
    [ "${1:-}" != -- ] || shift
    START=${SECONDS}
    env "$@" STUB_PODS="${T}/pods" CANARY_OUT_DIR="${T}/out" "${R}/canary/node.sh" > "${T}/${label}.log" 2>&1
    RC=$?
    ELAPSED=$((SECONDS - START))
    RESULT=${T}/out/node.json
    [ -f "${RESULT}" ] || { fail "${label}: no node.json (rc=${RC}); log:"; sed 's/^/    /' "${T}/${label}.log" | tail -20; return 1; }
}
field() { python3 -c 'import json, sys; v = json.load(open(sys.argv[1]))[sys.argv[2]]; print(json.dumps(v) if not isinstance(v, str) else v)' "${RESULT}" "$1"; }
expect() { # expect <label> <field> <value>
    local got
    got=$(field "$2")
    if [ "${got}" = "$3" ]; then ok "$1: $2=$3"; else fail "$1: $2=${got}, want $3 ($(field detail))"; fi
}
detail_has() { # detail_has <label> <text>
    grep -qF -- "$2" <(field detail) && ok "$1: detail names $2" || fail "$1: detail is '$(field detail)'"
}

# 1. Everything up and steady: pass, hard and exact.
node_case up "${UP}"
expect up verdict pass
expect up hard true
expect up kind node
[ "${RC}" -eq 0 ] && ok "up: node.sh exits 0" || fail "up: node.sh exited ${RC}"
grep -q 'k3d cluster create canary .*--no-lb' "${T}/state/calls" && ok "up: the cluster is created with NODE_ARGS" \
    || fail "up: no k3d cluster create with --no-lb"

# 2. A pod outside kube-system that crash-loops (NFD after a bad bump) fails.
node_case nfd "${UP/nfd-master=true:0/nfd-master=false:4}"
expect nfd verdict fail
detail_has nfd node-feature-discovery/nfd-master-1

# 3. A baked DaemonSet's pod that never starts (its image does not pull) fails.
node_case pull "${UP/intel-gpu-plugin-a Running intel-gpu-plugin=true:0/intel-gpu-plugin-a Pending intel-gpu-plugin=false:0}"
expect pull verdict fail
detail_has pull kube-system/intel-gpu-plugin-a
[ "${ELAPSED}" -le 15 ] && ok "pull: gave up at the timeout (${ELAPSED}s)" || fail "pull: took ${ELAPSED}s"

# 4. A pod that comes up late passes once it has held.
node_case late "${UP/intel-gpu-plugin-a Running intel-gpu-plugin=true:0/intel-gpu-plugin-a Pending intel-gpu-plugin=false:0}" "${UP}"
expect late verdict pass

# 5. A container that is ready for a moment and then crashes, again and again, fails:
# it never holds without a restart.
flap=()
for r in $(seq 60); do
    flap+=("${UP/nfd-master=true:0/nfd-master=true:${r}}")
done
node_case flap "${flap[@]}"
expect flap verdict fail
detail_has flap "did not hold"

# 6. One restart while the cluster settles, then steady: pass.
node_case restart-once "${UP}" "${UP/coredns=true:0/coredns=true:1}"
expect restart-once verdict pass

# 7. A DaemonSet a baked manifest defines that the cluster lacks fails.
node_case nods "${UP}" -- STUB_DS_DROP=node-feature-discovery/nfd-worker
expect nods verdict fail
detail_has nods "DaemonSet node-feature-discovery/nfd-worker missing"

# 8. No pods at all fails.
node_case nopods ""
expect nopods verdict fail

# 9. A failed cluster create fails, naming it.
node_case create "${UP}" -- STUB_CREATE_RC=1
expect create verdict fail
detail_has create "k3d cluster create failed"

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
