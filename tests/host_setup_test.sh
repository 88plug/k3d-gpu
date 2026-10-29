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

# host-setup's toolkit pin follows the node image's (Dockerfile ARG), and the
# nightly bump in build-and-push.yml moves both.
ROOT="$(dirname "${K3D_GPU}")/.."
CTK=$(sed -n 's/^ARG NVIDIA_CTK_VERSION="\(.*\)"$/\1/p' "${ROOT}/Dockerfile")
HS=$(sed -n 's/^HS_NVCTK=//p' "${K3D_GPU}")
OUT="HS_NVCTK=${HS} NVIDIA_CTK_VERSION=${CTK}"
ok=1; [ -n "${CTK}" ] && [ "${HS}" = "${CTK}" ] && ok=0
check "HS_NVCTK equals the Dockerfile NVIDIA_CTK_VERSION" "${ok}" "${OUT}"
OUT=$(cat "${ROOT}/.github/workflows/build-and-push.yml")
says "nightly bump moves HS_NVCTK" 'sed -i "s|^HS_NVCTK=.*|HS_NVCTK=${{ steps.nvctk.outputs.ctk }}|" scripts/k3d-gpu'

# host-setup's ppa:kobuk-team/intel-graphics pins. The PPA serves only its
# current builds, so the nightly check (hack/ppa-pins) moves them: the cases below
# read each pin from the launcher. ppa <var> <release>: the PPA's name for that
# release's build (26.3.5-1~ppa1 -> 26.3.5-1~24.04~ppa1).
pin() { sed -n "s/^$1=//p" "${K3D_GPU}"; }
ppa() { local v; v=$(pin "$1"); echo "${v%%~*}~$2~${v#*~}"; }
for v in HS_PPA_MEDIA HS_PPA_GMM HS_PPA_LIBVA; do
    OUT="${v}=$(pin "${v}")"
    ok=1; [[ "$(pin "${v}")" =~ ^[0-9][0-9.]*-[0-9]+~ppa[0-9]+$ ]] && ok=0
    check "${v} is a PPA build with the release taken out" "${ok}" "${OUT}"
    OUT=$(cat "${ROOT}/.github/workflows/build-and-push.yml")
    says "nightly check compares ${v} with the PPA's build" "[ \"\${{ steps.ppa.outputs.${v} }}\" != \"\${{ steps.current.outputs.${v} }}\" ]"
done
# The ppa step as the runner runs a step with no shell: key (bash -e, no pipefail),
# from a checkout whose hack/ppa-pins is a stub. A hack/ppa-pins failure has to fail
# the step: its empty outputs would otherwise read as moved pins and cut a release.
ppastep=$(python3 -c 'import sys, yaml
for s in yaml.safe_load(open(sys.argv[1]))["jobs"]["check"]["steps"]:
    if s.get("id") == "ppa": print(s["run"])' "${ROOT}/.github/workflows/build-and-push.yml")
OUT=${ppastep}
ok=1; [ -n "${ppastep}" ] && ok=0
check "build-and-push has the ppa step" "${ok}" "no step with id ppa"
ppastep_run() {  # ppastep_run <stub exit code>: the step's GITHUB_OUTPUT into OUT, its exit code into RC
    local d="${WORK}/ppastep"
    rm -rf "${d}"; mkdir -p "${d}/hack"
    printf '%s\n' "${ppastep}" > "${d}/step.sh"
    if [ "$1" -eq 0 ]; then
        printf '#!/bin/sh\nprintf "HS_PPA_MEDIA=1.0.0-1~ppa1\\nHS_PPA_GMM=2.0.0-1~ppa1\\nHS_PPA_LIBVA=3.0.0-1~ppa1\\n"\n' > "${d}/hack/ppa-pins"
    else
        printf '#!/bin/sh\necho "ppa-pins: cannot fetch the PPA'"'"'s noble index" >&2\nexit %s\n' "$1" > "${d}/hack/ppa-pins"
    fi
    chmod +x "${d}/hack/ppa-pins"
    : > "${d}/out"
    (cd "${d}" && GITHUB_OUTPUT="${d}/out" bash -e step.sh) >/dev/null 2>&1
    RC=$?
    OUT=$(cat "${d}/out")
}
ppastep_run 0
rc_is "nightly ppa step: hack/ppa-pins succeeding exits 0" 0
says "nightly ppa step: the pins land in its outputs" "HS_PPA_GMM=2.0.0-1~ppa1"
ppastep_run 1
ok=1; [ "${RC}" -ne 0 ] && ok=0
check "nightly ppa step: hack/ppa-pins failing fails the step" "${ok}" "rc=${RC}, want non-zero"
OUT=$(cat "${ROOT}/.github/workflows/build-and-push.yml")
says "nightly check reads the launcher's PPA pins" "grep '^HS_PPA_' scripts/k3d-gpu"
says "nightly bump writes the PPA pins into the launcher and README" "hack/ppa-pins --write scripts/k3d-gpu README.md"
OUT=$(grep -F 'kobuk-team/intel-graphics' "${ROOT}/README.md")
says "README names the pinned media driver" "media driver $(pin HS_PPA_MEDIA | cut -d- -f1)"
says "README names the pinned libva" "libva $(pin HS_PPA_LIBVA | cut -d- -f1)"
says "README names the pinned gmmlib" "gmmlib $(pin HS_PPA_GMM | cut -d- -f1)"

# hack/ppa-pins against fake Packages indexes: pkgidx <dir> <codename> <release>
# <media> <gmm> <libva> [<libva-x11-2>] writes <dir>/<codename> with each build
# (release taken out) put back for <release>.
pkgidx() {
    local d=$1 rel=$3 x11=${7:-$6} p v
    mkdir -p "${d}"
    : > "${d}/$2"
    for p in "intel-media-va-driver-non-free $4" "libigdgmm12 $5" "libva2 $6" "libva-drm2 $6" \
             "libva-x11-2 ${x11}" "libva-wayland2 $6" "vainfo 2.24.0-1~ppa1"; do
        v=${p#* }
        [ "${v}" = - ] || printf 'Package: %s\nVersion: %s\n\n' "${p%% *}" "${v%%~*}~${rel}~${v#*~}" >> "${d}/$2"
    done
}
ppapins() {  # ppapins <index dir> [args...]: hack/ppa-pins output into OUT, exit code into RC
    local d=$1; shift
    : > "${STUB_LOG}"
    OUT=$(PPA_INDEX_DIR="${d}" "${ROOT}/hack/ppa-pins" "$@" 2>&1)
    RC=$?
}
M=$(pin HS_PPA_MEDIA) G=$(pin HS_PPA_GMM) L=$(pin HS_PPA_LIBVA)
pkgidx "${WORK}/ppa-same" noble 24.04 "${M}" "${G}" "${L}"
pkgidx "${WORK}/ppa-same" resolute 26.04 "${M}" "${G}" "${L}"
ppapins "${WORK}/ppa-same"
rc_is "ppa-pins: the PPA serving the pins exits 0" 0
ok=1; [ "${OUT}" = "$(grep '^HS_PPA_' "${K3D_GPU}")" ] && ok=0
check "ppa-pins prints the launcher's own HS_PPA_* lines when nothing moved" "${ok}" "want the launcher's HS_PPA_* lines"
pkgidx "${WORK}/ppa-moved" noble 24.04 26.9.1-1~ppa3 "${G}" 2.25.0-1~ppa1
pkgidx "${WORK}/ppa-moved" resolute 26.04 26.9.1-1~ppa3 "${G}" 2.25.0-1~ppa1
ppapins "${WORK}/ppa-moved"
rc_is "ppa-pins: superseded builds exit 0" 0
says "ppa-pins names the PPA's new media driver build" "HS_PPA_MEDIA=26.9.1-1~ppa3"
says "ppa-pins names the PPA's new libva build" "HS_PPA_LIBVA=2.25.0-1~ppa1"
says "ppa-pins keeps an unmoved pin" "HS_PPA_GMM=${G}"
cp "${K3D_GPU}" "${WORK}/k3d-gpu-copy"; cp "${ROOT}/README.md" "${WORK}/README-copy.md"
ppapins "${WORK}/ppa-moved" --write "${WORK}/k3d-gpu-copy" "${WORK}/README-copy.md"
rc_is "ppa-pins --write exits 0" 0
OUT=$(diff "${K3D_GPU}" "${WORK}/k3d-gpu-copy")
says "--write moves HS_PPA_MEDIA in the launcher" "> HS_PPA_MEDIA=26.9.1-1~ppa3"
says "--write moves HS_PPA_LIBVA in the launcher" "> HS_PPA_LIBVA=2.25.0-1~ppa1"
ok=1; [ "$(grep -c '^[<>]' <<< "${OUT}")" -eq 4 ] && ok=0
check "--write changes only the moved launcher pins" "${ok}" "diff has other lines"
OUT=$(diff "${ROOT}/README.md" "${WORK}/README-copy.md")
says "--write moves the README's media driver" "media driver 26.9.1"
says "--write moves the README's libva" "libva 2.25.0"
ok=1; [ "$(grep -c '^[<>]' <<< "${OUT}")" -eq 2 ] && ok=0
check "--write changes only the README line naming the moved builds" "${ok}" "diff has other lines"
r=$(newroot ppa-bumped)
osrel "${r}" ubuntu 24.04
pci "${r}" 0000:00:02.0 0x8086 0x7d55 0x030000 xe
K3D_GPU_REPO=${K3D_GPU}; K3D_GPU="${WORK}/k3d-gpu-copy"
run "${r}" ""
K3D_GPU=${K3D_GPU_REPO}
says "the bumped launcher plans the new media driver build" "intel-media-va-driver-non-free=26.9.1-1~24.04~ppa3"
says "the bumped launcher plans the new libva build" "libva2=2.25.0-1~24.04~ppa1"
pkgidx "${WORK}/ppa-split" noble 24.04 "${M}" "${G}" "${L}"
pkgidx "${WORK}/ppa-split" resolute 26.04 26.9.1-1~ppa1 "${G}" "${L}"
cp "${K3D_GPU}" "${WORK}/k3d-gpu-split"
ppapins "${WORK}/ppa-split" --write "${WORK}/k3d-gpu-split" "${WORK}/README-copy.md"
rc_is "ppa-pins: 24.04 and 26.04 builds that differ fail (one pin covers both)" 1
says "ppa-pins names the package whose builds differ" "intel-media-va-driver-non-free"
ok=1; cmp -s "${K3D_GPU}" "${WORK}/k3d-gpu-split" && ok=0
check "ppa-pins --write changes nothing when it fails" "${ok}" "launcher copy was written"
pkgidx "${WORK}/ppa-libva" noble 24.04 "${M}" "${G}" "${L}" 2.25.0-1~ppa1
pkgidx "${WORK}/ppa-libva" resolute 26.04 "${M}" "${G}" "${L}" 2.25.0-1~ppa1
ppapins "${WORK}/ppa-libva"
rc_is "ppa-pins: libva packages at different builds fail" 1
says "ppa-pins names the libva package that differs" "libva-x11-2"
pkgidx "${WORK}/ppa-gone" noble 24.04 "${M}" - "${L}"
pkgidx "${WORK}/ppa-gone" resolute 26.04 "${M}" "${G}" "${L}"
ppapins "${WORK}/ppa-gone"
rc_is "ppa-pins: a package missing from an index fails" 1
says "ppa-pins names the missing package and release" "libigdgmm12: not in the PPA's noble index"
# A build without the ~ppa<N> suffix (the PPA's libigc2 is named so) has no pin form.
pkgidx "${WORK}/ppa-odd" noble 24.04 "${M}" - "${L}"
printf 'Package: libigdgmm12\nVersion: 22.10.2-1~24.04\n\n' >> "${WORK}/ppa-odd/noble"
cp "${WORK}/ppa-same/resolute" "${WORK}/ppa-odd/resolute"
ppapins "${WORK}/ppa-odd"
rc_is "ppa-pins: a build that is not <upstream>-<rev>~<release>~ppa<N> fails" 1
says "ppa-pins names the build it cannot pin" "libigdgmm12 22.10.2-1~24.04"

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
says "ubuntu 24.04 pins the PPA media driver for 24.04" "intel-media-va-driver-non-free=$(ppa HS_PPA_MEDIA 24.04)"
says "ubuntu pins libva from the PPA" "libva2=$(ppa HS_PPA_LIBVA 24.04)"
says "ubuntu pins libva-x11-2/libva-wayland2 with libva (vainfo needs both)" "libva-x11-2=$(ppa HS_PPA_LIBVA 24.04) libva-wayland2=$(ppa HS_PPA_LIBVA 24.04)"
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
# host-setup dates NPU firmware by the names the running kernel requests; pin a
# 6.16+ release so the result does not follow the test host's uname -r.
kernel "${r}" 6.17.0-5-generic
pci "${r}" 0000:00:0b.0 0x8086 0xb03e 0x120000 intel_vpu
run "${r}" ""
says "ubuntu 26.04 NPU uses the 26.04 driver archive" "linux-npu-driver-v1.38.0.20260910-34487311128-ubuntu2604.tar.gz"
says "NPU archive sha256 is checked" "83685e6d92f4db1ca65e442846d487c7feb1335634b33994292ceb38f933bcc7"
says "NPU install pulls libtbb12 and the archive .debs by name" "libtbb12 '${WORK}/dl/npu-v1.38.0/intel-level-zero-npu_1.38.0.20260910-34487311128~ubuntu26.04_amd64.deb' '${WORK}/dl/npu-v1.38.0/intel-driver-compiler-npu_1.38.0.20260910-34487311128~ubuntu26.04_amd64.deb' '${WORK}/dl/npu-v1.38.0/intel-fw-npu_1.38.0.20260910-34487311128~ubuntu26.04_amd64.deb'"
not_says "NPU install never globs the download dir" "*.deb"
says "only the named .debs are unpacked from the checked archive" "-C '${WORK}/dl/npu-v1.38.0' './intel-level-zero-npu_1.38.0.20260910-34487311128~ubuntu26.04_amd64.deb'"
says "NPU needs the loader, and 26.04 has no pinned one: said, not guessed" "no pinned Level Zero loader for ubuntu-26.04"
not_says "26.04 NPU never gets the 24.04 loader .deb" "libze1_1.34.0+u24.04_amd64.deb"
not_says "NPU-only host skips the media PPA" "add-apt-repository"
says "missing NPU firmware is reported" "intel/vpu/vpu_50xx_* firmware missing"

mkdir -p "${r}/lib/firmware/updates/intel/vpu"; touch "${r}/lib/firmware/updates/intel/vpu/vpu_50xx_v1.bin"
run "${r}" ""
says "NPU firmware under lib/firmware/updates counts" "intel/vpu/vpu_50xx_* firmware present"

r=$(newroot ubuntu2604-nv)
osrel "${r}" ubuntu 26.04
pci "${r}" 0000:01:00.0 0x10de 0x27b0 0x030000
run "${r}" ""
says "ubuntu NVIDIA toolkit is pinned" "nvidia-container-toolkit=${CTK} nvidia-container-toolkit-base=${CTK} libnvidia-container-tools=${CTK} libnvidia-container1=${CTK}"
says "ubuntu uses nvidia-cdi-refresh for the CDI spec" "sudo systemctl restart nvidia-cdi-refresh.service"
says "docker gets the nvidia runtime" "sudo nvidia-ctk runtime configure --runtime=docker"
not_says "NVIDIA-only host has no Intel steps" "kobuk-team"

run "${r}" "" --distro fedora
says "--distro prints another distro's plan" "sudo dnf install -y nvidia-container-toolkit-${CTK}"

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
# vainfo and clinfo are stubs on PATH here, so their packages pass their checks.
says "fedora installs what the Intel GPU + NPU checks miss from its repos" "sudo dnf install -y --refresh intel-opencl intel-level-zero oneapi-level-zero libva-intel-media-driver intel-gpu-firmware intel-npu-driver intel-npu-firmware"
not_says "fedora: installed clinfo and vainfo are not planned" "libva-utils"
run "${r}" "" K3D_GPU_VENDORS=-npu
says "-npu keeps the auto-detected Intel GPU" "intel-opencl"
not_says "-npu leaves the NPU packages out" "intel-npu-driver"

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

# An invalid K3D_GPU_VENDORS ends host-setup with resolve_vendors' own error.
run "${r}" "" K3D_GPU_VENDORS=bogus
rc_is "invalid K3D_GPU_VENDORS fails" 1
says "invalid K3D_GPU_VENDORS prints its error" "unknown vendor 'bogus' in K3D_GPU_VENDORS"
run "${r}" "" K3D_GPU_VENDORS=auto,intel
rc_is "K3D_GPU_VENDORS mixing auto and a list fails" 1
says "K3D_GPU_VENDORS mixing auto and a list prints its error" "mixes a vendor list with auto or opt-outs"
# resolve_vendors' warnings are about what `up` deploys: host-setup leaves them out,
# here a Gaudi without its driver beside an Intel GPU and a DSA device.
r=$(newroot gaudi-nodriver)
osrel "${r}" ubuntu 24.04
pci "${r}" 0000:00:02.0 0x8086 0x7d55 0x030000 xe
pci "${r}" 0000:4d:00.0 0x1da3 0x1060 0x120000
mkdir -p "${r}/sys/bus/dsa/devices/dsa0"; echo disabled > "${r}/sys/bus/dsa/devices/dsa0/state"
run "${r}" ""
rc_is "a Gaudi without its driver: host-setup still plans" 0
says "a Gaudi without its driver: the Intel GPU is planned" "intel-opencl-icd_"
not_says "host-setup leaves resolve_vendors' warnings out" "skipping Gaudi"
r=$(newroot arch-empty)

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
says "manjaro plan installs only the media set, with -Syu" "    sudo pacman -Syu --needed intel-media-driver libva"
not_says "manjaro: passing compute runtime is not planned" "intel-compute-runtime"
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
says "--plan on a healthy host plans no install" "Every check passed: nothing to install"
not_says "--plan on a healthy host proposes no upgrade" "pacman -Syu"

r=$(manjaro ubuntu-libva 2000 3.4.35 3.4.35)
osrel "${r}" ubuntu 24.04
run "${r}" "" STUB_CLINFO="Platform #0: Intel(R) OpenCL Graphics"
says "ubuntu libva fix uses the pinned PPA libva" "Fix: sudo add-apt-repository -y ppa:kobuk-team/intel-graphics && sudo apt-get install -y libva2=$(ppa HS_PPA_LIBVA 24.04)"

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
# Blind test: grubby printed a literal '<options>' placeholder.
says "fedora grubby line carries the real options" "sudo grubby --update-kernel=ALL --args='xe.force_probe=674c i915.force_probe=!674c xe.force_probe=e20b i915.force_probe=!e20b'"
not_says "no literal <options> placeholder" "<options>"

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

# ---- Xeon DSA/IAA work queues and QAT VFs --------------------------------------

# xeon_accels <root>: Granite Rapids as it boots: DSA dsa0 and IAA iax1 on idxd,
# disabled, no work queues; a 4xxx QAT PF with SR-IOV (16 VFs) but none enabled.
xeon_accels() {
    local d
    mkdir -p "$1/sys/bus/dsa/devices"
    for d in dsa0 iax1; do
        mkdir -p "$1/sys/bus/dsa/devices/${d}"
        echo disabled > "$1/sys/bus/dsa/devices/${d}/state"
        echo 0 > "$1/sys/bus/dsa/devices/${d}/pasid_enabled"
    done
    pci "$1" 0000:01:00.0 0x8086 0x4940 0x0b4000 4xxx
    echo 16 > "$1/sys/devices/pci0000:00/0000:01:00.0/sriov_totalvfs"
    echo 0 > "$1/sys/devices/pci0000:00/0000:01:00.0/sriov_numvfs"
}
AC="sudo accel-config"
DSA_RUN="${AC} config-engine dsa0/engine0.0 --group-id=0 && ${AC} config-wq dsa0/wq0.0 --group-id=0 --type=user --mode=dedicated --wq-size=16 --priority=10 --driver-name=user --name=k3d-gpu && ${AC} enable-device dsa0 && ${AC} enable-wq dsa0/wq0.0"
IAA_RUN="${AC} config-engine iax1/engine1.0 --group-id=0 && ${AC} config-wq iax1/wq1.0 --group-id=0 --type=user --mode=dedicated --wq-size=16 --priority=10 --driver-name=user --name=k3d-gpu && ${AC} enable-device iax1 && ${AC} enable-wq iax1/wq1.0"
QAT_RUN="echo 16 | sudo tee /sys/bus/pci/devices/0000:01:00.0/sriov_numvfs"

r=$(newroot xeon-accels)
osrel "${r}" ubuntu 24.04
xeon_accels "${r}"
run "${r}" ""
rc_is "Xeon with unconfigured accelerators: dry run exits 0" 0
not_says "a Xeon with no GPU is not 'nothing to set up'" "nothing to set up"
says "DSA without work queues is a check that fails" "DSA dsa0: no user work queue"
says "IAA without work queues is a check that fails" "IAA iax1: no user work queue"
says "QAT without VFs is a check that fails" "QAT 0000:01:00.0 (4xxx): no VFs"
says "plan installs accel-config on ubuntu" "sudo apt-get update && sudo apt-get install -y accel-config"
says "plan has the same DSA command doctor prints" "${DSA_RUN}"
says "plan has the same IAA command doctor prints" "${IAA_RUN}"
says "plan enables every QAT VF the PF supports" "${QAT_RUN}"
says "plan loads vfio-pci for the QAT plugin" "sudo modprobe vfio-pci"
says "revert is printed for DSA" "${AC} disable-wq dsa0/wq0.0 && ${AC} disable-device dsa0"
says "revert is printed for IAA" "${AC} disable-wq iax1/wq1.0 && ${AC} disable-device iax1"
says "revert is printed for QAT" "echo 0 | sudo tee /sys/bus/pci/devices/0000:01:00.0/sriov_numvfs"
not_says "accelerator-only plan has no GPU packages" "ca-certificates"
ran_nothing "accelerator dry run calls nothing"

run "${r}" "n" --apply
says "declined accelerator apply says so" "Aborted: nothing was run."
ran_nothing "declined accelerator apply calls nothing"

run "${r}" "y" --apply
rc_is "accelerator apply exits 0" 0
logged "apply installs accel-config" "sudo apt-get install -y accel-config"
logged "apply configures the DSA engine" "sudo accel-config config-engine dsa0/engine0.0 --group-id=0"
logged "apply configures the DSA work queue" "sudo accel-config config-wq dsa0/wq0.0 --group-id=0 --type=user --mode=dedicated --wq-size=16 --priority=10 --driver-name=user --name=k3d-gpu"
logged "apply enables the IAA work queue" "sudo accel-config enable-wq iax1/wq1.0"
logged "apply creates the QAT VFs" "sudo tee /sys/bus/pci/devices/0000:01:00.0/sriov_numvfs"
says "apply prints the revert after running" "revert: echo 0 | sudo tee /sys/bus/pci/devices/0000:01:00.0/sriov_numvfs"

mkdir -p "${r}/usr/bin"; printf '#!/bin/sh\n' > "${r}/usr/bin/accel-config"; chmod +x "${r}/usr/bin/accel-config"
mkdir -p "${r}/sys/bus/pci/drivers/vfio-pci"
run "${r}" ""
not_says "installed accel-config is not reinstalled" "install -y accel-config"
not_says "loaded vfio-pci is not loaded again" "modprobe vfio-pci"

r=$(newroot xeon-accels-pasid)
osrel "${r}" ubuntu 24.04
xeon_accels "${r}"
echo 1 > "${r}/sys/bus/dsa/devices/dsa0/pasid_enabled"
run "${r}" ""
says "a DSA device with user PASID gets a shared WQ in the plan" "${AC} config-wq dsa0/wq0.0 --group-id=0 --type=user --mode=shared --threshold=15 --wq-size=16"
run "${r}" "" K3D_GPU_VENDORS=qat
says "K3D_GPU_VENDORS=qat plans the QAT VFs" "${QAT_RUN}"
not_says "K3D_GPU_VENDORS=qat leaves DSA/IAA out" "accel-config"
run "${r}" "" K3D_GPU_VENDORS=dsa
says "K3D_GPU_VENDORS=dsa plans DSA" "${AC} config-engine dsa0/engine0.0"
not_says "K3D_GPU_VENDORS=dsa leaves IAA out" "iax1"
not_says "K3D_GPU_VENDORS=dsa leaves QAT out" "sriov_numvfs"
# Opt-outs subtract from auto detection: the rest is still planned.
run "${r}" "" K3D_GPU_VENDORS=auto,-qat
says "auto,-qat still plans DSA" "${AC} config-engine dsa0/engine0.0"
not_says "auto,-qat leaves QAT out" "sriov_numvfs"

# iax1 held by an enabled kernel WQ on iaa_crypto (crescent's state), dsa0 untouched.
r=$(newroot xeon-kernel-wq)
osrel "${r}" ubuntu 24.04
mkdir -p "${r}/usr/bin"; printf '#!/bin/sh\n' > "${r}/usr/bin/accel-config"; chmod +x "${r}/usr/bin/accel-config"
B="${r}/sys/bus/dsa/devices"
mkdir -p "${B}/dsa0" "${B}/iax1" "${B}/wq1.0" "${B}/wq1.1" "${B}/engine1.0" "${B}/group1.0" "${r}/sys/bus/dsa/drivers/crypto"
echo disabled > "${B}/dsa0/state"; echo enabled > "${B}/iax1/state"; echo 128 > "${B}/iax1/max_work_queues_size"
printf 'enabled\n' > "${B}/wq1.0/state"; echo 32 > "${B}/wq1.0/size"; echo 0 > "${B}/wq1.0/group_id"
ln -sfn "${r}/sys/bus/dsa/drivers/crypto" "${B}/wq1.0/driver"
printf 'disabled\n' > "${B}/wq1.1/state"; echo 0 > "${B}/wq1.1/size"; echo -1 > "${B}/wq1.1/group_id"
echo 0 > "${B}/engine1.0/group_id"
KWQ_RUN="${AC} disable-device iax1 && ${AC} load-config -c /var/lib/k3d-gpu/idxd-iax1.conf && ${AC} config-wq iax1/wq1.1 --group-id=0 --type=user --mode=dedicated --wq-size=16 --priority=10 --driver-name=user --name=k3d-gpu && ${AC} enable-device iax1 && ${AC} enable-wq iax1/wq1.0 && ${AC} enable-wq iax1/wq1.1"
KWQ_REV="${AC} disable-device iax1 && ${AC} load-config -c /var/lib/k3d-gpu/idxd-iax1.conf && ${AC} enable-device iax1 && ${AC} enable-wq iax1/wq1.0"
run "${r}" ""
says "kernel-WQ device is noted, not planned, without the opt-in" "note: IAA iax1: enabled with kernel work queues only — left alone; add --with-kernel-wqs"
not_says "no kernel-WQ commands without the opt-in" "disable-device iax1"
says "the untouched DSA device is still planned" "${AC} config-engine dsa0/engine0.0 --group-id=0"
run "${r}" "y" --apply
! grep -q 'iax1' "${STUB_LOG}"; check "apply without the opt-in never touches the kernel-WQ device" $? "iax1 in the call log"
run "${r}" "" --with-kernel-wqs
says "--with-kernel-wqs plans the snapshot of every device" "sudo install -d -m 0700 /var/lib/k3d-gpu && ${AC} save-config -s /var/lib/k3d-gpu/idxd-all.conf && sudo python3 -c"
says "--with-kernel-wqs keeps only this device's snapshot" "/var/lib/k3d-gpu/idxd-all.conf iax1 /var/lib/k3d-gpu/idxd-iax1.conf && ${KWQ_RUN}"
says "--with-kernel-wqs prints the snapshot revert" "revert: ${KWQ_REV}"
run "${r}" "y" --apply --with-kernel-wqs
rc_is "opt-in apply exits 0" 0
s=$(grep -n 'save-config -s /var/lib/k3d-gpu/idxd-all.conf' "${STUB_LOG}" | cut -d: -f1)
d=$(grep -n 'disable-device iax1' "${STUB_LOG}" | cut -d: -f1)
l=$(grep -n 'load-config -c /var/lib/k3d-gpu/idxd-iax1.conf' "${STUB_LOG}" | cut -d: -f1)
ordered=1
[ -n "${s}" ] && [ -n "${d}" ] && [ -n "${l}" ] && [ "${s}" -lt "${d}" ] && [ "${d}" -lt "${l}" ] && ordered=0
check "opt-in apply snapshots, then disables, then writes the snapshot back" "${ordered}" "order save=${s} disable=${d} load=${l}"
logged "opt-in apply disables the device" "sudo accel-config disable-device iax1"
logged "opt-in apply writes the snapshot back" "sudo accel-config load-config -c /var/lib/k3d-gpu/idxd-iax1.conf"
logged "opt-in apply re-enables the kernel WQ" "sudo accel-config enable-wq iax1/wq1.0"
logged "opt-in apply enables the user WQ" "sudo accel-config enable-wq iax1/wq1.1"
run "${r}" "" --bogus-flag
rc_is "unknown host-setup flag still fails" 1
rm -rf "${B}/dsa0"
run "${r}" "y" --apply
says "only a held kernel-WQ device: nothing to install, and the note says why" "note: IAA iax1: enabled with kernel work queues only — left alone"
ran_nothing "only a held kernel-WQ device: nothing is called"

r=$(newroot xeon-accels-fedora)
osrel "${r}" fedora 44
xeon_accels "${r}"
run "${r}" ""
says "fedora installs accel-config from its repo" "sudo dnf install -y accel-config"

r=$(newroot xeon-accels-arch)
osrel "${r}" arch
xeon_accels "${r}"
run "${r}" ""
says "arch has no accel-config package and says how to get it" "accel-config is not packaged for arch"
not_says "arch plan leaves out DSA/IAA commands it cannot run" "config-engine dsa0"
says "arch plan still enables QAT VFs" "${QAT_RUN}"

r=$(newroot xeon-accels-ready)
osrel "${r}" ubuntu 24.04
xeon_accels "${r}"
mkdir -p "${r}/dev/dsa" "${r}/dev/iax"; touch "${r}/dev/dsa/wq0.0" "${r}/dev/iax/wq1.0"
mkdir -p "${r}/sys/devices/pci0000:00/0000:01:01.0"; ln -sfn "${r}/sys/devices/pci0000:00/0000:01:01.0" "${r}/sys/devices/pci0000:00/0000:01:00.0/virtfn0"
run "${r}" ""
says "configured accelerators: nothing to set up" "nothing to set up"
not_says "configured accelerators get no accel-config steps" "accel-config"

# ---- gated plans: crescent (Ubuntu 26.04, Xeon 6741P, 4x Arc Pro B70 on xe) ---
# Every userspace check passes and iaa_crypto's kernel WQ claims all 256 WQ
# entries of iax1. No accel-config on the host. A plan installs only what a
# failing check names, from the detected release's own pinned set.

# crescent <name>: the fake root; Intel userspace in place under the multiarch dir.
crescent() {
    local r i B
    r=$(newroot "$1")
    osrel "${r}" ubuntu 26.04
    kernel "${r}" 7.0.0-31-generic
    for i in 18 3a 5c 7e; do pci "${r}" "0000:${i}:00.0" 0x8086 0xe223 0x030000 xe; done
    LIBDIR=usr/lib/x86_64-linux-gnu lib "${r}" libstdc++.so.6 "GLIBCXX_3.4.34"
    LIBDIR=usr/lib/x86_64-linux-gnu lib "${r}" intel-opencl/libigdrcl.so "GLIBCXX_3.4.30"
    LIBDIR=usr/lib/x86_64-linux-gnu lib "${r}" libze_intel_gpu.so.1 "GLIBCXX_3.4.30"
    LIBDIR=usr/lib/x86_64-linux-gnu lib "${r}" libze_loader.so.1 "x"
    LIBDIR=usr/lib/x86_64-linux-gnu lib "${r}" dri/iHD_drv_video.so "__vaDriverInit_1_22 GLIBCXX_3.4.30"
    LIBDIR=usr/lib/x86_64-linux-gnu lib "${r}" libva.so.2.2200.0 "x"
    mkdir -p "${r}/etc/OpenCL/vendors"
    echo /usr/lib/x86_64-linux-gnu/intel-opencl/libigdrcl.so > "${r}/etc/OpenCL/vendors/intel.icd"
    B="${r}/sys/bus/dsa/devices"
    mkdir -p "${B}/iax1" "${r}/sys/bus/dsa/drivers/crypto"
    echo enabled > "${B}/iax1/state"; echo 256 > "${B}/iax1/max_work_queues_size"; echo 0 > "${B}/iax1/pasid_enabled"
    mkdir -p "${B}/wq1.0"; echo enabled > "${B}/wq1.0/state"; echo 256 > "${B}/wq1.0/size"; echo 0 > "${B}/wq1.0/group_id"
    echo dedicated > "${B}/wq1.0/mode"
    ln -sfn "${r}/sys/bus/dsa/drivers/crypto" "${B}/wq1.0/driver"
    for i in 1 2 3 4 5 6 7; do
        mkdir -p "${B}/wq1.${i}"; echo disabled > "${B}/wq1.${i}/state"; echo 0 > "${B}/wq1.${i}/size"; echo -1 > "${B}/wq1.${i}/group_id"
    done
    for i in 0 1 2 3 4 5 6 7; do mkdir -p "${B}/engine1.${i}"; echo 0 > "${B}/engine1.${i}/group_id"; done
    for i in 0 1 2 3; do mkdir -p "${B}/group1.${i}"; done
    echo "${r}"
}
CR_OK=(STUB_VAINFO="vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 25.2.3 ()"
       STUB_CLINFO="Platform #0: Intel(R) OpenCL Graphics")
IAA_SHRINK="${AC} disable-device iax1 && ${AC} load-config -c /var/lib/k3d-gpu/idxd-iax1.conf && ${AC} config-wq iax1/wq1.0 --wq-size=240 && ${AC} config-wq iax1/wq1.1 --group-id=0 --type=user --mode=dedicated --wq-size=16 --priority=10 --driver-name=user --name=k3d-gpu && ${AC} enable-device iax1 && ${AC} enable-wq iax1/wq1.0 && ${AC} enable-wq iax1/wq1.1"

r=$(crescent crescent)
run "${r}" "" --plan --with-kernel-wqs "${CR_OK[@]}"
rc_is "crescent --plan exits 0" 0
says "crescent: the release is read as 26.04" "Host setup for ubuntu-26.04"
says "crescent: Level Zero loader ok" "ok: Level Zero loader: libze_loader.so.1"
says "crescent: clinfo ok" "ok: clinfo: Intel(R) OpenCL Graphics"
says "crescent: vainfo ok" "ok: vainfo: Intel iHD driver"
not_says "crescent: no check failed" "fail"
not_says "crescent: no install plan when every check passed" "Install plan"
not_says "crescent: no apt install when every check passed" "    sudo apt-get"
not_says "crescent: no PPA when the media checks passed" "add-apt-repository"
not_says "crescent: no compute-runtime download when its checks passed" "compute-runtime/releases"
not_says "crescent: no 24.04 .debs on 26.04" "u24.04"
not_says "crescent: accel-config is not planned when nothing in the plan needs it" "    sudo apt-get update && sudo apt-get install -y accel-config"
says "crescent: the plan says it installs nothing" "Every check passed: nothing to install"
says "crescent: IAA full claim is named" "IAA iax1: kernel WQ(s) wq1.0 (crypto) hold it with 256 of 256 WQ entries claimed, so no user WQ fits beside them"
says "crescent: IAA suggestion shrinks the kernel WQ and adds the user WQ" "apply:  sudo install -d -m 0700 /var/lib/k3d-gpu && ${AC} save-config -s /var/lib/k3d-gpu/idxd-all.conf && sudo python3 -c"
says "crescent: IAA suggestion carries the shrink" "/var/lib/k3d-gpu/idxd-all.conf iax1 /var/lib/k3d-gpu/idxd-iax1.conf && ${IAA_SHRINK}"
says "crescent: IAA suggestion has its revert" "revert: ${AC} disable-device iax1 && ${AC} load-config -c /var/lib/k3d-gpu/idxd-iax1.conf && ${AC} enable-device iax1 && ${AC} enable-wq iax1/wq1.0"
says "crescent: IAA suggestion is never applied" "never applied by host-setup"
says "crescent: IAA suggestion names what it needs" "needs accel-config: sudo apt-get update && sudo apt-get install -y accel-config"
run "${r}" "y" --apply --with-kernel-wqs "${CR_OK[@]}"
rc_is "crescent --apply exits 0" 0
ran_nothing "crescent --apply runs nothing (the IAA shrink is a suggestion only)"
run "${r}" "" "${CR_OK[@]}"
says "crescent without --plan: nothing to install" "Every check passed: nothing to install"

# One failing check: only its own package set.
rm "${r}/usr/lib/x86_64-linux-gnu/libze_intel_gpu.so.1"
run "${r}" "" "${CR_OK[@]}"
says "missing Level Zero GPU driver plans the 26.35 compute runtime" "releases/download/26.35.39758.10/libze-intel-gpu1_26.35.39758.10-0_amd64.deb"
# The 26.35 .debs depend on libigdgmm12 (>= 22.10.0); the 24.04 and 26.04
# archives carry 22.3.17 and 22.9.0, so the PPA's gmmlib comes first.
says "missing Level Zero GPU driver adds the PPA for its gmmlib" "    sudo add-apt-repository -y ppa:kobuk-team/intel-graphics"
says "missing Level Zero GPU driver: the PPA's tool is a prerequisite" "software-properties-common"
says "missing Level Zero GPU driver keeps apt on the GitHub .debs" "Pin: release o=LP-PPA-kobuk-team-intel-graphics"
says "missing Level Zero GPU driver pins the PPA's 26.04 gmmlib" "libigdgmm12=$(ppa HS_PPA_GMM 26.04)"
not_says "missing Level Zero GPU driver: no media driver" "intel-media-va-driver-non-free"
not_says "missing Level Zero GPU driver: no libva" "libva2="
a=$(grep -n 'add-apt-repository -y ppa:kobuk-team' <<< "${OUT}" | head -1 | cut -d: -f1)
g=$(grep -n "apt-get install -y.* libigdgmm12=" <<< "${OUT}" | head -1 | cut -d: -f1)
d=$(grep -n "apt-get install -y.*libze-intel-gpu1_" <<< "${OUT}" | head -1 | cut -d: -f1)
ordered=1
[ -n "${a}" ] && [ -n "${g}" ] && [ -n "${d}" ] && [ "${a}" -lt "${g}" ] && [ "${g}" -lt "${d}" ] && ordered=0
check "missing Level Zero GPU driver: PPA, then its gmmlib, then the GitHub .debs" "${ordered}" "order ppa=${a} gmmlib=${g} debs=${d}"
not_says "missing Level Zero GPU driver: no loader .deb" "libze1_"
not_says "missing Level Zero GPU driver: no NVIDIA" "nvidia"
LIBDIR=usr/lib/x86_64-linux-gnu lib "${r}" libze_intel_gpu.so.1 "GLIBCXX_3.4.30"

run "${r}" "" STUB_VAINFO="libva error: /usr/lib/x86_64-linux-gnu/dri/iHD_drv_video.so has no function __vaDriverInit_1_0" "${CR_OK[1]}"
says "media failure on 26.04 plans the PPA's 26.04 build" "intel-media-va-driver-non-free=$(ppa HS_PPA_MEDIA 26.04)"
says "media failure pins the 26.04 libva" "libva2=$(ppa HS_PPA_LIBVA 26.04)"
says "media failure pins the 26.04 gmmlib" "libigdgmm12=$(ppa HS_PPA_GMM 26.04)"
not_says "media failure plans no compute runtime" "intel-opencl-icd_"
not_says "media failure plans no 24.04 package" "24.04"

rm "${r}/usr/lib/x86_64-linux-gnu/libze_loader.so.1"
run "${r}" "" "${CR_OK[@]}"
says "26.04 has no pinned loader .deb and says so" "no pinned Level Zero loader for ubuntu-26.04"
not_says "26.04 never gets the u24.04 loader" "libze1_1.34.0+u24.04"

# accel-config: planned only when a planned DSA/IAA step needs it and it is missing.
r=$(crescent crescent-dsa)
mkdir -p "${r}/sys/bus/dsa/devices/dsa0"; echo disabled > "${r}/sys/bus/dsa/devices/dsa0/state"; echo 0 > "${r}/sys/bus/dsa/devices/dsa0/pasid_enabled"
run "${r}" "" "${CR_OK[@]}"
says "missing accel-config is a failing check" "accel-config not installed"
says "accel-config comes from the 26.04 archive" "sudo apt-get update && sudo apt-get install -y accel-config"
mkdir -p "${r}/usr/local/bin"; printf '#!/bin/sh\n' > "${r}/usr/local/bin/accel-config"; chmod +x "${r}/usr/local/bin/accel-config"
run "${r}" "" "${CR_OK[@]}"
says "accel-config under /usr/local/bin counts" "ok: accel-config: /usr/local/bin/accel-config"
not_says "installed accel-config is not planned" "install -y accel-config"

# Release detection and releases without a pinned set.
r=$(crescent codename)
printf 'NAME="Ubuntu"\nID=ubuntu\nVERSION_CODENAME=resolute\n' > "${r}/etc/os-release"
run "${r}" "" "${CR_OK[@]}"
says "release from VERSION_CODENAME when VERSION_ID is missing" "Host setup for ubuntu-26.04"

r=$(newroot ubuntu2510)
osrel "${r}" ubuntu 25.10
pci "${r}" 0000:03:00.0 0x8086 0xe20b 0x030000 xe
run "${r}" ""
rc_is "an Ubuntu release without a pinned set still exits 0" 0
says "an Ubuntu release without a pinned set says so" "no pinned package set for ubuntu-25.10 (pinned: ubuntu-24.04, ubuntu-26.04)"
not_says "no pinned set: no PPA" "add-apt-repository"
not_says "no pinned set: no .debs" ".deb"
not_says "no pinned set: no apt install" "apt-get install"

# Toolkit installed, CDI spec missing: only the spec, guarded for a toolkit that
# predates the nvidia-cdi-refresh units; no reinstall, no Docker restart.
r=$(newroot ubuntu2404-cdi-only)
osrel "${r}" ubuntu 24.04
pci "${r}" 0000:01:00.0 0x10de 0x27b0 0x030000
mkdir -p "${r}/usr/bin"; printf '#!/bin/sh\n' > "${r}/usr/bin/nvidia-ctk"; chmod +x "${r}/usr/bin/nvidia-ctk"
run "${r}" ""
says "CDI-only: the spec step falls back to cdi generate without the refresh units" "    if [ -e '${r}/usr/lib/systemd/system/nvidia-cdi-refresh.service' ]; then sudo systemctl enable --now nvidia-cdi-refresh.path"
not_says "CDI-only: installed toolkit is not reinstalled" "nvidia-container-toolkit="
not_says "CDI-only: docker is not restarted" "systemctl restart docker"
says "CDI-only: one command" "Install plan (1 commands)"

# No media driver and no vainfo: both planned in one run.
r=$(newroot ubuntu2404-novainfo)
osrel "${r}" ubuntu 24.04
pci "${r}" 0000:00:02.0 0x8086 0x7d55 0x030000 xe
# A PATH with every tool of this one except vainfo.
NOVA="${WORK}/novainfo-bin"; mkdir -p "${NOVA}"
IFS=: read -r -a dirs <<< "${STUB_BIN}:${PATH}"
for d in "${dirs[@]}"; do
    for f in "${d}"/*; do
        [ -x "${f}" ] && [ "${f##*/}" != vainfo ] && [ ! -e "${NOVA}/${f##*/}" ] && ln -s "${f}" "${NOVA}/${f##*/}"
    done
done
OUT=$(env K3D_GPU_SYSROOT="${r}" K3D_GPU_SETUP_DIR="${WORK}/dl" PATH="${NOVA}" bash "${K3D_GPU}" host-setup 2>&1)
says "missing media driver and vainfo: vainfo is planned with the driver" "libigdgmm12=$(ppa HS_PPA_GMM 24.04) vainfo"

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
