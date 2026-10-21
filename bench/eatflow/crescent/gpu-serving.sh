#!/usr/bin/env bash
# gpu-serving.sh stop|start|check [CONTAINER]: free or restore the two B70 cards of one live
# SGLang pair on crescent for H53 (EXPERIMENTS.md "H53 pre-registration"). Default container
# qwen38-b-hr (ZE_AFFINITY_MASK=2,3: cards 0000:7c:00.0 and 0000:96:00.0, port 30001).
#   stop   save `docker inspect` to ~/k3dgpu-bench/restore/, refuse while an aiperf process
#          runs in aiperf010, then `docker stop` (never rm) and record the time
#   start  `docker start`, then wait (at most 20 min) until /health is 200, /v1/models lists
#          the model and a 16-token chat request answers; record the time and the reply
#   check  the same three reads, no change
# Writes ~/k3dgpu-bench/gpu-state.txt and appends to ~/k3dgpu-bench/restore/serving-events.log.
set -uo pipefail
OP=${1:?usage: gpu-serving.sh stop|start|check [CONTAINER]}
C=${2:-qwen38-b-hr}
B=${HOME}/k3dgpu-bench
D=${B}/restore
mkdir -p "${D}"
# The serving port: the container's PORT variable (the qwen38 entry script reads it), else a
# --port in its arguments; no fallback, so a check never reads another pair's server.
port=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${C}" | sed -n 's/^PORT=//p' | head -1)
[ -n "${port}" ] || port=$(docker inspect --format '{{join .Args " "}}' "${C}" | grep -oE -- '--port[= ][0-9]+' | grep -oE '[0-9]+' | head -1)
[[ "${port}" =~ ^[0-9]+$ ]] || { echo "gpu-serving.sh: no serving port found for ${C}"; exit 2; }
mask=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${C}" | sed -n 's/^ZE_AFFINITY_MASK=//p' | head -1)
ev() { echo "$(date -u +%FT%TZ) ${C} $*" | tee -a "${D}/serving-events.log"; }
state() { echo "$(date -u +%FT%TZ) $*" > "${B}/gpu-state.txt.$$" && mv -f "${B}/gpu-state.txt.$$" "${B}/gpu-state.txt"; }   # whole or not at all
check() {
    local h m r
    h=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${port}/health")
    m=$(curl -s --max-time 10 "http://127.0.0.1:${port}/v1/models" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null)
    [ "${h}" = 200 ] && [ -n "${m}" ] || return 1
    r=$(curl -s --max-time 120 "http://127.0.0.1:${port}/v1/chat/completions" -H 'Content-Type: application/json' \
        -d "{\"model\":\"${m}\",\"messages\":[{\"role\":\"user\",\"content\":\"Say OK\"}],\"max_tokens\":16,\"temperature\":0}" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["choices"][0]["message"]["content"][:80])' 2>/dev/null)
    [ -n "${r}" ] || return 1
    echo "health 200, model ${m}, reply: ${r//$'\n'/ }"
}
case ${OP} in
    stop)
        docker inspect "${C}" > "${D}/${C}.inspect.json" || exit 1
        a=$(docker top aiperf010 -eo pid,args 2>/dev/null | awk 'NR>1 && $0 !~ /sleep infinity/' | wc -l)
        [ "${a}" -eq 0 ] || { ev "NOT stopped: ${a} aiperf process(es) running"; exit 4; }
        out=$(check) && ev "before stop: ${out}"
        docker stop "${C}" > /dev/null || exit 1
        ev "stopped (ZE_AFFINITY_MASK=${mask}, port ${port})"
        state "B70s: ${C} STOPPED for H53 (cards of ZE_AFFINITY_MASK=${mask} in use by H53); others serving" ;;
    start)
        docker start "${C}" > /dev/null || exit 1
        ev "started"
        end=$(( $(date +%s) + 1200 ))
        until out=$(check); do
            [ "$(date +%s)" -lt "${end}" ] || { ev "NOT answering 20 min after start"; state "B70s: ${C} started but NOT answering"; exit 5; }
            sleep 15
        done
        ev "answering: ${out}"
        state "B70s: serving restored (${C} answering); not used by k3dgpu-bench" ;;
    check)
        if out=$(check); then echo "${C}: ${out}"; else echo "${C}: not answering"; exit 1; fi ;;
    *) echo "usage: gpu-serving.sh stop|start|check [CONTAINER]"; exit 2 ;;
esac
