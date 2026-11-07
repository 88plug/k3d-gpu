# Intel EAS on k3d-gpu (`k3d-gpu eas`)

> **Status: the GA tags are the default; F5 is SUPPORTED.** Intel tagged EAS `v1.2.0`,
> enterprise-inference `v1.1.0` and EAT `release-2.0.0` on 2026-11-06, and `k3d-gpu eas`
> (and `k3d-gpu eat`, its agent-toolkit layer) pins them by default. F5
> ([EXPERIMENTS.md](../../EXPERIMENTS.md#f5-run-xeon-6741p-2026-11-07)) ran `k3d-gpu eat` once
> at those tags on the Xeon 6741P on 2026-11-07: `eat up` installed every layer with
> `failed=0` and no hand edit, `eat check` passed (Intel's `validate agent-toolkit`, then chat,
> a tool call, the guardrail's block and a sandbox run through the EAS gateway), the secret scan
> counted 0, and `eat down` cleaned up. The earlier run, F3 (2026-11-01, EAS `main`,
> i9-13900T), installed every layer with `failed=0` and passed `eas check`, and is REFUTED:
> its secret scan found the Keycloak admin password once, in a `ps` sample of `kcadm.sh`'s
> command line ([State and secrets](#state-and-secrets)). With `auth_provider: litellm`, the
> agent toolkit's setting at the GA tags, EAS installs no Keycloak at all.

Intel® AI for Enterprise Solutions ([intel/enterprise-ai-solutions](https://github.com/intel/enterprise-ai-solutions),
EAS) installs Intel's AI stack in layers with one script, `es_auto_installer.sh`. Since
EAT PR [#19](https://github.com/intel/enterprise-agent-toolkit/pull/19) and EAS PR
[#66](https://github.com/intel/enterprise-ai-solutions/pull/66), the Enterprise Agent
Toolkit (EAT 2.0) is one of those layers: `es_auto_installer.sh install agent-toolkit`.
EAS has a bring-your-own-cluster mode, `existing_kubernetes`, that skips Kubespray; a
k3d-gpu cluster is such a cluster.

`k3d-gpu eas` drives that mode: it reads the cluster, picks every EAS setting that a k3d
cluster needs, applies the k3d patches EAS has not merged yet, and runs the installer
from a pinned bastion container. EAT 2.0 has no installer of its own, so `k3d-gpu eat
up|status|down|check` is `k3d-gpu eas` with `--agent-toolkit`. EAT 1.x (the
`deploy-agentic-stack.sh` releases, gone at `release-2.0.0`) stays on
[`integrations/eat/eat-up.sh`](../../integrations/eat/README.md), which `k3d-gpu eat up
--legacy` runs.

## What "full EAS" covers

| Layer | `es_auto_installer.sh` | `k3d-gpu eas up` |
|---|---|---|
| platform (Istio, Envoy Gateway, cert-manager, PostgreSQL, observability; Keycloak with `auth_provider: keycloak`) | `install platform` | always |
| inference (model-manager and its vLLM CPU models, KServe, Envoy AI Gateway, NFD; models behind a Keycloak JWT by default, `auth_provider: keycloak`) | `install inference` | always |
| agent-toolkit (EAT 2.0: Redis Stack, pgvector, agent sandbox; its manifest sets `auth_provider: litellm`, which brings LiteLLM with its guardrail and hybrid router, and Langfuse, in place of Keycloak) | `install agent-toolkit` | `--agent-toolkit`, or `k3d-gpu eat up` |
| erag (Intel Enterprise RAG) | `install erag` | not yet, see [Limits](#limits) |

LiteLLM and Langfuse are inference-layer components that EAS installs only when
`auth_provider` is `litellm`, and Keycloak's client config only when it is not
([EI components.yaml#L21-L24 and #L41-L49](https://github.com/intel/enterprise-inference/blob/d035b1ef1981fbf5084ff05c9a337e620478bb87/components.yaml#L21-L49);
EAS's default is `keycloak`,
[global_config.yaml#L66](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/configs/defaults/global_config.yaml#L66)).
The agent-toolkit manifest sets it, in a `global_config.yaml` that `init` creates only
([repos.agent-toolkit.yaml#L8-L10](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/configs/repos/repos.agent-toolkit.yaml#L8-L10));
`eas up` sets the manifest's keys in the env after `init`, so an env inited before gets
them too. Gate 2 served its model at `inference.<base_domain_name>` with a Keycloak token
([upstream-eat.md, probe 5](upstream-eat.md#test-plan)).

The platform, inference and agent-toolkit layers run on the Xeon CPU path.
`kubernetes_accelerator: "cpu"` is the only value with behaviour in EAS today
([global_config.yaml#L98](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/configs/defaults/global_config.yaml#L98)),
and EAT 2.0 lists "Intel® Arc™ (XPU) deployment" as not in the release
([README.md#L36](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/README.md#L36)).

## Commands

```bash
k3d-gpu eas up [--agent-toolkit] [--eas-ref R] [--ei-ref R] [--eat-ref R] \
               [--model NAME] [--reset-env] [--dry-run] [--explain] [-- <ansible-playbook args>]
k3d-gpu eas status     # EAS status, pins, overlay decisions, gateway IP, pods not Ready
k3d-gpu eas check      # with --agent-toolkit: Intel's validate, then chat through the EAS gateway with a minted LiteLLM key, the guard and the sandbox step
k3d-gpu eas down       # EAS teardown; the cluster stays (k3d-gpu down removes it)

k3d-gpu eat up [--legacy] [eas up options]   # EAT 2.0 = eas up --agent-toolkit; --legacy: EAT 1.x's eat-up.sh
k3d-gpu eat status | down | check [--legacy] [--model NAME]
```

- The cluster is `K3D_GPU_CLUSTER` and its own kubeconfig, `${STATE_DIR}/kubeconfig-<cluster>`.
  When the cluster does not exist, `eas up` runs `k3d-gpu up` first, so EAS is one command
  on an Intel host.
- `--dry-run` reads the cluster, prints every choice and patch decision, and writes the
  files it would use under the state dir. It changes nothing in the cluster.
- Every automatic choice prints as `fact -> choice [source]`, with a short source
  (`repo@sha7 path:line`). `--explain` prints the full permalink instead.
- Arguments after `--` go to `ansible-playbook`, for example
  `k3d-gpu eas up --agent-toolkit -- -e '{"llm_services_autodeploy_models": []}'` to
  install without EAT's chat model.
- `eas down` gives the teardown the same image pins and overrides as `eas up` gave the
  install. EAS picks teardown components by the same `enabled` flags as install
  ([resolve_components.py](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/filter_plugins/resolve_components.py#L235)),
  so components `eas up` left to k3d-gpu, such as its NFD, stay.

What `eas up` does, in order:

1. Reads the cluster, read-only: node labels and allocatable CPU, memory and devices; the
   `k3s.io/node-args` annotation (ServiceLB, CIDRs); any `nri-resource-policy-balloons`
   DaemonSet and its owner; NFD; the agent-sandbox CRDs; EAT 1.x namespaces; the server
   version, rootless Docker, host `MemAvailable` and inotify limits; and
   `k3d-gpu fit --model <id> --engine vllm --device cpu --json` per model candidate.
2. Writes the choices: an ansible extra-vars file loaded last (`k3d-gpu-overrides.yaml`),
   the `existing_kubernetes` line in the env's `global_config.yaml` (the installer reads it
   there with `yq`,
   [es_auto_installer.sh#L1378-L1394](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L1378-L1394)),
   the `models.yaml` edits model-manager reads, the layer manifest's `rev:` rewritten to
   SHAs, and `choices.txt`.
3. Builds or reuses the bastion image (Ubuntu 24.04, kubectl, helm, yq, a hashed Python
   lock and `kubernetes.core`), tagged by the hash of its Dockerfile and lock.
4. In the bastion (host network, your uid and gid, the kubeconfig read-only): clones EAS
   at the pinned SHA, applies the [overlay](#patch-overlay), pins the layer manifest to
   SHAs ([`_checkout_rev` accepts one](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L658-L693)),
   then runs `init <layer> --env k3d-gpu` and
   `install <layer> --env k3d-gpu -- -e @/eas-state/k3d-gpu-overrides.yaml`.
   `configure` is skipped: it needs a terminal or `--force`, `sudo` and apt
   ([#L318-L352](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L318-L352)),
   and every tool it installs is already in the image. The installer reuses a venv that has
   `ansible-playbook` ([#L801-L806](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L801-L806)).
5. Prints the gateway: the address of the Gateway `eg-gateway` in `envoy-gateway-system`
   (its `status.addresses`). No Service is named `eg-gateway`: EAS sets no Service name, so
   Envoy Gateway names the proxy's LoadBalancer Service `envoy-` plus the Gateway's
   namespace, name and a hash
   ([gateway v1.7.1 resource_provider.go#L299-L304](https://github.com/envoyproxy/gateway/blob/3f75d826cc1dbd3d66632abbd45f96388d226c1d/internal/infrastructure/kubernetes/proxy/resource_provider.go#L299-L304));
   k3s ServiceLB gives it the node IP. With `--agent-toolkit` it also prints a ready
   `curl --resolve litellm.<base_domain_name>:443:<ip>` line. It never edits `/etc/hosts`.

## Automatic choices

Each row is one line of `choices.txt`. Sources are pinned to the commits in [Pins](#pins).

| Fact read from the cluster | Choice | Source |
|---|---|---|
| A k3d-gpu cluster | `existing_kubernetes: /kube/config`, the per-cluster kubeconfig mounted read-only | [EAS global_config.yaml#L15](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/configs/defaults/global_config.yaml#L15); [es_auto_installer.sh#L1496-L1502](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L1496-L1502) |
| k3d-gpu node image: containerd CNI `bin_dirs` is `["/usr/bin"]` | `istio_platform: k3d` (stock k3s needs `k3s`); needs overlay 0002 | [upstream-eat.md, Gate 2 probes 1 and 2](upstream-eat.md#test-plan) |
| ServiceLB on (no `--disable=servicelb` in `k3s.io/node-args`) | `metallb_enabled: false` | [EAS components.yaml#L57-L59](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/configs/components.yaml#L57-L59) |
| A balloons DaemonSet already runs (k3d-gpu baked it for a hybrid or multi-NUMA host), or Docker is rootless | `kubernetes_cpu_policy: best-effort` | [EAS preflight.yaml#L175-L233](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/playbooks/includes/preflight.yaml#L175-L233); Gate 2 probe 6 |
| No NRI policy on the node, NRI available (`K3D_GPU_BALLOONS=external`, or nothing baked) | `kubernetes_cpu_policy: nri-balloons`; needs overlay 0003 | same; [ledger:H14] for EAT's chart values on such a cluster |
| k3d-gpu's NFD runs in `node-feature-discovery` (a k3s manifest, not a Helm release) | `nfd_enabled: false`: EI looks only for a Helm release of that name and would install a second NFD | [EI roles/nfd/tasks/install.yaml#L15-L25](https://github.com/intel/enterprise-inference/blob/d035b1ef1981fbf5084ff05c9a337e620478bb87/roles/nfd/tasks/install.yaml#L15-L25) |
| `feature.node.kubernetes.io/cpu-cpuid.AMXBF16=true` on the node | `require_amx: true` in `models.yaml` | [EI models.yaml#L20](https://github.com/intel/enterprise-inference/blob/d035b1ef1981fbf5084ff05c9a337e620478bb87/model_manager/models.yaml#L20) |
| No AMXBF16 label | `require_amx: false`, so model pods are not left Pending on the AMX node affinity | [EI core/renderer.py#L90-L104](https://github.com/intel/enterprise-inference/blob/d035b1ef1981fbf5084ff05c9a337e620478bb87/model_manager/core/renderer.py#L90-L104); Gate 2 probe 5 |
| No AMX | `VLLM_CPU_SGL_KERNEL` left as shipped: vLLM v0.28.0 checks AMX itself | [vLLM utils.py#L238-L244](https://github.com/vllm-project/vllm/blob/2cf0a6915ce544dc493a0990f2ea38d81601128a/vllm/model_executor/layers/utils.py#L238-L244) |
| No `--cluster-cidr` or `--service-cidr` in `k3s.io/node-args` | `kubernetes_cluster_cidr: 10.42.0.0/16`, `kubernetes_service_cidr: 10.43.0.0/16` (EI's LiteLLM falls back to `10.233.64.0/18` for the pod network it admits, which EAT turns on with OpenShell and asks for on a BYO cluster) | [k3s server.go#L139-L144 at v1.37.1+k3s1](https://github.com/k3s-io/k3s/blob/356c025475bbefaa09cf0d7e48e8e2adbbde57db/pkg/cli/cmds/server.go#L139-L144); [EI litellm defaults#L38](https://github.com/intel/enterprise-inference/blob/d035b1ef1981fbf5084ff05c9a337e620478bb87/roles/litellm/defaults/main.yaml#L38); [EAT config.yaml#L61-L64](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/config.yaml#L61-L64) |
| EI's model deploy hands model-manager the LiteLLM master key through ansible `environment:`, which lands in the deploy's `/bin/sh -c` command line while each model deploys | `_deploy_litellm_secret: ""` in the overrides, so model-manager reads Secret `litellm/litellm-master-key` itself. Before the install, `eas up` checks that EI's `deploy_models.yaml` takes the key from that variable alone, and stops if it does not | [EI deploy_models.yaml#L231](https://github.com/intel/enterprise-inference/blob/d035b1ef1981fbf5084ff05c9a337e620478bb87/roles/llm_services/tasks/deploy_models.yaml#L231) |
| A BYO cluster | `kata_containers_enabled: false`, set explicitly (EAS's default is already `false`): the role fails on BYO | [EAS kata_containers/tasks/install.yaml#L9-L22](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/roles/kata_containers/tasks/install.yaml#L9-L22) |
| `gpu.intel.com/*` or `habana.ai/gaudi` allocatable | `kubernetes_accelerator: cpu` stays, and the line says "serve the device with k3d-gpu agent up" | [EAT README.md#L36](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/README.md#L36) |
| `--agent-toolkit`, no agent-sandbox CRDs | `agent_sandbox_enabled: true`: EAT installs agent-sandbox `v0.5.0` | [EAT config.yaml#L54-L56](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/config.yaml#L54-L56) |
| `--agent-toolkit`: EAT's router image is the floating `:latest-main` | `agent_sandbox_router_image` and `agent_sandbox_python_runtime_image` pinned by digest in the overrides | [EAT agent_sandbox defaults#L27-L28](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/roles/agent_sandbox/defaults/main.yaml#L27-L28) |
| `--agent-toolkit`: node allocatable memory and `fit` cover EAT's default (about 131 GiB: `qwen2-5-coder-14b` 96, Qwen3Guard 16, router 2, the rest 17) | keep the default model | [EAT single-node-deployment.md#L84](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/docs/single-node-deployment.md#L84) |
| They do not | the first EAT-documented 64 GiB model that `fit` says fits: `qwen2-5-coder-7b`, then `llama-3-1-8b`; its `VLLM_CPU_KVCACHE_SPACE` comes from `fit` | same line |
| Nothing fits | `llm_services_autodeploy_models: []`, with the reason | [EAT config.yaml#L32-L35](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/config.yaml#L32-L35) |
| `--model NAME` | that model, whatever the fit says | |
| `--agent-toolkit` under `kubernetes_cpu_policy: nri-balloons`, the host's CPU topology from `/sys` | EI's `cpu: auto` estimated as EAS and EI size it: one NUMA node's logical CPUs, less EAS's reserve there (its SMT siblings, or 12 CPUs with one thread per core) and Qwen3Guard's 8. When the pods outside the balloons (about 14 CPUs, EAT's sandbox router among them) would not fit beside it, an explicit `cpu` for the model in `models.yaml`; otherwise `cpu: auto` as shipped | [EI nri_policy.py#L569-L591](https://github.com/intel/enterprise-inference/blob/d035b1ef1981fbf5084ff05c9a337e620478bb87/model_manager/core/nri_policy.py#L569-L591); [EAS compute_reserved_cpus_advanced.sh#L252-L270](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/roles/nri_cpu_balloons/files/compute_reserved_cpus_advanced.sh#L252-L270); [EAT single-node-deployment.md#L191](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/docs/single-node-deployment.md#L191); H62's router left Pending on one guest shape |
| `--agent-toolkit` (in `eas.sh`, after `init`) | the layer manifest's `global_config:` keys, `auth_provider: litellm`, set in `env/k3d-gpu/global_config.yaml` even on an env `init` created before; the dry run lists them | [repos.agent-toolkit.yaml#L8-L10](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/configs/repos/repos.agent-toolkit.yaml#L8-L10); [es_auto_installer.sh#L930-L950](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L930-L950) |
| `--agent-toolkit` | `guardrails_enabled` and `hybrid_router_enabled` keep EAT's defaults (on) | [EAT config.yaml#L25-L28](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/config.yaml#L25-L28) |

## Refusals

`eas up` stops before it changes the cluster when:

| Condition | Message | Why |
|---|---|---|
| ServiceLB is off (`--disable=servicelb`, the EAT 1.x advice) | recreate the cluster without `--disable=servicelb` | the gateway's Envoy Service needs a LoadBalancer address, which k3s ServiceLB gives on a k3d node; MetalLB on k3d's Docker network is not part of this path |
| EAT 1.x namespaces from `eat-up.sh` exist (`genai-gateway`, its `ingress-nginx`) | remove the EAT 1.x stack first | both stacks would own LiteLLM and ingress |
| k3d-gpu's agent-sandbox v1.0.8 runs (`k3d-gpu sandbox up` was used) and `--agent-toolkit` is set | run `k3d-gpu sandbox down` first | EAT applies agent-sandbox v0.5.0's manifests, which would replace v1.0.8's CRDs and controller with an older release |
| `--layer erag` | ERAG is not wired into `k3d-gpu eas` yet; its Intel GPU flavour also needs an EI `vllm-xpu` runtime that no EI branch has | see [Limits](#limits) |
| A patch in the overlay neither applies nor is found merged or fixed | names the patch, the upstream PR that moved its hunk, and "regenerate" | see [Patch overlay](#patch-overlay) |

The other direction holds too: `k3d-gpu sandbox up` refuses while EAS's
`sandbox-router-deployment` exists.

## Patch overlay

The k3d patches for EAS are the upstream drafts in
[integrations/eat/upstream/patches/](../../integrations/eat/upstream/patches/), one copy for
both uses. The overlay-only patches are in
[integrations/eas/patches/](../../integrations/eas/patches/): the `release-1.2` form of 0006,
and 0007 and 0008, which keep a newer Gateway API install on `release-1.2`.
[`integrations/eas/overlay.json`](../../integrations/eas/overlay.json) lists, per patch, its
variants, when it is needed (`always`, `eas_owns_nri`, `multi_node`, `never`), a probe that
recognises an upstream fix done another way, the knob `eas up` writes, and a hint naming the
upstream PR known to move its hunk. [`integrations/eas/overlay.py`](../../integrations/eas/overlay.py)
reads it.

At the pinned EAS commit each patch gets one decision, printed as `fact -> choice [source]`:

- **skip**: not needed on this cluster;
- **drop-merged**: a variant reverse-applies (`git apply -R --check`): upstream took the patch;
- **drop-fixed**: the probe matches and the knob is still in the file it matched: upstream
  fixed it another way. A probe match with the knob gone is a STOP;
- **apply**: the first variant whose `git apply --check` passes;
- otherwise **STOP** (exit 3), before anything is written or the cluster is touched. The line
  names the upstream PR that moved the hunk and says to regenerate the patch.

| Patch | Needed when | Probe for an upstream fix | `release` profile (EAS `v1.2.0` `a051f00`, the default) | `main` profile (EAS `97f6157`) |
|---|---|---|---|---|
| 0002 Istio `global.platform` | always | `roles/istio/**` sets `global.platform` from a variable; knob `istio_platform` | apply | apply |
| 0003 nri-plugins chart pin `v0.15.0` | `eas_owns_nri`: `kubernetes_cpu_policy: nri-balloons` | `chart_version:` in `roles/nri_cpu_balloons/tasks/install_advanced.yaml` | apply under `nri-balloons` (a host k3d-gpu bakes no balloons for: one NUMA node, no hybrid CPU), skip under `best-effort` | same |
| 0004 BYO node topology labels | `multi_node` | `roles/kubernetes/tasks/install.yaml` runs `apply_node_topology.yaml` | skip (one k3d-gpu server) | skip |
| 0005 single-node k3d docs page | `never` (docs only) | none | skip | skip |
| 0006 Envoy control-plane affinity | always | an `Exists` term on the control-plane key in a file with no exact `""` selector, or `roles/envoy_gateway/vars/k3s.yaml` (the per-distribution layout of EAS #50) | apply, variant b (the EnvoyProxy task in [`install.yaml`](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/roles/envoy_gateway/tasks/install.yaml#L199-L219)) | apply, variant a (`roles/envoy_gateway/tasks/controller.yaml`) |
| 0007 Istio keeps a newer Gateway API | always | `roles/envoy_gateway/tasks/crds.yaml` decides `gateway_api_apply` (EAS #42) | apply: the istio role applies Gateway API v1.2.1 only when the CRDs are absent or older | drop-fixed |
| 0008 Envoy Gateway keeps a newer Gateway API | always | the same | apply: with Gateway API v1.5.0 or later installed, Envoy Gateway's own CRDs come from `gateway-crds-helm` and the chart's `crds/` are skipped | drop-fixed |

`overlay.json` records these decisions as `verified` cases (the `v1.2.0-rc.1` profile's are the
release's: `a051f00` changes only docs and `configs/repos` from `5c27fb3`);
`overlay.py --check-verified` re-checks them against an EAS clone, and
`integrations/eat/upstream/checks/apply-check.sh --overlay` does it on a fresh clone. 0006 has
a second variant because `controller.yaml` does not exist on `release-1.2`, which does not
contain EAS #42.

0007 and 0008 come from k3s: v1.37 bundles the Gateway API CRDs (its `gateway-api-crd` addon,
[manifests/gateway-api-crd.yaml at v1.37.1+k3s1](https://github.com/k3s-io/k3s/blob/v1.37.1%2Bk3s1/manifests/gateway-api-crd.yaml)),
and Gateway API v1.5.0 and later ship the `safe-upgrades.gateway.networking.k8s.io`
ValidatingAdmissionPolicy, which refuses a lower version. EAS `release-1.2` installs Gateway
API v1.2.1 from its istio role and v1.4.1 from gateway-helm's `crds/`, so on k3s v1.37 `install`
stopped at each in turn ("Installing CRDs with version before v1.5.0 is prohibited by
default"); F5's wiring smokes found both. EAS `main` already keeps newer foreign CRDs since
[#42](https://github.com/intel/enterprise-ai-solutions/pull/42)
([crds.yaml#L10-L28](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/roles/envoy_gateway/tasks/crds.yaml#L10-L28)),
which is why F3 on `main` did not meet it; 0007 and 0008 do the same on `release-1.2`.

Open EAS PRs that change a decision when they merge (`overlay.py --watch`, checked 2026-10-29):

| EAS PR | Effect on the overlay |
|---|---|
| [#50](https://github.com/intel/enterprise-ai-solutions/pull/50) and [#52](https://github.com/intel/enterprise-ai-solutions/pull/52) (OpenShift) | 0006 no longer applies: the selector moves into `_eg_k8s_provider`, still `control-plane: ""`. That is a STOP naming #50. Once a k3s vars file lands, 0006 becomes drop-fixed |
| [#61](https://github.com/intel/enterprise-ai-solutions/pull/61) (Headlamp SSO), merged 2026-10-30 | 0002 fails on its `global_config.yaml` hunk context: a STOP naming #61, so 0002 needs regenerating. 0004 (skipped on one node) and 0005 (docs, never applied) fail there too. 0006 still applies. EAS `main` is `0f7bc2b` since (read 2026-11-01); the pin stays `97f6157`, where the decisions above hold, and moving it past #61 needs 0002 regenerated first (read from #61's head, not re-checked on the merge) |

## Pins

`integrations/eas/pins.json` (schema `k3d-gpu.eas-pins/v1`) holds flat rows
`{name, repo, ref, sha, date, source}`. The `release` profile is the default; `main` and
`v1.2.0-rc.1` are the records F3 and H62 ran.

| Profile | EAS | EI (enterprise-inference) | EAT |
|---|---|---|---|
| `release` (default) | tag `v1.2.0` [`a051f00`](https://github.com/intel/enterprise-ai-solutions/commit/a051f00fcb2dd473fe763610471cd509db82c433) ([release notes](https://github.com/intel/enterprise-ai-solutions/releases/tag/v1.2.0)) | tag `v1.1.0` [`d035b1e`](https://github.com/intel/enterprise-inference/commit/d035b1ef1981fbf5084ff05c9a337e620478bb87) | tag `release-2.0.0` [`7b827e2`](https://github.com/intel/enterprise-agent-toolkit/commit/7b827e2d95cec826a20c7e3f5a30eb4818ed2992) |
| `main` | `main` [`97f6157`](https://github.com/intel/enterprise-ai-solutions/commit/97f6157d6a72f7173c698c474d4e51d2c79f1fd6) (#69) | `main` [`3e8a244`](https://github.com/intel/enterprise-inference/commit/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd) (#44) | `release-2.0.0` [`036b662`](https://github.com/intel/enterprise-agent-toolkit/commit/036b66234801e9bd950afd90830821859780d668) (the #19 merge; same tree as `agentic-toolkit-migration` `fb59459`) |
| `v1.2.0-rc.1` (`--eas-ref v1.2.0-rc.1`) | tag `v1.2.0-rc.1` [`5c27fb3`](https://github.com/intel/enterprise-ai-solutions/commit/5c27fb39a95eecec41aa4e29a1188984e9c726f3) | `release-1.1` [`170eb3a`](https://github.com/intel/enterprise-inference/commit/170eb3abb1956de0787727f21e356aef2b13ff24) | `release-2.0.0` `036b662` |

- The `release` triple is EAS `v1.2.0` and the commits its agent-toolkit manifest pins
  ([repos.agent-toolkit.yaml#L16 and #L25](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/configs/repos/repos.agent-toolkit.yaml#L12-L27)),
  the versions its release notes list. The `eas-release` pin in `pins/registry.toml` moves it:
  `hack/eas-release-pins` reads EAS's latest release, that manifest at the release commit and
  the tags the notes link, and refuses a manifest rev no linked tag points at.
- The `main` triple froze what EAS `main` cloned on 2026-10-29: inference `rev: "main"` and
  agent-toolkit `rev: "agentic-toolkit-migration"`.
- The `v1.2.0-rc.1` triple was the component table of the v1.2.0-rc.1 release notes.
- Any other `--eas-ref`, `--ei-ref` or `--eat-ref` is resolved to a SHA with
  `git ls-remote` when `eas up` runs, printed, and saved in the state dir. An EAS ref that
  is no profile takes EI and EAT from `configs/repos/repos.<layer>.yaml` at that commit,
  unless `--ei-ref` or `--eat-ref` names them. A floating ref never reaches the installer.
- `env/k3d-gpu` keeps the layer and pins it was set up with (`env.json`): another layer or
  other pins stop `eas up` before any change, and `eas down`, then `eas up --reset-env`,
  moves the env aside. EAS keeps one layer per checkout, and `init` never reseeds
  `models.yaml` or `config.<layer>.yaml`
  ([#L1093, #L1123](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L1093-L1123)).

Bastion tools (each binary checked against its upstream checksum file at build time):

| Tool | Pin | Why this version |
|---|---|---|
| kubectl | v1.37.2 | the minor of the node's k3s (`K3S_TAG` v1.37.1-k3s1); EAS's own v1.34.3 ([#L52](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L52)) is three minors off, outside the [kubectl skew policy](https://kubernetes.io/releases/version-skew-policy/#kubectl) |
| helm | v3.22.1 | latest v3; EAS's floor is v3.20.2 ([#L53](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L53)). Helm v4 is untested with `kubernetes.core` 6.3.0 |
| yq | v4.54.1 | EAS's floor is v4.53.2 ([#L51](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L51)) |
| jq | jq-1.8.2 | latest; enterprise-inference pipes yq into jq on the installer host to pick the models to deploy ([deploy_models.yaml#L99-L105](https://github.com/intel/enterprise-inference/blob/d035b1ef1981fbf5084ff05c9a337e620478bb87/roles/llm_services/tasks/deploy_models.yaml#L99-L105)), and EAS's `configure` installs no jq |
| ansible, kubernetes (Python) | 12.3.0, 35.0.0, hashed | newest inside EAS's ranges `ansible>=10.0,<13.0`, `kubernetes>=29.0,<36.0` ([requirements.txt](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/requirements.txt)) |
| kubernetes.core | 6.3.0 | EAS's [collections/requirements.yml](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/collections/requirements.yml) |

To move a pin (the Freshness workflow does the `release` rows; this is the review):

1. Read the new head: `git ls-remote https://github.com/intel/<repo> <ref>`, or for the
   release rows `hack/eas-release-pins`.
2. Edit that one row of `integrations/eas/pins.json`: `sha`, `date`, `source`.
3. Read the overlay decisions at the new EAS SHA: `k3d-gpu eas up --dry-run`, or
   `integrations/eat/upstream/checks/apply-check.sh --overlay` on a fresh clone. A STOP
   names the upstream PR that moved a hunk; regenerate that patch, or add a variant, and
   update the `verified` cases in `overlay.json` before the pin lands.
4. Run `tests/eas_test.sh` and update this page's tables.

The rows are flat on purpose, so a repo-wide pins registry can take them as they are.

## EAT's minimums on a smaller host

EAT's prerequisites assume a Xeon server. `eas up` checks each one and warns; it never
changes the host.

| EAT asks for | Source | What `eas up` does below it |
|---|---|---|
| 48 cores minimum, 96+ recommended | [prerequisites.md#L7](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/docs/prerequisites.md#L7) | warns and goes on; CPU requests that do not fit leave pods Pending, which `eas status` lists |
| 32 GB RAM minimum, 64 GB recommended | [prerequisites.md#L8](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/docs/prerequisites.md#L8) | sizes the model from node allocatable memory and `fit` (the default install requests about 131 GiB; the fallbacks 64 GiB each, [single-node-deployment.md#L84](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/docs/single-node-deployment.md#L84)) |
| `fs.inotify.max_user_instances = 8192`, `max_user_watches = 1048576` | [prerequisites.md#L21-L34](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/docs/prerequisites.md#L21-L34) | warns and prints the `sysctl` line to run |

## Known issues

`integrations/eas/known-issues.json` holds these rows as `{id, upstream, symptom, workaround, source, checked}`.

| Issue | Symptom | What `k3d-gpu eas` does |
|---|---|---|
| EAS [#71](https://github.com/intel/enterprise-ai-solutions/issues/71) (open) | `teardown platform` hangs on the Envoy AI Gateway CRD uninstall once a model was deployed | `eas down` clears the `AIGatewayRoute` finalizers first, then runs `teardown platform --env k3d-gpu --force` (teardown refuses a non-terminal run without `--force`, [#L1517-L1523](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L1517-L1523)) |
| Gateway API on k3s v1.37 | EAS `release-1.2` installs Gateway API v1.2.1 (istio) and v1.4.1 (gateway-helm's `crds/`) over the newer CRDs k3s bundles, whose safe-upgrades policy refuses both | overlay rows 0007 and 0008 keep the newer install, as EAS `main` does since #42 |
| `init` keeps an existing `global_config.yaml` | an env inited before keeps `auth_provider` as it was, and the agent toolkit installs without LiteLLM | sets the manifest's `global_config:` keys after `init`, and refuses an env set up for another layer or other pins until `eas down` and `--reset-env` |
| EI's `cpu: auto` | sizes the model without the pods outside the balloons, so EAT's sandbox router can find no CPU (H62) | an explicit `cpu` for the model when the estimate leaves no room |
| `require_amx: true` in EI's catalog | on a host without AMX every model pod stays Pending | writes `require_amx: false` when the node has no AMXBF16 label |
| kubectl skew | EAS installs kubectl v1.34.3, three minors behind k3s v1.37 | the bastion carries kubectl v1.37.2 |
| inotify minimum | file watchers fail with `too many open files` under the kernel default | warns and prints the `sysctl` line |
| 48-core minimum | under it, CPU requests may not fit | warns; `eas status` lists Pending pods |
| `--skip-models` in EI's docs ([config.yaml#L92](https://github.com/intel/enterprise-inference/blob/d035b1ef1981fbf5084ff05c9a337e620478bb87/config.yaml#L92)) | EAS's installer has no such flag and stops on it ([#L1348](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L1348)) | nothing to replace on the inference layer: EI's seeded config already has `llm_services_deploy_models: false` ([config.yaml#L93](https://github.com/intel/enterprise-inference/blob/d035b1ef1981fbf5084ff05c9a337e620478bb87/config.yaml#L93)). With `--agent-toolkit`, EAT's chat model and the Qwen3Guard guard model deploy whatever that key says ([EI llm_services install.yaml#L143-L162](https://github.com/intel/enterprise-inference/blob/d035b1ef1981fbf5084ff05c9a337e620478bb87/roles/llm_services/tasks/install.yaml#L143-L162)); `k3d-gpu eas up --agent-toolkit -- -e '{"llm_services_autodeploy_models": []}'` skips the chat model ([EAT config.yaml#L34](https://github.com/intel/enterprise-agent-toolkit/blob/7b827e2d95cec826a20c7e3f5a30eb4818ed2992/config.yaml#L34)), and the guard model stays while the guardrail is on |

Findings that are not workarounds in `known-issues.json` (EI's stale guardrail docs, the
master-key command line and the EAT 1.1.x findings) are listed in
[intel.md, Upstream findings](../intel.md#upstream-findings), with unsent issue drafts in
[upstream-eat.md](upstream-eat.md#issue-drafts-from-the-2026-11-01-runs-unsent) and, at the
GA tags, [upstream-eat.md](upstream-eat.md#issue-drafts-at-the-ga-tags-2026-11-07-unsent).

## State and secrets

- State lives in `${STATE_DIR}/eas-<cluster>/` (mode 0700): the EAS checkout, `env/k3d-gpu`,
  logs, `choices.txt`, `overlay.txt` and the resolved pins.
- The Keycloak admin password is generated by EAS
  ([keycloak defaults#L20](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/roles/keycloak/defaults/main.yaml#L20)).
  `HF_TOKEN` is passed to the bastion by name when it is set. `k3d-gpu eas` puts no secret
  on a command line or in a log, and keeps EI's LiteLLM master key off the model deploy's
  command line (the `_deploy_litellm_secret` row in [Automatic choices](#automatic-choices)).
- enterprise-inference's `keycloak_config` role, as it ships, runs
  `kcadm.sh config credentials ... --password <value>` and
  `kcadm.sh update clients/<id> -s secret=<value>` in the Keycloak pod
  ([install.yaml#L167-L196](https://github.com/intel/enterprise-inference/blob/d035b1ef1981fbf5084ff05c9a337e620478bb87/roles/keycloak_config/tasks/install.yaml#L167-L196)
  at v1.1.0; the same calls at `main` `3e8a244`).
  Its `no_log` keeps both values out of the logs, but while each kcadm JVM runs the value
  is in its argv, which the host's `ps` lists, since a k3d node is a container on the
  host. F3's secret scan caught the admin password in one 2 s sample
  ([F3 run](../../EXPERIMENTS.md#f3-run-i9-13900t-2026-11-01)); F3 did not scan for the
  client secret.
- Since F3, `eas up` patches that role right after `init`
  ([`patches/ei/0001`](../../integrations/eas/patches/ei/0001-keycloak-config-kcadm-argv.patch)
  for EI `main`, and its `release-1.1` variant
  [`0001-release-1.1-...`](../../integrations/eas/patches/ei/0001-release-1.1-keycloak-config-kcadm-argv.patch)
  for v1.1.0, whose task changed shape; applied by
  [`ei-kcadm.sh`](../../integrations/eas/ei-kcadm.sh), which tries each). With the agent
  toolkit at the GA tags (`auth_provider: litellm`) EI does not run `keycloak_config` and
  EAS installs no Keycloak; the inference layer alone keeps it. kcadm reads the admin
  password from `KC_CLI_PASSWORD`, as Keycloak 26.6.0 does when `--password` is absent
  ([BaseConfigCredentialsCmd.java#L104-L110](https://github.com/keycloak/keycloak/blob/0526b94f0ddf166a210e39fd69d897ab4bb14431/integration/client-cli/admin-cli/src/main/java/org/keycloak/client/cli/common/BaseConfigCredentialsCmd.java#L104-L110)),
  and a shell in the pod copies it from the `KC_BOOTSTRAP_ADMIN_PASSWORD` the Keycloak
  operator sets from `keycloak-admin-secret`
  ([KeycloakDistConfigurator.java#L102-L113](https://github.com/keycloak/keycloak/blob/0526b94f0ddf166a210e39fd69d897ab4bb14431/operator/src/main/java/org/keycloak/operator/controllers/KeycloakDistConfigurator.java#L102-L113)).
  Keycloak generates the client secret, and the role stores the value kcadm prints.
  `ei-kcadm.sh` stops the install when the task is not the one the patch was cut against,
  or when any kcadm call in EI passes a secret as an argument, and takes the patch out
  before `init` so that `init --upgrade` can move EI. `tests/eas_test.sh kcadm` runs the
  role both ways under `ansible-playbook`, with a fake password and a fake kcadm, while
  `ps` samples every argv: as shipped, both values are on kcadm's argv; patched, no sample
  holds either, for both variants. No live EAS run has checked the patch yet, and F3
  stays REFUTED. The EI
  issue is drafted and unsent ([upstream-eat.md](upstream-eat.md#ei-the-keycloak-admin-password-and-the-client-secret-are-kcadmsh-arguments)).
- `eas check` (after `--agent-toolkit`, the layer that brings LiteLLM) mints a LiteLLM
  virtual key from the `litellm-master-key` Secret (namespace `litellm`), uses it once
  against the gateway, and deletes it.

## Limits

- **Xeon CPU path only.** That is what EAS and EAT ship for a cluster like this one
  (sources [above](#what-full-eas-covers)). An Arc GPU or a Gaudi on the node is not used by
  EAS; serve it with `k3d-gpu agent up`. Registering such an engine behind EAS's LiteLLM,
  as [`eat-remote.sh`](../../integrations/eat/README.md) does for EAT 1.x, is a follow-up.
- **ERAG is not wired in yet.** EAS wires Intel Enterprise RAG in as a further layer
  ([repos.erag.yaml](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/configs/repos/repos.erag.yaml#L22-L28)),
  and `k3d-gpu eas up` does not install it. Its default flavour, `chatqna`, is a Xeon (CPU)
  deployment
  ([deployment/README.md#L3](https://github.com/intel/enterprise-rag/blob/319eb4652565fed42de9d601c61f9862b3759dfb/deployment/README.md#L3)),
  so it is on the same CPU path as the other layers and is the follow-up: pins, a test
  fixture and a run. Its Intel GPU flavour, `chatqna_intel_gpu`, is marked EXPERIMENTAL
  and needs `kubernetes_accelerator: intel-gpu` with an EI vLLM-XPU runtime
  ([config.yaml#L8-L15](https://github.com/intel/enterprise-rag/blob/319eb4652565fed42de9d601c61f9862b3759dfb/deployment/pipelines/chatqna_intel_gpu/config.yaml#L8-L15),
  ERAG `main` `319eb46`, tag `v3.0.0` `25812cb`). No branch of enterprise-inference has
  that runtime: all 33 branches carry only `openvino` and `vllm` under
  `model_manager/runtimes` (read 2026-10-29), so that flavour cannot run anywhere yet.
- **One node.** Patch 0004 (topology labels) is only needed on more than one node, and
  `eas up` targets the single k3d-gpu server.
- **Observed once, in F3.** The EAT 2.0 layer on k3d; agent-sandbox v0.5.0 on
  Kubernetes 1.37 (EAS tests 1.34.3); the CPU requests of the whole stack on a 32-CPU
  desktop; helm v3.22.1 with `kubernetes.core` 6.3.0; and no BYO task stopping on `sudo`
  inside the bastion. None of them stopped a step of the
  [F3 run](../../EXPERIMENTS.md#f3-run-i9-13900t-2026-11-01), one run on one host. The installer itself probes `sudo -n true` before every `install` and,
  without passwordless sudo, runs `ansible-playbook` with `--ask-become-pass`
  ([es_auto_installer.sh#L852-L861](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L852-L861),
  [#L1213](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L1213),
  [#L1459](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/es_auto_installer.sh#L1459)),
  which a bastion with no terminal cannot answer. The `eas` CLI PR's wiring smokes ran on a
  throwaway cluster; F3 is the scored run.

## Related

| If you want to… | Go to |
|---|---|
| Run EAT 1.x (`release-1.1.0`) on k3d-gpu | [integrations/eat](../../integrations/eat/README.md), [intel.md](../intel.md#run-intel-enterprise-agent-toolkit-on-one-machine) |
| See the upstream patch drafts and their checks | [upstream-eat.md](upstream-eat.md) |
| See EAT's PR history and pins | [platforms.md](../platforms.md#intel-enterprise-agent-toolkit-eat) |
| See the F3 pre-registration | [EXPERIMENTS.md](../../EXPERIMENTS.md#f3-pre-registration-i9-13900t-2026-10-29) |
| See the F3 run and its records | [EXPERIMENTS.md](../../EXPERIMENTS.md#f3-run-i9-13900t-2026-11-01), [bench/f3/](../../bench/f3/) |
