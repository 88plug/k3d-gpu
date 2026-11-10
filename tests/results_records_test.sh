#!/usr/bin/env bash
# Benchmark records stay committable and stay as they were copied.
#
# 1. .gitignore's "*.log" (build output) does not apply under a results/ directory
#    of bench/, so a plain `git add` of a results directory adds its logs. Logs
#    elsewhere stay ignored.
# 2. Every SHA256SUMS under bench/*/results/ verifies against the files next to it.
#    Some list host files that were not committed, so missing files are skipped; the
#    record directories named below list only committed files and verify in full.
#
# Usage: tests/results_records_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}" || exit 2
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

# --- 1. result logs are not ignored ----------------------------------------
kept=(
    bench/xhost/results/2026-10-20/H25x/hold/hold.log
    bench/xhost/results/2026-10-20/H25x/hold/out/typical/cache-usage-1.log
    bench/eatflow/results/2026-10-20/setup/export.log
    bench/h54/results/2026-10-20/host/bw.log
    bench/h54/results/2026-10-21/builds/convert.log
    bench/newrow/results/2099-01-01/H99/runs/H99-hold1/hold.log
)
for p in "${kept[@]}"; do
    if git check-ignore -q --no-index "${p}"; then fail "${p} is ignored"; else ok "${p} is not ignored"; fi
done
ignored=(build.log scripts/run.log bench/xhost/scratch.log tests/out/x.log)
for p in "${ignored[@]}"; do
    if git check-ignore -q --no-index "${p}"; then ok "${p} stays ignored"; else fail "${p} is not ignored"; fi
done

# --- 2. SHA256SUMS verify -----------------------------------------------------
records=(
    bench/eatflow/results/2026-10-20/H52/inventory
    bench/eatflow/results/2026-10-20/setup
    bench/h54/results/2026-10-20/host
    bench/h54/results/2026-10-21/builds
    bench/xhost/results/2026-10-20
)
for d in "${records[@]}"; do
    if [ -f "${d}/SHA256SUMS" ] && (cd "${d}" && sha256sum --strict --quiet -c SHA256SUMS); then
        ok "${d}/SHA256SUMS verifies, every file present"
    else
        fail "${d}/SHA256SUMS missing or does not verify"
    fi
done
n=0
while IFS= read -r -d '' s; do
    n=$((n + 1))
    d=$(dirname "${s}")
    if (cd "${d}" && sha256sum --strict --quiet --ignore-missing -c SHA256SUMS); then
        ok "${s} verifies"
    else
        fail "${s} does not verify"
    fi
done < <(find bench -path '*/results/*' -name SHA256SUMS -print0 | sort -z)
if [ "${n}" -gt 0 ]; then ok "${n} SHA256SUMS under bench/*/results/"; else fail "no SHA256SUMS under bench/*/results/"; fi

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
