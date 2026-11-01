#!/usr/bin/env bash
# vm.sh build | fetch | up VM | wait VM SECONDS | facts VM | ssh VM CMD... | down VM | purge
# H62's two KVM guests on crescent (EXPERIMENTS.md "H62 pre-registration"): VM a serves A1, VM b
# serves B5. QEMU runs in the pinned container of qemu.Dockerfile, so the host gets no package.
# Disks, seed images, the ssh key and ssh_config live under H62_STATE (default
# ~/k3dgpu-bench/h62); results do not. Shapes, ports and pins come from h62.py.
#   build             the QEMU image (h62.py qemu-image); its package list into H62_STATE
#   fetch             the Ubuntu 24.04 cloud image into H62_STATE/base.img, checked against its sha256
#   up VM             the guest's qcow2 overlay, its cloud-init seed and its QEMU container; VM a also
#                     gets A1's IR read-only (H62_IR, default the IR live.sh's gemma4 set names)
#   wait VM SECONDS   until the guest answers ssh and cloud-init has finished
#   facts VM          one JSON line: nproc, amx_tile count, MemTotal and kernel (the guest gate's facts)
#   ssh VM CMD...     a command in the guest
#   down VM           power the guest off (at most 120 s), then remove its container
#   purge             the guests' disks, the cloud image and the QEMU image (refused while a guest runs)
# H62_DRY=1 prints each docker command instead of running it.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
P() { python3 "${HERE}/h62.py" "$@"; }
STATE=${H62_STATE:-${HOME}/k3dgpu-bench/h62}
IMAGE=$(P qemu-image)
DRY=${H62_DRY:-0}
run() { if [ "${DRY}" = 1 ]; then echo "$*"; else "$@"; fi; }
in_qemu() { run docker run --rm --user "$(id -u):$(id -g)" -v "${STATE}:/h62" "${IMAGE}" "$@"; }
gssh() { ssh -F "${STATE}/ssh_config" "$(P vm "$1" host)" "${@:2}"; }
exists() { [ "${DRY}" != 1 ] && docker ps -a --format '{{.Names}}' | grep -x "$1" > /dev/null; }
mkdir -p "${STATE}"
chmod 700 "${STATE}"
case ${1:?usage: vm.sh build|fetch|up VM|wait VM SECONDS|facts VM|ssh VM CMD...|down VM|purge} in
build)
    run docker build -t "${IMAGE}" -f "${HERE}/qemu.Dockerfile" "${HERE}"
    if [ "${DRY}" != 1 ]; then
        docker image inspect --format '{{.Id}}' "${IMAGE}" > "${STATE}/qemu-image.txt"
        docker run --rm "${IMAGE}" cat /h62-packages.txt > "${STATE}/qemu-packages.txt"
    fi ;;
fetch)
    if [ ! -f "${STATE}/base.img" ]; then
        curl -fsSLo "${STATE}/base.img.part" "$(P pin cloud_image_url)"
        mv "${STATE}/base.img.part" "${STATE}/base.img"
    fi
    echo "$(P pin cloud_image_sha256)  ${STATE}/base.img" | sha256sum -c
    [ "$(stat -c %s "${STATE}/base.img")" = "$(P pin cloud_image_bytes)" ] || { echo "base.img: wrong size"; exit 1; }
    chmod 444 "${STATE}/base.img" ;;
up)
    vm=${2:?usage: vm.sh up a|b}
    name=$(P vm "${vm}" name)
    if exists "${name}"; then echo "${name} exists: vm.sh down ${vm} first"; exit 1; fi
    d=${STATE}/${vm}
    mkdir -p "${d}"
    [ -f "${STATE}/id_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -C h62 -f "${STATE}/id_ed25519"
    P ssh-config "${STATE}" > "${STATE}/ssh_config"
    # A new guest has a new host key on the same forwarded port.
    [ ! -f "${STATE}/known_hosts" ] || ssh-keygen -q -R "[127.0.0.1]:$(P vm "${vm}" ssh_port)" \
        -f "${STATE}/known_hosts" > /dev/null 2>&1 || true
    P user-data "${vm}" "${STATE}/id_ed25519.pub" > "${d}/user-data"
    P meta-data "${vm}" > "${d}/meta-data"
    rm -f "${d}/seed.iso" "${d}/disk.qcow2"
    in_qemu genisoimage -output "/h62/${vm}/seed.iso" -volid cidata -joliet -rock "/h62/${vm}/user-data" "/h62/${vm}/meta-data"
    in_qemu qemu-img create -f qcow2 -F qcow2 -b /h62/base.img "/h62/${vm}/disk.qcow2" "$(P vm "${vm}" disk_gib)G"
    share=()
    if [ "$(P vm "${vm}" share)" = True ]; then
        share=("$(readlink -f "${H62_IR:-${HOME}/k3dgpu-bench/models/gemma-4-26B-A4B-it-qat-int4-ov}")")
    fi
    kvm=${H62_KVM_GID:-$(stat -c %g /dev/kvm)}
    mapfile -t argv < <(P qemu-argv "${vm}" "${STATE}" "${IMAGE}" "$(id -u)" "$(id -g)" "${kvm}" "${share[@]}")
    [ "${#argv[@]}" -gt 10 ] || { echo "no QEMU command line for VM ${vm}"; exit 1; }
    run "${argv[@]}" ;;
wait)
    vm=${2:?usage: vm.sh wait a|b SECONDS}
    end=$(( SECONDS + ${3:?usage: vm.sh wait a|b SECONDS} ))
    until gssh "${vm}" true > /dev/null 2>&1; do
        [ "${SECONDS}" -lt "${end}" ] || { echo "VM ${vm}: no ssh within ${3} s"; exit 1; }
        sleep 5
    done
    gssh "${vm}" cloud-init status --wait > /dev/null ;;
facts)
    gssh "${2:?usage: vm.sh facts a|b}" python3 - <<'PY'
import json, os, platform
flags = [ln for ln in open("/proc/cpuinfo") if ln.startswith("flags")]
mem = {k: v for k, _, v in (ln.partition(":") for ln in open("/proc/meminfo"))}
print(json.dumps({"nproc": os.cpu_count(), "amx_tile": sum(" amx_tile" in ln for ln in flags),
                  "mem_total_kib": int(mem["MemTotal"].split()[0]), "kernel": platform.release()}))
PY
    ;;
ssh)
    gssh "${2:?usage: vm.sh ssh a|b CMD...}" "${@:3}" ;;
down)
    vm=${2:?usage: vm.sh down a|b}
    name=$(P vm "${vm}" name)
    gssh "${vm}" sudo poweroff > /dev/null 2>&1 || true
    for _ in $(seq 60); do
        [ "$(docker inspect -f '{{.State.Running}}' "${name}" 2>/dev/null)" = true ] || break
        sleep 2
    done
    run docker rm -f "${name}" > /dev/null 2>&1 || true ;;
purge)
    for vm in a b; do
        if exists "$(P vm "${vm}" name)"; then echo "VM ${vm} still exists: vm.sh down ${vm} first"; exit 1; fi
    done
    rm -rf "${STATE:?}/a" "${STATE:?}/b" "${STATE}/base.img" "${STATE}/base.img.part" "${STATE}/known_hosts"
    run docker image rm "${IMAGE}" > /dev/null 2>&1 || true ;;
*)
    echo "usage: vm.sh build|fetch|up VM|wait VM SECONDS|facts VM|ssh VM CMD...|down VM|purge"; exit 2 ;;
esac
