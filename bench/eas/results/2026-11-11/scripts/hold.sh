#!/bin/bash
# hold.sh <checkout> <run name> <cap min> <model-add args...>: under ~/k3dgpu-bench.lock
# (waits up to 12 h; flock -o, so no child keeps the lock descriptor): snapshot and stop
# aiperf010, qwen38-b-hr, qwen38-a-hr (takeover-stop.sh, into ~/k3dgpu-takeover/<run>), run
# run.sh under its cap, delete the cluster if anything is left, start the servers again in
# reverse order and wait for both baseline answers (restore.sh), then compare per-card VRAM
# with the snapshot. The lock is released only after the restore.
set -u
WT=$1 RUN=$2 CAP=$3
shift 3
BIN=$(cd "$(dirname "$0")" && pwd)
OUT="${HOME}/k3dgpu-bench/b70eas/${RUN}"
TK="${HOME}/k3dgpu-takeover/${RUN}"
CLUSTER=k3dgpu-b70eas
export PATH="${HOME}/k3dgpu-bench/bin:${HOME}/.local/bin:${PATH}"
mkdir -p "${OUT}" "${TK}"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "${OUT}/hold.log"; }
echo $$ > "${OUT}/hold.pid"
log "waiting for ~/k3dgpu-bench.lock (pid $$)"
exec flock -o -w 43200 "${HOME}/k3dgpu-bench.lock" bash -c '
    WT=$1 RUN=$2 CAP=$3 BIN=$4 OUT=$5 TK=$6 CLUSTER=$7
    shift 7
    log() { echo "$(date -u +%FT%TZ) $*" | tee -a "${OUT}/hold.log"; }
    log "lock taken"
    used=$(df --output=pcent / | tail -1 | tr -dc 0-9)
    log "disk / ${used}% used"
    if [ "${used}" -ge 90 ]; then log "disk gate: not starting"; exit 4; fi
    bash "${BIN}/takeover-stop.sh" "${TK}" >> "${OUT}/hold.log" 2>&1
    log "stopped: $(tr "\n" " " < "${TK}/stopped.txt")"
    "$(command -v gnutimeout || command -v timeout)" --signal=TERM --kill-after=180 $(( CAP * 60 + 600 )) bash "${BIN}/run.sh" "${WT}" "${CLUSTER}" "${OUT}" "${CAP}" "$@" >> "${OUT}/hold.log" 2>&1
    rc=$?
    log "run.sh rc=${rc}"
    echo "${rc}" > "${OUT}/run.rc"
    if k3d cluster get "${CLUSTER}" >/dev/null 2>&1; then
        log "cluster ${CLUSTER} still there: deleting"
        "$(command -v gnutimeout || command -v timeout)" 600 k3d cluster delete "${CLUSTER}" >> "${OUT}/hold.log" 2>&1
    fi
    for id in $(docker ps -q); do
        docker inspect -f "{{range .Mounts}}{{.Source}} {{end}}" "${id}" 2>/dev/null | grep -qF "${OUT}/state" && docker rm -f "${id}" >/dev/null 2>&1
    done
    bash "${BIN}/restore.sh" "${TK}" >> "${OUT}/hold.log" 2>&1
    rrc=$?
    log "restore rc=${rrc}"
    curl -s -m 10 localhost:9101/metrics > "${TK}/xe-exporter-restored.prom"
    python3 - "${TK}/xe-exporter-before.prom" "${TK}/xe-exporter-restored.prom" > "${TK}/vram-compare.txt" <<"PY"
import re, sys
def read(p):
    d = {}
    for line in open(p):
        m = re.match(r"xe_vram_used_bytes\{.*card=\"(card\d+)\".*\}\s+(\S+)", line)
        if m:
            d[m.group(1)] = float(m.group(2)) / 2**30
    return d
b, a = read(sys.argv[1]), read(sys.argv[2])
ok = bool(b) and all(c in a and a[c] >= 0.9 * b[c] for c in b)
for c in sorted(b):
    print("%s before %.2f GiB restored %.2f GiB" % (c, b[c], a.get(c, float("nan"))))
print("VRAM back: %s" % ("yes" if ok else "no"))
PY
    log "$(tr "\n" ";" < "${TK}/vram-compare.txt")"
    grep -q "VRAM back: yes" "${TK}/vram-compare.txt" || rrc=1
    echo "${rrc}" > "${OUT}/restore.rc"
    log "lock released"
    exit "${rc}"
' _ "${WT}" "${RUN}" "${CAP}" "${BIN}" "${OUT}" "${TK}" "${CLUSTER}" "$@"
