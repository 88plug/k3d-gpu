#!/bin/bash
# hold.sh <run root> <label>=<launcher>...: under ~/k3dgpu-bench.lock (waits up to 12 h),
# stop qwen38-a-hr, qwen38-b-hr and aiperf010 (takeover-stop.sh), run each launcher's
# end-state check (run.sh), then the ones appended to <run root>/queue (one
# <label>=<launcher> per line) until <run root>/release exists or the queue has been idle
# for 2 h, then start what was stopped and wait for its baseline answers (restore.sh).
# The lock is released when this script exits.
set -u
ROOT=$1
shift
BIN=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$ROOT" && cd "$ROOT" || exit 1
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$ROOT/hold.log"; }
echo $$ > hold.pid
log "waiting for ~/k3dgpu-bench.lock"
exec 9> "$HOME/k3dgpu-bench.lock"
flock -w 43200 9 || { log "lock not taken in 12 h"; exit 3; }
log "lock taken"
bash "$BIN/takeover-stop.sh" "$ROOT/takeover" 9>&- >> "$ROOT/hold.log" 2>&1
log "stopped: $(tr '\n' ' ' < "$ROOT/takeover/stopped.txt")"
touch queue
for spec in "$@"; do echo "${spec}" >> queue; done
done_n=0 idle=${SECONDS}
while :; do
    n=$(wc -l < queue)
    if [ "${n}" -gt "${done_n}" ]; then
        spec=$(sed -n "$((done_n + 1))p" queue)
        done_n=$((done_n + 1))
        label=${spec%%=*} k=${spec#*=}
        log "run ${label} with ${k}"
        bash "$BIN/run.sh" "${label}" "${k}" "$ROOT/${label}" 9>&- >> "$ROOT/hold.log" 2>&1
        log "run ${label} rc=$?"
        idle=${SECONDS}
        continue
    fi
    if [ -e release ]; then log "release asked"; break; fi
    if [ $((SECONDS - idle)) -ge 7200 ]; then log "queue idle 2 h"; break; fi
    sleep 20
done
bash "$BIN/restore.sh" "$ROOT/takeover" 9>&- >> "$ROOT/hold.log" 2>&1
log "restore rc=$?"
log "lock released"
