# GPUStack on k3d-gpu

[GPUStack](https://github.com/gpustack/gpustack) is a GPU cluster manager and
model-serving control plane. This page says what works on a k3d-gpu cluster
and what does not, with the evidence for each claim.

Everything here was checked against these pinned upstream versions (October 2026):

| Component | Version | Notes |
|-----------|---------|-------|
| GPUStack | **v2.2.3** (latest stable, 2026-07-31) | `v2.3.0rc2` is a pre-release |
| Helm chart | `oci://registry-1.docker.io/gpustack/gpustack-chart` **2.2.3** | digest `sha256:b660ab0ba7224def2aa12559803c776272dd854c7af264dee2529772f9299641` |
| Server/worker image | `gpustack/gpustack:v2.2.3` | index digest `sha256:55ecc762950a32641b6a4d96195c0662ef72998eaeeda31bba0e8e218d41afef` |
| gpustack-runtime (device detection) | 0.2.1 (pinned by v2.2.3); 0.2.6 on `main` | |
| gpustack-operator | v0.5.4 (pinned by v2.2.3) | installs NFD 0.18.3 and Kueue 0.17.6 |
| k3d-gpu node | k3s `v1.34.1-k3s1` | chart needs Kubernetes >= 1.30 |

## Summary

| Accelerator on the k3d-gpu node | GPUStack can schedule onto it? |
|---|---|
| NVIDIA GPU (`nvidia.com/gpu`) | **Yes.** Recipe A below. One change: hand `nvidia.com/gpu` to GPUStack's device plugin. |
| Intel GPU (`gpu.intel.com/xe`, `gpu.intel.com/i915`) | **No.** GPUStack has no Intel GPU support (see below). Use Recipe B: GPUStack routes to an OVMS endpoint that runs on the Intel GPU. |
| Intel NPU (`npu.intel.com/accel`) | **No.** Same reason. Recipe B works for an NPU-backed OVMS too. |
| Intel Gaudi (`habana.ai/gaudi`) | **No.** Upstream issue [#1205](https://github.com/gpustack/gpustack/issues/1205) is open. |
| Any of the above, GPUStack outside the cluster | Possible (Recipe C), but GPUStack and k3s then share the GPU with no common accounting. |

## Why Intel accelerators are not compatible

GPUStack v2 has no Intel (or Habana) device support in any of its layers:

- **Device detection.** `gpustack-runtime` has one detector per vendor. The
  `ManufacturerEnum` lists `amd`, `ascend`, `cambricon`, `hygon`, `iluvatar`,
  `metax`, `mthreads`, `nvidia`, `thead`. There is no Intel entry, in 0.2.1
  (shipped by v2.2.3) or in 0.2.6 (`main` and `v2.3.0rc2`). A grep for
  `intel`, `xpu`, `i915`, `habana` or `level_zero` finds nothing relevant.
- **Kubernetes operator.** `gpustack-operator` has detectors and device-plugin
  allocators for the same nine vendors only, in v0.5.4 and in v0.9.0 (latest
  tag). It has no allocator for `gpu.intel.com/*` or `habana.ai/gaudi`.
- **Helm chart.** `worker.gpuVendors` accepts `nvidia`, `mthreads`, `amd`,
  `ascend`, `hygon`, `metax`, `iluvatar`, `cambricon`, `thead`. An unknown value
  is dropped without an error. Rendering chart 2.2.3 with
  `--set worker.enabled=true --set 'worker.gpuVendors={intel}'` produces only
  the CPU worker DaemonSet (`nodeSelector: feature.gpustack.ai/acceleratable: "false"`).
- **Docs.** The [installation requirements](https://github.com/gpustack/gpustack/blob/v2.2.3/docs/installation/requirements.md)
  list NVIDIA, AMD, Ascend, Hygon, MetaX, MThreads, Iluvatar, Cambricon and T-Head. No Intel.
- **Upstream intent.** [#1088 "Support for Intel XPU such as the Arc A770"](https://github.com/gpustack/gpustack/issues/1088)
  is open with no maintainer reply. On [#1205 (Gaudi)](https://github.com/gpustack/gpustack/issues/1205)
  a maintainer said they have no Gaudi hardware and invited a detector PR against
  [gpustack/runtime](https://github.com/gpustack/runtime).

Adding Intel support means new code upstream: a detector in `gpustack-runtime`,
a detector and allocator in `gpustack-operator`, and an inference backend image
built for Intel GPUs (for example vLLM XPU or OVMS). That is out of scope here.

The repo owner's copy, [88plug/gpustack](https://github.com/88plug/gpustack),
is a standalone repo (not a GitHub fork). Its `main` is at the v2.2 line
(`gpustack-runtime==0.2.1`, operator v0.5.4) and adds no Intel support. Its
branch `fix/5122-k8s-nvidia-runtime` adds `runtimeClassName: nvidia` to
generated inference pods ([#5122](https://github.com/gpustack/gpustack/issues/5122)).
k3d-gpu does not need that fix: `k3d-gpu up` sets `--default-runtime=nvidia`, so
every pod on the node already runs under the NVIDIA runtime.

## Recipe A: GPUStack inside the cluster, on NVIDIA

This uses the upstream Helm chart. The chart deploys the GPUStack server, a
Higress gateway, and `gpustack-operator`. The operator then installs Node
Feature Discovery, Kueue and its own per-node device manager.

What k3d-gpu already gives the chart:

- Traefik is disabled (`--disable=traefik`), so Higress can own ingress.
- k3s ships a `RuntimeClass` named `nvidia`. The chart's NVIDIA worker uses
  `runtimeClassName: nvidia`. The operator only creates that RuntimeClass when
  none exists, so it leaves the k3s one alone.
- k3s's default `local-path` StorageClass backs the server's data volume.

**The one conflict: two device plugins for `nvidia.com/gpu`.** k3d-gpu bakes the
NVIDIA device plugin into the node. GPUStack's device manager registers its own
plugin for the same resource name in exclusive mode (`nvidia.com/gpu`), plus
`nvidia.com/gpu.shared` and `nvidia.com/gpu.sliced.units`. The kubelet keeps one
plugin endpoint per resource name (the latest to register wins), so the two
would replace each other. Turn off the
baked plugin first. Its DaemonSet only runs on nodes where
`nvidia.com/gpu.present` is not `false`:

```bash
k3d-gpu up                         # cluster with NVIDIA active
kubectl label nodes --all nvidia.com/gpu.present=false --overwrite
kubectl -n kube-system get ds nvidia-device-plugin-daemonset   # DESIRED drops to 0
```

Install the chart, pinned (Helm >= 3.18.4):

```bash
CHART=oci://registry-1.docker.io/gpustack/gpustack-chart
CHART_VERSION=2.2.3

helm install gpustack "$CHART" --version "$CHART_VERSION" \
  --namespace gpustack-system --create-namespace \
  --set worker.enabled=true \
  --set 'worker.gpuVendors={nvidia}'
```

The NVIDIA worker DaemonSet has `nodeSelector:
feature.node.kubernetes.io/pci-10de.present: "true"`. NFD sets that label once
the operator has installed it, so the worker stays `Pending` for the first few
minutes. That is expected.

Open the UI and get the admin password:

```bash
kubectl -n gpustack-system exec gpustack-server-0 -- \
  cat /var/lib/gpustack/initial_admin_password
kubectl -n gpustack-system port-forward svc/gpustack-server 8080:30080
# browse http://localhost:8080, user "admin"
```

`k3d-gpu` creates the cluster with `--no-lb`, so the `higress-gateway`
LoadBalancer Service is served by k3s's built-in service load balancer on the
node container's address, not on a host port. Use `port-forward` (above) or
`kubectl -n gpustack-system port-forward svc/higress-gateway 8081:80` from the host.

Things to know:

- **Model storage is ephemeral.** The worker keeps models in the hostPath
  `/var/lib/gpustack`. On k3d that path is inside the node container, so it is
  lost when the cluster is deleted (`k3d-gpu down`).
- **`k3d-gpu test` still works.** It asks for `nvidia.com/gpu: 1`, which
  GPUStack's device manager now advertises. GPUStack's own model pods also
  pass through Kueue admission against the operator's per-node GPU quota. Kueue does not count pods it does not manage, so while a manual pod
  holds a GPU, GPUStack can admit a model whose pod then waits `Pending`.
- **Hybrid P/E pinning.** Pods that GPUStack creates carry no
  `k3d-gpu.io/cores` label, so they land in the `shared` balloon. That is
  harmless; they just are not pinned.
- **Removing GPUStack.** `helm uninstall gpustack -n gpustack-system`, then
  `kubectl label nodes --all nvidia.com/gpu.present-` to bring back the baked
  plugin. The server PVC stays until you delete it (see the
  [upstream uninstall notes](https://github.com/gpustack/gpustack/blob/v2.2.3/docs/installation/helm.md#uninstallation)).

The GPUStack UI can also generate these manifests ("Clusters → Add Cluster →
Kubernetes"). That path renders the same worker DaemonSets and operator from
`gpustack/k8s/manifest_template.py`, so the same device-plugin step applies.

## Recipe B: Intel GPU or NPU through an OVMS provider

GPUStack cannot place workloads on Intel devices. It can still sit in front of a
model server that does. GPUStack v2 proxies "model providers" through Higress,
and its `openai` provider type takes a custom base URL. OpenVINO Model Server
2026.4.1 runs on `gpu.intel.com/xe` (or the NPU) in the k3d-gpu cluster and
speaks the OpenAI API under `/v3`.

Assumed endpoint (k3d-gpu agent stack): `http://ovms.<ns>.svc:8000/v3`, serving
for example `OpenVINO/Qwen3-4B-int4-ov` with tool calling.

In the GPUStack UI (Recipe A, or any GPUStack server that can resolve
cluster DNS):

1. **Models → Providers → Add Provider.**
2. Type **OpenAI**. Set the custom URL to `http://ovms.<ns>.svc:8000/v3`.
3. API key: any non-empty string. GPUStack requires at least one token. OVMS
   ignores the header unless it runs with an API key file.
4. Add the model. GPUStack lists models from `GET /v3/models`, which OVMS
   2026.4.1 serves ([OVMS client docs](https://github.com/openvinotoolkit/model_server/blob/v2026.4.1/docs/clients_genai.md)).
5. Add a route for the provider. Clients then call GPUStack's
   `/v1/chat/completions` with a GPUStack API key.

How GPUStack maps that URL (from `gpustack/schemas/model_provider.py`, v2.2.3):
`OpenAIConfig.get_base_url()` keeps scheme and host (`http://ovms.<ns>.svc:8000`).
The model list goes to `<path>/models` and chat to `<path>/chat/completions`, so
`/v3/models` and `/v3/chat/completions`. `gpustack/gateway/utils.py`
turns an `http://` hostname URL into a Higress `McpBridge` registry of type
`dns` on port 8000.

Not verified: an end-to-end run, and whether Higress's `ai-proxy` plugin
passes `tools` and `tool_calls` through unchanged. Check both before relying
on tool calling through GPUStack.

## Recipe C: GPUStack beside the cluster

GPUStack can also run as plain Docker containers on the host, next to the k3d
cluster, following the [upstream quickstart](https://github.com/gpustack/gpustack/blob/v2.2.3/docs/quickstart.md):

```bash
IMAGE=gpustack/gpustack
TAG=v2.2.3
sudo docker run -d --name gpustack --restart unless-stopped \
  -p 80:80 --volume gpustack-data:/var/lib/gpustack "$IMAGE:$TAG"
```

The worker container (`--runtime nvidia --privileged --network=host`, plus the
Docker socket) then starts inference containers straight on the host's Docker.
k3d-gpu's `--no-lb` means no k3d container holds host port 80.

The catch: GPUStack and k3s both see every NVIDIA GPU and neither knows about
the other's allocations, so they can over-commit VRAM. Use Recipe A when both
share one GPU. As above, a Docker worker gains nothing on an Intel-only host,
because GPUStack does not detect Intel devices there either.
