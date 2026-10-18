# k3d-gpu on Intel hardware: technical report

Status: working report, 2026-10-17. Ledger: [EXPERIMENTS.md](../../EXPERIMENTS.md), verdicts as of origin/main `ad69fc0`.

## Abstract

k3d-gpu turns one Linux host into a single-node k3s cluster (k3d) that detects the host's Intel hardware and configures it. It sets NRI balloons for hybrid P/E and NUMA CPUs, device plugins for iGPU, dGPU, NPU, DSA, IAA, QAT and Gaudi, and placement labels for agent and inference pods.

This report covers what was measured, and how. Every result comes from one host: an i9-13900T (Raptor Lake-S, 8 P-cores and 16 E-cores, UHD 770 iGPU with no XMX, AVX-VNNI, no AMX, no NPU). It is a T-SKU rated at 35 W base power, but its firmware runs PL1 = PL2 = 117 W, so it does not run the T-series 35 W envelope.

- Nine hypotheses are SUPPORTED. All nine are scoped to that host.
- Seven are REFUTED. Six of them changed shipped defaults.
- Fourteen are INCONCLUSIVE, mostly because the shared host throttles or its load was unmatched. Twelve are PENDING, nine of them the agent benchmark (H40-H48), pre-registered on 2026-10-17.
- Every other Intel family is detected and configured in fixture tests only. None has a measurement.

No world-first, superlative or priority claim is made; [docs/provenance.md](../provenance.md) supports none.

## Method

### Pre-registered ledger

Each hypothesis is a row of the Hypotheses table in `EXPERIMENTS.md`, written and committed before its first run. A row holds three things:

- a prediction, often with the predicted outcome range;
- a probe that could refute it;
- an outcome table that maps observations to verdicts.

A verdict is one of `PENDING`, `SUPPORTED`, `REFUTED` or `INCONCLUSIVE`. Most rows are scored by an analysis script (`analyze.py`, `joulepack.py verdict` and similar), not by hand. A run that misses its pre-registered protocol is INCONCLUSIVE even when the result looks good; [ledger:H1] is the example.

`tests/claims_check.sh` runs in CI. A performance number in `README.md`, `docs/*.md` or `docs/intel/*.md` must cite one of:

- a SUPPORTED ledger row;
- a sourced provenance row;
- a primary-source URL.

`tests/ledger_drift_check.py` also runs in CI. It fails when a doc states a verdict for a row (`H2 is REFUTED`, `PENDING [ledger:H1]`, a verdict table cell or a verdict section heading) that differs from the ledger, and when a verdict count in this report's summary is off.

### Matched-load design

The host is a shared, live machine: about 53 other containers, about 80 W idle package power, swap in use. So the probes control load instead of assuming a quiet box:

- **Interleaved arms.** ABBA or rotated orders (for example `A3f A4f B4 B3 B3 B4 A4f A3f`), so drift lands on every arm.
- **Fresh cluster per run.** Each run uses a scratch `KUBECONFIG` and its own named cluster, inside a host-wide lock.
- **Confounds logged per sample.** Package watts (RAPL), package throttle count, max thermal-zone temperature, load1, swap, and container count.
- **Noise guardrail.** A calibration arm must land within a fixed band of its known value. If it does not, the row is INCONCLUSIVE ("setup too noisy"), whatever the direction.
- **cpuset audit.** Placement is read from `/proc/self/status` (`Cpus_allowed_list`) inside the pod and compared with `/sys/devices/cpu_core/cpus` and `cpu_atom/cpus`.

## Results

### SUPPORTED (quotable, scoped to the i9-13900T)

| Row | Result | Scope limit |
|---|---|---|
| H6 | With no XMX on the iGPU, OVMS on the P-cores beat OVMS on the UHD 770: decode 15.7 vs 9.9 tok/s, and on a ~1936-token prompt TTFT was 11.2 s vs 29.5 s [ledger:H6] | UHD 770, Qwen3-4B int4, OVMS 2026.4.1. Says nothing about XMX iGPUs or dGPUs |
| H11 | Pinning the node image to the release costs `up` nothing measurable: median 36.57 s pinned vs 37.20 s floating, inside both arms' spread [ledger:H11] | Cached image; pull time not covered |
| H14 | Intel EAT's NRI balloons chart (v0.14.0) installs over a k3d-gpu cluster two ways: on a `K3D_GPU_BALLOONS=external` cluster with no flag, or on a default cluster with Helm 3 `--force` or Helm 4 `--force-conflicts`. 12/12 runs; `vllm` landed on P-cores 10-11, `redis` on E-cores 24-25 [ledger:H14] | Stand-in containers, not a full EAT deploy |
| H15 | Two pods share the UHD 770 by default (`K3D_GPU_INTEL_SHARED=auto`, 8 per iGPU). The two-pod aggregate was 649.6 fps, 1.18x solo [ledger:H15]. With exclusive allocation the second pod stayed Pending | VA-API media workload. Shows concurrency, not isolation |
| H16 | `K3D_GPU_DRA=1` hands the iGPU to a pod through the Intel GPU DRA driver gpu-v0.12.1 with no device plugin, 3/3 runs [ledger:H16] | One UHD 770, k3s 1.37 |
| H1b | Intel EAT `release-1.0.3-hotfix` deployed and served on this 24-core client CPU in one pass: every pod not in phase Succeeded was Ready, both Jobs completed, a chat completion returned HTTP 200, and no Xeon-only instruction or label was needed [ledger:H1b] | n=1, shared host under background load, k3s v1.37.1. Reachability only, no speed |
| H18 | `k3d-gpu sandbox up` kept sandbox pods on an E-core (CPU 24) behind default-deny egress. RFC1918 and tailscale targets were refused, PyPI was reachable, 3/3 runs [ledger:H18] | kube-router on k3s v1.37.1 |
| H30 | Under matched background load, the `k3d-gpu up` + `agent up` defaults (OVMS int4 on the 16 P-cores) decoded Qwen3-4B at 10.96 tok/s against Ollama 0.40.5 defaults at 7.05 tok/s (Q4_K_M, all CPUs), 3/3 rounds [ledger:H30] | One host, `typical` load, n = 12 requests per arm, 128 tokens. Configuration on the same CPU, not a GPU |
| H34 | Same-engine OVMS Fast Draft (0.6B int4 draft on the P-cores) beat the 4B int4 target alone under matched `typical` background load on a busy box: 1.39x decode tok/s and 0.75x package J/token, B better in 12/12 interleaved pairs [ledger:H34] | OVMS 2026.4.1 serial Fast Draft, gamma 5, single stream. The `heavy` level was load-unmatched (INCONCLUSIVE) |

### REFUTED (all but H27 changed a default)

| Row | What was refuted | Effect on the product |
|---|---|---|
| H2 | The UHD 770 beats the P-cores for a 4B model. It lost decode, prefill, 4-in-flight and energy [ledger:H2] | `auto` takes the iGPU only on a `gpu.intel.com/xmx=true` node |
| H3 | P/E pinning isolates inference from noisy neighbours. Decode held, prefill did not, and a DRAM-bandwidth neighbour was not isolated at all [ledger:H3] | Docs say "placement", never "isolation" |
| H4 | The old `auto` rule (8385dc2) picks the best device. It lost to the forced 16-P-CPU arm [ledger:H4] | Rule replaced by the XMX gate. The new rule was written after seeing the data, so H4 is re-run against it |
| H12, H13 | A Helm release named like EAT's adopts the baked balloons policy without a force flag. It never changed the policy spec [ledger:H12] [ledger:H13] | The two H14 install paths |
| H17 | DRA can be the default Intel GPU path with gpu-v0.12.1 [ledger:H17] | DRA stays opt-in |
| H27 | An on-box precision probe beats the static int4 default on agent-turn latency. Under matched background load, int4 failed the probe's quality gate, so the probe and the reference both picked int8, and int8 is slower than int4 [ledger:H27] | None: int4 stays the static default, and `autoquant` is a quality gate on this host, not a speed win |

### INCONCLUSIVE (findings, not claims; numbers live in the ledger)

| Row | Finding | Why not decided |
|---|---|---|
| H1 | Intel EAT `release-1.0.3-hotfix` returned a chat completion and a tool call on this 24-core client CPU, with no Xeon-only instruction needed [ledger:H1] | The model step ran twice on one cluster, `kubectl wait --all -A` was not run, and the host was shared. Confidence 0.7 that a clean pass holds |
| H7 | With both engines on 16-bit weights, the OVMS/vLLM-CPU decode ratio was about 1.16, against about 3.4 at mismatched precision (H2's int4 vs bf16). Most of the decode gap is weight precision. The prefill gap stayed large [ledger:H7] | 3+2 blocks instead of 5 ABBA rounds; block ratios straddle the pre-registered line; no energy arm |
| H8 | One 4B agent on the P-cores (OVMS int4) sits at the 10 s p95 SLO, with CIs on both sides. EAT's vLLM CPU defaults were OOMKilled at their 16Gi limit on a ~3.1-3.5k-token trace in 2/2 runs. The RTX arm is NVIDIA and is not read as an Intel result [ledger:H8] | Two of four arms have no scored step |
| H9 | Cold `quickstart` reached a working Open WebUI chat inside its budget [ledger:H9] | n=1, warm arm not run |
| H22 | The iGPU draft lost to the P-core draft in 16/16 paired rounds. Separately, a same-engine P-core draft beat the target alone in 8/8 rounds in both runs; that matches Intel's published Lunar Lake CPU-draft ratio [provenance:P222] [ledger:H22] | The noise guardrail fired in both runs: the probe's own 16-CPU load throttles the package. The same-engine draft was tested on its own as H34 (SUPPORTED) |
| H20, H21, H23, H24, H25, H29 | Energy and phase-steering mechanisms [ledger:H20] [ledger:H21] [ledger:H23] [ledger:H24] [ledger:H25] [ledger:H29] | Idle package power spread swamped the signal, or the run was partial |
| H31, H32 | The `k3d-gpu up` + `agent up` defaults (CPU, 16 P-cores) decoded faster than llama.cpp b11706 and plain OVMS 2026.4.1 defaults in 3/3 rounds each [ledger:H31] [ledger:H32] | Background load unmatched across arms (throttle events, and 1-min load for H32), likely carry-over from the competitors' all-CPU segments; plain OVMS's device was not logged |
| H26 | E-core L2-cluster confinement of a DRAM-bandwidth neighbour: the measured R (p95 inflation, cluster over spread) was 0.83, CI 0.61-0.87, at `typical` background load, which is not a verdict [ledger:H26] | Background load unmatched across arms at both levels (hog carry-over into the load windows; the `heavy` load was removed mid-run) |

PENDING with a pre-registration: H5 (prefill guard on/off with an E-core neighbour), H19 (noise guard tuning), H28 (image-volume model sharing), and the agent benchmark H40-H48 ([agent-benchmarks.md](../agent-benchmarks.md): real EAT blueprint flows and BFCL, graded, on a busy box). The external bake-offs are in the tables above: only the Ollama one is decided, so llama.cpp and plain OVMS carry no "faster than" claim.

## Per-family support matrix

"Configured" means k3d-gpu detects the family and generates its configuration. Golden fixture tests check this ([platforms.md](../platforms.md), `tests/platforms_doc_test.sh`). "Measured" means a ledger row exists for that family.

| Family | Configured (fixture) | What `auto` does | Measured |
|---|---|---|---|
| Raptor Lake-S, UHD 770 (+ NVIDIA) | yes | P/E balloons; shared iGPU; agent work on P-cores (no XMX) | yes, the only measured family [ledger:H6] [ledger:H15] [ledger:H18] |
| Raptor Lake-S, stale GuC | yes | `plan` refuses the iGPU and prints the firmware fix | no |
| Alder Lake-N (N100) | yes | shared iGPU; no balloons (not hybrid) | no |
| Meteor Lake, Arrow Lake-S/U (no XMX) | yes | P/E balloons; iGPU not picked by the XMX gate | no; prediction carried from H6 |
| Arrow Lake-H, Lunar Lake, Panther Lake, Wildcat Lake (XMX) | yes | iGPU eligible for `auto`; NPU plugin; IAA on Panther Lake | no; H6 does not cover XMX parts |
| NPU (37xx, 40xx, 50xx) | yes | NPU plugin; doctor names the firmware file; `auto` never picks the NPU | no |
| Nova Lake NPU | yes | no NPU plugin; doctor says no 60xx firmware is published | no |
| Arc B580, Pro B60/B70, A770; Flex 140; Max 1550 | yes | exclusive dGPU plugin; doctor gives kernel or DKMS guidance | no |
| Xeon SPR, Max (HBM flat/cache/devdax), GNR, GNR-D, SRF, DMR (simulated) | yes | AMX and NUMA labels; NUMA-replica balloons; DSA, IAA and QAT plugins | no |
| Gaudi 3 | yes | Habana device plugin; with no driver, `plan` refuses the HPU and prints the fix | no |

## Limitations

1. **One host.** Every measurement is one i9-13900T. No result transfers to another family without its own row.
2. **Shared, throttling host.** The host is live and shared, and the package throttles under the probes' own load. Most INCONCLUSIVE verdicts come from this. H22 throttles itself, so it is barred from this host.
3. **The XMX half of the device rule is untested.** H6 supports "stay on the P-cores when the iGPU has no XMX". Whether the iGPU wins when it has XMX is an open prediction.
4. **Small n.** Most SUPPORTED rows are n=3 per arm, so they show direction and correctness, not effect-size precision.
5. **EAT end to end is one run.** H1b deployed and served the full EAT stack once on this host. H1 stays INCONCLUSIVE: its wait could not pass on k3s v1.37. Nothing is known for other client CPUs.
6. **Power envelope.** The host is a 35 W T-SKU by rating only. Firmware sets PL1 = PL2 = 117 W, so every result here, H22's throttle included, is at that envelope. No result transfers to a T-SKU held at Intel's 35 W / 106 W limits.

## Reproduce

Each ledger row's run section ends with its own reproduce block, giving the image build, cluster name, probe and analysis commands. The entry points:

```bash
git clone https://github.com/88plug/k3d-gpu && cd k3d-gpu
scripts/k3d-gpu doctor && scripts/k3d-gpu plan     # what the host is detected as
tests/claims_check.sh                              # README.md + docs/*.md + docs/intel/*.md
python3 tests/ledger_drift_check.py                # doc verdicts agree with EXPERIMENTS.md
# One SUPPORTED row end to end (H18, the sandbox):
IMAGE=k3d-gpu:local; docker build -t "$IMAGE" . && IMAGE=$IMAGE OUT=$(mktemp -d) bench/sandbox_round.sh 1
```

## Related

| If you want to… | Go to |
|---|---|
| Read every row, run log and reproduce block | [EXPERIMENTS.md](../../EXPERIMENTS.md) |
| See what each family is detected as | [platforms.md](../platforms.md) |
| Check novelty and prior art | [provenance.md](../provenance.md) |
| See the upstream contribution draft | [upstream-eat.md](upstream-eat.md) |
