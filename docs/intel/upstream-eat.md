# Upstream contribution: k3d/k3s single-node path for Intel's agent toolkit

> **DRAFT: not submitted; awaiting owner approval.**
> Nothing here has been filed, opened, forked or pushed to any `intel/*` repository. A maintainer of this repo sends each piece by hand, in the order below, after approving it.

Drafted 2026-10-17. Heads re-read on 2026-10-19 with `gh api` and `git ls-remote`: EAT is unchanged and #19 is still a draft PR; EAS `main` moved from `7c02ca2` to `e8f3ebc`. Re-read on 2026-10-20: no head in the table moved. EAT #21 (Release 1.1.0) opened on 2026-10-19; see [its impact](#release-110-pr-21-impact). Re-read on 2026-10-28: #21 merged on 2026-10-22 and shipped as `release-1.1.0` (`0c726f5`). #19 merged on 2026-10-27 into the `release-2.0.0` branch, not `main`, at final head `fb59459` (merge `036b662`); no tag carries it. The EAT docs patch below now needs a re-base ([see below](#eat-patch-full-content)). EAS `main` moved from `e8f3ebc` to `890e66e`, where every EAS patch and the series still pass `apply-check.sh`. EAS #40 was closed unmerged on 2026-10-22, a minute after EAS #66 ("Integrate Agentic Toolkit", open) was opened. enterprise-inference `main` moved from `aaca1e5` to `3e8a244`; Gate 2 has not been re-run on it. Re-read on 2026-10-29: EAS #66 merged into `main` (`5767995`), where every EAS patch and the series still pass `apply-check.sh`. Its `configs/repos/repos.agent-toolkit.yaml` clones EAT at the `agentic-toolkit-migration` branch, not a tag, and it adds no `intel_gpu_plugin` role.

| Repo | Ref | SHA |
|---|---|---|
| intel/enterprise-agent-toolkit (EAT) | PR #19 head, "Migration Agentic Toolkit into Unified Convention" | `9d842f5c3b88ad2ec6ea5f7d66a003718bdac828` |
| intel/enterprise-agent-toolkit (EAT) | `main` | `0ecf67a8331e8b502ef41e08bf6d2881416178c5` |
| intel/enterprise-ai-solutions (EAS) | `main` | `e8f3ebc927a7659081f6604d157591bddfca40a9` |
| intel/enterprise-inference | `main`, cloned by `init inference` in Gate 2 | `aaca1e505a8f7947cd455d2dea5f433213a95cad` |

## Where the change belongs

The deploy path belongs in EAS, not EAT. EAT receives a docs pointer only.

- EAT PR #19 deletes EAT's own installer and cluster layer: `deploy-agentic-stack.sh`, `core/lib/**`, `core/playbooks/**` and `core/roles/**`, 173 files in all (read at its draft head `9d842f5`). It merged on 2026-10-27 into `release-2.0.0`, not `main`; no tag carries it yet.
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
  - Gate 2 probe 6: a full `install inference` on a default k3d-gpu cluster left exactly one `nri-resource-policy-balloons` DaemonSet, k3d-gpu's own, and listed `nri_cpu_balloons` as skipped. One run on one host.
  - The k3d-gpu node image needs `istio_platform: k3d`, not `k3s` (Gate 2 probe 1).
- **`K3D_GPU_BALLOONS=external` cluster**: EAS's own `nri-balloons` policy can own the node.
  - H14 proved this mode with the nri-plugins chart v0.14.0 and EAT's values [ledger:H14]. It did not exercise EAS's `nri_cpu_balloons` role.

## Release 1.1.0 (PR #21) impact

[EAT #21](https://github.com/intel/enterprise-agent-toolkit/pull/21) (head `c86c42b`) merged
on 2026-10-22 and shipped as `release-1.1.0` (`0c726f5`, the same tree). It keeps the old
layout, so it does not contain #19 and changes nothing above: the deploy path still belongs
in EAS, and the EAT docs patch still targets #19. What it changes for a k3d or k3d-gpu
cluster, read from source only:

| #21 adds | Already covered by k3d-gpu | Expected on a k3d-gpu cluster |
|---|---|---|
| Arc B60/B70 XPU path (`compute_platform=xpu`, `vllm-openai-xpu:v0.28.0`, TP4 `gemma-4-31B-it`) | B70 detection, `gpu.intel.com/xe` asserted by `up`, vLLM XPU `v0.32.0` by digest through `agent up` | Host Level Zero and OpenCL libraries mounted by `hostPath` resolve inside the k3d node container, not on the host |
| Intel GPU plugin from intel-device-plugins `main` (`intel_gpu_plugin_ref`, unpinned) | Plugin `0.37.1` baked into the node image | Two GPU plugin DaemonSets on one node |
| NUMA-aware NRI balloons, one `BalloonsPolicy` per node | Baked balloons for hybrid P/E and multi-NUMA hosts; `K3D_GPU_BALLOONS=external` [ledger:H14] | Two policies if `deploy_nri_balloon_policy=yes`; `eat-up.sh` sets `off` |
| Kata sandboxes (`kata-fc`, `kata-qemu`) | `doctor --perf` reports `/dev/kvm` on the host | Kata needs `/dev/kvm` inside the k3d node, which `up` does not pass through |
| Qwen3Guard guardrails, on by default; vLLM CPU `v0.28.0`; OVMS as a Docker engine; NVIDIA B300 profile; vLLM Semantic Router | none of these | The guardrail deploys on a sourced brownfield run unless `DEPLOY_GUARDRAILS=off` ([platforms.md](../platforms.md#intel-enterprise-agent-toolkit-eat)) |

Every cell in the last column is an expectation. They are being checked on one host with
4x Arc Pro B70; no result is in yet, and nothing here is sent upstream until they are.

## File tree

```text
EAT (after #19 merges; one PR, docs only)
├── README.md            +1 row in "Choose how to run it"
└── docs/README.md       +1 row in the "Deploy" table

EAS (main e8f3ebc; one concern per PR, in this order)
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

The EAS patch files, in full, are in this repo. Each was checked with `git apply --check` on a read-only clone (no fork), one patch at a time, then the five applied in order:

| Patch | File | EAS `main` e8f3ebc | EAS #40 head bbb3a53 |
|---|---|---|---|
| 0006 | [0006-eas-envoy-control-plane-affinity.patch](../../integrations/eat/upstream/patches/0006-eas-envoy-control-plane-affinity.patch) | applies. Regenerated 2026-10-17 against `roles/envoy_gateway/tasks/controller.yaml` (blob `cf1824b`), where EAS #42 (88fd2ae) moved the EnvoyProxy task; the previous `install.yaml` patch fails there | does not apply: #40 predates #42 and still has the task in `install.yaml`; it has to rebase onto `main` |
| 0002 | [0002-eas-istio-global-platform.patch](../../integrations/eat/upstream/patches/0002-eas-istio-global-platform.patch) | applies | applies |
| 0004 | [0004-eas-byo-node-topology-labels.patch](../../integrations/eat/upstream/patches/0004-eas-byo-node-topology-labels.patch) | applies | applies |
| 0003 | [0003-eas-pin-nri-balloons-chart.patch](../../integrations/eat/upstream/patches/0003-eas-pin-nri-balloons-chart.patch) | applies | applies |
| 0005 | [0005-eas-docs-single-node-k3d.patch](../../integrations/eat/upstream/patches/0005-eas-docs-single-node-k3d.patch) | applies after its 2026-10-19 regeneration (Gate 1) and the two Gate 2 fixes; rewrite to reviewer norms first (see below) | does not apply: its `docs/README.md` context now includes EAS #49's OpenShift row, which #40 predates |
| series 0002-0006 | all five in order | applies | n/a |
| EAT docs patch | the diff below | n/a (EAS only) | n/a |

The EAT docs patch below applies on EAT PR #19's draft head `9d842f5` and does not apply on EAT `main` `0ecf67a`, as intended: it edits the docs tables #19 adds. It does not apply on #19's final head `fb59459` or on its merge into `release-2.0.0` (`036b662`) either, so it needs a re-base onto `release-2.0.0` before it is sent. It is still unsent.

Gate 1 below has the static results on `e8f3ebc`, `ansible-lint` included.

## EAT patch (full content)

Base: PR #19 head `9d842f5`. It was checked with `patch -p1 --dry-run` on copies of both files from that commit, and both hunks apply. `git apply --check` on the same two files passed again on 2026-10-18. On 2026-10-28, on a read-only clone, it still passes on `9d842f5` and fails on `fb59459` and `036b662` (`patch failed: README.md:144` and `patch failed: docs/README.md:37`).

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

**Gate 0. A pre-registered end-to-end run passes before anything is sent.**

**Status: passed on one host by H1b, SUPPORTED [ledger:H1b].** H1 stays INCONCLUSIVE [ledger:H1]; its wait cannot pass on k3s v1.37. The doc stays a DRAFT; nothing is sent until the owner approves it. The issue and PRs cite one i9-13900T run, n=1, not a support statement for client CPUs.

Run 2 (2026-10-17, [EXPERIMENTS.md, H1 run 2](../../EXPERIMENTS.md#h1-run-2-i9-13900t-2026-10-17)) fixed run 1's deviations. It ran on one fresh cluster under a matched background load, each step once and timed:

| Step | Wall (s) | Result |
|---|---|---|
| `k3d-gpu up` | 48.8 | rc 0 |
| `eat-up.sh` | 516.7 | rc 0, 5 PLAY RECAPs `failed=0` |
| `eat-up.sh model cpu-qwen3-1-7b` | 157.7 | rc 0 |
| `kubectl wait --for=condition=Ready pod --all -A --timeout=30m` | 383.0 | cannot exit 0; stopped once only Succeeded Job pods were left |
| one chat completion through LiteLLM | 12.8 | HTTP 200, `finish_reason` stop |
| `eat-check.sh` | 28.3 | passed |

Every pod that can be Ready was Ready, vLLM included. The wait still cannot exit 0. k3s v1.37 runs its own `helm-install-gateway-api-crd` Job, and a Succeeded pod never turns Ready. So the probe as written cannot pass on any k3s v1.37 cluster. The fault is in the probe, not in EAT.

H1b ([EXPERIMENTS.md, H1b run](../../EXPERIMENTS.md#h1b-run-i9-13900t-2026-10-17)) is a new row with H1's hypothesis and criteria and one change: the wait skips Succeeded pods. It was pushed before the run, then run once on one fresh cluster under the same matched load:

| Step | Wall (s) | Result |
|---|---|---|
| `k3d-gpu up` | 48.9 | rc 0 |
| `eat-up.sh` | 494.6 | rc 0, 5 PLAY RECAPs `failed=0` |
| `eat-up.sh model cpu-qwen3-1-7b` | 154.9 | rc 0 |
| `kubectl wait --for=condition=Ready pod -A --field-selector=status.phase!=Succeeded --timeout=30m` | 280.1 | rc 0, 33 pods Ready |
| every Job | 0.1 | `helm-install-gateway-api-crd` and `register-qwen-qwen3-1-7b` `Complete` |
| one chat completion through LiteLLM | 12.6 | HTTP 200, `finish_reason` stop |
| `eat-check.sh` | 28.4 | passed |

```bash
# Fresh cluster, one uninterrupted pass (EXPERIMENTS.md, H1b run has the matched-load version)
IMAGE=k3d-gpu:eat-$(git rev-parse --short HEAD)
docker build -q -t "$IMAGE" .
K3D_GPU_IMAGE="$IMAGE" \
K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true \
K3D_GPU_K3S_ARGS=--disable=servicelb scripts/k3d-gpu up
EAT_WORK=$PWD/eat-work integrations/eat/eat-up.sh
EAT_WORK=$PWD/eat-work integrations/eat/eat-up.sh model cpu-qwen3-1-7b
kubectl wait --for=condition=Ready pod -A --field-selector=status.phase!=Succeeded --timeout=30m
kubectl get jobs -A   # every Job: COMPLETIONS 1/1
EAT_WORK=$PWD/eat-work integrations/eat/eat-check.sh   # expect: ==> EAT check passed.
scripts/k3d-gpu down
```

**Gate 1. Static checks.** **Status: passed on EAS `main` `e8f3ebc` (2026-10-19), after 0005 was regenerated.** Run from a read-only clone; every command below, with the tools in a fresh venv (ansible-core 2.21.5, ansible-lint 26.8.0, shellcheck 0.11.0):

| Check | EAS `main` `e8f3ebc` |
|---|---|
| `apply-check.sh`, each patch alone | 0002, 0003, 0004, 0006 apply. 0005 as drafted failed on `docs/README.md`: EAS #49 (`e8f3ebc`) added a "Deploy on OpenShift" row under the hunk's context. 0005 was regenerated: same k3d row, still right after "Multi-Node & BYO Cluster", context now includes the OpenShift row. Then all five apply, and still do after the Gate 2 fixes to `docs/deploy/k3d.md` |
| `apply-check.sh`, series with `git am` | applies |
| `eas-byo-topology.sh`, tree with 0004 | 11 of 11 cases ok. Unpatched `main` fails "BYO 3 nodes" and "BYO 3 nodes, one hand label", as expected |
| `eas-envoy-reinstall.py`, unpatched to 0006 | fresh install and re-install both ok. Unpatched to unpatched fails both, as expected |
| `ansible-lint` with EAS CI's flags | `Passed: 0 failure(s), 0 warning(s)`, profile `production`, on unpatched `main` and on the tree with all five patches |
| `shellcheck --severity=error es_auto_installer.sh` | clean, unpatched and patched |
| `yamllint -d relaxed`, every YAML file the patches touch | nothing apart from line length |

The only EAS commit since `7c02ca2` is #49, "detect the cluster platform (OpenShift or Kubernetes)". It adds a preflight that probes a BYO cluster once (`playbooks/includes/preflight_platform.yaml`). A k3s cluster serves no `config.openshift.io/v1`, so it reads as `kubernetes`; no patch touches that file.

```bash
integrations/eat/upstream/checks/apply-check.sh <EAS clone at main>        # every patch alone, then the series
integrations/eat/upstream/checks/eas-byo-topology.sh <EAS with 0004>
integrations/eat/upstream/checks/eas-envoy-reinstall.py <EAS unpatched> <EAS with 0006>
pip install ansible-lint==26.8.0
ansible-lint --skip-list name,var-naming,fqcn,yaml,jinja,key-order roles/ playbooks/   # EAS CI flags
shellcheck --severity=error es_auto_installer.sh
```

**Gate 2. Live probes on one throwaway k3d cluster.** **Status: all six ran on 2026-10-20 (UTC) on one i9-13900T host. Probes 1 and 2 found a defect in 0005, now fixed; probe 5 needed one deviation.** EAS `main` `e8f3ebc` with all five patches applied, from an Ubuntu 24.04 bastion container on the host network (`configure` supports Ubuntu and RHEL only; this host runs Manjaro). Each run took the shared live lock, used one cluster named `k3dgpu-gate2`, and deleted it afterwards.

| # | Probe | Cluster | Result |
|---|---|---|---|
| 1 | containerd's CNI `bin_dirs` | stock `rancher/k3s:v1.34.12-k3s1` | `["/var/lib/rancher/k3s/data/cni"]` only; `/bin` also holds the plugins, but containerd does not search it. Istio's **k3s** profile matches, its k3d profile does not |
| 1 | same | default k3d-gpu cluster (k3s v1.37.1, Ubuntu 26.04 node image) | `["/usr/bin"]`, and `/bin` is a symlink to `usr/bin`. Here the **k3d** profile matches and the k3s profile does not |
| 2 | `istio-cni` chained into flannel's conflist | stock k3s, `istio_platform: k3d` (0005 as drafted) | Chained: `10-flannel.conflist` lists `flannel`, `portmap`, `bandwidth`, `istio-cni`. But the binary landed in `/bin`, so every new pod in an ambient namespace failed `failed to find plugin "istio-cni" in path [/var/lib/rancher/k3s/data/cni]`, and the install stalled at the envoy-gateway certgen Job. **0005 now says `istio_platform: k3s`** |
| 2 | same | stock k3s, `istio_platform: k3s` | Chained the same way, binary in `/var/lib/rancher/k3s/data/cni/istio-cni`, `istio-cni-node` and `ztunnel` 1/1. `install inference` rc 0, PLAY RECAP `failed=0`, in 956 s and again in 972 s on a second fresh cluster |
| 2 | same | k3d-gpu cluster: `k3s`, then `k3d` | `k3s`: the same `failed to find plugin` error, path `[/usr/bin]`. `k3d`: binary in `/usr/bin`, `install inference` rc 0, `failed=0`, 863 s |
| 3 | envoy DaemonSet after 0006 | stock k3s | 1/1 Ready on the k3s server, `NODE SELECTOR <none>`, `EnvoyProxy` carries the `Exists` affinity |
| 3 | re-install over the unpatched selector | stock k3s | Merge-patched `nodeSelector: {node-role.kubernetes.io/control-plane: ""}` onto `eg-proxy-config` (what the unpatched task leaves), then `install gateway --only`: rc 0, the `nodeSelector` key gone, DaemonSet still 1/1 |
| 4 | 0004 labels, install then teardown | stock k3s, 1 server + 2 agents, agent-1 hand-labelled `workload-class=gpu` | `install kubernetes --only`: server `platform`, agent-0 `inference`, agent-1 kept `gpu`. `teardown infrastructure`: all three labels removed, the hand-set one included, as the 0004 docs state |
| 4 | kubeconfig without node `patch` | same; ServiceAccount with get/list/watch on everything | `kubectl auth can-i patch nodes`: `no`. `install kubernetes --only`: rc 0, `failed=0 ignored=2`, three 403s logged, no labels |
| 5 | 0005 end to end, then one chat completion | stock k3s | Page followed step by step with the two fixes below. `model-manager deploy qwen3-0-6b --wait`, Keycloak client-credentials token, then `POST https://inference.solutions.ai/llm-inference/qwen3-0-6b/v1/chat/completions` through `127.0.0.1:443`: HTTP 200, 64 completion tokens, `finish_reason` `length` (the `max_tokens` cap). The same call without a token: HTTP 401. **Deviation:** the default catalog sets `require_amx: true`; this host has no AMX, so the model pod stayed Pending until `require_amx: false` was set in `env/local/models.yaml` and the model redeployed |
| 6 | one NRI policy | default k3d-gpu cluster, `kubernetes_cpu_policy: best-effort` | Full `install inference`: exactly one `nri-resource-policy-balloons` DaemonSet, k3d-gpu's (pinned by digest, created before the install); the installer listed `nri_cpu_balloons` under "Skipped (disabled)" |

Teardown: `teardown infrastructure` rc 0, `failed=0`, on both stock-k3s runs, and the node kept only its k3s labels.

Fixes made to 0005 from these probes (the regenerated patch passes Gate 1 again):

- `istio_platform: k3d` became `k3s`, and the "What differs" row now names containerd's `bin_dirs` (probes 1 and 2).
- Step 1 exports `KUBECONFIG`. On BYO the installer writes no `env/local/kubeconfig.yaml`, so `model-manager` fell back to `localhost:8080` and failed (probe 5).
- Step 4 adds `inference.${DOMAIN}` to `/etc/hosts`. The default `auth_type: jwt` serves models there (probe 5).

The k3d-gpu image result does not go upstream, because the upstream page names plain k3d only. It means a default k3d-gpu cluster needs `istio_platform: k3d`, the opposite of stock k3s.

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
- move gap 2 to `roles/envoy_gateway/tasks/controller.yaml#L174-L178` (`e8f3ebc`; #49 did not touch the file);
- replace the "Not yet run end-to-end" block with the Gate 2 results above, one host, n=1.

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

- That the toolkit is supported on any Intel client CPU. The data points are three runs on one i9-13900T: H1 INCONCLUSIVE [ledger:H1] and one SUPPORTED run, n=1 [ledger:H1b].
- Any performance number. None appears in the upstream text.
- Any world-first. [docs/provenance.md](../provenance.md) supports none.

## Related

| If you want to… | Go to |
|---|---|
| See the patch pack and its static checks | [integrations/eat/upstream/README.md](../../integrations/eat/upstream/README.md) |
| See what was measured and how | [technical-report.md](technical-report.md) |
| See EAT on k3d-gpu today | [docs/intel.md](../intel.md) |
