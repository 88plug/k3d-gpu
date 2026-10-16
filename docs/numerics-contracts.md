# Numerics contracts (prototype)

Status: research prototype. Static resolver + simulation only. No measured
evidence exists yet; every number in this file comes from the simulation below,
whose priors are assumptions, and is labelled as such.

## Idea

A *realisation* is one way to run a model: an engine image (pinned by digest), a
device, and a numeric format (BF16 on AMX, FP8 on Gaudi 3, INT4/INT8 on NPU via
OpenVINO, Q4_K_M on llama.cpp, FP16 on CUDA). Each realisation can carry a
*conformance record*: per-token KL divergence and top-1 agreement versus a
reference realisation on a golden prompt set, measured teacher-forced. Records
live in a ConfigMap keyed by (model, realisation, engine digest).

A workload declares `k3d-gpu.io/numerics` = a class (`exact`,
`bf16-equivalent`, `draft-ok`) or explicit `{max_kl, min_top1, ...}`. Before
the preference order is applied, the resolver drops every realisation whose
evidence is missing, stale (engine digest changed since measurement), measured
with a method the spec does not accept, or out of tolerance. The chosen
realisation's evidence digest is stamped on the pod. If nothing qualifies,
admission fails with a per-candidate reason.

## Pre-registered acceptance criteria (written before any code or run)

Refuter objection taken as given: with no measured evidence, "violation" is
defined by the format-class table the author writes, so the original
">=25% baseline / 0% resolver" claim is circular, and a one-line format selector
gets the same 0%. The criteria below therefore (a) concede that explicitly as a
control and (b) move the claim to the only part that can carry weight: measured,
digest-keyed evidence.

Sim setup (fixed before running): 36 cells = 6 host profiles x 3 contract
classes x 2 models; in every cell the top-preference realisation is removed.
Hidden ground truth per (model, realisation, digest) = format prior KL x
lognormal(0, sigma) per-realisation deviation, x U(5,20) regression multiplier
with probability p_bug for the current engine digest. Evidence is generated from
truth with measurement noise; it exists for the current digest with probability
q, else only for the previous digest (prob 0.9), else none. The resolver never
sees truth. Defaults: sigma=0.5, p_bug=0.10, q=0.85, noise=0.15 (relative, KL),
200 seeds.

Policies compared:
- B0 preference-order fallback (KServe-style priority, no quality gate).
- B1 format-label selector: allow formats whose prior KL is within the class,
  then preference order (the refuter's one-liner, tuned on the declared axis).
- B2 best static format table: per (class, format) allow iff the empirical
  violation probability on a separate training seed set is <= tau; tau swept,
  reported as a (violation, reject) frontier.
- R contract resolver with evidence and digest staleness (fail-closed).
- R-nodigest ablation: R that accepts evidence from a previous digest.
- R-declared: R fed format-prior pseudo-evidence instead of measurements.

Criteria:
- AC1 (circularity control): R-declared makes the identical decision to B1 in
  all 36 cells. Declared-only mode adds nothing over a format selector.
- AC2 (original claim, reported but labelled declared-only / table-defined, not
  evidence): B0 violation rate, R-declared violation and reject rate.
- AC3 (main claim, measured-evidence regime, defaults): R violation <= 2%;
  B1 violation - R violation >= 5 pp; R reject <= 30%. REFUTED if the gap is
  < 5 pp or R reject > 30%.
- AC4 (no god's-eye control): with sigma=0 and p_bug=0 (format table exactly
  right), |R violation - B1 violation| <= 1 pp. If R still "wins", the sim is
  granting the win.
- AC5 (evidence must do the work): if evidence is generated from a decorrelated
  (shuffled) truth, R's violation advantage over B1 must collapse
  (gap < 2 pp or R worse). If it survives, the win is not coming from evidence.
- AC6 (digest ablation): at p_bug > 0, R-nodigest violation > R violation by
  >= 3 pp; at p_bug = 0 they are within 1 pp.
- AC7 (B2 frontier): R is not dominated by any point on the B2 frontier
  (no tau gives both lower violation and lower reject than R).
- AC8 (staleness kill line): report R reject vs q in {0.5..1.0}; the q where
  reject crosses 30% is the operational re-measure SLO.
- AC9 (unit): stale digest, missing evidence, out-of-tolerance, non
  teacher-forced top-1, top-k-truncated KL not accepted unless the spec allows,
  tool-call parse gate, explicit reject reason, deterministic evidence digest,
  llama-perplexity --kl-divergence output parser.

## What was built

- `scripts/k3d-gpu-numerics` (Python 3 stdlib): `resolve` (filter by evidence,
  then preference order; stamps `k3d-gpu.io/realisation` and
  `k3d-gpu.io/numerics-evidence` = sha256 of the record used; exit 3 with a
  per-candidate reason), `ingest-kld` (parses `llama-perplexity
  --kl-divergence` summary lines, format taken from
  `tools/perplexity/perplexity.cpp`), `configmap` (emits the evidence ConfigMap).
  Gates: missing, stale digest, record/key digest mismatch, format mismatch,
  KL method (full-vocab only unless the spec accepts `topk:K`), teacher-forced
  required, minimum scored tokens, optional pinned reference, `max_kl`,
  `min_top1`, optional `min_tool_parse` (agent tool-call parse rate).
- `share/numerics/example.yml`: empty evidence ConfigMap + annotated pod.
- `tests/numerics_test.py`: 20 unit/CLI/sim-integrity tests (`python3 tests/numerics_test.py`; not yet in CI — adding it to `.github/workflows/ci.yml` needs a token with `workflow` scope). Every
  gate was mutation-checked: disabling any one fails at least one test.
- `tests/numerics_sim.py`: the falsification sim. It drives the real resolver.
- Not built: an admission webhook / MutatingAdmissionPolicy. The resolver is a
  library + CLI; no realisation abstraction exists on main to hook into.

## Results (SIMULATION ONLY — priors are assumptions, 200 seeds x 36 cells)

`python3 tests/numerics_sim.py --seeds 200`, defaults sigma=0.5, p_bug=0.10,
q=0.85, noise=0.15. Stable across seed bases 0 / 5000 / 77777 (R viol
0.5–0.7%, B1 8.2–8.8%).

| policy | violate | reject | needless reject |
|---|---|---|---|
| B0 preference fallback | 33.6% | 0.0% | 0.0% |
| B1 format-label selector | 8.8% | 0.0% | 0.0% |
| R contract resolver | 0.7% | 8.6% | 5.7% |
| R-nodigest (ablation) | 1.6% | 3.5% | 0.9% |
| R-declared (no evidence) | 8.8% | 0.0% | 0.0% |
| B2 best static table, tau=0.1 | 1.0% | 33.3% | 30.3% |
| B2 best static table, tau=0.2 | 6.6% | 11.1% | 10.2% |

Verdicts against the pre-registered criteria:
- AC1 PASS: R-declared == B1 in 100% of cells. Refuter is right: declared-only
  mode is exactly a format selector. The original ">=25% vs 0%" claim (AC2:
  B0 33.6%) is table-defined and is not evidence for the mechanism.
- AC3 PASS (conditional): R 0.7% vs B1 8.8% (gap 8.1 pp), reject 8.6%. The gap
  is driven by engine regressions: p_bug sweep gap = 2.7 / 3.4 / 5.1 / 8.1 /
  12.2 pp at p_bug = 0 / .02 / .05 / .1 / .2. The claim holds only if
  engine-digest regressions occur at >= ~5% per bump (or per-realisation
  spread sigma >= ~1). Both are unmeasured; they are the live probes below.
- AC4 PASS: format table exact (sigma=0, p_bug=0): B1 0.6%, R 0.2%; R only
  adds 4.8% rejects. No win is granted when there is nothing to detect.
- AC5 PASS: evidence measured from a shuffled truth makes R worse than B1
  (25.8% vs 8.8%). The win comes from evidence/truth correlation only.
- AC6 FAIL: digest keying buys 1.0 pp violation at p_bug=0.1 (needed >= 3 pp)
  and costs 5.1 pp extra rejects. Its value scales with (1-q) x p_bug. With a
  re-measure job that keeps q high, digest keying is a cheap integrity check,
  not the source of the win. The measured evidence is.
- AC7 PASS: no B2 static-table setting dominates R (best B2 at <= R's
  violation needs 33% rejects).
- AC8: R reject 29.0 / 22.2 / 16.1 / 11.1 / 8.6 / 6.7 / 4.7 / 3.2% at
  q = .5 / .6 / .7 / .8 / .85 / .9 / .95 / 1.0. Never crosses 30% in range;
  the refuter's "stale pushes rejects past 30%" needs q < 0.5 here. So the
  re-measure SLO is: evidence for >= 70% of (model, realisation) on the
  current digest keeps rejects <= ~16%.
- Half of R's rejects are needless (5.7 of 8.6 pp): a compliant realisation
  existed but had no fresh evidence. That is the real cost of fail-closed.

## Refuter points not resolved by this prototype

- Full-vocab KL needs full logits. `llama-perplexity` has them. OVMS/OpenVINO
  GenAI on NPU/iGPU and vLLM expose top-k logprobs at best, so their records
  must say `kl_method: topk:K`, which `full`-only specs refuse. Whether OVMS
  can score prompt tokens teacher-forced at all is UNVERIFIED.
- Golden-set KL is a weak proxy for tool-call correctness. `min_tool_parse` is
  a schema slot only; no measurement harness for it exists here.
- No NPU, AMX or Gaudi host: those realisations remain declared-only.

## Live probes (after the running benchmarks finish)

On this host (i9-13900T, 2x NVIDIA, UHD 770):
1. sigma, cross-backend spread within one format:
   `llama-perplexity -m $MODEL_F16 -f $GOLDEN --kl-divergence-base ref.kld -ngl 99`
   then `llama-perplexity -m $MODEL_Q4KM --kl-divergence-base ref.kld --kl-divergence -ngl 99`
   (CUDA), `-ngl 0` (CPU), and a SYCL or Vulkan build on UHD 770. Spread of
   Mean KLD / Same top p across backends for the same GGUF = sigma estimate.
   Ingest each with `k3d-gpu-numerics ingest-kld`.
2. p_bug, regression rate per engine bump: repeat probe 1 across the last N
   pinned llama.cpp release tags; count tags whose KLD moves > 2x.
3. OVMS teacher-forced scoring: does the OVMS/OpenVINO GenAI endpoint on UHD 770
   return prompt-token logprobs (`echo` + `logprobs`)? If not, top-1 records
   for OV realisations are not obtainable and those cells stay declared-only.
4. Format separation: do INT4 (OV, UHD 770) and Q4_K_M (CUDA) records land
   on opposite sides of the `bf16-equivalent` line, as the prior table assumes?

On named hardware: Core Ultra NPU (OpenVINO INT4), Xeon 4th gen+ AMX (BF16),
Gaudi 3 (vllm-gaudi FP8) — same probes 1–2 against a BF16 reference.

## Provenance

- Known (prior art): declared accuracy requirement + per-variant measured
  accuracy + filter-then-select across hardware (INFaaS, USENIX ATC'21,
  arXiv 1905.13348); per-token KLD and "Same top p" vs reference logits
  (llama.cpp `llama-perplexity --kl-divergence`); precision-class filters
  (KairosRoute `min_quantization`); digest-keyed evidence-gated k8s admission
  (purser-io/purser); accuracy-threshold precision mapping on heterogeneous
  NPUs (Spantidi, IEEE TETC 2022; PiQi 2024); priority fallback with no quality
  gate (KServe ServingRuntime autoSelect/priority).
- No prior art found (searched, weak coverage): engine-digest staleness of
  numeric-fidelity evidence used as a pre-filter on a preference-ordered
  multi-engine fallback at k8s pod admission. Likely obvious over INFaaS +
  llama.cpp KLD + purser.
- Unsearched / gaps: GitHub search API (403), arXiv API (empty, then 429),
  Google Patents (503), OSDI/SOSP/MLSys/EuroSys proceedings, USPTO full text;
  real-world engine numeric-regression frequency (the p_bug the claim rests on).
