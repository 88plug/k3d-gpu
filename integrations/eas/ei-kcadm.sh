#!/usr/bin/env bash
# ei-kcadm.sh — enterprise-inference's keycloak_config role runs kcadm in the Keycloak pod
# through k8s_exec, with the Keycloak admin password (`config credentials --password`) and
# the inference client secret (`update clients/<id> -s secret=`) as arguments
# (EI 3e8a244 roles/keycloak_config/tasks/install.yaml#L166-L206). A process's arguments
# are in `ps` for every user on the node, and a k3d node is a container on the host.
# patches/ei/0001-keycloak-config-kcadm-argv.patch has kcadm read the password from
# KC_CLI_PASSWORD, copied in the pod from the KC_BOOTSTRAP_ADMIN_PASSWORD the Keycloak
# operator sets, and has Keycloak generate the client secret, which kcadm prints.
#
#   ei-kcadm.sh apply <EI checkout>
#       patch roles/keycloak_config/tasks/install.yaml (nothing to do when it already
#       carries the patch), then check that no kcadm call in the checkout passes a secret
#       as an argument. Exits 1, the checkout left as it was, when the task is not the one
#       the patch was cut against.
#   ei-kcadm.sh revert <EI checkout>
#       take the patch out again (nothing to do without it), so EAS's `init --upgrade`,
#       which refuses an ext/ repo with changes to tracked files, can move the checkout.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="${HERE}/patches/ei/0001-keycloak-config-kcadm-argv.patch"
TASK="roles/keycloak_config/tasks/install.yaml"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

mode=${1:-} ei=${2:-}
if [ -z "${ei}" ] || ! git -C "${ei}" rev-parse --git-dir >/dev/null 2>&1; then
    err "usage: ei-kcadm.sh apply|revert <enterprise-inference git checkout>"
fi
[ -f "${PATCH}" ] || err "${PATCH} missing"

carries() { git -C "${ei}" apply -R --check "${PATCH}" 2>/dev/null; }

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
    if carries; then
        info "EI ${TASK}: kcadm already reads the admin password from its environment"
    elif git -C "${ei}" apply --check "${PATCH}" 2>/dev/null; then
        git -C "${ei}" apply "${PATCH}"
        info "EI ${TASK}: kcadm reads the admin password from its environment and Keycloak generates the client secret (${PATCH##*/})"
    else
        err "EI's ${TASK} is not the task ${PATCH##*/} was cut against (EI 3e8a244 L131-L206): check whether it still passes the Keycloak admin password or the client secret on kcadm's command line, then cut the patch again"
    fi
    no_secret_args
    ;;
revert)
    if carries; then git -C "${ei}" apply -R "${PATCH}"; fi
    ;;
*) err "usage: ei-kcadm.sh apply|revert <enterprise-inference git checkout>" ;;
esac
