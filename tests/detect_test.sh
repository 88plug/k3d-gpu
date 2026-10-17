#!/usr/bin/env bash
# Host accelerator detection tests. Each case builds a fake sysfs/devfs tree and
# runs `k3d-gpu detect` against it via K3D_GPU_SYSROOT. No cluster, no hardware.
#
# Usage: tests/detect_test.sh

set -uo pipefail

K3D_GPU="$(cd "$(dirname "$0")/.." && pwd)/scripts/k3d-gpu"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

# pci <root> <bdf> <vendor> <device> [driver] [class]: a PCI function, optionally bound.
pci() {
    local root=$1 bdf=$2 vendor=$3 device=$4 driver=${5:-} class=${6:-0x030000}
    local dir="${root}/sys/devices/pci0000:00/${bdf}"
    mkdir -p "${dir}" "${root}/sys/bus/pci/devices"
    echo "${vendor}" > "${dir}/vendor"
    echo "${device}" > "${dir}/device"
    echo "${class}" > "${dir}/class"
    ln -sfn "${dir}" "${root}/sys/bus/pci/devices/${bdf}"
    if [ -n "${driver}" ]; then
        mkdir -p "${root}/sys/bus/pci/drivers/${driver}"
        ln -sfn "${root}/sys/bus/pci/drivers/${driver}" "${dir}/driver"
    fi
}

# render <root> <bdf> <n>: DRM render node renderD<n> backed by PCI <bdf>.
render() {
    mkdir -p "$1/sys/class/drm/renderD$3" "$1/dev/dri"
    ln -sfn "$1/sys/devices/pci0000:00/$2" "$1/sys/class/drm/renderD$3/device"
    touch "$1/dev/dri/renderD$3"
}

# accel <root> <bdf> <n>: compute accel node accel<n> backed by PCI <bdf>.
accel() {
    mkdir -p "$1/sys/class/accel/accel$3" "$1/dev/accel"
    ln -sfn "$1/sys/devices/pci0000:00/$2" "$1/sys/class/accel/accel$3/device"
    touch "$1/dev/accel/accel$3"
}

newroot() { local r="${WORK}/$1"; mkdir -p "${r}/sys" "${r}/dev"; echo "${r}"; }

# kernel <root> <release>: the kernel release doctor reads.
kernel() { mkdir -p "$1/proc/sys/kernel"; echo "$2" > "$1/proc/sys/kernel/osrelease"; }

# firmware <root> <relpath>: a firmware file under lib/firmware.
firmware() { mkdir -p "$(dirname "$1/lib/firmware/$2")"; touch "$1/lib/firmware/$2"; }

# doctor_says <name> <root> <ERE> [negate] [env assignments...]: doctor output (any exit code) must
# match <ERE>, or must not when a 4th argument is given.
doctor_says() {
    local name=$1 root=$2 re=$3 negate=${4:-} out hit=0
    shift 4 2>/dev/null || shift $#
    out=$(env K3D_GPU_SYSROOT="${root}" PATH="${STUB_BIN}:${PATH}" "$@" bash "${K3D_GPU}" doctor 2>&1)
    grep -E -- "${re}" <<< "${out}" >/dev/null && hit=1
    if { [ -z "${negate}" ] && [ "${hit}" -eq 1 ]; } || { [ -n "${negate}" ] && [ "${hit}" -eq 0 ]; }; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (${negate:+not }matching /${re}/)"
        sed 's/^/  | /' <<< "${out}"
    fi
}

# doctor shells out to docker/k3d/kubectl; stub them so output is host-independent.
STUB_BIN="${WORK}/bin"
mkdir -p "${STUB_BIN}"
printf '#!/bin/sh\nexit 0\n' > "${STUB_BIN}/k3d"; chmod +x "${STUB_BIN}/k3d"
# The kubectl stub stands in for a live cluster, driven by env. With everything
# unset there is no reachable cluster and doctor runs only its host checks.
#   STUB_NODES         node names                      get nodes -o name
#   STUB_HYBRID_NODES  nodes carrying the hybrid label get nodes -l ...
#   STUB_DS_<name>     "<desired> <ready>" of DaemonSet <name> (- as _); unset = NotFound
#   STUB_DS_LIST       namespace/name per DaemonSet    get daemonsets -A
#   STUB_NODE_RES      "<node> <resource> <capacity> <allocatable>" lines, \n separated
cat > "${STUB_BIN}/kubectl" <<'EOF'
#!/bin/bash
args=()
for a in "$@"; do case "${a}" in --context=*|--request-timeout=*) ;; *) args+=("${a}") ;; esac; done
set -- "${args[@]}"
[ "$1" = --context ] && shift 2
[ "$1" = -n ] && shift 2
[ "$1" = get ] || exit 0
case "$2 $*" in
    "nodes "*" -l "*) for n in ${STUB_HYBRID_NODES:-}; do echo "node/${n}"; done ;;
    "nodes "*go-template*) printf '%b' "${STUB_NODE_RES:-}" ;;
    "nodes "*) for n in ${STUB_NODES:-}; do echo "node/${n}"; done ;;
    "daemonsets "*) printf '%b' "${STUB_DS_LIST:-}" ;;
    "daemonset "*) v="STUB_DS_$(echo "$3" | tr -- - _)"
        [ -n "${!v+x}" ] || { echo "Error from server (NotFound): daemonsets.apps \"$3\" not found" >&2; exit 1; }
        echo "${!v}" ;;
esac
exit 0
EOF
chmod +x "${STUB_BIN}/kubectl"
# The docker stub answers `docker info --format` from STUB_DOCKER_RUNTIMES (runtime
# names) and STUB_DOCKER_CDI (CDI device IDs), so doctor's runtime checks are testable.
# `docker exec <node> test -S /var/run/nri/nri.sock` fails when STUB_NRI_SOCK_MISSING is set.
cat > "${STUB_BIN}/docker" <<'EOF'
#!/bin/sh
[ "$1" = info ] || [ "$1" = exec ] || exit 0
case "$*" in
    exec*nri.sock*) [ -z "${STUB_NRI_SOCK_MISSING:-}" ] ;;
    exec*) ;;
    *Runtimes*) for r in ${STUB_DOCKER_RUNTIMES:-}; do echo "${r}"; done ;;
    *DiscoveredDevices*) for id in ${STUB_DOCKER_CDI:-}; do echo "${id}"; done ;;
    *Driver*) echo "${STUB_DOCKER_DRIVER:-}" ;;
    *--format*) ;;
    *)  # plain `docker info`, shaped like Docker 29's
        echo " Runtimes: ${STUB_DOCKER_RUNTIMES:-}"
        for id in ${STUB_DOCKER_CDI:-}; do echo "  cdi: ${id}"; done ;;
esac
EOF
chmod +x "${STUB_BIN}/docker"

# expect <name> <root> <expected stdout> [env assignments...]
expect() {
    local name=$1 root=$2 want=$3 got rc
    shift 3
    got=$(env K3D_GPU_SYSROOT="${root}" "$@" bash "${K3D_GPU}" detect 2>/dev/null)
    rc=$?
    if [ "${rc}" -eq 0 ] && [ "${got}" = "${want}" ]; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (rc=${rc})"
        echo "  want: $(printf '%q' "${want}")"
        echo "  got:  $(printf '%q' "${got}")"
    fi
}

# expect_fail <name> <root> [env assignments...]: detect must exit non-zero.
expect_fail() {
    local name=$1 root=$2
    shift 2
    if env K3D_GPU_SYSROOT="${root}" "$@" bash "${K3D_GPU}" detect >/dev/null 2>&1; then
        FAILS=$((FAILS + 1)); echo "FAIL ${name} (expected non-zero exit)"
    else
        PASS=$((PASS + 1)); echo "ok   ${name}"
    fi
}

# ---- cases --------------------------------------------------------------------

r=$(newroot empty)
expect "empty host detects nothing" "${r}" ""

r=$(newroot amd)
pci "${r}" 0000:03:00.0 0x1002 0x744c amdgpu; render "${r}" 0000:03:00.0 128
expect "AMD render node is ignored" "${r}" ""

r=$(newroot lunarlake)
pci "${r}" 0000:00:02.0 0x8086 0x64a0 xe;        render "${r}" 0000:00:02.0 128
pci "${r}" 0000:00:0b.0 0x8086 0x643e intel_vpu; accel  "${r}" 0000:00:0b.0 0
expect "Lunar Lake: Xe2 iGPU + NPU, NPU is not Gaudi" "${r}" \
"intel gpu.intel.com/xe
npu npu.intel.com/accel"

r=$(newroot meteorlake)
pci "${r}" 0000:00:02.0 0x8086 0x7d55 i915;      render "${r}" 0000:00:02.0 128
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d intel_vpu; accel  "${r}" 0000:00:0b.0 0
expect "Meteor Lake: i915 iGPU + NPU" "${r}" \
"intel gpu.intel.com/i915
npu npu.intel.com/accel"

r=$(newroot mixed-intel)
pci "${r}" 0000:00:02.0 0x8086 0x7d55 i915; render "${r}" 0000:00:02.0 128
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe;   render "${r}" 0000:03:00.0 129
expect "Battlemage dGPU (xe) + i915 iGPU expose both resources" "${r}" \
"intel gpu.intel.com/i915 gpu.intel.com/xe"

r=$(newroot npu-unbound)
pci "${r}" 0000:00:0b.0 0x8086 0x643e
expect "NPU PCI function with no driver is not detected" "${r}" ""

r=$(newroot gaudi)
mkdir -p "${r}/sys/module/habanalabs"
pci "${r}" 0000:4d:00.0 0x1da3 0x1060 habanalabs; accel "${r}" 0000:4d:00.0 0
pci "${r}" 0000:4e:00.0 0x1da3 0x1060 habanalabs; accel "${r}" 0000:4e:00.0 1
expect "Gaudi 3 via habanalabs accel nodes" "${r}" "gaudi habana.ai/gaudi"

r=$(newroot gaudi-no-driver)
pci "${r}" 0000:4d:00.0 0x1da3 0x1060
expect "Gaudi PCI device without driver is skipped" "${r}" ""

r=$(newroot gaudi-plus-npu)
pci "${r}" 0000:4d:00.0 0x1da3 0x1060 habanalabs; accel "${r}" 0000:4d:00.0 0
pci "${r}" 0000:00:0b.0 0x8086 0xb03e intel_vpu;  accel "${r}" 0000:00:0b.0 1
expect "Gaudi and NPU on one host are told apart" "${r}" \
"npu npu.intel.com/accel
gaudi habana.ai/gaudi"

r=$(newroot nvidia)
mkdir -p "${r}/sys/module/nvidia"
expect "NVIDIA via loaded module" "${r}" "nvidia nvidia.com/gpu"

r=$(newroot explicit)
expect "K3D_GPU_VENDORS is trimmed, deduped and ordered as given" "${r}" \
"npu npu.intel.com/accel
intel gpu.intel.com/i915 gpu.intel.com/xe" K3D_GPU_VENDORS=" npu, intel ,npu"

expect_fail "unknown vendor in K3D_GPU_VENDORS is rejected" "${r}" K3D_GPU_VENDORS="intel,tpu"

# xeon <root>: Sapphire Rapids-style host with AMX flags, a DSA and an IAA device
# with one user work queue each, and a 4xxx QAT PF with one VF.
xeon() {
    local r=$1
    mkdir -p "${r}/proc" "${r}/sys/bus/dsa/devices/dsa0" "${r}/sys/bus/dsa/devices/iax1" "${r}/dev/dsa" "${r}/dev/iax"
    printf 'processor\t: 0\nflags\t\t: fpu avx512f amx_bf16 amx_tile amx_int8\n' > "${r}/proc/cpuinfo"
    touch "${r}/dev/dsa/wq0.0" "${r}/dev/iax/wq1.0"
    pci "${r}" 0000:6b:00.0 0x8086 0x4940 4xxx 0x0b4000
    pci "${r}" 0000:6b:00.1 0x8086 0x4941 4xxxvf 0x0b4000
    ln -sfn "${r}/sys/devices/pci0000:00/0000:6b:00.1" "${r}/sys/devices/pci0000:00/0000:6b:00.0/virtfn0"
}

r=$(newroot xeon-ready)
xeon "${r}"
expect "Xeon: DSA/IAA work queues, QAT VFs and AMX are detected" "${r}" \
"dsa dsa.intel.com/*
iaa iaa.intel.com/*
qat qat.intel.com/*
amx feature.node.kubernetes.io/cpu-cpuid.AMXBF16=true feature.node.kubernetes.io/cpu-cpuid.AMXINT8=true feature.node.kubernetes.io/cpu-cpuid.AMXTILE=true"

r=$(newroot xeon-unconfigured)
mkdir -p "${r}/sys/bus/dsa/devices/dsa0" "${r}/sys/bus/dsa/devices/iax1"
pci "${r}" 0000:6b:00.0 0x8086 0x4940 4xxx 0x0b4000
expect "DSA/IAA without work queues and QAT without VFs are not active" "${r}" ""

r=$(newroot qat-unbound)
pci "${r}" 0000:6b:00.0 0x8086 0x4940 "" 0x0b4000
pci "${r}" 0000:6b:00.1 0x8086 0x4941 "" 0x0b4000
ln -sfn "${r}/sys/devices/pci0000:00/0000:6b:00.1" "${r}/sys/devices/pci0000:00/0000:6b:00.0/virtfn0"
expect "QAT PF with no driver is not active" "${r}" ""

r=$(newroot qat-c62x)
pci "${r}" 0000:3d:00.0 0x8086 0x37c8 c6xx 0x0b4000
pci "${r}" 0000:3d:01.0 0x8086 0x37c9 c6xxvf 0x0b4000
ln -sfn "${r}/sys/devices/pci0000:00/0000:3d:01.0" "${r}/sys/devices/pci0000:00/0000:3d:00.0/virtfn0"
expect "C62x QAT PF with VFs is active" "${r}" "qat qat.intel.com/*"

r=$(newroot amd-epyc)
mkdir -p "${r}/proc"
printf 'processor\t: 0\nflags\t\t: fpu avx512f avx512_bf16\n' > "${r}/proc/cpuinfo"
expect "CPU without AMX flags is not amx" "${r}" ""

# ---- doctor diagnostics -------------------------------------------------------

r=$(newroot cri-unbound)
kernel "${r}" 7.2.9
pci "${r}" 0000:17:00.0 0x8086 0x674c "" 0x120000
doctor_says "unbound Crescent Island gets an xe.force_probe hint" "${r}" 'xe\.force_probe=674c'

r=$(newroot nvlp-unbound)
kernel "${r}" 7.2.9
pci "${r}" 0000:00:02.0 0x8086 0xd750 "" 0x030000
doctor_says "unbound Nova Lake-P iGPU gets an xe.force_probe hint" "${r}" 'xe\.force_probe=d750'

r=$(newroot npu-unbound-doc)
kernel "${r}" 6.12.10
pci "${r}" 0000:00:0b.0 0x8086 0xb03e "" 0x120000
doctor_says "unbound Panther Lake NPU points at intel_vpu and kernel 6.13" "${r}" 'intel_vpu.*6\.13'
doctor_says "unbound NPU is not given a GPU force_probe hint" "${r}" 'force_probe' negate

r=$(newroot ptl-old-kernel)
kernel "${r}" 6.16.3
pci "${r}" 0000:00:02.0 0x8086 0xb080 xe; render "${r}" 0000:00:02.0 128
doctor_says "Panther Lake iGPU on 6.16 warns about kernel 6.17" "${r}" 'Panther Lake.*6\.17'

r=$(newroot lnl-ok)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:02.0 0x8086 0x64a0 xe;                 render "${r}" 0000:00:02.0 128
pci "${r}" 0000:00:0b.0 0x8086 0x643e intel_vpu "" ;      accel  "${r}" 0000:00:0b.0 0
firmware "${r}" xe/lnl_guc_70.bin
firmware "${r}" intel/vpu/vpu_40xx_v1.bin
doctor_says "Lunar Lake on 6.18 with firmware has no kernel warning" "${r}" 'needs [0-9]' negate
doctor_says "Lunar Lake NPU firmware is found" "${r}" 'ok: .*vpu_40xx'

r=$(newroot npu-no-fw)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d intel_vpu; accel "${r}" 0000:00:0b.0 0
doctor_says "Meteor Lake NPU without firmware warns about vpu_37xx" "${r}" 'warn.*intel/vpu/vpu_37xx'

r=$(newroot xeon)
kernel "${r}" 6.18.1
mkdir -p "${r}/proc"
printf 'processor\t: 0\nflags\t\t: fpu sse2 avx512f amx_bf16 amx_tile amx_int8\n' > "${r}/proc/cpuinfo"
mkdir -p "${r}/sys/bus/dsa/devices/dsa0" "${r}/sys/bus/dsa/devices/iax1"
pci "${r}" 0000:6b:00.0 0x8086 0x4940 4xxx 0x0b4000
echo 0 > "${r}/sys/devices/pci0000:00/0000:6b:00.0/sriov_numvfs"
doctor_says "Xeon AMX flags are reported" "${r}" 'AMX.*amx_bf16.*amx_int8.*amx_tile'
doctor_says "DSA without work queues points at accel-config" "${r}" 'DSA.*accel-config'
doctor_says "IAA without work queues points at accel-config" "${r}" 'IAA.*accel-config'
doctor_says "QAT PF with no VFs points at sriov_numvfs" "${r}" 'QAT.*sriov_numvfs'

r=$(newroot ptl-rc-kernel)
kernel "${r}" 6.16-rc3
pci "${r}" 0000:00:02.0 0x8086 0xb080 xe; render "${r}" 0000:00:02.0 128
doctor_says "release-candidate kernel 6.16-rc3 still warns for Panther Lake" "${r}" 'Panther Lake.*6\.17'

r=$(newroot bmg-old-kernel)
kernel "${r}" 6.9-rc3
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe; render "${r}" 0000:03:00.0 128
doctor_says "6.9-rc3 is older than 6.12 for Battlemage" "${r}" 'Battlemage.*6\.12'

r=$(newroot not-bmg)
kernel "${r}" 6.11.2
pci "${r}" 0000:03:00.0 0x8086 0xe214 xe; render "${r}" 0000:03:00.0 128
doctor_says "0xe214 is not a Battlemage id in the kernel" "${r}" 'Intel Battlemage \(' negate

r=$(newroot arl-s)
kernel "${r}" 6.10.4
pci "${r}" 0000:00:02.0 0x8086 0xb640 i915; render "${r}" 0000:00:02.0 128
doctor_says "Arrow Lake-S 0xb640 needs kernel 6.12" "${r}" 'Arrow Lake.*6\.12'

# cpuinfo <root> <family> <model> <flags>: the first CPU's family, model and flags.
cpuinfo() {
    mkdir -p "$1/proc"
    printf 'processor\t: 0\ncpu family\t: %s\nmodel\t\t: %s\nmodel name\t: Fake CPU\nflags\t\t: %s\n' "$2" "$3" "$4" > "$1/proc/cpuinfo"
}

# engine <root> <bdf> <card> <engine> [capabilities]: an i915 engine dir under
# /sys/class/drm/<card>/engine (drivers/gpu/drm/i915/gt/sysfs_engines.c).
engine() {
    local d="$1/sys/devices/pci0000:00/$2/drm/$3"
    mkdir -p "${d}/engine/$4"
    ln -sfn "$1/sys/devices/pci0000:00/$2" "${d}/device"
    ln -sfn "${d}" "$1/sys/class/drm/$3"
    printf '%s' "${5:+$5$'\n'}" > "${d}/engine/$4/capabilities"
}

# Alder Lake / Raptor Lake iGPUs on i915: name, default-binding kernel, GuC firmware.
r=$(newroot rpl-s)
kernel "${r}" 6.6.128
pci "${r}" 0000:00:02.0 0x8086 0xa780 i915; render "${r}" 0000:00:02.0 128
firmware "${r}" i915/tgl_guc_70.bin
doctor_says "Raptor Lake-S UHD 770 (0xa780) is named" "${r}" 'ok: Intel Raptor Lake-S \(0xa780\) bound to i915'
doctor_says "Raptor Lake-S checks the tgl GuC firmware" "${r}" 'ok: i915/tgl_guc_\* firmware present'
doctor_says "Raptor Lake-S on 6.6 has no kernel warning" "${r}" 'needs kernel' negate

r=$(newroot adl-p-nofw)
kernel "${r}" 6.8.0
pci "${r}" 0000:00:02.0 0x8086 0x46a6 i915; render "${r}" 0000:00:02.0 128
doctor_says "Alder Lake-P iGPU wants the adlp GuC firmware" "${r}" 'warn.*i915/adlp_guc_\* firmware not found'

r=$(newroot adl-n-old)
kernel "${r}" 6.8.0
pci "${r}" 0000:00:02.0 0x8086 0x46d3 i915; render "${r}" 0000:00:02.0 128
doctor_says "Alder Lake-N 0x46d3 needs kernel 6.9 and names i915.force_probe" "${r}" 'Alder Lake-N \(0x46d3\) needs kernel 6\.9\+.*i915\.force_probe=46d3'
r=$(newroot adl-n)
kernel "${r}" 6.8.0
pci "${r}" 0000:00:02.0 0x8086 0x46d1 i915; render "${r}" 0000:00:02.0 128
doctor_says "Alder Lake-N 0x46d1 is bound by default on 6.8" "${r}" 'needs kernel' negate

r=$(newroot rpl-u-old)
kernel "${r}" 6.6.10
pci "${r}" 0000:00:02.0 0x8086 0xa7ac i915; render "${r}" 0000:00:02.0 128
doctor_says "Raptor Lake-U 0xa7ac needs kernel 6.7" "${r}" 'Raptor Lake-P/U \(0xa7ac\) needs kernel 6\.7\+'

r=$(newroot rpl-unbound)
kernel "${r}" 6.6.128
pci "${r}" 0000:00:02.0 0x8086 0xa780 "" 0x030000
doctor_says "unbound Raptor Lake-S iGPU points at i915" "${r}" 'Raptor Lake-S \(0xa780\) has no driver .*modprobe i915'
doctor_says "unbound i915 part is not told to block i915" "${r}" 'i915\.force_probe=!' negate

r=$(newroot adl-s-old)
kernel "${r}" 5.15.0
pci "${r}" 0000:00:02.0 0x8086 0x4680 i915; render "${r}" 0000:00:02.0 128
doctor_says "Alder Lake-S needs kernel 5.16 (i915 force_probe dropped)" "${r}" 'Alder Lake-S \(0x4680\) needs kernel 5\.16\+'

# Every i915 row's default-binding kernel: one release older warns with
# i915.force_probe, the release itself does not. Kernels: the first release whose
# i915_pciids.h lists the id and whose i915_pci.c does not require force_probe.
while read -r id name older mink; do
    name=${name//_/ }
    r=$(newroot "i915-${id}-old")
    kernel "${r}" "${older}.0"
    pci "${r}" 0000:00:02.0 0x8086 "${id}" i915; render "${r}" 0000:00:02.0 128
    doctor_says "${name} ${id} on ${older} needs kernel ${mink}" "${r}" "${name} \\(${id}\\) needs kernel ${mink//./\\.}\\+.*i915\\.force_probe=${id#0x}"
    r=$(newroot "i915-${id}")
    kernel "${r}" "${mink}.0"
    pci "${r}" 0000:00:02.0 0x8086 "${id}" i915; render "${r}" 0000:00:02.0 128
    doctor_says "${name} ${id} is bound by default on ${mink}" "${r}" 'needs kernel' negate
done <<'ROWS'
0x4680 Alder_Lake-S 5.15 5.16
0x468b Alder_Lake-S 6.0 6.1
0x46a6 Alder_Lake-P 5.16 5.17
0x46d1 Alder_Lake-N 5.17 5.18
0x46d3 Alder_Lake-N 6.8 6.9
0xa780 Raptor_Lake-S 5.16 5.17
0xa78a Raptor_Lake-S 5.18 5.19
0xa7a0 Raptor_Lake-P/U 5.18 5.19
0xa7ac Raptor_Lake-P/U 6.6 6.7
0x7d55 Meteor_Lake 6.6 6.7
0x7d51 Arrow_Lake-H 6.8 6.9
0x7d41 Arrow_Lake-U 6.8 6.9
0x7d67 Arrow_Lake-S 6.6 6.7
ROWS

# NPU 0x7d1d is Meteor Lake and Arrow Lake-H; the CPU model tells them apart.
r=$(newroot npu-7d1d-arlh)
kernel "${r}" 6.14.0
cpuinfo "${r}" 6 197 "fpu avx_vnni"
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d intel_vpu; accel "${r}" 0000:00:0b.0 0
doctor_says "NPU 0x7d1d on an Arrow Lake-H CPU (model 197) is Arrow Lake-H" "${r}" 'ok: Intel NPU Arrow Lake-H \(0x7d1d\)'
doctor_says "NPU generation is a node label" "${r}" 'up labels the node k3d-gpu\.io/npu\.gen=37xx'
r=$(newroot npu-7d1d-mtl)
kernel "${r}" 6.14.0
cpuinfo "${r}" 6 170 "fpu"
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d intel_vpu; accel "${r}" 0000:00:0b.0 0
doctor_says "NPU 0x7d1d on a Meteor Lake CPU (model 170) is Meteor Lake" "${r}" 'ok: Intel NPU Meteor Lake \(0x7d1d\)'
r=$(newroot npu-7d1d-unknown)
kernel "${r}" 6.14.0
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d intel_vpu; accel "${r}" 0000:00:0b.0 0
doctor_says "NPU 0x7d1d with no CPU model names both platforms" "${r}" 'ok: Intel NPU Meteor Lake / Arrow Lake-H \(0x7d1d\)'
r=$(newroot npu-7d1d-other-family)
kernel "${r}" 6.14.0
cpuinfo "${r}" 18 197 "fpu"
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d intel_vpu; accel "${r}" 0000:00:0b.0 0
doctor_says "model 197 outside family 6 is not Arrow Lake-H" "${r}" 'ok: Intel NPU Meteor Lake / Arrow Lake-H \(0x7d1d\)'

# XMX label from the platform: Arrow Lake-H has XMX, Arrow Lake-S does not.
r=$(newroot xmx-arlh)
kernel "${r}" 6.14.0
pci "${r}" 0000:00:02.0 0x8086 0x7d51 i915; render "${r}" 0000:00:02.0 128
doctor_says "Arrow Lake-H iGPU 0x7d51 is named" "${r}" 'ok: Intel Arrow Lake-H \(0x7d51\)'
doctor_says "Arrow Lake-H iGPU gets xmx=true" "${r}" 'up labels the node gpu\.intel\.com/xmx=true'
r=$(newroot xmx-arls)
kernel "${r}" 6.14.0
pci "${r}" 0000:00:02.0 0x8086 0x7d67 i915; render "${r}" 0000:00:02.0 128
doctor_says "Arrow Lake-S iGPU 0x7d67 is named" "${r}" 'ok: Intel Arrow Lake-S \(0x7d67\)'
doctor_says "Arrow Lake-S iGPU gets xmx=false" "${r}" 'up labels the node gpu\.intel\.com/xmx=false'
r=$(newroot xmx-mixed)
kernel "${r}" 6.14.0
pci "${r}" 0000:00:02.0 0x8086 0x7d55 i915; render "${r}" 0000:00:02.0 128
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe;   render "${r}" 0000:03:00.0 129
doctor_says "Meteor Lake iGPU + Battlemage dGPU node has XMX" "${r}" 'gpu\.intel\.com/xmx=true'
r=$(newroot xmx-unknown)
kernel "${r}" 7.3.0
pci "${r}" 0000:17:00.0 0x8086 0x674c xe; render "${r}" 0000:17:00.0 128
doctor_says "a GPU with no XMX entry gets no xmx label" "${r}" 'xmx=' negate

# Media engines from i915's engine/*/capabilities (values from the captured UHD 770).
r=$(newroot media)
kernel "${r}" 6.6.128
pci "${r}" 0000:00:02.0 0x8086 0xa780 i915; render "${r}" 0000:00:02.0 128
engine "${r}" 0000:00:02.0 card0 rcs0
engine "${r}" 0000:00:02.0 card0 vcs0 "hevc sfc"
engine "${r}" 0000:00:02.0 card0 vcs1 "hevc sfc"
engine "${r}" 0000:00:02.0 card0 vecs0 sfc
doctor_says "media engines are listed with their capabilities" "${r}" 'ok: card0 media engines: vcs0 \(hevc sfc\), vcs1 \(hevc sfc\), vecs0 \(sfc\)'
doctor_says "media engine counts are node labels" "${r}" 'gpu\.intel\.com/xmx=false gpu\.intel\.com/media-vcs=2 gpu\.intel\.com/media-vecs=1'
r=$(newroot media-xe)
kernel "${r}" 6.18.1
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe; render "${r}" 0000:03:00.0 128
doctor_says "xe GPU without an engine dir gets no media label" "${r}" 'media-vcs' negate

# CPU ISA labels under NFD's names (klauspost/cpuid feature ids).
r=$(newroot isa)
kernel "${r}" 6.6.128
cpuinfo "${r}" 6 143 "fpu sha_ni user_shstk avx_vnni pku gfni vaes ibt avx512f avx512_vnni avx512_bf16 amx_tile avx_vnni_x AVX_VNNI"
doctor_says "CPU ISA flags are reported under NFD names" "${r}" 'ok: CPU ISA: AVX512BF16 AVX512F AVX512VNNI AVXVNNI CETIBT CETSS GFNI SHA VAES — up labels'
doctor_says "pku has no NFD name and is not labelled" "${r}" 'PKU' negate
r=$(newroot isa-none)
kernel "${r}" 6.6.128
cpuinfo "${r}" 6 143 "fpu sse2"
doctor_says "a CPU with none of the ISA flags prints no ISA line" "${r}" 'CPU ISA' negate

r=$(newroot nvidia-dev)
kernel "${r}" 6.12.10
mkdir -p "${r}/sys/module/nvidia"; touch "${r}/dev/nvidiactl"
doctor_says "doctor sees /dev/nvidiactl under the sysroot" "${r}" 'ok: /dev/nvidia'

r=$(newroot npu-forced)
kernel "${r}" 6.12.10
doctor_says "forced npu with no NPU fails doctor" "${r}" 'fail.*NPU' "" K3D_GPU_VENDORS=npu

r=$(newroot npu-twice)
pci "${r}" 0000:00:0b.0 0x8086 0x643e intel_vpu; accel "${r}" 0000:00:0b.0 0
pci "${r}" 0000:00:0c.0 0x8086 0x643e intel_vpu; accel "${r}" 0000:00:0c.0 1
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe;        render "${r}" 0000:03:00.0 128
pci "${r}" 0000:04:00.0 0x8086 0xe20b xe;        render "${r}" 0000:04:00.0 129
expect "two NPUs and two xe GPUs list each resource once" "${r}" \
"intel gpu.intel.com/xe
npu npu.intel.com/accel"

r=$(newroot dangling)
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe; render "${r}" 0000:03:00.0 128
rm -rf "${r}/sys/bus/pci/drivers/xe"
expect "dangling driver symlink counts as unbound" "${r}" ""

r=$(newroot vfio)
pci "${r}" 0000:03:00.0 0x8086 0xe20b vfio-pci; render "${r}" 0000:03:00.0 128
expect "Intel GPU bound to vfio-pci is not an xe/i915 GPU" "${r}" ""

r=$(newroot npu-unbound-accel)
pci "${r}" 0000:00:0b.0 0x8086 0x643e; accel "${r}" 0000:00:0b.0 0
expect "accel node with no driver is not an NPU" "${r}" ""

r=$(newroot empty-items)
mkdir -p "${r}/sys/module/nvidia"
expect "empty items in K3D_GPU_VENDORS are skipped" "${r}" \
"nvidia nvidia.com/gpu
gaudi habana.ai/gaudi" K3D_GPU_VENDORS="nvidia,,gaudi"

r=$(newroot fw-zst)
kernel "${r}" 6.18.1
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe; render "${r}" 0000:03:00.0 128
mkdir -p "${r}/usr/lib/firmware/xe"; touch "${r}/usr/lib/firmware/xe/bmg_guc_70.bin.zst"
doctor_says "compressed firmware under usr/lib/firmware is found" "${r}" 'ok: xe/bmg_guc_'

r=$(newroot xeon-doc)
kernel "${r}" 6.18.1
xeon "${r}"
doctor_says "ready DSA is reported as deployed by up" "${r}" 'ok: DSA: .*deployed by .k3d-gpu up.'
doctor_says "ready QAT is reported as deployed by up" "${r}" 'ok: QAT .*1 VF.*deployed by .k3d-gpu up.'
doctor_says "QAT without vfio-pci loaded warns" "${r}" 'warn.*vfio-pci'
mkdir -p "${r}/sys/bus/pci/drivers/vfio-pci"
doctor_says "QAT with vfio-pci loaded does not warn about it" "${r}" 'warn.*vfio-pci' negate

r=$(newroot dsa-forced)
kernel "${r}" 6.18.1
doctor_says "forced dsa with no work queues fails doctor" "${r}" 'fail.*DSA' "" K3D_GPU_VENDORS=dsa

r=$(newroot gaudi-doc)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:0b.0 0x8086 0x643e intel_vpu; accel "${r}" 0000:00:0b.0 0
doctor_says "an NPU accel node does not satisfy the Gaudi check" "${r}" 'Checking Intel Gaudi' negate

# hybrid <root> <P cpus> <E cpus>: Intel hybrid CPU sysfs (Alder Lake and newer).
hybrid() {
    mkdir -p "$1/sys/devices/cpu_core" "$1/sys/devices/cpu_atom"
    echo "$2" > "$1/sys/devices/cpu_core/cpus"
    echo "$3" > "$1/sys/devices/cpu_atom/cpus"
}

r=$(newroot hybrid)
hybrid "${r}" 0-15 16-31
expect "hybrid P/E CPU is detected" "${r}" "hybrid k3d-gpu.io/hybrid-cpu=true"

r=$(newroot hybrid-no-ecores)
hybrid "${r}" 0-15 ""
expect "hybrid PMU with no online E-cores is not hybrid" "${r}" ""

r=$(newroot not-hybrid)
mkdir -p "${r}/sys/devices/cpu"
expect "non-hybrid CPU is not hybrid" "${r}" ""

r=$(newroot hybrid-forced)
expect "forced hybrid is accepted" "${r}" "hybrid k3d-gpu.io/hybrid-cpu=true" K3D_GPU_VENDORS=hybrid

r=$(newroot hybrid-doc)
kernel "${r}" 6.18.1
hybrid "${r}" 0-15 16-31
doctor_says "doctor reports P-core and E-core CPU lists" "${r}" 'ok: hybrid CPU: P-cores 0-15, E-cores 16-31'
r=$(newroot hybrid-doc-forced)
kernel "${r}" 6.18.1
doctor_says "forced hybrid on a non-hybrid CPU fails doctor" "${r}" 'fail.*hybrid' "" K3D_GPU_VENDORS=hybrid

# CPU topology sysfs (Documentation/ABI/stable/sysfs-devices-system-cpu,
# Documentation/ABI/testing/sysfs-devices-system-cpu).
# cpucache <root> <cpu> <level> <shared_cpu_list>: cpu<cpu>/cache/index<level>.
cpucache() {
    local d="$1/sys/devices/system/cpu/cpu$2/cache/index$3"
    mkdir -p "${d}"; echo "$3" > "${d}/level"; echo "$4" > "${d}/shared_cpu_list"
}
# corecpus <root> <cpu> <core_cpus_list>; cppc <root> <cpu> <highest_perf>.
corecpus() { mkdir -p "$1/sys/devices/system/cpu/cpu$2/topology"; echo "$3" > "$1/sys/devices/system/cpu/cpu$2/topology/core_cpus_list"; }
cppc() { mkdir -p "$1/sys/devices/system/cpu/cpu$2/acpi_cppc"; echo "$3" > "$1/sys/devices/system/cpu/cpu$2/acpi_cppc/highest_perf"; }

# Arrow Lake-H: LP E-cores in a third PMU, cpu_lowpower (events/intel/core.c, 6.13+).
r=$(newroot arl-h)
kernel "${r}" 6.18.1
hybrid "${r}" 0-5 6-13
mkdir -p "${r}/sys/devices/cpu_lowpower"; echo 14-15 > "${r}/sys/devices/cpu_lowpower/cpus"
expect "P + E + cpu_lowpower is hybrid" "${r}" "hybrid k3d-gpu.io/hybrid-cpu=true"
doctor_says "doctor lists cpu_lowpower CPUs as LP E-cores" "${r}" 'ok: hybrid CPU: P-cores 0-5, E-cores 6-13, LP E-cores 14-15 '
r=$(newroot p-and-lp-only)
hybrid "${r}" 0-5 ""
mkdir -p "${r}/sys/devices/cpu_lowpower"; echo 6-7 > "${r}/sys/devices/cpu_lowpower/cpus"
expect "P-cores plus LP E-cores only is hybrid" "${r}" "hybrid k3d-gpu.io/hybrid-cpu=true"

# Meteor Lake / Panther Lake: LP E-cores sit in cpu_atom but have no L3.
r=$(newroot mtl)
kernel "${r}" 6.18.1
hybrid "${r}" 0-11 12-21
for c in $(seq 0 19); do cpucache "${r}" "${c}" 3 0-19; done
for c in $(seq 0 2 10); do for t in "${c}" $((c + 1)); do cpucache "${r}" "${t}" 2 "${c}-$((c + 1))"; corecpus "${r}" "${t}" "${c}-$((c + 1))"; done; done
for c in $(seq 12 15); do cpucache "${r}" "${c}" 2 12-15; corecpus "${r}" "${c}" "${c}"; done
for c in $(seq 16 19); do cpucache "${r}" "${c}" 2 16-19; corecpus "${r}" "${c}" "${c}"; done
for c in 20 21; do cpucache "${r}" "${c}" 2 20-21; corecpus "${r}" "${c}" "${c}"; done
doctor_says "atom CPUs without an L3 are LP E-cores" "${r}" 'ok: hybrid CPU: P-cores 0-11, E-cores 12-19, LP E-cores 20-21 '
doctor_says "doctor offers the new core labels" "${r}" 'k3d-gpu.io/cores=performance\|efficient\|lowpower\|ecluster\|pcore-physical\|latency'
doctor_says "E-core clusters come from the shared L2" "${r}" 'ok: E-core clusters \(shared L2\): 12-15 16-19 '
doctor_says "physical P-cores are the first thread of each core" "${r}" 'ok: physical P-cores \(one thread per core\): 0,2,4,6,8,10 '
doctor_says "no CPPC data, no favored CPUs" "${r}" 'favored' negate
for c in $(seq 0 21); do
    mkdir -p "${r}/sys/devices/system/cpu/cpu${c}/cpufreq"
    echo powersave > "${r}/sys/devices/system/cpu/cpu${c}/cpufreq/scaling_governor"
    echo balance_performance > "${r}/sys/devices/system/cpu/cpu${c}/cpufreq/energy_performance_preference"
    echo intel_pstate > "${r}/sys/devices/system/cpu/cpu${c}/cpufreq/scaling_driver"
done
perf_mtl() { env K3D_GPU_SYSROOT="${r}" PATH="${STUB_BIN}:${PATH}" bash "${K3D_GPU}" doctor --perf 2>&1; }
if grep -qE 'ok: +LP E-cores 20-21: governor powersave, EPP balance_performance' <<< "$(perf_mtl)"; then
    PASS=$((PASS + 1)); echo "ok   perf: LP E-cores are their own group"
else
    FAILS=$((FAILS + 1)); echo "FAIL perf: LP E-cores are their own group"; perf_mtl | sed 's/^/  | /'
fi

r=$(newroot mtl-no-cache)
kernel "${r}" 6.18.1
hybrid "${r}" 0-11 12-21
doctor_says "without cache sysfs no atom CPU is called LP" "${r}" 'ok: hybrid CPU: P-cores 0-11, E-cores 12-21 '
doctor_says "without cache sysfs no LP line" "${r}" 'LP E-cores' negate

# Favored cores (ITMT): CPPC highest_perf, 68 on 8-11 vs 65 on the other P-cores
# and 39 on E-cores, as read on the i9-13900T development host.
r=$(newroot favored)
kernel "${r}" 6.18.1
hybrid "${r}" 0-15 16-31
for c in $(seq 0 31); do cppc "${r}" "${c}" 65; done
for c in 8 9 10 11; do cppc "${r}" "${c}" 68; done
for c in $(seq 16 31); do cppc "${r}" "${c}" 39; done
doctor_says "argmax of acpi_cppc/highest_perf is the favored set" "${r}" 'ok: favored CPUs 8-11 \(highest_perf 68, next 65\)'
r=$(newroot not-favored)
kernel "${r}" 6.18.1
hybrid "${r}" 0-3 4-7
for c in $(seq 0 7); do cppc "${r}" "${c}" 40; done
doctor_says "equal highest_perf everywhere means no favored CPUs" "${r}" 'favored' negate
# No CPPC: IA32_HWP_CAPABILITIES (MSR 0x771) bits 7:0 through a readable /dev/cpu/N/msr.
r=$(newroot favored-msr)
kernel "${r}" 6.18.1
hybrid "${r}" 0-1 2-3
for c in 0 1 2 3; do
    mkdir -p "${r}/dev/cpu/${c}"
    { head -c $((0x771)) /dev/zero; printf "\\$(printf '%03o' "$([ "${c}" = 1 ] && echo 70 || echo 50)")"; head -c 7 /dev/zero; } > "${r}/dev/cpu/${c}/msr"
done
doctor_says "MSR 0x771 highest perf is the fallback" "${r}" 'ok: favored CPUs 1 \(highest_perf 70, next 50\)'

# `up` points the latency balloon type at the favored cores: one L2 cache dir per
# favored physical core, added to its preferCloseToDevices by type index.
FAV_BIN="${WORK}/favbin"
mkdir -p "${FAV_BIN}"
cat > "${FAV_BIN}/kubectl" <<'EOF'
#!/bin/sh
echo "kubectl $*" >> "${STUB_LOG}"
case "$*" in
    "get nodes -l "*" -o name") echo "node/k3d-gpu-server-0" ;;
    *"get balloonspolicy default -o jsonpath="*) echo "${STUB_BLN_TYPES}" ;;
esac
exit 0
EOF
chmod +x "${FAV_BIN}/kubectl"
# fav_up <name> <root> <ERE on the recorded calls and output> [negate] [env assignments...]
fav_up() {
    local name=$1 r=$2 want=$3 negate=${4:-} ok=0
    shift 3
    [ $# -eq 0 ] || shift
    rm -f "${r}/calls"
    env K3D_GPU_SYSROOT="${r}" STUB_LOG="${r}/calls" PATH="${FAV_BIN}:${STUB_BIN}:${PATH}" \
        STUB_BLN_TYPES="performance efficient latency shared" "$@" bash "${K3D_GPU}" up > "${r}/up.out" 2>&1
    grep -qE -- "${want}" "${r}/calls" "${r}/up.out" 2>/dev/null && ok=1
    [ -z "${negate}" ] || ok=$((1 - ok))
    if [ "${ok}" = 1 ]; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name}"; sed 's/^/  | /' "${r}/calls" "${r}/up.out" 2>/dev/null
    fi
}
r=$(newroot favored-up)
hybrid "${r}" 0-15 16-31
for c in $(seq 0 31); do cppc "${r}" "${c}" 65; done
for c in 8 9 10 11; do cppc "${r}" "${c}" 68; done
for c in $(seq 0 2 14); do for t in "${c}" $((c + 1)); do cpucache "${r}" "${t}" 2 "${c}-$((c + 1))"; done; done
fav_up "up points the latency type (index 2) at one L2 dir per favored core" "${r}" \
    'patch balloonspolicy default --type=json -p \[\{"op":"add","path":"/spec/balloonTypes/2/preferCloseToDevices","value":\["/sys/devices/system/cpu/cpu8/cache/index2","/sys/devices/system/cpu/cpu10/cache/index2"\]\}\]$'
fav_up "favored hint dirs are host paths, not sysroot paths" "${r}" "value\":\[\"${r}" negate
fav_up "no latency type in the policy: no patch" "${r}" 'patch balloonspolicy' negate \
    STUB_BLN_TYPES="performance efficient shared"
fav_up "no latency type in the policy: up warns" "${r}" 'no latency balloon type' "" \
    STUB_BLN_TYPES="performance efficient shared"
r=$(newroot favored-up-nocache)
hybrid "${r}" 0-15 16-31
for c in $(seq 0 31); do cppc "${r}" "${c}" 65; done
for c in 8 9; do cppc "${r}" "${c}" 68; done
fav_up "favored CPUs without L2 sysfs: annotation only, no policy query" "${r}" 'balloonspolicy' negate
fav_up "favored CPUs without L2 sysfs: still annotated" "${r}" 'annotate nodes --all --overwrite k3d-gpu.io/favored-cpus=8-9$'

# ---- NUMA / SNC (Xeon) ----------------------------------------------------------

# node <root> <n> <cpulist> [package]: NUMA node n; "" is a memory-only node (HBM,
# CXL). The node's first CPU gets topology/physical_package_id <package>.
node() {
    local d="$1/sys/devices/system/node/node$2" first
    mkdir -p "${d}"
    echo "$3" > "${d}/cpulist"
    [ -n "$3" ] && [ -n "${4:-}" ] || return 0
    first=${3%%[-,]*}
    mkdir -p "$1/sys/devices/system/cpu/cpu${first}/topology"
    echo "$4" > "$1/sys/devices/system/cpu/cpu${first}/topology/physical_package_id"
}
# l3 <root> <cpulist>: the CPUs sharing CPU 0's L3 cache.
l3() {
    mkdir -p "$1/sys/devices/system/cpu/cpu0/cache/index3"
    echo "$2" > "$1/sys/devices/system/cpu/cpu0/cache/index3/shared_cpu_list"
}
NUMA2="numa k3d-gpu.io/numa-nodes=2 k3d-gpu.io/snc=1"

r=$(newroot numa-2s)
node "${r}" 0 0-3,8-11 0; node "${r}" 1 4-7,12-15 1; l3 "${r}" 0-3,8-11
expect "two sockets are a NUMA host with SNC off" "${r}" "${NUMA2}"

r=$(newroot numa-1)
node "${r}" 0 0-15 0; l3 "${r}" 0-15
expect "one NUMA node is not numa" "${r}" ""

r=$(newroot numa-snc3)
for n in 0 1 2 3 4 5; do node "${r}" "${n}" "$((n * 2))-$((n * 2 + 1))" "$((n / 3))"; done
l3 "${r}" 0-5
expect "SNC3 is CPU 0's L3 CPUs over its node's CPUs" "${r}" \
    "numa k3d-gpu.io/numa-nodes=6 k3d-gpu.io/snc=3"

r=$(newroot numa-snc-improbable)
for n in 0 1 2 3 4 5; do node "${r}" "${n}" "${n}" 0; done
l3 "${r}" 0-4
expect "an improbable L3/node ratio (5) reads as SNC off, like the kernel" "${r}" \
    "numa k3d-gpu.io/numa-nodes=6 k3d-gpu.io/snc=1"

r=$(newroot numa-no-l3)
node "${r}" 0 0-3 0; node "${r}" 1 4-7 1
expect "no L3 sharing list: NUMA without an SNC label" "${r}" "numa k3d-gpu.io/numa-nodes=2"

r=$(newroot numa-garbled)
node "${r}" 0 'x-3' 0; node "${r}" 1 4-7 1; l3 "${r}" 0-3
expect "a garbled node CPU list gives no SNC label" "${r}" "numa k3d-gpu.io/numa-nodes=2"

r=$(newroot numa-memory-only)
node "${r}" 0 0-7 0; node "${r}" 1 ""; l3 "${r}" 0-7
expect "a memory-only node (HBM/CXL) counts as a NUMA node" "${r}" "${NUMA2}"

r=$(newroot numa-forced)
expect "forced numa is accepted" "${r}" "numa k3d-gpu.io/numa-nodes=0" K3D_GPU_VENDORS=numa

r=$(newroot numa-hybrid)
hybrid "${r}" 0-7 8-15
node "${r}" 0 0-7 0; node "${r}" 1 8-15 0; l3 "${r}" 0-15
expect "hybrid and numa are independent" "${r}" "hybrid k3d-gpu.io/hybrid-cpu=true
numa k3d-gpu.io/numa-nodes=2 k3d-gpu.io/snc=2"

# `up` on a host that is both hybrid and NUMA waits for the one balloons DaemonSet once.
UP_BIN="${WORK}/upbin"
mkdir -p "${UP_BIN}"
cat > "${UP_BIN}/kubectl" <<'EOF'
#!/bin/sh
echo "kubectl $*" >> "${STUB_LOG}"
case "$*" in "get nodes -l "*" -o name") echo "node/k3d-gpu-server-0" ;; esac
exit 0
EOF
chmod +x "${UP_BIN}/kubectl"
r=$(newroot numa-hybrid-up)
hybrid "${r}" 0-7 8-15
node "${r}" 0 0-7 0; node "${r}" 1 8-15 0; l3 "${r}" 0-15
env K3D_GPU_SYSROOT="${r}" STUB_LOG="${r}/calls" PATH="${UP_BIN}:${STUB_BIN}:${PATH}" \
    bash "${K3D_GPU}" up >/dev/null 2>&1
n=$(grep -c 'rollout status daemonset/nri-resource-policy-balloons' "${r}/calls" 2>/dev/null)
if [ "${n}" = 1 ]; then
    PASS=$((PASS + 1)); echo "ok   hybrid + numa: the balloons DaemonSet is rolled out once"
else
    FAILS=$((FAILS + 1)); echo "FAIL hybrid + numa: balloons DaemonSet rolled out ${n:-0} time(s), want 1"
fi

r=$(newroot numa-doc)
kernel "${r}" 6.18.1
node "${r}" 0 0-3 0; node "${r}" 1 4-7 1; node "${r}" 2 ""; l3 "${r}" 0-3
doctor_says "doctor reports NUMA nodes, SNC and sockets" "${r}" \
    'ok: NUMA: 3 node\(s\) \(2 with CPUs\), SNC 1 per L3, 2 socket\(s\) — up deploys the NRI balloons policy; label pods k3d-gpu.io/cores=replica'
doctor_says "doctor names memory-only NUMA nodes" "${r}" 'info: NUMA node\(s\) 2 have memory but no CPUs'
doctor_says "no rdt_a flag: no resctrl guidance" "${r}" 'RDT' negate
mkdir -p "${r}/proc"
printf 'processor\t: 0\nflags\t\t: fpu cat_l3 cdp_l3 rdt_a mba\n' > "${r}/proc/cpuinfo"
doctor_says "rdt_a gives resctrl class guidance" "${r}" \
    'info: RDT allocation \(rdt_a: cat_l3 cdp_l3 mba\) — .*control\.rdt\.enable'

r=$(newroot numa-doc-forced)
kernel "${r}" 6.18.1
node "${r}" 0 0-7 0
doctor_says "forced numa on one node fails doctor" "${r}" 'fail.*NUMA' "" K3D_GPU_VENDORS=numa

# The balloons policy deploys on hybrid and on NUMA nodes, and ships a replica
# balloon type whose every field exists in the bundled v0.14.0 CRD.
BALLOONS="$(dirname "${K3D_GPU}")/../share/nri-balloons.yml"
balloons_check() {
    python3 - "${BALLOONS}" <<'EOF'
import sys
try:
    import yaml
except ImportError:
    print("skip: PyYAML missing"); sys.exit(0)
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
by = {(d["kind"], d["metadata"]["name"]): d for d in docs}
ds = by[("DaemonSet", "nri-resource-policy-balloons")]["spec"]["template"]["spec"]
assert "k3d-gpu.io/hybrid-cpu" not in ds.get("nodeSelector", {}), "nodeSelector still hybrid-only"
terms = ds["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
exprs = [t["matchExpressions"][0] for t in terms]
assert {"key": "k3d-gpu.io/hybrid-cpu", "operator": "In", "values": ["true"]} in exprs, exprs
assert {"key": "k3d-gpu.io/numa-nodes", "operator": "Exists"} in exprs, exprs
pol = by[("BalloonsPolicy", "default")]["spec"]
assert pol["agent"]["podResourceAPI"] is True
types = pol["balloonTypes"]
assert types[-1]["name"] == "shared", "shared (AlwaysTrue) must stay last"
rep = next(t for t in types if t["name"] == "replica")
want = {"pinMemory": True, "allocatorTopologyBalancing": True, "shareIdleCPUsInSame": "numa",
        "preferNewBalloons": True, "preferCloseToDevices": ["podresourceapi:*/*"]}
for k, v in want.items():
    assert rep.get(k) == v, (k, rep.get(k))
assert rep["matchExpressions"] == [{"key": "pod/labels/k3d-gpu.io/cores", "operator": "Equals", "values": ["replica"]}]
crd = by[("CustomResourceDefinition", "balloonspolicies.config.nri")]
props = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]
bt = props["balloonTypes"]["items"]["properties"]
for k in rep:
    assert k in bt, "replica field %s not in the CRD" % k
assert "numa" in bt["shareIdleCPUsInSame"]["enum"]
assert "podResourceAPI" in props["agent"]["properties"]
print("ok")
EOF
}
if out=$(balloons_check 2>&1) && [ "${out}" = ok ]; then
    PASS=$((PASS + 1)); echo "ok   balloons deploy on hybrid or NUMA nodes; replica type matches the CRD"
elif [[ "${out}" == skip:* ]]; then
    echo "skip balloons manifest check (${out#skip: })"
else
    FAILS=$((FAILS + 1)); echo "FAIL balloons manifest: ${out}"
fi

# ---- accelerators per socket vs ARK default (Xeon) -------------------------------

# idxd_dev <root> <bdf> <id> <name> <numa node> [wq]: a DSA/IAA PCI function bound to
# idxd, its <name> device on the dsa bus, and optionally one user work queue.
idxd_dev() {
    pci "$1" "$2" 0x8086 "$3" idxd 0x088000
    echo "$5" > "$1/sys/devices/pci0000:00/$2/numa_node"
    mkdir -p "$1/sys/devices/pci0000:00/$2/$4" "$1/sys/bus/dsa/devices"
    ln -sfn "$1/sys/devices/pci0000:00/$2/$4" "$1/sys/bus/dsa/devices/$4"
}
model() {
    mkdir -p "$1/proc"
    printf 'processor\t: 0\nmodel name\t: %s\nflags\t\t: fpu amx_tile\n' "$2" > "$1/proc/cpuinfo"
}

r=$(newroot socket-counts)
kernel "${r}" 6.18.1
model "${r}" "Intel(R) Xeon(R) Platinum 8480+"
node "${r}" 0 0-3 0; node "${r}" 1 4-7 1; l3 "${r}" 0-3
idxd_dev "${r}" 0000:e7:01.0 0x0b25 dsa0 0
idxd_dev "${r}" 0000:e7:02.0 0x0cfe iax1 0
idxd_dev "${r}" 0000:f2:01.0 0x0b25 dsa2 1
pci "${r}" 0000:6b:00.0 0x8086 0x4940 4xxx 0x0b4000
echo 0 > "${r}/sys/devices/pci0000:00/0000:6b:00.0/numa_node"
doctor_says "doctor counts accelerators per socket against the ARK default" "${r}" \
    'info: per socket: socket 0: DSA 1, IAA 1, QAT 1; socket 1: DSA 1, IAA 0, QAT 0 \(ARK default for 8480\+: DSA 1, IAA 1, QAT 1 per socket\)'
doctor_says "a socket below the ARK default is flagged" "${r}" \
    'warn.*: socket 1: IAA 0, QAT 0 below the ARK default for 8480\+'
doctor_says "no On Demand note without intel_vsec.sdsi" "${r}" 'On Demand' negate
mkdir -p "${r}/sys/bus/auxiliary/devices/intel_vsec.sdsi.0"
doctor_says "intel_vsec.sdsi gives the On Demand note" "${r}" \
    'info: Intel On Demand interface present \(intel_vsec\.sdsi\.0\)'

r=$(newroot socket-counts-unknown)
kernel "${r}" 6.18.1
idxd_dev "${r}" 0000:00:0c.0 0x11fb dsa0 -1
doctor_says "an unknown SKU gets counts without an ARK default" "${r}" \
    'info: per socket: socket 0: DSA 1, IAA 0, QAT 0 \(no ARK default on file for this CPU\)'
doctor_says "an unknown SKU is never flagged below default" "${r}" 'below the ARK default' negate

r=$(newroot socket-counts-unplaced)
kernel "${r}" 6.18.1
node "${r}" 0 0-3 0; node "${r}" 1 4-7 1
idxd_dev "${r}" 0000:e7:01.0 0x0b25 dsa0 -1
doctor_says "a device with no NUMA node on a two-socket host is socket ?" "${r}" \
    'info: per socket: socket 0: DSA 0, IAA 0, QAT 0; socket 1: DSA 0, IAA 0, QAT 0; socket \?: DSA 1, IAA 0, QAT 0'

# DSA/IAA functions are counted from the PCI bus by id, like QAT: a host whose idxd
# module is not loaded has no dsa bus, and that is not a disabled device.
r=$(newroot socket-counts-unbound)
kernel "${r}" 6.18.1
model "${r}" "Intel(R) Xeon(R) Platinum 8480+"
node "${r}" 0 0-3 0; l3 "${r}" 0-3
pci "${r}" 0000:e7:01.0 0x8086 0x0b25 "" 0x088000
pci "${r}" 0000:e7:02.0 0x8086 0x0cfe "" 0x088000
pci "${r}" 0000:6b:00.0 0x8086 0x4940 "" 0x0b4000
doctor_says "DSA/IAA without idxd loaded are still counted per socket" "${r}" \
    'info: per socket: socket 0: DSA 1, IAA 1, QAT 1 \(ARK default'
doctor_says "DSA/IAA without idxd loaded are not flagged below the ARK default" "${r}" 'below the ARK default' negate

r=$(newroot socket-counts-none)
kernel "${r}" 6.18.1
model "${r}" "Intel(R) Xeon(R) 6980P"
doctor_says "no DSA/IAA/QAT device: no per-socket line" "${r}" 'per socket' negate

# Docker 29 lists CDI devices (cdi: nvidia.com/gpu=all) in `docker info`; that is
# not a registered nvidia runtime, and doctor must not say it is.
r=$(newroot nvidia-rt)
mkdir -p "${r}/sys/module/nvidia"; touch "${r}/dev/nvidiactl"
doctor_says "nvidia runtime registered is reported" "${r}" 'ok: nvidia runtime registered' "" \
    STUB_DOCKER_RUNTIMES="io.containerd.runc.v2 nvidia runc"
doctor_says "CDI-only host is not reported as having the nvidia runtime" "${r}" 'nvidia runtime registered' negate \
    STUB_DOCKER_RUNTIMES="io.containerd.runc.v2 runc" STUB_DOCKER_CDI="nvidia.com/gpu=0 nvidia.com/gpu=all"
doctor_says "CDI-only host is ok via CDI" "${r}" 'ok: .*CDI' "" \
    STUB_DOCKER_RUNTIMES="io.containerd.runc.v2 runc" STUB_DOCKER_CDI="nvidia.com/gpu=0 nvidia.com/gpu=all"
doctor_says "no runtime and no CDI warns" "${r}" 'warn.*nvidia-ctk' "" \
    STUB_DOCKER_RUNTIMES="io.containerd.runc.v2 runc"

# ---- doctor: live-cluster failure modes (docs/failure-modes.md) ---------------

r=$(newroot cluster-hybrid)
kernel "${r}" 6.18.1
hybrid "${r}" 0-15 16-31
doctor_says "no reachable cluster skips the cluster checks" "${r}" 'Checking cluster' negate
CL=(STUB_NODES=k3d-gpu-server-0 STUB_HYBRID_NODES=k3d-gpu-server-0
    "STUB_DS_LIST=kube-system/nri-resource-policy-balloons\n")
doctor_says "Ready balloons DaemonSet on a hybrid node is ok" "${r}" 'ok: NRI balloons policy: 1/1 Ready' "" \
    "${CL[@]}" "STUB_DS_nri_resource_policy_balloons=1 1"
doctor_says "balloons DaemonSet not Ready while hybrid label set fails" "${r}" 'fail.*NRI balloons policy: 0/1 Ready' "" \
    "${CL[@]}" "STUB_DS_nri_resource_policy_balloons=1 0"
doctor_says "balloons not Ready says labelled pods start unpinned" "${r}" 'start unpinned' "" \
    "${CL[@]}" "STUB_DS_nri_resource_policy_balloons=1 0"
doctor_says "balloons DaemonSet missing while hybrid label set fails" "${r}" 'fail.*NRI balloons policy: DaemonSet .* not found' "" \
    "${CL[@]}"
doctor_says "missing NRI socket in the node is reported" "${r}" 'fail.*nri\.sock' "" \
    "${CL[@]}" "STUB_DS_nri_resource_policy_balloons=1 0" STUB_NRI_SOCK_MISSING=1
doctor_says "present NRI socket is not reported missing" "${r}" 'nri\.sock' negate \
    "${CL[@]}" "STUB_DS_nri_resource_policy_balloons=1 0"
doctor_says "no hybrid-labelled node means no balloons check" "${r}" 'NRI balloons policy:' negate \
    STUB_NODES=k3d-gpu-server-0
doctor_says "two NRI resource policies fail" "${r}" 'fail.*2 NRI resource policies.*topology-aware' "" \
    "${CL[@]}" "STUB_DS_nri_resource_policy_balloons=1 1" \
    "STUB_DS_LIST=kube-system/nri-resource-policy-balloons\nkube-system/nri-resource-policy-topology-aware\n"
doctor_says "one NRI resource policy is not a conflict" "${r}" 'NRI resource policies' negate \
    "${CL[@]}" "STUB_DS_nri_resource_policy_balloons=1 1"

r=$(newroot cluster-nvidia)
kernel "${r}" 6.18.1
mkdir -p "${r}/sys/module/nvidia"; touch "${r}/dev/nvidiactl"
doctor_says "Ready device plugin DaemonSet is ok" "${r}" 'ok: nvidia device plugin: 1/1 Ready' "" \
    STUB_NODES=k3d-gpu-server-0 "STUB_DS_nvidia_device_plugin_daemonset=1 1"
doctor_says "device plugin DaemonSet not Ready fails" "${r}" 'fail.*nvidia device plugin: 0/1 Ready' "" \
    STUB_NODES=k3d-gpu-server-0 "STUB_DS_nvidia_device_plugin_daemonset=1 0"
doctor_says "device plugin scheduled on no node fails" "${r}" 'fail.*nvidia device plugin: scheduled on no node' "" \
    STUB_NODES=k3d-gpu-server-0 "STUB_DS_nvidia_device_plugin_daemonset=0 0"
doctor_says "disconnected device plugin (allocatable 0, capacity kept) fails" "${r}" \
    'fail.*k3d-gpu-server-0: nvidia.com/gpu allocatable 0 of capacity 2' "" \
    STUB_NODES=k3d-gpu-server-0 "STUB_DS_nvidia_device_plugin_daemonset=1 1" \
    "STUB_NODE_RES=k3d-gpu-server-0 cpu 32 31\nk3d-gpu-server-0 nvidia.com/gpu 2 0\n"
doctor_says "partly unhealthy devices warn" "${r}" 'warn.*nvidia.com/gpu allocatable 1 of capacity 2' "" \
    STUB_NODES=k3d-gpu-server-0 "STUB_DS_nvidia_device_plugin_daemonset=1 1" \
    "STUB_NODE_RES=k3d-gpu-server-0 nvidia.com/gpu 2 1\n"
doctor_says "core resources with capacity above allocatable are ignored" "${r}" 'allocatable .* of capacity' negate \
    STUB_NODES=k3d-gpu-server-0 "STUB_DS_nvidia_device_plugin_daemonset=1 1" \
    "STUB_NODE_RES=k3d-gpu-server-0 cpu 32 31\nk3d-gpu-server-0 memory 64Gi 63Gi\nk3d-gpu-server-0 nvidia.com/gpu 2 2\n"

# `test` must show the pod's output even when it exits before anything could
# attach, and must fail when the pod fails. kubectl is stubbed: STUB_POD_PHASE is
# the phase every pod reaches, STUB_POD_LOGS what `kubectl logs` prints.
KSTUB="${WORK}/kbin"
mkdir -p "${KSTUB}"
cat > "${KSTUB}/kubectl" <<'EOF'
#!/bin/sh
case "$1" in
    get)  case "$*" in *phase*) echo "${STUB_POD_PHASE}" ;; esac ;;
    # no final newline, like nvidia-smi stderr; STUB_POD_LOGS_<pod name, - as _> overrides per pod
    logs) eval "out=\${STUB_POD_LOGS_$(echo "$2" | tr -- - _):-\${STUB_POD_LOGS}}"; printf %s "${out}" ;;
esac
exit 0
EOF
chmod +x "${KSTUB}/kubectl"

# test_says <name> <root> <want rc: 0|fail> <ERE> [env assignments...]
test_says() {
    local name=$1 root=$2 want=$3 re=$4 out rc
    shift 4
    out=$(env K3D_GPU_SYSROOT="${root}" PATH="${KSTUB}:${PATH}" "$@" bash "${K3D_GPU}" test 2>&1)
    rc=$?
    if grep -E -- "${re}" <<< "${out}" >/dev/null \
       && { { [ "${want}" = 0 ] && [ "${rc}" -eq 0 ]; } || { [ "${want}" = fail ] && [ "${rc}" -ne 0 ]; }; }; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (rc=${rc}, want ${want}, /${re}/)"
        sed 's/^/  | /' <<< "${out}"
    fi
}

r=$(newroot nvidia-test)
mkdir -p "${r}/sys/module/nvidia"
test_says "test prints nvidia-smi output from a pod that already exited" "${r}" 0 'GPU 0: Fake RTX' \
    STUB_POD_PHASE=Succeeded STUB_POD_LOGS="GPU 0: Fake RTX"
test_says "test fails when the nvidia-smi pod fails, and still shows its output" "${r}" fail 'NVML init failed' \
    STUB_POD_PHASE=Failed STUB_POD_LOGS="NVML init failed"

test_says "pod output without a final newline does not glue onto the next line" "${r}" 0 \
    '^(.\[[0-9;]*m)?==>.*Deploying gaudi-test' \
    STUB_POD_PHASE=Succeeded STUB_POD_LOGS="ERROR: noise" K3D_GPU_VENDORS=nvidia,gaudi

# Image refs are pasted into the pod overrides JSON, so `test` must refuse a value
# that is not a plain image reference instead of sending it on.
r=$(newroot nvidia-test)
mkdir -p "${r}/sys/module/nvidia"
inject='busybox:1.37","securityContext":{"privileged":true},"imagePullPolicy":"IfNotPresent'
test_says "test rejects a K3D_GPU_DEVICE_TEST_IMAGE that is not an image reference" "${r}" fail \
    "K3D_GPU_DEVICE_TEST_IMAGE .* is not a valid image reference" \
    STUB_POD_PHASE=Succeeded K3D_GPU_VENDORS=gaudi K3D_GPU_DEVICE_TEST_IMAGE="${inject}"
test_says "test rejects a K3D_GPU_TEST_IMAGE that is not an image reference" "${r}" fail \
    "K3D_GPU_TEST_IMAGE .* is not a valid image reference" \
    STUB_POD_PHASE=Succeeded K3D_GPU_TEST_IMAGE="${inject}"
test_says "test accepts a digest-pinned image reference" "${r}" 0 'Deploying gaudi-test' \
    STUB_POD_PHASE=Succeeded K3D_GPU_VENDORS=gaudi \
    K3D_GPU_DEVICE_TEST_IMAGE="docker.io/library/busybox:1.37@sha256:$(printf '%064d' 0)"

r=$(newroot hybrid-test)
hybrid "${r}" 0-15 16-31
test_says "hybrid test passes when each pod lands on its core type" "${r}" 0 'Cpus_allowed_list' \
    STUB_POD_PHASE=Succeeded STUB_POD_LOGS_cores_performance_test="Cpus_allowed_list:	0-1" \
    STUB_POD_LOGS_cores_efficient_test="Cpus_allowed_list:	20,24-25" K3D_GPU_VENDORS=hybrid
test_says "hybrid test fails when a performance pod is not on P-cores" "${r}" fail 'not on the performance CPUs' \
    STUB_POD_PHASE=Succeeded STUB_POD_LOGS_cores_performance_test="Cpus_allowed_list:	0-15,17-31" \
    STUB_POD_LOGS_cores_efficient_test="Cpus_allowed_list:	20" K3D_GPU_VENDORS=hybrid
test_says "hybrid test fails when an efficient pod is not on E-cores" "${r}" fail 'not on the efficient CPUs' \
    STUB_POD_PHASE=Succeeded STUB_POD_LOGS_cores_performance_test="Cpus_allowed_list:	0" \
    STUB_POD_LOGS_cores_efficient_test="Cpus_allowed_list:	3" K3D_GPU_VENDORS=hybrid
# On Arrow Lake-H up hands cpu_lowpower to the balloons E-core kind, so an
# efficient pod may land there.
mkdir -p "${r}/sys/devices/cpu_lowpower"; echo 32-33 > "${r}/sys/devices/cpu_lowpower/cpus"
test_says "hybrid test accepts an efficient pod on a cpu_lowpower CPU" "${r}" 0 'Cpus_allowed_list' \
    STUB_POD_PHASE=Succeeded STUB_POD_LOGS_cores_performance_test="Cpus_allowed_list:	0" \
    STUB_POD_LOGS_cores_efficient_test="Cpus_allowed_list:	32" K3D_GPU_VENDORS=hybrid

# DTO's auto-tuner (on by default) moves the offload threshold between 6 KiB and
# 64 KiB under load (intel/DTO dto.c). The Redis A/B probe needs a fixed
# DTO_MIN_BYTES, so the manifest and the doc's pod snippet must turn it off.
ROOT="$(dirname "${K3D_GPU}")/.."
dto_knobs_pinned() {
    local name=$1 got
    got=$(grep -A1 -E 'DTO_AUTO_ADJUST_KNOBS' "$2" | grep -oE '"[01]"' | head -n1)
    if [ "${got}" = '"0"' ]; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name} (DTO_AUTO_ADJUST_KNOBS=${got:-unset})"
    fi
}
dto_knobs_pinned "redis-dto.yaml pins the DTO offload threshold" "${ROOT}/examples/dataplane/redis-dto.yaml"
dto_knobs_pinned "dataplane doc pod snippet pins the DTO offload threshold" "${ROOT}/docs/accelerated-dataplane.md"

# The API server drops balloon options its CRD schema does not list, so every
# option in the shipped BalloonsPolicy must be in the CRD shipped beside it.
if python3 -c 'import yaml' 2>/dev/null; then
    if out=$(python3 "${ROOT}/tests/nri_balloons_check.py" 2>&1); then
        PASS=$((PASS + 1)); echo "ok   nri-balloons.yml BalloonsPolicy matches its CRD schema"
    else
        FAILS=$((FAILS + 1)); echo "FAIL nri-balloons.yml BalloonsPolicy matches its CRD schema"; sed 's/^/  | /' <<< "${out}"
    fi
else
    echo "skip nri-balloons.yml schema check (python3 yaml module missing)"
fi
# Each k3d-gpu.io/cores value doctor offers has a balloon type of that name.
for t in performance efficient lowpower ecluster pcore-physical latency; do
    if grep -qx "    name: ${t}" "${ROOT}/share/nri-balloons.yml" \
       && grep -q -- "- {key: pod/labels/k3d-gpu.io/cores, operator: Equals, values: \[${t}\]}" "${ROOT}/share/nri-balloons.yml"; then
        PASS=$((PASS + 1)); echo "ok   nri-balloons.yml has balloon type ${t}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL nri-balloons.yml has balloon type ${t}"
    fi
done

# ---- docs ---------------------------------------------------------------------

# Every relative link in README.md and docs/*.md must name a file in this tree.
# Code spans and fenced blocks are skipped: regexes there are not links.
REPO="$(dirname "${K3D_GPU}")/.."
dead=""
for md in "${REPO}/README.md" "${REPO}"/docs/*.md; do
    while read -r target; do
        [ -e "$(dirname "${md}")/${target}" ] || dead+=" ${md#"${REPO}"/}:${target}"
    done < <(sed '/^```/,/^```/d; s/`[^`]*`//g' "${md}" | grep -oE '\]\([^)#[:space:]]+' | sed 's/^](//' | grep -vE '^[a-z]+:')
done
if [ -z "${dead}" ]; then
    PASS=$((PASS + 1)); echo "ok   relative doc links resolve"
else
    FAILS=$((FAILS + 1)); echo "FAIL relative doc links resolve; dead:${dead}"
fi

# vendor_total counts AMX/hybrid/NUMA as distinct labelled nodes, not one per label:
# labels L1 and L2 are both on nodes a and b, L3 on none, so the answer is 2.
# Two nodes under two labels also catches node lists joined without a newline.
VT_FN=$(sed -n '/^vendor_total() {/,/^}/p' "${K3D_GPU}")
vt() {
    bash -euo pipefail -c "${VT_FN}"'
        vendor_resources() { printf "L1\nL2\nL3\n"; }
        kubectl() { [ "${STUB_NONE:-}" ] && return 1; case "$4" in L1|L2) printf "node/a\nnode/b\n" ;; esac; }
        vendor_total "$1"' _ "$1"
}
for case in "amx 2" "hybrid 2" "numa 2" "amx 0 none"; do
    read -r v want none <<< "${case}"
    got=$(STUB_NONE="${none:-}" vt "${v}" 2>&1)
    if [ "${got}" = "${want}" ]; then
        PASS=$((PASS + 1)); echo "ok   vendor_total ${case} counts distinct nodes"
    else
        FAILS=$((FAILS + 1)); echo "FAIL vendor_total ${case}: got '${got}', want '${want}'"
    fi
done


# The docker-in-docker feature's install.sh exits 1 when moby=true (its default) on
# Debian trixie, so a trixie dev container must set "moby": false.
DEVC="$(dirname "${K3D_GPU}")/../.devcontainer"
if grep -qE '^FROM .*-trixie[@: ]' "${DEVC}/Dockerfile" \
   && ! python3 - "${DEVC}/devcontainer.json" <<'EOF'
import json, sys
features = json.load(open(sys.argv[1])).get("features", {})
dind = [opts for ref, opts in features.items() if "/docker-in-docker:" in ref]
sys.exit(1 if any(opts.get("moby", True) is not False for opts in dind) else 0)
EOF
then
    FAILS=$((FAILS + 1)); echo "FAIL devcontainer docker-in-docker sets moby=false on a trixie base"
else
    PASS=$((PASS + 1)); echo "ok   devcontainer docker-in-docker sets moby=false on a trixie base"
fi

# ---- doctor --perf ---------------------------------------------------------------
# A fake host tuned for inference (good) and one with every knob wrong (bad). The
# advisor must flag each bad knob with an apply and an exact revert, stay quiet on
# the good host, and never write to the tree or run sudo.

# put <root> <path> <value>: write a sysfs/procfs file.
put() { mkdir -p "$(dirname "$1$2")"; printf '%s\n' "$3" > "$1$2"; }

# cpufreq <root> <cpulist> <governor> <epp> <scaling_max kHz> <cpuinfo_max kHz>
cpufreq() {
    local cpu
    for cpu in $(bash -c 'for p in ${1//,/ }; do [[ $p == *-* ]] && seq ${p%-*} ${p#*-} || echo $p; done' _ "$2"); do
        local d="/sys/devices/system/cpu/cpu${cpu}/cpufreq"
        put "$1" "${d}/scaling_driver" intel_pstate
        put "$1" "${d}/scaling_governor" "$3"
        [ -z "$4" ] || put "$1" "${d}/energy_performance_preference" "$4"
        put "$1" "${d}/scaling_max_freq" "$5"
        put "$1" "${d}/cpuinfo_max_freq" "$6"
        put "$1" "${d}/scaling_min_freq" 800000
    done
}

# idle <root> <state index> <name> <latency us> <disable>
idle() {
    put "$1" /sys/devices/system/cpu/cpuidle/current_driver intel_idle
    put "$1" "/sys/devices/system/cpu/cpu0/cpuidle/state$2/name" "$3"
    put "$1" "/sys/devices/system/cpu/cpu0/cpuidle/state$2/latency" "$4"
    put "$1" "/sys/devices/system/cpu/cpu0/cpuidle/state$2/disable" "$5"
}

# rapl <root> <zone> <enabled> <PL1 uW> <PL1 max uW> <PL2 uW>
rapl() {
    local z="/sys/class/powercap/$2"
    put "$1" "${z}/name" package-0
    put "$1" "${z}/enabled" "$3"
    put "$1" "${z}/constraint_0_name" long_term
    put "$1" "${z}/constraint_0_power_limit_uw" "$4"
    put "$1" "${z}/constraint_0_max_power_uw" "$5"
    put "$1" "${z}/constraint_1_name" short_term
    put "$1" "${z}/constraint_1_power_limit_uw" "$6"
}

# meminfo <root> <MemAvailable kB> <SwapTotal kB> <SwapFree kB> <HugePages_Total> <HugePages_Free>
meminfo() {
    mkdir -p "$1/proc"
    printf 'MemTotal: 67108864 kB\nMemAvailable: %s kB\nSwapTotal: %s kB\nSwapFree: %s kB\nHugePages_Total: %s\nHugePages_Free: %s\nHugepagesize: 2048 kB\n' \
        "$2" "$3" "$4" "$5" "$6" > "$1/proc/meminfo"
}

# i915gt <root> <bdf> <card> <cur> <min> <max> <boost> <RPn> <RP0> [slpc]
i915gt() {
    local g="/sys/class/drm/$3/gt/gt0" f
    mkdir -p "$1/sys/class/drm/$3"
    ln -sfn "$1/sys/devices/pci0000:00/$2" "$1/sys/class/drm/$3/device"
    for f in "cur:$4" "act:$4" "min:$5" "max:$6" "boost:$7" "RPn:$8" "RP0:$9"; do
        put "$1" "${g}/rps_${f%%:*}_freq_mhz" "${f#*:}"
    done
    [ -z "${10:-}" ] || put "$1" "${g}/slpc_ignore_eff_freq" 0
}

# The stubbed nvidia-smi prints STUB_NVSMI (";"-separated CSV rows); the stubbed
# sudo records that it ran, which the read-only test forbids.
printf '#!/bin/sh\n[ "$1" = topo ] && { echo "${STUB_NVSMI_TOPO:-}" | tr ";" "\\n"; exit 0; }\necho "${STUB_NVSMI:-}" | tr ";" "\\n"\nexit "${STUB_NVSMI_RC:-0}"\n' > "${STUB_BIN}/nvidia-smi"
printf '#!/bin/sh\necho "$*" >> "%s/sudo-called"\n' "${WORK}" > "${STUB_BIN}/sudo"
chmod +x "${STUB_BIN}/nvidia-smi" "${STUB_BIN}/sudo"

# perf_says <name> <root> <ERE> [negate] [env assignments...]: `doctor --perf` output
# (colour codes stripped) must match <ERE>, or must not when a 4th argument is given.
perf_says() {
    local name=$1 root=$2 re=$3 negate=${4:-} out hit=0
    shift 4 2>/dev/null || shift $#
    out=$(env K3D_GPU_SYSROOT="${root}" PATH="${STUB_BIN}:${PATH}" "$@" bash "${K3D_GPU}" doctor --perf 2>&1 \
          | sed 's/\x1b\[[0-9;]*m//g')
    grep -E -- "${re}" <<< "${out}" >/dev/null && hit=1
    if { [ -z "${negate}" ] && [ "${hit}" -eq 1 ]; } || { [ -n "${negate}" ] && [ "${hit}" -eq 0 ]; }; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (${negate:+not }matching /${re}/)"
        sed 's/^/  | /' <<< "${out}"
    fi
}

# Good host: every knob already where inference wants it.
good=$(newroot perf-good)
hybrid "${good}" 0-1 2-3
put "${good}" /sys/devices/system/cpu/online 0-3
cpufreq "${good}" 0-1 powersave performance 5100000 5100000
cpufreq "${good}" 2-3 powersave balance_performance 3900000 3900000
put "${good}" /sys/devices/system/cpu/intel_pstate/status active
put "${good}" /sys/devices/system/cpu/intel_pstate/no_turbo 0
put "${good}" /sys/devices/system/cpu/intel_pstate/max_perf_pct 100
put "${good}" /sys/devices/system/cpu/intel_pstate/min_perf_pct 16
put "${good}" /sys/devices/system/cpu/intel_pstate/hwp_dynamic_boost 1
idle "${good}" 0 POLL 0 0; idle "${good}" 1 C1E 2 0; idle "${good}" 2 C6 170 1
rapl "${good}" intel-rapl:0 1 65000000 35000000 117000000
put "${good}" /sys/kernel/mm/transparent_hugepage/enabled "always [madvise] never"
put "${good}" /sys/kernel/mm/transparent_hugepage/defrag "always defer defer+madvise [madvise] never"
meminfo "${good}" 50331648 8388608 8388608 0 0
put "${good}" /proc/sys/vm/swappiness 60
put "${good}" /proc/4242/comm irqbalance
pci "${good}" 0000:00:02.0 0x8086 0xa780 i915
i915gt "${good}" 0000:00:02.0 card0 1650 1650 1650 1650 300 1650 slpc

perf_says "perf: tuned host has no suggestions" "${good}" '^==> 0 suggestion\(s\)\. Read-only' "" STUB_DOCKER_DRIVER=overlay2
perf_says "perf: reports EPP per core type" "${good}" 'ok: +E-cores 2-3: governor powersave, EPP balance_performance'
perf_says "perf: names the evidence file" "${good}" 'from: /sys/devices/system/cpu/cpu\{0\.\.1\}/cpufreq/energy_performance_preference'
perf_says "perf: every section cites a source" "${good}" '^  src:  https://docs\.kernel\.org/admin-guide/mm/transhuge\.html'
perf_says "perf: GuC SLPC detected from slpc_ignore_eff_freq" "${good}" 'card0 i915 gt0: GuC SLPC; cur 1650'
perf_says "perf: a disabled deep C-state is not flagged" "${good}" 'note: enabled idle states' negate
perf_says "perf: PL1 above thermal spec power is ok" "${good}" 'ok: +intel-rapl:0 package-0: long_term 65 W, short_term 117 W \(thermal spec power 35 W\)'
perf_says "perf: irqbalance found in /proc/*/comm" "${good}" 'ok: +irqbalance is running'
perf_says "perf: overlay2 is ok" "${good}" 'ok: +storage driver overlay2' "" STUB_DOCKER_DRIVER=overlay2
perf_says "perf: model that fits is ok" "${good}" 'ok: +a 30 GiB model \(\+20% headroom = 36\.0 GiB\) fits in MemAvailable 48\.0 GiB' "" K3D_GPU_MODEL_GB=30
perf_says "perf: model that does not fit warns" "${good}" 'warn: a 45 GiB model needs ~54\.0 GiB' "" K3D_GPU_MODEL_GB=45

# Bad host: every knob wrong.
bad=$(newroot perf-bad)
hybrid "${bad}" 0-1 2-3
put "${bad}" /sys/devices/system/cpu/online 0-3
cpufreq "${bad}" 0-1 powersave balance_performance 5100000 5100000
cpufreq "${bad}" 2-3 powersave power 3900000 3900000
put "${bad}" /sys/devices/system/cpu/cpu3/cpufreq/scaling_max_freq 2000000
put "${bad}" /sys/devices/system/cpu/intel_pstate/status active
put "${bad}" /sys/devices/system/cpu/intel_pstate/no_turbo 1
put "${bad}" /sys/devices/system/cpu/intel_pstate/max_perf_pct 80
put "${bad}" /sys/devices/system/cpu/intel_pstate/hwp_dynamic_boost 0
idle "${bad}" 0 POLL 0 0; idle "${bad}" 1 C1E 2 0; idle "${bad}" 2 C6 170 0; idle "${bad}" 3 C10 380 0
rapl "${bad}" intel-rapl:0 1 25000000 35000000 60000000
put "${bad}" /sys/kernel/mm/transparent_hugepage/enabled "always madvise [never]"
put "${bad}" /sys/kernel/mm/transparent_hugepage/defrag "[always] defer defer+madvise madvise never"
meminfo "${bad}" 4194304 8388608 4194304 1024 1024
put "${bad}" /proc/sys/vm/swappiness 100
put "${bad}" /proc/4242/comm sshd
pci "${bad}" 0000:00:02.0 0x8086 0xa780 i915
i915gt "${bad}" 0000:00:02.0 card0 300 300 1200 1000 300 1650
pci "${bad}" 0000:03:00.0 0x8086 0xe20b xe
mkdir -p "${bad}/sys/class/drm/card1"; ln -sfn "${bad}/sys/devices/pci0000:00/0000:03:00.0" "${bad}/sys/class/drm/card1/device"
xf=/sys/devices/pci0000:00/0000:03:00.0/tile0/gt0/freq0
for f in cur:600 act:600 min:600 max:2000 rp0:2850 rpn:400; do put "${bad}" "${xf}/${f%%:*}_freq" "${f#*:}"; done
put "${bad}" "${xf}/power_profile" "base    [power_saving]"
mkdir -p "${bad}/sys/module/nvidia"; touch "${bad}/dev/nvidiactl"
BAD_ENV=(STUB_DOCKER_DRIVER=vfs "STUB_NVSMI=0, NVIDIA RTX 4000 SFF Ada Generation, Disabled;1, NVIDIA RTX 2000E Ada Generation, Enabled")

C=/sys/devices/system/cpu
perf_says "perf: low-energy EPP on E-cores warns" "${bad}" 'warn: E-cores 2-3: EPP power \(governor powersave\)' "" "${BAD_ENV[@]}"
perf_says "perf: EPP apply targets the E-cores" "${bad}" "apply:  echo performance \| sudo tee ${C}/cpu\{2\.\.3\}/cpufreq/energy_performance_preference" "" "${BAD_ENV[@]}"
perf_says "perf: EPP revert restores the exact old value" "${bad}" "revert: echo power \| sudo tee ${C}/cpu\{2\.\.3\}/cpufreq/energy_performance_preference" "" "${BAD_ENV[@]}"
perf_says "perf: balance_performance P-cores are ok" "${bad}" 'ok: +P-cores 0-1: governor powersave, EPP balance_performance' "" "${BAD_ENV[@]}"
perf_says "perf: a capped CPU is named with its cap" "${bad}" 'warn: E-cores 3: scaling_max_freq below the hardware max \(cpu:MHz 3:2000; hardware max 3900 MHz\)' "" "${BAD_ENV[@]}"
perf_says "perf: frequency cap revert restores the exact old value" "${bad}" "revert: echo 2000000 \| sudo tee ${C}/cpu3/cpufreq/scaling_max_freq" "" "${BAD_ENV[@]}"
perf_says "perf: no_turbo=1 warns with apply and revert" "${bad}" "revert: echo 1 \| sudo tee ${C}/intel_pstate/no_turbo" "" "${BAD_ENV[@]}"
perf_says "perf: max_perf_pct cap warns" "${bad}" 'warn: max_perf_pct=80' "" "${BAD_ENV[@]}"
perf_says "perf: deep C-states listed with indices" "${bad}" "apply:  echo 1 \| sudo tee ${C}/cpu\*/cpuidle/state\{2,3\}/disable" "" "${BAD_ENV[@]}"
perf_says "perf: PL1 under thermal spec power warns" "${bad}" 'warn: intel-rapl:0 package-0: PL1 25 W is below the 35 W thermal spec power' "" "${BAD_ENV[@]}"
perf_says "perf: PL1 revert puts the old limit back" "${bad}" 'revert: echo 25000000 \| sudo tee /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw' "" "${BAD_ENV[@]}"
perf_says "perf: THP never warns" "${bad}" 'warn: THP enabled=never' "" "${BAD_ENV[@]}"
perf_says "perf: THP defrag=always warns" "${bad}" 'warn: THP defrag=always' "" "${BAD_ENV[@]}"
perf_says "perf: reserved huge pages are counted" "${bad}" 'note: 1024 huge pages reserved, 1024 free = 2\.0 GiB' "" "${BAD_ENV[@]}"
perf_says "perf: swap in use warns" "${bad}" 'warn: 4\.0 GiB of swap in use' "" "${BAD_ENV[@]}"
perf_says "perf: swapoff not offered when RAM cannot hold the swap" "${bad}" 'swapoff' negate "${BAD_ENV[@]}"
perf_says "perf: high swappiness noted with exact revert" "${bad}" 'revert: sudo sysctl vm.swappiness=100' "" "${BAD_ENV[@]}"
perf_says "perf: missing irqbalance noted" "${bad}" 'note: irqbalance is not running' "" "${BAD_ENV[@]}"
perf_says "perf: i915 host RPS reported without SLPC" "${bad}" 'card0 i915 gt0: host RPS \(no GuC SLPC\)' "" "${BAD_ENV[@]}"
perf_says "perf: i915 max below RP0 warns" "${bad}" 'revert: echo 1200 \| sudo tee /sys/class/drm/card0/gt/gt0/rps_max_freq_mhz' "" "${BAD_ENV[@]}"
perf_says "perf: i915 boost below RP0 warns" "${bad}" 'warn: card0 gt0: boost 1000 MHz is below RP0 1650 MHz' "" "${BAD_ENV[@]}"
perf_says "perf: xe max below RP0 warns" "${bad}" 'warn: card1: max 2000 MHz is below RP0 2850 MHz' "" "${BAD_ENV[@]}"
perf_says "perf: xe power_saving profile warns" "${bad}" 'apply:  echo base \| sudo tee /sys/class/drm/card1/device/tile0/gt0/freq0/power_profile' "" "${BAD_ENV[@]}"
perf_says "perf: NVIDIA persistence off warns for that GPU only" "${bad}" 'warn: GPU 0 NVIDIA RTX 4000 SFF Ada Generation: persistence Disabled' "" "${BAD_ENV[@]}"
perf_says "perf: NVIDIA GPU with persistence on is ok" "${bad}" 'ok: +GPU 1 NVIDIA RTX 2000E Ada Generation: persistence Enabled' "" "${BAD_ENV[@]}"
perf_says "perf: vfs storage driver warns" "${bad}" 'warn: storage driver vfs' "" "${BAD_ENV[@]}"

# Raw EPP numbers: 0..128 is the performance side, above that favours energy.
r=$(newroot perf-epp-64)
put "${r}" /sys/devices/system/cpu/online 0-1
cpufreq "${r}" 0-1 powersave 64 3000000 3000000
perf_says "perf: raw EPP 64 is ok" "${r}" 'ok: +CPUs 0-1: governor powersave, EPP 64'
r=$(newroot perf-epp-200)
put "${r}" /sys/devices/system/cpu/online 0-1
cpufreq "${r}" 0-1 powersave 200 3000000 3000000
perf_says "perf: raw EPP 200 is flagged" "${r}" 'warn: CPUs 0-1: EPP 200'

# Non-HWP host: the generic powersave governor pins the lowest frequency.
r=$(newroot perf-acpi)
put "${r}" /sys/devices/system/cpu/online 0-1
cpufreq "${r}" 0-1 powersave "" 3000000 3000000
for c in 0 1; do put "${r}" "/sys/devices/system/cpu/cpu${c}/cpufreq/scaling_driver" acpi-cpufreq; done
perf_says "perf: acpi-cpufreq powersave governor warns" "${r}" 'warn: CPUs 0-1: governor powersave'
perf_says "perf: empty host has nothing to suggest" "$(newroot perf-empty)" '^==> 0 suggestion\(s\)\. Read-only'

# intel_pstate active without HWP: its own powersave algorithm scales with load.
r=$(newroot perf-pstate-nohwp)
put "${r}" /sys/devices/system/cpu/online 0-1
put "${r}" /sys/devices/system/cpu/intel_pstate/status active
cpufreq "${r}" 0-1 powersave "" 3000000 3000000
perf_says "perf: intel_pstate non-HWP powersave is ok" "${r}" 'ok: +CPUs 0-1: governor powersave \(intel_pstate active, no HWP' 
perf_says "perf: intel_pstate non-HWP powersave is not flagged" "${r}" 'warn: CPUs 0-1: governor' negate

# EPP mixed within a group: classified per CPU, revert restores each CPU's value.
r=$(newroot perf-epp-mixed-ok)
put "${r}" /sys/devices/system/cpu/online 0-2
cpufreq "${r}" 0 powersave performance 3000000 3000000
cpufreq "${r}" 1 powersave balance_performance 3000000 3000000
cpufreq "${r}" 2 powersave 64 3000000 3000000
perf_says "perf: mixed EPP that is all ok is not flagged" "${r}" '^==> 0 suggestion\(s\)\. Read-only'
r=$(newroot perf-epp-mixed-bad)
put "${r}" /sys/devices/system/cpu/online 0-4
cpufreq "${r}" 0 powersave power 3000000 3000000
cpufreq "${r}" 1 powersave balance_power 3000000 3000000
cpufreq "${r}" 2 powersave performance 3000000 3000000
cpufreq "${r}" 3 powersave power 3000000 3000000
cpufreq "${r}" 4 performance power 3000000 3000000
perf_says "perf: mixed EPP warns on the low-energy CPUs only" "${r}" 'warn: CPUs 0-1,3: EPP balance_power on 1, power on 0,3 \(governor powersave\)'
perf_says "perf: mixed EPP apply targets those CPUs" "${r}" "apply:  echo performance \| sudo tee ${C}/cpu\{0,1,3\}/cpufreq/energy_performance_preference"
perf_says "perf: mixed EPP revert restores each CPU's value" "${r}" "revert: echo balance_power \| sudo tee ${C}/cpu1/cpufreq/energy_performance_preference && echo power \| sudo tee ${C}/cpu\{0,3\}/cpufreq/energy_performance_preference"
perf_says "perf: mixed EPP revert is never a placeholder" "${r}" 'values were mixed' negate
r=$(newroot perf-gov-mixed)
put "${r}" /sys/devices/system/cpu/online 0-2
cpufreq "${r}" 0-2 powersave "" 3000000 3000000
put "${r}" /sys/devices/system/cpu/cpu1/cpufreq/scaling_governor ondemand
put "${r}" /sys/devices/system/cpu/cpu2/cpufreq/scaling_governor schedutil
for c in 0 1 2; do put "${r}" "/sys/devices/system/cpu/cpu${c}/cpufreq/scaling_driver" acpi-cpufreq; done
perf_says "perf: mixed governor revert restores each CPU's value" "${r}" "revert: echo ondemand \| sudo tee ${C}/cpu1/cpufreq/scaling_governor && echo powersave \| sudo tee ${C}/cpu0/cpufreq/scaling_governor"

# nvidia-smi that fails (NVML mismatch after a driver upgrade) is reported, not parsed.
NVML_ENV=(STUB_NVSMI_RC=9 "STUB_NVSMI=Failed to initialize NVML: Driver/library version mismatch")
perf_says "perf: nvidia-smi failure is reported" "${bad}" 'warn: nvidia-smi failed \(exit 9\): Failed to initialize NVML: Driver/library version mismatch' "" "${NVML_ENV[@]}"
perf_says "perf: nvidia-smi error text is not parsed as a GPU" "${bad}" 'GPU Failed|-i Failed' negate "${NVML_ENV[@]}"

# Unparsable or missing sysfs values never abort the run.
r=$(newroot perf-bad-sysfs)
hybrid "${r}" 0-2 3
cpufreq "${r}" 0-1 powersave performance 5100000 5100000
cpufreq "${r}" 3 powersave performance 3900000 3900000
put "${r}" /sys/devices/system/cpu/cpu1/cpufreq/cpuinfo_max_freq '<unknown>'
perf_says "perf: non-numeric cpuinfo_max_freq does not abort" "${r}" '^==> [0-9]+ suggestion\(s\)\. Read-only'
rapl "${r}" intel-rapl:0 1 65000000 35000000 '<unknown>'
perf_says "perf: non-numeric RAPL limit does not abort" "${r}" 'ok: +intel-rapl:0 package-0: long_term 65 W, short_term <unknown>'
perf_says "perf: offline CPU adds no 0 MHz hardware max" "${r}" 'ok: +P-cores 0-2: no frequency cap \(hardware max 5100 MHz\)'

# irqbalance revert matches the observed unit state.
r=$(newroot perf-irq-enabled)
put "${r}" /proc/4242/comm sshd
mkdir -p "${r}/etc/systemd/system/multi-user.target.wants"
ln -s /usr/lib/systemd/system/irqbalance.service "${r}/etc/systemd/system/multi-user.target.wants/irqbalance.service"
perf_says "perf: irqbalance enabled but stopped reverts with stop only" "${r}" 'revert: sudo systemctl stop irqbalance$'
perf_says "perf: irqbalance disabled reverts with disable --now" "${bad}" 'revert: sudo systemctl disable --now irqbalance$' "" "${BAD_ENV[@]}"
perf_says "perf: devicemapper named as removed in Engine 25.0" "${good}" 'devicemapper as removed in Docker Engine 25\.0' "" STUB_DOCKER_DRIVER=devicemapper

# ---- doctor --perf: thermal, RAPL inverse, DRAM, KSM, PCIe, isolation, PPD, iGPU RP1 --
# Values of the hot host follow the k3d-gpu development host (13th Gen i9-13900T,
# tests/fixtures/raptorlake-nvidia); the cool host is the same box tuned.

# msrset <root> <address> <16 hex digits>: one MSR, little-endian at its address in
# <root>/dev/cpu/0/msr, as the msr driver serves it.
msrset() {
    local f="$1/dev/cpu/0/msr" le="" i
    mkdir -p "$(dirname "${f}")"
    [ -f "${f}" ] || head -c 2442 /dev/zero > "${f}"
    for ((i = 14; i >= 0; i -= 2)); do le+="\\x${3:i:2}"; done
    printf '%b' "${le}" | dd of="${f}" bs=1 seek=$(($2)) conv=notrunc status=none
}

# dimm <root> <entry> <size MiB> <type> <form> <rated MT/s> <configured MT/s> <total width> <data width> <ranks>:
# one SMBIOS type 17 record (92-byte formatted area, empty string set) at
# /sys/firmware/dmi/entries/17-<entry>/raw. size 0 = empty slot.
dimm() {
    local -a b
    local i out="" sz=$3
    for ((i = 0; i < 92; i++)); do b[i]=0; done
    b[0]=17; b[1]=92
    b[8]=$(($8 & 255)); b[9]=$(($8 >> 8)); b[10]=$(($9 & 255)); b[11]=$(($9 >> 8))
    if [ "${sz}" -ge 32767 ]; then
        b[12]=255; b[13]=127; b[28]=$((sz & 255)); b[29]=$(((sz >> 8) & 255)); b[30]=$(((sz >> 16) & 255))
    else
        b[12]=$((sz & 255)); b[13]=$((sz >> 8))
    fi
    b[14]=$5; b[18]=$4
    b[21]=$(($6 & 255)); b[22]=$(($6 >> 8)); b[27]=${10}; b[32]=$(($7 & 255)); b[33]=$(($7 >> 8))
    for ((i = 0; i < 92; i++)); do out+=$(printf '\\x%02x' "${b[i]}"); done
    mkdir -p "$1/sys/firmware/dmi/entries/17-$2"
    printf '%b\0\0' "${out}" > "$1/sys/firmware/dmi/entries/17-$2/raw"
}

# gpu_behind <root> <port bdf> <port speed> <port width> <gpu bdf> <vendor> <device> <speed> <width> <group>:
# a GPU under a PCIe port, both in IOMMU group <group>.
gpu_behind() {
    local p="$1/sys/devices/pci0000:00/$2" g="$1/sys/devices/pci0000:00/$2/$5" ig="$1/sys/kernel/iommu_groups/${10}"
    mkdir -p "${p}" "${g}" "$1/sys/bus/pci/devices" "${ig}/devices"
    echo 0x8086 > "${p}/vendor"; echo 0x060400 > "${p}/class"; echo "$3" > "${p}/max_link_speed"; echo "$4" > "${p}/max_link_width"
    echo "$6" > "${g}/vendor"; echo "$7" > "${g}/device"; echo 0x030000 > "${g}/class"
    echo "$8" > "${g}/max_link_speed"; echo "$9" > "${g}/max_link_width"
    ln -sfn "${p}" "$1/sys/bus/pci/devices/$2"; ln -sfn "${g}" "$1/sys/bus/pci/devices/$5"
    ln -sfn "${ig}" "${p}/iommu_group"; ln -sfn "${ig}" "${g}/iommu_group"
    ln -sfn "${p}" "${ig}/devices/$2"; ln -sfn "${g}" "${ig}/devices/$5"
    echo identity > "${ig}/type"
}

# g10_common <root>: what the hot and the cool host share.
g10_common() {
    local c
    kernel "$1" 6.6.128-1-MANJARO
    printf 'processor\t: 0\ncpu family\t: 6\nmodel\t\t: 183\nmodel name\t: 13th Gen Intel(R) Core(TM) i9-13900T\nflags\t\t: fpu vmx ept split_lock_detect user_shstk avx_vnni tme ibt\n' > "$1/proc/cpuinfo"
    put "$1" /proc/uptime "2084121.99 61526561.14"
    put "$1" /sys/class/hwmon/hwmon4/name coretemp
    put "$1" /sys/class/hwmon/hwmon4/temp1_label "Package id 0"
    put "$1" /sys/class/hwmon/hwmon4/temp1_crit 100000
    put "$1" /sys/class/hwmon/hwmon4/temp1_input 96000
    pci "$1" 0000:00:04.0 0x8086 0xa71d proc_thermal_pci 0x118000
    put "$1" /sys/devices/pci0000:00/0000:00:04.0/tcc_offset_degree_celsius 5
    pci "$1" 0000:00:00.0 0x8086 0xa700 "" 0x060000
    put "$1" /proc/mounts "/dev/nvme1n1p2 / ext4 rw,noatime 0 0"
    touch "$1/dev/kvm"
    for c in nested:Y enable_apicv:Y ept:Y; do put "$1" "/sys/module/kvm_intel/parameters/${c%%:*}" "${c#*:}"; done
    mkdir -p "$1/sys/class/iommu/dmar0"
    put "$1" /proc/sys/kernel/split_lock_mitigate 1
    for c in 0 1 2 3; do dimm "$1" "${c}" 32768 34 13 4800 3600 80 64 2; done
}

# Hot host: throttling, PL1 3.3x TDP, ECC unmonitored, x4-capped GPU with a small
# BAR sharing its IOMMU group, KSM on, paranoid 0, split-lock detection off, PPD.
hot=$(newroot perf-hot)
g10_common "${hot}"
TT=/sys/devices/system/cpu/cpu0/thermal_throttle
put "${hot}" "${TT}/package_throttle_count" 22048145
put "${hot}" "${TT}/package_throttle_total_time_ms" 101468880
put "${hot}" "${TT}/package_throttle_max_time_ms" 224
put "${hot}" "${TT}/core_throttle_count" 645409
put "${hot}" /sys/devices/system/cpu/cpu8/thermal_throttle/core_throttle_count 8495804
rapl "${hot}" intel-rapl:0 1 117000000 35000000 117000000
msrset "${hot}" 0x19C 0000000088143a88
msrset "${hot}" 0x1B1 00000000880b0a88
msrset "${hot}" 0x64F 0000000019030100
msrset "${hot}" 0x6A2 0000000000000004
msrset "${hot}" 0x982 0000000000000001
put "${hot}" /sys/kernel/mm/transparent_hugepage/enabled "[always] madvise never"
put "${hot}" /sys/kernel/mm/ksm/run 1
put "${hot}" /sys/kernel/mm/ksm/pages_sharing 4096
gpu_behind "${hot}" 0000:00:06.0 "16.0 GT/s PCIe" 4 0000:02:00.0 0x10de 0x28b0 "16.0 GT/s PCIe" 8 4
G2=/sys/devices/pci0000:00/0000:00:06.0/0000:02:00.0
printf '0x0000000093000000 0x0000000093ffffff 0x0000000000040200\n0x0000006c00000000 0x0000006c0fffffff 0x000000000014220c\n' > "${hot}${G2}/resource"
put "${hot}" "${G2}/resource1_resize" 0000000000007fc0
pci "${hot}" 0000:03:00.0 0x144d 0xa80a nvme 0x010802
ln -sfn "${hot}/sys/devices/pci0000:00/0000:03:00.0" "${hot}/sys/kernel/iommu_groups/4/devices/0000:03:00.0"
gpu_behind "${hot}" 0000:00:01.0 "32.0 GT/s PCIe" 16 0000:01:00.0 0x10de 0x27b0 "16.0 GT/s PCIe" 16 2
put "${hot}" /proc/sys/dev/i915/perf_stream_paranoid 0
put "${hot}" /proc/cmdline "BOOT_IMAGE=/vmlinuz split_lock_detect=off quiet"
put "${hot}" /sys/devices/system/cpu/intel_pstate/status active
cpufreq "${hot}" 0-1 powersave performance 5100000 5100000
put "${hot}" /proc/19859/comm power-profiles-
pci "${hot}" 0000:00:02.0 0x8086 0xa780 i915 0x038000
i915gt "${hot}" 0000:00:02.0 card0 1650 300 1650 1650 300 1650 slpc
put "${hot}" /sys/class/drm/card0/gt/gt0/rps_RP1_freq_mhz 700
printf '\tSLPC state: running\n\twaitboosts: 1234\n' > "${hot}/guc_slpc_info"
mkdir -p "${hot}/sys/kernel/debug/dri/0/gt0/uc"; mv "${hot}/guc_slpc_info" "${hot}/sys/kernel/debug/dri/0/gt0/uc/"
HOT_ENV=("STUB_NVSMI_TOPO= 	GPU0	GPU1;GPU0	X	NS;GPU1	NS	X")

PC=/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw
perf_says "perf: throttle share of uptime warns" "${hot}" 'warn: package thermally throttled 4% of uptime: 22048145 events, 101468880 ms in total, longest 224 ms; core events peak at 8495804 on cpu8' "" "${HOT_ENV[@]}"
perf_says "perf: TCC activation from TjMax and offset" "${hot}" 'info: TCC activation 95 C \(TjMax 100 C - TCC offset 5 C\); package now 96 C' "" "${HOT_ENV[@]}"
perf_says "perf: 0x1B1 decoded, logged bits named" "${hot}" 'MSR 0x1B1 IA32_PACKAGE_THERM_STATUS 0x00000000880b0a88: now none; logged PROCHOT, threshold #1, threshold #2, power limit; 11 C below TjMax' "" "${HOT_ENV[@]}"
perf_says "perf: 0x19C decoded with current limit" "${hot}" 'MSR 0x19C IA32_THERM_STATUS \(CPU 0\) 0x0000000088143a88: now current limit; logged PROCHOT, threshold #1, threshold #2, power limit, current limit; 20 C below TjMax' "" "${HOT_ENV[@]}"
perf_says "perf: 0x64F decoded per SDM table 2-39" "${hot}" 'MSR 0x64F MSR_CORE_PERF_LIMIT_REASONS 0x0000000019030100: now other \(electrical\); logged PROCHOT, thermal, other \(electrical\), PL2, max turbo limit$' "" "${HOT_ENV[@]}"
perf_says "perf: sticky log bits are only read" "${hot}" 'the log bits are sticky' "" "${HOT_ENV[@]}"
perf_says "perf: PL1 far above TDP while throttling warns" "${hot}" 'warn: intel-rapl:0 package-0: PL1 117 W is 3\.3x the 35 W thermal spec power while the package is thermally throttled 4% of uptime' "" "${HOT_ENV[@]}"
perf_says "perf: PL1 inverse apply is 2x TDP" "${hot}" "apply:  echo 70000000 \| sudo tee ${PC}" "" "${HOT_ENV[@]}"
perf_says "perf: PL1 inverse revert restores 117 W" "${hot}" "revert: echo 117000000 \| sudo tee ${PC}" "" "${HOT_ENV[@]}"
perf_says "perf: DRAM population decoded from SMBIOS" "${hot}" 'info: 4x DDR5 SODIMM 32 GiB 2R, ECC \(80/64-bit\): rated 4800 MT/s, configured 3600 MT/s; 4 of 4 slots populated' "" "${HOT_ENV[@]}"
perf_says "perf: SODIMM 2DPC on the S line is off-POR (note 9)" "${hot}" 'not a supported configuration on the 13th Gen Core S line \(743844 table 25 note 9\); the nearest POR row, UDIMM 2DPC, 2 DIMMs 2R, is 3600 MT/s' "" "${HOT_ENV[@]}"
perf_says "perf: configured speed at POR is ok" "${hot}" 'ok: +configured 3600 MT/s is at the 3600 MT/s POR for 2DPC, 2 DIMMs 2R' "" "${HOT_ENV[@]}"
perf_says "perf: 1DPC alternative capped by the rated speed" "${hot}" 'would run at 4800 MT/s \(the lower of rated 4800 and the 1DPC 2R POR\): 76\.8 GB/s peak' "" "${HOT_ENV[@]}"
perf_says "perf: peak DRAM bandwidth computed" "${hot}" 'info: peak DRAM bandwidth 57\.6 GB/s = 2 channels x 3600 MT/s x 8 B' "" "${HOT_ENV[@]}"
perf_says "perf: ECC without EDAC is unmonitored" "${hot}" 'note: ECC modules, but no EDAC memory controller is registered.*dram\.ecc=unmonitored' "" "${HOT_ENV[@]}"
perf_says "perf: EDAC apply names ie31200_edac from 6.16" "${hot}" 'apply:  boot Linux 6\.16 or newer \(ie31200_edac binds host bridge 8086:a700 from 6\.16\), then: sudo modprobe ie31200_edac' "" "${HOT_ENV[@]}"
# ie31200_edac binds 8086:a703/4640/4630 from 6.15, 8086:a700/4660 from 6.16.
r=$(newroot perf-edac-a703)
g10_common "${r}"
pci "${r}" 0000:00:00.0 0x8086 0xa703 "" 0x060000
perf_says "perf: EDAC for 8086:a703 needs 6.15" "${r}" 'apply:  boot Linux 6\.15 or newer \(ie31200_edac binds host bridge 8086:a703 from 6\.15\), then: sudo modprobe ie31200_edac'
kernel "${r}" 6.15.2
perf_says "perf: EDAC for 8086:a703 on 6.15 loads directly" "${r}" 'apply:  sudo modprobe ie31200_edac$'
r=$(newroot perf-edac-4660)
g10_common "${r}"
pci "${r}" 0000:00:00.0 0x8086 0x4660 "" 0x060000
perf_says "perf: EDAC for 8086:4660 needs 6.16" "${r}" 'apply:  boot Linux 6\.16 or newer \(ie31200_edac binds host bridge 8086:4660 from 6\.16\)'
perf_says "perf: ext4 on 6.6 maps weights with 4 KiB pages" "${hot}" 'note: root filesystem is ext4 on Linux 6\.6\.128-1-MANJARO: model files mmapped from it are mapped with 4 KiB pages' "" "${HOT_ENV[@]}"
perf_says "perf: huge tmpfs apply and revert" "${hot}" 'revert: sudo umount /mnt/k3d-gpu-models' "" "${HOT_ENV[@]}"
perf_says "perf: huge tmpfs sized from --model-gb" "${hot}" 'huge=within_size,size=31G' "" "${HOT_ENV[@]}" K3D_GPU_MODEL_GB=30
perf_says "perf: KSM running noted with unmerge apply" "${hot}" 'apply:  echo 2 \| sudo tee /sys/kernel/mm/ksm/run' "" "${HOT_ENV[@]}"
perf_says "perf: GPU behind an x4 port warns" "${hot}" 'warn: 0000:02:00\.0 \[10de:28b0\]: device Gen4 x8, upstream port 0000:00:06\.0 Gen4 x4: the link trains at most Gen4 x4' "" "${HOT_ENV[@]}"
perf_says "perf: GPU at its own maximum is ok" "${hot}" 'ok: +0000:01:00\.0 \[10de:27b0\]: Gen4 x16 behind port 0000:00:01\.0 \(Gen5 x16\)' "" "${HOT_ENV[@]}"
perf_says "perf: small BAR with ReBAR capability warns" "${hot}" 'warn: 0000:02:00\.0 \[10de:28b0\]: BAR1 is 256 MiB; the device can map up to 16 GiB' "" "${HOT_ENV[@]}"
perf_says "perf: P2P NS reported from nvidia-smi topo" "${hot}" 'info: GPU peer-to-peer reads: GPU0-GPU1 NS' "" "${HOT_ENV[@]}"
perf_says "perf: shared IOMMU group warns, bridges ignored" "${hot}" 'warn: 0000:02:00\.0: IOMMU group 4 also holds 0000:03:00\.0 \(class 0x010802\)$' "" "${HOT_ENV[@]}"
perf_says "perf: GPU alone in its group is ok" "${hot}" 'ok: +0000:01:00\.0: alone in IOMMU group 2' "" "${HOT_ENV[@]}"
perf_says "perf: KVM reported with kvm_intel params" "${hot}" 'info: /dev/kvm present; kvm_intel nested=Y enable_apicv=Y ept=Y' "" "${HOT_ENV[@]}"
perf_says "perf: kernel IBT from IA32_S_CET" "${hot}" 'IA32_S_CET 0x0000000000000004: kernel IBT on' "" "${HOT_ENV[@]}"
perf_says "perf: split_lock_detect=off warns" "${hot}" 'warn: split-lock detection is off' "" "${HOT_ENV[@]}"
perf_says "perf: TME capable but locked off" "${hot}" 'TME capable but off \(IA32_TME_ACTIVATE 0x0000000000000001: lock=1 enable=0; mem-encryption=tme-capable-off\)' "" "${HOT_ENV[@]}"
perf_says "perf: perf_stream_paranoid=0 warns with revert" "${hot}" 'revert: sudo sysctl dev\.i915\.perf_stream_paranoid=0' "" "${HOT_ENV[@]}"
perf_says "perf: power-profiles-daemon override noted" "${hot}" 'note: power-profiles-daemon is running: it sets the governor and EPP itself' "" "${HOT_ENV[@]}"
perf_says "perf: PPD revert restarts the daemon" "${hot}" 'revert: sudo systemctl start power-profiles-daemon' "" "${HOT_ENV[@]}"
perf_says "perf: i915 RP1 in the frequency line" "${hot}" 'RPn 300, RP1 700, RP0 1650' "" "${HOT_ENV[@]}"
perf_says "perf: SLPC floor at RP1 when eff freq not ignored" "${hot}" 'GuC SLPC keeps the efficient frequency RP1 700 MHz as its floor while busy' "" "${HOT_ENV[@]}"
perf_says "perf: SLPC waitboost count from debugfs" "${hot}" 'info: card0 gt0: 1234 waitboosts since boot' "" "${HOT_ENV[@]}"

# Cool host: same box with every G10 check where it should be.
cool=$(newroot perf-cool)
g10_common "${cool}"
put "${cool}" "${TT}/package_throttle_count" 0
put "${cool}" "${TT}/package_throttle_total_time_ms" 0
rapl "${cool}" intel-rapl:0 1 117000000 35000000 117000000
put "${cool}" /sys/kernel/mm/ksm/run 0
mkdir -p "${cool}/sys/devices/system/edac/mc/mc0"
put "${cool}" /sys/devices/system/edac/mc/mc0/mc_name "Raptor Lake-S"
put "${cool}" /sys/devices/system/edac/mc/mc0/ce_count 0
put "${cool}" /sys/devices/system/edac/mc/mc0/ue_count 0
put "${cool}" /proc/mounts "/dev/nvme1n1p2 / xfs rw,noatime 0 0"
gpu_behind "${cool}" 0000:00:01.0 "32.0 GT/s PCIe" 16 0000:01:00.0 0x10de 0x27b0 "16.0 GT/s PCIe" 16 2
G1=/sys/devices/pci0000:00/0000:00:01.0/0000:01:00.0
printf '0x0000000095000000 0x0000000095ffffff 0x0000000000040200\n0x0000006000000000 0x00000067ffffffff 0x000000000014220c\n' > "${cool}${G1}/resource"
put "${cool}" "${G1}/resource1_resize" 000000000000ffc0
put "${cool}" /proc/sys/dev/i915/perf_stream_paranoid 1
perf_says "perf: cool G10 host has no suggestions" "${cool}" '^==> 0 suggestion\(s\)\. Read-only'
perf_says "perf: no throttle events is ok" "${cool}" 'ok: +no package throttle events since boot'
perf_says "perf: high PL1 without throttling is ok" "${cool}" 'ok: +intel-rapl:0 package-0: long_term 117 W'
perf_says "perf: ECC with EDAC is active" "${cool}" 'ok: +ECC modules with EDAC reporting \(dram\.ecc=active\): mc0 Raptor Lake-S ce 0 ue 0'
perf_says "perf: full BAR is ok" "${cool}" 'ok: +0000:01:00\.0 \[10de:27b0\]: BAR1 32 GiB, the largest size it supports'
perf_says "perf: KSM off is ok" "${cool}" 'ok: +KSM off \(run=0\)'
perf_says "perf: split-lock default warn is ok" "${cool}" 'ok: +split-lock detection warn \(default\), split_lock_mitigate=1'
perf_says "perf: MSRs skipped without root" "${cool}" 'info: MSR decode \(0x19C, 0x1B1, 0x64F\) skipped: /dev/cpu/0/msr needs root'
perf_says "perf: TME state needs root without the MSR" "${cool}" 'tme flag set: TME capable\. Whether firmware enabled it needs root'
perf_says "perf: xfs root gets no 4 KiB note" "${cool}" 'mapped with 4 KiB pages' negate
perf_says "perf: missing /proc/cmdline prints no shell error" "${cool}" 'No such file|line [0-9]+:' negate

# A card with its own PCIe switch (Arc A/B dGPUs: root port -> card upstream port
# -> downstream port -> GPU): the slot link is the upstream port's, two hops above
# the GPU. Link values below are synthetic.
# hop <root> <path under pci0000:00> <class> <speed> <width> [vendor device]
hop() {
    local d="$1/sys/devices/pci0000:00/$2"
    mkdir -p "${d}" "$1/sys/bus/pci/devices"
    echo "${6:-0x8086}" > "${d}/vendor"; echo "${7:-0x0000}" > "${d}/device"; echo "$3" > "${d}/class"
    echo "$4" > "${d}/max_link_speed"; echo "$5" > "${d}/max_link_width"
    ln -sfn "${d}" "$1/sys/bus/pci/devices/${2##*/}"
}
arc_card() {
    kernel "$1" 6.6.0
    hop "$1" 0000:00:01.0 0x060400 "16.0 GT/s PCIe" "$2"
    hop "$1" 0000:00:01.0/0000:01:00.0 0x060400 "16.0 GT/s PCIe" 8
    hop "$1" 0000:00:01.0/0000:01:00.0/0000:02:01.0 0x060400 "2.5 GT/s PCIe" 1
    hop "$1" 0000:00:01.0/0000:01:00.0/0000:02:01.0/0000:03:00.0 0x030000 "2.5 GT/s PCIe" 1 0x8086 0xe20b
}
r=$(newroot perf-arc-x4)
arc_card "${r}" 4
perf_says "perf: card switch in an x4 slot warns at the slot link" "${r}" 'warn: 0000:03:00\.0 \[8086:e20b\]: 0000:01:00\.0 Gen4 x8 on its path to the root port sits behind port 0000:00:01\.0 Gen4 x4: the GPU.s traffic crosses a link of at most Gen4 x4'
perf_says "perf: card switch in an x4 slot gets no ok link line" "${r}" 'ok: +0000:03:00\.0 \[8086:e20b\]: Gen' negate
r=$(newroot perf-arc-x16)
arc_card "${r}" 16
perf_says "perf: card switch in an x16 slot is ok up to the root port" "${r}" 'ok: +0000:03:00\.0 \[8086:e20b\]: Gen1 x1 behind port 0000:02:01\.0 \(Gen1 x1\): the link can reach the device maximum; no narrower link up to root port 0000:00:01\.0'
perf_says "perf: card switch in an x16 slot does not warn" "${r}" 'warn: 0000:03:00\.0' negate
# Two narrowing hops: the one nearest the GPU is reported.
r=$(newroot perf-two-hops)
kernel "${r}" 6.6.0
hop "${r}" 0000:00:01.0 0x060400 "16.0 GT/s PCIe" 4
hop "${r}" 0000:00:01.0/0000:01:00.0 0x060400 "16.0 GT/s PCIe" 8
hop "${r}" 0000:00:01.0/0000:01:00.0/0000:02:01.0 0x060400 "8.0 GT/s PCIe" 16
hop "${r}" 0000:00:01.0/0000:01:00.0/0000:02:01.0/0000:03:00.0 0x030000 "16.0 GT/s PCIe" 16 0x8086 0xe20b
perf_says "perf: two narrow hops report the one nearest the GPU" "${r}" 'warn: 0000:03:00\.0 \[8086:e20b\]: device Gen4 x16, upstream port 0000:02:01\.0 Gen3 x16: the link trains at most Gen3 x16'

# IA32_TME_ACTIVATE: active only when locked (bit 0) and enabled (bit 1), as the
# kernel's detect_tme_early() reads it.
r=$(newroot perf-tme-on)
g10_common "${r}"
msrset "${r}" 0x982 0000000000000003
perf_says "perf: TME locked and enabled is active" "${r}" 'info: TME active \(IA32_TME_ACTIVATE 0x0000000000000003: locked, encryption enabled'
r=$(newroot perf-tme-unlocked)
g10_common "${r}"
msrset "${r}" 0x982 0000000000000002
perf_says "perf: TME enabled but unlocked is not active" "${r}" 'TME capable but off \(IA32_TME_ACTIVATE 0x0000000000000002: lock=0 enable=1;'
perf_says "perf: TME enabled but unlocked is not reported active" "${r}" 'TME active' negate

# Uncorrected errors warn; root-only SMBIOS records; a 13th Gen HX below POR; an
# unknown platform falls back to data width.
r=$(newroot perf-ue)
g10_common "${r}"
mkdir -p "${r}/sys/devices/system/edac/mc/mc0"
put "${r}" /sys/devices/system/edac/mc/mc0/ue_count 3
perf_says "perf: uncorrected memory errors warn" "${r}" 'warn: mc0: 3 uncorrected memory errors since boot'
r=$(newroot perf-dmi-user)
kernel "${r}" 6.6.0
mkdir -p "${r}/sys/firmware/dmi/entries/17-0" "${r}/sys/firmware/dmi/entries/17-1"
perf_says "perf: SMBIOS records need root" "${r}" 'info: SMBIOS memory records are root-only'
r=$(newroot perf-hx-slow)
kernel "${r}" 6.6.0
printf 'cpu family\t: 6\nmodel\t\t: 183\nmodel name\t: 13th Gen Intel(R) Core(TM) i9-13980HX\n' > "${r}/proc/cpuinfo"
dimm "${r}" 0 16384 34 13 5600 4800 64 64 1
dimm "${r}" 1 16384 34 13 5600 4800 64 64 1
perf_says "perf: configured below POR warns" "${r}" 'warn: configured 4800 MT/s is below the 5600 MT/s POR for 1DPC 1R \(modules rated 5600\)'
perf_says "perf: below-POR apply names the POR speed" "${r}" 'apply:  BIOS: set the memory frequency to 5600 MT/s'
perf_says "perf: below-POR says the POR is the top SKU's (note 17)" "${r}" 'why: .*POR is the top SKU.s speed \(743844 table 25 note 17\); a lower SKU can be specified lower: check ark\.intel\.com for 13th Gen Intel\(R\) Core\(TM\) i9-13980HX'
perf_says "perf: non-ECC modules reported" "${r}" 'info: non-ECC modules'
# Model 0xBF is the 8+8 (C0) and 6+0 (H0) die: 743844 table 25 note 16 puts it at
# 12th Gen DDR5 speeds, so the 13th/14th Gen POR rows do not apply.
r=$(newroot perf-rpl-bf)
kernel "${r}" 6.6.0
printf 'cpu family\t: 6\nmodel\t\t: 191\nmodel name\t: 13th Gen Intel(R) Core(TM) i5-13400\n' > "${r}/proc/cpuinfo"
dimm "${r}" 0 16384 34 9 5600 4800 64 64 1
dimm "${r}" 1 16384 34 9 5600 4800 64 64 1
perf_says "perf: model 0xBF gets no 13th Gen POR warning (note 16)" "${r}" 'below the|POR for' negate
perf_says "perf: model 0xBF falls back to the data-width peak" "${r}" 'info: peak DRAM bandwidth 76\.8 GB/s = 4800 MT/s x 16 B of data width'
r=$(newroot perf-dmi-nospeed)
kernel "${r}" 6.6.0
printf 'cpu family\t: 6\nmodel\t\t: 183\nmodel name\t: 13th Gen Intel(R) Core(TM) i9-13900T\n' > "${r}/proc/cpuinfo"
dimm "${r}" 0 16384 34 9 0 0 65535 65535 1
perf_says "perf: unreported speed is not compared with POR" "${r}" 'info: configured speed not reported by firmware'
perf_says "perf: unreported speed gives no POR warning or peak" "${r}" 'below the|peak DRAM bandwidth|ECC \(|non-ECC' negate
perf_says "perf: unreported widths leave ECC unknown" "${r}" 'info: module widths not reported by firmware: ECC state unknown'
r=$(newroot perf-4r)
kernel "${r}" 6.6.0
printf 'cpu family\t: 6\nmodel\t\t: 183\nmodel name\t: 13th Gen Intel(R) Core(TM) i9-13900T\n' > "${r}/proc/cpuinfo"
for c in 0 1 2 3; do dimm "${r}" "${c}" 32768 34 13 4800 3600 64 64 4; done
perf_says "perf: a rank with no POR row prints no empty row" "${r}" 'UDIMM ,|is  MT/s|POR for  ' negate
perf_says "perf: a rank with no POR row still gets the peak" "${r}" 'info: peak DRAM bandwidth 57\.6 GB/s'
r=$(newroot perf-xeon-mem)
kernel "${r}" 6.6.0
printf 'cpu family\t: 6\nmodel\t\t: 143\nmodel name\t: Intel(R) Xeon(R) Platinum 8480+\n' > "${r}/proc/cpuinfo"
for c in 0 1 2 3 4 5 6 7; do dimm "${r}" "${c}" 65536 34 9 4800 4800 80 64 2; done
perf_says "perf: unknown platform peak assumes 1 DIMM per channel" "${r}" 'info: peak DRAM bandwidth 307\.2 GB/s = 4800 MT/s x 64 B of data width \(computed, assuming one module per channel'

# Real procfs files report size 0: the isolation checks still run on them.
r=$(newroot perf-procfs)
mkdir -p "${r}/proc"; ln -s /proc/cpuinfo "${r}/proc/cpuinfo"
perf_says "perf: isolation runs on a size-0 procfs cpuinfo" "${r}" '^==> Isolation for sandboxed agents'

# Hot host stays read-only too: MSR file, SMBIOS records and sysfs unchanged, no sudo.
before=$(cd "${hot}" && find . -type f -exec md5sum {} + | sort)
rm -f "${WORK}/sudo-called"
env K3D_GPU_SYSROOT="${hot}" PATH="${STUB_BIN}:${PATH}" "${HOT_ENV[@]}" bash "${K3D_GPU}" doctor --perf >/dev/null 2>&1
after=$(cd "${hot}" && find . -type f -exec md5sum {} + | sort)
if [ "${before}" = "${after}" ] && [ ! -e "${WORK}/sudo-called" ]; then
    PASS=$((PASS + 1)); echo "ok   perf: G10 checks are read-only (tree unchanged, sudo never run)"
else
    FAILS=$((FAILS + 1)); echo "FAIL perf: G10 checks are read-only (tree changed or sudo ran)"
fi

# Read-only: the run leaves the fake tree byte-identical and never calls sudo.
before=$(cd "${bad}" && find . -type f -exec md5sum {} + | sort)
rm -f "${WORK}/sudo-called"
env K3D_GPU_SYSROOT="${bad}" PATH="${STUB_BIN}:${PATH}" "${BAD_ENV[@]}" bash "${K3D_GPU}" doctor --perf >/dev/null 2>&1
after=$(cd "${bad}" && find . -type f -exec md5sum {} + | sort)
if [ "${before}" = "${after}" ] && [ ! -e "${WORK}/sudo-called" ]; then
    PASS=$((PASS + 1)); echo "ok   perf: read-only (tree unchanged, sudo never run)"
else
    FAILS=$((FAILS + 1)); echo "FAIL perf: read-only (tree changed or sudo ran)"
fi

# --model-gb parsing.
out=$(env K3D_GPU_SYSROOT="${good}" PATH="${STUB_BIN}:${PATH}" bash "${K3D_GPU}" doctor --perf --model-gb 30 2>&1)
if grep -q 'a 30 GiB model' <<< "${out}"; then PASS=$((PASS + 1)); echo "ok   perf: --model-gb is read"; else FAILS=$((FAILS + 1)); echo "FAIL perf: --model-gb is read"; fi
if env K3D_GPU_SYSROOT="${good}" bash "${K3D_GPU}" doctor --perf --model-gb lots >/dev/null 2>&1; then
    FAILS=$((FAILS + 1)); echo "FAIL perf: non-numeric --model-gb is rejected"
else
    PASS=$((PASS + 1)); echo "ok   perf: non-numeric --model-gb is rejected"
fi

# ---- agent: device selection and manifest rendering ---------------------------
# `agent manifest` reads the cluster through kubectl. The stub answers the
# allocatable query from STUB_ALLOC ("<resource> <count>" lines) and a label
# selector query with one node when the label is listed in STUB_NODE_LABELS.
ASTUB="${WORK}/abin"
mkdir -p "${ASTUB}"
cat > "${ASTUB}/kubectl" <<'EOF'
#!/bin/sh
[ -z "${STUB_LOG:-}" ] || echo "kubectl $*" >> "${STUB_LOG}"
case "$*" in
    *allocatable*) printf '%s\n' "${STUB_ALLOC:-}" ;;
    "get nodes -l "*)
        for l in ${STUB_NODE_LABELS:-}; do [ "$l" = "$4" ] && echo node/k3d-agent-server-0; done ;;
    apply*) echo applied >> "${STUB_APPLY_LOG:-/dev/null}"
        if [ -n "${STUB_LOG:-}" ]; then cat >> "${STUB_LOG}"; else cat >/dev/null; fi ;;
    *" exec "*) [ -n "${STUB_EXEC:-}" ] || exit 1; printf '%s\n' "${STUB_EXEC}" ;;
    "create --raw "*) cat >/dev/null; [ -n "${STUB_EXEC:-}" ] || exit 1; printf '%s\n' "${STUB_EXEC}" ;;
esac
exit 0
EOF
chmod +x "${ASTUB}/kubectl"

# agent_says <name> <root> <want rc: 0|fail> <ERE> [negate] [env assignments...]: `agent manifest`
# output (stdout + stderr) must match <ERE>, or must not when a 5th argument is given.
agent_says() {
    local name=$1 root=$2 want=$3 re=$4 negate=${5:-} out rc hit=0
    shift 5 2>/dev/null || shift $#
    out=$(env K3D_GPU_SYSROOT="${root}" PATH="${ASTUB}:${PATH}" "$@" bash "${K3D_GPU}" agent manifest 2>&1)
    rc=$?
    grep -E -- "${re}" <<< "${out}" >/dev/null && hit=1
    if { { [ -z "${negate}" ] && [ "${hit}" -eq 1 ]; } || { [ -n "${negate}" ] && [ "${hit}" -eq 0 ]; }; } \
       && { { [ "${want}" = 0 ] && [ "${rc}" -eq 0 ]; } || { [ "${want}" = fail ] && [ "${rc}" -ne 0 ]; }; }; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (rc=${rc}, want ${want}, ${negate:+not }/${re}/)"
        awk '{print "  | " $0}' <<< "${out}"
    fi
}

r=$(newroot agent-igpu)
pci "${r}" 0000:00:02.0 0x8086 0xa780 i915; render "${r}" 0000:00:02.0 128
gid=$(stat -c %g "${r}/dev/dri/renderD128")
agent_says "agent picks GPU when the cluster advertises gpu.intel.com/i915 and XMX" "${r}" 0 \
    '--target_device=GPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true
agent_says "agent GPU pod requests one gpu.intel.com/i915" "${r}" 0 \
    'gpu\.intel\.com/i915: "?1"?' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true
agent_says "agent GPU pod joins the host render group" "${r}" 0 \
    "supplementalGroups: \[${gid}\]" "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true
agent_says "agent picks gpu.intel.com/xe when that is what the cluster has" "${r}" 0 \
    'gpu\.intel\.com/xe: "?1"?' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/xe 2' STUB_NODE_LABELS=gpu.intel.com/xmx=true
agent_says "auto GPU pod is placed on an XMX node" "${r}" 0 \
    'gpu\.intel\.com/xmx: "true"' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/xe 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true
# Measured on an i9-13900T (UHD 770, no XMX): P-cores decode 15.7 tok/s and
# prefill 172 tok/s, the GPU 9.9 and 66. Without XMX auto stays on the CPU.
agent_says "auto picks CPU when the Intel GPU has no XMX" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' STUB_NODE_LABELS=gpu.intel.com/xmx=false
agent_says "auto picks CPU when no node says the GPU has XMX" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/xe 1'
agent_says "auto CPU on a no-XMX GPU node requests no GPU" "${r}" 0 \
    'gpu\.intel\.com' negate STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' STUB_NODE_LABELS=gpu.intel.com/xmx=false
agent_says "K3D_GPU_AGENT_DEVICE=GPU uses a GPU without XMX" "${r}" 0 \
    '--target_device=GPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' K3D_GPU_AGENT_DEVICE=GPU
agent_says "a forced GPU pod is not tied to XMX nodes" "${r}" 0 \
    'gpu\.intel\.com/xmx' negate STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' K3D_GPU_AGENT_DEVICE=GPU
agent_says "agent falls back to CPU when no Intel GPU is allocatable" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 0'
agent_says "agent CPU pod requests no GPU" "${r}" 0 \
    'gpu\.intel\.com' negate STUB_ALLOC='cpu 32'
agent_says "agent ignores a host GPU the cluster does not advertise" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC='cpu 32'
agent_says "K3D_GPU_AGENT_DEVICE=CPU overrides an available GPU" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' K3D_GPU_AGENT_DEVICE=CPU
agent_says "the override is case-insensitive" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' K3D_GPU_AGENT_DEVICE=cpu
agent_says "K3D_GPU_AGENT_DEVICE=GPU with no allocatable Intel GPU fails" "${r}" fail \
    'no Intel GPU' "" STUB_ALLOC='cpu 32' K3D_GPU_AGENT_DEVICE=GPU
agent_says "an unknown K3D_GPU_AGENT_DEVICE is rejected" "${r}" fail \
    'TPU' "" STUB_ALLOC='cpu 32' K3D_GPU_AGENT_DEVICE=TPU
agent_says "auto never picks the NPU" "${r}" 0 \
    '--target_device=NPU' negate STUB_ALLOC=$'cpu 32\nnpu.intel.com/accel 1'

# A device that cannot be honoured must stop `agent up` before anything is applied.
applylog="${WORK}/apply.log"
if env K3D_GPU_SYSROOT="${r}" PATH="${ASTUB}:${PATH}" STUB_ALLOC='cpu 32' STUB_APPLY_LOG="${applylog}" \
       K3D_GPU_AGENT_DEVICE=GPU bash "${K3D_GPU}" agent up >/dev/null 2>&1 || [ -s "${applylog}" ]; then
    FAILS=$((FAILS + 1)); echo "FAIL agent up with an unavailable forced device applies nothing and fails"
else
    PASS=$((PASS + 1)); echo "ok   agent up with an unavailable forced device applies nothing and fails"
fi

r=$(newroot agent-npu)
pci "${r}" 0000:00:0b.0 0x8086 0x643e intel_vpu; accel "${r}" 0000:00:0b.0 0
gid=$(stat -c %g "${r}/dev/accel/accel0")
agent_says "K3D_GPU_AGENT_DEVICE=NPU requests npu.intel.com/accel" "${r}" 0 \
    'npu\.intel\.com/accel: "?1"?' "" STUB_ALLOC=$'cpu 8\nnpu.intel.com/accel 1' K3D_GPU_AGENT_DEVICE=NPU
agent_says "NPU pod joins the accel node's group" "${r}" 0 \
    "supplementalGroups: \[${gid}\]" "" STUB_ALLOC=$'cpu 8\nnpu.intel.com/accel 1' K3D_GPU_AGENT_DEVICE=NPU
agent_says "K3D_GPU_AGENT_DEVICE=NPU with no allocatable NPU fails" "${r}" fail \
    'no Intel NPU' "" STUB_ALLOC='cpu 8' K3D_GPU_AGENT_DEVICE=NPU

r=$(newroot agent-hybrid)
hybrid "${r}" 0-15 16-31
pci "${r}" 0000:00:02.0 0x8086 0xa780 i915; render "${r}" 0000:00:02.0 128
agent_says "on a hybrid node the server pod is pinned to P-cores" "${r}" 0 \
    'k3d-gpu.io/cores: performance' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' STUB_NODE_LABELS=k3d-gpu.io/hybrid-cpu=true
agent_says "auto on a hybrid node with a no-XMX GPU runs on the CPU" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' \
    STUB_NODE_LABELS='k3d-gpu.io/hybrid-cpu=true gpu.intel.com/xmx=false'
agent_says "auto on a hybrid node with a no-XMX GPU takes every P-core" "${r}" 0 \
    'cpu: "16"' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' \
    STUB_NODE_LABELS='k3d-gpu.io/hybrid-cpu=true gpu.intel.com/xmx=false'
agent_says "a non-hybrid cluster gets no core-type label" "${r}" 0 \
    'k3d-gpu.io/cores' negate STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1'
agent_says "hybrid CPU inference sizes the balloon to every P-core CPU" "${r}" 0 \
    'cpu: "16"' "" STUB_ALLOC='cpu 32' STUB_NODE_LABELS=k3d-gpu.io/hybrid-cpu=true
agent_says "GPU inference on a hybrid node asks for a small P-core balloon" "${r}" 0 \
    'cpu: "2"' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' \
    STUB_NODE_LABELS='k3d-gpu.io/hybrid-cpu=true gpu.intel.com/xmx=true'
agent_says "K3D_GPU_AGENT_CPUS overrides the CPU request" "${r}" 0 \
    'cpu: "6"' "" STUB_ALLOC='cpu 32' STUB_NODE_LABELS=k3d-gpu.io/hybrid-cpu=true K3D_GPU_AGENT_CPUS=6

r=$(newroot agent-xeon)
agent_says "non-hybrid CPU inference requests half the allocatable CPUs" "${r}" 0 \
    'cpu: "28"' "" STUB_ALLOC='cpu 56'
agent_says "millicore allocatable is understood" "${r}" 0 \
    'cpu: "15"' "" STUB_ALLOC='cpu 31500m'
agent_says "CPU inference lands on an AMX node when there is one" "${r}" 0 \
    'feature.node.kubernetes.io/cpu-cpuid.AMXTILE: "true"' "" \
    STUB_ALLOC='cpu 56' STUB_NODE_LABELS=feature.node.kubernetes.io/cpu-cpuid.AMXTILE=true
agent_says "GPU inference is not tied to AMX nodes" "${r}" 0 \
    'AMXTILE' negate STUB_ALLOC=$'cpu 56\ngpu.intel.com/xe 1' \
    STUB_NODE_LABELS='feature.node.kubernetes.io/cpu-cpuid.AMXTILE=true gpu.intel.com/xmx=true'

r=$(newroot agent-model)
agent_says "default model is Qwen3-4B" "${r}" 0 '--source_model=OpenVINO/Qwen3-4B-int4-ov$' "" STUB_ALLOC='cpu 8'
agent_says "default model gets the hermes3 tool parser" "${r}" 0 '--tool_parser=hermes3$' "" STUB_ALLOC='cpu 8'
agent_says "default model gets the qwen3 reasoning parser" "${r}" 0 '--reasoning_parser=qwen3$' "" STUB_ALLOC='cpu 8'
agent_says "an empty K3D_GPU_AGENT_TOOL_PARSER turns the parser off" "${r}" 0 'tool_parser' negate \
    STUB_ALLOC='cpu 8' K3D_GPU_AGENT_TOOL_PARSER=
agent_says "the model cache lives on the models volume" "${r}" 0 '--cache_dir=/models/\.ov_cache' "" STUB_ALLOC='cpu 8'
agent_says "the server image is pinned by digest" "${r}" 0 'image: openvino/model_server@sha256:[0-9a-f]{64}' "" STUB_ALLOC='cpu 8'
agent_says "server logs at INFO by default" "${r}" 0 '--log_level=INFO$' "" STUB_ALLOC='cpu 8'
agent_says "K3D_GPU_AGENT_LOG_LEVEL=DEBUG reaches the server" "${r}" 0 '--log_level=DEBUG$' "" \
    STUB_ALLOC='cpu 8' K3D_GPU_AGENT_LOG_LEVEL=DEBUG
agent_says "a custom model leaves parser choice to OVMS" "${r}" 0 'tool_parser' negate \
    STUB_ALLOC='cpu 8' K3D_GPU_AGENT_MODEL=OpenVINO/Phi-4-mini-instruct-int4-ov

# OVMS --pull clones the model repo's default branch and has no revision option,
# so `agent status` reports the commit that was downloaded.
status_says() {
    local name=$1 re=$2 out
    shift 2
    out=$(env PATH="${ASTUB}:${PATH}" "$@" bash "${K3D_GPU}" agent status 2>&1)
    if grep -qE -- "${re}" <<< "${out}"; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name} (/${re}/)"; awk '{print "  | " $0}' <<< "${out}"
    fi
}
sha=0123456789abcdef0123456789abcdef01234567
status_says "agent status prints the downloaded model commit" "model revision: ${sha}" STUB_EXEC="${sha}"
status_says "agent status says unknown for a non-sha revision" 'model revision: unknown' STUB_EXEC=garbage
status_says "agent status says unknown when the pod cannot be read" 'model revision: unknown'

# ---- report -------------------------------------------------------------------
# `report --json` prints one JSON document describing the host (and the cluster
# when its kubeconfig context exists). report_says checks it with a python
# expression over the parsed document `r`; the JSON must parse.

# Stub kubectl for report: STUB_CONTEXTS is what `config get-contexts -o name`
# lists, STUB_NODES (tab-separated go-template lines) what `get nodes` prints.
RSTUB="${WORK}/rbin"
mkdir -p "${RSTUB}"
cat > "${RSTUB}/kubectl" <<'EOF'
#!/bin/sh
case "$*" in
    *get-contexts*) for c in ${STUB_CONTEXTS:-}; do echo "${c}"; done ;;
    *"get nodes"*)  [ -n "${STUB_NODES:-}" ] || exit 1; printf '%b' "${STUB_NODES}" ;;
esac
exit 0
EOF
chmod +x "${RSTUB}/kubectl"

# report_says <name> <root> <python expr over r> [env assignments...]
report_says() {
    local name=$1 root=$2 expr=$3 out rc
    shift 3
    out=$(env K3D_GPU_SYSROOT="${root}" PATH="${RSTUB}:${PATH}" "$@" bash "${K3D_GPU}" report --json 2>"${WORK}/report.err")
    rc=$?
    if [ "${rc}" -eq 0 ] && python3 -c '
import json, sys
r = json.loads(sys.stdin.read())
sys.exit(0 if eval(sys.argv[1]) else 1)' "${expr}" <<< "${out}"; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (rc=${rc}, want: ${expr})"
        { echo "${out}"; cat "${WORK}/report.err"; } | sed 's/^/  | /'
    fi
}

# Raptor Lake desktop: hybrid i9 with an i915 UHD 770 (GuC/HuC running, readable
# debugfs), one NVIDIA GPU and RAPL package + core zones.
r=$(newroot report-rpl)
kernel "${r}" 6.6.128-1-MANJARO
hybrid "${r}" 0-15 16-31
mkdir -p "${r}/proc" "${r}/sys/devices/system/cpu"
printf 'processor\t: 0\nmodel name\t: 13th Gen Intel(R) Core(TM) i9-13900T\nflags\t\t: fpu sse4_2 avx f16c fma avx2 avx_vnni sha_ni\n' > "${r}/proc/cpuinfo"
printf 'MemTotal:       131072000 kB\nMemFree:  1 kB\nMemAvailable:   73400320 kB\n' > "${r}/proc/meminfo"
echo 0-31 > "${r}/sys/devices/system/cpu/online"
pci "${r}" 0000:00:02.0 0x8086 0xa780 i915; render "${r}" 0000:00:02.0 128
mkdir -p "${r}/sys/class/drm/card0"
ln -sfn "${r}/sys/devices/pci0000:00/0000:00:02.0" "${r}/sys/class/drm/card0/device"
mkdir -p "${r}/sys/class/drm/card0-DP-1"
for e in "rcs0 " "bcs0 " "vcs0 hevc sfc" "vecs0 sfc"; do
    read -r en caps <<< "${e}"
    mkdir -p "${r}/sys/class/drm/card0/engine/${en}"
    echo "${caps}" > "${r}/sys/class/drm/card0/engine/${en}/capabilities"
done
mkdir -p "${r}/sys/kernel/debug/dri/0/gt0/uc"
# i915 intel_uc_fw_dump(): one "version:" line, "found X" or "wanted W, found X".
printf 'GuC firmware: i915/tgl_guc_70.bin\n\tstatus: RUNNING\n\tversion: found 70.49.4\n\tuCode: 336448 bytes\n\tRSA: 256 bytes\nGuC status 0x8002f0ec:\n\tBootrom status = 0x76\n' > "${r}/sys/kernel/debug/dri/0/gt0/uc/guc_info"
printf 'HuC firmware: i915/tgl_huc.bin\n\tstatus: RUNNING\n\tversion: wanted 7.10.0, found 7.9.3\n\tuCode: 589760 bytes\n\tRSA: 256 bytes\nHuC status: 0x00090001\n' > "${r}/sys/kernel/debug/dri/0/gt0/uc/huc_info"
mkdir -p "${r}/sys/module/nvidia" "${r}/proc/driver/nvidia/gpus/0000:01:00.0"
echo 590.48.01 > "${r}/sys/module/nvidia/version"
pci "${r}" 0000:01:00.0 0x10de 0x27b0 nvidia
printf 'Model: \t\t NVIDIA RTX 4000 SFF Ada Generation\nBus Location: \t 0000:01:00.0\nDevice Minor: \t 0\n' > "${r}/proc/driver/nvidia/gpus/0000:01:00.0/information"
for z in "intel-rapl:0 package-0" "intel-rapl:0:0 core"; do
    read -r zone zname <<< "${z}"
    mkdir -p "${r}/sys/class/powercap/${zone}"
    echo "${zname}" > "${r}/sys/class/powercap/${zone}/name"
    echo 262143328850 > "${r}/sys/class/powercap/${zone}/max_energy_range_uj"
done

report_says "report: schema and version" "${r}" \
    'r["schema"] == "k3d-gpu.report/v1" and r["k3d_gpu_version"] and r["host"]["kernel"] == "6.6.128-1-MANJARO"'
report_says "report: CPU model, P/E core lists and counts" "${r}" \
    '(c := r["host"]["cpu"]) and c["model"] == "13th Gen Intel(R) Core(TM) i9-13900T" and c["logical_cpus"] == 32 and c["hybrid"] is True and c["p_cores"] == "0-15" and c["e_cores"] == "16-31" and c["p_core_cpus"] == 16 and c["e_core_cpus"] == 16'
report_says "report: AI ISA flags, no AVX-512 or AMX" "${r}" \
    '(c := r["host"]["cpu"]) and c["isa"] == ["avx", "avx2", "avx_vnni", "f16c", "fma"] and c["avx_vnni"] is True and c["avx512"] is False and c["amx"] == []'
report_says "report: memory from meminfo" "${r}" \
    'r["host"]["memory"] == {"total_kib": 131072000, "available_kib": 73400320}'
report_says "report: i915 iGPU with render node, engines and GuC/HuC" "${r}" \
    'len(r["host"]["intel_gpus"]) == 1 and (g := r["host"]["intel_gpus"][0]) and g["card"] == "card0" and g["render_node"] == "renderD128" and g["pci"] == "0000:00:02.0" and g["device_id"] == "0xa780" and g["driver"] == "i915" and [e["name"] for e in g["engines"]] == ["bcs0", "rcs0", "vcs0", "vecs0"] and g["engines"][2]["capabilities"] == ["hevc", "sfc"] and g["guc"] == {"status": "RUNNING", "firmware": "i915/tgl_guc_70.bin", "version": "70.49.4"} and g["huc"]["status"] == "RUNNING" and g["huc"]["version"] == "7.9.3"'
report_says "report: NVIDIA GPU from procfs" "${r}" \
    '(n := r["host"]["nvidia"]) and n["driver"] == "590.48.01" and n["gpus"] == [{"pci": "0000:01:00.0", "model": "NVIDIA RTX 4000 SFF Ada Generation", "device_id": "0x27b0", "minor": 0}]'
report_says "report: RAPL zones with names and wrap range" "${r}" \
    '[(z["zone"], z["name"], z["max_energy_range_uj"]) for z in r["host"]["rapl"]] == [("intel-rapl:0", "package-0", 262143328850), ("intel-rapl:0:0", "core", 262143328850)] and r["host"]["rapl"][0]["energy_readable"] is False'
report_says "report: absent engines are empty, not missing" "${r}" \
    '(h := r["host"]) and h["npus"] == [] and h["gaudi"] == {"accel_nodes": 0, "pci_devices": 0, "driver_loaded": False} and h["dsa"] == {"devices": 0, "work_queues": 0} and h["iaa"] == {"devices": 0, "work_queues": 0} and h["qat"] == []'
report_says "report: detected vendors and resources" "${r}" \
    '[(d["vendor"], d["resources"]) for d in r["detected"]] == [("nvidia", ["nvidia.com/gpu"]), ("intel", ["gpu.intel.com/i915"]), ("hybrid", ["k3d-gpu.io/hybrid-cpu=true"])]'
report_says "report: no kubeconfig context means cluster absent with a reason" "${r}" \
    '(c := r["cluster"]) and c["name"] == "rpt" and c["context"] == "k3d-rpt" and c["present"] is False and "k3d-rpt" in c["reason"] and c["nodes"] == []' \
    K3D_GPU_CLUSTER=rpt STUB_CONTEXTS="k3d-other"
report_says "report: unreachable API means cluster absent with a reason" "${r}" \
    '(c := r["cluster"]) and c["present"] is False and "unreachable" in c["reason"]' \
    K3D_GPU_CLUSTER=rpt STUB_CONTEXTS="k3d-rpt"
report_says "report: cluster allocatable and k3d-gpu labels per node" "${r}" \
    '(c := r["cluster"]) and c["present"] is True and c["nodes"] == [{"name": "k3d-rpt-server-0", "allocatable": {"cpu": "32", "gpu.intel.com/i915": "1", "nvidia.com/gpu": "2"}, "labels": {"k3d-gpu.io/hybrid-cpu": "true", "nvidia.com/gpu.present": ""}}]' \
    K3D_GPU_CLUSTER=rpt STUB_CONTEXTS="k3d-rpt" \
    STUB_NODES='N\tk3d-rpt-server-0\nA\tk3d-rpt-server-0\tcpu\t32\nA\tk3d-rpt-server-0\tgpu.intel.com/i915\t1\nA\tk3d-rpt-server-0\tnvidia.com/gpu\t2\nL\tk3d-rpt-server-0\tkubernetes.io/os\tlinux\nL\tk3d-rpt-server-0\tk3d-gpu.io/hybrid-cpu\ttrue\nL\tk3d-rpt-server-0\tnvidia.com/gpu.present\t\n'

# Lunar Lake + Xeon-style extras: xe iGPU (engines per class under tile/gt, no
# readable debugfs), an NPU, a Gaudi PCI function without driver, DSA/IAA queues
# and a QAT PF with one VF. Plus a CPU model that needs JSON escaping.
r=$(newroot report-xe)
xeon "${r}"
printf 'processor\t: 0\nmodel name\t: Fake "Quoted" \\ CPU\nflags\t\t: fpu avx512f avx512_bf16 amx_bf16 amx_tile\n' > "${r}/proc/cpuinfo"
pci "${r}" 0000:00:02.0 0x8086 0x64a0 xe; render "${r}" 0000:00:02.0 128
mkdir -p "${r}/sys/class/drm/card1"
ln -sfn "${r}/sys/devices/pci0000:00/0000:00:02.0" "${r}/sys/class/drm/card1/device"
mkdir -p "${r}/sys/devices/pci0000:00/0000:00:02.0/tile0/gt0/engines/"{rcs,bcs,ccs,.defaults}
pci "${r}" 0000:00:0b.0 0x8086 0x643e intel_vpu; accel "${r}" 0000:00:0b.0 0
pci "${r}" 0000:4d:00.0 0x1da3 0x1060

report_says "report: JSON-escaped CPU model" "${r}" \
    'r["host"]["cpu"]["model"] == "Fake \"Quoted\" \\ CPU" and r["host"]["cpu"]["hybrid"] is False and r["host"]["cpu"]["p_cores"] is None'
report_says "report: AVX-512 and AMX flags" "${r}" \
    '(c := r["host"]["cpu"]) and c["avx512"] is True and c["amx"] == ["amx_bf16", "amx_tile"] and "avx512_bf16" in c["isa"]'
report_says "report: xe engines by class; GuC unknown without root" "${r}" \
    '(g := r["host"]["intel_gpus"][0]) and g["driver"] == "xe" and g["platform"] == "Lunar Lake" and [e["name"] for e in g["engines"]] == ["bcs", "ccs", "rcs"] and g["guc"]["status"] is None and "root" in g["guc"]["note"]'
report_says "report: NPU, Gaudi without driver, DSA/IAA queues, QAT VFs" "${r}" \
    '(h := r["host"]) and h["npus"] == [{"accel": "accel0", "pci": "0000:00:0b.0", "device_id": "0x643e", "driver": "intel_vpu", "platform": "Lunar Lake"}] and h["gaudi"] == {"accel_nodes": 0, "pci_devices": 1, "driver_loaded": False} and h["dsa"] == {"devices": 1, "work_queues": 1} and h["iaa"] == {"devices": 1, "work_queues": 1} and h["qat"] == [{"pci": "0000:6b:00.0", "device_id": "0x4940", "driver": "4xxx", "vfs": 1}]'
report_says "report: no NVIDIA and no RAPL are empty" "${r}" \
    'r["host"]["nvidia"] == {"driver": None, "gpus": []} and r["host"]["rapl"] == []'

# Default mode writes the JSON and a self-contained HTML that embeds the same JSON.
out_dir="${WORK}/report-out"
mkdir -p "${out_dir}"
if env K3D_GPU_SYSROOT="${r}" PATH="${RSTUB}:${PATH}" bash "${K3D_GPU}" report "${out_dir}" > "${WORK}/report.txt" 2>&1 \
   && python3 - "${out_dir}" <<'EOF'
import json, re, sys
d = sys.argv[1]
j = json.load(open(d + "/k3d-gpu-report.json"))
h = open(d + "/k3d-gpu-report.html").read()
m = re.search(r'<script id="report" type="application/json">(.*?)</script>', h, re.S)
assert m and json.loads(m.group(1)) == j, "embedded JSON differs"
assert not re.search(r'<(script|link)[^>]+(src|href)=', h), "HTML loads external resources"
EOF
then
    PASS=$((PASS + 1)); echo "ok   report: writes JSON and self-contained HTML with the same data"
else
    FAILS=$((FAILS + 1)); echo "FAIL report: writes JSON and self-contained HTML with the same data"
    sed 's/^/  | /' "${WORK}/report.txt"
fi
if grep -q 'Lunar Lake' "${WORK}/report.txt" && grep -q 'cluster.*not' "${WORK}/report.txt"; then
    PASS=$((PASS + 1)); echo "ok   report: text summary names the GPU and the cluster state"
else
    FAILS=$((FAILS + 1)); echo "FAIL report: text summary names the GPU and the cluster state"
    sed 's/^/  | /' "${WORK}/report.txt"
fi

# Root-readable xe debugfs (xe_uc_fw_print): "wanted release version W", then
# "found release version X" and, for GuC, "found compatibility version Y". The
# firmware version is the release one. HuC sits on the media GT (gt1); gt0 has
# no HuC (status N/A) and must not hide it. Newer kernels name the debugfs dir
# after the PCI address (dri/<minor> is only a symlink to it); this tree has only that.
r=$(newroot report-xe-root)
pci "${r}" 0000:00:02.0 0x8086 0x64a0 xe; render "${r}" 0000:00:02.0 128
mkdir -p "${r}/sys/class/drm/card0" "${r}/sys/kernel/debug/dri/0000:00:02.0/gt0/uc" "${r}/sys/kernel/debug/dri/0000:00:02.0/gt1/uc"
ln -sfn "${r}/sys/devices/pci0000:00/0000:00:02.0" "${r}/sys/class/drm/card0/device"
printf 'GuC firmware: xe/lnl_guc_70.bin\n\tstatus: RUNNING\n\twanted release version 70.29.2\n\tfound release version 70.44.1\n\tfound compatibility version 1.13.4\n\tuCode: 360768 bytes\n\tRSA: 384 bytes\n' > "${r}/sys/kernel/debug/dri/0000:00:02.0/gt0/uc/guc_info"
printf 'HuC firmware: \n\tstatus: N/A\n\twanted release version 0.0.0\n' > "${r}/sys/kernel/debug/dri/0000:00:02.0/gt0/uc/huc_info"
printf 'HuC firmware: xe/lnl_huc.bin\n\tstatus: RUNNING\n\twanted release version 9.4.13\n\tfound release version 9.4.13.0\n\tuCode: 1236992 bytes\n\nHuC status: 0x00000001\n' > "${r}/sys/kernel/debug/dri/0000:00:02.0/gt1/uc/huc_info"
report_says "report: xe GuC version is the release version, not the compatibility one" "${r}" \
    'r["host"]["intel_gpus"][0]["guc"] == {"status": "RUNNING", "firmware": "xe/lnl_guc_70.bin", "version": "70.44.1"}'
report_says "report: xe HuC on the media GT is found past an unsupported gt0" "${r}" \
    'r["host"]["intel_gpus"][0]["huc"] == {"status": "RUNNING", "firmware": "xe/lnl_huc.bin", "version": "9.4.13.0"}'

# i915 Meteor Lake: gt0 says "HuC not supported" (no status line), gt1 has it.
r=$(newroot report-mtl-root)
pci "${r}" 0000:00:02.0 0x8086 0x7d55 i915; render "${r}" 0000:00:02.0 128
mkdir -p "${r}/sys/class/drm/card0" "${r}/sys/kernel/debug/dri/0/gt0/uc" "${r}/sys/kernel/debug/dri/0/gt1/uc"
ln -sfn "${r}/sys/devices/pci0000:00/0000:00:02.0" "${r}/sys/class/drm/card0/device"
printf 'HuC not supported\n' > "${r}/sys/kernel/debug/dri/0/gt0/uc/huc_info"
printf 'HuC firmware: i915/mtl_huc_gsc.bin\n\tstatus: RUNNING\n\tversion: found 8.5.4\n' > "${r}/sys/kernel/debug/dri/0/gt1/uc/huc_info"
printf 'GuC firmware: i915/mtl_guc_70.bin\n\tstatus: LOAD FAIL\n\tversion: wanted 70.44.1, found 70.12.0\n' > "${r}/sys/kernel/debug/dri/0/gt0/uc/guc_info"
printf 'GuC not supported\n' > "${r}/sys/kernel/debug/dri/0/gt1/uc/guc_info"
report_says "report: i915 HuC on gt1 is found past 'HuC not supported' on gt0" "${r}" \
    'r["host"]["intel_gpus"][0]["huc"] == {"status": "RUNNING", "firmware": "i915/mtl_huc_gsc.bin", "version": "8.5.4"}'
report_says "report: a two-word status (LOAD FAIL) is kept whole; found, not wanted, version" "${r}" \
    'r["host"]["intel_gpus"][0]["guc"] == {"status": "LOAD FAIL", "firmware": "i915/mtl_guc_70.bin", "version": "70.12.0"}'
rm "${r}/sys/kernel/debug/dri/0/gt0/uc/guc_info"
report_says "report: readable debugfs with no GuC says so, not 'run as root'" "${r}" \
    '(g := r["host"]["intel_gpus"][0]["guc"]) and g["status"] is None and g["note"] == "GuC not supported"'

# `up` passes K3D_GPU_NODE_LABELS to k3d as extra node labels. k3d is stubbed to
# record its arguments; kubectl answers `get nodes -l` with one node so the
# hybrid verify step finds its labelled node.
USTUB="${WORK}/ubin"
mkdir -p "${USTUB}"
cat > "${USTUB}/k3d" <<'EOF'
#!/bin/sh
printf '%s\n' "$@" > "${STUB_K3D_ARGS}"
EOF
cat > "${USTUB}/kubectl" <<'EOF'
#!/bin/sh
case "$*" in *"get nodes -l"*) echo node/k3d-stub-server-0 ;; esac
exit 0
EOF
cp "${STUB_BIN}/docker" "${USTUB}/docker"
chmod +x "${USTUB}"/*

# up_says <name> <want rc: 0|fail> <ERE on the recorded k3d args> [negate] [env assignments...]
up_says() {
    local name=$1 want=$2 re=$3 negate=${4:-} args="${WORK}/k3d-args" out rc hit=0
    shift 4 2>/dev/null || shift $#
    rm -f "${args}"
    out=$(env K3D_GPU_SYSROOT="$(newroot up-empty)" PATH="${USTUB}:${PATH}" STUB_K3D_ARGS="${args}" \
        K3D_GPU_VENDORS=hybrid "$@" bash "${K3D_GPU}" up 2>&1)
    rc=$?
    [ -f "${args}" ] && grep -E -- "${re}" "${args}" >/dev/null && hit=1
    if { { [ "${want}" = 0 ] && [ "${rc}" -eq 0 ]; } || { [ "${want}" = fail ] && [ "${rc}" -ne 0 ]; }; } \
       && { { [ -z "${negate}" ] && [ "${hit}" -eq 1 ]; } || { [ -n "${negate}" ] && [ "${hit}" -eq 0 ]; }; }; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (rc=${rc}, want ${want}, ${negate:+not }/${re}/)"
        awk '{print "  | " $0}' <<< "${out}"
        [ -f "${args}" ] && sed 's/^/  k3d> /' "${args}"
    fi
}

up_says "up without extra labels adds none" 0 'ei-infra-eligible' negate
up_says "K3D_GPU_NODE_LABELS adds each label to every node" 0 \
    '^--k3s-node-label=ei-infra-eligible=true@server:\*;agent:\*$' "" \
    K3D_GPU_NODE_LABELS="ei-infra-eligible=true, ei-inference-eligible=true"
up_says "K3D_GPU_NODE_LABELS second label is passed too" 0 \
    '^--k3s-node-label=ei-inference-eligible=true@server:\*;agent:\*$' "" \
    K3D_GPU_NODE_LABELS="ei-infra-eligible=true, ei-inference-eligible=true"
up_says "K3D_GPU_NODE_LABELS rejects an entry without =" fail 'ei-infra-eligible' negate \
    K3D_GPU_NODE_LABELS="ei-infra-eligible"
up_says "up without extra k3s args keeps servicelb" 0 'servicelb' negate
up_says "K3D_GPU_K3S_ARGS passes each flag to every server" 0 \
    '^--k3s-arg=--disable=servicelb@server:\*$' "" \
    K3D_GPU_K3S_ARGS="--disable=servicelb --disable-network-policy"
up_says "K3D_GPU_K3S_ARGS second flag is passed too" 0 \
    '^--k3s-arg=--disable-network-policy@server:\*$' "" \
    K3D_GPU_K3S_ARGS="--disable=servicelb --disable-network-policy"
up_says "K3D_GPU_K3S_ARGS keeps a flag's own commas" 0 \
    '^--k3s-arg=--disable=servicelb,metrics-server@server:\*$' "" \
    K3D_GPU_K3S_ARGS="--disable=servicelb,metrics-server"
up_says "K3D_GPU_K3S_ARGS rejects a word that is not a flag" fail 'servicelb' negate \
    K3D_GPU_K3S_ARGS="--disable servicelb"

# Node Feature Discovery reads the host's kernel config, builtin modules and
# os-release; the node image has its own /boot, /lib and /etc, so `up` mounts
# the host's copies read-only under /k3d-gpu/host.
r=$(newroot up-nfd)
mkdir -p "${r}/lib/modules/6.18.1" "${r}/boot" "${r}/etc"
echo "ID=manjaro" > "${r}/etc/os-release"
up_says "up mounts the host's /lib/modules for NFD" 0 \
    "^--volume=${r}/lib/modules:/k3d-gpu/host/lib/modules:ro@server:\*;agent:\*$" "" K3D_GPU_SYSROOT="${r}"
up_says "up mounts no /boot without a config for the running kernel" 0 '/k3d-gpu/host/boot' negate \
    K3D_GPU_SYSROOT="${r}"
mkdir -p "${r}/proc/sys/kernel"
echo "6.18.1" > "${r}/proc/sys/kernel/osrelease"
echo "CONFIG_X86=y" > "${r}/boot/config-6.18.1"
echo "kernel" > "${r}/boot/vmlinuz-6.18.1"
up_says "up mounts the running kernel's /boot config for NFD" 0 \
    "^--volume=${r}/boot/config-6.18.1:/k3d-gpu/host/boot/config-6.18.1:ro@server:\*;agent:\*$" "" K3D_GPU_SYSROOT="${r}"
up_says "up mounts nothing else from /boot" 0 "^--volume=${r}/boot(/vmlinuz[^:]*)?:" negate K3D_GPU_SYSROOT="${r}"
up_says "up mounts the host's /etc/os-release for NFD" 0 \
    "^--volume=${r}/etc/os-release:/k3d-gpu/host/etc/os-release:ro@server:\*;agent:\*$" "" K3D_GPU_SYSROOT="${r}"
up_says "up still sets the hybrid label itself" 0 \
    '^--k3s-node-label=k3d-gpu.io/hybrid-cpu=true@server:\*;agent:\*$' "" K3D_GPU_SYSROOT="${r}"
up_says "up skips host paths that do not exist" 0 '/k3d-gpu/host/' negate

# AMX labels are launcher-set (NFD's worker blacklists them): they exist from
# node registration on, before (or without) NFD.
r=$(newroot up-amx)
mkdir -p "${r}/proc"
echo "flags : fpu amx_bf16 amx_tile amx_int8" > "${r}/proc/cpuinfo"
up_says "up sets the AMX labels itself" 0 \
    '^--k3s-node-label=feature.node.kubernetes.io/cpu-cpuid.AMXTILE=true@server:\*;agent:\*$' "" \
    K3D_GPU_SYSROOT="${r}" K3D_GPU_VENDORS=hybrid,amx

# One owner per node label: NFD must not publish a label the launcher sets. The
# launcher's cpu-cpuid names (nfd_cpuid_name, and AMX*) are in nfd-worker's
# cpuid attributeBlacklist next to NFD's own defaults, and the baked Intel
# NodeFeatureRules carry no npu/dsa/iaa/qat label.
NFD_YML="$(dirname "${K3D_GPU}")/../share/node-feature-discovery.yml"
blacklist=$(sed -n 's/^ *attributeBlacklist: \[\(.*\)\]$/\1/p' "${NFD_YML}" | tr -d ' ' | tr ',' '\n')
launcher_cpuid=$(sed -n '/^nfd_cpuid_name() {/,/^}/p' "${K3D_GPU}" | grep -oE 'echo [A-Z0-9_]+' | cut -d' ' -f2)
nfd_defaults="AVX10 BMI1 BMI2 CLMUL CMOV CX16 ERMS F16C HTT LZCNT MMX MMXEXT NX POPCNT RDRAND RDSEED RDTSCP SGX SGXLC SSE SSE2 SSE3 SSE4 SSE42 SSSE3 TDX_GUEST"
amx_ids="AMXBF16 AMXCOMPLEX AMXFP16 AMXFP8 AMXINT8 AMXTF32 AMXTILE AMXTRANSPOSE"
missing=""
for id in ${launcher_cpuid} ${nfd_defaults} ${amx_ids}; do
    grep -qxF "${id}" <<< "${blacklist}" || missing+=" ${id}"
done
if [ -n "${launcher_cpuid}" ] && [ -z "${missing}" ]; then
    PASS=$((PASS + 1)); echo "ok   nfd-worker blacklists the launcher's cpu-cpuid labels and keeps NFD's defaults"
else
    FAILS=$((FAILS + 1)); echo "FAIL nfd-worker cpuid attributeBlacklist misses:${missing:- (no launcher names found)}"
fi
dup=$(grep -oE 'intel\.feature\.node\.kubernetes\.io/(npu|dsa|iaa|qat):' "${NFD_YML}" | sort -u | paste -sd' ')
if [ -z "${dup}" ]; then
    PASS=$((PASS + 1)); echo "ok   baked NodeFeatureRules leave npu/dsa/iaa/qat labels to the launcher"
else
    FAILS=$((FAILS + 1)); echo "FAIL baked NodeFeatureRules also set: ${dup}"
fi

# ---- NPU firmware vs user space, device access, recipe ------------------------

# fwver <root> <relpath> <version string>: a firmware file under lib/firmware that
# embeds <version string> between binary bytes, as intel/vpu/vpu_*.bin do.
fwver() {
    mkdir -p "$(dirname "$1/lib/firmware/$2")"
    printf '\x7fhdr\x00\x01\x02%s\x00\xfftail' "$3" > "$1/lib/firmware/$2"
}

# npu_host <root> <pci id> [mode]: an NPU bound to intel_vpu with /dev/accel/accel0
# in that mode (owned by the test user's group), on kernel 6.18.
npu_host() {
    kernel "$1" 6.18.1
    pci "$1" 0000:00:0b.0 0x8086 "$2" intel_vpu; accel "$1" 0000:00:0b.0 0
    chmod "${3:-660}" "$1/dev/accel/accel0"
}

OLD_40XX='Nov 13 2025*NPU40xx*ci_tag_ud202548_vpu_rc_20251112_1901-1-g72f907ffc78*72f907ffc780df5579a2fed65afc4944da8b0e44'
NEW_40XX='Jun 30 2026*NPU40xx*build/ci/npu-fw-ci-ci_branch_UD202628_npu_release_26ww25-20260626_085052-f9a611865dbf3e6b37a17a0b01a7e81e7fc812e7-1-g3b431d96211*3b431d96211eb618edf1f7c444daa8185f1e686c'

r=$(newroot npu-fw-old)
npu_host "${r}" 0x643e
fwver "${r}" intel/vpu/vpu_40xx_v1.bin "${OLD_40XX}"
doctor_says "NPU firmware older than the OVMS image's NPU driver warns" "${r}" \
    'warn.*NPU firmware built 2025-11-13 is older than the 2026-06-30 firmware of linux-npu-driver v1\.35\.0'
doctor_says "older NPU firmware points at the intel-fw-npu release" "${r}" 'releases/tag/v1\.35\.0'

r=$(newroot npu-fw-same)
npu_host "${r}" 0x643e
fwver "${r}" intel/vpu/vpu_40xx_v1.bin "${NEW_40XX}"
doctor_says "NPU firmware from linux-npu-driver v1.35.0 is ok" "${r}" 'ok: NPU firmware built 2026-06-30, not older'
doctor_says "NPU firmware from linux-npu-driver v1.35.0 does not warn" "${r}" 'is older than' negate

r=$(newroot npu-fw-mtl)
npu_host "${r}" 0x7d1d
fwver "${r}" intel/vpu/vpu_37xx_v1.bin '20260701*MTL_CLIENT_SILICON-NVR+NN-deployment*3b431d96211e*3b431d96211e*3b431d96211'
doctor_says "Meteor Lake firmware date format (YYYYMMDD) is read" "${r}" 'ok: NPU firmware built 2026-07-01'

r=$(newroot npu-fw-ptl)
npu_host "${r}" 0xb03e
fwver "${r}" intel/vpu/vpu_50xx_v1.bin 'Jul  2 2026*NPU50xx*build/ci/x*3b431d96211e'
doctor_says "space-padded day in a 50xx firmware date is read" "${r}" 'ok: NPU firmware built 2026-07-02'

r=$(newroot npu-fw-xz)
npu_host "${r}" 0x643e
fwver "${r}" intel/vpu/vpu_40xx_v1.bin "${OLD_40XX}"
xz "${r}/lib/firmware/intel/vpu/vpu_40xx_v1.bin"
doctor_says "xz-compressed NPU firmware is decompressed and dated" "${r}" 'present \(/lib/firmware/intel/vpu/vpu_40xx_v1\.bin\.xz\)'
doctor_says "xz-compressed old NPU firmware warns" "${r}" 'warn.*built 2025-11-13'

r=$(newroot npu-fw-order)
npu_host "${r}" 0x643e
fwver "${r}" intel/vpu/vpu_40xx_v1.bin "${OLD_40XX}"
fwver "${r}" updates/intel/vpu/vpu_40xx_v1.bin "${NEW_40XX}"
zstd -q --rm "${r}/lib/firmware/updates/intel/vpu/vpu_40xx_v1.bin"
doctor_says "uncompressed /lib/firmware beats a compressed updates/ file, as in the kernel" "${r}" \
    'warn.*built 2025-11-13'
fwver "${r}" "updates/6.18.1/intel/vpu/vpu_40xx_v1.bin" "${NEW_40XX}"
doctor_says "uncompressed updates/<release> beats /lib/firmware" "${r}" \
    'present \(/lib/firmware/updates/6\.18\.1/intel/vpu/vpu_40xx_v1\.bin\)'

r=$(newroot npu-fw-v0)
npu_host "${r}" 0x643e
fwver "${r}" intel/vpu/vpu_40xx_v0.0.bin "${NEW_40XX}"
fwver "${r}" intel/vpu/vpu_40xx_v1.bin "${OLD_40XX}"
doctor_says "vpu_40xx_v1.bin is loaded before vpu_40xx_v0.0.bin" "${r}" 'warn.*built 2025-11-13'

r=$(newroot npu-fw-blank)
npu_host "${r}" 0x643e
firmware "${r}" intel/vpu/vpu_40xx_v1.bin
doctor_says "NPU firmware without a version string is reported, not judged" "${r}" 'note: no build date readable'

GID=$(id -g)
if [ "${GID}" -ne 0 ]; then
    r=$(newroot npu-gid)
    npu_host "${r}" 0x643e 660
    doctor_says "group-only NPU node names the supplemental group" "${r}" \
        "ok: /dev/accel/accel0 is group ${GID}, mode 660: non-root pods need securityContext.supplementalGroups: \[${GID}\]"
    out=$(K3D_GPU_SYSROOT="${r}" K3D_GPU_SHARE="${K3D_GPU%/scripts/k3d-gpu}/share" bash "${K3D_GPU}" recipe npu-embeddings 2>&1)
    if grep -q "supplementalGroups: \[${GID}\]" <<< "${out}" && ! grep -v '^[[:space:]]*#' <<< "${out}" | grep -q K3D_GPU_NPU_RENDER_GID; then
        PASS=$((PASS + 1)); echo "ok   recipe npu-embeddings fills in the NPU group"
    else
        FAILS=$((FAILS + 1)); echo "FAIL recipe npu-embeddings fills in the NPU group"; sed 's/^/  | /' <<< "${out}"
    fi
else
    echo "skip group-owned NPU node cases (running as root)"
fi

r=$(newroot npu-world)
npu_host "${r}" 0x643e 666
doctor_says "world read-write NPU node needs no group" "${r}" 'ok: /dev/accel/accel0 is mode 666: pods of any user'

r=$(newroot npu-rootonly)
npu_host "${r}" 0x643e 600
doctor_says "owner-only NPU node warns" "${r}" 'warn.*only root pods'
doctor_says "owner-only NPU node gets the udev rule" "${r}" 'SUBSYSTEM=="accel", GROUP="render", MODE="0660"'

r=$(newroot npu-recipe-none)
if env K3D_GPU_SYSROOT="${r}" K3D_GPU_SHARE="${K3D_GPU%/scripts/k3d-gpu}/share" bash "${K3D_GPU}" recipe npu-embeddings >/dev/null 2>&1; then
    FAILS=$((FAILS + 1)); echo "FAIL recipe npu-embeddings without an NPU must fail"
else
    PASS=$((PASS + 1)); echo "ok   recipe npu-embeddings without an NPU fails"
fi

# Firmware search follows the kernel: firmware_class.path first, then the four
# /lib/firmware dirs (no /usr/lib), and intel_vpu's firmware= replaces the name list.
r=$(newroot npu-fw-usrlib)
npu_host "${r}" 0x643e
mkdir -p "${r}/usr/lib/firmware/intel/vpu"
printf '%s' "${NEW_40XX}" > "${r}/usr/lib/firmware/intel/vpu/vpu_40xx_v1.bin"
doctor_says "NPU firmware only under /usr/lib/firmware is not what the kernel loads" "${r}" \
    'warn.*intel/vpu/vpu_40xx_\* firmware not found'

r=$(newroot npu-fw-classpath)
npu_host "${r}" 0x643e
fwver "${r}" updates/intel/vpu/vpu_40xx_v1.bin "${OLD_40XX}"
mkdir -p "${r}/opt/fw/intel/vpu" "${r}/sys/module/firmware_class/parameters"
printf '%s' "${NEW_40XX}" > "${r}/opt/fw/intel/vpu/vpu_40xx_v1.bin"
echo /opt/fw > "${r}/sys/module/firmware_class/parameters/path"
doctor_says "firmware_class.path beats /lib/firmware/updates" "${r}" \
    'present \(/opt/fw/intel/vpu/vpu_40xx_v1\.bin\)'

r=$(newroot npu-fw-param)
npu_host "${r}" 0x643e
fwver "${r}" intel/vpu/vpu_40xx_v1.bin "${NEW_40XX}"
fwver "${r}" intel/vpu/custom.bin "${OLD_40XX}"
mkdir -p "${r}/sys/module/intel_vpu/parameters"
echo intel/vpu/custom.bin > "${r}/sys/module/intel_vpu/parameters/firmware"
doctor_says "intel_vpu firmware= names the file the driver loads" "${r}" \
    'present \(/lib/firmware/intel/vpu/custom\.bin\)'
doctor_says "intel_vpu firmware= is dated, not the default name" "${r}" 'warn.*built 2025-11-13'
echo '(null)' > "${r}/sys/module/intel_vpu/parameters/firmware"
doctor_says "unset intel_vpu firmware= reads (null) and keeps the default names" "${r}" \
    'present \(/lib/firmware/intel/vpu/vpu_40xx_v1\.bin\)'
echo intel/vpu/missing.bin > "${r}/sys/module/intel_vpu/parameters/firmware"
doctor_says "intel_vpu firmware= naming a missing file does not fall back to the defaults" "${r}" \
    'warn.*firmware not found'

# A device node owned by group 0. The test user cannot chgrp to root, so a stat
# stub reports group 0 for every file.
GID0BIN="${WORK}/gid0bin"
mkdir -p "${GID0BIN}"
cat > "${GID0BIN}/stat" <<EOF
#!/bin/sh
$(command -v stat) "\$@" | awk '{\$1 = 0; print}'
EOF
chmod +x "${GID0BIN}/stat"
SHARE="${K3D_GPU%/scripts/k3d-gpu}/share"

r=$(newroot npu-gid0-660)
npu_host "${r}" 0x643e 660
doctor_says "group-0 NPU node with mode 660 warns" "${r}" 'warn.*/dev/accel/accel0 is group 0, mode 660: only root pods' \
    "" PATH="${GID0BIN}:${STUB_BIN}:${PATH}"
if out=$(env K3D_GPU_SYSROOT="${r}" K3D_GPU_SHARE="${SHARE}" PATH="${GID0BIN}:${PATH}" bash "${K3D_GPU}" recipe npu-embeddings 2>&1); then
    FAILS=$((FAILS + 1)); echo "FAIL recipe npu-embeddings refuses a group-0, mode-660 node"; awk '{print "  | " $0}' <<< "${out}"
elif grep -q 'is group 0, mode 660; only root pods can open it' <<< "${out}"; then
    PASS=$((PASS + 1)); echo "ok   recipe npu-embeddings refuses a group-0, mode-660 node"
else
    FAILS=$((FAILS + 1)); echo "FAIL recipe npu-embeddings refuses a group-0, mode-660 node (message)"; awk '{print "  | " $0}' <<< "${out}"
fi

r=$(newroot npu-gid0-666)
npu_host "${r}" 0x643e 666
out=$(env K3D_GPU_SYSROOT="${r}" K3D_GPU_SHARE="${SHARE}" PATH="${GID0BIN}:${PATH}" bash "${K3D_GPU}" recipe npu-embeddings 2>&1)
if grep -q 'supplementalGroups: \[\]$' <<< "${out}"; then
    PASS=$((PASS + 1)); echo "ok   recipe npu-embeddings gives a group-0, mode-666 node no supplemental group"
else
    FAILS=$((FAILS + 1)); echo "FAIL recipe npu-embeddings gives a group-0, mode-666 node no supplemental group"; awk '{print "  | " $0}' <<< "${out}"
fi

# `up` publishes the NPU node's group as a node label. k3d records its arguments;
# kubectl reports one allocatable NPU so verification passes at once.
UPBIN="${WORK}/upbin"
mkdir -p "${UPBIN}"
printf '#!/bin/sh\nexit 0\n' > "${UPBIN}/docker"
printf '#!/bin/sh\necho "$@" >> "${UP_LOG}"\n' > "${UPBIN}/k3d"
printf '#!/bin/sh\ncase "$*" in *go-template*) echo "npu.intel.com/accel 1" ;; esac\nexit 0\n' > "${UPBIN}/kubectl"
chmod +x "${UPBIN}"/*
if [ "${GID}" -ne 0 ]; then
    r=$(newroot npu-up)
    npu_host "${r}" 0x643e 660
    UP_LOG="${WORK}/up.log"
    out=$(env K3D_GPU_SYSROOT="${r}" UP_LOG="${UP_LOG}" PATH="${UPBIN}:${PATH}" bash "${K3D_GPU}" up 2>&1)
    if grep -q -- "--k3s-node-label=k3d-gpu.io/npu-render-gid=${GID}@server:\*;agent:\*" "${UP_LOG}"; then
        PASS=$((PASS + 1)); echo "ok   up labels the node with the NPU render group"
    else
        FAILS=$((FAILS + 1)); echo "FAIL up labels the node with the NPU render group"
        sed 's/^/  | /' "${UP_LOG}" 2>/dev/null; sed 's/^/  | /' <<< "${out}"
    fi
fi
r=$(newroot npu-up-gid0)
npu_host "${r}" 0x643e 666
UP_LOG="${WORK}/up-gid0.log"
out=$(env K3D_GPU_SYSROOT="${r}" UP_LOG="${UP_LOG}" PATH="${GID0BIN}:${UPBIN}:${PATH}" bash "${K3D_GPU}" up 2>&1)
if grep -q -- "--k3s-node-label=intel.feature.node.kubernetes.io/npu=true" "${UP_LOG}" \
   && ! grep -q -- "npu-render-gid" "${UP_LOG}"; then
    PASS=$((PASS + 1)); echo "ok   up does not label a group-0 NPU node"
else
    FAILS=$((FAILS + 1)); echo "FAIL up does not label a group-0 NPU node"
    sed 's/^/  | /' "${UP_LOG}" 2>/dev/null; awk '{print "  | " $0}' <<< "${out}"
fi
# ---- agent: vLLM backends for discrete Arc (vllm, llm-scaler) -----------------
r=$(newroot agent-bmg)
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe; render "${r}" 0000:03:00.0 128
gid=$(stat -c %g "${r}/dev/dri/renderD128")
XE1=$'cpu 16\ngpu.intel.com/xe 1'
agent_says "vllm backend serves from the upstream vLLM XPU image, pinned by digest" "${r}" 0 \
    'image: vllm/vllm-openai-xpu@sha256:[0-9a-f]{64}$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "llm-scaler backend serves from Intel's llm-scaler image, pinned by digest" "${r}" 0 \
    'image: intel/llm-scaler-vllm@sha256:[0-9a-f]{64}$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=llm-scaler
agent_says "the backend name is case-insensitive" "${r}" 0 \
    'vllm/vllm-openai-xpu@' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vLLM
agent_says "an unknown K3D_GPU_AGENT_BACKEND is rejected" "${r}" fail \
    "unknown K3D_GPU_AGENT_BACKEND 'tgi'" "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=tgi
agent_says "vllm pod requests one gpu.intel.com/xe" "${r}" 0 \
    'gpu\.intel\.com/xe: "?1"?$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm pod joins the host render group" "${r}" 0 \
    "supplementalGroups: \[${gid}\]" "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm Deployment and Service are named vllm" "${r}" 0 \
    '^  name: vllm$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm keeps its weights on its own PVC" "${r}" 0 \
    'claimName: vllm-models' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm readiness is vLLM's /health" "${r}" 0 \
    'path: /health$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm gets a memory-backed /dev/shm" "${r}" 0 \
    'mountPath: /dev/shm' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm manifest carries no OVMS flags" "${r}" 0 \
    'target_device|--pull|model_repository_path' negate STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm default model is Qwen/Qwen3-4B at a pinned revision" "${r}" 0 \
    '--revision=[0-9a-f]{40}$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm serves the default model under its short name" "${r}" 0 \
    '--served-model-name=Qwen3-4B$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm default model gets the hermes tool parser" "${r}" 0 \
    '--tool-call-parser=hermes$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm turns on automatic tool choice with the tool parser" "${r}" 0 \
    '--enable-auto-tool-choice$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm default model gets the qwen3 reasoning parser" "${r}" 0 \
    '--reasoning-parser=qwen3$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "a custom vllm model gets no pinned revision" "${r}" 0 \
    '--revision' negate STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_MODEL=meta-llama/Llama-3.1-8B-Instruct
agent_says "a custom vllm model gets no tool parser unless asked" "${r}" 0 \
    'tool-call-parser|enable-auto-tool-choice' negate STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_MODEL=meta-llama/Llama-3.1-8B-Instruct
agent_says "K3D_GPU_AGENT_REVISION pins a custom vllm model" "${r}" 0 \
    '--revision=abc123$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_MODEL=meta-llama/Llama-3.1-8B-Instruct K3D_GPU_AGENT_REVISION=abc123
agent_says "an empty K3D_GPU_AGENT_TOOL_PARSER turns vllm tool calling off" "${r}" 0 \
    'enable-auto-tool-choice' negate STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_TOOL_PARSER=

# Flags and environment of Intel's Arc runtime (enterprise-inference PR #22).
for flag in '--dtype=float16' '--block-size=64' '--enforce-eager' '--no-enable-prefix-caching' \
            '--gpu-memory-utilization=0.9' '--max-num-batched-tokens=2048' '--max-model-len=8192' \
            '--tensor-parallel-size=1'; do
    agent_says "vllm runs with Intel's Arc flag ${flag}" "${r}" 0 \
        "^ *- ${flag}\$" "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
done
for kv in 'ONEAPI_DEVICE_SELECTOR level_zero:\*' 'ZE_FLAT_DEVICE_HIERARCHY FLAT' 'SYCL_CACHE_PERSISTENT 1' \
          'ZES_ENABLE_SYSMAN 1' 'VLLM_WORKER_MULTIPROC_METHOD spawn' 'CCL_ZE_IPC_EXCHANGE pidfd' \
          'VLLM_NO_USAGE_STATS 1' 'HF_HOME /models/huggingface'; do
    agent_says "vllm env sets ${kv%% *}" "${r}" 0 \
        "name: ${kv%% *}\$" "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
    out=$(env K3D_GPU_SYSROOT="${r}" PATH="${ASTUB}:${PATH}" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm \
        bash "${K3D_GPU}" agent manifest 2>/dev/null | grep -A1 -- "name: ${kv%% *}\$" | sed -n 2p)
    if grep -E -- "value: \"${kv#* }\"\$" <<< "${out}" >/dev/null; then
        PASS=$((PASS + 1)); echo "ok   vllm env ${kv%% *} has Intel's value"
    else
        FAILS=$((FAILS + 1)); echo "FAIL vllm env ${kv%% *} has Intel's value (got: ${out})"
    fi
done
agent_says "vllm does not mount host GPU libraries (the image ships its own)" "${r}" 0 \
    'hostPath|host-gpu-libs|BASH_ENV' negate STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm drops env vars vLLM no longer reads" "${r}" 0 \
    'VLLM_USE_V1|VLLM_ATTENTION_BACKEND|TORCH_LLM_ALLREDUCE' negate STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm never runs model-repo code" "${r}" 0 \
    'trust-remote-code' negate STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "K3D_GPU_AGENT_MAX_LEN sets the context length" "${r}" 0 \
    '--max-model-len=16384$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_MAX_LEN=16384
agent_says "a non-numeric K3D_GPU_AGENT_MAX_LEN is rejected" "${r}" fail \
    'K3D_GPU_AGENT_MAX_LEN must be a positive integer' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_MAX_LEN=8k

agent_says "K3D_GPU_AGENT_GPUS=2 shards the model across two GPUs" "${r}" 0 \
    '--tensor-parallel-size=2$' "" STUB_ALLOC=$'cpu 16\ngpu.intel.com/xe 2' K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_GPUS=2
agent_says "K3D_GPU_AGENT_GPUS=2 requests two gpu.intel.com/xe" "${r}" 0 \
    'gpu\.intel\.com/xe: "?2"?$' "" STUB_ALLOC=$'cpu 16\ngpu.intel.com/xe 2' K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_GPUS=2
agent_says "more GPUs than the cluster advertises fails" "${r}" fail \
    'K3D_GPU_AGENT_GPUS=2 but the cluster advertises 1 gpu.intel.com/xe' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_GPUS=2
agent_says "a non-numeric K3D_GPU_AGENT_GPUS is rejected" "${r}" fail \
    'K3D_GPU_AGENT_GPUS must be a positive integer' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_GPUS=0

agent_says "vllm with no allocatable Intel GPU fails instead of falling back to CPU" "${r}" fail \
    'runs on an Intel GPU only' "" STUB_ALLOC='cpu 16' K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm refuses K3D_GPU_AGENT_DEVICE=CPU" "${r}" fail \
    'runs on an Intel GPU only.*asks for CPU' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_DEVICE=CPU
agent_says "vllm on a Battlemage host prints no hardware warning" "${r}" 0 \
    'validated on Arc B-series' negate STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "vllm on a hybrid node sits on P-cores" "${r}" 0 \
    'k3d-gpu.io/cores: performance' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm STUB_NODE_LABELS=k3d-gpu.io/hybrid-cpu=true
agent_says "the OVMS default still owns the agent when no backend is set" "${r}" 0 \
    'image: openvino/model_server@' "" STUB_ALLOC="${XE1}"
agent_says "the agent line names the GPU resource it requests" "${r}" 0 \
    'on GPU \(gpu\.intel\.com/xe\)' "" STUB_ALLOC="${XE1}" STUB_NODE_LABELS=gpu.intel.com/xmx=true
agent_says "vllm auto runs on the Intel GPU even where OVMS would pick the CPU (no XMX)" "${r}" 0 \
    'gpu\.intel\.com/xe: "?1"?' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm STUB_NODE_LABELS=gpu.intel.com/xmx=false
agent_says "vllm with no allocatable GPU names the backend, not K3D_GPU_AGENT_DEVICE" "${r}" fail \
    'K3D_GPU_AGENT_DEVICE=GPU' negate STUB_ALLOC='cpu 16' K3D_GPU_AGENT_BACKEND=vllm

r=$(newroot agent-lnl)
pci "${r}" 0000:00:02.0 0x8086 0x64a0 xe; render "${r}" 0000:00:02.0 128
agent_says "vllm on a non-Battlemage Intel GPU warns and names the GPU" "${r}" 0 \
    'validated on Arc B-series \(Battlemage\); this host.s Intel GPU is Lunar Lake' "" \
    STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm

# `agent up|down` with vllm: the OVMS server is stopped first, the API is /v1.
log="${WORK}/kubectl.log"
r=$(newroot agent-bmg)
: > "${log}"
WARM='{"choices":[{"message":{"content":"I am a model."}}]}'
out=$(env K3D_GPU_SYSROOT="${r}" PATH="${ASTUB}:${PATH}" STUB_ALLOC="${XE1}" STUB_LOG="${log}" STUB_EXEC="${WARM}" \
    K3D_GPU_AGENT_BACKEND=vllm bash "${K3D_GPU}" agent up 2>&1)
rc=$?
agent_log_has() {
    local name=$1 re=$2
    if grep -E -- "${re}" "${log}" >/dev/null; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name} (no /${re}/ in kubectl calls)"
        awk '{print "  | " $0}' "${log}"
    fi
}
if [ "${rc}" -eq 0 ]; then PASS=$((PASS + 1)); echo "ok   agent up with vllm succeeds against a stub cluster"
else FAILS=$((FAILS + 1)); echo "FAIL agent up with vllm (rc=${rc})"; awk '{print "  | " $0}' <<< "${out}"; fi
agent_log_has "agent up with vllm applies the vllm Deployment" '^  name: vllm$'
# agent_log_before <name> <ERE a> <ERE b>: the first log line matching a precedes the first matching b.
agent_log_before() {
    local name=$1 a b
    a=$(grep -n -E -m1 -- "$2" "${log}" | cut -d: -f1)
    b=$(grep -n -E -m1 -- "$3" "${log}" | cut -d: -f1)
    if [ -n "${a}" ] && [ -n "${b}" ] && [ "${a}" -lt "${b}" ]; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name} (/$2/ at line ${a:-none}, /$3/ at line ${b:-none})"
        awk '{print "  | " $0}' "${log}"
    fi
}
agent_log_before "agent up with vllm stops the OVMS server before applying" \
    'delete deployment/ovms service/ovms' '^kubectl apply -f -$'
agent_log_has "agent up with vllm waits for deployment/vllm" 'rollout status deployment/vllm'
agent_log_has "agent up with vllm warms up through /v1/chat/completions" \
    'create --raw /api/v1/namespaces/k3d-gpu-agent/services/vllm:http/proxy/v1/chat/completions'
: > "${log}"
env K3D_GPU_SYSROOT="${r}" PATH="${ASTUB}:${PATH}" STUB_LOG="${log}" bash "${K3D_GPU}" agent down >/dev/null 2>&1
agent_log_has "agent down removes the vllm server" 'delete deployment/vllm service/vllm'
agent_log_has "agent down removes the OVMS server" 'delete deployment/ovms service/ovms'
: > "${log}"
env K3D_GPU_SYSROOT="${r}" PATH="${ASTUB}:${PATH}" STUB_ALLOC="${XE1}" STUB_LOG="${log}" STUB_EXEC="${WARM}" \
    bash "${K3D_GPU}" agent up >/dev/null 2>&1
agent_log_has "agent up with OVMS applies the ovms Deployment" '^  name: ovms$'
agent_log_before "agent up with OVMS stops the vllm server before applying" \
    'delete deployment/vllm service/vllm' '^kubectl apply -f -$'
agent_log_has "agent up with OVMS warms up inside its pod at /v3/chat/completions" \
    'exec deployment/ovms -c ovms -- curl .*http://127\.0\.0\.1:8000/v3/chat/completions'

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
