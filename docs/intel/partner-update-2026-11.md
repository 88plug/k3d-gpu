# k3d-gpu partner update, November 2026

Status: 2026-11-07. k3d-gpu release 0.1.94. Ledger: [EXPERIMENTS.md](../../EXPERIMENTS.md),
83 rows. Only SUPPORTED rows are quoted as results; REFUTED and INCONCLUSIVE rows are stated
as the ledger graded them. Every number here is in the ledger with its protocol and
reproduce block, unless its line cites a merged pull request as the source. Nothing in this page has been sent to an `intel/*` repository.

k3d-gpu turns one Linux host into a single-node Kubernetes cluster that detects the host's
Intel hardware and configures it. The aim of this work is to make Intel's own stacks (EAS,
enterprise-inference and the Enterprise Agent Toolkit) easy to install and evaluate on one
machine, and to report back what we find while doing it.

## What is new since 2026-11-01

**EAT 2.0 at Intel's GA tags installs on k3d-gpu by default.** Intel published EAS
[v1.2.0](https://github.com/intel/enterprise-ai-solutions/releases/tag/v1.2.0), EAT
[release-2.0.0](https://github.com/intel/enterprise-agent-toolkit/releases/tag/release-2.0.0)
and enterprise-inference [v1.1.0](https://github.com/intel/enterprise-inference/releases/tag/v1.1.0)
on 2026-11-06. `k3d-gpu eas` now pins those tags as its default profile, and `k3d-gpu eat`
(`up`, `status`, `check`, `down`) names the agent-toolkit layer; `eat up --legacy` runs EAT
1.x's installer ([eas.md](eas.md)). F5 ran `k3d-gpu eat` once at the GA tags, on a fresh
default cluster on the Xeon 6741P ([F5 run](../../EXPERIMENTS.md#f5-run-xeon-6741p-2026-11-07)):

- `eat up` installed EAS's platform, inference and agent-toolkit layers with `failed=0` and no hand edit.
- `eat check` passed Intel's `validate agent-toolkit`, then a chat, a tool call, the guardrail's block and a sandbox run through the EAS gateway.
- The secret scan counted 0 for the LiteLLM master key and the sandbox router token, raw or base64, and `eat down` cleaned up.

F5 is SUPPORTED: one functional run on one host, not a performance row. Two of the k3d patches
it applied are new: k3s v1.37 bundles newer Gateway API CRDs than EAS `release-1.2` installs, and
overlay rows 0007 and 0008 keep the newer install, as EAS `main` already does. With
`auth_provider: litellm`, the agent toolkit's setting at the GA tags, EAS installs no Keycloak, so
F3's Keycloak finding does not arise on this path; the inference layer on its own keeps Keycloak.

**A second Raptor Lake client host is measured.** wildct is an i7-13700 (8 P-cores and
8 E-cores, AVX-VNNI, no AVX-512 or AMX) with a UHD 770 iGPU and two NVIDIA cards. The box
owner opts all three GPUs out, so its rows are CPU rows. Each ran on release 0.1.89 under the
[wildct pre-registration](../../EXPERIMENTS.md#wildct-pre-registration-client-raptor-lake-i7-13700-2026-10-20).
All nine rows there have a verdict, and four of them are SUPPORTED:

- Detection: a plain `k3d-gpu up` found the hybrid P/E cores and the six ISA extensions the launcher labels, and nothing the host lacks. `k3d-gpu test` put its P-core pod on CPU 0 and its E-core pod on CPU 17, and `up` named both GPU vendors as detected and opted out [ledger:H52w].
- Agent sandboxes, H18 replicated: in 3 of 3 runs `k3d-gpu sandbox up` kept the sandbox on an E-core behind default-deny egress. The host's tailscale and LAN addresses were refused from the sandbox, and PyPI was reachable [ledger:H18w].
- Shared-prefix agents, H25 replicated: with OVMS's shipped prefix caching on, an added agent's first chunk arrived 14.3x sooner than with it off (CI 13.38-14.89), under matched `typical` load [ledger:H25w]. This measures an OVMS feature; k3d-gpu enables nothing.
- Shared model page cache, H28 replicated: eight pods that mmap one 1 GiB model through an image volume shared one page-cache copy (1.00x the model size on the node, against 9.00x for per-pod copies) and were ready in 0.339 of the copy arm's time (CI 0.329-0.363), under matched `typical` load [ledger:H28w].

The fifth, H34w, is INCONCLUSIVE: with H34 replicated there under matched `typical` load,
OVMS's CPU Fast Draft against the target alone resolved neither the pre-registered gain nor its
exclusion, so the i7-13700 class gets no CPU draft by default. The sixth, the engine-choice
row H59w (run with this host's rules, outside the pack's order), is REFUTED: at four
concurrent requests llama.cpp decoded per request at about OVMS's rate, short of the
pre-registered 1.2x, so the i7-13700 class keeps OVMS as its CPU engine. The seventh, H57wb
(k3d-gpu against EAT's stock CPU arm in EAT's banking flow), is INCONCLUSIVE (not run): its start gates were
met, but that arm, vLLM CPU bf16 on the QAT bf16 weights, needs a 51.6 GB download and about
94 GB resident of 125 GiB beside the box's production containers.
It has no registered runner there, and the owner closed the row rather than register one
([wildct amendment R11](../../EXPERIMENTS.md#wildct-amendment-r11-h57wb-closed-as-not-run-by-the-owners-decision-2026-11-10)).
The last two, H57wa and H57wm, replicate the i9's Gemma 4 26B-A4B QAT single-stream rows, and both
are REFUTED. llama.cpp with Google's QAT Q4_0 GGUF and the QAT MTP drafter decoded at 1.34x the
rate of k3d-gpu's OVMS pick (CI 1.31-1.34), so OVMS did not lead at one stream there, and
the drafter added 1.13x over llama.cpp alone (CI 1.11-1.14), short of the pre-registered 1.2x, with
GSM8K 50 of 50 for every arm ([run](../../EXPERIMENTS.md#h57w-run-wildct-i7-13700-2026-11-11)). Neither moves a default: the class keeps OVMS
by the engine-choice row, and gets no QAT MTP drafter by default. With them the wildct campaign
ended on 2026-11-11, and everything it created on the host was removed but the box owner's
kernel-module file and a build cache left for the owner to clear.

**Intel's documented EAT 2.0 install, at the GA tags, finished in a KVM guest and served EAT's
banking flow as well as k3d-gpu's CPU stack.** H62r repeated H62 at the GA tags: Intel's
documented single-node default (EAS v1.2.0 installing EAT 2.0 and enterprise-inference v1.1.0
with Kubespray, gemma-4-26b on vLLM CPU v0.28.0) against k3d-gpu's CPU stack (OVMS with an int4 IR
of the same Gemma 4 26B-A4B QAT weights), each in its own KVM guest of one shape on the Xeon 6741P
([H62r run](../../EXPERIMENTS.md#h62r-run-xeon-6741p-2026-11-07)):

- Intel's install finished in its guest in one attempt, all three PLAY RECAPs at `failed=0`. EAS's documented operator setting `nri_reserved_cpu_list` ([nri_cpu_balloons.md#L71-L74](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/docs/customize/nri_cpu_balloons.md#L71-L74)) named the guest's 48 sibling vCPUs, so `cpu: auto` sized gemma-4-26b to 40 CPUs and EAT's sandbox router was placed. In H62 that router could not be scheduled.
- `validate agent-toolkit` passed, and a chat, a tool call, the guardrail's block and a sandbox run went through EAS's gateway.
- In 24 interleaved pairs of EAT's banking tasks, each stack finished 21 of 24 within the turn budget. The paired difference was 0.000 (CI -0.125 to +0.125), and every gate passed.

The row predicted k3d-gpu's stack far ahead, so the ledger grades it REFUTED. For EAT users: on
this Xeon, Intel's GA install, with the documented reserve list set, did as well in EAT's own
banking flow as k3d-gpu's own CPU stack. The scope is the ledger's: one hold, both stacks in KVM
guests, and the harness sent Intel's arm its turns at the model's Service, past EAS's LiteLLM
gateway and guardrail, a deviation H62 named before it ran (D4).

One finding from the run is for EAS: in a KVM guest whose kernel numbers a core's two threads
next to each other, EAS's topology probe counts the comma fields of `thread_siblings_list`
([probe-node-topology.sh#L30-L31](https://github.com/intel/enterprise-ai-solutions/blob/a051f00fcb2dd473fe763610471cd509db82c433/roles/nri_cpu_balloons/files/probe-node-topology.sh#L30-L31))
and reads one thread per core. EAS then reserves 12 CPUs instead of the 48 siblings, which is how
H62's install came to size the model to 76 CPUs
([H62r pre-registration](../../EXPERIMENTS.md#h62r-pre-registration-h62-at-the-ga-tags-eas-v120-with-eat-20-on-the-xeon-6741p-2026-11-06)).
Setting EAS's documented reserve list, as above, works around it.

**Pins move on their own, and each move is checked before it merges.** One registry,
`pins/registry.toml`, now owns every version, digest and model revision that k3d-gpu's own
files and its EAT stock path pin. The Freshness workflow proposes each move to upstream's
newest stable release, every 6 hours, as one pull request per group ([pins/README.md](../../pins/README.md)).

- The merge policy is computed from the diff. A patch or minor move of a plugin or a CPU engine merges on its own once CI and the canary pass. A minor move of a component that a SUPPORTED ledger row measured (OVMS and llama.cpp today) waits for a person, as do a major, a host driver and an EAT stock pin.
- A pin move also moves the test goldens and fixtures that record the old value, so a bump's pull request does not fail on a stale fixture ([#328](https://github.com/88plug/k3d-gpu/pull/328)).
- The Canary workflow runs nightly and on every pin change ([canary/README.md](../../canary/README.md)). It serves a pinned Qwen3-0.6B on vLLM CPU, OVMS and llama.cpp in a plain container with the launcher's arguments, not in a pod. It checks every flag and environment variable the launcher emits against the vLLM XPU, llm-scaler, Gaudi vLLM, llama.cpp SYCL and Vulkan, DLStreamer, sglang Xeon, NPU UMD and OVMS images, and it starts the node image the way `up` does. It runs on GitHub-hosted runners, so it needs no device.
- The hourly bot sweep merges one such pull request at a time, only while main's CI is green, and then dispatches one batched release.
- A pre-registered row's arms stay put. A PENDING row's runner pins move only by a dated ledger amendment, so H59w's registered llama.cpp build and drafter-conversion packages were held through its run, and still are. EAT 1.x's pin holds below `release-2.0.0`, which removed the installer `eat-up.sh` drives; EAT 2.0 goes through `k3d-gpu eas`.

**llama.cpp's CPU memory fit counts what the server holds.** In H59d on the i9-13900T, the
llama.cpp arm was OOMKilled at the 45Gi pod limit the fit gave it. The fit now counts the
locked GGUF, the copies of the repacked weights (AMX buffers at their packed size), both
contexts' compute buffers, the drafter and the per-slot logits
([#294](https://github.com/88plug/k3d-gpu/pull/294)).

**A real-hardware check of a bump found a benchmark bug on hybrid CPUs.** Checking the Ollama
bump (#321) on the i9-13900T found that `k3d-gpu bench --vs ollama --cpuset 0-15` started
Ollama's llama-server with 24 threads on 16 CPUs. llama.cpp's default thread count falls back
to the host's physical core count inside a cpuset, and Ollama's CPU backend spins its extra
threads at the barriers. `bench --cpuset` now sets `LLAMA_ARG_THREADS` to the count llama.cpp would pick on the cpuset's own CPUs, one per physical P-core on the i9-13900T, and that run's Ollama arm decoded at 10.5 tok/s instead of 0.30 ([#337](https://github.com/88plug/k3d-gpu/pull/337)).
The fix changes only the Ollama competitor arm of `bench`, and only with `--cpuset`.

## What was new on 2026-11-01

**The Xeon 6741P campaign is closed.** Every pre-registered row on the Xeon 6741P with four
Arc Pro B70s has a verdict. Read with its scope in [intel.md](../intel.md#eat-by-default-vs-with-k3d-gpu)
and the [technical report](technical-report.md).

- H59 is SUPPORTED. On the Xeon 6741P, at four concurrent requests, llama.cpp with Google's QAT Q4_0 GGUF and the QAT MTP drafter decoded Gemma 4 26B-A4B 3.50x faster per request than OVMS with OpenVINO's base-model int4 IR (95% CI 3.42-3.61), faster in each of 3 holds, at GSM8K 49 against 50 of 50 [ledger:H59]. One model, one host, shared with two GPU serving pairs that kept running. Not gated: OVMS gave an added agent its first token sooner when the prefix was reused (0.63 s against 0.82 s) [ledger:H59].
- As a result, the model optimizer's CPU engine default for that CPU class is now llama.cpp. `console/ledger-rules.json` carries H59 SUPPORTED for class `6741p`, so with `K3D_GPU_AGENT_BACKEND` unset and no variant named, `agent up --model google/gemma-4-26B-A4B-it` serves llama.cpp with the QAT GGUF and its MTP drafter (release 0.1.85). Other CPU classes keep OVMS until their own row is SUPPORTED. This compares two stacks as shipped (different engines and different weights), not two engines on identical weights. The rule also moves any other model with an int4 GGUF to llama.cpp on this class; H59 measured only Gemma 4 26B-A4B, so for other models that default is not yet backed by a measurement.
- H60 is SUPPORTED. Qwen3.8-Flash-Next (125B MoE, 6B active) ran entirely from the Xeon's DRAM through llama.cpp and read 0.82 of a same-hold Gemma 4 QAT control's share of the DRAM bandwidth ceiling (CI 0.819-0.831, band 0.7-1.3), with GSM8K 50/50 [ledger:H60].
- H7x is REFUTED. At matched 16-bit weights, OVMS 2026.4.1 still decoded ahead of the vLLM CPU engine EAT 1.1.0 ships (v0.28.0), so OVMS's lead on this host is not only weight precision. vLLM prefilled the long prompt faster.
- H28x is REFUTED (time part). Eight pods that mmap one model through an image volume shared one page-cache copy, as on the i9-13900T, but were not ready in half the time of per-pod copies. The memory part replicated; the time part did not.
- H58 is REFUTED. With no flag, `agent up` refused all three models of the zero-touch test: two int4 IRs missed the best B70's free-memory budget (the shared host's serving pairs held about half of every card), and Qwen3.8-Flash-Next had no cached DRAM bandwidth. `up` advertised three of four B70s and no DSA device. These are k3d-gpu gaps.
- H62 is INCONCLUSIVE. It compared k3d-gpu's CPU stack with Intel's new documented default, EAS v1.2.0-rc.1 installing EAT 2.0, each in its own KVM guest. Intel's install did not finish in its guest (enterprise-inference's `cpu: auto` finding below), so no hold ran and nothing is compared.
- H53 is INCONCLUSIVE. EAT 1.1.0's four-card vLLM XPU path did not start in a k3d-gpu cluster: oneCCL needs `/dev/dri/by-path`, which the k3d node does not have. That gap is k3d-gpu's.
- H54d, H54q and H50o are INCONCLUSIVE. H54d did not run (its arm is undefined). In H54q the QAT-matched int4 IR stayed within the margin of the bf16 QAT reference, but the base-model IR scored no lower than it. In H50o, EAT's OVMS chart (OVMS 2025.4) crashed on loading Gemma 4 26B-A4B, so no pair ran.
- F3 is REFUTED on one condition. `k3d-gpu eas up --agent-toolkit` installed EAS's platform, inference and agent-toolkit layers on a fresh default cluster on the i9-13900T with `failed=0` and no hand edit; `eas check` passed chat, tool call, the guardrail block and the sandbox through the EAS gateway; `eas down` tore it down. Its secret scan found the Keycloak admin password once in a `ps` sample, from enterprise-inference's `kcadm.sh` call ([F3 run](../../EXPERIMENTS.md#f3-run-i9-13900t-2026-11-01)).

**EAS support shipped** (release 0.1.84). `k3d-gpu eas up`, `eas status`, `eas check` and
`eas down` drive EAS's `es_auto_installer.sh` in its bring-your-own-cluster mode
(`existing_kubernetes`). `eas up` reads the cluster, prints every choice as
`fact -> choice [source]` with a pinned upstream permalink, applies the k3d patches EAS has
not merged yet, and runs the installer from a pinned bastion container. `--dry-run` and
`--explain` show all of it before anything changes. Details: [eas.md](eas.md).

## What we can hand over

All drafts are unsent and need a maintainer's approval before anything is filed.

| Item | What it does | Where |
|---|---|---|
| EAS patch 0002 | Istio `global.platform` from a variable, so k3s/k3d nodes get the right CNI path | [patches/](../../integrations/eat/upstream/patches/), [upstream-eat.md](upstream-eat.md) |
| EAS patch 0003 | Pin the nri-plugins chart (`v0.15.0`) | same |
| EAS patch 0004 | Node topology labels on bring-your-own clusters | same |
| EAS patch 0005 | A single-node k3d docs page | same |
| EAS patch 0006 | Envoy data plane selects control-plane nodes by label presence, so it schedules on k3s; a `release-1.2` variant too | same, and [integrations/eas/patches/](../../integrations/eas/patches/) |
| EAS issue | The k3s bring-your-own path, the umbrella for the patches | [issue-k3s-byo.md](../../integrations/eat/upstream/issue-k3s-byo.md) |
| EI issue draft | Guardrail docs: a block is HTTP 200 `content_filter` on LiteLLM v1.105.1, not HTTP 500 | [upstream-eat.md](upstream-eat.md#ei-issue-draft-a-guardrail-block-is-http-200-on-litellm-v11051-unsent) |
| EI patch and issue draft | The Keycloak admin password and the client secret are `kcadm.sh` arguments; with `KC_CLI_PASSWORD` and a Keycloak-generated secret neither is. `eas up` applies the patch | [upstream-eat.md](upstream-eat.md#ei-the-keycloak-admin-password-and-the-client-secret-are-kcadmsh-arguments), [patches/ei/0001](../../integrations/eas/patches/ei/0001-keycloak-config-kcadm-argv.patch) |
| EI issue draft | The LiteLLM master key is in the model deploy's command line; the override `eas up` uses is the suggested fix | [upstream-eat.md](upstream-eat.md#issue-drafts-from-the-2026-11-01-runs-unsent) |
| EI issue draft | `cpu: auto` can leave no CPU for components installed after the models (H62) | same |
| EAT issue draft | `release-1.1.0`'s OVMS chart image (`2025.4`) cannot load Gemma 4 26B-A4B | same |
| EAT issue draft | `release-1.1.0`'s XPU command sources a `setvars.sh` the `v0.28.0` XPU image lacks | same |
| EAS patches 0007 and 0008, issue draft | On k3s v1.37, EAS `release-1.2`'s Istio and Envoy Gateway installs keep the newer Gateway API CRDs k3s bundles, as EAS `main` does since #42 | [patches/](../../integrations/eas/patches/), [upstream-eat.md](upstream-eat.md#eas-release-12-a-newer-gateway-api-install-stops-the-platform-layer) |
| EAS issue drafts | `init` keeps an existing `global_config.yaml`'s `auth_provider`; `configure` refuses `--force` | [upstream-eat.md](upstream-eat.md#issue-drafts-at-the-ga-tags-2026-11-07-unsent) |
| EI issue draft | `v1.1.0` ships LiteLLM and vLLM CPU images older than the fixes for five published advisories | [upstream-eat.md](upstream-eat.md#ei-v110-the-stock-litellm-and-vllm-cpu-images-are-below-published-fixes) |
| EAT patch and issue draft | `redis_stack`'s validate passes the Redis password as `redis-cli -a`; `eas.sh` applies the patch after each `init` and before `eas check` runs the validate | [upstream-eat.md](upstream-eat.md#eat-redis_stack-validate-passes-the-redis-password-as-redis-cli--a), [patches/eat/0001](../../integrations/eas/patches/eat/0001-redis-stack-validate-redis-cli-auth.patch) |
| EAT docs note | `release-2.0.0`'s guides clone the moving branch `release-1.2`, and some pages still name 1.x paths | [upstream-eat.md](upstream-eat.md#eat-release-200-docs) |

## What is proven

SUPPORTED rows, each scoped to its host:

- On the Xeon 6741P: the CPU engine choice [ledger:H59], DRAM-resident serving of Qwen3.8-Flash-Next [ledger:H60], and H25 replicated [ledger:H25x].
- On the i9-13900T: sixteen rows, among them EAT `release-1.0.3-hotfix` deployed and served on a default k3d-gpu cluster, n=1 [ledger:H1b], EAT's NRI balloons chart installing over a k3d-gpu cluster [ledger:H14], and agent sandboxes behind default-deny egress [ledger:H18]. The full list is in the [technical report](technical-report.md#supported-quotable-scoped-to-the-i9-13900t-h25x-h59-and-h60-to-the-xeon-6741p-h52w-h18w-h25w-and-h28w-to-wildcts-i7-13700).
- On wildct, an i7-13700 client host: detection of its hybrid cores and ISA extensions with its GPUs opted out [ledger:H52w], H18 replicated, agent sandboxes on its E-cores behind default-deny egress [ledger:H18w], H25 replicated, an added agent's first chunk 14.3x sooner with OVMS's shipped prefix caching on [ledger:H25w], and H28 replicated, eight pods sharing one page-cache copy of a model through an image volume and ready in 0.34x the time of per-pod copies [ledger:H28w].

## Three results that help EAT

Each is stated as the ledger graded it. Inside EAT's banking flow on the Xeon 6741P:

- At the GA tags, Intel's documented install of EAS v1.2.0 with EAT 2.0 finished in a KVM guest and delivered as many tasks within the turn budget as k3d-gpu's CPU stack, 21 of 24 each (H62r REFUTED, above).
- EAT 1.1.0's stock vLLM CPU arm delivered at least as many tasks within the turn budget as k3d-gpu's CPU stack (H50 REFUTED).
- EAT 1.1.0's move to vLLM CPU v0.28.0 is past the v0.24.0 CPU attention hang that stalled EAT 1.0.3's stock arm in H54a.

## What is still open

- **The Keycloak argv secret.** F3 stays REFUTED. With the agent toolkit at the GA tags (`auth_provider: litellm`), EAS installs no Keycloak, and F5 found no Keycloak secret; the inference layer on its own keeps Keycloak. `k3d-gpu eas up` now patches enterprise-inference's `keycloak_config` role so that neither the admin password nor the client secret is a `kcadm.sh` argument ([State and secrets](eas.md#state-and-secrets)). The patch is checked with a `ps` sampler against a stand-in for kcadm; no live run has checked it yet. A fresh `eas up --agent-toolkit` with F3's secret scan would.
- **EAS's topology probe in a KVM guest.** H62r answered H62's question at the GA tags, with EAS's documented reserve list set. The probe's reading of a guest's threads as one per core is unchanged at `v1.2.0`, and we have not drafted an EAS issue for it yet. The `cpu: auto` issue draft stands for an install whose reserve leaves the pods outside the balloons too little CPU.
- **The vLLM agent path on Gaudi, and llm-scaler.** Up to release 0.1.93, `k3d-gpu agent up` did not serve through vLLM. On an Arc Pro B70 the engine exited at start because the pod's Kubernetes service link set `VLLM_PORT` to a URI, and the warm-up request carried no JSON content type. [#339](https://github.com/88plug/k3d-gpu/pull/339) fixes both and ships in release 0.1.94. With its pod spec and warm-up request applied by hand, vLLM v0.33.0 served Qwen3-4B on one B70. On 2026-11-08 a full `agent up` of release 0.1.94, with its v0.32.0 image and no hand edit, served Qwen3-4B on a B70: the pod was Ready, the launcher's warm-up was answered, "What is 17 times 23?" got 391, and a `get_weather` tool call came back. [#357](https://github.com/88plug/k3d-gpu/pull/357)'s head at `0985b179`, which moves the image to v0.33.0, served the same way ([record](../../bench/vllm-agent/results/2026-11-08/README.md)). The Gaudi path and llm-scaler share the manifest and have not run on hardware. On Arc, `agent up` with no backend set serves OVMS, which this did not affect; with `--model`, the fit can pick vLLM.
- **k3d-gpu gaps found on the Xeon.** `/dev/dri/by-path` for oneCCL across B70s (H53); the fit refusing rather than falling back to the CPU, and no DRAM bandwidth cached on a new host (H58); placing one-card arms on one card (H54b, H54c).
- **Replications.** H59 on the i9-13900T (H59d) is INCONCLUSIVE: llama.cpp's arm was OOMKilled at the fit's pod limit and not deliverable in two holds, so no statistic was scored. H59 on the i7-13700 (H59w) is REFUTED: at four concurrent requests llama.cpp decoded per request at about OVMS's rate, short of the pre-registered 1.2x. The wildct rows H57wa and H57wm are REFUTED (llama.cpp with the QAT MTP drafter outran OVMS at one stream, and the drafter's gain over llama.cpp alone stayed under 1.2x); H57wb is INCONCLUSIVE (not run), closed by the owner rather than register a runner for its EAT stock CPU arm on that production box. H34 on the i7-13700 (H34w) is INCONCLUSIVE: the CPU Fast Draft's gain over the target alone was neither the pre-registered win nor ruled out, so that class keeps no default CPU draft. Both CPU classes keep OVMS: the i9-13900T until a row is SUPPORTED for its class, and the i7-13700 unless H59w is superseded, since a REFUTED row wins over a SUPPORTED one.
- **EAT 1.1.0 through `eat-up.sh`.** Pinned since 2026-10-29 and pre-registered as F4; not run yet.
- **EAS upstream moves.** `k3d-gpu eas` defaults to the GA tags; the `main` profile (F3) and `v1.2.0-rc.1` (H62) stay as recorded. When Intel tags the next EAS release, the `eas-release` pin moves the default profile, and the overlay's verified cases and config goldens for the new tree still need a person to add them. EAS #61 merged on 2026-10-30 (EAS `main` `0f7bc2b`). The `main` profile stays at `97f6157`; moving past #61 needs patch 0002 regenerated. EAS #50 (OpenShift) merged into EAS `main` on 2026-11-02 and #52 is open (read 2026-11-07); neither is in `v1.2.0`, and moving the `main` profile past #50 needs 0006 reworked. EAS #71 (teardown hang) is open; `eas down` uses its workaround.
- **ERAG and Arc under EAS.** EAS's Enterprise RAG layer is not wired into `k3d-gpu eas` yet, and EAS has no Intel GPU path on this branch; Arc and Gaudi stay with `k3d-gpu agent up` (see the vLLM item above).

## Related

| If you want to… | Go to |
|---|---|
| Install EAS (EAT 2.0) on k3d-gpu | [eas.md](eas.md) |
| See the upstream findings in one table | [intel.md, Upstream findings](../intel.md#upstream-findings) |
| Read every verdict and its scope | [technical-report.md](technical-report.md) |
| See the patch pack, its checks and the issue drafts | [upstream-eat.md](upstream-eat.md) |
| Check a number | [EXPERIMENTS.md](../../EXPERIMENTS.md) |
