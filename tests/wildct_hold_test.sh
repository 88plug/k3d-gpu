#!/usr/bin/env bash
# wildct_hold_test.sh: bench/wildct/hold.sh, wait_hold.sh and h52w.sh on a host whose
# systemd or docker daemon stops answering (EXPERIMENTS.md "Wildct amendment R1"): every
# docker and k3d call hold.sh makes is bounded, a wedged host stops a hold before it starts
# anything, the RAPL reader must give a reading, cleanup that cannot finish is named, and
# runs/NAME/rc is written on every exit so a waiter never polls forever. h52w.sh's pod wait
# does not count an empty pod list as ready and records its outcome.
#
# Runs the scripts for real under a temporary HOME with stub docker, k3d, systemctl, df,
# cut (load1 only), kubectl and k3d-gpu on PATH. No root, no docker daemon, no cluster.
#
# Usage: tests/wildct_hold_test.sh

set -uo pipefail
[ -n "${WT_INNER:-}" ] || exec timeout -k 10 600 env WT_INNER=1 bash "$0" "$@"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOLD=${ROOT}/bench/wildct/hold.sh
WAIT=${ROOT}/bench/wildct/wait_hold.sh
H52W=${ROOT}/bench/wildct/h52w.sh
REAL_CUT=$(command -v cut)
PASS=0 FAILS=0
T=$(mktemp -d)
BG=()
cleanup() {
    local p
    for p in ${BG[@]+"${BG[@]}"}; do kill -9 "${p}" 2>/dev/null; wait "${p}" 2>/dev/null; done
    rm -rf "${T}"
}
trap cleanup EXIT

# fresh [CLUSTERS]: a new HOME with the stubs; SHIM_DIR files switch stub behaviour:
#   systemd_hang systemd_fail info_hang run_hang exec_empty rm_hang k3d_hang list_hang
#   (present = on; k3d_hang: cluster delete hangs, list_hang: cluster list hangs);
#   rm_hang_noterm: docker rm hangs and ignores SIGTERM; systemd_fail_from N: systemctl fails
#   from its Nth call on; load1 (the hold-start gate's load1, default 0.00)
#   df_avail (GB available on /, default 100), clusters.reg (one name per line)
fresh() {
    local c
    W=$(mktemp -d "${T}/w.XXXX")
    export HOME=${W}/home SHIM_DIR=${W} WILDCT_CALL_T=2 WILDCT_DELETE_T=2
    WB=${HOME}/k3dgpu-bench-wildct
    mkdir -p "${WB}/bin" "${WB}/k3d-gpu/usr/bin"
    cp "${ROOT}/scripts/k3d-gpu" "${WB}/k3d-gpu/usr/bin/k3d-gpu"
    : > "${W}/clusters.reg"
    : > "${W}/calls.log"
    for c in ${1:-}; do echo "${c}" >> "${W}/clusters.reg"; done
    local b=${WB}/bin
    cat > "${b}/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "${SHIM_DIR}/calls.log"
case $1 in
    info) [ -e "${SHIM_DIR}/info_hang" ] && exec sleep 1000; echo 29.6.1 ;;
    run) [ -e "${SHIM_DIR}/run_hang" ] && exec sleep 1000; echo 2677961bea6f ;;
    exec) [ -e "${SHIM_DIR}/exec_empty" ] && exit 1; echo 123456789012 ;;
    rm) [ -e "${SHIM_DIR}/rm_hang_noterm" ] && { trap '' TERM; exec sleep 1000; }
        [ -e "${SHIM_DIR}/rm_hang" ] && exec sleep 1000 ;;
esac
exit 0
EOF
    cat > "${b}/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "${SHIM_DIR}/calls.log"
n=$(grep -c '^systemctl ' "${SHIM_DIR}/calls.log")
[ -e "${SHIM_DIR}/systemd_hang" ] && exec sleep 1000
[ -e "${SHIM_DIR}/systemd_fail" ] && { echo "Failed to get properties: Connection timed out" >&2; exit 1; }
[ -e "${SHIM_DIR}/systemd_fail_from" ] && [ "${n}" -ge "$(cat "${SHIM_DIR}/systemd_fail_from")" ] \
    && { echo "Failed to get properties: Connection timed out" >&2; exit 1; }
echo active
EOF
    cat > "${b}/k3d" <<'EOF'
#!/usr/bin/env bash
echo "k3d $*" >> "${SHIM_DIR}/calls.log"
if [ "$1 $2" = "cluster list" ]; then
    [ -e "${SHIM_DIR}/list_hang" ] && exec sleep 1000
    python3 -c 'import json,sys; print(json.dumps([{"name": l.strip()} for l in open(sys.argv[1]) if l.strip()]))' \
        "${SHIM_DIR}/clusters.reg"
elif [ "$1 $2" = "cluster delete" ]; then
    [ -e "${SHIM_DIR}/k3d_hang" ] && exec sleep 1000
    shift 2
    for c in "$@"; do sed -i "/^${c}\$/d" "${SHIM_DIR}/clusters.reg"; done
fi
EOF
    printf '#!/usr/bin/env bash\na=$(cat "${SHIM_DIR}/df_avail" 2>/dev/null || echo 100)\necho "Filesystem 1G-blocks Used Available Capacity Mounted on"\necho "/dev/x 3667G 100G ${a}G 50%% /"\n' > "${b}/df"
    # The hold-start gate reads load1 with `cut ... /proc/loadavg`; the test host's own load is not the subject.
    printf '#!/usr/bin/env bash\n[ "${!#}" = /proc/loadavg ] && { cat "${SHIM_DIR}/load1" 2>/dev/null || echo 0.00; exit 0; }\nexec %s "$@"\n' "${REAL_CUT}" > "${b}/cut"
    chmod +x "${b}"/*
}

R() { echo "${HOME}/k3dgpu-bench-wildct/runs/$1"; }
# child PID COMM: the pids of PID's children named COMM (from /proc, no pgrep).
child() {
    local f pid c st ppid
    for f in /proc/[0-9]*/stat; do
        read -r pid c st ppid _ < "${f}" 2>/dev/null || continue
        [ "${ppid}" = "$1" ] && [ "${c}" = "($2)" ] && echo "${pid}"
    done
}
calls() { grep -c -- "$1" "${W}/calls.log"; }
# hold NAME CMD...: hold.sh NAME 30 3 CMD under a 60 s bound; sets RC and SECS.
hold() {
    local name=$1 t0=${SECONDS}
    shift
    timeout -k 5 60 bash "${HOLD}" "${name}" 30 3 "$@" > "${W}/hold.out" 2>&1
    RC=$? SECS=$((SECONDS - t0))
}
rcfile() { cat "$(R "$1")/rc" 2>/dev/null || echo none; }

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

healthy_hold_runs_and_cleans() {
    fresh "k3d-wtm-owner k3dgpu-w-old"
    hold H1 sh -c 'cp "$HOME/k3dgpu-bench-wildct/runs/H1/pid" "$SHIM_DIR/pid_seen"; echo ran > "$SHIM_DIR/ran"
        if flock -n "$HOME/k3dgpu-bench-wildct/live.lock" true; then echo free; else echo held; fi > "$SHIM_DIR/lock_seen"'
    [ "${RC}" = 0 ] || { echo "rc ${RC}"; cat "${W}/hold.out"; return 1; }
    [ "$(rcfile H1)" = 0 ] || { echo "rc file: $(rcfile H1)"; return 1; }
    [ -s "${W}/ran" ] || { echo "CMD did not run"; return 1; }
    [ -s "${W}/pid_seen" ] || { echo "no runs/H1/pid while CMD ran"; return 1; }
    [ "$(cat "${W}/lock_seen")" = held ] || { echo "live.lock not held while CMD ran"; return 1; }
    [ "$(calls 'systemctl show -p ActiveState --value docker')" -ge 1 ] || { echo "no systemd check"; return 1; }
    [ "$(calls 'docker exec k3dgpu-w-rapl cat /sys/class/powercap/intel-rapl:0/energy_uj')" -ge 1 ] || { echo "no RAPL reading check"; return 1; }
    [ "$(calls 'docker rm -f k3dgpu-w-rapl')" -ge 1 ] || { echo "RAPL reader not removed"; return 1; }
    [ "$(tr '\n' ' ' < "${W}/clusters.reg")" = "k3d-wtm-owner " ] || { echo "clusters left: $(cat "${W}/clusters.reg")"; return 1; }
    [ ! -e "$(R H1)/cleanup-incomplete" ] || { echo "cleanup-incomplete on a healthy hold"; return 1; }
}

wedged_systemd_stops_before_docker() {
    fresh
    touch "${W}/systemd_hang"
    hold H2 sh -c 'echo ran > "$SHIM_DIR/ran"'
    [ "$(rcfile H2)" = 8 ] || { echo "rc file $(rcfile H2), exit ${RC} after ${SECS} s"; return 1; }
    [ "${SECS}" -lt 20 ] || { echo "took ${SECS} s"; return 1; }
    [ ! -e "${W}/ran" ] || { echo "CMD ran on a wedged host"; return 1; }
    [ "$(calls 'docker run')" = 0 ] || { echo "a container was started on a wedged host"; return 1; }
    grep -q 'H2 host not healthy' "${HOME}/k3dgpu-bench-wildct/runs/campaign.log" || { echo "no log line"; return 1; }
}

systemd_error_stops_before_docker() {
    fresh
    touch "${W}/systemd_fail"
    hold H3 true
    [ "$(rcfile H3)" = 8 ] || { echo "rc file $(rcfile H3), exit ${RC}"; return 1; }
    [ "$(calls 'docker run')" = 0 ] || { echo "a container was started"; return 1; }
}

docker_info_hang_stops_before_docker_run() {
    fresh
    touch "${W}/info_hang"
    hold H4 true
    [ "$(rcfile H4)" = 8 ] || { echo "rc file $(rcfile H4), exit ${RC} after ${SECS} s"; return 1; }
    [ "${SECS}" -lt 20 ] || { echo "took ${SECS} s"; return 1; }
    [ "$(calls 'docker run')" = 0 ] || { echo "a container was started"; return 1; }
}

rapl_run_hang_is_bounded() {
    fresh
    touch "${W}/run_hang"
    hold H5 sh -c 'echo ran > "$SHIM_DIR/ran"'
    [ "$(rcfile H5)" = 6 ] || { echo "rc file $(rcfile H5), exit ${RC} after ${SECS} s"; return 1; }
    [ "${SECS}" -lt 30 ] || { echo "took ${SECS} s"; return 1; }
    [ ! -e "${W}/ran" ] || { echo "CMD ran without a RAPL reader"; return 1; }
    grep -q 'H5 RAPL reader did not start (docker run rc=124;' "${HOME}/k3dgpu-bench-wildct/runs/campaign.log" \
        || { echo "no log line with docker run's rc"; grep H5 "${HOME}/k3dgpu-bench-wildct/runs/campaign.log"; return 1; }
}

rapl_unreadable_stops_hold() {
    fresh
    touch "${W}/exec_empty"
    hold H6 sh -c 'echo ran > "$SHIM_DIR/ran"'
    [ "$(rcfile H6)" = 6 ] || { echo "rc file $(rcfile H6), exit ${RC}"; return 1; }
    [ ! -e "${W}/ran" ] || { echo "CMD ran without a RAPL reading"; return 1; }
    grep -q 'H6 RAPL reader gave no energy_uj' "${HOME}/k3dgpu-bench-wildct/runs/campaign.log" || { echo "no log line"; return 1; }
}

cleanup_hang_is_bounded_and_named() {
    fresh
    hold H7 sh -c 'echo k3dgpu-w-h7 >> "$SHIM_DIR/clusters.reg"; touch "$SHIM_DIR/k3d_hang" "$SHIM_DIR/rm_hang_noterm"'
    [ "$(rcfile H7)" = 0 ] || { echo "rc file $(rcfile H7), exit ${RC} after ${SECS} s"; return 1; }
    [ "${SECS}" -lt 45 ] || { echo "took ${SECS} s"; return 1; }
    grep -q 'k3d cluster delete k3dgpu-w-h7' "$(R H7)/cleanup-incomplete" 2>/dev/null || { echo "cluster delete not named"; cat "$(R H7)/cleanup-incomplete" 2>/dev/null; return 1; }
    grep -q 'docker rm -f k3dgpu-w-rapl' "$(R H7)/cleanup-incomplete" || { echo "RAPL rm not named"; return 1; }
}

early_exits_write_rc() {
    fresh
    echo 10 > "${W}/df_avail"
    hold H8 true
    [ "$(rcfile H8)" = 5 ] || { echo "disk floor: rc file $(rcfile H8), exit ${RC}"; return 1; }
    fresh
    echo 'VERSION="0.0.0"' > "${WB}/k3d-gpu/usr/bin/k3d-gpu"
    hold H9 true
    [ "$(rcfile H9)" = 7 ] || { echo "launcher diff: rc file $(rcfile H9), exit ${RC}"; return 1; }
}

cluster_list_hang_in_cleanup_is_named() {
    fresh
    hold H10 sh -c 'echo k3dgpu-w-h10 >> "$SHIM_DIR/clusters.reg"; touch "$SHIM_DIR/list_hang"'
    [ "$(rcfile H10)" = 0 ] || { echo "rc file $(rcfile H10), exit ${RC} after ${SECS} s"; return 1; }
    grep -q 'k3d cluster list' "$(R H10)/cleanup-incomplete" 2>/dev/null \
        || { echo "a cluster list that did not answer was read as no cluster left"; cat "$(R H10)/cleanup-incomplete" 2>/dev/null; return 1; }
}

# A second signal while the cleanup runs (an operator's repeated kill) must not cut the cleanup
# short: the clusters are still deleted and rc is still written.
second_signal_during_cleanup_still_writes_rc() {
    fresh
    local p
    bash "${HOLD}" H11 30 3 sh -c 'echo k3dgpu-w-h11 >> "$SHIM_DIR/clusters.reg"; touch "$SHIM_DIR/k3d_hang" "$SHIM_DIR/started"; sleep 20' \
        > "${W}/hold.out" 2>&1 &
    p=$!
    BG+=("${p}")
    for _ in $(seq 100); do [ -e "${W}/started" ] && break; sleep 0.1; done
    [ -e "${W}/started" ] || { echo "CMD never started"; cat "${W}/hold.out"; return 1; }
    kill -TERM "${p}"
    sleep 1
    kill -TERM "${p}" 2>/dev/null
    for _ in $(seq 300); do kill -0 "${p}" 2>/dev/null || break; sleep 0.1; done
    [ "$(rcfile H11)" = 143 ] || { echo "rc file $(rcfile H11) after a second TERM during cleanup"; return 1; }
    grep -q 'k3d cluster delete k3dgpu-w-h11' "$(R H11)/cleanup-incomplete" 2>/dev/null || { echo "cluster delete not named"; return 1; }
    [ "$(calls 'docker rm -f k3dgpu-w-rapl')" -ge 2 ] || { echo "cleanup cut short: RAPL reader not removed"; return 1; }
}

# An earlier attempt's records under the same NAME are kept, moved aside, never mixed in.
earlier_attempt_is_moved_aside() {
    fresh
    local r m old
    r=$(R H12); mkdir -p "${r}"
    for m in rc cleanup-incomplete unhealthy flagged disk-abort sample.jsonl; do echo old > "${r}/${m}"; done
    hold H12 true
    [ "$(rcfile H12)" = 0 ] || { echo "rc file $(rcfile H12), exit ${RC}"; return 1; }
    for m in cleanup-incomplete unhealthy flagged disk-abort; do
        [ ! -e "${r}/${m}" ] || { echo "stale ${m} from an earlier attempt kept in runs/H12"; return 1; }
    done
    ! grep -qx old "${r}/sample.jsonl" 2>/dev/null || { echo "the earlier attempt's samples are mixed into this one's"; return 1; }
    old=$(find "${r%/*}" -maxdepth 1 -name 'H12.*' -type d | head -1)
    [ -n "${old}" ] && [ "$(cat "${old}/flagged")" = old ] || { echo "the earlier attempt's records were not kept"; ls "${r%/*}"; return 1; }
}

# A TERM while hold.sh waits for the lock ends it at once, with rc 143.
term_during_lock_wait_is_prompt() {
    fresh
    local p q
    mkdir -p "${WB}/runs"
    ( exec 8> "${WB}/live.lock"; flock 8; exec sleep 60 ) &
    q=$!; BG+=("${q}")
    sleep 0.3
    bash "${HOLD}" H13 30 3 true > "${W}/hold.out" 2>&1 &
    p=$!; BG+=("${p}")
    for _ in $(seq 50); do grep -q 'H13 waiting for the lock' "${WB}/runs/campaign.log" 2>/dev/null && break; sleep 0.1; done
    sleep 0.3
    f=$(child "${p}" flock)
    kill -TERM "${p}"
    for _ in $(seq 50); do kill -0 "${p}" 2>/dev/null || break; sleep 0.1; done
    kill -0 "${p}" 2>/dev/null && { echo "hold.sh still waiting for the lock 5 s after TERM"; return 1; }
    [ "$(rcfile H13)" = 143 ] || { echo "rc file $(rcfile H13)"; return 1; }
    [ -n "${f}" ] || { echo "no flock child seen while waiting"; return 1; }
    sleep 0.2
    ! kill -0 "${f}" 2>/dev/null || { kill "${f}"; echo "flock ${f} left waiting on the lock after hold.sh ended"; return 1; }
}

# A TERM during the load gate's 120 s wait ends hold.sh at once, and its sleep is not left behind.
term_during_load_wait_is_prompt() {
    fresh
    local p s
    echo 20.00 > "${W}/load1"
    bash "${HOLD}" H18 30 3 true > "${W}/hold.out" 2>&1 &
    p=$!; BG+=("${p}")
    for _ in $(seq 50); do grep -q 'H18 busy (load1 20.00)' "${WB}/runs/campaign.log" 2>/dev/null && break; sleep 0.1; done
    sleep 0.3
    s=$(child "${p}" sleep)
    kill -TERM "${p}"
    for _ in $(seq 20); do kill -0 "${p}" 2>/dev/null || break; sleep 0.1; done
    kill -0 "${p}" 2>/dev/null && { echo "hold.sh still in the load wait 2 s after TERM"; return 1; }
    [ "$(rcfile H18)" = 143 ] || { echo "rc file $(rcfile H18)"; return 1; }
    [ -n "${s}" ] || { echo "no sleep child seen in the load wait"; return 1; }
    sleep 0.2
    ! kill -0 "${s}" 2>/dev/null || { kill "${s}"; echo "the load wait's sleep ${s} left behind"; return 1; }
}

# Processes the command leaves behind, also in a process group of their own (a timeout inside the
# command), are stopped at the end; processes that are not the hold's are not touched.
leftover_processes_are_stopped() {
    fresh
    local other a b
    sleep 300 & other=$!; BG+=("${other}")
    hold H19 sh -c 'timeout -k 1 300 sh -c '"'"'echo $$ > "$SHIM_DIR/stray_sh"; exec sleep 299'"'"' & echo $! > "$SHIM_DIR/stray_timeout"; sleep 0.3'
    [ "$(rcfile H19)" = 0 ] || { echo "rc file $(rcfile H19), exit ${RC}"; return 1; }
    a=$(cat "${W}/stray_timeout" 2>/dev/null) b=$(cat "${W}/stray_sh" 2>/dev/null)
    [ -n "${a}" ] && [ -n "${b}" ] || { echo "the command's strays were not recorded"; return 1; }
    BG+=("${a}" "${b}")
    ! kill -0 "${a}" 2>/dev/null || { echo "the command's timeout ${a} (its own process group) left running"; return 1; }
    ! kill -0 "${b}" 2>/dev/null || { echo "the command's sleep ${b} left running"; return 1; }
    kill -0 "${other}" 2>/dev/null || { echo "a process that is not the hold's was stopped"; return 1; }
    [ ! -e "$(R H19)/cleanup-incomplete" ] || { echo "cleanup-incomplete: $(cat "$(R H19)/cleanup-incomplete")"; return 1; }
}

# An earlier attempt's directory that cannot be moved aside: hold.sh stops (9) and writes nothing in it.
unmovable_earlier_attempt_stops() {
    [ "$(id -u)" != 0 ] || { echo "skipped as root"; return 0; }
    fresh
    local r st
    r=$(R H20); mkdir -p "${r}"; echo old > "${r}/rc"
    chmod a-w "${r%/*}"
    timeout -k 5 60 bash "${HOLD}" H20 30 3 true > "${W}/hold.out" 2>&1; st=$?
    chmod u+w "${r%/*}"
    [ "${st}" = 9 ] || { echo "exit ${st}"; cat "${W}/hold.out"; return 1; }
    [ "$(cat "${r}/rc")" = old ] && [ ! -e "${r}/pid" ] || { echo "the earlier attempt's directory was written to"; return 1; }
}

# The health check is repeated just before the hold starts (the load gate can last 60 min).
health_is_rechecked_before_start() {
    fresh
    echo 2 > "${W}/systemd_fail_from"
    hold H14 sh -c 'echo ran > "$SHIM_DIR/ran"'
    [ "$(rcfile H14)" = 8 ] || { echo "rc file $(rcfile H14), exit ${RC}"; return 1; }
    [ "$(calls 'systemctl show')" = 2 ] || { echo "systemctl calls: $(calls 'systemctl show')"; return 1; }
    [ "$(calls 'docker run')" = 0 ] && [ ! -e "${W}/ran" ] || { echo "the hold started on a host that stopped answering"; return 1; }
}

# k3d not answering before the hold: no hold (rc 8), nothing started.
k3d_not_answering_before_start_is_no_hold() {
    fresh
    touch "${W}/list_hang"
    hold H15 sh -c 'echo ran > "$SHIM_DIR/ran"'
    [ "$(rcfile H15)" = 8 ] || { echo "list hang: rc file $(rcfile H15), exit ${RC}"; return 1; }
    [ "$(calls 'docker run')" = 0 ] && [ ! -e "${W}/ran" ] || { echo "list hang: the hold started"; return 1; }
    fresh k3dgpu-w-old
    touch "${W}/k3d_hang"
    hold H16 sh -c 'echo ran > "$SHIM_DIR/ran"'
    [ "$(rcfile H16)" = 8 ] || { echo "leftover delete hang: rc file $(rcfile H16), exit ${RC}"; return 1; }
    [ "$(calls 'docker run')" = 0 ] && [ ! -e "${W}/ran" ] || { echo "leftover delete hang: the hold started beside a leftover cluster"; return 1; }
    grep -q 'k3d cluster delete k3dgpu-w-old' "$(R H16)/cleanup-incomplete" 2>/dev/null || { echo "leftover cluster not named"; return 1; }
}

# CMD does not inherit the host lock's fd (a process it leaves behind would keep the lock).
cmd_does_not_hold_the_lock_fd() {
    fresh
    hold H17 sh -c 'if [ -e /proc/$$/fd/9 ]; then echo open; else echo closed; fi > "$SHIM_DIR/fd9"'
    [ "$(cat "${W}/fd9" 2>/dev/null)" = closed ] || { echo "fd 9 in CMD: $(cat "${W}/fd9" 2>/dev/null)"; return 1; }
}

wait_hold_reports_each_end() {
    fresh
    local r out rc
    r=$(R W1); mkdir -p "${r}"; echo 0 > "${r}/rc"
    out=$(timeout 20 bash "${WAIT}" W1 10 0.2); rc=$?
    [ "${rc}" = 0 ] && [ "${out}" = "W1 rc=0" ] || { echo "rc 0: exit ${rc} '${out}'"; return 1; }
    r=$(R W2); mkdir -p "${r}"; echo 6 > "${r}/rc"
    timeout 20 bash "${WAIT}" W2 10 0.2 > /dev/null; rc=$?
    [ "${rc}" = 1 ] || { echo "rc 6: exit ${rc}"; return 1; }
    r=$(R W3); mkdir -p "${r}"; sleep 0 & wait $!; echo $! > "${r}/pid"
    out=$(timeout 20 bash "${WAIT}" W3 10 0.2); rc=$?
    [ "${rc}" = 2 ] || { echo "dead pid, no rc: exit ${rc} '${out}'"; return 1; }
    r=$(R W4); mkdir -p "${r}"; sleep 100 & BG+=($!); echo $! > "${r}/pid"
    out=$(timeout 20 bash "${WAIT}" W4 1 0.2); rc=$?
    [ "${rc}" = 4 ] || { echo "live pid past the ceiling: exit ${rc} '${out}'"; return 1; }
}

# An rc file caught between its creation and its write (empty) is not an rc.
wait_hold_empty_rc_is_not_an_rc() {
    fresh
    local r out rc
    r=$(R W6); mkdir -p "${r}"; : > "${r}/rc"; sleep 100 & BG+=($!); echo $! > "${r}/pid"
    out=$(timeout 20 bash "${WAIT}" W6 1 0.2); rc=$?
    [ "${rc}" = 4 ] || { echo "empty rc, live pid: exit ${rc} '${out}' (want 4, still running)"; return 1; }
    r=$(R W7); mkdir -p "${r}"; : > "${r}/rc"; sleep 0 & wait $!; echo $! > "${r}/pid"
    out=$(timeout 20 bash "${WAIT}" W7 10 0.2); rc=$?
    [ "${rc}" = 2 ] || { echo "empty rc, dead pid: exit ${rc} '${out}' (want 2, gone without rc)"; return 1; }
    r=$(R W8); mkdir -p "${r}"; : > "${r}/rc"; sleep 100 & BG+=($!); echo $! > "${r}/pid"
    ( sleep 0.5; echo 0 > "${r}/rc" ) &
    out=$(timeout 20 bash "${WAIT}" W8 10 0.2); rc=$?
    [ "${rc}" = 0 ] && [ "${out}" = "W8 rc=0" ] || { echo "rc written after an empty read: exit ${rc} '${out}'"; return 1; }
    r=$(R W9); mkdir -p "${r}"; : > "${r}/pid"
    out=$(timeout 20 bash "${WAIT}" W9 1 0.2); rc=$?
    [ "${rc}" = 4 ] || { echo "empty pid: exit ${rc} '${out}' (want 4, not 2: an empty pid is not a dead hold)"; return 1; }
}

wait_hold_follows_a_real_hold() {
    fresh
    local rc
    bash "${HOLD}" W5 30 3 sleep 1 > /dev/null 2>&1 &
    BG+=($!)
    timeout 40 bash "${WAIT}" W5 30 0.2 > /dev/null; rc=$?
    [ "${rc}" = 0 ] || { echo "exit ${rc}, rc file $(rcfile W5)"; return 1; }
}

# h52w: stub k3d-gpu (the installed release's path), kubectl and docker; inventory.sh is real.
h52w_fresh() {
    fresh
    cat > "${WB}/k3d-gpu/usr/bin/k3d-gpu" <<'EOF'
#!/usr/bin/env bash
echo "k3d-gpu $*" >> "${SHIM_DIR}/calls.log"
[ "$1" = up ] && exit "$(cat "${SHIM_DIR}/up_rc" 2>/dev/null || echo 0)"
echo "$1 ok"
EOF
    cat > "${WB}/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
echo "kubectl $*" >> "${SHIM_DIR}/calls.log"
case "$*" in
    "get pods -A --no-headers") cat "${SHIM_DIR}/pods" 2>/dev/null ;;
    *"-o json"*) echo '{"items": []}' ;;
esac
exit 0
EOF
    # timeout: records its arguments, then the real one runs.
    printf '#!/usr/bin/env bash\necho "timeout $*" >> "${SHIM_DIR}/calls.log"\nexec %s "$@"\n' "$(command -v timeout)" > "${WB}/bin/timeout"
    chmod +x "${WB}/k3d-gpu/usr/bin/k3d-gpu" "${WB}/bin/kubectl" "${WB}/bin/timeout"
}
h52w() { H52W_POLLS=3 H52W_POLL_S=0 timeout -k 5 120 bash "${H52W}" "${W}/out" > /dev/null 2>&1; }

h52w_empty_pod_list_is_not_ready() {
    h52w_fresh
    h52w
    grep -q '^pods ready: no' "${W}/out/rc.txt" || { echo "rc.txt:"; cat "${W}/out/rc.txt"; return 1; }
    [ "$(calls '^kubectl get pods -A --no-headers')" = 3 ] || { echo "polls: $(calls '^kubectl get pods -A --no-headers')"; return 1; }
}

h52w_running_pods_are_ready() {
    h52w_fresh
    printf 'kube-system coredns-1 1/1 Running 0 1m\nkube-system nri-1 1/1 Running 0 1m\nkube-system helm-1 0/1 Completed 0 1m\n' > "${W}/pods"
    h52w
    grep -q '^pods ready: yes' "${W}/out/rc.txt" || { echo "rc.txt:"; cat "${W}/out/rc.txt"; return 1; }
}

h52w_steps_are_bounded() {
    h52w_fresh
    printf 'kube-system coredns-1 1/1 Running 0 1m\n' > "${W}/pods"
    h52w
    local k=${WB}/k3d-gpu/usr/bin/k3d-gpu s
    for s in "300 ${k} detect" "300 ${k} doctor" "600 ${k} up" "900 ${k} test" "600 ${k} down" "30 kubectl get pods -A --no-headers" \
        "60 kubectl get pods -A -o wide" "60 kubectl get ds -A" "60 kubectl get addons.k3s.cattle.io -A" "60 kubectl get node -o json" \
        "60 kubectl get pods -A -o json"; do
        grep -q -- "^timeout -k [0-9]* ${s}\$" "${W}/calls.log" || { echo "not bounded: ${s}"; grep '^timeout' "${W}/calls.log"; return 1; }
    done
    [ "$(grep -c "^timeout -k [0-9]* 300 ${k} detect\$" "${W}/calls.log")" = 2 ] || { echo "detect (as found and with the opt-out) not both bounded"; return 1; }
}

h52w_failed_up_is_not_waited_on() {
    h52w_fresh
    echo 1 > "${W}/up_rc"
    h52w
    grep -q '^pods ready: not waited (up rc=1)' "${W}/out/rc.txt" || { echo "rc.txt:"; cat "${W}/out/rc.txt"; return 1; }
    [ "$(calls '^kubectl get pods -A --no-headers')" = 0 ] || { echo "polled a cluster that did not come up"; return 1; }
}

# row.sh: each row's environment as the wildct pre-registration lists it.
ROW=${ROOT}/bench/wildct/row.sh
IMG=cryptoandcoffee/k3d-gpu:0.1.74@sha256:1d7513e65064b36a7d62eac56ddb4b6d7b904d53f482a984a39ed8590bd527a4
W3_LEDGER=$(awk '/^### Wildct pre-registration/ {f = 1; next} f && /^### / {exit} f' "${ROOT}/EXPERIMENTS.md" \
    | grep -o 'K3D_GPU_K3S_ARGS="[^"]*"' | head -1 | sed 's/^K3D_GPU_K3S_ARGS="//; s/"$//')
rowprint() { WILDCT_ROW_PRINT=1 IMAGE=${IMG} bash "${ROW}" "$@"; }
has() {  # has ROW... -- LINE...: every LINE is a line of row.sh's printout
    local args=() l out
    while [ "$1" != -- ]; do args+=("$1"); shift; done
    shift
    out=$(rowprint "${args[@]}") || { echo "row.sh ${args[*]} failed"; return 1; }
    for l in "$@"; do grep -qxF -- "${l}" <<< "${out}" || { echo "row.sh ${args[*]}: no line '${l}' in:"; echo "${out}"; return 1; }; done
}

row_envs_match_the_registration() {
    [ -n "${W3_LEDGER}" ] || { echo "W3 not found in the wildct pre-registration"; return 1; }
    local o=K3D_GPU_VENDORS=auto,-nvidia,-intel w3="K3D_GPU_K3S_ARGS=${W3_LEDGER}"
    has smoke -- K3D_GPU_CLUSTER=k3dgpu-w-smoke "${o}" "${w3}" "K3D_GPU_IMAGE=${IMG}" || return 1
    has h18w -- K3D_GPU_CLUSTER=k3dgpu-w-sbx "IMAGE=${IMG}" "${o}" "${w3}" || return 1
    has h25w -- CLUSTER=k3dgpu-w-h25 LEVELS=typical SEED_BASE=2560 HOLD=1440 LOAD_CPUS=20-23 "${o}" "${w3}" \
        "MODELS=${HOME}/k3dgpu-bench-wildct/models" || return 1
    has h34w -- H34_CLUSTER=k3dgpu-w-h34 H34_VENDORS=auto,-nvidia,-intel H34_LEVELS=typical LOAD_CPUS=20-23 \
        "K3D_GPU_IMAGE=${IMG}" "H22_MODELS=${HOME}/k3dgpu-bench-wildct/models" "${w3}" || return 1
    has h28w-prep -- LEVELS=typical "IMAGE=${IMG}" || return 1
    has h28w -- CLUSTER=k3dgpu-w-h28 LEVELS=typical HOLD=1440 NODE_CPUS=0-15 LOAD_CPUS=20-23 "${w3}" \
        "LOCK=${HOME}/k3dgpu-bench-wildct/inner.lock" || return 1
    has h57w 2 perf -- H57D_ROW=H57w H57D_PREFIX=k3dgpu-w- H57D_LOAD1_MAX=12 H57D_VENDORS=auto,-nvidia,-intel \
        "H57D_IMAGE=${IMG}" "H57D_K3S_ARGS=${W3_LEDGER}" "LOCK=${HOME}/k3dgpu-bench-wildct/inner.lock" \
        "G=${HOME}/k3dgpu-bench-wildct/h57" "H57D_G=${HOME}/k3dgpu-bench-wildct/h57" "K3D_REAL=${HOME}/k3dgpu-bench-wildct/bin/k3d" \
        "bash ${ROOT}/bench/h57d/hold.sh 2 perf" || return 1
    has h57w 5 perf Ld Ld-mtp Od -- "bash ${ROOT}/bench/h57d/hold.sh 5 perf Ld Ld-mtp Od" || return 1
    has h57w-convert -- "G=${HOME}/k3dgpu-bench-wildct/h57" "LOCK=${HOME}/k3dgpu-bench-wildct/live.lock" || return 1
    has h18w -- "bash ${ROOT}/bench/sandbox_round.sh (runs 1-3)" || return 1
    # H52w's procedure: `hold.sh H52w 1800 3 bench/wildct/h52w.sh <out>`; h52w.sh sets its own
    # cluster, opt-out and W3, and runs the installed release's default node image (no IMAGE).
    local out
    out=$(WILDCT_HOLD=H52w WILDCT_ROW_PRINT=1 IMAGE='' bash "${ROW}" h52w) || { echo "row.sh h52w failed"; return 1; }
    [ "${out}" = "bash ${ROOT}/bench/wildct/h52w.sh ${HOME}/k3dgpu-bench-wildct/runs/H52w/out" ] || { echo "row.sh h52w: ${out}"; return 1; }
}

# RESUME.md's `launch NAME CAP NEED ROW [ARGS]` lines: the row is one row.sh runs, and CAP and
# NEED are the pre-registration's `hold.sh NAME CAP NEED` (H57w-<n> for H57w-1..5).
resume_launch_lines_match_the_registration() {
    local reg line name cap need rest base want row n=0 tcap tneed
    reg=$(awk '/^### Wildct pre-registration/ {f = 1; next} f && /^### / {exit} f' "${ROOT}/EXPERIMENTS.md" \
        | tr '\n' ' ' | grep -oE 'hold\.sh H[0-9A-Za-z<>-]+ [0-9]+ [0-9]+' | sort -u)
    [ -n "${reg}" ] || { echo "no hold.sh lines in the pre-registration"; return 1; }
    while IFS= read -r line; do
        read -r name cap need rest <<< "${line#launch }"
        [[ "${cap}" =~ ^[0-9]+$ ]] || continue          # the usage line
        base=${name}; [[ "${name}" =~ ^H57w-[0-9]+$ ]] && base='H57w-<n>'
        case ${name} in smoke*) want="" ;; *) want="hold.sh ${base} ${cap} ${need}" ;; esac
        if [ -n "${want}" ] && ! grep -qxF "${want}" <<< "${reg}"; then
            echo "RESUME.md '${line}': CAP/NEED not registered (registered: $(tr '\n' ';' <<< "${reg}"))"; return 1
        fi
        n=$((n + 1))
        case ${name} in smoke*) row=smoke ;; H57w-*) row="h57w ${name#H57w-}" ;; *) row=${name,,} ;; esac
        [[ "${rest} " == "${row} "* ]] || { echo "RESUME.md '${line}': NAME ${name} runs row '${rest}', not its own '${row}'"; return 1; }
        [[ "${rest}" == *"<"* ]] && continue            # H57w-5: the failed hold's mode and arms
        # shellcheck disable=SC2086 # the row and its arguments, split on purpose
        [ -n "${rest}" ] && WILDCT_HOLD=${name} WILDCT_ROW_PRINT=1 IMAGE=${IMG} bash "${ROW}" ${rest} > /dev/null 2>&1 \
            || { echo "RESUME.md '${line}': row.sh does not run '${rest}'"; return 1; }
    done < <(grep -oE '`launch [^`]+`' "${ROOT}/bench/wildct/RESUME.md" | tr -d '`' | sort -u)
    [ "${n}" -ge 10 ] || { echo "only ${n} launch lines found in RESUME.md"; return 1; }
    # The hold table's CAP and NEED columns (hold_wait reads CAP) agree with its launch line.
    while IFS='|' read -r _ _ name tcap tneed line; do
        name=$(tr -d ' `' <<< "${name}") tcap=${tcap// /} tneed=${tneed// /}
        line=$(grep -oE '`launch [^`]+`' <<< "${line}" | head -1 | tr -d '`')
        read -r _ _ cap need _ <<< "${line}"
        [ "${tcap} ${tneed}" = "${cap} ${need}" ] || { echo "RESUME.md table row ${name}: CAP/NEED ${tcap}/${tneed}, launch line ${cap}/${need}"; return 1; }
    done < <(grep -E '^\| [0-9(]+ \| `H[^`]+` \| [0-9]+ \| [0-9]+ \|' "${ROOT}/bench/wildct/RESUME.md")
}

# Rule W4: H28w's probe.sh reads RAPL through hold.sh's reader (K3D_GPU_RAPL_EXEC), not through its
# own docker exec into the node (CPUs 0-15); without a reader given, its default is unchanged.
probe_keeps_the_given_rapl_reader() {
    local probe=${ROOT}/bench/zero-copy-models/probe.sh nosudo=${T}/nosudo out
    mkdir -p "${nosudo}"
    printf '#!/bin/sh\nexit 1\n' > "${nosudo}/sudo"; chmod +x "${nosudo}/sudo"
    out=$(PATH=${nosudo}:${PATH} K3D_GPU_RAPL_EXEC="docker exec k3dgpu-w-rapl" CLUSTER=k3dgpu-w-h28 bash "${probe}" rapl-exec) \
        || { echo "probe.sh rapl-exec failed"; return 1; }
    [ "${out}" = "docker exec k3dgpu-w-rapl" ] || { echo "given reader replaced by '${out}'"; return 1; }
    out=$(PATH=${nosudo}:${PATH} CLUSTER=k3dgpu-w-h28 env -u K3D_GPU_RAPL_EXEC bash "${probe}" rapl-exec)
    [ "${out}" = "docker exec k3d-k3dgpu-w-h28-server-0" ] || { echo "default without sudo: '${out}'"; return 1; }
}

row_guards() {
    fresh
    IMAGE=cryptoandcoffee/k3d-gpu:0.1.74 bash "${ROW}" h25w 2> /dev/null; [ $? = 2 ] || { echo "IMAGE without a digest accepted"; return 1; }
    IMAGE=${IMG} bash "${ROW}" h57w 1 perf 2> /dev/null; [ $? = 2 ] || { echo "h57w ran without the k3d shim"; return 1; }
    WILDCT_HOLD=X bash "${ROW}" h57w-convert 2> /dev/null; [ $? = 2 ] || { echo "h57w-convert ran inside a hold"; return 1; }
    # WILDCT_ROW_CHECK=1 (launch's pre-check): every check, the k3d shim included; runs nothing.
    local a
    chk() { WILDCT_ROW_CHECK=1 IMAGE=${IMG} bash "${ROW}" "$@" > "${W}/chk.out" 2>&1; }
    chk h57w 1 perf; [ $? = 2 ] || { echo "check mode passed h57w without the k3d shim"; return 1; }
    install -D -m 0755 "${ROOT}/bench/h57d/k3d-shim.sh" "${HOME}/k3dgpu-bench-wildct/h57/bin/k3d"
    for a in "1 perf" "2 perf" "3 perf" "4 quality" "5 perf Ld Ld-mtp Od" "5 quality Ld Od Ld-mtp"; do
        # shellcheck disable=SC2086 # the row's arguments, split on purpose
        chk h57w ${a} || { echo "check mode refused h57w ${a}: $(cat "${W}/chk.out")"; return 1; }
        [ ! -s "${W}/chk.out" ] || { echo "check mode printed or ran something for h57w ${a}"; return 1; }
    done
    for a in "1 quality" "4 perf" "5 Perf Ld" "5 perf Xx" "1 perf Ld" "6 perf" "5 perf" "0 perf" "5 perf Ld Ld"; do
        # shellcheck disable=SC2086 # the row's arguments, split on purpose
        chk h57w ${a}; [ $? = 2 ] || { echo "check mode passed h57w ${a}"; return 1; }
    done
    chk h25w || { echo "check mode refused h25w: $(cat "${W}/chk.out")"; return 1; }
    [ "$(calls '')" = 0 ] || { echo "check mode ran a command: $(cat "${W}/calls.log")"; return 1; }
    bash "${ROW}" nope 2> /dev/null; [ $? = 2 ] || { echo "unknown row accepted"; return 1; }
}

row_smoke_runs_up_scan_down() {
    h52w_fresh
    printf 'kube-system coredns-1 1/1 Running 0 1m\n' > "${W}/pods"
    OUT=${W}/smoke IMAGE=${IMG} timeout -k 5 120 bash "${ROW}" smoke > /dev/null 2>&1 || { echo "smoke exit $?"; cat "${W}/smoke/rc.txt"; return 1; }
    grep -q '^pods ready: yes' "${W}/smoke/rc.txt" && grep -qx 'GPU-SCAN clean' "${W}/smoke/rc.txt" \
        || { cat "${W}/smoke/rc.txt"; return 1; }
    [ "$(calls '^k3d-gpu up')" = 1 ] && [ "$(calls '^k3d-gpu down')" = 1 ] || { echo "up/down calls"; return 1; }
    local s
    for s in "60 kubectl get pods -A -o wide" "60 kubectl get pods -A -o json"; do
        grep -q -- "^timeout -k [0-9]* ${s}\$" "${W}/calls.log" || { echo "smoke: not bounded: ${s}"; return 1; }
    done
    echo 1 > "${W}/up_rc"
    OUT=${W}/smoke2 IMAGE=${IMG} timeout -k 5 120 bash "${ROW}" smoke > /dev/null 2>&1 && { echo "failed up reported as success"; return 1; }
    grep -q '^pods ready: not waited (up rc=1)' "${W}/smoke2/rc.txt" || { cat "${W}/smoke2/rc.txt"; return 1; }
}

check "healthy hold: health check, RAPL reading, cleanup of only k3dgpu-w-*, pid and rc" healthy_hold_runs_and_cleans
check "systemd not answering: rc 8, no container started" wedged_systemd_stops_before_docker
check "systemd answering with an error: rc 8, no container started" systemd_error_stops_before_docker
check "docker info not answering: rc 8, no container started" docker_info_hang_stops_before_docker_run
check "RAPL reader stuck in docker run: bounded, rc 6, CMD not run" rapl_run_hang_is_bounded
check "RAPL reader without a reading: rc 6, CMD not run" rapl_unreadable_stops_hold
check "cleanup calls that hang (one ignoring SIGTERM) are bounded and named in cleanup-incomplete" cleanup_hang_is_bounded_and_named
check "disk floor and launcher exits write runs/NAME/rc" early_exits_write_rc
check "k3d cluster list not answering in cleanup is named in cleanup-incomplete" cluster_list_hang_in_cleanup_is_named
check "a second TERM during cleanup: cleanup finishes, rc 143 written" second_signal_during_cleanup_still_writes_rc
check "an earlier attempt under the same NAME is moved aside, not mixed in" earlier_attempt_is_moved_aside
check "a TERM during the lock wait ends hold.sh at once with rc 143" term_during_lock_wait_is_prompt
check "health is checked again just before the hold starts" health_is_rechecked_before_start
check "k3d not answering or a leftover not deleted before the hold: rc 8, nothing started" k3d_not_answering_before_start_is_no_hold
check "CMD does not inherit the host lock's fd" cmd_does_not_hold_the_lock_fd
check "a TERM during the load wait ends hold.sh at once; no sleep left" term_during_load_wait_is_prompt
check "processes the command leaves (any process group) are stopped; others are not" leftover_processes_are_stopped
check "an earlier attempt that cannot be moved aside: exit 9, nothing written in it" unmovable_earlier_attempt_stops
check "wait_hold.sh: rc 0, rc != 0, dead without rc, ceiling" wait_hold_reports_each_end
check "wait_hold.sh: an empty rc file is not an rc" wait_hold_empty_rc_is_not_an_rc
check "wait_hold.sh follows a real hold to its rc" wait_hold_follows_a_real_hold
check "h52w.sh: an empty pod list is not ready, at most H52W_POLLS polls" h52w_empty_pod_list_is_not_ready
check "h52w.sh: Running and Completed pods are ready" h52w_running_pods_are_ready
check "h52w.sh: no pod wait when up failed" h52w_failed_up_is_not_waited_on
check "h52w.sh: detect, doctor, up, each pod poll, test and down are bounded" h52w_steps_are_bounded
check "row.sh: every row's environment matches the wildct pre-registration" row_envs_match_the_registration
check "RESUME.md: every launch line names a row row.sh runs, with the registered CAP and NEED" resume_launch_lines_match_the_registration
check "row.sh: digest-pinned IMAGE, the H57 k3d shim, convert outside a hold, unknown rows" row_guards
check "H28w's probe.sh keeps hold.sh's RAPL reader (W4); its default is unchanged" probe_keeps_the_given_rapl_reader
check "row.sh smoke: up, pod wait, GPU scan, down; a failed up fails the smoke" row_smoke_runs_up_scan_down

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
