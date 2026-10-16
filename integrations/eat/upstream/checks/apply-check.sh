#!/usr/bin/env bash
# Checks that every patch in ../patches applies to a fresh copy of its upstream
# clone (git apply --check, one patch at a time), then that the EAS series
# applies in order with git am. The clones are copied, never modified.
#
# Usage: integrations/eat/upstream/checks/apply-check.sh <eat-clone> <eas-clone>

set -uo pipefail

EAT=${1:?usage: $0 <enterprise-agent-toolkit clone> <enterprise-ai-solutions clone>}
EAS=${2:?usage: $0 <enterprise-agent-toolkit clone> <enterprise-ai-solutions clone>}
PATCHES="$(cd "$(dirname "$0")/../patches" && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
FAILS=0

# fresh <clone> <name>: a throwaway copy with the clone's HEAD checked out clean.
fresh() {
    rm -rf "${WORK:?}/$2"
    cp -a "$1" "${WORK}/$2"
    git -C "${WORK}/$2" reset -q --hard
    git -C "${WORK}/$2" clean -qfdx
}

for p in "${PATCHES}"/*.patch; do
    case "$(basename "${p}")" in
        *-eat-*) src=${EAT} name=eat ;;
        *) src=${EAS} name=eas ;;
    esac
    fresh "${src}" "${name}"
    head=$(git -C "${WORK}/${name}" rev-parse --short HEAD)
    if git -C "${WORK}/${name}" apply --check "${p}"; then
        printf 'ok   %-46s %s@%s\n' "$(basename "${p}")" "${name}" "${head}"
    else
        printf 'FAIL %-46s %s@%s\n' "$(basename "${p}")" "${name}" "${head}"
        FAILS=$((FAILS + 1))
    fi
done

fresh "${EAS}" eas
if git -C "${WORK}/eas" -c user.name=check -c user.email=check@invalid am -q "${PATCHES}"/*-eas-*.patch; then
    echo "ok   EAS series applies in order with git am"
else
    echo "FAIL EAS series does not apply in order"
    FAILS=$((FAILS + 1))
fi

[ "${FAILS}" -eq 0 ]
