# Energy-aware residency advisor and tail-ledger router (research prototype)

Status: **research, simulation only**. Nothing here has been measured on hardware except the two
dGPU idle floors quoted below. Every iGPU number is an unmeasured placeholder and is swept, not
assumed.

Code: `bench/residency_model.py` (stdlib only). Tests: `python3 -m unittest bench/test_residency_model.py`.

## 1. Pre-registered acceptance criteria

Written and committed before any simulation result was produced (see `git log` for this file).
"In-sim" means the criterion is judged on the simulator; a pass is a statement about the model,
not about hardware. Section 5 lists the live probes that would decide the hardware question.

The refuter objection was accepted as the starting point, so four criteria test the objection
itself rather than the original claims.

| ID | Criterion | Pass condition |
|----|-----------|----------------|
| A1 | Tail accounting is additive (refuter: "charging E_tail per call double-counts") | For any arrival sequence on a device, the sum of the ledger's causal per-call charges equals the independently integrated power timeline to within 1e-6 relative. A naive per-call full-tail charge must over-count whenever gaps < tail length (demonstrated, not assumed). |
| A2 | No god's-eye state | Routers see only: perturbed calibration, predicted (noisy) output length, and their own causal history. Ground truth energy comes from integrating a separate power timeline. Headline numbers are re-run with every calibration parameter perturbed by +/-30 % and the sign of each conclusion is reported for every perturbation. |
| A3 | Claim (3), ledger router, in-sim | Over the swept tail grid, in cells where the dGPU tail energy is >= 1.5x the iGPU's whole incremental call energy: ledger router uses >= 20 % fewer platform J/task than both the best static device and the latency-greedy router, with p95 latency <= 1.5x latency-greedy. Attribution: removing the tail term from the ledger (an active-energy-only router, the closest prior-art analogue) must lose >= 10 percentage points of that saving. Also reported: the fraction of the plausible grid in which the condition cell exists at all. |
| A4 | Claim (2), iGPU residency, in-sim | Pre-registered expectation (refuter physics): with a desktop dGPU whose P8 floor is paid whether or not a model is resident, iGPU residency does NOT give >= 30 % fewer platform J/task at median gap >= 5 s. Pass = the simulator shows this (claim 2 killed for desktop cards). Under an RTD3 variant (floor drops when VRAM is empty, laptop dGPU), the closed-form crossover gap T* must land within +/-25 % of the simulated crossover. |
| A5 | Claim (1), advisor out-of-sample, in-sim | Advisor fitted on training traces, scored on disjoint test traces (different seeds; also a drifted step mix). Baseline = best reactive policy (LRU keep_alive in {60,300,900,inf} x max_loaded sweep, plus a load-cost-weighted eviction) tuned on the SAME training traces. Pass = stall >= 40 % lower and p95 per-call latency >= 15 % lower on test. Also reported against the baseline tuned on the test traces (oracle baseline). Ablation: a trace-blind pin set with the same remainder policy must lose most of the gain. |
| A6 | Tail calibration is resolvable | Using the nvidia-smi meter semantics (`power.draw` = 1 s average on Ampere+, `power.draw.instant`, documented accuracy +/-5 W), simulate a 100 ms poll and report the smallest tail energy estimable to within +/-50 % with N repeated calls. Pass = the original kill threshold (0.5 J) is resolvable with `power.draw.instant` and N <= 50; otherwise the kill threshold is declared unmeasurable and replaced. |

Primary energy metric everywhere: **platform J/task**, i.e. every device's power integrated over
the whole run (floors included) divided by tasks. A slower device lengthens the run and pays more
floor energy; this charges race-to-idle correctly. Incremental (above-floor) J/task is reported as
a secondary metric.

## 2. Measured inputs

| Quantity | Value | Source |
|---|---|---|
| RTX 4000 SFF Ada idle | P8, 12.86 W, 2011 MiB resident | one `nvidia-smi` query on this host (refuter) |
| RTX 2000E Ada idle | P8, 6.94 W, 8 MiB resident | same |
| `power.draw` semantics | 1 s average on Ampere or newer; accuracy +/-5 W | `nvidia-smi --help-query-gpu`, driver 590.48.01 |
| `power.draw.instant` | exists on this host | same |

Everything else (dGPU active/tail power and duration, iGPU decode speed and package increment,
load bandwidth, package floor) is a placeholder and is swept.
