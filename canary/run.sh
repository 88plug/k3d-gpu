#!/usr/bin/env bash
# One engine's canary on this machine: a GitHub-hosted runner in .github/workflows/canary.yml.
#
#   canary/run.sh ID              a runtime engine: its surface check, then serve and probe it
#   canary/run.sh --surface ID    a surface engine: the surface check only
#
# In order: (1) free disk, when the image needs more than is free and CANARY_FREE_DISK=1
# (it deletes the runner's preinstalled toolchains: never set it on a workstation);
# (2) record the CPU's avx2, avx512f and amx_tile flags; (3) the model at its pinned
# revision, from the cache (actions/cache restores it) or downloaded; (4) the image's
# size against the free disk (too-big), then docker pull, 3 attempts with backoff;
# (5) the surface check (canary/surface.py); (6) start the engine as the launcher would
# (canary/engines.toml) and probe it (canary/probe.py): ready within the timeout, one
# completion, one stream, its version; (7) an engine that cannot run on this ISA
# (isa_signal, no avx512f) is isa-skip, not a failure.
#
# Writes ${CANARY_OUT_DIR:-./canary-out}/<id>.json (the canary-result.json entry: kind,
# pinned_ref, verdict, hard, exact, cpu_flags, detail) and <id>.log (the engine's log),
# and exits non-zero unless the verdict is pass (an informational engine exits 0).
#
# Environment: CANARY_MODELS (model cache, default ~/.cache/k3d-gpu-canary),
# CANARY_READY_TIMEOUT (seconds, default the engine's), CANARY_PULL_BACKOFF (seconds,
# default 20), CANARY_CPUINFO (default /proc/cpuinfo), CANARY_FREE_BYTES (free disk,
# default df of docker's root), HF_ENDPOINT (default https://huggingface.co).

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR=${CANARY_OUT_DIR:-${PWD}/canary-out}
surface_only=""
if [ "${1:-}" = --surface ]; then surface_only=1; shift; fi
ID=${1:?usage: canary/run.sh [--surface] ID}
mkdir -p "${OUT_DIR}"
RESULT=${OUT_DIR}/${ID}.json
LOG=${OUT_DIR}/${ID}.log
WORK=$(mktemp -d "${TMPDIR:-/tmp}/canary-${ID}.XXXXXX")
NAME=canary-${ID}
: > "${LOG}"
E_KIND=${surface_only:+surface} E_REF="" E_INFORMATIONAL="" E_SURFACE=""
cleanup() {
    docker rm -f "${NAME}" > /dev/null 2>&1
    # an engine writes as its own user into the copied model tree
    rm -rf "${WORK}" 2>/dev/null || docker run --rm -v "${WORK}:/w" --entrypoint rm "${E_REF}" -rf /w/models > /dev/null 2>&1
    rm -rf "${WORK}" 2>/dev/null
    return 0
}
trap cleanup EXIT

say() { echo "canary ${ID}: $*" >&2; }

# finish <verdict> <detail>: write the result and exit. A pass needs the surface check
# (when there was one) and the probe (for a runtime engine) to pass.
finish() {
    R_VERDICT=$1 R_DETAIL=$2 python3 - "${RESULT}" "${WORK}" <<'PY'
import json, os, sys
out, work = sys.argv[1:]
env = os.environ
verdict, detail = env["R_VERDICT"], [env["R_DETAIL"]] if env["R_DETAIL"] else []
hard = exact = False
def load(name):
    try:
        with open(os.path.join(work, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
surface, probe = load("surface.json"), load("probe.json")
if surface:
    detail.append(surface["detail"])
if probe:
    detail += probe["failures"] + probe["notes"]
    if probe["text"] is not None:
        detail.append("text %r" % probe["text"])
    if probe["ready_s"] is not None:
        detail.append("ready after %ss, %d stream chunks%s" % (probe["ready_s"], probe["stream_chunks"],
                                                               ", version %s" % probe["version"] if probe["version"] else ""))
if verdict == "pass":
    if surface and surface["verdict"] != "pass":
        verdict = surface["verdict"]
    elif probe is not None and not probe["hard"]:
        verdict = "fail"
    else:
        hard = True
        exact = bool(probe["exact"]) if probe is not None else True
res = {"kind": env["E_KIND"] or "runtime", "pinned_ref": env["E_REF"], "verdict": verdict, "hard": hard, "exact": exact,
       "cpu_flags": env["R_CPU"].split(), "detail": "; ".join(d for d in detail if d)}
with open(out, "w") as f:
    json.dump(res, f, indent=1)
    f.write("\n")
print("canary %s: %s (hard %s, exact %s): %s" % (os.path.basename(out)[:-5], verdict, hard, exact, res["detail"]))
sys.exit(0 if verdict == "pass" else 1)
PY
    local rc=$?
    [ -z "${E_INFORMATIONAL}" ] || rc=0
    exit "${rc}"
}

# (2) CPU flags
R_CPU=""
cpuflags=" $(grep -m1 '^flags' "${CANARY_CPUINFO:-/proc/cpuinfo}" | cut -d: -f2) "
for f in avx2 avx512f amx_tile; do
    [[ "${cpuflags}" == *" ${f} "* ]] && R_CPU+="${f} "
done
export R_CPU

# The engine, from canary/engines.toml and the tree.
if ! cfg=$(python3 "${HERE}/plan.py" shell "${ID}" 2> "${WORK}/plan.err"); then
    export E_KIND E_REF
    finish fail "plan: $(tr '\n' ' ' < "${WORK}/plan.err")"
fi
eval "${cfg}"
export E_KIND E_REF
if [ -z "${surface_only}" ] && [ "${E_KIND}" != runtime ]; then finish fail "${ID} is a ${E_KIND} engine: canary/run.sh --surface ${ID}"; fi
say "${E_KIND} ${E_REF}; cpu flags: ${R_CPU:-none}"

# (3) the model at its pinned revision
if [ -z "${surface_only}" ]; then
    if [ "$(cat "${E_MODEL_DIR}/.complete" 2>/dev/null)" = "${E_REVISION}" ]; then
        say "model ${E_MODEL}@${E_REVISION} from the cache"
    else
        hf=${HF_ENDPOINT:-https://huggingface.co}
        mkdir -p "${E_MODEL_DIR}"
        for f in "${E_FILES[@]}"; do
            say "download ${E_MODEL}@${E_REVISION} ${f}"
            curl -fsSL --retry 3 --retry-all-errors --retry-delay 5 -o "${E_MODEL_DIR}/${f}.partial" \
                "${hf}/${E_MODEL}/resolve/${E_REVISION}/${f}" 2>> "${LOG}" \
                || finish fail "model: ${hf}/${E_MODEL}/resolve/${E_REVISION}/${f} did not download"
            mv -f "${E_MODEL_DIR}/${f}.partial" "${E_MODEL_DIR}/${f}"
        done
        echo "${E_REVISION}" > "${E_MODEL_DIR}/.complete"
    fi
fi

# (1, 4) size, free disk, pull
free_bytes() {
    local root
    if [ -n "${CANARY_FREE_BYTES:-}" ]; then echo "${CANARY_FREE_BYTES}"; return; fi
    root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null) || root=/var/lib/docker
    df -P -B1 "${root}" 2>/dev/null | awk 'NR == 2 {print $4}'
}
free_disk() {
    say "freeing disk: the runner's preinstalled toolchains"
    sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc /usr/local/.ghcup /opt/hostedtoolcache/CodeQL \
        /usr/local/share/boost /usr/share/swift /usr/local/share/powershell /opt/microsoft 2>> "${LOG}"
    sudo docker image prune -af > /dev/null 2>&1
    return 0
}
if bytes=$(python3 "${HERE}/plan.py" size "${E_REF}" 2>> "${LOG}"); then
    need=$(python3 -c 'import sys; print(int(float(sys.argv[1]) * float(sys.argv[2])))' "${bytes}" "${E_SIZE_FACTOR}")
    free=$(free_bytes)
    if [ "${need}" -gt "${free:-0}" ] && [ "${CANARY_FREE_DISK:-0}" = 1 ]; then
        free_disk
        free=$(free_bytes)
    fi
    say "image ${bytes} bytes compressed, ~${need} on disk, ${free} free"
    if [ "${need}" -gt "${free:-0}" ]; then
        finish too-big "the image is ${bytes} bytes compressed, ~${need} bytes pulled (x${E_SIZE_FACTOR}), ${free} bytes free"
    fi
else
    say "the image's size is unreadable (see the log); pulling anyway"
fi
pulled=""
for attempt in 1 2 3; do
    if docker pull "${E_REF}" > "${WORK}/pull.out" 2>&1; then pulled=1; break; fi
    say "pull attempt ${attempt} failed: $(tail -1 "${WORK}/pull.out")"
    cat "${WORK}/pull.out" >> "${LOG}"
    [ "${attempt}" -eq 3 ] || sleep $(( ${CANARY_PULL_BACKOFF:-20} * attempt ))
done
[ -n "${pulled}" ] || finish fail "pull failed after 3 attempts: $(tail -1 "${WORK}/pull.out")"

# (5) the surface check
if [ -n "${E_SURFACE}" ]; then
    if ! python3 "${HERE}/plan.py" show "${ID}" > "${WORK}/engine.json" \
        || ! python3 "${HERE}/surface.py" check --engine "${WORK}/engine.json" --out "${WORK}/surface.json" >&2; then
        finish fail "surface: canary/surface.py did not run"
    fi
fi
[ -z "${surface_only}" ] || finish pass ""

# (6) serve and probe
vols=() envs=() caps=()
if [ -n "${E_WRITABLE}" ]; then
    # The engine writes next to the model (OVMS: graph.pbtxt): a copy, never the cache.
    mkdir -p "${WORK}/models${E_MOUNT#"${E_WRITABLE}"}"
    cp -r "${E_MODEL_DIR}/." "${WORK}/models${E_MOUNT#"${E_WRITABLE}"}/"
    rm -f "${WORK}/models${E_MOUNT#"${E_WRITABLE}"}/.complete"
    chmod -R a+rwX "${WORK}/models"
    vols=(-v "${WORK}/models:${E_WRITABLE}")
else
    vols=(-v "${E_MODEL_DIR}:${E_MOUNT}:ro")
fi
for e in ${E_ENV[@]+"${E_ENV[@]}"}; do envs+=(-e "${e}"); done
for c in ${E_CAP_ADD[@]+"${E_CAP_ADD[@]}"}; do caps+=(--cap-add "${c}"); done
[ -z "${E_SHM_SIZE}" ] || caps+=(--shm-size "${E_SHM_SIZE}")
if [ "${#E_PREPARE[@]}" -gt 0 ]; then
    penv=()
    for e in ${E_PREPARE_ENV[@]+"${E_PREPARE_ENV[@]}"}; do penv+=(-e "${e}"); done
    say "prepare: ${E_PREPARE[*]}"
    docker run --rm --name "${NAME}-prepare" "${vols[@]}" ${penv[@]+"${penv[@]}"} "${E_REF}" "${E_PREPARE[@]}" >> "${LOG}" 2>&1 \
        || finish fail "the prepare run (${E_PREPARE[*]}) failed: $(tail -3 "${LOG}" | tr '\n' ' ')"
fi
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
docker rm -f "${NAME}" > /dev/null 2>&1
say "serve: ${E_ARGS[*]}"
docker run -d --name "${NAME}" -p "127.0.0.1:${port}:${E_PORT}" "${vols[@]}" ${envs[@]+"${envs[@]}"} \
    ${caps[@]+"${caps[@]}"} "${E_REF}" "${E_ARGS[@]}" > /dev/null 2>> "${LOG}" \
    || finish fail "docker run failed: $(tail -3 "${LOG}" | tr '\n' ' ')"
python3 "${HERE}/probe.py" --base "http://127.0.0.1:${port}" --api "${E_API}" --name "${E_NAME}" \
    --golden "${HERE}/../${E_GOLDEN}" --timeout "${CANARY_READY_TIMEOUT:-${E_READY_TIMEOUT}}" --container "${NAME}" \
    --version-path "${E_VERSION_PATH}" --version-key "${E_VERSION_KEY}" --version-regex "${E_VERSION_REGEX}" \
    --expect-version "${E_EXPECT_VERSION}" --out "${WORK}/probe.json"
docker logs "${NAME}" >> "${LOG}" 2>&1
docker rm -f "${NAME}" > /dev/null 2>&1

# (7) an engine that cannot run on this ISA
ready=$(python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); print("%s %s" % (d["ready"], d["exit_code"]))' "${WORK}/probe.json")
if [ "${ready%% *}" = False ] && [ -n "${E_ISA_SIGNAL}" ] && [[ " ${R_CPU}" != *" avx512f "* ]] \
    && { [ "${ready#* }" = 132 ] || grep -Eq -- "${E_ISA_SIGNAL}" "${LOG}"; }; then
    finish isa-skip "this runner has no avx512f and the engine stopped on its ISA (exit ${ready#* }; /${E_ISA_SIGNAL}/ in its log)"
fi
finish pass ""
