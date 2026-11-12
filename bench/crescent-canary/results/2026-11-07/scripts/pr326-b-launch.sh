# Piped to `ssh crescent bash -s -- STAMP`. Second hold of the PR #326 hardware canary, from the
# detached checkout of autobump/vllm-xpu at 38abe6d8. The four B70s are free (the owner-approved
# takeover stopped the serving pairs; this hold neither stops nor starts them). One cluster:
#   A  main's image (vllm/vllm-openai-xpu:v0.32.0@sha256:20509fab...), the manifest as rendered
#   B  the PR's image (v0.33.0@sha256:be3a9aea...), with the Deployment patched to
#      enableServiceLinks: false as soon as `agent up` creates it
# Only run records are written to crescent (runs/canary-vllm-xpu/STAMP).
set -euo pipefail
STAMP=$1
B=${HOME}/k3dgpu-bench
CO=${B}/k3d-gpu-canary-vllm-xpu
OUT=${B}/runs/canary-vllm-xpu/${STAMP}
[ "$(git -C "${CO}" rev-parse HEAD)" = 38abe6d85b93cdd4cbc1b026801639621387232c ] || { echo "checkout not at 38abe6d8"; exit 3; }
[ -z "$(git -C "${CO}" status --porcelain)" ] || { echo "checkout is not clean"; exit 3; }
mkdir -p "${OUT}"
read -r -d '' DRIVER <<'EOF' || true
set -uo pipefail
CO=$1 OUT=$2
B=${HOME}/k3dgpu-bench
CL=k3dgpu-canary-vllm-xpu
K=${CO}/scripts/k3d-gpu
LOCK=${HOME}/k3dgpu-bench.lock
HOLD_OFF=${B}/runs/canary-vllm-xpu/hold-off
MAIN_IMG=vllm/vllm-openai-xpu:v0.32.0@sha256:20509fabe50dda62c09c0786d1a6effa299d3dbdddcc49331b93e65144652a8c
WORK_END=960     # s after the lock: the steps end by here, then teardown
CAP=1170         # s after the lock: TERM if the steps are still running
PROM=http://127.0.0.1:9090
TO=$(command -v gnutimeout || command -v timeout)
NS=k3d-gpu-agent
PROXY=/api/v1/namespaces/${NS}/services/vllm:http/proxy
export PATH=${B}/bin:${PATH} KUBECONFIG=${OUT}/kubeconfig XDG_STATE_HOME=${OUT}/state K3D_GPU_CLUSTER=${CL}
ev() { echo "$(date -u +%FT%TZ) $*" | tee -a "${OUT}/events.log"; }
left() { echo $(( T0 + WORK_END - $(date +%s) )); }
vram() { curl -s -G -m 10 "${PROM}/api/v1/query" --data-urlencode 'query=xe_vram_used_bytes' \
    | python3 -c 'import json,sys; [print(r["metric"]["bdf"], r["metric"]["card"], "%.2f" % (float(r["value"][1]) / 2**30)) for r in json.load(sys.stdin)["data"]["result"]]'; }
CH=""
bg() { "$@" 9>&- & CH=$!; wait "${CH}"; local r=$?; CH=""; return "${r}"; }
priority_waiter() {
    local p q cmd me
    [ ! -e "${HOLD_OFF}" ] || { echo "hold-off file ${HOLD_OFF}"; return 0; }
    me=$(ps -o sid= -p $$ | tr -d ' ')
    for p in $(fuser "${LOCK}" 2>/dev/null); do
        [ "$(ps -o sid= -p "${p}" 2>/dev/null | tr -d ' ')" != "${me}" ] || continue
        q=$(ps -o ppid= -p "${p}" 2>/dev/null | tr -d ' ')
        cmd="$(tr '\0' ' ' < "/proc/${p}/cmdline" 2>/dev/null) $(tr '\0' ' ' < "/proc/${q:-0}/cmdline" 2>/dev/null)"
        if grep -qiE 'h62|eat2|k3d-gpu-f5|/f5/|eas-' <<< "${cmd}"; then echo "${p}: ${cmd:0:200}"; return 0; fi
    done
    return 1
}
logs_all() { kubectl -n "${NS}" logs deploy/vllm --tail=-1 2>/dev/null; kubectl -n "${NS}" logs deploy/vllm --previous --tail=-1 2>/dev/null; }
records() {  # records PHASE: pods, deployment, events, logs
    kubectl get pods -A -o wide > "${OUT}/$1-pods.txt" 2>&1
    kubectl -n "${NS}" get deploy vllm -o yaml > "${OUT}/$1-deploy-vllm.yaml" 2>&1
    kubectl -n "${NS}" describe pods > "${OUT}/$1-describe-pods.txt" 2>&1
    kubectl get events -A --sort-by=.lastTimestamp > "${OUT}/$1-events-k8s.txt" 2>&1
    kubectl -n "${NS}" logs deploy/vllm --tail=-1 > "${OUT}/$1-vllm.log" 2>&1
    kubectl -n "${NS}" logs deploy/vllm --previous --tail=-1 > "${OUT}/$1-vllm-previous.log" 2>&1
}
echo $$ > "${OUT}/driver.pid"
git -C "${CO}" rev-parse HEAD > "${OUT}/commit.txt"
exec 9> "${LOCK}"
ev "waiting for the lock"
deadline=$(( $(date +%s) + 7200 ))
while :; do
    w=$(( deadline - $(date +%s) ))
    [ "${w}" -gt 0 ] && flock -w "${w}" 9 || { ev "lock not taken in 7200 s: skipped"; echo skipped > "${OUT}/result"; exit 4; }
    if pw=$(priority_waiter); then ev "priority user queued (${pw}): released, waiting again"; flock -u 9; sleep 90; continue; fi
    break
done
T0=$(date +%s)
ev "lock taken"
SAMPLER="" WD=""
cleanup() {
    local rc=$? g p
    trap - EXIT TERM INT HUP
    [ -z "${WD}" ] || { kill $(ps -o pid= --ppid "${WD}") "${WD}" 2>/dev/null; }
    [ -z "${CH}" ] || { kill -TERM "${CH}" 2>/dev/null; wait "${CH}" 2>/dev/null; }
    ev "teardown (rc ${rc})"
    "${TO}" -k 15 45 "${K}" agent down > "${OUT}/agent-down.txt" 2>&1 9>&-; ev "agent down rc=$?"
    "${TO}" -k 15 90 "${K}" down > "${OUT}/down.txt" 2>&1 9>&-; ev "down rc=$?"
    if k3d cluster get "${CL}" > /dev/null 2>&1; then "${TO}" -k 15 60 k3d cluster delete "${CL}" > "${OUT}/cluster-delete.txt" 2>&1 9>&-; ev "k3d cluster delete rc=$?"; fi
    k3d cluster list > "${OUT}/clusters-after.txt" 2>&1
    docker ps -a --format '{{.Names}}' | grep -E "^k3d-${CL}-" > "${OUT}/containers-after.txt"
    for g in $(find "${OUT}/state" -name 'agent-guard.pid' 2>/dev/null); do
        p=$(cat "${g}"); if kill -0 "${p}" 2>/dev/null; then ev "agent guard ${p} still running"; else ev "agent guard ${p} gone"; fi
    done
    [ -z "${SAMPLER}" ] || kill "${SAMPLER}" 2>/dev/null
    vram > "${OUT}/vram-after.txt" 2>&1
    ev "vram after: $(tr '\n' ';' < "${OUT}/vram-after.txt")"
    ev "lock released"
    flock -u 9
    exec 9>&-
    ev "driver done"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP
( sleep "${CAP}"; ev "cap ${CAP} s reached: TERM"; kill -TERM $$ ) 9>&- &
WD=$!
{ date -u +%FT%TZ; uptime; k3d cluster list; docker ps --format '{{.Names}} {{.Status}}'; } > "${OUT}/pre.txt" 2>&1
vram > "${OUT}/vram-pre.txt" 2>&1
if ! vram | awk '{n++} $3 >= 2 {bad=1} END {exit (bad || n != 4)}'; then
    ev "a card holds >= 2 GiB: skipped ($(tr '\n' ';' < "${OUT}/vram-pre.txt"))"; echo skipped > "${OUT}/result"; exit 5
fi
ev "cards free: $(tr '\n' ';' < "${OUT}/vram-pre.txt")"
( while :; do echo "$(date -u +%T) $(vram | awk '{printf "%s=%s ", $2, $3}')"; sleep 10; done ) > "${OUT}/vram-samples.txt" 2>&1 9>&- &
SAMPLER=$!
t=$(date +%s); bg "${TO}" -k 20 300 "${K}" up > "${OUT}/up.txt" 2>&1; ev "up rc=$? in $(( $(date +%s) - t )) s"
kubectl get node -o jsonpath='{.items[0].status.allocatable}' > "${OUT}/allocatable.json" 2>&1
ev "allocatable $(cat "${OUT}/allocatable.json")"

# A. main's image, manifest as rendered: watch the server's log for its first start or failure.
t=$(date +%s)
K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_IMAGE=${MAIN_IMG} K3D_GPU_AGENT_TIMEOUT=360 \
    "${TO}" -k 20 380 "${K}" agent up > "${OUT}/a-agent-up.txt" 2>&1 9>&- &
AP=$! CH=$!
verdict=none
while kill -0 "${AP}" 2>/dev/null && [ "$(( $(date +%s) - t ))" -lt 400 ]; do
    l=$(logs_all)
    if grep -q 'appears to be a URI' <<< "${l}"; then verdict=vllm-port-uri; break; fi
    if grep -q 'Application startup complete' <<< "${l}"; then verdict=started; break; fi
    sleep 10
done
ev "A (${MAIN_IMG%%@*}): ${verdict} after $(( $(date +%s) - t )) s: $(logs_all | grep -m1 -oE "ValueError: VLLM_PORT '[^']*' appears to be a URI|Application startup complete" || true)"
if [ "${verdict}" = started ]; then
    wait "${AP}"; ev "A agent up rc=$?"
    kubectl get --raw "${PROXY}/v1/models" > "${OUT}/a-models.json" 2>&1
else
    kill -TERM "${AP}" 2>/dev/null; wait "${AP}" 2>/dev/null
fi
CH=""
records a
kubectl -n "${NS}" get deploy vllm -o jsonpath='{.spec.template.spec.enableServiceLinks}' > "${OUT}/a-enableServiceLinks.txt" 2>&1
kubectl -n "${NS}" delete deploy vllm --wait=true --timeout=60s > /dev/null 2>&1
n=0; while kubectl -n "${NS}" get pods -l app=vllm -o name 2>/dev/null | grep -q .; do n=$(( n + 2 )); [ "${n}" -lt 60 ] || break; sleep 2; done
ev "A deployment deleted; PVC kept: $(kubectl -n "${NS}" get pvc -o name 2>/dev/null | tr '\n' ' ')"

# B. the PR's image; enableServiceLinks: false patched in as soon as the Deployment exists.
w=$(left)
t=$(date +%s)
if [ "${w}" -le 120 ]; then
    ev "only ${w} s left for B: not run"; echo agent-up-not-run > "${OUT}/result"; exit 0
fi
K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_TIMEOUT=$(( w - 30 )) \
    "${TO}" -k 20 "${w}" "${K}" agent up > "${OUT}/b-agent-up.txt" 2>&1 9>&- &
AP=$! CH=$!
patched=no
while kill -0 "${AP}" 2>/dev/null && [ "$(( $(date +%s) - t ))" -lt 120 ]; do
    if kubectl -n "${NS}" get deploy vllm > /dev/null 2>&1; then
        kubectl -n "${NS}" patch deploy vllm --type=merge -p '{"spec":{"template":{"spec":{"enableServiceLinks":false}}}}' > "${OUT}/b-patch.txt" 2>&1 && patched=yes
        break
    fi
    sleep 1
done
ev "B patched enableServiceLinks=false: ${patched} at +$(( $(date +%s) - t )) s"
wait "${AP}"; arc=$?; CH=""
ev "B agent up rc=${arc} in $(( $(date +%s) - t )) s: $(grep -m1 'warm-up reply' "${OUT}/b-agent-up.txt" | sed 's/\x1b\[[0-9;]*m//g')"
records b
kubectl -n "${NS}" get deploy vllm -o jsonpath='{.spec.template.spec.enableServiceLinks}' > "${OUT}/b-enableServiceLinks.txt" 2>&1
docker exec "k3d-${CL}-server-0" crictl images --digests > "${OUT}/node-images.txt" 2>&1
if [ "${arc}" -eq 0 ]; then
    kubectl get --raw "${PROXY}/v1/models" > "${OUT}/b-models.json" 2>&1
    m=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"][0]["id"])' "${OUT}/b-models.json" 2>/dev/null || echo Qwen3-4B)
    printf '{"model":"%s","messages":[{"role":"user","content":"What is 17 times 23? Answer with the number only."}],"max_tokens":64,"temperature":0,"chat_template_kwargs":{"enable_thinking":false}}' "${m}" > "${OUT}/b-req.json"
    t=$(date +%s%N)
    kubectl create --raw "${PROXY}/v1/chat/completions" -f "${OUT}/b-req.json" > "${OUT}/b-chat.json" 2>&1
    ev "B chat rc=$? in $(( ($(date +%s%N) - t) / 1000000 )) ms: $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(repr(d["choices"][0]["message"]["content"]), d["usage"])' "${OUT}/b-chat.json" 2>&1)"
    vram > "${OUT}/b-vram-serving.txt" 2>&1
    ev "B vram serving: $(tr '\n' ';' < "${OUT}/b-vram-serving.txt")"
    kubectl -n "${NS}" exec deploy/vllm -- sh -c 'ls -l /dev/dri; echo ==; env | grep -E "^(VLLM_|ZE_|ONEAPI|SYCL)" | sort' > "${OUT}/b-pod-devices.txt" 2>&1
    kubectl -n "${NS}" exec deploy/vllm -- sh -c "dpkg-query -W -f='\${Package} \${Version}\n' libze1 libze-dev libze-intel-gpu1 intel-opencl-icd intel-ocloc intel-igc-core-2 intel-igc-opencl-2 libigdgmm12 xpu-smi 2>&1; echo ==; python3 -c 'import importlib.metadata as m; [print(p, m.version(p)) for p in (\"vllm\", \"torch\", \"intel-sycl-rt\", \"oneccl\")]'" > "${OUT}/b-pod-packages.txt" 2>&1
    echo served > "${OUT}/result"
else
    echo agent-up-failed > "${OUT}/result"
fi
EOF
setsid nohup bash -c "${DRIVER}" canary-326b "${CO}" "${OUT}" > "${OUT}/driver.log" 2>&1 < /dev/null &
echo "launched pid $! at $(date -u +%FT%TZ) out ${OUT}"
