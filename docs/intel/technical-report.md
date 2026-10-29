# k3d-gpu on Intel hardware: technical report

Status: working report, 2026-10-21. Ledger: [EXPERIMENTS.md](../../EXPERIMENTS.md), verdicts as of H28x amendment R1, 2026-10-29 (74 rows).

## Abstract

k3d-gpu turns one Linux host into a single-node k3s cluster (k3d) that detects the host's Intel hardware and configures it. It sets NRI balloons for hybrid P/E and NUMA CPUs, device plugins for iGPU, dGPU, NPU, DSA, IAA, QAT and Gaudi, and placement labels for agent and inference pods.

This report covers what was measured, and how. Every result but H52, H25x, H27x, H34x, H54a and H54d (a Xeon 6741P host) comes from one host: an i9-13900T (Raptor Lake-S, 8 P-cores and 16 E-cores, UHD 770 iGPU with no XMX, AVX-VNNI, no AMX, no NPU). It is a T-SKU rated at 35 W base power, but its firmware runs PL1 = PL2 = 117 W, so it does not run the T-series 35 W envelope.

- Seventeen hypotheses are SUPPORTED. Sixteen are scoped to that host; H25x is H25 replicated on the Xeon 6741P. H57dm (the Gemma 4 QAT MTP drafter speeds llama.cpp decode on the P-cores) is the newest.
- Fourteen are REFUTED. Six of them changed shipped defaults.
- 24 are INCONCLUSIVE, mostly because the shared host throttles or its load was unmatched. H54a and H54d (Gemma 4 26B-A4B QAT on the Xeon 6741P) are INCONCLUSIVE because EAT's stock vLLM CPU arm stalled in both of H54a's holds, so no arm could be scored against it. H57da (k3d-gpu's OVMS pick vs llama.cpp for Gemma 4 QAT on the i9-13900T) did not resolve: the strictly pinned llama.cpp arms tracked host load; H57db did not run (memory). H28x (H28 replicated on the Xeon 6741P) is INCONCLUSIVE: its one pre-registered hold failed in setup and measured nothing; one re-run is registered.
- 19 are PENDING: the Xeon 6741P rows H50, H50o, H51, H52b, H53, H54q, H54b, H54c and the zero-touch thesis row H58, H55 (SLO-gated admission of an E-core neighbour on the i9-13900T), H56 (two holds at once in cpuset slices of the Xeon 6741P), and the second client host's (wildct, i7-13700) rows H52w, H18w, H25w, H28w, H34w, H57wa, H57wm and H57wb, pre-registered and not yet run. H52, the first row run on that host (a Xeon 6741P with four Arc Pro B70s), is REFUTED. H27x, H27 re-run on the Xeon (AMX), is REFUTED as on the i9. H34x, H34 re-run there, is REFUTED: the CPU Fast Draft that sped up the i9 slowed decode on the Xeon.
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

### SUPPORTED (quotable, scoped to the i9-13900T; H25x to the Xeon 6741P)

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
| H19 | `k3d-gpu tune run --budget 4` did not crown noise under matched `typical` load: it kept the default (decision `incumbent`), and its only screened neighbour (`CORES=efficient`) failed the gates in a 3+3 replication (ratio 1.32, CI 0.73-1.87, p = 0.35) [ledger:H19] | Derived confidence 0.25. Screened 1 of 10 neighbours, so it shows no false crown, not that the default is best or that tuning beats it |
| H34 | Same-engine OVMS Fast Draft (0.6B int4 draft on the P-cores) beat the 4B int4 target alone under matched `typical` background load on a busy box: 1.39x decode tok/s and 0.75x package J/token, B better in 12/12 interleaved pairs [ledger:H34] | OVMS 2026.4.1 serial Fast Draft, gamma 5, single stream. The `heavy` level was load-unmatched (INCONCLUSIVE). On a Xeon 6741P the same draft slowed decode (H34x, REFUTED) |
| H31 | Under matched background load, the `k3d-gpu up` + `agent up` defaults (OVMS int4 on the 16 P-cores with CPU Fast Draft) decoded Qwen3-4B at 15.60 tok/s against llama.cpp b11706 defaults at 10.31 tok/s (Q4_K_M, all CPUs), 3/3 rounds [ledger:H31] | One host, `typical` load, n = 12 requests per arm, 128 tokens. Configuration on the same CPU, not a GPU |
| H32 | Under matched background load (amendment 27), the `k3d-gpu up` + `agent up` defaults (OVMS int4 on the 16 P-cores with CPU Fast Draft) decoded Qwen3-4B at 16.15 tok/s against plain OVMS 2026.4.1 defaults at 10.14 tok/s (same int4 weights; plain OVMS picked the iGPU), 3/3 rounds [ledger:H32] | One host, `typical` load, n = 12 requests per arm, 128 tokens. Compares two device choices, so it says nothing about OVMS on the same device |
| H25 | With OVMS's shipped prefix caching on, an added agent that sends the same 1538-token system prompt and tools got its first chunk in 1.02 s against 10.87 s with caching off: 10.61x, CI 10.36-10.74, 9/9 round pairs ≥ 4x, under matched `typical` background load (amendment 27) [ledger:H25] | One host, Qwen3-4B int4 on the 16 P-cores, one prefix shape; `heavy` load and memory per agent not measured. Measures a shipped OVMS feature; k3d-gpu enables nothing |
| H25x | H25 replicated on a Xeon 6741P (48 CPUs, AMX): with prefix caching on, an added agent's first chunk arrived 7.05x sooner than with it off (0.245 s vs 1.724 s, CI 6.94-7.13) under matched `typical` load [ledger:H25x] | One prefix shape; memory per agent not measured. Smaller than the i9's 10.6x because the Xeon's uncached prefill is faster |
| H28 | Eight pods that mmap one 1 GiB model through an image volume shared one page-cache copy: PSS 132.5 MiB per pod and 1.00x the model size on the node, against 1028.5 MiB and 9.00x for pods that copy it into an emptyDir; they were ready in 2980 ms vs 6333 ms (0.47x, CI 0.45-0.55, complete separation), under matched `typical` background load (amendment 27) [ledger:H28] | One host, overlayfs, N = 8, a 1 GiB file read page by page, CPU-resident weights. The time part clears 0.5x by a small margin |
| H40 | The agent harness reproduces the BFCL board: bf16 Qwen3-4B-Instruct-2507 on vLLM CUDA scored 88.0% on the fixed 100-id subset in BFCL's own mode (board-weighted 84.9%, band 77.9-91.9%), every category inside its binomial interval [ledger:H40] | A check of the harness, not of the hardware; one 100-id subset, one run, on the NVIDIA dGPU |

### REFUTED (all but H5, H27, H27x, H34x, H43, H45 and H48 changed a default)

| Row | What was refuted | Effect on the product |
|---|---|---|
| H2 | The UHD 770 beats the P-cores for a 4B model. It lost decode, prefill, 4-in-flight and energy [ledger:H2] | `auto` takes the iGPU only on a `gpu.intel.com/xmx=true` node |
| H3 | P/E pinning isolates inference from noisy neighbours. Decode held, prefill did not, and a DRAM-bandwidth neighbour was not isolated at all [ledger:H3] | Docs say "placement", never "isolation" |
| H5 | The opt-in prefill guard (an E-core `scaling_max_freq` cap) halves the P-core prefill penalty from an E-core neighbour. No cap level did, even the deepest, and package power stayed near its limit with the cap in force [ledger:H5] | None: the guard stays opt-in and off by default, and `doctor --perf` never suggests it unprompted |
| H4 | The old `auto` rule (8385dc2) picks the best device. It lost to the forced 16-P-CPU arm [ledger:H4] | Rule replaced by the XMX gate. The new rule was written after seeing the data, so H4 is re-run against it |
| H12, H13 | A Helm release named like EAT's adopts the baked balloons policy without a force flag. It never changed the policy spec [ledger:H12] [ledger:H13] | The two H14 install paths |
| H17 | DRA can be the default Intel GPU path with gpu-v0.12.1 [ledger:H17] | DRA stays opt-in |
| H27 | An on-box precision probe beats the static int4 default on agent-turn latency. Under matched background load, int4 failed the probe's quality gate, so the probe and the reference both picked int8, and int8 is slower than int4 [ledger:H27] | None: int4 stays the static default, and `autoquant` is a quality gate on this host, not a speed win |
| H27x | H27 on a Xeon 6741P with AMX, same weights and probe. int4 failed the quality gate again; the probe and the reference both picked fp16 (bf16 on AMX, which decoded faster than int8 here), and fp16's turn was 1.24 s slower than int4's [ledger:H27x] | None: int4 stays the static default on an AMX Xeon too |
| H34x | H34 on a Xeon 6741P: same-engine OVMS CPU Fast Draft (0.6B draft) beats the 4B target alone. Under matched `typical` load it was slower and costlier in 12/12 pairs: B/A decode 0.758x (CI 0.688-0.809), J/token 1.24x [ledger:H34x] | None yet: the CPU Fast Draft default of `agent up` rests on H34 (i9); scoping it by host is a separate change |
| H43 | Heavy E-core load keeps the CPU stack's time per C12 coding task (Intel's coding agent, its sandbox on the E-cores) within the pre-registered bound. In four short blocks it finished one task at both levels: 66.9 s at `typical`, 122.7 s at `heavy`. One task decides this verdict [ledger:H43] | None: sandbox placement is unchanged |
| H45 | The CPU stack (OVMS int4, P-cores) serves 1-4 graded banking agents per box at `typical` load. It failed at one agent: turn p95 26.1 s against the 10 s SLO, and 31 requests against the 60 needed. The RTX 4000 AWQ stack held 8 agents, and Ollama failed at one [ledger:H45] | None: no default rests on agents per box |
| H48 | The agent graders were sound as first run. A remote GLM-5.3 arm failed 5 of 12 items, 4 of them because DevUI joined the answer text with spaces; with that fixed, it passed 11 of 12 in a regrade and in a fresh run [ledger:H48] | Harness only: the grader was fixed and regression-tested before any agent verdict was read; no product default |
| H52 | A plain `k3d-gpu up` on a Xeon 6741P with four Arc Pro B70s detects every engine present, with nothing missing or phantom. Every usable engine (the four B70s, AMX, AVX-512) was detected, advertised and reached a pod, and nothing phantom was advertised; but the `xe` cards got no media engine labels, and `up` did not name the unconfigured DSA, IAA and QAT [ledger:H52] | `xe` media engines are now counted from the kernel's DRM usage stats; `up` naming DSA, IAA and QAT is re-tested as H52b |

### INCONCLUSIVE (findings, not claims; numbers live in the ledger)

| Row | Finding | Why not decided |
|---|---|---|
| H1 | Intel EAT `release-1.0.3-hotfix` returned a chat completion and a tool call on this 24-core client CPU, with no Xeon-only instruction needed [ledger:H1] | The model step ran twice on one cluster, `kubectl wait --all -A` was not run, and the host was shared. Confidence 0.7 that a clean pass holds |
| H7 | With both engines on 16-bit weights, the OVMS/vLLM-CPU decode ratio was about 1.16, against about 3.4 at mismatched precision (H2's int4 vs bf16). Most of the decode gap is weight precision. The prefill gap stayed large [ledger:H7] | 3+2 blocks instead of 5 ABBA rounds; block ratios straddle the pre-registered line; no energy arm |
| H8 | One 4B agent on the P-cores (OVMS int4) sits at the 10 s p95 SLO, with CIs on both sides. EAT's vLLM CPU defaults were OOMKilled at their 16Gi limit on a ~3.1-3.5k-token trace in 2/2 runs. The RTX arm is NVIDIA and is not read as an Intel result [ledger:H8] | Two of four arms have no scored step |
| H9 | Cold `quickstart` reached a working Open WebUI chat inside its budget [ledger:H9] | n=1, warm arm not run |
| H22 | The iGPU draft lost to the P-core draft in 16/16 paired rounds. Separately, a same-engine P-core draft beat the target alone in 8/8 rounds in both runs; that matches Intel's published Lunar Lake CPU-draft ratio [provenance:P222] [ledger:H22] | The noise guardrail fired in both runs: the probe's own 16-CPU load throttles the package. The same-engine draft was tested on its own as H34 (SUPPORTED) |
| H20, H21, H23, H24, H29 | Energy and phase-steering mechanisms [ledger:H20] [ledger:H21] [ledger:H23] [ledger:H24] [ledger:H29] | Idle package power spread swamped the signal, or the run was partial |
| H26 | E-core L2-cluster confinement of a DRAM-bandwidth neighbour: the measured R (p95 inflation, cluster over spread) was 0.83, CI 0.61-0.87, at `typical` background load, which is not a verdict [ledger:H26] | Background load unmatched across arms at both levels (hog carry-over into the load windows; the `heavy` load was removed mid-run) |
| H41, H42, H44, H46 | Agent benchmark on Intel's EAT banking flow (B24) and BFCL S100 at `typical` load. The k3d-gpu CPU stack (OVMS int4, P-cores) passed 20/24 banking tasks (task median 15.6 s); the RTX 4000 AWQ stack finished them in 0.12x that time; EAT's stock vLLM CPU bf16 stack finished 1 of 24 in its hold. BFCL S100 in server-fc mode: OVMS int4 passed 81 of 100 ids, bf16 on vLLM CUDA 88 of 100. Heavy E-core load stretched the CPU stack's p95 turn 2.59x (CI 0.87-2.94) [ledger:H41] [ledger:H42] [ledger:H44] [ledger:H46] | Background load unmatched across holds (swap use and throttle on the shared host); each arm ran one block, not ABBA; B1's J per success undefined |
| H47b, H49b | H47 and H49 re-run with pairs matched on idle pre-windows (load rule L2). H47b: on its 4 matched pairs a CPU draft shortened B24 task time (4 pairs: direction only). H49b: on its 3 matched pairs A1 passed 3/3 and EAT's stock vLLM CPU stack 1/3; across all 23 complete pairs, not gated, 19/23 vs 5/23 [ledger:H47b] [ledger:H49b] | Idle pre-windows did not agree within amendment 27's tolerances: 4 of 10 and 3 of 23 pairs matched (throttle/min and load-CPU busy noise) |
| H54a | Gemma 4 26B-A4B QAT single-stream on the Xeon 6741P, engine by engine. At batch 1, llama.cpp CPU on Google's QAT Q4_0 GGUF decoded ahead of k3d-gpu's OVMS arms (the QAT-matched IR and OpenVINO's published IR), and its QAT MTP drafter added a little on top. EAT's stock vLLM CPU arm stalled mid-decode in both holds, cause open (direction only; numbers in the ledger) [ledger:H54a] | The stock arm has no measured phase or score, so the pre-registered ratio and quality gate have no base; five arms were not reached in the 3-hold cap; every optimized number depends on amendment R4's void rule |
| H54d | Not run: H54a chose no arm to put in EAT's banking flow [ledger:H54d] | Its arm A is undefined and its arm B is the arm that stalled |

The external bake-offs are in the tables above: the Ollama, llama.cpp and plain OVMS ones are all decided.

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

1. **One host.** Every measurement but the Xeon 6741P rows named in the abstract is one i9-13900T. No result transfers to another family without its own row. The campaign on that Xeon 6741P (Granite Rapids, AMX) with 4x Arc Pro B70 is in progress; its Gemma 4 microbench row H54a has no scored stock arm yet.
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
