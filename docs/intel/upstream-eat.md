# Upstream contribution: k3d/k3s single-node path for Intel's agent toolkit

> **DRAFT: not submitted; awaiting owner approval.**
> Nothing here has been filed, opened, forked or pushed to any `intel/*` repository. A maintainer of this repo sends each piece by hand, in the order below, after approving it.

Drafted 2026-10-17 against these heads, re-read with `gh api` on 2026-10-17 (unchanged):

| Repo | Ref | SHA |
|---|---|---|
| intel/enterprise-agent-toolkit (EAT) | PR #19 head, "Migration Agentic Toolkit into Unified Convention" | `9d842f5c3b88ad2ec6ea5f7d66a003718bdac828` |
| intel/enterprise-agent-toolkit (EAT) | `main` | `0ecf67a8331e8b502ef41e08bf6d2881416178c5` |
| intel/enterprise-ai-solutions (EAS) | `main` | `7c02ca206cccea6119f380312708eb8b97d146f5` |

## Where the change belongs

The deploy path belongs in EAS, not EAT. EAT receives a docs pointer only.

- EAT PR #19 (open, mergeable, CI green, no review decision yet) deletes EAT's own installer and cluster layer: `deploy-agentic-stack.sh`, `core/lib/**`, `core/playbooks/**` and `core/roles/**`, 173 files in all.
- After #19, EAT ships only `components.yaml`, `config.yaml`, `roles/<component>/` and `charts/`. It installs through `es_auto_installer.sh install agent-toolkit` in EAS.
- EAS already has a bring-your-own-cluster (BYO) path, `existing_kubernetes: <kubeconfig>`, which skips Kubespray. A k3d cluster is a BYO cluster. No new mode is needed, only five small fixes to that path.
- An EAT code patch against today's `main` would edit files #19 deletes. That would waste review time, so this draft has none.

The EAS pieces already exist in this repo as `git format-patch` files under [integrations/eat/upstream/](../../integrations/eat/upstream/README.md). This page packages them with the EAT pointer, the test plan and the PR text.

## How k3d-gpu fits, and how it does not

Upstream will not take a dependency on this repo, and should not. The EAS page shows plain `k3d cluster create`. k3d-gpu appears only as an optional helper, for users who want the parts plain k3d does not do:

| Plain k3d gives | k3d-gpu adds (optional) | Evidence |
|---|---|---|
| A k3s node in Docker | Detection of the host's Intel CPU, GPU, NPU and accelerators, written as node labels (`k3d-gpu doctor`, `k3d-gpu plan`) | fixture tests only, [platforms.md](../platforms.md) |
| No CPU policy | NRI balloons for hybrid P/E or NUMA CPUs, baked into the node, or left to Helm with `K3D_GPU_BALLOONS=external` | install paths [ledger:H14] |
| No device plugins | Intel GPU (shared iGPU), NPU, DSA, IAA, QAT and Gaudi device plugins, chosen per detected device | iGPU sharing [ledger:H15]; other plugins fixture-tested only |
| No sandbox policy | Agent sandboxes on E-cores with default-deny egress (`k3d-gpu sandbox up`) | [ledger:H18] |

Which mode to use with EAS:

- **Default k3d-gpu cluster** (k3d-gpu owns the balloons policy): set `kubernetes_cpu_policy: best-effort` in EAS, so EAS does not install a second NRI policy.
  - Not verified end to end with EAS; it is probe 6 in the test plan.
- **`K3D_GPU_BALLOONS=external` cluster**: EAS's own `nri-balloons` policy can own the node.
  - H14 proved this mode with the nri-plugins chart v0.14.0 and EAT's values [ledger:H14]. It did not exercise EAS's `nri_cpu_balloons` role.

## File tree

```text
EAT (after #19 merges; one PR, docs only)
├── README.md            +1 row in "Choose how to run it"
└── docs/README.md       +1 row in the "Deploy" table

EAS (main 7c02ca2; one concern per PR, in this order)
├── roles/envoy_gateway/tasks/controller.yaml       0006  control-plane affinity, operator Exists
├── configs/defaults/global_config.yaml             0002  istio_platform documented
├── roles/istio/defaults/main.yaml                  0002  istio_platform: ""
├── roles/istio/tasks/install.yaml                  0002  global.platform on install
├── roles/istio/tasks/upgrade.yaml                  0002  global.platform on upgrade
├── roles/kubernetes/tasks/install.yaml             0004  BYO branch applies node topology labels
├── roles/kubernetes/tasks/apply_node_topology.yaml 0004
├── docs/customize/node_topology.md                 0004  "label by hand" text replaced
├── docs/deploy/topologies.md                       0004
├── roles/nri_cpu_balloons/defaults/main.yaml       0003  nri_cpu_balloons_chart_version: "v0.14.0"
├── roles/nri_cpu_balloons/tasks/install_advanced.yaml 0003
├── docs/deploy/k3d.md                              0005  new page "Single-Node Dev on k3d"
└── docs/README.md                                  0005  +1 row under Deploy
```

The EAS patch files, in full, are in this repo. Each was checked with `git apply --check` on a copy of the tree read from the GitHub tarball API (no clone, no fork), one patch at a time, then the five applied in order:

| Patch | File | EAS `main` 7c02ca2 | EAS #40 head bbb3a53 |
|---|---|---|---|
| 0006 | [0006-eas-envoy-control-plane-affinity.patch](../../integrations/eat/upstream/patches/0006-eas-envoy-control-plane-affinity.patch) | applies. Regenerated 2026-10-17 against `roles/envoy_gateway/tasks/controller.yaml` (blob `cf1824b`), where EAS #42 (88fd2ae) moved the EnvoyProxy task; the previous `install.yaml` patch fails there | does not apply: #40 predates #42 and still has the task in `install.yaml`; it has to rebase onto `main` |
| 0002 | [0002-eas-istio-global-platform.patch](../../integrations/eat/upstream/patches/0002-eas-istio-global-platform.patch) | applies | applies |
| 0004 | [0004-eas-byo-node-topology-labels.patch](../../integrations/eat/upstream/patches/0004-eas-byo-node-topology-labels.patch) | applies | applies |
| 0003 | [0003-eas-pin-nri-balloons-chart.patch](../../integrations/eat/upstream/patches/0003-eas-pin-nri-balloons-chart.patch) | applies | applies |
| 0005 | [0005-eas-docs-single-node-k3d.patch](../../integrations/eat/upstream/patches/0005-eas-docs-single-node-k3d.patch) | applies; rewrite to reviewer norms first (see below) | applies |
| series 0002-0006 | all five in order | applies | n/a |
| EAT docs patch | the diff below | n/a (EAS only) | n/a |

The EAT docs patch below applies on EAT PR #19 head `9d842f5` and does not apply on EAT `main` `0ecf67a`, as intended: it edits the docs tables #19 adds.

On the regenerated 0006, `checks/eas-envoy-reinstall.py` (now reading `controller.yaml`) passes: fresh install and re-install over the unpatched object both leave the affinity and no `nodeSelector`. Unpatched `main` fails both, as expected. `yamllint -d relaxed` on the patched `controller.yaml` reports nothing. `ansible-lint` was not re-run on this tree.

## EAT patch (full content)

Base: PR #19 head `9d842f5`. It was checked with `patch -p1 --dry-run` on copies of both files from that commit, and both hunks apply.

It links to the EAS page from 0005, the same way #19's docs index already links the EAS rack-scale guide. **Send it only after EAS merges 0005**; until then the link is a 404.

```diff
--- a/README.md
+++ b/README.md
@@ -144,6 +144,7 @@
 | **Single node** | First run on one host, or a small pilot | [Single-node quickstart](docs/getting-started/quickstart.md) |
 | **Multi-node** | Several hosts: one control-plane node plus worker nodes for extra model pods | [Multi-node](docs/deployment/multi-node.md) |
 | **Docker Compose** | Containers on a single Linux host with `docker/deploy.sh` (not installed by `es_auto_installer.sh`) | [Docker](docs/deployment/docker.md) |
+| **Single node on k3d (dev)** | A developer machine or CI runner, through the AI Solutions bring-your-own-cluster path on k3d (k3s in Docker); not sized for production | [Single-Node Dev on k3d](https://github.com/intel/enterprise-ai-solutions/blob/main/docs/deploy/k3d.md) |
 
 ---
 
--- a/docs/README.md
+++ b/docs/README.md
@@ -37,6 +37,7 @@
 |---|---|
 | [Deploy on multiple nodes](deployment/multi-node.md) | One control plane plus worker nodes, including the inventory YAML Kubespray uses |
 | [Docker Compose](deployment/docker.md) | Containers on a single Linux host with `docker/deploy.sh` (not installed by `es_auto_installer.sh`) |
+| [Single-Node Dev on k3d](https://github.com/intel/enterprise-ai-solutions/blob/main/docs/deploy/k3d.md) | The AI Solutions bring-your-own-cluster path on one k3d (k3s in Docker) cluster, for development and CI (AI Solutions guide) |
 | [Rack-scale AI fabric](https://github.com/intel/enterprise-ai-solutions/blob/main/docs/rack-scale-deployment.md) | High-speed fabric and NVMe Ceph (AI Solutions guide) |
 | [Decommission and redeploy](deployment/decommission.md) | How to tear the toolkit (or the whole install) down and install again |
 | [Corporate proxy](deployment/proxy.md) | Setting `http_proxy`, `https_proxy`, and `no_proxy` so image pulls and model downloads work |
```

Several relative links in #19's own tables (for example `docs/getting-started/quickstart.md`) point at pages that do not exist in the #19 tree. That is upstream's in-flight restructure, and this patch neither adds nor fixes such links.

## Changes to 0005 before sending (reviewer norms)

These come from amberjain1's review comments on EAT PR #20:

- Asked "where is this hardcoded?": every value on the page must name the config key it comes from. 0005's "What differs on k3d" table already does this.
- Asked not to add a time dimension: no wall-clock times anywhere. 0005 has none; keep it that way.
- Asked what separates two docs and who their audience is: one audience (developer and pilot). Do not split it into a second page.
- Asked where node-label annotations go: add one line saying a single-server k3d cluster needs no topology labels, and that multi-node labels come from 0004.

Two content changes:

- Keep `k3d cluster create` as the only required tool. Do not name k3d-gpu in the upstream page. If a maintainer asks for CPU pinning or device plugins on k3d, answer in the issue thread and link [README.md](../../README.md) as an optional helper.
- Change the last bullet of "Limits" from "throughput matches the host, not a Xeon server" to "This topology is for development and pilots. It is not sized for production, and the hardware minimums in Prerequisites still apply." This drops the throughput wording.

## Test plan

**Gate 0. Re-run the H1 probe exactly as pre-registered before sending anything.**

Until this passes, the issue and PRs say "statically verified" only. H1 is INCONCLUSIVE today: the model step ran twice on one cluster, `kubectl wait --all -A` was not recorded, and the host was shared [ledger:H1].

```bash
# Fresh cluster, one uninterrupted pass (EXPERIMENTS.md, H1 run, reproduce block)
IMAGE=k3d-gpu:eat-$(git rev-parse --short HEAD)
docker build -q -t "$IMAGE" .
K3D_GPU_IMAGE="$IMAGE" \
K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true \
K3D_GPU_K3S_ARGS=--disable=servicelb scripts/k3d-gpu up
EAT_WORK=$PWD/eat-work integrations/eat/eat-up.sh
EAT_WORK=$PWD/eat-work integrations/eat/eat-up.sh model cpu-qwen3-1-7b
kubectl wait --for=condition=Ready pod --all -A --timeout=30m
EAT_WORK=$PWD/eat-work integrations/eat/eat-check.sh   # expect: ==> EAT check passed.
scripts/k3d-gpu down
```

**Gate 1. Static checks.** `git apply --check` (each patch, then the series) and the envoy re-install check pass on EAS `main` 7c02ca2 with the regenerated 0006 (table above). Re-run `ansible-lint` and the topology check before sending.

```bash
integrations/eat/upstream/checks/apply-check.sh <EAS clone at main>        # every patch alone, then the series
integrations/eat/upstream/checks/eas-byo-topology.sh <EAS with 0004>
integrations/eat/upstream/checks/eas-envoy-reinstall.py <EAS unpatched> <EAS with 0006>
pip install ansible-lint==26.8.0
ansible-lint --skip-list name,var-naming,fqcn,yaml,jinja,key-order roles/ playbooks/   # EAS CI flags
shellcheck --severity=error es_auto_installer.sh
```

**Gate 2. Live probes on one throwaway k3d cluster.** None has run yet. They come from the "Not verified" list in `integrations/eat/upstream/README.md`.

1. containerd CNI bin dir: is it `/bin` (Istio's k3d profile) or `/var/lib/rancher/k3s/data/cni` (its k3s profile)?
2. Does `istio-cni` chain into flannel's conflist in `/var/lib/rancher/k3s/agent/etc/cni/net.d`?
3. Does `kubectl -n envoy-gateway-system get ds -o wide` show a scheduled envoy pod after the regenerated 0006? Is the old `nodeSelector` gone after a re-install?
4. 0004: do `install` then `teardown infrastructure` add and then remove the `workload-class` labels? Is a hand-set label kept? Does a kubeconfig without node `patch` RBAC let the install finish?
5. 0005 end to end: follow the page exactly, then send one chat completion through the gateway.
6. NRI coexistence: on a default k3d-gpu cluster with `kubernetes_cpu_policy: best-effort`, is exactly one `nri-resource-policy-balloons` DaemonSet present?

Each EAS PR body carries the testing block that EAT PR #20 uses: install, validate, re-install with no changes, teardown, teardown again, and lint results.

## DCO

EAS `CONTRIBUTING.md` requires a DCO sign-off (`git commit -s`) under the sender's real name. EAT's `CONTRIBUTING.md` was removed in acacfcd; the original also asked for a DCO sign-off.

The patches in this repo carry no `Signed-off-by:`. The certification has to come from the person who sends them, so they sign at apply time:

```bash
git am --signoff integrations/eat/upstream/patches/0002-eas-istio-global-platform.patch
git commit -s    # for the EAT docs patch
```

This repo's git policy (no assistant attribution lines) does not conflict with a human `Signed-off-by`.

## PR text

### EAS issue (send first)

The source is [issue-k3s-byo.md](../../integrations/eat/upstream/issue-k3s-byo.md). Before filing:

- repoint every permalink from `7743bac` to the current EAS `main` SHA;
- move gap 2 to `roles/envoy_gateway/tasks/controller.yaml#L174-L178` (7c02ca2).

### EAS PR (one per patch; example for 0006)

Title:

```text
envoy_gateway: select control-plane nodes by label presence so the data plane schedules on k3s
```

Body:

```markdown
The EnvoyProxy DaemonSet selects `node-role.kubernetes.io/control-plane: ""`.
k3s labels its servers `node-role.kubernetes.io/control-plane=true`, so on a k3s or
k3d BYO cluster the DaemonSet schedules zero pods and the gateway has no data plane.

This switches to a required node affinity with `operator: Exists`, keeps the
toleration, and sets `nodeSelector: null` so a re-install over an existing
EnvoyProxy drops the old selector (merge-patch semantics). No change on
kubeadm or Kubespray, where the label value is "".

Refs #<issue>.

### Testing
- install / validate / re-install (no change) / teardown / teardown again on a
  single-node k3d BYO cluster: <results>
- re-install over the unpatched EnvoyProxy: old nodeSelector removed
- ansible-lint 26.8.0 with CI flags: <result>
```

### EAT PR (after #19 merges, and only if invited in the issue thread)

Title:

```text
docs: link the single-node k3d (BYO) guide from the deployment options
```

Body:

```markdown
Adds one row to "Choose how to run it" in README.md and one row to the Deploy
table in docs/README.md. Both point to the AI Solutions guide
docs/deploy/k3d.md, which covers running the toolkit through the
bring-your-own-cluster path (`existing_kubernetes`) on a single k3d cluster.

Docs only; no code or chart change. The row says "dev", not production, and
does not change the hardware prerequisites.

Refs intel/enterprise-ai-solutions#<issue>.
```

## What this draft does not claim

- That the toolkit is supported on any Intel client CPU. The only data point is one i9-13900T run, INCONCLUSIVE [ledger:H1].
- Any performance number. None appears in the upstream text.
- Any world-first. [docs/provenance.md](../provenance.md) supports none.

## Related

| If you want to… | Go to |
|---|---|
| See the patch pack and its static checks | [integrations/eat/upstream/README.md](../../integrations/eat/upstream/README.md) |
| See what was measured and how | [technical-report.md](technical-report.md) |
| See EAT on k3d-gpu today | [docs/intel.md](../intel.md) |
