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
#   systemd_hang systemd_fail info_hang run_hang exec_empty rm_hang k3d_hang  (present = on);
#   rm_hang_noterm: docker rm hangs and ignores SIGTERM
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
[ -e "${SHIM_DIR}/systemd_hang" ] && exec sleep 1000
[ -e "${SHIM_DIR}/systemd_fail" ] && { echo "Failed to get properties: Connection timed out" >&2; exit 1; }
echo active
EOF
    cat > "${b}/k3d" <<'EOF'
#!/usr/bin/env bash
echo "k3d $*" >> "${SHIM_DIR}/calls.log"
if [ "$1 $2" = "cluster list" ]; then
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
    printf '#!/usr/bin/env bash\n[ "${!#}" = /proc/loadavg ] && { echo 0.00; exit 0; }\nexec %s "$@"\n' "${REAL_CUT}" > "${b}/cut"
    chmod +x "${b}"/*
}

R() { echo "${HOME}/k3dgpu-bench-wildct/runs/$1"; }
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
    hold H1 sh -c 'cp "$HOME/k3dgpu-bench-wildct/runs/H1/pid" "$SHIM_DIR/pid_seen"; echo ran > "$SHIM_DIR/ran"'
    [ "${RC}" = 0 ] || { echo "rc ${RC}"; cat "${W}/hold.out"; return 1; }
    [ "$(rcfile H1)" = 0 ] || { echo "rc file: $(rcfile H1)"; return 1; }
    [ -s "${W}/ran" ] || { echo "CMD did not run"; return 1; }
    [ -s "${W}/pid_seen" ] || { echo "no runs/H1/pid while CMD ran"; return 1; }
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
    grep -q 'H5 RAPL reader did not start' "${HOME}/k3dgpu-bench-wildct/runs/campaign.log" || { echo "no log line"; return 1; }
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
    chmod +x "${WB}/k3d-gpu/usr/bin/k3d-gpu" "${WB}/bin/kubectl"
}
h52w() { H52W_POLLS=3 H52W_POLL_S=0 timeout -k 5 120 bash "${H52W}" "${W}/out" > /dev/null 2>&1; }

h52w_empty_pod_list_is_not_ready() {
    h52w_fresh
    h52w
    grep -q '^pods ready: no' "${W}/out/rc.txt" || { echo "rc.txt:"; cat "${W}/out/rc.txt"; return 1; }
    [ "$(calls 'kubectl get pods -A --no-headers')" = 3 ] || { echo "polls: $(calls 'kubectl get pods -A --no-headers')"; return 1; }
}

h52w_running_pods_are_ready() {
    h52w_fresh
    printf 'kube-system coredns-1 1/1 Running 0 1m\nkube-system nri-1 1/1 Running 0 1m\nkube-system helm-1 0/1 Completed 0 1m\n' > "${W}/pods"
    h52w
    grep -q '^pods ready: yes' "${W}/out/rc.txt" || { echo "rc.txt:"; cat "${W}/out/rc.txt"; return 1; }
}

h52w_failed_up_is_not_waited_on() {
    h52w_fresh
    echo 1 > "${W}/up_rc"
    h52w
    grep -q '^pods ready: not waited (up rc=1)' "${W}/out/rc.txt" || { echo "rc.txt:"; cat "${W}/out/rc.txt"; return 1; }
    [ "$(calls 'kubectl get pods -A --no-headers')" = 0 ] || { echo "polled a cluster that did not come up"; return 1; }
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
}

row_guards() {
    fresh
    IMAGE=cryptoandcoffee/k3d-gpu:0.1.74 bash "${ROW}" h25w 2> /dev/null; [ $? = 2 ] || { echo "IMAGE without a digest accepted"; return 1; }
    IMAGE=${IMG} bash "${ROW}" h57w 1 perf 2> /dev/null; [ $? = 2 ] || { echo "h57w ran without the k3d shim"; return 1; }
    WILDCT_HOLD=X bash "${ROW}" h57w-convert 2> /dev/null; [ $? = 2 ] || { echo "h57w-convert ran inside a hold"; return 1; }
    bash "${ROW}" nope 2> /dev/null; [ $? = 2 ] || { echo "unknown row accepted"; return 1; }
}

row_smoke_runs_up_scan_down() {
    h52w_fresh
    printf 'kube-system coredns-1 1/1 Running 0 1m\n' > "${W}/pods"
    OUT=${W}/smoke IMAGE=${IMG} timeout -k 5 120 bash "${ROW}" smoke > /dev/null 2>&1 || { echo "smoke exit $?"; cat "${W}/smoke/rc.txt"; return 1; }
    grep -q '^pods ready: yes' "${W}/smoke/rc.txt" && grep -qx 'GPU-SCAN clean' "${W}/smoke/rc.txt" \
        || { cat "${W}/smoke/rc.txt"; return 1; }
    [ "$(calls 'k3d-gpu up')" = 1 ] && [ "$(calls 'k3d-gpu down')" = 1 ] || { echo "up/down calls"; return 1; }
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
check "wait_hold.sh: rc 0, rc != 0, dead without rc, ceiling" wait_hold_reports_each_end
check "wait_hold.sh follows a real hold to its rc" wait_hold_follows_a_real_hold
check "h52w.sh: an empty pod list is not ready, at most H52W_POLLS polls" h52w_empty_pod_list_is_not_ready
check "h52w.sh: Running and Completed pods are ready" h52w_running_pods_are_ready
check "h52w.sh: no pod wait when up failed" h52w_failed_up_is_not_waited_on
check "row.sh: every row's environment matches the wildct pre-registration" row_envs_match_the_registration
check "row.sh: digest-pinned IMAGE, the H57 k3d shim, convert outside a hold, unknown rows" row_guards
check "row.sh smoke: up, pod wait, GPU scan, down; a failed up fails the smoke" row_smoke_runs_up_scan_down

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
