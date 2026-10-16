#!/usr/bin/env bash
# hack/render-component tests against a local helm chart repo served on loopback.
# No cluster, no network beyond 127.0.0.1. Needs the pinned helm on PATH
# (hack/install-helm <dir>) and python3 for the repo's HTTP server.
#
# Usage: tests/render_test.sh

set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RENDER="${REPO_DIR}/hack/render-component"
WORK=$(mktemp -d)
SERVER_PID=
trap '[ -n "${SERVER_PID}" ] && kill "${SERVER_PID}" 2>/dev/null; rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

# shellcheck source=hack/tools.env
. "${REPO_DIR}/hack/tools.env"
command -v helm >/dev/null && [ "$(helm version --template '{{.Version}}')" = "${HELM_VERSION}" ] \
    || { echo "FAIL needs helm ${HELM_VERSION} on PATH (hack/install-helm <dir>)"; exit 1; }

ok() { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/     | /'; }
# verdict <rc> <name> [detail]: ok when <rc> is 0, else fail with <detail>.
verdict() { if [ "$1" -eq 0 ]; then ok "$2"; else fail "$2" "${3:-}"; fi; }

# chart <version>: package the "demo" chart at <version> into the served repo dir.
chart() {
    local src="${WORK}/src/demo"
    mkdir -p "${src}/templates" "${src}/crds"
    printf 'apiVersion: v2\nname: demo\nversion: %s\nappVersion: "%s"\n' "$1" "$1" > "${src}/Chart.yaml"
    printf 'greeting: hello\n' > "${src}/values.yaml"
    printf 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: {{ .Release.Name }}\n  namespace: {{ .Release.Namespace }}\ndata:\n  greeting: {{ .Values.greeting | quote }}\n  version: {{ .Chart.Version | quote }}\n' \
        > "${src}/templates/cm.yaml"
    printf 'apiVersion: apiextensions.k8s.io/v1\nkind: CustomResourceDefinition\nmetadata:\n  name: demos.example.com\n' \
        > "${src}/crds/demo.yaml"
    helm package "${src}" --destination "${WORK}/served" >/dev/null
}

mkdir -p "${WORK}/served"
chart 1.0.0
chart 1.1.0
chart 2.0.0-rc.1
python3 -u -m http.server 0 --bind 127.0.0.1 --directory "${WORK}/served" > "${WORK}/server.log" 2>&1 &
SERVER_PID=$!
PORT=
for _ in $(seq 1 50); do
    PORT=$(sed -n 's/.* port \([0-9]*\).*/\1/p' "${WORK}/server.log" | head -n1)
    [ -n "${PORT}" ] && break
    sleep 0.1
done
[ -n "${PORT}" ] || { echo "FAIL local chart repo did not start"; cat "${WORK}/server.log"; exit 1; }
URL="http://127.0.0.1:${PORT}"
helm repo index "${WORK}/served" --url "${URL}"
SUM100=$(sha256sum "${WORK}/served/demo-1.0.0.tgz" | cut -d' ' -f1)
SUM110=$(sha256sum "${WORK}/served/demo-1.1.0.tgz" | cut -d' ' -f1)

# newroot <name> <version> <sha256>: a repo root with one "demo" component pinned there.
newroot() {
    local r="${WORK}/$1" d="${WORK}/$1/hack/components/demo"
    mkdir -p "${d}"
    cat > "${d}/component.conf" <<EOF
KIND=helm
CHART_REPO=${URL}
CHART=demo
CHART_VERSION=$2
CHART_SHA256=$3
RELEASE=demo-rel
NAMESPACE=demo-ns
OUTPUT=share/demo.yml
EOF
    printf '# Demo manifest (chart demo @VERSION@)\n#\n' > "${d}/header.txt"
    printf 'greeting: hi\n' > "${d}/values.yaml"
    echo "${r}"
}

# run <root> [args...]: render-component against <root>; sets OUT and RC.
run() { local r=$1; shift; OUT=$(RENDER_ROOT="${r}" "${RENDER}" "$@" 2>&1); RC=$?; }

# --- pinned render -------------------------------------------------------------
r=$(newroot pinned 1.0.0 "${SUM100}")
run "${r}" demo
f="${r}/share/demo.yml"
if [ "${RC}" -eq 0 ] && [ -f "${f}" ] \
    && [ "$(head -n3 "${f}")" = $'# Demo manifest (chart demo 1.0.0)\n#\n# values:' ] \
    && grep -qx '#   greeting: hi' "${f}" \
    && grep -q 'greeting: "hi"' "${f}" && grep -q 'version: "1.0.0"' "${f}" \
    && grep -q 'namespace: demo-ns' "${f}" && grep -q 'name: demo-rel' "${f}" \
    && grep -q 'name: demos.example.com' "${f}"; then
    ok "pinned render writes header, echoed values, CRDs and the templated chart"
else
    fail "pinned render writes header, echoed values, CRDs and the templated chart" "rc=${RC} ${OUT}"
fi
cp "${f}" "${WORK}/first.yml"
run "${r}" demo
cmp -s "${f}" "${WORK}/first.yml"
verdict $? "re-rendering the same pin is byte-identical" "$(diff "${WORK}/first.yml" "${f}")"

# --- sha256 pin ----------------------------------------------------------------
r=$(newroot badsum 1.0.0 "${SUM110}")
run "${r}" demo
if [ "${RC}" -ne 0 ] && grep -q "pinned ${SUM110}" <<< "${OUT}" && [ ! -e "${r}/share/demo.yml" ]; then
    ok "a chart whose sha256 differs from the pin is refused and nothing is written"
else
    fail "a chart whose sha256 differs from the pin is refused and nothing is written" "rc=${RC} ${OUT}"
fi

# --- --latest --------------------------------------------------------------------
r=$(newroot latest 1.0.0 "${SUM100}")
run "${r}" --latest demo
conf="${r}/hack/components/demo/component.conf"
if [ "${RC}" -eq 0 ] && grep -qx 'CHART_VERSION=1.1.0' "${conf}" && grep -qx "CHART_SHA256=${SUM110}" "${conf}" \
    && grep -q 'version: "1.1.0"' "${r}/share/demo.yml" && grep -q 'chart demo 1.1.0' "${r}/share/demo.yml" \
    && grep -qx 'rendered demo 1.1.0 -> share/demo.yml' <<< "${OUT}"; then
    ok "--latest moves the pins to the newest stable release (skips 2.0.0-rc.1) and renders it"
else
    fail "--latest moves the pins to the newest stable release (skips 2.0.0-rc.1) and renders it" "rc=${RC} ${OUT}
$(cat "${conf}")"
fi
run "${r}" demo
verdict "${RC}" "the pins --latest wrote verify on a plain render" "${OUT}"

r=$(newroot brokenvalues 1.0.0 "${SUM100}")
printf 'greeting: [unclosed\n' > "${r}/hack/components/demo/values.yaml"
run "${r}" --latest demo
conf="${r}/hack/components/demo/component.conf"
if [ "${RC}" -ne 0 ] && grep -qx 'CHART_VERSION=1.0.0' "${conf}" && grep -qx "CHART_SHA256=${SUM100}" "${conf}" \
    && [ ! -e "${r}/share/demo.yml" ]; then
    ok "--latest leaves the pins alone when the render fails"
else
    fail "--latest leaves the pins alone when the render fails" "rc=${RC} ${OUT}
$(cat "${conf}")"
fi

r=$(newroot ahead 9.0.0 "${SUM100}")
run "${r}" --latest demo
if [ "${RC}" -ne 0 ] && grep -q 'older than pinned 9.0.0' <<< "${OUT}" \
    && grep -qx 'CHART_VERSION=9.0.0' "${r}/hack/components/demo/component.conf"; then
    ok "--latest never moves a pin backwards"
else
    fail "--latest never moves a pin backwards" "rc=${RC} ${OUT}"
fi

# --- component discovery and config errors ----------------------------------------
r=$(newroot listing 1.0.0 "${SUM100}")
mkdir -p "${r}/hack/components/other" "${r}/hack/components/not-a-component"
cp "${r}/hack/components/demo/"* "${r}/hack/components/other/"
run "${r}" --list
if [ "${RC}" -eq 0 ] && [ "${OUT}" = $'demo\nother' ]; then
    ok "--list names every dir with a component.conf"
else
    fail "--list names every dir with a component.conf" "rc=${RC} ${OUT}"
fi

run "${r}" nope
[ "${RC}" -ne 0 ] && grep -q "unknown component 'nope'" <<< "${OUT}"
verdict $? "an unknown component name fails" "rc=${RC} ${OUT}"

sed -i '/^RELEASE=/d' "${r}/hack/components/other/component.conf"
run "${r}" other
[ "${RC}" -ne 0 ] && grep -q 'missing RELEASE' <<< "${OUT}"
verdict $? "a component.conf missing a required key fails" "rc=${RC} ${OUT}"

sed -i 's/^KIND=.*/KIND=kustomize/' "${r}/hack/components/other/component.conf"
run "${r}" other
[ "${RC}" -ne 0 ] && grep -q "unknown KIND 'kustomize'" <<< "${OUT}"
verdict $? "an unknown KIND fails" "rc=${RC} ${OUT}"

sed -i "s|^CHART_REPO=.*|CHART_REPO=http://charts.example.com|" "${r}/hack/components/demo/component.conf"
run "${r}" demo
[ "${RC}" -ne 0 ] && grep -q 'must be an https:// helm repo' <<< "${OUT}"
verdict $? "a plain-http (non-loopback) chart repo is refused" "rc=${RC} ${OUT}"

# --- pinned helm ----------------------------------------------------------------
mkdir -p "${WORK}/stub"
printf '#!/bin/sh\necho v4.0.0\n' > "${WORK}/stub/helm"
chmod +x "${WORK}/stub/helm"
r=$(newroot wronghelm 1.0.0 "${SUM100}")
OUT=$(PATH="${WORK}/stub:${PATH}" RENDER_ROOT="${r}" "${RENDER}" demo 2>&1); RC=$?
[ "${RC}" -ne 0 ] && grep -q "helm v4.0.0 on PATH, need the pinned ${HELM_VERSION}" <<< "${OUT}"
verdict $? "a helm other than the pinned version is refused" "rc=${RC} ${OUT}"

echo
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
