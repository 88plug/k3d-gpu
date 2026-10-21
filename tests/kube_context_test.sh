#!/usr/bin/env bash
# Kubeconfig and context isolation (C2). Every kubectl call the launcher makes
# must reach its own cluster, however many `up`s run at once, and ~/.kube/config
# must not change unless the user opts in. No cluster: k3d and kubectl are stub
# scripts. The k3d stub does what k3d v5.9.0 does by default (create writes the
# default kubeconfig and switches its current-context); the kubectl stub logs
# which context each call reaches.
#
# Usage: tests/kube_context_test.sh
#   K3D_GPU_E2E=1 also runs the real-k3d case (creates and deletes two clusters).
#
# The launcher is sourced from a pipe in two cases.
# shellcheck disable=SC1090,SC2016,SC2030,SC2031,SC2329

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHER="${ROOT}/scripts/k3d-gpu"
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }

T=$(mktemp -d)
trap 'rm -rf "${T}"' EXIT
umask 022
lib() { sed '/^case "\${1:-help}" in$/,$d' "${LAUNCHER}"; }

B="${T}/bin"
mkdir -p "${B}"
printf '#!/bin/sh\nexit 0\n' > "${B}/docker"

# k3d stub. State in ${STUB_DIR}: created-<name> per live cluster, k3d.log (argv).
cat > "${B}/k3d" <<'EOF'
#!/usr/bin/env bash
echo "k3d $*" >> "${STUB_DIR}/k3d.log"
default_kc() { local f="${KUBECONFIG:-}"; f=${f%%:*}; echo "${f:-${HOME}/.kube/config}"; }
one() {
    printf 'apiVersion: v1\nclusters:\n- cluster:\n    server: https://0.0.0.0:6443\n  name: k3d-%s\n' "$1"
    printf 'contexts:\n- context:\n    cluster: k3d-%s\n    user: admin@k3d-%s\n  name: k3d-%s\n' "$1" "$1" "$1"
    printf 'current-context: k3d-%s\nkind: Config\nusers:\n- name: admin@k3d-%s\n  user: {}\n' "$1" "$1"
}
waitfor() { local i=0; while [ ! -e "$1" ] && [ "${i}" -lt 100 ]; do sleep 0.1; i=$((i + 1)); done; }
case "$1 $2" in
    "cluster create")
        n=$3
        # fb writes only after fa has; fa returns only after fb has: on a shared
        # current-context the last writer is always fb.
        [ "${n}" != fb ] || waitfor "${STUB_DIR}/created-fa"
        if [[ " $* " != *" --kubeconfig-update-default=false "* ]]; then
            f=$(default_kc); mkdir -p "$(dirname "${f}")"; one "${n}" > "${f}"
        fi
        touch "${STUB_DIR}/created-${n}"
        [ "${n}" != fa ] || waitfor "${STUB_DIR}/created-fb" ;;
    "cluster get") [ -e "${STUB_DIR}/created-$3" ] ;;
    "cluster delete")
        rm -f "${STUB_DIR}/created-$3"
        f=$(default_kc); [ ! -e "${f}" ] || echo "# k3d removed k3d-$3" >> "${f}" ;;
    "kubeconfig get") [ -e "${STUB_DIR}/created-$3" ] && one "$3" ;;
    "kubeconfig merge")
        if [[ " $* " == *" -d "* || " $* " == *" --kubeconfig-merge-default "* ]]; then
            flock -n "${HOME}/.kube/config.k3d-gpu.lock" true && echo unlocked > "${STUB_DIR}/lock"
            f=$(default_kc); mkdir -p "$(dirname "${f}")"; one "$3" >> "${f}"
        else
            out=""; prev=""
            for a in "$@"; do [ "${prev}" = --output ] && out=${a}; prev=${a}; done
            one "$3" > "${out}"
        fi ;;
esac
exit 0
EOF

# kubectl stub: logs caller (K3D_GPU_CLUSTER), the context the call reaches, the
# kubeconfig it read and its argv.
cat > "${B}/kubectl" <<'EOF'
#!/usr/bin/env bash
ctx="" kc=""
prev=""
for a in "$@"; do
    case "${prev}" in --context) ctx=${a} ;; --kubeconfig) kc=${a} ;; esac
    case "${a}" in --context=*) ctx=${a#--context=} ;; --kubeconfig=*) kc=${a#--kubeconfig=} ;; esac
    prev=${a}
done
if [ -z "${kc}" ]; then kc="${KUBECONFIG:-}"; kc=${kc%%:*}; kc=${kc:-${HOME}/.kube/config}; fi
[ -n "${ctx}" ] || ctx=$(sed -n 's/^current-context: *//p' "${kc}" 2>/dev/null | tail -1)
known=no
grep -qx "  name: ${ctx}" "${kc}" 2>/dev/null && known=yes
echo "caller=${K3D_GPU_CLUSTER:-gpu} target=${ctx:-none} known=${known} kc=${KUBECONFIG:-} argv=$*" >> "${STUB_DIR}/kubectl.log"
case "$*" in
    *"config delete-"*) sed -i "/${!#}/d" "${kc}" ;;
    *"get nodes -l"*) echo node/k3d-stub-server-0 ;;
    *"get pod"*phase*) echo Succeeded ;;
esac
exit 0
EOF
chmod +x "${B}"/*

# fresh: a new HOME, state dir and stub state; sets H, ST, SD.
fresh() {
    local d="${T}/$1"
    H="${d}/home" ST="${d}/home/.local/state/k3d-gpu" SD="${d}/stub"
    mkdir -p "${H}" "${SD}" "${d}/root/sys" "${d}/root/dev"
    SYS="${d}/root"
}
# run <args...>: the launcher with the stubs and the current fresh() tree.
run() {
    env -u KUBECONFIG HOME="${H}" K3D_GPU_STATE_DIR="${ST}" STUB_DIR="${SD}" PATH="${B}:${PATH}" \
        K3D_GPU_SYSROOT="${SYS}" K3D_GPU_VENDORS=hybrid K3D_GPU_AUTOQUANT_CACHE="${T}/none.json" "$@"
}
seed_home() {
    mkdir -p "${H}/.kube"
    printf 'apiVersion: v1\nclusters: []\ncontexts:\n- context: {}\n  name: work\n- context: {}\n  name: %s\ncurrent-context: %s\nkind: Config\nusers: []\n' "$1" "$1" > "${H}/.kube/config"
}
# all_target <name> <log> <want-target>: every kubectl line of the log reaches <want-target>.
all_target() {
    local bad
    if [ ! -s "$2" ]; then fail "$1: no kubectl calls logged"; return; fi
    bad=$(grep -v " target=$3 known=yes " "$2")
    if [ -z "${bad}" ]; then ok "$1"; else fail "$1: $(head -3 <<< "${bad}")"; fi
}

# ---- KC1 / KC2: two concurrent ups -----------------------------------------------
fresh race
seed_home work
before=$(sha256sum < "${H}/.kube/config")
run K3D_GPU_CLUSTER=fa bash "${LAUNCHER}" up > "${T}/fa.out" 2>&1 & pa=$!
run K3D_GPU_CLUSTER=fb bash "${LAUNCHER}" up > "${T}/fb.out" 2>&1 & pb=$!
wait "${pa}"; ra=$?; wait "${pb}"; rb=$?
if [ "${ra}" -eq 0 ] && [ "${rb}" -eq 0 ]; then ok "kube-context: concurrent up fa,fb both exit 0"
else fail "kube-context: concurrent up fa,fb exit ${ra},${rb}: $(tail -3 "${T}/fa.out" "${T}/fb.out")"; fi
bad=$(awk '{c=$1; t=$2; k=$3; sub(/^caller=/, "", c); sub(/^target=/, "", t); if (t != "k3d-" c || k != "known=yes") print}' "${SD}/kubectl.log")
if [ -s "${SD}/kubectl.log" ] && [ -z "${bad}" ]; then
    ok "kube-context: concurrent up fa,fb — every kubectl call targets its caller's cluster"
else
    fail "kube-context: concurrent up fa,fb — every kubectl call targets its caller's cluster: $(head -2 <<< "${bad}")"
fi
after_up=$(sha256sum < "${H}/.kube/config")
run K3D_GPU_CLUSTER=fa bash "${LAUNCHER}" down >/dev/null 2>&1
run K3D_GPU_CLUSTER=fb bash "${LAUNCHER}" down >/dev/null 2>&1
after_down=$(sha256sum < "${H}/.kube/config")
creates=$(grep -c 'cluster create' "${SD}/k3d.log")
flagged=$(grep 'cluster create' "${SD}/k3d.log" | grep -c -- '--kubeconfig-update-default=false')
if [ "${before}" = "${after_up}" ] && [ "${before}" = "${after_down}" ] && [ "${creates}" -eq 2 ] && [ "${flagged}" -eq 2 ]; then
    ok "kube-context: concurrent up+down leave ~/.kube/config byte-identical"
else
    fail "kube-context: concurrent up+down leave ~/.kube/config byte-identical (up same: $([ "${before}" = "${after_up}" ] && echo y || echo n), down same: $([ "${before}" = "${after_down}" ] && echo y || echo n), creates ${creates}, with update-default=false ${flagged})"
fi

# ---- KC3: the per-cluster file ---------------------------------------------------
fresh own
mkdir -p "${ST}"; chmod 0755 "${ST}"
run K3D_GPU_CLUSTER=ca bash "${LAUNCHER}" up > "${T}/ca.out" 2>&1
f="${ST}/kubeconfig-ca"
if [ -f "${f}" ] && [ "$(stat -c %a "${f}")" = 600 ] && [ "$(grep -c -- '^- context:' "${f}")" -eq 1 ] \
   && grep -qF "export KUBECONFIG=${f}" "${T}/ca.out"; then
    run K3D_GPU_CLUSTER=ca bash "${LAUNCHER}" down >/dev/null 2>&1
    if [ ! -e "${f}" ]; then ok "kube-context: up writes 0600 single-context kubeconfig-<cluster>; down removes it"
    else fail "kube-context: down left ${f}"; fi
else
    fail "kube-context: up writes 0600 single-context kubeconfig-<cluster>; down removes it (file: $(ls -l "${f}" 2>&1))"
fi

# ---- KC4: a hold's exported KUBECONFIG -----------------------------------------
fresh hold
hk="${T}/hold/kc/kubeconfig"; mkdir -p "${hk%/*}"
run KUBECONFIG="${hk}" K3D_GPU_CLUSTER=ch bash "${LAUNCHER}" up >/dev/null 2>&1
grep -q "kc=${hk} " "${SD}/kubectl.log" && ! grep -v "kc=${hk} " "${SD}/kubectl.log" | grep -q . \
    && grep -qx '  name: k3d-ch' "${hk}" 2>/dev/null && all_ok=1 || all_ok=0
all_target "kube-context: exported hold KUBECONFIG — calls reach k3d-ch" "${SD}/kubectl.log" k3d-ch
run KUBECONFIG="${hk}" K3D_GPU_CLUSTER=ch bash "${LAUNCHER}" down >/dev/null 2>&1
if [ "${all_ok}" = 1 ] && [ -f "${hk}" ] && [ ! -e "${ST}/kubeconfig-ch" ]; then
    ok "kube-context: exported hold KUBECONFIG is used, merged into, kept on down"
else
    fail "kube-context: exported hold KUBECONFIG is used, merged into, kept on down (kc ok ${all_ok}, file $([ -f "${hk}" ] && echo kept || echo gone))"
fi

# ---- KC5: shared or multi-entry KUBECONFIG is overridden -------------------------
for kcv in home multi; do
    fresh "ovr-${kcv}"; seed_home work; touch "${SD}/created-ca"
    case "${kcv}" in home) v="${H}/.kube/config" ;; multi) v="${H}/.kube/config:${T}/other" ;; esac
    out=$(run KUBECONFIG="${v}" K3D_GPU_CLUSTER=ca bash "${LAUNCHER}" test 2>&1)
    if [ -s "${SD}/kubectl.log" ] && ! grep -v "kc=${ST}/kubeconfig-ca " "${SD}/kubectl.log" | grep -q . \
       && grep -q "KUBECONFIG ${v} is not this cluster's own" <<< "${out}"; then
        ok "kube-context: KUBECONFIG=${kcv} is overridden with a notice"
    else
        fail "kube-context: KUBECONFIG=${kcv} is overridden with a notice: $(head -2 "${SD}/kubectl.log" 2>/dev/null)"
    fi
done

# ---- KC6: a cluster created before this change -----------------------------------
fresh lazy; seed_home k3d-other; touch "${SD}/created-ca"
run K3D_GPU_CLUSTER=ca bash "${LAUNCHER}" agent up --remote >/dev/null 2>&1
all_target "kube-context: existing cluster with no per-cluster file — agent up targets it" "${SD}/kubectl.log" k3d-ca
if [ -f "${ST}/kubeconfig-ca" ]; then ok "kube-context: existing cluster with no per-cluster file — agent up writes it lazily"
else fail "kube-context: existing cluster with no per-cluster file — agent up writes it lazily"; fi

# ---- KC7: require kubectl still sees a missing binary ----------------------------
fresh nokube
NB="${T}/nokube-bin"; mkdir -p "${NB}"
cp "${B}/k3d" "${B}/docker" "${NB}/"
for d in ${PATH//:/ }; do
    [ -d "${d}" ] || continue
    for x in "${d}"/*; do
        n=${x##*/}; [ "${n}" = kubectl ] || [ -e "${NB}/${n}" ] || ln -s "${x}" "${NB}/${n}" 2>/dev/null
    done
done
out=$(env -u KUBECONFIG HOME="${H}" K3D_GPU_STATE_DIR="${ST}" STUB_DIR="${SD}" PATH="${NB}" K3D_GPU_SYSROOT="${SYS}" \
    K3D_GPU_VENDORS=hybrid bash "${LAUNCHER}" test 2>&1); rc=$?
if [ "${rc}" -ne 0 ] && grep -qF "'kubectl' not found in PATH" <<< "${out}"; then
    ok "kube-context: require kubectl still fails when the kubectl binary is absent"
else
    fail "kube-context: require kubectl still fails when the kubectl binary is absent (rc ${rc}): $(tail -2 <<< "${out}")"
fi

# ---- KC8: K3D_GPU_KUBECONFIG and K3D_GPU_CONTEXT ---------------------------------
fresh foreign; seed_home k3d-gpu
fk="${T}/foreign.kubeconfig"
printf 'apiVersion: v1\nclusters: []\ncontexts:\n- context: {}\n  name: foreign-admin\ncurrent-context: foreign-admin\nkind: Config\nusers: []\n' > "${fk}"
run K3D_GPU_KUBECONFIG="${fk}" bash "${LAUNCHER}" agent up --remote >/dev/null 2>&1
first=$(head -1 "${SD}/kubectl.log" 2>/dev/null)
if [[ "${first}" == *" target=foreign-admin "*"kc=${fk} "* ]]; then
    ok "kube-context: K3D_GPU_KUBECONFIG uses the file's own context"
else fail "kube-context: K3D_GPU_KUBECONFIG uses the file's own context: ${first}"; fi
: > "${SD}/kubectl.log"
run K3D_GPU_KUBECONFIG="${fk}" K3D_GPU_CONTEXT=x bash "${LAUNCHER}" agent up --remote >/dev/null 2>&1
first=$(head -1 "${SD}/kubectl.log" 2>/dev/null)
if [[ "${first}" == *" target=x "* ]]; then ok "kube-context: K3D_GPU_CONTEXT overrides the context"
else fail "kube-context: K3D_GPU_CONTEXT overrides the context: ${first}"; fi

# ---- KC9: the resolver runs for every path, sourced too --------------------------
fresh lib
got=$(env -u KUBECONFIG -u K3D_GPU_PREFILL_GUARD HOME="${H}" K3D_GPU_STATE_DIR="${ST}" PATH="${B}:${PATH}" \
    bash -c 'source <(sed "/^case \"\${1:-help}\" in$/,\$d" "$1"); echo "${KUBECONFIG:-unset}"' _ "${LAUNCHER}" 2>/dev/null)
if [ "${got}" = "${ST}/kubeconfig-gpu" ]; then ok "kube-context: resolver runs when sourced"
else fail "kube-context: resolver runs when sourced: KUBECONFIG=${got}"; fi
fresh test; seed_home work; touch "${SD}/created-ct"
run K3D_GPU_CLUSTER=ct bash "${LAUNCHER}" test >/dev/null 2>&1
all_target "kube-context: test pins every call to k3d-ct" "${SD}/kubectl.log" k3d-ct

# ---- KC10: quickstart's port-forward (nohup skips shell functions) ----------------
fresh pf; seed_home work; touch "${SD}/created-gpu"
mkdir -p "${T}/pf/qs"
(export HOME="${H}" K3D_GPU_STATE_DIR="${ST}" STUB_DIR="${SD}" PATH="${B}:${PATH}" K3D_GPU_QUICKSTART_DIR="${T}/pf/qs"
 unset KUBECONFIG
 source <(lib)
 qs_step() { :; }; qs_admin_secret() { :; }; qs_port() { echo 18080; }; sleep() { :; }
 qs_pf_pid() { [ -e "${T}/pf/seen" ] && return 0; touch "${T}/pf/seen"; return 1; }
 qs_ui_up "kind: Namespace" >/dev/null 2>&1; wait) 2>/dev/null
pfl=$(grep 'port-forward' "${SD}/kubectl.log" 2>/dev/null)
if [[ "${pfl}" == *"argv=--context k3d-gpu "* ]]; then ok "kube-context: quickstart port-forward carries --context"
else fail "kube-context: quickstart port-forward carries --context: ${pfl:-no port-forward logged}"; fi

# ---- KC11: nothing in the launcher flips or bypasses the pinned context -----------
flip=$(grep -n 'config use-context' "${LAUNCHER}")
bypass=$(grep -nE '(^|[[:space:];(&|])(nohup|exec|xargs|timeout|env|sudo)[[:space:]]+([^|;&]*[[:space:]])?kubectl([[:space:]]|$)' "${LAUNCHER}" \
    | grep -v 'echo ' | grep -vE -- '--context|KCTX')
if [ -z "${flip}${bypass}" ]; then ok "kube-context: no context-flipping or wrapper-bypassing kubectl in the launcher"
else fail "kube-context: no context-flipping or wrapper-bypassing kubectl in the launcher: ${flip} ${bypass}"; fi

# ---- KC12: the opt-in merge into ~/.kube/config holds the lock ----------------------
fresh merge; seed_home work
run K3D_GPU_CLUSTER=cm K3D_GPU_MERGE_KUBECONFIG=1 bash "${LAUNCHER}" up >/dev/null 2>&1
if grep -qE 'kubeconfig merge cm .*(-d|--kubeconfig-merge-default)( |$)' "${SD}/k3d.log" && [ ! -e "${SD}/lock" ] && grep -qx '  name: k3d-cm' "${H}/.kube/config"; then
    ok "kube-context: opt-in merge runs under the flock"
else fail "kube-context: opt-in merge runs under the flock (lock: $(cat "${SD}/lock" 2>/dev/null || echo held))"; fi
run K3D_GPU_CLUSTER=cm K3D_GPU_MERGE_KUBECONFIG=1 bash "${LAUNCHER}" down >/dev/null 2>&1
if ! grep -q 'k3d-cm' "${H}/.kube/config"; then ok "kube-context: opt-in down removes the merged entry"
else fail "kube-context: opt-in down removes the merged entry"; fi
fresh nomerge
run K3D_GPU_CLUSTER=cn bash "${LAUNCHER}" up >/dev/null 2>&1
if grep -q 'cluster create cn' "${SD}/k3d.log" && ! grep -qE 'kubeconfig merge .*(-d|--kubeconfig-merge-default)( |$)' "${SD}/k3d.log"; then
    ok "kube-context: no merge into ~/.kube/config without opt-in"
else fail "kube-context: no merge into ~/.kube/config without opt-in"; fi

# ---- KC-E2E: real k3d ---------------------------------------------------------------
if [ "${K3D_GPU_E2E:-0}" = 1 ]; then
    EH="${T}/e2e-home"; mkdir -p "${EH}/.kube"
    printf 'apiVersion: v1\nclusters: []\ncontexts: []\ncurrent-context: ""\nkind: Config\nusers: []\n' > "${EH}/.kube/config"
    cp -r "${HOME}/.docker" "${EH}/.docker" 2>/dev/null || true
    before=$(sha256sum < "${EH}/.kube/config")
    e2e() { env -u KUBECONFIG HOME="${EH}" K3D_GPU_VENDORS=none K3D_GPU_CLUSTER="$1" bash "${LAUNCHER}" "$2"; }
    e2e kcx-a up > "${T}/e2e-a.out" 2>&1 & pa=$!
    e2e kcx-b up > "${T}/e2e-b.out" 2>&1 & pb=$!
    wait "${pa}"; ra=$?; wait "${pb}"; rb=$?
    mid=$(sha256sum < "${EH}/.kube/config")
    na=$(kubectl --kubeconfig "${EH}/.local/state/k3d-gpu/kubeconfig-kcx-a" get nodes -o name 2>&1)
    nb=$(kubectl --kubeconfig "${EH}/.local/state/k3d-gpu/kubeconfig-kcx-b" get nodes -o name 2>&1)
    e2e kcx-a down > "${T}/e2e-da.out" 2>&1; e2e kcx-b down > "${T}/e2e-db.out" 2>&1
    end=$(sha256sum < "${EH}/.kube/config")
    echo "  e2e: up rc ${ra},${rb}; nodes a=[${na}] b=[${nb}]"
    if [ "${ra}${rb}" = 00 ] && [ "${na}" = node/k3d-kcx-a-server-0 ] && [ "${nb}" = node/k3d-kcx-b-server-0 ] \
       && [ "${before}" = "${mid}" ] && [ "${before}" = "${end}" ]; then
        ok "kube-context e2e: real k3d create --kubeconfig-update-default=false leaves ~/.kube/config untouched"
    else
        fail "kube-context e2e: real k3d create --kubeconfig-update-default=false leaves ~/.kube/config untouched"
        tail -5 "${T}/e2e-a.out" "${T}/e2e-b.out"
    fi
fi

echo "kube_context_test: ${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
