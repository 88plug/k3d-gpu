#!/usr/bin/env bash
# canary/run.sh against stub engines: tests/openai_stub.py and tests/fake_ovms.py play
# the server, a docker shim on PATH plays the daemon (pull, run, inspect, logs, rm) and
# serves the pinned images' real help output (tests/fixtures/canary/help) and vLLM
# v0.32.0's real sources for the surface check. No image is pulled, nothing listens
# beyond 127.0.0.1, and each case reads the result JSON canary/run.sh writes.
#
# Usage: tests/canary_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIX=${ROOT}/tests/fixtures/canary
T=$(mktemp -d "${TMPDIR:-/tmp}/canary_test.XXXXXX")
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }
cleanup() {
    local f
    for f in "${T}"/state/*.pid; do
        [ -f "${f}" ] && kill -KILL -- "-$(cat "${f}")" 2>/dev/null
    done
    [ -z "${HF_PID:-}" ] || kill "${HF_PID}" 2>/dev/null
    rm -rf "${T}"
}
trap cleanup EXIT
mkdir -p "${T}/bin" "${T}/state" "${T}/dockerroot"

# The docker shim. STUB_SERVER is the command a detached `docker run` starts ({port} is
# the published host port); STUB_HELP the text --help prints; STUB_VERSION_TEXT what
# --version prints; STUB_SITE the site-packages tree `surface.py inside` reads;
# STUB_PULL_FAILS how many pulls fail first; STUB_IMAGE_BYTES the compressed image size.
cat > "${T}/bin/docker" <<'SHIM'
#!/usr/bin/env bash
S=${STUB_STATE:?}
echo "docker $*" >> "${S}/calls"
case "$1" in
    info) echo "${STUB_DOCKER_ROOT:?}"; exit 0 ;;
    buildx)
        printf '{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json","layers":[{"size":%s}]}\n' \
            "${STUB_IMAGE_BYTES:-1000000}"
        exit 0 ;;
    pull)
        n=$(cat "${S}/pullfails" 2>/dev/null || echo "${STUB_PULL_FAILS:-0}")
        if [ "${n}" -gt 0 ]; then echo $((n - 1)) > "${S}/pullfails"; echo "pull: toomanyrequests" >&2; exit 1; fi
        echo 0 > "${S}/pullfails"
        exit 0 ;;
    inspect)
        name=${*: -1}
        if [ -f "${S}/${name}.pid" ] && kill -0 "$(cat "${S}/${name}.pid")" 2>/dev/null; then echo "true 0"
        else echo "false $(cat "${S}/${name}.rc" 2>/dev/null || echo 0)"; fi
        exit 0 ;;
    logs) cat "${S}/${*: -1}.log" 2>/dev/null; exit 0 ;;
    rm)
        name=${*: -1}
        [ -f "${S}/${name}.pid" ] && kill -KILL -- "-$(cat "${S}/${name}.pid")" 2>/dev/null
        rm -f "${S}/${name}.pid"
        exit 0 ;;
    run) shift ;;
    *) echo "shim: unhandled docker $*" >&2; exit 1 ;;
esac
detach="" name="" port="" canary=""
while [ $# -gt 0 ]; do
    case "$1" in
        -d) detach=1; shift ;;
        --rm|--init) shift ;;
        --name) name=$2; shift 2 ;;
        -p) port=${2#127.0.0.1:}; port=${port%%:*}; shift 2 ;;
        -v) case "$2" in *:/canary:ro) canary=${2%%:/canary:ro} ;; esac; shift 2 ;;
        -e|--entrypoint|--cap-add|--user|--ulimit|--cpus|--shm-size|--network) shift 2 ;;
        -*) shift ;;
        *) break ;;
    esac
done
shift  # the image
if [ -n "${detach}" ]; then
    # Its own process group (setsid), whose leader writes the pid file: `rm` kills the group.
    cmd=${STUB_SERVER//\{port\}/${port}}
    ( setsid bash -c 'echo $$ > "$0"; eval "$1"' "${S}/${name}.pid" "${cmd}" > "${S}/${name}.log" 2>&1
      echo $? > "${S}/${name}.rc" ) > /dev/null 2>&1 &
    for _ in $(seq 50); do [ -s "${S}/${name}.pid" ] && break; sleep 0.05; done
    echo "cid-${name}"
    exit 0
fi
case " $* " in
    *" /canary/surface.py inside "*) shift; PYTHONPATH=${STUB_SITE:?} exec python3 "${canary}/surface.py" "$@" ;;
    *" --help"*) cat "${STUB_HELP:?}"; exit 0 ;;
    *" --version "*) echo "${STUB_VERSION_TEXT:-}"; exit 0 ;;
    *) exit 0 ;;
esac
SHIM
chmod +x "${T}/bin/docker"

export PATH="${T}/bin:${PATH}"
export STUB_STATE=${T}/state STUB_DOCKER_ROOT=${T}/dockerroot
export CANARY_MODELS=${T}/models CANARY_PULL_BACKOFF=0 CANARY_READY_TIMEOUT=20
printf 'processor\t: 0\nflags\t\t: fpu sse2 avx avx2 avx512f amx_tile\n' > "${T}/cpuinfo.avx512"
printf 'processor\t: 0\nflags\t\t: fpu sse2 avx avx2\n' > "${T}/cpuinfo.avx2"
export CANARY_CPUINFO=${T}/cpuinfo.avx512

# Every runtime engine's model, already in the cache (as actions/cache restores it).
for id in vllm-cpu ovms-cpu llama-cpu eat-vllm-cpu; do
    d=$(python3 "${ROOT}/canary/plan.py" model-dir "${id}")
    mkdir -p "${d}"
    : > "${d}/placeholder"
    python3 "${ROOT}/canary/plan.py" show "${id}" | python3 -c 'import json, sys; print(json.load(sys.stdin)["revision"])' > "${d}/.complete"
done

STUB=${ROOT}/tests/openai_stub.py
GOLD=" Paris. The capital of Italy is Rome"
LLAMA_HELP=${FIX}/help/llama-server-b11960-help.txt
VLLM_HELP=${FIX}/help/vllm-0.32.0-cpu-serve-help-all.txt

# The versions the stubs report are the ones the tree's pins name (canary/plan.py show),
# so a bump of any of these pins leaves the cases below as they are. pinned <id> <key>
# prints the engine's expected_version (key v) or its first derived constant (key d).
pinned() {
    python3 "${ROOT}/canary/plan.py" show "$1" | python3 -c 'import json, sys
d = json.load(sys.stdin)
print(d["expected_version"] if sys.argv[1] == "v" else d["derived"][0]["value"])' "$2"
}
LLAMA_V=$(pinned llama-cpu v)     # llama.cpp build number, from server-b<N>
VLLM_V=$(pinned vllm-cpu v)       # X.Y.Z, from vX.Y.Z
EAT_V=$(pinned eat-vllm-cpu v)
OVMS_V=$(pinned ovms-cpu v)       # empty while the pin has no tag: nothing is compared
SGLANG_V=$(pinned sglang-xeon v)
SCALER_V=$(pinned llm-scaler d)   # console/plan.py's llm-scaler vLLM version
for v in LLAMA_V VLLM_V EAT_V SGLANG_V SCALER_V; do
    [ -n "${!v}" ] || fail "${v}: the tree's pin names no version; the cases below need one"
done
LLAMA_OK="--build-info b${LLAMA_V}-8642541b2"

# run_case <label> <engine> [VAR=value...]: canary/run.sh <engine> in a fresh state and
# output directory; leaves the result in RESULT and run.sh's exit status in RC.
run_case() {
    local label=$1 id=$2 f
    shift 2
    for f in "${T}"/state/*.pid; do
        [ -f "${f}" ] && kill -KILL -- "-$(cat "${f}")" 2>/dev/null
    done
    rm -rf "${T}/state" "${T}/out"
    mkdir -p "${T}/state"
    START=${SECONDS}
    env "$@" CANARY_OUT_DIR="${T}/out" "${ROOT}/canary/run.sh" "${id}" > "${T}/${label}.log" 2>&1
    RC=$?
    ELAPSED=$((SECONDS - START))
    RESULT=${T}/out/${id}.json
    [ -f "${RESULT}" ] || { fail "${label}: no ${id}.json (run.sh rc=${RC}); log:"; sed 's/^/    /' "${T}/${label}.log" | tail -20; return 1; }
}
field() { python3 -c 'import json, sys; v = json.load(open(sys.argv[1]))[sys.argv[2]]; print(json.dumps(v) if not isinstance(v, str) else v)' "${RESULT}" "$1"; }
expect() { # expect <label> <field> <value>
    local got
    got=$(field "$2")
    if [ "${got}" = "$3" ]; then ok "$1: $2=$3"; else fail "$1: $2=${got}, want $3 ($(field detail))"; fi
}

# 1. A good server: hard, exact, version, stream; the surface check reads the help output.
run_case good llama-cpu STUB_HELP="${LLAMA_HELP}" \
    STUB_SERVER="python3 ${STUB} --port {port} --text '${GOLD}' ${LLAMA_OK}"
expect good verdict pass
expect good hard true
expect good exact true
expect good kind runtime
expect good cpu_flags '["avx2", "avx512f", "amx_tile"]'
want_ref=$(python3 "${ROOT}/canary/plan.py" ref llama-cpu)
expect good pinned_ref "${want_ref}"
[ "${RC}" -eq 0 ] && ok "good: run.sh exits 0" || fail "good: run.sh exited ${RC}"
keys=$(python3 -c 'import json, sys; print(" ".join(sorted(json.load(open(sys.argv[1])))))' "${RESULT}")
[ "${keys}" = "cpu_flags detail exact hard kind pinned_ref verdict" ] && ok "good: result has exactly the interface keys" \
    || fail "good: result keys are '${keys}'"
grep -q -- '--threads 4\|--threads [0-9]' "${STUB_STATE}/calls" && ok "good: {threads} is substituted" \
    || fail "good: no --threads N in the docker run call"
grep -qE -- "--cap-add IPC_LOCK" "${STUB_STATE}/calls" && ok "good: llama-cpu runs with IPC_LOCK (mlock)" \
    || fail "good: llama-cpu docker run has no --cap-add IPC_LOCK"
grep -q '^docker rm -f canary-llama-cpu' "${STUB_STATE}/calls" && ok "good: the engine container is removed" \
    || fail "good: no docker rm -f of the engine container"

# 2. Empty content: no text and no tokens is a failure.
run_case empty llama-cpu STUB_HELP="${LLAMA_HELP}" \
    STUB_SERVER="python3 ${STUB} --port {port} --text '' ${LLAMA_OK}"
expect empty verdict fail
expect empty hard false
[ "${RC}" -ne 0 ] && ok "empty: run.sh exits non-zero" || fail "empty: run.sh exited 0"

# 3. A stream that never sends [DONE].
run_case nodone llama-cpu STUB_HELP="${LLAMA_HELP}" \
    STUB_SERVER="python3 ${STUB} --port {port} --text '${GOLD}' ${LLAMA_OK} --no-done"
expect nodone verdict fail
grep -q 'DONE' <(field detail) && ok "nodone: detail names [DONE]" || fail "nodone: detail is '$(field detail)'"

# 4. The server reports another build than the pinned tag.
run_case version llama-cpu STUB_HELP="${LLAMA_HELP}" \
    STUB_SERVER="python3 ${STUB} --port {port} --text '${GOLD}' --build-info b$((LLAMA_V - 1))-0000000"
expect version verdict fail
grep -q "b$((LLAMA_V - 1))-" <(field detail) && ok "version: detail names the server's version" || fail "version: detail is '$(field detail)'"

# 5. A golden mismatch keeps hard and clears exact.
run_case golden llama-cpu STUB_HELP="${LLAMA_HELP}" \
    STUB_SERVER="python3 ${STUB} --port {port} --text ' Paris, of course, is' ${LLAMA_OK}"
expect golden verdict pass
expect golden hard true
expect golden exact false

# 6. A server that never lists its model fails at the timeout, not later.
run_case never llama-cpu STUB_HELP="${LLAMA_HELP}" CANARY_READY_TIMEOUT=3 \
    STUB_SERVER="python3 ${STUB} --port {port} --never-ready"
expect never verdict fail
[ "${ELAPSED}" -le 30 ] && ok "never: gave up within the timeout (${ELAPSED}s)" || fail "never: took ${ELAPSED}s"
grep -qi 'ready' <(field detail) && ok "never: detail says it never became ready" || fail "never: detail is '$(field detail)'"

# 6b. A probe that writes no result (here: it cannot parse its timeout) is a failure, never a pass.
run_case noprobe llama-cpu STUB_HELP="${LLAMA_HELP}" CANARY_READY_TIMEOUT=soon \
    STUB_SERVER="python3 ${STUB} --port {port} --text '${GOLD}' ${LLAMA_OK}"
expect noprobe verdict fail
expect noprobe hard false

# 7. OVMS: the pull step, then /v3 on fake_ovms.py, reporting the version its pin's tag
# names (a pin with no tag is not compared).
run_case ovms ovms-cpu FAKE_OVMS_TEXT="${GOLD}" FAKE_OVMS_VERSION="${OVMS_V:-2026.4.1}.cfc9ac721" \
    STUB_SERVER="python3 ${ROOT}/tests/fake_ovms.py {port}"
expect ovms verdict pass
expect ovms exact true
grep -q -- '--pull --source_model=OpenVINO/Qwen3-0.6B-int8-ov' "${STUB_STATE}/calls" \
    && ok "ovms: the pull step runs first" || fail "ovms: no --pull run before the server"
grep -q 'HF_ENDPOINT=http://127.0.0.1:9' "${STUB_STATE}/calls" && ok "ovms: the pull step cannot fetch" \
    || fail "ovms: the pull step is not offline"

# 8. vLLM: help output and the static parse of v0.32.0's sources both pass.
VLLM_OK=(STUB_HELP="${VLLM_HELP}" STUB_SITE="${FIX}/vllm-0.32.0"
         STUB_SERVER="python3 ${STUB} --port {port} --text '${GOLD}' --version ${VLLM_V}+cpu")
run_case vllm vllm-cpu "${VLLM_OK[@]}"
expect vllm verdict pass
expect vllm hard true

# 9. A flag the launcher emits that the image's help lacks fails the surface check.
grep -v -- '--served-model-name' "${VLLM_HELP}" > "${T}/vllm-help-missing.txt"
run_case vllm-flag vllm-cpu "${VLLM_OK[@]}" STUB_HELP="${T}/vllm-help-missing.txt"
expect vllm-flag verdict fail
grep -q -- '--served-model-name' <(field detail) && ok "vllm-flag: detail names the flag" || fail "vllm-flag: detail is '$(field detail)'"

# 10. Sources the static parse cannot read are unparseable, never skipped.
cp -r "${FIX}/vllm-0.32.0" "${T}/broken-site"
rm "${T}/broken-site/vllm/envs.py"
run_case vllm-unparseable vllm-cpu "${VLLM_OK[@]}" STUB_SITE="${T}/broken-site"
expect vllm-unparseable verdict unparseable
expect vllm-unparseable hard false

# 11. An engine killed by SIGILL on a runner without AVX-512 is isa-skip; with it, a failure.
run_case isa vllm-cpu "${VLLM_OK[@]}" CANARY_CPUINFO="${T}/cpuinfo.avx2" \
    STUB_SERVER="echo 'Illegal instruction (core dumped)'; exit 132"
expect isa verdict isa-skip
expect isa hard false
expect isa cpu_flags '["avx2"]'
run_case isa512 vllm-cpu "${VLLM_OK[@]}" STUB_SERVER="echo 'Illegal instruction (core dumped)'; exit 132"
expect isa512 verdict fail

# 12. An image larger than the free disk is too-big, and is never pulled.
run_case big llama-cpu STUB_HELP="${LLAMA_HELP}" STUB_IMAGE_BYTES=900000000000 CANARY_FREE_BYTES=1000000000 \
    STUB_SERVER="python3 ${STUB} --port {port} --text '${GOLD}' ${LLAMA_OK}"
expect big verdict too-big
! grep -q '^docker pull' "${STUB_STATE}/calls" && ok "big: no pull" || fail "big: pulled anyway"

# 13. Pulls retry: two failures then success passes; three failures fail.
run_case pull2 llama-cpu STUB_HELP="${LLAMA_HELP}" STUB_PULL_FAILS=2 \
    STUB_SERVER="python3 ${STUB} --port {port} --text '${GOLD}' ${LLAMA_OK}"
expect pull2 verdict pass
[ "$(grep -c '^docker pull' "${STUB_STATE}/calls")" -eq 3 ] && ok "pull2: three attempts" || fail "pull2: $(grep -c '^docker pull' "${STUB_STATE}/calls") attempts"
run_case pull3 llama-cpu STUB_HELP="${LLAMA_HELP}" STUB_PULL_FAILS=3 \
    STUB_SERVER="python3 ${STUB} --port {port} --text '${GOLD}' ${LLAMA_OK}"
expect pull3 verdict fail
grep -qi 'pull' <(field detail) && ok "pull3: detail names the pull" || fail "pull3: detail is '$(field detail)'"

# 14. An informational engine's failure does not fail run.sh.
run_case eat eat-vllm-cpu "${VLLM_OK[@]}" STUB_SERVER="python3 ${STUB} --port {port} --text '' --version ${EAT_V}"
expect eat verdict fail
[ "${RC}" -eq 0 ] && ok "eat: informational, run.sh exits 0" || fail "eat: run.sh exited ${RC}"

# 15. A model not in the cache is fetched at the pinned revision, then reused.
id=llama-cpu
rev=$(python3 "${ROOT}/canary/plan.py" show "${id}" | python3 -c 'import json, sys; print(json.load(sys.stdin)["revision"])')
mkdir -p "${T}/hf/ggml-org/Qwen3-0.6B-GGUF/resolve/${rev}"
echo gguf-bytes > "${T}/hf/ggml-org/Qwen3-0.6B-GGUF/resolve/${rev}/Qwen3-0.6B-Q8_0.gguf"
python3 -u -m http.server 0 --bind 127.0.0.1 --directory "${T}/hf" > "${T}/hf.log" 2>&1 &
HF_PID=$!
for _ in $(seq 50); do grep -q 'Serving HTTP' "${T}/hf.log" && break; sleep 0.1; done
HF_PORT=$(sed -n 's/.*port \([0-9]*\).*/\1/p' "${T}/hf.log" | head -1)
run_case fetch llama-cpu CANARY_MODELS="${T}/fresh" HF_ENDPOINT="http://127.0.0.1:${HF_PORT}" STUB_HELP="${LLAMA_HELP}" \
    STUB_SERVER="python3 ${STUB} --port {port} --text '${GOLD}' ${LLAMA_OK}"
expect fetch verdict pass
d=$(CANARY_MODELS="${T}/fresh" python3 "${ROOT}/canary/plan.py" model-dir "${id}")
[ "$(cat "${d}/Qwen3-0.6B-Q8_0.gguf" 2>/dev/null)" = gguf-bytes ] && ok "fetch: the file came from <model>/resolve/<revision>" \
    || fail "fetch: ${d}/Qwen3-0.6B-Q8_0.gguf missing"
[ "$(cat "${d}/.complete" 2>/dev/null)" = "${rev}" ] && ok "fetch: .complete names the revision" || fail "fetch: no .complete"
kill "${HF_PID}" 2>/dev/null
wait "${HF_PID}" 2>/dev/null
HF_PID=""
run_case cached llama-cpu CANARY_MODELS="${T}/fresh" HF_ENDPOINT="http://127.0.0.1:${HF_PORT}" STUB_HELP="${LLAMA_HELP}" \
    STUB_SERVER="python3 ${STUB} --port {port} --text '${GOLD}' ${LLAMA_OK}"
expect cached verdict pass

# run_surface <label> <engine> [VAR=value...]: canary/run.sh --surface <engine>, as run_case.
run_surface() {
    local label=$1 id=$2
    shift 2
    rm -rf "${T}/state" "${T}/out"
    mkdir -p "${T}/state"
    env "$@" CANARY_OUT_DIR="${T}/out" "${ROOT}/canary/run.sh" --surface "${id}" > "${T}/${label}.log" 2>&1
    RC=$?
    RESULT=${T}/out/${id}.json
    [ -f "${RESULT}" ] || { fail "${label}: no ${id}.json (run.sh rc=${RC}); log:"; sed 's/^/    /' "${T}/${label}.log" | tail -20; return 1; }
}

# 16. sglang: bench/h54's --tp is argparse's abbreviation of --tp-size, the only option
# it begins; once a second option begins with it, argparse calls it ambiguous. The
# v0.5.22 site reports the version the tree's pin names.
SGLANG_HELP=${FIX}/help/sglang-0.5.22-xeon-help.txt
cp -r "${FIX}/sglang-0.5.22" "${T}/sglang-site"
sed -i -E "s/^(__version__ = version = )'[^']*'/\1'${SGLANG_V}'/" "${T}/sglang-site/sglang/_version.py"
run_surface sglang sglang-xeon STUB_HELP="${SGLANG_HELP}" STUB_SITE="${T}/sglang-site"
expect sglang verdict pass
expect sglang kind surface
grep -q -- "--tp is argparse's abbreviation of --tp-size" <(field detail) && ok "sglang: detail names the abbreviation" \
    || fail "sglang: detail is '$(field detail)'"
grep -qF "version ${SGLANG_V}" <(field detail) && ok "sglang: version ${SGLANG_V} from sglang/_version.py" \
    || fail "sglang: detail is '$(field detail)'"
{ cat "${SGLANG_HELP}"; echo "  --tp-rank TP_RANK     a new option"; } > "${T}/sglang-help-ambiguous.txt"
run_surface sglang-ambiguous sglang-xeon STUB_HELP="${T}/sglang-help-ambiguous.txt" STUB_SITE="${T}/sglang-site"
expect sglang-ambiguous verdict fail
grep -q -- '--tp (bench/h54/h54.py:sglang_args)' <(field detail) && ok "sglang-ambiguous: detail names --tp" \
    || fail "sglang-ambiguous: detail is '$(field detail)'"

# 17. llm-scaler: its vLLM reports the setuptools-scm version of a tag with patches
# (intel/llm-scaler-vllm 1.5: 0.26.1.dev0+g568afb3a1.d<date>, vLLM v0.26.0's commit),
# which the X.Y.Z the tree derives (SCALER_V) matches; a build commits after a tag does not.
scaler_site() { # scaler_site <version>: the v0.32.0 tree, reporting <version>
    rm -rf "${T}/scaler-site"
    cp -r "${FIX}/vllm-0.32.0" "${T}/scaler-site"
    sed -i "s/^Version: .*/Version: $1/" "${T}/scaler-site/vllm-0.32.0+cpu.dist-info/METADATA"
}
SCALER_NEXT=${SCALER_V%.*}.$((${SCALER_V##*.} + 1))  # setuptools-scm's next version of the tag
scaler_site "${SCALER_NEXT}.dev0+g568afb3a1.d20260907.xpu"
run_surface scaler llm-scaler STUB_HELP="${VLLM_HELP}" STUB_SITE="${T}/scaler-site"
expect scaler verdict pass
scaler_site "${SCALER_NEXT}.dev4+g1a2b3c4d.d20260907.xpu"
run_surface scaler-ahead llm-scaler STUB_HELP="${VLLM_HELP}" STUB_SITE="${T}/scaler-site"
expect scaler-ahead verdict fail
grep -qF "console/plan.py says ${SCALER_V}," <(field detail) && ok "scaler-ahead: detail names console/plan.py" \
    || fail "scaler-ahead: detail is '$(field detail)'"

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
