#!/usr/bin/env bash
# Tests for bench/f3/lockrun.sh without k3d or a cluster: run.sh is a stub next to a
# copy of lockrun.sh, and HOME is a temporary directory, so no case touches the real
# ~/k3dgpu-bench.lock. Every lockrun.sh call runs under `timeout`, so a case that
# waits on a lock it should not wait on fails instead of hanging.
#
# Usage: bench/f3/test_lockrun.sh

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
WORK=$(mktemp -d)
holder=
cleanup() {
    [ -n "${holder}" ] && kill "${holder}" 2> /dev/null
    rm -rf "${WORK}"
}
trap cleanup EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

if [ ! -f "${HERE}/lockrun.sh" ]; then
    fail "lockrun.sh does not exist"
    echo "${PASS} passed, ${FAILS} failed"
    exit 1
fi

mkdir -p "${WORK}/bin" "${WORK}/home"
cp "${HERE}/lockrun.sh" "${WORK}/bin/lockrun.sh"
# The stub run.sh records its arguments and whether the campaign lock is held while
# it runs (lockrun.sh calls it as: run.sh <src> <cluster> <out> <t0> <cap>).
cat > "${WORK}/bin/run.sh" << 'EOF'
#!/usr/bin/env bash
L=${K3DGPU_LIVE_LOCK:-${HOME}/k3dgpu-bench.lock}
if flock -n "${L}" true; then echo free; else echo held; fi > "$3/seen"
echo "$*" > "$3/args"
EOF

# run_lockrun <out> [VAR=value ...]: lockrun.sh with HOME=${WORK}/home and no
# K3DGPU_LIVE_LOCK unless one is given, capped at 30 s.
run_lockrun() {
    local out=$1
    shift
    env -u K3DGPU_LIVE_LOCK HOME="${WORK}/home" "$@" \
        timeout 30 bash "${WORK}/bin/lockrun.sh" src k3dgpu-f3-test "${out}" 1
}

# hold <file>: hold a flock on <file> in the background until killed; sets holder.
hold() {
    ( exec 8> "$1"; flock 8; exec sleep 60 ) &
    holder=$!
    for _ in $(seq 1 50); do flock -n "$1" true || return 0; sleep 0.1; done
    return 1
}

# 1. The runner names no path inside one session's directory: a reboot or the end of
# that session removes it, and then flock cannot open the lock file.
if hits=$(grep -nE '/tmp/claude-[0-9]+/|<scratch[p]ad>|scratch[p]ad/' "${HERE}/lockrun.sh" "${HERE}/launch.sh"); then
    fail "the F3 runner names a session-scoped path: ${hits}"
else
    ok "the F3 runner names no session-scoped path"
fi

# 2. With the default lock it runs run.sh with its arguments while holding
# ~/k3dgpu-bench.lock, logs the lock lines and releases the lock.
out="${WORK}/o2"
run_lockrun "${out}"
rc=$?
if [ "${rc}" -eq 0 ] && [ "$(cat "${out}/seen" 2> /dev/null)" = held ] \
   && grep -qE "^src k3dgpu-f3-test ${out} [0-9]+ 1$" "${out}/args" 2> /dev/null; then
    ok "default lock: run.sh ran with its arguments while ~/k3dgpu-bench.lock was held"
else
    fail "default lock: rc ${rc}, seen '$(cat "${out}/seen" 2> /dev/null)', args '$(cat "${out}/args" 2> /dev/null)'"
fi
if grep -q "^waiting for ${WORK}/home/k3dgpu-bench.lock " "${out}/lock.txt" 2> /dev/null \
   && grep -q '^lock taken ' "${out}/lock.txt" && grep -q '^lock released .* run rc 0$' "${out}/lock.txt"; then
    ok "lock.txt names the lock and records taken and released"
else
    fail "lock.txt: $(cat "${out}/lock.txt" 2> /dev/null)"
fi
if flock -n "${WORK}/home/k3dgpu-bench.lock" true; then
    ok "the lock is free after the run"
else
    fail "the lock is still held after the run"
fi

# 3. A hold on ~/k3dgpu-bench.lock (the H54, H55 and H57dp holds' lock) keeps run.sh
# from starting until it ends.
out="${WORK}/o3"
if hold "${WORK}/home/k3dgpu-bench.lock"; then
    run_lockrun "${out}" &
    pid=$!
    sleep 3
    if [ ! -e "${out}/args" ] && grep -q '^waiting for ' "${out}/lock.txt" 2> /dev/null; then
        ok "run.sh waits while another hold has ~/k3dgpu-bench.lock"
    else
        fail "run.sh started while another hold had ~/k3dgpu-bench.lock"
    fi
    kill "${holder}" 2> /dev/null
    wait "${holder}" 2> /dev/null
    holder=
    wait "${pid}"
    rc=$?
    if [ "${rc}" -eq 0 ] && [ -e "${out}/args" ]; then
        ok "run.sh starts once the other hold ends"
    else
        fail "after the other hold ended: rc ${rc}, args '$(cat "${out}/args" 2> /dev/null)'"
    fi
else
    fail "the test could not take ${WORK}/home/k3dgpu-bench.lock"
fi

# 4. K3DGPU_LIVE_LOCK (bench/admission/run.sh's variable) names the lock instead; the
# default path is then not touched.
out="${WORK}/o4"
rm -f "${WORK}/home/k3dgpu-bench.lock"
if hold "${WORK}/campaign.lock"; then
    run_lockrun "${out}" K3DGPU_LIVE_LOCK="${WORK}/campaign.lock" &
    pid=$!
    sleep 3
    [ -e "${out}/args" ] && fail "run.sh started while another hold had K3DGPU_LIVE_LOCK"
    kill "${holder}" 2> /dev/null
    wait "${holder}" 2> /dev/null
    holder=
    wait "${pid}"
    rc=$?
    if [ "${rc}" -eq 0 ] && [ "$(cat "${out}/seen" 2> /dev/null)" = held ] \
       && [ ! -e "${WORK}/home/k3dgpu-bench.lock" ]; then
        ok "K3DGPU_LIVE_LOCK names the lock taken"
    else
        fail "K3DGPU_LIVE_LOCK: rc ${rc}, seen '$(cat "${out}/seen" 2> /dev/null)'"
    fi
else
    fail "the test could not take ${WORK}/campaign.lock"
fi

# 5. The desktop probes that run under the same box lock document it as
# ~/k3dgpu-bench.lock, so a probe run as documented and the F3 runner exclude each other.
for f in bench/amx-autoquant/probe.sh bench/joule-pack/probe.sh \
         bench/hetero-spec/probe.sh bench/hetero-spec/h34.sh; do
    if grep -qE '^#.*flock .*~/k3dgpu-bench\.lock bench/' "${REPO}/${f}" \
       && ! grep -qE '^#.*(<scratch[p]ad>|<lockfile>|live\.lock)' "${REPO}/${f}"; then
        ok "${f} documents ~/k3dgpu-bench.lock"
    else
        fail "${f} documents another lock: $(grep -nE '^#.*flock ' "${REPO}/${f}")"
    fi
done

# 6. No bench script outside a results directory names a session-scoped path.
if hits=$(cd "${REPO}" && grep -rnE '/tmp/claude-[0-9]+/|<scratch[p]ad>' bench \
              --include='*.sh' --include='*.py' | grep -v '/results/'); then
    fail "bench scripts name a session-scoped path: ${hits}"
else
    ok "no bench script names a session-scoped path"
fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
