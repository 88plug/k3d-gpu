#!/usr/bin/env bash
# Every image reference in the repo is pinned by digest. No network.
#
#   - `scripts/images-lock pins` passes on the repo
#   - in a scratch copy it fails on each kind of unpinned reference (tag only, bare
#     name, :latest even with a digest, a ${FOO_IMAGE:-default}, a Dockerfile FROM
#     whose ARG default is unpinned), on an allow line that matches nothing and on
#     an allow line with no reason; it passes on variables, digest-only refs, a CRD
#     schema `image:` key, prose (*.md, docs/) and an allowed reference
#   - a Dockerfile's apt install of an NVIDIA package (nvidia-*, libnvidia-*) without
#     =version, or with an ARG whose default is empty, fails pins; across continuation
#     lines, in *.Dockerfile too
#   - Helm values name an image as sibling `repository:` / `tag:` keys: pins fails
#     on such a pair with no digest, with no tag, or tagged latest
#   - images.lock collects the values pairs and Dockerfile FROMs under integrations/eat,
#     and the Dockerfile's *_IMAGE ARG defaults (the 24.04 variant's base)
#   - `repin` (registry stubbed) appends the digest to a name:tag, moves a stale
#     digest, leaves allowed and digest-only refs alone, does the same for a values
#     tag, and is idempotent; `repin --exclude GLOB` leaves matching files alone
#   - no workflow file pins name:tag@sha256: the nightly repins with
#     --exclude '.github/workflows/*' (its token cannot push workflow files)
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
    rm -rf "${WORK}/r"; mkdir -p "${WORK}/r/scripts" "${WORK}/r/share" "${WORK}/r/hack" "${WORK}/r/tests"
    cp "${REPO}/scripts/images-lock" "${WORK}/r/scripts/"
    cp "${REPO}/hack/pin-classes" "${WORK}/r/hack/"
    cp "${REPO}/tests/ledger_drift_check.py" "${WORK}/r/tests/"
    cp -r "${REPO}/console" "${WORK}/r/"
    printf 'share/local.yml app/local:dev built on the host, never pulled\n' > "${WORK}/r/images.unpinned"
    mkdir -p "${WORK}/r/pins"
    printf 'console/matrix.v1.json historical the measured cells (digest-only records)\n' > "${WORK}/r/pins/classes"
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
pins_case "a digest-only reference in an ours file" "scripts/run:1: openvino/model_server@${D1} has no tag" \
    "printf 'AGENT_OVMS_IMAGE=\"openvino/model_server@${D1}\"\n' > scripts/run"
pins_case "a digest-only reference in an eat-stock file" "integrations/eat/run:1: intel/x@${D1} has no tag" \
    "mkdir -p integrations/eat; printf 'integrations/eat/** eat-stock EAT\n' >> pins/classes; printf 'X_IMAGE=intel/x@${D1}\n' > integrations/eat/run"
pins_case "a digest-only reference images.unpinned allows" ok \
    "printf 'AGENT_OVMS_IMAGE=\"openvino/model_server@${D1}\"\n' > scripts/run; printf 'scripts/run openvino/model_server@* a test input\n' >> images.unpinned"
pins_case "digest-only references in ledger and historical files (records)" ok \
    "mkdir -p bench/h bench/l; printf 'bench/h/** historical done\nbench/l/** ledger H1 amend=X a row\n' >> pins/classes; printf 'IMG=openvino/model_server@${D1}\n' | tee bench/h/run.sh > bench/l/run.sh"
pins_case "variables" ok \
    "printf -- '- image: \${AGENT_IMAGE}\n- image: \"@VLLM_IMAGE@\"\n- image: {image}\n' > share/a.yml"
pins_case "a CRD schema image: key" ok \
    "printf 'properties:\n  image:\n    type: string\n' > share/a.yml"
pins_case "prose (*.md, docs/)" ok \
    "mkdir -p docs; printf 'docker run busybox:1.37\nimage: redis:7.4\n' | tee README.md > docs/x.txt"
# A Dockerfile's apt-get install of NVIDIA packages pins pkg=version (the nightly
# rebuild would otherwise install whatever NVIDIA's repo serves that day).
pins_case "an unpinned apt nvidia-container-toolkit" "Dockerfile:4: apt package nvidia-container-toolkit is not pinned" \
    "printf 'FROM ubuntu:26.04@${D1}\nRUN apt-get update && apt-get install -y nvidia-container-toolkit=1.20.1-1 curl \\\\\n    nvidia-container-toolkit-base=1.20.1-1 \\\\\n    && apt-get install -y nvidia-container-toolkit\n' > Dockerfile"
pins_case "an unpinned apt libnvidia-container1 on a continuation line" "Dockerfile:3: apt package libnvidia-container1 is not pinned" \
    "printf 'FROM ubuntu:26.04@${D1}\nRUN apt-get install -y --no-install-recommends curl \\\\\n    libnvidia-container1 util-linux\n' > Dockerfile"
pins_case "an apt NVIDIA pin whose ARG default is empty" "apt package nvidia-container-runtime is not pinned" \
    "printf 'FROM ubuntu:26.04@${D1}\nARG RT_VERSION\nRUN apt-get install -y nvidia-container-runtime=\${RT_VERSION}\n' > Dockerfile"
pins_case "apt NVIDIA packages pinned literally and by ARG" ok \
    "printf 'FROM ubuntu:26.04@${D1}\nARG NVIDIA_CTK_VERSION=\"1.20.1-1\"\nRUN apt-get install -y curl nvidia-container-toolkit=\${NVIDIA_CTK_VERSION} \\\\\n    libnvidia-container1=1.20.1-1 && echo nvidia-smi\n' > Dockerfile"
pins_case "an unpinned apt NVIDIA package in a *.Dockerfile" "images/x.Dockerfile:1: apt package nvidia-container-toolkit-base is not pinned" \
    "mkdir -p images; printf 'RUN apt install -y nvidia-container-toolkit-base\n' > images/x.Dockerfile"
# Helm values name an image as sibling repository: and tag: keys (EAT's charts, our overlays).
pins_case "a values repository/tag pair" "integrations/eat/gw-values.yaml:3: bitnamilegacy/redis:8.0.3-debian-12-r2 is not pinned" \
    "mkdir -p integrations/eat; printf 'redis:\n  image:\n    repository: bitnamilegacy/redis\n    tag: \"8.0.3-debian-12-r2\"\n' > integrations/eat/gw-values.yaml"
pins_case "a values repository with no tag (the chart's default tag, here latest)" \
    "integrations/eat/gw-values.yaml:3: bitnamilegacy/postgresql is not pinned" \
    "mkdir -p integrations/eat; printf 'postgresql:\n  image:\n    repository: bitnamilegacy/postgresql\n  auth:\n    tag: x\n' > integrations/eat/gw-values.yaml"
pins_case "a values pair tagged latest" "bitnamilegacy/redis:latest@${D1} is tagged latest" \
    "printf 'image:\n  tag: latest@${D1}\n  repository: bitnamilegacy/redis\n' > values.yaml"
pins_case "a values pair with a registry key" "values.yaml:2: docker.io/langfuse/langfuse:3.173.0 is not pinned" \
    "printf 'image:\n  repository: langfuse/langfuse\n  registry: docker.io\n  pullPolicy: Always\n  tag: 3.173.0\n' > values.yaml"
pins_case "values pairs pinned name:tag@sha256, nested and quoted" ok \
    "printf 'image:\n  repository: \"docker.litellm.ai/berriai/litellm\"\n  tag: \"v1.83.14-stable.patch.2@${D1}\"\nclickhouse:\n  zookeeper:\n    image:\n      repository: bitnamilegacy/zookeeper\n      tag: 3.9.3-debian-12-r8@${D2}\n' > values.yaml"
pins_case "a workflow's with: repository: (a repo, not an image)" ok \
    "mkdir -p .github/workflows; printf 'steps:\n  - uses: x/y@v1\n    with:\n      repository: owner/repo\n      tag: v1\n' > .github/workflows/x.yml"
pins_case "a values repository whose value is on the next line or a variable" ok \
    "printf 'properties:\n  repository:\n    type: string\nimage:\n  repository: {{ .Values.x }}\n  tag: \"1\"\n' > share/a.yml"

# ---- images.lock sources: integrations/eat values pairs and Dockerfile FROMs ----
scratch
mkdir -p "${WORK}/r/integrations/eat"
printf 'ARG K3S_TAG=v1.37.1-k3s1\nARG UBUNTU_2404_IMAGE="ubuntu:24.04@%s"\nFROM debian:trixie-slim@%s\n' "${D1}" "${D1}" > "${WORK}/r/Dockerfile"
: > "${WORK}/r/scripts/k3d-gpu"
printf 'FROM ubuntu:24.04@%s\n' "${D1}" > "${WORK}/r/integrations/eat/bastion.Dockerfile"
printf 'image:\n  repository: bitnamilegacy/redis\n  tag: "8.0.3-debian-12-r2@%s"\n' "${D2}" > "${WORK}/r/integrations/eat/gw-values.yaml"
printf 'cluster_url=api.example.com\n' > "${WORK}/r/integrations/eat/agentic-config.cfg"
if out=$(python3 - "${WORK}/r" "${D1}" "${D2}" 2>&1 <<'EOF'
import importlib.machinery, importlib.util, sys
root, d1, d2 = sys.argv[1:]
loader = importlib.machinery.SourceFileLoader("images_lock", root + "/scripts/images-lock")
spec = importlib.util.spec_from_loader("images_lock", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
found, _ = m.collect(root)
want = {
    ("docker.io/bitnamilegacy/redis", "8.0.3-debian-12-r2"): (d2, {"integrations/eat/gw-values.yaml"}),
    ("docker.io/library/ubuntu", "24.04"): (d1, {"Dockerfile", "integrations/eat/bastion.Dockerfile"}),
    ("docker.io/library/debian", "trixie-slim"): (d1, {"Dockerfile"}),
}
for key, (digest, sources) in want.items():
    got = found.get(key)
    assert got == {"digest": digest, "sources": sources}, (key, got)
EOF
); then ok "images.lock collects integrations/eat values pairs, Dockerfile FROMs and the Dockerfile's *_IMAGE ARGs"
else fail "images-lock collect integrations/eat"; sed 's/^/  | /' <<< "${out}"; fi

# ---- repin, with the registry stubbed ----
scratch
cat > "${WORK}/r/share/a.yml" <<EOF
- image: redis:7.4
- image: quay.io/x/y:v1@${D1}
- image: openvino/model_server@${D1}
EOF
printf 'TEST_IMAGE="${K3D_GPU_TEST_IMAGE:-nvidia/cuda:13.4.3-base-ubuntu26.04}"\n' > "${WORK}/r/scripts/run"
mkdir -p "${WORK}/r/integrations/eat"
cat > "${WORK}/r/integrations/eat/gw-values.yaml" <<EOF
redis:
  image:
    repository: bitnamilegacy/redis
    tag: "8.0.3-debian-12-r2"
postgresql:
  image:
    repository: bitnamilegacy/postgresql
    tag: "17.5.0-debian-12-r20@${D1}"
EOF
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
if grep -qxF "    tag: \"8.0.3-debian-12-r2@${D2}\"" "${WORK}/r/integrations/eat/gw-values.yaml" \
   && grep -qxF "    tag: \"17.5.0-debian-12-r20@${D2}\"" "${WORK}/r/integrations/eat/gw-values.yaml"; then
    ok "repin pins a values tag and moves a stale one"
else fail "repin values pair"; sed 's/^/  | /' "${WORK}/r/integrations/eat/gw-values.yaml"; fi
if out=$(repin 2>&1) && [ -z "${out}" ]; then ok "repin is idempotent"; else fail "repin changed a pinned tree again"; sed 's/^/  | /' <<< "${out}"; fi

# ---- repin --exclude: the nightly's token cannot push .github/workflows/ ----
scratch
mkdir -p "${WORK}/r/.github/workflows"
printf 'jobs:\n  a:\n    container: alpine:3.22@%s\n' "${D1}" > "${WORK}/r/.github/workflows/x.yml"
printf -- '- image: alpine:3.22@%s\n' "${D1}" > "${WORK}/r/share/a.yml"
cp "${WORK}/r/.github/workflows/x.yml" "${WORK}/x.yml.before"
exclude_out=$(python3 - "${WORK}/r" "${D2}" 2>&1 <<'EOF'
import importlib.machinery, importlib.util, sys
root, digest = sys.argv[1], sys.argv[2]
loader = importlib.machinery.SourceFileLoader("images_lock", root + "/scripts/images-lock")
spec = importlib.util.spec_from_loader("images_lock", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
m.registry_digest = lambda name, tag: digest
sys.exit(m.main(["images-lock", "repin", "--exclude", ".github/workflows/*", "--root", root]))
EOF
); rc=$?
if [ "${rc}" -eq 0 ] && cmp -s "${WORK}/x.yml.before" "${WORK}/r/.github/workflows/x.yml" \
   && ! grep -qF '.github/' <<< "${exclude_out}"; then
    ok "repin --exclude '.github/workflows/*' leaves a stale workflow pin alone"
else fail "repin --exclude rewrote or reported an excluded file (exit ${rc})"; sed 's/^/  | /' "${WORK}/r/.github/workflows/x.yml" <<< "${exclude_out}"; fi
if [ "$(cat "${WORK}/r/share/a.yml")" = "- image: alpine:3.22@${D2}" ] && grep -qF 'share/a.yml:1:' <<< "${exclude_out}"; then
    ok "repin --exclude still moves the files it does not exclude"
else fail "repin --exclude skipped share/a.yml"; sed 's/^/  | /' "${WORK}/r/share/a.yml" <<< "${exclude_out}"; fi
if out=$("${WORK}/r/scripts/images-lock" check --exclude 'x' --root "${WORK}/r" 2>&1); then
    fail "check accepted --exclude"; sed 's/^/  | /' <<< "${out}"
elif grep -qF -- '--exclude' <<< "${out}"; then ok "--exclude is a repin option only"
else fail "check --exclude failed for another reason"; sed 's/^/  | /' <<< "${out}"; fi

# ---- repin and drift leave ledger and historical paths (pins/classes) alone ----
# pyrun <python>: run python with `m` (scratch images-lock) loaded, the registry stubbed
# by STUB (a dict name:tag -> digest, else D2), and argv[1:] = root, D1, D2.
pyrun() {
    python3 - "${WORK}/r" "${D1}" "${D2}" <<EOF
import importlib.machinery, importlib.util, sys
root, d1, d2 = sys.argv[1:]
loader = importlib.machinery.SourceFileLoader("images_lock", root + "/scripts/images-lock")
spec = importlib.util.spec_from_loader("images_lock", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
STUB = {}
m.registry_digest = lambda name, tag: STUB.get("%s:%s" % (name, tag), d2)
$1
EOF
}
scratch
mkdir -p "${WORK}/r/pins" "${WORK}/r/bench/h" "${WORK}/r/bench/l"
printf 'bench/h/** historical a finished run\nbench/l/** ledger H1 amend=X a row\nhistorical-ref scripts/mixed.sh keep-me\n' >> "${WORK}/r/pins/classes"
for f in bench/h/run.sh bench/l/run.sh scripts/mixed.sh share/a.yml; do
    printf -- '- image: alpine:3.22@%s\n' "${D1}" > "${WORK}/r/${f}"
done
echo "keep-me" >> "${WORK}/r/scripts/mixed.sh"
cp -r "${WORK}/r" "${WORK}/before"
drift_out=$(pyrun 'sys.exit(m.main(["images-lock", "drift", "--root", root]))' 2>&1); rc=$?
if [ "${rc}" -eq 0 ] && grep -qxF "share/a.yml:1: alpine:3.22 ${D1} -> ${D2}" <<< "${drift_out}" \
   && ! grep -qE 'bench/|mixed' <<< "${drift_out}" && diff -r -x __pycache__ "${WORK}/before" "${WORK}/r" > /dev/null; then
    ok "drift lists a moved pin, writes nothing and skips ledger, historical and historical-ref files"
else fail "drift (exit ${rc})"; sed 's/^/  | /' <<< "${drift_out}"; fi
repin_out=$(pyrun 'sys.exit(m.main(["images-lock", "repin", "--root", root]))' 2>&1); rc=$?
if [ "${rc}" -eq 0 ] && [ "$(cat "${WORK}/r/share/a.yml")" = "- image: alpine:3.22@${D2}" ] \
   && cmp -s "${WORK}/before/bench/h/run.sh" "${WORK}/r/bench/h/run.sh" \
   && cmp -s "${WORK}/before/bench/l/run.sh" "${WORK}/r/bench/l/run.sh" \
   && cmp -s "${WORK}/before/scripts/mixed.sh" "${WORK}/r/scripts/mixed.sh"; then
    ok "repin leaves ledger, historical and historical-ref files untouched and moves the rest"
else fail "repin touched a ledger or historical file (exit ${rc})"; sed 's/^/  | /' <<< "${repin_out}"; fi
out=$(pyrun 'sys.exit(m.main(["images-lock", "repin", "--root", root, "bench/l/run.sh"]))' 2>&1)
if cmp -s "${WORK}/before/bench/l/run.sh" "${WORK}/r/bench/l/run.sh"; then ok "repin FILE skips a ledger file named on the command line"
else fail "repin FILE rewrote a ledger file"; sed 's/^/  | /' <<< "${out}"; fi

# ---- update --only FILE...: one drifted digest elsewhere does not block a bump ----
scratch
printf 'ARG K3S_TAG=v1.37.1-k3s1\nFROM debian:trixie-slim@%s\n' "${D1}" > "${WORK}/r/Dockerfile"
: > "${WORK}/r/scripts/k3d-gpu"
printf -- '- image: quay.io/a/a:1@%s\n' "${D1}" > "${WORK}/r/share/a.yml"
printf -- '- image: quay.io/b/b:1@%s\n' "${D1}" > "${WORK}/r/share/b.yml"
rm "${WORK}/r/share/local.yml"; : > "${WORK}/r/images.unpinned"
# update_case NAME WANT_RC GREP [ARGS...]: `update ARGS` in the scratch root. Every
# registry stub serves D1 except the name:tag refs DRIFT lists (they serve D2);
# k3s_images is stubbed and prints when it is called. GREP '!re' wants no match.
update_case() {
    local name=$1 want=$2 pat=$3 out rc hit
    shift 3
    out=$(update_args "$@" 2>&1); rc=$?
    case "${pat}" in
        "") hit=0 ;;
        !*) ! grep -qE -- "${pat#!}" <<< "${out}"; hit=$? ;;
        *) grep -qE -- "${pat}" <<< "${out}"; hit=$? ;;
    esac
    if [ "${rc}" = "${want}" ] && [ "${hit}" = 0 ]; then ok "${name}"
    else fail "${name} (exit ${rc}, want ${want})"; sed 's/^/  | /' <<< "${out}"; fi
}
update_args() {
    DRIFT="${DRIFT:-}" python3 - "${WORK}/r" "${D1}" "${D2}" "$@" <<'EOF'
import importlib.machinery, importlib.util, os, sys
root, d1, d2 = sys.argv[1:4]
loader = importlib.machinery.SourceFileLoader("images_lock", root + "/scripts/images-lock")
spec = importlib.util.spec_from_loader("images_lock", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
drift = os.environ.get("DRIFT", "").split()
m.registry_digest = lambda name, tag: d2 if "%s:%s" % (name, tag) in drift else d1
def k3s(tag):
    print("k3s-images.txt fetched for " + tag)
    return ["docker.io/rancher/mirrored-pause:3.6"]
m.k3s_images = k3s
sys.exit(m.main(["images-lock", "update", "--root", root] + sys.argv[4:]))
EOF
}
update_case "update writes images.lock (all registries agree)" 0 "k3s-images.txt fetched for v1.37.1-k3s1"
cp "${WORK}/r/images.lock" "${WORK}/lock.before"
DRIFT="quay.io/b/b:1" update_case "update without --only fails on a drifted digest anywhere" 2 \
    "quay.io/b/b:1: the repo pins ${D1} but the registry serves ${D2}"
DRIFT="quay.io/b/b:1" update_case "update --only share/a.yml ignores the drift in share/b.yml" 0 "" --only share/a.yml
if cmp -s "${WORK}/lock.before" "${WORK}/r/images.lock"; then ok "update --only keeps the entries of files it does not name"
else fail "update --only rewrote other entries"; diff "${WORK}/lock.before" "${WORK}/r/images.lock" | sed 's/^/  | /'; fi
DRIFT="quay.io/b/b:1" DRIFT="quay.io/b/b:1" update_case "update --only does not fetch k3s-images.txt unless the Dockerfile is named" 0 \
    "!k3s-images.txt fetched" --only share/a.yml
DRIFT="quay.io/b/b:1" update_case "update --only share/b.yml still validates the file it names" 2 \
    "quay.io/b/b:1: the repo pins ${D1} but the registry serves ${D2}" --only share/b.yml
DRIFT="quay.io/b/b:1" update_case "update --only Dockerfile refreshes k3s-images.txt" 0 "k3s-images.txt fetched" --only Dockerfile
printf -- '- image: quay.io/c/c:1@%s\n' "${D1}" > "${WORK}/r/share/c.yml"
update_case "update --only resolves a new reference of a named file" 0 "" --only share/c.yml
if grep -qF '"name": "quay.io/c/c"' "${WORK}/r/images.lock"; then ok "update --only adds the named file's new reference"
else fail "update --only did not add quay.io/c/c"; fi
printf -- '- image: quay.io/d/d:1@%s\n' "${D1}" > "${WORK}/r/share/d.yml"
update_case "update --only refuses a reference images.lock lacks from a file it does not name" 2 \
    "quay.io/d/d:1 .*share/d.yml.* not in images.lock" --only share/c.yml
rm "${WORK}/r/share/d.yml"
printf -- '- image: quay.io/a/a:1@%s\n' "${D2}" > "${WORK}/r/share/a.yml"
update_case "update --only refuses a digest images.lock does not hold, from a file it does not name" 2 \
    "quay.io/a/a:1 .*share/a.yml.* pins ${D2}, images.lock has ${D1}" --only share/c.yml
printf -- '- image: quay.io/a/a:1@%s\n' "${D1}" > "${WORK}/r/share/a.yml"
printf -- '- image: quay.io/d/d:1@%s\n' "${D1}" > "${WORK}/r/share/d.yml"
sed -i 's/v1.37.1-k3s1/v1.37.2-k3s1/' "${WORK}/r/Dockerfile"
update_case "update --only refuses a moved K3S_TAG unless the Dockerfile is named" 2 "K3S_TAG" --only share/d.yml

# ---- http(): 429 and 5xx retried, Retry-After honoured (capped), other statuses not ----
if out=$(python3 - "${REPO}" 2>&1 <<'EOF'
import http.server, importlib.machinery, importlib.util, sys, threading, urllib.error, urllib.request
loader = importlib.machinery.SourceFileLoader("images_lock", sys.argv[1] + "/scripts/images-lock")
spec = importlib.util.spec_from_loader("images_lock", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
PLAN, SLEPT = [], []
class H(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        code, retry = PLAN.pop(0) if PLAN else (200, None)
        self.send_response(code)
        if retry is not None:
            self.send_header("Retry-After", retry)
        if code == 200:
            self.send_header("Docker-Content-Digest", "sha256:" + "3" * 64)
        self.end_headers()
    def log_message(self, *a):
        pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/v2/x/y/manifests/1" % srv.server_port
m.time.sleep = SLEPT.append
def get():
    with m.http(urllib.request.Request(url, method="HEAD")) as r:
        return r.headers["Docker-Content-Digest"]
def case(plan, slept, ok=True):
    PLAN[:] = plan
    SLEPT.clear()
    try:
        got = get()
    except urllib.error.HTTPError as e:
        got = e.code
    assert (got == "sha256:" + "3" * 64) == ok, (plan, got)
    assert SLEPT == slept, (plan, SLEPT, slept)
    return got
case([(429, "7")], [7])
case([(503, None), (502, None)], [2, 4])
case([(429, "500")], [120])
case([(429, "Wed, 21 Oct 2015 07:28:00 GMT")], [0])
assert case([(429, "1")] * 5, [1, 1, 1, 1], ok=False) == 429
assert case([(404, None)], [], ok=False) == 404
print("ok")
EOF
); then ok "http() retries 429/5xx up to 5 attempts, honours Retry-After (seconds or date, capped at 120 s), not 404"
else fail "http() retry"; sed 's/^/  | /' <<< "${out}"; fi

# ---- the repo: pins and repin read the CI tool pins (hack/ci-images.env, hack/ci-tools.env) ----
if out=$(python3 - "${REPO}" 2>&1 <<'EOF'
import importlib.machinery, importlib.util, sys
root = sys.argv[1]
loader = importlib.machinery.SourceFileLoader("images_lock", root + "/scripts/images-lock")
spec = importlib.util.spec_from_loader("images_lock", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
files = m.scanned_files(root)
for f, want in (("hack/ci-images.env", "rhysd/actionlint:"), ("hack/ci-tools.env", "ghcr.io/yannh/kubeconform:")):
    refs = [r for _, r, _ in m.image_refs(m.read_text(root, f), f)] if f in files else []
    assert any(r.startswith(want) and m.is_pinned(r) for r in refs), (f, refs)
    assert not m.frozen(root, f), f
# pins/classes quotes finished runs' refs (historical-ref): repin must never move them.
assert "pins/classes" not in files and "images.unpinned" not in files
EOF
); then ok "pins and repin read hack/ci-images.env and hack/ci-tools.env (tagged, pinned, never frozen), not pins/classes"
else fail "images-lock does not scan the CI tool pins"; sed 's/^/  | /' <<< "${out}"; fi

# ---- the repo: no workflow file holds a pin the nightly would have to move ----
# repin-images runs `repin --exclude '.github/workflows/*'`, so a name:tag@sha256
# written in a workflow would never move again. Workflows read their image pins
# from files outside .github/workflows/ (the Dockerfile, docs-build/Dockerfile,
# hack/ci-images.env).
if out=$(python3 - "${REPO}" 2>&1 <<'EOF'
import fnmatch, importlib.machinery, importlib.util, sys
root = sys.argv[1]
loader = importlib.machinery.SourceFileLoader("images_lock", root + "/scripts/images-lock")
spec = importlib.util.spec_from_loader("images_lock", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
bad = []
for path in m.scanned_files(root):
    text = m.read_text(root, path) if fnmatch.fnmatchcase(path, ".github/workflows/*") else None
    for p in m.PINNED_RE.finditer(text or ""):
        if m.parse_ref(p.group("ref"))[1] is not None:
            bad.append("%s:%d: %s" % (path, text.count("\n", 0, p.start()) + 1, p.group(0)))
print("\n".join(bad))
sys.exit(1 if bad else 0)
EOF
); then ok "no workflow file pins a name:tag@sha256 (the nightly repin cannot push .github/workflows/)"
else fail "a workflow file pins name:tag@sha256; move it to a file outside .github/workflows/"; sed 's/^/  | /' <<< "${out}"; fi

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
