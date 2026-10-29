#!/usr/bin/env bash
# b1g-stall.sh OUTDIR: UNSCORED diagnostic of the H54a B1g stall (EXPERIMENTS.md, "H54a run":
# vLLM CPU v0.24.0 stopped mid-decode in both holds, EngineDeadError after the 300 s
# execute_model RPC timeout). Not a hold, not an arm; no hypothesis is scored from it.
#
# It starts B1g's container exactly as rendered in the committed H54a record
# (bench/h54/results/2026-10-21/H54a/hold2/B1g/deploy.yaml: image digest, args, env ConfigMap,
# chat-template ConfigMap, user 1001, read-only root, capabilities, no CPU or memory request or
# limit) with plain `docker run` instead of k3d, sends sequential 256-token greedy requests with
# the harness client (bench/llm_load.py, the H54a N=1 settings), and watches vLLM's /metrics.
# On a stall it records each thread of the container's processes twice, 10 s apart, and a
# py-spy dump of each process, then waits for the engine to die or 400 s.
#
# Box rules: the exclusive bench lock (~/k3dgpu-bench.lock, the K=1 hold lock), the hold-start
# rule (load1 <= 12 and no aiperf process, else wait up to 30 min, then refuse), the 97% disk
# line, `timeout -k 30 2400` on the whole run, no GPU device, nothing that serves is touched.
# The container is this script's own (name k3dgpu-diag-b1g-*): it is stopped and removed at the
# end, also on failure. Model weights are mounted read-only.
set -uo pipefail
OUT=${1:?usage: b1g-stall.sh OUTDIR}
D=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
H=$(cd "${D}/.." && pwd)
REPO=$(cd "${H}/../.." && pwd)
IMAGE=${DIAG_IMAGE:-vllm/vllm-openai-cpu@sha256:b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1}
MODELS=${DIAG_MODELS:-${HOME}/k3dgpu-gemma/models}
MDIR=google_gemma-4-26B-A4B-it-qat-q4_0-unquantized
DEPLOY=${DIAG_DEPLOY:-${H}/results/2026-10-21/H54a/hold2/B1g/deploy.yaml}
PORT=${DIAG_PORT:-18080}
REQS=${DIAG_REQUESTS:-60}
export DIAG_PYSPY=${DIAG_PYSPY:-${HOME}/k3dgpu-gemma/b1g-diag/pyspy/bin/py-spy}
NAME=k3dgpu-diag-b1g-$(date -u +%H%M%S)
PY=python3
mkdir -p "${OUT}"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "${OUT}/diag.log"; }
aiperf_n() { docker top aiperf010 -eo pid,args 2>/dev/null | awk 'NR>1 && $0 !~ /sleep infinity/' | wc -l; }
disk() { df -P / | awk 'NR==2 {gsub("%", "", $5); print $5}'; }
snapshot() { date -u +%FT%TZ; uptime; ps -eo pcpu,pid,user,comm --sort=-pcpu | head -11; echo "aiperf_procs: $(aiperf_n)"; df -P / | tail -1; docker ps --format '{{.Names}} {{.Status}}'; }

if [ ! -x "${DIAG_PYSPY}" ]; then   # py-spy 0.4.2 (pinned) in its own venv, outside the lock
    python3 -m venv "$(dirname "$(dirname "${DIAG_PYSPY}")")" && "$(dirname "${DIAG_PYSPY}")/pip" install -q py-spy==0.4.2 \
        || { echo "py-spy install failed" >&2; exit 2; }
fi
if [ -z "${DIAG_LOCKED:-}" ]; then
    log "waiting for the bench lock"
    exec 9> "${HOME}/k3dgpu-bench.lock"
    flock -w 7200 9 || { log "lock not taken in 7200 s"; exit 4; }
    log "lock taken"
    export DIAG_LOCKED=1
    exec timeout -k 30 2400 bash "$0" "$@"
fi

waited=0
while :; do
    l1=$(cut -d' ' -f1 /proc/loadavg) a=$(aiperf_n)
    if awk -v l="${l1}" 'BEGIN {exit !(l > 12)}' || [ "${a}" -gt 0 ]; then
        [ "${waited}" -lt 1800 ] || { log "busy after 30 min (load1 ${l1}, aiperf ${a}): not run"; exit 5; }
        log "busy (load1 ${l1}, aiperf ${a}): waiting 120 s"
        sleep 120; waited=$(( waited + 120 )); continue
    fi
    break
done
[ "$(disk)" -le 97 ] || { log "disk $(disk)% > 97%: not run"; exit 5; }
snapshot > "${OUT}/pre.txt"
{ echo "repo: $(git -C "${REPO}" rev-parse HEAD) dirty=$(git -C "${REPO}" status --porcelain | wc -l)"
  echo "image: ${IMAGE}"; docker image inspect "${IMAGE}" --format 'id={{.Id}} entrypoint={{json .Config.Entrypoint}}'
  echo "model: ${MODELS}/${MDIR}"; (cd "${MODELS}/${MDIR}" && sha256sum config.json generation_config.json model.safetensors.index.json)
  echo "py-spy: $(${DIAG_PYSPY} --version 2>&1)"; uname -r; nproc; } > "${OUT}/reproduce.txt" 2>&1

${PY} "${D}/b1g_diag.py" render "${DEPLOY}" "${OUT}/render" || { log "render failed"; exit 2; }
mapfile -t ARGS < <(${PY} -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))))' "${OUT}/render/args.json")
cleanup() {
    docker logs "${NAME}" > "${OUT}/server.log" 2>&1
    docker inspect "${NAME}" > "${OUT}/container.json" 2>/dev/null
    docker stop -t 30 "${NAME}" > /dev/null 2>&1
    docker rm "${NAME}" > /dev/null 2>&1
    [ -n "${SP:-}" ] && kill "${SP}" 2>/dev/null
    snapshot > "${OUT}/post.txt"
    gzip -f "${OUT}/server.log"
    log "cleaned up ${NAME}"
}
trap cleanup EXIT
trap 'exit 143' TERM INT

log "start ${NAME} (${IMAGE})"
docker run -d --name "${NAME}" --label k3dgpu.diag=b1g-stall --restart no \
    --user 1001 --read-only --cap-drop ALL --cap-add SYS_NICE --security-opt no-new-privileges \
    --ipc private --shm-size 1g \
    --tmpfs /tmp:rw,exec,size=8g --tmpfs /root/.cache/vllm:rw,exec,size=1g \
    -v "${MODELS}/${MDIR}:/data/${MDIR}:ro" -v "${OUT}/render/templates:/workspace/vllm/examples:ro" \
    --env-file "${OUT}/render/env.list" -p "127.0.0.1:${PORT}:2080" \
    "${IMAGE}" "${ARGS[@]}" > "${OUT}/container-id" 2>> "${OUT}/diag.log" || { log "docker run failed"; exit 3; }
CID=$(cat "${OUT}/container-id")
ROOT=$(docker inspect -f '{{.State.Pid}}' "${NAME}")
CG=/sys/fs/cgroup/system.slice/docker-${CID}.scope/cpu.stat
python3 "${REPO}/bench/eatflow/crescent/serving.py" "${OUT}/serving.jsonl" &
SP=$!

base=http://127.0.0.1:${PORT}/v1
t0=${SECONDS}
until curl -sf -m 5 "${base}/models" > "${OUT}/models.json" 2>/dev/null \
      && curl -sf -m 120 "${base}/chat/completions" -H 'Content-Type: application/json' \
         -d '{"model":"google/gemma-4-26B-A4B-it","messages":[{"role":"user","content":"Say hello."}],"max_tokens":16,"temperature":0}' \
         > "${OUT}/hello.json" 2>/dev/null; do
    [ "$(docker inspect -f '{{.State.Running}}' "${NAME}" 2>/dev/null)" = true ] || { log "container exited before ready"; exit 3; }
    [ $(( SECONDS - t0 )) -lt 1500 ] || { log "not ready after 1500 s"; exit 3; }
    sleep 10
done
log "ready after $(( SECONDS - t0 )) s; ${REQS} sequential requests"

${PY} "${REPO}/bench/llm_load.py" --base-url "${base}" --model google/gemma-4-26B-A4B-it \
    --requests "${REQS}" --concurrency 1 --warmup 0 --max-tokens 256 --temperature 0 --unique-prompts \
    --ignore-eos --timeout 1800 --label "UNSCORED B1g stall diagnostic" --out "${OUT}/load.json" \
    >> "${OUT}/diag.log" 2>&1 &
CP=$!
${PY} "${D}/b1g_diag.py" watch "http://127.0.0.1:${PORT}" "${CG}" "${OUT}" "${CP}" "${ROOT}"
rc=$?
case ${rc} in
    0) log "no stall in ${REQS} requests" ;;
    1) log "stall recorded (stall.json)"; kill "${CP}" 2>/dev/null ;;
    *) log "watch rc ${rc}" ;;
esac
wait "${CP}" 2>/dev/null
exit 0
