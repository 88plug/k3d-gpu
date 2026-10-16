"""Pre-registered acceptance criteria for scripts/k3d-gpu-sampler.

Committed before the sampler and its simulation exist. tests/sampler_sim.py
imports these constants and asserts against them; the thresholds are not tuned
after the fact. A criterion the simulation cannot meet is reported as FAILED,
not edited.

Scope, restated after review (the original claims were narrowed):
  * Pod attribution of GPU work uses DRM fdinfo (drm-engine-<class> ns per
    client), not the i915 PMU. The i915 PMU on the reference host is
    system-wide (cpumask=0) and cannot be scoped to a pod.
  * iGPU joules come from the RAPL PP1 ("uncore") domain split by fdinfo busy
    share. No regression is used for the iGPU.
  * P-core vs E-core joules are defined at the operating point: the energy of
    each stage's cycles at the rail voltage actually in effect during the
    window. Isolated single-stage runs are not ground truth for a concurrent
    turn, because P- and E-cores share one VccIA rail. The extra energy an
    E-core stage pays because a P-core stage raised the rail is reported
    separately as rail-coupling joules.
  * Sentinel placement counts only marginal energy: active energy per event
    plus the device's own wake/tail cost. The package idle floor is paid on
    every placement and is excluded from the argmin.
  * Every number from the simulation is about the MECHANISM under synthetic
    device parameters. None of it is a measurement of the i9-13900T.
"""

SEEDS = 200                  # perturbed parameter draws per criterion
SEED_PASS_FRACTION = 0.90    # a criterion holds if it holds on >= 90% of draws

# (a) engine-proof verdict
FAULTS_REQUIRED = 7          # 6 named silent-fallback faults + partial offload
CLEAN_RUNS = 20
CLEAN_FALSE_ALARMS_MAX = 0
# Ablations must lose at least this many faults, or the named mechanism is
# not what catches them.
ABLATE_SYSTEMWIDE_PMU_MIN_MISSES = 1   # i915 PMU instead of pod fdinfo, busy neighbour
ABLATE_NO_CEILING_MIN_MISSES = 1       # drop the cross-engine ceiling test

# (b) joules per engine
IGPU_PP1_SPLIT_MAX_ERR = 0.10          # PP1 x fdinfo share vs truth, busy neighbour present
CPU_TIME_SPLIT_MIN_ERR_IGPU = 0.50     # Kepler ratio / Scaphandre-style baseline on the iGPU stage
OPPOINT_MAX_ERR = 0.15                 # V^2-aware fit, P and E stages, concurrent turn
CONST_BETA_MIN_ERR_E = 0.15            # isolated constant-beta fit must miss the E stage by more
VIF_REFUSE = 10.0                      # identifiability gate: refuse P/E split above this VIF

# (c) sentinel placement
CROSSOVER_MAX_REL_ERR = 0.30           # fitted vs generator-exact E-core/iGPU crossover rate
RATE_MIN, RATE_MAX = 0.1, 10.0         # events/s sweep range
AUTO_VS_STATIC_MAX_LOSS = 0.02         # auto may never lose more than 2% vs static iGPU
ABLATE_NO_TAIL_MIN_WRONG = 0.50        # constant-energy fit picks wrong device at 0.1/s on >= 50% of seeds
