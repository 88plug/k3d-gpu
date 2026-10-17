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
| H7 | OVMS's decode lead over EAT's vLLM CPU engine in the H2 run comes from weight precision, not from the engine. | On the i9-13900T 16-CPU P-core balloon, one checkpoint served by OVMS 2026.4.1 and by vLLM CPU v0.24.0 at matched weight precision decodes within 15% between the two engines, and the two land within 15% of each other in effective weight bandwidth (weight bytes x decode tok/s). | Pre-registered 2026-10-16, before any run. One checkpoint and revision (`Qwen/Qwen3-4B-Instruct-2507`, revision recorded) for both engines: OVMS loads an OpenVINO IR exported from it with 16-bit weights, vLLM loads it in bf16. A second arm at int4 in both engines runs only if vLLM v0.24.0 CPU is first shown to load an int4 build of the same checkpoint. vLLM runs with EAT's chart defaults and once with `--enforce-eager` dropped, to split engine from config. H2 workloads and energy method, arms interleaved ABBA, 5 rounds. Refuted if the decode gap at matched precision is 15% or more. | PENDING |
| H19 | `k3d-gpu tune run --objective throughput` on the CPU agent stack (OVMS 2026.4.1, `OpenVINO/Qwen3-4B-int4-ov`, i9-13900T) does not crown a config whose gain over the default is run-to-run noise. | (a) The tune completes and writes a profile whose decision is either `default`, or `tuned` with a winner that an independent replication confirms. (b) The search's own null control, the explicit restatement of the default (`CPUS=16 CORES=performance MAX_NUM_SEQS=256 CACHE_SIZE=0 PREFIX_CACHING=true`, which the pilot showed writes a `graph.pbtxt` byte-identical to the default's), is never accepted. (c) Replication: the tuned winner, or with decision `default` the finalist with the best confirmation median, is measured again against the default in fresh trials outside the tune. Prediction: decision `default` (single-stream decode is memory-bound and the default already takes all 16 P-core CPUs), and the replication shows no gain beyond the noise gates. | Pre-registered 2026-10-17T09:15Z, before the counted tune, after two pilot trials (default and the explicit default, budget 4 x 64 tokens: 15.3/49.3/48.7 vs 15.3/49.8/46.8 aggregate tok/s at concurrency 1/4/8, identical graphs). Cluster `k3dgpu-r2-tune` from `docker build -t k3d-gpu:r2-tune .`, scratch `KUBECONFIG` and profile dir. Command: `k3d-gpu tune run --objective throughput --devices CPU --precisions int4` with the default criteria (n0=27, eta=3, n=5 confirmation runs per side in rotating order, margin 5%, k=1, alpha 0.05 Bonferroni, TTFT guardrail 25%, quality probe, `--unique-prompts` load). One tune (its confirmation is the n=5 interleaved step). Replication: 5 fresh trials per side, order A B B A A B B A A B (A = default), the tune's final budget (16 requests x 128 tokens at concurrency 1,4,8), same acceptance test (`tune.py` gates). Confounds sampled every 5 s for the whole run: package W (RAPL), 1-min load, swap used, sum of `package_throttle_count`. Outcome -> conclusion: (b) the explicit default accepted -> REFUTED (the gates pass noise); decision `tuned` and the replication fails the gates -> REFUTED (a noise winner was crowned); decision `tuned` and the replication passes -> SUPPORTED (a real winner); decision `default` and the replicated finalist also fails the gates -> SUPPORTED; decision `default` but the replication passes every gate -> INCONCLUSIVE (the tune missed a real gain, a power question, not a noise crown); the tune fails to finish or the default trial fails -> INCONCLUSIVE. | PENDING |

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
| 2026-10-17 | H16, H17 | i9-13900T, UHD 770: 6 fresh clusters D P P D D P (one `up`, 3 timed claim or i915 pods and a 2-pod check each), plus a `k3d-gpu test` cluster in DRA mode and a manual `k3d cluster create` from the same image (header and table in [H16/H17 run](#h16h17-run-i9-13900t-2026-10-17)) | H16 held in all 3 DRA runs. H17 (a) failed in all 3: the second claim pod stayed Pending (`cannot allocate all claims`) while 2 i915 pods ran at once in all 3 plugin runs; (b) and (c) held (pod latency 1.00x, `up` 1.06x) | DO-NOT-RE-ATTACK for gpu-v0.12.1: DRA mode stays opt-in. Re-attack when an Intel GPU DRA driver release publishes shared or multi-allocatable devices (consumable capacity or `allowMultipleAllocations`) |
| 2026-10-17 | H18 | i9-13900T with tailscale: pilot run 0, then 3 fresh clusters from this branch's image, `bench/sandbox_round.sh` (header and numbers in [H18 run](#h18-run-i9-13900t-2026-10-17)) | Held 3 of 3. Every sandbox pod on CPU 24 (cpu_atom 16-31); sandbox -> `100.65.179.24:22` and `192.168.1.108:22` refused in 0.13-0.16 s with 2 packets on the policy's drop log, while the control pod connected to both; `pypi.org:443` connected and `pip install --user tomli_w==1.2.0` installed to `/tmp/.local` and imported. Pilot: blocked egress is a REJECT, not a timeout, and `six` ships in the image (amendment in the H18 row) | Yes, for egress and placement with kube-router on k3s v1.37.1; re-attack on a new k3s, agent-sandbox or CNI |

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
