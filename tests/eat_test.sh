#!/usr/bin/env bash
# Checks for integrations/eat without a cluster.
#   1. Every script there is committed executable (the README runs them directly).
#   2. eat-check.sh against stub kubectl and curl:
#      - the gateway is reached on the host IP of the running ingress-nginx
#        controller pod, not on whichever labelled node is listed first
#      - a tool call must come back as finish_reason=tool_calls with get_weather
#      - the model's code reaches the sandbox intact, multi-line and with quotes,
#        both for a router that shlex.splits the command (agent-sandbox v0.5.0
#        python-runtime-sandbox) and for one that runs it under /bin/sh -c
#      - the check fails when the sandbox prints anything but 385
#   3. eat-up.sh refuses deploy_openshell=on, which the pinned EAT tag cannot deploy.
# Needs bash, jq, python3.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EAT="${ROOT}/integrations/eat"
PASS=0
FAILS=0
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

# 1. File modes in the index, which is what a fresh checkout gets.
while read -r mode _ _ path; do
    if [ "${mode}" = 100755 ]; then ok "executable in git: ${path}"; else fail "not executable in git (${mode}): ${path}"; fi
done < <(git -C "${ROOT}" ls-files -s -- 'integrations/eat/*.sh' 'integrations/eat/**/*.sh' 'integrations/eat/**/*.py')

# 2. eat-check.sh against stubs.
BIN="${WORK}/bin"
mkdir -p "${BIN}" "${WORK}/eat-work/eat/core/inventory/metadata"
echo 'cluster_url=api.example.com' > "${WORK}/eat-work/eat/core/inventory/agentic-config.cfg"
echo 'litellm_master_key: "sk-stub"' > "${WORK}/eat-work/eat/core/inventory/metadata/vault.yml"
: > "${WORK}/kubeconfig"

cat > "${BIN}/kubectl" <<'EOF'
#!/usr/bin/env bash
case "$*" in
    *"get nodes"*) echo 172.20.0.2 ;;      # first labelled node: not where ingress runs
    *"-n ingress-nginx get pods"*"app.kubernetes.io/component=controller"*"status.phase=Running"*) echo 172.20.0.3 ;;
    *"get pods -o name"*) printf 'pod/sandbox-router-1\npod/python-pool-abcde\n' ;;
    *"get secret sandbox-router-auth"*) printf 'tok' | base64 ;;
    *port-forward*) exec sleep 60 ;;
    *) echo "kubectl stub: unexpected: $*" >&2; exit 1 ;;
esac
EOF

cat > "${BIN}/curl" <<'EOF'
#!/usr/bin/env python3
"""curl stub: answers the LiteLLM gateway and the sandbox router."""
import json, os, shlex, subprocess, sys

args = sys.argv[1:]
body, url, resolve = None, None, None
i = 0
while i < len(args):
    a = args[i]
    if a in ("-d", "--data"):
        body = args[i + 1]; i += 2; continue
    if a == "--resolve":
        resolve = args[i + 1]; i += 2; continue
    if a in ("-H", "-o", "--max-time"):
        i += 2; continue
    if not a.startswith("-"):
        url = a
    i += 1

def msg(content=None, tool_calls=None, finish="stop"):
    m = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    print(json.dumps({"choices": [{"finish_reason": finish, "message": m}]}))

if url.endswith("/v1/chat/completions"):
    with open(os.environ["STUB_LOG"], "a") as f:
        f.write(f"resolve {resolve}\n")
    req = json.loads(body)
    with open(os.environ["STUB_LOG"], "a") as f:
        f.write(f"request max_tokens={req.get('max_tokens')} temperature={req.get('temperature')}\n")
    prompt = req["messages"][-1]["content"]
    if req.get("tools"):
        if os.environ.get("STUB_NO_TOOL"):
            msg("It is sunny.")
        else:
            msg(None, [{"id": "c1", "type": "function", "function": {
                "name": "get_weather", "arguments": json.dumps({"city": "Portland"})}}], "tool_calls")
    elif "Python" in prompt:
        msg("<think>\n\n</think>\n\n```python\n" + os.environ["STUB_CODE"] + "\n```")
    else:
        msg("<think>\n\n</think>\n\nKubernetes runs containers.")
elif url.endswith("/execute"):
    cmd = json.loads(body)["command"]
    argv = shlex.split(cmd) if os.environ["STUB_ROUTER"] == "shlex" else ["/bin/sh", "-c", cmd]
    p = subprocess.run(argv, capture_output=True, text=True)
    print(json.dumps({"stdout": p.stdout, "stderr": p.stderr, "exit_code": p.returncode}))
# anything else (the router readiness probe) succeeds with no output
EOF
chmod +x "${BIN}/kubectl" "${BIN}/curl"

# check_says <name> <want rc: 0|fail> <ERE on the output> [env assignments...]
check_says() {
    local name=$1 want=$2 re=$3 out rc
    shift 3
    : > "${WORK}/log"
    out=$(env PATH="${BIN}:${PATH}" EAT_WORK="${WORK}/eat-work" EAT_KUBECONFIG="${WORK}/kubeconfig" \
        STUB_LOG="${WORK}/log" STUB_ROUTER=shlex STUB_CODE='print(sum(i**2 for i in range(1, 11)))' \
        "$@" bash "${EAT}/eat-check.sh" 2>&1) && rc=0 || rc=$?
    out+=$'\n'$(cat "${WORK}/log")
    if { { [ "${want}" = 0 ] && [ "${rc}" -eq 0 ]; } || { [ "${want}" = fail ] && [ "${rc}" -ne 0 ]; }; } \
       && grep -Eq -- "${re}" <<< "${out}"; then
        ok "${name}"
    else
        fail "${name} (rc=${rc}, want ${want}, /${re}/)"
        sed 's/^/  | /' <<< "${out}"
    fi
}

check_says "eat-check passes on a working stack" 0 'EAT check passed'
check_says "eat-check reaches the gateway on the ingress controller's host IP" 0 \
    'resolve api.example.com:443:172.20.0.3'
check_says "eat-check reports the tool call" 0 'get_weather\(.*Portland'
check_says "eat-check fails when the model answers a tool request with text" fail 'tool' STUB_NO_TOOL=1
MULTI=$'total = 0\nfor i in range(1, 11):\n    total += i * i\nprint("it\'s", total) if False else print(total)'
check_says "multi-line code with quotes survives a shlex.split router (v0.5.0)" 0 '"stdout":"385\\n"' \
    STUB_CODE="${MULTI}"
check_says "multi-line code with quotes survives a /bin/sh -c router" 0 '"stdout":"385\\n"' \
    STUB_CODE="${MULTI}" STUB_ROUTER=sh
check_says "eat-check fails when the sandbox does not print 385" fail 'expected 385' \
    STUB_CODE='print(sum(range(1, 11)))'
check_says "eat-check defaults: 200 tokens, temperature 0" 0 'request max_tokens=200 temperature=0$'
check_says "eat-check for a shared remote endpoint: sampled and short" 0 'request max_tokens=64 temperature=0.6$' \
    EAT_TEMPERATURE=0.6 EAT_MAX_TOKENS=64
check_says "eat-check refuses a non-numeric temperature" fail 'EAT_TEMPERATURE must be a number' EAT_TEMPERATURE=hot

# 4. eat-remote.sh: a remote endpoint as a LiteLLM model. The key reaches the
#    cluster only as the Secret on kubectl's stdin, the gateway gets it as a
#    secretKeyRef (set env --from=secret), and the model is a model_list entry in
#    the gateway's config file whose api_key names the env var, never the key.
#    LiteLLM resolves os.environ/ only in its config file: a /model/new body is
#    stored in its database as is, and the literal "os.environ/..." string goes out
#    as the bearer token (live, LiteLLM v1.83.14: the endpoint answered 401). The
#    master key header goes to curl on stdin, not argv.
RKEY="sk-test-eat-remote-0123456789"
RKEY_B64=$(printf '%s' "${RKEY}" | base64 -w0)
RBIN="${WORK}/rbin"
mkdir -p "${RBIN}"
coproc ESTUB { exec python3 "${ROOT}/tests/openai_auth_stub.py" --key "${RKEY}" --models "zai-org/GLM-5.3"; }
read -r _ EPORT <&"${ESTUB[0]}"
# kubectl stub: the gateway ConfigMap lives in ${STUB_LOG}.cm (its proxy_config.yaml),
# a merge patch of it rewrites that file.
cat > "${RBIN}/kubectl" <<'EOF'
#!/usr/bin/env bash
echo "kubectl $*" >> "${STUB_LOG}"
case "$*" in
    *"apply --server-side"*) grep -q "OPENAI_API_KEY: ${STUB_KEY_B64}" && echo "secret-on-stdin" >> "${STUB_LOG}" ;;
    *"get configmap genai-gateway-config -o json"*) jq -n --rawfile c "${STUB_LOG}.cm" '{data: {"proxy_config.yaml": $c}}' ;;
    *"patch configmap genai-gateway-config"*)
        while [ $# -gt 0 ] && [ "$1" != -p ]; do shift; done
        jq -j '.data["proxy_config.yaml"]' <<< "$2" > "${STUB_LOG}.cm" ;;
    *port-forward*) exec sleep 30 ;;
esac
exit 0
EOF
# curl stub: /model/info lists one database deployment of the model on the same
# api_base (what an older eat-remote.sh registered) and one other model.
cat > "${RBIN}/curl" <<'EOF'
#!/usr/bin/env bash
echo "curl $*" >> "${STUB_LOG}"
grep -q 'Authorization: Bearer sk-stub' && echo "master-key-on-stdin" >> "${STUB_LOG}"
case "$*" in
    *"/models?"*) echo '{"data":[{"id":"zai-org/GLM-5.3"}]}' ;;
    *"/model/info"*) jq -nc --arg b "${STUB_BASE}" '{data: [
        {model_name: "zai-org/GLM-5.3", litellm_params: {api_base: $b}, model_info: {id: "stale-db-1", db_model: true}},
        {model_name: "zai-org/GLM-5.3", litellm_params: {api_base: $b}, model_info: {id: "cfg-1", db_model: false}},
        {model_name: "Qwen/Qwen3-1.7B", litellm_params: {api_base: "http://vllm"}, model_info: {id: "q-1", db_model: true}}]}' ;;
    *"/model/delete"*) echo '{}' ;;
esac
EOF
chmod +x "${RBIN}/kubectl" "${RBIN}/curl"
rlog="${WORK}/rlog"
printf '\nrouter_settings:\n  routing_strategy: usage-based-routing-v2\ngeneral_settings:\n  disable_spend_logs: false\n' > "${rlog}.cm"
cp "${rlog}.cm" "${rlog}.cm0"
remote() {
    : > "${rlog}"
    env PATH="${RBIN}:${PATH}" EAT_WORK="${WORK}/eat-work" EAT_KUBECONFIG="${WORK}/kubeconfig" STUB_LOG="${rlog}" \
        STUB_KEY_B64="${RKEY_B64}" STUB_BASE="http://127.0.0.1:${EPORT}/v1" OPENAI_BASE_URL="http://127.0.0.1:${EPORT}" \
        OPENAI_API_KEY="${RKEY}" "${EAT}/eat-remote.sh" "$@" 2>&1
}
out=$(remote) && rc=0 || rc=$?
log=$(cat "${rlog}")
cfg=$(cat "${rlog}.cm")
if [ "${rc}" -eq 0 ]; then ok "eat-remote registers the endpoint"; else fail "eat-remote rc=${rc}: ${out}"; fi
grep -q '^secret-on-stdin$' <<< "${log}" && ok "eat-remote: Secret on stdin, server-side" || fail "eat-remote: Secret not on stdin"
grep -q 'set env deployment/genai-gateway-deployment --from=secret/k3d-gpu-llm --keys=OPENAI_API_KEY --prefix=K3D_GPU_REMOTE_' <<< "${log}" \
    && ok "eat-remote: gateway env is a secretKeyRef" || fail "eat-remote: gateway env"
grep -q 'master-key-on-stdin' <<< "${log}" && ok "eat-remote: master key header on stdin" || fail "eat-remote: master key header"
if grep -q '"api_key":"os.environ/K3D_GPU_REMOTE_OPENAI_API_KEY"' <<< "${cfg}" && grep -q '"api_base":"http://127.0.0.1:'"${EPORT}"'/v1"' <<< "${cfg}" \
    && grep -q '"model":"openai/zai-org/GLM-5.3"' <<< "${cfg}" && grep -q '^model_list:$' <<< "${cfg}"; then
    ok "eat-remote: the config file's model_list names the env var and the /v1 base"
else
    fail "eat-remote: config file: ${cfg}"
fi
python3 -c 'import sys, yaml; c = yaml.safe_load(open(sys.argv[1])); m = c["model_list"][0]
assert m["model_name"] == "zai-org/GLM-5.3" and m["litellm_params"]["api_key"] == "os.environ/K3D_GPU_REMOTE_OPENAI_API_KEY", m
assert c["router_settings"]["routing_strategy"] == "usage-based-routing-v2"' "${rlog}.cm" 2>/dev/null \
    && ok "eat-remote: the config file is valid YAML with the old settings kept" || fail "eat-remote: config not YAML: ${cfg}"
grep -q '/model/new' <<< "${log}" && fail "eat-remote: still calls /model/new" || ok "eat-remote: no /model/new (LiteLLM would send the reference as the key)"
grep -q 'patch deployment genai-gateway-deployment .*k3d-gpu.io/eat-remote' <<< "${log}" \
    && ok "eat-remote: the gateway restarts when the config changes" || fail "eat-remote: no pod template annotation: ${log}"
grep -q '/model/delete .*"id":"stale-db-1"' <<< "${log}" && ok "eat-remote: an older database registration of the model is deleted" \
    || fail "eat-remote: stale database deployment kept: ${log}"
grep -q '"id":"cfg-1"\|"id":"q-1"' <<< "${log}" && fail "eat-remote: deleted a deployment it did not make" || ok "eat-remote: other deployments kept"
if grep -Fq -- "${RKEY}" <<< "${log}${out}${cfg}" || grep -Fq -- "${RKEY_B64}" <<< "${log}${out}${cfg}" || grep -Fq 'sk-stub' <<< "${log}"; then
    fail "eat-remote: a key reached argv, output or the config"
else
    ok "eat-remote: no key on any argv, in the output or in the config"
fi
out=$(remote) && rc=0 || rc=$?
if [ "${rc}" -eq 0 ] && [ "$(grep -c '^model_list:$' "${rlog}.cm")" -eq 1 ] && [ "$(grep -c 'K3D_GPU_REMOTE_OPENAI_API_KEY' "${rlog}.cm")" -eq 1 ]; then
    ok "eat-remote: a second run replaces its entry, not adds one"
else
    fail "eat-remote rerun: rc=${rc} ${out} $(cat "${rlog}.cm")"
fi
printf 'model_list:\n  - model_name: x\n' > "${rlog}.cm"
out=$(remote) && rc=0 || rc=$?
if [ "${rc}" -ne 0 ] && grep -q 'has its own model_list' <<< "${out}" && ! grep -q 'patch configmap' "${rlog}"; then
    ok "eat-remote: a config with its own model_list is left alone"
else
    fail "eat-remote: own model_list: rc=${rc} ${out}"
fi
kill "${ESTUB_PID}" 2>/dev/null || true

# 3. eat-up.sh refuses deploy_openshell=on before it touches the cluster: the pinned
#    release-1.0.3-hotfix has no OpenShell role. No kubectl stub: it must stop first.
#    The spellings follow EAT main's read_config_file: trimmed key and value, quotes
#    dropped, on mapped to yes, last assignment wins.
REFUSED='deploy_openshell=on needs an EAT release newer than release-1.0.3-hotfix'
PASSED='kubeconfig /nonexistent not found'
N=0
# openshell_case <name> <expect: refuse|pass> <lines appended to the shipped config...>
openshell_case() {
    local name=$1 want=$2 out rc msg cfg
    shift 2
    N=$((N + 1))
    cfg="${WORK}/openshell-${N}.cfg"
    cp "${EAT}/agentic-config.cfg" "${cfg}"
    [ "$#" -eq 0 ] || printf '%s\n' "$@" >> "${cfg}"
    out=$(EAT_CONFIG="${cfg}" EAT_KUBECONFIG=/nonexistent "${EAT}/eat-up.sh" 2>&1) && rc=0 || rc=$?
    if [ "${want}" = refuse ]; then msg=${REFUSED}; else msg=${PASSED}; fi
    if [ "${rc}" -ne 0 ] && grep -qF "${msg}" <<< "${out}"; then
        ok "eat-up ${want}: ${name}"
    else
        fail "eat-up ${want}: ${name} (rc=${rc}): ${out}"
    fi
}
openshell_case "shipped config" pass
openshell_case "deploy_openshell=on" refuse 'deploy_openshell=on'
openshell_case "spaces around =" refuse 'deploy_openshell = on'
openshell_case "leading indent" refuse '  deploy_openshell=on'
openshell_case "double-quoted value" refuse 'deploy_openshell="on"'
openshell_case "single-quoted value" refuse "deploy_openshell='on'"
openshell_case "=yes" refuse 'deploy_openshell=yes'
openshell_case "trailing spaces" refuse 'deploy_openshell=on   '
openshell_case "on then off, last wins" pass 'deploy_openshell=on' 'deploy_openshell=off'
openshell_case "off then on, last wins" refuse 'deploy_openshell=off' 'deploy_openshell=on'
openshell_case "commented out" pass '# deploy_openshell=on'
openshell_case "ON is not on to EAT" pass 'deploy_openshell=ON'
openshell_case "other key mentioning it" pass 'note=deploy_openshell=on'

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
