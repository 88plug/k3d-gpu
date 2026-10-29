#!/usr/bin/env bash
# xhost_tools_test.sh: bench/xhost/tools.sh installs exactly k3d v5.9.0 and kubectl v1.37.2,
# each checked against its checksum file, into ~/k3dgpu-xhost/bin (EXPERIMENTS.md cross-host
# amendment K1). curl is a shim that serves fake binaries with matching checksum files and logs
# every URL; each fake binary logs its arguments. Real sha256sum and install. No network, no root.
#
# Usage: tests/xhost_tools_test.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS=${ROOT}/bench/xhost/tools.sh
PASS=0 FAILS=0
T=$(mktemp -d)
trap 'rm -rf "${T}"' EXIT
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }
check() { local name=$1; shift; if "$@"; then ok "${name}"; else fail "${name}"; fi; }

mkdir -p "${T}/shims"
# curl -fsSLo FILE URL. Each binary is built once per URL under $SHIM_DIR/art, so the checksum
# file served later matches it. SHIM_BAD=<tool> serves a wrong checksum for that tool.
cat > "${T}/shims/curl" <<'EOF'
#!/usr/bin/env bash
set -eu
out= url=
while [ $# -gt 0 ]; do case $1 in -fsSLo) out=$2; shift 2 ;; *) url=$1; shift ;; esac; done
echo "${url}" >> "${SHIM_DIR}/curl.log"
mkdir -p "${SHIM_DIR}/art"
art() { # art TOOL URL: the fake binary's path, built on first use
    local f
    f=${SHIM_DIR}/art/$(printf '%s' "$2" | sha256sum | cut -c1-16)
    [ -e "${f}" ] || printf '#!/bin/sh\necho "%s %s"\necho "%s $*" >> "${SHIM_DIR}/run.log"\n' "$1" "$2" "$1" > "${f}"
    echo "${f}"
}
sum() { # sum TOOL URL: the binary's sha256, or a wrong one for SHIM_BAD
    if [ "${SHIM_BAD:-}" = "$1" ]; then printf '%064d' 0; else sha256sum "$(art "$1" "$2")" | cut -d' ' -f1; fi
}
case ${url} in
    */checksums.txt) echo "$(sum k3d "${url%/checksums.txt}/k3d-linux-amd64")  _dist/k3d-linux-amd64" > "${out}" ;;
    */k3d-linux-amd64) cp "$(art k3d "${url}")" "${out}" ;;
    */kubectl.sha256) sum kubectl "${url%.sha256}" > "${out}" ;;
    */bin/linux/amd64/kubectl) cp "$(art kubectl "${url}")" "${out}" ;;
    *) echo "curl shim: no artifact for ${url}" >&2; exit 22 ;;
esac
EOF
chmod +x "${T}/shims/curl"

# run: tools.sh in a fresh HOME with the shim first on PATH; sets RC, OUT, H, S.
run() {
    H=$(mktemp -d "${T}/home.XXXX") S=$(mktemp -d "${T}/shim.XXXX")
    : > "${S}/curl.log"; : > "${S}/run.log"
    mkdir -p "${H}/k3dgpu-xhost/runs" "${H}/k3dgpu-bench/bin"
    echo keep > "${H}/k3dgpu-xhost/runs/campaign.log"
    OUT=$(HOME=${H} SHIM_DIR=${S} PATH="${T}/shims:${PATH}" bash "${TOOLS}" 2>&1)
    RC=$?
}
has()  { grep -q -- "$1" "$2"; }
bins() { (cd "$1" 2>/dev/null && ls | sort | tr '\n' ' '); }
leftover_dl() { compgen -G "$1/tools-dl.*" > /dev/null; }

run
check "tools.sh exits 0" [ "${RC}" -eq 0 ]
check "it installs k3d and kubectl only" [ "$(bins "${H}/k3dgpu-xhost/bin")" = "k3d kubectl " ]
check "it fetches kubectl v1.37.2" has '^https://dl.k8s.io/release/v1.37.2/bin/linux/amd64/kubectl$' "${S}/curl.log"
check "it fetches kubectl's checksum file" has '^https://dl.k8s.io/release/v1.37.2/bin/linux/amd64/kubectl.sha256$' "${S}/curl.log"
check "it fetches k3d v5.9.0 and its checksums" has 'k3d-io/k3d/releases/download/v5.9.0/checksums.txt$' "${S}/curl.log"
check "it fetches four files, nothing else" [ "$(wc -l < "${S}/curl.log")" -eq 4 ]
check "the kubectl it installs is the one it fetched" has 'kubectl https://dl.k8s.io/release/v1.37.2/' <(printf '%s\n' "${OUT}")
check "it prints both versions" eval 'has "^k3d version$" "${S}/run.log" && has "^kubectl version --client$" "${S}/run.log"'
check "it leaves no download dir" eval '! leftover_dl "${H}/k3dgpu-xhost"'
check "it keeps the cross-host records" [ "$(cat "${H}/k3dgpu-xhost/runs/campaign.log")" = keep ]
check "it does not touch the crescent workspace" [ -z "$(ls -A "${H}/k3dgpu-bench/bin")" ]

# A second run replaces the binaries in place.
HOME_FIRST=${H}
OUT=$(HOME=${HOME_FIRST} SHIM_DIR=${S} PATH="${T}/shims:${PATH}" bash "${TOOLS}" 2>&1); RC=$?
check "a second run exits 0" [ "${RC}" -eq 0 ]

for bad in kubectl k3d; do
    SHIM_BAD=${bad} run
    check "a ${bad} checksum mismatch fails" [ "${RC}" -ne 0 ]
    check "a ${bad} checksum mismatch installs nothing" [ -z "$(ls -A "${H}/k3dgpu-xhost/bin")" ]
    check "a ${bad} checksum mismatch leaves no download dir" eval '! leftover_dl "${H}/k3dgpu-xhost"'
done

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
