<!--
Draft only. Not filed. Target repo: intel/enterprise-ai-solutions (EAS), which
owns the platform roles after intel/enterprise-agent-toolkit#19 (open as of
2026-10-16) moves the agent toolkit onto es_auto_installer.sh. Cross-post a
short pointer to intel/enterprise-agent-toolkit only if #19 stalls.
Every link below is a permalink to the default branch as of 2026-10-16:
  EAS intel/enterprise-ai-solutions@7743bac7e3d334da6eda602db10f7eec6b0360c5
  EAT intel/enterprise-agent-toolkit@0ecf67a8331e8b502ef41e08bf6d2881416178c5
-->

# Support k3s/k3d as a BYO single-node target

## Summary

The bring-your-own path (`existing_kubernetes: <kubeconfig>`) gets close to working on a [k3s](https://k3s.io) cluster, including k3d (k3s in Docker), which many developers run on a laptop or CI runner. Reading the code at current `main`, we found four small gaps and one docs gap. Each has a self-contained fix of 1 to 40 lines, and none changes behaviour on a Kubespray cluster.

Would you accept these as separate PRs? We have them ready and are happy to adjust them to your conventions. Nothing here asks to support k3s in production, or to change the Xeon-first defaults.

## Why k3s/k3d

- **Lower bar to try the stack.** A contributor can bring the platform up on a workstation in one `k3d cluster create`, with no spare server or Kubespray run. Issue intel/enterprise-agent-toolkit#2 asks for a "fast path to deploy and run the stack".
- **Same code path you already ship.** BYO mode already skips every Kubespray phase. The changes below make that path hold on a common certified distribution. They add no new mode.
- **Precedent.** intel/enterprise-agent-toolkit#4 (merged) dropped a `crictl` pre-pull because "not all environments have crictl available in $PATH (e.g., k3s, microk8s ...)".

## What already works on k3s (no change needed)

| Area | Why it works | Code |
|---|---|---|
| Version gate | `serverVersion.minor` is stripped to digits, so `v1.34.12+k3s1` passes the minimum-version check | [roles/kubernetes/tasks/validate.yaml#L60-L72](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/roles/kubernetes/tasks/validate.yaml#L60-L72) |
| BYO kubeconfig | `_resolve_kubeconfig` reads `existing_kubernetes`, then exports `KUBECONFIG` and `K8S_AUTH_KUBECONFIG` for every role | [es_auto_installer.sh#L1326-L1344](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/es_auto_installer.sh#L1326-L1344), [#L1446-L1452](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/es_auto_installer.sh#L1446-L1452) |
| Storage | `storage_backend: local-path` installs no provisioner, and components provision from the default StorageClass; k3s ships `local-path` as its default | [roles/storage/defaults/main.yaml#L31-L34](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/roles/storage/defaults/main.yaml#L31-L34), [configs/defaults/global_config.yaml#L23-L26](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/configs/defaults/global_config.yaml#L23-L26) |
| Load balancer | `metallb_enabled: false` leaves `LoadBalancer` Services to k3s's built-in ServiceLB | [configs/defaults/global_config.yaml#L125](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/configs/defaults/global_config.yaml#L125) |
| CPU policy | `best-effort` is an accepted `kubernetes_cpu_policy` and turns NRI off, for nodes where NRI is unavailable or already owned | [playbooks/includes/preflight.yaml#L201-L259](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/playbooks/includes/preflight.yaml#L201-L259) |

## Gaps

### 1. Istio CNI mounts the wrong host paths on k3s/k3d

Ambient mode needs istio-cni to write into the cluster's CNI config directory. The istio role installs the vendored umbrella chart (base/istiod/cni/ztunnel 1.27.3) without `global.platform` ([roles/istio/tasks/install.yaml#L75-L88](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/roles/istio/tasks/install.yaml#L75-L88), [charts/istio/values.yaml#L30-L31](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/charts/istio/values.yaml#L30-L31)). The cni chart therefore mounts `/etc/cni/net.d` and `/opt/cni/bin`. k3s keeps its CNI config in `/var/lib/rancher/k3s/agent/etc/cni/net.d`.

- Istio's [platform prerequisites](https://istio.io/latest/docs/ambient/install/platform-prerequisites/) require `global.platform=k3d` or `k3s` there.
- cni 1.27.3 reads `platform` or `global.platform` and merges `files/profile-platform-<name>.yaml` ([manifests/helm-profiles/platform-k3d.yaml @1.27.3](https://github.com/istio/istio/blob/1.27.3/manifests/helm-profiles/platform-k3d.yaml)).

**Fix (patch 0002):** add an `istio_platform` variable (default `""`) and pass it as `global.platform` on install and upgrade. We checked it with `helm template` on the vendored chart:

| `istio_platform` | Result |
|---|---|
| `""` | Renders exactly as today |
| `k3d` | Mounts `/bin` and `/var/lib/rancher/k3s/agent/etc/cni/net.d` |
| unknown value | Chart fails with `unknown platform` |

### 2. Envoy data plane schedules no pods on k3s

The `EnvoyProxy` pins the envoy DaemonSet to control-plane nodes with an exact-value selector, `node-role.kubernetes.io/control-plane: ""` ([roles/envoy_gateway/tasks/install.yaml#L199-L219](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/roles/envoy_gateway/tasks/install.yaml#L199-L219)).

- kubeadm and Kubespray set this label to `""`.
- k3s sets it to `"true"` ([k3s pkg/server/server.go#L550](https://github.com/k3s-io/k3s/blob/843c32de489c25ccc1bde53d21c5fd7df23c706e/pkg/server/server.go#L550)).
- So on k3s the selector matches no node, and the gateway has no data plane.
- `apply_node_topology.yaml` already tests this label for presence, not value.

**Fix (patch 0006):** a required node affinity with `operator: Exists` on the same key. Nothing changes on kubeadm/Kubespray. The object validates against the `EnvoyProxy` v1alpha1 CRD shipped in gateway-helm v1.7.1.

### 3. BYO clusters never get node-topology labels, but teardown removes them

- With `existing_kubernetes` set, the kubernetes role ends before `post_install` ([roles/kubernetes/tasks/install.yaml#L23-L25](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/roles/kubernetes/tasks/install.yaml#L23-L25)).
- `apply_node_topology.yaml` is also gated on `_k8s_needs_ssh`, which BYO never sets ([#L27-L34](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/roles/kubernetes/tasks/apply_node_topology.yaml#L27-L34)).
- Teardown, however, strips `workload-class` from BYO nodes as "labels we added" ([roles/kubernetes/tasks/teardown.yaml#L10-L39](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/roles/kubernetes/tasks/teardown.yaml#L10-L39)).
- The docs tell users to label by hand ([docs/deploy/topologies.md#L214](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/docs/deploy/topologies.md#L214)).

This is not k3s-specific, but it is the first thing a multi-node k3d user hits.

**Fix (patch 0004):** run `apply_node_topology.yaml` from the BYO branch when `node_topology_enabled` is true, and decide multi-node from the cluster's node count. Single-node stays a no-op. The docs are updated to match. We ran the role on localhost against a stub `kubernetes.core`:

| Case | Labels applied |
|---|---|
| BYO, 1 node | none |
| BYO, 3 nodes | server `platform`, agents `inference` |
| Topology off | none |
| Managed path | unchanged |

### 4. NRI balloons chart is unpinned

The nri-plugins Helm release is installed with no `chart_version` ([roles/nri_cpu_balloons/tasks/install_advanced.yaml#L197-L214](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/roles/nri_cpu_balloons/tasks/install_advanced.yaml#L197-L214)). The chart defaults its image tag to its appVersion, so each run takes whatever release is newest that day, policy binary included. Other roles pin their charts already, for example `seaweedfs_chart_version` and `istio_csr_helm_chart_version`.

This matters more on dev clusters: only one NRI resource policy can own a node, and a version drift between two installers is hard to diagnose.

**Fix (patch 0003):** `nri_cpu_balloons_chart_version: "v0.14.0"`. That is the newest release in the repo index today (published 2026-08-21), so a fresh install does not change now. The role's `advanced-balloon-values.yaml.j2`, rendered with sample inputs, passes `helm template` for v0.14.0, and the resulting `BalloonsPolicy` validates against that chart's CRD.

The same pattern exists in [EAT core/roles/nri_cpu_balloons/tasks/install_nri.yaml#L76-L104](https://github.com/intel/enterprise-agent-toolkit/blob/0ecf67a8331e8b502ef41e08bf6d2881416178c5/core/roles/nri_cpu_balloons/tasks/install_nri.yaml#L76-L104), which #19 removes.

### 5. No docs for a single-node k3d setup

**Fix (patch 0005):** a `docs/deploy/k3d.md` page, linked from `docs/README.md`. It covers:

- cluster create flags (Traefik off, host 80/443 through k3d's load balancer);
- the four `global_config.yaml` settings that differ on k3s;
- `/etc/hosts` on loopback;
- teardown;
- when to choose `best-effort`.

NRI note on that page: k3s turns containerd NRI off only when it runs in a user namespace ([k3s pkg/agent/templates/templates.go#L322-L325](https://github.com/k3s-io/k3s/blob/843c32de489c25ccc1bde53d21c5fd7df23c706e/pkg/agent/templates/templates.go#L322-L325)), so rootful k3d keeps the default `nri-balloons` policy available.

## Enterprise Agent Toolkit (current `main`, before #19)

These are for completeness. Since #19 deletes the files, we would only send a fix if #19 is not going to merge soon.

- **Platform detection.** It accepts only `^vX.Y.Z$` ([core/lib/brownfield/brownfield_deployment.sh#L124](https://github.com/intel/enterprise-agent-toolkit/blob/0ecf67a8331e8b502ef41e08bf6d2881416178c5/core/lib/brownfield/brownfield_deployment.sh#L124), [core/playbooks/setup-bastion.yml#L283](https://github.com/intel/enterprise-agent-toolkit/blob/0ecf67a8331e8b502ef41e08bf6d2881416178c5/core/playbooks/setup-bastion.yml#L283)). A k3s cluster (`v1.34.12+k3s1`) therefore falls through to the interactive platform prompt. Patch 0001 accepts `+k3sN`.
  - `brownfield_deployment()` is sourced ([deploy-agentic-stack.sh#L631](https://github.com/intel/enterprise-agent-toolkit/blob/0ecf67a8331e8b502ef41e08bf6d2881416178c5/deploy-agentic-stack.sh#L631)) but called only from inside itself ([brownfield_deployment.sh#L171-L203](https://github.com/intel/enterprise-agent-toolkit/blob/0ecf67a8331e8b502ef41e08bf6d2881416178c5/core/lib/brownfield/brownfield_deployment.sh#L171-L203)), and the `--menu` offers options 1-3 only ([deploy-agentic-stack.sh#L637-L670](https://github.com/intel/enterprise-agent-toolkit/blob/0ecf67a8331e8b502ef41e08bf6d2881416178c5/deploy-agentic-stack.sh#L637-L670)). The impact is limited to that path.
- **Node label.** vLLM *requires* the label `ei-inference-eligible=true` ([core/helm-charts/vllm/values.yaml#L141-L148](https://github.com/intel/enterprise-agent-toolkit/blob/0ecf67a8331e8b502ef41e08bf6d2881416178c5/core/helm-charts/vllm/values.yaml#L141-L148)). It is applied only on the Kubespray path ([core/lib/components/kubernetes-setup.sh#L15](https://github.com/intel/enterprise-agent-toolkit/blob/0ecf67a8331e8b502ef41e08bf6d2881416178c5/core/lib/components/kubernetes-setup.sh#L15)), so on an existing cluster the vLLM pod stays Pending until someone labels the node.

## Questions for maintainers

1. **NRI ownership on BYO.** Should the installer detect an NRI resource policy that is already running and skip, or fail early, instead of `helm upgrade --install` over it? Only one policy can own a node. Today the only choice is `kubernetes_cpu_policy: best-effort`, while the template comment says not to change it ([configs/defaults/global_config.yaml#L101-L106](https://github.com/intel/enterprise-ai-solutions/blob/7743bac7e3d334da6eda602db10f7eec6b0360c5/configs/defaults/global_config.yaml#L101-L106)).
2. **Intel GPU plugin (enterprise-ai-solutions#40).** The new `intel_gpu_plugin` role applies the upstream manifest from `ref: main` with no namespace. Would a pinned release tag, plus a skip when a GPU plugin DaemonSet already exists, fit? Clusters that already run the plugin, in `kube-system` for example, would otherwise get a second one.
3. **Placement of the docs page.** Is `docs/deploy/` the right home, or would you prefer it folded into `topologies.md`?

## What we did and did not verify

**Verified statically:**
- Every patch applies with `git apply --check` to a fresh clone of its repo's `main`. The EAS patches also apply to the head of enterprise-ai-solutions#40 (bbb3a53).
- The EAS series applies in order with `git am`.
- `ansible-lint` 26.8.0 with this repo's CI flags reports 0 failures on the patched tree (production profile).
- `helm template` and CRD-schema checks pass as described above.
- Role logic was exercised with stub modules.

**Not yet run end-to-end:** a full `./es_auto_installer.sh install inference` on a k3d cluster. The probes we will run before opening PRs:

```bash
# 1. CNI paths: does the rancher/k3s image's containerd use /bin for CNI binaries (the k3d profile)?
docker exec k3d-eas-server-0 sh -c 'grep -n -A3 "cni" /var/lib/rancher/k3s/agent/etc/containerd/config.toml'
# 2. Envoy data plane schedules on a k3s server node with patch 0006
kubectl -n envoy-gateway-system get ds -o wide
# 3. istio-cni chained into flannel's conflist with istio_platform: k3d
docker exec k3d-eas-server-0 ls /var/lib/rancher/k3s/agent/etc/cni/net.d
# 4. The whole BYO install, then a chat completion through the gateway
./es_auto_installer.sh install inference --env local
```
