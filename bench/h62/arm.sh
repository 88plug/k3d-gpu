#!/usr/bin/env bash
# arm.sh ARM CMD [ARG]: one H62 arm's server in its KVM guest (EXPERIMENTS.md "H62
# pre-registration"). bench/eatflow/live.sh's remote-arm mode calls it through arm-A1 and arm-B5.
#   port               the host port its Service is forwarded to (the guest's hostfwd)
#   restart SECONDS    delete its serving pod, then wait until a new pod is Ready; exit 3 when none is
#                      Ready within SECONDS (the hold's 900 s readiness rule passes the time left)
#   records DIR        the pod, the CPU ISA it sees (amx_tile lines), its cpuset and nproc, the nodes,
#                      the guest's load and memory, and B5's LLMInferenceService
#   logs DIR           every pod, events, the pod description and the server log (a hold that fails)
#   forward            (re)start `kubectl port-forward` of its Service in the guest, on the serve port
#   unforward          stop that port-forward
#   cgroup             "exec:<this script> ARM cpustat <dir>": its pod's cgroup in the guest, as the
#                      idle-arm check reads it (bench/eatflow/pairs.py)
#   cpustat DIR        that cgroup's cpu.stat
# ARM: A1 (VM a: k3d-gpu's agent OVMS) or B5 (VM b: EAS's gemma-4-26b LLMInferenceService).
# H62_STATE (default ~/k3dgpu-bench/h62) holds ssh_config; H62_SSH is the ssh client (tests stub
# it); H62_POLL_S is the restart poll period (default 5 s). Each guest's ~/h62/env sets PATH and
# KUBECONFIG for kubectl there.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SELF=${HERE}/arm.sh
P() { python3 "${HERE}/h62.py" "$@"; }
ARM=${1:?usage: arm.sh A1|B5 CMD [ARG]} CMD=${2:?usage: arm.sh A1|B5 CMD [ARG]}
fields=$(P arm-fields "${ARM}" 2>/dev/null) || { echo "arm.sh: no arm ${ARM} (A1 or B5)" >&2; exit 2; }
read -r _ HOST PORT NS SEL SVC SPORT CTR EXTRA <<< "${fields}"
STATE=${H62_STATE:-${HOME}/k3dgpu-bench/h62}
# g CMD: one command in the guest, after its ~/h62/env.
g() { ${H62_SSH:-ssh} -F "${STATE}/ssh_config" "${HOST}" ". ~/h62/env && $*"; }
pods() { g "kubectl -n ${NS} get pods -l ${SEL} -o json"; }
ready_pod() { pods | P pick-pod "${1:-}"; }    # "<name> <uid>" of a Ready pod whose uid is not in $1
case ${CMD} in
port)
    echo "${PORT}" ;;
restart)
    left=${3:-}
    [ -n "${left}" ] || { echo "usage: arm.sh ${ARM} restart SECONDS" >&2; exit 2; }
    [ "${left}" -gt 0 ] 2>/dev/null || { echo "no time left to restart"; exit 3; }
    end=$(( SECONDS + left ))
    old=$(pods | P uids) || { echo "cannot list the pods"; exit 1; }
    g "kubectl -n ${NS} delete pod -l ${SEL} --wait=false" || { echo "cannot delete the pods"; exit 1; }
    while [ "${SECONDS}" -lt "${end}" ]; do
        if p=$(ready_pod "${old}"); then echo "ready: ${p}"; exit 0; fi
        sleep "${H62_POLL_S:-5}"
    done
    echo "not ready within ${left} s"
    exit 3 ;;
records)
    d=${3:?usage: arm.sh ARM records DIR}
    mkdir -p "${d}"
    read -r name uid <<< "$(ready_pod "")"
    [ -n "${name:-}" ] || { echo "no Ready pod"; exit 1; }
    echo "${name} ${uid}" > "${d}/pod.txt"
    g "kubectl -n ${NS} get pod ${name} -o json" > "${d}/pod.json"
    g "kubectl -n ${NS} exec ${name} -c ${CTR} -- grep -c amx_tile /proc/cpuinfo" > "${d}/isa.txt" 2>&1
    g "kubectl -n ${NS} exec ${name} -c ${CTR} -- sh -c 'cat /sys/fs/cgroup/cpuset.cpus.effective; nproc'" \
        > "${d}/cpuset.txt" 2>&1
    g "kubectl get nodes -o json" > "${d}/nodes.json"
    g "uptime; free -g; grep -c amx_tile /proc/cpuinfo; nproc" > "${d}/guest.txt" 2>&1
    [ -z "${EXTRA}" ] || g "kubectl -n ${NS} get ${EXTRA} -o yaml" > "${d}/extra.yaml" 2>&1
    g "kubectl get pods -A -o wide" > "${d}/pods.txt" 2>&1
    echo "records: ${d}" ;;
logs)
    d=${3:?usage: arm.sh ARM logs DIR}
    mkdir -p "${d}"
    g "kubectl get pods -A -o wide" > "${d}/pods.txt" 2>&1
    g "kubectl get events -A --sort-by=.lastTimestamp" > "${d}/events.txt" 2>&1
    g "kubectl -n ${NS} describe pods -l ${SEL}" > "${d}/describe.txt" 2>&1
    g "kubectl -n ${NS} logs -l ${SEL} -c ${CTR} --tail=1000" > "${d}/server.log" 2>&1
    echo "logs: ${d}" ;;
forward)
    g "pid=\$(cat ~/h62/pf-${ARM}.pid 2>/dev/null) && kill \${pid} 2>/dev/null;" \
      "setsid nohup kubectl -n ${NS} port-forward --address 0.0.0.0 ${SVC} ${PORT}:${SPORT}" \
      "> ~/h62/pf-${ARM}.log 2>&1 < /dev/null & echo \$! > ~/h62/pf-${ARM}.pid" ;;
unforward)
    g "pid=\$(cat ~/h62/pf-${ARM}.pid 2>/dev/null) && kill \${pid} 2>/dev/null; rm -f ~/h62/pf-${ARM}.pid" ;;
cgroup)
    read -r _ uid <<< "$(ready_pod "")"
    [ -n "${uid:-}" ] || { echo "no Ready pod" >&2; exit 1; }
    # cgroupfs driver (k3s in VM a's k3d node): pod<uid>; systemd driver (Kubespray's containerd
    # in VM b): ...-pod<uid with underscores>.slice.
    dir=$(g "find /sys/fs/cgroup -maxdepth 9 -type d \\( -name pod${uid} -o -name '*pod${uid//-/_}.slice' \\) -print -quit")
    [ -n "${dir}" ] || { echo "no cgroup for pod ${uid}" >&2; exit 1; }
    echo "exec:${SELF} ${ARM} cpustat ${dir}" ;;
cpustat)
    g "cat ${3:?usage: arm.sh ARM cpustat DIR}/cpu.stat" ;;
*)
    echo "usage: arm.sh A1|B5 port|restart SECONDS|records DIR|logs DIR|forward|unforward|cgroup|cpustat DIR" >&2
    exit 2 ;;
esac
