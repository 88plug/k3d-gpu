#!/usr/bin/env bash
# cotenants.sh LABEL: one read-only record of the desktop's GPU co-tenants and host state.
set -u
echo "== ${1:-} $(date -u +%FT%TZ)"
echo "loadavg: $(cat /proc/loadavg)"
echo "pkg_temp_c: $(( $(cat "$(grep -lx x86_pkg_temp /sys/class/thermal/thermal_zone*/type | head -1 | xargs dirname)/temp") / 1000 ))"
echo "memavailable_kb: $(awk '/MemAvailable/{print $2}' /proc/meminfo)"
echo "power_profile: $(powerprofilesctl get 2>/dev/null || echo unknown)"
for c in qwen35-9b gpustack-worker; do
    echo "${c}: $(docker inspect -f 'running={{.State.Running}} restarts={{.RestartCount}} started={{.State.StartedAt}} image={{.Config.Image}}' "${c}" 2>&1)"
done
echo "qwen35-9b vllm counters:"
curl -s --max-time 5 http://127.0.0.1:18080/metrics 2>/dev/null \
    | grep -E '^vllm:(request_success_total|generation_tokens_total|prompt_tokens_total)' || echo "  (metrics not read)"
echo "nvidia-smi:"
nvidia-smi --query-gpu=index,name,memory.used,utilization.gpu,power.draw --format=csv,noheader 2>&1
echo "k3d clusters: $(k3d cluster list -o json 2>/dev/null | python3 -c 'import json,sys; print([c["name"] for c in json.load(sys.stdin)])' 2>&1)"
