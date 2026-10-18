#!/usr/bin/env bash
# bench/tune-noise/run.sh OUTDIR [LEVEL]: one H19 lock hold (EXPERIMENTS.md, H19 amendment A1).
#
# Fresh cluster ($K3D_GPU_CLUSTER, default k3dgpu-q-h19) on a scratch KUBECONFIG and tune
# dir under OUTDIR, background load LEVEL (default typical) on the E-cores, then:
#   1. k3d-gpu tune run --objective throughput --devices CPU --precisions int4 --budget $H19_TUNE_MIN
#   2. the candidate (analyze.py candidate) against the default in fresh trials, order
#      A B B A A B (A = default), each trial `k3d-gpu tune _trial` at the screening budget
#      (4 requests x 64 tokens at concurrency 1,4,8, cold), one 10 s load.py window
#      right before every trial, tagged with its arm.
# No trial starts after H19_LAST_START_S seconds from start, so the hold ends inside 20 min.
# The load is stopped and the cluster deleted on any exit.
set -euo pipefail
out=${1:?usage: run.sh OUTDIR [LEVEL]}
level=${2:-typical}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "${here}/../.." && pwd)
k3d="${repo}/scripts/k3d-gpu"
loadpy="${repo}/bench/load/load.py"
: "${H19_TUNE_MIN:=4}"
: "${H19_LAST_START_S:=930}"
: "${H19_REPS:=A B B A A B}"
mkdir -p "${out}"
out=$(cd "${out}" && pwd)
export KUBECONFIG="${out}/kubeconfig" K3D_GPU_CLUSTER="${K3D_GPU_CLUSTER:-k3dgpu-q-h19}"
export K3D_GPU_TUNE_DIR="${out}/tune" TMPDIR="${out}/tmp"
mkdir -p "${TMPDIR}"
start=${SECONDS}
mark() { echo "$(date -u +%FT%TZ) +$((SECONDS - start))s $*" | tee -a "${out}/timeline.txt"; }
sampler=""
cleanup() {
    [ -z "${sampler}" ] || kill "${sampler}" 2>/dev/null || true
    python3 "${loadpy}" stop || true
    "${k3d}" down >>"${out}/down.log" 2>&1 || true
    mark "cleanup done (load stopped, cluster deleted)"
}
trap cleanup EXIT

mark "start level=${level} cluster=${K3D_GPU_CLUSTER} commit=$(git -C "${repo}" rev-parse --short HEAD)"
"${k3d}" up >"${out}/up.log" 2>&1
mark "up done"
"${k3d}" agent up >"${out}/agent-up.log" 2>&1
mark "agent up done"
python3 "${loadpy}" start --level "${level}" --ttl 1500 >"${out}/load-start.log"
sleep 20
python3 "${loadpy}" sample --seconds 5 --windows 1000 --tag run --level "${level}" --out "${out}/confounds.jsonl" &
sampler=$!
mark "load ${level} started"

"${k3d}" tune run --objective throughput --devices CPU --precisions int4 --budget "${H19_TUNE_MIN}" \
    >"${out}/tune.log" 2>&1 || mark "tune exited $?"
mark "tune done"
prof=$(ls "${K3D_GPU_TUNE_DIR}"/*.json 2>/dev/null | head -1 || true)
[ -n "${prof}" ] && cp "${prof}" "${out}/profile.json"
if [ -n "${prof}" ]; then
    python3 "${here}/analyze.py" candidate "${prof}" >"${out}/candidate.json"
else
    echo '{"candidate": null, "crowned": false}' >"${out}/candidate.json"
fi
mark "candidate $(cat "${out}/candidate.json")"
python3 -c 'import json, sys; c = json.load(open(sys.argv[1]))["candidate"]; sys.exit(0 if c else 1)' \
    "${out}/candidate.json" || exit 0
python3 -c 'import json, sys; json.dump(json.load(open(sys.argv[1]))["candidate"], open(sys.argv[2], "w"))' \
    "${out}/candidate.json" "${out}/cfg-B.json"
echo '{"baseline": true}' >"${out}/cfg-A.json"
echo '{"requests": 4, "max_tokens": 64, "repeats": 1, "concurrency": [1, 4, 8], "cold": true}' >"${out}/budget.json"

i=0
for arm in ${H19_REPS}; do
    if [ $((SECONDS - start)) -ge "${H19_LAST_START_S}" ]; then
        mark "replication stopped before trial $((i + 1)): ${H19_LAST_START_S}s cap"
        break
    fi
    i=$((i + 1))
    d=$(printf '%s/rep-%02d' "${out}" "${i}")
    mkdir -p "${d}"
    echo "${arm}" >"${d}/arm"
    python3 "${loadpy}" sample --seconds 10 --tag "${arm}" --level "${level}" --out "${out}/load.jsonl"
    "${k3d}" tune _trial "${out}/cfg-${arm}.json" "${out}/budget.json" "${d}/result.json" \
        >"${d}/trial.log" 2>&1 || true
    mark "rep ${i} arm ${arm} ok=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("ok"))' "${d}/result.json" 2>/dev/null || echo none)"
done
