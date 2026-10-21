#!/usr/bin/env bash
# campaign.sh: H54b's holds then H54c's hold, back to back inside one take of the shared lock
# (amendment G1). When CARDS are not free and STOP_BHR=1 (the operator's authorisation), stops
# qwen38-b-hr (cards 2 and 3) after saving its `docker inspect`, and starts it again at the end,
# also on failure, checking that it answers a chat request. Each stop and restart is appended to
# ~/k3dgpu-bench/gpu-state.txt. Usage: CARDS=2,3 [STOP_BHR=1] campaign.sh "HOLD:ARMS" ... where
# HOLD:ARMS is e.g. "1:X1q X1q-mtp" for H54b, "c1:2" for H54c hold 1 on card 2, or
# "f:REPO:REVISION:GB" for a download between holds.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
X=${HOME}/k3dgpu-gemma
STATE=${HOME}/k3dgpu-bench/gpu-state.txt
CAP=${CAP:-2400}
log() { echo "== campaign $* $(date -u +%FT%TZ)" | tee -a "${X}/runs/campaign.log"; }
chat_ok() {
    curl -sf -m 120 http://127.0.0.1:30001/v1/chat/completions -H 'Content-Type: application/json' \
        -d '{"model":"Qwen3.8-27B","max_tokens":16,"temperature":0,"messages":[{"role":"user","content":"Say hi."}],"chat_template_kwargs":{"enable_thinking":false}}'
}
stopped=""
restore() {
    [ -n "${stopped}" ] || return 0
    docker start qwen38-b-hr >/dev/null
    local t0 r
    t0=$(date +%s)
    while [ $(( $(date +%s) - t0 )) -lt 1800 ]; do
        if r=$(chat_ok); then
            echo "${r}" > "${X}/restore/qwen38-b-hr.chat-after.json"
            echo "$(date -u +%FT%TZ) gemma: qwen38-b-hr restored at $(date -u +%FT%TZ) (chat answered after $(( $(date +%s) - t0 )) s); cards 2,3 back to it" >> "${STATE}"
            log "qwen38-b-hr restored, chat ok"
            stopped=""
            return 0
        fi
        sleep 15
    done
    echo "$(date -u +%FT%TZ) gemma: qwen38-b-hr started at $(date -u +%FT%TZ) but no chat answer in 1800 s" >> "${STATE}"
    log "qwen38-b-hr started, NO chat answer in 1800 s"
    return 1
}
trap 'restore' EXIT
trap 'exit 143' TERM HUP INT

mkdir -p "${X}/restore" "${X}/runs"
log "waiting for the lock"
exec 9> "${HOME}/k3dgpu-bench.lock"
flock -w 21600 9 || { log "lock not taken in 21600 s"; exit 4; }
log "lock taken"
export OUT=${X}/runs   # lib.sh's log directory
. "${HERE}/lib.sh"
need_stop=""
for c in ${CARDS//,/ }; do card_free "${c}" || need_stop=1; done
if [ -n "${need_stop}" ]; then
    [ "${STOP_BHR:-}" = 1 ] && [ "${CARDS}" = 2,3 ] || { log "cards ${CARDS} busy and no stop authorised: no holds"; exit 6; }
    ts=$(date -u +%Y%m%dT%H%M%SZ)
    docker inspect qwen38-b-hr > "${X}/restore/qwen38-b-hr.inspect-${ts}.json" || exit 1
    chat_ok > "${X}/restore/qwen38-b-hr.chat-before.json" || log "qwen38-b-hr did not answer before the stop"
    docker stop -t 120 qwen38-b-hr >/dev/null || { log "docker stop failed"; exit 1; }
    stopped=1
    echo "$(date -u +%FT%TZ) gemma: using cards 2,3 (renderD130,renderD131); qwen38-b-hr stopped at $(date -u +%FT%TZ) (inspect saved: ~/k3dgpu-gemma/restore/qwen38-b-hr.inspect-${ts}.json)" >> "${STATE}"
    log "qwen38-b-hr stopped"
    sleep 20
    for c in ${CARDS//,/ }; do card_free "${c}" || { log "card ${c} still busy after the stop"; exit 6; }; done
fi
for h in "$@"; do
    id=${h%%:*} arg=${h#*:}
    case "${id}" in
        f)  # f:REPO:REVISION:GB, a download between holds (fetch.sh, same lock)
            IFS=: read -r repo rev gb <<< "${arg}"
            H54_LOCKED=1 bash "${HERE}/fetch.sh" "${repo}" "${rev}" "${gb}"
            log "fetch ${repo} rc=$?"
            continue ;;
        c*) H54_LOCKED=1 bash "${HERE}/hold.sh" "H54c-hold${id#c}" "${CAP}" bash "${HERE}/h54c.sh" "${id#c}" "${arg}" ;;
        *)  # shellcheck disable=SC2086
            H54_LOCKED=1 CARDS=${CARDS} bash "${HERE}/hold.sh" "H54b-hold${id}" "${CAP}" bash "${HERE}/h54b.sh" "${id}" ${arg} ;;
    esac
    log "hold ${id} rc=$?"
done
