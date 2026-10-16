"""Pre-registered acceptance criteria for the hybrid-split joulemeter study.

Written and committed BEFORE the meter, the simulator or any experiment ran.
tests/joulemeter_sim.py imports this file and scores its results against it;
changing a threshold after seeing results is visible in git history.

Scope: simulation only. A pass here means "the mechanism survives a physics
model that includes the refuter's failure modes"; it is not a hardware claim.

Truth convention (stated up front, refuter point 4): per-window Shapley value of
each pod over the attributable energy E(S) - E(idle), recomputed by the hidden
physics for all 2^n pod subsets (frequencies, turbo bins and the power cap are
re-solved per subset). A second convention -- marginal energy E(all) - E(all\\i),
rescaled to sum to the attributable total -- is reported alongside; a claim only
counts if it holds under BOTH.

Metric: MAPE over pods of each pod's run-total energy (core + uncore + pkg_rest),
averaged over seeds. Idle is excluded from both truth and meter (Kepler exports
it separately).
"""

SEEDS = 10              # 10 co-runs ...
WINDOWS = 1500          # ... of 5 min at 200 ms
DT = 0.2

CRITERIA = {
    # C1 mechanism sanity: linear world, planted coefficients, 1% noise.
    "C1_recovery_coef_rel_err_max": 0.05,
    "C1_recovery_pod_mape_max": 0.02,
    # C2 conservation: exact by construction after renormalisation, so it is a
    # unit test, NOT evidence. The informative number is the raw (pre-renorm)
    # model gap, reported, and required < 5% in the linear world only.
    "C2_renorm_gap_max": 1e-9,
    "C2_raw_gap_linear_max": 0.05,
    # C3 headline (physics world with shared-rail V(max f) coupling, uncapped):
    # meter MAPE <= 10% and <= 0.5 x the tuned SmartWatts-style baseline
    # (summed cycles/instructions, per-frequency NNLS, same windows, same
    # renormalisation, best of its bucketing variants), in BOTH the pinned and
    # the floating scenario, under BOTH truth conventions.
    "C3_meter_mape_max": 0.10,
    "C3_ratio_vs_smartwatts_max": 0.5,
    # Refutation lines from the original claim.
    "refute_meter_mape_above": 0.15,
    "refute_ratio_vs_static_gamma_above": 0.5,
    # C4 the original claim's baseline numbers are predictions, not facts:
    # Kepler (CPU-time split, true idle handed to it) >= 35%,
    # static-gamma (solo-calibrated per-core-type energy/cycle) >= 20%.
    "C4_kepler_mape_min": 0.35,
    "C4_static_gamma_mape_min": 0.20,
    # C5 power cap (35 W PL1, the i9-13900T): prediction that the meter still
    # holds <= 15%. If not, live tests on this host need the cap lifted.
    "C5_capped_meter_mape_max": 0.15,
    # C6 falsification controls: the mechanism must be doing the work.
    #  - hybrid split off (=SmartWatts) must raise MAPE >= 2x (same as C3);
    #  - permuting the E-PMU counter series across windows (breaks its link to
    #    RAPL) must raise meter MAPE by >= 2x;
    #  - a meter fed counters from a DIFFERENT seed's run (no shared state)
    #    must not reach C3.
    "C6_permute_degrade_min": 2.0,
    # C7 robustness: across 40 random +-30% perturbations of the hidden physics
    # (P/E capacitance ratio, V/f slope, leakage, IPC spread, noise), C3's
    # meter-vs-SmartWatts ratio holds in >= 80% of draws.
    "C7_perturb_draws": 40,
    "C7_perturb_pass_frac_min": 0.80,
}
