#!/usr/bin/env bash
# Unit tests for `k3d-gpu quickstart` and `uninstall` (scripts/k3d-gpu) and the
# pinned installer (packaging/install.sh). No cluster, no network: docker, k3d,
# kubectl and curl are stub scripts on PATH that log their arguments, and the
# launcher's own `up` / `agent up` are replaced by a stub that records the
# environment it was given.
#
# Usage: tests/quickstart_test.sh
#
# The launcher is sourced from a pipe, and its globals (SYSROOT) are set for it.
# shellcheck disable=SC1090,SC2034

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHER="${ROOT}/scripts/k3d-gpu"
INSTALLER="${ROOT}/packaging/install.sh"
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }
eq()   { if [ "$2" = "$3" ]; then ok "$1"; else fail "$1: got '$2', want '$3'"; fi; }
has()  { if grep -Fq -- "$3" <<< "$2"; then ok "$1"; else fail "$1: missing '$3' in: $(head -c 400 <<< "$2")"; fi; }
lacks(){ if grep -Fq -- "$3" <<< "$2"; then fail "$1: unexpected '$3'"; else ok "$1"; fi; }

TMP=$(mktemp -d)
trap 'kill $(cat "${TMP}"/state/pf.pids 2>/dev/null) 2>/dev/null; rm -rf "${TMP}"' EXIT

# Launcher functions only (everything above the subcommand dispatch), as tests/fuzz/shim.sh does.
lib() { sed '/^case "\${1:-help}" in$/,$d' "${LAUNCHER}"; }

# meminfo <dir> <kB>: a SYSROOT whose /proc/meminfo has that MemTotal.
meminfo() { mkdir -p "$1/proc"; printf 'MemTotal:       %s kB\nMemAvailable:   1000 kB\n' "$2" > "$1/proc/meminfo"; }

# ---- qs_tier: model and memory by RAM tier ------------------------------------
tier() { local d="${TMP}/tier-$1"; meminfo "${d}" "$1"; (source <(lib); SYSROOT=${d}; qs_tier); }
eq "8 GB host -> 1.7B"   "$(tier 8000000)"   "OpenVINO/Qwen3-1.7B-int4-ov 4Gi 6f32d81e9deebeca30bb5490a7176cf4fa8c79e3"
eq "11.9 GiB -> 1.7B"    "$(tier 12582911)"  "OpenVINO/Qwen3-1.7B-int4-ov 4Gi 6f32d81e9deebeca30bb5490a7176cf4fa8c79e3"
eq "12 GiB -> 4B"        "$(tier 12582912)"  "OpenVINO/Qwen3-4B-int4-ov 6Gi b467368d16b75df14055562fe927ae8e1f15f7ef"
eq "16 GB host -> 4B"    "$(tier 16000000)"  "OpenVINO/Qwen3-4B-int4-ov 6Gi b467368d16b75df14055562fe927ae8e1f15f7ef"
eq "24 GiB -> 8B"        "$(tier 25165824)"  "OpenVINO/Qwen3-8B-int4-ov 10Gi 5c47abf4b8e12ebe8e99745bb0c1ec17e0c0abcc"
eq "128 GB host -> 8B"   "$(tier 131521428)" "OpenVINO/Qwen3-8B-int4-ov 10Gi 5c47abf4b8e12ebe8e99745bb0c1ec17e0c0abcc"
eq "no meminfo -> default 4B" "$( (source <(lib); SYSROOT=${TMP}/none; qs_tier) )" "OpenVINO/Qwen3-4B-int4-ov 6Gi b467368d16b75df14055562fe927ae8e1f15f7ef"
# A small host (WSL2 gets half of Windows' RAM): the request shrinks to fit, then stops.
eq "3.8 GiB WSL2 VM -> request fits" "$(tier 3984588)" "OpenVINO/Qwen3-1.7B-int4-ov 2099Mi 6f32d81e9deebeca30bb5490a7176cf4fa8c79e3"
eq "2 GiB host -> too small" "$(tier 2097152)" "OpenVINO/Qwen3-1.7B-int4-ov - 6f32d81e9deebeca30bb5490a7176cf4fa8c79e3"
for kb in 2000000 3984588 8000000 12582912 25165824 131521428; do
    r=$(tier "${kb}" | cut -d' ' -f3)
    [[ "${r}" =~ ^[0-9a-f]{40}$ ]] && ok "tier at ${kb} kB pins a revision" || fail "tier at ${kb} kB: revision '${r}'"
done

# ---- progress_bar -------------------------------------------------------------
pb() { (source <(lib); progress_bar "$@"); }
eq "bar 0%"      "$(pb 0 100 10)"   "[----------]   0%"
eq "bar 50%"     "$(pb 50 100 10)"  "[#####-----]  50%"
eq "bar 100%"    "$(pb 100 100 10)" "[##########] 100%"
eq "bar clamps"  "$(pb 300 100 10)" "[##########] 100%"
eq "bar no total" "$(pb 5 0 10)"    "[----------]   ?%"
eq "bar junk"    "$(pb abc 10 10)"  "[----------]   ?%"
eq "bar non-ASCII digit" "$(pb ٣ 1 10)" "[----------]   ?%"
eq "bar non-ASCII width -> 30" "$(pb 0 0 ٣ | wc -c)" "37"
eq "bar default width" "$(pb 1 2 | wc -c)" "37"
eq "bar bad width -> 30" "$(pb 1 2 x | wc -c)" "37"

# ---- qs_wsl_guidance ----------------------------------------------------------
wsl() { (source <(lib); SYSROOT=$1; qs_wsl_guidance); }
mkdir -p "${TMP}/native/proc" && echo "Linux version 6.6.0" > "${TMP}/native/proc/version"
eq "no guidance off WSL" "$(wsl "${TMP}/native")" ""
mkdir -p "${TMP}/wsl-nodxg/proc" && echo "Linux version 6.6.87.2-microsoft-standard-WSL2" > "${TMP}/wsl-nodxg/proc/version"
out=$(wsl "${TMP}/wsl-nodxg")
has "WSL without dxg: CPU"      "${out}" "/dev/dxg is missing"
has "WSL suggests wsl --update" "${out}" "wsl --update; wsl --shutdown"
has "WSL: NPU unreachable"      "${out}" "intel/linux-npu-driver#56"
has "WSL: Docker Desktop NVIDIA-only" "${out}" "NVIDIA-only"
mkdir -p "${TMP}/wsl-intel/proc" "${TMP}/wsl-intel/dev" "${TMP}/wsl-intel/usr/lib/wsl/drivers/iigd_dch.inf_amd64_x"
cp "${TMP}/wsl-nodxg/proc/version" "${TMP}/wsl-intel/proc/version" && touch "${TMP}/wsl-intel/dev/dxg"
has "WSL with Intel driver" "$(wsl "${TMP}/wsl-intel")" "advertises the Intel GPU as gpu.intel.com/dxg"
has "WSL: LLM runs on the CPU (agent does not request dxg)" "$(wsl "${TMP}/wsl-intel")" "The LLM runs on the CPU here"
lacks "WSL without dxg: no CPU promise" "$(wsl "${TMP}/wsl-nodxg")" "so the agent runs on the CPU"
mkdir -p "${TMP}/wsl-nv/proc" "${TMP}/wsl-nv/dev" "${TMP}/wsl-nv/usr/lib/wsl/lib"
cp "${TMP}/wsl-nodxg/proc/version" "${TMP}/wsl-nv/proc/version" && touch "${TMP}/wsl-nv/dev/dxg" "${TMP}/wsl-nv/usr/lib/wsl/lib/nvidia-smi"
out=$(wsl "${TMP}/wsl-nv")
has "WSL with NVIDIA driver" "${out}" "the cluster gets the NVIDIA GPU"
lacks "WSL NVIDIA: no Intel driver advice" "${out}" "install the current"
mkdir -p "${TMP}/wsl-nodrv/proc" "${TMP}/wsl-nodrv/dev"
cp "${TMP}/wsl-nodxg/proc/version" "${TMP}/wsl-nodrv/proc/version" && touch "${TMP}/wsl-nodrv/dev/dxg"
has "WSL dxg without Intel driver" "$(wsl "${TMP}/wsl-nodrv")" "no Intel or NVIDIA driver is under"

# ---- quickstart end to end against stubs -----------------------------------------
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
    "cluster get") [ -f "${STUB_STATE}/cluster" ] ;;
    "cluster delete") rm -f "${STUB_STATE}/cluster" ;;
esac
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
        printf '1 %s ["--model_path=/models/%s","--port=8000"]' "$(cat "${STUB_STATE}/mem")" "$(cat "${STUB_STATE}/agent")" ;;
    *"get secret open-webui-admin"*) [ -f "${STUB_STATE}/secret" ] || exit 1 ;;
    *"create secret generic open-webui-admin"*) touch "${STUB_STATE}/secret" ;;
    *"create namespace"*) echo "kind: Namespace" ;;
    *"apply -f -"*) cat >> "${STUB_STATE}/applied" ;;
    *port-forward*) echo $$ >> "${STUB_STATE}/pf.pids"; exec -a "kubectl $*" sleep 60 ;;
esac
exit 0
EOF
cat > "${B}/self" <<'EOF'
#!/usr/bin/env bash
echo "self $* model=${K3D_GPU_AGENT_MODEL:-} mem=${K3D_GPU_AGENT_MEMORY:-} tool=${K3D_GPU_AGENT_TOOL_PARSER:-} reason=${K3D_GPU_AGENT_REASONING_PARSER:-} backend=${K3D_GPU_AGENT_BACKEND:-} rev=${K3D_GPU_AGENT_REVISION:-}" >> "${STUB_STATE}/log"
case "$1" in
    up) touch "${STUB_STATE}/cluster" ;;
    agent) echo "${K3D_GPU_AGENT_MODEL}" > "${STUB_STATE}/agent"; echo "${K3D_GPU_AGENT_MEMORY}" > "${STUB_STATE}/mem" ;;
esac
EOF
chmod +x "${B}"/*

qs() {
    (export PATH="${B}:${PATH}" STUB_STATE="${S}" K3D_GPU_QUICKSTART_DIR="${S}/qs" K3D_GPU_QUICKSTART_POLL=0 \
        K3D_GPU_CHAT_UI="${ROOT}/share/agent-chat-ui.yml" K3D_GPU_VENDORS="${QS_VENDORS-hybrid}"
     source <(lib); SYSROOT="${TMP}/host"; qs_self() { echo "${B}/self"; }; resource_total() { echo 0; }; "$@") 2>&1
}

out=$(qs cmd_quickstart); rc=$?
eq "first run exits 0" "${rc}" 0
log=$(cat "${S}/log")
has "first run creates the cluster" "${log}" "self up"
has "first run starts the agent with the tier model" "${log}" "self agent up model=OpenVINO/Qwen3-8B-int4-ov mem=10Gi tool=hermes3 reason=qwen3 backend= rev=5c47abf4b8e12ebe8e99745bb0c1ec17e0c0abcc"
has "admin secret read from the 0600 file" "${log}" "--from-env-file="
lacks "admin password never on argv" "${log}" "password="
has "progress steps shown" "${out}" "[1/5]"
has "ready line" "${out}" "[5/5] [##########] 100% Ready"
has "chat URL on loopback" "${out}" "http://127.0.0.1:8080"
has "admin secret created" "${log}" "create secret generic open-webui-admin"
eq  "admin file is 0600" "$(stat -c %a "${S}/qs/chat-admin")" 600
has "admin file holds a password" "$(cat "${S}/qs/chat-admin")" "password="
has "UI default model follows the tier" "$(cat "${S}/applied")" 'value: "Qwen3-8B-int4-ov"'
lacks "UI default model replaced" "$(cat "${S}/applied")" "value: Qwen3-4B-int4-ov"
has "port-forward bound to loopback" "${log}" "port-forward --address 127.0.0.1 svc/open-webui 8080:8080"
pf1=$(cat "${S}/qs/port-forward.pid")
kill -0 "${pf1}" 2>/dev/null && ok "port-forward running" || fail "port-forward not running"

: > "${S}/log"
out=$(qs cmd_quickstart); rc=$?
eq "second run exits 0" "${rc}" 0
log=$(cat "${S}/log")
lacks "second run: no cluster create" "${log}" "self up"
lacks "second run: no agent up" "${log}" "self agent"
lacks "second run: secret kept" "${log}" "create secret"
has "second run reuses port-forward" "${out}" "port-forward already running (pid ${pf1})"
has "second run says agent serving" "${out}" "agent already serving OpenVINO/Qwen3-8B-int4-ov"

# A changed model (override) restarts only the agent.
: > "${S}/log"
out=$(K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-1.7B-int4-ov qs cmd_quickstart)
log=$(cat "${S}/log")
lacks "override: cluster kept" "${log}" "self up"
has "override: agent restarted with it" "${log}" "self agent up model=OpenVINO/Qwen3-1.7B-int4-ov mem=4Gi tool= reason= backend= rev="

# A changed memory reservation alone restarts the agent too.
: > "${S}/log"
K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-1.7B-int4-ov K3D_GPU_AGENT_MEMORY=3Gi qs cmd_quickstart >/dev/null
has "memory change restarts the agent" "$(cat "${S}/log")" "self agent up model=OpenVINO/Qwen3-1.7B-int4-ov mem=3Gi"

# What the user exports reaches agent up unchanged.
: > "${S}/log"
K3D_GPU_AGENT_TOOL_PARSER=mine K3D_GPU_AGENT_REVISION=abc qs cmd_quickstart >/dev/null
has "user parser and revision win" "$(cat "${S}/log")" "tool=mine reason=qwen3 backend= rev=abc"
: > "${S}/log"
out=$(K3D_GPU_AGENT_BACKEND=vllm qs cmd_quickstart --no-ui); rc=$?
eq "user backend vllm: exits 0" "${rc}" 0
has "user backend reaches agent up unchanged" "$(cat "${S}/log")" "self agent up model= mem= tool= reason= backend=vllm"
out=$(K3D_GPU_AGENT_BACKEND=vllm qs cmd_quickstart); rc=$?
[ "${rc}" -ne 0 ] && grep -q -- "--no-ui" <<< "${out}" && ok "vllm + chat UI names --no-ui" || fail "vllm + chat UI: rc=${rc} ${out}"

# --no-ui stops after the agent.
out=$(qs cmd_quickstart --no-ui)
has "--no-ui prints the API" "${out}" "Ready (no chat UI)"
out=$(qs cmd_quickstart --bogus); rc=$?
eq "unknown option fails" "${rc}" 1

# No accelerator detected: stops at step 1, before any cluster.
mv "${S}/cluster" "${S}/cluster.keep"
: > "${S}/log"
out=$(QS_VENDORS="" qs cmd_quickstart); rc=$?
[ "${rc}" -ne 0 ] && grep -q "no CPU-only path" <<< "${out}" && ok "no accelerator: stops with a message" || fail "no accelerator: rc=${rc}"
lacks "no accelerator: nothing created" "$(cat "${S}/log")" "self up"
mv "${S}/cluster.keep" "${S}/cluster"

# Missing tools are named, nothing runs.
out=$(PATH="${TMP}/empty" qs cmd_quickstart 2>&1); rc=$?
[ "${rc}" -ne 0 ] && ok "missing tools fail" || fail "missing tools passed"

# ---- uninstall ------------------------------------------------------------------
out=$(qs cmd_uninstall < /dev/null); rc=$?
eq "uninstall without --yes and no tty refuses" "${rc}" 1
[ -f "${S}/cluster" ] && ok "refused uninstall kept the cluster" || fail "refused uninstall deleted the cluster"
out=$(qs cmd_uninstall --yes); rc=$?
eq "uninstall --yes exits 0" "${rc}" 0
has "uninstall deletes the cluster" "$(cat "${S}/log")" "k3d cluster delete gpu"
kill -0 "${pf1}" 2>/dev/null && fail "port-forward still running" || ok "port-forward stopped"
[ ! -e "${S}/qs" ] && ok "quickstart state removed" || fail "quickstart state left: $(ls "${S}/qs")"
out=$(qs cmd_uninstall --yes); rc=$?
eq "uninstall twice is fine" "${rc}" 0
has "second uninstall: no cluster" "${out}" "no cluster 'gpu'"

# A cluster quickstart only reused is kept unless --cluster.
touch "${S}/cluster"; : > "${S}/log"
qs cmd_quickstart --no-ui >/dev/null
out=$(qs cmd_uninstall --yes)
lacks "reused cluster kept" "$(cat "${S}/log")" "k3d cluster delete"
has "reused cluster: says why" "${out}" "quickstart did not create it"
qs cmd_uninstall --yes --cluster >/dev/null
has "--cluster deletes a reused cluster" "$(cat "${S}/log")" "k3d cluster delete gpu"

# Port: 8080 taken -> the next free port; the env var wins.
if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import socket, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", 0)); s.listen(1)
print(s.getsockname()[1], flush=True); time.sleep(20)' > "${TMP}/port" &
    lpid=$!
    for _ in $(seq 1 50); do [ -s "${TMP}/port" ] && break; sleep 0.1; done
    lp=$(cat "${TMP}/port")
    (source <(lib); qs_port_free "${lp}") && fail "busy port ${lp} read as free" || ok "busy port read as busy"
    kill "${lpid}" 2>/dev/null; wait "${lpid}" 2>/dev/null
fi
eq "K3D_GPU_CHAT_PORT wins" "$(K3D_GPU_CHAT_PORT=9191 bash -c 'source <(sed "/^case \"\${1:-help}\" in$/,\$d" "$0"); qs_port' "${LAUNCHER}")" 9191

# A recycled pid in port-forward.pid is not quickstart's port-forward.
mkdir -p "${S}/qs"; sleep 30 & v=$!; echo "${v}" > "${S}/qs/port-forward.pid"
(source <(lib); QS_DIR="${S}/qs"; qs_pf_pid) && fail "foreign pid taken as the port-forward" || ok "foreign pid rejected"
[ -e "${S}/qs/port-forward.pid" ] && fail "stale pid file kept" || ok "stale pid file removed"
kill "${v}" 2>/dev/null; wait "${v}" 2>/dev/null; rmdir "${S}/qs" 2>/dev/null

# Proxy: carried into the nodes (k3d --env) and the model download.
px=$(HTTPS_PROXY='http://u:p@proxy:3128' NO_PROXY=corp bash -c 'source <(sed "/^case \"\${1:-help}\" in$/,\$d" "$0"); proxy_k3d_args; proxy_yaml_env' "${LAUNCHER}")
has "proxy: node env with escaped @" "${px}" 'HTTPS_PROXY=http://u:p\@proxy:3128@server:*;agent:*'
has "proxy: NO_PROXY keeps the host's and adds the cluster's" "${px}" "NO_PROXY=corp,127.0.0.1,localhost,10.42.0.0/16,10.43.0.0/16,.svc,.cluster.local@server:*;agent:*"
has "proxy: pod env" "${px}" 'value: "http://u:p@proxy:3128"'
eq "no proxy: nothing" "$(env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy bash -c 'source <(sed "/^case \"\${1:-help}\" in$/,\$d" "$0"); proxy_k3d_args; proxy_yaml_env' "${LAUNCHER}")" ""

# Uninstall leaves other files in the state dir alone.
mkdir -p "${S}/qs" && echo keep > "${S}/qs/mine"
qs cmd_uninstall --yes >/dev/null
[ -f "${S}/qs/mine" ] && ok "uninstall keeps foreign files" || fail "uninstall removed a foreign file"

# ---- packaging/install.sh -------------------------------------------------------------
inst() { (export K3D_GPU_INSTALL_SOURCE_ONLY=1; source "${INSTALLER}"; "$@") 2>&1; }
for v in K3D_GPU_DEB_SHA256 K3D_GPU_RPM_SHA256 K3D_SHA256_AMD64 K3D_SHA256_ARM64 KUBECTL_SHA256_AMD64 KUBECTL_SHA256_ARM64; do
    val=$(sed -n "s/^${v}=//p" "${INSTALLER}")
    [[ "${val}" =~ ^[0-9a-f]{64}$ ]] && ok "installer pins ${v}" || fail "installer ${v} is not a sha256: '${val}'"
done
eq "installer version pinned" "$(sed -n 's/^K3D_GPU_VERSION=//p' "${INSTALLER}" | grep -Ec '^[0-9]+\.[0-9]+\.[0-9]+$')" 1
grep -Eq 'latest' <(grep -v '^#' "${INSTALLER}") && fail "installer names 'latest'" || ok "installer never floats to latest"
eq "arch x86_64" "$(inst go_arch x86_64)" amd64
eq "arch aarch64" "$(inst go_arch aarch64)" arm64
eq "arch riscv64 unsupported" "$(inst go_arch riscv64)" ""
printf 'hello\n' > "${TMP}/f"
H=5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03
inst sha256_ok "${TMP}/f" "${H}" && ok "sha256 match" || fail "sha256 match"
inst sha256_ok "${TMP}/f" "${H/5/6}" && fail "sha256 mismatch accepted" || ok "sha256 mismatch rejected"
inst sha256_ok "${TMP}/f" "notahash" && fail "bad hash accepted" || ok "malformed hash rejected"
# fetch through file://: a mismatched download is deleted and the run stops.
out=$(inst fetch "file://${TMP}/f" "${TMP}/got" "${H/5/6}"); rc=$?
[ "${rc}" -ne 0 ] && [ ! -e "${TMP}/got" ] && ok "fetch mismatch stops and deletes" || fail "fetch mismatch: rc=${rc}"
has "fetch mismatch message" "${out}" "sha256 mismatch"
inst fetch "file://${TMP}/f" "${TMP}/got" "${H}" >/dev/null && [ -f "${TMP}/got" ] && ok "fetch match keeps file" || fail "fetch match"
mkdir -p "${TMP}/arch/etc" && echo 'ID=manjaro' > "${TMP}/arch/etc/os-release"
has "Arch hint" "$(K3D_GPU_SYSROOT="${TMP}/arch" inst other_distro_hint)" "yay -S k3d-gpu"
has "WSL docker hint" "$(K3D_GPU_SYSROOT="${TMP}/wsl-nodxg" inst docker_hint)" "Docker Desktop for Windows with WSL integration"
has "native docker hint" "$(K3D_GPU_SYSROOT="${TMP}/native" inst docker_hint)" "docs.docker.com/engine/install"
# Full dry run with a mirror holding a wrong .deb: refused before any install.
mkdir -p "${TMP}/mirror" "${TMP}/ibin"
cp "${TMP}/f" "${TMP}/mirror/k3d-gpu_$(sed -n 's/^K3D_GPU_VERSION=//p' "${INSTALLER}")-1_all.deb"
printf '#!/bin/sh\nexit 0\n' > "${TMP}/ibin/apt-get" && chmod +x "${TMP}/ibin/apt-get"
out=$(PATH="${TMP}/ibin:${PATH}" K3D_GPU_INSTALL_BASE="file://${TMP}/mirror" K3D_GPU_INSTALL_DRY_RUN=1 bash "${INSTALLER}" 2>&1); rc=$?
[ "${rc}" -ne 0 ] && ok "tampered .deb refused" || fail "tampered .deb accepted"
lacks "nothing installed after mismatch" "${out}" "+ sudo apt-get"
# Full successful dry run: the .deb matches (its pin set to the test file's sha),
# k3d and kubectl on PATH. Exits 0 and leaves no temp dir.
printf '#!/bin/sh\nexit 0\n' > "${TMP}/ibin/k3d"; cp "${TMP}/ibin/k3d" "${TMP}/ibin/kubectl"; chmod +x "${TMP}/ibin/k3d" "${TMP}/ibin/kubectl"
cp "${TMP}/f" "${TMP}/mirror/k3d-gpu_$(sed -n 's/^K3D_GPU_VERSION=//p' "${INSTALLER}")-1_all.deb"
mkdir -p "${TMP}/itmp"
out=$(PATH="${TMP}/ibin:${PATH}" TMPDIR="${TMP}/itmp" K3D_GPU_INSTALL_BASE="file://${TMP}/mirror" K3D_GPU_INSTALL_DRY_RUN=1 \
      K3D_GPU_INSTALL_SOURCE_ONLY=1 bash -c 'source "$0"; K3D_GPU_DEB_SHA256=$1; main' "${INSTALLER}" "${H}" 2>&1); rc=$?
eq "successful install exits 0" "${rc}" 0
has "successful install runs apt-get" "${out}" "apt-get install -y"
eq "successful install leaves no temp dir" "$(ls -A "${TMP}/itmp")" ""
inst_ver=$(sed -n 's/^K3D_GPU_VERSION=//p' "${INSTALLER}")
since=$(sed -n 's/^QUICKSTART_SINCE=//p' "${INSTALLER}")
if [ "$(printf '%s\n%s\n' "${inst_ver}" "${since}" | sort -V | head -n1)" = "${since}" ]; then
    has "installer hint: quickstart (pin has it)" "${out}" "then k3d-gpu quickstart"
else
    has "installer hint: names the first quickstart release" "${out}" "ships in ${since}"
fi
launcher_ver=$(sed -n 's/^VERSION="\([^"]*\)".*/\1/p' "${LAUNCHER}")
[ "$(printf '%s\n%s\n' "${inst_ver}" "${launcher_ver}" | sort -V | tail -n1)" = "${launcher_ver}" ] \
    && ok "installer pin ${inst_ver} is not newer than the launcher ${launcher_ver}" || fail "installer pin ${inst_ver} newer than launcher ${launcher_ver}"
grep -q "cmd_quickstart" "${LAUNCHER}" && ok "launcher has quickstart" || fail "launcher lacks quickstart"

# ---- qs_backend agrees with agent_device ----------------------------------------
# On a Gaudi host only an AUTO device (any case) sends the agent to vLLM; any other
# device makes `agent up` deploy OVMS, so quickstart must take the OVMS path.
qsb() { (source <(lib); resource_total() { [ "$1" = habana.ai/gaudi ] && echo 1 || echo 0; }; qs_backend); }
for d in auto AUTO Auto; do eq "gaudi + device ${d} -> vllm" "$(K3D_GPU_AGENT_DEVICE=${d} qsb)" vllm; done
eq "gaudi + device unset -> vllm" "$(unset K3D_GPU_AGENT_DEVICE; qsb)" vllm
for d in CPU cpu GPU gpu NPU HETERO:GPU,CPU; do eq "gaudi + device ${d} -> ovms" "$(K3D_GPU_AGENT_DEVICE=${d} qsb)" ovms; done
eq "explicit backend wins" "$(K3D_GPU_AGENT_DEVICE=cpu K3D_GPU_AGENT_BACKEND=VLLM qsb)" vllm

# ---- argument-less commands refuse extra arguments ----------------------------
# `k3d-gpu down mycluster` must not delete the default cluster instead.
for c in up down test logs; do
    rm -f "${TMP}/argstate/log"; mkdir -p "${TMP}/argstate"
    out=$(PATH="${B}:${PATH}" STUB_STATE="${TMP}/argstate" timeout 20 bash "${LAUNCHER}" "${c}" mycluster 2>&1); rc=$?
    [ "${rc}" -ne 0 ] && ok "${c} with an extra argument exits non-zero" || fail "${c} with an extra argument: rc=0"
    has "${c} names the stray argument" "${out}" "mycluster"
    lacks "${c} with an extra argument runs nothing" "$(cat "${TMP}/argstate/log" 2>/dev/null)" "k3d cluster"
done

echo "quickstart_test: ${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
