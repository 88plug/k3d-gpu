#!/usr/bin/env bash
# Each crescent hold (bench/eatflow/crescent/hold.sh) owns its process tree: nothing a hold
# started outlives it, the host lock is free the moment it exits, a signal lets CMD run its
# own cleanup before anything is killed, the start sweep touches only this campaign's stale
# processes, and every campaign cluster is deleted at the end (shared rule 4).
#
# Runs hold.sh for real under a temporary HOME with stub docker, k3d and df on its PATH
# (T13, T14: and a timeout that is not GNU's).
# Every process a test starts is named c3t<pid>-* (exec -a) and only those are killed.
#
# Usage: tests/hold_own_test.sh

set -uo pipefail
[ -n "${C3_INNER:-}" ] || exec timeout -k 10 600 env C3_INNER=1 bash "$0" "$@"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOLD=${ROOT}/bench/eatflow/crescent/hold.sh
P=c3t$$
CUT=$(command -v cut)
# GNU's timeout, for T13's stubs (gnutimeout where uutils' is the default, Ubuntu 25.10+).
GNUTO=$(command -v gnutimeout || command -v timeout)
WORK=$(mktemp -d)
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }
mine() { pgrep -f "^${P}-" || true; }
owners=()
cleanup() {
    local p
    p=$(mine)
    [ -z "${p}" ] || kill -KILL ${p} 2>/dev/null
    [ "${#owners[@]}" -eq 0 ] || kill -KILL "${owners[@]}" 2>/dev/null
    rm -rf "${WORK}"
}
trap cleanup EXIT

# newhome NAME: a fresh HOME with the stubs; prints its path.
newhome() {
    local h=${WORK}/$1 b
    b=${h}/k3dgpu-bench/bin
    mkdir -p "${b}"
    printf '#!/bin/sh\nexit 0\n' > "${b}/docker"
    printf '#!/bin/sh\necho "Filesystem 1024-blocks Used Available Capacity Mounted"\necho "/dev/stub 100 10 90 10%% /"\n' > "${b}/df"
    # The hold-start rule reads load1 with cut; report an idle host so a busy test runner
    # does not wait out the 120 s polls. Every other cut call goes to the real one.
    # A test that needs a busy host writes load1 to $HOME/load1.
    printf '#!/bin/sh\n[ "$3" = /proc/loadavg ] && { cat "$HOME/load1" 2>/dev/null || echo 0.00; exit 0; }\nexec %s "$@"\n' "${CUT}" > "${b}/cut"
    cat > "${b}/k3d" <<'EOF'
#!/usr/bin/env bash
# Stub k3d: the cluster list lives in $HOME/k3d.list; every delete is logged.
L=${HOME}/k3d.list
touch "${L}"
case "$1 $2" in
    "cluster list") python3 -c 'import json,sys; print(json.dumps([{"name": n} for n in open(sys.argv[1]).read().split()]))' "${L}" ;;
    "cluster create") echo "$3" >> "${L}" ;;
    "cluster delete") shift 2
        for n in "$@"; do echo "${n}" >> "${HOME}/k3d.deletes"; grep -vxF "${n}" "${L}" > "${L}.t"; mv "${L}.t" "${L}"; done ;;
esac
EOF
    chmod +x "${b}"/*
    : > "${h}/k3d.list"
    echo "${h}"
}
lockfree() { flock -n "$1/k3dgpu-bench.lock" true; }
starttime() { awk '{sub(/.*\) /, ""); print $20}' "/proc/$1/stat"; }
# hold HOME NAME CMD...: one hold, foreground.
hold() { local h=$1; shift; HOME=${h} bash "${HOLD}" "$1" 60 "${@:2}" > "${h}/hold.out" 2>&1; }

# T1: a clean hold leaves the lock free at once (the watchdog's sleep must not keep fd 9).
h=$(newhome t1)
hold "${h}" t1 bash -c 'exit 0'
if lockfree "${h}"; then ok "T1 lock free right after a clean hold"; else fail "T1 lock held after a clean hold: $(lslocks -n -o PID,COMMAND,PATH | grep -F "${h}")"; fi

# T2: a background child of CMD neither keeps the lock nor survives the hold.
h=$(newhome t2)
hold "${h}" t2 bash -c "(exec -a ${P}-t2 sleep 30) & exit 0"
if lockfree "${h}"; then ok "T2 lock free after CMD left a background child"; else fail "T2 lock held by: $(lslocks -n -o PID,COMMAND,PATH | grep -F "${h}")"; fi
if [ -z "$(pgrep -f "^${P}-t2")" ]; then ok "T2 background child killed"; else fail "T2 background child survived"; fi

# T3: the lock is still held while CMD runs.
h=$(newhome t3)
hold "${h}" t3 bash -c "flock -n '${h}/k3dgpu-bench.lock' true && echo MIDHOLD_FREE; exit 0"
if grep -q MIDHOLD_FREE "${h}/k3dgpu-bench/runs/t3/hold.log"; then fail "T3 lock free mid-hold"; else ok "T3 lock held mid-hold"; fi

# T4, T5: orphans_killed counts tagged processes alive after the group kill: 0 for a
# clean CMD, 1 for one plain child (dies in the group kill) plus one setsid escapee.
for i in 1 2 3; do
    h=$(newhome "t4-${i}")
    hold "${h}" t4 bash -c 'exit 0'
    v=$(cat "${h}/k3dgpu-bench/runs/t4/orphans_killed" 2>/dev/null || echo missing)
    if [ "${v}" = 0 ]; then ok "T4.${i} orphans_killed = 0 for a clean hold"; else fail "T4.${i} orphans_killed = ${v}, want 0"; fi
    h=$(newhome "t5-${i}")
    hold "${h}" t5 bash -c "(exec -a ${P}-t5a sleep 30) & setsid nohup bash -c 'exec -a ${P}-t5b sleep 30' > /dev/null 2>&1 & sleep 0.5; exit 0"
    v=$(cat "${h}/k3dgpu-bench/runs/t5/orphans_killed" 2>/dev/null || echo missing)
    if [ "${v}" = 1 ]; then ok "T5.${i} orphans_killed = 1 for one escapee"; else fail "T5.${i} orphans_killed = ${v}, want 1"; fi
    if [ -z "$(pgrep -f "^${P}-t5")" ]; then ok "T5.${i} no process of the hold left"; else fail "T5.${i} survivors: $(pgrep -af "^${P}-t5" | tr '\n' ' ')"; kill -KILL $(pgrep -f "^${P}-t5") 2>/dev/null; fi
done

# T6: SIGTERM to hold.sh mid-hold lets CMD's own EXIT trap run to the end (2 s of cleanup, as
# live.sh's docker rm and agent kills need) before the tree is reaped, the lock freed and the
# end record written.
h=$(newhome t6)
HOME=${h} bash "${HOLD}" t6 600 bash -c "trap 'echo begin >> ${h}/t6.trace; sleep 2; echo done >> ${h}/t6.trace' EXIT; (exec -a ${P}-t6a sleep 300) & touch '${h}/t6.started'; (exec -a ${P}-t6b sleep 300)" > "${h}/hold.out" 2>&1 &
hp=$!
for _ in $(seq 300); do [ -f "${h}/t6.started" ] && break; sleep 0.1; done
sleep 0.5
kill -TERM "${hp}"
for _ in $(seq 450); do kill -0 "${hp}" 2>/dev/null || break; sleep 0.1; done
if kill -0 "${hp}" 2>/dev/null; then fail "T6 hold.sh still running 45 s after SIGTERM"; kill -KILL "${hp}"; else ok "T6 hold.sh exited after SIGTERM"; fi
wait "${hp}" 2>/dev/null
v=$(tr '\n' ' ' < "${h}/t6.trace" 2>/dev/null)
if [ "${v}" = "begin done " ]; then ok "T6 CMD's EXIT trap ran to the end"; else fail "T6 CMD's EXIT trap trace = '${v}', want 'begin done '"; fi
if [ -z "$(pgrep -f "^${P}-t6")" ]; then ok "T6 no process of the hold left"; else fail "T6 survivors: $(pgrep -af "^${P}-t6" | tr '\n' ' ')"; fi
if lockfree "${h}"; then ok "T6 lock free after SIGTERM"; else fail "T6 lock held after SIGTERM"; fi
v=$(cat "${h}/k3dgpu-bench/runs/t6/rc" 2>/dev/null || echo missing)
if [ "${v}" = 143 ]; then ok "T6 rc = 143 (CMD's own status)"; else fail "T6 rc = ${v}, want 143"; fi
if grep -q '"ev": "end"' "${h}/k3dgpu-bench/runs/holds.log" 2>/dev/null; then ok "T6 end record written"; else fail "T6 no end record in holds.log"; fi

# T7: the start sweep kills this campaign's tagged process whose owner is dead, and keeps one
# whose owner lives, one with an unparseable tag and one of another campaign's directory.
h=$(newhome t7)
RUNS=${h}/k3dgpu-bench/runs
bash -c "exec -a ${P}-owner sleep 300" &
op=$!; owners+=("${op}")
sleep 0.2
ost=$(starttime "${op}")
(K3DGPU_HOLD=old:999999:1 K3DGPU_HOLD_RUNS=${RUNS} setsid bash -c "exec -a ${P}-t7dead sleep 300" > /dev/null 2>&1 &)
(K3DGPU_HOLD="sib:${op}:${ost}" K3DGPU_HOLD_RUNS=${RUNS} setsid bash -c "exec -a ${P}-t7live sleep 300" > /dev/null 2>&1 &)
(K3DGPU_HOLD=notatag K3DGPU_HOLD_RUNS=${RUNS} setsid bash -c "exec -a ${P}-t7junk sleep 300" > /dev/null 2>&1 &)
(K3DGPU_HOLD=old:999999:1 K3DGPU_HOLD_RUNS=${WORK}/other/runs setsid bash -c "exec -a ${P}-t7other sleep 300" > /dev/null 2>&1 &)
sleep 0.3
hold "${h}" t7 bash -c 'exit 0'
if [ -z "$(pgrep -f "^${P}-t7dead")" ]; then ok "T7 dead owner's tagged process swept"; else fail "T7 dead owner's tagged process survived"; fi
if [ -n "$(pgrep -f "^${P}-t7live")" ]; then ok "T7 live owner's tagged process kept"; else fail "T7 live owner's tagged process killed"; fi
if [ -n "$(pgrep -f "^${P}-t7junk")" ]; then ok "T7 unparseable tag kept"; else fail "T7 unparseable tag killed"; fi
if [ -n "$(pgrep -f "^${P}-t7other")" ]; then ok "T7 another directory's tagged process kept"; else fail "T7 another directory's tagged process killed"; fi
v=$(cat "${RUNS}/t7/stale_reaped" 2>/dev/null || echo missing)
if [ "${v}" = 1 ]; then ok "T7 stale_reaped = 1"; else fail "T7 stale_reaped = ${v}, want 1"; fi
kill -KILL $(pgrep -f "^${P}-t7") 2>/dev/null

# T8: shared rule 4: every cluster of this campaign is deleted, the leftovers at start and the
# hold's own at end, also when CMD fails; another campaign's cluster is untouched.
h=$(newhome t8)
printf '%s\n' k3dgpu-e-h50 k3dgpu-a-b1 k3dgpu-h52 k3dgpu-x-foo > "${h}/k3d.list"
hold "${h}" t8 bash -c 'k3d cluster create k3dgpu-e-h50o; exit 3'
d=$(tr '\n' ' ' < "${h}/k3d.deletes" 2>/dev/null)
for c in k3dgpu-e-h50 k3dgpu-a-b1 k3dgpu-h52 k3dgpu-e-h50o; do
    if grep -qxF "${c}" "${h}/k3d.deletes" 2>/dev/null; then ok "T8 ${c} deleted"; else fail "T8 ${c} not deleted (deletes: ${d})"; fi
done
if grep -qxF k3dgpu-x-foo "${h}/k3d.list" && ! grep -qxF k3dgpu-x-foo "${h}/k3d.deletes" 2>/dev/null; then
    ok "T8 other campaign's cluster untouched"; else fail "T8 other campaign's cluster deleted"; fi

# T9: the reap matches the tag as a fixed string: a decoy whose tag differs only where a
# regex would wildcard ('.' in the hold's tag, ':' in the decoy's) survives it.
h=$(newhome t9)
hold "${h}" c3.t9 bash -c "K3DGPU_HOLD=\${K3DGPU_HOLD/./:} setsid bash -c 'exec -a ${P}-t9 sleep 300' > /dev/null 2>&1 & sleep 0.3; exit 0"
if [ -n "$(pgrep -f "^${P}-t9")" ]; then ok "T9 near-miss tag not reaped"; else fail "T9 near-miss tag reaped (regex match)"; fi
kill -KILL $(pgrep -f "^${P}-t9") 2>/dev/null

# T10: holds.log has exactly one start and one end record for the hold's tag.
h=$(newhome t10)
hold "${h}" t10 bash -c 'exit 0'
if python3 - "${h}/k3dgpu-bench/runs" <<'EOF'
import json, sys
runs = sys.argv[1]
recs = [json.loads(ln) for ln in open(f"{runs}/holds.log") if ln.strip()]
tag = [r["tag"] for r in recs if r.get("name") == "t10"][0]
s = [r for r in recs if r.get("tag") == tag and r.get("ev") == "start"]
e = [r for r in recs if r.get("tag") == tag and r.get("ev") == "end"]
assert len(s) == 1 and len(e) == 1, (s, e)
assert all(k in e[0] for k in ("rc", "orphans_killed", "stale_reaped", "reap_residue")), e
assert e[0]["rc"] == "0", e
EOF
then ok "T10 one start and one end record"; else fail "T10 holds.log records"; fi
if python3 "${ROOT}/bench/eatflow/crescent/holds.py" check "${h}/k3dgpu-bench/runs/holds.log" > /dev/null 2>&1; then
    ok "T10 holds.py check passes on the hold's records"; else fail "T10 holds.py check rejects the hold's records"; fi

# T11: a CMD that ignores TERM is SIGKILLed by timeout -k 30, so a signalled hold still ends.
h=$(newhome t11)
HOME=${h} bash "${HOLD}" t11 600 bash -c "trap '' TERM; touch '${h}/t11.started'; (exec -a ${P}-t11 sleep 300)" > "${h}/hold.out" 2>&1 &
hp=$!
for _ in $(seq 300); do [ -f "${h}/t11.started" ] && break; sleep 0.1; done
sleep 0.5
kill -TERM "${hp}"
t0=${SECONDS}
for _ in $(seq 600); do kill -0 "${hp}" 2>/dev/null || break; sleep 0.1; done
dt=$(( SECONDS - t0 ))
if kill -0 "${hp}" 2>/dev/null; then fail "T11 hold.sh still running 60 s after SIGTERM"; kill -KILL "${hp}"
elif [ "${dt}" -ge 25 ]; then ok "T11 TERM-ignoring CMD stopped by timeout -k 30 (${dt} s)"
else fail "T11 hold.sh ended after ${dt} s, before timeout's 30 s grace"; fi
wait "${hp}" 2>/dev/null
if [ -z "$(pgrep -f "^${P}-t11")" ]; then ok "T11 no process of the hold left"; else fail "T11 survivors"; kill -KILL $(pgrep -f "^${P}-t11") 2>/dev/null; fi

# T12: SIGTERM while the start rule waits on a busy host ends the hold at once, before CMD.
h=$(newhome t12)
echo 99.00 > "${h}/load1"
HOME=${h} bash "${HOLD}" t12 60 bash -c "touch '${h}/t12.ran'" > "${h}/hold.out" 2>&1 &
hp=$!
for _ in $(seq 300); do grep -q busy "${h}/hold.out" 2>/dev/null && break; sleep 0.1; done
sleep 0.3
kill -TERM "${hp}"
for _ in $(seq 100); do kill -0 "${hp}" 2>/dev/null || break; sleep 0.1; done
if kill -0 "${hp}" 2>/dev/null; then fail "T12 hold.sh still waiting 10 s after SIGTERM"; kill -KILL "${hp}"; else ok "T12 hold.sh left the busy wait on SIGTERM"; fi
wait "${hp}"; v=$?
if [ "${v}" = 143 ]; then ok "T12 rc = 143"; else fail "T12 rc = ${v}, want 143"; fi
if [ ! -e "${h}/t12.ran" ]; then ok "T12 CMD never started"; else fail "T12 CMD started after SIGTERM"; fi
if lockfree "${h}"; then ok "T12 lock free"; else fail "T12 lock held: $(lslocks -n -o PID,COMMAND,PATH | grep -F "${h}")"; fi

# uutimeout FILE: a timeout that exits as uutils coreutils 0.10's does (the default timeout on
# Ubuntu 26.04): signalled while -k is set, it passes the signal on, waits for CMD and exits
# 124 whatever CMD's own status. Its other paths run GNU's.
uutimeout() {
    cat > "$1" <<EOF
#!/usr/bin/env bash
[ "\$1" = --version ] && { echo 'timeout (uutils coreutils) 0.10.0'; exit 0; }
trap 'kill -TERM "\${c}" 2>/dev/null; wait "\${c}"; exit 124' TERM
${GNUTO} "\$@" &
c=\$!
wait "\${c}"
EOF
    chmod +x "$1"
}

# T13: with uutils' timeout first on PATH and GNU's as gnutimeout (Ubuntu 26.04), a hold
# signalled mid-CMD still records CMD's own status, not 124.
h=$(newhome t13)
uutimeout "${h}/k3dgpu-bench/bin/timeout"
printf '#!/bin/sh\nexec %s "$@"\n' "${GNUTO}" > "${h}/k3dgpu-bench/bin/gnutimeout"
chmod +x "${h}/k3dgpu-bench/bin/gnutimeout"
HOME=${h} bash "${HOLD}" t13 600 bash -c "trap 'sleep 1' EXIT; touch '${h}/t13.started'; (exec -a ${P}-t13 sleep 300)" > "${h}/hold.out" 2>&1 &
hp=$!
for _ in $(seq 300); do [ -f "${h}/t13.started" ] && break; sleep 0.1; done
sleep 0.5
kill -TERM "${hp}"
for _ in $(seq 450); do kill -0 "${hp}" 2>/dev/null || break; sleep 0.1; done
if kill -0 "${hp}" 2>/dev/null; then fail "T13 hold.sh still running 45 s after SIGTERM"; kill -KILL "${hp}"; fi
wait "${hp}" 2>/dev/null
v=$(cat "${h}/k3dgpu-bench/runs/t13/rc" 2>/dev/null || echo missing)
if [ "${v}" = 143 ]; then ok "T13 rc = 143 with uutils' timeout first on PATH"; else fail "T13 rc = ${v}, want 143"; fi
if [ -z "$(pgrep -f "^${P}-t13")" ]; then ok "T13 no process of the hold left"; else fail "T13 survivors"; kill -KILL $(pgrep -f "^${P}-t13") 2>/dev/null; fi

# T14: with no GNU timeout on PATH, hold.sh ends with rc 2 before the lock: no CMD, no record.
h=$(newhome t14)
uutimeout "${h}/k3dgpu-bench/bin/timeout"
uutimeout "${h}/k3dgpu-bench/bin/gnutimeout"
HOME=${h} bash "${HOLD}" t14 60 bash -c "touch '${h}/t14.ran'" > "${h}/hold.out" 2>&1; v=$?
if [ "${v}" = 2 ]; then ok "T14 rc = 2 without a GNU timeout"; else fail "T14 rc = ${v}, want 2"; fi
if [ ! -e "${h}/t14.ran" ]; then ok "T14 CMD never started"; else fail "T14 CMD ran under a timeout that is not GNU's"; fi
if [ ! -s "${h}/k3dgpu-bench/runs/holds.log" ]; then ok "T14 no hold record"; else fail "T14 holds.log has records"; fi

# T15: a stop sends TERM to CMD alone, and once. A bash CMD exits at once on a TERM that lands
# while its EXIT trap runs (bash >= 5.2.14); timeout's group send was such a second TERM
# whenever timeout was preempted between its two sends. hold.sh gets TERM, then HUP while
# CMD's EXIT trap runs: the trap still ends, and CMD's background child sees no TERM (the
# reap kills it).
h=$(newhome t15)
printf 'trap "echo TERM >> %s/t15.child; exit 0" TERM\n(exec -a %s-t15s sleep 300) &\nwait\n' "${h}" "${P}" > "${h}/child.sh"
HOME=${h} bash "${HOLD}" t15 600 bash -c "trap 'echo begin >> ${h}/t15.trace; sleep 2; echo done >> ${h}/t15.trace' EXIT; (exec -a ${P}-t15c bash '${h}/child.sh') & touch '${h}/t15.started'; (exec -a ${P}-t15 sleep 300)" > "${h}/hold.out" 2>&1 &
hp=$!
for _ in $(seq 300); do [ -f "${h}/t15.started" ] && break; sleep 0.1; done
sleep 0.5
kill -TERM "${hp}"
for _ in $(seq 100); do grep -q begin "${h}/t15.trace" 2>/dev/null && break; sleep 0.1; done
kill -HUP "${hp}"
for _ in $(seq 450); do kill -0 "${hp}" 2>/dev/null || break; sleep 0.1; done
if kill -0 "${hp}" 2>/dev/null; then fail "T15 hold.sh still running 45 s after SIGTERM"; kill -KILL "${hp}"; fi
wait "${hp}" 2>/dev/null
v=$(tr '\n' ' ' < "${h}/t15.trace" 2>/dev/null)
if [ "${v}" = "begin done " ]; then ok "T15 CMD's EXIT trap ran to the end after TERM then HUP"; else fail "T15 CMD's EXIT trap trace = '${v}', want 'begin done '"; fi
if [ ! -e "${h}/t15.child" ]; then ok "T15 CMD's background child got no TERM"; else fail "T15 CMD's background child got TERM"; fi
v=$(cat "${h}/k3dgpu-bench/runs/t15/rc" 2>/dev/null || echo missing)
if [ "${v}" = 143 ]; then ok "T15 rc = 143"; else fail "T15 rc = ${v}, want 143"; fi
if [ -z "$(pgrep -f "^${P}-t15")" ]; then ok "T15 no process of the hold left"; else fail "T15 survivors: $(pgrep -af "^${P}-t15" | tr '\n' ' ')"; kill -KILL $(pgrep -f "^${P}-t15") 2>/dev/null; fi

# T16: the disk watchdog (30 s polls) stops CMD through hold.sh's own signal path: disk-abort
# is recorded, CMD's EXIT trap runs to the end and the hold records CMD's status.
h=$(newhome t16)
echo 10 > "${h}/disk"
printf '#!/bin/sh\necho "Filesystem 1024-blocks Used Available Capacity Mounted"\necho "/dev/stub 100 10 90 $(cat "$HOME/disk")%% /"\n' > "${h}/k3dgpu-bench/bin/df"
HOME=${h} bash "${HOLD}" t16 600 bash -c "trap 'echo begin >> ${h}/t16.trace; sleep 1; echo done >> ${h}/t16.trace' EXIT; touch '${h}/t16.started'; (exec -a ${P}-t16 sleep 300)" > "${h}/hold.out" 2>&1 &
hp=$!
for _ in $(seq 300); do [ -f "${h}/t16.started" ] && break; sleep 0.1; done
echo 99 > "${h}/disk"
for _ in $(seq 900); do kill -0 "${hp}" 2>/dev/null || break; sleep 0.1; done
if kill -0 "${hp}" 2>/dev/null; then fail "T16 hold.sh still running 90 s after the disk filled"; kill -KILL "${hp}"; fi
wait "${hp}" 2>/dev/null
if [ -s "${h}/k3dgpu-bench/runs/t16/disk-abort" ]; then ok "T16 disk-abort recorded"; else fail "T16 no disk-abort"; fi
v=$(tr '\n' ' ' < "${h}/t16.trace" 2>/dev/null)
if [ "${v}" = "begin done " ]; then ok "T16 CMD's EXIT trap ran to the end"; else fail "T16 CMD's EXIT trap trace = '${v}', want 'begin done '"; fi
v=$(cat "${h}/k3dgpu-bench/runs/t16/rc" 2>/dev/null || echo missing)
if [ "${v}" = 143 ]; then ok "T16 rc = 143"; else fail "T16 rc = ${v}, want 143"; fi
if [ -z "$(pgrep -f "^${P}-t16")" ]; then ok "T16 no process of the hold left"; else fail "T16 survivors"; kill -KILL $(pgrep -f "^${P}-t16") 2>/dev/null; fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
