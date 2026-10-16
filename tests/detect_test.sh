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

# ---- Intel GPU DRA mode (K3D_GPU_DRA=1) ---------------------------------------
# DSTUB's kubectl answers what `up` and `test` ask in DRA mode: STUB_K8S_VERSION
# (/version), STUB_API_VERSIONS, STUB_SLICES (rendered `get resourceslices`
# lines: driver pool generation kmd taint-effects), STUB_ALLOCATABLE, the pod
# phase and logs. It appends every call to STUB_LOG and saves `apply -f -` input
# to STUB_APPLIED. Its k3d appends its arguments, one per line, to STUB_LOG.
DSTUB="${WORK}/dbin"
mkdir -p "${DSTUB}"
cat > "${DSTUB}/kubectl" <<'EOF'
#!/bin/sh
echo "kubectl $*" >> "${STUB_LOG}"
case "$*" in
    "get --raw /version") printf '{\n  "major": "1",\n  "gitVersion": "%s"\n}\n' "${STUB_K8S_VERSION}" ;;
    api-versions) for a in ${STUB_API_VERSIONS}; do echo "${a}"; done ;;
    "get resourceslices"*) printf '%s' "${STUB_SLICES:-}" ;;
    "get nodes"*) printf '%s' "${STUB_ALLOCATABLE:-}" ;;
    "apply -f -") cat > "${STUB_APPLIED}" ;;
    "get pod"*) echo "${STUB_POD_PHASE:-Succeeded}" ;;
    logs*) printf %s "${STUB_POD_LOGS:-}" ;;
esac
exit 0
EOF
printf '#!/bin/sh\nfor a in "$@"; do echo "k3d-arg $a"; done >> "${STUB_LOG}"\n' > "${DSTUB}/k3d"
cp "${STUB_BIN}/docker" "${DSTUB}/docker"
chmod +x "${DSTUB}"/*

DRA_APIS="apps/v1 v1 resource.k8s.io/v1 resource.k8s.io/v1beta2"
# One untainted xe GPU in pool generation 3, next to devices a gpu.intel.com
# claim cannot get: an older generation, a tainted GPU, a vfio-pci GPU, another driver.
DRA_SLICES="gpu.intel.com node0 2 xe
gpu.intel.com node0 3 xe
gpu.intel.com node0 3 i915 NoSchedule,
gpu.intel.com node0 3 vfio-pci
gpu.example.com node0 9 xe
"
DRA_NONE="gpu.intel.com node0 3 xe NoExecute,
gpu.intel.com node0 3 vfio-pci
"
CASE=0

# run_says <cmd> <name> <root> <want rc: 0|fail> <ERE> [env assignments...]: run a
# subcommand against DSTUB. Leaves the call log in ${LOG} and applied YAML in ${APPLIED}.
run_says() {
    local cmd=$1 name=$2 root=$3 want=$4 re=$5 out rc
    shift 5
    CASE=$((CASE + 1)); LOG="${WORK}/dra-${CASE}.log"; APPLIED="${WORK}/dra-${CASE}.yaml"
    : > "${LOG}"
    out=$(env K3D_GPU_SYSROOT="${root}" PATH="${DSTUB}:${PATH}" STUB_LOG="${LOG}" STUB_APPLIED="${APPLIED}" \
        STUB_K8S_VERSION=v1.34.1+k3s1 STUB_API_VERSIONS="${DRA_APIS}" "$@" bash "${K3D_GPU}" "${cmd}" 2>&1)
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

# file_has <name> <file> <ERE> [negate]: <file> must contain a line matching <ERE>
# (must not when a 4th argument is given).
file_has() {
    local name=$1 file=$2 re=$3 negate=${4:-} hit=0
    grep -E -- "${re}" "${file}" >/dev/null 2>&1 && hit=1
    if { [ -z "${negate}" ] && [ "${hit}" -eq 1 ]; } || { [ -n "${negate}" ] && [ "${hit}" -eq 0 ]; }; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (${negate:+not }matching /${re}/ in ${file})"
        sed 's/^/  | /' "${file}" 2>/dev/null
    fi
}

r=$(newroot dra-intel)
kernel "${r}" 6.18.1
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe; render "${r}" 0000:03:00.0 128

expect "DRA mode lists the Intel DeviceClass instead of extended resources" "${r}" \
    "intel deviceclass/gpu.intel.com" K3D_GPU_DRA=1
expect "K3D_GPU_DRA=0 keeps the device plugin resources" "${r}" "intel gpu.intel.com/xe" K3D_GPU_DRA=0
expect_fail "K3D_GPU_DRA other than 0 or 1 is rejected" "${r}" K3D_GPU_DRA=yes

doctor_says "doctor reports DRA mode with an Intel GPU" "${r}" 'ok: up deploys the Intel GPU DRA driver' "" K3D_GPU_DRA=1
r2=$(newroot dra-no-intel)
kernel "${r2}" 6.18.1
mkdir -p "${r2}/sys/module/nvidia"; touch "${r2}/dev/nvidiactl"
doctor_says "doctor fails DRA mode without an Intel GPU" "${r2}" 'fail.*K3D_GPU_DRA=1 but no Intel GPU' "" K3D_GPU_DRA=1

run_says up "up in DRA mode verifies by counting ResourceSlice devices" "${r}" 0 'schedulable: intel=1\.' \
    K3D_GPU_DRA=1 STUB_SLICES="${DRA_SLICES}"
file_has "DRA up disables the intel-gpu-plugin addon" "${LOG}" '^k3d-arg --k3s-arg=--disable=intel-gpu-plugin@server:0$'
file_has "DRA up keeps the DRA driver addon" "${LOG}" 'disable=intel-gpu-dra' negate
file_has "DRA up waits for the kubelet plugin rollout" "${LOG}" \
    '^kubectl -n intel-gpu-resource-driver rollout status daemonset/intel-gpu-resource-driver-kubelet-plugin'
file_has "DRA up checks for resource.k8s.io/v1" "${LOG}" '^kubectl api-versions$'

run_says up "up without DRA disables the DRA addon and verifies allocatable" "${r}" 0 'schedulable: intel=2\.' \
    STUB_ALLOCATABLE="gpu.intel.com/xe 2
"
file_has "non-DRA up disables the intel-gpu-dra addon" "${LOG}" '^k3d-arg --k3s-arg=--disable=intel-gpu-dra@server:0$'
file_has "non-DRA up keeps intel-gpu-plugin" "${LOG}" 'disable=intel-gpu-plugin' negate
file_has "non-DRA up never reads ResourceSlices" "${LOG}" 'resourceslices' negate

run_says up "DRA on Kubernetes 1.34 warns that upstream supports 1.36+" "${r}" 0 'v1\.34\.1\+k3s1.*supported upstream on 1\.36\+' \
    K3D_GPU_DRA=1 STUB_SLICES="${DRA_SLICES}"
out=$(env K3D_GPU_SYSROOT="${r}" PATH="${DSTUB}:${PATH}" STUB_LOG=/dev/null STUB_K8S_VERSION=v1.36.5+k3s1 \
    STUB_API_VERSIONS="${DRA_APIS}" STUB_SLICES="${DRA_SLICES}" K3D_GPU_DRA=1 bash "${K3D_GPU}" up 2>&1)
if grep 'supported upstream' <<< "${out}" >/dev/null; then
    FAILS=$((FAILS + 1)); echo "FAIL DRA on 1.36 prints no version warning"; sed 's/^/  | /' <<< "${out}"
else
    PASS=$((PASS + 1)); echo "ok   DRA on 1.36 prints no version warning"
fi
run_says up "DRA without resource.k8s.io/v1 fails with the cluster version" "${r}" fail \
    'needs resource\.k8s\.io/v1 \(Kubernetes 1\.34\+\), cluster runs v1\.33\.5\+k3s1' \
    K3D_GPU_DRA=1 STUB_K8S_VERSION=v1.33.5+k3s1 STUB_API_VERSIONS="apps/v1 v1 resource.k8s.io/v1beta2"
run_says up "DRA without an Intel GPU fails before creating a cluster" "${r2}" fail 'K3D_GPU_DRA=1 needs an Intel GPU' \
    K3D_GPU_DRA=1
file_has "no cluster is created when DRA has no Intel GPU" "${LOG}" '^k3d-arg' negate

run_says test "DRA test pod gets a GPU through a ResourceClaimTemplate" "${r}" 0 'renderD128' \
    K3D_GPU_DRA=1 STUB_SLICES="${DRA_SLICES}" STUB_POD_LOGS="crw-rw---- 1 root render 226, 128 Oct 16 12:00 renderD128"
file_has "test creates a v1 ResourceClaimTemplate" "${APPLIED}" '^kind: ResourceClaimTemplate$'
file_has "the template requests the gpu.intel.com DeviceClass" "${APPLIED}" 'deviceClassName: gpu\.intel\.com$'
file_has "the test pod claims through the template" "${LOG}" \
    '^kubectl run intel-gpu-dra-test .*"resourceClaimTemplateName":"intel-gpu-dra-test".*"claims":\[\{"name":"gpu"\}\]'
file_has "the template is deleted after the test" "${LOG}" '^kubectl delete resourceclaimtemplate intel-gpu-dra-test'
file_has "DRA test never requests an extended resource" "${LOG}" 'gpu\.intel\.com/(xe|i915)' negate

run_says test "DRA test fails when the claim pod fails, and cleans up" "${r}" fail 'intel-gpu-dra-test pod failed' \
    K3D_GPU_DRA=1 STUB_SLICES="${DRA_SLICES}" STUB_POD_PHASE=Failed STUB_POD_LOGS="ls: /dev/dri: No such file or directory"
file_has "a failed DRA test still deletes the template" "${LOG}" '^kubectl delete resourceclaimtemplate intel-gpu-dra-test'
run_says test "DRA test fails when only tainted or vfio GPUs are published" "${r}" fail 'no allocatable gpu\.intel\.com device' \
    K3D_GPU_DRA=1 STUB_SLICES="${DRA_NONE}"
file_has "no DRA pod runs without an allocatable device" "${LOG}" '^kubectl run' negate

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
