# Presence-gated operating points: pre-registration

Written and committed before any prototype code or simulation existed.
The simulation reports against these numbers without changing them.

## Host facts read before registering (read-only, 2026-10-16)

- Display: `card0-DP-1` is connected, and `card0` is vendor `0x8086`. The desktop
  scans out of the UHD 770 iGPU, so the compositor plausibly renders on it. This
  is not proven: a wayland compositor can render on another GPU and copy, and the
  DRM-master holder cannot be read without root.
- Session: logind session 2, `Type=wayland`, `Active=yes`, `IdleHint=no`, `LockedHint=no`.
- CPU: EPP is currently `performance`. RAPL `constraint_0_power_limit_uw=117000000`
  (PL1 117 W, not 35 W, on this board), so PL1 does not hold EPP flat here.
- NVIDIA: RTX 4000 SFF Ada (pl 70 W, min 30 W) and RTX 2000E Ada (pl 50 W, min 30 W).
- OVMS docs (model_server main, `docs/llm/reference.md`): `max_num_batched_tokens`,
  `dynamic_split_fuse` and `enable_prefix_caching` are graph `node_options`.
  Changing them needs `POST /v1/config/reload`, which reloads the graph
  (`docs/online_config_changes.md`). This confirms refuter point (1) for
  server-side chunk changes.

## Design response to the refuter

1. Levers are split into two kinds. LIVE levers: EPP sysfs, `nvidia-smi -pl`,
   gateway admission concurrency, gateway background-release batch, gateway
   prefill chunk and pacing, and the node label. RESTART-BOUND levers: OVMS
   node_options, llama.cpp `--parallel/-b`, and balloons re-pin. Presence flips
   touch LIVE levers only. Restart-bound levers are set once, statically, to the
   throughput-tuned value.
2. iGPU chunking moves out of OVMS into the gateway. With
   `enable_prefix_caching: true`, the gateway feeds a long prompt as successive
   prefixes with `max_tokens=1`. Each request then prefills only the new chunk
   (block-granular), and the gateway paces a gap between chunks. Chunk size and
   pacing are live, so no reload is needed.
3. The energy comparison includes the static power-cap baseline the refuter
   asked for. This is the best static (cap, EPP, batch) that keeps present p95
   TTFT within 10%.
4. The frame-time sim carries the preemption regime as an explicit parameter. It
   must be able to output KILL, meaning the throughput chunk already meets the bound.

## Acceptance criteria

AC1 attention: present→away happens only after `T_away` (120 s) with no input
while unlocked. A lock → away at once. Any input or unlock → present on that event.
The switch-back lever plan finishes within 2 s of the event.

AC2 tiering: no presence flip ever emits a restart-bound lever. Tested.

AC3 budget: chunk = floor(rate_tok_per_ms × (frame_ms − compositor_p99_ms − margin_ms)
/ block) × block, and at least one block. Tested against hand-computed numbers.

AC4 frame sim (claim 2), per preemption regime:
- `kernel` (non-preemptible slice ∝ chunk) must meet all of these, else KILL:
  - gated present-mode p99 frame time ≤ 1.25× idle desktop;
  - throughput-tuned static chunk > 1.25×;
  - gated away-mode throughput ≥ 97% of throughput-tuned static;
  - gated away-mode throughput ≥ 15% above smoothness-tuned static.
- `timeslice` (slice capped at 1 ms): expected to KILL, and the sim must report it.
- Ablations that must lose:
  - a budget built from the wrong refresh rate (60 Hz assumed on 120 Hz);
  - a server-side chunk change through config reload (switch-back > 2 s).
- Perturb rate and overheads by ±30%, and report how often each verdict holds.

AC5 energy sim (claim 1): compare the J/task of gated against the best static
config that meets the present-TTFT bound. Sweep decode power sensitivity, agent
concurrency and away fraction. The claim holds where all of these are true:
saving ≥ 25%, wall ≤ +15%, and present p95 TTFT ≤ 1.05× latency-tuned. It is
refuted where saving < 10%.
- Ablation: shuffle the attention signal (same away fraction, uncorrelated with
  the true human). It must violate present TTFT, which shows the presence signal
  is doing the work.
- The controller sees only input-event timestamps and its own calibrated
  estimates (±15% off truth). The physics model is separate.

## Predictions (registered before running)

P1 kernel regime: AC4 passes for nominal parameters.
P2 timeslice regime: KILL.
P3 concurrency 1 (single agent stream): the AC5 saving against the static-cap
   baseline stays under 25% over most of the sweep. Refuter point (2) is expected
   to hold there.
P4 concurrency ≥ 4 with a high away fraction: saving ≥ 25% only where decode is
   power-insensitive (exponent ≤ 0.3). Uncertain.
P5 the shuffled-signal ablation violates present TTFT.
