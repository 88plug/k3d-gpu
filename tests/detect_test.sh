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
for tool in k3d kubectl; do printf '#!/bin/sh\nexit 0\n' > "${STUB_BIN}/${tool}"; chmod +x "${STUB_BIN}/${tool}"; done
# The docker stub answers `docker info --format` from STUB_DOCKER_RUNTIMES (runtime
# names) and STUB_DOCKER_CDI (CDI device IDs), so doctor's runtime checks are testable.
cat > "${STUB_BIN}/docker" <<'EOF'
#!/bin/sh
[ "$1" = info ] || exit 0
case "$*" in
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

# ---- docs ---------------------------------------------------------------------

# Every relative link in README.md and docs/*.md must name a file in this tree.
REPO="$(dirname "${K3D_GPU}")/.."
dead=""
for md in "${REPO}/README.md" "${REPO}"/docs/*.md; do
    while read -r target; do
        [ -e "$(dirname "${md}")/${target}" ] || dead+=" ${md#"${REPO}"/}:${target}"
    done < <(grep -oE '\]\([^)#[:space:]]+' "${md}" | sed 's/^](//' | grep -vE '^[a-z]+:')
done
if [ -z "${dead}" ]; then
    PASS=$((PASS + 1)); echo "ok   relative doc links resolve"
else
    FAILS=$((FAILS + 1)); echo "FAIL relative doc links resolve; dead:${dead}"
fi

# vendor_total counts AMX/hybrid as distinct labelled nodes, not one per label:
# labels L1 and L2 are both on nodes a and b, L3 on none, so the answer is 2.
# Two nodes under two labels also catches node lists joined without a newline.
VT_FN=$(sed -n '/^vendor_total() {/,/^}/p' "${K3D_GPU}")
vt() {
    bash -euo pipefail -c "${VT_FN}"'
        vendor_resources() { printf "L1\nL2\nL3\n"; }
        kubectl() { [ "${STUB_NONE:-}" ] && return 1; case "$4" in L1|L2) printf "node/a\nnode/b\n" ;; esac; }
        vendor_total "$1"' _ "$1"
}
for case in "amx 2" "hybrid 2" "amx 0 none"; do
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
printf '#!/bin/sh\necho "${STUB_NVSMI:-}" | tr ";" "\\n"\nexit "${STUB_NVSMI_RC:-0}"\n' > "${STUB_BIN}/nvidia-smi"
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

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
