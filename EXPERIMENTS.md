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
| H6 | On a host whose Intel iGPU has no XMX matrix engines, OVMS on the P-cores serves a 4B int4 LLM faster than OVMS on the iGPU. | On an i9-13900T (UHD 770), OVMS 2026.4.1 with `OpenVINO/Qwen3-4B-int4-ov` on the 16-CPU P-core balloon decodes at least 20% faster than the same server on the iGPU, and the iGPU's time to first token on a ~1.9k-token prompt is at least 2x the CPU's. | Pre-registered 2026-10-16 ~10:20 PDT, before the main campaign (10:29-11:32 PDT). Same server digest, model and graph except the device: GPU arm = the agent GPU manifest (2-CPU P-core balloon), CPU arm = the agent CPU manifest (16-CPU P-core balloon). Greedy, streamed, `ignore_eos`. Decode: 30-token prompt x 256 out; prefill: unique ~1936-token prompt x 16 out; 5 requests per block, 5 rounds, arm order alternating. Refuted if either margin is missed. | SUPPORTED |
| H11 | Pinning the node image to the launcher's release costs `k3d-gpu up` no measurable time, and a cluster made from another image is reported. | (a) On a host where the image is cached, `k3d-gpu up` with the pinned default reaches "Accelerators schedulable" within the run-to-run spread (max - min) of the same image named through a floating `:latest` ref (`K3D_GPU_IMAGE`); (b) the checks `up` adds before cluster create (`node_image`, `ensure_image` on a cached image) take a median under 0.5 s; (c) on a cluster created by the previous launcher (default `:latest`), `doctor` warns that it runs an unversioned image and `up` stops before `k3d cluster create`; after recreate, the server node runs `<repo>:<VERSION>` with label `org.opencontainers.image.version=<VERSION>` and `doctor` says ok. | Pre-registered 2026-10-17 03:45Z, before the timed runs. Image built from the branch with `docker build --label org.opencontainers.image.version=<VERSION> -t <repo>:<VERSION>`, the same image ID also tagged `k3dgpu-r2-us:latest` for the floating arm, so only the launcher path differs. (a) n=3 per arm, arms alternating, wall time of `up` to its final line, cluster deleted between runs. (b) n=5 timings of the two docker inspects `up` adds. Outcome -> conclusion: (a) \|Δmedian\| <= max arm spread -> SUPPORTED for (a), else REFUTED; (b) median >= 0.5 s -> REFUTED; (c) any missing warn, a `k3d cluster create` on an existing cluster, or another node image -> REFUTED. | SUPPORTED |

## Falsification log

Every probe run, kept or not. When an attack on a hypothesis is settled, mark it
DO-NOT-RE-ATTACK with the reason, so the same experiment is not repeated without
new evidence (new hardware, new driver, new model, changed method).

| Date | Hypothesis | Attack | Result | DO-NOT-RE-ATTACK |
|------|------------|--------|--------|------------------|
| 2026-10-16 | H6 | i9-13900T: OVMS 2026.4.1 + Qwen3-4B int4, P-core CPU arm vs UHD 770 arm, 5 rounds (header and numbers in [H6 run](#h6-run-i9-13900t-2026-10-16)) | Held. Decode 15.7 vs 9.9 tok/s (+58%); ~1936-token prompt time to first token 11.2 s vs 29.5 s (2.6x), i.e. prefill 172 vs 66 tok/s. The GPU pod used 1.0 CPU-s/s, the CPU pod 7.9 | DO-NOT-RE-ATTACK on UHD 770 / Raptor Lake with this model and server: re-run on new hardware, driver, model or OVMS release |
| 2026-10-16 | H4 | Same host and data as H6, against the `agent` auto rule of that time (any allocatable Intel GPU, else CPU) | That rule picked the UHD 770, which lost to the P-cores on decode and prefill. Not a verdict for H4, which needs every usable device swept with the H2 method; it changed the rule: `auto` now takes the GPU only on a `gpu.intel.com/xmx=true` node | No: H4 is re-run against the new rule |
| 2026-10-17 | H11 | i9-13900T: `up` with the pinned default vs the same image ID through a floating ref, 3 runs per arm alternating; the added inspects 5 times; a cluster made by main's launcher, then the branch launcher (header and numbers in [H11 run](#h11-run-i9-13900t-2026-10-17)) | Held. Median `up` 36.57 s pinned vs 37.20 s floating (Δ 0.63 s, arm spreads 1.15 s and 2.83 s); added checks median 0.026 s. On main's cluster `doctor` warned "node image cryptoandcoffee/k3d-gpu:latest (unversioned ...)" and `up` stopped with rc 1 before `k3d cluster create`; after recreate the node ran `cryptoandcoffee/k3d-gpu:0.1.21`, label 0.1.21, `doctor` ok | DO-NOT-RE-ATTACK unless `ensure_image`/`node_image` gain more docker calls or k3d changes how it resolves a cached image |

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

### H6 run (i9-13900T, 2026-10-16)

```text
# provenance
date:            2026-10-16T17:29Z..18:32Z (main campaign; pre-registered 17:20Z)
hypothesis:      H6
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores)
host kernel:     6.6.128-1-MANJARO
accelerators:    Intel UHD Graphics 770 (i915, no XMX) as gpu.intel.com/i915; hybrid CPU
k3d-gpu commit:  94f71e11d8a5a760010a028aaadcdb44df8df73e (local merge of claude/agent-stack 8385dc2 for the run)
node image:      k3d-gpu-h2:94f71e1, built from that commit (k3s v1.34.1+k3s1)
model:           OpenVINO/Qwen3-4B-int4-ov at b467368d16b75df14055562fe927ae8e1f15f7ef (the default branch since 2026-02-24),
                 openvino_model.bin sha256 074214fa29ab1b479536fc7184ef3036e7b355a5835e8590b61b70a8de1df2f9
runtime:         openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58 (2026.4.1-gpu),
                 --target_device=GPU (pod CPUs 0-1) vs --target_device=CPU (pod CPUs 0-15)
energy source:   /sys/class/powercap/intel-rapl:0 (package-0)
runs per arm:    5 rounds x 5 requests per workload
```

Medians over the 5 rounds, re-derived from the raw per-request files:

| Workload | Metric | GPU (UHD 770) | CPU (P-cores) |
|----------|--------|---------------|---------------|
| decode (30 in, 256 out) | decode tok/s | 9.89 | 15.66 |
| prefill (~1936 in, 16 out) | time to first token | 29.53 s | 11.21 s |
| prefill | prompt tok/s (1936 / TTFT) | 66 | 172 |
| all | pod CPU-seconds per second | 1.0 | 7.9 |

### H11 run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T03:49Z..03:56Z (pre-registered 03:45Z, commit 12d2efc, author date kept through rebase)
hypothesis:      H11
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores)
host kernel:     6.6.128-1-MANJARO
accelerators:    nvidia=2 (RTX 4000 SFF Ada, RTX 2000E Ada), intel=1 (UHD 770), hybrid
k3d-gpu commit:  this branch on base db0eeef (before #44; same launcher hunks as 5ef1863); end state re-checked with the launcher of 5ef1863 (rebased onto 8e8ba8e)
node image:      sha256:7e7cdeed4c852c6081a1efc5ce5fcc81ae3091e0f0a79f64613a4eb0fcf79a85, local build,
                 tagged cryptoandcoffee/k3d-gpu:0.1.21 and k3dgpu-r2-us:latest; label org.opencontainers.image.version=0.1.21
                 previous launcher's image: cryptoandcoffee/k3d-gpu:latest sha256:17e79e48ccd1 (label 26.04, from the Ubuntu base)
model:           none
runtime:         k3d v5.9.0, k3s v1.37.1+k3s1
energy source:   /sys/class/powercap/intel-rapl:0 (package-0), confounds only
runs per arm:    3 (order P L L P P L), checks 5
```

| Run | Arm | `up` wall (s) | package W before | load (1 min) |
|-----|-----|---------------|------------------|--------------|
| 1 | pinned | 36.57 | 86.1 | 4.27 |
| 2 | floating | 39.33 | 84.3 | 4.56 |
| 3 | floating | 36.50 | 83.1 | 4.61 |
| 4 | pinned | 36.39 | 76.0 | 5.39 |
| 5 | pinned | 37.54 | 72.8 | 4.04 |
| 6 | floating | 37.20 | 81.6 | 4.27 |

Pinned: median 36.57 s, max 37.54 s, spread 1.15 s. Floating: median 37.20 s,
max 39.33 s, spread 2.83 s. Every run ended "Accelerators schedulable: nvidia=2
intel=1 hybrid=1". Added checks (container inspect + image inspect, no cluster):
0.027, 0.028, 0.023, 0.026, 0.025 s, median 0.026 s. Confounds: 52-53 other
containers running (one came and went during runs 2-3), swap used 108.3 GiB
throughout, package throttle counter rose 6,889 over the 6 runs; per run
pinned 1,783 / 998 / 404, floating 1,940 / 1,549 / 215 (no idle window was
measured for comparison). Verdict: SUPPORTED,
confidence high for (b) and (c), moderate for (a): it bounds the difference by
the arm spread (2.83 s) only; n=3 cannot resolve a smaller effect.

```bash
# Reproduce (from the repo root of this commit; scratch kubeconfig, named cluster).
export KUBECONFIG=$(mktemp) K3D_GPU_CLUSTER=k3dgpu-r2-us
V=$(scripts/k3d-gpu version | awk '{print $2}')
docker build --label org.opencontainers.image.version="$V" -t "cryptoandcoffee/k3d-gpu:$V" .
docker tag "cryptoandcoffee/k3d-gpu:$V" k3dgpu-r2-us:latest
git show <previous release>:scripts/k3d-gpu > /tmp/k3d-gpu-old   # defaults to :latest
bash /tmp/k3d-gpu-old up
scripts/k3d-gpu doctor | grep 'node image'      # warn: ... (unversioned ...)
scripts/k3d-gpu up; echo rc=$?                  # names the image, rc=1, no create
scripts/k3d-gpu down
for arm in P L L P P L; do                     # (a)
  [ $arm = P ] && unset K3D_GPU_IMAGE || export K3D_GPU_IMAGE=k3dgpu-r2-us:latest
  s=$(date +%s.%N); scripts/k3d-gpu up >/dev/null; echo "$arm $(echo "$(date +%s.%N) - $s" | bc)"
  scripts/k3d-gpu down >/dev/null
done
docker image rm "cryptoandcoffee/k3d-gpu:$V" k3dgpu-r2-us:latest   # the local build must not shadow the published tag
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
- **H6** holds for the UHD 770 (Raptor Lake, no XMX), this model and OVMS 2026.4.1.
  It says nothing about an iGPU or dGPU with XMX, which is why `auto` still picks
  those.
- **RAPL counter wrap**: `energy_uj` wraps at `max_energy_range_uj` in the same
  directory. A run whose end reading is below its start reading must add that
  range back or be discarded.
- **H11** compares launcher paths on a cached image. It says nothing about pull
  time for an image that is not cached, which depends on the registry and link.
