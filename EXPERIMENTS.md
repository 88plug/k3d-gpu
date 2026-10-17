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
| H12 | A Helm release named like Intel EAT's can take over the NRI balloons policy of a k3d-gpu cluster, and P/E placement stays correct. | On a hybrid host, with a node image built from this branch: (a) on a default cluster (`K3D_GPU_BALLOONS=k3s`), `helm upgrade --install nri-resource-policy-balloons` (chart v0.14.0, `-n kube-system`, `-f contrib/eat/hybrid-values.yaml`) exits 0 and adopts the baked objects: release revision 1 `deployed`, the BalloonsPolicy gains `vllm-balloon`, the DaemonSet is Ready, and `k3d-gpu test` passes; a pod with a container named `vllm` gets only P-cores and a pod in namespace `redis` only E-cores. After `.skip` and `docker restart` of the server node the policy still has `vllm-balloon`. (b) with `K3D_GPU_BALLOONS=external`, the server's manifests dir has no `nri-balloons.yaml`, neither CRD `balloonspolicies.config.nri` nor DaemonSet `nri-resource-policy-balloons` exists before Helm, and the same `helm` command then gives the same placement and a passing `k3d-gpu test`. (c) control: on a cluster from an image built from `origin/main` (objects without Helm ownership annotations), the same command fails with "cannot be imported". | Pre-registered 2026-10-17 before the live runs. Image `docker build -t k3d-gpu:r2-beat .` from this branch; control image from `origin/main` fcfdd38. Cluster `k3dgpu-r2-beat`, scratch `KUBECONFIG`. Arms (a) and (b) n=3 each, order a b b a a b, fresh cluster per run; (c) n=1 (it only shows the probe can fail). Placement read from each pod's `Cpus_allowed_list` against `/sys/devices/cpu_core/cpus` and `cpu_atom/cpus`. Outcome -> conclusion: every (a) and (b) run meets every listed check -> SUPPORTED; any helm failure, missing `vllm-balloon`, wrong cpuset, failing `k3d-gpu test`, baked object present under (b), or revert after the `.skip` restart -> REFUTED; (c) succeeding -> the probe cannot tell adoption apart, INCONCLUSIVE. Confounds recorded per run: package W (RAPL, 5 s before), 1-min load, swap used, package throttle count delta. | REFUTED |
| H13 | H12's adoption works when Helm applies client-side, as Helm 3 (EAT pins v3.14.0) always does; with Helm 4, `K3D_GPU_BALLOONS=external` installs with its default server-side apply, and adoption needs `--server-side=false`. | Same checks as H12 (a)/(b) (exit 0, revision 1 `deployed`, `vllm-balloon` present, DaemonSet Ready, `k3d-gpu test` passes, `vllm` container on P-cores, `redis` namespace on E-cores, `vllm-balloon` kept across a `.skip` restart for adoption arms). Arms: A3 = default cluster + Helm v3.14.0; A4 = default cluster + Helm v4.1.1 `--server-side=false`; B4 = external cluster + Helm v4.1.1 defaults; B3 = external cluster + Helm v3.14.0. | Pre-registered 2026-10-17 04:23Z, after H12 run a1 failed, before any H13 run. Same image, cluster name, values and probe script as H12; the Helm binary and the `--server-side` flag are the only changes. Helm v3.14.0 from get.helm.sh, sha256 f43e1c33…6651 checked against its .sha256sum. n=3 per arm, order A3 A4 B4 B3, B3 B4 A4 A3, A3 A4 B4 B3, fresh cluster per run. Outcome -> conclusion: every run of an arm meets every check -> that arm SUPPORTED; H13 SUPPORTED only if all four arms are; any failure in an arm -> REFUTED for that arm, and the README states only the arms that held. Confounds as H12. | REFUTED |
| H14 | Helm installs EAT's chart values over a k3d-gpu cluster in two ways: on a `K3D_GPU_BALLOONS=external` cluster with no extra flag (Helm 3 and 4), and on a default cluster with Helm 3 `--force` or Helm 4 `--force-conflicts`, which replace the adopted BalloonsPolicy spec. | Every run meets H12's checks: helm exit 0, revision 1 `deployed`, BalloonsPolicy has `vllm-balloon` and `agent-control-plane`, DaemonSet Ready, `k3d-gpu test` passes, a `vllm` container gets only P-cores and a pod in namespace `redis` only E-cores; on default clusters `vllm-balloon` survives a `docker restart` of the server after `.skip`. | Pre-registered 2026-10-17 before any H14 run, after H13 runs A3-1 and A4-2 and a 2-cluster spike (log below). Arms: A3f = default cluster, Helm v3.14.0 `--force`; A4f = default cluster, Helm v4.1.1 `--force-conflicts`; B4 = external cluster, Helm v4.1.1 defaults; B3 = external cluster, Helm v3.14.0 defaults. n=3 per arm, order A3f A4f B4 B3 B3 B4 A4f A3f A3f A4f B4 B3, fresh cluster per run, same image, values and probe script as H12. Outcome -> conclusion: all runs of an arm pass every check -> that arm SUPPORTED, and only SUPPORTED arms are documented as working; any failed check -> that arm REFUTED. Confounds as H12. | SUPPORTED |
| H15 | On an iGPU-only Intel host, the `K3D_GPU_INTEL_SHARED=auto` default (8 pods per iGPU) lets two `gpu.intel.com/i915` pods use the UHD 770 at once, at no loss of total throughput against running them one at a time. | (F) On a cluster from this branch's image with the default, node allocatable `gpu.intel.com/i915` = 8, the plugin DaemonSet args are `-shared-dev-num=8 -allocation-policy=balanced` and survive `docker restart` of the server node, and two pods requesting `gpu.intel.com/i915: 1` are both Running with overlapping run intervals and exit 0; a control cluster with `K3D_GPU_INTEL_SHARED=1` advertises 1 and keeps the second pod Pending while the first runs. (T) With a VA-API media workload (1080p H.264 decode, `scale_vaapi` to 720p, `h264_vaapi` encode, 6000 frames per pod, `linuxserver/ffmpeg:version-9.0.2-cli`), the aggregate fps of two concurrent pods is at least 0.9x the median solo fps, each concurrent pod gets at least 0.4x solo, no pod fails and dmesg logs no new i915 GPU hang. | Pre-registered 2026-10-16 PDT (2026-10-17T06:04Z, commit 1350533, pushed) before any H15 run. One shared cluster (default env) and one control cluster, both from `docker build -t $TAG .` on `claude/igpu-sharing-r2`. Timed arms on the shared cluster: S = one pod alone, C = two pods started together on a wall-clock barrier; n=3 per arm, order S C C S S C. Per run: package W (RAPL), load (1 min), swap used, package throttle count delta. Outcome -> conclusion: F holds and every C run meets T -> SUPPORTED, keep `auto` = 8 on iGPU-only hosts; F holds but aggregate < 0.9x solo in 2 or more C runs -> REFUTED, `auto` becomes 1 (sharing opt-in); any failed pod or GPU hang -> REFUTED, `auto` becomes 1; solo spread (max - min) above 20% of its median -> INCONCLUSIVE, rerun. | SUPPORTED |
| H16 | On k3s 1.37, `K3D_GPU_DRA=1` hands the UHD 770 to a pod through the Intel GPU DRA driver (gpu-v0.12.1) and no device plugin. | On a cluster from this branch's image with `K3D_GPU_DRA=1` on an i9-13900T: no `intel-gpu-plugin` DaemonSet, a ResourceSlice of driver `gpu.intel.com` lists the iGPU, node allocatable has no `gpu.intel.com/i915`, `up` reports `intel=1`, and every pod that names a ResourceClaimTemplate for DeviceClass `gpu.intel.com` lists `renderD128` under `/dev/dri` and ends `Succeeded`. | Pre-registered 2026-10-17 (this commit, pushed before the first run) together with H17, same runs: 3 DRA clusters (arm D). Refuted by a plugin DaemonSet, no slice, an i915 extended resource, or any claim pod without `renderD128` or not `Succeeded`. | PENDING |
| H17 | DRA mode can become the default (auto) Intel GPU path when the driver is baked in, without losing what the device-plugin default gives on an iGPU host. | On the same host, all three: (a) two pods that each claim the iGPU through their own ResourceClaim from one template are both `Running` at once, as two `gpu.intel.com/i915: 1` pods are under the plugin's sharing default (H15); (b) median pod latency (create to `Succeeded`, busybox `ls /dev/dri`, image preloaded) in DRA mode is at most 1.2x the plugin's; (c) median `k3d-gpu up` wall time in DRA mode is at most 1.2x the plugin's. | Arms D (`K3D_GPU_DRA=1`) and P (default), one fresh cluster per run, n=3 runs per arm, order D P P D D P; per run 1 timed `up` and 3 timed pods (reported as the run's median), then the 2-pod check read 20 s after creation. Per run: package W over `up` (RAPL), load (1 min), swap used, package throttle count delta. Outcome -> conclusion: (a) fails in any D run -> REFUTED, DRA stays opt-in (`K3D_GPU_DRA=0` default), since auto would take away H15's sharing; (a) holds but (b) or (c) fails -> REFUTED, DRA stays opt-in; all hold -> SUPPORTED, DRA may become auto in a later change; F of H16 fails -> H17 not evaluated (DRA mode broken). A timing arm whose spread (max - min) exceeds 30% of its median -> that part INCONCLUSIVE. Prediction before measuring: REFUTED by (a), because gpu-v0.12.1 publishes no shared counters or multi-allocation (docs/experimental.md). | PENDING |

## Falsification log

Every probe run, kept or not. When an attack on a hypothesis is settled, mark it
DO-NOT-RE-ATTACK with the reason, so the same experiment is not repeated without
new evidence (new hardware, new driver, new model, changed method).

| Date | Hypothesis | Attack | Result | DO-NOT-RE-ATTACK |
|------|------------|--------|--------|------------------|
| 2026-10-16 | H6 | i9-13900T: OVMS 2026.4.1 + Qwen3-4B int4, P-core CPU arm vs UHD 770 arm, 5 rounds (header and numbers in [H6 run](#h6-run-i9-13900t-2026-10-16)) | Held. Decode 15.7 vs 9.9 tok/s (+58%); ~1936-token prompt time to first token 11.2 s vs 29.5 s (2.6x), i.e. prefill 172 vs 66 tok/s. The GPU pod used 1.0 CPU-s/s, the CPU pod 7.9 | DO-NOT-RE-ATTACK on UHD 770 / Raptor Lake with this model and server: re-run on new hardware, driver, model or OVMS release |
| 2026-10-16 | H4 | Same host and data as H6, against the `agent` auto rule of that time (any allocatable Intel GPU, else CPU) | That rule picked the UHD 770, which lost to the P-cores on decode and prefill. Not a verdict for H4, which needs every usable device swept with the H2 method; it changed the rule: `auto` now takes the GPU only on a `gpu.intel.com/xmx=true` node | No: H4 is re-run against the new rule |
| 2026-10-17 | H11 | i9-13900T: `up` with the pinned default vs the same image ID through a floating ref, 3 runs per arm alternating; the added inspects 5 times; a cluster made by main's launcher, then the branch launcher (header and numbers in [H11 run](#h11-run-i9-13900t-2026-10-17)) | Held. Median `up` 36.57 s pinned vs 37.20 s floating (Δ 0.63 s, arm spreads 1.15 s and 2.83 s); added checks median 0.026 s. On main's cluster `doctor` warned "node image cryptoandcoffee/k3d-gpu:latest (unversioned ...)" and `up` stopped with rc 1 before `k3d cluster create`; after recreate the node ran `cryptoandcoffee/k3d-gpu:0.1.21`, label 0.1.21, `doctor` ok | DO-NOT-RE-ATTACK unless `ensure_image`/`node_image` gain more docker calls or k3d changes how it resolves a cached image |
| 2026-10-17 | H12 | i9-13900T: control c1 on an `origin/main` image, then run a1 (default cluster, host Helm v4.1.1, no extra flag) | Control failed as predicted ("cannot be imported ... missing key meta.helm.sh/release-name"). a1 failed: helm rc 1, "conflict occurred while applying object ... conflicts with \"deploy@k3d-k3dgpu-r2-beat-server-0\"" on DaemonSet `.spec.template.spec.nodeSelector` and BalloonsPolicy `.spec.balloonTypes`: Helm 4 installs a new release with server-side apply, and k3s's deploy controller already manages those fields. REFUTED; runs b were not started | DO-NOT-RE-ATTACK with Helm 4 defaults on a default cluster: the conflict is deterministic |
| 2026-10-17 | H13 | Runs A3-1 (Helm v3.14.0) and A4-2 (Helm v4.1.1 `--server-side=false`) on default clusters; batch stopped after A4-2 | Both: helm rc 0, revision 1 `deployed`, DaemonSet relabelled to the release and its nodeSelector changed to `k3d-gpu.io/hybrid-cpu`, `k3d-gpu test` passed (P pod on `0`, E pod on `24`), but the BalloonsPolicy kept the baked types (`replica ... shared`, no `vllm-balloon`), so the `vllm` and `redis` probes both ran on the shared balloon `0-15,17-31`. Helm's client-side update of an adopted object diffs the rendered object against itself, so a custom resource gets an empty merge patch; native kinds get a three-way patch against the live object. REFUTED for A3 and A4 | DO-NOT-RE-ATTACK: adoption without a force flag never changes the BalloonsPolicy spec |
| 2026-10-17 | spike for H14 | One default cluster each: Helm v4.1.1 `--server-side=false --force-replace` on the A4-2 cluster; Helm v3.14.0 `--force`; Helm v4.1.1 `--force-conflicts` | All three: BalloonsPolicy types became `reserved vllm-balloon performance efficient lowpower ecluster pcore-physical latency agent-control-plane shared`. Exploratory, n=1 each; not a verdict | No: H14 measures the force paths with n=3 |
| 2026-10-17 | H14 | i9-13900T: 12 runs, 3 per arm (A3f, A4f, B4, B3) in the pre-registered order (header and per-run table in [H14 run](#h14-run-i9-13900t-2026-10-17)) | Held in 12/12. Every run: helm rc 0, revision 1 `deployed`, `vllm-balloon` and `agent-control-plane` present, DaemonSet 1/1, `k3d-gpu test` rc 0, `vllm` container on `10-11` (P-cores 0-15), `redis` pod on `24-25` (E-cores 16-31). External runs: no `nri-balloons.yaml` in the manifests dir, no CRD, no DaemonSet before Helm. Default runs: restart without `.skip` put the baked types back (6/6), restart with `.skip` kept `vllm-balloon` (6/6) | DO-NOT-RE-ATTACK unless nri-plugins, Helm or k3s's deploy controller changes |
| 2026-10-17 | EAT flow | One default cluster, Helm v3.14.0: `helm upgrade --install` with chart defaults, then again with `contrib/eat/hybrid-values.yaml` (EAT's install_nri.yaml then main.yaml order) | Revision 1 left the baked policy as is. Revision 2 replaced the balloon types with the values' types but kept the baked `agent.podResourceAPI: true` (the values say false): Helm patched only what differs between its two revisions. Exploratory, n=1; backs the docs/intel.md wording, no verdict | No |
| 2026-10-16 | idea: slack-gated fused prefill (`archive/invent-agent-phase-sched`) | EDF turn deadlines plus an adaptive chunk budget in the OVMS/GenAI scheduler; refuter re-ran `bench/phase_sched/phase_sim2.py` instrumented, N=5-7 | Killed. The engine is 98-99% busy at N=5-7, so R = N·W − Z and A@SLO = floor((SLO+Z)/W): reordering cannot shrink per-turn work W. With one SLO for every turn, EDF equals FCFS. A@20 s = 5 for FCFS-256/512/1024 and adaptive+EDF. Prior art: Niyama (arXiv:2503.22562) teaches the whole arrangement; Sarathi chunked prefill | DO-NOT-RE-ATTACK: scheduling order cannot beat the response-time law on a saturated engine. Reopen only with a lever that cuts prefill tokens per turn |
| 2026-10-16 | idea: E-core prefill offload with KV handoff (`archive/invent-agent-phase-sched`) | Refuter re-ran `hyb_sim.py` unchanged at SLO 10, 15, 20 and 30 s | Killed. Decode is ~75% of LLM time per turn (396 prefill vs 96 decode tokens), so a second prefill pool removes the smaller share. 1 → 1 agents at 10 s, 3 → 3 at 15 s, 5 → 5 at 20 s; a gain only at a lax 30 s SLO | DO-NOT-RE-ATTACK at SLO ≤ 20 s on this host: prefill is not the binding constraint once prefix caching is on |
| 2026-10-16 | idea: SLO-capacity sticky session placement across 2 dGPUs + CPU (`archive/invent-hetero-agent-tier`) | Refute of the 1.6x and ≥1.15x-over-weighted-routing claims | Killed. 1.6x is arithmetic: two private-memory engines of ~1.0 and ~0.75 capacity sum to ~1.75x under any sane router. The ≥1.15x claim beat untuned tok/s-weighted routing; join-shortest-queue / least-outstanding-requests (default in llm-d, SGLang router, vLLM production-stack, Dynamo) starves the slow CPU on its own | DO-NOT-RE-ATTACK: use a JSQ/LOR router as the baseline for any heterogeneous-placement claim |
| 2026-10-16 | idea: P/E isolation of dGPU engine host threads (`archive/invent-hetero-agent-tier`) | `bench/ceiling.py` step model for Qwen3-4B int4 on the RTX 4000 SFF | Killed. A decode step is 17.9 ms at B=1, 45.7 ms at B=16, 134.6 ms at B=64, so a 3 ms host overhead t0 is 2-17% of the step. The claimed +51% (43 → 65 agents) needs t0 ≈ 30 ms, which EEVDF's 3 ms base slice on kernel 6.6 does not produce | DO-NOT-RE-ATTACK unless a measured t0 per step exceeds 10 ms on the dGPU host |
| 2026-10-16 | idea: slot-only templated output (`archive/invent-skip-generation`) | Refuter re-ran `bench/skip_generation_sim.py` with a brevity cap (3 seeds, 600 s, roofline mode) | Killed. Against the raw 96-token default, macros give A@10/20 5/17 → 8/21; a brevity prompt with a max_tokens cap of 64 alone gives 10/24, and macros on top of the cap give 8/21 (worse) | DO-NOT-RE-ATTACK: a per-type max_tokens cap is the tuned baseline and macros lose to it |
| 2026-10-16 | idea: M3 per-turn 1.7B/4B cascade (`archive/invent-spec-decoding`) | Box-time accounting with prefix caching on | Killed. Context is append-only and the session ends on a 4B turn, so 4B still prefills every routed token; the cascade adds a 1.7B prefill of the same history and saves only ~0.002 s per tool-call token | DO-NOT-RE-ATTACK while prefix caching is on and the final turn runs on the large model |
| 2026-10-16 | idea: template-stable history + tool-gap KV warming (`archive/invent-prefix-reuse`) | Novelty and capacity check | Killed as an invention. Both halves are already in `research/prefix-reuse/README.md` (§2.2 item 4, §2.3) and in prior art: InferCept (ICML 2024) and Continuum (arXiv:2511.02230) keep KV across tool waits; QwenLM/Qwen3#1826 names the thinking-strip prefix break. Warming gives 0 capacity at saturation | DO-NOT-RE-ATTACK as a mechanism; the template fix stays a hygiene probe inside the prefix-reuse lead |
| 2026-10-16 | idea: engine-orchestrated agent topology (`archive/invent-agent-topology`) | Refuter re-ran the branch's fluid model with an unpinned baseline (8 P + 16×0.55 E), same tuning grid, N=4, PL1 117 W | Killed. BASE pinned all nine roles to the P-cores with E-cores idle. Against the unpinned baseline CPU_SPLIT is 1.01x and SPLIT_P 0.97x with p95 1.09x worse (S1 needs ≥1.20x). The iGPU adds nothing over E-cores; what is left is "LLM on P, rest on E", already on main | DO-NOT-RE-ATTACK: compare topologies against unpinned and main's P/E labels, never all-on-P |
| 2026-10-16 | idea: power-cap-aware placement P/E/iGPU (`archive/invent-power-aware`) | Fair rival: a power-blind mix → placement lookup table, tuned at one ambient, same dead times, seed 7, 12 plants | Killed. The table reaches 1.18x tuned static in S2/S4; controller v2 against it: S2 median 0.977, S4 0.979, S3 0.983, losing or tying in every plant. All of the +15-18% is mix-keyed switching that needs no power model; out-of-envelope plants cut the gain to 1.04x. Prior art: QScale (TCAD 2018), RT-TAS (TECS 2019), DynamoLLM, throttLL'eM, GreenLLM | DO-NOT-RE-ATTACK on a PL1 = PL2 = 117 W host: the cap almost never changes the best placement (S3 tuned/oracle 0.996-0.999) |
| 2026-10-16 | idea: package-power-coupled placement (`archive/frontier-package-power-coupled-placement-treat-p-`) | 12 result JSONs regenerated byte-equal (seeds 11/12, 6 variants, n=300); oracle search over every plan × cap on the true plant | Refuted, confirmed by re-refute. Even the oracle gets drafter +10.8/+11.1%, p95 cut 9.0/9.6%, energy −5.2/−5.8% in the favourable variant, and +0% in base; AC1 (+20% / 10% / ≤0) is unreachable. At the measured PL1 of 117 W the plant never throttles (0/300 draws) | DO-NOT-RE-ATTACK unless a thermal cap ≤ ~65 W is measured to bind on the host |
| 2026-10-16 | idea: DRAM bandwidth as a schedulable UMA resource (`archive/invent-dram-bandwidth-as-a-schedulable-uma-reso`) | Activity-gated static cpu.max baseline (no ITL sensor), oracle-bisected quota, 243-point grid | Rejected. The gated baseline holds victim ≥ 90% on 180/180 contended points, the guard on 50/180; the guard beats it by ≥25% on 0/180. Greedy admission (S2) costs −40% aggregate on the lone UMA node. What remains is "throttle best-effort while the victim is active": Heracles, CPI2, Koordinator CPU Suppress | DO-NOT-RE-ATTACK: the ITL feedback loop adds nothing over an activity gate |
| 2026-10-16 | idea: balloon-truthful runtime sizing NRI plugin (`archive/invent-balloon-truthful-runtime-sizing-nri-inje`) | Own sim (`go run ./cmd/sizer-sim`) plus a downward-API ablation; containerd v1.7.12 NRI update path read | Falsified by its own sim: the "≥2 of 3 runtimes" claim holds on 0-3% of the grid. Balloon resizes go through containerd's internal UpdateContainerResources, which never sends NRI UpdateContainer to other plugins, so cpus.json goes stale like env. `LLAMA_ARG_THREADS` from the downward API (`requests.cpu`) ties the plan on 480/480 points for E and non-SMT P balloons | DO-NOT-RE-ATTACK as an NRI plugin; size threads from the downward API |
| 2026-10-16 | idea: presence-gated operating points (`archive/invent-presence-gated-operating-points-human-at`) | Overhead ablation (req_ms 0-20, block 1-16), tuned throughput-static chunks, relaxed switch-back, truth-tuned away op | Refuted, reproduced. Smooth-static already runs at ~97% of peak (t_tok 3.75 ms), so away-vs-smooth is −1.6% (−0.5% at req_ms 0); claim 1 holds in 0/12 cells, best +13.8% | DO-NOT-RE-ATTACK: the physics leaves no 15% gap to collect |
| 2026-10-16 | idea: E-core tool-result compression for A@SLO (`archive/apb-e-core-tool-result-compression-spend`) | Pre-registered design-stage refutation against head+tail truncation at the same word budget | Refuted at design stage (confidence 0.95): same tokens means the same engine work, so A(keeplines) / A(trunc) is predicted 1.00 ± 0.05 against a 1.6x claim. Prior art: Headroom, LLMLingua-2, RTK. Only the answer-quality question (C4) is a lead | DO-NOT-RE-ATTACK for agents-per-box; the quality probe is in Research leads |
| 2026-10-16 | idea: residency advisor and per-call energy router (`archive/inv-compete-energy-aware-what-if-residency-advisor-a`) | 31 hindsight-tuned policies per cell; grid extended to tail_w 60 W, tail_s 20-30 s, gaps 30 s; exhaustive pin-set × policy oracle | Killed. Best hindsight saving vs best static device 6.0% platform (0/124 cells reach 20%); the exhaustive oracle reaches 25% stall reduction, not 40%. A calibrate-once offline chooser has lower max regret than the per-call ledger router (1.5% vs 13.9% exact calibration). Prior art: DynamoLLM, throttLL'eM, Splitwise | DO-NOT-RE-ATTACK: floor energy during think gaps dominates; pick one device offline |
| 2026-10-16 | idea: receipt-driven energy steering (`archive/frontier-energy-in-the-loop-mcp-per-call-joule-re`) | 1728-draw grid reproduced bit-for-bit; out-of-grid idle ≈ 0 and E/P 0.3-1.0 ablations | Steering refuted under the pre-registered whole-host metric: median −0.2% vs a fair static, 0% of draws pass H3. E-cores never win: E has lower whole-host joules only when E/P < ~0.11 at 12 W idle. It reopens only with incremental (idle ≈ 0) attribution, where p95 rises 1.25x. Prior art: Neuralwatt per-response energy, IPW agent-trace joules | DO-NOT-RE-ATTACK for steering on a single-tenant host; receipts themselves are a lead |
| 2026-10-16 | idea: hybrid-split joulemeter per-pod energy (`archive/inv-science-hybrid-split-joulemeter-per-pod-energy-f`) | Meter-tuning ablation (window, ridge, frequency edges), held-out seeds 100-109 | Headline refuted, reproduced. Pinned pods: hybrid / best SmartWatts ratio is exactly 1.00 in every config, because a pinned pod's own frequency gives away its core type; floating 1.15-1.18. Prior art: SmartWatts/PowerAPI, Kepler, Scaphandre, JouleShare (offline Shapley replay) | DO-NOT-RE-ATTACK for balloon-pinned pods: per-PMU splitting adds nothing over SmartWatts |
| 2026-10-16 | idea (no branch): KV-carrying work stealing across engines | Provenance search | Anticipated by Vesta (arXiv:2610.14686), which picks a per-turn executor for agent sessions trading backlog against KV transfer plus re-prefill | DO-NOT-RE-ATTACK |
| 2026-10-16 | idea (no branch): memory-envelope realisation (quant/context fitted per engine) | Provenance search | Anticipated by the KAITO memory estimator and the Foundry Local vLLM planner (weights + KV/token + overhead → max context → `--max-model-len`) | DO-NOT-RE-ATTACK |
| 2026-10-16 | idea (no branch): trace-replay what-if twin for agent runs | Provenance search | Anticipated by Vidur-Agent (arXiv:2606.01725), AgentServeSim (arXiv:2606.09613) and Daydream (ATC '20) | DO-NOT-RE-ATTACK |
| 2026-10-16 | idea (no branch): 4-bit KV on the CPU plugin with re-solved cache_size | Provenance search | Anticipated by OVMS's own `demos/continuous_batching/long_context` recipe (u4 KV, cache_size for concurrency, prefix caching) | DO-NOT-RE-ATTACK; it is configuration |
| 2026-10-16 | idea (no branch): append-only tool-output dedup with back-references | Provenance search | Anticipated by ContextPilot (arXiv:2511.03475, open code with a llama.cpp backend) | DO-NOT-RE-ATTACK |
| 2026-10-16 | idea (no branch): grammar jump-forward for tool calls in OpenVINO GenAI | Provenance search | Anticipated by openvinotoolkit/openvino.genai PR #4130 (Intel, closed unmerged); value is a reopen-and-measure, not an invention | DO-NOT-RE-ATTACK as invention |
| 2026-10-16 | idea (no branch): explicit KV pool plus host-DRAM KV tier across tool waits | Provenance search | Anticipated by Continuum (arXiv:2511.02230), InferCept, KVFlow and vLLM's offloading connector | DO-NOT-RE-ATTACK |
| 2026-10-16 | idea (no branch): slack-gated fleet lookup drafting | Provenance search | Anticipated by AgentSpec (arXiv:2608.24004, in vLLM), SuffixDecoding and vLLM's suffix method | DO-NOT-RE-ATTACK |
| 2026-10-16 | idea (no branch): pre-registered all-runs hash chain | Provenance search | Anticipated by ClaimReceipt (arXiv:2609.01992) | DO-NOT-RE-ATTACK |
| 2026-10-16 | idea (no branch): iGPU dma-buf frame/tensor bus across pods | Provenance search | Anticipated by Qualcomm IM SDK qtisocketsink/qtisocketsrc (dma-buf fds over a mounted UNIX socket) | DO-NOT-RE-ATTACK |
| 2026-10-16 | idea (no branch): firmware-enforced consumable GPU share via GuC | Provenance search | Anticipated by intel-resource-drivers-for-kubernetes v0.1.0-v0.2.0 (SR-IOV VF per claim with GuC exec_quantum / preempt_timeout), later removed | DO-NOT-RE-ATTACK |
| 2026-10-16 | idea (no branch): clock-locked efficiency lanes (DVFS as placement) | Provenance search | Anticipated by DynamoLLM, GreenLLM, VoltanaLLM, DualScale | DO-NOT-RE-ATTACK |
| 2026-10-16 | idea (no branch): tool-idle IAA compaction into zswap | Provenance search | Anticipated by AgentZip (arXiv:2609.11294) phase-aligned compression with restore prefetch | DO-NOT-RE-ATTACK |
| 2026-10-16 | idea (no branch): freeze-consistent fork of agent sandboxes | Provenance search | Anticipated by E2B `fork({count:N})` and Modal snapshots | DO-NOT-RE-ATTACK |
| 2026-10-17 | H15 | i9-13900T, UHD 770: shared cluster (default env) F checks, 6 timed runs S C C S S C; control cluster `K3D_GPU_INTEL_SHARED=1` (header and per-run table in [H15 run](#h15-run-i9-13900t-2026-10-17)) | Held. F: allocatable 8, args `["-shared-dev-num=8","-allocation-policy=balanced"]` before and after `docker restart`, `k3d-gpu test` rc 0; both pods of every C run Succeeded from the same start second. Solo median 548.4 fps (spread 20.1, 3.7%); two-pod aggregate median 649.6 fps = 1.18x solo (min 1.16x), each pod 0.58-0.59x solo; 0 failed pods, 0 GPU hangs. Control: allocatable 1, second pod Pending (`Insufficient gpu.intel.com/i915`) until the first finished | DO-NOT-RE-ATTACK on UHD 770 / i915 with this media workload; a compute workload (OpenVINO, oneAPI) or an Xe2/Xe3 iGPU is new evidence |

## Research leads

Prototypes and design studies from the 2026-10-16 research waves. Each was
peer-reviewed or refuted in a fresh context; none was measured on hardware, so
none backs a claim. The code lives on the annotated archive tag (`git show
<tag>:<path>`); the four marked *salvaged* are also in [`research/`](research/)
with their tests, which `tests/bench_test.sh` runs. Kills from the same waves are
in the falsification log above as DO-NOT-RE-ATTACK rows.

| Lead | Archive tag | Verdict | Strongest evidence | Next probe |
|------|-------------|---------|--------------------|------------|
| Agents-per-box ceiling model (*salvaged*: `research/agents-per-box-ceiling/`) | `archive/invent-agents-per-box-ceiling` | discovery | DRAM on this host runs at 3600 MT/s (2 DIMMs per channel, dmidecode), a 57.6 GB/s peak. At agent context lengths (3-16k tokens) the step is bound by reading KV, not weights, so batching stops helping. `ceiling.py selftest` 14/14 | P0: `stream_probe.c` 2 s triad on 8 P-cores, then all cores, to replace the assumed DRAM roof |
| Agent turns are prefill-bound | `archive/frontier-sweep-a-agent-serving`, `archive/invent-sweep-c-agent-workload` | discovery (model, conf 0.75) | Decode step 63.7 ms at c=1, 69.4 ms at c=4 (+1.9 ms per stream); prefill ~172 tok/s. A 500-token tool result costs 2.9 s of exclusive compute, so p95 ≤ 10 s fits 0 agents for a 4B model on P-cores; 2 at 20 s. Sourced workload parameters (Continuum, SWE-agent, InferCept) in `docs/research/agent_workload_params.json` on the tag | `bench/agent_trace.py --max-concurrency 1,2,4,8,16`; falsified if p95 at N=8 is under 1.5x p95 at N=1 |
| Prefix (KV) reuse recipe for OVMS (*salvaged*: `research/prefix-reuse/`) | `archive/invent-prefix-reuse` | research | Source-derived: cached blocks count as free and a dynamic pool grows to the sum of live requests, so a static `cache_size` keeps shared prefixes (1.6x-5.8x modelled TTFT gain). Refuter: holds only when clients set `max_tokens` near the reply length; OVMS `performance_tuning.md` already recommends static sizing | P3 arm D on a warm pod (not a fresh one), with and without `max_tokens`; GPU KV precision read from the compiled model (u4 vs i8 under BY_TOKEN) |
| Speculative decoding across engines (*salvaged*: `research/spec-decoding/`) | `archive/invent-spec-decoding` | research | GenAI 2026.4.1 runs draft then target serially under one mutex, so cross-engine drafting gives no overlap. Refuter: OVMS injects `INFERENCE_NUM_THREADS` into the target config and Fast Draft copies it to an iGPU draft, a likely compile failure; β and o are free parameters | Load a `draft_device=GPU` graph in a k3d pod first; pre-register β and o from a dedicated probe before P4 |
| T-series power envelope and run-length rule (*salvaged*: `research/tseries-envelope/`) | `archive/invent-tseries-envelope` | research | This host runs PL1 = PL2 = 117 W with a 56 s window, not the 35 W T-series envelope, and reads 79-91 °C at 73-100 W. Refuter: on this host the τ-derived rule is inert (RAPL never binds), `steady()` passes a 2.5%/half sag and a ±20% sawtooth, and dynamic run counts are prior art (Phoronix DynamicRunCount, ORNL F-t-Pj-RG, Laaber et al., NanoMesh PR #3) | P5: compare the 232 s rule against MLPerf's 600 s minimum on a thermally bound run; add a slope test to `steady()` |
| Shared-prefix (cascade) decode attention | `archive/apb-m3-read-the-shared-system-prompt-and` | prototype, PENDING | The bandwidth framing fails in the cache model (no DRAM saving to collect); a compute lever survives, with a model prior of 1.37x for the fp32 cascade. Kernel `bench/cascade_kernel.c` mirrors OpenVINO 2026.4.0's u8 KV layout. Prior art: ChunkAttention ran it on Xeon; Hydragen, RelayAttention, FlashInfer | K1: time stock vs cascade layer; stop below 1.3x. K2: `cascade_vnni` vs `vnni_gemv` ≥ 1.1x, else port the VNNI q·K kernel without cascade |
| Tool-result compression answer quality | `archive/apb-e-core-tool-result-compression-spend` | prototype | `keeplines` is provably identical to head+tail truncation when a text has no cue, and never exceeds the word budget (tests). The agents-per-box claim is killed above | C4: needle `answer_ok`, keeplines minus trunc ≥ +5 pp with n ≥ 100 per arm |
| MCP per-call joule receipts | `archive/frontier-energy-in-the-loop-mcp-per-call-joule-re` | minor-revision | Receipts survive: wrapper p99 adds 0.054 ms at load 12 (limit 2 ms). MCP 2026-07-28 reserves `traceparent`/`tracestate`/`baggage` in `_meta`, confirmed in the spec. Steering is killed above | Apply `tool-variants.yaml` to a cluster; read a receipt for one CPU and one embed tool call |
| Joule Ledger span energy | `archive/frontier-joule-ledger-node-daemonset-that-splits-` | prototype (sim verdict FAIL: C4, C5, C6) | Freshness bound and point estimate survive ablation: no-freshness median APE 0.392 vs 0.193. Honest edge over a split-aware Kepler is 1.4-1.8x, not 2x; spans shorter than 2 sample periods get lo = 0 | Live: NetworkPolicy enforcement by kube-router (unlabelled pod times out, labelled pod gets 200), `$(POD_IP)` expansion, token Secret mount |
| Placement twin for `k3d-gpu.io/cores` | `archive/inv-science-placement-twin-per-pod-p-e-factor-e-clus` | prototype | E/P factor and contention predictors survive in simulation; the E-module packing term and the SLO placement solver are falsified, and the simple IO rule beats the twin on placement. `perf stat` parser tested on a real i9-13900T capture | Per-image E/P factor from `perf stat` on P and E balloons for 3 real images; drop the solver |
| Xeon promotion gate (twin replay + projection certificate) | `archive/inv-compete-xeon-promotion-gate-twin-snapshot-balloo` | major-revision | Replay finds 8/8 planted hazards on both Xeon twins. The certificate equals "regenerate dev from prod and diff" (identical verdicts), and the unplanted ORDER_SENSITIVE finding needs `allocatorTopologyBalancing`. Replay misses limits-only containers | Apply Kubernetes request defaulting before replay; re-score C3 against regenerate-and-diff |
| Xeon SLO oracle | `archive/inv-compete-trace-transfer-slo-oracle-predict-xeon-a` | major-revision | Grid reproduced bit-identical. A card-free batched-replay baseline beats the oracle on dense models (1B 14.8 vs 17.7, 8B 7.3 vs 10.8 p95 MAPE); the only edge is the MoE expert-union term, and its truth world uses the same uniform routing. Prior art: Vidur, LLMServingSim | Re-pre-register MoE with Zipf-skewed routing (s = 0.8, 1.2) |
| Numerics contracts resolver | `archive/inv-intelstar-numerics-contracts-realisations-carry-me` | major-revision | Bug: a record with `kl_mean` = NaN is placed on the `exact` contract (fail-open); missing or string fields crash the resolver. A 2x optimistic golden set erases the gain over the format label; an evidence-optional hybrid gets most of it at one third of the rejects | Fix fail-closed parsing; measure golden-set KL vs agent/tool-call KL for one realisation |
| Per-engine counter sampler | `archive/inv-intelstar-per-engine-counter-sampler-proves-where-` | major-revision | 8/14 criteria held. The competent self-report baseline was a strawman: llama.cpp logs `offloaded %d/%d layers to GPU`, and a cpuset vs `/sys/devices/cpu_atom/cpus` check catches a wrong cores label. Partial offload at f = 0.8-0.95 is missed 99% | Add both baselines; widen offload and landing priors; re-score A1 |
| Interference witness for pinned pods | `archive/inv-science-interference-witness-for-pinned-pods-cor` | major-revision | A no-perf baseline weighting P-residency by `/proc/stat` busy ties the witness everywhere simulated (0.999 vs 0.998). The only niche is a co-tenant sharing a mixed P/E cpuset (0.678 vs 0.577). Prior art: Heracles, CPI2 | Simulate balloon escape and the shared mixed-cpuset co-tenant; keep `perf -G` only if it wins there |
| Limiter-stratified decode measurement | `archive/inv-science-limiter-stratified-measurement-per-decod` | major-revision | Paired ABBA with 60-120 s warm-up and no idle gaps already gives P-vs-E bias −0.52 pt; D4 (35 W cap) + warm-up + no gaps is best at −0.12 pt, coverage 0.90. The 14.5 pt headline compared against a cold, gapped baseline | P1/P2 on hardware: warm-up and no-gap ABBA at stock settings vs D4 |
| Execution-path witness | `archive/inv-science-execution-path-witness-kernel-accounted-` | major-revision | PARTIAL detection averages ~88.6% over seeds 4-11, below the 90% falsifier. A held-out tuned rule ("device-holding processes above 1.1 cores in any 100 ms interval") scores 100% / 0% false flags; the sim separates classes by construction (spin ≤ 1 core vs fallback ≥ 1.7) | Real OpenVINO/SYCL spin-wait CPU on clean iGPU runs, to set the threshold from data |
| Counter-driven core placement | `archive/invent-counter-driven-core-placement-per-pod-pm` | major-revision | A rate-only placer (Bias Scheduling / PMCSched lineage) matches or beats auto on M3 (0.72-0.73 vs 0.71). The M3 win over the static oracle rests on a 5 s move cost; at 15 s auto falls to unpinned | Measure pod restart plus model reload time for llama.cpp and OVMS |

Superseded, not a lead: `archive/experimental-features` (the `K3D_GPU_EXPERIMENTAL` framework) landed in substance as #37.


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

### H14 run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T04:31Z..04:58Z (pre-registered: H12 04:16:38Z, H13 04:22:10Z, H14 04:31:09Z, each committed before its first run)
hypothesis:      H14 (H12 and H13 runs c1, a1, A3-1, A4-2 at 04:17Z..04:28Z)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores)
host kernel:     6.6.128-1-MANJARO
accelerators:    nvidia=2 (RTX 4000 SFF Ada, RTX 2000E Ada), intel=1 (UHD 770), hybrid
k3d-gpu commit:  runs 1-12: branch claude/balloons-eat-compat-r2 on base fcfdd38 (balloons code, baked
                 manifest and values as merged); runs 13-14 rebased on b314467 (same image ID);
                 runs 15-16 rebased on 14adb7e (#49 changed the image: sha256:60235a7a4281);
                 runs 17-18 rebased on 62a6442 (#51 render pipeline: sha256:038ab24ee423)
node image:      sha256:93ae446b82c1, docker build -t k3d-gpu:r2-beat . from this branch
                 control: sha256:6e0d7baa2786, built from origin/main fcfdd38
model:           none
runtime:         k3d v5.9.0, k3s v1.37.1+k3s1, nri-resource-policy-balloons chart v0.14.0;
                 Helm v4.1.1 (host) and v3.14.0 (get.helm.sh, sha256 f43e1c33...6651 = its .sha256sum)
energy source:   /sys/class/powercap/intel-rapl:0 (package-0), confounds only
runs per arm:    3 (order A3f A4f B4 B3 B3 B4 A4f A3f A3f A4f B4 B3)
```

| Run | Arm | package W | load (1 min) | throttle delta | helm rc | `vllm-balloon` | test rc | `vllm` CPUs | `redis` CPUs | restart: no `.skip` / `.skip` |
|-----|-----|-----------|--------------|----------------|---------|----------------|---------|-------------|--------------|-------------------------------|
| 1 | A3f | 95.5 | 12.89 | 202 | 0 | yes | 0 | 10-11 | 24-25 | reverted / kept |
| 2 | A4f | 80.9 | 8.72 | 358 | 0 | yes | 0 | 10-11 | 24-25 | reverted / kept |
| 3 | B4 | 80.0 | 8.76 | 359 | 0 | yes | 0 | 10-11 | 24-25 | - |
| 4 | B3 | 80.2 | 8.14 | 80 | 0 | yes | 0 | 10-11 | 24-25 | - |
| 5 | B3 | 80.0 | 10.34 | 28 | 0 | yes | 0 | 10-11 | 24-25 | - |
| 6 | B4 | 81.2 | 7.88 | 158 | 0 | yes | 0 | 10-11 | 24-25 | - |
| 7 | A4f | 77.2 | 7.84 | 718 | 0 | yes | 0 | 10-11 | 24-25 | reverted / kept |
| 8 | A3f | 80.7 | 6.07 | 131 | 0 | yes | 0 | 10-11 | 24-25 | reverted / kept |
| 9 | A3f | 79.7 | 7.36 | 3091 | 0 | yes | 0 | 10-11 | 24-25 | reverted / kept |
| 10 | A4f | 85.2 | 5.10 | 1286 | 0 | yes | 0 | 10-11 | 24-25 | reverted / kept |
| 11 | B4 | 75.4 | 4.65 | 741 | 0 | yes | 0 | 10-11 | 24-25 | - |
| 12 | B3 | 79.3 | 4.39 | 237 | 0 | yes | 0 | 10-11 | 24-25 | - |

Package W is the 5 s before each run: median 80.1, range 75.4-95.5. Load
4.39-12.89, swap used 105 GiB throughout, 52-53 other containers, package
throttle count +7,389 over the 12 runs. None of these can turn a Helm exit code,
a policy's type list or a cpuset into a pass, so they do not bear on the
verdict. Every run's `k3d-gpu test` put its performance pod on CPU `0` and its
efficient pod on `24`. Confirmation runs after each rebase, A4f then B3 with the
rebased launcher and a rebuilt image, matched every check, restart rows
included: runs 13-14 (05:08Z-05:14Z), runs 15-16 (05:24Z-05:30Z, the image
from 14adb7e) and runs 17-18 (05:45Z-05:51Z, the manifest now rendered by
`hack/render-component` with `HELM_ADOPTABLE=yes`, body byte-identical).
Verdict: SUPPORTED,
confidence high for the four tested
paths on this host; H12 (Helm 4 defaults on a default cluster) and H13 (no force
flag on a default cluster) stay REFUTED.

```bash
# Reproduce (from the repo root of this commit; scratch kubeconfig, named cluster).
export KUBECONFIG=$(mktemp) K3D_GPU_CLUSTER=k3dgpu-r2-beat K3D_GPU_IMAGE=k3d-gpu:r2-beat
docker build -t "$K3D_GPU_IMAGE" .
chart() { helm upgrade --install nri-resource-policy-balloons nri-resource-policy-balloons \
  --repo https://containers.github.io/nri-plugins --version v0.14.0 -n kube-system \
  -f contrib/eat/hybrid-values.yaml "$@"; }
# A3f / A4f: default cluster, adopt with a force flag
scripts/k3d-gpu up
chart --force-conflicts                      # Helm 4; with Helm 3: chart --force
kubectl -n kube-system get balloonspolicy default -o jsonpath='{.spec.balloonTypes[*].name}'
scripts/k3d-gpu test
docker exec "k3d-$K3D_GPU_CLUSTER-server-0" touch /var/lib/rancher/k3s/server/manifests/nri-balloons.yaml.skip
docker restart "k3d-$K3D_GPU_CLUSTER-server-0"   # vllm-balloon stays; without .skip the baked types return
scripts/k3d-gpu down
# B4 / B3: external cluster, plain install
K3D_GPU_BALLOONS=external scripts/k3d-gpu up
docker exec "k3d-$K3D_GPU_CLUSTER-server-0" ls /var/lib/rancher/k3s/server/manifests   # no nri-balloons.yaml
chart && scripts/k3d-gpu test
kubectl run eat-vllm --image=busybox:1.37 --restart=Never --overrides='{"spec":{"containers":[{"name":"vllm","image":"busybox:1.37","command":["grep","Cpus_allowed_list","/proc/self/status"],"resources":{"requests":{"cpu":"2"},"limits":{"cpu":"2"}}}]}}'
scripts/k3d-gpu down
docker image rm "$K3D_GPU_IMAGE"
```

### H15 run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T06:05Z..06:17Z (pre-registered 06:04:45Z, commit 1350533, pushed before the first run)
hypothesis:      H15
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores)
host kernel:     6.6.128-1-MANJARO
accelerators:    nvidia=2 (RTX 4000 SFF Ada, RTX 2000E Ada), intel=1 (UHD 770, 0xa780, i915, PCI 0000:00:02.0), hybrid
k3d-gpu commit:  1350533 (claude/igpu-sharing-r2 on origin/main 765ad5f; after the run the branch was
                 rebased onto 76bb003, which changed only packaging, as e3ab698)
node image:      sha256:038ab24ee423, docker build -t k3d-gpu-int:igpu-share-r2 . from this branch
                 (k3s v1.37.1+k3s1, intel/intel-gpu-plugin:0.37.1)
model:           none; workload = work.sh below
runtime:         linuxserver/ffmpeg@sha256:742e17516dd4d7b76b939d774bd08f0f6b7cab8bce1c014b95ab2fb992fd0a1f
                 (version-9.0.2-cli), VA-API iHD on /dev/dri/renderD128 from the plugin
energy source:   /sys/class/powercap/intel-rapl:0 (package-0), confounds only
runs per arm:    3 (order S C C S S C), one cluster, 10 s idle between runs
```

Workload per pod: encode a 300-frame 1080p `testsrc2` clip with libx264 (untimed),
wait for a shared wall-clock start second, then time
`ffmpeg -hwaccel vaapi -hwaccel_output_format vaapi -stream_loop 19 -i in.mp4 -vf scale_vaapi=w=1280:h=720 -c:v h264_vaapi -f null -`
(6000 frames: GPU decode, scale and encode). Pods request `gpu.intel.com/i915: 1`
and nothing else.

```sh
# work.sh, mounted from a ConfigMap; env START_AT (epoch s), LOOPS=19
# GPU media workload for H15: VA-API decode + scale + H.264 encode of a 1080p clip.
# START_AT (epoch seconds) lines up concurrent pods; prints one RESULT line.
set -e
DEV=${DEV:-/dev/dri/renderD128}
[ -e "${DEV}" ] || DEV=$(ls /dev/dri/renderD* | head -1)
ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=1920x1080:rate=30 \
    -frames:v 300 -c:v libx264 -preset ultrafast -y /tmp/in.mp4
if [ -n "${START_AT:-}" ]; then
    while [ "$(date +%s)" -lt "${START_AT}" ]; do sleep 0.1; done
fi
t0=$(date +%s.%N)
ffmpeg -hide_banner -nostats -loglevel error -init_hw_device "vaapi=va:${DEV}" \
    -hwaccel vaapi -hwaccel_device va -hwaccel_output_format vaapi \
    -stream_loop "${LOOPS:-5}" -i /tmp/in.mp4 -vf scale_vaapi=w=1280:h=720 \
    -c:v h264_vaapi -f null -
t1=$(date +%s.%N)
frames=$((300 * (${LOOPS:-5} + 1)))
awk -v a="${t0}" -v b="${t1}" -v f="${frames}" -v d="${DEV}" \
    'BEGIN { printf "RESULT dev=%s frames=%d start=%.3f end=%.3f secs=%.3f fps=%.1f\n", d, f, a, b, b - a, f / (b - a) }'
```

| Run | Arm | Pod fps | Aggregate fps (frames / first start to last end) | package W | load (1 min) | swap used MiB | throttle delta | new GPU hangs |
|-----|-----|---------|------------------------------------------------|-----------|--------------|---------------|----------------|---------------|
| 1 | S | 548.4 | 548.4 | 70.4 | 3.92 | 108131 | 23816 | 0 |
| 2 | C | 325.0 / 325.9 | 649.9 | 72.4 | 3.16 | 108131 | 4832 | 0 |
| 3 | C | 324.8 / 324.8 | 649.6 | 72.8 | 3.45 | 108107 | 7784 | 0 |
| 4 | S | 551.0 | 551.0 | 78.2 | 3.31 | 108106 | 13870 | 0 |
| 5 | S | 530.9 | 530.9 | 77.8 | 2.91 | 108106 | 46976 | 0 |
| 6 | C | 317.5 / 317.5 | 634.9 | 76.4 | 2.98 | 108106 | 25666 | 0 |

| Arm | median | p90 (n=3: max) | spread (max - min) |
|-----|--------|----------------|--------------------|
| S fps | 548.4 | 551.0 | 20.1 (3.7%) |
| C aggregate fps | 649.6 | 649.9 | 15.0 (2.3%) |
| C aggregate / S median | 1.18x | 1.19x | min 1.16x |
| C per pod / S median | 0.59x | 0.59x | min 0.58x |

Package W is RAPL package-0 over the timed window (CPU cores and iGPU, the 52
other containers on the host included), throttle delta sums
`package_throttle_count` over all CPUs. Both stayed within the host's idle band
(about 71-80 W) and showed no pattern by arm, and swap use did not change.

Control (`K3D_GPU_INTEL_SHARED=1`, cluster made from the same image): allocatable
`gpu.intel.com/i915` 1, no plugin args; 15 s after both pods were created the first
was Running and the second Pending with `0/1 nodes are available: 1 Insufficient
gpu.intel.com/i915`; it started once the first finished. The two jobs took 24.9 s
end to end (482 fps for 12000 frames, pod start gap included) against 18.5 s when
shared.

Extra live checks on the same image: a shared cluster made with
`K3D_GPU_INTEL_MONITORING=1` set the args `-shared-dev-num=8
-allocation-policy=balanced -enable-monitoring`, advertised `gpu.intel.com/i915` 8
and `gpu.intel.com/monitoring` 1, and `k3d-gpu test` ran both the i915 and the
monitoring test pod (each listed `card0` and `renderD128`) with rc 0. Two
long-running pods on the shared cluster were both `Running` at once with node
allocated `gpu.intel.com/i915` 2 of 8.

Verdict: SUPPORTED, confidence high for this host and workload (every run met
every prediction; the smallest aggregate margin, 1.16x against the 0.9x
threshold, is about 7 times the solo spread). `K3D_GPU_INTEL_SHARED=auto` stays 8 on
iGPU-only hosts.

```bash
# Reproduce on an iGPU-only (or iGPU + NVIDIA) host, from this branch.
TAG=k3d-gpu-int:igpu-share-r2 FF=linuxserver/ffmpeg:version-9.0.2-cli
docker build -t "$TAG" . && docker pull "$FF"
export KUBECONFIG=$(mktemp) K3D_GPU_IMAGE=$TAG K3D_GPU_CLUSTER=h15
scripts/k3d-gpu up && k3d image import "$FF" -c h15
kubectl get node -o jsonpath='{.items[0].status.allocatable.gpu\.intel\.com/i915}'   # 8
docker restart k3d-h15-server-0   # args and the count survive
# Run work.sh (above) from a ConfigMap in 1 or 2 pods with limits gpu.intel.com/i915: 1 and a
# shared START_AT, order S C C S S C; compare the RESULT fps lines.
scripts/k3d-gpu down
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
- **H14** covers the i9-13900T (P-cores 0-15, E-cores 16-31), chart v0.14.0,
  Helm v3.14.0 and v4.1.1, and `contrib/eat/hybrid-values.yaml`. EAT itself was
  not deployed: a `vllm`-named busybox container and a pod in namespace `redis`
  stand in for its vLLM and agent services.
- **H15** covers one UHD 770 (i915, no XMX) and a VA-API media workload, which
  spreads over the video engines and the EUs. A compute workload that saturates the
  EUs alone (OpenVINO, oneAPI) may gain nothing from sharing, and nothing here
  measures memory pressure between pods. It shows that concurrent pods work and
  lose no total throughput; it does not show isolation.
