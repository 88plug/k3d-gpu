#!/usr/bin/env bash
# Console slices S6 and S7: the MCP tools hardware and plan_launch equal the CLI
# --json byte for byte on every fixture host, and the MCP apply tool writes
# nothing without a plan hash the human confirmed in the client. The launcher
# runs on the fixture sysroots with docker, k3d and kubectl stubbed; no cluster,
# no network. See tests/console/test_console_mcp.py.
#
# Usage: tests/console_mcp_test.sh [-v]

set -uo pipefail

TESTS=$(cd "$(dirname "$0")" && pwd)
if python3 "${TESTS}/console/test_console_mcp.py" "$@"; then
    echo "ok   console MCP equality (S6) and write gate (S7)"
else
    echo "FAIL console MCP equality (S6) and write gate (S7)"
    exit 1
fi
