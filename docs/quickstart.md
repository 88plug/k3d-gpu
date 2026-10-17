# Quickstart: a local chat in one command

`k3d-gpu quickstart` takes a Linux or WSL2 machine to a private chat UI on
`http://127.0.0.1:8080`, backed by an LLM that runs on your own hardware. No
cloud account and no API key. It is one command, and you can run it again
safely.

## What you need

- A Linux PC or laptop (Ubuntu, Debian, Fedora, openSUSE, Arch), or Windows 11
  with WSL2.
- Docker. On Windows: Docker Desktop with WSL integration turned on for your
  distro, or Docker Engine inside the distro.
- Disk space for the model: from about 0.5 GB to about 5 GB, depending on RAM
  (see the table below), plus the container images.
- A network connection for the first run. Later runs reuse what was downloaded.

## 1. Install

The installer is pinned to one release. It checks every file it downloads
against a sha256 written inside it, and stops on any mismatch before
installing anything. Read it before you run it:

```bash
curl -fsSLO https://raw.githubusercontent.com/88plug/k3d-gpu/main/packaging/install.sh
less install.sh
bash install.sh
```

It installs:

- the k3d-gpu `.deb` (apt) or `.rpm` (dnf, zypper); `sudo` asks for your
  password;
- `k3d` and `kubectl` into `~/.local/bin`, when they are not already on your
  `PATH`.

It never installs Docker or changes your system settings. If Docker is
missing, it prints the install command for you to run. On Arch, NixOS or
Homebrew it names the matching package and stops.

## 2. Start

```bash
k3d-gpu quickstart
```

You see five steps, each with a progress bar:

1. **Checking this machine**: the tools are present, Docker answers, and a
   model is picked from your RAM.
2. **Cluster**: a local Kubernetes cluster in Docker, with your Intel GPU, NPU
   or CPU cores passed through.
3. **LLM server**: OpenVINO Model Server downloads the model, with a download
   bar, and loads it. The device is picked for you: an Intel GPU with XMX
   matrix engines when you have one, otherwise the CPU's performance cores.
4. **Chat UI**: Open WebUI, with an admin account created for you.
5. **Port-forward**: the UI on `127.0.0.1` only, not on your network.

When it is done, open `http://127.0.0.1:8080`. Sign in with the email and
password in `~/.local/state/k3d-gpu/quickstart/chat-admin`. That file is
readable by you only. Nobody else can sign up: the admin account exists
before the UI starts.

### Interrupted? Run it again

Every step checks what is already running and skips it. A second run on a
finished machine changes nothing. If a run stops halfway (closed laptop,
dropped Wi-Fi), run `k3d-gpu quickstart` again: the model download resumes
where it stopped.

## Which model you get

The model is picked from the RAM the machine has:

| RAM (as the OS reports it) | Model | Download | Memory reserved |
|---|---|---|---|
| under 12 GiB (8 GB machines) | `OpenVINO/Qwen3-1.7B-int4-ov` | ~1.2 GB | 4 GiB |
| 12 to 24 GiB (16 GB machines) | `OpenVINO/Qwen3-4B-int4-ov` | ~2.3 GB | 6 GiB |
| 24 GiB and more | `OpenVINO/Qwen3-8B-int4-ov` | ~4.9 GB | 10 GiB |

Download sizes are the file totals the Hugging Face API reports for each repo
([4B example](https://huggingface.co/api/models/OpenVINO/Qwen3-4B-int4-ov?blobs=true)).
Only the 4B model has been measured on this project's test bed. The 1.7B and
8B tiers are a prediction of what fits, not a measured result.

Choose another model, or a smaller memory reservation:

```bash
K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-4B-int4-ov k3d-gpu quickstart
K3D_GPU_AGENT_MEMORY=4Gi k3d-gpu quickstart
```

Only the LLM server restarts; the cluster and your chats stay.

Other options:

- `k3d-gpu quickstart --no-ui`: only the OpenAI-compatible API, no chat UI.
- `K3D_GPU_CHAT_PORT=9090`: use another local port.

## Windows (WSL2)

1. In PowerShell: `wsl --install -d Ubuntu-24.04`, then open Ubuntu.
2. Install Docker Desktop and turn on WSL integration for that distro
   (Settings > Resources > WSL integration).
3. Inside Ubuntu, run the install and quickstart steps above.

`quickstart` detects WSL2 and prints what it found:

- **`/dev/dxg` missing**: WSL has no GPU access, so the model runs on the CPU.
  It suggests `wsl --update` and `wsl --shutdown` in PowerShell. It never runs
  them for you.
- **`/dev/dxg` and the Intel driver present**: the Intel GPU is offered to the
  cluster through `/dev/dxg`. This follows Intel's own container recipe for
  WSL ([OpenVINO docker_ci](https://github.com/openvinotoolkit/docker_ci/blob/master/docs/accelerators.md)).
  It is unit-tested only and has not yet been run on a Windows machine.
- Docker Desktop documents its `--gpus` flag for NVIDIA GPUs only
  ([Docker docs](https://docs.docker.com/desktop/features/gpu/)). The Intel
  path does not use that flag.
- **The Intel NPU does not work under WSL2.** Intel's Linux NPU driver has no
  WSL2 support ([intel/linux-npu-driver#56](https://github.com/intel/linux-npu-driver/issues/56)).

## Remove everything

```bash
k3d-gpu uninstall
```

It asks before deleting, or pass `--yes`. It removes:

- the port-forward;
- the cluster, with the downloaded models and your chats;
- quickstart's state files.

`--images` also removes the cluster's node image. The k3d-gpu package itself
stays; remove it with `sudo apt remove k3d-gpu` or `sudo dnf remove k3d-gpu`.

## How long does it take?

Time to a working chat depends mostly on your connection: the first run
downloads the node image, the server and UI images, and the model. The
project's target is under 5 minutes with images already cached and under
15 minutes from nothing. Ledger row H9 in
[EXPERIMENTS.md](https://github.com/88plug/k3d-gpu/blob/main/EXPERIMENTS.md)
records how a real run compared with that target.

## Troubleshooting

- **`missing: docker k3d kubectl`**: run the installer, or `k3d-gpu doctor`
  for the install commands.
- **`docker is installed but not reachable`**: start Docker. On Linux, add
  yourself to the `docker` group (`sudo usermod -aG docker $USER`), then log
  out and back in.
- **The download stalls**: corporate VPNs and proxies can block Hugging Face.
  Stop with Ctrl-C and run `quickstart` again on another network; it resumes.
- **Check on things**: `k3d-gpu agent status` shows the server, its device and
  the model commit.
