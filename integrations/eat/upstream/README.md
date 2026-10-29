# Upstream pack: k3s/k3d as a BYO target for Intel's agent platform

These are drafts for Intel's repos. **None of them has been filed, opened or pushed upstream.** A maintainer of this repo sends them by hand, one at a time.

## Which repo owns what

- [intel/enterprise-agent-toolkit#19](https://github.com/intel/enterprise-agent-toolkit/pull/19) ("Migration Agentic Toolkit into Unified Convention") is **open, not merged** as of 2026-10-16.
  - It deletes the toolkit's own installer: `deploy-agentic-stack.sh`, `core/lib/brownfield/`, `core/playbooks/setup-bastion.yml`, `core/playbooks/deploy-istio.yml`, `core/roles/nri_cpu_balloons/`.
  - It moves the toolkit onto `es_auto_installer.sh install agent-toolkit` in [intel/enterprise-ai-solutions](https://github.com/intel/enterprise-ai-solutions) (EAS) and intel/enterprise-inference.
  - It depends on intel/enterprise-inference#22 and goes in ahead of intel/enterprise-ai-solutions#40. Both are open.
  - Re-read 2026-10-29: #19 merged on 2026-10-27 into the `release-2.0.0` branch, not `main`, and no tag carries it. intel/enterprise-inference#22 merged on 2026-10-22. intel/enterprise-ai-solutions#40 was closed unmerged on 2026-10-22, and intel/enterprise-ai-solutions#66 ("Integrate Agentic Toolkit"), opened a minute before, merged on 2026-10-29. Every patch here still passes `checks/apply-check.sh` on EAS `main` `5767995`.
- The platform roles that matter for k3s (istio, envoy_gateway, kubernetes, nri_cpu_balloons) already live in EAS `main`. **EAS is the target for every patch here.**
- There is no toolkit (EAT) patch. Its k3s platform-detection gap sits in `brownfield_deployment()`, which no entry point calls: it is sourced at [deploy-agentic-stack.sh#L631](https://github.com/intel/enterprise-agent-toolkit/blob/0ecf67a8331e8b502ef41e08bf6d2881416178c5/deploy-agentic-stack.sh#L631) and called only from inside itself, and #19 deletes the file. The issue draft lists it as an observation only.

| Repo | Base it was cut from |
|---|---|
| intel/enterprise-agent-toolkit (EAT) | `main` @ `0ecf67a8331e8b502ef41e08bf6d2881416178c5` |
| intel/enterprise-ai-solutions (EAS) | `main` @ `7743bac7e3d334da6eda602db10f7eec6b0360c5`; 0006 regenerated on `main` @ `7c02ca206cccea6119f380312708eb8b97d146f5` |

## Contents

| File | What it is |
|---|---|
| [issue-k3s-byo.md](issue-k3s-byo.md) | Issue draft for EAS, "Support k3s/k3d as a BYO single-node target". Every claim is a permalink to the SHAs above. |
| [patches/](patches/) | `git format-patch` files for EAS, one concern each |
| [checks/](checks/) | Scripts that reproduce the static validation below without a cluster |

## Patches

| # | Repo | What it does | Why it is upstreamable |
|---|---|---|---|
| [0002](patches/0002-eas-istio-global-platform.patch) | EAS | Adds `istio_platform` (default `""`) and passes it to the Istio chart as `global.platform`, on install and upgrade | Istio's platform prerequisites page sets `global.platform` for k3d and k3s. The cni chart's k3d, k3s, microk8s and openshift profiles move the CNI conf/bin dirs (openshift also switches to multus); gke blanks the bin dir and minikube moves the netns dir, so the knob is not k3s-only. The default renders byte-for-byte as today. |
| [0003](patches/0003-eas-pin-nri-balloons-chart.patch) | EAS | Pins the nri-plugins balloons chart to `v0.14.0` via `nri_cpu_balloons_chart_version` | Matches how the repo pins its other charts (`seaweedfs_chart_version`, `istio_csr_helm_chart_version`, ...). v0.14.0 is today's newest release, so no fresh install changes now. |
| [0004](patches/0004-eas-byo-node-topology-labels.patch) | EAS | Applies the `workload-class` node labels on multi-node BYO clusters, as on managed ones; updates the two docs that said "label by hand" | Fixes an install/teardown asymmetry: teardown already removes these labels from BYO nodes. Not k3s-specific. Single-node and the managed path are unchanged. **Behaviour change for existing multi-node BYO users**, kept small: nodes that already carry a `workload-class` label (set by hand, as the old docs said) keep it, and on BYO a kubeconfig without node list/patch RBAC skips labelling instead of failing the install. |
| [0005](patches/0005-eas-docs-single-node-k3d.patch) | EAS | New `docs/deploy/k3d.md` "Single-Node Dev on k3d", linked from `docs/README.md` | Docs only. It builds on the documented BYO path and states the patches it relies on. |
| [0006](patches/0006-eas-envoy-control-plane-affinity.patch) | EAS | In `roles/envoy_gateway/tasks/controller.yaml`, selects envoy's control-plane nodes by label presence (`operator: Exists`) instead of the exact value `""`, and sets `nodeSelector: null` so a re-install over an older EnvoyProxy drops the old selector | **Extra, found during review.** k3s sets `node-role.kubernetes.io/control-plane=true`, so today the envoy DaemonSet schedules zero pods on k3s and the gateway has no data plane. No change on kubeadm/Kubespray. |

Suggested order to send them, after the issue gets a response:

1. 0006 and 0002. These are the blockers.
2. 0004.
3. 0003.
4. 0005, which references 0002 and 0006.

Every patch applies on its own, and they also apply as a series.

The patches carry no `Signed-off-by:`. EAS's CONTRIBUTING.md requires a DCO sign-off, and that certification has to come from the person sending the patch:

```bash
git am --signoff integrations/eat/upstream/patches/0002-eas-istio-global-platform.patch
```

## Static validation

| Check | Result |
|---|---|
| `git apply --check`, each patch on a fresh EAS copy, then the whole series in order | all pass on EAS `main` `e8f3ebc` (2026-10-19, [checks/apply-check.sh](checks/apply-check.sh) on a clone), after 0005 was regenerated for EAS #49's new `docs/README.md` row |
| Same, against the head of intel/enterprise-ai-solutions#40 (`bbb3a53`) | 0002-0004 apply. 0005 does not, because its context now has EAS #49's row. 0006 does not, because #40 predates EAS #42, which moved the EnvoyProxy task from `install.yaml` to `controller.yaml` |
| `ansible-lint` 26.8.0 (the version EAS CI pins in `.github/workflows/ansible-lint.yml`) with EAS CI's exact flags (`--skip-list name,var-naming,fqcn,yaml,jinja,key-order roles/ playbooks/`) on the patched tree | `Passed: 0 failure(s), 0 warning(s)`, profile `production`, same as unpatched `main` (on `e8f3ebc`, 2026-10-19) |
| `yamllint -d relaxed` on every YAML file the patches touch | no findings apart from line length; EAS CI skips the `yaml` rule |
| 0002 behaviour, `helm template` of EAS `charts/istio` (base/istiod/cni/ztunnel 1.27.3) | `global.platform=k3d` mounts `/bin` + `/var/lib/rancher/k3s/agent/etc/cni/net.d`; unset keeps `/opt/cni/bin` + `/etc/cni/net.d`; unknown value fails `unknown platform`. The Jinja `values:` expression evaluates to `{}` when empty |
| 0003 behaviour | `advanced-balloon-values.yaml.j2`, rendered with sample inputs, goes through `helm template` for chart v0.14.0; the `BalloonsPolicy` validates against the v0.14.0 CRD schema; the image resolves to `ghcr.io/containers/nri-plugins/nri-resource-policy-balloons:v0.14.0` |
| 0004 behaviour, [checks/eas-byo-topology.sh](checks/eas-byo-topology.sh) (stub `kubernetes.core`) | upstream: BYO 3 nodes → no labels (FAIL); patched: server `platform`, agents `inference`. BYO with one node already labelled → that node untouched; BYO with node list or patch denied → install succeeds, no labels; managed with patch denied → still fails. BYO 1 node, topology off, managed single- and multi-node all unchanged |
| 0006 behaviour | the patched `EnvoyProxy` validates against the v1alpha1 CRD in gateway-helm v1.7.1, which includes `pod.affinity` and does not mark `nodeSelector` nullable |
| 0006 re-install, [checks/eas-envoy-reinstall.py](checks/eas-envoy-reinstall.py) | models kubernetes.core 6.3.0 updating a custom resource (JSON merge patch, RFC 7386) over the object the unpatched task created. Without `nodeSelector: null` the old selector survives next to the affinity (FAIL); with it, gone. Fresh install: the null is pruned on create |

How to rerun the checks (an EAS clone; `ansible-core` and `jmespath` for the topology check; PyYAML for the envoy check):

```bash
integrations/eat/upstream/checks/apply-check.sh ../enterprise-ai-solutions
integrations/eat/upstream/checks/eas-byo-topology.sh <EAS checkout with 0004 applied>
integrations/eat/upstream/checks/eas-envoy-reinstall.py <unpatched EAS checkout> <EAS checkout with 0006 applied>
```

## Facts checked against primary sources

- **Istio platform profiles.** Taken from the cni 1.27.3 chart (`files/profile-platform-*.yaml`, `templates/zzz_profile.yaml`). k3d: conf `/var/lib/rancher/k3s/agent/etc/cni/net.d`, bin `/bin`. k3s: same conf, bin `/var/lib/rancher/k3s/data/cni`. microk8s: both under `/var/snap/microk8s`. minikube: only `cniNetnsDir`. gke: blanks `cniBinDir` for autodetection. openshift: conf `/etc/cni/multus/net.d`, bin `/var/lib/cni/bin`, multus, not chained. Also checked against Istio's [platform prerequisites](https://istio.io/latest/docs/ambient/install/platform-prerequisites/). That page also shows `global.platform=k3d` for k3d.
- **k3s control-plane label value `"true"`.** [k3s-io/k3s pkg/server/server.go#L550](https://github.com/k3s-io/k3s/blob/843c32de489c25ccc1bde53d21c5fd7df23c706e/pkg/server/server.go#L550).
- **k3s NRI.** Disabled only when k3s runs in a user namespace: [pkg/agent/templates/templates.go#L322-L325](https://github.com/k3s-io/k3s/blob/843c32de489c25ccc1bde53d21c5fd7df23c706e/pkg/agent/templates/templates.go#L322-L325).
- **nri-plugins.** Latest release `v0.14.0` (2026-08-21) per the GitHub releases API and the Helm repo index. The chart and app versions are `v0.14.0`.
- **k3d v5.9.0 flags used in 0005.** `--image`, `--k3s-arg ARG@NODEFILTER`, `-p ...@loadbalancer`, `--wait` and `kubeconfig get`, checked in k3d's docs at tag v5.9.0. `rancher/k3s:v1.34.12-k3s1` exists on Docker Hub, and its minor version matches EAS's pinned kubectl v1.34.3.

## Live probes (Gate 2)

All six ran on 2026-10-20 against EAS `main` `e8f3ebc` with the five patches, on one host, one throwaway k3d cluster at a time. Results and the three 0005 fixes they forced are in [docs/intel/upstream-eat.md, Gate 2](../../../docs/intel/upstream-eat.md#test-plan). In short: stock `rancher/k3s` needs `istio_platform: k3s` (its containerd searches only `/var/lib/rancher/k3s/data/cni`), the k3d-gpu node image needs `k3d` (`/usr/bin`), and 0004, 0006 and the NRI coexistence behave as described above. The probes:

1. **0002, CNI binaries.** Which directory does containerd search for CNI binaries? Istio's k3d profile writes to `/bin`, its k3s profile to `/var/lib/rancher/k3s/data/cni`.
   `docker exec k3d-eas-server-0 sh -c 'grep -n -A3 cni /var/lib/rancher/k3s/agent/etc/containerd/config.toml'`
2. **0002, ambient chaining.** After install, does `istio-cni` chain into flannel's conflist?
   `docker exec k3d-eas-server-0 ls /var/lib/rancher/k3s/agent/etc/cni/net.d`
3. **0006.** Is the envoy DaemonSet scheduled on the k3s server?
   `kubectl -n envoy-gateway-system get ds -o wide`
   Then the re-install case: install with the unpatched role, re-run install with 0006, and confirm the old selector is gone (expect no output):
   `kubectl -n envoy-gateway-system get envoyproxy eg-proxy-config -o yaml | grep -A2 nodeSelector`
4. **0004.** On a multi-node k3d cluster, run `install` then `teardown infrastructure`. The labels should appear, then disappear.
   `kubectl get nodes -L workload-class`
   Also: label one agent `workload-class=gpu` by hand before `install` (it should keep `gpu`), and install with a kubeconfig whose Role lacks node `patch` (install should finish, with the label tasks reported as ignored).
5. **0005 end to end.** Follow the page exactly, then send a chat completion through the gateway on `https://<base_domain_name>`.
6. **NRI coexistence.** With k3d-gpu's own `nri-resource-policy-balloons` DaemonSet present, confirm that `kubernetes_cpu_policy: best-effort` keeps EAS from installing a second policy.
   `kubectl -n kube-system get ds -l app.kubernetes.io/name=nri-resource-policy-balloons`

## Related upstream observation (no patch)

intel/enterprise-ai-solutions#40 adds an `intel_gpu_plugin` role with `intel_gpu_plugin_ref: "main"`. It runs `kubectl apply -f` on the upstream manifest from that branch, and the manifest has no namespace. The issue draft raises this as a question, not a patch, because the PR was open when it was drafted. #40 was closed unmerged on 2026-10-22, and EAS `main` `5767995` has no `intel_gpu_plugin` role, so that question (question 2) is moot: drop it before the issue is filed.
