# Intel's agent toolkit through the EAS installer on k3d-gpu

> **Not verified on a cluster yet.** Nothing in this directory has run against a live
> cluster. Its pre-registered end-to-end test, H61 in [EXPERIMENTS.md](../../EXPERIMENTS.md),
> is PENDING. Until H61 has run, read this as a supported path in code and tests only.

Intel moved its agent toolkit into its unified installer. EAT PR
[#19](https://github.com/intel/enterprise-agent-toolkit/pull/19) (merged 2026-10-27
into `release-2.0.0` as `036b662`) deletes EAT's own `deploy-agentic-stack.sh` and its
`core/` cluster layer. EAT is now the `agent-toolkit` layer of Intel AI for Enterprise
Solutions (EAS). That layer depends on EAS's `platform` layer and on the `inference`
layer from enterprise-inference (EI) (`components.yaml:14-17`). It installs with
`es_auto_installer.sh init agent-toolkit`, then `install agent-toolkit`.

`eas-up.sh` runs that installer on a k3d-gpu cluster through its bring-your-own-cluster
path (`existing_kubernetes`). It does not edit Intel's code, apart from one rebased patch
applied in a throwaway checkout. The settings a k3d node needs are detected and written
into the installer's own config files. `integrations/eat/` (EAT 1.x and its brownfield
path) is unchanged.

## Pins

| What | Ref | Commit |
|---|---|---|
| [intel/enterprise-ai-solutions](https://github.com/intel/enterprise-ai-solutions/releases/tag/v1.2.0-rc.1) (EAS) | tag `v1.2.0-rc.1` (tag object `1d5686e`), a prerelease "for validation. Not for production use" | `5c27fb39a95eecec41aa4e29a1188984e9c726f3` |
| intel/enterprise-inference (EI), `ext/enterprise.ai-inference` | `release-1.1` in the rc's manifest | `170eb3abb1956de0787727f21e356aef2b13ff24` |
| intel/enterprise-agent-toolkit (EAT), `ext/enterprise.agent-toolkit` | `release-2.0.0` in the rc's manifest | `036b66234801e9bd950afd90830821859780d668` |
| bastion base | `ubuntu:24.04` | `sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852` |
| yq, kubectl, helm in the bastion | `v4.53.2`, `v1.34.3`, `v3.20.2`, the installer's own pins (`es_auto_installer.sh:51-53`) | release artifacts by sha256 (`bastion.Dockerfile`) |
| the installer's Python venv | `requirements.txt` has `ansible>=10.0,<13.0` and other ranges | exact versions in `constraints.txt`, passed as `PIP_CONSTRAINT` |
| agent-sandbox router | EAT leaves `latest-main` floating | by digest in `images.yaml` (and `images.lock`) |

The rc's `configs/repos/repos.agent-toolkit.yaml` names EI and EAT by branch, with the
commits only in comments. `eas-up.sh` rewrites both revs to the commits in its checkout.
The installer accepts a commit as a rev (`es_auto_installer.sh:684-688`, `checkout
--detach`). After `init`, `eas-up.sh` checks that each `ext/` repo is at its pin, and
that the EAS checkout is at `5c27fb3`. EAS `main` (`97f6157`) has moved on and clones
EAT's `agentic-toolkit-migration` branch. This path targets the rc.

## Run

Needs docker, git, python3, kubectl and k3d-gpu on the host, and a cluster from
`k3d-gpu up`. Nothing else is installed on the host.

```bash
k3d-gpu up
integrations/eas/eas-up.sh       # preflight, then clone, pin, patch, init, render, install
integrations/eas/eas-check.sh    # the end state, item by item
```

`EAS_WORK` (default `./eas-work`) holds the EAS checkout, `facts.json` and the log.
`EAS_ENV` (default `local`) names the installer's `env/<name>/`. `EAS_KUBECONFIG`
defaults to `$KUBECONFIG`, then to `~/.kube/config`. `eas-up.sh preflight` runs the
checks alone and installs nothing.

The steps of `eas-up.sh`:

1. **Preflight** (`eas-facts.py`, on the host, before anything is cloned). It refuses,
   naming the cause, when:
   - the cluster does not answer;
   - another agent-sandbox controller owns `agent-sandbox-system`;
   - containerd's CNI layout is unknown;
   - a node's pod CIDR is outside the cluster CIDR;
   - the model does not fit.

   It writes `facts.json`.
2. **Checkout.** It clones EAS at the tag, checks the commit, rewrites the two revs and
   applies `patches/0006-envoy-control-plane-affinity-rc1.patch`. A checkout with any
   other edit is refused.
3. **Bastion.** It builds `bastion.Dockerfile` and runs it on the host network as uid
   1000, with the kubeconfig at `/kube/config` and `KUBECONFIG` exported. Gate 2 found
   that `model-manager` falls back to `localhost:8080` without it
   ([upstream-eat.md](../../docs/intel/upstream-eat.md), Gate 2 probe 5).
4. **In the bastion:**
   - `init agent-toolkit --env $EAS_ENV`;
   - the `ext/` commits are checked;
   - `eas-up.sh render` writes the settings below into `env/$EAS_ENV/`;
   - `install agent-toolkit --env $EAS_ENV` runs.

   The output goes to `$EAS_WORK/eas-up-<UTC time>.log`.

`es_auto_installer.sh configure` is not run. Everything it installs on Ubuntu 24.04 is
in the bastion image: python3, python3-venv, yq, kubectl and helm. `install` builds the
installer's venv itself (`ensure_deps`), under `PIP_CONSTRAINT`.
`collections/requirements.yml` pins `kubernetes.core` 6.3.0 exactly, so it needs no
constraint.

## What render writes

`render` is a function of the checkout and `facts.json` alone, with no cluster and no
docker, so CI tests it. `init` seeds `global_config.yaml` once, with the layer
manifest's `auth_provider: "litellm"`, and never overwrites it
(`es_auto_installer.sh:929-990`). So `render` runs after `init` and merges its keys into
that file with yq. It keeps `auth_provider` and Intel's comments, and a second run
changes nothing.

Before it writes, `render` checks each key in the file where its reader defines it. A
key Intel renames or moves stops the run, with the key and file named.

### global_config.yaml

`global_config.yaml` reaches Ansible as extra vars (`-e @$GLOBAL_CONFIG`), so its keys
override role defaults.

| Key | Value | Reader (EAS `5c27fb3`, EI `170eb3a`, EAT `036b662`) | Why |
|---|---|---|---|
| `existing_kubernetes` | `/kube/config` | EAS `roles/kubernetes/defaults/main.yaml:11`; `es_auto_installer.sh` `_resolve_kubeconfig` | The bring-your-own-cluster path: no Kubespray. |
| `metallb_enabled` | `false` | EAS `configs/defaults/global_config.yaml:115` | k3s ServiceLB answers `LoadBalancer` Services on the node. |
| `storage_backend` | `local-path` | EAS `configs/defaults/global_config.yaml:21` | k3s ships the `local-path` provisioner (already the default). |
| `kubernetes_cluster_cidr` | detected (k3s: `10.42.0.0/16`) | EAS `roles/kubernetes/defaults/main.yaml:40` | The default is Kubespray's `10.233.64.0/18`. Every node's `podCIDR` must lie inside; `EAS_CLUSTER_CIDR` overrides. |
| `kata_containers_enabled` | `false` | EAS `configs/defaults/global_config.yaml:120` | Kata needs `/dev/kvm` inside the k3d node, which `k3d-gpu up` does not pass through. |
| `kubernetes_cpu_policy` | `best-effort` | EAS `configs/defaults/global_config.yaml:96`, `playbooks/includes/preflight.yaml:175-233` | k3d-gpu already owns the node's NRI balloons policy. With `best-effort` the installer skips `nri_cpu_balloons`, so exactly one balloons DaemonSet remains, k3d-gpu's (Gate 2 probe 6). **Deviation:** EAS's comment calls `nri-balloons` the only supported value for production. |
| `nfd_enabled` | `false` | EI `roles/nfd/defaults/main.yaml:13` (default `true`) | k3d-gpu runs NFD v0.20.1 already. EI's would add a second one, v0.17.1. |
| `agent_sandbox_router_image` | the digest in `images.yaml` | EAT `roles/agent_sandbox/defaults/main.yaml:27`, substituted at `roles/agent_sandbox/tasks/install.yaml:138` | Upstream floats `latest-main`. |
| `istio_enabled` | `true` | EAS `configs/defaults/global_config.yaml:61` | EAT enrols `agent-sandbox-system` in ambient (`roles/agent_sandbox/tasks/install.yaml:20-30`), and EI's LiteLLM applies AuthorizationPolicies (`roles/litellm/tasks/install.yaml:638-676`). Istio stays on. |
| `istio_helm_default_values_file` | `env/<env>/istio-values.k3d-gpu.yaml` | EAS `roles/istio/defaults/main.yaml:32`, used by `roles/istio/tasks/install.yaml:81` and `upgrade.yaml:16` | See Istio below. |

### Istio ambient on k3d, without patching Intel's code

Istio's `istio-cni` must install its plugin where containerd looks for CNI binaries. On
a k3d or k3s node that is not `/opt/cni/bin`. The preflight reads each node's
`/var/lib/rancher/k3s/agent/etc/containerd/config.toml` with `docker exec`:

- `bin_dirs = ["/usr/bin"]` is the k3d-gpu node image: platform `k3d`.
- `["/var/lib/rancher/k3s/data/cni"]` is stock k3s: platform `k3s`.
- Anything else is refused. `EAS_ISTIO_PLATFORM=k3d|k3s` overrides the detection.

Gate 2 probes 1-2 showed that each layout fails with the other platform's profile.

`render` writes `env/<env>/istio-values.k3d-gpu.yaml`: the checkout's own
`charts/istio/values.yaml` with `global.platform` added. It points
`istio_helm_default_values_file` at that file. The variable replaces the default values
file instead of merging into it, so the file is generated on each run from the
checkout, not stored here. Patch 0002 (`istio_platform`) stays an upstream proposal
only.

`helm template` (helm v3.20.2) of the rc's vendored umbrella chart with the generated
file, Istio 1.27.3:

| Platform | `istio-cni-node` `cni-bin-dir` | `cni-net-dir` |
|---|---|---|
| `k3d` | `/bin` | `/var/lib/rancher/k3s/agent/etc/cni/net.d` |
| `k3s` | `/var/lib/rancher/k3s/data/cni` | `/var/lib/rancher/k3s/agent/etc/cni/net.d` |
| none (EAS's values) | `/opt/cni/bin` | `/etc/cni/net.d` |

### AMX-aware model placement

In EI's catalog `defaults.require_amx` is `true` (`model_manager/models.yaml:20`). Then
`model-manager` gives every model pod, the guard included, a required nodeAffinity on
`feature.node.kubernetes.io/cpu-cpuid.AMXBF16=true` (`core/renderer.py:90-125`).

The guardrail is on with `auth_provider: litellm`, and it fails closed:
- `register_guardrail.yaml:65-66` sets `pre_call` and `default_on`;
- `qwen3guard_gen.py.j2` blocks every request when the guard is unreachable.

`model-manager deploy --wait` only warns on a timeout and exits 0 (`core/cli.py:337-361`).
On a host without AMX the stock catalog therefore installs "successfully", leaves every
model Pending, and LiteLLM refuses every request.

`render` writes `require_amx: true` into `env/<env>/models.yaml` only when every
schedulable node carries that label. k3d-gpu sets it only on AMX hosts
(`scripts/k3d-gpu` `amx_labels`). `EAS_REQUIRE_AMX=true|false` overrides the detection.
With `true` on a host without AMX, the preflight warns. H61's negative control uses that.

### The model

EAT's `config.yaml:35` auto-deploys `qwen2-5-coder-14b` (96Gi, strict). With the guard,
the default install requests about 131 GiB (EAT `docs/single-node-deployment.md:84`).
`render` sets `llm_services_autodeploy_models: [$EAS_MODEL]` in
`config.agent-toolkit.yaml`. The default is `qwen3-1-7b`: EI `models.yaml:218-236`, vLLM
0.28.0, `--enable-auto-tool-choice --tool-call-parser hermes`.

`render` rewrites three fields of that entry and prints each one against the catalog:

| Field | Catalog | k3d-gpu | From |
|---|---|---|---|
| `env.VLLM_CPU_KVCACHE_SPACE` | `40` | the fit's value | `k3d-gpu fit --model Qwen/Qwen3-1.7B --engine vllm --device cpu --json` (the mechanism `integrations/eat/eat-up.sh` uses) |
| `memory` | `64Gi` | the fit's memory request | the same fit |
| `cpu` | `auto` | `8` (the value `auto` takes outside `nri-balloons`), or less to fit | the node |

The model's CPU must fit: model cpu + qwen3guard cpu (8, `models.yaml:417`) + the
requests already on the node <= allocatable. The same holds for memory (qwen3guard 16Gi).
When the model cannot fit with at least 2 CPUs, the preflight refuses and names the
numbers.

Overrides:
- `EAS_MODEL` with `EAS_MODEL_ID` picks another catalog model;
- `EAS_MODEL_CPU` sets its CPU.

### Agent-sandbox ownership

EAT applies agent-sandbox v0.5.0 into `agent-sandbox-system` with `kubernetes.core.k8s`
`state: present` (`roles/agent_sandbox/tasks/install.yaml:55-63`). That would patch
whatever controller is there. `k3d-gpu sandbox up` installs v1.0.8 into the same
namespace. The preflight refuses a controller other than v0.5.0. Run
`k3d-gpu sandbox down` first, or use a separate cluster.

### Envoy Gateway

At `5c27fb3`, `roles/envoy_gateway/tasks/install.yaml:199-219` selects
`node-role.kubernetes.io/control-plane: ""` for the EnvoyProxy DaemonSet. k3s labels
its servers `"true"`, so no envoy is scheduled, and no variable controls this.
`patches/0006-envoy-control-plane-affinity-rc1.patch` is upstream draft 0006 rebased onto
that file. It is the same change: a required node affinity with `Exists`, and
`nodeSelector: null`. `integrations/eat/upstream/checks/eas-envoy-reinstall.py`, run on
an rc clone before and after the patch, gives `ok` for a fresh install and for a
re-install over the unpatched object.

## What differs from Intel's single-node page

EAT `docs/single-node-deployment.md` installs a Kubespray cluster on a Xeon host. Here:

- k3d-gpu makes the cluster, and Kubespray is skipped (`existing_kubernetes`).
- k3s ServiceLB replaces MetalLB.
- k3d-gpu's NRI balloons policy and NFD replace EAS's and EI's.
- `require_amx` follows the node.
- The default model is `qwen3-1-7b`, not `qwen2-5-coder-14b`, sized by `k3d-gpu fit`.
- Istio ambient gets the k3d or k3s CNI layout.
- The envoy data plane is scheduled on a k3s server.
- Kata is off.
- `configure` is not run (see Run).

## eas-check.sh

`eas-check.sh` runs on the host with kubectl and curl. It prints `ok` or `FAIL` with a
cause for each item, and exits 1 when any item fails:

- the `istio-cni-node` and `ztunnel` DaemonSets are Ready;
- at least one envoy data-plane pod is Ready;
- there is exactly one agent-sandbox controller, v0.5.0;
- there is no second NRI balloons DaemonSet: at most one, and that one is k3d-gpu's in
  `kube-system`. None at all passes, on a host where k3d-gpu bakes no balloons policy;
- there is exactly one NFD worker, k3d-gpu's;
- `qwen3guard` and the model are Ready. For a pod that is not, the check prints the
  scheduler's `PodScheduled` message and names the cause: the AMX nodeAffinity
  (`require_amx`) or the fit.

Then LiteLLM, through the route users take: EI's `HTTPRoute litellm`, host
`litellm.<base_domain_name>`, with `curl --resolve` to the Envoy gateway's address
(`EAS_GATEWAY_ADDR` overrides it). The master key is read from Secret
`litellm/litellm-master-key` and reaches curl on stdin only. With it the check makes a
virtual key that lasts 15 minutes. With the virtual key it sends:
- one chat completion, which must return HTTP 200;
- one tool call, which must end with `finish_reason` `tool_calls` and `get_weather`;
- one prompt the guardrail must block.

Then it deletes the key. Last, a fixed program runs in a warm-pool sandbox through
EAT's sandbox-router (Secret `sandbox-router-auth`) and must print exactly 385.

## Limits

- **Not run live.** H61 is pending.
- **A prerelease.** EAS v1.2.0-rc.1 is a prerelease for validation, not for production.
- **No XPU.** Intel Arc (XPU) deployment is not in this EAT release (EAT `README.md:36`
  at `036b662`).
- **Kata is off.**
- **Platform requests are not counted.** The preflight counts the requests already on
  the node. The platform and inference pods the install adds come on top (about 17 GiB
  of memory, and 2 GiB for the router, per EAT `docs/single-node-deployment.md:84`).
  On a node with little headroom the model can still stay Pending. `eas-check.sh` then
  names `fit`.
- **Model images by tag.** The model images come from EI's catalog by tag
  (`docker.io/vllm/vllm-openai-cpu:v0.28.0`), not by digest.
- **One cluster per toolkit.** The cluster cannot run `k3d-gpu sandbox up` at the same
  time.

## Files

| File | Role |
|---|---|
| `eas-up.sh` | the run, and the steps `render`, `pin-revs`, `check-head`, `check-ext`, `patch` |
| `eas-facts.py` | the preflight; writes `facts.json` |
| `eas-check.sh` | the end-state check |
| `bastion.Dockerfile` | Ubuntu 24.04 by digest, plus yq, kubectl and helm by sha256 |
| `constraints.txt` | exact versions for the installer's venv |
| `images.yaml` | the image overrides, values-shaped so `scripts/images-lock` collects them |
| `patches/0006-envoy-control-plane-affinity-rc1.patch` | the one edit to Intel's checkout |

`tests/eas_test.sh` covers all of the above without a network, docker or a cluster. It
uses stub `kubectl`, `docker`, `k3d-gpu` and `curl`, and the fixture checkout in
`tests/fixtures/eas-rc1/` (upstream lines only, with goldens).
