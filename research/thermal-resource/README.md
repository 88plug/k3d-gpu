# Thermal headroom as a per-pod resource (H23)

Status: **PENDING**. Pre-registered in
[`EXPERIMENTS.md`](../../EXPERIMENTS.md#h23-pre-registration-2026-10-17) before
any measurement; the prediction there is REFUTED. Nothing here is wired into
`k3d-gpu` until H23 is SUPPORTED.

- `headroom_model.py`: reads TjMax, the TCC offset, PL1/PL2/Tau and the package
  temperature (read-only, `--sysroot` for fixtures), fits
  `T_pkg = T_idle + R_th * (P - P_idle)`, and turns the lower of the thermal and
  RAPL limits into whole watts of headroom (`budget`). Arm C's pod-count cap is
  `slots`.
- `test_headroom_model.py`: unit tests, run by `tests/bench_test.sh`.
- `bench/thermal-resource/probe.sh`: the live run (calibration on docker,
  one cluster `k3dgpu-i-thermal-resource`, 9 runs B T C C T B B T C, 18.5 min
  budget with a watchdog, cluster deleted on exit). `DRY_RUN=1` prints the pod
  lists of the three arms.

Prior art and the search log: `docs/provenance.md`, section "H23 prior-art
search", rows P230-P239.
