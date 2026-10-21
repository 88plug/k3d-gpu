#!/usr/bin/env bash
# Adversarial first-time-user cases for `k3d-gpu quickstart` / `uninstall`
# (scripts/k3d-gpu) on hosts other than the dev test bed: a recycled port-forward
# pid, a small WSL2 VM, WSL2 without /dev/dxg and no accelerator, a corporate
# proxy, and Ctrl-C during the model download. No cluster, no network: docker,
# k3d and kubectl are stubs on PATH, and the hosts are SYSROOT fixtures.
#
# Usage: tests/quickstart_adversarial_test.sh
# shellcheck disable=SC1090,SC2034

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHER="${ROOT}/scripts/k3d-gpu"
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }
has()  { if grep -Fq -- "$3" <<< "$2"; then ok "$1"; else fail "$1: missing '$3' in: $(head -c 400 <<< "$2")"; fi; }
lacks(){ if grep -Fq -- "$3" <<< "$2"; then fail "$1: unexpected '$3'"; else ok "$1"; fi; }

TMP=$(mktemp -d)
VICTIM=""
cleanup() {
    [ -n "${VICTIM}" ] && kill "${VICTIM}" 2>/dev/null
    pkill -F "${TMP}/agent.pid" 2>/dev/null
    for p in $(cat "${TMP}"/state/pf.pids 2>/dev/null); do kill "${p}" 2>/dev/null; done
    rm -rf "${TMP}"
}
trap cleanup EXIT

lib() { sed '/^case "\${1:-help}" in$/,$d' "${LAUNCHER}"; }
meminfo() { mkdir -p "$1/proc"; printf 'MemTotal:       %s kB\nMemAvailable:   1000 kB\n' "$2" > "$1/proc/meminfo"; }
wslroot() { mkdir -p "$1/proc"; echo "Linux version 6.6.87.2-microsoft-standard-WSL2" > "$1/proc/version"; }

S="${TMP}/state" B="${TMP}/bin"
mkdir -p "${S}" "${B}"
meminfo "${TMP}/host" 131521428
cat > "${B}/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "${STUB_STATE}/log"
EOF
cat > "${B}/k3d" <<'EOF'
#!/usr/bin/env bash
echo "k3d $*" >> "${STUB_STATE}/log"
case "$1 $2" in
    "cluster get") [ -f "${STUB_STATE}/cluster" ]; exit ;;
    "cluster delete") rm -f "${STUB_STATE}/cluster" ;;
    "cluster create") touch "${STUB_STATE}/cluster" ;;
esac
exit 0
EOF
cat > "${B}/curl" <<'EOF'
#!/usr/bin/env bash
exit 6
EOF
cat > "${B}/kubectl" <<'EOF'
#!/usr/bin/env bash
echo "kubectl $*" >> "${STUB_STATE}/log"
case "$*" in
    *"get deployment ovms"*)
        [ -f "${STUB_STATE}/agent" ] || exit 1
        printf '1 ["--model_path=/models/%s","--port=8000"]' "$(cat "${STUB_STATE}/agent")" ;;
    *"get secret open-webui-admin"*) [ -f "${STUB_STATE}/secret" ] || exit 1 ;;
    *"create secret generic open-webui-admin"*) touch "${STUB_STATE}/secret" ;;
    *"create namespace"*) echo "kind: Namespace" ;;
    *"apply -f -"*) cat > /dev/null ;;
    *port-forward*) echo $$ >> "${STUB_STATE}/pf.pids"; exec -a "kubectl $*" sleep 60 ;;
esac
exit 0
EOF
# `self agent up` runs long (a model download) and records its pid.
cat > "${B}/self" <<'EOF'
#!/usr/bin/env bash
echo "self $*" >> "${STUB_STATE}/log"
case "$1" in
    up) touch "${STUB_STATE}/cluster" ;;
    agent) echo $$ > "${STUB_STATE}/../agent.pid"
           sleep "${STUB_AGENT_SLEEP:-0}"
           echo "${K3D_GPU_AGENT_MODEL}" > "${STUB_STATE}/agent" ;;
esac
EOF
chmod +x "${B}"/*

qs() {
    (export PATH="${B}:${PATH}" STUB_STATE="${S}" K3D_GPU_STATE_DIR="${S}/state" K3D_GPU_QUICKSTART_DIR="${S}/qs" K3D_GPU_QUICKSTART_POLL=0 \
        K3D_GPU_CHAT_UI="${ROOT}/share/agent-chat-ui.yml" K3D_GPU_VENDORS="${QS_VENDORS-hybrid}"
     source <(lib); SYSROOT="${QS_SYSROOT:-${TMP}/host}"; qs_self() { echo "${B}/self"; }; resource_total() { echo 0; }; "$@") 2>&1
}

# ---- 1. recycled pid in port-forward.pid --------------------------------------
# After a reboot or a crashed port-forward, the pid in the file can belong to an
# unrelated process of this user. uninstall must not kill it, and quickstart
# must not report it as the running port-forward.
mkdir -p "${S}/qs"
sleep 300 &
VICTIM=$!
echo "${VICTIM}" > "${S}/qs/port-forward.pid"
touch "${S}/cluster"
qs cmd_uninstall --yes >/dev/null
if kill -0 "${VICTIM}" 2>/dev/null; then ok "uninstall spares an unrelated process with the recycled pid"
else fail "uninstall killed unrelated pid ${VICTIM} (recycled port-forward.pid)"; fi
kill "${VICTIM}" 2>/dev/null; wait "${VICTIM}" 2>/dev/null
sleep 300 &
VICTIM=$!
mkdir -p "${S}/qs"; echo "${VICTIM}" > "${S}/qs/port-forward.pid"
out=$(qs cmd_quickstart)
lacks "quickstart does not take an unrelated pid for its port-forward" "${out}" "port-forward already running (pid ${VICTIM})"
has  "quickstart starts a real port-forward when the pid is foreign" "$(cat "${S}/log")" "port-forward --address 127.0.0.1"
kill "${VICTIM}" 2>/dev/null; wait "${VICTIM}" 2>/dev/null; VICTIM=""
qs cmd_uninstall --yes >/dev/null; rm -f "${S}/log" "${S}/agent" "${S}/secret"

# ---- 2. small WSL2 VM -----------------------------------------------------------
# WSL2 gets half the Windows RAM by default, so an 8 GB laptop shows ~3.8 GiB.
# The tier's memory is a pod *request*: a request above MemTotal never schedules
# and the agent pod stays Pending. quickstart must pick a request that fits, or
# stop at step 1 with a message.
wslroot "${TMP}/wsl-small"; meminfo "${TMP}/wsl-small" 3984588
read -r m mem _ <<< "$( (source <(lib); SYSROOT="${TMP}/wsl-small"; qs_tier) )"
case "${mem}" in
    *Gi) req_kb=$(( ${mem%Gi} * 1048576 )) ;;
    *Mi) req_kb=$(( ${mem%Mi} * 1024 )) ;;
    *) req_kb=999999999 ;;
esac
if [ "${req_kb}" -lt 3984588 ]; then ok "3.8 GiB host: memory request ${mem} fits"
else
    out=$(QS_SYSROOT="${TMP}/wsl-small" qs cmd_quickstart); rc=$?
    if [ "${rc}" -ne 0 ] && grep -qi "ram\|memory" <<< "${out}" && ! grep -q "self agent" "${S}/log" 2>/dev/null; then
        ok "3.8 GiB host: stops early on RAM"
    else fail "3.8 GiB host: tier asks ${mem} of a 3.8 GiB host and quickstart proceeds (agent pod would stay Pending)"; fi
fi
qs cmd_uninstall --yes >/dev/null; rm -f "${S}/log" "${S}/agent" "${S}/secret"

# ---- 3. WSL2 without /dev/dxg, no accelerator ----------------------------------
# quickstart tells this user "the agent runs on the CPU", then `k3d-gpu up` must
# not refuse with "no supported accelerator".
wslroot "${TMP}/wsl-cpu"; meminfo "${TMP}/wsl-cpu" 16000000; rm -f "${S}/cluster"
printf 'processor\t: 0\nmodel name\t: Intel(R) Core(TM) i5-1235U\nflags\t\t: fpu sse sse2 avx avx2\n' > "${TMP}/wsl-cpu/proc/cpuinfo"
guide=$( (source <(lib); SYSROOT="${TMP}/wsl-cpu"; qs_wsl_guidance) )
up=$(PATH="${B}:${PATH}" STUB_STATE="${S}" K3D_GPU_SYSROOT="${TMP}/wsl-cpu" KUBECONFIG="${TMP}/kc" \
     timeout 60 bash "${LAUNCHER}" up 2>&1)
if grep -q "so the agent runs on the CPU" <<< "${guide}"; then
    lacks "WSL2 without dxg: 'up' honors the CPU promise" "${up}" "no supported accelerator"
else
    ok "WSL2 without dxg: guidance makes no CPU-fallback promise"
    rm -f "${S}/cluster" "${S}/log"
    out=$(QS_VENDORS="" QS_SYSROOT="${TMP}/wsl-cpu" qs cmd_quickstart --no-ui); rc=$?
    if grep -q "no supported accelerator" <<< "${up}"; then
        if [ "${rc}" -ne 0 ] && grep -q "no CPU-only path" <<< "${out}" && ! grep -q "self up" "${S}/log" 2>/dev/null; then
            ok "WSL2 without dxg or accelerator: quickstart stops at step 1 and says why"
        else fail "WSL2, 'up' refuses: quickstart went on: rc=${rc} $(tail -c 600 <<< "${out}")"; fi
    elif [ "${rc}" -eq 0 ]; then
        ok "WSL2 without dxg: 'up' detects an accelerator and quickstart goes on"
    else fail "WSL2 without dxg or accelerator: rc=${rc} $(tail -c 600 <<< "${out}")"; fi
fi
rm -f "${S}/log" "${S}/cluster"

# ---- 4. corporate proxy ---------------------------------------------------------
# Behind HTTPS_PROXY the k3s node's containerd (image pulls) and OVMS --pull
# (Hugging Face) need the proxy too. Detection should carry the host's proxy
# into the cluster (k3d -e HTTPS_PROXY=...@server:*), or quickstart should say
# it will not.
meminfo "${TMP}/proxy" 16000000
mkdir -p "${TMP}/proxy/dev/dri" && touch "${TMP}/proxy/dev/dri/renderD128"
up=$(PATH="${B}:${PATH}" STUB_STATE="${S}" K3D_GPU_SYSROOT="${TMP}/proxy" KUBECONFIG="${TMP}/kc" \
     HTTPS_PROXY=http://proxy.corp:3128 https_proxy=http://proxy.corp:3128 NO_PROXY=localhost \
     K3D_GPU_VENDORS=hybrid timeout 60 bash "${LAUNCHER}" up 2>&1)
create=$(grep "k3d cluster create" "${S}/log" 2>/dev/null)
qsout=$(HTTPS_PROXY=http://proxy.corp:3128 qs cmd_quickstart --no-ui)
if grep -q "HTTPS_PROXY" <<< "${create}" || grep -qi "proxy" <<< "${qsout}"; then ok "proxy carried into the cluster or named"
else fail "HTTPS_PROXY set: neither 'k3d cluster create' nor quickstart carries or mentions it (create: ${create:-not reached})"; fi
qs cmd_uninstall --yes >/dev/null; rm -f "${S}/log" "${S}/agent" "${S}/secret"

# ---- 5. Ctrl-C during the download ----------------------------------------------
# A non-interactive bash starts `agent up &` with SIGINT ignored. Ctrl-C (SIGINT
# to the foreground process group) ends quickstart; `agent up` must end too, or
# the resumed run starts a second one beside it.
rm -f "${TMP}/agent.pid"
# A test script's background job inherits SIGINT ignored, which a shell cannot
# trap; a terminal's foreground job has it at its default, so restore that.
(export STUB_AGENT_SLEEP=30; python3 -c 'import os, signal, sys
signal.signal(signal.SIGINT, signal.SIG_DFL); os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' bash -c "$(declare -f qs lib); B='${B}' S='${S}' TMP='${TMP}' ROOT='${ROOT}' LAUNCHER='${LAUNCHER}'; qs cmd_quickstart" >/dev/null 2>&1) &
for _ in $(seq 1 50); do [ -s "${TMP}/agent.pid" ] && break; sleep 0.1; done
apid=$(cat "${TMP}/agent.pid" 2>/dev/null)
if [ -n "${apid}" ]; then
    pgid=$(ps -o pgid= -p "${apid}" | tr -d ' ')
    kill -INT -- "-${pgid}" 2>/dev/null
    sleep 2
    if kill -0 "${apid}" 2>/dev/null; then fail "Ctrl-C left 'agent up' (pid ${apid}) running after quickstart exited"
    else ok "Ctrl-C stops 'agent up' with quickstart"; fi
    kill "${apid}" 2>/dev/null
else fail "Ctrl-C case: stub agent never started"; fi
wait 2>/dev/null

echo "quickstart_adversarial_test: ${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
