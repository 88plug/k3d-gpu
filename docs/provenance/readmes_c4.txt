=== scottmbaker/nuc-ai-experiments created 2026-03-28 pushed 2026-07-16 *4
  > ="accel", KERNEL=="accel*", MODE="0666"' > /etc/udev/rules.d/99-intel-npu.rules
  > ## Create the LXC container for NPU experiments
  > Create the LXC container and set the configuration to allow NPU and GPU passthrough.
  > cat > /root/setup-k3s-npu.sh
  > cat > /root/setup-k3s-gpu.sh
  > cat > /root/setup-npu-monitor.sh
  > --hostname k3s-node \
  > # K3s requirements
  > # Allow K3s system calls
  > # NPU passthrough
  > # NPU monitor access
  > pct push 100 /root/setup-k3s-npu.sh /root/setup-k3s-npu.sh
  > pct push 100 /root/setup-k3s-gpu.sh /root/setup-k3s-gpu.sh
  > pct push 100 /root/setup-npu-monitor.sh /root/setup-npu-monitor.sh
=== dsa04156/resource-advisor created 2026-10-02 pushed 2026-10-16 *0
  > **GPU·NPU 자원을 확인하고, 검증된 AI 작업을 큐에 제출하며, 결과·실험 기록·사용량을 연결하는 연구 플랫폼입니다.**
  > | 어떤 장비에서 실제 실행되는가 | [GPU·NPU 실행 범위](docs/all-accelerators.md) |
  > NPU는 장치별로 검증한 모델·SDK·입력 계약을 사용합니다. 등록된 장비라는 이유만으로
  > 임의 모델을 실행하거나 GPU↔NPU로 자동 변환하지 않습니다.
  > - Kubernetes GPU5대와 NPU5개에서 모델별 lab 템플릿의 실제 API 실행을 확인했습니다.
  > - Rockchip 연결은 trusted lab 실험용이고, 일부 NPU firmware identity는 미확인입니다.
  > 검증된 ResNet50의 **GPU·Hailo NPU 상시 워커**를 하나의 추론 풀로 묶는 API를 추가했습니다.
  > 실제 기본 동작 확인에서 13개 요청이 모두 성공했고 GPU 8개·NPU 5개로 분산됐습니다.
=== derio-net/frank created 2026-03-13 pushed 2026-10-16 *1
  > # Frank, the Talos Cluster — AI-Hybrid Kubernetes Homelab
  > | GPU (NVIDIA) | GPU Operator | RTX 5070 Ti on gpu-1, driver-less (host driver), va
  > | Public Edge | Hop (Hetzner CX23) | Single-node Talos cluster — public-facing edge for mesh networking and blog hosti
  > │   ├── phase04-gpu/           # NVIDIA extensions + GPU taint
  > | gpu-operator | gpu-operator | RTX 5070 Ti on gpu-1, nvidia.com/gpu: 1 allocatable |
  > ngle-container pod (192.168.55.212:3100, 12Gi memory limit, defensive nvidia.com/gpu toleration); the former paperclip-shell sidecar and 192.168.5
=== rothgar/ai-on-k8s created 2026-07-07 pushed 2026-07-14 *1
  > Targets [Talos Linux](https://www.talos.dev/) clusters. Supports NVIDIA, AMD ROCm, Intel Arc, and Tenstorrent accelerators.
  > | *(none)* | NVIDIA (CUDA) | `4x24gb`, `2-node-8x80gb` |
  > ### NVIDIA (CUDA)
  > | NVIDIA GPU Operator | [`infrastructure/nvidia-operator/`](infrastructure/nvidia-operator/) | NVIDIA configs |
  > | [`omni/spark/`](omni/spark/) | NVIDIA DGX Spark (GB10, 128 GB) | Qwen3.6-35B-A3B-FP8 | Open WebUI |
  > kind: PersistentVolumeClaim
=== QualityMinds/AIppliance-Magic-Stick created 2026-05-26 pushed 2026-10-16 *2
  > nstaller](docs/installation/bare-metal.md) | Ubuntu, host automation, K3s, Flux and Magic Stick |
  > | NVIDIA GPU | Ollama, vLLM, FreeToken on eligible devices |
  > [![CPU, NVIDIA and AMD memory views in the dashboard, showing free and unreserved me
  > sting-cluster installations reuse your cluster rather than installing K3s.
