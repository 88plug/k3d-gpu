#!/usr/bin/env bash
# teardown.sh: H62's teardown (EXPERIMENTS.md "H62 pre-registration"), after the last hold or any
# abort. Both guests are powered off and removed (vm.sh down), and their disks, the cloud image and
# the QEMU image are deleted (vm.sh purge); ~/k3dgpu-bench/results/H62 stays. Serving pairs that
# campaign.sh stopped are started again (gpu-serving.sh start) and then checked through Dynamo
# :8100 as crescent amendment E2 item 7 does: a 16-token chat answers with content and that pair's
# SGLang num_requests_total rises, polled for up to 180 s. The result is recorded; it gates nothing.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
B=${HOME}/k3dgpu-bench
R=${B}/results/H62
export H62_STATE=${H62_STATE:-${B}/h62}
mkdir -p "${R}"
for vm in a b; do bash "${HERE}/vm.sh" down "${vm}"; done
bash "${HERE}/vm.sh" purge
if [ -s "${R}/serving-stopped.txt" ]; then
    while read -r c; do
        bash "${REPO}/bench/eatflow/crescent/gpu-serving.sh" start "${c}" >> "${R}/serving.log" 2>&1
    done < "${R}/serving-stopped.txt"
    mv "${R}/serving-stopped.txt" "${R}/serving-restored.txt"
    n() { curl -s -m 8 "http://127.0.0.1:$1/metrics" | awk '/^sglang:num_requests_total/{s+=$2} END{print s+0}'; }
    for port in 30000 30001; do
        before=$(n "${port}") end=$(( SECONDS + 180 )) ok=0 c=""
        until [ "${SECONDS}" -ge "${end}" ]; do
            c=$(curl -s -m 60 http://127.0.0.1:8100/v1/chat/completions -H 'Content-Type: application/json' \
                -d '{"model":"Qwen3.8-27B","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16,"temperature":0,"chat_template_kwargs":{"enable_thinking":false}}' \
                | python3 -c 'import json,sys;print(json.load(sys.stdin)["choices"][0]["message"]["content"].strip())' 2>/dev/null)
            if [ -n "${c}" ] && [ "$(n "${port}")" -gt "${before}" ]; then ok=1; break; fi
            sleep 5
        done
        echo "$(date -u +%FT%TZ) :${port} pass=${ok} before=${before} after=$(n "${port}") content=${c:-none}" \
            | tee -a "${R}/frontdoor-post.txt"
    done
fi
