#!/usr/bin/env bash
# ei-kcadm.sh — enterprise-inference's keycloak_config role runs kcadm in the Keycloak pod
# through k8s_exec, with the Keycloak admin password (`config credentials --password`) and
# the inference client secret (`update clients/<id> -s secret=`) as arguments
# (EI v1.1.0 d035b1e roles/keycloak_config/tasks/install.yaml#L167-L196; EI 3e8a244
# #L166-L206). A process's arguments are in `ps` for every user on the node, and a k3d
# node is a container on the host. patches/ei/0001-*.patch have kcadm read the password
# from KC_CLI_PASSWORD, copied in the pod from the KC_BOOTSTRAP_ADMIN_PASSWORD the
# Keycloak operator sets, and have Keycloak generate the client secret, which kcadm
# prints. One variant per shape of the task:
#   a  0001-keycloak-config-kcadm-argv.patch              EI main (3e8a244)
#   b  0001-release-1.1-keycloak-config-kcadm-argv.patch  EI release-1.1 (v1.1.0 d035b1e;
#                                                         170eb3a has the same role)
# With auth_provider litellm (EAS's agent-toolkit manifest) EI's components.yaml turns
# keycloak_config off, so the patched task does not run there; the inference layer runs it.
#
#   ei-kcadm.sh apply <EI checkout>
#       patch roles/keycloak_config/tasks/install.yaml with the first variant that fits
#       (nothing to do when it already carries one), then check that no kcadm call in the
#       checkout passes a secret as an argument. Exits 1, the checkout left as it was,
#       when the task is none of the ones the variants were cut against.
#   ei-kcadm.sh revert <EI checkout>
#       take the variant out again (nothing to do without one), so EAS's `init --upgrade`,
#       which refuses an ext/ repo with changes to tracked files, can move the checkout.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCHES=("${HERE}/patches/ei/0001-keycloak-config-kcadm-argv.patch"
         "${HERE}/patches/ei/0001-release-1.1-keycloak-config-kcadm-argv.patch")
TASK="roles/keycloak_config/tasks/install.yaml"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

mode=${1:-} ei=${2:-}
if [ -z "${ei}" ] || ! git -C "${ei}" rev-parse --git-dir >/dev/null 2>&1; then
    err "usage: ei-kcadm.sh apply|revert <enterprise-inference git checkout>"
fi
for p in "${PATCHES[@]}"; do [ -f "${p}" ] || err "${p} missing"; done

# carried: the variant the checkout already carries, or nothing.
carried() {
    local p
    for p in "${PATCHES[@]}"; do
        if git -C "${ei}" apply -R --check "${p}" 2>/dev/null; then echo "${p}"; return 0; fi
    done
}

# no_secret_args: every non-comment line of a file that names kcadm, in any role,
# playbook or script of the checkout, is free of an option that takes a secret.
no_secret_args() {
    local f bad=()
    while IFS= read -r -d '' f; do
        grep -qF kcadm "${f}" || continue
        if awk '!/^[[:space:]]*#/ && /--(password|new-password|secret|storepass|keypass|trustpass)([[:space:]=]|$)|-s[[:space:]]+["'\'']?(secret|password|value)=/ { found = 1 }
                END { exit found ? 0 : 1 }' "${f}"; then
            bad+=("${f#"${ei}"/}")
        fi
    done < <(find "${ei}" -path "${ei}/.git" -prune -o -type f \
                 \( -name '*.yaml' -o -name '*.yml' -o -name '*.j2' -o -name '*.sh' \) -print0)
    [ "${#bad[@]}" -eq 0 ] \
        || err "a kcadm call in EI passes a secret as an argument, which ps shows on the node and, under k3d, on the host: ${bad[*]}"
}

case "${mode}" in
apply)
    have=$(carried)
    if [ -n "${have}" ]; then
        info "EI ${TASK}: kcadm already reads the admin password from its environment (${have##*/})"
    else
        for p in "${PATCHES[@]}"; do
            if git -C "${ei}" apply --check "${p}" 2>/dev/null; then have=${p}; break; fi
        done
        [ -n "${have}" ] \
            || err "EI's ${TASK} is not a task any variant was cut against (${PATCHES[*]##*/}): check whether it still passes the Keycloak admin password or the client secret on kcadm's command line, then cut a variant for it"
        git -C "${ei}" apply "${have}"
        info "EI ${TASK}: kcadm reads the admin password from its environment and Keycloak generates the client secret (${have##*/})"
    fi
    no_secret_args
    ;;
revert)
    have=$(carried)
    if [ -n "${have}" ]; then git -C "${ei}" apply -R "${have}"; fi
    ;;
*) err "usage: ei-kcadm.sh apply|revert <enterprise-inference git checkout>" ;;
esac
