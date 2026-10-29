#!/usr/bin/env bash
# eas.sh — drives Intel's es_auto_installer.sh (intel/enterprise-ai-solutions) against a
# k3d-gpu cluster in its bring-your-own mode (existing_kubernetes). `k3d-gpu eas` runs it in
# the bastion (bastion.Dockerfile); `overlay-check` also runs on the host for --dry-run.
#
#   eas.sh up <layer> [-- <ansible-playbook args>]
#       1. clone EAS and check out EAS_SHA (a clean tree: earlier overlay hunks are dropped)
#       2. the patch overlay: overlay.py --apply, decisions in overlay.txt
#       3. the layer manifest's revs as commit SHAs (repos.<layer>.yaml from eas-config.py)
#       4. es_auto_installer.sh init <layer> --env k3d-gpu
#       5. global_config.edit.yaml and models.edit.yaml (eas-config.py) into env/k3d-gpu/
#       6. es_auto_installer.sh install <layer> --env k3d-gpu -- -e @k3d-gpu-images.yaml
#          -e @k3d-gpu-overrides.yaml [args]: extra vars load last in es_auto_installer.sh
#          main(), so the overrides win
#   eas.sh overlay-check     steps 1-2 without applying: the overlay decisions only
#   eas.sh down              es_auto_installer.sh teardown platform --env k3d-gpu --force
#   eas.sh status            es_auto_installer.sh status --env k3d-gpu
#
# `configure` is never run: it needs a terminal or --force, sudo and apt
# (es_auto_installer.sh@97f6157 #L318-L351), and the bastion bakes everything it installs.
# The installer reuses a venv that has ansible-playbook (#L801-L806), so .venv links to the
# baked /opt/eas-venv, where it also finds kubernetes.core 6.3.0 (#L832-L847).
#
# Environment (set by `k3d-gpu eas`):
#   EAS_STATE        state dir: the EAS checkout and eas-config.py's files (default /eas-state)
#   EAS_SHA          the enterprise-ai-solutions commit (choices.txt names it)
#   EAS_CPU_POLICY   kubernetes_cpu_policy eas-config.py chose; EAS_NODES the node count (overlay.py)
#   EAS_VENV         the baked venv (default /opt/eas-venv)
#   EAS_OWNER        uid:gid that owns the state dir when this exits. The bastion runs as root:
#                    es_auto_installer.sh ensure_sudo runs `sudo -k` under set -e unless EUID
#                    is 0 (#L866-L875), and the image has no sudo.
# Values from eas-config.py's files reach yq through the environment (strenv), never as
# expression text.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EAS_STATE="${EAS_STATE:-/eas-state}"
EAS_VENV="${EAS_VENV:-/opt/eas-venv}"
EAS_URL="https://github.com/intel/enterprise-ai-solutions"
ENV_NAME="k3d-gpu"
CHECKOUT="${EAS_STATE}/eas"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

own_state() {
    [ -z "${EAS_OWNER:-}" ] || chown -R "${EAS_OWNER}" "${EAS_STATE}" 2>/dev/null || true
}
trap own_state EXIT

# checkout: EAS at EAS_SHA with no local change to a tracked file and no untracked file
# but the ignored ones (env/, ext/, .kubespray/), so every run starts from upstream's tree.
checkout() {
    [[ "${EAS_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || err "EAS_SHA must be a full commit SHA, got '${EAS_SHA:-}'"
    if [ ! -d "${CHECKOUT}/.git" ]; then
        info "Cloning ${EAS_URL} into ${CHECKOUT}"
        git clone -q "${EAS_URL}" "${CHECKOUT}"
    fi
    git -C "${CHECKOUT}" cat-file -e "${EAS_SHA}^{commit}" 2>/dev/null || git -C "${CHECKOUT}" fetch -q --tags origin
    git -C "${CHECKOUT}" checkout -q --force --detach "${EAS_SHA}"
    git -C "${CHECKOUT}" clean -q -fd
    info "EAS at ${EAS_SHA}"
}

# overlay [--apply]: overlay.py decides each patch at the checkout's HEAD (and with --apply
# applies the chosen ones); the decisions go to overlay.txt. A STOP exits non-zero first.
overlay() {
    [[ "${EAS_CPU_POLICY:-}" =~ ^[a-z-]+$ ]] || err "EAS_CPU_POLICY not set (kubernetes_cpu_policy from eas-config.py)"
    [[ "${EAS_NODES:-}" =~ ^[0-9]+$ ]] || err "EAS_NODES not set (the cluster's node count)"
    python3 "${HERE}/overlay.py" --eas "${CHECKOUT}" --policy "${EAS_CPU_POLICY}" --nodes "${EAS_NODES}" \
        --json "${EAS_STATE}/overlay-decisions.json" "$@" | tee "${EAS_STATE}/overlay.txt"
}

# pin_manifest <layer>: each row of repos.<layer>.yaml (layer, url, rev) sets rev on the
# manifest entry with that layer and url, which must exist once. A SHA is a rev
# _checkout_rev accepts (#L684-L689); EAS clones in full, so any published commit resolves.
pin_manifest() {
    local m="${CHECKOUT}/configs/repos/repos.$1.yaml" f="${EAS_STATE}/repos.$1.yaml" L U R
    [ -f "${f}" ] || err "${f} missing (eas-config.py writes it)"
    [ -f "${m}" ] || err "EAS has no configs/repos/repos.$1.yaml at ${EAS_SHA}"
    while IFS=$'\t' read -r L U R; do
        [[ "${R}" =~ ^[0-9a-f]{40}$ ]] || err "repos.$1.yaml: rev for ${U} is not a commit SHA: '${R}'"
        export L U R
        yq -e '[.repos[] | select(.layer == strenv(L) and .url == strenv(U))] | length == 1' "${m}" >/dev/null \
            || err "configs/repos/repos.$1.yaml has no single entry for layer ${L}, ${U}"
        yq -i '(.repos[] | select(.layer == strenv(L) and .url == strenv(U)) | .rev) = strenv(R)' "${m}"
    done < <(yq -r '.repos[] | [.layer, .url, .rev] | @tsv' "${f}")
}

# env_edits: global_config.edit.yaml merged into env/k3d-gpu/global_config.yaml (where
# _resolve_kubeconfig reads existing_kubernetes, #L1342-L1358), and models.edit.yaml into
# env/k3d-gpu/models.yaml: defaults merge, and each model merges into the catalog entry of
# its name, which must exist once.
env_edits() {
    local env="${CHECKOUT}/env/${ENV_NAME}" F N
    F="${EAS_STATE}/global_config.edit.yaml"
    [ -f "${F}" ] || err "${F} missing (eas-config.py writes it)"
    export F
    yq -i '. *= load(strenv(F))' "${env}/global_config.yaml"
    F="${EAS_STATE}/models.edit.yaml"
    [ -f "${F}" ] || err "${F} missing (eas-config.py writes it)"
    [ -f "${env}/models.yaml" ] || err "init seeded no env/${ENV_NAME}/models.yaml"
    yq -i '.defaults *= load(strenv(F)).defaults' "${env}/models.yaml"
    while IFS= read -r N; do
        export N
        yq -e '[.models[] | select(.name == strenv(N))] | length == 1' "${env}/models.yaml" >/dev/null \
            || err "env/${ENV_NAME}/models.yaml has no single catalog entry named ${N}"
        yq -i '(.models[] | select(.name == strenv(N))) *= (load(strenv(F)).models[] | select(.name == strenv(N)))' \
            "${env}/models.yaml"
    done < <(yq -r '.models[].name' "${F}")
}

installer() { (cd "${CHECKOUT}" && ./es_auto_installer.sh "$@"); }

cmd_up() {
    local layer=${1:-}
    [ -n "${layer}" ] || err "usage: eas.sh up <layer> [-- <ansible-playbook args>]"
    shift
    [ "${1:-}" != -- ] || shift
    checkout
    overlay --apply
    pin_manifest "${layer}"
    ln -sfn "${EAS_VENV}" "${CHECKOUT}/.venv"
    if [ -f "${CHECKOUT}/env/${ENV_NAME}/.solutions.yaml" ]; then
        # --upgrade moves already-cloned ext/ repos onto the manifest's new revs (never
        # rewriting a config); without it a changed pin is refused (#L910-L940).
        installer init "${layer}" --env "${ENV_NAME}" --upgrade
    else
        installer init "${layer}" --env "${ENV_NAME}"
    fi
    env_edits
    installer install "${layer}" --env "${ENV_NAME}" -- \
        -e "@${EAS_STATE}/k3d-gpu-images.yaml" -e "@${EAS_STATE}/k3d-gpu-overrides.yaml" "$@"
}

cmd_down() {
    [ -d "${CHECKOUT}/env/${ENV_NAME}" ] || err "no env/${ENV_NAME} in ${CHECKOUT}: nothing was installed from this state dir"
    ln -sfn "${EAS_VENV}" "${CHECKOUT}/.venv"
    # teardown's confirm refuses a non-terminal stdin without --force (#L129-L137, #L1486).
    installer teardown platform --env "${ENV_NAME}" --force
}

cmd_status() {
    [ -d "${CHECKOUT}/env/${ENV_NAME}" ] || err "no env/${ENV_NAME} in ${CHECKOUT}: run 'k3d-gpu eas up' first"
    ln -sfn "${EAS_VENV}" "${CHECKOUT}/.venv"
    installer status --env "${ENV_NAME}"
}

sub=${1:-}
[ $# -eq 0 ] || shift
case "${sub}" in
    up)            cmd_up "$@" ;;
    overlay-check) checkout; overlay ;;
    down)          cmd_down ;;
    status)        cmd_status ;;
    *) err "usage: eas.sh up <layer> [-- args] | overlay-check | down | status" ;;
esac
