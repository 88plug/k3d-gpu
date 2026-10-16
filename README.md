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
k3d-gpu up              # create the cluster (device plugins auto-deploy), verify each accelerator > 0
k3d-gpu test            # per accelerator: nvidia-smi pod / device-node listing pod
k3d-gpu logs            # tail the k3s server container logs
k3d-gpu down            # delete the cluster
```

Behaviour is tunable via environment variables:

| Variable             | Default                                   | Description                          |
|----------------------|-------------------------------------------|--------------------------------------|
| `K3D_GPU_CLUSTER`    | `gpu`                                     | cluster name                         |
| `K3D_GPU_IMAGE`      | `cryptoandcoffee/k3d-gpu:latest`          | node image (`latest` = ubuntu26.04)  |
| `K3D_GPU_SHARE`      | `/usr/share/k3d-gpu`                      | dir of fallback plugin manifests     |
| `K3D_GPU_VENDORS`    | `auto`                                    | `auto` detects; or a list of `nvidia,intel,npu,gaudi,dsa,iaa,qat,amx,hybrid` |
| `K3D_GPU_PLUGIN`     | `/usr/share/k3d-gpu/nvidia-device-plugin.yml` | fallback manifest (only used if a custom image lacks the baked one) |
| `K3D_GPU_INTEL_PLUGIN` | `/usr/share/k3d-gpu/intel-gpu-plugin.yml` | same, for the Intel GPU plugin |
| `K3D_GPU_GAUDI_PLUGIN` | `/usr/share/k3d-gpu/gaudi-device-plugin.yml` | same, for the Gaudi plugin |
| `K3D_GPU_NPU_PLUGIN` | `/usr/share/k3d-gpu/intel-npu-plugin.yml` | same, for the Intel NPU plugin |
| `K3D_GPU_DSA_PLUGIN` / `_IAA_` / `_QAT_` | `/usr/share/k3d-gpu/intel-{dsa,iaa,qat}-plugin.yml` | same, for the Xeon accelerator plugins |
| `K3D_GPU_HYBRID_PLUGIN` | `/usr/share/k3d-gpu/nri-balloons.yml` | same, for the hybrid-CPU NRI balloons policy |
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

Force or limit vendors with `K3D_GPU_VENDORS=nvidia,intel,npu,gaudi,dsa,iaa,qat,amx,hybrid`.
A loaded `nvidia` or `habanalabs` kernel module counts as present even before
its device nodes appear; `doctor` then flags the missing nodes.

### Host tuning advisor (`doctor --perf`)

`k3d-gpu doctor --perf [--model-gb N]` reads sysfs/procfs (plus `docker info` and
`nvidia-smi`) and reports what matters for inference serving: CPU governor and EPP
per core type, turbo and frequency caps, `hwp_dynamic_boost`, idle-state exit
latencies, RAPL PL1/PL2 against the thermal spec power, THP and hugetlb pages,
swap in use and `vm.swappiness`, irqbalance, Intel GPU frequency (i915 RPS / GuC
SLPC, xe `freq0`), NVIDIA persistence, the Docker storage driver, and whether a
model of N GiB fits in `MemAvailable`. Each finding names the file it came from
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
`hybrid` from `K3D_GPU_VENDORS` if another stack installs its own (for example,
Intel's Enterprise Agent Toolkit CPU balloons).

[`examples/kuberay`](examples/kuberay/README.md) lays a KubeRay `RayCluster` across
the core types: head on E-cores, a P-core and an E-core CPU worker group, and an
iGPU worker group.

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

### Agents per box (`k3d-gpu capacity`)

`k3d-gpu capacity` answers "how many tool-using agents can this box serve
before their LLM turns get slow?" for whatever chat endpoint the cluster runs.
It ramps closed-loop agents (send a turn, wait for the streamed reply, wait the
tool time, send the next turn) and stops at the first agent count whose p95
turn latency breaks the SLO.

```bash
k3d-gpu capacity                          # SLO p95 <= 10 s, support trace, up to 32 agents
k3d-gpu capacity --slo 5 --max 64 --engine igpu
k3d-gpu capacity --dry-run                # discover + quiet check + plan; sends nothing
k3d-gpu capacity --url http://127.0.0.1:8000/v3/chat/completions --model Qwen3-4B-int4-ov
```

* **Endpoint**: found in the cluster (an OVMS or vLLM Deployment/StatefulSet
  behind a Service; `k3d-gpu agent up`'s `k3d-gpu-agent/ovms` wins a tie) and
  reached through `kubectl port-forward`. The engine (cpu, igpu, npu, gaudi, nvidia)
  is read from the pod's device requests and `--target_device`; Gaudi HPU power
  is not sampled, so a Gaudi report's J per turn has no accelerator term. Pick one with
  `--engine` or `--endpoint NAMESPACE/SERVICE`.
* **Quiet check**: before any load it samples CPU busy, CPU package power
  against the box's own PL1, and NVIDIA GPU utilization and power against the
  GPU's own limit. A busy box is refused (exit 5); `--force` measures anyway and
  lists the failed checks as confounds.
* **Output**: `AGENTS PER BOX: N`, p50/p95/p99 per step, J per turn (RAPL
  package energy when readable, `sudo -v` first if `energy_uj` is root-only;
  NVIDIA `power.draw` when present), the binding limit (SLO, errors, tool-call
  validity, or `--max` reached) with hardware flags seen in that step (CPU
  package thermal throttling, package power at PL1, GPU power cap or thermal
  slowdown), and the confounds. JSON goes to
  `$XDG_STATE_HOME/k3d-gpu/capacity/ENGINE-TIME/capacity.json` (`--json`).
* **Scoring** is the pre-registered rule set in
  [`bench/apb/PROTOCOL.md`](bench/apb/PROTOCOL.md): steady window per rep,
  pooled nearest-rank p95, at least 60 window requests per step, and agents per
  box = the largest N such that every step up to N passes.

`python3 bench/apb/test_capacity.py` tests the ramp, the stop rule, the
agents-per-box rule, discovery and the JSON against a stub OpenAI server whose
latency grows with the requests in flight.

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
| `K3S_TAG`    | `v1.34.1-k3s1-amd64`  | K3s image tag from `rancher/k3s` (auto-bumped) |
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

