# Experiments

The ledger for every performance claim this project makes. A claim in `README.md`,
`docs/*.md` or `docs/intel/*.md` (a tok/s, J/token, "Nx faster" or "%" number, or a "first",
"only" or "fastest" superlative) must link its primary source on the same line,
or cite a row here as `[ledger:Hn]` whose verdict is **SUPPORTED**, or a sourced row
of [`docs/provenance.md`](docs/provenance.md) as `[provenance:Pn]`.
`tests/claims_check.sh` enforces this in CI. `tests/ledger_drift_check.py` also
fails CI when one of those docs states a verdict for a row that differs from this table.

Method: state the hypothesis and its prediction before measuring, name the probe
that could refute it, then record the verdict. A hypothesis is only marked
SUPPORTED after the probe ran on real hardware with the Reproduce header filled in.

Verdicts: `PENDING` (not yet measured), `SUPPORTED` (probe ran, prediction held),
`REFUTED` (probe ran, prediction failed), `INCONCLUSIVE` (probe ran, noise or
setup too weak to decide). Only `SUPPORTED` backs a claim.

## Hypotheses

| # | Hypothesis | Prediction | Probe | Verdict |
|---|------------|------------|-------|---------|
| H1 | EAT (Intel Enterprise Agent Toolkit) runs on a single non-Xeon machine. | On one host whose CPU is not a Xeon (Core or Core Ultra), `k3d-gpu up` followed by the EAT deploy from `claude/eat-on-k3d` reaches all EAT pods `Ready` and one inference request returns a completion. | Fresh host, Reproduce header filled in. `k3d-gpu up`, deploy EAT, `kubectl wait --for=condition=Ready pod --all -A --timeout=30m`, then one completion request against the EAT endpoint. Refuted by any pod not Ready at the timeout, a Xeon-only instruction or node-label requirement, or no completion. Wave 1 (2026-10-16, i9-13900T): a completion returned, but the probe was not run as written (see [H1 run](#h1-run-i9-13900t-2026-10-16)). Amended 2026-10-17T20:27Z, before run 2: matched load instead of a fresh host, one fresh cluster, every step timed and logged, grading of Job pods fixed in advance. Run 2 (2026-10-17): every step held in one pass except the `--all -A` wait, which no k3s v1.37 cluster can pass (k3s's Gateway API CRD Job leaves a Succeeded pod); see [H1 run 2](#h1-run-2-i9-13900t-2026-10-17). | INCONCLUSIVE |
| H1b | EAT (Intel Enterprise Agent Toolkit) runs on a single non-Xeon machine (H1's hypothesis, re-run with a readiness probe that can pass on k3s v1.37). | On one host whose CPU is not a Xeon (Core or Core Ultra), `k3d-gpu up` followed by the EAT deploy reaches every pod that is not in phase Succeeded `Ready` within 30 min, and one inference request returns a completion. | Pre-registered 2026-10-17T21:13Z (093ba98), before any H1b step; H1 keeps its INCONCLUSIVE verdict. One fresh cluster `k3dgpu-h1b-eat`, scratch `KUBECONFIG`, empty `EAT_WORK`, under `bench/load/load.py` `typical` on the E-cores (16-31) with 60 s load samples; the documented user path run once, each step timed: `k3d-gpu up`, `eat-up.sh`, `eat-up.sh model cpu-qwen3-1-7b`, `kubectl wait --for=condition=Ready pod -A --field-selector=status.phase!=Succeeded --timeout=30m`, one completion through EAT's LiteLLM gateway, `eat-check.sh`, `k3d-gpu down`. Every Job's completion is recorded on its own line and not graded. Refuted by any step 1-3 failing, a wait that ends non-zero with a Deployment, StatefulSet, DaemonSet, ReplicaSet or Sandbox pod not Ready, a Xeon-only instruction or node-label requirement, or no completion. Grading, lock budget and outcome table in [H1b pre-registration](#h1b-pre-registration-i9-13900t-2026-10-17). Run (2026-10-17, i9-13900T, k3s v1.37.1): steps 1-3 rc 0, the wait exited 0 after 280.1 s with all 33 selected pods Ready, both Jobs `Complete`, completion HTTP 200; see [H1b run](#h1b-run-i9-13900t-2026-10-17). | SUPPORTED |
| H2 | On an Intel hybrid host whose only Intel GPU is an iGPU (wave 1: i9-13900T, UHD 770), the iGPU serves a fixed model faster and with less energy per token than the CPU running the same model with a tuned thread count. | iGPU decode tok/s is higher than the best CPU tok/s, and iGPU J/token is lower than the CPU J/token at that best thread count. | Row restated 2026-10-16 to the pre-registration that ran (written ~10:20 PDT, before the 10:29-11:32 PDT campaign); the row first committed at 04:48 PDT named a Core Ultra host, which was not tested. OVMS 2026.4.1 with `OpenVINO/Qwen3-4B-int4-ov`, same digest and graph except the device. CPU baseline swept over 4 thread and core configs first, best kept. Energy from RAPL `package-0`, with a 20 s model-loaded idle before each block. Decode 30 x 256, prefill ~1936 x 16, 4-in-flight 30 x 128; 5 rounds, ABBA order. Refuted if the iGPU loses on either tok/s or J/token. | REFUTED |
| H3 | P/E pinning isolates inference from noisy neighbours on a hybrid CPU. | With the inference pod labelled `k3d-gpu.io/cores: performance` and a CPU-saturating neighbour labelled `k3d-gpu.io/cores: efficient`, inference latency stays within 10% of the no-neighbour baseline on both decode and prefill. Without pinning, the same neighbour raises latency by more than 10% on at least one workload. | Pre-registered 2026-10-16 ~13:20 PDT, before any timed block (13:24-14:53 PDT). OVMS on CPU, Qwen3-4B int4, 15-CPU request; neighbour `bench/noisy.sh` (stress-ng `--cpu 15`, Guaranteed 15 CPU). Arms: Q pinned and quiet, P pinned with the neighbour on the E-cores, U both unlabelled, plus Qu, Qe, X (roles swapped) and PM (stream neighbour). `Cpus_allowed_list` read in every block. 5 rounds x 7 arms, 5 requests per workload per block. Refuted if P is more than 10% above Q on either workload. Run 2026-10-16 20:24Z..21:53Z, provenance header and Reproduce block in [H3 run](#h3-run-i9-13900t-2026-10-16). | REFUTED |
| H4 | The automatic device choice equals the measured best device. | For a fixed model on a host with more than one usable device (CPU, iGPU, NPU, dGPU), the device the agent stack selects automatically (`claude/agent-stack`) is the one with the highest measured tok/s in a sweep over every usable device. | Record the auto-selected device. Then force each usable device in turn and measure tok/s with the H2 method. Refuted if any forced device beats the auto choice by more than the run-to-run spread. Wave 1 pre-registered 2026-10-16 15:30 PDT (H4b) and 16:22 PDT (H4c), each before its runs, against the `auto` rule of `claude/agent-stack` 8385dc2. Run 2026-10-16T22:27Z..2026-10-17T01:12Z, arms G = `auto` (UHD 770), C16, C8 and Ce; provenance header and Reproduce block in [H4 run](#h4-run-i9-13900t-2026-10-16). | REFUTED |
| H6 | On a host whose Intel iGPU has no XMX matrix engines, OVMS on the P-cores serves a 4B int4 LLM faster than OVMS on the iGPU. | On an i9-13900T (UHD 770), OVMS 2026.4.1 with `OpenVINO/Qwen3-4B-int4-ov` on the 16-CPU P-core balloon decodes at least 20% faster than the same server on the iGPU, and the iGPU's time to first token on a ~1.9k-token prompt is at least 2x the CPU's. | Pre-registered 2026-10-16 ~10:20 PDT, before the main campaign (10:29-11:32 PDT). Same server digest, model and graph except the device: GPU arm = the agent GPU manifest (2-CPU P-core balloon), CPU arm = the agent CPU manifest (16-CPU P-core balloon). Greedy, streamed, `ignore_eos`. Decode: 30-token prompt x 256 out; prefill: unique ~1936-token prompt x 16 out; 5 requests per block, 5 rounds, arm order alternating. Refuted if either margin is missed. | SUPPORTED |
| H11 | Pinning the node image to the launcher's release costs `k3d-gpu up` no measurable time, and a cluster made from another image is reported. | (a) On a host where the image is cached, `k3d-gpu up` with the pinned default reaches "Accelerators schedulable" within the run-to-run spread (max - min) of the same image named through a floating `:latest` ref (`K3D_GPU_IMAGE`); (b) the checks `up` adds before cluster create (`node_image`, `ensure_image` on a cached image) take a median under 0.5 s; (c) on a cluster created by the previous launcher (default `:latest`), `doctor` warns that it runs an unversioned image and `up` stops before `k3d cluster create`; after recreate, the server node runs `<repo>:<VERSION>` with label `org.opencontainers.image.version=<VERSION>` and `doctor` says ok. | Pre-registered 2026-10-17 03:45Z, before the timed runs. Image built from the branch with `docker build --label org.opencontainers.image.version=<VERSION> -t <repo>:<VERSION>`, the same image ID also tagged `k3dgpu-r2-us:latest` for the floating arm, so only the launcher path differs. (a) n=3 per arm, arms alternating, wall time of `up` to its final line, cluster deleted between runs. (b) n=5 timings of the two docker inspects `up` adds. Outcome -> conclusion: (a) \|Δmedian\| <= max arm spread -> SUPPORTED for (a), else REFUTED; (b) median >= 0.5 s -> REFUTED; (c) any missing warn, a `k3d cluster create` on an existing cluster, or another node image -> REFUTED. | SUPPORTED |
| H12 | A Helm release named like Intel EAT's can take over the NRI balloons policy of a k3d-gpu cluster, and P/E placement stays correct. | On a hybrid host, with a node image built from this branch: (a) on a default cluster (`K3D_GPU_BALLOONS=k3s`), `helm upgrade --install nri-resource-policy-balloons` (chart v0.14.0, `-n kube-system`, `-f contrib/eat/hybrid-values.yaml`) exits 0 and adopts the baked objects: release revision 1 `deployed`, the BalloonsPolicy gains `vllm-balloon`, the DaemonSet is Ready, and `k3d-gpu test` passes; a pod with a container named `vllm` gets only P-cores and a pod in namespace `redis` only E-cores. After `.skip` and `docker restart` of the server node the policy still has `vllm-balloon`. (b) with `K3D_GPU_BALLOONS=external`, the server's manifests dir has no `nri-balloons.yaml`, neither CRD `balloonspolicies.config.nri` nor DaemonSet `nri-resource-policy-balloons` exists before Helm, and the same `helm` command then gives the same placement and a passing `k3d-gpu test`. (c) control: on a cluster from an image built from `origin/main` (objects without Helm ownership annotations), the same command fails with "cannot be imported". | Pre-registered 2026-10-17 before the live runs. Image `docker build -t k3d-gpu:r2-beat .` from this branch; control image from `origin/main` fcfdd38. Cluster `k3dgpu-r2-beat`, scratch `KUBECONFIG`. Arms (a) and (b) n=3 each, order a b b a a b, fresh cluster per run; (c) n=1 (it only shows the probe can fail). Placement read from each pod's `Cpus_allowed_list` against `/sys/devices/cpu_core/cpus` and `cpu_atom/cpus`. Outcome -> conclusion: every (a) and (b) run meets every listed check -> SUPPORTED; any helm failure, missing `vllm-balloon`, wrong cpuset, failing `k3d-gpu test`, baked object present under (b), or revert after the `.skip` restart -> REFUTED; (c) succeeding -> the probe cannot tell adoption apart, INCONCLUSIVE. Confounds recorded per run: package W (RAPL, 5 s before), 1-min load, swap used, package throttle count delta. | REFUTED |
| H13 | H12's adoption works when Helm applies client-side, as Helm 3 (EAT pins v3.14.0) always does; with Helm 4, `K3D_GPU_BALLOONS=external` installs with its default server-side apply, and adoption needs `--server-side=false`. | Same checks as H12 (a)/(b) (exit 0, revision 1 `deployed`, `vllm-balloon` present, DaemonSet Ready, `k3d-gpu test` passes, `vllm` container on P-cores, `redis` namespace on E-cores, `vllm-balloon` kept across a `.skip` restart for adoption arms). Arms: A3 = default cluster + Helm v3.14.0; A4 = default cluster + Helm v4.1.1 `--server-side=false`; B4 = external cluster + Helm v4.1.1 defaults; B3 = external cluster + Helm v3.14.0. | Pre-registered 2026-10-17 04:23Z, after H12 run a1 failed, before any H13 run. Same image, cluster name, values and probe script as H12; the Helm binary and the `--server-side` flag are the only changes. Helm v3.14.0 from get.helm.sh, sha256 f43e1c33…6651 checked against its .sha256sum. n=3 per arm, order A3 A4 B4 B3, B3 B4 A4 A3, A3 A4 B4 B3, fresh cluster per run. Outcome -> conclusion: every run of an arm meets every check -> that arm SUPPORTED; H13 SUPPORTED only if all four arms are; any failure in an arm -> REFUTED for that arm, and the README states only the arms that held. Confounds as H12. | REFUTED |
| H14 | Helm installs EAT's chart values over a k3d-gpu cluster in two ways: on a `K3D_GPU_BALLOONS=external` cluster with no extra flag (Helm 3 and 4), and on a default cluster with Helm 3 `--force` or Helm 4 `--force-conflicts`, which replace the adopted BalloonsPolicy spec. | Every run meets H12's checks: helm exit 0, revision 1 `deployed`, BalloonsPolicy has `vllm-balloon` and `agent-control-plane`, DaemonSet Ready, `k3d-gpu test` passes, a `vllm` container gets only P-cores and a pod in namespace `redis` only E-cores; on default clusters `vllm-balloon` survives a `docker restart` of the server after `.skip`. | Pre-registered 2026-10-17 before any H14 run, after H13 runs A3-1 and A4-2 and a 2-cluster spike (log below). Arms: A3f = default cluster, Helm v3.14.0 `--force`; A4f = default cluster, Helm v4.1.1 `--force-conflicts`; B4 = external cluster, Helm v4.1.1 defaults; B3 = external cluster, Helm v3.14.0 defaults. n=3 per arm, order A3f A4f B4 B3 B3 B4 A4f A3f A3f A4f B4 B3, fresh cluster per run, same image, values and probe script as H12. Outcome -> conclusion: all runs of an arm pass every check -> that arm SUPPORTED, and only SUPPORTED arms are documented as working; any failed check -> that arm REFUTED. Confounds as H12. | SUPPORTED |
| H15 | On an iGPU-only Intel host, the `K3D_GPU_INTEL_SHARED=auto` default (8 pods per iGPU) lets two `gpu.intel.com/i915` pods use the UHD 770 at once, at no loss of total throughput against running them one at a time. | (F) On a cluster from this branch's image with the default, node allocatable `gpu.intel.com/i915` = 8, the plugin DaemonSet args are `-shared-dev-num=8 -allocation-policy=balanced` and survive `docker restart` of the server node, and two pods requesting `gpu.intel.com/i915: 1` are both Running with overlapping run intervals and exit 0; a control cluster with `K3D_GPU_INTEL_SHARED=1` advertises 1 and keeps the second pod Pending while the first runs. (T) With a VA-API media workload (1080p H.264 decode, `scale_vaapi` to 720p, `h264_vaapi` encode, 6000 frames per pod, `linuxserver/ffmpeg:version-9.0.2-cli`), the aggregate fps of two concurrent pods is at least 0.9x the median solo fps, each concurrent pod gets at least 0.4x solo, no pod fails and dmesg logs no new i915 GPU hang. | Pre-registered 2026-10-16 PDT (2026-10-17T06:04Z, commit 1350533, pushed) before any H15 run. One shared cluster (default env) and one control cluster, both from `docker build -t $TAG .` on `claude/igpu-sharing-r2`. Timed arms on the shared cluster: S = one pod alone, C = two pods started together on a wall-clock barrier; n=3 per arm, order S C C S S C. Per run: package W (RAPL), load (1 min), swap used, package throttle count delta. Outcome -> conclusion: F holds and every C run meets T -> SUPPORTED, keep `auto` = 8 on iGPU-only hosts; F holds but aggregate < 0.9x solo in 2 or more C runs -> REFUTED, `auto` becomes 1 (sharing opt-in); any failed pod or GPU hang -> REFUTED, `auto` becomes 1; solo spread (max - min) above 20% of its median -> INCONCLUSIVE, rerun. | SUPPORTED |
| H16 | On k3s 1.37, `K3D_GPU_DRA=1` hands the UHD 770 to a pod through the Intel GPU DRA driver (gpu-v0.12.1) and no device plugin. | On a cluster from this branch's image with `K3D_GPU_DRA=1` on an i9-13900T: no `intel-gpu-plugin` DaemonSet, a ResourceSlice of driver `gpu.intel.com` lists the iGPU, node allocatable has no `gpu.intel.com/i915`, `up` reports `intel=1`, and every pod that names a ResourceClaimTemplate for DeviceClass `gpu.intel.com` lists `renderD128` under `/dev/dri` and ends `Succeeded`. | Pre-registered 2026-10-17 (this commit, pushed before the first run) together with H17, same runs: 3 DRA clusters (arm D). Refuted by a plugin DaemonSet, no slice, an i915 extended resource, or any claim pod without `renderD128` or not `Succeeded`. | SUPPORTED |
| H17 | DRA mode can become the default (auto) Intel GPU path when the driver is baked in, without losing what the device-plugin default gives on an iGPU host. | On the same host, all three: (a) two pods that each claim the iGPU through their own ResourceClaim from one template are both `Running` at once, as two `gpu.intel.com/i915: 1` pods are under the plugin's sharing default (H15); (b) median pod latency (create to `Succeeded`, busybox `ls /dev/dri`, image preloaded) in DRA mode is at most 1.2x the plugin's; (c) median `k3d-gpu up` wall time in DRA mode is at most 1.2x the plugin's. | Arms D (`K3D_GPU_DRA=1`) and P (default), one fresh cluster per run, n=3 runs per arm, order D P P D D P; per run 1 timed `up` and 3 timed pods (reported as the run's median), then the 2-pod check read 20 s after creation. Per run: package W over `up` (RAPL), load (1 min), swap used, package throttle count delta. Outcome -> conclusion: (a) fails in any D run -> REFUTED, DRA stays opt-in (`K3D_GPU_DRA=0` default), since auto would take away H15's sharing; (a) holds but (b) or (c) fails -> REFUTED, DRA stays opt-in; all hold -> SUPPORTED, DRA may become auto in a later change; F of H16 fails -> H17 not evaluated (DRA mode broken). A timing arm whose spread (max - min) exceeds 30% of its median -> that part INCONCLUSIVE. Prediction before measuring: REFUTED by (a), because gpu-v0.12.1 publishes no shared counters or multi-allocation (docs/experimental.md). | REFUTED |
| H18 | `k3d-gpu sandbox up` keeps agent sandboxes on the E-cores and inside a default-deny egress policy on k3d (kube-router), while public egress (PyPI) still works. | On an i9-13900T with tailscale (host `100.65.179.24`, sshd on `0.0.0.0:22`) and a cluster from this branch's image: (a) `sandbox up` exits 0 with the warm pool `readyReplicas` >= 1 and every sandbox pod's `Cpus_allowed_list` inside the E-cores (cpu_atom); (b) from a sandbox pod, TCP to `100.65.179.24:22` (CGNAT, tailscale) and to `192.168.1.108:22` (RFC1918, host LAN) both fail to connect within 3 s, and the policy's kube-router drop-log rule counts the packets, while from a control pod (same image, namespace `sbx-control`, no policy) both connect; (c) from a sandbox pod, DNS resolves `pypi.org`, TCP to `pypi.org:443` connects, and `pip install --user tomli_w==1.2.0` (not in the image) with the profile's `HOME=/tmp` exits 0 and imports from `/tmp/.local`. | Pre-registered 2026-10-17 before any H18 run (this commit, pushed). Image `docker build -t $TAG .` from `claude/agent-sandbox-ecores` rebased on origin/main 2b45f87. Cluster `k3dgpu-r2-sbx`, scratch `KUBECONFIG`, one fresh cluster per run, n=3 runs. Per run: timed `sandbox up` (wall, s), then the probes interleaved sandbox/control, each probe once per pod. Confounds per run: package W (RAPL, 5 s before), 1-min load, swap used, package throttle count delta. Outcome -> conclusion: every run meets (a), (b) and (c) -> SUPPORTED; a sandbox pod off the E-cores, a sandbox reaching either private address, or a control pod failing to connect (probe cannot see a block) -> REFUTED (control failure -> INCONCLUSIVE); pip failing while `pypi.org:443` connects -> REFUTED for (c), README must drop the pip claim. Prediction: SUPPORTED. Amended before the counted runs, after pilot run 0 (log below): kube-router REJECTs (`icmp-port-unreachable`, so `Connection refused` in ~0.1 s) rather than dropping, so (b) asks for no connect plus the drop-log counter instead of a timeout; `six` ships in the image, so (c) installs `tomli_w`. Runner: `bench/sandbox_round.sh`. | SUPPORTED |
| H21 | Joule-aware packing (joule-pack): per-pod energy from RAPL (package, core, uncore) split by cgroup CPU time drives P/E balloon placement that beats main's static `efficient` agent label on agents-per-watt under a p95 turn SLO. | (a) Among placements U (unlabelled), P, E, X (`ecluster`) for N=12 closed-loop synthetic agents (busybox 1.37 sha256 turn sized to 0.25 s on an E-core, 1 s think, 333m each), the best SLO-meeting placement (p95 ≤ 1.0 s) has agents-per-watt (N / marginal package W) ≥ 1.15x arm E in every paired round; (b) in arm M (6 P + 6 E pods) the P pods' cgroup CPU-time share is within ±0.10 of their core-domain energy share from arms P and E. Prediction before measuring: REFUTED on both: best/E 1.00-1.08 (best = E or X), best/U ≥ 1.3, CPU-share error ≤ -0.25 (E overcharged). Prior art (not claimed): CPU-time-proportional pod RAPL split is Kepler's model; joule-aware QoS placement on heterogeneous cores is Octopus-Man/Hipster ([provenance:P211] [provenance:P212] [provenance:P213]); search log [provenance:P210]. | Pre-registered 2026-10-17T12:13Z before any H21 run. One fresh cluster `k3dgpu-i-joule-pack`, scratch KUBECONFIG, inside the shared live lock, ≤ 19 min. Calibration pod (not counted) sizes the turn. 3 rounds, order U P E X M / M X E P U / E U M P X, 12 s idle window before every arm, 8 s warm-up + 32 s measured per arm. RAPL zones found by name (package-0, core, uncore, psys) sampled 1 s; uncore on client parts is PP1 (mostly iGPU), so it is a negative control and the non-core residual is package − core; marginal W = arm minus the mean of the bracketing idle windows; cpusets audited from `/proc/self/status` against `/sys/devices/cpu_core/cpus` and `cpu_atom/cpus`. Confounds every 5 s: package throttle count, max thermal-zone temp, load1, swap used, container count. Outcome -> conclusion: (a) ≥ 1.15x in 3/3 rounds -> SUPPORTED, build the controller and test it against a static `ecluster` rule; < 1.15x in at least 2 rounds -> REFUTED, no controller; best/U ≥ 1.3 in 3/3 -> new H for an `efficient` agent default (label, not meter). (b) \|err\| ≤ 0.10 in 3/3 with M core within 25% of (P+E)/2 -> SUPPORTED; err ≤ -0.15 in 3/3 -> REFUTED, attribution must weight by core type; additivity gap > 25% -> INCONCLUSIVE. Uncore marginal \|Δ\| > 0.5 W, P marginal < idle spread, or idle spread > 50% of median E marginal -> INCONCLUSIVE. n=3 paired rounds (sign test p = 0.125 at 3/3; directional confidence 1 − 0.5^k, ceiling 0.875). Runner: `bench/joule-pack/probe.sh`; analysis: `bench/joule-pack/joulepack.py verdict`. Amendment 2026-10-17, after review and before any H21 run: (a) is also scored on marginal package J per turn (arm W × window / turns), because P/U arms do more turns per window and agents/W alone penalizes them; (a) is SUPPORTED only if both metrics give ≥ 1.15x in 3/3, and REFUTED if E/best J per turn is < 1.15x in ≥ 2 rounds; the ratio uses E's measured value even when E misses the SLO (reported as a flag, never an infinite win); p95 is nearest-rank ceil(0.95 n). Limits stated up front: N is fixed at 12 so the SLO is not expected to bind and admission is not exercised; the workload is pure-ALU, not a real agent; (a) is an oracle headroom gate over static labels, not a test of the controller. Amendment 2026-10-18, before any matched-load H21 run: the retry runs under controlled, matched background load (`bench/load/load.py`), with the same predictions and the same (a)/(b) outcome rules; see [H21 amendment](#h21-amendment-matched-background-load-2026-10-18). | INCONCLUSIVE ([run](#h21-run-i9-13900t-2026-10-17)) |
| H20 | Phase-steer: on a hybrid Intel CPU, running each agent's prefill on the P-cores and its decode on the E-cores lowers energy per token at the same p95. | On an i9-13900T (P 0-15, E 16-31), with N=6 closed-loop agents, one llama-server per agent (`-np 1`), Qwen3-4B Q4_K_M GGUF, and a turn of 396 new prompt tokens on a cached 1024-token prefix plus 96 decode tokens: arm PS (`--cpu-mask` = E, `--cpu-mask-batch` = P, the `k3d-gpu phase-steer` output) reaches a package J/token (baseline-subtracted) of at most 0.85x arm SP (both masks = P, the product's P-core default), AND its p95 turn latency is at most SP p95 + SP spread (max - min across reps). | Pre-registered 2026-10-17T08:40Z, before any H20 run. This tests the oracle, not an NRI plugin. llama.cpp's in-process mask split [provenance:P201] is the zero-migration upper bound of any NRI phase-steer: an NRI cpuset applies to the whole container, so a plugin adds cgroup moves and phase-detection lag. Prior art covers the mechanism [provenance:P200] [provenance:P202] [provenance:P203] [provenance:P204], so there is no "first" claim. Arms, all at `-t 4 -tb 4` with only the masks differing: SP (baseline); PS (treatment); ALL (both masks 0-31, kernel ITMT placement, the competent baseline); NEG (decode P, prefill E, n=1; must show the worst median TTFT, or the probe cannot tell placement apart). n=3 reps per primary arm in Latin-square order (SP PS ALL / PS ALL SP / ALL SP PS), then NEG. Fresh servers per cell; 15 s warm, 60 s measured, then drain to idle. Probe `bench/phase-steer/probe.py`, which caps itself at 20 min wall. Outcome table and confounds in [H20 pre-registration](#h20-pre-registration-phase-steer-oracle-2026-10-17). Prior P(SUPPORTED) = 0.15. Amendment A2 (2026-10-18T10:07Z, before any A2 measurement): every arm under the same `bench/load/load.py` background load on the E-cores (`typical` headline, `heavy` robustness), a 15 s load window per cell, INCONCLUSIVE only if load is unmatched across SP/PS/ALL or a gate fails; whole-turn rounds with the prefix restored from a saved slot; predictions unchanged ([H20 Amendment A2](#h20-amendment-a2-matched-background-load-2026-10-18)). Amended 2026-10-18 (A3) before any A3 cell: re-run under amendment 27 (`matched_a27`: settle gap and pre-window before each cell, covariates gate, package W, throttle/min and 1-min load during the round reported only; missing load voids), `typical` first; predictions, thresholds, gates and priors unchanged; see [H20 amendment A3](#h20-amendment-a3-re-run-under-amendment-27-2026-10-18). | INCONCLUSIVE: run 1 ([run](#h20-run-i9-13900t-2026-10-17)) measured no turn; run 2 under A2 ([run 2](#h20-run-2-amendment-a2-matched-background-load-i9-13900t-2026-10-18)) has load unmatched on throttle events/min at `typical` (spread 992 > 642) and `heavy` (952 > 263), every other load metric matched. Gated out but REFUTED in direction: at `typical` PS/SP net J/token 0.868 (bar 0.85), PS TPOT 1.88x SP, tok/s 0.64x SP. Run 3 under A3 (amendment 27, `typical`; [run 3](#h20-run-3-amendment-a3-amendment-27-load-rule-i9-13900t-2026-10-18)): load unmatched on pre-window swap used only (870.9 > 256 MiB, one SP window before a 2.6 GiB host swap-in); pre throttle/min now matched (88 < 212), no void, every settle gap settled; the SP J/token spread (32%) and SP placement (2/3 cells) gates fail too, so the verdict if matched is also INCONCLUSIVE. Direction REFUTED again: PS/SP net J/token 1.03 (CI 0.52-1.20, n = 3 cells per arm), raw 1.51x (CI 1.46-1.54), TPOT 1.86x (CI 1.83-1.89), tok/s 0.64x (CI 0.62-0.65) |
| H9 | `k3d-gpu quickstart` takes a clean host to a working chat (Open WebUI answering through the agent) within the retail budget. | Time to first agent (quickstart start to its Ready line, with a chat answered through the UI after) is under 5 min when the node, OVMS and Open WebUI images and the model are already cached (warm arm), and under 15 min from nothing cached (cold arm). | Prediction stated in the task brief before the run (2026-10-17); the run itself was started before this row was committed. One fresh cluster `k3dgpu-g-retail`, scratch `KUBECONFIG`, no node image, no in-cluster images and no model PVC (cold arm), hard budget `timeout 900`. End state checked by signing in to Open WebUI with the generated admin and getting a chat reply through `/api/chat/completions`. Outcome -> conclusion: cold arm over 15 min or no reply -> REFUTED; both arms under target with n>=3 each -> SUPPORTED; anything less -> INCONCLUSIVE. Wave 1 ran the cold arm n=1 ([H9 run](#h9-run-i9-13900t-2026-10-17)). | INCONCLUSIVE |
| H23 | Thermal/power headroom published as a per-pod consumable resource (watts) stops a k3d-gpu node from oversubscribing into thermal throttling without losing throughput, and beats a power-blind pod-count cap. | On an i9-13900T (k3s v1.37, PL1 = PL2 = 117 W, thermally bound), 24 fixed-work pods (12 hot: stress-ng `--cpu-method matrixprod`; 12 cool: same plus `--cpu-load 40`): arm T (node advertises `power.k3d-gpu.io/watts` = fitted thermal headroom, pods request their calibrated watts) has (a) package throttle time <= 0.5x arm B (no admission cap), (b) makespan <= B, and (c) makespan or throttle time better than arm C (`k3d-gpu.io/slots` cap = same budget / mean pod watts) by more than the arm spread. Prediction before measuring: REFUTED, T throttle <= 0.5x B but makespan >= 1.10x B, T within C's spread. | Pre-registered 2026-10-17T12:08Z before any H23 run (this commit, pushed before the first run). One cluster `k3dgpu-i-thermal-resource`, scratch KUBECONFIG, in `flock` with a 20 min wall budget. n=3 per arm, order B T C C T B B T C. Fixed work per pod (`--cpu-ops` from in-lock calibration). Per run: makespan, cpu0 `package_throttle_total_time_ms` delta, RAPL package J and W, max package temp, start temp, 1-min load, swap used, per-pod Pending->Running gap. Decision: arm ranges non-overlapping (3v3 permutation p = 0.05) or \|Δmedian\| > max arm spread. Outcome table in [H23 pre-registration](#h23-pre-registration-2026-10-17). Runner: `bench/thermal-resource/probe.sh`, model `research/thermal-resource/headroom_model.py`. Result ([H23 run](#h23-run-i9-13900t-2026-10-17)): the Amendment 1 budget gate fired after calibration (O0), before the cluster: fitted budget 10 W, 2 slots (< 4) at w_hot 5 / w_cool 3 W. Arms B/T/C were not run (0 of 9). The "idle" window drew 72-99 W (median 81.3 W) on the shared host, so background load used the headroom. AMENDED A2 2026-10-18T13:09Z before any A2 run (Amendment A2 in [H23 pre-registration](#h23-pre-registration-2026-10-17)): every arm under the same `bench/load/load.py` background load on the E-cores (typical headline, heavy robustness), held from 30 s before calibration through the gate and all runs, a 10 s load window per arm before each run, INCONCLUSIVE for load only when unmatched under load.py TOLERANCE; arms, model, 10 C margin, gate, n, outcome table and predictions unchanged. Run 2 (2026-10-18, matched-load design, [H23 run 2](#h23-run-2-matched-background-load-i9-13900t-2026-10-18)): INCONCLUSIVE (O0) at both levels, as A2 predicted. The budget gate fired after calibration, before any arm or cluster: typical loaded idle median 96.8 W, fit T_idle 85.0 C at 94.0 W, P_th 94.0 W, budget 0 W / 0 slots; heavy loaded idle 101.2 W, T_idle 87.0 C, P_th 90.5 W, budget 0 W. Arms B/T/C 0 of 9 per level, so no per-arm load windows exist to match (load matching not reached; the cause is the gate). Bootstrap (idle median and refit) budget 95% interval -7.1 to -0.1 W (typical), -14.6 to -7.9 W (heavy); 0 resamples reach the 16 W the gate needs: confidence > 0.999 in O0, none either way on the claim. Loaded idle is now within 10 C of T_tcc (95 C), so the pre-registered O0 action applies: DO-NOT-RE-ATTACK on this host. Missing probe: the B/T/C series on a host whose loaded idle (load.py typical held) sits >= 20 C below T_tcc. | INCONCLUSIVE (O0 under matched load; DO-NOT-RE-ATTACK on this host) ([H23 run 2](#h23-run-2-matched-background-load-i9-13900t-2026-10-18)) |
| H7 | OVMS's decode lead over EAT's vLLM CPU engine in the H2 run comes from weight precision, not from the engine. | On the i9-13900T 16-CPU P-core balloon, one checkpoint served by OVMS 2026.4.1 and by vLLM CPU v0.24.0 at matched weight precision decodes within 15% between the two engines, and the two land within 15% of each other in effective weight bandwidth (weight bytes x decode tok/s). | Pre-registered 2026-10-16, before any run. One checkpoint and revision (`Qwen/Qwen3-4B-Instruct-2507`, revision recorded) for both engines: OVMS loads an OpenVINO IR exported from it with 16-bit weights, vLLM loads it in bf16. A second arm at int4 in both engines runs only if vLLM v0.24.0 CPU is first shown to load an int4 build of the same checkpoint. vLLM runs with EAT's chart defaults and once with `--enforce-eager` dropped, to split engine from config. H2 workloads and energy method, arms interleaved ABBA, 5 rounds. Refuted if the decode gap at matched precision is 15% or more. Run 2026-10-17 at 16 CPUs, 3 OVMS and 2 vLLM blocks, decode gap 15.6% of vLLM's rate (13.5% of OVMS's), on the line ([H7 run](#h7-run-i9-13900t-2026-10-17)). | INCONCLUSIVE |
| H8 | Agents per box (APB: the largest number of closed-loop tool-using agents whose pooled p95 LLM turn latency stays <= 10 s on the `support` agent trace) on the i9-13900T depends on the serving arm as pre-registered in `bench/apb/PROTOCOL.md` section 7. | A1 OVMS int4 on the P-cores: APB in [4, 12] (confidence 0.6). A2 OVMS int4 on the UHD 770: 0 (0.8). A3 EAT's default vLLM CPU bf16: 0 (0.9). A4 vLLM CUDA AWQ int4 on the RTX 4000 SFF Ada: above A1 (0.75). Written 2026-10-16, before any run. | `k3d-gpu capacity --slo 10 --budget 12` per arm on a fresh cluster, one arm per 15-min hold (`bench/apb/arm.sh`). An arm's prediction is refuted by a scored APB outside it; a bracket that still contains the prediction decides nothing. Wave 1 (2026-10-17): A4 >= 32, A1 0 by rule with N=1 on the SLO edge, A2 and A3 unscored ([H8 run](#h8-run-i9-13900t-2026-10-17)). | INCONCLUSIVE |
| H19 | `k3d-gpu tune run --objective throughput` on the CPU agent stack (OVMS 2026.4.1, `OpenVINO/Qwen3-4B-int4-ov`, i9-13900T) does not crown a config whose gain over the default is run-to-run noise. | (a) The tune completes and writes a profile whose decision is either `default`, or `tuned` with a winner that an independent replication confirms. (b) The search's own null control, the explicit restatement of the default (`CPUS=16 CORES=performance MAX_NUM_SEQS=256 CACHE_SIZE=0 PREFIX_CACHING=true`, which the pilot showed writes a `graph.pbtxt` byte-identical to the default's), is never accepted. (c) Replication: the tuned winner, or with decision `default` the finalist with the best confirmation median, is measured again against the default in fresh trials outside the tune. Prediction: decision `default` (single-stream decode is memory-bound and the default already takes all 16 P-core CPUs), and the replication shows no gain beyond the noise gates. | Pre-registered 2026-10-17T09:15Z, before the counted tune, after two pilot trials (default and the explicit default, budget 4 x 64 tokens: 15.3/49.3/48.7 vs 15.3/49.8/46.8 aggregate tok/s at concurrency 1/4/8, identical graphs). Cluster `k3dgpu-r2-tune` from `docker build -t k3d-gpu:r2-tune .`, scratch `KUBECONFIG` and profile dir. Command: `k3d-gpu tune run --objective throughput --devices CPU --precisions int4` with the default criteria (n0=27, eta=3, n=5 confirmation runs per side in rotating order, margin 5%, k=1, alpha 0.05 Bonferroni, TTFT guardrail 25%, quality probe, `--unique-prompts` load). One tune (its confirmation is the n=5 interleaved step). Replication: 5 fresh trials per side, order A B B A A B B A A B (A = default), the tune's final budget (16 requests x 128 tokens at concurrency 1,4,8), same acceptance test (`tune.py` gates). Confounds sampled every 5 s for the whole run: package W (RAPL), 1-min load, swap used, sum of `package_throttle_count`. Outcome -> conclusion: (b) the explicit default accepted -> REFUTED (the gates pass noise); decision `tuned` and the replication fails the gates -> REFUTED (a noise winner was crowned); decision `tuned` and the replication passes -> SUPPORTED (a real winner); decision `default` and the replicated finalist also fails the gates -> SUPPORTED; decision `default` but the replication passes every gate -> INCONCLUSIVE (the tune missed a real gain, a power question, not a noise crown); the tune fails to finish or the default trial fails -> INCONCLUSIVE. Amended 2026-10-18T07:18Z (A1), before any trial: the shipping incremental `tune run --budget 4` under matched `typical` load, replication n = 3 per side; see [H19 amendment A1](#h19-amendment-a1-matched-background-load-2026-10-18). Run 2026-10-18 under A1 (`typical`, load matched): the tune kept the default (1/10 neighbours screened in the 4-min budget), and the replicated candidate (`CORES=efficient`) failed the gates: 31.4 vs 23.7 aggregate tok/s, ratio 1.32 (CI 0.73-1.87), p = 0.35, n = 3 per side, derived confidence 0.25; see [H19 run](#h19-run-matched-background-load-i9-13900t-2026-10-18). | SUPPORTED (confidence 0.25) |
| H30 | The k3d-gpu auto-config (`k3d-gpu up` + `k3d-gpu agent up`) decodes faster than Ollama 0.40.5 on the CPU (`qwen3:4b-q4_K_M`, manifest sha256 2bfd38a7) with vendor defaults, at the same model family and 4-bit tier. | k3d-gpu median decode tok/s is at least 1.05x ollama's in every one of 3 interleaved rounds; package J/token is reported alongside. | Pre-registered 2026-10-17, before any run. `k3d-gpu bench --vs ollama --id H30 --budget 900`: one client, the same 4 prompts, max_tokens 128, temperature 0, `ignore_eos` sent to both arms, 3 warmup requests per arm (inside the budget), rounds A,B / B,A / A,B, one request in flight, RAPL package counter per arm segment. Weights pinned: `bench` hashes the served file inside the competitor container and refuses to measure unless it matches `bench/competitors.lock`. Outcome -> conclusion: k3d-gpu/ollama median decode tok/s at least 1.05 in every round -> SUPPORTED; ollama/k3d-gpu at least 1.05 in every round -> REFUTED; mixed rounds, failed requests, an arm whose median output stops short of 128 tokens, arms counting tokens differently (usage vs stream chunks), or a spent 15-minute budget -> INCONCLUSIVE. Confound recorded per run: the k3d-gpu arm is reached through `kubectl port-forward`, the competitor through docker-proxy, so tunnel latency lands on the k3d-gpu arm only (a bias toward REFUTED). Ollama's OpenAI layer has no `ignore_eos` and streams Qwen3 thinking as `delta.reasoning`, which the client counts as output (openai/openai.go at v0.40.5). Prediction: SUPPORTED where `agent up` picks an Intel GPU, since Ollama 0.40.5 runs this model on the CPU here; REFUTED or INCONCLUSIVE on a CPU-only host. Amended 2026-10-18 before any run: matched background load, see [H30-H32 amendment A1](#h30-h32-amendment-a1-matched-background-load-2026-10-18). Run 2026-10-18 (A1, `typical`, load matched): k3d-gpu 10.96 tok/s (CI 10.86-11.18) vs Ollama 7.05 (6.95-7.14), ratio 1.55 (CI 1.53-1.59), 3/3 rounds, n = 12 per arm, J/token 8.52 vs 14.35, derived confidence 0.875. `agent up` picked the CPU (16 P-cores), so the CPU-branch prediction (REFUTED or INCONCLUSIVE) was wrong. See [H30-H32 run](#h30-h32-run-matched-background-load-i9-13900t-2026-10-18). | SUPPORTED |
| H31 | The k3d-gpu auto-config (`k3d-gpu up` + `k3d-gpu agent up`) decodes faster than llama.cpp server b11706 on the CPU (`Qwen/Qwen3-4B-GGUF` Q4_K_M at revision bc640142) with vendor defaults, at the same model family and 4-bit tier. | k3d-gpu median decode tok/s is at least 1.05x llamacpp's in every one of 3 interleaved rounds; package J/token is reported alongside. | Pre-registered 2026-10-17, before any run. `k3d-gpu bench --vs llamacpp --id H31 --budget 900`: one client, the same 4 prompts, max_tokens 128, temperature 0, `ignore_eos` sent to both arms, 3 warmup requests per arm (inside the budget), rounds A,B / B,A / A,B, one request in flight, RAPL package counter per arm segment. Weights pinned: `bench` hashes the served file inside the competitor container and refuses to measure unless it matches `bench/competitors.lock`. Outcome -> conclusion: k3d-gpu/llamacpp median decode tok/s at least 1.05 in every round -> SUPPORTED; llamacpp/k3d-gpu at least 1.05 in every round -> REFUTED; mixed rounds, failed requests, an arm whose median output stops short of 128 tokens, arms counting tokens differently (usage vs stream chunks), or a spent 15-minute budget -> INCONCLUSIVE. Confound recorded per run: the k3d-gpu arm is reached through `kubectl port-forward`, the competitor through docker-proxy, so tunnel latency lands on the k3d-gpu arm only (a bias toward REFUTED). Prediction: SUPPORTED where `agent up` picks an Intel GPU; REFUTED or INCONCLUSIVE on a CPU-only host. Amended 2026-10-18 before any run: matched background load, see [H30-H32 amendment A1](#h30-h32-amendment-a1-matched-background-load-2026-10-18); re-run amended again before any A2 request, see [H31-H32 amendment A2](#h31-h32-amendment-a2-idle-gap-before-each-load-window-2026-10-18). Run 2026-10-18 (A1, `typical`): load unmatched on throttle events/min (spread 184 > 142). Measured: k3d-gpu 11.20 tok/s (CI 11.07-11.33) vs llama.cpp 8.21 (7.99-8.29), ratio 1.36 (CI 1.34-1.41), 3/3 rounds, n = 12 per arm, derived confidence 0.875, verdict if matched SUPPORTED. See [H30-H32 run](#h30-h32-run-matched-background-load-i9-13900t-2026-10-18). Re-run 2026-10-18 under A2 (`typical`, 45 s settle before each window): load matched. k3d-gpu 15.60 tok/s (CI 14.95-18.78) vs llama.cpp 10.31 (10.08-10.38), ratio 1.51 (CI 1.45-1.84), 3/3 rounds (1.52, 1.54, 1.50), n = 12 per arm, J/token 6.06 vs 9.28, derived confidence 0.875. `agent up` picked the CPU (16 P-cores) with CPU Fast Draft, so the CPU-branch prediction (REFUTED or INCONCLUSIVE) was wrong. Supersedes the A1 run. See [H31-H32 A2 run](#h31-h32-run-a2-idle-gap-before-each-load-window-i9-13900t-2026-10-18). | SUPPORTED |
| H32 | The k3d-gpu auto-config (`k3d-gpu up` + `k3d-gpu agent up`) decodes faster than plain OVMS 2026.4.1 with its own device choice (`OpenVINO/Qwen3-4B-int4-ov` at revision b467368) with vendor defaults, at the same model family and 4-bit tier. | k3d-gpu median decode tok/s is at least 1.05x ovms's in every one of 3 interleaved rounds; package J/token is reported alongside. | Pre-registered 2026-10-17, before any run. `k3d-gpu bench --vs ovms --id H32 --budget 900`: one client, the same 4 prompts, max_tokens 128, temperature 0, `ignore_eos` sent to both arms, 3 warmup requests per arm (inside the budget), rounds A,B / B,A / A,B, one request in flight, RAPL package counter per arm segment. Weights pinned: `bench` hashes the served file inside the competitor container and refuses to measure unless it matches `bench/competitors.lock`. Outcome -> conclusion: k3d-gpu/ovms median decode tok/s at least 1.05 in every round -> SUPPORTED; ovms/k3d-gpu at least 1.05 in every round -> REFUTED; mixed rounds, failed requests, an arm whose median output stops short of 128 tokens, arms counting tokens differently (usage vs stream chunks), or a spent 15-minute budget -> INCONCLUSIVE. Confound recorded per run: the k3d-gpu arm is reached through `kubectl port-forward`, the competitor through docker-proxy, so tunnel latency lands on the k3d-gpu arm only (a bias toward REFUTED). The OVMS container gets `/dev/dri` (or `/dev/dxg` plus `/usr/lib/wsl` on WSL2) and the devices passed are recorded; a run where the competitor had no GPU node while the k3d-gpu arm ran on one is reported as such in the run section. Prediction: INCONCLUSIVE: same engine, same weights and the same device on a GPU host, so the gap should sit inside the margin. Amended 2026-10-18 before any run: matched background load, see [H30-H32 amendment A1](#h30-h32-amendment-a1-matched-background-load-2026-10-18); re-run amended again before any A2 request, see [H31-H32 amendment A2](#h31-h32-amendment-a2-idle-gap-before-each-load-window-2026-10-18). Run 2026-10-18 (A1, `typical`): load unmatched on 1-min load (2.75 > 2.48) and throttle events/min (224 > 85). Measured: k3d-gpu 11.11 tok/s (CI 10.43-11.44, CPU) vs plain OVMS 6.50 (6.38-6.72; its device was not logged), ratio 1.71 (CI 1.60-1.77), 3/3 rounds, n = 12 per arm, derived confidence 0.875, verdict if matched SUPPORTED. See [H30-H32 run](#h30-h32-run-matched-background-load-i9-13900t-2026-10-18). Re-run 2026-10-18 under A2 (`typical`, 45 s settle before each window): load unmatched again, on throttle events/min only (spread 147.9 > 147.7). Measured: k3d-gpu 16.23 tok/s (CI 15.54-19.42, CPU with Fast Draft) vs plain OVMS 10.24 (10.21-10.25, OVMS picked the iGPU: "No device specified, using recommended device: GPU"), ratio 1.58 (CI 1.52-1.90), 3/3 rounds (1.61, 1.58, 1.59), n = 12 per arm, J/token 5.92 vs 9.70, derived confidence 0.875, verdict if matched SUPPORTED. See [H31-H32 A2 run](#h31-h32-run-a2-idle-gap-before-each-load-window-i9-13900t-2026-10-18). Amended 2026-10-18 (A3) before any A3 request: re-run under amendment 27 (`matched_a27`: settle gap and pre-window, covariates gate, package W, throttle/min and 1-min load during the segment reported only; missing load voids), predictions and thresholds unchanged; see [H32 amendment A3](#h32-amendment-a3-re-run-under-amendment-27-2026-10-18). Re-run 2026-10-18 under A3 (`typical`, amendment 27): load matched on every covariate, 3 of 3 load containers in every window, every settle gap settled, no void. k3d-gpu 16.15 tok/s (CI 15.45-19.09, CPU with Fast Draft) vs plain OVMS 10.14 (10.11-10.16, iGPU), ratio 1.59 (CI 1.52-1.88), 3/3 rounds (1.60, 1.59, 1.61), n = 12 per arm, J/token 6.08 vs 9.92, derived confidence 0.875. The registered prediction (INCONCLUSIVE) was wrong: its same-device premise failed (k3d-gpu picked the CPU, plain OVMS the iGPU). The A1 and A2 runs stay INCONCLUSIVE under the rule that scored them. See [H32 run A3](#h32-run-a3-amendment-27-load-rule-i9-13900t-2026-10-18). | SUPPORTED |
| H24 | A spec-only roofline (DRAM channels x MT/s x 8 B; cores x int8 MAC/clk x GHz), calibrated on one 8-P-core point of this host, predicts decode, prefill and 4-in-flight throughput of core configurations it was not fitted on, within 25% MAPE. This is the transfer law `k3d-gpu plan --predict <sku>` relies on. | Held-out arms B (4 P-cores), C (8 E-cores), D (16 E-cores) on the i9-13900T, Qwen3-4B int4, OVMS 2026.4.1 (H6 digest). M0 (`console/sku_model.py arms`) predicts decode 15.7 / 15.7 / 15.7, prefill 86 / 32.3 / 64.5, c4 62.6 / 32.3 / 62.6 tok/s for B / C / D (C compute-bound at c4). Competing M1 (min(spec BW, measured per-cpuset triad BW)) predicts decode = 15.7 x triad(arm) / triad(A). Author prior: M0 decode REFUTED (B ~12.5, C ~8, D ~12), M0 prefill holds, M1 decode holds within 20%. | Pre-registered 2026-10-17T12:15Z, before any H24 run; `tests/console/test_sku_model.py` checks the model's numbers equal these within 1%. Probe `bench/sku-model/probe.py` (self-limits to 19 min). 4 OVMS containers `sci-h24-{A,B,C,D}`, same image digest and weights sha256 as H6, all loaded at once, one served at a time; no k3d cluster. stream_probe triad per arm x2. Visit order A B C D D C B A; per visit: 5 s idle sample, 3 decode (30-token prompt with nonce, 128 out, `ignore_eos`), 2 prefill (unique ~512-token prompt, 16 out), 1 c=4 (4 x 128 out). n per arm: decode 6, prefill 4, c4 2. Calibration = this run's arm A (H6's 15.66 is a sanity check: A within 15%). Outcome -> conclusion: see [H24 pre-registration](#h24-pre-registration). Review fixes in ed20aa4. Result ([H24 run](#h24-run-i9-13900t-2026-10-17)), run 1 at `a19f52c`, 10:47-11:04 PDT: the scorer returned "INCONCLUSIVE (missing cells: partial run)". 6 of 8 visits ran (A once, so no drift sentinel); every TTFT was null because OVMS streams Qwen3 thinking as `reasoning_content`, so decode c=1 and prefill have n = 0; every visit had package throttling (coretemp 95-99 C). Scored c4 cells (M0 calibrated on A, 45.9 tok/s, n=1): B 34.6 (-25%, n=1), C 22.2 (-6%, n=2), D 30.4 (-34%, n=2). Unscored end-to-end proxy (128 / e2e) for decode, relative to A (13.0 tok/s): B 0.78, C 0.57, D 0.71 (M0 predicts 1.0; M1 triad ratio 0.99 / 0.69 / 0.80). Missing probe: re-run at `cce5fd8` (reasoning_content counted, ~512-token prefill). AMENDED A1 2026-10-18 before any A1 run ([H24 Amendment A1](#h24-amendment-a1-matched-background-load-2026-10-18)): every arm under the same `bench/load/load.py` load on the E-cores (`typical` headline, `heavy` robustness), one 10 s load window per visit tagged with the arm, INCONCLUSIVE for load only when unmatched under load.py TOLERANCE; busy/throttle flags recorded, not gates; model, predictions, outcome table, n and order unchanged. Run 2 under A1 (2026-10-18, [H24 run 2](#h24-run-2-amendment-a1-matched-background-load-i9-13900t-2026-10-18)): load unmatched at `typical` (load1 spread 8.19 > 2.82, throttle/min 665 > 418) and `heavy` (5.55 > 4.88, 1701 > 440; heavy also partial, A once); E-core busy, package W and swap matched at both. Gated out, typical direction is row 3: decode vs M0 B -15.6%, C -47.0%, D -55.8% (n 6, 95% CI half-width <= 2 pp), M1 decode C -21.9%, D -40.3%; heavy larger (C -64.8%, D -73.3%). | INCONCLUSIVE (load unmatched under A1; REFUTED in direction, confidence 0.9) ([run 2](#h24-run-2-amendment-a1-matched-background-load-i9-13900t-2026-10-18)) |
| H25 | Agents that send a byte-identical system prompt and tool schemas to one OVMS CPU server reuse one prefix KV, so an added agent's first request skips the shared prefill. Whether the shared blocks also cut resident memory per live agent is measured too, not assumed. Pre-registered 2026-10-17T05:08-07:00, before any H25 run. | On an i9-13900T, OVMS 2026.4.1 (digest a928903f…), `OpenVINO/Qwen3-4B-int4-ov` b467368d on the 16-CPU P-core balloon, shared prefix Ps ≈ 1536 tokens, private Pp = 96, max_tokens 8, thinking off. (T) The median TTFT of an added agent (agents 2..6, sequential) is ≥ 4x lower with the shipping default (prefix caching on, cache_size 0) than with `--enable_prefix_caching=false`; the lower bound of the 95% bootstrap CI of the ratio is ≥ 4x. Point prediction: OFF 9.6 s ± 15% (Ps+Pp at the 172 tok/s of H6), ON 0.9 s (0.6-1.4). (C) An ON agent with one token changed at system position 16 measures ≥ 0.8x the OFF median. (M) Memory per added live agent in a 6-agent burst: OFF ≈ 117 MiB (52 blocks x 32 tok x 72 KiB u8). Expectation (prior 0.5): ON ΔRSS per agent is ≥ 0.8x OFF, because dynamic-pool growth counts full prompts; physical sharing alone would give ≈ 9-12 MiB. Null H0: added-agent TTFT ON/OFF < 2x with c equal to the ON agents (no cross-agent reuse); for M, ON ΔRSS per agent ≥ 0.8x OFF. | `bench/shared-prefix/run.sh` under the live lock, 20 min cap. Fresh OVMS pod every round, arm order ON OFF OFF ON ON OFF (n = 3 rounds per arm, 15 added-agent samples per arm), new prefix seed every round. Per round: warm-up, sequential agents a1..a6 plus the perturbed control c, then a 6-agent barrier burst with 1 Hz sampling of pod `memory.current`. Confounds per round: RAPL package energy, 1-min load, swap used, pod `memory.swap.current`, package throttle-count delta, running container count. Outcome table and guardrails in [H25 pre-registration](#h25-pre-registration). The deploy-time detector (`k3d-gpu agent prefix`) is scored by unit tests, not by this probe. Prior art exists (vLLM APC, SGLang RadixAttention on Xeon, OVMS prefix caching, KVFlow, KVComm): a claim states only the measured delta [provenance:P250] [provenance:P252] [provenance:P253] [provenance:P259]. Run 1 (2026-10-17): TTFT null in every request (probe read only content deltas), both parts void; exploratory end-to-end 4.96x (CI 4.68-5.33), see [H25 run](#h25-run-i9-13900t-2026-10-17). Run 3 (A2 matched background load on the E-cores, A3 first-chunk TTFT, phase S only): T INCONCLUSIVE at `typical` and `heavy` because load was unmatched on core throttle events/min (typical 37.9 vs 105.7, heavy 2422 vs 1014; busy, 1-min load, package W and swap matched), and at `typical` the ON spread gate failed too. Measured: added agent 1.32 s ON vs 12.44 s OFF at `typical` (9.44x, CI 6.99-9.90, 9/9 pairs ≥ 4x, derived confidence 0.91), 2.76 vs 25.56 s at `heavy` (9.27x, CI 8.96-9.73); M not re-measured. See [H25 run 3](#h25-run-3-matched-background-load-i9-13900t-2026-10-17). Amendment 2026-10-18 (A4), before any A4 round: T is re-run under amendment 27 (`matched_a27`: settle gap and pre-window, covariates gate, package W, throttle/min and 1-min load during the round reported only; missing load voids), new seeds, predictions and thresholds unchanged; see [H25 pre-registration](#h25-pre-registration). Run 4 (2026-10-18, A4, `typical` 3+3 rounds, `heavy` not run): load matched under amendment 27 (every covariate in tolerance, 3 of 3 load containers in every window, every settle gap settled), no round void. T SUPPORTED: added agent 1.02 s ON vs 10.87 s OFF (10.61x, CI 10.36-10.74, n = 15 per arm), c/OFF 1.02, a1 ON/OFF 0.99, 9/9 pairs ≥ 4x, derived confidence 0.91. M not measured (A3), stays INCONCLUSIVE. See [H25 run 4](#h25-run-4-amendment-27-load-rule-i9-13900t-2026-10-18). | SUPPORTED |
| H26 | On a hybrid Intel client with no RDT (i9-13900T: no `rdt_a`/`cat_l3`/`mba`/`cqm` flags, no `/sys/fs/resctrl`), confining a memory-bandwidth neighbour to one E-core L2 cluster (`k3d-gpu.io/cores: ecluster`, 4 E-cores) cuts the agent's p95 latency inflation by at least half against the same neighbour in the `efficient` balloon. On hosts with `rdt_a`+`mba`, nri-plugins RDT classes are the predicted remedy instead (not testable on this host; H26-X). | R = I(MC4)/I(ME15) <= 0.5, where I(arm) = p95(arm)/p95(Q) - 1 on agent request latency. Point prediction: p95(Q) ~2.3 s, I(ME15) >= +100% (replicates H3 PM), I(MC4) +20..+50%, R ~0.3; I(ME4) within spread of I(MC4) (core count, not L2 topology, does the work). Competing prior (prior-art note): L2 confinement helps little against a DRAM hog (LLC and memory controller stay shared, no MBA), improvement < 10%. | Pre-registered 2026-10-17T12:18Z before any H26 run; see [H26 pre-registration](#h26-pre-registration-i9-13900t-2026-10-17). Probe `bench/rdt-isolate/run.sh` (analysis `bench/rdt-isolate/analyze.py`). Agent = OVMS 2026.4.1 `OpenVINO/Qwen3-4B-int4-ov`, `k3d-gpu agent up` on CPU with 15 CPU; unique ~30-token prompt x 32 out, greedy, `ignore_eos`, streamed. Arms: Q none; ME15 `stress-ng --stream 15` on `efficient`; ME4 `--stream 4` on `efficient`; MC4 `--stream 4` on `ecluster`; every neighbour `--stream-l3-size 128m` (same total DRAM footprint). 3 blocks x 10 requests per arm (n=30), order Q ME15 ME4 MC4 / MC4 ME4 ME15 Q / Q ME15 ME4 MC4, one cluster `k3dgpu-i-rdt-isolate`. Outcome -> conclusion in the section. Run 2026-10-17 (A2, matched-load design, 12 blocks per level, n = 30 per arm): INCONCLUSIVE at `typical` and `heavy` because background load was unmatched across arms (`typical`: E-core busy 7.3 > 5.0 pp, 1-min load 10.6 > 5.1, throttle/min 411 > 156, from hog carry-over into the windows; `heavy`: the load containers were removed from outside at 21:33:30Z). Measured at `typical`: p95 Q 3.70 s, ME15 10.65 s (+188%), ME4 5.31 s (+43%), MC4 5.04 s (+36%); R = 0.83 (CI 0.61-0.87), P(R >= 0.8) 0.72, verdict if matched REFUTED. See [H26 run](#h26-run-matched-background-load-i9-13900t-2026-10-17). Amended 2026-10-18 (A3) before any A3 block: re-run under amendment 27 (`matched_a27`: settle gap and pre-window before each block, covariates gate, package W, throttle/min and 1-min load during the block reported only; missing load voids), cluster `k3dgpu-r-rdt-isolate`, `typical` first; predictions, thresholds and priors unchanged; see [H26 amendment A3](#h26-amendment-a3-re-run-under-amendment-27-2026-10-18). Re-run 2026-10-18 under A3 (`typical`, amendment 27; `heavy` not run): load unmatched on during busy_pct only (36.96 > 5.00: the background packs onto the E-cores the hog leaves free, the risk A3 named), every pre-window covariate matched, 3 of 3 load containers in every window, every settle gap settled, no void. Measured: p95 Q 2.31 s, ME15 6.69 s (+190%), ME4 3.31 s (+44%), MC4 3.23 s (+40%); R = 0.92 (CI 0.14-2.09), per round 0.33, 1.27, 0.98, n = 30 per arm, P(R >= 0.8) 0.54, verdict if matched REFUTED. The A2 run stays INCONCLUSIVE under the rule that scored it. See [H26 run A3](#h26-run-a3-amendment-27-load-rule-i9-13900t-2026-10-18). | INCONCLUSIVE |
| H28 | On k3s v1.37.1+k3s1 (containerd v2.3.4-k3s1), N agent pods that mount one model through an image volume and mmap it read-only share one page-cache copy, so the per-agent memory cost is near zero, and they become ready faster than pods that copy the model into their own emptyDir. | On an i9-13900T, 1 GiB unique blob, N=8: arm T (image volume + mmap) median PSS per pod <= 160 MiB, Shared_Clean >= 0.9x size, host ΔCached per round <= 1.25x size; arm C (init `cp` to emptyDir, then mmap) PSS >= 0.9x size, ΔCached >= 0.8xNxsize; T round-median time-to-ready (cold source, create to READY after faulting in the whole mapping) <= 0.5x C, and max(T) < min(C). Null: T PSS >= 0.5x size, or ΔCached >= 0.5xNxsize, or T time >= 0.8x C. | Pre-registered 2026-10-17T12:13:48Z before any H28 run. `bench/zero-copy-models/probe.sh` (prep outside the lock, run under live.lock, cluster `k3dgpu-i-zero-copy-models`, scratch KUBECONFIG, locked part killed at 20 min). n=3 rounds per arm, 8 pods per round, order C T T C C T, unique image per round, source file evicted per round (fsync + posix_fadvise DONTNEED in a helper container; mincore must read 0), no global drop_caches. Confounds per round: load1, swap used, swap-out, package throttle count, RAPL W. Outcome -> conclusion: see the [H28 pre-registration](#h28-pre-registration-i9-13900t-2026-10-17). Prior art: KEP-4639, KServe `oci+native://`, Tetris (ATC'22), overlayfs page-cache sharing [provenance:P280]; only the measured delta may be claimed. Prediction: SUPPORTED. Amended 2026-10-18 (A2) before any H28 round: run under amendment 27 (`bench/load/load.py` load on the E-cores, node pinned to the other CPUs, settle gap and pre-window before each round, during windows; covariates gate, package W, throttle/min and 1-min load during the round reported only; missing load voids), cluster `k3dgpu-r-zero-copy-models`, `typical` first; predictions, thresholds and priors unchanged; see [H28 amendment A2](#h28-amendment-a2-run-under-amendment-27-2026-10-18). Run 2026-10-18 under A2 (`typical`, the ledger level; amendment 27): load matched on every covariate, 3 of 3 load containers in every window, no round void, cold pages 0 in every round. T PSS 132.5 MiB per pod vs C 1028.5 MiB; model page cache 1.00x size (T) vs 9.00x (C, source + 8 copies); time to ready 2980 ms (T) vs 6333 ms (C), round-median ratio 0.47 (bootstrap CI 0.45-0.55, min-max of round medians), complete separation (p = 0.05), n = 3 rounds x 8 pods per arm; derived confidence 0.79. `heavy` (not scored, T n = 2): ratio 0.32, memory as at `typical`. See [H28 run A2](#h28-run-a2-amendment-27-load-rule-i9-13900t-2026-10-18). | SUPPORTED |
| H27 | A <=60 s on-box probe (`bench/amx-autoquant/probe.sh`) picks the weight precision for agents better than the static int4 default. (a) Fidelity: on an i9-13900T, the probe's quality-gated pick among Qwen3-4B {int4, int8, fp16} OpenVINO weights equals the pick of a 5-round reference benchmark in 3 of 3 probe repeats. (b) Value: that pick's agent-turn latency (TTFT on a 396-token prompt + 96 decoded tokens) beats the static default (int4) by more than the reference run-to-run spread. (c) Cost: the probe takes <=60 s wall time on a warm server, model download not counted. | Pre-registered 2026-10-17T05:08-07:00, before any H27 run. Predicted: (a) holds; (b) is REFUTED on this host because the pick = int4 = the default, delta = 0. Numbers: decode int4 14-17, int8 8-10.5, fp16 4-6 tok/s; TTFT@396 int4 1.9-2.8 s, int8 1.8-3.6 s, fp16 4-8 s; dNLL vs fp16 on held-out text: int8 <=0.01 nats/token, int4 0.02-0.08. Gate: dNLL <=0.05 nats/token (about +5% ppl). Null H0: the probe adds nothing over a static/cpuid rule (pick = int4, or the probe disagrees with the reference). Outcome priors: O1 pick=int4, (b) REFUTED 0.55; O2 int4 fails the gate, pick int8 0.25; O3 probe flips vs reference 0.08; O4 int8 beats int4 on turn latency 0.04; O5 INCONCLUSIVE (int4'/int4 outside [0.9,1.1] in >=2 rounds, swap, all dNLL=0, probe >60 s) 0.08. Confidence: high = pick dominates in 5/5 rounds (sign test p=0.031) and P1-P3 agree 3/3; medium = 4/5 (p=0.19) or 2/3. | OVMS `openvino/model_server:2026.4.1-gpu@sha256:a928903f...` (pin from `share/npu-embeddings.yml`), one container per precision (`k3dgpu-i-amx-autoquant-{int4,int8,fp16}`), all `--cpuset-cpus 0-15` (P-cores), no k3d cluster; HF commit SHAs recorded before the lock. Reference: n=5 rounds, one turn-shaped request per arm per round, arms int4, int8, fp16, int4' (negative control, same weights), order ABCD/DCBA alternating, nonce-prefixed prompts, greedy, `ignore_eos`. Probe: 3 repeats (before R1, after R2, after R5). Quality: teacher-forced NLL on 4096 held-out tokens (repo text written in 2026) + 1024 wikitext2 tokens (AWQ calibration-leak check). Per-request confounds: RAPL package W, package_throttle_count delta, 1-min load, foreign CPU on cpus 0-15, pgmajfault/pswpin deltas. Run: `flock -w 10800 <scratchpad>/live.lock bench/amx-autoquant/probe.sh` (self-limits to 20 min). Prior art: [provenance:P270]. AMX arms cannot run on this host (no amx_* flags); they need an SPR+ host. Post-review fixes (2026-10-17, before any live run, predictions unchanged): stateful IRs loaded as exported (`use_cache=False` raises in optimum-intel 2.2.0), NLL at `INFERENCE_PRECISION_HINT=f32`, probe order alternates (P2 reversed), watchdog cannot outlive an aborted run, a non-default pick is cached only with 5/5 round wins and no INCONCLUSIVE note. Smoke (outside the lock): `nll.py --precs int4 --max-windows 1` returns 3.188 nats/token (held-out, 511 tokens), 2.539 (wikitext2); OVMS 2026.4.1 serves `/v3/completions` straight from the HF OV dir. Known scoring limit, kept as pre-registered: (b) counts latency only, so an O2 outcome (gate rejects int4) scores as (b) REFUTED; the NLL pass is outside the 60 s budget of (c). Run 2026-10-17 under matched `typical` background load (amendments A1-A3, [H27 run](#h27-run-matched-background-load-i9-13900t-2026-10-17)): load MATCHED on all six metrics. int4 fails the gate, with dNLL +0.0703 nats/token (wikitext2 +0.0623). int8 is -0.0033. The reference pick is int8, winning 5/5 rounds over fp16. The probe picked int8 in 3/3 repeats, so (a) holds. Each probe took 20-21 s, so (c) holds. Median turn: int4 11.43 s (11.30-11.76), int8 16.59 s (16.25-17.18), fp16 32.21 s, int4' 11.29 s, with the control in 0.95-1.005. (b) fails: the pick is 5.16 s slower than the int4 default, which won 5/5 paired rounds. That is outcome O2, not the predicted O1. Confidence high (0.95). `heavy` (robustness): INCONCLUSIVE, because the budget stopped it after R4 and throttle/min was unmatched, but the direction was the same (int8 pick, int4 fastest). DO-NOT-RE-ATTACK (b) on non-AMX hybrid clients with these weights. | REFUTED |
| H29 | On a client Intel hybrid CPU, B=1 agent decode on the P-cores spends less energy per token at the same speed when the P-cores are capped and the uncore (ring/LLC) floor is pinned high, and the uncore pin is what keeps the speed. Static config held for the whole request (prefill included); not phase-aware. On client parts `intel_uncore_frequency` sets the ring/LLC clock, not the IMC/DRAM clock. | On an i9-13900T (`intel_uncore_frequency` legacy, ring 0.8-4.7 GHz, EPP performance), OVMS 2026.4.1 + `OpenVINO/Qwen3-4B-int4-ov` from `k3d-gpu agent up` (CPU): arm UC (pod CPUs `scaling_max_freq` 2.8 GHz + uncore min = max = 4.7 GHz) vs stock A gives median decode tok/s >= 0.97x and median package J/token <= 0.90x, lower in every paired round, on BOTH gross J/token and marginal J/token against the same round's stock-arm idle; and UC beats C (cap only) by >= 3% tok/s, or by >= 5% J/token on both metrics, in every paired round. Prior: SUPPORTED 0.10, REFUTED-split 0.20, REFUTED 0.50, INCONCLUSIVE 0.20. | Pre-registered 2026-10-17 05:13 PDT before any run (no measurement taken yet). AMENDED 2026-10-17 before any run, after two refuters: (1) the per-arm marginal metric subtracted each arm's own idle, which removes a pinned ring's standing idle cost from exactly the U/UC arms; the decision now uses gross J/token AND marginal J/token against the stock arm's idle of the same round (per-arm marginal recorded, not decided on); (2) "phase-aware" dropped: the knobs are static per request; (3) up to 8 rounds, deadline-bound (Williams square balanced at 4 and 8), L on even rounds; (4) added manipulation checks: U ring >= target - 100 MHz, A pod-CPU MHz >= cap + 200 MHz (else INCONCLUSIVE); (5) decode tok/s uses completion_tokens, the J/token denominator; (6) temperature abort 95 C; (7) the locked runner refreshes sudo itself (`sudo -A -v`), since a no-tty sudo timestamp is per parent process. Physics note (refuters, not fetched): on Alder/Raptor Lake client parts the ring may share the P-core VccIA rail, which favours REFUTED. `bench/uncore-phase/run.sh` (cluster `k3dgpu-i-uncore-phase`, scratch KUBECONFIG, under the live lock, <= 20 min wall) runs `bench/uncore-phase/probe.py`. Arms A (stock), U (uncore floor pinned to `initial_max_freq_khz`), C (P-core cap), UC (C + U), L (uncore pinned 2.0 GHz, regime control); n = 5 rounds per primary arm, n = 3 for L (rounds 0/2/4); arm order a 4x4 Williams square by round (amended before any run: rotate-then-reverse-odd-rounds put only A or C first, caught by the unit test); one 192-token streamed greedy `ignore_eos` request per cell after 6 s settle and a 5 s idle window under the same arm; one unrecorded stock warm-up. Marginal J/token = (package J - idle W x t) / tokens; gross J/token also reported. Per cell: ring MHz (`current_freq_khz`), pod-CPU MHz, package temp, throttle count delta, pswpin/pswpout, load1, busy fraction inside and outside the pod. Cells with a throttle increment or > 100 MiB swap-in are excluded. Outcome -> conclusion: < 4 valid cells in a primary arm, or A tok/s spread > 10% of median -> INCONCLUSIVE; L ring > 2.1 GHz or C cores > 2.9 GHz -> INCONCLUSIVE (knob not honoured); both parts hold -> SUPPORTED; only the first part holds -> REFUTED-split (doctor suggests the cap only); first part fails -> REFUTED. Confidence = 1 - one-sided sign-test p over paired rounds (n/n rounds: 4 = 0.938, 5 = 0.969, 8 = 0.996). Every sysfs write logged and restored (probe `finally`, signal handler, runner EXIT trap from a pre-run snapshot). Prior art exists (uncore DVFS for memory-bound HPC phases, phase-aware DVFS for mobile and GPU LLM inference, the Intel KPM Uncore CRD, hardware ELC; [provenance:P291]-[provenance:P299]); a static joint core + uncore setting is the setting of [provenance:P295], so no novelty is claimed for the mechanism, only the measured delta on this test bed (search log [provenance:P290], incomplete). Run 1 (2026-10-17, [H29 run](#h29-run-i9-13900t-2026-10-17)): INCONCLUSIVE on two pre-registered gates. Stock-arm decode spread 23.2% of median (9.14-11.77 tok/s, n=7; limit 10%), and the U pin was not honoured under load (ring 2900 MHz in 7/7 U cells against a 4700 MHz target). CPUs outside the pod were 61-97% busy, and package idle was 85-117 W, so RAPL package energy carried other tenants' load. AMENDED A2 2026-10-18 before any A2 run ([H29 amendment A2](#h29-amendment-a2-matched-background-load-2026-10-18)): every arm under the same `bench/load/load.py` background load (typical headline, heavy robustness), load recorded per arm, INCONCLUSIVE for load only when unmatched under load.py TOLERANCE; predictions, gates and outcome table unchanged; A3 (before the A2 re-run) moves the temperature abort from 95 C to 99 C, since the held load keeps the package at 90-96 C. Run 2 (2026-10-18, matched-load design, [H29 run 2](#h29-run-2-matched-background-load-i9-13900t-2026-10-18)): INCONCLUSIVE at both levels. Typical (headline): load unmatched across arms (throttle/min spread 1761 > 723, load1, swap growth; E busy matched within 0.6 points) and the 99 C abort stopped it after 8 cells. Heavy (robustness, n = 6-7 per arm): load unmatched on throttle/min only (C/UC idle 6-9 W higher at the 117 W PL1, so the cap shifts package budget to the E-core neighbour), stock decode spread 12.9% > 10%, and the U pin not honoured (ring 2900 MHz vs 4700; 0 of 16 U cells across all runs at target). Gated-out direction REFUTED: heavy UC/A decode 0.956x [0.925-0.981], gross J/token 1.084x [1.049-1.129], higher in 6/6 rounds (sign-test confidence 0.984). Confidence the run cannot decide: 0.95; that a matched run here would return SUPPORTED: about 0.02. Not testable as written on this host: the ring never reaches the pin while the E-cores carry load. | INCONCLUSIVE |
| H22 | Cross-engine speculative decoding (draft on the iGPU, target on the P-cores) raises single-stream decode tok/s over both the target alone and same-engine (P-core) drafting. | On an i9-13900T, OVMS 2026.4.1, `OpenVINO/Qwen3-4B-int4-ov` target on the 16-CPU P-core balloon, `OpenVINO/Qwen3-0.6B-int4-ov` Fast Draft with `draft_device: "GPU"` (UHD 770), `num_assistant_tokens` 5: median paired decode tok/s C/B >= 1.05 and C/A >= 1.10. Written before measuring (2026-10-17T05:08-07:00): REFUTED expected, C/B ~ 0.68 (0.55-0.85), C/A ~ 0.82 (0.65-1.0), because GenAI 2026.4.1 runs draft then verify serially (no overlap) and the UHD 770 runs a 0.6B draft slower than the P-cores. | Pre-registered 2026-10-17 before any H22 run ([H22 pre-registration](#h22-pre-registration-i9-13900t)). `bench/hetero-spec/probe.sh`: one OVMS pod, 5 graphs, per-request cyclic-rotation interleave, n=8 rounds, `bench/hetero-spec/analyze.py` verdict. Refuted if C/B <= 0.95 in >= 7/8 rounds or C/A < 1.0. Result ([H22 result](#h22-result-i9-13900t-2026-10-17)): the outcome table's noise row fired (A 11.3 tok/s, outside 15.7 +-15%; 8/8 rounds throttle-flagged). Directionally C lost to B in 8/8 rounds, median C/B 0.78 (95% CI 0.69-0.85), C/A 1.07. The pre-registered re-run ([H22 re-run](#h22-re-run-i9-13900t-2026-10-17)) started in a quiet window (load1 3.96, package 79 C) and the noise row fired again: A 12.7 tok/s (19% under 15.7), 8/8 rounds throttle-flagged, because the probe's own 16-CPU load heats the package (35 W rated base power, run at PL1 = PL2 = 117 W) to 86-97 C. Directionally C lost to B in 8/8 rounds again, median C/B 0.73 (95% CI 0.63-0.75), C/A 0.91. Both runs: C < B in 16/16 rounds. Missing probe: a run on a package that does not throttle under the target's own load. Scope: serial OVMS Fast Draft, UHD 770, T-SKU rated 35 W base power but run at PL1 = PL2 = 117 W, AVX-VNNI (no AMX), no NPU. | INCONCLUSIVE |
| H34 | Same-engine CPU Fast Draft speeds up and cheapens single-stream decode: OVMS 2026.4.1 Fast Draft with the `OpenVINO/Qwen3-0.6B-int4-ov` draft on the P-cores (H22 arm B) beats the `OpenVINO/Qwen3-4B-int4-ov` target alone (H22 arm A), both on the 16-CPU P-core balloon, under a controlled, matched background load on the E-cores. | Written 2026-10-17T22:04-07:00, before any H34 measurement. At the `typical` load level (headline): median paired B/A decode tok/s >= 1.20 and median paired B/A package J/token <= 0.85, with the tok/s ratio's 95% bootstrap CI above 1.0 and the J/token ratio's CI below 1.0. `heavy` is the robustness check, same rule. Expected from H22's exploratory B/A (1.35 and 1.32 tok/s, 0.76 and 0.82 J/token, n = 8 each on a noisy box): SUPPORTED at typical, prior 0.55. | Pre-registered ([H34 pre-registration](#h34-pre-registration-i9-13900t)). `bench/hetero-spec/h34.sh`: one OVMS pod, arms A and B, `bench/load/load.py` level on the E-cores, pairs in A B / B A order, n >= 8 pairs per level, a 5 s load window before every request, `bench/hetero-spec/h34.py` verdict. REFUTED if the tok/s CI upper bound < 1.20 or the J/token CI lower bound > 0.85. INCONCLUSIVE only if load is unmatched across arms (`load.py` TOLERANCE), < 8 complete pairs, or the CIs leave the effect unresolved. Result ([H34 result](#h34-result-i9-13900t-2026-10-17)): at `typical`, load MATCHED, n = 12 pairs, B/A decode tok/s 1.389 (95% CI 1.285-1.447) and package J/token 0.751 (0.714-0.828), B better on both in 12/12 pairs (sign p = 0.00049, confidence 0.9995); A 9.73, B 12.66 tok/s on a busy box (E-cores 57-59% busy, load1 ~22, throttling). `heavy` (robustness): INCONCLUSIVE, load unmatched (swap growth spread 80 > 64 MiB); descriptively B won 8/8, tok/s ratio 1.21, J/token ratio 0.85. Scope: OVMS 2026.4.1 serial Fast Draft, gamma 5, this host, single stream. | SUPPORTED |
| H5 | On a hybrid CPU whose package throttles, the opt-in prefill guard (`K3D_GPU_PREFILL_GUARD`, an E-core `scaling_max_freq` cap) protects P-core prefill from an E-core-pinned CPU neighbour. | At one or more cap levels the neighbour's median P-core prefill penalty (median `ttft_s` over the quiet median, minus 1) is at most half the guard-off penalty, and the p90 penalty is below the guard-off p90 penalty. | Pre-registered in [docs/prefill-guard.md](docs/prefill-guard.md#pre-registered-ab-test-h5): stage 1 host-level, arms quiet / guard off / caps at 75%, 50% and the E-core minimum, five Latin-square rounds per neighbour method (`matrixprod`, `loop`), n = 5 per arm; neighbour bogo-ops/s, a tool-latency proxy, decode tok/s and package power always reported. Stage 2 repeats the chosen level through `k3d-gpu up` and checks the NRI write path and the `down` restore. Refuted if no cap level reaches half; inconclusive if the guard-off penalty is inside the quiet spread or the quiet arm drifts. Amended 2026-10-19 (A1) before any H5 measurement: the H3 run's OVMS, model and unique ~1000-token prompt on CPUs 0-14, the neighbour in H3's `stress-ng` image under `--cpuset-cpus 17-31`, one lock hold per round, spread and drift defined, verdict per method with `matrixprod` deciding H5, stage 2 only after a SUPPORTED stage 1; see [H5 amendment A1](#h5-amendment-a1-stage-1-harness-and-definitions-2026-10-19). Stage 1 run 2026-10-20 (50 runs, every gate passed): no cap level reaches half the guard-off penalty. `matrixprod`: N0 +40.6% (bar +20.3%), C75 +36.4%, C50 +28.1%, CMIN +23.3%, at neighbour losses of 3.6%, 35.0% and 72.4%; `loop`: N0 +39.8%, best CMIN +23.7%; package power 114-115 W in every neighbour arm, capped or not (PL1 117 W). Stage 2 not run (no level chosen). See [H5 run](#h5-run-stage-1-i9-13900t-2026-10-20). | REFUTED |
| H40 | The agent harness reproduces the BFCL board: BFCL v4 (`bfcl-eval==2025.12.17`, gorilla `f7cf7359`) in the board's own mode (registry `Qwen/Qwen3-4B-Instruct-2507-FC`, BFCL applies the chat template and calls `/v1/completions`) on the fixed 100-id subset S100, sent through LiteLLM and the recording proxy, scores bf16 `Qwen/Qwen3-4B-Instruct-2507` @ `cdbee75f` on vLLM CUDA (arm A3ref) like the leaderboard row. | S100 accuracy in [77.9, 91.9]: the board-weighted 84.9 (row "Qwen3-4B-Instruct-2507 (FC)" of https://gorilla.cs.berkeley.edu/data_overall.csv, read 2026-10-17) +/- 7, the binomial 95% half-width at n=100. Every category within its own binomial 95% CI of the board value. Confidence 0.75. | `k3d-gpu bench agent bfcl --mode board --arm A3ref --level none`, n=100, one block, measurement budget 20 min ([pre-registration](#h40-h48-pre-registration-agent-benchmark-2026-10-17)). SUPPORTED: both hold, and H41 may be read. REFUTED: overall outside the band; the harness differs from the board (template, parser, handler) and H41 is not scored until fixed. INCONCLUSIVE: fewer than 100 ids completed, or failed requests. | SUPPORTED |
| H41 | Serving a 4-bit build keeps tool-calling quality: on BFCL-S100 in server-fc mode (the engine's own tool parser parses the calls), each of A1 (k3d-gpu CPU, OVMS int4), A3 (k3d-gpu RTX 4000, AWQ 4-bit), B1 (EAT stock vLLM CPU, bf16), C1 (Ollama Q4_K_M) and C2 (llama.cpp Q4_K_M) scores within 8 points of A3ref (bf16, vLLM CUDA) run in the same mode. | Per arm, the one-sided paired-bootstrap 95% lower bound of (arm - A3ref) on the shared completed ids is >= -8 points. A1, A3, C2 hold (0.7 each); B1 holds but completes only 60-100 ids (0.6); C1 holds (0.55, the highest risk: Ollama's template and tool path). | `k3d-gpu bench agent bfcl --mode server-fc`, typical load, A B ... B A order in 10-min halves, 20 min per arm, `load.py matched` across arms. Read only after H40 is SUPPORTED. Per arm: SUPPORTED when the lower bound >= -8; REFUTED when the upper bound < -8; INCONCLUSIVE when the CI straddles -8, fewer than 60 shared ids, or load unmatched (exit 3). | INCONCLUSIVE |
| H42 | On Intel's real EAT banking flow (`intel/enterprise-ai-blueprints` v1.0.0 banking-assistant, task set B24 at N=1, typical load), the k3d-gpu CPU stack A1 (OVMS, OpenVINO int4 self-export, P-cores) succeeds as often as the GPU stack and finishes a task in a third of the time of the EAT stock stack B1 (vLLM CPU, bf16) or less. A stack-vs-stack claim: A1 and B1 differ in runtime, placement and weight precision (int4 vs bf16) together, so the ratio does not isolate any one of them (amendment 16). | (1) Success on completed task ids, stated with the count, every arm that finishes: A1 >= 0.75, A3 >= 0.75, C2 >= 0.70; C1 <= A1 - 10 points (4k-context truncation, 0.5). (2) Paired median time ratio A1/B1 on the task ids both completed <= 0.35 (point 0.15, range 0.08-0.35, confidence 0.75). (3) A3 median time per task < A1 (0.85). (4) A2 > A1 (0.8). (5) B1 completes <= 12 of 24 tasks in 20 min (0.7). | `k3d-gpu bench agent run --tasks B24` per arm (A1 A2 A3 B1 C1 C2), A B ... B A in 10-min halves, `--mcp-url` end-state grading with `--reset` after each mutating task; sampling `agent_local`, agent memory off (amendments 7-20). SUPPORTED: (1)-(4) hold with load matched. REFUTED: A1/B1 time-ratio CI lower bound > 0.5, or A1 success < 0.6. Otherwise, or load unmatched, INCONCLUSIVE. Censored tasks are lower bounds, never failures. | INCONCLUSIVE |
| H43 | With the agent sandbox on the E-cores (coding-agent-maf, C12, `k3d-gpu sandbox up`), heavy E-core load stretches A1's median time per task by at most 1.5x over typical load while success stays within 10 points. | A1 holds on success but fails on time: ratio 1.6-2.5x, because the sandbox pods share the E-cores with the load and lack the P/E isolation the model server gets. Confidence 0.6. | `k3d-gpu bench agent run --tasks C12` for A1, A3, B1 at typical and at heavy, A B ... B A halves per level. SUPPORTED: ratio <= 1.5 and delta success >= -10 points. REFUTED: ratio CI lower bound > 1.5. Otherwise INCONCLUSIVE. Wired by amendments [W1](#h43-and-h45-amendment-w1-the-live-runner-2026-10-19) and [W2](#h43-amendment-w2-the-code-mode-reserve-2026-10-19) (B1 not run; the ABBA halves over levels within A1; paired ratio). Run 2026-10-20 ([H43 run](#h43-run-i9-13900t-2026-10-20)): A1 completed 1 C12 id at both levels in four 143 s blocks (C01: 66.9 s typical, 122.7 s heavy), ratio 1.84 with a degenerate CI 1.84-1.84, delta success 0 points; REFUTED by the rule on that one task. A3 (context): 1.03 (CI 1.01-1.07) on 12 ids. | REFUTED |
| H44 | Busy-box robustness: on B24's first 12 tasks, the p95 turn-latency inflation heavy/none is <= 1.25x for A1 (inference on the P-cores, load on the E-cores) and >= 1.5x for B1 (EAT stock floats onto the loaded E-cores). | A1 <= 1.25 (0.65). B1 >= 1.5 (0.6). | `k3d-gpu bench agent run --tasks B24 --limit 12` for A1 and B1, blocks none/heavy/none/heavy at 5 min each (20 min per arm). SUPPORTED: both hold. REFUTED: A1 inflation CI lower bound > 1.25, or B1 <= 1.25 as well (no difference). Otherwise INCONCLUSIVE. | INCONCLUSIVE |
| H45 | Graded agents per box (largest N on the ramp 1, 2, 4, 8 at which pooled p95 turn latency <= 10 s, success >= the arm's N=1 success - 10 points, error rate <= 1% and >= 60 window requests) on the read-only banking set B15-RO at typical load. | A3 >= 8 (0.8). A1 in [1, 4] (0.55). C1 <= 1 (0.7, `OLLAMA_NUM_PARALLEL=1` serializes). C2 <= A1 (0.6). B1 not run, predicted 0. An arm's prediction is refuted by a scored APB outside its range; a bracket that still contains the prediction decides nothing. | `k3d-gpu bench agent apb` per arm (A1 A3 C1 C2), 5 min per step. The synthetic `k3d-gpu capacity --slo 10` APB is reported beside it, not scored. Wired by amendment [W1](#h43-and-h45-amendment-w1-the-live-runner-2026-10-19) (the ramp split over 20-min holds; REFUTED when any prediction is refuted). Run 2026-10-20 ([H45 run](#h45-run-i9-13900t-2026-10-20)): A3 8 (holds), A1 0 (N=1: p95 26.1 s, 31 window requests; refuted), C1 0 (holds), C2 0 vs A1 undecided (amendment 27 load unmatched: pre throttle and swap). Synthetic capacity (3 min, not scored): every arm out of budget. | REFUTED |
| H46 | Joules per successful banking task (differential: block energy minus the mean power of a load-only control window at the same load, times the block time, over the successful tasks): A1 needs at most 0.4x of B1's. | A1/B1 <= 0.4 (0.7). A3 < A1 including dGPU power (0.7). A2 > A1 (0.6). | H42's blocks plus one 120 s `k3d-gpu bench agent control` window per arm with its server up and idle. SUPPORTED: ratio CI upper bound <= 0.4. REFUTED: ratio CI lower bound > 0.7. INCONCLUSIVE: B1 with zero successes (J per success undefined), load unmatched, or RAPL unreadable. | INCONCLUSIVE |
| H47 | CPU draft speculative decoding helps agent tasks: A4 (A1 plus a CPU draft, `OpenVINO/Qwen3-0.6B-int4-ov` @ `f864c610`) cuts B24's median time per task by >= 10% vs A1 with delta success >= -5 points. Runs only if H34 is SUPPORTED when the campaign starts; otherwise not run, recorded. | A 0-15% gain; INCONCLUSIVE likely (tool-call turns are 40-96 tokens, little to amortize, and the draft competes for the P-cores). Confidence it is SUPPORTED: 0.35. | `k3d-gpu bench agent run --tasks B24`, A4 vs A1 paired and interleaved at typical load. SUPPORTED: gain CI lower bound >= 10% with success held. REFUTED: gain CI upper bound < 0. Otherwise INCONCLUSIVE. Design replaced 2026-10-18, before any H47 run, by the within-hold interleaved design ([H47 and H49 pre-registration](#h47-and-h49-pre-registration-within-hold-interleaved-design-2026-10-18)): A1 and A4 as two model names in one OVMS pod, one hold, pairs A B B A over B24's task ids, load sampled per task and matched per pair (rule L1); the statistic, thresholds and prediction above are unchanged. Result ([H47 run](#h47-run-i9-13900t-2026-10-18)): 24 pairs in 2 holds, none void or over budget; load rule L1 matched 13 of 24 pairs (< 16 and < 75%), so INCONCLUSIVE. On the 13 matched pairs: gain 21.2% (95% CI 15.8-27.5%), delta success 0 (11/13 both arms), J per attempt A4/A1 0.82 (0.77-0.88); 0.98 of bootstrap resamples show a gain of at least 10%. | INCONCLUSIVE |
| H48 | The graders are sound: remote GLM-5.3 through harvest (arm R, gaudi.farmgpu.net, never a node's :8299) passes >= 11 of the measured items among 4 B24 tasks and 8 S100 ids, under a hard cap of 20 LLM requests. | >= 11 pass (0.8). | One `k3d-gpu bench agent proxy --max-requests 20 --key-file FILE --sampling agent_remote` in front of harvest carries every request: first `bfcl --mode server-fc --limit 8 --proxy-url` (8 single-turn ids, 8 requests), then `run --tasks B24 --ids B-T1-U1001,B-T3-U1002,B-T5-U1003,B-T8-U1001` through the same proxy; a task cut by the request cap or by the 256-token cap (`finish_reason=length`) is unmeasured. Latency and energy are not reported. SUPPORTED: >= 10 measured and <= 1 measured item fails. REFUTED: >= 3 measured items fail, and every failed item's task and grader are audited before H41-H46 are read. Otherwise INCONCLUSIVE (amendment 17; originally >= 11 / 10 / <= 9 of 12). | REFUTED |
| H49 | Inside EAT's banking flow (`intel/enterprise-ai-blueprints` v1.0.0 banking-assistant, B24 at N=1, typical load), the k3d-gpu auto-config stack A1 (OVMS int4 on the P-cores with the CPU Fast Draft of #116) delivers far more tasks within the 120 s turn budget than EAT stock B1 (vLLM CPU, bf16), when both arms run in one hold, interleaved task by task. A stack-vs-stack claim (amendment 16). | Written 2026-10-18T07:50-07:00, before any H49 measurement. Within-budget success (graded pass, every turn <= 120 s, task <= the 240 s cap; turn timeouts and cap hits are measured failures, not missing data): A1 >= 0.70 (0.75), B1 <= 0.10 (0.8), paired A1 - B1 >= +60 points (0.7). Secondary, not gated: J per completed task, paired J per attempt, p95 turn, with over-budget counts per arm. | [H47 and H49 pre-registration](#h47-and-h49-pre-registration-within-hold-interleaved-design-2026-10-18): both servers in one cluster in one hold, pairs over B24's task ids in A B B A order, a reset to seed after every mutating attempt, load sampled per attempt and matched per pair on background CPU (rule L1), up to 12 holds for 24 pairs. SUPPORTED: paired difference CI lower bound >= +30 points and A1 within-budget success >= 0.6, load rule and idle-arm check passed. REFUTED: CI upper bound < +30 points. Otherwise INCONCLUSIVE (also: < 16 matched pairs, < 75% of pairs matched, > 2 void pairs, or the idle arm's server above 0.5 CPU). Result ([H49 run](#h49-run-i9-13900t-2026-10-18)): 24 pairs over 7 valid holds (hold 1 void, amendment P2), no void pair; load rule L1 matched 2 of 24 pairs (< 16 and < 75%) and the arm-level swap check failed, so INCONCLUSIVE. B1's unpinned vLLM displaced the E-core load, so `bg_cpu` was lower in B1's window in 22 of 24 pairs. Not registered, all 24 pairs: within-budget success A1 20/24, B1 4/24 (19 over budget), paired difference +0.67 (95% CI +0.42 to +0.88). | INCONCLUSIVE |
| H47b | H47's hypothesis (CPU draft speculative decoding helps agent tasks: A4 cuts B24's median time per task by >= 10% vs A1 with delta success >= -5 points), re-run with pairs matched on idle pre-windows (load rule L2) instead of during-attempt `bg_cpu` (L1). | As H47: a 0-15% gain; SUPPORTED at 0.35. No prediction of the matched-pair count (see the pre-registration's null estimate). | Pre-registered 2026-10-18T14:35-07:00, before any H47b measurement; see [H47b and H49b pre-registration](#h47b-and-h49b-pre-registration-load-rule-l2-pairs-matched-on-pre-windows-2026-10-18). Arms, tasks, budgets, A B B A order, holds (at most 2 of 25 min), voids, outcome classes, statistic and thresholds as H47. Only change: rule L2. Before each attempt, with both arms idle, `load.py settle` then a 10 s pre-window; a pair is matched when both pre-windows show the full load and agree on load-CPU busy, throttle/min, swap used and swap growth within amendment 27's `TOLERANCE`; arm-level check `matched_a27` on the matched pairs' pre-windows; during-attempt `bg_cpu`, package W and throttle reported, never gated. INCONCLUSIVE below 16 matched pairs or 75% matched, or on the arm-level or idle check. `LIVE_MODE=pairs live.sh H47b`, `agentbench pairs-report --hyp H47b`. Result ([H47b run](#h47b-run-i9-13900t-2026-10-18)): 10 pairs in the 2 permitted 25-min holds (each stopped at the 600 s pair-start guard; settle gaps took a median 35 s, 5 of 20 hit the 60 s cap), none void or over budget, idle check 0.0006 CPU; L2 matched 4 of 10 pairs (6 unmatched: throttle/min 5, busy 1), under 16 and under 75%, so INCONCLUSIVE; the arm-level check on the 4 passes. On the 4 matched pairs: gain 18.1% (95% CI 5.0-19.2%), delta success 0 (4/4 both arms), J per attempt A1/A4 1.15 (1.00-1.18); derived confidence 0.95 of bootstrap resamples at a gain >= 10%, 0 below 0 (4 pairs: direction only). Reproduce: the run section. | INCONCLUSIVE |
| H49b | H49's hypothesis (inside EAT's banking flow, A1 delivers far more B24 tasks within the 120 s turn budget than EAT stock B1, both arms in one hold, interleaved), re-run with pairs matched on idle pre-windows (load rule L2) instead of during-attempt `bg_cpu` (L1), which B1's unpinned vLLM depressed in 22 of 24 H49 pairs. A stack-vs-stack claim (amendment 16). | As H49: A1 within-budget success >= 0.70 (0.75), B1 <= 0.10 (0.8), paired A1 - B1 >= +60 points (0.7). No prediction of the matched-pair count. | Pre-registered 2026-10-18T14:35-07:00, before any H49b measurement; see [H47b and H49b pre-registration](#h47b-and-h49b-pre-registration-load-rule-l2-pairs-matched-on-pre-windows-2026-10-18). Arms, tasks, budgets, A B B A order, holds (40 min, until 24 pairs or 12 holds), voids (> 2 void pairs INCONCLUSIVE), outcome classes, statistic and thresholds as H49. Only change: rule L2, as in H47b. SUPPORTED: paired difference CI lower bound >= +30 points and A1 within-budget success >= 0.6, with L2 and the idle-arm check passed. REFUTED: CI upper bound < +30 points. Otherwise INCONCLUSIVE. `LIVE_MODE=pairs live.sh H49b`, `agentbench pairs-report --hyp H49b`. Result ([H49b run](#h49b-run-i9-13900t-2026-10-18-and-19)): 24 pairs over 10 of 12 holds; 1 void pair (pair 8: hold 4 killed from outside after both attempts, before the pair row), idle check 0.0008 CPU; L2 matched 3 of 23 complete pairs (20 unmatched: throttle/min 10, busy 8, swap used 2), under 16 and under 75%, so INCONCLUSIVE; the arm-level check on the 3 passes. On the 3 matched pairs: A1 3/3, B1 1/3, paired difference +0.67 (95% CI 0.00 to +1.00); derived confidence 0.97 of bootstrap resamples at >= +0.30 (3 pairs: direction only). Outcomes, not gated, all pairs: during-attempt E-core busy A1 67.5% / B1 89.9%, throttle 65 / 3749 per min, package 101.7 / 99.0 W. Not registered, all 23 complete pairs: within-budget success A1 19/23, B1 5/23, paired difference +0.61 (95% CI +0.35 to +0.83). Reproduce: the run section. | INCONCLUSIVE |
| H50 | (Model, arms and sampling superseded by [amendment M1](#crescent-amendment-m1-gemma-4-26b-a4b-qat-on-the-cpu-rows-2026-10-20): Gemma 4 26B-A4B QAT.) On EAT's own target CPU (Xeon 6741P, Granite Rapids, AMX), inside EAT's banking flow, the k3d-gpu CPU stack A1 (OVMS int4 ~~with the CPU Fast Draft~~, `agent manifest`; draft OFF per [amendment A4](#crescent-amendment-a4-a1-is-k3d-gpus-best-cpu-config-for-the-xeon-2026-10-20): H34x REFUTED it on this Xeon) still delivers far more B24 tasks within the 120 s turn budget than EAT stock B1 (vLLM CPU bf16, `xeon-values.yaml` as shipped, AMX on), both arms in one hold, interleaved. H49b's comparison moved to the hardware EAT is built for. A stack-vs-stack claim (amendment 16). | Superseded by [amendment M1](#crescent-amendment-m1-gemma-4-26b-a4b-qat-on-the-cpu-rows-2026-10-20), which restates the predictions; as first written: Written 2026-10-20, before any measurement on the Xeon. A1 within-budget success >= 0.70 (0.8). B1 >= 0.60 (0.7): with AMX bf16 its turns should fit 120 s. Paired A1 - B1 in [-0.15, +0.20] (0.6). L2 matches >= 16 pairs and >= 75% (0.6), and the gates are read first, so the expected verdict is REFUTED (0.5), INCONCLUSIVE (0.45, mostly the L2 gates), SUPPORTED (0.05). Not gated: median attempt A1/B1 <= 0.7 (0.6). | H49b's design, statistic, outcome classes, grader, gates and thresholds on `crescent`; see [H50-H52 pre-registration](#h50-h52-pre-registration-the-xeon-6741p-2026-10-20). Holds of at most 40 min until 24 pairs or 8 holds. SUPPORTED: paired difference CI lower bound >= +30 points and A1 >= 0.6, with L2, the void and idle-arm checks passed. REFUTED: CI upper bound < +30 points. Otherwise INCONCLUSIVE. `bench/eatflow/crescent/pairs-hold.sh H50 <n>` (`LIVE_MODE=pairs LIVE_CAP=2370 live.sh H50`, KV 40 GiB), `agentbench pairs-report --hyp H50 --arm-a A1 --arm-b B1`. Model, arms, sampling and predictions from [amendment M1](#crescent-amendment-m1-gemma-4-26b-a4b-qat-on-the-cpu-rows-2026-10-20) (Gemma 4 26B-A4B QAT; breaks direct replication of H49b). Analysis rule L3 (primary) from [amendment A3](#crescent-amendment-a3-analysis-rule-l3-for-the-crescent-pairs-rows-2026-10-20). | PENDING |
| H50o | (Model, arms and sampling superseded by [amendment M1](#crescent-amendment-m1-gemma-4-26b-a4b-qat-on-the-cpu-rows-2026-10-20): Gemma 4 26B-A4B QAT.) H50's comparison with arm B3, EAT's own OVMS chart (`core/helm-charts/ovms` at release-1.0.3-hotfix, stock values, `openvino/model_server` 2025.4, the closest OpenVINO/ model it pulls, `OpenVINO/Qwen3-4B-int4-ov`) instead of B1: A1 delivers far more B24 tasks within budget than B3. With H50 it splits B1 -> A1 into EAT switching its own engine (B1 -> B3) and what k3d-gpu adds on top (B3 -> A1). | Superseded by [amendment M1](#crescent-amendment-m1-gemma-4-26b-a4b-qat-on-the-cpu-rows-2026-10-20), which restates the predictions; as first written: Written 2026-10-20, before any measurement. B3's stock graph has no tool parser (the chart's `ovms --pull` writes none) and its model reasons by default, so its tool calls arrive as text: B3 within-budget success <= 0.10 (0.7), paired A1 - B3 >= +60 points (0.65). Pair yield: a B3 attempt should end within 30-90 s (a failed tool call or a reasoned text answer), so a 40-min hold should run 15-25 pairs and 24 pairs fit 2-3 holds; L2 passes (0.6), so SUPPORTED (0.5), INCONCLUSIVE (0.45). | As H50 with arm B = B3 (same pairs, gates, thresholds, statistic, cluster per hold `k3dgpu-e-h50o`); holds of at most 40 min until 24 pairs or 6 holds. The B1 -> B3 and B3 -> A1 splits are described from the two rows' matched pairs, never gated. `bench/eatflow/crescent/pairs-hold.sh H50o <n>`, `agentbench pairs-report --hyp H50o --arm-a A1 --arm-b B3`. Model and predictions from [amendment M1](#crescent-amendment-m1-gemma-4-26b-a4b-qat-on-the-cpu-rows-2026-10-20). Analysis rule L3 from [amendment A3](#crescent-amendment-a3-analysis-rule-l3-for-the-crescent-pairs-rows-2026-10-20). | PENDING |
| H51 | (Model, arms and sampling superseded by [amendment M1](#crescent-amendment-m1-gemma-4-26b-a4b-qat-on-the-cpu-rows-2026-10-20): Gemma 4 26B-A4B QAT.) Graded agents per box on the Xeon 6741P: H45's ramp, gates and scoring (B15-RO, typical load, 300 s steps, pooled p95 turn <= 10 s, success >= the arm's N=1 success - 10 points, errors <= 1%, >= 60 window requests), extended to N=16, for A1 and B1. | Superseded by [amendment M1](#crescent-amendment-m1-gemma-4-26b-a4b-qat-on-the-cpu-rows-2026-10-20), which restates the predictions; as first written: Written 2026-10-20, before any measurement. A1 in [1, 8] (0.5). B1 in [0, 4] (0.5). B1 <= A1 (0.55). | H45's `apb` runner and scorer (`apb-score --hyp H51`, ramp 1, 2, 4, 8, 16), one fresh cluster per hold, at most 4 holds of at most 40 min. A prediction holds when the arm's bracket lies inside its range and is refuted when it lies outside; B1 <= A1 is decided only on matched load. REFUTED when any prediction is refuted, SUPPORTED when all three hold, else INCONCLUSIVE. Runs only if H50's holds end within their cap. Model and predictions from [amendment M1](#crescent-amendment-m1-gemma-4-26b-a4b-qat-on-the-cpu-rows-2026-10-20). | PENDING |
| H52 | A plain `k3d-gpu up` (no flags, the auto-adapt default) on the Xeon 6741P host as found detects and enables every engine actually present and advertises each usable one to the scheduler, with nothing missing and nothing phantom; engines present but not configured on the host are named, not advertised. | Written 2026-10-20 from an inventory read from sysfs, lspci and CPUID, not from k3d-gpu. 4 x Arc Pro B70 (xe) as `gpu.intel.com/xe: 4`; AMX TILE, BF16, INT8, FP16 and 14 AVX-512/VNNI labels; no hybrid, NUMA, NVIDIA, Gaudi or NPU objects; DSA, IAA and QAT named as present but unconfigured (0.4: `up` may not name them). | The table in [H50-H52 pre-registration](#h50-h52-pre-registration-the-xeon-6741p-2026-10-20): `k3d-gpu doctor`, `K3D_GPU_CLUSTER=k3dgpu-h52 k3d-gpu up`, `kubectl get node -o json`, `k3d-gpu test`, and one enumeration pod per B70. SUPPORTED: every row matches. REFUTED: any row does not. Run 2026-10-20 ([H52 run](#h52-run-xeon-6741p-2026-10-20)): the four B70s (`gpu.intel.com/xe: 4`, `xmx=true`), AMX (TILE, BF16, INT8, FP16) and all 14 AVX-512/VNNI labels detected and reached pods, nothing phantom; but no media engine label on `xe`, and `up` does not name the unconfigured DSA, IAA and QAT (`doctor` does). | REFUTED |
| H52b | After `k3d-gpu host-setup --apply` configures the host's DSA, IAA and QAT (the fix that adds that path, merged first), a plain `k3d-gpu up` also advertises them, and each one reaches a pod; the host is then restored exactly. | Written 2026-10-20, before the fix exists. DSA, IAA and QAT allocatable > 0, each at the count host-setup reports configuring; their test pods see `/dev/dsa`, `/dev/iax`, `/dev/vfio` (0.6). The other rows read as H52's expected table, media labels included ([amendment D1](#h52-amendment-d1-the-fixes-and-h52b-2026-10-20)). | Runs after H52, once the host-setup fix is merged; same procedure plus `host-setup --apply` before `up` and its printed revert after. SUPPORTED: the three engines advertised and reached, the H52 rows unchanged, and the host inventory read back equal to the one taken before. REFUTED: any of these fails. | PENDING |
| H53 | Inside EAT's banking flow on the Arc Pro B70s with Qwen3.8-27B (thinking off), k3d-gpu's own GPU choice AG (OVMS int4 on one card, `agent manifest`, auto device) delivers far more B24 tasks within the 120 s turn budget than B2, "EAT PR #21 nearest profile (Qwen3.6-27B) on Qwen3.8 weights, fp16" (vLLM XPU v0.28.0, TP 4). A stack-vs-stack claim. | Written 2026-10-20, before any H53 measurement. AG >= 0.70 (0.6); B2 boots by ladder rung (b) (0.6); B2 >= 0.50 (0.5); block mean AG - B2 in [-0.15, +0.30] (0.5). INCONCLUSIVE (0.6), REFUTED (0.3), SUPPORTED (0.1). | Solo holds in the order AG, B2, B2, AG, AG, B2 (up to 8), blocks = hold pairs (2k-1, 2k), block-level inference: mean of per-block d over >= 3 counted blocks, t-interval. SUPPORTED: lower bound >= +30 points and AG >= 0.6, L3 gates passed. REFUTED: upper bound < +30 points. Else INCONCLUSIVE. `bench/eatflow/crescent/solo-hold.sh N ARM`, `agentbench solo-report --hyp H53 --arm-a AG --arm-b B2`. See [H53 pre-registration](#h53-pre-registration-eats-banking-flow-on-arc-pro-b70-2026-10-20). | PENDING |
| H54a | On the Xeon 6741P, Gemma 4 26B-A4B (QAT weights) single-stream: the best optimized CPU arm, chosen by the pre-registered source and speed rule, decodes at N=1 at least 3x faster than EAT stock B1g (vLLM CPU v0.24.0 bf16, `xeon-values.yaml`'s Gemma entry on the QAT bf16 weights), with a 50-item quality spot-check no more than 10 points below B1g's. A microbench, not an agent claim. | Written 2026-10-20, before any H54 measurement. N=1 median decode tok/s: B1g in [4, 15] (0.6); Lg (llama.cpp CPU, Google QAT Q4_0) in [25, 70] (0.6); A1g (k3d-gpu auto, OVMS CPU, QAT-matched IR) in [15, 60] (0.5), A1g/A1o in [0.85, 1.02] (0.6); Vq (vLLM CPU v0.32.0 bf16) in [6, 18] (0.5), Vq-mtp/Vq in [1.4, 2.5] (0.5); Lg-mtp/Lg in [1.2, 2.2] (0.4); Sg (SGLang CPU AMX) deliverable (0.6). The chosen arm is Lg or Lg-mtp (0.55). B3g not deliverable (0.8). SUPPORTED (0.65), INCONCLUSIVE (0.25), REFUTED (0.1). Not gated: TTFT on a 1,000-token prompt, aggregate tok/s at N=4 and 16 (A1g N=16/N=1 <= 1.5, 0.5), J/token. | [H54 pre-registration](#h54-pre-registration-gemma-4-26b-a4b-qat-and-qwen38-microbenches-on-crescent-2026-10-20): `bench/llm_load.py` per arm (N=1: 8 requests, N=4: 16, N=16: 32, 256 tokens, greedy, `ignore_eos`), `bench/energy.sh` RAPL per phase, GSM8K items 0-49 graded by exact match, one arm serving at a time, at most 3 holds of at most 40 min. Statistic: ratio of the chosen arm's median N=1 decode to B1g's, 95% bootstrap CI over requests. SUPPORTED: CI lower bound >= 3.0 and the quality gate passed. REFUTED: CI upper bound < 3.0, or every arm with a point ratio >= 3.0 fails the quality gate. Otherwise INCONCLUSIVE (also: B1g not deliverable, or a void phase not re-run). Run 2026-10-21, 3 holds ([H54a run](#h54a-run-xeon-6741p-2026-10-21)): stock B1g stalled in both its holds (vLLM v0.24.0 `EngineDeadError` mid-decode, cause open; no scored phase, 0/50 GSM8K, all errored), so `h54.py score-a` has no base and no chosen arm. Measured: Lg 48.74, Lg-mtp 54.52, A1g 23.25, A1o 21.73 tok/s N=1 median, GSM8K 50, 50, 49, 49 of 50; each scored phase exists only under amendment R4 (its own foreign CPU 4.4-4.8 CPU-s/s). Vq, Vq-mtp, Vi, Sg, B3g not run (cap). Descriptive, not scored: Lg-mtp over B1g's 2 hold-2 rates 3.02 (CI 2.90-3.06). | INCONCLUSIVE |
| H54q | QAT matters at int4 on this model: on GSM8K items 0-199, the QAT-matched OpenVINO IR OVQ (INT4_SYM, group 32, built from Google's QAT bf16 weights) is non-inferior to the bf16 QAT reference R within 5 points, and the published base-model IR OVB (`OpenVINO/gemma-4-26b-a4b-it-int4-ov`; ~~INT4_ASYM group 128~~ AWQ INT4 asymmetric group 64, router layers unquantized, per its `openvino_config.json`: [note N1](#h54-note-n1-what-ovb-is-2026-10-21)) scores below OVQ. | Written 2026-10-20, before any measurement. R accuracy in [0.85, 0.96] (0.6). OVQ - R in [-0.03, +0.02] (0.6). GGUF Q4_0 (Lg) - R in [-0.03, +0.02] (0.6). OVB - R in [-0.07, 0] (0.5). The OVB < OVQ part resolves on 200 items (0.2). INCONCLUSIVE (0.65), SUPPORTED (0.2), REFUTED (0.15). | As H54a's quality protocol on items 0-199 of `openai/gsm8k` `main` test @ `740312ad`; items 0-49 come from H54a's runs of the same arm configs; at most 1 hold of at most 40 min for the rest. Paired bootstrap 95% CI over items (10,000 resamples, seed 1). SUPPORTED: CI lower bound of OVQ - R >= -0.05 and CI upper bound of OVB - OVQ < 0. REFUTED: CI upper bound of OVQ - R < -0.05, or CI lower bound of OVB - OVQ > 0. Otherwise INCONCLUSIVE. | PENDING |
| H54b | On Arc Pro B70 with Qwen3.8-27B single-stream: the best optimized arm on ONE card (k3d-gpu auto OVMS int4, Intel's llm-scaler vLLM, or llama.cpp SYCL, with and without each one's documented MTP) decodes at N=1 at least 2x faster than EAT #21's XPU path B2q on TWO cards (vLLM XPU v0.28.0, the `xpu-qwen36-27b` profile's flags on the Qwen3.8-27B weights, TP 2). | Written 2026-10-20, before any measurement. N=1 median decode tok/s: X1q (OVMS GPU int4) in [15, 28] (0.5), X1q-mtp/X1q in [1.3, 2.2] (0.4); Sq (llm-scaler 1.5, online FP8, TP 1) deliverable (0.5), in [10, 18]; Sq-mtp/Sq in [1.4, 2.3] (0.5); Lsq (llama.cpp SYCL, Intel Q4_K_M) in [12, 24] (0.5); B2q deliverable at TP 2 (0.4), in [5, 12] (0.5). Chosen/B2q >= 2 (0.6). Cards free for a window (0.5). INCONCLUSIVE (0.6), SUPPORTED (0.3), REFUTED (0.1). | H54a's protocol on the B70s, cards only when `~/k3dgpu-bench/gpu-state.txt` lists them free; at most 2 holds of at most 40 min. Same statistic and quality gate (against B2q, or the official-weights arm Sq when B2q is not deliverable, for the gate only). SUPPORTED: CI lower bound of chosen one-card arm / B2q >= 2.0 and the gate passed. REFUTED: CI upper bound < 2.0. Otherwise INCONCLUSIVE (also: B2q not deliverable, or no free card before the campaign ends). Gemma 4 QAT on one B70 (X1g, X1o, B2g, Sgg) is described if hold time remains, never gated. | PENDING |
| H54c | Hybrid placement ("use the fast DRAM"): llama.cpp with Gemma 4 26B-A4B QAT Q4_0, routed experts on the Xeon and everything else on one B70 (Hg, `-ngl 99 -ot exps=CPU`), decodes at N=1 within 25% of a pre-stated byte-split model built from the same image's full-CPU (Cg) and full-GPU (Gg) runs. Qwen3.8-Flash-Next falls back to Gemma 4 QAT (reasons in the pre-registration). | Written 2026-10-20, before any measurement. Model: t_Hg = t_Cg x (B_exp / B_tot) + t_Gg x (B_rest / B_tot) + 60 x 30 us, bytes from the GGUF tensor table. Within 25% (0.45). Hg < Gg (0.8). Hg / Cg in [0.8, 1.6] (0.5). Gg in [30, 90] tok/s (0.4). SUPPORTED (0.45), INCONCLUSIVE (0.3), REFUTED (0.25). | One hold of at most 40 min on one free card: Cg, Gg, Hg in that order, then Gg again (drift check), all in `ghcr.io/ggml-org/llama.cpp:server-intel-b11749`, H54a's N=1 phase only. SUPPORTED: abs(Hg measured / predicted - 1) <= 0.25. REFUTED: > 0.40. Otherwise INCONCLUSIVE (also: any of the three arms not delivered, or the two Gg runs differ by > 10%). | PENDING |
| H54d | Inside EAT's banking flow with Gemma 4 26B-A4B (QAT), the best H54a CPU arm A* delivers far more B24 tasks within budget than EAT stock B1g, under H50's design, statistic, gates and thresholds (rule L2, amendment C1). Runs only when A* is not H50's A1 config. | Written 2026-10-20, before any measurement. A* within-budget success >= 0.60 (0.55). B1g >= 0.50 (0.6). Paired A* - B1g in [-0.10, +0.30] (0.6). A* serves B24's tool calls without a parser change (0.6). Fewer than 16 matched pairs in 3 holds (0.5). REFUTED (0.4), INCONCLUSIVE (0.5), SUPPORTED (0.1). | H50's procedure with arm A = A*, arm B = B1g; at most 3 holds of at most 40 min, until 24 pairs. SUPPORTED: paired difference CI lower bound >= +30 points and A* >= 0.6, L2, void and idle-arm checks passed. REFUTED: CI upper bound < +30 points. Otherwise INCONCLUSIVE. Not run (and so recorded) when A* is H50's A1 config: H50 answers it. Not run 2026-10-21, no hold used ([H54d: not run](#h54d-not-run-2026-10-21)): A* is undefined (H54a chose no arm), B1g stalled in both H54a holds, and H54q has not run; the caps rule makes a row with its stock or chosen arm missing INCONCLUSIVE. | INCONCLUSIVE |
| H58 | The zero-touch thesis on the Xeon 6741P with four Arc Pro B70s. From a clean state, k3d-gpu brings up EAT's banking flow on Gemma 4 26B-A4B-it with no flag, no manual step and no edit. It detects and advertises every engine H52 and H52b found. It places each model by its own memory fit. Its result is within tolerance of the best hand-tuned arm on the same host. Under a controlled host memory squeeze, its guard backs off and nothing outside its own pods is OOM-killed. | Written 2026-10-20, before any H58 run and before the model optimizer (PR #163) and the fit-and-guard layer (branch `claude/fit-and-guard`) are on main. (a) Flags beyond the documented commands: 0 (0.8). (b) Manual steps or edits: 0 (0.6). (c) Decode tok/s >= 0.90x the best hand-tuned arm on this host (0.6), and within-budget B24 success within 5 points of it (0.7). (d) Every engine H52/H52b found is detected and advertised (0.75). (e) Qwen3.8-Flash-Next W4A16 (Intel AutoRound) lands on an experts-in-DRAM placement, and Gemma 4 and Qwen3.8-27B land on the GPU or the CPU, each as its fit decides, with no flag (0.5). (f) Under the squeeze: 0 OOM kills outside our pods (0.85), and the guard lowers admission (0.6). All six together: SUPPORTED (0.25), REFUTED (0.6), INCONCLUSIVE (0.15). | See [H58 pre-registration](#h58-pre-registration-the-zero-touch-thesis-on-crescent-2026-10-20). Runs last on crescent, after #163 and the fit-and-guard layer merge, with their commits recorded. SUPPORTED: (a) through (f) all hold. REFUTED: any clause fails on a valid run. A manual step found is a REFUTED run. It is fixed test-first, and the re-run is a new run with its own section. INCONCLUSIVE: no valid baseline for (c), or the run is not deliverable for reasons outside k3d-gpu. | PENDING |
| H57da | On an AMX-less client CPU (i9-13900T, the 16 P-core CPUs, AVX2 and VNNI, no AVX-512), with the Gemma 4 26B-A4B QAT family, k3d-gpu's own CPU pick Od (`agent manifest` auto on the P-cores: OVMS 2026.4.1, `OpenVINO/gemma-4-26b-a4b-it-int4-ov`) is the fastest single-stream arm: no other arm passing the quality gate (llama.cpp with Google's QAT Q4_0 GGUF, with or without the QAT MTP drafter) decodes at N=1 5% or more faster. A microbench, not an agent claim. | Written 2026-10-20, before any H57d measurement. N=1 median decode tok/s: Od in [8, 20] (0.5); Ld (llama.cpp Q4_0, 8 threads) in [12, 30] (0.5); Ld/Od in [1.1, 1.8] (0.5); every arm's GSM8K 0-49 score >= 0.80 (0.6). REFUTED (0.55), SUPPORTED (0.25), INCONCLUSIVE (0.2). Not gated: TTFT on a 1,000-token prompt, aggregate tok/s at N=4, package J/token gross and net of idle. | [H57d pre-registration](#h57d-pre-registration-gemma-4-26b-a4b-qat-on-the-amx-less-client-cpu-i9-13900t-2026-10-20): H54's measurement protocol per arm (`bench/llm_load.py`, 256 tokens, greedy, `ignore_eos`; RAPL per phase; GSM8K items 0-49 graded as H54), one arm serving at a time, arm order rotated over 3 performance holds, quality in a 4th, at most 5 holds of at most 45 min. Statistic: R = median N=1 decode of the fastest other gate-passing arm X / Od's, over the pooled requests of the 3 holds, 95% bootstrap CI (H54's). REFUTED: Od fails the quality gate, or CI lower bound >= 1.05 and X faster than Od in each of the 3 holds. SUPPORTED: Od passes the gate and CI upper bound < 1.05. INCONCLUSIVE otherwise, or when Od or every other arm is not delivered, or the load gate fails. Run 2026-10-21 ([H57d run](#h57d-run-i9-13900t-2026-10-21)): Ld-mtp/Od 0.785 (95% CI 0.692-1.561), per hold 0.72, 0.79, 1.44; Od 6.90, Ld 3.50, Ld-mtp 5.41 tok/s pooled N=1; all 50/50 on GSM8K; load matched (1.80 <= 2.0). The strict-pinned llama.cpp arms tracked host load 2.3-2.7x, Od 1.3x. | INCONCLUSIVE |
| H57dm | On the same client and model, the CPU Fast Draft rule extends to Gemma 4's MTP drafter: llama.cpp Ld-mtp (Ld plus Google's QAT MTP assistant, converted to Q4_0 GGUF here, `--spec-type draft-mtp`, default draft length) decodes at N=1 at least 1.20x Ld (H34's bar) with the quality gate passed. | Written 2026-10-20, before any H57d measurement. Ld-mtp/Ld in [0.8, 1.3] (0.6): Google's MTP note says the 26B A4B drafter may not pay at batch 1, and llama.cpp's MTP PR saw no MoE gain. REFUTED (0.65), INCONCLUSIVE (0.15), SUPPORTED (0.2). Not gated: the N=4 ratio and the J/token ratio. | As H57da (same holds and records). Statistic: Rm = median N=1 decode Ld-mtp / Ld, pooled, H54's bootstrap CI. SUPPORTED: Rm >= 1.20, CI lower bound > 1.0, Ld-mtp faster in each of the 3 holds, and Ld-mtp passes the gate. REFUTED: CI upper bound < 1.20, or Ld-mtp fails the gate. INCONCLUSIVE otherwise, or when either arm is not delivered or the load gate fails. Run 2026-10-21 ([H57d run](#h57d-run-i9-13900t-2026-10-21)): Ld-mtp/Ld 1.548 (95% CI 1.057-3.017), faster in each hold (1.47, 1.58, 1.52), 50/50 quality. Scope: llama.cpp b11749, default draft length 3, i9-13900T P-cores, single stream, busy shared host. | SUPPORTED |
| H57db | Inside EAT's banking flow (B24) with Gemma 4 26B-A4B QAT on the i9-13900T, k3d-gpu auto on the P-cores delivers far more tasks within budget than EAT stock CPU (vLLM CPU bf16 on the QAT bf16 weights, `xeon-values.yaml` entry `cpu-gemma4-26b-a4b`), interleaved pairs under rule L3. | Written 2026-10-20: not runnable here as registered (memory), so INCONCLUSIVE (not run) is expected (0.9). | [H57d pre-registration](#h57d-pre-registration-gemma-4-26b-a4b-qat-on-the-amx-less-client-cpu-i9-13900t-2026-10-20), H57db: runs only if the memory gate passes at a hold's start (MemAvailable >= 100 GiB: 51.6 GB of bf16 weights resident plus `VLLM_CPU_KVCACHE_SPACE` 40 GiB), with H50's procedure, rule L3 (PR #157) and at most 1 hold, where fewer than 16 complete pairs read INCONCLUSIVE by L3; otherwise INCONCLUSIVE (not run), with the reading recorded. Not run ([H57d run](#h57d-run-i9-13900t-2026-10-21)): memory gate failed, MemAvailable 42.9 GB at the campaign's start (< 100 GiB). | INCONCLUSIVE |
| H52w | A plain `k3d-gpu up` on wildct, a client Raptor Lake host (i7-13700, 8 P-cores + 8 E-cores, AVX2 and AVX-VNNI, no AVX-512, no AMX, one NUMA node), with only the box owner's GPU opt-out (`K3D_GPU_VENDORS=auto,-nvidia,-intel`), detects every CPU engine actually present and advertises each usable one, with nothing missing and nothing phantom; the two NVIDIA cards and the Intel iGPU are named as detected and opted out, and nothing in the cluster mounts or advertises a GPU. | Written 2026-10-20 from an inventory read from sysfs, /proc and PCI sysfs, not from k3d-gpu. Hybrid active, balloons policy running, the P-core test pod inside CPUs 0-15 and the E-core one inside 16-23 (0.8); launcher ISA labels exactly AVXVNNI, VAES, GFNI, SHA, CETIBT, CETSS, and no AVX512 or AMX label from any source (0.85); no NUMA, DSA, IAA, QAT, NPU or Gaudi object (0.9); `up` names nvidia and intel as opted out, no GPU resource, no GPU plugin pod, no pod mounting `/dev/dri` or `/dev/nvidia*` (0.85, given the opt-out fix). SUPPORTED (0.6), REFUTED (0.4). | The table in [the wildct pre-registration](#wildct-pre-registration-client-raptor-lake-i7-13700-2026-10-20): `bench/wildct/h52w.sh` under `bench/wildct/hold.sh` (inventory, `detect` as found, `doctor`, `up`, node object, pod GPU scan, `k3d-gpu test`, `down`, inventory again), the installed release. SUPPORTED: every row matches. REFUTED: any row does not. | PENDING |
| H18w | H18's hypothesis on wildct: `k3d-gpu sandbox up` keeps agent sandboxes on the E-cores and inside a default-deny egress policy on k3d (kube-router), while public egress (PyPI) still works. A replication: H18's runner, image source (the release node image) and parts (a), (b), (c). | Written 2026-10-20, before any H18w measurement. Every run meets (a), (b) and (c) (0.8): sandbox `Cpus_allowed_list` inside 16-23; TCP to `100.66.38.121:22` (tailscale CGNAT) and `192.168.8.238:22` (host LAN) refused from the sandbox with the drop-log counter > 0, connected from the control pod; `pip install --user tomli_w==1.2.0` exits 0. | H18's `bench/sandbox_round.sh`, n = 3 runs in one hold, unchanged except as listed in [the wildct pre-registration](#wildct-pre-registration-client-raptor-lake-i7-13700-2026-10-20): cluster `k3dgpu-w-sbx`, the GPU opt-out, rule W3, the release node image, RAPL through rule W4. Outcome table: H18's. | PENDING |
| H25w | H25's hypothesis (T part) on wildct: agents that send a byte-identical system prompt and tools to one OVMS CPU server reuse one prefix KV, so an added agent's first chunk arrives at least 4x sooner with the shipping prefix caching on than with `--enable_prefix_caching=false`. A replication: original model, prefix shape, probe, scorer and thresholds. | Written 2026-10-20, before any H25w measurement. Added-agent median TTFT OFF 10 s (7-15), ON 1.0 s (0.6-1.6), ratio 10x (6-14), CI lower bound >= 4x; c/OFF >= 0.8. SUPPORTED 0.8. | H25's `bench/shared-prefix/run.sh` and `probe.py score` (A3 first-chunk TTFT, A4 amendment-27 load rule), ON OFF interleaved 3+3 rounds at `typical`, unchanged except as listed in the wildct pre-registration: cluster `k3dgpu-w-h25`, the GPU opt-out, rule W2 (load on E-cores 20-23 only), W3, W4, new seeds (`SEED_BASE=2560`). | PENDING |
| H28w | H28's hypothesis on wildct: N = 8 agent pods that mmap one 1 GiB model through an image volume share one page-cache copy and become ready in at most half the time of pods that copy it into an emptyDir. A replication (H28 has no model; the 1 GiB blob is kept). | Written 2026-10-20, before any H28w measurement. Memory parts hold (0.95): T PSS <= 160 MiB per pod, model page cache about 1.0x size (T) vs about 9x (C). Time part: T/C round-median ready 0.5 (0.4-0.65), against H28's 0.47 on the i9. SUPPORTED 0.45, REFUTED (time part) 0.4, INCONCLUSIVE or not run (disk) 0.15. | H28's `bench/zero-copy-models/probe.sh` (A1, A2), arms C T T C C T plus the unscored warm-up, `typical` only, scored by `bench/zero-copy-models/score.py`, unchanged except as listed in the wildct pre-registration: cluster `k3dgpu-w-h28`, node on the P-cores (`NODE_CPUS=0-15`), W2, W3 (passed to the plain `k3d cluster create`), and a disk gate (W6). | PENDING |
| H34w | H34's hypothesis on wildct: same-engine CPU Fast Draft (OVMS 2026.4.1, `OpenVINO/Qwen3-0.6B-int4-ov` draft on the CPU) beats the `OpenVINO/Qwen3-4B-int4-ov` target alone on single-stream decode, both on the 16-CPU P-core balloon, under matched background load. A replication: original models, runner, scorer and rule. | Written 2026-10-20, before any H34w measurement. At `typical`: median paired B/A decode tok/s 1.35 (1.15-1.6) and median paired B/A package J/token 0.76 (0.6-0.92). SUPPORTED by H34's rule at 0.6, REFUTED 0.25, INCONCLUSIVE 0.15. | H34's `bench/hetero-spec/h34.sh` and `h34.py`, n = 12 pairs A B / B A, 5 s load window per request, `typical` only, unchanged except as listed in the wildct pre-registration: cluster `k3dgpu-w-h34`, the GPU opt-out in place of H34's `intel,hybrid`, W2, W3, W4. | PENDING |
| H57wa | H57da's hypothesis on wildct (i7-13700, the 16 P-core CPUs, AVX2 and AVX-VNNI, no AVX-512, no AMX): with the Gemma 4 26B-A4B QAT family, k3d-gpu's own CPU pick Od (`agent manifest` auto on the P-cores: OVMS 2026.4.1, `OpenVINO/gemma-4-26b-a4b-it-int4-ov`) is the fastest single-stream arm: no other gate-passing arm (llama.cpp with Google's QAT Q4_0 GGUF, with or without the QAT MTP drafter) decodes at N=1 5% or more faster. A cross-host replication of H57da on a second AMX-less client. | Written 2026-10-20, before any H57d or H57w measurement. N=1 median decode tok/s: Od in [7, 18] (0.5); Ld in [10, 26] (0.5); Ld/Od in [1.1, 1.8] (0.5); every arm's GSM8K 0-49 score >= 0.80 (0.6). REFUTED (0.55), SUPPORTED (0.25), INCONCLUSIVE (0.2). Not gated: TTFT on a 1,000-token prompt, aggregate tok/s at N=4, package J/token. | H57da's protocol, runner, statistic and outcome rules (the H57d pre-registration and its runner amendment), unchanged except as listed in the wildct pre-registration: H57d's runner (amendment R1) with the host knobs listed there: clusters `k3dgpu-w-h57d-<hold>`, the GPU opt-out in place of `K3D_GPU_VENDORS=hybrid`, W2-W6, hold-start `load1` <= 12, models downloaded on wildct at H57d's revisions, at most 5 holds of at most 75 min. | PENDING |
| H57wm | H57dm's hypothesis on wildct: llama.cpp Ld-mtp (Ld plus Google's QAT MTP assistant converted to Q4_0 GGUF, `--spec-type draft-mtp`, default draft length) decodes at N=1 at least 1.20x Ld with the quality gate passed. | Written 2026-10-20. Ld-mtp/Ld in [0.8, 1.3] (0.6). REFUTED (0.65), INCONCLUSIVE (0.15), SUPPORTED (0.2). | As H57wa (same holds and records); H57dm's statistic and rule. | PENDING |
| H57wb | H57db's hypothesis on wildct: inside EAT's banking flow (B24) with Gemma 4 26B-A4B QAT, k3d-gpu auto on the P-cores delivers far more tasks within budget than EAT stock CPU (vLLM CPU bf16 on the QAT bf16 weights). | Written 2026-10-20: not runnable here as registered (disk and memory), so INCONCLUSIVE (not run) is expected (0.95). | H57db's design and gates; runs only if at a hold's start / has >= 15 + 70 GB available (51.6 GB of bf16 weights plus images) and MemAvailable >= 100 GiB; otherwise INCONCLUSIVE (not run), with both readings recorded and no bf16 download. | PENDING |
| F2a | Open WebUI in a k3d-gpu cluster reaches a public remote endpoint by name: `k3d-gpu quickstart --remote https://gaudi.farmgpu.net` (no hostAliases) gives a chat through Open WebUI's own API that returns. An earlier run saw `Temporary failure in name resolution` for gaudi.farmgpu.net from a pod. | Pre-registered 2026-10-17, before measuring: the in-cluster failure is a cluster DNS gap (CoreDNS forwarding to an upstream the node container cannot use), not the endpoint; once fixed, one `POST /api/chat/completions` returns `finish_reason=stop` with non-empty content (0.7). | Fresh cluster `k3dgpu-f2-*` (scratch KUBECONFIG, node CPUs 8-15), `nslookup` from a pod before any change, CoreDNS Corefile and node `/etc/resolv.conf` read; root cause fixed in the product if it is a k3d-gpu config gap (regression test first); then one chat through Open WebUI's API with the generated admin account, max_tokens <= 64, temperature 0.6. SUPPORTED: chat returns. REFUTED: no chat returns after the fix. Run (2026-10-17, i9-13900T, k3s v1.35.5, node CPUs 8-15): the prediction's mechanism was wrong. CoreDNS forwarding works (node resolv.conf is k3d's 192.168.32.1 DNAT to Docker's 127.0.0.11; busybox and glibc lookups from pods resolve). The failure is a startup race: `k3d-gpu up` returned on node Ready, when the CoreDNS Deployment was 1 s old (image still pulling); a glibc pod started then got `gaierror(-3, 'Temporary failure in name resolution')` for 11 s (4 tries), first success at 13 s. Fix: `up` waits for the CoreDNS rollout; after it, the same pod resolved on its first try (0.0 s). Then `quickstart --remote https://gaudi.farmgpu.net` with no hostAliases: Open WebUI listed `zai-org/GLM-5.3` and one `POST /api/chat/completions` returned finish_reason=stop, content 'OK', 0.41 s; 0 name resolution errors in its log. | SUPPORTED |
| F2b | EAT's LiteLLM gateway serves the remote model registered by `integrations/eat/eat-remote.sh`, with the key only as an env reference. | Pre-registered 2026-10-17, before measuring: one completion through LiteLLM returns (0.7); the key string appears 0 times in the gateway DB dump, `kubectl describe` of the gateway Deployment/pods/Secret, and events (0.9). | `eat-remote.sh --remote https://gaudi.farmgpu.net --model zai-org/GLM-5.3` against EAT's genai-gateway in the same cluster, then one chat through LiteLLM (max_tokens <= 64, temperature 0.6), then `grep -c` of the key over `pg_dump`, `kubectl describe` and `kubectl get events -A`. SUPPORTED: completion returns and 0 key hits. REFUTED: any key hit, or no completion. Run (2026-10-17, same host, EAT release-1.0.3-hotfix, LiteLLM v1.83.14-stable.patch.2): first with eat-remote.sh as shipped, REFUTED: the chat came back 401 from the endpoint. LiteLLM resolves `os.environ/` only for config-file models; a `/model/new` model is loaded from its database with the reference string as the api_key (proxy_server.py `_add_deployment` decrypts only). Fixed eat-remote.sh to write a config-file `model_list` entry instead and delete the stale database entry; rerun: `eat-check.sh` passed (chat, tool call get_weather, sandbox printed 385), and the key (raw and base64) had 0 hits in `pg_dumpall` of the gateway's Postgres (169 KB), `kubectl describe` of the gateway namespace's Deployments, pods, Secrets and ConfigMaps, the Deployment YAML and `kubectl get events -A`. The verdict is for the fixed script. | SUPPORTED |
| H27x | H27's hypothesis on an AMX host (H27's "re-open for an AMX host"): on the Xeon 6741P (Granite Rapids, AMX), the <=60 s on-box probe (`bench/amx-autoquant/probe.sh`) picks the weight precision for agents better than the static int4 default. Same weights (`OpenVINO/Qwen3-4B-{int4,int8,fp16}-ov` at H27's commits), same parts (a) fidelity, (b) value, (c) cost. A replication: the original model is kept. | Written 2026-10-20, before any H27x measurement. (a) holds (0.85). (b) REFUTED (0.65, outcomes O1 + O2): the quality gate is a property of the weights (NLL at `INFERENCE_PRECISION_HINT=f32`), so int4 fails it again and the pick is int8, and decode stays bandwidth-bound, so int8 stays slower than int4 on the turn (outcome O2 again). (c) holds (0.9). Numbers: decode int4 25-45, int8 18-35, fp16 (bf16 on AMX) 10-22 tok/s; TTFT on 396 tokens 0.15-1.0 s for every arm; turn int4 2-4.5 s, int8 2.5-6 s, fp16 4-9 s. Priors: O1 0.05, O2 0.6, O3 0.05, O4 (int8 beats int4) 0.15, O5 0.15. | H27's row, outcome table and amendments A1-A3, unchanged except as listed in [the cross-host pre-registration](#cross-host-pre-registration-h25x-h27x-h28x-h34x-on-the-xeon-6741p-2026-10-20): OVMS containers on CPUs 0-23,48-71 (the CPUs outside `load.py`'s load set), `typical` only, weights downloaded at H27's pinned commits. Scorer `bench/amx-autoquant/summarize.py`. Run 2026-10-20 ([H27x run](#h27x-run-xeon-6741p-2026-10-20)): load MATCHED, int4 failed the gate again (dNLL +0.072), the probe and the reference both picked fp16 (bf16 on AMX; 5/5 rounds over int8, 3/3 repeats, 4 s per probe), and fp16's 4.23 s median turn is 1.24 s slower than the int4 default's 2.99 s (int4 faster in 5/5 rounds): (a) and (c) hold, (b) fails. | REFUTED |
| H34x | H34's hypothesis on the Xeon 6741P: same-engine CPU Fast Draft (OVMS 2026.4.1, `OpenVINO/Qwen3-0.6B-int4-ov` draft on the CPU) beats the `OpenVINO/Qwen3-4B-int4-ov` target alone on single-stream decode, under matched background load. A replication: the original models are kept. | Written 2026-10-20, before any H34x measurement. At `typical`: median paired B/A decode tok/s 1.40 (1.15-1.70) and median paired B/A package J/token 0.75 (0.60-0.95); SUPPORTED by H34's rule (tok/s >= 1.20 with its CI above 1.0, J/token <= 0.85 with its CI below 1.0) at 0.6, REFUTED 0.25 (the draft's share of a faster target step grows), INCONCLUSIVE 0.15. | H34's `bench/hetero-spec/h34.sh` and `h34.py`, n = 12 pairs A B / B A, 5 s load window per request, unchanged except as listed in the cross-host pre-registration: cluster `k3dgpu-x-h34`, `K3D_GPU_VENDORS=none`, node container on CPUs 0-23,48-71, crescent amendment C1, `typical` only. Run 2026-10-20 ([H34x run](#h34x-run-xeon-6741p-2026-10-20)): load MATCHED, 12/12 pairs; B/A decode tok/s 0.758 (CI 0.688-0.809) and package J/token 1.241 (CI 1.168-1.347), B slower and costlier in 12/12 pairs (A 37.2, B 28.0 tok/s): the draft slows decode on this Xeon. | REFUTED |
| H25x | H25's hypothesis (T part) on the Xeon 6741P: agents that send a byte-identical system prompt and tools to one OVMS CPU server reuse one prefix KV, so an added agent's first chunk arrives at least 4x sooner with the shipping prefix caching on than with `--enable_prefix_caching=false`. A replication: the original model and prefix shape are kept. | Written 2026-10-20, before any H25x measurement. Added-agent median TTFT OFF 1.2 s (0.5-2.5), ON 0.15 s (0.06-0.4), ratio 8x (4.5-14), the ratio's 95% CI lower bound >= 4x; c/OFF >= 0.8. SUPPORTED 0.75; the risk is fixed per-request overhead, which a 5-10x faster prefill leaves as a larger share of the ON time. M (memory per agent) not measured, as in H25 run 4. | H25's `bench/shared-prefix/run.sh` and `probe.py score` (A3 first-chunk TTFT, A4 amendment-27 load rule), ON OFF interleaved 3+3 rounds, unchanged except as listed in the cross-host pre-registration: cluster `k3dgpu-x-h25`, `K3D_GPU_VENDORS=none`, node container on CPUs 0-23,48-71 (`NODE_CPUS`), crescent amendment C1, `typical` only. Run 2026-10-21 ([H25x run](#h25x-run-xeon-6741p-2026-10-21)): load MATCHED (amendment 27), no round void; added agent's first chunk 0.245 s ON vs 1.724 s OFF (7.05x, CI 6.94-7.13, n = 15 per arm), c/OFF 0.99, every round pair >= 4x: T SUPPORTED; M not measured (A3). | SUPPORTED |
| H28x | H28's hypothesis on the Xeon 6741P: N = 8 agent pods that mmap one 1 GiB model through an image volume share one page-cache copy and become ready in at most half the time of pods that copy it into an emptyDir. A replication (H28 has no model; the 1 GiB blob is kept). | Written 2026-10-20, before any H28x measurement. Memory parts hold (0.95): T PSS <= 160 MiB per pod, model page cache about 1.0x size (T) vs about 9x (C). Time part uncertain: the copy runs at memory speed on this host, so the fixed pod-start cost is a larger share of both arms; T/C round-median ready 0.55 (0.35-0.8). SUPPORTED 0.35, REFUTED (time part) 0.5, other rows 0.15. | H28's `bench/zero-copy-models/probe.sh` (A1, A2), arms C T T C C T plus the unscored warm-up, unchanged except as listed in the cross-host pre-registration: cluster `k3dgpu-x-h28`, crescent amendment C1 passed to the plain `k3d cluster create`, `typical` only, scored by `bench/zero-copy-models/score.py` (H28's table, committed with this pre-registration). | PENDING |
| H55 | On the i9-13900T, an SLO gate on the E-core neighbour's `cpu.max`, set from the P-core inference server's own median TTFT, keeps CPU-served prefill within +10% of quiet while the neighbour keeps more throughput than H5's frequency-cap frontier (CMIN: +23.3% at 72.4% neighbour loss) and than the best static quota at the same penalty. | Written 2026-10-20, before any H55 measurement; control rule revised the same day, still before any measurement (see the section). Pre-registered prior, not a result: N0 about +40% (H5 replicate), K50 +20 to +30% at about 50% loss, K25 +8 to +15% at about 75% loss. A_slo climbs only while the penalty at its level is at most +5%, so it is expected to stay at the 25-37.5% levels, close to K25: +8 to +15% at 70-78% loss, failing (b) or (c). Expected verdict REFUTED: package power is the coupling, duty-cycling the neighbour is less power-efficient per op than H5's frequency cap, and a closed-loop c = 1 prefill load leaves no idle gap to admit it into. If H55 holds, A_slo settles at 50% or above at +10% or less. | H5 amendment A1's host-level harness (OVMS 2026.4.1, `OpenVINO/Qwen3-4B-int4-ov`, `bench/prefill-guard/prompt.txt` with a unique tag per request, CPUs 0-14; `stress-ng --cpu 15 --cpu-method matrixprod` on CPUs 17-31; tool proxy on CPU 16), with a 900 s closed-loop prefill window per run (16 tokens, concurrency 1). Arms Q, N0 (`cpu.max max`), K50 (`750000 100000`), K25 (`375000 100000`) and A_slo (`bench/admission/ctl.py` steps 25-100% of 15 CPUs on windows of 20 requests: down if the window median > 1.10 x the round's quiet reference, the median of 20 pre-roll requests; up if it is at most 1.05 x that), 5x5 Latin square, one round per lock hold. Supported only if A_slo's median penalty is at most +10%, its neighbour loss is below 72.4%, and no static arm reaches +10% at a loss no higher than A_slo's plus the neighbour's own N0 run-to-run spread; inconclusive gates and the hold cap in [H55 pre-registration](#h55-pre-registration-slo-gated-admission-of-an-e-core-neighbour-2026-10-20). Not run. A SUPPORTED H55 does not ship a controller by itself: kubelet owns the sandbox pod's `cpu.max` (`limits cpu "1"`). | PENDING |
| H56 | Two measurement holds in disjoint cpuset slices of the Xeon 6741P do not perturb each other: an A1 B24 agent block in slice 0 (CPUs 0-23,48-71) behaves equivalently, in per-attempt time, within-budget success and tasks completed, alone and with a concurrent identical A1 hold in slice 1 (CPUs 24-47,72-95), without an all-core clock drop on slice 0 and with the package below its power limit. It decides whether crescent may run two A1-or-lighter holds at once ([adoption rule](#h56-pre-registration-parallel-slice-holds-on-crescent-2026-10-20)); it is not a k3d-gpu product claim. | Written 2026-10-20, before any run; predictions unchanged by the 2026-10-21 revision, priors updated to its narrower bands. Geometric-mean W/A time ratio 1.04 (0.99-1.09); paired success W - A -0.01 (-0.06 to +0.04); completion W/A 0.97 (0.90-1.00); own-slice MHz W/A 0.97; W package 0.75 PL1 (0.55-0.95), A package 0.5 PL1; time pairs 80 (50-96). Batch-1 int4 decode is DRAM-bound, and the sibling's decode and its stream load share DRAM, L3 and package power. Verdict priors: SUPPORTED 0.30, INCONCLUSIVE 0.45, REFUTED 0.25. | Stage 0 first, read-only: `bench/eatflow/crescent/lockwait.py` over both campaigns' `campaign.log`; if lock wait our slots could remove is under 0.10 of hold time, stage 1 is not run and the row stays PENDING (`h56.sh` refuses through `h56.py gate`). [Amendment S0](#h56-pre-registration-parallel-slice-holds-on-crescent-2026-10-20) (2026-10-21, before any stage-1 hold, stage-0 reading disclosed): the share is pooled lock wait over hold time across every campaign log on crescent, nothing netted out; bar 0.10 unchanged. Then one W wiring smoke, which must show slice-0 setup <= 900 s and >= 3 tasks in 120 s. Stage 1: `bench/eatflow/crescent/h56.sh N` for N = 1..8, order A W W A A W W A, one condition-hold per exclusive campaign lock (slot locks first). A: slice 0 alone, slice 1 idle. W: the sibling in slice 1 first (cap 3000 s, budget 1800 s), slice 0 from the sibling's run budget on (cap 2400 s, budget 720 s, the same as A). Hold pairs (1,2) (4,3) (5,6) (8,7), tasks by id; 90% two-stage bootstrap CIs (hold pairs, then tasks; 10,000, seed 56; TOST at alpha 0.05). SUPPORTED: all 8 positions filled, time CI inside [1/1.05, 1.05], success CI inside [-1/24, +1/24], completion W/A >= 1/1.05, >= 60 time pairs, own-slice MHz W/A >= 0.95, W package < 0.95 PL1 and PL1 read. REFUTED: the time CI wholly outside its band, the success CI wholly outside its band, or the completion CI wholly below 1/1.05. Otherwise INCONCLUSIVE. VOID holds are replaced, at most 2. `bench/eatflow/crescent/h56.py score`. | PENDING |

## Falsification log

Every probe run, kept or not. When an attack on a hypothesis is settled, mark it
DO-NOT-RE-ATTACK with the reason, so the same experiment is not repeated without
new evidence (new hardware, new driver, new model, changed method).

| Date | Hypothesis | Attack | Result | DO-NOT-RE-ATTACK |
|------|------------|--------|--------|------------------|
| 2026-10-21 | H25x | Xeon 6741P: `bench/shared-prefix/run.sh`, OVMS agent pod on node CPUs 0-23,48-71 under matched `typical` load on 24-47,72-95, 3+3 rounds ([H25x run](#h25x-run-xeon-6741p-2026-10-21)) | SUPPORTED (T), load matched: 7.05x (CI 6.94-7.13), smaller than the i9's 10.61x because the Xeon's OFF prefill is faster | DO-NOT-RE-ATTACK T for this prompt shape on this host |
| 2026-10-20 | H34x | Xeon 6741P: `bench/hetero-spec/h34.sh`, OVMS pod on node CPUs 0-23,48-71 under matched `typical` load on 24-47,72-95, 12 pairs A B / B A ([H34x run](#h34x-run-xeon-6741p-2026-10-20)) | REFUTED, load matched: B/A tok/s 0.758 (CI 0.688-0.809), J/token 1.241 (1.168-1.347), 12/12 pairs B worse; target alone 37.2 tok/s, with the draft 28.0 | DO-NOT-RE-ATTACK same-engine CPU Fast Draft (0.6B draft, gamma 5, OVMS 2026.4.1) for this target on a Xeon with this many cores. Re-open with another gamma, draft or OVMS release |
| 2026-10-20 | H27x | Xeon 6741P (AMX): `bench/amx-autoquant/probe.sh` on CPUs 0-23,48-71 under matched `typical` load on 24-47,72-95, H27's weights, 5 rounds + 3 probe repeats ([H27x run](#h27x-run-xeon-6741p-2026-10-20)) | REFUTED, load matched. Pick fp16 (not H27's int8): bf16 on AMX decodes 24.7 tok/s vs int8's 19.4 and has the shortest TTFT; int4 (gate-rejected, dNLL +0.072) is fastest at 2.99 s per turn vs fp16 4.23 s, int8 5.50 s | DO-NOT-RE-ATTACK (b) with these three weights on an AMX Xeon either: the pick still costs latency. Re-open only with other weights or a revised gate |
| 2026-10-18 | H40 | i9-13900T: A3ref (bf16 Qwen3-4B-Instruct-2507 on vLLM CUDA, RTX 4000) in a fresh `k3dgpu-a-a3ref`, BFCL v4 S100 in board mode through LiteLLM and the recording proxy, load none, 100 ids ([H40-H48 run](#h40-h48-run-i9-13900t-2026-10-17-and-18)) | SUPPORTED: 88.0% (board-weighted 84.9, band 77.9-91.9), all 7 categories inside their binomial 95% interval of the board value, 100 of 100 requests HTTP 200 | Yes for `bfcl-eval==2025.12.17` and this model; re-attack on a new BFCL release or model |
| 2026-10-18 | H41 | i9-13900T: server-fc S100 at typical load, one hold per arm (amendment 23) against A3ref's server-fc block (88.0%) | INCONCLUSIVE: background load unmatched for every arm (swap used 3.6-4.4 GiB apart, throttle and package W). Before load: A3 87.0% (-1.0, LB -4.0, n=100) and C2 86.6% (-1.2, LB -4.9, n=82) would hold; A1 81.0% (-7.0, LB -11.0) and C1 80.0% (-11.7, LB -20.0, n=60) straddle -8; B1 8 ids, 22 of 24 requests over the 120 s turn timeout | No: re-run with the arms interleaved inside one hold so load can match |
| 2026-10-17 | H42 | i9-13900T: B24 at N=1, typical load, one block per arm (amendment 20), A1 A2 A3 B1 C1 C2 | INCONCLUSIVE: load unmatched across arms. A1 20/24, A3 18/24 (15/24 before amendment 21a), C2 19/20, C1 21/21 (prediction C1 <= A1 - 10 failed), A2 11/14; A3/A1 time 0.12, A2/A1 2.64; A1/B1 0.048 on the 1 task B1 completed | No: re-attack needs matched load (arms interleaved in one hold) and B1 with more than 1 completed task |
| 2026-10-18 | H44 | i9-13900T: A1 only (amendment 24), B24's first 12 tasks, blocks none/heavy/none/heavy of 186 s in one hold | INCONCLUSIVE: A1 p95 inflation heavy/none 2.59 (CI 0.87-2.94), neither <= 1.25 nor a lower bound > 1.25; B1 not run (turns over the 120 s timeout) | No: needs longer heavy blocks (they finished 7 of 12 tasks) and a B1 that answers inside the turn timeout |
| 2026-10-17 | H46 | i9-13900T: H42's blocks plus a 120 s load-only control window per arm | INCONCLUSIVE: B1's J per success undefined (1 success, control window cut by the hold timeout) and load unmatched. A1 76 J, A3 73 J (dGPU included), A2 191 J per success | No: re-run with H42 |
| 2026-10-18 | H48 | Remote GLM-5.3 through harvest, one capped proxy (20 requests, `agent_remote`): run 2 (2026-10-17 23:07, the first valid run) and a confirmation run 3 on the fixed graders (amendment 22) | REFUTED as pre-registered: run 2 graded 7 of 12 (B24 0/4, BFCL 7/8). The audit found all 4 B24 failures were a grader-input defect (DevUI joins split answer text with spaces), fixed in `1fbbe39`; regraded run 2 and run 3 both pass 11 of 12, with the one failure the model's (BFCL `irrelevance_203`) | Yes for the graders at `1fbbe39`; re-attack when a grader changes |
| 2026-10-17 | H9 | i9-13900T, 125 GiB RAM: cold `quickstart` n=1, then a rerun and `uninstall --yes` ([H9 run](#h9-run-i9-13900t-2026-10-17)) | Cold arm held: 303 s to Ready (target 900 s), RAM tier picked Qwen3-8B int4 (4882 MB) on 16 P-cores, chat replied "Paris." through Open WebUI; sign-up refused (403). Rerun 2 s with every step skipped; uninstall 6 s. Warm arm not run, n=1, host load average ~38 from other jobs | No: run the warm arm and n>=3 per arm, then on a 16 GB and an 8 GB host |
| 2026-10-16 | H6 | i9-13900T: OVMS 2026.4.1 + Qwen3-4B int4, P-core CPU arm vs UHD 770 arm, 5 rounds (header and numbers in [H6 run](#h6-run-i9-13900t-2026-10-16)) | Held. Decode 15.7 vs 9.9 tok/s (+58%); ~1936-token prompt time to first token 11.2 s vs 29.5 s (2.6x), i.e. prefill 172 vs 66 tok/s. The GPU pod used 1.0 CPU-s/s, the CPU pod 7.9 | DO-NOT-RE-ATTACK on UHD 770 / Raptor Lake with this model and server: re-run on new hardware, driver, model or OVMS release |
| 2026-10-16 | H4 | Same host and data as H6, against the `agent` auto rule of that time (any allocatable Intel GPU, else CPU) | That rule picked the UHD 770, which lost to the P-cores on decode and prefill. Not a verdict for H4, which needs every usable device swept with the H2 method; it changed the rule: `auto` now takes the GPU only on a `gpu.intel.com/xmx=true` node | ~~No: H4 is re-run against the new rule~~ (struck 2026-10-18: no re-run against the new rule took place. H4's verdict comes from the dedicated sweep (UHD 770 and three forced CPU arms) against this same 8385dc2 rule, run 2026-10-16T22:27Z..2026-10-17T01:12Z and logged as the 2026-10-16 H4 REFUTED entry, [H4 run](#h4-run-i9-13900t-2026-10-16). This entry stays not a verdict. The XMX-gated rule is untested) |
| 2026-10-17 | H11 | i9-13900T: `up` with the pinned default vs the same image ID through a floating ref, 3 runs per arm alternating; the added inspects 5 times; a cluster made by main's launcher, then the branch launcher (header and numbers in [H11 run](#h11-run-i9-13900t-2026-10-17)) | Held. Median `up` 36.57 s pinned vs 37.20 s floating (Δ 0.63 s, arm spreads 1.15 s and 2.83 s); added checks median 0.026 s. On main's cluster `doctor` warned "node image cryptoandcoffee/k3d-gpu:latest (unversioned ...)" and `up` stopped with rc 1 before `k3d cluster create`; after recreate the node ran `cryptoandcoffee/k3d-gpu:0.1.21`, label 0.1.21, `doctor` ok | DO-NOT-RE-ATTACK unless `ensure_image`/`node_image` gain more docker calls or k3d changes how it resolves a cached image |
| 2026-10-17 | H12 | i9-13900T: control c1 on an `origin/main` image, then run a1 (default cluster, host Helm v4.1.1, no extra flag) | Control failed as predicted ("cannot be imported ... missing key meta.helm.sh/release-name"). a1 failed: helm rc 1, "conflict occurred while applying object ... conflicts with \"deploy@k3d-k3dgpu-r2-beat-server-0\"" on DaemonSet `.spec.template.spec.nodeSelector` and BalloonsPolicy `.spec.balloonTypes`: Helm 4 installs a new release with server-side apply, and k3s's deploy controller already manages those fields. REFUTED; runs b were not started | DO-NOT-RE-ATTACK with Helm 4 defaults on a default cluster: the conflict is deterministic |
| 2026-10-17 | H13 | Runs A3-1 (Helm v3.14.0) and A4-2 (Helm v4.1.1 `--server-side=false`) on default clusters; batch stopped after A4-2 | Both: helm rc 0, revision 1 `deployed`, DaemonSet relabelled to the release and its nodeSelector changed to `k3d-gpu.io/hybrid-cpu`, `k3d-gpu test` passed (P pod on `0`, E pod on `24`), but the BalloonsPolicy kept the baked types (`replica ... shared`, no `vllm-balloon`), so the `vllm` and `redis` probes both ran on the shared balloon `0-15,17-31`. Helm's client-side update of an adopted object diffs the rendered object against itself, so a custom resource gets an empty merge patch; native kinds get a three-way patch against the live object. REFUTED for A3 and A4 | DO-NOT-RE-ATTACK: adoption without a force flag never changes the BalloonsPolicy spec |
| 2026-10-17 | spike for H14 | One default cluster each: Helm v4.1.1 `--server-side=false --force-replace` on the A4-2 cluster; Helm v3.14.0 `--force`; Helm v4.1.1 `--force-conflicts` | All three: BalloonsPolicy types became `reserved vllm-balloon performance efficient lowpower ecluster pcore-physical latency agent-control-plane shared`. Exploratory, n=1 each; not a verdict | No: H14 measures the force paths with n=3 |
| 2026-10-17 | H14 | i9-13900T: 12 runs, 3 per arm (A3f, A4f, B4, B3) in the pre-registered order (header and per-run table in [H14 run](#h14-run-i9-13900t-2026-10-17)) | Held in 12/12. Every run: helm rc 0, revision 1 `deployed`, `vllm-balloon` and `agent-control-plane` present, DaemonSet 1/1, `k3d-gpu test` rc 0, `vllm` container on `10-11` (P-cores 0-15), `redis` pod on `24-25` (E-cores 16-31). External runs: no `nri-balloons.yaml` in the manifests dir, no CRD, no DaemonSet before Helm. Default runs: restart without `.skip` put the baked types back (6/6), restart with `.skip` kept `vllm-balloon` (6/6) | DO-NOT-RE-ATTACK unless nri-plugins, Helm or k3s's deploy controller changes |
| 2026-10-17 | EAT flow | One default cluster, Helm v3.14.0: `helm upgrade --install` with chart defaults, then again with `contrib/eat/hybrid-values.yaml` (EAT's install_nri.yaml then main.yaml order) | Revision 1 left the baked policy as is. Revision 2 replaced the balloon types with the values' types but kept the baked `agent.podResourceAPI: true` (the values say false): Helm patched only what differs between its two revisions. Exploratory, n=1; backs the docs/intel.md wording, no verdict | No |
| 2026-10-17 | H21 | i9-13900T: `bench/joule-pack/probe.sh` at bdd1a02, 3 rounds x 5 arms, interleaved (header and table in [H21 run](#h21-run-i9-13900t-2026-10-17)) | INCONCLUSIVE on (a) and (b). Idle package floor 90.7 W median, spread 26.5 W over 16 idle windows, against arm marginals of -3.3..10.8 W: the shared host (53 containers, other experiments' load) swamps the signal; throttle counter moved in 11/15 arms. Per-round best/E 1.75x, 104.9x, 2.18x (agents/W) are noise, not a result | Not DO-NOT-RE-ATTACK: re-run only on a quiet host (no other containers, idle spread < 2 W); add an N sweep before claiming anything about the SLO |
| 2026-10-18 | H20 | i9-13900T: run 1 (2026-10-17, A1, a171ded) measured 0 turns in 4 of 10 cells; run 2 (2026-10-18, Amendment A2 at 2a1ba08): every arm under the same `bench/load/load.py` load on the E-cores, `typical` then `heavy`, 10 of 10 cells per level, prefix restored from saved slots, whole-turn rounds, 48 scored turns per level, all cache hits ([H20 run 2](#h20-run-2-amendment-a2-matched-background-load-i9-13900t-2026-10-18)); run 3 (2026-10-18, A3 at 4101f97): amendment 27 settle gap, pre and during windows, `typical` only, 10 of 10 cells ([H20 run 3](#h20-run-3-amendment-a3-amendment-27-load-rule-i9-13900t-2026-10-18)) | INCONCLUSIVE at both levels: load unmatched on core throttle events/min (typical 992 > 642, heavy 952 > 263); typical SP placement gate failed 3/3 (E busy +0.06-0.07); heavy SP J/token spread 114%. Gated out, but the direction is REFUTED: typical PS/SP net J/token 0.868 (MW p 0.10), PS TPOT 1.88x SP (guardrail 1.5x), tok/s 0.64x (guardrail 0.9x, 3/3 cells), raw package J/token 1.49x SP; ALL raw J/token below PS. Run 3 (A3, amendment 27, `typical`): INCONCLUSIVE, load unmatched on pre swap used (870.9 > 256 MiB, one SP window), pre throttle matched (88 < 212), no void; SP spread 32% and SP placement 2/3 fail too. Direction REFUTED: net J/token 1.03x SP (CI 0.52-1.20), raw 1.51x, TPOT 1.86x, tok/s 0.64x | Not DO-NOT-RE-ATTACK: low value (P(SUPPORTED) about 0.03 after run 3). Re-run only with the SP placement margin (unpinned helper threads) and raw package J/token re-registered first |
| 2026-10-17 | H22 | i9-13900T: `bench/hetero-spec/probe.sh`, runs 2 (06:29) and 3 (08:40, quiet gate passed), 8 rounds x 5 arms each (headers in [H22 result](#h22-result-i9-13900t-2026-10-17) and [H22 re-run](#h22-re-run-i9-13900t-2026-10-17)) | INCONCLUSIVE twice on the noise row: A 11.3 and 12.7 tok/s vs the 15.7 calibration, every round throttle-flagged; in run 3 the probe's own 16-CPU decode heated the package (35 W rated base power, run at PL1 = PL2 = 117 W) to 86-97 C. Directionally C < B in 16/16 rounds (C/B 0.78, 0.73), C/A 1.07, 0.91 | Not DO-NOT-RE-ATTACK, but do not re-run on this host: the throttle is self-induced. Re-run only on a package that holds the target without throttling, or after an amendment that recalibrates A under load |
| 2026-10-18 | H24 | i9-13900T: run 1 (2026-10-17, `a19f52c`, uncontrolled load, 6 of 8 visits, [H24 run](#h24-run-i9-13900t-2026-10-17)); run 2 (2026-10-18, Amendment A1 at `c914a85`, `bench/sku-model/run-a1.sh`): every arm under the same `bench/load/load.py` load on the E-cores, `typical` 8 of 8 visits, then `heavy` 5 of 8 (deadline), one lock hold, no cluster ([H24 run 2](#h24-run-2-amendment-a1-matched-background-load-i9-13900t-2026-10-18)) | Run 1 INCONCLUSIVE (partial run, TTFT never captured). Run 2 INCONCLUSIVE at both levels: load unmatched on load1 (typical 8.19 > 2.82, heavy 5.55 > 4.88) and core throttle/min (665 > 418, 1701 > 440), both carried in from the previous visit; busy, package W, swap matched. Gated out, typical is row 3: decode vs M0 B -15.6%, C -47.0%, D -55.8%, M1 C -21.9%, D -40.3%; prefill B +17%, C +36%, D -43%; c4 D -67% | Not DO-NOT-RE-ATTACK (not REFUTED under the gate). Re-run with a per-visit settle until load1 returns to the level baseline, and a heavy deadline that fits 8 visits; direction says M0 decode fails (confidence 0.9) |
| 2026-10-18 | H23 | i9-13900T: run 1 (2026-10-17, `cbc3a08`, uncontrolled load, [H23 run](#h23-run-i9-13900t-2026-10-17)); run 2 (2026-10-18, Amendment A2 at `bd4e800`, `bench/thermal-resource/run.sh`): `bench/load/load.py` typical then heavy held on the E-cores through calibration and the gate, one lock hold of 383 s, no cluster created (header in [H23 run 2](#h23-run-2-matched-background-load-i9-13900t-2026-10-18)) | Run 1 INCONCLUSIVE (O0): budget 10 W, 2 slots, idle window 72-99 W uncontrolled. Run 2 INCONCLUSIVE (O0) at both levels: typical loaded idle 96.8 W, T_idle 85.0 C, P_th 94.0 W, budget 0 W; heavy 101.2 W, 87.0 C, P_th 90.5 W, budget 0 W; 0 of 9 arm runs per level, so load matching not reached. Bootstrap budget 95% -7.1 to -0.1 W (typical), -14.6 to -7.9 W (heavy), never >= 16 W | DO-NOT-RE-ATTACK on this host (pre-registered O0 action: loaded idle 85-87 C is within 10 C of T_tcc 95 C). A busy box at the thermal margin leaves a 10 C-margin headroom model nothing to admit. Re-run only on a host whose loaded idle sits >= 20 C below T_tcc, checked read-only first |
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
| 2026-10-18 | H29 | i9-13900T: run 1 (2026-10-17, uncontrolled load) 32 cells, arms A/U/C/UC n=7, L n=3 ([H29 run](#h29-run-i9-13900t-2026-10-17)); run 2 (2026-10-18, amendments A2 + A3 at 618b012): every arm under the same `bench/load/load.py` load on the E-cores, Williams-square interleaved, typical 8 cells (99 C abort) then heavy 28 cells, n = 6-7 per primary arm ([H29 run 2](#h29-run-2-matched-background-load-i9-13900t-2026-10-18)) | Not decided. Run 1: A decode spread 23.2% > 10%; U ring 2900 MHz vs the 4700 MHz pin. Run 2: load unmatched across arms at both levels (throttle/min; plus load1 and swap growth at typical), heavy stock spread 12.9% > 10%, U ring 2900-2950 MHz vs 4700 at both levels. Gated-out direction REFUTED: heavy UC/A decode 0.956x, gross J/token 1.084x, higher in 6/6 rounds; C/A 0.930x and 1.117x | Not settled, but do not re-run unchanged on this host: the uncore pin is not honoured while the E-cores carry load (0 of 16 U cells at target across all runs). A re-run needs a host whose ring holds the pin under load, checked before booking the lock |
| 2026-10-17 | H27 | i9-13900T: `bench/amx-autoquant/probe.sh`, OVMS 2026.4.1 per precision on P-cores 0-15, Qwen3-4B int4/int8/fp16 + int4' control, 5 ABCD/DCBA rounds + 3 probe repeats under matched `bench/load/load.py` `typical` load on the E-cores (`heavy` as robustness). Four preflight aborts and one externally killed attempt are recorded ([H27 run](#h27-run-matched-background-load-i9-13900t-2026-10-17)) | REFUTED at `typical`, with load matched. int4 failed the quality gate (dNLL +0.070 > 0.05). The probe picked int8 in 3/3 repeats, the same pick as the reference (5/5 rounds), within 20-21 s. But int8's median turn of 16.59 s is 5.16 s slower than the int4 default (11.43 s; int4 won 5/5 paired rounds). `heavy`: INCONCLUSIVE (budget, throttle unmatched), same direction | DO-NOT-RE-ATTACK (b) on non-AMX hybrid clients with these three weights: the pick can only cost latency there. Re-open for an AMX host, other weights, or a revised quality gate pre-registered first |
| 2026-10-18 | H28 | i9-13900T: `bench/zero-copy-models/probe.sh` (A2), k3s v1.37.1+k3s1 overlayfs, 8 pods x 1 GiB unique model per round, arms C T T C C T plus an unscored warm-up, under `bench/load/load.py` `typical` on the E-cores with the node pinned to P-cores 0-15; `heavy` 5 rounds (T r12 cut by the time guard) ([H28 run A2](#h28-run-a2-amendment-27-load-rule-i9-13900t-2026-10-18)) | SUPPORTED at `typical`: load matched (amendment 27), no void; T PSS 132.5 MiB per pod vs C 1028.5 MiB, model page cache 1.00x vs 9.00x size, ready 2980 vs 6333 ms (ratio 0.47, CI 0.45-0.55, complete separation), confidence 0.79. A1's lopsided-charge prediction did not hold (T memcg file charge 111-160 MiB per pod) | `model-share` docs may cite [ledger:H28] for PSS and ready-time ratios (one host, `typical` load); no default change |
| 2026-10-18 | H26 | i9-13900T: `bench/rdt-isolate/run.sh`, OVMS agent on P-cores 0-14, stream hog arms Q ME15 ME4 MC4 in 12 blocks (n = 30 per arm). Run 2026-10-17 (A2): matched-load windows with no decay gap, `typical` and `heavy` ([H26 run](#h26-run-matched-background-load-i9-13900t-2026-10-17)). Run 2026-10-18 (A3): amendment 27 settle gap, pre and during windows, `typical` only ([H26 run A3](#h26-run-a3-amendment-27-load-rule-i9-13900t-2026-10-18)) | INCONCLUSIVE. A2: load unmatched (hog carry-over at `typical`, background removed mid-level at `heavy`); R 0.83 (CI 0.61-0.87). A3: pre-windows matched, load unmatched on during busy_pct (36.96 > 5.00); R = I(MC4)/I(ME4) 0.92 (CI 0.14-2.09), P(R >= 0.8) 0.54; verdict if matched REFUTED in both runs | No claim, no doctor change; next probe needs a during-busy design that the hog cannot move (to be pre-registered) |
| 2026-10-18 | H25 | i9-13900T: OVMS CPU, shipping prefix caching ON vs `--enable_prefix_caching=false`, 6 agents with a 1538-token shared head. Run 1: 5 of 6 rounds under uncontrolled 1-min load 18-29 ([H25 run](#h25-run-i9-13900t-2026-10-17)). Run 3: A2/A3, matched `bench/load/load.py` load on the E-cores, ON OFF interleaved, `typical` 3+3 rounds and `heavy` 3+2 ([H25 run 3](#h25-run-3-matched-background-load-i9-13900t-2026-10-17)). Run 4 (2026-10-18): A4, load scored by amendment 27 (`matched_a27`), `typical` 3+3 rounds, ON OFF interleaved, seeds 2531-2536 ([H25 run 4](#h25-run-4-amendment-27-load-rule-i9-13900t-2026-10-18)) | INCONCLUSIVE. Run 1: TTFT null in all requests (no content delta with tools), all rounds void. Run 3: load unmatched on core throttle events/min at both levels (every other load metric matched), ON spread gate failed at `typical`. Measured added-agent TTFT 1.32 s ON vs 12.44 s OFF (9.44x, CI 6.99-9.90) at `typical`, 2.76 vs 25.56 s (9.27x, CI 8.96-9.73) at `heavy`. Run 4: SUPPORTED at `typical`: load matched on every covariate, no void; added-agent TTFT 1.02 s ON vs 10.87 s OFF (10.61x, CI 10.36-10.74, n = 15 per arm), c/OFF 1.02, a1 ON/OFF 0.99, 9/9 pairs ≥ 4x (confidence 0.91); M not measured | Docs may cite the added-agent TTFT as [ledger:H25] (one host, `typical` load); `heavy` and M not re-measured; no default change |
| 2026-10-16 | H3 (motivates H5) | Wave-1, host level, no cluster, i9-13900T: inference pinned to CPUs 0-14, a 15-worker `stress-ng` neighbour pinned to E-cores 17-31 (disjoint cpusets), n = 4 per arm. | Quiet median prefill 7.03 s. Prefill rose 30% with a register-only neighbour (`--cpu-method loop`), 31% with the mixed one, 40% with `matrixprod`; decode rose 6%. The host was already at PROCHOT (`package_throttle_count` about 22M, package 91-96 C under load). A register-only neighbour doing nearly the cache-heavy damage points at package power/thermal/clock coupling, not cache. Reported by the wave-1 run; ~~its Reproduce header and raw results are not in this repository yet~~ (struck 2026-10-18: its header is now in [H3 host-level observation](#h3-host-level-observation-i9-13900t-2026-10-16), with every field the repository and its history do not hold marked not recorded; the raw results are still not in the repository), so this row settles nothing and backs no claim. It is not the H3 verdict, which rests on [H3 run](#h3-run-i9-13900t-2026-10-16). | No: re-run under H3's k3d-gpu probe with a full header. 2026-10-18: [H3 run](#h3-run-i9-13900t-2026-10-16) is that probe with a full header; its in-cluster mechanism probes (n = 20 per variant) are the comparable data |
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

| 2026-10-17 | H1 | i9-13900T (AVX2 and VNNI, no AVX-512, no AMX): run 2, the pre-registered probe on one fresh cluster `k3dgpu-h1-eat` (k3s v1.37.1, main 992df8d, EAT release-1.0.3-hotfix) under the `typical` background load, each step once ([H1 run 2](#h1-run-2-i9-13900t-2026-10-17)); run 1 (2026-10-16, [H1 run](#h1-run-i9-13900t-2026-10-16)) ran the model step twice and skipped the wait | INCONCLUSIVE by the amendment's Job-pod rule. `up` 48.8 s, EAT stack 516.7 s (5 PLAY RECAPs `failed=0`), model step 157.7 s, then 383.0 s until 33 of 34 pods were Ready, vLLM included. The one pod not Ready was k3s's own `helm-install-gateway-api-crd` Job pod (Succeeded), so `kubectl wait ... --all -A` cannot exit 0 on k3s v1.37. The completion returned HTTP 200, `finish_reason` stop, 20 prompt / 27 completion tokens, vllm-0.24.0; `eat-check.sh` passed. No Xeon-only instruction or label was needed | No: re-run once with a pre-registered wait that skips Succeeded pods and requires every Job `Complete` |
| 2026-10-17 | H1b | i9-13900T (AVX2 and VNNI, no AVX-512, no AMX): the pre-registered H1b probe on one fresh cluster `k3dgpu-h1b-eat` (k3s v1.37.1, claude/h1b-eat 1ac2a39, EAT release-1.0.3-hotfix) under the `typical` background load, each step once ([H1b run](#h1b-run-i9-13900t-2026-10-17)) | SUPPORTED. `up` 48.9 s, EAT stack 494.6 s (5 PLAY RECAPs `failed=0`), model step 154.9 s, `kubectl wait ... --field-selector=status.phase!=Succeeded` exit 0 after 280.1 s (33 pods, vLLM included). Jobs `helm-install-gateway-api-crd` and `register-qwen-qwen3-1-7b` both `Complete`. Completion HTTP 200, `finish_reason` stop, 20 prompt / 27 completion tokens; `eat-check.sh` passed. No Xeon-only instruction or label needed | No for this host; another CPU family needs its own row |
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

### H40-H48 pre-registration (agent benchmark, 2026-10-17)

Fixed before any probe runs. Nothing here is a measurement. Harness:
`bench/eatflow/` (`k3d-gpu bench agent`), guide: [docs/agent-benchmarks.md](docs/agent-benchmarks.md).
Every external piece is pinned in `bench/eatflow/agent.lock` (images by digest,
weights by revision or sha256, suites by version and wheel sha256, blueprints by commit).

Common rules (all of H40-H48):

- Model, every arm: `Qwen/Qwen3-4B-Instruct-2507` @ `cdbee75f17c01a7cc42f958dc650907174af0554`
  (A3 AWQ `cyankiwi/...-AWQ-4bit` @ `c46e2277`, C1 `qwen3:4b-instruct-2507-q4_K_M` manifest
  sha256 `0edcdef3...`, C2 unsloth Q4_K_M LFS sha256 `3605803b...`; full pins in agent.lock).
- Path, every local arm: agent -> recording proxy -> EAT LiteLLM -> backend Service. Only the
  LiteLLM backend URL changes per arm; the agent's `MODEL_NAME` alias stays
  `Qwen/Qwen3-4B-Instruct-2507`.
- Budget: <= 20 min of measurement per arm per hypothesis; bring-up outside it, capped at 15
  min. A bring-up over the cap or an OOM scores the arm "not deliverable" (as in H8).
- Load: typical unless stated (the headline), heavy for robustness, none as reference only.
  Each block runs `load.py sample --tag ARM`; `load.py matched` exit 3 makes a comparison
  INCONCLUSIVE whatever the latencies say.
- Turn latency: proxy `e2e_s`, errors and > 120 s count as +inf; nearest-rank p95 with a
  seeded bootstrap CI (2000 resamples, seed 40). TTFT is the first byte of any delta.
- Success: Wilson 95%; arm vs arm is the paired difference on shared task ids with a
  paired bootstrap (2000 resamples, seed 40).
- Censoring: a task cut by the block budget is a lower bound, never a failure; a task over
  the 600 s per-task timeout is a failure.
- Validity (PROTOCOL.md section 8, checks 2-4): `tool_valid_any` >= 0.9 at N=1;
  tool-turn `completion_tokens` medians within 2x across arms; N=1 drift within +/-15%.

Task sets (graders: `bench/eatflow/tasks.py`; expected values from the blueprint's seed
data, `src/mcp_server/data.py` sha256 `37b7b9c8...`):

- B24 = users U1001, U1002, U1003 x templates T1 (all balances), T2 (one balance), T3
  (last k transactions), T4 (loan next-due and remaining), T5 (find by email, total
  deposits), T6 (valid transfer, mutating), T7 (loan payment, mutating; U1003 holds no
  loan, so the end state must stay at seed), T8 (overdraft transfer that must be refused).
  Ids `B-T<k>-<user>`. The four README prompts are T1, T3, T4, T6 of U1001, verbatim.
- B15-RO = T1-T5 of all three users: the closed-loop concurrency set (no write tool).
- C12 = C01-C12, one exact answer each; C01, C02, C03 are the coding README prompts
  verbatim; C03, C11, C12 need `install_package` (PyPI egress).

S100 (`bench/eatflow/s100.json`, `bfcl.py s100 --data` regenerates it from the pinned
wheel's data; seed 40, stratified, then a seeded shuffle that fixes the run order):

| Category | Ids |
|---|---|
| simple_python | 16, 125, 234, 268, 296 |
| simple_java | 16, 26, 36, 81, 85 |
| simple_javascript | 17, 22, 28, 47, 48 |
| multiple | 7, 13, 15, 29, 33, 45, 51, 80, 117, 135, 154, 158, 164, 181, 188 |
| parallel | 12, 24, 37, 58, 64, 80, 83, 85, 104, 131, 133, 174, 187, 188, 193 |
| parallel_multiple | 16, 39, 40, 41, 43, 53, 74, 79, 110, 118, 125, 132, 133, 150, 178 |
| irrelevance | 2, 22, 28, 48, 52, 61, 77, 116, 118, 119, 149, 155, 156, 203, 215 |
| live_simple | 19-3-15, 35-12-0, 50-22-0, 55-25-1, 76-37-0, 95-56-0, 97-57-1, 126-82-0, 145-95-2, 169-99-3, 197-116-5, 212-117-4, 228-119-0, 232-122-1, 241-125-4 |
| live_multiple | 39-14-1, 326-132-16, 350-133-14, 396-139-0, 455-145-6, 492-148-2, 625-160-5, 856-180-4, 901-185-11, 981-215-5 |

An arm cut by its budget has completed a prefix of the run order (chunks of 10); arms are
compared on the intersection of completed ids, and an intersection below 60 is INCONCLUSIVE.

Changes from the design draft, fixed here before measuring:

1. BFCL mode. The board's row for this model uses BFCL's `QwenFCHandler`: BFCL applies
   the chat template itself and calls `/v1/completions`, so the serving engine's tool
   parser is never used. H40 runs that exact handler (`--mode board`). H41 runs
   `--mode server-fc`: BFCL's own `OpenAICompletionsHandler` (chat completions with
   `tools`) registered at launch as `k3d-gpu/server-fc` with `underscore_to_dot=True`,
   because that handler renames `.` to `_` in tool names. A3ref runs in both modes; H41's
   reference is A3ref in server-fc mode.
2. BFCL temperature is BFCL's default, 0.001 (the board's own setting), not 0.
3. The concurrency set is B15-RO (T1-T5, 15 tasks), not B16-RO: T8 calls a write tool.
4. BFCL venv: `bfcl-eval==2025.12.17` needs Python <= 3.12 (it pins numpy 1.26.4), and its
   unpinned `qwen-agent` imports `soundfile` without declaring it. The proven venv is
   frozen in `bench/eatflow/bfcl-constraints.txt`.
5. Task attribution: an LLM request belongs to a task when its last user message equals
   the task prompt and the proxy received it inside the task's time window.
6. The vLLM CUDA digest `a4a4c043...` is the linux/amd64 manifest of tag v0.31.0 (the
   tag's index digest is `c1c9f6fd...`).

Amendments after two refuters, fixed 2026-10-17 before any measurement (harness changes
in `bench/eatflow/`, each with a regression test in `test_agentbench.py`):

7. Sampling is pinned. The blueprint agent sends no sampling keys, so each engine fell
   back to its own default (vLLM: the model's `generation_config.json`, temperature 0.7,
   top_k 20, top_p 0.8, https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/raw/cdbee75f17c01a7cc42f958dc650907174af0554/generation_config.json;
   llama.cpp server: temperature 0.8, https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).
   The proxy now replaces every sampling key of every agent request with one set from
   `agent.lock` `sampling`: local arms `agent_local` = temperature 0, top_p 1, max_tokens
   1024 (greedy); arm R `agent_remote` = top_k 1, max_tokens 256, reasoning_effort low
   (harvest forbids temperature 0 and logprobs). Each proxy record and `facts.json`
   (`sampling_seen`) carry the set. BFCL keeps 0.001 (item 2), except on arm R, whose one
   shared proxy pins `agent_remote` for every request.
8. Agent memory is off. The blueprint defaults to `ENABLE_MEMORY=true` (one Redis index,
   user `default_user`), which re-injects earlier prompts as extra user messages. Every
   arm runs the agent with `ENABLE_MEMORY=false`; the proxy records each request's
   user-message count, a task request with more than one aborts the block, and
   `facts.json` records `memory`. Attribution (item 5) also matches the first user message.
9. Arm vs arm compares only shared completed task ids. The H42 (2), H46 and H47 time
   ratios are the paired ratio exp(median of per-id log(arm/ref)) over task ids both
   arms completed, with a paired bootstrap CI (2000 resamples, seed 40). Success is read on
   the same shared ids for comparisons; every absolute success rate is stated with its
   completed-id count (`k/n`), and H42 (1) thresholds are read on completed ids only.
10. Grader fixes. T8 needs a valid balance read of the source account (or `get_balances`,
    or a `transfer_funds` attempt) and an answer that cites insufficient funds ("cannot"
    or "unable" alone no longer passes). B-T7-U1003 needs a no-loan answer. A credit
    account's balance accepts the available credit or the amount owed (B-T1-U1002: 3200.00
    or 1800.00). Code tasks need each expected value in the sandbox tool output too, and C03
    fails when the 11th prime (31) is listed.
11. Load-only control windows (H46) go to `control-load.jsonl` and never enter load matching.
12. One unscored warm-up prompt (not in any task set) runs before each block's window.
13. In cycle mode (`apb`, H45) each sent prompt starts with a unique nonce, so prefix and
    prompt caches see unique user turns.
14. A task's turns are read once its final turn is in the proxy log (polled, up to 30 s),
    not after a fixed 2 s.
15. `--reset` must exit 0 within 300 s and the MCP server must read back the seed; then the
    warm-up prompt must answer again (a stale MCP session shows here). A failure aborts the
    block (`blocks.jsonl` `aborted`, exit 1). A refused task whose end state moved off the
    seed is reset too. The MCP URL must survive a reset (a NodePort, an in-cluster URL, or a
    host process), never a `kubectl port-forward`.
16. H42 is a stack-vs-stack claim. A1 (OVMS, OpenVINO int4 self-export, P-cores) and B1
    (EAT stock vLLM CPU, bf16) differ in runtime, placement and weight precision at once,
    so the A1/B1 ratio does not isolate any one of them; the row now says so.
17. Remote key and cap: `proxy --key-file` reads the key from a 0600 file owned by the
    caller (no environment-variable path), refuses to start without `--max-requests`, and
    counts every forwarded POST against the cap. The proxy records `finish_reason`; on arm R
    an item whose turns hit `finish_reason=length` (the 256-token cap) is unmeasured, like
    an item cut by the request cap. H48 then reads: SUPPORTED when at least 10 items are
    measured and at most 1 measured item fails; REFUTED when 3 or more measured items fail;
    otherwise INCONCLUSIVE.
18. `bench/eatflow/bfcl-constraints.txt` carries a sha256 for every package
    (`uv pip compile --generate-hashes`, the same 128 versions) and installs with
    `--require-hashes`.

Live-run setup, fixed 2026-10-17 before the first measured block (i9-13900T, one shared
lock, each hold at most 20 min wall time):

19. Topology per local arm: the model server runs in a fresh k3d-gpu cluster
    `k3dgpu-a-<arm>` (A1, A3, B1 staged as in `bench/apb/arm.sh`) or, for the vendor stacks
    C1 and C2, as a host container with its vendor defaults, as in `k3d-gpu bench --vs`.
    The rest of the path runs on the host, the same for every arm: the banking agent
    (DevUI) and its FastMCP sidecar from the pinned blueprint commit `758d484`
    (`ENABLE_MEMORY=false`, sidecar reset = restart the process), the recording proxy, and
    EAT's LiteLLM at the version EAT `0d3438d` pins (`v1.83.14-stable.patch.2`, by digest
    in the run's `facts.json`) holding the one alias `Qwen/Qwen3-4B-Instruct-2507`. This
    replaces EAT's in-cluster deployment of the agent and gateway, which the hold cannot fit;
    the agent code, prompts, tools, gateway and model path are unchanged.
20. ABBA halves need two holds per arm. Where time allows only one hold per arm, each arm
    runs one block (B24 in order, budget what the hold leaves after bring-up, at most 12
    min), in the order R, A3, A1, B1, C2, C1, A2; time ratios then come from single blocks
    and the verdict says so.

Recorded 2026-10-18 03:30 PDT, after the H42 blocks and before any block below:

21. Two grader fixes were made during the campaign, after the blocks they changed, from
    those blocks' recorded answers; each has a regression test, and `regrade` applied them
    to every block (the first rows stay in `results.raw.jsonl`). This is post hoc and is
    reported as such. (a) `069b7ae` (2026-10-17 15:20 PDT, after A3's block): T8 also
    accepts a refusal that states the balance is below the amount. (b) `1fbbe39` (23:11
    PDT, after R's second block): when DevUI's answer equals the model's own text at the
    proxy up to whitespace, the model's text is graded (Agent Framework joins split text
    items with spaces, so `$4,250.75` came back as `$ 4 , 250 . 75`). Rows changed: A3 3
    (all T8), R 4; every other block 0.
22. H48. Run 1 (2026-10-17 15:04, `837bf0b`) is void: the proxy forwarded two
    Authorization headers and Cloudflare answered HTTP 400 (fixed in `0ed40bc`). Run 2
    (23:07, `71db3ac`) is the first valid run and is scored as pre-registered, with the
    graders it ran with. One more hold (run 3: the same 12 items, cap 20, `agent_remote`,
    graders as of this amendment) checks the fixed graders; it is reported beside run 2 and
    does not replace run 2's verdict.
23. H41 runs one hold per arm, in the order A1, A3, C2, C1, B1: the item-19 topology,
    `live.sh` with `LIVE_MODE=bfcl`, one server-fc block of S100 at typical load with a
    budget of what the hold leaves (at most 900 s), not ABBA halves. The reference is
    A3ref's server-fc block of 2026-10-18 00:48 (typical load, 100 ids, 88.0%). Load is
    matched per arm against A3ref's windows. With swap use on this host drifting by GBs
    between holds (spread limit 256 MiB), an unmatched verdict is likely and is stated
    before measuring.
24. H44 runs for A1 only, in one hold: `live.sh` with `LIVE_MODE=h44`, B24's first 12
    tasks in blocks none/heavy/none/heavy of equal budget (at most 300 s; what the hold
    leaves, about 180 s), one output directory per block. Inflation = pooled p95 turn
    latency at heavy over pooled p95 at none, with `stats.ratio_ci` (2000 resamples, seed
    40), computed by `agentbench.inflation`. B1 is not run: its turns at typical took
    69-329 s at the proxy, over the 120 s turn timeout, so its p95 is +inf at every level
    and the ratio is undefined, and four B1 blocks with their warm-ups do not fit a hold.
    H44 can therefore end REFUTED (A1's CI lower bound > 1.25) or INCONCLUSIVE, not
    SUPPORTED.
25. Not run in this campaign, recorded: H43 (the coding agent is not wired into the live
    runner), H45 (four 300 s ramp steps plus bring-up exceed a 20-min hold), H47 (H34 was
    SUPPORTED at 2026-10-17 23:21 PDT, after this campaign started at 15:04, so by its own
    rule H47 is not run). B1 ran one H42 block: it completed 1 task, and the hold's timeout
    cut its H46 control window, so B1's J per success is undefined.

Recorded 2026-10-18T14:49Z (07:49 PDT), before any run under it. Method change to
`bench/load/load.py matched`; it applies to every probe that samples that tool, not only
H40-H48, and to NEW runs only:

27. Matching splits covariates from outcomes. **Why:** the earlier rule (H24 A1, H25 A2,
    H20 A2, H30-H32 A1/A2 and the rest) gated on every recorded metric, including package W,
    throttle events/min and 1-min load, and those move with the treatment itself: a
    cache-OFF arm does about 10x the prefill of cache-ON, so it heats the package and
    throttles more. Matching on a post-treatment variable can void a real effect. It voided
    [H25 run 3](#h25-run-3-matched-background-load-i9-13900t-2026-10-17) (every metric
    matched but throttle/min, 67.8 > 26.4 at typical, 1407.7 > 605.5 at heavy),
    [H32 run A2](#h31-h32-run-a2-idle-gap-before-each-load-window-i9-13900t-2026-10-18)
    (throttle/min 147.9 > 147.7, everything else matched) and
    [H20 run 2](#h20-run-2-amendment-a2-matched-background-load-i9-13900t-2026-10-18)
    (throttle/min only, at both levels; the pre-cell throttle rate followed the arm that had
    just run). [H26](#h26-run-matched-background-load-i9-13900t-2026-10-17) failed on
    carry-over (windows started seconds after the previous hog was deleted, so its run queue
    and throttling were still in the window) and at heavy its background containers vanished
    mid-level while the blocks went on being scored. **Rule** (`matched_a27`, picked when the
    windows carry a `phase`):
    (a) Covariates, gated under the unchanged `TOLERANCE` limits: from a **pre-window**
    sampled with the arm idle at the end of a settle gap before each arm starts, load-CPU
    `busy_pct`, `throttle_per_min`, `swap_used_mib` and `swap_delta_mib`; from windows
    **during** the arm, `busy_pct` over the load CPUs the treatment does not use
    (`--treatment-cpus` removes the ones it does) and the two swap metrics. Every arm needs
    at least one pre-window.
    (b) Outcomes, reported per arm and never gating: `pkg_w`, `throttle_per_min` and `load1`
    during the arm.
    (c) Hog-decay gap (`load.py settle`): after any arm or hog stops, 5 s windows are sampled
    until one reads `busy_pct` and `throttle_per_min` within tolerance of the level baseline
    (`max(abs, rel x baseline)`, the baseline being `--phase baseline` windows taken once
    after the level starts and before the first arm), at most 60 s. That window is the
    arm's pre-window. A gap that hits 60 s is recorded (`settled: false`) and reported; the
    pre-window covariates still decide.
    (d) Missing load: every phased window counts this tool's running load containers at its
    start and end (`load_running`, the minimum) against the number its level runs
    (`load_expected`). Any window with fewer, or with no count, makes the run **VOID**
    (`load.py matched` exit 4): never scored, re-run. Level `none` expects none.
    Windows without a `phase` keep the earlier rule unchanged, so no earlier run is
    regraded: H20, H25, H26 and H32 keep their INCONCLUSIVE verdicts. A probe adopts
    amendment 27 by sampling with `--phase`; its run section says which rule scored it.
    Load rule L1 (the H47/H49 interleaved, per-pair design) is separate and unchanged: a
    design that pre-registers L1 is scored by L1.
    Unit tests: `bench/load/test_load.py` (`Amendment27Test`, `SettleTest`,
    `WindowPhaseTest`): a treatment that raises throttle 10x during the arm still
    matches; background drift in the pre-window busy or throttle, in during-arm load-CPU
    busy, or in swap does not; a vanished load container voids.

Static proof of the harness (no cluster, 2026-10-17): the unit suite against stub servers;
BFCL S100 in both modes through the proxy against a stub endpoint with the pinned wheel
(100 of 100 ids written and scored; a stub that never calls a tool scores exactly the 15
irrelevance ids); and the real banking-assistant agent (Agent Framework DevUI and the
FastMCP sidecar from the pinned blueprint commit) against a scripted stub LLM, graded
end to end, including a mutating transfer read back from the MCP server.

### H40-H48 run (i9-13900T, 2026-10-17 and 18)

Host: the shared i9-13900T (8 P-cores, 16 E-cores, 125 GiB RAM, RTX 4000), with other
tenants running throughout: 1-min load 4.7-33, swap used 141-156 GiB of 187.5 GiB. Every hold
took the shared lock, ran at most 19.5 min (`timeout 1170`), used a fresh cluster
`k3dgpu-a-<arm>` (or a host container for C1/C2) with a scratch `KUBECONFIG`, and deleted
it before releasing the lock. Topology: amendment 19. Sampling: `agent_local` on every
local agent request and `agent_remote` on arm R, as recorded in each `facts.json`
(`sampling_seen`); agent memory off in every block (`facts.json` `memory`: every
attributed request carried one user message). Records (results, turns, blocks, load
windows, control windows, BFCL scores, facts, the hold log):
[bench/eatflow/results/2026-10-17/](bench/eatflow/results/2026-10-17/).

Harness commits: A1, B1, C2 at `069b7ae`; A3 and R run 1 at `837bf0b`; R run 2 at
`71db3ac`; C1, A2, A3ref at `1fbbe39`; R run 3 and every H41/H44 hold at `e52d0fe`
(amendments 21-25, pushed before those holds). Images by digest in each arm's
`image.txt` and `litellm-image.txt`. Blueprint `758d484`.

**H42 and H46: B24 at N=1, typical load, one block per arm (2026-10-17).** Table from
`bench/eatflow/live_table.py` (completed = not cut by the budget; J per completed task is
gross RAPL package energy, plus nvidia-smi for A3; J per success is differential: block
energy minus the arm's control-window mean power times the block time, over successes):

| Arm | Completed / 24 | Success (Wilson 95%) | Turn p95 s | Task median s | J / completed task | J / success (diff.) | Load vs A1 |
|---|---|---|---|---|---|---|---|
| A1 k3d-gpu CPU, OVMS int4 | 24 | 0.83 (20/24), 0.64-0.93 | 20.9 | 15.6 | 1861 | 76 | reference |
| A2 k3d-gpu iGPU, OVMS int4 | 14 | 0.79 (11/14), 0.52-0.92 | 61.6 | 43.0 | 5151 | 191 | unmatched |
| A3 k3d-gpu RTX 4000, AWQ | 24 | 0.75 (18/24), 0.55-0.88 | 2.1 | 2.0 | 362 | 73 | unmatched |
| B1 EAT stock vLLM CPU, bf16 | 1 | 1.00 (1/1), 0.21-1.00 | +inf | 398.5 | 55544 | undefined | unmatched |
| C1 Ollama Q4_K_M | 21 | 1.00 (21/21), 0.85-1.00 | 42.0 | 29.2 | 3967 | 81 | unmatched |
| C2 llama.cpp Q4_K_M | 20 | 0.95 (19/20), 0.76-0.99 | 39.4 | 30.8 | 3442 | 19 | unmatched |

Paired on shared completed ids against A1 (`report --ref A1`): A3/A1 time 0.12 (CI
0.11-0.12, n=24), A2/A1 2.64 (2.24-2.74, n=14), C1/A1 1.68 (1.61-1.92, n=21), C2/A1 1.76
(1.65-1.83, n=20), B1/A1 20.7 on n=1 (B-T1-U1001: A1 19.2 s, B1 398.5 s), so A1/B1 =
0.048 with no interval. Load across the six blocks: INCONCLUSIVE (busy 36 pp, 1-min
load 21, package 27 W, throttle 4559/min and swap used 15.7 GiB apart; the blocks ran
over 9.5 hours of a shared host).

- H42 criteria on point estimates: (1) A1 0.83 and C2 0.95 pass; A3 0.75 passes only
  after amendment 21a's T8 regrade (as run: 15/24 = 0.625); C1 1.00 is not <= A1 - 10, so
  that prediction failed. (2) A1/B1 0.048 rests on the one task B1 completed. (3) A3
  faster than A1 holds (0.12). (4) A2 slower than A1 holds (2.64). (5) B1 completed 1 of
  24 in its hold (its three agent turns took 69, 75 and 329 s at the proxy, so its
  nearest-rank p95 is over the 120 s turn timeout: +inf).
  **Verdict INCONCLUSIVE**: load unmatched. Neither REFUTED condition holds (A1 success
  0.83 >= 0.6; the A1/B1 ratio has no interval, point 0.048).
- H46: A3 73 J < A1 76 J per success (dGPU included) and A2 191 J > A1 hold as point
  estimates; A1/B1 is undefined (B1 has 1 success and its control window was cut by the
  hold timeout). C2's 19 J shows how small the differential gets when the control window
  draws nearly the block's power (95.1 W vs 95.6 W). **Verdict INCONCLUSIVE.**
- Failures, all graded on recorded tool calls and end state: A1 missed a balance call on
  B-T5 twice, the loan read on B-T7-U1003 and the no-loan answer on B-T4-U1003; A3 skipped
  the balance call on all three B-T2 tasks and the transfer on B-T6-U1002, and gave no
  no-loan answer on B-T4-U1003 and B-T7-U1003. C1's budget cut B-T6-U1003 and the reset
  after it did not read back the seed, so the block aborted there (its 21 completed rows
  stand; B-T7-U1003 and B-T8-U1003 did not run).

**H40 and H41: BFCL S100.** A3ref (bf16, vLLM CUDA) in board mode at no load, 2026-10-18
00:45: 88.0% on 100 of 100 ids (board-weighted 84.9, band 77.9-91.9), every request HTTP
200, every category inside its binomial 95% interval of the board value (irrelevance
100.0 vs 84.9, live_multiple 80.0 vs 76.2, live_simple 86.7 vs 79.1, multiple 86.7 vs
93.5, parallel 93.3 vs 92.5, parallel_multiple 93.3 vs 90.0, simple 73.3 vs 75.5).
**H40 SUPPORTED**, so H41 may be read. Server-fc at typical load, paired on shared ids
against A3ref's server-fc block (88.0%, 100 ids):

| Arm | Ids | Accuracy % | Diff vs A3ref (one-sided 95% LB / UB) | Load vs A3ref | Rule before load |
|---|---|---|---|---|---|
| A1 | 100 | 81.0 | -7.0 (-11.0 / -3.0) | unmatched (load1, pkg W, throttle, swap 4.2 GiB) | straddles -8 |
| A3 | 100 | 87.0 | -1.0 (-4.0 / +2.0) | unmatched (load1 1.27 > 1.26, pkg W, throttle, swap 4.4 GiB) | holds |
| C2 | 82 | 86.6 | -1.2 (-4.9 / +2.4) | unmatched (all six metrics) | holds |
| C1 | 60 | 80.0 | -11.7 (-20.0 / -5.0) | unmatched (five metrics) | straddles -8 |
| B1 | 8 | 25.0 | - (8 ids < 60) | - | fewer than 60 ids |

B1 at typical load answered 2 of its 24 BFCL requests inside the proxy's 120 s timeout;
its 25.0% measures the timeout, not tool-calling quality. **H41 INCONCLUSIVE** for every
arm: load unmatched everywhere, and A1 and C1 straddle the margin anyway.

**H44: A1, heavy vs none (2026-10-18 06:09-06:22).** Blocks of 186 s, none/heavy/none/
heavy. none: 12/12 and 12/12 tasks, E-core busy 22-23%; heavy: 7 and 7 tasks completed
(13/14 passed, one cut by the budget), E-core busy 99.9%. Pooled p95 turn latency 43.4 s
heavy (29 turns) vs 16.7 s none (50 turns): inflation 2.59, bootstrap CI 0.87-2.94. On
the 7 task ids both levels completed (sensitivity only, not the registered statistic):
1.73, CI 0.85-3.55; median turn 6.2 s vs 3.7 s. The CI contains 1.25, so A1 neither holds
(<= 1.25) nor fails (lower bound > 1.25), and B1 was not run (amendment 24). **Verdict
INCONCLUSIVE.** The point estimate says heavy E-core load stretches A1's tail turns
about 2.6x, against the predicted <= 1.25x.

**H48: arm R, GLM-5.3 through harvest.** Run 1 (15:04) is void (amendment 22). Run 2
(2026-10-17 23:07, 17 requests): BFCL 7/8, B24 0/4 as graded then; every B24 answer was
correct but arrived from DevUI as `$ 4 , 250 . 75`-style text. That is 5 failures of 12
measured items: **REFUTED as pre-registered.** Audit of every failed item: the 4 B24
failures were the grader's input (DevUI joins text items with spaces), fixed in
`1fbbe39` with a regression test; `irrelevance_203` is the model's own (it called a tool
on an irrelevance item). Regraded run 2: 11 of 12. Run 3 on the fixed graders
(2026-10-18 03:14, 17 requests, every turn `finish_reason` stop or tool_calls, none
truncated): BFCL 7/8, B24 4/4, 11 of 12, the same single model failure. The key came only
from the 0600 env file through `proxy --key-file`; no record or log of either run
contains it (checked by string search).

**Not run:** H43, H45, H47 and B1's H44 blocks (amendment 25). They stay PENDING.

### H47 and H49 pre-registration: within-hold interleaved design (2026-10-18)

Written 2026-10-18T07:50-07:00, before any H47 or H49 measurement and before any harness
code for it. Nothing here is a measurement; the swap analysis below reads only windows
already committed under `bench/eatflow/results/2026-10-17/`.

**Root cause of the H41, H42 and H46 INCONCLUSIVE verdicts.** Each arm ran in its own
hold of the shared lock, and the holds were up to 9.5 hours apart. The host's other
tenants moved between holds: swap used drifted 4.2-15.7 GiB, and the 1-min load, package
power and throttle rate moved with it. `load.py matched` compares arm means, so it saw the
drift between holds and reported it as unmatched load. That is the right reading of that
design: the arms really did run on different hosts in time. The fix is to stop comparing
holds. Both arms must run inside one hold, interleaved task by task, with load sampled
per task and matched per pair.

**Design (H47 and H49).**

1. One hold, both arms live. Both model servers run at once in one cluster
   `k3dgpu-a-h<id>`. On the host, each arm has its own LiteLLM (the one alias
   `Qwen/Qwen3-4B-Instruct-2507`, backend = that arm's server), its own recording proxy and
   its own banking agent (DevUI, `ENABLE_MEMORY=false`). One FastMCP sidecar is shared.
   Only one task is in flight at any time, so the idle arm's server carries no request.
2. Pairs. A pair is one task id run once on each arm. Pairs follow B24's fixed order;
   pair j runs A then B when j is even and B then A when j is odd. The sequence is
   therefore A B B A A B B A over the same task ids. A pair never spans two holds.
3. Reset. After every task attempt that may have moved state (T6, T7, or any attempt whose
   end state left the seed), the sidecar is reset and must read back the seed before the
   next attempt (amendment 15). The second arm of a pair therefore starts from the same
   seed as the first.
4. Warm-up. One unscored warm-up prompt per arm at the start of each hold (amendment 12).
5. Per-task load sample. Each attempt records, over its own window from send to final
   turn: host busy CPU-seconds per second over all 32 CPUs (`/proc/stat`), the CPU usage of
   each arm's server pod (cgroup `cpu.stat` `usage_usec`), package energy (RAPL),
   `load1`, throttle events, swap used and swap growth, and E-core busy %. Background CPU
   is `bg_cpu` = host busy CPU-s/s minus both arm pods' CPU-s/s. It covers the background
   load generator, the production tenants, the agent, LiteLLM and the proxy, and it
   excludes the arms' own work.
6. No block-budget censoring. A pair starts only if the hold has at least 600 s left
   (two task caps of 240 s plus 120 s for resets). A pair cut by the hold timeout anyway is
   void: both of its attempts are dropped and counted.
7. Idle-arm check. For every attempt, the other arm's pod CPU over that window is
   recorded. If its median over all attempts exceeds 0.5 CPU, the arms were not
   independent and the comparison is INCONCLUSIVE.
8. Statistics. Common rules (H40-H48) apply unless stated: Wilson 95% per arm; arm vs arm is
   a paired bootstrap on matched pairs (2000 resamples, seed 40); time and energy ratios use
   `stats.paired_ratio`.

**Load rule for interleaved designs (amendment L1 to `bench/load/load.py` matching,
2026-10-18; new runs only).** Applies to H47, H49 and later interleaved designs. Every
verdict already recorded keeps the rule it was graded with and is not re-graded.

- Per pair: matched when |`bg_cpu`(A) - `bg_cpu`(B)| <= 0.8 CPU-s/s. That is
  `TOLERANCE` `busy_pct` abs 5 pp of the 16 load CPUs, restated in CPUs, because the arms'
  own work must not enter the metric. B1 is EAT stock and its vLLM floats onto the loaded
  E-cores, so E-core busy % would measure the arm, not the background. Unmatched pairs are
  dropped from every statistic and counted. In the committed one-arm windows, 12 of 100
  adjacent 30 s window pairs differ by more than 5 pp E-core busy, so about 3 of 24 pairs
  are expected to drop.
- Arm level, over the matched pairs: mean `bg_cpu` spread <= 0.8 CPU-s/s, and mean
  `swap_used_mib` spread <= 256 MiB (unchanged).
- Recorded and reported per arm, not gated: swap growth (as MiB/min, because per-task
  windows run 8-240 s), `load1`, `pkg_w`, `throttle_per_min` and E-core `busy_pct`. In a
  within-hold design `load1`, `pkg_w`, throttle and E-core busy are driven by which arm is
  working, so a spread there measures the arm, not the host.
- INCONCLUSIVE when fewer than 16 pairs are matched, fewer than 75% of the pairs run are
  matched, or the arm-level check fails.

Swap tolerance, re-examined from recorded idle drift, not from any H47 or H49 outcome.
Committed data: the nine one-arm load files of 2026-10-17/18 with at least 8 windows
(`A1`, `A2`, `B1`, `C1`, `C2` `run/load.jsonl`; `H41-A1`, `H41-B1`, `H41-C1`, `H41-C2`
`bfcl-fc/load.jsonl`; 14-30 windows of 30 s, 6.5-14.5 min each). One arm ran in each, so
any spread between labels is host drift alone. Relabelling each file's windows A B B A in
time order and running `load.matched`:

- `swap_used_mib` arm-mean spread: 0.0-98.3 MiB, median 11.8. All 9 inside 256 MiB. Inside
  one hold, interleaved, 256 MiB is defensible: the null never comes within 2.6x of it. It
  failed H41/H42 only because those arms ran hours apart (4.2-15.7 GiB). It stays 256 MiB.
- `swap_delta_mib` arm-mean spread: 0.2-121.9 MiB per window, over 64 MiB in 3 of 9 files
  (A2 121.9, C1 96.7, C2 71.3). The 64 MiB gate fires on pure host drift in a third of
  null holds, so it is a coin flip, not a load check. In an interleaved design swap growth
  is a host-wide rate that hits both arms by construction. As in H27 amendment A3, swap
  growth is recorded per attempt and reported per arm, never a gate.
- Per adjacent window pair, |swap used A - B| > 256 MiB in 13 of 100 pairs (up to 985
  MiB, step jumps from the production tenants). So swap used is matched at the arm level,
  not per pair; per pair it would drop pairs on step events that no arm caused.

The ~140 GiB of swapped-out production pages is a level, not a rate. A fixed level does not
change between paired attempts minutes apart. Only the step jumps do, and those are what
the rule above records.

**H49. k3d-gpu auto-config vs EAT stock, inside EAT, on B24.**

- Arms. A1: k3d-gpu `agent manifest` as in amendment 19 (OVMS 2026.4.1, OpenVINO int4
  self-export `k3dgpu/Qwen3-4B-Instruct-2507-int4-ov`, P-cores) plus the Fast Draft default
  of #116 (`OpenVINO/Qwen3-0.6B-int4-ov` @ `f864c610` on the CPU). Recorded scope: the
  default rule (`agent_draft_measured` in `scripts/k3d-gpu`) drafts only for model ids
  matching `OpenVINO/Qwen3-<n>B-`. The benchmark's self-exported id does not match, so A1
  sets `K3D_GPU_AGENT_DRAFT=CPU` to get the draft the default applies to an OpenVINO Qwen3
  id. B1: EAT stock vLLM CPU, bf16, `xeon-values.yaml` as shipped with the 6 GiB KV cache of
  amendment 19, no pinning. Both in one cluster (node labels for B1 as in `live.sh`).
- Set and level: B24, N=1, typical load on the E-cores, 24 pairs. Holds run until 24 pairs
  or 12 holds, whichever comes first; the hold count is reported.
- Budgets. Turn: the proxy's 120 s timeout. Task cap: 240 s for both arms, two turns at the
  turn budget. Of the 45 B24 tasks A1 and C2 completed in H42, 42 took 2 LLM requests and 3
  took 3. A1's slowest B24 task took 39 s, so the cap does not bind A1.
- Outcome of each attempt, every one a measured result:
  - `pass_budget`: graded pass, every turn <= 120 s, task <= 240 s.
  - `fail_graded`: finished within both budgets, graded fail.
  - `over_turn`: a turn hit the 120 s proxy timeout.
  - `over_task`: the task reached the 240 s cap.
  - `void`: infrastructure fault not caused by the arm (reset did not read back the seed,
    sidecar down, pair cut by the hold timeout). Dropped with its pair and counted; more than
    2 void pairs makes H49 INCONCLUSIVE.
  `over_turn` and `over_task` are failures to deliver within budget, never missing data and
  never lower bounds. Their counts are reported per arm.
- Primary: within-budget success, the share of attempts that are `pass_budget`, per arm
  (Wilson) and paired A1 - B1 on matched pairs (bootstrap CI).
- Secondary, reported and not gated: J per completed task (gross package RAPL over the
  attempt window; completed = `pass_budget` or `fail_graded`; undefined for an arm with
  none, stated so). Both servers are resident throughout, so each attempt's energy includes
  the idle arm equally. The paired J-per-attempt ratio A1/B1 is also reported; B1's
  over-budget attempts stop at the cap, so its J is a lower bound and the ratio an upper
  bound. Also p95 turn latency per arm (nearest rank, timeouts as +inf) and median time per
  attempt.
- Prediction, before measuring: A1 within-budget success >= 0.70 (0.75). B1 <= 0.10
  (0.8): its H42 turns took 69, 75 and 329 s under typical load. Paired difference >= +60
  points (0.7).
- SUPPORTED: the paired difference's 95% CI lower bound >= +30 points and A1 within-budget
  success >= 0.6, with the load rule passed and the idle-arm check passed. REFUTED: the CI
  upper bound < +30 points. Otherwise INCONCLUSIVE.
- Scope: a stack-vs-stack claim (amendment 16). It does not isolate runtime, placement,
  precision or the draft.

**H47 (row unchanged, design replaced).** The row's rule "runs only if H34 is SUPPORTED when
the campaign starts" is met: H34 was SUPPORTED on 2026-10-17 at 23:21 PDT, and this
campaign starts after this pre-registration.

- Arms: A1 = the H49 A1 stack with `K3D_GPU_AGENT_DRAFT=off`. A4 = the same with
  `K3D_GPU_AGENT_DRAFT=CPU`. Both run in one OVMS pod on the P-cores serving two model
  names, as H34 did with its arms A and B. One cluster, one pod; each arm has its own
  LiteLLM, proxy and agent as in the design above.
- Set and level: B24, N=1, typical load, 24 pairs, at most 2 holds. Same pair order, resets,
  per-task sampling, idle check (here the idle check reads the OVMS pod's CPU while no
  request is in flight) and load rule L1.
- Statistic and verdict, unchanged from the row: gain = 1 - paired median time ratio
  A4/A1 on matched pairs where both completed (amendment 9), and delta success A4 - A1 on
  matched pairs. SUPPORTED: gain CI lower bound >= 10% with delta success >= -5 points.
  REFUTED: gain CI upper bound < 0. Otherwise INCONCLUSIVE. Prediction unchanged: 0-15%
  gain, SUPPORTED at 0.35.

**Run order and harness.** H47 runs before H49 (it is short and checks the runner on the
fast arm). The runner (`live.sh` with `LIVE_MODE=pairs` and an `agentbench pairs`
subcommand) does not exist yet. It lands in its own PR, with regression tests for the
pair order, the per-task `bg_cpu` sample, rule L1, void handling and the 600 s pair-start
guard. That PR must pass the gate before the first hold. If it must deviate from anything
above, a dated amendment is recorded here first.

### H47 and H49 amendment P1: the runner (2026-10-18)

Recorded 2026-10-18, with the runner, before any H47 or H49 pair was measured. A smoke hold
per hypothesis brought both arms up and sent only the unscored warm-ups (pair-start guard
set past the hold), so no pair ran. In the H49 smoke both servers were ready 421 s into the hold, and B1's
warm-up took 362 s (its first turn hit the 120 s proxy timeout twice before the client's
retry answered), so an H49 hold of 40 min leaves room for about 20 min of pairs. Runner: `bench/eatflow/live.sh` with `LIVE_MODE=pairs`,
`agentbench pairs` and `agentbench pairs-report` (`bench/eatflow/pairs.py`; regression
tests in `bench/eatflow/test_pairs.py`, the `ovms2` stage mode in `bench/apb/test_stage.py`).
Where it fills a gap in the design above or departs from it:

1. Holds. H47 holds last at most 25 min and H49 holds at most 40 min, with the lock released
   between holds (the 20-min cap of amendment 19 was for one-arm holds). Both servers must
   be ready with at least 900 s of the hold left, or the hold is NOT DELIVERABLE and runs no
   pair. The pairs deadline is the hold start plus its length minus 120 s for teardown; the
   600 s pair-start guard applies to that deadline. H47: at most 2 holds. H49: holds until
   24 pairs or 12 holds. The cluster is `k3dgpu-e-h47` or `k3dgpu-e-h49`, and the host
   containers are prefixed `k3dgpu-e-`.
2. Reset check. After a reset the seed must read back (amendment 15); the warm-up prompt is
   not sent again. A B1 warm-up takes minutes at typical load (its H42 turns took 69-329 s),
   which does not fit the 120 s the pair guard allows for resets. The pinned Agent Framework
   reconnects a lost MCP session and retries the call once (`agent_framework/_mcp.py`,
   `ClosedResourceError` and session-terminated handling). An attempt whose agent cannot be
   reached is void.
3. Warm-up. One unscored prompt per arm per hold, with the harness task timeout (600 s), after
   the background load starts. A failed warm-up stops the hold before any pair.
4. Budgets and outcome order. The task cap is a 240 s timeout on the DevUI request (DevUI
   answers once, when the task is done); the turn budget is the proxy's 120 s timeout. An
   attempt with any turn over 120 s (a proxy timeout error, or `e2e_s` > 120) is
   `over_turn`, even if the cap was hit later; otherwise one that reached the cap is
   `over_task`; otherwise it is graded. A turn that errored counts as +inf in the p95.
5. Abandoned work. After `over_task` the arm's agent is restarted, since it may still be
   working on the abandoned task. After `over_turn` or `over_task` the runner waits, up to
   300 s, until that arm's pod reads at most 0.5 CPU over 1 s, and records the wait
   (`quiesce.jsonl`). This stops an abandoned decode from running into the next attempt.
6. A1's draft. OVMS 2026.4.1 `--pull` puts the draft in `<target>/OpenVINO-Qwen3-0.6B-int4-ov`.
   The pinned draft (`f864c610`; `openvino_model.bin` sha256 `28613e99...`, byte-identical to a
   clone of that commit) is staged there without `.git`, so the pull serves it offline.
7. H47's pod. `stage.py ovms2` keeps the `agent manifest` OVMS Deployment (image, P-cores,
   resources) and serves two graph names from `bench/eatflow/h47/`: `graph-a1.pbtxt` and
   `graph-a4.pbtxt` are the graphs OVMS 2026.4.1 `--pull` writes for this target with
   `--tool_parser=hermes3`, without and with `--draft_source_model=OpenVINO/Qwen3-0.6B-int4-ov
   --draft_device=CPU`. They differ only in the draft lines. The model paths are made
   absolute. A1 is `qwen3-4b-instruct-2507` and A4 is `qwen3-4b-instruct-2507-a4`.
8. Idle check. H49: the other arm's pod CPU over each attempt window. H47 (one pod): a 3 s
   sample of the pod with no request in flight, right before each attempt.
9. Two agents on one host. DevUI's port is fixed at 8090 (`main.py`), so arm A's agent binds
   127.0.0.1:8090 and arm B's binds 127.0.0.2:8090. Each agent's `OPENAI_BASE_URL` is its own
   proxy, and each proxy forwards to that arm's LiteLLM.
10. Void. An unreachable agent, an end-state read failure, a failed reset (the hold then
    stops), a pair missing an attempt, or a pair that ends past the deadline.
11. Derived confidence. The share of 2000 paired bootstrap resamples (seed 40) on the
    SUPPORTED side: H49, A1 - B1 >= +0.30; H47, gain >= 10% (the share with gain < 0 is also
    reported).

### H47 and H49 amendment P2: H49 hold 1 is void (2026-10-18)

Recorded 2026-10-18 at 09:45 PDT, after H49 hold 1 and before any other H49 hold. H49 hold
1 (09:11-09:33 PDT, runner `bca4a51`) is void because of a fault in the harness. B1's
first attempt reached the 240 s cap, so the runner restarted B1's agent (amendment P1,
item 5). The restarted DevUI serves its entity under a new id, but the runner kept the old
one. Every later B1 attempt (pairs 1-15) then drew HTTP 404 within 2 ms and was scored
`fail_graded`. The hold was stopped by hand at pair 15.

- Fix: after a restart the runner looks the entity id up again. An HTTP 404 from DevUI is
  void, like an unreachable agent: a harness fault, not the arm's answer. Regression tests:
  `test_restart_after_cap_resolves_the_new_entity` (fails without the fix) and
  `test_http_404_from_the_agent_is_void`.
- H49 starts again at pair 0 in a new pairs directory, at the fixed commit. Hold 1's records
  are kept beside the run as void and do not enter any statistic, as with H48 run 1
  (amendment 22). Hold 1 counts toward the 12-hold limit.
- Disclosed: hold 1's one valid pair was pair 0 (B-T1-U1001). A1 passed in 13.1 s; B1 hit
  the 240 s cap, with one 60.8 s turn recorded before it. The design, statistic, thresholds
  and prediction do not change.
- H47 is unaffected. None of its 48 attempts reached the cap, so no agent was restarted.

### H47 run (i9-13900T, 2026-10-18)

**Reproduce:**

```text
# provenance
date:            2026-10-18T15:52:07Z (hold 1) and 16:05:31Z (hold 2); pairs from 15:53:58Z
hypothesis:      H47
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores, 125 GiB RAM)
host kernel:     6.6.128-1-MANJARO
accelerators:    k3d-gpu up: nvidia=2 intel=8 hybrid=1 (neither arm uses a GPU)
k3d-gpu commit:  bca4a51 (#131, the runner and amendment P1)
node image:      cryptoandcoffee/k3d-gpu@sha256:e367c7fa6a466b57bc135c16d984bd28b87006d8aad96fcf3aa13ecadd4c1b29
model:           k3dgpu/Qwen3-4B-Instruct-2507-int4-ov (openvino_model.bin sha256 5c1e36e4...); A4 draft
                 OpenVINO/Qwen3-0.6B-int4-ov @ f864c610 (openvino_model.bin sha256 28613e99...)
runtime:         openvino/model_server@sha256:a928903f... (2026.4.1), one pod on the P-core balloon,
                 graphs bench/eatflow/h47/ (A1 no draft, A4 draft_device CPU), tool_parser hermes3;
                 LiteLLM v1.83.14-stable.patch.2@sha256:73cbb215... per arm; blueprint 758d484
energy source:   RAPL package (/sys/class/powercap/intel-rapl:0, read through K3D_GPU_RAPL_EXEC)
runs per arm:    24 attempts (24 pairs, 2 holds: 18 + 6)
```

Commands: `scratchpad hold.sh` = `flock -w 14400 live.lock` then `LIVE_MODE=pairs LIVE_CAP=1500
LIVE_HOLD=<n> LIVE_PAIRS_DIR=<dir> timeout -k 30 1530 bench/eatflow/live.sh H47 <out>`, then
`k3d-gpu bench agent pairs-report <dir> --hyp H47 --arm-a A1 --arm-b A4`. Records (attempts with
their load windows, pairs, turns, warm-ups, `analysis.json`, the hold logs and pins):
[bench/eatflow/results/2026-10-18/H47/](bench/eatflow/results/2026-10-18/H47/).

Typical background load on the E-cores (16-31) for each whole hold. Both arms were ready 82 s
into each hold. Hold 1 ran pairs 0-17 and stopped at the 600 s guard; hold 2 ran pairs 18-23.
No attempt reached the turn budget or the task cap, no pair was void, and nothing was
censored. The OVMS pod read 0.0006 CPU (median) with no request in flight, so the idle check
passes.

Per pair (seconds per attempt, graded outcome, background CPU of each attempt's window in
CPU-s/s; matched = the two attempts' `bg_cpu` within 0.8):

| j | Task | A1 s | A1 | A4 s | A4 | bg_cpu A1 / A4 | Matched |
|---|---|---|---|---|---|---|---|
| 0 | B-T1-U1001 | 17.5 | pass | 14.0 | pass | 10.93 / 11.37 | yes |
| 1 | B-T2-U1001 | 7.1 | pass | 4.6 | pass | 10.26 / 10.09 | yes |
| 2 | B-T3-U1001 | 35.0 | pass | 25.2 | pass | 11.81 / 10.62 | no |
| 3 | B-T4-U1001 | 7.8 | pass | 5.8 | pass | 11.09 / 10.11 | no |
| 4 | B-T5-U1001 | 17.6 | pass | 14.5 | pass | 11.06 / 10.15 | no |
| 5 | B-T6-U1001 | 24.6 | pass | 18.5 | pass | 11.54 / 11.58 | yes |
| 6 | B-T7-U1001 | 18.7 | pass | 14.7 | pass | 11.17 / 11.21 | yes |
| 7 | B-T8-U1001 | 13.2 | pass | 12.9 | pass | 11.27 / 10.75 | yes |
| 8 | B-T1-U1002 | 21.7 | pass | 18.3 | pass | 11.37 / 11.67 | yes |
| 9 | B-T2-U1002 | 7.2 | pass | 4.9 | pass | 10.83 / 11.08 | yes |
| 10 | B-T3-U1002 | 24.8 | pass | 16.9 | pass | 11.86 / 10.15 | no |
| 11 | B-T4-U1002 | 11.9 | pass | 8.1 | pass | 12.19 / 11.64 | yes |
| 12 | B-T5-U1002 | 9.4 | fail | 9.9 | fail | 11.27 / 9.79 | no |
| 13 | B-T6-U1002 | 23.4 | pass | 17.3 | pass | 11.84 / 10.67 | no |
| 14 | B-T7-U1002 | 17.8 | pass | 14.2 | pass | 10.57 / 10.76 | yes |
| 15 | B-T8-U1002 | 11.4 | pass | 10.9 | pass | 10.55 / 12.27 | no |
| 16 | B-T1-U1003 | 13.2 | pass | 13.3 | pass | 10.69 / 10.74 | yes |
| 17 | B-T2-U1003 | 7.3 | pass | 5.1 | pass | 10.66 / 11.77 | no |
| 18 | B-T3-U1003 | 17.5 | pass | 12.1 | pass | 11.09 / 9.22 | no |
| 19 | B-T4-U1003 | 7.5 | fail | 7.6 | fail | 10.28 / 10.36 | yes |
| 20 | B-T5-U1003 | 8.4 | fail | 8.9 | fail | 10.85 / 9.60 | no |
| 21 | B-T6-U1003 | 21.7 | pass | 15.7 | pass | 10.40 / 9.88 | yes |
| 22 | B-T7-U1003 | 12.4 | fail | 9.8 | fail | 9.74 / 9.93 | yes |
| 23 | B-T8-U1003 | 14.1 | pass | 12.4 | pass | 11.56 / 10.07 | no |

Load rule L1: **13 of 24 pairs matched**. The unmatched pairs' `bg_cpu` gaps run 0.91-1.87
CPU-s/s on a background of about 11 CPU-s/s (the production tenants plus the typical load),
over windows of 5-35 s. At the arm level, over the matched pairs, mean `bg_cpu` is 10.86 vs
10.85 (spread 0.01, limit 0.8) and mean swap used is 136192 vs 136248 MiB (spread 56, limit
256). Both pass. Recorded per arm and not gated (A1 / A4, matched pairs): load1 17.6 / 17.9,
package 98.6 / 103.4 W, throttle 222 / 379 per min, E-core busy 62.8 / 62.6%, swap growth
-196 / +87 MiB/min.

On the 13 matched pairs (all 13 completed on both arms):

- Paired time ratio A4/A1 0.788 (95% CI 0.725-0.842), so the **gain is 21.2% (CI 15.8-27.5%)**.
- Delta success A4 - A1 is 0 points (CI 0..0). Within-budget success is 11/13 on both arms
  (0.85, Wilson 0.58-0.96).
- Turn p95 is 18.8 s for A1 and 14.7 s for A4. J per completed task (gross package) is 1478 J
  for A1 and 1245 J for A4. Paired J per attempt A4/A1 is 0.816 (CI 0.770-0.882).

**Verdict: INCONCLUSIVE.** The load gate fails: 13 matched pairs is under 16, and 13/24 is
under 75%. The effect itself is not in doubt. The gain's CI lower bound (15.8%) clears the
10% bar, delta success is 0, and a CI upper bound under 0 (REFUTED) is far away. Derived
confidence: 0.98 of the paired bootstrap resamples on the matched pairs have a gain of 10% or
more, and 0.003 have a gain under 0. This confidence measures the effect, not the verdict,
because the verdict is held by the load gate. Not registered, a sensitivity check only: on
all 24 pairs the ratio is 0.786 (CI 0.721-0.842), and A4 was faster in 20 of 24.

Guardrail: each pair's two attempts had the same graded outcome in 24 of 24, the same
answer text in 15 of 24, and 49 LLM requests per arm. Greedy speculative decoding should not
change the target's output, so the 9 differing answers are a difference in wording, not
in outcome. Failures, the same on both arms: B-T5 of U1002 and U1003 (no balance call),
B-T4-U1003 (no no-loan answer), B-T7-U1003 (no loan read). The prediction (a 0-15% gain,
SUPPORTED at 0.35) underestimated the gain. Tool-call turns are short, but A4's median turn
was 3.6 s against A1's 5.1 s.

### H49 run (i9-13900T, 2026-10-18)

**Reproduce:**

```text
# provenance
date:            2026-10-18T16:55:00Z to 21:11:39Z (holds 2-8); hold 1 (16:11Z) is void (amendment P2)
hypothesis:      H49
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores, 125 GiB RAM)
host kernel:     6.6.128-1-MANJARO
accelerators:    k3d-gpu up: nvidia=2 intel=8 hybrid=1 (neither arm uses a GPU)
k3d-gpu commit:  819680a (#134, hold 2) and 4c314ff (holds 3-8); the runner (bench/eatflow,
                 bench/apb/stage.py, bench/load/load.py) is identical at both
node image:      cryptoandcoffee/k3d-gpu@sha256:e367c7fa6a466b57bc135c16d984bd28b87006d8aad96fcf3aa13ecadd4c1b29
model:           A1 k3dgpu/Qwen3-4B-Instruct-2507-int4-ov (openvino_model.bin sha256 5c1e36e4...)
                 with draft OpenVINO/Qwen3-0.6B-int4-ov @ f864c610 (sha256 28613e99...);
                 B1 Qwen/Qwen3-4B-Instruct-2507 @ cdbee75f, bf16
runtime:         A1 openvino/model_server@sha256:a928903f... (2026.4.1), `agent manifest` with
                 K3D_GPU_AGENT_DRAFT=CPU, P-core balloon, tool_parser hermes3;
                 B1 vllm/vllm-openai-cpu:v0.24.0@sha256:b0e6f860..., EAT xeon-values.yaml, 6 GiB KV,
                 max_model_len 8192, no pinning; LiteLLM v1.83.14-stable.patch.2@sha256:73cbb215...
                 per arm; blueprint 758d484
energy source:   RAPL package (/sys/class/powercap/intel-rapl:0, read through K3D_GPU_RAPL_EXEC)
runs per arm:    24 attempts (24 pairs over 7 valid holds: 3, 3, 3, 4, 3, 5, 3 pairs)
```

Commands: `scratchpad h49loop.sh` runs one hold at a time, each `flock -w 14400 live.lock`
then `LIVE_MODE=pairs LIVE_CAP=2400 LIVE_HOLD=<n> LIVE_PAIRS_DIR=<dir> timeout -k 30 2430
bench/eatflow/live.sh H49 <out>`, until 24 pairs are recorded. Then
`k3d-gpu bench agent pairs-report <dir> --hyp H49 --arm-a A1 --arm-b B1`. Records:
[bench/eatflow/results/2026-10-18/H49/](bench/eatflow/results/2026-10-18/H49/) (void hold 1:
[H49void/](bench/eatflow/results/2026-10-18/H49void/)).

Each hold: both servers ready 309-347 s in. The B1 warm-up took 324-341 s, the A1 warm-up
20-27 s. Pairs ran until the 600 s guard (hold 8 until the last pair), 3-5 pairs per hold, 8 holds in all counting the void
one (the limit is 12). No pair was void. Typical background load on the E-cores for each whole
hold.

Per pair (seconds per attempt; outcome: pass = `pass_budget`, fail = `fail_graded`, over
turn = a turn over 120 s, over cap = the 240 s task cap; background CPU of each attempt's
window in CPU-s/s; matched = within 0.8):

| j | Task | A1 s | A1 | B1 s | B1 | bg_cpu A1 / B1 | Matched |
|---|---|---|---|---|---|---|---|
| 0 | B-T1-U1001 | 16.2 | pass | 240.1 | over cap | 12.04 / 10.49 | no |
| 1 | B-T2-U1001 | 4.6 | pass | 153.0 | pass | 10.66 / 8.10 | no |
| 2 | B-T3-U1001 | 23.4 | pass | 240.1 | over cap | 9.55 / 9.23 | yes |
| 3 | B-T4-U1001 | 6.7 | pass | 198.8 | over turn | 12.52 / 14.71 | no |
| 4 | B-T5-U1001 | 16.5 | pass | 240.1 | over cap | 14.20 / 9.90 | no |
| 5 | B-T6-U1001 | 15.8 | pass | 240.1 | over turn | 9.32 / 10.70 | no |
| 6 | B-T7-U1001 | 13.6 | pass | 240.1 | over cap | 10.36 / 8.03 | no |
| 7 | B-T8-U1001 | 12.5 | pass | 240.1 | over turn | 11.38 / 8.01 | no |
| 8 | B-T1-U1002 | 16.1 | pass | 240.1 | over cap | 9.43 / 8.10 | no |
| 9 | B-T2-U1002 | 4.3 | pass | 145.5 | pass | 8.92 / 7.95 | no |
| 10 | B-T3-U1002 | 15.3 | pass | 240.1 | over cap | 9.36 / 8.60 | yes |
| 11 | B-T4-U1002 | 6.7 | pass | 228.6 | over turn | 9.58 / 8.04 | no |
| 12 | B-T5-U1002 | 8.8 | fail | 190.4 | over turn | 9.43 / 8.27 | no |
| 13 | B-T6-U1002 | 15.9 | pass | 240.1 | over turn | 10.04 / 8.14 | no |
| 14 | B-T7-U1002 | 12.7 | pass | 240.1 | over cap | 10.47 / 7.98 | no |
| 15 | B-T8-U1002 | 9.1 | pass | 240.1 | over turn | 9.21 / 8.02 | no |
| 16 | B-T1-U1003 | 11.5 | pass | 240.1 | over cap | 10.60 / 8.00 | no |
| 17 | B-T2-U1003 | 4.7 | pass | 153.9 | pass | 11.97 / 7.89 | no |
| 18 | B-T3-U1003 | 12.8 | pass | 240.1 | over cap | 11.47 / 8.07 | no |
| 19 | B-T4-U1003 | 7.8 | fail | 131.9 | pass | 10.92 / 8.08 | no |
| 20 | B-T5-U1003 | 9.2 | fail | 192.1 | over turn | 10.92 / 8.14 | no |
| 21 | B-T6-U1003 | 15.8 | pass | 240.1 | over turn | 9.72 / 8.04 | no |
| 22 | B-T7-U1003 | 9.9 | fail | 220.2 | fail | 10.50 / 8.16 | no |
| 23 | B-T8-U1003 | 12.4 | pass | 240.1 | over turn | 9.78 / 8.06 | no |

Load rule L1: **2 of 24 pairs matched** (B-T3-U1001, B-T3-U1002). The arm-level check also
fails on those 2 pairs: mean swap used differs by 1637 MiB (limit 256). The idle-arm check
passes: the idle arm's pod read 0.0007 CPU (median), and B1's pod read at most 0.22 CPU during
any A1 attempt. B1 had 19 over-budget attempts. After 18 of them its pod idled within 300 s, and the
median wait over all 19 was 99 s. One wait ran the full 300 s.

Why the pairs do not match: in 22 of 24 pairs `bg_cpu` is lower in B1's window, by 0.3-4.3
CPU-s/s. B1's vLLM is not pinned and ran at 7.3-8.4 CPUs, so E-core busy rose to 89-92% in
its windows, against 54-78% in A1's. The typical load is duty-cycled stress-ng on those same
E-cores, and B1 takes CPU time from it, so the background CPU the rule subtracts depends on
which arm is running. The pre-registration chose `bg_cpu` so that the arm's own work would not
enter the metric, but the arm still moves the metric by displacing the load. With an unpinned
arm on the load CPUs, L1 cannot match pairs. This is recorded as a limit of the design for
the next pre-registration, not used to re-grade this one.

Recorded per arm and not gated (A1 / B1, all 24 pairs): `bg_cpu` 10.52 / 8.70, load1 24.6 /
25.6, package 103.9 / 98.0 W, throttle 243 / 5901 per min, E-core busy 61.4 / 90.0%, swap used
154171 / 154301 MiB, swap growth -35 / +30 MiB/min.

**Verdict: INCONCLUSIVE.** Three gates fail: 2 matched pairs is under 16, 2/24 is under 75%,
and the arm-level swap check fails. On the 2 matched pairs, A1 passed both and B1 hit the cap
on both. That is too few pairs for a CI, so no confidence is derived from them.

Not registered, a description of all 24 pairs, which the gate excludes:

- Within-budget success: A1 20/24 (0.83, Wilson 0.64-0.93) and B1 4/24 (0.17, Wilson
  0.07-0.36). The paired difference is +0.67 (95% CI +0.42 to +0.88). 0.997 of the paired
  bootstrap resamples are at +0.30 or more.
- B1's outcomes: 4 pass, 1 graded fail, 10 over the turn budget, 9 over the task cap. Its 4
  passes are 3 B-T2 single-balance tasks (145-154 s) and B-T4-U1003 (132 s).
- p95 turn: A1 14.3 s, B1 +inf (10 of 34 recorded B1 turns timed out or errored). Median attempt: A1
  12.4 s, B1 240.1 s.
- J per completed task: A1 1227 J, B1 12888-21445 J (5 tasks). The paired J per attempt ratio
  A1/B1 is 0.055 (CI 0.048-0.069). B1's attempts stop at the cap, so this ratio is an upper
  bound.

The prediction (A1 >= 0.70, B1 <= 0.10, difference >= +60 points) is close on these
descriptive numbers, but none of them is a verdict.

### H47b and H49b pre-registration: load rule L2, pairs matched on pre-windows (2026-10-18)

Written 2026-10-18T14:35-07:00, before any H47b or H49b measurement and with the harness
change in the same PR. Nothing here is a measurement. H47 and H49 keep their verdicts and the
rule (L1) that scored them; they are not re-graded. H47b and H49b are new ledger rows.

**Why.** Rule L1 matched pairs on `bg_cpu` measured *during* each attempt. In the
[H49 run](#h49-run-i9-13900t-2026-10-18) that metric moved with the arm: "in 22 of 24 pairs
`bg_cpu` is lower in B1's window, by 0.3-4.3 CPU-s/s. B1's vLLM is not pinned and ran at
7.3-8.4 CPUs, so E-core busy rose to 89-92% in its windows, against 54-78% in A1's. [...] B1
takes CPU time from it, so the background CPU the rule subtracts depends on which arm is
running." L1 matched 2 of 24 pairs. The run section records this "as a limit of the design for
the next pre-registration". Re-read from the committed
[analysis](bench/eatflow/results/2026-10-18/H49/pairs/analysis.json): `bg_cpu` A1 minus B1
is positive in 22 of 24 pairs, 0.32-4.31 CPU-s/s; the two other pairs are -1.38 and -2.19.
This is the case [amendment 27](#h40-h48-pre-registration-agent-benchmark-2026-10-17) names:
a variable the treatment moves is post-treatment, and matching on it can void a real effect.
B1 is unpinned and competes for the load CPUs, so during-attempt background CPU is
post-treatment. In H47 both arms share one P-core pod, yet L1 still matched only 13 of 24
pairs ([H47 run](#h47-run-i9-13900t-2026-10-18)), with gaps of 0.91-1.87 CPU-s/s over
5-35 s windows. A pair's background is a covariate only when read with the arms idle.

**What is unchanged.** Everything in the
[H47 and H49 pre-registration](#h47-and-h49-pre-registration-within-hold-interleaved-design-2026-10-18)
and amendments [P1](#h47-and-h49-amendment-p1-the-runner-2026-10-18) and
[P2](#h47-and-h49-amendment-p2-h49-hold-1-is-void-2026-10-18) except the pair-matching rule:

- Arms: H49b A1 vs B1 and H47b A1 vs A4, as in H49 and H47 (same images, models, draft,
  pinning, one cluster, one hold, both arms live, per-arm LiteLLM, proxy and agent).
- Tasks: B24, N=1, typical load on the E-cores, 24 pairs in A B B A order, resets to seed.
- Budgets: 120 s turn, 240 s task cap, abandoned-work restart and quiesce (P1 items 4-5).
- Holds: H49b holds of at most 40 min until 24 pairs or 12 holds; H47b holds of at most 25
  min, at most 2. The 900 s readiness rule and the 600 s pair-start guard stay.
- Voids: as in H49 and P1 item 10. More than 2 void pairs makes H49b INCONCLUSIVE.
- Outcome classes: `pass_budget`, `fail_graded`, `over_turn`, `over_task`, `void`.
- Idle-arm check: H49b, the other arm's pod CPU over each attempt; H47b, a 3 s sample of the
  shared pod before each attempt. A median above 0.5 CPU is INCONCLUSIVE.
- Statistics, verdict rules, thresholds, predictions and priors: those of H49 and H47.
  INCONCLUSIVE when fewer than 16 pairs are matched, fewer than 75% of the pairs run are
  matched, or the arm-level check fails.
- Pairs directories: new, `bench/eatflow/results/<date>/H49b/` and `.../H47b/`; clusters
  `k3dgpu-e-h49b` and `k3dgpu-e-h47b`.

**Load rule L2 (new runs only; H47b, H49b and later interleaved designs that cite it).**

1. Baseline. Once per hold, after the background load starts and before the warm-ups, with
   both arms idle: two 10 s `load.py` windows, `--phase baseline`
   (`<pairs dir>/baseline-hold<n>.jsonl`). This is the level reference of amendment 27 (c).
2. Before every attempt, with both arms idle (no request in flight; after any quiesce):
   `load.py settle` against that baseline (5 s windows until load-CPU `busy_pct` and
   `throttle_per_min` read within `TOLERANCE` of it, at most 60 s), then one 10 s
   pre-window over the load CPUs (all 16 E-cores), `--phase pre`. It records `busy_pct`,
   `throttle_per_min`, `swap_used_mib`, `swap_delta_mib`, the load containers running
   (`load_running` of `load_expected`), `settled` and `settle_s`, and both arm pods' CPU over
   the window. 10 s, not 5 s: one throttle event then reads 6/min, under the 10/min absolute
   tolerance; at 5 s a single event (12/min) would unmatch a quiet pair.
3. Per pair: matched when both pre-windows show the level's full load and agree on each
   amendment-27 pre covariate (`busy_pct` 5 pp, `throttle_per_min` max(10, 0.25 x the larger),
   `swap_used_mib` 256 MiB, `swap_delta_mib` 64 MiB; `load.py` `TOLERANCE`, unchanged).
   A missing pre-window, a missing value or fewer load containers than the level runs makes
   the pair unmatched (not void). Unmatched pairs are dropped from every statistic and
   counted by the first covariate that failed (`unmatched_by`).
4. Arm level: `load.py matched_a27` over the matched pairs' pre-windows only, each tagged
   with its arm: per-arm means of the four pre covariates within `TOLERANCE`.
5. Outcomes, reported per arm and never gated: `bg_cpu`, package W, throttle/min, E-core
   busy, `load1` and swap growth during each attempt, on the matched pairs and on every pair
   run (`load_during_per_arm`, `load_during_all_pairs_per_arm`); the pre-window means per arm
   (`load_pre_per_arm`); settle times and unsettled gaps.
6. Void counts stay explicit (`void_pairs`) and are separate from unmatched pairs.

Implementation: `bench/eatflow/pairs.py` (`l2`, `pre_mismatch`, `analyze` picks L2 for a
hypothesis id ending in `b`), `bench/eatflow/agentbench.py` (`pairs --hyp H47b|H49b
--baseline --level --pre-s`, `pre_window`; `pairs-report --hyp H47b|H49b`) and
`bench/eatflow/live.sh` (ARM `H47b` or `H49b` in `LIVE_MODE=pairs`: the baseline windows,
then the same hold). Regression tests in `bench/eatflow/test_pairs.py` (`L2Test`):
an unpinned arm that lowers during-attempt `bg_cpu` by 0.3-4.2 CPU-s/s in every pair, as B1
did, still matches all 24 pairs on its pre-windows (L1 keeps 3 of the same 24); background
drift between a pair's pre-windows in `busy_pct`, `throttle_per_min`, `swap_used_mib` or
`swap_delta_mib` does not match; a missing pre-window or missing load does not match; the
arm-level check reads pre-windows only; the 16-pair, 75% and void gates are unchanged; a
hold takes a pre-window before each attempt, in A B B A order, ending before the send.

**Disclosed before running.**

- Null estimate. No idle pre-window of this design exists in the repo. The closest
  committed data are the nine one-arm load files of 2026-10-17 used in the H47/H49
  pre-registration (30 s windows, taken while that arm worked). Over their 196 adjacent window
  pairs, the four L2 covariates all agree in 37 (19%); `throttle_per_min` fails 141 of them,
  and without it 135 of 196 (69%) agree. Those windows include the arm's own work (median
  throttle 1565/min), which an idle pre-window does not, so they overstate pre-window noise.
  If idle pre-window throttle is as noisy, H47b and H49b will be INCONCLUSIVE on the same
  16-pair and 75% gates. The rule and its tolerances are not changed to avoid that.
- Hold capacity. Each attempt now carries at least 15 s of settle and pre-window, up to 70 s.
  H47 ran 18 pairs in hold 1 at about 35 s per pair. At that rate plus 30-140 s per pair, two
  25-min holds may run fewer than 24 pairs. Pairs not run are neither matched nor void; the
  16-pair and 75% gates apply to what ran. H49b's pairs (median B1 attempt 240 s in H49)
  barely change.
- Prediction. Matched pairs: no point prediction (see the null estimate). The effect
  predictions are those of H49 and H47.
- The H49 numbers quoted above were measured under L1 and are not evidence for H49b.

### H47b run (i9-13900T, 2026-10-18)

**Reproduce:**

```text
# provenance
date:            2026-10-18T21:51:49Z (hold 1) and 22:05:59Z (hold 2); pairs from 21:56:03Z
hypothesis:      H47b
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores, 125 GiB RAM)
host kernel:     6.6.128-1-MANJARO
accelerators:    k3d-gpu up: nvidia=2 intel=8 hybrid=1 (neither arm uses a GPU)
k3d-gpu commit:  f31294a (#140, the L2 pre-registration and runner)
node image:      cryptoandcoffee/k3d-gpu@sha256:e367c7fa6a466b57bc135c16d984bd28b87006d8aad96fcf3aa13ecadd4c1b29
model:           k3dgpu/Qwen3-4B-Instruct-2507-int4-ov (openvino_model.bin sha256 5c1e36e4...); A4 draft
                 OpenVINO/Qwen3-0.6B-int4-ov @ f864c610 (openvino_model.bin sha256 28613e99...)
runtime:         openvino/model_server@sha256:a928903f... (2026.4.1), one pod on the P-core balloon,
                 graphs bench/eatflow/h47/ (A1 no draft, A4 draft_device CPU), tool_parser hermes3;
                 LiteLLM v1.83.14-stable.patch.2@sha256:73cbb215... per arm; blueprint 758d484
energy source:   RAPL package (/sys/class/powercap/intel-rapl:0, read through K3D_GPU_RAPL_EXEC)
runs per arm:    10 attempts (10 pairs, 2 holds: 4 + 6)
```

Commands: the scratchpad `hold.sh` runs `flock -w 21600 live.lock` then `LIVE_MODE=pairs LIVE_CAP=1500
LIVE_HOLD=<n> LIVE_PAIRS_DIR=<dir> timeout -k 30 1530 bench/eatflow/live.sh H47b <out>` for
holds 1 and 2, then `k3d-gpu bench agent pairs-report <dir> --hyp H47b --arm-a A1 --arm-b A4`.
Records (attempts with their pre-windows and load windows, the per-hold baselines, pairs, turns,
warm-ups, `analysis.json`, pins):
[bench/eatflow/results/2026-10-18/H47b/](bench/eatflow/results/2026-10-18/H47b/).

Typical background load on the E-cores (16-31) for each whole hold. Both arms were ready 83-86 s
into each hold. Each hold stopped at the 600 s pair-start guard: hold 1 after pairs 0-3, hold 2
after pairs 4-9. The pre-registration's hold-capacity risk came true. Each attempt now carries a
settle gap and a 10 s pre-window, and 5 of 20 settle gaps ran the full 60 s without settling
(median settle 35 s), so two 25-min holds ran 10 pairs, not 24. No attempt reached the turn
budget or the task cap, no pair was void, and nothing was censored. The OVMS pod read 0.0006 CPU
(median, at most 0.0007) with no request in flight, so the idle check passes.

Per pair (seconds per attempt, graded outcome, and each attempt's pre-window over the load CPUs;
matched = the two pre-windows agree under rule L2, otherwise the first covariate that failed):

| j | Task | A1 s | A1 | A4 s | A4 | pre busy % A1 / A4 | pre throttle/min A1 / A4 | Matched |
|---|---|---|---|---|---|---|---|---|
| 0 | B-T1-U1001 | 17.9 | pass | 14.4 | pass | 47.3 / 44.8 | 418 / 358 | yes |
| 1 | B-T2-U1001 | 7.4 | pass | 5.7 | pass | 55.4 / 48.2 | 692 / 1493 | no (busy_pct) |
| 2 | B-T3-U1001 | 34.1 | pass | 25.2 | pass | 50.4 / 52.6 | 358 / 72 | no (throttle_per_min) |
| 3 | B-T4-U1001 | 8.3 | pass | 6.8 | pass | 45.9 / 46.7 | 944 / 382 | no (throttle_per_min) |
| 4 | B-T5-U1001 | 16.7 | pass | 14.4 | pass | 49.1 / 46.2 | 310 / 704 | no (throttle_per_min) |
| 5 | B-T6-U1001 | 23.6 | pass | 17.6 | pass | 44.9 / 49.2 | 537 / 2833 | no (throttle_per_min) |
| 6 | B-T7-U1001 | 17.8 | pass | 14.8 | pass | 47.7 / 46.3 | 1110 / 1003 | yes |
| 7 | B-T8-U1001 | 13.4 | pass | 12.8 | pass | 45.8 / 44.5 | 560 / 598 | yes |
| 8 | B-T1-U1002 | 21.7 | pass | 17.8 | pass | 45.2 / 45.8 | 478 / 610 | yes |
| 9 | B-T2-U1002 | 7.1 | pass | 4.7 | pass | 48.1 / 45.2 | 908 / 215 | no (throttle_per_min) |

Load rule L2: **4 of 10 pairs matched**. The 6 unmatched pairs failed on `throttle_per_min` (5)
and `busy_pct` (1). With both arms idle, pre-window throttle ran from 72 to 2833 events/min and
load-CPU busy from 44.5 to 55.4%; swap used and swap growth matched in every pair. The arm-level
check over the 4 matched pairs passes: pre-window means (A1 / A4) busy 46.5 / 45.4% (limit 5 pp),
throttle 642 / 642 per min, swap used 152505 / 152505 MiB, swap growth -2.6 / -0.3 MiB.
Recorded per arm and never gated (A1 / A4, during the attempt): on the matched pairs `bg_cpu`
11.74 / 10.75 CPU-s/s, package 96.9 / 101.6 W, throttle 7.5 / 19.6 per min, E-core busy 67.2 /
61.9%, load1 12.4 / 14.1, swap growth -4.3 / -9.8 MiB/min; on all 10 pairs `bg_cpu` 11.60 /
10.66, package 97.8 / 103.0 W, throttle 72 / 107 per min, E-core busy 66.6 / 60.8%.

On the 4 matched pairs (all 4 completed on both arms):

- Paired time ratio A4/A1 0.819 (95% CI 0.808-0.950), so the **gain is 18.1% (CI 5.0-19.2%)**.
- Delta success A4 - A1 is 0 points (CI 0..0). Within-budget success is 4/4 on both arms
  (Wilson 0.51-1.00).
- Turn p95 is 19.2 s for A1 and 15.9 s for A4. J per completed task (gross package) is 1723 J
  for A1 and 1526 J for A4. Paired J per attempt A1/A4 is 1.151 (CI 1.000-1.183), so A4 used about 13% less package energy per attempt.

**Verdict: INCONCLUSIVE.** The load gate fails: 4 matched pairs is under 16, and 4/10 is under
75%. The gain's CI lower bound (5.0%) is also under the 10% bar, and its upper bound is far
above 0, so neither SUPPORTED nor REFUTED is reached on the matched pairs. Derived confidence:
0.95 of the paired bootstrap resamples on the 4 matched pairs have a gain of 10% or more, and 0
have a gain under 0. With 4 pairs the resamples take few distinct values, so this measures the
direction of the effect, not the verdict. Not registered, a sensitivity check only: on all 10
pairs the ratio is 0.808 (CI 0.739-0.834), and A4 was faster in 10 of 10.

Guardrail: each pair's two attempts had the same graded outcome in 10 of 10, the same answer
text in 6 of 10, and 21 LLM requests per arm. The prediction (a 0-15% gain, SUPPORTED at 0.35)
again underestimated the gain, as in H47. Under L2 the binding limit is idle pre-window throttle
on this host, the risk the pre-registration's null estimate named; the L2 tolerances are not
changed here.

### H49b run (i9-13900T, 2026-10-18 and 19)

**Reproduce:**

```text
# provenance
date:            2026-10-18T22:19:23Z (hold 1) to 2026-10-19T21:33:50Z (hold 10 released); holds 1-4
                 on 2026-10-18, holds 5-10 on 2026-10-19 after hold 4 was killed (see below)
hypothesis:      H49b
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores, 125 GiB RAM)
host kernel:     6.6.128-1-MANJARO
accelerators:    k3d-gpu up: nvidia=2 intel=8 hybrid=1 (neither arm uses a GPU)
k3d-gpu commit:  f31294a (#140, holds 1-4) and 310902c (holds 5-10: f31294a plus the H47b
                 records and text only; bench/eatflow, bench/load and scripts are identical)
node image:      cryptoandcoffee/k3d-gpu@sha256:e367c7fa6a466b57bc135c16d984bd28b87006d8aad96fcf3aa13ecadd4c1b29
model:           A1 k3dgpu/Qwen3-4B-Instruct-2507-int4-ov (openvino_model.bin sha256 5c1e36e4...)
                 with draft OpenVINO/Qwen3-0.6B-int4-ov @ f864c610 (sha256 28613e99...);
                 B1 Qwen/Qwen3-4B-Instruct-2507 @ cdbee75f, bf16
runtime:         A1 openvino/model_server@sha256:a928903f... (2026.4.1), `agent manifest` with
                 K3D_GPU_AGENT_DRAFT=CPU, P-core balloon, tool_parser hermes3;
                 B1 vllm/vllm-openai-cpu:v0.24.0@sha256:b0e6f860..., EAT xeon-values.yaml, 6 GiB KV,
                 max_model_len 8192, no pinning; LiteLLM v1.83.14-stable.patch.2@sha256:73cbb215...
                 per arm; blueprint 758d484
energy source:   RAPL package (/sys/class/powercap/intel-rapl:0, read through K3D_GPU_RAPL_EXEC)
runs per arm:    24 attempts (24 pairs over 10 holds: 3, 3, 2, 1 (void), 2, 3, 2, 3, 3, 2)
```

Commands: the scratchpad `hold.sh` runs `flock -w 21600 live.lock` then `LIVE_MODE=pairs
LIVE_CAP=2400 LIVE_HOLD=<n> LIVE_PAIRS_DIR=<dir> timeout -k 30 2430 bench/eatflow/live.sh H49b
<out>`, one hold at a time until 24 pairs are recorded (at most 12 holds). Then
`k3d-gpu bench agent pairs-report <dir> --hyp H49b --arm-a A1 --arm-b B1`. Records (attempts
with their pre-windows and load windows, the per-hold baselines, pairs, turns, warm-ups,
quiesce waits, `analysis.json`, pins):
[bench/eatflow/results/2026-10-18/H49b/](bench/eatflow/results/2026-10-18/H49b/).

Each hold: both servers ready 69-90 s in, baseline taken, pairs from 381-451 s in. The B1
warm-up took 343-434 s, the A1 warm-up 21-24 s. Each hold ran pairs until the 600 s pair-start
guard (hold 10 until the last pair). Typical background load on the E-cores (16-31) for each
whole hold, with all 3 load containers running in every pre-window.

Hold 4 was killed from outside at about 17:22 PDT on 2026-10-18, about 16 min before its deadline.
The runner process ended with its parent session, after both attempts of pair 8
(B-T1-U1002: A1 pass 17.8 s, B1 over cap 240.1 s) and before the pair row was written; its
cleanup never ran. `pair_table` already counts such a pair as void. Before hold 5, a void row
was appended for pair 8 with that reason, so the resumed run did not repeat it. Pair 8 is the
**1 void pair** (the limit is 2). Holds 5-10 then ran on 2026-10-19 under the same lock,
runner and pins; 10 of the 12 permitted holds were used.

The idle-arm check passes: the other arm's pod read 0.0008 CPU (median) over the attempts, at
most 0.012. Both pods read at most 0.066 CPU in any pre-window. B1 had 18 over-budget
attempts; the quiesce wait after them had a median of 114 s, and 2 ran the full 300 s.

Per pair (seconds per attempt; outcome: pass = `pass_budget`, fail = `fail_graded`, over turn
= a turn over 120 s, over cap = the 240 s task cap; each attempt's pre-window over the load
CPUs, both arms idle; matched = the two pre-windows agree under rule L2, otherwise the first
covariate that failed; pair 8 is void and not listed):

| j | Task | A1 s | A1 | B1 s | B1 | pre busy % A1 / B1 | pre throttle/min A1 / B1 | Matched |
|---|---|---|---|---|---|---|---|---|
| 0 | B-T1-U1001 | 13.6 | pass | 240.1 | over cap | 50.7 / 49.4 | 1063 / 2149 | no (throttle_per_min) |
| 1 | B-T2-U1001 | 4.5 | pass | 153.9 | pass | 44.4 / 50.4 | 1493 / 442 | no (busy_pct) |
| 2 | B-T3-U1001 | 24.6 | pass | 240.1 | over cap | 43.5 / 49.0 | 920 / 275 | no (busy_pct) |
| 3 | B-T4-U1001 | 6.0 | pass | 163.9 | pass | 49.0 / 50.5 | 155 / 84 | no (throttle_per_min) |
| 4 | B-T5-U1001 | 14.9 | pass | 240.1 | over cap | 46.9 / 45.1 | 275 / 788 | no (throttle_per_min) |
| 5 | B-T6-U1001 | 25.1 | pass | 240.1 | over turn | 98.3 / 54.4 | 12 / 799 | no (busy_pct) |
| 6 | B-T7-U1001 | 15.0 | pass | 240.1 | over cap | 50.8 / 51.1 | 406 / 179 | no (throttle_per_min) |
| 7 | B-T8-U1001 | 12.4 | pass | 240.1 | over turn | 49.6 / 57.0 | 3342 / 3345 | no (busy_pct) |
| 9 | B-T2-U1002 | 5.6 | pass | 167.7 | pass | 48.2 / 52.5 | 298 / 501 | no (throttle_per_min) |
| 10 | B-T3-U1002 | 19.6 | pass | 240.1 | over cap | 53.8 / 47.7 | 131 / 191 | no (busy_pct) |
| 11 | B-T4-U1002 | 8.5 | pass | 240.1 | over cap | 48.9 / 48.3 | 311 / 239 | yes |
| 12 | B-T5-U1002 | 10.6 | fail | 219.6 | over turn | 46.0 / 51.1 | 287 / 167 | no (busy_pct) |
| 13 | B-T6-U1002 | 19.4 | pass | 240.1 | over turn | 48.2 / 58.1 | 298 / 60 | no (busy_pct) |
| 14 | B-T7-U1002 | 16.4 | pass | 240.1 | over cap | 49.4 / 49.3 | 358 / 347 | yes |
| 15 | B-T8-U1002 | 12.7 | pass | 240.1 | over turn | 57.9 / 51.6 | 36 / 454 | no (busy_pct) |
| 16 | B-T1-U1003 | 14.9 | pass | 240.1 | over cap | 51.1 / 51.6 | 239 / 227 | no (swap_used_mib) |
| 17 | B-T2-U1003 | 5.5 | pass | 167.2 | pass | 51.6 / 55.2 | 167 / 143 | yes |
| 18 | B-T3-U1003 | 15.7 | pass | 240.1 | over cap | 59.6 / 56.5 | 24 / 36 | no (throttle_per_min) |
| 19 | B-T4-U1003 | 9.8 | fail | 146.4 | pass | 51.6 / 48.2 | 167 / 334 | no (throttle_per_min) |
| 20 | B-T5-U1003 | 11.1 | fail | 216.4 | over turn | 46.0 / 46.6 | 489 / 251 | no (throttle_per_min) |
| 21 | B-T6-U1003 | 21.5 | pass | 240.1 | over turn | 48.0 / 50.7 | 227 / 203 | no (swap_used_mib) |
| 22 | B-T7-U1003 | 13.6 | fail | 240.1 | over turn | 53.4 / 52.1 | 155 / 84 | no (throttle_per_min) |
| 23 | B-T8-U1003 | 16.9 | pass | 240.1 | over turn | 53.6 / 49.2 | 96 / 382 | no (throttle_per_min) |

Load rule L2: **3 of 23 pairs matched** (B-T4-U1002, B-T7-U1002, B-T2-U1003). The 20
unmatched pairs failed on `throttle_per_min` (10), `busy_pct` (8) and `swap_used_mib` (2).
With both arms idle, load-CPU busy ran from 43.5 to 98.3% and throttle from 12 to 4262 per
min across the 48 pre-windows. 13 of 48 settle gaps ran the full 60 s without settling
(median settle 32.5 s). The arm-level check over the 3 matched pairs passes: pre-window means
(A1 / B1) busy 50.0 / 50.9% (limit 5 pp), throttle 279 / 243 per min (limit 70), swap used
159741 / 159738 MiB, swap growth -1.7 / -12.8 MiB.

Recorded per arm and never gated (A1 / B1, during the attempt): on the 3 matched pairs
`bg_cpu` 11.98 / 9.72 CPU-s/s, package 103.1 / 98.8 W, throttle 53 / 2426 per min, E-core busy
66.9 / 89.7%, load1 17.9 / 27.3; on all 23 pairs `bg_cpu` 12.16 / 9.81, package 101.7 /
99.0 W, throttle 65 / 3749 per min, E-core busy 67.5 / 89.9%, load1 19.2 / 26.4, swap growth
-440 / -57 MiB/min. As in H49, B1's unpinned vLLM raises E-core busy in its own windows; the
pre-windows, read with both arms idle, no longer carry that (busy 50.0 / 50.9% on the matched
pairs). They differ instead by idle-time noise in the load itself.

On the 3 matched pairs: within-budget success A1 3/3 (Wilson 0.44-1.00), B1 1/3 (0.06-0.79);
paired difference +0.67 (95% CI 0.00 to +1.00). Turn p95 A1 12.9 s, B1 114.5 s; median attempt
A1 8.5 s, B1 240.1 s. J per completed task A1 1045 J (3 tasks), B1 16437 J (1 task); paired J
per attempt A1/B1 0.038 (CI 0.034-0.070).

**Verdict: INCONCLUSIVE.** The load gate fails: 3 matched pairs is under 16, and 3/23 is under
75%. The void count (1) and the idle-arm and arm-level checks pass. Derived confidence: 0.97
of the paired bootstrap resamples on the 3 matched pairs have A1 - B1 >= +0.30. With 3 pairs
the resamples take only a few distinct values, so this gives the direction of the effect, not
a verdict.

Not registered, a description of all 23 complete pairs, which the gate excludes: within-budget
success A1 19/23 (0.83, Wilson 0.63-0.93) and B1 5/23 (0.22, Wilson 0.10-0.42); paired
difference +0.61 (95% CI +0.35 to +0.83), and 0.99 of the paired bootstrap resamples are at
+0.30 or more. B1's outcomes: 5 pass, 9 over the turn budget, 9 over the task cap. Its 5 passes
are the 3 B-T2 single-balance tasks (154-168 s) and B-T4-U1001 and B-T4-U1003 (146-164 s).
A1 failed 4 graded (B-T5-U1002, B-T4-U1003, B-T5-U1003, B-T7-U1003). Median attempt A1 13.6 s,
B1 240.1 s. These are close to H49's all-pairs description (+0.67, CI +0.42 to +0.88).

Under L2 the binding limit is again idle pre-window noise on this host: throttle events and
load-CPU busy vary between two idle 10 s windows more than amendment 27's tolerances allow, as
the pre-registration's null estimate warned (H47b matched 4 of 10 the same way). The
prediction (A1 >= 0.70, B1 <= 0.10, difference >= +60 points) is close on the descriptive
numbers, except B1's 0.22, but none of them is a verdict. The L2 tolerances are not changed here.

### H43 and H45 amendment W1: the live runner (2026-10-19)

Written 2026-10-19, before any H43 or H45 measurement, with the runner in the same PR. Nothing
here is a measurement. The H43 and H45 rows (hypotheses, predictions, thresholds, verdict rules)
and the [H40-H48 pre-registration](#h40-h48-pre-registration-agent-benchmark-2026-10-17) are
unchanged. Amendment 25 left both unrun: H43 because "the coding agent is not wired into the
live runner", H45 because "four 300 s ramp steps plus bring-up exceed a 20-min hold". This fixes
the choices the rows left open. Three items depart from the rows' probes or from amendment 27,
and each one says so: items 5, 6 and 11.

Common to both: holds as in item 19 (the shared lock, at most 20 min: `LIVE_CAP=1170` inside
`timeout 1200`), a fresh cluster `k3dgpu-a-<arm>` or a host container, deleted after the hold,
also on failure. A hold does not start when `/` is above 97% full. Sampling `agent_local`.
Harness: the merge commit of this PR.

**H43.**

1. Sandbox: `k3d-gpu sandbox up`, as the row names it: agent-sandbox v1.0.7, warm pool
   `python-ecores`, pods labelled `k3d-gpu.io/cores=efficient` (the E-core balloon), 1 CPU each.
2. SDK bridge. The blueprint at `758d484` pins `k8s-agent-sandbox==0.4.6`, which claims a sandbox
   through the v1alpha1 SandboxClaim API with `create_sandbox(template=...)`. agent-sandbox
   v1.0.7 serves only v1beta1, and its SandboxClaim requires `warmPoolRef`, so the pinned agent
   cannot claim a sandbox from `sandbox up`. The agent runs with `k8s-agent-sandbox==1.0.7` (the
   SDK released for that controller) and `bench/eatflow/coding/sitecustomize.py`, which maps the
   agent's `create_sandbox(template=T, namespace=N, warmpool=W)` to 1.0.7's
   `create_sandbox(W or T, namespace=N)`. `agent.py` and `main.py` are unchanged. The other
   packages are the banking venv's (the blueprint's pins) plus `kubernetes==35.0.0`, as the
   coding blueprint pins it. `ray` is not installed: the agent imports it only with
   `RAY_ENABLED=true`. The whole set is in `bench/eatflow/coding-constraints.txt`, each package
   with its sha256, installed with `--require-hashes`. `agent.lock` holds the SDK wheel's sha256
   (`suites.coding_agent`) and the Redis image (`images.redis`). Agent environment:
   `SANDBOX_NAMESPACE=agent-sandbox`, `SANDBOX_TEMPLATE=python-ecores`,
   `SANDBOX_WARMPOOL=python-ecores`.
3. Router. The agent reaches its sandbox through upstream's sandbox-router, which `sandbox up`
   does not install (EAT pulls it by a floating staging tag). `bench/eatflow/sbrouter.py` is
   that router's HTTP path in the Python standard library: it forwards to `X-Sandbox-Pod-IP`, or
   else to `<X-Sandbox-ID>.<X-Sandbox-Namespace>.svc.cluster.local:<X-Sandbox-Port>`, and drops the
   router headers. It runs in the cluster (namespace `agent-sandbox-system`, label
   `app=sandbox-router`, which the sandbox NetworkPolicy admits; the pinned `proxy_python` image;
   a bearer token per hold) and is port-forwarded to the host agent. No placement label.
4. Memory. The coding agent always attaches a RedisContextProvider; it has no `ENABLE_MEMORY`
   switch. Amendment 8's aim (no earlier task re-injected) is kept by purging. Redis 8.10.2 runs
   on the host. Before the warm-up and before every task, every key is deleted and `DBSIZE` must
   read 0 (`run --pre-task`, outside the task's time). A failed purge aborts the block. The search
   index is not a key and stays. Before each block the agent process is restarted and its sandbox
   claims are deleted. A task censored at a block's end would otherwise keep running in the old
   process, write memory after the next purge and load the next block. Amendment 8's abort on a
   request with more than one user message still applies.
5. Arms. The row's statistic is A1's, so A1 decides the verdict. A3 runs as context.
   **Deviation from the probe**, as amendment 24 made for H44: B1 is not run. Its B24 turns
   took 69-329 s, over the 120 s turn timeout, and after its 343-434 s warm-up no C12 block fits
   a hold. B1 does not enter the verdict.
6. Blocks. **A reinterpretation of the probe's "A B ... B A halves per level"**: amendment 19 puts
   one arm in a hold, so the halves run over levels within the arm, as in H44. A1 gets one hold,
   with blocks typical, heavy, heavy, typical. Each level's two halves share one results
   directory, so the second half resumes C12's order where the first stopped; a censored task
   runs again. Per block: the agent restart (item 4), the level's load started (its TTL covers
   the hold), a 30 s gap, one warm-up prompt, then the window. Budget per block:
   (`CAP` - elapsed - 60)/4 - 110 s, at most 300, at least 120. A3's vLLM CUDA server answered
   419-521 s into its 2026-10-17 and 18 holds, so four blocks do not fit one hold. A3 gets two
   holds: typical then heavy, then heavy then typical, each block (`CAP` - elapsed - 60)/2 - 110 s.
   No load-matching gate: the comparison is A1 against itself across levels. Load is sampled with
   amendment 27's phase and container count. A block that exits non-zero (an abort) ends the hold.
   Rows written before an abort stand, as C1's did in H42.
7. Statistic. **An extension of amendment 9 to H43**: amendment 9 names H42 (2), H46 and H47, and
   H47's "median time per task" was scored this way. The ratio is the paired time ratio
   heavy/typical over the C12 ids A1 completed at both levels, exp(median of per-id log ratios),
   with a paired bootstrap CI (2000 resamples, seed 40). The ratio of the two medians on the same
   ids is reported beside it and does not decide. Completed means not censored by the block
   budget. A task that ends in an error, or over the 600 s timeout, is completed and failed, and
   enters the ratio at +inf time, as errors do in the turn-latency rule. An id that is +inf at
   both levels has no ratio and counts for success only. Rows dropped before scoring: a row whose
   requests carried more than one user message (memory on), and a row that overlaps a load window
   with fewer load containers than its level runs (amendment 27 (d), applied per row). Delta
   success is heavy minus typical on the same ids. `k3d-gpu bench agent stretch DIRS --arm A1`
   applies the row's rule. With no shared completed id the verdict is INCONCLUSIVE. The ids
   censored at one level but completed at the other are listed: they leave the ratio, and slow
   heavy tasks are the likeliest to be censored.

**H45.**

8. Ramp across holds. Bring-up sets the steps per hold. A1, C1 and C2 (servers answering 18-89 s
   into their 2026-10-17 holds) run N=1, 2 in a first hold and N=4, 8 in a second. A3 runs one
   step per hold. A hold runs only when every earlier step passed. Later steps gate success on
   the N=1 success from the arm's first hold (`apb --base-from`, which refuses a base hold with
   a failed step or at another level). A later hold runs on a freshly started server. Order: A1,
   A3, C1, C2, each arm's holds back to back. At most 10 holds.
9. Not deliverable. A hold exits without measuring when its server does not answer with its steps
   plus 300 s still left, or when less than its steps plus 150 s is left once the pre-window is
   taken. That arm's later steps are not run (no retry hold), and its APB is the bracket reached
   so far.
10. Scoring. `k3d-gpu bench agent apb-score` gives each arm a bracket [lo, hi]: lo is the
    largest N whose steps all passed; hi = lo once a step failed or N=8 passed, else 8. It
    refuses a hold not run at typical load or with steps other than 300 s. A prediction holds
    when the bracket lies inside its range and is refuted when it lies outside. A bracket that
    straddles the range edge decides nothing, as the row says. The row reads REFUTED when any
    arm's prediction is refuted, SUPPORTED when all four hold, and INCONCLUSIVE otherwise.
11. Load. Amendment 27 windows: per hold, a level baseline (2 x 10 s), then `load.py settle`,
    whose last window is the arm's pre-window, then "during" windows over the steps. No
    treatment CPUs are removed: A1 runs on the P-cores, and A3's vLLM, C1 and C2 are not pinned.
    **Deviation from amendment 27 (d)'s "re-run"**: a hold whose windows saw fewer load
    containers than the level runs is VOID. It is not scored, and the arm's later holds are not
    run. Item 8's hold cap leaves no room for a re-run in this campaign. Only "C2 <= A1"
    compares arms. It is decided only when `load.py matched` over A1's and C2's windows reads
    MATCHED; INCONCLUSIVE or VOID leaves it undecided. The per-arm ranges are not comparisons and
    are not gated.
12. The synthetic APB, reported and never scored. On each arm's last hold (a step failed, or
    N=8 ran) and at the same load: `k3d-gpu capacity --slo 10 --url <server>/v1/chat/completions
    --force --fresh` with what the hold leaves, at most 3 min, skipped under 1 min.

Harness fixes from a fresh-context review of this PR, made before any measurement: the success
gate passes at exactly -10 points (`0.8 - 0.10` read as 0.70000000000000001 and failed 0.7);
each step's window opens 30 s after its warm-up ends, not 30 s after the step starts (a cold
warm-up over 30 s fell inside the window); `apb.json` is written after every step and records
`level` and `step_s`.

Implementation: `bench/eatflow/agentbench.py` (`run --pre-task --load-phase`, `apb --ramp
--base-from --load-phase`, `apb_from_holds`, `hold_void`, `h45_score`, `stretch`; subcommands
`apb-score`, `stretch`), `bench/eatflow/sbrouter.py`, `bench/eatflow/coding/sitecustomize.py`,
`bench/eatflow/coding-constraints.txt` and `bench/eatflow/live.sh` (`LIVE_MODE=code` and `apb`).
Regression tests in `bench/eatflow/test_h4345.py`:

- Purge: it runs before the warm-up and every task, outside the task's time, and a failed purge
  aborts the block.
- Stretch: the ratio pairs only ids completed at both levels. A timed-out or errored task is
  completed, failed and +inf. Each verdict is read at its edge: -10 points exactly holds, and a
  CI straddling 1.5 is INCONCLUSIVE. Memory-on rows and rows during missing load are dropped.
- Router: it routes by header and prefers the pod IP. It refuses a missing token or a bad header,
  and answers 502 for an unreachable sandbox. The shim maps `template` to the warm pool.
- Pins: `agent.lock`, `live.sh` and the hashed constraints agree.
- Ramp: a second hold gates on the first hold's N=1 success and refuses to run without it, or
  with a failed base hold. The window starts 30 s after a 40 s warm-up. The success gate passes
  at -10 points exactly. `apb.json` survives an abort.
- Scoring: brackets come from one or two holds. A hold after a failed step, at another level, or
  with another step length is refused, and a VOID hold and every later one are not scored. The
  load sampler tags "during" windows. Per-arm ranges, straddling brackets, and C2 <= A1 are read
  under matched, unmatched and missing load.

**Disclosed before running.**

- A1's B24 turn p95 at typical was 20.9 s in H42 (N=1, one block), over the 10 s SLO; C1 and C2
  read 42.0 and 39.4 s. If that repeats on B15-RO, A1's APB is 0, outside its predicted [1, 4],
  and H45 reads REFUTED under item 10.
- C1 and C2 are not pinned, so their during-window busy share on the load CPUs moves with the arm
  (a post-treatment variable, the case of amendment 27 and the L2 rule). "C2 <= A1" is therefore
  likely to read unmatched and stay undecided.
- H44's A1 heavy/none p95 inflation was 2.59 (CI 0.87-2.94) on B24 turns. That is not H43's
  statistic (heavy/typical time per task on C12), and it is not evidence for H43.
- Wiring smoke, before this amendment was merged and not scored or kept, on 2026-10-19.
  (a) 22:33-22:41 PDT: A1 in `code` mode at level none (not an H43 level), one 300 s C12 block.
  Sandboxes were claimed from `python-ecores` and pinned to CPUs 24-25 (E-cores),
  `install_package` reached PyPI, and every request carried one user message; 8 tasks ran.
  (b) 22:41-22:48 PDT: C2 in `apb` mode at typical with one 60 s N=1 step (the registered step
  is 300 s), then `capacity`. The baseline, pre- and during windows were written with 3 of 3 load
  containers running. The step read p95 30.4 s on 6 requests. (c) After the review: A1 in `code`
  mode, two 251 s blocks at level none, 23:21-23:32 PDT, to check the agent restart and purge
  across a censored task. The second block's fresh agent resumed at the task the first block
  censored, every purge read back 0 keys, and every request carried one user message.
  These runs tested the wiring only. Items 1-12 were written before (a) ran. Items 4, 6, 7, 9, 10
  and 11 were then tightened after the review, from code reading, not from smoke results. The
  only runner change prompted by a smoke run was the step-length variable for smoke runs
  (`LIVE_APB_STEP_S`, default 300), after (b)'s first attempt stopped as not deliverable.

### H43 amendment W2: the code-mode reserve (2026-10-19)

Written 2026-10-19 at 23:58 PDT, before any H43 block ran. The first A1 hold (23:48:58-23:50:03
PDT, harness `dc2e1aa`) exited 62 s in, before its server answered, as "not deliverable".
`live.sh` reserved 4 x 230 + 240 = 1160 s after the server answers. That is more than the 1170 s
hold leaves after bring-up, although W1 item 6 needs only that each block can reach 120 s. No
task, warm-up or load window ran, so the hold measured nothing and is void. The campaign was
stopped before any other hold took the lock. Fix: the code-mode reserve is nb x 230 + 150 s
(per block 120 s plus 110 s, 60 s of teardown, 90 s of sandbox, router, Redis and agent setup),
checked by `CodeReserveTest` in `bench/eatflow/test_h4345.py`. Nothing else changes. A1's hold
runs again as W1 item 6's one A1 hold, and A3's two holds run as before.

### H43 run (i9-13900T, 2026-10-20)

**Reproduce:**

```text
# provenance
date:            2026-10-20T07:06:44Z (A1 hold) and 07:37:55Z (A3 hold)
hypothesis:      H43
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores, 125 GiB RAM)
host kernel:     6.6.128-1-MANJARO
accelerators:    k3d-gpu up: nvidia=2 intel=8 hybrid=1 (A1 uses none; A3 the RTX 4000)
k3d-gpu commit:  5ea35b7 (#149, amendment W2, on the W1 runner of #148)
node image:      cryptoandcoffee/k3d-gpu@sha256:8cd264aa68c77681d7fa474693944de0db8d55de5881253e4029bf72a67e175c (0.1.61)
model:           A1 k3dgpu/Qwen3-4B-Instruct-2507-int4-ov (openvino_model.bin sha256 5c1e36e4...);
                 A3 cyankiwi/Qwen3-4B-Instruct-2507-AWQ-4bit @ c46e2277
runtime:         A1 openvino/model_server@sha256:a928903f... (2026.4.1), P-core balloon, tool_parser hermes3;
                 A3 vllm/vllm-openai@sha256:a4a4c043... (v0.31.0) on the RTX 4000;
                 LiteLLM v1.83.14-stable.patch.2@sha256:73cbb215...; coding-agent-maf at 758d484 with
                 k8s-agent-sandbox 1.0.7 (bench/eatflow/coding-constraints.txt); agent-sandbox v1.0.7,
                 warm pool python-ecores (sandboxes on CPUs 24-25, E-cores); redis:8.10.2-alpine@sha256:38117873...
energy source:   RAPL package (/sys/class/powercap/intel-rapl:0, read through K3D_GPU_RAPL_EXEC), plus
                 nvidia-smi for A3
runs per arm:    A1 4 blocks of 143 s (typical, heavy, heavy, typical); A3 2 blocks of 255 s (typical, heavy)
```

Commands: the scratchpad `hold.sh` runs `flock -w 21600 live.lock`, refuses `/` above 97%, then
`LIVE_MODE=code LIVE_CAP=1170 LIVE_CODE_DIR=<dir> timeout -k 30 1200 bench/eatflow/live.sh A1 <out>`
(A3: `LIVE_CODE_LEVELS="typical heavy"`), then deletes the cluster. Score:
`k3d-gpu bench agent stretch <dir>/h43-a1-typical <dir>/h43-a1-heavy --arm A1`. Records (per-level
results, turns, blocks, phased load windows, facts, the stretch output, pins, sandbox placement):
[bench/eatflow/results/2026-10-19/H43/](bench/eatflow/results/2026-10-19/H43/).

Holds. A first A1 hold at 2026-10-20T06:48:58Z exited 62 s in, before any task ran, and is void
([amendment W2](#h43-amendment-w2-the-code-mode-reserve-2026-10-19)). The scored A1 hold: server
up by 79 s (`sandbox up` then), blocks of 143 s from 131 s in, hold over at 870 s. Every
load window showed 3 of 3 load containers. E-core busy read 77-93% at typical (the load plus the
sandbox's own work) and 99.8-99.9% at heavy. Every LLM request carried one user message, and every
purge read back 0 keys. No block aborted, and no row was dropped. The A3 hold: server up
by 357 s, two blocks of 255 s. A3 completed all 12 C12 ids at both levels in its first hold (about 80 s
per block), so the second A3 hold would have run no task under W1 item 6's resume rule and was not
run.

A1, per task (seconds; censored = cut by the block budget, measured again in the level's next block):

| Task | Typical | Heavy |
|---|---|---|
| C01 | 66.9 pass | 142.9 censored (block 2), then 122.7 pass (block 3) |
| C02 | 29.3 fail (610 not in the sandbox output) | 20.0 censored (block 3) |
| C03 | 46.5 censored (block 1), then 99.9 pass (block 4) | not reached |
| C04 | 42.8 censored (block 4) | not reached |

On the 1 C12 id A1 completed at both levels (C01): paired time ratio heavy/typical **1.84**
(paired bootstrap CI 1.84-1.84: one id, so every resample is the same), ratio of medians 1.84,
delta success 0 points (1/1 at both levels). C02 was completed at typical and censored at heavy,
so it leaves the ratio.

**Verdict: REFUTED**, by the registered rule as `stretch` applies it: the ratio's CI lower bound
(1.84) is above 1.5. The rule sets no minimum number of ids, and W1 set none. This verdict rests on
one task: C01, 66.9 s at typical and 122.7 s at heavy. Its CI is degenerate, so it measures one
task's stretch, not a distribution. It matches the row's prediction (ratio 1.6-2.5, success
held). The 143 s blocks are the cause. Four blocks with an agent restart, a 30 s gap and a
warm-up each fit a 20-min hold only that short (W1 item 6), and A1's C12 tasks took 29-123 s.

A3 (context, not scored): 12 of 12 ids completed at both levels. Paired ratio heavy/typical 1.03
(CI 1.01-1.07), success 4/12 typical and 5/12 heavy. Median task 3.0 s at both levels: on the GPU
the agent's turns are short. Most A3 failures are graded misses: the expected value is missing
from the answer, or absent from the sandbox output.

### H45 run (i9-13900T, 2026-10-20)

**Reproduce:**

```text
# provenance
date:            2026-10-20T08:04:21Z (A1) to 10:31:43Z (C2 released); 7 holds, back to back per arm
hypothesis:      H45
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores, 125 GiB RAM)
host kernel:     6.6.128-1-MANJARO
accelerators:    k3d-gpu up: nvidia=2 intel=8 hybrid=1 (A3 uses the RTX 4000; A1, C1, C2 none)
k3d-gpu commit:  5ea35b7 (#149; the H45 runner of #148, dc2e1aa, unchanged)
node image:      cryptoandcoffee/k3d-gpu@sha256:8cd264aa68c77681d7fa474693944de0db8d55de5881253e4029bf72a67e175c (0.1.61)
model:           A1 k3dgpu/Qwen3-4B-Instruct-2507-int4-ov (openvino_model.bin sha256 5c1e36e4...);
                 A3 cyankiwi/Qwen3-4B-Instruct-2507-AWQ-4bit @ c46e2277; C1 qwen3:4b-instruct-2507-q4_K_M
                 (manifest sha256 0edcdef3...); C2 unsloth Q4_K_M (LFS sha256 3605803b...)
runtime:         A1 openvino/model_server@sha256:a928903f... (2026.4.1), P-core balloon, tool_parser hermes3;
                 A3 vllm/vllm-openai@sha256:a4a4c043... (v0.31.0); C1 ollama/ollama:0.40.5@sha256:5d3a2f25...;
                 C2 ghcr.io/ggml-org/llama.cpp:server-b11706@sha256:f41d47ee...; LiteLLM
                 v1.83.14-stable.patch.2@sha256:73cbb215...; banking-assistant at 758d484, ENABLE_MEMORY=false
energy source:   RAPL package (through K3D_GPU_RAPL_EXEC), plus nvidia-smi for A3
runs per arm:    A1 1 hold (N=1); A3 4 holds (N=1, 2, 4, 8, one each); C1 1 hold (N=1); C2 1 hold (N=1)
```

Commands: the scratchpad `hold.sh` runs `flock -w 21600 live.lock`, refuses `/` above 97%, then
`LIVE_MODE=apb LIVE_CAP=1170 LIVE_LEVEL=typical LIVE_APB_RAMP=<steps> [LIVE_APB_BASE=<first hold>/apb]
timeout -k 30 1200 bench/eatflow/live.sh <arm> <out>`, then deletes the cluster. A hold ran only when
every earlier step of its arm passed. Score:
`k3d-gpu bench agent apb-score --arm A1=<h1>/apb --arm A3=<h1>/apb,<h2>/apb,<h3>/apb,<h4>/apb --arm C1=<h1>/apb --arm C2=<h1>/apb`.
Records (per hold: `apb.json`, results, blocks, the baseline, pre- and during load windows, facts,
proxy and turn logs gzipped, the capacity report; `apb-score.json`):
[bench/eatflow/results/2026-10-19/H45/](bench/eatflow/results/2026-10-19/H45/).

Holds. Servers came up 86 s (A1), 280-358 s (A3), 49 s (C1) and under a minute (C2) into
their holds, so every hold was deliverable. Every baseline, pre- and during window showed 3 of 3
load containers, so no hold is VOID. A1, C1 and C2 failed N=1, which ended each ramp after one
hold. A3 passed every step. Every step ran 300 s, and its first 30 s after the warm-up were not
scored.

| Arm | Step | Window requests | Turn p95 s | Success (completed) | Errors | Gate |
|---|---|---|---|---|---|---|
| A1 | N=1 | 31 | 26.1 | 15/16 (0.94) | 0 | fail: p95 > 10 s, < 60 requests |
| A3 | N=1 | 265 | 3.0 | 125/133 (0.94) | 0 | pass |
| A3 | N=2 | 486 | 3.2 | 232/249 (0.93) | 0 | pass |
| A3 | N=4 | 880 | 3.5 | 428/449 (0.95) | 0 | pass |
| A3 | N=8 | 1476 | 4.0 | 694/765 (0.91) | 0 | pass (>= 0.94 - 0.10) |
| C1 | N=1 | 16 | 61.2 | 8/8 (1.00) | 0 | fail: p95 > 10 s, < 60 requests |
| C2 | N=1 | 17 | 61.5 | 9/9 (1.00) | 0 | fail: p95 > 10 s, < 60 requests |

Agents per box (bracket from `apb-score`) against the predictions:

| Arm | APB | Predicted | Status |
|---|---|---|---|
| A3 | 8 | >= 8 | holds |
| A1 | 0 | [1, 4] | **refuted** |
| C1 | 0 | <= 1 | holds |
| C2 | 0 | <= A1 | undecided: load unmatched |

Load, amendment 27 over A1's and C2's windows: **INCONCLUSIVE**. Pre-window `throttle_per_min`
read 1022 vs 333 (limit 255), and swap used and swap growth differed by 0.9-1.9 GiB and
663 MiB. Pre-window busy read 53.5 vs 49.3% and during-window busy 76.0 vs 77.1%; both are
within the limit. So "C2 <= A1" (0 <= 0 on the brackets) decides nothing, as the
pre-registration disclosed it likely would. During the steps, E-core busy read 72.6-79.5% for A1,
53.4-58.6% for A3, 84.2-89.1% for C1 and 74.8-79.1% for C2. C1 and C2 are not pinned, so their
servers share the load CPUs. Two of A3's four settle gaps ran the full 60 s without settling
(recorded, not gated).

**Verdict: REFUTED.** A1's agents per box is 0, outside its predicted [1, 4], and W1 item 10
reads the row REFUTED when any prediction is refuted. A1 failed N=1 on two gates: turn p95
26.1 s against the 10 s SLO, and 31 window requests against the minimum 60. A1's B24 turns in
H42 (p95 20.9 s) pointed the same way, as the pre-registration disclosed. A3's 8 and C1's 0 hold
their predictions. C2's prediction is undecided.

The synthetic APB, reported beside and never scored: `k3d-gpu capacity --slo 10 --budget 3` in
each arm's last hold ran out of budget for every arm. A1, C1 and C2 closed no step, so they have
no APB (bracket from 0). A3 passed one step, at 1 agent (82 requests, p95 1.47 s), so its APB is
>= 1. A 3 min budget is too short for this tool, which defaults to 15 min, so these brackets
say nothing about the graded result.

### Cross-host pre-registration: H25x, H27x, H28x, H34x on the Xeon 6741P (2026-10-20)

Written 2026-10-20T22:20Z, before any H25x, H27x, H28x or H34x measurement. Nothing here is a
measurement. Two setup smokes ran first under the lock (`bench/xhost/smoke.sh`, disclosed
below); they send no inference request. Every verdict already recorded keeps the host and rule
it was graded with; the i9-13900T rows are not re-graded.

**Why.** Every SUPPORTED speed row ran on one i9-13900T. The `x` rows replicate the rows a
second host can test on their original protocol and model, on the hardware Intel targets: a
Xeon with AMX that does not throttle. H27 asked for an AMX host by name. The other `x` rows are
SUPPORTED rows that back doc claims (H25, H28, H34). A replication keeps the original row's
model, probe, scorer, thresholds and outcome table; every change is listed below, and nothing
else differs. The H50-H54 rows on the same host belong to another campaign and are not touched.

**Host (`crescent`).** As in the [H50-H52 pre-registration](#h50-h52-pre-registration-the-xeon-6741p-2026-10-20):
Xeon 6741P (Granite Rapids, family 6 model 173, microcode 0xa000133), 1 socket, 48 cores /
96 threads (SMT siblings `n` and `n+48`), 1 NUMA node, 503 GiB RAM, no swap, kernel
7.0.0-31-generic, Docker 29.8.0. Flags include `amx_tile amx_int8 amx_bf16 avx512_bf16
avx512_vnni` and RDT (`rdt_a cat_l3 mba`). RAPL `intel-rapl:0` (package-0) through `sudo -n`,
reads only; PL1 300 W. Read-only before this pre-registration (21:40Z, load1 2.0-2.3): package
131-137 W over six 5 s windows (spread 6 W), `x86_pkg_temp` 59 C (high 97 C), so loaded idle sits
far below the throttle point. Four Arc Pro B70 serve `qwen38-a-hr` and `qwen38-b-hr`
(`ZE_AFFINITY_MASK` 0,1 and 2,3, all four cards); no `x` arm uses a GPU, and nothing of the
serving stack is stopped or changed.

**Triage of every ledger item that names a second host** (the H-rows and falsification-log
entries that ask for another, Xeon/AMX, XMX, non-throttling or quiet host, and the SUPPORTED rows):

| Item | What it asks | On crescent | Why |
|---|---|---|---|
| H27 | AMX host for the precision probe | **H27x** | AMX present; original weights kept |
| H34 (SUPPORTED) | replicate CPU Fast Draft | **H34x** | CPU only; original models |
| H25 (SUPPORTED) | replicate added-agent TTFT | **H25x** | CPU only; original model |
| H28 (SUPPORTED) | replicate image-volume sharing | **H28x** | host-agnostic; C1 needed for the disk |
| H7 | 5 ABBA rounds, the eager-dropped arm, energy | runnable, not in this batch | needs an energy arm and an eager-dropped arm added to `bench/apb/h7.sh`; a later pre-registration |
| H26-X | RDT/MBA classes against a DRAM hog | runnable, not in this batch | never pre-registered; needs a new design and host `resctrl` writes on a shared box |
| H4 (XMX-gated `auto` rule) | sweep `auto` vs forced devices on an XMX GPU | blocked | all four B70 are held by the live serving; needs a free B70 window. H54 (Gemma, another campaign) covers XMX GPU vs CPU for its model only |
| H22 (draft on another engine) | draft on a GPU, target on a non-throttling CPU | blocked | the draft engine on this host is a B70 (no iGPU); needs a free B70 |
| H30, H31, H32 (SUPPORTED) | `agent up` auto vs competitors | blocked | `auto` picks a B70 here (`gpu.intel.com/xmx=true`), so the protocol needs a free B70 |
| H15, H16 (SUPPORTED) | iGPU sharing, DRA | blocked / N/A | i915 iGPU rows; the B70 is `xe` (a new row, needs a free card) |
| H9 | quickstart warm arm n>=3 | blocked | quickstart's device choice lands on a B70 here |
| H23 | thermal headroom resource on a cool host | N/A as written | this host does not throttle (59 C idle, PL1 300 W); 24 pods on 48 CPUs never reach the cap, so arm B has no throttle to halve; needs a new workload, not a replication |
| H6, H2 | iGPU without XMX vs P-cores | N/A | no iGPU |
| H3, H5, H12-H14, H18, H20, H21, H24, H26, H29, H43, H44 | P/E placement, E-core load, uncore on a client part | N/A | P-core-only Xeon: no `cpu_atom`, and `up` bakes no balloons on a single-NUMA non-hybrid node |
| H1, H1b, H8, H45 | EAT on a non-Xeon; agents per box | N/A / other campaign | H1 is non-Xeon by definition; H50-H51 run EAT and agents per box on this host |
| H11, H17, H19, H40, H48 | launcher pin, DRA default, tune, graders | not needed | host-independent or not hardware rows |
| H52 | detection | done by another campaign | not re-run |

**Shared rules X1-X7** (every `x` row):

1. **Lock and hold.** Each hold runs through `bench/xhost/hold.sh NAME CAP CMD`, inside
   `flock -w 21600 ~/k3dgpu-bench.lock` (shared with the H50-H54 campaign, so no two
   measurements overlap). It applies the same hold-start rule as that campaign's
   `bench/eatflow/crescent/hold.sh` (load1 <= 12 and no aiperf process, 2-min polls for at most
   60 min, then the hold runs flagged), runs no hold above 97% disk, samples the serving
   covariate (`bench/eatflow/crescent/serving.py`, reported, never gating), and deletes every
   `k3dgpu-x-*` cluster and the bench-load containers at the end, also on failure. Records go
   to `~/k3dgpu-xhost/runs/NAME/`.
2. **CPUs.** `bench/load/load.py` keeps its non-hybrid default: the load runs on the upper half
   of the physical cores with their SMT siblings, CPUs 24-47,72-95. The treatment runs on the
   other half, CPUs 0-23,48-71 (24 cores, 48 threads): the k3d node container is pinned there
   with `docker update --cpuset-cpus` (as H28 A2 pinned it to the P-cores), and H27x's OVMS
   containers get `--cpuset-cpus 0-23,48-71`. This is the host's analogue of "treatment on the
   P-cores, load on the E-cores": disjoint cpusets on one package. It replaces the 16-CPU
   P-core balloon of H25, H27 and H34; core count and core type differ from the i9 by design,
   so a replication tests the hypothesis, not the i9's numbers. `load.py` reads `busy` over
   the load CPUs minus the treatment's cpuset, as on the i9.
3. **No accelerator stack.** H25x and H34x run `k3d-gpu up` with `K3D_GPU_VENDORS=none`, so
   no GPU, DSA, IAA or QAT plugin is deployed and no device reaches the cluster; the agent
   manifest is the CPU one (`K3D_GPU_AGENT_DEVICE=CPU`). On the i9 H34 ran with
   `intel,hybrid`; on this host neither adds anything to a CPU pod. H28x creates its cluster
   with plain `k3d cluster create`, as H28 does. H27x makes no cluster.
4. **Disk ([crescent amendment C1](#crescent-amendment-c1-kubelet-eviction-thresholds-on-a-95-full-disk-2026-10-20)).** `/` is 95% used by other tenants, so every `x` cluster is
   created with C1's kubelet thresholds,
   `K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%"`
   (for H28x passed to `k3d cluster create` as `--k3s-arg ...@server:*`, by `probe.sh`). Smoke 1
   below showed the cause: without C1 the agent pod stayed Pending. C1 changes when the kubelet
   evicts, nothing else, and applies to both arms alike. X1's 97% guard stays.
5. **Pins.** k3d v5.9.0 and kubectl v1.35.2 in `~/k3dgpu-xhost/bin`, each checked against its
   upstream checksum; node image `cryptoandcoffee/k3d-gpu:0.1.61` (the launcher's default at this pre-registration's base, set explicitly with `K3D_GPU_IMAGE` or `IMAGE` in every command,
   k3s v1.37.1; a change: the originals ran 0.1.28 (H25 run 4), 0.1.55 (H34) and 0.1.59 (H28),
   with the agent-manifest code unchanged since H25 run 4); OVMS `openvino/model_server@sha256:a928903f...76fc58` (2026.4.1, as every
   original row); `load.py`'s stress-ng pin. Models are downloaded on the host at the original
   rows' commits (`bench/xhost/fetch_models.py`), never copied: `OpenVINO/Qwen3-4B-int4-ov` @ `b467368d` (`openvino_model.bin`
   sha256 `074214fa29ab1b47...`, the H6/H25 file), `OpenVINO/Qwen3-0.6B-int4-ov` @ `f864c610`
   (sha256 `28613e994a6e1aaf...`, the H47 file), and for H27x `-int8-ov` @ `fd9c3cc8`, `-fp16-ov`
   @ `00298f1c` (the commits of H27's `model-shas.txt`; `probe.sh --prepare` now downloads
   exactly these). Versions are the original rows' (a replication's protocol is frozen); OVMS
   2026.4.1 is also the newest OVMS release on 2026-10-20.
6. **Level.** `typical` only, the ledger level of every original row. `heavy` is not run, to keep
   holds short on a shared box; a row may not claim anything at `heavy`.
7. **Verdict.** Each row is scored by its original scorer and thresholds and recorded with a
   Reproduce header. A row whose run misses its protocol is INCONCLUSIVE.

**Per row.**

- **H27x.** Command:
  `AUTOQUANT_DIR=~/k3dgpu-xhost/amx-autoquant AUTOQUANT_CPUS=0-23,48-71 LOAD_LEVEL=typical bench/amx-autoquant/probe.sh`
  (after `--prepare` outside the lock), hold cap 2700 s (timed part aborts at 1140 s, NLL pass
  capped at 600 s, as A1). Changes: the cpuset (X2); the held-out text is this repo's
  `EXPERIMENTS.md` and `docs/*.md` frozen at `7d4cd10` (H27's was frozen at its own commit), so
  dNLL is measured on a different 2026 text, with the same 0.05 nats/token gate and wikitext2
  check, and the 396-token turn prompt and the 128-token probe prompt (`prompts.json`) are cut
  from that text at H27's offsets. The OVMS precision log lines are recorded per arm (expected: bf16 inference precision
  on AMX for the fp16 IR); AMX use is inferred from the CPU flags and OpenVINO's documented
  default, not verified per kernel. Everything else is H27 + A1-A3.
- **H34x.** Command: `K3D_GPU_IMAGE=cryptoandcoffee/k3d-gpu:0.1.61 H34_CLUSTER=k3dgpu-x-h34 H34_VENDORS=none H34_NODE_CPUS=0-23,48-71
  H34_LEVELS=typical H22_MODELS=~/k3dgpu-xhost/models K3D_GPU_K3S_ARGS=<C1> bench/hetero-spec/h34.sh <out>`,
  hold cap 1500 s (`H34_LEVEL_S` 1200, 12 pairs). Changes: X2, X3, X4, the cluster name. The pod
  keeps its 16-CPU request and floats inside the node's 48 CPUs (no balloon on this host).
  `h34.py` takes J/token from raw package energy, as H34 did; here the package carries about
  130 W of other tenants' draw, so B/A J/token sits near 1 / (B/A tok/s) and adds little
  evidence beyond the speed leg. The threshold is kept; the reading is reported with that note.
- **H25x.** Command: `IMAGE=cryptoandcoffee/k3d-gpu:0.1.61 MODELS=~/k3dgpu-xhost/models OUT=<out>
  LEVELS=typical CLUSTER=k3dgpu-x-h25 NODE_CPUS=0-23,48-71 SEED_BASE=2540 HOLD=1440
  K3D_GPU_VENDORS=none K3D_GPU_K3S_ARGS=<C1> bench/shared-prefix/run.sh`, hold cap 1500 s. Changes:
  X2, X3, X4, the cluster name, new seeds. The agent manifest sizes the CPU request as it ships
  (half the node: 48) and keeps its default CPU Fast Draft in both arms, as H25 run 4 did. The
  draft is staged pinned (`f864c610`) at `<target>/OpenVINO-Qwen3-0.6B-int4-ov`, where the
  pull container looks for it (`fetch_models.py --draft`), so nothing is downloaded in the pod.
- **H28x.** Command: `IMAGE=cryptoandcoffee/k3d-gpu:0.1.61 bench/zero-copy-models/probe.sh prep`
  outside the lock, then `WORK=<out> IMAGE=... CLUSTER=k3dgpu-x-h28 LEVELS=typical HOLD=1440
  LOCK=~/k3dgpu-xhost/inner.lock K3D_GPU_K3S_ARGS=<C1> bench/zero-copy-models/probe.sh run`
  inside `bench/xhost/hold.sh` (the inner lock is private, since X1 already holds the shared
  one), hold cap 1500 s, started only at <= 95% disk (`XHOST_DISK_START=95`; the run writes about 30 GiB and X1 stops it
  at 97%). `probe.sh` already pins the node to the CPUs outside the load set.
  Scored by `bench/zero-copy-models/score.py CSV typical/match.json`: H28's table and A1 applied
  mechanically (void and spread rows first, then probe-blind, then the memory and time rows),
  with unmatched load giving INCONCLUSIVE and a VOID load level (A2) VOID, to be re-run; H28
  itself was scored by hand from the same table. A round with a failed mincore read (resident
  -1) is void. The two REFUTED memory rows are told apart by the snapshotter in the CSV's meta
  row. A T memory result between the SUPPORTED and REFUTED bounds, which the table leaves open,
  reads INCONCLUSIVE.

**Order.** H27x, H34x, H25x, H28x, one hold each, in turns with the other campaign on the lock.

**Setup smokes (not scored).** Smoke 1 (21:54-22:05Z, `smoke.sh`, no C1): `up` 31 s with
`K3D_GPU_VENDORS=none`, node pinned to 0-23,48-71, the agent manifest asked 48 CPUs with the
CPU Fast Draft, and the pod stayed Pending for 600 s (no `PodScheduled`), the disk-pressure
failure C1 fixes. Smoke 2 (22:14-22:20Z, with C1): `up` 48 s, the agent pod Ready at 89 s,
`Cpus_allowed_list` 0-23,48-71, graph `device: "CPU"` with the Fast Draft
(`draft_device: "CPU"`), no node taint; then the H28 path (plain `k3d cluster create` with
C1 as `--k3s-arg ...@server:*`) had no taint after 90 s. No inference request was sent in either.
Smoke records: `bench/xhost/results/2026-10-20/smoke{1,2}/`.

### H25x run (Xeon 6741P, 2026-10-21)

```text
# Reproduce
date:            2026-10-21T00:07:36Z (lock taken) .. 00:13:08Z (cluster k3dgpu-x-h25 and load removed;
                 lock released); pre-registration merged first (#156, ef87f86)
hypothesis:      H25x (H25's T part, A3 first-chunk TTFT, A4 amendment-27 load rule; cross-host rules X1-X7)
host cpu:        Intel(R) Xeon(R) 6741P (Granite Rapids), 48 cores / 96 threads, 1 NUMA node, AMX, no swap
host kernel:     7.0.0-31-generic, Docker 29.8.0
k3d-gpu commit:  ef87f86 (main), k3d v5.9.0, kubectl v1.35.2
node image:      cryptoandcoffee/k3d-gpu:0.1.61, K3D_GPU_VENDORS=none, crescent C1 kubelet thresholds;
                 node container pinned to CPUs 0-23,48-71 (pod Cpus_allowed_list 0-23,48-71 every round)
runtime:         openvino/model_server@sha256:a928903f…6fc58 (2026.4.1), `agent manifest` CPU as shipped
                 (cpu request 48, CPU Fast Draft OpenVINO/Qwen3-0.6B-int4-ov), graph prefix caching on/off
models:          OpenVINO/Qwen3-4B-int4-ov b467368d; draft f864c610 staged at <target>/OpenVINO-Qwen3-0.6B-int4-ov
                 (bench/xhost/fetch_models.py --draft; sha256 28613e99…)
load:            bench/load/load.py `typical`, stress-ng 0.22.01 @sha256:2ea9a7c3…1cbe on 24-47,72-95 (3 containers)
command:         bench/xhost/hold.sh H25x 1500 env IMAGE=cryptoandcoffee/k3d-gpu:0.1.61 MODELS=~/k3dgpu-xhost/models \
                   OUT=<out> LEVELS=typical CLUSTER=k3dgpu-x-h25 NODE_CPUS=0-23,48-71 SEED_BASE=2540 HOLD=1440 \
                   K3D_GPU_VENDORS=none K3D_GPU_K3S_ARGS="$C1" bench/shared-prefix/run.sh
runs per arm:    3 rounds ON, 3 OFF (ON OFF ON OFF ON OFF, seeds 2541-2546), 5 added agents per round, n = 15 per arm
raw results:     bench/xhost/results/2026-10-20/H25x/hold/ (out/typical/: round-*.json, score.json, load.jsonl,
                 baseline.jsonl, cache-usage-*.log; out/on.yaml, off.yaml; pre/post snapshots, serving.jsonl)
```

The hold started at once (load1 2.5, no aiperf process). The whole run took 329 s; no round
void. The prompt was 1634 tokens in every round (H25's shape). The launcher's baked
`intel-gpu-plugin` was deployed despite `K3D_GPU_VENDORS=none` (a launcher bug, fixed
separately); no pod requested a GPU, so the CPU result is not affected.

**Load (amendment 27): MATCHED.** busy spread 0.27 pp before and 0.44 pp during the rounds
(limit 5.0), throttle/min 0, swap 0; reported only: package W ON 244 / OFF 257, load1 27.3 / 28.9.

| `typical` | ON (prefix caching, shipping) | OFF | OFF/ON |
|---|---|---|---|
| added agent's first-chunk TTFT, median (n = 15) | 0.245 s | 1.724 s | 7.05x (95% CI 6.94-7.13) |
| per-round medians | 0.244, 0.246, 0.243 | 1.723, 1.730, 1.720 | |

c/OFF 0.99 (>= 0.8), a1 ON/OFF 0.99; every ON/OFF round pair is >= 4x (derived confidence 1.0).

**Verdict: T SUPPORTED** (`probe.py score`): the ratio 7.05x and its CI lower bound 6.94x are
above 4x, c/OFF 0.99. M not measured (A3), stays INCONCLUSIVE. Row verdict SUPPORTED, as H25.
Against the prediction: inside every range (OFF 1.72 s in 0.5-2.5, ON 0.245 s in 0.06-0.4,
ratio 7.05x in 4.5-14). The ratio is lower than the i9's 10.61x because the OFF prefill is
6.3x faster here (1.72 s vs 10.87 s) while ON only 4.2x (0.245 vs 1.02 s): per-request overhead
is a larger share of the ON time, the risk the prediction named.

### H34x run (Xeon 6741P, 2026-10-20)

```text
# Reproduce
date:            2026-10-20T23:13:58Z (lock taken) .. 23:23:07Z (cluster k3dgpu-x-h34 and load removed;
                 lock released); pre-registration merged first (#156, ef87f86)
hypothesis:      H34x (H34's row and protocol; cross-host rules X1-X7)
host cpu:        Intel(R) Xeon(R) 6741P (Granite Rapids), 48 cores / 96 threads, 1 NUMA node, AMX, no swap
host kernel:     7.0.0-31-generic, Docker 29.8.0
k3d-gpu commit:  ef87f86 (main), k3d v5.9.0, kubectl v1.35.2
node image:      cryptoandcoffee/k3d-gpu:0.1.61, K3D_GPU_VENDORS=none, crescent C1 kubelet thresholds;
                 node container pinned to CPUs 0-23,48-71
runtime:         openvino/model_server@sha256:a928903f…6fc58 (2026.4.1), one pod `ovms`, cpu request 16,
                 graphs a-4b-cpu (A) and b-spec-cpu (B, draft_device CPU, gamma 5), from h34.sh
models:          OpenVINO/Qwen3-4B-int4-ov b467368d (openvino_model.bin 074214fa…),
                 OpenVINO/Qwen3-0.6B-int4-ov f864c610 (28613e99…), bench/xhost/fetch_models.py
load:            bench/load/load.py `typical`, stress-ng 0.22.01 @sha256:2ea9a7c3…1cbe on 24-47,72-95 (3 containers)
energy source:   RAPL intel-rapl:0 energy_uj (sudo -n), per request
command:         bench/xhost/hold.sh H34x 1500 env K3D_GPU_IMAGE=cryptoandcoffee/k3d-gpu:0.1.61 \
                   H34_CLUSTER=k3dgpu-x-h34 H34_VENDORS=none H34_NODE_CPUS=0-23,48-71 H34_LEVELS=typical \
                   H22_MODELS=~/k3dgpu-xhost/models K3D_GPU_K3S_ARGS="$C1" bench/hetero-spec/h34.sh <out>
                 python3 bench/hetero-spec/h34.py <out>/run.jsonl <out>/load-typical.jsonl
runs per arm:    12 pairs, A B / B A, a 5 s load window before every request, n = 12 per arm
raw results:     bench/xhost/results/2026-10-20/H34x/hold/ (out/: run.jsonl, load-typical.jsonl,
                 verdict.json, manifest.yaml, probe.log; pre/post snapshots, serving.jsonl)
```

Disclosure (added 2026-10-21): the launcher's baked `intel-gpu-plugin` was deployed despite
`K3D_GPU_VENDORS=none` (a launcher bug, fixed separately) and advertised the B70s; no pod
requested a GPU, so the CPU result is not affected.

The hold started at once (load1 2.3, no aiperf process). Server warm at 120 s, the typical
load started at 122 s, 12 of 12 pairs in 424 s, no request dropped, every request returned
256 tokens. Greedy output identical in 9 of 12 pairs (first 200 characters), reported, not a
gate.

**Load: MATCHED** (`h34.py`, `load.py` TOLERANCE). Per-arm window means: busy on the load CPUs
37.5 / 37.4%, load1 31.8 / 32.1, package W 204.5 / 204.4, throttle/min 0 / 0, swap 0 / 0.

| `typical`, n = 12 pairs | A target alone | B CPU Fast Draft | B/A paired median | 95% bootstrap CI | B better |
|---|---|---|---|---|---|
| decode tok/s (median, min-max) | 37.20 (36.51-37.59) | 27.96 (25.41-30.52) | 0.758 | 0.688-0.809 | 0/12 |
| package J/token (median) | 9.12 | 11.41 | 1.241 | 1.168-1.347 | 0/12 |

**Verdict: REFUTED**, applied by `h34.py`: the tok/s ratio's CI upper bound (0.809) is below
1.20, and the J/token CI lower bound (1.168) is above 0.85. Sign test p = 0.00049 on each leg
(B slower and costlier in 12/12 pairs). On this host the draft does the opposite of what H34
measured on the i9: it costs 24% of decode speed and 24% more energy per token. As pre-registered,
J/token here carries about 130 W of other tenants' draw and so mostly mirrors the speed leg
(package 288 W during A, 278 W during B).

Against the prediction (SUPPORTED 0.6, tok/s 1.40 in 1.15-1.70): wrong in sign. The target
alone decodes 37.2 tok/s on 24 Granite Rapids cores (3.8x the i9's 9.7 under load), so the
draft's 5 serial steps and the verify are no longer cheap against one target step; the
pre-registered REFUTED branch ("the draft's share of a faster target step grows") is what
happened. The CPU Fast Draft that `agent up` turns on by default for a CPU Qwen3 target (#116)
rests on H34 (i9-13900T); this row shows it slows decode on this Xeon. No default changes in this
ledger entry; scoping that default is a separate change.

### H27x run (Xeon 6741P, 2026-10-20)

```text
# Reproduce
date:            2026-10-20T22:46:24Z (lock taken, load started) .. 22:55:29Z (OVMS and load removed;
                 lock released); pre-registration merged first (#156, ef87f86)
hypothesis:      H27x (H27's row, outcome table and amendments A1-A3; cross-host rules X1-X7)
host cpu:        Intel(R) Xeon(R) 6741P (Granite Rapids, family 6 model 173, microcode 0xa000133),
                 48 cores / 96 threads, 1 NUMA node, amx_tile amx_int8 amx_bf16, 503 GiB RAM, no swap
host kernel:     7.0.0-31-generic, Docker 29.8.0
k3d-gpu commit:  ef87f86 (main); no k3d cluster
runtime:         openvino/model_server@sha256:a928903f…6fc58 (2026.4.1), one container per precision,
                 --cpuset-cpus 0-23,48-71, named k3dgpu-i-amx-autoquant-<arm>-<run id>
models:          OpenVINO/Qwen3-4B-int4-ov b467368d, -int8-ov fd9c3cc8, -fp16-ov 00298f1c (model-shas.txt)
nll:             python@sha256:a6e34c59… + optimum-intel 2.2.0 / openvino 2026.4.1 (pip-freeze.txt),
                 INFERENCE_PRECISION_HINT=f32, 4088 held-out (repo text at 7d4cd10) + 1022 wikitext2 tokens
load:            bench/load/load.py `typical`, stress-ng 0.22.01 @sha256:2ea9a7c3…1cbe on 24-47,72-95 (3 containers)
energy source:   RAPL intel-rapl:0 energy_uj (sudo -n, reads only), per request in results.jsonl
command:         bench/xhost/hold.sh H27x 2700 env AUTOQUANT_DIR=~/k3dgpu-xhost/amx-autoquant \
                   AUTOQUANT_CPUS=0-23,48-71 LOAD_LEVEL=typical bench/amx-autoquant/probe.sh
                 (after `probe.sh --prepare` outside the lock)
runs per arm:    5 reference rounds (R1 ABCD, R2 DCBA, ... R5 ABCD), n = 5 per arm, arms int4 int8 fp16 int4';
                 3 probe repeats; one 5 s load window per reference request after a 10 s idle gap
raw results:     bench/xhost/results/2026-10-20/H27x/ (run/: results.jsonl, results.json, nll.json, load.jsonl,
                 load-matched.json, probe.log; hold/: pre/post snapshots, serving.jsonl)
```

The hold started at once (load1 2.3, no aiperf process, disk 93%). The timed part took 495 s,
the NLL pass 50 s; no abort, no error. Every arm's container was ready on first try. Swap-in 0
pages; no throttle event in any request. The int4' control read 0.97-1.02 of int4 in every
round, inside [0.9, 1.1].

**Load (A1, `load.py matched`, legacy rule as H27): MATCHED.** Spreads across the four arms:
busy 0.15 pp (limit 5.0), load1 0.59 (3.88), package W 1.9 (20.5), throttle/min 0 (10), swap 0.

**Quality gate** (`nll.json`, dNLL vs fp16, nats/token): int4 +0.0722 on the held-out text
(wikitext2 +0.0616), above the 0.05 gate; int8 -0.0030 (wikitext2 -0.0110). The same weights
failed the same gate on the i9 (+0.0703).

**Reference** (n = 5 per arm, medians):

| Arm | turn s | TTFT s (396-token prompt) | decode tok/s | package W | gross J/token |
|---|---|---|---|---|---|
| int4 | 2.987 | 0.495 | 38.11 | 301.1 | 9.37 |
| int8 | 5.503 | 0.570 | 19.37 | 293.2 | 16.81 |
| fp16 (bf16 on AMX) | 4.225 | 0.375 | 24.71 | 288.6 | 12.69 |
| int4' (control) | 2.981 | 0.459 | 37.68 | 301.2 | 9.29 |

Per round (int4 / fp16 / int8 turn, s): R1 2.94 / 4.13 / 5.47, R2 3.06 / 4.12 / 5.42,
R3 2.99 / 4.30 / 5.52, R4 3.02 / 4.23 / 5.51, R5 2.94 / 4.22 / 5.50. Gross J/token is package
energy over the request (the 130 W of other tenants and the load included) divided by 96 tokens;
it is reported, not scored.

**Verdict: REFUTED**, scored by `summarize.py` against H27's parts:
- (a) fidelity holds: the gated reference pick is **fp16**, which beat int8 in 5/5 rounds; the
  probe picked fp16 in 3/3 repeats (P1-P3).
- (c) cost holds: each probe repeat took 4 s (budget 60 s).
- (b) value fails: the pick (fp16, 4.225 s) is 1.24 s slower per turn than the static int4
  default (2.987 s), and int4 was faster in 5/5 paired rounds.

Confidence high (5/5 round wins, 3/3 probe agreement). Against the predictions: (a), (b) and
(c) came out as predicted, and every turn and TTFT range held; the pick did not. H27x predicted
int8 (outcome O2); the run picked fp16, because on AMX the bf16 path decodes faster than the
int8 weights (24.7 vs 19.4 tok/s) and has the shortest TTFT (0.375 s), the reverse of the i9,
where int8 beat fp16. fp16 decode beat its 10-22 tok/s prediction range. So on this host the
probe's pick moves (fp16, not int8) but still costs latency against int4, as on the i9; int4
stays the static default, and `autoquant` remains a quality gate, not a speed win, on an AMX
Xeon too.

### H50-H52 pre-registration: the Xeon 6741P (2026-10-20)

Written 2026-10-20, before any H50, H50o, H51, H52 or H52b measurement, with the runner
changes in the same PR. Nothing here is a measurement. Every verdict already recorded keeps
the host and rule it was graded with. The i9-13900T rows (H49b, H45) are not re-graded.

**Why.** Every agent result so far ran on one i9-13900T desktop, a client part EAT does not
target. EAT's CPU path is written for Xeon (`xeon-values.yaml`, bf16, `VLLM_CPU_SGL_KERNEL`),
and H49b's B1 ran it without AMX. H50 asks H49b's question on EAT's own target CPU, where B1
runs as Intel intends. H52 asks whether k3d-gpu's auto-detection sees that host as it is.

**Host (`crescent`).** Intel Tiber dev cloud, bare metal, reached through a jump host.
Xeon 6741P (Granite Rapids, family 6 model 173, microcode 0xa000133), 1 socket, 48 cores and
96 threads, 1 NUMA node, 503 GiB RAM, no swap, kernel 7.0.0-31-generic, Docker 29.8.0
(cgroup v2, systemd driver). RAPL `package-0` is readable through the harness's read-only
container, so package energy is measured. It is a shared serving box: four Arc Pro B70 cards
serve two SGLang Qwen3.8-27B containers (`qwen38-a-hr`, `qwen38-b-hr`) behind Dynamo and
llm-d, plus an `aiperf010` load-generator container and an observability stack. None of them
is stopped, restarted or changed for H50, H50o, H51 or H52, and no arm uses a GPU. Before this
pre-registration, the two SGLang schedulers read about 2.3 CPUs at idle (`docker stats`), and
`load1` read 1.9-2.1.

**Shared rules for the crescent holds.**

Scripts: `bench/eatflow/crescent/` (`hold.sh` rules 1, 3 and 4; `serving.py` rule 3;
`pairs-hold.sh`, `apb-hold.sh`, `h52.sh` and `inventory.sh` per hypothesis).

1. Lock and hold start. Every hold runs inside `flock -w 7200 ~/k3dgpu-bench.lock`. In the
   lock, before the hold, the wrapper records `uptime`, the top 10 CPU processes, the
   processes in `aiperf010` other than its idle `sleep infinity`, and `df /`. If `load1` > 12
   or an aiperf process runs, it waits in 2-min polls up to 60 min and records the wait. After
   60 min the hold runs and is flagged; it is not void. A hold does not start when `/` is
   above 97% full.
2. CPUs. No cpuset: the k3d node container gets all 96 logical CPUs. (A cpuset leaving 16,
   then 8, CPUs free was planned; the box owner lifted it before this pre-registration.) The
   background load runs on `load.py`'s non-hybrid default, the top half of the physical cores
   with their SMT siblings: CPUs 24-47 and 72-95. A1 and B1 are not pinned on this host: k3d-gpu
   labels a node for its balloons policy only when it is hybrid or has more than one NUMA node
   (`cmd_up`, `balloons_baked` in `scripts/k3d-gpu`), so both arms float over all 96 CPUs, as
   B1 did on the desktop.
3. Serving covariate. A sampler beside every hold (`serving.py`) writes `serving.jsonl` every 10 s:
   `load1`, the CPU-s/s of every container this campaign did not create (from its cgroup
   `cpu.stat`), and the count of aiperf processes. It is reported per arm over the attempt
   windows (mean serving CPU, share of windows with aiperf active) and never gates a pair.
   L2's pre-window `busy_pct` already carries the serving CPU that lands on the load CPUs.
4. Cleanup. This campaign's clusters (`k3dgpu-e-*`, `k3dgpu-a-*`, `k3dgpu-h52`, `k3dgpu-h52b`) are deleted
   after every hold, also on failure; another campaign on the host (`k3dgpu-x-*`) shares the lock.
   Everything created on the host lives under `~/k3dgpu-bench/`. At the end the campaign's
   images, models and directories are removed, except a small results copy.
5. Pins. k3d v5.9.0, kubectl v1.35.2 and Helm v4.1.1 in `~/k3dgpu-bench/bin`, each checked
   against its upstream checksum. Node image `cryptoandcoffee/k3d-gpu:0.1.61` (index digest
   `8cd264aa...`). The banking venv is rebuilt on the host from the desktop venv's frozen pins
   (uv 0.8.0, Python 3.12.11, `uv pip freeze` identical). Models are downloaded on the host,
   not copied: `Qwen/Qwen3-4B-Instruct-2507` @ `cdbee75f` and `OpenVINO/Qwen3-0.6B-int4-ov` @
   `f864c610`, all 29 files sha256-identical to the desktop's. A1's int4 model is re-exported
   on the host with the desktop's pinned command and venv (`optimum-cli export openvino
   --weight-format int4 --sym --group-size 128 --ratio 1.0`; openvino 2026.4.1, nncf 3.4.0,
   optimum-intel 2.2.0, torch 2.14.1+cpu): `openvino_model.bin` is byte-identical (sha256
   `5c1e36e4...`), as are the tokenizer files; `openvino_model.xml` differs only in 379
   auto-numbered op names (`Convert_147181` vs `Convert_147182`), with the same topology and
   attributes.

**H50 (A1 vs B1 on the Xeon).** Everything of the
[H47b and H49b pre-registration](#h47b-and-h49b-pre-registration-load-rule-l2-pairs-matched-on-pre-windows-2026-10-18)
for H49b, with its amendments P1 and P2, except what this list changes.

- Arms. A1: `k3d-gpu agent manifest` as in H49b (OVMS 2026.4.1 `a928903f...`, the int4 model
  above, ~~`K3D_GPU_AGENT_DRAFT=CPU` with the 0.6B draft~~ (superseded: no draft, amendments M1 and A4), `tool_parser` hermes3) with
  `K3D_GPU_AGENT_DEVICE=CPU`. On this host the auto device would pick a B70, because the node
  carries `gpu.intel.com/xmx=true`; on the i9-13900T (no XMX) auto chose the CPU, so naming the
  device changes nothing there. On this host A1 requests half the node's CPUs (48) and gets
  no balloon. B1: EAT stock, `xeon-values.yaml`
  at 0d3438d (= release-1.0.3-hotfix) with only the image tag pinned to its digest
  (`v0.24.0@sha256:b0e6f860...`) and the shipped Qwen3-4B KV cache, 40 GiB
  (`LIVE_EAT_KV_GIB=40`; H49b used 6 GiB to fit the 125 GiB desktop). max_model_len 8192,
  bf16, no pinning, node labels `ei-infra-eligible=true,ei-inference-eligible=true`.
- Tasks, order, budgets, resets, outcome classes, grader, statistic, gates, thresholds and
  the derived confidence: H49b's. Load: typical, rule L2 with `load.py` `TOLERANCE`
  unchanged.
- Holds. At most 40 min each (`LIVE_CAP=2370` inside `timeout -k 30 2400`), until 24 pairs or
  8 holds. The 900 s readiness rule and the 600 s pair-start guard stay. Cluster
  `k3dgpu-e-h50`, pairs directory `bench/eatflow/results/2026-10-20/H50/pairs/`.
- Recorded per hold by `live.sh`: the `amx_tile` line count in each server pod's
  `/proc/cpuinfo` (`isa-a.txt`, `isa-b.txt`) and B's log lines naming AMX, AVX-512, its ISA or
  oneDNN (`isa-b-log.txt`).

Predictions are in the row. Reasoning: on the desktop B1's turns took 69-329 s with AVX2 and
no AMX; AMX bf16 matrix units and 8-channel DDR5 should bring its B24 tasks under the 240 s
cap, so its within-budget success should approach A1's, and the paired difference's CI is
expected to sit under +30 points.

**H50o (A1 vs B3).** H50 with arm B = B3: EAT's OVMS chart, `core/helm-charts/ovms` at
0d3438d, rendered by `helm template ovms-model-server` with `modelSource=OpenVINO/Qwen3-4B-int4-ov`,
`image.tag=2025.4@sha256:c3707200...` (the chart's tag pinned to its index digest), and its
ingress and APISIX route turned off because they need EAT's ingress controller and APISIX.
Everything else is the chart's default: the init container's `ovms --pull` from Hugging Face
(the revision it pulls is read from its log and its weights' sha256 recorded), the PVC, 12-CPU
requests and limits, `cache_size` 20, and whatever graph `ovms --pull` writes (on 2026-10-20 it wrote `enable_prefix_caching: true` and no parsers; the graph each hold serves is saved). Model: the chart accepts
an `OpenVINO/` model without a Hugging Face token; no `OpenVINO/` build of Instruct-2507
exists, so B3 serves `OpenVINO/Qwen3-4B-int4-ov` (the Qwen3-4B hybrid model, @ `b467368d` when
checked on 2026-10-20). Recorded scope: B3 therefore differs from A1 in model as well as
engine version and placement.

- Disclosed wiring smoke, outside any cluster and before this pre-registration, discarded: the
  chart's `ovms --pull` command in a plain container wrote a `graph.pbtxt` with no
  `tool_parser` and no `reasoning_parser`; served with the chart's `--config_path`, OVMS
  2025.4 answers `/v3/models` and `/v3/chat/completions` (200) and not `/v1` (404), and an
  8-token reply began `<think>`. So B3's LiteLLM points at `/v3`, and B3's tool calls are
  expected to come back as text. No B24 task was sent.
- Holds as H50, at most 6. Cluster `k3dgpu-e-h50o`, pairs directory `.../H50o/pairs/`.
- B1 -> B3 and B3 -> A1. Described, never gated: per-arm within-budget success and median
  attempt time on each row's matched pairs, and the difference of the two paired differences
  (A1 - B1) - (A1 - B3) over task ids matched in both rows. A1 anchors both rows; the two
  rows run in different holds, so this split is descriptive.
- Every change B3 needs only to run on k3d beyond the two routes above is recorded as a
  finding, and B3 is otherwise run as shipped.

**H51 (agents per box on the Xeon).** H45's runner, gates and scoring (amendment W1 items 8-12)
with these changes. Ramp 1, 2, 4, 8, 16 (`apb-score --hyp H51`; the bracket's top is 16).
Arms: `live.sh` arms A1 and B1 as H45 ran them (A1: `agent manifest` with no draft, since the
default draft rule does not match the self-exported id, now with `K3D_GPU_AGENT_DEVICE=CPU`;
B1: as in H50), each in its own fresh cluster `k3dgpu-a-<arm>`; B1 with
`LIVE_EAT_KV_GIB=40`. Holds of at most 40 min, at most 4 in all: each arm runs its ramp in one
hold when bring-up leaves its steps plus 300 s, else N=1, 2, 4 in a first hold and N=8, 16 in a
second (`LIVE_APB_BASE`); `bench/eatflow/crescent/apb-hold.sh` (`LIVE_APB_TOP=16`). Order A1 then B1. Load typical; amendment 27 windows; a VOID hold as
W1 item 11. B1 <= A1 compares arms and is decided only when `load.py matched` over both arms'
windows reads MATCHED. H51 runs only if H50's holds end within their cap. Predictions are in
the row: the 60-request gate needs about 4.5 s per request at N=1, which A1 missed on the
desktop (31 window requests); both arms are near that line on this host.

**H52 (detection as found).** Inventory read before this pre-registration from sysfs, lspci
and CPUID (`/dev/cpu/0/cpuid`), not from k3d-gpu, kept as `~/k3dgpu-bench/inventory/`. Before
it, a read-only `k3d-gpu doctor` was run on this host by the session that assigned this work;
it reported the four B70s, AMX and AVX-512, and DSA, IAA and QAT present but unconfigured. No
`k3d-gpu up` has run on this host.

| Engine | Host inventory | Expected from a plain `up` |
|---|---|---|
| Intel GPU | 4 x Battlemage G31 `8086:e223` (Arc Pro B70) at 12:00.0, 48:00.0, 7c:00.0, 96:00.0, driver `xe`, renderD128-131; 1 ASPEED BMC VGA `1a03:2000` (`ast`) | Intel active; `gpu.intel.com/xe` allocatable 4 (discrete GPUs: 1 pod each); no `gpu.intel.com/i915`; `gpu.intel.com/xmx=true`; media engine labels; the ASPEED card not counted |
| AMX | CPUID leaf 7: AMX-TILE, AMX-BF16, AMX-INT8 = 1; leaf 7.1: AMX-FP16 = 1, AMX-COMPLEX = 0 (`/proc/cpuinfo` lists only `amx_tile amx_bf16 amx_int8`) | AMX active; `feature.node.kubernetes.io/cpu-cpuid.` AMXTILE, AMXBF16, AMXINT8, AMXFP16 `=true`; no AMXCOMPLEX |
| AVX-512 and VNNI | `avx512f bw cd dq vl ifma vbmi vbmi2 bitalg vpopcntdq vnni bf16 fp16`, `avx_vnni` | one `cpu-cpuid.` label for each of these 14 flags |
| DSA | 3 PFs `8086:0b25` (00:01.0, 05:01.0, 0a:01.0), driver idxd; dsa0, dsa2, dsa4 disabled, no work queue | named as present but not configured; no DSA resource advertised |
| IAA | 3 PFs `8086:0cfe`, driver idxd; iax1, iax3, iax5 enabled with one kernel work queue each, bound to `iaa_crypto` (deflate-iaa); no user work queue | named as present but not configured for pods; no IAA resource advertised |
| QAT | 3 PFs `8086:4944` (4xxx; services sym;asym, dc, sym;asym), state up, 0 of 16 VFs each | named as present but not configured (no VFs); no QAT resource advertised |
| DLB | 3 x DLB 2.5 `8086:2714`, no driver bound | not scored: k3d-gpu has no DLB support; it must not be advertised |
| Core types, NUMA | no `cpu_atom` PMU; 1 NUMA node | no hybrid or NUMA label; no balloons policy pod (k3d-gpu gates it on those labels); node allocatable CPU 96 |
| Other vendors | no NVIDIA, Gaudi or NPU device | none active; no `nvidia.com`, `habana.ai` or `npu.intel.com` resource |

Procedure, in one lock hold of at most 30 min: `k3d-gpu doctor` (saved), then
`K3D_GPU_CLUSTER=k3dgpu-h52 k3d-gpu up` with a scratch `KUBECONFIG` and no other k3d-gpu
variable (saved), `kubectl get node -o json` once the device plugin pods are Ready, `k3d-gpu
test` (its device pods list each resource's device directory), and four concurrent pods, each
with `gpu.intel.com/xe: 1`, that list `/dev/dri` and enumerate the GPU through Level Zero or
OpenCL in a pinned Intel GPU image (no model and no compute; the image and command are
recorded). Then `k3d-gpu down`. Scoring: each table row is read against the saved output. A
row matches when its expected items are all there and nothing it rules out is there; the four
pods must each see one distinct render node and enumerate one Intel GPU. SUPPORTED when every
scored row matches, REFUTED when any does not. "Named" means `up`'s own output names the
engine as present and not configured; `doctor` naming it is recorded beside but does not
satisfy the row, because the row tests `up`. A missing or phantom engine is a k3d-gpu bug: it
gets a regression test, a fix and a re-run as a new row; H52's verdict stands.

**H52b (after host setup).** A separate change, owned elsewhere and merged before H52b runs,
gives `k3d-gpu host-setup --apply` the commands to configure DSA and IAA work queues and QAT
VFs, each with a printed revert. Procedure: the H52 inventory is read again; `host-setup
--apply` runs on the host (sudo); a fresh `k3dgpu-h52b` cluster, the same reads and `k3d-gpu
test`, now also covering DSA, IAA and QAT; the cluster is deleted; the printed revert runs;
the inventory is read a third time. SUPPORTED when DSA, IAA and QAT are each advertised with
allocatable > 0 at the count host-setup reports configuring, each test pod sees its device
(`/dev/dsa`, `/dev/iax`, `/dev/vfio`), every H52 row other than DSA, IAA and QAT reads as in
H52, and the third inventory equals the first apart from its timestamp. REFUTED otherwise.
Disclosed: the IAA kernel work queues are in use by `iaa_crypto`; a setup that disables them
must restore them in the revert, or H52b reads REFUTED.

**Runner changes (this PR), with regression tests.** `bench/eatflow/pairs.py` maps each pairs
hypothesis id to its design and load rule (`DESIGNS`: H50 and H50o are the H49 design under
L2), so `agentbench pairs` and `pairs-report` accept them. `bench/eatflow/live.sh`: ARM H50
and H50o in `LIVE_MODE=pairs`; B1's KV cache from `LIVE_EAT_KV_GIB` (default 6, as before; 40 for H50);
H50o's B3 deploy from `LIVE_EAT_OVMS_CHART` with the pinned tag, OVMS's `/v3` path for its
LiteLLM and model list; `LIVE_APB_TOP` for the last ramp step; A1 names `K3D_GPU_AGENT_DEVICE=CPU`; the ISA records. The crescent scripts above. `bench/eatflow/agentbench.py`:
`apb-score --hyp H51` (ramp to 16, `h51_score`). Tests: `H50Test` in `test_pairs.py` (H50 and
H50o read as H49b, the void gate applies, the live.sh mappings, the KV parameter and B3's pins)
and `H51ScoreTest` in `test_h4345.py` (brackets to 16 over one or two holds, the three
predictions, the matched-load comparison, the CLI, the last-step test).

**Order and caps.** H52 first (one hold), then H50 (up to 8 holds), H50o (up to 6), H51 (up to
4, only if H50 ended within its cap), H52b when its fix is merged. A protocol change is a dated
amendment merged before the data it governs. A wiring smoke on crescent is allowed, runs no
scored task, is discarded and is disclosed in the run section.

### H52 run (Xeon 6741P, 2026-10-20)

**Reproduce:**

```text
# provenance
date:            2026-10-20T21:31:15Z to 21:34:26Z (one hold, no wait: load1 1.42, no aiperf process)
hypothesis:      H52
host cpu:        Intel(R) Xeon(R) 6741P (Granite Rapids, family 6 model 173), 48 cores / 96 threads,
                 1 socket, 1 NUMA node, microcode 0xa000133, 503 GiB RAM
host kernel:     7.0.0-31-generic
accelerators:    host inventory: 4 x Arc Pro B70 (xe), AMX, AVX-512, 3 DSA, 3 IAA, 3 QAT, 3 DLB 2.5
k3d-gpu commit:  fda704f (#152)
node image:      cryptoandcoffee/k3d-gpu@sha256:8cd264aa68c77681d7fa474693944de0db8d55de5881253e4029bf72a67e175c (0.1.61),
                 k3s v1.37.1+k3s1, containerd 2.3.4-k3s1; k3d v5.9.0, kubectl v1.35.2
runtime:         enumeration pods vllm/vllm-openai-xpu:v0.28.0@sha256:4756b66a... (`xpu-smi discovery`),
                 k3d-gpu test pods busybox:1.37@sha256:bdf57e52...
serving load:    load1 1.9-3.6 over the hold, the two SGLang servers 2.4 CPU-s/s, aiperf idle
```

Commands: `bench/eatflow/crescent/hold.sh H52 1800 bash bench/eatflow/crescent/h52.sh <out>` (the
only k3d-gpu variable set is `K3D_GPU_CLUSTER=k3dgpu-h52`). Records (doctor, up, node object,
`k3d-gpu test`, enumeration, inventory, serving samples):
[bench/eatflow/results/2026-10-20/H52/](bench/eatflow/results/2026-10-20/H52/).

`up` took 38 s and printed `Accelerators: intel amx` and `Accelerators schedulable: intel=4
amx=1`. Node allocatable: `cpu` 96, `gpu.intel.com/xe` 4, nothing else extended.

| Engine | Expected (pre-registered) | Detected | Row |
|---|---|---|---|
| Intel GPU | `gpu.intel.com/xe` 4; no i915 resource; `gpu.intel.com/xmx=true`; media engine labels; ASPEED not counted | `gpu.intel.com/xe: 4`, no i915 resource, `xmx=true`, product `Arc_Pro_B70` (4); **no media label** (`gpu.intel.com/media-vcs`/`media-vecs` absent) | **no** |
| AMX | AMXTILE, AMXBF16, AMXINT8, AMXFP16; no AMXCOMPLEX | exactly those four; `k3d-gpu test` AMX pod read `amx_tile` | yes |
| AVX-512 and VNNI | one label per listed flag (14) | all 14 (plus NFD's other CPUID labels, e.g. AVX10_512) | yes |
| DSA | named by `up` as present, not configured; not advertised | not advertised; **`up` does not name it** (`doctor` does: "3 device(s) but no user work queues") | **no** |
| IAA | as DSA | not advertised; **not named by `up`** (`doctor`: "3 device(s) but no user work queues") | **no** |
| QAT | as DSA (no VFs) | not advertised; **not named by `up`** (`doctor`: "no VFs" for each PF) | **no** |
| DLB | not scored; not advertised | not advertised | (not scored) |
| Core types, NUMA | no hybrid or NUMA label; no balloons pod; allocatable CPU 96 | none; no balloons DaemonSet; 96 | yes |
| Other vendors | none active, no resource | none (`nvidia.com/gpu.present=false`, which k3d-gpu sets to keep its plugin off) | yes |

Enumeration pods: all four `gpu.intel.com/xe: 1` pods Succeeded. Two logs were captured and show
one distinct card each (`renderD131` = 0000:96:00.0 and `renderD129` = 0000:48:00.0, `xpu-smi
discovery` naming one "Intel(R) Arc(TM) Pro B70 Graphics"); the other two logs were lost
("unable to retrieve container logs"), because the harness read each log only after waiting for
every pod in turn. `k3d-gpu test`'s GPU pod saw `renderD129`. That item is therefore unverified
for two cards, a harness fault fixed for H52b ([amendment D1](#h52-amendment-d1-the-fixes-and-h52b-2026-10-20)).

**Verdict: REFUTED.** Four rows do not match. (1) On `xe`, k3d-gpu sets no media engine labels:
`intel_gpu_labels` counts engines from i915's `/sys/class/drm/cardN/engine/` directories, which
`xe` does not have (the B70's media GT lists only the classes `vcs` and `vecs`), and the
existing test "xe GPU without an engine dir gets no media label" fixed that as intended. The
kernel does report the counts to any DRM client: a read-only open of `renderD128` shows
`drm-engine-capacity-vcs: 2` and `drm-engine-capacity-vecs: 2` in its fdinfo. (2-4) `up` does
not say that DSA, IAA and QAT are present but unconfigured; `doctor` does. Nothing phantom was
advertised, and every usable engine (four B70s, AMX, AVX-512) was detected and reached a pod.
The prediction (DSA, IAA and QAT named, 0.4) was refuted; the media miss was not predicted.

### H52 amendment D1: the fixes and H52b (2026-10-20)

Recorded 2026-10-20, after the [H52 run](#h52-run-xeon-6741p-2026-10-20) and before any H52b
step. H52 keeps its verdict (REFUTED). The H52b row is unchanged except as listed.

1. Media engines on `xe` (this PR). `intel_gpu_labels` and `doctor` also count the engines of
   each Intel render node on `xe` from the DRM usage stats of a read-only client on the node
   (`drm_fdinfo`, `xe_media_engines`): one `drm-cycles-<class>` line per engine class and
   `drm-engine-capacity-<class>` when a class has more than one engine (absent means one;
   Documentation/gpu/drm-usage-stats.rst). The open creates no context and submits no work; a
   node the user cannot open gives no label, as before. `doctor` prints
   `renderDN media engines: vcs xN, vecs xM (xe DRM usage stats)`. Regression tests in
   `tests/detect_test.sh` with the B70's fdinfo captured on this host (capacity 2 and 2), a
   class without a capacity line (one), no video class (zero) and usage stats from another
   driver (ignored). `bench/eatflow/crescent/inventory.sh` now prints each render node's usage
   stats too, as the independent source for the count.
2. The enumeration pods (`h52.sh`). Each pod prints its listing and `xpu-smi discovery`, then a
   marker, then sleeps; the harness reads its log once the marker appears (at most 600 s) and
   then deletes it, so no log is lost to a completed container.
3. `up` naming DSA, IAA and QAT, and `host-setup --apply` for them: the separate change H52b
   already names, merged before H52b runs.
4. H52b's reading of the other rows: "every H52 row other than DSA, IAA and QAT reads as in
   H52" becomes "reads as H52's expected table", so the GPU row now needs
   `gpu.intel.com/media-vcs=2` and `gpu.intel.com/media-vecs=2` (the counts the kernel reports
   in item 1's inventory).
5. Disk pressure (see amendment C1). H52b's `up` stays plain. If the node is tainted
   `node.kubernetes.io/disk-pressure` before its test pods schedule, H52b runs once more with
   C1's kubelet thresholds and records it; eviction thresholds do not enter detection.

### H56 pre-registration: parallel slice holds on crescent (2026-10-20)

Written 2026-10-20, before any H56 step, stage 0 included; revised 2026-10-21 after review, still
before any step (the revision list closes this section). Every crescent hold today takes one
exclusive flock on `~/k3dgpu-bench.lock` (`bench/eatflow/crescent/hold.sh`), shared with the
k3dgpu-x-* campaign, so one hold runs on the 96-CPU box at a time. H56 tests whether two holds
in disjoint cpuset slices can run at once without changing each other's result. No gain in
wall time is claimed: stage 0 measures whether the lock wait is large enough to matter.

**Why slices, against the C1 debate's unsliced design.** The C1 design review settled on K=2
unsliced (both holds on all 96 CPUs, rule 2 unchanged) and dropped slicing, because slicing
changes the treatment and, on one NUMA node, does not isolate DRAM. H56 tests slices anyway,
for two reasons. Unsliced, two holds' threads share cores, so a W/A difference would mix the
scheduler's placement of two 96-thread pools with resource interference, and a null result
would not carry over to any other placement. Sliced, the CPUs are disjoint by construction and
what is left is exactly the shared-uncore channel the debate worried about: DRAM bandwidth, L3
and package power, measured by the time, completion and MHz statistics below. The treatment
change is accepted openly: sliced holds are a new configuration, never pooled with the unsliced
H50, H50o and H51 holds (adoption rule 3).

**What ships with this section, off by default.** `HOLD_SLOT` unset leaves every crescent hold
as it was (K=1): the legacy lock, gate and cleanup lines run unchanged in hold.sh's `else`
branches, `load.py` without a slot builds byte-identical `docker run` argv, `live.sh` keeps its
names, ports and DevUI addresses, and `serving.py` keeps `MINE`. As hold.sh already closes the
lock fd 9 in every child (C3), a slotted hold also closes the slot fds 7 and 8 in CMD, the
sampler, the watchdog and the gate's waits, so its locks live exactly as long as hold.sh. With
`HOLD_SLOT=i` (`HOLD_SLOTS=2`, the only value accepted):

| | slice 0 | slice 1 |
|---|---|---|
| CPUs (`load.py slice`, from sysfs; SMT siblings n and n+48) | 0-23,48-71 | 24-47,72-95 |
| load CPUs (top half of the slice's cores, rule 2's ratio) | 12-23,60-71 | 36-47,84-95 |
| cluster, host containers, load containers | `k3dgpu-a-s0-a1`, `k3dgpu-a-s0-{litellm,rapl}`, `k3d-gpu-bench-load-s0-*` | the same with `s1` |
| ports, DevUI | 18600-18660, 127.0.0.1:8090 | 18700-18760, 127.0.0.3:8090 |

- Locks (flock(2); `bench/eatflow/crescent/slots.sh`). The global order is slot-0 < slot-1 <
  main, so no wait cycle exists; every wait is bounded by `-w`. The slot-i lock plus the
  hold's start-of-hold sweep make it the owner of `s<i>` resources: the kernel drops the lock
  when hold.sh dies, and CMD holds no lock fd, so an orphaned CMD can outlive the lock until the
  next hold's sweep kills it (`holds.py sweep`, campaign-wide). A slotted hold then deletes every `k3dgpu-(e|a)-s<i>-*` cluster, container and slot-i load container
  and nothing else.
  - Adoption mode (not used by H56): `slot-<i>`, then the main lock shared. A hold queued on its
    slot holds nothing on the main lock. Before taking main shared it waits while `/proc/locks`
    lists an exclusive request blocked on main (the x-campaign, a legacy hold, `h56.sh`). An
    exclusive waiter therefore waits only for the slot holds already running plus at most one
    per slot that read `/proc/locks` just before it blocked: slot holds cannot starve it. Slot
    holds can wait behind a stream of exclusive holds, which is the intended priority.
  - Inherited mode (`HOLD_LOCKS_HELD=1`, used by `h56.sh`): the parent holds fds 7 (slot-0),
    8 (slot-1) and 9 (main) exclusive. Each fd must be that file, and its
    `/proc/self/fdinfo/<fd>` must list an exclusive FLOCK. fdinfo lists only locks owned by
    that open file description, so this reads ownership without re-locking; a parent that
    opened the fds without locking them, or holds main shared, is refused (exit 4).
- Gate. Own-slice busy over 60 s <= 12.5% (12/96, rule 1's load1 <= 12 as a fraction of the box)
  and no aiperf process, polled every 120 s for at most `HOLD_GATE_MAX_S` (default 3600 s; H56
  sets 600 s for A and the sibling and 120 s for slice 0 in W), then the hold runs flagged.
  Shared rule 1 is unchanged; this gate is used only by slotted holds.
- Pinning. CMD runs under `taskset -c <slice>`; live.sh runs `docker update --cpuset-cpus
  <slice>` on the server node right after `up`, starts LiteLLM and the RAPL container with the
  same cpuset, and the load on the slice's load CPUs (`LOAD_CPUS`). `cpuset.txt` records the
  node's docker cpuset and `cpuset.cpus.effective`, LiteLLM's, RAPL's and each load
  container's cpuset, and the OVMS pod's `Cpus_allowed_list` and `nproc`.
- Covariate. With `SERVING_SLOT=i`, `serving.py` counts the sibling's cluster and load as
  foreign, and adds own-slice busy, other-slice busy and own-slice mean `scaling_cur_freq`.
- Scope. `live.sh` refuses a slot (exit 2) outside `LIVE_MODE=agent` and arm A1. Pairs are out
  until the pair-index claim of `pairs.py` (`next_pair`) runs under a per-HYP lock; A2 (the
  B70s) is out, so the per-card GPU state is untouched.

**Probe.** Crescent amendment C1's K3S args; A1 as live.sh's agent mode builds it at the merge
commit (OVMS int4, `K3D_GPU_AGENT_DEVICE=CPU`); B24 at typical load; cluster `k3dgpu-a-s<i>-a1`.

- A (alone): slice 0 runs the hold (cap 2400 s, `LIVE_CAP` 2370, `LIVE_BUDGET` 720); slice 1
  runs nothing (its slot lock is held by the driver).
- W (with sibling): the sibling starts first in slice 1 (cap 3000 s, `LIVE_CAP` 2970,
  `LIVE_BUDGET` 1800). Slice 0 starts the A command once the sibling's `live.log` shows
  `== run budget`, at most 840 s after the sibling started; past that the condition-hold stops
  (`sync-failed`) and is VOID.
- Order A W W A A W W A, four condition-holds per side. `bench/eatflow/crescent/h56.sh N` first
  runs `h56.py gate` (below) and refuses with exit 7 if it fails. It then takes `flock -w
  21600` on slot-0, slot-1 and the main lock exclusive, and runs both holds on those file
  descriptions. A W condition-hold is refused above 95% disk; the 97% guard stays. The record
  directory `hold<N>` is made only after the locks and the disk check, so a lock timeout or a
  disk refusal leaves no record and is not a VOID.
- One wiring smoke of W (`H56_SMOKE=1`, both `LIVE_BUDGET` 120, into `smoke-W`), after stage 0
  passes. It is discarded from scoring and disclosed in the run section. It also gates stage 1:
  `h56.py gate` refuses every condition-hold unless the smoke's slice 0 started its first task
  at most 900 s after its hold began (W puts slice 0's setup and its 720 s budget inside the
  sibling's 1800 s, so a setup near 1080 s would VOID every W hold) and completed at least 3
  tasks in its 120 s (about 18 per 720 s, 72 time pairs over four hold pairs, against the 60
  required).

**Statistic.** All 8 positions must hold a non-VOID condition-hold, else INCONCLUSIVE ("position
N missing"); a missing position is never skipped, since that would shift the A/W pairing and
break the ABBA balance. Hold pairs are (1,2), (4,3), (5,6) and (8,7). Within a pair, tasks are
matched by id over slice-0 cycle-0 rows:

- Time: tasks uncensored in both holds; the geometric-mean W/A ratio of `duration_s`.
- Success: every common task; ok = `passed`, not censored and `duration_s` <= 120; the mean
  paired W - A difference.
- Completion: completed (uncensored) slice-0 tasks, summed over the W holds over the A holds.
  A sibling that slows slice 0 enough to finish fewer B24 tasks in the 720 s budget moves this,
  even when the time pairs it leaves look unchanged.
- CIs: 90% (TOST at alpha = 0.05), from 10,000 two-stage bootstrap resamples, seed 56: draw the
  4 hold pairs with replacement, then each drawn pair's tasks with replacement. Interference is
  a hold-level treatment (sibling phase, foreign load and thermal state vary per hold), so the
  hold pair is the unit; a task-only resample would ignore that variance. With 4 clusters a
  percentile CI is still optimistic; that limitation is accepted, not corrected.
- Own-slice MHz: the mean `own_mhz` of slice 0's `serving.jsonl` rows inside its task window,
  mean over W holds / mean over A holds. It is a gate: an all-core turbo-bin drop on slice 0
  when the sibling runs is the interference channel the package cap does not show.
- Power: package W = sum `energy_j` / sum `seconds` of slice 0's `blocks.jsonl`, mean over the W
  holds, against PL1 (`intel-rapl:0/constraint_0_power_limit_uw`, read by h56.sh into
  `pl1.txt`). Energy in W is a package total and is not split between the two holds.
- Descriptive only, never gating: own-slice busy W - A; the A holds' package W; IMC CAS counts
  (`sudo -n perf stat -a -I 5000` with the `uncore_imc*/cas_count_{read,write}/` names
  crescent's own `perf list` reports, none recorded if it reports none). IMC is not a gate
  because neither the event names nor a peak bandwidth have been read on crescent.

**Bands.** Time [1/1.05, 1.05] and success [-1/24, +1/24] are the margins the C1 debate declared
before any build (±5% time, ±1 task of a B24 block); the first draft of this section widened
them to ±10% with no stated reason, and this revision restores them. ±1 task is the smallest
success change one B24 block can show. ±5% time is the size of the effects the adopting rows
must resolve: their arms are compared at a 120 s turn budget, and a slowdown s pushes every task
whose solo time lies in [120/(1+s), 120] s over that budget, so a K=2 penalty must be small next
to the differences those rows decide. Feasibility evidence, from another host: on the i9-13900T,
two same-condition A1 blocks in one H44 hold had a per-task log time ratio s.d. of 0.03 (none)
and 0.05 (heavy) and no success discordance
(`bench/eatflow/results/2026-10-17/H44-A1/`); between holds on different days it was 0.20
(14 common tasks of the H45 A1 hold against the 2026-10-17 A1 run, different days and setups). Crescent's between-hold spread is unknown, which is
what the INCONCLUSIVE prior below carries.

**VOID** (replaced at the end with `h56.sh N`, which writes `hold<N>-r<k>`; more than 2 VOID
condition-holds make the row INCONCLUSIVE): rc != 0 or NOT DELIVERABLE in either slice;
`cpuset.txt` not the slice (node, effective, LiteLLM, RAPL or pod `Cpus_allowed_list`), the
OVMS pod's `nproc` not the slice's CPU count, load containers not on the slice's load CPUs, or
fewer than the level's 3 slot-tagged load containers at the block's start or end; in W, slice
0's block window (first `t_start` to last `t_end`) not inside the sibling's; the gate flagged
the hold.

**Verdict.** SUPPORTED when all 8 positions are filled with at most 2 VOID, the time CI lies
inside [1/1.05, 1.05], the success CI inside [-1/24, +1/24], completion W/A >= 1/1.05, at least
60 matched time pairs exist, own-slice MHz W/A >= 0.95, the W package mean is below 0.95 PL1 and
PL1 was read. REFUTED when the time CI lies wholly outside its band, the success CI wholly
outside its band, or the completion CI wholly below 1/1.05 (all 8 positions filled, at most 2
VOID). INCONCLUSIVE otherwise.

**Predictions** (written 2026-10-20, before any run, and kept): time ratio 1.04 (0.99-1.09);
success difference -0.01 (-0.06 to +0.04); W package 0.75 PL1 (0.55-0.95), A package 0.5 PL1;
own-slice MHz W/A 0.97. Added 2026-10-21: completion W/A 0.97 (0.90-1.00); time pairs 80
(50-96). Verdict priors, updated to the narrower bands: SUPPORTED 0.30, INCONCLUSIVE 0.45,
REFUTED 0.25 (they were 0.45, 0.35, 0.20 against the ±10% bands; the predicted time ratio now
sits near the upper edge). Reasoning: batch-1 int4 decode is DRAM-bound, and the sibling's
decode plus its stream load share DRAM, L3 and package power, which slicing does not separate.

**Disclosures.** A1 requests 48 CPUs on a 48-CPU slice; the kubelet still reports the host's 96.
The OVMS pod's `nproc` is checked against the slice on every hold (VOID otherwise); the thread
pool OVMS sizes from it is not read. dockerd, the k3d serverlb and IRQs are not pinned. That
`docker update --cpuset-cpus` on the node reaches the nested pod cgroups was measured twice: on
crescent, in the [cross-host setup smoke 2](#cross-host-pre-registration-h25x-h27x-h28x-h34x-on-the-xeon-6741p-2026-10-20)
(node pinned to 0-23,48-71, pod `Cpus_allowed_list` 0-23,48-71), and on the i9 in the
[H28 run A2](#h28-run-a2-amendment-27-load-rule-i9-13900t-2026-10-18) (every pod read 0-15).
`cpuset.txt` still checks it on every hold, so a hold where it does not hold is VOID, not
scored. Both conditions run sliced, so slicing is the same in A and W. The IMC capture is stopped
by SIGINT to perf itself, found as a descendant of the backgrounded `sudo` (sudo does not relay
a signal sent from its own process group); neither that nor the event names has run on
crescent, so IMC may be missing, which changes no verdict.

**Hold caps.** At most 10 condition-holds (8 plus 2 replacements). Each piece of a
condition-hold has an enforced bound: the gate maxima (600 s, 600 s and 120 s, each plus one
60 s sample per round), hold.sh's `timeout -k 30` on the caps, and `SYNC_MAX` 840 s. Their sums,
printed by `H56_PRINT_CONFIG=1 h56.sh 1`: A at most 3390 s, W at most 3990 s, cluster cleanup
excluded. The first draft's "at most 3300 s including the sync wait" was not enforced and is
withdrawn.

**Stage 0.** `lockwait.py` first, read-only, over this campaign's `campaign.log` and the
x-campaign's (`--xlog ~/k3dgpu-xhost/runs/campaign.log`). Per hold: lock wait, hold time (lock
taken to rc), and the part of the lock wait that overlaps an x-campaign hold (`wait_behind_x`).
Slot holds cannot remove that part, since x holds stay exclusive, so the stage-0 share is
(lock wait - wait behind x) / hold time; the raw share is reported beside it. Holds with no end
line (killed, timed out, exited after the lock) count up to their last line and are listed as
unterminated. H56's own holds (`H56-*`) never count, so they cannot feed adoption rule 2. If
share < 0.10, stage 1 is not run, the row stays PENDING, and the run section records the share;
`h56.py gate` enforces this from `lockwait.json`. The population is every past hold, mostly
H50/H50o/H51, which adoption rule 3 keeps unsliced; their queueing is the only evidence of
whether lock wait costs this box anything, so the share is read as an upper bound on what slots
could recover (also split by row in `by_row`).

**F0 dropped.** The debate's zero-run F0 (regress H50/H51 per-attempt time and success on the
foreign CPU covariate) is not pre-registered here. Its range is foreign load lighter than a
sibling hold, so a flat slope could not clear concurrency and only a positive slope would stop
the work; H56's W condition measures the sibling directly. F0 may be pre-registered separately
before anyone looks at that slope.

**Adoption rule** (a later amendment; H56's verdict alone does not switch anything on). K=2 for a
crescent row needs all of: (1) H56 SUPPORTED; (2) share >= 0.25 over the last 20 holds (H56's
own excluded); (3) the row slices its holds from its first hold, never pooling sliced with
unsliced holds (so not the in-flight H50, H50o or H51); (4) pairs only after the per-HYP
pair-claim lock lands; (5) both holds run A1, or an arm whose measured package power and memory
traffic per hold are no higher than A1's; any heavier sibling (a vLLM bf16/AMX arm, say) needs
its own H56-style test first; (6) the x-campaign's lock wait logged.

**Tests** (no hardware): `tests/crescent_hold_test.sh` (legacy lock exclusive and sweep
unchanged; a slot-1 hold starts while slot 0 runs; cleanup and load stop scoped to the slot; an
outside exclusive flock excludes slot holds and the reverse; a queued slot hold holds nothing on
the main lock; a slot hold yields to a queued exclusive waiter; locks free as soon as a hold
exits; recovery after `kill -9`; inherited locks, refused when the parent did not lock them
exclusive; the own-slice gate; taskset on the slice over a 2-slice fake sysfs of the CPUs the
test may use; `h56.sh` refusals leave no record; run with `CRESCENT_HOLD_STRICT=1` from
`tests/bench_test.sh`, so a skip fails), `bench/load/test_load.py` `SlotTest`,
`bench/eatflow/crescent/test_crescent.py` (serving slot covariate, lockwait, the gate, the scorer
on fixtures), and the `live.sh` slot checks in `tests/bench_test.sh`.

**Revision 2026-10-21** (review, before any step): margins restored to the debate's; hold-pair
bootstrap; completion and own-slice MHz added as gates; every position required; pod `nproc`
checked; record directory made after the locks; stage 0 and the smoke enforced by `h56.py gate`;
stage 0 net of x-campaign wait, H56 holds excluded, unterminated holds counted; writer
preference in adoption mode; inherited locks read from fdinfo; lock fds closed in the sampler,
watchdog and perf; hold bounds enforced; the cpuset precedent re-cited; F0 recorded as dropped;
adoption limited to A1-or-lighter siblings.

**Amendment S0 (2026-10-21, before any stage-1 hold).** Stage 0 is a go/no-go gate on whether
stage 1 is worth running, not part of H56's outcome; stage 1's interference test is unchanged.
Disclosure: the stage-0 reading was seen before this amendment. At #168's head (cb7c745) on
crescent, the pre-registered statistic read share 0.0084 (net of x-campaign wait; 655 s of lock
wait, 644 s of it behind x holds, over 1307 s held, 4 holds) and share_all 0.50 (gross), so
stage 1 would not run. That gate measured the wrong population: it read one of the three
campaign logs that share `~/k3dgpu-bench.lock` (this campaign's, the x-campaign's, and the
Gemma/H54 campaign's, which is not read at all) and netted the x-campaign's holds out as
unrecoverable, while the lock contention on the box is between all three. Amended stage 0:
`lockwait.py --pool` pools the holds of every campaign log on crescent,
`~/k3dgpu-bench/runs/campaign.log`, `~/k3dgpu-xhost/runs/campaign.log`,
`~/k3dgpu-gemma/runs/campaign.log` and `~/k3dgpu-gemma/results/campaign.log` (the Gemma/H54
campaign writes two; a "waiting for the lock (arms: ...)" line counts as a wait line), H56's own
holds excepted, and nothing netted out: share = sum(lock wait) / sum(hold time). The 0.10 bar is
unchanged, and `stage1` in `lockwait.json` follows the amended share; the original statistic is
kept in the same file (`original`). The amended value is recorded at this amendment's merge
commit. If it clears 0.10, stage 1 runs exactly as pre-registered; if not, H56 stays PENDING and
K=1. Adoption rule 2 is not amended (it still reads the pre-registered `share_last`).

Reproduce (crescent, at the merge commit; record `git rev-parse HEAD`; results are committed to
`bench/eatflow/results/<date>/H56/`):

```
mkdir -p ~/k3dgpu-bench/results/H56
python3 bench/eatflow/crescent/lockwait.py ~/k3dgpu-bench/runs/campaign.log --xlog ~/k3dgpu-xhost/runs/campaign.log --runs ~/k3dgpu-bench/runs \
  --pool ~/k3dgpu-bench/runs/campaign.log ~/k3dgpu-xhost/runs/campaign.log ~/k3dgpu-gemma/runs/campaign.log ~/k3dgpu-gemma/results/campaign.log \
  > ~/k3dgpu-bench/results/H56/lockwait.json   # amendment S0; `original` keeps the pre-registered statistic
# h56.sh refuses (exit 7) unless lockwait.json says stage1, and, for N, unless smoke-W passes h56.py gate
H56_SMOKE=1 bench/eatflow/crescent/h56.sh 2
python3 bench/eatflow/crescent/h56.py gate ~/k3dgpu-bench/results/H56
for n in 1 2 3 4 5 6 7 8; do bench/eatflow/crescent/h56.sh "$n" || break; done
python3 bench/eatflow/crescent/h56.py score ~/k3dgpu-bench/results/H56
# VOID replacement (at most 2 in all): bench/eatflow/crescent/h56.sh <N>   -> writes hold<N>-r<k>
```

### Crescent amendment C1: kubelet eviction thresholds on a 95%-full disk (2026-10-20)

Recorded 2026-10-20, after an H50 wiring smoke (no task sent, discarded) and before any H50,
H50o, H51 or H53 measurement. The host's `/` was 95-96% used by its other tenants (169 GB free
of 3.6 TB; this campaign's images, models and venvs are about 30 GB of it). The kubelet's
default hard eviction (`nodefs.available<10%`, `imagefs.available<15%`) therefore marks the
k3d node `DiskPressure` within a minute of `up`, taints it `NoSchedule`, and evicts pods: in
the smoke both servers stayed Pending and the NFD workers were evicted. H52 ran before the
taint landed.

- Every H50, H50o, H51 and H53 hold creates its cluster with
  `K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<1%,imagefs.available<1%,nodefs.inodesFree<1%"`
  (k3s passes it to the kubelet; set in `bench/eatflow/crescent/pairs-hold.sh` and
  `apb-hold.sh`). It changes when the kubelet evicts, nothing else; it applies to both arms of
  every comparison alike. Shared rule 1's 97% guard and the hold wrapper's disk watchdog stay:
  a hold stops above 97%.
- The smoke is disclosed in the H50 run section and is not scored.

### Crescent amendment M1: Gemma 4 26B-A4B (QAT) on the CPU rows (2026-10-20)

Recorded 2026-10-20, before any H50, H50o or H51 measurement. Only the wiring smokes of
amendment C1 have run (Qwen3-4B, no task sent, discarded). The box owner's directive: the CPU
arms run the Gemma 4 QAT family or the Qwen3.8 series, not Qwen3-4B. H50, H50o and H51 now run
Gemma 4 26B-A4B-it (QAT), which is in EAT's own CPU catalog (`cpu-gemma4-26b-a4b`, menu 26 of
`deploy-agentic-stack.sh`). **This breaks direct replication of H49b**: H50 no longer repeats
H49b's model on new hardware; it compares the two stacks on EAT's own catalog model on EAT's
target hardware. H49b keeps its verdict. Designs, rules, gates, thresholds and hold caps are
unchanged; arms, model and sampling change as follows, and the predictions are restated.

Model files (all read on 2026-10-20; downloaded on crescent by the Gemma workflow under
`~/k3dgpu-gemma/models` and reused here by path, each file's sha256 checked against the Hub's
LFS sha256 before the first hold and recorded):

- `google/gemma-4-26B-A4B-it-qat-q4_0-unquantized` @ `f1e06dc5` (bf16 safetensors of the
  QAT-trained weights, 51.6 GB, `Gemma4ForConditionalGeneration`, not gated).
- A1's OpenVINO IR: INT4 symmetric, group size 32, ratio 1.0, exported from those QAT weights
  (Q4_0 is symmetric with 32-weight blocks, so this keeps the QAT benefit), built once by this
  campaign as the single copy every crescent workflow reuses:
  `bench/models/export_qat_ov.sh` (`optimum-cli export openvino --task text-generation-with-past
  --weight-format int4 --sym --group-size 32 --ratio 1.0`, under the lock) into
  `~/k3dgpu-gemma/models/k3dgpu_gemma-4-26B-A4B-it-qat-int4-sym-g32-ov`, with a `manifest.json`
  (pins, command, source revision, every file's sha256). Pins: optimum-intel 2.2.0, optimum
  2.3.0, openvino 2026.4.1, nncf 3.4.0, torch 2.14.1 (each the latest release on 2026-10-20) and
  transformers 5.5.4, the newest optimum-intel 2.2.0 allows (`transformers<5.6`; the latest is
  5.20.2). If the text-only task cannot export this checkpoint, the export and its error are
  recorded and `image-text-to-text` is used instead. Export flags per the model-card digest
  (`model-cards/REFERENCE.md` 3.4): `--backup-precision none`, and no AWQ, GPTQ or scale
  estimation. The QAT grid: the checkpoint's weights already lie on a symmetric 4-bit grid with
  one step per 32-weight block (REFERENCE.md 3.2), and an absmax-based INT4 scale re-rounds every
  value (5-9% rel-RMS there). So after the export `bench/models/qat_grid.py` measures each INT4
  weight's rel-RMS error against the checkpoint (`check`), rewrites its int4 values and scale with
  scale = the block's grid step (`snap`), and measures again; both reports and their summaries
  (median and max per kind: attention, experts, other) are in the IR's `manifest.json`. A1 serves
  the snapped IR. `OpenVINO/gemma-4-26b-a4b-it-int4-ov` (INT4_ASYM g128 from the base, non-QAT
  model) is not A1's model.
- B3's model: `OpenVINO/gemma-4-26b-a4b-it-int4-ov`, pulled by EAT's OVMS chart itself (the
  chart accepts only `OpenVINO/` ids without a token); @ `4e5885c3` when read.

Arms:

- A1: `k3d-gpu agent manifest` on the CPU (`K3D_GPU_AGENT_DEVICE=CPU`), the QAT IR above,
  `K3D_GPU_AGENT_TOOL_PARSER=gemma4` (OVMS 2026.4.1's parser for this model's tool format, and the
  format EAT gives B1; k3d-gpu sets no parser for a model outside its default family), no
  reasoning parser (B1 has none), and no draft: the Gemma 4 MTP assistant is H54's factor, so A1
  has no speculative decoding here (H49b's A1 had the Qwen 0.6B draft).
- B1: EAT stock, `xeon-values.yaml` entry `google/gemma-4-26B-A4B-it` at 0d3438d (`--block-size
  128 --dtype bfloat16 ... --enforce-eager --enable-prefix-caching --enable-auto-tool-choice
  --tool-call-parser gemma4 --chat-template .../tool_chat_template_gemma4.jinja`,
  `VLLM_CPU_KVCACHE_SPACE` 40), rendered as EAT's playbook does (`LLM_MODEL_ID=google/gemma-4-26B-A4B-it`,
  `chatTemplateConfigMap.enabled=true`: the v0.24.0 image does not contain the template, the
  chart mounts it), image tag pinned to its digest, `--max-model-len 8192` (`xeon-values.yaml`'s
  `max_model_len`, as H49b). The one deviation from stock: `--model` points at the staged QAT
  bf16 weights instead of `google/gemma-4-26B-A4B-it`, so every arm runs the same QAT-trained
  weights.
- B3 (H50o): EAT's OVMS chart as before, `modelSource=OpenVINO/gemma-4-26b-a4b-it-int4-ov`. Read
  from the 2025.4 image's source before this amendment: OVMS 2025.4 has no `gemma4` tool or
  reasoning parser, and Gemma 4 support arrived in OVMS 2026.2. If B3 is not ready with 900 s
  of its first hold left, H50o runs no further hold and is INCONCLUSIVE (not deliverable),
  recorded as a finding.

Sampling, identical for every arm and set by the recording proxy (it replaces every sampling
key the agent sends), from the model card (`README.md` "Sampling Parameters" and
`generation_config.json` @ `f1e06dc5`): `temperature` 1.0, `top_p` 0.95, `top_k` 64, with
`max_tokens` 1024 as before (profile `agent_gemma4` in `bench/eatflow/agent.lock`). Thinking is
off for every arm: the card enables it only with `<|think|>` in the system prompt, which the
banking agent does not send, and the template's `enable_thinking` defaults to false; the proxy
also sends `chat_template_kwargs: {"enable_thinking": false}` so every engine renders the same
prompt (the coordinating session's model-card digest, `model-cards/REFERENCE.md` 1.1, cited in
the Reproduce headers). H49b's greedy `agent_local` profile is not used: the card's
documented setting applies, and the paired design carries the extra run-to-run variance.

Predictions, restated before measuring (H50): A1 within-budget success >= 0.70 (0.7). B1 >= 0.50
(0.6): 3.8B active parameters at bf16 on AMX should keep its turns under 120 s. Paired A1 - B1
in [-0.15, +0.30] (0.6); L2 passes (0.6); REFUTED (0.4), INCONCLUSIVE (0.5), SUPPORTED (0.1).
H50o: B3 not deliverable (0.6); otherwise B3 <= 0.10 (0.8). H51: A1 in [1, 4] (0.5): OVMS runs
the Gemma 4 MoE without continuous batching, so agents queue; B1 in [0, 4] (0.5); B1 <= A1
(0.5).

Runner: `LIVE_MODEL_SET=gemma4` in `bench/eatflow/live.sh` (model ids, dirs, tool parser,
LiteLLM alias, EAT release name, chat template), `LIVE_SAMPLING`, profile `agent_gemma4`,
`stage.py vllm` touching only the env ConfigMap; set by `pairs-hold.sh` and `apb-hold.sh` for
the crescent rows. Disk: B1's 51.6 GB of weights are mounted read-only into the k3d node
(`LIVE_MOUNT_B=1`, through k3d-gpu's new `K3D_GPU_VOLUMES`, and `stage.py`'s `STAGE_HOST_DIR`)
instead of copied into it, which with a 94%-full `/` would cross shared rule 1's 97% guard; A1's
IR is copied as before (its OVMS `--pull` writes into the model directory). This changes where the
node reads B1's files, not what B1 serves. Regression tests in `test_pairs.py` (`ModelSetTest`),
`test_stage.py`, `tests/detect_test.sh` (`K3D_GPU_VOLUMES`) and `bench/models/test_*.py`.

### Crescent amendment A3: analysis rule L3 for the crescent pairs rows (2026-10-20)

Recorded 2026-10-20, before any H50 or H50o measurement (no `H50*` directory exists on
crescent; only H52 and discarded wiring smokes have run there). It changes the pre-registered
analysis of H50 and H50o, and of any later crescent pairs row that cites it, from load rule L2 to
rule L3. H47, H49, H47b and H49b keep the rules they were graded with and are not re-graded.

**Why.** Rule L2 drops every pair whose two idle pre-windows disagree beyond `load.py`
`TOLERANCE`, then gates on at least 16 matched pairs and 75% matched. In H47b and H49b that
filter kept 4 of 10 and 3 of 23 pairs, mostly on idle throttle counts (summed over every logical
CPU, 12-3342 events/min between two idle windows against an absolute tolerance of 10). An audit
of the committed H47b and H49b pre-windows found no sign that arm predicts any pre covariate
(sign-flip p 0.34-0.82 on all four, both campaigns), so the filter discarded data without
guarding against a bias that was present. Amendment 27's point, that a covariate the treatment
moves must not decide the comparison, is kept by a balance gate instead of a match filter.

**Rule L3 (primary for H50 and H50o).**

1. Every complete, non-void pair enters every statistic. The 75%-matched gate is removed; the
   16-pair minimum (now complete pairs), the void limit (2) and the idle-arm check stay.
2. Balance gate: for each amendment-27 pre covariate (`busy_pct`, `throttle_per_min`,
   `swap_used_mib`, `swap_delta_mib`), the two-sided sign-flip test of the per-pair difference
   pre(A) - pre(B) (10000 draws, seed 40). Any p < 0.01 makes the row INCONCLUSIVE ("covariate
   tracks arm").
3. Primary statistic, thresholds and verdict rule unchanged: within-budget success per arm
   (Wilson), the paired difference over all complete pairs with its paired bootstrap 95% CI
   (2000 resamples, seed 40); SUPPORTED when the CI's lower bound is >= +30 points and A1 >= 0.6
   with every gate passed, REFUTED when the upper bound is < +30, else INCONCLUSIVE. The
   sign-flip p of the paired difference is reported beside it.
4. Reported, never gated: the adjusted difference (intercept of d(pass) ~ 1 + d(log1p
   throttle/min) + d(busy_pct) over the pre-windows, paired bootstrap CI). It is not a gate:
   the second attempt's pre-window follows the first attempt, so these regressors partly carry
   carryover, the post-treatment problem amendment 27 removed L1 for. Also reported: L2's
   matched subset and its paired difference (secondary, sensitivity only), every pre-mismatch
   reason, settle times, and the load during each attempt per arm.
5. Arm order: pair j runs A first or B first by a coin from `Random("<seed>:<j>")`, a pure
   function of the registered seed and j (so a resumed hold draws the same order), replacing the
   parity order whose position was confounded with task parity. Seeds: `H50-crescent-2026-10-20`,
   `H50o-crescent-2026-10-20` (and `H53-crescent-2026-10-20` for H53, once it is pre-registered).
6. The washout is unchanged: `load.py settle` (adaptive, at most 60 s) then the 10 s
   pre-window, before every attempt.
7. Registration: the first hold of a pairs directory writes `registration.json` (`hyp`, `rule`,
   `seed`) once (a temp file hard-linked into place, so it appears whole or not at all, under the
   host lock); every later hold must read the same registration or it stops; `pairs-report` refuses an L3 row whose directory has none or a
   different one. `live.sh` reads the rule from `bench/eatflow/pairs.py` (`DESIGNS`, the only
   table of hypothesis to rule). A `pairs.jsonl` line still being written (no newline yet) is
   not read.

**Restated prediction (H50, M1's arms):** REFUTED (0.55), INCONCLUSIVE (0.35), SUPPORTED (0.10):
the L2 gate no longer drives INCONCLUSIVE; the balance gate fails rarely (0.1); the paired
difference is expected below +30 points. H50o: as M1 (B3 not deliverable, 0.6).

**Implementation (this PR), with regression tests in `bench/eatflow/test_pairs.py` (`L3Test`):**
arm-independent pre-window noise keeps every pair and a clear effect reads SUPPORTED; a
covariate that tracks the arm reads INCONCLUSIVE; the 16-pair and void gates still apply; an L3
analysis without its registration, or with another, refuses; the seeded order is deterministic
and close to balanced; replaying the committed H49b pairs under L3 gives 23 pairs, a passing
balance gate and a paired difference of +0.609 (95% CI lower bound >= +0.30), which is a check
of the code, not a verdict on H49b; `live.sh` has no hypothesis-to-rule table.

**Review fixes, before any H50 data (2026-10-20):**
- The balance test on `throttle_per_min` uses log1p of the reading, as the adjustment does. The
  raw reading (12-3342/min on crescent, heavy-tailed) left the test almost no power.
- Reported beside the primary, never gated: the period-balanced crossover estimate. It is the
  mean of d = ok(A) - ok(B) over A-first pairs and over B-first pairs, averaged
  (`period_balanced`). The seeded coin need not balance the order, and H49b showed a large
  position effect.
- `pairs.design` refuses a rule-L3 row until this file's hypotheses table has its row. No hold
  runs and no score is computed before that.

### Crescent amendment A4: A1 is k3d-gpu's best CPU config for the Xeon (2026-10-20)

Written 2026-10-20, before any H50 or H50o measurement. It changes no arm. It records why A1's
configuration is the right one for this hardware class.

- **No CPU Fast Draft.** On this Xeon, H34x is REFUTED (PR #170). The Qwen3-0.6B int4 draft made
  decode 0.758x the target alone (28.0 vs 37.2 tok/s), and J/token 1.24x. All 12 of 12 pairs
  were worse.
- **int4, not AMX bf16 or fp16.** H27x is REFUTED (PR #159, merged fcfd7ace). AMX bf16 and fp16
  are still slower than int4 here.
- Amendment M1 already sets A1 to int4 (the snapped QAT IR) with no draft. A1 therefore
  coincides with the best measured CPU config, and needs no change.
- **Where the config is set.** A1 is pinned explicitly, not left to k3d-gpu's automatic choice:
  `K3D_GPU_AGENT_DEVICE=CPU`, int4 IR and `A_DRAFT=off`. These are set by the `gemma4` model set
  in `bench/eatflow/live.sh`, at the commit that merges this amendment.
- The model optimizer (PR #163, open) will make the draft default per hardware class. If it
  merges before H50's first hold, the hold records whether `k3d-gpu auto` on this host selects
  the same config, with the commit. A mismatch is recorded and does not change A1.
- **Gemma 4's MTP drafter** (`google/gemma-4-26B-A4B-it-assistant`) is a different mechanism from
  the 0.6B CPU draft. It is not an arm of H50 or H50o; it is H54's factor. Any MTP-on arm is
  labelled separately.

### H53 pre-registration: EAT's banking flow on Arc Pro B70 (2026-10-20)

Written 2026-10-20, before any H53 measurement and before either H53 arm has run on a GPU. The
runner merged in #157; the block-level scorer is in this PR. This replaces the draft withdrawn
from #157 after its review (M5): block-level inference, at least six holds, B2 labelled as a
deviation from stock. Nothing here is a measurement except facts marked as read from an
image or a model card. The crescent shared rules and amendments C1, M1 (sampling and thinking
discipline) and A3 (rule L3) apply unless listed.

**Why.** Intel's EAT 1.1.0 (PR #21, unmerged, head `c86c42b`, "exercised on Battlemage B-series
cards such as B60/B70") adds a first-party Intel Arc XPU path; EAT 1.0.3 (`0d3438d`) has none.
H53 runs EAT's banking flow on the B70s two ways: k3d-gpu's own GPU choice against EAT's own XPU
path as it ships for a 27B model. With H50 (Xeon CPU) it gives a 2 x 2 of {k3d-gpu, EAT stock} x
{Xeon CPU, B70}; the two halves serve different models, so only the within-half comparisons are
claims.

**Model.** Qwen3.8-27B (dense, `Qwen3_5ForConditionalGeneration`, 64 layers, 3 linear-attention
to 1 full-attention, one MTP layer), per the box owner's directive. AG serves
`OpenVINO/Qwen3.8-27B-int4-ov` @ `7b20ea8c` (INT4_ASYM g128, 16 GB, bundles an MTP IR; its
tokenizer is capped at 131072 tokens, `model-cards/REFERENCE.md`). B2 serves `Qwen/Qwen3.8-27B`
@ `1d4bf0f2` (bf16, 55.6 GB). Both downloaded on the host and verified against the Hub's sha256
(`bench/eatflow/crescent/models.sh qwen38`). Sampling, identical for every arm and pinned by the
recording proxy (profile `agent_qwen38`): the card's instruct (non-thinking) setting,
`temperature` 0.7, `top_p` 0.8, `top_k` 20, `presence_penalty` 1.5, `max_tokens` 1024 (`min_p`
0 and `repetition_penalty` 1.0 are the engines' defaults and are not sent), thinking off by
`chat_template_kwargs: {"enable_thinking": false}`. With thinking off the template injects no
reasoning-effort message (REFERENCE.md 1.2), so no effort field is sent; both engines pass the
kwargs to the chat template (read from their source).

**Arms (all four B70s free: both SGLang pairs stopped for the H53 window).**

- AG: `k3d-gpu agent manifest` with the auto device and the default draft rule: OVMS 2026.4.1
  (`a928903f...`) on one GPU (`--target_device=GPU`, `gpu.intel.com/xe: 1`), no draft (the
  default draft rule applies on the CPU only, so the bundled MTP is not used: OpenVINO MTP is
  greedy-only, which H53's sampled setting excludes), prefix caching as k3d-gpu sets it.
  `K3D_GPU_AGENT_TOOL_PARSER=qwen3coder` (OVMS's parser for Qwen's XML tool calls; k3d-gpu sets no
  parser for a model outside its default family), no reasoning parser.
- B2, labelled **"EAT PR #21 nearest profile (Qwen3.6-27B) on Qwen3.8 weights, fp16"**. It is a
  deviation from EAT stock: #21 has no Qwen3.8 entry, so B2 is #21's path for a 27B Qwen: `core/helm-charts/vllm` at `c86c42b`,
  `xpu-values.yaml`, rendered with `LLM_MODEL_ID=Qwen/Qwen3.6-27B` (`xpu-qwen36-27b`, EAT's
  catalog entry for this model size and family; Qwen3.8-27B is not in #21's catalog) and pointed
  at the Qwen3.8 weights: fp16, block 64, `--gpu-memory-utilization 0.9`, `--enforce-eager`,
  `--no-enable-prefix-caching`, `--disable-sliding-window`, `--enable-auto-tool-choice
  --tool-call-parser qwen3_xml`, the profile's env, and #21's `tensor_parallel_size` 4 (all four
  cards). The image tag `vllm/vllm-openai-xpu:v0.28.0` is pinned to its index digest
  (`4756b66a...`). EAT's own GPU plugin install and NRI rework are not used; k3d-gpu's baked
  Intel GPU plugin (0.37.1) serves the cards. Why TP 4: 27.8 B bf16 parameters are 55.6 GB, so TP
  2 needs 27.8 GB per card against 28.8 GB (0.9 x 32 GB) before any KV cache.

**What B2 needs to run on k3d (findings, offered to EAT).**

1. Host runtime mounts. #21 mounts the host's `/usr/lib/x86_64-linux-gnu`,
   `/etc/OpenCL/vendors` and `/usr/lib/x86_64-linux-gnu/intel-opencl` by hostPath and prepends
   them to `LD_LIBRARY_PATH`. On k3d, hostPath is the node container's filesystem, which has no
   `/etc/OpenCL/vendors` and no `intel-opencl`, so the pod cannot start, and the prepend would load
   the node's own libraries. The image ships its own Level Zero and OpenCL runtime (read from the
   image), so `stage.py xpu` drops the three mounts and the prepend.
2. Read from the image before this pre-registration (no GPU used): `vllm/vllm-openai-xpu:v0.28.0`
   has no `/opt/intel/oneapi/setvars.sh`, which #21's command sources first, and its parser has no
   `--disable-log-requests` (it has `--enable-log-requests` / `--no-enable-log-requests`). The
   first stops B2 at start on any host if it acts as read; the second matters only for profiles
   that pass it (`xpu-qwen36-27b` does not).
3. Boot ladder, in a wiring smoke (no scored task) before the first scored B2 hold, each rung only
   when the boot before it fails with that rung's error, one change per rung, each kept for every
   later B2 hold: (a) stock with item 1 only; (b) drop `source /opt/intel/oneapi/setvars.sh
   --force &&`; (c) drop `--disable-log-requests` where present; (d) `--max-model-len 32768`
   (#21's own declared `max_model_len`, which its template never passes), if the boot fails on the
   KV cache for the model's 262144-token default. The minimal rung set is recorded. If B2 does not answer after (d), H53
   is INCONCLUSIVE (not deliverable) and the failure is the finding.

**Design: the arms in alternating holds, scored by block.** AG needs one card and B2 all four,
so they cannot be live at once; H53 cannot use the within-hold pairs of H49-H50. Each hold runs
one arm alone on B24 at N=1 under the typical load, every attempt preceded by L3's settle gap and
pre-window. Holds follow `pairs.ARM_SEQUENCE`: AG, B2, B2, AG, AG, B2, then B2, AG. Hold N must
run that arm or it refuses (`solo-hold.sh`, `agentbench solo`). Each hold is at most 40 min and
runs the 24 tasks in one order drawn from the registered seed (`H53-crescent-2026-10-20`). Block
k is holds 2k-1 and 2k, by hold number. A pair is one task id with an attempt in each hold of its
block; a task run in only one hold of the block makes no pair.

- **Unit of inference: the block.** For block k, d_k is the mean over its complete pairs of
  ok(AG) - ok(B2), where ok is within-budget success. A block counts when it has at least 8
  complete pairs. The statistic is the mean of d_k over the n counted blocks. Its 95% interval is
  the t-interval with n - 1 degrees of freedom. The exact sign-flip p over blocks is reported
  (2^n patterns). Pairs within a block share the hold's drift, so they are not independent.
- **Holds.** Six holds (three blocks) are run. If fewer than three blocks count after six, up to
  two more holds run (holds 7 and 8, block 4). No hold runs after eight.
- **Verdict** (`bench/eatflow/solo.py` `block_verdict`, `agentbench solo-report`). INCONCLUSIVE:
  any L3 gate fails, or fewer than three blocks count. SUPPORTED: the interval's lower bound is
  >= +30 points and AG's within-budget success is >= 0.6. REFUTED: its upper bound is < +30 points.
  Otherwise INCONCLUSIVE.
- **L3 gates** read over the pairs of every block: the balance gate on the pre-windows (which
  also sees drift between holds), the 16-pair minimum and at most 2 void pairs. The pair-level
  paired bootstrap verdict is reported as secondary, never as the verdict. So are the
  period-balanced estimate and a per-hold load summary.
- **Idle-arm check: not applicable.** No second server is live in a solo hold. It is reported as
  not applicable, not as a measured 0 (`analyze(solo=True)`).
- Disclosed: pairs cross holds, which H41/H42 showed exposes them to drift between holds. Block
  inference with few blocks is wide by design. With three blocks, df is 2 and t is 4.30, so only
  a large and consistent effect can read SUPPORTED or REFUTED.

**Cards and serving.** Both SGLang pairs (`qwen38-a-hr` on cards 0000:12:00.0 and 0000:48:00.0,
`qwen38-b-hr` on 0000:7c:00.0 and 0000:96:00.0, read from their processes' `/proc/<pid>/fd`)
are stopped for the H53 window with `bench/eatflow/crescent/gpu-serving.sh stop` (saves `docker
inspect`, refuses while aiperf runs, `docker stop`, never `rm`) and restarted after the last H53
hold with `gpu-serving.sh start`, which waits until `/health`, `/v1/models` and a 16-token chat
answer; every stop and start time is recorded, and `~/k3dgpu-bench/gpu-state.txt` says which
cards are in use. Each arm's cards are read inside its pod before the first request; AG must hold
one card and be on the GPU, B2 four. `xe` lines of `dmesg` are recorded before and after every
hold.

**Reference, not scored.** EAT's #21 default model (`google/gemma-4-31B-it`, TP 4) is not run:
it is a different model from both halves.

**Predictions (before measuring).** AG within-budget success >= 0.70 (0.6). B2 boots after at
most rung (b) (0.6) and answers (0.75). B2 >= 0.50 (0.5): TP 4 fp16 eager over PCIe (no switch
between the cards) should keep its turns under 120 s. Block mean AG - B2 in [-0.15, +0.30]
(0.5). Three blocks count (0.7). Verdict: INCONCLUSIVE (0.6, mostly the wide three-block
interval), REFUTED (0.3), SUPPORTED (0.1).

**Order of work.** The H53 window opens after H50's holds: both pairs stopped, the B2 boot-ladder
smoke, then holds 1-6 (and 7-8 if needed), then serving restored and verified. The window is
at most eight 40-min holds with both pairs stopped; `gpu-state.txt` says so throughout.

### H54 pre-registration: Gemma 4 26B-A4B QAT and Qwen3.8 microbenches on crescent (2026-10-20)

Written 2026-10-20, before any H54a, H54q, H54b, H54c or H54d measurement. Nothing here is a
measurement except the host facts listed under "Read before this pre-registration". The
runner (deploy renders, phase driver, quality grader, scorer) lands in a dated amendment with
regression tests, merged before the first H54 hold. Every recorded verdict keeps its host and
rule. PR #157 (amendment M1, H53) moves H50, H50o and H51 to Gemma 4 QAT and names MTP as
H54's factor; H54 does not change those rows.

**Why.** H50 and H53 compare stacks inside EAT's banking flow. They do not say how fast each
engine decodes these models on this host, whether the int4 builds keep the QAT quality, or
whether splitting a MoE across the Xeon's DRAM and a B70 pays. H54 measures that, engine by
engine, each at its best documented setting, with MTP on and off where the model ships a
drafter.

**Models (box owner's directive: CPU arms use only the Gemma 4 QAT family or Qwen3.8).**

- Gemma 4 26B-A4B-it (QAT): 25.2B total, 3.8B active MoE (128 experts, 8 routed, 30 layers,
  hidden 2816, expert width 704, tied 262,144-token embedding; read from `config.json`).
  `google/gemma-4-26B-A4B-it-qat-q4_0-unquantized` @ `f1e06dc5` (bf16, 51.6 GB);
  `google/gemma-4-26B-A4B-it-qat-q4_0-gguf` @ `d1c082be` (Q4_0, 15.6 GB);
  `google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant` @ `95371415` (the QAT MTP
  drafter; the card requires a drafter of the target's precision); `OpenVINO/gemma-4-26b-a4b-it-int4-ov`
  @ `4e5885c3` (INT4_ASYM g128 r1.0 from the base, non-QAT model, per its README). All four
  are on crescent under `~/k3dgpu-gemma/models`, each with a `.pinned-revision` file and
  sha256 in `~/k3dgpu-gemma/weights.sha256`. `google/gemma-4-26B-A4B-it` is not downloaded.
- QAT-matched IR OVQ, built for H54: `optimum-cli export openvino` from the QAT bf16 weights,
  `--weight-format int4 --sym --group-size 32 --ratio 1.0` (Q4_0 is symmetric with 32-weight
  blocks), openvino 2026.4.1, nncf 3.4.0, optimum-intel 2.2.0, as H50's A1 export. The exact
  command, venv freeze and IR sha256 are recorded. OVMS documents no Gemma 4 MTP path, so no
  drafter IR is built.
- Qwen3.8-27B (dense, 27.8B, MTP head, thinking on by default): `Qwen/Qwen3.8-27B` @ `1d4bf0f2`;
  `OpenVINO/Qwen3.8-27B-int4-ov` @ `7b20ea8c` (bundles `openvino_mtp_model.xml`;
  `Intel/qwen3.8-27B-int4-ov` @ `53292eee` has no weights and its script downloads this IR, so
  it is not a separate arm); `Intel/Qwen3.8-27B-q4km-AutoRound` @ `4b30b30e` (Q4_K_M GGUF,
  16.8 GB); `RedHatAI/Qwen3.8-27B-INT4` @ `91bd022d` (w4a16, with `model_mtp.safetensors`).
  `~/models/Qwen3.8-27B-AWQ-INT4` belongs to the live SGLang pair and is not used.
- Quality set: `openai/gsm8k` config `main`, test split, items 0-199 in file order, @ `740312ad`.

**Source rule (box owner's).** For each engine the arm uses the best published variant in
this order: the model author (`google/`, `Qwen/`) and Intel's own (`Intel/`, `OpenVINO/`),
then `RedHatAI/`, then `88plug/` or a build made here from official weights, then any third
party. A lower-ranked variant replaces a higher-ranked one only when it is measured better on
this host: median N=1 decode at least 5% higher at a quality score no more than 2 items of 50
below. The decision is recorded per arm. A self-built variant that wins (OVQ, for example) is
reported as a candidate 88plug release; nothing is published. EAT-stock arms stay stock.

**Arms, CPU (Xeon 6741P), Gemma 4 QAT (H54a).**

| Arm | Stack | Model | Rank | MTP |
|---|---|---|---|---|
| B1g | EAT stock: `vllm/vllm-openai-cpu:v0.24.0@sha256:b0e6f860...`, `xeon-values.yaml` at `0d3438d` entry `cpu-gemma4-26b-a4b` (flags at lines 411-432, `VLLM_CPU_KVCACHE_SPACE` 40), shipped CPU requests and threads | QAT bf16 (the one deviation: EAT names `google/gemma-4-26B-A4B-it`); identical to M1's B1 | stock | none (stock) |
| B3g | EAT's OVMS chart at `0d3438d`, stock values, `openvino/model_server:2025.4` pinned to its digest | `OpenVINO/gemma-4-26b-a4b-it-int4-ov`, pulled by the chart | stock | none |
| A1o | `k3d-gpu agent manifest`, `K3D_GPU_AGENT_DEVICE=CPU`, OVMS `2026.4.1@sha256:148e3fe8...` | OVB | official | none documented |
| A1g | A1o's stack | OVQ | self-built | none documented |
| Lg | `ghcr.io/ggml-org/llama.cpp:server-b11749@sha256:9fbaa06d...`, `-t 48 -Cr 0-47 --cpu-strict 1 --jinja` | Google QAT Q4_0 GGUF | official | off |
| Lg-mtp | Lg with `--spec-type mtp` and the default draft length | plus the QAT drafter converted to GGUF here with b11749's `convert_hf_to_gguf.py` (no official GGUF drafter exists) | self-built drafter | on |
| Vq | `vllm/vllm-openai-cpu:v0.32.0@sha256:ffaba398...`, B1g's flags, `VLLM_CPU_OMP_THREADS_BIND=0-47`, attention backend named explicitly | QAT bf16 | official | off |
| Vq-mtp | Vq with `--speculative-config '{"method":"gemma4_mtp","model":<QAT drafter>}'` at vLLM's default `num_speculative_tokens` for the method | QAT bf16 and the QAT drafter | official | on |
| Vi | Vq's stack, probe first | `Intel/gemma-4-26B-A4B-it-int4-AutoRound` @ `edff6272` (int4 sym g128 from the base model) | official (Intel) | off |
| Sg | `lmsysorg/sglang:v0.5.22-xeon@sha256:4e5c413e...`, `--device cpu --attention-backend intel_amx`, TP 1, `SGLANG_CPU_OMP_THREADS_BIND=0-47`, probe first | QAT bf16 | official | off |

Each engine flag above is checked against that engine's source at its pinned version in the
runner amendment; a flag that does not exist there is dropped and recorded, never guessed.
A1o and A1g are k3d-gpu's own pick: its CPU request and thread settings as it writes them.
Every core of the 6741P is a P-core and it has one NUMA node, so k3d-gpu applies no balloon
(shared rule 2 of the H50-H52 pre-registration); "P-core pinning" is moot on this host and is
recorded as such.

**Arms, B70, Qwen3.8-27B (H54b).** X1q: `k3d-gpu agent manifest` with the auto device (OVMS
`2026.4.1-gpu@sha256:a928903f...`, one card), `OpenVINO/Qwen3.8-27B-int4-ov`. X1q-mtp: X1q with
the bundled MTP model (`--draft_model_path`, `num_assistant_tokens` 5, prefix caching off, as the
OVMS speculative-decoding demo at v2026.4.1 documents). Sq: `intel/llm-scaler-vllm:1.5@sha256:52218ad8...`,
`Qwen/Qwen3.8-27B` with online FP8, TP 1 (probe first; at TP 2 it is reported but not eligible
for the one-card rule). Sq-mtp: Sq with method `qwen3_5_mtp` at llm-scaler's documented
setting. Rq: Sq's stack with `RedHatAI/Qwen3.8-27B-INT4` and its MTP head, probe first. Lsq:
`ghcr.io/ggml-org/llama.cpp:server-intel-b11749@sha256:ae2d41ba...`, `Intel/Qwen3.8-27B-q4km-AutoRound`,
`-ngl 99`, one card; Lsq-mtp only if that GGUF carries the MTP tensors (read at load). B2q: EAT
PR #21 at `c86c42b`, `xpu-values.yaml` profile `xpu-qwen36-27b`'s flags with `LLM_MODEL_ID`
pointed at the Qwen3.8-27B weights, `vllm/vllm-openai-xpu:v0.28.0@sha256:4756b66a...`, TP 2 (the
profile's TP 4 needs all four cards), with PR #157's H53 boot ladder (a)-(d) and nothing else.
Thinking is off for every Qwen arm (`chat_template_kwargs: {"enable_thinking": false}`).
Described if time remains, never gated: Gemma 4 QAT on one card as X1g (OVMS GPU, OVQ), X1o
(OVB), B2g (EAT #21's Gemma profile flags on the QAT bf16 weights, TP 2) and Sgg (llm-scaler
1.5, QAT bf16 online FP8, `gemma4_mtp` with the QAT drafter, `num_speculative_tokens` 4).

**Dropped, with the reason.** KTransformers: no Gemma 4 support, and its GPU side needs CUDA.
Upstream vLLM XPU v0.32.0: Gemma 4 26B-A4B unvalidated upstream, and llm-scaler 1.5 is
Intel's own B-series vLLM with both models validated. SGLang XPU: no author, Intel or RedHat
int4 checkpoint validated on it for Qwen3.8-27B; the known-good config on this host is the
live pair's third-party image. `RedHatAI/gemma-4-26B-A4B-it-FP8-dynamic`: llm-compressor FP8
is unsupported on x86 CPU and Intel GPU in vLLM. `RedHatAI/gemma-4-26B-A4B-it-speculator.eagle3`:
trained against the base model, and the card asks for a same-precision QAT drafter for a QAT
target. `RedHatAI/Qwen3.8-27B-speculator.dspark`: no ranked engine documents DSpark on XPU at
its pin (llama.cpp's `--spec-type dspark` needs a GGUF draft that does not exist). NVFP4/MXFP4
variants: NVIDIA-oriented. Qwen3.8-Flash-Next for H54c: vLLM XPU raises `NotImplementedError`
for `Qwen4Exp`, vLLM CPU runs CUDA-only Triton code, OVMS has no IR, and the only Intel-runnable
build is the third-party `unsloth/Qwen3.8-Flash-Next-GGUF` @ `766911a6` (lowest source rank);
its smallest quant above 2 bits is 82 GB, which would take `/` (240 GB free of 3.4 TB on
2026-10-20) to about 97%, the hold abort line. H54c therefore runs Gemma 4 QAT. A Flash-Next
hybrid row may be registered later if the disk frees.

**Measurement protocol (every H54 microbench arm).**

1. One arm serves at a time, in its own pod in one fresh cluster per hold, `k3dgpu-g-h54<row>-<hold>`,
   created with amendment C1's `K3D_GPU_K3S_ARGS`. The previous arm's server is deleted, and
   its CPU reads idle, before the next boots. An arm not answering its model list and a
   16-token chat within 900 s (1,500 s for bf16 vLLM loads) is "not deliverable" for that hold
   and is recorded with its log; probe-first arms get one boot attempt.
2. Phases, from the client in the hold wrapper: 3 warmup requests; N=1: 8 requests; N=4: 16;
   N=16: 32. Each request: `bench/llm_load.py` default prompt with `--unique-prompts`,
   `max_tokens` 256, `ignore_eos`, temperature 0. TTFT: 4 requests at N=1 on a fixed
   1,000-token prompt (file and sha256 in the runner amendment), `max_tokens` 16. Decode tok/s
   per request is `llm_load.py`'s `decode_tokens_per_s`; aggregate tok/s is the phase's output
   tokens over its wall time.
3. Energy: `bench/energy.sh` on RAPL `package-0` per phase, plus 60 s of idle before each arm.
   J/output token is reported gross and net of the idle power. GPU energy comes from the `xe`
   hwmon energy counter if the card exposes one, else it is not measured. Not gated.
4. Quality spot-check: GSM8K items 0-49, zero-shot through each arm's chat template, the prompt
   "Solve the problem. End with a line 'Answer: <number>'.", temperature 0, `max_tokens` 1024,
   16 in flight, thinking off. Score: the last number in the reply equals the dataset's
   `####` answer after removing commas. The prompt file and grader land in the runner amendment.
5. Achieved bandwidth, reported beside every CPU arm: active bytes per token (from the model
   file's tensor table: all non-expert tensors plus 8/128 of the routed-expert tensors) times
   median N=1 decode, against the STREAM triad of 305 GB/s measured on 2026-10-20 (48 threads).

**Chosen arm (H54a, H54b).** Among arms passing the quality gate (score at least the stock
arm's minus 0.10, i.e. 5 of 50), the highest median N=1 decode, then the source rule above.
The scorer makes the selection from the recorded data, never by hand.

**H54q.** Variants R (= Vq's config), OVQ (= A1g), OVB (= A1o), Lg, and Vi if deliverable
(descriptive). Items 0-49 reuse the H54a runs of those exact configs; items 50-199 run in one
hold. Paired per-item scoring, rules in the row.

**H54c.** Bytes from the Q4_0 GGUF's tensor table: B_exp = 8/128 of the bytes of every
`ffn_(gate|up|down)_exps` tensor; B_rest = every other per-layer tensor plus the output matrix
(the tied `token_embd`); B_tot = B_exp + B_rest. t_Cg and t_Gg are the reciprocal median N=1
decodes of Cg (`-ngl 0`) and Gg (`-ngl 99`), both in the server-intel image so the binary is
constant; each part of the hybrid is assumed to move its bytes at its own device's measured
rate, plus 30 us for each of the two host-device crossings in each of the 30 layers. The
prediction and the measured Hg are both recorded. Reported beside, not gated: Cg's implied
GB/s against STREAM's 305. Disclosed confound: llama.cpp's SYCL XMX path for MoE covers Q4_K
but not Q4_0 (`docs/backend/SYCL.md` at b11761), so Gg and Hg's GPU part take the library GEMM
path.

**H54d.** A* is H54a's chosen arm. If A* is H50's A1 config (PR #157's A1: OVMS CPU, OVQ,
`gemma4` tool parser, no draft), H54d does not run and is recorded INCONCLUSIVE (not run, H50
covers it). Otherwise H50's procedure under amendment M1's sampling and arm B1, with A*'s
tool parser set to its engine's Gemma 4 parser, cluster `k3dgpu-g-h54d`. A tool-call wiring
smoke (no scored task) precedes the first hold.

**Shared box, holds and load.** Crescent is shared: two live SGLang Qwen3.8-27B containers
(`qwen38-a-hr`, `qwen38-b-hr`) on all four B70s, an `aiperf010` load generator, observability,
and two other campaigns (`~/k3dgpu-bench`, `~/k3dgpu-xhost`). This campaign creates only under
`~/k3dgpu-gemma` and clusters `k3dgpu-g-*`, and never stops, starts or changes a container,
image, volume or file it did not create. GPU arms use only cards that
`~/k3dgpu-bench/gpu-state.txt` (written by the main campaign, which owns the SGLang pair's stop
and restore) lists free at hold start; the card ids used are recorded. If no card is free
before the campaign ends, H54b and H54c are INCONCLUSIVE (not run).

- Lock: every hold, and every conversion or download that loads the CPU, runs inside
  `flock -w 21600 ~/k3dgpu-bench.lock`. Conversions are not holds.
- Disk: `df /` is read before each hold; above 97% the hold does not start; the hold wrapper's
  watchdog stops it above 97%.
- Load: no background load is added (a microbench on the box as found). The H50 hold-start rule
  applies (`load1` > 12 or an aiperf process: wait in 2-min polls up to 60 min). `serving.py`
  samples beside every hold. A phase is void if an aiperf process ran during it or the
  non-campaign containers' mean CPU over it exceeds 4 CPU-s/s; a void phase is re-run once in
  the same hold, and a second void leaves that arm's metric missing.
- CPUs: no node cpuset (shared rule 2). Stock arms keep their shipped requests, limits and
  thread settings. Optimized CPU arms run 48 inference threads, one per physical core on CPUs
  0-47 (STREAM peaked at 48 threads, not 96), set with each engine's own knob as listed.
- Caps: at most 10 holds in the campaign, each at most 40 min (`timeout -k 30 2400`): H54a 3,
  H54q 1, H54b 2, H54c 1, H54d 3. A void or failed hold counts. Order: H54a, H54q, then H54b
  and H54c when a card window opens, then H54d. Arms not reached in a row's cap are recorded
  as not run; a row whose stock arm or chosen arm is missing is INCONCLUSIVE.
- Cleanup: the hold's cluster is deleted after every hold, also on failure.

**Read before this pre-registration (2026-10-20, not scored).** STREAM (jeffhammond/STREAM
@ `6703f750`, 400M-element arrays, gcc 15.2.0 `-O3 -march=native`): triad 305.2 GB/s at 48
threads, 286.5 GB/s at 96, under the lock; Intel MLC v3.13 all-reads 371.9 GB/s, idle latency
115.5 ns, prefetchers left on (no root). Files in `~/k3dgpu-gemma/bw.log`. To make room for the
models, `~/openrouter-crescent-island.bak-pre-battlemage` (137.3 GB) was deleted on the box
owner's authorization after checking that no symlink, unit or container mount referenced it;
its listing is kept in `~/k3dgpu-gemma/deleted-bak-listing.txt.gz`.

**Order and changes.** A protocol change is a dated amendment merged before the data it
governs. A wiring smoke runs no scored request, is discarded and is disclosed in the run
section.

### H58 pre-registration: the zero-touch thesis on crescent (2026-10-20)

Written 2026-10-20, before any H58 run. This is the user's core claim: on Intel hardware, k3d-gpu
needs no flag, no manual step and no edit, and gets within tolerance of the best hand-tuned
configuration. The row was first drafted as "H55". PR #165 took that id for gated admission, so
this row is H58. H56 (PR #168) and the H57d* family (PR #171) are also taken.

**Order.** H58 runs last on crescent, after H50, H50o, H53 (if pre-registered), H51 and H54. Its
runner lands in a dated amendment with regression tests, merged before the first H58 run. That
amendment pins the Hub id and revision of the Qwen3.8-Flash-Next W4A16 (Intel AutoRound) model. It
also records the commits of the model optimizer (PR #163) and of the fit-and-guard layer
(branch `claude/fit-and-guard`), both merged first. A run that needs any code change made during
it is not valid.

**Clean state.** No k3d-gpu cluster of this campaign exists, and the run's model cache is empty
except for the downloads the run itself makes. The host configuration is as left by H52b's revert.
The pre-existing services (qwen38-a-hr, qwen38-b-hr, dynamo, llm-d, aiperf, observability) run as
found. They are recorded at the start (`docker ps`, `docker inspect` restart counts, the
serving.py sampler) and never touched.

**(a) and (b): flags, steps and edits.** The script runs only the documented commands, as a new
user would: `k3d-gpu up`, then `k3d-gpu agent up --model google/gemma-4-26B-A4B-it`, then EAT's
banking flow through k3d-gpu. It records every command, flag, environment variable and file edit
in `steps.jsonl`. A flag is any option or variable beyond those commands. A manual step is any
action the docs do not name: a file edited, a value copied by hand, a retry, a sudo step not
printed by k3d-gpu. Predicted count: 0 and 0. The count is the verdict input, not a judgement.

**(c) Within tolerance of the best.** The baseline is the best hand-tuned arm measured on this
host for the same model and flow. That is H50's A1 if H50 ran a valid hold, else H54's best
Gemma 4 arm. The baseline is named, with its section, before the H58 run starts. Measured the
way the baseline was: single-stream decode tok/s with its pinned sampling, and B24
within-budget success inside EAT's flow. Tolerance: decode >= 0.90x the baseline's, and success
within 5 points. No valid baseline: INCONCLUSIVE.

**(d) Engines.** `k3d-gpu detect` and the node labels must name every engine H52 and H52b found
on crescent: the four Arc Pro B70s (xe), AMX, and DSA, IAA and QAT where H52b made them usable.
Each one missing is a failure, unless H52b recorded it as unusable on this host.

**(e) Placement by the fit.** With no flag, `agent up --model` for Qwen3.8-Flash-Next W4A16 must
land on an experts-in-DRAM placement. Gemma 4 26B-A4B and Qwen3.8-27B must land on the GPU or the
CPU, as the fit computes from live free memory. The placement each run chose, and the fit's
inputs, are recorded from k3d-gpu's own output. A placement the fit did not choose is a failure.

**(f) The memory squeeze, run last of all, with the qwen38 pairs serving.** A `stress-ng --vm`
job runs in its own cgroup (pinned stress-ng under `~/k3dgpu-bench/bin`). It is sized to push
MemAvailable below the guard's threshold while our agent pods hold their model. Predictions: the
guard lowers admission, and nothing outside our pods is OOM-killed. That covers the host and
every pre-existing container. Checks: `dmesg` oom-kill lines, `docker events` (oom, die), and the
`docker inspect` restart counts before and after. Crescent has no swap, so "swaps hard" cannot
occur; it is recorded as not applicable. **Stop rule:** the squeeze stops at once if any
pre-existing container fails its health check, or if its sampled latency (serving.py) doubles
against the run's own pre-squeeze window. A stop is recorded; with no OOM kill it still counts for
the 0-kill clause, and the admission clause is then INCONCLUSIVE.

**Verdict.** SUPPORTED: (a) through (f) all hold. REFUTED: any clause fails on a valid run.
INCONCLUSIVE: as above. Each clause is reported on its own, whatever the verdict.

### H54 amendment R1: the H54a runner and the flag checks (2026-10-20)

Recorded 2026-10-20, before any H54a measurement. The pre-registration's runner amendment for
H54a: what runs each arm, the flags read against each engine's source at its pin, and the
details the pre-registration left to the runner. Wiring smokes ran first (disclosed in the H54a
run section, discarded, no scored request). Nothing here changes a row's statistic, gate,
threshold, cap or prediction.

**Files.** `bench/h54/`: `hold.sh` (lock `flock -w 21600`, the H50 hold-start rule, the 97% disk
line, `timeout -k 30 2400`, a disk watchdog, cluster deletion at the end, also on failure),
`hold-run.sh` (cluster, then each arm), `arm.sh` (one arm: render, readiness, phases, quality,
teardown), `h54.py` (renders, GSM8K grader, void rule, tensor-table bytes, `score-a`),
`k3d-shim.sh`, `reproduce.sh` (the Reproduce header of every hold), `convert.sh` (the two builds,
under the lock), `ov-export-venv.txt` (the export venv pins), `gsm8k-test-0-199.jsonl`
(`openai/gsm8k` `main` test @ `740312ad`, items 0-199 in file order; sha256 `45e54be2...`) and
`ttft-prompt.txt` (999 Gemma 4 tokens by the checkpoint's `tokenizer.json`; sha256 `b10cd3c7...`).
Regression tests: `bench/h54/test_h54.py` (grader, void rule, byte counts, bootstrap, the source
rule, every verdict branch, renders and flags, the shim), run in CI.

**Cluster.** `k3d-gpu up` with `K3D_GPU_CLUSTER=k3dgpu-g-h54a-<hold>`, amendment C1's
`K3D_GPU_K3S_ARGS`, `K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true`
(B1g's affinity, as H50), `K3D_GPU_VENDORS=amx` (no GPU device plugin: H54a touches no card; with
the device named CPU, `agent manifest` writes the same pod under `auto`, since only the AMX label
reaches it, as a node selector) and the node image k3d-gpu names, pinned:
`cryptoandcoffee/k3d-gpu:0.1.63@sha256:b6fc46f6...`. k3d-gpu on main has no host-volume knob and
copying the 51.6 GB bf16 weights into the node would cross the 97% line, so a k3d shim in front of
the pinned k3d v5.9.0 adds one volume to `k3d cluster create k3dgpu-g-*`: `~/k3dgpu-gemma/models`
at `/var/apb-models`, the hostPath `bench/apb/stage.py` already points every staged manifest at.
This changes where the node reads files, not what any arm serves.

**Flags read at each pin.**

- llama.cpp b11749 (`common/arg.cpp`, `common/speculative.cpp`): `-t`, `-Cr`/`--cpu-range`,
  `--cpu-strict` and `--jinja` exist. `--spec-type mtp` does not: b11749 names the type
  `draft-mtp`, so Lg-mtp runs `--spec-type draft-mtp -md <drafter GGUF>` at the default draft
  length (`--spec-draft-n-max` 3). The drafter GGUF is b11749's `convert_hf_to_gguf.py`
  (`Gemma4AssistantForCausalLM` is registered in `conversion/gemma.py`) to bf16, then b11749's
  `llama-quantize` to Q4_0, the target's precision (the card asks for a same-precision QAT
  drafter), both in `ghcr.io/ggml-org/llama.cpp:full-b11749@sha256:aefb375a...`. Everything else
  is the server's default (context from the model, its default slot count).
- vLLM v0.32.0: `--attention-backend` exists; on CPU, `platforms/cpu.py` returns `CPU_ATTN`
  whatever is selected, and `Gemma4Config` only sets `TRITON_ATTN` when the backend is unset, so
  the named backend is `CPU_ATTN`. `VLLM_CPU_OMP_THREADS_BIND` exists (`envs.py`). For a
  `gemma4_assistant` drafter `config/speculative.py` sets `n_predict` 1 and defaults
  `num_speculative_tokens` to it, so Vq-mtp passes no length.
- SGLang v0.5.22: `--device cpu`, `--attention-backend intel_amx` (the x86 default) and `--tp 1`
  are fields of its argument groups; `SGLANG_CPU_OMP_THREADS_BIND` is documented
  (`docs/docs/hardware-platforms/cpu_server.mdx`), which also says that with it set the memory
  per rank cannot be determined and `--max-total-tokens` should be set. Sg adds
  `--max-total-tokens 65536` so its KV pool cannot take the shared box's memory (B1g's bound is
  `VLLM_CPU_KVCACHE_SPACE` 40 GiB).
- OVMS (A1o, A1g): `agent manifest` writes `openvino/model_server@sha256:a928903f...`
  (2026.4.1-gpu, which carries the CPU plugin) for every device. The pre-registration's table
  names `148e3fe8...`, the CPU-only tag of the same release; its rule for these arms is
  "k3d-gpu's own pick ... as it writes them", so they run `a928903f...` and `148e3fe8...` is not
  used. Model ids: `OpenVINO/gemma-4-26b-a4b-it-int4-ov` (OVB, the download directory renamed to
  that path) and `k3dgpu/gemma-4-26b-a4b-it-qat-int4-sym-g32-ov` (OVQ); `--pull` finds each
  directory and downloads nothing. k3d-gpu sets no tool or reasoning parser for a model outside
  its default family. Its `--cache_dir` compiled-model cache is deleted after each arm.
- B1g as EAT's playbook installs `cpu-gemma4-26b-a4b` (`core/playbooks/deploy-inference-models.yml`
  at 0d3438d): release `vllm-gemma4-26b-a4b-cpu`, `chatTemplateConfigMap.enabled=true` (the
  v0.24.0 image does not contain `tool_chat_template_gemma4.jinja`; the chart mounts it) and
  `hostIPC=true`, with monitoring and ingress off (they need EAT's operators), the tag pinned to
  its digest, `--max-model-len 8192`, the QAT weights staged by `stage.py vllm`.
- Vq, Vq-mtp, Vi: B1g's render with only the image (v0.32.0), `--attention-backend CPU_ATTN`,
  `VLLM_CPU_OMP_THREADS_BIND=0-47`, the model (Vi) and the speculative config (Vq-mtp) changed;
  its ConfigMaps, chat template and every other flag kept.
- B3g: EAT's OVMS chart at 0d3438d, `modelSource=OpenVINO/gemma-4-26b-a4b-it-int4-ov`,
  `image.tag=2025.4@sha256:c3707200...`, ingress and APISIX route off; its init container pulls
  the model.

**OVQ.** `convert.sh`: the pre-registration's command, `optimum-cli export openvino --model <QAT
bf16> --weight-format int4 --sym --group-size 32 --ratio 1.0`, with the venv in
`ov-export-venv.txt`, plus `--task image-text-to-text`: run as written under the lock it stopped
with optimum's "Cannot infer the task from a local directory yet, please specify the task
manually", and `image-text-to-text` is OVB's pipeline (its card's `pipeline_tag`), so A1o and
A1g load the same IR layout. Backup precision is optimum-intel's default, as for OVB. Read from
OVB's own `openvino_config.json` while checking this: its `lm_model` entry records `awq`, group
size 64, asymmetric, the router layers ignored, where its card says INT4_ASYM group 128; recorded,
not acted on (OVB is the published build as shipped).
PR #157's A1 export (`--task text-generation-with-past --backup-precision none` and a grid snap)
is a different build; it is not merged and is not used here. For H54d's rule, "H50's A1 config"
stays as the pre-registration defines it (OVMS CPU, OVQ, `gemma4` parser, no draft).

**Per arm (`arm.sh`).** An arm starts only with at least 900 s of the hold left, else it is
"not reached" in that hold and moves to the next hold's front. Readiness is the model list and a
16-token chat, polled every 10 s up to the arm's limit (1,500 s for B1g, Vq, Vq-mtp; 900 s
otherwise), cut to the hold's time left minus 600 s; past the limit the arm is "not deliverable"
(or "not reached" when the hold's time cut it). Then: 60 s idle (RAPL `package-0` through
`energy.sh` and sudo), 3 warmup requests (discarded), N=1 8, N=4 16, N=16 32, TTFT 4 at N=1 on
`ttft-prompt.txt` with `max_tokens` 16, each phase under `energy.sh` and checked against
`serving.jsonl` (`h54.py void`: any aiperf process in a sample overlapping the phase, or mean
non-campaign CPU above 4 CPU-s/s), a void phase re-run once. Every request has
`--unique-prompts`, so no phase hits a prefix cache, the TTFT phase included. Then GSM8K items
0-49: one non-streamed chat per item, the pre-registered prompt, temperature 0, `max_tokens`
1024, `chat_template_kwargs: {"enable_thinking": false}` for every arm, 16 in flight; score: the
last number in the reply equals the `####` answer, commas removed, compared as numbers. Teardown:
the arm's objects and PVCs deleted, its image removed from the node, OVMS's cache cleared, and
the node container's CPU under 1 CPU-s/s over 10 s (at most 180 s) before the next arm.

**Scorer (`h54.py score-a`).** B1g's median N=1 decode and score are the base; the gate is
score >= B1g's - 0.10. Candidates: optimized arms (not B1g, B3g) with a measured N=1 phase that
pass the gate. Chosen: the fastest candidate of the best source rank present (0 author or Intel,
1 RedHatAI, 2 built here); a lower-ranked candidate replaces it only at >= 5% higher median N=1
decode and no more than 2 of 50 items fewer. Ranks: Lg, Vq, Vq-mtp, Vi, Sg, A1o 0; A1g and
Lg-mtp 2 (self-built IR, self-converted drafter). The CI: 10,000 resamples (seed 1) of each arm's
8 N=1 requests on its own, percentile 2.5 and 97.5 of the ratio of medians. Verdict branches as
the row states. An arm measured in a later hold replaces its not-reached record.

**Hold plan (cap 3).** Hold 1: B1g, Lg, Lg-mtp, A1g, A1o. Hold 2: arms not reached in hold 1,
then Vq, Vq-mtp. Hold 3: arms not reached, then Vi, Sg, B3g. Arms not reached in hold 3 are not
run.

### H54 amendment R2: the drafter conversion, Vq-mtp's draft length, the OVQ directory and two record details (2026-10-21)

Recorded 2026-10-21, before any H54a measurement, after the wiring smokes (no scored request,
discarded; disclosed in the H54a run section). Three runner details the smokes showed R1 had
wrong, and two record details. Nothing here changes a row's arms, models, statistic, gate,
threshold, cap or prediction.

- **Lg-mtp's drafter GGUF.** R1's conversion stopped in `ghcr.io/ggml-org/llama.cpp:full-b11749`:
  the image's transformers 4.57.6 cannot read the drafter's `tokenizer_config.json`
  (`extra_special_tokens: []`, the transformers 5 format): "AttributeError: 'list' object has no
  attribute 'keys'". `convert.sh` now runs the same image's `convert_hf_to_gguf.py` with
  transformers 5.5.4 (the OVQ export venv's pin) installed in the throwaway container, then the
  image's `llama-quantize` to Q4_0 as before. The script, gguf-py and quantizer are b11749's;
  only the tokenizer loader's library changes. The build's `manifest.json` records it.
- **Vq-mtp's draft length.** vLLM v0.32.0's `config/speculative.py` rewrites a
  `gemma4_assistant` drafter to `gemma4_mtp` with `n_predict` 1, then reaches the MTP branch's
  `num_speculative_tokens > 1` check before the line that defaults `num_speculative_tokens` to
  `n_predict`; with no length given the server exits with "TypeError: '>' not supported between
  instances of 'NoneType' and 'int'". Vq-mtp therefore passes `"num_speculative_tokens": 1`, the
  value that default line sets, so it is vLLM's default length, named.
- **OVQ's directory mode.** OVMS's `--pull` init container runs as uid 5000 and writes
  `graph.pbtxt` into the model directory; OVB's directory was mode 777, OVQ's (built by
  `convert.sh` as the campaign user) 775, so A1g's init container failed ("Unable to open file
  .../graph.pbtxt") and A1g was not deliverable in the smoke. OVQ's directory is now mode 777,
  as OVB's. No file in the IR changed.
- **The Reproduce header's EAT commit.** `reproduce.sh` read the EAT tree from `${G}/eat-src`, a
  path that does not exist, so the smokes' headers have an empty `eat:` line. It now reads the
  tree `hold.sh` exports as `EAT` (`~/k3dgpu-gemma/h54a/eat-src`, at `0d3438d7`).
- **The GPU device plugin.** R1 says `K3D_GPU_VENDORS=amx` leaves no GPU device plugin. The
  smokes' clusters still ran the `intel-gpu-plugin` DaemonSet (it advertises cards to the node;
  it runs nothing on them). No H54a pod requests a `gpu.intel.com` resource (checked in every
  smoke arm's `deploy.yaml`), so no card is allocated to or used by any arm. Recorded, no change.

### H54 amendment R3: an arm whose server stops answering after readiness (2026-10-21)

Recorded 2026-10-21 during H54a hold 1, before hold 2. It governs holds 2 and 3 and the status
of hold-1 records. Nothing here changes a row's arms, models, statistic, gate, threshold, cap or
prediction.

**What prompted it.** In hold 1 (`k3dgpu-g-h54a-1`, started 08:17:35Z) B1g was ready after 219 s,
answered 2 of its 3 warmup requests (256 tokens each), and stalled on the third after 166 output
tokens. About 300 s later vLLM v0.24.0's engine stopped: `TimeoutError` in
`shm_broadcast.dequeue`, then `EngineDeadError`, and the server exited at 08:28:20Z. Every scored
request (N=1, N=4, N=16, TTFT) and all 50 GSM8K items got "Connection refused". No void sample
(load1 fell to about 5 during the stall, no aiperf process). `arm.sh` still wrote `measured`,
because it only checked that `quality.json` existed. When this was written, only B1g's records of
hold 1 had been read; the hold's other arms were still running and none of their numbers had been
looked at.

**Rule.**

- `arm.sh` writes `measured` only when `h54.py served` passes: the N=1 phase completed at least
  one request with a decode rate, and not every quality item errored. Otherwise the arm is
  `failed` with the reason "server stopped answering".
- A hold carries to the next hold's front its not-reached arms (R1) and, once per arm, an arm
  failed because its server stopped answering (`h54.py carry`). An arm that stops answering twice
  is not carried again. A render, apply or readiness failure is never carried. The rule is the
  same for every arm, stock or optimized.
- Before each hold and at the end, `h54.py recheck` applies `served` to every record marked
  `measured`, so hold 1's B1g record is rewritten as `failed` (the old status is kept in the record
  as `was`). It reads only whether requests completed, not any decode rate or score.
- `bench/h54/campaign.sh` runs R1's hold plan with this carry, so hold 2 starts with the arms hold 1
  carries, then Vq and Vq-mtp. The cap stays 3 holds: an arm carried into hold 2 can push R1's
  later arms to hold 3 or out of the cap, recorded as not run.
- The stall itself is a result about the stock arm and is reported in the H54a run section.

### H54 amendment R4: the void rule's CPU part reads the arm's idle window (2026-10-21)

Recorded 2026-10-21 after H54a hold 1, before hold 2. It governs holds 2 and 3. Nothing here
changes a row's arms, models, statistic, gate, threshold, cap or prediction. A hold 2 was started
at 08:50:49Z and stopped by hand at 08:51Z while it waited on the hold-start rule (load1 29), before
its cluster existed and before any request; it is not counted as a hold. Its empty directory is
kept as `hold2-stopped-prestart-0851Z`.

**What prompted it.** The pre-registration voids a phase when the non-campaign containers' mean
CPU over it exceeds 4 CPU-s/s (H50's shared rule 3). In hold 1 every phase of Lg and Lg-mtp was
void on that part, on both tries, with no aiperf process. The non-campaign CPU is almost all the
live SGLang pair (`qwen38-a-hr`, `qwen38-b-hr`), idle (no request), and it follows the arm's own
load: 2.36-2.42 CPU-s/s with no arm load (B1g's boot and idle window; the gap between Lg and
Lg-mtp, 08:41-08:42Z), 4.4-4.8 CPU-s/s while an arm loads the cores (B1g's warmup, every Lg and
Lg-mtp phase). So the rule as written reads a post-treatment variable (the trap the H47b and H49b
pre-registration names): every arm that uses the cores voids itself, and no N=1 metric can be
recorded. The numbers above are the serving covariate only; when this was written, no decode rate
or score of hold 1 had been compared across arms.

**Rule.**

- A phase is void when an aiperf process is in any serving sample overlapping it (unchanged), or
  when the non-campaign containers' mean CPU over the arm's 60 s idle window (taken before the
  warmup, with the arm's server up and no request) exceeds 4 CPU-s/s. The phase's own
  non-campaign CPU is still recorded beside the check, descriptive only.
- A void phase's one re-run is preceded by a fresh 60 s idle window (`energy-idle-<phase>.json`),
  which then is the window for that re-run and later phases.
- Carry (with R1's not-reached arms and R3's stopped servers): an arm whose N=1 phase was void on
  both tries is carried to the next hold's front once (`h54.py carry`). Hold 1's Lg and Lg-mtp are
  carried by this rule, B1g by R3, A1g and A1o by R1, so hold 2 runs B1g, Lg, Lg-mtp, A1g, A1o, then
  Vq and Vq-mtp, as far as its time goes. The cap stays 3 holds; arms it leaves out are not run.
- Hold 1's records are not re-scored under this rule: their void phases stay void, and an arm's
  later measurement replaces them (R1).

### H54 note N1: what OVB is (2026-10-21)

Recorded 2026-10-21, after H54a and before H54q. A description fix, not a protocol change: no
arm, model, statistic, gate, threshold, cap or prediction changes. The pre-registration, the H54q
row and the scout describe OVB (`OpenVINO/gemma-4-26b-a4b-it-int4-ov` @ `4e5885c3`) as INT4_ASYM
group 128 from the base model, as its README says. Its own `openvino_config.json` (read from the
Hub at `4e5885c3`, and in R1) records AWQ, asymmetric, group size 64, with the `.*router.*`
layers left unquantized. The config is what was built, so OVB is AWQ INT4 asymmetric group 64,
router excluded, from the base (non-QAT) model. H54q's comparison is unchanged: OVB is still the
published base-model build, labelled by its repo. `k3d-gpu plan --predict` (#167) reads the same
config and prices A1o at group 64.

### H54a run (Xeon 6741P, 2026-10-21)

```text
# Reproduce
date:            hold 1 2026-10-21T08:17:34Z (lock) .. 08:50:34Z (cluster k3dgpu-g-h54a-1 deleted);
                 hold 2 09:07:56Z .. 09:37:25Z (k3dgpu-g-h54a-2); hold 3 09:37:27Z (lock; load1 32.5,
                 waited 120 s) .. 10:11:27Z (k3dgpu-g-h54a-3). Every hold under flock -w 21600
                 ~/k3dgpu-bench.lock; df / 94% before each hold and at the end.
                 Not a hold: 08:50:49Z start stopped by hand at 08:51:45Z before its cluster
                 existed (amendment R4; hold2-stopped-prestart-0851Z/note.txt).
pre-registered:  H54 pre-registration and R1 (#169) before the smokes; R2 (#188, 567a2bcb) merged
                 08:16:48Z, before hold 1; R3 (#189) merged 08:48:39Z, during hold 1 and before
                 hold 2; R4 (#190, 6edf1bb7) merged 09:07:31Z, before hold 2
hypothesis:      H54a
host:            crescent: Intel(R) Xeon(R) 6741P, 96 CPUs, 1 socket, 1 NUMA node, microcode
                 0xa000133, AMX (amx_bf16 amx_int8 amx_tile), governor performance, SMT on,
                 527767144 kB RAM, no swap; kernel 7.0.0-31-generic; Docker 29.8.0
cpuset:          none on the node; optimized arms bind 48 threads to CPUs 0-47 with the engine's
                 own knob; stock B1g as rendered (no CPU request, limit or balloon; see below)
k3d-gpu commit:  hold 1 567a2bcb, holds 2-3 6edf1bb7, 0 uncommitted; k3d v5.9.0 (shim adds the
                 ~/k3dgpu-gemma/models volume, R1), kubectl v1.35.2, helm v4.1.1
node image:      cryptoandcoffee/k3d-gpu:0.1.63@sha256:b6fc46f6...4b76, K3D_GPU_VENDORS=amx,
                 crescent amendment C1's K3D_GPU_K3S_ARGS
EAT:             intel/enterprise-agent-toolkit 0d3438d7 (release-1.0.3-hotfix)
images:          B1g vllm/vllm-openai-cpu:v0.24.0@sha256:b0e6f860...81a1
                 Lg, Lg-mtp ghcr.io/ggml-org/llama.cpp:server-b11749@sha256:9fbaa06d...f312
                 A1o, A1g openvino/model_server@sha256:a928903f...fc58 (2026.4.1-gpu, k3d-gpu's pin)
                 not run: vllm/vllm-openai-cpu:v0.32.0@sha256:ffaba398...be05 (Vq, Vq-mtp, Vi),
                 lmsysorg/sglang:v0.5.22-xeon@sha256:4e5c413e...230f (Sg),
                 openvino/model_server:2025.4@sha256:c3707200...962a (B3g, EAT's chart)
models:          google/gemma-4-26B-A4B-it-qat-q4_0-unquantized f1e06dc5 (B1g; shards d57e8bde...,
                 e5e6e258...); google/gemma-4-26B-A4B-it-qat-q4_0-gguf d1c082be
                 (gemma-4-26B_q4_0-it.gguf 3eca3b8f...); OpenVINO/gemma-4-26b-a4b-it-int4-ov
                 4e5885c3 (A1o; openvino_language_model.bin 0f887130...; AWQ asym g64, note N1);
                 google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant 95371415
                 (model.safetensors c082cc58...). Intel/gemma-4-26B-A4B-it-int4-AutoRound
                 edff6272 (Vi, not run) is the 8-shard set model-0000{1..8}-of-00008 in the header.
OVQ (A1g):       models/k3dgpu/gemma-4-26b-a4b-it-qat-int4-sym-g32-ov, built by bench/h54/convert.sh:
                 ovenv/bin/optimum-cli export openvino --model <QAT bf16 f1e06dc5> --task
                 image-text-to-text --weight-format int4 --sym --group-size 32 --ratio 1.0 <out>
                 (361 s); openvino 2026.4.1, openvino-tokenizers 2026.4.1.0, nncf 3.4.0,
                 optimum-intel 2.2.0, optimum 2.3.0, transformers 5.5.4, torch 2.14.1+cpu;
                 openvino_language_model.bin 15516f61...b52e (14519629844 B),
                 openvino_language_model.xml 3682b3bd...6d2a, openvino_text_embeddings_model.bin
                 1808d6e3...b7b0, openvino_vision_embeddings_model.bin 87a4b947...443f;
                 every file's sha256 in the build's manifest.json. The other directory,
                 models/k3dgpu_gemma-4-26B-A4B-it-qat-int4-sym-g32-ov, is PR #157's A1 export
                 (--backup-precision none, grid snap) for H50; no H54a arm loads it.
drafter (Lg-mtp): models/k3dgpu/gemma-4-26B-A4B-it-qat-assistant-gguf, built by convert.sh in
                 ghcr.io/ggml-org/llama.cpp:full-b11749@sha256:aefb375a...711e:
                 convert_hf_to_gguf.py --outtype bf16 (transformers 5.5.4, R2) then llama-quantize
                 Q4_0; ...-q4_0.gguf 8eb21d5b...3ca9 (321145696 B), ...-bf16.gguf 033e8562...bccf
data:            bench/h54/gsm8k-test-0-199.jsonl 45e54be2... (openai/gsm8k main test @ 740312ad);
                 bench/h54/ttft-prompt.txt b10cd3c7...
command:         bench/h54/campaign.sh (R1's hold plan, R3's carry); per hold bench/h54/hold.sh ->
                 hold-run.sh -> arm.sh per arm; python3 bench/h54/h54.py score-a <results>
energy source:   RAPL intel-rapl:0 package-0 via bench/energy.sh (sudo -n), per phase, 60 s idle
                 window before each arm
raw results:     bench/h54/results/2026-10-21/H54a/ (hold1, hold2, hold3: per arm status.json,
                 phases.json, load-*.json, energy-*.json, quality.json, deploy.yaml, server.log.gz;
                 per hold reproduce.txt, pre.txt, post.txt, serving.jsonl.gz, hold.log; score-a.json;
                 not-run.txt), bench/h54/results/2026-10-21/campaign.log,
                 bench/h54/results/2026-10-21/planner/ (#167's ceilings). Logs over 20 kB are
                 gzipped; kubeconfigs are not kept.
```

**Wiring smokes (discarded, no scored request).** Three smoke holds before hold 1, each under the
lock and with its cluster deleted: 03:48-04:12Z (B1g, Lg, Lg-mtp, A1o ready; A1g not deliverable,
the directory-mode fault R2 fixed), 04:45-05:14Z (Vq ready; Vq-mtp not deliverable, the draft-length
fault R2 fixed), 06:58-07:28Z (A1g, Vq-mtp, Sg ready; Vi not deliverable, vLLM v0.32.0 "Layer
language_model.model.layers.0.experts has no parameter 'w2_qweight'" loading the AutoRound
checkpoint; B3g crash-looped, exit code 139, not ready in the 405 s left). These are wiring
results, not H54a data: Vi, Sg and B3g were never reached in a scored hold.

**What ran.** Hold 1: B1g, Lg, Lg-mtp; A1g and A1o not reached (395 s left). Hold 2 (R3 and R4
carry): B1g, Lg, Lg-mtp; A1g and A1o not reached (610 s left); Vq and Vq-mtp not reached. Hold 3:
A1g, A1o; Vq, Vq-mtp, Vi, Sg and B3g not reached. The cap (3 holds) left those five not run.

**Stock arm B1g: stalled under this harness in both holds, cause open.** In hold 1 B1g was ready
after 219 s, answered 2 of 3 warmup requests and stalled on the third at 166 output tokens; vLLM
v0.24.0 logged "No available shared memory broadcast block found in 60 seconds" from 08:24:21Z,
then `TimeoutError` in `shm_broadcast` and `EngineDeadError`, and the server exited at 08:28:20Z.
No scored request completed (R3 rewrote its record from `measured` to `failed` at 08:50:49Z). In
hold 2 it completed 3 of 8 N=1 requests (2 with a decode rate: 17.92 and 18.17 tok/s), then died
the same way ("RPC call to execute_model timed out", `EngineDeadError`, 09:21:02Z); N=4, N=16,
TTFT and all 50 GSM8K items errored. R3 carries a stopped arm once, so B1g did not run a third
time. Neither hold had an out-of-memory kill or an aiperf process; load1 fell to about 4-5 during
the stall and the non-campaign CPU over hold 2's N=1 phase read 2.66 CPU-s/s, so neither box load
nor contention explains it. The cause is not known. What this arm was: R1's render of EAT's
`cpu-gemma4-26b-a4b` with no CPU request, limit or balloon, which matches H50's pre-registered B1
("no pinning") but is not EAT's default install: EAT 0d3438d's `core/lib/system/config-vars.sh`
defaults `balloon_policy_cpu="enabled"`, and its playbook then adds the balloon annotation and
CPU and memory requests and limits. Unbounded, vLLM's `VLLM_CPU_OMP_THREADS_BIND=auto` placed its
OpenMP threads on CPUs 48-94. No control ran (Vq, v0.32.0 with B1g's flags, never reached; B1g via
EAT's balloon path or outside k3d, not in the protocol). This is a result about this harness, not
a finding of an EAT or vLLM defect.

**Results** (N=1 decode: median of the 8 requests' `decode_tokens_per_s`; aggregate: phase output
tokens / wall time; TTFT: median of 4 on the 999-token prompt; J/token at N=1, RAPL package, gross
and net of the arm's own 60 s idle power; quality: GSM8K items 0-49, exact match):

| Arm | Hold | Status | N=1 decode tok/s (min-max) | N=4 / N=16 aggregate tok/s | TTFT s | J/token gross / net (idle W) | GSM8K 0-49 |
|---|---|---|---|---|---|---|---|
| B1g | 1 | failed (stopped answering) | none | none | none | none | 0/50, 50 errors |
| B1g | 2 | failed (stopped answering) | 18.04 (17.92-18.17; 2 rates, descriptive) | none | none | none | 0/50, 50 errors |
| Lg | 2 | measured | 48.74 (48.09-49.02) | 88.84 / 87.19 | 2.73 | 5.80 / 2.95 (131.4) | 50/50 |
| Lg-mtp | 2 | measured | 54.52 (50.50-55.54) | 121.21 / 120.05 | 2.71 | 5.34 / 2.77 (131.1) | 50/50 |
| A1g | 3 | measured | 23.25 (22.98-23.39) | 30.87 / 65.52 | 5.05 | 12.80 / 6.79 (132.1) | 49/50 |
| A1o | 3 | measured | 21.73 (21.60-22.12) | 27.66 / 61.58 | 4.83 | 13.66 / 7.31 (131.9) | 49/50 |

Lg and Lg-mtp's N=16 aggregates are capped by llama.cpp's default slot count (`n_slots = 4` in
its log): N=16 queues 12 requests, so those figures are N=4 capacity, not N=16. Lg-mtp's drafter
accepted about 0.61-0.69 of drafted tokens (mean accepted length about 2.8-2.9, its log). Hold 1's
Lg (47.93) and Lg-mtp (55.05) N=1 medians, void under the rule as pre-registered, agree with hold
2's within 2%; they are not used.

**Every optimized-arm number here exists only because of R4.** Each scored phase's own
non-campaign CPU read 4.39-4.83 CPU-s/s (N=1: Lg 4.72, Lg-mtp 4.75, A1g 4.75, A1o 4.74), above the
pre-registered 4.0 line, so under the void rule as first written every one of them is void and
H54a has no optimized metric. R4 was written after hold 1 showed that; it reads the arm's 60 s
idle window instead (2.47-2.63 CPU-s/s in holds 2 and 3). The phase readings sit about 2.2 CPU-s/s
above the idle ones in every phase that loads the cores, and B1g's stalled N=1 phase read 2.66.
With the live SGLang pair idling at about 2.5 CPU-s/s, R4's CPU part cannot fire on this host as
it stands; only its aiperf part can (it did not). The causal reading (the idle pair's CPU follows
the arm's load) is R4's argument; it is plausible, and it was made after the voids were seen.

**Verdict: INCONCLUSIVE**, from `h54.py score-a` (`score-a.json`): "B1g not delivered or not
measured". The statistic is the chosen arm's N=1 median over B1g's, and the quality gate is B1g's
score minus 0.10; B1g has neither a measured N=1 phase nor a score, so no arm can pass the gate and
no arm is chosen. The pre-registration's INCONCLUSIVE branch names this case ("B1g not
deliverable").

Read descriptively, and not as the verdict: with B1g's 2 hold-2 rates, Lg/B1g is 2.70 (bootstrap
CI 2.67-2.73) and Lg-mtp/B1g 3.02 (CI 2.90-3.06). By the source rule Lg-mtp (rank 2, 1.119x Lg at
50/50) would replace Lg (rank 0) as the chosen arm. Its CI lower bound is below 3.0, so even had
those rates counted, the row would not have been SUPPORTED; the 3x bar most likely would not have
cleared with a working B1g. No int4-vs-bf16 quality statement can be made: the bf16 arms have no
score (B1g errored, Vq not run), and 49-50/50 on these items is near the ceiling.

**Against the predictions.**

| Prediction | Result |
|---|---|
| B1g in [4, 15] (0.6) | 18.04 from 2 rates, descriptive: above the range |
| Lg in [25, 70] (0.6) | 48.74: held |
| A1g in [15, 60] (0.5) | 23.25: held |
| A1g/A1o in [0.85, 1.02] (0.6) | 1.07 (CI 1.06-1.08): missed, the QAT-matched IR decoded faster than OVB |
| Lg-mtp/Lg in [1.2, 2.2] (0.4) | 1.12 (CI 1.08-1.13): missed |
| A1g N=16/N=1 <= 1.5 (0.5, not gated) | 65.52 / 23.25 = 2.82: missed |
| Vq in [6, 18], Vq-mtp/Vq in [1.4, 2.5], Sg deliverable, B3g not deliverable | not run (cap); the smokes are wiring results only |
| Chosen arm Lg or Lg-mtp (0.55) | no chosen arm; descriptively Lg-mtp |
| SUPPORTED 0.65 / INCONCLUSIVE 0.25 / REFUTED 0.1 | INCONCLUSIVE |

**Bandwidth (pre-registration item 5).** Lg reads 2.380 GB per token (`h54.py active-bytes` on the
GGUF's tensor table: 1.577 GB of non-expert tensors plus 8/128 of 12.846 GB of routed experts),
so 48.74 tok/s moves about 116 GB/s, 38% of the 305.2 GB/s STREAM triad of 2026-10-20 (48
threads; the pre-registration's figure). That is derived (bytes from the file times the measured
rate), not a bandwidth measurement. Beside it, `k3d-gpu plan --predict` (#167, merged at
35b419ce, files in `planner/`) gives per-arm ceilings from its own STREAM triad of 320.0 GB/s
(2026-10-21T00:13:12Z, 48 threads; a second measurement, not the 305 above). These are ceilings
computed from a measured bandwidth, not measured decode rates:

| Arm | GB read per token (predictor) | Ceiling from STREAM 320 GB/s, tok/s | Measured N=1 tok/s | Measured / ceiling |
|---|---|---|---|---|
| Lg | 2.380 (GGUF tensor table) | 134.5 | 48.74 | 36% |
| A1g | 2.150 (config, int4 sym g32) | 148.9 | 23.25 | 16% |
| A1o | 2.060 (config, AWQ asym g64) | 155.3 | 21.73 | 14% |
| B1g | 7.644 (config, bf16) | 41.9 | none | none |

The predictor has no compute, KV or kernel term and no MTP, so Lg-mtp has no ceiling row. At
batch 1 the OVMS arms run at about a sixth of their bandwidth ceiling and llama.cpp at about a
third; the predictor is not a forecast for these engines on this host.

### H54d: not run (2026-10-21)

No hold used, no cluster created. H54d's arm A is H54a's chosen arm A*, and H54a chose none (B1g
has no score, so no arm passes the quality gate); its arm B is B1g, which stalled in both H54a
holds; and the pre-registration orders H54q before H54d, and H54q has not run. The
pre-registration's caps rule makes a row whose stock or chosen arm is missing INCONCLUSIVE, so
H54d is recorded INCONCLUSIVE (not run). Running it would need a dated amendment that defines A*
without B1g's score (or a working B1g), a runner amendment (the pairs runner has no llama.cpp arm
A), and the tool-call wiring smoke. Record: `bench/h54/results/2026-10-21/H54d/not-run.txt`.

### H54 note N2: why B1g stalled (unscored diagnostic, 2026-10-29)

Recorded 2026-10-29. A diagnostic, not a hold. No arm, row, statistic, gate, threshold, cap or
prediction changes, and no H54a number is re-scored. It answers the run section's "cause open".

**What ran.** `bench/h54/diag/b1g-stall.sh` (PR #209), under the bench lock and the hold-start
rule, outside k3d. B1g's container ran under plain `docker run`, rendered from the committed
hold-2 record (`hold2/B1g/deploy.yaml`): the same image digest, args, env ConfigMap and chat
template, user 1001, a read-only root, the same capabilities, and no CPU or memory request or
limit. The QAT weights were mounted read-only. Three things differ from the pod: a private IPC
namespace where the pod had `hostIPC: true` (both have a 1 GiB memory-backed `/dev/shm`), tmpfs
for `/tmp` and the vLLM cache where the pod had emptyDirs, and the model directory mounted
directly. The load was sequential 256-token greedy requests from `bench/llm_load.py` with the H54a
N=1 settings. Run 1 (05:30-05:39Z) used the render as is. Run 2 (05:40-05:57Z) was a labelled
control with one change, `VLLM_CPU_ATTN_SPLIT_KV=0`. Neither run used a GPU or touched a serving
container. Records: `bench/h54/results/2026-10-29/B1g-stall-diag/`.

Run 1 used the script at `9f795a86`, which had no perf step and dumped Python stacks with
`py-spy --native`; those dumps failed (rc 1 in `stall.json`). Its perf sample (`perf-spin.*`) and
its Python stacks (`pyspy-*-plain.txt`) were taken by hand during the stall, with the commands in
`perf-cmd.txt`; `perf-spin-script.txt.gz` keeps the first 400 lines of `perf script`. `6cbc2632`
then added both steps to the script, and run 2 used it.

**Run 1: the stall reproduced without k3d.** The server was ready after 162 s. Five requests
completed (median 18.7 tok/s); the sixth stopped at 248 of 256 tokens, at KV length 294. At the
stall the container's CPU fell from 48.0 to 1.006 CPU-s/s and stayed there. Thread snapshots 30 s
and 40 s after the stall agree: of the worker's 101 threads, one was runnable (CPU 53, 0.999
CPU-s/s over the 10 s between them), and the worker's main thread and 78 others slept on futexes.
perf took 5,003 samples of that thread over 5 s. 99.74% fell in
`cpu_attention::AttentionMainLoop<AttentionImpl<AMX, BFloat16, 512, BFloat16>>::reduce_splits(...,
bool volatile* flags, ...)`, inside an OpenMP region, spinning with `_mm_pause` on a
split-completion flag. The worker's Python stack sat in `cpu_attention_with_kv_cache`
(`cpu_attn.py:394`, called from `gemma4.py:536`). EngineCore's stack sat in
`shm_broadcast.acquire_read`, waiting for the `execute_model` reply. 300 s after the stall,
EngineCore raised "RPC call to execute_model timed out" and `EngineDeadError`, as in both H54a
holds. 300 s is vLLM's `VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS` default. EAT's `VLLM_RPC_TIMEOUT` is
not a v0.24.0 variable; the server logs it as unknown.

**Cause: a vLLM CPU attention bug, fixed upstream after v0.24.0.** vllm-project/vllm#45844
("Fix CPU split-KV scratchpad sizing", merged 2026-07-04 as `26eb8720`) was opened for a hang
"seen with google/gemma-4-26B-A4B-it". In v0.24.0 the CPU attention metadata builder takes the
KV-head count from the model config for every layer group: 8, from `num_key_value_heads`, with 16
query heads. The scheduler then sizes the split-KV reduction scratchpad for 16/8 = 2 query heads
per KV head times the split threshold. Gemma 4's global-attention layers have head dim 512 and 2
KV heads, against the sliding layers' 256 and 8; 5 of the 30 layers are global (`config.json`).
Their kernel handles 8 query heads per KV head, so split outputs overrun the scratchpad into the
split-completion flags, and `reduce_splits` can wait forever. The stuck kernel's template
arguments (AMX, head dim 512) are those layers'. v0.24.0 (tagged 2026-06-29) carries the old code:
`cpu_attn.py` line 140 takes `model_config.get_num_kv_heads`, and `cpu_attn_impl.hpp` lines 420
and 688 size the scratchpad from it. The merged fix takes the count from each group's KV-cache
spec and sizes the scratchpads for the largest query-head tile any work item uses. The PR's
description also names the split outputs' float element size; the merged diff does not change it,
and on the AMX kernel v0.24.0 already sizes them as float (`partial_output_buffer_t`). GitHub's
compare puts `26eb8720` in v0.25.0 and in every later tag checked (v0.26.0, v0.28.0, v0.32.0), and
not in v0.24.0.

**Deterministic?** Not in position, but recurrent. All three B1g engines stalled within their
first 690-1,540 decode steps: H54a hold 1 at KV length 211 (third 256-token request), hold 2 at
299 (sixth), run 1 at 294 (sixth). Earlier requests had passed those lengths without a stall.
Split writers and the flag race, so where it bites varies.

**H54a's two stalls are this bug: inferred, not observed.** No thread record exists for them.
The image, render and weights are the same, and so is the log signature. Their host readings fit
a single spinning thread. In hold 1, load1 sat at 5.2-5.5 during the stall and 3.8-3.9 after the
engine died. Hold 2's N=1 phase used 52,204 J in 342.8 s; less 3 requests at the warmup phase's
279.4 W, that leaves about 135 W for the ~300 s stall, against the arm's 131.6 W idle window.

**Control (run 2): split-KV off, no stall.** Ready after 162 s. All 60 requests completed at
256 tokens each: 15,370 tokens with the readiness chat, ten times the most any stalled engine
reached. Median 18.4 tok/s (decode 18.5), against run 1's 18.7 (18.9) over its five. The
container read 47.8-48.0 CPU-s/s whenever a request ran. The server logged no shared-memory wait
and no `EngineDeadError`. At the rate the three stalled engines show (3 stalls in about 3,770
decode steps), 15,360 steps pass without one with probability about e^-12. So the same image,
render and weights do not stall with split-KV off, and N=1 decode is no slower. The control is a
patch to the stock arm and is not a B1g measurement.

**Not the cause.** The k3d harness: run 1 had no k3d, kubelet or port-forward. Memory: no OOM
kill, and 40 GiB of KV on a 503 GiB host. Host contention: no aiperf process in any of the three,
and the serving containers read 2.3-3.2 CPU-s/s during the stalls (4.8-5.1 while run 1 decoded,
the rise amendment R4 describes). Prompt length: about 45-token prompts, stalls at different
KV lengths. Thread placement: the OpenMP threads on CPUs 48-94 decoded normally for minutes first.

**What EAT's stock config sets.** EAT `0d3438d` `xeon-values.yaml`, entry
`google/gemma-4-26B-A4B-it`: image `vllm/vllm-openai-cpu:v0.24.0`. ConfigMap:
`TRITON_CACHE_DIR`, `VLLM_CPU_KVCACHE_SPACE` 40, `VLLM_RPC_TIMEOUT` 100000,
`VLLM_ALLOW_LONG_MAX_MODEL_LEN` 1, `VLLM_ENGINE_ITERATION_TIMEOUT_S` 120, `USER`. Args:
`--block-size 128 --dtype bfloat16 --distributed_executor_backend mp --enable_chunked_prefill
--enforce-eager --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser gemma4
--chat-template .../tool_chat_template_gemma4.jinja`. Unlike the file's other CPU entries it sets
neither `VLLM_CPU_SGL_KERNEL` nor `VLLM_CPU_NUM_OF_RESERVED_CPU`. No entry sets
`VLLM_CPU_ATTN_SPLIT_KV`, whose v0.24.0 default (1) turns split-KV on. EAT's playbook default
also adds the `vllm-balloon` annotation with `cpu` 8 and `memory` 64Gi unless its sizing step
says otherwise. EAT release-1.1.0 (2026-10-22, `0c726f57`) keeps this entry's args and env (it adds
`HF_HOME` /data, which B1g's render already had) and moves the image to `v0.28.0`, which contains
the fix.

**What the pre-registrations do with it.** None of them lets the stock arm change: the image, the
flags and the env are EAT's as pinned. `VLLM_CPU_ATTN_SPLIT_KV=0` is not in EAT's config, so it is
a patch, used here only in the labelled control. H54a is closed (amendment R3 carried B1g once;
the cap is spent). H54d's arm A is defined by H54a's chosen arm, so it stays INCONCLUSIVE (not
run). H50 and H51 have not run; their B1 is this arm, and nothing in their rules avoids the bug.
Under H50's P1 rules a stalled turn is `over_turn`, a B1 outcome, not a void. But a stalled B1
pod spins one core for 300 s and then restarts and reloads. Both collide with the idle-arm check
(the other arm's pod above 0.5 CPU) and the quiesce rule. H51 scores a failed step: errors over 1%
end the arm's bracket. Changing B1 to EAT's current release is a new arm, and needs a dated
amendment before any H50 or H51 hold.

### H57d pre-registration: Gemma 4 26B-A4B QAT on the AMX-less client CPU (i9-13900T, 2026-10-20)

Written 2026-10-20, before any H57da, H57dm or H57db measurement. Nothing here is a measurement
except the host facts under "Read before this pre-registration". The runner lands in a dated
amendment with regression tests, merged before the first H57d hold. H57d is the desktop lane of
the Gemma 4 QAT work: H54 runs it on the Xeon 6741P (AMX, crescent); this row gives the
AMX-less client the same engines, model files and quality set, so the two hosts compare.

**Models (box owner's directive: only the Gemma 4 QAT family).** Downloaded on this host from
the Hub at these revisions, each file's sha256 checked against the Hub's LFS sha256 and recorded:

- `google/gemma-4-26B-A4B-it-qat-q4_0-gguf` @ `d1c082be` (`gemma-4-26B_q4_0-it.gguf`, 14.44 GB;
  the 1.19 GB mmproj is not downloaded: text only).
- `OpenVINO/gemma-4-26b-a4b-it-int4-ov` @ `4e5885c3` (OVB, 15.3 GB; INT4_ASYM g128 from the base,
  non-QAT model per its README; its `openvino_config.json` says AWQ g64; whichever it is, OVB is
  OpenVINO's own build and is labelled by its repo).
- `google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant` @ `95371415` (0.84 GB bf16), the QAT
  MTP drafter, converted here to a Q4_0 GGUF exactly as H54's runner does (`bench/h54/convert.sh`,
  PR #169: `ghcr.io/ggml-org/llama.cpp:full-b11749` `--convert --outtype bf16`, then `--quantize
  Q4_0`), with a manifest of every file's sha256.
- Not run here: the QAT-matched IR (OVQ, `bench/models/export_qat_ov.sh`, PR #157). On 2026-10-20
  at registration crescent had not built it (`~/k3dgpu-gemma/models/k3dgpu_gemma-4-26B-A4B-it-qat-int4-sym-g32-ov`
  absent), weights are never copied between hosts, and a rebuild here loads the 51.6 GB bf16
  checkpoint into memory with 50.4 GB available. If a later amendment rebuilds it here, it is a
  separate arm Oq at source rank 2, never a replacement for Od.

Storage: `/` has 59 GB free (97%). Model files and images go to the second disk (`/home/andrew/docker&models`,
874 GB free, where Docker's data root already is), in this campaign's own directory, bound into
the node read-write with k3d's `--volume` (H54's shim pattern), never copied in. Nothing is
written to `/` beyond the run records. Everything this campaign creates is deleted at the end
except the committed records.

**Arms (all on the P-cores, one serving at a time).**

| Arm | Stack | Model | Rank | Draft |
|---|---|---|---|---|
| Od | `k3d-gpu agent manifest`, `K3D_GPU_AGENT_DEVICE=CPU` (the hybrid node gives the pod the P-core balloon and requests its 16 CPUs), OVMS `a928903f...` (2026.4.1, k3d-gpu's pin), `K3D_GPU_AGENT_DRAFT=off`, as k3d-gpu writes it | OVB | official (OpenVINO) | none: OVMS documents no draft path for Gemma 4; the only Gemma 4 QAT drafter is the MTP assistant, which reads the target's KV, and no OpenVINO MTP IR exists for it (OV 2026.4's notes name Gemma 4 MTP; no export path or IR ships) |
| Ld | `ghcr.io/ggml-org/llama.cpp:server-b11749@sha256:9fbaa06d...` (H54's pin), pod labelled `k3d-gpu.io/cores: performance` with `requests.cpu` 16 (the same balloon as Od), `-t 8 -C 0x5555 --cpu-strict 1 --jinja` (one thread per physical P-core: CPUs 0, 2, ..., 14), every other flag the server's default | Google QAT Q4_0 GGUF | official | off |
| Ld-mtp | Ld plus `-md <drafter GGUF> --spec-type draft-mtp` at the default draft length (H54 amendment R1's reading of b11749) | plus the QAT drafter Q4_0 GGUF built here | self-built drafter (2) | on |

The CPU Fast Draft rule ([H34](#h34-result-i9-13900t-2026-10-17), SUPPORTED on this CPU): the
draft is on wherever the engine supports a Gemma 4 QAT drafter (Ld-mtp); the MTP drafter is its
own factor (H57dm), so Ld runs too. Each flag is read against the engine's source at its pin in
the runner amendment (H54 R1 already read b11749's); a flag missing there is dropped and recorded.
The CPU set each arm's server actually got (`Cpus_allowed_list`) is recorded.

**Measurement protocol (per arm, per hold).** H54's, with these desktop changes:

1. Cluster `k3dgpu-d-h57d-<hold>`, one per hold, `k3d-gpu up` with `K3D_GPU_VENDORS=hybrid` (no
   GPU device plugin; no card is used), node image `cryptoandcoffee/k3d-gpu:0.1.63` pinned to its
   digest, amendment C1's `K3D_GPU_K3S_ARGS`. An arm not answering its model list and a 16-token
   chat within 900 s is "not deliverable" for that hold, recorded with its log.
2. Performance holds 1-3, each running all three arms, in the rotated orders (Od, Ld, Ld-mtp),
   (Ld, Ld-mtp, Od), (Ld-mtp, Od, Ld). Per arm: 60 s idle (RAPL), 3 warmup requests, N=1: 8
   requests, N=4: 16, TTFT: 4 at N=1 on H54's 1,000-token prompt (`bench/h54/ttft-prompt.txt`) with
   `max_tokens` 16. Requests as H54: `bench/llm_load.py` default prompt with `--unique-prompts`,
   `max_tokens` 256, `ignore_eos`, temperature 0. H54's N=16 phase is dropped: 8 physical cores,
   and it would not fit three arms in a hold.
3. Quality hold 4: GSM8K items 0-49, H54's file, prompt, grader and settings
   (`bench/h54/gsm8k-test-0-199.jsonl`, temperature 0, `max_tokens` 1024, thinking off, 16 in
   flight), each arm once, order (Ld, Od, Ld-mtp). Quality gate: an arm passes when its score is
   at least the best arm's score minus 0.10 (5 of 50).
4. Energy: `bench/energy.sh` on RAPL `package-0` per phase (through sudo), J/output token gross and
   net of the arm's idle power. The package sits near its 117 W PL1 under the box's own background,
   so J/token is reported, never gated.
5. Load: about 50 production containers and other sessions share the host; nothing is stopped.
   The hold starts when `load1` <= 24 (2-min polls, at most 60 min, then it runs flagged). During
   every phase a sampler records, every 5 s, the busy CPU-s/s of CPUs 0-15 from `/proc/stat`, the
   k3d node container's own CPU-s/s from its cgroup, `load1`, and package temperature; foreign
   P-core load = the first minus the second. Load gate (H34's design law: busy is normal, unmatched
   is not): over the N=1 phases, the per-arm means of foreign P-core load differ by at most
   max(2.0 CPU-s/s, 25% of their mean); otherwise H57da and H57dm are INCONCLUSIVE (load
   unmatched). No phase is voided on load.
6. Statistics: per arm, the 24 pooled N=1 requests' `decode_tokens_per_s` (8 per hold); per-hold
   medians; the ratio of medians with H54's bootstrap CI (`h54.py boot_ratio`: 10,000 resamples
   of each arm on its own, seed 1, percentiles 2.5 and 97.5). X for H57da is the faster, by
   pooled median, of Ld and Ld-mtp among gate-passing delivered arms.

**H57db.** H50's banking-flow design (arm A: k3d-gpu auto on the P-cores, OVB, `gemma4` tool
parser; arm B: EAT stock CPU, `cpu-gemma4-26b-a4b` on the QAT bf16 weights, `VLLM_CPU_KVCACHE_SPACE`
40, as amendment M1's B1) under rule L3 (PR #157). Disk would allow it (the bf16 weights fit the
second disk); memory does not at registration: B needs about 94 GB resident and the host has
50.4 GB available with 127.7 of 187.5 GiB of swap in use. Memory gate: MemAvailable >= 100 GiB at
the hold's start; otherwise INCONCLUSIVE (not run), with the reading recorded, and no bf16
download. It never runs before PR #157 (`bench/eatflow/pairs.py` with L3) merges.

**Holds and caps.** Lock: every hold, conversion and download that loads the CPU runs inside
`flock -w 21600 <scratchpad>/live.lock` (this host's shared lock). Each hold at most 45 min
(`timeout -k 30 2700`); the cluster is deleted at the end, also on failure. Disk: `df /` and the
second disk are read before each hold; a hold does not start, and its watchdog stops it, when `/`
would have less than 15 GB free. Caps: at most 6 holds in all: H57da and H57dm share at most 5
(holds 1-3 performance, hold 4 quality, one spare that repeats a failed hold's arms in that
hold's order), H57db at most 1. A failed hold counts.

**Read before this pre-registration (2026-10-20, not scored).** `lscpu`: CPUs 0-15 are the 8
P-cores with their HT siblings (core n = CPUs 2n, 2n+1), 16-31 the E-cores; no `amx_*`, no
`avx512*` flags. 131.5 GB RAM, MemAvailable 50.4 GB; swap 187.5 GiB, 127.7 GiB used. `load1` read
41-67 while writing this, with other sessions' builds running. A 3 s RAPL read through
`energy.sh` gave 114.6 W package at that background.

**Order and changes.** A protocol change is a dated amendment merged before the data it governs.
A wiring smoke runs no scored request, is discarded and is disclosed in the run section.

### H54b-H54c amendment G1: the B70 runner (2026-10-20)

Written 2026-10-20, before any H54b or H54c measurement. It adds the runner the
[H54 pre-registration](#h54-pre-registration-gemma-4-26b-a4b-qat-and-qwen38-microbenches-on-crescent-2026-10-20)
asks for, for its two card rows only, and changes no hypothesis, threshold or statistic. H54a,
H54q and H54d get their own runner.

**Files.** `bench/h54gpu/`: `hold.sh` (bench/xhost/hold.sh's rules for clusters `k3dgpu-g-*`
and `~/k3dgpu-gemma`), `h54c.sh`, `h54b.sh`, `b2q.sh`, `ladder.py`, `lib.sh` (cluster, pod, phase and energy
helpers), `k3d-wrap.sh`, `gguf_bytes.py` (H54c's byte split from the GGUF header), `predict.py`
(H54c's prediction and verdict), `score.py` and `stats.py` (H54b's chosen arm, bootstrap and
verdict), `gsm8k.py` (protocol step 4), `ttft_prompt.txt` (the fixed TTFT prompt, 803 words,
sha256 `cf2a1da3...41fe4`) and `test_h54gpu.py` (CI). `bench/llm_load.py` gains `--extra-body`,
which merges a JSON object into each request; the Qwen arms send
`{"chat_template_kwargs": {"enable_thinking": false}}` with it. GSM8K items:
`openai/gsm8k` `main/test-00000-of-00001.parquet` @ `740312ad` (sha256 `ee7b8da9...4e4f59`),
converted in file order to `~/k3dgpu-gemma/data/gsm8k-test.jsonl` (1,319 items, sha256
`3730d312...3c39d14`) with pyarrow 22.0.0.

**Cards.** The pre-registration's rule stands: a card is used only when
`~/k3dgpu-bench/gpu-state.txt` lists it free. Revised 2026-10-21, before any H54b or H54c
measurement: the operator authorised this campaign to stop (never remove) `qwen38-b-hr`, the
live SGLang server on cards 2 and 3 (`ZE_AFFINITY_MASK=2,3`, renderD130 and renderD131), when
no card is free. Its `docker inspect` is saved to `~/k3dgpu-gemma/restore/` first, the stop and
the restart are appended to `gpu-state.txt`, and after the last hold it is started again and
must answer a chat request. `campaign.sh` does this: it takes the lock once, stops the server,
runs the card holds back to back (`hold.sh` with `H54_LOCKED=1`, each still under its own cap,
start rule and cleanup), and restarts the server on any exit, so it is down once.
`qwen38-a-hr` (cards 0 and 1) is never touched. A card is checked at
hold start to be free: no `xpu-smi ps` process on it, and no process holding its render node
memory-mapped (`fuser` access flag `m`). `qwen38-a-hr`'s Level Zero processes hold an unmapped
fd on all four render nodes (enumeration; `xpu-smi ps` lists them only on cards 0 and 1), which
the check does not count. Two placements follow from how each arm gets its card:

- Pinned pods (Cg, Gg, Hg, Lsq, Sq, Sq-mtp, Rq): a privileged pod whose `/dev/dri` is an
  in-memory directory holding only the free card's render node, so the engine cannot open the
  cards the live pair uses. The pod's `/dev/dri` listing and the host `fuser` table are saved
  per arm.
- Device-plugin arms (X1q, X1q-mtp through `k3d-gpu agent manifest`; B2q through EAT's chart):
  the Intel GPU plugin chooses the card (one container per card on discrete GPUs). Unless all
  four cards are free, four placeholder pods (busybox `sleep`, never opening the device) first
  take every `gpu.intel.com/xe`; the ones that got a free card are deleted, so the plugin can
  only hand out free cards. The arms run only when the cards left held are exactly the busy
  ones (`gpu-hold.txt`); otherwise they are recorded as not run.

**H54c.** One cluster from `k3d cluster create` with the k3d-gpu 0.1.61 node image
(`@sha256:8cd264aa...`), C1's kubelet thresholds and `~/k3dgpu-gemma/models` read-only at
`/models`. Every arm runs `server-intel-b11749` with the same base flags,
`-t 48 -Cr 0-47 --cpu-strict 1 --jinja -c 8192`. The context is set because the default is
the model's 262,144 tokens; it is the same for all four runs. Arm flags: Cg `-ngl 0`, Gg and
Gg2 `-ngl 99`, Hg `-ngl 99 -ot exps=CPU` (b11749's `--override-tensor`, buffer type `CPU`;
`--moe-cache-mib` stays at its default, off). The phase is H54a's N=1 phase: 60 s of idle, 3
warmup requests, then 8 requests of 256 tokens, with RAPL package-0 and the card's `xe` hwmon
energy counters read around it. t_Gg is the first Gg run; Gg2 is only the drift check. The
expert byte count includes each layer's per-expert `ffn_*_exps.scale` tensors. Read from the
GGUF header @ `d1c082be` with `gguf_bytes.py` (sizes cross-checked against the tensor offsets):
B_exp = 0.803 GB, B_rest = 1.577 GB (of which 0.606 GB is the tied Q6_K `token_embd`), and
B_tot = 2.380 GB per decoded token. The wiring smoke (`SMOKE=1`) runs Cg alone in the CPU image
`server-b11749` with no card mounted. It is discarded and disclosed with the run.

**H54b.** The cluster comes from `k3d-gpu up` with C1's `K3D_GPU_K3S_ARGS`, the node label
`ei-inference-eligible=true` (EAT's chart selects it) and the 0.1.61 node image. `k3d-gpu up`
has no volume option, so `k3d-wrap.sh`, installed as the hold's `k3d`, adds two volumes to
`cluster create`: `~/k3dgpu-gemma/models` read-only at `/models`, and its `hf/` cache
read-write at `/models/hf`. Arm flags, as each engine's docs at its pin give them:

- X1q: `K3D_GPU_AGENT_MODEL=OpenVINO/Qwen3.8-27B-int4-ov`, revision `7b20ea8c`, storage 40Gi,
  image `2026.4.1-gpu@sha256:a928903f...`, served name `m`; the device stays auto. OVMS 2026.4.1 `--pull` clones the default branch (it takes no revision), so the
  pulled commit is read from the model's `.git` after the boot and must be `7b20ea8c7df72...`
  (the default branch on 2026-10-21); otherwise the arm is recorded as off-revision.
- X1q-mtp: X1q, plus `--draft_model_path=.` on the pull container and
  `K3D_GPU_AGENT_PREFIX_CACHING=false`, as OVMS v2026.4.1's speculative-decoding demo has it
  (`num_assistant_tokens` defaults to 5).
- Lsq: `-ngl 99 --jinja -c 8192`.
- Sq: llm-scaler README section 3.5's Qwen 27B flags, at TP 1, with
  `--max-model-len 8192 --max-num-batched-tokens 8192`.
- Sq-mtp: Sq plus `--speculative-config '{"method":"qwen3_5_mtp","num_speculative_tokens":4}'`
  (README "MTP").
- Rq: Sq's flags without `--quantization` (the checkpoint is quantized), with the same MTP
  config.
- B2q: `helm template` of EAT's chart at `c86c42b` with `xpu-values.yaml`, plus an override that
  copies the `Qwen/Qwen3.6-27B` `modelConfigs` entry verbatim under `Qwen/Qwen3.8-27B`, sets
  `LLM_MODEL_ID` to that id, sets `tensor_parallel_size` to 2, and reads the model from the HF
  cache (`global.modelUseHostPath`, PVC off) instead of downloading it.

PR #157's H53 boot ladder is the `xpu` mode of `bench/apb/stage.py` at PR #157 head `23c024dd`
(the H53 text itself is not yet registered). `ladder.py` applies the same four rungs to the
chart render. Rung a drops the host Level Zero and OpenCL hostPath mounts and their
`LD_LIBRARY_PATH` prepend. Rung b also drops `source /opt/intel/oneapi/setvars.sh --force`.
Rung c also drops `--disable-log-requests`. Rung d also adds `--max-model-len 32768`. B2q boots
at rung a and moves up one rung after each failed boot (1,200 s each) while the hold has time.
The rung that booted is recorded. The chart's image is a tag (`v0.28.0`), so the pulled digest
is recorded beside the run.

**Disk.** Models are downloaded before a hold, under the lock: `Qwen/Qwen3.8-27B` @ `1d4bf0f2`
(55.6 GB, into the HF cache layout), `Intel/Qwen3.8-27B-q4km-AutoRound` @ `4b30b30e` (18.7 GB)
and `RedHatAI/Qwen3.8-27B-INT4` @ `91bd022d` (19.5 GB). X1q's model is pulled by its own
manifest into its volume during the arm. A pod's image is pulled again into the node's
containerd. A download, or an arm, that would take `/` above 96% (the 97% line minus other
tenants' growth) is not started, and its arm is recorded as not deliverable (disk). On
2026-10-21 03:05Z the 96% line left 51 GB, less than `Qwen/Qwen3.8-27B` alone, so B2q, Sq and
Sq-mtp are expected to be not deliverable (disk), and with them H54b's statistic (INCONCLUSIVE
by the pre-registration). A download is made only when the arm that reads it would then also pass its own guard
(model plus its NEED in `h54b.sh`): Intel q4km for Lsq (18.7 + 6 GB) is fetched; RedHat INT4
for Rq (19.5 + 45 GB) and Qwen bf16 (55.6 GB) are not, unless the headroom grows. The 96% line
left 30.8 GB at 04:00Z, room for X1q's 20 GB or the q4km fetch, not both, so `campaign.sh`
fetches q4km between H54b's two holds, under the same lock, after X1q's volume is gone.

**Order and holds.** H54b hold 1: X1q, X1q-mtp. Hold 2: Lsq, B2q, Sq, Sq-mtp, Rq (the last four
only where their model was fetched and the disk guard passes). Arms not reached in the
40-minute cap are recorded as not run. H54c: its one hold, on any one free card, after H54b.
Each hold's results directory starts with a `REPRODUCE` header (commit, command, images,
models with revisions).

**G1 revision 2 (2026-10-21 05:40Z, before any H54b or H54c measurement).** The 96% line grew to
93.5 GB at 05:34Z (another tenant freed space), so `Qwen/Qwen3.8-27B` is fetched (guard: 55.6 GB
plus B2q's 25 GB). B2q is the statistic's denominator, so it moves to the front: H54b hold 1:
B2q, X1q. Hold 2: X1q-mtp, Lsq, Sq, Sq-mtp, Rq (each only where its model was fetched and the
disk guard passes; the Intel q4km fetch runs between the holds). `ladder.py` now drops the
chart render's empty documents (`--- null`, which kubectl rejects). Nothing else changes.

### H57d amendment R1: the H57d runner, the read-only Od render and the hold length (2026-10-21)

Recorded 2026-10-21, before any scored H57d request. Two wiring smokes ran first under the
host lock (discarded, disclosed in the run section). Nothing here changes a row's arms, model
files, statistic, gate, threshold, hold count or prediction.

**Files.** `bench/h57d/`: `hold.sh` (the host lock `flock -w 21600`, the `load1` <= 24 start
rule, the 15 GB `/` floor, a MemAvailable >= 20 GiB start floor so a hold cannot push the shared
host into swap, a disk watchdog, cluster deletion at the end, also on failure), `hold-run.sh`
(cluster, sampler, arms), `arm.sh` (one arm: render, readiness, phases or quality, teardown),
`h57d.py` (arm table and orders, renders, the read-only Od transform, the 5 s P-core load
sampler, the load gate, `score`), `k3d-shim.sh`, `convert.sh`, `reproduce.sh`. GSM8K items, prompt,
grader, the 1,000-token TTFT prompt and the bootstrap are H54's (`bench/h54`, PR #169), imported,
not copied. Tests: `bench/h57d/test_h57d.py` (flags and CPU mask, renders, the read-only
transform, the sampler arithmetic, the load gate, every H57da and H57dm branch, the spare hold),
in CI.

**Read-only models.** The campaign directory (`~/k3dgpu-h57d`, a link to the second disk) is
bound into the node read-only at `/var/apb-models` by the k3d shim; nothing is copied into the
node. Ld and Ld-mtp mount it read-only. Od: `k3d-gpu agent manifest` as written, `stage.py
ovms` (PVC to that hostPath), then `h57d.py od-ro`: the `--pull` init container is removed (its
only write is `graph.pbtxt`, which `convert.sh` made once with the same image and the same
arguments; sha256 `c57ecf35...`, `max_num_seqs` 256, `enable_prefix_caching` true, `cache_size`
0, device CPU), the models mount is read-only, and `--cache_dir` moves to an emptyDir. The
serving container's image, other arguments, request (16 CPUs) and P-core label are unchanged.

**Drafter.** `convert.sh` runs b11749's own `convert_hf_to_gguf.py` and `llama-quantize` (Q4_0)
in `full-b11749`. The image's transformers 4.57.6 cannot read the QAT assistant's
`tokenizer_config.json` (`AttributeError: 'list' object has no attribute 'keys'`, the
transformers 5 `extra_special_tokens` list), so the converter runs with transformers 5.20.2 (the
latest stable) on `PYTHONPATH`; a build with 5.5.4 gave byte-identical bf16 and Q4_0 files
(Q4_0 sha256 `8eb21d5b...`, 321,145,696 bytes). Model files: sha256 of every LFS file equals the
Hub's at the pinned revisions (`~/k3dgpu-h57d/weights.sha256`, in each Reproduce header).

**Flags read at b11749** (`llama-server --help` of the pinned image): `-t`, `-C/--cpu-mask`
(hex), `--cpu-strict`, `--jinja`, `-md`, `--spec-type draft-mtp`, `--spec-draft-n-max` (default
3) exist. The server's defaults otherwise: 4 slots, context from the model (262,144, unified KV).

**Hold length.** The registered 45 min cannot hold three arms: in the smoke one Ld arm's
phases took 21 min (60 s idle, warmup 214 s, N=1 519 s, N=4 330 s, TTFT 85 s, at about 4 tok/s).
Each hold's cap becomes 75 min (`timeout -k 30 4500`; arms start only with 900 s left, the
quality hold's with 600 s). The number of holds (at most 5 for H57da and H57dm) is unchanged.

**Disclosed from the smokes, not acted on.** Ld's 8 server threads were pinned one per physical
P-core as registered (CPUs 0, 2, ..., 14, read from `/proc/1/task/*/status`) inside the pod's
cpuset 0-15; Od's threads float over 0-15. With strict pinning a thread whose CPU is taken by a
foreign process stalls the step, so the shared host may cost Ld more than Od. The registered flags
are kept; any H57da verdict is scoped to them.

### H57d run (i9-13900T, 2026-10-21)

**Reproduce:**

```text
# provenance
date:            2026-10-21T03:34:47Z (hold 1 start) to 2026-10-21T07:10:33Z (hold 4 end), 4 lock holds
hypothesis:      H57da, H57dm (H57db not run)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P = CPUs 0-15, E = 16-31), no amx_*, no avx512*,
                 avx_vnni; microcode 0x133; PL1 = PL2 = 117 W; governor powersave, EPP performance
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  458bca0e (#175, amendment R1 and bench/h57d), node image
                 cryptoandcoffee/k3d-gpu:0.1.63@sha256:b6fc46f60a47948eb1c4e6c5e80059c7f581427429c35a2627c58fc2307e4b76
runtimes:        ghcr.io/ggml-org/llama.cpp:server-b11749@sha256:9fbaa06d7f4449e2ae0d02b40e0431f7c212dcf231c9e3a7777a6afc2cc8f312;
                 openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58 (2026.4.1)
models:          google/gemma-4-26B-A4B-it-qat-q4_0-gguf @ d1c082be (gemma-4-26B_q4_0-it.gguf sha256 3eca3b8f...);
                 OpenVINO/gemma-4-26b-a4b-it-int4-ov @ 4e5885c3 (openvino_language_model.bin sha256 0f887130...);
                 QAT drafter google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant @ 95371415 -> Q4_0 GGUF
                 built here (sha256 8eb21d5b...); every LFS file equals the Hub's sha256 (weights.sha256)
energy source:   RAPL package-0 through energy.sh and sudo
records:         bench/h57d/results/2026-10-21/ (per hold reproduce.txt, load.jsonl, per arm deploy.yaml,
                 cpus.txt, load-*.json, energy-*.json, phases.jsonl, quality.json, server.log), verdict.json
```

Commands (from this commit, `LOCK` = the host's `live.lock`, `SUDO_ASKPASS` for RAPL):

```bash
LOCK=<live.lock> G=~/k3dgpu-h57d bench/h57d/convert.sh            # drafter GGUF + Od's graph.pbtxt
for h in 1 2 3; do LOCK=<live.lock> bench/h57d/hold.sh $h perf; done
LOCK=<live.lock> bench/h57d/hold.sh 4 quality
python3 bench/h57d/h57d.py score ~/k3dgpu-h57d/results/H57d > verdict.json
```

Two wiring smokes ran first under the lock and are discarded: smoke 1 (2026-10-20 23:57Z, every
arm ready, one 16-token request each), smoke 2 (00:03Z, Ld's full perf phases, which set the
hold length in amendment R1). All 12 scored arm runs and 3 quality runs were measured; no hold
was flagged (starts waited 120, 240, 0, 0 s for `load1` <= 24) and no spare hold was used. The
P-core load sampler read `load1` 16.5-85.8 (mean 37.4) in hold 1, 16.0-53.1 (28.1) in hold 2 and
9.3-20.7 (15.5) in hold 3; package temperature peaked at 94-100 C in every hold. Each server's
cgroup cpuset was 0-15 (the P-core balloon); Ld and Ld-mtp pinned their 8 compute threads to CPUs
0, 2, ..., 14, Od's threads floated over 0-15 (`cpus.txt`).

N=1 decode tok/s (8 requests per hold, 256 tokens, greedy, `ignore_eos`):

| Arm | Hold 1 | Hold 2 | Hold 3 | Pooled median (24) | Foreign P-core load, N=1 mean CPU-s/s | GSM8K 0-49 |
|---|---|---|---|---|---|---|
| Od (k3d-gpu, OVMS int4 OVB) | 6.39 | 6.85 | 8.55 | 6.90 | 4.11 | 50/50 |
| Ld (llama.cpp QAT Q4_0) | 3.13 | 3.42 | 8.07 | 3.50 | 5.62 | 50/50 |
| Ld-mtp (Ld + QAT MTP drafter) | 4.60 | 5.41 | 12.28 | 5.41 | 5.91 | 50/50 |

Load gate: spread of the arms' foreign P-core load 1.80 CPU-s/s <= 2.0, matched. Quality gate:
every arm 50/50, all pass.

**H57da: INCONCLUSIVE** (`h57d.py score`): X = Ld-mtp (the faster gate-passing llama.cpp arm).
Ld-mtp/Od = 0.785, 95% bootstrap CI 0.692-1.561, per hold 0.721, 0.791, 1.436. The CI is above
neither 1.05 nor wholly below it, and X is not faster in every hold. The prediction (Ld/Od in
[1.1, 1.8]) failed for the plain arm: Ld/Od was 0.49-0.50 in holds 1-2 and 0.94 in hold 3.

**H57dm: SUPPORTED**: Ld-mtp/Ld = 1.548, 95% bootstrap CI 1.057-3.017, faster in each hold
(1.471, 1.580, 1.520), quality gate passed (50/50). The prediction (REFUTED, ratio in
[0.8, 1.3]) failed: on this client the QAT MTP drafter in llama.cpp b11749 pays at batch 1.

Reported, not gated (medians over the 3 holds): TTFT on the 1,000-token prompt Od 26.5 s, Ld
19.2 s, Ld-mtp 18.5 s; N=4 aggregate tok/s Od 11.6/16.3/17.2, Ld 6.6/8.7/19.3, Ld-mtp
11.7/11.0/19.7 (holds 1/2/3); package J/output token over N=1, gross, Od 15.3, Ld 26.9, Ld-mtp
19.1 at 87-99 W. Net of the arm's idle power the values are near 0 (-0.1 to 1.1 J/token): the
shared host held the package near its 117 W limit at idle, so the net figure does not resolve.

What the numbers show, beside the verdicts. The llama.cpp arms moved with host load far more
than Od: from holds 1-2 (mean `load1` 28-37) to hold 3 (15.5) Ld rose 2.4-2.6x and Ld-mtp 2.3-2.7x,
Od 1.25-1.34x. That fits the disclosure in amendment R1: with `--cpu-strict 1` each of Ld's 8
threads is bound to one CPU, so a foreign process on any of them stalls every step, while Od's
threads can move. In the quietest hold Ld-mtp led Od by 1.44x and Ld trailed by 6%. The
comparison of engines on a busy client is therefore confounded with thread placement, and a
re-run (a new amendment) would add Ld without `--cpu-strict` or on 16 threads as its own arm.

Confounds:

- Shared host: about 50 production containers and other sessions ran throughout; arms were
  rotated over holds, not interleaved per request, so load drift between arms inside a hold is
  only partly controlled (the load gate measures the mean, it passed at 1.80 of 2.0).
- `K3D_GPU_VENDORS=hybrid` still deployed the node image's baked intel-gpu-plugin (bug fixed in
  PR #180): it opened the host's `/dev/dri` in the node and advertised the UHD 770. No arm
  requested a `gpu.intel.com` resource, and every arm ran on the CPU.
- Ld-mtp's drafter is self-converted (source rank 2); its outputs matched quality (50/50) but are
  not bit-identical to Ld's.
- OVB is INT4_ASYM from the non-QAT base model; the QAT-matched IR (OVQ) was not run here.

**H57db: INCONCLUSIVE (not run).** The memory gate failed: MemAvailable read 42.9 GB at hold 1's
start and 37.6 GB after the campaign, below the 100 GiB the stock bf16 arm needs (51.6 GB of
weights plus `VLLM_CPU_KVCACHE_SPACE` 40 GiB). No bf16 weights were downloaded and no hold ran.

Cleanup: every `k3dgpu-d-*` cluster was deleted at each hold's end; the campaign's model files,
images it pulled and its directory on the second disk are removed after this run.

### Wildct pre-registration: client Raptor Lake (i7-13700, 2026-10-20)

Written 2026-10-20, before any H52w, H18w, H25w, H28w, H34w or H57w measurement on this host.
Nothing here is a measurement except the host facts under "Host". No k3d-gpu command has run
on wildct. Every verdict already recorded keeps the host and rule it was graded with.

**Why.** Every SUPPORTED client-CPU row ran on one i9-13900T. wildct is a second client
Raptor Lake part with the same core layout (8 P-cores with HT, CPUs 0-15) but fewer E-cores (8,
not 16), a 30 MB L3 and a 35 W PL1, in a production box. The `w` rows replicate the
CPU-only SUPPORTED rows that back doc claims (H18, H25, H28, H34) on their original protocol
and model, prove detection on a client host (H52's question; H52 ran on the Xeon), and give
the Gemma 4 QAT client lane (H57d, desktop) a second AMX-less host. A replication keeps the
original row's model, probe, scorer, thresholds and outcome table; every change is listed
here, and nothing else differs.

**Host (`wildct`, reached by IP 100.66.38.121).** Intel Core i7-13700 (family 6 model 183,
microcode 0x133), 1 socket, 8 P-cores (CPUs 0-15, HT pairs 2n, 2n+1, max 5.1 GHz) and 8 E-cores
(CPUs 16-23, max 4.1 GHz), 1 NUMA node, 30 MiB L3, 125 GiB RAM (MemAvailable 78 GiB), 383 GiB
swap (0.6 GiB used), Manjaro, kernel 6.6.144, Docker 29.6.1 (cgroup v2, data root on `/`).
Flags include `avx2 avx_vnni gfni vaes sha_ni ibt user_shstk`; no `avx512*`, no `amx_*`, no
`hypervisor`. RAPL `intel-rapl:0` (package-0) is root-only; PL1 35 W. GPUs (never used, see
W8): UHD 770 iGPU `8086:a780` on `i915`, RTX 4000 SFF Ada `10de:27b0` and RTX 2000 Ada
`10de:28b0` on `nvidia`. No DSA, IAA, QAT, NPU or Gaudi. `/` (the only disk) 3.67 TB, 94% used,
243 GB available after the box owner's own cleanup on 2026-10-20 (31 GB before it). The box serves about 18 production containers (`feed-my-algorithm-*`,
`know-my-neighbor-db-1`, `proxmox-dc-manager`, `tos-rate-calculator`, `searxng-*`, ...);
`load1` 1.4-1.5 at registration. Nothing of theirs is stopped, started or changed, and
nothing pre-existing on the host is touched; two exited `k3d-wtm-*` containers belong to the
box owner and are not ours (rule W1 deletes only `k3dgpu-w-*`).

**Product fix found while writing this, merged before any hold.** With `intel` not active
(left out of `K3D_GPU_VENDORS`, or `none`), `up` still deployed the baked `intel-gpu-plugin`
DaemonSet: k3s applies every baked manifest it is not told to disable, and that DaemonSet's
only nodeSelector is the architecture, so on any host with an Intel GPU it opened the host's
`/dev/dri` (visible in the privileged node) and advertised the GPU. On wildct that would put
the iGPU into the cluster against the box owner's rule. PR #180 (`up` disables both Intel GPU
stacks when `intel` is not active; `K3D_GPU_VENDORS=auto,-<vendor>,...` keeps auto detection
minus the listed vendors, and `up` names what it opted out; regression tests in
`tests/detect_test.sh`) is merged and released before the first hold. Rows run elsewhere
with `intel` left out on an Intel GPU host ran with that plugin deployed; they are not
re-graded here.

**Shared rules W1-W8** (every `w` row):

1. **Lock and hold.** Each hold runs through `bench/wildct/hold.sh NAME CAP NEED_GB CMD`:
   `flock -w 21600 ~/k3dgpu-bench-wildct/live.lock` (this host's campaign lock); hold-start
   rule `load1` <= 12 (half the threads), 2-min polls for at most 60 min, then the hold runs
   flagged; CMD under `timeout -k 30 CAP`. At the end, also on failure or a signal, it
   deletes every `k3dgpu-w-*` cluster, the bench-load containers and the RAPL reader (W4), and
   nothing else. Records go to `~/k3dgpu-bench-wildct/runs/NAME/` and come back into the repo.
2. **CPUs.** The treatment keeps its original placement: the P-core balloon (CPUs 0-15) for
   H25w, H34w and H57w, the E-core balloon for H18w's sandboxes, the node pinned to the
   P-cores for H28w (`NODE_CPUS=0-15`, a new `probe.sh` knob, default unchanged; H28 A2 pinned
   it to "the CPUs outside the load set", which here would include the kept-free CPUs).
   The background load (`bench/load/load.py`, `typical`) runs on E-cores 20-23 only, through
   `load.py`'s `LOAD_CPUS` override (H56's, default unchanged): CPUs 16-19 stay outside every
   campaign cpuset, so at least 4 threads are always free for the production services (box
   owner's rule). The exceptions are idle: H18w's one 1-CPU sandbox pod and k3s's own
   pods, which float. On the i9 the load ran on 16 E-cores; here 4, so the load is lighter
   and its matching rule (`load.py matched`, amendment 27) is unchanged; `busy` is read over
   20-23 minus the treatment's cpuset. The replications test the hypothesis, not the i9's
   numbers.
3. **Disk and kubelet thresholds.** `/` is 100% used by the box's own data, so every cluster
   gets crescent amendment C1's kubelet thresholds in absolute form (1% of this disk is 36 GB,
   more than is free): `K3D_GPU_K3S_ARGS="--kubelet-arg=eviction-hard=memory.available<100Mi,nodefs.available<2Gi,imagefs.available<2Gi,nodefs.inodesFree<1%"`
   (H28w: passed by `probe.sh` to its plain `k3d cluster create`). It changes when the
   kubelet evicts, nothing else, for both arms alike. Without it the node is tainted
   `disk-pressure` (C1's smoke 1 showed the same on crescent).
4. **RAPL.** Package energy is root-only and no password is kept on wildct. `hold.sh` starts
   one privileged container (`k3dgpu-w-rapl`, `python:3.13-slim` at `probe.sh`'s pinned
   digest, no network, cpuset 20-23, `sleep`) and exports `K3D_GPU_RAPL_EXEC="docker exec
   k3dgpu-w-rapl"`; `load.py` and `probe.sh` already read through it, and `h34.sh`, `run.sh`,
   `sandbox_round.sh` and `energy.sh` (H57w) learn the same variable (sudo first, as before;
   `energy.sh` adds `-i` for its streaming reader). It reads `energy_uj` and nothing else.
5. **Covariates.** `bench/wildct/sample.py` beside every hold, every 5 s: `load1`, busy
   CPU-s/s of CPUs 0-15, 16-19 and 20-23, this campaign's containers' CPU-s/s and everything
   else's ("foreign"), MemAvailable, package temperature, `/` available. Reported, never
   gating, except where an original row gates (its own load rule) and H57w's load gate.
6. **Disk.** A hold starts only when `/` has at least 15 GB plus the row's need available,
   and its watchdog stops it when `/` drops below 15 GB: H52w and H18w 3 GB, H25w and H34w
   8 GB, H28w 35 GB (its image and copy churn), H57w 40 GB. Models live in
   `~/k3dgpu-bench-wildct/models`, downloaded on wildct from the Hub at the original rows'
   revisions (`bench/xhost/fetch_models.py`, `huggingface_hub` 1.21.0), each file's sha256
   checked against the Hub's LFS sha256 and recorded; never copied from another host. A row
   whose need never fits before the campaign ends is INCONCLUSIVE (not run), with the
   readings recorded. Everything this campaign creates on wildct is deleted at the end
   except the run records, which are copied back into the repo first.
7. **Pins.** k3d v5.9.0, helm v4.3.1, kubectl v1.37.1 (each the newest release on 2026-10-20)
   in `~/k3dgpu-bench-wildct/bin`, checked against their upstream checksums
   (`bench/wildct/install.sh`); k3d-gpu: the newest release at the first hold (the first one
   carrying PR #180: 0.1.65 or later), its `.deb` checked against the release's `SHA256SUMS` and unpacked in
   `~/k3dgpu-bench-wildct/k3d-gpu`, and its node image `cryptoandcoffee/k3d-gpu:<that
   version>`, digest recorded. The bench scripts run from this repository at a merged commit,
   shipped to wildct as an archive and never edited there; their `scripts/k3d-gpu` must equal
   the release's launcher apart from the version line, or the hold does not start. OVMS
   `a928903f...` (2026.4.1), `load.py`'s stress-ng pin and the original models as each row
   names them.
8. **GPUs.** Box owner's rule: no GPU on wildct is used, the iGPU included. Every cluster runs
   with `K3D_GPU_VENDORS=auto,-nvidia,-intel`, so no GPU device plugin, no nvidia runtime and
   no `--gpus` flag; no command here opens `/dev/dri` or `/dev/nvidia*` (`bench/wildct/inventory.sh`
   reads PCI sysfs only). The node container is privileged, as every k3d-gpu node is, so the
   device nodes exist inside it; H52w checks that no pod mounts or asks for one. This
   opt-out is H52w's one deviation from a plain `up`; W3 is the host's disk.

**H52w (detection as found).** Inventory read before this pre-registration from sysfs,
/proc and PCI sysfs (`bench/wildct/inventory.sh`, which the run repeats before and after).

| Engine | Host inventory | Expected from `up` with the GPU opt-out |
|---|---|---|
| Core types | `cpu_core` 0-15, `cpu_atom` 16-23, no `cpu_lowpower` | hybrid active, `k3d-gpu.io/hybrid-cpu=true`, the balloons policy pod Running; `k3d-gpu test`'s P-core pod inside 0-15 and E-core pod inside 16-23 |
| CPU ISA | `avx avx2 avx_vnni fma f16c gfni vaes sha_ni ibt user_shstk`; no `avx512*` | launcher labels `cpu-cpuid.` AVXVNNI, VAES, GFNI, SHA, CETIBT, CETSS (`=true`), exactly these; NFD's own `cpu-cpuid.*` labels recorded; no `AVX512*` label from any source |
| AMX | no `amx_*` flag; family 6 model 183 | amx not active; no `AMX*` label |
| NUMA | 1 node | numa not active; no `k3d-gpu.io/numa-nodes` label |
| NVIDIA GPU | 2 cards, `nvidia` module loaded | detected by `detect` as found; `up` names it as opted out; no `nvidia.com/gpu` allocatable, NVIDIA plugin not running, no `--gpus` on the node container |
| Intel GPU | UHD 770 `8086:a780` on `i915`, card1/renderD129 | detected by `detect` as found; `up` names it as opted out; no `gpu.intel.com/*` allocatable; no `intel-gpu-plugin` or DRA pod |
| DSA, IAA, QAT, NPU, Gaudi | none | none active, named or advertised |
| Pods | | no pod mounts a hostPath under `/dev/dri`, `/dev/nvidia*` or `/dev`, and no container asks for a GPU resource (`gpu-scan.txt` "clean") |
| Host | | the second inventory equals the first apart from its first line |

Procedure, one hold (`hold.sh H52w 1800 3 bench/wildct/h52w.sh <out>`): the inventory;
`k3d-gpu detect` as found (no variable: sysfs reads only, nothing opened); then with the
opt-out and W3: `detect`, `doctor`, `up` (cluster `k3dgpu-w-h52`, scratch `KUBECONFIG`),
the node object once every pod is Running, the pod GPU scan, `k3d-gpu test`, `down`, the
inventory again. A row matches when its expected items are all there and nothing it rules out
is there. "Named" means `up`'s own output names the engine. SUPPORTED when every row matches,
REFUTED when any does not; a missing or phantom engine is a k3d-gpu bug (regression test,
fix, re-run as a new row; H52w's verdict stands).

**Per row (commands run inside `hold.sh`; `$E` is the GPU opt-out plus W3 and W4's exports).**

- **H18w.** `hold.sh H18w 2100 3` around three `IMAGE=<release image> OUT=<out>
  K3D_GPU_CLUSTER=k3dgpu-w-sbx $E bench/sandbox_round.sh <run>`, runs 1-3. `TARGETS` is the
  runner's default, which on wildct resolves to `100.66.38.121:22 192.168.8.238:22
  pypi.org:443` (recorded). Changes: the cluster name, the release node image (H18 built one
  from its branch), the opt-out (H18's `up` was auto on the i9), W3, W4. Scored by H18's
  outcome table.
- **H25w.** `hold.sh H25w 1500 8` around `IMAGE=<release image> MODELS=<models> OUT=<out>
  LEVELS=typical CLUSTER=k3dgpu-w-h25 SEED_BASE=2560 HOLD=1440 LOAD_CPUS=20-23 $E
  bench/shared-prefix/run.sh`. `OpenVINO/Qwen3-4B-int4-ov` @ `b467368d`, the draft
  `OpenVINO/Qwen3-0.6B-int4-ov` @ `f864c610` staged as H25x stages it (`fetch_models.py
  --draft`). The agent manifest sizes its CPU request as it ships and keeps its default CPU
  Fast Draft in both arms, as H25 run 4 did. Changes: W2, W3, W4, the opt-out, the cluster
  name, new seeds.
- **H34w.** `hold.sh H34w 1500 8` around `K3D_GPU_IMAGE=<release image>
  H34_CLUSTER=k3dgpu-w-h34 H34_VENDORS=auto,-nvidia,-intel H34_LEVELS=typical
  H22_MODELS=<models> LOAD_CPUS=20-23 $E bench/hetero-spec/h34.sh <out>`
  (`H34_LEVEL_S` 1200, 12 pairs). Changes: the opt-out in place of `intel,hybrid` (the iGPU
  gave a CPU pod nothing on the i9), W2, W3, W4, the cluster name. J/token is from raw package
  energy, as H34 did; the package carries the production containers' draw too (W5 records it).
- **H28w.** `IMAGE=<release image> bench/zero-copy-models/probe.sh prep` inside a lock hold of
  its own (`hold.sh H28w-prep 900 35`), then `hold.sh H28w 1500 35` around `WORK=<out>
  IMAGE=<release image> CLUSTER=k3dgpu-w-h28 LEVELS=typical HOLD=1440
  LOCK=~/k3dgpu-bench-wildct/inner.lock NODE_CPUS=0-15 LOAD_CPUS=20-23
  K3D_GPU_K3S_ARGS=<W3> bench/zero-copy-models/probe.sh run`. The model images it builds are
  deleted at the end of the run (as `probe.sh` does) and by `hold.sh` if it fails. Scored by
  `score.py` as H28x is.
- **H57wa, H57wm.** H57d's arms Od, Ld, Ld-mtp, phases, rotation over 3 performance holds and
  a quality hold, statistic, load gate and 75-min holds, by H57d's runner (`bench/h57d`,
  amendment R1), whose host settings become knobs with unchanged defaults: `H57D_ROW=H57w`,
  `H57D_PREFIX=k3dgpu-w-`, `H57D_LOAD1_MAX=12`, `H57D_VENDORS=auto,-nvidia,-intel`,
  `H57D_IMAGE=<release image@digest>`, `H57D_K3S_ARGS=<W3>`, and `K3D_REAL` for the shim. Each
  H57w hold runs as `hold.sh H57w-<n> 4600 40 env G=~/k3dgpu-bench-wildct/h57 ...
  bench/h57d/hold.sh <n> <mode>` (the inner lock is a private file, since W1 holds this host's);
  H57d's own 15 GB and MemAvailable >= 20 GiB start floors stay. The P-cores are the same CPUs
  0-15 as on the i9 (Ld's mask `0x5555`, Od's 16-CPU request), so no arm flag changes. Models at H57d's revisions: `google/gemma-4-26B-A4B-it-qat-q4_0-gguf`
  @ `d1c082be` (`gemma-4-26B_q4_0-it.gguf` only), `OpenVINO/gemma-4-26b-a4b-it-int4-ov` @
  `4e5885c3`, `google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant` @ `95371415`
  converted by `bench/h57d/convert.sh` (which also writes Od's `graph.pbtxt`). Sampling and
  thinking are H57d's (temperature 0, thinking off). Nothing of the host's own model stores
  (an `ollama` service and its directories, other projects' models) is read or written.
- **H57wb.** As registered in its row; the readings are taken once, before the first H57w hold.

Not registered here: an H55-style zero-touch check. Its prerequisite, the model optimizer
(PR #163, `agent up --model`), is not merged at registration; a dated amendment registers it
if it merges while this host is in use.

**Runner changes (this PR).** `bench/wildct/` (hold.sh, sample.py, inventory.sh, h52w.sh,
install.sh, verify_models.py, clusters.py, setup-models.sh, setup-images.sh); `NODE_CPUS` in
`probe.sh`, `K3D_GPU_RAPL_EXEC` in `energy.sh`, `h34.sh`, `run.sh` and
`sandbox_round.sh`, and the H57d runner's host knobs, every default unchanged.

**Order and caps.** Setup outside the lock (install, model downloads, archive) and one
wiring smoke inside it (an `up` with the opt-out and W3, the GPU scan, `down`; no inference
request, discarded and disclosed). Then H52w (1 hold), H18w (1), H25w (1), H34w (1), H28w
(prep + 1), H57wb's reading, H57wa and H57wm (at most 5). A failed hold counts; each row may
repeat a failed hold once. At most 12 holds in all. A protocol change is a dated amendment
merged before the data it governs.

### H5 amendment A1: stage-1 harness and definitions (2026-10-19)

Written 2026-10-19T22:30-07:00, before any H5 measurement. It fixes what the
[H5 pre-registration](docs/prefill-guard.md#pre-registered-ab-test-h5) left open; the
hypothesis, arms, caps, Latin square, n, probes, thresholds and verdict rules are unchanged.
Before this text, one shortened harness check ran under `live.lock` (method `loop`, round-1
order, 2 s idle, 3 s lead, 60 s neighbour): it checked the plumbing and the cpufreq restore
(read back identical to the snapshot). Its numbers are not used or reported.

**Open choices fixed here.**

1. Server, model, prompt. The wave-1 probe recorded none of them
   ([H3 host-level observation](#h3-host-level-observation-i9-13900t-2026-10-16)). Stage 1 uses
   those of the [H3 run](#h3-run-i9-13900t-2026-10-16): OVMS 2026.4.1
   (`openvino/model_server@sha256:a928903f...`), `OpenVINO/Qwen3-4B-int4-ov` at b467368d
   (`openvino_model.bin` sha256 `074214fa...`, the `bench/competitors.lock` pin), CPU, the
   model's own `graph.pbtxt` (prefix caching on, `cache_size` 0). It runs as a host
   container with `--cpuset-cpus 0-14`, reached on loopback. The prompt is
   [`bench/prefill-guard/prompt.txt`](bench/prefill-guard/prompt.txt) (sha256 `1321749d...`,
   about 1026 prompt tokens). Every request carries `--unique-prompts`, so prefix caching
   never skips the prefill (H3 also used unique ~1000-token prompts).
2. `bench/llm_load.py` is the one on `main` (the `claude/report-bench` branch is merged).
   The decode request also sends `--ignore-eos`, so it decodes all 128 tokens.
3. Neighbour. `stress-ng` is not installed on the host, and installing it would change the
   host. The neighbour is H3's image
   (`ghcr.io/colinianking/stress-ng@sha256:2ea9a7c3...`, 0.22.01) with the registered
   arguments, pinned with `docker run --cpuset-cpus 17-31` in place of `taskset -c 17-31`.
4. A run, in order: 60 s idle; the arm's cap written to CPUs 16-31; the neighbour started
   (none for Q; Q keeps the 15 s lead); 15 s; RAPL and `package_throttle_count` read; 1
   warm-up and 3 prefill requests; `scaling_max_freq` of CPUs 16-31 read; the decode
   request; the tool proxy on CPU 16; RAPL and throttle count read again (package W is
   energy over that window). Then the harness waits for the neighbour's 150 s to end,
   restores the snapshot and checks it reads back identical.
5. Rounds. One OVMS per round, started inside the lock hold and warmed with 2 unrecorded
   requests, removed at round end. Each round is one `live.lock` hold of about 20 min, so
   other probes can run between rounds. Order: `matrixprod` rounds 1-5, then `loop` rounds
   1-5. CMIN is CPU 16's `cpuinfo_min_freq` (800000 kHz).
6. Definitions. Medians and p90 (nearest rank, as `llm_load.py`) are over all 15 recorded
   requests of an arm. Q spread is the max minus min of the five Q per-run medians; the N0
   rule divides it by the Q median. Drift is the difference between the median of the Q
   requests in rounds 1-2 and in rounds 4-5, against that same spread.
7. Verdict per method; the H5 verdict is that of `matrixprod` (the primary method), and
   `loop` is reported beside it. A cap level that reaches half the N0 penalty but not the
   p90 condition meets neither the SUPPORTED nor the REFUTED rule, so it reads
   INCONCLUSIVE. A missing run, a failed request, or a cap read that differs from the arm's
   cap on any of CPUs 16-31 makes that method INCONCLUSIVE. Nothing is re-run.
8. Stage 2 repeats "the level from stage 1", so it runs only if stage 1 is SUPPORTED.
   Otherwise the NRI write path stays unverified, as the pre-registration says.

**Disclosed before running.** Under definition 6 the drift test cannot fire: the median of
two runs' pooled requests lies between those two runs' medians, so the rounds 1-2 and 4-5
medians are both inside the range of the five Q run medians. The rule is kept as registered
and the drift value is reported. A steady trend in Q widens the spread instead, which makes
the N0 rule harder to pass.

**Safety.** Before the first round, every CPU's `scaling_max_freq`, `scaling_min_freq`,
`scaling_governor` and `energy_performance_preference` is saved
(`bench/prefill-guard/cpufreq.sh snapshot`). An EXIT trap restores it, a restore follows
every run, and each restore must read back byte-identical. A round refuses to start if the
host differs from the snapshot. Only `scaling_max_freq` of CPUs 16-31 is ever written.

Implementation: [`bench/prefill-guard/run.sh`](bench/prefill-guard/run.sh) (one round),
[`cpufreq.sh`](bench/prefill-guard/cpufreq.sh) (the only root step) and
[`analyze.py`](bench/prefill-guard/analyze.py) (the rule above). Tests:
`bench/prefill-guard/test_analyze.py` covers SUPPORTED with the chosen level, REFUTED,
half without the p90 condition, N0 inside the Q spread, a cap read that misses, missing and
failed runs, the primary-method verdict, and cap then restore against a fake sysfs.

### H5 run: stage 1 (i9-13900T, 2026-10-20)

**Reproduce:**

```text
# provenance
date:            2026-10-20T05:49:23Z (first run) to 2026-10-20T09:56:12Z (last run), 10 lock holds
hypothesis:      H5 (stage 1, host level, no cluster)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P = CPUs 0-15, E = 16-31; E max 3900000 kHz,
                 min 800000 kHz; intel_pstate active, governor powersave, EPP performance)
host kernel:     6.6.128-1-MANJARO
accelerators:    k3d-gpu detect: nvidia, intel (i915), hybrid (no accelerator used; no cluster)
k3d-gpu commit:  45862ee (#147, amendment A1 and bench/prefill-guard)
node image:      none (no cluster)
model:           OpenVINO/Qwen3-4B-int4-ov @ b467368d, openvino_model.bin sha256 074214fa29ab1b47...;
                 prompt bench/prefill-guard/prompt.txt sha256 1321749d2aa5dd10..., 1024-1027 prompt tokens
runtime:         openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58
                 (2026.4.1), CPU, docker --cpuset-cpus 0-14, no device flags; neighbour
                 ghcr.io/colinianking/stress-ng@sha256:2ea9a7c3828eda1b1ca3d0b0c5a669ec252f9caf97a6a33aedcea6b2a66d1cbe
                 (0.22.01), --cpuset-cpus 17-31, --cpu 15 --cpu-method <m> --timeout 150s --metrics-brief
energy source:   RAPL package (/sys/class/powercap/intel-rapl:0/energy_uj, read through sudo);
                 PL1 117 W, PL2 230 W (constraint_*_power_limit_uw)
runs per arm:    5 per method (15 prefill requests, 5 decode requests); 50 runs
```

Commands: one `live.lock` hold per round, `matrixprod` rounds 1-5 then `loop` rounds 1-5:

```bash
bench/prefill-guard/cpufreq.sh snapshot > snapshot.txt          # as root, once, before round 1
PG_SNAPSHOT=snapshot.txt PG_MODEL_DIR=<dir of OpenVINO/Qwen3-4B-int4-ov> PG_LOCK=<live.lock> \
  bench/prefill-guard/run.sh <out> <matrixprod|loop> <1-5>     # one round, sudo for cpufreq.sh
bench/prefill-guard/analyze.py <out> --out <out>/analysis.json
```

Records (per run `run.json`, `prefill.json`, `decode.json`, the neighbour's `nb.log`; per round
the server log and warm-up; the cpufreq snapshot and the read after the last round;
`analysis.json`; `SHA256SUMS`):
[bench/prefill-guard/results/2026-10-20/H5/](bench/prefill-guard/results/2026-10-20/H5/).

All 50 runs completed: every request returned, every cap read during a capped run showed the
cap on all 16 of CPUs 16-31, and the neighbour was still running at the cap read and at the end
of every run's measurements (47.6-104.0 s after the 15 s lead). Rounds waited 0-15 min for the
lock between holds. Start-of-run package temperature read 77-100 C and `load1` 5.6-17.4, from
the shared host's background load.

Penalty = arm median (or p90) `ttft_s` / Q median (or p90) - 1, over 15 requests per arm.
Neighbour loss = 1 - median bogo-ops/s / N0's. Tool proxy, decode and power are run medians;
throttle is the `package_throttle_count` delta over the measured window.

`matrixprod` (primary):

| Arm | TTFT median / p90 s | Run medians s | Penalty median / p90 | Neighbour bogo-ops/s (loss) | Tool proxy s (x N0) | Decode tok/s (x N0) | Package W | Throttle |
|---|---|---|---|---|---|---|---|---|
| Q | 7.15 / 8.25 | 6.90, 7.19, 7.29, 7.28, 6.82 | - | - | 0.89 | 10.43 | 102.1 | 1111 |
| N0 | 10.05 / 11.62 | 10.62, 9.32, 11.41, 9.85, 10.27 | +40.6% / +40.8% | 20713 | 1.70 | 9.35 | 114.6 | 96 |
| C75 | 9.75 / 14.98 | 8.58, 9.65, 9.71, 14.98, 9.96 | +36.4% / +81.6% | 19968 (3.6%) | 1.79 (1.05) | 9.38 (1.00) | 114.8 | 112 |
| C50 | 9.16 / 9.73 | 9.16, 8.95, 9.73, 9.29, 9.06 | +28.1% / +18.0% | 13465 (35.0%) | 2.70 (1.59) | 9.44 (1.01) | 115.0 | 586 |
| CMIN | 8.82 / 9.28 | 8.30, 8.98, 8.95, 9.00, 8.82 | +23.3% / +12.5% | 5719 (72.4%) | 6.36 (3.74) | 11.52 (1.23) | 114.6 | 3749 |

Q spread 0.470 s (6.6% of the Q median); N0's +40.6% is above it. Drift 0.193 s (rounds 1-2
vs 4-5), inside the spread (amendment A1 disclosed that this test cannot fire). Half the N0
penalty is +20.3%: C75 +36.4%, C50 +28.1%, CMIN +23.3%, so no level reaches it. C50 and CMIN
have a p90 penalty below N0's; C75 does not (one run at 14.98 s).

`loop` (secondary):

| Arm | TTFT median / p90 s | Run medians s | Penalty median / p90 | Neighbour bogo-ops/s (loss) | Tool proxy s (x N0) | Decode tok/s (x N0) | Package W | Throttle |
|---|---|---|---|---|---|---|---|---|
| Q | 6.57 / 7.28 | 6.15, 6.58, 7.28, 6.57, 6.02 | - | - | 0.82 | 11.13 | 103.4 | 806 |
| N0 | 9.19 / 10.01 | 9.92, 9.71, 9.02, 8.87, 8.68 | +39.8% / +37.6% | 27657 | 0.85 | 10.37 | 114.2 | 102 |
| C75 | 9.11 / 10.20 | 8.97, 9.17, 10.12, 8.81, 9.11 | +38.7% / +40.2% | 26592 (3.9%) | 0.91 (1.07) | 10.37 (1.00) | 113.9 | 142 |
| C50 | 8.47 / 9.12 | 7.96, 8.36, 8.53, 8.96, 8.33 | +28.9% / +25.3% | 18218 (34.1%) | 1.31 (1.53) | 10.40 (1.00) | 114.3 | 441 |
| CMIN | 8.13 / 9.22 | 7.84, 7.93, 8.82, 8.43, 8.18 | +23.7% / +26.7% | 8102 (70.7%) | 2.90 (3.40) | 10.79 (1.04) | 114.8 | 2386 |

Q spread 1.262 s (19.2% of the Q median); N0's +39.8% is above it. Drift 0.227 s. Half the N0
penalty is +19.9%: no level reaches it (best CMIN +23.7%).

**Verdict: REFUTED** (`matrixprod`, the primary method, per amendment A1): no cap level
reaches half the guard-off prefill penalty. `loop` is REFUTED by the same rule. Every
INCONCLUSIVE gate passed in both methods (no missing or failed run, every cap read showed the
cap, N0 penalty above the Q spread, no drift). Stage 2 does not run: it repeats the level
stage 1 chooses, and stage 1 chose none (amendment A1, item 8). The NRI write path and the
`down` restore through the node stay verified only against the stubs in
`tests/prefill_guard_test.sh`.

What the numbers show, beside the verdict. The deepest cap (CMIN, E-cores at 800 MHz) cut
the median prefill penalty from about +40% to about +23% in both methods, at a cost of
71-72% of the neighbour's throughput and a 3.4-3.7x slower tool proxy on an E-core. The
50% cap (the doctor suggestion) cut it to about +28% at a 34-35% neighbour loss. Package
power sat at 114-115 W in every neighbour arm, capped or not, against a 117 W PL1, and the
throttle count rose as the cap deepened. So the cap lowered E-core clocks without lowering
package power; the budget it freed did not return the P-core prefill to Q.

Confounds:

- Shared host. About 50 production containers and other sessions ran throughout; `load1`
  read 5.6-17.4 at run start and package temperature 77-100 C. The Latin square spreads
  this over arms; it does not remove it.
- The tool proxy runs on CPU 16, inside the capped set, so its slowdown is the cap's direct
  cost, as registered.
- Decode tok/s is one 128-token request per run; it is reported, not gated.
- Stage 1 is host level: the server and neighbour are host containers, not k3d-gpu pods, and
  the cap is written directly, as the pre-registration's stage 1 specifies.

cpufreq restore: the snapshot of all 32 CPUs' `scaling_max_freq`, `scaling_min_freq`,
`scaling_governor` and `energy_performance_preference` (128 lines, sha256 `7403983b...`) read
back identical after each of the 50 runs, at the end of each of the 10 rounds, and once more
after the campaign (`cpufreq-snapshot.txt` and `cpufreq-after.txt` in the records are equal).

### H55 pre-registration: SLO-gated admission of an E-core neighbour (2026-10-20)

Written 2026-10-20, before any H55 measurement. Nothing below is a result. No H55 run, and
no shortened harness check on the host, has been made.

Revised 2026-10-20, still before any H55 measurement, after review found that the first
control rule could not climb. It stepped up only when a window's p95 was at most
0.9 x 1.10 = 0.99 x the quiet pre-roll median, and a p95 of 20 requests is almost never
below the median. A_slo would have stayed at its 25% start, which is K25 plus a
controller. The rule now uses one statistic, the median, for the reference, the control
windows and the scoring. Other changes in the same revision: a 20-request pre-roll; the
read-error, missing bogo-ops and two-read gates; the neighbour-spread margin in (c); the
hold cap enforced on time; and one campaign lock by default.

**Why.** H3 and H5 are REFUTED: on the i9-13900T, P/E placement is not isolation. E-core
load slows CPU-served prefill on the P-cores through package power, the LLC and DRAM
bandwidth, and H5's deepest E-core frequency cap (CMIN) left +23.3% at a 72.4% neighbour
loss, with package power at 114-115 W in every neighbour arm. H55 asks whether admission
control does better than a static setting: a controller that throttles the neighbour only
as far as the inference server's own TTFT requires.

**Hypothesis.** An SLO gate on the E-core neighbour's `cpu.max`, set from the P-core
inference server's median TTFT, keeps CPU-served prefill within +10% of quiet, while the
neighbour keeps more throughput than both the H5 CMIN frontier and the best static quota at
the same penalty.

**Probe.** The [H5 amendment A1](#h5-amendment-a1-stage-1-harness-and-definitions-2026-10-19)
host-level harness, unchanged except as listed: OVMS 2026.4.1
(`openvino/model_server@sha256:a928903f...`), `OpenVINO/Qwen3-4B-int4-ov` at b467368d,
`--cpuset-cpus 0-14`; the prompt `bench/prefill-guard/prompt.txt` with a unique tag per
request; the neighbour H5's `stress-ng` image, `--cpu 15 --cpu-method matrixprod` on CPUs
17-31; the tool proxy on CPU 16. `matrixprod` only.

1. A run, in order: 60 s idle; the neighbour started (none for Q) with the arm's
   `cpu.max` set at creation (`docker run --cpu-period/--cpu-quota`, so runc writes it
   once) and `--timeout 960s`; for A_slo, `ctl.py` started; a 15 s lead; a 900 s window of
   closed-loop prefill requests (`bench/admission/load.py`: concurrency 1, `max_tokens` 16,
   one request after another until the window ends); one 128-token decode request
   (`--ignore-eos`); the tool proxy. Then the harness waits for the neighbour and
   `ctl.py` to exit. The requests of a run are those started inside the window.
2. A round: one OVMS start, 2 unrecorded warm-up requests, then 20 quiet pre-roll prefill
   requests. The round's reference `REF_r` is their median TTFT (`ctl.reference`), and
   `SLO_r` = 1.10 x `REF_r`. Then the five arms in a 5x5 Latin square
   (round r starts at arm r of Q, N0, K50, K25, A_slo).
3. Arms:

   | Arm | Neighbour `cpu.max` |
   |---|---|
   | Q | no neighbour |
   | N0 (static P/E, as H5's guard-off arm) | `max 100000` |
   | K50 | `750000 100000` (7.5 CPUs) |
   | K25 | `375000 100000` (3.75 CPUs) |
   | A_slo | starts at `375000 100000`; then `ctl.py` owns it |

4. A_slo rule (`bench/admission/ctl.py`). Levels 25, 37.5, 50, 62.5, 75, 87.5 and 100% of
   15 CPUs, period 100 ms. Every 1 s tick it re-resolves the neighbour's cgroup
   (`docker inspect` State.Pid, then `/proc/<pid>/cgroup`), reads `cpu.max` back, consumes
   the newline-terminated records `load.py` appended, and writes the absolute quota of the
   current level. Each 20 completed requests close one window (non-overlapping). It steps
   down one level if the window median > `SLO_r`, up one if it is at most 1.05 x `REF_r`,
   and holds in between; the level is clamped to 25-100%. A read-back that differs from
   its own last write is a stomp, logged and overwritten on the next tick. Detection is
   sampled: a foreign value is counted only if it is still there at a 1 s read-back. A
   value written and reverted between two reads, or written between a read and the write
   that follows it, is overwritten without being counted. A missing cgroup (ENOENT) means
   no write and one log line; any other read error also means no write that tick, logged as
   a read error. `ctl.py` is the one writer the harness starts for that `cpu.max`.
   `load.py` only appends and reads nothing back, so the inference path never waits on the
   controller.
5. Also recorded in every run: package W (RAPL, over the window), the
   `package_throttle_count` delta, the tool proxy, decode tok/s, `load1` and package
   temperature at run start, the IMC `uncore_imc_free_running_{0,1}/data_total` trace
   (`perf stat -I 1000`, as root, over the window), the static arms' `cpu.max` read at the
   window's start and end, and A_slo's full `ctl.jsonl` trace (every tick: level, target,
   read-back, stomp, windows). The IMC trace is passive. It calibrates a later
   aggregate-bandwidth admission row (A_bw), which is not an H55 arm: its threshold would be
   a guess before that calibration.

**Scoring** (`bench/admission/analyze.py`, over every request in each 900 s window).
Penalty = arm median TTFT over all the arm's requests / Q median - 1; the p90 penalty is
reported beside it. Neighbour loss = 1 - the arm's median bogo-ops/s (stress-ng
`--metrics-brief`, real time, over its whole run) / N0's. NB spread = (max - min of the
five N0 runs' bogo-ops/s) / N0's median. Q spread = max - min of the five
Q run medians; drift = |median of Q requests in rounds 1-2 - in rounds 4-5|, against that
spread.

**Verdict rule.**

- INCONCLUSIVE if any gate fails: a run missing, or with a failed request; N0's penalty
  inside the Q spread (divided by the Q median); drift above the spread; a neighbour run
  with no bogo-ops/s value (the `--metrics-brief` line not found); an A_slo run with fewer
  than 4 full windows; a stomp, an ENOENT, a read error or a write error logged by `ctl.py`
  inside the window; `ctl.py` dead at the window end; a neighbour not running at the window
  end; a static arm without both `cpu.max` reads (window start and end) equal to its set
  value, a failed read counting as missing.
- SUPPORTED only if all three hold: (a) A_slo's median penalty is at most +10%; (b) A_slo's
  neighbour loss is below 72.4% (H5 CMIN, `matrixprod`); (c) no static arm (N0, K50, K25)
  has a penalty of at most +10% at a loss no higher than A_slo's plus the NB spread. A
  static arm within the neighbour's own run-to-run noise of A_slo has not been beaten.
- REFUTED otherwise. When only (c) fails, a static quota does as well, and the controller
  is not earned. If A_slo stays at its 25% floor, it is K25 plus a controller, and (b) or
  (c) fails.

**Disclosed before running.**

- As in H5 amendment A1, the drift test cannot fire: the median of two runs' pooled
  requests lies between those runs' medians. It is kept and reported.
- A_slo starts at the lowest level and moves at most one level per window of 20. One 900 s
  window holds about 6 windows at H5's quiet TTFT (7.15 s), and fewer when the neighbour
  slows prefill. Six up-steps take it from 25% to 100%, but only when every window steps
  up. A run that cannot reach a level is scored as run.
- The rule climbs only while a window's median is at most 1.05 x `REF_r`: the controller
  admits more neighbour only while the penalty at its level is about +5% or less, and holds
  between +5% and +10%. That is a simulation of the rule, not a measurement:
  `test_ctl.py` draws log-normal TTFTs fitted to H5's quiet arm (median 7.15 s, p90
  8.25 s). Over 200 seeds of 6 quiet windows, at least 160 end at 75% or above, and at
  most 10 end at the floor; the replaced rule left all 200 at the floor.
- The neighbour outlives the window by the decode request and the tool proxy. `ctl.py`
  keeps ticking until the neighbour exits, so A_slo's loss includes that tail at its last
  level; the static arms' tail is at their set quota.
- In A_slo, `ctl.py` is pinned to CPU 16, beside the tool proxy, and costs one
  `docker inspect` per second.
- A host-side writer that re-asserts the scope's quota (a systemd `daemon-reload`, for
  one) makes the run INCONCLUSIVE when the foreign value is still there at a 1 s read-back.
  One that `ctl.py` overwrites before reading it is not seen.

**Pre-registered prior** (a prediction, not a result; re-derived for the revised rule). N0
about +40% (an H5 replicate); K50 +20 to +30% at about 50% loss; K25 +8 to +15% at about
75% loss. Under that prior the penalty at 25% is already above the +5% up-band, so A_slo
holds at, or steps back down to, the 25% floor, with at most an occasional step to 37.5%:
+8 to +15% at 70-78% loss, close to K25. Then (b) fails, or (c) does, because K25 lies
within the NB spread. Expected verdict: REFUTED. Package power is the coupling;
duty-cycling the neighbour is less power-efficient per op than H5's frequency cap; and a
closed-loop c = 1 prefill load has no idle gap to admit the neighbour into. If H55 holds,
A_slo settles at 50% or above at +10% or less, a loss of about 50% or less.

**Hold cap and locks.** One round per lock hold, at most 7200 s per hold (a round needs
about 92 min); at most 5 holds. The runner takes `K3DGPU_LIVE_LOCK`, by default
`~/k3dgpu-bench.lock` (the path `bench/eatflow/crescent/hold.sh` takes), and, when
`AD_EXTRA_LOCK` is set, that lock after it, each with `flock -w 21600`. No child inherits
a lock descriptor. Every child that can run long (the sleeps, the requests, `docker wait`,
`ctl.py`, `perf`) runs in the background while the round waits on it, so the watchdog's
TERM interrupts the wait at once, and the trap signals the child; root children run under
`setsid`, so sudo relays that TERM to them. A hold that hits the cap writes `INCOMPLETE`
for that round, which is not re-run inside the same hold. H55 writes no
cpufreq value: the campaign cpufreq snapshot is checked, read only, before each round and
after each run, and a mismatch aborts the round.

**Ship gate.** A SUPPORTED H55 does not ship a controller by itself. In the product,
kubelet writes the sandbox pod's `cpu.max` for `limits cpu "1"`
(`share/agent-sandbox-k3d.yml`) and can re-assert it, which would make it a second writer.
A shipped controller also needs an owner story: drop that limit (a product change with QoS
and balloon-sizing effects), or hand the controller the pod's quota. Neither is done here.

Implementation: [`bench/admission/run.sh`](bench/admission/run.sh) (one round),
[`load.py`](bench/admission/load.py), [`ctl.py`](bench/admission/ctl.py) and
[`analyze.py`](bench/admission/analyze.py) (the rule above). Tests:
`bench/admission/test_ctl.py` (stomp repair by an absolute store, ENOENT without a write,
the 20-request window, the hysteresis band and its clamps, a partial trailing record, the
median as the one statistic, a quiet trace climbing, a settle inside the band, a read error
logged without a raise, and `load.py` records feeding the controller against
`tests/openai_stub.py`), `bench/admission/test_analyze.py` (SUPPORTED, each REFUTED
condition including the NB-spread margin, each INCONCLUSIVE gate, and the results-tree
reader, including the two-read check), and
`bench/admission/test_run.sh` (`run.sh` against shims for docker, curl and sudo and
`tests/openai_stub.py`: a 6 s cap ends a 60 s window on time, with `INCOMPLETE` written,
the lock released and no child left; the 20-request pre-roll and its median reference).
On the host, `run.sh` needs root
(RAPL, `perf`, the cpufreq snapshot and `ctl.py`'s write), so its end-to-end path is
unverified until the first round.

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


### H28 amendment A2: run under amendment 27 (2026-10-18)

Written 2026-10-18T17:20Z, before any H28 round (H28 has never run; nothing below
has been measured). The H28 rows, the pre-registration table, A1, every threshold,
the n, the arm order and the priors are unchanged. What changes is the load record:
H28 now runs under controlled background load scored by amendment 27
(`matched_a27`, merged in #129, `523c23c`).
- **Load.** `bench/load/load.py` on the load CPUs (the E-cores, 16-31). The k3d node
  container is pinned to the other CPUs (`docker update --cpuset-cpus 0-15`), and the
  eviction helper too, so the pods, the arm C copies and the eviction never share a
  core with the load. Each round records the pods' `Cpus_allowed_list` (from the
  prime pod) as `--treatment-cpus`.
- **Windows.** Per level: two 10 s `--phase baseline` windows before the first round.
  Before each scored round, after eviction and before the pods are created:
  `load.py settle` (5 s windows until load-CPU busy and throttle/min are within
  tolerance of the baseline, at most 60 s); its last window is the round's
  pre-window. From pod creation to the end of the round's measurement: 5 s
  `--phase during` windows. The untimed warm-up round r0 records no windows.
- **Matching.** `load.py matched` per level, under the unchanged `TOLERANCE`:
  covariates gate (pre busy, throttle/min, swap used, swap growth; during busy over
  the load CPUs minus the pods' CPUs, swap used, swap growth). Package W, throttle/min
  and 1-min load during the round are reported, never gating. Arm C's `cp` of
  N x 1 GiB may move swap; if it does, the level reads INCONCLUSIVE (load unmatched),
  not a verdict. A window with fewer load containers than the level runs makes the
  level VOID (never scored, re-run).
- **Verdict.** Scored only against the pre-registration table and A1, at `typical`
  (the ledger level), and only if the level's load is MATCHED; unmatched load gives
  INCONCLUSIVE with the numbers reported.
- **Lock and order.** `flock -w 21600` on the live lock, cluster
  `k3dgpu-r-zero-copy-models`, scratch KUBECONFIG. The locked part is capped at
  25 min (`HOLD=1440`: rounds killed at `HOLD - 120` s, then the cluster is deleted,
  the load stopped and the model images removed before the lock is released).
  `typical` first; `heavy` (its own six model images) only if a full `typical`-length
  level still fits. A round starts only if the slowest round so far (300 s before the
  first) still fits; a round not run is reported, as A1's time guard did.
- **RAPL.** `sudo -n` is not available on this host; package energy is read through
  a `docker exec` into the privileged k3d node (`K3D_GPU_RAPL_EXEC`). It is an
  outcome only.

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


### H28 run A2: amendment 27 load rule (i9-13900T, 2026-10-18)

Run under [amendment A2](#h28-amendment-a2-run-under-amendment-27-2026-10-18): one lock
hold, one fresh cluster, deleted with the load stopped and the model images removed before
release. `typical` is the ledger level; `heavy` ran too, because `typical` left time, but
lost its last T round to the time guard and is reported only. Disclosed: an earlier H28
attempt exists outside the ledger (branch `claude/verdict-zero-copy-models-ml`, commit
fc922a9, a different A2 text, run 2026-10-17T23:49Z under the legacy load rule, load
unmatched on throttle/min). It was never merged or recorded, so it is not scored and not
regraded here; this run did not reuse any of its files.

```text
# Reproduce
date:            2026-10-18T17:25:50Z (lock taken, load started) .. 17:42:07Z (cluster deleted,
                 load stopped, model images removed; lock released)
                 amendment A2 pushed 17:18:21Z (8c171d9), before any round
hypothesis:      H28 (row + pre-registration, A1, A2; load rule amendment 27)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P 0-15, E 16-31), 125 GiB RAM, about 151 GiB
                 in swap, 54 other containers running
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  8c171d9 (branch claude/verdict-h28-a27; main 7eb96a2 + A2), k3d v5.9.0
node image:      cryptoandcoffee/k3d-gpu:0.1.59 (sha256:da4cb4902375…), k3s v1.37.1+k3s1,
                 snapshotter overlayfs (read from the k3s containerd config)
cluster:         k3dgpu-r-zero-copy-models, scratch KUBECONFIG; node container
                 `docker update --cpuset-cpus 0-15`; every pod read Cpus_allowed_list 0-15
pods:            python:3.13-slim@sha256:70729b46…8c2f (pulled by the node), N = 8 per round,
                 MIB = 1024, one unique model image per round (h28-model:rK, /dev/urandom)
load:            bench/load/load.py on E-cores 16-31 (3 containers); per level 2 baseline windows
                 of 10 s; before each scored round, after eviction, `load.py settle` (5 s windows,
                 max 60 s), its last window the pre-window; 5 s during windows from pod creation to
                 the end of the measurement, busy over 16-31 (load CPUs minus the pods' 0-15)
energy source:   RAPL intel-rapl:0 energy_uj through `docker exec` into the privileged node
                 (K3D_GPU_RAPL_EXEC; sudo -n not available), reads only
command:         IMAGE=cryptoandcoffee/k3d-gpu:0.1.59 bench/zero-copy-models/probe.sh prep
                 WORK=<scratch> IMAGE=cryptoandcoffee/k3d-gpu:0.1.59 LOCK=<live.lock> HOLD=1440 \
                   bench/zero-copy-models/probe.sh run          # flock -w 21600 inside
runs per arm:    typical: warm-up r0 (C, unscored), then C T T C C T: 3 rounds x 8 pods per arm;
                 heavy: C T T C C (r12, T, not run: time guard), 3 C and 2 T rounds
raw results:     h28-20261018T172008Z.csv sha256 424710fecfcd…, typical/load.jsonl 983a99fecbf2…,
                 heavy/load.jsonl 6bcd7866f95e…
```

Every scored round: cold pages left after eviction 0 (no VOID-cold), all 8 pods Ready well
inside 240 s, swap-out negative (swap shrank), MemAvailable check passed. The memory
metrics below are the medians of the 8 pods in each round; they were identical to within
0.1 MiB across rounds of the same arm.

**`typical` (the ledger level).**

| Round | Arm | ready ms (median, min-max) | PSS MiB | Shared_Clean MiB | model page cache (x size) | dCached (x size) | memcg `file` per pod MiB |
|-------|-----|----------------------------|---------|------------------|---------------------------|------------------|--------------------------|
| r1 | C | 6450.5 (6435-6477) | 1028.5 | 5.4 | 9.000 | 9.005 | 1118-1214 (sum 9218) |
| r2 | T | 2980 (2980-2980) | 132.5 | 1029.4 | 1.000 | 0.970 | 106-161 (sum 1025) |
| r3 | T | 3238 (3238-3238) | 132.5 | 1029.4 | 1.000 | 0.874 | 111-148 (sum 1024) |
| r4 | C | 5905.5 (5901-5930) | 1028.5 | 5.3 | 9.000 | 9.119 | 1131-1173 (sum 9218) |
| r5 | C | 6333 (6322-6366) | 1028.6 | 5.3 | 9.000 | 8.919 | 1116-1253 (sum 9218) |
| r6 | T | 2885 (2885-2885) | 132.5 | 1029.3 | 1.000 | 0.989 | 110-152 (sum 1024) |

Against the registered predictions (row and A1), all met:
- T memory: PSS 132.5 MiB per pod (<= 160), Shared_Clean 1029 MiB (>= 0.9x size = 922),
  model page cache 1.00x size (A1: <= 1.1x), dCached 0.87-0.99x (<= 1.25x).
- C duplication (probe not blind): PSS 1028.5 MiB (>= 922), model page cache 9.00x size
  (A1: >= 0.8 x (N+1) = 7.2x, the source plus 8 copies), dCached 8.9-9.1x (>= 6.4x).
- Time: median of round medians T 2980 ms vs C 6333 ms, ratio **0.47** (<= 0.5). Bootstrap
  95% CI 0.45-0.55 (10 000 resamples of the rounds within each arm, seed 28), the same as
  the min-max of round medians. max(T) 3238 < min(C) 5905: complete separation, exact
  one-sided Mann-Whitney p = 1/C(6,3) = 0.05. Spread gate: T 11.8%, C 8.6% of the median
  (<= 30%).
- The node saves (N-1) x size = 7 GiB of page cache per 8 pods of a 1 GiB model.
- Within a T round all 8 pods printed READY in the same millisecond: they fault the same
  pages and wait on the same reads, so they finish together.

**Load, amendment 27 (`matched_a27`), `typical`.** Covariates (gating, `TOLERANCE`):

| Phase | Metric | C | T | spread / limit |
|-------|--------|---|---|----------------|
| pre (n = 3 per arm) | E-core busy % | 45.09 | 47.44 | 2.35 / 5.0 ok |
| pre | throttle/min | 739.5 | 683.7 | 55.8 / 184.9 ok |
| pre | swap used MiB | 154739 | 154735 | 4 / 256 ok |
| pre | swap growth MiB | -0.4 | -0.8 | 0.4 / 64 ok |
| during (C 6, T 3 windows) | E-core busy % (16-31) | 49.53 | 48.37 | 1.16 / 5.0 ok |
| during | swap used MiB | 154738 | 154735 | 3 / 256 ok |
| during | swap growth MiB | -0.6 | -0.4 | 0.2 / 64 ok |

Load matched: **yes**, no void (3 of 3 load containers in all 17 phased windows). Baseline:
busy 45.8%, throttle 880.8/min. Settle gaps: C 50, 5, 10 s; T 60 (not settled), 25, 5 s;
the unsettled r2 gap is reported and its pre-window covariates still matched. Outcomes
during the round (reported, never gating): package W 90.7 C vs 91.2 T; throttle/min
196.4 vs 307.1; 1-min load 12.6 vs 11.6. Arm C's 8 GiB of copies did not move swap.

**`heavy` (reported, not scored).** Load matched under amendment 27 (pre and during busy
99.8-99.9% in both arms, swap spread 46 / 256 MiB, no void; every settle gap hit 60 s
because baseline throttle was about 12/min and the pre-windows read 0). T rounds r8, r9
ready 4852 and 4169 ms vs C r7, r10, r11 16727, 13878, 13954 ms: ratio 0.32 (min-max
0.25-0.35), complete separation with 2 v 3 (p = 0.10). Memory as at `typical`: T PSS
132.5 MiB, page cache 1.00x; C 8.73-9.00x (r7 dCached 6.09x, below 6.4x, while its mincore
count read 8.73x: the `heavy` `--hdd` churn moves Cached). The ratio falls under load
because the copy competes for DRAM and the page cache, and the mmap-only arm does not
copy.

**Recorded, not scored (A1 charge).** A1 predicted that the first T pod to fault a page is
charged for it, so the per-pod memcg `file` charge would be lopsided (one pod about 1x
size). That did not hold: with 8 pods faulting at once, the charge was spread 106-161 MiB
per pod, summing to 1x size (1024 MiB), and `memory.current` stayed 113-169 MiB per pod.
A pod that starts alone still takes the full 1x charge; the per-pod limit headroom stays
needed for the first pod.

**Derived confidence** (pre-registered rule: fraction of non-void rounds meeting the
prediction x (1 - p)): the 3 C rounds show duplication; the 3 T rounds meet every memory
prediction; on time, r2 (0.47) and r6 (0.46) are <= 0.5 x the C median and r3 (0.51) is
not. 5/6 x 0.95 = **0.79**.

**Verdict: SUPPORTED at `typical`** (confidence 0.79). On this host and stack, 8 pods
that mmap one model through an image volume share one page-cache copy (PSS 132.5 MiB each,
1.00x size on the node) where per-pod copies cost 9x, and they become ready in 0.47x the
time of the copy arm. The time part clears the 0.5x threshold by a small margin: its CI
upper bound (0.55) is above 0.5, and one of three T rounds alone did not clear it.

Limits: one host (Raptor Lake, NVMe, 125 GiB RAM, about 151 GiB already in swap), one
snapshotter (overlayfs), N = 8, a 1 GiB random blob read in 4 KiB steps (not a real model
load), CPU-resident weights only, `typical` load. The comparison is against a per-pod copy
from the same local image, a lower bound on a per-pod download; a hostPath or RWX-PVC
mount was not run and would share one inode the same way (A1), so the claim is "matches a
shared-file mount, and adds OCI delivery and pinning". Prior art: KEP-4639, KServe
`oci+native://`, Tetris (ATC'22) [provenance:P280]; only the measured delta is claimed.

```bash
# Reproduce from 8c171d9. Host: docker, k3d, kubectl; RAPL via sudo -n or the node.
lscpu | grep 'Model name'; uname -r; git rev-parse HEAD
IMAGE=cryptoandcoffee/k3d-gpu:0.1.59 bench/zero-copy-models/probe.sh prep   # outside the lock
WORK=$(mktemp -d) IMAGE=cryptoandcoffee/k3d-gpu:0.1.59 LOCK=<live.lock> HOLD=1440 \
  bench/zero-copy-models/probe.sh run     # CSV in $WORK/h28-*.csv, load in $WORK/<level>/
bench/load/load.py matched "$WORK"/typical/load.jsonl   # MATCHED / INCONCLUSIVE / VOID
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

### H20 Amendment A2 (matched background load, 2026-10-18)

Written 2026-10-18T10:07Z on `claude/verdict-phase-steer-ml`, after the A1 run and
before any A2 measurement. The hypothesis, the arms (A1 slices and flags), the
predictions, the guardrails, the outcome table and the priors are unchanged. Only the
load control and the measurement change.

**Why.** The A1 run measured nothing: a 60 s window could not hold a 58-75 s turn, and
the load on the host drifted between cells (load1 11.6-18.3, throttle Δ 578-6148). Real
Intel boxes are busy, so A2 does not wait for a quiet host. It holds one controlled
background load for every arm, records it per arm, and voids the run only when that
load is unmatched across arms.

**Load.** `bench/load/load.py` (#95), stress-ng 0.22.01 pinned by digest, on the
E-cores 16-31 (`load.py cpus`) for the whole level. `typical` is the headline;
`heavy` is the robustness check and runs only if lock time remains. The load sits on
the same CPUs as PS decode and NEG prefill: that is the busy box the claim must survive,
and every arm carries it.

**Load per arm.** Before every cell, with no server of the probe running, a 15 s
`load.py` window (`window()`, imported) tagged with the arm about to run: E-core
busy %, load1, package W, `core_throttle_count` events per minute, swap used, swap
growth. That window's package W is the cell's idle W for the baseline subtraction,
and its E busy is the cell's pre-cell E busy for the placement proof.

**Matching tolerance** (`load.py` TOLERANCE, fixed before this run): the spread of the
SP, PS and ALL per-arm means must be within busy % 5 points; load1 max(1.0, 15%);
package W max(3 W, 10%); throttle/min max(10, 25%); swap used 256 MiB; swap growth
64 MiB per window. Unmatched -> INCONCLUSIVE naming the metric. How high the load is
never voids a run. NEG (n = 1) is a sensitivity gate and is not in the match.

**Measurement: whole turns.**
- **Prefix restore.** Once per run, each agent's 1024-token nonce prefix is prefilled
  and saved with llama-server `--slot-save-path` (`POST /slots/0?action=save`).
  Each cell restores it into its fresh server (`action=restore`, read from the image
  `--help` and spiked on this image on 2026-10-18: 1024 tokens restored in 0.04 s;
  the next turn processed only its new tokens). This replaces the A1 warm-up, which
  cost 90-150 s per cell.
- **Synchronized rounds.** A cell is K = 1 scored round, with no warm round (the
  model is mlocked at start and the prefix is restored). In a round all 4 agents send one turn (prefix + 396 new tokens, 96 out,
  `ignore_eos`) at the same moment. RAPL is read when the round starts and when the
  last turn ends, so every scored turn is whole. Cell J/token (net) = (E - idle W x
  round s) / tokens. Aggregate tok/s = tokens / round s. Fast agents idle until the
  round ends; that idle power is in idle W and is subtracted.
- **Cache-hit check.** Each scored turn records `timings.prompt_n`. A turn that
  processed more than 396 + 8 prompt tokens missed the restored prefix and marks the
  cell CONTAMINATED.
- **Wall check.** A cell starts only if the time left covers the longest cell so far
  (300 s before the first). The probe budget (19.5 min per level) includes the prefix
  prep.

**Order and n.** The A1 Latin square with NEG moved to fourth, so a short level still
has its sensitivity gate: SP PS ALL NEG PS ALL SP ALL SP PS. n = 3 cells per primary
arm (each arm once in each position), NEG n = 1, 4 scored turns per cell, so p95
pools 12 turns per arm. K = 1 because a cached turn on this host takes about 55 s
(2-thread prefill measured at 16.6 tok/s on 2026-10-18), and 10 cells must fit 19.5 min.

**Gates carried from A1, adapted.**
- Fewer than 3 cells in a primary arm -> INCONCLUSIVE.
- NEG not the worst median TTFT -> INCONCLUSIVE.
- SP J/token spread > 20% of its median -> INCONCLUSIVE.
- Placement proof per cell: CONTAMINATED as in A1 (overlap, thread outside its slice,
  pinned ALL thread, SP E busy > pre + 0.05, PS E busy <= pre + 0.05), plus a cache
  miss. When the pre-cell E busy is above 0.90 (`heavy` saturates the E-cores), the
  PS busy test cannot pass and is replaced by the affinity test alone. One
  contaminated cell in one arm only, or more than one in any arm -> INCONCLUSIVE.
- The A1 "throttle Δ > 0 or pswpin > 0 in one arm only" flags become the load match
  above; in-cell throttle Δ and pswpin Δ are recorded but no longer gate, because a
  cell's own work moves them and the background is what must match.

**Statistics.** Per arm: median and min-max of the 3 cell J/token values; pooled p95
turn latency with the per-cell p95 spread; median TTFT and TPOT. The PS/SP and ALL/PS
ratios of median J/token. Derived confidence for the J/token leg = 1 - the exact
one-sided Mann-Whitney p of PS vs SP cell values (3 vs 3; all PS below all SP gives
p = 0.05, confidence 0.95).

**Predictions** (unchanged from the pre-registration): SUPPORTED needs PS J/token
<= 0.85x SP, PS p95 <= SP p95 + SP spread, the guardrails, and PS below ALL on
J/token. Prior P(SUPPORTED) = 0.15; the outcome table above applies as written.

### H20 run 2 (Amendment A2, matched background load, i9-13900T, 2026-10-18)

```text
# provenance
date:            2026-10-18T10:14:52Z lock taken; typical 10:15:22Z-10:31:02Z; heavy 10:31:34Z-10:50:54Z;
                 cleanup and lock release 10:50:56Z (lock held 36 min, each level under 20 min)
hypothesis:      H20 (Amendment A2 design, pre-registered at 35b2aff before any A2 cell)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (cpu_core 0-15, cpu_atom 16-31; hfi yes, itd no)
host kernel:     6.6.128-1-MANJARO
accelerators:    none (CPU only)
k3d-gpu commit:  2a1ba080b894c950b76e210185be231096431cf5 (probe A2); `phase-steer --json` state auto,
                 prefill 0-15, decode 16-31
runtime:         ghcr.io/ggml-org/llama.cpp:server@sha256:f41d47eee678c02656eefc9ed8cce76cab584aa7032805eeef0e6639fcfda485
                 (b11707), plain containers sci-h20-*, --cpuset-cpus 0-31; no k3d cluster (none created)
model:           Qwen/Qwen3-4B-GGUF Qwen3-4B-Q4_K_M.gguf at bc640142c66e1fdd12af0bd68f40445458f3869b
                 (file sha256 7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5)
background load: bench/load/load.py on CPUs 16-31, typical then heavy, 30 s settle after each start;
                 52 other containers on the host; load1 8.98 when the lock was taken
energy source:   /sys/class/powercap/intel-rapl:0 (package-0), read as root
cells:           10 of 10 per level (SP PS ALL NEG PS ALL SP ALL SP PS); prefix prep 49.3 s (typical),
                 reused at heavy; every scored turn processed exactly 396 prompt tokens (prefix restored)
sysfs writes:    none
cleanup:         0 sci-h20-* containers, 0 load containers, no k3dgpu-* cluster
```

**Background load per arm** (mean of the 15 s pre-cell windows, servers stopped;
`load.py` TOLERANCE limits in brackets):

| Level | Arm | E busy % [5 pts] | load1 | package W | throttle/min | swap growth MiB | Matched? |
|---|---|---|---|---|---|---|---|
| typical | SP  | 44.1 | 13.9 | 94.8 | 2085 | -0.5 | |
| typical | PS  | 44.2 | 14.9 | 95.5 | 2568 | -1.4 | |
| typical | ALL | 45.2 | 15.0 | 94.3 | 1576 | -0.4 | **no**: throttle/min spread 992 > 642 (every other metric matched) |
| heavy | SP  | 99.7 | 30.1 | 100.1 | 147 | 0.0 | |
| heavy | PS  | 99.8 | 32.2 | 100.3 | 1053 | -0.1 | |
| heavy | ALL | 99.8 | 31.9 | 100.7 | 101 | -1.3 | **no**: throttle/min spread 952 > 263 (every other metric matched) |

**Results** (n = 3 cells per primary arm, 4 whole turns per cell, 12 turns per arm; NEG n = 1;
J/token net = baseline-subtracted, the pre-registered metric; median [min-max]):

| Level | Arm | J/token net | J/token raw | tok/s (aggregate) | p95 turn s (per-cell p95 range) | median TTFT s | median TPOT s |
|---|---|---|---|---|---|---|---|
| typical | SP  | 1.33 [1.24-1.49] | 14.29 [14.23-14.35] | 7.35 [7.26-7.37] | 52.3 (52.1-52.9) | 18.0 | 0.352 |
| typical | PS  | 1.16 [0.97-1.27] | 21.31 [21.30-21.92] | 4.73 [4.60-4.73] | 83.3 (81.2-83.4) | 18.1 | 0.662 |
| typical | ALL | 1.49 [1.30-2.12] | 18.10 [17.74-18.27] | 5.78 [5.68-5.79] | 66.5 (66.3-67.6) | 28.7 | 0.356 |
| typical | NEG | 0.88 | 17.86 | 5.67 | 67.7 | 32.1 | 0.343 |
| heavy | SP  | 1.14 [0.27-1.57] | 21.56 [21.37-23.36] | 4.87 [4.50-4.88] | 84.7 (78.7-85.3) | 22.6 | 0.596 |
| heavy | PS  | 0.37 [0.02-0.48] | 26.70 [26.55-28.47] | 3.77 [3.56-3.84] | 101.8 (99.9-107.8) | 21.6 | 0.794 |
| heavy | ALL | 0.91 [0.68-0.91] | 23.21 [22.54-24.31] | 4.49 [4.33-4.61] | 88.3 (83.2-88.6) | 26.4 | 0.592 |
| heavy | NEG | 0.38 | 23.83 | 4.29 | 89.5 | 39.3 | 0.515 |

Gates, in the analyzer's order (`probe.py --analyze h20-a2.jsonl --level L`):

| Gate | typical | heavy |
|---|---|---|
| >= 3 cells per primary arm | pass (3/3/3) | pass (3/3/3) |
| load matched (SP/PS/ALL) | **fail**: throttle/min | **fail**: throttle/min |
| NEG worst median TTFT | pass (32.1 s vs ALL 28.7 s) | pass (39.3 s vs ALL 26.4 s) |
| SP J/token spread <= 20% of median | pass (19.3%) | **fail** (114%) |
| placement proof | **fail**: SP E busy 0.505-0.511 vs pre 0.435-0.452 (+0.06-0.07 > 0.05) in 3/3 SP cells; PS, ALL, NEG clean | pass (PS by affinity: pre E busy > 0.90) |

Verdict: H20 INCONCLUSIVE at both levels. The background load was not matched across arms:
core throttle events per minute in the pre-cell windows differed by 992/min at `typical`
(limit 642) and 952/min at `heavy` (limit 263). Every other load metric matched (E busy
within 1.1 points, package W within 1.2 W). At `typical` the SP placement proof also
failed in 3/3 cells. At `heavy` the SP J/token spread gate failed. High load voided
nothing; the unmatched throttle rate did.

Descriptive only (gated out, not a verdict). On every pre-registered leg that the
gates did not block, the direction is REFUTED:
- **Energy:** PS/SP net J/token is 0.868 at `typical`, above the 0.85 bar. Exact
  one-sided Mann-Whitney p = 0.10 that PS is lower at all (3 vs 3), so confidence 0.90.
  At `heavy` it is 0.33, but the net metric breaks down there: the package sits at its
  ~100 W limit with or without the servers, so serving work displaces stress-ng work
  instead of adding watts, and SP net J/token spans 0.27-1.57. Raw package J/token
  favours SP in every cell at both levels: 14.3 vs 21.3 at `typical` (PS 1.49x), 21.6
  vs 26.7 at `heavy` (1.24x).
- **Latency guardrails:** at `typical`, PS median TPOT is 1.88x SP (limit 1.5x).
  Aggregate tok/s is 0.64x SP (limit 0.9x). p95 turn latency is 83.3 s vs SP
  52.3 s + 0.8 s. Every PS cell is slower than every SP cell on tok/s (MW p = 0.05,
  confidence 0.95). The per-cell ranges do not overlap, and they barely move cell to
  cell (PS 81.2-83.4 s, SP 52.1-52.9 s), while the throttle rate in the pre-cell windows
  swings by more than 1000/min within each arm. Decode on E-cores that share their time
  with a typical neighbour is slow.
- **ALL vs PS:** ALL is 1.29x PS on net J/token at `typical`. But ALL has the
  higher tok/s (5.78 vs 4.73) and the lower raw J/token (18.1 vs 21.3).

Confidence that this run cannot decide H20 under its own rules: 0.95. The throttle gap
is a fact of the windows, not chance. Confidence that a matched re-run would return
SUPPORTED: low (about 0.05). The guardrail breach is 1.88x against a 1.5x limit, and
it is consistent across cells.

Missing probe (for any re-run, pre-registered first): match the throttle rate on
windows that do not carry the previous cell's heat. Two ways: settle until the throttle
rate is stable before the window, or use one 60 s window per Latin-square row. The
pre-cell throttle/min follows the arm that just ran (PS windows follow SP and NEG
cells). Count unpinned llama-server helper threads in the SP placement budget, or
raise the margin before the run. Do not use baseline-subtracted J/token when the
package sits at its power limit; pre-register raw package J/token instead.

```bash
# Reproduce (holds the live lock 40 min at most; probe needs root for RAPL).
export IMAGE=ghcr.io/ggml-org/llama.cpp:server@sha256:f41d47eee678c02656eefc9ed8cce76cab584aa7032805eeef0e6639fcfda485
for level in typical heavy; do
  bench/load/load.py stop; bench/load/load.py start --level "$level" --ttl 1500; sleep 30
  flock -w 14400 "$LOCK" sudo env PATH="$PATH" IMAGE="$IMAGE" timeout -k 10 1200 \
      python3 bench/phase-steer/probe.py --model Qwen3-4B-Q4_K_M.gguf --slots h20-slots \
      --level "$level" --out h20-a2.jsonl
  python3 bench/phase-steer/probe.py --analyze h20-a2.jsonl --level "$level"
done
bench/load/load.py stop
```

### H20 amendment A3: re-run under amendment 27 (2026-10-18)

Written 2026-10-18T19:19Z on `claude/verdict-h20-a27`, before any A3 cell (nothing below
has been measured). The hypothesis, the arms and slices (A1), the A2 measurement (prefix
restore, synchronized whole-turn rounds, cache-hit check, wall check), the order
SP PS ALL NEG PS ALL SP ALL SP PS, n = 3 cells per primary arm, every A2 gate (NEG worst
TTFT, SP J/token spread 20%, placement proof with its +0.05 margin and the 0.90
saturation rule), the predictions, the guardrails, the outcome table and the priors are
unchanged. What changes is the load record: the A2 run failed matching on throttle
events/min that followed the previous cell's heat, and amendment 27 (`matched_a27`,
merged in #129, `523c23c`) adds the settle gap that run lacked.
- **Load.** As A2: `bench/load/load.py start --level L --ttl 1500` on the E-cores 16-31
  for the whole level, 20 s before the probe starts. `typical` first, the ledger level;
  `heavy` only if the 25 min hold has time left (it is not expected to).
- **Windows.** Per level, two 10 s `phase: baseline` windows before the prefix prep,
  with no server of the probe running. Before each cell, with the previous cell's
  servers removed, `load.settle` (5 s windows until E busy and throttle/min are within
  `max(abs, rel x baseline)` of the baseline, at most 60 s) replaces A2's 15 s window;
  its last window is the cell's pre-window. That window's package W is the cell's idle W
  for the net J/token and its E busy is the pre-cell E busy for the placement proof, as
  in A2. While the scored round runs, 5 s `phase: during` windows (only windows that end
  inside the round are kept).
- **Treatment CPUs.** Every arm's servers run in container cpuset 0-31, and PS decode,
  NEG prefill and ALL's kernel placement use the E-cores, so every E-core is treatment
  CPU in every arm (`exclude` = 16-31). During busy_pct therefore reads unmeasured in
  every arm and does not gate; the during covariates are swap used and swap growth.
  E-core busy during the round is still recorded per cell (placement proof).
- **Matching.** `probe.py --analyze` passes the primary arms' pre and during windows to
  `load.py matched` (`matched_a27`), unchanged `TOLERANCE`: pre busy, pre throttle/min,
  swap used and swap growth gate; package W, throttle/min and 1-min load during the
  round are outcomes, reported only. A window with fewer than the level's 3 load
  containers running makes the level VOID (never scored, re-run). A settle gap that hits
  60 s is reported. The analysis also reports the verdict the outcome table gives with
  the load gate left out (`verdict_if_matched`). Unit tests `Amendment27` in
  `bench/phase-steer/test_probe.py`; the A2 file still reads INCONCLUSIVE (throttle
  991.98 > 641.99) under the legacy rule.
- **Risks stated before measuring.** (1) Settle compares each pre-window with the
  baseline, not with the other arms, so two settled arms can still differ by up to
  2 x 25% of the baseline throttle rate; P(load matched at `typical`) 0.5. (2) The SP
  placement proof failed 3/3 at `typical` in A2 (E busy +0.06-0.07 over pre); the gate
  and its margin are unchanged, so P(that gate passes) 0.3.
- **Lock and budget.** `flock -w 21600` on the live lock; the probe runs as root (RAPL,
  `/proc/<pid>/task`) under `timeout -k 10 1350`, its own cap raised from 1170 s to
  1320 s because the settle gap can take up to 60 s per cell. Plain containers
  `sci-h20-*`, no k3d cluster (none is created; the scratch KUBECONFIG stays unused).
  The load and every `sci-h20-*` container are removed before the lock is released. The
  hold is capped at 25 min. Cells the budget cuts reduce n; a primary arm under 3 cells
  is INCONCLUSIVE, as in A2.
- **Prediction unchanged:** SUPPORTED needs PS net J/token <= 0.85x SP, PS p95 <= SP p95
  + SP spread, the guardrails, and PS below ALL on J/token. Prior P(SUPPORTED) 0.15 as
  registered (the A2 descriptive result put it near 0.05). The A1 and A2 runs keep their
  INCONCLUSIVE verdicts under the rules that scored them; they are not regraded.

### H20 run 3 (amendment A3, amendment 27 load rule, i9-13900T, 2026-10-18)

```text
# provenance
date:            2026-10-18T19:28:17Z lock taken; typical 19:28:38Z-19:45:26Z; cleanup and lock release
                 19:45:27Z (lock held 17.2 min); heavy not run (hold budget, as A3 expected)
hypothesis:      H20 (pre-registration + amendments A1, A2, A3; load rule amendment 27)
pre-registered:  A3 committed 4101f97 at 19:19Z, pushed (PR #138) before the lock was queued at 19:20:05Z
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (cpu_core 0-15, cpu_atom 16-31)
host kernel:     6.6.128-1-MANJARO
accelerators:    none (CPU only)
k3d-gpu commit:  4101f97 (probe A3); `phase-steer --json` state auto, prefill 0-15, decode 16-31
runtime:         ghcr.io/ggml-org/llama.cpp:server@sha256:f41d47eee678c02656eefc9ed8cce76cab584aa7032805eeef0e6639fcfda485
                 (b11707), plain containers sci-h20-*, --cpuset-cpus 0-31; no k3d cluster (none created)
model:           Qwen/Qwen3-4B-GGUF Qwen3-4B-Q4_K_M.gguf at bc640142c66e1fdd12af0bd68f40445458f3869b
                 (file sha256 7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5)
background load: bench/load/load.py typical on CPUs 16-31 (3 containers), 20 s before the probe;
                 52 other containers on the host; load1 22.97 when the lock was taken
energy source:   /sys/class/powercap/intel-rapl:0 (package-0), read as root
cells:           10 of 10 (SP PS ALL NEG PS ALL SP ALL SP PS), probe wall 1007.8 s; prefix slots reused
                 (saved by run 2 on the same image and model); every scored turn processed exactly 396
                 prompt tokens; 0 failed requests
windows:         2 baseline (busy 44.5%, throttle 840/min); 10 settle pre-windows, all settled (5-30 s);
                 130 during windows; 3 of 3 load containers running in every window (no void)
sysfs writes:    none
cleanup:         0 sci-h20-* containers, 0 load containers, 0 k3dgpu-r-* clusters
```

**Load, amendment 27 (`matched_a27`), `typical`.** Covariates (gating, `TOLERANCE`;
per-arm means):

| Phase | Covariate | SP | PS | ALL | Spread / limit | Status |
|---|---|---|---|---|---|---|
| pre | busy % (E-cores) | 45.02 | 44.29 | 44.73 | 0.73 / 5.00 | ok |
| pre | throttle/min | 760 | 848 | 848 | 88 / 212 | ok |
| pre | swap used MiB | 153818.1 | 152947.2 | 152947.4 | 870.9 / 256.0 | **fail** |
| pre | swap growth MiB | -0.13 | -0.30 | -0.30 | 0.17 / 64.0 | ok |
| during | busy % | unmeasured | unmeasured | unmeasured | every E-core is treatment CPU (A3) | not gating |
| during | swap used MiB | 152942.8 | 152944.4 | 152943.9 | 1.6 / 256.0 | ok |
| during | swap growth MiB | -0.32 | -0.25 | -0.40 | 0.15 / 64.0 | ok |

The swap failure is one window: host swap use (about 150 GiB, other tenants) fell by
2624 MiB during cell 0 (SP), so SP's first pre-window reads 155601.8 MiB and every later
pre-window reads 152913-152977 MiB. Under the rule that window counts; it is not
dropped. The settle gap did what A2 lacked: pre throttle/min is now matched (spread 88,
limit 212; A2 992 > 642). Outcomes during the round (reported, never gating): package
W SP 105.7, PS 101.2, ALL 104.8; throttle/min SP 1747, PS 938, ALL 4742; 1-min load
SP 15.6, PS 17.1, ALL 15.7.

**Results** (n = 3 cells per primary arm, 4 whole turns per cell, 12 turns per arm; NEG
n = 1; J/token net = baseline-subtracted with the pre-window's package W, the
pre-registered metric; median [min-max]; ratio CIs are 95% bootstrap over cells,
10000 resamples):

| Arm | J/token net | J/token raw | tok/s (aggregate) | p95 turn s (per-cell p95 range) | median TTFT s | median TPOT s |
|---|---|---|---|---|---|---|
| SP  | 1.38 [1.22-1.67] | 14.59 [14.56-14.78] | 7.21 [7.15-7.25] | 53.3 (53.0-53.7) | 17.8 | 0.362 |
| PS  | 1.43 [0.87-1.47] | 22.00 [21.62-22.45] | 4.60 [4.50-4.67] | 84.5 (82.1-85.4) | 18.3 | 0.673 |
| ALL | 1.58 [1.45-1.73] | 18.68 [17.95-18.85] | 5.61 [5.55-5.84] | 68.5 (65.8-69.1) | 27.6 | 0.366 |
| NEG | 1.35 | 18.30 | 5.63 | 68.1 | 33.2 | 0.351 |

| Ratio | Point | 95% CI |
|---|---|---|
| PS/SP net J/token (bar <= 0.85) | 1.031 | 0.52-1.20 |
| PS/SP raw J/token | 1.508 | 1.46-1.54 |
| PS/SP tok/s (guardrail >= 0.9) | 0.638 | 0.62-0.65 |
| PS/SP median TPOT (guardrail <= 1.5; CI over the 12 turns per arm) | 1.86 | 1.83-1.89 |
| ALL/PS net J/token | 1.106 | 0.98-1.98 |
| ALL/PS raw J/token | 0.849 | 0.80-0.87 |

Gates, in the analyzer's order (`probe.py --analyze h20-a3.jsonl --level typical`):

| Gate | typical |
|---|---|
| >= 3 cells per primary arm | pass (3/3/3) |
| load matched (`matched_a27`) | **fail**: pre swap_used_mib spread 870.90 > 256.00 |
| NEG worst median TTFT | pass (33.2 s vs ALL 27.6 s) |
| SP J/token spread <= 20% of median | **fail** (0.45 = 32%) |
| placement proof | **fail**: SP E busy 0.517 / 0.513 vs pre 0.429 / 0.450 (+0.09, +0.06 > 0.05) in 2/3 SP cells (third +0.045); PS, ALL, NEG clean; affinity clean in every cell |

Verdict: H20 INCONCLUSIVE at `typical` (`heavy` not run). Load unmatched on one
covariate, pre-window swap used (870.9 > 256 MiB), from one SP window taken before a
2.6 GiB host swap-in; every other covariate matched, no window void, every settle gap
settled. With the load gate left out the outcome table still stops at INCONCLUSIVE
(`verdict_if_matched`: SP J/token spread 32% > 20%), and the SP placement proof fails in
2 of 3 cells, the risk A3 named (P 0.3 that it passes). The A1 and A2 runs keep their
INCONCLUSIVE verdicts; nothing is regraded.

Descriptive only (gated out, not a verdict). Every leg the gates did not block points
to REFUTED, as in run 2:
- **Energy:** PS/SP net J/token 1.03 (CI 0.52-1.20), above the 0.85 bar. Exact one-sided
  Mann-Whitney p = 0.50 that PS is lower (3 vs 3), so confidence 0.50 on that leg. Raw
  package J/token is 1.51x SP (CI 1.46-1.54); every PS cell uses more energy per token
  than every SP cell.
- **Latency guardrails:** PS median TPOT 1.86x SP (CI 1.83-1.89, limit 1.5x), tok/s 0.64x
  SP (CI 0.62-0.65, limit 0.9x), p95 turn 84.5 s vs SP 53.3 + 0.7 s. Every PS cell is
  slower than every SP cell on tok/s (MW p = 0.05). Run 2 read 1.88x, 0.64x and 83.3 s
  under the same arms: the latency penalty of E-core decode next to a typical neighbour
  reproduces to within 2%.
- **ALL vs PS:** ALL has lower raw J/token (0.85x PS) and higher tok/s (1.22x), with
  higher net J/token (1.11x, CI 0.98-1.98).

Confidence that this run cannot decide H20 under its own rules: 0.95 (two independent
gates fail besides the load match). Confidence that a matched run would return
SUPPORTED: about 0.03; the TPOT guardrail is breached by 1.86x against 1.5x, with CIs
that exclude the limit in both runs. Not DO-NOT-RE-ATTACK (the verdict is not REFUTED),
but low value: a re-run only buys a decision if the SP placement margin and the net
J/token metric are re-registered first (run 2's missing-probe note stands).

```bash
# Reproduce (holds the live lock 25 min at most; probe needs root for RAPL).
export IMAGE=ghcr.io/ggml-org/llama.cpp:server@sha256:f41d47eee678c02656eefc9ed8cce76cab584aa7032805eeef0e6639fcfda485
bench/load/load.py stop; bench/load/load.py start --level typical --ttl 1500; sleep 20
flock -w 21600 "$LOCK" sudo env PATH="$PATH" IMAGE="$IMAGE" timeout -k 10 1350 \
    python3 bench/phase-steer/probe.py --model Qwen3-4B-Q4_K_M.gguf --slots h20-slots \
    --level typical --out h20-a3.jsonl
bench/load/load.py stop
python3 bench/phase-steer/probe.py --analyze h20-a3.jsonl --level typical
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

### H29 amendment A2: matched background load (2026-10-18)

Written 2026-10-18T11:10Z on `claude/verdict-uncore-phase-ml`, after run 1 and before
any A2 measurement. Unchanged: the hypothesis, the arms (A, U, C, UC, L) and their
knob values, the request (one 192-token streamed greedy `ignore_eos` request per cell
after a 6 s settle), the Williams-square order with L third on even rounds, both parts
of the prediction and their thresholds (UC/A decode >= 0.97x, UC/A gross and
common-idle marginal J/token <= 0.90x and lower in every paired round; UC beats C by
>= 3% tok/s in every round, or by >= 5% J/token on both metrics), the outcome table,
the priors, the gates (fewer than 4 valid cells in a primary arm; stock decode spread
> 10% of median; L ring > target + 100 MHz; C cores > cap + 100 MHz; A cores < cap +
200 MHz; U ring < target - 100 MHz), the 95 C abort, and the sign-test confidence.
Only the load control and the cell exclusions change.

**Why.** Run 1 was decided by noise from load nobody controlled: CPUs outside the pod
61-97% busy and package idle 85-117 W, drifting between cells. Real Intel boxes are
busy, so A2 does not wait for a quiet host. Every arm runs under the same controlled
background load, the load is recorded per arm, and a level is INCONCLUSIVE for load
only when that load is unmatched across arms. How high it is never voids a run.

**Load.** `bench/load/load.py` (#95; stress-ng 0.22.01 pinned by digest) on the
E-cores 16-31 (`load.py cpus`). The OVMS pod is on the P-cores 0-15, so the load and
the decode never share a core; they share the package power budget, the ring and DRAM,
which is the busy box the claim must survive. `typical` is the headline. `heavy` is the
robustness check and does not override the typical verdict. The runner starts the
level after the cluster is up, waits 30 s, runs the probe, stops the load, then does the
same for `heavy` (one cluster, one lock hold, at most 20 min of wall time per level; the
typical budget includes `up` and `agent up`).

**Load per arm.** The per-cell idle window (5 s of RAPL in run 1) becomes a 10 s
`load.py` window (`window()`, imported by the probe), taken after the arm's knobs are
written and settled, with no request in flight, tagged with the arm: E-core busy %,
load1, package W, `core_throttle_count` events per minute, swap used, swap growth. Its
package W is the cell's idle W, so the common-idle marginal J/token subtracts the
stock arm's loaded idle of the same round, as pre-registered.

**Matching tolerance** (`load.py` TOLERANCE, fixed before this run): over the A, U, C
and UC per-arm means, the spread must be within busy 5 points; load1 max(1.0, 15%);
package W max(3 W, 10%); throttle/min max(10, 25%); swap used 256 MiB; swap growth
64 MiB per window. L (n = 3, a regime control) is not in the match. Unmatched ->
INCONCLUSIVE naming the metric; the analyzer prints the verdict the level would have
had next to it. The knobs act at idle too (a pinned ring and capped cores change idle
package W), so a package-W or throttle mismatch caused by the arms themselves still
counts as unmatched: the rule is applied as written.

**Exclusions.** In A2 cells, the in-cell package throttle increment and swap-in > 100 MiB
are recorded and no longer exclude a cell (as in H20 A2 and H27 A3): a held neighbour
moves those counters, and excluding on them would void a run because the load is high.
The background they reflect is what must match. Run 1 cells keep the original rule.

**n and statistics.** Rounds as many as the deadline allows, up to 8 (expected 4-5 per
level at about 35 s per cell). Per arm: median and min-max of decode tok/s, gross and
common-idle marginal J/token, ring and pod-CPU MHz. Per comparison: the per-round paired
ratios, their median, and a 95% percentile bootstrap CI of that median (10 000
resamples over rounds, seed 29). Derived confidence = 1 - one-sided sign-test p over
paired rounds, as pre-registered.

**Expectation, not a gate.** In run 1 the ring read 2900 MHz in every U cell against a
4700 MHz pin. If that recurs, the U-pin gate stops the level as INCONCLUSIVE with that
cause named; the descriptive ratios are still reported.

**Probe.** `SUDO_ASKPASS=<helper> LOCK=<live lock> bench/uncore-phase/run.sh OUT`
(LEVELS "typical heavy"), then `probe.py --analyze OUT/h29.jsonl --level typical` and
`--level heavy`.

**A3: temperature abort 95 C -> 99 C (2026-10-18T11:45Z, before the A2 re-run).** The
first A2 attempt (11:31-11:36Z, commit 43db2c4) stopped on the 95 C abort after 1 cell
at `typical` (A, package max 96 C) and 5 cells at `heavy` (A U L UC C; C max 96 C).
The package sits at 90-96 C under the held load before any request, so the abort, set
for a quiet host, ends every level within a round, because the load is high: the
thing the design law rules out. A3 moves the abort to 99 C, 1 C under this part's TjMax
(coretemp `temp1_crit` 100 C, where the CPU throttles itself), and changes nothing
else. Disclosed: those 6 cells were seen before A3. The abort applies the same way to
every arm and is not a verdict input. The 6 cells are kept in the attempt's file and are
not pooled into the re-run. They showed U ring 2900 MHz under the 4700 MHz pin, and
3350 MHz in U's idle window with the E-cores loaded. That already points at the U-pin
gate. The attempt's per-cell numbers: typical A 14.27 tok/s; heavy A 9.05, U 9.55,
L 9.66, UC 8.57, C 8.88 tok/s.

### H29 run 2: matched background load (i9-13900T, 2026-10-18)

Run under [amendment A2](#h29-amendment-a2-matched-background-load-2026-10-18) with
A3 (abort 99 C). One lock hold, one cluster, deleted with the load stopped before
release; `typical` then `heavy`, each under 20 min of wall time.

```text
# Reproduce
date:            2026-10-18T12:10:14Z (lock, runner start); typical 12:12:30Z-12:17:01Z (99 C abort);
                 heavy 12:17:01Z-12:35:26Z (deadline); cleanup and release about 12:35:30Z (25 min held)
                 A2 pushed 11:10Z (43db2c4), A3 pushed 11:45Z (618b012), both before any run-2 cell
hypothesis:      H29 (row + amendments A2, A3)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P 0-15, E 16-31), 52 other containers running,
                 about 158 GiB in swap, load1 19.5 at start
host kernel:     6.6.128-1-MANJARO
accelerators:    not used (agent on CPU, K3D_GPU_AGENT_DEVICE=CPU)
k3d-gpu commit:  618b012 (claude/verdict-uncore-phase-ml)
cluster:         k3dgpu-i-uncore-phase, scratch KUBECONFIG, node image cryptoandcoffee/k3d-gpu:0.1.57
runtime:         openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58,
                 OpenVINO/Qwen3-4B-int4-ov, --target_device=CPU, pod CPUs 0-15
knobs:           uncore legacy package_00_die_00, initial 800000-4700000 kHz; EPP performance, governor
                 powersave; PL1 117 W; DDR 3600 MT/s; TjMax 100 C (coretemp temp1_crit)
load:            bench/load/load.py on CPUs 16-31, typical then heavy, 30 s settle; one 10 s window
                 per cell after the arm's knobs settled, no request in flight, tagged with the arm
energy source:   /sys/class/powercap/intel-rapl:0 (package-0), read as root
cells:           typical 8 (rounds 0-1, stopped at 99 C); heavy 28 (rounds 0-6, deadline)
restore:         probe `finally` re-wrote every knob; afterwards all 34 snapshot values read back equal.
                 The runner's own restore line failed to log (writes.log created by the root probe;
                 fixed in this PR by creating it as the caller first)
raw results:     h29.jsonl sha256 7101831480ea289971998db291b6ce537a26878025a2e353dbb0bf61395b88ce
command:         SUDO_ASKPASS=<helper> LOCK=<live lock> bench/uncore-phase/run.sh OUT
                 python3 bench/uncore-phase/probe.py --analyze OUT/h29.jsonl --level typical|heavy
```

**Background load per arm** (means of the 10 s windows; `load.py` TOLERANCE limits in
brackets; failing metrics in bold):

| Level | Arm | E busy % [5 pts] | load1 | package W | throttle/min | swap growth MiB | Matched? |
|---|---|---|---|---|---|---|---|
| typical | A  | 44.4 | 10.7 | 93.2 | 1170 | -99.0 | |
| typical | U  | 44.8 | 12.0 | 93.0 | 1131 | -16.2 | |
| typical | C  | 45.0 | 13.7 | 98.4 | 2262 | -24.4 | |
| typical | UC | 44.5 | 11.9 | 100.0 | 2892 | -52.8 | **no**: load1 2.95 > 2.05, throttle/min 1761 > 723, swap growth 83 > 64 |
| heavy | A  | 99.8 | 28.7 | 98.2 | 44 | -14.5 | |
| heavy | U  | 99.9 | 29.3 | 97.4 | 70 | -11.3 | |
| heavy | C  | 99.9 | 30.7 | 107.1 | 1193 | -12.9 | |
| heavy | UC | 99.9 | 30.3 | 107.0 | 1756 | -13.9 | **no**: throttle/min 1712 > 439 (every other metric matched) |

The throttle gap follows the arms, not the neighbour: E busy is matched within 0.6
points at both levels, but C and UC idle 6-9 W higher than A and U, at the 117 W PL1,
with 16-40x the throttle rate at `heavy`. Capping the P-cores frees package budget that
the saturated E-core neighbour then uses. Under the rule as written that is unmatched.

**Results** (medians with [min-max]; ratios are medians of per-round paired ratios with
a 95% bootstrap CI over rounds; common-idle marginal = package J minus the stock arm's
loaded idle W of the same round x request time, per token):

| Level | Arm | n | decode tok/s | gross J/token | ring MHz in request | ring MHz idle | pod-CPU MHz |
|---|---|---|---|---|---|---|---|
| typical | A  | 2 | 14.39 [14.20-14.58] | 6.81 [6.73-6.89] | 3050 | 3975 | 3651 |
| typical | U  | 2 | 14.37 [14.31-14.43] | 6.72 [6.70-6.74] | 2950 | 3775 | 3596 |
| typical | C  | 2 | 13.99 [13.97-14.02] | 7.94 [7.91-7.98] | 3300 | 3300 | 2800 |
| typical | UC | 1 | 13.72 | 7.72 | 3400 | 4500 | 2800 |
| typical | L  | 1 | 13.70 | 7.11 | 2000 | 2000 | 3644 |
| heavy | A  | 6 | 9.00 [8.31-9.47] | 11.27 [10.70-12.24] | 2900 | 3250 | 3500 |
| heavy | U  | 6 | 9.07 [8.52-9.43] | 11.15 [10.78-11.77] | 2900 | 3250 | 3500 |
| heavy | C  | 7 | 8.32 [7.75-8.89] | 12.63 [11.89-13.60] | 3200 | 3300 | 2800 |
| heavy | UC | 6 | 8.52 [8.11-8.98] | 12.33 [11.70-12.90] | 3200 | 3600 | 2800 |
| heavy | L  | 3 | 8.88 [8.62-9.16] | 11.24 [10.99-11.64] | 2000 | 2000 | 3500 |

`heavy`, paired by round (n = 6):

| Ratio | decode tok/s | gross J/token | common-idle marginal J/token |
|---|---|---|---|
| UC/A | 0.956 [0.925-0.981] | 1.084 [1.049-1.129], lower in 0/6 rounds | 2.60 [2.03-3.90] |
| C/A  | 0.930 [0.901-0.984] | 1.117 [1.056-1.156] | 2.76 [2.06-4.34] |
| U/A  | 1.019 [0.961-1.039] | 0.984 [0.955-1.041] | 0.95 [0.50-1.11] |
| UC/C | 1.029 [0.954-1.075] | 0.974 [0.925-1.047] | 0.96 [0.82-1.09] |
| L/A (n = 3) | 0.995 [0.967-0.996] | 0.996 [0.991-1.027] | 0.58 [0.52-0.79] |

Gates, in the analyzer's order:

| Gate | typical | heavy |
|---|---|---|
| load matched (A/U/C/UC) | **fail**: load1, throttle/min, swap growth | **fail**: throttle/min |
| >= 4 valid cells per primary arm | **fail** (2/2/2/1): 99 C abort in round 1 | pass (6/6/7/6) |
| stock decode spread <= 10% of median | (not reached) 2.6% | **fail**: 12.9% |
| L ring <= 2100 MHz | 2000 | 2000 |
| C cores <= 2900 MHz | 2800 | 2800 |
| A cores >= 3000 MHz (cap binds) | 3651 | 3500 |
| U ring >= 4600 MHz | **fail**: 2950 | **fail**: 2900 |

**Verdict: H29 INCONCLUSIVE at both levels.** `typical` (headline): the background load
was unmatched across arms (throttle/min, load1, swap growth) and the 99 C abort stopped
the level after 8 cells. `heavy` (robustness): unmatched on throttle/min only, and two
more gates fail on the data (stock spread 12.9%, U pin not honoured). High load voided
nothing: E-core busy matched within 0.6 points at both levels.

Descriptive only (gated out, not a verdict). The direction is REFUTED wherever there is
data. At `heavy`, UC was slower than stock (0.956x) and cost more per token (gross
1.084x, higher in 6/6 rounds; sign-test confidence 0.984 that UC gross J/token is
higher). The cap alone (C) was worse still (0.930x, 1.117x). UC beat C by 2.9% tok/s,
below the 3% bar, and was faster in 4 of 6 rounds. The L control (ring pinned to 2.0
GHz) cost 0.5% decode at `heavy` and 6% in the single `typical` round. The ring is
barely on decode's critical path here, so pinning it high has little to buy.

**Confidence.** That this run cannot decide H29 under its own rules: 0.95 (several
independent gates fail at each level). That a matched run on this host would return
SUPPORTED: about 0.02. The U pin is not honoured whenever the E-cores carry load (ring
2900-3000 MHz in a request, 3250-3775 MHz idle, against a 4700 MHz pin, in run 1, the
A2 attempt and run 2: 0 of 16 U cells at the target). The gross J/token direction is
against the claim in every heavy round.

**Missing probe.** H29 as written cannot be tested on this host under a busy box: the
uncore pin never reaches 4.7 GHz while the E-cores are active. Do not re-run it here
unchanged. A re-run needs a part where `intel_uncore_frequency` holds the ring under
load (check `current_freq_khz` under decode with E-core load before booking the lock),
or a hypothesis restated around the ring clock the host can actually hold. A matched
throttle rate also needs knob-independent windows (the cap changes what the neighbour
draws at the package limit), so pre-register package-limit residency or raw package
J/token instead of the throttle match for knob experiments.

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

### H24 Amendment A1: matched background load (2026-10-18)

Registered 2026-10-18, before any A1 measurement (this commit lands before the
lock is taken). Design law: real Intel boxes are busy, so the result must hold
under realistic load; the run-1 confound was load that was not controlled and
drifted between visits, not high load as such. Model M0, competing model M1, the
frozen arm predictions, the outcome table, the priors, n, the visit order and the
guardrails above are all unchanged.

- **Background load.** Every arm runs under the same `bench/load/load.py` load,
  held by `bench/sku-model/run-a1.sh` from 30 s before the OVMS launch until the
  probe ends (triad and every visit included), on the load.py CPU set (the 16
  E-cores, 16-31). Levels: `typical` (headline: 16 `--cpu` workers at 30%, 2
  `--stream` at 0.5 CPU, 1 `--hdd` at 0.25 CPU, about 0.37 of E-core time), then
  `heavy` (robustness: 16 `--cpu` at 100%, 4 `--stream`, 2 `--hdd`, no quota) if
  the lock budget allows. Each level is scored on its own.
- **Interleaving.** Unchanged: A B C D D C B A per level.
- **Per-arm load record.** Each visit's 60 s idle wait is replaced by one 10 s
  load.py window with the arm idle, tagged with the arm: E-core busy %, load1,
  RAPL package W, core throttle events/min, swap used and swap growth. The
  arm-cpuset busy fraction over the same window is recorded.
- **Tolerance (pre-registered in load.py, not changed here).** Per metric, the
  spread of arm means must be within max(abs, rel x largest): busy 5 pp; load1
  1.0 or 15%; package 3 W or 10%; throttle 10/min or 25%; swap used 256 MiB;
  swap growth 64 MiB. Unmatched at a level -> that level is "INCONCLUSIVE: load
  unmatched (...)", and the gated-out outcome is reported as `verdict_if_matched`.
- **Gates.** Row 6 keeps A drift > 10% and any arm spread > 20%. Busy and
  throttle flags are recorded, not gates (the load match covers them); the swap
  flag (pswpin delta > 1000) stays a gate. A measured once (no drift sentinel) is
  now INCONCLUSIVE explicitly; the run-1 scorer read a missing drift as 0.
- **Expected effect, not a new prediction.** Arms C and D share the E-cores with
  the load and A and B do not, so C/D are expected slower than in a quiet run.
  M0 has no co-tenancy term; that is part of what this tests on a busy box.
- **Lock.** One `flock` hold, at most 20 min per level; no k3d cluster (if one
  were needed it would be `k3dgpu-i-sku-model` with a scratch KUBECONFIG). The
  trap removes the load containers and every `sci-h24-*` container. sudo is used
  only for the RAPL `energy_uj` read (load.py `read_energy_uj`).

```bash
flock -w 14400 "$LOCK" env IMAGE=<ovms digest> MODELS=<repo per arm> SUDO_ASKPASS=<helper> \
    bench/sku-model/run-a1.sh "$OUT"
python3 bench/sku-model/probe.py --score "$OUT/typical/raw.jsonl"
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

### H24 run 2 (Amendment A1, matched background load, i9-13900T, 2026-10-18)

**Reproduce:** commit `c914a85` (Amendment A1, pushed before the lock),
i9-13900T, kernel 6.6.128-1-MANJARO, OVMS
`openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58`
(the H6 digest), `OpenVINO/Qwen3-4B-int4-ov` `openvino_model.bin` sha256
`074214fa...df2f9` (equal to H6), one repository per arm. Load: `bench/load/load.py`
on cpus 16-31, stress-ng image pinned in load.py. One lock hold 06:49:25-07:21:57
PDT; `typical` 13:49:29-14:03:36Z (847 s, all 8 visits A B C D D C B A, 0 void),
`heavy` 14:03:36-14:21:57Z (1101 s, the probe's 17 min deadline stopped it after 5
visits A B C D D, 0 void). 52 production containers before and after; 0 load and 0
`sci-h24-*` containers left; no k3d cluster; no sysfs writes; sudo only for the
RAPL read. Host at start: loadavg 1.72, swap used 153.9 GB of 192 GB (production
tenants; constant within 0.2 GB over the run).

```bash
flock -w 14400 <scratchpad>/live.lock env SUDO_ASKPASS=<helper> IMAGE=<digest> MODELS=<repo-per-arm> \
    bench/sku-model/run-a1.sh <out>
python3 bench/sku-model/probe.py --score <out>/typical/raw.jsonl
python3 bench/sku-model/probe.py --score <out>/heavy/raw.jsonl
```

Raw files: `bench/sku-model/results/2026-10-18-a1/` (`facts.txt`, per level
`raw.jsonl` and scorer output `score.json`).

**Verdict: INCONCLUSIVE at both levels, load unmatched** (the pre-registered A1
gate). Gated out, the direction is REFUTED (row 3: M0 and M1 decode both fail).

Per-arm background load (mean of the arm's 10 s load windows, arm idle; E-core busy
%, 1-min load, package W, core throttle events/min, swap used GB):

| Level | Arm | busy | load1 | pkg W | throttle/min | swap | matched? |
|---|---|---|---|---|---|---|---|
| typical | A | 43.7 | 10.6 | 93.8 | 1084 | 153.77 | |
| typical | B | 44.2 | 15.3 | 94.8 | 1671 | 153.76 | |
| typical | C | 47.5 | 16.4 | 93.4 | 1018 | 153.76 | |
| typical | D | 45.8 | 18.8 | 93.5 | 1006 | 153.69 | no: load1 spread 8.19 > 2.82, throttle 665 > 418 (busy, W, swap ok) |
| heavy | A | 99.8 | 27.0 | 100.4 | 695 | 153.65 | |
| heavy | B | 99.9 | 30.1 | 100.3 | 1761 | 153.65 | |
| heavy | C | 99.8 | 29.4 | 100.9 | 240 | 153.65 | |
| heavy | D | 99.9 | 32.5 | 98.7 | 60 | 153.45 | no: load1 5.55 > 4.88, throttle 1701 > 440 (busy, W, swap ok) |

The controlled load itself matched: E-core busy within 3.8 pp (typical) and 0.1 pp
(heavy), package W within 1.4 W and 2.3 W. The two failing metrics are carried in
by the probe's own previous visit: load1 is a 1-minute average that still holds
the last arm's inference threads (it climbs after the 16-thread D visits), and the
throttle rate in the window after an A visit (B, and A's last visit) carries the
P-core heat of that visit. Both are pre-registered metrics, so the gate stands.

Typical (headline; M0 calibrated on this run's A: decode 13.90, prefill 183.6,
c4 48.35 tok/s; A drift 2.9%, A vs H6 -11.3%; cell MAPE 35.8%). CI = 95% bootstrap
over samples of A and the arm (5000 resamples); it covers sampling spread only,
not the unmatched load.

| Arm | metric | n | median tok/s | M0 x k | error (95% CI) | spread |
|---|---|---|---|---|---|---|
| B 4P | decode c=1 | 6 | 11.73 | 13.90 | -15.6% (-16.6, -13.0) | 3% |
| B 4P | prefill | 4 | 107.5 | 91.8 | +17.1% (+14.5, +19.6) | 0.5% |
| B 4P | c4 | 2 | 38.86 | 48.35 | -19.6% (-21.4, -17.9) | 3.9% |
| C 8E | decode c=1 | 6 | 7.36 | 13.90 | -47.0% (-48.1, -45.6) | 4.4% |
| C 8E | prefill | 4 | 46.7 | 34.4 | +35.7% (+32.7, +39.0) | 1.7% |
| C 8E | c4 | 2 | 19.80 | 24.87 | -20.4% (-21.8, -19.0) | 3.1% |
| D 16E | decode c=1 | 6 | 6.14 | 13.90 | -55.8% (-56.3, -54.6) | 2.2% |
| D 16E | prefill | 4 | 39.0 | 68.9 | -43.4% (-44.7, -42.1) | 2.1% |
| D 16E | c4 | 2 | 15.90 | 48.35 | -67.1% (-67.5, -66.7) | 2.0% |

M1 decode (triad under the same load, A 39.5, B 38.1, C 26.8, D 29.2 GB/s): B
-12.5%, C -21.9%, D -40.3%, so M1 also misses (bar 20%). Pre-registered baseline
(uncalibrated efficiency-1 roofline, 25.45 tok/s) absolute decode MAPE over B/C/D
67.0%; M0 absolute 46.4%. Gated out, the scorer's outcome is row 3; prefill and c4
also miss on C and D.

Heavy (robustness; partial: A once, so no drift sentinel, and that alone is the
pre-registered row-6 INCONCLUSIVE). Calibrated on A (decode 9.61, A vs H6 -38.6%):
decode B -21.8% (n 3), C -64.8% (n 3), D -73.3% (n 6); prefill B +55.2%, C +41.5%,
D -50.1%; c4 B -33.1%, C -44.9%, D -79.4% (n 1-2). M1 decode B -4.9%, C -34.6%,
D -62.0%. Same direction as typical, larger on the E-core arms that share their
cores with a saturated neighbour.

**n:** typical decode 6 / prefill 4 / c4 2 per arm (A 6 / 4 / 2), as registered;
heavy below it (A 3 / 2 / 1).

**Confidence.** That H24 is INCONCLUSIVE under the pre-registered A1 gate: 0.99
(the gate is mechanical; both levels fail two metrics by 1.1x-3.9x the tolerance).
On direction: 0.9 that a matched run refutes M0 decode (row 2 or 3). The decode
gaps on C and D (-47%, -56%) are 10x their bootstrap half-widths and 10x the
within-arm spread, they repeat at heavy, and run 1 (quiet host, uncontrolled
load) showed the same order on its decode proxy (C 0.57, D 0.71 of A). Part of
the C/D gap here is co-tenancy (the load sits on the E-cores, A and B do not share
cores with it), which M0 does not model; on a busy box that is the gap a user sees.
Prefill direction is mixed (B and C above M0, D below), so row 4 is not indicated.

**Not DO-NOT-RE-ATTACK** (not REFUTED under the gate). Missing probe: the same run
with load1 and throttle matched, e.g. a settle period after each visit until the
1-minute load returns to the level's baseline, or windows that exclude the
probe's own carry-over; heavy needs a deadline that fits 8 visits (about 25 min).

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
draft, T-SKU rated 35 W base power but run at PL1 = PL2 = 117 W, AVX-VNNI target (no AMX on this host), no NPU (`/dev/accel`
absent). It does not test a parallel draft/verify design (Dovetail, DuoDecoding).

**Shipped default while PENDING:** `K3D_GPU_AGENT_DRAFT` is `off` until a host
measurement shows a speedup (a cross-engine draft lost on Lunar Lake [provenance:P222]). `auto` puts the draft on the target's
own device: in the serial engine a cross-engine draft wins only if it is faster there,
and no host measurement says so yet.

### H22 result (i9-13900T, 2026-10-17)

**Reproduce:** commit `a8aeb01` (branch `claude/inv-hetero-spec`), i9-13900T
(UHD 770, T-SKU rated 35 W base power, firmware PL1 = PL2 = 117 W, 131 GB RAM, avx_vnni, no AMX, no `/dev/accel`), kernel
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
the noise. With 16 CPUs of target decode the package (35 W rated, run at PL1 = PL2 = 117 W) read 76-97 C and load1
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

### H34 pre-registration (i9-13900T)

Written 2026-10-17T22:04-07:00 at commit `a326f59`, before any H34 measurement.
Nothing below is a result.

**Why:** H22 runs 2 and 3 showed, exploratorily, that same-engine Fast Draft (arm B,
draft on the P-cores) beat the target alone (arm A) in 16/16 rounds: B/A 1.35 and 1.32
decode tok/s, 0.76 and 0.82 package J/token. Those runs were not designed for B vs A
and the box carried uncontrolled background load that drifted between arms. H34 tests
B vs A directly, under a controlled background load that is the same for both arms.

**Design law:** real Intel boxes are busy, so the result must hold under realistic
load. The H22 confound was uncontrolled load that drifted between arms, never high
load as such. So every arm runs under the same controlled background load, arms are
interleaved, the load is recorded per arm, and a level is INCONCLUSIVE only when the
load is unmatched across arms under the tolerance below, never because it is high.

**Arms** (one OVMS 2026.4.1 pod, `requests.cpu: 16`, label `k3d-gpu.io/cores:
performance`, so both arms share the P-core balloon; graphs as in H22 with
`cache_size: 2`, `enable_prefix_caching: false`):

| Arm | Graph | draft | draft_device |
|---|---|---|---|
| A | `a-4b-cpu` | - | - |
| B | `b-spec-cpu` | `Qwen3-0.6B-int4-ov` | CPU (default `num_assistant_tokens` 5, not swept) |

**Load levels** (`bench/load/load.py`, stress-ng 0.22.01 pinned by digest, on the
load CPUs = the 16 online E-cores 16-31, never the P-cores the arms run on):
`typical` = 16 `--cpu` workers at 30% + 2 `--stream` workers held to 0.5 CPU each +
1 `--hdd` churn worker held to 0.25 CPU (about 0.37 of E-core time, plus DRAM
traffic); `heavy` = 16 `--cpu` workers at 100% + 4 `--stream` + 2 `--hdd`, no quota.
`typical` is the headline; `heavy` is robustness and runs only if lock time remains.
The 52 production containers keep running throughout; they are part of the
background the matching check sees.

**Schedule:** cluster `k3dgpu-i-spec-decode-cpu` (scratch KUBECONFIG), models
pre-staged in `~/h22-models` and copied into the node, server Ready <= 4 min (else
BLOCKED), one warm-up per arm. Per level: start the load, settle 60 s, one warm-up per
arm, then up to 12 pairs; pair i runs A then B when i is even and B then A when i is
odd (counterbalanced A B B A interleave), both arms on prompt `i mod 8` of
`bench/hetero-spec/prompts.jsonl` (sha256 logged). Greedy, `max_tokens` 256,
`ignore_eos`, streamed, concurrency 1; tokens = `usage.completion_tokens`, a request
whose count is not 256 is dropped and counted. Each level holds the lock at most
20 min (the first level's 20 min include setup) and stops scoring 2 min before;
cluster and load containers are deleted on exit.

**Measured per request:** decode tok/s = (tokens - 1) / (t_last - t_first); package
J/token = RAPL package energy over the request / 256 (read only, sudo). **Load per
arm:** a 5 s `load.py sample` window right before every scored request, tagged with the
arm about to run: E-core busy %, load1, RAPL package W, `core_throttle_count` events per
minute, swap used and swap growth.

**Load matching tolerance** (`load.py` TOLERANCE, fixed before this run): the spread of
the per-arm means must be within busy % 5 points; load1 max(1.0, 15%); package W
max(3 W, 10%); throttle/min max(10, 25%); swap used 256 MiB; swap growth 64 MiB per
window. Matched -> the level gets a verdict; unmatched -> INCONCLUSIVE naming the metric.

**n and statistics:** n >= 8 complete pairs per level (target 12). Per pair the ratios
B/A of tok/s and of J/token; medians; 95% bootstrap CI over pairs (10k, seed 34); exact
two-sided sign test per ratio. Derived confidence = 1 - max(sign-test p of the two legs).

**Outcome table** (per level; `bench/hetero-spec/h34.py` applies it mechanically):

| Outcome | Prior (typical) | Verdict |
|---|---|---|
| Server not Ready, < 8 complete pairs, or load unmatched | 0.15 | INCONCLUSIVE (cause named) |
| median tok/s ratio >= 1.20 and median J ratio <= 0.85, tok/s CI low > 1.0 and J CI high < 1.0 | 0.55 | SUPPORTED |
| tok/s CI high < 1.20, or J CI low > 0.85 | 0.20 | REFUTED |
| anything else | 0.10 | INCONCLUSIVE (effect not resolved) |

Guardrail reported, not the verdict: greedy output identity of B vs A per pair
(first 200 characters). No calibration band: H22 showed the target's own 16-CPU decode
throttles this package, so throttling is recorded and matched, not a void condition.

**Product action if SUPPORTED at typical:** a follow-up PR makes auto-adapt enable
the CPU draft by default where detection says it wins, with `K3D_GPU_AGENT_DRAFT=off`
as the override. If REFUTED: `K3D_GPU_AGENT_DRAFT` stays `off`, DO-NOT-RE-ATTACK.

Scope of any verdict: OVMS 2026.4.1 serial Fast Draft, gamma 5, Qwen3-4B int4 target
with Qwen3-0.6B int4 draft, i9-13900T (T-SKU rated 35 W base, run at PL1 = PL2 =
117 W, AVX-VNNI, no AMX), 16 P-CPUs, single stream.

### H34 result (i9-13900T, 2026-10-17)

**Reproduce:** commit `bb382c2` (branch `claude/verdict-spec-decode-cpu-ml`, the
pre-registration commit), i9-13900T (8 P-cores + 16 E-cores, UHD 770, T-SKU rated
35 W base, firmware PL1 = PL2 = 117 W, 131 GB RAM, AVX-VNNI, no AMX), kernel
6.6.128-1-MANJARO, k3d v5.9.0, node image `cryptoandcoffee/k3d-gpu:0.1.55`, OVMS
2026.4.1 (`openvino/model_server@sha256:a928903f...76fc58`), models from
`~/h22-models` (the H22 run-2 pull), prompts sha256 `a825757982ac08ec...`, load image
stress-ng 0.22.01 (`bench/load/load.py` pin). Lock taken 22:06:01 PDT, run
22:06-22:41 (typical 22:06-22:22 including setup, heavy 22:22-22:41), cluster and load
containers deleted on exit:

```bash
# askpass prints the sudo password; RAPL energy_uj is only read
SUDO_ASKPASS=<askpass> flock -w 14400 <scratchpad>/live.lock bench/hetero-spec/h34.sh <out>
python3 bench/hetero-spec/h34.py bench/hetero-spec/results/2026-10-17-h34.jsonl \
    bench/hetero-spec/results/2026-10-17-h34-load-typical.jsonl \
    bench/hetero-spec/results/2026-10-17-h34-load-heavy.jsonl
```

Raw records: `bench/hetero-spec/results/2026-10-17-h34.jsonl` (40 requests, all
`usage.completion_tokens` = 256, none dropped), load windows
`2026-10-17-h34-load-{typical,heavy}.jsonl`, verdict `2026-10-17-h34.verdict.json`.

**Verdict: SUPPORTED at `typical` (headline)**, applied mechanically by `h34.py`.
Load matched (MATCHED under the pre-registered tolerance), 12/12 pairs complete.

| `typical`, n = 12 pairs | A target alone | B CPU Fast Draft | B/A paired median | 95% bootstrap CI | B wins |
|---|---|---|---|---|---|
| decode tok/s (median, min-max) | 9.73 (7.29-10.79) | 12.66 (11.12-14.89) | 1.389 | 1.285-1.447 | 12/12 |
| package J/token (median) | 10.21 | 8.32 | 0.751 | 0.714-0.828 | 12/12 |

Both legs clear the pre-registered bar (tok/s ratio >= 1.20 with CI above 1.0; J/token
ratio <= 0.85 with CI below 1.0). Sign test p = 0.00049 on each leg; derived confidence
1 - max(p) = 0.9995. The prediction carried over from H22 (+35% tok/s, -24% J/token)
holds: +39% and -25%. Greedy output identical in 11/12 pairs (first 200 characters);
the one difference (chat2) is a numerics divergence under speculation, reported, not a gate.

**Per-arm background load, `typical`** (means of the 5 s windows before each request):

| Metric | A | B | spread | tolerance | ok |
|---|---|---|---|---|---|
| E-core busy % | 56.7 | 58.7 | 1.9 | 5.0 | yes |
| load1 | 21.6 | 22.3 | 0.7 | 3.4 | yes |
| package W | 94.3 | 94.3 | 0.0 | 9.4 | yes |
| throttle events/min | 543 | 426 | 117 | 136 | yes |
| swap used MiB | 144096 | 144094 | 2 | 256 | yes |
| swap growth MiB/window | 28.9 | 0.5 | 28.3 | 64 | yes |

The box was busy, not quiet: E-cores 47-77% busy (the 30% stress-ng duty plus the 52
production containers), load1 15-29, the package throttling throughout. That is the
condition the design law asks the result to hold under, and it held.

**`heavy` (robustness): INCONCLUSIVE, load unmatched** (`swap_delta_mib` spread 80.4 >
64.0: one A window swapped in 548 MiB). Not a verdict, recorded descriptively: 8 pairs
(budget reached at 8/12), E-cores 99.9% busy in both arms, load1 36-58, package
98-117 W; A 4.72 tok/s and 23.1 J/token, B 5.98 tok/s and 18.2 J/token; B faster and
cheaper in 8/8 pairs, tok/s ratio median 1.21 (CI 1.09-1.89), J/token ratio 0.85
(CI 0.53-0.97). Saturated E-cores halve the P-core decode rate; the draft still helps
in every pair, but this run does not back a number at `heavy`.

**Product effect:** a measured speedup now exists for the CPU target on this host
class, so the pre-registered action applies: a follow-up PR makes auto-adapt enable the
CPU draft by default where the target runs on the CPU, with `K3D_GPU_AGENT_DRAFT=off`
as the override. Scope: OVMS 2026.4.1 serial Fast Draft, gamma 5, Qwen3-4B int4 target
with Qwen3-0.6B int4 draft on the 16 P-CPUs, single stream, one host, one session.
Not measured: other targets/drafts, gamma sweeps, AMX hosts, concurrency > 1.

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

### H1 run 2 (i9-13900T, 2026-10-17)

Amendment, written 2026-10-17T20:27Z and pushed before any step of this run.
It fixes the three deviations of run 1 and adds nothing to the H1 prediction.

- **Host.** No fresh host exists for this probe; it runs on the same shared
  i9-13900T. "Fresh host" is replaced by a matched-load design: for the whole run,
  `bench/load/load.py start --level typical` holds the E-cores (16-31) at its
  fixed background load, and `bench/load/load.py sample` records busy %, load1 and
  swap in 60 s windows (package W is null: no root for RAPL). Other tenants of
  the host keep running and are recorded, not removed.
- **One fresh cluster.** Cluster `k3dgpu-h1-eat`, created by this run and deleted
  after it, scratch `KUBECONFIG`, an empty `EAT_WORK`. Every step runs once; no
  step is retried and no `helm uninstall` happens between steps.
- **Steps, each timed (wall s) and logged in order.** The reproduce block of run 1
  with `K3D_GPU_CLUSTER=k3dgpu-h1-eat`, from `main` 992df8d: (0) `docker build`
  of the node image, the EAT clone at the pinned commit and the bastion build,
  before the host lock (they touch no cluster; `eat-up.sh` then finds both and
  skips them); (1) `k3d-gpu up`; (2) `eat-up.sh`; (3) `eat-up.sh model
  cpu-qwen3-1-7b`; (4) `kubectl wait --for=condition=Ready pod --all -A
  --timeout=30m`, output recorded verbatim; (5) one chat completion through EAT's
  LiteLLM gateway (ingress-nginx, `Host: api.example.com`), request and response
  recorded; (6) `eat-check.sh` (the README's step 4, not graded); (7) `k3d-gpu
  down`. Host lock: at most 25 min from step 1 to step 7.
- **Grading, fixed now.** SUPPORTED only if steps 1-3 exit 0, step 4 exits 0
  (every pod Ready), step 5 returns HTTP 200 with a non-empty completion, and no
  Xeon-only instruction or node label is needed. Step 4 exiting non-zero is
  REFUTED when any pod owned by a Deployment, StatefulSet, DaemonSet or ReplicaSet
  is not Ready at the end of the wait. It is INCONCLUSIVE when the only pods not
  Ready are finished Job pods (phase Succeeded, or Failed with a later Succeeded
  pod of the same Job): such a pod can never become Ready, so the probe as
  written cannot pass on a stack that runs Jobs. Once every pod that is not Ready
  is a finished Job pod, the outcome of the wait is fixed and it is stopped there
  (to stay inside the lock), with the pod list recorded. Any step 1-3 failing,
  or no completion, is REFUTED; the lock running out before step 5 is
  INCONCLUSIVE.

```text
# provenance
date:            2026-10-17T20:29:19Z..20:48:37Z (host lock held 1158 s of 1500); amendment pushed 20:27:55Z (40d2401)
hypothesis:      H1
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores; AVX2 and VNNI, no AVX-512, no AMX)
host kernel:     6.6.128-1-MANJARO
accelerators:    "Accelerators schedulable: nvidia=2 intel=8 hybrid=1"
k3d-gpu commit:  main 992df8da20cfee7959500e32418d24b0ada97864
node image:      k3d-gpu:eat-992df8d, image ID sha256:8a8a093acd204807b72587208c942d0d441364686f70b27e7be442aaa7d5c29a (k3s v1.37.1+k3s1)
model:           Qwen/Qwen3-1.7B, bf16 (EAT catalog id cpu-qwen3-1-7b; revision not pinned by EAT)
runtime:         EAT release-1.0.3-hotfix 0d3438d7119de44cafc5903f66665c0b623e3333; bastion image ID
                 sha256:ca1bed9cd2e387e0afcfc3fd569b0b34443ab687e2eff06bb751a4b708c1b90e;
                 vllm/vllm-openai-cpu:v0.24.0@sha256:b0e6f8604413a8bfcca8836bb4b57b47f1aa653957e07f8903ece531309981a1, KV cache 6 GiB
background load: bench/load/load.py typical on CPUs 16-31 for the whole run; 19 windows of 60 s:
                 load-CPU busy 54.2-66.2 % (median 62.7), load1 10.95-17.6 (median 14.5), swap used 139.7-140.4 GiB,
                 core throttle 16-4873 events/min (median 114); package W not measured (no root for RAPL);
                 52 other containers running at the start, host load1 7.56
runs per arm:    1 (one cluster, k3dgpu-h1-eat, deleted at the end; scratch KUBECONFIG; empty EAT_WORK)
```

| Step | Command | Start (UTC) | rc | Wall (s) |
|---|---|---|---|---|
| 0 | node image build, EAT clone, bastion build (all cached; before the lock) | 20:28:25 | 0 | 2.2 |
| 1 | `k3d-gpu up` (README env, `K3D_GPU_CLUSTER=k3dgpu-h1-eat`) | 20:29:20 | 0 | 48.8 |
| 2 | `eat-up.sh` (5 PLAY RECAPs, all `failed=0`) | 20:30:08 | 0 | 516.7 |
| 3 | `eat-up.sh model cpu-qwen3-1-7b` | 20:38:45 | 0 | 157.7 |
| 4 | `kubectl wait --for=condition=Ready pod --all -A --timeout=30m` | 20:41:23 | stopped (124) | 383.0 |
| 5 | one chat completion through LiteLLM | 20:47:46 | 0 | 12.8 |
| 6 | `eat-check.sh` (not graded) | 20:47:59 | 0 | 28.3 |
| 7 | `k3d-gpu down` | 20:48:28 | 0 | 9.6 |

Step 4 output, verbatim:

```text
pod/agent-sandbox-controller-786cd8f99f-5q8z7 condition met
pod/python-pool-2nrkz condition met
pod/python-pool-c2zzn condition met
pod/python-pool-dl55s condition met
pod/python-pool-rfm9t condition met
pod/python-pool-wxxn6 condition met
pod/sandbox-router-deployment-d4bf6ff8d-nhflq condition met
pod/sandbox-router-deployment-d4bf6ff8d-zkgds condition met
EARLY STOP at 2026-10-17T20:47:46Z: every pod not Ready is a finished Job pod
```

`kubectl wait` takes the pods in order and waits on each in turn. It sat on
`default/register-qwen-qwen3-1-7b-4vth7`, the Succeeded pod of EAT's model
registration Job (completed at the end of step 3, later removed by the Job's
TTL). At the stop, 33 of 34 pods were Ready, including the vLLM pod
`vllm-qwen-3-1-7b-cpu-6ccd6d895f-f4rcs` (its image pull and model load took most
of the 383 s; the stop condition was polled every 15 s). The one pod not Ready
was `kube-system/helm-install-gateway-api-crd-zc6w4`, phase Succeeded: k3s's own
HelmChart Job for the Gateway API CRDs, which a k3s v1.37 cluster runs. A
Succeeded pod never turns Ready, so the wait would have reached its 30 min
timeout.

Step 5, the completion (trimmed):

```text
POST https://api.example.com/v1/chat/completions (resolve api.example.com:443 -> 172.24.0.2)
request:  {"model":"Qwen/Qwen3-1.7B","max_tokens":64,"temperature":0,
           "messages":[{"role":"user","content":"In one sentence, what is Kubernetes? /no_think"}]}
http_code=200 time_total=12.687422s
response: {"model":"Qwen/Qwen3-1.7B","system_fingerprint":"vllm-0.24.0-5616035f","choices":[{"finish_reason":"stop",
           "message":{"content":"<think>\n\n</think>\n\nKubernetes is an open-source platform for automating the
           deployment, scaling, and management of containerized applications."}}],
           "usage":{"completion_tokens":27,"prompt_tokens":20,"total_tokens":47}}
```

Step 6 printed `get_weather({"city": "Portland"})` for the tool call, the
sandbox ran the model's code (`{"stdout":"385\n","stderr":"","exit_code":0}`)
and it ended `==> EAT check passed.`

Verdict: **INCONCLUSIVE**, by the amendment's Job-pod rule. Every criterion but
one held as written: steps 1-3 exited 0 in one pass with no retry, every pod
owned by a ReplicaSet, StatefulSet, DaemonSet or Sandbox was Ready, the
completion returned HTTP 200 with content, and the only node labels EAT needed
were `ei-infra-eligible` and `ei-inference-eligible`, which are not Xeon labels.
The criterion that failed is step 4 exiting 0. It cannot exit 0 on k3s v1.37:
k3s's own Gateway API CRD Job leaves a Succeeded pod, and EAT's registration Job
leaves one until its TTL. The fault is in the probe, not in EAT. The run also
does not meet "fresh host": the amendment replaced it with a matched background
load on the shared host.

Re-attack: a new amendment, before the next run, that waits on the pods that can
be Ready (`kubectl wait --for=condition=Ready pod --all -A
--field-selector=status.phase!=Succeeded --timeout=30m`) and requires every Job
in the cluster to report `Complete`. This run suggests it would pass on this
host, but that is a prediction, not a result.

Confounds: the shared host (52 other containers, 140 GiB of swap in use), the
`typical` load on the E-cores, a throttling package (up to 4873 core throttle
events in one minute), n=1, and every image already cached on the host except
the vLLM image, which the new node pulled.

```bash
# Reproduce on one fresh cluster under the typical load. Host needs docker, k3d, kubectl, curl, jq.
lscpu | grep 'Model name'; uname -r; git rev-parse HEAD
export KUBECONFIG=$PWD/h1.kubeconfig K3D_GPU_CLUSTER=k3dgpu-h1-eat EAT_WORK=$PWD/eat-work
IMAGE=k3d-gpu:eat-$(git rev-parse --short HEAD)
docker build -q -t "$IMAGE" . && docker image inspect --format '{{.Id}}' "$IMAGE"
bench/load/load.py start --level typical --ttl 1800
bench/load/load.py sample --seconds 60 --windows 26 --tag h1 --level typical --out load.jsonl &
K3D_GPU_IMAGE="$IMAGE" \
K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true \
K3D_GPU_K3S_ARGS=--disable=servicelb scripts/k3d-gpu up
integrations/eat/eat-up.sh
integrations/eat/eat-up.sh model cpu-qwen3-1-7b
kubectl wait --for=condition=Ready pod --all -A --timeout=30m   # cannot exit 0: Succeeded Job pods
integrations/eat/eat-check.sh                                    # expect: ==> EAT check passed.
scripts/k3d-gpu down; bench/load/load.py stop
```

### H1b pre-registration (i9-13900T, 2026-10-17)

Written 2026-10-17T21:13Z and pushed (093ba98) before any H1b step. H1 stays INCONCLUSIVE;
H1b is a new row, run once, and graded only by what is written here.

Why a new row: run 2 of H1 held every criterion but its readiness wait,
`kubectl wait --for=condition=Ready pod --all -A`, which cannot exit 0 on k3s v1.37
because a Succeeded Job pod never turns Ready ([H1 run 2](#h1-run-2-i9-13900t-2026-10-17)).
H1b keeps H1's hypothesis and every other criterion and fixes only the wait.

Field selector checked before this was written (2026-10-17T21:06-21:11Z, under
the host lock, a throwaway k3d cluster `k3dgpu-h1b-fsel` on the k3s v1.37.1 node
image `k3d-gpu:eat-992df8d`, deleted after). The Kubernetes docs list
`status.phase` as a pod field selector with `=`, `==` and `!=`
(https://kubernetes.io/docs/concepts/overview/working-with-objects/field-selectors/),
and `kubectl wait --help` (client v1.35.2) lists `--field-selector`. On the
cluster, `kubectl get pods -A --field-selector=status.phase!=Succeeded` dropped
all four Succeeded Job pods (`helm-install-gateway-api-crd`,
`helm-install-traefik-crd`, `helm-install-traefik` and a test Job) and kept every
other pod, and `kubectl wait --for=condition=Ready pod -A
--field-selector=status.phase!=Succeeded` waited on those pods only.

- **Host.** The same shared i9-13900T and the same matched-load design as H1
  run 2: `bench/load/load.py start --level typical` holds the E-cores (16-31)
  for the whole run, and `bench/load/load.py sample` records busy %, load1, swap
  and throttle events in 60 s windows. Other tenants keep running and are
  recorded.
- **One fresh cluster.** `k3dgpu-h1b-eat`, created by this run and deleted after
  it, scratch `KUBECONFIG` (never `~/.kube/config`), an empty `EAT_WORK`. Every
  step runs once; nothing is retried and nothing is uninstalled between steps.
- **Steps, each timed (wall s) and logged in order.** (0) before the host lock:
  `docker build` of the node image at this commit, the EAT clone at
  `release-1.0.3-hotfix` and the bastion build (no cluster touched); (1)
  `k3d-gpu up` with `K3D_GPU_CLUSTER=k3dgpu-h1b-eat` and the README's EAT env;
  (2) `eat-up.sh`; (3) `eat-up.sh model cpu-qwen3-1-7b`; (4) `kubectl wait
  --for=condition=Ready pod -A --field-selector=status.phase!=Succeeded
  --timeout=30m`, output and exit code recorded verbatim; (4j) every Job in the
  cluster recorded on its own line (namespace/name, `Complete` or `Failed`
  condition, succeeded and failed counts, completion time), and the pod list;
  (5) one chat completion through EAT's LiteLLM gateway (ingress-nginx,
  `Host: api.example.com`), request and response recorded; (6) `eat-check.sh`
  (not graded); (7) `k3d-gpu down`. Host lock: `flock` on the shared live lock,
  at most 25 min from taking it to releasing it; the cluster is deleted and the
  load stopped before release.
- **Grading, fixed now.** SUPPORTED only if steps 1-3 exit 0, step 4 exits 0,
  step 5 returns HTTP 200 with a non-empty completion, and no Xeon-only
  instruction or node label is needed. The Job lines of step 4j are recorded,
  not graded. Any step 1-3 failing, or no completion, is REFUTED. Step 4 ending
  non-zero is REFUTED when any pod owned by a Deployment, StatefulSet,
  DaemonSet, ReplicaSet or Sandbox is not Ready at its end; it is INCONCLUSIVE
  when every pod not Ready is a Job pod (for example a Failed pod of a Job that
  later succeeded, which the selector keeps), or when the wait ends on a pod
  deleted during it. To stay inside the lock, step 4 is stopped 21 min after
  the lock is taken; a stop there, or the lock running out before step 5, is
  INCONCLUSIVE, with the pod list recorded.
- **Prediction.** SUPPORTED, from run 2: the wait should take about the vLLM
  pod's image pull and model load (run 2: 33 of 34 pods Ready 383 s after step
  3). Prior P(SUPPORTED) = 0.75; the main risks are a lock stop under a slower
  image pull and a pod replaced during the wait.

### H1b run (i9-13900T, 2026-10-17)

```text
# provenance
date:            2026-10-17T21:47:30Z..22:04:41Z (host lock held 1031 s of 1500); pre-registration pushed 21:13:47Z (093ba98, time fixed in 1ac2a39)
hypothesis:      H1b
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (8 P-cores, 16 E-cores; AVX2 and VNNI, no AVX-512, no AMX)
host kernel:     6.6.128-1-MANJARO
accelerators:    "Accelerators schedulable: nvidia=2 intel=8 hybrid=1"
k3d-gpu commit:  claude/h1b-eat 1ac2a390ea35b5de0351521d636ab0b515c40bf9 (main 968cf95 plus docs and ledger-check changes only)
node image:      k3d-gpu:eat-1ac2a39, image ID sha256:8a8a093acd204807b72587208c942d0d441364686f70b27e7be442aaa7d5c29a (k3s v1.37.1+k3s1);
                 the same image ID as H1 run 2
model:           Qwen/Qwen3-1.7B, bf16 (EAT catalog id cpu-qwen3-1-7b; revision not pinned by EAT)
runtime:         EAT release-1.0.3-hotfix 0d3438d7119de44cafc5903f66665c0b623e3333 (fresh clone); bastion image ID
                 sha256:ca1bed9cd2e387e0afcfc3fd569b0b34443ab687e2eff06bb751a4b708c1b90e;
                 vllm/vllm-openai-cpu:v0.24.0 (EAT chart values), KV cache 6 GiB
background load: bench/load/load.py typical on CPUs 16-31 for the whole run; 17 windows of 60 s:
                 load-CPU busy 54.8-65.0 % (median 58.4), load1 12.2-16.6 (median 14.3), swap used 143.4-144.4 GiB,
                 core throttle 26-10182 events/min (median 778); package W not measured (no root for RAPL);
                 53 other containers running at the start, host load1 19.3
runs per arm:    1 (one cluster, k3dgpu-h1b-eat, deleted at the end; scratch KUBECONFIG; empty EAT_WORK)
```

| Step | Command | Start (UTC) | rc | Wall (s) |
|---|---|---|---|---|
| 0 | node image build, EAT clone, bastion build (before the lock) | 21:14:50 | 0 | 2.9 |
| 1 | `k3d-gpu up` (README env, `K3D_GPU_CLUSTER=k3dgpu-h1b-eat`) | 21:47:31 | 0 | 48.9 |
| 2 | `eat-up.sh` (5 PLAY RECAPs, all `failed=0`) | 21:48:20 | 0 | 494.6 |
| 3 | `eat-up.sh model cpu-qwen3-1-7b` | 21:56:34 | 0 | 154.9 |
| 4 | `kubectl wait --for=condition=Ready pod -A --field-selector=status.phase!=Succeeded --timeout=30m` | 21:59:09 | 0 | 280.1 |
| 4j | every Job recorded | 22:03:50 | 0 | 0.1 |
| 5 | one chat completion through LiteLLM | 22:03:50 | 0 | 12.6 |
| 6 | `eat-check.sh` (not graded) | 22:04:03 | 0 | 28.4 |
| 7 | `k3d-gpu down` | 22:04:31 | 0 | 10.2 |

Step 4 output, verbatim:

```text
pod/agent-sandbox-controller-786cd8f99f-8hvgh condition met
pod/python-pool-2pch5 condition met
pod/python-pool-cwtnk condition met
pod/python-pool-nw67x condition met
pod/python-pool-sbrn5 condition met
pod/python-pool-zc6f9 condition met
pod/sandbox-router-deployment-d4bf6ff8d-l7zs2 condition met
pod/sandbox-router-deployment-d4bf6ff8d-xkqdn condition met
pod/vllm-qwen-3-1-7b-cpu-6ccd6d895f-ndjwl condition met
pod/genai-gateway-deployment-68cd6b65d6-kwtpx condition met
pod/genai-gateway-postgresql-0 condition met
pod/genai-gateway-redis-master-0 condition met
pod/genai-gateway-trace-clickhouse-shard0-0 condition met
pod/genai-gateway-trace-postgresql-0 condition met
pod/genai-gateway-trace-redis-primary-0 condition met
pod/genai-gateway-trace-s3-5c79c56bf4-np6np condition met
pod/genai-gateway-trace-web-6f564cd7cf-xk2hw condition met
pod/genai-gateway-trace-worker-5bcfd5c94d-8gxcw condition met
pod/genai-gateway-trace-zookeeper-0 condition met
pod/genai-gateway-trace-zookeeper-1 condition met
pod/genai-gateway-trace-zookeeper-2 condition met
pod/ingress-nginx-controller-6fdb88bff4-tb8dk condition met
pod/coredns-577d995dff-wjhf9 condition met
pod/intel-gpu-plugin-zb2j6 condition met
pod/local-path-provisioner-6858d854cf-rpzgx condition met
pod/metrics-server-778c4649b4-p9bx8 condition met
pod/nri-resource-policy-balloons-5grz4 condition met
pod/nvidia-device-plugin-daemonset-wcspx condition met
pod/nfd-gc-7d6955597d-mx7w7 condition met
pod/nfd-master-74b6cfb7df-84ncx condition met
pod/nfd-worker-55c2g condition met
pod/pgvector-0 condition met
pod/redis-stack-server-0 condition met
```

The two pods the selector left out were the Succeeded pods of the two Jobs.
Step 4j, one line per Job:

```text
default/register-qwen-qwen3-1-7b          SuccessCriteriaMet,Complete  succeeded=1  failed=0  start=2026-10-17T21:58:53Z  completion=2026-10-17T21:59:00Z
kube-system/helm-install-gateway-api-crd  SuccessCriteriaMet,Complete  succeeded=1  failed=0  start=2026-10-17T21:47:43Z  completion=2026-10-17T21:48:09Z
```

Step 5, the completion (trimmed):

```text
POST https://api.example.com/v1/chat/completions (resolve api.example.com:443 -> 172.24.0.2)
request:  {"model":"Qwen/Qwen3-1.7B","max_tokens":64,"temperature":0,
           "messages":[{"role":"user","content":"In one sentence, what is Kubernetes? /no_think"}]}
http_code=200 time_total=12.512036s
response: {"model":"Qwen/Qwen3-1.7B","system_fingerprint":"vllm-0.24.0-5616035f","choices":[{"finish_reason":"stop",
           "message":{"content":"<think>\n\n</think>\n\nKubernetes is an open-source platform for automating the
           deployment, scaling, and management of containerized applications."}}],
           "usage":{"completion_tokens":27,"prompt_tokens":20,"total_tokens":47}}
```

Step 6 printed `get_weather({"city": "Portland"})` for the tool call, the
sandbox ran the model's code (`{"stdout":"385\n","stderr":"","exit_code":0}`)
and it ended `==> EAT check passed.`

Verdict: **SUPPORTED**, confidence 0.85 for this host and this EAT release.
Every criterion held as written: steps 1-3 exited 0 in one pass with no retry,
the wait exited 0 on its own (no stop), the completion returned HTTP 200 with
content, and the only node labels EAT needed were `ei-infra-eligible` and
`ei-inference-eligible`, which are not Xeon labels. EAT's vLLM values file is
named `xeon-values.yaml`, but it ran on a CPU with no AVX-512 and no AMX, so no
Xeon-only instruction was needed. H1 keeps its INCONCLUSIVE verdict.

Scope: one i9-13900T, n=1, a shared host under the `typical` load instead of a
fresh host (53 other containers, ~144 GiB of swap in use, a throttling
package). It shows that EAT deploys and serves on this client CPU. It says
nothing about speed or about other Core or Core Ultra parts.

```bash
# Reproduce on one fresh cluster under the typical load. Host needs docker, k3d, kubectl, curl, jq.
lscpu | grep 'Model name'; uname -r; git rev-parse HEAD
export KUBECONFIG=$PWD/h1b.kubeconfig K3D_GPU_CLUSTER=k3dgpu-h1b-eat EAT_WORK=$PWD/eat-work
IMAGE=k3d-gpu:eat-$(git rev-parse --short HEAD)
docker build -q -t "$IMAGE" . && docker image inspect --format '{{.Id}}' "$IMAGE"
bench/load/load.py start --level typical --ttl 1800
bench/load/load.py sample --seconds 60 --windows 26 --tag h1b --level typical --out load.jsonl &
K3D_GPU_IMAGE="$IMAGE" \
K3D_GPU_NODE_LABELS=ei-infra-eligible=true,ei-inference-eligible=true \
K3D_GPU_K3S_ARGS=--disable=servicelb scripts/k3d-gpu up
integrations/eat/eat-up.sh
integrations/eat/eat-up.sh model cpu-qwen3-1-7b
kubectl wait --for=condition=Ready pod -A --field-selector=status.phase!=Succeeded --timeout=30m
kubectl get jobs -A                                              # every Job: COMPLETIONS 1/1
integrations/eat/eat-check.sh                                    # expect: ==> EAT check passed.
scripts/k3d-gpu down; bench/load/load.py stop
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

### H3 host-level observation (i9-13900T, 2026-10-16)

The wave-1 host-level probe in the 2026-10-16 "H3 (motivates H5)" log entry. It
is not the H3 verdict run. Its numbers first appear in commit 39db284
(2026-10-16T23:04Z), which marked them as not backed by a header. No raw
results, scripts or command lines for it are in the repository, its history or
the `archive/*` tags. Fields below come from that log entry and the wave-1 host
notes; the rest are not recorded.

```text
# provenance
date:            2026-10-16, before 23:04Z (commit 39db284); start and end times not recorded
hypothesis:      H3 (observation; motivates H5)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P = CPUs 0-15, E = 16-31)
host kernel:     not recorded
accelerators:    not recorded (host level, no cluster)
k3d-gpu commit:  not recorded (no cluster was used)
node image:      none (no cluster)
model:           not recorded
runtime:         not recorded (server and digest); inference pinned to CPUs 0-14; neighbour stress-ng
                 with 15 workers pinned to CPUs 17-31, methods all, loop, matrixprod; stress-ng version not recorded
energy source:   not recorded (package temperature 91-96 C and package_throttle_count about 22M were read)
runs per arm:    4
```

```bash
# Reproduce: the exact commands were not recorded. The shape, from the log entry:
#   inference server pinned to CPUs 0-14 (pinning tool, server, model and prompt not recorded)
#   stress-ng --cpu 15 --cpu-method <all|loop|matrixprod>, pinned to CPUs 17-31 (tool not recorded)
# The k3d-gpu version of this test, with a full header, is the H3 run above.
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

**Amendment A4: re-run under amendment 27** (written 2026-10-18T15:38Z, committed 70a3d6f, after
run 3 and before any A4 round; nothing below has been measured). Run 3 stays
INCONCLUSIVE under the rule that scored it (no regrade). Amendment 27
(`matched_a27`, merged in #129) replaces the load rule for this re-run:
- **Load record.** Per level, two 10 s `--phase baseline` windows after the
  cluster is up and before the first round. Before each round, after the pod is
  Ready: `load.py settle` (5 s windows until load-CPU busy and throttle/min are
  within tolerance of the baseline, at most 60 s; replaces A3's 30 s idle and
  two 10 s windows); its last window is the round's pre-window. While the round
  runs, 5 s `--phase during` windows with `--treatment-cpus` set to the pod's
  `Cpus_allowed_list` (the P-core balloon, so the E-core load set is unchanged).
- **Matching.** Covariates gate under the unchanged `TOLERANCE`: pre-window busy,
  throttle/min, swap used and swap growth; during-window busy and the two swap
  metrics. Package W, throttle/min and 1-min load during the round are reported
  outcomes, never gating. A window with fewer load containers than the level
  runs makes the level VOID (re-run, never scored).
- **Order and seeds.** ON OFF ON OFF ON OFF per level as in A2; new seeds
  2531-2536 (`typical`) and 2541-2546 (`heavy`). Cluster `k3dgpu-r-shared-prefix`,
  scratch KUBECONFIG. Lock budget 25 min for the whole run (`HOLD=1440`):
  `typical` first; `heavy` only if a full level (18:30 + 60 s) still fits.
- **Unchanged.** Every prediction, threshold, prior and guardrail in the
  pre-registration, A1, A2 and A3. The ledger verdict is T at `typical`; M is
  not measured (A3).

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

### H25 run 4: amendment 27 load rule (i9-13900T, 2026-10-18)

```text
# Reproduce
date:            2026-10-18T15:38:49Z (lock taken) .. 15:48:57Z (cluster and load removed, lock released)
hypothesis:      H25 (pre-registration + amendments A1, A2, A3, A4; load rule amendment 27)
pre-registered:  A4 committed 70a3d6f at 15:38:18Z, pushed before the lock; bcca7be (15:38:44Z)
                 only bounds the during sampler to 60 windows
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P 0-15, E 16-31)
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  bcca7be (branch claude/verdict-h25-a27), k3d v5.9.0
node image:      cryptoandcoffee/k3d-gpu:0.1.28 (sha256:7af53bf2e970…, as runs 1 and 3)
model:           OpenVINO/Qwen3-4B-int4-ov b467368d, openvino_model.bin sha256 074214fa…df2f9 (matches H6)
runtime:         openvino/model_server@sha256:a928903f…6fc58 (pulled by the pod; k3d import found no local copy),
                 agent CPU manifest, pod Cpus_allowed_list 0-15 in all 6 rounds, hostPath models
load:            bench/load/load.py `typical`, stress-ng 0.22.01 @sha256:2ea9a7c3…1cbe on E-cores 16-31 (3 containers)
energy source:   RAPL intel-rapl:0 energy_uj (sudo -A), in round-*.json and load windows
command:         IMAGE=cryptoandcoffee/k3d-gpu:0.1.28 MODELS=... OUT=... LEVELS="typical heavy" \
                 CLUSTER=k3dgpu-r-shared-prefix SEED_BASE=2530 HOLD=1440 \
                 flock -w 21600 "$LOCK" timeout 1440 bench/shared-prefix/run.sh
runs per arm:    typical ON 3, OFF 3 (order ON OFF ON OFF ON OFF, seeds 2531-2536);
                 heavy not run (HOLD: 745 s left after typical, a level needs 1170 s)
```

All 42 scored requests returned with no error, `prompt_tokens` 1634 and
`completion_tokens` 8, TTFT present in every one; graph.pbtxt read back as
intended in all 6 rounds. No round is void. Every phased window (2 baseline,
6 pre, 75 during) counted 3 of 3 load containers running, so the level is not
VOID. Every settle gap settled within tolerance (5, 5, 15, 15, 5, 5 s).

**Load, amendment 27 (`matched_a27`).** Covariates (gating, `TOLERANCE`):

| Phase | Metric | ON | OFF | spread / limit |
|-------|--------|----|-----|----------------|
| pre (n = 3 per arm) | E-core busy % | 43.91 | 43.77 | 0.14 / 5.0 ok |
| pre | throttle/min | 557.8 | 645.2 | 87.4 / 161.3 ok |
| pre | swap used MiB | 137818 | 137617 | 201 / 256 ok |
| pre | swap growth MiB | -9.0 | -10.5 | 1.5 / 64 ok |
| during (ON 24, OFF 51 windows) | E-core busy % (load CPUs minus pod CPUs 0-15 = 16-31) | 54.24 | 55.69 | 1.45 / 5.0 ok |
| during | swap used MiB | 137728 | 137516 | 213 / 256 ok |
| during | swap growth MiB | -17.5 | -12.3 | 5.2 / 64 ok |

Load matched: **yes**. Swap used is the closest check (213 of 256 MiB), and it
fell steadily through the run in both arms (138452 MiB at baseline, 137113 at
round 6), so ON, which starts each pair, reads higher. Outcomes during the
round (reported, never gating): package W 111.8 ON vs 114.6 OFF; throttle/min
2324.6 ON vs 1337.4 OFF; 1-min load 12.8 vs 14.4. Throttle during ON exceeds
OFF here, the reverse of run 3 at `typical`.

**Pre-registered verdict: T SUPPORTED at `typical`** (the ledger level). M is
not measured (A3) and stays INCONCLUSIVE from run 1.

| | ON (shipping default) | OFF (`--enable_prefix_caching=false`) |
|---|---|---|
| added agents a2..a6, median TTFT | 1.02 s (n = 15) | 10.87 s (n = 15) |
| per-round medians | 1.06, 1.04, 1.01 s (spread 0.05 s, 4%) | 10.74, 10.87, 10.90 s (spread 0.16 s, 1.5%) |
| cold agent a1, per round | 10.91, 10.77, 10.79 s | 10.81, 11.08, 10.91 s |
| control c (one token changed near position 16), per round | 11.06, 11.37, 10.97 s | - |
| package energy per round | 4838, 4888, 4835 J | 10495, 10611, 10537 J |

- Ratio OFF/ON of the added-agent median TTFT: **10.61x**, bootstrap 95% CI
  10.36-10.74x (10000 resamples, seed 25). The lower bound clears the
  pre-registered 4x.
- Controls: c/OFF median 1.02 (≥ 0.8x: one changed token near the start makes
  a cold request); a1 ON/OFF 0.99 (inside ±15%). Per-arm spread gate passes.
- Cross-round pairs ≥ 4x: 9 of 9. Derived confidence by the A2 rule:
  (9+1)/(9+2) = 0.91 that an added agent's first chunk arrives ≥ 4x sooner with
  the shipping default on this host under this load.
- Point prediction: OFF 9.6 s ± 15% (8.2-11.0 s), measured 10.87 s, inside;
  ON 0.9 s (0.6-1.4), measured 1.02 s, inside.
- Energy: a round of 7 requests used 0.46x the OFF package energy with
  caching ON (whole package, background included).
- Consistent with run 3 (9.44x at `typical`, 9.27x at `heavy`, scored
  INCONCLUSIVE under the earlier load rule, not regraded).

Limits: one host (Raptor Lake, 16 P-cores for OVMS), one model, one prefix
shape (Ps 1538, Pp 96), `typical` load only; `heavy` was not run in this hold.
What this measures is the cost per added agent of OVMS's shipped prefix
caching, which k3d-gpu does not enable or change; it is not an invention
(prior art [provenance:P250] [provenance:P252] [provenance:P253]
[provenance:P259]). Per the pre-registered action, docs may cite the
added-agent TTFT as [ledger:H25]; no default or code change follows.

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

**Amendment A2: matched background load (2026-10-17, written before any timed block).**
Real Intel hosts are busy, so H26 now runs under the same controlled background load in
every arm. The hog stays the treatment: its design, arms, block order and A1 rules do not change.
- Background: `bench/load/load.py start --level L` on the load CPUs (E-cores 16-31 here) for the whole
  level, `typical` first (the headline), then `heavy` (robustness) if the lock budget allows. The
  hog and the background share the E-cores; the agent stays on P-cores (A1 abort rule).
- Order per level as before: Q ME15 ME4 MC4 / MC4 ME4 ME15 Q / Q ME15 ME4 MC4, 10 timed requests
  per block, n = 30 per arm. One cluster for both levels; the background is restarted per level.
- Per block, after the previous hog is deleted and before the next one starts (agent idle, no hog):
  `load.py sample --seconds 5 --windows 3 --tag ARM --level L`. These windows decide matching:
  `load.py matched` over the level's windows with TOLERANCE as committed in 402518e (unchanged since)
  (busy_pct 5 pp; load1 max(1, 15%); pkg_w max(3 W, 10%); throttle_per_min max(10, 25%);
  swap used 256 MiB; swap growth 64 MiB per window). Unmatched means INCONCLUSIVE at that level,
  naming the metric. High load alone never voids a level.
- The in-block readings (IMC GB/s, package J, throttle delta, pswpin, CPU 0-15 busy, load1) include
  the hog, so they are reported per arm but do not decide matching.
- ME4's placement check counts only stream workers in a `kubepods` cgroup, so the background's own
  `stress-ng-stream` workers cannot satisfy it.
- Each level gets its own A1 verdict. The ledger verdict is the `typical` one; `heavy` is reported as
  robustness. Confidence stays the bootstrap P(R <= 0.5); P(R >= 0.8) is reported beside it.
- Budget: each level aborts 18 min after its background load starts, the whole run at 39 min of
  lock time (cluster setup included); a level with under 10 min left is not started; unrun blocks
  are void. The cluster and the load containers are removed before the lock is released.
- sudo -A reads RAPL `energy_uj` and the uncore IMC counters (`perf stat`), as pre-registered; no writes.
- Predictions unchanged from A1: REFUTED (R ~1). Priors: SUPPORTED 0.10, REFUTED as stated 0.20,
  REFUTED 0.45, INCONCLUSIVE 0.25.

### H26 run: matched background load (i9-13900T, 2026-10-17)

```text
# Reproduce
date:            2026-10-17T21:11:51Z (lock taken) .. 21:47:30Z (cluster and load removed)
hypothesis:      H26 (pre-registration + amendments A1, A2)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P 0-15, E 16-31; E-core L2 groups 16-19 20-23 24-27 28-31)
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  fd01e13 (branch claude/verdict-rdt-isolate-ml), k3d v5.9.0
node image:      cryptoandcoffee/k3d-gpu@sha256:dda8eec17ceff513ae6ae814e7c98158ae81127fbb4741941a2e4965ac5b05fe (0.1.47)
model:           OpenVINO/Qwen3-4B-int4-ov b467368d, openvino_model.bin sha256 074214fa…df2f9 (matches H6, H25),
                 cloned outside the lock and copied into the agent PVC (MODELS); the pull init skipped the download
runtime:         openvino/model_server@sha256:a928903f…6fc58, `k3d-gpu agent up`, CPU, 15 CPU,
                 agent Cpus_allowed_list 0-14 (off the E-cores), K3D_GPU_AUTOQUANT=off
neighbour:       bench/noisy.sh --stressor stream, stress-ng 0.22.01 by digest, --stream-l3-size 128m
load:            bench/load/load.py, stress-ng 0.22.01 @sha256:2ea9a7c3…1cbe on E-cores 16-31
energy source:   RAPL intel-rapl:0 energy_uj and uncore IMC free-running counters (sudo -A, reads only)
command:         SUDO_ASKPASS=<askpass> K3D_GPU_AUTOQUANT=off MODELS=<clone dir> OUT=<dir> \
                 flock -w 14400 "$LOCK" bench/rdt-isolate/run.sh      (LEVELS default "typical heavy")
runs per arm:    per level 3 blocks x 10 timed requests (n = 30), order Q ME15 ME4 MC4 / MC4 ME4 ME15 Q /
                 Q ME15 ME4 MC4; 9 background windows of 5 s per arm per level
```

A first attempt (20:48-20:59Z) never reached a timed block: the agent's pull init fetched the
model from Hugging Face at about 2 MB/s and the deployment hit its 600 s progress deadline. The
run above copies a pre-fetched clone into the PVC instead (`MODELS`, commit fd01e13).

Guardrails held at both levels: 132/132 requests returned 32 tokens, the agent pod never
restarted, no block was void. Every MC4 hog sat on CPUs 24-27 (one L2 group). Every ME4 hog
touched 2-3 L2 groups. The `efficient` balloon the ME15 and ME4 hogs got was
`15,17-19,21-31`, so it held one P-core (15) and left out E-cores 16 and 20. The agent (0-14)
did not overlap it.

**Background load per arm** (means of the 9 windows taken with no hog running, just before
each block; limits from `load.py` TOLERANCE):

| Level | Arm | E-core busy % | 1-min load | package W | throttle/min | swap used MiB | swap growth MiB |
|-------|-----|---------------|------------|-----------|--------------|---------------|-----------------|
| typical | Q | 61.8 | 26.39 | 92.7 | 212 | 148284 | -6.1 |
| typical | ME15 | 60.6 | 23.31 | 94.7 | 390 | 148266 | -1.6 |
| typical | ME4 | 54.5 | 33.94 | 91.5 | 624 | 148250 | -0.9 |
| typical | MC4 | 57.2 | 27.09 | 92.0 | 281 | 148284 | -1.3 |
| typical | spread / limit | **7.3 / 5.0 fail** | **10.64 / 5.09 fail** | 3.2 / 9.5 ok | **411 / 156 fail** | 34 / 256 ok | 5.3 / 64 ok |
| heavy | Q | 38.7 | 23.29 | 91.7 | 1476 | 147965 | -0.2 |
| heavy | ME15 | 38.1 | 20.95 | 92.2 | 1179 | 147965 | -0.5 |
| heavy | ME4 | 11.3 | 24.55 | 85.8 | 1859 | 148016 | -1.1 |
| heavy | MC4 | 14.4 | 18.73 | 85.8 | 1909 | 148014 | -0.7 |
| heavy | spread / limit | **27.4 / 5.0 fail** | **5.82 / 3.68 fail** | 6.4 / 9.2 ok | **730 / 477 fail** | 51 / 256 ok | 0.9 / 64 ok |

Load matched: **no**, at both levels.
- `typical`: the 1-min load in ME4's windows (33.9) is the outlier, and ME4 follows ME15 in
  rounds 1 and 3. The windows start seconds after the previous hog is deleted, so the 15-worker
  hog's run queue is still in the 1-min load average, and the throttle rate and E-core busy
  carry over the same way. A2 put no decay gap before the windows. That is a design fault in the
  amendment, recorded here and not corrected after the fact.
- `heavy`: the background itself stopped at 21:33:30Z, 2 min 27 s after it started. All three
  load containers ended in the same second with no OOM kill in the kernel log, so they were
  removed from outside the run. E-core busy fell from 99.9% to 4-22% for blocks 3-12. Block 1
  (Q) was the only Q block under the full heavy load.

**Pre-registered verdict: INCONCLUSIVE at `typical` (the ledger level) and at `heavy`. Cause:
background load unmatched across arms (`typical`: busy_pct, load1 and throttle_per_min;
`heavy`: the background was removed mid-level).**

What the same requests measured at `typical` (reported, not a verdict):

| Arm | p50 | p95 | p95 95% CI | I(p95) | block medians | package J per block | IMC GB/s in block |
|-----|-----|-----|------------|--------|---------------|---------------------|-------------------|
| Q | 3.37 s | 3.70 s | 3.59-3.86 | 0 | 3.53, 3.39, 3.21 | 3627 | 47.2 |
| ME15 | 9.23 s | 10.65 s | 10.01-11.45 | +188% | 9.73, 9.22, 8.80 | 9577 | 48.7 |
| ME4 | 5.04 s | 5.31 s | 5.23-5.37 | +43% | 4.66, 5.05, 5.17 | 5101 | 48.2 |
| MC4 | 4.34 s | 5.04 s | 4.73-5.06 | +36% | 4.19, 4.46, 4.56 | 4587 | 48.0 |

- R = I(MC4)/I(ME4) = **0.83**, bootstrap 95% CI 0.61-0.87 (10 000 resamples stratified by
  block). Per round 0.31, 0.84, 0.89. P(R <= 0.5) = 0.0001 and P(R >= 0.8) = 0.72. With load
  matched this would have read REFUTED (`verdict_if_matched`); dropping swap-flagged blocks does
  not change it. Derived confidence in that reading: 0.72, the bootstrap share in the R >= 0.8
  band. It is not claimable while the load is unmatched.
- The positive control held: ME15 more than doubles p95 (+188%), as in H3 PM.
- The hog adds little DRAM traffic over this background. Q already reads 47.2 GB/s at the IMC
  (the `typical` profile runs `--stream` workers), and the hog arms read 48.0-48.7 GB/s. The
  latency cost of ME15 looks like E-core and ring contention more than added DRAM bandwidth
  here. That is post hoc.
- `heavy` (background gone after block 2): Q p95 6.65 s (block 1 alone ran at 6.08 s median,
  blocks 8-9 at 2.9 s), so I(ME4) and I(MC4) come out negative and R is undefined.
  `verdict_if_matched` is INCONCLUSIVE ("I(ME4) < +10%"). These numbers carry no weight.

The A1 prediction (R ~1, REFUTED) is where the `typical` point estimate lands, but H26 stays
INCONCLUSIVE. Next probe, to be pre-registered before use: a decay gap of at least 3 min of
1-min load (or windows sampled at a fixed point that every arm reaches from the same
predecessor), and a load container owner check so a run can see its own background vanish
and stop.

### H26 amendment A3: re-run under amendment 27 (2026-10-18)

Written 2026-10-18T18:15Z, before any A3 block (nothing below has been measured). The
H26 row, the pre-registration table, A1 (primary R = I(MC4)/I(ME4), placement checks,
IMC and noise gates), A2's arms, block order, n = 30 per arm, the thresholds, the
predictions and the priors are unchanged. What changes is the load record: the A2 run
failed matching on hog carry-over, and amendment 27 (`matched_a27`, merged in #129,
`523c23c`) adds the decay gap that run lacked.
- **Load.** As A2: `bench/load/load.py start --level L` on the E-cores (16-31) for the
  whole level, agent on P-cores 0-14 (A1 abort rule), the hog sharing the E-cores with
  the background. `typical` first, the ledger level.
- **Windows.** Per level, two 10 s `--phase baseline` windows 20 s after the background
  starts, before the first block. Before each block, after the previous hog pod is
  deleted and before the next starts, `load.py settle` replaces A2's 3 x 5 s windows:
  5 s windows until load-CPU busy and throttle/min are within tolerance of the baseline,
  at most 60 s; the last one is the block's pre-window. While the block's 11 requests
  run (from 5 s after the hog is Running), 5 s `--phase during` windows with
  `--treatment-cpus` set to the hog's `Cpus_allowed_list` (none for Q).
- **Matching.** `load.py matched` (`matched_a27`) over the level's pre and during
  windows, unchanged `TOLERANCE`: covariates gate (pre busy, throttle/min, swap used,
  swap growth; during busy over the E-cores the hog does not hold, swap used, swap
  growth). Package W, throttle/min and 1-min load during the block are outcomes,
  reported only. A window with fewer than the level's 3 load containers running makes
  the level VOID (never scored, re-run). A settle gap that hits 60 s is reported.
  `analyze.py` scores these windows with `matched_a27` (unit tests `Amendment27`).
- **Risk stated before measuring.** During ME15 and ME4 the hog's balloon held 13 of
  the 16 E-cores in the A2 run (`15,17-19,21-31`), so their during busy reads only
  E-cores 16 and 20; MC4's reads 12 E-cores, Q's all 16. The background's `--cpu`
  workers float over 16-31, and if the scheduler packs them onto the free E-cores
  during the 15-worker hog, ME15's during busy rises and the level reads INCONCLUSIVE
  (during busy_pct unmatched). Predicted P(load matched at `typical`) 0.5.
- **In-block readings** (IMC GB/s, package J, throttle delta, pswpin, CPU 0-15 busy,
  1-min load) are reported per arm, as in A2.
- **Lock and budget.** `flock -w 21600` on the live lock, cluster
  `k3dgpu-r-rdt-isolate`, scratch KUBECONFIG, `SUDO_ASKPASS` for the pre-registered
  reads only (RAPL, `perf stat` on the IMC counters). The hold is capped at 25 min:
  `TOTAL_S=1380` for the script (setup included) and `ABORT_S=1260` per level; the
  cluster and the load containers are removed before the lock is released. `heavy`
  starts only with 10 min of `TOTAL_S` left, so it is not expected to run. Blocks the
  budget cuts are void and reduce n; the run section reports them.
- **Prediction unchanged (A1):** REFUTED (R ~1). Priors: SUPPORTED 0.10, REFUTED as
  stated 0.20, REFUTED 0.45, INCONCLUSIVE 0.25. The A2 run stays INCONCLUSIVE under the
  rule that scored it; it is not regraded.

### H26 run A3: amendment 27 load rule (i9-13900T, 2026-10-18)

```text
# Reproduce
date:            2026-10-18T18:42:09Z (lock taken) .. 18:58:27Z (cluster and load removed, lock released)
hypothesis:      H26 (pre-registration + amendments A1, A2, A3; load rule amendment 27)
pre-registered:  A3 committed 093ccbf at 18:16Z, pushed (PR #137) before the lock was queued at 18:16:57Z
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P 0-15, E 16-31; E-core L2 groups 16-19 20-23 24-27 28-31)
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  093ccbf (branch claude/verdict-h26-a27), k3d v5.9.0
node image:      cryptoandcoffee/k3d-gpu:0.1.59 (sha256:e367c7fa6a46…)
model:           OpenVINO/Qwen3-4B-int4-ov b467368d (the clone the A2 run used), copied into the agent PVC (MODELS)
runtime:         openvino/model_server@sha256:a928903f…6fc58, `k3d-gpu agent up`, CPU, 15 CPU,
                 agent Cpus_allowed_list 0-14 (off the E-cores), K3D_GPU_AUTOQUANT=off
neighbour:       bench/noisy.sh --stressor stream, stress-ng 0.22.01 by digest, --stream-l3-size 128m
load:            bench/load/load.py `typical`, stress-ng 0.22.01 @sha256:2ea9a7c3…1cbe on E-cores 16-31 (3 containers)
energy source:   RAPL intel-rapl:0 energy_uj and uncore IMC free-running counters (sudo -A, reads only)
command:         SUDO_ASKPASS=<askpass> K3D_GPU_AUTOQUANT=off MODELS=<clone dir> OUT=<dir> LEVELS="typical heavy" \
                 K3D_GPU_CLUSTER=k3dgpu-r-rdt-isolate \
                 flock -w 21600 "$LOCK" timeout -k 20 1440 bench/rdt-isolate/run.sh
runs per arm:    typical: 3 blocks x 10 timed requests (n = 30), order Q ME15 ME4 MC4 / MC4 ME4 ME15 Q /
                 Q ME15 ME4 MC4; heavy not run (under 10 min of TOTAL_S left after typical, as A3 expected)
```

Guardrails held: 132/132 requests returned 32 tokens, the agent pod never restarted, no
block was void. Every MC4 hog sat on CPUs 24-27 (one L2 group). Every ME4 hog's 4 workers
touched 2-3 L2 groups. The ME15 and ME4 hogs got the `efficient` balloon `15,17-19,21-31`,
as in the A2 run. Every phased window (2 baseline, 12 pre, 66 during) counted 3 of 3 load
containers running, so the level is not VOID. Every settle gap settled within tolerance
(5-60 s; MC4 block 5 reached tolerance in its 60 s window).

**Load, amendment 27 (`matched_a27`), `typical`.** Covariates (gating, `TOLERANCE`):

| Phase | Metric | Q | ME15 | ME4 | MC4 | spread / limit |
|-------|--------|---|------|-----|-----|----------------|
| pre (3 per arm) | E-core busy % | 47.28 | 46.35 | 46.27 | 44.88 | 2.40 / 5.0 ok |
| pre | throttle/min | 732.4 | 701.0 | 772.4 | 684.9 | 87.5 / 193.1 ok |
| pre | swap used MiB | 153378 | 153380 | 153380 | 153378 | 2.2 / 256 ok |
| pre | swap growth MiB | 0.0 | -0.13 | 0.0 | -0.07 | 0.13 / 64 ok |
| during (9 / 29 / 14 / 14 windows) | busy %, E-cores the hog does not hold | 58.04 (16-31) | 95.00 (16, 20) | 73.03 (16, 20) | 79.06 (16-23, 28-31) | **36.96 / 5.0 fail** |
| during | swap used MiB | 153378 | 153379 | 153380 | 153377 | 2.7 / 256 ok |
| during | swap growth MiB | -0.07 | -0.30 | -0.28 | -0.19 | 0.24 / 64 ok |

Load matched: **no**, on one covariate: during busy_pct. The settle gap did its job: every
pre-window covariate matched, the carry-over that failed the A2 run is gone. The failure is
the one A3 named before the run. When the hog holds 13 of the 16 E-cores, the background's
floating `--cpu` workers pack onto the E-cores left free, so the free cores read 95% busy
under the 15-worker hog against 58% with no hog. Even the primary pair alone would fail
(ME4 73.03 vs MC4 79.06, spread 6.0 > 5.0). Outcomes during the block (reported, never
gating): package W Q 100.8, ME15 93.6, ME4 95.7, MC4 96.4; throttle/min 39.7, 0.0, 1.7,
6.8; 1-min load 14.4, 22.2, 19.3, 16.5.

**Pre-registered verdict: INCONCLUSIVE at `typical` (the ledger level). Cause: load
unmatched on during busy_pct (36.96 > 5.00).** `heavy` was not run.

What the same requests measured (reported, not a verdict):

| Arm | p50 | p95 | p95 95% CI | I(p95) | block medians | package J per block | IMC GB/s in block |
|-----|-----|-----|------------|--------|---------------|---------------------|-------------------|
| Q | 1.66 s | 2.31 s | 1.98-2.59 | 0 | 1.60, 1.69, 1.62 | 1896 | 41.9 |
| ME15 | 4.41 s | 6.69 s | 5.67-7.49 | +190% | 4.41, 4.23, 4.82 | 4857 | 49.3 |
| ME4 | 2.28 s | 3.31 s | 2.88-3.78 | +44% | 2.71, 2.20, 2.24 | 2592 | 48.0 |
| MC4 | 2.24 s | 3.23 s | 2.58-3.63 | +40% | 2.09, 2.16, 2.31 | 2464 | 47.9 |

- R = I(MC4)/I(ME4) = **0.92**, bootstrap 95% CI 0.14-2.09 (10 000 resamples stratified by
  block, seed 26). Per round 0.33, 1.27, 0.98. P(R <= 0.5) = 0.25, P(R >= 0.8) = 0.54.
  With load matched this would have read REFUTED (`verdict_if_matched`; dropping
  swap-flagged blocks does not change it). Derived confidence in that reading: 0.54, the
  bootstrap share in the R >= 0.8 band. It is not claimable while the load is unmatched, so
  no DO-NOT-RE-ATTACK is recorded.
- Gates that would pass: positive control I(ME15) +190% (>= +20%); I(ME4) +44% (>= +10%);
  Q block-median spread 5.5% (<= 25%); the MC4 hog kept 98.7% of the ME4 hog's IMC
  bandwidth above Q (5.98 vs 6.06 GB/s), so MC4 is not a throttled hog.
- Consistent with the A2 run (R 0.83, CI 0.61-0.87, scored INCONCLUSIVE under the earlier
  rule, not regraded) and with the A1 prediction (R ~1): placing 4 stream workers inside one
  E-core L2 cluster gives the P-core agent about the same p95 as spreading them. Round 1's
  0.33 comes from ME4 block 3 (median 2.71 s against 2.20 and 2.24), which ran right after
  the first ME15 block.
- Absolute latencies are lower than in the A2 run (Q p95 2.31 vs 3.70 s; the host was
  quieter: 1-min load 14-22 vs 23-34). The p95 point prediction of the pre-registration
  (Q ~2.3 s) lands this time.

Next probe, to be pre-registered before use: the during-arm busy covariate cannot match
when the treatment itself occupies the load CPUs, because the background's workers move
onto the cores the hog leaves free. Either run the background on CPUs no hog arm can use
(for example pin the hog balloons and the load to disjoint E-core sets, which changes the
arms), or pre-register for H26 that during busy over the load CPUs is an outcome when the
treatment shares them, keeping the pre-window covariates as the gate. Neither is applied to
this run.

### H27 amendment A1: matched background load (2026-10-17)

Written 2026-10-17T22:10Z, before any H27 run. The ledger row above stays the
pre-registration. A1 changes only the background the arms run under. The arms, the
round order, n, the gate, the scoring rules (a), (b) and (c), the outcome priors and the
predictions stay as written.

Real Intel boxes are busy. In earlier runs the background load was uncontrolled and drifted
between arms. A1 runs every arm under the same controlled background load,
interleaved. It records that load per arm. A level is INCONCLUSIVE because its load is
unmatched across arms, never because the load is high.

- **Background:** `bench/load/load.py start --level L` on the load CPUs (E-cores 16-31 on
  this i9-13900T). OVMS stays on P-cores 0-15, as pre-registered. The background starts
  before the 60 s swap preflight and the OVMS cold loads. It runs for the whole level and
  stops after the last timed request. Levels: `typical` first, which is the headline and the
  ledger verdict. Then `heavy` as the robustness check, only if lock time remains.
- **Order and n (unchanged):** warm-up, P1, R1 ABCD, R2 DCBA, P2, R3 ABCD, R4 DCBA,
  R5 ABCD, P3. Arms are int4, int8, fp16 and int4' (the negative control). n = 5 reference
  requests per arm per level, plus 3 probe repeats.
- **Load windows:** before every reference request, OVMS sits idle for a 10 s gap. Then
  `load.py sample --seconds 5 --tag ARM --level L` runs, and then the request. That gives
  5 windows per arm per level, with no request in flight during any window. Matching is
  `load.py matched` over the level's 20 windows, with TOLERANCE as committed in 402518e
  and unchanged since: busy_pct 5 pp; load1 max(1, 15%); pkg_w max(3 W, 10%);
  throttle_per_min max(10, 25%); swap used 256 MiB; swap growth 64 MiB per window.
- **Why a 10 s gap is enough (checked before the run):** H26 failed matching because its
  E-core hog's run queue carried into the next arm's windows. Here the treatment runs on the
  P-cores, so it cannot touch E-core busy, and P-core throttling and power stop when the
  request ends. Only the 1-min load average carries over. I simulated that average
  (60 s EMA, 16 runnable threads per request, request lengths int4 9 s, int8 14 s,
  fp16 26 s). Across the ABCD/DCBA predecessor mix, the per-arm carry-over spread is
  0.8-1.0 at gaps of 0-30 s. The limit is max(1, 15% of about 25) = 3.75 or more.
- **Owner check:** before each window, exactly 3 `io.k3d-gpu.bench-load` containers of that
  level must be running. If the background vanishes, the level aborts and reads
  INCONCLUSIVE ("background lost"). This repeats the H26 heavy failure on purpose so it
  cannot go unseen.
- **Not used for matching:** the per-request confounds in `turn.py` (RAPL W, throttle,
  load1, P-core busy, pgmajfault, pswpin) include the request itself. They are reported per
  arm but do not decide matching.
- **Quality pass:** NLL is teacher-forced and deterministic, so the load cannot change it.
  It runs once, under the lock, after the `typical` level, with no background running.
  Both levels score against that one `nll.json`.
- **Budget:** each level's timed part aborts at 19 min of its own wall time. The NLL pass
  is capped at 10 min. `heavy` starts only if 19 min of the lock budget remain, and the
  whole lock hold is capped at 40 min. A level the watchdog aborts is INCONCLUSIVE
  ("budget"). The `k3dgpu-i-amx-autoquant-*` containers and the load containers are removed
  before the lock is released. No k3d cluster is made, as pre-registered.
- **Verdict per level:** (a), (b) and (c), plus the O5 INCONCLUSIVE rules, as pre-registered.
  A level is INCONCLUSIVE only if (1) its load is unmatched, naming the metric, (2) the
  background was lost, (3) the budget ran out, or (4) an O5 gate fired. High load alone
  never voids a level. The ledger verdict is the `typical` one, and `heavy` is reported as
  robustness. Confidence is as pre-registered: high means 5/5 round wins (sign test
  p = 0.031) and 3/3 probe agreement; medium means 4/5 or 2/3.
- RAPL is read through `sudo -A`, reads only.
- **Predictions unchanged:** (a) holds; (b) is REFUTED because the pick is int4, the
  default (delta 0). The latency ranges in the row were set for an idle host. They are
  predictions, not decision rules, and A1 does not move them. Priors unchanged: O1 0.55,
  O2 0.25, O3 0.08, O4 0.04, O5 0.08.

### H27 amendment A2: swap preflight on a busy host (2026-10-17)

Written 2026-10-17T22:24Z. Two A1 attempts (22:17Z typical, 22:18Z heavy) aborted in the
60 s swap preflight, before any OVMS container or request: swap-in was 5 and 3 pages
(20 and 12 KiB). This host holds about 145 GiB of swapped-out pages from its production
containers and swaps in a few pages a minute in steady state. The pre-registered gate demanded
exactly zero, so it is a quiet-host gate, and A1's rule is that load alone never voids a run.
- New preflight: abort only if swap-in over the 60 s exceeds 2560 pages (10 MiB). That is
  the same per-block swap flag H26 used. The count is logged every time.
- Swap drift during a level is still caught per arm by `load.py matched` (swap used 256 MiB,
  swap growth 64 MiB per window). The fp16 major-fault flag in `summarize.py` is unchanged.
- No request was sent and no outcome is known. Arms, order, n, gate, scoring, predictions and
  priors are unchanged from the row and A1.

### H27 amendment A3: swap-in is recorded, not a gate (2026-10-17)

Written 2026-10-17T22:25Z. Two more attempts under A2 (22:20Z typical, 22:21Z heavy) also
aborted in the 60 s preflight, before any OVMS container or request. Swap-in was 10880 and
7628 pages (42 and 30 MiB). Across four preflights this host's swap-in ranged from 3 to
10880 pages a minute. It holds about 153 GiB in swap and has 48 GiB MemAvailable. The swap-in
comes from the production containers, which this run must not touch. Any fixed threshold
would either void every run or be tuned after the fact, so A3 drops the gate:
- The preflight logs swap-in over 60 s and never aborts on it. The MemAvailable >= 16 GiB
  abort is unchanged.
- Swap stays a recorded and matched background variable. Per arm, `load.py matched` checks
  swap used (256 MiB) and swap growth (64 MiB per window). Every request records its pswpin
  and pgmajfault deltas, and those are reported per arm. The pre-registered fp16 flag
  (>= 2 requests with > 1000 major faults) is unchanged.
- No request was sent and no outcome is known. Arms, order, n, gate, scoring, predictions and
  priors are unchanged from the row and A1.

### H27 run: matched background load (i9-13900T, 2026-10-17)

```text
# Reproduce
date:            2026-10-17T23:12:24Z (lock taken) .. 23:27:04Z (OVMS and load removed), typical;
                 2026-10-17T22:30:32Z .. 22:55:57Z: NLL pass and the heavy level
hypothesis:      H27 (row + amendments A1, A2, A3)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P 0-15, E 16-31), no amx_* flags, 125 GiB RAM,
                 about 153 GiB in swap, 55 other containers running
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  6feed93 (typical), 7809b99 (NLL, heavy); branch claude/verdict-amx-autoquant-ml; no k3d cluster
runtime:         openvino/model_server@sha256:a928903f…6fc58 (2026.4.1), one container per precision,
                 --cpuset-cpus 0-15, named k3dgpu-i-amx-autoquant-<arm>-<run id>
models:          OpenVINO/Qwen3-4B-int4-ov b467368d, -int8-ov fd9c3cc8, -fp16-ov 00298f1c (HF commit SHAs)
nll:             python@sha256:a6e34c59… + optimum-intel 2.2.0 / openvino 2026.4.1 (pins in probe.sh),
                 INFERENCE_PRECISION_HINT=f32, 4088 held-out + 1022 wikitext2 tokens, 512-token windows
load:            bench/load/load.py, stress-ng 0.22.01 @sha256:2ea9a7c3…1cbe on E-cores 16-31
energy source:   RAPL intel-rapl:0 energy_uj (sudo -A, reads only)
command:         SUDO_ASKPASS=<askpass> LOAD_LEVEL=typical AUTOQUANT_NLL=defer \
                   flock -o -w 14400 "$LOCK" bench/amx-autoquant/probe.sh
                 bench/amx-autoquant/probe.sh --nll <run dir>                  (under the lock)
                 bench/amx-autoquant/probe.sh --summarize <run dir> <nll.json>
runs per arm:    per level 5 reference rounds (R1 ABCD, R2 DCBA, ... R5 ABCD), n = 5 per arm, arms int4 int8
                 fp16 int4'; 3 probe repeats; one 5 s load window per reference request after a 10 s idle gap
```

**Attempts before the scored run, all recorded:**
- 22:17Z and 22:18Z: the zero-swap-in preflight aborted (5 and 3 pages) before any container
  started. That led to A2.
- 22:20Z and 22:21Z: the A2 preflight aborted (10880 and 7628 pages), again before any
  container started. That led to A3.
- 22:30Z typical: the warm-up, P1 and the R1 int4 request ran. Then all three OVMS containers
  got `kill` (exit 137) at 22:33:42Z, 1 s apart in the order int4, int8, fp16. This run's own
  cleanup came 16 s later and removed only the load. The order and the fixed names match
  `probe.sh`'s cleanup in the abort-path test (`tests/autoquant_test.sh`), and other
  workflows' test suites were running on the host at the time. That cause is inferred from
  the docker event log and was not observed directly. Fix (6feed93): container names carry
  a per-run id, cleanup removes only what its own run started, and the abort test runs
  against a stub docker. The NLL pass then ran on that lock hold (160 s, no background).
- 22:36Z heavy: aborted by the watchdog at 1140 s after R4. Requests under `heavy` took
  2.3x longer than the budget plan assumed.

**Quality (NLL pass, deterministic, no background):**

| Arm | held-out NLL | dNLL vs fp16 | wikitext2 dNLL | gate 0.05 |
|-----|--------------|--------------|----------------|-----------|
| fp16 | 3.5421 | 0 | 0 | pass |
| int8 | 3.5388 | -0.0033 | -0.0147 | pass |
| int4 | 3.6124 | **+0.0703** | +0.0623 | **fail** |

**Background load per arm, `typical`** (means of 5 windows of 5 s, no request in flight; limits
from `load.py` TOLERANCE):

| Arm | E-core busy % | 1-min load | package W | throttle/min | swap used MiB | swap growth MiB |
|-----|---------------|------------|-----------|--------------|---------------|-----------------|
| int4 | 49.6 | 15.88 | 97.3 | 2414 | 156686 | -0.18 |
| int8 | 50.3 | 16.13 | 96.1 | 2618 | 156688 | -0.60 |
| fp16 | 49.9 | 16.31 | 95.9 | 2853 | 156688 | -0.40 |
| int4' | 50.0 | 16.75 | 96.8 | 2862 | 156686 | -0.48 |
| spread / limit | 0.7 / 5.0 ok | 0.87 / 2.51 ok | 1.4 / 9.7 ok | 449 / 716 ok | 2.3 / 256 ok | 0.42 / 64 ok |

Load matched: **yes** (`load.py matched`: MATCHED). The background was still running (3 of 3
containers) at the end of the timed part.

**Turn latency, `typical`** (396-token prompt plus 96 tokens, seconds; 95% CI of the median from
10 000 bootstrap resamples; per-request means of the confounds include the request itself):

| Arm | median | min-max | 95% CI | TTFT median | decode tok/s median | package W | pswpin (sum) | pgmajfault (sum, host) |
|-----|--------|---------|--------|-------------|---------------------|-----------|--------------|------------------------|
| int4 | 11.43 | 11.30-11.76 | 11.30-11.76 | 2.79 | 11.01 | 98.9 | 121 | 902 |
| int8 | 16.59 | 16.25-17.18 | 16.25-17.18 | 2.90 | 6.94 | 95.0 | 121 | 1991 |
| fp16 | 32.21 | 31.78-33.70 | 31.78-33.70 | 6.02 | 3.62 | 94.0 | 357 | 1919 |
| int4' | 11.29 | 11.18-11.36 | 11.18-11.36 | 2.74 | 11.07 | 98.2 | 113 | 625 |

- Negative control int4'/int4 per round: 0.990, 1.005, 0.950, 0.983, 0.988, all inside
  [0.9, 1.1]. No O5 gate fired: no swap abort, no fp16 major-fault flag, NLLs differ, and every
  probe took 20-21 s.
- Reference pick (`autoquant.pick`, gate 0.05): **int8**. int4 fails the gate, and int8 beats
  fp16, the other passing arm, in 5/5 rounds.
- Probe picks: P1, P2 and P3 all chose int8 (3/3). Each estimated turn ranked the arms the same
  way as the reference: int4 11.7-12.2 s, int8 16.5-17.4 s, fp16 33.1-34.1 s.

**Scoring against the pre-registered parts:**
- (a) Fidelity: **holds**. The probe's pick equals the reference pick in 3/3 repeats.
- (b) Value: **fails**. The pick (int8, 16.59 s) is 5.16 s *slower* than the static int4
  default (11.43 s), not faster. int4 beat int8 in 5/5 paired rounds (sign test p = 0.031),
  and the gap is 11 times int4's spread (0.46 s). Had int4 passed the gate, the pick would
  have been int4, the default, with delta 0, so (b) fails under either gate outcome.
- (c) Cost: **holds**. Each probe repeat took 20-21 s under `typical` load, inside the 60 s
  budget (heavy: 44-46 s).

**Verdict (ledger, `typical`): REFUTED.** This is outcome O2 (prior 0.25), not the predicted
O1. The verdict matches the prediction for (b), but the predicted mechanism was wrong: the
probe does not pick int4. Instead, the static int4 default fails the pre-registered quality
gate on held-out 2026 text (+0.070 nats/token, about +7% perplexity; +0.062 on wikitext2), so
the probe trades 45% turn time for quality. Derived confidence: high (0.95). The
pre-registered high criteria hold (5/5 round wins, 3/3 probe agreement). (b) fails with no
overlap between the int4 and int8 latency ranges. The one soft point is the single NLL pass,
whose 0.020 margin over the gate has no CI. That margin decides only *which* way (b) fails,
not *whether* it fails.

**`heavy` (robustness, not a verdict): INCONCLUSIVE.** Budget: the watchdog stopped it after
R4 of 5. Load unmatched: throttle_per_min spread 3883 > 2450 (the other five metrics matched;
E-core busy was 99.97-99.99% in every arm). The negative control was also outside
[0.9, 1.1] in R1-R3 (0.87-0.89). Same direction as `typical`: medians int4 26.7 s,
int8 33.2 s, fp16 65.0 s, int4' 24.2 s. The reference pick was int8 (4/4 wins; fp16 flagged
for major faults), and the probe picked int8 in 2/2 repeats.

DO-NOT-RE-ATTACK (b) on non-AMX hybrid clients with these three Qwen3-4B OpenVINO weights:
the probe's choice costs latency there, so it can only be sold as a quality gate. Re-open it
for a host with AMX (the bf16/int8 AMX arms were never run), other weights or calibration, or a
revised quality gate pre-registered first. The int4 quality miss is a separate finding: it
is n=1 per arm on 4088 tokens and needs its own row before anything ships on it.

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

**Amendment A2: matched background load (2026-10-18, before any A2 measurement).**
Written 2026-10-18T13:09Z on `claude/verdict-thermal-resource-ml` and pushed before
the lock is taken. Run 1 (O0) is not re-read. Unchanged: the hypothesis, the null,
the arms B/T/C and their requests, the 24-pod workload, the thermal model and its
10 C margin, the Amendment 1 budget gate (O0), the run order B T C C T B B T C, n = 3
per arm, the 75 s makespan cap, the cooldown rule, the TjMax abort, the decision rule
(non-overlapping 3v3 ranges, permutation p = 0.05, or |Δmedian| > max arm spread),
criteria (a)-(c), the outcome table O0-O6, the priors and the prediction (O0 if the
gate fires, else O3 REFUTED: T throttle <= 0.5x B, T makespan >= 1.10x B, T within
C's spread).

- **Why.** Run 1 was stopped by background draw nobody controlled (idle window
  72-99 W on the shared host). Real Intel boxes are busy, so A2 does not wait for a
  quiet window: every arm runs under the same held background load, the load is
  recorded per arm, and a level is INCONCLUSIVE for load only when that load is
  unmatched across arms. How high it is never voids a run.
- **Load.** `bench/load/load.py` (stress-ng 0.22.01 pinned by digest) on the E-cores
  16-31. `typical` is the headline; `heavy` is the robustness check and runs only if
  the lock hold has time left after `typical`. The level is started 30 s before the
  probe and held through calibration, the gate and all runs. The H23 pods are not
  pinned, so they share the E-cores with the load as well as the package power and
  thermal budget: contention is part of the treatment. The calibration "idle" window
  is therefore the loaded idle, and the budget the model sells is headroom net of the
  neighbour.
- **Load per arm.** The 5 s pre-run package reading becomes a 10 s `load.py` window
  after the cooldown, with no H23 pod running, tagged with the arm (`load.jsonl`):
  E-core busy %, load1, package W, `core_throttle_count` events/min, swap used, swap
  growth. 3 windows per arm.
- **Matching tolerance** (`load.py` TOLERANCE, fixed before this amendment): over the
  B, T and C per-arm means, spread within busy 5 points; load1 max(1.0, 15%); package
  W max(3 W, 10%); throttle/min max(10, 25%); swap used 256 MiB; swap growth 64 MiB.
  Unmatched -> INCONCLUSIVE naming the metric, with the verdict the level would have
  had reported next to it.
- **Void rules.** Under a held level the "pre-run W > idle median + 15 W" and "swap
  growth > 512 MiB" voids are recorded, not applied: the per-arm match covers that
  drift, and those rules would void runs because the load is high. A censored run
  (75 s cap) is still void; O6 (>= 2 void runs, or an arm spread > 25% of its
  median) still applies.
- **Statistics.** Per arm: median and min-max of makespan, `throttle_ms_per_pod`
  (criterion a), net throttle and package W. Derived confidence = 1 - permutation p
  for a decided comparison (0.95 at 3v3 non-overlap), as registered.
- **Timing.** One lock hold; per level at most 20 min of wall time (30 s settle +
  probe budget 1080 s + 60 s watchdog grace). Cluster `k3dgpu-i-thermal-resource`,
  scratch `KUBECONFIG`, created and deleted by the probe inside each level; load
  containers removed before the lock is released.
- **Method fix.** The probe now reads RAPL with `sudo -A` when `SUDO_ASKPASS` is set
  (run 1 needed a `sudo` shim on `PATH`), the same rule as `load.py`.
- **Probe.** `SUDO_ASKPASS=<helper> LOCK=<live lock> bench/thermal-resource/run.sh OUT`
  (LEVELS "typical heavy").

**Expectation for the gate, not a gate.** From the H29 A2 run on this host, `typical`
holds the package at about 93 W and 90-96 C before any work; run 1's fit put idle at
79 C. With the loaded idle within 10 C of T_tcc (95 C) the model's threshold P_th
falls to or below the loaded idle and the budget to 0-10 W, so O0 (budget gate) is
expected again at both levels (prior 0.85, unchanged). If it fires, that is the
pre-registered O0, not a load-matching failure: under a busy box the model has no
headroom to sell, and B/T/C are not run.

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

### H23 run 2: matched background load (i9-13900T, 2026-10-18)

Run under [Amendment A2](#h23-pre-registration-2026-10-17) (matched background load,
pushed in `bd4e800` at 13:10Z, before the lock). One lock hold, `typical` then `heavy`.

```text
# Reproduce
date:            2026-10-18T13:22:37Z (lock taken) .. 13:29:00Z (load stopped, lock released; 383 s held)
                 typical 13:22:38Z-13:25:46Z; heavy 13:25:47Z-13:29:00Z
hypothesis:      H23 (pre-registration + Amendments 1 and A2)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P 0-15, E 16-31); other tenants running, recorded not removed
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  bd4e800 (claude/verdict-thermal-resource-ml)
host limits:     PL1 = PL2 = 117 W, tau 56 s, TjMax 100 C, TCC offset 5 C (int340x) -> T_tcc 95 C;
                 governor powersave, EPP performance (read only)
load:            bench/load/load.py on CPUs 16-31 (stress-ng pinned by digest), started 30 s before the probe,
                 held through calibration and the gate, stopped after each level
workload:        stress-ng image pinned in research/tseries-envelope/README.md (calibration containers h23-cal-*)
energy source:   /sys/class/powercap/intel-rapl:0 (package-0), sudo -A
cluster:         none created: the first budget gate fired at both levels (k3dgpu-i-thermal-resource never existed)
raw results:     bench/thermal-resource/results/2026-10-18-a2/ (fit.csv sha256 typical e5c12627..., heavy 6269c784...)
command:         SUDO_ASKPASS=<helper> LOCK=<live lock> bench/thermal-resource/run.sh OUT   # LEVELS "typical heavy"
```

**Verdict: INCONCLUSIVE (O0, degenerate budget) at both levels**, applied mechanically by
the Amendment 1 gate before any arm: `watts 0 < 2 x max pod watts`. Arms B/T/C ran 0 of 9
at each level, so criteria (a)-(c) have n = 0 and there are no per-arm load windows to
match. The cause is the gate, not the load match: the held load is what the model must
sell headroom net of, and on this host it leaves none.

| Quantity | typical (headline) | heavy (robustness) |
|---|---|---|
| Loaded idle window (30 x 1 s) | median 96.8 W (94.0-103.0), package 82-95 C (median 85) | median 101.2 W (98.5-106.6), 85-90 C (median 87) |
| Background throttle in idle window | 1404 ms of 30 s (4.7%) | 147 ms of 30 s (0.5%) |
| Hot window (8 x matrixprod, last 30 s) | 111.3 W, 84-93 C | 118.9 W, 88-94 C |
| Cool window (8 x 40% load, last 30 s) | 106.0 W, 85-95 C | 114.2 W, 86-96 C |
| Fit (n = 90) | R_th 0.350 C/W, T_idle 85.0 C at 94.0 W, max residual 9.3 C | R_th 0.255 C/W, T_idle 87.0 C at 98.5 W, max residual 5.2 C |
| Threshold P_th (binding: thermal) | 94.0 + (85 - 85.0) / 0.350 = 94.0 W | 98.5 + (85 - 87.0) / 0.255 = 90.5 W |
| Per-pod watts | w_hot 2, w_cool 2 | w_hot 3, w_cool 2 |
| Budget | floor(94.0 - 96.8) -> 0 W, 0 slots | floor(90.5 - 101.2) -> 0 W, 0 slots |

**Confidence** that the gate fires on this host under a held load: the bootstrap of
the idle median (20,000 resamples, fit fixed) puts the raw budget at -3.5 to -1.7 W
(typical) and -11.4 to -9.9 W (heavy), 95%; refitting the thermal model on resampled
points as well (5,000 resamples) gives -7.1 to -0.1 W and -14.6 to -7.9 W. 0 resamples
reach the 16 W (4 slots) the gate needs: confidence > 0.999 in O0. Confidence in H23's
claim itself: none either way (no arm ran).

**Against the prediction.** A2 expected O0 at both levels with a budget of 0-10 W, from
a loaded idle within 10 C of T_tcc. Observed: loaded idle 85.0 C (typical) and 87.0 C
(heavy), 8-10 C below T_tcc, budget 0 W. The prediction held. The pre-registered O0
action now applies ("DO-NOT-RE-ATTACK on this host while idle is within 10 C of T_tcc"):
in run 1 the condition did not hold (79.3 C), under a held load it does. A busy
i9-13900T at PL1 = PL2 = 117 W with a 35 W TDP cooler idles at the thermal margin, so
a headroom budget with a 10 C margin has nothing to admit; the scheduler would admit 0
pods in arm T and C.

**DO-NOT-RE-ATTACK on this host** (pre-registered O0 action). Missing probe: the B/T/C
series on a host whose loaded idle (`load.py` `typical` held) sits >= 20 C below T_tcc,
checked read-only before booking the lock.

### H30-H32 amendment A1: matched background load (2026-10-18)

Written 2026-10-18T05:06Z and pushed before any H30, H31 or H32 request. No competitor
container has served a request and no outcome is known. The rows' claims, arms, prompts,
max_tokens, margin, rounds, warmups, budget, outcome table and predictions are unchanged.
A1 adds the background-load design every probe on this shared host now runs under, and
fixes one weights pin that could never pass.

- **Host and load.** The shared i9-13900T. `bench/load/load.py start --level typical`
  holds the E-cores (16-31) for the whole timed part of each row (headline). `heavy`
  runs only if a later lock hold has time left; it is reported as robustness and is not
  the verdict. Other tenants keep running and are recorded, not removed.
- **Load per arm.** `bench/vs.py` (env `VS_LOAD_WINDOW_S=10`, `VS_LOAD_OUT`) samples one
  10 s `load.py` window right before every timed segment, with no request in flight,
  tagged with the arm that segment runs. That gives 3 windows per arm per row.
  `load.py matched` decides with its fixed TOLERANCE (busy 5 pp; 1-min load 1.0 or 15%;
  package W 3 or 10%; throttle/min 10 or 25%; swap used 256 MiB; swap growth 64 MiB).
  Unmatched load makes the row INCONCLUSIVE whatever the ratios say, and the verdict it
  would have had is reported next to it.
- **n.** As registered: 3 interleaved rounds (A,B / B,A / A,B), 4 prompts x 128 tokens
  per arm segment, so 12 timed requests and 3 segment medians per arm. 3 warmups per arm.
- **Statistics, fixed now.** Per arm, the median decode tok/s of the 12 requests with a
  95% CI from 10 000 bootstrap resamples; the k3d-gpu/competitor ratio of those medians
  with a 95% bootstrap CI (resampling requests within each arm). Derived confidence =
  min(P_boot, 1 - p_sign), where P_boot is the bootstrap share of ratios on the verdict's
  side of the margin (>= 1.05 for SUPPORTED, <= 1/1.05 for REFUTED, inside for
  INCONCLUSIVE) and p_sign is the one-sided sign test over the 3 rounds (3/3 gives
  0.875 at most). The verdict itself is still the registered per-round rule.
- **One cluster per row.** `k3dgpu-q-h30`, `k3dgpu-q-h31`, `k3dgpu-q-h32`, each created
  by `k3d-gpu up` + `k3d-gpu agent up` with default settings inside its own lock hold,
  scratch `KUBECONFIG` (never `~/.kube/config`), and deleted with the load stopped before
  the lock is released. One competitor per lock hold, at most 20 min of wall time per
  hold. The device and backend `agent up` picks are recorded. Competitor weights are
  prefetched into bench's cache volume before the lock (download only, hash checked);
  bench still hashes the served file before it measures.
- **Ollama pin fix.** `competitors.lock` pinned the sha256 of Ollama's on-disk manifest
  copy. Ollama 0.40.5 rewrites that copy (it appends the registry manifest's own digest
  as an extra layer entry), so the file hashes to 2506fc96…, not to the registry digest
  2bfd38a7…, and bench would refuse every run. The registry still serves manifest
  2bfd38a7 (checked 2026-10-18 with `curl registry.ollama.ai/v2/library/qwen3/manifests/4b-q4_K_M`),
  and its model layer is sha256 163553ae…. The pin is now that model-layer blob
  (`/root/.ollama/models/blobs/sha256-163553ae…`, content-addressed, rehashed and equal).
  Same weights, a file that can be checked.
- **Predictions, unchanged.** H30: SUPPORTED where `agent up` picks an Intel GPU,
  REFUTED or INCONCLUSIVE on CPU. H31: same. H32: INCONCLUSIVE (same engine, weights and
  device on a GPU host).

### H30-H32 run: matched background load (i9-13900T, 2026-10-18)

Run under [amendment A1](#h30-h32-amendment-a1-matched-background-load-2026-10-18), level
`typical` only. `heavy` was not run. One lock hold per competitor, each with its own fresh
cluster, deleted with the load stopped before release.

```text
# Reproduce
date:            H30 2026-10-18T05:48:01Z (lock) .. 05:59:31Z (released, 690 s)
                 H31 2026-10-18T06:08:23Z .. 06:20:28Z (725 s)
                 H32 2026-10-18T06:44:33Z .. 06:56:26Z (713 s)
                 amendment A1 pushed 05:08Z (2929261), before any request
hypothesis:      H30, H31, H32 (rows + amendment A1)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P 0-15, E 16-31), UHD 770 iGPU, 125 GiB RAM,
                 about 145-148 GiB in swap, 52 other containers running at each start
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  8c9f81c (branch claude/verdict-h30-h32-ml; main a326f59 + A1 + bench/vs.py load windows)
clusters:        k3dgpu-q-h30, k3dgpu-q-h31, k3dgpu-q-h32; scratch KUBECONFIG; node image
                 cryptoandcoffee/k3d-gpu:0.1.55; `k3d-gpu up` 38-50 s, `k3d-gpu agent up` 127-149 s
k3d-gpu arm:     `agent up` defaults picked OpenVINO/Qwen3-4B-int4-ov b467368d on CPU, P-cores,
                 16 CPUs (OVMS 2026.4.1), not the iGPU; reached through kubectl port-forward
competitors:     H30 ollama/ollama:0.40.5@sha256:5d3a2f25…8241, served model layer sha256 163553ae… (checked)
                 H31 ghcr.io/ggml-org/llama.cpp:server-b11706@sha256:f41d47ee…a485, gguf sha256 7485fe6f… (checked)
                 H32 openvino/model_server:2026.4.1-gpu@sha256:a928903f…6fc58 with /dev/dri, bin sha256 074214fa… (checked)
                 all with vendor defaults: no --cpuset, so they could use all 32 CPUs, E-cores included
load:            bench/load/load.py typical, stress-ng 0.22.01 @sha256:2ea9a7c3…1cbe on E-cores 16-31;
                 one 10 s window before every timed segment, no request in flight, tagged per arm
energy source:   RAPL intel-rapl:0 energy_uj (sudo -A, reads only)
command:         SUDO_ASKPASS=<askpass> VS_LOAD_WINDOW_S=10 VS_LOAD_OUT=load.jsonl VS_LOAD_LEVEL=typical \
                   k3d-gpu bench --vs <stack> --id <Hn> --budget 900 --out bench-<Hn>
                 bench/vs_matched.py bench-<Hn>/result.json load.jsonl
runs per arm:    3 warmups + 3 interleaved rounds x 4 prompts x 128 tokens: n = 12 requests, 3 load windows
raw results:     result.json sha256 H30 42bc0ae9…9a0c, H31 8da4ad8d…6bfc, H32 6d76e996…22f3
```

**Decode speed** (median of 12 requests; 95% CI from 10 000 bootstrap resamples; ratio =
k3d-gpu/competitor of those medians, requests resampled within each arm):

| Row | k3d-gpu tok/s (CI) | competitor tok/s (CI) | ratio (CI) | per-round ratios | J/token k3d-gpu / competitor | TTFT s k3d-gpu / competitor |
|-----|--------------------|-----------------------|------------|------------------|------------------------------|-----------------------------|
| H30 ollama | 10.96 (10.86-11.18) | 7.05 (6.95-7.14) | 1.55 (1.53-1.59) | 1.55, 1.58, 1.57 | 8.52 / 14.35 | 0.33 / 0.89 |
| H31 llamacpp | 11.20 (11.07-11.33) | 8.21 (7.99-8.29) | 1.36 (1.34-1.41) | 1.37, 1.35, 1.40 | 8.46 / 11.59 | 0.29 / 0.69 |
| H32 ovms | 11.11 (10.43-11.44) | 6.50 (6.38-6.72) | 1.71 (1.60-1.77) | 1.73, 1.68, 1.65 | 8.52 / 15.15 | 0.30 / 0.98 |

Every request in every arm returned 128 tokens counted from `usage`, with no failures and
3/3 warmups ok, so no outcome-table exclusion fired. Every run ended inside the budget
(479-544 s of about 886 s).

**Background load per arm** (means of 3 windows of 10 s; limits from `load.py` TOLERANCE):

| Row | arm | E-core busy % | 1-min load | package W | throttle/min | swap used MiB | swap growth MiB |
|-----|-----|---------------|------------|-----------|--------------|---------------|-----------------|
| H30 | k3d-gpu | 53.2 | 19.08 | 93.1 | 351 | 147746 | -1.3 |
| H30 | ollama | 56.8 | 19.30 | 93.5 | 347 | 147745 | -0.4 |
| H30 | spread / limit | 3.6 / 5.0 ok | 0.22 / 2.90 ok | 0.4 / 9.3 ok | 4 / 88 ok | 0.9 / 256 ok | 0.9 / 64 ok |
| H31 | k3d-gpu | 51.5 | 17.65 | 93.4 | 383 | 147110 | -0.7 |
| H31 | llamacpp | 50.8 | 18.77 | 93.6 | 567 | 147108 | -0.9 |
| H31 | spread / limit | 0.7 / 5.0 ok | 1.12 / 2.82 ok | 0.3 / 9.4 ok | **184 / 142 fail** | 2.0 / 256 ok | 0.2 / 64 ok |
| H32 | k3d-gpu | 55.7 | 13.76 | 90.2 | 116 | 145395 | -51.4 |
| H32 | ovms | 55.8 | 16.50 | 93.2 | 339 | 145306 | -1.1 |
| H32 | spread / limit | 0.1 / 5.0 ok | **2.75 / 2.48 fail** | 3.0 / 9.3 ok | **224 / 85 fail** | 89 / 256 ok | 50 / 64 ok |

Load matched: H30 **yes**, H31 **no** (throttle events), H32 **no** (1-min load and
throttle events). In both unmatched rows the competitor's windows carried more throttling
and load. The windows are taken with no request in flight, but each competitor window
follows the competitor's previous segment or warmup, and the competitors run on all 32
CPUs by default, E-cores included. That is likely carry-over from the arm itself rather
than drift in the background. This is inferred; the run cannot separate the two.

**Derived confidence** (A1: min(P_boot, 1 - p_sign)): P_boot = 1.00 in all three rows (no
bootstrap ratio fell below 1.05), and 3/3 rounds on one side give p_sign = 0.125, so the
confidence in the measured direction is 0.875 for each row. That is the ceiling 3 rounds
allow.

**Verdicts:**
- **H30: SUPPORTED** (load matched). k3d-gpu decoded 1.55x Ollama's rate in every round,
  CI 1.53-1.59, at 0.59x Ollama's package J/token. Derived confidence 0.875. The
  hypothesis holds, but the prediction's mechanism was wrong. `agent up` picked the CPU
  (16 P-cores), not the iGPU, and the registered prediction for a CPU pick was "REFUTED or
  INCONCLUSIVE". The gap comes from configuration on the same CPU, not from a GPU.
- **H31: INCONCLUSIVE** (load unmatched on throttle events). If matched it would be
  SUPPORTED: 1.36x, CI 1.34-1.41, 3/3 rounds, confidence 0.875 in that direction. The
  prediction for a CPU pick ("REFUTED or INCONCLUSIVE") would again have been wrong
  about the measured direction.
- **H32: INCONCLUSIVE** (load unmatched on 1-min load and throttle events). If matched it
  would be SUPPORTED: 1.71x, CI 1.60-1.77, 3/3 rounds, confidence 0.875 in that
  direction. The registered prediction was INCONCLUSIVE because the gap would sit inside
  the margin. The measured gap is far outside it. The premise "same device" did not hold:
  the k3d-gpu arm ran on CPU. Which device plain OVMS chose with `/dev/dri` passed was not
  logged (its container is removed at the end of the run). That is a gap in this run.

No row is REFUTED, so no DO-NOT-RE-ATTACK applies. Re-attack H31 and H32 with a new
amendment written before the run. It should fix the carry-over: a longer idle gap before
each window, or windows that skip the first seconds after a segment. It should also log
the OVMS competitor's device (`docker logs` before removal), and keep the competitors'
vendor defaults.

Confounds: one shared host (52 other containers, about 146 GiB in swap), the `typical`
load on the E-cores, n = 12 requests per arm (3 rounds), and a different cluster and
model pod per row. The k3d-gpu arm also went through `kubectl port-forward`, which biases
against k3d-gpu. Ollama counts Qwen3 thinking tokens as output, and both arms reached 128
tokens.

docs/benchmarks.md was rendered by `bench/vs.py docs` from the three result files with
H31 and H32 set to their ledger verdict (INCONCLUSIVE, load unmatched).

```bash
# Reproduce one row (H30 shown) from this commit. Host: docker, k3d, kubectl, RAPL via sudo.
lscpu | grep 'Model name'; uname -r; git rev-parse HEAD
export KUBECONFIG=$PWD/h30.kubeconfig K3D_GPU_CLUSTER=k3dgpu-q-h30
scripts/k3d-gpu up && scripts/k3d-gpu agent up        # logs the device it picks
bench/load/load.py start --level typical --ttl 1500; sleep 20
SUDO_ASKPASS=<askpass> VS_LOAD_WINDOW_S=10 VS_LOAD_OUT=load.jsonl VS_LOAD_LEVEL=typical \
  scripts/k3d-gpu bench --vs ollama --id H30 --budget 900 --out bench-H30
bench/vs_matched.py bench-H30/result.json load.jsonl   # medians, CIs, load_matched, confidence
bench/load/load.py stop; scripts/k3d-gpu down
```

### H19 amendment A1: matched background load (2026-10-18)

Written 2026-10-18T07:18Z and pushed before any H19 trial. No H19 tune or replication
trial has run since the 2026-10-17 pilot, and no outcome is known. The row's hypothesis,
predictions and outcome table are unchanged. A1 changes the design so the row fits one
20-minute lock hold on the shared host and runs under the same matched background load
as every other probe here.

- **What changed since registration, and why.** The registered command
  (`tune run` with n0 = 27, three rungs, then n = 5 confirmation runs per side at
  16 x 128 tokens) needs well over an hour of redeploys. The shipping `k3d-gpu tune run`
  is now the budget-bounded incremental search (one short screening trial per one-knob
  neighbour of the default, then a confirmation only when it fits the budget, with the
  same gates). A1 tests that shipping command. Part (b) of the prediction (the explicit
  default is never accepted) cannot fire in this mode: the incremental search never
  screens the incumbent's own explicit restatement. It is recorded as not tested.
- **Host and load.** The shared i9-13900T. `bench/load/load.py start --level typical` on
  the E-cores (16-31) from before the tune to the end of the replication (headline).
  `heavy` runs only if a later hold has time left, as robustness, not the verdict. Other
  tenants keep running and are recorded, not removed. Confounds every 5 s for the whole
  hold (`load.py sample --seconds 5`, tag `run`).
- **Tune.** One fresh cluster `k3dgpu-q-h19` (`k3d-gpu up` + `k3d-gpu agent up`, defaults,
  launcher's pinned node image), scratch `KUBECONFIG` and tune dir, then
  `k3d-gpu tune run --objective throughput --devices CPU --precisions int4 --budget 4`
  with the default criteria (margin 5%, k = 1, alpha 0.05, TTFT guardrail 25%, quality
  probe, `--unique-prompts`).
- **Candidate.** The tune's winner when it crowns one (decision `tuned`, "crowned");
  otherwise its pending challenger; otherwise the screened neighbour with the best
  screening score (`bench/tune-noise/analyze.py candidate`). No screened neighbour at
  all -> INCONCLUSIVE.
- **Replication, n.** Fresh trials of the default (A) and the candidate (B) in the order
  A B B A A B (the rotation `tune.py` confirms in), n = 3 per side, each a
  `k3d-gpu tune _trial` at the screening budget (4 requests x 64 tokens at concurrency
  1, 4, 8, cold start every trial). One 10 s `load.py` window right before every trial,
  no request in flight, tagged with that trial's arm. No trial starts later than 930 s
  after the hold's start, so the hold ends inside 20 min. Fewer than 3 trials per side ->
  INCONCLUSIVE.
- **Gates and statistics, fixed now.** The replication is replayed through `tune.py`'s own
  `_confirm` with n = 3 (gain >= 5%, separation >= 1 spread, one-sided Mann-Whitney
  p <= 0.05, TTFT and quality guardrails); with 3 against 3 the smallest possible p is
  exactly 0.05. Per arm the median aggregate tok/s with a 95% CI from 10 000 bootstrap
  resamples, and the B/A ratio of medians with its 95% CI. Derived confidence = P_boot,
  the bootstrap share of ratios on the verdict's side of 1.05 (below it when the
  replication fails the gates), and min(P_boot, 1 - p) when it passes.
- **Tolerance.** `load.py matched` over the A and B windows with its fixed TOLERANCE
  (busy 5 pp; 1-min load 1.0 or 15%; package W 3 or 10%; throttle/min 10 or 25%; swap used
  256 MiB; swap growth 64 MiB). Unmatched load makes the row INCONCLUSIVE whatever the
  gates say, and the verdict it would have had is reported next to it.
- **Outcome table, unchanged.** Crowned and the replication fails the gates -> REFUTED;
  crowned and it passes -> SUPPORTED; not crowned and the replication fails -> SUPPORTED;
  not crowned but it passes every gate -> INCONCLUSIVE; the tune or the default trial
  fails -> INCONCLUSIVE.
- **Prediction, unchanged.** Decision `default` (single-stream decode is memory-bound
  and the default already takes all 16 P-core CPUs), and the replication shows no gain
  beyond the gates: SUPPORTED.
- **Probe.** `bench/tune-noise/run.sh OUT typical` (one hold, cleans up on exit), then
  `bench/tune-noise/analyze.py verdict OUT`.

### H19 run: matched background load (i9-13900T, 2026-10-18)

Run under [amendment A1](#h19-amendment-a1-matched-background-load-2026-10-18), level
`typical` only. `heavy` was not run. One lock hold, one fresh cluster, deleted with the
load stopped before release.

```text
# Reproduce
date:            2026-10-18T10:01:09Z (lock) .. 10:14:07Z (released, 778 s)
                 amendment A1 pushed 07:18Z (a847ba4), before any trial
hypothesis:      H19 (row + amendment A1)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P 0-15, E 16-31), UHD 770 iGPU, 125 GiB RAM,
                 about 148 GiB in swap, other tenants running (not removed)
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  ec6bb76 (branch claude/verdict-h19-ml; main 209809a + A1 + run.sh TERM cleanup)
cluster:         k3dgpu-q-h19, scratch KUBECONFIG and tune dir, node image cryptoandcoffee/k3d-gpu:0.1.57;
                 `k3d-gpu up` 59 s, `k3d-gpu agent up` 76 s
agent:           `agent up` defaults: OpenVINO/Qwen3-4B-int4-ov b467368d on CPU, P-cores, 16 CPUs
                 (OVMS 2026.4.1), Fast Draft Qwen3-0.6B-int4-ov on CPU
load:            bench/load/load.py typical on E-cores 16-31 (3 containers), started before the tune,
                 stopped at cleanup; 5 s confounds for the whole hold; one 10 s window per trial, tagged
command:         SUDO_ASKPASS=<askpass> bench/tune-noise/run.sh OUT typical
                   (k3d-gpu tune run --objective throughput --devices CPU --precisions int4 --budget 4)
                 bench/tune-noise/analyze.py verdict OUT
trials:          tune 168 s; replication A B B A A B, each `tune _trial` at 4 requests x 64 tokens,
                 concurrency 1,4,8, cold; 6/6 ok; last trial started at +704 s (cap 930 s)
raw results:     profile.json sha256 e2441210…, load.jsonl 4c6fb4a0…, result.json rep-01..06
                 a38648a6…, b28e1da3…, be493ae4…, fb269491…, a2775ad6…, 01068087…
```

**Tune.** The budget of 4 min fit screening only ("about 19 min" to also confirm). It
screened 1 of 10 one-knob neighbours: `CORES=efficient` scored 19.54 aggregate tok/s
against the default's 24.06, so no neighbour beat it by 5%, and the decision was
`incumbent` (the default, 2 trials). Nothing was crowned. Part (b) of the prediction was
not tested (A1).

**Replication** (candidate = that screened neighbour, the only one; aggregate tok/s per
trial; 95% CI from 10 000 bootstrap resamples):

| Arm | trials (in run order) | median (CI) | TTFT median s |
|-----|-----------------------|-------------|---------------|
| A default (P-cores) | 17.26, 23.72, 35.72 | 23.72 (17.26-35.72) | 0.45 |
| B `CORES=efficient` | 31.39, 25.92, 32.32 | 31.39 (25.92-32.32) | 0.55 |

B/A ratio of medians 1.32, CI 0.73-1.87. `tune.py` `_confirm` replay with n = 3: gain
+32% passes the 5% margin, but separation 0.42 < k = 1 (inside the run-to-run spread)
and Mann-Whitney p = 0.35 > 0.05, so the gates fail. The default arm rose 17 -> 24 -> 36
across the hold; that within-arm drift is wider than the gap between the arms.

**Background load per arm** (means of 3 windows of 10 s; limits from `load.py` TOLERANCE):

| arm | E-core busy % | 1-min load | package W | throttle/min | swap used MiB | swap growth MiB |
|-----|---------------|------------|-----------|--------------|---------------|-----------------|
| A default | 48.6 | 16.79 | 95.3 | 2023 | 148036 | -0.2 |
| B efficient | 48.4 | 16.51 | 93.8 | 2359 | 148033 | -2.0 |
| spread / limit | 0.3 / 5.0 ok | 0.28 / 2.52 ok | 1.5 / 9.5 ok | 336 / 590 ok | 3 / 256 ok | 1.8 / 64 ok |

Load matched: **yes**.

**Derived confidence** (A1: P_boot, the bootstrap share of ratios below 1.05 since the
gates failed): 0.25. Three-quarters of the bootstrap ratios sit above 1.05, so the
replication's point estimate favours the E-core neighbour; n = 3 has no power to tell.

**Verdict: SUPPORTED (confidence 0.25).** Outcome-table cell "decision `default` and the
replicated finalist also fails the gates". The tune did not crown noise, and kept the
default. The claim is narrow: `tune run --budget 4` on this host searched 1 of 10
neighbours and kept the default. It does not show the default is the best config, and
it does not show `tune run` beats the defaults (it changed nothing). Whether the E-core
config is faster under E-core load is open; the replication's CI spans 0.73-1.87.

### H31-H32 amendment A2: idle gap before each load window (2026-10-18)

Written 2026-10-18T10:32Z and pushed 10:33Z (48f55c5), before any A2 request. The A1 run left H31 and H32
INCONCLUSIVE on unmatched load, and the competitor's windows carried more throttling
and 1-min load, likely carry-over from the competitor's own previous segment. A2
re-runs H31 and H32 only (H30 is SUPPORTED under matched load and is not re-run). The
rows' claims, arms, prompts, max_tokens, margin, rounds, warmups, budget, outcome table
and predictions are unchanged. Everything in A1 holds except what is listed here.

- **Settle gap.** `bench/vs.py` reads `VS_LOAD_SETTLE_S`: before each load window the run
  idles that many seconds with no request in flight, then samples the 10 s window. A2
  uses `VS_LOAD_SETTLE_S=45` (the 1-min load average decays to about 0.47 of a step in
  45 s). The gap counts against the 900 s budget; the A1 runs used 479-544 s, and six
  gaps add 270 s. Each window records its `settle_s`.
- **Competitor device logged.** `k3d-gpu bench` writes the competitor container's log to
  `<out>/competitor.log` before it removes the container, so the device plain OVMS picks
  with `/dev/dri` passed is on record (it was missing in A1).
- **Load and matching.** Headline level `typical` (E-cores 16-31), `heavy` only if a
  later lock hold has time left, reported as robustness, not the verdict. Matched means
  `load.py matched` with its fixed TOLERANCE (busy 5 pp; 1-min load 1.0 or 15%; package W
  3 or 10%; throttle/min 10 or 25%; swap used 256 MiB; swap growth 64 MiB). Unmatched
  load makes the row INCONCLUSIVE whatever the ratios say, with the would-be verdict
  reported next to it.
- **n and statistics.** As in A1: 3 interleaved rounds x 4 prompts x 128 tokens, so 12
  timed requests and 3 load windows per arm, 3 warmups per arm; medians with 95%
  bootstrap CIs (10 000 resamples), ratio CI, derived confidence min(P_boot, 1 - p_sign).
- **Clusters and lock.** `k3dgpu-q-h31`, `k3dgpu-q-h32`, fresh `k3d-gpu up` + `agent up`
  with defaults per lock hold, scratch `KUBECONFIG`, one competitor per hold, at most 20
  min wall time per hold, cluster deleted with the load stopped before release.
  Competitor weights prefetched (download only, hash checked) before the lock.
- **Predictions, unchanged from registration.** H31: SUPPORTED where `agent up` picks an
  Intel GPU, REFUTED or INCONCLUSIVE on a CPU-only pick. H32: INCONCLUSIVE (same engine,
  weights and device on a GPU host).

### H31-H32 run A2: idle gap before each load window (i9-13900T, 2026-10-18)

Run under [amendment A2](#h31-h32-amendment-a2-idle-gap-before-each-load-window-2026-10-18),
level `typical` only (`heavy` was not run: it is robustness only and cannot change a
verdict). One lock hold per competitor, each with its own fresh cluster, deleted with the
load stopped before release. This run supersedes the A1 numbers for H31 and H32; H30 keeps
its A1 verdict.

```text
# Reproduce
date:            H31 2026-10-18T10:59:41Z (lock) .. 11:13:59Z (released, 858 s)
                 H32 2026-10-18T11:35:50Z .. 11:49:39Z (829 s)
                 amendment A2 pushed 10:33Z (48f55c5), before any A2 request
hypothesis:      H31, H32 (rows + amendments A1, A2)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P 0-15, E 16-31), UHD 770 iGPU, 125 GiB RAM,
                 about 147 GiB in swap, 52 other containers running at each start
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  82b7457 (branch claude/verdict-h31-h32-a2; main 5dae99b + A2)
clusters:        k3dgpu-q-h31, k3dgpu-q-h32; scratch KUBECONFIG; `k3d-gpu up` 57-60 s, `agent up` 141-143 s
k3d-gpu arm:     `agent up` defaults picked OpenVINO/Qwen3-4B-int4-ov on CPU, P-cores, 16 CPUs, with CPU
                 Fast Draft OpenVINO/Qwen3-0.6B-int4-ov (default since H34); not the iGPU;
                 reached through kubectl port-forward
competitors:     H31 ghcr.io/ggml-org/llama.cpp:server-b11706@sha256:f41d47ee…a485, gguf sha256 7485fe6f… (checked), CPU
                 H32 openvino/model_server:2026.4.1-gpu@sha256:a928903f…6fc58 with /dev/dri, bin sha256 074214fa… (checked);
                 competitor.log: "Integrated GPU found, recommending: GPU", "No device specified, using recommended device: GPU"
                 both with vendor defaults: no --cpuset
load:            bench/load/load.py typical on E-cores 16-31; before every timed segment a 45 s idle settle,
                 then one 10 s window, no request in flight, tagged per arm (settle_s recorded per window)
energy source:   RAPL intel-rapl:0 energy_uj (sudo -A, reads only)
command:         SUDO_ASKPASS=<askpass> VS_LOAD_WINDOW_S=10 VS_LOAD_SETTLE_S=45 VS_LOAD_OUT=load.jsonl \
                   VS_LOAD_LEVEL=typical k3d-gpu bench --vs <stack> --id <Hn> --budget 900 --keep-cache --out bench-<Hn>
                 bench/vs_matched.py bench-<Hn>/result.json load.jsonl
runs per arm:    3 warmups + 3 interleaved rounds x 4 prompts x 128 tokens: n = 12 requests, 3 load windows
raw results:     result.json sha256 H31 659f4892…3cdf, H32 11f51202…5684
```

**Decode speed** (median of 12 requests; 95% CI from 10 000 bootstrap resamples; ratio =
k3d-gpu/competitor of those medians, requests resampled within each arm):

| Row | k3d-gpu tok/s (CI) | competitor tok/s (CI) | ratio (CI) | per-round ratios | J/token k3d-gpu / competitor | TTFT s k3d-gpu / competitor |
|-----|--------------------|-----------------------|------------|------------------|------------------------------|-----------------------------|
| H31 llamacpp | 15.60 (14.95-18.78) | 10.31 (10.08-10.38) | 1.51 (1.45-1.84) | 1.52, 1.54, 1.50 | 6.06 / 9.28 | 0.38 / 0.62 |
| H32 ovms | 16.23 (15.54-19.42) | 10.24 (10.21-10.25) | 1.58 (1.52-1.90) | 1.61, 1.58, 1.59 | 5.92 / 9.70 | 0.36 / 0.55 |

Every request in both arms returned 128 tokens counted from `usage`, with no failures and
3/3 warmups ok, so no outcome-table exclusion fired. Both runs ended inside the budget
(646 s of 854 s, 642 s of 880 s).

**Background load per arm** (means of 3 windows of 10 s, each after a 45 s settle; limits
from `load.py` TOLERANCE):

| Row | arm | E-core busy % | 1-min load | package W | throttle/min | swap used MiB | swap growth MiB |
|-----|-----|---------------|------------|-----------|--------------|---------------|-----------------|
| H31 | k3d-gpu | 51.3 | 12.11 | 89.6 | 212 | 147523 | -0.4 |
| H31 | llamacpp | 50.3 | 12.00 | 89.0 | 216 | 147521 | -0.2 |
| H31 | spread / limit | 1.0 / 5.0 ok | 0.11 / 1.82 ok | 0.7 / 9.0 ok | 4 / 54 ok | 1.2 / 256 ok | 0.2 / 64 ok |
| H32 | k3d-gpu | 44.0 | 8.56 | 91.2 | 443 | 147403 | -0.2 |
| H32 | ovms | 44.7 | 9.47 | 92.1 | 591 | 147367 | -0.2 |
| H32 | spread / limit | 0.6 / 5.0 ok | 0.90 / 1.42 ok | 0.9 / 9.2 ok | **147.9 / 147.7 fail** | 36 / 256 ok | 0.0 / 64 ok |

Load matched: H31 **yes**, H32 **no** (throttle events, over the limit by 0.2/min). The
settle gap closed the 1-min load gap that failed H32 under A1 (2.75 then, 0.90 now) and the
throttle gap that failed H31. H32's competitor windows still carried more throttling. Plain
OVMS ran on the iGPU, which shares the package power and thermal budget with the cores, and
two of its three windows (850 and 707/min) sit above every k3d-gpu window (at most 563/min). Whether that is carry-over from the iGPU
or background drift, this run cannot separate.

**Derived confidence** (A1: min(P_boot, 1 - p_sign)): P_boot = 1.00 in both rows (no
bootstrap ratio fell below 1.05), and 3/3 rounds give p_sign = 0.125, so 0.875 in the
measured direction, the ceiling 3 rounds allow.

**Verdicts:**
- **H31: SUPPORTED** (load matched). k3d-gpu decoded 1.51x llama.cpp's rate in every round,
  CI 1.45-1.84, at 0.65x llama.cpp's package J/token. Derived confidence 0.875. The
  registered prediction for a CPU pick ("REFUTED or INCONCLUSIVE") was wrong: `agent up`
  picked the CPU and still won. The k3d-gpu arm includes CPU Fast Draft (speculative
  decoding with Qwen3-0.6B), which `agent up` turns on by default since H34; that is part
  of the auto-config the row tests, and the A1 run without it (1.36x) was also above the
  margin.
- **H32: INCONCLUSIVE** (load unmatched on throttle events, spread 147.9 against a limit of
  147.7). If matched it would be SUPPORTED: 1.58x, CI 1.52-1.90, 3/3 rounds, confidence
  0.875 in that direction. The registered prediction (INCONCLUSIVE, gap inside the margin)
  rested on "same device"; that premise failed again in the other direction: plain OVMS
  picked the iGPU (logged this time), and k3d-gpu's auto-config picked the CPU with Fast
  Draft and decoded 1.58x faster.

No row is REFUTED, so no DO-NOT-RE-ATTACK applies. Re-attack H32 only with a new amendment
written before the run that addresses the iGPU competitor's throttle carry-over (for
example more windows per arm, or windows taken in a separate quiet slot between segments);
the competitor keeps its vendor defaults.

Confounds: one shared host (52 other containers, about 147 GiB in swap), the `typical` load
on the E-cores, n = 12 requests per arm (3 rounds), and a different cluster and model pod per
row. The k3d-gpu arm went through `kubectl port-forward`, which biases against k3d-gpu.
The k3d-gpu arm's wide upper CIs (to 18.8 and 19.4) come from Fast Draft acceptance varying
by prompt; the lower bounds stay above the competitors' upper bounds.

docs/benchmarks.md was rendered by `bench/vs.py docs` from H30's A1 result file and the A2
H31 and H32 result files, with H32 set to its ledger verdict (INCONCLUSIVE, load unmatched).

```bash
# Reproduce one row (H31 shown) from this commit. Host: docker, k3d, kubectl, RAPL via sudo.
lscpu | grep 'Model name'; uname -r; git rev-parse HEAD
export KUBECONFIG=$PWD/h31.kubeconfig K3D_GPU_CLUSTER=k3dgpu-q-h31
scripts/k3d-gpu up && scripts/k3d-gpu agent up        # logs the device it picks
bench/load/load.py start --level typical --ttl 1500; sleep 20
SUDO_ASKPASS=<askpass> VS_LOAD_WINDOW_S=10 VS_LOAD_SETTLE_S=45 VS_LOAD_OUT=load.jsonl VS_LOAD_LEVEL=typical \
  scripts/k3d-gpu bench --vs llamacpp --id H31 --budget 900 --out bench-H31
bench/vs_matched.py bench-H31/result.json load.jsonl   # medians, CIs, load_matched, confidence
bench/load/load.py stop; scripts/k3d-gpu down
```

### H32 amendment A3: re-run under amendment 27 (2026-10-18)

Written 2026-10-18T16:23Z and pushed 16:24Z (e2006cc), before any A3 request; nothing
below had been measured. The A2 run stays INCONCLUSIVE under the rule that scored it (no regrade).
Amendment 27 (`matched_a27`, merged in #129, `523c23c`) replaces the load rule for this
re-run. The row's claim, arms, prompts, max_tokens, margin, rounds, warmups, budget,
outcome table and prediction are unchanged; everything in A1 and A2 holds except what
is listed here.

- **Load record** (`bench/vs.py`, `VS_LOAD_PHASED=1`). Two 10 s `--phase baseline`
  windows before the warmups, with the load running and no request in flight. Before
  every timed segment, `load.py settle` (5 s windows until load-CPU busy and
  throttle/min are within tolerance of the baseline, at most 60 s; it replaces A2's
  45 s idle and 10 s window); its last window is the segment's pre-window. While the
  segment runs, 5 s `--phase during` windows.
- **Treatment CPUs.** `VS_LOAD_TREATMENT_CPUS=0-15` for both arms: the k3d-gpu pod's
  P-core set, disjoint from the E-core load set 16-31, so during-window busy covers all
  16 E-cores. Plain OVMS keeps its vendor defaults (no `--cpuset`), so any host thread
  it puts on an E-core counts in its during-window busy; that can only push the run
  toward unmatched, never toward a verdict.
- **Matching.** Covariates gate under the unchanged `TOLERANCE`: pre-window busy,
  throttle/min, swap used and swap growth; during-window busy and the two swap metrics.
  Package W, throttle/min and 1-min load during the segment are reported outcomes,
  never gating. A window with fewer load containers than `typical` runs makes the run
  VOID (re-run, never scored). Unmatched covariates make the row INCONCLUSIVE whatever
  the ratios say, with the would-be verdict reported.
- **n and statistics.** As in A1/A2: 3 interleaved rounds x 4 prompts x 128 tokens,
  n = 12 timed requests per arm, 3 warmups per arm; medians with 95% bootstrap CIs
  (10 000 resamples), ratio CI, derived confidence min(P_boot, 1 - p_sign)
  (`bench/vs_matched.py`).
- **Cluster and lock.** `k3dgpu-r-h32`, fresh `k3d-gpu up` + `agent up` with defaults,
  scratch `KUBECONFIG`, at most 25 min in the lock, `typical` first, `heavy` only if a
  full second bench still fits (robustness only, never the verdict); cluster deleted with
  the load stopped before release.
- **Prediction, unchanged from registration.** INCONCLUSIVE (same engine, weights and
  device on a GPU host). A1 and A2 both measured a CPU pick for k3d-gpu and an iGPU pick
  for plain OVMS, so the premise is known to fail here; the prediction is kept as
  registered and scored as written.

### H32 run A3: amendment 27 load rule (i9-13900T, 2026-10-18)

Run under [amendment A3](#h32-amendment-a3-re-run-under-amendment-27-2026-10-18), level
`typical` only (`heavy` was not run: a second bench did not fit the 25-minute hold, and it
is robustness only). One lock hold, one fresh cluster, deleted with the load stopped before
release. Load scored by amendment 27 (`matched_a27`). The A1 and A2 runs keep their
INCONCLUSIVE grading under the rule that scored them; this run decides the row.

```text
# Reproduce
date:            2026-10-18T16:33:00Z (lock) .. 16:44:16Z (released, 676 s)
                 amendment A3 pushed 16:24Z (e2006cc), before any A3 request
hypothesis:      H32 (row + amendments A1, A2, A3; load rule amendment 27)
host cpu:        13th Gen Intel(R) Core(TM) i9-13900T (P 0-15, E 16-31), UHD 770 iGPU, 125 GiB RAM,
                 about 149.7 GiB in swap, 52 other containers running, 1-min load 20.0 at the start
host kernel:     6.6.128-1-MANJARO
k3d-gpu commit:  e2006cc (branch claude/verdict-h32-a27; main 12286d1 + A3)
cluster:         k3dgpu-r-h32, scratch KUBECONFIG; `k3d-gpu up` 59 s, `agent up` 110 s
k3d-gpu arm:     `agent up` defaults picked OpenVINO/Qwen3-4B-int4-ov on CPU, P-cores, 16 CPUs (pod
                 Cpus_allowed_list 0-15), with CPU Fast Draft OpenVINO/Qwen3-0.6B-int4-ov; reached through
                 kubectl port-forward
competitor:      openvino/model_server:2026.4.1-gpu@sha256:a928903f…6fc58 with /dev/dri, vendor defaults, no
                 --cpuset; bin sha256 074214fa… (checked); competitor.log: "Available devices for Open VINO:
                 CPU, GPU", "No device specified, using recommended device: GPU"
load:            bench/load/load.py typical on E-cores 16-31 (3 containers), started 20 s before the bench;
                 2 baseline windows of 10 s before the warmups; before each segment `load.py settle`
                 (5 s windows, max 60 s), its last window the pre-window; 5 s during windows while the
                 segment ran, busy over the load CPUs minus 0-15 (all 16 E-cores)
energy source:   RAPL intel-rapl:0 energy_uj (sudo -A, reads only)
command:         SUDO_ASKPASS=<askpass> VS_LOAD_PHASED=1 VS_LOAD_WINDOW_S=10 VS_LOAD_TREATMENT_CPUS=0-15 \
                   VS_LOAD_OUT=load.jsonl VS_LOAD_LEVEL=typical \
                   k3d-gpu bench --vs ovms --id H32 --budget 900 --keep-cache --out bench-H32
                 bench/vs_matched.py bench-H32/result.json load.jsonl
runs per arm:    3 warmups (3/3 ok) + 3 interleaved rounds x 4 prompts x 128 tokens: n = 12 requests;
                 bench used 465 s of 884 s
raw results:     result.json sha256 713f4a74…3e1f, load.jsonl sha256 1dbbcd39…a62b
```

**Decode speed** (median of 12 requests; 95% CI from 10 000 bootstrap resamples; ratio =
k3d-gpu/plain OVMS of those medians, requests resampled within each arm):

| Arm | decode tok/s (CI) | per-round tok/s | J/token | TTFT s |
|-----|-------------------|-----------------|---------|--------|
| k3d-gpu (CPU, Fast Draft) | 16.15 (15.45-19.09) | 16.13, 16.16, 16.30 | 6.08 | 0.36 |
| plain OVMS (iGPU) | 10.14 (10.11-10.16) | 10.11, 10.19, 10.15 | 9.92 | 0.56 |

Ratio 1.59, CI 1.52-1.88; per-round ratios 1.60, 1.59, 1.61, all above the 1.05 margin.
Every request in both arms returned 128 tokens counted from `usage`, with no failures, so no
outcome-table exclusion fired.

**Load, amendment 27 (`matched_a27`).** Covariates (gating, `TOLERANCE`):

| phase | metric | k3d-gpu | plain OVMS | spread / limit |
|-------|--------|---------|------------|----------------|
| pre | E-core busy % | 45.18 | 44.88 | 0.30 / 5.0 ok |
| pre | throttle/min | 645.0 | 541.5 | 103.5 / 161.3 ok |
| pre | swap used MiB | 149611.7 | 149608.8 | 2.9 / 256 ok |
| pre | swap growth MiB | -0.3 | -0.2 | 0.1 / 64 ok |
| during | E-core busy % | 54.74 | 51.64 | 3.10 / 5.0 ok |
| during | swap used MiB | 149610.4 | 149607.1 | 3.3 / 256 ok |
| during | swap growth MiB | -0.45 | -0.29 | 0.16 / 64 ok |

Outcomes during the segments (reported, never gating): package W 99.3 vs 96.7, throttle/min
353.9 vs 195.3, 1-min load 11.89 vs 10.93 (k3d-gpu vs plain OVMS). The k3d-gpu arm keeps
16 P-cores busy, so it heats the package and throttles more than an iGPU arm; under the
earlier rule that difference alone could void the run. Baseline (2 windows): busy 43.9%,
throttle 604.5/min. Settle gaps: k3d-gpu 30, 5, 15 s; plain OVMS 10, 15, 30 s; all six
settled. Load containers: 3 of 3 running in every phased window (62 windows: 2 baseline,
6 pre, 54 during). Load matched: **yes**, no void.

**Derived confidence** (min(P_boot, 1 - p_sign)): P_boot = 1.00 (no bootstrap ratio below
1.05), 3/3 rounds give p_sign = 0.125, so 0.875, the ceiling 3 rounds allow.

**Verdict: SUPPORTED** (confidence 0.875). Under matched load the `k3d-gpu up` +
`agent up` defaults decoded 1.59x as fast as plain OVMS 2026.4.1 with its own defaults, at
0.61x its package J/token, on the same int4 weights. The registered prediction
(INCONCLUSIVE) was wrong because its premise, "same device", did not hold: plain OVMS
picked the iGPU, and k3d-gpu's auto-config picked the 16 P-cores with CPU Fast Draft
(the H6 and H34 findings). The claim is the configuration choice, not the engine: it says
nothing about OVMS on the same device.

Confounds: one shared host (52 other containers, about 149.7 GiB in swap), `typical` load
on the E-cores, n = 12 requests per arm (3 rounds), the k3d-gpu arm through `kubectl
port-forward` (a bias against k3d-gpu). Plain OVMS ran unpinned, so any of its host threads
on an E-core counted in its during-window busy; that could only push toward unmatched. The
k3d-gpu arm's wide upper CI (to 19.1) comes from Fast Draft acceptance varying by prompt;
its lower bound stays above plain OVMS's upper bound. Order effects: rounds A,B / B,A / A,B
gave the same ratio within 0.02.

docs/benchmarks.md was rendered by `bench/vs.py docs` from H30's A1 result file, H31's A2
result file and this H32 result file.

```bash
# Reproduce from this commit. Host: docker, k3d, kubectl, RAPL via sudo.
lscpu | grep 'Model name'; uname -r; git rev-parse HEAD
export KUBECONFIG=$PWD/h32.kubeconfig K3D_GPU_CLUSTER=k3dgpu-r-h32
scripts/k3d-gpu up && scripts/k3d-gpu agent up        # logs the device it picks
bench/load/load.py start --level typical --ttl 1500; sleep 20
SUDO_ASKPASS=<askpass> VS_LOAD_PHASED=1 VS_LOAD_WINDOW_S=10 VS_LOAD_TREATMENT_CPUS=0-15 \
  VS_LOAD_OUT=load.jsonl VS_LOAD_LEVEL=typical \
  scripts/k3d-gpu bench --vs ovms --id H32 --budget 900 --out bench-H32
bench/vs_matched.py bench-H32/result.json load.jsonl   # medians, CIs, matched_a27, confidence
bench/load/load.py stop; scripts/k3d-gpu down
```

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
  unconfined, and does not say pinning fails on a quiet host. The verdict rests
  on [H3 run](#h3-run-i9-13900t-2026-10-16), which has a full header. The
  host-level observation that motivates H5 has a header with most fields not
  recorded ([H3 host-level observation](#h3-host-level-observation-i9-13900t-2026-10-16))
  and backs no claim (added 2026-10-18).
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
- **H28** covers one host, overlayfs, 8 pods and a 1 GiB file read page by page, under
  `typical` load. It shows image-volume pods share one page-cache copy; it does not show
  the same for other snapshotters (fuse, `native`), GPU or NPU offload (device buffers are
  per process), or a time win against a network download.
- **H16/H17** cover one UHD 770 (i915) and the Intel GPU DRA driver gpu-v0.12.1
  on k3s v1.37.1. H17's refutation is about this driver release, which publishes
  each GPU as one exclusive device; it says nothing about DRA itself, nor about
  discrete Intel GPUs, where the plugin default is also one pod per GPU.

### H21 amendment: matched background load (2026-10-18)

Written 2026-10-18, before any matched-load H21 run. The 2026-10-17 run was
INCONCLUSIVE because uncontrolled host load drifted between arms (idle package
spread 26.5 W over the run). Real Intel boxes are busy, so the retry does not wait
for a quiet host. Every arm runs under the same controlled background load,
interleaved, and the load is recorded per arm.

- **Load.** `LOAD_LEVEL=typical|heavy bench/joule-pack/probe.sh` starts
  `bench/load/load.py start --level L` on the load CPU set (E-cores 16-31 on this
  i9-13900T) before `k3d-gpu up` and stops it on every exit path. Turn
  calibration runs under that load, so the turn size is per level. `typical` is the
  headline result. `heavy` is the robustness check, run only if lock time remains.
  The agent pods in arms E, X and M share the E-cores with the load. That is
  intended: it is the busy box the rule has to work on.
- **Per-arm load record.** Inside each 12 s idle window, one `load.py sample`
  window of 10 s is tagged with the arm that follows it. It records E-core busy %,
  load1, RAPL package W, core throttle events/min, swap used and swap growth to
  `$OUT/load.jsonl`.
- **Matching.** `load.py matched` over the five arm tags, with `load.py` TOLERANCE
  unchanged (busy 5 pp; load1 1.0 or 15%; package 3 W or 10%; throttle 10/min or
  25%; swap used 256 MiB; swap growth 64 MiB). If the load is unmatched, (a) and
  (b) are INCONCLUSIVE, and the verdict names the metric. High load alone never
  makes a run INCONCLUSIVE.
- **Floor gates.** The two run-wide idle-spread gates measured host drift, and the
  bracketed marginal already removes that drift. They are replaced by the same
  thresholds applied to the quantity the marginal subtracts: the drift between the
  two idle windows that bracket an arm (|before - after| package W, median over
  arms). INCONCLUSIVE if the median drift > 50% of the median E marginal, or if any
  P marginal < the median drift. The run-wide spread is still reported. The
  uncore negative control, the cpuset audit, the >= 2 complete rounds rule, the
  additivity gate and every (a)/(b) outcome rule are unchanged.
  `joulepack.py verdict` applies these gates only when `load.jsonl` is present, so
  the 2026-10-17 run still scores by its own rules.
- **n and arms.** Unchanged: 3 rounds, orders U P E X M / M X E P U / E U M P X,
  12 s idle + 8 s warm-up + 32 s measured per arm, N = 12, 333m, 1 s think,
  BUDGET 1140 s, HARD_STOP 1200 s per level. Confidence 1 - 0.5^k (ceiling 0.875).
- **Predictions (unchanged).** REFUTED on both: best/E 1.00-1.08 (best = E or X),
  best/U >= 1.3, CPU-share error <= -0.25 (E overcharged).

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
