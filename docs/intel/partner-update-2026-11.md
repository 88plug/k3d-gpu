# k3d-gpu partner update, November 2026

Status: 2026-11-07. k3d-gpu release 0.1.92. Ledger: [EXPERIMENTS.md](../../EXPERIMENTS.md),
83 rows. Only SUPPORTED rows are quoted as results; REFUTED and INCONCLUSIVE rows are stated
as the ledger graded them. Every number here is in the ledger with its protocol and
reproduce block, unless its line cites a merged pull request as the source. Nothing in this page has been sent to an `intel/*` repository.

k3d-gpu turns one Linux host into a single-node Kubernetes cluster that detects the host's
Intel hardware and configures it. The aim of this work is to make Intel's own stacks (EAS,
enterprise-inference and the Enterprise Agent Toolkit) easy to install and evaluate on one
machine, and to report back what we find while doing it.

## What is new since 2026-11-01

**A second Raptor Lake client host is measured.** wildct is an i7-13700 (8 P-cores and
8 E-cores, AVX-VNNI, no AVX-512 or AMX) with a UHD 770 iGPU and two NVIDIA cards. The box
owner opts all three GPUs out, so its rows are CPU rows. Each ran on release 0.1.89 under the
[wildct pre-registration](../../EXPERIMENTS.md#wildct-pre-registration-client-raptor-lake-i7-13700-2026-10-20).
Four rows have run there so far, and three of them are SUPPORTED:

- Detection: a plain `k3d-gpu up` found the hybrid P/E cores and the six ISA extensions the launcher labels, and nothing the host lacks. `k3d-gpu test` put its P-core pod on CPU 0 and its E-core pod on CPU 17, and `up` named both GPU vendors as detected and opted out [ledger:H52w].
- Agent sandboxes, H18 replicated: in 3 of 3 runs `k3d-gpu sandbox up` kept the sandbox on an E-core behind default-deny egress. The host's tailscale and LAN addresses were refused from the sandbox, and PyPI was reachable [ledger:H18w].
- Shared-prefix agents, H25 replicated: with OVMS's shipped prefix caching on, an added agent's first chunk arrived 14.3x sooner than with it off (CI 13.38-14.89), under matched `typical` load [ledger:H25w]. This measures an OVMS feature; k3d-gpu enables nothing.

The fourth, H34w, is INCONCLUSIVE: with H34 replicated there under matched `typical` load,
OVMS's CPU Fast Draft against the target alone resolved neither the pre-registered gain nor its
exclusion, so the i7-13700 class gets no CPU draft by default. The rest of the host's rows
(H28w, H57wa, H57wm, H57wb and the engine-choice row H59w) are pre-registered and not yet run.
Until a row for the i7-13700 class says otherwise, that class keeps OVMS as its CPU engine.

**H62 at Intel's GA tags is pre-registered.** Intel published EAS
[v1.2.0](https://github.com/intel/enterprise-ai-solutions/releases/tag/v1.2.0), EAT
[release-2.0.0](https://github.com/intel/enterprise-agent-toolkit/releases/tag/release-2.0.0)
and enterprise-inference [v1.1.0](https://github.com/intel/enterprise-inference/releases/tag/v1.1.0)
on 2026-11-06. H62r repeats H62 on those tags: k3d-gpu's CPU stack against Intel's
documented single-node default (EAS v1.2.0 installing EAT 2.0 with Kubespray), each in its own
KVM guest of one shape on the Xeon 6741P, inside EAT's banking flow on Gemma 4 26B-A4B's QAT
weights. Its predictions are H62's, copied verbatim
([H62r pre-registration](../../EXPERIMENTS.md#h62r-pre-registration-h62-at-the-ga-tags-eas-v120-with-eat-20-on-the-xeon-6741p-2026-11-06)).

**Pins move on their own, and each move is checked before it merges.** One registry,
`pins/registry.toml`, now owns every version, digest and model revision that k3d-gpu's own
files and its EAT stock path pin. The Freshness workflow proposes each move to upstream's
newest stable release, every 6 hours, as one pull request per group ([pins/README.md](../../pins/README.md)).

- The merge policy is computed from the diff. A patch or minor move of a plugin or a CPU engine merges on its own once CI and the canary pass. A minor move of a component that a SUPPORTED ledger row measured (OVMS and llama.cpp today) waits for a person, as do a major, a host driver and an EAT stock pin.
- The Canary workflow runs nightly and on every pin change ([canary/README.md](../../canary/README.md)). It serves a pinned Qwen3-0.6B on vLLM CPU, OVMS and llama.cpp the way the launcher runs them. It checks every flag and environment variable the launcher emits against the vLLM XPU, llm-scaler, Gaudi vLLM, llama.cpp SYCL and Vulkan, DLStreamer, sglang Xeon, NPU UMD and OVMS images, and it starts the node image the way `up` does. It runs on GitHub-hosted runners, so it needs no device.
- The hourly bot sweep merges one such pull request at a time, only while main's CI is green, and then dispatches one batched release.
- A pre-registered row's arms stay put. A PENDING row's runner pins move only by a dated ledger amendment, so H59w's registered llama.cpp build and drafter-conversion packages are held. EAT 1.x's pin holds below `release-2.0.0`, which removed the installer `eat-up.sh` drives; EAT 2.0 goes through `k3d-gpu eas`.

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

## What is proven

SUPPORTED rows, each scoped to its host:

- On the Xeon 6741P: the CPU engine choice [ledger:H59], DRAM-resident serving of Qwen3.8-Flash-Next [ledger:H60], and H25 replicated [ledger:H25x].
- On the i9-13900T: sixteen rows, among them EAT `release-1.0.3-hotfix` deployed and served on a default k3d-gpu cluster, n=1 [ledger:H1b], EAT's NRI balloons chart installing over a k3d-gpu cluster [ledger:H14], and agent sandboxes behind default-deny egress [ledger:H18]. The full list is in the [technical report](technical-report.md#supported-quotable-scoped-to-the-i9-13900t-h25x-h59-and-h60-to-the-xeon-6741p-h52w-h18w-and-h25w-to-wildcts-i7-13700).
- On wildct, an i7-13700 client host: detection of its hybrid cores and ISA extensions with its GPUs opted out [ledger:H52w], H18 replicated, agent sandboxes on its E-cores behind default-deny egress [ledger:H18w], and H25 replicated, an added agent's first chunk 14.3x sooner with OVMS's shipped prefix caching on [ledger:H25w].

## Two results that help EAT

Both are stated as the ledger graded them. Inside EAT's banking flow
on the Xeon 6741P, EAT 1.1.0's stock vLLM CPU arm delivered at least as many tasks within
the turn budget as k3d-gpu's CPU stack (H50 REFUTED), and EAT 1.1.0's move to vLLM CPU
v0.28.0 is past the v0.24.0 CPU attention hang that stalled EAT 1.0.3's stock arm in H54a.

## What is still open

- **The Keycloak argv secret.** F3 stays REFUTED. `k3d-gpu eas up` now patches enterprise-inference's `keycloak_config` role so that neither the admin password nor the client secret is a `kcadm.sh` argument ([State and secrets](eas.md#state-and-secrets)). The patch is checked with a `ps` sampler against a stand-in for kcadm; no live run has checked it yet. A fresh `eas up --agent-toolkit` with F3's secret scan would.
- **Intel's default install in a guest (H62, H62r).** H62's install stopped where `cpu: auto` left too little CPU for EAT's sandbox router. H62r's pre-registration traces that, from source at the GA tags, to EAS's topology probe reading a KVM guest's threads as one per core, and re-runs the row at those tags.
- **k3d-gpu gaps found on the Xeon.** `/dev/dri/by-path` for oneCCL across B70s (H53); the fit refusing rather than falling back to the CPU, and no DRAM bandwidth cached on a new host (H58); placing one-card arms on one card (H54b, H54c).
- **Replications.** H59 on the i9-13900T (H59d) is INCONCLUSIVE: llama.cpp's arm was OOMKilled at the fit's pod limit and not deliverable in two holds, so no statistic was scored. H59 on the i7-13700 (H59w) is PENDING, as are the wildct client-host rows after H34w. H34 on the i7-13700 (H34w) is INCONCLUSIVE: the CPU Fast Draft's gain over the target alone was neither the pre-registered win nor ruled out, so that class keeps no default CPU draft. Until a row is SUPPORTED for its class, those CPU classes keep OVMS.
- **EAT 1.1.0 through `eat-up.sh`.** Pinned since 2026-10-29 and pre-registered as F4; not run yet.
- **EAS upstream moves.** Intel tagged EAS `v1.2.0` on 2026-11-06; `k3d-gpu eas` still defaults to EAS `main` at `97f6157`, with `v1.2.0-rc.1` as an opt-in profile. EAS #61 merged on 2026-10-30 (EAS `main` `0f7bc2b`). The pin stays at `97f6157`; moving past #61 needs patch 0002 regenerated. EAS #50 and #52 (OpenShift) are open and would move 0006. EAS #71 (teardown hang) is open; `eas down` uses its workaround.
- **ERAG and Arc under EAS.** EAS's Enterprise RAG layer is not wired into `k3d-gpu eas` yet, and EAS has no Intel GPU path on this branch; Arc and Gaudi stay with `k3d-gpu agent up`.

## Related

| If you want to… | Go to |
|---|---|
| Install EAS (EAT 2.0) on k3d-gpu | [eas.md](eas.md) |
| See the upstream findings in one table | [intel.md, Upstream findings](../intel.md#upstream-findings) |
| Read every verdict and its scope | [technical-report.md](technical-report.md) |
| See the patch pack, its checks and the issue drafts | [upstream-eat.md](upstream-eat.md) |
| Check a number | [EXPERIMENTS.md](../../EXPERIMENTS.md) |
