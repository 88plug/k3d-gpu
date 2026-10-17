#!/usr/bin/env bash
# noisy.sh — a CPU-burning noisy-neighbour pod (stress-ng) for interference tests.
#
# Usage:
#   bench/noisy.sh start [--cores performance|efficient|ecluster|none] [--workers N] [--cpu Q]
#                        [--stressor cpu|stream] [--stream-bytes SIZE]
#                        [--name NAME] [--namespace NS] [--dry-run]
#   bench/noisy.sh stop  [--name NAME] [--namespace NS]
#   bench/noisy.sh status [--name NAME] [--namespace NS]
#
#   --cores    performance / efficient / ecluster label the pod k3d-gpu.io/cores=<value>,
#              so on a hybrid node the NRI balloons policy pins it to P-cores, E-cores
#              or the E-cores of one L2 cluster; none (default) leaves it unlabelled
#   --stressor cpu (default, stress-ng --cpu) or stream (stress-ng --stream, DRAM bandwidth)
#   --stream-bytes  stream: --stream-l3-size, the total buffer size stress-ng splits
#              across the workers (default 128m, so every worker count has the same
#              DRAM footprint: 3 buffers of 128m in total)
#   --workers  stress-ng workers (default 4)
#   --cpu      CPU request = limit (default: the worker count); it sizes the
#              balloon, so --workers above --cpu oversubscribe that balloon
#   --dry-run  print the pod manifest instead of applying it
#
# Uses kubectl's current context (set KUBECONFIG / --context via KUBECTL_ARGS).
# start waits for the pod to run and prints the CPUs it may use.

set -euo pipefail

# stress-ng V0.22.01, the upstream image built by ColinIanKing/stress-ng's
# release workflow (tag = release commit), pinned by digest.
NOISY_IMAGE="${NOISY_IMAGE:-ghcr.io/colinianking/stress-ng:72a9d0cae0f420a887991d4024d3101c4af7a4fc@sha256:2ea9a7c3828eda1b1ca3d0b0c5a669ec252f9caf97a6a33aedcea6b2a66d1cbe}"
read -r -a KARGS <<< "${KUBECTL_ARGS:-}"

die() { echo "noisy.sh: $*" >&2; exit 2; }
kc() { kubectl ${KARGS[@]+"${KARGS[@]}"} "$@"; }

ACTION=${1:-}
[ $# -eq 0 ] || shift
CORES=none WORKERS=4 CPU="" NAME=noisy-neighbour NS=default DRY=0 STRESSOR=cpu STREAM_BYTES=128m
while [ $# -gt 0 ]; do
    case "$1" in
        --cores)     CORES=${2:?}; shift 2 ;;
        --workers)   WORKERS=${2:?}; shift 2 ;;
        --cpu)       CPU=${2:?}; shift 2 ;;
        --stressor)  STRESSOR=${2:?}; shift 2 ;;
        --stream-bytes) STREAM_BYTES=${2:?}; shift 2 ;;
        --name)      NAME=${2:?}; shift 2 ;;
        --namespace) NS=${2:?}; shift 2 ;;
        --dry-run)   DRY=1; shift ;;
        *) die "unknown option $1" ;;
    esac
done
case "${CORES}" in performance|efficient|ecluster|none) ;; *) die "--cores must be performance, efficient, ecluster or none" ;; esac
case "${STRESSOR}" in cpu|stream) ;; *) die "--stressor must be cpu or stream" ;; esac
[[ "${STREAM_BYTES}" =~ ^[1-9][0-9]*[kmg]?$ ]] || die "--stream-bytes must be a size (128m, 1g)"
[[ "${WORKERS}" =~ ^[1-9][0-9]*$ ]] || die "--workers must be a positive integer"
CPU=${CPU:-${WORKERS}}
[[ "${CPU}" =~ ^[0-9]+(\.[0-9]+|m)?$ ]] || die "--cpu must be a CPU quantity (4, 2.5, 500m)"
[[ "${NAME}" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || die "--name must be a DNS label"

manifest() {
    local args="--cpu ${WORKERS}" mem=64Mi labels="\"app.kubernetes.io/name\": \"k3d-gpu-noisy\""
    [ "${CORES}" = none ] || labels+=", \"k3d-gpu.io/cores\": \"${CORES}\""
    # stream: 3 buffers of --stream-l3-size in total plus index arrays; 1Gi covers 128m.
    [ "${STRESSOR}" = cpu ] || { args="--stream ${WORKERS} --stream-l3-size ${STREAM_BYTES}"; mem=1Gi; }
    # Logs the CPUs the pod may use first, then burns them until deleted.
    cat <<EOF
{
  "apiVersion": "v1",
  "kind": "Pod",
  "metadata": {"name": "${NAME}", "namespace": "${NS}", "labels": {${labels}}},
  "spec": {
    "restartPolicy": "Never",
    "terminationGracePeriodSeconds": 5,
    "containers": [{
      "name": "stress-ng",
      "image": "${NOISY_IMAGE}",
      "command": ["/bin/sh", "-c",
        "grep Cpus_allowed_list /proc/self/status; exec stress-ng ${args} --timeout 0 --metrics-brief"],
      "resources": {
        "requests": {"cpu": "${CPU}", "memory": "${mem}"},
        "limits":   {"cpu": "${CPU}", "memory": "${mem}"}
      }
    }]
  }
}
EOF
}

start() {
    if [ "${DRY}" -eq 1 ]; then manifest; return; fi
    manifest | kc apply -f -
    local phase="" deadline=$((SECONDS + 180)) line=""
    while [ "${SECONDS}" -lt "${deadline}" ]; do
        phase=$(kc -n "${NS}" get pod "${NAME}" -o jsonpath='{.status.phase}' 2>/dev/null || true)
        line=$(kc -n "${NS}" logs "${NAME}" 2>/dev/null | grep -m1 Cpus_allowed_list || true)
        [ "${phase}" = Running ] && [ -n "${line}" ] && break
        case "${phase}" in Failed|Succeeded) break ;; esac
        sleep 2
    done
    [ "${phase}" = Running ] || die "pod ${NAME} is ${phase:-not created} (kubectl -n ${NS} describe pod ${NAME})"
    echo "noisy: ${NAME} running, cores=${CORES} stressor=${STRESSOR} workers=${WORKERS} cpu=${CPU}; ${line:-Cpus_allowed_list not logged yet}"
}

stop() {
    kc -n "${NS}" delete pod "${NAME}" --ignore-not-found --wait=true --timeout=60s
}

status() {
    kc -n "${NS}" get pod "${NAME}" -o wide
    kc -n "${NS}" logs "${NAME}" 2>/dev/null | grep -m1 Cpus_allowed_list || true
}

case "${ACTION}" in
    start)  start ;;
    stop)   stop ;;
    status) status ;;
    *) sed -n '2,24p' "$0"; exit 2 ;;
esac
