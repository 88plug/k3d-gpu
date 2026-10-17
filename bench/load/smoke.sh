#!/usr/bin/env bash
# bench/load/smoke.sh — local smoke for the background-load profile (no cluster).
# Samples the host idle, starts the "typical" level, checks the sampler sees the
# load CPUs get busier, checks a second start is a no-op, stops, and checks no
# io.k3d-gpu.bench-load container is left. About 70 s plus the first image pull.
#
# Package W needs root for RAPL energy_uj: set SUDO_ASKPASS (sudo -A) or have
# sudo -n work; without it pkg_w is null and the smoke still runs.
# Env: OUT  results dir (default: a new dir under $TMPDIR)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
L=(python3 "${HERE}/load.py")
OUT=${OUT:-$(mktemp -d "${TMPDIR:-/tmp}/bench-load-smoke.XXXXXX")}
mkdir -p "${OUT}"
leftover() { docker ps -aq --filter "label=io.k3d-gpu.bench-load=1" | wc -l; }
trap '"${L[@]}" stop >/dev/null 2>&1 || true' EXIT

"${L[@]}" stop >/dev/null
echo "load CPUs: $("${L[@]}" cpus)"
"${L[@]}" sample --seconds 15 --tag base --level none --out "${OUT}/samples.jsonl"
"${L[@]}" start --level typical --ttl 180
[ "$(leftover)" -eq 3 ] || { echo "FAIL: expected 3 load containers, have $(leftover)"; exit 1; }
again=$("${L[@]}" start --level typical --ttl 180)
[ "${again}" = "already running" ] || { echo "FAIL: second start was not a no-op: ${again}"; exit 1; }
sleep 10
"${L[@]}" sample --seconds 15 --windows 2 --tag typical --level typical --out "${OUT}/samples.jsonl"
"${L[@]}" status
"${L[@]}" stop
sleep 2
[ "$(leftover)" -eq 0 ] || { echo "FAIL: $(leftover) load container(s) left after stop"; exit 1; }
cat "${OUT}/samples.jsonl"
python3 - "${OUT}/samples.jsonl" <<'EOF'
import json, sys
ws = [json.loads(l) for l in open(sys.argv[1])]
base = [w for w in ws if w["tag"] == "base"][0]
typ = [w for w in ws if w["tag"] == "typical"]
busy = sum(w["busy_pct"] for w in typ) / len(typ)
print(f"load-CPU busy: base {base['busy_pct']} typical {busy:.1f}; "
      f"load1 {base['load1']} -> {typ[-1]['load1']}; pkg_w {base['pkg_w']} -> {typ[-1]['pkg_w']}")
if busy < base["busy_pct"] + 15:
    sys.exit("FAIL: typical did not raise load-CPU busy by >= 15 points")
print("PASS")
EOF
echo "samples: ${OUT}/samples.jsonl"
