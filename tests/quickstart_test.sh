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
eq "8 GB host -> 1.7B"   "$(tier 8000000)"   "OpenVINO/Qwen3-1.7B-int4-ov 4Gi"
eq "11.9 GiB -> 1.7B"    "$(tier 12582911)"  "OpenVINO/Qwen3-1.7B-int4-ov 4Gi"
eq "12 GiB -> 4B"        "$(tier 12582912)"  "OpenVINO/Qwen3-4B-int4-ov 6Gi"
eq "16 GB host -> 4B"    "$(tier 16000000)"  "OpenVINO/Qwen3-4B-int4-ov 6Gi"
eq "24 GiB -> 8B"        "$(tier 25165824)"  "OpenVINO/Qwen3-8B-int4-ov 10Gi"
eq "128 GB host -> 8B"   "$(tier 131521428)" "OpenVINO/Qwen3-8B-int4-ov 10Gi"
eq "no meminfo -> default 4B" "$( (source <(lib); SYSROOT=${TMP}/none; qs_tier) )" "OpenVINO/Qwen3-4B-int4-ov 6Gi"

# ---- progress_bar -------------------------------------------------------------
pb() { (source <(lib); progress_bar "$@"); }
eq "bar 0%"      "$(pb 0 100 10)"   "[----------]   0%"
eq "bar 50%"     "$(pb 50 100 10)"  "[#####-----]  50%"
eq "bar 100%"    "$(pb 100 100 10)" "[##########] 100%"
eq "bar clamps"  "$(pb 300 100 10)" "[##########] 100%"
eq "bar no total" "$(pb 5 0 10)"    "[----------]   ?%"
eq "bar junk"    "$(pb abc 10 10)"  "[----------]   ?%"
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
has "WSL with Intel driver" "$(wsl "${TMP}/wsl-intel")" "advertised as gpu.intel.com/dxg"
mkdir -p "${TMP}/wsl-nodrv/proc" "${TMP}/wsl-nodrv/dev"
cp "${TMP}/wsl-nodxg/proc/version" "${TMP}/wsl-nodrv/proc/version" && touch "${TMP}/wsl-nodrv/dev/dxg"
has "WSL dxg without Intel driver" "$(wsl "${TMP}/wsl-nodrv")" "no Intel driver is under"

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
        printf '1 ["--model_path=/models/%s","--port=8000"]' "$(cat "${STUB_STATE}/agent")" ;;
    *"get secret open-webui-admin"*) [ -f "${STUB_STATE}/secret" ] || exit 1 ;;
    *"create secret generic open-webui-admin"*) touch "${STUB_STATE}/secret" ;;
    *"create namespace"*) echo "kind: Namespace" ;;
    *"apply -f -"*) cat >> "${STUB_STATE}/applied" ;;
    *port-forward*) echo $$ >> "${STUB_STATE}/pf.pids"; exec sleep 60 ;;
esac
exit 0
EOF
cat > "${B}/self" <<'EOF'
#!/usr/bin/env bash
echo "self $* model=${K3D_GPU_AGENT_MODEL:-} mem=${K3D_GPU_AGENT_MEMORY:-} tool=${K3D_GPU_AGENT_TOOL_PARSER:-} reason=${K3D_GPU_AGENT_REASONING_PARSER:-} backend=${K3D_GPU_AGENT_BACKEND:-}" >> "${STUB_STATE}/log"
case "$1" in
    up) touch "${STUB_STATE}/cluster" ;;
    agent) echo "${K3D_GPU_AGENT_MODEL}" > "${STUB_STATE}/agent" ;;
esac
EOF
chmod +x "${B}"/*

qs() {
    (export PATH="${B}:${PATH}" STUB_STATE="${S}" K3D_GPU_QUICKSTART_DIR="${S}/qs" K3D_GPU_QUICKSTART_POLL=0 \
        K3D_GPU_CHAT_UI="${ROOT}/share/agent-chat-ui.yml"
     source <(lib); SYSROOT="${TMP}/host"; qs_self() { echo "${B}/self"; }; "$@") 2>&1
}

out=$(qs cmd_quickstart); rc=$?
eq "first run exits 0" "${rc}" 0
log=$(cat "${S}/log")
has "first run creates the cluster" "${log}" "self up"
has "first run starts the agent with the tier model" "${log}" "self agent up model=OpenVINO/Qwen3-8B-int4-ov mem=10Gi tool=hermes3 reason=qwen3 backend=ovms"
has "progress steps shown" "${out}" "[1/5]"
has "ready line" "${out}" "[5/5] [##########] 100% Ready"
has "chat URL on loopback" "${out}" "http://127.0.0.1:8080"
has "admin secret created" "${log}" "create secret generic open-webui-admin"
eq  "admin file is 0600" "$(stat -c %a "${S}/qs/chat-admin")" 600
has "admin file holds a password" "$(cat "${S}/qs/chat-admin")" "password="
has "UI default model follows the tier" "$(cat "${S}/applied")" "value: Qwen3-8B-int4-ov"
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
has "override: agent restarted with it" "${log}" "self agent up model=OpenVINO/Qwen3-1.7B-int4-ov"

# --no-ui stops after the agent.
out=$(qs cmd_quickstart --no-ui)
has "--no-ui prints the API" "${out}" "Ready (no chat UI)"
out=$(qs cmd_quickstart --bogus); rc=$?
eq "unknown option fails" "${rc}" 1

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

echo "quickstart_test: ${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
