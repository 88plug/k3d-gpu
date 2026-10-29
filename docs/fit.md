# Fit and guard

`k3d-gpu fit` sizes a model to what this host has free right now. `agent up --model M`
and `plan --model M` call it, and `agent guard` keeps the host safe while the model
serves. The fit reads the host and the model; it never changes the host.

```
k3d-gpu fit --model Intel/Qwen3.8-Flash-Next-W4A16-AutoRound            # one line
k3d-gpu fit --model google/gemma-4-26B-A4B-it-qat-q4_0-gguf --explain   # with the arithmetic
k3d-gpu agent up --model RedHatAI/Qwen3.8-27B-INT4                       # sized, then guarded
```

The one-line summary names the placement, the context length times the sequences that
fit at once, the GPU bytes per card, the host bytes, the pod memory limit and the host
reserve. A model that cannot fit exits 3, says why, and names the smallest listed variant
that fits.

## What it reads

| Input | Source |
| --- | --- |
| VRAM per Intel card, total and in use | the driver's memory-region query on the render node: xe `DRM_IOCTL_XE_DEVICE_QUERY` (`DRM_XE_DEVICE_QUERY_MEM_REGIONS`), i915 `DRM_IOCTL_I915_QUERY` (`DRM_I915_QUERY_MEMORY_REGIONS`). xe has no sysfs file for either |
| Who holds VRAM | DRM fdinfo (`drm-resident-vram0`, `drm-resident-local0`), once per `drm-client-id` |
| RAM | `/proc/meminfo` MemTotal, MemAvailable, swap |
| cgroup limit | `memory.max` and `memory.current` up this process's cgroup v2 path |
| NUMA, huge pages | `/sys/devices/system/node/node*/meminfo`, THP mode, hugetlb pools |
| Memory pressure | `/proc/pressure/memory` |
| k3d node limit | the node container's memory limit (`docker inspect`, `HostConfig.Memory`), when it has one |
| CPU ISA | `/proc/cpuinfo` flags: AMX, AVX-512, AVX2 |
| DRAM bandwidth | a cached STREAM triad run (`~/.local/state/k3d-gpu/stream.json`, `{"triad_gbs": N}`), else the theoretical peak from SMBIOS type 17 (root only), labelled as such |
| Weights | the safetensors or GGUF headers, per tensor: experts, token embeddings, n-gram tables, lm_head, vision, MTP, the rest. OpenVINO IR by file |
| KV cache | `config.json`: full-attention, sliding-window and linear-attention layers counted apart |
| Context cap | `max_position_embeddings`, lowered by the tokenizer's `model_max_length` |

A Hugging Face model's config, tokenizer config and weight headers are fetched once per
revision into `~/.cache/k3d-gpu/models` (a few MB, not the weights). A GGUF repo has no
`config.json`; the fit reads its model card's `base_model` for that. A GGUF repo with several models
(one per quant, each possibly a gguf-split model in `-0000N-of-0000M.gguf` shards) has
every model's every shard header read; drafters (`mtp-*`, `dflash-*`) and the vision
projector are left out. The fit then picks one with no flag: int4 first (the model
optimizer's llama.cpp precision), then fewer bits, more bits last, taking the first
that holds 4 sequences at the target context. `--file F` (or `K3D_GPU_FIT_FILE`) names
one instead: a file, any of its shards, or the quant label (`IQ4_NL`). Weights that
read as 0 bytes, a split model with shards missing, or a MoE with no expert tensors are
refused, never planned. A card held by others at half its VRAM or more is busy and is
left out.

The Intel device plugin, not the fit, picks which cards a pod gets, and on its own it
knows only the cards other pods hold. So `k3d-gpu up` keeps the busy cards out of its
view. The plugin (intel-device-plugins v0.37.1) has no per-card filter: `-allow-ids` and
`-deny-ids` (`cmd/gpu_plugin/gpu_plugin.go:864-865`) match the PCI device id, which every
card of one model shares. It does drop a card whose `/dev/dri` nodes it cannot stat
(`devPathForDrmFile`, `:546`; `scan`, `:726-728`). `up` therefore mounts `/dev/dri/cardN`
and `renderDN` of the free cards only into the plugin's pod, instead of all of `/dev/dri`,
and says which cards it left out and who holds them. On a host whose SGLang fills two of
four B70s, the cluster advertises the other two, and every placement the fit makes lands
on them. The busy set is read once, at `up`, and the plugin's mounts name each free card's
nodes. A card removed or renumbered later (a reboot that reorders them) leaves a mount
with no node, and the plugin pod does not start: recreate the cluster (`k3d-gpu down`,
`k3d-gpu up`). `K3D_GPU_INTEL_CARDS=all` advertises every card.

## The arithmetic

`--explain` prints every step. For each engine the model's format allows (vLLM for
safetensors, OVMS for OpenVINO IR, llama.cpp for GGUF; llama.cpp after a GGUF
conversion when the native engine cannot run the architecture), placements are tried
in this order, and the first that fits wins:

1. one GPU;
2. several GPUs: vLLM tensor parallel (heads divisible by the size), or llama.cpp's
   layer split;
3. MoE experts in DRAM (llama.cpp `--n-cpu-moe N`): only when the GPU placements do not
   fit, the CPU has AMX or AVX-512, and the DRAM side's bandwidth bound per token stays
   above a floor. The fit takes the fewest layers whose experts leave the GPU;
4. CPU only.

Per placement:

```
weights        = bytes of each tensor class to serve (MTP left out unless the GGUF holds it)
KV per token   = full layers x 2 x kv heads x head dim x bytes
               + indexer keys (sparse attention)
               + sliding layers x 2 x kv heads x head dim x bytes, for at most the window
state per seq  = linear layers x (v heads x k dim x v dim x 4 B + conv state)
overhead (GPU) = runtime + activations (batch x widest matmul x 2 x 2 B) + logits (seqs x vocab x 4 B)
host reserve   = max(10% of MemTotal, 4 GiB);   host budget = min(MemAvailable, cgroup room) - reserve
GPU reserve    = max(5% of VRAM, 1 GiB);        GPU budget  = VRAM - in use by others - reserve
pod limit      = request = ceil(host bytes x 1.1) GiB, inside the host budget
```

Context first: the largest context, halving from the model's cap, at which 4 sequences
fit at once and that is at least 32768 tokens (or the cap). Only when no placement
reaches that does the fit take less, down to one sequence of 4096 tokens, and the
summary says it is below the target. Then as many sequences as fit at that context.

llama.cpp keeps the token embeddings and the n-gram table on the CPU by design
(`src/llama-model.cpp`, input layer "always keep it on the CPU"), so those count as host
bytes on every llama.cpp placement. OpenVINO's GPU plugin compiles from a host copy, so
an OVMS GPU placement counts the weights as host bytes too.

The overhead is an estimate from the formula, not a measurement;
`K3D_GPU_FIT_OVERHEAD_GIB` replaces it with a measured value.

## Engine flags set from the fit

| Engine | Flags (spellings from each engine's own argument parser) |
| --- | --- |
| vLLM v0.32.0 | `--tensor-parallel-size`, `--max-model-len`, `--max-num-seqs`, `--gpu-memory-utilization` (vLLM still profiles activations inside that share), `--kv-cache-dtype fp8` when the checkpoint's `kv_cache_scheme` made the fit count one byte. CPU build (EAT): `VLLM_CPU_KVCACHE_SPACE` |
| OVMS 2026.4.1 | `--max_num_seqs`, `--cache_size` (a static pool: the dynamic one grows past the fit) |
| llama.cpp v0.8.0 | `-c` (context x slots), `-np`, `-ngl`, `-ts`, `--n-cpu-moe` or `--cpu-moe`, `-ctk`/`-ctv` |

`agent up` applies them, sets the pod's memory request and limit to the fit, and
annotates the pod `k3d-gpu.io/agent-fit`. `K3D_GPU_AGENT_MAX_LEN`, `K3D_GPU_AGENT_MAX_NUM_SEQS`
and `K3D_GPU_AGENT_GPUS` are inputs the fit checks, not values that replace it. With
`K3D_GPU_AGENT_BACKEND` unset, the engine the fit picks serves the model: OVMS, vLLM or
llama-server. With `--model`, the model optimizer first picks the variant for the default
engine (OVMS: Gemma 4's OpenVINO IR, for example). When that engine has nothing to serve,
as with Qwen3.8-Flash-Next, the id is served as given, by the engine its fit picks. When
the fit then picks another engine, the optimizer's choices are made again for that engine,
pinned to the model the fit sized. A backend that is set and cannot serve the fit is
refused, with the llama-server flags to run instead. `K3D_GPU_AGENT_BACKEND=llama.cpp
--model google/gemma-4-26B-A4B-it` serves the optimizer's llama.cpp pick, Google's QAT
Q4_0 GGUF, sized by the fit.

Which engine serves a model on the CPU when several can is the ledger's call, read the way
the CPU Fast Draft default is (H34): the engine-choice rows of EXPERIMENTS.md, H59 and its
replications per CPU class (H59d, H59w). With `K3D_GPU_AGENT_BACKEND` unset, a row of that
family SUPPORTED for this host's CPU class makes llama.cpp the CPU engine: the model
optimizer then picks the model's int4 GGUF (Google's QAT Q4_0 GGUF for Gemma 4) with the QAT
MTP drafter, the arm the row measured. A REFUTED, INCONCLUSIVE or PENDING row, a row for
another CPU class, or no row keeps OVMS, as does a model with no int4 GGUF. The rows ship in
`console/ledger-rules.json` for installs without EXPERIMENTS.md, and
`k3d-gpu plan --model M --explain` names the row that decided. A GPU placement is not
affected: these are CPU rows.

## The llama.cpp backend

llama-server (ggml-org/llama.cpp v0.8.0, image tags `*-b11960`, pinned by digest) runs in
deployment `llamacpp` of the agent namespace and serves the OpenAI API at `/v1`, port 8000.
It uses the `server-intel` (SYCL) image on an Intel GPU and the `server` (CPU) image
otherwise. Each option and its value are two arguments: llama-server reads a value from the
next argument and refuses `--option=value` (`common/arg.cpp` `parse_cli_args`, `:827-865`).
Beyond the fit's flags it sets:

| Flag | Why |
| --- | --- |
| `--model` + `--model-url` | the GGUF at the commit the fit read, downloaded once into the PVC (llama.cpp resumes it and checks its ETag) |
| `--jinja`, `--reasoning off` | the GGUF's own chat template; its tool-call format is picked from the template (Gemma 4 `<\|tool_call>call:`, Qwen3.8's XML `<tool_call><function=...>`); thinking off for agent turns (`K3D_GPU_AGENT_THINKING=on` or `model` changes it) |
| `--threads`, `--threads-batch` | the pod's physical cores and CPUs (the P-core balloon on a hybrid node); llama.cpp otherwise counts the host's cores |
| `--load-mode mmap+mlock` | CPU placement only, with `IPC_LOCK`. On a GPU or experts-in-DRAM placement the lock grows to the end of the furthest CPU tensor (`src/llama-model-loader.cpp:1735-1737`), so it would also hold GPU tensors and could pass the pod limit |
| `--spec-type draft-mtp` | when the model optimizer picks it, or with `K3D_GPU_AGENT_DRAFT=mtp` (`off` disables it). The optimizer picks a GGUF's own MTP head, and on the CPU the QAT MTP assistant only where the ledger measured it winning for this CPU class: a SUPPORTED row of the H57m family (H57dm, the i9-13900T; its replication H57wm), or the H59-family row that made llama.cpp the CPU engine. A REFUTED row turns it off; [H34x](../EXPERIMENTS.md) refuted OVMS's CPU draft on the Xeon. A GGUF with an MTP head drafts from it; Google's QAT GGUF has none, so its QAT assistant is converted once to Q4_0, the target's precision |

Safetensors weights whose architecture only llama.cpp runs here, such as
Qwen3.8-Flash-Next, are converted once. A Job (`gguf-convert-<hash of its inputs>`) runs
the `full-b11960` image's `convert_hf_to_gguf.py`, with transformers 5.21.0 and
huggingface-hub 2.3.0 on `PYTHONPATH`. The image's own transformers cannot read
transformers-5 tokenizer configs. It downloads the pinned revision and writes
`model.gguf` and `manifest.json` (sha256, size, pip freeze) under `/models/gguf`. Then it
removes the download. Unquantized weights keep their 16-bit type. FP8 checkpoints are
stored as Q8_0 (`--fp8-as-q8`), the 1 byte per weight the fit counted. Other quantized
checkpoints are refused before anything runs. The converter dequantizes GPTQ and packed
int to 16 bits, a GGUF larger than the fit sized, and does not read the rest
(`conversion/base.py:575`). That includes the AutoRound W4A16 Flash-Next. A conversion
Job that failed runs again on the next `agent up`, and `agent down` deletes one still running. `agent up`
waits for the Job (`K3D_GPU_AGENT_CONVERT_TIMEOUT`, 12 hours by default), and the server
pod waits for `manifest.json`. A later `agent up` of the same model finds the GGUF and
converts nothing. `K3D_GPU_FIT=off` skips the fit; `on` also sizes the default
models, which `auto` (the default) leaves as they are, as it leaves a quickstart RAM-tier
pick and a `tune` profile's model. In `auto`, a fit that cannot run (offline, no
metadata, an unreadable host) is a warning and the model is served unsized; a fit that
runs and says no stops `agent up`.

## Guard

`agent up` of a fitted model starts `k3d-gpu agent guard` in the background
(`K3D_GPU_AGENT_GUARD=off` leaves it out; `agent down` stops it). Every 5 seconds it
reads `/proc/pressure/memory` and MemAvailable
([PSI](https://docs.kernel.org/accounting/psi.html): "some" is the share of time at
least one task stalled on memory, "full" the share all did).

```
some avg10 >= 20             one rung down (fewer sequences, then less context)
full avg10 >= 5, or          emergency: straight to the lowest rung; at the lowest rung, stop
MemAvailable < reserve / 2
some avg60 < 5 for 60 steps  one rung back up (5 minutes calm)
after any step               hold 12 steps (1 minute): the restart reloads the weights,
                             and avg10 needs about 10 seconds to fall
```

That ladder frees host memory only when the KV cache lives there (CPU placement), and
there each rung also shrinks the OVMS static KV pool and the pod limit. A server whose
KV cache is in VRAM frees none by shrinking: its pod limit already bounds it, so
pressure is reported, and an emergency that lasts three samples stops it, but only when
the server holds at least reserve / 2 of host RAM (llama.cpp's experts and n-gram table
in DRAM do; a vLLM pod's runtime does not). A stop scales the Deployment to zero;
`k3d-gpu agent up` starts it again.

## Decided at start, and dynamic

| Decided when `agent up` renders the manifest | Changed by the guard |
| --- | --- |
| engine, placement, GPU count, tensor parallel size | the rung: sequences, then context |
| `--gpu-memory-utilization`, OVMS `--cache_size`, llama.cpp `--n-cpu-moe` | stop (replicas 0) in an emergency |
| pod memory request and limit (the kernel's OOM killer acts on this pod, not on others) | |

Each guard step renders rung N of the fit `agent up` recorded
(`~/.local/state/k3d-gpu/agent-fit.json`, `K3D_GPU_FIT_RUNG=N`) and applies it, so the
server restarts with the smaller fit. It does not fit again: the running server holds
the memory a new probe would see. A second `agent up --model M` does fit again: it stops
the running server first (the Deployment is replaced anyway), so the probe sees that
server's memory as free, and starts it again when the new model does not fit. None of vLLM, OVMS or llama-server can lower admission or
the KV size of a live server: vLLM's `--max-num-active-seqs` and `--max-num-queued-reqs`
are start-up options, OVMS has no live admission control, and llama-server's slot
endpoints save, restore or erase a slot but do not change their number.

## The EAT overlay

`integrations/eat/eat-up.sh model <id>` with `EAT_FIT_MODEL=<Hugging Face id>` (and no
`EAT_VLLM_KV_GIB`) sizes EAT's vLLM CPU KV cache with
`k3d-gpu fit --engine vllm --device cpu`, and stops before touching the cluster when the
model does not fit.
