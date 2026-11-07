#!/usr/bin/env bash
# eat-redis-cli.sh — enterprise-agent-toolkit's redis_stack role, on EAS's `validate`, runs
# redis-cli in the Redis pod through k8s_exec with the Redis password as `-a <value>`
# (EAT release-2.0.0 7b827e2 roles/redis_stack/tasks/validate.yaml#L20-L55; 036b662 has the
# same file). A process's arguments are in `ps` for every user on the node, and a k3d node
# is a container on the host. patches/eat/0001-redis-stack-validate-redis-cli-auth.patch has
# redis-cli read the password from REDISCLI_AUTH, copied in the pod from the REDIS_PASSWORD
# the chart sets there. `k3d-gpu eas check` (and `eat check`) runs that validate.
#
#   eat-redis-cli.sh apply <EAT checkout>
#       patch roles/redis_stack/tasks/validate.yaml (nothing to do when it already carries
#       the patch), then check that no redis-cli call in the checkout's roles, playbooks or
#       charts passes the password as an argument. Exits 1, the checkout left as it was,
#       when the task is not the one the patch was cut against.
#   eat-redis-cli.sh revert <EAT checkout>
#       take the patch out again (nothing to do without it), so EAS's `init --upgrade`,
#       which refuses an ext/ repo with changes to tracked files, can move the checkout.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="${HERE}/patches/eat/0001-redis-stack-validate-redis-cli-auth.patch"
TASK="roles/redis_stack/tasks/validate.yaml"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

mode=${1:-} eat=${2:-}
if [ -z "${eat}" ] || ! git -C "${eat}" rev-parse --git-dir >/dev/null 2>&1; then
    err "usage: eat-redis-cli.sh apply|revert <enterprise-agent-toolkit git checkout>"
fi
[ -f "${PATCH}" ] || err "${PATCH} missing"

carries() { git -C "${eat}" apply -R --check "${PATCH}" 2>/dev/null; }

# no_password_args: every non-comment line that names redis-cli, in a role, playbook or
# chart of the checkout (what EAS runs; EAT's docker/ compose files are not), is free of
# -a, --pass and a -u URI with a password.
no_password_args() {
    local f bad=() d
    for d in roles playbooks charts; do
        [ -d "${eat}/${d}" ] || continue
        while IFS= read -r -d '' f; do
            grep -qF redis-cli "${f}" || continue
            if awk '!/^[[:space:]]*#/ && /redis-cli/ && (/(^|[[:space:]"'\''])(-a|--pass)([[:space:]"'\'']|$)/ || /-u[[:space:]]+["'\'']?rediss?:\/\/[^[:space:]@]*:[^[:space:]@]+@/) { found = 1 }
                    END { exit found ? 0 : 1 }' "${f}"; then
                bad+=("${f#"${eat}"/}")
            fi
        done < <(find "${eat}/${d}" -type f \
                     \( -name '*.yaml' -o -name '*.yml' -o -name '*.j2' -o -name '*.tpl' -o -name '*.sh' -o -name '*.py' \) -print0)
    done
    [ "${#bad[@]}" -eq 0 ] \
        || err "a redis-cli call in EAT passes the Redis password as an argument, which ps shows on the node and, under k3d, on the host: ${bad[*]}"
}

case "${mode}" in
apply)
    if carries; then
        info "EAT ${TASK}: redis-cli already reads the Redis password from REDISCLI_AUTH"
    elif git -C "${eat}" apply --check "${PATCH}" 2>/dev/null; then
        git -C "${eat}" apply "${PATCH}"
        info "EAT ${TASK}: redis-cli reads the Redis password from REDISCLI_AUTH (${PATCH##*/})"
    else
        err "EAT's ${TASK} is not the task ${PATCH##*/} was cut against (EAT release-2.0.0 7b827e2 L20-L55): check whether it still passes the Redis password on redis-cli's command line, then cut the patch again"
    fi
    no_password_args
    ;;
revert)
    if carries; then git -C "${eat}" apply -R "${PATCH}"; fi
    ;;
*) err "usage: eat-redis-cli.sh apply|revert <enterprise-agent-toolkit git checkout>" ;;
esac
