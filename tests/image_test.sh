#!/usr/bin/env bash
# Node image selection tests. `k3d-gpu up` runs against stubbed docker/k3d/kubectl:
# the docker stub reports which images are cached (STUB_LOCAL_IMAGES, each
# <ref> or <ref>=<image id>), which pull (STUB_PULLABLE) and the existing
# cluster's server container ("<ref> <image id> <release label>" in STUB_NODE);
# k3d logs its arguments and exits 1, so `up` stops right after cluster create.
# kubectl answers `get nodes -o name` only when STUB_NODE is set (for doctor).
# No cluster, no registry.
#
# Usage: tests/image_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
K3D_GPU="${ROOT}/scripts/k3d-gpu"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

VERSION=$(bash "${K3D_GPU}" version | awk '{print $2}')
REPO="cryptoandcoffee/k3d-gpu"
PINNED="${REPO}:${VERSION}"

BIN="${WORK}/bin"
mkdir -p "${BIN}" "${WORK}/sys" "${WORK}/dev"
cat > "${BIN}/docker" <<'EOF'
#!/bin/sh
echo "docker $*" >> "${STUB_LOG}"
if [ "$1 $2" = "container inspect" ]; then
    [ -n "${STUB_NODE:-}" ] || { echo "Error: No such container" >&2; exit 1; }
    echo "${STUB_NODE}"
    exit 0
fi
if [ "$1 $2" = "image inspect" ]; then
    for a; do ref=$a; done
    for i in ${STUB_LOCAL_IMAGES:-}; do
        if [ "${i%%=*}" = "${ref}" ]; then
            [ "$3" = -f ] && echo "${i#*=}"
            exit 0
        fi
    done
    exit 1
fi
if [ "$1" = pull ]; then
    for i in ${STUB_PULLABLE:-}; do [ "$i" = "$2" ] && exit 0; done
    echo "Error response from daemon: manifest for $2 not found" >&2
    exit 1
fi
exit 0
EOF
printf '#!/bin/sh\necho "k3d $*" >> "${STUB_LOG}"\nexit 1\n' > "${BIN}/k3d"
cat > "${BIN}/kubectl" <<'EOF'
#!/bin/sh
case "$*" in *"get nodes -o name"*) [ -n "${STUB_NODE:-}" ] && echo node/k3d-gpu-server-0 ;; esac
exit 0
EOF
chmod +x "${BIN}"/*

pass() { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

# matches <ERE> <text>: a leading ! means the ERE must not match.
matches() {
    local re=$1 text=$2
    if [ "${re#!}" != "${re}" ]; then
        ! grep -qE -- "${re#!}" <<< "${text}"
    else
        grep -qE -- "${re}" <<< "${text}"
    fi
}

# ere <string>: the string as a literal ERE.
ere() { sed 's/[][\.*^$+?(){}|]/\\&/g' <<< "$1"; }

# up_says <name> <ERE on output> <ERE on stub log, or -> [env assignments...]
up_says() { CMD=up says "$@"; }
# doctor_says: the same for `k3d-gpu doctor`.
doctor_says() { CMD=doctor says "$@"; }
says() {
    local name=$1 re_out=$2 re_log=$3 out ok=1
    shift 3
    : > "${WORK}/log"
    out=$(env STUB_LOG="${WORK}/log" K3D_GPU_SYSROOT="${WORK}" K3D_GPU_VENDORS=gaudi \
        K3D_GPU_EXPERIMENTAL=off PATH="${BIN}:${PATH}" "$@" bash "${K3D_GPU}" "${CMD}" 2>&1)
    # Output and log are matched as one line each, so .* spans lines.
    matches "${re_out}" "$(tr '\n' ' ' <<< "${out}")" || ok=0
    [ "${re_log}" = - ] || matches "${re_log}" "$(tr '\n' ' ' < "${WORK}/log")" || ok=0
    if [ "${ok}" -eq 1 ]; then
        pass "${name}"
    else
        fail "${name} (/${re_out}/ on output, /${re_log}/ on log)"
        sed 's/^/  | /' <<< "${out}"; sed 's/^/  log| /' "${WORK}/log"
    fi
}

if [[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then pass "version is X.Y.Z (${VERSION})"; else fail "version '${VERSION}' is not X.Y.Z"; fi

up_says "default image is the tag matching the launcher version" \
    "with image $(ere "${PINNED}")" "k3d cluster create .*--image=$(ere "${PINNED}")( |$)" \
    STUB_LOCAL_IMAGES="${PINNED}"
up_says "a cached pinned image is not pulled again" \
    . "!docker pull" \
    STUB_LOCAL_IMAGES="${PINNED}"
up_says "a missing pinned image is pulled, then the cluster is created" \
    "Pulling node image $(ere "${PINNED}")" "docker pull $(ere "${PINNED}")" \
    STUB_PULLABLE="${PINNED}"
up_says "an unpublished pinned image fails before cluster create and names the opt-in" \
    "cannot pull node image $(ere "${PINNED}").*K3D_GPU_IMAGE=$(ere "${REPO}:latest")" "!k3d cluster create" \
    STUB_PULLABLE=""
up_says "K3D_GPU_IMAGE overrides the default verbatim" \
    "with image registry\.local/k3d-gpu:dev" "--image=registry\.local/k3d-gpu:dev( |$)" \
    K3D_GPU_IMAGE=registry.local/k3d-gpu:dev STUB_LOCAL_IMAGES=registry.local/k3d-gpu:dev
up_says "K3D_GPU_IMAGE=<repo>:latest opts in and warns that the tag floats" \
    "WARN.*$(ere "${REPO}:latest").*floating" "--image=$(ere "${REPO}:latest")( |$)" \
    K3D_GPU_IMAGE="${REPO}:latest" STUB_LOCAL_IMAGES="${REPO}:latest"
up_says "an untagged K3D_GPU_IMAGE means :latest and warns" \
    "WARN.*floating" "--image=registry\.local/k3d-gpu( |$)" \
    K3D_GPU_IMAGE=registry.local/k3d-gpu STUB_LOCAL_IMAGES=registry.local/k3d-gpu
up_says "a registry port is not taken for a tag" \
    "WARN.*floating" - \
    K3D_GPU_IMAGE=localhost:5000/k3d-gpu STUB_LOCAL_IMAGES=localhost:5000/k3d-gpu
DIGEST="${REPO}@sha256:$(printf '%064d' 0)"
up_says "a digest-pinned K3D_GPU_IMAGE does not warn" \
    "!floating" "--image=$(ere "${DIGEST}")( |$)" \
    K3D_GPU_IMAGE="${DIGEST}" STUB_LOCAL_IMAGES="${DIGEST}"
up_says "the pinned default does not warn" \
    "!floating" - \
    STUB_LOCAL_IMAGES="${PINNED}"

# A cluster an older launcher made keeps that launcher's image: k3d cannot swap it.
OLD_ID="sha256:a$(printf '%063d' 1)" NEW_ID="sha256:b$(printf '%063d' 2)"
up_says "up on an existing cluster from an unversioned :latest names it and stops" \
    "warn.*node image $(ere "${REPO}:latest") \(unversioned.*uses $(ere "${PINNED}").*k3d-gpu down && k3d-gpu up.*already exists" \
    "!k3d cluster create|docker pull" \
    STUB_NODE="${REPO}:latest ${OLD_ID} " STUB_LOCAL_IMAGES="${PINNED}=${NEW_ID}"
up_says "up on an existing cluster from an older release names that release" \
    "warn.*node image $(ere "${REPO}:0.0.9") \(release 0\.0\.9\).*uses $(ere "${PINNED}")" "!k3d cluster create" \
    STUB_NODE="${REPO}:0.0.9 ${OLD_ID} 0.0.9"
up_says "up on an existing cluster from the current image reports it ok" \
    "ok: node image $(ere "${PINNED}") \(release $(ere "${VERSION}")\).*already exists" "!k3d cluster create" \
    STUB_NODE="${PINNED} ${NEW_ID} ${VERSION}" STUB_LOCAL_IMAGES="${PINNED}=${NEW_ID}"
up_says "a re-pulled tag the cluster no longer matches is reported" \
    "warn.*$(ere "${PINNED}") now names image b00000000000 locally, the cluster runs a00000000000" - \
    STUB_NODE="${PINNED} ${OLD_ID} ${VERSION}" STUB_LOCAL_IMAGES="${PINNED}=${NEW_ID}"
doctor_says "doctor flags a cluster made from an older image" \
    "Checking cluster k3d-gpu.*warn.*node image $(ere "${REPO}:latest") \(unversioned" - \
    STUB_NODE="${REPO}:latest ${OLD_ID} "
doctor_says "doctor reports the node image of a current cluster" \
    "ok: node image $(ere "${PINNED}") \(release" - \
    STUB_NODE="${PINNED} ${NEW_ID} ${VERSION}" STUB_LOCAL_IMAGES="${PINNED}=${NEW_ID}"
doctor_says "doctor without a cluster says nothing about the node image" \
    "!node image" -

if bash "${K3D_GPU}" help | grep -qE "K3D_GPU_IMAGE .*default: $(ere "${PINNED}")[;)]"; then
    pass "help shows the pinned default image"
else
    fail "help shows the pinned default image"
fi

# The launcher defaults to <repo>:<VERSION>, so the release build must publish that
# tag, from the release tag's source, before the launcher reaches AUR.
WF="${ROOT}/.github/workflows"
contract() {
    local name=$1 file=$2 re=$3
    if matches "${re}" "$(cat "${WF}/${file}")"; then pass "${name}"; else fail "${name} (/${re}/ in ${file})"; fi
}
contract "release build publishes <repo>:<version> on Docker Hub" build-and-push.yml \
    '^ +cryptoandcoffee/k3d-gpu:\$\{\{ inputs\.release \}\}$'
contract "release build publishes <repo>:<version> on GHCR" build-and-push.yml \
    '^ +ghcr\.io/.*/k3d-gpu:\$\{\{ inputs\.release \}\}$'
contract "release build checks out the release tag" build-and-push.yml \
    "ref: .*aur-v"
contract "release build dispatches the AUR deploy after pushing" build-and-push.yml \
    "gh workflow run aur-deploy\.yml"
contract "auto-release hands the version to the image build" auto-release.yml \
    "gh workflow run build-and-push\.yml .*-f release="
contract "auto-release does not publish to AUR itself" auto-release.yml \
    "!gh workflow run aur-deploy"

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
