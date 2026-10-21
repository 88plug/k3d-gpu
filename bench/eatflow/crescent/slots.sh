# shellcheck shell=bash
# slots.sh: sourced by hold.sh. Slot mode (HOLD_SLOT=0|1, HOLD_SLOTS=2) runs one hold per
# cpuset slice of crescent (EXPERIMENTS.md "H56 pre-registration: parallel slice holds on
# crescent"). Without HOLD_SLOT nothing here runs and hold.sh is the K=1 hold it always was.
#
# Locks, all flock(2), released by the kernel when the last holder of the file description
# dies. Global order: slot-0 < slot-1 < main (~/k3dgpu-bench.lock), so no wait cycle exists.
#   adoption mode: flock -w 7200 on ~/k3dgpu-bench.lock.d/slot-<i>, then main shared. A hold
#                  queued on its slot holds nothing on main. Before taking main shared it
#                  waits while /proc/locks shows an exclusive request blocked on main (the
#                  k3dgpu-x-* campaign, a legacy hold, h56.sh), so overlapping slot holds
#                  cannot starve an exclusive waiter: one queued behind the slot holds already
#                  running waits for those, plus any slot hold that read /proc/locks just
#                  before the waiter blocked, at most one per slot. Slot holds may wait behind
#                  a stream of exclusive holds; that is the intended priority.
#   HOLD_LOCKS_HELD=1 (bench/eatflow/crescent/h56.sh): the parent holds fds 7 (slot-0),
#                  8 (slot-1) and 9 (main) exclusive. Each fd must be that file, and its
#                  /proc/self/fdinfo must list an exclusive FLOCK: fdinfo lists only locks owned
#                  by that open file description, so this reads ownership without re-locking
#                  (a re-lock would take a lock the parent never took, or convert a shared one).
#                  Else exit 4.
# Owning slot-i proves no live hold owns the s<i> resources, so this hold deletes every
# k3dgpu-(e|a)-s<i>-* cluster and slot-i load container it finds, and nothing else (the load
# through LOAD_SLOT, which load.py reads as its default slot).
#
# Test knobs: HOLD_SYSFS (slice lookup), HOLD_PROC (gate /proc/stat), HOLD_GATE_S (gate
# sample, default 60), HOLD_POLL_S (default 120), HOLD_GATE_MAX_S (default 3600).
SLOT_LOCK_DIR=${HOME}/k3dgpu-bench.lock.d
SLOT_LOAD_DIR=${HERE}/../../load

# slot_init: validate the slot, look up the slice and export the slot to everything the hold runs.
slot_init() {
    local out
    [ "${HOLD_SLOTS:-2}" = 2 ] || { echo "slots: HOLD_SLOTS must be 2 (got ${HOLD_SLOTS})" >&2; return 2; }
    [[ "${HOLD_SLOT}" =~ ^[01]$ ]] || { echo "slots: HOLD_SLOT must be 0 or 1 (got ${HOLD_SLOT})" >&2; return 2; }
    out=$(python3 "${SLOT_LOAD_DIR}/load.py" slice --slots 2 --slot "${HOLD_SLOT}" --sysfs "${HOLD_SYSFS:-/sys}") \
        || { echo "slots: no slice ${HOLD_SLOT} of 2 on this host" >&2; return 2; }
    read -r SLOT_CPUS SLOT_LOAD_CPUS <<< "${out}"
    export LIVE_SLOT=${HOLD_SLOT} LOAD_SLOT=${HOLD_SLOT} LOAD_CPUS=${SLOT_LOAD_CPUS} LIVE_NODE_CPUS=${SLOT_CPUS} \
        SERVING_SLOT=${HOLD_SLOT} SERVING_CPUS=${SLOT_CPUS}
    echo "slot=${HOLD_SLOT} slots=2 cpus=${SLOT_CPUS} load_cpus=${SLOT_LOAD_CPUS} locks_held=${HOLD_LOCKS_HELD:-0}" > "${R}/slot.txt"
}

# fd_holds_x FD: this shell's open file description on FD holds an exclusive flock.
fd_holds_x() {
    grep -qE '^lock:.*[[:space:]]FLOCK[[:space:]]+ADVISORY[[:space:]]+WRITE[[:space:]]' "/proc/self/fdinfo/$1" 2>/dev/null
}

# x_waiting FILE: an exclusive flock request is blocked on FILE ("->" lines of /proc/locks,
# matched by inode; another file system's equal inode only makes a slot hold wait longer).
x_waiting() {
    local ino
    ino=$(stat -c %i "$1") || return 1
    grep -qE -- "->[[:space:]]+FLOCK[[:space:]]+ADVISORY[[:space:]]+WRITE[[:space:]]+[0-9-]+[[:space:]]+[0-9a-f]+:[0-9a-f]+:${ino}[[:space:]]" /proc/locks
}

slot_lock() {
    local fd f end
    if [ "${HOLD_LOCKS_HELD:-0}" = 1 ]; then
        for fd in 7 8 9; do
            case ${fd} in
                7) f=${SLOT_LOCK_DIR}/slot-0 ;;
                8) f=${SLOT_LOCK_DIR}/slot-1 ;;
                9) f=${HOME}/k3dgpu-bench.lock ;;
            esac
            if ! { [ "/proc/self/fd/${fd}" -ef "${f}" ] && fd_holds_x "${fd}"; }; then
                log "inherited lock fd ${fd} (${f}) not held exclusive by this file description"
                return 4
            fi
        done
        return 0
    fi
    mkdir -p "${SLOT_LOCK_DIR}"
    exec 8> "${SLOT_LOCK_DIR}/slot-${HOLD_SLOT}"
    flock -w 7200 8 || { log "slot-${HOLD_SLOT} not taken in 7200 s"; return 4; }
    exec 9> "${HOME}/k3dgpu-bench.lock"
    end=$(( SECONDS + 7200 ))
    while x_waiting "${HOME}/k3dgpu-bench.lock"; do
        [ "${SECONDS}" -lt "${end}" ] || { log "lock not taken in 7200 s (an exclusive waiter queued throughout)"; return 4; }
        sleep 1
    done
    flock -s -w $(( end - SECONDS > 0 ? end - SECONDS : 0 )) 9 || { log "lock not taken in 7200 s"; return 4; }
}

# slot_busy: busy % of this slice's CPUs over HOLD_GATE_S, from /proc/stat ("" if unread).
slot_busy() {
    python3 - "${SLOT_LOAD_DIR}" "${SLOT_CPUS}" "${HOLD_PROC:-/proc}/stat" "${HOLD_GATE_S:-60}" 7>&- 8>&- 9>&- <<'PY'
import sys, time
sys.path.insert(0, sys.argv[1])
import load
cpus, stat, s = load.parse_cpulist(sys.argv[2]), sys.argv[3], float(sys.argv[4])
b0, t0 = load.cpu_times(load._read(stat), cpus)
time.sleep(s)
b1, t1 = load.cpu_times(load._read(stat), cpus)
print(f"{100 * (b1 - b0) / (t1 - t0):.2f}" if t1 > t0 else "")
PY
}

# slot_gate: the slotted hold-start rule (H56): own-slice busy <= 12.5% (12 of 96, the rule-1
# analog) and no aiperf process, polled every HOLD_POLL_S for at most HOLD_GATE_MAX_S, then
# the hold runs flagged. Sets `waited`.
slot_gate() {
    local b a max=${HOLD_GATE_MAX_S:-3600} poll=${HOLD_POLL_S:-120}
    waited=0
    while :; do
        b=$(slot_busy) a=$(aiperf_n)
        if [ -z "${b}" ] || awk -v b="${b}" 'BEGIN {exit !(b > 12.5)}' || [ "${a}" -gt 0 ]; then
            if [ "${waited}" -ge "${max}" ]; then
                log "FLAGGED: still busy after ${max} s (own-slice busy ${b:-unread}%, aiperf ${a})"
                echo "own_busy ${b:-unread} aiperf ${a}" > "${R}/flagged"
                break
            fi
            log "busy (own-slice busy ${b:-unread}%, aiperf ${a}): waiting ${poll} s"
            # As hold.sh's legacy gate: a signal ends the wait (stop_if_signalled), and the
            # sleep holds no lock fd.
            sleep "${poll}" 7>&- 8>&- 9>&- &
            wait "$!"; kill "$!" 2>/dev/null
            stop_if_signalled
            waited=$(( waited + poll ))
            continue
        fi
        break
    done
}

# slot_containers: this slot's live.sh host-side containers only (${PFX}-s<i>-*).
slot_containers() {
    docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E "^k3dgpu-(e|a)-s${HOLD_SLOT}-" | tr '\n' ' '
}

# slot_clusters: this slot's clusters only.
slot_clusters() {
    k3d cluster list -o json 2>/dev/null | python3 -c 'import json,sys,re; print(" ".join(c["name"] for c in json.load(sys.stdin) if re.match(r"k3dgpu-(e|a)-s" + sys.argv[1] + "-", c["name"])))' "${HOLD_SLOT}"
}
