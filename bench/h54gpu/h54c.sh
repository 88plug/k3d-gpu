#!/usr/bin/env bash
# h54c.sh HOLD CARD: H54c's one hold, run inside hold.sh (EXPERIMENTS.md "H54 pre-registration",
# H54c, and amendment G1). Cluster k3dgpu-g-h54c-HOLD; llama.cpp server-intel-b11749 on the
# Google QAT Q4_0 GGUF, one arm at a time on B70 card CARD (renderD128+CARD, the only render
# node in the pod's /dev/dri): Cg (-ngl 0), Gg (-ngl 99), Hg (-ngl 99 -ot exps=CPU), Gg2
# (Gg again, drift check). Each: N=1 phase only. Then gguf_bytes.py + predict.py.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "${HERE}/../.." && pwd)
export ROOT
hold=$1 card=$2
OUT=${HOME}/k3dgpu-gemma/results/H54c/${SMOKE:+smoke-}hold${hold}
mkdir -p "${OUT}"
export KUBECONFIG=${OUT}/kubeconfig PATH=${HOME}/k3dgpu-gemma/bin:${PATH}
. "${HERE}/lib.sh"
LCPP_IMAGE=${LCPP_IMAGE:-ghcr.io/ggml-org/llama.cpp:server-intel-b11749@sha256:ae2d41ba73574a1e12cd237c88b512c8bb39ba6f703e873912b76cb8988a81bd}
GGUF=google_gemma-4-26B-A4B-it-qat-q4_0-gguf/gemma-4-26B_q4_0-it.gguf
CL=k3dgpu-g-h54c-${hold}
COMMON="-t 48 -Cr 0-47 --cpu-strict 1 --jinja -c 8192 --host 0.0.0.0 --port 8080"

reproduce "bash bench/h54gpu/h54c.sh ${hold} ${card}" "${LCPP_IMAGE}"
[ -n "${SMOKE:-}" ] || card_free "${card}" || { say "card ${card} busy at hold start: no hold"; exit 6; }
say "H54c hold ${hold} card ${card} image ${LCPP_IMAGE}"
cluster_up "${CL}" || { say "cluster create failed"; exit 7; }

pod() {   # pod NAME EXTRA_ARGS: the arm's pod spec on stdout
    local a args="" rd="renderD$((128 + card))" dev=""
    for a in -m "/models/${GGUF}" ${COMMON} $2; do args+="\"${a}\","; done
    # /dev/dri in the pod holds only the free card: an in-memory dir with that one render
    # node mounted on it, so the engine cannot open the cards the live serving uses.
    [ -n "${SMOKE:-}" ] || dev=", {name: dri, mountPath: /dev/dri}, {name: card, mountPath: /dev/dri/${rd}}"
    cat <<YAML
apiVersion: v1
kind: Pod
metadata: {name: $1}
spec:
  restartPolicy: Never
  containers:
  - name: server
    image: ${LCPP_IMAGE}
    args: [${args%,}]
    securityContext: {privileged: true}
    volumeMounts: [{name: models, mountPath: /models, readOnly: true}${dev}]
  volumes:
  - {name: models, hostPath: {path: /models}}
  - {name: dri, emptyDir: {medium: Memory}}
  - {name: card, hostPath: {path: /dev/dri/${rd}, type: CharDevice}}
YAML
}

ARMS=("cg:-ngl 0" "gg:-ngl 99" "hg:-ngl 99 -ot exps=CPU" "gg2:-ngl 99")
# SMOKE=1: the wiring smoke (amendment G1): Cg only, CPU image, no card mounted, discarded.
[ -z "${SMOKE:-}" ] || ARMS=("cg:-ngl 0")
for arm in "${ARMS[@]}"; do
    name=${arm%%:*} extra=${arm#*:}
    pod "${name}" "${extra}" > "${OUT}/${name}.yaml"
    say "arm ${name}: ${extra}"
    if serve_pod "${name}" "${OUT}/${name}.yaml" 18080; then
        kubectl exec "${name}" -- ls -l /dev/dri > "${OUT}/${name}.dri" 2>&1
        sudo -n fuser -v /dev/dri/renderD* 2>&1 | tee "${OUT}/${name}.fuser" >/dev/null
        n1_phase "${name}" 18080 "${card}"
        say "arm ${name} done: $(python3 -c "import json;print(json.load(open('${OUT}/${name}.n1.json'))['phases'][0]['stats']['decode_tokens_per_s'])" 2>/dev/null)"
    else
        say "arm ${name} not deliverable"
    fi
    stop_pod "${name}"
    sleep 20
done
python3 "${HERE}/gguf_bytes.py" "${MODELS}/${GGUF}" > "${OUT}/bytes.json"
python3 "${HERE}/predict.py" "${OUT}/bytes.json" "${OUT}/cg.n1.json" "${OUT}/gg.n1.json" \
    "${OUT}/hg.n1.json" "${OUT}/gg2.n1.json" > "${OUT}/verdict.json"
say "verdict $(python3 -c "import json;d=json.load(open('${OUT}/verdict.json'));print(d['verdict'], d.get('measured_over_pred'))")"
