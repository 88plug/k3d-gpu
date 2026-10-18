#!/usr/bin/env bash
# H24 Amendment A1 runner (matched background load). Run it under the live-work lock:
#   flock -w 14400 "$LOCK" env IMAGE=<ovms digest> MODELS=<repo per arm> \
#       SUDO_ASKPASS=<helper> bench/sku-model/run-a1.sh <out dir>
# For each level in LEVELS (default "typical heavy") it holds bench/load/load.py at
# that level from 30 s before the OVMS launch to the end of the probe, runs the probe
# (its own 17 min deadline, outer timeout 1140 s), stops the load and scores. Each
# level gets at most BUDGET_S (1200) of wall time. No k3d cluster is created; the
# trap removes the load containers and every sci-h24-* container.
# sudo is used for RAPL energy_uj reads only (load.py's sudo -A / sudo -n read).
set -euo pipefail
OUT=$1
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PROBE="${REPO}/bench/sku-model/probe.py"
LOADPY="${REPO}/bench/load/load.py"
LEVELS=${LEVELS:-typical heavy}
BUDGET=${BUDGET_S:-1200}
: "${IMAGE:?set IMAGE (OVMS 2026.4.1 by digest)}" "${MODELS:?set MODELS (one repository per arm)}"
mkdir -p "${OUT}"

cleanup() {
    python3 "${LOADPY}" stop >> "${OUT}/facts.txt" 2>&1 || true
    for a in A B C D; do docker rm -f "sci-h24-${a}" >/dev/null 2>&1 || true; done
    echo "end $(date -u +%FT%TZ) load_left=$(docker ps -q --filter label=io.k3d-gpu.bench-load=1 | wc -l)" \
        "sci_left=$(docker ps -aq --filter name=^sci-h24- | wc -l)" >> "${OUT}/facts.txt"
}
trap cleanup EXIT

{
    echo "# provenance"; date -u +%FT%TZ; lscpu | grep 'Model name'; uname -r
    git -C "${REPO}" rev-parse HEAD
    echo "image ${IMAGE}"
    sha256sum "${MODELS}/A/OpenVINO/Qwen3-4B-int4-ov/openvino_model.bin"
    echo "load_cpus $(python3 "${LOADPY}" cpus)"
    echo "containers $(docker ps -q | wc -l)"; free -m | grep -i swap; cat /proc/loadavg
} > "${OUT}/facts.txt" 2>&1

for LEVEL in ${LEVELS}; do
    T0=$(date +%s)
    echo "level ${LEVEL} start $(date -u +%FT%TZ) loadavg $(cut -d' ' -f1-3 /proc/loadavg)" >> "${OUT}/facts.txt"
    python3 "${LOADPY}" start --level "${LEVEL}" --ttl "$(( BUDGET + 120 ))" >> "${OUT}/facts.txt"
    sleep 30
    LEFT=$(( BUDGET - ( $(date +%s) - T0 ) - 30 ))
    [ "${LEFT}" -gt 1140 ] && LEFT=1140
    timeout "${LEFT}" python3 "${PROBE}" --out "${OUT}/${LEVEL}" --level "${LEVEL}" \
        2> "${OUT}/probe-${LEVEL}.log" > /dev/null || echo "probe ${LEVEL} rc=$?" >> "${OUT}/facts.txt"
    python3 "${LOADPY}" stop >> "${OUT}/facts.txt" 2>&1
    for a in A B C D; do docker rm -f "sci-h24-${a}" >/dev/null 2>&1 || true; done
    echo "level ${LEVEL} end $(date -u +%FT%TZ) wall_s=$(( $(date +%s) - T0 )) loadavg $(cut -d' ' -f1-3 /proc/loadavg)" >> "${OUT}/facts.txt"
    python3 "${PROBE}" --score "${OUT}/${LEVEL}/raw.jsonl" > "${OUT}/${LEVEL}/score.json" || true
done
