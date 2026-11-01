#!/usr/bin/env bash
# Checks that every patch in ../patches applies to a fresh copy of the
# enterprise-ai-solutions clone (git apply --check, one patch at a time), then
# that the series applies in order with git am. The clone is copied, never
# modified.
#
# With --overlay it prints the decisions of integrations/eas/overlay.py, the
# overlay `k3d-gpu eas up` applies, against a real EAS clone: every
# overlay.json `verified` case at its pinned sha (fails when a recorded decision
# moved), then every `watch` ref (branch tips and open upstream PRs) for
# k3d-gpu's own facts. Without a clone it clones intel/enterprise-ai-solutions;
# a given clone is copied first. Fetches branches and PR heads, so it needs the
# network and stays out of CI.
#
# Usage: integrations/eat/upstream/checks/apply-check.sh <eas-clone>
#        integrations/eat/upstream/checks/apply-check.sh --overlay [<eas-clone>]

set -uo pipefail

PATCHES="$(cd "$(dirname "$0")/../patches" && pwd)"
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
FAILS=0

if [ "${1:-}" = --overlay ]; then
    if [ -n "${2:-}" ]; then
        cp -a "$2" "${WORK}/eas"
    else
        git clone -q https://github.com/intel/enterprise-ai-solutions.git "${WORK}/eas" || exit 2
    fi
    git -C "${WORK}/eas" fetch -q origin '+refs/heads/*:refs/remotes/origin/*' \
        '+refs/pull/*/head:refs/remotes/pull/*/head' || exit 2
    echo "## verified pins (integrations/eas/overlay.json)"
    python3 "${ROOT}/integrations/eas/overlay.py" --eas "${WORK}/eas" --check-verified || FAILS=$((FAILS + 1))
    echo
    echo "## watch refs, k3d-gpu facts"
    python3 "${ROOT}/integrations/eas/overlay.py" --eas "${WORK}/eas" --watch || FAILS=$((FAILS + 1))
    [ "${FAILS}" -eq 0 ]
    exit
fi

EAS=${1:?usage: $0 <enterprise-ai-solutions clone> | --overlay [<clone>]}

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
