#!/usr/bin/env bash
# Runs detect_kubernetes_platform() from enterprise-agent-toolkit's
# core/lib/brownfield/brownfield_deployment.sh against a stub kubectl that
# reports a chosen serverVersion.gitVersion. No cluster needed.
#
# Usage: integrations/eat/upstream/checks/eat-detect-platform.sh <eat-checkout>
# Exit 0 when every case matches. Unpatched upstream fails the two +k3s cases.

set -uo pipefail

EAT=${1:?usage: $0 <enterprise-agent-toolkit checkout>}
LIB="${EAT}/core/lib/brownfield/brownfield_deployment.sh"
[ -f "${LIB}" ] || { echo "not found: ${LIB}" >&2; exit 2; }
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
FAILS=0

mkdir -p "${WORK}/bin"
cat > "${WORK}/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  version)
    printf '{"clientVersion":{"major":"1","minor":"34","gitVersion":"v1.34.3","gitCommit":"0123456789abcdef","gitTreeState":"clean","goVersion":"go1.24.9","compiler":"gc","platform":"linux/amd64"},"kustomizeVersion":"v5.7.1","serverVersion":{"major":"1","minor":"%s","gitVersion":"%s","gitCommit":"fedcba9876543210","gitTreeState":"clean","goVersion":"go1.24.9","compiler":"gc","platform":"linux/amd64"}}\n' "${STUB_MINOR}" "${STUB_GITVERSION}" ;;
  api-resources) exit 0 ;;
  get) echo "Ubuntu 24.04.3 LTS" ;;
  *) exit 1 ;;
esac
EOF
chmod +x "${WORK}/bin/kubectl"

# check <gitVersion> <minor> <want>: PROMPTED means the interactive prompt ran.
check() {
    local got
    # shellcheck disable=SC2016  # $0 and $kubernetes_platform expand in the child shell
    got=$(PATH="${WORK}/bin:${PATH}" STUB_GITVERSION=$1 STUB_MINOR=$2 bash -c '
        source "$0"
        prompt_kubernetes_platform() { kubernetes_platform=PROMPTED; }
        detect_kubernetes_platform >/dev/null
        echo "${kubernetes_platform}"' "${LIB}" </dev/null 2>&1)
    if [ "${got}" = "$3" ]; then
        printf 'ok   %-18s -> %s\n' "$1" "${got}"
    else
        printf 'FAIL %-18s -> %s (want %s)\n' "$1" "${got}" "$3"
        FAILS=$((FAILS + 1))
    fi
}

check v1.34.3        34  vanilla
check v1.34.12+k3s1  34  vanilla
check v1.37.1+k3s12  37  vanilla
check v1.34.2-eks-1  34+ eks
check v1.33.5-gke.10 33+ gke
check v1.34.12+k3sX  34  PROMPTED
check v1.34.1+rke2r1 34  PROMPTED

[ "${FAILS}" -eq 0 ]
