#!/usr/bin/env bash
# stress-ng.sh install|check: H58's pinned stress-ng under ~/k3dgpu-bench/bin (EXPERIMENTS.md "H58
# pre-registration" (f), amendment R1). stress-ng V0.22.01, the latest release (2026-09-20), built
# from its tag's commit with the host's compiler; no release binary is published.
#   install  clone the tag, check that it resolves to the pinned commit, build it (make -j), copy
#            the binary to ${B}/opt/stress-ng-0.22.01/, write its sha256 beside it and link
#            ${B}/bin/stress-ng to it. Under the bench lock: the build uses half the CPUs.
#   check    the link, the version line and the recorded sha256; exit 1 on any mismatch.
# H58_BENCH overrides ${B} (default ~/k3dgpu-bench), for the tests.
set -uo pipefail
B=${H58_BENCH:-${HOME}/k3dgpu-bench}
VER=0.22.01
TAG=V${VER}
COMMIT=72a9d0cae0f420a887991d4024d3101c4af7a4fc
URL=https://github.com/ColinIanKing/stress-ng.git
DIR=${B}/opt/stress-ng-${VER}
BIN=${DIR}/stress-ng
LINK=${B}/bin/stress-ng

check() {
    [ -L "${LINK}" ] && [ -x "${BIN}" ] && [ "$(readlink -f "${LINK}")" = "$(readlink -f "${BIN}")" ] \
        || { echo "${LINK} does not point at ${BIN}"; return 1; }
    [ -s "${BIN}.sha256" ] || { echo "no recorded sha256 for ${BIN}"; return 1; }
    [ "$(sha256sum "${BIN}" | cut -c1-64)" = "$(cut -c1-64 "${BIN}.sha256")" ] || { echo "${BIN} differs from its recorded sha256"; return 1; }
    "${LINK}" --version 2>&1 | grep -q "stress-ng, version ${VER} " || { echo "${LINK} is not stress-ng ${VER}"; return 1; }
    echo "stress-ng ${VER} at ${BIN}, sha256 $(cut -c1-64 "${BIN}.sha256"), commit ${COMMIT}"
}

case "${1:-}" in
    check) check ;;
    install)
        src=$(mktemp -d)
        trap 'rm -rf "${src}"' EXIT
        timeout 300 git clone -q --depth 1 --branch "${TAG}" "${URL}" "${src}/s" || { echo "clone failed"; exit 1; }
        got=$(git -C "${src}/s" rev-parse HEAD)
        [ "${got}" = "${COMMIT}" ] || { echo "${TAG} resolves to ${got}, not ${COMMIT}"; exit 1; }
        timeout 1800 make -C "${src}/s" -j "$(( $(nproc) / 2 ))" > "${src}/build.log" 2>&1 || { tail -30 "${src}/build.log"; echo "build failed"; exit 1; }
        mkdir -p "${DIR}" "${B}/bin"
        install -m 0755 "${src}/s/stress-ng" "${BIN}"
        sha256sum "${BIN}" > "${BIN}.sha256"
        ln -sfn "${BIN}" "${LINK}"
        check
        ;;
    *) echo "usage: stress-ng.sh install|check"; exit 2 ;;
esac
