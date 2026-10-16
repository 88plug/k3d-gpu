#!/usr/bin/env bash
# Capture the parts of a host's /sys, /dev, /proc and firmware tree that k3d-gpu
# reads into a fake sysroot, for tests/sim_matrix_test.sh. Symlinks keep their
# exact relative targets, sysfs attribute files keep their contents, device nodes
# become empty files, and empty directories get a .keep so git stores them.
#
# Usage: tests/fixtures/capture.sh <out-dir> [source-root]
#   <out-dir>      fixture sysroot to write (must not exist)
#   [source-root]  tree to read from (default: the running host, /)
#
# Reads only vendor/device/class ids, driver bindings, device node names, GPU
# engine names and capabilities, NUMA node CPU lists, socket ids and PCI NUMA
# nodes, CPU 0's L3 sharing list, IMC event strings, the CPU family, model and
# flags, the kernel release and firmware file names. No serial numbers, MACs or
# firmware contents are copied.

set -euo pipefail

OUT=${1:?usage: capture.sh <out-dir> [source-root]}
SRC=${2:-}
SRC=${SRC%/}
[ ! -e "${OUT}" ] || { echo "capture.sh: ${OUT} already exists" >&2; exit 1; }
mkdir -p "${OUT}"
OUT=$(cd "${OUT}" && pwd)
ROOT=$(cd "${SRC:-/}" && pwd -P)

# rel <path>: path inside the source root, without the root prefix.
rel() {
    local p
    p=$(readlink -f "$1")
    if [ "${p}" = "${ROOT}" ]; then echo .; else echo "${p#"${ROOT%/}"/}"; fi
}

# link <path>: recreate the symlink at <path> with the same target text.
link() {
    local r
    r="$(rel "$(dirname "$1")")/$(basename "$1")"
    mkdir -p "$(dirname "${OUT}/${r}")"
    ln -sfn "$(readlink "$1")" "${OUT}/${r}"
}

# dir <path>: the directory a path resolves to, created in the copy.
dir() { mkdir -p "${OUT}/$(rel "$1")"; }

# attr <dir> <name>...: copy small sysfs attribute files that exist.
attr() {
    local d=$1 n
    shift
    for n in "$@"; do
        [ -r "${d}/${n}" ] && [ ! -d "${d}/${n}" ] || continue
        cat "${d}/${n}" > "${OUT}/$(rel "${d}")/${n}"
    done
}

# PCI functions: ids, class, bound driver, SR-IOV VFs.
for dev in "${SRC}"/sys/bus/pci/devices/*; do
    [ -e "${dev}" ] || continue
    link "${dev}"
    dir "${dev}"
    attr "${dev}" vendor device class sriov_numvfs
    if [ -L "${dev}/driver" ]; then
        link "${dev}/driver"
        if [ -d "${dev}/driver" ]; then dir "${dev}/driver"; fi
    fi
    for vf in "${dev}"/virtfn*; do
        if [ -L "${vf}" ]; then link "${vf}"; fi
    done
done
# vfio-pci loaded with nothing bound yet still has a driver directory.
if [ -d "${SRC}/sys/bus/pci/drivers/vfio-pci" ]; then mkdir -p "${OUT}/sys/bus/pci/drivers/vfio-pci"; fi

# DRM and accel class nodes and their device links.
for node in "${SRC}"/sys/class/drm/* "${SRC}"/sys/class/accel/*; do
    [[ "$(basename "${node}")" =~ ^(card|renderD|accel|accel_controlD)[0-9]+$ ]] || continue
    link "${node}"
    dir "${node}"
    if [ -L "${node}/device" ]; then link "${node}/device"; fi
    # i915 lists each GPU engine and its capabilities under cardN/engine.
    for eng in "${node}"/engine/*; do
        [ -d "${eng}" ] || continue
        dir "${eng}"
        attr "${eng}" capabilities
    done
done

# DSA/IAA devices and work queues.
for node in "${SRC}"/sys/bus/dsa/devices/*; do
    [ -L "${node}" ] || continue
    link "${node}"
    dir "${node}"
done

# Loaded modules k3d-gpu looks for.
for m in nvidia habanalabs intel_vpu xe i915 idxd; do
    [ -d "${SRC}/sys/module/${m}" ] || continue
    mkdir -p "${OUT}/sys/module/${m}"
    attr "${SRC}/sys/module/${m}" initstate
done

# Hybrid CPU PMUs.
for pmu in cpu_core cpu_atom; do
    [ -d "${SRC}/sys/devices/${pmu}" ] || continue
    mkdir -p "${OUT}/sys/devices/${pmu}"
    attr "${SRC}/sys/devices/${pmu}" cpus
done

# First CPU's family, model, model name and flags, and the kernel release.
if [ -r "${SRC}/proc/cpuinfo" ]; then
    mkdir -p "${OUT}/proc"
    {
        printf 'processor\t: 0\n'
        grep -m1 '^cpu family[[:space:]]*:' "${SRC}/proc/cpuinfo" || true
        grep -m1 '^model[[:space:]]*:' "${SRC}/proc/cpuinfo" || true
        grep -m1 '^model name' "${SRC}/proc/cpuinfo" || true
        grep -m1 '^flags' "${SRC}/proc/cpuinfo" || true
    } > "${OUT}/proc/cpuinfo"
fi
if [ -r "${SRC}/proc/sys/kernel/osrelease" ]; then
    mkdir -p "${OUT}/proc/sys/kernel"
    cat "${SRC}/proc/sys/kernel/osrelease" > "${OUT}/proc/sys/kernel/osrelease"
fi

# Device nodes as empty files; /dev/char links only for DSA/IAA work queues.
for node in "${SRC}"/dev/dri/card[0-9]* "${SRC}"/dev/dri/renderD* "${SRC}"/dev/accel/* \
            "${SRC}"/dev/dsa/wq* "${SRC}"/dev/iax/wq* "${SRC}"/dev/nvidia[0-9]* "${SRC}"/dev/nvidiactl \
            "${SRC}"/dev/hl[0-9]*; do
    [ -e "${node}" ] || continue
    mkdir -p "$(dirname "${OUT}/${node#"${SRC}"/}")"
    : > "${OUT}/${node#"${SRC}"/}"
done
for node in "${SRC}"/dev/char/*; do
    [ -L "${node}" ] || continue
    case "$(readlink "${node}")" in ../dsa/*|../iax/*) link "${node}" ;; esac
done

# NUMA: each node's CPU list, the socket of its first CPU, CPU 0's L3 sharing list
# (SNC = L3 CPUs / node CPUs) and each PCI function's node; the On Demand (SDSi)
# interface; the IMC CAS-count events.
for node in "${SRC}"/sys/devices/system/node/node[0-9]*; do
    [ -d "${node}" ] || continue
    dir "${node}"
    attr "${node}" cpulist
    first=$(head -n1 "${node}/cpulist" 2>/dev/null || true)
    first=${first%%[-,]*}
    if [ -n "${first}" ] && [ -d "${SRC}/sys/devices/system/cpu/cpu${first}/topology" ]; then
        dir "${SRC}/sys/devices/system/cpu/cpu${first}/topology"
        attr "${SRC}/sys/devices/system/cpu/cpu${first}/topology" physical_package_id
    fi
done
if [ -d "${SRC}/sys/devices/system/cpu/cpu0/cache/index3" ]; then
    dir "${SRC}/sys/devices/system/cpu/cpu0/cache/index3"
    attr "${SRC}/sys/devices/system/cpu/cpu0/cache/index3" shared_cpu_list
fi
for dev in "${SRC}"/sys/bus/pci/devices/*; do
    if [ -e "${dev}/numa_node" ]; then attr "${dev}" numa_node; fi
done
for aux in "${SRC}"/sys/bus/auxiliary/devices/intel_vsec.sdsi.*; do
    [ -L "${aux}" ] || continue
    link "${aux}"
    dir "${aux}"
done
for pmu in "${SRC}"/sys/bus/event_source/devices/uncore_imc_[0-9]*; do
    [ -r "${pmu}/events/cas_count_read" ] || continue
    link "${pmu}"
    dir "${pmu}/events"
    attr "${pmu}/events" cas_count_read cas_count_read.scale cas_count_read.unit \
        cas_count_write cas_count_write.scale cas_count_write.unit
done

# GPU GuC and NPU firmware file names (empty files). A merged /lib -> usr/lib stays a link.
for base in lib usr/lib; do
    if [ -L "${SRC}/${base}" ]; then
        link "${SRC}/${base}"
        continue
    fi
    for fw in "${SRC}/${base}"/firmware/i915/*_guc_* "${SRC}/${base}"/firmware/xe/*_guc_* \
              "${SRC}/${base}"/firmware/intel/vpu/vpu_*; do
        [ -e "${fw}" ] || continue
        mkdir -p "$(dirname "${OUT}/${fw#"${SRC}"/}")"
        : > "${OUT}/${fw#"${SRC}"/}"
    done
done

find "${OUT}" -type d -empty -exec touch {}/.keep \;
echo "captured $(find "${OUT}" \( -type f -o -type l \) | wc -l) entries into ${OUT}"
