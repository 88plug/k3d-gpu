#!/usr/bin/env bash
# H28 probe (EXPERIMENTS.md): N agent pods mmap one model file in place from an
# OCI image volume (arm T) versus N pods that first copy it into their own
# emptyDir (arm C, the per-pod copy baseline and a lower bound on a per-pod
# download). Both arms mount the same image volume with the same python image;
# the only difference is the copy. One unique model image per round, so no page
# cache carries over between rounds. Prints CSV rows: one "meta", one "pod" per
# pod, one "round" per round; also written to $WORK/h28-<utc>.csv.
#
# Usage:
#   probe.sh prep                 build the model images (outside the lock, ~2 min)
#   probe.sh run                  wait until no k3dgpu-f-* cluster exists, take
#                                 $LOCK, re-check, run an untimed warm-up round
#                                 (r0, arm C) and every round, then delete the
#                                 cluster and the model images while still
#                                 holding the lock. Rounds stop at 13 min, the
#                                 rounds process is killed at 17 min.
#   probe.sh render ARM ROUND I   print one pod manifest (ARM = T or C)
# Env:
#   IMAGE  k3d-gpu node image (k3s v1.37.1+k3s1)          (prep/run)
#   PYIMG  python image pinned by digest                    (default below)
#   LOCK   live-experiment lock file                        (run)
#   N      pods per round (8)   MIB  model size in MiB (1024)
#   ORDER  arm per round ("C T T C C T": baseline first, interleaved; the
#          warm-up round r0 is extra and never scored)
#   WORK   results and scratch kubeconfig dir (default: a mktemp dir)
# Eviction and the page-residency check run as root inside a helper container
# that mounts the node's /var/lib/rancher/k3s volume read-only: fsync, then
# posix_fadvise(DONTNEED) on this round's model file only, then mincore. No
# drop_caches and no sysfs write. RAPL needs `sudo -n`; without it W is "-".
# Before eviction a prime pod mounts the round's image volume without reading it,
# so the layer is unpacked on disk; a round whose file is not found or keeps any
# resident page after eviction is printed VOID-cold and not scored.
# Primary memory metric: resident pages (mincore, run inside the pods) of the
# shared file plus, in arm C, of every copy: the node's page-cache cost for the
# model, independent of the rest of the host. Pod-level memcg memory.current and
# file (read from the node's cgroupfs) show where that cost is charged. dCached
# is secondary only.
set -euo pipefail
PYIMG=${PYIMG:-python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f}
CL=k3dgpu-i-zero-copy-models
N=${N:-8}
MIB=${MIB:-1024}
ORDER=${ORDER:-"C T T C C T"}
GUARD_S=780    # no new round after 13 min; a round takes <= ~6.5 min
SELF=$(readlink -f "$0")

pod() {  # $1 arm  $2 round  $3 index
    local src="/m/model/blob-r$2" init=""
    if [ "$1" = C ]; then
        init="  initContainers:
    - name: copy
      image: \"${PYIMG}\"
      command: [cp, \"${src}\", /data/blob]
      volumeMounts: [{name: m, mountPath: /m, readOnly: true}, {name: data, mountPath: /data}]"
        src=/data/blob
    fi
    cat <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: h28-${1,,}-$2-$3
  labels: {h28: r$2}
spec:
  terminationGracePeriodSeconds: 0
  enableServiceLinks: false
${init}
  containers:
    - name: agent
      image: "${PYIMG}"
      command:
        - python3
        - -c
        - |
          import mmap, os, time
          f = os.open("${src}", os.O_RDONLY)
          m = mmap.mmap(f, 0, prot=mmap.PROT_READ)
          s = 0
          for i in range(0, len(m), 4096):
              s += m[i]
          print("READY", time.time_ns(), s, flush=True)
          time.sleep(10**6)
      volumeMounts: [{name: m, mountPath: /m, readOnly: true}, {name: data, mountPath: /data}]
  volumes:
    - name: m
      image: {reference: "h28-model:r$2", pullPolicy: IfNotPresent}
    - name: data
      emptyDir: {}
---
EOF
}

prime_pod() {  # $1 round: mounts the image volume, never reads it
    cat <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: h28-p-$1
spec:
  terminationGracePeriodSeconds: 0
  enableServiceLinks: false
  containers:
    - {name: p, image: "${PYIMG}", command: [sleep, "3600"], volumeMounts: [{name: m, mountPath: /m, readOnly: true}]}
  volumes:
    - {name: m, image: {reference: "h28-model:r$1", pullPolicy: IfNotPresent}}
EOF
}

# Resident 4 KiB pages of the file at $2, measured inside pod $1 with mincore.
# mincore reports the shared page cache of the inode, so it is node-wide truth.
resident() {
    kubectl exec "$1" -c agent -- python3 -c '
import ctypes, mmap, os, sys
libc = ctypes.CDLL(None, use_errno=True)
libc.mmap.restype = ctypes.c_void_p
libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
fd = os.open(sys.argv[1], os.O_RDONLY)
size = os.fstat(fd).st_size
addr = libc.mmap(None, size, mmap.PROT_READ, mmap.MAP_SHARED, fd, 0)
pages = (size + 4095) // 4096
vec = (ctypes.c_ubyte * pages)()
print(sum(v & 1 for v in vec) if libc.mincore(addr, size, vec) == 0 else -2)
' "$2" 2>/dev/null || echo -1
}

# Pod-level cgroup v2 memory.current and memory.stat file (bytes) of pod $1,
# read from the node's cgroupfs (cgroupfs or systemd driver names); "-,-" if absent.
memcg() {
    local uid
    uid=$(kubectl get pod "$1" -o jsonpath='{.metadata.uid}')
    docker exec "k3d-${CL}-server-0" sh -c '
d=$(find /sys/fs/cgroup -maxdepth 5 -type d \( -name "pod$1" -o -name "*pod$2.slice" \) 2>/dev/null | head -n 1)
[ -n "$d" ] || { echo "-,-"; exit 0; }
echo "$(cat "$d/memory.current"),$(awk "\$1 == \"file\" { print \$2 }" "$d/memory.stat")"
' sh "${uid}" "${uid//-/_}" 2>/dev/null || echo "-,-"
}

prep() {
    : "${IMAGE:?set IMAGE}"
    local r=0 d
    d=$(mktemp -d)
    for _ in 0 ${ORDER}; do
        head -c "$((MIB * 1048576))" /dev/urandom > "${d}/blob-r${r}"
        printf 'FROM scratch\nCOPY blob-r%s /model/blob-r%s\n' "${r}" "${r}" > "${d}/Dockerfile"
        docker build -q -t "h28-model:r${r}" "${d}" >/dev/null
        rm -f "${d}/blob-r${r}"
        r=$((r + 1))
    done
    rmdir "${d}" 2>/dev/null || rm -rf "${d}"
    docker pull -q "${PYIMG}" >/dev/null
}

mi() { awk -v k="$1:" '$1 == k { print $2 }' /proc/meminfo; }  # kB

confounds() {  # load1,swap_used_kB,throttle_count,pkgW (5 s)
    local t e0 e1 w=-
    t=$(cat /sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_count 2>/dev/null || echo -)
    if e0=$(sudo -n cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null); then
        sleep 5
        e1=$(sudo -n cat /sys/class/powercap/intel-rapl:0/energy_uj)
        [ "${e1}" -ge "${e0}" ] && w=$(((e1 - e0) / 5000000))
    fi
    echo "$(cut -d' ' -f1 /proc/loadavg),$(($(mi SwapTotal) - $(mi SwapFree))),${t},${w}"
}

# Evict round $1's unpacked model file(s) from the page cache; print resident
# pages left afterwards (must be 0), or -1 when no unpacked file was found.
evict() {
    docker run --rm --network none --volumes-from "k3d-${CL}-server-0:ro" --entrypoint python3 "${PYIMG}" -c '
import ctypes, glob, mmap, os, sys
libc = ctypes.CDLL(None, use_errno=True)
libc.mmap.restype = ctypes.c_void_p
libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
files = [p for p in glob.glob("/var/lib/rancher/k3s/agent/containerd/**/model/blob-r" + sys.argv[1], recursive=True) if os.path.isfile(p)]
if not files:
    print(-1); sys.exit(0)
left = 0
for p in files:
    fd = os.open(p, os.O_RDONLY)
    size = os.fstat(fd).st_size
    os.fsync(fd)
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    addr = libc.mmap(None, size, mmap.PROT_READ, mmap.MAP_SHARED, fd, 0)
    pages = (size + 4095) // 4096
    vec = (ctypes.c_ubyte * pages)()
    if libc.mincore(addr, size, vec) != 0:
        print(-2); sys.exit(0)
    left += sum(v & 1 for v in vec)
    libc.munmap(addr, size)
    os.close(fd)
print(left)
' "$1"
}

round() {  # $1 arm  $2 round  $3 kind (scored|warmup)
    local arm=$1 r=$2 kind=$3 cold c0 a0 s0 t0 c1 a1 s1 i rdy rss pss shc deadline p own tot src
    prime_pod "${r}" | kubectl apply -f - >/dev/null
    kubectl wait --for=condition=Ready "pod/h28-p-${r}" --timeout=240s >/dev/null || true
    kubectl delete pod "h28-p-${r}" --wait=true >/dev/null 2>&1 || true
    cold=$(evict "${r}")
    if [ "${cold}" != 0 ]; then
        echo "round,${arm},${r},${cold},VOID-cold,${kind}"
        return 0
    fi
    c0=$(mi Cached); a0=$(mi AnonPages); s0=$(mi SwapFree)
    t0=$(date +%s%N)
    for i in $(seq 1 "${N}"); do pod "${arm}" "${r}" "${i}"; done | kubectl apply -f - >/dev/null
    if ! kubectl wait --for=condition=Ready pod -l "h28=r${r}" --timeout=240s >/dev/null; then
        echo "round,${arm},${r},${cold},VOID-timeout,${kind}"
        kubectl delete pod -l "h28=r${r}" --wait=true >/dev/null 2>&1 || true
        return 0
    fi
    deadline=$((SECONDS + 120))
    for i in $(seq 1 "${N}"); do
        until kubectl logs "h28-${arm,,}-${r}-${i}" -c agent 2>/dev/null | grep -q READY; do
            [ "${SECONDS}" -lt "${deadline}" ] || break
            sleep 0.2
        done
    done
    sleep 3
    c1=$(mi Cached); a1=$(mi AnonPages); s1=$(mi SwapFree)
    src=$(resident "h28-${arm,,}-${r}-1" "/m/model/blob-r${r}")
    tot=${src}
    for i in $(seq 1 "${N}"); do
        p="h28-${arm,,}-${r}-${i}"
        rdy=$(kubectl logs "${p}" -c agent 2>/dev/null | awk '/READY/ { print $2 }')
        read -r rss pss shc < <(kubectl exec "${p}" -c agent -- cat /proc/1/smaps_rollup \
            | awk '$1 == "Rss:" { r = $2 } $1 == "Pss:" { p = $2 } $1 == "Shared_Clean:" { s = $2 } END { print r, p, s }')
        own=${src}
        if [ "${arm}" = C ]; then
            own=$(resident "${p}" /data/blob)
            if [ "${own}" -ge 0 ] && [ "${tot}" -ge 0 ]; then tot=$((tot + own)); else tot=-1; fi
        fi
        echo "pod,${arm},${r},${i},$(( ${rdy:-0} > 0 ? (rdy - t0) / 1000000 : -1 )),${rss},${pss},${shc},${own},$(memcg "${p}"),${kind}"
    done
    echo "round,${arm},${r},${cold},$(( tot >= 0 ? tot * 4 : -1 )),$((c1 - c0)),$((a1 - a0)),$((s0 - s1)),$(confounds),${kind}"
    kubectl delete pod -l "h28=r${r}" --wait=true >/dev/null
}

f_cluster() {  # true while any k3dgpu-f-* cluster exists
    k3d cluster list -o json | python3 -c 'import json, sys; sys.exit(0 if any(c["name"].startswith("k3dgpu-f-") for c in json.load(sys.stdin)) else 1)'
}

# Runs under the lock: re-check, rounds (killed at 17 min), then delete the
# cluster and the model images before flock releases the lock. Exit 75 = retry.
locked() {
    local rc=0 r=0
    if f_cluster; then echo "RETRY,k3dgpu-f-* appeared while waiting for the lock" >&2; return 75; fi
    timeout -k 15 1020 "${SELF}" _rounds || rc=$?
    k3d cluster delete "${CL}" >/dev/null 2>&1 || true
    for _ in 0 ${ORDER}; do docker image rm -f "h28-model:r${r}" >/dev/null 2>&1 || true; r=$((r + 1)); done
    return "${rc}"
}

run_locked() {
    local start=${SECONDS} r=0 arm snap imgs k3s free
    WORK=${WORK:-$(mktemp -d)}
    export KUBECONFIG="${WORK}/kubeconfig"
    trap 'exit 143' TERM INT
    free=$(df -Pk "$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /)" | awk 'NR == 2 { print $4 }')
    if [ "${free}" -lt $(( (2 * N + 2) * MIB * 1024 )) ]; then
        echo "ABORT,disk,free_kB=${free} below (2N+2) x MIB" >&2
        exit 1
    fi
    if [ "$(mi MemAvailable)" -lt $((N * MIB * 1024 * 3 / 2)) ]; then
        echo "ABORT,mem,MemAvailable_kB=$(mi MemAvailable) below 1.5 x N x MIB; rerun with MIB=512 (prep again)" >&2
        exit 1
    fi
    k3d cluster create "${CL}" --image "${IMAGE}" --wait --kubeconfig-update-default=false \
        --kubeconfig-switch-context=false >/dev/null
    k3d kubeconfig get "${CL}" > "${KUBECONFIG}"
    imgs=("${PYIMG}")
    for _ in 0 ${ORDER}; do imgs+=("h28-model:r${r}"); r=$((r + 1)); done
    k3d image import -c "${CL}" "${imgs[@]}" >/dev/null
    snap=$(docker exec "k3d-${CL}-server-0" sh -c 'cat /var/lib/rancher/k3s/agent/etc/containerd/config*.toml' 2>/dev/null \
        | sed -n 's/^[[:space:]]*snapshotter[[:space:]]*=[[:space:]]*"\([a-z0-9_-]*\)".*/\1/p' | head -n 1)
    k3s=$(kubectl version -o json | python3 -c 'import json, sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])')
    echo "meta,k3s=${k3s},snapshotter=${snap:-unknown},pyimg=${PYIMG},N=${N},MiB=${MIB},order=${ORDER// /},kernel=$(uname -r),cpu=$(sed -n 's/^model name[[:space:]]*: //p' /proc/cpuinfo | head -n 1)"
    echo "# pod,arm,round,i,ready_ms,rss_kB,pss_kB,shared_clean_kB,own_file_resident_pages,podcg_memory_current_B,podcg_file_B,kind"
    echo "# round,arm,round,cold_pages_left,model_resident_kB,dCached_kB,dAnon_kB,swapout_kB,load1,swap_used_kB,throttle_count,pkgW,kind"
    round C 0 warmup
    r=1
    for arm in ${ORDER}; do
        if (( SECONDS - start > GUARD_S )); then echo "ABORT,time-guard,round=${r}"; break; fi
        round "${arm}" "${r}" scored
        r=$((r + 1))
    done
}

case "${1:-}" in
    prep) prep ;;
    run)
        : "${LOCK:?set LOCK (live-experiment lock file)}" "${IMAGE:?set IMAGE}"
        WORK=${WORK:-$(mktemp -d)}
        export WORK IMAGE PYIMG N MIB ORDER
        out="${WORK}/h28-$(date -u +%Y%m%dT%H%M%SZ).csv"
        while :; do
            while f_cluster; do sleep 30; done
            rc=0
            flock -w 10800 "${LOCK}" "${SELF}" _locked | tee -a "${out}" || rc=${PIPESTATUS[0]}
            [ "${rc}" -eq 75 ] || exit "${rc}"
        done
        ;;
    _locked) locked ;;
    _rounds) run_locked ;;
    render) pod "${2:?arm T|C}" "${3:?round}" "${4:?index}" ;;
    *) echo "usage: $0 prep|run|render ARM ROUND I" >&2; exit 2 ;;
esac
