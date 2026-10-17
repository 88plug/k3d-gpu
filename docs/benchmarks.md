# Benchmarks: k3d-gpu against other stacks

Head-to-head runs made with `k3d-gpu bench --vs <stack>`. Each row is one ledger
hypothesis in [`EXPERIMENTS.md`](../EXPERIMENTS.md); its run section there holds the
Reproduce header, the raw-results sha256 and the exact command. A row backs a claim
only when its verdict is SUPPORTED.

Protocol: one client (`bench/llm_load.py`), one prompt set, temperature 0,
`ignore_eos` sent to every arm, warmup requests discarded, interleaved rounds
(A,B then B,A), one request in flight, energy from the RAPL package counter.
Competitors run their pinned image and pinned weights (`bench/competitors.lock`;
the served file's sha256 is checked before measuring) with vendor defaults, same
model family and 4-bit tier.

Known asymmetries, recorded in every run section: Ollama's OpenAI layer has no
`ignore_eos`, so a round where either arm stops early is INCONCLUSIVE; the k3d-gpu
arm is reached through `kubectl port-forward` (an API-server stream) while the
competitor is reached through docker-proxy, so tunnel latency and buffering land
on the k3d-gpu arm only.

| Ledger | Competitor | Tier | Decode tok/s (k3d-gpu / competitor) | J/token (k3d-gpu / competitor) | Verdict |
|---|---|---|---|---|---|
| [ledger:H19] | ollama | 4-bit (GGUF Q4_K_M) | not run | not run | PENDING |
| [ledger:H20] | llamacpp | 4-bit (GGUF Q4_K_M) | not run | not run | PENDING |
| [ledger:H21] | ovms | 4-bit (OpenVINO int4) | not run | not run | PENDING |

No head-to-head has run yet, so this page makes no speed or energy claim. The goal
of each row is a measured answer, whichever way it falls.

Run one and regenerate this page (rows without a result file stay PENDING):

```bash
k3d-gpu up && k3d-gpu agent up
k3d-gpu bench --vs ollama --id H19 --budget 900 --out bench-H19
bench/vs.py docs --out docs/benchmarks.md bench-H*/result.json
```
