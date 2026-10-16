=== intel/intel-technology-enabling-for-openshift created 2023-02-16 pushed 2026-01-29 *22
  > # Intel® Enterprise AI Foundation for OpenShift*
  > e full-stack solution for the Intel® Enterprise AI Foundation on the [OpenShift platform](https://www.redhat.com/en/technologies/cloud-computing/open
  > rs technology](https://github.com/intel/intel-technology-enabling-for-openshift/wiki/Intel-Technology-Enabling-for-OpenShift-Architecture-and-Working
  > chitecture-options) to provision AI accelerators, including the Intel Gaudi Processor, Flex and Max GPUs, and Xeon CPU accelerators such as QAT,
  > SGX, and DSA. Additionally, the project introduces solutions for integrating [Gaud
  > i Software](https://docs.habana.ai/en/latest/index.html) or [OneAPI-based](https://www.intel.com/cont
  > s/en/developer/tools/oneapi/overview.html#gs.kgdasr) AI software into OpenShift AI. Key AI workload integrations, such as LLM inferencing, fine-tunin
  > include the GPU network provisioning and full-stack integration with OpenShift.
  > ![Alt text](/docs/images/Intel-Technology-Enabling-for-OpenShift-Architecture.png)
  > Figure-1 Intel Enterprise AI foundation for OpenShift Architecture
  > commended **Infrastructure Cluster** is built with [**Intel® scalable Gaudi® Accelerator**](https://docs.habana.ai/en/latest/Gaudi_Overview/Gaudi
  > _Architecture.html#gaudi-architecture) and standard servers. The [Intel® Xeon® processors](htt
  > /details/processors/xeon/xeon6-product-brief.html ) are used in these Gaudi servers as worker nodes and in standard servers as highly available c
  > The [**Gaudi embedded RDMA over Converged Ethernet (RoCE) network**](https://docs.
=== intel/gpu-base-operator created 2026-05-18 pushed 2026-10-16 *2
  > kind: ClusterPolicy
  > WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
=== open-edge-platform/edge-developer-kit-reference-scripts created 2024-03-25 pushed 2026-10-16 *54
  > ├── npu_installer.sh                # NPU driver installer
=== korabcenaj/dual-gpu-ai-orchestrator created 2026-04-13 pushed 2026-10-15 *0
  > - The repository does not claim NVIDIA execution or production-scale scheduling.
=== crothmeier/k3s-gpu-platform created 2025-11-18 pushed 2025-11-18 *0
  > # K3s GPU Platform
  > Production-ready Kubernetes (K3s) cluster configuration for heterogeneous GPU workloads with automated
  > - `phx-ai01`: NVIDIA Tesla T4 (16GB VRAM)
  > - `phx-ai02`: NVIDIA L4 (24GB VRAM)
  > - `phx-ai03`: NVIDIA RTX 3090 (24GB VRAM)
  > - `phx-nvidia-a4000`: NVIDIA A4000 (16GB VRAM)
  > **NVIDIA Driver:** 580.95.05
  > kubectl get pods -n gpu-operator-resources -l app=nvidia-dcgm-exporter
  > # Deploy NVIDIA device plugin
  > kubectl apply -f manifests/gpu/nvidia-device-plugin/
  > nodes -o custom-columns=NAME:.metadata.name,GPUs:.status.allocatable.nvidia\\.com/gpu
  > k3s-gpu-platform/
  > │   │   ├── nvidia-device-plugin/    # GPU device plugin for heterogeneous fleet
  > │   │   └── runtime-class.yaml       # NVIDIA runtime configuration
=== celesrenata/nixos-k3s-configs created 2024-06-16 pushed 2026-10-09 *17
  > # NixOS K3s Configurations
  > Kubernetes deployment configurations for a home lab K3s cluster running on NixOS.
  > 1. Set up your NixOS K3s cluster (see installation guide in existing README.md)
  > - `nvidia` - NVIDIA GPU support
=== PatrykQuantumNomad/kinder created 2026-03-01 pushed 2026-06-07 *0
  > # kinder — kind, but with everything you actually need.
  > Kubernetes clusters using Docker container "nodes". Built on top of [kind], it pre-installs production-ready addons so you can go from zero to
  > | [NVIDIA GPU](https://kinder.patrykgolabek.dev/addons/nvidia-gpu/) | GPU passthrough for AI/ML workloads on Linux hosts with NVIDI
  > kinder create cluster --profile minimal   # no addons (core kind only)
  > kinder uses the same `kind.x-k8s.io/v1alpha4` config format as kind. Existing kind config files work without modification.
  > apiVersion: kind.x-k8s.io/v1alpha4
  > kind: Cluster
  > ## Why kinder over plain kind?
  > , local-path-provisioner, Headlamp, local registry, cert-manager, and NVIDIA GPU support
  > - **100% compatible** — any kind config or workflow still works
  > kinder is a fork of [kind] by the Kubernetes SIG-Testing community. All credit for the core clu
  > fecycle, node images, and kubeadm bootstrapping goes to the original [kind maintainers](https://kind.sigs.k8s.io/).
  > [kind]: https://kind.sigs.k8s.io/
=== tracebloc/client created 2026-09-17 pushed 2026-10-16 *0
  > cebloc** unified Helm chart — one chart for AKS, EKS, bare-metal, and OpenShift. See [`client/Chart.yaml`](client/Chart.yaml) or the [releases page](
  > A single command provisions a Kubernetes cluster, auto-detects and installs GPU drivers (NVIDIA or AMD), deploys the tracebloc clie
  > docs/PREREQUISITES.md). On a new Linux machine that cluster is native k3s, without Docker.
  > **Kubernetes version, and why an existing k3d cluster keeps its old one.** On native k3s (Linux) a re-run replaces
  > a k3s binary that is not the pinned version in place, so nothing below appl
  > ies there. On k3d, new installs create the cluster on the pinned, validated k3s version
  > staller scripts published here; `install-k8s.sh --help` prints it). **k3s's version is fixed when the cluster is created** and cannot be change
  > k3d cluster delete tracebloc      # then re-run the installer
  > (Nothing installed on this machine yet? Then just the `k3d` line.) This is the same two-step the installer itself prints when it
  > kind: IngestConfig
