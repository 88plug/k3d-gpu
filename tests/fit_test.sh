#!/usr/bin/env bash
# fit and guard through the launcher: `k3d-gpu fit`, the fit `agent manifest` applies
# (vLLM and OVMS memory flags, pod memory limit, refusal), `agent up --model`'s flag
# form, and one `agent guard --once` step under simulated PSI pressure. Runs
# tests/fit_test.py first (the arithmetic itself). The cluster is a stubbed kubectl;
# hosts are tests/fixtures/fit/hosts (K3D_GPU_FIT_HOST), models the fixture
# metadata linked into a scratch K3D_GPU_FIT_CACHE.
#
# Usage: tests/fit_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
K3D_GPU="${ROOT}/scripts/k3d-gpu"
FIX="${ROOT}/tests/fixtures/fit"
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; [ -z "${2:-}" ] || sed 's/^/     | /' <<< "$2"; }

python3 "${ROOT}/tests/fit_test.py" && ok "fit_test.py" || fail "fit_test.py"

WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
mkdir -p "${WORK}/bin" "${WORK}/cache" "${WORK}/state" "${WORK}/root/proc/pressure" "${WORK}/root/sys" "${WORK}/root/dev"
cat > "${WORK}/bin/kubectl" <<'EOF'
#!/bin/sh
while [ "${1:-}" = --context ]; do shift 2; done
echo "kubectl $*" >> "${STUB_LOG:-/dev/null}"
case "$*" in
    *allocatable*) printf '%s\n' "${STUB_ALLOC:-}" ;;
    *"jsonpath={.spec.replicas}"*) printf '%s' "${STUB_REPLICAS:-}" ;;
    "get nodes -l "*)
        for l in ${STUB_NODE_LABELS:-}; do [ "$l" = "$4" ] && echo node/k3d-agent-server-0; done ;;
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
    K3D_GPU_AUTOQUANT_CACHE="${WORK}/no-autoquant.json" PATH="${WORK}/bin:${PATH}" \
    K3D_GPU_HF_FIXTURES="${FIX%/*}/hf" XDG_CACHE_HOME="${WORK}/xdg"
XEON="${FIX}/hosts/xeon6741p-4xb70.json"
DESK="${FIX}/hosts/desktop-i9-nogpu.json"
LAPTOP="${FIX}/hosts/laptop-16g.json"

# --- k3d-gpu fit -------------------------------------------------------------------
out=$(bash "${K3D_GPU}" fit --host "${XEON}" --model google/gemma-4-26B-A4B-it-qat-q4_0-gguf 2>&1); rc=$?
if [ "${rc}" -eq 0 ] && grep -q '^fit: google/gemma-4-26B-A4B-it-qat-q4_0-gguf on 1 GPU via llama.cpp, context' <<< "${out}"; then
    ok "fit: one summary line for a model id, from the cache"
else fail "fit: one summary line for a model id, from the cache (rc ${rc})" "${out}"; fi

out=$(bash "${K3D_GPU}" fit --host "${XEON}" --model Intel/Qwen3.8-Flash-Next-W4A16-AutoRound --explain 2>&1); rc=$?
if [ "${rc}" -eq 0 ] && grep -q 'llama.cpp/moe-offload: FITS' <<< "${out}" && grep -q 'DRAM decode bound' <<< "${out}" \
    && grep -q 'BUSY' <<< "${out}" && grep -q -- '--n-cpu-moe' <<< "${out}"; then
    ok "fit --explain: Flash-Next on the Xeon takes the fast-RAM path, with the arithmetic"
else fail "fit --explain: Flash-Next on the Xeon takes the fast-RAM path" "${out}"; fi

out=$(bash "${K3D_GPU}" fit --host "${LAPTOP}" --model Qwen/Qwen3.8-27B 2>&1); rc=$?
if [ "${rc}" -eq 3 ] && grep -q 'does not fit this host' <<< "${out}"; then
    ok "fit: a model that cannot fit exits 3 with the reason"
else fail "fit: a model that cannot fit exits 3 with the reason (rc ${rc})" "${out}"; fi

out=$(bash "${K3D_GPU}" fit --host "${XEON}" --model "${FIX}/models/RedHatAI__Qwen3.8-27B-INT4" --json 2>&1); rc=$?
if [ "${rc}" -eq 0 ] && python3 -c 'import json, sys; d = json.loads(sys.argv[1]); assert d["schema"] == "k3d-gpu.fit/v1" and d["fits"]' "${out}"; then
    ok "fit --json: a fit/v1 document, from a local model directory"
else fail "fit --json: a fit/v1 document" "${out}"; fi

out=$(bash "${K3D_GPU}" fit --host "${XEON}" --model not/in-cache-offline 2>&1 </dev/null); rc=$?
if [ "${rc}" -ne 0 ] && grep -q 'no metadata for not/in-cache-offline' <<< "${out}"; then
    ok "fit: a model with no metadata is an error, not a guess"
else fail "fit: a model with no metadata is an error" "${out}"; fi

# --- agent manifest with a fit ----------------------------------------------------------
# render <name> [env...] -- [agent args...]: manifest in ${WORK}/<name>.yaml, stderr in .err
render() {
    local name=$1; shift
    local -a envs=()
    while [ "$1" != -- ]; do envs+=("$1"); shift; done
    shift
    env ${envs[@]+"${envs[@]}"} bash "${K3D_GPU}" agent manifest "$@" > "${WORK}/${name}.yaml" 2> "${WORK}/${name}.err"
}
has() { grep -q -- "$2" "${WORK}/$1.yaml"; }

# A GGUF repo with several split models (ggml-org Flash-Next: IQ4_NL, Q2_K, Q8_0): the fit
# picks one with no flag; --file (or K3D_GPU_FIT_FILE) picks another; plan takes --file too.
FLASH=ggml-org/Qwen3.8-Flash-Next-GGUF
out=$(bash "${K3D_GPU}" fit --host "${XEON}" --model "${FLASH}" 2>&1); rc=$?
if [ "${rc}" -eq 0 ] && grep -q "^fit: ${FLASH}:IQ4_NL on 2 GPU(s) + experts and lookup tables in DRAM" <<< "${out}"; then
    ok "fit: a multi-GGUF repo needs no flag (IQ4_NL picked, split shards summed)"
else fail "fit: multi-GGUF repo auto-pick (rc ${rc})" "${out}"; fi
out=$(bash "${K3D_GPU}" fit --host "${XEON}" --model "${FLASH}" --file Qwen3.8-Flash-Next-Q8_0-00002-of-00002.gguf --explain 2>&1); rc=$?
if [ "${rc}" -eq 0 ] && grep -q ':Q8_0 on' <<< "${out}" && grep -q -- "-hf ${FLASH}:Q8_0" <<< "${out}"; then
    ok "fit --file: a shard name picks its whole split model"
else fail "fit --file shard (rc ${rc})" "${out}"; fi
out=$(K3D_GPU_FIT_FILE=Q2_K bash "${K3D_GPU}" fit --host "${XEON}" --model "${FLASH}" 2>&1); rc=$?
if [ "${rc}" -eq 0 ] && grep -q ':Q2_K on' <<< "${out}"; then
    ok "fit: K3D_GPU_FIT_FILE names the quant"
else fail "fit: K3D_GPU_FIT_FILE (rc ${rc})" "${out}"; fi
out=$(bash "${K3D_GPU}" fit --host "${XEON}" --model "${FLASH}" --file Q5_K_M 2>&1); rc=$?
if [ "${rc}" -eq 2 ] && grep -q 'no GGUF group matches' <<< "${out}"; then
    ok "fit --file: a quant the repo does not have is an error"
else fail "fit --file unknown quant (rc ${rc})" "${out}"; fi
out=$(K3D_GPU_MODEL_OPT=off K3D_GPU_FIT_HOST="${XEON}" bash "${K3D_GPU}" plan \
    --facts "${ROOT}/tests/fixtures/battlemage-pro-b70-k616/facts.json" --model "${FLASH}" --file Q2_K 2>&1); rc=$?
if grep -q ':Q2_K on' <<< "${out}"; then
    ok "plan --model M --file F: the fit sizes that GGUF model"
else fail "plan --file (rc ${rc})" "${out}"; fi
if ! render flashq2 STUB_ALLOC=$'cpu 64\ngpu.intel.com/xe 4' K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_FIT_HOST="${XEON}" \
        K3D_GPU_AGENT_MODEL="${FLASH}" -- --file Q2_K \
    && grep -q "run llama-server with -hf ${FLASH}:Q2_K" "${WORK}/flashq2.err"; then
    ok "agent manifest --file: the picked GGUF model reaches the fit (llama-server flags name it)"
else fail "agent manifest --file" "$(cat "${WORK}/flashq2.err")"; fi

# Weights that read as 0 bytes are refused loudly, never planned.
mkdir -p "${WORK}/zero"
cp "${FIX}/models/Qwen__Qwen3.8-Flash-Next/config.json" "${WORK}/zero/"
printf '{"schema": "k3d-gpu.weights/v1", "format": "gguf", "source": "zero", "files": [], "classes": {}, "total_bytes": 0}\n' \
    > "${WORK}/zero/weights.json"
out=$(bash "${K3D_GPU}" fit --host "${XEON}" --model "${WORK}/zero" 2>&1); rc=$?
if [ "${rc}" -eq 3 ] && grep -q 'REFUSED: weights read as 0 bytes' <<< "${out}"; then
    ok "fit: weights that read as 0 bytes are refused"
else fail "fit: zero weights refused (rc ${rc})" "${out}"; fi


if render vllm STUB_ALLOC=$'cpu 64\ngpu.intel.com/xe 4' K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_FIT_HOST="${XEON}" \
        K3D_GPU_AGENT_MODEL=RedHatAI/Qwen3.8-27B-INT4 -- \
    && has vllm '--max-num-seqs=' && has vllm '--gpu-memory-utilization=0\.[0-9]' && has vllm 'k3d-gpu.io/agent-fit: "gpu' \
    && grep -A1 '^            limits:$' "${WORK}/vllm.yaml" | grep -qE '^              memory: [0-9]+Gi$' \
    && ! has vllm '--max-model-len=8192$' && grep -q '^.*fit: RedHatAI/Qwen3.8-27B-INT4 on' "${WORK}/vllm.err"; then
    ok "agent manifest (vllm): context, seqs, GPU share and pod memory limit come from the fit"
else fail "agent manifest (vllm) from the fit" "$(cat "${WORK}/vllm.err" "${WORK}/vllm.yaml" 2>/dev/null | head -80)"; fi

# --model M with the model optimizer off renders what K3D_GPU_AGENT_MODEL=M does; with it
# on (the default) the named variant is served and fitted the same, plus the optimizer's
# arguments (tests/model_opt_test.sh covers those).
if render vllm-flag STUB_ALLOC=$'cpu 64\ngpu.intel.com/xe 4' K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_FIT_HOST="${XEON}" \
        K3D_GPU_MODEL_OPT=off -- --model RedHatAI/Qwen3.8-27B-INT4 \
    && diff -q "${WORK}/vllm.yaml" "${WORK}/vllm-flag.yaml" >/dev/null; then
    ok "agent manifest --model M (optimizer off) renders what K3D_GPU_AGENT_MODEL=M does"
else fail "agent manifest --model M" "$(cat "${WORK}/vllm-flag.err"; diff "${WORK}/vllm.yaml" "${WORK}/vllm-flag.yaml")"; fi
if render vllm-opt STUB_ALLOC=$'cpu 64\ngpu.intel.com/xe 4' K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_FIT_HOST="${XEON}" -- \
        --model RedHatAI/Qwen3.8-27B-INT4 \
    && has vllm-opt '^            - RedHatAI/Qwen3.8-27B-INT4$' \
    && [ "$(grep 'agent-fit:' "${WORK}/vllm.yaml")" = "$(grep 'agent-fit:' "${WORK}/vllm-opt.yaml")" ] \
    && has vllm-opt "'--speculative-config'"; then
    ok "agent manifest --model M (optimizer on): the named variant, the same fit, plus the optimizer's arguments"
else fail "agent manifest --model M (optimizer on)" "$(cat "${WORK}/vllm-opt.err"; diff "${WORK}/vllm.yaml" "${WORK}/vllm-opt.yaml")"; fi

if render ovms STUB_ALLOC='cpu 32' K3D_GPU_FIT_HOST="${DESK}" K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3.8-27B-int4-ov -- \
    && has ovms '--max_num_seqs=' && has ovms '--cache_size=' && has ovms 'k3d-gpu.io/agent-fit: "cpu' \
    && grep -A1 '^            limits:$' "${WORK}/ovms.yaml" | grep -qE '^              memory: [0-9]+Gi$'; then
    ok "agent manifest (ovms, CPU): static KV pool, seqs and pod memory limit from the fit"
else fail "agent manifest (ovms, CPU) from the fit" "$(cat "${WORK}/ovms.err" "${WORK}/ovms.yaml" 2>/dev/null | head -60)"; fi

# OVMS serves one GPU: K3D_GPU_AGENT_GPUS=2 is a vLLM knob, so the OVMS pod (one
# gpu.intel.com/xe) gets a KV pool that fits one card, not two cards' worth.
one_card=$(python3 -c 'import json, sys; sys.path.insert(0, sys.argv[1]); import fit
print(max(c["budget"] for c in fit.gpu_budget(json.load(open(sys.argv[2])))[0]) // fit.GIB)' "${ROOT}/console" "${XEON}")
if render ovms2 STUB_ALLOC=$'cpu 64\ngpu.intel.com/xe 4' STUB_NODE_LABELS=gpu.intel.com/xmx=true K3D_GPU_FIT_HOST="${XEON}" \
        K3D_GPU_AGENT_MODEL=OpenVINO/gemma-4-26b-a4b-it-int4-ov K3D_GPU_AGENT_GPUS=2 -- \
    && cache=$(sed -n 's/^ *- --cache_size=\([0-9]*\)$/\1/p' "${WORK}/ovms2.yaml") && [ -n "${cache}" ] \
    && [ "${cache}" -le "${one_card}" ] && has ovms2 '^              gpu.intel.com/xe: 1$' && ! has ovms2 'gpu-split'; then
    ok "agent manifest (ovms, K3D_GPU_AGENT_GPUS=2): --cache_size=${cache} fits one card's ${one_card} GiB budget"
else fail "agent manifest (ovms, K3D_GPU_AGENT_GPUS=2) sized for one card (cache ${cache:-?}, one card ${one_card})" \
    "$(cat "${WORK}/ovms2.err"; grep -E 'cache_size|agent-fit|gpu.intel.com/xe' "${WORK}/ovms2.yaml")"; fi

if render tier STUB_ALLOC='cpu 32' K3D_GPU_FIT_HOST="${LAPTOP}" K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3.8-27B-int8-ov \
        K3D_GPU_AGENT_MODEL_TIER=1 -- && ! has tier 'agent-fit'; then
    ok "a quickstart tier pick (K3D_GPU_AGENT_MODEL_TIER=1) is not sized: it is no user choice"
else fail "a quickstart tier pick is not sized" "$(cat "${WORK}/tier.err")"; fi

if ! render llama STUB_ALLOC=$'cpu 64\ngpu.intel.com/xe 4' K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_FIT_HOST="${XEON}" \
        K3D_GPU_AGENT_MODEL=Intel/Qwen3.8-Flash-Next-W4A16-AutoRound -- \
    && grep -q 'cannot serve it: run llama-server with .*--n-cpu-moe' "${WORK}/llama.err"; then
    ok "agent manifest: a fit only llama.cpp can serve is refused with the llama-server flags"
else fail "agent manifest: llama.cpp-only fit refused" "$(cat "${WORK}/llama.err")"; fi

# Optimizer off, so plan fits the id as given (with it on, an unknown id keeps the engine's default).
out=$(K3D_GPU_MODEL_OPT=off bash "${K3D_GPU}" plan --facts "${ROOT}/tests/fixtures/battlemage-pro-b70-k616/facts.json" --model not/real-model 2>&1); rc=$?
K3D_GPU_FIT=off bash "${K3D_GPU}" plan --facts "${ROOT}/tests/fixtures/battlemage-pro-b70-k616/facts.json" --model not/real-model \
    > /dev/null 2>&1; rc_off=$?
if [ "${rc}" -eq "${rc_off}" ] && grep -q 'no metadata for not/real-model' <<< "${out}"; then
    ok "plan --model: a fit that cannot run is reported and leaves plan's exit status alone"
else fail "plan --model exit status (rc ${rc}, off ${rc_off})" "${out}"; fi

if ! render nofit STUB_ALLOC='cpu 32' K3D_GPU_FIT_HOST="${LAPTOP}" K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3.8-27B-int8-ov -- \
    && grep -q 'not starting a server that cannot fit' "${WORK}/nofit.err"; then
    ok "agent manifest refuses a model that cannot fit, with the reason"
else fail "agent manifest refuses a model that cannot fit" "$(cat "${WORK}/nofit.err")"; fi

if render override STUB_ALLOC=$'cpu 64\ngpu.intel.com/xe 4' K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_FIT_HOST="${XEON}" \
        K3D_GPU_AGENT_MODEL=RedHatAI/Qwen3.8-27B-INT4 K3D_GPU_AGENT_MAX_LEN=16384 K3D_GPU_AGENT_MAX_NUM_SEQS=3 -- \
    && has override '--max-model-len=16384' && has override '--max-num-seqs=3'; then
    ok "agent manifest: K3D_GPU_AGENT_MAX_LEN and _MAX_NUM_SEQS override the fit, and are checked by it"
else fail "agent manifest overrides" "$(cat "${WORK}/override.err")"; fi

if render default STUB_ALLOC='cpu 32' K3D_GPU_FIT_HOST="${DESK}" -- && ! has default 'agent-fit' \
    && render off STUB_ALLOC='cpu 32' K3D_GPU_FIT=off K3D_GPU_FIT_HOST="${LAPTOP}" K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3.8-27B-int8-ov -- \
    && ! has off 'agent-fit'; then
    ok "the default model, and K3D_GPU_FIT=off, render without a fit"
else fail "default model / K3D_GPU_FIT=off render without a fit" "$(cat "${WORK}/default.err" "${WORK}/off.err" 2>/dev/null)"; fi

# agent up's fit (AGENT_FIT_RECORD=1): a running server of the backend is stopped before
# the probe and the fit is recorded; when the new model cannot fit, the old one comes back.
rm -f "${WORK}/kubectl.log" "${WORK}/state/k3d-gpu/agent-fit.json"
if STUB_LOG="${WORK}/kubectl.log" render record STUB_REPLICAS=1 AGENT_FIT_RECORD=1 STUB_ALLOC=$'cpu 64\ngpu.intel.com/xe 4' \
        K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_FIT_HOST="${XEON}" K3D_GPU_AGENT_MODEL=RedHatAI/Qwen3.8-27B-INT4 -- \
    && grep -q '^kubectl -n k3d-gpu-agent scale deployment/vllm --replicas=0' "${WORK}/kubectl.log" \
    && grep -q '^kubectl -n k3d-gpu-agent wait --for=delete pod -l app=vllm' "${WORK}/kubectl.log" \
    && python3 -c 'import json, sys; assert json.load(open(sys.argv[1]))["model"] == "RedHatAI/Qwen3.8-27B-INT4"' \
        "${WORK}/state/k3d-gpu/agent-fit.json"; then
    ok "agent up's fit stops the running server before the probe and records the fit"
else fail "agent up's fit stops the running server and records the fit" "$(cat "${WORK}/record.err" "${WORK}/kubectl.log")"; fi
rm -f "${WORK}/kubectl.log"
if ! STUB_LOG="${WORK}/kubectl.log" render record-no STUB_REPLICAS=1 AGENT_FIT_RECORD=1 STUB_ALLOC='cpu 32' \
        K3D_GPU_FIT_HOST="${LAPTOP}" K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3.8-27B-int8-ov -- \
    && grep -q '^kubectl -n k3d-gpu-agent scale deployment/ovms --replicas=1' "${WORK}/kubectl.log" \
    && grep -q 'previous ovms is started again' "${WORK}/record-no.err"; then
    ok "agent up's fit: a model that cannot fit gets the previous server back"
else fail "agent up's fit restores the previous server" "$(cat "${WORK}/record-no.err" "${WORK}/kubectl.log")"; fi
if render norecord STUB_ALLOC='cpu 32' K3D_GPU_FIT_HOST="${DESK}" K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3.8-27B-int4-ov -- \
    && [ ! -e "${WORK}/state/k3d-gpu/agent-fit.json.tmp" ] \
    && python3 -c 'import json, sys; assert json.load(open(sys.argv[1]))["model"] == "RedHatAI/Qwen3.8-27B-INT4"' \
        "${WORK}/state/k3d-gpu/agent-fit.json"; then
    ok "agent manifest alone never rewrites the recorded fit"
else fail "agent manifest leaves the recorded fit alone" "$(cat "${WORK}/norecord.err")"; fi

# --- agent guard --once --------------------------------------------------------------------
# A CPU-placed fit under PSI some avg10 = 35%: one rung down, re-rendered and applied.
mkdir -p "${WORK}/state/k3d-gpu"
python3 "${ROOT}/console/fit.py" plan --host "${DESK}" --model "${FIX}/models/OpenVINO__Qwen3.8-27B-int4-ov" \
    --name OpenVINO/Qwen3.8-27B-int4-ov --mode json > "${WORK}/state/k3d-gpu/agent-fit.json"
printf 'some avg10=35.00 avg60=20.00 avg300=5.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n' \
    > "${WORK}/root/proc/pressure/memory"
printf 'MemTotal:       67108864 kB\nMemAvailable:   20000000 kB\n' > "${WORK}/root/proc/meminfo"
rm -f "${WORK}/kubectl.log"
sum0=$(sha256sum < "${WORK}/state/k3d-gpu/agent-fit.json")
want=$(python3 -c 'import json, sys; g = json.load(open(sys.argv[1]))["guard"]; print("%d %d" % tuple(g["rungs"][1]))' \
    "${WORK}/state/k3d-gpu/agent-fit.json")
out=$(STUB_LOG="${WORK}/kubectl.log" STUB_APPLIED="${WORK}/applied.yaml" STUB_ALLOC='cpu 32' K3D_GPU_FIT_HOST="${DESK}" \
    K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3.8-27B-int4-ov bash "${K3D_GPU}" agent guard --once 2>&1); rc=$?
if [ "${rc}" -eq 0 ] && grep -q 'one rung down' <<< "${out}" && grep -q '^kubectl apply -f -' "${WORK}/kubectl.log" \
    && grep -q -- "--max_num_seqs=${want% *}\$" "${WORK}/applied.yaml" && grep -q "agent-fit: \"cpu ${want% *}x${want#* }\"" "${WORK}/applied.yaml" \
    && [ "$(sha256sum < "${WORK}/state/k3d-gpu/agent-fit.json")" = "${sum0}" ]; then
    ok "agent guard --once: PSI pressure applies rung 1 of the recorded fit, and leaves the record as it was"
else fail "agent guard --once under pressure (rc ${rc}, want ${want})" "${out}$(cat "${WORK}/applied.yaml" 2>/dev/null | grep -E 'max_num|agent-fit')"; fi
out=$(STUB_LOG="${WORK}/kubectl.log" bash "${K3D_GPU}" agent guard --once 2>&1); rc=$?
if [ "${rc}" -eq 0 ] && grep -q 'cooling down' <<< "${out}"; then
    ok "agent guard --once: the next sample after a step cools down"
else fail "agent guard cooldown (rc ${rc})" "${out}"; fi

# Emergency (PSI full avg10 9%) at the lowest rung: the server is scaled to zero.
python3 - "${WORK}/state/k3d-gpu/agent-guard.json" "${WORK}/state/k3d-gpu/agent-fit.json" <<'EOF'
import json, sys
fit = json.load(open(sys.argv[2]))
json.dump({"rung": len(fit["guard"]["rungs"]) - 1, "calm": 0, "hot": 0}, open(sys.argv[1], "w"))
EOF
printf 'some avg10=60.00 avg60=40.00 avg300=5.00 total=1\nfull avg10=9.00 avg60=3.00 avg300=0.50 total=2\n' \
    > "${WORK}/root/proc/pressure/memory"
rm -f "${WORK}/kubectl.log"
out=$(STUB_LOG="${WORK}/kubectl.log" bash "${K3D_GPU}" agent guard --once 2>&1); rc=$?
if [ "${rc}" -eq 0 ] && grep -q 'stop the server' <<< "${out}" \
    && grep -q '^kubectl -n k3d-gpu-agent scale deployment/ovms --replicas=0' "${WORK}/kubectl.log"; then
    ok "agent guard --once: an emergency at the lowest rung scales the server to zero"
else fail "agent guard --once emergency (rc ${rc})" "${out}"; fi

out=$(XDG_STATE_HOME="${WORK}/empty" bash "${K3D_GPU}" agent guard --once 2>&1); rc=$?
if [ "${rc}" -ne 0 ] && grep -q 'no fit recorded' <<< "${out}"; then
    ok "agent guard without a recorded fit is an error"
else fail "agent guard without a recorded fit" "${out}"; fi


# --- plan --model and agent up --model: the same variant and the same fit ------------------
# The model optimizer picks a variant at a pinned revision; plan must size that variant
# (not the requested id) on the device agent up serves it on. The fit cache holds each
# fixture model under its HF commit too (tests/fixtures/hf), as the optimizer pins it.
python3 - "${ROOT}/tests/fixtures/hf" "${FIX}/models" "${WORK}/cache" <<'PY'
import json, os, sys
hf, models, cache = sys.argv[1:]
for f in os.listdir(hf):
    for rid, rec in json.load(open(os.path.join(hf, f)))["repos"].items():
        src = os.path.join(models, rid.replace("/", "__"))
        dst = os.path.join(cache, "%s@%s" % (rid.replace("/", "__"), rec["sha"]))
        if os.path.isdir(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
PY
XB70="${ROOT}/tests/console/model-opt/xeon-b70.facts.json"
GPU_ENV=(STUB_ALLOC=$'cpu 64\ngpu.intel.com/xe 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true K3D_GPU_FIT_HOST="${XEON}")
# parity <name> <model> [env...]: the variant and the fit line of `plan --model` equal
# those `agent manifest --model` prints and renders.
parity() {
    local name=$1 model=$2; shift 2
    local p a pv av pf af
    p=$(env "$@" K3D_GPU_FIT_HOST="${XEON}" bash "${K3D_GPU}" plan --facts "${XB70}" --model "${model}" 2>&1)
    a=$(env "${GPU_ENV[@]}" "$@" bash "${K3D_GPU}" agent manifest --model "${model}" 2>&1)
    pv=$(sed -n 's/.*; picked \([^ ]*\) on .*/\1/p' <<< "${p}" | head -1)
    av=$(grep -E '^ +- --source_model=|^            - [A-Za-z0-9][^ =]*/[^ ]*$' <<< "${a}" | head -1 | sed 's/.*--source_model=//; s/^ *- //')
    pf=$(grep -o 'fit: .*' <<< "${p}" | head -1)
    af=$(grep -o 'fit: .*' <<< "${a}" | head -1)
    if [ -n "${pv}" ] && [ "${pv}" = "${av}" ] && [ -n "${pf}" ] && [ "${pf}" = "${af}" ]; then
        ok "plan and agent up agree for ${name}: ${pv}; ${pf}"
    else
        fail "plan and agent up agree for ${name}" "plan: ${pv} | ${pf}
agent: ${av} | ${af}
--- plan
${p}
--- agent
${a}"
    fi
}
parity "the Gemma base id (OVMS on the B70: the int4 IR, not the bf16 base)" google/gemma-4-26B-A4B-it
parity "Qwen3.8-27B (OVMS on the B70)" Qwen/Qwen3.8-27B
parity "an explicit variant (RedHatAI INT4 on vLLM)" RedHatAI/Qwen3.8-27B-INT4 K3D_GPU_AGENT_BACKEND=vllm
out=$(K3D_GPU_FIT_HOST="${XEON}" bash "${K3D_GPU}" plan --facts "${XB70}" --model google/gemma-4-26B-A4B-it 2>&1)
if grep -q '^fit: OpenVINO/gemma-4-26b-a4b-it-int4-ov on' <<< "${out}"; then
    ok "plan --model sizes the variant the optimizer picked"
else fail "plan --model sizes the variant the optimizer picked" "${out}"; fi

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
