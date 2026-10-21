#!/usr/bin/env bash
# The llama.cpp agent backend and free-card pinning, offline. `agent manifest` renders
# against the fit fixtures (tests/fixtures/fit: hosts through K3D_GPU_FIT_HOST, model
# metadata linked into a scratch K3D_GPU_FIT_CACHE) with a stubbed kubectl:
#   - Xeon 6741P + 4x Arc Pro B70, card0 and card1 filled by host processes: the QAT GGUF
#     on one free B70 (SYCL image, the fit's -c/-np/-ngl, --jinja, --reasoning off,
#     download pinned to the GGUF's commit), Qwen3.8-Flash-Next with experts and n-gram
#     table in DRAM (--n-cpu-moe, -ts, two B70s, a one-time conversion Job and the pod
#     waiting for it), the FP8 checkpoint converted with --fp8-as-q8, the AutoRound W4A16
#     checkpoint refused (the converter cannot read it), Gemma 4 safetensors on vLLM TP2;
#   - a desktop with no GPU: the QAT GGUF on the CPU image, P-core threads, mlock;
#   - MTP only on request: Gemma's QAT drafter converted to Q4_0, Flash-Next's own head;
#   - a backend the user set is not overridden; the guard re-renders llama.cpp at a rung.
# Card pinning runs `up` with docker, k3d and kubectl stubbed on a sysroot with four
# B70s: the plugin manifest written into the node mounts only card2/card3's nodes.
#
# Usage: tests/llamacpp_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
K3D_GPU="${ROOT}/scripts/k3d-gpu"
FIX="${ROOT}/tests/fixtures/fit"
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; [ -z "${2:-}" ] || sed 's/^/     | /' <<< "$2"; }

WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
mkdir -p "${WORK}/bin" "${WORK}/cache" "${WORK}/state" "${WORK}/root/proc/pressure" "${WORK}/root/sys" "${WORK}/root/dev"
# Two hardware threads per core (cpu0's core_cpus_list), for the thread count.
mkdir -p "${WORK}/root/sys/devices/system/cpu/cpu0/topology"
echo 0,16 > "${WORK}/root/sys/devices/system/cpu/cpu0/topology/core_cpus_list"
cat > "${WORK}/bin/kubectl" <<'EOF'
#!/bin/sh
echo "kubectl $*" >> "${STUB_LOG:-/dev/null}"
case "$*" in
    *allocatable*) printf '%s\n' "${STUB_ALLOC:-}" ;;
    *"jsonpath={.spec.replicas}"*) printf '%s' "${STUB_REPLICAS:-}" ;;
    "get nodes -l gpu.intel.com/xmx=true -o name") [ -z "${STUB_XMX:-}" ] || echo node/k3d-gpu-server-0 ;;
    "get nodes -l k3d-gpu.io/cores-hybrid=true -o name") [ -z "${STUB_HYBRID:-}" ] || echo node/k3d-gpu-server-0 ;;
    "apply -f -") cat > "${STUB_APPLIED:-/dev/null}" ;;
esac
exit 0
EOF
chmod +x "${WORK}/bin/kubectl"
for d in "${FIX}"/models/*/; do
    n=$(basename "${d}")
    ln -s "${d%/}" "${WORK}/cache/${n}@main"
done
export K3D_GPU_SYSROOT="${WORK}/root" K3D_GPU_FIT_CACHE="${WORK}/cache" XDG_STATE_HOME="${WORK}/state" \
    K3D_GPU_AUTOQUANT_CACHE="${WORK}/no-autoquant.json" PATH="${WORK}/bin:${PATH}"
XEON="${FIX}/hosts/xeon6741p-4xb70.json"
DESK="${FIX}/hosts/desktop-i9-nogpu.json"
# The Xeon's cluster after `up` pinned the free cards: two B70s advertised, XMX labelled.
XEON_ENV=(STUB_XMX=1 STUB_ALLOC=$'cpu 64\ngpu.intel.com/xe 2' K3D_GPU_FIT_HOST="${XEON}")
SYCL=sha256:632c96d3c52ddadcd4e53fd2835e6430814cf68dde06b39067c1e281bdc5bd90
CPUIMG=sha256:8ffe0d28ff5261cc2f78ca1f67db1515bc2529b904a3c25f5b24f4cedfc3411d
FULL=sha256:627dde1b97b1a42ba6fc20a13452650a988f199321a258ec1bec80196b9a02a8

# render <name> [env...] -- [agent args...]: manifest in ${WORK}/<name>.yaml, stderr in .err
render() {
    local name=$1; shift
    local -a envs=()
    while [ "$1" != -- ]; do envs+=("$1"); shift; done
    shift
    env ${envs[@]+"${envs[@]}"} bash "${K3D_GPU}" agent manifest "$@" > "${WORK}/${name}.yaml" 2> "${WORK}/${name}.err"
}
has() { grep -q -- "$2" "${WORK}/$1.yaml"; }
show() { cat "${WORK}/$1.err" "${WORK}/$1.yaml" 2>/dev/null | head -"${2:-120}"; }
# The server container's args, one per line, without the YAML list prefix.
server_args() {
    awk '/^        - name: llamacpp$/ { c = 1 } c && /^          args:$/ { a = 1; next }
         a && /^            - / { sub(/^            - /, ""); gsub(/^"|"$/, ""); print; next } a { exit }' "${WORK}/$1.yaml"
}
arg() { server_args "$1" | grep -qx -- "$2"; }
# The manifest parses into one document per object (python yaml, when present).
yaml_ok() {
    python3 -c 'import sys
try:
    import yaml
except ImportError:
    sys.exit(0)
text = open(sys.argv[1]).read()
docs = [d for d in yaml.safe_load_all(text) if d]
# One document per kind: line (a missing separator merges two objects into one map).
assert len(docs) == text.count("\nkind: ") + text.startswith("kind: "), len(docs)' "${WORK}/$1.yaml"
}

# --- Gemma 4 QAT GGUF on one free B70 ------------------------------------------------
GEMMA_GGUF=google/gemma-4-26B-A4B-it-qat-q4_0-gguf
GREV=d1c082be9cf3c8a514acf63b8761f4b41935842e
if render gguf "${XEON_ENV[@]}" -- --model "${GEMMA_GGUF}" \
    && has gguf '^  name: llamacpp$' && has gguf "image: ghcr.io/ggml-org/llama.cpp:server-intel-b11669@${SYCL}" \
    && arg gguf "--model=/models/gguf/google__gemma-4-26B-A4B-it-qat-q4_0-gguf@${GREV}/gemma-4-26B_q4_0-it.gguf" \
    && arg gguf "--model-url=https://huggingface.co/${GEMMA_GGUF}/resolve/${GREV}/gemma-4-26B_q4_0-it.gguf" \
    && arg gguf '--alias=gemma-4-26B-A4B-it-qat-q4_0-gguf' && arg gguf '--jinja' && arg gguf '--reasoning=off' \
    && arg gguf '-c' && arg gguf '786432' && arg gguf '-np' && arg gguf '6' && arg gguf '-ngl' && arg gguf '999' \
    && arg gguf '--port=8000' && server_args gguf | grep -qE '^--threads=[0-9]+$' \
    && ! arg gguf '-ts' && ! has gguf 'spec-type' && ! has gguf 'kind: Job' && ! has gguf 'load-mode' \
    && grep -A3 '^            limits:$' "${WORK}/gguf.yaml" | grep -q '^              gpu.intel.com/xe: 1$' \
    && has gguf 'name: ZES_ENABLE_SYSMAN' && has gguf 'k3d-gpu.io/agent-backend: llama.cpp' \
    && has gguf 'path: /health' && has gguf 'gpu.intel.com/xmx: "true"' \
    && grep -q 'backend llama.cpp, the engine the fit picked' "${WORK}/gguf.err" && yaml_ok gguf; then
    ok "QAT GGUF on the Xeon + 4x B70 (2 busy): llama-server on one B70, flags from the fit, no flag given"
else fail "QAT GGUF on the Xeon + 4x B70 (2 busy)" "$(show gguf)"; fi

# --- the desktop with no GPU: the CPU image on the P-core balloon ------------------------
if render cpu STUB_ALLOC='cpu 32' K3D_GPU_FIT_HOST="${DESK}" -- --model "${GEMMA_GGUF}" \
    && has cpu "image: ghcr.io/ggml-org/llama.cpp:server-b11669@${CPUIMG}" && arg cpu '-ngl' && arg cpu '0' \
    && arg cpu '--load-mode=mmap+mlock' && has cpu 'add: \[IPC_LOCK\]' && ! has cpu 'gpu.intel.com' \
    && ! has cpu 'ZES_ENABLE_SYSMAN' && grep -A1 '^            limits:$' "${WORK}/cpu.yaml" | grep -qE '^              memory: 45Gi$' \
    && server_args cpu | grep -qx -- '--threads-batch=16' && server_args cpu | grep -qx -- '--threads=8' \
    && yaml_ok cpu; then
    ok "QAT GGUF on the desktop with no GPU: CPU image, -ngl 0, mlock, pod limit and threads from the pod's CPUs"
else fail "QAT GGUF on the CPU-only desktop" "$(show cpu)"; fi

# --- Qwen3.8-Flash-Next: experts and n-gram table in DRAM, converted once ----------------
FN=Qwen/Qwen3.8-Flash-Next
FNREV=de4b8e4d43b917e7706784d8bb445c9af86a3540
if render fn "${XEON_ENV[@]}" -- --model "${FN}" \
    && arg fn "--model=/models/gguf/Qwen__Qwen3.8-Flash-Next@${FNREV}/model.gguf" && ! has fn 'model-url' \
    && arg fn '--n-cpu-moe' && arg fn '44' && arg fn '-ts' && arg fn '30,30' && arg fn '-np' && arg fn '4' \
    && grep -A3 '^            limits:$' "${WORK}/fn.yaml" | grep -q '^              gpu.intel.com/xe: 2$' \
    && has fn 'kind: Job' && has fn "image: ghcr.io/ggml-org/llama.cpp:full-b11669@${FULL}" \
    && has fn '^            - "Qwen/Qwen3.8-Flash-Next"$' && has fn "^            - \"${FNREV}\"\$" \
    && has fn '^            - "--outtype"$' && has fn '^            - "bf16"$' && ! has fn 'fp8-as-q8' \
    && has fn 'value: "transformers==5.20.2 ' && has fn 'name: wait-gguf' \
    && has fn "for d in /models/gguf/Qwen__Qwen3.8-Flash-Next@${FNREV}; do" \
    && has fn 'kind: ConfigMap' && has fn '^    gguf_convert () $' && ! has fn 'load-mode' \
    && grep -q 'requests:' "${WORK}/fn.yaml" && ! grep -q '^              cpu: "2"$' <(sed -n '/name: llamacpp$/,$p' "${WORK}/fn.yaml") \
    && yaml_ok fn; then
    ok "Flash-Next on the Xeon: --n-cpu-moe and -ts on two B70s, a pinned one-time conversion Job, the pod waits for it"
else fail "Flash-Next on the Xeon" "$(show fn 200)"; fi

if render fn8 "${XEON_ENV[@]}" -- --model Qwen/Qwen3.8-Flash-Next-FP8 \
    && has fn8 '^            - "--fp8-as-q8"$' && has fn8 'kind: Job'; then
    ok "Flash-Next FP8: converted with --fp8-as-q8, the 1 B per weight the fit counted"
else fail "Flash-Next FP8 conversion" "$(show fn8)"; fi

if ! render w4 "${XEON_ENV[@]}" -- --model Intel/Qwen3.8-Flash-Next-W4A16-AutoRound \
    && grep -q 'does not read quant_method auto-round (conversion/base.py:573)' "${WORK}/w4.err"; then
    ok "Flash-Next AutoRound W4A16: refused before anything runs, the converter cannot read it"
else fail "Flash-Next W4A16 refusal" "$(show w4)"; fi

# The conversion script itself: built once, a second run returns at once.
CV="${WORK}/convert"
mkdir -p "${CV}/bin" "${CV}/models/gguf"
cat > "${CV}/bin/pip" <<'EOF'
#!/bin/sh
echo "pip $*" >> "${CV_LOG}"
EOF
cat > "${CV}/bin/python3" <<'EOF'
#!/bin/sh
echo "python3 $*" >> "${CV_LOG}"
case "$*" in
    *convert_hf_to_gguf.py*) for a; do [ "$prev" = --outfile ] && echo gguf > "$a"; prev=$a; done ;;
    *manifest.json*) exec /usr/bin/env -u PATH "${REAL_PY}" "$@" ;;
esac
exit 0
EOF
chmod +x "${CV}/bin/pip" "${CV}/bin/python3"
sed -n '/^    gguf_convert () $/,$p' "${WORK}/fn.yaml" | sed -n '1,/^    gguf_convert "\$@"$/p' | sed 's/^    //' > "${CV}/convert.sh"
cv() {
    env CV_LOG="${CV}/log" REAL_PY="$(command -v python3)" GGUF_CONVERT_PIP="transformers==5.20.2" PATH="${CV}/bin:${PATH}" \
        bash "${CV}/convert.sh" Qwen/Qwen3.8-Flash-Next "${FNREV}" "${CV}/models/gguf/fn" 1000 - --outtype bf16
}
if cv > "${CV}/out1" 2>&1 && [ -s "${CV}/models/gguf/fn/model.gguf" ] && [ -f "${CV}/models/gguf/fn/manifest.json" ] \
    && [ ! -e "${CV}/models/gguf/fn.src" ] && [ ! -e "${CV}/models/gguf/fn.partial" ] \
    && grep -q '^pip install -q --target /tmp/pydeps' "${CV}/log" \
    && grep -q "convert_hf_to_gguf.py --outtype bf16 --outfile ${CV}/models/gguf/fn.partial/model.gguf ${CV}/models/gguf/fn.src" "${CV}/log" \
    && : > "${CV}/log" && cv > "${CV}/out2" 2>&1 && grep -q 'already built' "${CV}/out2" && [ ! -s "${CV}/log" ]; then
    ok "gguf_convert: downloads the pinned revision, converts, writes the manifest last; a second run does nothing"
else fail "gguf_convert" "$(cat "${CV}/out1" "${CV}/out2" "${CV}/log" 2>/dev/null)"; fi
rm -rf "${CV}/models/gguf/fn"
if ! env CV_LOG="${CV}/log" PATH="${CV}/bin:${PATH}" bash "${CV}/convert.sh" Qwen/Qwen3.8-Flash-Next "${FNREV}" \
        "${CV}/models/gguf/fn" "$((1 << 60))" - --outtype bf16 > "${CV}/out3" 2>&1 && grep -q 'GiB more under' "${CV}/out3"; then
    ok "gguf_convert: stops before downloading when the volume has no room"
else fail "gguf_convert disk check" "$(cat "${CV}/out3")"; fi

# --- Gemma 4 safetensors: the fit picks vLLM, tensor parallel over the two free B70s ------
if render st "${XEON_ENV[@]}" -- --model google/gemma-4-26B-A4B-it \
    && has st '^  name: vllm$' && has st '--tensor-parallel-size=2' \
    && grep -A3 '^            limits:$' "${WORK}/st.yaml" | grep -q '^              gpu.intel.com/xe: 2$'; then
    ok "Gemma 4 safetensors on the Xeon: vLLM tensor parallel on the two free B70s, no flag given"
else fail "Gemma 4 safetensors follows the fit to vLLM" "$(show st)"; fi

# --- a backend the user set is kept ----------------------------------------------------
if ! render set "${XEON_ENV[@]}" K3D_GPU_AGENT_BACKEND=ovms -- --model "${GEMMA_GGUF}" \
    && grep -q 'K3D_GPU_AGENT_BACKEND=ovms cannot serve it: run llama-server with' "${WORK}/set.err"; then
    ok "K3D_GPU_AGENT_BACKEND=ovms is not overridden by the fit: refused with the llama-server flags"
else fail "an explicit backend is kept" "$(show set)"; fi
if render setl "${XEON_ENV[@]}" K3D_GPU_AGENT_BACKEND=llama.cpp -- --model "${GEMMA_GGUF}" && has setl '^  name: llamacpp$'; then
    ok "K3D_GPU_AGENT_BACKEND=llama.cpp serves the GGUF"
else fail "K3D_GPU_AGENT_BACKEND=llama.cpp" "$(show setl)"; fi

# --- MTP on request ---------------------------------------------------------------------
DRAFTER=google__gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant@9537141506fe8875b3ed45b264af13580cb29166
if render mtp "${XEON_ENV[@]}" K3D_GPU_AGENT_DRAFT=mtp -- --model "${GEMMA_GGUF}" \
    && arg mtp '--spec-type=draft-mtp' && arg mtp "--spec-draft-model=/models/gguf/${DRAFTER}/model.gguf" \
    && has mtp '^            - "Q4_0"$' && has mtp '^            - "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant"$' \
    && has mtp "for d in /models/gguf/${DRAFTER}; do" && ! has mtp 'spec-draft-n-max'; then
    ok "K3D_GPU_AGENT_DRAFT=mtp on the QAT GGUF: its QAT assistant converted once to Q4_0, default draft length"
else fail "MTP drafter for the QAT GGUF" "$(show mtp 200)"; fi
if render fnmtp "${XEON_ENV[@]}" K3D_GPU_AGENT_DRAFT=mtp -- --model "${FN}" \
    && arg fnmtp '--spec-type=draft-mtp' && ! has fnmtp 'spec-draft-model'; then
    ok "K3D_GPU_AGENT_DRAFT=mtp on Flash-Next: drafts from its own MTP head, kept by the conversion"
else fail "MTP from Flash-Next's own head" "$(show fnmtp)"; fi
if ! render badd "${XEON_ENV[@]}" K3D_GPU_AGENT_DRAFT=NPU -- --model "${GEMMA_GGUF}" \
    && grep -q 'does not apply to llama.cpp' "${WORK}/badd.err"; then
    ok "an OVMS draft device is refused on llama.cpp"
else fail "OVMS draft device on llama.cpp" "$(show badd)"; fi

# --- agent up's fit records llama.cpp; the guard re-renders it at a rung ------------------
rm -f "${WORK}/state/k3d-gpu/agent-fit.json"
if render rec AGENT_FIT_RECORD=1 STUB_ALLOC='cpu 32' K3D_GPU_FIT_HOST="${DESK}" -- --model "${GEMMA_GGUF}" \
    && [ -f "${WORK}/state/k3d-gpu/agent-fit.json" ]; then
    want=$(python3 -c 'import json, sys; g = json.load(open(sys.argv[1]))["guard"]; print("%d %d" % tuple(g["rungs"][1]))' \
        "${WORK}/state/k3d-gpu/agent-fit.json")
    if render rung STUB_ALLOC='cpu 32' K3D_GPU_FIT_HOST="${DESK}" K3D_GPU_FIT_RUNG=1 K3D_GPU_AGENT_MODEL="${GEMMA_GGUF}" -- \
        && has rung '^  name: llamacpp$' && arg rung '-np' && arg rung "${want% *}" \
        && arg rung "$(( ${want% *} * ${want#* } ))"; then
        ok "a guard rung re-renders llama.cpp with that rung's slots and total context"
    else fail "guard rung for llama.cpp (want ${want})" "$(show rung)"; fi
    printf 'some avg10=60.00 avg60=40.00 avg300=5.00 total=1\nfull avg10=9.00 avg60=3.00 avg300=0.50 total=2\n' \
        > "${WORK}/root/proc/pressure/memory"
    printf 'MemTotal:       67108864 kB\nMemAvailable:   20000000 kB\n' > "${WORK}/root/proc/meminfo"
    python3 - "${WORK}/state/k3d-gpu/agent-guard.json" "${WORK}/state/k3d-gpu/agent-fit.json" <<'EOF'
import json, sys
fit = json.load(open(sys.argv[2]))
json.dump({"rung": len(fit["guard"]["rungs"]) - 1, "calm": 0, "hot": 0}, open(sys.argv[1], "w"))
EOF
    out=$(STUB_LOG="${WORK}/kubectl.log" bash "${K3D_GPU}" agent guard --once 2>&1)
    if grep -q '^kubectl -n k3d-gpu-agent scale deployment/llamacpp --replicas=0' "${WORK}/kubectl.log" 2>/dev/null; then
        ok "agent guard: an emergency stops deployment/llamacpp, the server the fit recorded"
    else fail "agent guard targets llamacpp" "${out}"; fi
else fail "agent up's fit records the llama.cpp fit" "$(show rec)"; fi

# --- free-card pinning: `up` on four B70s, card0 and card1 filled by host processes --------
UP="${WORK}/up"
SR="${UP}/sysroot"
mkdir -p "${UP}/bin" "${UP}/home"
cp -a "${ROOT}/tests/fixtures/battlemage-b580/sysroot" "${SR}"
rm -f "${SR}/sys/class/drm/card0" "${SR}/sys/class/drm/renderD128" "${SR}/dev/dri/card0" "${SR}/dev/dri/renderD128"
rm -rf "${SR}/sys/devices/pci0000:00/0000:00:01.1/0000:03:00.0" "${SR}/sys/bus/pci/devices/0000:03:00.0"
i=0
for bdf in 0000:18:00.0 0000:3a:00.0 0000:9a:00.0 0000:bc:00.0; do
    dev="${SR}/sys/devices/pci0000:00/0000:00:01.$((i + 1))/${bdf}"
    mkdir -p "${dev}/drm/card${i}" "${dev}/drm/renderD$((128 + i))"
    printf '0xe223\n' > "${dev}/device"; printf '0x8086\n' > "${dev}/vendor"; printf '0x030000\n' > "${dev}/class"
    ln -s ../../../../bus/pci/drivers/xe "${dev}/driver"
    ln -s ../.. "${dev}/drm/card${i}/device"; ln -s ../.. "${dev}/drm/renderD$((128 + i))/device"
    ln -s "../../devices/pci0000:00/0000:00:01.$((i + 1))/${bdf}/drm/card${i}" "${SR}/sys/class/drm/card${i}"
    ln -s "../../devices/pci0000:00/0000:00:01.$((i + 1))/${bdf}/drm/renderD$((128 + i))" "${SR}/sys/class/drm/renderD$((128 + i))"
    ln -s "../../../devices/pci0000:00/0000:00:01.$((i + 1))/${bdf}" "${SR}/sys/bus/pci/devices/${bdf}"
    : > "${SR}/dev/dri/card${i}"; : > "${SR}/dev/dri/renderD$((128 + i))"
    i=$((i + 1))
done
cat > "${UP}/bin/k3d" <<'EOF'
#!/bin/bash
[ "$1" = version ] && { echo "k3d version v0.0.0-stub"; exit 0; }
echo "k3d $*" >> "${STUB_LOG}"
EOF
cat > "${UP}/bin/kubectl" <<'EOF'
#!/bin/bash
echo "kubectl $*" >> "${STUB_LOG}"
mf="${STUB_LOG%/*}/intel-gpu-plugin.yaml"
case "$*" in
    "get nodes -o go-template="*) printf 'cpu 64\npods 110\ngpu.intel.com/xe %s\n' "${STUB_XE:-4}" ;;
    *"get daemonset intel-gpu-plugin -o jsonpath={.spec.template.spec.volumes[*].name}")
        [ -f "${mf}" ] && sed -n 's/^        - name: \(.*\)$/\1/p' "${mf}" | paste -sd' ' - ;;
    "get nodes -l "*" -o name") echo "node/k3d-gpu-server-0" ;;
esac
exit 0
EOF
cat > "${UP}/bin/docker" <<'EOF'
#!/bin/bash
mf="${STUB_LOG%/*}/intel-gpu-plugin.yaml"
if [ "$1" = exec ]; then
    case "$*" in
        *" cat /var/lib/rancher/k3s/server/manifests/intel-gpu-plugin.yaml") cat "${K3D_GPU_SHARE}/intel-gpu-plugin.yml" ;;
        "exec -i "*" sh -c "*"intel-gpu-plugin.yaml"*) cat > "${mf}" ;;
    esac
fi
exit 0
EOF
chmod +x "${UP}/bin/k3d" "${UP}/bin/kubectl" "${UP}/bin/docker"
up() {
    rm -f "${UP}/calls" "${UP}/intel-gpu-plugin.yaml"
    env HOME="${UP}/home" PATH="${UP}/bin:${PATH}" K3D_GPU_SYSROOT="${SR}" K3D_GPU_SHARE="${ROOT}/share" \
        STUB_LOG="${UP}/calls" K3D_GPU_FIT_HOST="${XEON}" K3D_GPU_VENDORS=intel K3D_GPU_INTEL_APPLY_TIMEOUT=5 "$@" \
        bash "${K3D_GPU}" up > "${UP}/out" 2>&1
}
mf="${UP}/intel-gpu-plugin.yaml"
if up STUB_XE=2 && [ -f "${mf}" ] \
    && grep -q 'Intel GPU card0 (0000:18:00.0) is busy: vllm\[4101\]' "${UP}/out" \
    && grep -q 'Intel GPU card1 (0000:3a:00.0) is busy: llama-server\[4377\]' "${UP}/out" \
    && grep -q '^              mountPath: /dev/dri/card2$' "${mf}" && grep -q '^              mountPath: /dev/dri/renderD131$' "${mf}" \
    && grep -q '^            path: /dev/dri/card3$' "${mf}" && grep -q '^            type: CharDevice$' "${mf}" \
    && grep -q '^              mountPath: /dev/dri/by-path$' "${mf}" \
    && ! grep -qE '/dev/dri/(card0|card1|renderD128|renderD129)$' "${mf}" && ! grep -q '^            path: /dev/dri$' "${mf}" \
    && grep -q '^              mountPath: /sys/class/drm$' "${mf}" && ! grep -q 'expected' "${UP}/out"; then
    ok "up on 4x B70 with 2 busy: the plugin mounts only card2 and card3's nodes, so it advertises those two"
else fail "up pins the free cards" "$(cat "${UP}/out"; cat "${mf}" 2>/dev/null | sed -n '/volumeMounts/,$p')"; fi
if up K3D_GPU_INTEL_CARDS=all && [ ! -f "${mf}" ] && ! grep -q 'is busy' "${UP}/out"; then
    ok "K3D_GPU_INTEL_CARDS=all leaves the plugin's /dev/dri mount as it is"
else fail "K3D_GPU_INTEL_CARDS=all" "$(cat "${UP}/out")"; fi
if ! up K3D_GPU_INTEL_CARDS=some && grep -q 'K3D_GPU_INTEL_CARDS must be auto or all' "${UP}/out"; then
    ok "an unknown K3D_GPU_INTEL_CARDS is refused"
else fail "K3D_GPU_INTEL_CARDS validation" "$(cat "${UP}/out")"; fi
if up K3D_GPU_FIT_HOST="${DESK}" && [ ! -f "${mf}" ]; then
    ok "no busy card: the plugin manifest is left as baked"
else fail "no busy card leaves the plugin alone" "$(cat "${UP}/out")"; fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
