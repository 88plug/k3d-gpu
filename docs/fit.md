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
refused, never planned. A card held by
others at half its VRAM or more is busy and is left out. The Intel device plugin, not
the fit, picks which cards the pod gets, and it knows only the cards other pods hold:
a card a host process fills can still be handed out, and the fit's per-card budget
then does not hold for it.

## The arithmetic

`--explain` prints every step. For each engine the model's format allows (vLLM for
safetensors, OVMS for OpenVINO IR, llama.cpp for GGUF; llama.cpp after a GGUF
conversion when the native engine cannot run the architecture), placements are tried
in this order, and the first that fits wins:

1. one GPU;
2. several GPUs: vLLM tensor parallel (heads divisible by the size), or llama.cpp's
   layer split, sized card by card (below);
3. MoE experts in DRAM (llama.cpp `--n-cpu-moe N`): only when the GPU placements do not
   fit, the CPU has AMX or AVX-512, and the DRAM side's bandwidth bound per token stays
   above a floor. The fit takes the fewest layers whose experts leave the GPU, trying
   1, 2, ... of the free cards (largest budget first) and keeping the plan with the most
   context, then the fewest such layers, then the most sequences, then the fewest cards;
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

On several GPUs llama.cpp places whole layers, so the fit sizes each card from the
layers it gets, the way llama.cpp b11749 assigns them with `-ngl 999`
(`src/llama-model.cpp`, `load_tensors`). Layer `il` goes to the device
`upper_bound(cumsum(ts) / sum(ts), il / (layers + 1))`, and the output layer
(`il = layers`, holding `lm_head` and a GGUF's MTP tensors) goes the same way, which is
the last card. Each layer's weights are its share of the class totals, minus its experts
when it is one of the first `--n-cpu-moe` layers. The vision projector counts on the first
card: `tools/mtmd/clip.cpp` loads it on the first GPU. Each layer's KV and recurrent state
sit on its card. Every card also carries its own overhead.
The fit emits `-ts` as whole layer counts summing to layers + 1, so each card gets exactly
the layers it was sized for. It keeps every card within the smallest budget of the cards
used, because the device plugin, not the fit, decides which card is device 0 inside the
pod, and it balances the largest card's load. When no split fits, the placement is refused.
`--explain` lists each card's layers, weights, KV and total, and `card_bytes` in `--json`
has the same numbers.

The overhead is an estimate from the formula, not a measurement;
`K3D_GPU_FIT_OVERHEAD_GIB` replaces it with a measured value.

## Engine flags set from the fit

| Engine | Flags (spellings from each engine's own argument parser) |
| --- | --- |
| vLLM v0.32.0 | `--tensor-parallel-size`, `--max-model-len`, `--max-num-seqs`, `--gpu-memory-utilization` (vLLM still profiles activations inside that share), `--kv-cache-dtype fp8` when the checkpoint's `kv_cache_scheme` made the fit count one byte. CPU build (EAT): `VLLM_CPU_KVCACHE_SPACE` |
| OVMS 2026.4.1 | `--max_num_seqs`, `--cache_size` (a static pool: the dynamic one grows past the fit) |
| llama.cpp | `-c` (context x slots), `-np`, `-ngl`, `-ts` (layers per card), `--n-cpu-moe` or `--cpu-moe` |

`agent up` applies them, sets the pod's memory request and limit to the fit, and
annotates the pod `k3d-gpu.io/agent-fit`. `K3D_GPU_AGENT_MAX_LEN`, `K3D_GPU_AGENT_MAX_NUM_SEQS`
and `K3D_GPU_AGENT_GPUS` are inputs the fit checks, not values that replace it. A fit
that picks llama.cpp is refused by the OVMS and vLLM backends with the llama-server
flags to run instead. `K3D_GPU_FIT=off` skips the fit; `on` also sizes the default
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
