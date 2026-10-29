#!/usr/bin/env bash
kill "$(cat "/home/devcloud/k3dgpu-bench/smoke/H51/B1/mcp.pid")" 2>/dev/null
for _ in $(seq 50); do (exec 3<>"/dev/tcp/127.0.0.1/18600") 2>/dev/null || break; sleep 0.1; done
cd "/home/devcloud/k3dgpu-bench/eab/usecases/banking-assistant/src" && (MCP_HOST=127.0.0.1 MCP_PORT=18600 exec "/home/devcloud/k3dgpu-bench/mafvenv/bin/python" -m mcp_server.server >> "/home/devcloud/k3dgpu-bench/smoke/H51/B1/mcp.log" 2>&1 &
               echo $! > "/home/devcloud/k3dgpu-bench/smoke/H51/B1/mcp.pid")
for _ in $(seq 120); do (exec 3<>"/dev/tcp/127.0.0.1/18600") 2>/dev/null && exit 0; sleep 0.5; done
exit 1
