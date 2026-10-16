# LLM benchmark landscape for k3d-gpu

Status: research only, compiled 2026-10-16. Nothing in this document was
measured on the k3d-gpu host. Every number below is someone else's published
claim, re-fetched from the primary source where marked **V** (verified). Rows
marked **S** come from a sweep and were not re-fetched for this document;
treat them as leads, not facts. Rows marked **X** failed re-verification.

Host this document targets: Intel Core i9-13900T (Raptor Lake, 8 P-cores with
HT = CPUs 0-15, 16 E-cores = CPUs 16-31, AVX2 + AVX-VNNI, no AVX-512, no AMX,
35 W base TDP), Intel UHD Graphics 770 (Gen12 Xe-LP, 32 EU), 2 NVIDIA GPUs.

## 1. Published results, deduplicated, with conditions

Three independent sweeps were merged. Duplicates collapsed: the EAS sizing
table (all three sweeps), the MLPerf v6.1 Xeon 6980P result (two sweeps) and
the OpenBenchmarking i9-14900K Qwen2.5-VL TTFT row (two sweeps).

"2nd tok/s" = OpenVINO `llm_bench` second-token throughput, i.e. 1/TPOT at
batch 1. "Server/Offline" = MLPerf LoadGen scenarios.

### 1a. Client / iGPU / desktop CPU (closest to our host)

| # | St | System | Model | Precision | In / Out | Batch / conc | Software | Metric | Value | Source (date) |
|---|----|--------|-------|-----------|----------|--------------|----------|--------|-------|---------------|
| C1 | V | Core Ultra 7 255H iGPU (Arrow Lake-H) | llama-3.2-3b-instruct | INT4-MIXED | 1024 / not stated (page) | 1 | OpenVINO 2026.4, llm_bench | 2nd tok/s; 1st-token ms | 23.4-26.9 (3 rows); 275.6-381.7 ms | [llm_models_7-255H.csv](https://docs.openvino.ai/2026/_static/benchmarks_files/llm_models_7-255H.csv) (2026-09-23) |
| C2 | V | Core Ultra 7 255H iGPU | llama-3.1-8b-instruct / qwen3-8b / qwen2.5-1.5b | INT4-MIXED | 1024 / n.s. | 1 | OV 2026.4 | 2nd tok/s | 10.8-14.4 / 10.5-14.0 / 37.2-39.2 | same CSV |
| C3 | V | Core Ultra 7 258V iGPU (Lunar Lake, Xe2) | llama-3.2-3b-instruct | INT4-MIXED | 1024 / n.s. | 1 | OV 2026.4 | 2nd tok/s; 1st ms | 39.8-41.8; 234.6-273.0 ms | [llm_models_7-258V.csv](https://docs.openvino.ai/2026/_static/benchmarks_files/llm_models_7-258V.csv) (2026-09-23) |
| C4 | V | Core Ultra 7 258V iGPU | llama-3.1-8b / qwen3-8b / phi-4-mini / qwen2.5-1.5b | INT4-MIXED | 1024 / n.s. | 1 | OV 2026.4 | 2nd tok/s | 16.6-21.4 / 19.5-20.7 / 30.4-33.3 / 51.5-75.2 (6 rows) | same CSV |
| C5 | V | Core 5-330 iGPU (Wildcat Lake) | llama-3.2-3b / llama-3.1-8b / qwen2.5-1.5b | INT4-MIXED | 1024 / n.s. | 1 | OV 2026.4 | 2nd tok/s | 22.3-22.9 / 10.9-11.2 / 39.5-42.7 | [llm_models_5-330.csv](https://docs.openvino.ai/2026/_static/benchmarks_files/llm_models_5-330.csv) (2026-09-23) |
| C6 | V | Core Ultra 7 155H iGPU-only (Meteor Lake Xe-LPG) | llama-3.2-3b-instruct | INT4 / INT8 / FP16 | 1024 / 128 (perf page) | 1 | OV 2026.4 general graphs | tok/s; ms/token | 30.33 / 21.22 / 11.99; 32.96 / 47.12 / 83.4 ms | [graph-data-ov.json](https://docs.openvino.ai/2026/_static/benchmarks_files/data/graph-data-ov.json) |
| C7 | V | Core Ultra 7 155H iGPU-only | llama-3-8b / qwen2.5-7b / phi-3-mini-4k | INT4 | 1024 / 128 | 1 | OV 2026.4 | tok/s | 15.88 / 17.15 / 25.56 | same JSON |
| C8 | V | Core Ultra 7 155H NPU-only | phi-3-mini-4k-instruct | INT4 | 1024 / 128 | 1 | OV 2026.4 | tok/s; ms | 10.36; 96.48 ms | same JSON |
| C9 | V | Core i9-13900K CPU-only, 2x32 GB DDR5-4800 | Llama-2-7b-chat | INT4 / INT8 / FP32 | 1024 / 128, beam 1 | 1 | OpenVINO 2024.0 | tok/s; ms/token | 8.9 / 6.7 / 2.2; 112.65 / 148.81 / 458.75 | [Intel OpenVINO LLM white paper, Table 1](https://www.intel.cn/content/dam/develop/public/us/en/documents/openvino-toolkit-llms-solution-white-paper.pdf) (2024) |
| C10 | V | Core i9-13900K CPU-only | chatGLM2-6b / Mistral-7b | INT4 | 1024 / 128 | 1 | OV 2024.0 | tok/s | 9.5 / 9.4 | same, Table 1 |
| C11 | V | Core i7-1360P iGPU (Iris Xe, Gen12 Xe-LP, 96 EU) | Llama-2-7b-chat | INT4 / INT8 | 1024 / 128 | 1 | OV 2024.0 | tok/s; ms/token | 4.6 / 3.5; 217.08 / 286.83 | same, Table 2 |
| C12 | V | UHD 770 (ADL-S, 32 EU) vs i9-12900K CPU | Gemma-4-26B-A4B-it GGUF | IQ4_XS | pp512 / tg128 | 1, `-r 1` | llama.cpp Vulkan, build 1f0aa2a | t/s | UHD 770: 25.4 pp / 2.37 tg. CPU 16t: 86.5 / 22.2. Q8_0 on UHD 770: OOM | [codeandcodes/intel-llamacpp-bench](https://github.com/codeandcodes/intel-llamacpp-bench) (2026-05-30) |
| C13 | V | i5-12500T UHD 770, i915, Mesa ANV 25.0.7 | Qwen3-Embedding-4B | Q8_0 | ~3000-token prompt, ubatch 512 | 1 | llama.cpp Vulkan | prefill t/s | 40.29 at first ubatch; results silently wrong past the 20 s i915 watchdog | [llama.cpp #27634](https://github.com/ggml-org/llama.cpp/issues/27634) (2026-08-23) |
| C14 | V | i5-12450H UHD (ADL-P GT1, 32 EU) | gemma3:4b / qwen3:14b | Ollama default (unstated) | unstated | 1 | Ollama 0.30.10 | decode tok/s, CPU -> iGPU | 12.85 -> 7.94 / 3.79 -> 2.67 | [ollama #14953 comment](https://github.com/ollama/ollama/issues/14953#issuecomment-5465475985) (2026-08-29) |
| C15 | S | i9-12900 NUC12 UHD 770 + Arc A770 | gemma4 E4B | Q4_K_M | pp512 / tg128 | 1 | llama.cpp SYCL | t/s | default dGPU+iGPU split 59.90 / 4.83; A770 alone 1095.78 / 32.80 | [llama.cpp discussion #26823](https://github.com/ggml-org/llama.cpp/discussions/26823) |
| C16 | S | i7-1370P iGPU (Iris Xe 96 EU) | llama3:8b | Ollama default | 17-token prompt | 1 | Ollama oneAPI build | prompt / eval tok/s | iGPU 19.27 / 4.58; CPU 9.56 / 4.55 | [ollama #6232 comment](https://github.com/ollama/ollama/issues/6232#issuecomment-2277849284) (2024-08-09) |
| C17 | S | i7-1185G7 Iris Xe 96 EU | llama-2-7b | Q4_0 | pp512 / tg128 | 1 | llama.cpp Vulkan b7124 | t/s | 38.00 / 6.01 | [llama.cpp discussion #10879](https://github.com/ggml-org/llama.cpp/discussions/10879#discussioncomment-15037280) |
| C18 | S | i9-14900K (125 W sibling of our die) | TinyLlama-1.1B-Chat | profile default | pts/openvino-genai-1.3.0 | 1 | OpenVINO GenAI 2026.0 | CPU tok/s; TTFT | 70 tok/s (4 results); 18.4 ms | [OpenBenchmarking pts/openvino-genai](https://openbenchmarking.org/test/pts/openvino-genai) |
| C19 | S | i9-14900K | Qwen2.5-VL-3B-Instruct | profile default | pts/openvino-genai-1.3.0 | 1 | OV GenAI 2026.0 | CPU TTFT | 83 ms | same |
| C20 | S | i9-14900K | granite-3.0-3b-a800m-instruct | Q8_0 | `llama-bench -n 128 -p 0` | 1 | llama.cpp, OpenBLAS | TG128 tok/s | 30 +/- 2 (6 results) | [OpenBenchmarking pts/llama-cpp](https://openbenchmarking.org/performance/test/pts/llama-cpp/ad2656d4db15a097c520ccc5e0a907a9b0ed708b) |
| C21 | S | Core Ultra 9 285K | Llama-3.1-Tulu-3-8B | Q8_0 | `llama-bench -n 128 -p 0` | 1 | llama.cpp b7083+ | TG128 tok/s | 10.0 +/- 0.1 (11 results) | [OpenBenchmarking pts/llama-cpp](https://openbenchmarking.org/performance/test/pts/llama-cpp/029f1d6c954f179afa85b95ff990bb6be4d71d5c) |
| C22 | S | Core Ultra 9 285K iGPU | Llama 2 7B (MLPerf Client v0.6) | INT4 | MLPerf Client v0.6 tasks | 1 | MLPerf Client v0.6 | TPS; system J/token | 14.49 tok/s; 4.76 J/token at 68.93 W wall | [SkatterBencher](https://skatterbencher.com/2025/05/15/improving-llama-2-performance-by-overclocking-arrow-lake-intel-graphics/) (2025-05-15) |
| C23 | S | Core Ultra 9 288V NPU | Llama 2 7B (MLPerf Client v0.6) | INT4 | v0.6 tasks | 1 | MLPerf Client v0.6 | TTFT; TPS | 1.09 s; 18.55 tok/s | [Intel newsroom](https://newsroom.intel.com/client-computing/intel-achieves-first-only-full-npu-support-mlperf-client-v0-6-benchmark) (2025-05-05) |
| C24 | X | Core Ultra 9 285K CPU | Qwen3-8B | INT4-cw vs Q4_K_M | ~11.7k / 160 | 1 | OV 2026.3 vs Ollama 0.40.1 | decode tok/s | claimed 17.6-17.8 vs 14.1 | [NoLlama BENCHMARKS.md](https://github.com/aweussom/NoLlama/blob/main/docs/BENCHMARKS.md): these numbers are **not present** in the file at HEAD on 2026-10-16. Dropped. |

C1-C5 data-quality note: each CSV holds 2-6 unlabeled rows per (model,
precision, input). They carry no run or device label, so ranges are reported.
qwen2.5-1.5b on 258V spans 51.5 to 75.2 tok/s.

### 1b. Datacenter (Xeon / Arc / Gaudi): context only, not comparable to our host

| # | St | System | Model | Precision | Workload | Software | Metric | Value | Source |
|---|----|--------|-------|-----------|----------|----------|--------|-------|--------|
| D1 | V | 2x Xeon 6980P (128 cores), 24x96 GB MRDIMM-8800 | Llama 3.1 8B | not stated | MLPerf Inference v6.1 Closed; CNN-DailyMail, max_new_tokens 128; Server SLO p99 TTFT 2000 ms / TPOT 100 ms | Intel submission `mint-lark-1a28` | tokens/s | Server 1,139.51; Offline 1,916.74 (v6.0: 471.54 / 1,229.56) | [Intel newsroom, MLPerf v6.1](https://www.intel.com/content/www/us/en/newsroom/news/data-center/intel-software-optimizations-boost-ai-inference-in-mlperf-v6-1.html) |
| D2 | V | Supermicro 2x Xeon 6980P | llama3_1-8b | not in summary | MLPerf v5.1 Closed | Intel MLPerf 5.1 container | tokens/s | Server 450.87; Offline 1195.93 (both "Result is: VALID") | [inference_results_v5.1 log summaries](https://github.com/mlcommons/inference_results_v5.1/tree/main/closed/Supermicro/results/1-node-2S-GNR_128C) |
| D3 | V | Xeon 6972P CPU-only | llama-3-8b / llama-3.2-3b / qwen2.5-7b / phi-3-mini | INT4 | batch 1, 1024 / 128 | OV 2026.4 general graphs | tok/s; ms | 155.6 / 6.4; 286.2 / 3.5; 164.7 / 6.1; 225 / 4.4 | graph-data-ov.json |
| D4 | V,flag | Xeon 6972P | deepseek-r1-distill-llama-8b | INT4 | batch 1 | OV 2026.4 | tok/s; ms | 155.4 with 38.6 ms: **self-inconsistent** (1/38.6 ms = 25.9 tok/s) | graph-data-ov.json |
| D5 | V | Xeon Platinum 8580 CPU-only | llama-3.2-3b / llama-3-8b | INT4 | batch 1, 1024 / 128 | OV 2026.4 | tok/s | 57.07 / 30.04 | graph-data-ov.json |
| D6 | V | Xeon 8580 / 8480+ / 6726P-B; Arc A770M | Meta-Llama-3-8B-Instruct | FP16 (Xeon), INT8 (Arc) | vLLM `benchmark_serving.py`, ShareGPT, request-rate sweep, prefix cache off | OVMS 2025.3 | tok/s; mean TPOT | 8580: 401.41 / 78.71 ms at 1 req/s; 1151.94 / 173.91 ms at inf. 8480+: 994.92 at inf. A770M: 2128.09 / 98.86 ms at inf | [graph-data-ovms-genai.json](https://docs.openvino.ai/2026/_static/benchmarks_files/data/graph-data-ovms-genai.json) (2025-09-03) |
| D7 | V | Lenovo SR650 V4, 2x Xeon 6740P 48C, DDR5-6400 | Llama-3.1-8B-Instruct | not stated (AMX BF16 build) | 512 / 512, users 1-128 | vLLM CPU (version n.s.) | agg tok/s; tok/s/user; TTFT | c=1: 10.16, 197.02 ms. c=16: 138.25. c=128: 405.50, 6238.52 ms | [Lenovo Press LP2406](https://lenovopress.lenovo.com/lp2406-implementing-ai-agents-without-gpus-high-performance-inference-on-intel-xeon-6) (2026-03-24) |
| D8 | S | Lenovo SR650 V4, 2x 6745P / 2x 6767P | Llama 3.1 8B | n.s. | TPOT <= 100 ms, lengths n.s. | vLLM | peak tok/s | 934 / 1,089 | [LP2363](https://lenovopress.lenovo.com/lp2363.pdf) |
| D9 | S | 2x Xeon 6972P, 6 NUMA-pinned OVMS containers | Meta-Llama-3-8B-Instruct | FP16 | ShareGPT, 6000 prompts, rate 20 | vLLM 0.7.3 bench, OVMS latest | out tok/s; TTFT; TPOT | 2317.29; 34457 ms; 223.55 ms (blows any 100 ms SLA) | [OVMS scaling demo](https://github.com/openvinotoolkit/model_server/blob/main/demos/continuous_batching/scaling/README.md) |
| D10 | S | Supermicro, Xeon 6900P (SKU text inconsistent) | Llama-3 8B | n.s. | 128 / 128, TTFT <= 3 s, TPOT <= 100 ms | vLLM CPU | users at SLA | 64 vs 24 (8568Y+) | Supermicro whitepaper |
| D11 | S | Xeon 6767P vs EPYC 9755 / 9965 | Llama-3.1-8B-Instruct | n.s. | in 128-1024, out 256-1024, TPOT < 100 ms | vLLM 0.9.1, IPEX 2.7 | ratio only | 1.4x / 2.7x; no absolute tok/s in text | Intel community blog 1717082 |
| D12 | S | Xeon 6543P-B, 8 vCPU guest | Qwen2.5-7B-Instruct | BF16 | random 128 / 128, `--ignore-eos`, c=1/4/8 | vLLM CPU | TTFT p50 / TPOT p50 / out tok/s | c=1: 409.8 ms / 184.4 ms / 5.37 | [Spinifex docs](https://docs.mulgadc.com/docs/cisco-ucs-llm-serving) |
| D13 | S | 8x Gaudi2 | Llama 2 70B | n.s. | MLPerf v4.0 | TGI | tok/s | Offline 8035.0; Server 6287.5 | Intel newsroom (2024-03-27) |
| D14 | S | Gaudi2 x8 + Xeon 8380 head | Llama-3.1-8B (ChatQnA) | bf16 | pubmed_10, out 1024, 1-256 users | OPEA v1.3 | TTFT / TPOT | 1 user 126.8 / 12.5 ms | [OPEA ChatQnA results](https://opea-project.github.io/latest/GenAIExamples/ChatQnA/benchmark_results.html) |

Flag on D3: the 6972P INT4 batch-1 numbers are 5.2x the Xeon 8580 rows in the
same file (155.6 vs 30.04 tok/s, llama-3-8b). The file gives no instance
count, core count or SNC setting, so this gap cannot be checked from the data.

### 1c. Intel's own toolkit sizing table (targets, not measurements)

| # | St | System | Model | Precision | Workload | Software | p99 TTFT / TPOT | Concurrency |
|---|----|--------|-------|-----------|----------|----------|-----------------|-------------|
| E1 | V | "2-NUMA demo system" (header: r8i.96xlarge, 96 vCPU, 4 NUMA) | Llama-3.1-8B-Instruct | BF16 | lengths not stated; context 131072 | vLLM 0.10.2+cpu | 1200 / 55 ms | 16 |
| E2 | V | same | Qwen3-1.7B | BF16 | n.s. | same | 900 / 45 ms | 32 |
| E3 | V | same | Llama-3.2-3B-Instruct | BF16 | n.s. | same | 800 / 40 ms | 24 |
| E4 | V | same | Qwen3-4B-Instruct-2507 | BF16 | n.s. | same | 850 / 42 ms | 20 |

Source: `intel/enterprise-ai-solutions`
`roles/nri_cpu_balloons/vars/model_benchmark_config.yml` @7743bac,
`benchmark_date: "2026-04-17"`. The header says 4 NUMA nodes; every entry
says `instance_type: "2-NUMA demo system"`. The file ships no raw logs and no
input/output lengths.

## 2. Has anyone run Intel Enterprise Agent Toolkit?

**No published third-party performance run of EAT, Enterprise Inference (EI)
or Enterprise AI Solutions (EAS) was found.** No run on non-Xeon hardware, a
desktop, a laptop, an iGPU, k3s/k3d or a VM was found either.

Evidence:

- EAT's own prerequisites exclude our class of host: `| CPU | 48 cores | 96+ cores |`
  (`intel/enterprise-agent-toolkit` `docs/prerequisites.md`, line 7).
- The only performance numbers in the toolkit repos are the EAS sizing
  targets (table 1c). Grepping the EAT, EI and EAS clones for
  `tokens/s|tok/s|tokens per second|TTFT|time to first|throughput|benchmark`
  returns only Grafana dashboard labels and that config file.
- `intel/enterprise-agent-toolkit` on 2026-10-16: 41 stars, 21 forks, two
  issues ever filed (#2 repo organisation, #9 vLLM chart ships with
  `apisix.enabled: false` and no auth). Discussions are disabled. The forks
  belong to Intel staff accounts.
- One third-party derivative exists: `navchetna/tender-eval`. Its README calls
  it "an extension of the Intel AI for Enterprise Agent Toolkit" with models
  served by vLLM on Xeon. It publishes no tok/s or TTFT. Its single-machine
  AI PC variant (`Ervin0307/aipc` branch, Intel NPU + Arc) explicitly does
  not use the toolkit.
- `opea-project/Enterprise-Inference` issues #27 and #29 show single-node
  deploy attempts that failed (ingress timeout, dead model endpoint), with
  no numbers. Dell and IBM write-ups describe Xeon/Gaudi3 deployments
  without measured results. (Sweep-reported, not re-fetched.)
- The only Xeon tok/s figure inside the EI repo is the HybridSearch sample:
  "Xeon" (SKU not stated) Qwen3-8B 4.5 tok/s with `max_tokens` 50. Its
  conditions are too weak to compare against. (Sweep-reported.)

Queries that returned nothing relevant:
- GitHub issue search for `benchmark` and `performance` in all four repos.
- GitHub search for `laptop OR k3s OR kind OR minikube OR "Core i" OR desktop OR VM OR AMD OR EPYC`.
- `gh search code '"enterprise-inference" tokens/s'`.
- Hacker News Algolia for "Enterprise Agent Toolkit" and "Enterprise Inference Intel".
- Exa and SearXNG web searches for EAT/EI benchmark results. SearXNG returned
  Enterprise Rent-A-Car spam.
- Not checked: the GitHub issue search for `latency` in
  opea-project/Enterprise-Inference, which hit the API rate limit.

Conclusion: a k3d-gpu run of EAT-style serving on a 24-core client CPU would
be the first public data point of its kind. It has nothing to "beat" in the
literal sense. The honest comparison is against the EAS SLO targets used as
gates (section 4), not as numbers.

## 3. Apples-to-apples targets for this host

A published number is a fair comparison only when model, precision, input and
output lengths, batch/concurrency and metric definition all match. Hardware
differs by definition, and that difference is the result. No published LLM row
exists for the i9-13900T, Raptor Lake desktop iGPUs or UHD 770 under OpenVINO
2026.x. The OV general graph data lists Raptor Lake parts (i5-13600K, i5-1335U,
i7-1355U) for vision and BERT models only.

### 3a. Fair comparisons (reproduce the exact conditions)

| Target | Our run must use | Compare to | Caveat |
|--------|------------------|------------|--------|
| T1. OV llm_bench, iGPU, INT4, 1024 in | `llm_bench` (OV / GenAI 2026.4 to match), `-d GPU`, INT4-MIXED models converted the same way, 1024-token prompt, `-ic 128`, `-n` >= 3, batch 1. Models: llama-3.2-3b-instruct, llama-3.1-8b-instruct, qwen2.5-1.5b, qwen3-8b, phi-4-mini | C1-C7 (255H, 258V, 5-330, 155H iGPUs) | UHD 770 has 32 Xe-LP EUs; 155H has 8 Xe-LPG cores (128 EU) with a newer ISA. Expect a large shortfall: report it as a ratio, not a "beat". The 4 GB single-allocation cap on UHD 770 bounds model and KV size. |
| T2. OV CPU INT4 Llama-2-7b-chat, 1024 / 128 | `llm_bench -d CPU`, Llama-2-7b-chat INT4, 1024 / 128, beam 1, P-cores only (`k3d-gpu.io/cores=performance`) | C9-C10 (i9-13900K, OV 2024.0: 8.9 tok/s) | Same die family. 13900K PL1 is 125 W vs our 35 W, and OV 2024.0 vs 2026.4. Run once with OV 2024.0 as well if the claim is "same software". |
| T3. OV CPU INT4 llama-3.2-3b / llama-3-8b, 1024 / 128 | `llm_bench -d CPU`, P-core balloon vs E-core balloon vs all cores | D5 (Xeon 8580: 57.07 / 30.04) as a ceiling reference | Different class (8580 has AMX). Valid only as "same workload" context. |
| T4. OV iGPU INT4 Llama-2-7b, 1024 / 128 | `llm_bench -d GPU` on UHD 770 | C11 (i7-1360P Iris Xe 96 EU, 4.6 tok/s) | Same Gen12 Xe-LP ISA, 3x the EUs. The fairest iGPU proxy that exists. |
| T5. llama.cpp UHD 770 vs CPU | `llama-bench -m gemma-4-26B-A4B-it-IQ4_XS.gguf -p 512 -n 128 -r 3`, Vulkan `-dev Vulkan0 -ngl 99` and CPU P-cores | C12 (UHD 770 2.37 tg / 12900K 22.2 tg) | Identical iGPU. Set `GGML_VK_SERIALIZE_SUBMISSIONS=1` (C13) or results can be silently wrong. Engine differs from our OVMS stack. |
| T6. pts/llama-cpp TG128 | `phoronix-test-suite benchmark llama-cpp`, granite-3.0-3b-a800m Q8_0 and Llama-3.1-Tulu-3-8B Q8_0, CPU BLAS | C20 (14900K 30), C21 (285K 10.0) | Profile version and llama.cpp build must match. 14900K is a 125 W upper bound. Sweep rows: re-fetch before quoting. |
| T7. pts/openvino-genai | `phoronix-test-suite benchmark openvino-genai`, TinyLlama CPU | C18 (14900K 70 tok/s, 18.4 ms) | Same caveats as T6. |
| T8. vLLM-style serving shape at c=1, 512 / 512 | `bench/llm_load.py` (or `vllm bench serve`) against our OVMS `/v3`, Llama-3.1-8B-Instruct, random 512 / 512, `ignore_eos`, c=1 | D7 c=1 (10.16 tok/s, TTFT 197 ms) | Shape match only: Xeon 6740P + AMX BF16 vLLM vs our INT4 OVMS. Report as context, not a win or loss. |

### 3b. Not comparable, and why

| Row(s) | Why not |
|--------|---------|
| D1, D2 (MLPerf Inference Xeon) | Fixed CNN-DailyMail dataset, LoadGen Server/Offline scenarios, accuracy gate (99% of FP32 ROUGE), 2-socket 256-thread AMX parts. Our host cannot submit, and a home-made "Server" number is not an MLPerf result. |
| E1-E4 (EAS targets) | Targets, not measurements; no in/out lengths; BF16 on AMX Xeon; header and entries disagree on the platform. Use them as SLO gates only. |
| D6, D9 (OVMS GenAI graphs, scaling demo) | FP16 on Xeon, ShareGPT at a request-rate sweep, OVMS 2025.3. Different precision and arrival process. |
| D8, D10, D11 | Lengths or precision not stated, or only ratios published. |
| D13, D14, HybridSearch | Gaudi accelerators, or conditions too weak (single request, 50 tokens, unknown SKU). |
| C14-C17, C22-C23 | Unstated quant or prompt (C14, C16), old MLPerf Client v0.6 with Llama 2 7B (C22, C23). Use as corroboration that 32-EU Xe-LP decode is not faster than CPU decode, not as targets. |
| MLPerf Client v2.0 | Official hardware floor is "Intel Core Ultra Series 2 (Lunar/Arrow Lake) or newer" and Arc B-series. On Linux x64 only the NativeOpenVINO path exists, with `device_type` GPU or NPU (no CPU). An unofficial run on UHD 770 is possible at best (UNVERIFIED that it loads), and no vendor result tables were found in text form. |

### 3c. UHD 770 traps that invalidate a run if ignored

- i915 `request_timeout_ms` (default 20000) silently cancels long submissions.
  Fix with `GGML_VK_SERIALIZE_SUBMISSIONS=1` (llama.cpp) or the kernel arg
  `i915.request_timeout_ms=0`. Record the value in every result file (C13, V).
- UHD 770 maximum single allocation is about 4 GB (llama.cpp #29087, S).
  Size the OVMS KV cache under it.
- Do not split work across the dGPUs and the iGPU; the default SYCL split costs
  ~7x (C15, S).
- OVMS `2026.3.1-gpu` hangs on iGPU embeddings with `rest_workers` > 1
  (model_server #4510, S). Check 2026.4.1 before trusting an iGPU result.
- MoE models on iGPU under OVMS load a second host copy (model_server #4035, S).

## 4. Methodology: align bench/llm_load.py with MLPerf Client / OVMS / vLLM

`bench/llm_load.py` lives on `claude/report-bench`, which was **not on the
remote** when this was written. The changes below are designed against its
stated interface (streaming TTFT and tok/s, JSON output).

### 4a. Reference definitions (re-fetched)

- **TPOT** = (E2E latency - TTFT) / (output_tokens - 1). This is the same
  formula in vLLM (`vllm/benchmarks/serve.py`: `tpot = latency_minus_ttft /
  (output_len - 1)`, TPOT 0 when output_len <= 1) and MLPerf LoadGen.
- **ITL** = list of gaps between streamed chunks, reported separately from TPOT
  (vLLM `itls`).
- **Output throughput** = sum(output tokens) / benchmark wall duration (vLLM
  `output_throughput`, MLPerf tokens/s).
- **MLPerf Server SLOs** (`loadgen/mlperf.conf`): `llama3_1-8b` TTFT 2000 ms /
  TPOT 100 ms; `llama3_1-8b-interactive` TTFT 500 ms / TPOT 30 ms; both at
  p99.
- **MLPerf Client** reports TTFT in seconds per prompt and TPS over all tokens
  except the first. v2.0 task shapes (in / out tokens): content generation
  128 / 256, creative writing 512 / 512, structured text 830 / 256, code
  analysis 1856 / 128, summarization 3906 / 128 and 7631 / 128 (extended).
  Its configs carry `WarmUp` and `Iterations` fields. Accuracy gate: MMLU and
  IFEval.
- **OpenVINO llm_bench**: `-n` iterations with iteration 0 excluded as warm-up;
  `-ic` caps output (default 512); prompts are permuted per iteration to defeat
  prefix caching unless `--disable_prompt_permutation` is set. Reports 1st-token
  latency, 2nd-token latency, 2nd tok/s.

### 4b. Required changes to bench/llm_load.py

1. **Metric names and formulas.** Emit `ttft_ms`, `tpot_ms` (formula above),
   `itl_ms` (list), `e2e_ms` per request. Emit `output_throughput_tok_s`
   (sum out / wall) and `total_throughput_tok_s` (in + out) per run. Emit
   `client_tps` = mean over requests of (out - 1) / (E2E - TTFT), which equals
   MLPerf Client TPS and llm_bench "2nd tok/s".
2. **Token counting.** Count output tokens from the server `usage` field
   (`stream_options: {"include_usage": true}`) or a local tokenizer, never by
   counting SSE chunks. OVMS can bundle several tokens per chunk, which breaks
   chunk-count TPOT.
3. **Percentiles.** Report mean, p50, p90 and p99 for TTFT, TPOT, ITL and E2E.
   MLPerf gates on p99; EAS targets are p99.
4. **Goodput.** Count requests meeting TTFT <= X and TPOT <= Y. Ship two presets:
   `mlperf` (2000 / 100 ms) and `interactive` (500 / 30 ms). Add an `eas` preset
   per model from table 1c. Report goodput as req/s and as a fraction.
5. **Fixed shapes.** Add `--shape` presets: `mlpc-content` 128/256,
   `mlpc-creative` 512/512, `mlpc-structured` 830/256, `mlpc-code` 1856/128,
   `mlpc-sum4k` 3906/128, `ov-1024` 1024/128, `rand-128` 128/128,
   `lenovo-512` 512/512. Build prompts to the exact input token count with the
   model tokenizer. Force output length with `ignore_eos: true` plus
   `max_tokens`, or `min_tokens` = `max_tokens`. Record actual lengths.
6. **Concurrency.** Closed-loop concurrency sweep `--concurrency 1,4,8,16,32` with
   `num_prompts` >= 10 x concurrency (Spinifex pattern). Add an optional
   open-loop `--request-rate` (Poisson) mode to match vLLM/OVMS graphs.
7. **Warm-up and repetition.** `--warmup` (default 5 requests, discarded). At
   least 3 repeats per point; report the median and min-max across repeats, and
   `n` (request count) per point.
8. **Prefix caching.** Permute prompts per request (unique prefix), like
   llm_bench, or record that the server's prefix cache is disabled. Write which
   one in the JSON.
9. **Energy.** Name the field `rapl_pkg_j_per_out_token`: integrated
   `package-0` RAPL energy over the measured window divided by output tokens.
   Also record `rapl_pkg_j_per_total_token`. Handle counter wraparound with
   `max_energy_range_uj`. Subtract an idle baseline measured over the same
   duration and report both raw and idle-subtracted values. RAPL covers the CPU
   package, including the UHD 770; it does **not** cover the NVIDIA GPUs, so
   add `nvidia-smi` power integration for dGPU runs. This is not comparable to
   MLPerf Power or SkatterBencher J/token, which use wall power through SPEC
   PTDaemon.
10. **Provenance block.** Every JSON result must carry: model id + revision,
    precision/quant, server image tag and digest, OpenVINO version, device
    (`CPU`, `GPU.0`, `GPU.1` ...), CPU set and balloon (`performance` /
    `efficient`), kernel version, compute-runtime version,
    `i915.request_timeout_ms`, power profile / PL1 if readable, shape,
    concurrency, warm-up, repeats, ignore_eos, prefix-cache state and
    timestamp. Without it a number is not publishable.
11. **Accuracy gate.** Any INT4 or speculative-decoding "beat" claim needs an
    accuracy check on the same model build (MLPerf Client uses MMLU / IFEval).
    Without it the claim is not comparable.

### 4c. Probes required before any number in section 3 is ours (all UNVERIFIED)

None of these were run, because a live benchmark workflow is using the host.

1. `llm_bench -m <llama-3.2-3b-instruct INT4-MIXED OV IR> -d GPU -p <1024-token prompt> -ic 128 -n 5` on UHD 770, then `-d CPU` under the performance balloon (T1, T3).
2. `llm_bench -m <Llama-2-7b-chat INT4 OV IR> -d CPU -p <1024-token prompt> -ic 128 -n 5`, P-cores only (T2), and `-d GPU` (T4).
3. `llama-bench -m gemma-4-26B-A4B-it-IQ4_XS.gguf -p 512 -n 128 -r 3 -dev Vulkan0 -ngl 99` with `GGML_VK_SERIALIZE_SUBMISSIONS=1`, and the CPU equivalent (T5).
4. `bench/llm_load.py --shape lenovo-512 --concurrency 1` against OVMS `/v3`, Llama-3.1-8B-Instruct, CPU device (T8).
5. MLPerf Client 2.0.1 Linux CLI with a NativeOpenVINO `device_type: GPU` config on UHD 770: record only whether it loads.

## 5. Sources not re-verified

The S rows above come from three research sweeps run on 2026-10-16. Their
source URLs are listed, but this document did not re-fetch them. Re-fetch
before quoting any S row externally. Row C24 was checked and dropped because
its cited numbers are absent from the source at HEAD.
