#!/usr/bin/env bash
# zero_copy_workdir_test.sh: bench/zero-copy-models/probe.sh's results directory (WORK).
# H28x's hold on crescent (2026-10-21) passed a WORK that did not exist; the CSV was lost
# and the locked part died at the scratch kubeconfig write after the load had started and
# the cluster had been created (EXPERIMENTS.md "H28x amendment R1"). `run` must create
# WORK and stop before the lock when it cannot write there; `_rounds` must stop before the
# load and the cluster when WORK is missing. docker, k3d, kubectl, flock and sudo are shims
# that log their calls; flock does not run its command. No docker daemon, no cluster.
#
# Usage: tests/zero_copy_workdir_test.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROBE=${ROOT}/bench/zero-copy-models/probe.sh
PASS=0 FAILS=0
T=$(mktemp -d)
trap 'rm -rf "${T}"' EXIT
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() {
    local l
    FAILS=$((FAILS + 1)); echo "FAIL $1"
    [ -z "${2:-}" ] || while IFS= read -r l; do echo "  | ${l}"; done <<< "$2"
}

SHIMS=${T}/bin
mkdir -p "${SHIMS}"
for c in docker k3d kubectl flock sudo; do
    cat > "${SHIMS}/${c}" <<'EOF'
#!/usr/bin/env bash
echo "${0##*/} $*" >> "${CALLS}"
case "${0##*/} $1 $2" in
    "k3d cluster list") echo "[]" ;;
    "docker info "*) echo "${DOCKER_ROOT}" ;;
    "sudo "*) exit 1 ;;
esac
exit 0
EOF
    chmod +x "${SHIMS}/${c}"
done

# probe CASE VERB [ENV...]: run probe.sh VERB with the shims first on PATH; sets OUT (stdout
# and stderr), RC and CALLS (the shim log).
probe() {
    local case=$1 verb=$2
    shift 2
    CALLS=${T}/${case}.calls
    : > "${CALLS}"
    OUT=$(env PATH="${SHIMS}:${PATH}" CALLS="${CALLS}" DOCKER_ROOT="${T}" N=1 MIB=1 HOLD=600 \
        LOCK="${T}/inner.lock" IMAGE=k3d-gpu:test "$@" timeout -k 5 60 bash "${PROBE}" "${verb}" 2>&1)
    RC=$?
}

# 1. run creates a WORK whose parents do not exist (crescent's runs/H28x/work), and the CSV
#    in it, before it takes the lock.
W=${T}/a/runs/H28x/work
probe a run WORK="${W}"
csv=$(find "${W}" -maxdepth 1 -name 'h28-*.csv' 2>/dev/null | head -n 1)
if [ "${RC}" -eq 0 ] && [ -d "${W}" ] && [ -n "${csv}" ] && grep -q '^flock ' "${CALLS}"; then
    ok "run: creates a missing WORK and its CSV, then takes the lock"
else
    fail "run: creates a missing WORK and its CSV, then takes the lock (rc ${RC}, WORK $([ -d "${W}" ] && echo exists || echo missing), csv '${csv}')" \
        "${OUT}$(printf '\n'; cat "${CALLS}")"
fi

# 2. run stops, loudly and before the lock, when WORK cannot be created (a file in its path).
: > "${T}/b-file"
probe b run WORK="${T}/b-file/work"
if [ "${RC}" -ne 0 ] && grep -q 'ABORT,workdir' <<< "${OUT}" && ! grep -q '^flock ' "${CALLS}" \
    && ! grep -q '^k3d cluster create' "${CALLS}"; then
    ok "run: an uncreatable WORK fails before the lock"
else
    fail "run: an uncreatable WORK fails before the lock (rc ${RC})" "${OUT}$(printf '\n'; cat "${CALLS}")"
fi

# 3. The locked part (_rounds) stops on a missing WORK before it starts the load or creates
#    the cluster. Without the check it got as far as the kubeconfig write (crescent, line 324).
probe c _rounds WORK="${T}/c/missing"
if [ "${RC}" -ne 0 ] && grep -q 'ABORT,workdir' <<< "${OUT}" && ! grep -q '^docker run' "${CALLS}" \
    && ! grep -q '^k3d cluster create' "${CALLS}"; then
    ok "_rounds: a missing WORK fails before the load and the cluster"
else
    fail "_rounds: a missing WORK fails before the load and the cluster (rc ${RC})" "${OUT}$(printf '\n'; cat "${CALLS}")"
fi

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
