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
2. [MCP server for AI agents](#mcp-server-for-ai-agents)  
3. [Features](#features)  
4. [Prerequisites](#prerequisites)  
5. [Environment Variables](#environment-variables)  
6. [Building & Pushing the Image](#building--pushing-the-image)  
7. [k3d Cluster Setup](#k3d-cluster-setup)  
8. [Testing GPU Access](#testing-gpu-access)  
9. [References](#references)  
10. [Contributing](#contributing)  
11. [Release History](#release-history)  
12. [License](#license)  

---

## Quick Start (k3d-gpu CLI)

Just want a local chat? `k3d-gpu quickstart` (0.1.33 and newer) goes from a machine with
Docker and a detected accelerator to a chat UI on `127.0.0.1:8080` in one
command: cluster, LLM server (model picked
from your RAM), Open WebUI with a generated admin account. `k3d-gpu uninstall`
removes it all. See [docs/quickstart.md](docs/quickstart.md), the pinned,
sha256-checked installer `packaging/install.sh`, and
[docs/first-run.md](docs/first-run.md) for your kind of machine (WSL2 laptop,
mini PC, Fedora laptop, Xeon server, Arch + Arc, NVIDIA workstation).
No accelerator, or a team endpoint? `k3d-gpu quickstart --remote` uses any
OpenAI-compatible endpoint (`OPENAI_BASE_URL`, `OPENAI_API_KEY`) instead of a
local model: see [docs/remote-endpoints.md](docs/remote-endpoints.md).

The `k3d-gpu` launcher wraps the whole workflow — no need to remember
`k3d cluster create` flags. Install it with your package manager (see [Install](#install)), then:

```bash
k3d-gpu detect          # list accelerators on this host and their k8s resources
k3d-gpu doctor          # preflight: accelerators, kernel/firmware, docker, runtimes, k3d, kubectl
k3d-gpu host-setup      # check host drivers/userspace, print pinned install commands (--apply runs them)
k3d-gpu doctor --perf   # read-only host tuning advisor for inference (add --model-gb N)
k3d-gpu report          # what the host and the cluster have, per engine (text + JSON + HTML)
k3d-gpu facts           # detect + doctor + doctor --perf + report as one facts/v1 JSON document
k3d-gpu plan --explain  # engine, model and placement from measured cells, and why
k3d-gpu audit           # posture: open listeners off loopback, images not pinned in images.lock
k3d-gpu up              # create the cluster (device plugins auto-deploy), verify each accelerator > 0
k3d-gpu test            # per accelerator: nvidia-smi pod / device-node listing pod
k3d-gpu logs            # tail the k3s server container logs
k3d-gpu agent up        # OpenAI-compatible LLM server: Gaudi (vLLM), else an XMX Intel GPU, else P-cores (OVMS)
k3d-gpu recipe npu-embeddings | kubectl apply -f -   # OVMS embeddings on the NPU
k3d-gpu sandbox up      # opt-in: agent-sandbox with a warm pool of sandboxes on E-cores
k3d-gpu down            # delete the cluster
k3d-gpu help            # every command: also remote, plan --predict, tune, capacity, bench, model-share
```

### Install

Every package installs the same payload: the launcher at `bin/k3d-gpu`, the MCP server at `bin/k3d-gpu-mcp`,
and in `share/k3d-gpu/` the plugin manifests, `console/`, `bench/`, `oracle/` and `images.lock`
(`facts`, `plan`, `audit`, `tune`, `capacity` and `k3d-gpu-mcp` also need `python3`).
The node image `cryptoandcoffee/k3d-gpu` is published for linux/amd64 only, so on arm64 the
packages install but `k3d-gpu up` stops unless `K3D_GPU_IMAGE` names a k3s node image built for
arm64. All need Docker, [k3d](https://k3d.io) and `kubectl` on the
host (the Nix package brings its own k3d and kubectl). Releases are tagged `aur-vX.Y.Z`; the
.deb, .rpm and `SHA256SUMS` are attached to each `aur-v*` [GitHub release](https://github.com/88plug/k3d-gpu/releases)
(the flake and the .deb/.rpm start with the first release after 0.1.20).

**Arch Linux / Manjaro (AUR)**

```bash
yay -S k3d-gpu          # or build from packaging/aur/PKGBUILD
```

**Debian / Ubuntu (.deb)**

```bash
VER=$(curl -fsSL 'https://api.github.com/repos/88plug/k3d-gpu/releases?per_page=100' |
      sed -n 's/.*"tag_name": *"aur-v\([0-9.]*\)".*/\1/p' | head -1)   # newest package release
curl -fLO "https://github.com/88plug/k3d-gpu/releases/download/aur-v${VER}/k3d-gpu_${VER}-1_all.deb"
sudo apt install "./k3d-gpu_${VER}-1_all.deb"
```

**Fedora / RHEL / openSUSE (.rpm)**

```bash
VER=$(curl -fsSL 'https://api.github.com/repos/88plug/k3d-gpu/releases?per_page=100' |
      sed -n 's/.*"tag_name": *"aur-v\([0-9.]*\)".*/\1/p' | head -1)   # newest package release
sudo dnf install "https://github.com/88plug/k3d-gpu/releases/download/aur-v${VER}/k3d-gpu-${VER}-1.noarch.rpm"
# openSUSE: sudo zypper install <same URL>
```

**Homebrew on Linux** — this repository is the tap:

```bash
brew tap 88plug/k3d-gpu https://github.com/88plug/k3d-gpu
brew install 88plug/k3d-gpu/k3d-gpu
```

**Nix** (flake, needs the `nix-command` and `flakes` features; x86_64-linux and aarch64-linux):

```bash
nix run github:88plug/k3d-gpu -- detect               # try without installing
nix profile add github:88plug/k3d-gpu                 # pin a release: github:88plug/k3d-gpu/aur-vX.Y.Z
```

In a NixOS or home-manager config, add the flake as an input and use
`inputs.k3d-gpu.packages.${system}.default`, or apply `inputs.k3d-gpu.overlays.default`
to get `pkgs.k3d-gpu`. The license (FSL-1.1-ALv2) is unfree to nixpkgs: the flake's own
packages allow it, but through the overlay your nixpkgs needs
`config.allowUnfreePredicate = p: lib.getName p == "k3d-gpu";`.

Homebrew and Nix install outside `/usr/share`, so their `k3d-gpu` wrapper sets
`K3D_GPU_SHARE` to the bundled manifests (an exported `K3D_GPU_SHARE` still wins).
`packaging/check.sh nfpm|brew|nix` builds and tests each package in pinned containers.

Behaviour is tunable via environment variables:

| Variable             | Default                                   | Description                          |
|----------------------|-------------------------------------------|--------------------------------------|
| `K3D_GPU_CLUSTER`    | `gpu`                                     | cluster name                         |
| `K3D_GPU_IMAGE`      | `cryptoandcoffee/k3d-gpu:<launcher version>` | node image built from the same release (ubuntu26.04); see [Upgrading](#upgrading) |
| `K3D_GPU_SHARE`      | `/usr/share/k3d-gpu`                      | dir of fallback plugin manifests     |
| `K3D_GPU_VENDORS`    | `auto`                                    | `auto` detects; or a list of `nvidia,intel,npu,gaudi,dsa,iaa,qat,amx,hybrid,numa,wsl` |
| `K3D_GPU_NODE_LABELS` | none                                    | extra node labels, comma list of `key=value` (e.g. `ei-infra-eligible=true,ei-inference-eligible=true`) |
| `K3D_GPU_K3S_ARGS`   | none                                      | extra k3s server flags, space-separated (e.g. `--disable=servicelb`) |
| `K3D_GPU_PLUGIN`     | `/usr/share/k3d-gpu/nvidia-device-plugin.yml` | fallback manifest (only used if a custom image lacks the baked one) |
| `K3D_GPU_INTEL_PLUGIN` | `/usr/share/k3d-gpu/intel-gpu-plugin.yml` | same, for the Intel GPU plugin |
| `K3D_GPU_INTEL_SHARED` | `auto`                                  | pods that may share one Intel GPU; `auto` = 8 when every Intel GPU is integrated, else 1 ([sharing](#sharing-an-intel-gpu)) |
| `K3D_GPU_INTEL_ALLOCATION` | `balanced`                          | `balanced`, `packed` or `none`: which GPU a shared pod lands on |
| `K3D_GPU_INTEL_MONITORING` | `0`                                 | `1` adds `gpu.intel.com/monitoring` (every Intel GPU, for exporters) |
| `K3D_GPU_GAUDI_PLUGIN` | `/usr/share/k3d-gpu/gaudi-device-plugin.yml` | same, for the Gaudi plugin |
| `K3D_GPU_NPU_PLUGIN` | `/usr/share/k3d-gpu/intel-npu-plugin.yml` | same, for the Intel NPU plugin |
| `K3D_GPU_DSA_PLUGIN` / `_IAA_` / `_QAT_` | `/usr/share/k3d-gpu/intel-{dsa,iaa,qat}-plugin.yml` | same, for the Xeon accelerator plugins |
| `K3D_GPU_HYBRID_PLUGIN` | `/usr/share/k3d-gpu/nri-balloons.yml` | same, for the NRI balloons policy (hybrid and NUMA hosts) |
| `K3D_GPU_WSL_PLUGIN` | `/usr/share/k3d-gpu/intel-gpu-wsl-plugin.yml` | same, for the Intel GPU plugin in WSL mode |
| `K3D_GPU_BALLOONS`   | `k3s`                                     | `external` leaves the NRI balloons policy to a Helm release |
| `K3D_GPU_AGENT_*`    | see [Agent](#agent-an-openai-compatible-llm-on-the-best-intel-engine) | `k3d-gpu agent` settings |
| `K3D_GPU_NPU_EMBEDDINGS` | `/usr/share/k3d-gpu/npu-embeddings.yml` | template printed by `k3d-gpu recipe npu-embeddings` |
| `K3D_GPU_DRA`        | `0`                                       | `1` = Intel GPUs through the DRA driver instead of the device plugin (experimental, Kubernetes 1.36+) |
| `K3D_GPU_INTEL_DRA`  | `/usr/share/k3d-gpu/intel-gpu-dra.yml`    | same, for the Intel GPU DRA driver |
| `K3D_GPU_SANDBOX`    | `/usr/share/k3d-gpu/agent-sandbox.yml`    | agent-sandbox controller + CRDs for `k3d-gpu sandbox up` |
| `K3D_GPU_SANDBOX_PROFILE` | `/usr/share/k3d-gpu/agent-sandbox-k3d.yml` | E-core SandboxTemplate + warm pool for `k3d-gpu sandbox up` |
| `K3D_GPU_SANDBOX_TIMEOUT` | `180`                                | seconds `sandbox up` waits for a ready sandbox |
| `K3D_GPU_PREFILL_GUARD` | off | opt-in, hybrid CPUs: cap E-cores of the `efficient` balloon at a kHz value or a percent of the E-core max ([prefill guard](docs/prefill-guard.md)); writes host cpufreq, `down` restores it |
| `K3D_GPU_STATE_DIR` | `${XDG_STATE_HOME:-~/.local/state}/k3d-gpu` | where the prefill guard keeps its rendered policy and the recorded `scaling_max_freq` per cluster |
| `K3D_GPU_DEVICE_TEST_IMAGE` | `busybox:1.37`                     | image used by `k3d-gpu test` for Intel GPU/NPU and Gaudi |
| `K3D_GPU_TEST_IMAGE` | `nvidia/cuda:13.4.2-base-ubuntu26.04`     | image used by `k3d-gpu test`         |

### Upgrading

Launcher `X.Y.Z` creates clusters from `cryptoandcoffee/k3d-gpu:X.Y.Z`, the image
built from the same release, so the baked device plugins match the launcher.
Every release publishes that tag once and never moves it.

An existing cluster keeps the image it was created with: k3d cannot swap a node
image in place (`k3d cluster edit` and `k3d node edit` only add or remove ports).
After upgrading the launcher, recreate the cluster to get the new image — this
deletes the cluster's state, so back up anything you need first:

```bash
k3d-gpu version                                   # launcher version = default image tag
k3d-gpu doctor                                    # "node image ..." line: what the cluster runs
k3d-gpu down && k3d-gpu up
```

`k3d-gpu doctor` names the image the cluster's server node was created from and
its release (the `org.opencontainers.image.version` label that versioned release
builds carry; "unversioned" means the image predates them). It warns when that
is not the image this launcher uses, or when the same tag now names a different
local image (a re-pulled `:latest`). `k3d-gpu up` on a cluster that already
exists prints the same line and stops instead of failing inside k3d. Without the
launcher: `docker inspect -f '{{.Config.Image}}' k3d-gpu-server-0`.

Other tags:

- `K3D_GPU_IMAGE=cryptoandcoffee/k3d-gpu:X.Y.Z-ubuntu24.04` — the Ubuntu 24.04 build of a release.
- `K3D_GPU_IMAGE=cryptoandcoffee/k3d-gpu:latest` — opt in to the floating tag. k3d only
  pulls an image that is missing locally, so `docker pull cryptoandcoffee/k3d-gpu:latest`
  before `k3d-gpu up` to get the current build.

The rest of this README documents the underlying image and the manual `k3d`
commands the launcher runs for you.

---

## MCP server for AI agents

`k3d-gpu-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server
that lets an AI agent see which engines the cluster has and place work on them. It
speaks stdio and needs only `python3` (3.8+, standard library) next to the `k3d-gpu`
launcher and `kubectl`. Every tool but `apply` is read-only, and `apply` runs only
a plan whose hash the user confirmed in the client.

| Tool | Returns |
|------|---------|
| `accelerators` | the `k3d-gpu report --json` document: host devices, detected vendors and their resources, per-node allocatable resources and accelerator labels (falls back to `detect` + node allocatable on launchers without `report`) |
| `recommend_placement` | `workload`: `llm` \| `embeddings` \| `media` \| `batch`, `objective`: `latency` (default) \| `throughput` \| `efficiency`. The best engine the cluster advertises and the pod-spec fragment to merge: the extended-resource limit, `k3d-gpu.io/cores=performance\|efficient` with a CPU request (the balloon is sized by it) on hybrid CPUs, the AMX `nodeSelector` on the CPU path. An `llm` at `latency` or `throughput` takes the Intel GPU only on a `gpu.intel.com/xmx=true` node, the same rule as `k3d-gpu agent up` |
| `agent_endpoint` | the OpenVINO Model Server from `k3d-gpu agent up`: in-cluster `/v3` base URL, a `port-forward` command and `localhost` URL for the host, device, served models (`/v3/models`) |
| `cluster_health` | node readiness, per-node accelerator counts, device-plugin DaemonSet rollout (and plugins missing for host-detected devices), and a list of problems |
| `hardware` | the host's `facts/v1` document: the exact bytes `k3d-gpu facts --json` prints |
| `plan_launch` | `engine`, `device`, `model`, `port` (all optional). The exact bytes `k3d-gpu plan --json` prints with those options, plus the plan hash (`sha256` of those bytes, what `k3d-gpu plan --hash` prints) in a second text item and in `_meta["k3d-gpu.io/planHash"]`. There is no `bind`: an off-loopback listener is never set up over MCP |
| `apply` | `plan_hash` and the options given to `plan_launch`. The only write: `k3d-gpu apply --plan-hash`, which runs `k3d-gpu agent up` with the plan's image, device, model and P-core count. See below |

`apply` writes only after a human confirms the plan hash in the client. On the
first call it recomputes the plan; if the plan names no engine or no longer
hashes to `plan_hash`, it refuses. Otherwise it answers `input_required` with a
form elicitation that shows the plan and its hash (MCP 2026-07-28 multi
round-trip requests), and runs the plan only when the client retries with the
user's `accept` and `confirm: true`. The `requestState` it hands out is signed
with a per-process key, expires after 10 minutes, is bound to the call's
arguments and works once. It refuses when the client declares no form
elicitation, in a session opened with the legacy `initialize` handshake, and
when the user declines; the refusal names the `k3d-gpu apply --plan-hash ...`
command the user can run in a terminal instead. `k3d-gpu apply` checks the hash
again before it writes.

Protocol: MCP **2026-07-28** (per-request `_meta`, `server/discover`) and, for
clients that still open with the `initialize` handshake, 2025-11-25, 2025-06-18,
2025-03-26 and 2024-11-05.

Claude Code:

```bash
claude mcp add --transport stdio k3d-gpu -- k3d-gpu-mcp
# a non-default cluster name:
claude mcp add --env K3D_GPU_CLUSTER=dev --transport stdio k3d-gpu -- k3d-gpu-mcp
```

Other MCP clients launch the same command; most read a JSON entry like this one (the
top-level key differs between clients — check yours):

```json
{
  "mcpServers": {
    "k3d-gpu": {
      "command": "k3d-gpu-mcp",
      "env": { "K3D_GPU_CLUSTER": "gpu" }
    }
  }
}
```

From a git checkout use the absolute path to `scripts/k3d-gpu-mcp`; it finds the
`scripts/k3d-gpu` launcher next to it.

| Variable | Default | Description |
|----------|---------|-------------|
| `K3D_GPU_CLUSTER` | `gpu` | cluster; kubectl always uses context `k3d-<name>` |
| `K3D_GPU_BIN` | `k3d-gpu` next to the server, else `PATH` | launcher to run |
| `K3D_GPU_MCP_TIMEOUT` | `60` | seconds per launcher or kubectl call |

`tests/mcp_test.sh` drives the server through scripted JSON-RPC sessions against a
stubbed launcher and kubectl.

---

## Features

- K3s + NVIDIA Container Toolkit on an Ubuntu base — **26.04** (default) and **24.04**  
- NVIDIA, **Intel GPU** (Arc, Battlemage, Core Ultra integrated GPUs, Crescent Island — `xe` or `i915`), **Intel NPU** (Core Ultra) and **Intel Gaudi 3 (HPU)** device plugins **baked into k3s auto-deploy** — `up` exposes them with no `kubectl apply`. The launcher labels nodes so each plugin only runs where its hardware is  
- Auto-detection on the host from sysfs: `/dev/nvidia*`, Intel DRM render nodes (`xe`/`i915`), and `/dev/accel/accel*` nodes told apart by PCI vendor (`8086` + `intel_vpu` = NPU, `1da3` = Gaudi). `k3d-gpu detect` prints what it found  
- `k3d-gpu doctor` checks kernel and firmware per platform, names the `xe.force_probe=<id>` needed for parts newer than your kernel, and checks Xeon built-in accelerators (AMX, DSA, IAA, QAT) — deployed automatically once the host side is configured. With the cluster up it also checks the device plugin and NRI balloons DaemonSets, conflicting NRI policies and disconnected device plugins (see [docs/failure-modes.md](docs/failure-modes.md))  
- [Node Feature Discovery](#node-feature-discovery) with Intel's NodeFeatureRules on every node: CPU flags (`cpu-cpuid.*`), kernel, PCI and `intel.feature.node.kubernetes.io/gpu` labels read from the host, one owner per label  
- CDI-ready: containerd 2.x scans `/etc/cdi` and `/var/run/cdi` (CDI on by default), so future vendors that emit CDI specs need no node changes  
- Pre‑configured nvidia containerd runtime; `--default-runtime=nvidia` for zero-config GPU pods  
- No CUDA toolkit in the node image — driver libs are injected from the host, workloads bring their own CUDA  
- Exposes the standard K3s entrypoint (`/bin/k3s agent`); volumes for kubelet, k3s state, CNI, logs  
- Tunable via build arguments for the K3s and Ubuntu versions  
- Agent framework quickstarts for the cluster's OpenAI-compatible model endpoint (OpenAI Agents SDK with MCP, LangGraph, CrewAI, LlamaIndex, Intel Enterprise Agent Toolkit's LiteLLM gateway) in [`examples/agents/`](examples/agents/)  

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
| Intel  | Core Ultra iGPU / Arc **on Windows (WSL2)** | `gpu.intel.com/dxg` | WSL2 GPU-PV: `/dev/dxg`, Intel graphics driver on Windows — see [Windows (WSL2)](#windows-wsl2) |

k3d node containers are privileged, so host `/dev/dri` and `/dev/accel` are
visible with no extra flag; the vendor plugins hand the device nodes to pods.
Devices must exist when the cluster is created — recreate the cluster after
loading a driver. Request them in a pod like any extended resource:
`resources.limits: {gpu.intel.com/xe: 1}`, `{npu.intel.com/accel: 1}` or `{habana.ai/gaudi: 1}`.
Workload images bring their own user space: Intel compute-runtime / Level Zero
for GPUs, the [NPU driver](https://github.com/intel/linux-npu-driver) + OpenVINO
for the NPU. Gaudi runs in the plugin's plain mode, which hands pods the devices
without the habana container runtime; full Gaudi workloads may still expect it.

### Sharing an Intel GPU

By default the Intel GPU plugin hands each GPU to one container. Then an
inference server takes the iGPU and an embeddings pod or a media transcode
pod stays Pending. `K3D_GPU_INTEL_SHARED=<n>` sets the plugin's
`-shared-dev-num`, so `n` pods can each request `gpu.intel.com/i915: 1` (or
`xe`) on one GPU at once. For example, OVMS, an embeddings server and a media
pod can all run on a UHD 770.

| Setting | Plugin flag (intel-device-plugins v0.37.1) | Effect |
|---------|--------------------------------------------|--------|
| `K3D_GPU_INTEL_SHARED=auto` (default) | `-shared-dev-num=8` if every Intel GPU is the integrated one (PCI `00:02.0`), else none | allocatable `gpu.intel.com/<driver>` = 8 per iGPU |
| `K3D_GPU_INTEL_SHARED=<n>` | `-shared-dev-num=<n>` | allocatable = `n` per GPU, every GPU on the node |
| `K3D_GPU_INTEL_ALLOCATION` | `-allocation-policy=balanced\|packed\|none` (only passed when shared) | which GPU a shared request lands on: spread out, fill one first, or kubelet's order |
| `K3D_GPU_INTEL_MONITORING=1` | `-enable-monitoring` | adds one `gpu.intel.com/monitoring` per node that grants every Intel GPU, for exporters such as XPU Manager; `k3d-gpu test` checks it |

Why the default is like this:

- **An iGPU is already shared.** The host desktop, browser and video player
  all use it. Exclusive allocation inside the cluster isolates nothing.
- **Sharing costs no total throughput here.** On a UHD 770, two media pods
  (VA-API 1080p decode, scale, H.264 encode) ran together.
  Each got 0.59x the speed of one pod alone, so together they did 1.18x the work per second [ledger:H15].
  Exclusive allocation would have left the second pod Pending until the first
  finished. A compute job that keeps every EU busy may gain nothing; see the
  ledger's scope note.
- **A discrete GPU stays exclusive.** The count applies to every Intel GPU on
  the node. So a host with an Arc card (with or without an iGPU) keeps
  one-pod-per-GPU unless you set a number. A discrete id at the iGPU's PCI slot
  (a card passed through to a VM) also stays exclusive.
- **Why 8.** The count is only a scheduling ceiling and reserves nothing.
  8 covers an inference server, embeddings, media and a few jobs. It still
  stops dozens of pods from silently piling onto a 32-EU iGPU. Upstream's
  sharing overlay uses 30.

Tradeoffs when sharing is on:

- **No isolation.** Every pod gets the whole device node (`/dev/dri/renderD*`).
  The driver keeps each process in its own GPU address space, but there is no
  memory or compute partitioning and no quota. A pod that hangs the GPU
  triggers an engine reset, which can also kill other pods' in-flight work.
- **Memory contention.** An iGPU has no VRAM; it uses system RAM. Kubernetes
  does not count GPU memory, so pods can together exhaust it. A large model in
  OVMS plus embeddings can fail allocations or push the host into swap. Size
  the models to fit together.
- **Engine contention.** Pods are time-sliced on the same EUs and media
  engines. Latency rises when pods are busy at the same time. Do not share a
  GPU that runs latency-critical work.
- **Fixed for the cluster's life.** The launcher writes the flags into the
  node's baked `intel-gpu-plugin.yaml` (k3s re-applies that file on every
  start, so a `kubectl patch` would not stick). To change them, recreate the
  cluster.

`k3d-gpu doctor` prints the sharing mode it will use.

### Intel NPU: firmware, device access, embeddings

- **Firmware vs user space.** The NPU firmware is on the host
  (`/lib/firmware/intel/vpu/vpu_*`); the Level Zero NPU driver and compiler are
  in the serving image. `openvino/model_server:2026.4.1-gpu` bakes
  [linux-npu-driver](https://github.com/intel/linux-npu-driver) v1.35.0, whose
  firmware is built 2026-06-30. Per linux-npu-driver's "Firmware version
  mismatch" note, the firmware must come from the driver release that matches
  the compiler; older firmware fails with
  `MAPPED_INFERENCE_VERSION is NOT compatible`. `doctor` reads the build date
  out of the file the kernel loads (same search order as the kernel, `.zst`/`.xz`
  included) and warns when it is older. Fix: install `intel-fw-npu` from the
  v1.35.0 release (it lands in `/lib/firmware/updates/intel/vpu`, which the
  kernel prefers) and reload `intel_vpu`.
- **Device access.** Pods get `/dev/accel/accelN` with the host's owner, group
  and mode. A non-root pod (OVMS runs as uid 5000) needs that group in
  `securityContext.supplementalGroups` unless the node is mode 0666 (systemd's
  default; Ubuntu uses 0660). `doctor` prints the group, and `up` labels the node
  `k3d-gpu.io/npu-render-gid=<gid>`.
- **Embeddings recipe.** `k3d-gpu recipe npu-embeddings | kubectl apply -f -`
  runs OVMS 2026.4.1 with `OpenVINO/Qwen3-Embedding-0.6B-int8-ov` (pinned
  commit) on the NPU and serves `POST /v3/embeddings` on Service
  `npu-embeddings:8000`. It uses `--max_length 512`: below 1024, OVMS reshapes
  the model to a static `[1, max_length]` shape and pads every input to it;
  1024 and above uses the NPUW LLM text-embedding pipeline. The manifest
  (`share/npu-embeddings.yml`) documents the rules.

Force or limit vendors with `K3D_GPU_VENDORS=nvidia,intel,npu,gaudi,dsa,iaa,qat,amx,hybrid,numa,wsl`.
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

`k3d-gpu doctor --perf --bandwidth` is the one measuring step: it builds STREAM
(`jeffhammond/STREAM` at a pinned commit, source checked by sha256) with
`-O3 -march=native -fopenmp`, runs it with one thread per physical core and
arrays four times the last-level cache, and caches the best Triad, with the CPU
model and MemTotal it ran on, in `~/.local/state/k3d-gpu/bandwidth.json`. It
takes about a minute and changes no host setting.

### Bandwidth ceiling for a model (`plan --predict --model`)

`k3d-gpu plan --predict --model ID[@REV]|DIR|FILE.gguf` predicts the batch-1
decode ceiling of one model on this host: bandwidth divided by the weight bytes
one token reads. Parameter counts come from the model's `config.json` (hidden
sizes, heads, layer types, `num_experts` and `num_experts_per_tok` or their
aliases); for a mixture of experts only `top_k / num_experts` of the expert
weights count per token. A `.gguf` file is read from its tensor table instead,
which gives exact bytes per tensor. `--format` sets the bytes per parameter
(`bf16`, `fp8`, `w4a16-g128`, `ov-int4-asym-g128`, `ov-int4-sym-g32`, `q4_0`,
`q4_k_m`, ...); without it the format comes from `openvino_config.json`, the
config's `quantization_config`, or its dtype. A Hugging Face id is fetched as
`config.json` only (cached when the revision is a commit sha).

It prints one row per placement that fits: all on the CPU, all on one Intel GPU,
tensor parallel over several, and, for a GGUF MoE, experts on the CPU with the
rest on a GPU (`llama.cpp -ngl 99 -ot exps=CPU`), which adds both memory terms
and a per-layer hop latency (`--hop-us`, assumed). Each row names the engine
that serves that container on that device (OVMS for OpenVINO IR, llm-scaler-vLLM
on Arc, vLLM or SGLang on the CPU, llama.cpp for GGUF). Every bandwidth is
labelled: DRAM is `measured` when this host's `doctor --perf --bandwidth` cache
exists, else `theoretical` from SMBIOS type 17 (populated channels times MT/s
times 8 B, readable as root), or `given` with `--dram-gbs`; GPU VRAM size comes
from the BAR and its bandwidth from the device's spec sheet. `--json` prints the
same as `k3d-gpu.moe-predict/v1`.

It is a predictor: it has no compute, KV-cache or kernel-efficiency term and does
not model speculative decoding. How close a measurement comes to it is what
EXPERIMENTS.md H54c tests.

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
| `k3d-gpu.io/xmx.i915=false`, `k3d-gpu.io/xmx.xe=false` | `gpu.intel.com/xmx=true`, and that driver also holds an Intel GPU without known XMX (its device plugin resource may hand that GPU out, so `agent up` auto and `recommend_placement` skip it) | per platform, from Mesa's `has_systolic` |
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
    k3d-gpu.io/cores: performance   # or: efficient, lowpower, ecluster, pcore-physical, latency
spec:
  containers:
  - name: app
    resources: {requests: {cpu: "4"}, limits: {cpu: "4"}}   # requests size the balloon
```

| Vendor key | Active when the host has | Kubernetes exposes |
|------------|--------------------------|--------------------|
| `hybrid` | online CPUs in `/sys/devices/cpu_core` and in `/sys/devices/cpu_atom` or `cpu_lowpower` | node label `k3d-gpu.io/hybrid-cpu=true`; pod label `k3d-gpu.io/cores=performance\|efficient` pins to P- or E-cores; `lowpower\|ecluster\|pcore-physical\|latency` are further balloon types ([how](docs/intel.md#how-hybrid-pe-pinning-works)); node annotation `k3d-gpu.io/favored-cpus` when CPPC ranks some CPUs above the rest |

Unlabelled pods land in a `shared` balloon that uses every idle CPU, so nothing
else loses capacity. kube-system runs on a one-CPU reserve. k3s turns NRI on in
containerd for a privileged node, and the node sees the host's core-type PMUs.
By default the policy only changes cpusets inside the node's cgroup. It never
touches cpufreq, C-states or RDT. The one exception is opt-in: the
[prefill guard](docs/prefill-guard.md) (`K3D_GPU_PREFILL_GUARD=50% k3d-gpu up`)
caps the max frequency of the E-cores running `efficient` pods, because P- and
E-cores share one package power and thermal budget. With the guard set or
recorded, `k3d-gpu doctor --perf` shows the package throttle counters behind it.

Only one NRI resource policy can own a node. k3s re-applies its baked manifests
every time it starts, which would undo another stack's changes to the policy. So
`up` tells k3s to delete the baked policy (`--disable=nri-balloons`) when neither
`hybrid` nor `numa` is active (not detected, or left out of `K3D_GPU_VENDORS`), or
when you set `K3D_GPU_BALLOONS=external`. Use `external` when a Helm release will
own the policy, for example Intel's Enterprise Agent Toolkit (EAT). EAT installs
the same chart as release `nri-resource-policy-balloons` in `kube-system`. With
`external`, `up` also skips its own edits to the policy: the favored-CPU hint on
the `latency` balloon and, on Arrow Lake-H, `OVERRIDE_SYS_ATOM_CPUS` on the
DaemonSet. A Helm release that needs them sets them in its own values.

The baked objects also carry Helm's ownership metadata for that release, so on a
cluster created without `external`, `helm upgrade --install` of that release
adopts them instead of failing with "cannot be imported". Adopting does not
replace the policy by itself. Helm 3 sends an adopted BalloonsPolicy an empty
patch, so the baked balloon types stay. Helm 4 applies server-side and stops on
conflicts with k3s's deploy controller. To replace the baked policy on such a
cluster, keep k3s from re-applying it, then pass a force flag (use your cluster
name in place of `gpu`):

```bash
docker exec k3d-gpu-server-0 touch /var/lib/rancher/k3s/server/manifests/nri-balloons.yaml.skip
helm upgrade --install nri-resource-policy-balloons nri-resource-policy-balloons \
  --repo https://containers.github.io/nri-plugins --version v0.14.0 \
  --namespace kube-system -f values.yaml --force   # Helm 4: --force-conflicts
```

Without the `.skip` file, the next start of the node puts the baked policy back.
EAT's own playbook runs Helm with no force flag, so on such a cluster it leaves a
mix of baked and EAT settings (see [docs/intel.md](docs/intel.md)). For EAT,
create the cluster with `external` ([ledger:H14]).

#### Intel Enterprise Agent Toolkit on a hybrid CPU

[`contrib/eat/`](contrib/eat) keeps EAT's vLLM on P-cores and its agent services
(LiteLLM, Redis, pgvector, sandboxes) on E-cores:

- `hybrid-values.yaml` holds chart values for `K3D_GPU_BALLOONS=external`. Install
  them with the `helm` command at the top of the file, then set
  `deploy_nri_balloon_policy=no` in EAT's `agentic-config.cfg`. That setting also
  turns EAT's `balloon_policy_cpu` off, which has a cost: EAT skips its CPU
  topology detection and deploys vLLM without its balloon `--set` flags
  (`cpu_balloon_annotation`, `cpu`, `memory`, `tensor_parallel_size`,
  `pipeline_parallel_size`). vLLM still lands in `vllm-balloon`, because that
  type matches the container name `vllm`, but its CPU and memory requests and
  its parallelism come from the defaults in EAT's `xeon_values_file`. Set them
  there.
- `enterprise-agent-toolkit.patch` is for EAT itself. It makes its CPU reservation
  correct on hybrid CPUs, which upstream gets wrong, and adds the same P/E split
  to EAT's own policy. EAT detects a hybrid CPU only when it computes
  `nri_reserved_cpu_list` itself. If you set `nri_reserved_cpu_list`, also set
  `nri_hybrid_cpu=true`, or the P/E split is skipped.
- `compute_reserved_cpus.sh` is the patched script. `tests/reserved_cpus_test.sh`
  tests it.

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

### Windows (WSL2)

Run `k3d-gpu` inside a WSL2 distro on a Windows AI PC. WSL2 has no PCI GPU and no
`/dev/dri` render node for the GPU. The Windows driver is reached through GPU
paravirtualization instead: one `/dev/dxg` device for every GPU, the Windows driver
store mounted at `/usr/lib/wsl/drivers`, and `libdxcore.so` plus vendor libraries
at `/usr/lib/wsl/lib`.

| Vendor key | Active when the host has | Kubernetes exposes |
|------------|--------------------------|--------------------|
| `wsl` | `/proc/version` names a `microsoft` kernel, `/dev/dxg` exists, and the Intel graphics driver package (`iigd_dch*`) is in `/usr/lib/wsl/drivers` | `gpu.intel.com/dxg`, one per Intel GPU |
| `nvidia` | the same WSL2 kernel and `/dev/dxg`, plus `/usr/lib/wsl/lib/nvidia-smi` from the NVIDIA Windows driver | `nvidia.com/gpu` (one `all` device) |

**Intel (`wsl`).** The upstream Intel GPU plugin has a WSL mode (`-wsl`, plus its
Level Zero sidecar). It advertises `gpu.intel.com/dxg` and gives each pod
`/dev/dxg`, a read-only `/usr/lib/wsl` and `ZE_AFFINITY_MASK=<index>`. `up` mounts
`/usr/lib/wsl` read-only into the node and labels it `k3d-gpu.io/wsl-gpu=true`;
that label gates the baked `intel-gpu-plugin-wsl` DaemonSet. The node is
privileged, so it already sees `/dev/dxg`. Workload images bring Intel
compute-runtime (Level Zero / OpenCL). Its README lists WSL support from Tiger
Lake through Meteor, Arrow, Lunar and Panther Lake, plus Alchemist and Battlemage.

```yaml
resources:
  limits: {gpu.intel.com/dxg: 1}
```

**NVIDIA.** This uses the normal `nvidia` path, which works in two layers.
First, `--gpus=all` gives the node container the GPU. Docker does that with
either the legacy `nvidia-container-runtime-hook` (libnvidia-container) or, when
the daemon resolves `--gpus` through CDI, a spec from `nvidia-ctk cdi generate`.
Both find `libdxcore.so` and switch to WSL mode: they mount `/dev/dxg`, the
libraries from `/usr/lib/wsl/lib` and the NVIDIA driver-store files. Second,
inside the node, k3s containerd runs pods with the image's `nvidia` runtime, which
has to detect WSL mode again from the `/dev/dxg` and `libdxcore.so` the first
layer gave the node. The NVIDIA device plugin (v0.19.1+) reports one `all` device
on WSL. Docker Desktop ships the toolkit itself. A `dockerd` inside the WSL
distro needs `nvidia-container-toolkit` installed there.

With both vendors, the node gets the read-only `/usr/lib/wsl` bind and the
toolkit's mounts of files inside that same tree. The toolkit only mounts over
paths that already exist in the bind (same source tree), which a read-only
parent allows; it would fail only on a path missing from the bind.

**Docker Desktop or `dockerd` in the distro.** Both run on the same WSL2 kernel,
so `/dev/dxg` exists for either one. With Docker Desktop, the `/usr/lib/wsl` bind
mount comes from Docker Desktop's own distro, not yours. Docker Desktop
documents GPU support for NVIDIA only.

> **Unverified on real hardware.** The detection, manifests and `up` arguments
> are unit-tested. They have not been run on a Windows machine. To check a
> machine, run these in the WSL distro, in order:
>
> ```bash
> grep -i microsoft /proc/version && ls -l /dev/dxg && ls /usr/lib/wsl/lib /usr/lib/wsl/drivers
> docker run --rm --privileged busybox:1.37 ls -l /dev/dxg                    # node sees /dev/dxg
> docker run --rm -v /usr/lib/wsl:/usr/lib/wsl:ro busybox:1.37 ls /usr/lib/wsl/lib   # bind source resolves (Docker Desktop too)
> k3d-gpu detect && k3d-gpu up && k3d-gpu test                               # expect wsl=1, /dev/dxg listed
> kubectl -n kube-system logs ds/intel-gpu-plugin-wsl -c intel-gpu-levelzero  # Level Zero found the Intel GPU
> kubectl describe node | grep nvidia.com/gpu                                # NVIDIA on WSL: node runtime resolved WSL mode
> docker exec k3d-gpu-server-0 nvidia-smi                                    # Intel + NVIDIA: node started, toolkit mounts landed
> ```
>
> The last proof is a pod with `gpu.intel.com/dxg: 1` running `clinfo` from
> `intel/intel-opencl-icd` (upstream's `demo/intelgpu-job-wsl.yaml`). It should
> report one device.

### Node Feature Discovery

Every cluster also runs [Node Feature Discovery](https://github.com/kubernetes-sigs/node-feature-discovery)
v0.19.0 with Intel's
[NodeFeatureRules](https://github.com/intel/intel-device-plugins-for-kubernetes/tree/v0.37.1/deployments/nfd/overlays/node-feature-rules)
v0.37.1, baked into the image (namespace `node-feature-discovery`). About 30 s
after `up`, nodes carry the host's CPU, kernel and PCI features. On an
i9-13900T with a UHD 770 and two RTX Ada cards (k3s v1.37.1), for example:

```
feature.node.kubernetes.io/cpu-cpuid.HYBRID_CPU=true   # 49 cpu-cpuid labels from NFD, 6 from the launcher
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
bind-mounts the host's `/lib/modules`, `/boot/config-$(uname -r)` and `/etc/os-release` read-only
at `/k3d-gpu/host` in the node, and the baked worker reads them from there.
Stock NFD in a k3d node reports the node image's OS (`ubuntu`) and drops its
enabled-module feature, so Intel's `intel.feature.node.kubernetes.io/gpu` rule
never matches. A cluster made from this image with plain `k3d cluster create`
(no launcher mounts) still gets the CPU, PCI and kernel-version labels, but no
os-release labels and no `intel.feature.node.kubernetes.io/gpu`.

Each label has one owner. The launcher sets `intel.feature.node.kubernetes.io/npu`,
`dsa`, `iaa` and `qat`, `k3d-gpu.io/*`, `gpu.intel.com/xmx` and `media-*`, and the
`cpu-cpuid.*` labels for the CPU flags it reads from `/proc/cpuinfo` (`AVXVNNI`,
`AVX512F`, `AVX512VNNI`, `AVX512BF16`, `VAES`, `GFNI`, `SHA`, `CETIBT`, `CETSS`,
`AMX*`). They exist from node registration on, so plugins and `agent up` see them
at once, and still do when the NFD image cannot be pulled. The baked NFD does not
publish them: its worker config blacklists those cpuid names, and Intel's `npu`,
`dsa`, `iaa` and `qat` rules are dropped. Those rules fire on the PCI device plus
its driver, before any work queue or VF exists, while the launcher only labels
once the host side is set up. Two writers would also fight: nfd-master takes
ownership of every label it publishes and deletes it when its own feature goes
away, launcher label or not. NFD owns everything else, including
`intel.feature.node.kubernetes.io/gpu` and `cpu-cpuid` names the launcher does
not set.

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
| allocatable `habana.ai/gaudi` | `HPU` | one Gaudi, served by vLLM (see [vLLM on Gaudi](#vllm-on-gaudi)) |
| allocatable `gpu.intel.com/xe`, else `gpu.intel.com/i915`, on a node labelled `gpu.intel.com/xmx=true`, skipping a driver labelled `k3d-gpu.io/xmx.<driver>=false` | `GPU` | one GPU, a `gpu.intel.com/xmx: "true"` node selector; the host render group is added as a supplemental group |
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
| `K3D_GPU_AGENT_DEVICE` | `auto` | `auto`: a Gaudi, else the GPU only on an XMX node, else CPU. `CPU`, `GPU`, `NPU` or `HPU` (any case) to force one; forcing a device the cluster does not advertise, or an Intel GPU the firmware gatekeeper refuses, fails |
| `K3D_GPU_AGENT_MODEL` | `OpenVINO/Qwen3-4B-int4-ov` | Hugging Face model in OpenVINO IR format |
| `K3D_GPU_AGENT_MODEL_NAME` | last part of the model id | name clients send as `model` |
| `K3D_GPU_AGENT_DRAFT` | unset | OVMS Fast Draft speculative decoding. Unset: a CPU target serving an OpenVINO Qwen3 model drafts with `OpenVINO/Qwen3-0.6B-int4-ov` on the CPU (1.39x decode tok/s, 0.75x package J/token on an i9-13900T under matched background load [ledger:H34]); any other target has no draft. `off` disables it; `auto` drafts on the target's device; `CPU`, `GPU` or `NPU` name the draft device |
| `K3D_GPU_AGENT_DRAFT_MODEL` | `OpenVINO/Qwen3-0.6B-int4-ov` | draft model; a non-default draft model is used only with an explicit `K3D_GPU_AGENT_DRAFT` |
| `K3D_GPU_AGENT_TOOL_PARSER` / `_REASONING_PARSER` | `hermes3` / `qwen3` for the default model, unset otherwise | unset lets OVMS detect them from the chat template; empty turns one off |
| `K3D_GPU_AGENT_CPUS` | see above | CPU request (balloon size on hybrid nodes) |
| `K3D_GPU_AGENT_MEMORY` | `6Gi` | memory request |
| `K3D_GPU_AGENT_STORAGE` | `20Gi` | PVC size |
| `K3D_GPU_AGENT_TIMEOUT` | `1800` | seconds `agent up` waits for download + load |
| `K3D_GPU_AGENT_LOG_LEVEL` | `INFO` | `DEBUG` also logs the compiled model's execution device (and every request) |
| `K3D_GPU_AGENT_IMAGE` | `openvino/model_server@sha256:a928…fc58` (2026.4.1-gpu) | server image |
| `K3D_GPU_AGENT_BACKEND` | `ovms` (`vllm` on a Gaudi) | `ovms`, `vllm` or `llm-scaler` (see below) |

#### vLLM backends for discrete Arc (Battlemage)

`K3D_GPU_AGENT_BACKEND=vllm` or `llm-scaler` swaps OVMS for vLLM, serving the
OpenAI API at `/v1` from `svc/vllm`:

| Backend | Image (pinned by digest) | Notes |
|---------|--------------------------|-------|
| `vllm` | `vllm/vllm-openai-xpu:v0.31.0` | upstream vLLM for Intel GPUs; validated on Arc Pro B-series |
| `llm-scaler` | `intel/llm-scaler-vllm:1.5` (vLLM 0.26.0-b2) | Intel's build for Arc B60/B70 |

```bash
K3D_GPU_AGENT_BACKEND=vllm k3d-gpu agent up
kubectl -n k3d-gpu-agent port-forward svc/vllm 8000:8000 &
curl http://localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"Qwen3-4B","messages":[{"role":"user","content":"hi"}]}'
```

Both run on an Intel GPU only (`vllm` also on a Gaudi, below): for them `auto`
means the first allocatable `gpu.intel.com/xe`, else `i915`, with no XMX gate, and `K3D_GPU_AGENT_DEVICE=CPU`
or `NPU` is refused. A host whose Intel GPU is not Battlemage gets a warning, not
a refusal. The default model is `Qwen/Qwen3-4B` at a pinned revision (8 GB in
float16, fits a 12 GB B580) with the `hermes` tool and `qwen3` reasoning parsers.
Flags and environment follow Intel's Arc runtime in
[intel/enterprise-inference](https://github.com/intel/enterprise-inference)
(`--dtype float16`, `--block-size 64`, `--enforce-eager`, no prefix caching).
Weights and SYCL/Triton caches live in `pvc/vllm-models`. `agent up` stops the
other backend first so its GPU is free; `agent down` and `agent status` cover both.
The chat UI below and other clients that use `svc/ovms` at `/v3` need
`http://vllm.k3d-gpu-agent.svc:8000/v1` instead.

Not yet run on Arc hardware here: serving end to end and tensor parallelism
above 1 (`CCL_ZE_IPC_EXCHANGE=pidfd`; oneCCL calls `prctl(PR_SET_PTRACER, getppid())`,
so sibling workers should need no `CAP_SYS_PTRACE` under yama `ptrace_scope=1`).

| Variable | Default | Description |
|----------|---------|-------------|
| `K3D_GPU_AGENT_MODEL` | `Qwen/Qwen3-4B` | Hugging Face model (safetensors) |
| `K3D_GPU_AGENT_REVISION` | pinned commit for the default model, unset otherwise | `--revision` |
| `K3D_GPU_AGENT_GPUS` | `1` | GPUs to shard across (`--tensor-parallel-size`) |
| `K3D_GPU_AGENT_MAX_LEN` | `8192` | `--max-model-len` |
| `K3D_GPU_AGENT_MEMORY` | `8Gi` | memory request |

#### vLLM on Gaudi

When the cluster advertises `habana.ai/gaudi`, `auto` picks the Gaudi (`HPU`)
and serves it with the `vllm` backend: Intel's prebuilt
`vault.habana.ai/gaudi-docker/1.24.2/ubuntu24.04/habanalabs/vllm-0.30.0-ptupstream-2.13.0:1.24.2-481`
image (vllm-gaudi v0.30.0 on Gaudi software 1.24.2, the release of the baked
device plugin; public, pinned by digest). Same `svc/vllm`, `/v1` API, default
model (`Qwen/Qwen3-4B` at the pinned revision, here in bfloat16) and parsers as
on Arc; the variables above apply.

```bash
k3d-gpu agent up                              # a Gaudi in the cluster: vLLM on it
kubectl -n k3d-gpu-agent port-forward svc/vllm 8000:8000 &
curl http://localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"Qwen3-4B","messages":[{"role":"user","content":"hi"}]}'
```

`K3D_GPU_AGENT_DEVICE=HPU` (or `gaudi`) requires a Gaudi; `CPU` or `GPU` skips
it. An explicit `K3D_GPU_AGENT_BACKEND=ovms` or `llm-scaler` never picks a Gaudi,
and refuses `HPU`: neither runs on it. The pod gets `PT_HPU_LAZY_MODE=0` (eager +
torch.compile, the vllm-gaudi default), the PT_HPU settings vllm-gaudi documents
for tensor parallelism and Qwen models, `SYS_NICE`, an 8 GiB memory-backed
`/dev/shm` (Intel runs it with `--ipc=host`) and a 16Gi memory request.
`HABANA_VISIBLE_DEVICES` comes from the device plugin. `--max-model-len` also
bounds the HPU warm-up, which runs on every start and takes minutes;
`K3D_GPU_AGENT_TIMEOUT` (1800 s) bounds it.

Not yet run on Gaudi hardware here: serving end to end. The device plugin runs
without Intel's `habana-container-runtime` hook (see `share/gaudi-device-plugin.yml`),
so whether vLLM opens the HPU with only the plugin's device nodes and environment
is unverified. vllm-gaudi validates `Qwen3-0.6B`, not `Qwen3-4B`, and tensor
parallelism above 1 is untested.

#### Multimodal add-ons: vision, speech-to-text, video analytics

`--with` adds services next to the LLM, each placed on the Intel engine that
suits it:

```bash
k3d-gpu agent up --with vlm,stt,video
k3d-gpu agent manifest --with vlm,stt,video   # print the YAML instead
```

| Add-on | What runs | Placement (auto) | Why |
|--------|-----------|------------------|-----|
| `vlm` | OVMS 2026.4.1, `OpenVINO/Qwen3-VL-4B-Instruct-int4-ov`; `/v3/chat/completions` with `image_url` parts (base64 data URLs; set `K3D_GPU_AGENT_VLM_MEDIA_DOMAINS` to allow http(s) image URLs) | GPU, else CPU. NPU only when asked: it then serves `OpenVINO/Phi-3.5-vision-instruct-fp16-ov`, as in OVMS's NPU demo | The GPU's XMX/EU arrays run both the image encoder and the int4 decoder |
| `stt` | OVMS, `OpenVINO/whisper-large-v3-turbo-fp16-ov`; `/v3/audio/transcriptions` and `/v3/audio/translations` | NPU, else GPU, else CPU | Whisper has a fixed-shape NPU pipeline in OpenVINO GenAI, and on the NPU it leaves the GPU to vision and video |
| `video` | Intel DL Streamer 2026.2.0, OMZ `person-vehicle-bike-detection-2004`; detections as JSON lines in `kubectl logs` | GPU, else CPU. Never NPU | Decode runs on the GPU's video engines (vcs0/vcs1 on a UHD 770), scaling and colour conversion on the video-enhancement engine (vecs0) and inference on render/compute, with frames kept as VA surfaces throughout. Only the GPU has media engines |

The add-ons always run OVMS (or DL Streamer), whatever `K3D_GPU_AGENT_BACKEND`
the LLM uses. The LLM's device is the one `agent up` picks without add-ons (a
Gaudi, else an XMX GPU, else P-cores); add-ons never move it. The Intel device
plugins give each GPU or NPU to one pod unless sharing is configured, so
`agent up` counts the free units (allocatable minus what pods in other
namespaces hold, minus the LLM's) and plans the add-ons into what is left.
Explicit `K3D_GPU_AGENT_VLM_DEVICE` / `_STT_DEVICE` / `_VIDEO_DEVICE` requests
are served first and fail if nothing is free. Then auto add-ons claim engines in
order: video, vlm, stt. On an iGPU without XMX and an NPU, the LLM runs on the
P-cores, video gets the iGPU, stt the NPU, and the VLM falls back to CPU with a
warning. On one XMX GPU the LLM keeps it and video decodes on CPU.

`agent up` deletes the add-ons left out of its `--with` list, and waits for
their pods to go, before it applies the new plan. A device an earlier add-on
held is then free for the new one. Their PVCs stay. `agent down` removes the
add-ons too.

On a hybrid node, add-ons using an accelerator sit on E-cores and CPU ones on
P-cores. The LLM stays on P-cores on any device: its GPU figures above were
measured that way, and E-cores for it are unmeasured.

On the NPU the VLM is compiled for a fixed prompt length, which the image
tokens count against. OVMS's default is 1024; `agent up` sets 4096
(`K3D_GPU_AGENT_VLM_MAX_PROMPT_LEN` changes it).

`K3D_GPU_AGENT_VIDEO_SOURCE` points the pipeline at a stream URI (`rtsp://…`,
`https://…`). Host paths and `file://` are refused, because the pod cannot see
the host's files. By default it loops DL Streamer's sample clip, pinned by
commit and checksum.

Not yet run on hardware here: the add-ons end to end (VA decode and
`va-surface-sharing` in DL Streamer 2026.2.0, Whisper on the NPU, Qwen3-VL on the
GPU, Phi-3.5-vision on the NPU). `tests/agent_kubeconform.sh` schema-checks every
manifest they render.

### Chat UI for the agent (optional)

`share/agent-chat-ui.yml` (installed as `/usr/share/k3d-gpu/agent-chat-ui.yml`)
runs [Open WebUI](https://github.com/open-webui/open-webui) v0.12.2, pinned by
digest, in front of the agent's OpenVINO Model Server. It is not deployed by
`up`; apply it when you want it. Its only backend is the `svc/ovms` that
`k3d-gpu agent up` creates, so start the agent first:

```bash
# Optional, before the first apply: create the admin account up front.
kubectl create namespace k3d-gpu-agent
kubectl -n k3d-gpu-agent create secret generic open-webui-admin \
  --from-literal=email=you@example.com --from-literal=password='<long random password>'

kubectl apply -f /usr/share/k3d-gpu/agent-chat-ui.yml
kubectl -n k3d-gpu-agent rollout status deploy/open-webui --timeout=300s
kubectl -n k3d-gpu-agent port-forward svc/open-webui 8080:8080   # open http://localhost:8080
```

| Setting | Value |
|---------|-------|
| Connection | `OPENAI_API_BASE_URL=http://ovms.k3d-gpu-agent.svc:8000/v3` — OVMS serves the OpenAI API under `/v3`, not `/v1`. Open WebUI lists the models from `/v3/models`; `DEFAULT_MODELS=Qwen3-4B-int4-ov`. Ollama is off. |
| Placement | pod label `k3d-gpu.io/cores=efficient`, 1 CPU request: on a hybrid CPU the UI runs on E-cores and leaves the P-cores to inference. No effect on other hosts. |
| Storage | PVC `open-webui-data` (2Gi, `local-path`) at `/data` (`DATA_DIR`): users, chats, settings, uploads and the session key. |

Security:

- **Sign-in is on** (`WEBUI_AUTH=True`). With the `open-webui-admin` Secret the
  admin account is created on first start and self sign-up is switched off.
  Without it, **the first account to sign up becomes admin** — do that right
  after the first apply. Later sign-ups wait for admin approval
  (`DEFAULT_USER_ROLE=pending`).
- The session-signing key (`WEBUI_SECRET_KEY`) is generated on first start into
  `/data/.webui_secret_key`. No secret sits in the manifest; deleting the PVC
  logs everyone out.
- The Service is `ClusterIP`. `kubectl port-forward` listens on 127.0.0.1 only.
  Do not expose it with a NodePort, LoadBalancer or `--address 0.0.0.0` without
  TLS in front. Traffic to OVMS stays in the cluster, and OVMS itself has no
  API key, so anything that can reach `svc/ovms` can use the model.
- `OFFLINE_MODE=True`: no Hugging Face downloads and no version checks; the
  embedding models baked into the image are used. `ENABLE_COMMUNITY_SHARING=False`.
  The image already disables its analytics (`SCARF_NO_ANALYTICS`, `DO_NOT_TRACK`,
  `ANONYMIZED_TELEMETRY=false`).
- The image runs as root (it writes under `/app/backend` at runtime); the pod
  sets `allowPrivilegeEscalation: false` and the `RuntimeDefault` seccomp profile.
- Open WebUI keeps its settings in the database after the first start
  (`ENABLE_PERSISTENT_CONFIG`), so later edits to these env values do not apply
  to an existing PVC; change them in Admin Panel > Settings > Connections.

Remove it and its data, leaving the agent alone:
`kubectl -n k3d-gpu-agent delete deploy/open-webui svc/open-webui pvc/open-webui-data`.
(`kubectl delete -f` on the manifest would also delete the `k3d-gpu-agent`
namespace, and with it the agent.)

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

### Facts and plan (`facts`, `plan`)

`k3d-gpu facts` puts what `detect`, `doctor`, `doctor --perf` and `report` read
into one JSON document, `facts/v1` (schema: `console/facts.v1.schema.json`), and
writes it to `${XDG_STATE_HOME:-~/.local/state}/k3d-gpu/facts.json`.
`facts --json`, `detect --json` and `doctor --json` print the same document. It
holds the report's `host`, `detected` and `cluster` members unchanged, the node
labels `up` sets, one `targets[]` entry per place an engine can run (CPU, each
Intel/NVIDIA GPU, NPU, Gaudi) with whether it is usable and why not, every
doctor and doctor --perf finding (level, text, evidence, apply and revert), and
a `fingerprint` of the hardware alone. Nothing in it comes from the clock, so
the same host gives the same bytes; `tests/sim_matrix_test.sh` checks that for
every fixture (`tests/fixtures/*/facts.json`).

`k3d-gpu plan` feeds facts/v1 and the matrix of measured cells
(`console/matrix.v1.json`) to `plan()` (`console/plan.py`), a pure function
that returns the engine, model, placement (P-core balloon, CPU or device
resource), port and auth mode. It picks only a **green** cell: one with a
`k3d-gpu report` run, a passing tool-calling eval and the engine image this
release pins. Community cells are amber, everything else grey, and neither is
ever picked or interpolated. With no green cell for the box it prints
``unmeasured: no green cell for this box; quickstart and agent up still pick by device``.
There is no `prove` command yet (it is a design in docs/console-design.md);
`k3d-gpu quickstart` and `k3d-gpu agent up` serve with their device rules
regardless, and `k3d-gpu capacity` measures this box. Today no cell is green: the two
cells from the [ledger:H6] run have no report run and no tool-calling eval yet,
so every host plans unmeasured. `plan --explain` prints each target, each cell
that applies with its status and reasons, and the decision; `plan --json` is
the same as `k3d-gpu.plan/v1`. `--engine`, `--device`, `--model`, `--bind` and
`--port` override; a non-loopback `--bind` turns auth to `oidc`. `--facts FILE`
plans from a saved document instead of this host. Both need `python3` (standard
library only).

**Firmware gatekeeper.** Run as root, `doctor` reads each Intel GPU's GuC record
from the i915/xe debugfs and compares it with the version the running driver
asks for. A GuC older than that, or one that is not `RUNNING` (`MISSING`,
`ERROR`, `LOAD FAIL`, ...), refuses every engine on that GPU with one line:

```
fail: Intel Raptor Lake-S (0xa780) at 0000:00:02.0: GuC i915/tgl_guc_70.1.1.bin 70.1.1 is older than the 70.5.1 i915 wants; GPU engines refused; fix: install linux-firmware with i915/tgl_guc_70.bin 70.5.1 or newer, then reboot
```

Both drivers would still run an older minor release; the gate is stricter on
purpose. xe, and i915 from Alder Lake-P and Raptor Lake-S on, submit all GPU
work through the GuC, so the refusal covers OVMS on the GPU and llama.cpp on
Vulkan alike. On Alder Lake-S, i915 loads the GuC only to authenticate the HuC
and submits through execlists, so a GuC fault there warns instead. A failed HuC
only warns: it runs media decode and encode, not inference. Without root the GuC is reported as not checked. The refused GPU's
facts/v1 target reads `usable: false` with the `reason` and `fix`; `plan` then
falls back to the next green cell on a usable target (OVMS on the CPU, or
llama.cpp on Vulkan, pinned as `ghcr.io/ggml-org/llama.cpp:server-vulkan-v0.5.0`
by digest) and lists the fix. With nothing left to pick the decision is `fix`,
not an engine; a Gaudi without its driver plans the same way. `agent up` follows
the same verdict: `auto` uses OVMS on the CPU and says why, and
`K3D_GPU_AGENT_DEVICE=GPU` stops with the fix.

`k3d-gpu facts --html` prints `report.html`, the page of a Proof Pack: the
plan's decision, every target, the matrix cells considered, capacity rows, every
doctor and doctor --perf finding, and the host inventory, rendered from facts/v1
by `console/report_html.py`. It is one self-contained file: inline styles only,
no script, and a Content-Security-Policy of `default-src 'none'`, so it opens
offline and fetches nothing. The only URLs in it are links a reader can follow
(the finding sources and the project page). `--facts FILE` renders a saved
facts/v1 document and `--matrix FILE` a different matrix. The page takes nothing
from the clock, so the same facts give the same bytes.

`plan --hash` prints the plan hash, `sha256:` plus the SHA-256 of the
`plan --json` bytes. `k3d-gpu apply --plan-hash HASH` with the same options
recomputes the plan and runs it (`agent up` with the plan's OVMS image, device,
model and P-core count) only if it still hashes to `HASH`; a changed host,
matrix or option set gives another hash and apply refuses, as it does for a plan
with no engine and for a plan whose endpoint port is not 8000, the one port
`agent up` serves.

### Audit and images.lock (`audit`)

`images.lock` lists every container image k3d-gpu ships, each pinned by its
registry digest: the images in `share/**/*.yml` (the opt-in telemetry too), the Dockerfile's `FROM` images,
the launcher's agent backend, add-on and test-pod images, and the system images k3s itself
starts (from the `k3s-images.txt` of the Dockerfile's `K3S_TAG`). The node
image the Dockerfile builds is not in it. CI runs `scripts/images-lock check`,
which fails when the lock misses an image the repo references, holds one it no
longer does, or disagrees with a digest the repo pins; `scripts/images-lock
update` re-reads the digests from the registries, and the scheduled bump jobs
(plugins and k3s, and each re-rendered upstream chart) run it after each bump.

`k3d-gpu audit` reads the running cluster (`docker inspect` and `crictl images`
on each node of cluster `K3D_GPU_CLUSTER`, its workloads through kubectl, and
the kubeconfig files' modes) and fails, with a fix per finding, on:

- a listener off loopback with no authentication it can establish: a node port
  published on `0.0.0.0` or `::` other than the API server, which k3s runs with
  anonymous auth off;
- an image in containerd whose digest images.lock does not pin for its name, or
  that has no registry digest;
- an image pulled at runtime: the live read takes kubelet's `Pulled` events
  ("Successfully pulled image", not "already present"), which the API server
  keeps for one hour by default, so an older pull is not seen;
- a kubeconfig readable by group or others (it holds the cluster admin key);
- Open WebUI with `WEBUI_AUTH` off.

The default layout (`up` then `agent up`, endpoint through `kubectl
port-forward` on 127.0.0.1) passes the listener, image, kubeconfig and Open
WebUI rules (`tests/console/audit/default`). Within an hour of its first start
it still fails the runtime-pull rule: the node image does not bake the k3s
system images, the device plugins or the agent images yet, so containerd pulls
them then. `audit --json` prints `k3d-gpu.audit/v1`;
`audit --observe` prints what it read (`k3d-gpu.observed/v1`), and `audit
--observed FILE` checks a saved one instead of the cluster. `--plan FILE` adds
the endpoint a `plan --json` document plans: off loopback it must be `oidc`.
Exit 0 pass, 1 fail, 2 bad input. Needs `python3` (standard library only).

### Capacity and tuning (`capacity`, `tune`)

Both commands spend a wall-clock budget, keep what they learned on disk, and
pick up from it on the next run, so a rerun only refines the last answer.

`k3d-gpu capacity` finds agents per box: the largest number of closed-loop
agents (`bench/apb/capacity.py`) whose p95 turn latency stays under `--slo`
(default 10 s) against the cluster's OVMS/vLLM chat endpoint.

```bash
k3d-gpu capacity                    # budget 15 min; prints AGENTS PER BOX and the bracket
k3d-gpu capacity --budget 5         # stops early: "APB >= a (budget)" is what is proven so far
k3d-gpu capacity                    # resumes from the stored bracket, refines it
k3d-gpu capacity --fresh            # forget the stored bracket and start over
```

- N doubles (1, 2, 4, 8, ...) until a step does not pass, then bisects between
  the largest pass and the smallest failure, up to `--max` (default 32).
- A step repeats only while the bootstrap confidence interval of its p95
  straddles the SLO: at most `--reps` (default 3) repetitions, fewer when
  the answer is already clear. `--step-cap` (default 180 s) stops adding
  repetitions to a step.
- When `--budget` runs out the report gives the bracket found so far, marked
  `(budget)`, with status `partial`; agents per box is its proven lower end,
  or `APB unknown (budget)` when no step passed yet (never 0). A step the
  budget cut short is unmeasured, not a fail: it does not bound the bracket,
  and the next run measures that N.
- A repetition is not started when it would end past `--budget`: its length
  is estimated from the slowest session time seen so far (the warm-up
  included) times the sessions per agent. The report then names the next
  step and the time one repetition of it needs.
- The bracket is stored per host, engine, model, endpoint, SLO and trace in
  `$XDG_STATE_HOME/k3d-gpu/capacity/state/`. A closed bracket is answered from
  there without sending any load; a bracket left open by the budget is not.
  Stores written by older versions, where a budget-cut step closed the
  bracket, are rebuilt from their measured steps on load.

`k3d-gpu tune run` tunes the OVMS agent's serving config on this host from its
current best (the stored profile's winner, else the default):

```bash
k3d-gpu tune run                    # budget 10 min
k3d-gpu tune run --budget 20 --objective ttft
k3d-gpu tune show                   # incumbent and the history of runs
k3d-gpu tune run --full             # the full successive-halving search instead
```

- Each run screens the incumbent's untried one-knob neighbours with one
  short trial each, then confirms only the best screener against the
  incumbent: interleaved runs, five per side, with the same gates as the full
  search (margin `--margin 0.05`, separation beyond the run-to-run spread,
  Mann-Whitney, guardrail metric, quality probe).
- The incumbent stays unless the challenger passes every gate. When the budget
  ends mid-confirmation nothing changes.
- A confirmation is ten long trials. When it would take more than three
  quarters of `--budget`, the run spends the whole budget screening and keeps
  the best screener as `pending`, printing the `--budget` that confirms it.
  The next run with that budget confirms the pending challenger first, with no
  screening; a shorter run keeps screening the remaining neighbours.
- Trials stop at the budget: a fifth of `--budget` (at most 2 min) is kept for
  redeploying the result, and a trial still running at that point (a first
  download of a precision sibling, say) is stopped and tried again next run.
- The profile (`~/.config/k3d-gpu/tune/<fingerprint>.json`) records which
  neighbours were already screened against which incumbent, so the next run
  tries only the rest; with none left it reports `converged`.
- Every trial reuses the compiled-model cache on the PVC (`--cache_dir`). A
  screening trial of the config already deployed runs on the live pod with no
  restart; every other trial redeploys, because each tuned knob is an OVMS
  load-time setting (graph options and `plugin_config`).

### Measurement harness (`bench/`)

Small tools for measuring workloads on a k3d-gpu cluster. They are not
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

**`bench/agent_trace.py`** — replays a multi-turn agent trace (shared system
prompt and tool schema, growing context, bursty sessions) against an
OpenAI-compatible endpoint and records per-turn TTFT, decode tokens/s, cached
prompt tokens and tool-call validity; `--cache-bust` A/Bs the prefix cache.
`bench/agent_trace_gen.py` writes the synthetic trace families. Usage and
method: [bench/AGENT_TRACE.md](bench/AGENT_TRACE.md).

`tests/bench_test.sh` tests all four without root, a cluster or a model,
using a fake RAPL tree, `tests/openai_stub.py` (a streaming stub server) and
the in-process stub in `bench/test_agent_trace.py`.

### Intel Enterprise Agent Toolkit

[integrations/eat](integrations/eat/README.md) runs Intel's
[Enterprise Agent Toolkit](https://github.com/intel/enterprise-agent-toolkit)
(LiteLLM gateway, Redis, pgvector, agent sandbox, CPU vLLM) on a single k3d-gpu
node through EAT's own brownfield path, on a non-Xeon client CPU. Its vLLM pod
joins the hybrid balloons policy's `performance` balloon through EAT's own chart
annotation, so EAT's CPU balloons stay off and `hybrid` stays on. It deploys EAT's
latest tag, `release-1.0.3-hotfix`; OpenShell (`deploy_openshell`) is on EAT `main`
only, so `eat-up.sh` refuses it (see [docs/platforms.md](docs/platforms.md)).

### Experimental features (auto-adapting)

Some upstream features are still alpha, beta or preview: Kubernetes DRA and
ImageVolume gates, the OpenVINO NPU turbo property, sched_ext, xe SR-IOV and
`force_probe`. By default `up` auto-enables the ones this host supports that are
beta or GA in the shipped k3s and need no host change (`dra` and
`image-volume`, and `npu-turbo` on NPU hosts). A gate already on by default in
the shipped k3s is not passed again. `K3D_GPU_EXPERIMENTAL=<name,...>` forces exactly that
list, `off` disables all, and an unknown name is an error. `K3D_GPU_EXPERIMENTAL=list k3d-gpu detect`
or `k3d-gpu doctor --experimental` shows what this host can use and why.
k3d-gpu only sets cluster-side options: feature gates, node labels and an
OpenVINO plugin-config ConfigMap. A feature that needs a host kernel or module
change prints the command and its revert, and k3d-gpu never runs it. See
**[docs/experimental.md](docs/experimental.md)** for the ranking and sources.

### Intel GPU DRA mode

`K3D_GPU_DRA=1 k3d-gpu up` hands Intel GPUs to pods through the
[Intel GPU DRA driver](https://github.com/intel/intel-resource-drivers-for-kubernetes)
(`gpu-v0.12.1`, `ghcr.io/intel/intel-gpu-resource-driver`) instead of
intel-gpu-plugin. The two never run together: k3s skips `intel-gpu-plugin` in
DRA mode and `intel-gpu-dra` otherwise. The driver publishes each GPU in a
ResourceSlice, and a pod gets one through a ResourceClaim:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: intel-gpu
spec:
  spec:
    devices:
      requests:
      - name: gpu
        exactly:
          deviceClassName: gpu.intel.com
---
apiVersion: v1
kind: Pod
metadata:
  name: app
spec:
  resourceClaims:
  - name: gpu
    resourceClaimTemplateName: intel-gpu
  containers:
  - name: app
    image: busybox:1.37
    command: ["ls", "-l", "/dev/dri"]
    resources:
      claims:
      - name: gpu
```

`up` counts the GPUs in ResourceSlices (bound to xe or i915, untainted) instead of
node allocatable, and `test` runs a pod like the one above. DRA mode is
experimental and needs Kubernetes 1.36+, the driver's supported floor upstream
(where DRADeviceTaints is on by default, so unhealthy GPUs stay tainted); `up`
fails on an older cluster (the image ships k3s v1.37). The driver's DaemonSet only runs on nodes
labelled `k3d-gpu.io/intel-gpu-dra=true`, which `up` sets in DRA mode, so a
cluster created any other way never runs it. The driver runs with
binding management off, so it never moves a host GPU between xe/i915 and vfio-pci.
Pods ask for `gpu.intel.com` claims, not `gpu.intel.com/xe` or `/i915` resources.

DRA mode stays opt-in, also on k3s 1.37 where DRA is GA, because with gpu-v0.12.1
it loses what the default gives: the driver publishes each GPU as one exclusive
device, so a second pod's claim stays Pending where the plugin's iGPU sharing
default runs both [ledger:H17]; `K3D_GPU_INTEL_SHARED`, `_ALLOCATION` and
`_MONITORING` configure the plugin and are ignored (with a warning) in DRA mode;
and `k3d-gpu agent` and the recipes request `gpu.intel.com/xe|i915`, which DRA mode
does not advertise (on a DRA GPU, `agent` in auto mode serves on the CPU and says
why; a forced GPU, a GPU draft or a vLLM backend stops and names `K3D_GPU_DRA=0`). A claim pod gets the iGPU through the driver on k3s 1.37
[ledger:H16]. The `dra` experimental feature only sets Kubernetes gates and does
not switch the Intel GPU path.

### Agent sandboxes on E-cores

`k3d-gpu sandbox up` installs [kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox)
v1.0.7 (controller image pinned by digest) and a `python-ecores` SandboxTemplate
plus a one-replica warm pool in namespace `agent-sandbox`. Sandbox pods carry
`k3d-gpu.io/cores: efficient` and request one CPU, so on a hybrid host untrusted
agent tool execution runs on E-cores and the P-cores stay free for inference.
`up` waits for a warm sandbox, then runs `status`, which reads each sandbox's
`Cpus_allowed_list` and fails if it is not inside the host's E-cores.
`k3d-gpu sandbox down` removes it all.

```bash
k3d-gpu sandbox up
kubectl create -f - <<EOF
apiVersion: extensions.agents.x-k8s.io/v1beta1
kind: SandboxClaim
metadata: {generateName: task-, namespace: agent-sandbox}
spec: {warmPoolRef: {name: python-ecores}}
EOF
```

The sandbox serves `POST /execute {"command": "..."}` on port 8888
(`registry.k8s.io/agent-sandbox/python-runtime-sandbox:v1.0.7`, pinned by digest).

What works on k3d, and what does not:

| Piece | On k3d-gpu |
|-------|------------|
| Controller, Sandbox, SandboxTemplate, SandboxClaim, SandboxWarmPool | Yes. Plain manifests, no webhook or cert-manager. |
| NetworkPolicy | Enforced by k3s's embedded kube-router controller. The template allows ingress on 8888 only from pods labelled `k3d-gpu.io/sandbox-client=true` (and upstream's `sandbox-router`). Egress is limited to cluster DNS and public addresses. RFC1918, CGNAT `100.64.0.0/10` (tailscale) and link-local are blocked, so a sandbox cannot reach pods, services, the k3d docker network, tailnet peers or the host by those addresses. A blocked connect fails at once with `Connection refused` (kube-router rejects it), not a timeout [ledger:H18]. The host's public IPs stay reachable, so bind inference servers to a private address or firewall them. Ingress is gated on a pod label that any namespace can set: fine for a single-user dev cluster, not for a shared one. |
| gVisor / Kata (`runtimeClassName`) | Not available. The node image ships neither, and Kata needs `/dev/kvm` in the node container. Sandboxes run under runc, hardened instead: non-root, all capabilities dropped, no privilege escalation, `RuntimeDefault` seccomp, no service-account token. That is container isolation, not a VM or user-space kernel boundary. |
| sandbox-router, sandboxd | Not installed. Upstream publishes them only as staging `latest-main` images, which cannot be pinned to a release. Clients reach a sandbox by pod IP (from a `k3d-gpu.io/sandbox-client=true` pod) or `kubectl port-forward`, which containerd opens inside the pod's network namespace, so the policy does not block it. |

Upstream's default policy blocks DNS as well. The profile allows `kube-dns` on
port 53 so `pip install` resolves names, and sets `HOME=/tmp` so pip's user
install has somewhere to write (the image's user 1000 has no home directory).
PyPI's addresses are public, so the egress rule allows them and `pip install
--user <pkg>` lands in `/tmp/.local` [ledger:H18]; a package index on a private
address is blocked like any other.
The image ships no `git` or `curl`; fetch with Python or pip. Edit
`share/agent-sandbox-k3d.yml` (or point `K3D_GPU_SANDBOX_PROFILE` at a copy) to
change the image, CPU request, warm pool size or policy.

### Capacity oracle (`k3d-gpu share`, opt-in)

`k3d-gpu share` turns a finished `capacity` run into a bundle for the public
per-SKU leaderboard ([docs/oracle.md](docs/oracle.md),
[docs/leaderboard.md](docs/leaderboard.md); design:
[docs/moat-design.md](docs/moat-design.md)). It never sends anything on its own.

```bash
k3d-gpu share --dry-run        # build, validate and print the bundle; write and send nothing
k3d-gpu share                  # same, then write ./k3d-gpu-share-<sha12>.json (mode 0600)
k3d-gpu share --submit         # same, then ask y/N at the terminal before opening a public pull request
```

* **What it reads**: `k3d-gpu report --json --no-cluster` (the cluster is not
  queried) and the newest finished run under
  `$XDG_STATE_HOME/k3d-gpu/capacity/` (or `--capacity FILE`).
* **What a bundle holds**: counts, device ids, ISA flags, the CPU model string,
  kernel major.minor, memory rounded to a standard size, PL1/PL2, EPP, the
  pinned tool/server/model versions, the ISO week, and each request's timings
  and token counts. Not the hostname, machine-id, serial numbers, MAC or IP
  addresses, user names, paths, PCI addresses, CPU lists, cluster or node names,
  URLs or timestamps. `oracle/project.py` (the allowlist) and
  `oracle/schema/bundle-v1.json` (every object closed, every string an enum or
  pattern) are the authority when this list and the code differ.
* **Checks before it is shown as submittable**: `oracle/validate.py`, the same
  rules the oracle ingest runs (V1-V12 in the design doc), recomputes p95 and
  agents per box from the samples and scans the bundle for this machine's own
  hostname, machine-id, user name, home path, MAC addresses and DMI serials. A
  rejected bundle is neither written nor sendable.
* **Sending**: only with `--submit`, only at an interactive terminal, only after
  a typed `y`/`yes`; there is no `--yes`. With `gh` installed and logged in it
  forks `88plug/k3d-gpu` and opens a pull request adding
  `data/oracle/<sha256>.json`; without `gh` it prints those steps. CI validates
  the file (V1-V12); a maintainer merges it and regenerates the leaderboard. The
  bundle has no identifiers, but a pull request is public under your account:
  a submission is not anonymous.

`python3 oracle/test_oracle.py` tests the schema, the projection over every sim
fixture with planted identifiers, each validator rule, the matrix generator
against `docs/oracle-matrix.md`, and that `share` never runs `gh` without the
typed confirmation (stub `gh`, pseudo-terminal). `oracle/test_fuzz.py` fuzzes the
identifier scrubber and the validator (`FUZZ_N`, `FUZZ_SEED`).

---

## Prerequisites

- **Docker** (20.10+). `packaging/install.sh` prints the install command; it never installs Docker.
- **k3d** (v5.0.0 or later) and **kubectl**. `packaging/install.sh` installs pinned
  copies into `~/.local/bin`; on Arch, `yay -S rancher-k3d-bin` and `pacman -S kubectl`.
- At least one accelerator `k3d-gpu detect` reports: an Intel GPU, NPU or Gaudi,
  Xeon AMX/DSA/IAA/QAT, hybrid P/E cores, NUMA, or an NVIDIA GPU.
- **NVIDIA only**: an up-to-date driver and the NVIDIA Container Toolkit
  (`nvidia-ctk`; the node image needs no CUDA toolkit). Intel hosts need neither.
  The quickstart LLM never runs on an NVIDIA GPU; see [docs/first-run.md](docs/first-run.md).

Per-machine install paths with time expectations: [docs/first-run.md](docs/first-run.md).

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
| DSA / IAA user work queues | `accel-config` (universe), then per disabled device: 1 engine, 1 group, 1 user WQ (shared with user PASID, else dedicated) | `accel-config`, same steps | no package: build intel/idxd-config accel-config-v4.1.9; the plan leaves DSA/IAA out until then |
| QAT VFs | `sriov_numvfs` = the PF's `sriov_totalvfs`, `modprobe vfio-pci` | same | same |

Fedora, Arch and Manjaro repos serve only their current build, so those plans
install the repo version; Arch and Manjaro always use `pacman -Syu`. Before the
plan, `host-setup` reads the failures a partial upgrade leaves behind and prints
the fix:

- **Media driver ahead of libva** (`vainfo`: `iHD_drv_video.so has no function __vaDriverInit_1_0`): the driver exports `__vaDriverInit_1_<N>` for libva 2.N and libva is older. Checked from the files and by running `vainfo`.
- **Compute runtime ahead of libstdc++** (`version 'GLIBCXX_3.4.35' not found`; `clinfo` silently loses the Intel platform): the runtime was built by a newer GCC than the installed `gcc-libs`. Checked by comparing symbol versions, running `clinfo -l` and `ldd` on the registered ICD.
- Parts newer than the kernel's default support get boot options (printed, never applied): `i915.force_probe=<id>` for i915 parts (Meteor Lake, Arrow Lake), `xe.force_probe=<id> i915.force_probe=!<id>` for xe parts.
- **Xeon DSA, IAA and QAT present but unconfigured** (no user work queue, no VFs): the plan carries the same per-device commands `k3d-gpu doctor` prints, as the intel-device-plugins v0.37.1 initcontainers configure them, and prints each revert. These last until reboot. `up` and `detect` name such engines and point here.
  A device that kernel work queues already hold (iaa_crypto, dmaengine) is left
  alone unless you pass `--with-kernel-wqs`. Disabling an idxd device clears its
  whole config, so that path snapshots it first (`accel-config save-config` to
  `/var/lib/k3d-gpu/idxd-<dev>.conf`), writes the snapshot back, adds the user WQ
  beside the kernel ones, and re-enables them. Its revert loads the same snapshot.
  Under live zswap on `deflate-iaa` the IAA device is never touched.

Downloads go to `${XDG_CACHE_HOME:-~/.cache}/k3d-gpu/host-setup` (override: `K3D_GPU_SETUP_DIR`), which must be a directory you own; it is set to mode 0700. On Ubuntu an apt pin keeps the PPA's `intel-opencl-icd`/`libze*`/`libigc*` builds from replacing the GitHub .debs.

---

## Environment Variables

These are Docker **build args** (not runtime env):

| Build arg    | Default               | Description                                  |
|--------------|-----------------------|----------------------------------------------|
| `K3S_TAG`    | `v1.37.1-k3s1`        | K3s image tag from `rancher/k3s` (auto-bumped) |
| `K3S_DIGEST` | `sha256:ca7f37d9…`    | index digest of `K3S_TAG` (auto-bumped with it) |
| `UBUNTU_IMAGE` | `ubuntu:26.04@sha256:…` | Ubuntu base image, pinned by digest (`26.04` default; `24.04` also published) |

Build a specific Ubuntu base:

```bash
docker build \
  --build-arg UBUNTU_IMAGE="ubuntu:24.04" \
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

### Rendered components

Manifests rendered from an upstream helm chart (today `share/nri-balloons.yml`) are
generated, not hand-edited. Each one has a dir under `hack/components/` pinning the
chart version and sha256, its values and its header:

```bash
hack/install-helm ~/.local/bin        # the helm pinned in hack/tools.env
hack/render-component                 # re-render every component from its pins
hack/render-component --latest nri-balloons   # move the pins to upstream latest
tests/render_test.sh
```

CI fails when a committed manifest differs from its pinned render. The daily
build-and-push workflow runs `--latest` for every component and opens one PR per
component (branch `autobump/<name>`); merging it changes `share/`, so auto-release cuts a
launcher release and the node image is rebuilt for it.

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
- [Telemetry](docs/telemetry.md): opt-in Prometheus, node-exporter (RAPL, P/E-core labels), Intel GPU and DCGM exporter manifests in `share/telemetry/`  

---

## Contributing

Contributions, issues, and feature requests are welcome! Please fork the repository and submit a pull request.

---

## Release History

| Date       | K3s Tag             | NVIDIA Plugin | Intel GPU Plugin | Gaudi Plugin |
|------------|---------------------|---------------|------------------|--------------|
| 2026-10-17 | v1.37.1-k3s1 | v0.20.1 | v0.37.1 | 1.24.2-481 |
| 2026-10-16 | v1.37.1-k3s1 | v0.20.1 | v0.37.1 | 1.24.2-481 |
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

