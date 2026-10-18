#!/usr/bin/env bash
# live.sh: one agent-benchmark arm, live, inside one hold of the shared lock.
#
#   bench/eatflow/live.sh ARM OUT            ARM = A1 A2 A3 A3ref B1 C1 C2 (local), R (remote),
#                                            S (smoke: a stub server already on port 18630)
#
# Local arm (EXPERIMENTS.md H40-H48 amendment 19): the model server runs in a fresh
# k3d-gpu cluster k3dgpu-a-<arm> (A1/A2 OVMS int4, A3 vLLM CUDA AWQ, B1 EAT stock vLLM
# CPU bf16) or as a host container with vendor defaults (C1 Ollama, C2 llama.cpp). On
# the host, the same for every arm: EAT's LiteLLM (alias Qwen/Qwen3-4B-Instruct-2507),
# the recording proxy (in-process in `run`, sampling agent_local), the banking agent
# and its MCP sidecar from the pinned blueprint commit, ENABLE_MEMORY=false. Then B24 at
# N=1 under LIVE_LEVEL background load, and everything is torn down.
#
# Remote arm R (H48): no cluster, no load. One capped proxy (--key-file, 20 POSTs,
# sampling agent_remote) in front of harvest carries BFCL --limit 8 and four B24 tasks.
#
# Environment:
#   LIVE_MODELS     dir with Qwen3-4B-Instruct-2507{,-AWQ,-int4-ov}, gguf/, ollama/   (required, local)
#   LIVE_BLUEPRINT  enterprise-ai-blueprints checkout at 758d484                      (required)
#   LIVE_PY         python with the blueprint's requirements.txt pins                 (required)
#   LIVE_EAT_CHART  EAT core/helm-charts/vllm at 0d3438d                              (B1)
#   LIVE_KEY_FILE   0600 env file with OPENAI_BASE_URL and OPENAI_API_KEY             (R)
#   LIVE_BFCL_PY    python of the hashed BFCL venv                                    (R)
#   LIVE_LEVEL      background load level (default typical; R always none)
#   LIVE_CAP        wall-clock cap of the hold, s (default 1140)
#   LIVE_BUDGET     measurement budget cap, s (default 720)
#   LIVE_MODE       agent (default): B24 at LIVE_LEVEL, then a control window;
#                   bfcl: H41, BFCL S100 server-fc at LIVE_LEVEL, no agent (amendment 23);
#                   h44: B24's first 12 tasks in blocks none/heavy/none/heavy (amendment 24);
#                   pairs: ARM = H47 or H49, both arms live in cluster k3dgpu-e-<arm>, one hold
#                   of interleaved pairs into LIVE_PAIRS_DIR (LIVE_HOLD = this hold's number)
set -uo pipefail

ARM=${1:?usage: live.sh ARM OUT}
OUT=${2:?usage: live.sh ARM OUT}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
K=${REPO}/scripts/k3d-gpu
AB="python3 ${HERE}/agentbench.py"
BP=${LIVE_BLUEPRINT:?}/usecases/banking-assistant/src
PY=${LIVE_PY:?}
CAP=${LIVE_CAP:-1140}
LEVEL=${LIVE_LEVEL:-typical}
MODE=${LIVE_MODE:-agent}
ALIAS=Qwen/Qwen3-4B-Instruct-2507
SERVED=qwen3-4b-instruct-2507
P_SRV=18630 P_LLM=18640 P_PROXY=18620 P_MCP=18600
LITELLM_IMAGE=docker.litellm.ai/berriai/litellm:v1.83.14-stable.patch.2@sha256:73cbb215d3d43ae5b42816217b200386eb08d567767fcb36cba163152c5f3c79  # EAT 0d3438d genai-gateway; the index digest the 2026-10-17/18 holds pulled
RAPL_IMAGE=python:3.14.8-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
lc=$(echo "${ARM}" | tr 'A-Z' 'a-z')
PFX=k3dgpu-a
[ "${MODE}" = pairs ] && PFX=k3dgpu-e
export K3D_GPU_CLUSTER=${PFX}-${lc}
mkdir -p "${OUT}"
OUT=$(cd "${OUT}" && pwd)
export KUBECONFIG=${OUT}/kubeconfig XDG_STATE_HOME=${OUT}/state
t0=$(date +%s)
el() { echo $(( $(date +%s) - t0 )); }
stamp() { echo "== $1 $(date +%T) (+$(el)s)" | tee -a "${OUT}/live.log"; }
pids=()
# DevUI bearer token for this hold only (the agent and the harness read DEVUI_AUTH_TOKEN).
DEVUI_AUTH_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(16))")
export DEVUI_AUTH_TOKEN

cleanup() {
    stamp cleanup
    python3 "${REPO}/bench/load/load.py" stop >/dev/null 2>&1
    for p in "${pids[@]}"; do kill "${p}" 2>/dev/null; done
    [ -f "${OUT}/mcp.pid" ] && kill "$(cat "${OUT}/mcp.pid")" 2>/dev/null
    for f in "${OUT}"/agent-*.pid; do [ -f "${f}" ] && kill "$(cat "${f}")" 2>/dev/null; done
    docker rm -f "${PFX}-litellm" "${PFX}-litellm-a" "${PFX}-litellm-b" "${PFX}-${lc}-srv" "${PFX}-rapl" >/dev/null 2>&1
    k3d cluster delete "${K3D_GPU_CLUSTER}" >/dev/null 2>&1
    stamp "done"
}
trap cleanup EXIT

wait_http() {  # wait_http URL SECONDS
    local end=$(( $(date +%s) + $2 ))
    until curl -fsS -o /dev/null --max-time 5 "$1"; do
        [ "$(date +%s)" -lt "${end}" ] || return 1
        sleep 2
    done
}

start_mcp() {
    (cd "${BP}" && MCP_HOST=127.0.0.1 MCP_PORT=${P_MCP} exec "${PY}" -m mcp_server.server) >> "${OUT}/mcp.log" 2>&1 &
    echo $! > "${OUT}/mcp.pid"
    local end=$(( $(date +%s) + 60 ))
    until (exec 3<>"/dev/tcp/127.0.0.1/${P_MCP}") 2>/dev/null; do
        [ "$(date +%s)" -lt "${end}" ] || return 1
        sleep 0.5
    done
}

# The sidecar reset (--reset): restart the MCP process; its state is in-process seed data.
cat > "${OUT}/reset-mcp.sh" <<EOF
#!/usr/bin/env bash
kill "\$(cat "${OUT}/mcp.pid")" 2>/dev/null
for _ in \$(seq 50); do (exec 3<>"/dev/tcp/127.0.0.1/${P_MCP}") 2>/dev/null || break; sleep 0.1; done
cd "${BP}" && (MCP_HOST=127.0.0.1 MCP_PORT=${P_MCP} exec "${PY}" -m mcp_server.server >> "${OUT}/mcp.log" 2>&1 &
               echo \$! > "${OUT}/mcp.pid")
for _ in \$(seq 120); do (exec 3<>"/dev/tcp/127.0.0.1/${P_MCP}") 2>/dev/null && exit 0; sleep 0.5; done
exit 1
EOF
chmod +x "${OUT}/reset-mcp.sh"

start_agent() {  # start_agent MODEL_NAME
    (cd "${BP}" && OPENAI_BASE_URL="http://127.0.0.1:${P_PROXY}/v1" OPENAI_API_KEY=not-needed MODEL_NAME="$1" \
        MCP_SERVER_URL="http://127.0.0.1:${P_MCP}/mcp" ENABLE_MEMORY=false AUTO_OPEN_BROWSER=false \
        DEVUI_HOST=127.0.0.1 exec "${PY}" main.py) >> "${OUT}/agent.log" 2>&1 &
    pids+=($!)
    local end=$(( $(date +%s) + 90 ))
    until curl -fsS -o /dev/null --max-time 5 -H "Authorization: Bearer ${DEVUI_AUTH_TOKEN}" \
            "http://127.0.0.1:8090/v1/entities"; do
        [ "$(date +%s)" -lt "${end}" ] || return 1
        sleep 2
    done
}

git -C "${REPO}" rev-parse HEAD > "${OUT}/commit.txt"
git -C "${LIVE_BLUEPRINT}" rev-parse HEAD > "${OUT}/blueprint-commit.txt"
stamp "arm ${ARM} start"

if [ "${ARM}" = R ]; then
    # Only OPENAI_BASE_URL is taken from the env file here; the key stays in the file and
    # only the proxy reads it (--key-file). Nothing below prints either.
    # shellcheck source=/dev/null
    base=$(set -a; . "${LIVE_KEY_FILE:?}"; set +a; printf '%s' "${OPENAI_BASE_URL%/}")
    upstream=${base%/v1}
    ${AB} proxy --upstream "${upstream}" --key-file "${LIVE_KEY_FILE}" --max-requests 20 \
        --sampling agent_remote --port "${P_PROXY}" --log "${OUT}/proxy.jsonl" >> "${OUT}/proxy.err" 2>&1 &
    pids+=($!)
    wait_http "http://127.0.0.1:${P_PROXY}/_bench/stats" 30 || { echo "proxy did not start"; exit 1; }
    curl -fsS --max-time 30 "http://127.0.0.1:${P_PROXY}/v1/models" > "${OUT}/models.json" || { echo "models failed"; exit 1; }
    model=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['data'][0]['id'])" "${OUT}/models.json")
    echo "remote model: ${model}" | tee -a "${OUT}/live.log"
    start_mcp || { echo "mcp did not start"; exit 1; }
    start_agent "${model}" || { echo "agent did not start"; exit 1; }
    stamp bfcl
    ${AB} bfcl --mode server-fc --arm R --level none --out "${OUT}/bfcl" --proxy-url "http://127.0.0.1:${P_PROXY}" \
        --served-model "${model}" --python "${LIVE_BFCL_PY:?}" --threads 1 --limit 8 --budget-s 600 \
        --no-energy --no-load-sample 2>&1 | tail -5 | tee -a "${OUT}/live.log"
    stamp run
    ${AB} run --arm R --level none --out "${OUT}/run" --agent-url http://127.0.0.1:8090 --tasks B24 \
        --ids B-T1-U1001,B-T3-U1002,B-T5-U1003,B-T8-U1001 --proxy-log "${OUT}/proxy.jsonl" \
        --mcp-url "http://127.0.0.1:${P_MCP}/mcp" --reset "${OUT}/reset-mcp.sh" --no-warmup \
        --budget-s 600 --no-energy --no-load-sample 2>&1 | tail -5 | tee -a "${OUT}/live.log"
    curl -fsS "http://127.0.0.1:${P_PROXY}/_bench/stats" | tee -a "${OUT}/live.log"
    exit 0
fi

M=${LIVE_MODELS:?}
docker rm -f "${PFX}-rapl" >/dev/null 2>&1
# RAPL energy_uj is root-only; a read-only container with the powercap paths unmasked
# reads it (K3D_GPU_RAPL_EXEC), as no sudo is available to the harness.
docker run -d --name "${PFX}-rapl" --security-opt systempaths=unconfined --security-opt apparmor=unconfined \
    --read-only --network none "${RAPL_IMAGE}" sleep 7200 >/dev/null
export K3D_GPU_RAPL_EXEC="docker exec ${PFX}-rapl"

nodes() { docker ps --filter "label=k3d.cluster=${K3D_GPU_CLUSTER}" --filter "label=k3d.role=server" --format '{{.Names}}'; }
stage() {  # stage <local dir> <path under /var/apb-models> <uid>
    local n
    for n in $(nodes); do
        docker exec "${n}" mkdir -p "/var/apb-models/$2"
        docker cp "${M}/$1/." "${n}:/var/apb-models/$2/"
        docker exec "${n}" rm -rf "/var/apb-models/$2/.cache"
        docker exec "${n}" chown -R "$3:$3" /var/apb-models
    done
}
cluster_up() {
    stamp "k3d-gpu up"
    timeout 300 "${K}" up > "${OUT}/up.log" 2>&1 || { echo "up failed"; tail -20 "${OUT}/up.log"; exit 1; }
}
if [ "${MODE}" = pairs ]; then
    # H47 / H49 (EXPERIMENTS.md "H47 and H49 pre-registration"): both arms live in one
    # cluster, each with its own LiteLLM, proxy (in-process in `agentbench pairs`) and
    # agent; one shared MCP sidecar; pairs A B B A over B24, one attempt in flight at a time.
    # LIVE_PAIRS_DIR: the pairs directory shared by every hold; LIVE_HOLD: this hold's number;
    # LIVE_PAIRS_GUARD: the pair-start guard, s (default 600; a smoke sets it past the hold).
    PD=${LIVE_PAIRS_DIR:?} HOLD=${LIVE_HOLD:?}
    mkdir -p "${PD}"
    PD=$(cd "${PD}" && pwd)
    case ${ARM} in
        H47) A=A1 B=A4 ;;
        H49) A=A1 B=B1; export K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true ;;
        *) echo "pairs: ARM must be H47 or H49"; exit 2 ;;
    esac
    TGT=k3dgpu/Qwen3-4B-Instruct-2507-int4-ov
    DRAFT_DIR=OpenVINO-Qwen3-0.6B-int4-ov     # where OVMS --pull puts the draft, inside the target dir
    stage_path() {  # stage_path <local dir> <path under /var/apb-models> <uid>: chown that path only
        local n
        for n in $(nodes); do
            docker exec "${n}" mkdir -p "/var/apb-models/$2" /var/apb-models/.ov_cache
            docker cp "${M}/$1/." "${n}:/var/apb-models/$2/"
            docker exec "${n}" rm -rf "/var/apb-models/$2/.cache" "/var/apb-models/$2/.git"
            docker exec "${n}" chown -R "$3:$3" "/var/apb-models/$2"
            docker exec "${n}" chown 5000:5000 /var/apb-models/.ov_cache
            docker exec "${n}" chmod 1777 /var/apb-models
        done
    }
    cluster_up
    stage_path Qwen3-4B-Instruct-2507-int4-ov "${TGT}" 5000
    stage_path Qwen3-0.6B-int4-ov "${TGT}/${DRAFT_DIR}" 5000
    draft=CPU
    [ "${ARM}" = H47 ] && draft=off
    K3D_GPU_AGENT_MODEL=${TGT} K3D_GPU_AGENT_MODEL_NAME=${SERVED} K3D_GPU_AGENT_DRAFT=${draft} \
        K3D_GPU_AGENT_TOOL_PARSER=hermes3 K3D_GPU_AGENT_REASONING_PARSER='' K3D_GPU_AUTOQUANT=off \
        "${K}" agent manifest > "${OUT}/agent.yaml" 2> "${OUT}/agent.err" || { cat "${OUT}/agent.err"; exit 1; }
    SERVED_A=${SERVED}
    if [ "${ARM}" = H47 ]; then
        # One OVMS pod, two graph names: A1 (no draft) and A4 (CPU Fast Draft), the graphs
        # OVMS 2026.4.1 --pull writes for those settings (bench/eatflow/h47/).
        SERVED_B=${SERVED}-a4
        python3 "${REPO}/bench/apb/stage.py" ovms2 "${OUT}/agent.yaml" "${OUT}/deploy-a.yaml" "${TGT}" \
            "${SERVED_A}=${HERE}/h47/graph-a1.pbtxt" "${SERVED_B}=${HERE}/h47/graph-a4.pbtxt"
    else
        python3 "${REPO}/bench/apb/stage.py" ovms "${OUT}/agent.yaml" "${OUT}/deploy-a.yaml"
        chart=${LIVE_EAT_CHART:?}
        printf 'image:\n  tag: "v0.24.0@sha256:b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1"\n' \
            > "${OUT}/pin.yaml"
        python3 "${REPO}/bench/apb/stage.py" values "${chart}/xeon-values.yaml" "${OUT}/pin.yaml" 6 "${OUT}/values.yaml"
        helm template vllm-qwen-3-4b-cpu "${chart}" --values "${OUT}/values.yaml" \
            --set LLM_MODEL_ID=${ALIAS} --set global.monitoring=false \
            --set svcmonitor.enabled=false --set ingress.enabled=false --set route.enabled=false > "${OUT}/eat.yaml"
        python3 "${REPO}/bench/apb/stage.py" vllm "${OUT}/eat.yaml" "${OUT}/deploy-b.yaml" Qwen3-4B-Instruct-2507 8192
        stage_path Qwen3-4B-Instruct-2507 Qwen3-4B-Instruct-2507 1001
        kubectl apply -f "${OUT}/deploy-b.yaml" > /dev/null
    fi
    kubectl apply -f "${OUT}/deploy-a.yaml" > /dev/null
    stamp wait-ready
    ready() {  # ready NS DEP: rolled out with at least 900 s of the hold left
        local left=$(( CAP - $(el) - 900 ))
        [ "${left}" -gt 0 ] && kubectl -n "$1" rollout status "deploy/$2" --timeout="${left}s"
    }
    if ! ready k3d-gpu-agent ovms || { [ "${ARM}" = H49 ] && ! ready default vllm-qwen-3-4b-cpu; }; then
        echo "NOT DELIVERABLE: not ready with 900 s of the hold left" | tee -a "${OUT}/live.log"
        kubectl get pods -A > "${OUT}/pods.txt" 2>&1
        kubectl -n k3d-gpu-agent logs deploy/ovms --all-containers > "${OUT}/server-a.log" 2>&1
        [ "${ARM}" = H49 ] && kubectl -n default logs deploy/vllm-qwen-3-4b-cpu > "${OUT}/server-b.log" 2>&1
        exit 3
    fi
    stamp ready
    podinfo() {  # podinfo NS: "<uid> <image> <imageID>" of the running pod
        kubectl -n "$1" get pods --field-selector=status.phase=Running \
            -o jsonpath='{.items[0].metadata.uid} {.items[0].spec.containers[0].image} {.items[0].status.containerStatuses[0].imageID}'
    }
    podinfo k3d-gpu-agent > "${OUT}/image-a.txt"
    read -r uid_a _ < "${OUT}/image-a.txt"
    kubectl -n k3d-gpu-agent port-forward svc/ovms "${P_SRV}:8000" > "${OUT}/pf-a.log" 2>&1 &
    pids+=($!)
    cg_a=$(${AB} cgroup --uid "${uid_a}") || { echo "no cgroup for the A pod"; exit 1; }
    cg_b=${cg_a}
    BPORT=${P_SRV}
    if [ "${ARM}" = H49 ]; then
        podinfo default > "${OUT}/image-b.txt"
        read -r uid_b _ < "${OUT}/image-b.txt"
        BPORT=$(( P_SRV + 1 ))
        kubectl -n default port-forward svc/vllm-qwen-3-4b-cpu-service "${BPORT}:80" > "${OUT}/pf-b.log" 2>&1 &
        pids+=($!)
        cg_b=$(${AB} cgroup --uid "${uid_b}") || { echo "no cgroup for the B pod"; exit 1; }
    fi
    echo "pod cgroups: ${A} ${cg_a}; ${B} ${cg_b}" | tee -a "${OUT}/live.log"
    grep -h usage_usec "${cg_a}/cpu.stat" "${cg_b}/cpu.stat" | tee -a "${OUT}/live.log" | grep -q . \
        || { echo "pod cpu.stat unreadable"; exit 1; }
    wait_http "http://127.0.0.1:${P_SRV}/v1/models" 120 || { echo "A server not answering"; exit 3; }
    curl -fsS "http://127.0.0.1:${P_SRV}/v1/models" > "${OUT}/models-a.json"
    if [ "${ARM}" = H49 ]; then
        wait_http "http://127.0.0.1:${BPORT}/v1/models" 300 || { echo "B server not answering"; exit 3; }
        curl -fsS "http://127.0.0.1:${BPORT}/v1/models" > "${OUT}/models-b.json"
        SERVED_B=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['data'][0]['id'])" "${OUT}/models-b.json")
    fi
    litellm() {  # litellm x served port llm_port
        printf 'model_list:\n  - model_name: %s\n    litellm_params:\n      model: openai/%s\n      api_base: http://127.0.0.1:%s/v1\n      api_key: not-needed\n      timeout: 600\n' \
            "${ALIAS}" "$2" "$3" > "${OUT}/litellm-$1.yaml"
        docker run -d --name "${PFX}-litellm-$1" --network host -v "${OUT}/litellm-$1.yaml:/app/config.yaml:ro" \
            "${LITELLM_IMAGE}" --config /app/config.yaml --port "$4" --host 127.0.0.1 >/dev/null
        docker inspect --format '{{.Image}} {{index .Config.Image}}' "${PFX}-litellm-$1" > "${OUT}/litellm-image-$1.txt"
        wait_http "http://127.0.0.1:$4/health/liveliness" 120 || { echo "litellm $1 did not start"; exit 1; }
    }
    litellm a "${SERVED_A}" "${P_SRV}" "${P_LLM}"
    litellm b "${SERVED_B}" "${BPORT}" "$(( P_LLM + 1 ))"
    echo "served: ${A}=${SERVED_A} ${B}=${SERVED_B}" | tee -a "${OUT}/live.log"
    start_mcp || { echo "mcp did not start"; exit 1; }
    # One agent per arm. DevUI's port is fixed (8090), so the arms bind two loopback addresses.
    for x in a b; do
        if [ "${x}" = a ]; then host=127.0.0.1 pport=${P_PROXY}; else host=127.0.0.2 pport=$(( P_PROXY + 1 )); fi
        {
            echo '#!/usr/bin/env bash'
            echo "# (Re)start the ${x} arm's banking agent and wait for its DevUI."
            echo "[ -f '${OUT}/agent-${x}.pid' ] && kill \"\$(cat '${OUT}/agent-${x}.pid')\" 2>/dev/null"
            echo "for _ in \$(seq 100); do (exec 3<>/dev/tcp/${host}/8090) 2>/dev/null || break; sleep 0.1; done"
            echo "cd '${BP}' && (OPENAI_BASE_URL=http://127.0.0.1:${pport}/v1 OPENAI_API_KEY=not-needed MODEL_NAME='${ALIAS}' \\"
            echo "    MCP_SERVER_URL=http://127.0.0.1:${P_MCP}/mcp ENABLE_MEMORY=false AUTO_OPEN_BROWSER=false \\"
            echo "    DEVUI_HOST=${host} exec '${PY}' main.py >> '${OUT}/agent-${x}.log' 2>&1 &"
            echo "    echo \$! > '${OUT}/agent-${x}.pid')"
            echo "for _ in \$(seq 45); do"
            echo "    curl -fsS -o /dev/null --max-time 5 -H \"Authorization: Bearer \${DEVUI_AUTH_TOKEN}\" http://${host}:8090/v1/entities && exit 0"
            echo "    sleep 2"
            echo "done"
            echo "exit 1"
        } > "${OUT}/agent-${x}.sh"
        chmod 700 "${OUT}/agent-${x}.sh"
        "${OUT}/agent-${x}.sh" || { echo "agent ${x} did not start"; tail -20 "${OUT}/agent-${x}.log"; exit 1; }
    done
    stamp "load ${LEVEL}"
    python3 "${REPO}/bench/load/load.py" start --level "${LEVEL}" --ttl $(( CAP - $(el) + 60 )) > "${OUT}/load-start.log" 2>&1
    deadline=$(( t0 + CAP - 120 ))     # teardown fits in the last 120 s of the hold
    idle=()
    [ "${ARM}" = H47 ] && idle=(--idle-s 3)
    stamp "pairs hold ${HOLD}, deadline +$(( deadline - t0 ))s"
    ${AB} pairs --hyp "${ARM}" --hold "${HOLD}" --out "${PD}" --deadline "${deadline}" --tasks B24 \
        --arm-a "${A}" --agent-a http://127.0.0.1:8090 --upstream-a "http://127.0.0.1:${P_LLM}" \
        --proxy-port-a "${P_PROXY}" --restart-a "${OUT}/agent-a.sh" --pod-a "${cg_a}" \
        --arm-b "${B}" --agent-b http://127.0.0.2:8090 --upstream-b "http://127.0.0.1:$(( P_LLM + 1 ))" \
        --proxy-port-b "$(( P_PROXY + 1 ))" --restart-b "${OUT}/agent-b.sh" --pod-b "${cg_b}" \
        --mcp-url "http://127.0.0.1:${P_MCP}/mcp" --reset "${OUT}/reset-mcp.sh" --sampling agent_local \
        --guard-s "${LIVE_PAIRS_GUARD:-600}" "${idle[@]}" 2>&1 | tee -a "${OUT}/live.log"
    stamp finished
    exit 0
fi
gpu_flag=()
case ${ARM} in
    A1|A2)
        cluster_up
        [ "${ARM}" = A2 ] && export K3D_GPU_AGENT_DEVICE=GPU
        K3D_GPU_AGENT_MODEL=k3dgpu/Qwen3-4B-Instruct-2507-int4-ov K3D_GPU_AGENT_MODEL_NAME=${SERVED} \
            K3D_GPU_AGENT_TOOL_PARSER=hermes3 K3D_GPU_AGENT_REASONING_PARSER='' K3D_GPU_AUTOQUANT=off \
            "${K}" agent manifest > "${OUT}/agent.yaml" 2> "${OUT}/agent.err" || { cat "${OUT}/agent.err"; exit 1; }
        python3 "${REPO}/bench/apb/stage.py" ovms "${OUT}/agent.yaml" "${OUT}/deploy.yaml"
        stage Qwen3-4B-Instruct-2507-int4-ov k3dgpu/Qwen3-4B-Instruct-2507-int4-ov 5000
        NS=k3d-gpu-agent DEP=ovms SVC=ovms SPORT=8000 ;;
    A3|A3ref)
        # A3: AWQ 4-bit; A3ref (H40, H41 reference): the bf16 weights on the same engine and GPU.
        w=Qwen3-4B-Instruct-2507-AWQ
        [ "${ARM}" = A3ref ] && w=Qwen3-4B-Instruct-2507
        cluster_up
        gpu=$(nvidia-smi --query-gpu=name,uuid --format=csv,noheader | awk -F', ' '/RTX 4000/ {print $2}')
        image=vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b
        sed -e "s|@VLLM_IMAGE@|${image}|" -e "s|@GPU_UUID@|${gpu}|" \
            -e "s|--served-model-name=Qwen/Qwen3-4B-AWQ|--served-model-name=${SERVED}|" \
            "${REPO}/bench/apb/vllm-cuda.yaml" > "${OUT}/cuda.yaml"
        python3 "${REPO}/bench/apb/stage.py" cuda "${OUT}/cuda.yaml" "${OUT}/deploy.yaml" "${w}"
        stage "${w}" "${w}" 0
        gpu_flag=(--gpu-power)
        NS=apb-vllm DEP=vllm SVC=vllm SPORT=8000 ;;
    B1)
        export K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true
        cluster_up
        chart=${LIVE_EAT_CHART:?}
        # EAT stock: xeon-values.yaml as shipped, the tag pinned to its digest and the KV cache
        # sized to fit this host (6 GiB); no k3d-gpu balloon overlay, stock max_model_len 8192.
        printf 'image:\n  tag: "v0.24.0@sha256:b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1"\n' \
            > "${OUT}/pin.yaml"
        python3 "${REPO}/bench/apb/stage.py" values "${chart}/xeon-values.yaml" "${OUT}/pin.yaml" 6 "${OUT}/values.yaml"
        helm template vllm-qwen-3-4b-cpu "${chart}" --values "${OUT}/values.yaml" \
            --set LLM_MODEL_ID=${ALIAS} --set global.monitoring=false \
            --set svcmonitor.enabled=false --set ingress.enabled=false --set route.enabled=false > "${OUT}/eat.yaml"
        python3 "${REPO}/bench/apb/stage.py" vllm "${OUT}/eat.yaml" "${OUT}/deploy.yaml" Qwen3-4B-Instruct-2507 8192
        stage Qwen3-4B-Instruct-2507 Qwen3-4B-Instruct-2507 1001
        NS=default DEP=vllm-qwen-3-4b-cpu SVC=vllm-qwen-3-4b-cpu-service SPORT=80 ;;
    C1)
        stamp "ollama"
        docker run -d --name "${PFX}-${lc}-srv" -p "127.0.0.1:${P_SRV}:11434" -v "${M}/ollama:/root/.ollama" \
            ollama/ollama:0.40.5@sha256:5d3a2f25f9825628daba94dc0b1d421c7d57d6c03ae5faa9ba050e058bab8241 >/dev/null
        SERVED=qwen3:4b-instruct-2507-q4_K_M ;;
    C2)
        stamp "llama.cpp"
        docker run -d --name "${PFX}-${lc}-srv" -p "127.0.0.1:${P_SRV}:8080" -v "${M}/gguf:/models:ro" \
            ghcr.io/ggml-org/llama.cpp:server-b11706@sha256:f41d47eee678c02656eefc9ed8cce76cab584aa7032805eeef0e6639fcfda485 \
            -m /models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf --alias "${SERVED}" --jinja --host 0.0.0.0 --port 8080 >/dev/null ;;
    S)  stamp "smoke: a stub server is already on 127.0.0.1:${P_SRV}" ;;
    *) echo "unknown arm ${ARM}"; exit 2 ;;
esac

if [ -n "${NS:-}" ]; then
    kubectl apply -f "${OUT}/deploy.yaml" > /dev/null
    stamp wait-ready
    left=$(( CAP - $(el) - 420 ))
    if [ "${left}" -le 0 ] || ! kubectl -n "${NS}" rollout status "deploy/${DEP}" --timeout="${left}s"; then
        echo "NOT DELIVERABLE: not ready within the cap" | tee -a "${OUT}/live.log"
        kubectl -n "${NS}" describe pods | tail -30 > "${OUT}/describe.txt"
        kubectl -n "${NS}" logs "deploy/${DEP}" --all-containers > "${OUT}/server.log" 2>&1
        exit 3
    fi
    kubectl -n "${NS}" get pods -o jsonpath='{range .items[*]}{.spec.containers[0].image}{" "}{.status.containerStatuses[0].imageID}{"\n"}{end}' > "${OUT}/image.txt"
    kubectl -n "${NS}" port-forward "svc/${SVC}" "${P_SRV}:${SPORT}" > "${OUT}/pf.log" 2>&1 &
    pids+=($!)
fi
stamp server-wait
case ${ARM} in
    C1) wait_http "http://127.0.0.1:${P_SRV}/api/version" 120 && \
            curl -fsS "http://127.0.0.1:${P_SRV}/api/tags" > "${OUT}/ollama-tags.json" ;;
    *) wait_http "http://127.0.0.1:${P_SRV}/v1/models" 600 ;;
esac || { echo "NOT DELIVERABLE: server not answering"; docker logs "${PFX}-${lc}-srv" > "${OUT}/server.log" 2>&1; exit 3; }
curl -fsS "http://127.0.0.1:${P_SRV}/v1/models" > "${OUT}/models.json"
# LiteLLM targets the name the server itself serves (the EAT chart names its own).
SERVED=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['data'][0]['id'])" "${OUT}/models.json")
echo "served model: ${SERVED}" | tee -a "${OUT}/live.log"

cat > "${OUT}/litellm.yaml" <<EOF
model_list:
  - model_name: ${ALIAS}
    litellm_params:
      model: openai/${SERVED}
      api_base: http://127.0.0.1:${P_SRV}/v1
      api_key: not-needed
      timeout: 600
EOF
docker run -d --name "${PFX}-litellm" --network host -v "${OUT}/litellm.yaml:/app/config.yaml:ro" \
    "${LITELLM_IMAGE}" --config /app/config.yaml --port "${P_LLM}" --host 127.0.0.1 >/dev/null
docker inspect --format '{{.Image}} {{index .Config.Image}}' "${PFX}-litellm" > "${OUT}/litellm-image.txt"
wait_http "http://127.0.0.1:${P_LLM}/health/liveliness" 120 || { echo "litellm did not start"; docker logs "${PFX}-litellm" | tail -20; exit 1; }
if [ "${ARM}" = A3ref ]; then
    # H40: BFCL's board handler (completions, BFCL's own template) at no load; then H41's
    # reference: server-fc (the engine's tool parser) at LEVEL load. No agent is involved.
    half=$(( (CAP - $(el) - 60) / 2 ))
    [ "${half}" -ge 120 ] || { echo "NOT DELIVERABLE: ${half} s per BFCL half"; exit 3; }
    stamp "bfcl board, load none, budget ${half}"
    ${AB} bfcl --mode board --arm A3ref --level none --out "${OUT}/bfcl-board" --upstream "http://127.0.0.1:${P_LLM}" \
        --python "${LIVE_BFCL_PY:?}" --threads 4 --budget-s "${half}" "${gpu_flag[@]}" 2>&1 | tail -3 | tee -a "${OUT}/live.log"
    half=$(( CAP - $(el) - 60 ))
    [ "${half}" -le 600 ] || half=600
    stamp "load ${LEVEL}"
    python3 "${REPO}/bench/load/load.py" start --level "${LEVEL}" --ttl $(( half + 120 )) > "${OUT}/load-start.log" 2>&1
    stamp "bfcl server-fc, budget ${half}"
    ${AB} bfcl --mode server-fc --arm A3ref --level "${LEVEL}" --out "${OUT}/bfcl-fc" --upstream "http://127.0.0.1:${P_LLM}" \
        --served-model "${ALIAS}" --python "${LIVE_BFCL_PY}" --threads 4 --budget-s "${half}" "${gpu_flag[@]}" 2>&1 \
        | tail -3 | tee -a "${OUT}/live.log"
    stamp finished
    exit 0
fi
if [ "${MODE}" = bfcl ]; then
    # H41: server-fc (the engine's tool parser) at LEVEL load, one block, no agent.
    budget=$(( CAP - $(el) - 120 ))
    [ "${budget}" -le 900 ] || budget=900
    [ "${budget}" -ge 120 ] || { echo "NOT DELIVERABLE: ${budget} s left for BFCL"; exit 3; }
    stamp "load ${LEVEL}"
    python3 "${REPO}/bench/load/load.py" start --level "${LEVEL}" --ttl $(( budget + 120 )) > "${OUT}/load-start.log" 2>&1
    stamp "bfcl server-fc, budget ${budget}"
    ${AB} bfcl --mode server-fc --arm "${ARM}" --level "${LEVEL}" --out "${OUT}/bfcl-fc" --upstream "http://127.0.0.1:${P_LLM}" \
        --served-model "${ALIAS}" --python "${LIVE_BFCL_PY:?}" --threads 4 --budget-s "${budget}" "${gpu_flag[@]}" 2>&1 \
        | tail -3 | tee -a "${OUT}/live.log"
    stamp finished
    exit 0
fi
start_mcp || { echo "mcp did not start"; exit 1; }
start_agent "${ALIAS}" || { echo "agent did not start"; tail -20 "${OUT}/agent.log"; exit 1; }

if [ "${MODE}" = h44 ]; then
    # H44: B24's first 12 tasks, blocks none/heavy/none/heavy of equal budget, one out dir each.
    blk=$(( (CAP - $(el) - 60) / 4 - 60 ))
    [ "${blk}" -le 300 ] || blk=300
    [ "${blk}" -ge 120 ] || { echo "NOT DELIVERABLE: ${blk} s per H44 block"; exit 3; }
    i=0
    for lv in none heavy none heavy; do
        i=$(( i + 1 ))
        python3 "${REPO}/bench/load/load.py" stop >/dev/null 2>&1
        [ "${lv}" = none ] || python3 "${REPO}/bench/load/load.py" start --level "${lv}" --ttl $(( blk + 180 )) \
            >> "${OUT}/load-start.log" 2>&1
        stamp "h44 block ${i} ${lv}, budget ${blk}"
        ${AB} run --arm "${ARM}" --level "${lv}" --out "${OUT}/h44-${i}-${lv}" --agent-url http://127.0.0.1:8090 \
            --tasks B24 --limit 12 --upstream "http://127.0.0.1:${P_LLM}" --proxy-port "${P_PROXY}" \
            --sampling agent_local --mcp-url "http://127.0.0.1:${P_MCP}/mcp" --reset "${OUT}/reset-mcp.sh" \
            --budget-s "${blk}" "${gpu_flag[@]}" 2>&1 | tail -3 | tee -a "${OUT}/live.log"
    done
    stamp finished
    exit 0
fi

# Left after the block: the warm-up (before the window), the H46 control window and teardown.
budget=$(( CAP - $(el) - 330 ))
[ "${budget}" -le "${LIVE_BUDGET:-720}" ] || budget=${LIVE_BUDGET:-720}
[ "${budget}" -ge 120 ] || { echo "NOT DELIVERABLE: ${budget} s left for measurement"; exit 3; }
stamp "load ${LEVEL}"
python3 "${REPO}/bench/load/load.py" start --level "${LEVEL}" --ttl $(( budget + 300 )) > "${OUT}/load-start.log" 2>&1
stamp "run budget ${budget}"
${AB} run --arm "${ARM}" --level "${LEVEL}" --out "${OUT}/run" --agent-url http://127.0.0.1:8090 --tasks B24 \
    --upstream "http://127.0.0.1:${P_LLM}" --proxy-port "${P_PROXY}" --sampling agent_local \
    --mcp-url "http://127.0.0.1:${P_MCP}/mcp" --reset "${OUT}/reset-mcp.sh" --budget-s "${budget}" \
    "${gpu_flag[@]}" 2>&1 | tail -5 | tee -a "${OUT}/live.log"
stamp "control 120 s (server idle, same load)"
${AB} control --arm "${ARM}" --level "${LEVEL}" --out "${OUT}/run" --seconds 120 "${gpu_flag[@]}" 2>&1 \
    | tail -2 | tee -a "${OUT}/live.log"
stamp finished
