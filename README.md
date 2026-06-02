# k3d-gpu

[![AUR](https://img.shields.io/aur/version/k3d-gpu?label=AUR&style=flat-square)](https://aur.archlinux.org/packages/k3d-gpu)
[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat-square)](LICENSE.md)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/88plug/k3d-gpu)

A Docker-based solution for building a [rancher/k3s](https://hub.docker.com/r/rancher/k3s) + [nvidia/cuda](https://hub.docker.com/r/nvidia/cuda) image that enables a k3d cluster to access your host’s NVIDIA CUDA‑capable GPU(s).

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

k3d-gpu doctor          # preflight: GPU, docker, nvidia runtime, k3d, kubectl
k3d-gpu up              # create the cluster + apply the NVIDIA device plugin
k3d-gpu test            # run a CUDA pod and print nvidia-smi
k3d-gpu logs            # tail the k3s server container logs
k3d-gpu down            # delete the cluster
```

Behaviour is tunable via environment variables:

| Variable             | Default                                   | Description                          |
|----------------------|-------------------------------------------|--------------------------------------|
| `K3D_GPU_CLUSTER`    | `gpu`                                     | cluster name                         |
| `K3D_GPU_IMAGE`      | `cryptoandcoffee/k3d-gpu:latest`          | k3s + CUDA image                     |
| `K3D_GPU_PLUGIN`     | `/usr/share/k3d-gpu/nvidia-device-plugin.yml` | bundled device-plugin manifest   |
| `K3D_GPU_TEST_IMAGE` | `nvidia/cuda:13.1.2-base-ubuntu24.04`     | image used by `k3d-gpu test`         |

The rest of this README documents the underlying image and the manual `k3d`
commands the launcher runs for you.

---

## Features

- Combines K3s and NVIDIA CUDA support in a single container image  
- Pre‑configured with NVIDIA Container Toolkit for containerd  
- Exposes standard K3s entrypoint (`/bin/k3s agent`)  
- Mounts volumes for kubelet, k3s state, CNI, and logs  
- Tunable via build arguments for K3s and CUDA versions  

---

## Prerequisites

- **Docker** (20.10+), configured with NVIDIA GPU support (i.e., `nvidia-docker2` or Docker’s built‑in `--gpus`)  
- **k3d** (v5.0.0 or later) to manage local K3s clusters  
- A host NVIDIA GPU with up‑to‑date drivers & CUDA toolkit  

---

## Environment Variables

| Variable    | Default                                | Description                                           |
|-------------|----------------------------------------|-------------------------------------------------------|
| `K3S_TAG`   | `v1.34.1-k3s1-amd64`                   | K3s image tag to use from `rancher/k3s`               |
| `CUDA_TAG`  | `13.1.2-base-ubuntu24.04`              | CUDA base image tag from `nvidia/cuda`                |

You can override these when building:

```bash
docker build \
  --build-arg K3S_TAG="v1.28.8-k3s1" \
  --build-arg CUDA_TAG="12.4.1-base-ubuntu22.04" \
  -t cryptoandcoffee/k3d-gpu .
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

To schedule GPU workloads, install the NVIDIA device plugin DaemonSet in your cluster:

```bash
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.19.2/nvidia-device-plugin.yml
```  

---

## Testing GPU Access

Once your cluster and plugin are running, verify GPU visibility:

```bash
# On the server node:
docker exec -it k3d-gpu-cluster-server-0 nvidia-smi

# In a pod (runtimeClassName is required unless nvidia is the node default):
kubectl run cuda-test --rm -it --restart=Never \
  --image=nvidia/cuda:13.1.2-base-ubuntu24.04 \
  --overrides='{"spec":{"runtimeClassName":"nvidia"}}' \
  -- nvidia-smi
```

Successful `nvidia-smi` output confirms that your GPU is accessible from within the cluster.

> **Note:** If `nvidia-smi` reports `Failed to initialize NVML` or a non-zero
> `result=` while `docker exec … nvidia-smi` on the node works, the CUDA image
> is newer than the host driver. Pin the test image to a tag your driver
> supports (e.g. via `K3D_GPU_TEST_IMAGE` for the launcher) — see the
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

| Date       | CUDA Tag                     | K3s Tag               |
|------------|------------------------------|-----------------------|
| 2026-05-02 | 13.1.2-base-ubuntu24.04 | v1.34.1-k3s1-amd64 |
| 2026-04-18 | 13.2.1-base-ubuntu24.04 | v1.34.1-k3s1-amd64 |
| 2026-03-17 | 13.2.0-base-ubuntu24.04 | v1.34.1-k3s1-amd64 |

---

## License

[FSL-1.1-ALv2](LICENSE.md) © 2025 Crypto & Coffee Development Team

