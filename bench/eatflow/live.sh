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
#   LIVE_EAT_OVMS_CHART EAT core/helm-charts/ovms at 0d3438d                         (H50o B3)
#   LIVE_EAT_XPU_CHART EAT core/helm-charts/vllm at PR #21 c86c42b                   (H53 B2)
#   LIVE_GPU_FREE   PCI addresses of the Intel GPUs the H53 arms may use               (H53)
#   LIVE_B2_TP      B2's tensor_parallel_size, cards (default 4, #21's own value)            (H53)
#   LIVE_SOLO       solo mode: the one arm of this hold, AG or B2                           (H53)
#   LIVE_B2_LADDER  H53 B2 boot ladder steps the smoke found needed (b, c, d; default none) (H53)
#   LIVE_MODEL_SET  qwen3-4b (default) or gemma4 (crescent amendment M1): every arm's model
#   LIVE_SAMPLING   the proxy's pinned sampling profile (default: the model set's; agent.lock)
#   LIVE_MOUNT_B    1: mount the B arm's weights read-only into the node instead of copying them
#   LIVE_EAT_KV_GIB B1 VLLM_CPU_KVCACHE_SPACE, GiB (default 6, the 125 GiB desktop; H50: 40, as shipped)
#   LIVE_EAT_B1_TAG B1's vLLM CPU image tag@digest (default EAT 0d3438d's v0.24.0; H50, H51: EAT
#                   release-1.1.0's v0.28.0, crescent amendment E1)
#   LIVE_KEY_FILE   0600 env file with OPENAI_BASE_URL and OPENAI_API_KEY             (R)
#   LIVE_BFCL_PY    python of the hashed BFCL venv                                    (R)
#   LIVE_LEVEL      background load level (default typical; R always none)
#   LIVE_CAP        wall-clock cap of the hold, s (default 1140)
#   LIVE_BUDGET     measurement budget cap, s (default 720)
#   LIVE_MODE       agent (default): B24 at LIVE_LEVEL, then a control window;
#                   bfcl: H41, BFCL S100 server-fc at LIVE_LEVEL, no agent (amendment 23);
#                   h44: B24's first 12 tasks in blocks none/heavy/none/heavy (amendment 24);
#                   solo: ARM = H53, one arm (LIVE_SOLO) per hold on the Arc Pro B70s (bench/eatflow/solo.py);
#                   pairs: ARM = H47, H49, H47b, H49b, H50 or H50o, both arms live in cluster k3dgpu-e-<arm>, one hold
#                   of interleaved pairs into LIVE_PAIRS_DIR (LIVE_HOLD = this hold's number);
#                   code: H43 (amendment W1), the coding agent on C12 with `k3d-gpu sandbox up`, one
#                   block per level in LIVE_CODE_LEVELS (default "typical heavy heavy typical") into
#                   LIVE_CODE_DIR/h43-<arm>-<level> (shared by an arm's holds, so a level's halves resume);
#                   apb: H45 (amendment W1), the graded ramp steps LIVE_APB_RAMP (default 1,2) on B15-RO
#                   at LIVE_LEVEL with amendment-27 windows, LIVE_APB_BASE = the hold dir holding N=1;
#                   then, on the arm's last hold, `k3d-gpu capacity --slo 10` with what the hold leaves
#                   (last hold: a step failed or N=LIVE_APB_TOP passed; default 8, H51 16)
#   LIVE_CODE_PY    python with the coding blueprint's pins and k8s-agent-sandbox 1.0.7    (code)
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
KV_GIB=${LIVE_EAT_KV_GIB:-6}
MODE=${LIVE_MODE:-agent}
# The model set (crescent amendment M1): ids, local dirs (under LIVE_MODELS), A1's OVMS tool
# parser and draft, EAT's release name and chart flags for B1, B3's OpenVINO model, and the
# sampling profile the recording proxy pins (agent.lock).
MODEL_SET=${LIVE_MODEL_SET:-qwen3-4b}
case ${MODEL_SET} in
    qwen3-4b)
        ALIAS=Qwen/Qwen3-4B-Instruct-2507 SERVED=qwen3-4b-instruct-2507
        TGT=k3dgpu/Qwen3-4B-Instruct-2507-int4-ov TGT_DIR=Qwen3-4B-Instruct-2507-int4-ov A_TOOL=hermes3 A_DRAFT=CPU
        B_DIR=Qwen3-4B-Instruct-2507 B_REL=vllm-qwen-3-4b-cpu B_CHAT=false SAMPLING=agent_local ;;
    gemma4)
        ALIAS=google/gemma-4-26B-A4B-it SERVED=gemma-4-26b-a4b-it
        TGT=k3dgpu/gemma-4-26B-A4B-it-qat-int4-ov TGT_DIR=gemma-4-26B-A4B-it-qat-int4-ov A_TOOL=gemma4 A_DRAFT=off
        B_DIR=gemma-4-26B-A4B-it-qat-q4_0-unquantized B_REL=vllm-gemma4-26b-a4b-cpu B_CHAT=true SAMPLING=agent_gemma4 ;;
    qwen38)
        # H53 (GPU): AG's OpenVINO IR and B2's bf16 weights; B2_PROFILE is the EAT PR #21 catalog
        # entry B2 renders for this model size (xpu-qwen36-27b), pointed at these weights.
        ALIAS=Qwen/Qwen3.8-27B SERVED=qwen3.8-27b
        TGT=OpenVINO/Qwen3.8-27B-int4-ov TGT_DIR=Qwen3.8-27B-int4-ov A_TOOL=qwen3coder A_DRAFT=off
        B_DIR=Qwen3.8-27B B_REL=vllm-qwen38-27b-xpu B_CHAT=false SAMPLING=agent_qwen38 B2_PROFILE=Qwen/Qwen3.6-27B ;;
    *) echo "LIVE_MODEL_SET must be qwen3-4b, gemma4 or qwen38"; exit 2 ;;
esac
SAMPLING=${LIVE_SAMPLING:-${SAMPLING}}
# LIVE_MOUNT_B=1 (crescent amendment M1): the B arm's weights are mounted read-only into the node
# (K3D_GPU_VOLUMES) and its hostPath points there, instead of a copy into the node's filesystem.
B_HOST=/var/apb-models
if [ "${LIVE_MOUNT_B:-}" = 1 ]; then
    B_HOST=/var/apb-ro
    K3D_GPU_VOLUMES="$(readlink -f "${LIVE_MODELS:?}/${B_DIR}"):${B_HOST}/${B_DIR}:ro"
    export K3D_GPU_VOLUMES
    # The mounted directory must be the one verified at setup (bench/eatflow/crescent/models.sh
    # wrote <B_DIR>.listing after its sha256 check): same files, sizes, mtimes and index hashes.
    mkdir -p "${OUT}"
    bash "${HERE}/crescent/listing.sh" "${LIVE_MODELS}/${B_DIR}" > "${OUT}/b-listing.txt" \
        || { echo "NOT DELIVERABLE: cannot list ${B_DIR}"; exit 3; }
    cmp -s "${OUT}/b-listing.txt" "${LIVE_MODELS}/${B_DIR}.listing" \
        || { echo "NOT DELIVERABLE: ${B_DIR} differs from its setup listing"; exit 3; }
fi
P_SRV=18630 P_LLM=18640 P_PROXY=18620 P_MCP=18600 P_ROUTER=18650 P_REDIS=18660
LITELLM_IMAGE=docker.litellm.ai/berriai/litellm:v1.83.14-stable.patch.2@sha256:73cbb215d3d43ae5b42816217b200386eb08d567767fcb36cba163152c5f3c79  # EAT 0d3438d genai-gateway; the index digest the 2026-10-17/18 holds pulled
RAPL_IMAGE=python:3.14.8-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
# H43: the coding agent's Redis memory. Redis 8 bundles the query engine the blueprint's
# RedisContextProvider needs (FT.CREATE); the 8.10.2-alpine index digest, read 2026-10-19.
REDIS_IMAGE=redis:8.10.2-alpine@sha256:3811787313eba226a2ef38658c6ccb91cd5e110edc89c37767de373120a0e5a0
# H50o B3: EAT's OVMS chart image tag (core/helm-charts/ovms values.yaml "2025.4") pinned to the
# index digest read 2026-10-20, and the OpenVINO model its init container pulls (the closest
# OpenVINO/ Qwen3 4B the chart accepts without an HF token; there is no OpenVINO/ Instruct-2507).
B3_TAG=2025.4@sha256:c370720007564bbdcbca13b9b714e4537068542cda512e8095259e66b617962a
# B1: the image tag of EAT's xeon-values.yaml pinned to its index digest. Default: EAT 0d3438d's
# v0.24.0 (read 2026-10-17), which every desktop row and H54a ran. The crescent wrappers pass EAT
# release-1.1.0's v0.28.0 (crescent amendment E1).
B1_TAG=${LIVE_EAT_B1_TAG:-v0.24.0@sha256:b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1}
B3_MODEL=OpenVINO/Qwen3-4B-int4-ov
[ "${MODEL_SET}" = gemma4 ] && B3_MODEL=OpenVINO/gemma-4-26b-a4b-it-int4-ov
# H53 B2: EAT PR #21's xpu-values.yaml image tag (vllm/vllm-openai-xpu v0.28.0) pinned to the
# index digest read 2026-10-20, and the placeholder that holds the cards the arms may not use
# (k3d-gpu's DEVICE_TEST_IMAGE).
B2_TAG=v0.28.0@sha256:4756b66a077627133cee653b551f6f5eaa1b9a981b5eea13edd33fcd3b0d3ca3
HOLD_IMAGE=busybox:1.37@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e
# Time a mode needs after the server answers: rollout and server-wait give up past it.
# code: per block 120 s (the least budget) + 110 s (restart, 30 s gap, warm-up), 60 s of
# teardown, ~90 s of sandbox, router, Redis and agent setup (H43 amendment W2).
RESERVE=420
case ${MODE} in
    code) read -r -a CODE_LEVELS <<< "${LIVE_CODE_LEVELS:-typical heavy heavy typical}"
          RESERVE=$(( ${#CODE_LEVELS[@]} * 230 + 150 )) ;;
    apb)  RESERVE=$(( $(tr ',' '\n' <<< "${LIVE_APB_RAMP:-1,2}" | grep -c .) * ${LIVE_APB_STEP_S:-300} + 300 )) ;;
esac
lc=$(echo "${ARM}" | tr 'A-Z' 'a-z')
PFX=k3dgpu-a
case ${MODE} in pairs|solo) PFX=k3dgpu-e ;; esac
DEVUI=127.0.0.1
# H56 slots (bench/eatflow/crescent/slots.sh sets LIVE_SLOT and LIVE_NODE_CPUS): slot i names
# everything ${PFX}-s<i>-*, moves every port by 100*i and DevUI to 127.0.0.<2i+1>, pins the
# server node, LiteLLM and RAPL containers to LIVE_NODE_CPUS and records the cpusets (cpuset.txt).
if [ -n "${LIVE_SLOT:-}" ]; then
    [[ "${LIVE_SLOT}" =~ ^[01]$ ]] || { echo "slots: LIVE_SLOT must be 0 or 1"; exit 2; }
    { [ "${MODE}" = agent ] && [ "${ARM}" = A1 ]; } || { echo "slots: LIVE_MODE=agent ARM=A1 only"; exit 2; }
    PFX=${PFX}-s${LIVE_SLOT}
    o=$(( 100 * LIVE_SLOT ))
    P_SRV=$(( P_SRV + o )) P_LLM=$(( P_LLM + o )) P_PROXY=$(( P_PROXY + o )) P_MCP=$(( P_MCP + o ))
    P_ROUTER=$(( P_ROUTER + o )) P_REDIS=$(( P_REDIS + o ))
    DEVUI=127.0.0.$(( 2 * LIVE_SLOT + 1 ))
    export LOAD_SLOT=${LIVE_SLOT}
fi
if [ "${LIVE_PRINT_SLOT:-0}" = 1 ]; then
    echo "cluster=${PFX}-${lc} ports=${P_SRV},${P_LLM},${P_PROXY},${P_MCP},${P_ROUTER},${P_REDIS} devui=${DEVUI} node_cpus=${LIVE_NODE_CPUS:-}"
    exit 0
fi
cs=()
[ -n "${LIVE_NODE_CPUS:-}" ] && cs=(--cpuset-cpus "${LIVE_NODE_CPUS}")
if [ -n "${LIVE_SLOT:-}" ]; then
    for p in "${P_SRV}" "${P_LLM}" "${P_PROXY}" "${P_MCP}" "${P_ROUTER}" "${P_REDIS}"; do
        ! ss -Hltn "sport = :${p}" | grep -q . || { echo "slots: port ${p} is in use"; exit 2; }
    done
    ! ss -Hltn "sport = :8090" | awk '{print $4}' | grep -qE "^(${DEVUI//./\\.}|0\.0\.0\.0|\*|\[::\]):8090$" \
        || { echo "slots: ${DEVUI}:8090 is in use"; exit 2; }
fi
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
    docker rm -f "${PFX}-litellm" "${PFX}-litellm-a" "${PFX}-litellm-b" "${PFX}-${lc}-srv" "${PFX}-rapl" \
        "${PFX}-redis" >/dev/null 2>&1
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
        DEVUI_HOST=${DEVUI} exec "${PY}" main.py) >> "${OUT}/agent.log" 2>&1 &
    pids+=($!)
    local end=$(( $(date +%s) + 90 ))
    until curl -fsS -o /dev/null --max-time 5 -H "Authorization: Bearer ${DEVUI_AUTH_TOKEN}" \
            "http://${DEVUI}:8090/v1/entities"; do
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
docker run -d --name "${PFX}-rapl" "${cs[@]}" --security-opt systempaths=unconfined --security-opt apparmor=unconfined \
    --read-only --network none "${RAPL_IMAGE}" sleep 7200 >/dev/null
export K3D_GPU_RAPL_EXEC="docker exec ${PFX}-rapl"

nodes() { docker ps --filter "label=k3d.cluster=${K3D_GPU_CLUSTER}" --filter "label=k3d.role=server" --format '{{.Names}}'; }
stage() {  # stage <local dir> <path under /var/apb-models> <uid>
    local n
    for n in $(nodes); do
        docker exec "${n}" mkdir -p "/var/apb-models/$2"
        docker cp -L "${M}/$1/." "${n}:/var/apb-models/$2/"
        docker exec "${n}" rm -rf "/var/apb-models/$2/.cache"
        docker exec "${n}" chown -R "$3:$3" /var/apb-models
    done
}
# cpusets: H56's cpuset.txt, "<role> <name> <cpus>" lines that crescent/h56.py checks.
cpusets() {
    local n c
    echo "expect_node ${LIVE_NODE_CPUS}"
    echo "expect_load ${LOAD_CPUS:-}"
    for n in $(nodes); do
        echo "node ${n} $(docker inspect --format '{{.HostConfig.CpusetCpus}}' "${n}")"
        echo "node_effective ${n} $(docker exec "${n}" cat /sys/fs/cgroup/cpuset.cpus.effective)"
    done
    for c in "${PFX}-litellm" "${PFX}-rapl"; do
        echo "${c##*-} ${c} $(docker inspect --format '{{.HostConfig.CpusetCpus}}' "${c}")"
    done
    for c in $(docker ps --filter "label=io.k3d-gpu.bench-load.slot=${LIVE_SLOT}" --format '{{.Names}}'); do
        echo "load ${c} $(docker inspect --format '{{.HostConfig.CpusetCpus}}' "${c}")"
    done
    echo "pod_allowed ${DEP} $(kubectl -n "${NS}" exec "deploy/${DEP}" -- sh -c 'grep Cpus_allowed_list /proc/self/status' | awk '{print $2}')"
    echo "pod_nproc ${DEP} $(kubectl -n "${NS}" exec "deploy/${DEP}" -- nproc)"
}
cluster_up() {
    stamp "k3d-gpu up"
    timeout 300 "${K}" up > "${OUT}/up.log" 2>&1 || { echo "up failed"; tail -20 "${OUT}/up.log"; exit 1; }
    if [ -n "${LIVE_NODE_CPUS:-}" ]; then
        local n
        for n in $(nodes); do
            docker update --cpuset-cpus "${LIVE_NODE_CPUS}" "${n}" >> "${OUT}/up.log" 2>&1 \
                || { echo "NOT DELIVERABLE: node cpuset ${LIVE_NODE_CPUS} not applied to ${n}"; exit 3; }
        done
        stamp "nodes pinned to ${LIVE_NODE_CPUS}"
    fi
}
if [ "${MODE}" = solo ]; then
    # H53 ("H53 pre-registration", LIVE_MODEL_SET=qwen38): one arm per hold on the Arc Pro B70s,
    # holds in the order AG, B2, B2, AG, scored as block pairs (bench/eatflow/solo.py). LIVE_SOLO:
    # AG (k3d-gpu's auto device and draft: OVMS on one card) or B2 (EAT PR #21's vLLM XPU path,
    # its xpu-qwen36-27b profile on these weights, LIVE_B2_TP cards). LIVE_GPU_FREE: the PCI
    # addresses of the cards the hold may use; every other card is held by a placeholder pod.
    PD=${LIVE_PAIRS_DIR:?} HOLD=${LIVE_HOLD:?} SOLO=${LIVE_SOLO:?LIVE_SOLO is AG or B2}
    read -r FAMILY RULE <<< "$(python3 "${HERE}/pairs.py" design "${ARM}")"
    [ "${ARM}" = H53 ] && [ "${RULE:-}" = L3 ] || { echo "solo: ARM must be H53 (a rule-L3 row)"; exit 2; }
    mkdir -p "${PD}"
    PD=$(cd "${PD}" && pwd)
    export K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true
    TP=${LIVE_B2_TP:-4}
    case ${SOLO} in AG) need=1 ;; B2) need=${TP} ;; *) echo "LIVE_SOLO must be AG or B2"; exit 2 ;; esac
    cluster_up
    # Hold every card outside LIVE_GPU_FREE with a placeholder pod (one card each), then free
    # the placeholders on the cards this hold may use.
    free=${LIVE_GPU_FREE:?H53 needs LIVE_GPU_FREE, the PCI addresses of the cards the hold may use}
    n=$(kubectl get node -o jsonpath='{.items[0].status.allocatable.gpu\.intel\.com/xe}')
    for i in $(seq 1 "${n:-0}"); do
        printf 'apiVersion: v1\nkind: Pod\nmetadata: {name: gpu-hold-%s, namespace: default, labels: {k3d-gpu.io/gpu-hold: "1"}}\nspec:\n  containers:\n  - name: hold\n    image: %s\n    command: ["sleep", "86400"]\n    resources: {limits: {gpu.intel.com/xe: 1}}\n' \
            "${i}" "${HOLD_IMAGE}" | kubectl apply -f - > /dev/null
    done
    kubectl wait --for=condition=Ready pod -l k3d-gpu.io/gpu-hold=1 -n default --timeout=180s > /dev/null 2>&1
    : > "${OUT}/gpu-hold.txt"
    for i in $(seq 1 "${n:-0}"); do
        rd=$(kubectl -n default exec "gpu-hold-${i}" -- ls /dev/dri | grep renderD | head -1)
        bdf=$(basename "$(readlink -f "/sys/class/drm/${rd}/device")")
        echo "gpu-hold-${i} ${rd} ${bdf}" >> "${OUT}/gpu-hold.txt"
        if [[ " ${free} " == *" ${bdf} "* ]]; then kubectl -n default delete pod "gpu-hold-${i}" --wait > /dev/null; fi
    done
    nfree=$(awk -v f=" ${free} " 'index(f, " " $3 " ")' "${OUT}/gpu-hold.txt" | wc -l)
    [ "${nfree}" -ge "${need}" ] || { echo "NOT DELIVERABLE: ${nfree} of the free cards (${free}) seen, ${need} needed"; cat "${OUT}/gpu-hold.txt"; exit 3; }
    if [ "${SOLO}" = AG ]; then
        stage_path "${TGT_DIR}" "${TGT}" 5000
        K3D_GPU_AGENT_DEVICE=auto K3D_GPU_AGENT_MODEL=${TGT} K3D_GPU_AGENT_MODEL_NAME=${SERVED} K3D_GPU_AGENT_DRAFT=default \
            K3D_GPU_AGENT_TOOL_PARSER=${A_TOOL} K3D_GPU_AGENT_REASONING_PARSER='' K3D_GPU_AUTOQUANT=off \
            "${K}" agent manifest > "${OUT}/agent.yaml" 2> "${OUT}/agent.err" || { cat "${OUT}/agent.err"; exit 1; }
        grep -q -- '--target_device=GPU' "${OUT}/agent.yaml" || { echo "NOT DELIVERABLE: AG is not on the GPU"; exit 3; }
        python3 "${REPO}/bench/apb/stage.py" ovms "${OUT}/agent.yaml" "${OUT}/deploy.yaml"
        NS=k3d-gpu-agent DEP=ovms SVC=ovms SPORT=8000 CTR=ovms
    else
        # B2: EAT PR #21's xpu-values.yaml with its catalog profile for this model size
        # (B2_PROFILE), the tag pinned to its digest and TP cards; the host-runtime mounts a k3d
        # node cannot satisfy are dropped and the boot ladder applied (stage.py xpu).
        chart=${LIVE_EAT_XPU_CHART:?}
        printf 'image:\n  tag: "%s"\n' "${B2_TAG}" > "${OUT}/pin.yaml"
        helm template "${B_REL}" "${chart}" --values "${chart}/xpu-values.yaml" --values "${OUT}/pin.yaml" \
            --set LLM_MODEL_ID="${B2_PROFILE:?}" --set tensor_parallel_size="${TP}" --set global.monitoring=false \
            --set svcmonitor.enabled=false --set ingress.enabled=false --set route.enabled=false > "${OUT}/eat.yaml"
        STAGE_HOST_DIR=${B_HOST} python3 "${REPO}/bench/apb/stage.py" xpu "${OUT}/eat.yaml" "${OUT}/deploy.yaml" \
            "${B_DIR}" - "ladder=${LIVE_B2_LADDER:-}"
        [ "${B_HOST}" = /var/apb-models ] && stage_path "${B_DIR}" "${B_DIR}" 1001
        NS=default DEP=${B_REL} SVC=${B_REL}-service SPORT=80 CTR=vllm
    fi
    kubectl apply -f "${OUT}/deploy.yaml" > /dev/null
    stamp wait-ready
    left=$(( CAP - $(el) - 900 ))
    if [ "${left}" -le 0 ] || ! kubectl -n "${NS}" rollout status "deploy/${DEP}" --timeout="${left}s"; then
        echo "NOT DELIVERABLE: not ready with 900 s of the hold left" | tee -a "${OUT}/live.log"
        kubectl get pods -A > "${OUT}/pods.txt" 2>&1
        kubectl -n "${NS}" logs "deploy/${DEP}" --all-containers > "${OUT}/server.log" 2>&1
        exit 3
    fi
    stamp ready
    # Every card the arm holds must be a free card, as many as it asked for.
    got=0
    for rd in $(kubectl -n "${NS}" exec "deploy/${DEP}" -c "${CTR}" -- ls /dev/dri | grep renderD); do
        bdf=$(basename "$(readlink -f "/sys/class/drm/${rd}/device")")
        echo "${DEP} ${rd} ${bdf}" | tee -a "${OUT}/gpu-arms.txt"
        [[ " ${free} " == *" ${bdf} "* ]] || { echo "ABORTED: ${DEP} holds ${bdf}, not a free card"; exit 3; }
        got=$(( got + 1 ))
    done
    [ "${got}" -eq "${need}" ] || { echo "ABORTED: ${DEP} holds ${got} card(s), not ${need}"; exit 3; }
    kubectl -n "${NS}" get pods --field-selector=status.phase=Running \
        -o jsonpath='{.items[0].metadata.uid} {.items[0].spec.containers[0].image} {.items[0].status.containerStatuses[0].imageID}' \
        > "${OUT}/image.txt"
    read -r uid _ < "${OUT}/image.txt"
    kubectl -n "${NS}" port-forward "svc/${SVC}" "${P_SRV}:${SPORT}" > "${OUT}/pf.log" 2>&1 &
    pids+=($!)
    cg=$(${AB} cgroup --uid "${uid}") || { echo "no cgroup for the pod"; exit 1; }
    wait_http "http://127.0.0.1:${P_SRV}/v1/models" 300 || { echo "NOT DELIVERABLE: server not answering"; exit 3; }
    curl -fsS "http://127.0.0.1:${P_SRV}/v1/models" > "${OUT}/models.json"
    SERVED_X=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['data'][0]['id'])" "${OUT}/models.json")
    printf 'model_list:\n  - model_name: %s\n    litellm_params:\n      model: openai/%s\n      api_base: http://127.0.0.1:%s/v1\n      api_key: not-needed\n      timeout: 600\n' \
        "${ALIAS}" "${SERVED_X}" "${P_SRV}" > "${OUT}/litellm.yaml"
    docker run -d --name "${PFX}-litellm" --network host -v "${OUT}/litellm.yaml:/app/config.yaml:ro" \
        "${LITELLM_IMAGE}" --config /app/config.yaml --port "${P_LLM}" --host 127.0.0.1 >/dev/null
    docker inspect --format '{{.Image}} {{index .Config.Image}}' "${PFX}-litellm" > "${OUT}/litellm-image.txt"
    wait_http "http://127.0.0.1:${P_LLM}/health/liveliness" 120 || { echo "litellm did not start"; exit 1; }
    start_mcp || { echo "mcp did not start"; exit 1; }
    {
        echo '#!/usr/bin/env bash'
        echo "# (Re)start the banking agent and wait for its DevUI."
        echo "[ -f '${OUT}/agent.pid' ] && kill \"\$(cat '${OUT}/agent.pid')\" 2>/dev/null"
        echo "for _ in \$(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8090) 2>/dev/null || break; sleep 0.1; done"
        echo "cd '${BP}' && (OPENAI_BASE_URL=http://127.0.0.1:${P_PROXY}/v1 OPENAI_API_KEY=not-needed MODEL_NAME='${ALIAS}' \\"
        echo "    MCP_SERVER_URL=http://127.0.0.1:${P_MCP}/mcp ENABLE_MEMORY=false AUTO_OPEN_BROWSER=false \\"
        echo "    DEVUI_HOST=127.0.0.1 exec '${PY}' main.py >> '${OUT}/agent.log' 2>&1 &"
        echo "    echo \$! > '${OUT}/agent.pid')"
        echo "for _ in \$(seq 45); do"
        echo "    curl -fsS -o /dev/null --max-time 5 -H \"Authorization: Bearer \${DEVUI_AUTH_TOKEN}\" http://127.0.0.1:8090/v1/entities && exit 0"
        echo "    sleep 2"
        echo "done"
        echo "exit 1"
    } > "${OUT}/agent.sh"
    chmod 700 "${OUT}/agent.sh"
    "${OUT}/agent.sh" || { echo "agent did not start"; tail -20 "${OUT}/agent.log"; exit 1; }
    stamp "load ${LEVEL}"
    python3 "${REPO}/bench/load/load.py" start --level "${LEVEL}" --ttl $(( CAP - $(el) + 60 )) > "${OUT}/load-start.log" 2>&1
    deadline=$(( t0 + CAP - 120 ))
    stamp "baseline ${LEVEL}"
    python3 "${REPO}/bench/load/load.py" sample --seconds 10 --windows 2 --phase baseline --tag baseline \
        --level "${LEVEL}" --out "${PD}/baseline-hold${HOLD}.jsonl"
    stamp "solo hold ${HOLD} (${SOLO}), deadline +$(( deadline - t0 ))s"
    ${AB} solo --hyp "${ARM}" --hold "${HOLD}" --arm "${SOLO}" --out "${PD}" --deadline "${deadline}" --tasks B24 \
        --agent http://127.0.0.1:8090 --upstream "http://127.0.0.1:${P_LLM}" --proxy-port "${P_PROXY}" \
        --restart "${OUT}/agent.sh" --pod "${cg}" --mcp-url "http://127.0.0.1:${P_MCP}/mcp" --reset "${OUT}/reset-mcp.sh" \
        --sampling "${SAMPLING}" --baseline "${PD}/baseline-hold${HOLD}.jsonl" --level "${LEVEL}" 2>&1 | tee -a "${OUT}/live.log"
    stamp finished
    exit 0
fi
if [ "${MODE}" = pairs ]; then
    # H47 / H49 (EXPERIMENTS.md "H47 and H49 pre-registration"): both arms live in one
    # cluster, each with its own LiteLLM, proxy (in-process in `agentbench pairs`) and
    # agent; one shared MCP sidecar; pairs A B B A over B24, one attempt in flight at a time.
    # LIVE_PAIRS_DIR: the pairs directory shared by every hold; LIVE_HOLD: this hold's number;
    # LIVE_PAIRS_GUARD: the pair-start guard, s (default 600; a smoke sets it past the hold).
    # H47b / H49b: the same designs under load rule L2 ("H47b and H49b pre-registration"): a
    # level baseline once per hold, then a settle gap and a pre-window before every attempt.
    # H50 ("H50-H52 pre-registration"): H49b's design and rule on the Xeon 6741P. H50o: the same
    # with B3, EAT's own OVMS chart (core/helm-charts/ovms) at its stock values, as arm B.
    PD=${LIVE_PAIRS_DIR:?} HOLD=${LIVE_HOLD:?}
    # The load rule comes from bench/eatflow/pairs.py DESIGNS, the only table of it.
    read -r FAMILY RULE <<< "$(python3 "${HERE}/pairs.py" design "${ARM}")"
    [ -n "${RULE:-}" ] || { echo "pairs: ${ARM} is not a pairs hypothesis"; exit 2; }
    # What to deploy: the family's arms, or the row's own B arm (H50o: B3).
    DESIGN=${FAMILY}
    [ "${ARM}" = H50o ] && DESIGN=H50o
    [ "${ARM}" = H50 ] && KV_GIB=${LIVE_EAT_KV_GIB:-40}   # EAT's shipped KV cache (40 GiB)
    mkdir -p "${PD}"
    PD=$(cd "${PD}" && pwd)
    case ${DESIGN} in
        H47) A=A1 B=A4 ;;
        H49) A=A1 B=B1; export K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true ;;
        H50o) A=A1 B=B3 ;;
    esac
    # The B arm's server (every design but H47's one pod): namespace, Deployment, Service,
    # service port and OpenAI path.
    B_NS=default B_DEP=${B_REL} B_SVC=${B_REL}-service B_SPORT=80 B_API=/v1
    [ "${DESIGN}" = H50o ] && B_DEP=ovms-model-server B_SVC=ovms-model-server B_SPORT=8080 B_API=/v3
    DRAFT_DIR=OpenVINO-Qwen3-0.6B-int4-ov     # where OVMS --pull puts the draft, inside the target dir
    stage_path() {  # stage_path <local dir> <path under /var/apb-models> <uid>: chown that path only
        local n
        for n in $(nodes); do
            docker exec "${n}" mkdir -p "/var/apb-models/$2" /var/apb-models/.ov_cache
            docker cp -L "${M}/$1/." "${n}:/var/apb-models/$2/"
            docker exec "${n}" rm -rf "/var/apb-models/$2/.cache" "/var/apb-models/$2/.git"
            docker exec "${n}" chown -R "$3:$3" "/var/apb-models/$2"
            docker exec "${n}" chown 5000:5000 /var/apb-models/.ov_cache
            docker exec "${n}" chmod 1777 /var/apb-models
        done
    }
    cluster_up
    stage_path "${TGT_DIR}" "${TGT}" 5000
    [ "${A_DRAFT}" = off ] || stage_path Qwen3-0.6B-int4-ov "${TGT}/${DRAFT_DIR}" 5000
    draft=${A_DRAFT} dev=CPU
    [ "${DESIGN}" = H47 ] && draft=off
    # A1 is the CPU stack: on a host with an XMX Intel GPU (crescent's B70s) the auto device
    # would pick the GPU, so the device is named (on the i9-13900T, auto chose CPU).
    K3D_GPU_AGENT_DEVICE=${dev} K3D_GPU_AGENT_MODEL=${TGT} K3D_GPU_AGENT_MODEL_NAME=${SERVED} K3D_GPU_AGENT_DRAFT=${draft} \
        K3D_GPU_AGENT_TOOL_PARSER=${A_TOOL} K3D_GPU_AGENT_REASONING_PARSER='' K3D_GPU_AUTOQUANT=off \
        "${K}" agent manifest > "${OUT}/agent.yaml" 2> "${OUT}/agent.err" || { cat "${OUT}/agent.err"; exit 1; }
    SERVED_A=${SERVED}
    if [ "${DESIGN}" = H47 ]; then
        # One OVMS pod, two graph names: A1 (no draft) and A4 (CPU Fast Draft), the graphs
        # OVMS 2026.4.1 --pull writes for those settings (bench/eatflow/h47/).
        SERVED_B=${SERVED}-a4
        python3 "${REPO}/bench/apb/stage.py" ovms2 "${OUT}/agent.yaml" "${OUT}/deploy-a.yaml" "${TGT}" \
            "${SERVED_A}=${HERE}/h47/graph-a1.pbtxt" "${SERVED_B}=${HERE}/h47/graph-a4.pbtxt"
    elif [ "${DESIGN}" = H50o ]; then
        # B3: EAT's OVMS chart as shipped (OpenVINO model pulled by its init container, its
        # PVC, 12-CPU requests and limits), with the image tag pinned to its digest and the
        # routes that need EAT's ingress and APISIX turned off.
        python3 "${REPO}/bench/apb/stage.py" ovms "${OUT}/agent.yaml" "${OUT}/deploy-a.yaml"
        helm template ovms-model-server "${LIVE_EAT_OVMS_CHART:?}" --set modelSource="${B3_MODEL}" \
            --set image.tag="${B3_TAG}" --set ingress.enabled=false --set apisixRoute.enabled=false \
            > "${OUT}/deploy-b.yaml"
        kubectl apply -f "${OUT}/deploy-b.yaml" > /dev/null
    else
        python3 "${REPO}/bench/apb/stage.py" ovms "${OUT}/agent.yaml" "${OUT}/deploy-a.yaml"
        chart=${LIVE_EAT_CHART:?}
        printf 'image:\n  tag: "%s"\n' "${B1_TAG}" > "${OUT}/pin.yaml"
        python3 "${REPO}/bench/apb/stage.py" values "${chart}/xeon-values.yaml" "${OUT}/pin.yaml" "${KV_GIB}" "${OUT}/values.yaml"
        helm template "${B_REL}" "${chart}" --values "${OUT}/values.yaml" \
            --set LLM_MODEL_ID=${ALIAS} --set chatTemplateConfigMap.enabled=${B_CHAT} --set global.monitoring=false \
            --set svcmonitor.enabled=false --set ingress.enabled=false --set route.enabled=false > "${OUT}/eat.yaml"
        STAGE_HOST_DIR=${B_HOST} python3 "${REPO}/bench/apb/stage.py" vllm "${OUT}/eat.yaml" "${OUT}/deploy-b.yaml" "${B_DIR}" 8192
        [ "${B_HOST}" = /var/apb-models ] && stage_path "${B_DIR}" "${B_DIR}" 1001
        kubectl apply -f "${OUT}/deploy-b.yaml" > /dev/null
    fi
    kubectl apply -f "${OUT}/deploy-a.yaml" > /dev/null
    stamp wait-ready
    ready() {  # ready NS DEP: rolled out with at least 900 s of the hold left
        local left=$(( CAP - $(el) - 900 ))
        [ "${left}" -gt 0 ] && kubectl -n "$1" rollout status "deploy/$2" --timeout="${left}s"
    }
    if ! ready k3d-gpu-agent ovms || { [ "${DESIGN}" != H47 ] && ! ready "${B_NS}" "${B_DEP}"; }; then
        echo "NOT DELIVERABLE: not ready with 900 s of the hold left" | tee -a "${OUT}/live.log"
        kubectl get pods -A > "${OUT}/pods.txt" 2>&1
        kubectl -n k3d-gpu-agent logs deploy/ovms --all-containers > "${OUT}/server-a.log" 2>&1
        [ "${DESIGN}" != H47 ] && kubectl -n "${B_NS}" logs "deploy/${B_DEP}" --all-containers > "${OUT}/server-b.log" 2>&1
        exit 3
    fi
    stamp ready
    # The CPU ISA each server sees (amx_tile lines in its /proc/cpuinfo) and the B server's
    # log lines naming its CPU backend (H50: B1 is meant to run with AMX).
    kubectl -n k3d-gpu-agent exec deploy/ovms -c ovms -- grep -c amx_tile /proc/cpuinfo > "${OUT}/isa-a.txt" 2>&1
    if [ "${DESIGN}" != H47 ]; then
        kubectl -n "${B_NS}" exec "deploy/${B_DEP}" -- grep -c amx_tile /proc/cpuinfo > "${OUT}/isa-b.txt" 2>&1
        kubectl -n "${B_NS}" logs "deploy/${B_DEP}" 2>&1 | grep -iE 'amx|avx512|isa|cpu (backend|platform)|onednn' \
            | head -40 > "${OUT}/isa-b-log.txt"
    fi
    # LIVE_MOUNT_B (crescent amendment M1): B1 serves its weights from the read-only bind
    # (/cache in its pod), so a write there must fail with EROFS; record the host disk.
    df -P / > "${OUT}/df.txt"
    if [ "${B_HOST}" = /var/apb-ro ] && [ "${DESIGN}" != H47 ] && [ "${DESIGN}" != H50o ]; then
        # Where the pod sees the bind: the mountPath of the volume EAT's chart calls model-volume
        # (/data in core/helm-charts/vllm), read from the deployment as it runs.
        mnt=$(kubectl -n "${B_NS}" get "deploy/${B_DEP}" \
            -o jsonpath='{.spec.template.spec.containers[0].volumeMounts[?(@.name=="model-volume")].mountPath}')
        echo "model-volume mountPath: ${mnt:-none}" > "${OUT}/ro-mount.txt"
        [ -n "${mnt}" ] || { echo "NOT DELIVERABLE: no model-volume mount in the B pod" | tee -a "${OUT}/live.log"; exit 3; }
        if kubectl -n "${B_NS}" exec "deploy/${B_DEP}" -- touch "${mnt}/${B_DIR}/.k3dgpu-rw-probe" \
            > "${OUT}/ro-probe.txt" 2>&1; then
            echo "NOT DELIVERABLE: ${B_DIR} is writable in the B pod" | tee -a "${OUT}/live.log"
            exit 3
        fi
        grep -q 'Read-only file system' "${OUT}/ro-probe.txt" \
            || { echo "NOT DELIVERABLE: the read-only probe failed for another reason" | tee -a "${OUT}/live.log"; exit 3; }
    fi
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
    if [ "${DESIGN}" != H47 ]; then
        podinfo "${B_NS}" > "${OUT}/image-b.txt"
        read -r uid_b _ < "${OUT}/image-b.txt"
        BPORT=$(( P_SRV + 1 ))
        kubectl -n "${B_NS}" port-forward "svc/${B_SVC}" "${BPORT}:${B_SPORT}" > "${OUT}/pf-b.log" 2>&1 &
        pids+=($!)
        cg_b=$(${AB} cgroup --uid "${uid_b}") || { echo "no cgroup for the B pod"; exit 1; }
    fi
    echo "pod cgroups: ${A} ${cg_a}; ${B} ${cg_b}" | tee -a "${OUT}/live.log"
    grep -h usage_usec "${cg_a}/cpu.stat" "${cg_b}/cpu.stat" | tee -a "${OUT}/live.log" | grep -q . \
        || { echo "pod cpu.stat unreadable"; exit 1; }
    wait_http "http://127.0.0.1:${P_SRV}/v1/models" 120 || { echo "A server not answering"; exit 3; }
    curl -fsS "http://127.0.0.1:${P_SRV}/v1/models" > "${OUT}/models-a.json"
    API_B=/v1
    if [ "${DESIGN}" != H47 ]; then
        API_B=${B_API}
        wait_http "http://127.0.0.1:${BPORT}${API_B}/models" 300 || { echo "B server not answering"; exit 3; }
        curl -fsS "http://127.0.0.1:${BPORT}${API_B}/models" > "${OUT}/models-b.json"
        SERVED_B=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['data'][0]['id'])" "${OUT}/models-b.json")
    fi
    litellm() {  # litellm x served port llm_port api_path
        printf 'model_list:\n  - model_name: %s\n    litellm_params:\n      model: openai/%s\n      api_base: http://127.0.0.1:%s%s\n      api_key: not-needed\n      timeout: 600\n' \
            "${ALIAS}" "$2" "$3" "$5" > "${OUT}/litellm-$1.yaml"
        docker run -d --name "${PFX}-litellm-$1" --network host -v "${OUT}/litellm-$1.yaml:/app/config.yaml:ro" \
            "${LITELLM_IMAGE}" --config /app/config.yaml --port "$4" --host 127.0.0.1 >/dev/null
        docker inspect --format '{{.Image}} {{index .Config.Image}}' "${PFX}-litellm-$1" > "${OUT}/litellm-image-$1.txt"
        wait_http "http://127.0.0.1:$4/health/liveliness" 120 || { echo "litellm $1 did not start"; exit 1; }
    }
    litellm a "${SERVED_A}" "${P_SRV}" "${P_LLM}" /v1
    litellm b "${SERVED_B}" "${BPORT}" "$(( P_LLM + 1 ))" "${API_B}"
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
    [ "${DESIGN}" = H47 ] && idle=(--idle-s 3)
    l2=()
    if [ "${RULE}" != L1 ]; then
        # Rules L2 and L3: the level baseline (2 x 10 s, both arms idle, before the warm-ups).
        stamp "baseline ${LEVEL}"
        python3 "${REPO}/bench/load/load.py" sample --seconds 10 --windows 2 --phase baseline --tag baseline \
            --level "${LEVEL}" --out "${PD}/baseline-hold${HOLD}.jsonl"
        l2=(--baseline "${PD}/baseline-hold${HOLD}.jsonl" --level "${LEVEL}")
    fi
    stamp "pairs hold ${HOLD}, deadline +$(( deadline - t0 ))s"
    ${AB} pairs --hyp "${ARM}" --hold "${HOLD}" --out "${PD}" --deadline "${deadline}" --tasks B24 \
        --arm-a "${A}" --agent-a http://127.0.0.1:8090 --upstream-a "http://127.0.0.1:${P_LLM}" \
        --proxy-port-a "${P_PROXY}" --restart-a "${OUT}/agent-a.sh" --pod-a "${cg_a}" \
        --arm-b "${B}" --agent-b http://127.0.0.2:8090 --upstream-b "http://127.0.0.1:$(( P_LLM + 1 ))" \
        --proxy-port-b "$(( P_PROXY + 1 ))" --restart-b "${OUT}/agent-b.sh" --pod-b "${cg_b}" \
        --mcp-url "http://127.0.0.1:${P_MCP}/mcp" --reset "${OUT}/reset-mcp.sh" --sampling "${SAMPLING}" \
        --guard-s "${LIVE_PAIRS_GUARD:-600}" "${idle[@]}" "${l2[@]}" 2>&1 | tee -a "${OUT}/live.log"
    stamp finished
    exit 0
fi
gpu_flag=()
case ${ARM} in
    A1|A2)
        cluster_up
        # A1 is the CPU stack wherever the auto device would pick an XMX GPU (crescent); A2 the GPU.
        export K3D_GPU_AGENT_DEVICE=CPU
        [ "${ARM}" = A2 ] && export K3D_GPU_AGENT_DEVICE=GPU
        K3D_GPU_AGENT_MODEL=${TGT} K3D_GPU_AGENT_MODEL_NAME=${SERVED} \
            K3D_GPU_AGENT_TOOL_PARSER=${A_TOOL} K3D_GPU_AGENT_REASONING_PARSER='' K3D_GPU_AUTOQUANT=off \
            "${K}" agent manifest > "${OUT}/agent.yaml" 2> "${OUT}/agent.err" || { cat "${OUT}/agent.err"; exit 1; }
        python3 "${REPO}/bench/apb/stage.py" ovms "${OUT}/agent.yaml" "${OUT}/deploy.yaml"
        stage "${TGT_DIR}" "${TGT}" 5000
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
        # sized by LIVE_EAT_KV_GIB (6 GiB by default, to fit the 125 GiB desktop); no k3d-gpu balloon overlay, stock max_model_len 8192.
        printf 'image:\n  tag: "%s"\n' "${B1_TAG}" > "${OUT}/pin.yaml"
        python3 "${REPO}/bench/apb/stage.py" values "${chart}/xeon-values.yaml" "${OUT}/pin.yaml" "${KV_GIB}" "${OUT}/values.yaml"
        helm template "${B_REL}" "${chart}" --values "${OUT}/values.yaml" \
            --set LLM_MODEL_ID=${ALIAS} --set chatTemplateConfigMap.enabled=${B_CHAT} --set global.monitoring=false \
            --set svcmonitor.enabled=false --set ingress.enabled=false --set route.enabled=false > "${OUT}/eat.yaml"
        STAGE_HOST_DIR=${B_HOST} python3 "${REPO}/bench/apb/stage.py" vllm "${OUT}/eat.yaml" "${OUT}/deploy.yaml" "${B_DIR}" 8192
        [ "${B_HOST}" = /var/apb-models ] && stage "${B_DIR}" "${B_DIR}" 1001
        NS=default DEP=${B_REL} SVC=${B_REL}-service SPORT=80 ;;
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
    left=$(( CAP - $(el) - RESERVE ))
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
sw=600
case ${MODE} in code|apb) sw=$(( CAP - $(el) - RESERVE )); [ "${sw}" -le 600 ] || sw=600 ;; esac
case ${ARM} in
    C1) wait_http "http://127.0.0.1:${P_SRV}/api/version" 120 && \
            curl -fsS "http://127.0.0.1:${P_SRV}/api/tags" > "${OUT}/ollama-tags.json" ;;
    *) [ "${sw}" -gt 0 ] && wait_http "http://127.0.0.1:${P_SRV}/v1/models" "${sw}" ;;
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
docker run -d --name "${PFX}-litellm" "${cs[@]}" --network host -v "${OUT}/litellm.yaml:/app/config.yaml:ro" \
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
if [ "${MODE}" = code ]; then
    # H43 (amendment W1): `k3d-gpu sandbox up` (agent-sandbox v1.0.7, warm pool python-ecores on
    # the E-cores), the router the blueprint's SDK calls (bench/eatflow/sbrouter.py), Redis for
    # its memory (purged before every task), and the coding agent from the pinned blueprint
    # commit with k8s-agent-sandbox 1.0.7 behind the shim in bench/eatflow/coding/.
    CDIR=${LIVE_CODE_DIR:-${OUT}}
    mkdir -p "${CDIR}"
    CDIR=$(cd "${CDIR}" && pwd)
    stamp "sandbox up"
    K3D_GPU_SHARE="${REPO}/share" timeout 300 "${K}" sandbox up > "${OUT}/sandbox-up.log" 2>&1 \
        || { echo "NOT DELIVERABLE: sandbox up failed"; tail -20 "${OUT}/sandbox-up.log"; exit 3; }
    rtok=$(python3 -c "import secrets; print(secrets.token_hex(16))")
    printf 'token=%s\n' "${rtok}" > "${OUT}/router.env"
    chmod 600 "${OUT}/router.env"
    kubectl -n agent-sandbox-system create secret generic sandbox-router --from-env-file="${OUT}/router.env" >/dev/null
    python3 "${HERE}/sbrouter.py" manifest > "${OUT}/router.yaml"
    kubectl apply -f "${OUT}/router.yaml" >/dev/null
    kubectl -n agent-sandbox-system rollout status deploy/sandbox-router --timeout=120s >/dev/null \
        || { echo "NOT DELIVERABLE: sandbox router not ready"; exit 3; }
    kubectl -n agent-sandbox-system port-forward svc/sandbox-router-svc "${P_ROUTER}:8080" > "${OUT}/pf-router.log" 2>&1 &
    pids+=($!)
    wait_http "http://127.0.0.1:${P_ROUTER}/healthz" 60 || { echo "router port-forward failed"; exit 1; }
    docker run -d --name "${PFX}-redis" -p "127.0.0.1:${P_REDIS}:6379" "${REDIS_IMAGE}" >/dev/null
    docker inspect --format '{{.Image}} {{index .Config.Image}}' "${PFX}-redis" > "${OUT}/redis-image.txt"
    # Memory purge (--pre-task): delete every key, then read back none. The search index is
    # not a key, so it stays; the agent's RedisContextProvider then finds no earlier turn.
    cat > "${OUT}/purge-mem.sh" <<EOF
#!/usr/bin/env bash
docker exec ${PFX}-redis sh -c 'redis-cli --scan | xargs -r redis-cli del >/dev/null' || exit 1
[ "\$(docker exec ${PFX}-redis redis-cli dbsize)" = 0 ]
EOF
    chmod +x "${OUT}/purge-mem.sh"
    for _ in $(seq 30); do docker exec "${PFX}-redis" redis-cli ping 2>/dev/null | grep -q PONG && break; sleep 1; done
    CBP=${LIVE_BLUEPRINT}/usecases/coding-agent-maf/src
    "${LIVE_CODE_PY:?}" -c "import k8s_agent_sandbox, importlib.metadata as m; print('k8s-agent-sandbox', m.version('k8s-agent-sandbox'))" \
        | tee -a "${OUT}/live.log"
    # (Re)start the agent: kill the last one (its censored task would keep running and write
    # memory), release its sandbox claims, start a fresh process, wait for its DevUI.
    code_agent() {
        [ -f "${OUT}/agent-code.pid" ] && kill "$(cat "${OUT}/agent-code.pid")" 2>/dev/null
        for _ in $(seq 100); do (exec 3<>/dev/tcp/127.0.0.1/8090) 2>/dev/null || break; sleep 0.1; done
        kubectl -n agent-sandbox delete sandboxclaims --all --wait=false >/dev/null 2>&1
        (cd "${CBP}" && PYTHONPATH="${HERE}/coding" \
            OPENAI_BASE_URL="http://127.0.0.1:${P_PROXY}/v1" OPENAI_API_KEY=not-needed MODEL_NAME="${ALIAS}" \
            REDIS_URL="redis://127.0.0.1:${P_REDIS}" SANDBOX_ROUTER_URL="http://127.0.0.1:${P_ROUTER}" \
            SANDBOX_ROUTER_TOKEN="${rtok}" SANDBOX_NAMESPACE=agent-sandbox SANDBOX_TEMPLATE=python-ecores \
            SANDBOX_WARMPOOL=python-ecores WORKSPACE_DIR="${OUT}/workspace" AUTO_OPEN_BROWSER=false \
            DEVUI_HOST=127.0.0.1 exec "${LIVE_CODE_PY}" main.py) >> "${OUT}/agent.log" 2>&1 &
        echo $! > "${OUT}/agent-code.pid"
        pids+=($!)
        local end=$(( $(date +%s) + 90 ))
        until curl -fsS -o /dev/null --max-time 5 -H "Authorization: Bearer ${DEVUI_AUTH_TOKEN}" \
                "http://127.0.0.1:8090/v1/entities"; do
            [ "$(date +%s)" -lt "${end}" ] || { echo "coding agent did not start"; tail -20 "${OUT}/agent.log"; return 1; }
            sleep 2
        done
    }
    nb=${#CODE_LEVELS[@]}
    # Per block: agent restart (~10 s) and a 30 s gap after the level's load starts.
    blk=$(( (CAP - $(el) - 60) / nb - 110 ))
    [ "${blk}" -le 300 ] || blk=300
    [ "${blk}" -ge 120 ] || { echo "NOT DELIVERABLE: ${blk} s per H43 block"; exit 3; }
    i=0
    for lv in "${CODE_LEVELS[@]}"; do
        i=$(( i + 1 ))
        python3 "${REPO}/bench/load/load.py" stop >/dev/null 2>&1
        code_agent || exit 1
        [ "${lv}" = none ] || python3 "${REPO}/bench/load/load.py" start --level "${lv}" --ttl $(( CAP + 60 )) \
            >> "${OUT}/load-start.log" 2>&1
        sleep 30
        stamp "h43 block ${i} ${lv}, budget ${blk}"
        ${AB} run --arm "${ARM}" --level "${lv}" --out "${CDIR}/h43-${lc}-${lv}" --agent-url http://127.0.0.1:8090 \
            --tasks C12 --upstream "http://127.0.0.1:${P_LLM}" --proxy-port "${P_PROXY}" --sampling "${SAMPLING}" \
            --pre-task "${OUT}/purge-mem.sh" --load-phase during --budget-s "${blk}" "${gpu_flag[@]}" 2>&1 \
            | tail -3 | tee -a "${OUT}/live.log"
        rc=${PIPESTATUS[0]}
        [ "${rc}" = 0 ] || { echo "ABORTED: H43 block ${i} exited ${rc}; the hold stops" | tee -a "${OUT}/live.log"; exit 1; }
    done
    kubectl -n agent-sandbox get sandboxes,sandboxclaims -o wide > "${OUT}/sandboxes.txt" 2>&1
    K3D_GPU_SHARE="${REPO}/share" "${K}" sandbox status >> "${OUT}/sandboxes.txt" 2>&1
    stamp finished
    exit 0
fi
start_mcp || { echo "mcp did not start"; exit 1; }
start_agent "${ALIAS}" || { echo "agent did not start"; tail -20 "${OUT}/agent.log"; exit 1; }

if [ "${MODE}" = apb ]; then
    # H45 (amendment W1): this hold's ramp steps on B15-RO at LEVEL, amendment-27 windows: the
    # level baseline (2 x 10 s), a settle gap whose last window is the arm's pre-window, then
    # "during" windows over the steps. Each step is 300 s; the first 30 s are not scored.
    ramp=${LIVE_APB_RAMP:-1,2}
    top=${LIVE_APB_TOP:-8}
    [[ "${top}" =~ ^[0-9]+$ ]] || { echo "LIVE_APB_TOP must be a number, got '${top}'"; exit 2; }
    D=${OUT}/apb
    mkdir -p "${D}"
    base=()
    [ -n "${LIVE_APB_BASE:-}" ] && base=(--base-from "${LIVE_APB_BASE}")
    nsteps=$(tr ',' '\n' <<< "${ramp}" | grep -c .)
    stamp "load ${LEVEL}"
    python3 "${REPO}/bench/load/load.py" start --level "${LEVEL}" --ttl $(( CAP - $(el) + 60 )) > "${OUT}/load-start.log" 2>&1
    python3 "${REPO}/bench/load/load.py" sample --seconds 10 --windows 2 --phase baseline --tag baseline \
        --level "${LEVEL}" --out "${D}/baseline.jsonl"
    python3 "${REPO}/bench/load/load.py" settle --baseline "${D}/baseline.jsonl" --tag "${ARM}" --level "${LEVEL}" \
        --out "${D}/load.jsonl"
    left=$(( CAP - $(el) - 150 - nsteps * ${LIVE_APB_STEP_S:-300} ))
    [ "${left}" -ge 0 ] || { echo "NOT DELIVERABLE: ${left} s short for ${nsteps} step(s)"; exit 3; }
    stamp "apb ramp ${ramp}"
    ${AB} apb --arm "${ARM}" --level "${LEVEL}" --out "${D}" --agent-url http://127.0.0.1:8090 \
        --upstream "http://127.0.0.1:${P_LLM}" --proxy-port "${P_PROXY}" --sampling "${SAMPLING}" --step-s "${LIVE_APB_STEP_S:-300}" \
        --ramp "${ramp}" "${base[@]}" --load-phase during "${gpu_flag[@]}" 2>&1 | tail -6 | tee -a "${OUT}/live.log"
    rc=${PIPESTATUS[0]}
    [ "${rc}" = 0 ] || { echo "ABORTED: apb exited ${rc}" | tee -a "${OUT}/live.log"; exit 1; }
    # The synthetic APB, reported beside the graded one and never scored: on the arm's last
    # hold (a step failed, or N=LIVE_APB_TOP ran: default 8, H51 16), with what the hold leaves (at most 3 min, at least 1).
    last=$(python3 -c "import json,sys; s=json.load(open(sys.argv[1]))['steps'][-1]; print(int(not s['pass'] or s['n'] == ${top}))" \
        "${D}/apb.json" 2>/dev/null || echo 0)
    cmin=$(( (CAP - $(el) - 150) / 60 ))
    [ "${cmin}" -le 3 ] || cmin=3
    if [ "${last}" = 1 ] && [ "${cmin}" -ge 1 ]; then
        stamp "capacity --slo 10, budget ${cmin} min"
        timeout $(( cmin * 60 + 60 )) "${K}" capacity --slo 10 --url "http://127.0.0.1:${P_SRV}/v1/chat/completions" \
            --model "${SERVED}" --budget "${cmin}" --force --fresh --state-dir "${OUT}/capacity-state" \
            --out "${OUT}/capacity" > "${OUT}/capacity.log" 2>&1
        tail -3 "${OUT}/capacity.log" | tee -a "${OUT}/live.log"
    fi
    stamp finished
    exit 0
fi

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
            --sampling "${SAMPLING}" --mcp-url "http://127.0.0.1:${P_MCP}/mcp" --reset "${OUT}/reset-mcp.sh" \
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
[ -z "${LIVE_SLOT:-}" ] || cpusets > "${OUT}/cpuset.txt"
stamp "run budget ${budget}"
${AB} run --arm "${ARM}" --level "${LEVEL}" --out "${OUT}/run" --agent-url "http://${DEVUI}:8090" --tasks B24 \
    --upstream "http://127.0.0.1:${P_LLM}" --proxy-port "${P_PROXY}" --sampling "${SAMPLING}" \
    --mcp-url "http://127.0.0.1:${P_MCP}/mcp" --reset "${OUT}/reset-mcp.sh" --budget-s "${budget}" \
    "${gpu_flag[@]}" 2>&1 | tail -5 | tee -a "${OUT}/live.log"
[ -z "${LIVE_SLOT:-}" ] || echo "load_end - $(docker ps -q --filter "label=io.k3d-gpu.bench-load.slot=${LIVE_SLOT}" \
    --filter status=running | wc -l)" >> "${OUT}/cpuset.txt"
stamp "control 120 s (server idle, same load)"
${AB} control --arm "${ARM}" --level "${LEVEL}" --out "${OUT}/run" --seconds 120 "${gpu_flag[@]}" 2>&1 \
    | tail -2 | tee -a "${OUT}/live.log"
stamp finished
