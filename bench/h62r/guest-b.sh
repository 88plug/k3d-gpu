#!/usr/bin/env bash
# guest-b.sh: VM b's setup for H62r's B5 (EXPERIMENTS.md "H62r pre-registration"), run in the
# guest by setup.sh as user ubuntu, under its 120 min ceiling. B5 is Intel's documented
# single-node install of the Agent Toolkit at the GA tags: EAT release-2.0.0
# docs/single-node-deployment.md from Step 1, with EAS cloned at v1.2.0 (the commit the guide's
# `-b release-1.2` names, read 2026-11-06), on the inventory EAS ships (Option A, localhost:
# Kubespray installs Kubernetes here).
#   - prerequisites.md "Node Kernel Settings", verbatim
#   - clone; D1, now a check (the v1.2.0 layer manifest names the release commits itself);
#     configure (its y/N prompt answered y on a terminal, H62 amendment V2); init agent-toolkit
#   - the documented edit (gemma-4-26b in llm_services_autodeploy_models) and D2 (gemma-4-26b's
#     model_id is the QAT checkpoint)
#   - D3's sibling read: EAS's own topology probe (from this clone) and the kernel's
#     thread_siblings_list; when the probe reads one thread per core where the kernel lists two,
#     nri_reserved_cpu_list (EAS's global config, env/local) names the siblings as the probe
#     defines them (the higher thread of each core), what EAS's default 'siblings' profile
#     reserves on crescent's host
#   - install agent-toolkit, wait for the gemma-4-26b workload pod, then the records; the sizing
#     records (balloon policies, every pod's CPU request, the nodes, the sandbox router) pass or
#     fail; on a failure also H62 amendment V4's records
# Nothing else is changed: guardrail, hybrid router, observability, NRI balloons, Istio, MetalLB
# and the agent sandbox are EAS's and EAT's defaults. Records: ~/h62r/records (no kubeconfig, no
# secret file).
set -euo pipefail
G=${HOME}/h62r
P() { python3 "${G}/h62r.py" "$@"; }
R=${G}/records
E=${HOME}/enterprise-ai-solutions
TAG=$(P pin eas_tag) SHA=$(P pin eas_sha)
mkdir -p "${R}"
step() { echo "== $1 $(date -u +%FT%TZ)" | tee -a "${R}/steps.txt"; }
# sizing PREFIX: what `cpu: auto` read and rendered, and whether the sandbox router was placed.
sizing() {
    kubectl get balloonspolicies.config.nri -A -o yaml > "${R}/$1-balloonspolicies.yaml" 2>&1 || true
    kubectl get pods -A -o custom-columns='NS:.metadata.namespace,POD:.metadata.name,PHASE:.status.phase,CPU:.spec.containers[*].resources.requests.cpu,LIMIT:.spec.containers[*].resources.limits.cpu' \
        > "${R}/$1-cpu-requests.txt" 2>&1 || true
    kubectl describe nodes > "${R}/$1-nodes.txt" 2>&1 || true
    kubectl -n agent-sandbox-system get deploy sandbox-router-deployment -o wide > "${R}/$1-router.txt" 2>&1 || true
}
echo "HF_TOKEN: $([ -n "${HF_TOKEN:-}" ] && echo set || echo unset)" > "${R}/hf-token.txt"
step sysctl
sudo tee /etc/sysctl.d/90-inotify.conf > /dev/null <<'EOF'
fs.inotify.max_user_instances = 8192
fs.inotify.max_user_watches   = 1048576
EOF
sudo sysctl --system 2>&1 | tee "${R}/sysctl.txt" > /dev/null
step clone
[ -d "${E}" ] || git clone -q -b "${TAG}" https://github.com/intel/enterprise-ai-solutions.git "${E}"
cd "${E}"
[ "$(git rev-parse HEAD)" = "${SHA}" ] || { echo "EAS ${TAG} is not ${SHA}"; exit 1; }
step d1
P eas-check configs/repos/repos.agent-toolkit.yaml > "${R}/d1.txt"
step configure
# configure takes no option and its one y/N prompt refuses a stdin that is not a terminal (v1.2.0's
# es_auto_installer.sh is rc.1's), so it runs on a terminal that answers y, as a person at one
# would (H62 amendment V2); configure's own steps are unchanged.
P on-tty "${R}/configure.log" y ./es_auto_installer.sh configure
step init
./es_auto_installer.sh init agent-toolkit > "${R}/init.log" 2>&1
for pair in "enterprise.ai-inference $(P pin ei_sha)" "enterprise.agent-toolkit $(P pin eat_sha)"; do
    read -r dest want <<< "${pair}"
    have=$(git -C "ext/${dest}" rev-parse HEAD)
    echo "${dest} ${have}" >> "${R}/ext-commits.txt"
    [ "${have}" = "${want}" ] || { echo "ext/${dest} is at ${have}, not ${want}"; exit 1; }
done
step edit
P eas-edit env/local > "${R}/edit.diff"
step d3-siblings
sh roles/nri_cpu_balloons/files/probe-node-topology.sh > "${R}/probe.txt"
for c in /sys/devices/system/cpu/cpu[0-9]*; do
    echo "${c##*/} $(cat "${c}/topology/thread_siblings_list")"
done > "${R}/thread-siblings.txt"
cpus=$(P sibling-read /sys/devices/system/cpu "${R}/probe.txt")
if [ -n "${cpus}" ]; then echo "${cpus}"; else echo "none: the probe reads the kernel threads per core"; fi > "${R}/d3-siblings.txt"
[ -z "${cpus}" ] || P reserve "${cpus}" env/local/global_config.yaml > "${R}/d3-siblings.diff"
for f in global_config.yaml config.agent-toolkit.yaml config.inference.yaml models.yaml inventory/hosts.yaml; do
    [ ! -f "env/local/${f}" ] || cp "env/local/${f}" "${R}/env-${f//\//-}"
done
step install
if ! ./es_auto_installer.sh install agent-toolkit > "${R}/install.log" 2>&1; then
    # H62 amendment V4: the cluster's state when Intel's install fails, read before teardown
    # removes the guest, and the sizing records. Records only; the install still counts as failed.
    step install-failed
    export KUBECONFIG=${E}/env/local/kubeconfig.yaml
    { kubectl get nodes -o wide; kubectl describe nodes; } > "${R}/fail-nodes.txt" 2>&1 || true
    kubectl get pods -A -o wide > "${R}/fail-pods.txt" 2>&1 || true
    kubectl get events -A --sort-by=.lastTimestamp > "${R}/fail-events.txt" 2>&1 || true
    kubectl get pods -A --no-headers 2>/dev/null \
        | awk '{split($3, r, "/"); if (r[1] != r[2] && $4 != "Completed") print $1, $2}' \
        | while read -r ns pod; do
            echo "=== ${ns}/${pod}"
            kubectl -n "${ns}" describe pod "${pod}"
            kubectl -n "${ns}" logs "${pod}" --all-containers --tail=50
        done > "${R}/fail-describe.txt" 2>&1 || true
    sizing fail
    exit 1
fi
step ready
export KUBECONFIG=${E}/env/local/kubeconfig.yaml
printf 'export KUBECONFIG=%s\n' "${KUBECONFIG}" > "${G}/env"
NS=$(P arm B5 ns) SEL=$(P arm B5 selector)
until kubectl -n "${NS}" get pods -l "${SEL}" -o json | P pick-pod "" > "${R}/ready-pod.txt"; do sleep 15; done
step records
sizing ok
{ ./es_auto_installer.sh status; echo "rc=$?"; } > "${R}/status.txt" 2>&1 || true
{ python3 --version; yq --version; kubectl version; helm version; uname -r; } > "${R}/versions.txt" 2>&1 || true
kubectl get nodes -o json > "${R}/nodes.json"
kubectl get pods -A -o wide > "${R}/pods.txt"
kubectl -n "${NS}" get pods -o json > "${R}/llm-inference-pods.json"
kubectl -n "${NS}" get llminferenceservices -o yaml > "${R}/llminferenceservices.yaml"
step finished
