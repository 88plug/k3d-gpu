#!/usr/bin/env bash
# inventory.sh: the host's accelerators read from sysfs, /proc and lspci-equivalent PCI sysfs,
# not from k3d-gpu (EXPERIMENTS.md "H50-H52 pre-registration", H52). Read-only. The first line
# is the only one that changes between two reads of an unchanged host.
r() { cat "$1" 2>/dev/null; }
name() { local p; p=$(readlink "$1" 2>/dev/null) && basename "${p}"; }
echo "# date $(date -u +%FT%TZ) host $(hostname) kernel $(uname -r)"
echo "cpu: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2) microcode $(grep -m1 microcode /proc/cpuinfo | awk '{print $3}')"
nodes=$(cd /sys/devices/system/node && echo node[0-9]*)
atom=no
[ -e /sys/devices/cpu_atom ] && atom=yes
echo "online $(r /sys/devices/system/cpu/online) nodes ${nodes} cpu_atom=${atom} smt=$(r /sys/devices/system/cpu/smt/active)"
for f in amx_tile amx_bf16 amx_int8 amx_fp16 amx_complex avx512f avx512_bf16 avx512_fp16 avx512_vnni avx_vnni; do
    v=0
    grep -qw "${f}" /proc/cpuinfo && v=1
    printf '%s=%s ' "${f}" "${v}"
done
echo
echo "## pci (Intel GPU, DSA, IAA, QAT, DLB)"
for d in /sys/bus/pci/devices/*; do
    case "$(r "${d}/vendor"):$(r "${d}/device")" in
        0x8086:0xe223|0x8086:0x0b25|0x8086:0x0cfe|0x8086:0x4944|0x8086:0x4945|0x8086:0x2714|0x8086:0x2715)
            echo "$(basename "${d}") $(r "${d}/vendor"):$(r "${d}/device") class=$(r "${d}/class") driver=$(name "${d}/driver") numa=$(r "${d}/numa_node") sriov=$(r "${d}/sriov_numvfs")/$(r "${d}/sriov_totalvfs") qat_state=$(r "${d}/qat/state") qat_cfg=$(r "${d}/qat/cfg_services")" ;;
    esac
done
echo "## drm"
for c in /sys/class/drm/card[0-9]* /sys/class/drm/renderD*; do
    [ -e "${c}/device/vendor" ] || continue
    echo "$(basename "${c}") $(r "${c}/device/vendor"):$(r "${c}/device/device") $(name "${c}/device/driver") $(basename "$(readlink -f "${c}/device")")"
done
echo "## idxd devices"
for d in /sys/bus/dsa/devices/dsa* /sys/bus/dsa/devices/iax*; do
    [ -e "${d}/state" ] || continue
    echo "$(basename "${d}") state=$(r "${d}/state") pci=$(basename "$(readlink -f "${d}/..")") max_wqs=$(r "${d}/max_work_queues") max_engines=$(r "${d}/max_engines")"
done
echo "## idxd work queues not in the reset state (disabled, size 0, no group)"
for w in /sys/bus/dsa/devices/wq*; do
    [ -e "${w}/state" ] || continue
    s=$(r "${w}/state") sz=$(r "${w}/size") g=$(r "${w}/group_id")
    [ "${s}" = disabled ] && [ "${sz}" = 0 ] && [ "${g}" = -1 ] && continue
    echo "$(basename "${w}") state=${s} size=${sz} group=${g} mode=$(r "${w}/mode") type=$(r "${w}/type") name=$(r "${w}/name") driver=$(name "${w}/driver")"
done
echo "## idxd engines in a group"
for e in /sys/bus/dsa/devices/engine*; do
    g=$(r "${e}/group_id")
    [ "${g}" = -1 ] || echo "$(basename "${e}") group=${g}"
done
echo "## device nodes"
for p in /dev/dsa /dev/iax /dev/accel /dev/qat_adf_ctl /dev/vfio; do
    if [ -e "${p}" ]; then echo "${p}: $(cd "${p}" 2>/dev/null && echo ./* || echo file)"; else echo "${p}: absent"; fi
done
echo "## modules"
awk '{print $1}' /proc/modules | grep -xE 'idxd|iaa_crypto|qat_4xxx|intel_qat|dlb2|xe|i915|vfio_pci' | sort | paste -sd' '
