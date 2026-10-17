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
# flags, the kernel release and firmware file names, plus the tuning, thermal,
# memory, PCIe-link, IOMMU and isolation state `doctor --perf` reads. No serial
# numbers, MACs or firmware contents are copied: SMBIOS memory records keep their
# numeric fields only. Run as root to include the root-only inputs (CPU 0 MSRs,
# SMBIOS type 17); /proc/1/mounts gives the host's root mount even from a
# container with the host tree mounted as <source-root>.

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

# attr <dir> <name>...: copy small sysfs attribute files that exist. A file the
# kernel refuses to read (EIO, ENODATA) is skipped.
attr() {
    local d=$1 n o
    shift
    for n in "$@"; do
        [ -r "${d}/${n}" ] && [ ! -d "${d}/${n}" ] || continue
        o="${OUT}/$(rel "${d}")/${n}"
        cat "${d}/${n}" > "${o}" 2>/dev/null || rm -f "${o}"
    done
}

# PCI functions: ids, class, bound driver, SR-IOV VFs, NPU scheduler mode.
for dev in "${SRC}"/sys/bus/pci/devices/*; do
    [ -e "${dev}" ] || continue
    link "${dev}"
    dir "${dev}"
    attr "${dev}" vendor device class sriov_numvfs sriov_totalvfs sched_mode max_link_speed max_link_width \
         resource resource{0..5}_resize tcc_offset_degree_celsius
    if [ -L "${dev}/iommu_group" ]; then link "${dev}/iommu_group"; fi
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

# sched_ext state (Linux 6.12+), read by the scx experimental feature.
if [ -r "${SRC}/sys/kernel/sched_ext/state" ]; then
    mkdir -p "${OUT}/sys/kernel/sched_ext"
    attr "${SRC}/sys/kernel/sched_ext" state
fi

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
            "${SRC}"/dev/hl[0-9]* "${SRC}"/dev/kvm "${SRC}"/dev/vhost-vsock; do
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

# ---- doctor --perf inputs ----------------------------------------------------
# CPU frequency, idle and thermal-throttle state. cpuN/cpufreq is a link to a
# shared policy directory.
attrs_at() { local d=$1; shift; [ -d "${d}" ] || return 0; dir "${d}"; attr "${d}" "$@"; }
C="${SRC}/sys/devices/system/cpu"
if [ -d "${C}" ]; then
    attrs_at "${C}" online
    attrs_at "${C}/intel_pstate" status no_turbo max_perf_pct min_perf_pct hwp_dynamic_boost
    attrs_at "${C}/cpuidle" current_driver
    for cpu in "${C}"/cpu[0-9]*; do
        [ -d "${cpu}" ] || continue
        if [ -L "${cpu}/cpufreq" ]; then link "${cpu}/cpufreq"; fi
        attrs_at "${cpu}/cpufreq" scaling_driver scaling_governor energy_performance_preference \
            scaling_max_freq scaling_min_freq cpuinfo_max_freq
        attrs_at "${cpu}/thermal_throttle" core_throttle_count core_throttle_max_time_ms \
            core_throttle_total_time_ms package_throttle_count package_throttle_max_time_ms \
            package_throttle_total_time_ms
    done
    for s in "${C}"/cpu0/cpuidle/state[0-9]*; do attrs_at "${s}" name latency disable; done
fi
attrs_at "${SRC}/sys/module/intel_idle/parameters" max_cstate
attrs_at "${SRC}/sys/module/kvm_intel/parameters" nested enable_apicv ept

# coretemp package sensors (TjMax is tempN_crit).
for h in "${SRC}"/sys/class/hwmon/hwmon*; do
    [ "$(cat "${h}/name" 2>/dev/null)" = coretemp ] || continue
    link "${h}"
    attrs_at "${h}" name
    for l in "${h}"/temp*_label; do
        grep -q '^Package id' "${l}" 2>/dev/null || continue
        n=${l##*/}; n=${n%_label}
        attr "${h}" "${n}_label" "${n}_crit" "${n}_input"
    done
done

# RAPL package zones.
for z in "${SRC}"/sys/class/powercap/intel-rapl:[0-9] "${SRC}"/sys/class/powercap/intel-rapl-mmio:[0-9]; do
    [ -L "${z}" ] || continue
    link "${z}"
    attrs_at "${z}" name enabled constraint_{0,1,2}_{name,power_limit_uw,max_power_uw}
done

# MSRs doctor --perf decodes, from CPU 0 (root only). The copy is a regular file
# with each 8-byte value at its MSR address, as the msr driver serves them; the
# chosen MSRs do not overlap in that layout.
MSRS="0x19C 0x1B1 0x64F 0x6A2 0x982"
if [ -r "${SRC}/dev/cpu/0/msr" ]; then
    mkdir -p "${OUT}/dev/cpu/0"
    head -c $((0x982 + 8)) /dev/zero > "${OUT}/dev/cpu/0/msr"
    for a in ${MSRS}; do
        dd if="${SRC}/dev/cpu/0/msr" of="${OUT}/dev/cpu/0/msr" bs=8 count=1 skip=$((a)) seek=$((a)) \
            iflag=skip_bytes oflag=seek_bytes conv=notrunc status=none 2>/dev/null || true
    done
fi

# SMBIOS type 17 (Memory Device) records, root only. Only the formatted area is
# kept; the string set (locator, manufacturer, serial, asset tag, part number) is
# replaced by an empty one.
for e in "${SRC}"/sys/firmware/dmi/entries/17-*; do
    [ -d "${e}" ] || continue
    dir "${e}"
    [ -r "${e}/raw" ] || continue
    len=$(od -An -tu1 -j1 -N1 "${e}/raw" 2>/dev/null | tr -d ' ')
    [[ "${len}" =~ ^[0-9]+$ ]] || continue
    { head -c "${len}" "${e}/raw"; printf '\0\0'; } > "${OUT}/$(rel "${e}")/raw"
done

# EDAC memory controllers.
for mc in "${SRC}"/sys/devices/system/edac/mc/mc[0-9]*; do attrs_at "${mc}" mc_name ce_count ue_count; done

# Memory, KSM, uptime, the root mount, and the boot options and sysctls doctor --perf reads.
attrs_at "${SRC}/sys/kernel/mm/transparent_hugepage" enabled defrag
attrs_at "${SRC}/sys/kernel/mm/ksm" run pages_sharing
if [ -r "${SRC}/proc/meminfo" ]; then
    mkdir -p "${OUT}/proc"
    grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree|HugePages_Total|HugePages_Free|Hugepagesize):' \
        "${SRC}/proc/meminfo" > "${OUT}/proc/meminfo" || true
fi
# PID 1's view: /proc/mounts follows the reader's mount namespace (a container's).
for m in "${SRC}/proc/1/mounts" "${SRC}/proc/mounts"; do
    [ -r "${m}" ] || continue
    mkdir -p "${OUT}/proc"
    awk '$2 == "/"' "${m}" > "${OUT}/proc/mounts"
    break
done
if [ -r "${SRC}/proc/cmdline" ]; then
    tr ' ' '\n' < "${SRC}/proc/cmdline" | grep -E '^(split_lock_detect|intel_iommu|iommu)=' \
        | paste -sd' ' > "${OUT}/proc/cmdline" || true
fi
for f in proc/uptime proc/sys/vm/swappiness proc/sys/kernel/split_lock_mitigate \
         proc/sys/dev/i915/perf_stream_paranoid proc/sys/dev/xe/observation_paranoid; do
    [ -r "${SRC}/${f}" ] || continue
    mkdir -p "$(dirname "${OUT}/${f}")"
    cat "${SRC}/${f}" > "${OUT}/${f}"
done

# Daemons doctor --perf looks for (comm is cut to 15 characters).
for c in "${SRC}"/proc/[0-9]*/comm; do
    case "$(cat "${c}" 2>/dev/null)" in
        irqbalance|power-profiles-|nvidia-persiste)
            mkdir -p "$(dirname "${OUT}/${c#"${SRC}"/}")"
            cat "${c}" > "${OUT}/${c#"${SRC}"/}" ;;
    esac
done

# IOMMU units and groups.
for u in "${SRC}"/sys/class/iommu/*; do
    [ -L "${u}" ] || continue
    link "${u}"
    dir "${u}"
done
for g in "${SRC}"/sys/kernel/iommu_groups/*; do
    [ -d "${g}" ] || continue
    attrs_at "${g}" type
    for d in "${g}"/devices/*; do
        if [ -L "${d}" ]; then link "${d}"; fi
    done
done

# Intel GPU frequency controls: i915 per-GT sysfs and xe freq0.
for gt in "${SRC}"/sys/class/drm/card[0-9]*/gt/gt[0-9]*; do
    attrs_at "${gt}" rps_cur_freq_mhz rps_act_freq_mhz rps_min_freq_mhz rps_max_freq_mhz \
        rps_boost_freq_mhz rps_RP0_freq_mhz rps_RP1_freq_mhz rps_RPn_freq_mhz slpc_ignore_eff_freq \
        throttle_reason_{status,pl1,pl2,pl4,prochot,ratl,thermal,vr_tdc,vr_thermalert}
done
for f0 in "${SRC}"/sys/class/drm/card[0-9]*/device/tile[0-9]*/gt[0-9]*/freq0; do
    attrs_at "${f0}" cur_freq act_freq min_freq max_freq rp0_freq rpn_freq power_profile
done

find "${OUT}" -type d -empty -exec touch {}/.keep \;
echo "captured $(find "${OUT}" \( -type f -o -type l \) | wc -l) entries into ${OUT}"
