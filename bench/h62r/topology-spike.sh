#!/usr/bin/env bash
# topology-spike.sh: what EAS v1.2.0's node topology probe reads in a KVM guest of H62r's shape
# (EXPERIMENTS.md "H62r pre-registration", D3's sibling read). It boots a guest with
# -smp 96,sockets=1,cores=48,threads=2 from an initramfs (alpine's busybox userland and EAS's
# roles/nri_cpu_balloons/files/probe-node-topology.sh at the pinned EAS commit), prints cpu1's
# thread_siblings_list, the probe's NRI_NUMA, NRI_LOGICAL, NRI_TPC and NRI_PHYSICAL lines and its
# NRI_SIBLINGS line (the cpuset the 'siblings' profile reserves when NRI_TPC reads 2), and powers
# off. Not a hold and not a measurement of either arm.
# Needs docker, qemu-system-x86_64 and /dev/kvm; TOPO_KERNEL is the guest kernel (default: the
# first /boot/vmlinuz*); TOPO_SMP overrides the shape. The guest has no network and no disk.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ALPINE=alpine:3.24.4@sha256:93b41ef40b1677fce8fcc64ff4af09daddfacf5040afde618bc46c1e2d1cc7d4
SHA=$(python3 "${HERE}/h62r.py" pin eas_sha)
KERNEL=${TOPO_KERNEL:-$(find /boot -maxdepth 1 -name 'vmlinuz*' | sort | head -1)}
SMP=${TOPO_SMP:-$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import h62r; print(h62r.SMP)' "${HERE}")}
[ -r "${KERNEL}" ] || { echo "no readable guest kernel (set TOPO_KERNEL)"; exit 2; }
d=$(mktemp -d)
cid=""
cleanup() { [ -z "${cid}" ] || docker rm "${cid}" > /dev/null 2>&1 || true; rm -rf "${d}"; }
trap cleanup EXIT
mkdir -p "${d}/root"
cid=$(docker create "${ALPINE}")
docker export "${cid}" | tar -x -C "${d}/root"
curl -fsSLo "${d}/root/probe.sh" \
    "https://raw.githubusercontent.com/intel/enterprise-ai-solutions/${SHA}/roles/nri_cpu_balloons/files/probe-node-topology.sh"
cat > "${d}/root/init" <<'EOF'
#!/bin/sh
mount -t devtmpfs devtmpfs /dev
exec > /dev/ttyS0 2>&1
mount -t proc proc /proc
mount -t sysfs sysfs /sys
echo "SPIKE cpu1 thread_siblings_list=$(cat /sys/devices/system/cpu/cpu1/topology/thread_siblings_list)"
sh /probe.sh | grep -E '^NRI_(NUMA|LOGICAL|TPC|PHYSICAL)=|^NRI_SIBLINGS=' | sed 's/^/SPIKE /'
poweroff -f
EOF
chmod +x "${d}/root/init"
(cd "${d}/root" && find . | cpio -o -H newc 2> /dev/null | gzip -1) > "${d}/initrd.gz"
echo "qemu: $(qemu-system-x86_64 --version | head -1); kernel ${KERNEL}; -smp ${SMP}; probe at EAS ${SHA}"
timeout 180 qemu-system-x86_64 -machine q35,accel=kvm -cpu host -smp "${SMP}" -m 1G -kernel "${KERNEL}" \
    -initrd "${d}/initrd.gz" -append "console=ttyS0 quiet rdinit=/init" -nographic -no-reboot 2>&1 \
    | tr -d '\r' | sed -n 's/.*SPIKE //p'
