#!/usr/bin/env bash
# guest-b.sh: VM b's setup for H62's B5 (EXPERIMENTS.md "H62 pre-registration"), run in the guest
# by setup.sh as user ubuntu, under its 120 min ceiling. B5 is Intel's documented single-node
# install of the Agent Toolkit: EAT release-2.0.0 docs/single-node-deployment.md from Step 1, with
# the clone the v1.2.0-rc.1 release notes give in place of the guide's `-b release-1.2`, on the
# inventory EAS ships (Option A, localhost: Kubespray installs Kubernetes here).
#   - prerequisites.md "Node Kernel Settings", verbatim
#   - clone, D1 (the layer manifest's two branch revs pinned to the commits Intel's comments on
#     those lines name, before init), configure (its y/N prompt answered y on a terminal,
#     amendment V2), init agent-toolkit
#   - the documented edit (gemma-4-26b in llm_services_autodeploy_models) and D2 (gemma-4-26b's
#     model_id is the QAT checkpoint), then install agent-toolkit
#   - wait for the gemma-4-26b workload pod, then the records; if the install fails, the
#     cluster's nodes, pods, events and the pods that are not Ready (amendment V4)
# Nothing else is changed: guardrail, hybrid router, observability, NRI balloons, Istio, MetalLB
# and the agent sandbox are EAS's and EAT's defaults. Records: ~/h62/records (no kubeconfig, no
# secret file).
set -euo pipefail
G=${HOME}/h62
P() { python3 "${G}/h62.py" "$@"; }
R=${G}/records
E=${HOME}/enterprise-ai-solutions
TAG=$(P pin eas_tag) SHA=$(P pin eas_sha)
mkdir -p "${R}"
step() { echo "== $1 $(date -u +%FT%TZ)" | tee -a "${R}/steps.txt"; }
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
python3 "${G}/h62.py" eas-pin configs/repos/repos.agent-toolkit.yaml > "${R}/d1.diff"
step configure
# configure takes no option (v1.2.0-rc.1 refuses `configure --force`) and its one y/N prompt
# refuses a stdin that is not a terminal, so it runs on a terminal that answers y, as a person
# at one would (amendment V2); configure's own steps are unchanged.
python3 "${G}/h62.py" on-tty "${R}/configure.log" y ./es_auto_installer.sh configure
step init
./es_auto_installer.sh init agent-toolkit > "${R}/init.log" 2>&1
for pair in "enterprise.ai-inference $(P pin ei_sha)" "enterprise.agent-toolkit $(P pin eat_sha)"; do
    read -r dest want <<< "${pair}"
    have=$(git -C "ext/${dest}" rev-parse HEAD)
    echo "${dest} ${have}" >> "${R}/ext-commits.txt"
    [ "${have}" = "${want}" ] || { echo "ext/${dest} is at ${have}, not ${want}"; exit 1; }
done
step edit
# base_domain_name stays EAS's default: global_config.yaml is not edited
python3 "${G}/h62.py" eas-edit env/local > "${R}/edit.diff"
for f in global_config.yaml config.agent-toolkit.yaml config.inference.yaml models.yaml inventory/hosts.yaml; do
    [ ! -f "env/local/${f}" ] || cp "env/local/${f}" "${R}/env-${f//\//-}"
done
step install
if ! ./es_auto_installer.sh install agent-toolkit > "${R}/install.log" 2>&1; then
    # Amendment V4: the cluster's state when Intel's install fails, read before teardown removes
    # the guest. Records only; the install still counts as failed.
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
    exit 1
fi
step ready
export KUBECONFIG=${E}/env/local/kubeconfig.yaml
printf 'export KUBECONFIG=%s\n' "${KUBECONFIG}" > "${G}/env"
NS=$(P arm B5 ns) SEL=$(P arm B5 selector)
until kubectl -n "${NS}" get pods -l "${SEL}" -o json | P pick-pod "" > "${R}/ready-pod.txt"; do sleep 15; done
step records
{ ./es_auto_installer.sh status; echo "rc=$?"; } > "${R}/status.txt" 2>&1 || true
{ python3 --version; yq --version; kubectl version; helm version; uname -r; } > "${R}/versions.txt" 2>&1 || true
kubectl get nodes -o json > "${R}/nodes.json"
kubectl get pods -A -o wide > "${R}/pods.txt"
kubectl -n "${NS}" get pods -o json > "${R}/llm-inference-pods.json"
kubectl -n "${NS}" get llminferenceservices -o yaml > "${R}/llminferenceservices.yaml"
step finished
