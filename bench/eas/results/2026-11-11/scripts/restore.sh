#!/bin/bash
# restore.sh <dir>: start what takeover-stop.sh stopped (qwen38-a-hr, qwen38-b-hr,
# aiperf010, in that order), then wait (30 min ceiling each) until each server answers
# its baseline request: content "Paris", finish_reason stop. Exit 0 only when all do.
set -u
T=$1
cd "$T" || exit 1
umask 077
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$T/restore-log.txt"; }
vram() { curl -s -m 10 localhost:9101/metrics | awk '/^xe_vram_used_bytes/ { match($0, /card="[^"]+"/); printf "%s %.2f;", substr($0, RSTART + 6, RLENGTH - 7), $2 / 2^30 }'; }
REQ='{"model":"Qwen3.8-27B","messages":[{"role":"user","content":"What is the capital of France? Answer in one word."}],"max_tokens":48,"temperature":0,"chat_template_kwargs":{"enable_thinking":false}}'
rc=0
for n in qwen38-a-hr qwen38-b-hr aiperf010; do
    grep -qx "$n" stopped.txt || continue
    log "$n start rc=$(docker start "$n" > /dev/null 2>&1; echo $?)"
done
for n in qwen38-a-hr qwen38-b-hr aiperf010; do
    grep -qx "$n" stopped.txt || continue
    case "$n" in qwen38-a-hr) p=30000 ;; qwen38-b-hr) p=30001 ;; *) p=- ;; esac
    if [ "$p" = - ]; then
        sleep 5
        log "$n $(docker inspect -f 'status={{.State.Status}} started={{.State.StartedAt}} restart={{.HostConfig.RestartPolicy.Name}}' "$n")"
        [ "$(docker inspect -f '{{.State.Status}}' "$n")" = running ] || rc=1
        continue
    fi
    t0=$(date +%s)
    while :; do
        el=$(( $(date +%s) - t0 ))
        if [ "$(docker inspect -f '{{.State.Status}}' "$n")" != running ]; then log "FAIL $n is not running after ${el}s"; rc=1; break; fi
        m=$(curl -s -m 120 -o "restore-$p.json" -w "http=%{http_code} total_s=%{time_total}" "localhost:$p/v1/chat/completions" -H 'Content-Type: application/json' -d "${REQ}")
        c=$(jq -r '.choices[0].message.content // empty' "restore-$p.json" 2>/dev/null)
        f=$(jq -r '.choices[0].finish_reason // empty' "restore-$p.json" 2>/dev/null)
        if [ "$c" = Paris ] && [ "$f" = stop ]; then
            log "OK $n port=$p ${m} $(jq -c '{model,content:.choices[0].message.content,finish:.choices[0].finish_reason,usage}' "restore-$p.json") after ${el}s"
            break
        fi
        if [ "${el}" -ge 1800 ]; then log "FAIL $n port=$p no baseline answer in ${el}s (${m} content=${c:-none})"; rc=1; break; fi
        sleep 15
    done
done
sleep 5
log "vram after restore: $(vram)"
log "restore rc=${rc}"
exit "${rc}"
