#!/usr/bin/env bash
# eas.sh — drives Intel's es_auto_installer.sh (intel/enterprise-ai-solutions) against a
# k3d-gpu cluster in its bring-your-own mode (existing_kubernetes). `k3d-gpu eas` runs it in
# the bastion (bastion.Dockerfile); `overlay-check` also runs on the host for --dry-run.
# Line numbers below are es_auto_installer.sh at EAS v1.2.0 (a051f00).
#
#   eas.sh up <layer> [-- <ansible-playbook args>]
#       1. clone EAS and check out EAS_SHA (a clean tree: earlier overlay hunks are dropped)
#       2. the patch overlay: overlay.py --apply, decisions in overlay.txt
#       3. the layer manifest's revs as commit SHAs (repos.<layer>.yaml from eas-config.py)
#       4. es_auto_installer.sh init <layer> --env k3d-gpu; EI's model deploy must take the
#          LiteLLM master key from the Secret read the overrides empty (key_argv_guard), and
#          EI's keycloak_config role gets patches/ei/0001 (variant a or b), which keeps the
#          Keycloak admin password and the client secret out of kcadm's argv (ei-kcadm.sh:
#          it stops the install when the role's task changed shape; the patch comes out
#          again before init, so `init --upgrade` finds ext/ clean)
#       5. the layer manifest's global_config: keys into env/k3d-gpu/global_config.yaml:
#          init writes them only into a global_config.yaml it creates (_init_seed_defaults,
#          #L930-L950), so an env inited before keeps its own (for the agent toolkit,
#          auth_provider litellm: LiteLLM, its guardrail and the models' registration)
#       6. global_config.edit.yaml and models.edit.yaml (eas-config.py) into env/k3d-gpu/:
#          keys merge; a model's env merges, its args_append follows its catalog args and
#          its cpu replaces the catalog's
#       7. es_auto_installer.sh install <layer> --env k3d-gpu -- -e @k3d-gpu-images.yaml
#          -e @k3d-gpu-overrides.yaml [args]: extra vars load last in es_auto_installer.sh
#          main() (#L1548), so the overrides win
#   eas.sh overlay-check [<layer>]
#                            steps 1-2 without applying: the overlay decisions only, then
#                            the layer manifest's global_config: keys step 5 sets
#   eas.sh validate <layer>  es_auto_installer.sh validate <layer> --env k3d-gpu -- install's
#                            extra vars: Intel's post-deploy checks of every enabled component
#   eas.sh down              es_auto_installer.sh teardown platform --env k3d-gpu --force -- -e
#                            @k3d-gpu-images.yaml -e @k3d-gpu-overrides.yaml: install's extra
#                            vars, so teardown skips what install skipped (k3d-gpu's NFD)
#   eas.sh status            es_auto_installer.sh status --env k3d-gpu
#
# `configure` is never run: it needs a terminal or --force, sudo and apt (#L318-L351), and
# at v1.2.0 it refuses --force ("'configure' takes no arguments"); the bastion bakes
# everything it installs. The installer reuses a venv that has ansible-playbook
# (#L801-L806), so .venv links to the baked /opt/eas-venv, whose purelib holds
# kubernetes.core 6.3.0 where ansible-core finds collections on its sys.path.
#
# Environment (set by `k3d-gpu eas`):
#   EAS_STATE        state dir: the EAS checkout and eas-config.py's files (default /eas-state)
#   EAS_SHA          the enterprise-ai-solutions commit (choices.txt names it)
#   EAS_CPU_POLICY   kubernetes_cpu_policy eas-config.py chose; EAS_NODES the node count (overlay.py)
#   EAS_VENV         the baked venv (default /opt/eas-venv)
#   EAS_OWNER        uid:gid that owns the state dir when this exits. The bastion runs as root:
#                    es_auto_installer.sh ensure_sudo runs `sudo -k` under set -e unless EUID
#                    is 0 (#L852-L861), and the image has no sudo.
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

# manifest_globals <layer> [--list]: the layer manifest's top-level global_config: map
# (plain values only, as _init_layer_globals #L954-L962 requires), merged into
# env/k3d-gpu/global_config.yaml; with --list, only printed. Nothing when the manifest
# has none (EAS main 97f6157).
manifest_globals() {
    local M="${CHECKOUT}/configs/repos/repos.$1.yaml" G="${CHECKOUT}/env/${ENV_NAME}/global_config.yaml" rows
    [ -f "${M}" ] || err "EAS has no configs/repos/repos.$1.yaml at ${EAS_SHA}"
    export M
    rows=$(yq -r '.global_config // {} | to_entries[] | .key + ": " + (.value | to_json(0))' "${M}") \
        || err "configs/repos/repos.$1.yaml: global_config is not a map of plain values"
    [ -n "${rows}" ] || return 0
    if [ "${2:-}" = --list ]; then
        info "configs/repos/repos.$1.yaml global_config, set in env/${ENV_NAME}/global_config.yaml after init:"
        sed 's/^/  /' <<< "${rows}"
        return 0
    fi
    [ -f "${G}" ] || err "init seeded no env/${ENV_NAME}/global_config.yaml"
    yq -i '. *= (load(strenv(M)).global_config // {})' "${G}"
    info "env/${ENV_NAME}/global_config.yaml: the global_config of repos.$1.yaml ($(echo "${rows}" | tr '\n' ' ' | sed 's/ $//'); init sets it only in a new file)"
}

# args_merge <current args JSON> <args_append JSON>: the catalog entry's args with
# args_append after them, each --flag of args_append (and its value) taken out of the
# current args first, so a re-run (init never reseeds models.yaml) replaces rather than
# repeats it. Prints JSON.
args_merge() {
    python3 -c 'import json, sys
cur, add = json.loads(sys.argv[1]), json.loads(sys.argv[2])
valued = {t: i + 1 < len(add) and not add[i + 1].startswith("--") for i, t in enumerate(add) if t.startswith("--")}
out, skip = [], False
for t in cur:
    if skip:
        skip = False
    elif t.split("=", 1)[0] in valued:
        skip = valued[t.split("=", 1)[0]] and "=" not in t
    else:
        out.append(t)
print(json.dumps(out + add))' "$1" "$2"
}

# env_edits: global_config.edit.yaml merged into env/k3d-gpu/global_config.yaml (where
# _resolve_kubeconfig reads existing_kubernetes, #L1378-L1394), and models.edit.yaml into
# env/k3d-gpu/models.yaml: defaults merge; each model is the catalog entry of its name,
# which must exist once: its env merges, its args_append goes after its args, and its cpu
# (when the edit has one) replaces the catalog's.
env_edits() {
    local env="${CHECKOUT}/env/${ENV_NAME}" F N A C cur add
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
        yq -i '(.models[] | select(.name == strenv(N))).env *= ((load(strenv(F)).models[] | select(.name == strenv(N))).env // {})' \
            "${env}/models.yaml"
        cur=$(yq -o=json -I=0 '(.models[] | select(.name == strenv(N))).args // []' "${env}/models.yaml")
        add=$(yq -o=json -I=0 '(.models[] | select(.name == strenv(N))).args_append // []' "${F}")
        A=$(args_merge "${cur}" "${add}")
        export A
        yq -i '(.models[] | select(.name == strenv(N))).args = (strenv(A) | from_yaml)' "${env}/models.yaml"
        C=$(yq -r '(.models[] | select(.name == strenv(N))).cpu // ""' "${F}")
        if [ -n "${C}" ]; then
            [[ "${C}" =~ ^[0-9]+$ ]] || err "models.edit.yaml: cpu for ${N} is not a whole number: '${C}'"
            export C
            yq -i '(.models[] | select(.name == strenv(N))).cpu = strenv(C)' "${env}/models.yaml"
        fi
    done < <(yq -r '.models[].name' "${F}")
}

installer() { (cd "${CHECKOUT}" && ./es_auto_installer.sh "$@"); }

# extra_vars: install's -e files, for teardown and validate too.
extra_vars() {
    EXTRA=()
    [ ! -f "${EAS_STATE}/k3d-gpu-images.yaml" ] || EXTRA+=(-e "@${EAS_STATE}/k3d-gpu-images.yaml")
    EXTRA+=(-e "@${EAS_STATE}/k3d-gpu-overrides.yaml")
}

# key_argv_guard: EI's model deploy hands model-manager the LiteLLM master key as
# MM_LITELLM_MASTER_KEY in its ansible environment:, which ansible writes into the deploy's
# /bin/sh -c command line. k3d-gpu-overrides.yaml empties the Secret read that value comes
# from (_deploy_litellm_secret, eas-config.py argv_master_key); an EI that takes the key
# from anything else would put it back on that command line, so it stops the install.
key_argv_guard() {
    local f="${CHECKOUT}/ext/enterprise.ai-inference/roles/llm_services/tasks/deploy_models.yaml"
    [ -d "${CHECKOUT}/ext/enterprise.ai-inference" ] || return 0
    [ -f "${f}" ] || err "EI has no roles/llm_services/tasks/deploy_models.yaml: check how it now hands model-manager the LiteLLM master key (eas-config.py argv_master_key)"
    # Every line naming the master key (MM_LITELLM_MASTER_KEY, the Secret's .data.master_key,
    # litellm_master_key), under any variable name, must take it from _deploy_litellm_secret;
    # a shell's $MM_LITELLM_MASTER_KEY reads the value that line sets.
    awk '{ l = $0; gsub(/\$\{?MM_LITELLM_MASTER_KEY\}?/, "", l) }
         tolower(l) ~ /master_key/ && l !~ /^[[:space:]]*#/ { any = 1; if (l !~ /_deploy_litellm_secret/) bad = 1 }
         /^[[:space:]]*register:[[:space:]]*_deploy_litellm_secret[[:space:]]*$/ { reg = 1 }
         END { exit (bad || (any && !reg)) ? 1 : 0 }' "${f}" \
        || err "EI's deploy_models.yaml no longer takes the master key from _deploy_litellm_secret alone: k3d-gpu-overrides.yaml would not keep the master key off the deploy's command line (eas-config.py argv_master_key)"
}

# ei_kcadm apply|revert: ei-kcadm.sh on EI's checkout, once init has cloned it.
ei_kcadm() {
    local ei="${CHECKOUT}/ext/enterprise.ai-inference"
    [ -d "${ei}" ] || return 0
    bash "${HERE}/ei-kcadm.sh" "$1" "${ei}"
}

cmd_up() {
    local layer=${1:-}
    [ -n "${layer}" ] || err "usage: eas.sh up <layer> [-- <ansible-playbook args>]"
    shift
    [ "${1:-}" != -- ] || shift
    checkout
    overlay --apply
    pin_manifest "${layer}"
    ln -sfn "${EAS_VENV}" "${CHECKOUT}/.venv"
    ei_kcadm revert
    if [ -f "${CHECKOUT}/env/${ENV_NAME}/.solutions.yaml" ]; then
        # --upgrade moves already-cloned ext/ repos onto the manifest's new revs (never
        # rewriting a config); without it a changed pin is refused (#L896-L926).
        installer init "${layer}" --env "${ENV_NAME}" --upgrade
    else
        installer init "${layer}" --env "${ENV_NAME}"
    fi
    key_argv_guard
    ei_kcadm apply
    manifest_globals "${layer}"
    env_edits
    installer install "${layer}" --env "${ENV_NAME}" -- \
        -e "@${EAS_STATE}/k3d-gpu-images.yaml" -e "@${EAS_STATE}/k3d-gpu-overrides.yaml" "$@"
}

cmd_validate() {
    local layer=${1:-}
    [ -n "${layer}" ] || err "usage: eas.sh validate <layer>"
    [ -d "${CHECKOUT}/env/${ENV_NAME}" ] || err "no env/${ENV_NAME} in ${CHECKOUT}: run 'k3d-gpu eas up' first"
    [ -f "${EAS_STATE}/k3d-gpu-overrides.yaml" ] \
        || err "${EAS_STATE}/k3d-gpu-overrides.yaml missing: validate needs install's extra vars (run 'k3d-gpu eas up --dry-run' to write it again)"
    extra_vars
    ln -sfn "${EAS_VENV}" "${CHECKOUT}/.venv"
    installer validate "${layer}" --env "${ENV_NAME}" -- "${EXTRA[@]}"
}

cmd_down() {
    [ -d "${CHECKOUT}/env/${ENV_NAME}" ] || err "no env/${ENV_NAME} in ${CHECKOUT}: nothing was installed from this state dir"
    # Teardown picks its components by the same enabled flags as install
    # (filter_plugins/resolve_components.py), so it gets install's extra vars. Without the
    # overrides, nfd_enabled is true again and EI's nfd teardown deletes k3d-gpu's own NFD.
    [ -f "${EAS_STATE}/k3d-gpu-overrides.yaml" ] \
        || err "${EAS_STATE}/k3d-gpu-overrides.yaml missing: without it teardown also removes what eas up left to k3d-gpu (its NFD); run 'k3d-gpu eas up --dry-run' to write it again"
    extra_vars
    ln -sfn "${EAS_VENV}" "${CHECKOUT}/.venv"
    # teardown's confirm refuses a non-terminal stdin without --force (#L129-L136, #L1522).
    installer teardown platform --env "${ENV_NAME}" --force -- "${EXTRA[@]}"
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
    overlay-check) checkout; overlay; [ -z "${1:-}" ] || manifest_globals "$1" --list ;;
    validate)      cmd_validate "$@" ;;
    down)          cmd_down ;;
    status)        cmd_status ;;
    *) err "usage: eas.sh up <layer> [-- args] | overlay-check [<layer>] | validate <layer> | down | status" ;;
esac
