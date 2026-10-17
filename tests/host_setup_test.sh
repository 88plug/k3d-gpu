#!/usr/bin/env bash
# `k3d-gpu host-setup` tests. Each case builds a fake root (os-release, PCI
# devices, libraries) and runs host-setup against it via K3D_GPU_SYSROOT, with
# vainfo, clinfo, ldd, sudo, curl, sha256sum and tar stubbed. Nothing is installed.
#
# Usage: tests/host_setup_test.sh

set -uo pipefail

K3D_GPU="$(cd "$(dirname "$0")/.." && pwd)/scripts/k3d-gpu"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

STUB_BIN="${WORK}/bin"
STUB_LOG="${WORK}/calls.log"
mkdir -p "${STUB_BIN}"
# vainfo / clinfo / ldd print STUB_<NAME> (empty when unset).
for tool in vainfo clinfo ldd; do
    printf '#!/bin/sh\nprintf "%%s\\n" "${STUB_%s:-}"\n' "$(tr '[:lower:]' '[:upper:]' <<< "${tool}")" > "${STUB_BIN}/${tool}"
done
# Installers only log their arguments; sudo fails when its args match STUB_SUDO_FAIL.
for tool in sudo curl sha256sum tar nvidia-ctk; do
    cat > "${STUB_BIN}/${tool}" <<EOF
#!/bin/sh
echo "${tool} \$*" >> "${STUB_LOG}"
case "${tool} \$*" in "sudo "*) [ -n "\${STUB_SUDO_FAIL:-}" ] && case "\$*" in *"\${STUB_SUDO_FAIL}"*) exit 1 ;; esac ;; esac
exit 0
EOF
done
chmod +x "${STUB_BIN}"/*

newroot() { local r="${WORK}/$1"; mkdir -p "${r}/sys/bus/pci/devices" "${r}/etc"; echo "${r}"; }

# osrel <root> <id> [version]
osrel() { printf 'NAME="x"\nID=%s\nVERSION_ID="%s"\n' "$2" "${3:-}" > "$1/etc/os-release"; }

# pci <root> <bdf> <vendor> <device> <class> [driver]
pci() {
    local dir="$1/sys/devices/pci0000:00/$2"
    mkdir -p "${dir}"
    echo "$3" > "${dir}/vendor"; echo "$4" > "${dir}/device"; echo "$5" > "${dir}/class"
    ln -sfn "${dir}" "$1/sys/bus/pci/devices/$2"
    if [ -n "${6:-}" ]; then
        mkdir -p "$1/sys/bus/pci/drivers/$6"
        ln -sfn "$1/sys/bus/pci/drivers/$6" "${dir}/driver"
    fi
}

# lib <root> <path under the library dir> <content>: a fake library holding the
# given strings, under LIBDIR (default usr/lib).
lib() { local d="$1/${LIBDIR:-usr/lib}"; mkdir -p "$(dirname "${d}/$2")"; printf '%s' "$3" > "${d}/$2"; }

kernel() { mkdir -p "$1/proc/sys/kernel"; echo "$2" > "$1/proc/sys/kernel/osrelease"; }

# run <root> <stdin> [args/env...]: host-setup output (stdout+stderr) into OUT, exit code into RC.
run() {
    local root=$1 input=$2 args=() envs=()
    shift 2
    for a in "$@"; do
        case "${a}" in [A-Z]*=*) envs+=("${a}") ;; *) args+=("${a}") ;; esac
    done
    : > "${STUB_LOG}"
    OUT=$(printf '%s' "${input}" | env K3D_GPU_SYSROOT="${root}" K3D_GPU_SETUP_DIR="${WORK}/dl" \
        PATH="${STUB_BIN}:${PATH}" ${envs[@]+"${envs[@]}"} bash "${K3D_GPU}" host-setup ${args[@]+"${args[@]}"} 2>&1)
    RC=$?
}

check() {  # check <name> <ok-condition-exit-code> <detail on failure>
    if [ "$2" -eq 0 ]; then
        PASS=$((PASS + 1)); echo "ok   $1"
    else
        FAILS=$((FAILS + 1)); echo "FAIL $1 ($3)"; sed 's/^/  | /' <<< "${OUT}"
        [ -s "${STUB_LOG}" ] && sed 's/^/  log| /' "${STUB_LOG}"
    fi
}
says()     { grep -F -- "$2" <<< "${OUT}" >/dev/null; check "$1" $? "missing: $2"; }
not_says() { ! grep -F -- "$2" <<< "${OUT}" >/dev/null; check "$1" $? "unexpected: $2"; }
logged()   { grep -F -- "$2" "${STUB_LOG}" >/dev/null; check "$1" $? "not run: $2"; }
rc_is()    { local rc=0; [ "${RC}" -eq "$2" ] || rc=1; check "$1" "${rc}" "rc=${RC}, want $2"; }
ran_nothing() { local rc=0; [ ! -s "${STUB_LOG}" ] || rc=1; check "$1" "${rc}" "installer stubs were called"; }
nth_call() {  # nth_call <name> <n> <expected n-th logged call>
    local rc=0 got
    got=$(sed -n "$2p" "${STUB_LOG}")
    [ "${got}" = "$3" ] || rc=1
    check "$1" "${rc}" "call $2 was: ${got}"
}

# ---- distro plans -----------------------------------------------------------

r=$(newroot ubuntu2404)
osrel "${r}" ubuntu 24.04
pci "${r}" 0000:00:02.0 0x8086 0x7d55 0x030000 xe
run "${r}" ""
rc_is "dry run exits 0" 0
says "ubuntu 24.04 pins the PPA media driver for 24.04" "intel-media-va-driver-non-free=26.3.2-1~24.04~ppa1"
says "ubuntu pins libva from the PPA" "libva2=2.24.1-1~24.04~ppa2"
says "ubuntu pins libva-x11-2/libva-wayland2 with libva (vainfo needs both)" "libva-x11-2=2.24.1-1~24.04~ppa2 libva-wayland2=2.24.1-1~24.04~ppa2"
says "ubuntu keeps apt from swapping the GitHub compute .debs for PPA builds" "Pin: release o=LP-PPA-kobuk-team-intel-graphics"
says "the apt pin covers the runtime packages" "Package: intel-opencl-icd libze-intel-gpu1 libze1"
says "downloads go to a private dir that must be the user's own" "mkdir -p '${WORK}/dl' && [ ! -L '${WORK}/dl' ] && [ -O '${WORK}/dl' ] && chmod 0700 '${WORK}/dl'"
says "ubuntu downloads compute-runtime 26.35 .debs" "releases/download/26.35.39758.10/libze-intel-gpu1_26.35.39758.10-0_amd64.deb"
says "ubuntu verifies the opencl-icd sha256" "61712caaddeba3d38e4f79e2a0fb23fea25596ca2d72c3144c6eea2331ec4301  ${WORK}/dl/intel-opencl-icd_26.35.39758.10-0_amd64.deb' | sha256sum -c -"
says "ubuntu installs IGC 2.41.5 with the runtime" "intel-igc-opencl-2_2.41.5+22716_amd64.deb"
says "ubuntu installs the level-zero loader .deb" "libze1_1.34.0+u24.04_amd64.deb"
not_says "no NVIDIA plan without an NVIDIA GPU" "nvidia-container-toolkit"
not_says "no NPU plan without an NPU" "linux-npu-driver"
says "dry run says nothing changed" "Dry run: nothing was changed"
ran_nothing "dry run calls no installer"

r=$(newroot ubuntu2604-npu)
osrel "${r}" ubuntu 26.04
pci "${r}" 0000:00:0b.0 0x8086 0xb03e 0x120000 intel_vpu
run "${r}" ""
says "ubuntu 26.04 NPU uses the 26.04 driver archive" "linux-npu-driver-v1.38.0.20260910-34487311128-ubuntu2604.tar.gz"
says "NPU archive sha256 is checked" "83685e6d92f4db1ca65e442846d487c7feb1335634b33994292ceb38f933bcc7"
says "NPU install pulls libtbb12 and the archive .debs by name" "libtbb12 '${WORK}/dl/npu-v1.38.0/intel-level-zero-npu_1.38.0.20260910-34487311128~ubuntu26.04_amd64.deb' '${WORK}/dl/npu-v1.38.0/intel-driver-compiler-npu_1.38.0.20260910-34487311128~ubuntu26.04_amd64.deb' '${WORK}/dl/npu-v1.38.0/intel-fw-npu_1.38.0.20260910-34487311128~ubuntu26.04_amd64.deb'"
not_says "NPU install never globs the download dir" "*.deb"
says "only the named .debs are unpacked from the checked archive" "-C '${WORK}/dl/npu-v1.38.0' './intel-level-zero-npu_1.38.0.20260910-34487311128~ubuntu26.04_amd64.deb'"
says "NPU gets the level-zero loader too" "libze1_1.34.0+u24.04_amd64.deb"
not_says "NPU-only host skips the media PPA" "add-apt-repository"
says "missing NPU firmware is reported" "intel/vpu/vpu_50xx_* firmware missing"

mkdir -p "${r}/lib/firmware/updates/intel/vpu"; touch "${r}/lib/firmware/updates/intel/vpu/vpu_50xx_v1.bin"
run "${r}" ""
says "NPU firmware under lib/firmware/updates counts" "intel/vpu/vpu_50xx_* firmware present"

r=$(newroot ubuntu2604-nv)
osrel "${r}" ubuntu 26.04
pci "${r}" 0000:01:00.0 0x10de 0x27b0 0x030000
run "${r}" ""
says "ubuntu NVIDIA toolkit is pinned" "nvidia-container-toolkit=1.20.1-1 nvidia-container-toolkit-base=1.20.1-1 libnvidia-container-tools=1.20.1-1 libnvidia-container1=1.20.1-1"
says "ubuntu uses nvidia-cdi-refresh for the CDI spec" "sudo systemctl restart nvidia-cdi-refresh.service"
says "docker gets the nvidia runtime" "sudo nvidia-ctk runtime configure --runtime=docker"
not_says "NVIDIA-only host has no Intel steps" "kobuk-team"

run "${r}" "" --distro fedora
says "--distro prints another distro's plan" "sudo dnf install -y nvidia-container-toolkit-1.20.1-1"

run "${r}" "" --distro=debian-12
rc_is "unsupported distro fails" 1
says "unsupported distro names the supported ones" "supports Ubuntu 24.04/26.04, Fedora, Arch and Manjaro"

run "${r}" "" --bogus
rc_is "unknown option fails" 1

r=$(newroot fedora)
osrel "${r}" fedora 44
pci "${r}" 0000:00:02.0 0x8086 0xe20b 0x030000 xe
pci "${r}" 0000:00:0b.0 0x8086 0x643e 0x120000 intel_vpu
run "${r}" ""
says "fedora installs Intel GPU + NPU from its repos" "sudo dnf install -y --refresh intel-opencl intel-level-zero oneapi-level-zero libva-intel-media-driver libva-utils clinfo intel-gpu-firmware intel-npu-driver intel-npu-firmware"

r=$(newroot debian)
osrel "${r}" debian 12
run "${r}" ""
rc_is "unsupported host distro fails" 1

r=$(newroot arch-empty)
osrel "${r}" arch
run "${r}" ""
rc_is "host with nothing to set up exits 0" 0
says "host with nothing to set up says so" "nothing to set up"
run "${r}" "" K3D_GPU_VENDORS=nvidia
says "K3D_GPU_VENDORS forces a plan" "sudo pacman -Syu --needed nvidia-container-toolkit"
says "arch uses nvidia-cdi-refresh when the package ships it (1.20.1+)" "if [ -e '${r}/usr/lib/systemd/system/nvidia-cdi-refresh.service' ]; then sudo systemctl enable --now nvidia-cdi-refresh.path && sudo systemctl restart nvidia-cdi-refresh.service; else sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml; fi"

run "${r}" "" --distro=ubuntu-24.04 K3D_GPU_VENDORS=intel K3D_GPU_SETUP_DIR= HOME=/home/u XDG_CACHE_HOME=
says "default download dir is the user's cache, not /tmp" "mkdir -p '/home/u/.cache/k3d-gpu/host-setup'"
run "${r}" "" --distro=ubuntu-24.04 K3D_GPU_VENDORS=intel K3D_GPU_SETUP_DIR= XDG_CACHE_HOME=/xdg
says "XDG_CACHE_HOME is honored" "mkdir -p '/xdg/k3d-gpu/host-setup'"

# ---- Manjaro partial-upgrade failure modes ----------------------------------

manjaro() {  # manjaro <name> <libva so version> <libstdc++ GLIBCXX max> <runtime GLIBCXX need>
    local r
    r=$(newroot "$1")
    osrel "${r}" manjaro
    pci "${r}" 0000:00:02.0 0x8086 0x4680 0x030000 i915
    lib "${r}" dri/iHD_drv_video.so "__vaDriverInit_1_24 GLIBCXX_3.4.30"
    lib "${r}" "libva.so.2.$2.0" "x"
    lib "${r}" libstdc++.so.6 "GLIBCXX_3.4.20 GLIBCXX_$3"
    lib "${r}" intel-opencl/libigdrcl.so "GLIBCXX_3.4.30 GLIBCXX_$4"
    lib "${r}" libze_intel_gpu.so.1 "GLIBCXX_$4"
    lib "${r}" libze_loader.so.1 "x"
    mkdir -p "${r}/etc/OpenCL/vendors"; echo /usr/lib/intel-opencl/libigdrcl.so > "${r}/etc/OpenCL/vendors/intel.icd"
    echo "${r}"
}

r=$(manjaro manjaro-libva 2300 3.4.35 3.4.35)
run "${r}" "" STUB_VAINFO="libva error: /usr/lib/dri/iHD_drv_video.so has no function __vaDriverInit_1_0" \
    STUB_CLINFO="Platform #0: Intel(R) OpenCL Graphics"
says "media driver ahead of libva is caught from the files" "exports __vaDriverInit_1_24 (built for libva 2.24) but libva is 2.23"
says "vainfo's __vaDriverInit_1_0 error is read" "vainfo: /usr/lib/dri/iHD_drv_video.so has no function __vaDriverInit_1_0"
says "libva fix on manjaro is a full upgrade" "Fix: sudo pacman -Syu (a full upgrade; never pacman -Sy <pkg> alone)"
says "failed checks are counted" "2 check(s) failed"
says "manjaro plan installs with -Syu" "sudo pacman -Syu --needed intel-compute-runtime level-zero-loader intel-media-driver libva libva-utils clinfo linux-firmware-intel"
says "manjaro gets the mixed-repo warning" "never Arch [extra] or AUR binaries"

r=$(manjaro manjaro-cxx 2400 3.4.34 3.4.35)
run "${r}" "" STUB_VAINFO="vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 26.2.4 ()" \
    STUB_CLINFO="Platform #0: NVIDIA CUDA" \
    STUB_LDD="	/usr/lib/libstdc++.so.6: version \`GLIBCXX_3.4.35' not found (required by /usr/lib/intel-opencl/libigdrcl.so)"
says "runtime newer than libstdc++ is caught from the files" "/usr/lib/intel-opencl/libigdrcl.so needs GLIBCXX_3.4.35 but libstdc++ provides up to GLIBCXX_3.4.34"
says "the Level Zero GPU driver is checked too" "/usr/lib/libze_intel_gpu.so.1 needs GLIBCXX_3.4.35"
says "clinfo without an Intel platform is reported" "clinfo lists no Intel OpenCL platform although /usr/lib/intel-opencl/libigdrcl.so is registered"
says "ldd names the missing GLIBCXX" "version \`GLIBCXX_3.4.35' not found (required by /usr/lib/intel-opencl/libigdrcl.so)"
says "matching libva passes" "media driver exports __vaDriverInit_1_24, libva is 2.24"
says "working vainfo shows the driver" "vainfo: Intel iHD driver for Intel(R) Gen Graphics - 26.2.4 ()"

r=$(manjaro manjaro-dlopen 2400 3.4.35 3.4.35)
run "${r}" "" STUB_VAINFO="libva error: dlopen of /usr/lib/dri/iHD_drv_video.so failed: /usr/lib/libstdc++.so.6: version \`GLIBCXX_3.4.35' not found" \
    STUB_CLINFO="Platform #0: Intel(R) OpenCL Graphics"
says "vainfo dlopen GLIBCXX failure names the fix" "libstdc++ is older than the media driver needs. Fix: sudo pacman -Syu"

r=$(manjaro manjaro-ok 2400 3.4.35 3.4.35)
run "${r}" "" STUB_VAINFO="vainfo: Driver version: Intel iHD driver - 26.2.4 ()" STUB_CLINFO="Platform #0: Intel(R) OpenCL Graphics"
says "healthy host: libstdc++ ok" "libstdc++ provides GLIBCXX_3.4.35, enough for the installed Intel runtimes"
says "healthy host: clinfo ok" "ok: clinfo: Intel(R) OpenCL Graphics"
not_says "healthy host has no failures" "fail"
not_says "healthy host gets no install plan" "Install plan"
not_says "healthy host is not told to upgrade the system" "pacman -Syu"
says "healthy host says there is nothing to install" "nothing to install"
run "${r}" "" --plan STUB_VAINFO="vainfo: Driver version: Intel iHD driver - 26.2.4 ()" STUB_CLINFO="Platform #0: Intel(R) OpenCL Graphics"
says "--plan prints the full plan even on a healthy host" "Install plan"

r=$(manjaro ubuntu-libva 2000 3.4.35 3.4.35)
osrel "${r}" ubuntu 24.04
run "${r}" "" STUB_CLINFO="Platform #0: Intel(R) OpenCL Graphics"
says "ubuntu libva fix uses the pinned PPA libva" "Fix: sudo add-apt-repository -y ppa:kobuk-team/intel-graphics && sudo apt-get install -y libva2=2.24.1-1~24.04~ppa2"

# ---- kernel guidance --------------------------------------------------------

r=$(newroot kernel)
osrel "${r}" fedora 44
kernel "${r}" 6.8.0
pci "${r}" 0000:03:00.0 0x8086 0x674c 0x030000
pci "${r}" 0000:04:00.0 0x8086 0xe20b 0x030000
run "${r}" ""
says "unsupported part gets force_probe" "Intel Crescent Island (0x674c) needs the boot options xe.force_probe=674c i915.force_probe=!674c"
says "unbound part on an old kernel gets force_probe or a kernel" "Intel Battlemage (0xe20b) needs the boot options xe.force_probe=e20b i915.force_probe=!e20b (or kernel 6.12+)"
says "fedora boot options go through grubby" "sudo grubby --update-kernel=ALL"

# Late Battlemage ids, Flex and Max: force_probe cannot add an id missing from
# xe's table (Pro B70 0xe223 joined in 6.17); Max has no upstream driver at all.
r=$(newroot kernel-dgpu)
osrel "${r}" fedora 44
kernel "${r}" 6.16.0
pci "${r}" 0000:03:00.0 0x8086 0xe223 0x030000
pci "${r}" 0000:04:00.0 0x8086 0x56c1 0x038000
pci "${r}" 0000:05:00.0 0x8086 0x0bd5 0x038000
run "${r}" ""
says "late Battlemage id gets a kernel, not force_probe" "Intel Battlemage (0xe223) needs kernel 6.17+ (running 6.16.0); force_probe cannot add an id missing from the driver's table"
not_says "late Battlemage id gets no force_probe" "xe.force_probe=e223"
says "Flex gets dkms or xe force-probe" "Intel Flex (ATS-M) (0x56c1) needs intel-i915-dkms LTS (2523.x), or xe force-probe (not production upstream; exact apply and revert: k3d-gpu doctor --experimental, xe-force-probe)"
says "Max has no upstream driver" "Intel Max (Ponte Vecchio) (0x0bd5) has no upstream driver"
not_says "Max gets no force_probe" "xe.force_probe=0bd5"

# Late Alchemist ids and Flex below 6.8 (no xe): no force_probe advice.
r=$(newroot kernel-dg2-late)
osrel "${r}" fedora 44
kernel "${r}" 6.5.0
pci "${r}" 0000:03:00.0 0x8086 0x56ba 0x030000
run "${r}" ""
says "late DG2 id on 6.5 gets a kernel" "Intel Alchemist DG2-G11 (0x56ba) needs kernel 6.7+ (running 6.5.0); force_probe cannot add an id missing from the driver's table"
not_says "late DG2 id on 6.5 gets no force_probe" "force_probe=56ba"

r=$(newroot kernel-dg2-late2)
osrel "${r}" fedora 44
kernel "${r}" 6.8.0
pci "${r}" 0000:03:00.0 0x8086 0x56be 0x030000
run "${r}" ""
says "late DG2 id on 6.8 gets 6.10" "Intel Alchemist DG2-G10 (0x56be) needs kernel 6.10+ (running 6.8.0)"
not_says "late DG2 id on 6.8 gets no force_probe" "force_probe=56be"

r=$(newroot kernel-flex-515)
osrel "${r}" fedora 44
kernel "${r}" 5.15.0
pci "${r}" 0000:03:00.0 0x8086 0x56c1 0x038000
run "${r}" ""
says "Flex on 5.15 gets dkms only" "Intel Flex (ATS-M) (0x56c1) needs intel-i915-dkms LTS (2523.x); kernel 5.15.0 has no xe (merged in 6.8)"
not_says "Flex on 5.15 gets no xe.force_probe" "xe.force_probe=56c1"

r=$(newroot kernel-i915)
osrel "${r}" fedora 44
kernel "${r}" 6.5.0
pci "${r}" 0000:00:02.0 0x8086 0x7d55 0x030000
run "${r}" ""
says "i915-default part on an old kernel gets i915.force_probe" "Intel Meteor Lake (0x7d55) needs the boot option i915.force_probe=7d55 (or kernel 6.7+)"
not_says "i915-default part is never pointed at xe" "xe.force_probe=7d55"
not_says "boot options are never in the plan" "    sudo grubby"

# ---- --apply ----------------------------------------------------------------

r=$(newroot apply)
osrel "${r}" arch
pci "${r}" 0000:01:00.0 0x10de 0x27b0 0x030000
run "${r}" "n" --apply
says "--apply asks first" "Run these 4 commands now? [y/N]"
says "--apply with no answers aborts" "Aborted: nothing was run."
ran_nothing "declined --apply runs nothing"

run "${r}" "" --apply
ran_nothing "--apply with no input runs nothing"

run "${r}" "y" --apply
rc_is "--apply y succeeds" 0
logged "--apply runs pacman" "sudo pacman -Syu --needed nvidia-container-toolkit"
logged "--apply generates the CDI spec when the package has no refresh units" "sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml"
nth_call "--apply runs the plan in order" 4 "sudo systemctl restart docker"

run "${r}" "y" --apply STUB_SUDO_FAIL="cdi generate"
rc_is "--apply stops on a failed command" 1
says "--apply names the failed command" "command failed: if [ -e"
not_says "--apply runs nothing after a failure" "+ sudo systemctl restart docker"

r=$(newroot apply-ubuntu)
osrel "${r}" ubuntu 26.04
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d 0x120000 intel_vpu
run "${r}" "y" --apply
rc_is "ubuntu --apply succeeds" 0
logged "ubuntu --apply downloads the NPU archive" "curl -fL --retry 3 -o ${WORK}/dl/linux-npu-driver-v1.38.0.20260910-34487311128-ubuntu2604.tar.gz"
logged "ubuntu --apply checks the sha256" "sha256sum -c -"
logged "ubuntu --apply unpacks the archive" "tar -xzf ${WORK}/dl/linux-npu-driver-v1.38.0.20260910-34487311128-ubuntu2604.tar.gz -C ${WORK}/dl/npu-v1.38.0"

r=$(newroot apply-arch-units)
osrel "${r}" manjaro
pci "${r}" 0000:01:00.0 0x10de 0x27b0 0x030000
mkdir -p "${r}/usr/lib/systemd/system"; touch "${r}/usr/lib/systemd/system/nvidia-cdi-refresh.service"
run "${r}" "y" --apply
logged "--apply with the refresh units restarts nvidia-cdi-refresh" "sudo systemctl restart nvidia-cdi-refresh.service"
not_says "--apply with the refresh units writes no static /etc spec" "+ sudo nvidia-ctk cdi generate"

mkdir -p "${WORK}/elsewhere"; ln -sfn "${WORK}/elsewhere" "${WORK}/dl-link"
r=$(newroot apply-symlink)
osrel "${r}" ubuntu 26.04
pci "${r}" 0000:00:0b.0 0x8086 0x7d1d 0x120000 intel_vpu
run "${r}" "y" --apply K3D_GPU_SETUP_DIR="${WORK}/dl-link"
rc_is "--apply refuses a download dir that is a symlink" 1
not_says "--apply downloads nothing into a refused dir" "+ curl"

# ---- library search order -----------------------------------------------------

for d in usr/lib/x86_64-linux-gnu usr/lib64; do
    r=$(newroot "libdir-${d//\//-}")
    osrel "${r}" fedora 44
    pci "${r}" 0000:00:02.0 0x8086 0x4680 0x030000 i915
    LIBDIR=$d lib "${r}" dri/iHD_drv_video.so "__vaDriverInit_1_24"
    LIBDIR=$d lib "${r}" libva.so.2.2300.0 "x"
    LIBDIR=$d lib "${r}" libstdc++.so.6 "GLIBCXX_3.4.34"
    LIBDIR=$d lib "${r}" intel-opencl/libigdrcl.so "GLIBCXX_3.4.35"
    LIBDIR=$d lib "${r}" libze_loader.so.1 "x"
    run "${r}" "" STUB_CLINFO="Platform #0: Intel(R) OpenCL Graphics"
    says "media driver vs libva found under ${d}" "exports __vaDriverInit_1_24 (built for libva 2.24) but libva is 2.23"
    says "libstdc++ vs runtime found under ${d}" "/${d}/intel-opencl/libigdrcl.so needs GLIBCXX_3.4.35"
    says "Level Zero loader found under ${d}" "Level Zero loader: libze_loader.so.1"
done

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
