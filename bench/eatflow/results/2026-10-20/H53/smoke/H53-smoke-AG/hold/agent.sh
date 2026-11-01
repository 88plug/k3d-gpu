#!/usr/bin/env bash
# (Re)start the banking agent and wait for its DevUI.
[ -f '/home/devcloud/k3dgpu-bench/smoke/H53/H53-smoke-AG/hold/agent.pid' ] && kill "$(cat '/home/devcloud/k3dgpu-bench/smoke/H53/H53-smoke-AG/hold/agent.pid')" 2>/dev/null
for _ in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8090) 2>/dev/null || break; sleep 0.1; done
cd '/home/devcloud/k3dgpu-bench/eab/usecases/banking-assistant/src' && (OPENAI_BASE_URL=http://127.0.0.1:18620/v1 OPENAI_API_KEY=not-needed MODEL_NAME='Qwen/Qwen3.8-27B' \
    MCP_SERVER_URL=http://127.0.0.1:18600/mcp ENABLE_MEMORY=false AUTO_OPEN_BROWSER=false \
    DEVUI_HOST=127.0.0.1 exec '/home/devcloud/k3dgpu-bench/mafvenv/bin/python' main.py >> '/home/devcloud/k3dgpu-bench/smoke/H53/H53-smoke-AG/hold/agent.log' 2>&1 &
    echo $! > '/home/devcloud/k3dgpu-bench/smoke/H53/H53-smoke-AG/hold/agent.pid')
for _ in $(seq 45); do
    curl -fsS -o /dev/null --max-time 5 -H "Authorization: Bearer ${DEVUI_AUTH_TOKEN}" http://127.0.0.1:8090/v1/entities && exit 0
    sleep 2
done
exit 1
