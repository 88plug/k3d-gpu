#!/usr/bin/env bash
# Kubeconfig and context isolation (C2). Every kubectl call the launcher makes
# must reach its own cluster, however many `up`s run at once, and ~/.kube/config
# must not change unless the user opts in. No cluster: k3d and kubectl are stub
# scripts. The k3d stub does what k3d v5.9.0 does by default (create writes the
# default kubeconfig and switches its current-context); the kubectl stub logs
# which context each call reaches.
#
# Usage: tests/kube_context_test.sh
#   K3D_GPU_E2E=1 also runs the real-k3d cases (creates and deletes four clusters).
#
# The launcher is sourced from a pipe in several cases.
# shellcheck disable=SC1090,SC2016,SC2030,SC2031,SC2034,SC2154,SC2329

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
            # --output: k3d reads the file, merges and renames, with no lock; it
            # switches current-context unless -s=false and the file has one.
            out=""; prev=""
            for a in "$@"; do [ "${prev}" = --output ] && out=${a}; prev=${a}; done
            flock -n "${out}.k3d-gpu.lock" true && echo "unlocked $3" >> "${STUB_DIR}/lock-out"
            old=$(cat "${out}" 2>/dev/null)
            sleep "${STUB_MERGE_SLEEP:-0}"
            cur=$(sed -n 's/^current-context: *//p' <<< "${old}")
            [[ " $* " == *" --kubeconfig-switch-context=false "* && -n "${cur}" ]] || cur=k3d-$3
            { grep -v '^current-context:' <<< "${old}" | grep .; one "$3" | grep -v '^current-context:'
              echo "current-context: ${cur}"; } > "${out}.stub.$$"
            mv -f "${out}.stub.$$" "${out}"
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
# A kubeconfig marked STALE has old certs: the call fails as kubectl would.
if grep -q STALE "${kc}" 2>/dev/null; then echo "x509: certificate signed by unknown authority" >&2; exit 1; fi
case "$*" in
    *"config delete-"*) sed -i "/${!#}/d" "${kc}" ;;
    *"config get-contexts"*) sed -n 's/^  name: //p' "${kc}" ;;
    *"config current-context"*) sed -n 's/^current-context: *//p' "${kc}" ;;
    *"get nodes -o go-template"*) printf 'N\tnode-%s\n' "${ctx}" ;;
    *"get nodes -l"*) echo node/k3d-stub-server-0 ;;
    *"get pod"*phase*) echo Succeeded ;;
    *port-forward*) if [ -n "${PF_PIDFILE:-}" ]; then echo $$ > "${PF_PIDFILE}"; exec sleep 30; fi ;;
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
bad=$(grep -v " target=foreign-admin known=yes kc=${fk} " "${SD}/kubectl.log" 2>/dev/null)
if [ -s "${SD}/kubectl.log" ] && [ -z "${bad}" ]; then
    ok "kube-context: K3D_GPU_KUBECONFIG uses the file's own context on every call"
else fail "kube-context: K3D_GPU_KUBECONFIG uses the file's own context on every call: $(head -2 <<< "${bad}")"; fi
: > "${SD}/kubectl.log"
run K3D_GPU_KUBECONFIG="${fk}" K3D_GPU_CONTEXT=x bash "${LAUNCHER}" agent up --remote >/dev/null 2>&1
bad=$(grep -v " target=x " "${SD}/kubectl.log" 2>/dev/null)
if [ -s "${SD}/kubectl.log" ] && [ -z "${bad}" ]; then ok "kube-context: K3D_GPU_CONTEXT overrides the context on every call"
else fail "kube-context: K3D_GPU_CONTEXT overrides the context on every call: $(head -2 <<< "${bad}")"; fi
# report names and queries the context in use, not a hard-coded k3d-<cluster>.
: > "${SD}/kubectl.log"
rep=$(run K3D_GPU_KUBECONFIG="${fk}" bash "${LAUNCHER}" report --json 2>/dev/null)
bad=$(grep -v " target=foreign-admin " "${SD}/kubectl.log" 2>/dev/null)
if python3 -c 'import json,sys; c=json.load(sys.stdin)["cluster"]; sys.exit(0 if c["present"] and c["context"]=="foreign-admin" else 1)' <<< "${rep}" 2>/dev/null \
   && [ -z "${bad}" ]; then ok "kube-context: report with K3D_GPU_KUBECONFIG reads the file's context"
else fail "kube-context: report with K3D_GPU_KUBECONFIG reads the file's context: $(head -c 300 <<< "${rep}") $(head -2 <<< "${bad}")"; fi
# Python children (remote.py, capacity.py) get the context through K3D_GPU_KCTX.
got=$(run K3D_GPU_CLUSTER=cz K3D_GPU_CONTEXT=x bash -c 'source <(sed "/^case \"\${1:-help}\" in$/,\$d" "$1"); printenv K3D_GPU_KCTX' _ "${LAUNCHER}" 2>/dev/null)
got2=$(run K3D_GPU_CLUSTER=cz K3D_GPU_KCTX=stale bash -c 'source <(sed "/^case \"\${1:-help}\" in$/,\$d" "$1"); printenv K3D_GPU_KCTX' _ "${LAUNCHER}" 2>/dev/null)
if [ "${got}" = x ] && [ "${got2}" = k3d-cz ]; then ok "kube-context: launcher exports its context to child tools (K3D_GPU_KCTX)"
else fail "kube-context: launcher exports its context to child tools (K3D_GPU_KCTX): '${got}' '${got2}'"; fi

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
    | grep -v 'echo ' | grep -v 'KCTX')
# A backgrounded shell function sets $! to a subshell: killing it orphans kubectl.
bg=$(grep -nE '(^|[^[:alnum:]_./-])kubectl[[:space:]][^#]*[^&]&[[:space:]]*(#.*)?$' "${LAUNCHER}" | grep -v 'command kubectl')
# The wrapper names the context; a hard-coded --context overrides K3D_GPU_CONTEXT.
hard=$(grep -nE '(^|[^[:alnum:]_./-])kubectl[[:space:]][^#]*--context' "${LAUNCHER}" | grep -v 'KCTX' | grep -vE '^[0-9]+:[[:space:]]*#')
if [ -z "${flip}${bypass}${bg}${hard}" ]; then ok "kube-context: no context-flipping, wrapper-bypassing, backgrounded or hard-pinned kubectl in the launcher"
else fail "kube-context: no context-flipping, wrapper-bypassing, backgrounded or hard-pinned kubectl in the launcher: ${flip} ${bypass} ${bg} ${hard}"; fi
# Hints the user copies name the kubeconfig: a bare `kubectl` reads ~/.kube/config.
hint=$(grep -nE '(info|warn|err|echo)[^#]*"[^"]*(— |: |\(|; )kubectl (-n|get|describe|delete|logs|port-forward)' "${LAUNCHER}")
hint2=$(grep -nE '^ +kubectl -n \$\{' "${LAUNCHER}")
if [ -z "${hint}${hint2}" ]; then ok "kube-context: user-facing kubectl hints carry the kubeconfig"
else fail "kube-context: user-facing kubectl hints carry the kubeconfig: $(head -3 <<< "${hint}${hint2}")"; fi

# ---- KC13: a backgrounded port-forward's $! is kubectl itself ---------------------
fresh bgpf; touch "${SD}/created-gpu"
pidf="${T}/bgpf/pf.pid"
(export HOME="${H}" K3D_GPU_STATE_DIR="${ST}" STUB_DIR="${SD}" PATH="${B}:${PATH}" PF_PIDFILE="${pidf}"
 unset KUBECONFIG
 source <(lib)
 free_port() { echo 18999; }; sleep() { :; }; require() { :; }
 curl() { echo '{"data":[{"id":"m-int4"}]}'; }
 AGENT_APPS=(ovms) kctx=k3d-gpu
 bench_k3d_arm 4bit >/dev/null 2>&1
 i=0; while [ ! -s "${pidf}" ] && [ "${i}" -lt 50 ]; do command sleep 0.1; i=$((i + 1)); done
 kill "${pf}" 2>/dev/null; command sleep 0.3
 echo "${pf}" > "${T}/bgpf/bang")
kp=$(cat "${pidf}" 2>/dev/null)
if [ -n "${kp}" ] && ! kill -0 "${kp}" 2>/dev/null && [ "$(cat "${T}/bgpf/bang" 2>/dev/null)" = "${kp}" ]; then
    ok "kube-context: bench port-forward \$! is the kubectl process (kill stops it)"
else
    fail "kube-context: bench port-forward \$! is the kubectl process (kill stops it): pf=$(cat "${T}/bgpf/bang" 2>/dev/null) kubectl=${kp}"
    [ -z "${kp}" ] || kill "${kp}" 2>/dev/null
fi

# ---- KC14: an exported shared KUBECONFIG keeps its current-context; merges lock ---
fresh shared
sk="${T}/shared/work.yaml"
printf 'apiVersion: v1\nclusters: []\ncontexts:\n- context: {}\n  name: work\ncurrent-context: work\nkind: Config\nusers: []\n' > "${sk}"
run KUBECONFIG="${sk}" STUB_MERGE_SLEEP=0.5 K3D_GPU_CLUSTER=sa bash "${LAUNCHER}" up > "${T}/sa.out" 2>&1 & pa=$!
run KUBECONFIG="${sk}" STUB_MERGE_SLEEP=0.5 K3D_GPU_CLUSTER=sb bash "${LAUNCHER}" up > "${T}/sb.out" 2>&1 & pb=$!
wait "${pa}"; wait "${pb}"
if grep -qx '  name: k3d-sa' "${sk}" && grep -qx '  name: k3d-sb' "${sk}"; then
    ok "kube-context: concurrent ups merging into one exported KUBECONFIG keep both contexts"
else fail "kube-context: concurrent ups merging into one exported KUBECONFIG keep both contexts: $(grep 'name: k3d' "${sk}" | tr '\n' ' ')"; fi
if [ "$(sed -n 's/^current-context: *//p' "${sk}")" = work ] && [ ! -e "${SD}/lock-out" ] \
   && [ "$(grep -c 'kubeconfig merge s[ab] .*--kubeconfig-switch-context=false' "${SD}/k3d.log")" -eq 2 ]; then
    ok "kube-context: merge into an exported KUBECONFIG keeps its current-context and holds the lock"
else fail "kube-context: merge into an exported KUBECONFIG keeps its current-context and holds the lock (current $(sed -n 's/^current-context: *//p' "${sk}"), $(cat "${SD}/lock-out" 2>/dev/null | tr '\n' ' '))"; fi
: > "${SD}/kubectl.log"
run KUBECONFIG="${sk}" K3D_GPU_CLUSTER=sa bash "${LAUNCHER}" test >/dev/null 2>&1
all_target "kube-context: exported shared KUBECONFIG — calls still reach k3d-sa" "${SD}/kubectl.log" k3d-sa
# An empty hold file gets this cluster as its current-context (bare kubectl in live.sh).
fresh hold0
h0="${T}/hold0/kc"; : > "${h0}"
run KUBECONFIG="${h0}" K3D_GPU_CLUSTER=h0 bash "${LAUNCHER}" up >/dev/null 2>&1
if [ "$(sed -n 's/^current-context: *//p' "${h0}")" = k3d-h0 ]; then ok "kube-context: an empty exported hold kubeconfig gets current-context k3d-<cluster>"
else fail "kube-context: an empty exported hold kubeconfig gets current-context k3d-<cluster>: $(cat "${h0}")"; fi

# ---- KC17: up with K3D_GPU_KUBECONFIG alone switches that file to the new cluster ---
# (no context is named, so every call after create reads the file's current-context)
fresh kgk
gk="${T}/kgk/other.yaml"; mkdir -p "${gk%/*}"
printf 'apiVersion: v1\nclusters: []\ncontexts:\n- context: {}\n  name: other\ncurrent-context: other\nkind: Config\nusers: []\n' > "${gk}"
run K3D_GPU_KUBECONFIG="${gk}" K3D_GPU_CLUSTER=nu bash "${LAUNCHER}" up >/dev/null 2>&1
all_target "kube-context: up with K3D_GPU_KUBECONFIG alone — every call reaches k3d-nu" "${SD}/kubectl.log" k3d-nu
if [ "$(sed -n 's/^current-context: *//p' "${gk}")" = k3d-nu ] && grep -qx '  name: other' "${gk}"; then
    ok "kube-context: up with K3D_GPU_KUBECONFIG alone switches the file's current-context to k3d-<cluster>"
else fail "kube-context: up with K3D_GPU_KUBECONFIG alone switches the file's current-context to k3d-<cluster>: current $(sed -n 's/^current-context: *//p' "${gk}")"; fi
# A named context needs no switch: the file keeps its current-context.
fresh kgkc
gk="${T}/kgkc/other.yaml"; mkdir -p "${gk%/*}"
printf 'apiVersion: v1\nclusters: []\ncontexts:\n- context: {}\n  name: other\ncurrent-context: other\nkind: Config\nusers: []\n' > "${gk}"
run K3D_GPU_KUBECONFIG="${gk}" K3D_GPU_CONTEXT=k3d-nc K3D_GPU_CLUSTER=nc bash "${LAUNCHER}" up >/dev/null 2>&1
all_target "kube-context: up with K3D_GPU_KUBECONFIG and K3D_GPU_CONTEXT — every call reaches the named context" "${SD}/kubectl.log" k3d-nc
if [ "$(sed -n 's/^current-context: *//p' "${gk}")" = other ]; then
    ok "kube-context: up with K3D_GPU_KUBECONFIG and K3D_GPU_CONTEXT keeps the file's current-context"
else fail "kube-context: up with K3D_GPU_KUBECONFIG and K3D_GPU_CONTEXT keeps the file's current-context: current $(sed -n 's/^current-context: *//p' "${gk}")"; fi

# ---- KC15: a stale per-cluster file is refreshed and the call retried ------------
# (the stub logs, then fails a call on a STALE file: two logged calls = the call and its retry)
fresh stale; touch "${SD}/created-cs"; mkdir -p "${ST}"
printf 'STALE\n  name: k3d-cs\ncurrent-context: k3d-cs\n' > "${ST}/kubeconfig-cs"
rc=$(run K3D_GPU_CLUSTER=cs bash -c 'source <(sed "/^case \"\${1:-help}\" in$/,\$d" "$1"); set +e; kubectl get nodes >/dev/null 2>&1; echo $?' _ "${LAUNCHER}" 2>/dev/null)
if [ "${rc}" = 0 ] && ! grep -q STALE "${ST}/kubeconfig-cs" && [ "$(stat -c %a "${ST}/kubeconfig-cs")" = 600 ] \
   && [ "$(grep -c "argv=--context k3d-cs get nodes" "${SD}/kubectl.log")" -eq 2 ]; then
    ok "kube-context: a stale per-cluster kubeconfig (cluster recreated elsewhere) is rewritten and the call retried"
else fail "kube-context: a stale per-cluster kubeconfig is rewritten and the call retried (rc ${rc}): $(head -1 "${ST}/kubeconfig-cs"); $(cat "${SD}/kubectl.log" 2>&1)"; fi

# ---- KC16: the lazy lookup runs once per shell; no command writes state for it ----
# (a $(kubectl) subshell repeats it while the cluster is absent: the launcher keeps
# no marker file, since `facts` and `up` without a guard must write no state)
fresh once
run bash -c 'source <(sed "/^case \"\${1:-help}\" in$/,\$d" "$1"); kubectl get a; kubectl get b; kc get c; x=$(kubectl get d)' _ "${LAUNCHER}" >/dev/null 2>&1
n=$(grep -c 'cluster get' "${SD}/k3d.log" 2>/dev/null)
left=$(ls -A "${ST}" 2>/dev/null)
if [ "${n:-0}" -eq 1 ] && [ -z "${left}" ]; then ok "kube-context: absent cluster — one lookup for the main shell's calls, none written to the state dir"
else fail "kube-context: absent cluster — one lookup for the main shell's calls, none written to the state dir (k3d cluster get ran ${n:-0} times; state: ${left})"; fi
# A child tool as the first kubectl user still finds the file (capacity's discovery).
fresh child; touch "${SD}/created-cc"
mkdir -p "${T}/child/bench/apb"
printf 'import subprocess\nsubprocess.run(["kubectl", "get", "nodes"])\n' > "${T}/child/bench/apb/capacity.py"
run K3D_GPU_CLUSTER=cc K3D_GPU_BENCH="${T}/child/bench" bash "${LAUNCHER}" capacity >/dev/null 2>&1
all_target "kube-context: capacity.py as the first kubectl user reaches k3d-cc" "${SD}/kubectl.log" k3d-cc

# ---- KC12: the opt-in merge into ~/.kube/config holds the lock ----------------------
if type -P flock >/dev/null; then ok "kube-context: flock present (KC12 lock checks are meaningful)"
else fail "kube-context: flock present (KC12 lock checks are meaningful)"; fi
fresh merge; seed_home work
run K3D_GPU_CLUSTER=cm K3D_GPU_MERGE_KUBECONFIG=1 bash "${LAUNCHER}" up >/dev/null 2>&1
if grep -qE 'kubeconfig merge cm .*(-d|--kubeconfig-merge-default)( |$)' "${SD}/k3d.log" && [ ! -e "${SD}/lock" ] && grep -qx '  name: k3d-cm' "${H}/.kube/config"; then
    ok "kube-context: opt-in merge runs under the flock"
else fail "kube-context: opt-in merge runs under the flock (lock: $(cat "${SD}/lock" 2>/dev/null || echo held))"; fi
run K3D_GPU_CLUSTER=cm K3D_GPU_MERGE_KUBECONFIG=1 bash "${LAUNCHER}" down >/dev/null 2>&1
if ! grep -q 'k3d-cm' "${H}/.kube/config"; then ok "kube-context: opt-in down removes the merged entry"
else fail "kube-context: opt-in down removes the merged entry"; fi
# A context merged at up is removed at down without the opt-in too.
fresh merge2; seed_home work
run K3D_GPU_CLUSTER=cm K3D_GPU_MERGE_KUBECONFIG=1 bash "${LAUNCHER}" up >/dev/null 2>&1
run K3D_GPU_CLUSTER=cm bash "${LAUNCHER}" down >/dev/null 2>&1
if grep -qx '  name: work' "${H}/.kube/config" && ! grep -q 'k3d-cm' "${H}/.kube/config"; then ok "kube-context: down removes a merged entry without the opt-in"
else fail "kube-context: down removes a merged entry without the opt-in: $(grep k3d-cm "${H}/.kube/config" | head -2)"; fi
# No flock: the merge still happens, with a warning.
fresh noflock; seed_home work
NF="${T}/noflock-bin"; mkdir -p "${NF}"
for d in "${B}" ${PATH//:/ }; do
    [ -d "${d}" ] || continue
    for x in "${d}"/*; do
        n=${x##*/}; [ "${n}" = flock ] || [ -e "${NF}/${n}" ] || ln -s "${x}" "${NF}/${n}" 2>/dev/null
    done
done
out=$(env -u KUBECONFIG HOME="${H}" K3D_GPU_STATE_DIR="${ST}" STUB_DIR="${SD}" PATH="${NF}" K3D_GPU_SYSROOT="${SYS}" \
    K3D_GPU_VENDORS=hybrid K3D_GPU_AUTOQUANT_CACHE="${T}/none.json" K3D_GPU_CLUSTER=cf K3D_GPU_MERGE_KUBECONFIG=1 bash "${LAUNCHER}" up 2>&1)
if grep -q 'flock not found' <<< "${out}" && grep -qx '  name: k3d-cf' "${H}/.kube/config"; then
    ok "kube-context: without flock the opt-in merge still runs, with a warning"
else fail "kube-context: without flock the opt-in merge still runs, with a warning: $(grep -i flock <<< "${out}" | head -2)"; fi
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
    # The exported-KUBECONFIG path (crescent holds export an empty mktemp file; a
    # user may export a shared one), two ups at once: real `k3d kubeconfig merge
    # --output` under the lock. The empty file gets current-context k3d-<cluster>
    # (bare kubectl in live.sh), the shared one keeps its own.
    hk="${T}/e2e-hold.kubeconfig" sk="${T}/e2e-shared.kubeconfig"
    : > "${hk}"
    printf 'apiVersion: v1\nclusters: []\ncontexts:\n- context:\n    cluster: none\n  name: work\ncurrent-context: work\nkind: Config\nusers: []\n' > "${sk}"
    e2k() { env HOME="${EH}" KUBECONFIG="$1" K3D_GPU_VENDORS=none K3D_GPU_CLUSTER="$2" bash "${LAUNCHER}" "$3"; }
    e2k "${hk}" kcx-h up > "${T}/e2e-h.out" 2>&1 & pa=$!
    e2k "${sk}" kcx-s up > "${T}/e2e-s.out" 2>&1 & pb=$!
    wait "${pa}"; ra=$?; wait "${pb}"; rb=$?
    nh=$(KUBECONFIG="${hk}" kubectl get nodes -o name 2>&1)
    ns=$(kubectl --kubeconfig "${sk}" --context k3d-kcx-s get nodes -o name 2>&1)
    ch=$(kubectl --kubeconfig "${hk}" config current-context 2>&1)
    cs=$(kubectl --kubeconfig "${sk}" config current-context 2>&1)
    sctx=$(kubectl --kubeconfig "${sk}" config get-contexts -o name 2>&1 | sort | tr '\n' ' ')
    e2k "${hk}" kcx-h down > "${T}/e2e-dh.out" 2>&1; e2k "${sk}" kcx-s down > "${T}/e2e-ds.out" 2>&1
    sctx_down=$(kubectl --kubeconfig "${sk}" config get-contexts -o name 2>&1 | tr '\n' ' ')
    end2=$(sha256sum < "${EH}/.kube/config")
    echo "  e2e exported: up rc ${ra},${rb}; hold nodes=[${nh}] current=${ch}; shared nodes=[${ns}] current=${cs} contexts=[${sctx}] after down=[${sctx_down}]"
    if [ "${ra}${rb}" = 00 ] && [ "${nh}" = node/k3d-kcx-h-server-0 ] && [ "${ns}" = node/k3d-kcx-s-server-0 ] \
       && [ "${ch}" = k3d-kcx-h ] && [ "${cs}" = work ] && [ "${sctx}" = "k3d-kcx-s work " ] \
       && [ "${sctx_down}" = "work " ] && [ "${before}" = "${end2}" ]; then
        ok "kube-context e2e: real k3d merge into an exported KUBECONFIG (empty hold file, shared file)"
    else
        fail "kube-context e2e: real k3d merge into an exported KUBECONFIG (empty hold file, shared file)"
        tail -5 "${T}/e2e-h.out" "${T}/e2e-s.out"
    fi
fi

echo "kube_context_test: ${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
