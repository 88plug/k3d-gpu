#!/usr/bin/env bash
# Prefill guard tests (K3D_GPU_PREFILL_GUARD). No cluster, no hardware, no host
# writes: k3d, kubectl and docker are stubs that log their calls, and the host
# is a fake sysfs tree under K3D_GPU_SYSROOT.
#
# Covers the guard manifest (share/nri-balloons-guard.yml) against the default
# one, `up` with and without the guard, `down` writing the recorded
# scaling_max_freq back, and the `doctor --perf` suggestion.
#
# Usage: tests/prefill_guard_test.sh

set -uo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
K3D_GPU="${REPO}/scripts/k3d-gpu"
DEFAULT_YML="${REPO}/share/nri-balloons.yml"
GUARD_YML="${REPO}/share/nri-balloons-guard.yml"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
PASS=0 FAILS=0

ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; [ -z "${2:-}" ] || sed 's/^/  | /' <<< "$2"; }
# check <name> <command...>: pass when the command succeeds.
check() { local name=$1; shift; if "$@"; then ok "${name}"; else fail "${name}"; fi; }

# ---- manifests -----------------------------------------------------------------

# body <file>: the rendered objects, without the comment header.
body() { awk '/^---$/ {f = 1} f' "$1"; }
# nonpolicy <file>: every document except the BalloonsPolicy one; policydoc: that one only.
nonpolicy() {
    body "$1" | awk '
        /^---$/ { if (doc != "" && !skip) printf "%s", doc; doc = ""; skip = 0 }
        { doc = doc $0 "\n" }
        /^kind: BalloonsPolicy$/ { skip = 1 }
        END { if (doc != "" && !skip) printf "%s", doc }'
}
policydoc() {
    body "$1" | awk '
        /^---$/ { if (keep) printf "%s", doc; doc = ""; keep = 0 }
        { doc = doc $0 "\n" }
        /^kind: BalloonsPolicy$/ { keep = 1 }
        END { if (keep) printf "%s", doc }'
}

if [ -f "${GUARD_YML}" ]; then ok "guard manifest exists"; else fail "guard manifest exists (${GUARD_YML})"; fi
if [ -n "$(nonpolicy "${DEFAULT_YML}")" ] && [ "$(nonpolicy "${DEFAULT_YML}")" = "$(nonpolicy "${GUARD_YML}" 2>/dev/null)" ]; then
    ok "guard manifest: CRDs, RBAC and DaemonSet identical to the default manifest"
else
    fail "guard manifest: CRDs, RBAC and DaemonSet identical to the default manifest"
fi
# The guard policy is the default policy plus the cpuClass key and the cpuClasses
# list: with those taken out again it must be the default policy, line for line.
# (toYaml sorts keys, so cpuClass becomes the first key of the efficient item.)
unguard() {
    awk '/^  cpuClasses:$/ {skip = 1; next}
         skip && /^  [^ -]/ {skip = 0}
         skip {next}
         /^  - cpuClass: prefill-guard$/ {fold = 1; next}
         fold && /^    matchExpressions:$/ {print "  - matchExpressions:"; fold = 0; next}
         {print}'
}
removed=$(diff <(policydoc "${DEFAULT_YML}") <(policydoc "${GUARD_YML}" 2>/dev/null | unguard))
if [ -z "${removed}" ] && [ -n "$(policydoc "${GUARD_YML}" 2>/dev/null)" ]; then
    ok "guard policy = default policy + cpuClass + cpuClasses"
else
    fail "guard policy = default policy + cpuClass + cpuClasses" "${removed}"
fi
gp=$(policydoc "${GUARD_YML}" 2>/dev/null)
# The efficient balloon type carries the guard class; nothing else does.
eff=$(awk '/^  balloonTypes:/ {b = 1} b && /^  - / {t = ""} b {t = t $0 "\n"} b && /^    name: efficient$/ {print t; exit}' <<< "${gp}")
check "efficient balloon uses cpuClass prefill-guard" grep -Eq '^  [- ] cpuClass: prefill-guard$' <<< "${eff}"
check "only one balloon type sets a cpuClass" test "$(grep -Ec '^  [- ] cpuClass:' <<< "${gp}")" = 1
check "cpuClasses defines default with the host-max placeholder" \
    grep -Pzq '  - maxFreq: __K3D_GPU_HOST_MAX__\n    name: default\n' <<< "${gp}"
check "cpuClasses defines prefill-guard with the cap placeholder" \
    grep -Pzq '  - maxFreq: __K3D_GPU_GUARD_MAX__\n    name: prefill-guard\n' <<< "${gp}"
# v0.14.0 resolves "turbo"/"base"/"min" from the first online CPU only, and its
# cpufreq writer sets min/max/governor but never EPP: neither may appear.
check "guard policy uses no symbolic frequency" bash -c '! grep -Eq "Freq: (turbo|base|min)$" <<< "$1"' _ "${gp}"
check "guard policy sets no EPP (not written by nri-plugins v0.14.0)" bash -c '! grep -q energyPerformancePreference <<< "$1"' _ "${gp}"
check "guard header names the chart version" grep -q 'nri-resource-policy-balloons v0.14.0' "${GUARD_YML}"

# ---- stubs ---------------------------------------------------------------------

BIN="${WORK}/bin"
mkdir -p "${BIN}"
cat > "${BIN}/k3d" <<'EOF'
#!/bin/bash
[ "$1" = version ] && { echo "k3d version v0.0.0-stub"; exit 0; }
{ printf 'k3d'; printf ' %s' "$@"; echo; } >> "${STUB_LOG}"
EOF
cat > "${BIN}/kubectl" <<'EOF'
#!/bin/bash
echo "kubectl $*" >> "${STUB_LOG}"
case "$*" in
    "get nodes -o go-template="*) : ;;
    "get nodes -l "*" -o name") echo "node/k3d-gpu-server-0" ;;
esac
exit 0
EOF
# docker exec reads the restore lines from stdin; STUB_EXEC_RC fails it.
cat > "${BIN}/docker" <<'EOF'
#!/bin/bash
[ "$1" = info ] && exit 0
if [ "$1" = exec ]; then
    { printf 'docker'; printf ' %s' "$@"; echo; echo "--- stdin"; cat; echo "--- end"; } >> "${STUB_LOG}"
    exit "${STUB_EXEC_RC:-0}"
fi
exit 0
EOF
chmod +x "${BIN}/k3d" "${BIN}/kubectl" "${BIN}/docker"

put() { mkdir -p "$(dirname "$1$2")"; printf '%s\n' "$3" > "$1$2"; }
# cpus <root> <first> <last> <cpuinfo_max kHz> <scaling_max kHz> <cpuinfo_min kHz>
cpus() {
    local c d
    for c in $(seq "$2" "$3"); do
        d="/sys/devices/system/cpu/cpu${c}/cpufreq"
        put "$1" "${d}/scaling_driver" intel_pstate
        put "$1" "${d}/scaling_governor" powersave
        put "$1" "${d}/energy_performance_preference" balance_performance
        put "$1" "${d}/cpuinfo_max_freq" "$4"
        put "$1" "${d}/scaling_max_freq" "$5"
        put "$1" "${d}/cpuinfo_min_freq" "$6"
        put "$1" "${d}/scaling_min_freq" "$6"
    done
}
# raptor <name>: an i9-13900T-shaped tree: P 0-15 (8-11 favoured at 5.3 GHz), E 16-31.
raptor() {
    local r="${WORK}/$1"
    mkdir -p "${r}/sys/devices/cpu_core" "${r}/sys/devices/cpu_atom" "${r}/dev"
    echo 0-15 > "${r}/sys/devices/cpu_core/cpus"
    echo 16-31 > "${r}/sys/devices/cpu_atom/cpus"
    put "${r}" /sys/devices/system/cpu/online 0-31
    cpus "${r}" 0 7 5100000 5100000 800000
    cpus "${r}" 8 11 5300000 5300000 800000
    cpus "${r}" 12 15 5100000 5100000 800000
    cpus "${r}" 16 31 3900000 3900000 800000
    echo "${r}"
}
# throttle <root> <cpu> <package count> <package total ms> <core count> [package power-limit count]
throttle() {
    local t="/sys/devices/system/cpu/cpu$2/thermal_throttle"
    put "$1" "${t}/package_throttle_count" "$3"
    put "$1" "${t}/package_throttle_total_time_ms" "$4"
    put "$1" "${t}/package_throttle_max_time_ms" 417
    put "$1" "${t}/core_throttle_count" "$5"
    [ -z "${6:-}" ] || put "$1" "${t}/package_power_limit_count" "$6"
}

# run <root> <state dir> <command...>: k3d-gpu against the fake tree; sets OUT, RC, CALLS.
run() {
    local root=$1 state=$2
    shift 2
    : > "${WORK}/calls"
    OUT=$(env -i HOME="${WORK}/home" PATH="${BIN}:/usr/bin:/bin" K3D_GPU_SYSROOT="${root}" \
        K3D_GPU_SHARE="${REPO}/share" K3D_GPU_STATE_DIR="${state}" STUB_LOG="${WORK}/calls" \
        ${ENVS[@]+"${ENVS[@]}"} bash "${K3D_GPU}" "$@" 2>&1 | sed 's/\x1b\[[0-9;]*m//g')
    RC=${PIPESTATUS[0]}
    CALLS=$(cat "${WORK}/calls")
}
# has <name> <ERE> <text> / hasnt <name> <ERE> <text>
has()   { if grep -Eq -- "$2" <<< "$3"; then ok "$1"; else fail "$1 (want /$2/)" "$3"; fi; }
hasnt() { if grep -Eq -- "$2" <<< "$3"; then fail "$1 (unwanted /$2/)" "$3"; else ok "$1"; fi; }

MANIFESTS=/var/lib/rancher/k3s/server/manifests/nri-balloons.yaml

# ---- up --------------------------------------------------------------------------

r=$(raptor up-off)
ENVS=()
run "${r}" "${WORK}/state-off" up
check "up without guard succeeds (rc=${RC})" test "${RC}" -eq 0
hasnt "up without guard mounts no guard manifest" "nri-balloons.yaml" "${CALLS}"
check "up without guard writes no state" test ! -e "${WORK}/state-off"

r=$(raptor up-pct)
ENVS=(K3D_GPU_PREFILL_GUARD=50%)
run "${r}" "${WORK}/state" up
check "up with 50% guard succeeds (rc=${RC})" test "${RC}" -eq 0
has "up mounts the rendered guard manifest over the baked one" \
    "--volume=${WORK}/state/gpu/nri-balloons.yaml:${MANIFESTS}@server:\*" "${CALLS}"
g="${WORK}/state/gpu/nri-balloons.yaml"
has "50% of the 3900 MHz E-core max is 1950000 kHz" '^  - maxFreq: 1950000kHz$' "$(cat "${g}" 2>/dev/null)"
has "default class uncaps to the highest CPU max (favoured cores)" '^  - maxFreq: 5300000kHz$' "$(cat "${g}" 2>/dev/null)"
hasnt "no placeholder left in the rendered manifest" '__K3D_GPU_' "$(cat "${g}" 2>/dev/null)"
check "rendered manifest = guard template with values filled" \
    test -s "${g}" -a "$(sed -e 's/__K3D_GPU_GUARD_MAX__/1950000kHz/' -e 's/__K3D_GPU_HOST_MAX__/5300000kHz/' "${GUARD_YML}")" = "$(cat "${g}" 2>/dev/null)"
rec="${WORK}/state/gpu/scaling_max_freq"
has "up records every CPU's scaling_max_freq" '^31 3900000$' "$(cat "${rec}" 2>/dev/null)"
check "restore record holds 32 CPUs" test "$(wc -l < "${rec}" 2>/dev/null)" = 32
has "restore record keeps favoured-core values" '^8 5300000$' "$(cat "${rec}" 2>/dev/null)"
has "up says what the guard writes and how to undo it" 'prefill guard: .*E-cores.*1950 MHz' "${OUT}"
has "up names the revert" 'k3d-gpu down' "${OUT}"

# An earlier record (cluster deleted outside k3d-gpu) holds the real originals: keep it.
put "${r}" /sys/devices/system/cpu/cpu20/cpufreq/scaling_max_freq 1950000
mkdir -p "${WORK}/state-keep/gpu"
printf '20 3900000\n' > "${WORK}/state-keep/gpu/scaling_max_freq"
run "${r}" "${WORK}/state-keep" up
has "an existing restore record is kept" '^20 3900000$' "$(cat "${WORK}/state-keep/gpu/scaling_max_freq")"
check "an existing restore record is not overwritten" test "$(wc -l < "${WORK}/state-keep/gpu/scaling_max_freq")" = 1

r=$(raptor up-khz)
ENVS=(K3D_GPU_PREFILL_GUARD=2000000)
run "${r}" "${WORK}/state-khz" up
has "a kHz guard value is used as-is" '^  - maxFreq: 2000000kHz$' "$(cat "${WORK}/state-khz/gpu/nri-balloons.yaml" 2>/dev/null)"

ENVS=(K3D_GPU_PREFILL_GUARD=off)
run "${r}" "${WORK}/state-offword" up
hasnt "guard=off mounts nothing" "nri-balloons.yaml" "${CALLS}"

for bad in abc 0% 101% 1.5% 500 4000000 3900000 -5 '50%;id'; do
    ENVS=("K3D_GPU_PREFILL_GUARD=${bad}")
    run "${r}" "${WORK}/state-bad" up
    if [ "${RC}" -ne 0 ] && ! grep -q '^k3d cluster create' <<< "${CALLS}" && [ ! -e "${WORK}/state-bad" ]; then
        ok "guard '${bad}' is rejected before any cluster is created"
    else
        fail "guard '${bad}' is rejected before any cluster is created (rc=${RC})" "${OUT}"
    fi
done
ENVS=(K3D_GPU_PREFILL_GUARD=500)
run "${r}" "${WORK}/state-bad" up
has "a cap below the E-core minimum names the minimum" '800000' "${OUT}"

# ACPI processor passive cooling lowers scaling_max_freq through its own freq QoS
# request; reading it then would record the cooled value, and 'down' would write
# it back as the user cap, keeping it after the cooling ends. Refuse to record.
r=$(raptor up-cooling)
put "${r}" /sys/class/thermal/cooling_device0/type "TCC Offset"
put "${r}" /sys/class/thermal/cooling_device0/cur_state 4
put "${r}" /sys/class/thermal/cooling_device7/type Processor
put "${r}" /sys/class/thermal/cooling_device7/cur_state 0
ENVS=(K3D_GPU_PREFILL_GUARD=50%)
run "${r}" "${WORK}/state-cool0" up
check "idle processor cooling does not block the guard (rc=${RC})" test "${RC}" -eq 0
put "${r}" /sys/class/thermal/cooling_device7/cur_state 2
run "${r}" "${WORK}/state-cool" up
check "active processor cooling refuses the guard" test "${RC}" -ne 0
has "the refusal names the cooling device" 'cooling_device7' "${OUT}"
check "active processor cooling records nothing" test ! -e "${WORK}/state-cool"
hasnt "active processor cooling creates no cluster" '^k3d cluster create' "${CALLS}"
# An earlier record already holds the pre-guard values: nothing is read, nothing refused.
mkdir -p "${WORK}/state-cool-keep/gpu"
printf '20 3900000\n' > "${WORK}/state-cool-keep/gpu/scaling_max_freq"
run "${r}" "${WORK}/state-cool-keep" up
check "active cooling with an existing record proceeds (rc=${RC})" test "${RC}" -eq 0

# Not hybrid: a single core type has no E-cores to cap.
x="${WORK}/xeon"
mkdir -p "${x}/sys" "${x}/dev/dsa"
touch "${x}/dev/dsa/wq0.0"
cpus "${x}" 0 3 3800000 3800000 800000
ENVS=(K3D_GPU_PREFILL_GUARD=50%)
run "${x}" "${WORK}/state-xeon" up
check "guard on a non-hybrid host fails" test "${RC}" -ne 0
has "non-hybrid refusal explains why" 'hybrid' "${OUT}"
hasnt "non-hybrid refusal creates no cluster" '^k3d cluster create' "${CALLS}"

# Hybrid host, hybrid vendor left out: nothing would deploy the policy.
ENVS=(K3D_GPU_PREFILL_GUARD=50% K3D_GPU_VENDORS=amx)
run "${r}" "${WORK}/state-novendor" up
has "guard without the hybrid vendor is refused" 'add hybrid to K3D_GPU_VENDORS' "${OUT}"

# A Helm release owns the policy: the guard has no baked manifest to replace.
ENVS=(K3D_GPU_PREFILL_GUARD=50% K3D_GPU_BALLOONS=external)
run "${r}" "${WORK}/state-external" up
check "guard with an external balloons policy fails" test "${RC}" -ne 0
has "guard with an external balloons policy is refused" 'K3D_GPU_BALLOONS=external' "${OUT}"
check "the external refusal writes no state" test ! -e "${WORK}/state-external/gpu"
hasnt "the external refusal creates no cluster" '^k3d cluster create' "${CALLS}"

# A second guarded cluster would record the first one's caps as the originals.
ENVS=(K3D_GPU_PREFILL_GUARD=50% K3D_GPU_CLUSTER=other)
run "${r}" "${WORK}/state" up
has "a second guarded cluster is refused" "cluster 'gpu' still holds a guard record" "${OUT}"
check "the refused cluster writes no state" test ! -e "${WORK}/state/other"
hasnt "the refused cluster is not created" '^k3d cluster create' "${CALLS}"

# ---- down ------------------------------------------------------------------------

ENVS=()
run "${r}" "${WORK}/state" down
has "down writes the record back through the node container" '^docker exec -i k3d-gpu-server-0 sh -c ' "${CALLS}"
has "down feeds the recorded values" '^31 3900000$' "${CALLS}"
first_exec=$(grep -n '^docker exec' <<< "${CALLS}" | head -n1 | cut -d: -f1)
first_del=$(grep -n '^k3d cluster delete' <<< "${CALLS}" | head -n1 | cut -d: -f1)
check "restore runs before the cluster is deleted" test "${first_exec:-99}" -lt "${first_del:-0}"
check "down removes the state after a restore" test ! -e "${WORK}/state/gpu"

run "${r}" "${WORK}/state-none" down
hasnt "down without a record runs no docker exec" '^docker exec' "${CALLS}"
has "down without a record still deletes" '^k3d cluster delete gpu' "${CALLS}"

ENVS=(STUB_EXEC_RC=1)
run "${r}" "${WORK}/state-keep" down
has "a failed restore prints the manual command" 'sudo tee /sys/devices/system/cpu/cpu\$c/cpufreq/scaling_max_freq' "${OUT}"
check "a failed restore keeps the record" test -f "${WORK}/state-keep/gpu/scaling_max_freq"

# A corrupt record is never fed to the node.
mkdir -p "${WORK}/state-bad-rec/gpu"
printf '1 3900000\n2 $(reboot)\n' > "${WORK}/state-bad-rec/gpu/scaling_max_freq"
ENVS=()
run "${r}" "${WORK}/state-bad-rec" down
hasnt "a malformed record is not executed" '^docker exec' "${CALLS}"
has "a malformed record is reported" 'malformed' "${OUT}"

# ---- doctor --perf -------------------------------------------------------------------

r=$(raptor doc-hot)
throttle "${r}" 0 22236660 102128310 651794
throttle "${r}" 16 22173096 102257307 14262
ENVS=()
run "${r}" "${WORK}/state-doc" doctor --perf
hasnt "no guard requested or recorded, no guard section" 'prefill guard' "${OUT}"
ENVS=(K3D_GPU_PREFILL_GUARD=50%)
run "${r}" "${WORK}/state-doc" doctor --perf
has "doctor flags package throttling on a hybrid host" 'note: package throttled 22236660 times since boot' "${OUT}"
has "doctor cites the counter file" 'from: +/sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_\{count,total_time_ms\}' "${OUT}"
has "doctor gives the exact apply" 'apply: +k3d-gpu down && K3D_GPU_PREFILL_GUARD=50% k3d-gpu up' "${OUT}"
has "doctor gives the exact revert" 'revert: +k3d-gpu down && k3d-gpu up' "${OUT}"
has "doctor states the cap the suggestion means" '1950 MHz' "${OUT}"
has "doctor points at the pre-registered test" 'H5' "${OUT}"

r=$(raptor doc-pl)
throttle "${r}" 0 10 20 0 7
run "${r}" "${WORK}/state-doc" doctor --perf
has "doctor shows the power-limit counter when the kernel exposes it" 'package_power_limit_count 7' "${OUT}"

r=$(raptor doc-cool)
throttle "${r}" 0 0 0 0
run "${r}" "${WORK}/state-doc" doctor --perf
hasnt "no throttling, no guard suggestion" 'PREFILL_GUARD' "${OUT}"
has "no throttling is reported ok" 'ok: +package_throttle_count 0' "${OUT}"

throttle "${x}" 0 500 900 3
run "${x}" "${WORK}/state-doc" doctor --perf
hasnt "non-hybrid host gets no guard suggestion" 'PREFILL_GUARD' "${OUT}"

r=$(raptor doc-active)
throttle "${r}" 0 5 5 0
ENVS=()
mkdir -p "${WORK}/state-active/gpu"
printf '16 3900000\n' > "${WORK}/state-active/gpu/scaling_max_freq"
run "${r}" "${WORK}/state-active" doctor --perf
has "doctor reports a recorded guard and its restore file" "prefill guard record for cluster 'gpu': ${WORK}/state-active/gpu/scaling_max_freq" "${OUT}"

echo ""
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
