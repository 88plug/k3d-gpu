# DRAM bandwidth as a schedulable UMA resource (prototype)

Status: **static prototype + simulation only.** Nothing here is wired into
`k3d-gpu up`. No cluster, server or benchmark was run to produce these results.

On client SoCs (Raptor Lake i9-13900T + UHD 770) the CPU cores and the iGPU share
one DDR5 controller. LLM decode on both is DRAM-bandwidth-bound. This prototype
treats DRAM GB/s as a node resource (`k3d-gpu.io/mem-bw-gbps`) and pairs it with a
cgroup-only runtime guard, because this part has no RDT/MBA (checked read-only:
no `rdt_a`/`mba` cpuinfo flags).

## Pre-registered acceptance criteria (written before any code)

Committed in its own commit, ahead of the implementation, so the criteria
cannot be fitted to the results. Sim criteria are judged on the simulator in
`sim.py`; live criteria (L*) are for the host probes listed below and are NOT
judged here.

Mechanism correctness (unit tests):
- U1. Bytes per decoded token = weight bytes + context_tokens x KV bytes per
  token (every decode step re-reads the whole KV cache, not one token's KV).
  Llama-3.1-8B fp16 KV = 2 x 32 layers x 8 kv-heads x 128 dim x 2 B = 131072 B/token.
- U2. The mutating-webhook handler adds `k3d-gpu.io/mem-bw-gbps` (integer GB/s,
  rounded up) to requests and limits from pod annotations, returns a valid
  JSONPatch AdmissionReview, and leaves pods without model annotations alone.
- U3. Node capacity = floor(0.85 x measured STREAM triad GB/s).
- U4. The guard reads PSI `memory.pressure` and `memory.stat` refaults from a
  cgroup tree and writes a `cpu.max` string ("<quota> <period>" or "max").

Simulation (S*), each with an ablation that must FAIL when the key element is removed:
- S1 Admission (greedy demand). With demand = the pod's unconstrained (greedy)
  draw, every pod admitted to the UMA node keeps p95 ITL within 15% of solo,
  while default co-location degrades at least one pod by >= 40%.
  Ablation S1a: demand = (bytes/token x declared target tok/s), the idea as
  originally written. Prediction: it admits the same set as default and does
  NOT protect (refuter objection: greedy decoders ignore the target).
- S2 Aggregate. On the UMA node alone (no dGPU steering), aggregate tok/s
  under greedy admission is within 5% of default co-location.
- S3 Guard. Bursty UHD 770 decode (duty cycle <= 0.5) next to a STREAM-like
  BestEffort sandbox: victim active-period throughput >= 90% of solo, with
  sandbox slowdown >= 25% lower than the best static cpu.max (oracle-tuned)
  that achieves the same 90%.
  Ablation S3a: duty cycle 1.0 -> the guard's advantage over static must fall
  below 25% (the win must come from temporal adaptivity, not from the sim).
  Ablation S3b: guard ranks sandboxes at random instead of by PSI/refaults ->
  must miss the 90% target or cost more sandbox throughput.
  Ablation S3c: sensor disabled (guard never sees ITL) -> victim < 90%.
- S4 Attribution. With DRAM contention removed (infinite bandwidth) and only
  the power limit active, naive co-location must NOT degrade the victim by
  >= 15% at PL1=117 W. If it does at PL1=35 W, that degradation is power, not
  bandwidth, and is reported separately.
- S5 Robustness. S1 and S3 are re-run over a perturbation grid (DRAM capacity,
  iGPU reach, arbitration weight, loaded-latency knee, cpu.max->bandwidth curve
  exponent, sensor noise). Report the fraction of the grid where each passes;
  do not report only the best point.
- Kill (sim). Report the region of the grid where naive co-location degrades
  the iGPU victim < 15%. If the plausible UHD 770 region (reach <= 25 GB/s) is
  entirely inside it, the idea is dead for this host before any live probe.

Live (not judged here):
- L1. UHD 770 OVMS decode p95 ITL solo vs next to a STREAM-like sandbox, PL1
  pinned (same value both runs). Kill if degradation < 15%.
- L2. Predicted bytes/token within +-20% of uncore IMC free-running traffic
  divided by tok/s, SOLO only (IMC counters are whole-socket; no per-pod
  attribution under co-run).
