#!/usr/bin/env bash
# h60.sh OUT: EXPERIMENTS.md H60, Qwen3.8-Flash-Next (ggml-org IQ4_NL GGUF) served by llama.cpp
# from host DRAM on crescent, measured with H54's protocol for one arm. Run inside
# bench/xhost/hold.sh (the shared lock; its serving.py sampler writes SERVING).
#
# The weights live in tmpfs (/dev/shm), not on /, so the / disk guard stays as it is: the GGUF is
# downloaded there at the hold's start and removed on every exit (trap), after which the window
# (download start .. removal) is recorded in OUT/shm-window.json. llama.cpp mmaps the file, so the
# weights sit in DRAM once.
#
# Guards: MemAvailable >= 250 GiB before the download (else exit 3, nothing downloaded); the
# host's own Qwen3.8 server (localhost:30000) must answer a 4-token chat before (else exit 3)
# and is probed again after; /proc/pressure/memory and MemAvailable are sampled every 10 s into
# OUT/psi.log, and the hold is stopped (OUT/psi-abort) when "full avg10" >= 5 or MemAvailable
# falls below 50 GiB.
#
# Environment:
#   SERVING      serving.py's JSONL for this hold (required; h54.py void reads it)
#   H60_HFPY     python with huggingface_hub (default python3)
#   H60_SHM      tmpfs directory for the GGUF (default /dev/shm/k3dgpu-h60)
#   H60_SMOKE=1  wiring smoke: model up, one 16-token request, no scored phase
#   K3D_GPU_*    pass through to `k3d-gpu up` (VENDORS, K3S_ARGS, IMAGE)
set -uo pipefail
OUT=${1:?usage: h60.sh OUT}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
H54=${REPO}/bench/h54
LOAD=${REPO}/bench/llm_load.py
ENERGY=${REPO}/bench/energy.sh
PY=python3
HFPY=${H60_HFPY:-python3}
SHM=${H60_SHM:-/dev/shm/k3dgpu-h60}
HF_REPO=ggml-org/Qwen3.8-Flash-Next-GGUF
HF_REV=91fb00353e766eb43944ed223665ea4fb87dad47
QUANT=IQ4_NL
GGUF=Qwen3.8-Flash-Next-${QUANT}-00001-of-00002.gguf
IMG=ghcr.io/ggml-org/llama.cpp:server-b11783@sha256:c7b03e45cff9c4e984acd39032a6f3a82c811075b72bf176e329f84b8598c135
export K3D_GPU_CLUSTER=${K3D_GPU_CLUSTER:-k3dgpu-x-h60}
NS=h60 SVC=h60-llamacpp PORT=18060 MODEL=flashnext
mkdir -p "${OUT}"
export KUBECONFIG=${OUT}/kubeconfig
t0=$(date +%s)
stamp() { echo "== $* $(date -u +%FT%TZ) (+$(( $(date +%s) - t0 ))s)" | tee -a "${OUT}/h60.log"; }
gib() { awk '/^MemAvailable:/ {printf "%d", $2 / 1048576}' /proc/meminfo; }
probe() {  # probe NAME: a 4-token chat to the host's own Qwen3.8 server, recorded
    curl -sS -m 120 http://127.0.0.1:30000/v1/chat/completions -H 'Content-Type: application/json' \
        -d '{"model":"Qwen3.8-27B","max_tokens":4,"messages":[{"role":"user","content":"Say hi."}]}' \
        -o "${OUT}/probe-$1.json" -w '%{http_code}' 2>/dev/null
}
pf="" psi="" dl_t0=""
cleanup() {
    [ -z "${pf}" ] || kill "${pf}" 2>/dev/null
    stamp cleanup
    k3d cluster delete "${K3D_GPU_CLUSTER}" >/dev/null 2>&1
    [ -z "${psi}" ] || kill "${psi}" 2>/dev/null
    if [ -n "${dl_t0}" ]; then
        rm -rf "${SHM}"
        printf '{"download_start": %s, "removed": %s, "removed_ok": %s}\n' "${dl_t0}" "$(date +%s)" \
            "$([ -e "${SHM}" ] && echo false || echo true)" > "${OUT}/shm-window.json"
    fi
    echo "after $(probe after)" >> "${OUT}/probes.txt"
    stamp done
}
trap cleanup EXIT
trap 'exit 143' TERM INT HUP

# ---- guards before anything is written to tmpfs ---------------------------------------------
avail=$(gib)
echo "MemAvailable ${avail} GiB" | tee "${OUT}/mem-before.txt"
[ "${avail}" -ge 250 ] || { echo "MemAvailable ${avail} GiB < 250: no download"; exit 3; }
code=$(probe before)
echo "before ${code}" > "${OUT}/probes.txt"
[ "${code}" = 200 ] || { echo "host Qwen3.8 server not answering (HTTP ${code}): no hold"; exit 3; }
( while :; do
      echo "$(date +%s) $(gib) $(tr '\n' ' ' < /proc/pressure/memory)" >> "${OUT}/psi.log"
      full=$(awk '/^full/ {sub("avg10=", "", $2); print $2}' /proc/pressure/memory)
      if awk -v f="${full}" 'BEGIN {exit !(f >= 5)}' || [ "$(gib)" -lt 50 ]; then
          echo "full avg10 ${full}, MemAvailable $(gib) GiB at $(date -u +%FT%TZ)" > "${OUT}/psi-abort"
          kill -TERM $$
          break
      fi
      sleep 10
  done ) &
psi=$!

# ---- weights into tmpfs ----------------------------------------------------------------------
stamp download
dl_t0=$(date +%s)
mkdir -p "${SHM}"
"${HFPY}" - "${HF_REPO}" "${HF_REV}" "${SHM}" "Qwen3.8-Flash-Next-${QUANT}-*.gguf" > "${OUT}/download.log" 2>&1 <<'EOF' \
    || { echo "download failed"; tail -5 "${OUT}/download.log"; exit 1; }
import json, sys
from huggingface_hub import HfApi, snapshot_download
repo, rev, dst, pat = sys.argv[1:5]
p = snapshot_download(repo, revision=rev, local_dir=dst, allow_patterns=[pat])
info = HfApi().model_info(repo, revision=rev, files_metadata=True)
import fnmatch, os
want = {s.rfilename: s.size for s in info.siblings if fnmatch.fnmatch(s.rfilename, pat)}
got = {f: os.path.getsize(os.path.join(dst, f)) for f in want}
print(json.dumps({"want": want, "got": got}))
assert want and got == want, "size mismatch"
EOF
rm -rf "${SHM}/.cache"
${PY} "${HERE}/h60.py" active-bytes "${SHM}"/Qwen3.8-Flash-Next-${QUANT}-*.gguf > "${OUT}/active-bytes.json" \
    || { echo "active-bytes failed"; exit 1; }
echo "MemAvailable after download $(gib) GiB" | tee -a "${OUT}/mem-before.txt"

# ---- cluster and server ----------------------------------------------------------------------
stamp up
K3D_GPU_VOLUMES="${SHM}:/var/fn:ro" timeout 600 "${REPO}/scripts/k3d-gpu" up > "${OUT}/up.log" 2>&1 \
    || { echo "up failed"; tail -20 "${OUT}/up.log"; exit 1; }
${PY} - "${OUT}/deploy.yaml" "${IMG}" "${GGUF}" <<'EOF'
import json, sys
out, img, gguf = sys.argv[1:4]
# H54 Lg's llama.cpp arguments (bench/h54/h54.py llamacpp_args): 48 threads, one per physical
# core, bound to CPUs 0-47; plus reasoning off (thinking off, as every H54 arm).
args = ["-m", f"/var/fn/{gguf}", "--host", "0.0.0.0", "--port", "8080", "--alias", "flashnext",
        "-t", "48", "-Cr", "0-47", "--cpu-strict", "1", "--jinja", "-rea", "off"]
c = {"name": "llamacpp", "image": img, "args": args, "ports": [{"containerPort": 8080}],
     "volumeMounts": [{"name": "fn", "mountPath": "/var/fn", "readOnly": True}]}
meta = {"name": "h60-llamacpp", "namespace": "h60", "labels": {"app": "h60-llamacpp"}}
docs = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "h60"}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": meta,
         "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "h60-llamacpp"}},
                  "template": {"metadata": {"labels": {"app": "h60-llamacpp"}},
                               "spec": {"containers": [c], "volumes": [{"name": "fn", "hostPath": {"path": "/var/fn", "type": "Directory"}}]}}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "h60-llamacpp", "namespace": "h60"},
         "spec": {"selector": {"app": "h60-llamacpp"}, "ports": [{"port": 8080, "targetPort": 8080}]}}]
open(out, "w").write("\n".join(json.dumps(d) for d in docs) + "\n")
EOF
kubectl apply -f "${OUT}/deploy.yaml" > "${OUT}/apply.log" 2>&1 || { echo "apply failed"; cat "${OUT}/apply.log"; exit 1; }
stamp boot
base=http://127.0.0.1:${PORT}/v1 ready=""
for _ in $(seq 180); do
    if [ -z "${pf}" ] || ! kill -0 "${pf}" 2>/dev/null; then
        kubectl -n "${NS}" port-forward "svc/${SVC}" "${PORT}:8080" >/dev/null 2>&1 &
        pf=$!
        sleep 3
    fi
    if curl -fsS -m 10 "${base}/models" > "${OUT}/models.json" 2>/dev/null \
       && curl -fsS -m 300 "${base}/chat/completions" -H 'Content-Type: application/json' \
            -d "{\"model\":\"${MODEL}\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Say hello.\"}]}" \
            > "${OUT}/hello.json" 2>/dev/null; then
        ready=1
        break
    fi
    sleep 10
done
pod=$(kubectl -n "${NS}" get pods -o jsonpath='{.items[0].metadata.name}')
kubectl -n "${NS}" get pod "${pod}" -o jsonpath='{.spec.containers[0].image} {.status.containerStatuses[0].imageID}' > "${OUT}/image.txt"
record() { kubectl -n "${NS}" logs "${pod}" > "${OUT}/server.log" 2>&1; kubectl -n "${NS}" describe pod "${pod}" > "${OUT}/describe.txt" 2>&1; }
[ -n "${ready}" ] || { record; echo "not deliverable: no model list and 16-token chat in 1800 s"; exit 2; }
stamp "ready"
if [ "${H60_SMOKE:-0}" = 1 ]; then
    ${PY} "${LOAD}" --base-url "${base}" --model "${MODEL}" --requests 1 --warmup 0 --max-tokens 16 --ignore-eos \
        --unique-prompts --out "${OUT}/smoke-load.json" >> "${OUT}/h60.log" 2>&1
    record
    exit 0
fi

# ---- H54's phases for one arm (bench/h54/arm.sh), N=16 left out --------------------------------
idle() {
    W0=$(date +%s.%N)
    bash "${ENERGY}" --zone intel-rapl:0 --interval 5 --json "${OUT}/energy-idle${1:+-$1}.json" -- sleep 60 2>> "${OUT}/h60.log"
    W1=$(date +%s.%N)
}
load() {  # load NAME REQUESTS CONCURRENCY MAX_TOKENS [extra]
    local name=$1 n=$2 c=$3 mt=$4
    shift 4
    bash "${ENERGY}" --zone intel-rapl:0 --interval 5 --json "${OUT}/energy-${name}.json" -- \
        ${PY} "${LOAD}" --base-url "${base}" --model "${MODEL}" --requests "${n}" --concurrency "${c}" --warmup 0 \
        --max-tokens "${mt}" --temperature 0 --unique-prompts --timeout 1800 --label "H60 ${name}" \
        --out "${OUT}/load-${name}.json" "$@" 2>> "${OUT}/h60.log"
}
phase() {  # run, void check (h54.py void on SERVING), one re-run after a fresh idle if void
    local name=$1 a b v
    for try in 1 2; do
        a=$(date +%s.%N)
        load "$@"
        b=$(date +%s.%N)
        v=$(${PY} "${H54}/h54.py" void "${SERVING:?}" "${a}" "${b}" "${W0}" "${W1}")
        echo "{\"phase\": \"${name}\", \"try\": ${try}, \"t0\": ${a}, \"t1\": ${b}, \"check\": ${v}}" >> "${OUT}/phases.jsonl"
        case ${v} in *'"void": true'*) [ "${try}" = 1 ] && { mv "${OUT}/load-${name}.json" "${OUT}/load-${name}-void1.json"; idle "${name}"; continue; } ;; esac
        break
    done
}
idle
load warmup 3 1 256 --ignore-eos
phase n1 8 1 256 --ignore-eos
phase n4 16 4 256 --ignore-eos
phase ttft 4 1 16 --prompt "$(cat "${H54}/ttft-prompt.txt")"
stamp quality
${PY} "${H54}/h54.py" gsm8k "${base}" "${MODEL}" "${H54}/gsm8k-test-0-199.jsonl" "${OUT}/quality.json" 0-49 >> "${OUT}/h60.log" 2>&1
record
${PY} "${HERE}/h60.py" score "${OUT}" | tee "${OUT}/verdict.json"
