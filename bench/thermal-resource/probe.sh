#!/usr/bin/env bash
# H23 probe (EXPERIMENTS.md, "H23 pre-registration"): thermal headroom as a
# per-pod consumable resource. Calibrates a thermal model on plain docker
# containers, brings up one cluster, advertises the fitted budget as the node
# extended resources power.k3d-gpu.io/watts (arm T) and k3d-gpu.io/slots (arm C),
# then runs 24 fixed-work stress-ng pods per run in the order B T C C T B B T C.
# One RESULT line per run goes to $OUT/results.txt; the cluster is deleted on exit.
#
# Run it only under the shared live lock, after no k3dgpu-f-* cluster exists:
#   flock -w 10800 "$LOCK" bench/thermal-resource/probe.sh
# Env:   OUT      results dir (default: a new dir under $TMPDIR)
#        BUDGET_S wall-clock budget in seconds (default 1110 = 18:30)
#        K3D_GPU_IMAGE  node image for `k3d-gpu up` (default: the launcher's pin)
#        DRY_RUN=1      print the pod manifests for each arm and exit (no cluster)
#        CAL_ONLY=1     stop after calibration and the budget gate (no cluster)
# Exit 4 = INCONCLUSIVE before any run: the fitted budget admits < 4 or all 24
# pods (headroom_model.py degenerate()), so arms T/C cannot differ from B.
# Root is only used to read RAPL energy_uj (sudo -n); nothing in sysfs is written.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
MODEL="${ROOT}/research/thermal-resource/headroom_model.py"
# stress-ng pin from research/tseries-envelope/README.md
STRESS=ghcr.io/colinianking/stress-ng:72a9d0cae0f420a887991d4024d3101c4af7a4fc@sha256:2ea9a7c3828eda1b1ca3d0b0c5a669ec252f9caf97a6a33aedcea6b2a66d1cbe
TAG=h23-stress:pin
NAME=k3dgpu-i-thermal-resource
ORDER=(B T C C T B B T C)
NPODS=24 SOLO_S=12 CAP_S=75 COOL_CAP_S=45
BUDGET_S=${BUDGET_S:-1110}
T_START=$(date +%s)
OUT=${OUT:-$(mktemp -d "${TMPDIR:-/tmp}/h23.XXXXXX")}
mkdir -p "${OUT}"
export KUBECONFIG="${OUT}/kubeconfig" K3D_GPU_CLUSTER="${NAME}"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "${OUT}/probe.log" >&2; }
left() { echo $(( BUDGET_S - ($(date +%s) - T_START) )); }

# ---- host readings (read-only) ---------------------------------------------------
TEMP_FILE=""
for h in /sys/class/hwmon/hwmon*; do
    [ "$(cat "${h}/name" 2>/dev/null)" = coretemp ] || continue
    for l in "${h}"/temp*_label; do
        [ "$(cat "${l}")" = "Package id 0" ] && TEMP_FILE=${l%_label}_input
    done
done
temp_c() { awk '{printf "%.1f", $1 / 1000}' "${TEMP_FILE}"; }
# first package RAPL zone, by name
RAPL=""
for z in /sys/class/powercap/intel-rapl:[0-9]; do
    case "$(cat "${z}/name" 2>/dev/null)" in package*) RAPL=${z}; break ;; esac
done
energy_uj() { sudo -n cat "${RAPL}/energy_uj"; }
E_RANGE=$(cat "${RAPL:-/nonexistent}/max_energy_range_uj" 2>/dev/null || echo 0)
throttle_ms() { cat /sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_total_time_ms; }
swap_mib() { awk '/^SwapTotal/ {t = $2} /^SwapFree/ {f = $2} END {printf "%d", (t - f) / 1024}' /proc/meminfo; }
load1() { cut -d' ' -f1 /proc/loadavg; }
# watts over <seconds>
watts() {
    local e0 e1
    e0=$(energy_uj); sleep "$1"; e1=$(energy_uj)
    awk -v a="${e0}" -v b="${e1}" -v s="$1" -v r="${E_RANGE}" 'BEGIN {d = b - a; if (d < 0) d += r; printf "%.1f", d / 1e6 / s}'
}
# sample <seconds> <csv>: one "watts,celsius" line per second
sample() {
    local i
    for ((i = 0; i < $1; i++)); do echo "$(watts 1),$(temp_c)" >> "$2"; done
}

# ---- pods ----------------------------------------------------------------------
# manifest <arm> <cpu request> <w_hot> <w_cool> <ops_hot> <ops_cool>: 24 pods as a List
manifest() {
    local arm=$1 cpu=$2 i kind extra ops w res
    echo "---"
    echo "apiVersion: v1"
    echo "kind: List"
    echo "items:"
    for ((i = 0; i < NPODS; i++)); do
        if [ $((i % 2)) -eq 0 ]; then kind=hot extra="" ops=$5 w=$3; else kind=cool extra=', "--cpu-load", "40"' ops=$6 w=$4; fi
        case "${arm}" in
            B) res="{cpu: \"${cpu}\"}" ;;
            T) res="{cpu: \"${cpu}\", power.k3d-gpu.io/watts: \"${w}\"}" ;;
            C) res="{cpu: \"${cpu}\", k3d-gpu.io/slots: \"1\"}" ;;
        esac
        cat <<EOF
- apiVersion: v1
  kind: Pod
  metadata: {name: h23-${kind}-${i}, labels: {app: h23, h23-kind: ${kind}}}
  spec:
    restartPolicy: Never
    terminationGracePeriodSeconds: 0
    containers:
    - name: s
      image: ${TAG}
      imagePullPolicy: Never
      command: ["stress-ng", "--cpu", "1", "--cpu-method", "matrixprod", "--cpu-ops", "${ops}"${extra}]
      resources: {requests: ${res}, limits: ${res}}
EOF
    done
}

if [ "${DRY_RUN:-0}" = 1 ]; then
    for arm in B T C; do manifest "${arm}" 1 6 3 1000 500; done
    exit 0
fi

cleanup() {
    local c
    [ -z "${WATCHDOG:-}" ] || { kill "${WATCHDOG}" 2>/dev/null; wait "${WATCHDOG}" 2>/dev/null; } || true
    for c in $(docker ps -aq --filter name='^h23-cal-'); do docker rm -f "${c}" >/dev/null 2>&1 || true; done
    if k3d cluster list -o json 2>/dev/null | grep -q "\"name\":\"${NAME}\""; then
        log "deleting cluster ${NAME}"
        k3d cluster delete "${NAME}" >/dev/null 2>&1 || true
    fi
    docker image rm "${TAG}" >/dev/null 2>&1 || true
    log "done, $(( $(date +%s) - T_START )) s in the lock; results in ${OUT}"
}
trap cleanup EXIT
trap 'exit 143' TERM INT
# Watchdog: the whole probe stays inside BUDGET_S + 60 s; TERM runs cleanup.
# Its sleep is killed with it, so no child keeps the caller's flock fd open.
(
    trap 'kill "${sp}" 2>/dev/null; exit 0' TERM
    sleep $(( BUDGET_S + 60 )) & sp=$!
    wait "${sp}" && kill -TERM $$ 2>/dev/null
) &
WATCHDOG=$!

[ -n "${TEMP_FILE}" ] || { log "no coretemp Package id 0 sensor"; exit 2; }
[ -n "${RAPL}" ] && [ "${E_RANGE}" -gt 0 ] || { log "no package RAPL zone"; exit 2; }
energy_uj >/dev/null || { log "sudo -n cannot read RAPL energy_uj"; exit 2; }
docker pull -q "${STRESS}" >/dev/null && docker tag "${STRESS}" "${TAG}"
help=$(docker run --rm --entrypoint stress-ng "${TAG}" --help 2>&1 || true)
for f in --cpu-ops --cpu-load --cpu-method; do
    grep -q -- "${f}" <<< "${help}" || { log "stress-ng in ${STRESS} has no ${f}"; exit 2; }
done
log "governor $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null) epp $(cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null)"
python3 "${MODEL}" host > "${OUT}/host.json"
log "host $(cat "${OUT}/host.json")"

# ---- calibration (docker, outside k8s) -------------------------------------------
# solo_rate <extra args>: bogo ops per second of one stressor for 4 s
solo_rate() {
    docker run --rm --name h23-cal-solo --entrypoint stress-ng "${TAG}" --cpu 1 --cpu-method matrixprod "$@" \
        --timeout 4 --metrics-brief 2>&1 \
        | awk '/metrc/ {for (i = 1; i <= NF; i++) if ($i == "cpu" && $(i + 2) + 0 > 0) {printf "%.0f", $(i + 1) / $(i + 2); exit}}'
}
th_bg0=$(throttle_ms); : > "${OUT}/fit.csv"
log "idle window 30 s"
sample 30 "${OUT}/fit.csv"
th_bg=$(( $(throttle_ms) - th_bg0 ))
p_idle_med=$(sort -t, -k1 -n "${OUT}/fit.csv" | awk -F, '{a[NR] = $1} END {print a[int((NR + 1) / 2)]}')
r_hot=$(solo_rate); r_cool=$(solo_rate --cpu-load 40)
[ -n "${r_hot}" ] && [ -n "${r_cool}" ] || { log "could not read stress-ng metrics"; exit 2; }
ops_hot=$(( r_hot * SOLO_S )) ops_cool=$(( r_cool * SOLO_S ))
# window <kind> <extra args>: 8 containers for 52 s; prints the mean watts of the last 30 s
# (the first 20 s let the package and heatsink settle before the fit samples)
window() {
    local k=$1 i
    shift
    for i in 1 2 3 4 5 6 7 8; do
        docker run -d --rm --name "h23-cal-$k-$i" --entrypoint stress-ng "${TAG}" --cpu 1 --cpu-method matrixprod "$@" --timeout 52 >/dev/null
    done
    sleep 20
    : > "${OUT}/win-$k.csv"; sample 30 "${OUT}/win-$k.csv"; cat "${OUT}/win-$k.csv" >> "${OUT}/fit.csv"
    sleep 3
    awk -F, '{s += $1} END {printf "%.1f", s / NR}' "${OUT}/win-$k.csv"
}
log "hot window"; p_hot=$(window hot)
log "cool window"; p_cool=$(window cool --cpu-load 40)
python3 "${MODEL}" fit "${OUT}/fit.csv" > "${OUT}/fit.json"
w_hot=$(awk -v a="${p_hot}" -v b="${p_idle_med}" 'BEGIN {w = (a - b) / 8; printf "%d", (w == int(w)) ? w : int(w) + 1}')
w_cool=$(awk -v a="${p_cool}" -v b="${p_idle_med}" 'BEGIN {w = (a - b) / 8; printf "%d", (w == int(w)) ? w : int(w) + 1}')
[ "${w_hot}" -ge 1 ] || w_hot=1
[ "${w_cool}" -ge 1 ] || w_cool=1
read -r p_i t_i r_th < <(python3 -c 'import json, sys; f = json.load(open(sys.argv[1])); print(f["p_idle_w"], f["t_idle_c"], f["r_th_c_per_w"])' "${OUT}/fit.json")
t_tcc=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["t_tcc_c"])' "${OUT}/host.json")
read -r pl1 pl2 < <(python3 -c 'import json, sys; h = json.load(open(sys.argv[1])); print(h["pl1_w"], h["pl2_w"])' "${OUT}/host.json")
# gate <p_now> <file>: budget at p_now into <file>; exit 4 (INCONCLUSIVE) when degenerate
gate() {
    python3 "${MODEL}" budget --p-idle "${p_i}" --t-idle "${t_i}" --r-th "${r_th}" --t-tcc "${t_tcc}" \
        --p-now "$1" --pl1 "${pl1}" --pl2 "${pl2}" --ewma "$1" --w-hot "${w_hot}" --w-cool "${w_cool}" \
        --npods "${NPODS}" > "$2"
    log "budget $(cat "$2")"
    reason=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("degenerate") or "")' "$2")
    if [ -n "${reason}" ]; then
        echo "INCONCLUSIVE degenerate budget: ${reason}" | tee "${OUT}/verdict.txt"
        exit 4
    fi
}
log "fit $(cat "${OUT}/fit.json") p_hot ${p_hot} p_cool ${p_cool} w_hot ${w_hot} w_cool ${w_cool} ops ${ops_hot}/${ops_cool} bg_throttle_ms ${th_bg}/30s"

gate "${p_idle_med}" "${OUT}/budget-pre.json"
[ "${CAL_ONLY:-0}" != 1 ] || { log "CAL_ONLY: stop before the cluster"; exit 0; }

# ---- cluster ----------------------------------------------------------------------
log "k3d-gpu up"
timeout 300 "${ROOT}/scripts/k3d-gpu" up > "${OUT}/up.log" 2>&1
k3d image import "${TAG}" -c "${NAME}" >/dev/null 2>&1
node=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
alloc_cpu=$(kubectl get node "${node}" -o jsonpath='{.status.allocatable.cpu}')
cpu=1
if [ "${alloc_cpu%m}" != "${alloc_cpu}" ]; then alloc_m=${alloc_cpu%m}; else alloc_m=$(( alloc_cpu * 1000 )); fi
if [ "${alloc_m}" -lt $(( NPODS * 1000 )) ]; then
    cpu=500m
    log "DEVIATION allocatable cpu ${alloc_cpu} < ${NPODS}; every arm requests cpu 500m"
fi
p_now=$(watts 5)
gate "${p_now}" "${OUT}/budget.json"
read -r budget nslots < <(python3 -c 'import json, sys; b = json.load(open(sys.argv[1])); print(b["watts"], b["slots"])' "${OUT}/budget.json")
log "budget $(cat "${OUT}/budget.json") (T admits ~$(( budget * 2 / (w_hot + w_cool) )) of ${NPODS})"
kubectl patch node "${node}" --subresource=status --type=json -p \
    "[{\"op\":\"add\",\"path\":\"/status/capacity/power.k3d-gpu.io~1watts\",\"value\":\"${budget}\"},{\"op\":\"add\",\"path\":\"/status/capacity/k3d-gpu.io~1slots\",\"value\":\"${nslots}\"}]" >/dev/null
ok=0
for _ in $(seq 30); do
    if [ "$(kubectl get node "${node}" -o jsonpath='{.status.allocatable.k3d-gpu\.io/slots}')" = "${nslots}" ] &&
        [ "$(kubectl get node "${node}" -o jsonpath='{.status.allocatable.power\.k3d-gpu\.io/watts}')" = "${budget}" ]; then
        ok=1; break
    fi
    sleep 1
done
log "node allocatable $(kubectl get node "${node}" -o jsonpath='{.status.allocatable}')"
[ "${ok}" = 1 ] || { log "allocatable never showed watts=${budget} slots=${nslots}"; exit 2; }

# ---- runs -------------------------------------------------------------------------
echo "# arm run start_C pre_W load1 swap0 swap1 makespan_s censored throttle_ms throttle_net_ms energy_J pkg_W max_C failed gap_med_s gap_max_s void done throttle_ms_per_pod" > "${OUT}/results.txt"
start_ref=""
run=0
for arm in "${ORDER[@]}"; do
    run=$((run + 1))
    if [ "$(left)" -lt $(( CAP_S + COOL_CAP_S + 30 )) ]; then log "budget left $(left) s: stopping before run ${run}"; break; fi
    for ((c = 0; c < COOL_CAP_S; c++)); do
        [ -z "${start_ref}" ] && break
        awk -v t="$(temp_c)" -v r="${start_ref}" 'BEGIN {exit !(t <= r + 3)}' && break
        sleep 1
    done
    pre_w=$(watts 5); t0c=$(temp_c); [ -n "${start_ref}" ] || start_ref=${t0c}
    l1=$(load1); s0=$(swap_mib); th0=$(throttle_ms); e0=$(energy_uj); maxc=${t0c}
    manifest "${arm}" "${cpu}" "${w_hot}" "${w_cool}" "${ops_hot}" "${ops_cool}" > "${OUT}/pods-${run}.yaml"
    ts=$(date +%s.%N)
    kubectl apply -f "${OUT}/pods-${run}.yaml" >/dev/null
    censored=0 hot=0
    while :; do
        phases=$(kubectl get pods -l app=h23 -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}')
        done_n=$(grep -cE '^(Succeeded|Failed)$' <<< "${phases}" || true)
        tc=$(temp_c)
        maxc=$(awk -v a="${maxc}" -v b="${tc}" 'BEGIN {print (b > a) ? b : a}')
        if awk -v t="${tc}" -v j="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["tjmax_c"])' "${OUT}/host.json")" 'BEGIN {exit !(t >= j)}'; then hot=$((hot + 1)); else hot=0; fi
        [ "${hot}" -le 4 ] || { log "package at TjMax for > 2 s: abort"; exit 3; }
        [ "${done_n}" -ge "${NPODS}" ] && break
        if awk -v a="${ts}" -v b="$(date +%s.%N)" -v c="${CAP_S}" 'BEGIN {exit !(b - a >= c)}'; then censored=1; break; fi
        sleep 0.5
    done
    te=$(date +%s.%N); e1=$(energy_uj); th1=$(throttle_ms); s1=$(swap_mib)
    kubectl get pods -l app=h23 -o json > "${OUT}/pods-${run}.json"
    read -r failed gap_med gap_max < <(python3 - "${OUT}/pods-${run}.json" <<'PY'
import json, sys, datetime
def ts(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").timestamp()
items = json.load(open(sys.argv[1]))["items"]
failed = sum(1 for p in items if p["status"].get("phase") == "Failed")
gaps = []
for p in items:
    for c in p["status"].get("containerStatuses", []):
        st = c.get("state", {})
        started = (st.get("terminated") or st.get("running") or {}).get("startedAt")
        if started:
            gaps.append(ts(started) - ts(p["metadata"]["creationTimestamp"]))
gaps.sort()
med = gaps[len(gaps) // 2] if gaps else -1
print(failed, med, gaps[-1] if gaps else -1)
PY
)
    mk=$(awk -v a="${ts}" -v b="${te}" 'BEGIN {printf "%.2f", b - a}')
    ej=$(awk -v a="${e0}" -v b="${e1}" -v r="${E_RANGE}" 'BEGIN {d = b - a; if (d < 0) d += r; printf "%.1f", d / 1e6}')
    pw=$(awk -v j="${ej}" -v m="${mk}" 'BEGIN {printf "%.1f", j / m}')
    thd=$(( th1 - th0 ))
    thn=$(awk -v d="${thd}" -v b="${th_bg}" -v m="${mk}" 'BEGIN {printf "%d", d - b * m / 30}')
    void=0
    awk -v w="${pre_w}" -v i="${p_idle_med}" 'BEGIN {exit !(w > i + 15)}' && void=1
    [ $(( s1 - s0 )) -gt 512 ] && void=1
    # a censored run did less work, so its throttle time is not comparable
    [ "${censored}" = 1 ] && void=1
    tpp=$(awk -v t="${thn}" -v n="${done_n}" 'BEGIN {printf "%.1f", (n > 0) ? t / n : -1}')
    echo "${arm} ${run} ${t0c} ${pre_w} ${l1} ${s0} ${s1} ${mk} ${censored} ${thd} ${thn} ${ej} ${pw} ${maxc} ${failed} ${gap_med} ${gap_max} ${void} ${done_n} ${tpp}" | tee -a "${OUT}/results.txt"
    kubectl delete pods -l app=h23 --grace-period=0 --wait=true >/dev/null 2>&1 || true
done

# ---- summary ------------------------------------------------------------------------
python3 - "${OUT}/results.txt" <<'PY' | tee "${OUT}/summary.txt"
import sys
rows = [l.split() for l in open(sys.argv[1]) if l.strip() and not l.startswith("#")]
for arm in "BTC":
    r = [x for x in rows if x[0] == arm and x[17] == "0"]
    for col, name in ((7, "makespan_s"), (9, "throttle_ms"), (10, "throttle_net_ms"), (19, "throttle_ms_per_pod"), (12, "pkg_W")):
        v = sorted(float(x[col]) for x in r)
        if v:
            print("%s %-16s n=%d median=%.2f min=%.2f max=%.2f spread=%.2f" % (arm, name, len(v), v[len(v) // 2], v[0], v[-1], v[-1] - v[0]))
PY
