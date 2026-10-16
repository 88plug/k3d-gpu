#!/usr/bin/env bash
# Static checks of the Open WebUI chat add-on manifest (share/agent-chat-ui.yml).
# No cluster: the manifest is read as text and, when kubeconform is on PATH,
# validated against the Kubernetes schemas of the k3s version the node image ships.
#
# Usage: tests/chat_ui_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="${CHAT_UI_MANIFEST:-${ROOT}/share/agent-chat-ui.yml}"
K8S_VERSION=$(sed -n 's/^ARG K3S_TAG="v\([0-9.]*\)-k3s.*/\1/p' "${ROOT}/Dockerfile")
PASS=0 FAILS=0

ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

# has <name> <ERE>: some manifest line matches.
has() { if grep -Eq -- "$2" "${MANIFEST}"; then ok "$1"; else fail "$1 (no line matches: $2)"; fi; }
# lacks <name> <ERE>: no manifest line matches.
lacks() { if grep -Eq -- "$2" "${MANIFEST}"; then fail "$1 (found: $(grep -E -- "$2" "${MANIFEST}" | head -1))"; else ok "$1"; fi; }

# env_value <NAME>: the literal value of a plain env entry, quotes removed.
env_value() {
    awk -v n="$1" '
        $0 ~ "- name: " n "$" { want = 1; next }
        want && /value:/ { sub(/.*value: */, ""); gsub(/"/, ""); print; exit }
        want && /- name:/ { exit }' "${MANIFEST}"
}

# expect_env <NAME> <value>
expect_env() {
    local got
    got=$(env_value "$1")
    if [ "${got}" = "$2" ]; then ok "env $1=$2"; else fail "env $1 is '${got}', want '$2'"; fi
}

[ -f "${MANIFEST}" ] || { echo "FAIL manifest missing: ${MANIFEST}"; exit 1; }

# Image pinned by digest, never by a moving tag.
has   "image pinned by digest" '^ +image: ghcr\.io/open-webui/open-webui@sha256:[0-9a-f]{64}$'
lacks "no tag-only image"      '^ +image: [^@]+:[^@]+$'
# The exact index digest of ghcr.io/open-webui/open-webui:v0.12.2, and the
# version the header comment names. A bump changes all three together.
OWUI_VERSION=v0.12.2
OWUI_DIGEST=sha256:10e6a78ba2d172493460a9e8a92c496aaa7a64dc4d26d622a2e95e0c736081c9
has "image is ${OWUI_VERSION} digest" "^ +image: ghcr\.io/open-webui/open-webui@${OWUI_DIGEST}$"
has "header names ${OWUI_VERSION}"    "^# Source: https://github\.com/open-webui/open-webui ${OWUI_VERSION//./\\.}\."

# E-core placement through the NRI balloons label, sized by a CPU request.
has "pod label cores=efficient" '^ +k3d-gpu\.io/cores: efficient$'
has "cpu request sizes balloon" '^ +cpu: "1"'

# The agent connection: OVMS's OpenAI API lives under /v3.
expect_env OPENAI_API_BASE_URL "http://ovms.k3d-gpu-agent.svc:8000/v3"
expect_env ENABLE_OPENAI_API True
expect_env ENABLE_OLLAMA_API False
lacks "no /v1 OpenAI path" 'value: .*/v1$'

# Auth on by default; no secret material in the manifest.
expect_env WEBUI_AUTH True
expect_env DEFAULT_USER_ROLE pending
expect_env WEBUI_SECRET_KEY_FILE /data/.webui_secret_key
lacks "no inline WEBUI_SECRET_KEY" '- name: WEBUI_SECRET_KEY$'
expect_env WEBUI_ADMIN_PASSWORD ""   # no literal value: Secret only
has   "admin password from Secret" 'secretKeyRef: \{name: open-webui-admin, key: password, optional: true\}'
lacks "no kind: Secret"            '^kind: Secret$'

# Reachable only from inside the cluster or a port-forward.
has   "Service is ClusterIP"   '^  type: ClusterIP$'
lacks "no NodePort/LB/Ingress" '(NodePort|LoadBalancer|^kind: Ingress|hostPort|hostNetwork)'

# Nothing leaves the cluster: no HF downloads or update checks.
expect_env OFFLINE_MODE True
expect_env ENABLE_COMMUNITY_SHARING False

# Persistence: local-path PVC holds DATA_DIR.
expect_env DATA_DIR /data
has "PVC on local-path"     '^  storageClassName: local-path$'
has "PVC mounted at /data"  '^ +mountPath: /data$'
has "Recreate strategy"     '^ +type: Recreate'

if command -v kubeconform >/dev/null 2>&1; then
    if out=$(kubeconform -strict -summary -kubernetes-version "${K8S_VERSION}" "${MANIFEST}" 2>&1); then
        ok "kubeconform (k8s ${K8S_VERSION}): ${out##*Summary: }"
    else
        fail "kubeconform (k8s ${K8S_VERSION}): ${out}"
    fi
else
    echo "skip kubeconform not on PATH"
fi

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
