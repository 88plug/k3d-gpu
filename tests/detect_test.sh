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

# doctor_says <name> <root> <ERE> [negate]: doctor output (any exit code) must
# match <ERE>, or must not when a 4th argument is given.
doctor_says() {
    local name=$1 root=$2 re=$3 negate=${4:-} out hit=0
    out=$(env K3D_GPU_SYSROOT="${root}" PATH="${STUB_BIN}:${PATH}" bash "${K3D_GPU}" doctor 2>&1)
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
for tool in docker k3d kubectl; do printf '#!/bin/sh\nexit 0\n' > "${STUB_BIN}/${tool}"; chmod +x "${STUB_BIN}/${tool}"; done

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

r=$(newroot gaudi-doc)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:0b.0 0x8086 0x643e intel_vpu; accel "${r}" 0000:00:0b.0 0
doctor_says "an NPU accel node does not satisfy the Gaudi check" "${r}" 'Checking Intel Gaudi' negate

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
