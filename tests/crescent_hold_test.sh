#!/usr/bin/env bash
# crescent_hold_test.sh: bench/eatflow/crescent/hold.sh's locks, gate and cleanup scope
# (EXPERIMENTS.md "H56 pre-registration"). Real flock and taskset; docker, k3d and df are
# shims over registry files; /proc/stat and sysfs are fakes for the slotted gate and the
# slices. No root, no docker daemon, no cluster.
#
# Usage: tests/crescent_hold_test.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOLD=${ROOT}/bench/eatflow/crescent/hold.sh
H56SH=${ROOT}/bench/eatflow/crescent/h56.sh
LOADPY=${ROOT}/bench/load/load.py
REAL_CUT=$(command -v cut)
NPROC=$(nproc)
PASS=0 FAILS=0 SKIPS=0
T=$(mktemp -d)
BG=()
bgkill() {
    local p
    for p in ${BG[@]+"${BG[@]}"}; do kill -9 "${p}" 2>/dev/null; wait "${p}" 2>/dev/null; done
    BG=()
}
cleanup() {
    local p
    bgkill
    rm -rf "${T}"
}
trap cleanup EXIT

# fresh: a new HOME with shims, registries and fakes. Clusters and load ids as args:
# fresh "k3dgpu-a-s0-a1 k3dgpu-x-h34" "l0:0 l1:1 lx:-"
fresh() {
    local c id
    bgkill
    STATW=
    W=$(mktemp -d "${T}/w.XXXX")
    export HOME=${W}/home SHIM_DIR=${W}
    mkdir -p "${HOME}/k3dgpu-bench/bin" "${W}/proc" "${W}/sys/devices/system/cpu"
    : > "${W}/clusters.reg"
    : > "${W}/load.reg"
    : > "${W}/calls.log"
    for c in ${1:-}; do echo "${c}" >> "${W}/clusters.reg"; done
    for id in ${2:-}; do echo "${id%%:*} ${id##*:}" >> "${W}/load.reg"; done
    local bin=${HOME}/k3dgpu-bench/bin
    cat > "${bin}/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "${SHIM_DIR}/calls.log"
case $1 in
    top) exit 1 ;;
    ps)
        flt=() q=0
        while [ $# -gt 0 ]; do
            case $1 in --filter) flt+=("$2"); shift ;; -q) q=1 ;; esac
            shift
        done
        want=all
        for f in ${flt[@]+"${flt[@]}"}; do
            case ${f} in label=io.k3d-gpu.bench-load.slot=*) want=${f##*=} ;;
                         label=io.k3d-gpu.bench-load=1) ;; *) exit 0 ;; esac
        done
        while read -r id slot; do
            [ "${want}" = all ] || [ "${want}" = "${slot}" ] || continue
            if [ "${q}" = 1 ]; then echo "${id}"; else echo "${id} typical 9999999999"; fi
        done < "${SHIM_DIR}/load.reg" ;;
    rm)
        shift
        for id in "$@"; do [ "${id}" = -f ] || sed -i "/^${id} /d" "${SHIM_DIR}/load.reg"; done ;;
esac
exit 0
EOF
    cat > "${bin}/k3d" <<'EOF'
#!/usr/bin/env bash
echo "k3d $*" >> "${SHIM_DIR}/calls.log"
if [ "$1 $2" = "cluster list" ]; then
    python3 -c 'import json,sys; print(json.dumps([{"name": l.strip()} for l in open(sys.argv[1]) if l.strip()]))' \
        "${SHIM_DIR}/clusters.reg"
elif [ "$1 $2" = "cluster delete" ]; then
    shift 2
    for c in "$@"; do sed -i "/^${c}\$/d" "${SHIM_DIR}/clusters.reg"; done
fi
EOF
    # df: 50% full unless ${SHIM_DIR}/df_pct says otherwise.
    printf '#!/usr/bin/env bash\np=$(cat "${SHIM_DIR}/df_pct" 2>/dev/null || echo 50)\necho "Filesystem 1024-blocks Used Available Capacity Mounted on"\necho "/dev/x 100 $p $((100 - p)) $p%% /"\n' > "${bin}/df"
    # The legacy gate reads load1 with `cut ... /proc/loadavg`; the test host's own load is not the subject.
    printf '#!/usr/bin/env bash\n[ "${!#}" = /proc/loadavg ] && { echo 0.00; exit 0; }\nexec %s "$@"\n' "${REAL_CUT}" > "${bin}/cut"
    chmod +x "${bin}"/*
    # Fake sysfs: 4 CPUs, no SMT (slice 0 = 0-1, load 1; slice 1 = 2-3, load 3).
    echo 0-3 > "${W}/sys/devices/system/cpu/online"
    for c in 0 1 2 3; do mkdir -p "${W}/sys/devices/system/cpu/cpu${c}/topology"; echo "${c}" > "${W}/sys/devices/system/cpu/cpu${c}/topology/thread_siblings_list"; done
    export HOLD_SYSFS=${W}/sys HOLD_PROC=${W}/proc HOLD_GATE_S=0.3 HOLD_POLL_S=1 HOLD_GATE_MAX_S=1 SERVING_STEP=0.2
    unset HOLD_SLOT HOLD_SLOTS HOLD_LOCKS_HELD
    stat_writer ""
}

# stat_writer "BUSY CPUS": a fake /proc/stat whose counters grow every 0.05 s; listed CPUs busy, the rest idle.
STATW=
stat_writer() {
    local busy=" $1 "
    [ -z "${STATW}" ] || { kill -9 "${STATW}" 2>/dev/null; wait "${STATW}" 2>/dev/null; }
    ( k=0
      while :; do
          k=$((k + 1))
          { echo "cpu  0 0 0 0 0 0 0 0"
            for ((c = 0; c < NPROC; c++)); do
                if [[ "${busy}" == *" ${c} "* ]]; then echo "cpu${c} $((k * 10)) 0 0 1 0 0 0 0"; else echo "cpu${c} 0 0 0 $((k * 10)) 0 0 0 0"; fi
            done; } > "${W}/proc/stat.tmp"
          mv "${W}/proc/stat.tmp" "${W}/proc/stat"
          sleep 0.05
      done ) &
    STATW=$!
    BG+=("${STATW}")
    for _ in $(seq 50); do [ -s "${W}/proc/stat" ] && break; sleep 0.05; done
}

now() { date +%s.%N; }
elapsed_lt() { awk -v a="$1" -v b="$2" -v m="$3" 'BEGIN {exit !(b - a < m)}'; }
wait_log() {  # wait_log PATTERN SECONDS
    local end=$(( $(date +%s) + $2 ))
    until grep -q -- "$1" "${HOME}/k3dgpu-bench/runs/campaign.log" 2>/dev/null; do
        [ "$(date +%s)" -lt "${end}" ] || return 1
        sleep 0.1
    done
}
regs() { tr '\n' ' ' < "${W}/clusters.reg" | sed 's/ $//'; }

check() {
    local name=$1 t0=${SECONDS}
    shift
    if "$@" > "${T}/check.out" 2>&1; then
        PASS=$((PASS + 1)); echo "ok   ${name} ($((SECONDS - t0)) s)"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name}"
        sed 's/^/  | /' "${T}/check.out"
    fi
}

legacy_default_unchanged() {
    fresh "k3dgpu-a-a1 k3dgpu-e-h50 k3dgpu-a-s1-a1 k3dgpu-x-h34" "l0:0 lx:-"
    timeout 60 "${HOLD}" L 30 sh -c 'if flock -n -s "$HOME/k3dgpu-bench.lock" true; then echo FREE; else echo HELD; fi > "$SHIM_DIR/m"'
    [ "$(cat "${W}/m")" = HELD ] || { echo "main lock not exclusive under a legacy hold"; return 1; }
    [ "$(regs)" = "k3dgpu-x-h34" ] || { echo "left: $(regs)"; return 1; }
    [ ! -s "${W}/load.reg" ] || { echo "legacy stop left load: $(cat "${W}/load.reg")"; return 1; }
    [ ! -e "${HOME}/k3dgpu-bench.lock.d" ] || { echo "legacy hold made slot files"; return 1; }
}

slot1_starts_while_slot0_runs() {
    fresh
    HOLD_SLOT=0 timeout 60 "${HOLD}" S0 30 sleep 5 &
    BG+=($!)
    wait_log "S0 start:" 20 || { echo "slot-0 hold did not start"; return 1; }
    local t0 t1
    t0=$(now)
    HOLD_SLOT=1 timeout 60 "${HOLD}" S1 30 sh -c 'date +%s.%N > "$SHIM_DIR/m1"'
    t1=$(cat "${W}/m1" 2>/dev/null || echo 1e12)
    elapsed_lt "${t0}" "${t1}" 2 || { echo "slot-1 CMD started $(awk -v a="${t0}" -v b="${t1}" 'BEGIN {print b - a}') s after"; return 1; }
    grep -q "S0 rc=" "${HOME}/k3dgpu-bench/runs/campaign.log" && { echo "slot 0 had already finished"; return 1; }
    return 0
}

slot_cleanup_scoped() {
    fresh "k3dgpu-a-s0-a1 k3dgpu-a-s1-a1 k3dgpu-x-h34"
    HOLD_SLOT=1 timeout 60 "${HOLD}" S1 30 true
    [ "$(regs)" = "k3dgpu-a-s0-a1 k3dgpu-x-h34" ] || { echo "left: $(regs)"; return 1; }
}

slot_load_stop_scoped() {
    fresh "" "l0:0 l1a:1 l1b:1"
    HOLD_SLOT=1 timeout 60 "${HOLD}" S1 30 true
    [ "$(cut -d' ' -f1 "${W}/load.reg" | tr '\n' ' ')" = "l0 " ] || { echo "load left: $(cat "${W}/load.reg")"; return 1; }
    grep -q '^docker rm -f l1a l1b$' "${W}/calls.log" || { echo "no scoped rm:"; grep 'docker rm' "${W}/calls.log"; return 1; }
}

x_exclusive_excludes_slots() {
    fresh
    flock -x "${HOME}/k3dgpu-bench.lock" sleep 3 &
    BG+=($!)
    sleep 0.3
    local t0 a b
    t0=$(now)
    HOLD_SLOT=0 timeout 60 "${HOLD}" S0 30 sh -c 'date +%s.%N > "$SHIM_DIR/m0"; sleep 4' &
    BG+=($!)
    HOLD_SLOT=1 timeout 60 "${HOLD}" S1 30 sh -c 'date +%s.%N > "$SHIM_DIR/m1"; sleep 4' &
    BG+=($!)
    for _ in $(seq 100); do [ -s "${W}/m0" ] && [ -s "${W}/m1" ] && break; sleep 0.1; done
    a=$(cat "${W}/m0" 2>/dev/null || echo 1e12) b=$(cat "${W}/m1" 2>/dev/null || echo 1e12)
    elapsed_lt "${t0}" "${a}" 2.4 && { echo "slot 0 started while an exclusive holder ran"; return 1; }
    elapsed_lt "${t0}" "${a}" 5.5 || { echo "slot 0 did not start within the bound"; return 1; }
    elapsed_lt "${t0}" "${b}" 5.5 || { echo "slot 1 did not start within the bound (both slots must run together)"; return 1; }
    if flock -n -x "${HOME}/k3dgpu-bench.lock" true; then echo "exclusive taken while slot holds ran"; return 1; fi
    return 0
}

queued_slot_hold_does_not_hold_main() {
    fresh
    HOLD_SLOT=0 timeout 60 "${HOLD}" A 30 sleep 4 &
    BG+=($!)
    wait_log "A start:" 20 || { echo "A did not start"; return 1; }
    HOLD_SLOT=0 timeout 60 "${HOLD}" B 30 true &
    BG+=($!)
    wait_log "B waiting for the lock" 10 || { echo "B did not queue"; return 1; }
    sleep 0.5
    local ino n
    ino=$(stat -c %i "${HOME}/k3dgpu-bench.lock")
    n=$(grep -v -- '->' /proc/locks | grep -c ":${ino} ")
    [ "${n}" = 1 ] || { echo "${n} holders of the main lock while B is queued (want 1, A's):"; grep ":${ino} " /proc/locks; return 1; }
}

kill9_epoch_recovery() {
    fresh
    local mark="sleep 41.${RANDOM}"
    HOLD_SLOT=0 setsid "${HOLD}" K0 60 sh -c "echo k3dgpu-a-s0-a1 >> \"\$SHIM_DIR/clusters.reg\"; ${mark}" &
    local pid=$!
    BG+=("${pid}")
    for _ in $(seq 100); do grep -q k3dgpu-a-s0-a1 "${W}/clusters.reg" && pgrep -f "${mark}" >/dev/null && break; sleep 0.1; done
    # SIGKILL hold.sh's own group only (it, the sampler, the watchdog). CMD runs in its own
    # setsid group with no lock fd, so it lives on as a tagged orphan and slot 0's lock drops.
    kill -9 -- "-${pid}" 2>/dev/null
    wait "${pid}" 2>/dev/null
    pgrep -f "${mark}" >/dev/null || { echo "CMD did not outlive its hold"; return 1; }
    flock -n -x "${HOME}/k3dgpu-bench.lock.d/slot-0" true || { echo "slot-0 lock still held after the kill"; return 1; }
    # The next hold, in either slot, sweeps the dead owner's orphan (its start sweep is
    # campaign-wide); only a slot-0 hold deletes slot 0's cluster.
    HOLD_SLOT=1 timeout 60 "${HOLD}" K1 30 true
    [ "$(regs)" = "k3dgpu-a-s0-a1" ] || { echo "a slot-1 hold touched slot 0's leftover: $(regs)"; return 1; }
    pgrep -f "${mark}" >/dev/null && { echo "the orphan CMD survived the next hold's sweep"; return 1; }
    [ "$(cat "${HOME}/k3dgpu-bench/runs/K1/stale_reaped")" -ge 1 ] || { echo "K1 stale_reaped: $(cat "${HOME}/k3dgpu-bench/runs/K1/stale_reaped")"; return 1; }
    HOLD_SLOT=0 timeout 60 "${HOLD}" K0b 30 true
    [ -z "$(regs)" ] || { echo "the next slot-0 hold left: $(regs)"; return 1; }
}

inherited_locks_mode() {
    fresh
    mkdir -p "${HOME}/k3dgpu-bench.lock.d"
    local t0 t1 rc
    t0=$(now)
    ( exec 7> "${HOME}/k3dgpu-bench.lock.d/slot-0" 8> "${HOME}/k3dgpu-bench.lock.d/slot-1" 9> "${HOME}/k3dgpu-bench.lock"
      flock 7 && flock 8 && flock -x 9 || exit 9
      HOLD_LOCKS_HELD=1 HOLD_SLOT=0 timeout 20 "${HOLD}" I0 30 sh -c 'date +%s.%N > "$SHIM_DIR/m"' )
    t1=$(cat "${W}/m" 2>/dev/null || echo 1e12)
    elapsed_lt "${t0}" "${t1}" 3 || { echo "inherited-lock hold did not start at once"; return 1; }
    # The parent holds main exclusive, but on fd 6; fd 9 is a second open of the same file, so
    # it is not the parent's locked file description: refused.
    ( exec 6> "${HOME}/k3dgpu-bench.lock" 7> "${HOME}/k3dgpu-bench.lock.d/slot-0" 8> "${HOME}/k3dgpu-bench.lock.d/slot-1" 9> "${HOME}/k3dgpu-bench.lock"
      flock 7 && flock 8 && flock -x 6 || exit 9
      HOLD_LOCKS_HELD=1 HOLD_SLOT=0 timeout 5 "${HOLD}" I1 30 sh -c ': > "$SHIM_DIR/ran1"' )
    rc=$?
    [ "${rc}" = 4 ] || { echo "rc ${rc}, want 4"; return 1; }
    [ ! -e "${W}/ran1" ] || { echo "CMD ran on an fd 9 the parent did not lock"; return 1; }
}

inherited_locks_unlocked_parent_refused() {
    fresh
    mkdir -p "${HOME}/k3dgpu-bench.lock.d"
    local rc
    # The parent opened fds 7, 8, 9 and locked none of them; no one else holds anything.
    ( exec 7> "${HOME}/k3dgpu-bench.lock.d/slot-0" 8> "${HOME}/k3dgpu-bench.lock.d/slot-1" 9> "${HOME}/k3dgpu-bench.lock"
      HOLD_LOCKS_HELD=1 HOLD_SLOT=0 timeout 10 "${HOLD}" U0 30 sh -c ': > "$SHIM_DIR/ran"' )
    rc=$?
    [ "${rc}" = 4 ] || { echo "rc ${rc}, want 4"; return 1; }
    [ ! -e "${W}/ran" ] || { echo "CMD ran on locks the parent never took"; return 1; }
    # The parent holds main shared (not the exclusive the mode needs): refused, and the
    # parent's shared lock is left as it was (no conversion through the inherited fd).
    ( exec 7> "${HOME}/k3dgpu-bench.lock.d/slot-0" 8> "${HOME}/k3dgpu-bench.lock.d/slot-1" 9> "${HOME}/k3dgpu-bench.lock"
      flock 7 && flock 8 && flock -s 9 || exit 9
      HOLD_LOCKS_HELD=1 HOLD_SLOT=0 timeout 10 "${HOLD}" U1 30 sh -c ': > "$SHIM_DIR/ran"'
      echo "rc=$?" > "${W}/u1"
      grep -E '^lock:.*FLOCK +ADVISORY +READ ' /proc/self/fdinfo/9 >> "${W}/u1" )
    grep -q '^rc=4$' "${W}/u1" || { echo "shared-held parent: $(head -1 "${W}/u1"), want rc=4"; return 1; }
    grep -q READ "${W}/u1" || { echo "the parent's shared lock on fd 9 was converted or dropped"; return 1; }
    [ ! -e "${W}/ran" ] || { echo "CMD ran with main held shared"; return 1; }
}

writer_not_starved() {
    fresh
    HOLD_SLOT=0 timeout 60 "${HOLD}" A 30 sleep 3 &
    BG+=($!)
    wait_log "A start:" 20 || { echo "A did not start"; return 1; }
    flock -x "${HOME}/k3dgpu-bench.lock" sh -c 'date +%s.%N > "$SHIM_DIR/mw"' &
    BG+=($!)
    sleep 0.5
    HOLD_SLOT=1 timeout 60 "${HOLD}" B 30 sh -c 'date +%s.%N > "$SHIM_DIR/m1"'
    local w b
    w=$(cat "${W}/mw" 2>/dev/null || echo 1e12) b=$(cat "${W}/m1" 2>/dev/null || echo 0)
    awk -v w="${w}" -v b="${b}" 'BEGIN {exit !(w < b)}' \
        || { echo "slot-1 hold ran at ${b}, before the queued exclusive waiter (${w})"; return 1; }
}

locks_released_at_exit() {
    fresh
    timeout 60 "${HOLD}" L 30 true
    flock -n -x "${HOME}/k3dgpu-bench.lock" true || { echo "main lock still held after a legacy hold exited"; return 1; }
    HOLD_SLOT=1 timeout 60 "${HOLD}" S1 30 true
    flock -n -x "${HOME}/k3dgpu-bench.lock.d/slot-1" true || { echo "slot-1 still held after the hold exited"; return 1; }
    flock -n -x "${HOME}/k3dgpu-bench.lock" true || { echo "main lock still held after a slot hold exited"; return 1; }
}

slot_gate_term() {  # hold_own_test T12 in slot mode: TERM during the busy wait
    fresh
    stat_writer "0 1"
    HOLD_SLOT=0 HOLD_GATE_MAX_S=600 HOLD_POLL_S=30 "${HOLD}" GT 30 sh -c ': > "$SHIM_DIR/ran"' &
    local pid=$! rc t0
    BG+=("${pid}")
    wait_log "GT busy" 20 || { echo "the gate did not wait"; return 1; }
    t0=${SECONDS}
    kill -TERM "${pid}"
    for _ in $(seq 100); do kill -0 "${pid}" 2>/dev/null || break; sleep 0.1; done
    kill -0 "${pid}" 2>/dev/null && { echo "hold.sh still running 10 s after TERM"; kill -9 "${pid}"; return 1; }
    wait "${pid}"
    rc=$?
    [ "${rc}" = 143 ] || { echo "rc ${rc}, want 143"; return 1; }
    [ $(( SECONDS - t0 )) -le 5 ] || { echo "TERM took $(( SECONDS - t0 )) s"; return 1; }
    [ ! -e "${W}/ran" ] || { echo "CMD started after TERM"; return 1; }
    flock -n -x "${HOME}/k3dgpu-bench.lock.d/slot-0" true || { echo "slot-0 lock held after TERM"; return 1; }
    flock -n -x "${HOME}/k3dgpu-bench.lock" true || { echo "main lock held after TERM"; return 1; }
}

slot_gate_own_slice() {
    fresh
    stat_writer "2 3"
    HOLD_SLOT=0 timeout 60 "${HOLD}" G0 30 true
    [ "$(cat "${HOME}/k3dgpu-bench/runs/G0/wait_s")" = 0 ] || { echo "waited with the other slice busy"; return 1; }
    [ ! -e "${HOME}/k3dgpu-bench/runs/G0/flagged" ] || { echo "flagged with own slice idle"; return 1; }
    stat_writer "0 1"
    HOLD_SLOT=0 timeout 60 "${HOLD}" G1 30 true
    [ -e "${HOME}/k3dgpu-bench/runs/G1/flagged" ] || { echo "own slice busy, not flagged"; return 1; }
    [ "$(cat "${HOME}/k3dgpu-bench/runs/G1/wait_s")" -ge 1 ] || { echo "own slice busy, no wait"; return 1; }
}

# A fake 2-slice sysfs over the CPUs this test may run on (no SMT), so taskset runs on any host
# with 2 or more allowed CPUs, hybrid or not.
ALLOWED=$(python3 -c 'import os; print(" ".join(map(str, sorted(os.sched_getaffinity(0)))))')
read -r -a ALLOWED_A <<< "${ALLOWED}"
slot_taskset() {
    fresh
    local c n=$(( ${#ALLOWED_A[@]} / 2 * 2 )) want
    rm -rf "${W}/sys"
    mkdir -p "${W}/sys/devices/system/cpu"
    for c in "${ALLOWED_A[@]:0:${n}}"; do
        mkdir -p "${W}/sys/devices/system/cpu/cpu${c}/topology"
        echo "${c}" > "${W}/sys/devices/system/cpu/cpu${c}/topology/thread_siblings_list"
    done
    echo "${ALLOWED_A[*]:0:${n}}" | tr ' ' ',' > "${W}/sys/devices/system/cpu/online"
    want=$(python3 "${LOADPY}" slice --slots 2 --slot 1 --sysfs "${W}/sys" 2>/dev/null | cut -d' ' -f1)
    HOLD_SLOT=1 timeout 60 "${HOLD}" TS 30 sh -c 'grep Cpus_allowed_list /proc/self/status > "$SHIM_DIR/m"'
    local got
    got=$(awk '{print $2}' "${W}/m" 2>/dev/null)
    [ -n "${want}" ] && [ "${got}" = "${want}" ] || { echo "CMD on '${got}', slice 1 is '${want}'"; return 1; }
}

check "legacy (no HOLD_SLOT): main lock exclusive, every k3dgpu-(e|a)- leftover swept" legacy_default_unchanged
check "slots: a slot-1 hold starts while slot 0 runs" slot1_starts_while_slot0_runs
check "slots: a slot-1 hold deletes only s1 clusters" slot_cleanup_scoped
check "slots: a slot-1 hold stops only slot-1 load" slot_load_stop_scoped
check "slots: an outside exclusive flock excludes slot holds, and the reverse" x_exclusive_excludes_slots
check "slots: a queued slot hold does not hold the main lock" queued_slot_hold_does_not_hold_main
check "slots: after kill -9, slot 0's leftover is cleaned by slot 0 only" kill9_epoch_recovery
check "slots: HOLD_LOCKS_HELD runs on the parent's locks, refuses an fd 9 that is not the parent's locked description" inherited_locks_mode
check "slots: HOLD_LOCKS_HELD refuses fds the parent opened but did not lock exclusive" inherited_locks_unlocked_parent_refused
check "slots: a slot hold yields the main lock to a queued exclusive waiter" writer_not_starved
check "locks: free as soon as the hold exits (the watchdog's sleep holds none)" locks_released_at_exit
check "slots: the gate reads the own slice only" slot_gate_own_slice
check "slots: TERM during the slotted gate's busy wait stops the hold at once, locks free" slot_gate_term
if [ "${#ALLOWED_A[@]}" -lt 2 ]; then
    SKIPS=$((SKIPS + 1)); echo "skip slots: CMD runs under taskset on the slice (fewer than 2 allowed CPUs)"
else
    check "slots: CMD runs under taskset on the slice" slot_taskset
fi

# h56.sh: refusals leave no condition-hold record behind.
h56_env() { env PATH="${HOME}/k3dgpu-bench/bin:${PATH}" H56_LOCK_W=1 "$@"; }
h56_ready() {  # stage 0 passed and a feasible smoke on record
    local r=${HOME}/k3dgpu-bench/results/H56
    mkdir -p "${r}/smoke-W/s0/run"
    echo '{"stage1": true, "share": 0.2}' > "${r}/lockwait.json"
    echo 1000 > "${r}/smoke-W/s0/t0"
    python3 -c 'import json; [print(json.dumps({"task": f"B-T{i}", "cycle": 0, "censored": False, "passed": True, "duration_s": 20.0, "t_start": 1300.0 + 21 * i, "t_end": 1320.0 + 21 * i})) for i in range(3)]' \
        > "${r}/smoke-W/s0/run/results.jsonl"
}
h56_stage0_enforced() {
    fresh
    local rc
    h56_env bash "${H56SH}" 1 > "${T}/h56.out" 2>&1
    rc=$?
    [ "${rc}" = 7 ] || { echo "rc ${rc}, want 7"; cat "${T}/h56.out"; return 1; }
    [ ! -e "${HOME}/k3dgpu-bench/results/H56/hold1" ] || { echo "hold1 written without stage 0"; return 1; }
    h56_env H56_SMOKE=1 bash "${H56SH}" 1 > "${T}/h56.out" 2>&1
    rc=$?
    [ "${rc}" = 7 ] || { echo "smoke rc ${rc}, want 7"; return 1; }
}
h56_lock_timeout_no_record() {
    fresh
    h56_ready
    mkdir -p "${HOME}/k3dgpu-bench.lock.d"
    flock -x "${HOME}/k3dgpu-bench.lock.d/slot-0" sleep 6 &
    BG+=($!)
    sleep 0.3
    local rc
    h56_env bash "${H56SH}" 1 > "${T}/h56.out" 2>&1
    rc=$?
    [ "${rc}" = 4 ] || { echo "rc ${rc}, want 4"; cat "${T}/h56.out"; return 1; }
    [ ! -e "${HOME}/k3dgpu-bench/results/H56/hold1" ] || { echo "hold1 left by a lock timeout"; return 1; }
}
h56_disk_refusal_no_record() {
    fresh
    h56_ready
    echo 96 > "${W}/df_pct"
    local rc
    h56_env bash "${H56SH}" 2 > "${T}/h56.out" 2>&1
    rc=$?
    [ "${rc}" = 5 ] || { echo "rc ${rc}, want 5"; cat "${T}/h56.out"; return 1; }
    [ ! -e "${HOME}/k3dgpu-bench/results/H56/hold2" ] || { echo "hold2 left by a disk refusal"; return 1; }
}
check "h56.sh: refuses without stage 0 (lockwait.json stage1), writes nothing" h56_stage0_enforced
check "h56.sh: a lock timeout leaves no hold<N>" h56_lock_timeout_no_record
check "h56.sh: a W disk refusal leaves no hold<N>" h56_disk_refusal_no_record
echo "crescent_hold: ${PASS} passed, ${FAILS} failed, ${SKIPS} skipped"
# CRESCENT_HOLD_STRICT=1 (tests/bench_test.sh): a skip is a failure, so CI cannot hide one.
[ "${FAILS}" = 0 ] && { [ "${CRESCENT_HOLD_STRICT:-0}" != 1 ] || [ "${SKIPS}" = 0 ]; }
