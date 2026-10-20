#!/usr/bin/env bash
# Host accelerator detection tests. Each case builds a fake sysfs/devfs tree and
# runs `k3d-gpu detect` against it via K3D_GPU_SYSROOT. No cluster, no hardware.
#
# Usage: tests/detect_test.sh

set -uo pipefail

K3D_GPU="$(cd "$(dirname "$0")/.." && pwd)/scripts/k3d-gpu"
WORK=$(mktemp -d)
# A measured autoquant pick on this dev box (EXPERIMENTS.md H27) must not leak into goldens.
export K3D_GPU_AUTOQUANT_CACHE="${WORK}/no-autoquant.json"
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

# ---- AMX model-derived labels and Xeon CPU Max memory modes ------------------

AMXL="feature.node.kubernetes.io/cpu-cpuid"
# xcpu <root> <family> <model> <flags> [model name]: one-CPU cpuinfo.
xcpu() {
    mkdir -p "$1/proc"
    printf 'processor\t: 0\ncpu family\t: %s\nmodel\t\t: %s\nmodel name\t: %s\nflags\t\t: fpu %s\n' \
        "$2" "$3" "${5:-Intel(R) Xeon(R)}" "$4" > "$1/proc/cpuinfo"
}
AMX3="amx_bf16 amx_tile amx_int8"

r=$(newroot gnr-bare); xcpu "${r}" 6 173 "${AMX3}"
expect "Granite Rapids 6/173 bare metal gets AMXFP16" "${r}" \
"amx ${AMXL}.AMXBF16=true ${AMXL}.AMXFP16=true ${AMXL}.AMXINT8=true ${AMXL}.AMXTILE=true"
r=$(newroot gnrd-bare); xcpu "${r}" 6 174 "${AMX3}"
expect "Granite Rapids-D 6/174 gets AMXFP16 and AMXCOMPLEX" "${r}" \
"amx ${AMXL}.AMXBF16=true ${AMXL}.AMXCOMPLEX=true ${AMXL}.AMXFP16=true ${AMXL}.AMXINT8=true ${AMXL}.AMXTILE=true"
r=$(newroot dmr-bare); xcpu "${r}" 19 1 "${AMX3}"
expect "Diamond Rapids 19/1 gets AMXFP16 and AMXCOMPLEX" "${r}" \
"amx ${AMXL}.AMXBF16=true ${AMXL}.AMXCOMPLEX=true ${AMXL}.AMXFP16=true ${AMXL}.AMXINT8=true ${AMXL}.AMXTILE=true"
r=$(newroot gnr-vm); xcpu "${r}" 6 173 "${AMX3} hypervisor"
expect "Granite Rapids under a hypervisor gets no inferred labels" "${r}" \
"amx ${AMXL}.AMXBF16=true ${AMXL}.AMXINT8=true ${AMXL}.AMXTILE=true"
r=$(newroot gnr-notile); xcpu "${r}" 6 173 "amx_bf16 amx_int8"
expect "Granite Rapids without amx_tile gets no inferred labels" "${r}" \
"amx ${AMXL}.AMXBF16=true ${AMXL}.AMXINT8=true"
r=$(newroot spr-bare); xcpu "${r}" 6 143 "${AMX3}"
expect "Sapphire Rapids 6/143 gets no AMXFP16" "${r}" \
"amx ${AMXL}.AMXBF16=true ${AMXL}.AMXINT8=true ${AMXL}.AMXTILE=true"
r=$(newroot gnr-dup); xcpu "${r}" 6 173 "${AMX3} amx_fp16"
expect "a kernel printing amx_fp16 gives one AMXFP16 label" "${r}" \
"amx ${AMXL}.AMXBF16=true ${AMXL}.AMXFP16=true ${AMXL}.AMXINT8=true ${AMXL}.AMXTILE=true"

# dax <root> <dev> <parent dir under sys/devices> <driver>: a /sys/bus/dax device.
dax() {
    local dir="$1/sys/devices/$3/$2"
    mkdir -p "${dir}" "$1/sys/bus/dax/devices" "$1/sys/bus/dax/drivers/$4"
    ln -sfn "$1/sys/bus/dax/drivers/$4" "${dir}/driver"
    ln -sfn "${dir}" "$1/sys/bus/dax/devices/$2"
}
MAX="Intel(R) Xeon(R) CPU Max 9480"
r=$(newroot max-devdax); kernel "${r}" 6.18.1; xcpu "${r}" 6 143 "${AMX3}" "${MAX}"
dax "${r}" dax0.0 platform/hmem.0 device_dax
doctor_says "Xeon Max hmem dax on device_dax gets the system-ram advice" "${r}" 'dax0\.0 in devdax mode.*daxctl reconfigure-device --mode=system-ram'
r=$(newroot max-kmem); kernel "${r}" 6.18.1; xcpu "${r}" 6 143 "${AMX3}" "${MAX}"
dax "${r}" dax0.0 platform/hmem.0 kmem
doctor_says "Xeon Max hmem dax already on kmem is silent" "${r}" 'devdax mode' negate
r=$(newroot spr-pmem); kernel "${r}" 6.18.1; xcpu "${r}" 6 143 "${AMX3}" "Intel(R) Xeon(R) Platinum 8480+"
dax "${r}" dax0.0 platform/e820_pmem/ndbus0/region0/dax0.1 device_dax
doctor_says "SPR PMem devdax namespace gets no system-ram advice" "${r}" 'system-ram|HBM the kernel' negate
r=$(newroot max-pmem); kernel "${r}" 6.18.1; xcpu "${r}" 6 143 "${AMX3}" "${MAX}"
dax "${r}" dax0.0 platform/e820_pmem/ndbus0/region0/dax0.1 device_dax
doctor_says "PMem devdax on a Xeon Max host is not called HBM" "${r}" 'system-ram|HBM the kernel' negate
r=$(newroot spr-cxl); kernel "${r}" 6.18.1; xcpu "${r}" 6 143 "${AMX3}" "Intel(R) Xeon(R) Platinum 8480+"
dax "${r}" dax0.0 platform/ACPI0017:00/root0/decoder0.0/region0/dax_region0 device_dax
doctor_says "SPR CXL devdax is not called HBM" "${r}" 'system-ram|HBM the kernel' negate
r=$(newroot spr-hmem); kernel "${r}" 6.18.1; xcpu "${r}" 6 143 "${AMX3}" "Intel(R) Xeon(R) Platinum 8480+"
dax "${r}" dax0.0 platform/hmem.0 device_dax
doctor_says "non-Max 6/143 hmem dax gets no HBM advice" "${r}" 'system-ram|HBM the kernel' negate

# msc <root> <node>: node<N> with a 16 GiB direct-mapped memory-side cache.
msc() {
    local d="$1/sys/devices/system/node/node$2/memory_side_cache/index1"
    mkdir -p "${d}"; echo 17179869184 > "${d}/size"; echo 0 > "${d}/indexing"
}
r=$(newroot max-cache); kernel "${r}" 6.18.1; xcpu "${r}" 6 143 "${AMX3}" "${MAX}"
for n in 0 1 2 10; do msc "${r}" "${n}"; done
doctor_says "memory-side cache nodes are listed in numeric order" "${r}" 'node 0 index 1 .*; node 1 .*; node 2 .*; node 10 index 1'
doctor_says "memory-side cache without shuffle on suggests page_alloc.shuffle=y" "${r}" 'CONFIG_SHUFFLE_PAGE_ALLOCATOR=y.*sudo cat /sys/module/page_alloc/parameters/shuffle.*grubby --update-kernel=ALL --args=page_alloc\.shuffle=y.*--remove-args=page_alloc\.shuffle'
mkdir -p "${r}/sys/module/page_alloc/parameters"; echo Y > "${r}/sys/module/page_alloc/parameters/shuffle"
doctor_says "memory-side cache with shuffle on reports ok" "${r}" 'ok: page_alloc\.shuffle is on'
doctor_says "memory-side cache with shuffle on does not suggest it" "${r}" 'Intel suggests page_alloc' negate

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
doctor_says "Panther Lake iGPU on 6.16 gets no force_probe advice" "${r}" 'force_probe=b0' negate

# Wildcat Lake iGPU: v6.17 INTEL_PTL_IDS binds 0xfd80/0xfd81 to ptl_desc.
r=$(newroot wcl-old-kernel)
kernel "${r}" 6.16.3
pci "${r}" 0000:00:02.0 0x8086 0xfd80 xe; render "${r}" 0000:00:02.0 128
doctor_says "Wildcat Lake iGPU on 6.16 warns about kernel 6.17" "${r}" 'Wildcat Lake.*6\.17'
doctor_says "Wildcat Lake iGPU on 6.16 gets no force_probe advice" "${r}" 'force_probe=fd8' negate
r=$(newroot wcl-unbound-old)
kernel "${r}" 6.16.3
pci "${r}" 0000:00:02.0 0x8086 0xfd80 "" 0x030000
doctor_says "unbound Wildcat Lake on 6.16 asks for kernel 6.17" "${r}" 'Wildcat Lake \(0xfd80\) has no driver.*kernel 6\.17\+'
doctor_says "unbound Wildcat Lake on 6.16 gets no force_probe advice" "${r}" 'force_probe=fd8' negate
r=$(newroot wcl-617)
kernel "${r}" 6.17.2
pci "${r}" 0000:00:02.0 0x8086 0xfd80 xe; render "${r}" 0000:00:02.0 128
firmware "${r}" xe/ptl_guc_70.bin
doctor_says "Wildcat Lake iGPU on 6.17 has no kernel warning" "${r}" 'needs [0-9]' negate

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
echo 16 > "${r}/sys/devices/pci0000:00/0000:6b:00.0/sriov_totalvfs"
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

r=$(newroot bmg-b60-backport)
kernel "${r}" 6.14.3
pci "${r}" 0000:03:00.0 0x8086 0xe211 xe; render "${r}" 0000:03:00.0 128
doctor_says "Pro B60 bound on 6.14 is a stable backport, not force_probe" "${r}" 'is in this kernel.s xe id table.*6\.15'
doctor_says "Pro B60 bound on 6.14 gets no force_probe" "${r}" 'force_probe=e211' negate

r=$(newroot bmg-b580-616)
kernel "${r}" 6.16.1
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe; render "${r}" 0000:03:00.0 128
doctor_says "B580 on 6.16 has no kernel warning" "${r}" 'Battlemage.*(needs kernel|stable backport)' negate

r=$(newroot dg2-a770)
kernel "${r}" 6.8.0
pci "${r}" 0000:03:00.0 0x8086 0x56a0 i915; render "${r}" 0000:03:00.0 128
doctor_says "Arc A770 is Alchemist DG2-G10" "${r}" 'Intel Alchemist DG2-G10 \(0x56a0\) bound to i915'
doctor_says "Arc A770 gets the xmx label" "${r}" 'gpu.intel.com/xmx=true'

r=$(newroot dg2-a770-old)
kernel "${r}" 6.1.0
pci "${r}" 0000:03:00.0 0x8086 0x56a0 i915; render "${r}" 0000:03:00.0 128
doctor_says "Arc A770 on 6.1 needs 6.2 or i915.force_probe" "${r}" 'Alchemist DG2-G10.*6\.2\+.*i915.force_probe=56a0'

# Late Alchemist ids joined i915's table after DG2 bound by default (6.2):
# 56BA-56BD in 6.7, 56BE/56BF in 6.10. force_probe cannot add a missing id.
r=$(newroot dg2-56ba-66)
kernel "${r}" 6.6.0
pci "${r}" 0000:03:00.0 0x8086 0x56ba i915; render "${r}" 0000:03:00.0 128
doctor_says "bound 0x56ba on 6.6 is a backport" "${r}" 'Alchemist DG2-G11 \(0x56ba\) is in this kernel.s i915 id table.*6\.7'
doctor_says "bound 0x56ba on 6.6 gets no force_probe" "${r}" 'force_probe=56ba' negate

r=$(newroot dg2-56ba-65-unbound)
kernel "${r}" 6.5.0
pci "${r}" 0000:03:00.0 0x8086 0x56ba "" 0x030000
doctor_says "unbound 0x56ba on 6.5 needs a kernel" "${r}" 'DG2-G11 \(0x56ba\) has no driver .*i915 lists this id from kernel 6\.7 .*upgrade the kernel'
doctor_says "unbound 0x56ba on 6.5 gets no modprobe or force_probe" "${r}" '0x56ba.*(modprobe i915|force_probe=56ba)' negate

r=$(newroot dg2-56be-69-unbound)
kernel "${r}" 6.9.0
pci "${r}" 0000:03:00.0 0x8086 0x56be "" 0x030000
doctor_says "unbound 0x56be on 6.9 needs 6.10" "${r}" 'DG2-G10 \(0x56be\) has no driver .*from kernel 6\.10 '
doctor_says "unbound 0x56be on 6.9 gets no modprobe" "${r}" '0x56be.*modprobe i915' negate

r=$(newroot dg2-56be-68)
kernel "${r}" 6.8.0
pci "${r}" 0000:03:00.0 0x8086 0x56be i915; render "${r}" 0000:03:00.0 128
doctor_says "bound 0x56be on 6.8 is a backport" "${r}" 'DG2-G10 \(0x56be\) is in this kernel.s i915 id table.*6\.10'

r=$(newroot flex-515-unbound)
kernel "${r}" 5.15.0
pci "${r}" 0000:03:00.0 0x8086 0x56c1 "" 0x038000
doctor_says "unbound Flex on 5.15 gets dkms" "${r}" 'Flex \(ATS-M\) \(0x56c1\) has no driver .*has no xe .*intel-i915-dkms'
doctor_says "unbound Flex on 5.15 gets no xe.force_probe" "${r}" 'xe.force_probe|force-probe xe' negate

r=$(newroot flex-68-unbound)
kernel "${r}" 6.8.0
pci "${r}" 0000:03:00.0 0x8086 0x56c1 "" 0x038000
doctor_says "unbound Flex on 6.8 points at the exact experimental apply" "${r}" 'Flex \(ATS-M\) \(0x56c1\) has no driver .*doctor --experimental, xe-force-probe'

r=$(newroot arl-s)
kernel "${r}" 6.10.4
pci "${r}" 0000:00:02.0 0x8086 0xb640 i915; render "${r}" 0000:00:02.0 128
doctor_says "Arrow Lake-S 0xb640 needs kernel 6.12" "${r}" 'Arrow Lake.*6\.12'

# cpuinfo <root> <family> <model> <flags> [vendor]: the first CPU's vendor_id
# (default GenuineIntel), family, model and flags.
cpuinfo() {
    mkdir -p "$1/proc"
    printf 'processor\t: 0\nvendor_id\t: %s\ncpu family\t: %s\nmodel\t\t: %s\nmodel name\t: Fake CPU\nflags\t\t: %s\n' "${5:-GenuineIntel}" "$2" "$3" "$4" > "$1/proc/cpuinfo"
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

# Newest-ISA CPU models: families 18 (Nova Lake) and 19 (Diamond Rapids) are Intel;
# their ISA is reported as a gcc -march prediction, never as a cpuinfo read.
for c in "19 1|Diamond Rapids-X|AMX-FP8" "18 1|Nova Lake|no AMX" "18 3|Nova Lake-L|APX_F" "6 173|Granite Rapids-X|AVX10\.1" "6 174|Granite Rapids-D|AMX-COMPLEX"; do
    IFS='|' read -r fm want isa <<< "${c}"
    r=$(newroot "isa-${fm// /-}")
    cpuinfo "${r}" "${fm% *}" "${fm#* }" "fpu avx512f"
    doctor_says "family/model ${fm} is ${want}, ISA predicted not measured" "${r}" "info: ${want} \\(family ${fm% *}, model ${fm#* }\\): ISA predicted by gcc PTA_[A-Z_]+, not read from CPUID: .*${isa}"
done
r=$(newroot isa-unknown)
cpuinfo "${r}" 6 183 "fpu"
doctor_says "Raptor Lake (6/183) gets no ISA prediction" "${r}" 'ISA predicted by gcc' negate
# Family/model ids are Intel's only with vendor_id GenuineIntel: AMD Llano prints 18/1.
r=$(newroot isa-amd-18-1)
cpuinfo "${r}" 18 1 "fpu" AuthenticAMD
doctor_says "AuthenticAMD family 18 model 1 (Llano) is not Nova Lake" "${r}" 'ISA predicted by gcc' negate
r=$(newroot isa-other-19-1)
cpuinfo "${r}" 19 1 "fpu" HygonGenuine
doctor_says "non-Intel family 19 model 1 is not Diamond Rapids-X" "${r}" 'ISA predicted by gcc' negate
# Cancelled AMX extensions never become labels even if a kernel prints them.
r=$(newroot amx-cancelled)
cpuinfo "${r}" 19 1 "fpu amx_tile amx_tf32 amx_transpose amx_int8"
doctor_says "amx_tf32 and amx_transpose are never reported" "${r}" 'ok: AMX \(amx_int8 amx_tile\)'
doctor_says "amx_tf32 never named as an AMX flag" "${r}" 'ok: AMX \([^)]*tf32' negate
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
# Two GPUs on one driver, one with XMX and one without: the device plugin may
# hand out either, so that driver is labelled as not all-XMX.
r=$(newroot xmx-same-driver)
kernel "${r}" 6.14.0
pci "${r}" 0000:00:02.0 0x8086 0xa780 i915; render "${r}" 0000:00:02.0 128
pci "${r}" 0000:03:00.0 0x8086 0x56a0 i915; render "${r}" 0000:03:00.0 129
doctor_says "UHD 770 + Arc A770 on i915: i915 is not all-XMX" "${r}" 'gpu\.intel\.com/xmx=true .*k3d-gpu\.io/xmx\.i915=false'
doctor_says "Meteor Lake i915 + Battlemage xe: xe is not flagged" "$(newroot xmx-mixed)" 'k3d-gpu\.io/xmx\.xe' negate
doctor_says "Arrow Lake-H alone gets no per-driver label" "$(newroot xmx-arlh)" 'k3d-gpu\.io/xmx\.' negate
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
# xe has no per-engine sysfs; its engine counts come from the DRM usage stats of a client on the
# render node (drm-usage-stats.rst: drm-cycles-<class>, drm-engine-capacity-<class> when > 1).
# Values from an Arc Pro B70 (0xe223) on xe, kernel 7.0.0-31 (crescent, 2026-10-20).
xe_fdinfo() { mkdir -p "$1/proc/drm-fdinfo"; printf "$3" > "$1/proc/drm-fdinfo/renderD$2"; }
B70_FDINFO='drm-driver:\txe\ndrm-pdev:\t0000:12:00.0\ndrm-cycles-rcs:\t0\ndrm-total-cycles-rcs:\t5798\ndrm-cycles-vcs:\t0\ndrm-total-cycles-vcs:\t5798\ndrm-engine-capacity-vcs:\t2\ndrm-cycles-vecs:\t0\ndrm-total-cycles-vecs:\t5798\ndrm-engine-capacity-vecs:\t2\ndrm-cycles-bcs:\t0\ndrm-cycles-ccs:\t0\n'
r=$(newroot media-xe-b70)
kernel "${r}" 7.0.0
pci "${r}" 0000:12:00.0 0x8086 0xe223 xe; render "${r}" 0000:12:00.0 128
xe_fdinfo "${r}" 128 "${B70_FDINFO}"
doctor_says "xe media engines come from the DRM usage stats" "${r}" 'ok: renderD128 media engines: vcs x2, vecs x2 \(xe DRM usage stats\)'
doctor_says "xe media engine counts are node labels" "${r}" 'gpu\.intel\.com/xmx=true gpu\.intel\.com/media-vcs=2 gpu\.intel\.com/media-vecs=2'
r=$(newroot media-xe-one)
kernel "${r}" 7.0.0
pci "${r}" 0000:12:00.0 0x8086 0xe223 xe; render "${r}" 0000:12:00.0 128
xe_fdinfo "${r}" 128 'drm-driver:\txe\ndrm-cycles-rcs:\t0\ndrm-cycles-vcs:\t0\ndrm-cycles-vecs:\t0\n'
doctor_says "an xe engine class with no capacity line counts one" "${r}" 'media-vcs=1 gpu\.intel\.com/media-vecs=1'
r=$(newroot media-xe-novcs)
kernel "${r}" 7.0.0
pci "${r}" 0000:12:00.0 0x8086 0xe223 xe; render "${r}" 0000:12:00.0 128
xe_fdinfo "${r}" 128 'drm-driver:\txe\ndrm-cycles-rcs:\t0\ndrm-cycles-ccs:\t0\n'
doctor_says "xe usage stats with no video class count zero" "${r}" 'media-vcs=0 gpu\.intel\.com/media-vecs=0'
r=$(newroot media-xe-i915stats)
kernel "${r}" 7.0.0
pci "${r}" 0000:12:00.0 0x8086 0xe223 xe; render "${r}" 0000:12:00.0 128
xe_fdinfo "${r}" 128 'drm-driver:\ti915\ndrm-engine-video:\t0 ns\ndrm-engine-capacity-video:\t2\n'
doctor_says "usage stats from another driver are not read as xe" "${r}" 'media-vcs' negate

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
doctor_says "hybrid, no rdt_a, E-core clusters: cpu-cap, ecluster not recommended" "${r}" \
    'info: noisy-neighbour isolation: cpu-cap — no rdt_a flag, .*k3d-gpu.io/cores=ecluster) is not recommended'
doctor_says "isolation never claims a measured effect" "${r}" 'isolation: .*(SUPPORTED|faster|% )' negate
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

# Lunar Lake (6/189): 4 P without SMT + 4 Skymont in cpu_atom, none with an L3.
r=$(newroot lnl-cpu)
kernel "${r}" 6.18.1
cpuinfo "${r}" 6 189 "fpu avx2 avx_vnni"
hybrid "${r}" 0-3 4-7
for c in 0 1 2 3; do cpucache "${r}" "${c}" 3 0-3; cpucache "${r}" "${c}" 2 "${c}"; corecpus "${r}" "${c}" "${c}"; done
for c in 4 5 6 7; do cpucache "${r}" "${c}" 2 4-7; corecpus "${r}" "${c}" "${c}"; done
doctor_says "Lunar Lake: every cpu_atom CPU is an LP E-core" "${r}" 'ok: hybrid CPU: P-cores 0-3, E-cores none, LP E-cores 4-7 '
doctor_says "Lunar Lake: no E-core cluster line" "${r}" 'E-core clusters' negate
doctor_says "Lunar Lake: the expected topology gives no note" "${r}" 'Lunar Lake topology' negate
cpucache "${r}" 4 3 0-7
doctor_says "Lunar Lake: an E-core with an L3 still has 4+4 cores, no note" "${r}" 'Lunar Lake topology' negate
# No cache sysfs (VM, container): no LP split, but the core counts still match.
r=$(newroot lnl-no-cache)
kernel "${r}" 6.18.1
cpuinfo "${r}" 6 189 "fpu avx2 avx_vnni"
hybrid "${r}" 0-3 4-7
doctor_says "Lunar Lake without cache sysfs reads E-cores 4-7" "${r}" 'ok: hybrid CPU: P-cores 0-3, E-cores 4-7 '
doctor_says "Lunar Lake without cache sysfs gives no note" "${r}" 'Lunar Lake topology' negate
r=$(newroot lnl-5atom)
kernel "${r}" 6.18.1
cpuinfo "${r}" 6 189 "fpu avx2 avx_vnni"
hybrid "${r}" 0-3 4-8
doctor_says "Lunar Lake model with 5 atom CPUs gives a topology note" "${r}" 'note: Lunar Lake topology is 4 P-cores \+ 4 LP E-cores'
r=$(newroot lnl-lowpower)
kernel "${r}" 6.18.1
cpuinfo "${r}" 6 189 "fpu avx2 avx_vnni"
hybrid "${r}" 0-3 4-7
mkdir -p "${r}/sys/devices/cpu_lowpower"; echo 8-9 > "${r}/sys/devices/cpu_lowpower/cpus"
doctor_says "Lunar Lake model with a cpu_lowpower PMU gives a topology note" "${r}" 'note: Lunar Lake topology is 4 P-cores \+ 4 LP E-cores'
r=$(newroot lnl-garbled)
kernel "${r}" 6.18.1
cpuinfo "${r}" 6 189 "fpu avx2 avx_vnni"
hybrid "${r}" 0-3x 4-7
doctor_says "Lunar Lake model with a garbled cpu_core list warns" "${r}" 'warn.*malformed CPU list'
doctor_says "Lunar Lake model with a garbled cpu_core list gives no note" "${r}" 'Lunar Lake topology' negate

# Arrow Lake-U (6/181): SMT P-cores, LP E-cores in cpu_atom without an L3.
r=$(newroot arlu-cpu)
kernel "${r}" 6.18.1
cpuinfo "${r}" 6 181 "fpu avx2 avx_vnni"
hybrid "${r}" 0-3 4-13
for c in $(seq 0 11); do cpucache "${r}" "${c}" 3 0-11; done
for c in 0 1; do cpucache "${r}" "${c}" 2 0-1; corecpus "${r}" "${c}" 0-1; done
for c in 2 3; do cpucache "${r}" "${c}" 2 2-3; corecpus "${r}" "${c}" 2-3; done
for c in $(seq 4 11); do cpucache "${r}" "${c}" 2 "$(( c < 8 ? 4 : 8 ))-$(( c < 8 ? 7 : 11 ))"; corecpus "${r}" "${c}" "${c}"; done
for c in 12 13; do cpucache "${r}" "${c}" 2 12-13; corecpus "${r}" "${c}" "${c}"; done
doctor_says "Arrow Lake-U: LP E-cores split out of cpu_atom" "${r}" 'ok: hybrid CPU: P-cores 0-3, E-cores 4-11, LP E-cores 12-13 '
doctor_says "Arrow Lake-U: one thread per SMT P-core" "${r}" 'ok: physical P-cores \(one thread per core\): 0,2 '
doctor_says "Arrow Lake-U: no Lunar Lake note" "${r}" 'Lunar Lake topology' negate

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
doctor_says "rdt_a + mba: rdt-mba isolation, resctrl unmounted" "${r}" \
    'isolation: rdt-mba — CAT \+ MBA .*resctrl mounted: no\).*add mbAllocation: \{all: \["50%"\]\} to the BestEffort class'
doctor_says "rdt isolation adds no second apply line" "${r}" '^        apply:  sudo mount -t resctrl' negate
mkdir -p "${r}/sys/fs/resctrl/info"
doctor_says "a mounted resctrl is reported" "${r}" 'isolation: rdt-mba .*resctrl mounted: yes'
printf 'processor\t: 0\nflags\t\t: fpu cat_l2 cdp_l2 rdt_a\n' > "${r}/proc/cpuinfo"
doctor_says "rdt_a + cat_l2 without mba: rdt-cat, bandwidth stays shared" "${r}" \
    'isolation: rdt-cat — CAT can cap .*no MBA, so DRAM bandwidth stays shared'
printf 'processor\t: 0\nflags\t\t: fpu mba cat_l3\n' > "${r}/proc/cpuinfo"
doctor_says "mba and cat_l3 without rdt_a: no RDT mode" "${r}" 'isolation: cpu-cap — no rdt_a flag'

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

# ---- unconfigured DSA/IAA/QAT: exact enable + revert commands ---------------------

# gnr_unconfigured <root>: Xeon 6 (Granite Rapids) as it boots: DSA dsa0/dsa2 and IAA
# iax1 on idxd, disabled, no work queues (dsa2 has user PASID), and 4xxx + 420xx QAT
# PFs with SR-IOV but no VFs. sysfs names per Documentation/ABI/stable/sysfs-driver-dma-idxd.
gnr_unconfigured() {
    local d
    kernel "$1" 6.18.1
    printf 'processor\t: 0\nflags\t\t: fpu avx512f amx_bf16 amx_tile amx_int8\n' > "$1/proc/cpuinfo"
    idxd_dev "$1" 0000:e7:01.0 0x11fb dsa0 0
    idxd_dev "$1" 0000:e7:02.0 0x1216 iax1 0
    idxd_dev "$1" 0000:f2:01.0 0x11fb dsa2 0
    for d in dsa0 iax1 dsa2; do
        echo disabled > "$1/sys/bus/dsa/devices/${d}/state"
        echo 0 > "$1/sys/bus/dsa/devices/${d}/pasid_enabled"
    done
    echo 1 > "$1/sys/bus/dsa/devices/dsa2/pasid_enabled"
    pci "$1" 0000:01:00.0 0x8086 0x4940 4xxx 0x0b4000
    pci "$1" 0000:02:00.0 0x8086 0x4946 420xx 0x0b4000
    for d in 0000:01:00.0 0000:02:00.0; do
        echo 16 > "$1/sys/devices/pci0000:00/${d}/sriov_totalvfs"
        echo 0 > "$1/sys/devices/pci0000:00/${d}/sriov_numvfs"
    done
}

r=$(newroot gnr-unconfigured)
gnr_unconfigured "${r}"
ac="sudo accel-config"
doctor_says "unconfigured DSA: exact accel-config apply for dsa0 (dedicated, no PASID)" "${r}" \
    "^ {8}apply: +${ac} config-engine dsa0/engine0\.0 --group-id=0 && ${ac} config-wq dsa0/wq0\.0 --group-id=0 --type=user --mode=dedicated --wq-size=16 --priority=10 --driver-name=user --name=k3d-gpu && ${ac} enable-device dsa0 && ${ac} enable-wq dsa0/wq0\.0$"
doctor_says "unconfigured DSA: exact revert for dsa0" "${r}" \
    "^ {8}revert: ${ac} disable-wq dsa0/wq0\.0 && ${ac} disable-device dsa0$"
doctor_says "unconfigured DSA with user PASID gets a shared WQ with a threshold" "${r}" \
    "^ {8}apply: +${ac} config-engine dsa2/engine2\.0 --group-id=0 && ${ac} config-wq dsa2/wq2\.0 --group-id=0 --type=user --mode=shared --threshold=15 --wq-size=16 --priority=10 --driver-name=user --name=k3d-gpu && ${ac} enable-device dsa2 && ${ac} enable-wq dsa2/wq2\.0$"
doctor_says "unconfigured IAA: exact accel-config apply for iax1" "${r}" \
    "^ {8}apply: +${ac} config-engine iax1/engine1\.0 --group-id=0 && ${ac} config-wq iax1/wq1\.0 --group-id=0 --type=user --mode=dedicated --wq-size=16 --priority=10 --driver-name=user --name=k3d-gpu && ${ac} enable-device iax1 && ${ac} enable-wq iax1/wq1\.0$"
doctor_says "unconfigured IAA: exact revert for iax1" "${r}" \
    "^ {8}revert: ${ac} disable-wq iax1/wq1\.0 && ${ac} disable-device iax1$"
doctor_says "DSA warning names host-setup --apply as the one command" "${r}" \
    'warn.*: DSA: 2 device\(s\) but no user work queues.*k3d-gpu host-setup --apply'
doctor_says "QAT 4xxx: VF count is sriov_totalvfs, not N" "${r}" \
    '^ {8}apply: +echo 16 \| sudo tee /sys/bus/pci/devices/0000:01:00\.0/sriov_numvfs$'
doctor_says "QAT 4xxx: exact revert" "${r}" \
    '^ {8}revert: echo 0 \| sudo tee /sys/bus/pci/devices/0000:01:00\.0/sriov_numvfs$'
doctor_says "QAT 4xxx names the plugin's VF driver 4xxxvf" "${r}" 'warn.*: QAT 0000:01:00\.0 \(4xxx\): no VFs.*4xxxvf'
doctor_says "QAT 420xx names the plugin's VF driver 420xxvf" "${r}" 'warn.*: QAT 0000:02:00\.0 \(420xx\): no VFs.*420xxvf'
doctor_says "no placeholder N VF count is printed" "${r}" 'echo N ' negate
expect "unconfigured DSA/IAA/QAT stay out of detect's stdout" "${r}" \
"amx feature.node.kubernetes.io/cpu-cpuid.AMXBF16=true feature.node.kubernetes.io/cpu-cpuid.AMXINT8=true feature.node.kubernetes.io/cpu-cpuid.AMXTILE=true"
out=$(env K3D_GPU_SYSROOT="${r}" bash "${K3D_GPU}" detect 2>&1 >/dev/null)
if grep -E 'present but disabled: DSA dsa0 dsa2, IAA iax1, QAT 0000:01:00\.0 0000:02:00\.0 .*k3d-gpu host-setup --apply' <<< "${out}" >/dev/null; then
    PASS=$((PASS + 1)); echo "ok   detect names present-but-disabled engines and the enable command on stderr"
else
    FAILS=$((FAILS + 1)); echo "FAIL detect names present-but-disabled engines and the enable command on stderr"; sed 's/^/  | /' <<< "${out}"
fi
doctor_says "missing accel-config with no distro package says how to build it" "${r}" \
    'warn.*: accel-config is not installed and this distro has no package: build intel/idxd-config accel-config-v4\.1\.9'
mkdir -p "${r}/etc"; printf 'ID=ubuntu\nVERSION_ID="24.04"\n' > "${r}/etc/os-release"
doctor_says "missing accel-config on ubuntu gives the apt install as its apply" "${r}" \
    '^ {8}apply: +sudo apt-get update && sudo apt-get install -y accel-config$'
mkdir -p "${r}/usr/bin"; printf '#!/bin/sh\n' > "${r}/usr/bin/accel-config"; chmod +x "${r}/usr/bin/accel-config"
doctor_says "installed accel-config is not flagged" "${r}" 'accel-config is not installed' negate
doctor_says "each DSA device is its own finding with its WQ mode" "${r}" 'warn.*: DSA dsa0: disabled — 1 engine, 1 group, 1 dedicated user WQ'
echo enabled > "${r}/sys/bus/dsa/devices/dsa0/state"
doctor_says "an enabled DSA device (kernel work queues) gets no config commands" "${r}" 'dsa0/engine0' negate
rm "${r}/sys/devices/pci0000:00/0000:01:00.0/sriov_totalvfs"
doctor_says "QAT PF without sriov_totalvfs points at BIOS SR-IOV, no tee" "${r}" \
    'warn.*: QAT 0000:01:00\.0 \(4xxx\): no VFs and no SR-IOV.*sriov_totalvfs'
doctor_says "QAT PF without sriov_totalvfs gets no numvfs command" "${r}" '0000:01:00\.0/sriov_numvfs' negate

# Two-digit device ids name their engine and WQ by the same id, in numeric order;
# a kind that already has a user WQ gets no commands for its other devices.
r=$(newroot idxd-ids)
kernel "${r}" 6.18.1
idxd_dev "${r}" 0000:e7:01.0 0x11fb dsa2 0
idxd_dev "${r}" 0000:e8:01.0 0x11fb dsa10 0
idxd_dev "${r}" 0000:e9:01.0 0x1216 iax11 0
idxd_dev "${r}" 0000:ea:01.0 0x1216 iax3 0
mkdir -p "${r}/dev/iax"; touch "${r}/dev/iax/wq3.0"
doctor_says "dsa10 is configured as dsa10/engine10.0 and dsa10/wq10.0" "${r}" \
    "^ {8}apply: +${ac} config-engine dsa10/engine10\.0 --group-id=0 && ${ac} config-wq dsa10/wq10\.0 .*enable-wq dsa10/wq10\.0$"
out=$(env K3D_GPU_SYSROOT="${r}" PATH="${STUB_BIN}:${PATH}" bash "${K3D_GPU}" doctor 2>&1 | grep -oE 'warn.*: DSA dsa[0-9]+' | grep -oE 'dsa[0-9]+$' | paste -sd' ')
if [ "${out}" = "dsa2 dsa10" ]; then
    PASS=$((PASS + 1)); echo "ok   DSA devices are listed in numeric order"
else
    FAILS=$((FAILS + 1)); echo "FAIL DSA devices are listed in numeric order (got '${out}')"
fi
doctor_says "a kind with a user WQ gets no commands for its other devices" "${r}" 'iax11' negate
mkdir -p "${r}/dev/dsa"; touch "${r}/dev/dsa/wq2.0"
doctor_says "a kind with a user WQ is ok" "${r}" 'ok: DSA: 2 device\(s\), 1 user work queue'

r=$(newroot idxd-enabled)
kernel "${r}" 6.18.1
idxd_dev "${r}" 0000:e7:01.0 0x11fb dsa0 0
echo enabled > "${r}/sys/bus/dsa/devices/dsa0/state"
doctor_says "an all-enabled kind with no user WQ is left to its owner" "${r}" \
    'warn.*: DSA: 1 device\(s\) but no user work queues and none is disabled'
doctor_says "an all-enabled kind gets no host-setup pointer" "${r}" 'DSA.*host-setup --apply' negate

r=$(newroot qat-no-driver-novf)
kernel "${r}" 6.18.1
pci "${r}" 0000:01:00.0 0x8086 0x4940 "" 0x0b4000
echo 16 > "${r}/sys/devices/pci0000:00/0000:01:00.0/sriov_totalvfs"
doctor_says "QAT PF with no driver gets no numvfs command" "${r}" 'sriov_numvfs' negate

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
put "${good}" /sys/devices/system/cpu/intel_uncore_frequency/package_00_die_00/initial_min_freq_khz 800000
put "${good}" /sys/devices/system/cpu/intel_uncore_frequency/package_00_die_00/initial_max_freq_khz 4700000
put "${good}" /sys/devices/system/cpu/intel_uncore_frequency/package_00_die_00/min_freq_khz 800000
put "${good}" /sys/devices/system/cpu/intel_uncore_frequency/package_00_die_00/max_freq_khz 4700000

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

# Uncore: only numeric max/initial pairs are judged; ok only on equality; min files
# are optional; TPMI package roots are not reported twice.
UNC=/sys/devices/system/cpu/intel_uncore_frequency
unc() { put "$1" "${UNC}/$2/max_freq_khz" "$3"; put "$1" "${UNC}/$2/initial_max_freq_khz" "$4"; }
r=$(newroot perf-uncmax-bad); cpuinfo "${r}" 6 183 "fpu"
unc "${r}" uncore00 abc 2200000; unc "${r}" uncore01 "" 2200000
perf_says "perf: malformed uncore max is not judged" "${r}" 'uncore0[01]:' negate
r=$(newroot perf-uncmax-above); cpuinfo "${r}" 6 183 "fpu"
unc "${r}" uncore00 2500000 2200000
perf_says "perf: uncore max above initial is not called the hardware ceiling" "${r}" 'ok: +uncore00' negate
perf_says "perf: uncore max above initial is info with both numbers" "${r}" 'info: uncore00: max_freq_khz 2500000 is above initial_max_freq_khz 2200000 \(raised after boot\)'
r=$(newroot perf-uncmax-legacy); cpuinfo "${r}" 6 183 "fpu"
unc "${r}" package_00_die_00 4700000 4700000
perf_says "perf: client package_00_die_00 at its ceiling without min files is ok" "${r}" 'ok: +package_00_die_00: max 4700 MHz, the hardware ceiling'
r=$(newroot perf-uncmax-tpmi); cpuinfo "${r}" 6 173 "fpu"
unc "${r}" uncore00 1800000 2200000; unc "${r}" package_00_die_00 1800000 2200000
perf_says "perf: TPMI cap warns on the cluster" "${r}" 'warn: uncore00: max_freq_khz 1800000 is below the hardware ceiling 2200000'
perf_says "perf: TPMI package root is not reported twice" "${r}" 'package_00_die_00' negate

# TDX host: the note only when VT-x is exposed and kvm_intel can take tdx=1.
KM=/sys/module/kvm_intel/parameters
r=$(newroot perf-tdx-n); cpuinfo "${r}" 19 1 "fpu vmx tdx_host_platform"; kernel "${r}" 6.18.1
put "${r}" "${KM}/tdx" N
perf_says "perf: tdx=N on a TDX CPU gives a pasteable modprobe apply" "${r}" 'apply: +sudo modprobe -r kvm_intel && sudo modprobe kvm_intel tdx=1$'
perf_says "perf: tdx=N note says to stop VMs first" "${r}" 'why: .*stop running VMs and microVM sandboxes first'
perf_says "perf: tdx note has no BIOS step" "${r}" 'BIOS: enable TDX' negate
r=$(newroot perf-tdx-y); cpuinfo "${r}" 19 1 "fpu vmx tdx_host_platform"; kernel "${r}" 6.18.1
put "${r}" "${KM}/tdx" Y
perf_says "perf: tdx=Y is info" "${r}" 'info: kvm_intel tdx=Y'
r=$(newroot perf-tdx-guest); cpuinfo "${r}" 19 1 "fpu tdx_guest"
perf_says "perf: tdx_guest is a TDX guest" "${r}" 'info: this node is a TDX guest'
r=$(newroot perf-tdx-oldkernel); cpuinfo "${r}" 6 143 "fpu vmx tdx_host_platform"; kernel "${r}" 6.12.0
mkdir -p "${r}${KM}"
perf_says "perf: kernel 6.12 gets no modprobe tdx=1" "${r}" 'tdx=1' negate
perf_says "perf: kernel 6.12 is told KVM TDX needs 6.16" "${r}" 'info: .*kernel 6\.12\.0 has no KVM TDX host support \(needs Linux 6\.16\+\)'
r=$(newroot perf-tdx-noparam); cpuinfo "${r}" 19 1 "fpu vmx tdx_host_platform"; kernel "${r}" 6.18.1
mkdir -p "${r}${KM}"
perf_says "perf: 6.16+ without the tdx parameter lacks CONFIG_KVM_INTEL_TDX" "${r}" 'info: .*built without CONFIG_KVM_INTEL_TDX'
perf_says "perf: missing tdx parameter gets no apply" "${r}" 'tdx=1' negate
r=$(newroot perf-tdx-unloaded); cpuinfo "${r}" 19 1 "fpu vmx tdx_host_platform"; kernel "${r}" 6.18.1
perf_says "perf: unloaded kvm_intel is not 'runs without TDX'" "${r}" 'runs without TDX' negate
perf_says "perf: unloaded kvm_intel apply has no modprobe -r" "${r}" 'apply: +sudo modprobe kvm_intel tdx=1$'
r=$(newroot perf-tdx-novmx); cpuinfo "${r}" 19 1 "fpu tdx_host_platform"; kernel "${r}" 6.18.1
put "${r}" "${KM}/tdx" N
perf_says "perf: no vmx means no TDX modprobe advice" "${r}" 'tdx=1' negate
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
# Swap that RAM can hold: the cycle names each active device from /proc/swaps,
# because swapon -a re-enables only fstab entries (zram-generator swap is a
# systemd unit, not in fstab), and the revert turns each one back on.
r=$(newroot perf-swap-zram)
meminfo "${r}" 25165824 8388608 4194304 0 0
printf 'Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/zram0                              partition\t8388604\t\t4194304\t\t100\n/swapfile                               file\t\t1048572\t\t0\t\t-2\n' \
    > "${r}/proc/swaps"
perf_says "perf: swap cycle never uses swapon -a" "${r}" 'swapon -a' negate
perf_says "perf: swap cycle names each device and keeps its priority" "${r}" \
    'apply:  sudo swapoff /dev/zram0 && sudo swapon -p 100 /dev/zram0 && sudo swapoff /swapfile && sudo swapon /swapfile'
perf_says "perf: swap cycle has a revert that re-enables each device" "${r}" \
    'revert: sudo swapon -p 100 /dev/zram0; sudo swapon /swapfile'
r=$(newroot perf-swap-noproc)
meminfo "${r}" 25165824 8388608 4194304 0 0
perf_says "perf: no swap cycle without /proc/swaps" "${r}" 'swapoff' negate
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

# Uncore frequency (intel_uncore_frequency): legacy die dirs on client parts, TPMI
# uncoreNN with ELC on Xeon 6. A lowered ceiling is a warn with apply and exact revert;
# a raised floor is information only (H29 is PENDING, so no pin is ever suggested).
uncore() {  # uncore <root> <dir> <initial_min> <initial_max> <min> <max>
    local d=/sys/devices/system/cpu/intel_uncore_frequency/$2
    put "$1" "${d}/initial_min_freq_khz" "$3"; put "$1" "${d}/initial_max_freq_khz" "$4"
    put "$1" "${d}/min_freq_khz" "$5"; put "$1" "${d}/max_freq_khz" "$6"
}
r=$(newroot perf-uncore-capped)
uncore "${r}" package_00_die_00 800000 4700000 800000 3000000
perf_says "perf: uncore ceiling below the hardware max is flagged" "${r}" 'warn: package_00_die_00: max_freq_khz 3000000 is below the hardware ceiling 4700000'
perf_says "perf: uncore apply restores the hardware ceiling" "${r}" 'apply:  echo 4700000 \| sudo tee /sys/devices/system/cpu/intel_uncore_frequency/package_00_die_00/max_freq_khz$'
perf_says "perf: uncore revert writes the observed ceiling back" "${r}" 'revert: echo 3000000 \| sudo tee /sys/devices/system/cpu/intel_uncore_frequency/package_00_die_00/max_freq_khz$'
before=$(cd "${r}" && find . -type f -exec md5sum {} + | sort)
rm -f "${WORK}/sudo-called"
env K3D_GPU_SYSROOT="${r}" PATH="${STUB_BIN}:${PATH}" bash "${K3D_GPU}" doctor --perf >/dev/null 2>&1
after=$(cd "${r}" && find . -type f -exec md5sum {} + | sort)
if [ "${before}" = "${after}" ] && [ ! -e "${WORK}/sudo-called" ]; then
    PASS=$((PASS + 1)); echo "ok   perf: uncore checks are read-only (tree unchanged, sudo never run)"
else
    FAILS=$((FAILS + 1)); echo "FAIL perf: uncore checks are read-only (tree changed or sudo ran)"
fi
r=$(newroot perf-uncore-floor)
uncore "${r}" package_00_die_00 800000 4700000 4700000 4700000
perf_says "perf: a raised uncore floor is information with its revert" "${r}" 'info: package_00_die_00: floor raised to 4700000 kHz \(hardware floor 800000\).*echo 800000 \| sudo tee'
perf_says "perf: a raised uncore floor is not a suggestion" "${r}" 'warn: package_00_die_00' negate
perf_says "perf: the uncore section never suggests a pin while H29 is unproven" "${r}" 'unproven \(EXPERIMENTS\.md H29, INCONCLUSIVE\)'
r=$(newroot perf-uncore-tpmi)
uncore "${r}" uncore00 800000 2200000 800000 2200000
uncore "${r}" uncore01 800000 2500000 800000 2500000
put "${r}" /sys/devices/system/cpu/intel_uncore_frequency/uncore00/elc_floor_freq_khz 1200000
put "${r}" /sys/devices/system/cpu/intel_uncore_frequency/uncore00/elc_low_threshold_percent 10
put "${r}" /sys/devices/system/cpu/intel_uncore_frequency/uncore00/elc_high_threshold_percent 95
put "${r}" /sys/devices/system/cpu/intel_uncore_frequency/uncore00/elc_high_threshold_enable 1
perf_says "perf: TPMI uncore clusters are each reported" "${r}" 'ok: +uncore01: 800-2500 MHz, the full hardware range'
perf_says "perf: TPMI ELC settings are shown" "${r}" 'info: uncore00: ELC floor 1200000 kHz, thresholds low 10% high 95% \(enable 1\)'
r=$(newroot perf-uncore-none)
perf_says "perf: no intel_uncore_frequency means no uncore section" "${r}" 'Uncore frequency' negate

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
# allocatable query from STUB_ALLOC ("<resource> <count>" lines), the pod-limits
# query from STUB_USED ("<namespace> <resource> <count>" lines) and a label
# selector query with one node when the label is listed in STUB_NODE_LABELS.
ASTUB="${WORK}/abin"
mkdir -p "${ASTUB}"
cat > "${ASTUB}/kubectl" <<'EOF'
#!/bin/sh
[ -z "${STUB_LOG:-}" ] || echo "kubectl $*" >> "${STUB_LOG}"
case "$*" in
    *allocatable*) printf '%s\n' "${STUB_ALLOC:-}" ;;
    "get pods -A "*) printf '%s\n' "${STUB_USED:-}" ;;
    "get resourceslices "*) printf '%s\n' "${STUB_SLICES:-}" ;;
    "get nodes -l "*)
        for l in ${STUB_NODE_LABELS:-}; do [ "$l" = "$4" ] && echo node/k3d-agent-server-0; done ;;
    apply*) echo applied >> "${STUB_APPLY_LOG:-/dev/null}"
        if [ -n "${STUB_LOG:-}" ]; then cat >> "${STUB_LOG}"; else cat >/dev/null; fi ;;
    "-n k3d-gpu-agent get deployment ovms-vlm -o jsonpath="*)
        echo '["--rest_port=8000","--model_name=Qwen3-VL-4B-Instruct-int4-ov"]' ;;
    *" exec deployment/ovms-vlm "*)
        if [ -n "${STUB_VLM_REPLY:-}" ]; then echo "${STUB_VLM_REPLY}"; else echo '{"choices":[{"message":{"content":"Red"}}]}'; fi ;;
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
# xmx=true from an Arc A770, but i915 also holds the UHD 770: auto must not
# request a gpu.intel.com/i915 the plugin may fill with the iGPU.
agent_says "auto skips a driver that is not all-XMX" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 2' STUB_NODE_LABELS='gpu.intel.com/xmx=true k3d-gpu.io/xmx.i915=false'
agent_says "auto still takes an all-XMX xe beside a mixed i915" "${r}" 0 \
    'gpu\.intel\.com/xe: "?1"?' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 2\ngpu.intel.com/xe 1' STUB_NODE_LABELS='gpu.intel.com/xmx=true k3d-gpu.io/xmx.i915=false'
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

# H34: by default a CPU target serving an OpenVINO Qwen3 model drafts on the CPU (the
# measured win); every other target has no draft; off disables it. H22: auto drafts on the
# target's own device; a named device adds its resource to the pod.
agent_says "a CPU target drafts on the CPU by default" "${r}" 0 \
    '--draft_device=CPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1'
agent_says "... with the default draft model" "${r}" 0 \
    '--draft_source_model=OpenVINO/Qwen3-0\.6B-int4-ov' "" STUB_ALLOC='cpu 32'
agent_says "... and requests no GPU for it" "${r}" 0 \
    'gpu\.intel\.com' negate STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1'
agent_says "K3D_GPU_AGENT_DRAFT=off: no draft" "${r}" 0 \
    'draft' negate STUB_ALLOC='cpu 32' K3D_GPU_AGENT_DRAFT=off
agent_says "K3D_GPU_AGENT_DRAFT=OFF: no draft (case-insensitive)" "${r}" 0 \
    'draft' negate STUB_ALLOC='cpu 32' K3D_GPU_AGENT_DRAFT=OFF
agent_says "no default draft for a non-Qwen3 target model" "${r}" 0 \
    'draft' negate STUB_ALLOC='cpu 32' K3D_GPU_AGENT_MODEL=OpenVINO/Phi-4-mini-instruct-int4-ov
agent_says "no default draft with a custom draft model" "${r}" 0 \
    'draft' negate STUB_ALLOC='cpu 32' K3D_GPU_AGENT_DRAFT_MODEL=OpenVINO/Qwen3-1.7B-int4-ov
agent_says "no default draft for a GPU target" "${r}" 0 \
    'draft' negate STUB_ALLOC=$'cpu 32\ngpu.intel.com/xe 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true
agent_says "K3D_GPU_AGENT_DRAFT=auto drafts on the target device (CPU)" "${r}" 0 \
    '--draft_device=CPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' K3D_GPU_AGENT_DRAFT=auto
agent_says "auto draft on a CPU target requests no GPU" "${r}" 0 \
    'gpu\.intel\.com' negate STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' K3D_GPU_AGENT_DRAFT=auto
agent_says "auto draft names the default draft model" "${r}" 0 \
    '--draft_source_model=OpenVINO/Qwen3-0\.6B-int4-ov' "" STUB_ALLOC='cpu 32' K3D_GPU_AGENT_DRAFT=auto
agent_says "auto draft follows a GPU target" "${r}" 0 \
    '--draft_device=GPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/xe 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true K3D_GPU_AGENT_DRAFT=auto
agent_says "K3D_GPU_AGENT_DRAFT=GPU puts the draft on the iGPU, target stays CPU" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' K3D_GPU_AGENT_DRAFT=GPU
agent_says "a GPU draft on a CPU target requests one gpu.intel.com/i915" "${r}" 0 \
    'gpu\.intel\.com/i915: "?1"?' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' K3D_GPU_AGENT_DRAFT=GPU
agent_says "a GPU draft pod joins the host render group" "${r}" 0 \
    "supplementalGroups: \[${gid}\]" "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' K3D_GPU_AGENT_DRAFT=GPU
agent_says "a GPU draft with no allocatable Intel GPU fails" "${r}" fail \
    'no Intel GPU' "" STUB_ALLOC='cpu 32' K3D_GPU_AGENT_DRAFT=GPU
agent_says "a NPU draft with no allocatable NPU fails" "${r}" fail \
    'no Intel NPU' "" STUB_ALLOC='cpu 32' K3D_GPU_AGENT_DRAFT=NPU
agent_says "a NPU draft requests npu.intel.com/accel" "${r}" 0 \
    'npu\.intel\.com/accel: "?1"?' "" STUB_ALLOC=$'cpu 32\nnpu.intel.com/accel 1' K3D_GPU_AGENT_DRAFT=NPU
agent_says "an unknown K3D_GPU_AGENT_DRAFT is rejected" "${r}" fail \
    'unknown K3D_GPU_AGENT_DRAFT' "" STUB_ALLOC='cpu 32' K3D_GPU_AGENT_DRAFT=TPU
agent_says "the draft override is case-insensitive" "${r}" 0 \
    '--draft_device=GPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' K3D_GPU_AGENT_DRAFT=gpu

# GPU target + NPU draft (Core Ultra): the pod holds both devices and joins both groups.
r2=$(newroot agent-gpu-npu)
pci "${r2}" 0000:00:02.0 0x8086 0x7d55 i915; render "${r2}" 0000:00:02.0 128
pci "${r2}" 0000:00:0b.0 0x8086 0x7d1d intel_vpu; accel "${r2}" 0000:00:0b.0 0
rgid=$(stat -c %g "${r2}/dev/dri/renderD128")
ngid=$(id -G | tr ' ' '\n' | grep -vx "${rgid}" | head -1)
if [ -n "${ngid}" ] && chgrp "${ngid}" "${r2}/dev/accel/accel0" 2>/dev/null; then
    agent_says "a GPU target with an NPU draft joins both device groups" "${r2}" 0 \
        "supplementalGroups: \[${rgid}, ${ngid}\]" "" \
        STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1\nnpu.intel.com/accel 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true K3D_GPU_AGENT_DRAFT=NPU
else
    echo "skip a GPU target with an NPU draft joins both device groups (no second group to chgrp to)"
fi
agent_says "a GPU target with an NPU draft limits both devices" "${r2}" 0 \
    'npu\.intel\.com/accel: "?1"?' "" \
    STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1\nnpu.intel.com/accel 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true K3D_GPU_AGENT_DRAFT=NPU
agent_says "a GPU target with an NPU draft keeps its own GPU limit" "${r2}" 0 \
    'gpu\.intel\.com/i915: "?1"?' "" \
    STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1\nnpu.intel.com/accel 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true K3D_GPU_AGENT_DRAFT=NPU

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
# Values spliced into the manifest stay one YAML scalar: a newline or a quote is refused.
nl=$'\n'
agent_says "K3D_GPU_AGENT_LOG_LEVEL with a newline is refused" "${r}" fail 'K3D_GPU_AGENT_LOG_LEVEL must be' "" \
    STUB_ALLOC='cpu 8' "K3D_GPU_AGENT_LOG_LEVEL=INFO${nl}          securityContext:${nl}            privileged: true"
agent_says "K3D_GPU_AGENT_LOG_LEVEL must be a known level" "${r}" fail 'K3D_GPU_AGENT_LOG_LEVEL must be' "" \
    STUB_ALLOC='cpu 8' K3D_GPU_AGENT_LOG_LEVEL=LOUD
agent_says "K3D_GPU_AGENT_MODEL with a newline is refused" "${r}" fail "' is not a valid model id" "" \
    STUB_ALLOC='cpu 8' "K3D_GPU_AGENT_MODEL=OpenVINO/x${nl}          securityContext: {privileged: true}"
agent_says "K3D_GPU_AGENT_MODEL_NAME with a quote is refused" "${r}" fail 'K3D_GPU_AGENT_MODEL_NAME .* is not a valid served name' "" \
    STUB_ALLOC='cpu 8' 'K3D_GPU_AGENT_MODEL_NAME=x"y'
agent_says "K3D_GPU_AGENT_IMAGE with a newline is refused" "${r}" fail "' is not a valid image reference" "" \
    STUB_ALLOC='cpu 8' "K3D_GPU_AGENT_IMAGE=example/ovms:1${nl}          securityContext: {privileged: true}"
agent_says "K3D_GPU_AGENT_REVISION with a space is refused" "${r}" fail 'K3D_GPU_AGENT_REVISION .* is not a valid revision' "" \
    STUB_ALLOC='cpu 8' K3D_GPU_AGENT_MODEL=OpenVINO/Phi-4-mini-instruct-int4-ov 'K3D_GPU_AGENT_REVISION=main x'
agent_says "an image pinned by tag only gets a warning" "${r}" 0 'K3D_GPU_AGENT_IMAGE .* is not pinned by digest' "" \
    STUB_ALLOC='cpu 8' K3D_GPU_AGENT_IMAGE=example/ovms:latest
agent_says "an image pinned by digest gets no warning" "${r}" 0 'not pinned by digest' negate \
    STUB_ALLOC='cpu 8' K3D_GPU_AGENT_IMAGE=example/ovms@sha256:1
agent_says "a custom model leaves parser choice to OVMS" "${r}" 0 'tool_parser' negate \
    STUB_ALLOC='cpu 8' K3D_GPU_AGENT_MODEL=OpenVINO/Phi-4-mini-instruct-int4-ov

# ---- firmware gatekeeper: GuC state from debugfs -------------------------------
# Formats are the kernel's own: i915 intel_uc_fw_dump() prints "GuC firmware:
# <selected>", "GuC firmware wanted: <path>" when it fell back to another file,
# "status: <S>" and "version: found X" or "version: wanted W, found X"; xe
# xe_uc_fw_print() prints "wanted release version W" and "found release version X"
# (drivers/gpu/drm/i915/gt/uc/intel_uc_fw.c, drivers/gpu/drm/xe/xe_uc_fw.c, v7.3).
# gate_root <name> <device id> <driver> <debugfs dir> <guc_info text> [huc_info text]
gate_root() {
    local r
    r=$(newroot "$1")
    kernel "${r}" 6.18.0
    pci "${r}" 0000:00:02.0 0x8086 "$2" "$3"; render "${r}" 0000:00:02.0 128
    mkdir -p "${r}/sys/class/drm/card0" "${r}/sys/kernel/debug/dri/$4/gt0/uc"
    ln -sfn "${r}/sys/devices/pci0000:00/0000:00:02.0" "${r}/sys/class/drm/card0/device"
    printf '%b' "$5" > "${r}/sys/kernel/debug/dri/$4/gt0/uc/guc_info"
    [ -z "${6:-}" ] || printf '%b' "$6" > "${r}/sys/kernel/debug/dri/$4/gt0/uc/huc_info"
    echo "${r}"
}
# doctor_rc <root>: doctor's exit code.
doctor_rc() { env K3D_GPU_SYSROOT="$1" PATH="${STUB_BIN}:${PATH}" bash "${K3D_GPU}" doctor >/dev/null 2>&1; echo $?; }

r=$(gate_root gate-i915-stale 0xa780 i915 0 'GuC firmware: i915/tgl_guc_70.1.1.bin\nGuC firmware wanted: i915/tgl_guc_70.bin\n\tstatus: RUNNING\n\tversion: wanted 70.5.1, found 70.1.1\n\tuCode: 1 bytes\n')
doctor_says "gate: i915 GuC older than the driver wants refuses the GPU" "${r}" \
    '^  .\[31mfail.\[0m: Intel Raptor Lake-S \(0xa780\) at 0000:00:02\.0: GuC i915/tgl_guc_70\.1\.1\.bin 70\.1\.1 is older than the 70\.5\.1 i915 wants; GPU engines refused; fix: install linux-firmware with i915/tgl_guc_70\.bin 70\.5\.1 or newer, then reboot$'
if [ "$(doctor_rc "${r}")" -ne 0 ]; then PASS=$((PASS + 1)); echo "ok   gate: a refused GPU fails doctor"
else FAILS=$((FAILS + 1)); echo "FAIL gate: a refused GPU fails doctor"; fi

r=$(gate_root gate-xe-stale 0xe20b xe 0000:00:02.0 'GuC firmware: xe/bmg_guc_70.bin\n\tstatus: RUNNING\n\twanted release version 70.54.0\n\tfound release version 70.36.0\n\tfound compatibility version 1.20.0\n')
doctor_says "gate: xe GuC release below the wanted release refuses the GPU" "${r}" \
    'fail.\[0m: Intel Battlemage \(0xe20b\) at 0000:00:02\.0: GuC xe/bmg_guc_70\.bin 70\.36\.0 is older than the 70\.54\.0 xe wants; GPU engines refused; fix: install linux-firmware with xe/bmg_guc_70\.bin 70\.54\.0 or newer, then reboot$'

r=$(gate_root gate-missing 0x7d51 i915 0 'GuC firmware: i915/mtl_guc_70.bin\n\tstatus: MISSING\n\tversion: wanted 70.53.0, found 0.0.0\n')
doctor_says "gate: missing GuC firmware refuses the GPU with an install fix" "${r}" \
    'fail.\[0m: .*GuC i915/mtl_guc_70\.bin status MISSING; GPU engines refused; fix: install linux-firmware with i915/mtl_guc_70\.bin, then reboot$'

r=$(gate_root gate-loadfail 0x7d51 i915 0 'GuC firmware: i915/mtl_guc_70.bin\n\tstatus: LOAD FAIL\n\tversion: found 70.53.0\n')
doctor_says "gate: a GuC that failed to load refuses the GPU" "${r}" \
    'fail.\[0m: .*GuC i915/mtl_guc_70\.bin status LOAD FAIL; GPU engines refused; fix: install the current linux-firmware \(i915/mtl_guc_70\.bin\), then reboot; dmesg \| grep -i guc shows the driver.s reason$'

r=$(gate_root gate-current 0xa780 i915 0 'GuC firmware: i915/tgl_guc_70.bin\n\tstatus: RUNNING\n\tversion: found 70.49.4\n')
doctor_says "gate: a running GuC at or above the wanted version passes" "${r}" \
    'ok: Intel Raptor Lake-S \(0xa780\) at 0000:00:02\.0: GuC i915/tgl_guc_70\.bin 70\.49\.4 running'
doctor_says "gate: a running GuC refuses nothing" "${r}" 'refused' negate

r=$(gate_root gate-numeric 0xe20b xe 0 'GuC firmware: xe/bmg_guc_70.bin\n\tstatus: RUNNING\n\twanted release version 70.9.0\n\tfound release version 70.10.0\n\tfound compatibility version 1.2.0\n')
doctor_says "gate: versions compare numerically (70.10.0 is not older than 70.9.0)" "${r}" 'refused' negate
r=$(gate_root gate-patch 0xe20b xe 0 'GuC firmware: xe/bmg_guc_70.bin\n\tstatus: RUNNING\n\twanted release version 70.54.1\n\tfound release version 70.54.0\n')
doctor_says "gate: a GuC one patch release older than wanted refuses the GPU" "${r}" \
    'GuC xe/bmg_guc_70\.bin 70\.54\.0 is older than the 70\.54\.1 xe wants; GPU engines refused'
r=$(gate_root gate-newer-major 0xe20b xe 0 'GuC firmware: xe/bmg_guc_70.bin\n\tstatus: RUNNING\n\twanted release version 70.54.1\n\tfound release version 71.0.0\n')
doctor_says "gate: a newer major GuC release is not refused" "${r}" 'refused' negate

r=$(gate_root gate-adl-s 0x4680 i915 0 'GuC firmware: i915/tgl_guc_70.1.1.bin\nGuC firmware wanted: i915/tgl_guc_70.bin\n\tstatus: RUNNING\n\tversion: wanted 70.5.1, found 70.1.1\n')
doctor_says "gate: a stale GuC on Alder Lake-S (execlists) warns with the fix" "${r}" \
    'warn.\[0m: Intel Alder Lake-S \(0x4680\) at 0000:00:02\.0: GuC i915/tgl_guc_70\.1\.1\.bin 70\.1\.1 is older than the 70\.5\.1 i915 wants \(i915 submits through execlists on Alder Lake-S, so engines are not refused\); fix: install linux-firmware with i915/tgl_guc_70\.bin 70\.5\.1 or newer, then reboot$'
doctor_says "gate: a stale GuC on Alder Lake-S refuses nothing" "${r}" 'GPU engines refused' negate

r=$(gate_root gate-disabled 0x9a49 i915 0 'GuC firmware: i915/tgl_guc_70.1.1.bin\n\tstatus: DISABLED\n\tversion: found 0.0.0\n')
doctor_says "gate: a GuC the driver does not use (DISABLED) refuses nothing" "${r}" 'refused' negate

r=$(gate_root gate-huc 0x7d51 i915 0 'GuC firmware: i915/mtl_guc_70.bin\n\tstatus: RUNNING\n\tversion: found 70.53.0\n' 'HuC firmware: i915/mtl_huc_gsc.bin\n\tstatus: ERROR\n\tversion: found 8.5.4\n')
doctor_says "gate: a failed HuC warns with a fix" "${r}" \
    'warn.\[0m: Intel Arrow Lake-H \(0x7d51\) at 0000:00:02\.0: HuC i915/mtl_huc_gsc\.bin status ERROR \(media decode/encode only; inference is not affected\); fix: install the current linux-firmware \(i915/mtl_huc_gsc\.bin\), then reboot'
doctor_says "gate: a failed HuC refuses no engine" "${r}" 'refused' negate

r=$(newroot gate-noroot)
kernel "${r}" 6.18.0
pci "${r}" 0000:00:02.0 0x8086 0xa780 i915; render "${r}" 0000:00:02.0 128
mkdir -p "${r}/sys/class/drm/card0"; ln -sfn "${r}/sys/devices/pci0000:00/0000:00:02.0" "${r}/sys/class/drm/card0/device"
doctor_says "gate: unreadable debugfs says the GuC was not checked" "${r}" \
    'info: Intel Raptor Lake-S \(0xa780\) at 0000:00:02\.0: GuC version not checked: debugfs needs root \(sudo k3d-gpu doctor\)'
doctor_says "gate: unreadable debugfs refuses nothing" "${r}" 'refused' negate

# agent: the gatekeeper's refusal beats the XMX auto rule, and a forced GPU stops.
r=$(gate_root agent-gate 0xe20b xe 0 'GuC firmware: xe/bmg_guc_70.bin\n\tstatus: RUNNING\n\twanted release version 70.54.0\n\tfound release version 70.36.0\n')
agent_says "gate: auto falls back to OVMS on the CPU when the Intel GPU is refused" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/xe 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true
agent_says "gate: auto names the refusal and its fix" "${r}" 0 \
    'Intel GPU 0000:00:02\.0 refused: GuC xe/bmg_guc_70\.bin 70\.36\.0 is older than the 70\.54\.0 xe wants; fix: install linux-firmware with xe/bmg_guc_70\.bin 70\.54\.0 or newer, then reboot; using OVMS on the CPU' \
    "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/xe 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true
agent_says "gate: the CPU fallback requests no GPU" "${r}" 0 \
    'gpu\.intel\.com/xe' negate STUB_ALLOC=$'cpu 32\ngpu.intel.com/xe 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true
agent_says "gate: K3D_GPU_AGENT_DEVICE=GPU on a refused GPU fails with the fix" "${r}" fail \
    'K3D_GPU_AGENT_DEVICE=GPU but Intel GPU 0000:00:02\.0 is refused: .*fix: install linux-firmware' "" \
    STUB_ALLOC=$'cpu 32\ngpu.intel.com/xe 1' K3D_GPU_AGENT_DEVICE=GPU
agent_says "gate: a vLLM backend on a refused GPU fails with the fix, not a CPU fallback" "${r}" fail \
    'K3D_GPU_AGENT_BACKEND=vllm needs an Intel GPU but Intel GPU 0000:00:02\.0 is refused: .*fix: install linux-firmware' "" \
    STUB_ALLOC=$'cpu 32\ngpu.intel.com/xe 1' STUB_NODE_LABELS=gpu.intel.com/xmx=true K3D_GPU_AGENT_BACKEND=vllm

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
[ -z "${STUB_KUBECTL_LOG:-}" ] || echo "$*" >> "${STUB_KUBECTL_LOG}"
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
    out=$(env K3D_GPU_SYSROOT="${root}" PATH="${RSTUB}:${PATH}" "$@" bash -c 'exec bash "$1" report --json ${REPORT_ARGS:-}' _ "${K3D_GPU}" 2>"${WORK}/report.err")
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
report_says "report: UHD 770 (Raptor Lake-S) has no XMX" "${r}" \
    'r["host"]["intel_gpus"][0]["platform"] == "Raptor Lake-S" and r["host"]["intel_gpus"][0]["xmx"] is False'
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
# --no-cluster (used by `share`) must not run kubectl at all, even with a context.
report_says "report: --no-cluster leaves kubectl alone" "${r}" \
    '(c := r["cluster"]) and c["present"] is False and c["reason"] == "not queried (--no-cluster)"' \
    K3D_GPU_CLUSTER=rpt STUB_CONTEXTS="k3d-rpt" STUB_NODES='N\tk3d-rpt-server-0\n' REPORT_ARGS=--no-cluster STUB_KUBECTL_LOG="${WORK}/report-kubectl.log"
if [ -s "${WORK}/report-kubectl.log" ]; then
    FAILS=$((FAILS + 1)); echo "FAIL report: --no-cluster ran kubectl"; sed 's/^/  | /' "${WORK}/report-kubectl.log"
else
    PASS=$((PASS + 1)); echo "ok   report: --no-cluster ran no kubectl command"
fi
says_rc() {
    local name=$1 want=$2 re=$3 out rc
    shift 3
    out=$("$@" 2>&1)
    rc=$?
    if [ "${rc}" -eq "${want}" ] && grep -E -- "${re}" <<< "${out}" >/dev/null; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name} (rc=${rc}, want ${want}, /${re}/)"; sed 's/^/  | /' <<< "${out}"
    fi
}
says_rc "report: an unknown option is refused" 1 'unknown option --bogus' \
    env K3D_GPU_SYSROOT="${r}" PATH="${RSTUB}:${PATH}" bash "${K3D_GPU}" report --json --bogus
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
report_says "report: Lunar Lake iGPU has XMX" "${r}" 'r["host"]["intel_gpus"][0]["xmx"] is True'
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
# K3D_GPU_VOLUMES: host directories (a model cache) mounted into every node, read-only unless
# :rw is given, so pods can read them by hostPath without a copy into the node.
mkdir -p "${WORK}/vol/models" "${WORK}/vol/cache"
up_says "up without K3D_GPU_VOLUMES mounts no extra dir" 0 "${WORK}/vol" negate
up_says "K3D_GPU_VOLUMES mounts a host dir read-only in every node" 0 \
    "^--volume=${WORK}/vol/models:/var/models:ro@server:\*;agent:\*$" "" \
    K3D_GPU_VOLUMES="${WORK}/vol/models:/var/models"
up_says "K3D_GPU_VOLUMES takes a list and an explicit rw" 0 \
    "^--volume=${WORK}/vol/cache:/var/cache-x:rw@server:\*;agent:\*$" "" \
    K3D_GPU_VOLUMES="${WORK}/vol/models:/var/models,${WORK}/vol/cache:/var/cache-x:rw"
up_says "K3D_GPU_VOLUMES rejects a relative host path" fail 'vol' negate \
    K3D_GPU_VOLUMES="vol/models:/var/models"
up_says "K3D_GPU_VOLUMES rejects a missing host dir" fail 'nope' negate \
    K3D_GPU_VOLUMES="${WORK}/vol/nope:/var/models"
up_says "K3D_GPU_VOLUMES rejects a mode other than ro or rw" fail 'vol' negate \
    K3D_GPU_VOLUMES="${WORK}/vol/models:/var/models:z"

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

# ---- agent on Gaudi: the device selector picks the HPU, served by vLLM --------
r=$(newroot agent-gaudi)
HPU_ALLOC=$'cpu 160\nhabana.ai/gaudi 8'
agent_says "auto serves with vLLM when the cluster advertises habana.ai/gaudi" "${r}" 0 \
    'command: \[vllm, serve\]' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "agent Gaudi pod requests one habana.ai/gaudi" "${r}" 0 \
    '^ +habana\.ai/gaudi: 1$' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "the Gaudi pod is annotated with device HPU" "${r}" 0 \
    'k3d-gpu\.io/agent-device: HPU$' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "Gaudi beats an allocatable XMX Intel GPU" "${r}" 0 \
    '^ +habana\.ai/gaudi: 1$' "" STUB_ALLOC=$'cpu 160\ngpu.intel.com/xe 1\nhabana.ai/gaudi 8' \
    STUB_NODE_LABELS=gpu.intel.com/xmx=true
agent_says "the Gaudi path renders no OVMS and no Arc flags" "${r}" 0 \
    'target_device|model_server|--block-size|--enforce-eager|ZE_|CCL_|SYCL_' negate STUB_ALLOC="${HPU_ALLOC}"
agent_says "zero allocatable Gaudi falls back to OVMS on CPU" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC=$'cpu 160\nhabana.ai/gaudi 0'
agent_says "K3D_GPU_AGENT_DEVICE=CPU overrides an available Gaudi" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC="${HPU_ALLOC}" K3D_GPU_AGENT_DEVICE=CPU
agent_says "K3D_GPU_AGENT_DEVICE=GPU overrides an available Gaudi" "${r}" 0 \
    '--target_device=GPU' "" STUB_ALLOC=$'cpu 160\ngpu.intel.com/xe 1\nhabana.ai/gaudi 8' K3D_GPU_AGENT_DEVICE=GPU
agent_says "K3D_GPU_AGENT_DEVICE=gaudi is accepted as HPU" "${r}" 0 \
    'command: \[vllm, serve\]' "" STUB_ALLOC="${HPU_ALLOC}" K3D_GPU_AGENT_DEVICE=gaudi
agent_says "K3D_GPU_AGENT_DEVICE=HPU with no allocatable Gaudi fails" "${r}" fail \
    'no Gaudi' "" STUB_ALLOC='cpu 160' K3D_GPU_AGENT_DEVICE=HPU
agent_says "an explicit ovms backend never picks the Gaudi" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC="${HPU_ALLOC}" K3D_GPU_AGENT_BACKEND=ovms
agent_says "an explicit ovms backend refuses K3D_GPU_AGENT_DEVICE=HPU" "${r}" fail \
    'does not run on a Gaudi' "" STUB_ALLOC="${HPU_ALLOC}" K3D_GPU_AGENT_BACKEND=ovms K3D_GPU_AGENT_DEVICE=HPU
agent_says "llm-scaler refuses K3D_GPU_AGENT_DEVICE=HPU" "${r}" fail \
    'does not run on a Gaudi' "" STUB_ALLOC="${HPU_ALLOC}" K3D_GPU_AGENT_BACKEND=llm-scaler K3D_GPU_AGENT_DEVICE=HPU
agent_says "llm-scaler auto skips the Gaudi for the Intel GPU" "${r}" 0 \
    'gpu\.intel\.com/xe: "?1"?$' "" STUB_ALLOC=$'cpu 160\ngpu.intel.com/xe 1\nhabana.ai/gaudi 8' K3D_GPU_AGENT_BACKEND=llm-scaler
agent_says "an explicit vllm backend runs on the Gaudi" "${r}" 0 \
    '^ +habana\.ai/gaudi: 1$' "" STUB_ALLOC="${HPU_ALLOC}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "the vLLM image is Intel's vllm-gaudi 0.30.0 build pinned by digest" "${r}" 0 \
    'image: vault\.habana\.ai/gaudi-docker/1\.24\.2/ubuntu24\.04/habanalabs/vllm-0\.30\.0-ptupstream-2\.13\.0@sha256:[0-9a-f]{64}$' "" \
    STUB_ALLOC="${HPU_ALLOC}"
agent_says "K3D_GPU_AGENT_IMAGE overrides the Gaudi image" "${r}" 0 \
    'image: example/vllm-gaudi:dev$' "" STUB_ALLOC="${HPU_ALLOC}" K3D_GPU_AGENT_IMAGE=example/vllm-gaudi:dev
agent_says "HPU runs in bfloat16" "${r}" 0 '--dtype=bfloat16$' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "HPU default model is Qwen3-4B at a pinned revision" "${r}" 0 \
    '--revision=1cfa9a7208912126459214e8b04321603b3df60c$' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "HPU default model is served as Qwen3-4B" "${r}" 0 \
    '--served-model-name=Qwen3-4B$' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "HPU default model gets vLLM's hermes tool parser" "${r}" 0 \
    '--tool-call-parser=hermes$' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "HPU tool calling is switched on" "${r}" 0 \
    '--enable-auto-tool-choice$' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "HPU default model gets the qwen3 reasoning parser" "${r}" 0 \
    '--reasoning-parser=qwen3$' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "a custom HPU model gets no parsers and no pinned revision" "${r}" 0 \
    'parser|revision|auto-tool' negate STUB_ALLOC="${HPU_ALLOC}" K3D_GPU_AGENT_MODEL=meta-llama/Llama-3.1-8B-Instruct
agent_says "K3D_GPU_AGENT_REVISION pins a custom HPU model" "${r}" 0 \
    '--revision=abc123$' "" STUB_ALLOC="${HPU_ALLOC}" K3D_GPU_AGENT_MODEL=meta-llama/Llama-3.1-8B-Instruct K3D_GPU_AGENT_REVISION=abc123
agent_says "vLLM on HPU runs eager + torch.compile (PT_HPU_LAZY_MODE=0)" "${r}" 0 \
    'name: PT_HPU_LAZY_MODE' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "HABANA_VISIBLE_DEVICES is left to the device plugin" "${r}" 0 \
    'name: HABANA_VISIBLE_DEVICES' negate STUB_ALLOC="${HPU_ALLOC}"
agent_says "vLLM on HPU gets SYS_NICE" "${r}" 0 'add: \[SYS_NICE\]$' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "vLLM on HPU gets an 8Gi memory-backed /dev/shm" "${r}" 0 \
    'sizeLimit: 8Gi$' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "vLLM on HPU requests 16Gi of memory" "${r}" 0 'memory: 16Gi$' "" STUB_ALLOC="${HPU_ALLOC}"
agent_says "K3D_GPU_AGENT_MAX_LEN reaches vLLM on HPU" "${r}" 0 \
    '--max-model-len=32768$' "" STUB_ALLOC="${HPU_ALLOC}" K3D_GPU_AGENT_MAX_LEN=32768
agent_says "K3D_GPU_AGENT_GPUS shards across Gaudis" "${r}" 0 \
    '^ +habana\.ai/gaudi: 2$' "" STUB_ALLOC="${HPU_ALLOC}" K3D_GPU_AGENT_GPUS=2
agent_says "K3D_GPU_AGENT_GPUS above the allocatable Gaudis fails" "${r}" fail \
    'K3D_GPU_AGENT_GPUS=9 but the cluster advertises 8 habana\.ai/gaudi' "" STUB_ALLOC="${HPU_ALLOC}" K3D_GPU_AGENT_GPUS=9
agent_says "the Arc manifest keeps its own image, env and 2Gi /dev/shm" "${r}" 0 \
    'sizeLimit: 2Gi$' "" STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "the Arc manifest gets no Gaudi settings" "${r}" 0 \
    'PT_HPU|SYS_NICE|command: \[vllm' negate STUB_ALLOC="${XE1}" K3D_GPU_AGENT_BACKEND=vllm

# `agent up` on a Gaudi: the OVMS server is stopped first, the API is vLLM's /v1.
: > "${log}"
out=$(env K3D_GPU_SYSROOT="${r}" PATH="${ASTUB}:${PATH}" STUB_ALLOC="${HPU_ALLOC}" STUB_LOG="${log}" STUB_EXEC="${WARM}" \
    bash "${K3D_GPU}" agent up 2>&1)
rc=$?
if [ "${rc}" -eq 0 ]; then PASS=$((PASS + 1)); echo "ok   agent up on a Gaudi succeeds against a stub cluster"
else FAILS=$((FAILS + 1)); echo "FAIL agent up on a Gaudi (rc=${rc})"; awk '{print "  | " $0}' <<< "${out}"; fi
agent_log_has "agent up on a Gaudi applies the vllm Deployment on HPU" 'k3d-gpu\.io/agent-device: HPU$'
agent_log_before "agent up on a Gaudi stops the OVMS server before applying" \
    'delete deployment/ovms service/ovms' '^kubectl apply -f -$'
agent_log_has "agent up on a Gaudi waits for deployment/vllm" 'rollout status deployment/vllm'
agent_log_has "agent up on a Gaudi warms up through /v1/chat/completions" \
    'create --raw /api/v1/namespaces/k3d-gpu-agent/services/vllm:http/proxy/v1/chat/completions'

# ---- agent add-ons: device plan around the LLM and add-on manifests ----------
# `agent manifest --with ...` keeps the LLM on agent_select's device and plans
# the add-ons against the units it and pods in other namespaces leave free.
# deploy_doc prints one Deployment of a rendered manifest.
deploy_doc() {
    awk -v want="$1" '
        function flush() { if (kind == "Deployment" && name == want) printf "%s", buf; buf = ""; kind = ""; name = "" }
        /^---$/ { flush(); next }
        { buf = buf $0 "\n" }
        /^kind: / { kind = $2 }
        /^  name: / && name == "" { name = $2 }
        END { flush() }'
}

# addon_says <name> <root> <want rc: 0|fail> <deployment|-> <ERE> [negate] [env...]: with the
# agent arguments in WITH, the named Deployment (or, for -, all output incl. stderr) must match.
addon_says() {
    local name=$1 root=$2 want=$3 doc=$4 re=$5 negate=${6:-} out rc hit=0
    shift 6 2>/dev/null || shift $#
    # shellcheck disable=SC2086 # WITH is a word list
    out=$(env K3D_GPU_SYSROOT="${root}" PATH="${ASTUB}:${PATH}" "$@" bash "${K3D_GPU}" agent manifest ${WITH} 2>&1)
    rc=$?
    [ "${doc}" = - ] || out=$(deploy_doc "${doc}" <<< "${out}")
    grep -E -- "${re}" <<< "${out}" >/dev/null && hit=1
    if { { [ -z "${negate}" ] && [ "${hit}" -eq 1 ]; } || { [ -n "${negate}" ] && [ "${hit}" -eq 0 ]; }; } \
       && { { [ "${want}" = 0 ] && [ "${rc}" -eq 0 ]; } || { [ "${want}" = fail ] && [ "${rc}" -ne 0 ]; }; }; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (rc=${rc}, want ${want}, ${doc}: ${negate:+not }/${re}/)"
        awk '{print "  | " $0}' <<< "${out}"
    fi
}

r=$(newroot addon-igpu-npu)
pci "${r}" 0000:00:02.0 0x8086 0xa780 i915; render "${r}" 0000:00:02.0 128
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d intel_vpu; accel "${r}" 0000:00:0b.0 0
rgid=$(stat -c %g "${r}/dev/dri/renderD128")
agid=$(stat -c %g "${r}/dev/accel/accel0")
SHARED=$'cpu 32\ngpu.intel.com/i915 3\nnpu.intel.com/accel 1'
ONE=$'cpu 32\ngpu.intel.com/i915 1\nnpu.intel.com/accel 1'
XMX=gpu.intel.com/xmx=true
WITH="--with vlm,stt,video"
addon_says "with GPU units for all, video gets the iGPU" "${r}" 0 video-analytics \
    'gpu\.intel\.com/i915: "?1"?' "" STUB_ALLOC="${SHARED}"
addon_says "with GPU units for all, vlm gets the iGPU" "${r}" 0 ovms-vlm \
    '--target_device=GPU$' "" STUB_ALLOC="${SHARED}"
addon_says "with GPU units for all, an XMX LLM keeps the iGPU" "${r}" 0 ovms \
    '--target_device=GPU$' "" STUB_ALLOC="${SHARED}" STUB_NODE_LABELS="${XMX}"
addon_says "add-ons do not change the LLM's auto rule: no XMX, CPU" "${r}" 0 ovms \
    '--target_device=CPU$' "" STUB_ALLOC="${SHARED}"
addon_says "stt goes to the NPU first" "${r}" 0 ovms-stt \
    'npu\.intel\.com/accel: "?1"?' "" STUB_ALLOC="${SHARED}"
addon_says "stt on the NPU runs Whisper there" "${r}" 0 ovms-stt \
    '--target_device=NPU$' "" STUB_ALLOC="${SHARED}"
addon_says "stt NPU pod joins the accel node's group" "${r}" 0 ovms-stt \
    "supplementalGroups: \[${agid}\]" "" STUB_ALLOC="${SHARED}"
addon_says "video GPU pod joins the render group" "${r}" 0 video-analytics \
    "supplementalGroups: \[${rgid}\]" "" STUB_ALLOC="${SHARED}"
addon_says "add-ons run the OVMS image" "${r}" 0 ovms-stt \
    'image: openvino/model_server@sha256:[0-9a-f]{64}' "" STUB_ALLOC="${SHARED}"
addon_says "one unshared iGPU, no XMX: the LLM is on CPU and video gets the iGPU" "${r}" 0 video-analytics \
    'k3d-gpu.io/agent-device: GPU' "" STUB_ALLOC="${ONE}"
addon_says "one unshared iGPU, no XMX: vlm falls back to CPU" "${r}" 0 ovms-vlm \
    '--target_device=CPU$' "" STUB_ALLOC="${ONE}"
addon_says "one unshared iGPU, XMX: the LLM keeps it" "${r}" 0 ovms \
    '--target_device=GPU$' "" STUB_ALLOC="${ONE}" STUB_NODE_LABELS="${XMX}"
addon_says "one unshared iGPU, XMX: video decodes on CPU" "${r}" 0 video-analytics \
    'k3d-gpu.io/agent-device: CPU' "" STUB_ALLOC="${ONE}" STUB_NODE_LABELS="${XMX}"
addon_says "one unshared iGPU: CPU fallbacks request no GPU" "${r}" 0 ovms-vlm \
    'gpu\.intel\.com' negate STUB_ALLOC="${ONE}"
addon_says "a GPU fallback to CPU is reported" "${r}" 0 - \
    'vlm: no Intel GPU unit is left' "" STUB_ALLOC="${ONE}"
addon_says "K3D_GPU_AGENT_DEVICE=GPU keeps the iGPU for the LLM" "${r}" 0 ovms \
    '--target_device=GPU$' "" STUB_ALLOC="${ONE}" K3D_GPU_AGENT_DEVICE=GPU
addon_says "... and video then falls back to CPU" "${r}" 0 video-analytics \
    'k3d-gpu.io/agent-device: CPU' "" STUB_ALLOC="${ONE}" K3D_GPU_AGENT_DEVICE=GPU
addon_says "an add-on's explicit request wins the GPU over add-on priority" "${r}" 0 ovms-vlm \
    '--target_device=GPU$' "" STUB_ALLOC="${ONE}" K3D_GPU_AGENT_VLM_DEVICE=gpu
addon_says "... and video then decodes on CPU" "${r}" 0 video-analytics \
    'k3d-gpu.io/agent-device: CPU' "" STUB_ALLOC="${ONE}" K3D_GPU_AGENT_VLM_DEVICE=GPU
addon_says "an add-on's explicit GPU request fails when the LLM holds the only one" "${r}" fail - \
    'no free Intel GPU' "" STUB_ALLOC="${ONE}" STUB_NODE_LABELS="${XMX}" K3D_GPU_AGENT_VLM_DEVICE=GPU
addon_says "a GPU held by a pod elsewhere is not free" "${r}" 0 video-analytics \
    'k3d-gpu.io/agent-device: CPU' "" STUB_ALLOC="${ONE}" STUB_USED='default gpu.intel.com/i915 1'
addon_says "the agent's own pods do not hold units against the plan" "${r}" 0 video-analytics \
    'k3d-gpu.io/agent-device: GPU' "" STUB_ALLOC="${ONE}" STUB_USED='k3d-gpu-agent gpu.intel.com/i915 1'
addon_says "an NPU held elsewhere sends stt to a GPU" "${r}" 0 ovms-stt \
    '--target_device=GPU$' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 4\nnpu.intel.com/accel 1' STUB_USED='other npu.intel.com/accel 1'
addon_says "an LLM on the NPU leaves stt the GPU" "${r}" 0 ovms-stt \
    '--target_device=GPU$' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_DEVICE=NPU
addon_says "explicit GPU with none free fails" "${r}" fail - \
    'no free Intel GPU' "" STUB_ALLOC="${ONE}" K3D_GPU_AGENT_VLM_DEVICE=GPU K3D_GPU_AGENT_VIDEO_DEVICE=GPU
addon_says "video on the NPU is rejected" "${r}" fail - \
    'media engines' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VIDEO_DEVICE=NPU
addon_says "an unknown add-on device is rejected" "${r}" fail - \
    "unknown K3D_GPU_AGENT_STT_DEVICE 'DSP'" "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_STT_DEVICE=DSP
addon_says "the LLM's own override still applies" "${r}" 0 ovms \
    '--target_device=CPU$' "" STUB_ALLOC="${SHARED}" STUB_NODE_LABELS="${XMX}" K3D_GPU_AGENT_DEVICE=CPU

# Fast Draft on a second device holds that unit too: the add-ons plan around it,
# and a draft device held elsewhere is not free.
WITH="--with vlm"
addon_says "a GPU draft holds the only iGPU: vlm falls back to CPU" "${r}" 0 ovms-vlm \
    '--target_device=CPU$' "" STUB_ALLOC="${ONE}" K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_DRAFT=GPU
addon_says "... and the draft keeps its GPU limit" "${r}" 0 ovms \
    'gpu\.intel\.com/i915: "?1"?' "" STUB_ALLOC="${ONE}" K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_DRAFT=GPU
addon_says "a GPU draft plus an explicit GPU vlm on one unit fails" "${r}" fail - \
    'no free Intel GPU' "" STUB_ALLOC="${ONE}" K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_DRAFT=GPU K3D_GPU_AGENT_VLM_DEVICE=GPU
WITH="--with stt"
addon_says "an NPU draft holds the NPU: stt goes to the GPU" "${r}" 0 ovms-stt \
    '--target_device=GPU$' "" STUB_ALLOC="${ONE}" K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_DRAFT=NPU
WITH=""
addon_says "a GPU draft with the GPU held elsewhere fails" "${r}" fail - \
    'K3D_GPU_AGENT_DRAFT=GPU.*no free' "" STUB_ALLOC="${ONE}" STUB_USED='default gpu.intel.com/i915 1' K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_DRAFT=GPU
addon_says "an NPU draft with the NPU held elsewhere fails" "${r}" fail - \
    'K3D_GPU_AGENT_DRAFT=NPU.*no free' "" STUB_ALLOC="${ONE}" STUB_USED='other npu.intel.com/accel 1' K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_DRAFT=NPU
n=$(env K3D_GPU_SYSROOT="${r}" PATH="${ASTUB}:${PATH}" STUB_ALLOC="${ONE}" K3D_GPU_AGENT_DEVICE=HETERO:GPU,CPU K3D_GPU_AGENT_DRAFT=GPU \
    bash "${K3D_GPU}" agent manifest 2>/dev/null | deploy_doc ovms | grep -cE 'gpu\.intel\.com/i915: "?1"?')
if [ "${n}" = 1 ]; then PASS=$((PASS + 1)); echo "ok   a GPU draft on a HETERO target shares its GPU (one limit)"
else FAILS=$((FAILS + 1)); echo "FAIL a GPU draft on a HETERO target shares its GPU (one limit): ${n} limits"; fi

# DRA mode (K3D_GPU_DRA=1): the GPU is a ResourceSlice device, not gpu.intel.com/xe|i915.
# The agent stack requests the extended resources only, so it says so instead of
# serving on the CPU silently or blaming the cluster.
DSL="gpu.intel.com node0 1 xe"
agent_says "DRA GPU: auto serves on CPU and says why" "${r}" 0 \
    'Intel GPU through DRA' "" STUB_ALLOC='cpu 32' STUB_SLICES="${DSL}" STUB_NODE_LABELS="${XMX}"
agent_says "DRA GPU: auto still renders a CPU target" "${r}" 0 \
    '--target_device=CPU' "" STUB_ALLOC='cpu 32' STUB_SLICES="${DSL}" STUB_NODE_LABELS="${XMX}"
agent_says "no GPU at all: auto prints no DRA note" "${r}" 0 \
    'DRA' negate STUB_ALLOC='cpu 32'
agent_says "DRA GPU: a forced GPU names DRA" "${r}" fail \
    'Intel GPU through DRA.*K3D_GPU_DRA=0' "" STUB_ALLOC='cpu 32' STUB_SLICES="${DSL}" K3D_GPU_AGENT_DEVICE=GPU
agent_says "DRA GPU: HETERO names DRA" "${r}" fail \
    'Intel GPU through DRA' "" STUB_ALLOC='cpu 32' STUB_SLICES="${DSL}" K3D_GPU_AGENT_DEVICE=HETERO:GPU,CPU
agent_says "DRA GPU: the vllm backend names DRA" "${r}" fail \
    'Intel GPU through DRA' "" STUB_ALLOC='cpu 32' STUB_SLICES="${DSL}" K3D_GPU_AGENT_BACKEND=vllm
agent_says "DRA GPU: a GPU draft names DRA" "${r}" fail \
    'Intel GPU through DRA' "" STUB_ALLOC='cpu 32' STUB_SLICES="${DSL}" K3D_GPU_AGENT_DRAFT=GPU

# A vLLM LLM holds K3D_GPU_AGENT_GPUS units; the add-ons stay on OVMS.
XE3=$'cpu 32\ngpu.intel.com/xe 3\nnpu.intel.com/accel 1'
WITH="--with vlm,video"
addon_says "vllm LLM with add-ons renders the vllm Deployment" "${r}" 0 vllm \
    'gpu\.intel\.com/xe: "?2"?' "" STUB_ALLOC="${XE3}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_GPUS=2
addon_says "vllm on 2 of 3 GPU units leaves video the third" "${r}" 0 video-analytics \
    'k3d-gpu.io/agent-device: GPU' "" STUB_ALLOC="${XE3}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_GPUS=2
addon_says "... and vlm none" "${r}" 0 ovms-vlm \
    '--target_device=CPU$' "" STUB_ALLOC="${XE3}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_GPUS=2
addon_says "an add-on next to vllm runs the OVMS image, not vLLM's" "${r}" 0 ovms-vlm \
    'image: openvino/model_server@sha256:[0-9a-f]{64}' "" STUB_ALLOC="${XE3}" K3D_GPU_AGENT_BACKEND=vllm K3D_GPU_AGENT_GPUS=2
addon_says "a Gaudi LLM leaves the Intel GPU to video" "${r}" 0 video-analytics \
    'k3d-gpu.io/agent-device: GPU' "" STUB_ALLOC=$'cpu 32\nhabana.ai/gaudi 1\ngpu.intel.com/i915 1'

WITH="--with stt"
addon_says "stt alone, no NPU, one XMX iGPU: the LLM keeps the GPU" "${r}" 0 ovms \
    '--target_device=GPU$' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' STUB_NODE_LABELS="${XMX}"
addon_says "stt alone, no NPU, one XMX iGPU: stt runs on CPU" "${r}" 0 ovms-stt \
    '--target_device=CPU$' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' STUB_NODE_LABELS="${XMX}"
addon_says "stt alone, no NPU, two GPU units: stt gets one" "${r}" 0 ovms-stt \
    '--target_device=GPU$' "" STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 2' STUB_NODE_LABELS="${XMX}"
addon_says "stt pulls Whisper as a speech2text task" "${r}" 0 ovms-stt \
    '--task=speech2text$' "" STUB_ALLOC="${SHARED}"
addon_says "default Whisper is large-v3-turbo" "${r}" 0 ovms-stt \
    '--source_model=OpenVINO/whisper-large-v3-turbo-fp16-ov$' "" STUB_ALLOC="${SHARED}"
addon_says "K3D_GPU_AGENT_STT_MODEL picks another Whisper" "${r}" 0 ovms-stt \
    '--model_name=whisper-base-int8-ov$' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_STT_MODEL=OpenVINO/whisper-base-int8-ov
addon_says "add-ons not asked for are not rendered" "${r}" 0 - \
    'name: (ovms-vlm|video-analytics)$' negate STUB_ALLOC="${SHARED}"

WITH="--with=VLM"
addon_says "add-on names are case-insensitive" "${r}" 0 ovms-vlm \
    '--task=text_generation$' "" STUB_ALLOC="${SHARED}"
addon_says "default VLM is Qwen3-VL-4B int4" "${r}" 0 ovms-vlm \
    '--source_model=OpenVINO/Qwen3-VL-4B-Instruct-int4-ov$' "" STUB_ALLOC="${SHARED}"
addon_says "VLM on the NPU uses the fp16 Phi-3.5-vision IR" "${r}" 0 ovms-vlm \
    '--source_model=OpenVINO/Phi-3.5-vision-instruct-fp16-ov$' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VLM_DEVICE=NPU
addon_says "K3D_GPU_AGENT_VLM_MODEL overrides the VLM" "${r}" 0 ovms-vlm \
    '--model_name=Qwen3-VL-8B-Instruct-int4-ov$' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VLM_MODEL=OpenVINO/Qwen3-VL-8B-Instruct-int4-ov
addon_says "image URLs are refused unless domains are allowed" "${r}" 0 ovms-vlm \
    'allowed_media_domains' negate STUB_ALLOC="${SHARED}"
addon_says "K3D_GPU_AGENT_VLM_MEDIA_DOMAINS allows image URLs" "${r}" 0 ovms-vlm \
    '--allowed_media_domains=example.com$' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VLM_MEDIA_DOMAINS=example.com
addon_says "VLM on the NPU lifts the 1024-token prompt limit images count against" "${r}" 0 ovms-vlm \
    '--max_prompt_len=4096$' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VLM_DEVICE=NPU
addon_says "K3D_GPU_AGENT_VLM_MAX_PROMPT_LEN sets the NPU prompt limit" "${r}" 0 ovms-vlm \
    '--max_prompt_len=8192$' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VLM_DEVICE=NPU K3D_GPU_AGENT_VLM_MAX_PROMPT_LEN=8192
addon_says "VLM on the GPU has no NPU prompt limit" "${r}" 0 ovms-vlm \
    'max_prompt_len' negate STUB_ALLOC="${SHARED}"

WITH="--with video"
addon_says "GPU video decodes into VA surfaces" "${r}" 0 - \
    'decodebin3 ! vapostproc ! video/x-raw\(memory:VAMemory\)' "" STUB_ALLOC="${SHARED}"
addon_says "GPU video ranks the va decoders first" "${r}" 0 video-analytics \
    'value: vah264dec:MAX,vah265dec:MAX' "" STUB_ALLOC="${SHARED}"
addon_says "CPU video leaves decoder ranks alone" "${r}" 0 video-analytics \
    'GST_PLUGIN_FEATURE_RANK' negate STUB_ALLOC='cpu 32'
addon_says "CPU video requests no GPU" "${r}" 0 video-analytics \
    'gpu\.intel\.com' negate STUB_ALLOC='cpu 32'
addon_says "the DL Streamer image is pinned by digest" "${r}" 0 video-analytics \
    'image: intel/dlstreamer@sha256:[0-9a-f]{64}' "" STUB_ALLOC="${SHARED}"
addon_says "the model is checked against its sha256" "${r}" 0 video-analytics \
    'sha256sum -c' "" STUB_ALLOC="${SHARED}"
addon_says "the sample clip is fetched by default" "${r}" 0 video-analytics \
    'input\.mp4 https://' "" STUB_ALLOC="${SHARED}"
addon_says "a video source skips the sample clip" "${r}" 0 video-analytics \
    'input\.mp4 https://' negate STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VIDEO_SOURCE=rtsp://cam/stream
addon_says "a video source reaches the pipeline" "${r}" 0 video-analytics \
    'value: "rtsp://cam/stream"' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VIDEO_SOURCE=rtsp://cam/stream
addon_says "a host path as video source is rejected (the pod cannot see it)" "${r}" fail - \
    'K3D_GPU_AGENT_VIDEO_SOURCE must be a stream URI' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VIDEO_SOURCE=/home/me/cam.mp4
addon_says "a file:// video source is rejected too" "${r}" fail - \
    'K3D_GPU_AGENT_VIDEO_SOURCE must be a stream URI' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VIDEO_SOURCE=file:///home/me/cam.mp4
addon_says "a video source with a quote cannot break out of the YAML string" "${r}" fail - \
    'K3D_GPU_AGENT_VIDEO_SOURCE must be a stream URI' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VIDEO_SOURCE='rtsp://cam/x"
        privileged: true'
addon_says "a video source with a backslash is rejected" "${r}" fail - \
    'K3D_GPU_AGENT_VIDEO_SOURCE must be a stream URI' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VIDEO_SOURCE='rtsp://cam/a\b'
addon_says "a video source with a gst-launch '!' is rejected" "${r}" fail - \
    'K3D_GPU_AGENT_VIDEO_SOURCE must be a stream URI' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VIDEO_SOURCE='rtsp://cam/a!fakesink'
addon_says "a video source with a space is rejected" "${r}" fail - \
    'K3D_GPU_AGENT_VIDEO_SOURCE must be a stream URI' "" STUB_ALLOC="${SHARED}" K3D_GPU_AGENT_VIDEO_SOURCE='rtsp://cam/a b'
addon_says "an https video source with a query string is accepted" "${r}" 0 video-analytics \
    'value: "https://cam.example/live\.m3u8\?token=abc&x=1"' "" STUB_ALLOC="${SHARED}" \
    K3D_GPU_AGENT_VIDEO_SOURCE='https://cam.example/live.m3u8?token=abc&x=1'

r=$(newroot addon-hybrid)
hybrid "${r}" 0-15 16-31
WITH="--with vlm,video"
addon_says "on a hybrid node an offloaded add-on sits on E-cores" "${r}" 0 video-analytics \
    'k3d-gpu.io/cores: efficient' "" STUB_ALLOC="${SHARED}" STUB_NODE_LABELS=k3d-gpu.io/hybrid-cpu=true
addon_says "on a hybrid node a CPU add-on sits on P-cores" "${r}" 0 ovms-vlm \
    'k3d-gpu.io/cores: performance' "" STUB_ALLOC="${ONE}" STUB_NODE_LABELS=k3d-gpu.io/hybrid-cpu=true

WITH="--with tts"
addon_says "an unknown add-on is rejected" "${r}" fail - "unknown agent add-on 'tts'" "" STUB_ALLOC="${SHARED}"
WITH="--with"
addon_says "--with needs a list" "${r}" fail - '--with needs a list' "" STUB_ALLOC="${SHARED}"
WITH="--gpu"
addon_says "an unknown agent option is rejected" "${r}" fail - "unknown agent option '--gpu'" "" STUB_ALLOC="${SHARED}"
WITH=""

# up_applies <name> <yes|no> [env...]: whether `agent up ${WITH}` applies anything.
# It must stop first when the plan fails: the planners run inside command
# substitutions, where set -e does not reach.
up_applies() {
    local name=$1 want=$2 applied="${WORK}/applied.log"
    shift 2
    rm -f "${applied}"
    # shellcheck disable=SC2086 # WITH is a word list
    env K3D_GPU_SYSROOT="${r}" PATH="${ASTUB}:${PATH}" STUB_APPLY_LOG="${applied}" "$@" \
        bash "${K3D_GPU}" agent up ${WITH} >/dev/null 2>&1
    if { [ "${want}" = no ] && [ ! -s "${applied}" ]; } || { [ "${want}" = yes ] && [ -s "${applied}" ]; }; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name} (applied: $([ -s "${applied}" ] && echo yes || echo no), want ${want})"
    fi
}
WITH="--with stt"
up_applies "agent up applies nothing when an add-on's device is missing" no \
    STUB_ALLOC='cpu 8' K3D_GPU_AGENT_STT_DEVICE=NPU
up_applies "agent up --with applies nothing when the LLM's device is missing" no \
    STUB_ALLOC='cpu 8' K3D_GPU_AGENT_DEVICE=GPU
up_applies "agent up applies a plan that fits" yes STUB_ALLOC=$'cpu 8\nnpu.intel.com/accel 1'
WITH=""

# `agent up ${WITH}` must delete the add-ons left out of WITH before it applies
# the plan, because the plan counts the agent namespace's devices as free.
log="${WORK}/addon-kubectl.log"
WARM='{"choices":[{"message":{"content":"hi"}}]}'
addon_up() {
    : > "${log}"
    # shellcheck disable=SC2086 # WITH is a word list
    env K3D_GPU_SYSROOT="${r}" PATH="${ASTUB}:${PATH}" STUB_LOG="${log}" STUB_EXEC="${WARM}" "$@" \
        bash "${K3D_GPU}" agent up ${WITH} 2>&1
}
WITH="--with vlm"
addon_up STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' >/dev/null
agent_log_before "agent up --with vlm first removes a video add-on from an earlier run" \
    'delete .*deployment/video-analytics' '^kubectl apply -f -$'
agent_log_before "... and an stt add-on" 'delete .*deployment/ovms-stt' '^kubectl apply -f -$'
agent_log_before "... and waits for their pods to go" 'delete .*--cascade=foreground --wait=true' '^kubectl apply -f -$'
if grep -E -- 'delete .*deployment/ovms-vlm( |$)' "${log}" >/dev/null; then
    FAILS=$((FAILS + 1)); echo "FAIL ... but keeps the vlm it is about to update"; awk '{print "  | " $0}' "${log}"
else
    PASS=$((PASS + 1)); echo "ok   ... but keeps the vlm it is about to update"
fi
# The VLM warm-up POSTs from inside its pod, as the LLM's does: through the API
# server's service proxy OVMS answers "Mediapipe graph definition ... not found".
agent_log_has "the VLM warm-up runs inside the vlm pod" 'exec deployment/ovms-vlm -c ovms -- curl .*/v3/chat/completions'
agent_log_has "agent up waits for the vlm add-on" 'rollout status deployment/ovms-vlm'
if grep -q 'create --raw' "${log}"; then
    FAILS=$((FAILS + 1)); echo "FAIL no OVMS warm-up goes through the service proxy"; awk '{print "  | " $0}' "${log}"
else
    PASS=$((PASS + 1)); echo "ok   no OVMS warm-up goes through the service proxy"
fi
WITH=""
addon_up STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 1' >/dev/null
agent_log_before "plain agent up removes every add-on before taking the GPU" \
    'delete deployment/ovms-vlm service/ovms-vlm deployment/ovms-stt service/ovms-stt deployment/video-analytics configmap/video-analytics' \
    '^kubectl apply -f -$'
WITH="--with vlm,stt,video"
addon_up STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 3\nnpu.intel.com/accel 1' >/dev/null
if grep -E -- 'delete .*(ovms-vlm|ovms-stt|video-analytics)' "${log}" >/dev/null; then
    FAILS=$((FAILS + 1)); echo "FAIL agent up with every add-on removes none"; awk '{print "  | " $0}' "${log}"
else
    PASS=$((PASS + 1)); echo "ok   agent up with every add-on removes none"
fi
WITH=""

# A VLM reply without choices (OVMS answering with an error) fails agent up.
WITH="--with vlm"
out=$(addon_up STUB_ALLOC=$'cpu 32\ngpu.intel.com/i915 2' \
    STUB_VLM_REPLY='{"error":"Mediapipe graph definition with requested name is not found"}') && rc=0 || rc=$?
if [ "${rc}" -ne 0 ] && grep -q 'vlm warm-up request failed' <<< "${out}"; then
    PASS=$((PASS + 1)); echo "ok   a VLM warm-up reply without choices fails agent up"
else
    FAILS=$((FAILS + 1)); echo "FAIL a VLM warm-up reply without choices fails agent up (rc=${rc})"; awk '{print "  | " $0}' <<< "${out}"
fi
WITH=""

# agent down deletes the LLM and every add-on, and keeps the model PVCs.
: > "${log}"
env K3D_GPU_SYSROOT="${r}" PATH="${ASTUB}:${PATH}" STUB_LOG="${log}" bash "${K3D_GPU}" agent down >/dev/null 2>&1
agent_log_has "agent down removes the OVMS LLM" 'delete deployment/ovms service/ovms'
agent_log_has "agent down removes every add-on" \
    'delete deployment/ovms-vlm service/ovms-vlm deployment/ovms-stt service/ovms-stt deployment/video-analytics configmap/video-analytics'
if grep -qi 'pvc\|persistentvolumeclaim' "${log}"; then
    FAILS=$((FAILS + 1)); echo "FAIL agent down keeps the model PVCs"; awk '{print "  | " $0}' "${log}"
else
    PASS=$((PASS + 1)); echo "ok   agent down keeps the model PVCs"
fi

# ---- WSL2 (Windows) -----------------------------------------------------------

# wsl <root> [driver store dir...]: a WSL2 distro. GPU-PV shows up as /dev/dxg, the
# Windows driver store under usr/lib/wsl/drivers, and its libraries under
# usr/lib/wsl/lib. WSLg's vgem render node has no PCI vendor behind it.
wsl() {
    local r=$1 d
    shift
    mkdir -p "${r}/proc" "${r}/usr/lib/wsl/lib" "${r}/sys/devices/platform/vgem/drm/renderD128" "${r}/sys/class/drm" "${r}/dev/dri"
    echo "Linux version 6.6.87.2-microsoft-standard-WSL2 (root@439a258ad544) (gcc (GCC) 11.2.0) #1 SMP PREEMPT_DYNAMIC" > "${r}/proc/version"
    touch "${r}/dev/dxg" "${r}/usr/lib/wsl/lib/libdxcore.so" "${r}/dev/dri/renderD128"
    ln -sfn "${r}/sys/devices/platform/vgem" "${r}/sys/devices/platform/vgem/drm/renderD128/device"
    ln -sfn "${r}/sys/devices/platform/vgem/drm/renderD128" "${r}/sys/class/drm/renderD128"
    for d in "$@"; do mkdir -p "${r}/usr/lib/wsl/drivers/${d}"; done
}

r=$(newroot wsl-intel)
wsl "${r}" iigd_dch.inf_amd64_8a3b1c6f2e0d4a17
expect "WSL2 with the Intel graphics driver is wsl, not intel" "${r}" "wsl gpu.intel.com/dxg"

r=$(newroot wsl-arc)
wsl "${r}" iigd_dch_d.inf_amd64_0f1e2d3c4b5a6978
expect "WSL2 with the Intel Arc driver package is wsl" "${r}" "wsl gpu.intel.com/dxg"

r=$(newroot wsl-nvidia)
wsl "${r}" nv_dispi.inf_amd64_5b9f0c6c1a7e2d34
touch "${r}/usr/lib/wsl/lib/nvidia-smi"
expect "WSL2 with only the NVIDIA driver is nvidia, not wsl" "${r}" "nvidia nvidia.com/gpu"

r=$(newroot wsl-both)
wsl "${r}" iigd_dch.inf_amd64_8a3b1c6f2e0d4a17 nv_dispi.inf_amd64_5b9f0c6c1a7e2d34
touch "${r}/usr/lib/wsl/lib/nvidia-smi"
expect "WSL2 laptop with Intel iGPU and NVIDIA dGPU is both" "${r}" \
"nvidia nvidia.com/gpu
wsl gpu.intel.com/dxg"

r=$(newroot wsl-no-dxg)
wsl "${r}" iigd_dch.inf_amd64_8a3b1c6f2e0d4a17
rm "${r}/dev/dxg"
expect "WSL2 without /dev/dxg (GPU-PV off) is not wsl" "${r}" ""

r=$(newroot dxg-not-wsl)
wsl "${r}" iigd_dch.inf_amd64_8a3b1c6f2e0d4a17
echo "Linux version 6.18.1-arch1-1 (linux@archlinux) (gcc (GCC) 15.2.1)" > "${r}/proc/version"
expect "/dev/dxg on a kernel that is not WSL is not wsl" "${r}" ""

r=$(newroot wsl-mixed-case)
wsl "${r}" iigd_dch.inf_amd64_8a3b1c6f2e0d4a17
echo "Linux version 5.15.167.4-Microsoft-standard-WSL2 (root@1a2b3c) (gcc (GCC) 11.2.0)" > "${r}/proc/version"
expect "WSL kernel string is matched case-insensitively" "${r}" "wsl gpu.intel.com/dxg"

r=$(newroot wsl-forced)
expect "forced wsl is accepted" "${r}" "wsl gpu.intel.com/dxg" K3D_GPU_VENDORS=wsl

r=$(newroot wsl-doc)
kernel "${r}" 6.6.87.2-microsoft-standard-WSL2
wsl "${r}" iigd_dch.inf_amd64_8a3b1c6f2e0d4a17
doctor_says "doctor reports /dev/dxg on WSL2" "${r}" 'ok: WSL2 .*/dev/dxg'
doctor_says "doctor names the Intel driver store package" "${r}" 'ok: .*iigd_dch\.inf_amd64_8a3b1c6f2e0d4a17'
doctor_says "doctor does not ask for xe.force_probe on WSL2" "${r}" 'force_probe' negate
doctor_says "doctor says a driver store package does not prove a live Intel GPU" "${r}" \
    'note: .*driver package.*not .*live Intel GPU.*Device Manager'
rm "${r}/usr/lib/wsl/lib/libdxcore.so"
doctor_says "doctor warns when libdxcore.so is missing" "${r}" 'warn.*libdxcore'

r=$(newroot wsl-doc-nointel)
kernel "${r}" 6.6.87.2-microsoft-standard-WSL2
wsl "${r}" nv_dispi.inf_amd64_5b9f0c6c1a7e2d34
doctor_says "forced wsl without an Intel driver store warns about the Windows driver" "${r}" \
    'warn.*Intel graphics driver' "" K3D_GPU_VENDORS=wsl

r=$(newroot wsl-doc-native)
kernel "${r}" 6.18.1
doctor_says "forced wsl on a native Linux host fails doctor" "${r}" 'fail.*WSL2' "" K3D_GPU_VENDORS=wsl

r=$(newroot wsl-doc-nvidia)
kernel "${r}" 6.6.87.2-microsoft-standard-WSL2
wsl "${r}" nv_dispi.inf_amd64_5b9f0c6c1a7e2d34
touch "${r}/usr/lib/wsl/lib/nvidia-smi"
doctor_says "NVIDIA on WSL2 is ok without /dev/nvidia*" "${r}" 'ok: WSL2: NVIDIA .*/dev/dxg'
doctor_says "NVIDIA on WSL2 does not fail on missing /dev/nvidia*" "${r}" 'no /dev/nvidia\* devices' negate

r=$(newroot wsl-test)
wsl "${r}" iigd_dch.inf_amd64_8a3b1c6f2e0d4a17
test_says "wsl test lists /dev/dxg in a pod holding gpu.intel.com/dxg" "${r}" 0 'crw-rw-rw-.*/dev/dxg' \
    STUB_POD_PHASE=Succeeded STUB_POD_LOGS="crw-rw-rw-    1 root     root       10, 125 Oct 16 09:00 /dev/dxg"

# `up` on WSL2: the node gets the Windows driver store and the plugin's gate label.
# k3d records its arguments; kubectl reports STUB_ALLOC (default one
# gpu.intel.com/dxg) as allocatable so verify passes.
USTUB="${WORK}/ubin"
mkdir -p "${USTUB}"
cp "${STUB_BIN}/docker" "${USTUB}/docker"
cat > "${USTUB}/k3d" <<'EOF'
#!/bin/sh
printf '%s\n' "$@" > "${STUB_K3D_ARGS}"
EOF
cat > "${USTUB}/kubectl" <<'EOF'
#!/bin/sh
case "$*" in
    *allocatable*) echo "${STUB_ALLOC:-gpu.intel.com/dxg 1}" ;;
    "get nodes -l "*) echo node/k3d-gpu-server-0 ;;
esac
exit 0
EOF
chmod +x "${USTUB}"/*

# up_args <name> <root> <ERE> [negate] [env...]: `up` must succeed and its k3d
# arguments must match <ERE> (or must not with a 4th argument).
up_args() {
    local name=$1 root=$2 re=$3 negate=${4:-} out rc hit=0 args="${WORK}/k3d-args"
    shift 4 2>/dev/null || shift $#
    rm -f "${args}"
    out=$(env K3D_GPU_SYSROOT="${root}" STUB_K3D_ARGS="${args}" PATH="${USTUB}:${PATH}" "$@" bash "${K3D_GPU}" up 2>&1)
    rc=$?
    grep -E -- "${re}" "${args}" >/dev/null 2>&1 && hit=1
    if [ "${rc}" -eq 0 ] && { { [ -z "${negate}" ] && [ "${hit}" -eq 1 ]; } || { [ -n "${negate}" ] && [ "${hit}" -eq 0 ]; }; }; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (rc=${rc}, ${negate:+not }matching /${re}/)"
        sed 's/^/  | /' <<< "${out}"
        [ -f "${args}" ] && sed 's/^/  k3d> /' "${args}"
    fi
}

r=$(newroot wsl-up)
wsl "${r}" iigd_dch.inf_amd64_8a3b1c6f2e0d4a17
up_args "up on WSL2 mounts the Windows driver store into the node read-only" "${r}" \
    '^--volume=/usr/lib/wsl:/usr/lib/wsl:ro@server:\*;agent:\*$'
up_args "up on WSL2 labels the node for the WSL Intel GPU plugin" "${r}" \
    '^--k3s-node-label=k3d-gpu\.io/wsl-gpu=true@server:\*;agent:\*$'
up_args "up on WSL2 without NVIDIA does not pass --gpus" "${r}" '^--gpus' negate
r=$(newroot native-up)
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe; render "${r}" 0000:03:00.0 128
up_args "up on native Linux does not mount /usr/lib/wsl" "${r}" 'usr/lib/wsl' negate STUB_ALLOC="gpu.intel.com/xe 1"

# Every intel-device-plugins image ships from one release, and the plugins share
# its CRDs and APIs, so the manifests must all carry the GPU plugin's tag. The CI
# auto-bump rewrites each file with its own sed; one it misses shows up here.
SHARE="$(dirname "${K3D_GPU}")/../share"
gpu_tag=$(grep -ho 'intel/intel-gpu-plugin:[0-9.]*' "${SHARE}/intel-gpu-plugin.yml" | head -n1)
gpu_tag=${gpu_tag##*:}
drift=$(grep -Ho 'intel/intel-[a-z-]*:[0-9][0-9.]*' "${SHARE}"/*.yml | grep -v ":${gpu_tag}\$" || true)
if [ -n "${gpu_tag}" ] && [ -z "${drift}" ]; then
    PASS=$((PASS + 1)); echo "ok   every intel-device-plugins image matches intel-gpu-plugin:${gpu_tag}"
else
    FAILS=$((FAILS + 1)); echo "FAIL intel-device-plugins images differ from intel-gpu-plugin:${gpu_tag}"
    while read -r line; do echo "  | ${line}"; done <<< "${drift}"
fi

# One NRI policy per node: the baked balloons addon stays only for a hybrid CPU with
# k3s owning it; otherwise k3s is told to delete it so a Helm release can own the node.
NO_BALLOONS='^--k3s-arg=--disable=nri-balloons@server:\*$'
r=$(newroot up-hybrid)
hybrid "${r}" 0-15 16-31
up_args "up keeps the baked balloons policy on a hybrid CPU" "${r}" "${NO_BALLOONS}" negate
up_args "up labels a hybrid node" "${r}" '^--k3s-node-label=k3d-gpu.io/hybrid-cpu=true@server:\*;agent:\*$'
up_args "K3D_GPU_BALLOONS=external disables the baked balloons policy" "${r}" "${NO_BALLOONS}" "" \
    K3D_GPU_BALLOONS=external
up_args "K3D_GPU_BALLOONS=external still labels the hybrid node" "${r}" 'k3d-gpu.io/hybrid-cpu=true' "" \
    K3D_GPU_BALLOONS=external

r=$(newroot up-xeon)
mkdir -p "${r}/proc"
echo "flags		: fpu sse2 avx512f amx_bf16 amx_tile amx_int8" > "${r}/proc/cpuinfo"
up_args "up disables the baked balloons policy on a non-hybrid CPU" "${r}" "${NO_BALLOONS}"
up_args "K3D_GPU_VENDORS without hybrid disables the baked balloons policy" "${r}" "${NO_BALLOONS}" "" \
    K3D_GPU_VENDORS=amx

# The same baked policy serves NUMA hosts (replica balloons), hybrid or not.
r=$(newroot up-numa)
node "${r}" 0 0-3,8-11 0; node "${r}" 1 4-7,12-15 1; l3 "${r}" 0-3,8-11
mkdir -p "${r}/proc"
echo "flags		: fpu sse2 avx512f amx_bf16 amx_tile amx_int8" > "${r}/proc/cpuinfo"
up_args "up keeps the baked balloons policy on a NUMA host" "${r}" "${NO_BALLOONS}" negate
up_args "K3D_GPU_BALLOONS=external disables it on a NUMA host" "${r}" "${NO_BALLOONS}" "" \
    K3D_GPU_BALLOONS=external
up_args "K3D_GPU_BALLOONS=external still labels the NUMA node" "${r}" 'k3d-gpu.io/numa-nodes=2' "" \
    K3D_GPU_BALLOONS=external
up_args "K3D_GPU_VENDORS without numa disables it on a NUMA host" "${r}" "${NO_BALLOONS}" "" \
    K3D_GPU_VENDORS=amx

# With external, up leaves the Helm release's policy alone: no favored-CPU patch and
# no OVERRIDE_SYS_ATOM_CPUS env (a later helm upgrade would drop them anyway).
r=$(newroot up-external-noedit)
hybrid "${r}" 0-13 14-27
mkdir -p "${r}/sys/devices/cpu_lowpower"; echo 28-29 > "${r}/sys/devices/cpu_lowpower/cpus"
for c in $(seq 0 29); do cppc "${r}" "${c}" 65; done
for c in 8 9 10 11; do cppc "${r}" "${c}" 68; done
for c in $(seq 0 2 12); do for t in "${c}" $((c + 1)); do cpucache "${r}" "${t}" 2 "${c}-$((c + 1))"; done; done
fav_up "k3s owner: up patches the latency type" "${r}" 'patch balloonspolicy default'
fav_up "k3s owner: up sets OVERRIDE_SYS_ATOM_CPUS for LP E-cores" "${r}" 'set env daemonset/nri-resource-policy-balloons OVERRIDE_SYS_ATOM_CPUS='
fav_up "external owner: up does not patch the latency type" "${r}" 'balloonspolicy' negate \
    K3D_GPU_BALLOONS=external
fav_up "external owner: up does not set OVERRIDE_SYS_ATOM_CPUS" "${r}" 'OVERRIDE_SYS_ATOM_CPUS' negate \
    K3D_GPU_BALLOONS=external
fav_up "external owner: up does not wait for the balloons DaemonSet" "${r}" 'daemonset/nri-resource-policy-balloons' negate \
    K3D_GPU_BALLOONS=external
fav_up "external owner: favored CPUs still annotated" "${r}" 'annotate nodes --all --overwrite k3d-gpu.io/favored-cpus=8-11$' "" \
    K3D_GPU_BALLOONS=external

r=$(newroot numa-doc-external)
kernel "${r}" 6.18.1
node "${r}" 0 0-3 0; node "${r}" 1 4-7 1; l3 "${r}" 0-3
doctor_says "doctor says external leaves a NUMA host's policy to another stack" "${r}" \
    'ok: NUMA: .*leaves the NRI balloons policy to another stack \(K3D_GPU_BALLOONS=external' "" \
    K3D_GPU_BALLOONS=external

# contrib/eat/hybrid-values.yaml must pass the CRD the baked file ships (the API
# server prunes unknown fields) and offer every k3d-gpu.io/cores type the baked
# policy has, so labelled pods place the same under EAT's release.
if python3 -c 'import yaml' 2>/dev/null; then
    hv="${WORK}/hybrid-values-policy.yml"
    python3 - "${BALLOONS_YML:-$(dirname "${K3D_GPU}")/../share/nri-balloons.yml}" \
        "$(dirname "${K3D_GPU}")/../contrib/eat/hybrid-values.yaml" > "${hv}" <<'EOF'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
crd = next(d for d in docs if d["kind"] == "CustomResourceDefinition"
           and d["metadata"]["name"] == "balloonspolicies.config.nri")
vals = yaml.safe_load(open(sys.argv[2]))
pol = {"apiVersion": "config.nri/v1alpha1", "kind": "BalloonsPolicy",
       "metadata": {"name": "default", "namespace": "kube-system"}, "spec": vals["config"]}
print(yaml.safe_dump_all([crd, pol]))
EOF
    if out=$(python3 "${ROOT}/tests/nri_balloons_check.py" "${hv}" 2>&1); then
        PASS=$((PASS + 1)); echo "ok   contrib/eat/hybrid-values.yaml matches the balloons CRD schema"
    else
        FAILS=$((FAILS + 1)); echo "FAIL contrib/eat/hybrid-values.yaml matches the balloons CRD schema"; sed 's/^/  | /' <<< "${out}"
    fi
    missing=$(python3 - "$(dirname "${K3D_GPU}")/../share/nri-balloons.yml" \
        "$(dirname "${K3D_GPU}")/../contrib/eat/hybrid-values.yaml" <<'EOF'
import sys, yaml
def cores(types):
    return {e["values"][0] for t in types for e in t.get("matchExpressions", [])
            if e["key"] == "pod/labels/k3d-gpu.io/cores"}
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
baked = cores(next(d for d in docs if d["kind"] == "BalloonsPolicy")["spec"]["balloonTypes"])
eat = cores(yaml.safe_load(open(sys.argv[2]))["config"]["balloonTypes"])
print(" ".join(sorted(baked - eat - {"replica"})))
EOF
)
    if [ -z "${missing}" ]; then
        PASS=$((PASS + 1)); echo "ok   contrib/eat/hybrid-values.yaml has every hybrid k3d-gpu.io/cores type"
    else
        FAILS=$((FAILS + 1)); echo "FAIL contrib/eat/hybrid-values.yaml lacks k3d-gpu.io/cores types: ${missing}"
    fi
else
    echo "skip contrib/eat/hybrid-values.yaml checks (python3 yaml module missing)"
fi

# Helm adopts an existing object only if it carries managed-by=Helm and the release
# name/namespace annotations; every chart-templated object in the baked manifest must,
# so Intel EAT's `helm upgrade --install nri-resource-policy-balloons -n kube-system` can
# take it over instead of failing.
BALLOONS_YML="$(dirname "${K3D_GPU}")/../share/nri-balloons.yml"
unowned=$(awk '
    function check() {
        if (tmpl && !(mb && rn && rns)) print src
        tmpl = mb = rn = rns = 0
    }
    /^---$/ { check(); next }
    /^# Source: .*\/templates\// { tmpl = 1; src = $3 }
    /^    app.kubernetes.io\/managed-by: Helm$/ { mb = 1 }
    /^    meta.helm.sh\/release-name: nri-resource-policy-balloons$/ { rn = 1 }
    /^    meta.helm.sh\/release-namespace: kube-system$/ { rns = 1 }
    END { check() }' "${BALLOONS_YML}")
owned=$(grep -c '^# Source: .*/templates/' "${BALLOONS_YML}")
if [ -z "${unowned}" ] && [ "${owned}" -gt 0 ]; then
    PASS=$((PASS + 1)); echo "ok   all ${owned} templated balloons objects carry Helm ownership metadata"
else
    FAILS=$((FAILS + 1)); echo "FAIL templated balloons objects without Helm ownership metadata: ${unowned:-none found}"
fi

r=$(newroot balloons-bad)
expect_fail "unknown K3D_GPU_BALLOONS is rejected" "${r}" K3D_GPU_BALLOONS=helm

r=$(newroot hybrid-doc-external)
kernel "${r}" 6.18.1
hybrid "${r}" 0-15 16-31
doctor_says "doctor says K3D_GPU_BALLOONS=external leaves the policy to another stack" "${r}" \
    'ok: hybrid CPU: .*leaves the NRI balloons policy to another stack \(K3D_GPU_BALLOONS=external' "" \
    K3D_GPU_BALLOONS=external
doctor_says "doctor says a hybrid CPU left out of K3D_GPU_VENDORS gets no policy" "${r}" \
    'leaves the NRI balloons policy to another stack .*K3D_GPU_VENDORS=amx' "" K3D_GPU_VENDORS=amx

# ---- Intel GPU sharing ----------------------------------------------------------

# Integrated GPU at 00:02.0 (UHD 770 on Raptor Lake, i915) and a Battlemage dGPU.
r=$(newroot igpu-share)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:02.0 0x8086 0xa780 i915; render "${r}" 0000:00:02.0 128
doctor_says "iGPU-only host shares the GPU 8 ways by default" "${r}" 'ok: GPU sharing on: up to 8 pods per Intel GPU, balanced allocation'
doctor_says "explicit K3D_GPU_INTEL_SHARED=1 turns sharing off" "${r}" 'ok: GPU sharing off' "" K3D_GPU_INTEL_SHARED=1
doctor_says "K3D_GPU_INTEL_ALLOCATION=packed is reported" "${r}" 'up to 8 pods .*packed allocation' "" K3D_GPU_INTEL_ALLOCATION=packed
for bad in 0 04 -2 abc 1.5 " 3"; do
    doctor_says "K3D_GPU_INTEL_SHARED='${bad}' fails doctor" "${r}" "fail.*K3D_GPU_INTEL_SHARED='${bad}'" "" K3D_GPU_INTEL_SHARED="${bad}"
done
doctor_says "unknown K3D_GPU_INTEL_ALLOCATION fails doctor" "${r}" "fail.*K3D_GPU_INTEL_ALLOCATION='spread'" "" K3D_GPU_INTEL_ALLOCATION=spread
doctor_says "K3D_GPU_INTEL_MONITORING=yes fails doctor" "${r}" "fail.*K3D_GPU_INTEL_MONITORING='yes'" "" K3D_GPU_INTEL_MONITORING=yes
doctor_says "K3D_GPU_INTEL_MONITORING=1 is reported" "${r}" 'ok: gpu.intel.com/monitoring enabled' "" K3D_GPU_INTEL_MONITORING=1

r=$(newroot dgpu-share)
kernel "${r}" 6.18.1
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe; render "${r}" 0000:03:00.0 128
doctor_says "discrete GPU stays exclusive by default" "${r}" 'ok: GPU sharing off: one pod per Intel GPU \(K3D_GPU_INTEL_SHARED=auto\)'
doctor_says "discrete GPU can be shared explicitly" "${r}" 'up to 3 pods per Intel GPU' "" K3D_GPU_INTEL_SHARED=3

r=$(newroot mixed-share)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:02.0 0x8086 0xa780 i915; render "${r}" 0000:00:02.0 128
pci "${r}" 0000:03:00.0 0x8086 0xe20b xe;   render "${r}" 0000:03:00.0 129
doctor_says "iGPU plus dGPU stays exclusive by default (one count covers both)" "${r}" 'ok: GPU sharing off'

# A discrete card at the iGPU's slot (passed through to a VM guest) is not an iGPU.
r=$(newroot vm-dgpu-share)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:02.0 0x8086 0xe20b xe; render "${r}" 0000:00:02.0 128
doctor_says "Battlemage at 00:02.0 stays exclusive by default" "${r}" 'ok: GPU sharing off'
r=$(newroot vm-alchemist-share)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:02.0 0x8086 0x56a0 i915; render "${r}" 0000:00:02.0 128
doctor_says "Alchemist at 00:02.0 stays exclusive by default" "${r}" 'ok: GPU sharing off'

# iGPU SR-IOV: the PF at 00:02.0 and a VF at 00:02.1 are both the integrated GPU.
r=$(newroot igpu-vf-share)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:02.0 0x8086 0xa780 i915; render "${r}" 0000:00:02.0 128
pci "${r}" 0000:00:02.1 0x8086 0xa780 i915; render "${r}" 0000:00:02.1 129
doctor_says "iGPU with an SR-IOV VF is still shared by default" "${r}" 'ok: GPU sharing on: up to 8 pods'

# `up` against stubbed k3d/docker/kubectl. STUB_NODE is the server node's
# filesystem: docker exec reads and writes under it. kubectl reports the plugin
# DaemonSet's args from the node's manifest (as k3s would apply it), records
# `apply -f -` input in STUB_NODE/applied.yaml, and STUB_ALLOCATABLE as node allocatable.
USTUB="${WORK}/ubin"
mkdir -p "${USTUB}"
printf '#!/bin/sh\nexit 0\n' > "${USTUB}/k3d"
cat > "${USTUB}/docker" <<'EOF'
#!/bin/sh
[ "$1" = exec ] || exit 0
shift
[ "$1" = -i ] && shift
shift  # node name
case "$1" in
    cat) exec cat "${STUB_NODE}$2" ;;
    sh)  exec sh -c "$(printf '%s' "$3" | sed "s#'/var/#'${STUB_NODE}/var/#g")" ;;
esac
exit 1
EOF
cat > "${USTUB}/kubectl" <<'EOF'
#!/bin/sh
case "$*" in
    *"get nodes -o go-template"*) printf '%s\n' "${STUB_ALLOCATABLE}" ;;
    *"get daemonset intel-gpu-plugin -o jsonpath"*)
        [ -n "${STUB_DS_NO_ARGS:-}" ] && exit 0  # k3s never applied the edit
        f="${STUB_NODE}/var/lib/rancher/k3s/server/manifests/intel-gpu-plugin.yaml"
        printf '[%s]' "$(sed -n 's/^ *- \("-.*"\)$/\1/p' "${f}" | paste -sd, -)" ;;
    "apply -f -") cat > "${STUB_NODE}/applied.yaml" ;;
esac
exit 0
EOF
chmod +x "${USTUB}/k3d" "${USTUB}/docker" "${USTUB}/kubectl"

# node_with_manifest <name>: a node fs holding the baked intel-gpu-plugin.yaml.
node_with_manifest() {
    local n="${WORK}/node-$1"
    mkdir -p "${n}/var/lib/rancher/k3s/server/manifests"
    cp "$(dirname "${K3D_GPU}")/../share/intel-gpu-plugin.yml" "${n}/var/lib/rancher/k3s/server/manifests/intel-gpu-plugin.yaml"
    echo "${n}"
}

# up_check <name> <root> <node> <want rc: 0|fail> <file under node> <want text, or "unchanged"> [env...]:
# run `up`, then the file must contain <want text> verbatim (or match the baked manifest).
# A successful run must also see the DaemonSet pick up the args (no "not applied" warning).
up_check() {
    local name=$1 root=$2 node=$3 want=$4 file=$5 text=$6 out rc ok=1
    shift 6
    out=$(env K3D_GPU_SYSROOT="${root}" PATH="${USTUB}:${PATH}" STUB_NODE="${node}" \
        STUB_ALLOCATABLE="gpu.intel.com/i915 8" K3D_GPU_VENDORS=intel "$@" bash "${K3D_GPU}" up 2>&1)
    rc=$?
    { [ "${want}" = 0 ] && [ "${rc}" -eq 0 ]; } || { [ "${want}" = fail ] && [ "${rc}" -ne 0 ]; } || ok=0
    if [ "${want}" = 0 ] && [[ "${out}" == *"has not applied the new intel-gpu-plugin args"* ]]; then ok=0; fi
    if [ "${want}" = 0 ] && [[ "${out}" == *"Intel GPU slots, expected"* ]]; then ok=0; fi
    if [ "${text}" = unchanged ]; then
        cmp -s "${node}/${file}" "$(dirname "${K3D_GPU}")/../share/intel-gpu-plugin.yml" || ok=0
    elif [ -n "${text}" ]; then
        # Whole-block match: grep -F would accept any one of the lines.
        [[ "$(cat "${node}/${file}" 2>/dev/null)" == *"${text}"* ]] || ok=0
    fi
    if [ "${ok}" -eq 1 ]; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (rc=${rc}, want ${want})"
        sed 's/^/  | /' <<< "${out}"
        sed 's/^/  > /' "${node}/${file}" 2>/dev/null
    fi
}

MF=var/lib/rancher/k3s/server/manifests/intel-gpu-plugin.yaml
r=$(newroot igpu-up)
pci "${r}" 0000:00:02.0 0x8086 0xa780 i915; render "${r}" 0000:00:02.0 128

n=$(node_with_manifest igpu-auto)
up_check "up adds shared-dev-num and allocation-policy under the plugin image" "${r}" "${n}" 0 "${MF}" \
'          image: intel/intel-gpu-plugin:0.37.1@sha256:004641b2547d58f1eadce58473fa160ad71377de1eb588cf1f82832387bf3e33
          args:
            - "-shared-dev-num=8"
            - "-allocation-policy=balanced"
          imagePullPolicy: IfNotPresent'
if compgen -G "${n}/var/lib/rancher/k3s/server/manifests/.*" >/dev/null; then
    FAILS=$((FAILS + 1)); echo "FAIL up leaves no temp file in the k3s manifests dir"
else
    PASS=$((PASS + 1)); echo "ok   up leaves no temp file in the k3s manifests dir"
fi

n=$(node_with_manifest igpu-mon)
up_check "up adds -enable-monitoring alone when sharing is off" "${r}" "${n}" 0 "${MF}" \
'          args:
            - "-enable-monitoring"
          imagePullPolicy' K3D_GPU_INTEL_SHARED=1 K3D_GPU_INTEL_MONITORING=1

n=$(node_with_manifest igpu-off)
up_check "up leaves the baked manifest alone with sharing off and no monitoring" "${r}" "${n}" 0 "${MF}" unchanged \
    K3D_GPU_INTEL_SHARED=1

n=$(node_with_manifest igpu-bad)
up_check "up rejects a bad K3D_GPU_INTEL_SHARED before creating anything" "${r}" "${n}" fail "${MF}" unchanged \
    K3D_GPU_INTEL_SHARED=0

n=$(node_with_manifest igpu-has-args)
sed -i 's#^\(          image: intel/intel-gpu-plugin:.*\)$#\1\n          args: ["-v=2"]#' "${n}/${MF}"
cp "${n}/${MF}" "${WORK}/has-args.yaml"
up_check "up fails rather than doubling an existing args list" "${r}" "${n}" fail "${MF}" '          args: ["-v=2"]'
if cmp -s "${n}/${MF}" "${WORK}/has-args.yaml"; then
    PASS=$((PASS + 1)); echo "ok   failed rewrite leaves the node manifest intact"
else
    FAILS=$((FAILS + 1)); echo "FAIL failed rewrite leaves the node manifest intact"
fi

# k3s never re-applies the edited file: up warns instead of reporting the args live.
n=$(node_with_manifest igpu-stale)
out=$(env K3D_GPU_SYSROOT="${r}" PATH="${USTUB}:${PATH}" STUB_NODE="${n}" STUB_DS_NO_ARGS=1 \
    STUB_ALLOCATABLE="gpu.intel.com/i915 1" K3D_GPU_VENDORS=intel K3D_GPU_INTEL_APPLY_TIMEOUT=1 \
    bash "${K3D_GPU}" up 2>&1)
if [[ "${out}" == *"has not applied the new intel-gpu-plugin args after 1s"* ]]; then
    PASS=$((PASS + 1)); echo "ok   up warns when the DaemonSet never picks up the new args"
else
    FAILS=$((FAILS + 1)); echo "FAIL up warns when the DaemonSet never picks up the new args"
    while IFS= read -r l; do echo "  | ${l}"; done <<< "${out}"
fi

# The args land but kubelet keeps the pre-share count: up warns with the numbers.
n=$(node_with_manifest igpu-oldcount)
out=$(env K3D_GPU_SYSROOT="${r}" PATH="${USTUB}:${PATH}" STUB_NODE="${n}" \
    STUB_ALLOCATABLE="gpu.intel.com/i915 1" K3D_GPU_VENDORS=intel K3D_GPU_INTEL_APPLY_TIMEOUT=1 \
    bash "${K3D_GPU}" up 2>&1)
if [[ "${out}" == *"cluster advertises 1 Intel GPU slots, expected 8 (8 per GPU, 1 GPU(s))"* ]] \
    && [[ "${out}" != *"has not applied"* ]]; then
    PASS=$((PASS + 1)); echo "ok   up warns when allocatable never reaches the shared count"
else
    FAILS=$((FAILS + 1)); echo "FAIL up warns when allocatable never reaches the shared count"
    while IFS= read -r l; do echo "  | ${l}"; done <<< "${out}"
fi

# A digest-pinned plugin image still gets its args.
n=$(node_with_manifest igpu-digest)
sed -i 's#^\(          image: intel/intel-gpu-plugin\):[^ ]*$#\1@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef#' "${n}/${MF}"
up_check "up adds args under a digest-pinned plugin image" "${r}" "${n}" 0 "${MF}" \
'          image: intel/intel-gpu-plugin@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
          args:
            - "-shared-dev-num=8"'

# Custom image with no baked manifest: the launcher applies the share/ copy with args.
n="${WORK}/node-bare"; mkdir -p "${n}"
up_check "custom image without the baked manifest gets the args via kubectl apply" "${r}" "${n}" 0 applied.yaml \
'            - "-shared-dev-num=4"
            - "-allocation-policy=packed"' K3D_GPU_INTEL_SHARED=4 K3D_GPU_INTEL_ALLOCATION=packed \
    K3D_GPU_INTEL_PLUGIN="$(dirname "${K3D_GPU}")/../share/intel-gpu-plugin.yml"

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
printf 'kubectl %s\n' "$*" >> "${STUB_LOG}"
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
    K3D_GPU_DRA=1 STUB_K8S_VERSION=v1.36.5+k3s1 STUB_SLICES="${DRA_SLICES}"
file_has "DRA up labels nodes for the DRA kubelet plugin" "${LOG}" \
    '^k3d-arg --k3s-node-label=k3d-gpu\.io/intel-gpu-dra=true@server:\*;agent:\*$'
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
file_has "non-DRA up never sets the DRA node label" "${LOG}" 'intel-gpu-dra=true' negate

run_says up "DRA on Kubernetes 1.34 fails: the driver is supported on 1.36+" "${r}" fail \
    'v1\.34\.1\+k3s1.*gpu-v0\.12\.1 needs Kubernetes 1\.36\+' K3D_GPU_DRA=1 STUB_SLICES="${DRA_SLICES}"
file_has "DRA on 1.34 never waits for the kubelet plugin" "${LOG}" 'rollout status daemonset/intel-gpu-resource-driver-kubelet-plugin' negate
run_says up "DRA on Kubernetes 1.37 passes the version gate" "${r}" 0 'schedulable: intel=1\.' \
    K3D_GPU_DRA=1 STUB_K8S_VERSION=v1.37.0+k3s1 STUB_SLICES="${DRA_SLICES}"
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

# DRA mode on an iGPU: the sharing default (#57) configures intel-gpu-plugin, which
# DRA mode disables, so up must not touch the plugin and says so when the user set it.
r3=$(newroot dra-igpu)
kernel "${r3}" 6.18.1
pci "${r3}" 0000:00:02.0 0x8086 0xa780 i915; render "${r3}" 0000:00:02.0 128
run_says up "DRA up on an iGPU verifies through ResourceSlices" "${r3}" 0 'schedulable: intel=1\.' \
    K3D_GPU_DRA=1 STUB_K8S_VERSION=v1.37.1+k3s1 STUB_SLICES="gpu.intel.com node0 1 i915"
file_has "DRA up on an iGPU never patches intel-gpu-plugin args" "${LOG}" 'intel-gpu-plugin\.yaml|daemonset intel-gpu-plugin' negate
run_says up "DRA up warns that plugin sharing settings are ignored" "${r3}" 0 \
    'K3D_GPU_DRA=1 ignores K3D_GPU_INTEL_SHARED=4 K3D_GPU_INTEL_MONITORING=1' \
    K3D_GPU_DRA=1 STUB_K8S_VERSION=v1.37.1+k3s1 STUB_SLICES="gpu.intel.com node0 1 i915" \
    K3D_GPU_INTEL_SHARED=4 K3D_GPU_INTEL_MONITORING=1
run_says up "DRA up still rejects a bad K3D_GPU_INTEL_SHARED" "${r3}" fail "K3D_GPU_INTEL_SHARED='0'" \
    K3D_GPU_DRA=1 K3D_GPU_INTEL_SHARED=0
doctor_says "doctor in DRA mode reports claim-based exclusivity, not plugin sharing" "${r3}" \
    'ok: DRA mode: one pod per Intel GPU per ResourceClaim' "" K3D_GPU_DRA=1
doctor_says "doctor in DRA mode never claims plugin sharing" "${r3}" 'GPU sharing on' negate K3D_GPU_DRA=1
doctor_says "doctor in DRA mode warns about ignored plugin settings" "${r3}" \
    'warn.*K3D_GPU_DRA=1 ignores K3D_GPU_INTEL_ALLOCATION=packed' "" K3D_GPU_DRA=1 K3D_GPU_INTEL_ALLOCATION=packed

# The DRA manifest is baked into every image, so launchers that never pass
# --disable=intel-gpu-dra (AUR <= 0.1.7, the README's manual k3d command) still
# apply it. The kubelet plugin must then stay off nodes without the DRA label.
DRA_YML="$(dirname "${K3D_GPU}")/../share/intel-gpu-dra.yml"
dra_ds=$(awk '/^---/ { ds = 0 } /^kind: DaemonSet$/ { ds = 1 } ds' "${DRA_YML}")
file_has "DRA kubelet plugin only runs on nodes labelled k3d-gpu.io/intel-gpu-dra" \
    <(grep -A1 '^      nodeSelector:$' <<< "${dra_ds}") '^        k3d-gpu\.io/intel-gpu-dra: "true"$'
for hp in /var/run/cdi /run/xpumd; do
    file_has "DRA hostPath ${hp} is DirectoryOrCreate" \
        <(grep -A1 "^          path: ${hp}\$" <<< "${dra_ds}") '^          type: DirectoryOrCreate$'
done

# intel_dra_devices' go-template, rendered by kubectl's own printer: kubectl
# label --local applies -o go-template to each item of a List offline, which is
# what `get resourceslices` does inside the template's range over .items. The
# fixture holds 3 devices a gpu.intel.com claim can get (pool-a gen 2 xe,
# pool-a gen 2 i915 with a None taint, pool-b xe) and 8 it cannot.
if type -P kubectl >/dev/null; then
    cat > "${WORK}/slices.json" <<'EOF'
{"apiVersion": "v1", "kind": "List", "items": [
 {"apiVersion": "resource.k8s.io/v1", "kind": "ResourceSlice", "metadata": {"name": "a-old"},
  "spec": {"driver": "gpu.intel.com", "nodeName": "n0", "pool": {"name": "pool-a", "generation": 1, "resourceSliceCount": 1},
   "devices": [{"name": "g0", "attributes": {"driver": {"string": "xe"}}}]}},
 {"apiVersion": "resource.k8s.io/v1", "kind": "ResourceSlice", "metadata": {"name": "a-new"},
  "spec": {"driver": "gpu.intel.com", "nodeName": "n0", "pool": {"name": "pool-a", "generation": 2, "resourceSliceCount": 1},
   "devices": [
    {"name": "g0", "attributes": {"driver": {"string": "xe"}, "model": {"string": "Arc B580"}}},
    {"name": "g1", "attributes": {"driver": {"string": "i915"}}, "taints": [{"key": "info", "effect": "None"}]},
    {"name": "g2", "attributes": {"driver": {"string": "xe"}}, "taints": [{"key": "unhealthy", "effect": "NoExecute"}]},
    {"name": "g3", "attributes": {"driver": {"string": "xe"}}, "taints": [{"key": "a", "effect": "None"}, {"key": "b", "effect": "NoSchedule"}]},
    {"name": "g4", "attributes": {"driver": {"string": "vfio-pci"}}},
    {"name": "g5", "attributes": {"model": {"string": "no driver attribute"}}},
    {"name": "g6"}]}},
 {"apiVersion": "resource.k8s.io/v1", "kind": "ResourceSlice", "metadata": {"name": "b"},
  "spec": {"driver": "gpu.intel.com", "nodeName": "n1", "pool": {"name": "pool-b", "generation": 7, "resourceSliceCount": 1},
   "devices": [{"name": "g0", "attributes": {"driver": {"string": "xe"}}}]}},
 {"apiVersion": "resource.k8s.io/v1", "kind": "ResourceSlice", "metadata": {"name": "other"},
  "spec": {"driver": "gpu.example.com", "nodeName": "n0", "pool": {"name": "pool-a", "generation": 9, "resourceSliceCount": 1},
   "devices": [{"name": "g0", "attributes": {"driver": {"string": "xe"}}}]}}
]}
EOF
    slice_got=$( # shellcheck source=scripts/k3d-gpu
        source "${K3D_GPU}" help >/dev/null
        KUBECONFIG=/dev/null kubectl label --local -f "${WORK}/slices.json" k3d-gpu.io/t=1 \
            -o go-template="$(dra_slice_template)" 2>&1 | dra_count_devices
        # and `up` hands kubectl the same template inside the range over .items
        grep -F -x "kubectl get resourceslices -o go-template={{range .items}}$(dra_slice_template){{end}}" \
            "${WORK}/dra-1.log" >/dev/null || echo "up-template-mismatch")
    if [ "${slice_got}" = 3 ]; then
        PASS=$((PASS + 1)); echo "ok   DRA slice template counts allocatable devices through kubectl's printer"
    else
        FAILS=$((FAILS + 1)); echo "FAIL DRA slice template counts allocatable devices through kubectl's printer (want 3, got '${slice_got}')"
    fi
else
    echo "skip DRA slice template through kubectl's printer (no kubectl on PATH)"
fi

# `sandbox` drives kubectl only. This stub logs every call to STUB_LOG and answers
# from STUB_HYBRID_NODES (nodes with the hybrid label), STUB_POOL_READY (warm pool
# readyReplicas), STUB_SANDBOX_PODS (running sandbox pods) and STUB_SANDBOX_CPUS
# (the Cpus_allowed_list a sandbox pod reports). A call whose arguments match the
# ERE in STUB_FAIL prints nothing and exits 1.
SSTUB="${WORK}/sbin"
mkdir -p "${SSTUB}"
cat > "${SSTUB}/kubectl" <<'EOF'
#!/bin/sh
echo "$*" >> "${STUB_LOG}"
if [ -n "${STUB_FAIL:-}" ] && echo "$*" | grep -Eq -- "${STUB_FAIL}"; then
    echo "error: stub failure" >&2
    exit 1
fi
case "$*" in
    *"get nodes -l"*) for n in ${STUB_HYBRID_NODES:-}; do echo "node/${n}"; done ;;
    *"get sandboxwarmpool "*) printf %s "${STUB_POOL_READY-1}" ;;
    *"get pods -l k3d-gpu.io/cores=efficient"*) for p in ${STUB_SANDBOX_PODS:-}; do echo "${p}"; done ;;
    *" exec "*) echo "Cpus_allowed_list:	${STUB_SANDBOX_CPUS:-}" ;;
esac
exit 0
EOF
chmod +x "${SSTUB}/kubectl"
SHARE="$(cd "$(dirname "$0")/.." && pwd)/share"

# sandbox_says <name> <root> <want rc: 0|fail> <ERE> <action> [negate] [env assignments...]:
# output of `k3d-gpu sandbox <action>` must match <ERE>, or must not after "negate".
# The kubectl call log is in ${WORK}/kubectl.log.
sandbox_says() {
    local name=$1 root=$2 want_rc=$3 re=$4 action=$5 negate="" out rc hit=0
    shift 5
    if [ "${1:-}" = negate ]; then negate=1; shift; fi
    : > "${WORK}/kubectl.log"
    out=$(env K3D_GPU_SYSROOT="${root}" K3D_GPU_SHARE="${SHARE}" STUB_LOG="${WORK}/kubectl.log" \
        PATH="${SSTUB}:${PATH}" "$@" bash "${K3D_GPU}" sandbox "${action}" 2>&1)
    rc=$?
    grep -E -- "${re}" <<< "${out}" >/dev/null && hit=1
    [ -z "${negate}" ] || hit=$((1 - hit))
    if [ "${hit}" -eq 1 ] \
       && { { [ "${want_rc}" = 0 ] && [ "${rc}" -eq 0 ]; } || { [ "${want_rc}" = fail ] && [ "${rc}" -ne 0 ]; }; }; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1))
        echo "FAIL ${name} (rc=${rc}, want ${want_rc}, ${negate:+not }/${re}/)"
        sed 's/^/  | /' <<< "${out}"
    fi
}

# log_order <name> <ERE> <ERE>...: each pattern matches a kubectl call, in this order.
log_order() {
    local name=$1 re line=0 n
    shift
    for re in "$@"; do
        n=$(grep -n -E -- "${re}" "${WORK}/kubectl.log" | awk -F: -v after="${line}" '$1 > after {print $1; exit}')
        if [ -z "${n}" ]; then
            FAILS=$((FAILS + 1)); echo "FAIL ${name} (no call /${re}/ after line ${line})"
            sed 's/^/  | /' "${WORK}/kubectl.log"
            return
        fi
        line=${n}
    done
    PASS=$((PASS + 1)); echo "ok   ${name}"
}

r=$(newroot sandbox-hybrid)
hybrid "${r}" 0-15 16-31
sandbox_says "sandbox up installs agent-sandbox and the E-core profile" "${r}" 0 'ok: python-ecores-abc12: CPUs 16-17' up \
    STUB_HYBRID_NODES=k3d-gpu-server-0 STUB_SANDBOX_PODS=python-ecores-abc12 STUB_SANDBOX_CPUS=16-17
log_order "sandbox up waits for the CRDs and controller before applying the profile" \
    'apply -f .*/agent-sandbox\.yml$' \
    'wait --for=condition=Established .*crd/sandboxtemplates\.extensions\.agents\.x-k8s\.io.*crd/sandboxwarmpools' \
    'rollout status deployment/agent-sandbox-controller' \
    'apply -f .*/agent-sandbox-k3d\.yml$' \
    'get sandboxwarmpool python-ecores'
sandbox_says "sandbox up on a hybrid node does not warn about the label" "${r}" 0 'does nothing here' up negate \
    STUB_HYBRID_NODES=k3d-gpu-server-0 STUB_SANDBOX_PODS=p1 STUB_SANDBOX_CPUS=16
sandbox_says "sandbox up warns when no node carries the hybrid label" "${r}" 0 'WARN.*hybrid-cpu=true.*does nothing here' up \
    STUB_SANDBOX_PODS=p1 STUB_SANDBOX_CPUS=16
sandbox_says "sandbox status passes when every sandbox runs on E-cores" "${r}" 0 'ok: a: CPUs 20,24-25' status \
    STUB_SANDBOX_PODS="a b" STUB_SANDBOX_CPUS=20,24-25
sandbox_says "sandbox status fails when a sandbox runs on P-cores" "${r}" fail "CPUs '3' are not within the E-cores 16-31" status \
    STUB_SANDBOX_PODS=a STUB_SANDBOX_CPUS=3
sandbox_says "sandbox status fails when a sandbox spans P- and E-cores (no pinning)" "${r}" fail 'not pinned to E-cores' status \
    STUB_SANDBOX_PODS=a STUB_SANDBOX_CPUS=0-31
sandbox_says "sandbox status fails with no running sandbox pods" "${r}" fail 'no running sandbox pods' status
sandbox_says "sandbox up fails when the warm pool never gets ready" "${r}" fail 'no ready sandbox after 1s' up \
    STUB_POOL_READY=0 K3D_GPU_SANDBOX_TIMEOUT=1
sandbox_says "sandbox status fails on a hybrid host when a sandbox reports no CPU list" "${r}" fail "CPUs '' are not within the E-cores 16-31" status \
    STUB_SANDBOX_PODS=a STUB_FAIL=' exec '

# A failing kubectl step stops `sandbox up` before the next one runs.
# no_call <name> <ERE>: no kubectl call in the log matches <ERE>.
no_call() {
    if grep -E -- "$2" "${WORK}/kubectl.log" >/dev/null; then
        FAILS=$((FAILS + 1)); echo "FAIL $1 (unexpected call /$2/)"
        sed 's/^/  | /' "${WORK}/kubectl.log"
    else
        PASS=$((PASS + 1)); echo "ok   $1"
    fi
}
sandbox_says "sandbox up fails when the controller manifest does not apply" "${r}" fail 'stub failure' up \
    STUB_FAIL='apply -f .*/agent-sandbox\.yml$'
no_call "sandbox up stops after a failed controller apply" 'wait --for|rollout|agent-sandbox-k3d'
sandbox_says "sandbox up fails when the CRDs never become Established" "${r}" fail 'stub failure' up \
    STUB_FAIL='wait --for=condition=Established'
no_call "sandbox up does not apply the profile without its CRDs" 'rollout|agent-sandbox-k3d'
sandbox_says "sandbox up fails when the controller rollout fails" "${r}" fail 'stub failure' up \
    STUB_FAIL='rollout status'
no_call "sandbox up does not apply the profile without a controller" 'agent-sandbox-k3d'
sandbox_says "sandbox up fails when the profile does not apply" "${r}" fail 'stub failure' up \
    STUB_FAIL='apply -f .*/agent-sandbox-k3d\.yml$'
no_call "sandbox up does not wait on a warm pool it failed to create" 'get sandboxwarmpool'

r=$(newroot sandbox-flat)
sandbox_says "sandbox status on a non-hybrid host reports CPUs without judging them" "${r}" 0 'a: CPUs 0-7 \(not a hybrid host' status \
    STUB_SANDBOX_PODS=a STUB_SANDBOX_CPUS=0-7
sandbox_says "sandbox up fails early when the manifest is missing" "${r}" fail 'not found' up \
    K3D_GPU_SANDBOX=/nonexistent/agent-sandbox.yml
if grep -q apply "${WORK}/kubectl.log"; then
    FAILS=$((FAILS + 1)); echo "FAIL sandbox up with a missing manifest applies nothing"
else
    PASS=$((PASS + 1)); echo "ok   sandbox up with a missing manifest applies nothing"
fi
sandbox_says "sandbox down removes the profile, then agent-sandbox" "${r}" 0 'Removing' down
log_order "sandbox down deletes the profile before the CRDs" \
    'delete -f .*/agent-sandbox-k3d\.yml --ignore-not-found' 'delete -f .*/agent-sandbox\.yml --ignore-not-found'
sandbox_says "sandbox with no action prints usage and fails" "${r}" fail 'usage: k3d-gpu sandbox up\|down\|status' ""

# The shipped profile: sandboxes are labelled for E-cores, request CPU (requests size
# the balloon) and every image is pinned by digest.
manifest_has() {
    if grep -E -- "$2" "${SHARE}/$3" >/dev/null; then
        PASS=$((PASS + 1)); echo "ok   $1"
    else
        FAILS=$((FAILS + 1)); echo "FAIL $1 (/$2/ not in share/$3)"
    fi
}
manifest_has "sandbox profile labels pods k3d-gpu.io/cores=efficient" '^        k3d-gpu.io/cores: efficient$' agent-sandbox-k3d.yml
manifest_has "sandbox profile requests a whole CPU" '^            cpu: "1"$' agent-sandbox-k3d.yml
# Egress must skip CGNAT 100.64.0.0/10 too: tailscale gives the host and every tailnet
# peer an address there, which the 0.0.0.0/0 rule would otherwise allow.
manifest_has "sandbox egress blocks CGNAT 100.64.0.0/10 (tailscale)" '^          - 100\.64\.0\.0/10$' agent-sandbox-k3d.yml
# The image's user 1000 has no passwd entry, so HOME=/ and pip's user install fails
# on /.local; HOME=/tmp gives pip a writable user site.
if grep -A1 -- '^        - name: HOME$' "${SHARE}/agent-sandbox-k3d.yml" | grep -qx -- '          value: /tmp'; then
    PASS=$((PASS + 1)); echo "ok   sandbox profile sets a writable HOME for pip"
else
    FAILS=$((FAILS + 1)); echo "FAIL sandbox profile sets a writable HOME for pip (no 'name: HOME' followed by 'value: /tmp')"
fi
manifest_has "sandbox runtime image pinned by digest" 'image: registry.k8s.io/agent-sandbox/python-runtime-sandbox:v1.0.7@sha256:[0-9a-f]{64}$' agent-sandbox-k3d.yml
manifest_has "agent-sandbox controller image pinned by digest" 'image: registry.k8s.io/agent-sandbox/agent-sandbox-controller:v1.0.7@sha256:[0-9a-f]{64}$' agent-sandbox.yml
if grep -E 'image: ' "${SHARE}/agent-sandbox.yml" "${SHARE}/agent-sandbox-k3d.yml" | grep -v '@sha256:' | grep -v ':[[:space:]]*$' >/dev/null; then
    FAILS=$((FAILS + 1)); echo "FAIL every agent-sandbox image is pinned by digest"
else
    PASS=$((PASS + 1)); echo "ok   every agent-sandbox image is pinned by digest"
fi

# NPU firmware names follow the running kernel (ivpu_fw.c fw_names[] per tag).
r=$(newroot npu-k68-v1-only)
kernel "${r}" 6.8.0-45-generic
pci "${r}" 0000:00:0b.0 0x8086 0x643e intel_vpu; accel "${r}" 0000:00:0b.0 0
firmware "${r}" intel/vpu/vpu_40xx_v1.bin
doctor_says "kernel 6.8 never loads vpu_40xx_v1.bin" "${r}" \
    'warn.*vpu_40xx_\* firmware not found; kernel 6\.8\.0-45-generic loads vpu_40xx\.bin,intel/vpu/vpu_40xx_v0\.0\.bin'
r=$(newroot npu-k68-root-name)
kernel "${r}" 6.8.0-45-generic
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d intel_vpu; accel "${r}" 0000:00:0b.0 0
firmware "${r}" mtl_vpu.bin
doctor_says "kernel 6.8 loads mtl_vpu.bin for 37xx" "${r}" 'present \(/lib/firmware/mtl_vpu\.bin\)'
r=$(newroot npu-k612-no-mtl-name)
kernel "${r}" 6.12.48
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d intel_vpu; accel "${r}" 0000:00:0b.0 0
firmware "${r}" mtl_vpu.bin
doctor_says "kernel 6.12 no longer loads mtl_vpu.bin" "${r}" 'warn.*vpu_37xx_\* firmware not found'
r=$(newroot npu-k65)
kernel "${r}" 6.5.0
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d intel_vpu; accel "${r}" 0000:00:0b.0 0
firmware "${r}" intel/vpu/mtl_vpu_v0.0.bin
doctor_says "kernel 6.5 loads intel/vpu/mtl_vpu_v0.0.bin" "${r}" 'present \(/lib/firmware/intel/vpu/mtl_vpu_v0\.0\.bin\)'

# intel-fw-npu's updates/.../vpu_50xx_v1.bin is not what 6.13-6.15 loads.
r=$(newroot npu-ptl-k614-fwnpu)
kernel "${r}" 6.14.0-33-generic
pci "${r}" 0000:00:0b.0 0x8086 0xb03e intel_vpu; accel "${r}" 0000:00:0b.0 0
firmware "${r}" intel/vpu/vpu_50xx_v0.0.bin
firmware "${r}" updates/intel/vpu/vpu_50xx_v1.bin
doctor_says "PTL on 6.14 dates linux-firmware's vpu_50xx_v0.0.bin" "${r}" 'present \(/lib/firmware/intel/vpu/vpu_50xx_v0\.0\.bin\)'
doctor_says "PTL on 6.14 with intel-fw-npu suggests the v0.0 link" "${r}" \
    'run: +sudo ln -s vpu_50xx_v1\.bin /lib/firmware/updates/intel/vpu/vpu_50xx_v0\.0\.bin'
doctor_says "PTL on 6.14 with intel-fw-npu gives the revert" "${r}" \
    'revert: sudo rm /lib/firmware/updates/intel/vpu/vpu_50xx_v0\.0\.bin'
r=$(newroot npu-ptl-k616-fwnpu)
kernel "${r}" 6.16.3
pci "${r}" 0000:00:0b.0 0x8086 0xb03e intel_vpu; accel "${r}" 0000:00:0b.0 0
firmware "${r}" updates/intel/vpu/vpu_50xx_v1.bin
doctor_says "PTL on 6.16 loads intel-fw-npu's v1 with no link hint" "${r}" 'sudo ln -s' negate

# 60xx: no firmware is published; say so rather than "install intel-fw-npu".
r=$(newroot npu-nvl-param)
kernel "${r}" 6.19.0
pci "${r}" 0000:00:0b.0 0x8086 0xd71d intel_vpu; accel "${r}" 0000:00:0b.0 0
doctor_says "bound Nova Lake NPU without firmware says none is published" "${r}" 'no published NPU firmware for 60xx'
doctor_says "Nova Lake NPU is not told to install intel-fw-npu" "${r}" 'install linux-firmware or intel-fw-npu' negate
r=$(newroot npu-nvl-unbound)
kernel "${r}" 6.19.0
pci "${r}" 0000:00:0b.0 0x8086 0xd71d "" 0x120000
mkdir -p "${r}/sys/module/intel_vpu"
doctor_says "unbound Nova Lake NPU with intel_vpu loaded: no published 60xx firmware" "${r}" \
    'Nova Lake \(0xd71d\) has no driver.*no published NPU firmware for 60xx'

# Unbound NPU: first cause wins (kernel, module, firmware).
r=$(newroot npu-lnl-nomod)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:0b.0 0x8086 0x643e "" 0x120000
doctor_says "unbound NPU with intel_vpu not loaded gives modprobe and its revert" "${r}" \
    'sudo modprobe intel_vpu \(revert: sudo modprobe -r intel_vpu\)'
r=$(newroot npu-lnl-nofw)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:0b.0 0x8086 0x643e "" 0x120000
mkdir -p "${r}/sys/module/intel_vpu"
doctor_says "unbound NPU with intel_vpu loaded and no firmware names the firmware" "${r}" \
    'found no 40xx firmware \(intel/vpu/vpu_40xx_v1\.bin,intel/vpu/vpu_40xx_v0\.0\.bin\)'

# Plugin coverage: an intel_vpu id the pinned plugin does not list.
r=$(newroot npu-unknown-id)
kernel "${r}" 7.3.0
pci "${r}" 0000:00:0b.0 0x8086 0x1234 intel_vpu; accel "${r}" 0000:00:0b.0 0
doctor_says "NPU id outside the plugin's npuIDs is flagged" "${r}" 'warn.*intel-npu-plugin [0-9.]+ does not list 0x1234'
r=$(newroot npu-known-id)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:0b.0 0x8086 0xb03e intel_vpu; accel "${r}" 0000:00:0b.0 0
doctor_says "PTL NPU is served by the pinned plugin" "${r}" 'does not list' negate
# The id list is keyed to the plugin tag share/intel-npu-plugin.yml pins.
tag=$(sed -n 's/^NPU_PLUGIN_TAG="\(.*\)"$/\1/p' "${K3D_GPU}")
if [ -n "${tag}" ] && grep -qE "image: intel/intel-npu-plugin:${tag//./\\.}@sha256:[0-9a-f]{64}\$" "$(dirname "${K3D_GPU}")/../share/intel-npu-plugin.yml"; then
    PASS=$((PASS + 1)); echo "ok   NPU_PLUGIN_TAG matches the pinned intel-npu-plugin image"
else
    FAILS=$((FAILS + 1)); echo "FAIL NPU_PLUGIN_TAG (${tag}) matches the pinned intel-npu-plugin image"
fi

# Auto detection skips an NPU the bundled plugin does not serve; flags override.
r=$(newroot npu-unserved-detect)
kernel "${r}" 7.3.0
pci "${r}" 0000:00:0b.0 0x8086 0x1234 intel_vpu; accel "${r}" 0000:00:0b.0 0
expect "auto detection skips an NPU the bundled plugin does not serve" "${r}" ""
expect "K3D_GPU_NPU_PLUGIN makes an unlisted NPU id active" "${r}" "npu npu.intel.com/accel" \
    K3D_GPU_NPU_PLUGIN=/opt/newer-npu-plugin.yml
expect "K3D_GPU_VENDORS=npu forces an unlisted NPU id active" "${r}" "npu npu.intel.com/accel" K3D_GPU_VENDORS=npu
doctor_says "doctor explains an NPU auto detection skipped" "${r}" 'warn.*intel-npu-plugin [0-9.]+ does not list 0x1234'
doctor_says "K3D_GPU_NPU_PLUGIN skips the bundled id check" "${r}" 'does not list' negate \
    K3D_GPU_NPU_PLUGIN=/opt/newer-npu-plugin.yml
doctor_says "K3D_GPU_NPU_PLUGIN gets a note instead" "${r}" 'note: K3D_GPU_NPU_PLUGIN=/opt/newer-npu-plugin\.yml replaces the bundled plugin' "" \
    K3D_GPU_NPU_PLUGIN=/opt/newer-npu-plugin.yml
# The stub cluster advertises no npu.intel.com/accel, so up exits non-zero after
# k3d create; the recorded create arguments are what these check. Later sections
# replace the up_says stubs, so put back the k3d one that records its arguments
# and a kubectl that accepts everything.
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
up_says "up labels an unlisted NPU when K3D_GPU_VENDORS=npu forces it" fail \
    '^--k3s-node-label=intel\.feature\.node\.kubernetes\.io/npu=true' "" K3D_GPU_SYSROOT="${r}" K3D_GPU_VENDORS=npu
up_says "up labels an unlisted NPU under K3D_GPU_NPU_PLUGIN" fail \
    '^--k3s-node-label=intel\.feature\.node\.kubernetes\.io/npu=true' "" K3D_GPU_SYSROOT="${r}" K3D_GPU_VENDORS=auto \
    K3D_GPU_NPU_PLUGIN=/opt/newer-npu-plugin.yml
r=$(newroot npu-unreadable-id)
kernel "${r}" 6.18.1
pci "${r}" 0000:00:0b.0 0x8086 0x643e intel_vpu; accel "${r}" 0000:00:0b.0 0
: > "${r}/sys/devices/pci0000:00/0000:00:0b.0/device"
doctor_says "unreadable NPU device id is named as such" "${r}" 'its PCI device id is unreadable'
doctor_says "unreadable NPU device id is not called unserved" "${r}" 'does not list' negate

# A kernel older than the NPU's support gets no firmware-name advice.
r=$(newroot npu-nvl-k614)
kernel "${r}" 6.14.0
pci "${r}" 0000:00:0b.0 0x8086 0xd71d intel_vpu; accel "${r}" 0000:00:0b.0 0
doctor_says "Nova Lake on 6.14 says the kernel is too old" "${r}" 'warn.*Nova Lake NPU needs kernel 6\.19\+'
doctor_says "Nova Lake on 6.14 skips the 60xx firmware message" "${r}" 'no published NPU firmware' negate

# Unreadable kernel version: no kernel cause, no version-specific name list.
r=$(newroot npu-ptl-nokernel)
kernel "${r}" ""
pci "${r}" 0000:00:0b.0 0x8086 0xb03e intel_vpu; accel "${r}" 0000:00:0b.0 0
doctor_says "empty osrelease is reported as unreadable" "${r}" 'kernel version unreadable'
doctor_says "empty osrelease does not list pre-6.6 mtl names for PTL" "${r}" 'mtl_vpu' negate
r=$(newroot npu-ptl-nokernel-unbound)
kernel "${r}" ""
pci "${r}" 0000:00:0b.0 0x8086 0xb03e "" 0x120000
doctor_says "empty osrelease: unbound NPU is not blamed on the kernel" "${r}" 'predates its intel_vpu support' negate
doctor_says "empty osrelease: unbound NPU gets the modprobe hint" "${r}" 'sudo modprobe intel_vpu'

# The intel-fw-npu v0.0 link is suggested only when it changes the loaded file.
# fwnpu_ptl <name> <kernel>: PTL NPU with intel-fw-npu's updates/.../vpu_50xx_v1.bin.
fwnpu_ptl() {
    local r
    r=$(newroot "$1"); kernel "${r}" "$2"
    pci "${r}" 0000:00:0b.0 0x8086 0xb03e intel_vpu; accel "${r}" 0000:00:0b.0 0
    firmware "${r}" updates/intel/vpu/vpu_50xx_v1.bin
    echo "${r}"
}
r=$(fwnpu_ptl npu-fwnpu-root-name 6.14.0)
firmware "${r}" vpu_50xx.bin
doctor_says "root-level vpu_50xx.bin wins: no v0.0 link advice" "${r}" 'sudo ln -s' negate
r=$(fwnpu_ptl npu-fwnpu-param 6.14.0)
firmware "${r}" intel/vpu/vpu_50xx_v0.0.bin
put "${r}" /sys/module/intel_vpu/parameters/firmware my_vpu.bin
firmware "${r}" my_vpu.bin
doctor_says "firmware= parameter set: no v0.0 link advice" "${r}" 'sudo ln -s' negate
r=$(newroot npu-fwnpu-k65)
kernel "${r}" 6.5.0
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d intel_vpu; accel "${r}" 0000:00:0b.0 0
firmware "${r}" intel/vpu/mtl_vpu_v0.0.bin
firmware "${r}" updates/intel/vpu/vpu_37xx_v1.bin
doctor_says "kernel 6.5 requests mtl names: no v0.0 link advice" "${r}" 'sudo ln -s' negate
r=$(fwnpu_ptl npu-fwnpu-custom 6.14.0)
mkdir -p "${r}/opt/fw/intel/vpu"; touch "${r}/opt/fw/intel/vpu/vpu_50xx_v0.0.bin"
put "${r}" /sys/module/firmware_class/parameters/path /opt/fw
doctor_says "firmware_class.path copy wins: no v0.0 link advice" "${r}" 'sudo ln -s' negate
r=$(fwnpu_ptl npu-fwnpu-dangling 6.14.0)
firmware "${r}" intel/vpu/vpu_50xx_v0.0.bin
ln -s vpu_50xx_v9.bin "${r}/lib/firmware/updates/intel/vpu/vpu_50xx_v0.0.bin"
doctor_says "dangling v0.0 link: advice uses ln -sfn" "${r}" \
    'run: +sudo ln -sfn vpu_50xx_v1\.bin /lib/firmware/updates/intel/vpu/vpu_50xx_v0\.0\.bin'
doctor_says "dangling v0.0 link: revert restores the old target" "${r}" \
    'revert: sudo ln -sfn vpu_50xx_v9\.bin /lib/firmware/updates/intel/vpu/vpu_50xx_v0\.0\.bin'
doctor_says "dangling v0.0 link: no plain ln -s" "${r}" 'sudo ln -s vpu' negate
r=$(newroot npu-fwnpu-zst)
kernel "${r}" 6.14.0
pci "${r}" 0000:00:0b.0 0x8086 0xb03e intel_vpu; accel "${r}" 0000:00:0b.0 0
firmware "${r}" intel/vpu/vpu_50xx_v0.0.bin.zst
firmware "${r}" updates/intel/vpu/vpu_50xx_v1.bin.zst
doctor_says "compressed intel-fw-npu v1 gets a .zst link" "${r}" \
    'run: +sudo ln -s vpu_50xx_v1\.bin\.zst /lib/firmware/updates/intel/vpu/vpu_50xx_v0\.0\.bin\.zst'
r=$(newroot npu-fwnpu-zst-late)
kernel "${r}" 6.14.0
pci "${r}" 0000:00:0b.0 0x8086 0xb03e intel_vpu; accel "${r}" 0000:00:0b.0 0
firmware "${r}" intel/vpu/vpu_50xx_v0.0.bin
firmware "${r}" updates/intel/vpu/vpu_50xx_v1.bin.zst
doctor_says "a .zst link loses to an uncompressed v0.0: no advice" "${r}" 'sudo ln -s' negate
r=$(fwnpu_ptl npu-fwnpu-stop 6.14.0)
firmware "${r}" intel/vpu/vpu_50xx_v0.0.bin
doctor_says "v0.0 link advice says to stop NPU users first" "${r}" "Stop what uses the NPU first \('k3d-gpu down'"
# capacity: dispatch and locating bench/apb/capacity.py (the ramp itself is
# tested by bench/apb/test_capacity.py).
# says <name> <want rc: 0|N> <ERE> <cmd...>: output must match and rc must equal want.
says() {
    local name=$1 want_rc=$2 re=$3 out rc
    shift 3
    out=$("$@" 2>&1)
    rc=$?
    if [ "${rc}" -eq "${want_rc}" ] && grep -E -- "${re}" <<< "${out}" >/dev/null; then
        PASS=$((PASS + 1)); echo "ok   ${name}"
    else
        FAILS=$((FAILS + 1)); echo "FAIL ${name} (rc=${rc}, want ${want_rc}, /${re}/)"
        sed 's/^/  | /' <<< "${out}"
    fi
}
REPO=$(cd "$(dirname "${K3D_GPU}")/.." && pwd)
says "help lists capacity" 0 '^  capacity \[--slo 10\]' bash "${K3D_GPU}" help
says "help lists share" 0 '^  share \[--capacity FILE\]' bash "${K3D_GPU}" help
says "share --help comes from the repo's oracle/share.py" 0 'There is no --yes' \
    bash "${K3D_GPU}" share --help
says "capacity --help comes from the repo's bench/apb/capacity.py" 0 'Agents per box at a p95' \
    bash "${K3D_GPU}" capacity --help
says "capacity passes its arguments through (--url needs --model)" 2 '--url needs --model' \
    bash "${K3D_GPU}" capacity --url http://127.0.0.1:1/v1/chat/completions
SOLO="${WORK}/solo"
mkdir -p "${SOLO}/bin" "${SOLO}/share/bench/apb"
cp "${K3D_GPU}" "${SOLO}/bin/k3d-gpu"
says "capacity outside a checkout with no packaged copy says what is missing" 1 'capacity.py not found' \
    env K3D_GPU_SHARE="${SOLO}/none" bash "${SOLO}/bin/k3d-gpu" capacity --help
cp "${REPO}/bench/agent_trace.py" "${REPO}/bench/agent_trace_gen.py" "${SOLO}/share/bench/"
cp "${REPO}/bench/apb/apb.py" "${REPO}/bench/apb/capacity.py" "${SOLO}/share/bench/apb/"
says "capacity runs from the packaged copy under K3D_GPU_SHARE/bench" 0 'Agents per box at a p95' \
    env K3D_GPU_SHARE="${SOLO}/share" bash "${SOLO}/bin/k3d-gpu" capacity --help
says "share outside a checkout with no packaged copy says what is missing" 1 'share.py not found' \
    env K3D_GPU_SHARE="${SOLO}/none" bash "${SOLO}/bin/k3d-gpu" share --help
# The files the packages install under /usr/share/k3d-gpu/oracle.
mkdir -p "${SOLO}/share/oracle/schema"
for f in bundle.py project.py validate.py share.py schema/bundle-v1.json; do cp "${REPO}/oracle/${f}" "${SOLO}/share/oracle/${f}"; done
says "share runs from the packaged copy under K3D_GPU_SHARE/oracle" 0 'There is no --yes' \
    env K3D_GPU_SHARE="${SOLO}/share" bash "${SOLO}/bin/k3d-gpu" share --help
says "packaged share finds no capacity run and says so" 2 'no finished capacity run' \
    env K3D_GPU_SHARE="${SOLO}/share" XDG_STATE_HOME="${SOLO}/state" K3D_GPU_SYSROOT="${SOLO}/root" bash "${SOLO}/bin/k3d-gpu" share --dry-run

# The node image is published for linux/amd64 only (build-and-push.yml platforms), so
# `up` on another CPU stops before docker runs unless K3D_GPU_IMAGE names an image for it.
ARCH_BIN="${WORK}/arch-bin"
mkdir -p "${ARCH_BIN}"
printf '#!/bin/sh\n[ "$1" = -m ] && { echo aarch64; exit 0; }\nexec /usr/bin/env -i uname "$@"\n' > "${ARCH_BIN}/uname"
for t in docker k3d kubectl; do
    printf '#!/bin/sh\necho "%s $*" >> "%s/arch-calls"\nexit 1\n' "${t}" "${WORK}" > "${ARCH_BIN}/${t}"
done
chmod +x "${ARCH_BIN}"/*
ARCH_ROOT=$(newroot arch-empty)
says "up on aarch64 with the default node image names the amd64-only image" 1 'published for linux/amd64 only.*K3D_GPU_IMAGE' \
    env -u K3D_GPU_IMAGE PATH="${ARCH_BIN}:${PATH}" K3D_GPU_SYSROOT="${ARCH_ROOT}" bash "${K3D_GPU}" up
says "up on aarch64 stops before docker runs" 0 '^$' bash -c "cat '${WORK}/arch-calls' 2>/dev/null; true"
says "up on aarch64 with K3D_GPU_IMAGE set passes the arch check" 1 'no supported accelerator' \
    env K3D_GPU_IMAGE=example/k3s-arm64:1 PATH="${ARCH_BIN}:${PATH}" K3D_GPU_SYSROOT="${ARCH_ROOT}" bash "${K3D_GPU}" up
echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
