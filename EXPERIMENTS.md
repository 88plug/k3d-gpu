# Experiments

The ledger for every performance claim this project makes. A claim in `README.md`
or `docs/*.md` (a tok/s, J/token, "Nx faster" or "%" number, or a "first",
"only" or "fastest" superlative) must link its primary source on the same line,
or cite a row here as `[ledger:Hn]` whose verdict is **SUPPORTED**, or a sourced row
of [`docs/provenance.md`](docs/provenance.md) as `[provenance:Pn]`.
`tests/claims_check.sh` enforces this in CI.

Method: state the hypothesis and its prediction before measuring, name the probe
that could refute it, then record the verdict. A hypothesis is only marked
SUPPORTED after the probe ran on real hardware with the Reproduce header filled in.

Verdicts: `PENDING` (not yet measured), `SUPPORTED` (probe ran, prediction held),
`REFUTED` (probe ran, prediction failed), `INCONCLUSIVE` (probe ran, noise or
setup too weak to decide). Only `SUPPORTED` backs a claim.

## Hypotheses

| # | Hypothesis | Prediction | Probe | Verdict |
|---|------------|------------|-------|---------|
| H1 | EAT (Intel Enterprise Agent Toolkit) runs on a single non-Xeon machine. | On one host whose CPU is not a Xeon (Core or Core Ultra), `k3d-gpu up` followed by the EAT deploy from `claude/eat-on-k3d` reaches all EAT pods `Ready` and one inference request returns a completion. | Fresh host, Reproduce header filled in. `k3d-gpu up`, deploy EAT, `kubectl wait --for=condition=Ready pod --all -A --timeout=30m`, then one completion request against the EAT endpoint. Refuted by any pod not Ready at the timeout, a Xeon-only instruction or node-label requirement, or no completion. | PENDING |
| H2 | On an Intel Core Ultra host, the iGPU serves a fixed model faster and with less energy per token than the CPU running the same model with a tuned thread count. | iGPU decode tok/s is higher than the best CPU tok/s, and iGPU J/token is lower than the CPU J/token at that best thread count. | Same model, same weights digest, same prompt set, same output length, greedy decoding. CPU baseline swept over thread counts first, best run kept. Energy from RAPL `package` domain (`/sys/class/powercap/intel-rapl:0/energy_uj`) sampled before and after each run. At least 5 runs per arm, report median and spread. Refuted if the iGPU loses on either tok/s or J/token. | PENDING |
| H3 | P/E pinning isolates inference from noisy neighbours on a hybrid CPU. | With the inference pod labelled `k3d-gpu.io/cores: performance` and a CPU-saturating neighbour labelled `k3d-gpu.io/cores: efficient`, inference tok/s stays within the run-to-run spread of the no-neighbour baseline. Without pinning, the same neighbour lowers tok/s by more than that spread. | Three arms on one hybrid host: (a) pinned, no neighbour; (b) pinned, neighbour pod labelled `efficient` running `stress-ng --cpu 0`; (c) both pods unlabelled (shared balloon), same neighbour. Read each pod's `Cpus_allowed_list` from `/proc/self/status` to prove placement. At least 5 runs per arm. Refuted if (b) falls outside the spread of (a), or if (c) does not degrade (the neighbour was too weak to test isolation). | PENDING |
| H4 | The automatic device choice equals the measured best device. | For a fixed model on a host with more than one usable device (CPU, iGPU, NPU, dGPU), the device the agent stack selects automatically (`claude/agent-stack`) is the one with the highest measured tok/s in a sweep over every usable device. | Record the auto-selected device. Then force each usable device in turn and measure tok/s with the H2 method. Refuted if any forced device beats the auto choice by more than the run-to-run spread. | PENDING |
| H5 | On a hybrid CPU whose package throttles, the opt-in prefill guard (`K3D_GPU_PREFILL_GUARD`, an E-core `scaling_max_freq` cap) protects P-core prefill from an E-core-pinned CPU neighbour. | At one or more cap levels the neighbour's median P-core prefill penalty (median `ttft_s` over the quiet median, minus 1) is at most half the guard-off penalty, and the p90 penalty is below the guard-off p90 penalty. | Pre-registered in [docs/prefill-guard.md](docs/prefill-guard.md#pre-registered-ab-test-h5): stage 1 host-level, arms quiet / guard off / caps at 75%, 50% and the E-core minimum, five Latin-square rounds per neighbour method (`matrixprod`, `loop`), n = 5 per arm; neighbour bogo-ops/s, a tool-latency proxy, decode tok/s and package power always reported. Stage 2 repeats the chosen level through `k3d-gpu up` and checks the NRI write path and the `down` restore. Refuted if no cap level reaches half; inconclusive if the guard-off penalty is inside the quiet spread or the quiet arm drifts. | PENDING |

## Falsification log

Every probe run, kept or not. When an attack on a hypothesis is settled, mark it
DO-NOT-RE-ATTACK with the reason, so the same experiment is not repeated without
new evidence (new hardware, new driver, new model, changed method).

| Date | Hypothesis | Attack | Result | DO-NOT-RE-ATTACK |
|------|------------|--------|--------|------------------|
| 2026-10-16 | H3 (motivates H5) | Wave-1, host level, no cluster, i9-13900T: inference pinned to CPUs 0-14, a 15-worker `stress-ng` neighbour pinned to E-cores 17-31 (disjoint cpusets), n = 4 per arm. | Quiet median prefill 7.03 s. Prefill rose 30% with a register-only neighbour (`--cpu-method loop`), 31% with the mixed one, 40% with `matrixprod`; decode rose 6%. The host was already at PROCHOT (`package_throttle_count` about 22M, package 91-96 C under load). A register-only neighbour doing nearly the cache-heavy damage points at package power/thermal/clock coupling, not cache. Reported by the wave-1 run; its Reproduce header and raw results are not in this repository yet, so this row settles nothing and backs no claim. | No: re-run under H3's k3d-gpu probe with a full header. |

## Reproduce

Each run starts with this provenance header, copied into the falsification log
entry or a linked results file. A run without a complete header does not count.

```text
# provenance
date:            <UTC ISO-8601>
hypothesis:      <Hn>
host cpu:        <lscpu | grep 'Model name'>
host kernel:     <uname -r>
accelerators:    <k3d-gpu detect>
k3d-gpu commit:  <git rev-parse HEAD>
node image:      <docker image inspect --format '{{index .RepoDigests 0}}' $IMAGE>
model:           <name, quantization, weights sha256>
runtime:         <serving image digest and its device flags>
energy source:   <RAPL domain path, or external meter make and model>
runs per arm:    <n>
```

```bash
# Collect the header fields on the test host.
lscpu | grep 'Model name'
uname -r
scripts/k3d-gpu detect
git rev-parse HEAD
docker image inspect --format '{{index .RepoDigests 0}}' "$IMAGE"
# RAPL package energy counter in microjoules; read before and after a run.
cat /sys/class/powercap/intel-rapl:0/energy_uj
```

## Scope corrections

Limits on what a SUPPORTED verdict may be quoted as saying.

- **H1** proves one non-Xeon host model, not every non-Xeon CPU. k3d nodes are
  containers on that one host, so it says nothing about multi-host EAT.
- **H2** RAPL `package` energy covers the CPU cores and the integrated GPU, and
  leaves out DRAM, storage and the rest of the platform. J/token numbers are
  package J/token. The result holds for the measured model, quantization and
  output length only.
- **H3** pinning acts on cpusets inside the node's cgroup. It does not isolate
  memory bandwidth, last-level cache or thermal headroom, so a neighbour that
  stresses those is a separate hypothesis.
- **H4** holds for the devices present on the test host and the measured model.
  It does not show the choice is best on other hosts.
- **RAPL counter wrap**: `energy_uj` wraps at `max_energy_range_uj` in the same
  directory. A run whose end reading is below its start reading must add that
  range back or be discarded.
