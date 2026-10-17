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

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
