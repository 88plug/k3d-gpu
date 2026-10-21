#!/usr/bin/env bash
# The model optimizer end to end through the launcher, no cluster, no network:
#   - tests/console/test_model_opt.py (unit tests and the zero-flag decision table)
#   - `plan --model <hf-id> --explain` on a Xeon 6 + Arc Pro B70 facts fixture
#     matches tests/console/model-opt/*.golden (Gemma-4-26B-A4B, Qwen3.8-27B)
#   - `agent manifest --model <hf-id>` against a stub kubectl renders the optimizer's
#     choices into the OVMS and vLLM manifests, with no flag set
#   - env flags override single choices; K3D_GPU_MODEL_OPT=off serves the id as given
#   - every failure (unknown model, no metadata, nothing the engine can serve)
#     keeps the engine's defaults with one line and exit 0
# Model metadata comes from tests/fixtures/hf (K3D_GPU_HF_FIXTURES).
#
# Usage: tests/model_opt_test.sh [--update]   (--update rewrites the goldens)

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
K3D_GPU="${ROOT}/scripts/k3d-gpu"
GOLD="${ROOT}/tests/console/model-opt"
UPDATE=0
[ "${1:-}" = "--update" ] && UPDATE=1
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

export K3D_GPU_HF_FIXTURES="${ROOT}/tests/fixtures/hf" XDG_CACHE_HOME="${WORK}/cache" XDG_STATE_HOME="${WORK}/state" K3D_GPU_SYSROOT="${WORK}/root"
mkdir -p "${WORK}/root"
# A fixed ledger, so a verdict landing in EXPERIMENTS.md does not move the goldens.
export K3D_GPU_MODEL_OPT_LEDGER="${GOLD}/ledger.md"
export K3D_GPU_FIT=off  # fit (#174) is tested in its own suite; it reads HF weight headers
unset K3D_GPU_AGENT_OPT_MODEL K3D_GPU_AGENT_MODEL K3D_GPU_AGENT_BACKEND K3D_GPU_AGENT_DEVICE K3D_GPU_AGENT_DRAFT K3D_GPU_AGENT_VARIANT \
    K3D_GPU_AGENT_THINKING K3D_GPU_AGENT_PRECISION K3D_GPU_MODEL_OPT K3D_GPU_AGENT_PREFIX_CACHING

if python3 "${ROOT}/tests/console/test_model_opt.py" 2>&1 | tail -1 | grep -qx OK; then
    ok "model_opt unit tests and the zero-flag decision table"
else
    python3 "${ROOT}/tests/console/test_model_opt.py" 2>&1 | tail -30
    fail "model_opt unit tests and the zero-flag decision table"
fi

# ---- plan --model --explain goldens on the Xeon + B70 fixture -------------------
for m in google/gemma-4-26B-A4B-it Qwen/Qwen3.8-27B; do
    g="${GOLD}/xeon-b70.${m##*/}.explain.golden"
    out=$("${K3D_GPU}" plan --facts "${GOLD}/xeon-b70.facts.json" --model "${m}" --explain 2>&1)
    rc=$?
    if [ "${UPDATE}" = 1 ]; then printf '%s\n' "${out}" > "${g}"; fi
    if [ "${rc}" -eq 0 ] && [ "${out}" = "$(cat "${g}" 2>/dev/null)" ]; then
        ok "plan --model ${m} --explain matches ${g##*/}"
    else
        fail "plan --model ${m} --explain differs from ${g##*/} (rc=${rc})"
        diff <(printf '%s\n' "${out}") "${g}" | head -20
    fi
done
out=$("${K3D_GPU}" plan --facts "${GOLD}/xeon-b70.facts.json" --model google/gemma-4-26B-A4B-it 2>&1)
case "${out}" in
    "Detected Intel(R) Xeon(R) 6960P (AMX) + 1x Battlemage 0xe223 (XMX); picked OpenVINO/gemma-4-26b-a4b-it-int4-ov on OVMS (GPU)"*)
        ok "plan --model leads with the one-line summary" ;;
    *) fail "plan --model summary line: ${out%%$'\n'*}" ;;
esac
out=$("${K3D_GPU}" plan --facts "${GOLD}/xeon-b70.facts.json" --model acme/never-heard-of --explain 2>&1)
if [ $? -eq 0 ] && grep -q "no metadata for acme/never-heard-of" <<< "${out}"; then
    ok "plan --model on an unknown model degrades with one line"
else
    fail "plan --model on an unknown model: ${out}"
fi
a=$("${K3D_GPU}" plan --facts "${GOLD}/xeon-b70.facts.json" --model Qwen/Qwen3.8-27B --hash)
b=$("${K3D_GPU}" plan --facts "${GOLD}/xeon-b70.facts.json" --model Qwen/Qwen3.8-27B --hash)
c=$(K3D_GPU_MODEL_OPT=off "${K3D_GPU}" plan --facts "${GOLD}/xeon-b70.facts.json" --model Qwen/Qwen3.8-27B --hash)
if [ "${a}" = "${b}" ] && [ "${a}" != "${c}" ]; then ok "plan --hash is stable and covers the optimizer"; else fail "plan --hash: ${a} ${b} ${c}"; fi

# ---- agent manifest --model against a stub kubectl --------------------------------
ASTUB="${WORK}/bin"
mkdir -p "${ASTUB}"
cat > "${ASTUB}/kubectl" <<'EOF'
#!/bin/sh
case "$*" in
    *allocatable*) printf '%s\n' "${STUB_ALLOC:-}" ;;
    "get pods -A "*) printf '%s\n' "${STUB_USED:-}" ;;
    "get nodes -l "*)
        for l in ${STUB_NODE_LABELS:-}; do [ "$l" = "$4" ] && echo node/k3d-agent-server-0; done ;;
esac
exit 0
EOF
chmod +x "${ASTUB}/kubectl"
XE=$'cpu 32\ngpu.intel.com/xe 1'

# says <name> <want rc: 0|fail> <ERE> [negate] -- env... -- args...
says() {
    local name=$1 want=$2 re=$3 negate=$4 out rc hit=0
    shift 4
    local -a envs=()
    while [ $# -gt 0 ] && [ "$1" != -- ]; do envs+=("$1"); shift; done
    shift
    out=$(env PATH="${ASTUB}:${PATH}" "${envs[@]}" bash "${K3D_GPU}" agent manifest "$@" 2>&1)
    rc=$?
    grep -Eq -- "${re}" <<< "${out}" && hit=1
    if { { [ -z "${negate}" ] && [ "${hit}" = 1 ]; } || { [ -n "${negate}" ] && [ "${hit}" = 0 ]; }; } \
       && { { [ "${want}" = 0 ] && [ "${rc}" = 0 ]; } || { [ "${want}" = fail ] && [ "${rc}" != 0 ]; }; }; then
        ok "${name}"
    else
        fail "${name} (rc=${rc}, ${negate:+not }/${re}/)"
        awk '{print "  | " $0}' <<< "${out}" | head -40
    fi
}
GPU=(STUB_ALLOC="${XE}" STUB_NODE_LABELS=gpu.intel.com/xmx=true)

says "OVMS on an XMX GPU serves the official OpenVINO IR of Qwen3.8-27B" 0 '--source_model=OpenVINO/Qwen3.8-27B-int4-ov' "" "${GPU[@]}" -- --model Qwen/Qwen3.8-27B
says "the served name stays the requested model's" 0 '--model_name=Qwen3.8-27B$' "" "${GPU[@]}" -- --model Qwen/Qwen3.8-27B
says "OVMS gets the documented Qwen3.8 parsers" 0 "--tool_parser=qwen3coder" "" "${GPU[@]}" -- --model Qwen/Qwen3.8-27B
says "OVMS MTP stays off by default (H25 prefix reuse)" 0 'draft_model_path' negate "${GPU[@]}" -- --model Qwen/Qwen3.8-27B
says "the one-line summary is printed" 0 'Detected .* \+ 1x gpu.intel.com/xe[^;]*; picked OpenVINO/Qwen3.8-27B-int4-ov on OVMS \(GPU\), no drafter; thinking off for agents' "" "${GPU[@]}" -- --model Qwen/Qwen3.8-27B
says "K3D_GPU_AGENT_DRAFT=mtp turns OVMS MTP on with prefix caching off" 0 "'--draft_model_path=\.'" "" "${GPU[@]}" K3D_GPU_AGENT_DRAFT=mtp -- --model Qwen/Qwen3.8-27B
says "  ...and writes --enable_prefix_caching=false" 0 "'--enable_prefix_caching=false'" "" "${GPU[@]}" K3D_GPU_AGENT_DRAFT=mtp -- --model Qwen/Qwen3.8-27B
says "  ...and no Fast Draft beside it" 0 'draft_source_model' negate "${GPU[@]}" K3D_GPU_AGENT_DRAFT=mtp -- --model Qwen/Qwen3.8-27B
says "Gemma 4 on OVMS: the OpenVINO IR, gemma4 parsers" 0 '--reasoning_parser=gemma4' "" "${GPU[@]}" -- --model google/gemma-4-26B-A4B-it
says "a tool parser set in the environment wins" 0 '--tool_parser=hermes3' "" "${GPU[@]}" K3D_GPU_AGENT_TOOL_PARSER=hermes3 -- --model google/gemma-4-26B-A4B-it
says "vLLM on the GPU: Qwen3.8 MTP" 0 "'\{\"method\": \"mtp\", \"num_speculative_tokens\": 3\}'" "" "${GPU[@]}" K3D_GPU_AGENT_BACKEND=vllm -- --model Qwen/Qwen3.8-27B
says "vLLM pins the model revision" 0 '--revision=1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0' "" "${GPU[@]}" K3D_GPU_AGENT_BACKEND=vllm -- --model Qwen/Qwen3.8-27B
says "vLLM: thinking off server-side" 0 "'--default-chat-template-kwargs'" "" "${GPU[@]}" K3D_GPU_AGENT_BACKEND=vllm -- --model Qwen/Qwen3.8-27B
says "vLLM: text-only load" 0 "'--language-model-only'" "" "${GPU[@]}" K3D_GPU_AGENT_BACKEND=vllm -- --model Qwen/Qwen3.8-27B
says "vLLM: Gemma 4 with its assistant drafter" 0 'gemma-4-26B-A4B-it-assistant' "" "${GPU[@]}" K3D_GPU_AGENT_BACKEND=vllm -- --model google/gemma-4-26B-A4B-it
says "Gaudi: vLLM, no speculative decoding by default" 0 'speculative' negate STUB_ALLOC=$'cpu 32\nhabana.ai/gaudi 1' -- --model Qwen/Qwen3.8-27B
says "Gaudi: bf16 original at its revision" 0 '--revision=1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0' "" STUB_ALLOC=$'cpu 32\nhabana.ai/gaudi 1' -- --model Qwen/Qwen3.8-27B
says "CPU: the Phi-4 control serves its OpenVINO int4 IR, no draft" 0 '--source_model=OpenVINO/Phi-4-mini-instruct-int4-ov' "" STUB_ALLOC='cpu 32' -- --model microsoft/Phi-4-mini-instruct
says "a model with no OpenVINO IR keeps OVMS's default, with one line" 0 'keeping the engine.s default model' "" STUB_ALLOC='cpu 32' -- --model LiquidAI/LFM2-1.2B
says "  ...the default model is served" 0 '--source_model=OpenVINO/Qwen3-4B-int4-ov' "" STUB_ALLOC='cpu 32' -- --model LiquidAI/LFM2-1.2B
says "an unknown model with no metadata keeps OVMS's default" 0 '--source_model=OpenVINO/Qwen3-4B-int4-ov' "" STUB_ALLOC='cpu 32' -- --model acme/never-heard-of
says "  ...vLLM serves it as given" 0 '^            - acme/never-heard-of$' "" "${GPU[@]}" K3D_GPU_AGENT_BACKEND=vllm -- --model acme/never-heard-of
says "Flash-Next on OVMS: refused with the reason, default kept" 0 'qwen4_exp is in no OpenVINO' "" "${GPU[@]}" -- --model Qwen/Qwen3.8-Flash-Next
says "K3D_GPU_MODEL_OPT=off serves the id as given" 0 '--source_model=OpenVINO/Qwen3.8-27B-int4-ov' "" "${GPU[@]}" K3D_GPU_MODEL_OPT=off -- --model OpenVINO/Qwen3.8-27B-int4-ov
says "--model and K3D_GPU_AGENT_MODEL together are refused" fail 'use one' "" K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-4B-int4-ov -- --model Qwen/Qwen3.8-27B
says "--model that is neither an org/name id nor a directory is refused" fail 'not a valid model id' "" -- --model 'bad id'
says "an explicit Fast Draft device takes the place of MTP" 0 'draft_model_path' negate "${GPU[@]}" K3D_GPU_AGENT_DRAFT=GPU K3D_GPU_AGENT_PREFIX_CACHING=false -- --model Qwen/Qwen3.8-27B
says "  ...and is rendered" 0 '--draft_source_model=OpenVINO/Qwen3-0.6B-int4-ov' "" "${GPU[@]}" K3D_GPU_AGENT_DRAFT=GPU K3D_GPU_AGENT_PREFIX_CACHING=false -- --model Qwen/Qwen3.8-27B
says "K3D_GPU_AGENT_DRAFT=mtp with prefix caching forced on: no MTP" 0 'draft_model_path' negate "${GPU[@]}" K3D_GPU_AGENT_DRAFT=mtp K3D_GPU_AGENT_PREFIX_CACHING=true -- --model Qwen/Qwen3.8-27B
says "without --model nothing changes" 0 'model optimizer' negate STUB_ALLOC='cpu 32' --
says "  ...the default model and parsers are served" 0 '--tool_parser=hermes3' "" STUB_ALLOC='cpu 32' --

echo
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
