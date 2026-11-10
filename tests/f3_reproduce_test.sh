#!/usr/bin/env bash
# Tests for bench/f3/reproduce.sh (EXPERIMENTS.md, "F3 note N1") without k3d or a
# cluster: run.sh is a stub next to a copy of reproduce.sh, and HOME is a temporary
# directory, so no case touches the real ~/k3dgpu-bench.lock. Every reproduce.sh call
# runs under `timeout`, so a case that waits on a lock it should not wait on fails
# instead of hanging. Also checks that bench/f3/lockrun.sh is still the file that ran,
# and that only finished records name a session's scratchpad path.
#
# Usage: tests/f3_reproduce_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
F3="${ROOT}/bench/f3"
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

# 1. lockrun.sh is the record of the 2026-11-01 run: its sha256 is the one
# runner.sha256 recorded for the lockrun.sh that ran.
want=$(awk '$2 ~ /\/lockrun\.sh$/ {print $1}' "${F3}/results/2026-11-01/runner.sha256")
have=$(sha256sum "${F3}/lockrun.sh" | cut -d' ' -f1)
if [ -n "${want}" ] && [ "${want}" = "${have}" ]; then
    ok "bench/f3/lockrun.sh is byte-identical to the lockrun.sh that ran"
else
    fail "bench/f3/lockrun.sh sha256 ${have}, runner.sha256 records '${want}'"
fi

if [ ! -f "${F3}/reproduce.sh" ]; then
    fail "bench/f3/reproduce.sh does not exist"
    echo "${PASS} passed, ${FAILS} failed"
    exit 1
fi

# 2. The reproduce runner's code names no path under /tmp: a session's scratchpad there
# is gone after a reboot or once the session is cleaned up.
if hits=$(grep -nE '/tmp/|scratch[p]ad' "${F3}/reproduce.sh" | grep -vE '^[0-9]+:[[:space:]]*#'); then
    fail "reproduce.sh's code names a path under /tmp or a scratchpad: ${hits}"
else
    ok "reproduce.sh's code names no path under /tmp or a scratchpad"
fi

# 3. It runs run.sh as lockrun.sh did: the same backstop timeout and arguments, with the
# lock-taken epoch as the cap's start.
call='timeout --signal=TERM --kill-after=120 $(( $4 * 60 + 300 )) bash "$5/run.sh" "$1" "$2" "$3" "${t}" "$4"'
if grep -qF "${call}" "${F3}/lockrun.sh" && grep -qF "${call}" "${F3}/reproduce.sh"; then
    ok "reproduce.sh calls run.sh exactly as lockrun.sh does"
else
    fail "reproduce.sh and lockrun.sh call run.sh differently"
fi

mkdir -p "${WORK}/bin" "${WORK}/home"
cp "${F3}/reproduce.sh" "${WORK}/bin/reproduce.sh"
# The stub run.sh records its arguments, whether the lock is held while it runs, and
# the files its own descriptors name (flock -o: none is the lock).
cat > "${WORK}/bin/run.sh" << 'EOF'
#!/usr/bin/env bash
L=${K3DGPU_LIVE_LOCK:-${HOME}/k3dgpu-bench.lock}
if flock -n "${L}" true; then echo free; else echo held; fi > "$3/seen"
for fd in /proc/$$/fd/*; do readlink "${fd}"; done > "$3/fds"
echo "$*" > "$3/args"
EOF

# run_reproduce <out> [VAR=value ...]: reproduce.sh with HOME=${WORK}/home and no
# K3DGPU_LIVE_LOCK unless one is given, capped at 30 s.
run_reproduce() {
    local out=$1
    shift
    env -u K3DGPU_LIVE_LOCK HOME="${WORK}/home" "$@" \
        timeout 30 bash "${WORK}/bin/reproduce.sh" src k3dgpu-f3-test "${out}" 1
}

# hold <file>: hold a flock on <file> in the background until killed; sets holder.
hold() {
    ( exec 8> "$1"; flock 8; exec sleep 60 ) &
    holder=$!
    for _ in $(seq 1 50); do flock -n "$1" true || return 0; sleep 0.1; done
    return 1
}

# 4. With the default lock it runs run.sh with its arguments while holding
# ~/k3dgpu-bench.lock, logs the lock lines and releases the lock.
out="${WORK}/o4"
run_reproduce "${out}"
rc=$?
if [ "${rc}" -eq 0 ] && [ "$(cat "${out}/seen" 2> /dev/null)" = held ] \
   && grep -qE "^src k3dgpu-f3-test ${out} [0-9]+ 1$" "${out}/args" 2> /dev/null; then
    ok "default lock: run.sh ran with its arguments while ~/k3dgpu-bench.lock was held"
else
    fail "default lock: rc ${rc}, seen '$(cat "${out}/seen" 2> /dev/null)', args '$(cat "${out}/args" 2> /dev/null)'"
fi
if [ -s "${out}/fds" ] && ! grep -qF "k3dgpu-bench.lock" "${out}/fds"; then
    ok "run.sh holds no descriptor on the lock (flock -o)"
else
    fail "run.sh's descriptors: $(tr "\n" " " 2> /dev/null < "${out}/fds")"
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

# 5. A hold on ~/k3dgpu-bench.lock (the desktop holds' lock) keeps run.sh from starting
# until it ends.
out="${WORK}/o5"
if hold "${WORK}/home/k3dgpu-bench.lock"; then
    run_reproduce "${out}" &
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

# 6. K3DGPU_LIVE_LOCK (bench/admission/run.sh's variable) names the lock instead; the
# default path is then not touched.
out="${WORK}/o6"
rm -f "${WORK}/home/k3dgpu-bench.lock"
if hold "${WORK}/campaign.lock"; then
    run_reproduce "${out}" K3DGPU_LIVE_LOCK="${WORK}/campaign.lock" &
    pid=$!
    sleep 3
    started=no
    [ -e "${out}/args" ] && started=yes
    kill "${holder}" 2> /dev/null
    wait "${holder}" 2> /dev/null
    holder=
    wait "${pid}"
    rc=$?
    if [ "${started}" = no ] && [ "${rc}" -eq 0 ] && [ "$(cat "${out}/seen" 2> /dev/null)" = held ] \
       && [ ! -e "${WORK}/home/k3dgpu-bench.lock" ]; then
        ok "K3DGPU_LIVE_LOCK names the lock taken"
    else
        fail "K3DGPU_LIVE_LOCK: started early ${started}, rc ${rc}, seen '$(cat "${out}/seen" 2> /dev/null)'"
    fi
else
    fail "the test could not take ${WORK}/campaign.lock"
fi

# 7. A wrong argument count is a usage error that takes no lock and runs nothing.
out="${WORK}/o7"
env -u K3DGPU_LIVE_LOCK HOME="${WORK}/home" timeout 30 bash "${WORK}/bin/reproduce.sh" src k3dgpu-f3-test "${out}" \
    2> "${WORK}/usage.err"
rc=$?
if [ "${rc}" -eq 2 ] && [ ! -e "${out}" ] && grep -q '^usage: ' "${WORK}/usage.err"; then
    ok "three arguments: usage error (exit 2), nothing run"
else
    fail "three arguments: rc ${rc}, out exists $([ -e "${out}" ] && echo yes || echo no)"
fi

# 8. Only a finished record (pins/classes: historical) names a session's scratchpad
# path; a runner that can still run (ours or an arm) names none.
n=0 bad=0
while IFS= read -r f; do
    case "/${f}" in */results/*) continue ;; esac
    n=$((n + 1))
    kind=$(cd "${ROOT}" && python3 hack/pin-classes effective "${f}")
    if [ "${kind}" != historical ]; then
        bad=$((bad + 1))
        fail "${f} (${kind}) names a session's scratchpad path"
    fi
done < <(cd "${ROOT}" && git grep -lE '/tmp/claude-[0-9]+/|<scratch[p]ad>' -- bench)
if [ "${n}" -gt 0 ] && [ "${bad}" -eq 0 ]; then
    ok "the ${n} bench file(s) outside results/ that name a session's scratchpad path are all historical"
elif [ "${n}" -eq 0 ]; then
    fail "git grep found no bench file naming a scratchpad path (lockrun.sh should)"
fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
