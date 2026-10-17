# Quickstart: a local chat in one command

`k3d-gpu quickstart` takes a Linux or WSL2 machine to a private chat UI on
`http://127.0.0.1:8080`, backed by an LLM that runs on your own hardware. No
cloud account and no API key. It is one command, and you can run it again
safely.

!!! note "Version"
    `quickstart` ships in k3d-gpu 0.1.29. The installer below is pinned to
    0.1.28, which has no `quickstart` command, until the 0.1.29 release exists
    and the installer pin moves to it. Until then, run it from a checkout:
    `git clone https://github.com/88plug/k3d-gpu && k3d-gpu/scripts/k3d-gpu quickstart`.

## What you need

- A Linux PC or laptop (Ubuntu, Debian, Fedora, openSUSE, Arch), or Windows 11
  with WSL2, on which `k3d-gpu detect` reports at least one accelerator (an
  Intel GPU, NPU or Gaudi, NVIDIA, Xeon AMX/DSA/IAA/QAT, hybrid P/E cores or
  NUMA). quickstart has no CPU-only path yet: on a host where `detect` prints
  nothing it stops at step 1 and says so.
- Docker. On Windows: Docker Desktop with WSL integration turned on for your
  distro, or Docker Engine inside the distro.
- Disk space for the model: from about 1.2 GB to about 4.9 GB, depending on
  RAM (see the table below), plus the container images.
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

1. **Checking this machine**: the tools are present, Docker answers, an
   accelerator is detected, a proxy is noticed, and a model is picked from
   your RAM.
2. **Cluster**: a local Kubernetes cluster in Docker, with your Intel GPU, NPU
   or CPU cores passed through. A cluster quickstart creates is the one
   `uninstall` deletes.
3. **LLM server**: OpenVINO Model Server downloads the model, with a download
   bar, and loads it. The device is picked for you, as `k3d-gpu agent up`
   picks it: an Intel GPU with XMX matrix engines when you have one, otherwise
   the CPU's performance cores. On a Gaudi the server is vLLM instead, and the
   chat UI step needs `--no-ui` (the UI talks to OpenVINO Model Server only).
4. **Chat UI**: Open WebUI, with an admin account created for you.
5. **Port-forward**: the UI on `127.0.0.1` only, not on your network.

When it is done, open the URL it prints: `http://127.0.0.1:8080`, or the
next free port up to 8099 when 8080 is taken. Sign in with the email and
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
| RAM (as the OS reports it) | Model | Pinned commit | Download | Memory reserved |
|---|---|---|---|---|
| under 12 GiB (8 GB machines) | `OpenVINO/Qwen3-1.7B-int4-ov` | `6f32d81e` | [~1.2 GB](https://huggingface.co/api/models/OpenVINO/Qwen3-1.7B-int4-ov?blobs=true) (1208326810 bytes) | 4 GiB |
| 12 to 24 GiB (16 GB machines) | `OpenVINO/Qwen3-4B-int4-ov` | `b467368d` | [~2.3 GB](https://huggingface.co/api/models/OpenVINO/Qwen3-4B-int4-ov?blobs=true) (2290768181 bytes) | 6 GiB |
| 24 GiB and more | `OpenVINO/Qwen3-8B-int4-ov` | `5c47abf4` | [~4.9 GB](https://huggingface.co/api/models/OpenVINO/Qwen3-8B-int4-ov?blobs=true) (4882865352 bytes) | 10 GiB |

Download sizes are the file totals the Hugging Face API reports for each repo
(linked on each row), and each tier is pinned to the repo commit named in its
row (the API's `sha`), so a fresh pull gets the same files. Only the 4B model
has been measured on this project's test bed, and the 8B cold path once
(ledger H9). The 1.7B tier is a prediction of what fits, not a measured
result.

On a small machine the memory reservation shrinks to fit: it is capped at the
RAM minus 1 GiB for the chat UI and 768 MiB for Kubernetes. Below 2 GiB
quickstart stops at step 1. A WSL2 VM gets half of Windows' RAM by default, so
an 8 GB laptop shows about 3.8 GiB; raise `memory=` in
`%UserProfile%\.wslconfig` and run `wsl --shutdown` to give it more.

Choose another model, or a smaller memory reservation:

```bash
K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3-4B-int4-ov k3d-gpu quickstart
K3D_GPU_AGENT_MEMORY=4Gi k3d-gpu quickstart
```

A changed model or memory reservation restarts only the LLM server; the
cluster and your chats stay. Overriding the model with another tier model
also takes that tier's memory, unless you set the memory too. Any
`K3D_GPU_AGENT_*` setting you export (backend, parsers, revision, device)
reaches `agent up` unchanged.

Other options:

- `k3d-gpu quickstart --no-ui`: only the OpenAI-compatible API, no chat UI.
- `K3D_GPU_CHAT_PORT=9090`: use this local port instead of the first free one
  from 8080.
- `k3d-gpu quickstart --remote`: chat against an OpenAI-compatible endpoint
  from `OPENAI_BASE_URL` and `OPENAI_API_KEY` instead of a local model. It needs
  no accelerator and downloads no model; see [Remote endpoints](remote-endpoints.md).

## Windows (WSL2)

1. In PowerShell: `wsl --install -d Ubuntu-24.04`, then open Ubuntu.
2. Install Docker Desktop and turn on WSL integration for that distro
   (Settings > Resources > WSL integration).
3. Inside Ubuntu, run the install and quickstart steps above.

`quickstart` detects WSL2 and prints what it found. Under WSL2 the LLM runs
on the CPU: the cluster can advertise the WSL2 GPU (`gpu.intel.com/dxg`), but
the LLM server does not request it yet.

- **`/dev/dxg` missing**: WSL has no GPU access. quickstart still needs an
  accelerator that `k3d-gpu detect` reports, and stops at step 1 when there is
  none. It suggests `wsl --update` and `wsl --shutdown` in PowerShell. It
  never runs them for you.
- **`/dev/dxg` and the Intel driver present**: the cluster advertises the
  Intel GPU through `/dev/dxg`. This follows Intel's own container recipe for
  WSL ([OpenVINO docker_ci](https://github.com/openvinotoolkit/docker_ci/blob/master/docs/accelerators.md)).
  It is unit-tested only and has not yet been run on a Windows machine.
- **`/dev/dxg` and the NVIDIA driver present**: the cluster gets the NVIDIA GPU.
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
- the cluster, with the downloaded models and your chats, when quickstart
  created it. A cluster you made with `k3d-gpu up` is kept; `--cluster`
  deletes it too;
- quickstart's state files.

`--images` also removes the cluster's node image. The k3d-gpu package itself
stays; remove it with `sudo apt remove k3d-gpu`, `sudo dnf remove k3d-gpu` or
`sudo zypper remove k3d-gpu`.

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
- **Behind a proxy**: export `HTTPS_PROXY` (and `NO_PROXY`) before
  `quickstart`. It passes them to the cluster's nodes, for image pulls
  ([k3s proxy settings](https://docs.k3s.io/advanced#configuring-an-http-proxy)),
  and to the model download, and adds the cluster's own networks to
  `NO_PROXY`. This applies to a cluster quickstart creates; an existing one
  keeps its settings.
- **The download stalls**: Stop with Ctrl-C (the LLM server's setup stops
  too) and run `quickstart` again; it resumes.
- **Check on things**: `k3d-gpu agent status` shows the server, its device and
  the model commit.
