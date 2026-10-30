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
#   3. eat-up.sh refuses deploy_openshell=on, unverified on k3d-gpu, and a config with no
#      deploy_guardrails value, on which release-1.1.0 deploys its guardrail. It also
#      refuses a deploy_nri_balloon_policy other than off (a second balloons policy),
#      compute_platform=xpu (an unpinned second GPU plugin), deploy_kata_containers=on
#      (no /dev/kvm in a k3d node) and rack_scale_mode=on (it builds its own cluster).
#   5. The GenAI Gateway installs from copies of its charts with exact dependency
#      versions and every image name:tag@sha256 (gateway-charts.py, brownfield.sh).
#   6. eat-opt.py puts the model optimizer's choices on EAT's own modelConfigs entry,
#      additive only, and prints each change against EAT stock.
#   7. The pins for EAT release-1.1.0: the tag and commit eat-up.sh clones, the
#      components agentic-config.cfg turns off, brownfield.sh handing the config's
#      deploy_guardrails to EAT's DEPLOY_GUARDRAILS, and the vLLM CPU image by digest.
# Needs bash, jq, python3 (with PyYAML).

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
        OPENAI_API_KEY="${RKEY}" "${EAT}/eat-remote.sh" 2>&1
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

# 3. eat-up.sh refuses deploy_openshell=on before it touches the cluster: release-1.1.0
#    ships OpenShell, but it has never run on a k3d-gpu cluster. No kubectl stub: it
#    must stop first. The spellings follow release-1.1.0's read_config_file: trimmed key
#    and value, quotes dropped, on mapped to yes, last assignment wins.
REFUSED='deploy_openshell=on is unverified on k3d-gpu'
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

# 3b. EAT_FIT_MODEL: the vLLM CPU KV cache comes from `k3d-gpu fit` (a stub printing
#     a fit/v1 document), before the cluster is touched; a model that cannot fit stops.
mkdir -p "${WORK}/fitbin"
cat > "${WORK}/fitbin/k3d-gpu" <<'EOF'
#!/bin/sh
[ "$*" = "fit --model ${STUB_MODEL} --engine vllm --device cpu --json" ] || { echo "unexpected: $*" >&2; exit 2; }
cat "${STUB_FIT}"
[ "${STUB_FITS}" = true ] || exit 3
EOF
chmod +x "${WORK}/fitbin/k3d-gpu"
printf '%s\n' '{"fits": true, "summary": "fit: org/m on CPU (AMX) via vllm", "flags": ["--max-model-len=32768", "--max-num-seqs=4", "VLLM_CPU_KVCACHE_SPACE=17"]}' > "${WORK}/fit-yes.json"
printf '%s\n' '{"fits": false, "summary": "fit: org/m does not fit this host: too big", "flags": []}' > "${WORK}/fit-no.json"
out=$(PATH="${WORK}/fitbin:${PATH}" STUB_MODEL=org/m STUB_FIT="${WORK}/fit-yes.json" STUB_FITS=true \
    EAT_FIT_MODEL=org/m EAT_KUBECONFIG=/nonexistent "${EAT}/eat-up.sh" 2>&1) && rc=0 || rc=$?
if [ "${rc}" -ne 0 ] && grep -qF 'KV cache 17 GiB from k3d-gpu fit' <<< "${out}" && grep -qF "${PASSED}" <<< "${out}"; then
    ok "eat-up: EAT_FIT_MODEL sizes the vLLM KV cache from k3d-gpu fit"
else
    fail "eat-up: EAT_FIT_MODEL sizes the vLLM KV cache (rc=${rc}): ${out}"
fi
out=$(PATH="${WORK}/fitbin:${PATH}" STUB_MODEL=org/m STUB_FIT="${WORK}/fit-no.json" STUB_FITS=false \
    EAT_FIT_MODEL=org/m EAT_KUBECONFIG=/nonexistent "${EAT}/eat-up.sh" 2>&1) && rc=0 || rc=$?
if [ "${rc}" -ne 0 ] && grep -qF 'does not fit this host' <<< "${out}" && ! grep -qF "${PASSED}" <<< "${out}"; then
    ok "eat-up: a model that cannot fit stops before the cluster is touched"
else
    fail "eat-up: a model that cannot fit stops (rc=${rc}): ${out}"
fi
out=$(PATH="${WORK}/fitbin:${PATH}" EAT_VLLM_KV_GIB=9 EAT_FIT_MODEL=org/m EAT_KUBECONFIG=/nonexistent "${EAT}/eat-up.sh" 2>&1) && rc=0 || rc=$?
if [ "${rc}" -ne 0 ] && ! grep -qF 'from k3d-gpu fit' <<< "${out}" && grep -qF "${PASSED}" <<< "${out}"; then
    ok "eat-up: EAT_VLLM_KV_GIB overrides the fit"
else
    fail "eat-up: EAT_VLLM_KV_GIB overrides the fit (rc=${rc}): ${out}"
fi
openshell_case "commented out" pass '# deploy_openshell=on'
openshell_case "ON is not on to EAT" pass 'deploy_openshell=ON'
openshell_case "other key mentioning it" pass 'note=deploy_openshell=on'

# 3c. eat-up.sh stops before the cluster when the config gives deploy_guardrails no value.
#     release-1.1.0 then deploys its Qwen3Guard guardrail (deploy-agentic-stack.sh:195
#     defaults DEPLOY_GUARDRAILS to on, and brownfield.sh keeps that default), and a config
#     written for release-1.0.3-hotfix has no such key. Each case starts from the shipped
#     config without its deploy_guardrails line; the key is read as read_config_file reads it.
GUARD_REFUSED='does not set deploy_guardrails'
# guard_case <name> <expect: refuse|pass> <lines appended to the shipped config minus deploy_guardrails...>
guard_case() {
    local name=$1 want=$2 out rc msg cfg
    shift 2
    N=$((N + 1))
    cfg="${WORK}/guard-${N}.cfg"
    grep -v '^deploy_guardrails=' "${EAT}/agentic-config.cfg" > "${cfg}"
    [ "$#" -eq 0 ] || printf '%s\n' "$@" >> "${cfg}"
    out=$(EAT_CONFIG="${cfg}" EAT_KUBECONFIG=/nonexistent "${EAT}/eat-up.sh" 2>&1) && rc=0 || rc=$?
    if [ "${want}" = refuse ]; then msg=${GUARD_REFUSED}; else msg=${PASSED}; fi
    if [ "${rc}" -ne 0 ] && grep -qF "${msg}" <<< "${out}"; then
        ok "eat-up guardrail ${want}: ${name}"
    else
        fail "eat-up guardrail ${want}: ${name} (rc=${rc}): ${out}"
    fi
}
guard_case "no deploy_guardrails key (a release-1.0.3-hotfix config)" refuse
guard_case "empty value" refuse 'deploy_guardrails='
guard_case "commented out" refuse '# deploy_guardrails=off'
guard_case "other key mentioning it" refuse 'note=deploy_guardrails=off'
guard_case "deploy_guardrails=off" pass 'deploy_guardrails=off'
guard_case "deploy_guardrails=on is the user's choice" pass 'deploy_guardrails=on'
guard_case "spaces and quotes" pass ' deploy_guardrails = "off" '
guard_case "set, then emptied: last wins" refuse 'deploy_guardrails=off' 'deploy_guardrails='

# 3d. eat-up.sh stops before the cluster on release-1.1.0 settings that put a second copy
#     of what a k3d-gpu cluster runs next to it, or that need what a k3d node lacks. Each
#     case starts from the shipped config without the key's line; the key is read as
#     read_config_file reads it (on -> yes, off -> no, last assignment wins).
#     - deploy_nri_balloon_policy: fresh-install.sh:247 deploys EAT's NRI balloons policy
#       on yes, and brownfield.sh model mode's prompt_for_input turns an absent key on for
#       compute_platform c (parse-user-prompts.sh:55-66): only off or no pass.
#     - compute_platform x or xpu: fresh-install.sh:219 applies intel-device-plugins'
#       gpu_plugin from its main branch (deploy-intel-gpu-plugin.yml:12-14).
#     - deploy_kata_containers on or yes: Kata needs /dev/kvm (deploy-kata-containers.yml).
#     - rack_scale_mode on or yes: builds the cluster from rack-profile.yaml (netplan, BGP,
#       the NVIDIA GPU operator; fresh-install.sh:195-210).
# key_case <name> <expect: refuse|pass> <refusal text> <key> <lines appended...>
key_case() {
    local name=$1 want=$2 refused=$3 key=$4 out rc msg cfg
    shift 4
    N=$((N + 1))
    cfg="${WORK}/key-${N}.cfg"
    grep -v "^${key}=" "${EAT}/agentic-config.cfg" > "${cfg}"
    [ "$#" -eq 0 ] || printf '%s\n' "$@" >> "${cfg}"
    out=$(EAT_CONFIG="${cfg}" EAT_KUBECONFIG=/nonexistent "${EAT}/eat-up.sh" 2>&1) && rc=0 || rc=$?
    if [ "${want}" = refuse ]; then msg=${refused}; else msg=${PASSED}; fi
    if [ "${rc}" -ne 0 ] && grep -qF "${msg}" <<< "${out}" && { [ "${want}" = pass ] || ! grep -qF "${PASSED}" <<< "${out}"; }; then
        ok "eat-up ${key} ${want}: ${name}"
    else
        fail "eat-up ${key} ${want}: ${name} (rc=${rc}): ${out}"
    fi
}
NRI_REFUSED="deploy_nri_balloon_policy must be off"
key_case "absent (model mode turns it on)" refuse "${NRI_REFUSED}" deploy_nri_balloon_policy
key_case "on" refuse "${NRI_REFUSED}" deploy_nri_balloon_policy 'deploy_nri_balloon_policy=on'
key_case "yes" refuse "${NRI_REFUSED}" deploy_nri_balloon_policy 'deploy_nri_balloon_policy=yes'
key_case "empty" refuse "${NRI_REFUSED}" deploy_nri_balloon_policy 'deploy_nri_balloon_policy='
key_case "off then on, last wins" refuse "${NRI_REFUSED}" deploy_nri_balloon_policy \
    'deploy_nri_balloon_policy=off' 'deploy_nri_balloon_policy=on'
key_case "off" pass "" deploy_nri_balloon_policy 'deploy_nri_balloon_policy=off'
key_case "no" pass "" deploy_nri_balloon_policy 'deploy_nri_balloon_policy=no'
key_case "spaces and quotes" pass "" deploy_nri_balloon_policy ' deploy_nri_balloon_policy = "off" '
XPU_REFUSED="compute_platform=xpu"
key_case "xpu" refuse "${XPU_REFUSED}" compute_platform 'compute_platform=xpu'
key_case "x" refuse "${XPU_REFUSED}" compute_platform 'compute_platform=x'
key_case "quoted xpu" refuse "${XPU_REFUSED}" compute_platform 'compute_platform = "xpu"'
key_case "cpu" pass "" compute_platform 'compute_platform=cpu'
key_case "absent (EAT's default cpu)" pass "" compute_platform
KATA_REFUSED="deploy_kata_containers=on"
key_case "on" refuse "${KATA_REFUSED}" deploy_kata_containers 'deploy_kata_containers=on'
key_case "yes" refuse "${KATA_REFUSED}" deploy_kata_containers 'deploy_kata_containers=yes'
key_case "off then on, last wins" refuse "${KATA_REFUSED}" deploy_kata_containers \
    'deploy_kata_containers=off' 'deploy_kata_containers=on'
key_case "off" pass "" deploy_kata_containers 'deploy_kata_containers=off'
RACK_REFUSED="rack_scale_mode=on"
key_case "on" refuse "${RACK_REFUSED}" rack_scale_mode 'rack_scale_mode=on'
key_case "yes" refuse "${RACK_REFUSED}" rack_scale_mode 'rack_scale_mode=yes'
key_case "off" pass "" rack_scale_mode 'rack_scale_mode=off'
cfg="${WORK}/xpu-reason.cfg"
cp "${EAT}/agentic-config.cfg" "${cfg}"
echo 'compute_platform=xpu' >> "${cfg}"
out=$(EAT_CONFIG="${cfg}" EAT_KUBECONFIG=/nonexistent "${EAT}/eat-up.sh" 2>&1) || true
if grep -qF 'K3D_GPU_AGENT_BACKEND=vllm' <<< "${out}"; then ok "eat-up: the xpu refusal points at vLLM on an Intel GPU through k3d-gpu agent up"
else fail "eat-up: the xpu refusal names no alternative: ${out}"; fi

# 5. The GenAI Gateway path installs pinned charts and images. EAT's
#    deploy-genai-gateway.yml syncs helm_charts_base_genai{,_trace} to the node, runs
#    `helm dependency update` on genai-gateway (Chart.yaml ranges postgresql >=13.3.0,
#    redis >=18.0.0, whose newest charts default to bitnamilegacy/*:latest) and installs
#    Langfuse with charts/langfuse/values.yaml. gateway-charts.py writes copies of both
#    chart dirs with exact dependency versions and every image name:tag@sha256, from
#    gateway-values.yaml and gateway-trace-values.yaml; EAT's checkout is not touched.
#    brownfield.sh hands the copies to that one playbook as extra vars.
FIX="${WORK}/eat-fixture"
GW="${FIX}/core/helm-charts/genai-gateway"
LF="${FIX}/core/helm-charts/genai-gateway-trace/charts/langfuse"
mkdir -p "${GW}/templates" "${GW}/charts" "${LF}" "${FIX}/core/inventory"
# The shapes of EAT release-1.1.0's files (0c726f5), cut to the keys that matter here.
cat > "${GW}/Chart.yaml" <<'EOF'
apiVersion: v2
name: genaigateway
version: 0.1.0
dependencies:
  - name: postgresql
    version: ">=13.3.0"
    repository: oci://registry-1.docker.io/bitnamicharts
    condition: postgresql.enabled
  - name: redis
    version: ">=18.0.0"
    repository: oci://registry-1.docker.io/bitnamicharts
    condition: redis.enabled
EOF
cat > "${GW}/values.yaml" <<'EOF'
image:
  repository: docker.litellm.ai/berriai/litellm
  tag: v1.83.14-stable.patch.2
  pullPolicy: Always
env:
  REDIS_PORT: "6379"
customRouter:
  enabled: true
config:
  proxy_config_yaml: |
    router_settings:
      routing_strategy: usage-based-routing-v2
    litellm_settings:
      callbacks: ["langfuse_otel", "custom_hybrid_router.proxy_handler_instance"]
postgresql:
  enabled: true
  image:
    repository: bitnamilegacy/postgresql
  auth:
    database: litellm
redis:
  enabled: true
  image:
    repository: "bitnamilegacy/redis"
  auth:
    enabled: true
EOF
cat > "${GW}/templates/deployment.yaml" <<'EOF'
    spec:
      initContainers:
        - name: wait-for-postgres-redis
          image: busybox:1.36
      containers:
        - name: litellm-container
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
EOF
: > "${GW}/charts/postgresql-16.7.4.tgz"
cat > "${LF}/values.yaml" <<'EOF'
global:
  security:
    allowInsecureImages: true
langfuse:
  image:
    tag: Null
  web:
    image:
      repository: langfuse/langfuse
      tag: Null
  worker:
    image:
      repository: langfuse/langfuse-worker
      tag: Null
postgresql:
  image:
    repository: bitnamilegacy/postgresql
redis:
  image:
    repository: bitnamilegacy/valkey
clickhouse:
  image:
    repository: bitnamilegacy/clickhouse
  zookeeper:
    image:
      repository: bitnamilegacy/zookeeper
s3:
  image:
    repository: bitnamilegacy/minio
EOF
fixture_sum() { (cd "${FIX}" && find . -type f -print0 | sort -z | xargs -0 sha256sum); }
FIX_SUM=$(fixture_sum)

OUT="${WORK}/gateway-charts"
if out=$("${EAT}/gateway-charts.py" "${FIX}/core/helm-charts" "${EAT}/gateway-values.yaml" \
        "${EAT}/gateway-trace-values.yaml" "${OUT}" 2>&1); then
    ok "gateway-charts.py writes the pinned chart copies"
else
    fail "gateway-charts.py: ${out}"
fi
# The tags are the ones EAT's path resolved on 2026-10-18 (postgresql and redis: the
# digests bitnamilegacy's :latest served), so the pins change no behaviour.
if out=$(python3 - "${OUT}" 2>&1 <<'EOF'
import re, sys, yaml
out = sys.argv[1]
pinned = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
chart = yaml.safe_load(open(out + "/genai-gateway/Chart.yaml"))
deps = {d["name"]: d for d in chart["dependencies"]}
assert deps["postgresql"]["version"] == "18.12.4" and deps["redis"]["version"] == "28.3.1", deps
assert deps["redis"]["repository"] == "oci://registry-1.docker.io/bitnamicharts" and deps["redis"]["condition"] == "redis.enabled", deps
gw = yaml.safe_load(open(out + "/genai-gateway/values.yaml"))
lf = yaml.safe_load(open(out + "/genai-gateway-trace/charts/langfuse/values.yaml"))
want = {
    "litellm": (gw["image"], "docker.litellm.ai/berriai/litellm", "v1.83.14-stable.patch.2"),
    "gateway postgresql": (gw["postgresql"]["image"], "bitnamilegacy/postgresql", "17.5.0-debian-12-r20"),
    "gateway redis": (gw["redis"]["image"], "bitnamilegacy/redis", "8.0.3-debian-12-r2"),
    "langfuse web": (lf["langfuse"]["web"]["image"], "langfuse/langfuse", "3.173.0"),
    "langfuse worker": (lf["langfuse"]["worker"]["image"], "langfuse/langfuse-worker", "3.173.0"),
    "langfuse postgresql": (lf["postgresql"]["image"], "bitnamilegacy/postgresql", "17.3.0-debian-12-r1"),
    "langfuse valkey": (lf["redis"]["image"], "bitnamilegacy/valkey", "8.0.2-debian-12-r2"),
    "langfuse clickhouse": (lf["clickhouse"]["image"], "bitnamilegacy/clickhouse", "25.2.1-debian-12-r0"),
    "langfuse zookeeper": (lf["clickhouse"]["zookeeper"]["image"], "bitnamilegacy/zookeeper", "3.9.3-debian-12-r8"),
    "langfuse minio": (lf["s3"]["image"], "bitnamilegacy/minio", "2024.12.18-debian-12-r1"),
}
for what, (img, repo, tag) in want.items():
    assert img["repository"] == repo, (what, img)
    assert pinned.match(str(img["tag"])) and img["tag"].split("@")[0] == tag, (what, img)
# EAT's own values survive the merge.
assert gw["image"]["pullPolicy"] == "Always" and gw["env"]["REDIS_PORT"] == "6379", gw
assert gw["postgresql"]["auth"]["database"] == "litellm" and gw["redis"]["auth"]["enabled"] is True, gw
assert gw["config"]["proxy_config_yaml"].startswith("router_settings:\n  routing_strategy: usage-based-routing-v2"), gw["config"]
# release-1.1.0's hybrid router: its callback and its ConfigMap switch reach the copy as EAT wrote them.
assert gw["customRouter"]["enabled"] is True, gw.get("customRouter")
assert 'callbacks: ["langfuse_otel", "custom_hybrid_router.proxy_handler_instance"]' in gw["config"]["proxy_config_yaml"], gw["config"]
assert lf["global"]["security"]["allowInsecureImages"] is True and lf["langfuse"]["image"]["tag"] is None, lf
assert "k3d-gpu" not in gw and "k3d-gpu" not in lf
EOF
); then ok "gateway charts: exact dependency versions, every image name:tag@sha256 at today's tag, EAT's values kept"
else fail "gateway charts content"; sed 's/^/  | /' <<< "${out}"; fi
tdiff=$(diff "${GW}/templates/deployment.yaml" "${OUT}/genai-gateway/templates/deployment.yaml" || true)
if grep -Eq '^> +image: busybox:1\.36@sha256:[0-9a-f]{64}$' <<< "${tdiff}" \
   && grep -Eq '^< +image: busybox:1\.36$' <<< "${tdiff}" && [ "$(grep -c '^[<>]' <<< "${tdiff}")" -eq 2 ]; then
    ok "gateway charts: the wait-for-postgres-redis init image is pinned, nothing else in the template moves"
else
    fail "gateway charts template"; sed 's/^/  | /' <<< "${tdiff}"
fi
if [ "$(fixture_sum)" = "${FIX_SUM}" ]; then ok "gateway charts: EAT's checkout is left as it was"
else fail "gateway-charts.py changed EAT's checkout"; fi
cp -r "${OUT}" "${WORK}/gateway-charts.first"
if out=$("${EAT}/gateway-charts.py" "${FIX}/core/helm-charts" "${EAT}/gateway-values.yaml" \
        "${EAT}/gateway-trace-values.yaml" "${OUT}" 2>&1) && diff -r "${WORK}/gateway-charts.first" "${OUT}" >/dev/null; then
    ok "gateway charts: a second run over the same out dir writes the same files"
else fail "gateway-charts.py rerun: ${out}"; fi
# Refusals: an EAT that no longer matches the overlay must stop the deploy, not
# install half-pinned charts.
gw_refuses() {
    local name=$1 re=$2 edit=$3 out rc
    rm -rf "${WORK}/fx2"; cp -r "${FIX}" "${WORK}/fx2"
    (cd "${WORK}/fx2/core/helm-charts" && eval "${edit}")
    out=$("${EAT}/gateway-charts.py" "${WORK}/fx2/core/helm-charts" "${EAT}/gateway-values.yaml" \
        "${EAT}/gateway-trace-values.yaml" "${WORK}/fx2-out" 2>&1) && rc=0 || rc=$?
    if [ "${rc}" -ne 0 ] && grep -Eq -- "${re}" <<< "${out}"; then ok "gateway-charts.py refuses ${name}"
    else fail "gateway-charts.py ${name} (rc=${rc}, /${re}/): ${out}"; fi
}
gw_refuses "a template without the busybox:1.36 init image" 'busybox:1\.36' \
    "sed -i 's/busybox:1.36/busybox:1.37/' genai-gateway/templates/deployment.yaml"
gw_refuses "a Chart.yaml without the redis dependency" 'has no redis dependency' \
    "python3 -c 'import yaml; p=\"genai-gateway/Chart.yaml\"; c=yaml.safe_load(open(p)); c[\"dependencies\"]=c[\"dependencies\"][:1]; yaml.safe_dump(c, open(p, \"w\"))'"
gw_refuses "a Langfuse values file without a key the overlay pins" 'zookeeper' \
    "python3 -c 'import yaml; p=\"genai-gateway-trace/charts/langfuse/values.yaml\"; c=yaml.safe_load(open(p)); del c[\"clickhouse\"][\"zookeeper\"]; yaml.safe_dump(c, open(p, \"w\"))'"
gw_refuses "an image repository the overlay does not expect" 'bitnami/redis' \
    "sed -i 's#bitnamilegacy/redis#bitnami/redis#' genai-gateway/values.yaml"

# brownfield.sh stack mode against a stub EAT: the gateway playbook, and only it,
# gets the chart copies as extra vars; the menu answers are unchanged.
BF="${WORK}/bf"
mkdir -p "${BF}/bin" "${BF}/home"
cp -r "${FIX}" "${BF}/eat"
echo 'cluster_url=api.example.com' > "${BF}/eat/core/inventory/agentic-config.cfg"
cat > "${BF}/eat/deploy-agentic-stack.sh" <<'EOF'
# EAT 0c726f5 deploy-agentic-stack.sh:195 runs this at source time; only its main()
# reads deploy_guardrails from the config, and fresh-install.sh:419 gates on this one.
DEPLOY_GUARDRAILS="${DEPLOY_GUARDRAILS:-on}"
_source_core_libs() { CORE_DIR="${EAT_DIR}/core"; }
brownfield_deployment() {
    local a b c
    read -r a; read -r b; read -r c
    echo "answers ${a} ${b} ${c}" >> "${STUB_LOG}"
    echo "guardrails ${DEPLOY_GUARDRAILS}" >> "${STUB_LOG}"
    cd "${CORE_DIR}"
    ansible-playbook -i hosts playbooks/deploy-ingress-controller.yml --extra-vars "secret_name=api.example.com"
    ansible-playbook -i hosts playbooks/deploy-genai-gateway.yml --extra-vars "secret_name=api.example.com" --vault-password-file v
}
EOF
cat > "${BF}/bin/sudo" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "${BF}/bin/openssl" <<'EOF'
#!/usr/bin/env bash
while [ $# -gt 0 ]; do case "$1" in -keyout|-out) : > "$2"; shift ;; esac; shift; done
EOF
cat > "${BF}/bin/ansible-playbook" <<'EOF'
#!/usr/bin/env bash
echo "ansible-playbook $*" >> "${STUB_LOG}"
for a in "$@"; do
    case "${a}" in @*) cat "${a#@}" >> "${STUB_LOG}" ;; esac
done
EOF
chmod +x "${BF}/bin/"*
: > "${BF}/log"
out=$(env PATH="${BF}/bin:${PATH}" HOME="${BF}/home" STUB_LOG="${BF}/log" EAT_DIR="${BF}/eat" \
    EAT_KUBECONFIG="${WORK}/kubeconfig" K3D_GPU_EAT_DIR="${EAT}" bash "${EAT}/brownfield.sh" 2>&1) && rc=0 || rc=$?
log=$(cat "${BF}/log")
if [ "${rc}" -eq 0 ] && grep -qx 'answers 5 1 yes' <<< "${log}"; then ok "brownfield.sh stack: runs with the same menu answers"
else fail "brownfield.sh stack rc=${rc}: ${out} ${log}"; fi
gw_call=$(grep '^ansible-playbook .*deploy-genai-gateway.yml' <<< "${log}" || true)
if grep -Eq -- "--extra-vars @${BF}/home/[^ ]+\.yml" <<< "${gw_call}" \
   && grep -qx "helm_charts_base_genai: ${BF}/home/k3d-gpu-charts/genai-gateway" <<< "${log}" \
   && grep -qx "helm_charts_base_genai_trace: ${BF}/home/k3d-gpu-charts/genai-gateway-trace" <<< "${log}" \
   && grep -q 'busybox:1.36@sha256:' "${BF}/home/k3d-gpu-charts/genai-gateway/templates/deployment.yaml"; then
    ok "brownfield.sh stack: the gateway playbook installs the pinned chart copies"
else
    fail "brownfield.sh stack: gateway call: ${gw_call}"; sed 's/^/  | /' <<< "${log}"
fi
ingress_call=$(grep '^ansible-playbook .*deploy-ingress-controller.yml' <<< "${log}" || true)
if [ "${ingress_call}" = 'ansible-playbook -i hosts playbooks/deploy-ingress-controller.yml --extra-vars secret_name=api.example.com' ]; then
    ok "brownfield.sh stack: other playbooks run as EAT calls them"
else fail "brownfield.sh stack: ingress call: ${ingress_call}"; fi

# 7b. The guardrail follows the config's deploy_guardrails, read as release-1.1.0's
#     read_config_file reads every other toggle (trimmed, quotes dropped, last
#     assignment wins); with no value EAT's own default (on) stands.
# bf_guardrails <name> <want: on|off> <config lines after cluster_url...>
bf_guardrails() {
    local name=$1 want=$2 out rc got
    shift 2
    printf '%s\n' 'cluster_url=api.example.com' "$@" > "${BF}/eat/core/inventory/agentic-config.cfg"
    : > "${BF}/log"
    out=$(env PATH="${BF}/bin:${PATH}" HOME="${BF}/home" STUB_LOG="${BF}/log" EAT_DIR="${BF}/eat" \
        EAT_KUBECONFIG="${WORK}/kubeconfig" K3D_GPU_EAT_DIR="${EAT}" bash "${EAT}/brownfield.sh" 2>&1) && rc=0 || rc=$?
    got=$(sed -n 's/^guardrails //p' "${BF}/log")
    if [ "${rc}" -eq 0 ] && [ "${got}" = "${want}" ]; then ok "brownfield.sh guardrail ${want}: ${name}"
    else fail "brownfield.sh guardrail ${name}: rc=${rc}, DEPLOY_GUARDRAILS='${got}', want ${want}: ${out}"; fi
}
bf_guardrails "no deploy_guardrails key (EAT's default)" on
bf_guardrails "deploy_guardrails=off" off 'deploy_guardrails=off'
bf_guardrails "deploy_guardrails=on" on 'deploy_guardrails=on'
bf_guardrails "spaces and quotes" off ' deploy_guardrails = "off" '
bf_guardrails "on then off, last wins" off 'deploy_guardrails=on' 'deploy_guardrails=off'
bf_guardrails "off then on, last wins" on 'deploy_guardrails=off' 'deploy_guardrails=on'
bf_guardrails "commented out" on '# deploy_guardrails=off'
mapfile -t shipped < "${EAT}/agentic-config.cfg"
bf_guardrails "the shipped agentic-config.cfg" off "${shipped[@]}"
echo 'cluster_url=api.example.com' > "${BF}/eat/core/inventory/agentic-config.cfg"
# A gateway playbook the wrapper never sees (enabled in the config) fails the deploy.
echo 'deploy_genai_gateway=on' >> "${BF}/eat/core/inventory/agentic-config.cfg"
sed -i 's#playbooks/deploy-genai-gateway.yml#playbooks/deploy-genai-gateway-renamed.yaml#' "${BF}/eat/deploy-agentic-stack.sh"
out=$(env PATH="${BF}/bin:${PATH}" HOME="${BF}/home" STUB_LOG="${BF}/log" EAT_DIR="${BF}/eat" \
    EAT_KUBECONFIG="${WORK}/kubeconfig" K3D_GPU_EAT_DIR="${EAT}" bash "${EAT}/brownfield.sh" 2>&1) && rc=0 || rc=$?
if [ "${rc}" -ne 0 ] && grep -q 'never ran through the pinning wrapper' <<< "${out}"; then
    ok "brownfield.sh stack: fails when the gateway playbook bypasses the pins"
else fail "brownfield.sh stack: bypass not caught (rc=${rc}): ${out}"; fi

# 6. eat-opt.py: the model optimizer as values on EAT's own entry, printed against
#    stock. EAT's playbook and values are reduced to the lines eat-opt.py reads
#    (deploy-inference-models.yml block for cpu-gemma4-26b-a4b, xeon-values.yaml
#    entry for google/gemma-4-26B-A4B-it, both at 0d3438d; release-1.1.0 keeps these
#    lines and the image tag is its v0.28.0); model metadata is the saved bundle in
#    tests/fixtures/hf, through eat-opt.py's cache.
OPT="${WORK}/opt"
mkdir -p "${OPT}/cache"
cp "${ROOT}/tests/fixtures/hf/google__gemma-4-26B-A4B-it.json" "${OPT}/cache/"
cat > "${OPT}/playbook.yml" <<'EOF'
- hosts: all
  tasks:
    - name: Check if CPU Gemma4 is installed
      tags: install-cpu-gemma4-26b-a4b
    - name: Deploy CPU Gemma4
      block:
        - name: Deploy CPU based LLM model Gemma4 Installation
          ansible.builtin.command: >-
            helm upgrade --install vllm-gemma4-26b-a4b-cpu "{{ remote_helm_charts_base }}/vllm"
            --values "{{ xeon_values_file }}"
            --set LLM_MODEL_ID="google/gemma-4-26B-A4B-it"
      tags: install-cpu-gemma4-26b-a4b
      when:
        - "'cpu-gemma4-26b-a4b' in (model_name_list | regex_replace(',', ' ') | split())"
    - name: Deploy CPU Qwen
      block:
        - name: Deploy CPU based LLM model Qwen-3-1-7B Installation
          ansible.builtin.command: >-
            helm upgrade --install vllm-qwen-3-1-7b-cpu "{{ remote_helm_charts_base }}/vllm"
            --set LLM_MODEL_ID="Qwen/Qwen3-1.7B"
      tags: install-cpu-qwen3-1-7b
EOF
cat > "${OPT}/values.yaml" <<'EOF'
image:
  repository: vllm/vllm-openai-cpu
  tag: "v0.28.0@sha256:197354e6475e638eb5ccca09beb078488046a82de0e204e7429bf1a804df9647"
modelConfigs:
  "google/gemma-4-26B-A4B-it":
    configMapValues:
      VLLM_CPU_KVCACHE_SPACE: "6"
    extraCmdArgs:
      [
        "--block-size", "128",
        "--dtype", "bfloat16",
        "--enable-prefix-caching",
        "--enable-auto-tool-choice",
        "--tool-call-parser", "gemma4",
        "--chat-template", "/workspace/vllm/examples/tool_chat_template_gemma4.jinja",
      ]
defaultModelConfigs:
  extraCmdArgs: ["--dtype", "bfloat16"]
EOF
cp "${OPT}/values.yaml" "${OPT}/stock.yaml"
out=$(python3 "${EAT}/eat-opt.py" "${OPT}/playbook.yml" "${OPT}/values.yaml" cpu-gemma4-26b-a4b "${ROOT}/console" --cache "${OPT}/cache" 2>&1) && rc=0 || rc=$?
args=$(python3 -c 'import sys, yaml; print(" ".join(yaml.safe_load(open(sys.argv[1]))["modelConfigs"]["google/gemma-4-26B-A4B-it"]["extraCmdArgs"]))' "${OPT}/values.yaml")
want='--block-size 128 --dtype bfloat16 --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser gemma4 --chat-template /workspace/vllm/examples/tool_chat_template_gemma4.jinja --reasoning-parser gemma4 --default-chat-template-kwargs {"enable_thinking": false} --language-model-only'
if [ "${rc}" -eq 0 ] && [ "${args}" = "${want}" ]; then
    ok "eat-opt.py: appends the optimizer's arguments after EAT's own, none removed"
else
    fail "eat-opt.py rc=${rc}: got '${args}'"; sed 's/^/  | /' <<< "${out}"
fi
if grep -qF '+ --reasoning-parser gemma4' <<< "${out}" && grep -qF "kept EAT's --tool-call-parser (optimizer: gemma4)" <<< "${out}" \
   && grep -qF 'EAT overlay vs stock for cpu-gemma4-26b-a4b (modelConfigs[google/gemma-4-26B-A4B-it]):' <<< "${out}"; then
    ok "eat-opt.py: prints each change against EAT stock"
else fail "eat-opt.py: change report: ${out}"; fi
if ! grep -q 'speculativeConfig' "${OPT}/values.yaml" && grep -q 'unsupported on CPU before v0.31.0' <<< "${out}"; then
    ok "eat-opt.py: no speculative config on EAT's vLLM v0.28.0 CPU image, and says why"
else fail "eat-opt.py: speculative config on v0.28.0 CPU: ${out}"; fi
cp "${OPT}/stock.yaml" "${OPT}/values.yaml"
out=$(EAT_MODEL_OPT=off python3 "${EAT}/eat-opt.py" "${OPT}/playbook.yml" "${OPT}/values.yaml" cpu-gemma4-26b-a4b "${ROOT}/console" --cache "${OPT}/cache" 2>&1)
if cmp -s "${OPT}/stock.yaml" "${OPT}/values.yaml" && grep -q 'EAT_MODEL_OPT=off' <<< "${out}"; then
    ok "eat-opt.py: EAT_MODEL_OPT=off leaves the values as merged"
else fail "eat-opt.py: EAT_MODEL_OPT=off: ${out}"; fi
out=$(python3 "${EAT}/eat-opt.py" "${OPT}/playbook.yml" "${OPT}/values.yaml" cpu-nope "${ROOT}/console" --cache "${OPT}/cache" 2>&1) && rc=0 || rc=$?
if [ "${rc}" -eq 0 ] && cmp -s "${OPT}/stock.yaml" "${OPT}/values.yaml" && grep -q 'no LLM_MODEL_ID for cpu-nope' <<< "${out}"; then
    ok "eat-opt.py: an unknown catalog id leaves the values as merged, with one line"
else fail "eat-opt.py: unknown catalog id (rc=${rc}): ${out}"; fi

# 7. EAT release-1.1.0 (https://github.com/intel/enterprise-agent-toolkit/releases/tag/release-1.1.0).
tag=$(sed -n 's/^EAT_TAG="\(.*\)"$/\1/p' "${EAT}/eat-up.sh")
commit=$(sed -n 's/^EAT_COMMIT="\(.*\)"$/\1/p' "${EAT}/eat-up.sh")
if [ "${tag}" = release-1.1.0 ] && [ "${commit}" = 0c726f576595971c53cc45b94e08ba969beb4a4a ]; then
    ok "eat-up.sh clones EAT release-1.1.0 at 0c726f5"
else fail "eat-up.sh pins EAT ${tag} ${commit}"; fi
# release-1.1.0 adds these toggles; its own agentic-config.cfg turns deploy_guardrails on.
# Each is written out off, so the stack does not hang on a default, and read the way its
# read_config_file reads them (core/lib/system/precheck/read-config-file.sh at 0c726f5).
if out=$(python3 - "${EAT}/agentic-config.cfg" 2>&1 <<'EOF'
import sys
seen = {}
for line in open(sys.argv[1]):
    key, _, value = line.rstrip("\n").partition("=")
    key, value = key.strip(), value.strip().strip("\"'")
    if not key or key.startswith("#"):
        continue
    seen[key] = {"on": "yes", "off": "no"}.get(value, value)
for k in ("deploy_guardrails", "deploy_vllm_sr", "deploy_openshell", "deploy_kata_containers",
          "deploy_ceph", "rack_scale_mode", "deploy_nri_balloon_policy"):
    assert seen.get(k) == "no", (k, seen.get(k))
EOF
); then ok "agentic-config.cfg turns guardrail, vLLM SR, OpenShell, Kata, Ceph, rack-scale and EAT's balloons off"
else fail "agentic-config.cfg toggles: ${out}"; fi
# release-1.1.0's xeon-values.yaml names vllm/vllm-openai-cpu:v0.28.0; the overlay pins that
# tag to its multi-arch index digest (Docker Hub, read 2026-10-29; crescent amendment E1).
if out=$(python3 - "${EAT}/vllm-values.yaml" 2>&1 <<'EOF'
import sys, yaml
img = yaml.safe_load(open(sys.argv[1]))["image"]
assert img["repository"] == "vllm/vllm-openai-cpu", img
assert img["tag"] == "v0.28.0@sha256:197354e6475e638eb5ccca09beb078488046a82de0e204e7429bf1a804df9647", img
EOF
); then ok "vllm-values.yaml pins vllm/vllm-openai-cpu v0.28.0 by digest"
else fail "vllm-values.yaml image: ${out}"; fi
cfg="${WORK}/openshell-reason.cfg"
cp "${EAT}/agentic-config.cfg" "${cfg}"
echo 'deploy_openshell=on' >> "${cfg}"
out=$(EAT_CONFIG="${cfg}" EAT_KUBECONFIG=/nonexistent "${EAT}/eat-up.sh" 2>&1) && rc=0 || rc=$?
if [ "${rc}" -ne 0 ] && grep -qF 'set deploy_openshell=off' <<< "${out}" && ! grep -qiE 'newer than|no OpenShell role' <<< "${out}"; then
    ok "eat-up: the OpenShell refusal names the real reason, not a missing role"
else fail "eat-up: OpenShell refusal (rc=${rc}): ${out}"; fi

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
