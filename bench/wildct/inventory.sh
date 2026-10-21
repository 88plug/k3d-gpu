#!/usr/bin/env bash
# inventory.sh: wildct's CPU and accelerator inventory read from sysfs and /proc, not from
# k3d-gpu (EXPERIMENTS.md "Wildct pre-registration", H52w). Read-only, and it opens no device
# node: GPUs are read from PCI sysfs only (box owner's rule: no GPU is used on this host). The
# first line is the only one that changes between two reads of an unchanged host.
r() { cat "$1" 2>/dev/null; }
name() { local p; p=$(readlink "$1" 2>/dev/null) && basename "${p}"; }
echo "# date $(date -u +%FT%TZ) host $(hostname) kernel $(uname -r)"
echo "cpu: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2) family $(grep -m1 '^cpu family' /proc/cpuinfo | awk '{print $NF}') model $(grep -m1 '^model[[:space:]]*:' /proc/cpuinfo | awk '{print $NF}') microcode $(grep -m1 microcode /proc/cpuinfo | awk '{print $3}')"
nodes=$(cd /sys/devices/system/node && echo node[0-9]*)
echo "online $(r /sys/devices/system/cpu/online) nodes ${nodes} smt=$(r /sys/devices/system/cpu/smt/active)"
echo "cpu_core $(r /sys/devices/cpu_core/cpus) cpu_atom $(r /sys/devices/cpu_atom/cpus) cpu_lowpower $(r /sys/devices/cpu_lowpower/cpus)"
for c in $(seq 0 $(( $(nproc --all) - 1 ))); do
    printf 'cpu%s:%s:%s ' "${c}" "$(r "/sys/devices/system/cpu/cpu${c}/topology/thread_siblings_list")" "$(r "/sys/devices/system/cpu/cpu${c}/cpufreq/cpuinfo_max_freq")"
done
echo
echo "flags: $(grep -m1 '^flags' /proc/cpuinfo | tr ' ' '\n' | grep -E '^(avx|amx|sha_ni|vaes|gfni|ibt|user_shstk|hypervisor|fma|f16c)' | sort | paste -sd' ')"
echo "rapl $(r /sys/class/powercap/intel-rapl:0/name) pl1_uw $(r /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw) pl2_uw $(r /sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw)"
echo "mem_total_kb $(awk '/^MemTotal/ {print $2}' /proc/meminfo) thp $(r /sys/kernel/mm/transparent_hugepage/enabled)"
echo "## pci display, 3D and accelerator functions (class 03xx, 0b40, 1200)"
for d in /sys/bus/pci/devices/*; do
    case "$(r "${d}/class")" in
        0x03*|0x0b40*|0x1200*) echo "$(basename "${d}") $(r "${d}/vendor"):$(r "${d}/device") class=$(r "${d}/class") driver=$(name "${d}/driver")" ;;
    esac
done
echo "## drm (sysfs names only)"
for c in /sys/class/drm/card[0-9] /sys/class/drm/renderD*; do
    [ -e "${c}/device/vendor" ] || continue
    echo "$(basename "${c}") $(r "${c}/device/vendor"):$(r "${c}/device/device") $(name "${c}/device/driver")"
done
echo "## device directories present (not opened)"
for p in /dev/dsa /dev/iax /dev/accel /dev/qat_adf_ctl /dev/nvidiactl /dev/dri; do
    if [ -e "${p}" ]; then echo "${p}: present"; else echo "${p}: absent"; fi
done
echo "## modules"
awk '{print $1}' /proc/modules | grep -xE 'idxd|iaa_crypto|qat_4xxx|intel_qat|xe|i915|nvidia|nvidia_uvm|intel_vpu|habanalabs|vfio_pci' | sort | paste -sd' '
