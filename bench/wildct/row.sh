#!/usr/bin/env bash
# row.sh ROW [ARGS...]: one wildct row's command as the wildct pre-registration lists it ("Per
# row") with amendment R1, run as bench/wildct/hold.sh's CMD (hold.sh exports
# K3D_GPU_RAPL_EXEC, rule W4, and WILDCT_HOLD, the hold's name) from the shipped repository
# archive. The release's node image and the models are staged first (bench/wildct/RESUME.md).
#   smoke        the wiring smoke: `up` with the GPU opt-out and W3 (cluster k3dgpu-w-smoke), the
#                pod wait, the pod GPU scan, `down`; no inference request (discarded, disclosed)
#   h52w         H52w's procedure: bench/wildct/h52w.sh OUT (it sets its own cluster, opt-out
#                and W3, and runs the installed release's default node image; no IMAGE)
#   h18w         H18's bench/sandbox_round.sh, runs 1-3
#   h25w         H25's bench/shared-prefix/run.sh
#   h34w         H34's bench/hetero-spec/h34.sh
#   h28w-prep    bench/zero-copy-models/probe.sh prep (typical's model images only, r0-r6)
#   h28w         bench/zero-copy-models/probe.sh run (its plain k3d cluster create gets W3 and the
#                two Intel GPU disables `up` passes with intel not active: W8, amendment R1)
#   h57w HOLD MODE [ARM...]  H57d's bench/h57d/hold.sh with the wildct knobs (inner lock): holds
#                1-3 perf and 4 quality, in their registered arm order (no ARM); the spare hold is
#                HOLD 5 with the failed hold's mode and its arms in its order (H57d's rule)
#   h57w-convert H57d's bench/h57d/convert.sh (drafter GGUF, Od's graph.pbtxt) under the host
#                lock, not in a hold; at most 3600 s
#   h57wb        H57wb's start reading (/ available, MemAvailable) and its decision; not a hold
#   h59w HOLD [REPEATS]  H59w: bench/h59/hold.sh wildct (inner lock; H59 amendment R1): holds 1-3
#                perf and 4 quality in their registered orders; a spare 5 or 6 names the failed
#                hold it repeats
#   h59w-prep    bench/h59/prep.sh wildct: the downloads, then the drafter conversion under the
#                host lock; not in a hold
# Env: IMAGE  the release node image as name:tag@sha256:digest (every row but h52w and h57wb)
#      OUT    output directory (default ~/k3dgpu-bench-wildct/runs/${WILDCT_HOLD:-ROW}/out)
#      WILDCT_ROW_PRINT=1  print the environment (one NAME=value per line) and the command, run nothing
#      WILDCT_ROW_CHECK=1  run every check (the k3d shim's included), print and run nothing: exit 0 when
#                          the row would run (bench/wildct/RESUME.md's launch checks before a hold)
set -uo pipefail
W=${HOME}/k3dgpu-bench-wildct
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "${HERE}/../.." && pwd)
row=${1:?usage: row.sh ROW [HOLD MODE [ARM...]]}
export PATH=${W}/bin:${PATH}
W3='--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<2Gi,imagefs.available<2Gi,nodefs.inodesFree<1%'
OPTOUT=auto,-nvidia,-intel
# W8 for a plain `k3d cluster create` (H28w's probe.sh), which never reads K3D_GPU_VENDORS: the
# node image bakes both Intel GPU stacks into k3s's auto-deploy manifests, and intel-gpu-plugin's
# only nodeSelector is the architecture. These are the two disables `up` passes when intel is not
# active (wildct amendment R1).
NOINTEL='--disable=intel-gpu-plugin --disable=intel-gpu-dra'
G=${W}/h57
OUT=${OUT:-${W}/runs/${WILDCT_HOLD:-${row}}/out}
need_image() { [[ "${IMAGE:-}" =~ ^[^@]+:[^@]+@sha256:[0-9a-f]{64}$ ]] || { echo "row.sh: set IMAGE to the release node image as name:tag@sha256:<digest>" >&2; exit 2; }; }
E=() ARGV=() RUNS=1
case ${row} in
    smoke)
        need_image
        E=(KUBECONFIG="${OUT}/kubeconfig" K3D_GPU_SHARE="${W}/k3d-gpu/usr/share/k3d-gpu" K3D_GPU_CLUSTER=k3dgpu-w-smoke
           K3D_GPU_IMAGE="${IMAGE}" K3D_GPU_VENDORS="${OPTOUT}" K3D_GPU_K3S_ARGS="${W3}")
        ARGV=(smoke) ;;
    h52w)
        ARGV=(bash "${REPO}/bench/wildct/h52w.sh" "${OUT}") ;;
    h18w)
        need_image
        E=(IMAGE="${IMAGE}" OUT="${OUT}" K3D_GPU_CLUSTER=k3dgpu-w-sbx K3D_GPU_VENDORS="${OPTOUT}" K3D_GPU_K3S_ARGS="${W3}")
        ARGV=(bash "${REPO}/bench/sandbox_round.sh") RUNS=3 ;;
    h25w)
        need_image
        E=(IMAGE="${IMAGE}" MODELS="${W}/models" OUT="${OUT}" LEVELS=typical CLUSTER=k3dgpu-w-h25 SEED_BASE=2560 HOLD=1440
           LOAD_CPUS=20-23 K3D_GPU_VENDORS="${OPTOUT}" K3D_GPU_K3S_ARGS="${W3}")
        ARGV=(bash "${REPO}/bench/shared-prefix/run.sh") ;;
    h34w)
        need_image
        E=(K3D_GPU_IMAGE="${IMAGE}" H34_CLUSTER=k3dgpu-w-h34 H34_VENDORS="${OPTOUT}" H34_LEVELS=typical H22_MODELS="${W}/models"
           LOAD_CPUS=20-23 K3D_GPU_VENDORS="${OPTOUT}" K3D_GPU_K3S_ARGS="${W3}")
        ARGV=(bash "${REPO}/bench/hetero-spec/h34.sh" "${OUT}") ;;
    h28w-prep)
        need_image
        E=(IMAGE="${IMAGE}" LEVELS=typical)
        ARGV=(bash "${REPO}/bench/zero-copy-models/probe.sh" prep) ;;
    h28w)
        need_image
        E=(WORK="${OUT}" IMAGE="${IMAGE}" CLUSTER=k3dgpu-w-h28 LEVELS=typical HOLD=1440 LOCK="${W}/inner.lock" NODE_CPUS=0-15
           LOAD_CPUS=20-23 K3D_GPU_K3S_ARGS="${W3} ${NOINTEL}")
        ARGV=(bash "${REPO}/bench/zero-copy-models/probe.sh" run) ;;
    h57w)
        need_image
        hold=${2:?usage: row.sh h57w HOLD MODE [ARM...]} mode=${3:?usage: row.sh h57w HOLD MODE [ARM...]}
        case ${hold}:${mode}:$# in
            [123]:perf:3 | 4:quality:3 | 5:perf:[456] | 5:quality:[456]) ;;
            *) echo "row.sh: h57w ${*:2}: holds 1-3 are perf and 4 quality, with no ARM; the spare 5 takes the failed hold's mode and its arms" >&2; exit 2 ;;
        esac
        for a in "${@:4}"; do
            case ${a} in Od | Ld | Ld-mtp) ;; *) echo "row.sh: h57w: unknown arm ${a} (Od, Ld, Ld-mtp)" >&2; exit 2 ;; esac
        done
        [ "$(printf '%s\n' "${@:4}" | sort | uniq -d)" = "" ] || { echo "row.sh: h57w: an arm is repeated" >&2; exit 2; }
        E=(G="${G}" H57D_G="${G}" LOCK="${W}/inner.lock" K3D_REAL="${W}/bin/k3d" H57D_ROW=H57w H57D_PREFIX=k3dgpu-w-
           H57D_LOAD1_MAX=12 H57D_VENDORS="${OPTOUT}" H57D_IMAGE="${IMAGE}" H57D_K3S_ARGS="${W3}")
        ARGV=(bash "${REPO}/bench/h57d/hold.sh" "${hold}" "${mode}" "${@:4}")
        [ "${WILDCT_ROW_PRINT:-0}" = 1 ] || cmp -s "${REPO}/bench/h57d/k3d-shim.sh" "${G}/bin/k3d" \
            || { echo "row.sh: ${G}/bin/k3d is not bench/h57d/k3d-shim.sh (install it first)" >&2; exit 2; } ;;
    h57w-convert)
        # convert.sh takes the host lock itself, so it never runs inside hold.sh (which holds it).
        [ -z "${WILDCT_HOLD:-}" ] || { echo "row.sh: h57w-convert runs outside hold.sh" >&2; exit 2; }
        E=(G="${G}" LOCK="${W}/live.lock")
        ARGV=(timeout -k 30 3600 bash "${REPO}/bench/h57d/convert.sh") ;;
    h57wb)
        ARGV=(h57wb) ;;
    h59w)
        need_image
        case ${2:-}:${3:-}:$# in
            [1234]::2 | [56]:[1234]:3) ;;
            *) echo "row.sh: h59w ${*:2}: holds 1-3 perf and 4 quality take no other argument; a spare 5 or 6 names the hold it repeats" >&2; exit 2 ;;
        esac
        E=(G="${W}/h59" LOCK="${W}/inner.lock" K3D_REAL="${W}/bin/k3d" H59_IMAGE="${IMAGE}")
        ARGV=(bash "${REPO}/bench/h59/hold.sh" wildct "${@:2}")
        [ "${WILDCT_ROW_PRINT:-0}" = 1 ] || cmp -s "${REPO}/bench/h59/k3d-shim.sh" "${W}/h59/bin/k3d" \
            || { echo "row.sh: ${W}/h59/bin/k3d is not bench/h59/k3d-shim.sh (install it first)" >&2; exit 2; } ;;
    h59w-prep)
        # prep.sh takes the host lock itself for the conversion, so it never runs inside hold.sh.
        [ -z "${WILDCT_HOLD:-}" ] || { echo "row.sh: h59w-prep runs outside hold.sh" >&2; exit 2; }
        E=(G="${W}/h59" LOCK="${W}/live.lock" PY_HF="${W}/hfvenv/bin/python")
        ARGV=(bash "${REPO}/bench/h59/prep.sh" wildct) ;;
    *) echo "row.sh: unknown row ${row}" >&2; exit 2 ;;
esac
[ "${WILDCT_ROW_CHECK:-0}" != 1 ] || exit 0
if [ "${WILDCT_ROW_PRINT:-0}" = 1 ]; then
    [ "${#E[@]}" -eq 0 ] || printf '%s\n' "${E[@]}"
    echo "${ARGV[*]}$([ "${RUNS}" = 1 ] || echo " (runs 1-${RUNS})")"
    exit 0
fi

# smoke: the wiring smoke with the installed release's launcher (as h52w.sh runs it).
smoke() {
    local K=${W}/k3d-gpu/usr/bin/k3d-gpu up_rc ready t0
    mkdir -p "${OUT}"
    t0=$(date +%s)
    timeout -k 10 600 "${K}" up > "${OUT}/up.log" 2>&1; up_rc=$?
    echo "up rc=${up_rc} in $(( $(date +%s) - t0 )) s" | tee -a "${OUT}/rc.txt"
    grep -E 'opted out|Accelerators' "${OUT}/up.log"
    timeout -k 10 60 docker inspect -f 'privileged={{.HostConfig.Privileged}} devices={{.HostConfig.Devices}} runtime={{.HostConfig.Runtime}} gpus={{.HostConfig.DeviceRequests}} restarts={{.RestartCount}}' \
        "k3d-${K3D_GPU_CLUSTER}-server-0" > "${OUT}/node-container.txt" 2>&1
    ready="not waited (up rc=${up_rc})"
    if [ "${up_rc}" = 0 ]; then
        ready="no (60 polls, every 5 s)"
        t0=$(date +%s)
        for _ in $(seq 60); do
            if bash "${HERE}/ready.sh"; then
                ready="yes after $(( $(date +%s) - t0 )) s"
                break
            fi
            sleep 5
        done
    fi
    echo "pods ready: ${ready}" | tee -a "${OUT}/rc.txt"
    timeout -k 5 60 kubectl get pods -A -o wide > "${OUT}/pods.txt" 2>&1
    timeout -k 5 60 kubectl get node -o jsonpath='{.items[0].spec.taints}{"\n"}{.items[0].status.allocatable}{"\n"}' > "${OUT}/node.txt" 2>&1
    timeout -k 5 60 kubectl get pods -A -o json 2>/dev/null | python3 "${HERE}/gpu_scan.py" > "${OUT}/gpu-scan.txt" 2>&1
    tail -1 "${OUT}/gpu-scan.txt" | tee -a "${OUT}/rc.txt"
    timeout -k 10 600 "${K}" down > "${OUT}/down.log" 2>&1
    echo "down rc=$?" | tee -a "${OUT}/rc.txt"
    [ "${up_rc}" = 0 ] && [[ "${ready}" == yes* ]] && grep -q '^GPU-SCAN clean$' "${OUT}/rc.txt"
}

# h57wb: the start reading of H57wb's row: runs only with / >= 15 + 70 GB available and
# MemAvailable >= 100 GiB; otherwise INCONCLUSIVE (not run). Neither reading downloads anything.
h57wb() {
    local avail mem d
    avail=$(df -P -BG / | awk 'NR==2 {gsub("G", "", $4); print $4}')
    mem=$(awk '/^MemAvailable:/ {printf "%.1f", $2 / 1048576}' /proc/meminfo)
    if [ "${avail}" -ge 85 ] && awk -v m="${mem}" 'BEGIN {exit !(m >= 100)}'; then
        d="gates met: H57wb has no runner on this host; stop and register one before any H57w hold"
    else
        d="INCONCLUSIVE (not run): a gate is not met"
    fi
    mkdir -p "${W}/runs/H57wb"
    echo "$(date -u +%FT%TZ) root_avail_gb=${avail} (need >= 85) mem_available_gib=${mem} (need >= 100): ${d}" | tee "${W}/runs/H57wb/reading.txt"
}

case ${row} in
    smoke) ( export "${E[@]}"; smoke ) ;;
    h57wb) h57wb ;;
    *)
        mkdir -p "${OUT}"
        rc=0
        for i in $(seq "${RUNS}"); do
            if [ "${RUNS}" = 1 ]; then
                env "${E[@]}" "${ARGV[@]}" || rc=$?
            else
                env "${E[@]}" "${ARGV[@]}" "${i}" 2>&1 | tee "${OUT}/run-${i}.txt"
                st=${PIPESTATUS[0]}
                [ "${st}" = 0 ] || rc=${st}
            fi
        done
        exit "${rc}" ;;
esac
