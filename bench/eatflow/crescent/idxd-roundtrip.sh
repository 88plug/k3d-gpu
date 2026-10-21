#!/usr/bin/env bash
# idxd-roundtrip.sh DEV [OUT]: H52b's first real-hardware check on crescent. Does accel-config
# v4.1.9's save-config / load-config round-trip an enabled IAA (or DSA) device whose kernel work
# queues serve iaa_crypto, byte for byte? It runs exactly the revert half of k3d-gpu's
# `host-setup --apply --with-kernel-wqs` (scripts/k3d-gpu accel_enable_steps, class kernel) on
# the device as found: save-config, keep DEV's entry, disable-device, load-config, enable-device
# and each kernel WQ that was enabled. It adds no user WQ. Before and after it records
# `accel-config list -i`, every readable sysfs attribute of DEV and its WQs, groups and engines,
# the wq-to-driver bindings, /proc/crypto's deflate-iaa entry and the idxd/iaa dmesg lines, then
# diffs them. Exit 0: identical. Exit 1: differs (do not use --with-kernel-wqs). Exit 2: refused.
#
# accel-config is not installed on crescent; the Ubuntu 26.04 package 4.1.9-2 (the version
# k3d-gpu's steps are written against) is unpacked under ~/k3dgpu-bench/opt/accel-config with
# `apt-get download` + `dpkg-deb -x` (no system install). Runs under the campaign lock.
set -euo pipefail
DEV=${1:?usage: idxd-roundtrip.sh DEV [OUT]}
[[ "${DEV}" =~ ^(iax|dsa)[0-9]+$ ]] || { echo "DEV must be iaxN or dsaN"; exit 2; }
B=${HOME}/k3dgpu-bench
OUT=${2:-${B}/results/H52b/roundtrip-${DEV}}
P=${B}/opt/accel-config
VER=4.1.9-2
S=/sys/bus/dsa/devices
mkdir -p "${OUT}"

if [ ! -x "${P}/usr/bin/accel-config" ]; then
    mkdir -p "${B}/opt/debs" "${P}"
    (cd "${B}/opt/debs" && apt-get download "accel-config=${VER}" "libaccel-config1=${VER}")
    for d in "${B}"/opt/debs/*accel-config*_"${VER}"_amd64.deb; do dpkg-deb -x "${d}" "${P}"; done
fi
sha256sum "${B}"/opt/debs/*.deb > "${OUT}/debs.sha256"
AC=(sudo -n env "LD_LIBRARY_PATH=${P}/usr/lib/x86_64-linux-gnu" "${P}/usr/bin/accel-config")
"${AC[@]}" --version > "${OUT}/accel-config-version.txt"
grep -q '^4\.1\.9' "${OUT}/accel-config-version.txt" || { echo "accel-config is not 4.1.9"; exit 2; }

# zswap holds the IAA device while its compressor is deflate-iaa (scripts/k3d-gpu refuses too).
zs=$(cat /sys/module/zswap/parameters/compressor 2>/dev/null || true)
[ "${zs}" != deflate-iaa ] || { echo "zswap compressor is deflate-iaa: refused"; exit 2; }
[ "$(cat "${S}/${DEV}/state")" = enabled ] || { echo "${DEV} is not enabled"; exit 2; }
id=${DEV#iax}; id=${id#dsa}
kwqs=()
for w in "${S}"/wq"${id}".*; do
    [ "$(cat "${w}/state")" = enabled ] && [ "$(cat "${w}/type")" = kernel ] && kwqs+=("${w##*/}")
done
[ "${#kwqs[@]}" -gt 0 ] || { echo "${DEV} has no enabled kernel WQ"; exit 2; }

snapshot() {    # snapshot TAG
    "${AC[@]}" list -i > "${OUT}/$1-list.json"
    for o in "${S}/${DEV}" "${S}"/wq"${id}".* "${S}"/group"${id}".* "${S}"/engine"${id}".*; do
        for a in "${o}"/*; do
            [ -f "${a}" ] && [ -r "${a}" ] || continue
            printf '%s/%s=%s\n' "${o##*/}" "${a##*/}" "$(tr '\n' ' ' < "${a}" 2>/dev/null || echo '<unreadable>')"
        done
        [ -L "${o}/driver" ] && printf '%s/driver->%s\n' "${o##*/}" "$(basename "$(readlink "${o}/driver")")"
    done | LC_ALL=C sort > "${OUT}/$1-sysfs.txt"
    awk '/^name/ {p = 0} /^driver *: deflate-iaa/ {p = 1} p' /proc/crypto > "${OUT}/$1-crypto.txt"
    lsmod | awk '$1 == "iaa_crypto" || $1 == "idxd"' > "${OUT}/$1-lsmod.txt"
    sudo -n dmesg -T | grep -iE 'idxd|iaa' | tail -100 > "${OUT}/$1-dmesg.txt" || true
}

exec 9> "${HOME}/k3dgpu-bench.lock"
flock -w 7200 9 || { echo "lock not taken"; exit 2; }
snapshot before
"${AC[@]}" save-config -s "${OUT}/idxd-all.conf"
python3 - "${OUT}/idxd-all.conf" "${DEV}" "${OUT}/idxd-${DEV}.conf" <<'EOF'
import json, sys
a = json.load(open(sys.argv[1]))
a = a if isinstance(a, list) else [a]
s = [x for x in a if x.get("dev") == sys.argv[2]]
s or sys.exit("no " + sys.argv[2] + " in " + sys.argv[1])
json.dump(s, open(sys.argv[3], "w"), indent=1)
EOF
# Any failure from here on: put the snapshot back and re-enable, then say so (exit 3).
restore() {
    echo "FAILED mid-cycle: restoring ${DEV} from ${OUT}/idxd-${DEV}.conf" | tee "${OUT}/restore.txt"
    "${AC[@]}" disable-device "${DEV}" || true
    "${AC[@]}" load-config -c "${OUT}/idxd-${DEV}.conf" || true
    "${AC[@]}" enable-device "${DEV}" || true
    for k in "${kwqs[@]}"; do "${AC[@]}" enable-wq "${DEV}/${k}" || true; done
    snapshot after-restore
    exit 3
}
trap restore ERR
t0=$(date +%s.%N)
"${AC[@]}" disable-device "${DEV}"
"${AC[@]}" load-config -c "${OUT}/idxd-${DEV}.conf"
"${AC[@]}" enable-device "${DEV}"
for k in "${kwqs[@]}"; do "${AC[@]}" enable-wq "${DEV}/${k}"; done
t1=$(date +%s.%N)
trap - ERR
python3 -c "print(round($t1 - $t0, 3))" > "${OUT}/disabled-seconds.txt"
snapshot after
rc=0
for f in list.json sysfs.txt crypto.txt lsmod.txt; do
    if ! diff -u "${OUT}/before-${f}" "${OUT}/after-${f}" > "${OUT}/diff-${f}.txt"; then rc=1; fi
done
diff -u "${OUT}/before-dmesg.txt" "${OUT}/after-dmesg.txt" > "${OUT}/diff-dmesg.txt" || true
echo "rc=${rc} kernel_wqs=${kwqs[*]} disabled_s=$(cat "${OUT}/disabled-seconds.txt")" | tee "${OUT}/result.txt"
exit "${rc}"
