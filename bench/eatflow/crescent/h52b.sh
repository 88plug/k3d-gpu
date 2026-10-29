#!/usr/bin/env bash
# h52b.sh OUT: the H52b hold's steps (EXPERIMENTS.md "H52 amendment D2", item 7), run under
# hold.sh. In this order, each step's output saved under OUT:
#   1. inventory.sh, the first read (inventory-1.txt)
#   2. `k3d-gpu host-setup --plan --with-kernel-wqs` (plan.txt). h52b.py gate reads its install
#      plan; any command not of D2 item 2's kinds stops the hold here, nothing applied (exit 3)
#   3. `k3d-gpu host-setup --apply --with-kernel-wqs`, its y/N prompt answered y (apply.txt)
#   4. h52.sh OUT/h52b k3dgpu-h52b: doctor, a plain `up`, the node object, `k3d-gpu test`, the
#      enumeration pods, `down`; its own inventory read is the second
#   5. once no k3dgpu-h52b cluster is left, the reverts --apply printed, in its order
#      (reverts.txt, revert-out.txt; each rc in rc.txt)
#   6. inventory.sh, the third read (inventory-3.txt), and the diff of the first and third
#      reads without their first lines (inventory-diff.txt; empty when equal)
# Also saved before and after, read-only and not scored: dpkg-query for the accel-config
# package (D2 item 6: it stays installed), lsmod, needrestart's list of pending service restarts
# (batch, list mode: on Ubuntu its APT hook restarts those services itself after an install),
# /proc/crypto's deflate-iaa entry and the iaa_crypto driver's work queues and settings. Once --apply has started, an exit for any
# reason (TERM or INT included) still deletes the cluster, runs the reverts and takes the third
# read before it returns. Exit 0: every step ran (the records carry the outcome); 3: stopped at
# the plan; 128+N: stopped by signal N after the reverts.
# H52B_K3DGPU, H52B_H52 and H52B_INVENTORY replace the three tools (tests only).
set -uo pipefail
OUT=${1:?usage: h52b.sh OUT}
CL=k3dgpu-h52b
B=${HOME}/k3dgpu-bench
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
K=${H52B_K3DGPU:-${HERE}/../../../scripts/k3d-gpu}
H52=${H52B_H52:-${HERE}/h52.sh}
INV=${H52B_INVENTORY:-${HERE}/inventory.sh}
mkdir -p "${OUT}"
export PATH=${B}/bin:${PATH}
step() { echo "$(date -u +%FT%TZ) $*" | tee -a "${OUT}/steps.txt"; }
hs() { XDG_STATE_HOME=${OUT}/hs-state XDG_CACHE_HOME=${OUT}/hs-cache "${K}" host-setup "$@"; }
reads() {    # reads TAG
    local w q
    dpkg-query -W -f='${Package} ${Version} ${db:Status-Status}\n' accel-config libaccel-config1 > "${OUT}/dpkg-$1.txt" 2>&1
    lsmod > "${OUT}/lsmod-$1.txt" 2>&1
    # shellcheck disable=SC2024  # the record is ours; only needrestart's scan needs root
    sudo -n timeout 120 needrestart -b -r l > "${OUT}/needrestart-$1.txt" 2>&1
    awk '/^name/ {p = 0} /^driver *: deflate-iaa/ {p = 1} p' /proc/crypto > "${OUT}/crypto-$1.txt" 2>&1
    { for a in sync_mode verify_compress; do echo "${a}=$(cat "/sys/bus/dsa/drivers/crypto/${a}" 2>/dev/null)"; done
      w=""; for q in /sys/bus/dsa/drivers/crypto/wq*; do [ -e "${q}" ] && w+=" ${q##*/}"; done; echo "wqs=${w# }"; } > "${OUT}/iaa-crypto-$1.txt"
}
applied=0 undone=0 hp=""
undo() {
    [ "${applied}" -eq 1 ] && [ "${undone}" -eq 0 ] || return 0
    undone=1
    trap 'step "signal during the reverts: carrying on"' TERM INT HUP
    if [ -n "${hp}" ] && kill -0 "${hp}" 2>/dev/null; then
        step "stopping h52.sh (pid ${hp})"
        kill -TERM "${hp}" 2>/dev/null
        wait "${hp}" 2>/dev/null
    fi
    if k3d cluster list -o json 2>/dev/null | python3 -c 'import json,sys; sys.exit(0 if any(c["name"] == sys.argv[1] for c in json.load(sys.stdin)) else 1)' "${CL}"; then
        step "cluster ${CL} still there: deleting it before the reverts"
        timeout 60 k3d cluster delete "${CL}" > "${OUT}/cluster-delete.txt" 2>&1
        echo "cluster delete rc=$?" >> "${OUT}/rc.txt"
    fi
    src=${OUT}/apply.txt
    python3 "${HERE}/h52b.py" reverts "${src}" > "${OUT}/reverts.txt" 2>/dev/null \
        || { src=${OUT}/plan.txt; python3 "${HERE}/h52b.py" reverts "${src}" > "${OUT}/reverts.txt"; }
    step "reverts from ${src##*/}: $(wc -l < "${OUT}/reverts.txt")"
    local i=0 rev
    while IFS= read -r rev; do
        i=$(( i + 1 ))
        step "revert ${i}: ${rev}"
        echo "## revert ${i}: ${rev}" >> "${OUT}/revert-out.txt"
        timeout 120 bash -c "${rev}" >> "${OUT}/revert-out.txt" 2>&1 < /dev/null
        echo "revert ${i} rc=$?" >> "${OUT}/rc.txt"
    done < "${OUT}/reverts.txt"
    step "third inventory read"
    bash "${INV}" > "${OUT}/inventory-3.txt" 2>&1
    diff <(tail -n +2 "${OUT}/inventory-1.txt") <(tail -n +2 "${OUT}/inventory-3.txt") > "${OUT}/inventory-diff.txt"
    echo "inventory diff rc=$? ($(wc -l < "${OUT}/inventory-diff.txt") lines)" >> "${OUT}/rc.txt"
    reads after
    step "done"
}
on_sig() { step "signal $1"; exit $(( 128 + $1 )); }
trap undo EXIT
trap 'on_sig 15' TERM
trap 'on_sig 2' INT
trap 'on_sig 1' HUP

git -C "${HERE}" rev-parse HEAD > "${OUT}/commit.txt" 2>&1
step "first inventory read"
bash "${INV}" > "${OUT}/inventory-1.txt" 2>&1
reads before
step "plan: host-setup --plan --with-kernel-wqs"
hs --plan --with-kernel-wqs > "${OUT}/plan.txt" 2>&1
echo "plan rc=$?" >> "${OUT}/rc.txt"
if ! python3 "${HERE}/h52b.py" gate "${OUT}/plan.txt" > "${OUT}/plan-gate.txt" 2>&1; then
    step "STOP: the plan holds a command outside D2 item 2 (plan-gate.txt); nothing applied, H52b stays PENDING"
    exit 3
fi
step "apply: host-setup --apply --with-kernel-wqs, answered y"
applied=1
printf 'y\n' | hs --apply --with-kernel-wqs > "${OUT}/apply.txt" 2>&1
echo "apply rc=$?" >> "${OUT}/rc.txt"
python3 "${HERE}/h52b.py" gate "${OUT}/apply.txt" > "${OUT}/apply-gate.txt" 2>&1
step "h52.sh ${OUT}/h52b ${CL}"
bash "${H52}" "${OUT}/h52b" "${CL}" > "${OUT}/h52.log" 2>&1 < /dev/null &
hp=$!
wait "${hp}"
echo "h52 rc=$?" >> "${OUT}/rc.txt"
hp=""
undo
