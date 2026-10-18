#!/usr/bin/env bash
# Every image reference in the repo is pinned by digest. No network.
#
#   - `scripts/images-lock pins` passes on the repo
#   - in a scratch copy it fails on each kind of unpinned reference (tag only, bare
#     name, :latest even with a digest, a ${FOO_IMAGE:-default}, a Dockerfile FROM
#     whose ARG default is unpinned), on an allow line that matches nothing and on
#     an allow line with no reason; it passes on variables, digest-only refs, a CRD
#     schema `image:` key, prose (*.md, docs/) and an allowed reference
#   - `repin` (registry stubbed) appends the digest to a name:tag, moves a stale
#     digest, leaves allowed and digest-only refs alone, and is idempotent
#
# Usage: tests/image_pins_test.sh

set -uo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $*"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $*"; }

D1="sha256:$(printf '1%.0s' {1..64})"
D2="sha256:$(printf '2%.0s' {1..64})"

if out=$("${REPO}/scripts/images-lock" pins 2>&1); then ok "every image reference in the repo is pinned or allowed"
else fail "images-lock pins on the repo"; sed 's/^/  | /' <<< "${out}"; fi

# scratch: a root holding the images-lock tool and an allow file with one entry.
scratch() {
    rm -rf "${WORK}/r"; mkdir -p "${WORK}/r/scripts" "${WORK}/r/share"
    cp "${REPO}/scripts/images-lock" "${WORK}/r/scripts/"
    cp -r "${REPO}/console" "${WORK}/r/"
    printf 'share/local.yml app/local:dev built on the host, never pulled\n' > "${WORK}/r/images.unpinned"
    printf -- '- image: app/local:dev\n' > "${WORK}/r/share/local.yml"
}

# pins_case <name> <want: ok or a FAIL substring> <shell edit run inside the scratch root>
pins_case() {
    local name=$1 want=$2 edit=$3 out rc
    scratch
    (cd "${WORK}/r" && eval "${edit}")
    out=$("${WORK}/r/scripts/images-lock" pins --root "${WORK}/r" 2>&1); rc=$?
    if [ "${want}" = ok ]; then
        if [ "${rc}" -eq 0 ]; then ok "pins passes ${name}"; else fail "pins rejects ${name}"; sed 's/^/  | /' <<< "${out}"; fi
    elif [ "${rc}" -ne 0 ] && grep -qF -- "${want}" <<< "${out}"; then
        ok "pins catches ${name}"
    else
        fail "pins missed ${name} (exit ${rc}, want '${want}')"; sed 's/^/  | /' <<< "${out}"
    fi
}

pins_case "the allowed reference alone" ok ":"
pins_case "a tag-only image: line" "share/a.yml:2: redis:7.4 is not pinned by digest" \
    "printf 'spec:\n  - image: redis:7.4\n' > share/a.yml"
pins_case "a quoted tag-only \"image\" JSON key" "bench.json:1: ollama/ollama:0.40.5 is not pinned" \
    "printf '{\"image\": \"ollama/ollama:0.40.5\"}\n' > bench.json"
pins_case "a bare name with no tag" "docker.io/library/redis is not pinned" \
    "printf -- '- image: docker.io/library/redis\n' > share/a.yml"
pins_case ":latest even with a digest" "archlinux:latest@${D1} is tagged latest" \
    "mkdir -p .github/workflows; printf 'jobs:\n  a:\n    container: archlinux:latest@${D1}\n' > .github/workflows/x.yml"
pins_case "a \${FOO_IMAGE:-default}" 'scripts/run:1: busybox:1.37 is not pinned' \
    "printf 'IMG=\"\${K3D_GPU_TEST_IMAGE:-busybox:1.37}\"\n' > scripts/run"
pins_case "a plain FOO_IMAGE= assignment" 'scripts/run:1: openvino/model_server:2026.4.1-gpu is not pinned' \
    "printf 'NPU_UMD_IMAGE=\"openvino/model_server:2026.4.1-gpu\"\n' > scripts/run"
pins_case "a FROM line" "Dockerfile:1: debian:trixie-slim is not pinned" \
    "printf 'FROM debian:trixie-slim AS build\n' > Dockerfile"
pins_case "a FROM whose ARG default is unpinned" "Dockerfile:2: ubuntu:26.04 is not pinned" \
    "printf 'ARG UBUNTU_TAG=26.04\nFROM ubuntu:\${UBUNTU_TAG}\n' > Dockerfile"
pins_case "a docker:// action" ".github/workflows/x.yml:2: alpine:3.22 is not pinned" \
    "mkdir -p .github/workflows; printf 'steps:\n  - uses: docker://alpine:3.22\n' > .github/workflows/x.yml"
pins_case "the allowed reference in another file" "share/b.yml:1: app/local:dev is not pinned" \
    "printf -- '- image: app/local:dev\n' > share/b.yml"
pins_case "an allow line that matches nothing" "'share/gone.yml x/y:1' matches no unpinned reference" \
    "printf 'share/gone.yml x/y:1 was removed\n' >> images.unpinned"
pins_case "an allow line with no reason" "want '<path glob> <ref or *> <reason>'" \
    "printf 'share/local.yml app/local:dev\n' >> images.unpinned"
pins_case "name:tag@sha256 references" ok \
    "printf -- '- image: redis:7.4@${D1}\nFROM debian:trixie-slim@${D2}\n' > share/a.yml"
pins_case "a FROM whose ARG default is pinned" ok \
    "printf 'ARG UBUNTU_IMAGE=\"ubuntu:26.04@${D1}\"\nFROM \${UBUNTU_IMAGE}\n' > Dockerfile"
pins_case "digest-only references" ok \
    "printf 'AGENT_OVMS_IMAGE=\"openvino/model_server@${D1}\"\n' > scripts/run"
pins_case "variables" ok \
    "printf -- '- image: \${AGENT_IMAGE}\n- image: \"@VLLM_IMAGE@\"\n- image: {image}\n' > share/a.yml"
pins_case "a CRD schema image: key" ok \
    "printf 'properties:\n  image:\n    type: string\n' > share/a.yml"
pins_case "prose (*.md, docs/)" ok \
    "mkdir -p docs; printf 'docker run busybox:1.37\nimage: redis:7.4\n' | tee README.md > docs/x.txt"

# ---- repin, with the registry stubbed ----
scratch
cat > "${WORK}/r/share/a.yml" <<EOF
- image: redis:7.4
- image: quay.io/x/y:v1@${D1}
- image: openvino/model_server@${D1}
EOF
printf 'TEST_IMAGE="${K3D_GPU_TEST_IMAGE:-nvidia/cuda:13.4.3-base-ubuntu26.04}"\n' > "${WORK}/r/scripts/run"
chmod 755 "${WORK}/r/scripts/run"
repin() {
    python3 - "${WORK}/r" "${D2}" <<'EOF'
import importlib.machinery, importlib.util, sys
root, digest = sys.argv[1], sys.argv[2]
loader = importlib.machinery.SourceFileLoader("images_lock", root + "/scripts/images-lock")
spec = importlib.util.spec_from_loader("images_lock", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
m.registry_digest = lambda name, tag: digest
for c in m.repin(root, []):
    print(c)
EOF
}
out=$(repin 2>&1)
want="- image: redis:7.4@${D2}
- image: quay.io/x/y:v1@${D2}
- image: openvino/model_server@${D1}"
if [ "$(cat "${WORK}/r/share/a.yml")" = "${want}" ]; then ok "repin pins a tag, moves a stale digest, keeps a digest-only ref"
else fail "repin share/a.yml"; sed 's/^/  | /' "${WORK}/r/share/a.yml" <<< "${out}"; fi
if grep -qxF "TEST_IMAGE=\"\${K3D_GPU_TEST_IMAGE:-nvidia/cuda:13.4.3-base-ubuntu26.04@${D2}}\"" "${WORK}/r/scripts/run"; then
    ok "repin pins a \${FOO_IMAGE:-default}"
else fail "repin \${FOO_IMAGE:-default}"; sed 's/^/  | /' "${WORK}/r/scripts/run"; fi
if [ "$(cat "${WORK}/r/share/local.yml")" = "- image: app/local:dev" ]; then ok "repin leaves an allowed reference alone"
else fail "repin touched an allowed reference"; fi
if [ -x "${WORK}/r/scripts/run" ]; then ok "repin keeps a rewritten script executable"; else fail "repin dropped the executable bit"; fi
if out=$(repin 2>&1) && [ -z "${out}" ]; then ok "repin is idempotent"; else fail "repin changed a pinned tree again"; sed 's/^/  | /' <<< "${out}"; fi

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
