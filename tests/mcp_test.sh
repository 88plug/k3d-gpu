#!/usr/bin/env bash
# k3d-gpu-mcp tests. Each case drives the MCP server over stdio with a scripted
# JSON-RPC session; the k3d-gpu launcher and kubectl are stubs answering from
# fixture files. No cluster, no network.
#
# Usage: tests/mcp_test.sh

set -uo pipefail

MCP="$(cd "$(dirname "$0")/.." && pwd)/scripts/k3d-gpu-mcp"
WORK=$(mktemp -d)
# A measured autoquant pick on this dev box (EXPERIMENTS.md H27) must not leak into goldens.
export K3D_GPU_AUTOQUANT_CACHE="${WORK}/no-autoquant.json"
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

# ---- stubs --------------------------------------------------------------------

STUB_BIN="${WORK}/bin"
mkdir -p "${STUB_BIN}"

# kubectl stub: logs its argv to ${STUB_KUBE}/calls, drops the --context and
# --request-timeout flags, and prints ${STUB_KUBE}/<args with non-alphanumerics
# as _>; a missing fixture is a NotFound error.
cat > "${STUB_BIN}/kubectl" <<'EOF'
#!/usr/bin/env bash
echo "$*" >> "${STUB_KUBE}/calls"
args=()
while [ $# -gt 0 ]; do
    case "$1" in
        --context) shift 2 ;;
        --request-timeout=*) shift ;;
        *) args+=("$1"); shift ;;
    esac
done
key=$(printf '%s' "${args[*]}" | tr -c 'A-Za-z0-9' '_')
if [ -f "${STUB_KUBE}/${key}" ]; then cat "${STUB_KUBE}/${key}"; exit 0; fi
echo "Error from server (NotFound): ${args[*]}" >&2
exit 1
EOF
# Launcher stub: `report --json` prints ${STUB_KUBE}/report.json (exit 1 with the
# usage text when absent, like a launcher without the subcommand); `detect`
# prints ${STUB_KUBE}/detect; `version` reports 9.8.7. With STUB_SLEEP set, every
# other subcommand sleeps that many seconds instead.
cat > "${STUB_BIN}/k3d-gpu" <<'EOF'
#!/usr/bin/env bash
[ "$1" = version ] || [ -z "${STUB_SLEEP:-}" ] || exec sleep "${STUB_SLEEP}"
case "$1" in
    report) [ -f "${STUB_KUBE}/report.json" ] && exec cat "${STUB_KUBE}/report.json"
            echo "Usage: k3d-gpu <command>"; exit 1 ;;
    detect) cat "${STUB_KUBE}/detect" 2>/dev/null; exit 0 ;;
    version) echo "k3d-gpu 9.8.7" ;;
    *) exit 1 ;;
esac
EOF
chmod +x "${STUB_BIN}/kubectl" "${STUB_BIN}/k3d-gpu"

# fixtures <name>: a fresh fixture dir; prints its path.
fixtures() { local d="${WORK}/$1"; mkdir -p "${d}"; : > "${d}/calls"; echo "${d}"; }

# fixture <dir> <kubectl args...>: stdin becomes the stub's answer for those args.
fixture() {
    local d=$1; shift
    cat > "${d}/$(fixture_key "$@")"
}
fixture_key() { printf '%s' "$*" | tr -c 'A-Za-z0-9' '_'; }

# context <dir> [name]: the kubeconfig has context k3d-<name> (default gpu).
context() { printf 'other\nk3d-%s\n' "${2:-gpu}" | fixture "$1" config get-contexts -o name; }

# ---- JSON-RPC session helpers -------------------------------------------------

MODERN_META='"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{},"io.modelcontextprotocol/clientInfo":{"name":"mcp_test","version":"1"}}'

# call <id> <tool> [arguments-json]: a modern tools/call request line.
call() {
    local args='{}'
    [ -z "${3:-}" ] || args=$3
    printf '{"jsonrpc":"2.0","id":%s,"method":"tools/call","params":{%s,"name":"%s","arguments":%s}}\n' \
        "$1" "${MODERN_META}" "$2" "${args}"
}

# session <fixture-dir> <stdin> [env assignments...]: run the server to EOF; prints stdout.
session() {
    local d=$1 input=$2
    shift 2
    env PATH="${STUB_BIN}:${PATH}" K3D_GPU_BIN="${STUB_BIN}/k3d-gpu" STUB_KUBE="${d}" "$@" \
        python3 "${MCP}" <<< "${input}" 2>"${d}/stderr"
}

# check <name> <session stdout> <python expression>: the expression sees `r`, the
# responses keyed by id (a parse-error reply under None), `lines`, the raw output
# lines, and `sc(id)`, the structuredContent of a tools/call reply.
CHECK_PY='
import json, sys
expr = sys.argv[1]
lines = [l for l in sys.stdin.read().split("\n") if l]
r = {}
for l in lines:
    m = json.loads(l)
    r[m.get("id")] = m
sc = lambda i: r[i]["result"]["structuredContent"]
if not eval(expr):
    print("expression false; responses:")
    for l in lines:
        print("  | " + l)
    sys.exit(1)
'
check() {
    local name=$1 out=$2 expr=$3 why
    if why=$(python3 -c "${CHECK_PY}" "${expr}" <<< "${out}" 2>&1); then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name}: ${expr}"; sed 's/^/  /' <<< "${why}"
    fi
}

# ---- protocol: legacy (initialize) era ----------------------------------------

d=$(fixtures legacy)
out=$(session "${d}" '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"ping"}')
check "legacy initialize echoes a supported version" "${out}" \
    'r[1]["result"]["protocolVersion"] == "2025-11-25" and r[1]["result"]["serverInfo"]["name"] == "k3d-gpu-mcp" and r[1]["result"]["capabilities"] == {"tools": {"listChanged": False}}'
check "serverInfo carries the launcher's version" "${out}" 'r[1]["result"]["serverInfo"]["version"] == "9.8.7"'
check "notifications get no reply" "${out}" 'len(lines) == 3'
check "legacy tools/list after initialize needs no _meta" "${out}" \
    '[t["name"] for t in r[2]["result"]["tools"]] == ["accelerators", "recommend_placement", "agent_endpoint", "cluster_health", "hardware", "plan_launch", "apply"]'
check "every tool has an object inputSchema and every tool but apply is read-only" "${out}" \
    'all(t["inputSchema"]["type"] == "object" and t["annotations"]["readOnlyHint"] == (t["name"] != "apply") for t in r[2]["result"]["tools"])'
check "legacy ping answers an empty result" "${out}" 'r[3]["result"]["resultType"] == "complete"'

out=$(session "${d}" '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}
{"jsonrpc":"2.0","id":2,"method":"initialize","params":{"protocolVersion":"1999-01-01","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}')
check "initialize keeps an older supported version" "${out}" 'r[1]["result"]["protocolVersion"] == "2025-06-18"'
check "initialize with an unknown version gets the latest legacy one" "${out}" 'r[2]["result"]["protocolVersion"] == "2025-11-25"'

# ---- protocol: modern (2026-07-28) era -----------------------------------------

out=$(session "${d}" "{\"jsonrpc\":\"2.0\",\"id\":\"d\",\"method\":\"server/discover\",\"params\":{${MODERN_META}}}
{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\",\"params\":{${MODERN_META}}}")
check "server/discover advertises 2026-07-28 and the legacy versions" "${out}" \
    'r["d"]["result"]["supportedVersions"][0] == "2026-07-28" and "2025-11-25" in r["d"]["result"]["supportedVersions"]'
check "server/discover result is complete, cacheable, self-identified" "${out}" \
    'r["d"]["result"]["resultType"] == "complete" and r["d"]["result"]["ttlMs"] >= 0 and r["d"]["result"]["cacheScope"] == "public" and r["d"]["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["version"]'
check "modern tools/list carries resultType, ttlMs and cacheScope" "${out}" \
    'r[2]["result"]["resultType"] == "complete" and "ttlMs" in r[2]["result"] and r[2]["result"]["cacheScope"] in ("public", "private") and len(r[2]["result"]["tools"]) == 7'

out=$(session "${d}" '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"1900-01-01","io.modelcontextprotocol/clientCapabilities":{}}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28"}}}
{"jsonrpc":"2.0","id":3,"method":"tools/list"}
{"jsonrpc":"2.0","id":4,"method":"resources/list","params":{}}
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}},"name":"rm_rf"}}
not json
[{"jsonrpc":"2.0","id":7,"method":"tools/list"}]
{"jsonrpc":"2.0","id":true,"method":"tools/list"}
{"jsonrpc":"2.0","result":{},"id":"from-client"}')
check "unsupported version: -32022 with supported and requested" "${out}" \
    'r[1]["error"]["code"] == -32022 and r[1]["error"]["data"]["requested"] == "1900-01-01" and "2026-07-28" in r[1]["error"]["data"]["supported"]'
check "missing clientCapabilities: -32602" "${out}" 'r[2]["error"]["code"] == -32602'
check "no _meta and no initialize: -32602 naming the versions" "${out}" \
    'r[3]["error"]["code"] == -32602 and "2026-07-28" in r[3]["error"]["message"]'
check "unknown method: -32601" "${out}" 'r[4]["error"]["code"] == -32601'
check "unknown tool: -32602" "${out}" 'r[5]["error"]["code"] == -32602 and "rm_rf" in r[5]["error"]["message"]'
check "bad JSON, batch array and boolean id are rejected, client responses ignored" "${out}" \
    '[json.loads(l)["error"]["code"] for l in lines[5:]] == [-32700, -32600, -32600] and all(json.loads(l)["id"] is None for l in lines[5:]) and len(lines) == 8'

out=$(session "${d}" '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2025-11-25","io.modelcontextprotocol/clientCapabilities":{}}}}')
check "per-request _meta with a legacy version and no initialize: -32022" "${out}" \
    'r[1]["error"]["code"] == -32022 and r[1]["error"]["data"]["requested"] == "2025-11-25" and "initialize" in r[1]["error"]["message"]'
out=$(session "${d}" '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2025-11-25","io.modelcontextprotocol/clientCapabilities":{}}}}')
check "legacy session may still carry its version in _meta" "${out}" 'len(r[2]["result"]["tools"]) == 7'

# ---- accelerators ------------------------------------------------------------------

d=$(fixtures report)
cat > "${d}/report.json" <<'EOF'
{"schema":"k3d-gpu.report/v1","k3d_gpu_version":"0.1.7","detected":[{"vendor":"intel","resources":["gpu.intel.com/xe"]}],"cluster":{"name":"gpu","context":"k3d-gpu","present":true,"reason":null,"nodes":[{"name":"k3d-gpu-server-0","allocatable":{"cpu":"32","gpu.intel.com/xe":"1"},"labels":{}}]}}
EOF
out=$(session "${d}" "$(call 1 accelerators)")
check "accelerators returns the report JSON as structured content" "${out}" \
    'sc(1)["schema"] == "k3d-gpu.report/v1" and sc(1)["cluster"]["nodes"][0]["allocatable"]["gpu.intel.com/xe"] == "1"'
check "accelerators text content is the same JSON" "${out}" \
    'json.loads(r[1]["result"]["content"][0]["text"]) == sc(1) and r[1]["result"]["isError"] is False'

d=$(fixtures fallback)
context "${d}"
printf 'intel gpu.intel.com/xe\nnpu npu.intel.com/accel\nhybrid k3d-gpu.io/hybrid-cpu=true\n' > "${d}/detect"
fixture "${d}" get nodes -o json <<'EOF'
{"items":[{"metadata":{"name":"k3d-gpu-server-0","labels":{"k3d-gpu.io/hybrid-cpu":"true","kubernetes.io/os":"linux"}},"status":{"allocatable":{"cpu":"32","gpu.intel.com/xe":"1","npu.intel.com/accel":"1"}}}]}
EOF
out=$(session "${d}" "$(call 1 accelerators)")
check "launcher without report: detect + kubectl nodes" "${out}" \
    'sc(1)["schema"] == "k3d-gpu.mcp-fallback/v1" and sc(1)["detected"][1] == {"vendor": "npu", "resources": ["npu.intel.com/accel"]} and sc(1)["cluster"]["present"]'
check "fallback keeps only accelerator labels" "${out}" \
    'sc(1)["cluster"]["nodes"][0]["labels"] == {"k3d-gpu.io/hybrid-cpu": "true"}'
check "kubectl always targets context k3d-<cluster>" "${out}" \
    'all("--context k3d-gpu" in c for c in open("'"${d}"'/calls").read().splitlines() if not c.startswith("config"))'

d=$(fixtures report-bad-json)
echo '{"schema": truncated' > "${d}/report.json"
printf 'nvidia nvidia.com/gpu\n' > "${d}/detect"
out=$(session "${d}" "$(call 1 accelerators)")
check "report --json exiting 0 with invalid JSON falls back to detect" "${out}" \
    'sc(1)["schema"] == "k3d-gpu.mcp-fallback/v1" and sc(1)["detected"] == [{"vendor": "nvidia", "resources": ["nvidia.com/gpu"]}]'

echo '["not", "an", "object"]' > "${d}/report.json"
out=$(session "${d}" "$(call 1 accelerators)")
check "report --json printing valid JSON that is not an object falls back to detect" "${out}" \
    'sc(1)["schema"] == "k3d-gpu.mcp-fallback/v1" and sc(1)["detected"] == [{"vendor": "nvidia", "resources": ["nvidia.com/gpu"]}]'

d=$(fixtures timeout)
out=$(session "${d}" "$(call 1 accelerators)" STUB_SLEEP=5 K3D_GPU_MCP_TIMEOUT=0.5)
check "a launcher call past K3D_GPU_MCP_TIMEOUT is a tool error naming the timeout" "${out}" \
    'r[1]["result"]["isError"] is True and "timed out after 0.5s" in r[1]["result"]["content"][0]["text"]'

for tool in accelerators agent_endpoint cluster_health; do
    out=$(session "${d}" "$(call 1 "${tool}" '{"bogus":1}')")
    check "${tool}: unknown argument is a tool error" "${out}" \
        'r[1]["result"]["isError"] is True and "bogus" in r[1]["result"]["content"][0]["text"]'
done

# ---- recommend_placement ----------------------------------------------------------

# nodes <dir> <allocatable-json> <labels-json>: a report with one cluster node.
nodes() {
    printf '{"schema":"k3d-gpu.report/v1","detected":[],"cluster":{"present":true,"nodes":[{"name":"n0","allocatable":%s,"labels":%s}]}}\n' "$2" "$3" > "$1/report.json"
}

d=$(fixtures place-gaudi)
nodes "${d}" '{"cpu":"64","habana.ai/gaudi":"8","gpu.intel.com/xe":"1"}' '{"k3d-gpu.io/hybrid-cpu":"true"}'
out=$(session "${d}" "$(call 1 recommend_placement '{"workload":"llm"}')
$(call 2 recommend_placement '{"workload":"llm","objective":"efficiency"}')
$(call 3 recommend_placement '{"workload":"media","objective":"throughput"}')")
check "llm/latency picks Gaudi with a habana.ai/gaudi limit on P-cores" "${out}" \
    'sc(1)["engine"] == "gaudi" and sc(1)["podSpecFragment"] == {"metadata": {"labels": {"k3d-gpu.io/cores": "performance"}}, "spec": {"containers": [{"name": "main", "resources": {"requests": {"cpu": "1"}, "limits": {"habana.ai/gaudi": "1"}}}]}} and not any("xmx" in n for n in sc(1)["notes"])'
check "objective defaults to latency" "${out}" 'sc(1)["objective"] == "latency"'
check "llm/efficiency prefers the Intel GPU on E-cores" "${out}" \
    'sc(2)["engine"] == "intel-gpu-xe" and sc(2)["podSpecFragment"]["metadata"]["labels"]["k3d-gpu.io/cores"] == "efficient" and sc(2)["alternatives"] == ["gaudi", "cpu"]'
check "media never lands on Gaudi" "${out}" 'sc(3)["engine"] == "intel-gpu-xe" and "gaudi" not in sc(3)["alternatives"]'

d=$(fixtures place-laptop)
nodes "${d}" '{"cpu":"16","gpu.intel.com/i915":"1","npu.intel.com/accel":"1"}' '{"k3d-gpu.io/hybrid-cpu":"true"}'
out=$(session "${d}" "$(call 1 recommend_placement '{"workload":"embeddings","objective":"efficiency"}')
$(call 2 recommend_placement '{"workload":"llm","objective":"latency"}')
$(call 3 recommend_placement '{"workload":"batch","objective":"throughput"}')")
check "embeddings/efficiency picks the NPU" "${out}" \
    'sc(1)["engine"] == "npu" and sc(1)["podSpecFragment"]["spec"]["containers"][0]["resources"]["limits"] == {"npu.intel.com/accel": "1"}'
check "llm/latency: no NPU, and a GPU without XMX loses to the P-cores (the agent's auto rule)" "${out}" \
    'sc(2)["engine"] == "cpu" and sc(2)["alternatives"] == [] and sc(2)["podSpecFragment"]["metadata"]["labels"]["k3d-gpu.io/cores"] == "performance" and any("gpu.intel.com/xmx=true" in n for n in sc(2)["notes"])'
check "batch/throughput: CPU only, E-cores, a CPU request and no device limit" "${out}" \
    'sc(3)["engine"] == "cpu" and sc(3)["podSpecFragment"]["spec"]["containers"][0]["resources"] == {"requests": {"cpu": "1"}} and sc(3)["podSpecFragment"]["metadata"]["labels"]["k3d-gpu.io/cores"] == "efficient"'

d=$(fixtures place-xmx)
nodes "${d}" '{"cpu":"16","gpu.intel.com/xe":"1"}' '{"gpu.intel.com/xmx":"true"}'
out=$(session "${d}" "$(call 1 recommend_placement '{"workload":"llm","objective":"throughput"}')
$(call 2 recommend_placement '{"workload":"embeddings"}')")
check "llm on an XMX GPU: the GPU, pinned to an xmx=true node, no cores label off hybrid" "${out}" \
    'sc(1)["engine"] == "intel-gpu-xe" and sc(1)["podSpecFragment"] == {"spec": {"containers": [{"name": "main", "resources": {"limits": {"gpu.intel.com/xe": "1"}}}], "nodeSelector": {"gpu.intel.com/xmx": "true"}}}'
check "the XMX gate is for LLMs only: embeddings take the GPU with no xmx selector" "${out}" \
    'sc(2)["engine"] == "intel-gpu-xe" and "nodeSelector" not in sc(2)["podSpecFragment"]["spec"]'

d=$(fixtures place-xmx-partial)
nodes "${d}" '{"cpu":"16","gpu.intel.com/i915":"2"}' '{"gpu.intel.com/xmx":"true","k3d-gpu.io/xmx.i915":"false"}'
out=$(session "${d}" "$(call 1 recommend_placement '{"workload":"llm","objective":"throughput"}')")
check "llm: an i915 that also holds a GPU without XMX is skipped (the agent's auto rule)" "${out}" \
    'sc(1)["engine"] == "cpu" and any("xmx.<driver>=false" in n for n in sc(1)["notes"])'

d=$(fixtures place-xeon)
nodes "${d}" '{"cpu":"96","gpu.intel.com/xe":"0"}' '{"feature.node.kubernetes.io/cpu-cpuid.AMXTILE":"true"}'
out=$(session "${d}" "$(call 1 recommend_placement '{"workload":"llm","objective":"throughput"}')")
check "Xeon with AMX and a zero-count GPU: CPU on the AMX node, no cores label" "${out}" \
    'sc(1)["engine"] == "cpu" and sc(1)["podSpecFragment"] == {"spec": {"containers": [{"name": "main"}], "nodeSelector": {"feature.node.kubernetes.io/cpu-cpuid.AMXTILE": "true"}}}'

d=$(fixtures place-multinode)
printf '{"detected":[],"cluster":{"present":true,"nodes":[{"name":"n0","allocatable":{"cpu":"8"},"labels":{}},{"name":"n1","allocatable":{"cpu":"8","nvidia.com/gpu":"2"},"labels":{"k3d-gpu.io/hybrid-cpu":"true"}}]}}\n' > "${d}/report.json"
out=$(session "${d}" "$(call 1 recommend_placement '{"workload":"llm"}')")
check "multi-node: devices and labels on any node count (k3d labels every node alike)" "${out}" \
    'sc(1)["engine"] == "nvidia" and sc(1)["podSpecFragment"]["metadata"]["labels"]["k3d-gpu.io/cores"] == "performance"'

d=$(fixtures place-plugin-down)
printf '{"detected":[{"vendor":"intel","resources":["gpu.intel.com/xe"]}],"cluster":{"present":true,"nodes":[{"name":"n0","allocatable":{"cpu":"8"},"labels":{}}]}}\n' > "${d}/report.json"
out=$(session "${d}" "$(call 1 recommend_placement '{"workload":"media"}')")
check "host GPU the cluster does not advertise: CPU now, with a plugin note" "${out}" \
    'sc(1)["engine"] == "cpu" and any("gpu.intel.com/xe" in n and "cluster_health" in n for n in sc(1)["notes"])'

d=$(fixtures place-host)
printf '{"detected":[{"vendor":"nvidia","resources":["nvidia.com/gpu"]},{"vendor":"dsa","resources":["dsa.intel.com/*"]}],"cluster":{"present":false,"nodes":[]}}\n' > "${d}/report.json"
out=$(session "${d}" "$(call 1 recommend_placement '{"workload":"llm"}')
$(call 2 recommend_placement '{"workload":"training"}')
$(call 3 recommend_placement '{"workload":"llm","objective":"latency","gpus":4}')")
check "no cluster: placement from host detection, with a note" "${out}" \
    'sc(1)["engine"] == "nvidia" and sc(1)["inventorySource"] == "host" and any("k3d-gpu up" in n for n in sc(1)["notes"])'
check "unknown workload is a tool error the model can read" "${out}" \
    'r[2]["result"]["isError"] is True and "llm" in r[2]["result"]["content"][0]["text"]'
check "unknown argument is a tool error" "${out}" 'r[3]["result"]["isError"] is True and "gpus" in r[3]["result"]["content"][0]["text"]'
out=$(session "${d}" "$(call 1 recommend_placement)")
check "missing required workload is a tool error naming it" "${out}" \
    'r[1]["result"]["isError"] is True and "workload" in r[1]["result"]["content"][0]["text"]'

# ---- agent_endpoint -------------------------------------------------------------

d=$(fixtures agent-none)
out=$(session "${d}" "$(call 1 agent_endpoint)")
check "agent: no cluster context" "${out}" 'sc(1)["deployed"] is False and "k3d-gpu up" in sc(1)["reason"]'

context "${d}"
out=$(session "${d}" "$(call 1 agent_endpoint)")
check "agent: not deployed points at 'k3d-gpu agent up'" "${out}" \
    'sc(1)["deployed"] is False and "k3d-gpu agent up" in sc(1)["reason"] and sc(1)["baseUrl"] is None'

d=$(fixtures agent-up)
context "${d}" dev
fixture "${d}" -n k3d-gpu-agent get service ovms -o json <<'EOF'
{"spec":{"ports":[{"name":"http","port":8000,"targetPort":"http"}]}}
EOF
fixture "${d}" -n k3d-gpu-agent get deployment ovms -o json <<'EOF'
{"spec":{"template":{"metadata":{"annotations":{"k3d-gpu.io/agent-device":"GPU"}}}},"status":{"readyReplicas":1}}
EOF
fixture "${d}" get --raw /api/v1/namespaces/k3d-gpu-agent/services/ovms:http/proxy/v3/models <<'EOF'
{"object":"list","data":[{"id":"Qwen3-4B-int4-ov","object":"model","created":0,"owned_by":"OVMS"}]}
EOF
out=$(session "${d}" "$(call 1 agent_endpoint)" K3D_GPU_CLUSTER=dev)
check "agent: ready on GPU with its /v3 URLs and models" "${out}" \
    'sc(1)["ready"] and sc(1)["device"] == "GPU" and sc(1)["models"] == ["Qwen3-4B-int4-ov"] and sc(1)["baseUrl"] == "http://ovms.k3d-gpu-agent.svc.cluster.local:8000/v3" and sc(1)["hostAccess"]["baseUrl"] == "http://localhost:8000/v3"'
check "agent: port-forward hint uses the cluster context" "${out}" \
    '"--context k3d-dev -n k3d-gpu-agent port-forward svc/ovms 8000:8000" in sc(1)["hostAccess"]["command"]'

rm "${d}/$(fixture_key get --raw /api/v1/namespaces/k3d-gpu-agent/services/ovms:http/proxy/v3/models)"
out=$(session "${d}" "$(call 1 agent_endpoint)" K3D_GPU_CLUSTER=dev)
check "agent: deployed but not answering is not ready" "${out}" \
    'sc(1)["deployed"] and not sc(1)["ready"] and "/v3/models" in sc(1)["reason"]'

# ---- cluster_health -------------------------------------------------------------

d=$(fixtures health)
out=$(session "${d}" "$(call 1 cluster_health)")
check "health: no context is unhealthy and unreachable" "${out}" 'not sc(1)["reachable"] and not sc(1)["healthy"]'

context "${d}"
fixture "${d}" get nodes -o json <<'EOF'
{"items":[{"metadata":{"name":"k3d-gpu-server-0"},"status":{"conditions":[{"type":"Ready","status":"True"}],"allocatable":{"cpu":"16","gpu.intel.com/xe":"1","npu.intel.com/accel":"0"}}}]}
EOF
fixture "${d}" get daemonsets --all-namespaces -o json <<'EOF'
{"items":[{"metadata":{"namespace":"kube-system","name":"intel-gpu-plugin"},"status":{"desiredNumberScheduled":1,"numberReady":1}},{"metadata":{"namespace":"kube-system","name":"svclb-traefik"},"status":{"desiredNumberScheduled":1,"numberReady":0}}]}
EOF
out=$(session "${d}" "$(call 1 cluster_health)")
check "health: Ready node and rolled-out plugin is healthy" "${out}" \
    'sc(1)["healthy"] and sc(1)["nodes"] == [{"name": "k3d-gpu-server-0", "ready": True, "accelerators": {"gpu.intel.com/xe": 1}}]'
check "health: only device-plugin DaemonSets are judged" "${out}" '[p["name"] for p in sc(1)["plugins"]] == ["intel-gpu-plugin"]'

fixture "${d}" get nodes -o json <<'EOF'
{"items":[{"metadata":{"name":"k3d-gpu-server-0"},"status":{"conditions":[{"type":"Ready","status":"False"}],"allocatable":{}}}]}
EOF
fixture "${d}" get daemonsets --all-namespaces -o json <<'EOF'
{"items":[{"metadata":{"namespace":"kube-system","name":"intel-npu-plugin"},"status":{"desiredNumberScheduled":0,"numberReady":0}},{"metadata":{"namespace":"habana-system","name":"habanalabs-device-plugin-daemonset"},"status":{"desiredNumberScheduled":2,"numberReady":1}}]}
EOF
out=$(session "${d}" "$(call 1 cluster_health)")
check "health: NotReady node, unscheduled and partial plugins are problems" "${out}" \
    'not sc(1)["healthy"] and len(sc(1)["problems"]) == 3 and any("1/2" in p for p in sc(1)["problems"])'

printf 'intel gpu.intel.com/xe\nnpu npu.intel.com/accel\namx feature.node.kubernetes.io/cpu-cpuid.AMXTILE=true\n' > "${d}/detect"
fixture "${d}" get nodes -o json <<'EOF'
{"items":[{"metadata":{"name":"k3d-gpu-server-0"},"status":{"conditions":[{"type":"Ready","status":"True"}],"allocatable":{"gpu.intel.com/xe":"1"}}}]}
EOF
fixture "${d}" get daemonsets --all-namespaces -o json <<'EOF'
{"items":[{"metadata":{"namespace":"kube-system","name":"intel-gpu-plugin"},"status":{"desiredNumberScheduled":1,"numberReady":1}}]}
EOF
out=$(session "${d}" "$(call 1 cluster_health)")
check "health: a detected device whose plugin DaemonSet is absent is a problem" "${out}" \
    'not sc(1)["healthy"] and len(sc(1)["problems"]) == 1 and "kube-system/intel-npu-plugin" in sc(1)["problems"][0]'
rm "${d}/detect"

echo 'not json' | fixture "${d}" get nodes -o json
out=$(session "${d}" "$(call 1 cluster_health)")
check "health: kubectl printing invalid JSON is unreachable, not a crash" "${out}" \
    'not sc(1)["reachable"] and any("invalid JSON" in p for p in sc(1)["problems"])'

# ---- transport ------------------------------------------------------------------

d=$(fixtures transport)
if timeout 10 env PATH="${STUB_BIN}:${PATH}" STUB_KUBE="${d}" python3 "${MCP}" < /dev/null > "${d}/out" 2>&1 \
    && [ ! -s "${d}/out" ]; then
    PASS=$((PASS + 1)); echo "ok   server exits cleanly on stdin EOF"
else
    FAILS=$((FAILS + 1)); echo "FAIL server exits cleanly on stdin EOF"; sed 's/^/  | /' "${d}/out"
fi
out=$(session "${d}" "$(call 1 cluster_health)")
check "every reply is one line of JSON" "${out}" 'len(lines) == 1 and "\n" not in lines[0]'

# ---- README -----------------------------------------------------------------------

# `claude mcp add --env K=V <name>` reads <name> as another K=V pair and fails
# (code.claude.com/docs/en/mcp): every --env must be followed by another option.
README="$(dirname "${MCP}")/../README.md"
ENV_THEN_NAME='claude mcp add.*(-e|--env) +[A-Za-z_][A-Za-z0-9_]*=[^ ]+ +[^- ]'
if grep -qE "${ENV_THEN_NAME}" "${README}"; then
    FAILS=$((FAILS + 1)); echo "FAIL README: claude mcp add puts the server name right after --env"
    grep -E "${ENV_THEN_NAME}" "${README}" | sed 's/^/  | /'
else
    PASS=$((PASS + 1)); echo "ok   README claude mcp add examples keep --env away from the server name"
fi

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
