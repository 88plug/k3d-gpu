# k3d-gpu

[![AUR](https://img.shields.io/aur/version/k3d-gpu?label=AUR&style=flat-square)](https://aur.archlinux.org/packages/k3d-gpu)
[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat-square)](LICENSE.md)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/88plug/k3d-gpu)

A Docker-based [rancher/k3s](https://hub.docker.com/r/rancher/k3s) node image on an [Ubuntu](https://hub.docker.com/_/ubuntu) base with the NVIDIA container toolkit baked in, so a k3d cluster can schedule your host’s accelerators — **NVIDIA GPUs, Intel Arc / Xe2 (Battlemage) GPUs, and Intel Gaudi 3 (HPU)** — exposed on `up` with no `kubectl apply`. The launcher auto-detects what the host has. Built for **Ubuntu 26.04** (default) and **24.04**.

> **Intel platforms:** one `k3d-gpu up` detects and schedules `i915` Intel GPUs
> and pins pods to P-cores or E-cores on hybrid CPUs; both have run on a real
> i9-13900T host. It also detects and schedules `xe` Intel GPUs, the Core Ultra
> NPU, Gaudi 3 and Xeon DSA / IAA / QAT / AMX, which so far are tested only
> against a simulated sysfs. See **[docs/intel.md](docs/intel.md)** for the capability matrix, how
> P/E pinning works, and how it compares with other local Kubernetes.

---

## Table of Contents

1. [Quick Start (k3d-gpu CLI)](#quick-start-k3d-gpu-cli)  
2. [Features](#features)  
3. [Prerequisites](#prerequisites)  
4. [Environment Variables](#environment-variables)  
5. [Building & Pushing the Image](#building--pushing-the-image)  
6. [k3d Cluster Setup](#k3d-cluster-setup)  
7. [Testing GPU Access](#testing-gpu-access)  
8. [References](#references)  
9. [Contributing](#contributing)  
10. [Release History](#release-history)  
11. [License](#license)  

---

## Quick Start (k3d-gpu CLI)

The Arch package installs a `k3d-gpu` launcher that wraps the whole workflow — no
need to remember `k3d cluster create` flags:

```bash
yay -S k3d-gpu          # or build from packaging/aur/PKGBUILD

k3d-gpu detect          # list accelerators on this host and their k8s resources
k3d-gpu doctor          # preflight: accelerators, kernel/firmware, docker, runtimes, k3d, kubectl
k3d-gpu host-setup      # check host drivers/userspace, print pinned install commands (--apply runs them)
k3d-gpu doctor --perf   # read-only host tuning advisor for inference (add --model-gb N)
k3d-gpu report          # what the host and the cluster have, per engine (text + JSON + HTML)
k3d-gpu up              # create the cluster (device plugins auto-deploy), verify each accelerator > 0
k3d-gpu test            # per accelerator: nvidia-smi pod / device-node listing pod
k3d-gpu logs            # tail the k3s server container logs
k3d-gpu agent up        # OpenAI-compatible LLM server on the Intel GPU (else CPU)
k3d-gpu down            # delete the cluster
```

Behaviour is tunable via environment variables:

| Variable             | Default                                   | Description                          |
|----------------------|-------------------------------------------|--------------------------------------|
| `K3D_GPU_CLUSTER`    | `gpu`                                     | cluster name                         |
| `K3D_GPU_IMAGE`      | `cryptoandcoffee/k3d-gpu:latest`          | node image (`latest` = ubuntu26.04)  |
| `K3D_GPU_SHARE`      | `/usr/share/k3d-gpu`                      | dir of fallback plugin manifests     |
| `K3D_GPU_VENDORS`    | `auto`                                    | `auto` detects; or a list of `nvidia,intel,npu,gaudi,dsa,iaa,qat,amx,hybrid,numa` |
| `K3D_GPU_NODE_LABELS` | none                                    | extra node labels, comma list of `key=value` (e.g. `ei-infra-eligible=true,ei-inference-eligible=true`) |
| `K3D_GPU_K3S_ARGS`   | none                                      | extra k3s server flags, space-separated (e.g. `--disable=servicelb`) |
| `K3D_GPU_PLUGIN`     | `/usr/share/k3d-gpu/nvidia-device-plugin.yml` | fallback manifest (only used if a custom image lacks the baked one) |
| `K3D_GPU_INTEL_PLUGIN` | `/usr/share/k3d-gpu/intel-gpu-plugin.yml` | same, for the Intel GPU plugin |
| `K3D_GPU_GAUDI_PLUGIN` | `/usr/share/k3d-gpu/gaudi-device-plugin.yml` | same, for the Gaudi plugin |
| `K3D_GPU_NPU_PLUGIN` | `/usr/share/k3d-gpu/intel-npu-plugin.yml` | same, for the Intel NPU plugin |
| `K3D_GPU_DSA_PLUGIN` / `_IAA_` / `_QAT_` | `/usr/share/k3d-gpu/intel-{dsa,iaa,qat}-plugin.yml` | same, for the Xeon accelerator plugins |
| `K3D_GPU_HYBRID_PLUGIN` | `/usr/share/k3d-gpu/nri-balloons.yml` | same, for the NRI balloons policy (hybrid and NUMA hosts) |
| `K3D_GPU_AGENT_*`    | see [Agent](#agent-an-openai-compatible-llm-on-the-best-intel-engine) | `k3d-gpu agent` settings |
| `K3D_GPU_DEVICE_TEST_IMAGE` | `busybox:1.37`                     | image used by `k3d-gpu test` for Intel GPU/NPU and Gaudi |
| `K3D_GPU_TEST_IMAGE` | `nvidia/cuda:13.4.2-base-ubuntu26.04`     | image used by `k3d-gpu test`         |

The rest of this README documents the underlying image and the manual `k3d`
commands the launcher runs for you.

---

## Features

- K3s + NVIDIA Container Toolkit on an Ubuntu base — **26.04** (default) and **24.04**  
- NVIDIA, **Intel GPU** (Arc, Battlemage, Core Ultra integrated GPUs, Crescent Island — `xe` or `i915`), **Intel NPU** (Core Ultra) and **Intel Gaudi 3 (HPU)** device plugins **baked into k3s auto-deploy** — `up` exposes them with no `kubectl apply`. The launcher labels nodes so each plugin only runs where its hardware is  
- Auto-detection on the host from sysfs: `/dev/nvidia*`, Intel DRM render nodes (`xe`/`i915`), and `/dev/accel/accel*` nodes told apart by PCI vendor (`8086` + `intel_vpu` = NPU, `1da3` = Gaudi). `k3d-gpu detect` prints what it found  
- `k3d-gpu doctor` checks kernel and firmware per platform, names the `xe.force_probe=<id>` needed for parts newer than your kernel, and checks Xeon built-in accelerators (AMX, DSA, IAA, QAT) — deployed automatically once the host side is configured  
- [Node Feature Discovery](#node-feature-discovery) with Intel's NodeFeatureRules on every node: CPU flags (`cpu-cpuid.*`), kernel, PCI and `intel.feature.node.kubernetes.io/*` labels read from the host  
- CDI-ready: containerd 2.x scans `/etc/cdi` and `/var/run/cdi` (CDI on by default), so future vendors that emit CDI specs need no node changes  
- Pre‑configured nvidia containerd runtime; `--default-runtime=nvidia` for zero-config GPU pods  
- No CUDA toolkit in the node image — driver libs are injected from the host, workloads bring their own CUDA  
- Exposes the standard K3s entrypoint (`/bin/k3s agent`); volumes for kubelet, k3s state, CNI, logs  
- Tunable via build arguments for the K3s and Ubuntu versions  

---

## Supported accelerators

| Vendor | Hardware | Kubernetes resource | Host requirement |
|--------|----------|---------------------|------------------|
| NVIDIA | any CUDA GPU | `nvidia.com/gpu` | driver + nvidia-container-toolkit, Docker `--gpus` |
| Intel  | Arc **Battlemage** (Xe2, `xe`) | `gpu.intel.com/xe` | kernel 6.12+, linux-firmware with `xe/bmg_guc_*` |
| Intel  | Core Ultra iGPU: **Lunar Lake** (Xe2) / **Panther Lake** (Xe3) / Wildcat Lake (`xe`) | `gpu.intel.com/xe` | kernel 6.12+ / 6.17+ / 6.18+ |
| Intel  | Core Ultra iGPU: **Meteor Lake** / **Arrow Lake** (`i915`), Arc / Flex / Max on `i915` | `gpu.intel.com/i915` | kernel 6.7+ / 6.9+ |
| Intel  | **Crescent Island** (Xe3P data-center GPU), Nova Lake | `gpu.intel.com/xe` | `xe` driver; still needs `xe.force_probe=<id>` (see `doctor`) |
| Intel  | **NPU** in Core Ultra: Meteor / Arrow / Lunar / Panther / Wildcat Lake | `npu.intel.com/accel` | `intel_vpu` driver (kernel 6.3+ MTL … 6.13+ PTL), `intel/vpu/vpu_*` firmware |
| Intel  | **Gaudi 3** (HPU, PCI `1da3`) | `habana.ai/gaudi` | Intel's out-of-tree `habanalabs` driver (mainline stops at Gaudi 2), `/dev/accel/accel*` |

k3d node containers are privileged, so host `/dev/dri` and `/dev/accel` are
visible with no extra flag; the vendor plugins hand the device nodes to pods.
Devices must exist when the cluster is created — recreate the cluster after
loading a driver. Request them in a pod like any extended resource:
`resources.limits: {gpu.intel.com/xe: 1}`, `{npu.intel.com/accel: 1}` or `{habana.ai/gaudi: 1}`.
Workload images bring their own user space: Intel compute-runtime / Level Zero
for GPUs, the [NPU driver](https://github.com/intel/linux-npu-driver) + OpenVINO
for the NPU. Gaudi runs in the plugin's plain mode, which hands pods the devices
without the habana container runtime; full Gaudi workloads may still expect it.

Force or limit vendors with `K3D_GPU_VENDORS=nvidia,intel,npu,gaudi,dsa,iaa,qat,amx,hybrid,numa`.
A loaded `nvidia` or `habanalabs` kernel module counts as present even before
its device nodes appear; `doctor` then flags the missing nodes.

### Host tuning advisor (`doctor --perf`)

`k3d-gpu doctor --perf [--model-gb N]` reads sysfs/procfs (plus `docker info` and
`nvidia-smi`) and reports what matters for inference serving: CPU governor and EPP
per core type, turbo and frequency caps, `hwp_dynamic_boost`, idle-state exit
latencies, RAPL PL1/PL2 against the thermal spec power, THP and hugetlb pages,
swap in use and `vm.swappiness`, irqbalance, Intel GPU frequency (i915 RPS / GuC
SLPC, xe `freq0`), NVIDIA persistence, the Docker storage driver, and whether a
model of N GiB fits in `MemAvailable`. It also reports thermal throttling (the
kernel's throttle counters, the TCC activation point, and as root a decode of
MSRs `0x19C`/`0x1B1`/`0x64F` that leaves their sticky log bits alone), a PL1 far
above TDP on a package that throttles, DRAM configured vs rated vs platform POR
speed with the computed peak bandwidth and ECC/EDAC state (SMBIOS type 17, root),
4 KiB mappings of model files on ext4 before Linux 6.16, KSM, each GPU's PCIe link
against its upstream port, Resizable BAR, NVIDIA peer-to-peer, sandbox isolation
(`/dev/kvm`, IOMMU groups, CET, split-lock detection, TME, the GPU observation
sysctls), power-profiles-daemon rewriting EPP, and the i915 RP1 floor and
waitboost count. Each finding names the file it came from
and cites its source; each suggestion prints a one-line apply command and the
command that restores the value it found. It never writes to the host, and it
exits 0 — the suggestions are advice, not failures.

### Xeon built-in accelerators

Auto-detected too. `up` deploys their plugins once the host side is set up — the
launcher never changes host configuration itself; `k3d-gpu doctor` says exactly
what is missing.

| Vendor key | Accelerator | Active when the host has | Kubernetes exposes |
|------------|-------------|--------------------------|--------------------|
| `amx`  | **AMX** (Sapphire Rapids and newer) | `amx_*` CPU flags | node labels `feature.node.kubernetes.io/cpu-cpuid.AMXTILE=true` etc. (NFD names) — no device needed |
| `dsa`  | **DSA** (`idxd`) | user work queues `/dev/dsa/wq*` (`accel-config`) | `dsa.intel.com/wq-user-dedicated` / `wq-user-shared` |
| `iaa`  | **IAA** (`idxd`) | user work queues `/dev/iax/wq*` (`accel-config`) | `iaa.intel.com/wq-user-dedicated` / `wq-user-shared` |
| `qat`  | **QAT** C62x, 4xxx, 420xx, 6xxx | PF bound to its QAT driver with SR-IOV VFs (`sriov_numvfs`), `vfio-pci` loaded, IOMMU on | `qat.intel.com/cy`, `dc`, … (per the PF's `cfg_services`) |

For DSA/IAA the launcher bind-mounts the host's `/dev/char` into the node: the
plugin only accepts a work queue whose udev `/dev/char/<major>:<minor>` link
exists, and a privileged container has none of its own. QAT userspace (qatlib)
needs locked memory in the workload pod. The DLB plugin was removed upstream in
intel-device-plugins v0.37.0, so DLB is not supported.

For what these accelerators can do for an agent data plane today (Redis with DTO,
pgvector backups through zlib-accel, zswap on IAA, Envoy and Istio TLS on QAT), the
pod requirements and the probes still to run on Sapphire Rapids, see
[docs/accelerated-dataplane.md](docs/accelerated-dataplane.md) and
[examples/dataplane/](examples/dataplane/).

`doctor` also counts DSA, IAA and QAT PCI functions per socket (by PCI id, so a
device counts before its driver loads) and prints the SKU's
"default devices" from its ark.intel.com page when the CPU is in its table
(8480+, 6980P, 6960P, 6780E). A socket below that default gets a warning; the
BIOS may have the device disabled. When `/sys/bus/auxiliary/devices/intel_vsec.sdsi.*`
exists, `doctor` notes the Intel On Demand interface. It never writes to it.

### Node labels from the host

`up` also labels the node with hardware facts a pod can select on. No flag
turns them on; `k3d-gpu doctor` prints the labels this host gets.

| Label | Set when | Source |
|-------|----------|--------|
| `feature.node.kubernetes.io/cpu-cpuid.<NAME>=true` | always, for each of `avx_vnni`, `avx512f`, `avx512_vnni`, `avx512_bf16`, `vaes`, `gfni`, `sha_ni`, `ibt`, `user_shstk` in `/proc/cpuinfo` | NFD's names: `AVXVNNI`, `AVX512F`, `AVX512VNNI`, `AVX512BF16`, `VAES`, `GFNI`, `SHA`, `CETIBT`, `CETSS` |
| `gpu.intel.com/xmx=true\|false` | `intel` is active; `true` if any Intel GPU has XMX matrix engines, `false` only when every one is known to lack them | per platform, from Mesa's `has_systolic` |
| `gpu.intel.com/media-vcs=<n>`, `gpu.intel.com/media-vecs=<n>` | `intel` is active on `i915` | video decode/encode and video enhancement engines under `/sys/class/drm/card*/engine` |
| `k3d-gpu.io/npu.gen=37xx\|40xx\|50xx\|60xx` | `npu` is active | the NPU's firmware generation |

Kernels do not print `avx_ifma`, `amx_fp16`, `avx_vnni_int8`, `avx_vnni_int16` or
`avx_ne_convert` in `/proc/cpuinfo`, so they get no label here. NFD has no `pku`
feature, so `pku` gets none either. The `xe` driver has no per-engine sysfs
directory, so `xe` GPUs get no media labels.

### Hybrid P-core / E-core placement

On Intel hybrid CPUs (Alder Lake and newer client parts, e.g. Core i9-13900),
`up` deploys the [NRI balloons policy](https://github.com/containers/nri-plugins)
so a pod picks its core type with one label:

```yaml
metadata:
  labels:
    k3d-gpu.io/cores: performance   # or: efficient
spec:
  containers:
  - name: app
    resources: {requests: {cpu: "4"}, limits: {cpu: "4"}}   # requests size the balloon
```

| Vendor key | Active when the host has | Kubernetes exposes |
|------------|--------------------------|--------------------|
| `hybrid` | online CPUs in both `/sys/devices/cpu_core` and `/sys/devices/cpu_atom` | node label `k3d-gpu.io/hybrid-cpu=true`; pod label `k3d-gpu.io/cores=performance\|efficient` pins to P- or E-cores |

Unlabelled pods land in a `shared` balloon that uses every idle CPU, so nothing
else loses capacity. kube-system runs on a one-CPU reserve. k3s turns NRI on in
containerd for a privileged node, and the node sees the host's core-type PMUs.
The policy only changes cpusets inside the node's cgroup. It never touches
cpufreq, C-states or RDT. Only one NRI resource policy can own a node, so drop
`hybrid` (and `numa`) from `K3D_GPU_VENDORS` if another stack installs its own (for example,
Intel's Enterprise Agent Toolkit CPU balloons).

### NUMA placement (multi-socket and SNC Xeon)

A host with more than one NUMA node (two sockets, Sub-NUMA Clustering, or HBM/CXL
memory nodes) turns on the `numa` vendor. `up` labels the node and deploys the same
NRI balloons policy, so a pod asks for one NUMA node with one label:

```yaml
metadata:
  labels:
    k3d-gpu.io/cores: replica
spec:
  containers:
  - name: llm
    resources: {requests: {cpu: "16"}, limits: {cpu: "16"}}
```

Each `replica` pod gets its own balloon inside one NUMA node. Its memory is pinned to
that node (`pinMemory`), it shares idle CPUs only within that node, and new replicas
spread across nodes (`allocatorTopologyBalancing`). A pod that holds a device
(`nvidia.com/gpu`, `dsa.intel.com/*`, …) gets CPUs next to it (`preferCloseToDevices:
podresourceapi:*/*`, which needs the policy's `agent.podResourceAPI: true`).

| Vendor key | Active when the host has | Kubernetes exposes |
|------------|--------------------------|--------------------|
| `numa` | more than one `/sys/devices/system/node/node*` | node labels `k3d-gpu.io/numa-nodes=<n>`, `k3d-gpu.io/snc=<nodes per L3>`; pod label `k3d-gpu.io/cores=replica` |

`snc` is CPUs sharing CPU 0's L3 divided by the CPUs of CPU 0's node, the rule
`snc_get_config()` in `arch/x86/kernel/cpu/resctrl/monitor.c` used through Linux 6.19.
Linux 7.0 divides NUMA nodes by packages instead, which also counts memory-only nodes,
so the label reads from CPU nodes on every kernel. `doctor`
names memory-only nodes. When the CPU has `rdt_a`, `doctor` prints how to turn on the
policy's resctrl classes. The policy ships with RDT off because it rewrites host-wide
cache allocation, which the launcher never does.

[`examples/kuberay`](examples/kuberay/README.md) lays a KubeRay `RayCluster` across
the core types: head on E-cores, a P-core and an E-core CPU worker group, and an
iGPU worker group.

### Node Feature Discovery

Every cluster also runs [Node Feature Discovery](https://github.com/kubernetes-sigs/node-feature-discovery)
v0.19.0 with Intel's
[NodeFeatureRules](https://github.com/intel/intel-device-plugins-for-kubernetes/tree/v0.37.1/deployments/nfd/overlays/node-feature-rules)
v0.37.1, baked into the image (namespace `node-feature-discovery`). About 30 s
after `up`, nodes carry the host's CPU, kernel and PCI features. On an
i9-13900T with a UHD 770 and two RTX Ada cards, for example:

```
feature.node.kubernetes.io/cpu-cpuid.AVXVNNI=true      # 55 cpu-cpuid labels in all
feature.node.kubernetes.io/cpu-cpuid.HYBRID_CPU=true
feature.node.kubernetes.io/cpu-model.id=183
feature.node.kubernetes.io/kernel-version.full=6.6.128-1-MANJARO
feature.node.kubernetes.io/pci-0300_10de.present=true
feature.node.kubernetes.io/pci-0380_8086.present=true
feature.node.kubernetes.io/system-os_release.ID=manjaro
gpu.intel.com/device-id.0380-a780.present=true
intel.feature.node.kubernetes.io/gpu=true
```

nfd-worker reads the kernel config, the builtin-module list and os-release from
the host. Inside a k3d node those paths hold the node image's own files, so `up`
bind-mounts the host's `/lib/modules`, `/boot` and `/etc/os-release` read-only
at `/k3d-gpu/host` in the node, and the baked worker reads them from there.
Stock NFD in a k3d node reports the node image's OS (`ubuntu`) and drops its
enabled-module feature, so Intel's `intel.feature.node.kubernetes.io/gpu` rule
never matches. A cluster made from this image with plain `k3d cluster create`
(no launcher mounts) still gets the CPU, PCI and kernel-version labels, but no
os-release labels and no `intel.feature.node.kubernetes.io/gpu`.

The launcher keeps setting its own labels (`npu`, `dsa`, `iaa`, `qat`,
`cpu-cpuid.AMX*`, `k3d-gpu.io/hybrid-cpu`). They exist from node registration
on, so plugins schedule at once and still schedule when the NFD image cannot be
pulled. NFD only removes labels it set itself. Intel's `dsa`, `iaa` and `qat`
rules fire on the PCI device plus its driver, before any work queue or VF is
configured. On such a Xeon NFD may label the node first and schedule the
plugin, which then advertises nothing until the host is set up.

### Optional: KServe + OpenVINO Model Server

`examples/kserve/install.sh` adds [KServe](https://kserve.github.io/website/)
v0.21.0 in Standard mode (plain Deployments and Services, no Knative or Istio)
with cert-manager v1.21.2, both from their pinned OCI Helm charts. It then
serves `OpenVINO/Qwen3-4B-int4-ov` on the Intel GPU through KServe's
OpenVINO Model Server runtime (OVMS 2026.4.1), with tool calling:

```bash
examples/kserve/install.sh          # needs kubectl, helm 3.8+, an Intel GPU (xe or i915)
curl http://qwen3-predictor.llm.svc/v3/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3","messages":[{"role":"user","content":"hi"}]}'   # from a pod
```

| File | What it is |
|------|------------|
| `kserve-values.yaml` | `kserve-resources` chart values: Standard mode, no Ingress objects |
| `openvino-runtime.yaml` | `kserve-openvino` ClusterServingRuntime from KServe v0.21.0 `config/runtimes` (not in that release's charts), image pinned to `openvino/model_server:2026.4.1-gpu` by digest |
| `qwen3-isvc.yaml` | namespace `llm`, InferenceService `qwen3` (one Intel GPU, pod label `k3d-gpu.io/cores=performance`, model pinned to a Hugging Face revision) and Service `ovms` on port 8000 |

`install.sh` reads the host's first Intel render node from sysfs. The pod
requests `gpu.intel.com/xe` or `gpu.intel.com/i915` to match that node's
driver, and gets its group id, since the OVMS container runs as uid 5000.
Service `ovms` gives the agent-stack address shape,
`http://ovms.llm.svc:8000/v3`; the model id there is `qwen3`, not
`Qwen3-4B-int4-ov`.
`tests/kserve_test.sh` checks the install against stub `helm`/`kubectl` and,
with `kubeconform` and `helm` on `PATH`, validates the manifests and rendered
charts at Kubernetes 1.34 and 1.35.

### Agent: an OpenAI-compatible LLM on the best Intel engine

`k3d-gpu agent up` deploys [OpenVINO Model Server](https://github.com/openvinotoolkit/model_server)
2026.4.1 (`openvino/model_server:2026.4.1-gpu`, pinned by digest) into the
running cluster and serves `OpenVINO/Qwen3-4B-int4-ov` over the OpenAI API at
`/v3` (`chat/completions`, `completions`), with tool calling (`hermes3` parser)
and reasoning (`qwen3` parser).

```bash
k3d-gpu up                       # cluster first
k3d-gpu agent up                 # download (once), load, warm up; prints a reply
K3D_GPU_AGENT_DEVICE=CPU k3d-gpu agent up   # same model on the CPU instead
kubectl -n k3d-gpu-agent port-forward svc/ovms 8000:8000 &
curl http://localhost:8000/v3/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"Qwen3-4B-int4-ov","messages":[{"role":"user","content":"hi"}]}'
k3d-gpu agent status             # pod, PVC, device, model commit, model state (/v1/config)
k3d-gpu agent down               # stop serving; the downloaded model stays
k3d-gpu agent manifest           # print the YAML instead of applying it
```

The device comes from what the cluster advertises, not from the host alone:

| Cluster has | Device | Pod gets |
|-------------|--------|----------|
| allocatable `gpu.intel.com/xe`, else `gpu.intel.com/i915`, on a node labelled `gpu.intel.com/xmx=true` | `GPU` | one GPU, a `gpu.intel.com/xmx: "true"` node selector; the host render group is added as a supplemental group |
| anything else, including an Intel GPU without XMX (or with `xmx` unknown) | `CPU` | no device; the P-cores on a hybrid node; on a node labelled `...cpu-cpuid.AMXTILE=true` it is pinned there |
| `npu.intel.com/accel`, only with `K3D_GPU_AGENT_DEVICE=NPU` | `NPU` | the NPU and its accel node's group |

`K3D_GPU_AGENT_DEVICE=GPU` uses any allocatable Intel GPU, XMX or not.

`auto` never picks the NPU, same as OVMS's own device detection: LLMs on the NPU
run the stateful pipeline only (no continuous batching, static prompt shapes).
On a hybrid node the server pod carries `k3d-gpu.io/cores=performance`, so it
runs on P-cores. Its CPU request sizes that balloon: 2 CPUs when a GPU or NPU
does the math, every P-core CPU for CPU inference (half the node's CPUs on
non-hybrid nodes).

The model is fetched once into `pvc/ovms-models` (an init container runs
`ovms --pull`; on later starts it finds the files and only rewrites the
model's `graph.pbtxt`, so a device change downloads nothing). The compiled-model
cache (`--cache_dir`) lives on the same PVC, so a restart skips most GPU
compilation. `agent down` keeps the PVC; `kubectl delete namespace k3d-gpu-agent`
removes everything. The Intel GPU plugin hands out each GPU exclusively, so
`k3d-gpu test` cannot get the GPU while the agent runs on it.

`agent up` finishes with one short generation run inside the pod (`kubectl exec`
+ `curl`). It reports the reply and how long it took. Use the port-forward, not
the API server's service proxy, for POSTs: through that proxy OVMS answers
`Mediapipe graph definition with requested name is not found`. To see which
device the model compiled for, run with `K3D_GPU_AGENT_LOG_LEVEL=DEBUG`, then
`kubectl -n k3d-gpu-agent logs deploy/ovms | grep -A1 EXECUTION_DEVICES`
(for example `GPU.0: Intel(R) UHD Graphics 770 (iGPU)`).

The Intel plugins inject devices through CDI, which already gives the device
node to the pod's own user. The render group is a fallback for plugins that
pass plain device nodes.

Why `auto` wants XMX: measured on an i9-13900T (UHD 770 iGPU, no XMX; 8
P-cores), Qwen3-4B int4, 5 rounds per arm ([ledger:H6]):

| Device | Decode | Prefill (~1.9k-token prompt) | Pod CPU use |
|--------|--------|------------------------------|-------------|
| `CPU` on the P-cores | 15.7 tokens/s [ledger:H6] | 172 tokens/s [ledger:H6] | 7.9 CPUs |
| `GPU` (UHD 770) | 9.9 tokens/s [ledger:H6] | 66 tokens/s [ledger:H6] | 1.0 CPU |

A GPU without XMX matrix engines loses to the P-cores at both decode and
prefill, so `auto` serves on the P-cores. What the GPU buys is CPU: its pod keeps
about one CPU busy instead of eight. Set `K3D_GPU_AGENT_DEVICE=GPU` when the
P-cores are needed for other work more than the model needs speed. An Intel
GPU with XMX (Arc A/B-series, Lunar Lake iGPUs; `k3d-gpu up` labels
the node `gpu.intel.com/xmx=true`) is where `auto` moves the model to the GPU.

Model weights are not pinned to a revision: OVMS 2026.4.1 `--pull` clones the
Hugging Face repo's default branch and has no revision option (`--source_model`
takes a repo id only). `agent up` and `agent status` print the commit that was
downloaded (`model revision: <sha>`, read from the clone's `.git` in the PVC),
so a change shows up as a different sha. The PVC keeps that download until it
is deleted.

| Variable | Default | Description |
|----------|---------|-------------|
| `K3D_GPU_AGENT_DEVICE` | `auto` | `auto`: GPU only on an XMX node, else CPU. `CPU`, `GPU` or `NPU` (any case) to force one; forcing a device the cluster does not advertise fails |
| `K3D_GPU_AGENT_MODEL` | `OpenVINO/Qwen3-4B-int4-ov` | Hugging Face model in OpenVINO IR format |
| `K3D_GPU_AGENT_MODEL_NAME` | last part of the model id | name clients send as `model` |
| `K3D_GPU_AGENT_TOOL_PARSER` / `_REASONING_PARSER` | `hermes3` / `qwen3` for the default model, unset otherwise | unset lets OVMS detect them from the chat template; empty turns one off |
| `K3D_GPU_AGENT_CPUS` | see above | CPU request (balloon size on hybrid nodes) |
| `K3D_GPU_AGENT_MEMORY` | `6Gi` | memory request |
| `K3D_GPU_AGENT_STORAGE` | `20Gi` | PVC size |
| `K3D_GPU_AGENT_TIMEOUT` | `1800` | seconds `agent up` waits for download + load |
| `K3D_GPU_AGENT_LOG_LEVEL` | `INFO` | `DEBUG` also logs the compiled model's execution device (and every request) |
| `K3D_GPU_AGENT_IMAGE` | `openvino/model_server@sha256:a928…fc58` (2026.4.1-gpu) | server image |

### Inventory report

`k3d-gpu report` says what this host and cluster actually have, engine by
engine. It reads sysfs, procfs, debugfs (GuC/HuC firmware, root only) and the
cluster's node objects and nothing else, runs no benchmarks and takes well under a second. It prints a summary
and writes `k3d-gpu-report.json` and a self-contained `k3d-gpu-report.html`
(no external assets) into the current directory, or into `k3d-gpu report DIR`.
`k3d-gpu report --json` prints only the JSON.

| JSON key | Contents | Source |
|----------|----------|--------|
| `host.cpu` | model, online CPUs, P-core / E-core lists and counts, AI ISA flags (`avx*`, `avx_vnni`, `avx512*`, `amx_*`, `fma`, `f16c`) | `/proc/cpuinfo`, `/sys/devices/cpu_core\|cpu_atom/cpus` |
| `host.memory` | total and available KiB | `/proc/meminfo` |
| `host.intel_gpus[]` | card, render node, PCI address and id, driver, engines, GuC/HuC status, firmware and version | `/sys/class/drm/card*`: i915 `engine/<name>/capabilities`; xe `device/tile*/gt*/engines/<class>`; GuC/HuC from debugfs `dri/<PCI address or n>/gt*/uc/{guc,huc}_info` (first GT that has it; the firmware release version) |
| `host.npus[]`, `host.gaudi`, `host.dsa`, `host.iaa`, `host.qat[]` | NPU accel nodes; Gaudi PCI devices / accel nodes / driver; DSA/IAA devices and user work queues; QAT PFs and VF counts | `/sys/class/accel`, `/sys/bus/pci`, `/sys/bus/dsa`, `/dev/dsa`, `/dev/iax` |
| `host.nvidia` | driver version; per GPU: PCI address, model, device id, minor | `/sys/module/nvidia/version`, `/proc/driver/nvidia/gpus/*/information` (no `nvidia-smi`) |
| `host.rapl[]` | zone, name, wrap point (`max_energy_range_uj`), whether `energy_uj` is readable | `/sys/class/powercap/intel-rapl*` |
| `detected[]` | the accelerators `up` would enable and their resources | same detection as `k3d-gpu detect` |
| `cluster` | `present`, or the `reason` it is not; per node: every allocatable resource and the accelerator labels (`k3d-gpu.io/*`, `nvidia.com/*`, `habana.ai/*`, NFD `*feature.node.kubernetes.io/*`) | `kubectl --context k3d-<K3D_GPU_CLUSTER> get nodes` |

`cluster` always uses the `k3d-<K3D_GPU_CLUSTER>` context, never whatever
context is current, so the report cannot describe the wrong cluster. GuC/HuC
state lives in debugfs, which only root can read: without root the report says
`status: null` with a note rather than guessing. `energy_uj` is root-only too.
The document carries `"schema": "k3d-gpu.report/v1"`.

### Measurement harness (`bench/`)

Three small tools for measuring workloads on a k3d-gpu cluster. They are not
installed by the package; run them from a checkout.

**`bench/energy.sh`** — energy one command used, from a RAPL counter (default
`intel-rapl:0`, the CPU package, which includes the iGPU on client parts):

```bash
bench/energy.sh [--zone intel-rapl:0] [--interval 5] [--json out.json] -- <command> [args...]
# energy: zone=intel-rapl:0 (package-0) joules=148.882 seconds=2.015 avg_watts=73.873 command_seconds=2.001 samples=2 exit=0
```

The command runs as you; only the counter reads run as root, in one `sudo`
reader process. With no terminal, set `SUDO_ASKPASS` to a program that prints
the password. The counter wraps at `max_energy_range_uj`; the reader samples it
every `--interval` seconds and every step's delta is summed, so wraps are
counted. Package power includes everything else running on the host, so
measure an idle baseline the same way (`-- sleep 60`) and subtract it.

**`bench/llm_load.py`** — streams chat requests at an OpenAI-compatible
endpoint (Python 3 standard library only). `--base-url` is the API root:
`http://HOST:PORT/v3` for OpenVINO Model Server, `.../v1` for vLLM, llama.cpp
and Ollama.

```bash
bench/llm_load.py --base-url http://127.0.0.1:8000/v3 --model MODEL \
    --requests 16 --concurrency 1,4 --max-tokens 128 [--ignore-eos] --out run.json
```

Each concurrency level is a phase. Per request it records `first_chunk_s`,
`ttft_s` (first content), `latency_s`, `output_tokens` (from
`stream_options.include_usage`, `null` if the server sends no usage),
`tokens_per_s` and `decode_tokens_per_s`. Per phase it records median / p90 /
min / max of each, plus wall time and aggregate tokens/s. OVMS sends a
role-only first chunk as soon as the first token exists, and its usage chunk
only comes from continuous-batching servables.

**`bench/noisy.sh`** — a CPU-burning neighbour pod
(`stress-ng` 0.22.01, pinned by digest) in the current kube context:

```bash
bench/noisy.sh start --cores efficient --workers 4   # or performance, or none (unlabelled)
bench/noisy.sh status
bench/noisy.sh stop
```

`--cores` sets `k3d-gpu.io/cores`, so on a hybrid node the balloons policy
pins it. CPU request = limit = `--workers` unless `--cpu` says otherwise.
`start` waits until the pod runs and prints its `Cpus_allowed_list`.
`KUBECTL_ARGS="--context k3d-NAME"` picks the cluster.

`tests/bench_test.sh` tests all three without root, a cluster or a model,
using a fake RAPL tree and `tests/openai_stub.py`, a streaming stub server.

### Intel Enterprise Agent Toolkit

[integrations/eat](integrations/eat/README.md) runs Intel's
[Enterprise Agent Toolkit](https://github.com/intel/enterprise-agent-toolkit)
(LiteLLM gateway, Redis, pgvector, agent sandbox, CPU vLLM) on a single k3d-gpu
node through EAT's own brownfield path, on a non-Xeon client CPU. Its vLLM pod
joins the hybrid balloons policy's `performance` balloon through EAT's own chart
annotation, so EAT's CPU balloons stay off and `hybrid` stays on.

---

## Prerequisites

- **Docker** (20.10+), configured with NVIDIA GPU support (i.e., `nvidia-docker2` or Docker’s built‑in `--gpus`)  
- **k3d** (v5.0.0 or later) to manage local K3s clusters  
- A host NVIDIA GPU with an up‑to‑date driver (the node image needs no CUDA toolkit)  

### Host setup (`k3d-gpu host-setup`)

`k3d-gpu host-setup` checks the host userspace for each detected Intel GPU, Intel
NPU and NVIDIA GPU, then prints the exact install commands for this distro
(Ubuntu 24.04/26.04, Fedora, Arch, Manjaro; `--distro` prints another one's). It
changes nothing unless you pass `--apply` and answer `y`.

| Component | Ubuntu 24.04 / 26.04 | Fedora | Arch / Manjaro |
|-----------|----------------------|--------|----------------|
| Compute runtime (OpenCL + Level Zero GPU) | GitHub .debs, sha256-checked: compute-runtime 26.35.39758.10, IGC 2.41.5 | `intel-opencl intel-level-zero` | `intel-compute-runtime` |
| Level Zero loader | GitHub `libze1` 1.34.0 .deb, sha256-checked | `oneapi-level-zero` | `level-zero-loader` |
| Media driver + libva | `ppa:kobuk-team/intel-graphics`, pinned 26.3.2 / 2.24.1 | `libva-intel-media-driver` | `intel-media-driver libva` |
| NPU driver + firmware | GitHub linux-npu-driver v1.38.0 archive, sha256-checked (includes `intel-fw-npu`) | `intel-npu-driver intel-npu-firmware` | `intel-npu-driver linux-firmware-intel` |
| NVIDIA container toolkit | NVIDIA apt repo, pinned 1.20.1-1; CDI via `nvidia-cdi-refresh` | NVIDIA dnf repo, pinned 1.20.1-1 | `nvidia-container-toolkit`; CDI via `nvidia-cdi-refresh` (1.20.1-1+), else `nvidia-ctk cdi generate` |

Fedora, Arch and Manjaro repos serve only their current build, so those plans
install the repo version; Arch and Manjaro always use `pacman -Syu`. Before the
plan, `host-setup` reads the failures a partial upgrade leaves behind and prints
the fix:

- **Media driver ahead of libva** (`vainfo`: `iHD_drv_video.so has no function __vaDriverInit_1_0`): the driver exports `__vaDriverInit_1_<N>` for libva 2.N and libva is older. Checked from the files and by running `vainfo`.
- **Compute runtime ahead of libstdc++** (`version 'GLIBCXX_3.4.35' not found`; `clinfo` silently loses the Intel platform): the runtime was built by a newer GCC than the installed `gcc-libs`. Checked by comparing symbol versions, running `clinfo -l` and `ldd` on the registered ICD.
- Parts newer than the kernel's default support get boot options (printed, never applied): `i915.force_probe=<id>` for i915 parts (Meteor Lake, Arrow Lake), `xe.force_probe=<id> i915.force_probe=!<id>` for xe parts.

Downloads go to `${XDG_CACHE_HOME:-~/.cache}/k3d-gpu/host-setup` (override: `K3D_GPU_SETUP_DIR`), which must be a directory you own; it is set to mode 0700. On Ubuntu an apt pin keeps the PPA's `intel-opencl-icd`/`libze*`/`libigc*` builds from replacing the GitHub .debs.

---

## Environment Variables

These are Docker **build args** (not runtime env):

| Build arg    | Default               | Description                                  |
|--------------|-----------------------|----------------------------------------------|
| `K3S_TAG`    | `v1.37.1-k3s1`        | K3s image tag from `rancher/k3s` (auto-bumped) |
| `K3S_DIGEST` | `sha256:ca7f37d9…`    | index digest of `K3S_TAG` (auto-bumped with it) |
| `UBUNTU_TAG` | `26.04`               | Ubuntu base tag (`26.04` default; `24.04` also published) |

Build a specific Ubuntu base:

```bash
docker build \
  --build-arg UBUNTU_TAG="24.04" \
  -t cryptoandcoffee/k3d-gpu:ubuntu24.04 .
```

---

## Building & Pushing the Image

Clone this repository and build with the included `build.sh` or manually:

```bash
git clone https://github.com/88plug/k3d-gpu.git
cd k3d-gpu

# Using build.sh
./build.sh

# Or manually
docker build --platform linux/amd64 \
  -t cryptoandcoffee/k3d-gpu .

# Push to Docker Hub (or your registry)
docker push cryptoandcoffee/k3d-gpu
```

---

## k3d Cluster Setup

Create a k3d cluster that uses the GPU‑enabled image and passes all host GPUs into each node container:

```bash
k3d cluster create gpu-cluster \
  --image cryptoandcoffee/k3d-gpu \
  --servers 1 --agents 1 \
  --gpus all \
  --port 6443:6443@loadbalancer \
  --k3s-arg "--default-runtime=nvidia@server:*" \
  --k3s-arg "--default-runtime=nvidia@agent:*"
```

> **Note:** The `--gpus all` flag exposes every host GPU to the node containers.
>
> **`--default-runtime=nvidia` is required.** k3s auto-detects the nvidia
> containerd runtime but still leaves `runc` as the default, so pods start
> without the GPU driver libraries — the device plugin then fails with
> `Failed to initialize NVML: ERROR_LIBRARY_NOT_FOUND` and the cluster
> advertises **zero** GPUs even though `docker exec … nvidia-smi` works on the
> node. This flag makes nvidia the default runtime on every node. The
> [`k3d-gpu` launcher](#quick-start-k3d-gpu-cli) sets it for you. If you cannot
> change the default runtime, set `runtimeClassName: nvidia` on each GPU pod
> instead (the bundled device-plugin manifest already does).

### Host System Configuration

For optimal performance, you may need to increase inotify limits on your **host system** (not in containers):

```bash
# Temporarily (until reboot):
sudo sysctl -w fs.inotify.max_user_watches=100000
sudo sysctl -w fs.inotify.max_user_instances=100000

# Permanently (survives reboots):
echo "fs.inotify.max_user_watches=100000" | sudo tee -a /etc/sysctl.conf
echo "fs.inotify.max_user_instances=100000" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

### NVIDIA Device Plugin

The device plugin is **baked into the image** at
`/var/lib/rancher/k3s/server/manifests/`, so k3s auto-deploys it on startup —
nothing to install. The bundled manifest sets `runtimeClassName: nvidia`, so GPUs
are advertised even without changing the node default runtime.

Only if you run a **custom base image** that doesn't ship it, apply the upstream
manifest yourself:

```bash
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/main/deployments/static/nvidia-device-plugin.yml
```

---

## Testing GPU Access

Verify GPU visibility:

```bash
k3d-gpu test            # runs a CUDA pod (runtimeClassName: nvidia) and prints nvidia-smi

# or check the scheduler directly — must be > 0:
kubectl get nodes -o jsonpath='{.items[*].status.allocatable.nvidia\.com/gpu}{"\n"}'
```

`k3d-gpu up` already asserts `nvidia.com/gpu > 0` and fails loudly if not, so a
clean `up` means GPUs are schedulable.

> **Note:** If `nvidia-smi` reports `Failed to initialize NVML` or a non-zero
> `result=` while `docker exec … nvidia-smi` on the node works, the test pod's
> CUDA image is newer than the host driver. Point `K3D_GPU_TEST_IMAGE` at a tag
> your driver supports — see the
> [CUDA/driver compatibility matrix](https://docs.nvidia.com/deploy/cuda-compatibility/).

---

## References

- [justinthelaw/k3d-gpu-support](https://github.com/justinthelaw/k3d-gpu-support)  
- [k3d: Running CUDA workloads](https://k3d.io/v5.7.2/usage/advanced/cuda/)  
- [NVIDIA Container Toolkit](https://github.com/NVIDIA/libnvidia-container)  
- [GPUStack on k3d-gpu](docs/integrations/gpustack.md): NVIDIA recipe; why Intel GPU/NPU/Gaudi are not supported  

---

## Contributing

Contributions, issues, and feature requests are welcome! Please fork the repository and submit a pull request.

---

## Release History

| Date       | K3s Tag             | NVIDIA Plugin | Intel GPU Plugin | Gaudi Plugin |
|------------|---------------------|---------------|------------------|--------------|
| 2026-09-30 | v1.34.1-k3s1-amd64 | v0.20.1 | — | — |
| 2026-09-23 | v1.34.1-k3s1-amd64 | v0.20.1 | — | — |
| 2026-09-19 | v1.34.1-k3s1-amd64 | v0.20.0 | — | — |
| 2026-08-20 | v1.34.1-k3s1-amd64 | v0.20.0 | — | — |
| 2026-07-29 | v1.34.1-k3s1-amd64 | v0.19.3 | — | — |
| 2026-06-23 | v1.34.1-k3s1-amd64 | v0.19.3 | — | — |
| 2026-06-04 | v1.34.1-k3s1-amd64 | v0.19.2 | — | — |
| 2026-06-03 | v1.34.1-k3s1-amd64 | v0.19.2 | — | — |
| 2026-06-03 | v1.34.1-k3s1-amd64 | v0.19.2 | — | — |
| 2026-06-02 | v1.34.1-k3s1-amd64  | v0.19.2       | — | — |
---

## License

[FSL-1.1-ALv2](LICENSE.md) © 2025 Crypto & Coffee Development Team

