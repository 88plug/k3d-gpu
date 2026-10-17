#!/usr/bin/env bash
# Remote OpenAI-compatible endpoints in the launcher (scripts/k3d-gpu): `remote`,
# `agent up|status|down --remote`, `quickstart --remote`, `sandbox up --remote`,
# the capacity/tune/bench guards, doctor, and K3D_GPU_VENDORS=none.
# No cluster, no network: kubectl, docker, k3d and the launcher's own `up` are
# stubs, and tests/openai_auth_stub.py is the endpoint (it checks the bearer key).
# The key is fake. The redaction checks at the end grep every log, every output
# and every file the runs left for the key and its base64.
#
# Usage: tests/remote_test.sh   (console/remote.py itself: tests/remote_test.py)
# shellcheck disable=SC1090,SC2034

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHER="${ROOT}/scripts/k3d-gpu"
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }
eq()   { if [ "$2" = "$3" ]; then ok "$1"; else fail "$1: got '$2', want '$3'"; fi; }
has()  { if grep -Fq -- "$3" <<< "$2"; then ok "$1"; else fail "$1: missing '$3' in: $(head -c 600 <<< "$2")"; fi; }
lacks(){ if grep -Fq -- "$3" <<< "$2"; then fail "$1: unexpected '$3'"; else ok "$1"; fi; }

unset OPENAI_BASE_URL OPENAI_API_BASE OPENAI_HOST OPENAI_API_KEY OPENAI_MODEL
KEY="sk-test-remote-0123456789abcdef"
KEY_B64=$(printf '%s' "${KEY}" | base64 -w0)
TMP=$(mktemp -d)
STUB_PID=""
trap '[ -z "${STUB_PID}" ] || kill "${STUB_PID}" 2>/dev/null; kill $(cat "${TMP}"/s/pf.pids 2>/dev/null) 2>/dev/null; rm -rf "${TMP}"' EXIT
S="${TMP}/s" B="${TMP}/bin"
mkdir -p "${S}" "${B}"
ALL_OUT="${TMP}/all-output"

# The endpoint.
coproc STUB { exec python3 "${ROOT}/tests/openai_auth_stub.py" --key "${KEY}" --log "${S}/endpoint.log"; }
read -r _ PORT <&"${STUB[0]}"
STUB_PID=${STUB_PID:-$!}
URL="http://127.0.0.1:${PORT}/v1"

lib() { sed '/^case "\${1:-help}" in$/,$d' "${LAUNCHER}"; }

# kubectl stub: argv to ${S}/kubectl.log. Stdin of an apply is checked, not stored:
# "stdin-secret" when it carried the base64 key, and the server-side flag.
cat > "${B}/kubectl" <<'EOF'
#!/usr/bin/env bash
echo "kubectl $*" >> "${STUB_STATE}/kubectl.log"
case "$*" in
    "get nodes") exit 0 ;;
    *"create namespace"*) echo "kind: Namespace" ;;
    *"apply --server-side"*)
        in=$(cat)
        grep -q "OPENAI_API_KEY: ${STUB_KEY_B64}" <<< "${in}" && echo "stdin-secret $*" >> "${STUB_STATE}/kubectl.log"
        ns=$(sed -n 's/^  namespace: //p' <<< "${in}")
        printf '%s\t%s' "$(sed -n 's/^    k3d-gpu.io\/base-url: "\(.*\)"/\1/p' <<< "${in}")" \
            "$(sed -n 's/^    k3d-gpu.io\/model: "\(.*\)"/\1/p' <<< "${in}")" > "${STUB_STATE}/secret-${ns}"
        printf '%s' "${STUB_KEY_B64}" > "${STUB_STATE}/secret-key-${ns}" ;;
    *"apply -f -"*) cat >> "${STUB_STATE}/applied" ;;
    *"get secret k3d-gpu-llm -o go-template"*)
        ns=$2; [ -f "${STUB_STATE}/secret-${ns}" ] || exit 1; cat "${STUB_STATE}/secret-${ns}" ;;
    *"get secret k3d-gpu-llm -o jsonpath"*)
        ns=$2; [ -f "${STUB_STATE}/secret-key-${ns}" ] || exit 1; cat "${STUB_STATE}/secret-key-${ns}" ;;
    *"get sandboxtemplate"*) [ -f "${STUB_STATE}/pin" ] || exit 1; cat "${STUB_STATE}/pin" ;;
    *"get secret open-webui-admin"*) exit 1 ;;
    *"delete secret"*) rm -f "${STUB_STATE}/secret-${2}" "${STUB_STATE}/secret-key-${2}" ;;
esac
exit 0
EOF
cat > "${B}/k3d" <<'EOF'
#!/usr/bin/env bash
echo "k3d $*" >> "${STUB_STATE}/k3d.log"
case "$1 $2" in "cluster get") [ -f "${STUB_STATE}/cluster" ] ;; esac
EOF
cat > "${B}/docker" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "${B}/self" <<'EOF'
#!/usr/bin/env bash
echo "self $* vendors=${K3D_GPU_VENDORS:-}" >> "${STUB_STATE}/self.log"
[ "$1" != up ] || touch "${STUB_STATE}/cluster"
EOF
mkdir -p "${TMP}/bench/apb"
cat > "${TMP}/bench/apb/capacity.py" <<'EOF'
import os, sys
print("capacity-args " + " ".join(sys.argv[1:]))
print("capacity-key " + ("set" if os.environ.get("OPENAI_API_KEY") else "unset"))
EOF
chmod +x "${B}"/*

# run <fn> ARGS...: the launcher's functions sourced in a subshell with the stubs;
# output (both streams) is also appended to ${ALL_OUT} for the redaction check.
run() {
    (export PATH="${B}:${PATH}" STUB_STATE="${S}" STUB_KEY_B64="${KEY_B64}" K3D_GPU_CONSOLE="${ROOT}/console" \
        K3D_GPU_QUICKSTART_DIR="${S}/qs" K3D_GPU_CHAT_UI="${ROOT}/share/agent-chat-ui.yml" K3D_GPU_BENCH="${TMP}/bench" \
        K3D_GPU_SANDBOX="${ROOT}/share/agent-sandbox.yml" K3D_GPU_SANDBOX_PROFILE="${ROOT}/share/agent-sandbox-k3d.yml"
     source <(lib); SYSROOT="${TMP}/host"; qs_self() { echo "${B}/self"; }; "$@") 2>&1 | tee -a "${ALL_OUT}"
}
mkdir -p "${TMP}/host/proc" && echo "Linux version 6.6.0" > "${TMP}/host/proc/version"

# ---- remote_parse and remote_url_local ----------------------------------------
out=$(run eval 'remote_parse --remote https://x.example --model m --api-key-file /k --i-own-this-endpoint --no-ui; echo "R=${REMOTE} O=${OWN_ENDPOINT} F=${REMOTE_KEYFILE} A=${REMOTE_ARGS[*]} REST=${REMOTE_REST[*]}"')
eq "parse: every remote flag, the rest kept" "${out}" "R=1 O=1 F=/k A=--remote https://x.example --model m --api-key-file /k REST=--no-ui"
out=$(run eval 'remote_parse --remote --no-ui; echo "R=${REMOTE} A=${REMOTE_ARGS[*]:-} REST=${REMOTE_REST[*]}"')
eq "parse: bare --remote takes no value that is not a URL" "${out}" "R=1 A= REST=--no-ui"
out=$(run eval 'remote_parse --remote=https://y.example/v1; echo "${REMOTE_ARGS[*]}"')
eq "parse: --remote=URL" "${out}" "--remote https://y.example/v1"
for u in http://127.0.0.1:8000/v3 http://localhost:8000 'http://[::1]:8000/v1' http://ovms.k3d-gpu-agent.svc:8000/v3 http://vllm.ns.svc.cluster.local:8000/v1; do
    run remote_url_local "${u}" >/dev/null && ok "local: ${u}" || fail "local: ${u}"
done
for u in https://gaudi.example.net/v1 http://10.0.0.5:8000/v1 https://evil.svc.example.com/v1 https://127.0.0.1.example.com/v1; do
    run remote_url_local "${u}" >/dev/null && fail "not local: ${u}" || ok "not local: ${u}"
done

# ---- remote check through the launcher ----------------------------------------
out=$(env PATH="${B}:${PATH}" K3D_GPU_CONSOLE="${ROOT}/console" OPENAI_BASE_URL="${URL}" OPENAI_API_KEY="${KEY}" \
    "${LAUNCHER}" remote check 2>&1 | tee -a "${ALL_OUT}")
has "check: host" "${out}" "host      127.0.0.1"
has "check: models" "${out}" "models    zai-org/GLM-5.3"
has "check: auth ok with latency" "${out}" "auth      ok (GET /models in"
has "check: key masked" "${out}" "key       sk-…cdef (from OPENAI_API_KEY)"
has "check: one chat" "${out}" "chat      ok: HTTP 200"
eq "check: two requests, chat at 16 tokens, temperature not 0" "$(cat "${S}/endpoint.log")" \
    "GET /v1/models auth=ok max_tokens=None temperature=None
POST /v1/chat/completions auth=ok max_tokens=16 temperature=0.6"
out=$(env PATH="${B}:${PATH}" K3D_GPU_CONSOLE="${ROOT}/console" OPENAI_HOST="http://127.0.0.1:${PORT}" OPENAI_API_KEY="sk-test-wrong-000000000" \
    "${LAUNCHER}" remote check 2>&1 | tee -a "${ALL_OUT}"); rc=${PIPESTATUS[0]}
has "check: OPENAI_HOST gets /v1" "${out}" "endpoint  ${URL} (from OPENAI_HOST)"
has "check: a wrong key is rejected by name" "${out}" "rejected the key (HTTP 401"
lacks "check: the wrong key is not echoed" "${out}" "sk-test-wrong-000000000"
umask 077; printf 'OPENAI_API_KEY=%s\n' "${KEY}" > "${TMP}/key.env"; umask 022
out=$(env PATH="${B}:${PATH}" K3D_GPU_CONSOLE="${ROOT}/console" OPENAI_API_KEY="" \
    "${LAUNCHER}" remote check --remote "${URL}" --api-key-file "${TMP}/key.env" --json 2>&1 | tee -a "${ALL_OUT}")
has "check: --remote and --api-key-file" "${out}" '"auth": "ok"'

# ---- agent up --remote / status / down ------------------------------------------
: > "${S}/endpoint.log"
out=$(OPENAI_BASE_URL="${URL}" OPENAI_API_KEY="${KEY}" run cmd_agent up --remote)
has "agent up --remote: no local server" "${out}" "no local model server"
has "agent up --remote: envFrom hint" "${out}" "envFrom: [{secretRef: {name: k3d-gpu-llm}}]"
log=$(cat "${S}/kubectl.log")
has "agent up --remote: Secret applied server-side from stdin with the key" "${log}" "stdin-secret apply --server-side --force-conflicts --field-manager=k3d-gpu -f -"
lacks "agent up --remote: no local model deployment" "${log}" "rollout status"
lacks "agent up --remote: no --from-literal" "${log}" "--from-literal"
eq "agent up --remote: one GET /models, no chat" "$(cat "${S}/endpoint.log")" "GET /v1/models auth=ok max_tokens=None temperature=None"
out=$(OPENAI_BASE_URL="${URL}" OPENAI_API_KEY="${KEY}" run cmd_agent up --remote --with vlm); rc=$?
has "agent up --remote refuses other options" "${out}" "takes no other options"
out=$(run cmd_agent status --remote)
has "agent status takes no --remote" "${out}" "--remote goes with 'agent up'"
out=$(OPENAI_API_KEY="" run cmd_agent_status)
has "agent status: endpoint from the Secret" "${out}" "remote endpoint ${URL}, model zai-org/GLM-5.3"
has "agent status: checked with the Secret's key" "${out}" "ok: 127.0.0.1:${PORT}/v1 answers GET /models with the Secret's key"
printf 'https://other.example/v1\tm' > "${S}/secret-agent-sandbox"
out=$(run cmd_agent_status)
has "agent status: a sandbox copy naming another endpoint fails" "${out}" "names 'https://other.example/v1 m'"
rm -f "${S}/secret-agent-sandbox"
out=$(run cmd_agent down)
has "agent down deletes the Secret" "$(cat "${S}/kubectl.log")" "delete secret k3d-gpu-llm --ignore-not-found"
out=$(run cmd_agent up --model m)
has "agent: --model without --remote is refused" "${out}" "go with --remote"

# ---- quickstart --remote ---------------------------------------------------------
: > "${S}/endpoint.log"; : > "${S}/kubectl.log"; rm -f "${S}/cluster" "${S}/self.log"
out=$(OPENAI_BASE_URL="${URL}" OPENAI_API_KEY="${KEY}" K3D_GPU_VENDORS="" \
    run eval 'detect_intel() { :; }; resolve_vendors() { ACTIVE=(); }; qs_ui_up() { printf "%s" "$1" > "${STUB_STATE}/ui.yaml"; echo UI-UP; }; cmd_quickstart --remote')
rc=$?
has "quickstart --remote: endpoint first" "${out}" "remote endpoint ${URL} (from OPENAI_BASE_URL), model zai-org/GLM-5.3: no model download"
has "quickstart --remote: no accelerator -> plain cluster" "$(cat "${S}/self.log")" "self up vendors=none"
lacks "quickstart --remote: no local agent up" "$(cat "${S}/self.log")" "self agent"
has "quickstart --remote: Secret written" "$(cat "${S}/kubectl.log")" "stdin-secret apply --server-side"
has "quickstart --remote: UI started" "${out}" "UI-UP"
ui=$(cat "${S}/ui.yaml")
has "quickstart --remote: UI reads the base URL from the Secret" "${ui}" "secretKeyRef: {name: k3d-gpu-llm, key: OPENAI_BASE_URL}"
has "quickstart --remote: UI reads the key from the Secret" "${ui}" "secretKeyRef: {name: k3d-gpu-llm, key: OPENAI_API_KEY}"
has "quickstart --remote: UI default model" "${ui}" 'value: "zai-org/GLM-5.3"'
has "quickstart --remote: persistent config off" "${ui}" 'name: ENABLE_PERSISTENT_CONFIG'
eq "quickstart --remote: one request to the endpoint (GET /models)" "$(wc -l < "${S}/endpoint.log")" 1
out=$(OPENAI_BASE_URL="${URL}" OPENAI_API_KEY="${KEY}" run cmd_quickstart --remote --no-ui)
has "quickstart --remote --no-ui: reuses the cluster and is ready" "${out}" "Ready (no chat UI)"
out=$(OPENAI_BASE_URL="https://gaudi.example.invalid/v1" OPENAI_API_KEY="" run cmd_quickstart --remote)
has "quickstart --remote without a key stops before the cluster" "${out}" "needs a key"
out=$(OPENAI_BASE_URL="${URL}" run eval 'qs_wsl_guidance() { :; }; k3d() { return 0; }; kubectl() { return 1; }; cmd_quickstart')
has "quickstart without --remote: an env endpoint is only a hint" "${out}" "'k3d-gpu quickstart --remote' uses it"

# ---- sandbox up --remote ---------------------------------------------------------
: > "${S}/kubectl.log"; : > "${S}/applied"
out=$(K3D_GPU_REMOTE_ADDRS=104.21.77.178,172.67.210.235 OPENAI_BASE_URL="https://llm.example.net/v1" OPENAI_API_KEY="${KEY}" \
    run eval 'remote_resolve() { printf "https://llm.example.net/v1\tzai-org/GLM-5.3\tOPENAI_BASE_URL\n"; }; sandbox_status() { :; }; SANDBOX_TIMEOUT=0; kubectl() { command kubectl "$@"; [ "$*" != "-n agent-sandbox get sandboxwarmpool python-ecores -o jsonpath={.status.readyReplicas}" ] || echo 1; }; cmd_sandbox up --remote')
has "sandbox up --remote: egress named" "${out}" "sandbox egress is llm.example.net/v1 only, no DNS"
applied=$(cat "${S}/applied")
has "sandbox up --remote: /32 per address" "${applied}" "cidr: 104.21.77.178/32"
lacks "sandbox up --remote: no open internet rule" "${applied}" "cidr: 0.0.0.0/0"
lacks "sandbox up --remote: no DNS egress" "${applied}" "k8s-app: kube-dns"
has "sandbox up --remote: hostAliases" "${applied}" "hostnames: [llm.example.net]"
has "sandbox up --remote: sandbox Secret copy" "$(cat "${S}/kubectl.log")" "stdin-secret apply --server-side --force-conflicts --field-manager=k3d-gpu -f -"
grep -n 'stdin-secret\|apply -f -' "${S}/kubectl.log" | grep -q 'stdin-secret' && \
    [ "$(grep -n 'stdin-secret' "${S}/kubectl.log" | head -1 | cut -d: -f1)" -lt "$(grep -n '^kubectl apply -f -$' "${S}/kubectl.log" | tail -1 | cut -d: -f1)" ] \
    && ok "sandbox up --remote: Secret before the profile" || fail "sandbox up --remote: Secret before the profile"
out=$(run cmd_sandbox status --remote)
has "sandbox: --remote only with up" "${out}" "--remote goes with 'sandbox up'"
printf 'llm.example.net 104.21.77.178,172.67.210.235' > "${S}/pin"
out=$(K3D_GPU_REMOTE_ADDRS=104.21.77.178,172.67.210.235 run eval 'has_hybrid() { return 1; }; sandbox_status')
has "sandbox status: pin matches" "${out}" "ok: egress pinned to llm.example.net"
out=$(K3D_GPU_REMOTE_ADDRS=104.21.77.178,188.114.96.1 run eval 'has_hybrid() { return 1; }; sandbox_status')
has "sandbox status: a moved endpoint fails" "${out}" "now resolves to 104.21.77.178,188.114.96.1"
rm -f "${S}/pin"

# ---- capacity / tune / bench guards ------------------------------------------------
out=$(OPENAI_BASE_URL="${URL}" OPENAI_API_KEY="${KEY}" run cmd_capacity --remote)
has "capacity --remote: refused without --dry-run or ownership" "${out}" "needs --dry-run, or --i-own-this-endpoint"
out=$(run cmd_capacity --url https://gaudi.example.net/v1 --model m)
has "capacity --url to a remote host: refused the same way" "${out}" "needs --dry-run, or --i-own-this-endpoint"
out=$(run cmd_capacity --url http://127.0.0.1:8000/v3 --model m)
has "capacity --url to loopback: unchanged" "${out}" "capacity-args --url http://127.0.0.1:8000/v3 --model m"
lacks "capacity loopback: no budget added" "${out}" "--budget 5"
: > "${S}/endpoint.log"
out=$(OPENAI_BASE_URL="${URL}" OPENAI_API_KEY="${KEY}" run cmd_capacity --remote --dry-run)
has "capacity --remote --dry-run: resolved, nothing sent" "${out}" "capacity-args --dry-run --url ${URL} --model auto"
eq "capacity --remote --dry-run: no request" "$(wc -l < "${S}/endpoint.log")" 0
out=$(run eval "remote_resolve() { printf 'https://gaudi.example.net/v1\tm\tOPENAI_BASE_URL\n'; }; cmd_capacity --remote --i-own-this-endpoint --slo 5 --api-key-file ${TMP}/key.env")
has "capacity --remote owned: budgeted" "${out}" "capacity-args --slo 5 --url https://gaudi.example.net/v1 --model m --budget 5 --max 4"
has "capacity --remote owned: key handed over in the environment" "${out}" "capacity-key set"
out=$(run eval "remote_resolve() { printf 'https://gaudi.example.net/v1\tm\tx\n'; }; cmd_capacity --remote --i-own-this-endpoint --budget 2 --max 2")
has "capacity owned: the user's budget kept" "${out}" "--budget 2 --max 2 --url https://gaudi.example.net/v1 --model m"
lacks "capacity owned: no second budget" "${out}" "--budget 5"
out=$(run cmd_tune run --remote)
has "tune --remote is refused" "${out}" "a remote endpoint has no knob"
out=$(run cmd_bench --vs remote)
has "bench --vs remote: refused without ownership" "${out}" "needs --dry-run, or --i-own-this-endpoint"
out=$(OPENAI_BASE_URL="https://gaudi.example.net" OPENAI_API_KEY="${KEY}" run cmd_bench --vs remote --dry-run --rounds 5 --max-tokens 500)
has "bench --vs remote --dry-run: budgeted plan" "${out}" "--rounds 1 --warmup 1 --max-tokens 64 --remote-arm remote"
has "bench --vs remote --dry-run: remote arm" "${out}" "--arm remote=https://gaudi.example.net/v1,"
out=$(run cmd_bench --vs ollama --remote)
has "bench: --remote only with --vs remote" "${out}" "go with --vs remote"

# ---- doctor ------------------------------------------------------------------------
out=$(OPENAI_BASE_URL="${URL}" OPENAI_API_KEY="${KEY}" run eval 'REMOTE=1; FAIL=0; doctor_remote; echo "FAIL=${FAIL}"')
has "doctor --remote: reachable" "${out}" "ok: ${URL} answers GET /models; model zai-org/GLM-5.3"
has "doctor --remote: passes" "${out}" "FAIL=0"
out=$(OPENAI_BASE_URL="${URL}" OPENAI_API_KEY="sk-test-wrong-000000000" run eval 'REMOTE=1; FAIL=0; doctor_remote; echo "FAIL=${FAIL}"')
has "doctor --remote: rejected key fails" "${out}" "FAIL=1"
out=$(OPENAI_BASE_URL="${URL}" OPENAI_API_KEY="sk-test-wrong-000000000" run eval 'FAIL=0; doctor_remote; echo "FAIL=${FAIL}"')
has "doctor, env only: a warning" "${out}" "warn"
has "doctor, env only: does not fail" "${out}" "FAIL=0"
out=$(run eval 'doctor_remote; echo done')
eq "doctor without an endpoint: silent" "${out}" "done"

# ---- K3D_GPU_VENDORS=none ------------------------------------------------------------
out=$(K3D_GPU_VENDORS=none run eval 'VENDORS_REQUEST=none; detect_intel() { :; }; detect_npu() { :; }; resolve_vendors; echo "n=${#ACTIVE[@]}"; verify_gpus')
has "vendors none: nothing active" "${out}" "n=0"
has "vendors none: verify passes" "${out}" "No accelerators requested; cluster ready."

# ---- redaction: the key never left the endpoint, the Secret's stdin and env --------
for f in "${S}"/kubectl.log "${S}"/k3d.log "${S}"/self.log "${S}"/endpoint.log "${S}"/applied "${S}"/ui.yaml "${ALL_OUT}"; do
    [ -f "${f}" ] || continue
    if grep -Fq -- "${KEY}" "${f}" || grep -Fq -- "${KEY_B64}" "${f}"; then fail "redaction: key in ${f##*/}"; else ok "redaction: no key in ${f##*/}"; fi
done
if grep -rlF -- "${KEY}" "${S}/qs" 2>/dev/null | grep -q .; then fail "redaction: key in quickstart state"; else ok "redaction: no key in quickstart state"; fi

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
