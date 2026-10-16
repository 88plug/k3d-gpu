# Pre-registration: energy-in-the-loop MCP (per-call joule receipts + baggage budget steering)

Written and committed 2026-10-16 **before** `enersim.py` existed. Thresholds below are not
to be edited after results are seen; any later change goes in DESIGN.md as a deviation.

## Claims under test

| ID | Claim | Pass condition | Refuted if |
|----|-------|----------------|------------|
| H1 | Arithmetic gate | Upper bound on savings = (steerable share of task joules) x (best per-call relative saving). Must be >= 20 % for the original claim to be possible. | bound < 20 % in the LLM-dominated regime (LLM share >= 0.75). Bound < 10 % = "impossible by arithmetic". |
| H2 | Original claim, **fair** baseline | Steering uses >= 20 % fewer total task joules than the static min-joule-under-SLO sweep, p95 task latency <= +10 % vs the best-latency static, quality identical. | savings < 10 % or p95 > +10 %. |
| H2b | Original claim vs strawman | Same, vs best-latency static. Reported only to show the size of the strawman gap. | n/a (not decisive). |
| H3 | Weaker surviving claim (non-stationary host) | In the non-stationary regime (thermal throttle episodes + NVIDIA co-tenant contention), steering beats the fair static by >= 5 % task joules with p95 <= +10 %, in >= 80 % of perturbed parameter draws. | < 80 % of draws pass. |
| C1 | Sim-honesty control | In the **stationary** regime, steering must NOT beat the fair static by more than 2 % (no free win from the sim). | margin > 2 % = sim is broken; all other results void. |
| H4 | Budget | With baggage budget = 70 % of the fair-static median task joules, >= 90 % of tasks finish within budget. Report the unsteerable floor; if the floor exceeds the budget the claim is impossible by arithmetic, not a policy failure. | < 90 % within budget. |
| H5 | Receipt overhead (wrapper only, mock ledger) | added p99 <= 2 ms per tools/call. Excludes the real ledger query, which is a live probe. | p99 > 2 ms. |
| H6 | Confidence gating | Under realistic apportionment noise, gated steering joules <= ungated steering joules (averaged over draws). | gated worse in > 50 % of draws. |

## Metric

Task joules = integral of **whole-host** power over the task's wall time (psys/wall-meter
analogue + both NVIDIA boards), i.e. idle power x duration + incremental power. A variant
that is slower pays idle power for longer (race-to-idle is in the metric, not assumed away).

## Baselines (defined before running)

- **static-best-latency**: each tool fixed to its lowest mean-latency variant (the idea's baseline; strawman per the objection).
- **static-min-joule-SLO (fair)**: offline sweep on a separate seed under the same
  non-stationary process (representative conditions). Per tool, choose the variant minimising
  mean task joules subject to tool p95 latency <= 1.10 x the best-latency variant's p95.
  Same pods, no runtime steering.
- **oracle-dynamic**: knows the true current cost of every variant. Upper bound on any runtime policy.

## Ablations (all pre-declared)

steer (full) | no-confidence-gate | epsilon=0 | steer on incremental joules only (ignores idle x latency) | no budget mode.

## Perturbations (sim must not secretly grant the win)

LLM share in {0.60, 0.75, 0.90, 0.95}; E/P joules-per-op ratio in {0.7, 1.0, 1.3}
(E can be worse: race-to-idle); host idle power in {12, 25, 45} W; non-stationarity amplitude
in {0, 0.15, 0.30}; apportionment noise sigma_bg in {0.5, 2, 5} W; 5 seeds. Coefficients are
unfitted priors, labelled as such.
