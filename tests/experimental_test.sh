#!/usr/bin/env bash
# Experimental feature tests (K3D_GPU_EXPERIMENTAL). No cluster, no hardware,
# no host change: every case runs k3d-gpu against a fake sysroot with docker,
# k3d and kubectl stubbed.
#
# Per feature, a sim sysroot where it is available checks what `up` does (k3d
# create arguments, the OpenVINO ConfigMap, printed host steps), and one where
# it is not checks the exact reason. Then the framework rules: default off,
# unknown names fail, list cannot mix with names, doctor reports availability.
# Last, `K3D_GPU_EXPERIMENTAL=list` against every tests/fixtures/<platform>
# sysroot is compared with that platform's experimental.golden.
#
# Usage: tests/experimental_test.sh [--update]   (--update rewrites the goldens)

set -uo pipefail

TESTS=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "${TESTS}")
K3D_GPU="${REPO}/scripts/k3d-gpu"
UPDATE=0
[ "${1:-}" = --update ] && UPDATE=1
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

STUB_BIN="${WORK}/bin"
mkdir -p "${STUB_BIN}"
cat > "${STUB_BIN}/k3d" <<'EOF'
#!/bin/bash
[ "$1" = version ] && { echo "k3d version v0.0.0-stub"; exit 0; }
{ echo "k3d $1 $2 $3"; shift 3; printf '  %s\n' "$@"; } >> "${STUB_LOG}"
EOF
# kubectl: logs every call; `apply -f -` also logs the manifest it was given.
cat > "${STUB_BIN}/kubectl" <<'EOF'
#!/bin/bash
echo "kubectl $*" >> "${STUB_LOG}"
case "$*" in
    "apply -f -") sed 's/^/  | /' >> "${STUB_LOG}" ;;
    "get nodes -o go-template="*) cat "${STUB_ALLOCATABLE:-/dev/null}" ;;
    "get nodes -l "*" -o name") echo "node/k3d-gpu-server-0" ;;
esac
exit 0
EOF
printf '#!/bin/bash\nexit 0\n' > "${STUB_BIN}/docker"
# What the stubbed cluster reports, so verify_gpus finds each vendor at once.
printf 'gpu.intel.com/xe 1\nnpu.intel.com/accel 1\n' > "${WORK}/allocatable"
chmod +x "${STUB_BIN}"/*

# ---- fake host trees ------------------------------------------------------------

newroot() { local r="${WORK}/$1"; mkdir -p "${r}/sys" "${r}/dev" "${r}/proc/sys/kernel"; echo "6.18.0" > "${r}/proc/sys/kernel/osrelease"; echo "${r}"; }

# pci <root> <bdf> <vendor> <device> <class> [driver]
pci() {
    local root=$1 bdf=$2 dir="$1/sys/devices/pci0000:00/$2"
    mkdir -p "${dir}" "${root}/sys/bus/pci/devices"
    echo "$3" > "${dir}/vendor"; echo "$4" > "${dir}/device"; echo "$5" > "${dir}/class"
    ln -sfn "${dir}" "${root}/sys/bus/pci/devices/${bdf}"
    if [ -n "${6:-}" ]; then
        mkdir -p "${root}/sys/bus/pci/drivers/$6"
        ln -sfn "${root}/sys/bus/pci/drivers/$6" "${dir}/driver"
    fi
}
render() { mkdir -p "$1/sys/class/drm/renderD$3" "$1/dev/dri"; ln -sfn "$1/sys/devices/pci0000:00/$2" "$1/sys/class/drm/renderD$3/device"; touch "$1/dev/dri/renderD$3"; }
accel() { mkdir -p "$1/sys/class/accel/accel$3" "$1/dev/accel"; ln -sfn "$1/sys/devices/pci0000:00/$2" "$1/sys/class/accel/accel$3/device"; touch "$1/dev/accel/accel$3"; }

# A CPU-only host: nothing but AMX-less cpuinfo. Every "unmet" case starts here.
BARE=$(newroot bare)
printf 'processor\t: 0\nflags\t\t: fpu sse2\n' > "${BARE}/proc/cpuinfo"

# Panther Lake NPU (0xb03e) bound to intel_vpu, firmware on the OS scheduler.
# IDs from scripts/k3d-gpu npu_platform; sched_mode text from ivpu_sysfs.c.
NPU=$(newroot npu)
cp "${BARE}/proc/cpuinfo" "${NPU}/proc/cpuinfo"
pci "${NPU}" 0000:00:0b.0 0x8086 0xb03e 0x120000 intel_vpu
accel "${NPU}" 0000:00:0b.0 0
echo OS > "${NPU}/sys/devices/pci0000:00/0000:00:0b.0/sched_mode"

# Kernel with sched_ext, no scheduler loaded (/sys/kernel/sched_ext/state).
SCX=$(newroot scx)
cp "${BARE}/proc/cpuinfo" "${SCX}/proc/cpuinfo"
mkdir -p "${SCX}/sys/kernel/sched_ext"
echo disabled > "${SCX}/sys/kernel/sched_ext/state"

# Nova Lake-P GPU (0xd750, still behind xe force_probe) with no driver bound.
FP=$(newroot forceprobe)
cp "${BARE}/proc/cpuinfo" "${FP}/proc/cpuinfo"
pci "${FP}" 0000:00:02.0 0x8086 0xd750 0x030000

# Battlemage (0xe20b) bound to xe with an SR-IOV capability. The VF count is
# synthetic: only "non-zero" matters to the check.
SRIOV=$(newroot sriov)
cp "${BARE}/proc/cpuinfo" "${SRIOV}/proc/cpuinfo"
pci "${SRIOV}" 0000:03:00.0 0x8086 0xe20b 0x030000 xe
render "${SRIOV}" 0000:03:00.0 128
echo 2 > "${SRIOV}/sys/devices/pci0000:00/0000:03:00.0/sriov_totalvfs"
echo 0 > "${SRIOV}/sys/devices/pci0000:00/0000:03:00.0/sriov_numvfs"
mkdir -p "${SRIOV}/sys/module/xe"

# ---- runner ---------------------------------------------------------------------

# run <root> <experimental> <command...>: output, exit code and stub call log.
run() {
    local root=$1 exp=$2 log="${WORK}/calls" out rc
    shift 2
    : > "${log}"
    out=$(env -i HOME="${WORK}" PATH="${STUB_BIN}:/usr/bin:/bin" K3D_GPU_SYSROOT="${root}" \
        K3D_GPU_SHARE="${REPO}/share" STUB_LOG="${log}" STUB_ALLOCATABLE="${WORK}/allocatable" \
        K3D_GPU_EXPERIMENTAL="${exp}" K3D_GPU_VENDORS="${VENDORS:-hybrid}" bash "${SCRIPT:-${K3D_GPU}}" "$@" 2>&1)
    rc=$?
    { printf '%s\nexit=%s\n' "${out}" "${rc}"; cat "${log}"; } | sed 's/\x1b\[[0-9;]*m//g'
}

# expect_count <name> <ERE> <n> <text>: exactly n lines of text match ERE.
expect_count() {
    local n
    n=$(grep -cE -- "$2" <<< "$4")
    if [ "${n}" -eq "$3" ]; then
        PASS=$((PASS + 1)); echo "ok   $1"
    else
        FAILS=$((FAILS + 1)); echo "FAIL $1 (${n} lines match /$2/, want $3)"; sed 's/^/  | /' <<< "$4"
    fi
}

# expect <name> <ERE> <text> [negate]: text must match ERE (or must not).
expect() {
    local hit=0
    grep -E -- "$2" <<< "$3" >/dev/null && hit=1
    if { [ -z "${4:-}" ] && [ "${hit}" -eq 1 ]; } || { [ -n "${4:-}" ] && [ "${hit}" -eq 0 ]; }; then
        PASS=$((PASS + 1)); echo "ok   $1"
    else
        FAILS=$((FAILS + 1)); echo "FAIL $1 (${4:+not }matching /$2/)"; sed 's/^/  | /' <<< "$3"
    fi
}

# The script on an older k3s minor: a gate is passed only while it is off by
# default in the k3s the node image ships (EXP_K3S_MINOR).
K3D_GPU_134="${WORK}/k3d-gpu-1.34"
sed 's/^EXP_K3S_MINOR=.*/EXP_K3S_MINOR=34/' "${K3D_GPU}" > "${K3D_GPU_134}"

# EXP_K3S_MINOR must be the minor of the k3s the Dockerfile ships, so a k3s
# bump re-checks every gate's maturity (kube_features.go at the new tag).
want=$(sed -n 's/^ARG K3S_TAG="v1\.\([0-9]*\)\..*/\1/p' "${REPO}/Dockerfile")
have=$(sed -n 's/^EXP_K3S_MINOR=\([0-9]*\)$/\1/p' "${K3D_GPU}")
expect "EXP_K3S_MINOR matches the Dockerfile k3s minor (${want})" "^${want}$" "${have:-unset}"

GATES='DRAPartitionableDevices=true,DRAConsumableCapacity=true,DRADeviceBindingConditions=true,DRAExtendedResource=true,ResourceHealthStatus=true'

# ---- per feature: available -> up ------------------------------------------------

# Shipped k3s (1.37): every DRA gate is beta default-on or GA, ImageVolume is GA.
# Passing them changes nothing, and a GA gate is removed a few releases later
# (ImageVolume in 1.39), which would stop k3s from starting.
out=$(run "${BARE}" dra,image-volume up)
expect "1.37: dra label"                 "--k3s-node-label=experimental.k3d-gpu.io/dra=true@server:\*;agent:\*" "${out}"
expect "1.37: image-volume label"        "--k3s-node-label=experimental.k3d-gpu.io/image-volume=true@server:\*;agent:\*" "${out}"
expect "1.37: no default-on gate passed" "feature-gates" "${out}" negate
expect "1.37: exit 0"                    "^exit=0$" "${out}"

out=$(SCRIPT="${K3D_GPU_134}" run "${BARE}" dra up)
expect "dra: apiserver gates"  "--k3s-arg=--kube-apiserver-arg=feature-gates=${GATES}@server:\*$" "${out}"
expect "dra: scheduler gates"  "--k3s-arg=--kube-scheduler-arg=feature-gates=${GATES}@server:\*$" "${out}"
expect "dra: controller gates" "--k3s-arg=--kube-controller-manager-arg=feature-gates=${GATES}@server:\*$" "${out}"
expect "dra: kubelet gates"    "--k3s-arg=--kubelet-arg=feature-gates=${GATES}@server:\*;agent:\*$" "${out}"
expect "dra: label"            "--k3s-node-label=experimental.k3d-gpu.io/dra=true@server:\*;agent:\*" "${out}"
expect "dra: no ConfigMap"     "apply -f -" "${out}" negate
expect "dra: exit 0"           "^exit=0$" "${out}"

out=$(SCRIPT="${K3D_GPU_134}" run "${BARE}" image-volume,dra up)
expect "image-volume: merged into one gate list" "--kube-apiserver-arg=feature-gates=ImageVolume=true,${GATES}@" "${out}"
expect_count "image-volume: one apiserver arg" "kube-apiserver-arg=" 1 "${out}"
expect "image-volume: label" "experimental.k3d-gpu.io/image-volume=true" "${out}"

out=$(VENDORS=npu run "${NPU}" npu-turbo up)
expect "npu-turbo: ConfigMap applied" "^kubectl apply -f -$" "${out}"
expect "npu-turbo: NPU.json" "NPU.json: '\{\"NPU_TURBO\": \"true\"\}'" "${out}"
expect "npu-turbo: no CPU/GPU json" "(CPU|GPU).json" "${out}" negate
expect "npu-turbo: no feature gates" "feature-gates" "${out}" negate
expect "npu-turbo: sched label" "--k3s-node-label=k3d-gpu.io/npu-sched=OS@" "${out}"
expect "npu-turbo: HWS host step" "run: +echo 'options intel_vpu sched_mode=1'" "${out}"
expect "npu-turbo: HWS revert" "revert: sudo rm /etc/modprobe.d/k3d-gpu-npu-hws.conf" "${out}"

out=$(run "${SCX}" scx up)
expect "scx: state label" "--k3s-node-label=k3d-gpu.io/sched-ext=disabled@" "${out}"
expect "scx: pinned scx release" "sched-ext/scx v1.1.4" "${out}"
expect "scx: never run" "host steps below are NOT run by k3d-gpu" "${out}"
expect "scx: revert" "revert: stop scx_lavd" "${out}"

out=$(run "${FP}" xe-force-probe up)
expect "xe-force-probe: modprobe.d snippet" "options xe force_probe=%s.*'d750' '!?d750'" "${out}"
expect "xe-force-probe: i915 blocked" "options i915 force_probe=!%s" "${out}"
expect "xe-force-probe: revert" "revert: sudo rm /etc/modprobe.d/k3d-gpu-xe-force-probe.conf && sudo reboot" "${out}"
expect "xe-force-probe: no feature gates" "feature-gates" "${out}" negate

out=$(VENDORS=intel run "${SRIOV}" xe-sriov up)
expect "xe-sriov: totalvfs label" "--k3s-node-label=k3d-gpu.io/xe-sriov-totalvfs=2@" "${out}"
expect "xe-sriov: enable step" "run: +echo N \| sudo tee /sys/bus/pci/devices/0000:03:00.0/sriov_numvfs" "${out}"
expect "xe-sriov: revert step" "revert: echo 0 \| sudo tee /sys/bus/pci/devices/0000:03:00.0/sriov_numvfs" "${out}"
echo 2 > "${SRIOV}/sys/devices/pci0000:00/0000:03:00.0/sriov_numvfs"
out=$(VENDORS=intel run "${SRIOV}" xe-sriov up)
expect "xe-sriov: VFs already on" "already has 2 of 2 VFs; nothing to do" "${out}"
echo 0 > "${SRIOV}/sys/devices/pci0000:00/0000:03:00.0/sriov_numvfs"

# The NPU scheduler label comes only from an accel node bound to intel_vpu, and
# only for the two values ivpu_sysfs.c prints.
NPU2=$(newroot npu2)
cp "${BARE}/proc/cpuinfo" "${NPU2}/proc/cpuinfo"
pci "${NPU2}" 0000:00:0a.0 0x1da3 0x1020 0x120000 habanalabs
accel "${NPU2}" 0000:00:0a.0 0
echo OS > "${NPU2}/sys/devices/pci0000:00/0000:00:0a.0/sched_mode"
pci "${NPU2}" 0000:00:0b.0 0x8086 0xb03e 0x120000 intel_vpu
accel "${NPU2}" 0000:00:0b.0 1
echo HW > "${NPU2}/sys/devices/pci0000:00/0000:00:0b.0/sched_mode"
out=$(VENDORS=npu run "${NPU2}" npu-turbo up)
expect "npu-turbo: label from intel_vpu node only" "--k3s-node-label=k3d-gpu.io/npu-sched=HW@" "${out}"
expect "npu-turbo: HW scheduler, no host step" "options intel_vpu sched_mode=1" "${out}" negate
echo bogus > "${NPU2}/sys/devices/pci0000:00/0000:00:0b.0/sched_mode"
out=$(VENDORS=npu run "${NPU2}" npu-turbo up)
expect "npu-turbo: unknown sched_mode, no label" "k3d-gpu.io/npu-sched=" "${out}" negate

# xe-sriov needs the xe driver and a non-zero sriov_totalvfs. An Alder/Raptor
# Lake iGPU on i915 reports sriov_totalvfs=7 and must not count.
I915=$(newroot i915sriov)
cp "${BARE}/proc/cpuinfo" "${I915}/proc/cpuinfo"
pci "${I915}" 0000:00:02.0 0x8086 0xa780 0x030000 i915
render "${I915}" 0000:00:02.0 128
echo 7 > "${I915}/sys/devices/pci0000:00/0000:00:02.0/sriov_totalvfs"
echo 0 > "${I915}/sys/devices/pci0000:00/0000:00:02.0/sriov_numvfs"
expect "xe-sriov: i915 PF does not count" "no Intel GPU bound to xe reports SR-IOV" "$(VENDORS=intel run "${I915}" xe-sriov up)"
echo 0 > "${SRIOV}/sys/devices/pci0000:00/0000:03:00.0/sriov_totalvfs"
expect "xe-sriov: totalvfs 0 does not count" "no Intel GPU bound to xe reports SR-IOV" "$(VENDORS=intel run "${SRIOV}" xe-sriov up)"
echo 2 > "${SRIOV}/sys/devices/pci0000:00/0000:03:00.0/sriov_totalvfs"

# ---- per feature: unmet -> exact reason, nothing created -------------------------

for f in npu-turbo scx xe-force-probe xe-sriov; do
    out=$(run "${BARE}" "${f}" up)
    expect "${f}: unmet fails up" "ERROR:.*experimental feature '${f}' cannot be enabled: " "${out}"
    expect "${f}: unmet creates nothing" "^k3d cluster create" "${out}" negate
done
expect "npu-turbo: reason" "no Intel NPU accel node bound to intel_vpu" "$(run "${BARE}" npu-turbo up)"
expect "scx: reason names kernel" "kernel 6.18.0 has no /sys/kernel/sched_ext/state" "$(run "${BARE}" scx up)"
expect "xe-force-probe: reason" "no unbound Intel GPU" "$(run "${BARE}" xe-force-probe up)"
expect "xe-sriov: reason" "no Intel GPU bound to xe reports SR-IOV" "$(run "${BARE}" xe-sriov up)"

# ---- framework rules ---------------------------------------------------------------

# Auto-adapt: unset (or "auto") turns on every feature that is detected, mature
# (beta or GA in the shipped k3s) and needs no host change. "off" turns all off.
out=$(run "${BARE}" "" up)
expect "auto: image-volume label" "experimental.k3d-gpu.io/image-volume=true@" "${out}"
expect "auto: dra on (beta default-on in 1.37)" "experimental.k3d-gpu.io/dra=true@" "${out}"
expect "auto: no gate passed on 1.37" "feature-gates" "${out}" negate
out=$(SCRIPT="${K3D_GPU_134}" run "${BARE}" "" up)
expect "auto 1.34: beta ImageVolume gate on" "kube-apiserver-arg=feature-gates=ImageVolume=true@" "${out}"
expect "auto 1.34: alpha DRA stays off" "DRAPartitionableDevices|experimental.k3d-gpu.io/dra" "${out}" negate
out=$(run "${BARE}" "" up)
expect "auto: no NPU, no ConfigMap" "apply -f -" "${out}" negate
expect "auto: host-change features never auto" "experimental.k3d-gpu.io/(scx|xe-force-probe|xe-sriov)" "${out}" negate
expect "auto: no host steps on a bare host" "NOT run by k3d-gpu" "${out}" negate
expect "auto: explicit 'auto' matches unset" "experimental.k3d-gpu.io/image-volume=true@" "$(run "${BARE}" auto up)"
out=$(run "${NPU}" "" up)
expect "auto: NPU host gets npu-turbo" "experimental.k3d-gpu.io/npu-turbo=true@" "${out}"
out=$(run "${BARE}" off up)
expect "off: no gates"  "feature-gates" "${out}" negate
expect "off: no labels" "experimental.k3d-gpu.io" "${out}" negate
expect "off: no ConfigMap" "apply -f -" "${out}" negate
out=$(SCRIPT="${K3D_GPU_134}" run "${BARE}" dra up)
expect "explicit list overrides auto: dra on" "DRAPartitionableDevices=true" "${out}"
expect "explicit list overrides auto: image-volume not added" "experimental.k3d-gpu.io/image-volume" "${out}" negate
expect "off cannot mix" "cannot be combined" "$(run "${BARE}" off,dra detect)"

out=$(run "${BARE}" dra,nope detect)
expect "unknown name fails" "ERROR: unknown experimental feature 'nope'" "${out}"
expect "unknown name exit 1" "^exit=1$" "${out}"
out=$(run "${BARE}" dra,nope up)
expect "unknown name fails before create" "^k3d cluster create" "${out}" negate
expect "list cannot mix" "cannot be combined with feature names" "$(run "${BARE}" list,dra detect)"
expect "whitespace ignored" "kube-apiserver-arg=feature-gates=ImageVolume=true@" "$(SCRIPT="${K3D_GPU_134}" run "${BARE}" ' image-volume , ' up)"
expect_count "duplicates collapse" "experimental.k3d-gpu.io/dra=true@" 1 "$(run "${BARE}" dra,dra up)"

out=$(run "${BARE}" list up)
expect "list: exit 0" "^exit=0$" "${out}"
expect "list: creates nothing" "^k3d " "${out}" negate
expect "list: every feature" "^xe-sriov: absent — no Intel GPU bound to xe" "${out}"

out=$(run "${NPU}" "" doctor --experimental)
expect "doctor --experimental: auto-enabled is marked" "ok: npu-turbo available \\(auto\\)$" "${out}"
expect "doctor --experimental: unavailable is info" "info: scx absent: kernel" "${out}"
out=$(run "${BARE}" off doctor)
expect "doctor: section hidden when off" "Experimental features" "${out}" negate
out=$(run "${BARE}" scx doctor)
expect "doctor: requested unmet fails" "fail.*scx requested but kernel" "${out}"
expect "doctor: requested unmet exit 1" "^exit=1$" "${out}"

# ---- every sim platform: list golden --------------------------------------------------

for fx in "${TESTS}"/fixtures/*/sysroot; do
    name=$(basename "$(dirname "${fx}")")
    golden="$(dirname "${fx}")/experimental.golden"
    got=$(env -i HOME="${WORK}" PATH="${STUB_BIN}:/usr/bin:/bin" K3D_GPU_SYSROOT="${fx}" \
        K3D_GPU_EXPERIMENTAL=list bash "${K3D_GPU}" detect 2>&1)
    if [ "${UPDATE}" -eq 1 ]; then
        printf '%s\n' "${got}" > "${golden}"; echo "wrote ${golden#"${REPO}"/}"
    elif [ -f "${golden}" ] && diff -u "${golden}" <(printf '%s\n' "${got}") > "${WORK}/diff"; then
        PASS=$((PASS + 1)); echo "ok   ${name}: experimental list"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name}: experimental list"
        if [ -f "${golden}" ]; then sed 's/^/  | /' "${WORK}/diff"; else echo "  | missing golden; run with --update"; fi
    fi
done

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
