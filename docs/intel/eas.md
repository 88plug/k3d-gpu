# Intel EAS on k3d-gpu (`k3d-gpu eas`)

> **Status: built from upstream source, not yet run end to end.** The scored run is
> F3 in [EXPERIMENTS.md](../../EXPERIMENTS.md#f3-pre-registration-i9-13900t-2026-10-29),
> pre-registered on 2026-10-29 and PENDING. Until F3 has run, nothing on this page says
> that EAS works on k3d-gpu. The EAT 2.0 layer has never run on k3s or k3d in this
> repo's records: Gate 2 covered EAS's platform and inference layers only
> ([upstream-eat.md](upstream-eat.md#test-plan)).

Intel® AI for Enterprise Solutions ([intel/enterprise-ai-solutions](https://github.com/intel/enterprise-ai-solutions),
EAS) installs Intel's AI stack in layers with one script, `es_auto_installer.sh`. Since
EAT PR [#19](https://github.com/intel/enterprise-agent-toolkit/pull/19) and EAS PR
[#66](https://github.com/intel/enterprise-ai-solutions/pull/66), the Enterprise Agent
Toolkit (EAT 2.0) is one of those layers: `es_auto_installer.sh install agent-toolkit`.
EAS has a bring-your-own-cluster mode, `existing_kubernetes`, that skips Kubespray; a
k3d-gpu cluster is such a cluster.

`k3d-gpu eas` drives that mode: it reads the cluster, picks every EAS setting that a k3d
cluster needs, applies the k3d patches EAS has not merged yet, and runs the installer
from a pinned bastion container. EAT 1.x (the `deploy-agentic-stack.sh` releases) stays on
[`integrations/eat/eat-up.sh`](../../integrations/eat/README.md).

## What "full EAS" covers

| Layer | `es_auto_installer.sh` | `k3d-gpu eas up` |
|---|---|---|
| platform (Istio, Envoy Gateway, cert-manager, PostgreSQL, Keycloak, observability) | `install platform` | always |
| inference (model-manager and its vLLM CPU models, KServe, Envoy AI Gateway, NFD; models behind a Keycloak JWT by default, `auth_type: jwt`) | `install inference` | always |
| agent-toolkit (EAT 2.0: Redis Stack, pgvector, agent sandbox; sets `auth_type: litellm_vkey`, which brings LiteLLM with its guardrail and hybrid router, and Langfuse) | `install agent-toolkit` | `--agent-toolkit` |
| erag (Intel Enterprise RAG) | `install erag` | not yet, see [Limits](#limits) |

LiteLLM and Langfuse are inference-layer components that EAS installs only when
`auth_type` is `litellm_vkey`
([EI components.yaml#L42-L53](https://github.com/intel/enterprise-inference/blob/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd/components.yaml#L42-L53);
EI's default is `jwt`,
[config.yaml#L44](https://github.com/intel/enterprise-inference/blob/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd/config.yaml#L44)),
and the agent-toolkit layer sets it
([EAT config.yaml#L24](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/config.yaml#L24)).
Gate 2 served its model at `inference.<base_domain_name>` with a Keycloak token
([upstream-eat.md, probe 5](upstream-eat.md#test-plan)).

The platform, inference and agent-toolkit layers run on the Xeon CPU path.
`kubernetes_accelerator: "cpu"` is the only value with behaviour in EAS today
([global_config.yaml#L106](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/configs/defaults/global_config.yaml#L106);
"Intel GPUs: intel_gpu_operator ... is not available on this branch yet",
[nvidia_gpu_operator/tasks/install.yaml#L20](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/roles/nvidia_gpu_operator/tasks/install.yaml#L20)),
and EAT 2.0 lists "Intel® Arc™ (XPU) deployment" as not in the release
([README.md#L36](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/README.md#L36)).

## Commands

```bash
k3d-gpu eas up [--agent-toolkit] [--eas-ref R] [--ei-ref R] [--eat-ref R] \
               [--model NAME] [--dry-run] [--explain] [-- <ansible-playbook args>]
k3d-gpu eas status     # EAS status, pins, overlay decisions, gateway IP, pods not Ready
k3d-gpu eas check      # with --agent-toolkit: chat through the EAS gateway with a minted LiteLLM key, then the sandbox step
k3d-gpu eas down       # EAS teardown; the cluster stays (k3d-gpu down removes it)
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
  ([resolve_components.py](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/filter_plugins/resolve_components.py#L245)),
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
   [es_auto_installer.sh#L1342-L1358](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L1342-L1358)),
   the `models.yaml` edits model-manager reads, the layer manifest's `rev:` rewritten to
   SHAs, and `choices.txt`.
3. Builds or reuses the bastion image (Ubuntu 24.04, kubectl, helm, yq, a hashed Python
   lock and `kubernetes.core`), tagged by the hash of its Dockerfile and lock.
4. In the bastion (host network, your uid and gid, the kubeconfig read-only): clones EAS
   at the pinned SHA, applies the [overlay](#patch-overlay), pins the layer manifest to
   SHAs ([`_checkout_rev` accepts one](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L658-L693)),
   then runs `init <layer> --env k3d-gpu` and
   `install <layer> --env k3d-gpu -- -e @/eas-state/k3d-gpu-overrides.yaml`.
   `configure` is skipped: it needs a terminal or `--force`, `sudo` and apt
   ([#L318-L352](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L318-L352)),
   and every tool it installs is already in the image. The installer reuses a venv that has
   `ansible-playbook` ([#L801-L806](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L801-L806)).
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
| A k3d-gpu cluster | `existing_kubernetes: /kube/config`, the per-cluster kubeconfig mounted read-only | [EAS global_config.yaml#L14](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/configs/defaults/global_config.yaml#L14); [es_auto_installer.sh#L1460-L1466](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L1460-L1466) |
| k3d-gpu node image: containerd CNI `bin_dirs` is `["/usr/bin"]` | `istio_platform: k3d` (stock k3s needs `k3s`); needs overlay 0002 | [upstream-eat.md, Gate 2 probes 1 and 2](upstream-eat.md#test-plan) |
| ServiceLB on (no `--disable=servicelb` in `k3s.io/node-args`) | `metallb_enabled: false` | [EAS components.yaml#L100-L102](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/configs/components.yaml#L100-L102) |
| A balloons DaemonSet already runs (k3d-gpu baked it for a hybrid or multi-NUMA host), or Docker is rootless | `kubernetes_cpu_policy: best-effort` | [EAS preflight.yaml#L212-L270](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/playbooks/includes/preflight.yaml#L212-L270); Gate 2 probe 6 |
| No NRI policy on the node, NRI available (`K3D_GPU_BALLOONS=external`, or nothing baked) | `kubernetes_cpu_policy: nri-balloons`; needs overlay 0003 | same; [ledger:H14] for EAT's chart values on such a cluster |
| k3d-gpu's NFD runs in `node-feature-discovery` (a k3s manifest, not a Helm release) | `nfd_enabled: false`: EI looks only for a Helm release of that name and would install a second NFD | [EI roles/nfd/tasks/install.yaml#L15-L25](https://github.com/intel/enterprise-inference/blob/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd/roles/nfd/tasks/install.yaml#L15-L25) |
| `feature.node.kubernetes.io/cpu-cpuid.AMXBF16=true` on the node | `require_amx: true` in `models.yaml` | [EI models.yaml#L20](https://github.com/intel/enterprise-inference/blob/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd/model_manager/models.yaml#L20) |
| No AMXBF16 label | `require_amx: false`, so model pods are not left Pending on the AMX node affinity | [EI core/renderer.py#L90-L104](https://github.com/intel/enterprise-inference/blob/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd/model_manager/core/renderer.py#L90-L104); Gate 2 probe 5 |
| No AMX | `VLLM_CPU_SGL_KERNEL` left as shipped: vLLM v0.28.0 checks AMX itself | [vLLM utils.py#L238-L244](https://github.com/vllm-project/vllm/blob/2cf0a6915ce544dc493a0990f2ea38d81601128a/vllm/model_executor/layers/utils.py#L238-L244) |
| No `--cluster-cidr` or `--service-cidr` in `k3s.io/node-args` | `kubernetes_cluster_cidr: 10.42.0.0/16`, `kubernetes_service_cidr: 10.43.0.0/16` (EI's LiteLLM falls back to `10.233.64.0/18` for the pod network it admits, which EAT turns on with OpenShell and asks for on a BYO cluster) | [k3s server.go#L139-L144 at v1.37.1+k3s1](https://github.com/k3s-io/k3s/blob/356c025475bbefaa09cf0d7e48e8e2adbbde57db/pkg/cli/cmds/server.go#L139-L144); [EI litellm defaults#L65](https://github.com/intel/enterprise-inference/blob/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd/roles/litellm/defaults/main.yaml#L65); [EAT config.yaml#L61-L64](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/config.yaml#L61-L64) |
| A BYO cluster | `kata_containers_enabled: false`, set explicitly (EAS's default is already `false`): the role fails on BYO | [EAS kata_containers/tasks/install.yaml#L9-L22](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/roles/kata_containers/tasks/install.yaml#L9-L22) |
| `gpu.intel.com/*` or `habana.ai/gaudi` allocatable | `kubernetes_accelerator: cpu` stays, and the line says "serve the device with k3d-gpu agent up" | [EAT README.md#L36](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/README.md#L36); [EAS nvidia_gpu_operator install.yaml#L20](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/roles/nvidia_gpu_operator/tasks/install.yaml#L20) |
| `--agent-toolkit`, no agent-sandbox CRDs | `agent_sandbox_enabled: true`: EAT installs agent-sandbox `v0.5.0` | [EAT config.yaml#L54-L56](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/config.yaml#L54-L56) |
| `--agent-toolkit`: EAT's router image is the floating `:latest-main` | `agent_sandbox_router_image` and `agent_sandbox_python_runtime_image` pinned by digest in the overrides | [EAT agent_sandbox defaults#L27-L28](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/roles/agent_sandbox/defaults/main.yaml#L27-L28) |
| `--agent-toolkit`: node allocatable memory and `fit` cover EAT's default (about 131 GiB: `qwen2-5-coder-14b` 96, Qwen3Guard 16, router 2, the rest 17) | keep the default model | [EAT single-node-deployment.md#L84](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/docs/single-node-deployment.md#L84) |
| They do not | the first EAT-documented 64 GiB model that `fit` says fits: `qwen2-5-coder-7b`, then `llama-3-1-8b`; its `VLLM_CPU_KVCACHE_SPACE` comes from `fit` | same line |
| Nothing fits | `llm_services_autodeploy_models: []`, with the reason | [EAT config.yaml#L32-L35](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/config.yaml#L32-L35) |
| `--model NAME` | that model, whatever the fit says | |
| `--agent-toolkit` | `guardrails_enabled` and `hybrid_router_enabled` keep EAT's defaults (on) | [EAT config.yaml#L25-L28](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/config.yaml#L25-L28) |

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
both uses. The only overlay-only patch is the `release-1.2` form of 0006,
[integrations/eas/patches/0006-release-1.2-envoy-control-plane-affinity.patch](../../integrations/eas/patches/0006-release-1.2-envoy-control-plane-affinity.patch).
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

| Patch | Needed when | Probe for an upstream fix | `main` profile (EAS `97f6157`) | `v1.2.0-rc.1` profile (EAS `5c27fb3`) |
|---|---|---|---|---|
| 0002 Istio `global.platform` | always | `roles/istio/**` sets `global.platform` from a variable; knob `istio_platform` | apply | apply |
| 0006 Envoy control-plane affinity | always | an `Exists` term on the control-plane key in a file with no exact `""` selector, or `roles/envoy_gateway/vars/k3s.yaml` (the per-distribution layout of EAS #50) | apply, variant a (`roles/envoy_gateway/tasks/controller.yaml`) | apply, variant b (the EnvoyProxy task in [`install.yaml`](https://github.com/intel/enterprise-ai-solutions/blob/5c27fb39a95eecec41aa4e29a1188984e9c726f3/roles/envoy_gateway/tasks/install.yaml#L199-L219)) |
| 0003 nri-plugins chart pin `v0.15.0` | `eas_owns_nri`: `kubernetes_cpu_policy: nri-balloons` | `chart_version:` in `roles/nri_cpu_balloons/tasks/install_advanced.yaml` | skip on a baked-balloons cluster; apply on `K3D_GPU_BALLOONS=external` | same |
| 0004 BYO node topology labels | `multi_node` | `roles/kubernetes/tasks/install.yaml` runs `apply_node_topology.yaml` | skip (one k3d-gpu server) | skip |
| 0005 single-node k3d docs page | `never` (docs only) | none | skip | skip |

`overlay.json` records these decisions as `verified` cases; `overlay.py --check-verified`
re-checks them against an EAS clone, and
`integrations/eat/upstream/checks/apply-check.sh --overlay` does it on a fresh clone. On
2026-10-29 every patch applied on EAS `main` `97f6157`; on `v1.2.0-rc.1` 0002, 0003 and 0004
applied and 0005 and 0006 did not (0006's file, `controller.yaml`, does not exist on
`release-1.2`, which does not contain EAS #42), which is why 0006 has a second variant.

Open EAS PRs that change a decision when they merge (`overlay.py --watch`, checked 2026-10-29):

| EAS PR | Effect on the overlay |
|---|---|
| [#50](https://github.com/intel/enterprise-ai-solutions/pull/50) and [#52](https://github.com/intel/enterprise-ai-solutions/pull/52) (OpenShift) | 0006 no longer applies: the selector moves into `_eg_k8s_provider`, still `control-plane: ""`. That is a STOP naming #50. Once a k3s vars file lands, 0006 becomes drop-fixed |
| [#61](https://github.com/intel/enterprise-ai-solutions/pull/61) (Headlamp SSO) | 0002 fails on its `global_config.yaml` hunk context: a STOP naming #61, so 0002 needs regenerating. 0004 (skipped on one node) and 0005 (docs, never applied) fail there too. 0006 still applies |

## Pins

`integrations/eas/pins.json` (schema `k3d-gpu.eas-pins/v1`) holds flat rows
`{name, repo, ref, sha, date, source}`. Each SHA was the head of its ref on 2026-10-29
(`git ls-remote`).

| Profile | EAS | EI (enterprise-inference) | EAT |
|---|---|---|---|
| `main` (default) | `main` [`97f6157`](https://github.com/intel/enterprise-ai-solutions/commit/97f6157d6a72f7173c698c474d4e51d2c79f1fd6) (#69) | `main` [`3e8a244`](https://github.com/intel/enterprise-inference/commit/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd) (#44) | `release-2.0.0` [`036b662`](https://github.com/intel/enterprise-agent-toolkit/commit/036b66234801e9bd950afd90830821859780d668) (the #19 merge; same tree as `agentic-toolkit-migration` `fb59459`) |
| `v1.2.0-rc.1` (`--eas-ref v1.2.0-rc.1`) | tag `v1.2.0-rc.1` [`5c27fb3`](https://github.com/intel/enterprise-ai-solutions/commit/5c27fb39a95eecec41aa4e29a1188984e9c726f3) | `release-1.1` [`170eb3a`](https://github.com/intel/enterprise-inference/commit/170eb3abb1956de0787727f21e356aef2b13ff24) | `release-2.0.0` `036b662` |

- The `main` triple is what EAS `main` itself clones today: inference `rev: "main"` and
  agent-toolkit `rev: "agentic-toolkit-migration"`
  ([repos.agent-toolkit.yaml#L12 and #L21](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/configs/repos/repos.agent-toolkit.yaml#L9-L24)).
  The pins freeze that combination.
- The `v1.2.0-rc.1` triple is the component table of the
  [v1.2.0-rc.1 release notes](https://github.com/intel/enterprise-ai-solutions/releases/tag/v1.2.0-rc.1),
  Intel's pre-release vehicle for EAT 2.0.
- Any other `--eas-ref`, `--ei-ref` or `--eat-ref` is resolved to a SHA with
  `git ls-remote` when `eas up` runs, printed, and saved in the state dir. A floating ref
  never reaches the installer.

Bastion tools (each binary checked against its upstream checksum file at build time):

| Tool | Pin | Why this version |
|---|---|---|
| kubectl | v1.37.2 | the minor of the node's k3s (`K3S_TAG` v1.37.1-k3s1); EAS's own v1.34.3 ([#L52](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L52)) is three minors off, outside the [kubectl skew policy](https://kubernetes.io/releases/version-skew-policy/#kubectl) |
| helm | v3.22.1 | latest v3; EAS's floor is v3.20.2 ([#L53](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L53)). Helm v4 is untested with `kubernetes.core` 6.3.0 |
| yq | v4.54.1 | EAS's floor is v4.53.2 ([#L51](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L51)) |
| jq | jq-1.8.2 | latest; enterprise-inference pipes yq into jq on the installer host to pick the models to deploy ([deploy_models.yaml#L108-L114](https://github.com/intel/enterprise-inference/blob/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd/roles/llm_services/tasks/deploy_models.yaml#L108-L114)), and EAS's `configure` installs no jq |
| ansible, kubernetes (Python) | 12.3.0, 35.0.0, hashed | newest inside EAS's ranges `ansible>=10.0,<13.0`, `kubernetes>=29.0,<36.0` ([requirements.txt](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/requirements.txt)) |
| kubernetes.core | 6.3.0 | EAS's [collections/requirements.yml](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/collections/requirements.yml) |

To move a pin:

1. Read the new head: `git ls-remote https://github.com/intel/<repo> <ref>`.
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
| 48 cores minimum, 96+ recommended | [prerequisites.md#L7](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/docs/prerequisites.md#L7) | warns and goes on; CPU requests that do not fit leave pods Pending, which `eas status` lists |
| 32 GB RAM minimum, 64 GB recommended | [prerequisites.md#L8](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/docs/prerequisites.md#L8) | sizes the model from node allocatable memory and `fit` (the default install requests about 131 GiB; the fallbacks 64 GiB each, [single-node-deployment.md#L84](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/docs/single-node-deployment.md#L84)) |
| `fs.inotify.max_user_instances = 8192`, `max_user_watches = 1048576` | [prerequisites.md#L21-L34](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/docs/prerequisites.md#L21-L34) | warns and prints the `sysctl` line to run |

## Known issues

`integrations/eas/known-issues.json` holds these rows as `{id, upstream, symptom, workaround, source, checked}`.

| Issue | Symptom | What `k3d-gpu eas` does |
|---|---|---|
| EAS [#71](https://github.com/intel/enterprise-ai-solutions/issues/71) (open) | `teardown platform` hangs on the Envoy AI Gateway CRD uninstall once a model was deployed | `eas down` clears the `AIGatewayRoute` finalizers first, then runs `teardown platform --env k3d-gpu --force` (teardown refuses a non-terminal run without `--force`, [#L1486](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L1481-L1487)) |
| `require_amx: true` in EI's catalog | on a host without AMX every model pod stays Pending | writes `require_amx: false` when the node has no AMXBF16 label |
| kubectl skew | EAS installs kubectl v1.34.3, three minors behind k3s v1.37 | the bastion carries kubectl v1.37.2 |
| inotify minimum | file watchers fail with `too many open files` under the kernel default | warns and prints the `sysctl` line |
| 48-core minimum | under it, CPU requests may not fit | warns; `eas status` lists Pending pods |
| `--skip-models` in EI's docs ([config.yaml#L97](https://github.com/intel/enterprise-inference/blob/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd/config.yaml#L97)) | EAS's installer has no such flag and stops on it ([#L1312](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L1312)) | nothing to replace on the inference layer: EI's seeded config already has `llm_services_deploy_models: false` ([config.yaml#L98](https://github.com/intel/enterprise-inference/blob/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd/config.yaml#L98)). With `--agent-toolkit`, EAT's chat model and the Qwen3Guard guard model deploy whatever that key says ([EI llm_services install.yaml#L143-L161](https://github.com/intel/enterprise-inference/blob/3e8a24486b6ee812adf2bf452f8ba5751c16c8bd/roles/llm_services/tasks/install.yaml#L143-L161)); `k3d-gpu eas up --agent-toolkit -- -e '{"llm_services_autodeploy_models": []}'` skips the chat model ([EAT config.yaml#L34](https://github.com/intel/enterprise-agent-toolkit/blob/036b66234801e9bd950afd90830821859780d668/config.yaml#L34)), and the guard model stays while the guardrail is on |

## State and secrets

- State lives in `${STATE_DIR}/eas-<cluster>/` (mode 0700): the EAS checkout, `env/k3d-gpu`,
  logs, `choices.txt`, `overlay.txt` and the resolved pins.
- The Keycloak admin password is generated by EAS
  ([keycloak defaults#L28](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/roles/keycloak/defaults/main.yaml#L28)).
  `HF_TOKEN` is passed to the bastion by name when it is set. No secret is put on a
  command line or in a log.
- `eas check` (after `--agent-toolkit`, the layer that brings LiteLLM) mints a LiteLLM
  virtual key from the `litellm-master-key` Secret (namespace `litellm`), uses it once
  against the gateway, and deletes it.

## Limits

- **Xeon CPU path only.** That is what EAS and EAT ship for a cluster like this one
  (sources [above](#what-full-eas-covers)). An Arc GPU or a Gaudi on the node is not used by
  EAS; serve it with `k3d-gpu agent up`. Registering such an engine behind EAS's LiteLLM,
  as [`eat-remote.sh`](../../integrations/eat/README.md) does for EAT 1.x, is a follow-up.
- **ERAG is not wired in yet.** EAS wires Intel Enterprise RAG in as a further layer
  ([repos.erag.yaml](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/configs/repos/repos.erag.yaml#L22-L28)),
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
- **Not yet observed.** The EAT 2.0 layer on k3s or k3d; agent-sandbox v0.5.0 on
  Kubernetes 1.37 (EAS tests 1.34.3); the CPU requests of the whole stack on a 32-CPU
  desktop; helm v3.22.1 with `kubernetes.core` 6.3.0; and whether any BYO task needs `sudo`
  inside the bastion. The installer itself probes `sudo -n true` before every `install` and,
  without passwordless sudo, runs `ansible-playbook` with `--ask-become-pass`
  ([es_auto_installer.sh#L866-L875](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L866-L875),
  [#L1179](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L1179),
  [#L1423](https://github.com/intel/enterprise-ai-solutions/blob/97f6157d6a72f7173c698c474d4e51d2c79f1fd6/es_auto_installer.sh#L1423)),
  which a bastion with no terminal cannot answer. The `eas` CLI PR's wiring smoke settles
  these on a throwaway cluster; F3 is the scored run.

## Related

| If you want to… | Go to |
|---|---|
| Run EAT 1.x (`release-1.1.0`) on k3d-gpu | [integrations/eat](../../integrations/eat/README.md), [intel.md](../intel.md#run-intel-enterprise-agent-toolkit-on-one-machine) |
| See the upstream patch drafts and their checks | [upstream-eat.md](upstream-eat.md) |
| See EAT's PR history and pins | [platforms.md](../platforms.md#intel-enterprise-agent-toolkit-eat) |
| See the F3 pre-registration | [EXPERIMENTS.md](../../EXPERIMENTS.md#f3-pre-registration-i9-13900t-2026-10-29) |
