#!/usr/bin/env bash
# arm.sh ARM OUT: deploy one H54a arm into the hold's cluster, wait for it, run the phases and
# the quality spot-check, record everything under OUT, then delete the arm (EXPERIMENTS.md "H54
# pre-registration", measurement protocol, and "H54 amendment R1").
#   G         ~/k3dgpu-gemma (models under G/models, mounted in the node at /var/apb-models)
#   REPO      this repository's checkout on the host
#   CLUSTER   the hold's cluster; KUBECONFIG its scratch kubeconfig
#   DEADLINE  epoch second the hold's arms must end by
#   SERVING   the hold's serving.jsonl (bench/eatflow/crescent/serving.py)
#   H54Q      set: an H54q arm (amendment Q2): ARM is R, OVQ, OVB, Lg or Vi, its manifest comes
#             from H54a's records (h54.py q-render), and after readiness it answers its GSM8K
#             items (h54.py q-items) until the item stop, 90 s before DEADLINE; no phases
# status.json: measured | not_deliverable | not_reached | failed, with the reason.
set -uo pipefail
ARM=$1 OUT=$2
mkdir -p "${OUT}"
H=${REPO:?}/bench/h54
PY=python3
LOAD=${REPO}/bench/llm_load.py
ENERGY=${REPO}/bench/energy.sh
now() { date +%s; }
stamp() { echo "$(date -u +%FT%TZ) ${ARM} $*" | tee -a "${OUT}/arm.log"; }
status() { ${PY} -c 'import json,sys; json.dump({"status": sys.argv[1], "reason": sys.argv[2], "at": sys.argv[3]}, open(sys.argv[4], "w"))' \
    "$1" "$2" "$(date -u +%FT%TZ)" "${OUT}/status.json"; stamp "status $1: $2"; }
B=${ARM}   # the H54a arm whose server this is
[ -z "${H54Q:-}" ] || B=$(${PY} "${H}/h54.py" q-base "${ARM}") || exit 1
field() { ${PY} "${H}/h54.py" arms | ${PY} -c 'import json,sys; a=json.load(sys.stdin); print(a["arms"][sys.argv[1]].get(sys.argv[2], ""))' "${B}" "$1"; }
served() { ${PY} "${H}/h54.py" arms | ${PY} -c 'import json,sys; print(json.load(sys.stdin)["served"][sys.argv[1]])' "${B}"; }
NODE=k3d-${CLUSTER:?}-server-0
NSP=$(field ns) SVC=$(field svc) SPORT=$(field port) API=$(field api) BOOT=$(field boot) DEPLOY=$(field deploy)
MODEL=$(served)
PORT=$(( 18000 + RANDOM % 1000 ))
pf=""
# The port-forward runs in a respawning loop: kubectl port-forward can drop on a long phase.
pf_loop() {
    trap 'kill "${k}" 2>/dev/null; exit 0' TERM
    while :; do
        kubectl -n "${NSP}" port-forward "svc/${SVC}" "${PORT}:${SPORT}" >> "${OUT}/pf.log" 2>&1 &
        k=$!
        wait "${k}"
        echo "port-forward exited $(date -u +%FT%TZ)" >> "${OUT}/pf.log"
        sleep 1
    done
}
cleanup_pf() { [ -z "${pf}" ] || kill "${pf}" 2>/dev/null; pf=""; }
trap cleanup_pf EXIT

# H54a: an arm starts with at least 900 s of the hold left, and readiness ends 600 s before the
# hold does. H54q (Q2): no item is asked after STOP; an arm starts with at least 300 s before
# STOP, and readiness ends 60 s before it.
if [ -n "${H54Q:-}" ]; then
    STOP=$(( DEADLINE - 90 ))
    END=${STOP} NEED=300 EDGE=$(( STOP - 60 )) WHAT="before the item stop"
else
    END=${DEADLINE} NEED=900 EDGE=$(( DEADLINE - 600 )) WHAT="of the hold left"
fi
left=$(( END - $(now) ))
if [ "${left}" -lt "${NEED}" ]; then status not_reached "${left} s ${WHAT} (< ${NEED})"; exit 0; fi
stamp "start (${left} s ${WHAT})"

# ---- render --------------------------------------------------------------------------------
M=${OUT}/deploy.yaml
eat_vllm() {  # B1g: EAT's chart at 0d3438d as its playbook installs cpu-gemma4-26b-a4b (release name,
    # xeon-values.yaml, chat-template ConfigMap, hostIPC), the tag pinned to its digest, KV 40 GiB,
    # monitoring and ingress off (they need EAT's operators), the QAT weights staged in place
    printf 'image:\n  tag: "v0.24.0@sha256:b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1"\n' > "${OUT}/pin.yaml"
    ${PY} "${REPO}/bench/apb/stage.py" values "${EAT:?}/core/helm-charts/vllm/xeon-values.yaml" "${OUT}/pin.yaml" 40 "${OUT}/values.yaml"
    helm template vllm-gemma4-26b-a4b-cpu "${EAT}/core/helm-charts/vllm" --values "${OUT}/values.yaml" \
        --set LLM_MODEL_ID=google/gemma-4-26B-A4B-it --set chatTemplateConfigMap.enabled=true --set hostIPC=true \
        --set global.monitoring=false --set svcmonitor.enabled=false \
        --set ingress.enabled=false --set route.enabled=false > "${OUT}/eat.yaml" || return 1
    ${PY} "${REPO}/bench/apb/stage.py" vllm "${OUT}/eat.yaml" "$1" google_gemma-4-26B-A4B-it-qat-q4_0-unquantized 8192
}
[ -z "${H54Q:-}" ] || DEPLOY=q
case ${DEPLOY} in
    q) ${PY} "${H}/h54.py" q-render "${ARM}" "${M}" ;;
    eat-vllm) eat_vllm "${M}" ;;
    derive-vllm) eat_vllm "${OUT}/b1g.yaml" && ${PY} "${H}/h54.py" derive-vllm "${ARM}" "${OUT}/b1g.yaml" "${M}" ;;
    eat-ovms)
        helm template ovms-model-server "${EAT:?}/core/helm-charts/ovms" --set modelSource=OpenVINO/gemma-4-26b-a4b-it-int4-ov \
            --set image.tag="2025.4@sha256:c370720007564bbdcbca13b9b714e4537068542cda512e8095259e66b617962a" \
            --set ingress.enabled=false --set apisixRoute.enabled=false > "${M}" ;;
    k3d-gpu)
        K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_MODEL=$(field model) K3D_GPU_AGENT_MODEL_NAME=gemma K3D_GPU_AGENT_DRAFT=off \
            K3D_GPU_AUTOQUANT=off "${REPO}/scripts/k3d-gpu" agent manifest > "${OUT}/agent.yaml" 2> "${OUT}/agent.err" \
            && ${PY} "${REPO}/bench/apb/stage.py" ovms "${OUT}/agent.yaml" "${M}" ;;
    render) ${PY} "${H}/h54.py" render "${ARM}" "${M}" ;;
esac
[ -s "${M}" ] || { status failed "render failed"; exit 1; }
kubectl apply -f "${M}" > "${OUT}/apply.log" 2>&1 || { status failed "apply failed: $(tail -1 "${OUT}/apply.log")"; exit 1; }

# ---- readiness: the model list and a 16-token chat ------------------------------------------
t_apply=$(now)
limit=$(( BOOT < EDGE - t_apply ? BOOT : EDGE - t_apply ))
base="http://127.0.0.1:${PORT}${API}"
ready=""
while [ $(( $(now) - t_apply )) -lt "${limit}" ]; do
    if [ -z "${pf}" ] || ! kill -0 "${pf}" 2>/dev/null; then
        pf=""
        if kubectl -n "${NSP}" get endpoints "${SVC}" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null | grep -q . \
           || kubectl -n "${NSP}" get pods -o jsonpath='{.items[*].status.phase}' 2>/dev/null | grep -q Running; then
            pf_loop &
            pf=$!
            sleep 3
        fi
    fi
    if [ -n "${pf}" ] && curl -fsS --max-time 10 "${base}/models" > "${OUT}/models.json" 2>/dev/null \
       && curl -fsS --max-time 120 "${base}/chat/completions" -H 'Content-Type: application/json' \
            -d "{\"model\":\"${MODEL}\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Say hello.\"}]}" \
            > "${OUT}/hello.json" 2>/dev/null; then
        ready=1
        break
    fi
    sleep 10
done
t_ready=$(now)
echo "{\"apply\": ${t_apply}, \"ready\": ${t_ready}, \"boot_s\": $(( t_ready - t_apply )), \"limit_s\": ${limit}}" > "${OUT}/boot.json"
kubectl get pods -A -o wide > "${OUT}/pods.txt" 2>&1
pod=$(kubectl -n "${NSP}" get pods -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
record_logs() {
    kubectl -n "${NSP}" logs "${pod}" --all-containers > "${OUT}/server.log" 2>&1
    kubectl -n "${NSP}" describe pod "${pod}" > "${OUT}/describe.txt" 2>&1
}
teardown() {
    cleanup_pf
    record_logs
    kubectl delete -f "${M}" --wait=true --timeout=300s > "${OUT}/delete.log" 2>&1
    kubectl -n "${NSP}" delete pvc --all --wait=true --timeout=120s >> "${OUT}/delete.log" 2>&1
    # The arm's image and OVMS's compiled-model cache leave the disk with the arm.
    img=$(${PY} -c 'import sys,yaml; print(" ".join({c["image"] for d in yaml.safe_load_all(open(sys.argv[1])) if d and d.get("kind")=="Deployment" for c in d["spec"]["template"]["spec"]["containers"]+d["spec"]["template"]["spec"].get("initContainers",[])}))' "${M}")
    for i in ${img}; do docker exec "${NODE}" crictl rmi "${i}" >> "${OUT}/delete.log" 2>&1; done
    docker exec "${NODE}" sh -c 'rm -rf /var/apb-models/.ov_cache/*' >> "${OUT}/delete.log" 2>&1
    # The node's CPU reads idle (< 1 CPU-s/s over 10 s) before the next arm, at most 180 s.
    cg=/sys/fs/cgroup/system.slice/docker-$(docker inspect -f '{{.Id}}' "${NODE}").scope/cpu.stat
    for _ in $(seq 18); do
        a=$(awk '/^usage_usec/{print $2}' "${cg}"); sleep 10; b=$(awk '/^usage_usec/{print $2}' "${cg}")
        [ $(( (b - a) / 10000000 )) -lt 1 ] && break
    done
}
if [ -z "${ready}" ]; then
    if [ "${limit}" -lt "${BOOT}" ]; then status not_reached "not ready within the ${limit} s the hold left (boot limit ${BOOT} s)"
    else status not_deliverable "no model list and 16-token chat within ${BOOT} s"; fi
    teardown
    exit 0
fi
stamp "ready after $(( t_ready - t_apply )) s"
kubectl -n "${NSP}" get pod "${pod}" -o jsonpath='{.spec.containers[0].image} {.status.containerStatuses[0].imageID}' > "${OUT}/image.txt"
kubectl -n "${NSP}" get pod "${pod}" -o json > "${OUT}/pod.json"
kubectl -n "${NSP}" exec "${pod}" -c "$(kubectl -n "${NSP}" get pod "${pod}" -o jsonpath='{.spec.containers[0].name}')" -- \
    sh -c 'grep -c amx_tile /proc/cpuinfo; nproc' > "${OUT}/isa.txt" 2>&1

if [ -n "${H54Q:-}" ]; then
    # H54q (Q2): the arm's GSM8K items, or in a wiring smoke two non-GSM8K questions; no phases.
    # The item window's void check is recorded, not gated (H54a never voided its quality items).
    t0=$(date +%s.%N)
    if [ -n "${SMOKE:-}" ]; then
        ${PY} "${H}/h54.py" q-smoke "${base}" "${MODEL}" "${OUT}" "${STOP}" >> "${OUT}/arm.log" 2>&1
        status smoke "ready; wiring check in smoke-items.jsonl (rc $?)"
    else
        stamp "items: $(${PY} "${H}/h54.py" q-todo "$(dirname "$(dirname "${OUT}")")" "${ARM}" | wc -w) to ask"
        ${PY} "${H}/h54.py" q-items "${base}" "${MODEL}" "$(dirname "$(dirname "${OUT}")")" "${ARM}" "${OUT}" "${STOP}" >> "${OUT}/arm.log" 2>&1
        rc=$?
        if [ -s "${OUT}/q.json" ]; then
            status "$(${PY} -c 'import json,sys; print(json.load(open(sys.argv[1]))["status"])' "${OUT}/q.json")" \
                "$(${PY} -c 'import json,sys; print(json.load(open(sys.argv[1]))["reason"])' "${OUT}/q.json")"
        else
            status failed "q-items exited ${rc} without a record"
        fi
    fi
    t1=$(date +%s.%N)
    echo "{\"phase\": \"items\", \"t0\": ${t0}, \"t1\": ${t1}, \"check\": $(${PY} "${H}/h54.py" void "${SERVING:?}" "${t0}" "${t1}")}" >> "${OUT}/phases.jsonl"
    teardown
    exit 0
fi

if [ -n "${SMOKE:-}" ]; then
    # A wiring smoke (discarded, disclosed): one streamed 16-token request with ignore_eos, and one
    # chat with chat_template_kwargs, then the arm is deleted. No scored request.
    ${PY} "${LOAD}" --base-url "${base}" --model "${MODEL}" --requests 1 --concurrency 1 --warmup 0 --max-tokens 16 \
        --ignore-eos --unique-prompts --out "${OUT}/smoke-load.json" >> "${OUT}/arm.log" 2>&1
    curl -sS --max-time 300 "${base}/chat/completions" -H 'Content-Type: application/json' \
        -d "{\"model\":\"${MODEL}\",\"max_tokens\":16,\"chat_template_kwargs\":{\"enable_thinking\":false},\"messages\":[{\"role\":\"user\",\"content\":\"Say hello.\"}]}" \
        > "${OUT}/smoke-kwargs.json" 2>&1
    status smoke "ready; wiring checks in smoke-*.json"
    teardown
    exit 0
fi

# ---- phases ----------------------------------------------------------------------------------
# Amendment R4: the 60 s idle window (no arm load) is the void rule's CPU window (W0, W1); a void
# phase gets a fresh idle window (energy-idle-<phase>.json) before its one re-run.
idle() {  # idle [SUFFIX]
    W0=$(date +%s.%N)
    bash "${ENERGY}" --zone intel-rapl:0 --interval 5 --json "${OUT}/energy-idle${1:+-$1}.json" -- sleep 60 2>> "${OUT}/arm.log"
    W1=$(date +%s.%N)
}
load() {  # load NAME REQUESTS CONCURRENCY MAX_TOKENS [extra llm_load args]
    local name=$1 n=$2 c=$3 mt=$4
    shift 4
    bash "${ENERGY}" --zone intel-rapl:0 --interval 5 --json "${OUT}/energy-${name}.json" -- \
        ${PY} "${LOAD}" --base-url "${base}" --model "${MODEL}" --requests "${n}" --concurrency "${c}" --warmup 0 \
        --max-tokens "${mt}" --temperature 0 --unique-prompts --timeout 1800 --label "H54a ${ARM} ${name}" \
        --out "${OUT}/load-${name}.json" "$@" 2>> "${OUT}/arm.log"
}
phase() {  # phase NAME REQUESTS CONCURRENCY MAX_TOKENS [extra]: run, void check, one re-run if void
    local name=$1 t0 t1 v
    for try in 1 2; do
        t0=$(date +%s.%N)
        load "$@"
        t1=$(date +%s.%N)
        v=$(${PY} "${H}/h54.py" void "${SERVING:?}" "${t0}" "${t1}" "${W0}" "${W1}")
        echo "{\"phase\": \"${name}\", \"try\": ${try}, \"t0\": ${t0}, \"t1\": ${t1}, \"w0\": ${W0}, \"w1\": ${W1}, \"check\": ${v}}" >> "${OUT}/phases.jsonl"
        case ${v} in *'"void": true'*) [ "${try}" = 1 ] && { mv "${OUT}/load-${name}.json" "${OUT}/load-${name}-void1.json"; idle "${name}"; continue; } ;; esac
        break
    done
}
idle
load warmup 3 1 256 --ignore-eos
phase n1 8 1 256 --ignore-eos
phase n4 16 4 256 --ignore-eos
phase n16 32 16 256 --ignore-eos
phase ttft 4 1 16 --prompt "$(cat "${H}/ttft-prompt.txt")"
stamp "quality"
t0=$(date +%s.%N)
${PY} "${H}/h54.py" gsm8k "${base}" "${MODEL}" "${H}/gsm8k-test-0-199.jsonl" "${OUT}/quality.json" 0-49 >> "${OUT}/arm.log" 2>&1
t1=$(date +%s.%N)
echo "{\"phase\": \"quality\", \"t0\": ${t0}, \"t1\": ${t1}, \"check\": $(${PY} "${H}/h54.py" void "${SERVING}" "${t0}" "${t1}" "${W0}" "${W1}")}" >> "${OUT}/phases.jsonl"
${PY} - "${OUT}" <<'EOF'
import json, os, sys
out = sys.argv[1]
rows = [json.loads(x) for x in open(os.path.join(out, "phases.jsonl"))]
ph = {}
for r in rows:
    p = ph.setdefault(r["phase"], {"tries": []})
    p["tries"].append(r)
for name, p in ph.items():
    last = p["tries"][-1]
    p["void"] = last["check"]["void"]
    p["rerun_void"] = len(p["tries"]) > 1 and p["void"]
    f = os.path.join(out, f"load-{name}.json")
    if os.path.exists(f):
        p["result"] = json.load(open(f))
json.dump(ph, open(os.path.join(out, "phases.json"), "w"), indent=1)
EOF
# Amendment R3: a server that stopped answering after readiness is failed, not measured.
if why=$(${PY} "${H}/h54.py" served "${OUT}"); then status measured "${why}"; else status failed "${why}"; fi
teardown
exit 0
