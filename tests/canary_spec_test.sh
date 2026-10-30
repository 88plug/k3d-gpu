#!/usr/bin/env bash
# canary/engines.toml against the real tree, canary/flags.sh, canary/plan.py's planning
# (auto against fixture diffs, explicit lists, the schedule) and report, and the shape
# of .github/workflows/canary.yml. Offline: no image, no registry, no network.
#
# Usage: tests/canary_spec_test.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLAN=${ROOT}/canary/plan.py
T=$(mktemp -d "${TMPDIR:-/tmp}/canary_spec.XXXXXX")
trap 'rm -rf "$T"' EXIT
PASS=0 FAILS=0
ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; }
jget() { python3 -c 'import json, sys; d = json.load(sys.stdin)
for k in sys.argv[1:]: d = d[k]
print(d if isinstance(d, str) else json.dumps(d))' "$@"; }

WANT_IDS="dlstreamer eat-vllm-cpu gaudi-vllm llama-cpu llama-full llama-sycl llama-vulkan llm-scaler node npu-umd ovms ovms-cpu sglang-xeon vllm-cpu vllm-xpu"
ids=$(python3 "${PLAN}" ids | sort | tr '\n' ' ')
[ "${ids% }" = "${WANT_IDS}" ] && ok "engines.toml has every engine of the spec" || fail "engine ids are '${ids}'"

# --- the real tree --------------------------------------------------------------------
FLAGS=$("${ROOT}/canary/flags.sh") && ok "flags.sh runs on the tree" || fail "flags.sh failed"
for id in ${WANT_IDS}; do
    if ! ref=$(python3 "${PLAN}" ref "${id}" 2> "${T}/err"); then
        fail "${id}: locator does not match exactly one ref: $(cat "${T}/err")"
        continue
    fi
    [[ "${ref}" =~ ^[a-z0-9./_-]+(:[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}$ ]] && ok "${id}: ${ref%%@*}@sha256:..." \
        || fail "${id}: '${ref}' is not name[:tag]@sha256:<64 hex>"
    show=$(python3 "${PLAN}" show "${id}" 2> "${T}/err") || { fail "${id}: plan.py show: $(cat "${T}/err")"; continue; }
    kind=$(jget kind <<< "${show}")
    if [ "${kind}" = runtime ]; then
        rev=$(jget revision <<< "${show}")
        [[ "${rev}" =~ ^[0-9a-f]{40}$ ]] && ok "${id}: revision is 40-hex" || fail "${id}: revision '${rev}'"
        g=$(jget golden <<< "${show}")
        if python3 - "${ROOT}/${g}" <<'PY'
import json, re, sys
d = json.load(open(sys.argv[1]))
assert sorted(d) == ["exact", "prompt", "recorded_with", "regex"], sorted(d)
assert d["prompt"] == "The capital of France is", d["prompt"]
re.compile(d["regex"])
assert d["exact"] is None or isinstance(d["exact"], str)
assert d["exact"] is None or re.search(d["regex"], d["exact"]), "the golden text fails its own regex"
PY
        then ok "${id}: golden ${g} is well formed"; else fail "${id}: golden ${g}"; fi
    fi
    if [ "${kind}" != node ]; then
        n=$(jget flags <<< "${show}" | python3 -c 'import json, sys; print(len(json.load(sys.stdin)))')
        [ "${n}" -gt 0 ] && ok "${id}: flags.sh names ${n} flags/envs for it" || fail "${id}: no flags from flags.sh"
        fam=$(jget family <<< "${show}")
        for s in $(jget flags_source <<< "${show}" | python3 -c 'import json, sys; print(" ".join(json.load(sys.stdin)))'); do
            grep -qP "^${fam//./\\.}\t${s//./\\.}\t" <<< "${FLAGS}" \
                || fail "${id}: flags.sh prints no ${fam} names from flags_source ${s}"
        done
    fi
done

# tree <dir>: the files planning reads, copied from the real tree.
tree() {
    mkdir -p "$1"
    (cd "${ROOT}" && cp -r --parents Dockerfile scripts/k3d-gpu console/plan.py console/model_opt.py console/fit.py \
        console/moe_predict.py bench/h54/h54.py integrations/eat/vllm-values.yaml packaging/install.sh share canary "$1")
}

# Exactly one match: a duplicated pin line is refused.
mkdir -p "${T}/dup/scripts" "${T}/dup/canary"
cp "${ROOT}/canary/engines.toml" "${T}/dup/canary/"
grep -m1 '^LLAMA_CPU_IMAGE=' "${ROOT}/scripts/k3d-gpu" > "${T}/dup/scripts/k3d-gpu"
grep -m1 '^LLAMA_CPU_IMAGE=' "${ROOT}/scripts/k3d-gpu" >> "${T}/dup/scripts/k3d-gpu"
if python3 "${PLAN}" ref llama-cpu --root "${T}/dup" > /dev/null 2>&1; then fail "a locator matching twice resolved"
else ok "a locator matching twice is an error"; fi
# Both reference forms, whichever the tree holds today: the OVMS pin rewritten to
# name@sha256 and to name:tag@sha256. The pins themselves move with every bump, so no
# test here names their current values.
tree "${T}/form"
sed -i -E 's#^(AGENT_OVMS_IMAGE="openvino/model_server)(:[^@"]+)?@#\1@#' "${T}/form/scripts/k3d-gpu"
ref=$(python3 "${PLAN}" ref ovms-cpu --root "${T}/form")
tag=$(python3 "${PLAN}" show ovms-cpu --root "${T}/form" | jget tag)
[[ "${ref}" == openvino/model_server@sha256:* && -z "${tag}" ]] && ok "a locator reads the name@sha256 form, with no tag" \
    || fail "name@sha256 form resolved to '${ref}', tag '${tag}'"
sed -i -E 's#^(AGENT_OVMS_IMAGE="openvino/model_server)@#\1:9.9.9-gpu@#' "${T}/form/scripts/k3d-gpu"
ref=$(python3 "${PLAN}" ref ovms-cpu --root "${T}/form")
[[ "${ref}" == openvino/model_server:9.9.9-gpu@sha256:* ]] && ok "a locator reads the name:tag@sha256 form" \
    || fail "name:tag form resolved to '${ref}'"
tag=$(python3 "${PLAN}" show ovms-cpu --root "${T}/form" | jget tag)
[ "${tag}" = 9.9.9-gpu ] && ok "the tag is split from a name:tag@sha256 ref" || fail "tag is '${tag}'"

# Every derived constant resolves to exactly one value.
for id in vllm-xpu llm-scaler gaudi-vllm npu-umd; do
    vals=$(python3 "${PLAN}" show "${id}" | jget derived | python3 -c 'import json, sys; print(" ".join(d["value"] for d in json.load(sys.stdin)))')
    [ -n "${vals}" ] && ok "${id}: derived constants ${vals}" || fail "${id}: no derived constants"
done
v=$(python3 "${PLAN}" show vllm-xpu | jget derived | python3 -c 'import json, sys; print(" ".join(sorted({d["value"] for d in json.load(sys.stdin)})))')
[[ "${v}" =~ ^[0-9.]+$ ]] && ok "vllm-xpu: plan.py, model_opt.py and the launcher agree (${v})" || fail "vllm-xpu derived '${v}'"

# --- flags.sh -------------------------------------------------------------------------
has() { grep -qP "^$1\t[^\t]+\t$2\t$3$" <<< "${FLAGS}"; }
for want in "vllm flag --served-model-name" "vllm flag --no-enable-prefix-caching" "vllm env VLLM_WORKER_MULTIPROC_METHOD" \
            "vllm env VLLM_CPU_KVCACHE_SPACE" "vllm flag --speculative-config" "ovms flag --target_device" \
            "ovms flag --draft_model_path" "llama\\.cpp flag --load-mode" "llama\\.cpp flag -ngl" "llama\\.cpp flag -md" \
            "llama-convert flag --outtype" "llama-convert flag --outfile" "dlstreamer element gvadetect" \
            "dlstreamer property gvadetect\\.pre-process-backend" "sglang flag --attention-backend"; do
    read -r f k n <<< "${want}"
    has "${f}" "${k}" "${n}" && ok "flags.sh: ${f//\\/} ${k} ${n//\\/}" || fail "flags.sh lacks ${want}"
done
grep -qP '^sglang\t[^\t]+\tflag\t-m$' <<< "${FLAGS}" && fail "flags.sh counts python's -m as an sglang flag" \
    || ok "flags.sh: sglang flags start after the module name"
# A flag literal with no engine test fails flags.sh, so a new emitter cannot hide.
mkdir -p "${T}/emit/scripts" "${T}/emit/console" "${T}/emit/bench/h54"
cp "${ROOT}/scripts/k3d-gpu" "${T}/emit/scripts/"
cp "${ROOT}/console/fit.py" "${T}/emit/console/"
cp "${ROOT}/bench/h54/h54.py" "${T}/emit/bench/h54/"
{ cat "${ROOT}/console/model_opt.py"; printf '\n\ndef new_emitter():\n    return ["--brand-new-flag"]\n'; } > "${T}/emit/console/model_opt.py"
if "${ROOT}/canary/flags.sh" --root "${T}/emit" > /dev/null 2> "${T}/err"; then fail "flags.sh accepted an unattributed flag literal"
elif grep -q 'new_emitter.*--brand-new-flag' "${T}/err"; then ok "flags.sh names an unattributed flag literal and fails"
else fail "flags.sh failed without naming the literal: $(cat "${T}/err")"; fi

# --- planning -------------------------------------------------------------------------
tree "${T}/base"
# plan_auto <label> <expected ids> <edit command run in the head tree...>
plan_auto() {
    local label=$1 want=$2 got
    shift 2
    rm -rf "${T}/head"
    cp -r "${T}/base" "${T}/head"
    (cd "${T}/head" && "$@") || { fail "${label}: edit failed"; return; }
    # An edit whose pattern no longer matches the tree would read as "selected nothing".
    if [ "$1" != true ] && diff -rq "${T}/base" "${T}/head" > /dev/null; then
        fail "${label}: the edit changed nothing in the tree"
        return
    fi
    got=$(python3 "${PLAN}" matrix --mode auto --base-root "${T}/base" --root "${T}/head" | jget selected \
        | python3 -c 'import json, sys; print(" ".join(sorted(e["id"] for e in json.load(sys.stdin))))')
    [ "${got}" = "${want}" ] && ok "auto, ${label}: ${want:-nothing}" || fail "auto, ${label}: selected '${got}', want '${want}'"
}
digest() { printf '%064d' "$1"; }
# edit <file> <old> <new>: replace the one occurrence of <old>.
edit() {
    python3 - "$@" <<'PY'
import sys
p, old, new = sys.argv[1:]
s = open(p).read()
assert s.count(old) == 1, "%s: %r occurs %d times" % (p, old, s.count(old))
open(p, "w").write(s.replace(old, new))
PY
}
plan_auto "no change" "" true
plan_auto "SYCL digest" "llama-sycl" \
    sed -i -E "s/(LLAMA_SYCL_IMAGE=.*@sha256:)[0-9a-f]{64}/\1$(digest 1)/" scripts/k3d-gpu
plan_auto "OVMS digest (two engines locate it)" "ovms ovms-cpu" \
    sed -i -E "s/(AGENT_OVMS_IMAGE=.*@sha256:)[0-9a-f]{64}/\1$(digest 2)/" scripts/k3d-gpu
plan_auto "NPU user space pin" "npu-umd" \
    sed -i -E "s/(NPU_UMD_IMAGE=.*@sha256:)[0-9a-f]{64}/\1$(digest 3)/" scripts/k3d-gpu
plan_auto "EAT's vLLM tag" "eat-vllm-cpu" \
    sed -i -E 's/^(  tag: ")[^"@]+@/\1v9.9.9@/' integrations/eat/vllm-values.yaml
plan_auto "sglang in moe_predict" "sglang-xeon" \
    sed -i -E "s/(lmsysorg\\/sglang(:[^@\"]+)?@sha256:)[0-9a-f]{64}/\1$(digest 4)/" console/moe_predict.py
plan_auto "k3s pin" "node" sed -i 's/^ARG K3S_TAG="v[^"]*"/ARG K3S_TAG="v9.9.9-k3s1"/' Dockerfile
plan_auto "a baked manifest" "node" sed -i '1i # touched' share/intel-gpu-plugin.yml
plan_auto "a derived constant" "vllm-xpu" sed -i -E 's/"GPU": "[0-9.]+"/"GPU": "9.9.9"/' console/plan.py
plan_auto "a vllm_manifest flag" "gaudi-vllm llm-scaler vllm-cpu vllm-xpu" \
    edit scripts/k3d-gpu '"${item}--port=8000"' '"${item}--port=8000"'$'\n''    args+="${item}--enable-chunked-prefill"'
plan_auto "an OVMS manifest flag" "npu-umd ovms ovms-cpu" \
    edit scripts/k3d-gpu $'            - --rest_port=8000\n            - --model_path=/models/${AGENT_MODEL}\n' \
        $'            - --rest_port=8000\n            - --grpc_port=9000\n            - --model_path=/models/${AGENT_MODEL}\n'
plan_auto "an unrelated launcher edit" "" edit scripts/k3d-gpu 'K3D_GPU_AGENT_STORAGE:-20Gi' 'K3D_GPU_AGENT_STORAGE:-30Gi'
plan_auto "a golden" "llama-cpu" sed -i 's/"recorded_with": {/"recorded_with": {"note": "x", /' canary/goldens/llama-cpu.json
plan_auto "one engine's table" "llama-cpu" sed -i 's/"-c", "2048", "-np"/"-c", "4096", "-np"/' canary/engines.toml
all_ids=$(echo "${WANT_IDS}")
plan_auto "the harness itself" "${all_ids}" sed -i '1a # touched' canary/run.sh
plan_auto "the defaults table" "${all_ids}" sed -i 's/^ready_timeout = 600/ready_timeout = 900/' canary/engines.toml
plan_auto "the canary's README alone" "" sed -i '1a touched' canary/README.md

# The same through git: --base names a commit, and the tree is read from it.
mkdir -p "${T}/repo"
cp -r "${T}/base/." "${T}/repo/"
(
    set -e
    cd "${T}/repo"
    export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
    export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
    git init -q -b main .
    git add -A
    git commit -qm base
    sed -i -E "s/(LLAMA_VULKAN_IMAGE=.*@sha256:)[0-9a-f]{64}/\1$(digest 5)/" scripts/k3d-gpu
    git commit -qam bump
) || fail "git fixture"
got=$(python3 "${PLAN}" matrix --mode auto --base HEAD~1 --root "${T}/repo" | jget selected \
    | python3 -c 'import json, sys; print(" ".join(e["id"] for e in json.load(sys.stdin)))')
[ "${got}" = llama-vulkan ] && ok "auto against a git base commit: llama-vulkan" || fail "auto --base HEAD~1 selected '${got}'"

got=$(python3 "${PLAN}" matrix --engines llama-cpu,node | jget selected | python3 -c 'import json, sys; print(" ".join(e["id"] for e in json.load(sys.stdin)))')
[ "${got}" = "llama-cpu node" ] && ok "an explicit list selects exactly it" || fail "--engines llama-cpu,node selected '${got}'"
python3 "${PLAN}" matrix --engines llama-cpu,nope > /dev/null 2>&1 && fail "an unknown engine id was accepted" \
    || ok "an unknown engine id is refused"
all=$(python3 "${PLAN}" matrix --mode all)
n=$(jget selected <<< "${all}" | python3 -c 'import json, sys; print(len(json.load(sys.stdin)))')
[ "${n}" -eq 15 ] && ok "the schedule selects all 15 engines" || fail "--mode all selected ${n}"
python3 - <<'PY' "${all}" "$(python3 "${PLAN}" show llama-cpu)" && ok "matrix: runtime rows carry the model cache key, surface rows the id, node a flag" || fail "matrix shape"
import json, sys
m = json.loads(sys.argv[1])
eng = json.loads(sys.argv[2])
rt = {r["id"]: r for r in m["runtime"]}
assert sorted(rt) == ["eat-vllm-cpu", "llama-cpu", "ovms-cpu", "vllm-cpu"], sorted(rt)
r = rt["llama-cpu"]
# The key holds the model and its revision, so a revision bump misses the old cache.
slug = "%s@%s" % (eng["model"].replace("/", "__"), eng["revision"])
assert r["cache_key"] == "canary-model-" + slug, (r, slug)
assert r["cache_path"] == "~/.cache/k3d-gpu-canary/" + slug, (r, slug)
assert len(m["surface"]) == 10 and all(set(s) == {"id"} for s in m["surface"]), m["surface"]
assert m["node"] is True
assert {e["id"]: e["informational"] for e in m["selected"]}["eat-vllm-cpu"] is True
PY
python3 "${PLAN}" matrix --mode all --github-output "${T}/gh" > /dev/null
grep -qx 'node=true' "${T}/gh" && grep -q '^runtime=\[' "${T}/gh" && grep -q '^surface=\[' "${T}/gh" && grep -q '^selected=\[' "${T}/gh" \
    && ok "matrix --github-output writes runtime, surface, node and selected" || fail "github output: $(cat "${T}/gh")"

# --- report ---------------------------------------------------------------------------
part() { # part <id> <kind> <verdict> <hard> <exact>
    printf '{"kind": "%s", "pinned_ref": "x/%s@sha256:%s", "verdict": "%s", "hard": %s, "exact": %s, "cpu_flags": ["avx2"], "detail": "d"}' \
        "$2" "$1" "$(digest 9)" "$3" "$4" "$5" > "${T}/parts/$1.json"
}
report() { # report <selected ids...>: runs plan.py report, sets RRC and leaves canary-result.json
    local sel
    sel=$(python3 -c 'import json, sys; print(json.dumps([{"id": i, "kind": "runtime", "informational": i.startswith("eat-")} for i in sys.argv[1:]]))' "$@")
    python3 "${PLAN}" report --selected "${sel}" --parts "${T}/parts" --sha abc --ref claude/x --out "${T}/result.json" \
        --summary "${T}/summary.md" --github-output "${T}/rgh" > "${T}/report.log" 2>&1
    RRC=$?
}
rm -rf "${T}/parts"; mkdir -p "${T}/parts"
part llama-cpu runtime pass true true
part vllm-xpu surface pass true true
report llama-cpu vllm-xpu
[ "${RRC}" -eq 0 ] && ok "report: every engine green exits 0" || fail "report exited ${RRC}: $(cat "${T}/report.log")"
python3 - "${T}/result.json" <<'PY' && ok "report: canary-result.json has the interface schema" || fail "canary-result.json schema"
import json, sys
d = json.load(open(sys.argv[1]))
assert sorted(d) == ["engines", "ref", "sha"] and d["sha"] == "abc" and d["ref"] == "claude/x", d
for k, e in d["engines"].items():
    assert sorted(e) == ["cpu_flags", "detail", "exact", "hard", "kind", "pinned_ref", "verdict"], e
    assert e["kind"] in ("runtime", "surface", "node")
    assert e["verdict"] in ("pass", "fail", "isa-skip", "too-big", "unparseable")
    assert isinstance(e["hard"], bool) and isinstance(e["exact"], bool) and isinstance(e["cpu_flags"], list)
PY
grep -q 'llama-cpu' "${T}/summary.md" && ok "report: the step summary lists the engines" || fail "no summary"
part llama-cpu runtime pass true false
report llama-cpu
[ "${RRC}" -eq 0 ] && ok "report: exact false alone is still green" || fail "report exited ${RRC} on an exact mismatch"
part llama-cpu runtime fail false false
report llama-cpu vllm-xpu
[ "${RRC}" -ne 0 ] && ok "report: one failed engine exits non-zero" || fail "report exited 0 with a failed engine"
part llama-cpu runtime pass true true
part eat-vllm-cpu runtime fail false false
report llama-cpu eat-vllm-cpu
[ "${RRC}" -eq 0 ] && ok "report: an informational failure does not fail it" || fail "report exited ${RRC} on an informational failure"
report llama-cpu ovms-cpu
[ "${RRC}" -ne 0 ] && ok "report: a selected engine with no result fails" || fail "report exited 0 with a missing result"
[ "$(jget engines ovms-cpu verdict < "${T}/result.json")" = fail ] && ok "report: the missing engine is recorded as fail" \
    || fail "missing engine verdict $(jget engines ovms-cpu verdict < "${T}/result.json")"
part vllm-cpu runtime isa-skip false false
report vllm-cpu llama-cpu
[ "${RRC}" -ne 0 ] && ok "report: isa-skip is not green" || fail "report exited 0 on isa-skip"
grep -qx 'isa_skip=vllm-cpu' "${T}/rgh" && ok "report: isa-skip engines go to the re-dispatch output" || fail "rgh: $(cat "${T}/rgh")"
part vllm-cpu runtime pass true true
report vllm-cpu
grep -qx 'isa_skip=' "${T}/rgh" && ok "report: no isa-skip, no re-dispatch" || fail "rgh: $(cat "${T}/rgh")"
report
[ "${RRC}" -eq 0 ] && [ "$(jget engines < "${T}/result.json")" = "{}" ] && ok "report: nothing selected is green and empty" \
    || fail "empty report rc=${RRC}"

# --- the workflow -----------------------------------------------------------------------
python3 - "${ROOT}/.github/workflows/canary.yml" <<'PY' && ok "canary.yml: triggers, permissions, timeouts, pins, artifact" || fail "canary.yml shape"
import re, sys, yaml
src = open(sys.argv[1]).read()
w = yaml.safe_load(src)
on = w.get(True) or w.get("on")
assert w["name"] == "Canary", w["name"]
assert [c["cron"] for c in on["schedule"]] == ["29 3 * * *"], on["schedule"]
inp = on["workflow_dispatch"]["inputs"]
assert str(inp["engines"]["default"]) == "auto" and str(inp["attempt"]["default"]) == "1", inp
paths = set(on["pull_request"]["paths"])
need = {"Dockerfile", "scripts/k3d-gpu", "share/**", "console/moe_predict.py", "console/plan.py",
        "console/model_opt.py", "canary/**", "packaging/install.sh"}
assert need <= paths, need - paths
assert w["permissions"] == {"contents": "read"}, w["permissions"]
assert w["concurrency"]["group"] == "canary-${{ github.ref }}", w["concurrency"]
assert "pull_request" in str(w["concurrency"]["cancel-in-progress"]), w["concurrency"]
jobs = w["jobs"]
for name, j in jobs.items():
    assert "timeout-minutes" in j, name + " has no timeout-minutes"
    assert "permissions" in j, name + " has no permissions"
    assert j["runs-on"] == "ubuntu-latest", name + " must run on a GitHub-hosted runner"
assert jobs["runtime"]["timeout-minutes"] == 45 and jobs["runtime"]["strategy"]["fail-fast"] is False
assert jobs["surface"]["timeout-minutes"] == 30 and jobs["node"]["timeout-minutes"] == 40
assert "always()" in jobs["report"]["if"]
writes = {n for n, j in jobs.items() if any(v == "write" for v in (j["permissions"] or {}).values())}
assert writes <= {"redispatch"}, writes
assert "secrets." not in src, "no secrets"
for u in re.findall(r"uses:\s*(\S+)", src):
    assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", u), "not pinned by SHA: " + u
assert "name: canary-result" in src
assert not re.search(r"\b(v?\d+\.\d+\.\d+)\b", re.sub(r"#.*", "", src)), "a version literal in the workflow"
PY

# A pull request runs the canary when it changes any file the plan reads (canary/plan.py's
# BASE_PATHS, each engine's locator, derived and flags_source files, the node's paths), or
# a change that selects an engine would merge without its canary.
python3 - "${ROOT}" <<'PY' && ok "canary.yml: pull_request paths cover every file the plan reads" || fail "canary.yml: pull_request paths miss a file the plan reads"
import fnmatch, importlib.util, os, sys, yaml
root = sys.argv[1]
spec = importlib.util.spec_from_file_location("plan", os.path.join(root, "canary/plan.py"))
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)
_, engines = plan.load(root)
need = set(plan.BASE_PATHS)
for e in engines.values():
    need.add(e["locator"]["file"])
    need.update(d["file"] for d in e.get("derived", []))
    need.update(p.rstrip("/") for p in e.get("paths", []))
    need.update(s.split(":", 1)[0] for s in e.get("flags_source", []))
w = yaml.safe_load(open(os.path.join(root, plan.WORKFLOW)))
pats = (w.get(True) or w.get("on"))["pull_request"]["paths"]
# GitHub's ** matches across directories; a directory is covered when a file in it is.
missing = sorted(p for p in need
                 if not any(fnmatch.fnmatch(p + "/f" if os.path.isdir(os.path.join(root, p)) else p, pat) for pat in pats))
assert not missing, "not in on.pull_request.paths: %s" % missing
PY

# node.sh creates the cluster with the arguments the launcher's create passes on a host
# without NVIDIA; each must still be one the launcher passes, word for word.
node_args=$(sed -n 's/^NODE_ARGS=(\(.*\))$/\1/p' "${ROOT}/canary/node.sh")
[ -n "${node_args}" ] && ok "node.sh: NODE_ARGS=(${node_args})" || fail "node.sh has no NODE_ARGS=(...) line"
eval "set -- ${node_args}"
launcher=$(tr -d '"' < "${ROOT}/scripts/k3d-gpu")  # its words, as the shell passes them
for a in "$@"; do
    grep -qF -- "${a}" <<< "${launcher}" && ok "node.sh: the launcher passes ${a}" \
        || fail "node.sh passes ${a}, which scripts/k3d-gpu's cluster create no longer does"
done
[[ " ${node_args} " == *"nvidia.com/gpu.present=false"* ]] && ok "node.sh: the node is labelled as having no NVIDIA GPU" \
    || fail "node.sh does not label the node nvidia.com/gpu.present=false"

# The workflow runs the canary's scripts directly, so each must be executable.
scripts=$(grep -oE '(^|[[:space:]])canary/[A-Za-z0-9_.-]+\.(sh|py)' "${ROOT}/.github/workflows/canary.yml" | tr -d ' ' | sort -u)
[ -n "${scripts}" ] || fail "canary.yml runs no canary/ script"
for s in ${scripts}; do
    [ -x "${ROOT}/${s}" ] && ok "canary.yml runs ${s}: executable" || fail "canary.yml runs ${s}, which is not executable"
done

echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
