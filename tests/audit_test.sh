#!/usr/bin/env bash
# `k3d-gpu audit` and images.lock tests. No cluster, no network.
#
#   - unit and property tests of console/audit.py (tests/console/test_audit.py)
#   - scripts/images-lock check passes on the repo and catches each kind of drift
#     (an image added, a tag bumped, a pinned digest changed, a k3s bump, an entry
#     dropped, a stale entry, a malformed lock) in a scratch copy
#   - `k3d-gpu audit` against a k3d cluster read through stubbed docker and kubectl
#     (tests/console/audit/default: one server node of an `up` + `agent up` layout):
#     the default layout passes; an unauthenticated published port, an image not in
#     images.lock, a moved digest, an image with no digest, a world-readable
#     kubeconfig and Open WebUI without sign-in each fail. Text goldens in
#     tests/console/audit/<case>.golden; JSON output validates as audit/v1
#   - observed/v1 fixtures: a runtime pull and an unauthenticated IPv6 listener fail
#   - every tests/fixtures/*/plan.json, and each fixture's plan with --bind 0.0.0.0,
#     passes as the planned endpoint; the same off-loopback plan in loopback-key mode fails
#   - bad options and bad input fail with a message
#
# Usage: tests/audit_test.sh [--update]   (--update rewrites tests/console/audit/*.golden)

set -uo pipefail

TESTS=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "${TESTS}")
K3D_GPU="${REPO}/scripts/k3d-gpu"
CONSOLE="${REPO}/console"
DIR="${TESTS}/console/audit"
UPDATE=0
[ "${1:-}" = "--update" ] && UPDATE=1
WORK=$(mktemp -d)
# A measured autoquant pick on this dev box (EXPERIMENTS.md H27) must not leak into goldens.
export K3D_GPU_AUTOQUANT_CACHE="${WORK}/no-autoquant.json"
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

ok()   { PASS=$((PASS + 1)); echo "ok   $*"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $*"; }

if python3 "${TESTS}/console/test_audit.py"; then ok "audit unit and property tests"; else fail "audit unit and property tests"; fi

validate() {  # validate <ref> <file>...
    local ref=$1; shift
    python3 - "${CONSOLE}" "${ref}" "$@" <<'EOF'
import json, sys
sys.path.insert(0, sys.argv[1])
import facts, schema_check
root, ref = facts.load_schema(), sys.argv[2]
for f in sys.argv[3:]:
    schema_check.check(json.load(open(f, encoding="utf-8")), root, ref)
EOF
}

# ---- images.lock ----
if out=$("${REPO}/scripts/images-lock" check 2>&1); then ok "images.lock matches the repo"; else fail "images.lock check"; sed 's/^/  | /' <<< "${out}"; fi
validate "#/\$defs/images_lock" "${REPO}/images.lock" && ok "images.lock validates as images-lock/v1" || fail "images.lock schema"

# lock_drift <name> <want substring> <shell edit run inside the scratch copy>
lock_drift() {
    local name=$1 want=$2 edit=$3 root="${WORK}/lockcopy" out rc
    rm -rf "${root}"; mkdir -p "${root}/scripts"
    cp -r "${REPO}/share" "${REPO}/console" "${root}/"
    cp "${REPO}/Dockerfile" "${REPO}/images.lock" "${root}/"
    cp "${REPO}/scripts/k3d-gpu" "${REPO}/scripts/images-lock" "${root}/scripts/"
    (cd "${root}" && eval "${edit}")
    out=$("${root}/scripts/images-lock" check --root "${root}" 2>&1); rc=$?
    if [ "${rc}" -ne 0 ] && grep -qF -- "${want}" <<< "${out}"; then
        ok "images-lock check catches ${name}"
    else
        fail "images-lock check missed ${name} (exit ${rc}, want '${want}')"; sed 's/^/  | /' <<< "${out}"
    fi
}
lock_drift "a new image in share/" "not in images.lock: docker.io/library/redis:7.4 (from share/extra.yml)" \
    "printf 'spec:\n  containers:\n    - image: redis:7.4\n' > share/extra.yml"
lock_drift "a bumped plugin tag" "not in images.lock: docker.io/intel/intel-gpu-plugin:0.38.0" \
    "sed -i 's/intel-gpu-plugin:0.37.1/intel-gpu-plugin:0.38.0/' share/intel-gpu-plugin.yml"
lock_drift "the stale entry a bump leaves" "stale in images.lock: docker.io/intel/intel-gpu-plugin:0.37.1" \
    "sed -i 's/intel-gpu-plugin:0.37.1/intel-gpu-plugin:0.38.0/' share/intel-gpu-plugin.yml share/intel-gpu-wsl-plugin.yml"
lock_drift "a bumped image under share/telemetry" \
    "not in images.lock: quay.io/prometheus/node-exporter:v9.9.9 (from share/telemetry/node-exporter.yml)" \
    "sed -i 's/node-exporter:v[0-9.]*/node-exporter:v9.9.9/' share/telemetry/node-exporter.yml"
lock_drift "a bumped plain image pin in the launcher" \
    "not in images.lock: docker.io/openvino/model_server:2099.1.0-gpu (from scripts/k3d-gpu NPU_UMD_IMAGE)" \
    "sed -i 's|^NPU_UMD_IMAGE=.*|NPU_UMD_IMAGE=\"openvino/model_server:2099.1.0-gpu\"|' scripts/k3d-gpu"
lock_drift "a moved backend digest in the launcher" "vllm/vllm-openai-xpu: the repo pins sha256:$(printf 'e%.0s' {1..64})" \
    "sed -i 's|vllm/vllm-openai-xpu@sha256:[0-9a-f]*|vllm/vllm-openai-xpu@sha256:$(printf 'e%.0s' {1..64})|' scripts/k3d-gpu"
lock_drift "a changed pinned digest" "the repo pins sha256:$(printf 'f%.0s' {1..64})" \
    "sed -i 's/node-feature-discovery:v0.19.0@sha256:[0-9a-f]*/node-feature-discovery:v0.19.0@sha256:$(printf 'f%.0s' {1..64})/' share/node-feature-discovery.yml"
lock_drift "a k3s bump" "images.lock is for k3s v1.37.1-k3s1, the Dockerfile pins v1.38.0-k3s1" \
    "sed -i 's/^ARG K3S_TAG=.*/ARG K3S_TAG=\"v1.38.0-k3s1\"/' Dockerfile"
lock_drift "a bumped test image" "not in images.lock: docker.io/nvidia/cuda:13.5.0-base-ubuntu26.04" \
    "sed -i 's|K3D_GPU_TEST_IMAGE:-nvidia/cuda:[^}]*|K3D_GPU_TEST_IMAGE:-nvidia/cuda:13.5.0-base-ubuntu26.04|' scripts/k3d-gpu"
lock_drift "a dropped entry" "not in images.lock: docker.io/library/ubuntu:26.04 (from Dockerfile)" \
    "python3 -c \"import json; d=json.load(open('images.lock')); d['images']=[e for e in d['images'] if e['name']!='docker.io/library/ubuntu']; open('images.lock','w').write(json.dumps(d))\""
lock_drift "a lock entry with no digest" "schema violations" \
    "python3 -c \"import json; d=json.load(open('images.lock')); d['images'][0]['digest']='latest'; open('images.lock','w').write(json.dumps(d))\""
lock_drift "a wrong source list" "images.lock sources" \
    "python3 -c \"import json; d=json.load(open('images.lock')); d['images'][0]['sources']=['share/other.yml']; open('images.lock','w').write(json.dumps(d))\""
# A CRD schema has `image:` keys whose value is on the next line (`type: string`);
# they name no image, so the next line's word must not be read as one.
lock_drift "an image added next to a CRD schema image: key" \
    "not in images.lock: docker.io/library/redis:7.4 (from share/crd.yml)" \
    "printf 'properties:\n  image:\n    type: string\n  - image: redis:7.4\n' > share/crd.yml"
if out=$(cd "${WORK}/lockcopy" && "${WORK}/lockcopy/scripts/images-lock" check --root "${WORK}/lockcopy" 2>&1 | grep -E 'library/(type|properties)'); then
    fail "images-lock reads a CRD schema's image: key as an image"; sed 's/^/  | /' <<< "${out}"
else
    ok "images-lock skips a CRD schema's image: key"
fi

# ---- the live path, with docker and kubectl stubbed ----
mkdir -p "${WORK}/bin"
cat > "${WORK}/bin/docker" <<'EOF'
#!/bin/sh
# Reads $AUDIT_STUB (a copy of tests/console/audit/default); logs every call.
echo "docker $*" >> "${AUDIT_STUB}/calls"
case "$1" in
    ps) case "$*" in *"label=k3d.cluster=gpu"*) cat "${AUDIT_STUB}/ps.txt" ;; esac ;;
    inspect) for last in "$@"; do :; done; cat "${AUDIT_STUB}/inspect-${last}.json" ;;
    exec) [ "$3 $4 $5 $6" = "crictl images -o json" ] && cat "${AUDIT_STUB}/crictl-$2.json" ;;
    *) exit 1 ;;
esac
EOF
cat > "${WORK}/bin/kubectl" <<'EOF'
#!/bin/sh
echo "kubectl $*" >> "${AUDIT_STUB}/calls"
case "$*" in
    *"--context k3d-gpu "*"get deployments,statefulsets,daemonsets -A -o json"*) cat "${AUDIT_STUB}/workloads.json" ;;
    *"--context k3d-gpu "*"get events -A --field-selector reason=Pulled -o json"*) cat "${AUDIT_STUB}/events.json" ;;
    *) exit 1 ;;
esac
EOF
chmod +x "${WORK}/bin/docker" "${WORK}/bin/kubectl"

# stub <case> [python edit of the stub files, run in the case dir]
stub() {
    local d="${WORK}/stub/$1"
    rm -rf "${d}"; mkdir -p "${d}"; cp "${DIR}"/default/* "${d}/"
    printf 'apiVersion: v1\nkind: Config\n' > "${d}/kubeconfig"; chmod 600 "${d}/kubeconfig"
    [ -z "${2:-}" ] || (cd "${d}" && python3 -c "$2")
    echo "${d}"
}
live() {  # live <stub dir> <audit args...>
    local d=$1; shift
    PATH="${WORK}/bin:${PATH}" AUDIT_STUB="${d}" KUBECONFIG="${d}/kubeconfig" "${K3D_GPU}" audit "$@"
}
golden() {  # golden <case> <command...>
    local name=$1 out rc; shift
    out=$("$@" 2>&1); rc=$?
    out=$(sed "s|${WORK}|<work>|g" <<< "${out}")$'\n'"exit=${rc}"
    if [ "${UPDATE}" -eq 1 ]; then
        printf '%s\n' "${out}" > "${DIR}/${name}.golden"; echo "wrote tests/console/audit/${name}.golden"; return
    fi
    if diff -u "${DIR}/${name}.golden" <(printf '%s\n' "${out}") > "${WORK}/diff"; then
        ok "audit ${name} (${name}.golden)"
    else
        fail "audit ${name} (${name}.golden)"; sed 's/^/  | /' "${WORK}/diff"
    fi
}

PY_IMG='import json
f = "crictl-k3d-gpu-server-0.json"; d = json.load(open(f))
d["images"].append({"id": "sha256:" + "9" * 64, "repoTags": TAGS, "repoDigests": DIGESTS, "pinned": False})
json.dump(d, open(f, "w"))'
img_edit() { local s=${PY_IMG/TAGS/$1}; echo "${s/DIGESTS/$2}"; }
OWUI_DIGEST=$(sed -nE 's/^ +image: ghcr\.io\/open-webui\/open-webui@(sha256:[0-9a-f]{64})$/\1/p' "${REPO}/share/agent-chat-ui.yml")
chat_ui_edit() {  # chat_ui_edit <WEBUI_AUTH value as a Python literal>
    printf '%s\n' "$(img_edit '[]' "[\"ghcr.io/open-webui/open-webui@${OWUI_DIGEST}\"]")" \
        'w = json.load(open("workloads.json"))' \
        "w[\"items\"].append({\"kind\": \"Deployment\", \"metadata\": {\"namespace\": \"k3d-gpu-agent\", \"name\": \"open-webui\"}, \"spec\": {\"template\": {\"spec\": {\"containers\": [{\"name\": \"open-webui\", \"image\": \"ghcr.io/open-webui/open-webui@${OWUI_DIGEST}\", \"env\": [{\"name\": \"WEBUI_AUTH\", \"value\": $1}]}]}}}})" \
        'json.dump(w, open("workloads.json", "w"))'
}

d=$(stub default)
golden default live "${d}"
golden default-json live "${d}" --json
if grep -q "docker exec k3d-gpu-server-0 crictl images -o json" "${d}/calls" && ! grep -q "k3d-gpu-tools" "${d}/calls"; then
    ok "audit reads containerd on the cluster's server node only (not the k3d tools container)"
else
    fail "audit node selection"; sed 's/^/  | /' "${d}/calls"
fi
live "${d}" --json > "${WORK}/default.json" 2>/dev/null
live "${d}" --observe > "${WORK}/default.observed.json" 2>/dev/null
validate "#/\$defs/audit" "${WORK}/default.json" && ok "audit --json validates as audit/v1" || fail "audit --json schema"
validate "#/\$defs/observed" "${WORK}/default.observed.json" && ok "audit --observe validates as observed/v1" || fail "audit --observe schema"
sed "s|${WORK}|<work>|g" "${WORK}/default.observed.json" > "${WORK}/default.observed.cmp"
if [ "${UPDATE}" -eq 1 ]; then cp "${WORK}/default.observed.cmp" "${DIR}/default.observed.json"
elif cmp -s "${WORK}/default.observed.cmp" "${DIR}/default.observed.json"; then ok "audit --observe (default.observed.json)"
else fail "audit --observe differs from default.observed.json"; diff -u "${DIR}/default.observed.json" "${WORK}/default.observed.cmp" | sed 's/^/  | /'; fi

golden unauth-published live "$(stub unauth-published 'import json
f = "inspect-k3d-gpu-server-0.json"; d = json.load(open(f))
d["30080/tcp"] = [{"HostIp": "0.0.0.0", "HostPort": "8000"}]
json.dump(d, open(f, "w"))')"
golden loopback-published live "$(stub loopback-published 'import json
f = "inspect-k3d-gpu-server-0.json"; d = json.load(open(f))
d["30080/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": "8000"}]
json.dump(d, open(f, "w"))')"
golden image-not-in-lock live "$(stub image-not-in-lock "$(img_edit '["docker.io/bitnami/redis:7.4"]' "[\"docker.io/bitnami/redis@sha256:$(printf '1%.0s' {1..64})\"]")")"
golden image-digest-moved live "$(stub image-digest-moved 'import json
f = "crictl-k3d-gpu-server-0.json"; d = json.load(open(f))
for i in d["images"]:
    if i["repoTags"] == ["docker.io/intel/intel-gpu-plugin:0.37.1"]:
        i["repoDigests"] = ["docker.io/intel/intel-gpu-plugin@sha256:" + "2" * 64]
json.dump(d, open(f, "w"))')"
golden image-no-digest live "$(stub image-no-digest "$(img_edit '["docker.io/library/app:dev"]' '[]')")"
d=$(stub kubeconfig-0644); chmod 644 "${d}/kubeconfig"
golden kubeconfig-0644 live "${d}"
golden chat-ui live "$(stub chat-ui "$(chat_ui_edit '"True"')")"
golden chat-ui-noauth live "$(stub chat-ui-noauth "$(chat_ui_edit '"False"')")"
# A kubelet "Successfully pulled image" event: the image came from a registry at
# pod start, not from the node image (an "already present" event passes).
golden runtime-pull live "$(stub runtime-pull 'import json
e = json.load(open("events.json"))
for ns, pod, ref, node in (("default", "gpu-test", "nvidia/cuda:13.4.3-base-ubuntu26.04", "k3d-gpu-server-0"),
                           ("kube-system", "coredns-5f7c9", "rancher/mirrored-coredns-coredns:1.14.7", "k3d-gpu-server-0"),
                           ("kube-system", "coredns-5f7c9", "rancher/mirrored-coredns-coredns:1.14.7", "k3d-gpu-server-0")):
    e["items"].append({"kind": "Event", "reason": "Pulled", "source": {"component": "kubelet", "host": node},
                       "involvedObject": {"kind": "Pod", "namespace": ns, "name": pod},
                       "message": "Successfully pulled image \"%s\" in 4.1s (4.1s including waiting). Image size: 1 bytes." % ref})
json.dump(e, open("events.json", "w"))')"

# observed/v1 fixtures: what the live read cannot produce yet (an in-cluster listener's own auth).
golden runtime-fetch "${K3D_GPU}" audit --observed "${DIR}/runtime-fetch.observed.json"
golden ipv6-unauth "${K3D_GPU}" audit --observed "${DIR}/ipv6-unauth.observed.json"
[ "${UPDATE}" -eq 1 ] && exit 0
validate "#/\$defs/observed" "${DIR}"/*.observed.json && ok "observed fixtures validate as observed/v1" || fail "observed fixtures schema"
"${K3D_GPU}" audit --json --observed "${DIR}/ipv6-unauth.observed.json" > "${WORK}/fail.json" 2>/dev/null
py=${CONSOLE_JSONSCHEMA_PYTHON:-python3}
"${py}" "${TESTS}/console/crosscheck.py" "${REPO}/images.lock" "${DIR}"/*.observed.json "${WORK}/default.json" "${WORK}/fail.json"
case $? in
    0) ok "schema_check agrees with jsonschema on images.lock, observed/v1 and audit/v1" ;;
    77) echo "skip schema_check vs jsonschema (jsonschema not importable by ${py})" ;;
    *) fail "schema_check disagrees with jsonschema" ;;
esac

# The real chat UI manifest's WEBUI_AUTH is what the chat-ui case runs with.
if grep -A1 -- '- name: WEBUI_AUTH' "${REPO}/share/agent-chat-ui.yml" | grep -q 'value: "True"'; then
    ok "share/agent-chat-ui.yml keeps WEBUI_AUTH True (chat-ui case)"
else
    fail "share/agent-chat-ui.yml WEBUI_AUTH is not \"True\""
fi

# ---- every fixture's planned endpoint ----
shopt -s nullglob
n=0 bad=""
for pj in "${REPO}"/tests/fixtures/*/plan.json; do
    fx=$(basename "$(dirname "${pj}")")
    "${K3D_GPU}" audit --observed "${DIR}/default.observed.json" --plan "${pj}" > "${WORK}/o" 2>&1 \
        && grep -q "planned endpoint" "${WORK}/o" || bad+=" ${fx}(loopback)"
    "${K3D_GPU}" plan --json --facts "$(dirname "${pj}")/facts.json" --bind 0.0.0.0 > "${WORK}/p.json" 2>/dev/null || bad+=" ${fx}(plan)"
    "${K3D_GPU}" audit --observed "${DIR}/default.observed.json" --plan "${WORK}/p.json" > "${WORK}/o" 2>&1 || bad+=" ${fx}(0.0.0.0)"
    # The same off-loopback plan in loopback (no login) mode must fail.
    sed -i 's/"auth": "oidc"/"auth": "loopback-key"/' "${WORK}/p.json"
    if "${K3D_GPU}" audit --observed "${DIR}/default.observed.json" --plan "${WORK}/p.json" > "${WORK}/o" 2>&1 \
       || ! grep -q "unauthenticated listener off loopback" "${WORK}/o"; then
        bad+=" ${fx}(loopback-key on 0.0.0.0 passed)"
    fi
    n=$((n + 1))
done
if [ "${n}" -ge 14 ] && [ -z "${bad}" ]; then
    ok "all ${n} fixtures: planned endpoint passes on loopback and with --bind 0.0.0.0 (oidc)"
else
    fail "fixture planned endpoints (${n} fixtures):${bad}"
fi

# ---- the packaged layout finds console/audit.py and images.lock ----
pkg="${WORK}/pkg"
# Run the real PKGBUILD package() into ${pkg}, so the test checks what ships.
mkdir -p "${WORK}/src"; ln -sfn "${REPO}" "${WORK}/src/k3d-gpu"
( set -e; source "${REPO}/packaging/aur/PKGBUILD"; srcdir="${WORK}/src" pkgdir="${pkg}"; package ) > "${WORK}/pkgbuild.log" 2>&1
if K3D_GPU_SHARE="${pkg}/usr/share/k3d-gpu" "${pkg}/usr/bin/k3d-gpu" audit --observed "${DIR}/default.observed.json" > "${WORK}/pkg.out" 2>&1; then
    ok "packaged layout (PKGBUILD's console files and images.lock under \$K3D_GPU_SHARE) audits"
else
    fail "packaged layout audits"; sed 's/^/  | /' "${WORK}/pkg.out" "${WORK}/pkgbuild.log"
fi

# ---- bad input ----
expect_fail() {  # expect_fail <message substring> <args...>
    local want=$1 out; shift
    if out=$(PATH="${WORK}/bin:${PATH}" AUDIT_STUB="${WORK}/stub/empty" "${K3D_GPU}" "$@" 2>&1 < /dev/null); then
        fail "$* succeeded"
    elif grep -qF -- "${want}" <<< "${out}"; then
        ok "$* fails: ${want}"
    else
        fail "$* failed without '${want}'"; sed 's/^/  | /' <<< "${out}"
    fi
}
mkdir -p "${WORK}/stub/empty"; : > "${WORK}/stub/empty/ps.txt"
expect_fail "unknown audit option" audit --frobnicate
expect_fail "audit: --lock needs a value" audit --lock
expect_fail "no running node of k3d cluster 'gpu'" audit
expect_fail "does not exist" audit --observed "${WORK}/missing.json"
expect_fail "images.lock not found" audit --observed "${DIR}/default.observed.json" --lock "${WORK}/missing.lock"
expect_fail "plan file" audit --observed "${DIR}/default.observed.json" --plan "${WORK}/missing.json"
echo '{"schema": "k3d-gpu.observed/v1"}' > "${WORK}/bad.json"
expect_fail "schema violations" audit --observed "${WORK}/bad.json"
echo 'not json' > "${WORK}/junk.json"
expect_fail "audit:" audit --observed "${WORK}/junk.json"
expect_fail "schema violations" audit --observed "${DIR}/default.observed.json" --lock "${WORK}/bad.json"
rc=0; "${K3D_GPU}" audit --observed "${WORK}/bad.json" > /dev/null 2>&1 || rc=$?
[ "${rc}" -eq 2 ] && ok "bad input exits 2 (fail exits 1)" || fail "bad input exit ${rc}, want 2"

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
