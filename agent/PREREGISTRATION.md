# Agent topology: pre-registration

Committed before the simulator exists and before any number below was computed.
Every later result is judged against this file as written here. Edits after this
commit are visible in `git log -p agent/PREREGISTRATION.md`; a criterion changed
after a result counts as a failed criterion.

## Claim under test

One agent workload (RAG retrieval, LLM call with tool calls, tool execution, next
turn), split across engines by role, beats the same workload with every role
pinned to the P-cores, on the same i9-13900T package.

| role                     | split placement                         | baseline |
|--------------------------|-----------------------------------------|----------|
| LLM prefill + decode     | P-cores (`SPLIT_P`) or iGPU (`SPLIT_G`) | P-cores  |
| embeddings, rerank       | NPU if present, else iGPU (P-cores when the LLM holds the iGPU) | P-cores |
| tool sandbox, vector DB, gateway | E-cores                         | P-cores  |
| media decode             | iGPU video engines (vcs0/vcs1)          | P-cores (software) |

## Baselines (the split must beat the tuned one)

- `BASE` (all on P-cores): LLM thread count tuned per concurrency level over
  {2, 4, 6, 8}; the best value is the baseline. The split variants get the same
  tuning, no more.
- Ablation `CPU_SPLIT`: LLM on P-cores, every other role on E-cores, no
  accelerators. Separates the gain from E-core offload from the gain from iGPU
  offload.

## Metric

Agent-turn = gateway in, embed query, vector search, rerank, LLM prefill + decode
(ends in a tool call), gateway out, tool execution, optional media decode, embed
the tool output into the vector DB. Closed loop, N concurrent agents, zero think
time. Report throughput (turns/s), mean and p95 turn latency.

## Simulation criteria (model-level, judged on the committed reference priors)

- S1. Reference priors, PL1 = 117 W (the limit this host is configured to, read
  from `/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw`), N = 4:
  the best split variant delivers >= 1.20x the throughput of tuned `BASE` with
  p95 turn latency <= `BASE` p95.
- S2. The model must show at least one regime where splitting does NOT win
  (best split / tuned `BASE` throughput <= 1.05x). Predicted regimes, each
  judged separately:
  - S2a. N = 1 (no co-runners to offload away from the LLM).
  - S2b. PL1 = 35 W (Intel's Processor Base Power for this SKU) at N = 4: the
    iGPU and E-cores draw from the same package budget as the P-cores.
  - S2c. DRAM bandwidth cut to 30 GB/s at N = 4: decode is bandwidth-bound and
    moving compute elsewhere frees no bandwidth.
  A predicted regime the model does not reproduce is reported as a falsified
  prediction, not edited away.
- S3. Simulator invariants hold in unit tests (single job alone finishes in
  work / capacity; two equal jobs under processor sharing finish together at 2x;
  power cap solved exactly; bandwidth cap enforced; same seed, same output).

## Live acceptance (the only thing that counts as reduced to practice)

The simulator output is a prediction. The mechanism is reduced to practice only
when measured on this host with the `bench/` harness (claude/report-bench) and
the service-time priors replaced by H2/H3 measurements:

- L1. N = 4 agents: best split throughput >= 1.20x tuned `BASE`, p95 turn latency
  <= `BASE` p95, 3 runs each, the gain's 95% bootstrap CI lower bound > 1.0.
- L2. N = 1: split mean turn latency <= 1.05x `BASE` (splitting must not hurt the
  single-agent case by more than 5%).
- L3. The re-run simulator, fed the measured service times, predicts the
  measured L1 gain within +-15%. Outside that band the contention model is wrong
  and its regime map (S2) is not trusted.

Nothing in this branch measures L1-L3: live benchmarks occupy the host.
