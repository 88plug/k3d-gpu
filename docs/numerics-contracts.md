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
