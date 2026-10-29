#!/usr/bin/env bash
# h60.sh OUT: EXPERIMENTS.md H60, Qwen3.8-Flash-Next (ggml-org IQ4_NL GGUF) served by llama.cpp
# from host DRAM on crescent, against a same-hold control (H54's Lg: Google's Gemma 4 QAT Q4_0
# GGUF on the same llama.cpp build and arguments), measured with H54's protocol. Run inside
# bench/xhost/hold.sh (the shared lock; its serving.py sampler writes SERVING).
#
# The Flash-Next weights live in tmpfs (/dev/shm), not on /, so the / disk guard is unchanged:
# downloaded at the hold's start and removed on every exit (the trap removes them first), the
# window recorded in OUT/shm-window.json. llama.cpp mmaps the file and repacks the IQ4_NL experts
# into an anonymous copy (ggml-cpu repack, on by default), so the hold holds about 170 GB of DRAM
# for weights plus the KV cache.
#
# Guards: MemAvailable >= 300 GiB before the download (else exit 3, nothing downloaded); the
# host's own Qwen3.8 server (localhost:30000) must answer a 4-token chat before (else exit 3)
# and is probed again after; /proc/pressure/memory and MemAvailable are sampled every 10 s into
# OUT/psi.log, and the hold is stopped at once (OUT/psi-abort) when "full avg10" >= 5 or
# MemAvailable falls below 50 GiB: every long step runs in the background and is waited on, so
# the TERM interrupts the wait and the trap kills the step.
#
# Environment:
#   SERVING      serving.py's JSONL for this hold (required; h54.py void reads it)
#   H60_HFPY     python with huggingface_hub (default python3)
#   H60_SHM      tmpfs directory for the GGUF (default /dev/shm/k3dgpu-h60)
#   H60_LG_DIR   directory holding H54's gemma-4-26B_q4_0-it.gguf (required)
#   H60_SMOKE=1  wiring smoke: both servers up, one 16-token request each, no scored phase
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
LG_DIR=${H60_LG_DIR:?set H60_LG_DIR to the directory of gemma-4-26B_q4_0-it.gguf}
LG_GGUF=gemma-4-26B_q4_0-it.gguf
HF_REPO=ggml-org/Qwen3.8-Flash-Next-GGUF
HF_REV=91fb00353e766eb43944ed223665ea4fb87dad47
QUANT=IQ4_NL
FN_GGUF=Qwen3.8-Flash-Next-${QUANT}-00001-of-00002.gguf
IMG=ghcr.io/ggml-org/llama.cpp:server-b11783@sha256:c7b03e45cff9c4e984acd39032a6f3a82c811075b72bf176e329f84b8598c135
export K3D_GPU_CLUSTER=${K3D_GPU_CLUSTER:-k3dgpu-x-h60}
NS=h60 PORT=18060
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
child="" pf="" psi="" dl_t0=""
bg() {  # bg CMD...: run CMD in the background and wait, so a TERM interrupts the wait at once
    "$@" &
    child=$!
    wait "${child}"
    local rc=$?
    child=""
    return "${rc}"
}
cleanup() {
    # The tmpfs copy first: an unlinked file is freed as soon as the server's mapping goes.
    [ -z "${dl_t0}" ] || rm -rf "${SHM}"
    [ -z "${child}" ] || { pkill -TERM -P "${child}" 2>/dev/null; kill -TERM "${child}" 2>/dev/null; }
    [ -z "${pf}" ] || kill "${pf}" 2>/dev/null
    # Again once the step has exited (up to 5 s): a download still running can write a shard
    # after the first rm. removed_ok reads this second removal.
    if [ -n "${dl_t0}" ]; then
        for _ in $(seq 50); do [ -n "${child}" ] && kill -0 "${child}" 2>/dev/null || break; sleep 0.1; done
        rm -rf "${SHM}"
        printf '{"download_start": %s, "removed": %s, "removed_ok": %s}\n' "${dl_t0}" "$(date +%s)" \
            "$([ -e "${SHM}" ] && echo false || echo true)" > "${OUT}/shm-window.json"
    fi
    stamp cleanup
    k3d cluster delete "${K3D_GPU_CLUSTER}" >/dev/null 2>&1
    [ -z "${psi}" ] || kill "${psi}" 2>/dev/null
    echo "after $(probe after)" >> "${OUT}/probes.txt"
    stamp finished
}
trap cleanup EXIT
trap 'exit 143' TERM INT HUP

# ---- guards before anything is written to tmpfs ---------------------------------------------
avail=$(gib)
echo "MemAvailable ${avail} GiB" | tee "${OUT}/mem-before.txt"
[ "${avail}" -ge 300 ] || { echo "MemAvailable ${avail} GiB < 300: no download"; exit 3; }
[ -f "${LG_DIR}/${LG_GGUF}" ] || { echo "no ${LG_DIR}/${LG_GGUF}"; exit 3; }
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

# ---- Flash-Next weights into tmpfs -------------------------------------------------------------
stamp download
dl_t0=$(date +%s)
mkdir -p "${SHM}"
cat > "${OUT}/download.py" <<'EOF'
import fnmatch, json, os, sys
from huggingface_hub import HfApi, snapshot_download
repo, rev, dst, pat = sys.argv[1:5]
snapshot_download(repo, revision=rev, local_dir=dst, allow_patterns=[pat])
info = HfApi().model_info(repo, revision=rev, files_metadata=True)
want = {s.rfilename: s.size for s in info.siblings if fnmatch.fnmatch(s.rfilename, pat)}
got = {f: os.path.getsize(os.path.join(dst, f)) for f in want}
print(json.dumps({"want": want, "got": got}))
assert want and got == want, "size mismatch"
EOF
bg "${HFPY}" "${OUT}/download.py" "${HF_REPO}" "${HF_REV}" "${SHM}" "Qwen3.8-Flash-Next-${QUANT}-*.gguf" > "${OUT}/download.log" 2>&1 \
    || { echo "download failed"; tail -5 "${OUT}/download.log"; exit 1; }
rm -rf "${SHM}/.cache"
${PY} "${HERE}/h60.py" active-bytes "${SHM}"/Qwen3.8-Flash-Next-${QUANT}-*.gguf > "${OUT}/active-bytes.json" \
    || { echo "active-bytes failed"; exit 1; }
${PY} "${HERE}/h60.py" active-bytes "${LG_DIR}/${LG_GGUF}" > "${OUT}/active-bytes-lg.json" \
    || { echo "active-bytes (Lg) failed"; exit 1; }
echo "MemAvailable after download $(gib) GiB" | tee -a "${OUT}/mem-before.txt"

# ---- cluster ---------------------------------------------------------------------------------
stamp up
K3D_GPU_VOLUMES="${SHM}:/var/fn:ro,${LG_DIR}:/var/lg:ro" bg timeout 600 "${REPO}/scripts/k3d-gpu" up > "${OUT}/up.log" 2>&1 \
    || { echo "up failed"; tail -20 "${OUT}/up.log"; exit 1; }
render() {  # render NAME MODEL_PATH ALIAS OUT: H54 Lg's llama.cpp arguments on this build
    ${PY} - "$@" "${IMG}" <<'EOF'
import json, sys
name, model, alias, out, img = sys.argv[1:6]
# bench/h54/h54.py llamacpp_args: 48 threads, one per physical core, bound to CPUs 0-47; plus
# reasoning off for Flash-Next (thinking off, as every H54 arm; Gemma's template has no thinking).
args = ["-m", model, "--host", "0.0.0.0", "--port", "8080", "--alias", alias,
        "-t", "48", "-Cr", "0-47", "--cpu-strict", "1", "--jinja"]
if alias == "flashnext":
    args += ["-rea", "off"]
vols, mounts = [], []
for v, p in (("fn", "/var/fn"), ("lg", "/var/lg")):
    if model.startswith(p + "/"):
        vols.append({"name": v, "hostPath": {"path": p, "type": "Directory"}})
        mounts.append({"name": v, "mountPath": p, "readOnly": True})
c = {"name": "llamacpp", "image": img, "args": args, "ports": [{"containerPort": 8080}], "volumeMounts": mounts}
meta = {"name": name, "namespace": "h60", "labels": {"app": name}}
docs = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "h60"}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": meta,
         "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}},
                  "template": {"metadata": {"labels": {"app": name}}, "spec": {"containers": [c], "volumes": vols}}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "namespace": "h60"},
         "spec": {"selector": {"app": name}, "ports": [{"port": 8080, "targetPort": 8080}]}}]
open(out, "w").write("\n".join(json.dumps(d) for d in docs) + "\n")
EOF
}

# ---- one arm: boot, phases, teardown ---------------------------------------------------------
idle() {  # idle ARM [SUFFIX]
    W0=$(date +%s.%N)
    bg bash "${ENERGY}" --zone intel-rapl:0 --interval 5 --json "${OUT}/$1/energy-idle${2:+-$2}.json" -- sleep 60 2>> "${OUT}/h60.log"
    W1=$(date +%s.%N)
}
load() {  # load ARM NAME REQUESTS CONCURRENCY MAX_TOKENS [extra]
    local arm=$1 name=$2 n=$3 c=$4 mt=$5
    shift 5
    bg bash "${ENERGY}" --zone intel-rapl:0 --interval 5 --json "${OUT}/${arm}/energy-${name}.json" -- \
        ${PY} "${LOAD}" --base-url "${base}" --model "${model}" --requests "${n}" --concurrency "${c}" --warmup 0 \
        --max-tokens "${mt}" --temperature 0 --unique-prompts --timeout 1800 --label "H60 ${arm} ${name}" \
        --out "${OUT}/${arm}/load-${name}.json" "$@" 2>> "${OUT}/h60.log"
}
check() {  # check T0 T1: h54.py void as one JSON line; an error in the check itself reads void
    # h54.py void exits 1 on a void window, so its output decides, not its exit status.
    local o
    o=$(${PY} "${H54}/h54.py" void "${SERVING:?}" "$1" "$2" "${W0}" "${W1}" 2>/dev/null)
    case ${o} in
        *$'\n'*) echo '{"void": true, "reason": "void check failed"}' ;;
        '{"void": '*'}') echo "${o}" ;;
        *) echo '{"void": true, "reason": "void check failed"}' ;;
    esac
}
phase() {  # phase ARM NAME ...: run, H54's void check, one re-run after a fresh idle if void
    local arm=$1 name=$2 a b v
    for try in 1 2; do
        a=$(date +%s.%N)
        load "$@"
        b=$(date +%s.%N)
        v=$(check "${a}" "${b}")
        echo "{\"phase\": \"${name}\", \"try\": ${try}, \"t0\": ${a}, \"t1\": ${b}, \"w0\": ${W0}, \"w1\": ${W1}, \"check\": ${v}}" >> "${OUT}/${arm}/phases.jsonl"
        case ${v} in *'"void": true'*) [ "${try}" = 1 ] && { mv "${OUT}/${arm}/load-${name}.json" "${OUT}/${arm}/load-${name}-void1.json"; idle "${arm}" "${name}"; continue; } ;; esac
        break
    done
}
arm() {  # arm NAME MODEL_PATH ALIAS: one server at a time
    local a=$1 deadline
    model=$3 base=http://127.0.0.1:${PORT}/v1
    mkdir -p "${OUT}/${a}"
    render "h60-${a}" "$2" "$3" "${OUT}/${a}/deploy.yaml"
    kubectl apply -f "${OUT}/${a}/deploy.yaml" > "${OUT}/${a}/apply.log" 2>&1 || { echo "${a}: apply failed"; return 1; }
    stamp "${a} boot"
    deadline=$(( $(date +%s) + 1800 )) ready=""
    while [ "$(date +%s)" -lt "${deadline}" ]; do
        if [ -z "${pf}" ] || ! kill -0 "${pf}" 2>/dev/null; then
            kubectl -n "${NS}" port-forward "svc/h60-${a}" "${PORT}:8080" >/dev/null 2>&1 &
            pf=$!
            sleep 3
        fi
        if curl -fsS -m 10 "${base}/models" > "${OUT}/${a}/models.json" 2>/dev/null \
           && curl -fsS -m 120 "${base}/chat/completions" -H 'Content-Type: application/json' \
                -d "{\"model\":\"${model}\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Say hello.\"}]}" \
                > "${OUT}/${a}/hello.json" 2>/dev/null; then
            ready=1
            break
        fi
        sleep 10
    done
    local pod
    pod=$(kubectl -n "${NS}" get pods -l "app=h60-${a}" -o jsonpath='{.items[0].metadata.name}')
    kubectl -n "${NS}" get pod "${pod}" -o jsonpath='{.spec.containers[0].image} {.status.containerStatuses[0].imageID}' > "${OUT}/${a}/image.txt"
    kubectl -n "${NS}" exec "${pod}" -- sh -c 'grep -c amx_tile /proc/cpuinfo; nproc' > "${OUT}/${a}/isa.txt" 2>&1
    if [ -n "${ready}" ]; then
        stamp "${a} ready"
        if [ "${H60_SMOKE:-0}" = 1 ]; then
            bg ${PY} "${LOAD}" --base-url "${base}" --model "${model}" --requests 1 --warmup 0 --max-tokens 16 --ignore-eos \
                --unique-prompts --out "${OUT}/${a}/smoke-load.json" >> "${OUT}/h60.log" 2>&1
            [ "${a}" = fn ] && curl -sS -m 300 "${base}/chat/completions" -H 'Content-Type: application/json' \
                -d "{\"model\":\"${model}\",\"max_tokens\":16,\"chat_template_kwargs\":{\"enable_thinking\":false},\"messages\":[{\"role\":\"user\",\"content\":\"Say hello.\"}]}" \
                > "${OUT}/${a}/smoke-kwargs.json" 2>&1
        else
            idle "${a}"
            load "${a}" warmup 3 1 256 --ignore-eos
            phase "${a}" n1 8 1 256 --ignore-eos
            if [ "${a}" = fn ]; then
                phase fn n4 16 4 256 --ignore-eos
                phase fn ttft 4 1 16 --prompt "$(cat "${H54}/ttft-prompt.txt")"
                stamp quality
                local q0 q1
                q0=$(date +%s.%N)
                bg ${PY} "${H54}/h54.py" gsm8k "${base}" "${model}" "${H54}/gsm8k-test-0-199.jsonl" "${OUT}/fn/quality.json" 0-49 >> "${OUT}/h60.log" 2>&1
                q1=$(date +%s.%N)
                echo "{\"phase\": \"quality\", \"t0\": ${q0}, \"t1\": ${q1}, \"w0\": ${W0}, \"w1\": ${W1}, \"check\": $(check "${q0}" "${q1}")}" >> "${OUT}/fn/phases.jsonl"
            fi
        fi
    else
        echo "${a}: not deliverable, no model list and 16-token chat in 1800 s" | tee -a "${OUT}/${a}/status.txt"
    fi
    kubectl -n "${NS}" logs "${pod}" > "${OUT}/${a}/server.log" 2>&1
    kubectl -n "${NS}" describe pod "${pod}" > "${OUT}/${a}/describe.txt" 2>&1
    kill "${pf}" 2>/dev/null; pf=""
    kubectl delete -f "${OUT}/${a}/deploy.yaml" --wait=true --timeout=300s > "${OUT}/${a}/delete.log" 2>&1
    [ -n "${ready}" ]
}
# The control first (it takes about 10 minutes and frees nothing in tmpfs), then Flash-Next.
arm lg "/var/lg/${LG_GGUF}" gemma || echo "Lg control not delivered" >> "${OUT}/h60.log"
arm fn "/var/fn/${FN_GGUF}" flashnext || exit 2
[ "${H60_SMOKE:-0}" = 1 ] || ${PY} "${HERE}/h60.py" score "${OUT}" | tee "${OUT}/verdict.json"
