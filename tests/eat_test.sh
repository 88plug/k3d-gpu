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
#   3. eat-up.sh resolves EAT_RELEASE (release-1.0.3-hotfix by default, or
#      release-1.1.0) and checks the config against that release before it touches
#      the cluster: OpenShell and the guardrail only where the release has the role,
#      never EAT's NRI balloons policy, and on release-1.1.0 no XPU path, Kata or
#      rack-scale mode. Then the release's commit, offline, and what the bastion gets.
#   5. The GenAI Gateway installs from copies of its charts with exact dependency
#      versions and every image name:tag@sha256 (gateway-charts.py, brownfield.sh).
#      brownfield.sh turns EAT's guardrail on only from deploy_guardrails, and then
#      from a chart copy with the guard image pinned by digest; in model mode it
#      refuses EAT values whose vLLM tag the overlays do not match.
#   6. eat-opt.py puts the model optimizer's choices on EAT's own modelConfigs entry,
#      additive only, and prints each change against EAT stock, at the chart's vLLM
#      version (v0.24.0 and, on release-1.1.0's values, v0.28.0).
#   7. vllm-merge.py merges the k3d-gpu overlays over EAT's values and refuses a
#      merged vLLM tag other than the one EAT's own file names.
# Needs bash, git, jq, python3 (with PyYAML).

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
done < <(git -C "${ROOT}" ls-files -s -- 'integrations/eat/*.sh' 'integrations/eat/*.py' 'integrations/eat/**/*.sh' 'integrations/eat/**/*.py')
git -C "${ROOT}" ls-files --error-unmatch -- integrations/eat/vllm-merge.py >/dev/null 2>&1 \
    && ok "tracked in git: integrations/eat/vllm-merge.py" || fail "not tracked in git: integrations/eat/vllm-merge.py"

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

# 3. eat-up.sh checks the config against the EAT release before it touches the
#    cluster. No kubectl stub: a config that passes stops at the missing kubeconfig.
#    The spellings follow EAT's read_config_file (the same at 0d3438d and 0c726f5):
#    trimmed key and value, quotes dropped, on mapped to yes, last assignment wins.
#    With EAT_RELEASE unset, release-1.0.3-hotfix has no OpenShell role.
REFUSED='deploy_openshell=on needs EAT_RELEASE=release-1.1.0'
PASSED='kubeconfig /nonexistent not found'
N=0
BASE_CFG="${EAT}/agentic-config.cfg"
# cfg_case <release, empty: unset> <name> <expect: refuse|pass> <refusal text>
#          <lines appended to BASE_CFG...>
cfg_case() {
    local release=$1 name=$2 want=$3 msg=$4 out rc cfg envs=()
    shift 4
    N=$((N + 1))
    cfg="${WORK}/cfg-${N}.cfg"
    cp "${BASE_CFG}" "${cfg}"
    [ "$#" -eq 0 ] || printf '%s\n' "$@" >> "${cfg}"
    [ -z "${release}" ] || envs=(EAT_RELEASE="${release}")
    out=$(env -u EAT_RELEASE ${envs[@]+"${envs[@]}"} EAT_CONFIG="${cfg}" EAT_KUBECONFIG=/nonexistent \
        "${EAT}/eat-up.sh" 2>&1) && rc=0 || rc=$?
    [ "${want}" = refuse ] || msg=${PASSED}
    if [ "${rc}" -ne 0 ] && grep -qF -- "${msg}" <<< "${out}" \
       && { [ "${want}" = pass ] || ! grep -qF "${PASSED}" <<< "${out}"; }; then
        ok "eat-up ${release:-default} ${want}: ${name}"
    else
        fail "eat-up ${release:-default} ${want}: ${name} (rc=${rc}): ${out}"
    fi
}
# openshell_case <name> <expect: refuse|pass> <lines...>: the default release.
openshell_case() { local name=$1 want=$2; shift 2; cfg_case "" "${name}" "${want}" "${REFUSED}" "$@"; }
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

# 3a. EAT_RELEASE: two releases, each with its own checks. release-1.1.0 has the
#     OpenShell role, so the same spellings reach the kubeconfig check there.
cfg_case release-1.0.3-hotfix "deploy_openshell=on, release named" refuse "${REFUSED}" 'deploy_openshell=on'
cfg_case release-1.1.0 "shipped config" pass ""
cfg_case release-1.1.0 "deploy_openshell=on" pass "" 'deploy_openshell=on'
cfg_case release-1.1.0 "deploy_openshell = \"yes\"" pass "" 'deploy_openshell = "yes"'
for r in release-1.2.0 main release-1.0.3 ''; do
    out=$(EAT_RELEASE="${r}" EAT_CONFIG=/nonexistent EAT_KUBECONFIG=/nonexistent "${EAT}/eat-up.sh" 2>&1) && rc=0 || rc=$?
    if [ -z "${r}" ]; then
        # set but empty is unset: the default release, which then needs the config
        if [ "${rc}" -ne 0 ] && grep -qF 'config /nonexistent not found' <<< "${out}"; then ok "eat-up: EAT_RELEASE='' is the default release"
        else fail "eat-up: EAT_RELEASE='' (rc=${rc}): ${out}"; fi
        continue
    fi
    if [ "${rc}" -ne 0 ] && grep -qF "EAT_RELEASE=${r} is not supported" <<< "${out}" \
       && grep -qF 'release-1.0.3-hotfix' <<< "${out}" && grep -qF 'release-1.1.0' <<< "${out}" \
       && ! grep -qF 'not found' <<< "${out}"; then
        ok "eat-up: EAT_RELEASE=${r} refused, the supported releases listed, before the config and kubeconfig"
    else
        fail "eat-up: EAT_RELEASE=${r} (rc=${rc}): ${out}"
    fi
done

# deploy_nri_balloon_policy, both releases: anything but off or no installs EAT's
# NRI balloons policy, a second one beside k3d-gpu's. Both releases turn it on when
# the key is absent (parse-user-prompts.sh:55-60 at 0d3438d, :55-66 at 0c726f5, where
# compute_platform defaults to cpu).
BALLOONS="k3d-gpu's balloons policy must remain the only one"
grep -v '^deploy_nri_balloon_policy=' "${EAT}/agentic-config.cfg" > "${WORK}/no-balloons.cfg"
for rel in release-1.0.3-hotfix release-1.1.0; do
    BASE_CFG="${WORK}/no-balloons.cfg"
    cfg_case "${rel}" "deploy_nri_balloon_policy absent" refuse "${BALLOONS}"
    cfg_case "${rel}" "deploy_nri_balloon_policy= (empty)" refuse "${BALLOONS}" 'deploy_nri_balloon_policy='
    cfg_case "${rel}" "deploy_nri_balloon_policy only commented out" refuse "${BALLOONS}" '# deploy_nri_balloon_policy=off'
    cfg_case "${rel}" "deploy_nri_balloon_policy=on" refuse "${BALLOONS}" 'deploy_nri_balloon_policy=on'
    cfg_case "${rel}" "deploy_nri_balloon_policy = \"yes\"" refuse "${BALLOONS}" 'deploy_nri_balloon_policy = "yes"'
    cfg_case "${rel}" "deploy_nri_balloon_policy=yes, indented" refuse "${BALLOONS}" '  deploy_nri_balloon_policy=yes'
    cfg_case "${rel}" "deploy_nri_balloon_policy off then on, last wins" refuse "${BALLOONS}" \
        'deploy_nri_balloon_policy=off' 'deploy_nri_balloon_policy=on'
    cfg_case "${rel}" "deploy_nri_balloon_policy=off" pass "" 'deploy_nri_balloon_policy=off'
    cfg_case "${rel}" "deploy_nri_balloon_policy=no" pass "" 'deploy_nri_balloon_policy=no'
    cfg_case "${rel}" "deploy_nri_balloon_policy='off'" pass "" "deploy_nri_balloon_policy='off'"
    cfg_case "${rel}" "deploy_nri_balloon_policy on then no, last wins" pass "" \
        'deploy_nri_balloon_policy=on' 'deploy_nri_balloon_policy=no'
    BASE_CFG="${EAT}/agentic-config.cfg"
done

# release-1.1.0 only: its XPU path would add a second GPU plugin, Kata needs /dev/kvm,
# and rack-scale mode builds its own cluster.
XPU='K3D_GPU_AGENT_BACKEND=vllm'
cfg_case release-1.1.0 "compute_platform=xpu" refuse "${XPU}" 'compute_platform=xpu'
cfg_case release-1.1.0 "compute_platform = x" refuse "${XPU}" 'compute_platform = x'
cfg_case release-1.1.0 "compute_platform=\"xpu\"" refuse "${XPU}" 'compute_platform="xpu"'
cfg_case release-1.1.0 "compute_platform=cpu" pass "" 'compute_platform=cpu'
cfg_case release-1.1.0 "compute_platform xpu then c, last wins" pass "" 'compute_platform=xpu' 'compute_platform=c'
KATA='k3d nodes have no /dev/kvm'
cfg_case release-1.1.0 "deploy_kata_containers=on" refuse "${KATA}" 'deploy_kata_containers=on'
cfg_case release-1.1.0 "deploy_kata_containers = yes" refuse "${KATA}" 'deploy_kata_containers = yes'
cfg_case release-1.1.0 "deploy_kata_containers=off" pass "" 'deploy_kata_containers=off'
RACK='a k3d-gpu cluster is brownfield'
cfg_case release-1.1.0 "rack_scale_mode=on" refuse "${RACK}" 'rack_scale_mode=on'
cfg_case release-1.1.0 "rack_scale_mode=off" pass "" 'rack_scale_mode=off'

# deploy_guardrails: release-1.0.3-hotfix has no guardrail role; release-1.1.0
# deploys Qwen3Guard on request, with its image pinned by digest (section 5).
GUARD='deploy_guardrails=on needs EAT_RELEASE=release-1.1.0'
cfg_case "" "deploy_guardrails=on" refuse "${GUARD}" 'deploy_guardrails=on'
cfg_case "" "deploy_guardrails = 'yes'" refuse "${GUARD}" "deploy_guardrails = 'yes'"
cfg_case "" "deploy_guardrails off then on, last wins" refuse "${GUARD}" 'deploy_guardrails=off' 'deploy_guardrails=on'
cfg_case "" "deploy_guardrails on then off, last wins" pass "" 'deploy_guardrails=on' 'deploy_guardrails=off'
cfg_case release-1.1.0 "deploy_guardrails=on" pass "" 'deploy_guardrails=on'
cfg_case release-1.1.0 "deploy_guardrails = \"yes\"" pass "" 'deploy_guardrails = "yes"'

# 3c. The release's commit, offline. kubectl answers the two node-label queries.
#     docker logs its calls; with STUB_NO_DOCKER set it fails, and the log must stay
#     empty: eat-up.sh stops before `docker build`.
SB="${WORK}/sbin"
mkdir -p "${SB}"
cat > "${SB}/kubectl" <<'EOF'
#!/usr/bin/env bash
case "$*" in
    "get nodes -l ei-infra-eligible=true -o name"|"get nodes -l ei-inference-eligible=true -o name") echo node/k3d-eat-server-0 ;;
    *) echo "kubectl stub: unexpected: $*" >&2; exit 1 ;;
esac
EOF
cat > "${SB}/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "${STUB_LOG}"
[ -z "${STUB_NO_DOCKER:-}" ] || { echo "docker stub: must not be called: $*" >&2; exit 99; }
EOF
chmod +x "${SB}/kubectl" "${SB}/docker"
C103=0d3438d7119de44cafc5903f66665c0b623e3333
C110=0c726f576595971c53cc45b94e08ba969beb4a4a
# A real local repository whose HEAD is neither release.
CW="${WORK}/cw"
mkdir -p "${CW}/eat/core/inventory"
git -C "${CW}/eat" init -q
git -C "${CW}/eat" -c user.name=t -c user.email=t@example.com -c commit.gpgsign=false -c core.hooksPath=/dev/null \
    commit -q --allow-empty -m other
OTHER=$(git -C "${CW}/eat" rev-parse HEAD)
for rel in release-1.0.3-hotfix release-1.1.0; do
    if [ "${rel}" = release-1.1.0 ]; then want=${C110}; else want=${C103}; fi
    : > "${WORK}/dlog"
    out=$(env PATH="${SB}:${PATH}" STUB_LOG="${WORK}/dlog" STUB_NO_DOCKER=1 EAT_RELEASE="${rel}" EAT_WORK="${CW}" \
        EAT_KUBECONFIG="${WORK}/kubeconfig" "${EAT}/eat-up.sh" 2>&1) && rc=0 || rc=$?
    if [ "${rc}" -ne 0 ] && grep -qF "is not ${rel}" <<< "${out}" && grep -qF "expected commit ${want}" <<< "${out}" \
       && grep -qF "found HEAD ${OTHER}" <<< "${out}" && grep -qF 'fresh EAT_WORK' <<< "${out}" && [ ! -s "${WORK}/dlog" ]; then
        ok "eat-up ${rel}: a checkout at another commit is refused, naming the release and both commits, before docker"
    else
        fail "eat-up ${rel}: commit check (rc=${rc}): ${out} $(cat "${WORK}/dlog")"
    fi
done
# What the bastion gets, with a git stub reporting HEAD: the default release mounts no
# image overlay; release-1.1.0 mounts vllm-image-release-1.1.0.yaml and names it in
# EAT_VLLM_IMAGE_VALUES. An EAT_WORK cloned for one release is refused for the other.
GB="${WORK}/gbin"
BW="${WORK}/bw"
mkdir -p "${GB}" "${BW}/eat/.git" "${BW}/eat/core/inventory"
cat > "${GB}/git" <<'EOF'
#!/usr/bin/env bash
case "$*" in
    *" rev-parse HEAD") echo "${STUB_HEAD}" ;;
    *) echo "git stub: unexpected: $*" >&2; exit 1 ;;
esac
EOF
chmod +x "${GB}/git"
# bastion <release, empty: unset> <HEAD>: eat-up.sh's output; the docker calls in ${WORK}/dlog.
bastion() {
    local envs=()
    [ -z "$1" ] || envs=(EAT_RELEASE="$1")
    : > "${WORK}/dlog"
    env -u EAT_RELEASE -u EAT_VLLM_IMAGE_VALUES PATH="${GB}:${SB}:${PATH}" STUB_LOG="${WORK}/dlog" STUB_HEAD="$2" \
        EAT_WORK="${BW}" EAT_KUBECONFIG="${WORK}/kubeconfig" ${envs[@]+"${envs[@]}"} "${EAT}/eat-up.sh" 2>&1
}
out=$(bastion "" "${C103}") && rc=0 || rc=$?
run=$(grep '^docker run ' "${WORK}/dlog" || true)
if [ "${rc}" -eq 0 ] && grep -qF -- "-v ${EAT}/vllm-values.yaml:/vllm-values.yaml:ro" <<< "${run}" \
   && grep -qF -- "-v ${BW}/eat:/home/ubuntu/eat" <<< "${run}" && ! grep -qF 'vllm-image' <<< "${run}" \
   && ! grep -qF 'EAT_VLLM_IMAGE_VALUES' <<< "${run}" && cmp -s "${EAT}/agentic-config.cfg" "${BW}/eat/core/inventory/agentic-config.cfg"; then
    ok "eat-up default: release-1.0.3-hotfix at ${C103:0:7} reaches the bastion with vllm-values.yaml alone"
else
    fail "eat-up default bastion (rc=${rc}): ${out} | ${run}"
fi
out=$(bastion release-1.1.0 "${C110}") && rc=0 || rc=$?
run=$(grep '^docker run ' "${WORK}/dlog" || true)
if [ "${rc}" -eq 0 ] && grep -qF -- "-v ${EAT}/vllm-values.yaml:/vllm-values.yaml:ro" <<< "${run}" \
   && grep -qF -- "-v ${EAT}/vllm-image-release-1.1.0.yaml:/vllm-image-values.yaml:ro" <<< "${run}" \
   && grep -qF -- "-e EAT_VLLM_IMAGE_VALUES=/vllm-image-values.yaml" <<< "${run}"; then
    ok "eat-up release-1.1.0: ${C110:0:7} reaches the bastion with the v0.28.0 image overlay mounted"
else
    fail "eat-up release-1.1.0 bastion (rc=${rc}): ${out} | ${run}"
fi
# cross <release, empty: unset> <its commit> <HEAD of the other release>
cross() {
    local out rc
    out=$(bastion "$1" "$3") && rc=0 || rc=$?
    if [ "${rc}" -ne 0 ] && grep -qF "is not ${1:-release-1.0.3-hotfix}" <<< "${out}" && grep -qF "expected commit $2" <<< "${out}" \
       && grep -qF "found HEAD $3" <<< "${out}" && [ ! -s "${WORK}/dlog" ]; then
        ok "eat-up ${1:-default}: an EAT_WORK cloned for the other release (${3:0:7}) is refused"
    else
        fail "eat-up ${1:-default}: cross-release EAT_WORK (rc=${rc}): ${out}"
    fi
}
cross release-1.1.0 "${C110}" "${C103}"
cross "" "${C103}" "${C110}"

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
# The shapes of EAT 0d3438d's files, cut to the keys that matter here.
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
config:
  proxy_config_yaml: |
    router_settings:
      routing_strategy: usage-based-routing-v2
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

# EAT's vllm/xeon-values.yaml at 0d3438d (release-1.0.3-hotfix) and 0c726f5
# (release-1.1.0), cut to the keys vllm-merge.py, the chart and eat-opt.py read: the
# image, the balloon annotation and resources, one catalog entry and the default
# entry, each with VLLM_CPU_KVCACHE_SPACE.
VM="${WORK}/vm"
mkdir -p "${VM}"
for rel in 103 110; do
    if [ "${rel}" = 103 ]; then tag=v0.24.0 tp="'{{ .Values.tensor_parallel_size }}'"; else tag=v0.28.0 tp=1; fi
    cat > "${VM}/xeon-${rel}.yaml" <<EOF
accelDevice: ""
cpu_balloon_annotation: ""
resources:
  requests:
    cpu: "{{ .Values.cpu }}"
    memory: "{{ .Values.memory }}"
image:
  repository: vllm/vllm-openai-cpu
  tag: "${tag}"
  pullPolicy: IfNotPresent
LLM_MODEL_ID: ""
modelConfigs:
  "Qwen/Qwen3-1.7B":
    tensor_parallel_size: ${tp}
    configMapValues:
      VLLM_CPU_KVCACHE_SPACE: "40"
      USER: "vllm"
    extraCmdArgs: ["--block-size", "128", "--dtype", "bfloat16", "--enable-auto-tool-choice", "--tool-call-parser", "hermes"]
defaultModelConfigs:
  tensor_parallel_size: ${tp}
  configMapValues:
    VLLM_CPU_KVCACHE_SPACE: "40"
  extraCmdArgs: ["--block-size", "128", "--dtype", "bfloat16"]
EOF
done
PIN103='v0.24.0@sha256:b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1'
PIN110='v0.28.0@sha256:197354e6475e638eb5ccca09beb078488046a82de0e204e7429bf1a804df9647'
IMG110="${EAT}/vllm-image-release-1.1.0.yaml"
# tag_of <values file>: its image.tag
tag_of() { python3 -c 'import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))["image"]["tag"])' "$1" 2>/dev/null; }

# brownfield.sh against a stub EAT. Stack mode: the gateway playbook, and only it,
# gets the chart copies as extra vars; the menu answers are unchanged. The stub's
# deploy-agentic-stack.sh sets DEPLOY_GUARDRAILS at top level as release-1.1.0's does
# (line 195, run when brownfield.sh sources it) and deploys the guard through
# deploy-guardrails.yml when it is on, as fresh-install.sh:419 does. Model mode: the
# calls brownfield.sh makes, each a stub, with EAT's xeon-values.yaml in the checkout.
BF="${WORK}/bf"
mkdir -p "${BF}/bin" "${BF}/home"
# EAT 0c726f5's core/helm-charts/vllm, cut to what the guard's helm call reads.
VC="${WORK}/vllm-chart"
mkdir -p "${VC}/templates"
printf 'apiVersion: v2\nname: vllm\nversion: 0.1.0\n' > "${VC}/Chart.yaml"
printf 'image:\n  repository: vllm/vllm-openai-cpu\n' > "${VC}/values.yaml"
cat > "${VC}/templates/deployment.yaml" <<'EOF'
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
EOF
cat > "${VC}/guardrail-values.yaml" <<'EOF'
fullnameOverride: vllm-guard
LLM_MODEL_ID: "Qwen/Qwen3Guard-Gen-0.6B"
cpu: 2
memory: 16Gi
image:
  repository: vllm/vllm-openai-cpu
  tag: "v0.28.0"
  pullPolicy: IfNotPresent
defaultModelConfigs:
  configMapValues:
    VLLM_CPU_KVCACHE_SPACE: "4"
  extraCmdArgs: ["--block-size", "128", "--dtype", "bfloat16", "--max-num-seqs", "8"]
modelConfigs:
  "Qwen/Qwen3Guard-Gen-0.6B":
    optimal_cpu_cores: 8
EOF
cp "${VM}/xeon-110.yaml" "${VC}/xeon-values.yaml"
# bf_eat <config lines...>: a fresh stub EAT checkout with that agentic-config.cfg.
bf_eat() {
    rm -rf "${BF}/eat"
    cp -r "${FIX}" "${BF}/eat"
    cp -r "${VC}" "${BF}/eat/core/helm-charts/vllm"
    printf '%s\n' "$@" > "${BF}/eat/core/inventory/agentic-config.cfg"
    cat > "${BF}/eat/deploy-agentic-stack.sh" <<'EOF'
DEPLOY_GUARDRAILS="${DEPLOY_GUARDRAILS:-on}"
_source_core_libs() { CORE_DIR="${EAT_DIR}/core"; HOMEDIR="${CORE_DIR}"; }
brownfield_deployment() {
    local a b c
    read -r a; read -r b; read -r c
    echo "answers ${a} ${b} ${c}" >> "${STUB_LOG}"
    echo "DEPLOY_GUARDRAILS=${DEPLOY_GUARDRAILS}" >> "${STUB_LOG}"
    cd "${CORE_DIR}"
    ansible-playbook -i hosts playbooks/deploy-ingress-controller.yml --extra-vars "secret_name=api.example.com"
    ansible-playbook -i hosts playbooks/deploy-genai-gateway.yml --extra-vars "secret_name=api.example.com" --vault-password-file v
    if [[ "${DEPLOY_GUARDRAILS:-off}" == "on" ]]; then
        ansible-playbook -i hosts playbooks/deploy-guardrails.yml --extra-vars "hugging_face_token= guardrail_model=Qwen/Qwen3Guard-Gen-0.6B" --vault-password-file v
    fi
}
manage_kubeconfig() { :; }
read_config_file() { :; }
prompt_for_input() { :; }
setup_initial_env() { REMOTEDIR=/tmp/helm-charts; xeon_values_file_path="${REMOTEDIR}/vllm/xeon-values.yaml"; brownfield_deployment_host_file=bastion-hosts.yaml; }
setup_bastion() { :; }
deploy_inference_llm_models_playbook() { echo "models ${model_name_list} values ${xeon_values_file_path} inventory ${INVENTORY_PATH}" >> "${STUB_LOG}"; }
EOF
}
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
# bf_run [VAR=value...] [-- brownfield.sh args...]: brownfield.sh's output; its calls in ${BF}/log.
bf_run() {
    local envs=()
    while [ $# -gt 0 ] && [ "$1" != -- ]; do envs+=("$1"); shift; done
    [ $# -eq 0 ] || shift
    : > "${BF}/log"
    env -u DEPLOY_GUARDRAILS -u EAT_VLLM_IMAGE_VALUES PATH="${BF}/bin:${PATH}" HOME="${BF}/home" STUB_LOG="${BF}/log" \
        EAT_DIR="${BF}/eat" EAT_KUBECONFIG="${WORK}/kubeconfig" K3D_GPU_EAT_DIR="${EAT}" ${envs[@]+"${envs[@]}"} \
        bash "${EAT}/brownfield.sh" "$@" 2>&1
}
bf_eat cluster_url=api.example.com
out=$(bf_run) && rc=0 || rc=$?
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
if grep -qx 'DEPLOY_GUARDRAILS=off' <<< "${log}" && ! grep -q 'deploy-guardrails.yml' <<< "${log}"; then
    ok "brownfield.sh stack: no deploy_guardrails key, no guard (release-1.1.0 alone would default it on)"
else fail "brownfield.sh stack: guard without deploy_guardrails: ${log}"; fi

# The guardrail switch comes from the config, never from the environment.
# guard_case <name> <expect: off|on> [bf_run VAR=value...] -- <config lines...>
guard_case() {
    local name=$1 want=$2 envs=() out rc log
    shift 2
    while [ "$1" != -- ]; do envs+=("$1"); shift; done
    shift
    bf_eat cluster_url=api.example.com "$@"
    out=$(bf_run ${envs[@]+"${envs[@]}"}) && rc=0 || rc=$?
    log=$(cat "${BF}/log")
    if [ "${rc}" -eq 0 ] && grep -qx "DEPLOY_GUARDRAILS=${want}" <<< "${log}"; then ok "brownfield.sh stack: ${name}: guard ${want}"
    else fail "brownfield.sh stack: ${name} (rc=${rc}, want ${want}): ${out} ${log}"; fi
}
mapfile -t SHIPPED < <(grep -v '^cluster_url=' "${EAT}/agentic-config.cfg")
guard_case "shipped agentic-config.cfg" off -- "${SHIPPED[@]}"
guard_case "deploy_guardrails=off with DEPLOY_GUARDRAILS=on in the environment" off DEPLOY_GUARDRAILS=on -- deploy_guardrails=off
guard_case "deploy_guardrails=ON is not on to EAT" off -- deploy_guardrails=ON
guard_case "deploy_guardrails=on" on EAT_VLLM_IMAGE_VALUES="${IMG110}" -- deploy_guardrails=on
guard_case "deploy_guardrails = \"yes\"" on EAT_VLLM_IMAGE_VALUES="${IMG110}" -- 'deploy_guardrails = "yes"'
guard_case "deploy_guardrails on then off, last wins" off -- deploy_guardrails=on deploy_guardrails=off

# deploy_guardrails=on: deploy-guardrails.yml, and only it, gets a copy of EAT's vllm
# chart whose guardrail-values.yaml names the guard image by digest.
bf_eat cluster_url=api.example.com deploy_guardrails=on
out=$(bf_run EAT_VLLM_IMAGE_VALUES="${IMG110}") && rc=0 || rc=$?
log=$(cat "${BF}/log")
guard_call=$(grep '^ansible-playbook .*deploy-guardrails.yml' <<< "${log}" || true)
GC="${BF}/home/k3d-gpu-guard-charts"
if [ "${rc}" -eq 0 ] && grep -qF -- "--extra-vars @${BF}/home/k3d-gpu-guard-vars.yml" <<< "${guard_call}" \
   && grep -qx "remote_helm_charts_base: ${GC}" <<< "${log}" \
   && [ "$(tag_of "${GC}/vllm/guardrail-values.yaml")" = "${PIN110}" ] \
   && cmp -s "${VC}/Chart.yaml" "${GC}/vllm/Chart.yaml" && cmp -s "${VC}/templates/deployment.yaml" "${GC}/vllm/templates/deployment.yaml" \
   && [ "$(tag_of "${BF}/eat/core/helm-charts/vllm/guardrail-values.yaml")" = v0.28.0 ]; then
    ok "brownfield.sh stack: deploy_guardrails=on installs the guard from a chart copy at ${PIN110:0:16}..., EAT's checkout untouched"
else
    fail "brownfield.sh stack: guard call (rc=${rc}): ${out} | ${guard_call}"; sed 's/^/  | /' <<< "${log}"
fi
if python3 - "${GC}/vllm/guardrail-values.yaml" <<'PY'
import sys, yaml
v = yaml.safe_load(open(sys.argv[1]))
assert v["image"] == {"repository": "vllm/vllm-openai-cpu", "tag": v["image"]["tag"], "pullPolicy": "IfNotPresent"}, v["image"]
assert v["cpu"] == 2 and v["memory"] == "16Gi" and v["fullnameOverride"] == "vllm-guard", v
assert v["defaultModelConfigs"]["configMapValues"]["VLLM_CPU_KVCACHE_SPACE"] == "4", v
assert v["modelConfigs"]["Qwen/Qwen3Guard-Gen-0.6B"]["optimal_cpu_cores"] == 8, v
PY
then ok "brownfield.sh stack: the guard keeps EAT's values but the image (cpu 2, 16Gi, KV 4 GiB)"
else fail "brownfield.sh stack: the guard's values changed beyond the image"; fi
calls=$(grep '^ansible-playbook ' <<< "${log}")
if [ "$(grep -c -- '--extra-vars @' <<< "${calls}")" -eq 2 ] \
   && grep -- '--extra-vars @' <<< "${calls}" | grep -q 'deploy-genai-gateway.yml' \
   && grep -- '--extra-vars @' <<< "${calls}" | grep -q 'deploy-guardrails.yml'; then
    ok "brownfield.sh stack: only the gateway and guard playbooks get an extra vars file"
else fail "brownfield.sh stack: extra vars files: ${calls}"; fi
bf_eat cluster_url=api.example.com deploy_guardrails=on
out=$(bf_run) && rc=0 || rc=$?
if [ "${rc}" -ne 0 ] && grep -qF 'deploy_guardrails=on needs EAT_VLLM_IMAGE_VALUES' <<< "${out}" && ! grep -q '^answers' "${BF}/log"; then
    ok "brownfield.sh stack: deploy_guardrails=on without an image overlay stops before EAT runs"
else fail "brownfield.sh stack: guard with no overlay (rc=${rc}): ${out}"; fi
bf_eat cluster_url=api.example.com deploy_guardrails=on
sed -i 's#playbooks/deploy-guardrails.yml#playbooks/deploy-guardrails-renamed.yml#' "${BF}/eat/deploy-agentic-stack.sh"
out=$(bf_run EAT_VLLM_IMAGE_VALUES="${IMG110}") && rc=0 || rc=$?
if [ "${rc}" -ne 0 ] && grep -q 'deploy-guardrails.yml never ran through the pinning wrapper' <<< "${out}"; then
    ok "brownfield.sh stack: fails when the guard playbook bypasses the pin"
else fail "brownfield.sh stack: guard bypass not caught (rc=${rc}): ${out}"; fi
# A gateway playbook the wrapper never sees (enabled in the config) fails the deploy.
bf_eat cluster_url=api.example.com deploy_genai_gateway=on
sed -i 's#playbooks/deploy-genai-gateway.yml#playbooks/deploy-genai-gateway-renamed.yaml#' "${BF}/eat/deploy-agentic-stack.sh"
out=$(bf_run) && rc=0 || rc=$?
if [ "${rc}" -ne 0 ] && grep -q 'deploy-genai-gateway.yml never ran through the pinning wrapper' <<< "${out}"; then
    ok "brownfield.sh stack: fails when the gateway playbook bypasses the pins"
else fail "brownfield.sh stack: bypass not caught (rc=${rc}): ${out}"; fi

# Model mode: EAT's xeon-values.yaml merged with the overlays and handed to EAT's
# playbook. On release-1.1.0's values the image overlay is what makes the tags meet;
# without it brownfield.sh stops before EAT's playbook runs.
# bf_model <xeon-values fixture> [VAR=value...]
bf_model() {
    local fx=$1
    shift
    bf_eat cluster_url=api.example.com
    cp "${fx}" "${BF}/eat/core/helm-charts/vllm/xeon-values.yaml"
    bf_run EAT_MODEL_OPT=off EAT_VLLM_VALUES="${EAT}/vllm-values.yaml" "$@" -- model cpu-qwen3-1-7b
}
MERGED="${BF}/home/xeon-values.k3d-gpu.yaml"
out=$(bf_model "${VM}/xeon-103.yaml") && rc=0 || rc=$?
if [ "${rc}" -eq 0 ] && grep -qx "models cpu-qwen3-1-7b values ${MERGED} inventory bastion-hosts.yaml" "${BF}/log" \
   && [ "$(tag_of "${MERGED}")" = "${PIN103}" ]; then
    ok "brownfield.sh model: release-1.0.3-hotfix's values with vllm-values.yaml alone, v0.24.0 by digest"
else fail "brownfield.sh model 1.0.3 (rc=${rc}): ${out} $(cat "${BF}/log")"; fi
out=$(bf_model "${VM}/xeon-110.yaml" EAT_VLLM_IMAGE_VALUES="${IMG110}") && rc=0 || rc=$?
if [ "${rc}" -eq 0 ] && grep -qx "models cpu-qwen3-1-7b values ${MERGED} inventory bastion-hosts.yaml" "${BF}/log" \
   && [ "$(tag_of "${MERGED}")" = "${PIN110}" ]; then
    ok "brownfield.sh model: release-1.1.0's values with the image overlay, v0.28.0 by digest"
else fail "brownfield.sh model 1.1.0 (rc=${rc}): ${out} $(cat "${BF}/log")"; fi
rm -f "${MERGED}"
out=$(bf_model "${VM}/xeon-110.yaml") && rc=0 || rc=$?
if [ "${rc}" -ne 0 ] && grep -qF 'v0.24.0' <<< "${out}" && grep -qF 'v0.28.0' <<< "${out}" \
   && ! grep -q '^models ' "${BF}/log" && [ ! -e "${MERGED}" ]; then
    ok "brownfield.sh model: release-1.1.0's values without the image overlay are refused, naming both tags"
else fail "brownfield.sh model 1.1.0 without the overlay (rc=${rc}): ${out} $(cat "${BF}/log")"; fi

# 6. eat-opt.py: the model optimizer as values on EAT's own entry, printed against
#    stock. EAT's playbook and values are reduced to the lines eat-opt.py reads
#    (deploy-inference-models.yml block for cpu-gemma4-26b-a4b, xeon-values.yaml
#    entry for google/gemma-4-26B-A4B-it, both at 0d3438d); model metadata is the
#    saved bundle in tests/fixtures/hf, through eat-opt.py's cache.
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
  tag: "v0.24.0@sha256:b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1"
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
    ok "eat-opt.py: no speculative config on EAT's vLLM v0.24.0 CPU image, and says why"
else fail "eat-opt.py: speculative config on v0.24.0 CPU: ${out}"; fi
cp "${OPT}/stock.yaml" "${OPT}/values.yaml"
out=$(EAT_MODEL_OPT=off python3 "${EAT}/eat-opt.py" "${OPT}/playbook.yml" "${OPT}/values.yaml" cpu-gemma4-26b-a4b "${ROOT}/console" --cache "${OPT}/cache" 2>&1)
if cmp -s "${OPT}/stock.yaml" "${OPT}/values.yaml" && grep -q 'EAT_MODEL_OPT=off' <<< "${out}"; then
    ok "eat-opt.py: EAT_MODEL_OPT=off leaves the values as merged"
else fail "eat-opt.py: EAT_MODEL_OPT=off: ${out}"; fi
out=$(python3 "${EAT}/eat-opt.py" "${OPT}/playbook.yml" "${OPT}/values.yaml" cpu-nope "${ROOT}/console" --cache "${OPT}/cache" 2>&1) && rc=0 || rc=$?
if [ "${rc}" -eq 0 ] && cmp -s "${OPT}/stock.yaml" "${OPT}/values.yaml" && grep -q 'no LLM_MODEL_ID for cpu-nope' <<< "${out}"; then
    ok "eat-opt.py: an unknown catalog id leaves the values as merged, with one line"
else fail "eat-opt.py: unknown catalog id (rc=${rc}): ${out}"; fi

# 6b. The same on release-1.1.0's values: its 0c726f5 entry for the model (image
#     v0.28.0 by tag), merged by vllm-merge.py with vllm-values.yaml and the image
#     overlay. eat-opt.py reads vLLM 0.28.0 from the merged tag. The expected choices
#     are computed here from console/model_opt.py for vLLM 0.28.0 on the CPU.
cat > "${OPT}/xeon-110.yaml" <<'EOF'
image:
  repository: vllm/vllm-openai-cpu
  tag: "v0.28.0"
  pullPolicy: IfNotPresent
modelConfigs:
  "google/gemma-4-26B-A4B-it":
    tensor_parallel_size: 1
    pipeline_parallel_size: 1
    configMapValues:
      HF_HOME: "/data"
      VLLM_CPU_KVCACHE_SPACE: "40"
    extraCmdArgs:
      [
        "--block-size", "128",
        "--dtype", "bfloat16",
        "--distributed_executor_backend", "mp",
        "--enable_chunked_prefill",
        "--enforce-eager",
        "--enable-prefix-caching",
        "--enable-auto-tool-choice",
        "--tool-call-parser", "gemma4",
        "--chat-template", "/workspace/vllm/examples/tool_chat_template_gemma4.jinja",
      ]
defaultModelConfigs:
  tensor_parallel_size: 1
  configMapValues:
    VLLM_CPU_KVCACHE_SPACE: "40"
  extraCmdArgs: ["--dtype", "bfloat16"]
EOF
if "${EAT}/vllm-merge.py" --kv-gib 6 "${OPT}/xeon-110.yaml" "${OPT}/merged-110.yaml" "${EAT}/vllm-values.yaml" "${IMG110}" 2>&1; then
    cp "${OPT}/merged-110.yaml" "${OPT}/merged-110.stock.yaml"
    out=$(python3 "${EAT}/eat-opt.py" "${OPT}/playbook.yml" "${OPT}/merged-110.yaml" cpu-gemma4-26b-a4b "${ROOT}/console" \
        --cache "${OPT}/cache" 2>&1) && rc=0 || rc=$?
    printf '%s\n' "${out}" > "${OPT}/eat-opt-110.out"
    if [ "${rc}" -eq 0 ] && res=$(python3 - "${ROOT}/console" "${OPT}/cache" "${OPT}/merged-110.stock.yaml" \
            "${OPT}/merged-110.yaml" "${OPT}/eat-opt-110.out" 2>&1 <<'PY'
import sys, yaml
console, cache, stock_path, merged_path, out_path = sys.argv[1:]
sys.path.insert(0, console)
import model_opt
mid = "google/gemma-4-26B-A4B-it"
bundle = model_opt.load_or_fetch(mid, cache=cache)
def patch_at(version):
    return model_opt.eat_patch(model_opt.optimize(bundle, "vllm", "CPU", {"vllm_version": version, "thinking": "off", "variant": mid}))
patch = patch_at("0.28.0")
# The version is what the explanation turns on: 0.24.0's differs.
assert patch_at("0.24.0")["explain"] != patch["explain"]
out = open(out_path).read()
assert "vLLM 0.28.0 on CPU" in out, out
assert patch["summary"] in out, (patch["summary"], out)
for line in patch["explain"][1:]:
    assert ("  " + line) in out, (line, out)
eat_args = yaml.safe_load(open(stock_path))["modelConfigs"][mid]["extraCmdArgs"]
entry = yaml.safe_load(open(merged_path))["modelConfigs"][mid]
args = entry["extraCmdArgs"]
# EAT's own arguments first and unchanged, then each optimizer argument EAT lacks.
have = {a for a in eat_args if a.startswith("--")}
want, add, i = [], patch["add_args"], 0
while i < len(add):
    flag = add[i]
    value = add[i + 1] if i + 1 < len(add) and not add[i + 1].startswith("--") else None
    i += 2 if value is not None else 1
    if flag != "--speculative-config" and flag not in have:
        want += [flag] + ([value] if value is not None else [])
assert args == eat_args + want, (args, eat_args + want)
assert ("speculativeConfig" in entry) == bool(patch["speculative_config"]), entry
print("+ " + " ".join(want))
PY
    ); then
        ok "eat-opt.py on release-1.1.0's values: vLLM 0.28.0, the console optimizer's 0.28.0 choices (${res})"
    else
        fail "eat-opt.py on release-1.1.0's values (rc=${rc}): ${res:-} ${out}"
    fi
else
    fail "eat-opt.py on release-1.1.0's values: vllm-merge.py failed"
fi

# 7. vllm-merge.py: EAT's xeon-values.yaml <- vllm-values.yaml <- the release's image
#    overlay, every KV cache set to --kv-gib. The merged image tag must be the one EAT's
#    own file names (the part before '@'), so release-1.1.0's model configs never run on
#    v0.24.0 and release-1.0.3-hotfix's never on v0.28.0.
# merge_case <name> <expect: ok|refused> <tag or refusal text> <vllm-merge.py args...>
merge_case() {
    local name=$1 want=$2 what=$3 out rc
    shift 3
    rm -f "${VM}/out.yaml"
    out=$("${EAT}/vllm-merge.py" "$@" 2>&1) && rc=0 || rc=$?
    if [ "${want}" = ok ] && [ "${rc}" -eq 0 ] && [ "$(tag_of "${VM}/out.yaml")" = "${what}" ]; then
        ok "vllm-merge.py: ${name}"
    elif [ "${want}" = refused ] && [ "${rc}" -ne 0 ] && grep -qF -- "${what}" <<< "${out}" && [ ! -e "${VM}/out.yaml" ]; then
        ok "vllm-merge.py refuses ${name}"
    else
        fail "vllm-merge.py ${name} (rc=${rc}, want ${want} ${what}): ${out}"
    fi
}
cp "${VM}/xeon-103.yaml" "${VM}/xeon-103.orig"
merge_case "release-1.0.3-hotfix + vllm-values.yaml: v0.24.0 by digest" ok "${PIN103}" \
    --kv-gib 6 "${VM}/xeon-103.yaml" "${VM}/out.yaml" "${EAT}/vllm-values.yaml"
if python3 - "${VM}/out.yaml" <<'PY'
import sys, yaml
v = yaml.safe_load(open(sys.argv[1]))
assert v["memory"] == "16Gi" and v["cpu"] == "8" and v["cpu_balloon_annotation"] == "performance", v
assert v["image"]["repository"] == "vllm/vllm-openai-cpu" and v["image"]["pullPolicy"] == "IfNotPresent", v["image"]
q = v["modelConfigs"]["Qwen/Qwen3-1.7B"]
assert q["configMapValues"] == {"VLLM_CPU_KVCACHE_SPACE": "6", "USER": "vllm"}, q
assert q["extraCmdArgs"][-1] == "hermes", q
assert v["defaultModelConfigs"]["configMapValues"]["VLLM_CPU_KVCACHE_SPACE"] == "6", v
assert v["resources"]["requests"]["memory"] == "{{ .Values.memory }}", v
PY
then ok "vllm-merge.py: memory 16Gi, cpu \"8\", balloon performance and KV 6 GiB survive; EAT's keys kept"
else fail "vllm-merge.py: merged values"; fi
cmp -s "${VM}/xeon-103.yaml" "${VM}/xeon-103.orig" && ok "vllm-merge.py: EAT's values file is left as it was" \
    || fail "vllm-merge.py changed EAT's values file"
merge_case "release-1.1.0 + vllm-values.yaml + its image overlay: v0.28.0 by digest" ok "${PIN110}" \
    --kv-gib 6 "${VM}/xeon-110.yaml" "${VM}/out.yaml" "${EAT}/vllm-values.yaml" "${IMG110}"
if python3 - "${VM}/out.yaml" <<'PY'
import sys, yaml
v = yaml.safe_load(open(sys.argv[1]))
assert v["memory"] == "16Gi" and v["cpu"] == "8" and v["cpu_balloon_annotation"] == "performance", v
assert v["modelConfigs"]["Qwen/Qwen3-1.7B"]["configMapValues"]["VLLM_CPU_KVCACHE_SPACE"] == "6", v
assert v["modelConfigs"]["Qwen/Qwen3-1.7B"]["tensor_parallel_size"] == 1, v
PY
then ok "vllm-merge.py: on release-1.1.0's values too, memory, cpu, balloon and KV survive"
else fail "vllm-merge.py: merged release-1.1.0 values"; fi
merge_case "release-1.1.0 + vllm-values.yaml alone (v0.24.0 under v0.28.0's configs)" refused \
    "v0.24.0" --kv-gib 6 "${VM}/xeon-110.yaml" "${VM}/out.yaml" "${EAT}/vllm-values.yaml"
merge_case "release-1.1.0 + vllm-values.yaml alone, naming EAT's tag" refused \
    "v0.28.0" --kv-gib 6 "${VM}/xeon-110.yaml" "${VM}/out.yaml" "${EAT}/vllm-values.yaml"
merge_case "release-1.0.3-hotfix + release-1.1.0's image overlay" refused \
    "v0.24.0" --kv-gib 6 "${VM}/xeon-103.yaml" "${VM}/out.yaml" "${EAT}/vllm-values.yaml" "${IMG110}"
merge_case "no --kv-gib: EAT's own KV cache kept, tag checked" ok "${PIN110}" \
    "${VM}/xeon-110.yaml" "${VM}/out.yaml" "${IMG110}"
python3 -c 'import sys, yaml; v = yaml.safe_load(open(sys.argv[1])); assert v["defaultModelConfigs"]["configMapValues"]["VLLM_CPU_KVCACHE_SPACE"] == "40"' \
    "${VM}/out.yaml" 2>/dev/null && ok "vllm-merge.py: without --kv-gib the KV cache stays EAT's" \
    || fail "vllm-merge.py: KV cache changed without --kv-gib"
merge_case "--require-digest on a tag without a digest" refused \
    "not <tag>@sha256:<digest>" --require-digest "${VM}/xeon-110.yaml" "${VM}/out.yaml"
merge_case "--require-digest on the image overlay" ok "${PIN110}" \
    --require-digest "${VM}/xeon-110.yaml" "${VM}/out.yaml" "${IMG110}"
printf 'modelConfigs: {}\n' > "${VM}/no-image.yaml"
merge_case "an EAT values file with no image.tag" refused "names no image.tag" \
    "${VM}/no-image.yaml" "${VM}/out.yaml" "${EAT}/vllm-values.yaml"

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
