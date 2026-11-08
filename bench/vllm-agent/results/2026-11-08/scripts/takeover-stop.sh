#!/bin/bash
# takeover-stop.sh <dir>: snapshot qwen38-a-hr, qwen38-b-hr and aiperf010, answer each
# server's baseline request, then stop the running ones gracefully (docker stop -t 120)
# in the order of the 2026-11-07 takeover. Writes <dir>/stopped.txt (names stopped).
set -u
T=$1
mkdir -p "$T" && cd "$T" || exit 1
umask 077
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$T/stop-log.txt"; }
vram() { curl -s -m 10 localhost:9101/metrics | awk '/^xe_vram_used_bytes/ { match($0, /card="[^"]+"/); printf "%s %.2f;", substr($0, RSTART + 6, RLENGTH - 7), $2 / 2^30 }'; }
REQ='{"model":"Qwen3.8-27B","messages":[{"role":"user","content":"What is the capital of France? Answer in one word."}],"max_tokens":48,"temperature":0,"chat_template_kwargs":{"enable_thinking":false}}'
NAMES="aiperf010 qwen38-b-hr qwen38-a-hr"

date -u +%FT%TZ > snapshot-time.txt
docker ps -a --no-trunc > docker-ps-a.txt
for n in ${NAMES}; do docker inspect "$n" > "inspect-$n.json" 2>&1; done
curl -s -m 10 localhost:9101/metrics > xe-exporter-before.prom
log "vram before: $(vram)"
for n in qwen38-a-hr qwen38-b-hr; do
    docker logs --since 1h "$n" > "logs-1h-$n.txt" 2>&1
    log "$n POST lines in the last 10 min: $(docker logs --since 10m "$n" 2>&1 | grep -c 'POST /v1')"
done
for p in 30000 30001; do
    m=$(curl -s -m 90 -o "baseline-$p.json" -w "port=$p http=%{http_code} total_s=%{time_total}" "localhost:$p/v1/chat/completions" -H 'Content-Type: application/json' -d "${REQ}")
    log "baseline ${m} $(jq -c '{model,content:.choices[0].message.content,finish:.choices[0].finish_reason}' "baseline-$p.json" 2>/dev/null)"
done
: > stopped.txt
for n in ${NAMES}; do
    st=$(docker inspect -f '{{.State.Status}}' "$n" 2>/dev/null)
    if [ "${st}" != running ]; then log "$n not running (${st:-absent}), left as is"; continue; fi
    log "$n stop begin"
    t=$(date +%s)
    docker stop -t 120 "$n" > /dev/null
    log "$n stop took $(( $(date +%s) - t ))s: $(docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}} restart={{.HostConfig.RestartPolicy.Name}}' "$n")"
    echo "$n" >> stopped.txt
done
sleep 10
curl -s -m 10 localhost:9101/metrics > xe-exporter-after.prom
log "vram after: $(vram)"
