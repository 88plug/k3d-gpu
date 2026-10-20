#!/usr/bin/env bash
# Tests for bench/admission/run.sh without root, docker or OVMS: docker, curl and
# sudo are shims on PATH, and tests/openai_stub.py serves the requests. Each case
# uses its own lock file, never ~/k3dgpu-bench.lock.
#
# Usage: bench/admission/test_run.sh

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
WORK=$(mktemp -d)
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

python3 "${REPO}/tests/openai_stub.py" --port 0 --tokens 4 --ttft 0.01 --itl 0.001 > "${WORK}/stub.out" 2> /dev/null &
stub=$!
trap 'kill "${stub}" 2> /dev/null; rm -rf "${WORK}"' EXIT
for _ in $(seq 1 50); do [ -s "${WORK}/stub.out" ] && break; sleep 0.1; done
PORT=$(awk '{print $2; exit}' "${WORK}/stub.out")

mkdir -p "${WORK}/bin" "${WORK}/model"
cat > "${WORK}/bin/docker" << 'EOF'
#!/usr/bin/env bash
case $1 in run) echo shim-id ;; inspect) exit 1 ;; esac
exit 0
EOF
cat > "${WORK}/bin/curl" << 'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "${WORK}/bin/sudo" << 'EOF'
#!/usr/bin/env bash
while [ "${1:-}" = -n ] || [ "${1:-}" = -S ] || [ "${1:-}" = -p ]; do
    [ "$1" = -p ] && shift; shift
done
case "$1 ${2##*/} ${3:-}" in
    "bash cpufreq.sh snapshot") echo snap; exit 0 ;;
    "bash cpufreq.sh rapl") echo "1 1000000000"; exit 0 ;;
esac
[ "$1" = perf ] && exec sleep 3599.25
exec "$@"
EOF
chmod +x "${WORK}/bin/"*
echo snap > "${WORK}/snap"

if [ ! -f "${HERE}/run.sh" ]; then
    fail "run.sh does not exist"
    echo "${PASS} passed, ${FAILS} failed"
    exit 1
fi

# 1. The hold cap ends the round on time. Round 1 starts with arm Q, whose window
# (60 s here) is far longer than the 6 s cap. The round must end within a few
# seconds of the cap, write INCOMPLETE, release the lock and leave no load.py.
out="${WORK}/cap"
t0=$(date +%s)
PATH="${WORK}/bin:${PATH}" K3DGPU_LIVE_LOCK="${WORK}/cap.lock" AD_SNAPSHOT="${WORK}/snap" \
    AD_MODEL_DIR="${WORK}/model" AD_PORT="${PORT}" AD_HOLD_S=6 AD_IDLE_S=0 AD_LEAD_S=0 \
    AD_WINDOW_S=60 timeout 120 bash "${HERE}/run.sh" "${out}" 1 > /dev/null 2>&1
rc=$?
dt=$(( $(date +%s) - t0 ))
if [ "${dt}" -le 15 ]; then ok "hold_cap_on_time (${dt}s, cap 6s)"; else fail "hold_cap_on_time: ${dt}s for a 6s cap (rc ${rc})"; fi
if [ "$(cat "${out}/round1.status" 2> /dev/null)" = INCOMPLETE ]; then ok "hold_cap_writes_incomplete"
else fail "hold_cap_writes_incomplete: status '$(cat "${out}/round1.status" 2> /dev/null)'"; fi
if flock -n "${WORK}/cap.lock" true; then ok "hold_cap_releases_lock"; else fail "hold_cap_releases_lock"; fi
sleep 1
if pgrep -f "load.py .*--out ${out}/" > /dev/null; then
    fail "hold_cap_kills_load"; pkill -f "load.py .*--out ${out}/"
else
    ok "hold_cap_kills_load"
fi
# The root-side child (perf, here the sudo shim's sleep) is signalled too.
if pgrep -fx 'sleep 3599.25' > /dev/null; then
    fail "hold_cap_kills_root_child"; pkill -fx 'sleep 3599.25'
else
    ok "hold_cap_kills_root_child"
fi

# 2. The pre-roll is 20 quiet requests and the reference is their median.
if [ -s "${out}/preroll-r1.json" ] && [ -s "${out}/ref-r1.txt" ] &&
   python3 - "${out}/preroll-r1.json" "$(cat "${out}/ref-r1.txt")" << 'PY'
import json, statistics, sys
s = [x["ttft_s"] for p in json.load(open(sys.argv[1]))["phases"] for x in p["samples"] if x["ok"]]
assert len(s) == 20, len(s)
assert abs(round(statistics.median(s), 4) - float(sys.argv[2])) < 1e-9
PY
then ok "preroll_20_median_reference"; else fail "preroll_20_median_reference"; fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" = 0 ]
