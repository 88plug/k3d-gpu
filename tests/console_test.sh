#!/usr/bin/env bash
# Console spine tests: facts/v1 and plan(). No cluster, no network.
#
#   - unit and property tests of console/ (tests/console/test_console.py)
#   - every tests/fixtures/*/facts.json and plan.json and both matrices validate
#     (tests/sim_matrix_test.sh writes and byte-checks those goldens)
#   - the schema subset validator agrees with the jsonschema package
#     (tests/console/crosscheck.py; skipped when jsonschema is not importable by
#     ${CONSOLE_JSONSCHEMA_PYTHON:-python3})
#   - `plan` against a matrix whose cells are green (synthetic report ids) picks,
#     places and renders as the goldens in tests/console/*.golden say
#   - S3, the firmware gatekeeper: on raptorlake-stale-guc (GuC older than i915
#     wants) the iGPU is refused with a one-line fix and plan falls back to
#     llama.cpp on Vulkan or OVMS on the CPU (tests/console/matrix-fallback.json,
#     synthetic cells); gaudi3-no-driver plans a fix, not an engine
#   - `facts` writes the state file; detect --json and doctor --json print facts/v1
#   - bad options and bad input fail with a message
#
# Usage: tests/console_test.sh [--update]   (--update rewrites tests/console/*.golden)

set -uo pipefail

TESTS=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "${TESTS}")
K3D_GPU="${REPO}/scripts/k3d-gpu"
CONSOLE="${REPO}/console"
DIR="${TESTS}/console"
UPDATE=0
[ "${1:-}" = "--update" ] && UPDATE=1
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

ok()   { PASS=$((PASS + 1)); echo "ok   $*"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $*"; }

if python3 "${DIR}/test_console.py"; then ok "console unit and property tests"; else fail "console unit and property tests"; fi
if python3 "${DIR}/test_sku_model.py"; then ok "sku_model unit and property tests (H24)"; else fail "sku_model unit and property tests (H24)"; fi

# Every golden document validates against its schema.
validate() {  # validate <ref or ""> <file>...
    local ref=$1; shift
    python3 - "${CONSOLE}" "${ref}" "$@" <<'EOF'
import json, sys
sys.path.insert(0, sys.argv[1])
import facts, schema_check
root, ref = facts.load_schema(), sys.argv[2] or None
for f in sys.argv[3:]:
    schema_check.check(json.load(open(f, encoding="utf-8")), root, ref)
EOF
}
shopt -s nullglob
facts_files=("${REPO}"/tests/fixtures/*/facts.json)
plan_files=("${REPO}"/tests/fixtures/*/plan.json)
[ "${#facts_files[@]}" -ge 14 ] && [ "${#facts_files[@]}" -eq "${#plan_files[@]}" ] \
    && ok "facts.json and plan.json goldens for all ${#facts_files[@]} fixtures" \
    || fail "expected facts.json and plan.json for every fixture (found ${#facts_files[@]} and ${#plan_files[@]})"
validate "" "${facts_files[@]}" && ok "facts goldens validate as facts/v1" || fail "facts goldens validate as facts/v1"
validate "#/\$defs/plan" "${plan_files[@]}" && ok "plan goldens validate as plan/v1" || fail "plan goldens validate as plan/v1"
validate "#/\$defs/matrix" "${CONSOLE}/matrix.v1.json" "${DIR}/matrix-green.json" "${DIR}/matrix-fallback.json" \
    && ok "matrices validate as matrix/v1" || fail "matrices validate as matrix/v1"

# S2: the baked matrix has no green cell on any fixture. S3: the fixtures with a
# refused target (and only they) plan a fix instead.
FIX_FIXTURES="gaudi3-no-driver raptorlake-stale-guc"
bad=""
for f in "${plan_files[@]}"; do
    name=$(basename "$(dirname "${f}")") want=unmeasured
    [[ " ${FIX_FIXTURES} " == *" ${name} "* ]] && want=fix
    grep -q "\"decision\": \"${want}\"" "${f}" || bad+=" ${name}"
done
if [ -z "${bad}" ]; then
    ok "baked matrix: ${FIX_FIXTURES// / and } plan a fix, every other fixture plans unmeasured"
else
    fail "baked matrix: wrong decision for${bad}"
fi
if grep -L 'unmeasured, run `k3d-gpu prove` on this box' "${REPO}"/tests/fixtures/*/plan-explain.golden | grep -q .; then
    fail "a fixture plan-explain.golden lacks the unmeasured line"
else
    ok "every fixture's --explain says unmeasured, run prove"
fi
for name in ${FIX_FIXTURES}; do
    if python3 - "${REPO}/tests/fixtures/${name}/plan.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["engine"] is None and d["cell"] is None and d["placement"] is None, d
assert d["fixes"] and all(f["fix"] for f in d["fixes"]), d["fixes"]
assert d["message"] == d["summary"][0] and "; fix: " in d["message"] and "\n" not in d["message"], d["message"]
EOF
    then ok "${name}: plan returns a one-line fix, not an engine"
    else fail "${name}: plan returns a one-line fix, not an engine"; fi
done

py=${CONSOLE_JSONSCHEMA_PYTHON:-python3}
"${py}" "${DIR}/crosscheck.py" "${facts_files[@]}" "${plan_files[@]}" "${CONSOLE}/matrix.v1.json" "${DIR}/matrix-green.json" "${DIR}/matrix-fallback.json"
case $? in
    0) ok "schema_check agrees with jsonschema" ;;
    77) echo "skip schema_check vs jsonschema (jsonschema not importable by ${py})" ;;
    *) fail "schema_check disagrees with jsonschema" ;;
esac

# Green-path goldens: plan reads a facts file, so nothing on this host is read.
golden() {  # golden <name> <plan args...>; GOLDEN_FIXTURE and GOLDEN_MATRIX pick the inputs
    local name=$1 out rc; shift
    out=$("${K3D_GPU}" plan --facts "${REPO}/tests/fixtures/${GOLDEN_FIXTURE:-raptorlake-nvidia}/facts.json" \
        --matrix "${GOLDEN_MATRIX:-${DIR}/matrix-green.json}" "$@" 2>&1)
    rc=$?
    out+=$'\n'"exit=${rc}"
    if [ "${UPDATE}" -eq 1 ]; then
        printf '%s\n' "${out}" > "${DIR}/${name}.golden"; echo "wrote tests/console/${name}.golden"; return
    fi
    if diff -u "${DIR}/${name}.golden" <(printf '%s\n' "${out}") > "${WORK}/diff"; then
        ok "plan $* (${name}.golden)"
    else
        fail "plan $* (${name}.golden)"; sed 's/^/  | /' "${WORK}/diff"
    fi
}
golden green-explain --explain
golden green-gpu-shared --device gpu --bind 0.0.0.0 --port 9000
golden green-json --json
golden green-engine-excluded --explain --engine llama.cpp
# S3 fallback: the healthy host picks the iGPU; the stale-GuC host refuses it,
# names the fix and falls back to llama.cpp-Vulkan on the NVIDIA GPU, or to OVMS
# on the P-cores when the engine is OVMS. A forced refused device gets the fix.
GOLDEN_MATRIX="${DIR}/matrix-fallback.json" golden fallback-healthy
GOLDEN_MATRIX="${DIR}/matrix-fallback.json" GOLDEN_FIXTURE=raptorlake-stale-guc golden fallback-vulkan --explain
GOLDEN_MATRIX="${DIR}/matrix-fallback.json" GOLDEN_FIXTURE=raptorlake-stale-guc golden fallback-cpu --engine ovms
GOLDEN_MATRIX="${DIR}/matrix-fallback.json" GOLDEN_FIXTURE=raptorlake-stale-guc golden fallback-forced-gpu --engine ovms --device gpu
GOLDEN_MATRIX="${DIR}/matrix-fallback.json" GOLDEN_FIXTURE=raptorlake-stale-guc golden fallback-json --json
[ "${UPDATE}" -eq 1 ] && exit 0

# --json output of the green plan is plan/v1.
"${K3D_GPU}" plan --json --facts "${REPO}/tests/fixtures/raptorlake-nvidia/facts.json" --matrix "${DIR}/matrix-green.json" > "${WORK}/p.json"
validate "#/\$defs/plan" "${WORK}/p.json" && ok "green plan --json validates as plan/v1" || fail "green plan --json validates as plan/v1"

# `facts` on a fixture, with docker, k3d and kubectl stubbed: state file written
# and valid; detect --json and doctor --json print the same document.
mkdir -p "${WORK}/bin" "${WORK}/state"
for t in docker k3d kubectl; do printf '#!/bin/sh\nexit 0\n' > "${WORK}/bin/${t}"; chmod +x "${WORK}/bin/${t}"; done
run() { PATH="${WORK}/bin:${PATH}" XDG_STATE_HOME="${WORK}/state" \
    K3D_GPU_SYSROOT="${REPO}/tests/fixtures/arrowlake-h/sysroot" "${K3D_GPU}" "$@"; }
if run facts > "${WORK}/summary" 2>&1 && [ -s "${WORK}/state/k3d-gpu/facts.json" ] \
   && validate "" "${WORK}/state/k3d-gpu/facts.json" \
   && grep -q "^facts/v1 for sha256:" "${WORK}/summary" && grep -q "npu:0000:00:0b.0" "${WORK}/summary"; then
    ok "facts writes \$XDG_STATE_HOME/k3d-gpu/facts.json and prints a summary"
else
    fail "facts writes the state file"; sed 's/^/  | /' "${WORK}/summary"
fi
run facts --json > "${WORK}/a.json" 2>/dev/null
run detect --json > "${WORK}/b.json" 2>/dev/null
run doctor --json > "${WORK}/c.json" 2>/dev/null
if cmp -s "${WORK}/a.json" "${WORK}/state/k3d-gpu/facts.json" && cmp -s "${WORK}/a.json" "${WORK}/b.json" \
   && cmp -s "${WORK}/a.json" "${WORK}/c.json"; then
    ok "facts --json, detect --json, doctor --json and the state file are the same bytes"
else
    fail "facts --json, detect --json, doctor --json and the state file differ"
fi
# The OVMS pin is the OVMS image whatever agent backend is set; K3D_GPU_AGENT_IMAGE
# overrides it only for the OVMS backend.
pins() { python3 -c 'import json,sys; p=json.load(sys.stdin)["release"]["pins"]; print(p["ovms"], p["llama.cpp"])'; }
ovms_pin=$(run facts --json 2>/dev/null | pins)
vllm_pin=$(K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_IMAGE=example/vllm@sha256:0 run facts --json 2>/dev/null | pins)
over_pin=$(K3D_GPU_AGENT_IMAGE=example/ovms@sha256:1 run facts --json 2>/dev/null | pins)
if [[ "${ovms_pin}" == "openvino/model_server@sha256:"*" ghcr.io/ggml-org/llama.cpp@sha256:"* ]] \
   && [ "${vllm_pin}" = "${ovms_pin}" ] && [ "${over_pin}" = "example/ovms@sha256:1 ${ovms_pin#* }" ]; then
    ok "facts pins: OVMS and llama.cpp digests; a vLLM backend leaves the OVMS pin alone"
else
    fail "facts pins (ovms: ${ovms_pin}; vllm: ${vllm_pin}; override: ${over_pin})"
fi

# The packaged layout (/usr/bin/k3d-gpu + /usr/share/k3d-gpu/console/<the files
# PKGBUILD installs>) finds its console files and plans.
pkg="${WORK}/pkg"
# Run the real PKGBUILD package() into ${pkg}, so the test checks what ships.
mkdir -p "${WORK}/src"; ln -sfn "${REPO}" "${WORK}/src/k3d-gpu"
( set -e; source "${REPO}/packaging/aur/PKGBUILD"; export srcdir="${WORK}/src" pkgdir="${pkg}"; package ) > "${WORK}/pkgbuild.log" 2>&1
if K3D_GPU_SHARE="${pkg}/usr/share/k3d-gpu" "${pkg}/usr/bin/k3d-gpu" plan --json \
       --facts "${REPO}/tests/fixtures/raptorlake-nvidia/facts.json" > "${WORK}/pkg.json" 2> "${WORK}/pkg.err" \
   && cmp -s "${WORK}/pkg.json" "${REPO}/tests/fixtures/raptorlake-nvidia/plan.json"; then
    ok "packaged layout (PKGBUILD's console files under \$K3D_GPU_SHARE/console) plans"
else
    fail "packaged layout plans"; sed 's/^/  | /' "${WORK}/pkg.err" "${WORK}/pkgbuild.log"
fi

# Bad input fails with a message and a non-zero exit.
expect_fail() {  # expect_fail <message substring> <args...>
    local want=$1 out; shift
    if out=$(run "$@" 2>&1 < /dev/null); then
        fail "$* succeeded"
    elif grep -qF -- "${want}" <<< "${out}"; then
        ok "$* fails: ${want}"
    else
        fail "$* failed without '${want}'"; sed 's/^/  | /' <<< "${out}"
    fi
}
F="${REPO}/tests/fixtures/raptorlake-nvidia/facts.json"
expect_fail "unknown facts option" facts --yaml
expect_fail "unknown detect option" detect --yaml
expect_fail "unknown plan option" plan --frobnicate
expect_fail "plan: --port needs a value" plan --port
expect_fail "port must be an integer" plan --facts "${F}" --port abc
expect_fail "port must be an integer" plan --facts "${F}" --port 70000
expect_fail "device must be one of" plan --facts "${F}" --device tpu
expect_fail "does not exist" plan --facts "${WORK}/missing.json"
echo '{"schema": "k3d-gpu.facts/v1"}' > "${WORK}/bad.json"
expect_fail "schema violations" plan --facts "${WORK}/bad.json"
echo 'not json' > "${WORK}/junk.json"
expect_fail "plan:" plan --facts "${WORK}/junk.json"
expect_fail "schema violations" plan --facts "${F}" --matrix "${WORK}/bad.json"

# plan --predict (console/sku_model.py, H24): the launcher passes through and
# the JSON says what it is (a model output, labelled).
if out=$("${K3D_GPU}" plan --predict i9-13900t --json --mts 3600 --channels 2) \
    && python3 -c 'import json,sys; p=json.loads(sys.argv[1]); assert p["schema"]=="sku-predict/v1" and p["status"]=="extrapolated" and p["cpu"]["decode_tok_s"]==15.7, p' "${out}"; then
    ok "plan --predict i9-13900t --json"
else
    fail "plan --predict i9-13900t --json"
fi
if "${K3D_GPU}" plan --predict list | grep -q '^xeon-6980p '; then ok "plan --predict list"; else fail "plan --predict list"; fi
expect_fail "plan: --predict needs a SKU" plan --predict
expect_fail "unknown SKU" plan --predict i9-99999
expect_fail "unknown plan --predict option" plan --predict n100 --bogus
expect_fail "--predict must be the first option" plan --json --predict n100

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
