# Model-card reference: Gemma-4-26B-A4B, Qwen3.8-27B, Qwen3.8-Flash-Next on Intel hardware

Compiled 2026-10-20 from the 24 pinned repos in `INDEX.txt` (every file read) plus the linked usage docs.
Citation keys:
- `dir/file:L` = file in this directory, line number. `api` = that dir's `api.json`.
- `[sizes]` = HF API `?blobs=true` at the pinned revision, fetched 2026-10-20.
- `[probe:*]` = measurements I ran on the pinned files (method stated inline). They are measured facts, not card claims.
- URLs = web sources read 2026-10-20.

Directory prefixes:
- `G/` = `google__gemma-4-26B-A4B-it`
- `Q/` = `Qwen__Qwen3.8-27B`
- `F/` = `Qwen__Qwen3.8-Flash-Next`
- Every other directory is spelled out in full.

---

## 1. Model families

### 1.1 Gemma-4-26B-A4B (Google DeepMind)

**Architecture**
- MoE size: 25.2B total, 3.8B active, 30 layers, 1024-token sliding window, 256K context, 262K vocab, 8 active of 128 experts plus 1 shared, text+image, vision encoder ~550M (`G/README.md:70-80`).
- Exact parameter counts:
  - `safetensors.total` = 25,805,936,206 BF16 (`G/api`).
  - The index metadata says 26,544,131,376 (`qat_idx` metadata of `google/gemma-4-26B-A4B-it-qat-q4_0-unquantized`).
- `Gemma4ForConditionalGeneration` text config (`G/config.json:20-98`):
  - hidden 2816, `moe_intermediate_size` 704, dense/shared `intermediate_size` 2112
  - 16 heads, 8 KV heads, `head_dim` 256 on sliding layers
  - global layers: `global_head_dim` 512, `num_global_key_value_heads` 2, `attention_k_eq_v: true` (unified K=V on global layers)
  - `final_logit_softcapping` 30.0, tied embeddings, `sliding_window` 1024
- Attention layout:
  - Layers alternate 5 sliding then 1 full, so layers 5, 11, 17, 23 and 29 are full (`G/config.json:36-67`). The last layer is always global (`G/README.md:49`).
  - RoPE: full layers use "proportional" p-RoPE with `partial_rotary_factor` 0.25 and theta 1e6; sliding layers use theta 1e4 (`G/config.json:79-89`).
  - Global layers have no `v_proj`. AutoRound lists 25 `v_proj` versus 30 `k_proj` (`Intel__gemma-4-26B-A4B-it-int4-mixed-AutoRound/config.json` extra_config). Google's GGUF has `attn_v` ×25 [probe:gguf-header].
- Vision tower: 27 layers, hidden 1152, `patch_size` 16, `pooling_kernel_size` 3, 280 soft tokens per image by default (`G/config.json:102-145`).
  - Visual token budgets: 70/140/280/560/1120 (`G/README.md:396-400`).
  - Order: image before text, audio after text (`G/README.md:389-392`). 26B has no audio (`G/README.md:79`).
- No n-gram embedding:
  - `hidden_size_per_layer_input: 0` (`G/config.json:32`). Per-Layer Embeddings exist only on E2B/E4B (`G/README.md:64`).
  - The OV export still ships a tiny `openvino_text_embeddings_per_layer_model` (`OpenVINO__gemma-4-26b-a4b-it-int4-ov/api`).
- License: Apache-2.0 (`G/README.md:3`). No repo in this set is gated (`gated=false` in every `api.json`).
- Training data cutoff: January 2025 (`G/README.md:433`).

**Sampling**
- One set for all modes: `temperature=1.0`, `top_p=0.95`, `top_k=64` (`G/README.md:364-368`; `G/generation_config.json`).
- No min_p or presence penalty is given.
- EOS ids: `[1, 106, 50]` (`G/generation_config.json`), i.e. `<eos>`, `<turn|>` and id 50. The base model has only `eos=1` (`google__gemma-4-26B-A4B/generation_config.json`).

**Thinking control**
- Thinking is on when `<|think|>` sits at the start of the system prompt (`G/README.md:374`).
- Output format: `<|channel>thought\n…<channel|>` then the answer (`G/README.md:376`).
- When thinking is off, 26B still emits an empty `<|channel>thought\n<channel|>` (`G/README.md:377-378`).
- Canonical template:
  - `enable_thinking` defaults to **false** (`G/chat_template.jinja:186`).
  - When false, the template pre-fills `<|channel>thought\n<channel|>` after `<|turn>model\n` (`G/chat_template.jinja:383-386`).
- Per request: `chat_template_kwargs: {"enable_thinking": true|false}`.
- RedHat validated serving inverts the default: `default-chat-template-kwargs: '{"enable_thinking": true}'` (`RedHatAI__gemma-4-26B-A4B-it/README.md:14`; FP8 card `:18`, `:317`).
- vLLM's guide also uses `--default-chat-template-kwargs '{"enable_thinking": true}'` (https://github.com/vllm-project/recipes/blob/main/Google/Gemma4.md).
- Multi-turn: drop thoughts from history, except on tool-call turns (`G/README.md:385`).
  - The template enforces this: it renders `reasoning`/`reasoning_content` only after the last user turn, or when `preserve_thinking` is set on tool-call turns (`G/chat_template.jinja:238-243`).

**Template quirks**
- Template header: "Published 2026-07-09 … Fixed tool-calling loops, turn closures, and thinking content-ordering" (`G/chat_template.jinja:1-6`).
- Same file at the pinned revision in these repos: google -it, qat-unquantized, qat-unquantized-assistant, OpenVINO int4-ov, and Google's GGUF `tokenizer.chat_template` [probe:md5, probe:gguf-header].
- RedHat ships it byte-identical except for a trailing newline.
- **Intel AutoRound gemma repos ship an older template** (`Intel__gemma-4-26B-A4B-it-int4-AutoRound/chat_template.jinja`). Differences:
  - no `role: tool` OpenAI forward-scan
  - no `null` handling
  - `<|think|>` without `\n`
  - images wrapped in `\n\n`
  - string `arguments` passed through raw
  - Override it with the canonical template.
- BOS: the template emits `bos_token` itself (`G/chat_template.jinja:188`), and Google's GGUF sets `tokenizer.ggml.add_bos_token=True` [probe:gguf-header].
  - Clients that tokenize the rendered text with `add_special_tokens=True` get a double `<bos>`.
- Tool-call wire format:
  - Call: `<|tool_call>call:NAME{key:value,…}<tool_call|>`, with strings wrapped in `<|"|>`. `arguments` must be a mapping; a string raises (`G/chat_template.jinja:245-266`).
  - Declarations: `<|tool>declaration:…<tool|>` in the system turn (`G/chat_template.jinja:207-214`).
  - Responses: `<|tool_response>response:NAME{…}<tool_response|>` (`G/chat_template.jinja:168-181`).
  - The response parser regex lives in `G/tokenizer_config.json` (`response_schema`, `response_template`).

**Tool and reasoning parsers per engine**
- vLLM:
  - Flags: `--enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4 --chat-template examples/tool_chat_template_gemma4.jinja` (`RedHatAI__gemma-4-26B-A4B-it-FP8-dynamic/README.md:69-76`; https://recipes.vllm.ai/Google/gemma-4-26B-A4B-it).
  - Recipe minimum: vLLM ≥ 0.25.0. RedHat validated on vLLM 0.24.0 (`RedHatAI__gemma-4-26B-A4B-it/README.md:27`).
  - RedHat's `required_cli_args`: `--reasoning-parser gemma4 --enable-prefix-caching` (`:13`).
- llama.cpp:
  - `--jinja` plus the built-in `COMMON_CHAT_FORMAT_PEG_GEMMA4` parser.
  - Reasoning flags: `--reasoning-format`, `-rea/--reasoning on|off|auto`.
  - PR #21760 fixed 26B-specific `<|channel>` and tool parse edge cases (https://raw.githubusercontent.com/ggml-org/llama.cpp/master/common/chat.cpp; https://github.com/ggml-org/llama.cpp/pull/21760).
- OVMS: see §1.4.

**MTP and speculative story**
- Google drafter: `google/gemma-4-26B-A4B-it-assistant`.
  - `Gemma4AssistantForCausalLM`, 4 layers (3 sliding, 1 full), hidden 1024 on backbone 2816.
  - `num_kv_shared_layers: 4`: it cross-attends to the target's KV and has no KV cache of its own.
  - Tied 262K embedding. 419.7M params, 0.84 GB BF16 (`google__gemma-4-26B-A4B-it-assistant/config.json`; `api`; [sizes]).
  - `num_centroids` 2048 is present, but the centroid head is not used on 26B: `use_ordered_embeddings: false` (`config.json:87`; https://github.com/vllm-project/recipes/blob/main/Google/Gemma4.md says centroid masking "No" for 26B).
  - HF generation config: `num_assistant_tokens: 6`, `"constant"` (`google__gemma-4-26B-A4B-it-assistant/generation_config.json`).
  - The QAT assistant's generation config lacks these keys (`google__gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant/generation_config.json`).
- Claims and MoE caveats:
  - Card: "up to 3x" and "exact same quality" (`google__gemma-4-26B-A4B-it-assistant/README.md:23`).
  - Google MTP doc, MoE caveat: "At batch size 1 this overlap is limited, which is why the 26B A4B drafter may not yield speedups on hardware platforms without good parallelism" (https://ai.google.dev/gemma/docs/mtp/overview).
  - The llama.cpp MTP PR author saw no MoE speedup, but >2x on the dense 31B (https://github.com/ggml-org/llama.cpp/pull/23398).
  - vLLM PR #41745, H100, 26B, γ=4: B1 160%, B16 204%, B64 227% (https://github.com/vllm-project/vllm/pull/41745).
- **Precision matching:** "the assistant model must also be a QAT checkpoint with the same precision" (`google__gemma-4-26B-A4B-it-qat-q4_0-gguf/README.md:32`).
  - The QAT GGUF repo ships **no assistant GGUF** (`google__gemma-4-26B-A4B-it-qat-q4_0-gguf/api` siblings).
  - The QAT assistant exists only as BF16 safetensors (`google__gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant`).
  - To use it you convert it yourself: `convert_hf_to_gguf.py … --outtype f16` then `llama-quantize … Q4_0` (https://github.com/ggml-org/llama.cpp/pull/24282).
  - ggml-org publishes non-QAT `mtp-gemma-4-26B-A4B-it-{BF16,Q8_0,Q4_0}.gguf` (https://huggingface.co/ggml-org/gemma-4-26B-A4B-it-GGUF).
- Engine flags:
  - vLLM: `--speculative-config '{"method":"mtp","model":"google/gemma-4-26B-A4B-it-assistant","num_speculative_tokens":4}'`. The recipe recommends 4.
    - Gemma4 MTP originally needed nightly (recipe).
    - Open bug #51493: the V1 runner `pre_projection` shape mismatch on 26B; workaround `VLLM_USE_V2_MODEL_RUNNER=1` (https://github.com/vllm-project/vllm/pull/51493).
  - llama.cpp: `--model-draft <assistant.gguf> --spec-type draft-mtp --spec-draft-n-max 4` (PR #23398).
    - Open SYCL bug: gemma4-assistant MTP flash-attention crash with `-sm tensor` on Arc B580 (https://github.com/ggml-org/llama.cpp/issues/29419).
  - HF transformers: `assistant_model=…`, `do_sample=False` in Google's doc (https://ai.google.dev/gemma/docs/mtp/mtp).
- RedHat EAGLE-3 alternative: `RedHatAI/gemma-4-26B-A4B-it-speculator.eagle3`.
  - 0.93B params, 1.86 GB, 1 llama-style layer, `draft_vocab_size` 32000, aux layers [2, 15, 27] (`…eagle3/config.json:8-14`; [sizes]).
  - Card says "preliminary". Its title is mislabelled "gemma4-31B-it" (`…eagle3/README.md:10-12`).
  - Trained on 31B-it responses with no reasoning (`:23`).
  - Flags: `"method":"eagle3","num_speculative_tokens":3` (`:34-43`).
  - Acceptance length at k=3: HumanEval 2.75, math 2.95, qa 1.99, summarization 1.87 (`:100-170`).
  - Use with `/chat/completions` only (`:24`).
  - Not shown to work under thinking: the training data had none.

### 1.2 Qwen3.8-27B (Alibaba Qwen)

**Architecture** (`Q/README.md:32-53`; `Q/config.json`)
- Dense 27B, `Qwen3_5ForConditionalGeneration` (`model_type qwen3_5`).
- 64 layers in a hybrid layout `16 × (3 × (Gated DeltaNet → FFN) → 1 × (Gated Attention → FFN))`: 48 `linear_attention` plus 16 `full_attention` layers, `full_attention_interval` 4.
- Gated DeltaNet: 48 V heads, 16 QK heads, `head_dim` 128, conv kernel 4, `mamba_ssm_dtype: float32`.
- Gated attention: 24 Q heads, 4 KV heads, `head_dim` 256, RoPE dim 64 (`partial_rotary_factor` 0.25), theta 1e7, interleaved mRoPE [11, 11, 10], `attn_output_gate: true`.
- FFN 17,408. Vocab 248,320, untied.
- MTP: `mtp_num_hidden_layers: 1`, "trained with multiple steps".
- Vision: 27-layer ViT, hidden 1152.
- Context: 262,144 native, extensible to 1M with YaRN (`Q/README.md:53`, `:514-558`).
- Parameters: 27,781,427,952 total BF16, 55.6 GB (`Q/api`; [sizes]).
- No n-gram embedding.
- License: Apache-2.0.
- Token ids: `bos_token: null`, `add_bos_token: false`, EOS `[248046 <|im_end|>, 248044 <|endoftext|>]` (`Q/tokenizer_config.json`; `Q/generation_config.json`).
- `RedHatAI/Qwen3.8-27B` is a byte-identical mirror (README, config, template and tokenizer md5 match).

**Sampling** (`Q/README.md:250-256`, `:500-505`)
- Thinking mode: `temperature=1.0, top_p=0.95, top_k=20, min_p=0.0, presence_penalty=0.0, repetition_penalty=1.0`.
- Non-thinking mode: `temperature=0.7, top_p=0.80, top_k=20, min_p=0.0, presence_penalty=1.5, repetition_penalty=1.0`.
- `presence_penalty` can range 0–2 against repetition, with possible language mixing at high values.
- `generation_config.json` encodes only the thinking defaults (`temperature 1.0, top_k 20, top_p 0.95`).
- Output budget: reasoning up to 262,144 tokens, final answer 131,072 (`Q/README.md:507-512`).

**Thinking control**
- Thinking is **on by default**. `preserve_thinking` is **on by default**: all historical `reasoning_content` is re-rendered (`Q/README.md:28`, `:246`, `:264`, `:472`).
- Disable thinking per request with `extra_body={"chat_template_kwargs":{"enable_thinking":false}}` (`Q/README.md:457-460`).
- `reasoning_effort` ∈ {`xhigh` (default), `medium`, `low`} (`Q/README.md:258-262`).
  - The template **injects a system message** "Reasoning effort is set to xhigh. Please think carefully…" whenever thinking is on and effort is xhigh (default) or low. Medium injects nothing (`Q/chat_template.jinja:45-56`, `:76-86`).
  - So the default prompt is not the bare user prompt. Any other value raises.
- The generation prompt pre-fills `<|im_start|>assistant\n<think>\n` when thinking is on.
  - The model's output therefore starts inside the think block with no opening tag, and the reasoning parser must handle that.
  - With thinking off, the pre-fill is `<think>\n\n</think>\n\n` (`Q/chat_template.jinja:163-170`).
- With `preserve_thinking` on, every assistant history turn gets `<think>\n…\n</think>\n\n`, even when the reasoning is empty (`Q/chat_template.jinja:116-120`).
- System messages may not contain images or videos, and a system message must come first. Otherwise the template raises (`:9-10`, `:105-106`).

**Tool-call format**
- Qwen3-Coder-style XML: `<tool_call>\n<function=NAME>\n<parameter=ARG>\nvalue\n</parameter>\n</function>\n</tool_call>`.
- Tools are listed as JSON lines inside `<tools>` (`Q/chat_template.jinja:57-68`, `:121-145`).
- Tool results go back as `<tool_response>` inside a user turn (`:147-158`).
- Parsers:
  - vLLM: RedHat uses `--tool-call-parser qwen3_xml --reasoning-parser qwen3 --enable-auto-tool-choice` (`RedHatAI__Qwen3.8-27B-INT4/README.md:46-60`). See §1.4 for upstream recipe notes.
  - llama.cpp: `--jinja`.

**MTP and speculative story**
- Built-in MTP head:
  - In BF16/FP8 safetensors.
  - OV IR: `openvino_mtp_model.xml/.bin`, 0.26 GB INT4 / 0.42 GB INT8 ([sizes]).
  - RedHat INT4: `model_mtp.safetensors`, 0.85 GB, left unquantized via `ignore: re:^mtp.*` (`RedHatAI__Qwen3.8-27B-INT4/config.json` quantization_config).
  - Intel Q4_K_M GGUF: `qwen35.nextn_predict_layers=1`, block_count 65, `blk.64.nextn.*` [probe:gguf-header].
- vLLM MTP: `--speculative-config '{"method":"mtp","num_speculative_tokens":3}'` (`RedHatAI__Qwen3.8-27B-INT4/README.md:50`).
- OpenVINO GenAI MTP:
  - `draft = ov_genai.draft_model(model_path, device)`, `VLMPipeline(..., draft_model=draft, scheduler_config)`.
  - Requires `enable_prefix_caching=False`, `do_sample=False`, `num_assistant_tokens=2`.
  - Card: "currently supports this MTP path with greedy decoding and prefix caching disabled" (`OpenVINO__Qwen3.8-27B-int4-ov/README.md:115-139`).
- DSpark drafter: `RedHatAI/Qwen3.8-27B-speculator.dspark`.
  - 1.99B params, 3.98 GB BF16, 5-layer Qwen3 backbone with sliding window 2048.
  - Reads aux hidden states from layers 4, 12, 20, 28, 36, 44, 52 and 60. Block 8, full 248,320 vocab, Markov head rank 256, confidence head.
  - Trained on an H200 against **BF16** Qwen3.8-27B with vLLM 0.29.0. Checkpoint is about 75% of one epoch (`…dspark/README.md:15-41`, `:71`, `:91`; `config.json`).
  - Flags: `'{"model":"RedHatAI/Qwen3.8-27B-speculator.dspark","num_speculative_tokens":7,"method":"dspark"}'`. The config's own proposal says 8 tokens (`README.md:98`; `config.json:35`).
  - Acceptance length on a dense BF16 verifier: math 5.74, rag 4.81, HumanEval 4.42, tool_call 3.76, writing 3.77. Long-context MRCR overall 4.49, held to 1M+ (`README.md:112-143`). Sampling temperature is not stated.
  - Card claims DSpark beats MTP at equal 8 tokens, mostly at low and medium concurrency (`:146`).
  - Quantized verifiers are allowed: "Eagle-3, P-EAGLE, DFlash, and DSpark can be paired with any supported verifier model (including quantized variants)" (https://raw.githubusercontent.com/vllm-project/speculators/main/docs/user_guide/algorithms/decision_guide.md). RedHat recommends it with their INT4 (`RedHatAI__Qwen3.8-27B-INT4/README.md:53-60`).
  - DSpark first shipped in vLLM v0.25.0 (https://api.github.com/repos/vllm-project/vllm/releases).

### 1.3 Qwen3.8-Flash-Next (Alibaba Qwen, experimental "Qwen4 architecture preview")

**Architecture** (`F/README.md:28-71`; `F/config.json`)
- `Qwen4ExpForConditionalGeneration` (`qwen4_exp`).
- Size: "125B with 6B activated, plus 51B n-gram embedding and 4B MTP". Total 179,999,981,459 params, 360 GB BF16 (`F/api`; [sizes]).
- Hidden 2560, 48 layers: `12 × (3 × (Gated DeltaNet → MoE) → 1 × (Qwen Sparse Attention → MoE))`.
- MoE: 512 experts, 10 routed plus 1 shared, expert FFN 640.
- QSA: 24 Q heads, 2 KV heads, `head_dim` 256. Indexer is MQA with 4 query heads and 1 key head, dim 128. Budget is 512 blocks or 2048 tokens (`indexer_budget 2048`, `indexer_compress_ratio 4`).
- Gated Residual / hyper-connections: 4 branches, rank 320 (`hc_count 4`, `hc_lowrank 320`).
- **N-gram embedding**: 20,000,000 entries of bigrams and trigrams, injected at layer 2 (`ngram_size 3`, `ngram_vocab_size_base 20000000`, `ple_layer_ids [2]`, `split_ngram_parts 128`, `heads_per_ngram 8`).
  - Described as "more amenable to offloading than MoE" (`F/README.md:34`).
- MTP: 1 hybrid layer (`mtp.hybrid true`).
- Context: 262,144 native, 1M with YaRN.
- Vision tower: the same 27-layer ViT.
- **License: `qwen-community-1.0` ("other")**, not Apache (`F/README.md:3-5`). Read `LICENSE` before use.
- Sampling, thinking, `reasoning_effort` and `preserve_thinking` are the same as 27B (`F/README.md` usage section; diff against Q shows only name changes). The chat template is byte-identical to 27B's.
- Serving engines named: "SGLang, KTransformers or vLLM are strongly recommended" (`F/README.md:324`).
- **Memory fact that decides the hardware** ([sizes]; index of `Intel__Qwen3.8-Flash-Next-W4A16-AutoRound` [probe:index]):
  - The n-gram table alone is 51.2B params, which is 102.4 GB in BF16. It is a single shard, `model-00016-of-00017.safetensors` (102.4 GB) in the AutoRound repo.
  - FP8 repo: the n-gram table is converted to FP8 (`modules_to_convert: ["ple.ple_embedding.ngram_embedding"]`) for a total of 185.5 GB.
  - AutoRound W4A16 keeps it BF16, for 181.2 GB total.
  - **No published variant fits 4 × 32 GB = 128 GB of B70 VRAM.** The n-gram table must go to host RAM, or the model has to be split.

---

## 2. All 24 variants

Notes for every table in this section:
- Size is the weight files at the pinned revision ([sizes]).
- "Engines/devices" lists only what the card itself says.
- Accuracy is the card's own number. "none" means the card publishes none.

### 2a. Gemma-4-26B-A4B (12 repos)

**google/gemma-4-26B-A4B @24548b6** (pretrained base)
- Format: BF16 safetensors, 51.6 GB. QAT: no.
- Engines/devices: transformers.
- Accuracy: none in card.
- Caveats:
  - Not instruction-tuned.
  - `eos=1` only.
  - The README is the -it card verbatim (`google__gemma-4-26B-A4B/README.md`).

**google/gemma-4-26B-A4B-it @4d7ae49**
- Format: BF16 safetensors, 51.6 GB. QAT: no.
- Engines/devices: transformers. vLLM, llama.cpp and others are covered by their own docs.
- Accuracy: MMLU-Pro 82.6, AIME26 88.3, LCB v6 77.1, GPQA-D 82.3, Tau2 68.2, MRCR-128k 44.1 (`G/README.md:88-109`).
- Caveats: reference checkpoint.

**google/gemma-4-26B-A4B-it-assistant @6e5aaaf**
- Format: BF16, 0.84 GB, MTP drafter (`Gemma4AssistantForCausalLM`). QAT: no.
- Engines/devices: transformers (`assistant_model=`). vLLM `mtp` and llama.cpp `draft-mtp` per their docs.
- Accuracy: "up to 3x", lossless (card `:23`).
- Caveats:
  - Pair only with a non-QAT target.
  - `pipeline_tag any-to-any`.

**google/gemma-4-26B-A4B-it-qat-q4_0-gguf @d1c082b**
- Format: GGUF `file_type=2` (MOSTLY_Q4_0), 14.44 GB, plus `mmproj` 1.19 GB.
  - Tensor types: all attention, shared FFN and experts (`ffn_gate_up_exps`, `ffn_down_exps`) are Q4_0. `token_embd` is **Q6_K**. Router and norms are F32 [probe:gguf-header].
- QAT: **yes**.
- Engines/devices: llama.cpp, Ollama, LM Studio and others (QAT blog).
- Accuracy: no QAT-vs-BF16 numbers in the card. The blog claims "similar quality to bfloat16".
- Caveats:
  - Not a bit-exact image of the QAT weights: rel-RMS 5% on attention and ~8% on experts. See §3.
  - No assistant GGUF in the repo.
  - `add_bos_token=True`.

**google/gemma-4-26B-A4B-it-qat-q4_0-unquantized @f1e06dc**
- Format: BF16 safetensors, 51.6 GB, weights already lying on the Q4_0 grid [probe:grid].
- QAT: **yes**. Card calls it "ideal for custom downstream compilation".
- Engines/devices: any, after you quantize it yourself.
- Accuracy: none.
- Caveats:
  - Running it in BF16 wastes memory.
  - Convert with a grid-aware quantizer (§3).

**google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant @9537141**
- Format: BF16, 0.84 GB.
- QAT: **yes**. This is the QAT drafter.
- Engines/devices: same as the assistant.
- Accuracy: none.
- Caveats:
  - Required with QAT targets (card `:32`).
  - `generation_config.json` lacks `num_assistant_tokens`.
  - The card body is the generic non-assistant card.

**OpenVINO/gemma-4-26b-a4b-it-int4-ov @4e5885c**
- Format: OV IR, 15.3 GB (LM 13.96 GB).
  - Card: `INT4_ASYM`, group 128, ratio 1.0 (`README.md:25-29`).
  - **`openvino_config.json` disagrees:** `group_size 64`, `quant_method "awq"` with `dataset null` (data-free AWQ), `sym false`, `ignored_scope .*router.*`.
- QAT: no. Built from the BF16 -it model.
- Engines/devices:
  - OpenVINO ≥ 2026.4.0, Optimum-Intel ≥ 2.2.0, `transformers==5.5.4` (`:35-47`).
  - Usage is `OVModelForVisualCausalLM` on CPU.
- Accuracy: none.
- Caveats:
  - **No MTP or assistant IR in the repo**.
  - OVMS 2026.3: "Gemma4 … supported only on CPU. GPU execution might be not reliable yet". OVMS 2026.4.0: "Improved accuracy for Gemma-4-26B-a4B on GPU" (https://github.com/openvinotoolkit/model_server/releases).

**Intel/gemma-4-26B-A4B-it-int4-AutoRound @edff627**
- Format: AutoRound W4A16, 15.4 GB.
  - Settings: `bits 4`, `group_size 128`, `sym true`, packing `auto_round:auto_gptq`, `autoround_version 0.13.0`. Scope is `model.language_model.layers` only, so vision is kept 16-bit (`quantization_config.json`).
- QAT: no. Default tuning, 200 iters.
- Engines/devices: transformers. AutoRound backends `ark` (CPU/XPU), `triton` (XPU/CUDA) and `torch` per AutoRound docs. vLLM XPU since v0.20 / MoE v0.27.
- Accuracy: none (one sample output).
- Caveats:
  - **Ships the old launch template**: override it.
  - Needs auto-round PR #1656.
  - No license field in the card.
  - The card states only "int4 … group_size 128 and symmetric" (`README.md:10`).

**Intel/gemma-4-26B-A4B-it-int4-mixed-AutoRound @81939a2**
- Format: AutoRound, 16.2 GB.
  - MoE experts are int4 (group 128, sym).
  - **Attention, shared/dense MLP and router are int8**. Everything else is 16-bit (`README.md:14`, `extra_config`).
- QAT: no. **Pure RTN** (`--iters 0 --disable_opt_rtn`, `:16`, `:68-82`).
- Engines/devices: same as above.
- Accuracy: none.
- Caveats: old template. Apache-2.0.

**RedHatAI/gemma-4-26B-A4B-it @34fc892**
- Format: BF16 mirror, 51.6 GB. QAT: no.
- Engines/devices: vLLM 0.24.0, RHOAI/RHAIIS 3.5 (`README.md:8-27`).
- Accuracy: same as google.
- Caveats:
  - `required_cli_args` add `--enable-prefix-caching`.
  - Default thinking on.
  - The card omits 12B and audio text.

**RedHatAI/gemma-4-26B-A4B-it-FP8-dynamic @ed35d7a**
- Format: compressed-tensors FP8, 28.6 GB.
  - Weights FP8 E4M3 per-channel static; activations FP8 per-token dynamic.
  - Made by `model_free_ptq` with `scheme FP8_DYNAMIC`. Ignores `lm_head`, embeddings, router and vision tower (`README.md:44-58`, `:116-128`; `recipe.yaml`).
- QAT: no.
- Engines/devices: vLLM 0.24.0, with TP=2 for evals.
- Accuracy, no-think recovery: IFEval 99.3–99.4%, GSM8K-Plat 99.9%, MMLU-Pro 99.7%, MATH-500 101.3%, AIME25 100%, GPQA 102.1%, LCB 99.2% (`:138-203`).
- Accuracy, think recovery: 97.6–103.3%, BFCLv4 overall 101.0% (`:205-295`).
- Caveats:
  - The ignore list covers only `Linear`. Whether the fused 3-D MoE experts are FP8 is not stated.
  - Safetensors: 24.48B params F8_E4M3 vs 2.06B BF16 (`api`). That matches experts being FP8.
  - FP8 compute needs FP8 kernels: on XPU, W8A8 FP8 since v0.25 and W8A16 FP8 (releases API).

**RedHatAI/gemma-4-26B-A4B-it-speculator.eagle3 @226b269**
- Format: BF16 EAGLE-3 drafter, 1.86 GB.
- QAT: no.
- Engines/devices: vLLM main, `method eagle3`.
- Accuracy: acceptance length @k=3 is 1.87–2.95 (`README.md:100-170`).
- Caveats:
  - "Preliminary".
  - Trained on 31B outputs without reasoning.
  - Needs `custom_code` (`config.py`).

### 2b. Qwen3.8-27B (9 repos)

**Qwen/Qwen3.8-27B @1d4bf0f**
- Format: BF16, 55.6 GB. QAT: no.
- Engines/devices: transformers, vLLM, SGLang, TokenSpeed (`README.md:9-12`, `:236-240`).
- Accuracy:
  - SWE-bench Pro 61.7, TB2.1 73.0, GPQA-D 89.2, LCB v6 90.3, IFBench 79.5 (`Q/README.md:74-172`).
  - OSWorld-Verified 84.3 (`:195`).
- Caveats: thinking on by default, plus the injected effort prompt.

**Qwen/Qwen3.8-27B-FP8 @017b9c7**
- Format: FP8 E4M3 fine-grained **block 128×128**, dynamic activations, 30.9 GB.
  - Not converted: vision, norms, gates, `linear_attn.{A_log,conv1d,dt_bias,in_proj_a,in_proj_b,in_proj_ba,norm}`, `lm_head`, embeddings (`config.json` quantization_config, 882 entries).
- QAT: no.
- Engines/devices: same as BF16.
- Accuracy: "nearly identical" (`README.md:15-16`); no numbers.
- Caveats: needs FP8 block-quant kernels (vLLM XPU "FP8 … block-scaled" listed in the kernel map).

**OpenVINO/Qwen3.8-27B-int4-ov @7b20ea8**
- Format: OV IR **INT4_ASYM group 128 ratio 1.0** (card), 15.9 GB.
  - Files: LM 13.93 GB, **MTP 0.26 GB**, text-embeddings 1.27 GB, vision.
  - `openvino_config`: `quant_method default`, `sym false`, `group_size null`, MTP compressed the same way.
- QAT: no.
- Engines/devices:
  - OpenVINO and GenAI ≥ 2026.4.0, `transformers==5.2.0`, "latest Optimum Intel dev" (`README.md:35-41`).
  - Optimum-Intel `OVModelForVisualCausalLM`, OV GenAI `VLMPipeline` with MTP, OVMS (Docker `openvino/model_server:weekly`) (`:42-217`).
- Accuracy: none.
- Caveats:
  - **`tokenizer_config.model_max_length=131072`** while every other repo says 262144.
  - MTP is greedy-only with prefix caching off.
  - Notebook export used `--group-size-fallback adjust`.

**OpenVINO/Qwen3.8-27B-int8-ov @c129bf9**
- Format: OV IR **INT8_ASYM**, per-channel (`group_size -1`), 27.8 GB. MTP 0.42 GB.
- QAT: no.
- Engines/devices: same as int4-ov.
- Accuracy: none.
- Caveats: built with optimum 2.3.0. The `openvino_config` does not list an `mtp_model` entry.

**Intel/qwen3.8-27B-int4-ov @53292ee**
- Format: **no weights**. A use-case wrapper (README, `export_and_quantize.sh`, MIT) that downloads `OpenVINO/Qwen3.8-27B-int4-ov`.
- QAT: n/a.
- Engines/devices: OV GenAI `VLMPipeline` and DLStreamer `gvagenai`, CPU/GPU, no NPU (`README.md:25-33`, `:157-161`, `:315-320`).
- Accuracy: none.
- Caveats: says nightly OV/GenAI plus `transformers==5.2` were needed when written (`:58-64`). Not a separate variant to benchmark.

**Intel/Qwen3.8-27B-q4km-AutoRound @4b30b30**
- Format: GGUF **Q4_K_M** (`file_type 15`), 16.81 GB, plus `mmproj` 1.84 GB.
  - Tensor types: Q4_K for most; Q6_K for half of `ffn_down`/`attn_qkv`/`attn_v` and the output; `token_embd` Q4_K.
  - Includes the MTP `nextn` block. `ssm_alpha`/`ssm_beta` are Q4_K [probe:gguf-header].
- QAT: no. `auto_round_best … --enable_alg_ext` (`README.md:68`).
- Engines/devices: llama.cpp `llama-cli -m … -mm mmproj` (`:17-20`).
- Accuracy: none.
- Caveats:
  - No license field.
  - GGUF `bos_token=<|endoftext|>` but `add_bos_token=False`.
  - Thinking shows as `[Start thinking]` in llama-cli.

**RedHatAI/Qwen3.8-27B @019ada8**
- Format: BF16 byte-identical mirror, 55.6 GB. QAT: no.
- Engines/devices: vLLM.
- Accuracy: same as Qwen.
- Caveats: none beyond Qwen's.

**RedHatAI/Qwen3.8-27B-INT4 @91bd022**
- Format: compressed-tensors **W4A16**, 19.45 GB.
  - Weights: int4 **sym group 128**, `actorder static`, GPTQ after AWQ (`duo_scaling both`), 512 open-perfectblend samples at 4096 tokens.
  - **Plus FP8 KV cache scheme** (static per-tensor, `kv_cache_scheme` in config).
  - Kept BF16: `lm_head`, embeddings, vision, `in_proj_a`/`in_proj_b`, and MTP (separate `model_mtp.safetensors`, 0.85 GB) (`README.md:63-169`; `recipe.yaml`; `config.json`).
- QAT: no.
- Engines/devices: vLLM; `--reasoning-parser qwen3 --tool-call-parser qwen3_xml --enable-auto-tool-choice` with MTP 3 or DSpark 7 (`:44-61`).
- Accuracy, recovery vs BF16: IFEval 99.67%, MMLU-Pro 98.81%, GSM8K-Plat 101.07%, MATH-500 99.52%, GPQA-D 98.49%, AIME25 98.69% (`:179-228`).
- Caveats:
  - vLLM auto-switches the KV cache to fp8 when it sees `kv_cache_scheme` (`vllm/utils/torch_utils.py` L448-521). On XPU the FP8 KV cache exists since v0.20. Pass `--kv-cache-dtype auto/bf16` deliberately if comparing.

**RedHatAI/Qwen3.8-27B-speculator.dspark @87ca2fd**
- Format: BF16 DSpark drafter, 3.98 GB. QAT: no.
- Engines/devices: vLLM, `method dspark` (≥ v0.25.0), validated on H100 with vLLM 0.29.0.
- Accuracy: acceptance length 3.76–5.74, long-context 4.49 (`README.md:112-143`).
- Caveats:
  - About 75% of one epoch trained.
  - Needs `custom_code`.
  - The card also suggests `RedHatAI/Qwen3.8-27B-NVFP4` (**NVIDIA-only**, no XPU kernel).

### 2c. Qwen3.8-Flash-Next (3 repos)

**Qwen/Qwen3.8-Flash-Next @de4b8e4**
- Format: BF16, 360 GB. QAT: no.
- Engines/devices: SGLang, KTransformers, vLLM, TokenSpeed (`F/README.md:319-331`).
- Accuracy:
  - SWE-bench Pro 62.5, DeepSWE 58.7, SWE-Multilingual 81.0, GPQA-D 91.7, LCB v6 91.9, IFBench 81.3, Toolathlon 73.5.
  - Sources: `F/README.md` Language table; values extracted from rows 99-225 with tags stripped.
- Caveats: license `qwen-community-1.0`.

**Qwen/Qwen3.8-Flash-Next-FP8 @236dfdf**
- Format: FP8 block 128×128, 185.5 GB, n-gram table FP8.
  - Not converted: attention (`self_attn.*`, indexer), all `linear_attn` projections, shared experts, hyper-connections, router (943 entries).
  - In effect only routed experts and the n-gram table are FP8.
- QAT: no.
- Engines/devices: same as BF16.
- Accuracy: "nearly identical" (card `:17-18`).
- Caveats: does not fit 128 GB VRAM.

**Intel/Qwen3.8-Flash-Next-W4A16-AutoRound @4c67bf6**
- Format: AutoRound W4A16, int4 sym group 128 (`auto_round:auto_gptq` packing), 181.2 GB.
  - Only routed experts are int4.
  - Kept 16-bit: attention, linear_attn, shared experts, gate, hyper-connections, PLE/n-gram (**102.4 GB BF16**), indexer, MTP (5.21 GB in `model_extra_tensors.safetensors`) (`README.md:24-33`; `config.json`).
- QAT: no. `--iters 200`.
- Engines/devices: transformers. AutoRound backends per §2a.
- Accuracy: GSM8K 0.9682 vs 0.9673, MMLU 0.8560 vs 0.8651, PIQA 0.8215, HellaSwag 0.6873; average 99.64% of BF16 (`README.md:14-17`).
- Caveats:
  - Biggest file is a 102.4 GB single shard.
  - `config.json.autoround.bak` is left in the repo.
  - Shard index skips `00002`, which is harmless (index has no references to it).

### 1.4 Engine flag crib, per family

#### vLLM

**Gemma-4-26B-A4B**
- Serve flags: `--enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4 --chat-template examples/tool_chat_template_gemma4.jinja`.
- Version: ≥ 0.25.0 (https://recipes.vllm.ai/Google/gemma-4-26B-A4B-it).
- Text-only: `--limit-mm-per-prompt '{"image":0,"audio":0}'`.
- MTP: `--speculative-config '{"method":"mtp","model":"google/gemma-4-26B-A4B-it-assistant","num_speculative_tokens":4}'`.
- Intel XPU recipe, validated on "4x Intel Arc Pro B60 / B70":
  - Image `vllm/vllm-openai-xpu:latest`.
  - Flags `--enforce-eager --attention-backend flash_attn --trust-remote-code --no-enable-prefix-caching --max-num-batched-tokens 8192 --max-model-len 5120 --tensor-parallel-size 4` (same URL).
- Google provides no w4a16 checkpoint for 26B, because expert dim 704 → "excessive quality loss". The guide suggests `--quantization int8_per_channel_weight_only` instead (https://github.com/vllm-project/recipes/blob/main/Google/Gemma4.md).

**Qwen3.8-27B**
- Serve flags: `--enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 --mm-encoder-tp-mode data` (https://recipes.vllm.ai/Qwen/Qwen3.8-27B).
  - `qwen3_xml`, the name RedHat uses, is the same class on vLLM main (`vllm/tool_parsers/__init__.py` @612e928).
- MTP: `'{"method":"mtp","num_speculative_tokens":3}'`. Give N explicitly or startup fails.
  - Recipe acceptance: 92.2% BF16, 84.8% FP8 (https://github.com/vllm-project/recipes/blob/main/models/Qwen/Qwen3.8-27B.yaml).
- Text-only: `--language-model-only`. It is mutually exclusive with `--mm-encoder-tp-mode data`.
- XPU: "Validated on 4x Intel Arc Pro B60 / B70 … `--enforce-eager` is required". This covers the BF16 variant only. MTP on XPU is not covered by the recipe.
- Prefix caching on GDN models: `--enable-prefix-caching` drives mamba "align" caching. `--mamba-cache-mode` is deprecated (`vllm/engine/arg_utils.py`).

**Qwen3.8-Flash-Next**
- Serve flags: `--tensor-parallel-size 4 --enable-expert-parallel --enable-prefix-caching --kv-cache-dtype fp8 --attention-config.indexer_kv_dtype fp8 --speculative-config '{"method":"mtp","num_speculative_tokens":3}' --tool-call-parser qwen3_coder --reasoning-parser qwen3`.
- Version: ≥ 0.30.0.
- N-gram offload: `--engram-config '{"cpu_offload":true}'` is the default. "offload currently only runs on Nvidia devices".
- **"XPU/TPU startup error"**. vLLM source: `NotImplementedError("Qwen4Exp currently supports CUDA and ROCm only")` (https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next; `vllm/models/qwen4_exp/__init__.py` @612e928).

**vLLM `reasoning_effort` passthrough**
- The top-level `reasoning_effort` accepts `none|minimal|low|medium|high|xhigh|max` and is copied into the template kwargs (`vllm/entrypoints/openai/chat_completion/protocol.py`).
- The Qwen template only allows `xhigh|medium|low` and raises on anything else (`Q/chat_template.jinja:48-50`).

**Intel llm-scaler (vLLM fork for Arc Pro B60/B70)**
- Gemma 4 26B-A4B: online `fp8` and `sym_int4` (since 0.21.0-b1). MTP method names `gemma4_mtp` and `qwen3_5_mtp`.
- llm-scaler-vllm 1.5 (vLLM 0.26.0) adds for Qwen3.8-27B:
  - block-FP8 checkpoints
  - MTP and DFlash
  - sym INT4
  - FP8 KV
  - fixed "prefix caching with MTP"
- No Flash-Next support.
- Known issue: P2P is "limited 33 GB/s" on x16 without a switch (https://github.com/intel/llm-scaler, `vllm/README.md`, `KNOWN_ISSUES.md`, releases).

#### OpenVINO GenAI / OVMS 2026.4.x

**Parsers**
- Tool: `gemma4` for Gemma; `qwen3coder` for Qwen3.8.
- Reasoning: `gemma4` for Gemma; `qwen3` for Qwen3.8.
- Both are auto-detected from the template. `<|tool_call>call:` selects gemma4; `<function=` + `<parameter=` (+ `<think>`) selects qwen3coder + qwen3 (https://github.com/openvinotoolkit/model_server/blob/main/docs/llm/reference.md; `src/llm/io_processing/chat_template/analyzer.cpp`).

**MTP**
- Auto-detected when `openvino_mtp_model.xml` sits in the draft dir.
- Command: `--source_model OpenVINO/Qwen3.8-27B-int4-ov --draft_model_path . --enable_prefix_caching false`. "Prefix caching is not currently supported in MTP mode". `max_tokens` is required.
- Per-request `num_assistant_tokens`, default 5 (https://github.com/openvinotoolkit/model_server/tree/main/demos/continuous_batching/speculative_decoding).
- Notebook results (one unnamed GPU, INT4):
  - n=2 gives 1.78× at 68% acceptance. n=3 gives 1.77×; n=4 gives 1.13×.
  - Greedy only, 1 sequence, no confidence threshold.
  - Outputs are "not guaranteed to be bit-exact" (https://github.com/openvinotoolkit/openvino_notebooks/blob/latest/notebooks/qwen3.8-mtp/qwen3.8-mtp.ipynb).

**Devices and multi-GPU**
- `--target_device GPU.<i>|CPU|HETERO:…`.
- `--model_distribution_policy TENSOR_PARALLEL|PIPELINE_PARALLEL`. HETERO supports only PIPELINE_PARALLEL.
- Multi-GPU tensor parallel on Arc is **not documented** (https://github.com/openvinotoolkit/model_server/blob/main/docs/parameters.md).

**KV cache and prefix caching**
- KV precision `u8|u4|bf16|f16` via `plugin_config` `KV_CACHE_PRECISION`. INT4 KV on GPU since 2026.2.
- `--enable_prefix_caching`: the CLI defaults to true but the graph default is false.
- `--cache_interval_multiplier`: default 8; 64 recommended above ~20k prompts for linear-attention models (OVMS v2026.2.1 notes).

**Model support history**
- OV 2026.2: "Only on GPUs: Gemma 4 31B and Gemma 4 26B-A4B".
- OVMS 2026.3 known issue: "Gemma4 … supported only on CPU. GPU execution might be not reliable yet".
- OVMS 2026.4.0: "Improved accuracy for Gemma-4-26B-a4B on GPU".
- 2026.4: Qwen3.8 27B on CPU and GPU, plus MTP for Gemma 4 / Qwen3.5 / Qwen3.6 (https://github.com/openvinotoolkit/openvino/releases; https://github.com/openvinotoolkit/model_server/releases).
- Flash-Next (qwen4_exp) is **not listed** in any OV release.

#### llama.cpp

**Common flags**
- `--jinja`; Qwen3-Coder XML and `peg-gemma4` parsers are auto-selected.
- `--reasoning-format none|deepseek|deepseek-legacy` and `-rea on|off|auto` (`common/arg.cpp`, `common/chat.cpp` @1242be6).
- MTP: `--spec-type draft-mtp --spec-draft-n-max N`.
  - Qwen3.x uses the in-GGUF `nextn` head: #22673, ~75% acceptance, ">2×" on Qwen3.6.
  - Gemma uses `--model-draft <assistant.gguf>`: #23398.
  - Flash-Next: qwen4exp MTP #29761, 1.55×.

**Architectures**
- `qwen35` and `qwen4exp` are present.
- qwen4exp (#27742) handles the "97.7 GiB n-gram hash table" with `ggml_get_rows`, so the table can live in host RAM.

**SYCL / Intel GPU**
- `GGML_OP_GATED_DELTA_NET` is supported on SYCL and Vulkan.
- Gemma4 26B on Arc Pro B70 flash-attention dispatch fix: #28450.
- Open: gemma4-assistant MTP crash with SYCL `-sm tensor` (#29419); Vulkan gemma4 decode −5% on B580 (#30018).

---

## 3. QAT deep dive (Gemma-4-26B-A4B only)

Gemma is the only family here with QAT. Qwen and Intel do not publish QAT, and every Qwen 4-bit variant is post-training quantization (PTQ).

### 3.1 What Google says

**Formats**
- "QAT checkpoints for the popular Q4_0 quantization format as well as a novel quantization format specialized for mobile".
- "GGUF formats are ready for use with llama.cpp, and compressed tensors are provided for vLLM. For everything else, we share unquantized checkpoints that can be converted and quantized into formats supporting Q4_0." (https://blog.google/innovation-and-ai/technology/developers-tools/quantization-aware-training-gemma-4/)
- Card definition: "Unquantized QAT checkpoints (Q4_0): Half-precision weights extracted from the QAT pipeline" (`google__gemma-4-26B-A4B-it-qat-q4_0-gguf/README.md:28`).
- Compressed-tensors w4a16 exists for E2B/E4B/12B/31B and **not 26B A4B** (`:31`).
  - Google's 31B and 12B ct checkpoints are `int4 sym group 32, observer memoryless_minmax, pack-quantized`, with `lm_head` and vision ignored (https://huggingface.co/google/gemma-4-31B-it-qat-w4a16-ct/raw/main/config.json).
- Tech report: "Q4_0 quantization: blockwise quantization, often referred to as Q4_0". "to enable stable inference in fp16, we introduce a scalar scale at each block in order to bound the activation ranges". The image encoder QAT is W8A8 (https://arxiv.org/pdf/2607.02770).
- Tech report Table 3 footprint at 32k context with int8 KV: 26B-A4B bf16 "52.0 / 7.6" versus Q4_0 "16.2 / 2.8" (same).
- Google publishes **no QAT-vs-BF16 accuracy numbers** for 26B. Quality claims are qualitative only ("similar quality to bfloat16", card `:25`).
- Q4_0 itself, per the llama.cpp definition, is symmetric int4 in blocks of 32 along the input dim. Each block has an fp16 scale `d = max/-8`, where max is the signed largest-magnitude element, and q ∈ [-8, 7].
  - Google does not restate this. The measurements below confirm both the block size and the symmetry.

### 3.2 What the files actually contain [probe:grid]

Method: I pulled 64 rows of each tensor by HTTP range from the pinned safetensors and GGUF. Probe scripts are in `probes/`: `qatprobe3.py` (grid), `ggufblk.py` (GGUF vs QAT), `rtn.py` (re-quant error), `ggufhdr.py` (GGUF header).

**QAT BF16 checkpoint** (`-qat-q4_0-unquantized`)
- Every 32-element block along the input dim holds **≤16 distinct values**: mean 12.6 for `q_proj`, ~10.7 for experts.
- Those values are **integer multiples of one step d, including 0**: symmetric, no zero-point. This held for 99.8–100% of blocks.
- Tensors checked:
  - `self_attn.{q,o}_proj`
  - `mlp.{up,down}_proj` (shared/dense FFN)
  - `experts.{gate_up,down}_proj`
  - `router.proj`
  - `embed_tokens` (span ≤15 steps)
- The vision tower is **not** on a 4-bit grid (32 distinct values per block). That matches the W8A8 vision QAT.
- Baseline: the non-QAT BF16 -it checkpoint has 32 distinct values per block and no grid.

**Google's Q4_0 GGUF is a plain llama.cpp re-quantization of that checkpoint**
- Every GGUF block scale equals `absmax/8` with the sign of the extreme (100% of 5632 blocks of `blk.3.attn_q`). It is not the QAT grid step.
- Where the extreme value is positive (+7·d; 32% of attention blocks, ~50% of expert blocks), llama.cpp's `max/-8` rule changes the step by 7/8, so every value is re-rounded.
- Measured rel-RMS error of Q4_0 vs the QAT weights:
  - `q_proj` / `o_proj`: 5.0%
  - `experts.gate_up` / `experts.down`: **7.9–8.0%**
  - For comparison, Q4_0 of the **non-QAT** BF16 model: 9.0% (`q_proj`) and 8.75% (experts).
- So the shipped GGUF keeps about half the QAT advantage on attention and almost none on the routed experts.
  - Expert blocks use only ~11–13 of the 16 levels, which is typically `k ∈ [-4..7]`. That is exactly the case `max/-8` handles worst.
- Re-quantizing with **scale = the QAT grid step** (sym int4, group 32, fp16 scale) reproduces the weights to 0.7% (attention) and **0.18% (experts)** rel-RMS. The residual is fp16 scale rounding.
- Other schemes on the QAT checkpoint (rel-RMS, `q_proj` / experts):

  | Scheme | q_proj | experts |
  |---|---|---|
  | INT4_ASYM g32 | 5.8% | 8.3% |
  | INT4_ASYM g64 | 7.1% | 8.2% |
  | INT4_ASYM g128 | 8.5% | 8.1% |
  | INT4_SYM absmax/8 g32 | 6.0% | 8.7% |
  | INT4_SYM absmax/8 g128 | 9.0% | 8.5% |

  Any group > 32, or any asymmetric zero-point, breaks the grid.
- `token_embd` is on the grid in the checkpoint but stored as **Q6_K** in Google's GGUF. That is a different grid, so it is re-rounded, though at 6 bits.
- The router is kept F32 in the GGUF, which is lossless.

These are weight-space errors, not task accuracy. Whether the 5–8% vs 0.2–0.7% gap moves benchmarks is an **open measurement**.

### 3.3 What is lost converting to other schemes

| Target | QAT effect |
|---|---|
| llama.cpp Q4_0 via stock `llama-quantize` (what Google shipped) | partial: attention keeps ~half; experts keep ~none (§3.2) |
| llama.cpp Q4_K / Q4_K_M / IQ4 | lost: super-block scales and mins (asym) do not match the grid |
| OV `INT4_ASYM` group 128 (the official OV repo is built from **non-QAT** BF16; its config says AWQ group 64) | no QAT at all |
| OV `INT4_SYM` group 32 via `optimum-cli` from the QAT checkpoint | partial, depending on NNCF's scale rule. Lossless only if the scale equals the grid step. No doc says it reproduces the grid (UNVERIFIED) |
| compressed-tensors W4A16 group 128 (llm-compressor default `W4A16`: int4 sym g128) | lost: group 128 spans 4 QAT blocks with different steps |
| compressed-tensors int4 sym **group 32** (Google's own ct recipe for other sizes, `memoryless_minmax` observer) | partial: minmax gives absmax/(qmax) scales, which hits the same 7/8 issue as llama.cpp unless the scale is overridden |
| AutoRound W4A16 g128 (Intel repos, built from non-QAT BF16) | no QAT |
| FP8 / INT8 of the QAT checkpoint | near-lossless numerically, but no memory win over BF16-derived FP8, so pointless |

### 3.4 Best way to carry QAT into each engine

**Rule:** quantize the `-qat-q4_0-unquantized` weights with sym int4, **group 32 along the input dim**, scale = the block's grid step, no zero-point, no AWQ/GPTQ/scale-search. Do the same for the `-qat-q4_0-unquantized-assistant` drafter, so the target and drafter share a precision (card `:32`).

**llama.cpp**
- Official choice: Google GGUF + mmproj.
- Better, self-built, measure before adopting:
  - Write the QAT checkpoint to GGUF Q4_0 with block scale = grid step: d = ±step, keep q ∈ [-8, 7]. QAT guarantees the fit.
  - Keep `token_embd` Q4_0 on-grid (or Q8_0) instead of Q6_K.
  - Convert the QAT assistant the same way (`convert_hf_to_gguf.py --outtype f16` + quantize; PR #24282 shows the stock path).
- Block size is identical to Q4_0, so kernels and speed are unchanged.

**OpenVINO**
- Export the QAT checkpoint with `optimum-cli export openvino --weight-format int4 --sym --group-size 32 --ratio 1.0 --backup-precision none`, without `--awq`/`--scale-estimation`/`--gptq`. All these flags are listed in the optimum-intel export docs (https://github.com/huggingface/optimum-intel/blob/main/docs/source/openvino/export.mdx).
- Then **verify** the INT4 scales against the grid step. If NNCF uses absmax-based scales, patch the scales (or use the GPTQ-import path: OV preserves pre-quantized HF GPTQ checkpoints, per OV docs).
- Expected cost of group 32 vs 128 on GPU: more scale traffic, so a speed hit is possible. Measure.
- There is no OV MTP IR for Gemma. MTP would need the assistant exported too, and OV 2026.4 claims Gemma 4 MTP support (release notes) without shipping the IR.

**vLLM-XPU**
- Produce compressed-tensors `pack-quantized` int4 sym **group 32** with the grid-step scales, like Google's 31B ct but with the scale fix.
- XPU wNa16 kernels accept "group sizes are multiples of 32" (`vllm/model_executor/kernels/linear/mixed_precision/xpu.py`). XPU MoE "W4A16 int4 group_size=32" landed in v0.24.
- This also answers vLLM's stated reason for skipping 26B ("704 experts → excessive loss with 4-bit"): that loss was measured for PTQ. QAT experts are already 4-bit-exact. **Open question:** measure the accuracy of this self-built ct against BF16.

---

## 4. Decision table for our hardware

**Hardware**
- Server: Xeon 6741P (AMX) + 4× Arc Pro B70 (`xe`, 32 GB each = 128 GB).
- Also: client CPUs without AMX.

**Source rule:** official google / Qwen / Intel / OpenVINO first, then RedHatAI, then 88plug/self-built unless measured better.
- "Fits" = weights only. KV, activations and the vision encoder come on top.
- **[NV]** = NVIDIA-only.

### Gemma-4-26B-A4B

**vLLM-XPU, 4× B70**
- Recommended variant: `google/gemma-4-26B-A4B-it` BF16, 51.6 GB, TP4 per the Intel-validated recipe.
- Low-memory pick inside tier 1: `Intel/…-int4-AutoRound`, 15.4 GB, single GPU. Swap in the canonical template.
- Speculative pairing: `google/…-it-assistant` (non-QAT, matches the BF16/PTQ target), `method mtp`, N=4. On llm-scaler the method is `gemma4_mtp`. If the V1 runner errors, set `VLLM_USE_V2_MODEL_RUNNER=1` (#51493).
- Flags:
  - `vllm/vllm-openai-xpu:latest`
  - `--enforce-eager --attention-backend flash_attn --no-enable-prefix-caching --max-num-batched-tokens 8192 -tp 4`
  - `--tool-call-parser gemma4 --reasoning-parser gemma4 --enable-auto-tool-choice --chat-template examples/tool_chat_template_gemma4.jinja`
  - `--limit-mm-per-prompt '{"image":0,"audio":0}'` for text-only
- Open questions (measure):
  - MTP gain at bs=1 on an MoE on B70 (Google says it may be none).
  - AutoRound int4 accuracy vs BF16: no published numbers, and 704-wide experts.
  - RedHat FP8-dynamic (28.6 GB) TP2 vs BF16 TP4.
  - Self-built QAT ct int4 g32 (§3.4).
  - Recipe `--max-model-len 5120` is far below 256K. Find the real ceiling.

**OVMS / OV GenAI, 1× B70 GPU**
- Recommended variant: `OpenVINO/gemma-4-26b-a4b-it-int4-ov`, 15.3 GB.
- Speculative pairing: **none shipped** (no MTP IR).
- Flags: `--source_model OpenVINO/gemma-4-26b-a4b-it-int4-ov --target_device GPU.0`, tool/reasoning parser `gemma4` (auto-detected).
- Open questions:
  - GPU correctness on 2026.4.1, given the OVMS 2026.3 "GPU … might be not reliable" note.
  - README (ASYM g128) vs config (AWQ g64) mismatch: confirm what the IR really is.
  - Self-built QAT INT4_SYM g32 IR vs official.

**OV / OVMS on Xeon AMX CPU**
- Recommended variant: same OV int4 IR on `CPU`.
- Speculative pairing: none.
- Flags: `--target_device CPU`.
- Open questions: OV never lists Gemma4-26B for CPU explicitly. OVMS 2026.3 says CPU-only. Verify.

**vLLM CPU on Xeon AMX**
- Recommended variant: `google/…-it` BF16 (vLLM guide: Gemma 4 on "Intel Xeon 6 CPUs", image `vllm/vllm-openai-cpu:latest-x86_64`).
- Speculative pairing: assistant, N=4.
- Flags: TP = NUMA nodes (Gemma4.md, the Xeon 6 section).
- Open questions: tok/s vs OV int4 on CPU.

**llama.cpp (SYCL or Vulkan on B70; CPU)**
- Recommended variant: `google/…-qat-q4_0-gguf` (Q4_0 + Q6_K embed), 14.4 GB, plus mmproj 1.19 GB.
- Speculative pairing: QAT assistant, self-converted to Q4_0 GGUF (none official). Only adopt if measured faster: llama.cpp authors saw no MoE gain.
- Flags: `--jinja -ngl 99 -mm <mmproj>`, `-rea on|off`; for MTP `--model-draft <qat-assistant-q4_0.gguf> --spec-type draft-mtp --spec-draft-n-max 4`.
- Open questions:
  - Grid-exact self-built Q4_0 (§3.4) vs Google's GGUF on accuracy.
  - SYCL MTP crash #29419.
  - Vulkan `-np 4` crash #29221.

**Client CPU (no AMX)**
- Recommended variant: QAT Q4_0 GGUF (llama.cpp) or OV int4 IR (CPU).
- Speculative pairing: none, or MTP if measured.
- Open questions: OV int4 vs llama.cpp Q4_0 on AVX2/AVX-512 VNNI.

### Qwen3.8-27B

**vLLM-XPU, 4× B70**
- Recommended variant: `Qwen/Qwen3.8-27B` BF16, 55.6 GB, TP4 (recipe-validated on B60/B70). Or `Qwen/Qwen3.8-27B-FP8`, 30.9 GB, block-128, TP2 (`fp8_per_block` is in the XPU list; llm-scaler 1.5 supports Qwen3.8-27B block-FP8).
- Speculative pairing: native `method mtp`, N=3 (`qwen3_5_mtp` on llm-scaler). Tier 2: `RedHatAI/Qwen3.8-27B-INT4` (19.45 GB) + `…-speculator.dspark`, N=7.
- Flags: `--enforce-eager -tp 4 --tool-call-parser qwen3_coder --reasoning-parser qwen3 --enable-auto-tool-choice`, `--language-model-only` for text-only.
- Open questions:
  - MTP on upstream XPU: not in the recipe; llm-scaler claims it.
  - DSpark on XPU: only DSv4 DSpark is noted (v0.26 #47677).
  - RedHat INT4 auto-enables an FP8 KV cache, so pin `--kv-cache-dtype` when comparing.
  - Whether CT W4A16 g128 + actorder runs on XPU wNa16.

**OVMS / OV GenAI, 1× B70 GPU**
- Recommended variant: `OpenVINO/Qwen3.8-27B-int4-ov`, 15.9 GB incl. MTP.
- Speculative pairing: built-in MTP IR (`--draft_model_path .`), `num_assistant_tokens` 2–3. The notebook's best is n=2: 1.78×.
- Flags: `--enable_prefix_caching false` (required with MTP); parsers `qwen3coder` + `qwen3`; `max_tokens` must be set.
- Open questions:
  - MTP is greedy-only. Benchmark sampled requests without MTP.
  - The int8-ov repo (27.8 GB) on a 32 GB card leaves almost no KV room, and multi-GPU TP is undocumented.
  - Tokenizer max length is 131072. Override it if you need 262K.

**OV on Xeon AMX CPU**
- Recommended variant: `OpenVINO/Qwen3.8-27B-int8-ov` (27.8 GB; quality) or int4-ov (speed).
- Speculative pairing: built-in MTP (CPU supported).
- Flags: `--target_device CPU`.
- Open questions: int8 vs int4 tok/s on AMX; whether MTP helps on CPU.

**llama.cpp (B70 SYCL/Vulkan; CPU)**
- Recommended variant: `Intel/Qwen3.8-27B-q4km-AutoRound`, 16.8 GB, plus mmproj 1.84 GB. It contains the `nextn` MTP head.
- Speculative pairing: in-GGUF MTP, `--spec-type draft-mtp --spec-draft-n-max 3`.
- Flags: `--jinja -ngl 99`.
- Open questions: GDN op speed on SYCL vs Vulkan; whether the MTP path works on SYCL.

**Client CPU**
- Recommended variant: OV int4-ov or the q4km GGUF.
- Speculative pairing: MTP in either.
- Open questions: which is faster without AMX.

### Qwen3.8-Flash-Next

**vLLM-XPU**
- **Blocked**: `Qwen4Exp currently supports CUDA and ROCm only`. The n-gram CPU offload is [NV]-only.
- Speculative pairing: n/a.
- Open questions: watch vllm-xpu-kernels #635 and the upstream gate.

**OpenVINO / OVMS**
- **Not supported**: `qwen4_exp` is not in any OV release.
- Speculative pairing: n/a.

**llama.cpp (only path)**
- Recommended variant: **self-built GGUF**. No GGUF exists in the set.
  - Starting points: `Qwen/Qwen3.8-Flash-Next` BF16 (tier 1), or Intel's W4A16 for reference accuracy.
  - Layout: experts in Q4 across the 4 B70s; n-gram table (51.2B params, 102 GB in BF16) in host RAM.
- Speculative pairing: in-model MTP, `--spec-type draft-mtp` (#29761: 1.55×, 0.64 acceptance).
- Flags: `--jinja`, split across GPUs, n-gram table via `ggml_get_rows` on host.
- Open questions:
  - Is qwen4exp correct on SYCL/Vulkan? QSA and hyper-connection ops are UNVERIFIED there.
  - Host RAM size needed.
  - n-gram table quantization (FP8/Q8 halves it).
  - License `qwen-community-1.0`.

**KTransformers / SGLang**
- Named by Qwen (`F/README.md:324`). SGLang offloads via `--ple-offload-embedding`.
- No Intel-GPU recipe found. **UNVERIFIED** on our hardware.

### NVIDIA-only or not usable here

- **NVFP4:**
  - `RedHatAI/Qwen3.8-27B-NVFP4` is referenced by the DSpark card (not in our set).
  - There is no NVFP4 kernel entry for XPU in vLLM (`kernels/linear/__init__.py`). AutoRound: Intel GPUs "have no native NVFP4 acceleration path".
- **vLLM Gemma4 MTP nightly images** `gemma4-0505-cu129/cu130` are CUDA-only. **[NV]**
- **Flash-Next `--engram-config cpu_offload`:** "only runs on Nvidia devices". **[NV]**
- **MXFP4:** none of the 24 repos use it. vLLM-XPU has MXFP4 kernels, but llm-scaler documents MXFP4 for gpt-oss only.
- **FlashInfer, CUTLASS and Marlin:** none of these backends exist on XPU. Benchmarks borrowed from H100/H200/B200 cards (eagle3, DSpark, the vLLM MTP PR) do **not** transfer.

---

## 5. Known gotchas: what breaks benchmarks or makes them unfair

### 5.1 Thinking defaults differ per family and per engine

**Qwen3.8**
- Thinking is **on** by default.
- The default `reasoning_effort=xhigh` **adds a system message to every prompt** (`Q/chat_template.jinja:45-86`).
- `preserve_thinking` defaults on, which re-injects all historical reasoning.
- Effect: prompt length and KV differ from a "plain" prompt, and outputs run up to 262K reasoning tokens.
- For speed runs, pin all three per request: `enable_thinking`, `reasoning_effort`, `preserve_thinking`. Report which values you used.
- vLLM forwards `reasoning_effort` values outside `xhigh|medium|low` (e.g. `high`, `max`), which makes the template **raise**.

**Gemma 4**
- The template default is **off** (`G/chat_template.jinja:186`).
- RedHat and vLLM guides force it on server-wide (`--default-chat-template-kwargs '{"enable_thinking": true}'`).
- Same model and same request, different engine → different token counts. Always pass `chat_template_kwargs.enable_thinking` explicitly.

**Sampling presets**
- Gemma: one preset, T=1.0 / p=0.95 / k=64.
- Qwen thinking: T=1.0 / p=0.95 / k=20 / pp=0.
- Qwen non-thinking: T=0.7 / p=0.8 / k=20 / **presence_penalty=1.5**.
- Engines that drop `top_k` or `presence_penalty` silently change the distribution.

### 5.2 Speculative decoding is not apples-to-apples

**OpenVINO GenAI and OVMS MTP**
- **Greedy only**: `do_sample=False`, one sequence, no confidence threshold.
- **Prefix caching must be off** (`OpenVINO__Qwen3.8-27B-int4-ov/README.md:115-139`; OVMS demo; notebook).
- OVMS also requires `max_tokens`.
- Greedy MTP output is "not guaranteed to be bit-exact" vs non-MTP.

**vLLM on XPU**
- The Gemma recipe disables prefix caching (`--no-enable-prefix-caching`) and runs `--enforce-eager`.
- Adaptive verification rejects `--enforce-eager`.
- Do not compare against CUDA-graph numbers.

**Card acceptance and speedup numbers**
- They come from H100/H200/B200 and an unnamed Windows GPU.
- The Gemma 26B MoE gains little at batch 1 (Google MTP overview; llama.cpp #23398).
- Report batch size.

**Drafter precision matching**
- A QAT target needs the QAT assistant (`…qat-q4_0-gguf/README.md:32`).
- There is no assistant GGUF and no assistant OV IR, so a QAT run with MTP requires self-converting the drafter.
- The DSpark and EAGLE-3 drafters were trained on BF16 targets. Their acceptance on INT4 or FP8 verifiers is untested by the card authors for XPU.

**Configured draft lengths**
- Gemma assistant: `num_assistant_tokens: 6` in its generation config.
- vLLM recipe: 4.
- EAGLE-3: 3.
- DSpark: 7 in the README vs 8 in the config.
- OVMS default: 5. OV notebook best: 2.
- Pin and report N.

### 5.3 Quantization side effects

**RedHat Qwen INT4 carries `kv_cache_scheme` FP8**
- vLLM auto-switches the KV cache to fp8 for it.
- So a "W4A16 vs BF16" comparison silently becomes "W4A16 + FP8-KV vs BF16-KV".

**OV gemma int4 README vs config**
- README: INT4_ASYM g128.
- `openvino_config.json`: data-free AWQ g64, router excluded.
- Cite the config, and confirm it from the IR.

**Google's QAT GGUF**
- It is stock `llama-quantize` Q4_0 (`absmax/8` scales) with a Q6_K embedding.
- It is not a bit-exact QAT export (§3.2). "QAT vs PTQ" conclusions drawn from it understate QAT.

**The 4-bit variants share nothing**
- Intel Gemma int4: tuned g128.
- Intel mixed: **RTN**, int8 attention.
- OV: AWQ g64.
- Google: Q4_0 g32.
- Label each by scheme, not just "int4".

**Flash-Next quantized repos**
- They quantize only routed experts (and, for FP8, the n-gram table).
- "4-bit Flash-Next" is still 181 GB.

### 5.4 Templates, BOS and tokenizer

**Intel AutoRound Gemma repos**
- They ship the pre-July-2026 template: OpenAI `role:tool` handling is broken, and `<|think|>` lacks its newline.
- Override with the canonical template, or the vLLM `examples/tool_chat_template_gemma4.jinja`.

**Gemma BOS**
- The template emits `<bos>`, and the GGUF has `add_bos_token=True`.
- Raw-completion clients that template client-side and tokenize with special tokens produce **double BOS**.
- Qwen has no BOS (`add_bos_token false`).

**Qwen output starts mid-think**
- The template pre-fills `<think>\n`, so the output starts inside the think block.
- Token counters that look for `<think>` miss it, and the reasoning parser must be `qwen3`.

**Tool arguments**
- Gemma's canonical template **raises** on string `arguments` (`G/chat_template.jinja:258-262`). Clients must send dicts.

**`OpenVINO/Qwen3.8-27B-int4-ov`**
- `model_max_length=131072`, vs 262144 everywhere else.
- Context-length sweeps will truncate.

### 5.5 Context and memory defaults

**Gemma vLLM XPU recipe**
- `--max-model-len 5120`, and FP8 card examples at 32768/69632. The model supports 262144.
- Benchmarks at different max-len reserve different KV.

**Vision encoders load by default**
- Gemma vision ~550M; Qwen ViT 27 layers.
- Use `--limit-mm-per-prompt '{"image":0,"audio":0}'` (Gemma), or `--language-model-only` / `'{"image":0,"video":0}'` (Qwen), for text-only runs. Otherwise memory and TTFT include the encoder.

**Qwen YaRN beyond 262K is static**
- It "potentially impact[s] performance on shorter texts".
- Enable it only for >262K runs (`Q/README.md:555-558`).

**OVMS prefix caching defaults**
- `--enable_prefix_caching` defaults differ between CLI (true) and graph (false).
- Linear-attention models need `--cache_interval_multiplier` tuning.

### 5.6 Versions, licensing and repo hygiene

**Required versions**
- **Gemma 4:** vLLM ≥ 0.25.0, transformers 5.5+ (configs from 5.5.0.dev0 to 5.10.0.dev0); OV IR needs `transformers==5.5.4` and OV ≥ 2026.4.0.
- **Qwen3.8 OV:** `transformers==5.2.0`.
- **DSpark:** vLLM ≥ 0.25.0.
- **Flash-Next:** vLLM ≥ 0.30.0; SGLang ≥ 0.5.22.
- **AutoRound Gemma:** auto-round PR #1656.
- **Custom code:** EAGLE-3 and DSpark need `--trust-remote-code` (`config.py`).

**Gating and licenses**
- No repo is gated.
- Licenses: Gemma 4 Apache-2.0; Qwen3.8-27B Apache-2.0; **Flash-Next `qwen-community-1.0`**.
- `Intel/qwen3.8-27B-int4-ov` is MIT, but it is a wrapper with no weights.
- `Intel/gemma-4-26B-A4B-it-int4-AutoRound` and `Intel/Qwen3.8-27B-q4km-AutoRound` carry no license field.

**Flash-Next thinking**
- The SGLang cookbook claims Flash-Next "always reasons — thinking cannot be turned off".
- Its template is byte-identical to the 27B template, which honours `enable_thinking=false` (https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-Flash-Next vs `F/chat_template.jinja`).
- Verify empirically.

**Mislabels and repo hygiene**
- EAGLE-3 card title says "gemma4-31B" but targets 26B-A4B (`…eagle3/README.md:10,15`).
- `Intel/Qwen3.8-Flash-Next-W4A16-AutoRound` has a `config.json.autoround.bak`, and its shard numbering skips 00002.
