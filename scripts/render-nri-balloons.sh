#!/usr/bin/env bash
# render-nri-balloons.sh — re-render the NRI balloons manifests from the helm
# values kept in each file's comment header ("# values:" to the first "---").
#
# Each file is: its header, unchanged, then the output of
#   helm template nri-resource-policy-balloons <chart> --namespace kube-system --include-crds -f <values>
# for the pinned chart below (downloaded by version, checked against SHA256).
# Maintainer tool: needs helm and network. The launcher never runs it.
#
# Usage: scripts/render-nri-balloons.sh [--check] [file...]
#        (default files: share/nri-balloons.yml share/nri-balloons-guard.yml)
#   --check  compare instead of writing; exit 1 when a file differs from its render

set -euo pipefail

CHART=nri-resource-policy-balloons
CHART_VERSION=v0.14.0
CHART_REPO=https://containers.github.io/nri-plugins
CHART_SHA256=425c92f79c7b33c560da75f632d83d1353e94027cffded249378a9c083fd3733

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CHECK=0
if [ "${1:-}" = --check ]; then CHECK=1; shift; fi
[ $# -gt 0 ] || set -- "${ROOT}/share/nri-balloons.yml" "${ROOT}/share/nri-balloons-guard.yml"

command -v helm >/dev/null || { echo "render-nri-balloons: helm not found" >&2; exit 2; }
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

helm pull "${CHART}" --repo "${CHART_REPO}" --version "${CHART_VERSION}" --destination "${WORK}" >/dev/null
TGZ=$(find "${WORK}" -maxdepth 1 -name '*.tgz' -print -quit)
echo "${CHART_SHA256}  ${TGZ}" | sha256sum -c --quiet - \
    || { echo "render-nri-balloons: chart ${CHART_VERSION} digest changed" >&2; exit 2; }

rc=0
for f in "$@"; do
    awk '/^---$/ {exit} {print}' "${f}" > "${WORK}/header"
    # "#   key: v" -> "key: v": the values block of the header, uncommented.
    sed -n '/^# values:$/,$p' "${WORK}/header" | sed '1d' | sed -E 's/^# {3}//; s/^#$//' > "${WORK}/values.yaml"
    [ -s "${WORK}/values.yaml" ] || { echo "render-nri-balloons: no '# values:' block in ${f}" >&2; exit 2; }
    { cat "${WORK}/header"
      helm template "${CHART}" "${TGZ}" --namespace kube-system --include-crds -f "${WORK}/values.yaml" 2>/dev/null
    } > "${WORK}/out"
    if [ "${CHECK}" -eq 1 ]; then
        if cmp -s "${WORK}/out" "${f}"; then echo "ok   ${f#"${ROOT}"/}"; else echo "DIFF ${f#"${ROOT}"/}"; rc=1; fi
    else
        cp "${WORK}/out" "${f}"
        echo "wrote ${f#"${ROOT}"/}"
    fi
done
exit "${rc}"
