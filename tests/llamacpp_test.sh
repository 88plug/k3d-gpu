#!/usr/bin/env bash
# The llama.cpp agent backend and free-card pinning, offline. `agent manifest` renders
# against the fit fixtures (tests/fixtures/fit: hosts through K3D_GPU_FIT_HOST, model
# metadata linked into a scratch K3D_GPU_FIT_CACHE) with a stubbed kubectl:
#   - Xeon 6741P + 4x Arc Pro B70, card0 and card1 filled by host processes: the QAT GGUF
#     on one free B70 (SYCL image, the fit's -c/-np/-ngl, --jinja, --reasoning off,
#     download pinned to the GGUF's commit, no -hf), the split GGUF the fit picks in a repo
#     with several (or --file names), Qwen3.8-Flash-Next with experts and n-gram
#     table in DRAM (--n-cpu-moe, -ts, two B70s, a one-time conversion Job and the pod
#     waiting for it), the FP8 checkpoint converted with --fp8-as-q8, the AutoRound W4A16
#     checkpoint refused (the converter cannot read it), Gemma 4 safetensors on vLLM TP2;
#   - a desktop with no GPU: the QAT GGUF on the CPU image, P-core threads, mlock;
#   - MTP only on request: Gemma's QAT drafter converted to Q4_0, Flash-Next's own head;
#   - a backend the user set is not overridden; the guard re-renders llama.cpp at a rung;
#   - agent up after the fit switched the backend warms up and names the served model.
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
while [ "${1:-}" = --context ]; do shift 2; done
echo "kubectl $*" >> "${STUB_LOG:-/dev/null}"
case "$*" in
    *allocatable*) printf '%s\n' "${STUB_ALLOC:-}" ;;
    *"jsonpath={.spec.replicas}"*) printf '%s' "${STUB_REPLICAS:-}" ;;
    "get nodes -l gpu.intel.com/xmx=true -o name") [ -z "${STUB_XMX:-}" ] || echo node/k3d-gpu-server-0 ;;
    "get nodes -l k3d-gpu.io/cores-hybrid=true -o name") [ -z "${STUB_HYBRID:-}" ] || echo node/k3d-gpu-server-0 ;;
    "apply -f -") cat > "${STUB_APPLIED:-/dev/null}" ;;
    *'@.type=="Complete"'*) printf '%s' "${STUB_JOB:-}" ;;
    "create --raw "*chat/completions*) cat > "${STUB_BODY:-/dev/null}"; printf '%s' "${STUB_REPLY:-}" ;;
    *chat/completions*) printf '%s' "${STUB_REPLY:-}" ;;
esac
exit 0
EOF
chmod +x "${WORK}/bin/kubectl"
# The fit metadata under main and under its pinned commit (the revision the optimizer names).
for d in "${FIX}"/models/*/; do
    n=$(basename "${d}")
    ln -s "${d%/}" "${WORK}/cache/${n}@main"
    rev=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("source", "").partition("@")[2])' "${d}/weights.json")
    [ -z "${rev}" ] || ln -s "${d%/}" "${WORK}/cache/${n}@${rev}"
done
# The model optimizer (agent up --model) reads tests/fixtures/hf, never the network.
export K3D_GPU_SYSROOT="${WORK}/root" K3D_GPU_FIT_CACHE="${WORK}/cache" XDG_STATE_HOME="${WORK}/state" \
    XDG_CACHE_HOME="${WORK}/xdg" K3D_GPU_HF_FIXTURES="${ROOT}/tests/fixtures/hf" \
    K3D_GPU_AUTOQUANT_CACHE="${WORK}/no-autoquant.json" PATH="${WORK}/bin:${PATH}"
unset K3D_GPU_AGENT_OPT_MODEL K3D_GPU_AGENT_MODEL K3D_GPU_AGENT_BACKEND K3D_GPU_AGENT_DEVICE K3D_GPU_AGENT_DRAFT \
    K3D_GPU_AGENT_VARIANT K3D_GPU_AGENT_THINKING K3D_GPU_MODEL_OPT K3D_GPU_FIT
XEON="${FIX}/hosts/xeon6741p-4xb70.json"
DESK="${FIX}/hosts/desktop-i9-nogpu.json"
# The Xeon's cluster after `up` pinned the free cards: two B70s advertised, XMX labelled.
XEON_ENV=(STUB_XMX=1 STUB_ALLOC=$'cpu 64\ngpu.intel.com/xe 2' K3D_GPU_FIT_HOST="${XEON}")
SYCL=sha256:068cd41cdc292bed89b4a6b7740ea7a33df8ba26bf880d0e12ac75972dce1700
CPUSRV=sha256:8052c13edd987de8858f8150031e025ac3d66823f09cf3ec8220899c4abce220
FULL=sha256:7afc81bfe7a93de287324044fe86ccc70b1b7ac8832ed8e6ea658e11fb448ddf

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
# pair <name> <option> <value>: <value> is the server argument right after <option>.
pair() { server_args "$1" | awk -v o="$2" -v v="$3" 'p && $0 == v { f = 1 } { p = ($0 == o) } END { exit !f }'; }
# llama-server's parser (llama.cpp v0.8.0 common/arg.cpp parse_cli_args, :827-865) takes an
# option's value from the next argument, never from "--opt=value", and refuses a name it does
# not define. llama_argv_ok <name>: the server args parse so, with the names and arities the
# renderer uses (common/arg.cpp v0.8.0: -t :1528, -tb :1538, -c :1650, -ctxcp :1709,
# --cache-ram :1727, -ctk :2449, -ctv :2462, -np :2556, --load-mode :2703, --cpu-moe :2772,
# --n-cpu-moe :2779, -ngl :2811, -ts :2853, --alias :3033, --model-url :3066, --host :3343,
# --port :3359, --jinja :3727, --reasoning :3746, --spec-draft-model :4322, --spec-type :4330,
# --model :3057).
llama_argv_ok() {
    server_args "$1" | python3 -c 'import sys
arity = {"--model": 1, "--model-url": 1, "--alias": 1, "--host": 1, "--port": 1, "--jinja": 0,
         "--reasoning": 1, "-c": 1, "-np": 1, "-ngl": 1, "-ts": 1, "--n-cpu-moe": 1, "--cpu-moe": 0,
         "-ctk": 1, "-ctv": 1, "--threads": 1, "--threads-batch": 1, "--load-mode": 1, "--cache-ram": 1, "-ctxcp": 1,
         "--spec-type": 1, "--spec-draft-model": 1}
argv = sys.stdin.read().split("\n")[:-1]
i = 0
while i < len(argv):
    if argv[i] not in arity:
        sys.exit("invalid argument: %s" % argv[i])
    if arity[argv[i]] and i + 1 >= len(argv):
        sys.exit("expected value for argument %s" % argv[i])
    i += 1 + arity[argv[i]]'
}
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
DRAFTER=google__gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant@9537141506fe8875b3ed45b264af13580cb29166
if render gguf "${XEON_ENV[@]}" -- --model "${GEMMA_GGUF}" \
    && has gguf '^  name: llamacpp$' && has gguf "image: ghcr.io/ggml-org/llama.cpp:server-intel-b11960@${SYCL}" \
    && pair gguf --model "/models/gguf/google__gemma-4-26B-A4B-it-qat-q4_0-gguf@${GREV}/gemma-4-26B_q4_0-it.gguf" \
    && pair gguf --model-url "https://huggingface.co/${GEMMA_GGUF}/resolve/${GREV}/gemma-4-26B_q4_0-it.gguf" \
    && pair gguf --alias 'gemma-4-26B-A4B-it-qat-q4_0-gguf' && arg gguf '--jinja' && pair gguf --reasoning 'off' \
    && pair gguf -c 655360 && pair gguf -np 5 && pair gguf -ngl 999 && pair gguf --cache-ram 0 && pair gguf -ctxcp 4 \
    && pair gguf --port '8000' && server_args gguf | grep -A1 -x -- '--threads' | tail -n1 | grep -qE '^[0-9]+$' \
    && ! arg gguf '-ts' && ! has gguf 'spec-type' && ! has gguf 'kind: Job' && ! has gguf 'load-mode' && ! arg gguf '-hf' \
    && grep -A3 '^            limits:$' "${WORK}/gguf.yaml" | grep -q '^              gpu.intel.com/xe: 1$' \
    && has gguf 'name: ZES_ENABLE_SYSMAN' && has gguf 'k3d-gpu.io/agent-backend: llama.cpp' \
    && has gguf 'path: /health' && has gguf 'gpu.intel.com/xmx: "true"' \
    && grep -qE 'backend llama.cpp, the engine the fit picked|is GGUF, which llama.cpp serves' "${WORK}/gguf.err" && yaml_ok gguf; then
    ok "QAT GGUF on the Xeon + 4x B70 (2 busy): llama-server on one B70, flags from the fit, no flag given"
else fail "QAT GGUF on the Xeon + 4x B70 (2 busy)" "$(show gguf)"; fi

# A GGUF repo named with no backend set goes to llama.cpp before the optimizer runs, even
# when the optimizer has metadata that would remap it to another engine's variant.
if render ggopt "${XEON_ENV[@]}" -- --model "${GEMMA_GGUF}" && has ggopt '^  name: llamacpp$' \
    && grep -q 'is GGUF, which llama.cpp serves' "${WORK}/ggopt.err" && ! grep -q 'OVMS' "${WORK}/ggopt.err"; then
    ok "--model <GGUF repo>: optimized for llama.cpp, the only engine that loads it"
else fail "GGUF repo goes to llama.cpp first" "$(show ggopt)"; fi

# --- the desktop with no GPU: the CPU image on the P-core balloon ------------------------
if render cpu STUB_ALLOC='cpu 32' K3D_GPU_FIT_HOST="${DESK}" -- --model "${GEMMA_GGUF}" \
    && has cpu "image: ghcr.io/ggml-org/llama.cpp:server-b11960@${CPUSRV}" && arg cpu '-ngl' && arg cpu '0' \
    && pair cpu --load-mode 'mmap+mlock' && has cpu 'add: \[IPC_LOCK\]' && ! has cpu 'gpu.intel.com' \
    && ! has cpu 'ZES_ENABLE_SYSMAN' && grep -A1 '^            limits:$' "${WORK}/cpu.yaml" | grep -qE '^              memory: 45Gi$' \
    && pair cpu --threads-batch 16 && pair cpu --threads 8 \
    && yaml_ok cpu; then
    ok "QAT GGUF on the desktop with no GPU: CPU image, -ngl 0, mlock, pod limit and threads from the pod's CPUs"
else fail "QAT GGUF on the CPU-only desktop" "$(show cpu)"; fi

# --- a GGUF repo with several split models (IQ4_NL, Q2_K, Q8_0): the one the fit picked ----
# llama-server downloads the first shard of the GGUF the fit sized, pinned to the commit the
# fit read (--model-url; it fetches the other shards beside --model), and nothing else: no -hf,
# which would fetch the repo's default branch into the container's cache on every start.
FNG=ggml-org/Qwen3.8-Flash-Next-GGUF
FNGREV=91fb00353e766eb43944ed223665ea4fb87dad47
# fng_ok <name> <quant>: --model and --model-url name that quant's first shard, no other quant.
fng_ok() {
    local f="Qwen3.8-Flash-Next-$2-00001-of-00002.gguf"
    pair "$1" --model "/models/gguf/ggml-org__Qwen3.8-Flash-Next-GGUF@${FNGREV}/${f}" \
        && pair "$1" --model-url "https://huggingface.co/${FNG}/resolve/${FNGREV}/${f}" \
        && ! arg "$1" '-hf' && [ "$(server_args "$1" | grep -c -- '-of-00002\.gguf')" -eq 2 ]
}
if render fngq2 "${XEON_ENV[@]}" -- --model "${FNG}" --file Q2_K && fng_ok fngq2 Q2_K \
    && grep -q "fit: ${FNG}:Q2_K on " "${WORK}/fngq2.err" && yaml_ok fngq2; then
    ok "--model ${FNG} --file Q2_K: llama-server downloads the Q2_K split the fit sized, at the fit's commit"
else fail "--file Q2_K renders the Q2_K GGUF" "$(show fngq2)"; fi
if render fng "${XEON_ENV[@]}" -- --model "${FNG}" && fng_ok fng IQ4_NL && grep -q "fit: ${FNG}:IQ4_NL on " "${WORK}/fng.err"; then
    ok "--model ${FNG}: the IQ4_NL split the fit picks with no flag"
else fail "the fit's GGUF pick reaches the render" "$(show fng)"; fi
if render fngoff "${XEON_ENV[@]}" K3D_GPU_FIT=off K3D_GPU_AGENT_BACKEND=llama.cpp -- --model "${FNG}" && fng_ok fngoff IQ4_NL; then
    ok "K3D_GPU_FIT=off: the metadata's first GGUF (the int4 IQ4_NL split) is served"
else fail "GGUF without the fit" "$(show fngoff)"; fi

# --- Qwen3.8-Flash-Next: experts and n-gram table in DRAM, converted once ----------------
FN=Qwen/Qwen3.8-Flash-Next
FNREV=de4b8e4d43b917e7706784d8bb445c9af86a3540
if render fn "${XEON_ENV[@]}" -- --model "${FN}" \
    && pair fn --model "/models/gguf/Qwen__Qwen3.8-Flash-Next@${FNREV}/model.gguf" && ! has fn 'model-url' \
    && pair fn --n-cpu-moe 45 && pair fn -ts 39,11 && pair fn -np 4 && pair fn --cache-ram 0 && pair fn -ctxcp 4 \
    && grep -A3 '^            limits:$' "${WORK}/fn.yaml" | grep -q '^              gpu.intel.com/xe: 2$' \
    && has fn 'kind: Job' && has fn "image: ghcr.io/ggml-org/llama.cpp:full-b11960@${FULL}" \
    && has fn '^            - "Qwen/Qwen3.8-Flash-Next"$' && has fn "^            - \"${FNREV}\"\$" \
    && has fn '^            - "--outtype"$' && has fn '^            - "bf16"$' && ! has fn 'fp8-as-q8' \
    && has fn 'value: "transformers==5.21.0 ' && has fn 'name: wait-gguf' \
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
    && grep -q 'does not read quant_method auto-round (conversion/base.py:575)' "${WORK}/w4.err"; then
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
    env CV_LOG="${CV}/log" REAL_PY="$(command -v python3)" GGUF_CONVERT_PIP="transformers==5.21.0" PATH="${CV}/bin:${PATH}" \
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

# The Job's name covers its whole spec: a Job's pod template cannot change, so another proxy
# (or image, pins, memory) is another Job, and the same render is the same Job.
jobname() { grep -m1 -oE '^  name: gguf-convert-[0-9a-f]+$' "${WORK}/$1.yaml" | cut -d' ' -f4; }
if render fn2 "${XEON_ENV[@]}" -- --model "${FN}" && render fnp "${XEON_ENV[@]}" HTTPS_PROXY=http://proxy.example:3128 -- --model "${FN}" \
    && [ -n "$(jobname fn)" ] && [ "$(jobname fn)" = "$(jobname fn2)" ] && [ "$(jobname fn)" != "$(jobname fnp)" ] \
    && has fnp 'value: "http://proxy.example:3128"'; then
    ok "conversion Job: named for its whole spec, the same on a re-render, another with a proxy"
else fail "conversion Job name" "$(jobname fn) $(jobname fn2) $(jobname fnp)"; fi

# --- agent up: a render that fails after the fit stopped the running server restarts it ----
rm -f "${WORK}/kubectl.log"
out=$(env STUB_LOG="${WORK}/kubectl.log" STUB_REPLICAS=1 "${XEON_ENV[@]}" \
    bash "${K3D_GPU}" agent up --model Intel/Qwen3.8-Flash-Next-W4A16-AutoRound 2>&1); rc=$?
if [ "${rc}" -ne 0 ] && grep -q 'auto-round' <<< "${out}" \
    && grep -q '^kubectl -n k3d-gpu-agent scale deployment/ovms --replicas=0' "${WORK}/kubectl.log" \
    && grep -q '^kubectl -n k3d-gpu-agent scale deployment/ovms --replicas=1' "${WORK}/kubectl.log" \
    && ! grep -q '^kubectl apply' "${WORK}/kubectl.log"; then
    ok "agent up: a model refused after the fit's probe gets the stopped server back, and nothing is applied"
else fail "agent up restores the stopped server (rc ${rc})" "${out}$(cat "${WORK}/kubectl.log")"; fi

# --- agent up on llama.cpp: applied, warmed up, and the GGUF it serves named -----------------
rm -f "${WORK}/kubectl.log"
out=$(env STUB_LOG="${WORK}/kubectl.log" STUB_APPLIED="${WORK}/up.yaml" K3D_GPU_AGENT_GUARD=off "${XEON_ENV[@]}" \
    STUB_REPLY='{"choices":[{"message":{"role":"assistant","content":"A language model."}}]}' \
    bash "${K3D_GPU}" agent up --model "${GEMMA_GGUF}" 2>&1); rc=$?
if [ "${rc}" -eq 0 ] && grep -q '^kubectl apply -f -' "${WORK}/kubectl.log" && grep -q '^  name: llamacpp$' "${WORK}/up.yaml" \
    && grep -q '^kubectl -n k3d-gpu-agent rollout status deployment/llamacpp' "${WORK}/kubectl.log" \
    && grep -q 'warm-up reply ([0-9]*s): A language model.' <<< "${out}" \
    && grep -qF "model ${GEMMA_GGUF}: https://huggingface.co/${GEMMA_GGUF}/resolve/${GREV}/gemma-4-26B_q4_0-it.gguf" <<< "${out}"; then
    ok "agent up --model <QAT GGUF>: llama-server applied and warmed up, the GGUF it downloads named"
else fail "agent up on llama.cpp (rc ${rc})" "${out}"; fi

# --- agent up where the fit switches the backend inside the render's subshell --------------
# The parent then names, and warms up, the model the manifest serves under its own name, not
# the default model of the backend the fit switched to.
# up_served <name> [env...] -- [agent args...]: agent up with any conversion Job Complete; the
# manifest in <name>.yaml, the warm-up request body in <name>.body, the output in <name>.out.
up_served() {
    local name=$1; shift
    local -a envs=()
    while [ "$1" != -- ]; do envs+=("$1"); shift; done
    shift
    rm -f "${WORK}/${name}.body"
    env STUB_APPLIED="${WORK}/${name}.yaml" STUB_BODY="${WORK}/${name}.body" STUB_JOB=True K3D_GPU_AGENT_GUARD=off \
        STUB_REPLY='{"choices":[{"message":{"role":"assistant","content":"A language model."}}]}' \
        ${envs[@]+"${envs[@]}"} bash "${K3D_GPU}" agent up "$@" > "${WORK}/${name}.out" 2>&1
}
# served_name <name>: the name the manifest's server answers to (llama-server --alias, vLLM
# --served-model-name).
served_name() {
    server_args "$1" | awk 'p { print; exit } { p = ($0 == "--alias") }'
    grep -m1 -oE -- '--served-model-name=[^[:space:]"]+' "${WORK}/$1.yaml" | cut -d= -f2-
}
body_model() { python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["model"])' "${WORK}/$1.body" 2>/dev/null; }
# served_ok <name> <app> <model id> <model line>: the manifest's server is <app>, and the
# warm-up body, the 'OpenAI API ready' line and the model line all name what it serves.
served_ok() {
    local n=$1 app=$2 id=$3 line=$4 name
    name=$(served_name "${n}")
    [ -n "${name}" ] && grep -q "^  name: ${app}\$" "${WORK}/${n}.yaml" \
        && [ "$(body_model "${n}")" = "${name}" ] \
        && grep -qF "OpenAI API ready, model '${name}':" "${WORK}/${n}.out" \
        && grep -qF "\"model\":\"${name}\"" "${WORK}/${n}.out" \
        && grep -qF "model ${id}${line}" "${WORK}/${n}.out"
}
if up_served upfn "${XEON_ENV[@]}" -- --model "${FN}" \
    && served_ok upfn llamacpp "${FN}" ": /models/gguf/Qwen__Qwen3.8-Flash-Next@${FNREV}/model.gguf" \
    && [ "$(served_name upfn)" = Qwen3.8-Flash-Next ]; then
    ok "agent up --model ${FN} (OVMS -> llama.cpp in the fit): warmed up and named as the manifest's --alias"
else fail "agent up --model ${FN} names the served model" "$(cat "${WORK}/upfn.out"; echo "body: $(cat "${WORK}/upfn.body" 2>/dev/null)")"; fi
if up_served upst "${XEON_ENV[@]}" K3D_GPU_MODEL_OPT=off -- --model google/gemma-4-26B-A4B-it \
    && served_ok upst vllm google/gemma-4-26B-A4B-it " at revision " \
    && [ "$(served_name upst)" = gemma-4-26B-A4B-it ]; then
    ok "K3D_GPU_MODEL_OPT=off agent up --model google/gemma-4-26B-A4B-it (OVMS -> vLLM TP2 in the fit): warmed up and named as --served-model-name"
else fail "K3D_GPU_MODEL_OPT=off agent up names the served model" "$(cat "${WORK}/upst.out"; echo "body: $(cat "${WORK}/upst.body" 2>/dev/null)")"; fi

rm -f "${WORK}/kubectl.log"
if STUB_LOG="${WORK}/kubectl.log" bash "${K3D_GPU}" agent down > /dev/null 2>&1 \
    && grep -q '^kubectl -n k3d-gpu-agent delete deployment/llamacpp service/llamacpp' "${WORK}/kubectl.log" \
    && grep -q '^kubectl -n k3d-gpu-agent delete job -l app=gguf-convert --field-selector status.successful=0' "${WORK}/kubectl.log"; then
    ok "agent down: removes llamacpp and any unfinished conversion Job, keeps the GGUFs"
else fail "agent down" "$(cat "${WORK}/kubectl.log")"; fi

if render img "${XEON_ENV[@]}" K3D_GPU_AGENT_IMAGE=openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58 \
        -- --model "${GEMMA_GGUF}" \
    && has img "image: ghcr.io/ggml-org/llama.cpp:server-intel-b11960@${SYCL}" && grep -q 'K3D_GPU_AGENT_IMAGE is not used' "${WORK}/img.err"; then
    ok "an image set for the default backend is not handed to the backend the fit picked"
else fail "K3D_GPU_AGENT_IMAGE on a followed backend" "$(show img)"; fi
if ! render auto "${XEON_ENV[@]}" K3D_GPU_AGENT_DRAFT=auto -- --model "${GEMMA_GGUF}" \
    && grep -q 'does not apply to llama.cpp' "${WORK}/auto.err"; then
    ok "K3D_GPU_AGENT_DRAFT=auto (an OVMS setting) does not turn MTP on"
else fail "K3D_GPU_AGENT_DRAFT=auto on llama.cpp" "$(show auto)"; fi

# --- H58's command: the optimizer picks Gemma 4's OpenVINO IR, the fit one free B70 -------
if render h58 "${XEON_ENV[@]}" -- --model google/gemma-4-26B-A4B-it \
    && has h58 '^  name: ovms$' && has h58 '--source_model=OpenVINO/gemma-4-26b-a4b-it-int4-ov' \
    && has h58 '--target_device=GPU' && has h58 'k3d-gpu.io/agent-fit: "gpu ' \
    && grep -A3 '^            limits:$' "${WORK}/h58.yaml" | grep -q '^              gpu.intel.com/xe: 1$'; then
    ok "agent up --model google/gemma-4-26B-A4B-it on the Xeon (2 B70s busy): OVMS, OpenVINO int4 IR, one free B70"
else fail "H58's Gemma 4 command" "$(show h58)"; fi
# K3D_GPU_AGENT_BACKEND=llama.cpp: the optimizer picks the QAT GGUF for llama.cpp and the
# fit sizes it; with K3D_GPU_AGENT_DRAFT=mtp the QAT assistant is converted as its drafter.
if render optl "${XEON_ENV[@]}" K3D_GPU_AGENT_BACKEND=llama.cpp K3D_GPU_AGENT_DRAFT=mtp -- --model google/gemma-4-26B-A4B-it \
    && has optl '^  name: llamacpp$' && pair optl --model-url "https://huggingface.co/${GEMMA_GGUF}/resolve/${GREV}/gemma-4-26B_q4_0-it.gguf" \
    && pair optl --alias 'gemma-4-26B-A4B-it' && pair optl --spec-type 'draft-mtp' \
    && pair optl --spec-draft-model "/models/gguf/${DRAFTER}/model.gguf" && has optl 'k3d-gpu.io/agent-fit: "gpu ' \
    && grep -q 'picked google/gemma-4-26B-A4B-it-qat-q4_0-gguf on llama.cpp' "${WORK}/optl.err"; then
    ok "K3D_GPU_AGENT_BACKEND=llama.cpp --model google/gemma-4-26B-A4B-it: the optimizer's QAT GGUF, sized by the fit, with the QAT MTP drafter"
else fail "optimizer pick into llama-server" "$(show optl)"; fi

# The safetensors as given (no optimizer): the fit picks vLLM, tensor parallel over the two free B70s.
if render st "${XEON_ENV[@]}" K3D_GPU_AGENT_MODEL=google/gemma-4-26B-A4B-it -- \
    && has st '^  name: vllm$' && has st '--tensor-parallel-size=2' \
    && grep -A3 '^            limits:$' "${WORK}/st.yaml" | grep -q '^              gpu.intel.com/xe: 2$'; then
    ok "Gemma 4 safetensors as given: the backend follows the fit to vLLM tensor parallel on the two free B70s"
else fail "Gemma 4 safetensors follows the fit to vLLM" "$(show st)"; fi

# The fit sizes vLLM's CPU build only for EAT; the agent has no such backend.
if ! render vcpu STUB_ALLOC='cpu 64' K3D_GPU_FIT_HOST="${XEON}" K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_MODEL=RedHatAI/Qwen3.8-27B-INT4 -- \
    && grep -q 'vLLM on the CPU is not an agent backend' "${WORK}/vcpu.err"; then
    ok "a fit that picks vLLM's CPU build is refused, naming the variants the agent serves"
else fail "vLLM CPU fit refused" "$(show vcpu)"; fi

# --- a backend the user set is kept ----------------------------------------------------
if ! render set "${XEON_ENV[@]}" K3D_GPU_AGENT_BACKEND=ovms K3D_GPU_AGENT_MODEL="${GEMMA_GGUF}" -- \
    && grep -q 'K3D_GPU_AGENT_BACKEND=ovms cannot serve it: run llama-server with' "${WORK}/set.err"; then
    ok "K3D_GPU_AGENT_BACKEND=ovms is not overridden by the fit: refused with the llama-server flags"
else fail "an explicit backend is kept" "$(show set)"; fi
if render setl "${XEON_ENV[@]}" K3D_GPU_AGENT_BACKEND=llama.cpp -- --model "${GEMMA_GGUF}" && has setl '^  name: llamacpp$'; then
    ok "K3D_GPU_AGENT_BACKEND=llama.cpp serves the GGUF"
else fail "K3D_GPU_AGENT_BACKEND=llama.cpp" "$(show setl)"; fi
# K3D_GPU_AGENT_GPUS is the fit's --gpus for llama.cpp as for vLLM (an OVMS pod holds one
# GPU): the QAT GGUF, one card's model, is split layer by layer over the two free B70s asked.
if render setl2 "${XEON_ENV[@]}" K3D_GPU_AGENT_BACKEND=llama.cpp K3D_GPU_AGENT_GPUS=2 -- --model "${GEMMA_GGUF}" \
    && grep -q "fit: ${GEMMA_GGUF}:Q4_0 on 2 GPUs, layer split via llama.cpp" "${WORK}/setl2.err" \
    && server_args setl2 | grep -A1 -x -- '-ts' | tail -n1 | grep -qE '^[0-9]+,[0-9]+$' \
    && grep -A3 '^            limits:$' "${WORK}/setl2.yaml" | grep -q '^              gpu.intel.com/xe: 2$'; then
    ok "K3D_GPU_AGENT_BACKEND=llama.cpp K3D_GPU_AGENT_GPUS=2: the fit splits the GGUF over two B70s, -ts in layers"
else fail "K3D_GPU_AGENT_GPUS=2 reaches the llama.cpp fit" "$(show setl2)"; fi

# --- MTP on request ---------------------------------------------------------------------
if render mtp "${XEON_ENV[@]}" K3D_GPU_AGENT_DRAFT=mtp -- --model "${GEMMA_GGUF}" \
    && pair mtp --spec-type 'draft-mtp' && pair mtp --spec-draft-model "/models/gguf/${DRAFTER}/model.gguf" \
    && has mtp '^            - "Q4_0"$' && has mtp '^            - "google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant"$' \
    && has mtp "for d in /models/gguf/${DRAFTER}; do" && ! has mtp 'spec-draft-n-max'; then
    ok "K3D_GPU_AGENT_DRAFT=mtp on the QAT GGUF: its QAT assistant converted once to Q4_0, default draft length"
else fail "MTP drafter for the QAT GGUF" "$(show mtp 200)"; fi
if render fnmtp "${XEON_ENV[@]}" K3D_GPU_AGENT_DRAFT=mtp -- --model "${FN}" \
    && pair fnmtp --spec-type 'draft-mtp' && ! has fnmtp 'spec-draft-model'; then
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

# --- the ledger picks llama.cpp on the CPU (model_opt.py H59 and H57m families) ----------
# The CPU class is the model name in the sysroot's /proc/cpuinfo; the ledger a stub.
LG="${WORK}/ledger"
mkdir -p "${LG}"
h59() { printf '## Hypotheses\n\n| H59 | Engine choice on the Xeon 6741P: llama.cpp vs OVMS int4 | p | m | %s |\n' "$1" > "${LG}/h59-$1.md"; }
h59 SUPPORTED; h59 INCONCLUSIVE; h59 PENDING; h59 REFUTED
printf '## Hypotheses\n\n| H57da | On the i9-13900T OVMS is the fastest arm | p | m | INCONCLUSIVE |\n| H57dm | On the i9-13900T Ld-mtp beats Ld | p | m | %s |\n' \
    SUPPORTED > "${LG}/h57-SUPPORTED.md"
python3 "${ROOT}/console/model_opt.py" ledger-rules --ledger "${LG}/h59-SUPPORTED.md" --out "${LG}/rules.json"
printf 'processor\t: 0\nmodel name\t: Intel(R) Xeon(R) 6741P\n' > "${WORK}/root/proc/cpuinfo"
XCPU=(STUB_ALLOC='cpu 64' K3D_GPU_FIT_HOST="${XEON}")
if render l59 "${XCPU[@]}" K3D_GPU_MODEL_OPT_LEDGER="${LG}/h59-SUPPORTED.md" -- --model google/gemma-4-26B-A4B-it \
    && has l59 '^  name: llamacpp$' && has l59 "image: ghcr.io/ggml-org/llama.cpp:server-b11960@${CPUSRV}" \
    && pair l59 --model-url "https://huggingface.co/${GEMMA_GGUF}/resolve/${GREV}/gemma-4-26B_q4_0-it.gguf" \
    && pair l59 --alias 'gemma-4-26B-A4B-it' && pair l59 --spec-type 'draft-mtp' \
    && pair l59 --spec-draft-model "/models/gguf/${DRAFTER}/model.gguf" && has l59 'kind: Job' \
    && pair l59 --load-mode 'mmap+mlock' && has l59 'k3d-gpu.io/agent-fit: "cpu ' \
    && grep -q "llama.cpp is this CPU class's engine in EXPERIMENTS.md" "${WORK}/l59.err" \
    && grep -q 'with the google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant drafter' "${WORK}/l59.err" && yaml_ok l59; then
    ok "H59 SUPPORTED for this CPU class: --model google/gemma-4-26B-A4B-it on the CPU is llama.cpp, the QAT GGUF with the QAT MTP drafter"
else fail "the ledger's CPU engine (H59 SUPPORTED)" "$(show l59)"; fi
if render l59j "${XCPU[@]}" K3D_GPU_MODEL_OPT_LEDGER="${LG}/rules.json" -- --model google/gemma-4-26B-A4B-it \
    && has l59j '^  name: llamacpp$' && pair l59j --spec-type 'draft-mtp'; then
    ok "packaged install: console/ledger-rules.json's H59 rows pick the CPU engine as EXPERIMENTS.md does"
else fail "the ledger's CPU engine from ledger-rules.json" "$(show l59j)"; fi
for v in INCONCLUSIVE PENDING REFUTED; do
    if render "l59${v}" "${XCPU[@]}" K3D_GPU_MODEL_OPT_LEDGER="${LG}/h59-${v}.md" -- --model google/gemma-4-26B-A4B-it \
        && has "l59${v}" '^  name: ovms$' && has "l59${v}" '--source_model=OpenVINO/gemma-4-26b-a4b-it-int4-ov' \
        && ! has "l59${v}" 'llamacpp'; then
        ok "H59 ${v}: OVMS stays the CPU engine"
    else fail "H59 ${v} keeps OVMS" "$(show "l59${v}")"; fi
done
if render l59o "${XCPU[@]}" K3D_GPU_MODEL_OPT_LEDGER="${LG}/h59-SUPPORTED.md" K3D_GPU_AGENT_BACKEND=ovms -- --model google/gemma-4-26B-A4B-it \
    && has l59o '^  name: ovms$' && has l59o '--source_model=OpenVINO/gemma-4-26b-a4b-it-int4-ov'; then
    ok "H59 SUPPORTED but K3D_GPU_AGENT_BACKEND=ovms: the backend set is kept"
else fail "an explicit backend over the ledger's CPU engine" "$(show l59o)"; fi
# A variant named with K3D_GPU_AGENT_VARIANT (an OVMS IR, or the QAT conversion, which
# serves its fallback IR until it is built) is served by OVMS as without the row.
if render l59v "${XCPU[@]}" K3D_GPU_MODEL_OPT_LEDGER="${LG}/h59-SUPPORTED.md" K3D_GPU_AGENT_VARIANT=OpenVINO/gemma-4-26b-a4b-it-int4-ov \
        -- --model google/gemma-4-26B-A4B-it \
    && has l59v '^  name: ovms$' && has l59v '--source_model=OpenVINO/gemma-4-26b-a4b-it-int4-ov' && ! has l59v 'llamacpp' \
    && render l59q "${XCPU[@]}" K3D_GPU_MODEL_OPT_LEDGER="${LG}/h59-SUPPORTED.md" K3D_GPU_AGENT_VARIANT=qat-convert \
        -- --model google/gemma-4-26B-A4B-it \
    && has l59q '^  name: ovms$' && ! has l59q 'llamacpp' && grep -q 'QAT-matched conversion' "${WORK}/l59q.err"; then
    ok "H59 SUPPORTED but K3D_GPU_AGENT_VARIANT names a variant: OVMS serves it"
else fail "a named variant over the ledger's CPU engine" "$(show l59v)$(show l59q)"; fi
# An OVMS Fast Draft device (auto, CPU) asks for OVMS; off and mtp apply to llama.cpp too.
for v in auto CPU; do
    if render "l59d${v}" "${XCPU[@]}" K3D_GPU_MODEL_OPT_LEDGER="${LG}/h59-SUPPORTED.md" K3D_GPU_AGENT_DRAFT="${v}" \
            -- --model google/gemma-4-26B-A4B-it \
        && has "l59d${v}" '^  name: ovms$' && has "l59d${v}" '--draft_source_model=' && ! has "l59d${v}" 'llamacpp'; then
        ok "H59 SUPPORTED but K3D_GPU_AGENT_DRAFT=${v} (an OVMS Fast Draft device): OVMS drafts, rc 0"
    else fail "K3D_GPU_AGENT_DRAFT=${v} over the ledger's CPU engine" "$(show "l59d${v}")"; fi
done
if render l59doff "${XCPU[@]}" K3D_GPU_MODEL_OPT_LEDGER="${LG}/h59-SUPPORTED.md" K3D_GPU_AGENT_DRAFT=off \
        -- --model google/gemma-4-26B-A4B-it \
    && has l59doff '^  name: llamacpp$' && ! has l59doff 'spec-type'; then
    ok "H59 SUPPORTED and K3D_GPU_AGENT_DRAFT=off: llama.cpp, no drafter"
else fail "K3D_GPU_AGENT_DRAFT=off keeps the ledger's CPU engine" "$(show l59doff)"; fi
if render l59g "${XEON_ENV[@]}" K3D_GPU_MODEL_OPT_LEDGER="${LG}/h59-SUPPORTED.md" -- --model google/gemma-4-26B-A4B-it \
    && has l59g '^  name: ovms$' && has l59g '--target_device=GPU'; then
    ok "H59 SUPPORTED is a CPU row: on the free B70s the engine stays OVMS"
else fail "H59 on a GPU placement" "$(show l59g)"; fi
printf 'processor\t: 0\nmodel name\t: 13th Gen Intel(R) Core(TM) i9-13900T\n' > "${WORK}/root/proc/cpuinfo"
DCPU=(STUB_ALLOC='cpu 32' K3D_GPU_FIT_HOST="${DESK}" K3D_GPU_AGENT_BACKEND=llama.cpp K3D_GPU_MODEL_OPT_LEDGER="${LG}/h57-SUPPORTED.md")
if render l57 "${DCPU[@]}" -- --model google/gemma-4-26B-A4B-it && has l57 '^  name: llamacpp$' \
    && pair l57 --model-url "https://huggingface.co/${GEMMA_GGUF}/resolve/${GREV}/gemma-4-26B_q4_0-it.gguf" \
    && pair l57 --spec-type 'draft-mtp' && pair l57 --spec-draft-model "/models/gguf/${DRAFTER}/model.gguf" \
    && render l57off "${DCPU[@]}" K3D_GPU_AGENT_DRAFT=off -- --model google/gemma-4-26B-A4B-it && ! has l57off 'spec-type' \
    && render l57g "${DCPU[@]}" K3D_GPU_MODEL_OPT_LEDGER="${LG}/h59-SUPPORTED.md" -- --model google/gemma-4-26B-A4B-it \
    && has l57g '^  name: llamacpp$' && ! has l57g 'spec-type'; then
    ok "H57dm SUPPORTED for the i9-13900T: llama.cpp on its CPU drafts with the QAT MTP assistant (off without the row, or with K3D_GPU_AGENT_DRAFT=off)"
else fail "the ledger's MTP drafter (H57dm)" "$(show l57)$(show l57off)$(show l57g)"; fi
rm -f "${WORK}/root/proc/cpuinfo"

# Every llama-server render above: its args parse as llama-server's own parser reads them.
bad=""
for n in gguf ggopt cpu fngq2 fng fngoff fn fn8 img optl setl mtp fnmtp rung l59 l59j l59doff l57 l57off l57g; do
    [ -s "${WORK}/${n}.yaml" ] || { bad+=" ${n}(not rendered)"; continue; }
    why=$(llama_argv_ok "${n}" 2>&1) || bad+=" ${n}: ${why}"
done
if [ -z "${bad}" ]; then
    ok "llama-server args: each option, then its value as the next argument, all defined by llama.cpp v0.8.0"
else fail "llama-server args llama.cpp would refuse" "${bad}"; fi

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
while [ "${1:-}" = --context ]; do shift 2; done
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
