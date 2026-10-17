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
| H1 | EAT (Intel Enterprise Agent Toolkit) runs on a single non-Xeon machine. | On one host whose CPU is not a Xeon (Core or Core Ultra), `k3d-gpu up` followed by the EAT deploy from `claude/eat-on-k3d` reaches all EAT pods `Ready` and one inference request returns a completion. | Fresh host, Reproduce header filled in. `k3d-gpu up`, deploy EAT, `kubectl wait --for=condition=Ready pod --all -A --timeout=30m`, then one completion request against the EAT endpoint. Refuted by any pod not Ready at the timeout, a Xeon-only instruction or node-label requirement, or no completion. Wave 1 (2026-10-16, i9-13900T): a completion returned, but the probe was not run as written (see [H1 run](#h1-run-i9-13900t-2026-10-16)). | INCONCLUSIVE |
| H2 | On an Intel hybrid host whose only Intel GPU is an iGPU (wave 1: i9-13900T, UHD 770), the iGPU serves a fixed model faster and with less energy per token than the CPU running the same model with a tuned thread count. | iGPU decode tok/s is higher than the best CPU tok/s, and iGPU J/token is lower than the CPU J/token at that best thread count. | Row restated 2026-10-16 to the pre-registration that ran (written ~10:20 PDT, before the 10:29-11:32 PDT campaign); the row first committed at 04:48 PDT named a Core Ultra host, which was not tested. OVMS 2026.4.1 with `OpenVINO/Qwen3-4B-int4-ov`, same digest and graph except the device. CPU baseline swept over 4 thread and core configs first, best kept. Energy from RAPL `package-0`, with a 20 s model-loaded idle before each block. Decode 30 x 256, prefill ~1936 x 16, 4-in-flight 30 x 128; 5 rounds, ABBA order. Refuted if the iGPU loses on either tok/s or J/token. | REFUTED |
| H3 | P/E pinning isolates inference from noisy neighbours on a hybrid CPU. | With the inference pod labelled `k3d-gpu.io/cores: performance` and a CPU-saturating neighbour labelled `k3d-gpu.io/cores: efficient`, inference latency stays within 10% of the no-neighbour baseline on both decode and prefill. Without pinning, the same neighbour raises latency by more than 10% on at least one workload. | Pre-registered 2026-10-16 ~13:20 PDT, before any timed block (13:24-14:53 PDT). OVMS on CPU, Qwen3-4B int4, 15-CPU request; neighbour `bench/noisy.sh` (stress-ng `--cpu 15`, Guaranteed 15 CPU). Arms: Q pinned and quiet, P pinned with the neighbour on the E-cores, U both unlabelled, plus Qu, Qe, X (roles swapped) and PM (stream neighbour). `Cpus_allowed_list` read in every block. 5 rounds x 7 arms, 5 requests per workload per block. Refuted if P is more than 10% above Q on either workload. | REFUTED |
| H4 | The automatic device choice equals the measured best device. | For a fixed model on a host with more than one usable device (CPU, iGPU, NPU, dGPU), the device the agent stack selects automatically (`claude/agent-stack`) is the one with the highest measured tok/s in a sweep over every usable device. | Record the auto-selected device. Then force each usable device in turn and measure tok/s with the H2 method. Refuted if any forced device beats the auto choice by more than the run-to-run spread. Wave 1 pre-registered 2026-10-16 15:30 PDT (H4b) and 16:22 PDT (H4c), each before its runs, against the `auto` rule of `claude/agent-stack` 8385dc2. | REFUTED |
| H6 | On a host whose Intel iGPU has no XMX matrix engines, OVMS on the P-cores serves a 4B int4 LLM faster than OVMS on the iGPU. | On an i9-13900T (UHD 770), OVMS 2026.4.1 with `OpenVINO/Qwen3-4B-int4-ov` on the 16-CPU P-core balloon decodes at least 20% faster than the same server on the iGPU, and the iGPU's time to first token on a ~1.9k-token prompt is at least 2x the CPU's. | Pre-registered 2026-10-16 ~10:20 PDT, before the main campaign (10:29-11:32 PDT). Same server digest, model and graph except the device: GPU arm = the agent GPU manifest (2-CPU P-core balloon), CPU arm = the agent CPU manifest (16-CPU P-core balloon). Greedy, streamed, `ignore_eos`. Decode: 30-token prompt x 256 out; prefill: unique ~1936-token prompt x 16 out; 5 requests per block, 5 rounds, arm order alternating. Refuted if either margin is missed. | SUPPORTED |
| H11 | Pinning the node image to the launcher's release costs `k3d-gpu up` no measurable time, and a cluster made from another image is reported. | (a) On a host where the image is cached, `k3d-gpu up` with the pinned default reaches "Accelerators schedulable" within the run-to-run spread (max - min) of the same image named through a floating `:latest` ref (`K3D_GPU_IMAGE`); (b) the checks `up` adds before cluster create (`node_image`, `ensure_image` on a cached image) take a median under 0.5 s; (c) on a cluster created by the previous launcher (default `:latest`), `doctor` warns that it runs an unversioned image and `up` stops before `k3d cluster create`; after recreate, the server node runs `<repo>:<VERSION>` with label `org.opencontainers.image.version=<VERSION>` and `doctor` says ok. | Pre-registered 2026-10-17 03:45Z, before the timed runs. Image built from the branch with `docker build --label org.opencontainers.image.version=<VERSION> -t <repo>:<VERSION>`, the same image ID also tagged `k3dgpu-r2-us:latest` for the floating arm, so only the launcher path differs. (a) n=3 per arm, arms alternating, wall time of `up` to its final line, cluster deleted between runs. (b) n=5 timings of the two docker inspects `up` adds. Outcome -> conclusion: (a) \|Δmedian\| <= max arm spread -> SUPPORTED for (a), else REFUTED; (b) median >= 0.5 s -> REFUTED; (c) any missing warn, a `k3d cluster create` on an existing cluster, or another node image -> REFUTED. | SUPPORTED |
| H12 | A Helm release named like Intel EAT's can take over the NRI balloons policy of a k3d-gpu cluster, and P/E placement stays correct. | On a hybrid host, with a node image built from this branch: (a) on a default cluster (`K3D_GPU_BALLOONS=k3s`), `helm upgrade --install nri-resource-policy-balloons` (chart v0.14.0, `-n kube-system`, `-f contrib/eat/hybrid-values.yaml`) exits 0 and adopts the baked objects: release revision 1 `deployed`, the BalloonsPolicy gains `vllm-balloon`, the DaemonSet is Ready, and `k3d-gpu test` passes; a pod with a container named `vllm` gets only P-cores and a pod in namespace `redis` only E-cores. After `.skip` and `docker restart` of the server node the policy still has `vllm-balloon`. (b) with `K3D_GPU_BALLOONS=external`, the server's manifests dir has no `nri-balloons.yaml`, neither CRD `balloonspolicies.config.nri` nor DaemonSet `nri-resource-policy-balloons` exists before Helm, and the same `helm` command then gives the same placement and a passing `k3d-gpu test`. (c) control: on a cluster from an image built from `origin/main` (objects without Helm ownership annotations), the same command fails with "cannot be imported". | Pre-registered 2026-10-17 before the live runs. Image `docker build -t k3d-gpu:r2-beat .` from this branch; control image from `origin/main` fcfdd38. Cluster `k3dgpu-r2-beat`, scratch `KUBECONFIG`. Arms (a) and (b) n=3 each, order a b b a a b, fresh cluster per run; (c) n=1 (it only shows the probe can fail). Placement read from each pod's `Cpus_allowed_list` against `/sys/devices/cpu_core/cpus` and `cpu_atom/cpus`. Outcome -> conclusion: every (a) and (b) run meets every listed check -> SUPPORTED; any helm failure, missing `vllm-balloon`, wrong cpuset, failing `k3d-gpu test`, baked object present under (b), or revert after the `.skip` restart -> REFUTED; (c) succeeding -> the probe cannot tell adoption apart, INCONCLUSIVE. Confounds recorded per run: package W (RAPL, 5 s before), 1-min load, swap used, package throttle count delta. | REFUTED |
| H13 | H12's adoption works when Helm applies client-side, as Helm 3 (EAT pins v3.14.0) always does; with Helm 4, `K3D_GPU_BALLOONS=external` installs with its default server-side apply, and adoption needs `--server-side=false`. | Same checks as H12 (a)/(b) (exit 0, revision 1 `deployed`, `vllm-balloon` present, DaemonSet Ready, `k3d-gpu test` passes, `vllm` container on P-cores, `redis` namespace on E-cores, `vllm-balloon` kept across a `.skip` restart for adoption arms). Arms: A3 = default cluster + Helm v3.14.0; A4 = default cluster + Helm v4.1.1 `--server-side=false`; B4 = external cluster + Helm v4.1.1 defaults; B3 = external cluster + Helm v3.14.0. | Pre-registered 2026-10-17 04:23Z, after H12 run a1 failed, before any H13 run. Same image, cluster name, values and probe script as H12; the Helm binary and the `--server-side` flag are the only changes. Helm v3.14.0 from get.helm.sh, sha256 f43e1c33…6651 checked against its .sha256sum. n=3 per arm, order A3 A4 B4 B3, B3 B4 A4 A3, A3 A4 B4 B3, fresh cluster per run. Outcome -> conclusion: every run of an arm meets every check -> that arm SUPPORTED; H13 SUPPORTED only if all four arms are; any failure in an arm -> REFUTED for that arm, and the README states only the arms that held. Confounds as H12. | REFUTED |
| H14 | Helm installs EAT's chart values over a k3d-gpu cluster in two ways: on a `K3D_GPU_BALLOONS=external` cluster with no extra flag (Helm 3 and 4), and on a default cluster with Helm 3 `--force` or Helm 4 `--force-conflicts`, which replace the adopted BalloonsPolicy spec. | Every run meets H12's checks: helm exit 0, revision 1 `deployed`, BalloonsPolicy has `vllm-balloon` and `agent-control-plane`, DaemonSet Ready, `k3d-gpu test` passes, a `vllm` container gets only P-cores and a pod in namespace `redis` only E-cores; on default clusters `vllm-balloon` survives a `docker restart` of the server after `.skip`. | Pre-registered 2026-10-17 before any H14 run, after H13 runs A3-1 and A4-2 and a 2-cluster spike (log below). Arms: A3f = default cluster, Helm v3.14.0 `--force`; A4f = default cluster, Helm v4.1.1 `--force-conflicts`; B4 = external cluster, Helm v4.1.1 defaults; B3 = external cluster, Helm v3.14.0 defaults. n=3 per arm, order A3f A4f B4 B3 B3 B4 A4f A3f A3f A4f B4 B3, fresh cluster per run, same image, values and probe script as H12. Outcome -> conclusion: all runs of an arm pass every check -> that arm SUPPORTED, and only SUPPORTED arms are documented as working; any failed check -> that arm REFUTED. Confounds as H12. | SUPPORTED |
| H15 | On an iGPU-only Intel host, the `K3D_GPU_INTEL_SHARED=auto` default (8 pods per iGPU) lets two `gpu.intel.com/i915` pods use the UHD 770 at once, at no loss of total throughput against running them one at a time. | (F) On a cluster from this branch's image with the default, node allocatable `gpu.intel.com/i915` = 8, the plugin DaemonSet args are `-shared-dev-num=8 -allocation-policy=balanced` and survive `docker restart` of the server node, and two pods requesting `gpu.intel.com/i915: 1` are both Running with overlapping run intervals and exit 0; a control cluster with `K3D_GPU_INTEL_SHARED=1` advertises 1 and keeps the second pod Pending while the first runs. (T) With a VA-API media workload (1080p H.264 decode, `scale_vaapi` to 720p, `h264_vaapi` encode, 6000 frames per pod, `linuxserver/ffmpeg:version-9.0.2-cli`), the aggregate fps of two concurrent pods is at least 0.9x the median solo fps, each concurrent pod gets at least 0.4x solo, no pod fails and dmesg logs no new i915 GPU hang. | Pre-registered 2026-10-16 PDT (2026-10-17T06:04Z, commit 1350533, pushed) before any H15 run. One shared cluster (default env) and one control cluster, both from `docker build -t $TAG .` on `claude/igpu-sharing-r2`. Timed arms on the shared cluster: S = one pod alone, C = two pods started together on a wall-clock barrier; n=3 per arm, order S C C S S C. Per run: package W (RAPL), load (1 min), swap used, package throttle count delta. Outcome -> conclusion: F holds and every C run meets T -> SUPPORTED, keep `auto` = 8 on iGPU-only hosts; F holds but aggregate < 0.9x solo in 2 or more C runs -> REFUTED, `auto` becomes 1 (sharing opt-in); any failed pod or GPU hang -> REFUTED, `auto` becomes 1; solo spread (max - min) above 20% of its median -> INCONCLUSIVE, rerun. | SUPPORTED |
| H16 | On k3s 1.37, `K3D_GPU_DRA=1` hands the UHD 770 to a pod through the Intel GPU DRA driver (gpu-v0.12.1) and no device plugin. | On a cluster from this branch's image with `K3D_GPU_DRA=1` on an i9-13900T: no `intel-gpu-plugin` DaemonSet, a ResourceSlice of driver `gpu.intel.com` lists the iGPU, node allocatable has no `gpu.intel.com/i915`, `up` reports `intel=1`, and every pod that names a ResourceClaimTemplate for DeviceClass `gpu.intel.com` lists `renderD128` under `/dev/dri` and ends `Succeeded`. | Pre-registered 2026-10-17 (this commit, pushed before the first run) together with H17, same runs: 3 DRA clusters (arm D). Refuted by a plugin DaemonSet, no slice, an i915 extended resource, or any claim pod without `renderD128` or not `Succeeded`. | SUPPORTED |
| H17 | DRA mode can become the default (auto) Intel GPU path when the driver is baked in, without losing what the device-plugin default gives on an iGPU host. | On the same host, all three: (a) two pods that each claim the iGPU through their own ResourceClaim from one template are both `Running` at once, as two `gpu.intel.com/i915: 1` pods are under the plugin's sharing default (H15); (b) median pod latency (create to `Succeeded`, busybox `ls /dev/dri`, image preloaded) in DRA mode is at most 1.2x the plugin's; (c) median `k3d-gpu up` wall time in DRA mode is at most 1.2x the plugin's. | Arms D (`K3D_GPU_DRA=1`) and P (default), one fresh cluster per run, n=3 runs per arm, order D P P D D P; per run 1 timed `up` and 3 timed pods (reported as the run's median), then the 2-pod check read 20 s after creation. Per run: package W over `up` (RAPL), load (1 min), swap used, package throttle count delta. Outcome -> conclusion: (a) fails in any D run -> REFUTED, DRA stays opt-in (`K3D_GPU_DRA=0` default), since auto would take away H15's sharing; (a) holds but (b) or (c) fails -> REFUTED, DRA stays opt-in; all hold -> SUPPORTED, DRA may become auto in a later change; F of H16 fails -> H17 not evaluated (DRA mode broken). A timing arm whose spread (max - min) exceeds 30% of its median -> that part INCONCLUSIVE. Prediction before measuring: REFUTED by (a), because gpu-v0.12.1 publishes no shared counters or multi-allocation (docs/experimental.md). | REFUTED |
| H18 | `k3d-gpu sandbox up` keeps agent sandboxes on the E-cores and inside a default-deny egress policy on k3d (kube-router), while public egress (PyPI) still works. | On an i9-13900T with tailscale (host `100.65.179.24`, sshd on `0.0.0.0:22`) and a cluster from this branch's image: (a) `sandbox up` exits 0 with the warm pool `readyReplicas` >= 1 and every sandbox pod's `Cpus_allowed_list` inside the E-cores (cpu_atom); (b) from a sandbox pod, TCP to `100.65.179.24:22` (CGNAT, tailscale) and to `192.168.1.108:22` (RFC1918, host LAN) both fail to connect within 3 s, and the policy's kube-router drop-log rule counts the packets, while from a control pod (same image, namespace `sbx-control`, no policy) both connect; (c) from a sandbox pod, DNS resolves `pypi.org`, TCP to `pypi.org:443` connects, and `pip install --user tomli_w==1.2.0` (not in the image) with the profile's `HOME=/tmp` exits 0 and imports from `/tmp/.local`. | Pre-registered 2026-10-17 before any H18 run (this commit, pushed). Image `docker build -t $TAG .` from `claude/agent-sandbox-ecores` rebased on origin/main 2b45f87. Cluster `k3dgpu-r2-sbx`, scratch `KUBECONFIG`, one fresh cluster per run, n=3 runs. Per run: timed `sandbox up` (wall, s), then the probes interleaved sandbox/control, each probe once per pod. Confounds per run: package W (RAPL, 5 s before), 1-min load, swap used, package throttle count delta. Outcome -> conclusion: every run meets (a), (b) and (c) -> SUPPORTED; a sandbox pod off the E-cores, a sandbox reaching either private address, or a control pod failing to connect (probe cannot see a block) -> REFUTED (control failure -> INCONCLUSIVE); pip failing while `pypi.org:443` connects -> REFUTED for (c), README must drop the pip claim. Prediction: SUPPORTED. Amended before the counted runs, after pilot run 0 (log below): kube-router REJECTs (`icmp-port-unreachable`, so `Connection refused` in ~0.1 s) rather than dropping, so (b) asks for no connect plus the drop-log counter instead of a timeout; `six` ships in the image, so (c) installs `tomli_w`. Runner: `bench/sandbox_round.sh`. | SUPPORTED |
| H20 | Phase-steer: on a hybrid Intel CPU, running each agent's prefill on the P-cores and its decode on the E-cores lowers energy per token at the same p95. | On an i9-13900T (P 0-15, E 16-31), with N=6 closed-loop agents, one llama-server per agent (`-np 1`), Qwen3-4B Q4_K_M GGUF, and a turn of 396 new prompt tokens on a cached 1024-token prefix plus 96 decode tokens: arm PS (`--cpu-mask` = E, `--cpu-mask-batch` = P, the `k3d-gpu phase-steer` output) reaches a package J/token (baseline-subtracted) of at most 0.85x arm SP (both masks = P, the product's P-core default), AND its p95 turn latency is at most SP p95 + SP spread (max - min across reps). | Pre-registered 2026-10-17T08:40Z, before any H20 run. This tests the oracle, not an NRI plugin. llama.cpp's in-process mask split [provenance:P201] is the zero-migration upper bound of any NRI phase-steer: an NRI cpuset applies to the whole container, so a plugin adds cgroup moves and phase-detection lag. Prior art covers the mechanism [provenance:P200] [provenance:P202] [provenance:P203] [provenance:P204], so there is no "first" claim. Arms, all at `-t 4 -tb 4` with only the masks differing: SP (baseline); PS (treatment); ALL (both masks 0-31, kernel ITMT placement, the competent baseline); NEG (decode P, prefill E, n=1; must show the worst median TTFT, or the probe cannot tell placement apart). n=3 reps per primary arm in Latin-square order (SP PS ALL / PS ALL SP / ALL SP PS), then NEG. Fresh servers per cell; 15 s warm, 60 s measured, then drain to idle. Probe `bench/phase-steer/probe.py`, which caps itself at 20 min wall. Outcome table and confounds in [H20 pre-registration](#h20-pre-registration-phase-steer-oracle-2026-10-17). Prior P(SUPPORTED) = 0.15. | INCONCLUSIVE ([run](#h20-run-i9-13900t-2026-10-17)) |
| H21 | Joule-aware packing (joule-pack): per-pod energy from RAPL (package, core, uncore) split by cgroup CPU time drives P/E balloon placement that beats main's static `efficient` agent label on agents-per-watt under a p95 turn SLO. | (a) Among placements U (unlabelled), P, E, X (`ecluster`) for N=12 closed-loop synthetic agents (busybox 1.37 sha256 turn sized to 0.25 s on an E-core, 1 s think, 333m each), the best SLO-meeting placement (p95 ≤ 1.0 s) has agents-per-watt (N / marginal package W) ≥ 1.15x arm E in every paired round; (b) in arm M (6 P + 6 E pods) the P pods' cgroup CPU-time share is within ±0.10 of their core-domain energy share from arms P and E. Prediction before measuring: REFUTED on both: best/E 1.00-1.08 (best = E or X), best/U ≥ 1.3, CPU-share error ≤ -0.25 (E overcharged). Prior art (not claimed): CPU-time-proportional pod RAPL split is Kepler's model; joule-aware QoS placement on heterogeneous cores is Octopus-Man/Hipster ([provenance:P211] [provenance:P212] [provenance:P213]); search log [provenance:P210]. | Pre-registered 2026-10-17T12:13Z before any H21 run. One fresh cluster `k3dgpu-i-joule-pack`, scratch KUBECONFIG, inside the shared live lock, ≤ 19 min. Calibration pod (not counted) sizes the turn. 3 rounds, order U P E X M / M X E P U / E U M P X, 12 s idle window before every arm, 8 s warm-up + 32 s measured per arm. RAPL zones found by name (package-0, core, uncore, psys) sampled 1 s; uncore on client parts is PP1 (mostly iGPU), so it is a negative control and the non-core residual is package − core; marginal W = arm minus the mean of the bracketing idle windows; cpusets audited from `/proc/self/status` against `/sys/devices/cpu_core/cpus` and `cpu_atom/cpus`. Confounds every 5 s: package throttle count, max thermal-zone temp, load1, swap used, container count. Outcome -> conclusion: (a) ≥ 1.15x in 3/3 rounds -> SUPPORTED, build the controller and test it against a static `ecluster` rule; < 1.15x in at least 2 rounds -> REFUTED, no controller; best/U ≥ 1.3 in 3/3 -> new H for an `efficient` agent default (label, not meter). (b) \|err\| ≤ 0.10 in 3/3 with M core within 25% of (P+E)/2 -> SUPPORTED; err ≤ -0.15 in 3/3 -> REFUTED, attribution must weight by core type; additivity gap > 25% -> INCONCLUSIVE. Uncore marginal \|Δ\| > 0.5 W, P marginal < idle spread, or idle spread > 50% of median E marginal -> INCONCLUSIVE. n=3 paired rounds (sign test p = 0.125 at 3/3; directional confidence 1 − 0.5^k, ceiling 0.875). Runner: `bench/joule-pack/probe.sh`; analysis: `bench/joule-pack/joulepack.py verdict`. Amendment 2026-10-17, after review and before any H21 run: (a) is also scored on marginal package J per turn (arm W × window / turns), because P/U arms do more turns per window and agents/W alone penalizes them; (a) is SUPPORTED only if both metrics give ≥ 1.15x in 3/3, and REFUTED if E/best J per turn is < 1.15x in ≥ 2 rounds; the ratio uses E's measured value even when E misses the SLO (reported as a flag, never an infinite win); p95 is nearest-rank ceil(0.95 n). Limits stated up front: N is fixed at 12 so the SLO is not expected to bind and admission is not exercised; the workload is pure-ALU, not a real agent; (a) is an oracle headroom gate over static labels, not a test of the controller. | INCONCLUSIVE ([run](#h21-run-i9-13900t-2026-10-17)) |
| H9 | `k3d-gpu quickstart` takes a clean host to a working chat (Open WebUI answering through the agent) within the retail budget. | Time to first agent (quickstart start to its Ready line, with a chat answered through the UI after) is under 5 min when the node, OVMS and Open WebUI images and the model are already cached (warm arm), and under 15 min from nothing cached (cold arm). | Prediction stated in the task brief before the run (2026-10-17); the run itself was started before this row was committed. One fresh cluster `k3dgpu-g-retail`, scratch `KUBECONFIG`, no node image, no in-cluster images and no model PVC (cold arm), hard budget `timeout 900`. End state checked by signing in to Open WebUI with the generated admin and getting a chat reply through `/api/chat/completions`. Outcome -> conclusion: cold arm over 15 min or no reply -> REFUTED; both arms under target with n>=3 each -> SUPPORTED; anything less -> INCONCLUSIVE. Wave 1 ran the cold arm n=1 ([H9 run](#h9-run-i9-13900t-2026-10-17)). | INCONCLUSIVE |
| H23 | Thermal/power headroom published as a per-pod consumable resource (watts) stops a k3d-gpu node from oversubscribing into thermal throttling without losing throughput, and beats a power-blind pod-count cap. | On an i9-13900T (k3s v1.37, PL1 = PL2 = 117 W, thermally bound), 24 fixed-work pods (12 hot: stress-ng `--cpu-method matrixprod`; 12 cool: same plus `--cpu-load 40`): arm T (node advertises `power.k3d-gpu.io/watts` = fitted thermal headroom, pods request their calibrated watts) has (a) package throttle time <= 0.5x arm B (no admission cap), (b) makespan <= B, and (c) makespan or throttle time better than arm C (`k3d-gpu.io/slots` cap = same budget / mean pod watts) by more than the arm spread. Prediction before measuring: REFUTED, T throttle <= 0.5x B but makespan >= 1.10x B, T within C's spread. | Pre-registered 2026-10-17T12:08Z before any H23 run (this commit, pushed before the first run). One cluster `k3dgpu-i-thermal-resource`, scratch KUBECONFIG, in `flock` with a 20 min wall budget. n=3 per arm, order B T C C T B B T C. Fixed work per pod (`--cpu-ops` from in-lock calibration). Per run: makespan, cpu0 `package_throttle_total_time_ms` delta, RAPL package J and W, max package temp, start temp, 1-min load, swap used, per-pod Pending->Running gap. Decision: arm ranges non-overlapping (3v3 permutation p = 0.05) or \|Δmedian\| > max arm spread. Outcome table in [H23 pre-registration](#h23-pre-registration-2026-10-17). Runner: `bench/thermal-resource/probe.sh`, model `research/thermal-resource/headroom_model.py`. Result ([H23 run](#h23-run-i9-13900t-2026-10-17)): the Amendment 1 budget gate fired after calibration (O0), before the cluster: fitted budget 10 W, 2 slots (< 4) at w_hot 5 / w_cool 3 W. Arms B/T/C were not run (0 of 9). The "idle" window drew 72-99 W (median 81.3 W) on the shared host, so background load used the headroom. Missing probe: the 9-run B/T/C series on a host whose idle window is quiet (spread < 5 W) or with a budget gate that clears. | INCONCLUSIVE ([run](#h23-run-i9-13900t-2026-10-17)) |
| H7 | OVMS's decode lead over EAT's vLLM CPU engine in the H2 run comes from weight precision, not from the engine. | On the i9-13900T 16-CPU P-core balloon, one checkpoint served by OVMS 2026.4.1 and by vLLM CPU v0.24.0 at matched weight precision decodes within 15% between the two engines, and the two land within 15% of each other in effective weight bandwidth (weight bytes x decode tok/s). | Pre-registered 2026-10-16, before any run. One checkpoint and revision (`Qwen/Qwen3-4B-Instruct-2507`, revision recorded) for both engines: OVMS loads an OpenVINO IR exported from it with 16-bit weights, vLLM loads it in bf16. A second arm at int4 in both engines runs only if vLLM v0.24.0 CPU is first shown to load an int4 build of the same checkpoint. vLLM runs with EAT's chart defaults and once with `--enforce-eager` dropped, to split engine from config. H2 workloads and energy method, arms interleaved ABBA, 5 rounds. Refuted if the decode gap at matched precision is 15% or more. Run 2026-10-17 at 16 CPUs, 3 OVMS and 2 vLLM blocks, decode gap 15.6% of vLLM's rate (13.5% of OVMS's), on the line ([H7 run](#h7-run-i9-13900t-2026-10-17)). | INCONCLUSIVE |
| H8 | Agents per box (APB: the largest number of closed-loop tool-using agents whose pooled p95 LLM turn latency stays <= 10 s on the `support` agent trace) on the i9-13900T depends on the serving arm as pre-registered in `bench/apb/PROTOCOL.md` section 7. | A1 OVMS int4 on the P-cores: APB in [4, 12] (confidence 0.6). A2 OVMS int4 on the UHD 770: 0 (0.8). A3 EAT's default vLLM CPU bf16: 0 (0.9). A4 vLLM CUDA AWQ int4 on the RTX 4000 SFF Ada: above A1 (0.75). Written 2026-10-16, before any run. | `k3d-gpu capacity --slo 10 --budget 12` per arm on a fresh cluster, one arm per 15-min hold (`bench/apb/arm.sh`). An arm's prediction is refuted by a scored APB outside it; a bracket that still contains the prediction decides nothing. Wave 1 (2026-10-17): A4 >= 32, A1 0 by rule with N=1 on the SLO edge, A2 and A3 unscored ([H8 run](#h8-run-i9-13900t-2026-10-17)). | INCONCLUSIVE |
| H19 | `k3d-gpu tune run --objective throughput` on the CPU agent stack (OVMS 2026.4.1, `OpenVINO/Qwen3-4B-int4-ov`, i9-13900T) does not crown a config whose gain over the default is run-to-run noise. | (a) The tune completes and writes a profile whose decision is either `default`, or `tuned` with a winner that an independent replication confirms. (b) The search's own null control, the explicit restatement of the default (`CPUS=16 CORES=performance MAX_NUM_SEQS=256 CACHE_SIZE=0 PREFIX_CACHING=true`, which the pilot showed writes a `graph.pbtxt` byte-identical to the default's), is never accepted. (c) Replication: the tuned winner, or with decision `default` the finalist with the best confirmation median, is measured again against the default in fresh trials outside the tune. Prediction: decision `default` (single-stream decode is memory-bound and the default already takes all 16 P-core CPUs), and the replication shows no gain beyond the noise gates. | Pre-registered 2026-10-17T09:15Z, before the counted tune, after two pilot trials (default and the explicit default, budget 4 x 64 tokens: 15.3/49.3/48.7 vs 15.3/49.8/46.8 aggregate tok/s at concurrency 1/4/8, identical graphs). Cluster `k3dgpu-r2-tune` from `docker build -t k3d-gpu:r2-tune .`, scratch `KUBECONFIG` and profile dir. Command: `k3d-gpu tune run --objective throughput --devices CPU --precisions int4` with the default criteria (n0=27, eta=3, n=5 confirmation runs per side in rotating order, margin 5%, k=1, alpha 0.05 Bonferroni, TTFT guardrail 25%, quality probe, `--unique-prompts` load). One tune (its confirmation is the n=5 interleaved step). Replication: 5 fresh trials per side, order A B B A A B B A A B (A = default), the tune's final budget (16 requests x 128 tokens at concurrency 1,4,8), same acceptance test (`tune.py` gates). Confounds sampled every 5 s for the whole run: package W (RAPL), 1-min load, swap used, sum of `package_throttle_count`. Outcome -> conclusion: (b) the explicit default accepted -> REFUTED (the gates pass noise); decision `tuned` and the replication fails the gates -> REFUTED (a noise winner was crowned); decision `tuned` and the replication passes -> SUPPORTED (a real winner); decision `default` and the replicated finalist also fails the gates -> SUPPORTED; decision `default` but the replication passes every gate -> INCONCLUSIVE (the tune missed a real gain, a power question, not a noise crown); the tune fails to finish or the default trial fails -> INCONCLUSIVE. | PENDING |
| H30 | The k3d-gpu auto-config (`k3d-gpu up` + `k3d-gpu agent up`) decodes faster than Ollama 0.40.5 on the CPU (`qwen3:4b-q4_K_M`, manifest sha256 2bfd38a7) with vendor defaults, at the same model family and 4-bit tier. | k3d-gpu median decode tok/s is at least 1.05x ollama's in every one of 3 interleaved rounds; package J/token is reported alongside. | Pre-registered 2026-10-17, before any run. `k3d-gpu bench --vs ollama --id H30 --budget 900`: one client, the same 4 prompts, max_tokens 128, temperature 0, `ignore_eos` sent to both arms, 3 warmup requests per arm (inside the budget), rounds A,B / B,A / A,B, one request in flight, RAPL package counter per arm segment. Weights pinned: `bench` hashes the served file inside the competitor container and refuses to measure unless it matches `bench/competitors.lock`. Outcome -> conclusion: k3d-gpu/ollama median decode tok/s at least 1.05 in every round -> SUPPORTED; ollama/k3d-gpu at least 1.05 in every round -> REFUTED; mixed rounds, failed requests, an arm whose median output stops short of 128 tokens, arms counting tokens differently (usage vs stream chunks), or a spent 15-minute budget -> INCONCLUSIVE. Confound recorded per run: the k3d-gpu arm is reached through `kubectl port-forward`, the competitor through docker-proxy, so tunnel latency lands on the k3d-gpu arm only (a bias toward REFUTED). Ollama's OpenAI layer has no `ignore_eos` and streams Qwen3 thinking as `delta.reasoning`, which the client counts as output (openai/openai.go at v0.40.5). Prediction: SUPPORTED where `agent up` picks an Intel GPU, since Ollama 0.40.5 runs this model on the CPU here; REFUTED or INCONCLUSIVE on a CPU-only host. | PENDING |
| H31 | The k3d-gpu auto-config (`k3d-gpu up` + `k3d-gpu agent up`) decodes faster than llama.cpp server b11706 on the CPU (`Qwen/Qwen3-4B-GGUF` Q4_K_M at revision bc640142) with vendor defaults, at the same model family and 4-bit tier. | k3d-gpu median decode tok/s is at least 1.05x llamacpp's in every one of 3 interleaved rounds; package J/token is reported alongside. | Pre-registered 2026-10-17, before any run. `k3d-gpu bench --vs llamacpp --id H31 --budget 900`: one client, the same 4 prompts, max_tokens 128, temperature 0, `ignore_eos` sent to both arms, 3 warmup requests per arm (inside the budget), rounds A,B / B,A / A,B, one request in flight, RAPL package counter per arm segment. Weights pinned: `bench` hashes the served file inside the competitor container and refuses to measure unless it matches `bench/competitors.lock`. Outcome -> conclusion: k3d-gpu/llamacpp median decode tok/s at least 1.05 in every round -> SUPPORTED; llamacpp/k3d-gpu at least 1.05 in every round -> REFUTED; mixed rounds, failed requests, an arm whose median output stops short of 128 tokens, arms counting tokens differently (usage vs stream chunks), or a spent 15-minute budget -> INCONCLUSIVE. Confound recorded per run: the k3d-gpu arm is reached through `kubectl port-forward`, the competitor through docker-proxy, so tunnel latency lands on the k3d-gpu arm only (a bias toward REFUTED). Prediction: SUPPORTED where `agent up` picks an Intel GPU; REFUTED or INCONCLUSIVE on a CPU-only host. | PENDING |
| H32 | The k3d-gpu auto-config (`k3d-gpu up` + `k3d-gpu agent up`) decodes faster than plain OVMS 2026.4.1 with its own device choice (`OpenVINO/Qwen3-4B-int4-ov` at revision b467368) with vendor defaults, at the same model family and 4-bit tier. | k3d-gpu median decode tok/s is at least 1.05x ovms's in every one of 3 interleaved rounds; package J/token is reported alongside. | Pre-registered 2026-10-17, before any run. `k3d-gpu bench --vs ovms --id H32 --budget 900`: one client, the same 4 prompts, max_tokens 128, temperature 0, `ignore_eos` sent to both arms, 3 warmup requests per arm (inside the budget), rounds A,B / B,A / A,B, one request in flight, RAPL package counter per arm segment. Weights pinned: `bench` hashes the served file inside the competitor container and refuses to measure unless it matches `bench/competitors.lock`. Outcome -> conclusion: k3d-gpu/ovms median decode tok/s at least 1.05 in every round -> SUPPORTED; ovms/k3d-gpu at least 1.05 in every round -> REFUTED; mixed rounds, failed requests, an arm whose median output stops short of 128 tokens, arms counting tokens differently (usage vs stream chunks), or a spent 15-minute budget -> INCONCLUSIVE. Confound recorded per run: the k3d-gpu arm is reached through `kubectl port-forward`, the competitor through docker-proxy, so tunnel latency lands on the k3d-gpu arm only (a bias toward REFUTED). The OVMS container gets `/dev/dri` (or `/dev/dxg` plus `/usr/lib/wsl` on WSL2) and the devices passed are recorded; a run where the competitor had no GPU node while the k3d-gpu arm ran on one is reported as such in the run section. Prediction: INCONCLUSIVE: same engine, same weights and the same device on a GPU host, so the gap should sit inside the margin. | PENDING |
| H24 | A spec-only roofline (DRAM channels x MT/s x 8 B; cores x int8 MAC/clk x GHz), calibrated on one 8-P-core point of this host, predicts decode, prefill and 4-in-flight throughput of core configurations it was not fitted on, within 25% MAPE. This is the transfer law `k3d-gpu plan --predict <sku>` relies on. | Held-out arms B (4 P-cores), C (8 E-cores), D (16 E-cores) on the i9-13900T, Qwen3-4B int4, OVMS 2026.4.1 (H6 digest). M0 (`console/sku_model.py arms`) predicts decode 15.7 / 15.7 / 15.7, prefill 86 / 32.3 / 64.5, c4 62.6 / 32.3 / 62.6 tok/s for B / C / D (C compute-bound at c4). Competing M1 (min(spec BW, measured per-cpuset triad BW)) predicts decode = 15.7 x triad(arm) / triad(A). Author prior: M0 decode REFUTED (B ~12.5, C ~8, D ~12), M0 prefill holds, M1 decode holds within 20%. | Pre-registered 2026-10-17T12:15Z, before any H24 run; `tests/console/test_sku_model.py` checks the model's numbers equal these within 1%. Probe `bench/sku-model/probe.py` (self-limits to 19 min). 4 OVMS containers `sci-h24-{A,B,C,D}`, same image digest and weights sha256 as H6, all loaded at once, one served at a time; no k3d cluster. stream_probe triad per arm x2. Visit order A B C D D C B A; per visit: 5 s idle sample, 3 decode (30-token prompt with nonce, 128 out, `ignore_eos`), 2 prefill (unique ~512-token prompt, 16 out), 1 c=4 (4 x 128 out). n per arm: decode 6, prefill 4, c4 2. Calibration = this run's arm A (H6's 15.66 is a sanity check: A within 15%). Outcome -> conclusion: see [H24 pre-registration](#h24-pre-registration). Review fixes in ed20aa4. Result ([H24 run](#h24-run-i9-13900t-2026-10-17)), run 1 at `a19f52c`, 10:47-11:04 PDT: the scorer returned "INCONCLUSIVE (missing cells: partial run)". 6 of 8 visits ran (A once, so no drift sentinel); every TTFT was null because OVMS streams Qwen3 thinking as `reasoning_content`, so decode c=1 and prefill have n = 0; every visit had package throttling (coretemp 95-99 C). Scored c4 cells (M0 calibrated on A, 45.9 tok/s, n=1): B 34.6 (-25%, n=1), C 22.2 (-6%, n=2), D 30.4 (-34%, n=2). Unscored end-to-end proxy (128 / e2e) for decode, relative to A (13.0 tok/s): B 0.78, C 0.57, D 0.71 (M0 predicts 1.0; M1 triad ratio 0.99 / 0.69 / 0.80). Missing probe: re-run at `cce5fd8` (reasoning_content counted, ~512-token prefill). | INCONCLUSIVE ([run](#h24-run-i9-13900t-2026-10-17)) |
| H25 | Agents that send a byte-identical system prompt and tool schemas to one OVMS CPU server reuse one prefix KV, so an added agent's first request skips the shared prefill. Whether the shared blocks also cut resident memory per live agent is measured too, not assumed. Pre-registered 2026-10-17T05:08-07:00, before any H25 run. | On an i9-13900T, OVMS 2026.4.1 (digest a928903f…), `OpenVINO/Qwen3-4B-int4-ov` b467368d on the 16-CPU P-core balloon, shared prefix Ps ≈ 1536 tokens, private Pp = 96, max_tokens 8, thinking off. (T) The median TTFT of an added agent (agents 2..6, sequential) is ≥ 4x lower with the shipping default (prefix caching on, cache_size 0) than with `--enable_prefix_caching=false`; the lower bound of the 95% bootstrap CI of the ratio is ≥ 4x. Point prediction: OFF 9.6 s ± 15% (Ps+Pp at the 172 tok/s of H6), ON 0.9 s (0.6-1.4). (C) An ON agent with one token changed at system position 16 measures ≥ 0.8x the OFF median. (M) Memory per added live agent in a 6-agent burst: OFF ≈ 117 MiB (52 blocks x 32 tok x 72 KiB u8). Expectation (prior 0.5): ON ΔRSS per agent is ≥ 0.8x OFF, because dynamic-pool growth counts full prompts; physical sharing alone would give ≈ 9-12 MiB. Null H0: added-agent TTFT ON/OFF < 2x with c equal to the ON agents (no cross-agent reuse); for M, ON ΔRSS per agent ≥ 0.8x OFF. | `bench/shared-prefix/run.sh` under the live lock, 20 min cap. Fresh OVMS pod every round, arm order ON OFF OFF ON ON OFF (n = 3 rounds per arm, 15 added-agent samples per arm), new prefix seed every round. Per round: warm-up, sequential agents a1..a6 plus the perturbed control c, then a 6-agent barrier burst with 1 Hz sampling of pod `memory.current`. Confounds per round: RAPL package energy, 1-min load, swap used, pod `memory.swap.current`, package throttle-count delta, running container count. Outcome table and guardrails in [H25 pre-registration](#h25-pre-registration). The deploy-time detector (`k3d-gpu agent prefix`) is scored by unit tests, not by this probe. Prior art exists (vLLM APC, SGLang RadixAttention on Xeon, OVMS prefix caching, KVFlow, KVComm): a claim states only the measured delta [provenance:P250] [provenance:P252] [provenance:P253] [provenance:P259]. Run 1 (2026-10-17): TTFT null in every request (probe read only content deltas), both parts void; exploratory end-to-end 4.96x (CI 4.68-5.33), see [H25 run](#h25-run-i9-13900t-2026-10-17). Run 3 (A2 matched background load on the E-cores, A3 first-chunk TTFT, phase S only): T INCONCLUSIVE at `typical` and `heavy` because load was unmatched on core throttle events/min (typical 37.9 vs 105.7, heavy 2422 vs 1014; busy, 1-min load, package W and swap matched), and at `typical` the ON spread gate failed too. Measured: added agent 1.32 s ON vs 12.44 s OFF at `typical` (9.44x, CI 6.99-9.90, 9/9 pairs ≥ 4x, derived confidence 0.91), 2.76 vs 25.56 s at `heavy` (9.27x, CI 8.96-9.73); M not re-measured. See [H25 run 3](#h25-run-3-matched-background-load-i9-13900t-2026-10-17). | INCONCLUSIVE |
| H26 | On a hybrid Intel client with no RDT (i9-13900T: no `rdt_a`/`cat_l3`/`mba`/`cqm` flags, no `/sys/fs/resctrl`), confining a memory-bandwidth neighbour to one E-core L2 cluster (`k3d-gpu.io/cores: ecluster`, 4 E-cores) cuts the agent's p95 latency inflation by at least half against the same neighbour in the `efficient` balloon. On hosts with `rdt_a`+`mba`, nri-plugins RDT classes are the predicted remedy instead (not testable on this host; H26-X). | R = I(MC4)/I(ME15) <= 0.5, where I(arm) = p95(arm)/p95(Q) - 1 on agent request latency. Point prediction: p95(Q) ~2.3 s, I(ME15) >= +100% (replicates H3 PM), I(MC4) +20..+50%, R ~0.3; I(ME4) within spread of I(MC4) (core count, not L2 topology, does the work). Competing prior (prior-art note): L2 confinement helps little against a DRAM hog (LLC and memory controller stay shared, no MBA), improvement < 10%. | Pre-registered 2026-10-17T12:18Z before any H26 run; see [H26 pre-registration](#h26-pre-registration-i9-13900t-2026-10-17). Probe `bench/rdt-isolate/run.sh` (analysis `bench/rdt-isolate/analyze.py`). Agent = OVMS 2026.4.1 `OpenVINO/Qwen3-4B-int4-ov`, `k3d-gpu agent up` on CPU with 15 CPU; unique ~30-token prompt x 32 out, greedy, `ignore_eos`, streamed. Arms: Q none; ME15 `stress-ng --stream 15` on `efficient`; ME4 `--stream 4` on `efficient`; MC4 `--stream 4` on `ecluster`; every neighbour `--stream-l3-size 128m` (same total DRAM footprint). 3 blocks x 10 requests per arm (n=30), order Q ME15 ME4 MC4 / MC4 ME4 ME15 Q / Q ME15 ME4 MC4, one cluster `k3dgpu-i-rdt-isolate`. Outcome -> conclusion in the section. | PENDING |
| H28 | On k3s v1.37.1+k3s1 (containerd v2.3.4-k3s1), N agent pods that mount one model through an image volume and mmap it read-only share one page-cache copy, so the per-agent memory cost is near zero, and they become ready faster than pods that copy the model into their own emptyDir. | On an i9-13900T, 1 GiB unique blob, N=8: arm T (image volume + mmap) median PSS per pod <= 160 MiB, Shared_Clean >= 0.9x size, host ΔCached per round <= 1.25x size; arm C (init `cp` to emptyDir, then mmap) PSS >= 0.9x size, ΔCached >= 0.8xNxsize; T round-median time-to-ready (cold source, create to READY after faulting in the whole mapping) <= 0.5x C, and max(T) < min(C). Null: T PSS >= 0.5x size, or ΔCached >= 0.5xNxsize, or T time >= 0.8x C. | Pre-registered 2026-10-17T12:13:48Z before any H28 run. `bench/zero-copy-models/probe.sh` (prep outside the lock, run under live.lock, cluster `k3dgpu-i-zero-copy-models`, scratch KUBECONFIG, locked part killed at 20 min). n=3 rounds per arm, 8 pods per round, order C T T C C T, unique image per round, source file evicted per round (fsync + posix_fadvise DONTNEED in a helper container; mincore must read 0), no global drop_caches. Confounds per round: load1, swap used, swap-out, package throttle count, RAPL W. Outcome -> conclusion: see the [H28 pre-registration](#h28-pre-registration-i9-13900t-2026-10-17). Prior art: KEP-4639, KServe `oci+native://`, Tetris (ATC'22), overlayfs page-cache sharing [provenance:P280]; only the measured delta may be claimed. Prediction: SUPPORTED. | PENDING |
| H27 | A <=60 s on-box probe (`bench/amx-autoquant/probe.sh`) picks the weight precision for agents better than the static int4 default. (a) Fidelity: on an i9-13900T, the probe's quality-gated pick among Qwen3-4B {int4, int8, fp16} OpenVINO weights equals the pick of a 5-round reference benchmark in 3 of 3 probe repeats. (b) Value: that pick's agent-turn latency (TTFT on a 396-token prompt + 96 decoded tokens) beats the static default (int4) by more than the reference run-to-run spread. (c) Cost: the probe takes <=60 s wall time on a warm server, model download not counted. | Pre-registered 2026-10-17T05:08-07:00, before any H27 run. Predicted: (a) holds; (b) is REFUTED on this host because the pick = int4 = the default, delta = 0. Numbers: decode int4 14-17, int8 8-10.5, fp16 4-6 tok/s; TTFT@396 int4 1.9-2.8 s, int8 1.8-3.6 s, fp16 4-8 s; dNLL vs fp16 on held-out text: int8 <=0.01 nats/token, int4 0.02-0.08. Gate: dNLL <=0.05 nats/token (about +5% ppl). Null H0: the probe adds nothing over a static/cpuid rule (pick = int4, or the probe disagrees with the reference). Outcome priors: O1 pick=int4, (b) REFUTED 0.55; O2 int4 fails the gate, pick int8 0.25; O3 probe flips vs reference 0.08; O4 int8 beats int4 on turn latency 0.04; O5 INCONCLUSIVE (int4'/int4 outside [0.9,1.1] in >=2 rounds, swap, all dNLL=0, probe >60 s) 0.08. Confidence: high = pick dominates in 5/5 rounds (sign test p=0.031) and P1-P3 agree 3/3; medium = 4/5 (p=0.19) or 2/3. | OVMS `openvino/model_server:2026.4.1-gpu@sha256:a928903f...` (pin from `share/npu-embeddings.yml`), one container per precision (`k3dgpu-i-amx-autoquant-{int4,int8,fp16}`), all `--cpuset-cpus 0-15` (P-cores), no k3d cluster; HF commit SHAs recorded before the lock. Reference: n=5 rounds, one turn-shaped request per arm per round, arms int4, int8, fp16, int4' (negative control, same weights), order ABCD/DCBA alternating, nonce-prefixed prompts, greedy, `ignore_eos`. Probe: 3 repeats (before R1, after R2, after R5). Quality: teacher-forced NLL on 4096 held-out tokens (repo text written in 2026) + 1024 wikitext2 tokens (AWQ calibration-leak check). Per-request confounds: RAPL package W, package_throttle_count delta, 1-min load, foreign CPU on cpus 0-15, pgmajfault/pswpin deltas. Run: `flock -w 10800 <scratchpad>/live.lock bench/amx-autoquant/probe.sh` (self-limits to 20 min). Prior art: [provenance:P270]. AMX arms cannot run on this host (no amx_* flags); they need an SPR+ host. Post-review fixes (2026-10-17, before any live run, predictions unchanged): stateful IRs loaded as exported (`use_cache=False` raises in optimum-intel 2.2.0), NLL at `INFERENCE_PRECISION_HINT=f32`, probe order alternates (P2 reversed), watchdog cannot outlive an aborted run, a non-default pick is cached only with 5/5 round wins and no INCONCLUSIVE note. Smoke (outside the lock): `nll.py --precs int4 --max-windows 1` returns 3.188 nats/token (held-out, 511 tokens), 2.539 (wikitext2); OVMS 2026.4.1 serves `/v3/completions` straight from the HF OV dir. Known scoring limit, kept as pre-registered: (b) counts latency only, so an O2 outcome (gate rejects int4) scores as (b) REFUTED; the NLL pass is outside the 60 s budget of (c). Live run not yet executed: the shared live lock stayed held by other workflows for this session. | PENDING |
| H29 | On a client Intel hybrid CPU, B=1 agent decode on the P-cores spends less energy per token at the same speed when the P-cores are capped and the uncore (ring/LLC) floor is pinned high, and the uncore pin is what keeps the speed. Static config held for the whole request (prefill included); not phase-aware. On client parts `intel_uncore_frequency` sets the ring/LLC clock, not the IMC/DRAM clock. | On an i9-13900T (`intel_uncore_frequency` legacy, ring 0.8-4.7 GHz, EPP performance), OVMS 2026.4.1 + `OpenVINO/Qwen3-4B-int4-ov` from `k3d-gpu agent up` (CPU): arm UC (pod CPUs `scaling_max_freq` 2.8 GHz + uncore min = max = 4.7 GHz) vs stock A gives median decode tok/s >= 0.97x and median package J/token <= 0.90x, lower in every paired round, on BOTH gross J/token and marginal J/token against the same round's stock-arm idle; and UC beats C (cap only) by >= 3% tok/s, or by >= 5% J/token on both metrics, in every paired round. Prior: SUPPORTED 0.10, REFUTED-split 0.20, REFUTED 0.50, INCONCLUSIVE 0.20. | Pre-registered 2026-10-17 05:13 PDT before any run (no measurement taken yet). AMENDED 2026-10-17 before any run, after two refuters: (1) the per-arm marginal metric subtracted each arm's own idle, which removes a pinned ring's standing idle cost from exactly the U/UC arms; the decision now uses gross J/token AND marginal J/token against the stock arm's idle of the same round (per-arm marginal recorded, not decided on); (2) "phase-aware" dropped: the knobs are static per request; (3) up to 8 rounds, deadline-bound (Williams square balanced at 4 and 8), L on even rounds; (4) added manipulation checks: U ring >= target - 100 MHz, A pod-CPU MHz >= cap + 200 MHz (else INCONCLUSIVE); (5) decode tok/s uses completion_tokens, the J/token denominator; (6) temperature abort 95 C; (7) the locked runner refreshes sudo itself (`sudo -A -v`), since a no-tty sudo timestamp is per parent process. Physics note (refuters, not fetched): on Alder/Raptor Lake client parts the ring may share the P-core VccIA rail, which favours REFUTED. `bench/uncore-phase/run.sh` (cluster `k3dgpu-i-uncore-phase`, scratch KUBECONFIG, under the live lock, <= 20 min wall) runs `bench/uncore-phase/probe.py`. Arms A (stock), U (uncore floor pinned to `initial_max_freq_khz`), C (P-core cap), UC (C + U), L (uncore pinned 2.0 GHz, regime control); n = 5 rounds per primary arm, n = 3 for L (rounds 0/2/4); arm order a 4x4 Williams square by round (amended before any run: rotate-then-reverse-odd-rounds put only A or C first, caught by the unit test); one 192-token streamed greedy `ignore_eos` request per cell after 6 s settle and a 5 s idle window under the same arm; one unrecorded stock warm-up. Marginal J/token = (package J - idle W x t) / tokens; gross J/token also reported. Per cell: ring MHz (`current_freq_khz`), pod-CPU MHz, package temp, throttle count delta, pswpin/pswpout, load1, busy fraction inside and outside the pod. Cells with a throttle increment or > 100 MiB swap-in are excluded. Outcome -> conclusion: < 4 valid cells in a primary arm, or A tok/s spread > 10% of median -> INCONCLUSIVE; L ring > 2.1 GHz or C cores > 2.9 GHz -> INCONCLUSIVE (knob not honoured); both parts hold -> SUPPORTED; only the first part holds -> REFUTED-split (doctor suggests the cap only); first part fails -> REFUTED. Confidence = 1 - one-sided sign-test p over paired rounds (n/n rounds: 4 = 0.938, 5 = 0.969, 8 = 0.996). Every sysfs write logged and restored (probe `finally`, signal handler, runner EXIT trap from a pre-run snapshot). Prior art exists (uncore DVFS for memory-bound HPC phases, phase-aware DVFS for mobile and GPU LLM inference, the Intel KPM Uncore CRD, hardware ELC; [provenance:P291]-[provenance:P299]); a static joint core + uncore setting is the setting of [provenance:P295], so no novelty is claimed for the mechanism, only the measured delta on this test bed (search log [provenance:P290], incomplete). Run 1 (2026-10-17, [H29 run](#h29-run-i9-13900t-2026-10-17)): INCONCLUSIVE on two pre-registered gates. Stock-arm decode spread 23.2% of median (9.14-11.77 tok/s, n=7; limit 10%), and the U pin was not honoured under load (ring 2900 MHz in 7/7 U cells against a 4700 MHz target). CPUs outside the pod were 61-97% busy, and package idle was 85-117 W, so RAPL package energy carried other tenants' load. | INCONCLUSIVE |
| H22 | Cross-engine speculative decoding (draft on the iGPU, target on the P-cores) raises single-stream decode tok/s over both the target alone and same-engine (P-core) drafting. | On an i9-13900T, OVMS 2026.4.1, `OpenVINO/Qwen3-4B-int4-ov` target on the 16-CPU P-core balloon, `OpenVINO/Qwen3-0.6B-int4-ov` Fast Draft with `draft_device: "GPU"` (UHD 770), `num_assistant_tokens` 5: median paired decode tok/s C/B >= 1.05 and C/A >= 1.10. Written before measuring (2026-10-17T05:08-07:00): REFUTED expected, C/B ~ 0.68 (0.55-0.85), C/A ~ 0.82 (0.65-1.0), because GenAI 2026.4.1 runs draft then verify serially (no overlap) and the UHD 770 runs a 0.6B draft slower than the P-cores. | Pre-registered 2026-10-17 before any H22 run ([H22 pre-registration](#h22-pre-registration-i9-13900t)). `bench/hetero-spec/probe.sh`: one OVMS pod, 5 graphs, per-request cyclic-rotation interleave, n=8 rounds, `bench/hetero-spec/analyze.py` verdict. Refuted if C/B <= 0.95 in >= 7/8 rounds or C/A < 1.0. Result ([H22 result](#h22-result-i9-13900t-2026-10-17)): the outcome table's noise row fired (A 11.3 tok/s, outside 15.7 +-15%; 8/8 rounds throttle-flagged). Directionally C lost to B in 8/8 rounds, median C/B 0.78 (95% CI 0.69-0.85), C/A 1.07. The pre-registered re-run ([H22 re-run](#h22-re-run-i9-13900t-2026-10-17)) started in a quiet window (load1 3.96, package 79 C) and the noise row fired again: A 12.7 tok/s (19% under 15.7), 8/8 rounds throttle-flagged, because the probe's own 16-CPU load heats the 35 W package to 86-97 C. Directionally C lost to B in 8/8 rounds again, median C/B 0.73 (95% CI 0.63-0.75), C/A 0.91. Both runs: C < B in 16/16 rounds. Missing probe: a run on a package that does not throttle under the target's own load. Scope: serial OVMS Fast Draft, UHD 770, 35 W T-SKU, AVX-VNNI (no AMX), no NPU. | INCONCLUSIVE |
| H5 | On a hybrid CPU whose package throttles, the opt-in prefill guard (`K3D_GPU_PREFILL_GUARD`, an E-core `scaling_max_freq` cap) protects P-core prefill from an E-core-pinned CPU neighbour. | At one or more cap levels the neighbour's median P-core prefill penalty (median `ttft_s` over the quiet median, minus 1) is at most half the guard-off penalty, and the p90 penalty is below the guard-off p90 penalty. | Pre-registered in [docs/prefill-guard.md](docs/prefill-guard.md#pre-registered-ab-test-h5): stage 1 host-level, arms quiet / guard off / caps at 75%, 50% and the E-core minimum, five Latin-square rounds per neighbour method (`matrixprod`, `loop`), n = 5 per arm; neighbour bogo-ops/s, a tool-latency proxy, decode tok/s and package power always reported. Stage 2 repeats the chosen level through `k3d-gpu up` and checks the NRI write path and the `down` restore. Refuted if no cap level reaches half; inconclusive if the guard-off penalty is inside the quiet spread or the quiet arm drifts. | PENDING |

## Falsification log

Every probe run, kept or not. When an attack on a hypothesis is settled, mark it
DO-NOT-RE-ATTACK with the reason, so the same experiment is not repeated without
new evidence (new hardware, new driver, new model, changed method).

| Date | Hypothesis | Attack | Result | DO-NOT-RE-ATTACK |
|------|------------|--------|--------|------------------|
| 2026-10-17 | H9 | i9-13900T, 125 GiB RAM: cold `quickstart` n=1, then a rerun and `uninstall --yes` ([H9 run](#h9-run-i9-13900t-2026-10-17)) | Cold arm held: 303 s to Ready (target 900 s), RAM tier picked Qwen3-8B int4 (4882 MB) on 16 P-cores, chat replied "Paris." through Open WebUI; sign-up refused (403). Rerun 2 s with every step skipped; uninstall 6 s. Warm arm not run, n=1, host load average ~38 from other jobs | No: run the warm arm and n>=3 per arm, then on a 16 GB and an 8 GB host |
| 2026-10-16 | H6 | i9-13900T: OVMS 2026.4.1 + Qwen3-4B int4, P-core CPU arm vs UHD 770 arm, 5 rounds (header and numbers in [H6 run](#h6-run-i9-13900t-2026-10-16)) | Held. Decode 15.7 vs 9.9 tok/s (+58%); ~1936-token prompt time to first token 11.2 s vs 29.5 s (2.6x), i.e. prefill 172 vs 66 tok/s. The GPU pod used 1.0 CPU-s/s, the CPU pod 7.9 | DO-NOT-RE-ATTACK on UHD 770 / Raptor Lake with this model and server: re-run on new hardware, driver, model or OVMS release |
| 2026-10-16 | H4 | Same host and data as H6, against the `agent` auto rule of that time (any allocatable Intel GPU, else CPU) | That rule picked the UHD 770, which lost to the P-cores on decode and prefill. Not a verdict for H4, which needs every usable device swept with the H2 method; it changed the rule: `auto` now takes the GPU only on a `gpu.intel.com/xmx=true` node | No: H4 is re-run against the new rule |
| 2026-10-17 | H11 | i9-13900T: `up` with the pinned default vs the same image ID through a floating ref, 3 runs per arm alternating; the added inspects 5 times; a cluster made by main's launcher, then the branch launcher (header and numbers in [H11 run](#h11-run-i9-13900t-2026-10-17)) | Held. Median `up` 36.57 s pinned vs 37.20 s floating (Δ 0.63 s, arm spreads 1.15 s and 2.83 s); added checks median 0.026 s. On main's cluster `doctor` warned "node image cryptoandcoffee/k3d-gpu:latest (unversioned ...)" and `up` stopped with rc 1 before `k3d cluster create`; after recreate the node ran `cryptoandcoffee/k3d-gpu:0.1.21`, label 0.1.21, `doctor` ok | DO-NOT-RE-ATTACK unless `ensure_image`/`node_image` gain more docker calls or k3d changes how it resolves a cached image |
| 2026-10-17 | H12 | i9-13900T: control c1 on an `origin/main` image, then run a1 (default cluster, host Helm v4.1.1, no extra flag) | Control failed as predicted ("cannot be imported ... missing key meta.helm.sh/release-name"). a1 failed: helm rc 1, "conflict occurred while applying object ... conflicts with \"deploy@k3d-k3dgpu-r2-beat-server-0\"" on DaemonSet `.spec.template.spec.nodeSelector` and BalloonsPolicy `.spec.balloonTypes`: Helm 4 installs a new release with server-side apply, and k3s's deploy controller already manages those fields. REFUTED; runs b were not started | DO-NOT-RE-ATTACK with Helm 4 defaults on a default cluster: the conflict is deterministic |
| 2026-10-17 | H13 | Runs A3-1 (Helm v3.14.0) and A4-2 (Helm v4.1.1 `--server-side=false`) on default clusters; batch stopped after A4-2 | Both: helm rc 0, revision 1 `deployed`, DaemonSet relabelled to the release and its nodeSelector changed to `k3d-gpu.io/hybrid-cpu`, `k3d-gpu test` passed (P pod on `0`, E pod on `24`), but the BalloonsPolicy kept the baked types (`replica ... shared`, no `vllm-balloon`), so the `vllm` and `redis` probes both ran on the shared balloon `0-15,17-31`. Helm's client-side update of an adopted object diffs the rendered object against itself, so a custom resource gets an empty merge patch; native kinds get a three-way patch against the live object. REFUTED for A3 and A4 | DO-NOT-RE-ATTACK: adoption without a force flag never changes the BalloonsPolicy spec |
| 2026-10-17 | spike for H14 | One default cluster each: Helm v4.1.1 `--server-side=false --force-replace` on the A4-2 cluster; Helm v3.14.0 `--force`; Helm v4.1.1 `--force-conflicts` | All three: BalloonsPolicy types became `reserved vllm-balloon performance efficient lowpower ecluster pcore-physical latency agent-control-plane shared`. Exploratory, n=1 each; not a verdict | No: H14 measures the force paths with n=3 |
| 2026-10-17 | H14 | i9-13900T: 12 runs, 3 per arm (A3f, A4f, B4, B3) in the pre-registered order (header and per-run table in [H14 run](#h14-run-i9-13900t-2026-10-17)) | Held in 12/12. Every run: helm rc 0, revision 1 `deployed`, `vllm-balloon` and `agent-control-plane` present, DaemonSet 1/1, `k3d-gpu test` rc 0, `vllm` container on `10-11` (P-cores 0-15), `redis` pod on `24-25` (E-cores 16-31). External runs: no `nri-balloons.yaml` in the manifests dir, no CRD, no DaemonSet before Helm. Default runs: restart without `.skip` put the baked types back (6/6), restart with `.skip` kept `vllm-balloon` (6/6) | DO-NOT-RE-ATTACK unless nri-plugins, Helm or k3s's deploy controller changes |
| 2026-10-17 | EAT flow | One default cluster, Helm v3.14.0: `helm upgrade --install` with chart defaults, then again with `contrib/eat/hybrid-values.yaml` (EAT's install_nri.yaml then main.yaml order) | Revision 1 left the baked policy as is. Revision 2 replaced the balloon types with the values' types but kept the baked `agent.podResourceAPI: true` (the values say false): Helm patched only what differs between its two revisions. Exploratory, n=1; backs the docs/intel.md wording, no verdict | No |
| 2026-10-17 | H21 | i9-13900T: `bench/joule-pack/probe.sh` at bdd1a02, 3 rounds x 5 arms, interleaved (header and table in [H21 run](#h21-run-i9-13900t-2026-10-17)) | INCONCLUSIVE on (a) and (b). Idle package floor 90.7 W median, spread 26.5 W over 16 idle windows, against arm marginals of -3.3..10.8 W: the shared host (53 containers, other experiments' load) swamps the signal; throttle counter moved in 11/15 arms. Per-round best/E 1.75x, 104.9x, 2.18x (agents/W) are noise, not a result | Not DO-NOT-RE-ATTACK: re-run only on a quiet host (no other containers, idle spread < 2 W); add an N sweep before claiming anything about the SLO |
| 2026-10-17 | H20 | i9-13900T: `bench/phase-steer/probe.py` at a171ded (A1 design: 4 agents, disjoint slices, `--poll 0`), 4 of 10 cells recorded (SP, PS, ALL, PS) before the 20-min cap (header and numbers in [H20 run](#h20-run-i9-13900t-2026-10-17)) | INCONCLUSIVE: 0 turns inside the 60 s window in all 4 cells. Cached turns took 58-75 s (decode 2.2-2.4 tok/s, prefill 12-22 tok/s) on a host with load1 11.6-18.3 and 52 other containers; package throttle counter +578..+6148 and pswpin > 0 in every cell. No J/token or p95 exists for any arm | Not DO-NOT-RE-ATTACK: re-run once, after an amendment that measures whole turns (per-turn RAPL integration, or a window of at least 5 turn times), on a host below 10% background busy |
| 2026-10-17 | H22 | i9-13900T: `bench/hetero-spec/probe.sh`, runs 2 (06:29) and 3 (08:40, quiet gate passed), 8 rounds x 5 arms each (headers in [H22 result](#h22-result-i9-13900t-2026-10-17) and [H22 re-run](#h22-re-run-i9-13900t-2026-10-17)) | INCONCLUSIVE twice on the noise row: A 11.3 and 12.7 tok/s vs the 15.7 calibration, every round throttle-flagged; in run 3 the probe's own 16-CPU decode heated the 35 W package to 86-97 C. Directionally C < B in 16/16 rounds (C/B 0.78, 0.73), C/A 1.07, 0.91 | Not DO-NOT-RE-ATTACK, but do not re-run on this host: the throttle is self-induced. Re-run only on a package that holds the target without throttling, or after an amendment that recalibrates A under load |
| 2026-10-17 | H24 | i9-13900T: `bench/sku-model/probe.py` at `a19f52c`, run 1 (10:47 PDT, quiet gate passed), 4 OVMS arms, visits A B C D D C (deadline before B A) (header in [H24 run](#h24-run-i9-13900t-2026-10-17)) | INCONCLUSIVE (partial run): decode c=1 and prefill n = 0 (TTFT never captured: OVMS streams Qwen3 thinking as `reasoning_content`); prefill prompts were ~2920 tokens, not ~512; throttling on every visit. Scored c4 vs M0: B -25% (n=1), C -6%, D -34%. Unscored decode proxy (128 / e2e) relative to A: B 0.78, C 0.57, D 0.71, against M0 1.0 | Not DO-NOT-RE-ATTACK: harness faults, fixed in `cce5fd8` (tests in `bench/sku-model/test_probe.py`). Re-run once at `cce5fd8`; the proxy says M0 decode will likely fail (row 2 or 3) |
| 2026-10-17 | H23 | i9-13900T: `bench/thermal-resource/probe.sh` at `cbc3a08`, run 1 (09:32 PDT, quiet gate passed), calibration then budget gate (header in [H23 run](#h23-run-i9-13900t-2026-10-17)) | INCONCLUSIVE (O0): budget 10 W, 2 slots, gate needs >= 4; 0 of 9 arm runs. Fit R_th 0.297 C/W, T_idle 79.3 C, P_th 91.6 W against an idle-window median of 81.3 W (range 72.3-98.9 W, 6.3% of the idle window already throttled). Bootstrap of the idle median (fit fixed): budget 95% interval 6-11 W, never >= 16 W | Not DO-NOT-RE-ATTACK: the pre-registered O0 condition (idle within 10 C of T_tcc) did not hold (79.3 C vs 95 C). The shortfall is background draw on a shared host, so re-run once on a quieter window; if it gates again, move H23 to a host that idles >= 20 C below T_tcc |
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
| 2026-10-17 | H16, H17 | i9-13900T, UHD 770: 6 fresh clusters D P P D D P (one `up`, 3 timed claim or i915 pods and a 2-pod check each), plus a `k3d-gpu test` cluster in DRA mode and a manual `k3d cluster create` from the same image (header and table in [H16/H17 run](#h16h17-run-i9-13900t-2026-10-17)) | H16 held in all 3 DRA runs. H17 (a) failed in all 3: the second claim pod stayed Pending (`cannot allocate all claims`) while 2 i915 pods ran at once in all 3 plugin runs; (b) and (c) held (pod latency 1.00x, `up` 1.06x) | DO-NOT-RE-ATTACK for gpu-v0.12.1: DRA mode stays opt-in. Re-attack when an Intel GPU DRA driver release publishes shared or multi-allocatable devices (consumable capacity or `allowMultipleAllocations`) |
| 2026-10-17 | H18 | i9-13900T with tailscale: pilot run 0, then 3 fresh clusters from this branch's image, `bench/sandbox_round.sh` (header and numbers in [H18 run](#h18-run-i9-13900t-2026-10-17)) | Held 3 of 3. Every sandbox pod on CPU 24 (cpu_atom 16-31); sandbox -> `100.65.179.24:22` and `192.168.1.108:22` refused in 0.13-0.16 s with 2 packets on the policy's drop log, while the control pod connected to both; `pypi.org:443` connected and `pip install --user tomli_w==1.2.0` installed to `/tmp/.local` and imported. Pilot: blocked egress is a REJECT, not a timeout, and `six` ships in the image (amendment in the H18 row) | Yes, for egress and placement with kube-router on k3s v1.37.1; re-attack on a new k3s, agent-sandbox or CNI |
| 2026-10-17 | H29 | i9-13900T: `bench/uncore-phase/run-locked.sh` on cluster `k3dgpu-i-uncore-phase`, 32 of 36 planned cells (rounds 0-6; the deadline stopped it before round 7), arms A/U/C/UC n=7, L n=3 (header and numbers in [H29 run](#h29-run-i9-13900t-2026-10-17)) | Not decided. Gate 1: A decode spread 23.2% > 10%. Gate 2 (not reached by the analyzer, read from the data): U ring 2900 MHz in 7/7 cells vs the 4700 MHz pin, so the U arm did not test a high ring. Indicative only, under the confound: UC/A paired median decode 0.926x, gross J/token 1.115x (lower in 1 of 7 rounds); C/A 0.976x and 1.055x | Not settled: re-run on a quiet host (CPUs outside the pod < 10% busy, idle package power near the host's own idle), and first check that `min_freq_khz = max_freq_khz = 4700000` holds the ring at 4.7 GHz under decode load |
| 2026-10-17 | H25 | i9-13900T: OVMS CPU, shipping prefix caching ON vs `--enable_prefix_caching=false`, 6 agents with a 1538-token shared head. Run 1: 5 of 6 rounds under uncontrolled 1-min load 18-29 ([H25 run](#h25-run-i9-13900t-2026-10-17)). Run 3: A2/A3, matched `bench/load/load.py` load on the E-cores, ON OFF interleaved, `typical` 3+3 rounds and `heavy` 3+2 ([H25 run 3](#h25-run-3-matched-background-load-i9-13900t-2026-10-17)) | INCONCLUSIVE. Run 1: TTFT null in all requests (no content delta with tools), all rounds void. Run 3: load unmatched on core throttle events/min at both levels (every other load metric matched), ON spread gate failed at `typical`. Measured added-agent TTFT 1.32 s ON vs 12.44 s OFF (9.44x, CI 6.99-9.90) at `typical`, 2.76 vs 25.56 s (9.27x, CI 8.96-9.73) at `heavy` | No: re-run with load matched on throttle events (pre-register an even round count and more, shorter windows) |
| 2026-10-16 | H3 (motivates H5) | Wave-1, host level, no cluster, i9-13900T: inference pinned to CPUs 0-14, a 15-worker `stress-ng` neighbour pinned to E-cores 17-31 (disjoint cpusets), n = 4 per arm. | Quiet median prefill 7.03 s. Prefill rose 30% with a register-only neighbour (`--cpu-method loop`), 31% with the mixed one, 40% with `matrixprod`; decode rose 6%. The host was already at PROCHOT (`package_throttle_count` about 22M, package 91-96 C under load). A register-only neighbour doing nearly the cache-heavy damage points at package power/thermal/clock coupling, not cache. Reported by the wave-1 run; its Reproduce header and raw results are not in this repository yet, so this row settles nothing and backs no claim. | No: re-run under H3's k3d-gpu probe with a full header. |
| 2026-10-17 | H8 | i9-13900T, `k3d-gpu capacity --slo 10 --force` per arm (budget 12 min or what the 15-min hold left), each arm on a fresh cluster, model staged into the node ([H8 run](#h8-run-i9-13900t-2026-10-17)) | A4 vLLM CUDA (RTX 4000 SFF Ada, AWQ): APB >= 32, every step to `--max` 32 passed, p95 5.96 s at 32. A1 OVMS int4 P-cores: APB 0 by rule in 2 of 2 runs: N=1 `insufficient` (n 39 < 60) at p95 9.65 and 9.86 s; pooled over both runs n 78, p95 9.75 s, CI 9.56-10.34 (exploratory). A2 OVMS int4 UHD 770: >= 0, open (warm-up p95 26.1 s at N=1; a scored rep needs ~20 min). A3 EAT vLLM CPU defaults: not scored, OOMKilled at its 16Gi limit in the warm-up, 2 of 2 runs | No: A1-A3 need longer budgets, A3 a memory limit that holds the trace |
| 2026-10-17 | H7 | i9-13900T: OVMS `OpenVINO/Qwen3-4B-fp16-ov` vs EAT's vLLM CPU `Qwen/Qwen3-4B` bf16, 16 P-core CPUs each, one server at a time, blocks A B B A A B, block 6 cut by the 20-min cap ([H7 run](#h7-run-i9-13900t-2026-10-17)) | Decode median 4.31 tok/s OVMS (n 6, 4.06-4.38) vs 3.73 vLLM (n 4, 3.69-3.82): 1.156x, block-median ratios 1.08-1.18, on both sides of the 15% line. TTFT on ~417 tokens 5.15 s vs 22.28 s (4.3x). H2's 3.4x decode gap is mostly precision | No: the pre-registered 5 ABBA rounds, the `--enforce-eager`-dropped arm and energy are not run yet |

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

| 2026-10-16 | H1 | i9-13900T (AVX2 and VNNI, no AVX-512, no AMX): EAT release-1.0.3-hotfix through its brownfield path on one k3d-gpu node, no EAT file edited ([H1 run](#h1-run-i9-13900t-2026-10-16)) | INCONCLUSIVE, confidence 0.7 that a clean pass holds. A chat completion and a tool call returned through EAT's LiteLLM gateway from EAT's CPU vLLM (Qwen3-1.7B), and `eat-check.sh` passed. No Xeon-only instruction was needed. The probe was not run as written: the model step ran twice on one cluster (KV cache 4 to 6 GiB, `helm uninstall` between), no `kubectl wait ... --all -A` was recorded, and the host was a shared live host, not a fresh one | No: re-run the probe once, uninterrupted, on a fresh cluster |
| 2026-10-16 | H2 | i9-13900T: OVMS 2026.4.1 + Qwen3-4B int4 on UHD 770 vs the tuned 16-CPU P-core arm, 5 ABBA rounds ([H2 run](#h2-run-i9-13900t-2026-10-16)) | REFUTED, confidence 0.9. The iGPU lost decode (9.88 vs 15.67 tok/s), prefill (29.5 s vs 11.2 s TTFT), 4-in-flight (30.4 vs 56.0 tok/s) and decode energy (8.86 vs 5.32 total package J/token). Prefill incremental energy tied. It won only CPU offload (1.0 vs 7.9 CPU-s/s). Same campaign as H6 | DO-NOT-RE-ATTACK on UHD 770 / Raptor Lake with this model and server: re-run on an XMX iGPU, new driver, model or OVMS release |
| 2026-10-16 | H2 | Secondary arm in the same run: EAT's vLLM CPU v0.24.0, bf16, `Qwen3-4B-Instruct-2507`, on the same 16-CPU P-core balloon, 2 blocks after the A/B | 4.63 decode tok/s against OVMS int4's 15.67. Not an engine comparison: precision (bf16 vs int4), checkpoint and config all differ, and both engines ran at about the same effective weight bandwidth. No engine ratio is claimed; H7 is the matched-precision re-run | No: superseded by H7 |
| 2026-10-16 | H3 | i9-13900T: OVMS CPU 15-CPU request, stress-ng neighbour, 7 arms x 5 rounds ([H3 run](#h3-run-i9-13900t-2026-10-16)) | REFUTED, confidence 0.85. Decode held (P +6.0% vs Q), prefill did not (P +32.6%, block ranges disjoint). Pinning still beat no labels on prefill (P -13.6% vs U). A stream (DRAM bandwidth) neighbour on the E-cores was not isolated at all (decode +168%) | No: re-run Q vs P on a host whose own processes are confined off the P-cores, to split host spill from package coupling |
| 2026-10-16 | H4 | i9-13900T: `auto` of `claude/agent-stack` 8385dc2 (picks the UHD 770) vs forced CPU on 16 P-CPUs, 8 P-CPUs and 15 E-CPUs, 5 rounds each ([H4 run](#h4-run-i9-13900t-2026-10-16)) | REFUTED, confidence 0.85. The forced 16-P-CPU arm beat `auto` by 47% on decode and cut prefill TTFT by 59% (paired medians); even the E-core arm, which holds no P-core CPU, beat it by 21% decode. `auto` won only least host CPU (1.0 CPU-s/s) and short-prompt TTFT against the E-core arm (~51 ms) | DO-NOT-RE-ATTACK for the 8385dc2 rule on this host. The XMX-gated rule now on `main` was written after this data and is not tested by it |

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

### H28 pre-registration (i9-13900T, 2026-10-17)

Registered 2026-10-17T12:13:48Z, before any H28 measurement. Probe: `bench/zero-copy-models/probe.sh`;
product path: `k3d-gpu model-share` (renders the fleet Deployment and warns when the
snapshotter, the device or an unpinned tag would give each replica its own copy).

Baseline first: arm C = the same image volume mounted in an init container that runs
`cp` into an `emptyDir`, then the agent mmaps the copy. It is a lower bound on a
per-pod download. Treatment: arm T = the agent mmaps the file in place from the image
volume. The only difference between the arms is the copy.

Confounds and how each is killed:
- Hot cache from `k3d image import`: the round's unpacked snapshot file is evicted
  (fsync + `posix_fadvise(DONTNEED)` as root in a helper container that mounts the
  node volume read-only; no sysfs write) and checked with `mincore`.
- Carryover between rounds: a unique blob and image per round.
- Throttling, swap, other containers on the host: arms interleaved; throttle count,
  RAPL W, load1, swap used and swap-out recorded; swap-out > 256 MiB voids the round.
- Snapshotter: read from the k3s-generated containerd config, recorded in `meta`.
- Probe blindness: arm C must show N-fold duplication.

n=3 rounds per arm, 8 pods per round. The round is the unit. 3 v 3 with complete
separation gives an exact one-sided Mann-Whitney p = 1/C(6,3) = 0.05, the smallest
this design can produce. Confidence = (fraction of non-void rounds meeting the
prediction) x (1 - p). Spread = min-max of the round medians.
Guardrails: cold pages left = 0; swap-out <= 256 MiB; Ready within 240 s; <= 18 min of
rounds under the lock, 20 min hard kill; MemAvailable >= 1.5 x N x size or abort
(rerun at MIB=512).

| Outcome | Prior | Conclusion | Action |
|---|---|---|---|
| T meets memory and time; C shows duplication | 0.70 | SUPPORTED | `model-share` docs may cite [ledger:H28] for PSS and ready-time ratios |
| T memory holds, T time > 0.5x C but <= 0.8x C, or rounds overlap | 0.10 | REFUTED (time part) | Claim memory sharing only; time stays unclaimed |
| T PSS >= 0.5x size, or ΔCached about N x size, snapshotter overlayfs | 0.05 | REFUTED | Image-volume views do not share page cache on this stack; drop the "zero-copy" wording; file the data upstream |
| Same, snapshotter `native`/fuse | 0.05 | REFUTED for this snapshotter | `model-share` already warns; add a doctor detect-and-suggest step (overlayfs, exact apply/revert); rerun |
| C ΔCached < 0.8 x N x size or C PSS < 0.9x size | 0.05 | INCONCLUSIVE (probe blind) | Fix the probe (per-pod cgroup `memory.current`); no claim |
| >= 2 rounds of an arm void, or round-median spread > 30% of median | 0.05 | INCONCLUSIVE | Rerun with MIB=512 or in a quieter window |

**Amendment A1, registered 2026-10-17T12:39:21Z, before any H28 measurement** (it
answers two refuter reviews; the original rows above stay as registered):
- Primary memory metric is now `model_resident_kB`: mincore resident pages of the
  round's shared file plus, in arm C, of every pod's copy. It counts only the model's
  page cache, so the 59 other containers on the host do not move it. ΔCached stays,
  as a secondary metric. Predictions: T <= 1.1x size; C >= 0.8 x (N+1) x size (the
  source plus N copies). Null: T >= 0.5 x N x size.
- Charge, recorded, not scored: pod-level memcg `memory.current` and `file`. Predicted
  for T: the first pod to fault a page is charged for it, so the per-pod `file` charge
  is uneven and sums to about 1x size. That pod needs headroom of about 1x size in
  `memory.current`. The claim is therefore node-total: the node saves (N-1) x size,
  and the per-agent cost is near zero only in PSS terms.
- Cold check: a prime pod mounts the image volume without reading it, so the layer is
  unpacked before eviction. The script prints VOID-cold for any `cold != 0`.
- Order bias: an untimed warm-up round (r0, arm C) runs first and is never scored.
- Lock: the rounds process is killed at 17 min. The cluster and model images are
  deleted while the lock is still held, and k3dgpu-f-* is re-checked inside the lock.
- A hostPath or RWX-PVC arm is not run. It would share one inode in the same way, so
  any claim is scoped to "matches a shared-file mount, and adds OCI delivery and
  pinning".
- The priors are revised before measuring, because 8 GiB of `cp` writes stay in page
  cache on a 128 GiB host: SUPPORTED 0.45, REFUTED (time part) 0.35, the other rows
  unchanged at 0.05 each.

**Run status, 2026-10-17T12:41Z: not run, still PENDING.** The A1 probe is committed
and passes `tests/model_share_test.sh` and shellcheck. The live lock had one holder
and six waiters (at most 20 min each), more than this attempt's budget, so no round
ran and no number exists. The prep images were removed. Next step: `probe.sh prep`,
then `probe.sh run` under live.lock. The verdict is scored only against the rows
above and A1.

Generalization (prediction, by family): page-cache sharing is a kernel and filesystem
property, so the memory result should hold on every Intel family (Core, Core Ultra,
Atom/N-series, Xeon) with the saving (N-1) x size, while weights stay CPU-resident.
GPU or NPU offload copies into per-process device buffers, so `model-share` renders
CPU-resident weights only (A1 removed its decorative `--device` flag). The time ratio depends on copy cost: a larger win is predicted on
low-RAM, single-channel or eMMC hosts (N100, 16 GB Core Ultra), a smaller one on
8-channel DDR5 Xeons. On multi-socket Xeon the shared copy sits on one NUMA node; a
per-socket follow-up is needed before claiming anything there. memcg charges the page
cache to the first pod that faults it, so `memory.current` stays lopsided even when
PSS is not; the first-loading pod needs the limit headroom.

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

### H16/H17 run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T07:20:31Z..07:28:56Z (pre-registered in 205dfb6, pushed 07:15:01Z; rebased onto aa968f0 as 8524df2)
hypothesis:      H16, H17
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores)
host kernel:     6.6.128-1-MANJARO
accelerators:    nvidia=2 (RTX 4000 SFF Ada, RTX 2000E Ada), intel=1 (UHD 770, 0xa780, i915, PCI 0000:00:02.0), hybrid
k3d-gpu commit:  8477aed (claude/intel-gpu-dra-r2 on origin/main 6de91c0; launcher identical to 205dfb6,
                 8477aed only regenerated test goldens; the branch was later rebased onto
                 origin/main aa968f0, which did not touch the up path, as 375faa0)
node image:      sha256:8a8a093acd20, docker build -t k3d-gpu:dra-r2-f859afb . from this branch
                 (k3s v1.37.1+k3s1, intel/intel-gpu-plugin:0.37.1,
                 ghcr.io/intel/intel-gpu-resource-driver:v0.12.1@sha256:1479956240397c7207c3d38c0d296bd591cd66f26d432452f9a2d75336843776)
model:           none; workload = busybox:1.37 `ls /dev/dri` (imported into each cluster, imagePullPolicy IfNotPresent)
runtime:         D: pods name ResourceClaimTemplate h16-gpu (DeviceClass gpu.intel.com);
                 P: pods request gpu.intel.com/i915: 1 (plugin default, -shared-dev-num=8)
energy source:   /sys/class/powercap/intel-rapl:0 (package-0), confounds only
runs per arm:    3 (order D P P D D P), one fresh cluster per run, 10 s idle between runs
```

Each run: `k3d-gpu up` timed wall to wall (node image cached, add-on images pulled
by each fresh cluster), 3 pods created one after another and timed from `kubectl
apply` to phase `Succeeded` (0.2 s poll), then two `sleep 40` pods created together
and their phases read 20 s later. The runner is now `bench/dra_round.sh`: the
script used for the run, with the cluster name and paths made parameters and RAPL
read with `sudo -n` afterwards; one validation round of it (D, not counted) gave
`up` 36.1 s, pods 2.57 / 2.87 / 2.84 s and Running, Pending.

| Run | Arm | `up` s | pod latency s (3 pods) | run median | 2 pods at 20 s | package W over `up` | load (1 min) | swap used MiB | throttle delta |
|-----|-----|--------|------------------------|------------|----------------|---------------------|--------------|---------------|----------------|
| 1 | D | 39.5 | 3.18 / 2.64 / 2.90 | 2.90 | Running, Pending | 74.5 | 2.72 | 108046 | 52946 |
| 2 | P | 39.4 | 3.68 / 2.64 / 2.36 | 2.64 | Running, Running | 80.0 | 3.85 | 108044 | 75578 |
| 3 | P | 36.0 | 3.17 / 2.64 / 2.92 | 2.92 | Running, Running | 75.8 | 3.54 | 108043 | 13478 |
| 4 | D | 40.3 | 2.89 / 2.89 / 2.90 | 2.89 | Running, Pending | 72.1 | 2.45 | 108042 | 19414 |
| 5 | D | 40.1 | 3.11 / 2.88 / 2.92 | 2.92 | Running, Pending | 74.6 | 2.94 | 108040 | 37308 |
| 6 | P | 37.8 | 2.90 / 2.68 / 2.91 | 2.90 | Running, Running | 77.7 | 4.20 | 108045 | 15014 |

| Measure | D median | D p90 (n=3: max) | D spread | P median | P p90 (n=3: max) | P spread | D / P |
|---------|----------|------------------|----------|----------|------------------|----------|-------|
| `up` s | 40.1 | 40.3 | 0.8 (2.0%) | 37.8 | 39.4 | 3.4 (9.0%) | 1.06x |
| pod latency s (run medians) | 2.90 | 2.92 | 0.03 (1.0%) | 2.90 | 2.92 | 0.28 (9.7%) | 1.00x |
| pods running at once | 1 of 2 (all 3 runs) | | | 2 of 2 (all 3 runs) | | | |

H16 facts, the same in every D run: DaemonSets listed no `intel-gpu-plugin`
(only `intel-gpu-resource-driver-kubelet-plugin` and the nodeSelector-gated
`intel-gpu-plugin-wsl`), one ResourceSlice of driver `gpu.intel.com` with device
`0000-00-02-0-0xa780` (attribute driver `i915`), node allocatable had no
`gpu.intel.com/i915`, `up` printed `Accelerators schedulable: nvidia=2 intel=1
hybrid=1`, and all 9 claim pods listed `card0 renderD128` and ended `Succeeded`.
In every P run allocatable showed `gpu.intel.com/i915` 8. The second D pod's
scheduling message was `0/1 nodes are available: 1 cannot allocate all claims`,
with its claim `pending` next to the first one's `allocated,reserved`.

Confounds: package W over `up` stayed in the host's idle band (72-80 W) for both
arms, load 2.45-4.20, swap flat at 108040-108046 MiB; the throttle delta (sum of
`package_throttle_count` over all CPUs) varied 13478-75578 with no pattern by arm.
A detect_test.sh run from another worktree was on the host during the campaign.
Two earlier campaign starts (07:18Z) never ran a round: `round.sh` was not
executable, then `sudo -n` had no cached credential; no cluster was created and
no number was recorded.

Extra live checks on the same image: `K3D_GPU_DRA=1 k3d-gpu up && k3d-gpu test`
ran the `intel-gpu-dra-test` claim pod (it listed `card0` and `renderD128`) with
rc 0; the kubelet plugin logged `Sent initial health status, devices: 1`
(ResourceHealthStatus, on by default in 1.37). A plain `k3d cluster create
--image <this image>` with no `--disable` (the manual README path, and what an
older launcher does) ran `intel-gpu-plugin` and left the DRA DaemonSet at DESIRED
0 (node selector `k3d-gpu.io/intel-gpu-dra=true`), with no ResourceSlice.

Verdict: H16 SUPPORTED, confidence high (every fact held in 3 of 3 runs). H17
REFUTED, confidence high: part (a) failed in 3 of 3 DRA runs and held in 3 of 3
plugin runs, and the outcome does not depend on timing; (b) and (c) held with
margins far above the spreads. `K3D_GPU_DRA` stays opt-in (default 0): auto would
remove H15's iGPU sharing default, and the agent and recipes request
`gpu.intel.com/xe|i915`, which DRA mode does not advertise.

```bash
# Reproduce on an Intel iGPU host, from this branch.
TAG=k3d-gpu:dra-r2 && docker build -t "$TAG" . && docker pull busybox:1.37
export IMAGE=$TAG OUT=$(mktemp -d)
n=0; for m in D P P D D P; do sleep 10; n=$((n + 1)); bench/dra_round.sh "$m" "$n"; done
cat "$OUT/results.txt"   # one RESULT line per run; per-run logs in $OUT
```

### H9 run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T12:16:06Z .. 12:21:09Z (cold quickstart), rerun + uninstall right after
host:            i9-13900T, 125.4 GiB MemTotal, load average 37.8 (1 min) at 12:17:46Z from other jobs
launcher:        claude/g-retail (scripts/k3d-gpu 0.1.28 + quickstart), node image cryptoandcoffee/k3d-gpu:0.1.28 (not cached before)
cluster:         k3dgpu-g-retail, scratch KUBECONFIG, K3D_GPU_CHAT_PORT=18080, deleted by `uninstall --yes`
agent:           OVMS 2026.4.1 (pinned digest), OpenVINO/Qwen3-8B-int4-ov commit 5c47abf4b8e12ebe8e99745bb0c1ec17e0c0abcc, CPU, 16 P-cores
chat UI:         Open WebUI v0.12.2 (pinned digest), admin from the generated Secret

# results
cold quickstart: rc=0 wall 303 s (budget 900 s); download bar 0% -> 98% of 4882 MB (16 lines)
warm-up reply:   13 s, "I am an AI language model designed to assist with information and conversation."
end state:       /health 200; sign-in token issued; /api/v1/auths/signup 403; /api/models ['Qwen3-8B-int4-ov'];
                 /api/chat/completions "Name the capital of France in one word." -> "Paris." (1.1 s)
rerun:           rc=0 wall 2 s; cluster, agent, UI and port-forward all reported as already there
uninstall --yes: rc=0 wall 6 s; cluster gone from `k3d cluster list`, port-forward pid gone, state dir removed
```

The UI was checked through its API, not by a screenshot of the page. The warm
arm (all images and the model cached, new cluster) was not run, so H9 stays
INCONCLUSIVE.

### H18 run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T08:17:43Z (pilot run 0), 08:21:20Z..08:25:10Z (runs 1-3); runner check 08:25:25Z
                 pre-registered in 1811012 (pushed before the pilot), amended in a173b04 (pushed before run 1)
hypothesis:      H18
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores; cpu_core 0-15, cpu_atom 16-31)
host kernel:     6.6.128-1-MANJARO
accelerators:    nvidia=2 (RTX 4000 SFF Ada, RTX 2000E Ada), intel=8 (UHD 770, shared), hybrid=1
network:         tailscale0 100.65.179.24/32, br0 192.168.1.108/24, sshd on 0.0.0.0:22
k3d-gpu commit:  a173b04 (claude/agent-sandbox-ecores rebased on origin/main 2b45f87); the branch was
                 later rebased onto origin/main b244662 (#61, k3d-gpu audit), which does not touch
                 the sandbox or up paths: 1811012 -> 789f978, a173b04 -> 67a6f17. On that base a
                 fresh cluster ran `sandbox up` (CPU 24) and `k3d-gpu audit` listed both sandbox
                 images as ok in images.lock; `sandbox down` removed both namespaces
node image:      sha256:8a8a093acd204807b72587208c942d0d441364686f70b27e7be442aaa7d5c29a, docker build -t k3d-gpu:r2-sandbox .
                 (k3s v1.37.1+k3s1; same image ID as the H16/H17 build, this branch changes no image input)
runtime:         agent-sandbox v1.0.7 controller and python-runtime-sandbox v1.0.7, both pinned by digest;
                 NetworkPolicy enforced by k3s's embedded kube-router
model:           none
energy source:   /sys/class/powercap/intel-rapl:0 (package-0), confounds only, sampled every 5 s by a
                 separate sudo loop (the runner's `sudo -n` had no cached credentials, so it printed -)
runs per arm:    3 counted (one fresh cluster each, 10 s idle between), plus pilot run 0 and a runner check
```

| Run | `sandbox up` (s) | sandbox CPUs | sandbox -> 100.65.179.24:22 | control -> same | sandbox -> 192.168.1.108:22 | control -> same | drop-log pkts | pypi.org:443 | pip `tomli_w` | load (1 min) | throttle delta |
|-----|------|----|------|------|------|------|---|------|------|------|------|
| 1 | 12.3 | 24 | refused, 0.14 s | connect | refused, 0.16 s | connect | 2 | connect | ok, 0.7 s | 3.97 | 12016 |
| 2 | 16.6 | 24 | refused, 0.14 s | connect | refused, 0.15 s | connect | 2 | connect | ok, 0.8 s | 3.34 | 13926 |
| 3 | 21.5 | 24 | refused, 0.15 s | connect | refused, 0.15 s | connect | 2 | connect | ok, 0.7 s | 5.29 | 12874 |

`sandbox up` wall time (controller install, CRDs, warm pool Ready, then the
E-core status check; each fresh cluster pulls the controller and runtime images):
median 16.6 s, p90 20.5 s, spread
(max - min) 9.2 s, n=3. Confounds: package power 74.4-80.9 W (median 76.7 W, 29 samples)
across runs 2 and 3, 77.7 W idle before the pilot; swap used 108078-108081 MiB;
the throttle delta is the sum of every CPU's `package_throttle_count` over the
run, so the host was throttling throughout, as in earlier runs. None of the
checks is a timing threshold, so the throttling does not bear on the verdict.

Pilot run 0 (not counted): the same checks with an earlier probe. Placement and
both blocks held, but the blocked connects failed with `Connection refused` in
0.13 s, not a 3 s timeout: kube-router ends its policy chain with an NFLOG rule
("DROP by policy agent-sandbox/python-ecores-network-policy") and the pod
firewall rejects with `icmp-port-unreachable`. The pip probe was void:
`six==1.17.0` is already in the runtime image (`Requirement already satisfied`).
The amendment in the H18 row replaced the timeout with "no connect plus drop-log
packets" and `six` with `tomli_w`, before run 1. The pilot's spare cluster was
also used to read the image's package list and the iptables rules.

Runner check (after the counted runs, `bench/sandbox_round.sh check`, with the
probe now printing the exception instead of kubectl's exit line): same results,
`NOCONNECT ConnectionRefusedError [Errno 111] Connection refused` for both
private addresses, `sandbox up` 8.8 s.

What PyPI egress depends on: the profile allows `0.0.0.0/0` minus RFC1918, CGNAT
and link-local, and kube-dns on port 53. PyPI (Fastly, 151.101.0.0/16) is public,
so the policy allows it; a mirror on a private address would be blocked.

Verdict: H18 SUPPORTED, confidence high for this host (every check held in 3 of
3 runs, with a control pod that proves each blocked address is reachable without
the policy). Scope: k3s v1.37.1 kube-router, IPv4 only (the cluster has no IPv6,
so the IPv6 ipBlock was not exercised); the host's public addresses were not
tested because this host has none.

```bash
# Reproduce on a hybrid host, from this branch (sudo -v first for RAPL).
TAG=k3d-gpu:r2-sandbox && docker build -t "$TAG" .
export IMAGE=$TAG OUT=$(mktemp -d)
for n in 1 2 3; do bench/sandbox_round.sh "$n"; sleep 10; done
# TARGETS="ip:port ..." overrides the default tailscale0:22, LAN:22, pypi.org:443
### H20 pre-registration (phase-steer oracle, 2026-10-17)

Written 2026-10-17T08:40Z on `claude/inv-phase-steer`, before any H20 measurement.

**Question settled:** can any P(prefill) / E(decode) split beat static P-only placement on
this host? If the zero-overhead oracle cannot, an NRI plugin cannot either, because it adds
cgroup cpuset moves and phase-detection lag. In that case H20 closes without plugin code.

**Prior art (no "first" claim).**
- llama.cpp per-phase affinity `-C`/`-Cb` [provenance:P201]. It has used separate threadpools since PR #7526 and #8672 [provenance:P202].
- Phase-adaptive P/E work splitting from Intel, arXiv 2411.19542 [provenance:P200].
- Decode-phase small-core selection for energy, MNN-AECS arXiv 2506.19884 [provenance:P203].
- Static P/E container placement through NRI balloons `preferCoreType` [provenance:P204].
- Mainline Linux has no per-task Thread Director class [provenance:P205]. Only HFI per-CPU capability is exposed, so `phase-steer` uses the sysfs core-type PMUs.
- Gaps the search did not cover are listed under [provenance:P206].

Novelty, if any, is limited to node-level, phase-triggered re-targeting across agent
containers. It can be claimed only as a measured delta against SP and ALL.

**Suspected flaws in the H20 premise.**
- The product serves through OVMS with continuous batching, which has no per-agent thread set. H20 applies only to one engine per agent.
- "Decode is memory-bound per H3" has no support. H3 is a P/E isolation result, and the DRAM roof of this host is unmeasured.
- The E-cores add no DRAM bandwidth. If decode is bandwidth-bound, E decode per agent is slower, and p95 grows.

**Amendment A1 (2026-10-17, written after two refuter reviews and before any H20 run).**
Both refuters found the probe could not test H20. ggml strict mode pins thread j of each
pool to the j-th set bit of its mask, counting from bit 0 in every process
(`ggml_thread_cpumask_next`, ggml-cpu.c [provenance:P202]). Six servers with one shared
strict mask therefore stacked on CPUs 0-3 (two SMT P-cores) or 16-19, ALL was the same
placement as SP, and the placement proof could not see it. Changes, all made before measuring:
- **N = 4 agents, disjoint slices, one thread per physical core.** Agent i: P slice CPUs
  {4i, 4i+2} (two physical P-cores, no SMT sibling); E slice CPUs 16+4i..16+4i+3 (one
  Gracemont L2 cluster). Thread count = slice size in each pool.
- **SP:** `-t 2 -tb 2`, both masks = P slice, strict. **PS:** `-t 4` on the E slice,
  `-tb 2` on the P slice, strict. **NEG:** `-t 2` on P, `-tb 4` on E, strict.
  **ALL:** `-t 2 -tb 2`, no mask, `--cpu-strict 0`: the kernel (ITMT/HFI) places SP's
  thread budget anywhere on 0-31. This is the real competent baseline.
- **Spin pinned:** `--poll 0 --poll-batch 0` in every arm, so energy is work, not busy-wait.
- **Placement proof extended:** each cell records every server thread's
  `Cpus_allowed_list`. A strict-arm cell is CONTAMINATED if two servers pin the same CPU or
  a server pins outside its slice; an ALL cell is CONTAMINATED if any thread is pinned.
  Unit-tested (`test_slices_disjoint_every_arm`, `test_overlap_detected`).
- **Host layout asserted:** the probe refuses unless `cpu_core` = 0-15 and `cpu_atom` = 16-31.
- **Design consequence, stated up front:** PS uses 6 physical cores per agent, SP 2. That
  is the treatment (decode moves onto otherwise idle E-cores), and J/token charges for it.
  Outcome table, guardrails and priors are unchanged. Prior P(SUPPORTED) stays 0.15.
- **Pinned image has no `--mlock`:** b11707 replaced it with `--load-mode mmap+mlock`
  (read from the image `--help`, which made the first A1 start refuse at 05:43:58 PDT before
  any measurement). The probe now passes `--load-mode mmap+mlock` and requires that flag.
- **Product:** `k3d-gpu phase-steer` now prints non-strict masks (`--cpu-mask-batch P
  --cpu-mask E`), since one strict mask shared by N agents stacks them.

**Host pre-checks** (read-only, logged in the jsonl header):
- `/sys/devices/cpu_core/cpus` and `cpu_atom/cpus`, plus the `k3d-gpu phase-steer --json` output.
- `hfi`/`itd` in `/proc/cpuinfo`.
- The image `--help` lists `-C`, `-Cb`, `--cpu-strict`, `--cpu-strict-batch`, `-np` and `--mlock`; the probe refuses to run otherwise.
- No `k3dgpu-f-*` cluster exists. The probe creates no k3d cluster: it runs plain containers `sci-h20-*` with `--cpuset-cpus 0-31`, so the llama.cpp masks alone decide placement. It removes them on exit and on abort.

**Pinned inputs.**
- Server image `$IMAGE` = `ghcr.io/ggml-org/llama.cpp:server@sha256:f41d47eee678c02656eefc9ed8cce76cab584aa7032805eeef0e6639fcfda485`, resolved 2026-10-17. At that time the latest release was b11707 [provenance:P207].
- Model `Qwen/Qwen3-4B-GGUF` file `Qwen3-4B-Q4_K_M.gguf`, revision `bc640142c66e1fdd12af0bd68f40445458f3869b` [provenance:P208].

**Workload.** Six agents, each with its own server (`-np 1`, `cache_prompt: true`). Each
agent has its own 1024-token nonce prefix. Each turn appends 396 new tokens and asks for 96
(`n_predict` 96, `ignore_eos` true, both fields in the server README [provenance:P201]).
The loop is closed, with no think time.

**Arms.** Only the masks differ. All arms use `-t 4 -tb 4 --cpu-strict 1 --cpu-strict-batch 1 --mlock`.

| Arm | `--cpu-mask` (decode) | `--cpu-mask-batch` (prefill) | Role |
|-----|-----------------------|------------------------------|------|
| SP  | 0-15 | 0-15 | baseline, product default (P balloon) |
| PS  | 16-31 | 0-15 | treatment, `k3d-gpu phase-steer` auto output |
| ALL | 0-31 | 0-31 | competent baseline, kernel ITMT/HFI placement |
| NEG | 0-15 | 16-31 | negative control, n=1 |

**Schedule.**
- Order: SP PS ALL, then PS ALL SP, then ALL SP PS, then NEG.
- Per cell: start the 6 servers, warm for 15 s, then measure for 60 s. The window opens only after every agent has a first token. Then stop and drain until package W is at most idle + 3 W, or 20 s pass.
- Budget: 10 cells x ~100 s ≈ 17 min. The probe aborts at 19 min 30 s, removes its containers, and marks a primary arm with fewer than 3 reps INCONCLUSIVE.

**n and why.** Three reps x 3 arms is the most that fits in 20 min with each arm once in
each Latin-square position. That cancels order and thermal drift.
- Per arm, about 70 turns are pooled, so p95 rests on the top ~4 turns. It is reported with its rank CI.
- TTFT and TPOT give thousands of samples per arm.
- J/token gives 3 values per arm, reported as median and min-max.

**Metrics per cell.**
- Turn latency, TTFT and TPOT, from SSE arrival times.
- Tokens out.
- RAPL `intel-rapl:0` and `intel-rapl:0:0` energy, with wrap handled through `max_energy_range_uj`.
- Idle W over the 5 s before the cell.
- J/token, both raw and baseline-subtracted: (E - idleW x t) / tokens.

**Confounds, and how each is controlled or recorded per cell.**
- **Thermal:** Latin square plus drain. The probe records the `package_throttle_count` delta and the mean `scaling_cur_freq` of P and E. A throttle delta > 0 in one arm only makes the result INCONCLUSIVE.
- **Swap:** `--mlock`, and the `pswpin` delta from `/proc/vmstat`. A cell with pswpin > 0 is flagged. Flags in more than 1 rep of an arm make the result INCONCLUSIVE.
- **Other containers on the box:** the probe records the 5 s pre-cell busy fraction of 0-15 and 16-31 separately, plus the 1-min load. Pre-cell busy > 10% on either side flags the cell. The idle-W subtraction removes their steady power from J/token.
- **Placement proof:** `/proc/stat` busy on 0-15 vs 16-31 per cell, and each server thread's `Cpus_allowed_list` once per cell. PS must show E busy above background. SP must show E busy no higher than the pre-cell background. A violation marks the cell CONTAMINATED.
- **Probe sensitivity:** NEG must have the highest median TTFT. If it does not, the result is INCONCLUSIVE regardless of the primary result.

**Guardrails.** Any breach makes the "same p95" half REFUTED:
- p95 TTFT at most 1.2x SP;
- median TPOT at most 1.5x SP;
- zero failed requests;
- aggregate tok/s at least 0.9x SP.

**Outcome -> conclusion** (priors set before the run):

| Outcome | Conclusion | Prior | Action |
|---------|------------|-------|--------|
| PS J/token ≤ 0.85x SP, p95 within SP + spread, guardrails hold, PS beats ALL on J/token | SUPPORTED (oracle) | 0.15 | Build the NRI controller as H20b. It must keep ≥ 80% of the oracle gain. Then sweep N for agents per box |
| PS passes vs SP but ALL is equal or better | REFUTED as invention: kernel placement already gets it | 0.10 | No plugin; doctor may suggest 0-31 for per-agent engines |
| PS J/token lower, but p95 or a guardrail breached | REFUTED (energy bought with latency) | 0.35 | Close at N=6; at most an opt-in eco profile, no claim |
| PS J/token > 0.85x SP | REFUTED | 0.30 | Do not re-attack on Raptor Lake until the DRAM roof is measured |
| NEG not worst TTFT, SP J/token spread > 20% of its median, or a confound flag in one arm only | INCONCLUSIVE | 0.10 | Fix the flagged confound, rerun once, never pool |

**What generalizes.** Each family needs its own run.
- Alder Lake and Raptor Lake: same sign as this host.
- Arrow Lake S/H, with Skymont E-cores and no P-core SMT: PS is most likely to pass.
- Meteor Lake and Arrow Lake H: `phase-steer` already leaves out the LP E-cores, which have no L3.
- Lunar Lake and Panther Lake U: their E-cores have no L3, so `phase-steer` classes them as LP and reports off. Not testable with this mechanism.
- Non-hybrid parts: `phase-steer` reports off. Nothing to test.

**Auto-adapt if SUPPORTED:** enable only when `cpu_atom` exists, serving is one engine per
agent, and the family is on the measured list. Flags override.

### H20 run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T14:46:01Z lock taken, 14:48:33Z probe start, 15:08:32Z probe stopped
hypothesis:      H20 (Amendment A1 design)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (cpu_core 0-15, cpu_atom 16-31; hfi yes, itd no)
host kernel:     6.6.128-1-MANJARO
accelerators:    none (CPU only)
k3d-gpu commit:  a171dedb4605c7dc696bbcc5dd08cebfd250e84d (origin/main); `phase-steer --json` state auto,
                 prefill 0-15, decode 16-31
runtime:         ghcr.io/ggml-org/llama.cpp:server@sha256:f41d47eee678c02656eefc9ed8cce76cab584aa7032805eeef0e6639fcfda485
                 (b11707), plain containers sci-h20-*, --cpuset-cpus 0-31; no k3d cluster
model:           Qwen/Qwen3-4B-GGUF Qwen3-4B-Q4_K_M.gguf at bc640142c66e1fdd12af0bd68f40445458f3869b
                 (file sha256 7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5)
energy source:   /sys/class/powercap/intel-rapl:0 (package-0)
cells:           4 of 10 recorded: SP r1, PS r1, ALL r1, PS r2; cell 5 (ALL r2) stopped by the
                 runner's 1200 s timeout; NEG never ran
quiet gate:      cleared after 150 s (load1 <= 8 and package <= 80 C); start load1 6.24, package 75 C,
                 52 other containers; end load1 12.09, package 82 C. Per cell load1 11.6-18.3
sysfs writes:    none (the probe only reads RAPL, /proc and cpufreq)
cleanup:         0 sci-h20-* containers left; no k3dgpu-* cluster on the host
```

Per cell, from `h20.jsonl` (window = 60 s, opened once every agent had finished one turn):

| Cell | Arm | turns in window | idle W | P busy (pre) | E busy (pre) | throttle Δ | pswpin Δ | placement |
|------|-----|---|------|------|------|------|-----|----|
| 0 | SP  | 0 | 91.3 | 0.64 (0.20) | 0.35 (0.25) | 6148 | 15  | pinned, no overlap |
| 1 | PS  | 0 | 86.5 | 0.42 (0.16) | 0.78 (0.33) | 578  | 116 | pinned, no overlap |
| 2 | ALL | 0 | 78.7 | 0.29 (0.13) | 0.58 (0.35) | 904  | 12  | unpinned |
| 3 | PS  | 0 | 81.0 | 0.34 (0.16) | 0.76 (0.15) | 1560 | 5   | pinned, no overlap |

No cell has a turn that both started and finished inside the window, so J/token, p95,
TTFT and TPOT are empty for every arm. The llama-server logs (copied while cells 2-4 ran;
not a pre-registered metric, n = 4 agents per cell) show why:

| Cell | Arm | cached turn: prefill 396 tok | decode 96 tok | turn wall | first turn (1420 tok) prefill |
|------|-----|------|------|------|------|
| 2 | ALL | 11.8-17.4 tok/s | 2.20-2.32 tok/s | 64-75 s | 11.9-16.6 tok/s |
| 3 | PS  | 20.1-21.8 tok/s | 2.34-2.41 tok/s | 58-60 s | 21.6-23.5 tok/s |
| 4 | ALL | (stopped) | (stopped) | | 16.3-18.5 tok/s |

A turn of 58-75 s fits inside a 60 s window only if it starts in the window's first
second or two, so the closed loop almost never places a whole turn inside it. The probe's wall check budgets warm + window + 45 s per cell but not the
wait for every agent's first turn (90-150 s here), so only 5 cells fit and the runner's
timeout stopped the fifth; the runner then removed its containers.

Verdict: H20 INCONCLUSIVE. The analyzer stops at "a primary arm has fewer than 3 reps";
behind that gate no arm has a single measured turn. Confounds would also block a
decision: the throttle counter moved and pswpin > 0 in every cell, and SP's E busy (0.35)
is above its pre-cell background + 0.05 (0.25), so the placement proof flags SP from other
tenants' load. Confidence that this run cannot decide H20: 0.95 (0 of 4 cells measured;
the 0 follows from turn time ≈ window, not chance). The exploratory logs are not evidence
for either side: PS decode (2.34-2.41 tok/s) and ALL decode (2.20-2.32) differ by less than
the spread inside a cell, and there is no energy figure.

Missing probe: an amendment, written before the re-run, that measures whole turns: integrate
RAPL over each completed turn (or a window of at least 5 turn times, which breaks the
20-min budget at 10 cells), budget the first-turn wait inside the wall check, and run on a
host with background busy below 10% on both core types. Re-run once; never pool with this run.

```bash
# Reproduce (holds the live lock; probe needs root for RAPL).
export IMAGE=ghcr.io/ggml-org/llama.cpp:server@sha256:f41d47eee678c02656eefc9ed8cce76cab584aa7032805eeef0e6639fcfda485
flock -w 14400 "$LOCK" sudo env PATH="$PATH" IMAGE="$IMAGE" timeout -k 10 1200 \
    python3 bench/phase-steer/probe.py --model Qwen3-4B-Q4_K_M.gguf --out h20.jsonl
python3 bench/phase-steer/probe.py --analyze h20.jsonl
```

### H29 run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T13:50:11Z (runner start), cells 13:52:45Z..14:08:03Z
hypothesis:      H29
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores; cpu_core 0-15)
host kernel:     6.6.128-1-MANJARO
accelerators:    not used (agent on CPU, K3D_GPU_AGENT_DEVICE=CPU)
k3d-gpu commit:  28b6478bfd26c41d2d7349bef74c681264c760d9 (origin/main)
node image:      cryptoandcoffee/k3d-gpu:0.1.30
model:           OpenVINO/Qwen3-4B-int4-ov at b467368d16b75df14055562fe927ae8e1f15f7ef
runtime:         openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58,
                 --target_device=CPU, pod CPUs 0-15
energy source:   /sys/class/powercap/intel-rapl:0 (package-0)
runs per arm:    A, U, C, UC n=7 (rounds 0-6); L n=3 (rounds 0, 2, 4); 32 cells, deadline hit in round 7
knobs:           uncore legacy package_00_die_00, initial 800000-4700000 kHz; EPP performance,
                 governor powersave; PL1 117 W; DDR 3600 MT/s; 53 containers on the host
quiet gate:      start load1 7.25, package 84 C (gate cleared after 30 s at load1 7.25, 78 C);
                 end load1 19.38, package 88 C. Per cell load1 10.9-31.3, CPUs outside the pod
                 61-97% busy, package temperature up to 94 C
restore:         probe `finally` re-wrote every knob; afterwards all 34 snapshot values were read
                 back equal. The runner's own sudo re-write failed (sudo timestamp expired after the
                 18-minute window), so the probe's restore is the one that applied
```

Medians per arm (valid cells; one L cell excluded for a throttle increment):

| Arm | n | decode tok/s (spread) | gross J/token | ring MHz under load | pod-CPU MHz | idle W |
|-----|---|------|------|------|------|------|
| A  | 7 | 11.39 (2.64) | 8.29 | 2900 | 3500 | 89.0 |
| U  | 7 | 11.50 (3.10) | 8.17 | 2900 | 3488 | 87.1 |
| C  | 7 | 10.72 (3.69) | 9.06 | 3200 | 2800 | 98.0 |
| UC | 7 | 10.48 (3.71) | 9.45 | 3200 | 2800 | 102.7 |
| L  | 3 | 11.50 (1.29) | 7.97 | 2000 | 3500 | 87.9 |

Paired by round, medians of the per-round ratios (n=7): UC/A decode 0.926x, gross
J/token 1.115x (UC lower in 1 of 7 rounds); C/A 0.976x and 1.055x; U/A 1.016x and
0.983x; UC/C 0.983x and 1.041x. The marginal J/token metric is not usable here:
with 85-117 W of package idle, most of it from other tenants, the idle subtraction
is larger than the request's own energy and several cells come out negative.

Verdict: H29 INCONCLUSIVE. The analyzer stops at the first pre-registered gate,
stock decode spread 23.2% of median (limit 10%). A second gate also fails on the
data: the U arm asked for ring min = max = 4700 MHz, and the ring read 2900 MHz
in every U cell, the same as stock, so U did not test a high ring. The L pin
(2000 MHz) and the C cap (2800 MHz) were honoured. Confidence in INCONCLUSIVE
over a decision: high, since two independent pre-registered gates fail. The
direction under the confound (UC slower and costlier than A, sign-test confidence
that UC gross J/token is lower: 0.008) is recorded, not decided on.

Missing probe: a run with CPUs outside the pod below 10% busy, so package energy
belongs to the request, and a check, before the run, that the 4700 MHz pin holds
the ring at that clock under decode load. If it does not, the U and UC arms
cannot test H29 on this host as written.

```bash
# Reproduce on the test host (sudo through SUDO_ASKPASS; the runner holds the live lock).
SUDO_ASKPASS=<helper> LOCK=<live lock> bench/uncore-phase/run.sh "$(mktemp -d)"
python3 bench/uncore-phase/probe.py --analyze <out>/h29.jsonl
```

### H24 pre-registration

Registered 2026-10-17T12:15Z, before any H24 measurement. Prior art and the
search log: [provenance:P240] (log), P241 to P249 (closest prior art). No source
found does all of: spec-only Intel inputs (P/E mix, channels x MT/s, AMX/VNNI,
iGPU EUs/XMX, NPU TOPS), Core + Core Ultra + Xeon coverage, both agents-per-box
and tok/s, one-host calibration, shipped in a k3d CLI with a calibrated /
extrapolated label. Each part alone is prior art (roofline: LLM-Viewer, which
already models the i9-13900K; one-box calibration with held-out error:
quantprobe; calibrated agent-capacity planning: Poolside). So the only claim
this hypothesis can earn is the measured delta against those baselines, phrased
"no prior art found for this combination (search log [provenance:P240])". Never
"world first".

**Model M0** (`console/sku_model.py`). W = 2,263,625,445 B per decode token
(`OpenVINO/Qwen3-4B-int4-ov` openvino_model.bin); MAC = 4.0e9 per token.
BW_spec = 3600 MT/s x 2 ch x 8 B = 57.6 GB/s (dmidecode, H6 host).
eta_m = 15.66 x W / BW_spec = 0.615; C_spec(8P) = 8 x 64 MAC/clk x 4.0 GHz;
eta_c = 172 x MAC / C_spec = 0.336. decode = 1 / max(W/(eta_m BW), MAC/(eta_c C));
c4 step = max(W/(eta_m BW), 4 MAC/(eta_c C)); prefill = eta_c C / MAC.
Correction before measurement: an earlier draft of this table listed arm C's
c4 as 32.7; the model gives 32.25 (= its prefill, both compute-bound). The row
above carries the computed value.

| Arm (cpuset, threads) | decode c=1 | prefill | c=4 aggregate |
|---|---|---|---|
| A 8P (0-15, 8 thr), calibration | 15.7 | 172 | 62.6 |
| B 4P (0-7, 4 thr) | 15.7 | 86 | 62.6 |
| C 8E (16-23, 8 thr) | 15.7 | 32.3 | 32.3 |
| D 16E (16-31, 16 thr) | 15.7 | 64.5 | 62.6 |

**Competing model M1:** decode = eta_m' x min(BW_spec, triad(arm cpuset)) / W,
eta_m' fitted on arm A only; triad measured per arm by
`research/agents-per-box-ceiling/stream_probe.c`.

**H0 (M0 holds):** all 9 held-out cells (B, C, D x decode, prefill, c4) within
25% of M0, MAPE <= 25%, no cell beyond 40%.

**Baseline first.** Arm A is measured first and last. Baseline for the model:
the uncalibrated (efficiency 1) roofline, as LLM-Viewer computes it, on the same
cells (`baseline_uncalibrated_decode_tok_s` in `plan --predict --json`).

**Primary metric per cell.** Median of the arm's samples; error
e = (measured - M0) / M0. Spread = (max - min) / median per arm.

| Confound | Kill | Recorded per visit |
|---|---|---|
| Thermal throttling | Interleave A B C D D C B A; A first and last is the drift sentinel | package throttle count delta, mean `scaling_cur_freq` of the arm's cpus, RAPL package W, coretemp max |
| Swap | MemAvailable >= 20 GB with all 4 servers loaded, else abort; pswpin delta > 1000 pages flags the visit | `/proc/vmstat` pswpin/pswpout delta |
| Other containers on the box (not touched) | Interleave; 5 s idle busy sample of the arm cpuset before each visit, flagged if > 10% busy; verdict also recomputed without flagged visits | 1-min load, arm-cpuset busy fraction idle vs during |
| Co-resident idle servers | Negative control: each idle container's cgroup `cpu.stat usage_usec` < 1% of a core during other arms' visits | per-container usage delta |
| Placement | `docker inspect` cpuset; `/proc/stat` busy on arm cpus vs the rest | busy split |
| Prefix / KV reuse | `--enable_prefix_caching false`; 64-bit nonce at the start of every prompt | `cached_tokens` must be 0 or null |
| Harness drift vs H6 | Calibrate on this run's A | A vs 15.66 |

**n.** Decode 6 per arm, prefill 4, c4 2. Effects to detect >= 20% against an
expected CV <= 5%. More does not fit the 20 min lock.

**Outcome -> conclusion**

| # | Outcome | Proves | Action | Prior |
|---|---|---|---|---|
| 1 | All 9 held-out cells within 25% of M0, MAPE <= 25%, A drift <= 10% | M0 transfers across core configs on this host | Keep M0 in `plan --predict`; run the off-host held-out set (H24d) before any cross-SKU claim | 0.15 |
| 2 | M0 decode cells off by > 25% (gap > 2x spread), M1 decode within 20%, prefill within 25% | Spec DRAM roof is not the decode bound for core-starved configs; per-core-type bandwidth is | Ship M1: per-microarch triad constants; `--predict` prints the bound used | 0.45 |
| 3 | M0 and M1 decode both off by > 25% | Bandwidth-only decode model is wrong | No numeric `--predict` decode; spec roofline only as a labelled upper bound | 0.15 |
| 4 | Decode holds, prefill off by > 25% | VNNI MAC/clk or clock constants do not transfer; AMX/XMX extrapolation untrustworthy | `--predict` omits prefill for unmeasured compute paths | 0.10 |
| 5 | c4 cells fail only on C | max() roofline misses compute/memory overlap | Refit c4 with the additive mode of `ceiling.py` | (inside 2/3) |
| 6 | A drift > 10%, any arm spread > 20%, or >= 2 flagged visits per arm | Noise floor too high on this shared host | INCONCLUSIVE; rerun, flagged visits excluded, not re-scored | 0.15 |

**Guardrails:** MemAvailable < 16 GB -> abort; coretemp >= 100 C -> abort visit;
`completion_tokens` != requested -> visit void; any OVMS error -> visit void and
counted; lock held > 19 min -> stop, keep partial data; only `sci-h24-*`
containers are ever started or removed. No sysfs writes.

**Agents-per-box is not measured here.** The probe measures the model's inputs.
`plan --predict` agents-per-box stays "derived" until a separate SLO-load run;
no doc may cite H24 for an agents-per-box number.

**Off-host held-out (H24d, no lock).** Freeze >= 5 third-party OpenVINO/OVMS
decode tok/s rows (SKU, model, quant, runtime version, URL) here BEFORE running
the model on them; pass if MAPE <= 30%. llama.cpp and other runtimes are scored
separately (runtime confound). None frozen yet: the prior-art search found no
published cross-SKU Intel CPU rows for this model and runtime, so H24d is
expected INCONCLUSIVE for lack of data, not validated.

**Review fixes before any run (2026-10-17, commit ed20aa4).** Two refuters
reviewed 870719f; neither asked to kill. Fixed before measuring, predictions
unchanged (the frozen arm numbers above are still pinned by the unit test):
- `plan --predict auto` read this host's 3600 MT/s x 2 for every i9-13900T. It
  now reads the box's own configured memory (`dmidecode -t 17`, root) and
  prints "calibrated" only when that equals the calibration config; unread
  memory falls back to the spec value, labelled extrapolated.
- Non-hybrid parts gave half the cores to sandboxes but computed inference on
  all of them; inference now gets only the remaining half.
- c4 is labelled `derived` (`fields_status`); only decode c=1 and prefill were fitted.
- Probe: SIGTERM now runs container cleanup; internal deadline 17 min (120 s
  under the outer `timeout 1140`); the idle gate waits up to 60 s for the arm
  cpuset to fall under 10% busy before flagging; the package throttle counter is
  read once, not summed over 32 cpus.
- Scorer: the old fallthrough labelled any unmatched mix "row 5"; row 5 is now
  tested explicitly (c4 fails only on C) and anything else is "REFUTED (mixed:
  no pre-registered row)". Row 2 checks gap > 2x spread. The row-6 spread gate
  covers prefill and c4, not only decode. The pre-registered baseline (the
  uncalibrated roofline) is now scored, on absolute decode, next to M0 absolute.
Accepted and not fixed: the probe cannot test the bandwidth term (all arms
share one DIMM set), so even row 1 is intra-host core-subset evidence only;
cross-SKU accuracy needs H24d. Clock and MAC/clk constants for unmeasured
microarchitectures stay "assumed".

**Status 2026-10-17 05:40 PDT: not run, PENDING.** The live-work lock was
held by another experiment (`k3dgpu-i-shared-prefix`) for the whole work
budget; the waiting job was cancelled before acquiring it, so no H24 container
started and no number exists. No verdict and no confidence are claimed. Host
state at the time (refuter reading, /proc/stat 5 s): loadavg ~21-30, arm-cpuset
idle busy 0.37-0.77, package 86 C. At that load the pre-registered row 6
(INCONCLUSIVE) is the expected outcome even with the 60 s idle wait; run in a
quiet window.

**Run** (no k3d cluster is created; the lock serialises live work on the box):

```bash
OUT=$(mktemp -d)
flock -w 10800 "$LOCK" timeout 1140 sudo -E python3 bench/sku-model/probe.py --out "$OUT"
python3 bench/sku-model/probe.py --score "$OUT/raw.jsonl"
```

### H24 run (i9-13900T, 2026-10-17)

**Reproduce:** commit `a19f52c` (run at local `d5b7d02`, same `bench/sku-model/` tree, before rebase; `main` 09c3ef3 plus the per-arm model
repository fix), i9-13900T, kernel 6.6.128-1-MANJARO, OVMS
`openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58`
(2026.4.1-gpu, the H6 digest), `OpenVINO/Qwen3-4B-int4-ov` with
`openvino_model.bin` sha256 `074214fa...df2f9` (equal to H6), one hard-linked
repository per arm. Lock taken 10:46:28 PDT. Quiet gate (1-min load <= 8 and
package <= 80 C, polled every 30 s, up to 30 min) cleared on poll 2. Probe
10:47:28-11:03:58 (990 s, exit 0, stopped by its own deadline after 6 visits).
No k3d cluster; 0 `sci-h24-*` containers left. No sysfs file was written.

```bash
# quiet gate passed: load1 5.57, package 73 C (loadavg 5.57 10.08 10.19)
flock -w 14400 <scratchpad>/live.lock bash -c 'sudo -A -v; sudo -n timeout 1140 env IMAGE=<digest> MODELS=<repo-per-arm> python3 bench/sku-model/probe.py --out <out>'
python3 bench/sku-model/probe.py --score <out>/raw.jsonl
# end: load1 11.05, package 78 C (loadavg 11.05 12.72 11.95)
```

Deviations, both fixed in the harness before scoring anything:
- `a19f52c`: OVMS `--source_model` rewrites `graph.pbtxt`, which carries
  `plugin_config`, on every start; the four arms shared one mount and started
  together, so an arm could load another arm's threads and core type. Each arm
  now mounts its own repository and the probe aborts unless the graph matches.
  This run read back all four configs as launched (A 8 thr PCORE, B 4 PCORE,
  C 8 ECORE, D 16 ECORE).
- `sudo -n timeout 1140 env ...` instead of the documented
  `timeout 1140 sudo -E ...`: sudo's timestamp is per parent pid without a tty.

Raw files: `bench/sku-model/results/2026-10-17-run1/` (`raw.jsonl`, scorer
output `score.json`, gate log).

**Verdict: INCONCLUSIVE (missing cells: partial run)**, applied by the
pre-registered scorer. Three harness faults, found in this run's raw data:
1. Every `ttft_s` was null. OVMS 2026.4.1 streams Qwen3 thinking as
   `delta.reasoning_content`; the probe counted only `delta.content`. So decode
   c=1 and prefill (both TTFT-based) have n = 0 in all arms.
2. The prefill prompt (420 `w<int>` words) tokenized to 2915-2939 tokens, not
   the pre-registered ~512, so each visit took 2-3 min and the deadline stopped
   the run after A B C D D C. A ran once: no drift sentinel, no A-vs-H6 check.
3. One RAPL reading wrapped (`package_w` -1310 W, visit 2).
Fixed in `cce5fd8` with failing-first tests. Row 6 noise markers also fired:
package throttle count rose in every visit (658-7972), coretemp 95-99 C under
load, so C and D each have >= 2 flagged visits even with complete data.
Idle-container controls passed (other servers < 0.06% of a core), cpuset idle
busy 0.03-0.27, no swap.

Scored cells (c4 aggregate; M0 calibrated on this run's A):

| Arm | c4 tok/s (n, spread) | M0 x k | error |
|---|---|---|---|
| A (calibration) | 45.94 (1) | - | - |
| B 4P | 34.58 (1) | 45.94 | -24.7% |
| C 8E | 22.17 (2, 0.2%) | 23.63 | -6.2% |
| D 16E | 30.37 (2, 0.5%) | 45.94 | -33.9% |

Unscored proxies, not the pre-registered metrics (they include the prompt's
prefill and, for prefill, assume the arm's decode rate for the 15 tail tokens):

| Arm | decode 128/e2e tok/s (n, spread) | / A | M0 / A | M1 / A (triad) | prefill tok/s, ~2.9k-token prompt (n) | / A | M0 / A |
|---|---|---|---|---|---|---|---|
| A | 13.01 (3, 0.8%) | 1.00 | 1.00 | 1.00 (38.9 GB/s) | 159.9 (2) | 1.00 | 1.00 |
| B | 10.15 (3, 0.2%) | 0.78 | 1.00 | 0.99 (38.4) | 89.9 (2) | 0.56 | 0.50 |
| C | 7.37 (6, 6.6%) | 0.57 | 1.00 | 0.69 (26.8) | 53.3 (4) | 0.33 | 0.19 |
| D | 9.26 (6, 11.8%) | 0.71 | 1.00 | 0.80 (30.9) | 89.0 (4) | 0.56 | 0.38 |

Confidence: 0.95 that this run cannot score H24 (it is missing 6 of 9 held-out
cells by construction). On direction only: the decode proxy misses M0 by 22%,
43% and 29% with within-arm spread <= 12%, and misses M1 by 21%, 18% and 11%;
the prior for row 3 (both fail) or row 2 (M1 holds) rises, about 0.8 that a
clean re-run refutes M0 decode. A's proxy (13.0) is 17% under H6's 15.66, past
the 15% sanity line; throttling (3056 MHz mean on the P-cores, 95 C) and the
proxy's prefill share both push it down. Prefill at ~2.9k tokens is not the
pre-registered length, so it says nothing about row 4. Missing probe: one
re-run at `cce5fd8` in a window where the package stays under throttle.

### H22 pre-registration (i9-13900T)

Written 2026-10-17T05:08-07:00, before any H22 measurement. Nothing below is a result.

Prior art exists, so no world-first claim: OVMS `draft_device` and OpenVINO GenAI
draft-on-another-device [provenance:P222] [provenance:P229], Dovetail (draft on GPU,
target on CPU) and DuoDecoding [provenance:P221], Lunar Lake NPU-draft slowdown
[provenance:P222], llama.cpp `--device-draft` [provenance:P224]. Only a measured
delta may be claimed. The narrow part searched without a hit is the measurement-gated,
auto-adapting k8s placement of the draft (no prior art found, search log
[provenance:P220]); it backs no performance number.

**Arms** (all in one OVMS 2026.4.1 process, one pod, P-core balloon 16 CPUs plus
`gpu.intel.com/i915: 1`):

| Arm | Graph | device | draft | draft_device | Role |
|---|---|---|---|---|---|
| A | `a-4b-cpu` | CPU | - | - | baseline (target alone) |
| B | `b-spec-cpu` | CPU | 0.6B | CPU | same-engine rival (default `num_assistant_tokens` 5, not swept) |
| C | `c-spec-igpu` | CPU | 0.6B | GPU | treatment (H22) |
| DP | `dp-06b-cpu` | CPU | - | - | measures t_d(P) |
| DG | `dg-06b-gpu` | GPU | - | - | measures t_d(iGPU) |

Every graph: `enable_prefix_caching: false`, `cache_size: 2`, no `plugin_config`.

**Load:** 8 fixed prompts (3 tool-call JSON, 3 code edit, 2 chat; sha256 recorded),
greedy (`temperature 0`), `max_tokens 256`,
`ignore_eos`, streamed, concurrency 1. Decode tok/s = (tokens - 1) / (t_last - t_first).

**Schedule (self-limits to 20 min under the lock):** cluster `k3dgpu-i-hetero-spec`
(scratch KUBECONFIG) -> server Ready with 5 graphs (<= 6 min, else ABORT) -> P0 gate ->
one warm-up per graph -> 8 rounds; round r uses prompt r for every arm, arm order
rotated by r over (A,B,C,DP,DG) (Latin square) -> cluster deleted. Hard stop at 18:00;
partial rounds are reported as partial.

**n = 8:** the smallest n where a unanimous paired sign test gives two-sided
p = 0.0078 < 0.01; 8 rounds x ~62 s fit the budget. Pairing by round cancels drift.

**P0 gate (before timing):** (1) OVMS log loads both graphs with a draft; (2) the i915
render-engine busy ns of the OVMS pid (`/proc/<pid>/fdinfo/*` `drm-engine-render`,
sudo read) is ~0 during an A request and > 0 during DG and C requests; C with ~0 GPU
busy is a silent CPU fallback and makes arm C invalid; (3) a GPU draft compile failure
stops the run (verdict row "P0 fail").

**Confounds:** per request the probe records throttle-count deltas, package temp,
iGPU `rps_act_freq_mhz`, swap si/so, 1-min load, RAPL package energy (reads only, no
sysfs writes). Calibration: A's median must sit within 15.7 tok/s +-15% (H6), else
INCONCLUSIVE. The verdict must hold with flagged rounds (throttle or swap) dropped.

**Guardrails (reported, not the verdict):** greedy output identity of B and C vs A;
TTFT on the long prompt C/A <= 1.20; J/token per arm.

**Derived numbers:** t_t = 1000/A, t_d(P) = 1000/DP, t_d(G) = 1000/DG. Primary
statistic R = median paired C/B; 95% CI by bootstrap over rounds (10k, seed 22);
confidence = 1 - exact two-sided sign-test p of the paired C/B vs 1.0.

**Outcome -> conclusion (priors written before measuring):**

| Outcome | Prior | Conclusion | Action |
|---|---|---|---|
| R >= 1.05, >= 7/8 rounds C > B, median C/A >= 1.10, P0 + calibration pass | 0.05 | SUPPORTED (measured delta only) | `K3D_GPU_AGENT_DRAFT=auto` may pick the iGPU when a DG-vs-DP probe finds t_d(G) < t_d(P) |
| 0.95 < R < 1.05, or rounds split (< 7/8 one way) | 0.13 | INCONCLUSIVE (no gain) | draft stays on the target device |
| R <= 0.95 with >= 7/8 rounds C < B, or median C/A < 1.0 | 0.62 | REFUTED | auto rule stays: draft device = target device |
| P0 fail: GPU draft does not load, or no GPU busy in C | 0.15 | REFUTED as shipped (mechanism untested) | file upstream issue with the log |
| A outside 15.7 +-15%, or > 3/8 rounds flagged | 0.05 | INCONCLUSIVE (setup too noisy) | re-run once in a quieter window |

`bench/hetero-spec/analyze.py` applies this table mechanically to the probe's JSONL.

**Amended 2026-10-17, after review and before any H22 run** (two refuters, both
"prototype, not kill"): (1) tokens are `usage.completion_tokens` from
`stream_options.include_usage`, not SSE chunks, since OVMS writes all of a step's
accepted tokens in one chunk; a request whose count is not 256 is dropped and
counted; (2) a server that never gets Ready, or an OVMS pid the probe cannot find,
is INCONCLUSIVE (BLOCKED, mechanism untested), not REFUTED; (3) P0 rests only on the
i915 render-busy delta, and A must stay under 1 ms of render time per request
(else INCONCLUSIVE, uncontrolled); the `grep draft` log check is gone; (4) OVMS logs
at INFO, not DEBUG; (5) models are pre-staged outside the lock and copied into the
node, every slow child runs under `timeout`; (6) the schedule is a cyclic rotation,
not a Latin square, and B is not tuned (no gamma sweep), so a REFUTED says nothing
about a better gamma. Scope of any verdict: OVMS 2026.4.1 serial Fast Draft, UHD 770
draft, 35 W T-SKU, AVX-VNNI target (no AMX on this host), no NPU (`/dev/accel`
absent). It does not test a parallel draft/verify design (Dovetail, DuoDecoding).

**Shipped default while PENDING:** `K3D_GPU_AGENT_DRAFT` is `off` until a host
measurement shows a speedup (a cross-engine draft lost on Lunar Lake [provenance:P222]). `auto` puts the draft on the target's
own device: in the serial engine a cross-engine draft wins only if it is faster there,
and no host measurement says so yet.

### H22 result (i9-13900T, 2026-10-17)

**Reproduce:** commit `a8aeb01` (branch `claude/inv-hetero-spec`), i9-13900T
(UHD 770, 35 W T-SKU, 131 GB RAM, avx_vnni, no AMX, no `/dev/accel`), kernel
6.6.128-1-MANJARO, k3d v5.9.0 / k3s v1.35.5-k3s1, OVMS 2026.4.1
(`openvino/model_server@sha256:a928903f...76fc58`), models pulled by that image's
`--pull` on 2026-10-17. Run 2026-10-17 06:29-06:44 PDT under the box-wide live lock:

```bash
# outside the lock: pre-stage both models into ~/h22-models (OVMS --pull layout)
SUDO_PASS=... flock -w 10800 <scratchpad>/live.lock bench/hetero-spec/probe.sh <out>
python3 bench/hetero-spec/analyze.py bench/hetero-spec/results/2026-10-17-run2.jsonl
```

Raw records: `bench/hetero-spec/results/2026-10-17-run2.jsonl` (40 requests, n = 8
rounds x 5 arms, all with `usage.completion_tokens` = 256, none dropped).

**Verdict: INCONCLUSIVE (setup too noisy)**, applied mechanically by `analyze.py`.
Two pre-registered guardrails fired: A's median 11.3 tok/s sits 28% under the
15.7 tok/s H6 calibration (band +-15%), and 8/8 rounds are flagged because the
package throttle counter rose during every round (package 79-93 C, 1-min load
14-26: the box was shared). The pre-registered action is one re-run in a quieter
window. Nothing here backs a claim.

P0 passed: i915 render-busy 0 ns during an A request and 8.65 s during a C request,
so the C draft did run on the iGPU (no silent CPU fallback). Per-request render
time in C 8.1-10.0 s, in DG 5.3-5.7 s, in A/B/DP 0.

| Arm | median tok/s | min-max | J/token (median, package) | TTFT s (median) |
|---|---|---|---|---|
| A target alone (CPU) | 11.28 | 8.53-12.12 | 8.75 | 0.35 |
| B Fast Draft, draft on CPU | 15.22 | 12.64-17.43 | 6.64 | 0.47 |
| C Fast Draft, draft on iGPU | 12.03 | 10.65-13.17 | 8.67 | 0.66 |
| DP 0.6B alone, CPU | 49.46 | 46.12-51.82 | 2.27 | 0.08 |
| DG 0.6B alone, iGPU | 32.99 | 31.71-34.40 | 3.17 | 0.21 |

Paired by round: C/B median 0.781 (bootstrap 95% CI 0.694-0.849), C < B in 8/8
rounds (sign test p = 0.0078, so 1 - p = 0.992 for the direction); C/A median
1.074. Derived draft cost: t_d(P) 20.2 ms/token, t_d(iGPU) 30.3 ms/token, so the
iGPU drafts 1.5x slower than the P-cores here and the serial engine pays that on
every step. The prediction (C/B ~ 0.68, 0.55-0.85) holds in direction and its range
contains the observed 0.78; C/A came out above the predicted ~0.82 because B's
speculation gain (B/A 1.35) is large on this host.

Confidence: none in the verdict sense (INCONCLUSIVE carries no confidence). The
direction "iGPU draft is slower than the P-core draft" has 8/8 paired rounds, but
both arms ran on a throttling, shared package, so it stays a direction, not a
result. Product effect: none. `K3D_GPU_AGENT_DRAFT` stays `off` by default and
`auto` keeps the draft on the target's device, which is what the measured
t_d(P) < t_d(iGPU) would pick anyway. Not exercised: a parallel draft/verify engine
(Dovetail, DuoDecoding), AMX targets, NPU drafts, a gamma sweep.

Run 1 (06:03-06:18) was void: a probe bug (two-line host samples under `pipefail`,
and an fd glob of another user's `/proc/<pid>/fdinfo`) dropped every request record
and read P0 as 0 GPU busy, so `analyze.py` printed REFUTED from P0. That output is
not a result; the bug is fixed in `a8aeb01` and covered by a selftest in
`tests/bench_test.sh`.

Exploratory, not pre-registered and not a claim: B (same-engine CPU Fast Draft)
beat A in 8/8 rounds (median B/A 1.35) at 24% less package energy per token.
That is a separate hypothesis for a later ledger entry.

### H22 re-run (i9-13900T, 2026-10-17)

The pre-registered action for a noisy run is one re-run in a quieter window. This
is that re-run; there is no third.

**Reproduce:** commit `5e95198` (`main`; `bench/hetero-spec/` unchanged since
`3ba166d`), same host, kernel, k3d v5.9.0 / k3s v1.35.5-k3s1, OVMS 2026.4.1
(`openvino/model_server@sha256:a928903f...76fc58`), models from `~/h22-models`
(the run-2 pull). Lock taken 08:40:24 PDT. Quiet gate (1-min load <= 8 and package
<= 80 C, polled every 30 s, up to 30 min) cleared on the first poll. Probe
08:40:24-08:54:56 (870 s, 8/8 rounds, cluster deleted on exit):

```bash
# quiet gate passed: load1 3.96, package 79 C, throttle counter 738918504
SUDO_PASS=... flock -w 14400 <scratchpad>/live.lock bench/hetero-spec/probe.sh <out>
# end: load1 12.26, package 92 C, throttle counter 739045152 (+126648)
python3 bench/hetero-spec/analyze.py bench/hetero-spec/results/2026-10-17-run3.jsonl
```

Raw records: `bench/hetero-spec/results/2026-10-17-run3.jsonl` (40 requests, n = 8
rounds x 5 arms, all `tokens_ok`, none dropped).

**Verdict: INCONCLUSIVE (setup too noisy)**, applied mechanically by `analyze.py`.
Both guardrails fired again: A's median 12.7 tok/s is 19% under the 15.7 tok/s H6
calibration (band +-15%), and all 40 requests are flagged (the package throttle
counter rose during each one). The window was quiet at the start; the probe made
the noise. With 16 CPUs of target decode the 35 W package read 76-97 C and load1
8.6-13.1 during requests. A quieter window will not clear this guardrail on this
host, so the re-run budget is spent and the verdict stays INCONCLUSIVE.

P0 passed: i915 render-busy 0 ns during an A request, 8.44 s during a C request.
Per-request render time in C 7.8-9.9 s, in DG 5.2-5.4 s, in A/B/DP 0.

| Arm | median tok/s | min-max | J/token (median, package) | TTFT s (median) |
|---|---|---|---|---|
| A target alone (CPU) | 12.68 | 12.18-12.94 | 7.23 | 0.32 |
| B Fast Draft, draft on CPU | 16.71 | 15.15-18.68 | 5.92 | 0.46 |
| C Fast Draft, draft on iGPU | 11.79 | 9.97-13.50 | 8.46 | 0.62 |
| DP 0.6B alone, CPU | 53.46 | 52.89-54.29 | 2.00 | 0.08 |
| DG 0.6B alone, iGPU | 30.11 | 28.76-31.15 | 3.53 | 0.21 |

Paired by round: C/B median 0.732 (bootstrap 95% CI 0.631-0.746), per round
0.61-0.76, C < B in 8/8 (sign test p = 0.0078); C/A median 0.914, per round
0.78-1.07, C < A in 6/8. Derived draft cost: t_d(P) 18.7 ms/token, t_d(iGPU)
33.2 ms/token (1.8x). Had the guardrails passed, the outcome table would read
REFUTED on both legs (C/B <= 0.95 in 8/8, and C/A < 1.0).

Across runs 2 and 3: C < B in 16/16 paired rounds (sign test p = 3.1e-5), C/B
medians 0.78 and 0.73, both inside the pre-registered prediction (0.55-0.85).
Confidence: none in the verdict sense (INCONCLUSIVE carries no confidence); the
direction "on this host the UHD 770 draft is slower than the P-core draft" holds at
1 - p = 0.99997 over 16 rounds, all on a throttling package. Product effect: none.
`K3D_GPU_AGENT_DRAFT` stays `off`; `auto` keeps the draft on the target's device.

Missing probe for a verdict: the same 5-arm run on a package that does not throttle
under its own 16-CPU decode (a higher-TDP Raptor Lake, or this host with a cooler
that holds it under the throttle point), or a pre-registered amendment that
recalibrates A under the same thermal state before the run. Exploratory, not a claim:
B/A median 1.35 again, B > A in 8/8.

### Wave 1 host (H1-H4, i9-13900T, 2026-10-16)

H1-H4 ran on one shared, live host. These confounds apply to every wave-1 entry
below, on top of each entry's own:

- **Background load.** 52 production containers, a desktop session and other
  sessions' jobs ran throughout. Idle package power with the model loaded read
  64-80 W (71-80 W in H2); load1 at block start read 2.6-24. Arms were
  interleaved and deltas are paired per round to spread the drift, which does not
  remove it.
- **Memory pressure.** About 105 GiB of the host's 187 GiB swap was in use when
  read after the runs (`free -h`). It was not sampled per block.
- **Thermal.** Package temperature read 78-95 C. H3 saw the package
  thermal-throttle counter rise in quiet and loaded blocks alike, so absolute
  numbers are for a throttled package. H4 did not record temperature.
- **Code under test.** H2-H4 ran local merges of unmerged branches, never
  pushed; the commit hashes below cannot be fetched from the remote. The node
  image is a local build with no registry digest; all four runs used image ID
  `sha256:7e262114e919e0d0b7e6385e2726e1c09e54a95ee81054b714880452a5369aaa`
  (`Dockerfile` and `share/` were the same in each merge).
- **Harness.** The campaign scripts (`campaign*.sh`, `block.sh`, `switch.sh`,
  `h2.py`, `h3.py`, `analyze*.py`) lived in the measurement scratch directory, not
  in the repo. They drive the repo's `bench/llm_load.py` (streamed requests),
  `bench/energy.sh` (RAPL) and `bench/noisy.sh` (neighbour pod). Requests are
  samples within a block; the effective n for spread is the number of blocks.

### H1 run (i9-13900T, 2026-10-16)

```text
# provenance
date:            2026-10-16T16:14Z..16:43Z (EAT stack deploy finished 16:22Z, model Ready, eat-check passed 16:42Z)
hypothesis:      H1
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores; AVX2 and VNNI, no AVX-512, no AMX)
host kernel:     6.6.128-1-MANJARO
accelerators:    "Accelerators schedulable: nvidia=2 intel=1 hybrid=1"
k3d-gpu commit:  claude/eat-on-k3d 88eebd2 (launcher 59ebb5e, integrations/eat 467990d); merged to main as ccaa3b9 (#33)
node image:      k3d-gpu:eat-59ebb5e, image ID sha256:7e262114e919e0d0b7e6385e2726e1c09e54a95ee81054b714880452a5369aaa (k3s v1.34.1+k3s1)
model:           Qwen/Qwen3-1.7B, bf16 (revision not recorded)
runtime:         EAT release-1.0.3-hotfix 0d3438d7119de44cafc5903f66665c0b623e3333; vllm/vllm-openai-cpu:v0.24.0 (tag only, not
                 digest-pinned in this run), KV cache 6 GiB, `performance` balloon cpu=8 (CPUs 0-7)
energy source:   not measured
runs per arm:    1
```

Pre-registration: the H1 row of the Hypotheses table, unchanged since it was
first committed at 04:48 PDT (406a8c4, on the claims-guard branch, merged to
`main` as dd34d06), before this run (09:14-09:43 PDT). The run is graded against
that row's prediction and probe.

Verdict: **INCONCLUSIVE**, confidence 0.7 that a clean pass would hold.

What was observed:

- `k3d-gpu up` with `K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true`
  and `K3D_GPU_K3S_ARGS=--disable=servicelb`; the EAT stack took about 8 min of
  ansible, 5 PLAY RECAPs, all `failed=0`.
- The model pod reached Ready about 6 min after the model step
  (`kubectl wait` on that pod: condition met).
- `POST /v1/chat/completions` through LiteLLM via EAT's ingress-nginx: HTTP 200,
  `finish_reason` stop, 20 prompt / 27 completion tokens, `system_fingerprint`
  vllm-0.24.0. A tool call returned `finish_reason` tool_calls.
- `integrations/eat/eat-check.sh`: the model wrote Python, EAT's agent-sandbox
  ran it (stdout `385`, exit 0), `==> EAT check passed.`
- EAT's bf16 vLLM settings ran on a CPU with no AVX-512 or AMX (oneDNN fell back
  to torch linear). The `ei-*` node labels EAT needs are not Xeon labels.

Why not SUPPORTED:

- The first model attempt crash-looped (a 4 GiB KV cache is below the 4.38 GiB a
  40960-token context needs). The model step ran again on the same cluster after
  `helm uninstall`, with 6 GiB. The documented steps were never run once,
  uninterrupted.
- `kubectl wait --for=condition=Ready pod --all -A` was not run; readiness was
  recorded per component, not for every pod.
- The host was a shared live host, not a fresh one.
- Speed and energy were not measured; the claim is reachability only.

Confounds: those of the wave-1 host; plus n=1, and an independent reviewer
did not re-run the deploy (it confirmed the tag-to-commit mapping, the rendered
values and P-core placement on a separate cluster).

```bash
# Reproduce on a fresh cluster, in one pass. Host needs docker, k3d, kubectl, curl, jq.
lscpu | grep 'Model name'; uname -r
git rev-parse HEAD
IMAGE=k3d-gpu:eat-$(git rev-parse --short HEAD)
docker build -q -t "$IMAGE" .
docker image inspect --format '{{.Id}}' "$IMAGE"
K3D_GPU_IMAGE="$IMAGE" \
K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true \
K3D_GPU_K3S_ARGS=--disable=servicelb scripts/k3d-gpu up
EAT_WORK=$PWD/eat-work integrations/eat/eat-up.sh
EAT_WORK=$PWD/eat-work integrations/eat/eat-up.sh model cpu-qwen3-1-7b
kubectl wait --for=condition=Ready pod --all -A --timeout=30m
EAT_WORK=$PWD/eat-work integrations/eat/eat-check.sh   # expect: ==> EAT check passed.
scripts/k3d-gpu down
```

### H2 run (i9-13900T, 2026-10-16)

```text
# provenance
date:            2026-10-16T16:46Z..20:05Z; main A/B 17:29Z..18:32Z, co-run 18:35Z..19:18Z, vLLM 19:25Z..20:02Z
hypothesis:      H2 (the A/B data is shared with H6)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores), Lenovo P360 Ultra
host kernel:     6.6.128-1-MANJARO
accelerators:    Intel UHD Graphics 770 (i915, no XMX) as gpu.intel.com/i915; hybrid CPU; "nvidia=2 intel=1 hybrid=1"
k3d-gpu commit:  94f71e11d8a5a760010a028aaadcdb44df8df73e, local merge of claude/agent-stack 8385dc2 and
                 claude/report-bench afd7e56 (never pushed)
node image:      k3d-gpu-h2:94f71e1, image ID sha256:7e262114e919e0d0b7e6385e2726e1c09e54a95ee81054b714880452a5369aaa (k3s v1.34.1+k3s1)
model:           OpenVINO/Qwen3-4B-int4-ov at b467368d16b75df14055562fe927ae8e1f15f7ef,
                 openvino_model.bin sha256 074214fa29ab1b479536fc7184ef3036e7b355a5835e8590b61b70a8de1df2f9
runtime:         openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58 (2026.4.1-gpu),
                 --target_device=GPU (pod CPUs 0-1) vs --target_device=CPU (pod CPUs 0-15);
                 secondary: vllm/vllm-openai-cpu:v0.24.0@sha256:b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1,
                 Qwen/Qwen3-4B-Instruct-2507 bf16, EAT 0d3438d chart values (KV 6 GiB, max-model-len 32768, cpu 16)
energy source:   /sys/class/powercap/intel-rapl:0 (package-0, includes the iGPU) via bench/energy.sh;
                 turbostat 2026.04.21 PkgWatt agreed within 0.1 W
runs per arm:    5 rounds; per round 5 decode, 5 prefill and 8 four-in-flight requests (vLLM: 2 blocks)
```

Pre-registration, written ~10:20 PDT after an exploratory CPU tuning sweep and
before the main campaign:

```text
A = GPU: agent product manifest (UHD 770, 2-CPU P-core balloon = CPUs 0-1)
B = CPU-tuned: agent CPU manifest (16-CPU P-core balloon 0-15). Sweep (n=5 each) chose
    OV default LATENCY (8 threads, HT off, KV u8, dyn-quant 32) over HT16, all-cores-8thr,
    all-cores-23thr.
C = EAT vLLM CPU, bf16, Qwen3-4B-Instruct-2507. Secondary; different precision and
    checkpoint; measured after A/B.
Workloads (temperature 0, ignore_eos, streamed): decode 30 x 256, c=1; prefill unique
~1936 x 16, c=1 (leading nonce defeats the prefix cache); conc4 30 x 128, 4 in flight.
5 rounds, order ABBAABBAAB, warm-up excluded. Energy: RAPL package-0; 20 s idle before
each block; incremental J = E - P_idle * t.
P1 decode tok/s: B > A by >= 20%.      P2 prefill TTFT: A >= 2x slower than B.
P3 conc4 aggregate tok/s: B > A.       P4 decode incremental J/token: B < A.
P5 prefill incremental J/request: A < B.
P6 CPU offload: A ~1 CPU-s/s, B ~8 CPU-s/s.
Any |delta| smaller than the block-to-block spread -> INCONCLUSIVE for that metric.
```

Outcome: P1, P2, P3, P4 and P6 held; P5 failed and reads as a tie. The
hypothesis (the iGPU wins speed and energy) is **REFUTED**, confidence 0.9.

Per-request values are median / p90 / [min, max] over n requests; energy and
CPU rows are over n=5 blocks. Paired = per-round delta, median [range].

| Workload | Metric | GPU (UHD 770) | CPU (16 P-CPUs) | Paired GPU vs CPU |
|----------|--------|---------------|-----------------|-------------------|
| decode | decode tok/s (n=25) | 9.88 / 9.94 / [9.53, 9.99] | 15.67 / 15.79 / [15.41, 15.82] | -37% [-36.3, -39.3] |
| decode | TTFT, s (n=25) | 0.418 / 0.635 / [0.384, 0.673] | 0.245 / 0.259 / [0.232, 0.277] | |
| decode | total package J/token | 8.86 / 9.11 / [8.69, 9.11] | 5.32 / 5.40 / [5.26, 5.40] | |
| decode | incremental J/token | 1.344 / 1.584 / [0.633, 1.584] | 0.655 / 0.727 / [0.617, 0.727] | +0.728 J [-0.079, +0.940] |
| decode | package W (idle W) | 86.0 (72.6, range 71.2-80.0) | 82.5 (72.2, range 70.9-73.0) | |
| prefill | TTFT, s (n=25) | 29.47 / 30.05 / [29.29, 30.09] | 11.22 / 11.74 / [11.12, 12.11] | +163% |
| prefill | incremental J/request | 432 / 477 / [122, 477] | 420 / 430 / [407, 430] | +3.0% [-71, +17], tie |
| conc4 | aggregate tok/s | 30.35 / 30.40 / [29.33, 30.40] | 56.03 / 56.43 / [55.28, 56.43] | -46% |
| all | pod CPU-s per s | 1.00 | 7.75-7.88 | |

The GPU round-1 idle read 80.0 W, which makes that round's incremental energy
look twice as good as the others; the total J/token and a pooled-idle
cross-check (1.39 vs 0.65 J/token) agree with the paired median.

EAT vLLM CPU arm (secondary): 4.63 / 4.64 / [4.57, 4.65] decode tok/s (n=10),
prefill TTFT 131.1 s (n=10), 9.99 aggregate tok/s at 4 in flight, 19.37 total
J/token. **This is not an engine comparison.** The arm differs from the OVMS CPU
arm in weight precision (bf16, 7.49 GiB of weights, against int4, about 2.3 GB),
in checkpoint (Qwen3-4B-Instruct-2507 against Qwen3-4B), and in config (EAT's
`--enforce-eager` and chunked prefill 2048, bf16 computed through torch linear
on a CPU without native bf16). It ran after the A/B with 2 blocks, not
interleaved. Both engines decoded at about the same effective weight bandwidth
(about 36 GB/s for OVMS int4, 37 GB/s for vLLM bf16), which is what the
precision gap alone predicts. The ratio of the two decode numbers must not be
quoted as an OVMS-over-vLLM speedup. H7 is the matched-precision re-run.

Exploratory, added after the pre-registration (3 blocks per cell; stress-ng
0.22.01 `--matrix` on 8 E-core CPUs, LLM decoding back to back):

| Co-runner | Solo bogo-ops/s | With GPU LLM | With CPU LLM |
|-----------|-----------------|--------------|--------------|
| E-cores (CPUs 17-24) | 19065 [18807, 19218] | 18043 (-5.4%) | 16154 (-15.3%) |

LLM decode was unchanged by the co-runner in both modes (GPU 9.92, CPU 15.76
tok/s). Solo baselines were measured only in GPU rounds and not interleaved with
the CPU co-run blocks.

Confounds, beyond the wave-1 host:

- The tuning was asymmetric: the CPU arm had a 4-config sweep, the GPU arm kept
  product defaults (f16, KV u4, dynamic quant 32) and only its host CPUs were
  varied (2 vs 4: 9.81 vs 9.79 tok/s). The iGPU ran at GFX%rc6 0.0 and 1650 MHz
  throughout, so it was saturated, but a GPU-side sweep was not run.
- The 20 s idle baseline is taken with the model loaded, and the iGPU also
  drives the display.
- load1 includes the engine's own threads, so CPU-mode blocks read higher by
  construction.
- CPU-mode prefill ran at 91 C package temperature.

```bash
# Reproduce. Harness scripts: see "Wave 1 host" above; repo pieces only shown here.
lscpu | grep 'Model name'; uname -r
git rev-parse HEAD                    # the run used local merge 94f71e1
IMAGE=k3d-gpu-h2:$(git rev-parse --short HEAD)
docker build -t "$IMAGE" .
K3D_GPU_IMAGE="$IMAGE" scripts/k3d-gpu up          # expect: Accelerators schedulable: ... intel=1 hybrid=1
K3D_GPU_AGENT_DEVICE=GPU scripts/k3d-gpu agent manifest > gpu.yaml
K3D_GPU_AGENT_DEVICE=CPU scripts/k3d-gpu agent manifest > cpu.yaml
# Per block: apply one manifest, wait Ready, 1 decode + 1 prefill warm-up (excluded), then
bench/energy.sh --json idle.json -- sleep 20
bench/energy.sh --json decode-energy.json -- \
  bench/llm_load.py --base-url "http://$NODE_IP:$NODE_PORT/v3" --model Qwen3-4B-int4-ov \
  --requests 5 --concurrency 1 --max-tokens 256 --ignore-eos --warmup 0 --out decode.json
# prefill: same with a unique ~1936-token prompt per request and --max-tokens 16;
# conc4: --requests 8 --concurrency 4 --max-tokens 128. Order ABBAABBAAB, 5 rounds.
# Pod CPU: read the pod cgroup's cpu.stat usage_usec before and after each block.
scripts/k3d-gpu down
```

### H3 run (i9-13900T, 2026-10-16)

```text
# provenance
date:            2026-10-16T20:00Z..22:25Z; main campaign 20:24Z..21:53Z, mechanism probes 21:53Z..22:25Z
hypothesis:      H3
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P = CPUs 0-15, 8 cores x 2 threads; E = 16-31)
host kernel:     6.6.128-1-MANJARO
accelerators:    hybrid CPU; "nvidia=2 intel=1 hybrid=1"; kube-system reserve on CPU 16
k3d-gpu commit:  a7d90fc, local merge of claude/hybrid-core-balloons fb5c17d, claude/agent-stack 8385dc2 and
                 claude/report-bench afd7e56 (never pushed)
node image:      k3d-gpu-h3:a7d90fc, image ID sha256:7e262114e919e0d0b7e6385e2726e1c09e54a95ee81054b714880452a5369aaa (k3s v1.34.1+k3s1)
model:           OpenVINO/Qwen3-4B-int4-ov (as H2)
runtime:         openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58, --target_device=CPU,
                 request 15 CPU, no limit; neighbour ghcr.io/colinianking/stress-ng@sha256:2ea9a7c3828eda1b1ca3d0b0c5a669ec252f9caf97a6a33aedcea6b2a66d1cbe
                 (0.22.01), --cpu 15, request = limit = 15 CPU
energy source:   turbostat 2026.04.21 PkgWatt only (no per-block RAPL joules)
runs per arm:    5 blocks x 5 requests per workload (35 blocks, 350 requests, all ok)
```

Pre-registration, written ~13:20 PDT after placement probes and before any
timed block:

```text
Probed placements (quiet): performance -> cpuset 0-14 (OV 8 busy threads, 1 per P-core);
efficient -> 17-31 (15 threads); unlabelled -> shared balloon 0-15,17-31 (8 threads on P-CPUs).
Conditions (inference label / neighbour): Q performance/none, Qu unlabelled/none,
Qe efficient/none, U unlabelled/unlabelled, P performance/efficient,
X efficient/performance, PM performance/stress-ng --stream 15 on efficient [secondary].
decode ~30 x 128, prefill unique ~1000 x 16; 5 sequential requests each per block.
5 rounds x 7 conditions, order rotated each round and reversed on even rounds; fresh
OVMS pod and warm-up per block.
Pass: median latency(P) within +10% of Q on BOTH workloads, AND U > Q by more than 10%
on at least one workload.
P > Q+10% but P << U -> partial: balloons help but do not fully isolate; report residual.
X vs Qe > 10% -> isolation asymmetric.  PM > Q+10% -> CPU balloons do not isolate
memory-bandwidth neighbours.  |delta| < block-to-block spread -> INCONCLUSIVE.
```

Mechanism pre-note (written ~14:10 PDT, after interim rounds 1-2, before
running): prefill only, inference on the P-cores, E-core neighbour variants none,
`--cpu-method all`, `loop` (register-only) and `matrixprod`. "Ring/uncore clock
or turbo budget is the mechanism -> loop also slows prefill by more than 10%, and
UncMHz drops. Cache/ring traffic -> loop stays close to none."

Outcome: decode passed, prefill failed, so H3 is **REFUTED**, confidence 0.85.
It lands in the pre-registered "partial" cell: pinning beats no labels but does
not isolate.

Latency in seconds, median / p90 / [min, max], n=25 requests per arm; block
medians give the spread (n=5 blocks).

| Arm | Decode latency | Decode tok/s | Prefill latency | Prefill block medians |
|-----|----------------|--------------|-----------------|-----------------------|
| Q | 8.387 / 9.238 / [8.136, 9.753] | 15.68 | 6.582 / 7.086 / [6.255, 7.726] | 6.43-6.90 |
| Qu | 8.347 / 8.602 / [8.058, 8.734] | 15.64 | 6.404 / 6.632 / [6.162, 6.946] | 6.27-6.59 |
| Qe | 10.790 / 12.477 / [10.079, 12.649] | 12.27 | 11.810 / 12.458 / [11.201, 12.832] | 11.37-12.47 |
| U | 9.380 / 9.805 / [8.830, 10.361] | 14.10 | 10.096 / 10.932 / [9.078, 11.632] | 9.50-11.09 |
| P | 8.887 / 10.239 / [8.531, 10.906] | 14.83 | 8.726 / 9.883 / [7.738, 10.558] | 7.84-9.34 |
| X | 14.999 / 18.957 / [12.754, 20.348] | 8.85 | 19.196 / 23.563 / [15.624, 25.362] | 16.11-23.42 |
| PM | 22.436 / 24.445 / [21.165, 25.339] | 5.82 | 13.330 / 15.444 / [12.523, 16.153] | 12.67-15.50 |

| Comparison | Decode, paired median [range] | Prefill, paired median [range] |
|------------|-------------------------------|--------------------------------|
| P vs Q (pass needs <= +10%) | +5.8% [-3.5, +22.4] | +33.6% [+21.8, +43.2] |
| U vs Q | +8.5% [+2.0, +16.4] | +49.8% [+45.0, +70.0] |
| P vs U | -2.8% [-6.5, +5.1] | -15.8% [-18.8, -7.9] |
| X vs Qe | +31.1% [+8.7, +87.9] | +64.9% [+34.6, +100.9] |
| PM vs Q | +165.8% [+139.8, +190.5] | +99.1% [+83.5, +136.7] |

Mechanism probes (prefill, n=20 per variant): none 7.19 s, `all` +27.3%, `loop`
+24.4%, `matrixprod` +38.4%; UncMHz 2900 to 2750-2800, busy P-core MHz -3 to -5%,
package W 104 to 111-114. By the pre-note's own rule, `loop` slowing prefill by
more than 10% with UncMHz dropping reads as package power or clock coupling.
The clock loss accounts for only about 3.5-8%. A second probe found the
hyperthread siblings of the inference CPUs went from 13.5-15.5% to 27-48% busy,
and `ps` caught host processes (browser, `shellcheck`, `redis-server`,
`grafana`) on CPUs 0-14 in about 40 samples against 7 without the neighbour. Balloons
only set cpusets inside the k3d node's cgroup, so host processes are not
confined. Host spill is the likelier main cause of the residual; that
attribution is post-hoc and correlational, not isolated by a causal test.

Confounds, beyond the wave-1 host:

- The P decode pass rests on the median: its paired range reaches +22.4%.
- U's decode effect (+8.5% paired) is inside the spread; U meets its leg on
  prefill only, which the pre-registration allows.
- The neighbour is quota-capped at 15 CPU; an unlimited, bursty one may hurt U
  more.
- An equal 15-CPU request is not equal hardware: 8 OV threads on 7.5 physical
  P-cores against 15 threads on 15 E-cores.
- The stream neighbour's actual DRAM bandwidth was not measured. The CPU has no
  RDT, CAT or MBA (`/proc/cpuinfo`, no `/sys/fs/resctrl`).
- Decode always ran before prefill within a block; the prefill-only mechanism
  probes still show +24-27%, so order is not the main cause.

```bash
# Reproduce. Harness scripts: see "Wave 1 host" above; repo pieces only shown here.
lscpu | grep 'Model name'; uname -r
git rev-parse HEAD                    # the run used local merge a7d90fc
IMAGE=k3d-gpu-h3:$(git rev-parse --short HEAD)
docker build -q -t "$IMAGE" .
K3D_GPU_IMAGE="$IMAGE" scripts/k3d-gpu up          # expect: ... hybrid=1
K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_CPUS=15 scripts/k3d-gpu agent manifest > inf-performance.yaml
# inf-efficient / inf-none: same manifest with the k3d-gpu.io/cores label set to efficient or removed.
# Per block: apply the arm's manifest, wait Ready, warm up (excluded), start the neighbour, then
bench/noisy.sh start --cores efficient --workers 15 --cpu 15      # P; U: --cores none; X: --cores performance
kubectl exec -n k3d-gpu-agent deploy/ovms -- grep Cpus_allowed_list /proc/1/status
bench/llm_load.py --base-url "http://$NODE_IP:$NODE_PORT/v3" --model Qwen3-4B-int4-ov \
  --requests 5 --concurrency 1 --max-tokens 128 --ignore-eos --warmup 0 --out decode.json
# prefill: unique ~1000-token prompt per request, --max-tokens 16
bench/noisy.sh stop
sudo turbostat --quiet --show CPU,Busy%,Bzy_MHz,PkgWatt,CorWatt,PkgTmp --interval 5 > turbostat.log &
scripts/k3d-gpu down
```

### H4 run (i9-13900T, 2026-10-16)

```text
# provenance
date:            2026-10-16T22:27Z..2026-10-17T01:12Z; H4b 22:33Z..00:03Z, H4c 00:03Z..01:12Z
hypothesis:      H4
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores)
host kernel:     6.6.128-1-MANJARO
accelerators:    Intel UHD Graphics 770 (i915, no XMX) as gpu.intel.com/i915; hybrid CPU; "nvidia=2 intel=1 hybrid=1"
k3d-gpu commit:  4201b10e4fc5be7f31be359dfca9bc20579c3a07, local merge of claude/agent-stack 8385dc2 and
                 claude/report-bench afd7e56 (never pushed)
node image:      k3d-gpu:agent-dev, image ID sha256:7e262114e919e0d0b7e6385e2726e1c09e54a95ee81054b714880452a5369aaa (k3s v1.34.1+k3s1)
model:           OpenVINO/Qwen3-4B-int4-ov (as H2)
runtime:         openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58;
                 G = auto (GPU, i915, CPUs 0-1), C16 = CPU on CPUs 0-15, C8 = CPU on 0-7 (K3D_GPU_AGENT_CPUS=8),
                 Ce = CPU on E-CPUs 17-31 (product manifest with cores=efficient and cpu 15, edited by sed)
energy source:   /sys/class/powercap/intel-rapl:0 (package-0) via bench/energy.sh; no turbostat
runs per arm:    5 rounds x (5 decode, 5 prefill, 8 four-in-flight) requests; 450 requests, all ok
```

Pre-registration (H4 15:27 PDT, H4b 15:30 PDT before any run, H4c 16:22 PDT
before any Ce run):

```text
Objectives, each on H2's method, n >= 5 interleaved, median: O1 single-stream speed
(decode tok/s, TTFT); O2 aggregate tok/s at c=4; O3 incremental J/token (decode) and
J/request (prefill); O4 CPU offload; O5 long-prompt TTFT.
Stated objective of auto (README, claude/agent-stack 8385dc2): "auto still picks the GPU,
because it leaves the P-cores free for everything else."
H4b: G vs C16 vs C8, rotated order. Q1 C8 decode >= G. Q2 C8 prefill TTFT < G.
Q3 C8 ~3.9-4 CPU-s/s. Q4 G and C16 within 10% of H2 (a >20% shift means a measurement bug).
H4c: G vs Ce, ABAB. R1 Ce decode > G by >= 10%. R2 Ce prefill TTFT < G.
R3 Ce incremental J/token < G. R4 Ce ~12-16 CPU-s/s vs G ~1.
R1 and R2 true -> for "keep P-cores free" the GPU is not best on this host.
```

Outcome: Q1-Q4 and R1, R2, R4 held; R3 held on decode and tied on prefill.
Against the ledger row, `auto` (G) lost tok/s to every forced CPU arm by far more
than the spread: H4 is **REFUTED**, confidence 0.85.

Medians of n=25 requests (decode, prefill) or n=5 blocks (aggregate, J, CPU);
[min, max] in brackets.

| Metric | G (auto) | C16 | C8 | Ce |
|--------|----------|-----|----|----|
| decode tok/s, H4b | 10.50 [9.81, 10.74] | 15.60 [14.21, 16.04] | 13.47 [12.50, 14.10] | |
| decode tok/s, H4c | 10.69 [10.61, 10.77] | | | 13.01 [12.76, 13.16] |
| decode TTFT, s | 0.388 (H4b), 0.381 (H4c) | 0.248 | 0.365 | 0.432 |
| prefill TTFT, s | 27.45 [27.04, 28.26] (H4b), 27.21 (H4c) | 11.13 [10.75, 12.22] | 19.54 [18.48, 20.92] | 20.38 [19.92, 21.16] |
| conc4 aggregate tok/s | 32.64 (H4b), 32.23 (H4c) | 56.45 | 48.01 | 42.46 |
| decode total J/token | 8.61 (H4b), 8.47 (H4c) | 5.65 | 6.72 | 6.92 |
| pod CPU-s per s | 1.00 | 7.84 | 3.95 | 14.60 |

Paired per-round deltas, median [range]: decode aggregate C16 vs G +47.2%
[+44.2, +51.5], C8 vs G +28.5% [+25.0, +29.8], Ce vs G +21.0% [+20.4, +21.4];
prefill TTFT C16 vs G -59.4% [-60.2, -56.1], Ce vs G -25.2% [-25.8, -24.5];
decode TTFT Ce vs G +14.0% [+7.9, +16.6] (G's one latency win, about 51 ms).
Prefill incremental J/request tied: G 374, C16 379 (H4b); G 671, Ce 667 (H4c,
paired +0.9% [-7.4, +14.2]). Staleness check against H2 passed (G +6%, C16
-0.4%).

Against the README's stated objective, Ce leaves all 16 P-core CPUs free and
still beats G on decode, prefill, concurrency and decode energy. Whether that
answers "free for everything else" is an interpretation: Ce burns 14.6 CPU-s/s
of E-cores where G burns 1.0, and its cost to co-tenants was not measured. The
REFUTED verdict rests on the ledger row (highest tok/s), not on that reading.

Corrections to the run's own summary: it counted 330 requests (the raw files
hold 450, all ok); it gave "G -21% decode" against Ce, where G/Ce is -17.5% and
+21% is Ce over G; and its prefill "J per request" figures were total J per
output token over 16 tokens.

Confounds, beyond the wave-1 host:

- Idle package power stepped down about 10 W partway through H4b; incremental
  J ranges overlap, so the energy verdict uses total J/token from the same
  paired windows, whose ranges do not overlap between arms.
- In H4b rounds 1 and 2, C8 ran back to back on one pod; every other switch
  replaced the pod.
- Ce is a hand-edited manifest; the launcher had no cores knob.
- Package temperature was not recorded.
- The `auto` rule tested is 8385dc2's. `main`'s XMX-gated rule was written
  after this data, so this run does not test it.

```bash
# Reproduce. Harness scripts: see "Wave 1 host" above; repo pieces only shown here.
lscpu | grep 'Model name'; uname -r
git rev-parse HEAD                    # the run used local merge 4201b10
IMAGE=k3d-gpu:agent-dev
docker build -t "$IMAGE" .
K3D_GPU_IMAGE="$IMAGE" scripts/k3d-gpu up
scripts/k3d-gpu agent up              # record the auto choice it logs
K3D_GPU_AGENT_DEVICE=GPU scripts/k3d-gpu agent manifest > g.yaml
K3D_GPU_AGENT_DEVICE=CPU scripts/k3d-gpu agent manifest > c16.yaml
K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_CPUS=8 scripts/k3d-gpu agent manifest > c8.yaml
sed -e 's/cores: performance/cores: efficient/' -e 's/cpu: "16"/cpu: "15"/' c16.yaml > ce.yaml
# Per block: the H2 block (energy.sh idle 20 s, then decode, prefill, conc4 through llm_load.py).
# H4b: G C16 C8 in 5 rotated orders; H4c: Ce G alternating, 5 rounds.
scripts/k3d-gpu down
```

### H25 pre-registration

Written 2026-10-17T05:08-07:00, before any H25 measurement. Nothing below may
change after the first live round; a change voids the run.

**Premise check.** OVMS 2026.4.1 `--pull` already writes
`enable_prefix_caching: true` and `cache_size: 0` (the pinned image's `--help`
prints `--enable_prefix_caching ... (default: true)` and `--cache_size ...
(default: 0)`, checked 2026-10-17 [provenance:P253]). The k3d-gpu agent
manifest uses `--pull`, so prefix caching is on today. "Auto-enable" adds
nothing; the baseline is the shipping default and caching OFF is the negative
control. What H25 can add is a measured cost per added agent, and the
deploy-time detector `k3d-gpu agent prefix`, which reads agent manifests
before any request and reports the shared, block-aligned prefix.

Outside the lock (no cluster, no load):
1. Stage `OpenVINO/Qwen3-4B-int4-ov` at revision b467368d16b75df14055562fe927ae8e1f15f7ef into a scratch dir and check `openvino_model.bin` against the H6 header.
2. `docker run --rm $IMAGE --help` on the pinned OVMS digest; grep `enable_prefix_caching` and `cache_size` (done above).
3. Prompts come from `bench/shared-prefix/probe.py gen` with fixed seeds, one prefix seed per round: system text plus 12 EAT-style tool schemas with frozen key order, Ps calibrated to 1536 ± 32 rendered tokens, a 96-token private message per agent, and the perturbed copy c with one word changed at system position 16.

Inside `flock -w 10800 .../live.lock` (wait first until no `k3dgpu-f-*`
cluster exists), wall-time cap 20 min, hard abort at 18:30 elapsed. On abort
the cluster is deleted and rounds not run count as missing.
- Cluster `k3dgpu-i-shared-prefix`, scratch KUBECONFIG, model dir copied into the node and used as a hostPath (A1), OVMS image imported.
- The agent CPU manifest with one change: models come from a hostPath instead of the PVC. Image, args, P-core balloon, 16 CPUs and `--log_level=INFO` are the same in both arms.
- Arm ON: no extra pull args. Arm OFF: `--enable_prefix_caching=false` on the pull container. graph.pbtxt is read back after every init; a mismatch voids the round.

Every round runs on a fresh pod, in the order ON OFF OFF ON ON OFF:
0. Confounds: RAPL energy_uj start, 1-min load, swap used, package_throttle_count summed over CPUs, running container count.
1. Warm-up: one unique 16-token request, max_tokens 4, not scored.
2. Phase S, concurrency 1: agents a1..a6, then c. Stream with include_usage, max_tokens 8, `chat_template_kwargs.enable_thinking=false`. TTFT = time to the first content or reasoning delta. a1 is the cold reference; a2..a6 are the added-agent samples (5 per round, 15 per arm).
3. Phase B: 6 new agents b1..b6, same prefix seed, new private messages, released on a barrier. Pod `memory.current` and `memory.swap.current` sampled at 1 Hz from 5 s before release to the last response. ΔRSS per agent = (peak − pre-burst) / 6.
4. RAPL energy_uj end and throttle delta. The cluster is deleted before the lock is released.

Statistics: T ratio = median OFF added-agent TTFT / median ON added-agent TTFT;
95% CI from 10,000 bootstrap resamples over the 15 + 15 samples; spread =
max − min of the per-round medians; confidence = share of the 9 cross-round
ON/OFF pairs whose ratio is ≥ 4x, reported next to the CI.

Guardrails (a breach voids that round, not the hypothesis): > 2% request
errors or a null TTFT; `usage.prompt_tokens` not identical across agents and
arms; empty output within max_tokens 8; graph.pbtxt mismatch;
`memory.swap.current` > 0 during phase B (voids M for that round). More than 1
of 3 rounds voided in an arm makes the arm INCONCLUSIVE. Per-arm spread > 30%
of the median makes that part INCONCLUSIVE.

| Outcome | Prior | Conclusion | Action |
|---------|-------|------------|--------|
| T: CI lower bound ≥ 4x, c ≥ 0.8x OFF, a1 ON within ±15% of OFF | 0.85 | Cross-agent prefix reuse works on the shipping default; T SUPPORTED | Docs may cite the added-agent TTFT as [ledger:H25]; no auto-enable code (already on); ship canonical-prefix guidance |
| T: 2x ≤ ratio < 4x, or CI straddles 4x | 0.07 | Reuse happens, but fixed cost T0 (template, tokenizer, parser) is large | INCONCLUSIVE for 4x; next probe: T0 by tool-schema size |
| T: ratio < 2x, c ≈ ON agents | 0.04 | REFUTED: no reuse across agents | Dump and diff rendered prompts; no claim; do not re-attack until the cause is named |
| c measures warm (< 0.5x OFF) | 0.01 | Probe defect | Run void; fix the generator |
| Guardrail or spread breach in ≥ 2 rounds of an arm | 0.03 | INCONCLUSIVE (host noise) | Re-run at lower load; report confounds |
| M: ON ΔRSS per agent ≤ 0.5x OFF | 0.35 | Shared blocks also save resident memory per live agent | Claim MiB per added agent [ledger:H25] |
| M: ON ΔRSS per agent ≥ 0.8x OFF | 0.50 | Sharing does not show in RSS; dynamic-pool growth reserves full prompts | No memory claim; follow-up tests static `cache_size` |
| M: 0.5x < ratio < 0.8x | 0.15 | Partial; 256-token growth steps blur it | INCONCLUSIVE for M; report the raw ΔRSS curve |

Generalization (model prediction, not measured): the T ratio ≈ (Ps+Pp)/(Pp +
T0·rate) is first-order SKU-invariant on the CPU path, and the absolute saving
per added agent is Ps / prefill rate, so it is largest on low-core parts
(Alder Lake-N) and smallest on AMX Xeons. The Core Ultra iGPU path uses
16-token blocks and u4 KV, so its memory per token differs and is untested.

**Amendment A1** (2026-10-17, before any live round; nothing measured yet).
Two refuters reviewed b3e2797. Agreed and fixed:
- Calibration. chars/4 was wrong for this vocabulary: private messages were
  55-61 Qwen3 tokens (not 96) and varied within a seed; the head varied
  1319-1330 across seeds, so every round would void on the
  identical-`prompt_tokens` guardrail. `gen()` now builds every text from a
  fixed number of words, each one Qwen3 token. Checked offline with the staged
  model's own `tokenizer.json` and `chat_template.jinja` (tokenizers 0.22.2,
  jinja2 3.1.6): for seeds 2501-2506 every agent prompt is 1634 tokens, the
  shared head is Ps = 1538 (inside 1536 ± 32), every private message is
  Pp = 96, and c is also 1634. `test_probe.py` asserts this when
  `H25_TOKENIZER_DIR` is set, and asserts the seed-invariant word shape always.
- Model dir: it is copied into the node with `docker cp` and used as a
  hostPath, not mounted with `-v`.
- The OFF `sed` was checked offline against `k3d-gpu agent manifest` output:
  the line `            - --task=text_generation` exists exactly once.
- Detector (`k3d-gpu agent prefix`): real token counts with `--tokenizer`;
  device labelled as assumed when `K3D_GPU_AGENT_DEVICE` is auto; warning on
  non-OVMS backends; groups under `--min-tokens` reusable tokens are dropped;
  non-UTF-8 and empty inputs exit 2. Greedy grouping is documented, not changed.

Agreed and not fixed in this run: the probe measures OVMS's shipped prefix
caching against a negative control, so a pass is a per-added-agent datapoint
on this host, not an invention. It does not test one shared engine versus N
per-agent engines, or static versus dynamic `cache_size`. Those arms are the
follow-up. No "auto-enable" claim is possible: prefix caching is already on.

**Amendment A2: matched background load** (written 2026-10-17T19:40Z, after run 1
and before any matched-load round; nothing below has been measured). Run 1 ran
under uncontrolled load (other workflows' clusters, 1-min load 18-29) that
drifted between arms. Real Intel boxes are busy, so A2 does not ask for an idle
host; it puts every arm under the SAME controlled background load and records it.
- **Load.** `bench/load/load.py` (#95), stress-ng 0.22.01 pinned by digest, on
  the E-cores 16-31 (`load.py cpus`); OVMS stays on the 16-CPU P-core balloon.
  Levels, in this order: `typical` (headline) then `heavy` (robustness), the
  second only if the lock budget allows. One cluster serves both levels; the
  load runs for the whole level (started before cluster setup for `typical`,
  switched with a 30 s settle before the first `heavy` round).
- **Order.** Interleaved `load.py order ON OFF --reps 3` = ON OFF ON OFF ON OFF
  per level (replaces ON OFF OFF ON ON OFF). A fresh pod and a new prefix seed
  every round as before; seeds 2511-2516 (`typical`) and 2521-2526 (`heavy`).
  n = 3 rounds per arm per level, 15 added-agent samples per arm. Cap: 20 min
  of lock time per level (18:30 no-new-round rule; the first level includes
  cluster setup). Rounds not run count as missing, under the same arm rules.
- **Load record.** Each round: `load.py sample --seconds 10 --windows 2 --tag
  ARM --level LEVEL` after the pod is Ready and before the warm-up request, so
  the windows hold the background the arm runs under with the agent idle, not
  the arm's own prefill. Each window: load-CPU busy %, 1-min load, RAPL
  package W, core throttle events/min, swap used and swap growth. RAPL energy,
  throttle delta, 1-min load, swap and container count per round as in run 1.
- **Matching rule** (fixed in `load.py` TOLERANCE before this amendment): per
  level, the spread across arms of the per-arm means must stay within busy
  ±5 points, 1-min load max(1, 15%), package W max(3 W, 10%), throttle/min
  max(10, 25%), swap used ±256 MiB, swap growth ±64 MiB per window. Unmatched
  load makes that level INCONCLUSIVE and names the metric; `probe.py score
  --load` applies this. High load alone never voids a round. Known risk,
  stated now and not adjusted later: the 1-min load still carries part of the
  previous round's prefill when a window starts.
- **Unchanged.** Every prediction, threshold, prior and guardrail in the
  pre-registration and A1 (T: CI lower bound ≥ 4x, c ≥ 0.8x OFF, a1 ON within
  ±15% of OFF; M bands 0.5x / 0.8x). The guardrail action "re-run at lower load"
  becomes "re-run at matched load". The ledger verdict is T at `typical`;
  `heavy` is reported as robustness and cannot change it. Absolute times are
  expected to stretch under load; the ratio prediction is unchanged.
  Confidence = (pairs ≥ 4x + 1) / (pairs + 2) over cross-round ON/OFF pairs.

**Amendment A3: first-token signal, phase S only** (written 2026-10-17T19:55Z,
after run 2 was stopped in its second round and before any further round;
predictions unchanged). Run 2 (`typical` load, lock 19:39:12Z-19:45:11Z,
cluster and load removed) showed that the run-1 fix did not work: with tools in
the request no content, reasoning or tool_calls delta ever arrives, so TTFT
stayed null in all 13 scored requests of round 1. One extra request captured
the raw OVMS stream (OFF pod, max_tokens 8): a chunk with
`delta: {"role": "assistant", "content": null}` at 21.06 s, then only the
`finish_reason: length` chunk at 21.84 s and usage `completion_tokens: 8`. The
tool parser holds the 8-token reply; the role chunk is sent once the first
token is generated. Run 2 also showed the burst's own load in the next round's
windows: 1-min load 14.4 (ON, round 1) vs 24.0 (OFF, after the ON burst), with
busy, package W and swap close. Changes:
- TTFT = arrival of the first SSE chunk that carries a choice (the role
  chunk). The "empty output" guardrail reads `usage.completion_tokens` (0 =
  empty) because the parser holds the text.
- Phase B is not run, so M is not measured again: it keeps the run-1
  exploratory reading and stays INCONCLUSIVE. This keeps the burst out of the
  next round's load windows and fits 6 rounds in a level's 20 min.
- 30 s idle after the pod is Ready, then the two 10 s load windows.
- Everything else in A2 stands: levels, ABAB order, seeds, n, matching rule,
  and every T prediction and threshold.

### H25 run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T12:26:42Z (lock taken) .. 12:43:57Z (cluster deleted)
hypothesis:      H25 (pre-registration + amendment A1)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  3846649 (branch claude/inv-shared-prefix), k3d v5.9.0
node image:      cryptoandcoffee/k3d-gpu:0.1.28 (no Dockerfile change on the branch)
model:           OpenVINO/Qwen3-4B-int4-ov b467368d, openvino_model.bin sha256 074214fa…df2f9 (matches H6)
runtime:         openvino/model_server@sha256:a928903f…6fc58, agent CPU manifest, 16-CPU P-core balloon, hostPath models
energy source:   RAPL intel-rapl:0 energy_uj (in round-*.json)
runs per arm:    pre-registered 3; run ON 3, OFF 2 (round 6 OFF not run: 18:30 cap, pod image pulled in-lock)
```

Host confounds, every round: 1-min load 18.2-28.8 (other workflows' clusters
were running), host swap used ~124 GiB, 54-59 containers, package throttle
deltas 4972-46273. Pod `memory.swap.current` stayed 0 in every burst.
graph.pbtxt read back `enable_prefix_caching` true/false as intended in all
5 rounds. `usage.prompt_tokens` was 1634 in every one of the 65 scored
requests, exactly the A1 offline prediction, so the calibration fix held.

**Pre-registered verdict: T INCONCLUSIVE, M INCONCLUSIVE.** TTFT was null in
every request: the stream never carried a `content` or `reasoning_content`
delta (text_len 0, no errors, HTTP 200). The probe counted only those two
fields; with 12 tools in the request the 8-token reply most likely came as
tool-call deltas or was held by the tool parser. The cause was not captured.
Every round voids on "errors or null TTFT", as the scorer said. Fix after the
run: `request()` also counts `tool_calls` deltas (unit test with a local SSE
server); it has not run live.

Exploratory, NOT pre-registered (same requests, end-to-end latency with
max_tokens 8 in place of TTFT; it includes up to 8 decode steps, which are the
same in both arms):

| Metric | ON (shipping default) | OFF (`--enable_prefix_caching=false`) |
|--------|-----------------------|---------------------------------------|
| added agents a2..a6, median | 3.42 s (n = 15) | 16.99 s (n = 10) |
| per-round medians | 3.26, 3.42, 3.52 s (spread 0.26 s) | 17.73, 16.14 s (spread 1.59 s) |
| cold agent a1, median | 15.86 s | 17.18 s (ON/OFF 0.92, inside ±15%) |
| control c (one token changed at ~16), median | 15.90 s (0.94x OFF: cold, as predicted) | - |
| ΔRSS per agent, 6-agent burst | 64.7, 72.9, 119.9 MiB (median 72.9) | 112.4, 119.8 MiB (median 116.1; predicted 117) |

Ratio OFF/ON 4.96x, bootstrap 95% CI 4.68-5.33x; 6 of 6 cross-round pairs
≥ 4x, so the derived confidence that an added agent is ≥ 4x faster end to end
on this host is (6+1)/(6+2) = 0.88 (rule of succession over round pairs).
Memory ON/OFF 0.63x falls in the pre-registered 0.5-0.8 band, and one ON
round equals OFF: M INCONCLUSIVE. Absolute numbers are under heavy host
load and are slower than an idle host (H6 prefill 172 tok/s predicts ~9.6 s
OFF).

What this is: a measured cost of a shipped OVMS feature on one Raptor Lake
host. It is not an invention: cross-agent prefix reuse is prior art (vLLM APC,
SGLang RadixAttention on Xeon, OVMS prefix caching, KVFlow, KVComm)
[provenance:P250] [provenance:P252] [provenance:P253] [provenance:P259], and
k3d-gpu enables nothing (OVMS `--pull` already does). No README claim. The
detector stays report-only; no default changes. Next probe: re-run T with the
tool-call TTFT fix at low host load, then the arms the refuters asked for
(one shared engine vs N per-agent engines; static vs dynamic `cache_size`).

### H25 run 3: matched background load (i9-13900T, 2026-10-17)

```text
# Reproduce
date:            2026-10-17T19:48:16Z (lock taken) .. 20:21:36Z (cluster and load removed)
hypothesis:      H25 (pre-registration + amendments A1, A2, A3)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P 0-15, E 16-31)
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  4f7ca72 (branch claude/verdict-shared-prefix-ml), k3d v5.9.0
node image:      cryptoandcoffee/k3d-gpu:0.1.28 (as run 1)
model:           OpenVINO/Qwen3-4B-int4-ov b467368d, openvino_model.bin sha256 074214fa…df2f9 (matches H6)
runtime:         openvino/model_server@sha256:a928903f…6fc58, agent CPU manifest, 16-CPU P-core balloon, hostPath models
load:            bench/load/load.py, stress-ng 0.22.01 @sha256:2ea9a7c3…1cbe on E-cores 16-31
energy source:   RAPL intel-rapl:0 energy_uj (sudo -A), in round-*.json and load windows
command:         LEVELS="typical heavy" IMAGE=... MODELS=... OUT=... \
                 flock -w 14400 "$LOCK" bench/shared-prefix/run.sh
runs per arm:    typical ON 3, OFF 3 (order ON OFF ON OFF ON OFF, seeds 2511-2516);
                 heavy ON 3, OFF 2 (seeds 2521-2525; round 6 OFF not run: 18:30 cap)
```

Run 2 (19:39-19:45Z) is the aborted A2 attempt described in amendment A3; its
one finished round is not scored. In run 3 every one of the 77 scored requests
returned HTTP 200 with `prompt_tokens` 1634 and `completion_tokens` 8, the
TTFT signal was present in all of them, and graph.pbtxt read back as intended
in all 11 rounds. No round is void.

**Background load per arm** (means of the 2 x 10 s idle-agent windows per
round; tolerance from `load.py` TOLERANCE):

| Level | Arm | E-core busy % | 1-min load | package W | throttle/min | swap used MiB | swap growth MiB |
|-------|-----|---------------|------------|-----------|--------------|---------------|-----------------|
| typical | ON | 65.1 | 18.96 | 93.7 | 37.9 | 144936 | -10.1 |
| typical | OFF | 66.3 | 20.45 | 95.5 | 105.7 | 144820 | +0.6 |
| typical | spread / limit | 1.2 / 5.0 ok | 1.49 / 3.07 ok | 1.8 / 9.6 ok | **67.8 / 26.4 fail** | 116 / 256 ok | 10.6 / 64 ok |
| heavy | ON | 99.9 | 35.29 | 109.0 | 2421.9 | 143915 | +14.2 |
| heavy | OFF | 99.97 | 36.87 | 109.0 | 1014.2 | 143966 | +3.9 |
| heavy | spread / limit | 0.03 / 5.0 ok | 1.58 / 5.53 ok | 0.08 / 10.9 ok | **1407.7 / 605.5 fail** | 51 / 256 ok | 10.3 / 64 ok |

Load matched: **no**, at both levels, on one metric each: core throttle events
per minute. Every other metric matched, most of them well inside the limit.
The throttle count is bursty. In `typical`, one OFF round's two windows read
263 and 323 events/min and every other window read 0-120. In `heavy` it rose
through the run (12-156 in round 1, 4918-6228 in round 5), and the missing
6th OFF round left the last, hottest ON round unpaired.

**Pre-registered verdict: T INCONCLUSIVE at `typical` (the ledger level) and at
`heavy`. Cause: load unmatched on throttle_per_min.** At `typical` a second
gate also fails: the ON per-round medians spread 0.52 s, more than 30% of the
1.31 s median (round 3 ran at 1.82 s, and its windows held the highest E-core
busy and package W of the ON rounds). M was not measured (A3).

What the same requests measured (reported, not a verdict):

| | typical ON | typical OFF | heavy ON | heavy OFF |
|---|---|---|---|---|
| added agents a2..a6, median TTFT | 1.32 s (n = 15) | 12.44 s (n = 15) | 2.76 s (n = 15) | 25.56 s (n = 10) |
| per-round medians | 1.31, 1.82, 1.29 s | 12.44, 12.84, 11.84 s | 2.83, 2.71, 2.76 s | 25.61, 25.41 s |
| cold agent a1, median | 11.68 s | 12.87 s | 26.92 s | 25.89 s |
| control c, median | 12.08 s | 12.62 s | 26.24 s | 25.40 s |
| package energy per round | 5340, 6843, 5402 J | 12136, 12784, 11482 J | 11002, 10634, 10942 J | 24619, 23370 J |

- Ratio OFF/ON of the added-agent median TTFT: `typical` 9.44x, bootstrap 95%
  CI 6.99-9.90x; `heavy` 9.27x, CI 8.96-9.73x. The lower bounds clear the
  pre-registered 4x at both levels.
- Controls: c/OFF 0.97 (`typical`) and 1.03 (`heavy`), so one changed token
  near the start of the system text makes a cold request, as predicted; a1
  ON/OFF 0.91 and 1.04, inside ±15%.
- Cross-round pairs ≥ 4x: 9 of 9 (`typical`) and 6 of 6 (`heavy`). Derived
  confidence by the A2 rule: (9+1)/(9+2) = 0.91 and (6+1)/(6+2) = 0.88. That is
  confidence in the ≥ 4x effect on this host under this load, not a
  SUPPORTED verdict, which the load rule withholds.
- The throttle mismatch points in opposite directions at the two levels (fewer
  throttle events in ON windows at `typical`, more at `heavy`) and the ratio
  is 9.3-9.4x at both, so the throttle gap does not look like it is causing
  the effect. That reading is post hoc and does not change the verdict.
- Absolute TTFT doubles from `typical` to `heavy` in both arms, because the
  E-core load also slows the P-core prefill (shared package power and
  ring/DRAM). The ratio does not move. The earlier run-1 end-to-end figure
  (4.96x) included up to 8 decode steps; the first-chunk TTFT ratio is larger.
- Energy: a round of 7 requests used 0.48x (`typical`) and 0.45x (`heavy`)
  of the OFF package energy with caching ON (whole package, background
  included).
- Heavy round 3's raw RAPL delta wrapped once; it is corrected with
  `max_energy_range_uj` 262143328850.

No README claim (the verdict is INCONCLUSIVE, and prefix caching is shipped
OVMS behaviour, see run 1). Next probe: the same run with load matched on
throttle events too. Two candidate fixes, to be pre-registered before use: an
even round count per level so time trends cancel, and more, shorter load
windows per round so one bursty window cannot carry an arm.

### H26 pre-registration (i9-13900T, 2026-10-17)

Written 2026-10-17T12:18Z, before any timed block. No sysfs writes are planned. Reads only
(sudo -n): RAPL `energy_uj`, `thermal_throttle/package_throttle_count`, `perf stat`
on the uncore IMC free-running counters.

**Host facts (read 2026-10-17, before the run):**
- `/proc/cpuinfo` has none of `rdt_a cat_l3 cat_l2 cdp_l3 mba cqm cqm_mbm_total`; `/sys/fs/resctrl` does not exist
  (`CONFIG_X86_CPU_RESCTRL=y`). [provenance:P260]
- E-core L2 groups (`cpu*/cache/index2/shared_cpu_list`): 16-19, 20-23, 24-27, 28-31.
- `uncore_imc_free_running_0` and `_1` expose `data_read` and `data_write` (scale 6.103515625e-5 MiB).
- The RDT/MBA arm is N/A on this host: H26-X stays PENDING until a Xeon host runs it.

**Prior:** H3 arm PM (stream 15 on E, inference on P): decode +165.8%, prefill +99.1%. CPU balloons
alone do not isolate a bandwidth neighbour; H26 tests whether L2-cluster confinement does. Prior art
(nri-plugins RDT classes and balloons `loadClasses`, Koordinator resctrl, Heracles) is in
`docs/provenance.md` P260-P267; only the measured delta is claimable.

**Arms** (neighbour = `bench/noisy.sh --stressor stream`, stress-ng 0.22.01 by digest,
request = limit, `--stream-l3-size 128m`: stress-ng splits that size across workers, so every arm's
neighbour holds 3 x 128 MiB of buffers in total and only the worker count and placement differ):

| Arm | Label | CPU | Workers | Purpose |
|-----|-------|-----|---------|---------|
| Q | none | 0 | 0 | baseline |
| ME15 | efficient | 15 | 15 | today's behaviour (H3 PM replication); positive control |
| ME4 | efficient | 4 | 4 | core-count control: 4 hog cores, placement left to balloons |
| MC4 | ecluster | 4 | 4 | treatment: 4 hog cores inside one L2 cluster |

**Agent:** one OVMS pod for the whole run (`K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_CPUS=15 k3d-gpu agent up`),
`Cpus_allowed_list` logged. Per block: neighbour up (Running plus its `Cpus_allowed_list` line), 5 s
settle, 1 discarded request, 10 timed requests (`bench/rdt-isolate/client.py`, nonce per prompt),
neighbour deleted. n = 30 per arm; p95 is nearest-rank (29th of 30), so the decision threshold is a
2x effect, not a fine margin.

**Recorded per block:** neighbour CPUs, IMC GB/s over a 15 s window, package joules, throttle delta,
`pswpin` delta, CPU 0-15 busy %, 1-min load; agent pod restarts over the run.

**Analysis and outcome -> conclusion** (`bench/rdt-isolate/analyze.py`, unit-tested; priors in brackets):

| Observed | Verdict | Action |
|----------|---------|--------|
| I(ME15) < +20% | INCONCLUSIVE [0.05] | Probe blind; check IMC GB/s, rerun with a larger stream size |
| Q block-p95 spread > 25% of its median, or dropping swap-flagged blocks (pswpin > 2560) flips the verdict | INCONCLUSIVE [0.10] | Rerun at a quieter hour |
| MC4 neighbour IMC GB/s (above Q) < 15% of ME15's | INCONCLUSIVE [0.05] | Confinement starves the hog: a throughput trade, not isolation |
| R <= 0.5, bootstrap P(R <= 0.5) >= 0.8 (10 000 resamples stratified by block), 3/3 rounds R <= 0.5 | SUPPORTED [0.40] | doctor may recommend `ecluster` for bandwidth-heavy neighbours on hybrid clients |
| 0.5 < R < 0.8, or R <= 0.5 with lower confidence | REFUTED as stated [0.25] | Document I(MC4), I(ME15) with spread; no auto default |
| R >= 0.8 | REFUTED [0.15] | doctor keeps saying ecluster caps cores, not bandwidth, and points to Xeon RDT/MBA |
| H26b: I(ME4) - I(MC4) within the ME4/MC4 block spread | core count, not L2 topology | Capping the neighbour's CPU count is the simpler fallback |

A MC4 block whose neighbour CPUs are not inside one L2 group is VOID. Guardrails: every request
returns 32 tokens; the agent pod never restarts. The probe aborts at 18 min of its own wall time
and marks unrun blocks INCONCLUSIVE.

**Amendment A1 (2026-10-17, after two refuter reviews, before any timed block).** The primary
metric above compared 4 hog workers (MC4) with 15 (ME15), so it could pass on core count alone.
A1 replaces it; the table above is kept for the record but no longer decides.
- Primary: R = I(MC4) / I(ME4). ME4 is now `--stream 4 --cpu 15` on `efficient`, so the same 4
  workers float over all 15 E-cores; MC4 holds them in one L2 cluster. Only placement differs.
- Placement is checked per block: the stream workers' CPUs (host `ps -eLo psr`, sampled 8 s into
  the window) must touch >= 2 L2 groups for ME4, and the neighbour's `Cpus_allowed_list` must sit
  inside one group for MC4, else the block is VOID. L2 groups come from sysfs, not the 13900T map.
- The agent must sit off the E-cores (`/sys/devices/cpu_atom/cpus`) or the run aborts.
- INCONCLUSIVE also when I(ME4) < +10% (nothing to isolate) or any arm lacks an IMC reading.
  The Q-noise guard uses block medians, not block p95 (the max of 10).
- SUPPORTED additionally needs the MC4 hog's IMC GB/s (above Q) >= 85% of the ME4 hog's;
  below that any gain is the hog being throttled, recorded as REFUTED as stated.
- Prediction (A1): REFUTED (R ~1). P-cores have private L2; L2 placement of the hog
  partitions none of the shared LLC, ring or IMC. Priors: SUPPORTED 0.10, REFUTED as stated 0.20,
  REFUTED 0.45, INCONCLUSIVE 0.25.
- Run status (2026-10-17T12:58Z): the A1 probe queued for the live lock at 12:33Z and did not get it inside the 40 min work budget (another live experiment held it); no timed block ran, no cluster was made. H26 stays PENDING; the next run is `SUDO_ASKPASS=<askpass> flock -w 10800 $LOCK bench/rdt-isolate/run.sh` on b7d2f16 or later.
### H23 pre-registration (2026-10-17)

Written 2026-10-17T12:08Z, before any measurement. Ledger id H23; provenance ids
P230-P239 only.

**Hypothesis.** Admitting pods against a predicted watts-of-headroom budget
(arm T) cuts package thermal throttle time to <= 0.5x the uncapped baseline
(arm B) at no loss of makespan, and beats a power-blind pod-count cap at the
same budget (arm C).

**Null (H0, the claim is real).** All 3 T throttle deltas <= 0.5x median(B);
range(T makespan) <= range(B makespan); and on makespan or throttle time,
range(T) is better than range(C) and does not overlap it.

**Prediction.** REFUTED: T throttle <= 0.5x B, T makespan >= 1.10x B, and T
within C's spread. Reasons:
- PL1 = PL2 = 117 W never binds on this host (Research leads, T-series
  envelope), so the limit is TCC, not RAPL.
- TCC trims frequency, while admission idles whole cores. Power grows faster
  than linearly with frequency, so at equal power all cores at lower clocks do
  more work than fewer cores at full clocks.
- With identical pods a watts budget is a pod-count cap; only the hot/cool mix
  can separate T from C.

**Suspected flaws in the claim.**
- The "4% of uptime" figure is `throttle_pct()` in `scripts/k3d-gpu`: cpu0
  `package_throttle_total_time_ms` over uptime, since boot, whole host. The
  52 containers outside k8s are in it, and the scheduler cannot admit them.
- Accounting is not enforcement: a watts request does not limit real draw.
  The probe reports the calibrated per-type watts and the measured package
  watts per run, so the attribution error is visible.
- Extended resources are integers and static between patches; a lower
  capacity does not evict running pods.

**Arms (one cluster, same image, same 24 pods each run).**
- B: each pod requests `cpu: 1`. No admission cap.
- T: the node status gets `power.k3d-gpu.io/watts = floor(P_th - P_idle_now)`
  by a JSON PATCH of the node's `/status` subresource ([provenance:P232]).
  Each pod requests `cpu: 1` plus `power.k3d-gpu.io/watts: w_type` (hot or
  cool, from calibration).
- C: the node gets `k3d-gpu.io/slots = floor(budget / mean(w_hot, w_cool))`
  with the same budget as T; each pod requests 1 slot.
- If node allocatable CPU is under 24, every arm requests `cpu: 500m` instead
  and the probe writes a DEVIATION line.
- DRA consumable capacity ([provenance:P231]) is not in the arms: a claim needs
  a kubelet DRA plugin to prepare it, and admission goes through the same
  scheduler accounting. If T wins, a follow-up H checks DRA parity.

**Thermal model (fitted inside the lock, then frozen).**
- Calibration windows run as plain docker containers named `h23-cal-*` (outside
  k8s, same stress-ng image), so the fit does not depend on scheduling.
- Fit `T_pkg = T_idle + R_th * (P - P_idle)` by least squares on 1 s samples of
  coretemp package temp and RAPL package W from a 30 s idle window, a 20 s
  window with 8 hot containers and a 20 s window with 8 cool containers.
- TCC point: `T_tcc = TjMax (coretemp tempN_crit) - tcc_offset_degree_celsius`.
- Threshold power: `P_th = P_idle + (T_tcc - 10 - T_idle) / R_th`, capped by
  PL1 once the PL1 EWMA (time window Tau, [provenance:P235]) reaches PL1.
- Per-pod watts: `w_type = ceil((P_window - P_idle) / 8)`.
- If the budget admits all 24 pods, T is the same as B; a B run that throttles
  then refutes the model (O5).

**Workload.** `$IMAGE` = the stress-ng image pinned by digest in
`research/tseries-envelope/README.md`. The probe checks `--cpu-ops` and
`--cpu-load` in the image's `--help` before the first run. `--cpu-ops N_type`
is set from a 4 s solo calibration so a solo pod takes about 12 s.

**Confounds and handling.**
| Confound | Handling |
|---|---|
| Thermal carry-over between runs | Cooldown until package temp <= run-1 start temp + 3 C, 45 s cap; start temp recorded. Arms interleave B T C C T B B T C. |
| 52 other containers | Package W in the 5 s before each run recorded; run void if > idle median + 15 W. 1-min load recorded. |
| Swap | Swap used before and after; a delta > 512 MiB voids the run. |
| Throttle from outside the cluster | Background throttle rate from the 30 s idle window; deltas reported raw and minus background. |
| Scheduler requeue latency (T, C) | Per-pod Pending->Running gap recorded; verdict uses raw makespan. |
| Frequency and governor drift | No sysfs writes; governor and EPP logged read-only. |

**n and why.** 9 counted runs of <= 75 s plus <= 45 s cooldown, about 2 min
for up and delete, 70 s calibration: inside the 20 min lock. n=3 per arm; with
3 vs 3 non-overlapping ranges have one-sided permutation p = 1/20 = 0.05.
Confidence = 1 - p for a decided comparison. An arm whose spread is over 25%
of its median makes that comparison INCONCLUSIVE.

**Outcome -> conclusion (priors sum to 1.0).**
| # | Outcome | Conclusion | Prior | Action |
|---|---|---|---|---|
| O1 | B throttle delta (minus background) < 1 s in >= 2 of 3 B runs | INCONCLUSIVE: this load does not oversubscribe into throttling | 0.15 | DO-NOT-RE-ATTACK until a workload that keeps the package throttled is found |
| O2 | (a), (b) and (c) all hold | SUPPORTED | 0.05 | Build the DRA consumable-capacity driver behind auto-adapt; new H for DRA parity and a second SKU |
| O3 | (a) holds; T makespan > B (non-overlapping) | REFUTED: admission turns throttling into idle cores | 0.50 | DO-NOT-RE-ATTACK for throughput; doctor note only; a latency/SLO metric needs its own H |
| O4 | T within C's spread on makespan and throttle | REFUTED for "watts as a resource" | 0.15 | Drop the resource; a slot count is configuration |
| O5 | T throttle > 0.5x B, or the model admits all 24 and B still throttles | REFUTED: the headroom model is wrong | 0.10 | Log model residuals; no product change |
| O6 | Any arm spread > 25% of its median, or >= 2 void runs | INCONCLUSIVE | 0.05 | Re-run on a quiet host with n=5 |

**Guardrails.** 0 failed pods; package temp at TjMax for > 2 s aborts; makespan
cap 75 s (censored); stop at 18:30 into the lock, delete the cluster, release.
No sysfs writes (sudo only reads `energy_uj`); the only writes are node-status
patches inside the scratch cluster, which is deleted.

**Prior art.** Watts as an extended resource (GEOPM + Kueue, [provenance:P234]),
a node-level power filter (Intel TAS, [provenance:P233]), power-aware scheduler
plugins (Apple, [provenance:P237]) and predictive power/thermal slack placement
(TAPAS, [provenance:P236]) exist. H23 claims only its measured delta against
arms B and C. No-prior-art wording is limited to "per-pod watts as DRA
consumable capacity set from a turbo-budget model", and only after the search
in [provenance:P230] is re-run with a fresh budget.

**Amendment 1 (2026-10-17, after two refuter reviews, before any H23 run).**
Written and pushed before the lock is taken. Changes to the method:
- Budget gate (new outcome O0). After calibration, and again after `k3d-gpu up`,
  `headroom_model.py budget --npods 24` reports `degenerate` when the budget is
  under 2x the largest pod watts, admits fewer than 4 slots, or admits all 24.
  The probe then writes `INCONCLUSIVE degenerate budget: <reason>` and exits 4
  before any arm runs (before the cluster, for the first gate).
- The margin stays 10 C below T_tcc. It is not tuned to the host: a margin
  picked to make the budget non-zero would be fitted to the data.
- Fit windows: 8 containers for 52 s, fit samples from the last 30 s (was
  20 s / last 15 s), so the heatsink has 20 s to settle.
- Throttle is also reported per completed pod (`throttle_ms_per_pod`), and a
  censored run is void. Criterion (a) uses `throttle_ms_per_pod`.
- The probe fails (exit 2) unless node allocatable shows both
  `power.k3d-gpu.io/watts` and `k3d-gpu.io/slots` at the patched values.
- The watchdog's `sleep` is killed with the watchdog, so an early exit frees
  the shared lock at once (scratchpad repro: lock free 1.0 s after a 1 s probe,
  was 19 s). RAPL energy is read from the zone named `package*`, wrapped at its
  `max_energy_range_uj`. `host` reports `tcc_offset_source` (`none` = offset
  unknown, MSR 0x1A2 not read) and the number of package zones.

**Prediction for the gate.** Read-only, before the lock: package 86-92 C at
host load 20-26, T_tcc 95 C. T_tcc - margin = 85 C is at or below the idle
temperature, so P_th <= P_idle and the budget is 0 W. Predicted outcome: O0,
the probe stops after calibration (n = 1 calibration, 0 arm runs).

| # | Outcome | Conclusion | Prior | Action |
|---|---|---|---|---|
| O0 | Gate reports degenerate (first or second gate) | INCONCLUSIVE: the model has no headroom to sell on this host; arms B/T/C not run | 0.85 | DO-NOT-RE-ATTACK on this host while idle is within 10 C of T_tcc; re-run on a host that idles >= 20 C below T_tcc |
| O1-O6 | As above, if the gate passes | As above | 0.15 (scaled) | As above |

### H8 run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T16:18:44Z (first lock) .. 18:12:14Z (last cluster deleted)
hypothesis:      H8 (bench/apb/PROTOCOL.md section 7 predictions)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T
host kernel:     6.6.128-1-MANJARO
accelerators:    nvidia=2 (RTX 4000 SFF Ada, RTX 2000E Ada; driver 590.48.01), intel=8 (UHD 770, i915, shared), hybrid=1
k3d-gpu commit:  1a54ffb (main) + this change's bench/apb scripts and vllm-cuda.yaml fix; k3d v5.9.0
node image:      cryptoandcoffee/k3d-gpu:0.1.41, image ID sha256:4556ee04444b7e7111a603476d16d0fa25d7ba3a3fffb9b04be9978c456817ac
model:           A1/A2 OpenVINO/Qwen3-4B-int4-ov b467368d; A3 Qwen/Qwen3-4B-Instruct-2507 cdbee75f (bf16);
                 A4 Qwen/Qwen3-4B-AWQ 74d4bd2b. Local `hf download --local-dir` copies, staged into the node (hostPath)
runtime:         A1/A2 openvino/model_server@sha256:a928903f…6fc58 (`agent manifest`: A1 auto = CPU, P-cores 0-15;
                 A2 K3D_GPU_AGENT_DEVICE=GPU, gpu.intel.com/i915); A3 vllm/vllm-openai-cpu:v0.24.0@sha256:b0e6f860…981a1,
                 EAT 0d3438d chart render for cpu-qwen3-4b + integrations/eat/vllm-values.yaml (cpu 8 -> CPUs 0-7,
                 memory 16Gi, KV 6 GiB) + --max-model-len 32768; A4 vllm/vllm-openai@sha256:a4a4c043…c3a6b,
                 bench/apb/vllm-cuda.yaml on the RTX 4000 (runtimeClass nvidia)
energy source:   RAPL intel-rapl:0 package + psys (capacity's sudo -n reader) and nvidia-smi power.draw, both GPUs
runs per arm:    one `k3d-gpu capacity` run per arm (A1 twice); each arm in its own 15-min lock hold on a fresh cluster
```

Driver: `bench/apb/arm.sh ARM` (cluster `up`, model copied into the node, the arm's
manifest, `k3d-gpu capacity --budget B --step-cap B*60 --slo 10 --force --fresh`,
cluster deleted). B = 12 min, or less when bring-up left less of the 15-min hold.
The runs used a scratch copy of that script with the same steps (the committed one
differs in paths only); A1's first run predates `--step-cap` and kept capacity's
default 180 s. Every run had to use `--force`: the box was never quiet (package 80.6-84.6 W at
start against the 41 W bar, 53 other containers, 1-min load 3.3-9.3). capacity
lists that as a confound; it slows every arm.

| Arm | APB at p95 <= 10 s | Binding limit | What ran |
|-----|--------------------|---------------|----------|
| A1 OVMS int4, P-cores (auto) | 0 by the scoring rule; N=1 undecided (p95 9.65 and 9.86 s, CI straddles 10 s) | time: one N=1 rep takes ~5 min, so a run fits one scored rep and n stays 39 < 60 (`insufficient`) | 2 runs, 12-min budget each, 1 scored N=1 rep each |
| A2 OVMS int4, UHD 770 (forced) | >= 0, upper end open | `--budget`: a scored N=1 rep needs ~20.5 min | 12-min budget; warm-up only (7 turns, p95 26.1 s, discarded) |
| A3 EAT vLLM CPU bf16 (defaults) | not measured | server OOMKilled at its 16Gi limit, 2 of 2 runs | 6.7 and 6.4-min budgets (vLLM start took 6 min); warm-up only |
| A4 vLLM CUDA, RTX 4000 SFF Ada, AWQ | >= 32 (`--max`) | none hit: every step to N=32 passed; flags cpu_thermal_throttle, gpu0_power_cap | 9.7-min budget, 6 scored steps |

A4 steps (pooled p95 with bootstrap 95% CI, n window requests, net J per output token):

| N | p50 s | p95 s | p95 CI | n | output tok/s | TTFT p95 s | J/token net |
|---|-------|-------|--------|---|--------------|------------|-------------|
| 1 | 0.55 | 1.48 | 1.46-1.49 | 82 | 32.7 | 0.18 | 0.79 |
| 2 | 0.57 | 1.53 | 1.33-1.55 | 72 | 60.8 | 0.22 | 0.53 |
| 4 | 0.62 | 1.65 | 1.46-1.69 | 69 | 114.8 | 0.27 | 0.30 |
| 8 | 0.79 | 1.93 | 1.75-2.04 | 149 | 201.8 | 0.36 | 0.20 |
| 16 | 1.24 | 3.18 | 3.09-3.30 | 280 | 320.8 | 0.63 | 0.13 |
| 32 | 2.30 | 5.96 | 5.83-6.15 | 609 | 377.4 | 1.27 | 0.11 |

A4 per-rep p95 at N=1 and N=2: 1.48/1.49 s and 1.53/1.52 s (spread 0.01 s);
N >= 4 ran one rep each (the CI cleared the SLO). Prefix-cache hit 0.96 of
prompt tokens at every N. Tool-call validity 0.80-0.93 (protocol check 2 wants
>= 0.9 at N=1: 0.86, a near miss). No drift check: the budget ran out first.

A1: run 1 kept capacity's default `--step-cap 180`, which stopped N=1 after one
rep with 5.6 min of budget left; run 2 set `--step-cap` to the budget, and the
second rep still did not start because capacity estimated it at 380 s with
325 s left. Both runs scored one N=1 rep: p95 9.65 s (CI 9.41-10.34) and 9.86 s
(CI 9.56-10.56), n 39, p50 4.50 and 4.62 s, decode 17.2 and 16.6 tok/s median,
TTFT 2.5-2.6 s median on ~3.3k-token prompts. Pooling the two reps (two
clusters, same arm; exploratory, not a pre-registered step): n 78, p95 9.75 s,
CI 9.56-10.34, a pass on the point estimate. So one agent sits on the 10 s line
and N=2 was never reached. The N=1 p95 is 34-37% above PROTOCOL.md's predicted
7.2 s, past its 30% demand-model falsifier: completion lengths match the model
(median 33 tokens, 34 on A4), so the host load is the first suspect. Tool-call
validity 0.73 (check 2 wants >= 0.9).

capacity reported these runs as "search closed: APB = 0" and stored N=1 as the
upper end, so a rerun with a larger `--budget` returned the stored 0 without
measuring; `--fresh` was needed. That treated a step ended by the budget like a
measured failure; it was a reporting gap in capacity, not a property of the box.
Fixed since: a step the budget cuts short (run 2 here) is unmeasured, the
bracket stays open ("APB unknown (budget)", status `partial`), a rerun measures
that N, and stores holding such a step are rebuilt on load. Run 1 ended on
`--step-cap`, not the budget, so its `insufficient` N=1 still bounds the
bracket. The table's "0 by the scoring rule" is unchanged: both runs scored one
N=1 rep with the CI crossing 10 s.

A2: the warm-up's first turn took 119.6 s (kernel compile on a fresh cluster),
the next six 15.3-27.9 s with TTFT 5.7-14.5 s; every turn after the first is over
the 10 s SLO at N=1, so APB 0 is the expected answer, but no scored step ran and
the bracket stays open.

A3: run 2 recorded the vLLM container `OOMKilled` (exit 137, restartCount 1)
during the 2-session warm-up. The first turn (a ~3.1k-token prompt) hit the
120 s client timeout, the server died on a later turn and the rest were refused.
Run 1, not instrumented for it, shows the same sequence (timeouts, then
`RemoteDisconnected`, then connection refused). EAT's defaults (16Gi limit, 6 GiB KV, `--max-num-batched-tokens 2048`)
do not hold this trace's ~3.1-3.5k-token prompts on this host. That is an
outcome of the defaults, not an APB; PROTOCOL.md's "0 (0.9)" is untested.

A4 needed one fix first: run 1 crash-looped in `EngineCore failed to start` with
`ValueError: VLLM_PORT 'tcp://10.43.141.188:8000' appears to be a URI`. The
Service is named `vllm`, so Kubernetes service links set `VLLM_PORT`, which vLLM
reads as its own setting. `bench/apb/vllm-cuda.yaml` now sets
`enableServiceLinks: false`; run 3 came up in 196 s (image pull 2 min).

Predictions (PROTOCOL.md section 7) against these results: A4 > A1 held (A4 >= 32
against 0 by rule with N=1 on the SLO edge). A1 in [4, 12] is refuted by the scoring rule (APB 0 in both runs), though N=1 sits on the SLO edge. A2 = 0 and A3 = 0 are not tested (no
scored step). Verdict **INCONCLUSIVE**: two of four arms have no scored step.

Next probe, in order: A2 with a 45-min budget (two scored N=1 reps); A3 with
the memory limit raised until the warm-up survives (record it as a deviation
from EAT's defaults); A1 with a 30-min budget (two or more N=1 reps, then N=2); A4 with
`--max 64`.

Reproduce:

```bash
# Model copies (revisions as in the header), then one arm per run:
hf download OpenVINO/Qwen3-4B-int4-ov --revision b467368d16b75df14055562fe927ae8e1f15f7ef --local-dir "$M/Qwen3-4B-int4-ov"
hf download Qwen/Qwen3-4B-Instruct-2507 --revision cdbee75f17c01a7cc42f958dc650907174af0554 --local-dir "$M/Qwen3-4B-Instruct-2507"
hf download Qwen/Qwen3-4B-AWQ --revision 74d4bd2bd4bff9cafc9345221320bffb08b406a3 --local-dir "$M/Qwen3-4B-AWQ"
git clone --depth 1 --branch release-1.0.3-hotfix https://github.com/intel/enterprise-agent-toolkit eat   # 0d3438d
for arm in ovms-cpu ovms-igpu eat-vllm-cpu vllm-cuda; do
    APB_MODELS=$M APB_EAT_CHART=$PWD/eat/core/helm-charts/vllm bench/apb/arm.sh "$arm"
done
```

### H7 run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T17:26:54Z (lock taken) .. 17:46:28Z (cluster deleted)
hypothesis:      H7
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T
host kernel:     6.6.128-1-MANJARO
accelerators:    as the H8 run (not used: both arms on the CPU)
k3d-gpu commit:  1a54ffb (main) + this change's bench/apb/h7.sh and stage.py; k3d v5.9.0
node image:      cryptoandcoffee/k3d-gpu:0.1.41 (sha256:4556ee04…817ac)
model:           one checkpoint, Qwen/Qwen3-4B 1cfa9a72: A = OpenVINO/Qwen3-4B-fp16-ov 00298f1c (fp16 IR exported
                 from it; openvino_model.bin 8,044,936,594 B, sha256 8ada14248ee24b94…), B = Qwen/Qwen3-4B bf16
                 safetensors (8,044,982,000 B)
runtime:         A openvino/model_server@sha256:a928903f…6fc58, `agent manifest` CPU, 16 CPUs, P-core balloon;
                 B vllm/vllm-openai-cpu:v0.24.0@sha256:b0e6f860…981a1, EAT 0d3438d chart defaults (default
                 model config: bf16, --enforce-eager, chunked prefill 2048, prefix caching) + vllm-values.yaml
                 with cpu=16, KV 6 GiB, --max-model-len 32768. Both read Cpus_allowed_list 0-15 in every block
energy source:   none (not measured in this run)
runs per arm:    blocks A B B A A B, one server up at a time; block 6 (B) was cut by the 20-min cap: A 3 blocks, B 2
```

`bench/apb/h7.sh`. Per block, after a warm-up request: 2 decode requests (128
tokens, `ignore_eos`, unique prompt so no prefix-cache hit) and 1 TTFT request
(a unique ~417-token prompt, 4 output tokens), all through `bench/llm_load.py`.

| Block | Engine | decode tok/s | TTFT s (prompt tokens) |
|-------|--------|--------------|------------------------|
| 1 | A OVMS fp16 | 4.06, 4.15 | 5.22 (416) |
| 2 | B vLLM bf16 | 3.69, 3.71 | 22.45 (417) |
| 3 | B vLLM bf16 | 3.75, 3.82 | 22.11 (417) |
| 4 | A OVMS fp16 | 4.33, 4.38 | 5.15 (419) |
| 5 | A OVMS fp16 | 4.37, 4.29 | 4.96 (418) |

Decode median A 4.31 tok/s (n 6, range 4.06-4.38), B 3.73 tok/s (n 4, range
3.69-3.82). A/B = 1.156: OVMS decodes 15.6% faster, i.e. vLLM is 13.5% slower.
Block-median ratios run from 1.08 (block 1 vs block 3) to 1.18 (block 4 vs block
2); the two adjacent pairs give 1.11 (blocks 1-2) and 1.15 (blocks 3-4). Weights
are the same size, so effective weight bandwidth has the same ratio: 34.7 vs
30.0 GB/s. TTFT on the ~417-token prompt: A 5.15 s median (prefill ~81 tok/s), B
22.28 s (~19 tok/s), 4.3x.

Verdict **INCONCLUSIVE**. The pre-registered line is a 15% decode gap; the pooled
gap is 15.6% measured against vLLM and 13.5% against OVMS, and the block-level
ratios fall on both sides of it. The run is short of the pre-registration: 3 and
2 blocks instead of 5 ABBA rounds, no `--enforce-eager`-dropped arm, no energy,
and the checkpoint is Qwen3-4B, not Qwen3-4B-Instruct-2507, because OpenVINO
publishes a 16-bit IR of the former (`OpenVINO/Qwen3-4B-fp16-ov`) and exporting
one in the run would have cost the cap. What the run does show: H2's 3.4x
decode gap (OVMS int4 15.67 vs vLLM bf16 4.63 tok/s) shrinks to about 1.16x when
both engines read 16-bit weights, so most of that gap is weight precision; the
prefill gap (4.3x) is not explained by precision and is outside H7's prediction.
Host: 53 other containers running, as in H8.

Reproduce:

```bash
hf download OpenVINO/Qwen3-4B-fp16-ov --revision 00298f1c614ffdc000814d8cbe1b7613aa73cdca --local-dir "$M/Qwen3-4B-fp16-ov"
hf download Qwen/Qwen3-4B --revision 1cfa9a7208912126459214e8b04321603b3df60c --local-dir "$M/Qwen3-4B"
APB_MODELS=$M APB_EAT_CHART=$PWD/eat/core/helm-charts/vllm bench/apb/h7.sh
```

### H23 run (i9-13900T, 2026-10-17)

**Reproduce:** commit `0f346f3` (`main`; `bench/thermal-resource/` and
`research/thermal-resource/` unchanged since `cbc3a08`), i9-13900T, kernel
6.6.128-1-MANJARO, k3d v5.9.0, stress-ng image pinned in
`research/tseries-envelope/README.md`. Governor `powersave`, EPP `performance`
(read only). Lock taken 09:32:45 PDT; no other k3d cluster existed. Quiet gate
(1-min load <= 8 and package <= 80 C, polled every 30 s, up to 30 min) cleared on
the first poll. Probe 09:32:46-09:35:23 (157 s, exit 4), no cluster created, 0
`h23-*` containers left.

```bash
# quiet gate passed: load1 5.24, package 80.0 C
flock -w 14400 <scratchpad>/live.lock bash -c 'OUT=<out> BUDGET_S=1110 bench/thermal-resource/probe.sh'
# end: load1 7.19, package 77.0 C
```

Deviation: the probe reads RAPL with `sudo -n` from nested subshells, where a
`sudo -v` timestamp (kept per parent pid without a tty) does not reach. A `sudo`
shim on `PATH` turned `-n` into `-A` (askpass), adding about 22 ms per read, so
each 1 s `watts` sample reads up to ~2% high in every window alike. No sysfs file
was written.

Raw files: `bench/thermal-resource/results/2026-10-17-run1/` (probe log, host and
fit JSON, the 90 fit samples, the gate output).

**Verdict: INCONCLUSIVE (O0, degenerate budget)**, applied mechanically by the
Amendment 1 gate: `slots 2 < 4`. No arm ran, so criteria (a)-(c) have n = 0.

| Quantity | Value |
|---|---|
| Host | PL1 = PL2 = 117 W, tau 56 s, TjMax 100 C, TCC offset 5 C (int340x), T_tcc 95 C |
| Idle window (30 x 1 s) | median 81.3 W (range 72.3-98.9), package 73-95 C (median 81) |
| Background throttle | 1899 ms of the 30 s idle window (6.3%) |
| Hot window (8 x matrixprod, last 30 s) | mean 117.2 W, 81-95 C |
| Cool window (8 x 40% load, last 30 s) | mean 101.4 W, 81-96 C |
| Fit (n = 90) | R_th 0.297 C/W, T_idle 79.3 C at P_idle 72.3 W (lowest sample), max residual 9.7 C |
| Threshold | P_th = 72.3 + (95 - 10 - 79.3) / 0.297 = 91.6 W (binding: thermal) |
| Per-pod watts | w_hot 5, w_cool 3 (mean 4) |
| Budget | floor(91.6 - 81.3) = 10 W -> 2 slots; the gate needs >= 16 W (4 slots) |

Confidence: holding the fit fixed, a bootstrap of the idle-window median (20,000
resamples) puts it at 79.9-84.8 W (95%), a budget of 6-11 W, and 0 of 20,000
resamples reach the 16 W the gate needs. The fit itself was not resampled (max
residual 9.7 C), so this says the gate fires on this host at this background load;
it says nothing about the headroom model on a quiet host.

Against the prediction: Amendment 1 predicted O0 with a budget of 0 W because idle
was expected at 86-92 C. Idle read 79.3 C (fit), 15.7 C below T_tcc, so the
budget was 10 W, not 0. The gate fired because the idle window was not idle: the
shared host drew 72-99 W with nothing from H23 running, and P_th is anchored at the
lowest idle sample while the budget subtracts the median. The pre-registered O0
action (DO-NOT-RE-ATTACK while idle is within 10 C of T_tcc) does not apply.
Missing probe: the B/T/C series on a quiet window (idle spread < 5 W), or on a host
that idles >= 20 C below T_tcc.

## Scope corrections

Limits on what a SUPPORTED verdict may be quoted as saying.

- **H1** proves one non-Xeon host model, not every non-Xeon CPU. k3d nodes are
  containers on that one host, so it says nothing about multi-host EAT. The
  wave-1 run is reachability only (one completion, no speed or energy), with
  Qwen3-1.7B and EAT's CPU vLLM.
- **H7** at matched precision would say nothing about which engine is faster at
  each engine's own best precision; it only splits engine from precision.
- **H2** RAPL `package` energy covers the CPU cores and the integrated GPU, and
  leaves out DRAM, storage and the rest of the platform. J/token numbers are
  package J/token. The result holds for the measured model, quantization and
  output length only. The wave-1 REFUTED verdict holds for the UHD 770 (no XMX)
  only; a Core Ultra or other XMX iGPU is untested. The EAT vLLM arm in that run
  differs in precision, checkpoint and config, so no OVMS-over-vLLM engine ratio
  may be quoted from it (H7).
- **H3** pinning acts on cpusets inside the node's cgroup. It does not isolate
  memory bandwidth, last-level cache or thermal headroom, so a neighbour that
  stresses those is a separate hypothesis. It does not confine host processes
  outside the k3d node either: the wave-1 refutation was measured with those
  unconfined, and does not say pinning fails on a quiet host.
- **H4** holds for the devices present on the test host and the measured model.
  It does not show the choice is best on other hosts. The wave-1 REFUTED verdict
  is for the `auto` rule of `claude/agent-stack` 8385dc2 on a UHD 770 host. It is
  not a verdict on `main`'s XMX-gated rule, which on that host picks the 16-CPU
  P-core arm (C16, the fastest arm measured) but was written after seeing the data.
- **H6** holds for the UHD 770 (Raptor Lake, no XMX), this model and OVMS 2026.4.1.
  It says nothing about an iGPU or dGPU with XMX, which is why `auto` still picks
  those.
- **RAPL counter wrap**: `energy_uj` wraps at `max_energy_range_uj` in the same
  directory. A run whose end reading is below its start reading must add that
  range back or be discarded.
- **H8** APB is for one shared host (53 other containers, ~81 W package at idle,
  CPU thermal throttling in every arm), the synthetic `support` trace, 10 s SLO and
  `kubectl port-forward` access. An open bracket (`>= n`) is a lower bound only.
  A4 compares a dGPU with AWQ int4 against CPU and iGPU arms at OpenVINO int4:
  different hardware and quantization, not an engine comparison.
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
- **H16/H17** cover one UHD 770 (i915) and the Intel GPU DRA driver gpu-v0.12.1
  on k3s v1.37.1. H17's refutation is about this driver release, which publishes
  each GPU as one exclusive device; it says nothing about DRA itself, nor about
  discrete Intel GPUs, where the plugin default is also one pod per GPU.

### H21 run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T12:43Z..13:01Z (pre-registered 12:13Z e8110c2; amendment bdd1a02 before the run)
hypothesis:      H21 (a) placement headroom over the `efficient` label, (b) CPU-time attribution
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T, P 0-15, E 16-31; RAPL package-0, core, uncore, psys
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  bdd1a02 (claude/inv-joule-pack)
node image:      cryptoandcoffee/k3d-gpu:0.1.28 (launcher pin), k3d v5.9.0, kubectl v1.35.2
agent image:     busybox 1.37 by digest (images.lock); turn 29360128 bytes sha256 (calibrated), 1 s think
command:         OUT=... SUDO_ASKPASS=... flock -w 10800 <live.lock> bench/joule-pack/probe.sh
                 bench/joule-pack/joulepack.py verdict "$OUT"
host state:      53 containers running (other workflows), swap used ~122 GB, max zone temp 79-85 C
```

n = 3 paired rounds, orders U P E X M / M X E P U / E U M P X, 32 s measured per arm, 12 s idle
before each arm. Marginal = arm W minus mean of the bracketing idle windows.

| rnd | arm | pkg W | core W | uncore W | turns | p95 s | agents/W | J/turn |
|-----|-----|------:|-------:|---------:|------:|------:|---------:|-------:|
| 1 | U | 6.20 | 5.99 | -0.02 | 336 | 0.20 | 1.94 | 0.590 |
| 1 | P | 8.86 | 8.61 | 0.04 | 350 | 0.14 | 1.36 | 0.810 |
| 1 | E | 10.84 | 10.52 | 0.07 | 309 | 0.26 | 1.11 | 1.123 |
| 1 | X | 8.97 | 8.63 | 0.05 | 313 | 0.25 | 1.34 | 0.917 |
| 1 | M | 9.86 | 9.59 | -0.05 | 306 | 0.62 | 1.22 | 1.031 |
| 2 | M | 1.04 | 1.12 | 0.04 | 325 | 0.30 | 11.6 | 0.102 |
| 2 | X | -3.31 | -3.00 | 0.11 | 318 | 0.24 | - | - |
| 2 | E | 2.11 | 2.11 | 0.03 | 313 | 0.25 | 5.70 | 0.215 |
| 2 | P | 0.02 | 0.28 | 0.03 | 356 | 0.12 | 597 | 0.002 |
| 2 | U | 2.72 | 2.83 | 0.05 | 347 | 0.20 | 4.42 | 0.250 |
| 3 | E | 5.63 | 5.73 | -0.08 | 312 | 0.27 | 2.13 | 0.577 |
| 3 | U | 6.64 | 6.70 | 0.02 | 346 | 0.20 | 1.81 | 0.615 |
| 3 | M | 1.31 | 1.37 | 0.12 | 328 | 0.25 | 9.13 | 0.128 |
| 3 | P | 2.58 | 2.60 | 0.06 | 352 | 0.13 | 4.65 | 0.234 |
| 3 | X | 9.46 | 9.16 | 0.01 | 312 | 0.26 | 1.27 | 0.970 |

Idle package: median 90.72 W, spread 26.47 W, n = 16. Both pre-registered floor checks
fired ("P marginal < idle spread"; "idle spread > 50% of median E marginal"), so (a) and (b)
are INCONCLUSIVE by rule; confidence is not derived for an INCONCLUSIVE verdict. The
uncore negative control held (|dW| <= 0.12 W in every arm), the cpusets matched the pools
in every pod, every arm met the 1.0 s p95 SLO (max 0.62 s, M in round 1), and the root
sampler and cluster were gone after the run. What did hold: turns per window are P 350-356,
U 336-347, E/X/M 306-328, the work bias the J/turn amendment corrects for. The marginal watts
swing from -3.3 to +10.8 W for the same arm across rounds, so no per-round ratio here (e.g.
best/E 104.9x in round 2) means anything. The feature stays a bench prototype; nothing in
the product changes.
