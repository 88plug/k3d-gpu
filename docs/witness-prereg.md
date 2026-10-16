# Execution-path witness: pre-registration

This file was written and committed before any witness code, simulator or result existed.
The git commit that adds it timestamps the commitment.

## Claim under test (rescoped after refutation)

Full CPU fallback is trivial to see. The GPU client logs ~0 ns of engine time,
and any fdinfo reader (intel_gpu_top -p, nvtop, qmassa) shows it. That case stays
only as a sanity check. It does not carry the claim.

The claim is about the two hard cases that a fixed "busy >= 0.7 x wall" rule gets wrong:

1. **Partial fallback.** Some layers or ops run on CPU and the rest on the GPU
   (llama.cpp with partial offload, HETERO, unsupported ops split off). The GPU
   is busy, so an "any engine time" check passes the run.
2. **Clean runs that look idle.** Memory-bound or launch-bound decode, host-side
   tokenization and sampling, and agent tool waits all push engine busy time far
   below wall time on a run that never left the GPU.

The mechanism: a same-pod sidecar samples raw counters once per period. Sources are DRM fdinfo
(i915 `drm-engine-*` ns, xe `drm-cycles-*`/`drm-total-cycles-*`), NVIDIA per-process
SM samples, and `/proc/<pid>/stat` CPU time. It evaluates a rule whose sha256 is
written into the bundle before the run. The rule has two elements under test:

- **E1, interval resolution.** CPU excess over a host allowance `c0` is computed for each
  sample interval, not over the whole run, so idle tool waits cannot absorb it.
- **E2, engine-process scoping.** CPU time counts only for processes that hold a
  DRM or NVIDIA client on the claimed device, not for the whole pod. Separate
  in-pod tool processes therefore do not count as fallback.

Statistic: `S = sum(excess_i*dt) / (sum(excess_i*dt) + sum(gpu_busy_i*dt))`, where
`excess_i = max(0, engine_cpu_cores_i - c0)`. The run FAILs if `S > s_max`, if the claimed
device shows no engine time, or if the busy time sits on a different device.

Pre-registered rule parameters:

- `period_ms=100`
- `c0=1.25` cores
- `s_max=0.10`
- `min_gpu_share=0.05` (GPU busy over non-idle intervals)
- engine classes counted: i915 `render`,`compute`; xe `rcs`,`ccs`; NVIDIA `sm`

## Simulation campaign (seeded, 200 runs per class)

The simulator emits only raw text: fdinfo files, `/proc/<pid>/stat` lines and
`nvidia-smi pmon` rows, with the kernel's quantization (`/proc` CPU in 10 ms ticks).
The witness reads only that text, through the same parsers it uses on a real host.

- CLEAN: per-token GPU busy fraction U(0.25, 0.95). Host phases are single-threaded.
  Agent tool waits fill 0-70% of wall. A runtime spin thread uses 0-1 core. In 50% of runs a
  separate in-pod tool process burns 2-6 cores.
- PARTIAL: CLEAN plus a fraction f ~ U(0.10, 0.50) of per-token compute on CPU with
  2-8 threads.
- FULL: all compute on CPU.
- WRONG: the GPU work runs on a different pdev than the one claimed.

## Acceptance criteria

- C1: the witness flags >= 95% of PARTIAL+FULL+WRONG and <= 2% of CLEAN.
- C2: baseline B2 (fixed rule: busy >= 0.7 x wall and pod CPU < 1.5 cores) flags >= 20% of CLEAN.
- C3: baseline B1 (allocation + any engine time on the claimed device) flags <= 10% of PARTIAL.
- C4: ablation A1 (E1 removed: whole-run aggregate excess) cuts PARTIAL detection by >= 20 points.
- C5: ablation A2 (E2 removed: whole-pod CPU) raises CLEAN false flags by >= 10 points.
- C6: the verifier returns FAIL on 100% of tampered bundles. Tampering means a rule edited after the run,
  or a recorded verdict that does not match the recomputation from raw samples.

## Falsification

- If witness detection is < 90% or CLEAN false flags are > 5%, the claim is falsified.
- If C4 or C5 is not met, that element is not doing the work and is dropped from the claim.

## Predictions recorded before running

- The witness misses partial fallback that runs on a single CPU thread, because one thread
  sits inside `c0`. Expected detection there is well below 50%.
- Sampling periods long enough to straddle tool waits (>= 1 s) erode E1.

## Scope limits

- Simulated rates validate the logic against the simulator's assumptions, not against real
  hardware. Real rates need the live campaign listed in docs/witness.md.
- NPU: no primary source found shows per-client `drm-engine-*` keys for ivpu.
  The NPU path is out of scope.
