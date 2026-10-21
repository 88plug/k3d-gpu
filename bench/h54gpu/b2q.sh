#!/usr/bin/env bash
# b2q.sh OUT: H54b's stock arm B2q (EXPERIMENTS.md "H54 pre-registration" and amendment G1).
# EAT PR #21's vLLM chart at c86c42b, rendered with `helm template` from its xpu-values.yaml.
# Changes, and nothing else: LLM_MODEL_ID Qwen/Qwen3.8-27B with the xpu-qwen36-27b profile's
# modelConfigs entry copied under that id, tensor_parallel_size 2, and the model read from the
# campaign's HF cache (global.modelUseHostPath, pvc off) instead of a fresh download; then PR
# #157's H53 boot ladder (ladder.py).
set -uo pipefail
OUT=$1
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "${HERE}/../.." && pwd)
export ROOT
. "${HERE}/lib.sh"
EAT=${EAT:-${HOME}/k3dgpu-gemma/eat}
CHART=${EAT}/core/helm-charts/vllm
[ "$(git -C "${EAT}" rev-parse --short=7 HEAD)" = c86c42b ] || { say "B2q: EAT checkout is not c86c42b"; exit 1; }
python3 - "${CHART}/xpu-values.yaml" > "${OUT}/b2q-override.yaml" <<'PY'
import json, re, sys
# Copy the Qwen/Qwen3.6-27B modelConfigs block verbatim under the Qwen3.8-27B id (YAML text,
# no re-serialisation, so every flag stays as EAT wrote it).
text = open(sys.argv[1]).read()
m = re.search(r'^  "Qwen/Qwen3\.6-27B":\n((?:    .*\n|\s*\n)+)', text, re.M)
if not m:
    sys.exit("Qwen/Qwen3.6-27B profile not found")
print('LLM_MODEL_ID: "Qwen/Qwen3.8-27B"')
print('tensor_parallel_size: "2"')
print("pvc:\n  enabled: false")
print("global:\n  modelUseHostPath: /models/hf")
print('modelConfigs:\n  "Qwen/Qwen3.8-27B":')
print(m.group(1).rstrip())
PY
helm template b2q "${CHART}" -f "${CHART}/xpu-values.yaml" -f "${OUT}/b2q-override.yaml" > "${OUT}/b2q.yaml" 2>> "${OUT}/arms.log" ||
    { say "B2q: helm template failed"; echo "helm template failed" > "${OUT}/B2q.not-deliverable"; exit 1; }
# PR #157's boot ladder: one boot per rung, a first, the next rung only after a failed boot,
# while the hold has time (BOOT_S per boot, default 1200 s).
PY=${PY:-${HOME}/k3dgpu-gemma/pqvenv/bin/python}
booted=""
for rung in a b c d; do
    "${PY}" "${HERE}/ladder.py" "${rung}" "${OUT}/b2q.yaml" "${OUT}/b2q-${rung}.yaml" || break
    kubectl apply -f "${OUT}/b2q-${rung}.yaml" >> "${OUT}/arms.log" 2>&1
    dep=$(kubectl get deploy -o name | head -1)
    say "B2q rung ${rung}: ${dep}"
    if kubectl rollout status "${dep}" --timeout="${BOOT_S:-1200}s" >> "${OUT}/arms.log" 2>&1; then
        booted=${rung}
        break
    fi
    kubectl logs "${dep}" --all-containers > "${OUT}/B2q-${rung}.log" 2>&1
    kubectl delete -f "${OUT}/b2q-${rung}.yaml" --wait=true --timeout=300s >> "${OUT}/arms.log" 2>&1
done
if [ -n "${booted}" ]; then
    echo "${booted}" > "${OUT}/B2q.rung"
    kubectl get pod -o jsonpath='{range .items[*]}{.status.containerStatuses[*].imageID}{"\n"}{end}' > "${OUT}/B2q.image-digest"
    kubectl port-forward "${dep}" 18001:8000 > "${OUT}/B2q.pf.log" 2>&1 &
    PF=$!
    sleep 5
    MODEL_ID=Qwen/Qwen3.8-27B full_phases B2q "http://127.0.0.1:18001/v1" "0,1,2,3" \
        '{"chat_template_kwargs": {"enable_thinking": false}}'
    say "arm B2q done (rung ${booted})"
    kill "${PF}" 2>/dev/null
    kubectl logs "${dep}" --all-containers > "${OUT}/B2q.log" 2>&1
    kubectl delete -f "${OUT}/b2q-${booted}.yaml" --wait=true --timeout=300s >> "${OUT}/arms.log" 2>&1
else
    echo "no rung booted" > "${OUT}/B2q.not-deliverable"
    say "arm B2q not deliverable"
fi
