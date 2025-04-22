# k3d-gpu

A Docker-based solution for building a [rancher/k3s](https://hub.docker.com/r/rancher/k3s) + [nvidia/cuda](https://hub.docker.com/r/nvidia/cuda) image that enables a k3d cluster to access your host’s NVIDIA CUDA‑capable GPU(s).

---

## Table of Contents

1. [Features](#features)  
2. [Prerequisites](#prerequisites)  
3. [Environment Variables](#environment-variables)  
4. [Building & Pushing the Image](#building--pushing-the-image)  
5. [k3d Cluster Setup](#k3d-cluster-setup)  
6. [Testing GPU Access](#testing-gpu-access)  
7. [References](#references)  
8. [Contributing](#contributing)  
9. [License](#license)  

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
| `K3S_TAG`   | `v1.29.15-k3s1-amd64`                  | K3s image tag to use from `rancher/k3s`               |
| `CUDA_TAG`  | `12.8.1-base-ubuntu24.04`              | CUDA base image tag from `nvidia/cuda`                |

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
  --port 6443:6443@loadbalancer
```

> **Note:** The `--gpus all` flag exposes every host GPU to the server and agent containers.

### NVIDIA Device Plugin

To schedule GPU workloads, install the NVIDIA device plugin DaemonSet in your cluster:

```bash
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.13.0/nvidia-device-plugin.yml
```  

---

## Testing GPU Access

Once your cluster and plugin are running, verify GPU visibility:

```bash
# On the server node:
docker exec -it k3d-gpu-cluster-server-0 nvidia-smi

# In a pod:
kubectl run cuda-test --rm -it --restart=Never \
  --image=nvidia/cuda:12.0-base-ubuntu22.04 \
  -- nvidia-smi
```

Successful `nvidia-smi` output confirms that your GPU is accessible from within the cluster.

---

## References

- [justinthelaw/k3d-gpu-support](https://github.com/justinthelaw/k3d-gpu-support)  
- [k3d: Running CUDA workloads](https://k3d.io/v5.7.2/usage/advanced/cuda/)  
- [NVIDIA Container Toolkit](https://github.com/NVIDIA/libnvidia-container)  

---

## Contributing

Contributions, issues, and feature requests are welcome! Please fork the repository and submit a pull request.

---

## License

Apache 2.0 © 2025 Crypto & Coffee Development Team

