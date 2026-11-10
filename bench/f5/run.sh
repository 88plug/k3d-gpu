#!/usr/bin/env bash
# One `k3d-gpu eat` run on a fresh k3d-gpu cluster on crescent (Xeon 6741P): the F5 steps
# (EXPERIMENTS.md "F5 pre-registration"). Runs inside the bench lock the caller holds. Every
# step runs once, is timed and logged, its rc recorded; nothing is retried. The cluster is
# deleted and the host's inotify limits are put back on every exit.
#   run.sh <k3d-gpu checkout> <cluster> <out dir> <lock-taken epoch> <cap minutes>
set -uo pipefail

WT=$1 CLUSTER=$2 OUT=$3 T_LOCK=$4 CAP_MIN=$5
K="${WT}/scripts/k3d-gpu"
mkdir -p "${OUT}/steps"
export K3D_GPU_CLUSTER="${CLUSTER}"
export K3D_GPU_STATE_DIR="${OUT}/state"
unset KUBECONFIG K3D_GPU_KUBECONFIG K3D_GPU_K3S_ARGS K3D_GPU_BALLOONS K3D_GPU_NODE_LABELS K3D_GPU_VENDORS HF_TOKEN
export KCFG="${K3D_GPU_STATE_DIR}/kubeconfig-${CLUSTER}"
EAS_STATE="${K3D_GPU_STATE_DIR}/eas-${CLUSTER}"
DEADLINE=$(( T_LOCK + CAP_MIN * 60 ))
STEPS="${OUT}/steps.tsv"
PSFILE="${OUT}/ps-samples.gz"
BIN="$(cd "$(dirname "$0")" && pwd)"
SCAN="${BIN}/scan.sh"
VALS="${BIN}/.vals-${CLUSTER}"
INOTIFY_WANT=(8192 1048576)   # EAT docs/prerequisites.md#L22-L32 at release-2.0.0
: > "${STEPS}"

kc() { KUBECONFIG="${KCFG}" kubectl "$@"; }
now() { date +%s; }
log() { echo "[$(date -Is)] $*" | tee -a "${OUT}/run.log"; }

hostline() {
    local tag=$1
    {
        echo "# ${tag} $(date -Is)"
        echo "uptime: $(uptime)"
        grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo
        echo "running containers: $(docker ps -q | wc -l)"
        echo "oom_kill: $(awk '$1=="oom_kill"{print $2}' /proc/vmstat)"
        echo "inotify: $(cat /proc/sys/fs/inotify/max_user_instances) $(cat /proc/sys/fs/inotify/max_user_watches)"
        echo "disk /: $(df -h / | tail -1)"
    } >> "${OUT}/host.txt"
}

# 60 s host samples for the whole lock: MemAvailable, load1, swap used, OOM kills, pods.
sampler() {
    while :; do
        printf '%s mem_avail_kib=%s load1=%s swap_used_kib=%s oom_kill=%s pods_not_ready=%s\n' \
            "$(date -Is)" "$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)" "$(cut -d' ' -f1 /proc/loadavg)" \
            "$(awk '/^SwapTotal:/{t=$2} /^SwapFree:/{f=$2} END{print t-f}' /proc/meminfo)" \
            "$(awk '$1=="oom_kill"{print $2}' /proc/vmstat)" \
            "$(kc get pods -A --no-headers 2>/dev/null | awk '$4!="Completed" && ($4!="Running" || split($3,a,"/") && a[1]!=a[2])' | wc -l)" \
            >> "${OUT}/samples.txt"
        sleep 60
    done
}

# ps -eo args every 2 s during steps 3, 5 and 8 (gzip members appended).
ps_sampler() {
    while :; do
        { echo "### $(date -Is)"; ps -eo args; } | gzip -1 >> "${PSFILE}"
        sleep 2
    done
}

# Containers of the EAS bastion that mount this run's state dir (a timed-out eas_run
# leaves its docker run behind, since timeout signals only the launcher).
kill_bastions() {
    local id
    for id in $(docker ps -q 2>/dev/null); do
        if docker inspect -f '{{range .Mounts}}{{.Source}} {{end}}' "${id}" 2>/dev/null | grep -qF "${EAS_STATE}"; then
            log "stopping bastion container ${id} (mounts ${EAS_STATE})"
            docker rm -f "${id}" >/dev/null 2>&1 || true
        fi
    done
}

# step <name> <ceiling s> <reserve s> <cmd...>: run once under timeout, wall and rc recorded.
step() {
    local name=$1 ceil=$2 reserve=$3 t0 rc left lim
    shift 3
    left=$(( DEADLINE - $(now) - reserve ))
    if [ "${left}" -le 60 ]; then
        log "step ${name}: not started, the lock cap leaves ${left}s after the teardown reserve"
        printf '%s\tNOT-RUN\t0\tlock cap\n' "${name}" >> "${STEPS}"
        return 125
    fi
    lim=$(( ceil < left ? ceil : left ))
    log "step ${name}: start (timeout ${lim}s): $*"
    t0=$(now)
    timeout --kill-after=30 "${lim}" "$@" > "${OUT}/steps/${name}.log" 2>&1
    rc=$?
    log "step ${name}: rc=${rc} wall=$(( $(now) - t0 ))s"
    printf '%s\t%s\t%s\t%s\n' "${name}" "${rc}" "$(( $(now) - t0 ))" "$([ "${rc}" = 124 ] || [ "${rc}" = 137 ] && echo "timeout ${lim}s" || echo -)" >> "${STEPS}"
    [ "${rc}" != 124 ] && [ "${rc}" != 137 ] || kill_bastions
    return "${rc}"
}

INOTIFY_WAS=""
finish() {
    local rc=$?
    [ -z "${SAMPLER:-}" ] || kill "${SAMPLER}" 2>/dev/null
    [ -z "${PSS:-}" ] || kill "${PSS}" 2>/dev/null
    rm -rf "${VALS}"
    kill_bastions
    if k3d cluster get "${CLUSTER}" >/dev/null 2>&1; then
        log "exit trap: deleting cluster ${CLUSTER}"
        timeout 600 k3d cluster delete "${CLUSTER}" >> "${OUT}/run.log" 2>&1 || true
    fi
    if [ -n "${INOTIFY_WAS}" ]; then
        set -- ${INOTIFY_WAS}
        sudo -n sysctl -q -w "fs.inotify.max_user_instances=$1" "fs.inotify.max_user_watches=$2" \
            && log "inotify put back to $1 $2" || log "could not put inotify back to $1 $2"
    fi
    hostline end
    log "run.sh done (rc ${rc})"
}
trap finish EXIT
trap 'exit 143' TERM INT

k3d cluster get "${CLUSTER}" >/dev/null 2>&1 && { log "cluster ${CLUSTER} exists already: not starting"; exit 3; }
hostline start
{
    echo "k3d-gpu commit: $(git -C "${WT}" rev-parse HEAD) ($(git -C "${WT}" status --porcelain | wc -l) local changes)"
    echo "k3d-gpu version: $("${K}" version 2>/dev/null | head -1)"
    echo "node image: ${K3D_GPU_IMAGE:-default} $(docker image inspect -f '{{.Id}}' "${K3D_GPU_IMAGE:-cryptoandcoffee/k3d-gpu:$(sed -n 's/^VERSION="\([^"]*\)".*/\1/p' "${K}")}" 2>/dev/null)"
    echo "HF_TOKEN: $([ -n "${HF_TOKEN:-}" ] && echo set || echo unset)"
    echo "tools: $(k3d version | head -1); $(kubectl version --client 2>/dev/null | head -1); docker $(docker version --format '{{.Server.Version}}')"
    echo "lock taken: $(date -Is -d "@${T_LOCK}")  cap: ${CAP_MIN} min"
} > "${OUT}/header.txt"
sampler & SAMPLER=$!

# (0b) EAT's inotify prerequisite on the host, put back at exit.
INOTIFY_WAS="$(cat /proc/sys/fs/inotify/max_user_instances) $(cat /proc/sys/fs/inotify/max_user_watches)"
step 0b-inotify 60 3000 sudo -n sysctl -w "fs.inotify.max_user_instances=${INOTIFY_WANT[0]}" \
    "fs.inotify.max_user_watches=${INOTIFY_WANT[1]}"
# (1) k3d-gpu up
step 1-up 1800 3000 "${K}" up
# (2) eat up --dry-run; the choices, overrides, edits, facts and overlay copied into the record
step 2-dry-run 900 3000 "${K}" eat up --dry-run
cp -f "${EAS_STATE}/choices.txt" "${EAS_STATE}/overlay.txt" "${EAS_STATE}/k3d-gpu-overrides.yaml" \
      "${EAS_STATE}/facts.json" "${EAS_STATE}/models.edit.yaml" "${EAS_STATE}/global_config.edit.yaml" "${OUT}/" 2>/dev/null
# (3) eat up, stopped at 100 min
ps_sampler & PSS=$!
step 3-eat-up 6000 1500 "${K}" eat up
kill "${PSS}" 2>/dev/null; PSS=
docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' | grep '^k3d-gpu-eas-bastion:' > "${OUT}/bastion-image.txt"
cp -f "${EAS_STATE}/overlay.txt" "${OUT}/overlay.applied.txt" 2>/dev/null
cp -f "${EAS_STATE}/eas/env/k3d-gpu/global_config.yaml" "${OUT}/env-global_config.yaml" 2>/dev/null
# (4) the Ready wait, the Jobs, the pod list, the model pods' CPU requests
step 4-wait 1800 1500 env KUBECONFIG="${KCFG}" kubectl wait --for=condition=Ready pod -A \
    --field-selector=status.phase!=Succeeded --timeout=30m
kc get jobs -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{" "}{range .status.conditions[*]}{.type}={.status}{" "}{end}{"\n"}{end}' > "${OUT}/jobs.txt" 2>&1
kc get pods -A -o wide > "${OUT}/pods.txt" 2>&1
kc -n llm-inference get pods -o jsonpath='{range .items[*]}{.metadata.name}{" "}{range .spec.containers[*]}{.name}={.resources.requests.cpu}/{.resources.limits.cpu}{" "}{end}{"\n"}{end}' > "${OUT}/model-cpu.txt" 2>&1
kc describe node > "${OUT}/node.txt" 2>&1
# (5) eat check: Intel's validate, then chat, tool call, the guard's block and the sandbox
ps_sampler & PSS=$!
step 5-check 1200 1500 "${K}" eat check
kill "${PSS}" 2>/dev/null; PSS=
kc get pods -A -o wide > "${OUT}/pods-after-check.txt" 2>&1
# (6) eat status
step 6-status 600 1500 "${K}" eat status
# (7) secret scan: counts only, no value printed
step 7-scan 600 1500 bash -c 'bash "$1" read "$2" "$3" && bash "$1" count "$2" step7 "$4" "$4/steps" "$4/run.log" "$4/ps-samples.gz" "$5/logs"' \
    _ "${SCAN}" "${VALS}" "${KCFG}" "${OUT}" "${EAS_STATE}"
# (8) eat down, then the namespaces
ps_sampler & PSS=$!
step 8-eat-down 1200 300 "${K}" eat down
kill "${PSS}" 2>/dev/null; PSS=
kc get ns > "${OUT}/ns-after-down.txt" 2>&1
# Not graded: the same counts after eat down (its logs and ps samples included).
bash "${SCAN}" count "${VALS}" post-down "${OUT}" "${OUT}/steps" "${OUT}/run.log" "${OUT}/ps-samples.gz" "${EAS_STATE}/logs" \
    > "${OUT}/scan-post-down.txt" 2>&1
rm -rf "${VALS}"
# (9) k3d-gpu down
step 9-down 600 0 "${K}" down
exit 0
