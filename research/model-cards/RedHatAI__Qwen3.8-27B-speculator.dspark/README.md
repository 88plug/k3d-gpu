---
library_name: speculators
base_model:
- Qwen/Qwen3.8-27B
license: apache-2.0
tags:
- speculative-decoding
- dspark
- speculators
- long-context
---

# RedHatAI/Qwen3.8-27B-speculator.dspark

## Model Overview

- **Model Architecture:** `DSparkDraftModel`
  - **Target Model Architecture:** `Qwen3_5ForConditionalGeneration`
  - **Input:** Text
  - **Output:** Text
- **Model Optimizations:**
  - **Speculative Decoding Algorithm:** DSpark
  - **Draft Model:** 5-layer Qwen3-style backbone
  - **Maximum Draft Length:** 8 tokens
  - **Auxiliary Target Layers:** 4, 12, 20, 28, 36, 44, 52, and 60
  - **Markov Head:** Vanilla, rank 256
  - **Confidence Head:** Enabled with Markov features
- **Configured Maximum Context Length:** 262,144 tokens
- **Release Date:** 2026-09-21
- **Version:** 1.0
- **Model Developers:** Red Hat AI

This is a DSpark speculator model for [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B). It was trained using the [Speculators](https://github.com/vllm-project/speculators) library (version `0.8.0.dev221`).

DSpark extends DFlash with a Markov head for modeling intra-block token dependencies and a confidence head for predicting per-position acceptance. The 5-layer draft model consumes auxiliary hidden states from layers 4, 12, 20, 28, 36, 44, 52, and 60 of the target model and proposes up to 8 tokens per decoding step.

## Training Details

This checkpoint was warm-started from an earlier Qwen3.8-27B DSpark checkpoint and trained on a Qwen3.8-27B-regenerated version of [Open PerfectBlend](https://huggingface.co/datasets/mlabonne/open-perfectblend). Data preparation produced 1,739,710 8,192-token training examples with a 99/1 train-validation split. Hidden-state extraction used four independent vLLM data-parallel replicas, while four FSDP ranks trained the draft model.

The released checkpoint is from global step 107,520, or approximately 75% of the planned one-epoch run. It used the full verifier vocabulary, `sample_from_anchor=true`, and up to 1,024 anchors per sequence.

<details>
  <summary>Key training configuration</summary>

```yaml
train:
  speculator_type: dspark
  seed: 42
  verifier:
    verifier_name_or_path: Qwen/Qwen3.8-27B
  data:
    total_seq_len: 8192
    train_data_ratio: 0.99
    max_anchors: 1024
  loss:
    loss_fn: '{"ce":0.1,"tv":0.9}'
  optimizer:
    optimizer: muon
    lr: 0.001
    muon_lr: 0.001
  trainer:
    epochs: 1
    checkpoint_freq: 0.05
    fsdp_shard: true
  dflash:
    block_size: 8
    sample_from_anchor: true
```

Hidden-state extraction used vLLM `0.29.0` with data-parallel size 4, tensor-parallel size 1, and target layer IDs `4 12 20 28 36 44 52 60`. Training used Speculators commit [`876e2b6`](https://github.com/vllm-project/speculators/commit/876e2b6f2ec84725a63fe5de25355c3c66537563).

</details>

## Model Specifications

| | |
|---|---|
| **Base Model** | Qwen/Qwen3.8-27B |
| **Chat Template** | Qwen/Qwen3.8-27B (use `/chat/completions`) |
| **Architecture** | `DSparkDraftModel` |
| **Format** | Safetensors, bfloat16 |
| **License** | Apache 2.0 |
| **Draft Layers** | 5 |
| **Target Layer IDs** | 4, 12, 20, 28, 36, 44, 52, 60 |
| **Draft Vocab Size** | 248,320 |
| **Mask Token ID** | 248,077 |
| **Training Sequence Length** | 8,192 |
| **Maximum Anchors** | 1,024 |
| **Confidence Head** | Vanilla Markov, rank 256 |
| **Training Hardware** | 8x NVIDIA H200: 4 extraction + 4 FSDP training GPUs |
| **Validation Hardware** | 1x NVIDIA H100 |

## Deployment

```bash
vllm serve Qwen/Qwen3.8-27B \
  --speculative-config '{"model":"RedHatAI/Qwen3.8-27B-speculator.dspark","num_speculative_tokens":7,"method":"dspark"}'
```
For further optimization, consider swapping Qwen/Qwen3.8-27B for a quantized checkpoint, [RedHatAI/Qwen3.8-27B-NVFP4](https://huggingface.co/RedHatAI/Qwen3.8-27B-NVFP4) or [RedHatAI/Qwen3.8-27B-INT4](https://huggingface.co/RedHatAI/Qwen3.8-27B-INT4)

```bash
vllm serve RedHatAI/Qwen3.8-27B-NVFP4 \
  --speculative-config '{"model":"RedHatAI/Qwen3.8-27B-speculator.dspark","num_speculative_tokens":7,"method":"dspark"}'
```

## Acceptance Rates

Evaluation used vLLM `0.29.0`, a dense `Qwen/Qwen3.8-27B` verifier.


Per-position acceptance rates:

| Dataset | Acceptance Length | Pos 0 | Pos 1 | Pos 2 | Pos 3 | Pos 4 | Pos 5 | Pos 6 | Pos 7 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| HumanEval | 4.42 | 82.5% | 65.4% | 51.4% | 40.9% | 33.0% | 27.0% | 22.7% | 19.5% |
| math_reasoning | 5.74 | 89.6% | 79.9% | 70.1% | 61.2% | 53.7% | 45.9% | 39.6% | 33.8% |
| qa | 4.22 | 79.0% | 62.0% | 49.1% | 39.1% | 31.3% | 25.2% | 19.9% | 15.9% |
| question | 3.80 | 78.0% | 58.2% | 43.0% | 31.9% | 24.1% | 18.6% | 14.6% | 11.6% |
| rag | 4.81 | 83.9% | 69.6% | 57.3% | 47.4% | 39.4% | 32.9% | 27.3% | 22.9% |
| summarization | 3.94 | 81.8% | 63.3% | 48.1% | 35.2% | 25.3% | 18.1% | 12.8% | 9.4% |
| tool_call | 3.76 | 77.9% | 57.6% | 42.4% | 31.4% | 23.6% | 18.0% | 14.1% | 11.2% |
| translation | 4.55 | 83.4% | 68.1% | 55.5% | 44.6% | 35.7% | 28.7% | 22.2% | 17.3% |
| writing | 3.77 | 77.7% | 57.6% | 42.6% | 31.7% | 23.9% | 18.4% | 14.4% | 11.4% |

## Long-Context Acceptance

Evaluation of `RedHatAI/Qwen3.8-27B-speculator.dspark` using 8 speculative tokens on MRCR 8-Needle.

| Prompt length | Unique requests | Mean acceptance length |
|---|---:|---:|
| 4K–8K | 29 | 4.745 |
| 8K–16K | 24 | 4.041 |
| 16K–32K | 21 | 4.627 |
| 32K–65K | 25 | 4.484 |
| 65K–131K | 23 | 4.345 |
| 131K–262K | 26 | 4.843 |
| 262K–524K | 24 | 4.671 |
| 524K–1M | 38 | 4.400 |
| 1M+ | 14 | 4.297 |
| **Overall** | **224** | **4.492** |

- Maximum successful prompt length: **1,114,783 tokens**

## Performance Eval Against MTP
Across HumanEval, math reasoning, and RAG, DSpark consistently delivers higher throughput and better interactivity than MTP with the same eight speculative tokens. The advantage is strongest at low-to-medium concurrency—particularly for math reasoning—and remains positive at concurrency 128, though it narrows for RAG. Both methods scale smoothly, while DSpark sustains lower per-token latency as load increases.

![qwen38-dspark8-vs-mtp8-top3](https://cdn-uploads.huggingface.co/production/uploads/67f401f4bb5b52cdad90f9a7/voDTSDis5MscKy65-y-i-.png)

