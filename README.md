# k3d-gpu

[![AUR](https://img.shields.io/aur/version/k3d-gpu?label=AUR&style=flat-square)](https://aur.archlinux.org/packages/k3d-gpu)
[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat-square)](LICENSE.md)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/88plug/k3d-gpu)

A Docker-based [rancher/k3s](https://hub.docker.com/r/rancher/k3s) node image on an [Ubuntu](https://hub.docker.com/_/ubuntu) base with the NVIDIA container toolkit baked in, so a k3d cluster can schedule your host’s accelerators — **NVIDIA GPUs, Intel Arc / Xe2 (Battlemage) GPUs, and Intel Gaudi 3 (HPU)** — exposed on `up` with no `kubectl apply`. The launcher auto-detects what the host has. Built for **Ubuntu 26.04** (default) and **24.04**.

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
| `K3D_GPU_VENDORS`    | `auto`                                    | `auto` detects; or a list of `nvidia,intel,npu,gaudi,dsa,iaa,qat,amx` |
| `K3D_GPU_PLUGIN`     | `/usr/share/k3d-gpu/nvidia-device-plugin.yml` | fallback manifest (only used if a custom image lacks the baked one) |
| `K3D_GPU_INTEL_PLUGIN` | `/usr/share/k3d-gpu/intel-gpu-plugin.yml` | same, for the Intel GPU plugin |
| `K3D_GPU_GAUDI_PLUGIN` | `/usr/share/k3d-gpu/gaudi-device-plugin.yml` | same, for the Gaudi plugin |
| `K3D_GPU_NPU_PLUGIN` | `/usr/share/k3d-gpu/intel-npu-plugin.yml` | same, for the Intel NPU plugin |
| `K3D_GPU_DSA_PLUGIN` / `_IAA_` / `_QAT_` | `/usr/share/k3d-gpu/intel-{dsa,iaa,qat}-plugin.yml` | same, for the Xeon accelerator plugins |
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

Force or limit vendors with `K3D_GPU_VENDORS=nvidia,intel,npu,gaudi,dsa,iaa,qat,amx`.
A loaded `nvidia` or `habanalabs` kernel module counts as present even before
its device nodes appear; `doctor` then flags the missing nodes.

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

---

## Prerequisites

- **Docker** (20.10+), configured with NVIDIA GPU support (i.e., `nvidia-docker2` or Docker’s built‑in `--gpus`)  
- **k3d** (v5.0.0 or later) to manage local K3s clusters  
- A host NVIDIA GPU with an up‑to‑date driver (the node image needs no CUDA toolkit)  

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

