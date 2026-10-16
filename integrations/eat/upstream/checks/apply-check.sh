#!/usr/bin/env bash
# Checks that every patch in ../patches applies to a fresh copy of the
# enterprise-ai-solutions clone (git apply --check, one patch at a time), then
# that the series applies in order with git am. The clone is copied, never
# modified.
#
# Usage: integrations/eat/upstream/checks/apply-check.sh <eas-clone>

set -uo pipefail

EAS=${1:?usage: $0 <enterprise-ai-solutions clone>}
PATCHES="$(cd "$(dirname "$0")/../patches" && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
FAILS=0

# fresh: a throwaway copy with the clone's HEAD checked out clean.
fresh() {
    rm -rf "${WORK:?}/eas"
    cp -a "${EAS}" "${WORK}/eas"
    git -C "${WORK}/eas" reset -q --hard
    git -C "${WORK}/eas" clean -qfdx
}

for p in "${PATCHES}"/*.patch; do
    fresh
    head=$(git -C "${WORK}/eas" rev-parse --short HEAD)
    if git -C "${WORK}/eas" apply --check "${p}"; then
        printf 'ok   %-46s eas@%s\n' "$(basename "${p}")" "${head}"
    else
        printf 'FAIL %-46s eas@%s\n' "$(basename "${p}")" "${head}"
        FAILS=$((FAILS + 1))
    fi
done

fresh
if git -C "${WORK}/eas" -c user.name=check -c user.email=check@invalid am -q "${PATCHES}"/*.patch; then
    echo "ok   series applies in order with git am"
else
    echo "FAIL series does not apply in order"
    FAILS=$((FAILS + 1))
fi

[ "${FAILS}" -eq 0 ]
