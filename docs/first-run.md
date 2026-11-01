# First run: your machine, start to chat

One path per kind of machine, from a fresh OS to a chat answered by a model on
your own hardware. Every path ends in the same two commands:

```bash
k3d-gpu doctor
k3d-gpu quickstart
```

[Quickstart](quickstart.md) explains what those steps do. This page covers what
comes before them on your machine, and where your machine differs.

## About the times on this page

One run is measured. Ledger row H9 in
[EXPERIMENTS.md](https://github.com/88plug/k3d-gpu/blob/main/EXPERIMENTS.md):
a cold `quickstart` on an i9-13900T with 125 GiB RAM reached Ready in 303 s
(n=1, no images cached, Qwen3-8B on 16 P-cores). That is the only measured
number here. Every other time is a **prediction**, not a measurement. It adds
the download sizes to your setup steps, and your connection speed decides most
of it. The cold first run downloads the node image, the OpenVINO Model Server
image, the Open WebUI image (several GB) and the model (1.2 to 4.9 GB, by RAM).

## Common to every path

1. **Install k3d-gpu.** The pinned installer checks every download's sha256:

    ```bash
    curl -fsSLO https://raw.githubusercontent.com/88plug/k3d-gpu/main/packaging/install.sh
    less install.sh
    bash install.sh
    ```

    It installs the latest k3d-gpu release (its `K3D_GPU_VERSION` pin), plus k3d and kubectl into `~/.local/bin` when
    they are missing. On Arch use the AUR instead (see below).
2. **Docker.** The installer never installs it; it prints the command. After
    adding yourself to the `docker` group, log out and back in.
3. **Skip `host-setup` for the first chat.** quickstart's LLM server runs in a
    container that carries its own GPU runtime. `host-setup` installs host-side
    userspace (OpenCL, Level Zero, media driver) for tools you run on the host.
    It is not a quickstart prerequisite.
4. **`k3d-gpu plan` will say `unmeasured`.** No matrix cell is green yet, so
    `plan` names no engine on any host. quickstart and `agent up` still pick a
    device by their own rules (below). That is expected.

**Where the LLM runs.** quickstart and `agent up` pick, in order: a Gaudi
(vLLM), an Intel GPU with XMX matrix engines, else the CPU. An NVIDIA GPU is
never picked for the LLM, and neither is an Intel GPU without XMX (UHD and
Iris Xe iGPUs, N-series). `quickstart` prints the device at step 3.

**The served model name** depends on RAM: `Qwen3-1.7B-int4-ov` under 12 GiB,
`Qwen3-4B-int4-ov` from 12 GiB, `Qwen3-8B-int4-ov` from 24 GiB. The
examples in `examples/agents` default to the 4B name. On another tier, export
the served name first, for example
`export K3D_GPU_AGENT_MODEL=Qwen3-8B-int4-ov`. Those examples ship only in a
git checkout, not in the .deb/.rpm.

## Windows 11 laptop, WSL2 (for example a Core Ultra 7 155H)

1. In PowerShell as admin: `wsl --install`, then reboot.
2. Install Docker Desktop and turn on WSL integration for your distro (or
   Docker Engine inside the distro).
3. In the distro: the common install, `k3d-gpu doctor`, `k3d-gpu quickstart`.
4. Open the printed `http://127.0.0.1:8080` in your **Windows** browser. WSL2
   forwards localhost to Windows by default. The sign-in is in
   `~/.local/state/k3d-gpu/quickstart/chat-admin` inside the distro.

What differs on WSL2:

- `detect` shows `wsl gpu.intel.com/dxg`, but the LLM runs on the **CPU**: the
  agent does not request the WSL2 GPU yet. quickstart says so at step 1.
- The NPU is not available under WSL2
  ([intel/linux-npu-driver#56](https://github.com/intel/linux-npu-driver/issues/56)),
  so `recipe npu-embeddings` cannot run here.
- WSL2 hides the P/E-core split; no P-core pinning applies.
- The WSL2 VM gets half the Windows RAM by default. A 32 GB laptop shows about
  15.4 GiB and gets the 4B tier. Raise `memory=` in `%UserProfile%\.wslconfig`
  for the 8B tier.
- This path is unit-tested only. It has not been run on a Windows machine.

Predicted: 30 to 60 min. The reboot and the Docker Desktop install are most of
it.

## Mini PC, Intel N100, Ubuntu 24.04, 16 GB

1. Docker Engine: <https://docs.docker.com/engine/install/ubuntu/>, then
   `sudo usermod -aG docker $USER` and log in again.
2. The common install, `k3d-gpu doctor`, `k3d-gpu quickstart`.

What differs: the UHD iGPU has no XMX, so the LLM runs on the CPU. The N100
has only E-cores; the agent gets a share of the 4 cores, not P-cores. The 4B
model is picked from 16 GB. No token rate is measured for E-core-only hosts;
expect a slow first reply, made longer by the model's thinking tokens. For
faster replies, use the 1.7B tier:
`K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-1.7B-int4-ov k3d-gpu quickstart` (this
variable is the Hugging Face repo id for the launcher, not the served name).

Predicted: 20 to 40 min, mostly downloads and the first CPU warm-up.

## Laptop, Lunar Lake (Core Ultra 200V), Fedora

1. Update first: `sudo dnf upgrade --refresh`, then reboot. Lunar Lake needs
   kernel 6.12+ on `xe`; a first-boot 6.11 kernel gets a doctor warning.
   Fedora releases past end of life have archived repos; use a supported one.
2. Docker: Fedora ships Podman, not Docker. Add the `docker-ce` repo
   (<https://docs.docker.com/engine/install/fedora/>), then
   `sudo systemctl enable --now docker`, `sudo usermod -aG docker $USER` and
   log in again.
3. The common install (it uses the .rpm), `k3d-gpu doctor`,
   `k3d-gpu quickstart`.

What differs: the Xe2 iGPU has XMX, so the LLM runs on the GPU. With 32 GB the
8B tier is picked. The 8B model ran once, cold, on a CPU (H9); it has not run
on this GPU.

Predicted: 25 to 45 min, including the kernel update and the 4.9 GB model.

## Server, dual-socket Xeon 6 (Granite Rapids)

1. Docker Engine for your distro, the common install, `k3d-gpu doctor`,
   `k3d-gpu quickstart`.
2. The chat UI binds to `127.0.0.1` on the server. From your workstation:
   `ssh -L 8080:127.0.0.1:8080 you@server`, then open
   `http://127.0.0.1:8080` locally.

What differs: the LLM runs on the CPU with the 8B tier. The agent pod has no
NUMA pinning (no `k3d-gpu.io/cores: replica` label), so on a multi-socket host
it can span sockets. On one single-socket Xeon 6741P the AMX labels were set
[ledger:H52], and after `host-setup --apply` DSA reached a pod while IAA got no
work queue and the QAT plugin crash-looped [ledger:H52b]; NUMA placement is
verified in simulation only. The EAT integration (`integrations/eat`) is
verified on a hybrid client CPU only; its `performance` balloon does not exist on a non-hybrid
Xeon. A QAT warning from doctor does not affect the agent.

Predicted: 15 to 30 min on a fast datacenter link.

## Desktop, Arch Linux, Core i7-14700K + Arc B580

1. Packages: `sudo pacman -S docker kubectl`, then
   `sudo systemctl enable --now docker`, `sudo usermod -aG docker $USER` and
   log in again.
2. k3d and k3d-gpu from the AUR: `yay -S rancher-k3d-bin k3d-gpu`. The AUR's
   `k3d-git` is a different program (the K-3D modeller).
3. `k3d-gpu doctor`, `k3d-gpu quickstart`.

What differs: both GPUs are advertised (`i915` for the UHD 770 iGPU, `xe` for
the B580). The B580 has XMX, so the LLM runs on it (`gpu.intel.com/xe` at step
3). `plan` has no B580 cell yet and says `unmeasured`. No B580 has run here;
Battlemage on `xe` has run only as four Arc Pro B70s in a Xeon [ledger:H52].
`host-setup` proposes `pacman -Syu`; it is not needed for the first chat. Remove with `yay -R k3d-gpu`.

Predicted: 20 to 40 min.

## Workstation with an NVIDIA GPU

1. Docker plus the NVIDIA Container Toolkit
   (<https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>),
   the common install, `k3d-gpu doctor`, `k3d-gpu quickstart`.

What differs: **the LLM runs on the CPU, not the NVIDIA GPU.** The cluster
advertises `nvidia.com/gpu` for your own pods, but `agent up` and quickstart
serve on Gaudi, Intel XMX GPUs or the CPU only. To serve an LLM on the NVIDIA
GPU, see [GPUStack](integrations/gpustack.md) Recipe A; it is not verified on a
k3d-gpu cluster.

Predicted: 15 to 30 min. The single measured run (H9, 303 s) was on a host of
this class, with the LLM on 16 P-cores.
