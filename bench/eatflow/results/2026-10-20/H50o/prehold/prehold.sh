#!/usr/bin/env bash
# prehold.sh STEP: H50o's steps around its holds on crescent (EXPERIMENTS.md, H50o amendment O1,
# items 3, 5 and 6), run from the H50o checkout ~/k3dgpu-bench/k3d-gpu-h50o at the deploy commit.
#   smoke      the B3 wiring smoke: pairs-hold.sh's H50o environment, except
#              LIVE_PAIRS_GUARD=99999 (no pair starts) and the directories under smoke/H50o-o1
#   verify     A1's IR against its manifest.json, under the bench lock
#   frontdoor  E2 item 7: a 16-token chat through Dynamo :8100 per serving pair, passing when it
#              answers with content and that pair's SGLang num_requests_total rises (180 s each)
#   plain      unscored control after hold 1: B3's server outside k3d. The chart's image and
#              server args, user 5000, no capabilities, on the same revision's files (H54q's OVB
#              copy, read-only) with the graph.pbtxt and config.json B3's pull wrote in the smoke;
#              once at the chart's limits (12 CPUs, 24 GiB) and once with no limits
set -uo pipefail
B=${HOME}/k3dgpu-bench
REPO=${B}/k3d-gpu-h50o
C=${REPO}/bench/eatflow
O=${B}/results/H50o/prehold
mkdir -p "${O}"
NODE_IMAGE=cryptoandcoffee/k3d-gpu:0.1.61@sha256:8cd264aa68c77681d7fa474693944de0db8d55de5881253e4029bf72a67e175c
B1_TAG=v0.28.0@sha256:197354e6475e638eb5ccca09beb078488046a82de0e204e7429bf1a804df9647
head_of() { (cd "${REPO}" && git rev-parse HEAD); }
case ${1:?usage: prehold.sh smoke|verify|frontdoor} in
smoke)
    S=${B}/smoke/H50o-o1
    mkdir -p "${S}/pairs"
    exec "${C}/crescent/hold.sh" H50o-smoke-o1 2400 env K3D_GPU_IMAGE="${NODE_IMAGE}" K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%" \
        LIVE_MODE=pairs LIVE_CAP=2370 LIVE_HOLD=1 LIVE_PAIRS_GUARD=99999 \
        LIVE_PAIRS_DIR="${S}/pairs" LIVE_MODEL_SET=gemma4 LIVE_MOUNT_B=1 LIVE_MODELS="${B}/models" LIVE_BLUEPRINT="${B}/eab" \
        LIVE_PY="${B}/mafvenv/bin/python" LIVE_EAT_CHART="${B}/eat-pr21/core/helm-charts/vllm" LIVE_EAT_B1_TAG="${B1_TAG}" \
        LIVE_EAT_OVMS_CHART="${B}/eat-src/core/helm-charts/ovms" PATH="${B}/bin:${PATH}" \
        bash "${C}/live.sh" H50o "${S}/hold1" ;;
verify)
    exec 9> "${HOME}/k3dgpu-bench.lock"
    flock -w 7200 9 || { echo "lock not taken in 7200 s"; exit 4; }
    ir=$(readlink -f "${B}/models/gemma-4-26B-A4B-it-qat-int4-ov")
    { date -u +%FT%TZ; head_of; } > "${O}/ir-verify.txt"
    python3 - "${ir}" >> "${O}/ir-verify.txt" 2>&1 <<'PY'
import hashlib, json, os, sys
d = sys.argv[1]
files = json.load(open(os.path.join(d, "manifest.json")))["files"]
bad = 0
for n, want in sorted(files.items()):
    h = hashlib.sha256()
    with open(os.path.join(d, n), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    ok = h.hexdigest() == want["sha256"] and os.path.getsize(os.path.join(d, n)) == want["bytes"]
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'} {n} {h.hexdigest()} {want['bytes']}")
extra = sorted(set(os.listdir(d)) - set(files) - {"manifest.json"})
print(f"dir {d}; {len(files)} files, {bad} bad; not in the manifest: {extra}")
sys.exit(1 if bad else 0)
PY
    echo "ir rc=$?" >> "${O}/ir-verify.txt"
    cp "${ir}/manifest.json" "${O}/ir-manifest.json" ;;
frontdoor)
    f=${O}/frontdoor-${2:-check}.txt
    : > "${f}"
    n() { curl -s -m 8 "http://127.0.0.1:$1/metrics" | awk '/^sglang:num_requests_total/{s+=$2} END{print s+0}'; }
    for P in 30000 30001; do
        b=$(n "${P}") end=$(( SECONDS + 180 )) ok=0 c=""
        until [ "${SECONDS}" -ge "${end}" ]; do
            c=$(curl -s -m 60 http://127.0.0.1:8100/v1/chat/completions -H 'Content-Type: application/json' \
                -d '{"model":"Qwen3.8-27B","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16,"temperature":0,"chat_template_kwargs":{"enable_thinking":false}}' \
                | python3 -c 'import json,sys;print(json.load(sys.stdin)["choices"][0]["message"]["content"].strip())' 2>/dev/null)
            if [ -n "${c}" ] && [ "$(n "${P}")" -gt "${b}" ]; then ok=1; break; fi
            sleep 5
        done
        echo "$(date -u +%FT%TZ) :${P} pass=${ok} before=${b} after=$(n "${P}") content=${c:-none}" | tee -a "${f}"
    done ;;
plain)
    exec 9> "${HOME}/k3dgpu-bench.lock"
    flock -w 7200 9 || { echo "lock not taken in 7200 s"; exit 4; }
    IMAGE=openvino/model_server:2025.4@sha256:c370720007564bbdcbca13b9b714e4537068542cda512e8095259e66b617962a
    MODEL=OpenVINO/gemma-4-26b-a4b-it-int4-ov
    OVB=${HOME}/k3dgpu-gemma/models/${MODEL}
    R=${B}/smoke/H50o-o1/hold1/b3-model
    P=${O}/plain
    mkdir -p "${P}"
    { date -u +%FT%TZ; head_of; head -1 "${OVB}/.cache/huggingface/download/config.json.metadata"; } > "${P}/provenance.txt"
    for lim in chart none; do
        args=()
        [ "${lim}" = chart ] && args=(--cpus 12 --memory 24g)
        docker rm -f k3dgpu-e-h50o-plain > /dev/null 2>&1
        timeout 300 docker run --name k3dgpu-e-h50o-plain --network none --user 5000 --cap-drop ALL \
            --security-opt no-new-privileges "${args[@]}" \
            -v "${OVB}:/models/${MODEL}:ro" -v "${R}/graph.pbtxt:/models/${MODEL}/graph.pbtxt:ro" \
            -v "${R}/config.json:/models/config.json:ro" \
            "${IMAGE}" --config_path=/models/config.json --rest_port=8081 --port=8080 --log_level=INFO \
            > "${P}/ovms-${lim}.log" 2>&1
        echo "limits=${lim} rc=$?" | tee -a "${P}/provenance.txt"
        docker inspect --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Error}}' k3dgpu-e-h50o-plain \
            >> "${P}/provenance.txt" 2>&1
        docker rm -f k3dgpu-e-h50o-plain > /dev/null 2>&1
    done ;;
*) echo "usage: prehold.sh smoke|verify|frontdoor|plain"; exit 2 ;;
esac
