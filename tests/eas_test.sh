#!/usr/bin/env bash
# Checks for integrations/eas (Intel's agent toolkit through the EAS v1.2.0-rc.1
# installer) with no network, docker or cluster:
#   1. every script there is committed executable
#   2. eas-up.sh preflight + render, against stub kubectl, docker and k3d-gpu and the
#      fixture checkout in tests/fixtures/eas-rc1:
#      - require_amx true on xeon-gnr's node labels, false on raptorlake-nvidia's, and
#        EAS_REQUIRE_AMX overrides either way
#      - global_config.yaml gets exactly the listed overrides and keeps the
#        auth_provider litellm that init seeded; Istio values are the checkout's
#        charts/istio/values.yaml plus global.platform k3d or k3s from containerd's
#        bin_dirs; models.yaml and config.agent-toolkit.yaml match their goldens;
#        a second render changes nothing
#      - refused, with the cause named and nothing written: unknown bin_dirs, a
#        foreign agent-sandbox controller (with the `k3d-gpu sandbox down` hint), a
#        model that does not fit (with the numbers), an upstream key that is gone
#   3. pins: an EAS checkout at another commit, a manifest that lists other repos, and
#      ext repos at other commits are refused; the manifest's revs become the SHAs
#   4. the rebased envoy patch applies to the rc's EnvoyProxy task and leaves the
#      control-plane affinity Exists and nodeSelector null, once
#   5. a full run stops in the preflight before cloning, building or running anything
#   6. eas-check.sh against stub kubectl and curl: the all-pass case, each failure with
#      its cause, and the LiteLLM master key never in an argument or the output
# Needs bash, git, jq, python3 (with PyYAML) and mikefarah yq v4.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EAS="${ROOT}/integrations/eas"
FIX="${ROOT}/tests/fixtures/eas-rc1"
PASS=0
FAILS=0
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT
unset K3D_GPU EAS_REQUIRE_AMX EAS_ISTIO_PLATFORM EAS_CLUSTER_CIDR EAS_MODEL EAS_MODEL_ID EAS_MODEL_CPU EAS_ENV EAS_GATEWAY_ADDR

ok()   { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { FAILS=$((FAILS + 1)); echo "FAIL $1"; [ -z "${2:-}" ] || sed 's/^/     | /' <<< "$2"; }
# check <name> <cmd...>
check() { local name=$1; shift; if "$@"; then ok "${name}"; else fail "${name}"; fi; }

yq --version 2>/dev/null | grep -q mikefarah || { echo "eas_test: needs mikefarah yq v4 on PATH" >&2; exit 2; }
EAS_COMMIT=5c27fb39a95eecec41aa4e29a1188984e9c726f3
EI_COMMIT=170eb3abb1956de0787727f21e356aef2b13ff24
EAT_COMMIT=036b66234801e9bd950afd90830821859780d668
ROUTER="$(yq '.agent_sandbox_router.image.repository' "${EAS}/images.yaml"):$(yq '.agent_sandbox_router.image.tag' "${EAS}/images.yaml")"

# ---- 1. File modes in the index, which is what a fresh checkout gets ----------
n=0
while read -r mode _ _ path; do
    n=$((n + 1))
    if [ "${mode}" = 100755 ]; then ok "executable in git: ${path}"; else fail "not executable in git (${mode}): ${path}"; fi
done < <(git -C "${ROOT}" ls-files -s -- 'integrations/eas/*.sh' 'integrations/eas/*.py')
if [ "${n}" -ge 3 ]; then ok "integrations/eas has its scripts in git (${n})"; else fail "integrations/eas scripts not in git (${n})"; fi

# ---- stubs for the preflight ---------------------------------------------------
PBIN="${WORK}/pbin"
mkdir -p "${PBIN}"
cat > "${PBIN}/kubectl" <<'EOF'
#!/usr/bin/env bash
echo "kubectl $*" >> "${STUB}/calls"
case "$*" in
    "get nodes -o json") cat "${STUB}/nodes.json" ;;
    *"get deployment agent-sandbox-controller --ignore-not-found"*) cat "${STUB}/sandbox-image" 2>/dev/null || true ;;
    "get pods -A -o json") cat "${STUB}/pods.json" ;;
    *) echo "kubectl stub: unexpected: $*" >&2; exit 1 ;;
esac
EOF
cat > "${PBIN}/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "${STUB}/calls"
case "$1" in
    exec)
        [ ! -e "${STUB}/no-exec" ] || { echo "Error response from daemon: No such container: $2" >&2; exit 1; }
        printf "[plugins.'io.containerd.cri.v1.runtime'.cni]\n  bin_dirs = %s\n  conf_dir = \"/var/lib/rancher/k3s/agent/etc/cni/net.d\"\n" "$(cat "${STUB}/bin_dirs")" ;;
    *) echo "docker stub: unexpected: $*" >&2; exit 1 ;;
esac
EOF
cat > "${PBIN}/k3d-gpu" <<'EOF'
#!/usr/bin/env bash
echo "k3d-gpu $*" >> "${STUB}/calls"
[ "$1" = fit ] || { echo "k3d-gpu stub: unexpected: $*" >&2; exit 1; }
cat "${STUB}/fit.json"
EOF
chmod +x "${PBIN}"/*

# stub_dir <name> <fixture> <alloc cpu or ""> <alloc memory>: a fresh stub state with one
# node carrying the --k3s-node-label labels k3d-gpu emits for that fixture.
stub_dir() {
    local s="${WORK}/$1" cpu=$3
    mkdir -p "${s}"
    : > "${s}/calls"
    [ -n "${cpu}" ] || cpu=$(awk '$1 == "cpu" {print $2}' "${ROOT}/tests/fixtures/$2/allocatable")
    python3 - "${ROOT}/tests/fixtures/$2/up.golden" "${cpu}" "$4" > "${s}/nodes.json" <<'PY'
import json, re, sys
golden, cpu, mem = sys.argv[1:]
labels = {"kubernetes.io/hostname": "k3d-gpu-server-0", "node-role.kubernetes.io/control-plane": "true"}
for m in re.finditer(r"--k3s-node-label=([^=@\s]+)=([^@\s]*)@", open(golden).read()):
    labels[m.group(1)] = m.group(2)
print(json.dumps({"items": [{"metadata": {"name": "k3d-gpu-server-0", "labels": labels},
                             "spec": {"podCIDR": "10.42.0.0/24"},
                             "status": {"allocatable": {"cpu": cpu, "memory": mem, "pods": "110"}}}]}))
PY
    # 2.2 CPUs and 3.5 GiB already requested on the node (an init container larger than its
    # pod's containers counts instead of them); a Succeeded pod does not count.
    cat > "${s}/pods.json" <<'JSON'
{"items": [
 {"metadata": {"name": "coredns-1", "namespace": "kube-system"}, "status": {"phase": "Running"},
  "spec": {"nodeName": "k3d-gpu-server-0", "containers": [{"resources": {"requests": {"cpu": "200m", "memory": "1Gi"}}}]}},
 {"metadata": {"name": "nfd-worker-1", "namespace": "node-feature-discovery"}, "status": {"phase": "Running"},
  "spec": {"nodeName": "k3d-gpu-server-0", "initContainers": [{"resources": {"requests": {"cpu": "2", "memory": "2560Mi"}}}],
           "containers": [{"resources": {"requests": {"cpu": "500m", "memory": "1Gi"}}}, {"resources": {"requests": {"cpu": "500m", "memory": "1024Mi"}}}]}},
 {"metadata": {"name": "helm-install-1", "namespace": "kube-system"}, "status": {"phase": "Succeeded"},
  "spec": {"nodeName": "k3d-gpu-server-0", "containers": [{"resources": {"requests": {"cpu": "8", "memory": "64Gi"}}}]}}
]}
JSON
    echo '["/usr/bin"]' > "${s}/bin_dirs"
    cat > "${s}/fit.json" <<'JSON'
{"schema": "k3d-gpu.fit/v1", "fits": true, "summary": "fit: Qwen/Qwen3-1.7B on CPU (AVX2) via vllm (stub)",
 "flags": ["--max-model-len=40960", "--max-num-seqs=1", "VLLM_CPU_KVCACHE_SPACE=6"],
 "k8s": {"memory_request": "11Gi", "memory_limit": "11Gi"}}
JSON
    echo "${s}"
}

# preflight <stub dir> [VAR=value...]: eas-up.sh preflight; output in <stub>/out, rc in <stub>/rc.
preflight() {
    local s=$1
    shift
    : > "${s}/kubeconfig"
    env "$@" STUB="${s}" PATH="${PBIN}:${PATH}" EAS_WORK="${s}/work" EAS_KUBECONFIG="${s}/kubeconfig" \
        bash "${EAS}/eas-up.sh" preflight > "${s}/out" 2>&1
    echo $? > "${s}/rc"
}

# checkout <name>: the fixture checkout with env/local/ seeded the way
# `es_auto_installer.sh init agent-toolkit` seeds it.
checkout() {
    local co="${WORK}/$1"
    cp -a "${FIX}/eas" "${co}"
    mkdir -p "${co}/env/local"
    sed 's/^auth_provider: "keycloak"/auth_provider: "litellm" /' "${co}/configs/defaults/global_config.yaml" \
        > "${co}/env/local/global_config.yaml"
    cp "${co}/ext/enterprise.agent-toolkit/config.yaml" "${co}/env/local/config.agent-toolkit.yaml"
    cp "${co}/ext/enterprise.ai-inference/model_manager/models.yaml" "${co}/env/local/models.yaml"
    echo "${co}"
}

# render <checkout> <facts>: output in <checkout>.out, rc in <checkout>.rc.
render() {
    bash "${EAS}/eas-up.sh" render "$1" "$2" > "$1.out" 2>&1
    echo $? > "$1.rc"
}

json_of() { yq -o=json "$1" | jq -S .; }
# golden <rendered> <golden file> <checkout>: same document as the golden.
golden() {
    diff <(json_of "$1") <(sed -e "s#@CHECKOUT@#$3#" -e "s#@ROUTER@#${ROUTER}#" "${FIX}/golden/$2" | jq -S .) > "${WORK}/diff" 2>&1
}
fact() { jq -r "$2" "$1/work/facts.json"; }
has()  { grep -qF -- "$2" "$1"; }

# ---- 2. preflight and render ---------------------------------------------------
# AMX host: xeon-gnr's labels, the k3d-gpu node image's bin_dirs.
s=$(stub_dir amx xeon-gnr "" 512Gi)
preflight "${s}"
check "preflight on xeon-gnr labels: rc 0" [ "$(cat "${s}/rc")" = 0 ]
check "AMX labels: require_amx true" [ "$(fact "${s}" .require_amx)" = true ]
check "bin_dirs [\"/usr/bin\"]: Istio platform k3d" [ "$(fact "${s}" .istio_platform)" = k3d ]
check "model fit from k3d-gpu fit: KV 6, memory 11Gi, cpu 8" \
    [ "$(fact "${s}" '[.model.kv_cache_gib, .model.memory, .model.cpu] | join(" ")')" = "6 11Gi 8" ]
check "the fit counts an init container larger than its pod and skips Succeeded pods (2.2 CPUs)" \
    [ "$(fact "${s}" .node.requested_cpu)" = 2.2 ]
check "k3d-gpu fit asked for the model with vllm on the CPU" \
    has "${s}/calls" "k3d-gpu fit --model Qwen/Qwen3-1.7B --engine vllm --device cpu --json"
co=$(checkout co-amx)
render "${co}" "${s}/work/facts.json"
if [ "$(cat "${co}.rc")" = 0 ]; then ok "render on the AMX facts: rc 0"; else fail "render on the AMX facts" "$(cat "${co}.out")"; fi
if golden "${co}/env/local/global_config.yaml" global_config.json "${co}"; then
    ok "global_config.yaml: exactly the overrides, auth_provider litellm kept"
else fail "global_config.yaml golden" "$(cat "${WORK}/diff")"; fi
check "global_config.yaml keeps Intel's comments" grep -q '^# --- Kubernetes ---' "${co}/env/local/global_config.yaml"
if golden "${co}/env/local/istio-values.k3d-gpu.yaml" istio-values-k3d.json "${co}"; then
    ok "Istio values: charts/istio/values.yaml plus global.platform k3d"
else fail "Istio values k3d golden" "$(cat "${WORK}/diff")"; fi
if golden "${co}/env/local/models.yaml" models-amx.json "${co}"; then ok "models.yaml (AMX) golden: require_amx true"
else fail "models.yaml (AMX) golden" "$(cat "${WORK}/diff")"; fi
if golden "${co}/env/local/config.agent-toolkit.yaml" config.agent-toolkit.json "${co}"; then
    ok "config.agent-toolkit.yaml: llm_services_autodeploy_models [qwen3-1-7b]"
else fail "config.agent-toolkit.yaml golden" "$(cat "${WORK}/diff")"; fi
check "render prints the cpu change against the catalog" has "${co}.out" "qwen3-1-7b.cpu: auto -> 8"
check "render prints the memory change against the catalog" has "${co}.out" "qwen3-1-7b.memory: 64Gi -> 11Gi"
check "render prints the KV change against the catalog" has "${co}.out" "qwen3-1-7b.env.VLLM_CPU_KVCACHE_SPACE: 40 -> 6"
check "render names the best-effort deviation" has "${co}.out" "kubernetes_cpu_policy best-effort: EAS calls nri-balloons the only supported"
for f in global_config.yaml models.yaml config.agent-toolkit.yaml istio-values.k3d-gpu.yaml; do cp "${co}/env/local/${f}" "${WORK}/${f}.1"; done
render "${co}" "${s}/work/facts.json"
same=1
for f in global_config.yaml models.yaml config.agent-toolkit.yaml istio-values.k3d-gpu.yaml; do
    cmp -s "${co}/env/local/${f}" "${WORK}/${f}.1" || same=0
done
check "a second render changes no file" [ "${same}" = 1 ]
check "a second render reports the keys unchanged" has "${co}.out" "metallb_enabled: false (unchanged)"

# No AMX: raptorlake-nvidia's labels, stock k3s bin_dirs.
s=$(stub_dir noamx raptorlake-nvidia "" 125Gi)
echo '["/var/lib/rancher/k3s/data/cni"]' > "${s}/bin_dirs"
preflight "${s}"
check "no AMX labels: require_amx false" [ "$(fact "${s}" .require_amx)" = false ]
check "bin_dirs [\"/var/lib/rancher/k3s/data/cni\"]: Istio platform k3s" [ "$(fact "${s}" .istio_platform)" = k3s ]
co=$(checkout co-noamx)
render "${co}" "${s}/work/facts.json"
if golden "${co}/env/local/models.yaml" models-noamx.json "${co}"; then ok "models.yaml (no AMX) golden: require_amx false"
else fail "models.yaml (no AMX) golden" "$(cat "${WORK}/diff") $(cat "${co}.out")"; fi
if golden "${co}/env/local/istio-values.k3d-gpu.yaml" istio-values-k3s.json "${co}"; then
    ok "Istio values: charts/istio/values.yaml plus global.platform k3s"
else fail "Istio values k3s golden" "$(cat "${WORK}/diff")"; fi

# EAS_REQUIRE_AMX overrides the detection both ways.
s=$(stub_dir amxon raptorlake-nvidia "" 125Gi)
preflight "${s}" EAS_REQUIRE_AMX=true
check "EAS_REQUIRE_AMX=true on a host without AMX: require_amx true" [ "$(fact "${s}" .require_amx)" = true ]
check "EAS_REQUIRE_AMX=true without AMX warns that model pods will stay Pending" has "${s}/out" "will stay Pending"
s=$(stub_dir amxoff xeon-gnr "" 512Gi)
preflight "${s}" EAS_REQUIRE_AMX=false
check "EAS_REQUIRE_AMX=false on an AMX host: require_amx false" [ "$(fact "${s}" .require_amx)" = false ]
s=$(stub_dir amxbad xeon-gnr "" 512Gi)
preflight "${s}" EAS_REQUIRE_AMX=maybe
check "EAS_REQUIRE_AMX=maybe is refused" has "${s}/out" "EAS_REQUIRE_AMX must be true or false"

# Refusals in the preflight: rc 1, the cause named, no facts file.
# refused <stub dir> <name> <text>
refused() {
    if [ "$(cat "$1/rc")" = 1 ] && [ ! -e "$1/work/facts.json" ] && has "$1/out" "$3"; then ok "$2"
    else fail "$2" "rc $(cat "$1/rc"): $(cat "$1/out")"; fi
}
s=$(stub_dir cni xeon-gnr "" 512Gi)
echo '["/opt/cni/bin"]' > "${s}/bin_dirs"
preflight "${s}"
refused "${s}" "unknown bin_dirs [\"/opt/cni/bin\"] refused, named" 'bin_dirs ["/opt/cni/bin"] is neither'
s=$(stub_dir noexec xeon-gnr "" 512Gi)
: > "${s}/no-exec"
preflight "${s}"
refused "${s}" "unreadable containerd config refused, EAS_ISTIO_PLATFORM named" "Set EAS_ISTIO_PLATFORM=k3d or k3s"
preflight "${s}" EAS_ISTIO_PLATFORM=k3s
check "EAS_ISTIO_PLATFORM=k3s: no containerd read needed" [ "$(fact "${s}" .istio_platform)" = k3s ]

s=$(stub_dir sb108 xeon-gnr "" 512Gi)
printf 'registry.k8s.io/agent-sandbox/agent-sandbox-controller:v1.0.8@sha256:e1d96312487eba7d8f001e848a9d4e644b7deea8c2a4564ccc318abb729be731' > "${s}/sandbox-image"
preflight "${s}"
refused "${s}" "agent-sandbox v1.0.8 controller refused with the sandbox-down hint" "Run \`k3d-gpu sandbox down\` first, or use a separate cluster"
check "the sandbox refusal comes before any docker call" bash -c "! grep -q '^docker' '${s}/calls'"
s=$(stub_dir sb050 xeon-gnr "" 512Gi)
printf 'registry.k8s.io/agent-sandbox/agent-sandbox-controller:v0.5.0' > "${s}/sandbox-image"
preflight "${s}"
check "agent-sandbox v0.5.0 (EAT's own) proceeds" [ "$(cat "${s}/rc")" = 0 ]
s=$(stub_dir sbnone xeon-gnr "" 512Gi)
preflight "${s}"
check "no agent-sandbox controller proceeds" [ "$(cat "${s}/rc")" = 0 ]

s=$(stub_dir fitcpu raptorlake-nvidia 12 125Gi)
preflight "${s}"
refused "${s}" "cpu requests above allocatable refused with the numbers" \
    "cpu allocatable 12, already requested 2.2, qwen3guard 8, qwen3-1-7b >= 2 (needs 12.2)"
s=$(stub_dir fitmem raptorlake-nvidia "" 20Gi)
preflight "${s}"
refused "${s}" "memory requests above allocatable refused with the numbers" \
    "memory allocatable 20.0 GiB, already requested 3.5 GiB, qwen3guard 16Gi, qwen3-1-7b 11Gi from k3d-gpu fit (needs 30.5 GiB)"
s=$(stub_dir fitno raptorlake-nvidia "" 125Gi)
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d["fits"]=False; d["summary"]="no placement (stub)"; json.dump(d, open(sys.argv[1], "w"))' "${s}/fit.json"
preflight "${s}"
refused "${s}" "a model k3d-gpu fit cannot place is refused" "does not fit this host: no placement (stub)"
s=$(stub_dir shrink raptorlake-nvidia 16 125Gi)
preflight "${s}"
check "a smaller node gets a smaller explicit cpu (16 - 2.2 - 8 -> 5)" [ "$(fact "${s}" .model.cpu)" = 5 ]
co=$(checkout co-shrink)
render "${co}" "${s}/work/facts.json"
check "render rewrites the entry to the fitted cpu and prints it" has "${co}.out" "qwen3-1-7b.cpu: auto -> 5"
check "models.yaml carries cpu 5" [ "$(yq '.models[] | select(.name == "qwen3-1-7b") | .cpu' "${co}/env/local/models.yaml")" = 5 ]
s=$(stub_dir cpuset raptorlake-nvidia "" 125Gi)
preflight "${s}" EAS_MODEL_CPU=12
check "EAS_MODEL_CPU=12 is used as given" [ "$(fact "${s}" .model.cpu)" = 12 ]
s=$(stub_dir cpubig raptorlake-nvidia "" 125Gi)
preflight "${s}" EAS_MODEL_CPU=30
refused "${s}" "EAS_MODEL_CPU above what the node has is refused" "qwen3-1-7b 30 (needs 40.2)"
s=$(stub_dir othermodel raptorlake-nvidia "" 125Gi)
preflight "${s}" EAS_MODEL=qwen3-4b
refused "${s}" "EAS_MODEL without EAS_MODEL_ID is refused" "EAS_MODEL=qwen3-4b needs EAS_MODEL_ID"

# Render refuses an upstream key that is gone, before it writes anything.
s=$(stub_dir keys xeon-gnr "" 512Gi)
preflight "${s}"
# gone <name> <file in checkout> <key regex> <key named>
gone() {
    local co
    co=$(checkout "co-$1")
    sed -i "/^$3:/d" "${co}/$2"
    cp "${co}/env/local/global_config.yaml" "${co}.seed"
    render "${co}" "${s}/work/facts.json"
    if [ "$(cat "${co}.rc")" = 1 ] && has "${co}.out" "upstream key $4 not found in $2" \
        && cmp -s "${co}/env/local/global_config.yaml" "${co}.seed" && [ ! -e "${co}/env/local/istio-values.k3d-gpu.yaml" ]; then
        ok "render refuses when $4 is gone from $2, names it, writes nothing"
    else fail "render with $4 gone from $2" "$(cat "${co}.out")"; fi
}
gone istio roles/istio/defaults/main.yaml istio_helm_default_values_file istio_helm_default_values_file
gone nfd ext/enterprise.ai-inference/roles/nfd/defaults/main.yaml nfd_enabled nfd_enabled
gone router ext/enterprise.agent-toolkit/roles/agent_sandbox/defaults/main.yaml agent_sandbox_router_image agent_sandbox_router_image
co=$(checkout co-guard)
sed -i 's/^    memory: 16Gi$/    memory: 12Gi/' "${co}/ext/enterprise.ai-inference/model_manager/models.yaml"
render "${co}" "${s}/work/facts.json"
check "render refuses a guard whose catalog requests changed" has "${co}.out" "models[qwen3guard] with cpu 8 and memory 16Gi"
co=$(checkout co-noinit)
rm -rf "${co}/env"
render "${co}" "${s}/work/facts.json"
check "render before init is refused" has "${co}.out" "run 'es_auto_installer.sh init agent-toolkit --env local' first"
co=$(checkout co-schema)
echo '{"schema": "other"}' > "${WORK}/bad-facts.json"
render "${co}" "${WORK}/bad-facts.json"
check "render refuses a facts file of another schema" has "${co}.out" "is not a k3d-gpu.eas-facts/v1 facts file"

# ---- 3. pins --------------------------------------------------------------------
# repo <dir>: a git repository of what is in <dir>, one commit.
repo() {
    git -C "$1" init -q && git -C "$1" add -A \
        && git -C "$1" -c user.name=t -c user.email=t@invalid commit -qm fixture
}
co=$(checkout co-pins)
rm -rf "${co}/env" "${co}/ext"
repo "${co}"
out=$(bash "${EAS}/eas-up.sh" check-head "${co}" 2>&1); rc=$?
if [ "${rc}" = 1 ] && grep -qF "not EAS v1.2.0-rc.1 (${EAS_COMMIT})" <<< "${out}"; then ok "EAS checkout at another commit refused"
else fail "EAS checkout at another commit" "${out}"; fi
out=$(bash "${EAS}/eas-up.sh" pin-revs "${co}" 2>&1); rc=$?
m="${co}/configs/repos/repos.agent-toolkit.yaml"
if [ "${rc}" = 0 ] && [ "$(yq '.repos[] | select(.dest == "enterprise.ai-inference") | .rev' "${m}")" = "${EI_COMMIT}" ] \
    && [ "$(yq '.repos[] | select(.dest == "enterprise.agent-toolkit") | .rev' "${m}")" = "${EAT_COMMIT}" ]; then
    ok "pin-revs: the manifest's revs are the pinned SHAs"
else fail "pin-revs rewrites the revs" "${out}"; fi
check "pin-revs prints release-1.1 -> the EI pin" grep -qF "inference rev release-1.1 -> ${EI_COMMIT}" <<< "${out}"
out=$(bash "${EAS}/eas-up.sh" pin-revs "${co}" 2>&1)
check "pin-revs a second time changes nothing" grep -qF "agent-toolkit rev ${EAT_COMMIT} (unchanged)" <<< "${out}"
cp "${m}" "${WORK}/manifest"
sed -i 's#https://github.com/intel/enterprise-agent-toolkit#https://github.com/example/fork#' "${m}"
out=$(bash "${EAS}/eas-up.sh" pin-revs "${co}" 2>&1); rc=$?
check "a manifest naming another repo is refused" grep -qF "has no repo layer=agent-toolkit url=https://github.com/intel/enterprise-agent-toolkit" <<< "${out}"
cp "${WORK}/manifest" "${m}"
yq -i '.repos += [{"layer": "extra", "url": "https://example.invalid/x", "dest": "x", "rev": "main"}]' "${m}"
out=$(bash "${EAS}/eas-up.sh" pin-revs "${co}" 2>&1); rc=$?
if [ "${rc}" = 1 ] && grep -qF "lists 3 repos, not the 2 pinned here" <<< "${out}"; then ok "a manifest with a third repo is refused"
else fail "a manifest with a third repo" "${out}"; fi
mkdir -p "${co}/ext/enterprise.ai-inference" "${co}/ext/enterprise.agent-toolkit"
echo x > "${co}/ext/enterprise.ai-inference/f"; repo "${co}/ext/enterprise.ai-inference"
echo y > "${co}/ext/enterprise.agent-toolkit/f"; repo "${co}/ext/enterprise.agent-toolkit"
out=$(bash "${EAS}/eas-up.sh" check-ext "${co}" 2>&1); rc=$?
if [ "${rc}" = 1 ] && grep -qF "ext/enterprise.ai-inference is at" <<< "${out}" && grep -qF "not the pinned ${EI_COMMIT}" <<< "${out}"; then
    ok "ext repo at another commit refused, named"
else fail "ext repo at another commit" "${out}"; fi
rm -rf "${co}/ext/enterprise.ai-inference"
out=$(bash "${EAS}/eas-up.sh" check-ext "${co}" 2>&1); rc=$?
check "a missing ext repo is refused" grep -qF "ext/enterprise.ai-inference is missing" <<< "${out}"

# ---- 4. the rebased envoy patch ---------------------------------------------------
co=$(checkout co-patch)
rm -rf "${co}/env" "${co}/ext"
repo "${co}"
PATCH="${EAS}/patches/0006-envoy-control-plane-affinity-rc1.patch"
check "0006 rc1 passes git apply --check on the rc's EnvoyProxy task" git -C "${co}" apply --check "${PATCH}"
out=$(bash "${EAS}/eas-up.sh" patch "${co}" 2>&1); rc=$?
check "eas-up.sh patch applies it" [ "${rc}" = 0 ]
if python3 - "${co}/roles/envoy_gateway/tasks/install.yaml" <<'PY'
import sys, yaml
tasks = yaml.safe_load(open(sys.argv[1]))
t = next(t for t in tasks if t.get("name", "").startswith("envoy_gateway | Create EnvoyProxy config"))
pod = t["kubernetes.core.k8s"]["definition"]["spec"]["provider"]["kubernetes"]["envoyDaemonSet"]["pod"]
assert "nodeSelector" in pod and pod["nodeSelector"] is None, pod
expr = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"]
assert expr == [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists"}], expr
assert pod["tolerations"][0]["key"] == "node-role.kubernetes.io/control-plane"
PY
then ok "patched task: control-plane affinity Exists, nodeSelector null, toleration kept"
else fail "patched EnvoyProxy task"; fi
check "only the EnvoyProxy task changed" [ "$(git -C "${co}" status --porcelain | awk '{print $2}')" = roles/envoy_gateway/tasks/install.yaml ]
out=$(bash "${EAS}/eas-up.sh" patch "${co}" 2>&1); rc=$?
check "a second patch run is a no-op" grep -qF "already applied" <<< "${out}"
co=$(checkout co-moved)
rm -rf "${co}/env" "${co}/ext"
sed -i 's/node-role.kubernetes.io\/control-plane: ""/node-role.kubernetes.io\/control-plane: "true"/' "${co}/roles/envoy_gateway/tasks/install.yaml"
repo "${co}"
out=$(bash "${EAS}/eas-up.sh" patch "${co}" 2>&1); rc=$?
if [ "${rc}" = 1 ] && grep -qF "the EnvoyProxy task moved upstream" <<< "${out}"; then ok "a moved EnvoyProxy task is refused"
else fail "a moved EnvoyProxy task" "${out}"; fi

# ---- 5. a full run stops in the preflight -------------------------------------------
for c in sb108 cni; do
    s="${WORK}/${c}"
    : > "${s}/calls"
    rm -rf "${s}/work"
    env STUB="${s}" PATH="${PBIN}:${PATH}" EAS_WORK="${s}/work" EAS_KUBECONFIG="${s}/kubeconfig" \
        bash "${EAS}/eas-up.sh" > "${s}/out" 2>&1
    rc=$?
    if [ "${rc}" = 1 ] && [ ! -e "${s}/work/eas" ] && ! grep -qE '^docker (build|run)' "${s}/calls"; then
        ok "full run with the ${c} refusal: rc 1, nothing cloned, built or run"
    else fail "full run with the ${c} refusal" "rc ${rc}: $(cat "${s}/out") $(cat "${s}/calls")"; fi
done

# ---- 6. eas-check.sh -----------------------------------------------------------------
CBIN="${WORK}/cbin"
mkdir -p "${CBIN}"
cat > "${CBIN}/kubectl" <<'EOF'
#!/usr/bin/env bash
echo "kubectl $*" >> "${STUB}/argv"
case "$*" in
    "get daemonsets -A -o json") cat "${STUB}/daemonsets.json" ;;
    "get deployments -A -o json") cat "${STUB}/deployments.json" ;;
    "get pods -A -o json") cat "${STUB}/pods.json" ;;
    "-n litellm get httproute litellm -o jsonpath={.spec.hostnames[0]}") printf litellm.solutions.ai ;;
    "-n litellm get httproute litellm -o jsonpath={.spec.parentRefs[0].namespace}/{.spec.parentRefs[0].name}") printf envoy-gateway-system/eg-gateway ;;
    "-n envoy-gateway-system get gateway eg-gateway -o jsonpath={.status.addresses[0].value}") printf 172.20.0.3 ;;
    "-n litellm get secret litellm-master-key -o jsonpath={.data.master_key}") printf 'sk-master-S3CRET' | base64 ;;
    "-n agent-sandbox-system get secret sandbox-router-auth -o jsonpath={.data.ROUTER_AUTH_TOKEN}") printf 'router-T0KEN' | base64 ;;
    *port-forward*) exec sleep 60 ;;
    *) echo "kubectl stub: unexpected: $*" >&2; exit 1 ;;
esac
EOF
cat > "${CBIN}/curl" <<'EOF'
#!/usr/bin/env python3
"""curl stub: the LiteLLM route (behind --resolve) and the sandbox router; logs argv."""
import json, os, sys

stub = os.environ["STUB"]
args = sys.argv[1:]
with open(os.path.join(stub, "argv"), "a") as f:
    f.write("curl " + " ".join(args) + "\n")
headers, body, url, out, wfmt, resolve = {}, None, None, None, None, None
i = 0
while i < len(args):
    a = args[i]
    if a in ("-H", "-d", "-o", "-w", "--resolve", "--max-time"):
        v = args[i + 1]
        if a == "-H":
            lines = sys.stdin.read().splitlines() if v == "@-" else [v]
            for line in lines:
                k, _, val = line.partition(":")
                headers[k.strip().lower()] = val.strip()
        elif a == "-d":
            body = json.loads(v)
        elif a == "-o":
            out = v
        elif a == "-w":
            wfmt = v
        elif a == "--resolve":
            resolve = v
        i += 2
        continue
    if not a.startswith("-"):
        url = a
    i += 1


def reply(code, doc):
    text = json.dumps(doc)
    if out:
        open(out, "w").write(text)
    else:
        sys.stdout.write(text)
    if wfmt:
        sys.stdout.write(str(code))
    sys.exit(0)


auth = headers.get("authorization", "")
if url == "127.0.0.1:18081":
    sys.exit(0)
if url == "127.0.0.1:18081/execute":
    if auth != "Bearer router-T0KEN" or headers.get("x-sandbox-id") != "python-pool-abcde":
        reply(401, {"error": "unauthorized"})
    cmd = body["command"]
    assert cmd == "python3 -c 'print(sum(i * i for i in range(1, 11)))'", cmd
    reply(200, {"stdout": os.environ.get("STUB_SANDBOX_OUT", "385") + "\n", "stderr": "", "exit_code": 0})
if resolve != "litellm.solutions.ai:443:172.20.0.3" or not url.startswith("https://litellm.solutions.ai/"):
    sys.stderr.write("curl stub: not the LiteLLM route: %s %s\n" % (resolve, url))
    sys.exit(7)
path = url[len("https://litellm.solutions.ai"):]
master, vkey = "Bearer sk-master-S3CRET", "Bearer sk-virtual-V1RTUAL"
if path == "/key/generate":
    reply(200, {"key": "sk-virtual-V1RTUAL", "key_alias": body["key_alias"]}) if auth == master else reply(401, {"error": {"message": "no"}})
if path == "/key/delete":
    reply(200, {"deleted_keys": body["key_aliases"]}) if auth == master else reply(401, {"error": {"message": "no"}})
if path == "/v1/chat/completions":
    if auth != vkey:
        reply(401, {"error": {"message": "Authentication Error"}})
    text = body["messages"][0]["content"]
    if os.environ.get("STUB_GUARD_DOWN"):
        reply(400, {"error": {"message": "Blocked by the content-safety guardrail: the guard model backend is unreachable (503)."}})
    if "pipe bomb" in text and os.environ.get("STUB_GUARD_BLOCKS", "1") == "1":
        reply(400, {"error": {"message": "Blocked by the content-safety guardrail: unsafe content (categories: Violent)."}})
    if body.get("tools"):
        reply(200, {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                    "tool_calls": [{"type": "function", "function": {"name": "get_weather", "arguments": "{\"city\": \"Portland\"}"}}]}}]})
    reply(200, {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Kubernetes runs containers."}}]})
reply(404, {"error": {"message": "no route"}})
EOF
chmod +x "${CBIN}"/*

# cluster <dir>: the cluster state of a good install, as kubectl -A -o json lists it.
cluster() {
    mkdir -p "$1"
    : > "$1/argv"
    python3 - "$1" <<'PY'
import json, sys
d = sys.argv[1]
def obj(ns, name, image, **extra):
    o = {"metadata": {"namespace": ns, "name": name}, "spec": {"template": {"spec": {"containers": [{"image": image}]}}}}
    o.update(extra)
    return o
ready = {"conditions": [{"type": "PodScheduled", "status": "True"}, {"type": "Ready", "status": "True"}], "phase": "Running"}
def pod(ns, name, labels, status=ready):
    return {"metadata": {"namespace": ns, "name": name, "labels": labels}, "status": status}
ds = [obj("istio-system", "istio-cni-node", "docker.io/istio/install-cni:1.27.3", status={"desiredNumberScheduled": 1, "numberReady": 1}),
      obj("istio-system", "ztunnel", "docker.io/istio/ztunnel:1.27.3", status={"desiredNumberScheduled": 1, "numberReady": 1}),
      obj("kube-system", "nri-resource-policy-balloons", "ghcr.io/containers/nri-plugins/nri-resource-policy-balloons:v0.15.0"),
      obj("node-feature-discovery", "nfd-worker", "registry.k8s.io/nfd/node-feature-discovery:v0.20.1")]
deploys = [obj("agent-sandbox-system", "agent-sandbox-controller", "registry.k8s.io/agent-sandbox/agent-sandbox-controller:v0.5.0"),
           obj("litellm", "litellm", "ghcr.io/berriai/litellm:v1.83.14-stable.patch.2")]
envoy = {"app.kubernetes.io/name": "envoy", "app.kubernetes.io/component": "proxy", "app.kubernetes.io/managed-by": "envoy-gateway"}
pods = [pod("envoy-gateway-system", "envoy-eg-gateway-abc", envoy),
        pod("llm-inference", "qwen3guard-kserve-1", {"app.kubernetes.io/name": "qwen3guard"}),
        pod("llm-inference", "qwen3-1-7b-kserve-1", {"app.kubernetes.io/name": "qwen3-1-7b"}),
        pod("agent-sandbox-system", "python-pool-abcde", {})]
for k, v in (("daemonsets", ds), ("deployments", deploys), ("pods", pods)):
    json.dump({"items": v}, open("%s/%s.json" % (d, k), "w"))
PY
}
# edit <dir> <kind> <python expression on items>: change one listing.
edit() {
    python3 - "$1/$2.json" "$3" <<'PY'
import json, sys
listing, code = sys.argv[1:]
items = json.load(open(listing))["items"]
exec(code)
json.dump({"items": items}, open(listing, "w"))
PY
}
# eascheck <dir> [VAR=value...]: eas-check.sh; output in <dir>/out, rc in <dir>/rc.
eascheck() {
    local d=$1
    shift
    env "$@" STUB="${d}" PATH="${CBIN}:${PATH}" EAS_KUBECONFIG="${d}/kubeconfig" \
        bash "${EAS}/eas-check.sh" > "${d}/out" 2>&1
    echo $? > "${d}/rc"
}
# failed <dir> <name> <text>: rc 1 and the cause in the output.
failed() {
    if [ "$(cat "$1/rc")" = 1 ] && has "$1/out" "$3"; then ok "$2"; else fail "$2" "rc $(cat "$1/rc"): $(cat "$1/out")"; fi
}

d="${WORK}/check-pass"
cluster "${d}"
eascheck "${d}"
if [ "$(cat "${d}/rc")" = 0 ] && has "${d}/out" "eas-check passed." && ! grep -q '^FAIL' "${d}/out"; then
    ok "eas-check: every item passes on a good install"
else fail "eas-check all-pass" "$(cat "${d}/out")"; fi
check "eas-check: the tool call names get_weather" has "${d}/out" 'tool call: finish_reason tool_calls, get_weather({"city": "Portland"})'
check "eas-check: the guardrail block is reported" has "${d}/out" "guardrail: unsafe prompt blocked, HTTP 400"
check "eas-check: the virtual key is deleted" has "${d}/out" "virtual key k3d-gpu-eas-check-"
check "eas-check: the sandbox printed 385" has "${d}/out" "printed 385"
check "eas-check: reached LiteLLM on its route through --resolve" grep -q -- '--resolve litellm.solutions.ai:443:172.20.0.3' "${d}/argv"
check "eas-check: the master key is in no argument" bash -c "! grep -q 'sk-master-S3CRET' '${d}/argv'"
check "eas-check: the virtual key is in no argument" bash -c "! grep -q 'sk-virtual-V1RTUAL' '${d}/argv'"
check "eas-check: the router token is in no argument" bash -c "! grep -q 'router-T0KEN' '${d}/argv'"
check "eas-check: no key in the output" bash -c "! grep -qE 'sk-master-S3CRET|sk-virtual-V1RTUAL|router-T0KEN' '${d}/out'"

d="${WORK}/check-amx"
cluster "${d}"
edit "${d}" pods 'p = next(p for p in items if p["metadata"]["name"] == "qwen3guard-kserve-1"); p["status"] = {"phase": "Pending", "conditions": [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable", "message": "0/1 nodes are available: 1 node(s) didn'"'"'t match Pod'"'"'s node affinity/selector. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling."}]}'
eascheck "${d}" STUB_GUARD_DOWN=1
failed "${d}" "eas-check: guard Pending on the AMX affinity names require_amx" "model qwen3guard: qwen3guard-kserve-1 Pending, require_amx"
failed "${d}" "eas-check: the fail-closed guardrail refusing chat is named" "refused by the fail-closed guardrail (guard qwen3guard not Ready)"

d="${WORK}/check-mem"
cluster "${d}"
edit "${d}" pods 'p = next(p for p in items if p["metadata"]["name"] == "qwen3-1-7b-kserve-1"); p["status"] = {"phase": "Pending", "conditions": [{"type": "PodScheduled", "status": "False", "message": "0/1 nodes are available: 1 Insufficient memory."}]}'
eascheck "${d}"
failed "${d}" "eas-check: Insufficient memory names the fit" "model qwen3-1-7b: qwen3-1-7b-kserve-1 Pending, fit:"

d="${WORK}/check-nri"
cluster "${d}"
edit "${d}" daemonsets 'items.append({"metadata": {"namespace": "nri-plugins", "name": "nri-resource-policy-balloons"}, "spec": {"template": {"spec": {"containers": [{"image": "ghcr.io/containers/nri-plugins/nri-resource-policy-balloons:v0.14.0"}]}}}})'
eascheck "${d}"
failed "${d}" "eas-check: two NRI balloons DaemonSets fail" "NRI: 2 balloons policy DaemonSet(s)"

d="${WORK}/check-nfd"
cluster "${d}"
edit "${d}" daemonsets 'items.append({"metadata": {"namespace": "node-feature-discovery", "name": "node-feature-discovery-worker"}, "spec": {"template": {"spec": {"containers": [{"image": "registry.k8s.io/nfd/node-feature-discovery:v0.17.1"}]}}}})'
eascheck "${d}"
failed "${d}" "eas-check: two NFDs fail" "NFD: 2 worker DaemonSet(s)"

d="${WORK}/check-envoy"
cluster "${d}"
edit "${d}" pods 'items[:] = [p for p in items if not p["metadata"]["name"].startswith("envoy-")]'
eascheck "${d}"
failed "${d}" "eas-check: zero envoy data-plane pods fail" "Envoy Gateway data plane: 0 of 0 envoy pod(s) Ready"

d="${WORK}/check-cni"
cluster "${d}"
edit "${d}" daemonsets 'next(x for x in items if x["metadata"]["name"] == "istio-cni-node")["status"]["numberReady"] = 0'
eascheck "${d}"
failed "${d}" "eas-check: istio-cni not Ready fails" "Istio ambient: istio-system/istio-cni-node 0/1 Ready"

d="${WORK}/check-sb"
cluster "${d}"
edit "${d}" deployments 'items.append({"metadata": {"namespace": "agent-sandbox", "name": "agent-sandbox-controller"}, "spec": {"template": {"spec": {"containers": [{"image": "registry.k8s.io/agent-sandbox/agent-sandbox-controller:v1.0.8"}]}}}})'
eascheck "${d}"
failed "${d}" "eas-check: a second agent-sandbox controller fails" "agent-sandbox: 2 controller(s)"

d="${WORK}/check-guard"
cluster "${d}"
eascheck "${d}" STUB_GUARD_BLOCKS=0
failed "${d}" "eas-check: a guardrail that does not block fails" "guardrail: an unsafe prompt was not blocked (HTTP 200)"

d="${WORK}/check-384"
cluster "${d}"
eascheck "${d}" STUB_SANDBOX_OUT=384
failed "${d}" "eas-check: a sandbox printing 384 fails" "printed '384', expected 385"

echo
echo "${PASS} passed, ${FAILS} failed"
[ "${FAILS}" -eq 0 ]
