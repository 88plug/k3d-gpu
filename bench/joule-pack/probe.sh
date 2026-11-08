#!/usr/bin/env bash
# joule-pack (H21) live probe. One run = turn-size calibration, then up to
# 3 rounds x 5 placement arms with an idle window before every arm, on one fresh
# cluster. Raw samples and pod logs go to $OUT; analyse with
#   bench/joule-pack/joulepack.py verdict "$OUT"
#
# Run it inside the shared live lock, never two live probes at once:
#   OUT=$(mktemp -d) flock -w 10800 <lockfile> bench/joule-pack/probe.sh
#
# Env: OUT (results dir, required); LOAD_LEVEL (none|typical|heavy, default none:
# bench/load/load.py background load on its CPU set for the whole run, and one
# load.py sample window inside every idle window, tagged with the arm that
# follows, into $OUT/load.jsonl; the verdict then requires matched load); IMAGE (node image, default: the launcher's
# pinned one); SUDO_ASKPASS (sudo -A), else sudo must work non-interactively
# (sudo -n) in this process: RAPL energy_uj is root-only, and a `sudo -v` taken
# in another shell does not carry over when there is no tty.
# Reads sysfs only; writes nothing to sysfs. Self-limits: no arm starts with
# fewer than 127 s left of BUDGET (1140 s), and a watchdog ends the run at
# HARD_STOP (1200 s); the cluster is deleted on every exit path.
#
# Arms (N=12 synthetic agents, 333m request each, same work, same think gap):
#   U unlabelled (default shared balloon)  P k3d-gpu.io/cores=performance
#   E k3d-gpu.io/cores=efficient (main's agent rule)  X k3d-gpu.io/cores=ecluster
#   M 6 performance + 6 efficient (attribution arm)
set -uo pipefail
: "${OUT:?set OUT to a results directory}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NAME=k3dgpu-i-joule-pack
export KUBECONFIG="${OUT}/kubeconfig" K3D_GPU_CLUSTER="${NAME}"
[ -n "${IMAGE:-}" ] && export K3D_GPU_IMAGE="${IMAGE}"
# busybox 1.37, digest as pinned in images.lock.
AGENT_IMAGE=${AGENT_IMAGE:-$(python3 -c 'import json, sys
for i in json.load(open(sys.argv[1]))["images"]:
    if i["name"] == "docker.io/library/busybox":
        print("%s:%s@%s" % (i["name"], i["tag"], i["digest"]))' "${ROOT}/images.lock")}
N=12 REQ=333m THINK=1 WARM=8 MEAS=32 IDLE=12
TARGET_E_TURN=0.25               # s; E-core turn the work is sized to (duty ~20%)
CAL_BYTES=67108864
BUDGET=${BUDGET:-1140}
HARD_STOP=${HARD_STOP:-1200}
T0=$(date +%s)
NS=h21
LOAD_LEVEL=${LOAD_LEVEL:-none}
LOADPY="${ROOT}/bench/load/load.py"
mkdir -p "${OUT}"
[ -n "${AGENT_IMAGE}" ] || { echo "no busybox pin in images.lock" >&2; exit 2; }

left() { echo $(( BUDGET - ($(date +%s) - T0) )); }
cleanup() {
    [ -n "${WATCHDOG:-}" ] && kill "${WATCHDOG}" 2>/dev/null
    # The root sampler exits by itself within 1 s of this shell going away.
    [ -n "${SAMPLER:-}" ] && "${ELEV[@]}" kill "${SAMPLER}" 2>/dev/null
    [ -n "${CONF:-}" ] && kill "${CONF}" 2>/dev/null
    k3d cluster delete "${NAME}" >/dev/null 2>&1
    [ "${LOAD_LEVEL}" != none ] && python3 "${LOADPY}" stop >> "${OUT}/events.log" 2>&1
    rm -f "${KUBECONFIG}"
    echo "cleanup done at +$(( $(date +%s) - T0 ))s" >> "${OUT}/events.log"
}
trap cleanup EXIT
trap 'echo "stopped by signal at +$(( $(date +%s) - T0 ))s" >> "${OUT}/events.log"; exit 124' TERM INT

if k3d cluster list 2>/dev/null | awk 'NR>1 {print $1}' | grep -q '^k3dgpu-f-'; then
    echo "a k3dgpu-f-* cluster exists; refusing" >&2; exit 3
fi
if k3d cluster list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "${NAME}"; then
    echo "removing leftover ${NAME}" >> "${OUT}/events.log"; k3d cluster delete "${NAME}" >/dev/null 2>&1
fi
ELEV=(sudo -n); [ -n "${SUDO_ASKPASS:-}" ] && ELEV=(sudo -A)
"${ELEV[@]}" true || { echo "sudo (${ELEV[*]}) does not work here; set SUDO_ASKPASS" >&2; exit 5; }
( sleep "${HARD_STOP}"; kill -TERM $$ ) &
WATCHDOG=$!

# Auto-adapt: zones and pools from the host, by name (joulepack.py detect).
DETECT=$(python3 "${ROOT}/bench/joule-pack/joulepack.py" detect --sysroot /) || exit 2
printf '%s\n' "${DETECT}" > "${OUT}/detect.json"
read -r ENABLED ZONES < <(python3 -c 'import json, sys
d = json.load(sys.stdin)
z = [d["rapl"][k][0]["zone"] for k in ("package", "core", "uncore", "psys") if k in d["rapl"]]
print("yes" if d["enabled"] else "no", " ".join(z))' <<< "${DETECT}")
[ "${ENABLED}" = yes ] || { echo "joule-pack not applicable on this host: $(cat "${OUT}/detect.json")" >&2; exit 4; }
python3 -c 'import json, sys
d = json.load(sys.stdin)
for k, v in d["pools"].items():
    print(k, ",".join(map(str, v)))' <<< "${DETECT}" > "${OUT}/pools.txt"

Z=/sys/class/powercap
for z in ${ZONES}; do
    printf '%s %s %s\n' "$z" "$(cat "$Z/$z/name")" "$("${ELEV[@]}" cat "$Z/$z/max_energy_range_uj")"
done > "${OUT}/zones.txt"
awk 'NF != 3 {exit 1}' "${OUT}/zones.txt" || { echo "zones.txt malformed (sudo read failed)" >&2; exit 5; }
# Host sampler, 1 s: epoch then energy_uj of each zone, in zones.txt order.
# Bounded twice: timeout HARD_STOP+30 s, and it ends when this shell ($$) exits.
# shellcheck disable=SC2016 # expands in the root shell
"${ELEV[@]}" timeout "$(( HARD_STOP + 30 ))" bash -c 'Z=/sys/class/powercap; P=$1; shift
  while kill -0 "$P" 2>/dev/null; do l="E ${EPOCHREALTIME/,/.}"
  for z in "$@"; do l+=" $(<"$Z/$z/energy_uj")"; done; echo "$l"; sleep 1; done' _ "$$" ${ZONES} > "${OUT}/rapl.txt" &
SAMPLER=$!
sleep 3
[ "$(grep -c '^E ' "${OUT}/rapl.txt")" -ge 2 ] || { echo "RAPL sampler wrote no rows" >&2; exit 5; }
[ "${PREFLIGHT_ONLY:-0}" = 1 ] && { echo "preflight ok: $(wc -l < "${OUT}/rapl.txt") rows" >&2; exit 0; }
( while :; do
    th=$(cat /sys/devices/system/cpu/cpu*/thermal_throttle/package_throttle_count 2>/dev/null | awk '{s+=$1} END {print s+0}')
    echo "C $(date +%s) load1=$(cut -d' ' -f1 /proc/loadavg) swap_mib=$(free -m | awk '/^Swap/ {print $3}') throttle=${th} temp_mC=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1) containers=$(docker ps -q | wc -l)"
    sleep 5; done ) > "${OUT}/confounds.txt" &
CONF=$!
case "${LOAD_LEVEL}" in none|typical|heavy) ;; *) echo "LOAD_LEVEL must be none|typical|heavy" >&2; exit 2 ;; esac
if [ "${LOAD_LEVEL}" != none ]; then
    python3 "${LOADPY}" start --level "${LOAD_LEVEL}" --ttl $(( HARD_STOP + 120 )) >> "${OUT}/events.log" 2>&1 \
        || { echo "load start failed (see ${OUT}/events.log)" >&2; exit 1; }
fi
echo "LOAD ${LOAD_LEVEL} cpus=$(python3 "${LOADPY}" cpus)" >> "${OUT}/events.log"

"${ROOT}/scripts/k3d-gpu" up > "${OUT}/up.log" 2>&1 || { echo "up failed (see ${OUT}/up.log)" >&2; exit 1; }
docker pull -q "${AGENT_IMAGE}" >/dev/null && k3d image import -c "${NAME}" "${AGENT_IMAGE}" >> "${OUT}/up.log" 2>&1
kubectl create ns "${NS}" >/dev/null

# Agent body: wait for S, report cpuset, closed loop of (work, think) until END;
# cgroup CPU time at the measurement start (U0) and end (U1).
body() { # <start_at> <bytes> <label>
    cat <<EOF
      S=$1; M0=\$((S + ${WARM})); END=\$((M0 + ${MEAS})); u0=
      while [ \$(date +%s) -lt \$S ]; do sleep 0.2; done
      echo "CPUS \$(grep Cpus_allowed_list /proc/self/status | cut -f2) LAB $3"
      while [ \$(date +%s) -lt \$END ]; do
        if [ -z "\$u0" ] && [ \$(date +%s) -ge \$M0 ]; then u0=1; echo "U0 \$(date +%s) \$(grep usage_usec /sys/fs/cgroup/cpu.stat | cut -d' ' -f2)"; fi
        a=\$(cut -d' ' -f1 /proc/uptime); head -c $2 /dev/zero | sha256sum >/dev/null; b=\$(cut -d' ' -f1 /proc/uptime)
        echo "T \$(date +%s) \$a \$b"
        sleep ${THINK}
      done
      echo "U1 \$(date +%s) \$(grep usage_usec /sys/fs/cgroup/cpu.stat | cut -d' ' -f2)"
EOF
}
pod() { # <name> <arm> <label> <start_at> <bytes>
    cat <<EOF
---
apiVersion: v1
kind: Pod
metadata:
  name: $1
  namespace: ${NS}
  labels: {h21-arm: "$2"${3:+, k3d-gpu.io/cores: $3}}
spec:
  restartPolicy: Never
  terminationGracePeriodSeconds: 0
  containers:
  - name: agent
    image: ${AGENT_IMAGE}
    imagePullPolicy: IfNotPresent
    resources: {requests: {cpu: ${REQ}, memory: 16Mi}}
    command: [sh, -c]
    args:
    - |
$(body "$4" "$5" "${3:-none}")
EOF
}

# Calibration (not counted): one efficient pod at CAL_BYTES per turn; BYTES is
# sized so an uncontended E-core turn takes TARGET_E_TURN s (median turn).
at=$(( $(date +%s) + 5 ))
pod cal C efficient "${at}" "${CAL_BYTES}" | kubectl apply -f - >/dev/null
sleep $(( at + WARM + 12 - $(date +%s) ))
kubectl -n "${NS}" logs cal > "${OUT}/cal.log" 2>&1
kubectl -n "${NS}" delete pod cal --wait=false >/dev/null
BYTES=$(awk '/^T /{print $4-$3}' "${OUT}/cal.log" | sort -g | awk -v cb="${CAL_BYTES}" -v tt="${TARGET_E_TURN}" '
  {d[n++]=$1} END { if (n == 0) { print 0; exit } m=d[int((n-1)/2)]; if (m <= 0) m=0.01
    b=int(cb*tt/m/1048576)*1048576; if (b < 4194304) b=4194304; if (b > 2147483648) b=2147483648; print b }')
[ "${BYTES}" -gt 0 ] || { echo "calibration failed (see ${OUT}/cal.log)" >&2; exit 1; }
echo "BYTES ${BYTES}" >> "${OUT}/events.log"

window_idle() { # [next arm]: background-load sample tagged with the arm that follows
    local s ls=; s=$(date +%s)
    if [ -n "${1:-}" ]; then
        python3 "${LOADPY}" sample --seconds $(( IDLE - 2 )) --tag "$1" --level "${LOAD_LEVEL}" \
            --out "${OUT}/load.jsonl" 2>> "${OUT}/events.log" & ls=$!
    fi
    sleep "${IDLE}" & wait $!
    [ -n "${ls}" ] && wait "${ls}"
    echo "W idle ${s} $((s + IDLE))" >> "${OUT}/windows.txt"
}
window_arm() { # <round> <arm>
    local at=$(( $(date +%s) + 6 )) i lab p
    for i in $(seq 1 "${N}"); do
        case "$2" in
            U) lab="" ;; P) lab=performance ;; E) lab=efficient ;; X) lab=ecluster ;;
            M) if [ "$i" -le $((N / 2)) ]; then lab=performance; else lab=efficient; fi ;;
        esac
        pod "a-${2,,}-${i}" "$2" "${lab}" "${at}" "${BYTES}"
    done | kubectl apply -f - >/dev/null || echo "apply failed r$1 $2" >> "${OUT}/events.log"
    sleep $(( at + WARM + MEAS - $(date +%s) + 2 )) & wait $!
    echo "W $2 $((at + WARM)) $((at + WARM + MEAS)) round=$1" >> "${OUT}/windows.txt"
    for p in $(kubectl -n "${NS}" get pods -l "h21-arm=$2" -o name); do
        kubectl -n "${NS}" logs "$p" > "${OUT}/r$1-$2-${p##*/}.log" 2>&1
    done
    # Wait for teardown so it does not land in the next idle window.
    kubectl -n "${NS}" delete pods -l "h21-arm=$2" --wait=true --timeout=30s >/dev/null
}

ORDER=("U P E X M" "M X E P U" "E U M P X")
for r in 1 2 3; do
    for arm in ${ORDER[$((r - 1))]}; do
        if [ "$(left)" -lt $(( IDLE + WARM + MEAS + 75 )) ]; then
            echo "budget stop before r${r} ${arm}" >> "${OUT}/events.log"; break 2
        fi
        window_idle "${arm}"
        window_arm "${r}" "${arm}"
    done
done
window_idle
echo "done at +$(( $(date +%s) - T0 ))s" >> "${OUT}/events.log"
