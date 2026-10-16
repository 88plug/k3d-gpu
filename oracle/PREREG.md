# SLO oracle: pre-registered acceptance criteria

This file was written and committed before the simulation was run. It is not
edited afterwards. Results go in `ORACLE.md`.

## Question (narrowed after the refuter's objection)

Take a phase-separated model calibrated on a few Xeon microbenchmarks: a
prefill compute term, a decode bandwidth term and a per-step overhead. Replay
it as an iteration-level continuous-batching schedule over an engine-agnostic
agent DAG. Does it predict task-level p50/p95 better than the fair baseline?
The fair baseline uses the SAME Xeon anchors with per-phase ratio scaling,
replayed along the critical path.

The laptop-to-Xeon transfer claim is dropped unless criterion C4 shows that
the laptop calibration carries information. The sim draws laptop and Xeon
efficiencies independently, so C4 is expected to fail. An honest world is
built that way on purpose. That result describes the sim only. Only the live
probe can say whether real laptop and Xeon efficiencies correlate.

## Setup

* Ground truth is `truth_sim.py`, a separate world that the oracle never
  imports. It includes:
  - compute efficiency that varies non-linearly with tokens per step (AMX
    tile saturation);
  - bandwidth efficiency that falls as batch size grows (a stand-in for
    NUMA/SNC effects);
  - lognormal noise on every step;
  - a front-end overhead on every request;
  - a sampler cost for every sequence;
  - a block-granular prefix cache;
  - jitter on tool durations;
  - true peak and bandwidth that differ from the platform card by a random
    factor.
  These parameters are drawn per seed.
* The oracle sees the model card, the platform card and the engine config.
  The engine config covers max batch, chunk size and prefix-cache capacity.
  These are deployment flags, so they are knowable. The oracle also sees the
  trace and four anchor measurements. The anchors come from running
  microbenchmarks in the truth world.
* Traces are engine-agnostic. Each LLM step records its dependency list, its
  ordered prefix segments (key, tokens) and its output tokens. Each tool step
  records its duration. Traces hold no cached-token counts and no timestamps.
  A laptop run of each trace is also recorded, with cached tokens and a
  timeline. The baselines and ablations that need those fields use it.
* The grid covers:
  - 3 models: ~1B dense, ~8B dense, and MoE 30B-A3B;
  - 3 trace families: react chain, fan-out, long-context RAG;
  - 2 loads: low (one task at a time) and high (Poisson arrivals);
  - 8 perturbed truth seeds.

## Criteria (all computed over the full grid)

* C1 accuracy: oracle task-p50 MAPE <= 25 %, task-p95 MAPE <= 30 %, and
  per-call TPOT MAPE <= 20 %.
* C2 vs the fair baseline: at HIGH load, oracle task-p95 MAPE <= 0.5 x the
  anchor+ratio baseline's MAPE. No win is claimed at LOW load. The
  prediction is that the two MAPEs land within 15 % of each other, plus 5
  points. A large oracle win at low load would mean the sim grants the win,
  and that counts as a failure signal for the sim itself.
* C3 mechanism ablations, HIGH load, task-p95 MAPE:
  - pricing each call solo (no batching contention) must be >= 1.5x worse;
  - using the laptop-recorded cached tokens instead of the recomputed cache
    must be worse;
  - time-scaling the laptop timeline instead of replaying dependencies must
    be worse.
  If an ablation does not hurt, that component is not doing work, and the
  result is reported that way.
* C4 transfer: fit the oracle on LAPTOP anchors and apply it to the Xeon card
  with no Xeon anchor. The transfer claim is kept only if its task-p50 MAPE
  is <= 25 %. The prediction is that it fails.
* C5 error bar: band half-width = max(10 %, 2 x the relative residual on the
  held-out validation anchor). Truth p95 must fall inside the band in >= 80 %
  of cells.
* C6 break point: the oracle does not model the compute-efficiency
  saturation knee or the NUMA degradation. Sweep how strong those effects
  are, past the grid's range, and report where C1 stops holding.

Any criterion that fails is reported as failed.
