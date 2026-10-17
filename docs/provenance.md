# Provenance search: what k3d-gpu claims, and who did it first

Search date: **2026-10-16**. Method: each claim is split into
elements. Each element is searched with at least three query families across
GitHub, the web, arXiv and Google Patents. Then two tests run against what was
found: **anticipated** (one source teaches every element) and
**taught-as-combination** (a competent engineer *would* combine the found
sources and expect it to work).

Every verdict below is relative to the corpus searched, never absolute. The
raw machine logs for the GitHub, arXiv, Google Patents and DuckDuckGo venues
are in [`docs/provenance/`](provenance/). In those files, each query has one
`===` line with the date, the venue, the query and the hit count, followed by
every hit reviewed. Exa, WebSearch and SearXNG have no raw log file: they are
recorded only as summary rows in §6 (the 8 SearXNG queries as one row, without
per-query text).

## Verdicts at a glance

| Claim | Anticipated? | Taught as a combination? | Plain verdict |
|---|---|---|---|
| **C1** one-command local k8s that auto-detects NVIDIA + Intel GPU/iGPU, NPU, Gaudi, DSA, IAA, QAT, AMX | No | **Yes, largely** | Nobody found ships this exact set in one local launcher. But Intel's device-plugins operator + NFD already teach the Intel half, and local NVIDIA launchers are common. The new part is k3d-in-Docker engineering, not the idea. |
| **C2** automatic P-core/E-core pod pinning in a local k8s | No | **Yes** | nri-plugins v0.7.1 (2024-09-23) release notes teach the exact mechanism. `kubernetes-sigs/dra-driver-cpu` exposes `coreType` too. No one found *packages* it into a local or dev cluster, and Intel's own edge stack says it does not tell P and E cores apart. |
| **C3** Intel Enterprise Agent Toolkit on one non-Xeon machine via k3s/k3d | No | **Yes** | No one found runs EAT on k3s/k3d (issues, forks, code all empty). But Intel's successor installer has a bring-your-own-kubeconfig mode, so this is an expected use. **Not yet demonstrated by us either.** |
| **C4** OVMS stack that picks iGPU/NPU/CPU from k8s allocatable resources | No | **Yes, strongly** | **Prior art for the policy:** OVMS 2026.3 already picks dGPU → iGPU → CPU itself and leaves NPU out. AIppliance-Magic-Stick gates k8s model targets on allocatable `gpu.intel.com/xe`/`i915`. Our change only moves the choice to the scheduler layer. Also, our claim overstates the code: it never picks the NPU on its own. |

**Prior art found, said plainly:**

- **C4:** the canonical policy already exists. OVMS's own device auto-detect (v2026.3) uses the same order we use.
- **C2:** the mechanism already exists. nri-plugins `preferCoreType` (2024) and dra-driver-cpu `coreType` (2025) are published primitives with worked examples. Intel's edge blueprint has described P/E core allocation for K3s since 2026-05-25, but its scripts do not implement it.

None of these anticipates a claim as worded. All of them shrink what we can honestly call new.

---

## 1. Corpus and coverage

| Venue | How | Queries | Status |
|---|---|---|---|
| GitHub repositories | `gh search repos` (words, not phrases) | 65 | OK. Log: `c1..c4_repos.txt` |
| GitHub code | `gh search code` | 23 | OK. Log: `c_code.txt` |
| GitHub issues/PRs | `gh search issues` | 18 | OK. Log: `issues.txt` |
| GitHub direct reads | `gh api` READMEs, releases, commits, forks; shallow clones | about 40 reads | OK. Logs: `readmes_*.txt`, `eat_fork*.txt` |
| arXiv | `export.arxiv.org/api/query` | 26 | OK. Log: `arxiv.txt` |
| Google Patents | `patents.google.com/xhr/query` | 7 | Search OK. Abstract pages often returned **503**. Log: `patents.txt`, `patent_abstracts.txt` |
| Exa (semantic web) | `web_search_exa` + `web_fetch_exa` | 9 searches, 2 fetches | OK. Summary rows in §6 only; no raw log file |
| WebSearch (built-in) | 4 queries | 4 | OK. Summary rows in §6 only; no raw log file |
| SearXNG | `search_many` | 8 | **Degraded.** Bing-only junk (restaurants, car dealers), nothing relevant. One summary row in §6; no raw log file or per-query text. Logged as a gap. |
| DuckDuckGo HTML | `html.duckduckgo.com` | 6 | **Unreachable.** A bot-challenge page, 0 results. Log: `ddg.txt` |

**Not searched (coverage gaps):**

- GitLab, Codeberg, Gitee and Bitbucket.
- Chinese-language web (CSDN, Zhihu).
- Docker Hub image descriptions.
- Artifact Hub, beyond one incidental hit.
- Conference video (KubeCon talks were seen by abstract only).
- Google Scholar and the IEEE/ACM libraries (arXiv only).

Limits of the GitHub venues:

- **Code search** indexes only default branches. It excludes most forks, and it has file-size and recency limits.
- **The first GitHub batch was discarded.** I wrongly searched multi-word queries as exact phrases, which returned near-zero hits. It was re-run as word queries, and only the re-run is logged.

**Self-hits excluded:** `88plug/k3d-gpu` (this repo) and its PRs #5–#7 came up in Exa and WebSearch. They are the claimant, not prior art.

---

## 2. C1: one-command local cluster with NVIDIA plus the Intel accelerator family

**Elements:**

- E1: a local, Docker-hosted k8s launcher (k3d), with one command (`up`).
- E2: detects accelerators on the host.
- E3: NVIDIA GPU.
- E4: Intel GPU and iGPU (`i915`/`xe`).
- E5: Intel NPU.
- E6: Gaudi.
- E7: DSA, IAA and QAT.
- E8: AMX labels.
- E9: plugins baked in and deployed with no `kubectl apply`.

**Query families:**

- (a) launcher names: `k3d gpu`, `kind gpu`, `nvkind`, `minikube gpu`, `microk8s gpu`, `k3s gpu`, `k3d-gpu`;
- (b) vendor and device jargon: `intel device plugins`, `npu kubernetes`, `gaudi kubernetes`, `habana kubernetes`, `intel qat kubernetes`, `amx kubernetes`, `igpu kubernetes`, `node feature discovery intel`;
- (c) concept phrasing: `kubernetes gpu autodetect`, `local cluster accelerators`, `local kubernetes gpu`;
- (d) code: `gpu.intel.com/i915 nvidia.com/gpu k3d`, `habana.ai/gaudi k3d`, `qat.intel.com k3d`, `npu.intel.com k3d`, `k3d cluster create intel-gpu-plugin`, `intel-device-plugins-operator k3s`;
- (e) Exa, WebSearch and arXiv natural-language variants.

### C1 closest findings

| Source (date) | What it does | Missing elements |
|---|---|---|
| **QualityMinds/AIppliance-Magic-Stick** (repo 2026-05-26; `docs/reference/gpu-operators.md` first commit 2026-09-23) | K3s appliance. NFD vendor labels (`pci-10de`, `pci-8086`) auto-enable NVIDIA, AMD and Intel GPU operator modules "only when compatible hardware is present". A target becomes selectable only after the vendor resource is allocatable. | E1 (bare-metal K3s installer, not Docker-hosted), E5, E6, E7, E8. Custom licence. |
| **intel/intel-device-plugins-for-kubernetes** operator + NFD rules (long-standing; plugin v0.36/0.37 in 2026) | One operator deploys DSA, GPU, IAA, QAT, NPU and SGX plugins. NFD labels nodes so each plugin runs only where its hardware is. | E1, E3, E6, E9. It is a component, not a launcher. |
| **open-edge-platform/edge-node-infrastructure-blueprint** `install-intel-device-plugins.sh` (2026-05-25) | Air-gapped K3s + NFD + Intel GPU and NPU plugins on a single Core/Core Ultra node. | E1 (bare-metal USB image), E3, E6, E7, E8 |
| **Edge Microvisor Toolkit Standalone Node** (Intel, 2025–26) | Immutable OS + K3s + Intel GPU plugin, for 12th/13th-gen Core and Core Ultra. | E1, E3, E5–E8 |
| **intel/intel-technology-enabling-for-openshift** (2023–) | Gaudi, Flex/Max GPU, QAT, SGX and DSA on OpenShift. | E1 (production OpenShift), E3, E5 |
| **NVIDIA/nvkind**, **kinder** (2026-03), **Batteries Included** `bi start-local`, **minikube `--gpus=all\|nvidia\|amd`** | Local Docker-hosted cluster with GPU passthrough and auto-detection. | E4–E8 (NVIDIA, sometimes AMD, only) |
| **tracebloc/client** `install-k8s.sh` (2026-09) | One command: k3d or k3s, GPU auto-detect, device plugin. | E4–E8. Its docs say outright that Intel GPUs "install CPU-only". |
| **randomvariable/tilt-common** k3d-gpu PR #1, **justinthelaw/k3d-gpu-support**, **jakkaj/k3d-gpu** and about 10 other k3d-gpu images | k3d + NVIDIA. | E4–E8 |
| **rothgar/ai-on-k8s** (2026-07) | Talos manifests for NVIDIA, AMD, Intel Arc and Tenstorrent. | E1, E2, E5–E8 |

arXiv (8 queries on k8s + accelerators, local, NPU, Gaudi): nothing relevant.
Patents: the "device plugin … GPU AND NPU … cluster" queries return Chinese
heterogeneous-scheduling patents for data centres. None covers a local
launcher.

### C1 tests

- **Anticipated:** no. No single source covers E1+E3+E5+E6+E7.
- **Taught-as-combination:** **yes, for the core idea.**
  - Intel's operator README says to deploy with NFD so each plugin lands only where its hardware is. That is E2 and E4–E8 in one sentence.
  - A k3d/kind NVIDIA image is everyday homelab material (more than ten repos).
  - An engineer who wants one local cluster for both would combine the two and expect it to work.
  - AIppliance-Magic-Stick has already done the NVIDIA + Intel GPU half on K3s, gated by NFD.
- **What the combination does not teach** (the honest delta). These are k3d-in-Docker problems you only hit by building it:
  - telling `/dev/accel` NPU from Gaudi by PCI vendor;
  - bind-mounting the host's `/dev/char` so DSA/IAA work queues resolve inside a privileged node container;
  - the `xe.force_probe` doctor hints;
  - baking the manifests into the k3s auto-deploy directory so `up` needs no `kubectl apply`.

  They are engineering, not invention.

**Verdict (C1):** closest found: QualityMinds/AIppliance-Magic-Stick (K3s,
NFD-gated NVIDIA/AMD/Intel GPU auto-activation) plus Intel's device-plugins
operator (DSA/GPU/IAA/QAT/NPU behind NFD). They differ in this: neither is a
Docker-hosted local launcher, and neither covers NPU + Gaudi + DSA/IAA/QAT +
AMX together with NVIDIA. No anticipating reference in the searched corpus
(venues and dates in §1; gaps: GitLab/Gitee, Docker Hub, CN web, default-branch-only
code search). It is taught as a combination, so the claim is "first packaging
found", not "new idea".

---

## 3. C2: automatic P-core/E-core pod pinning in a local k8s

**Elements:**

- E1: detect a hybrid CPU (`/sys/devices/cpu_core` + `cpu_atom`).
- E2: a per-pod core-type choice. Here it is a pod label, `k3d-gpu.io/cores=performance|efficient`.
- E3: the mechanism is NRI balloons `preferCoreType`.
- E4: unlabelled pods share all idle CPUs.
- E5: shipped pre-wired in a local/dev cluster launcher (k3d), deployed on `up`.

**Query families:**

- (a) mechanism names: `preferCoreType`, `nri balloons`, `nri-plugins`, `nri resource policy`, `cri-resource-manager`, `balloons` code;
- (b) hardware jargon: `p-core e-core`, `alder lake kubernetes`, `raptor lake kubernetes`, `big.little kubernetes`, `heterogeneous cores containers`, `e-cores containers`;
- (c) concept phrasing: `hybrid cpu kubernetes`, `performance efficient cores kubernetes`, `core type pinning`, `cpu pinning k3s`, `efficiency cores kubernetes` (issues);
- (d) arXiv `"P-cores" AND "E-cores"`, `"Alder Lake" AND scheduling`, `kubernetes AND "big.LITTLE"`;
- (e) patents: `(container OR pod) (kubernetes OR orchestrat*) ("performance core" OR "efficiency core") pin*` and `"core type" (label OR annotation) scheduling`.

### C2 closest findings

| Source (date) | What it teaches | Missing elements |
|---|---|---|
| **containers/nri-plugins v0.7.1 release notes** (2024-09-23; PR #354; core-type sysfs support from commit `9a580472`, 2024-03-12) | Balloon types with `preferCoreType: efficient` for `logging`/`monitoring` namespaces and `performance` for a high-priority type. This is **the mechanism E3, with a worked example**. | E5 (no local-cluster packaging). E2 is shown by namespace, not by pod label, though label `matchExpressions` are a documented balloon feature. |
| **kubernetes-sigs/dra-driver-cpu** (`dra.cpu/coreType` = `p-core`/`e-core` since the first kubelet-plugin commit `97d1e510`, 2025-08-05; a third value `standard` was added later and is present at `f267a051` in `pkg/cpuinfo/cpuinfo.go` and `docs/user/device-attributes.md`; docs show a CEL selector `coreType == "p-core"`) | A second, independent primitive: DRA claims for P-cores. | E3 (a different mechanism), E4, E5 |
| **open-edge-platform/edge-node-infrastructure-blueprint** design proposal (2026-05-25) | States the image "supports … P/E core allocation" (`infrastructure/docs/design-proposals/en-infrastructure-blueprint.md:13` @66c85ab). Describes "P/E core allocation via kernel boot parameters (`isolcpus`, `cpuset`)" (line 56) and "P/E core pinning via `isolcpus` and `cpuset`" in cloud-init (line 285) for K3s on Core Ultra, and a "P/E core pinning functionality" test step (line 288). `tools/system-info/system-info.sh` detects and reports P-core and E-core counts from sysfs `core_type`. | Host-level boot params, not per-pod. **Pinning not implemented**: grep of its install/provision scripts for `isolcpus\|core.pin\|p/e` is empty (@66c85ab). Detection exists only as a reporting tool. |
| **Intel Edge Orchestrator 3.0 release notes** (`open-edge-platform/edge-manage-docs` `docs/release_notes/3.0/rel_notes_3.0.rst:507` @bd2478b, 2025-09-16) | States "The Product does not differentiate P-cores and E-cores in Intel Core processor-based platforms." | (Intel's own gap statement.) |
| **kubernetes/kubernetes#106157** (2021, closed stale) | A feature request for P/E core grouping; SIG Node asked for use cases. | Request only |
| **fabriziosalmi/proxmox-lxc-autoscale** (`p-cores`/`e-cores` groups, 2026-03/09); **canonical/lxd#16404** (open, 2025-09) | P/E pinning for LXC containers | Not k8s |
| **intel/enterprise-agent-toolkit** NRI role (@0ecf67a) | Installs balloons on its own cluster, with no core-type use. Grep for P/E/core type across the clone is empty. | E1, E2, E3 (core type) |

- **Code search:** `preferCoreType` returns only nri-plugins itself and operatorhub copies of its CRD. No consumer repo on any default branch (`c_code.txt`).
- `gh search code` for `isolcpus` / `preferCoreType` / `balloons` across `open-edge-platform` found no core-type use.
- `intel/cri-resource-manager` has no `CoreKind`/`PreferCoreType`.
- arXiv: 1 hit (GC scheduling on asymmetric multicore, 2403.02200), not k8s.
- Patents: data-centre heterogeneous scheduling (Intel "software defined super cores", CN core-binding for cloud-native apps). None packages core-type pinning for a local k8s. Four candidate abstracts were **not reviewed** (gap): CN119938341B and KR20250175472A returned 503; CN116820777A returned no abstract, then 503 on retry; CN121433917A returned no abstract (`patent_abstracts.txt`).

### C2 tests

- **Anticipated:** no. No source covers E5.
- **Taught-as-combination:** **yes.**
  - The nri-plugins release notes hand over the exact configuration.
  - k3s's containerd template turns NRI off only when k3s runs in a user namespace (`pkg/agent/templates/templates.go` on k3s master: `{{ if .IsRunningInUserNS }} … disable = true`). A privileged node keeps containerd's default.
  - A competent engineer who wants E-core background pods on a hybrid dev box *would* install the balloons chart, and could reasonably expect it to work.
- **What remains is packaging and defaults:**
  - auto-detect hybrid and gate the DaemonSet with a node label;
  - one pod label instead of namespace lists;
  - a shared balloon so unlabelled pods keep every idle CPU;
  - surviving inside a k3d node container that sees the host's PMUs.

**Verdict (C2):** closest found: containers/nri-plugins v0.7.1 `preferCoreType`
(2024-09-23) and kubernetes-sigs/dra-driver-cpu `coreType` (2025-08-05). They
differ in this: they are primitives with examples, and neither is packaged or
auto-enabled in any local/dev cluster. Intel's edge K3s blueprint describes
P/E core allocation and reports P/E core counts, but implements no per-pod or
core_type-based pinning (its `tools/power-tuning/set_power_profile.sh` @66c85ab,
lines 501-515, confines host work to hard-coded low-index CPUs via `intel_lpmd`
at 5-20 W, which is host-wide, not per pod and not core_type-aware), and
Intel's Edge Orchestrator says it does not tell P from E. No anticipating reference in the searched corpus (venues and
dates in §1; gaps: four patent abstracts unread, CN web, non-default branches).
It is taught as a combination, so the claim is "first packaging found".

---

## 4. C3: Enterprise Agent Toolkit on one non-Xeon machine via k3s/k3d

**Elements:**

- E1: the EAT stack (LiteLLM gateway, observability, Redis/pgvector, agent sandbox, vLLM).
- E2: a single machine.
- E3: a non-Xeon (client) CPU.
- E4: k3s/k3d as the substrate instead of Kubespray.

**Query families:**

- (a) names: `enterprise agent toolkit`, `enterprise-agent-toolkit`, `intel enterprise inference`, `deploy-agentic-stack.sh` (code);
- (b) substrate: `enterprise-inference k3s`, `intel agentic k3s`, `agentic stack k3d`, `litellm k3d`, `enterprise-agent-toolkit k3s` (code);
- (c) sister stacks: `opea k3s`, `opea kind`, `opea minikube`, `GenAIExamples k3s`, `opea laptop`, `intel ai pc kubernetes`;
- (d) issues in intel/enterprise-agent-toolkit and intel/enterprise-inference: `k3s`, `laptop`, `kind`, `single node`;
- (e) forks: all 21 forks of intel/enterprise-agent-toolkit, with branch names checked for k3s/k3d/kind/laptop/client/npu/gpu (`eat_fork_branches.txt`);
- (f) Exa, WebSearch and arXiv `"agentic" AND kubernetes AND "single node"`.

### C3 closest findings

| Source (date) | What it shows | Missing elements |
|---|---|---|
| **intel/enterprise-ai-solutions** `es_auto_installer.sh` (@7743bac, 2026-10-16) | A non-interactive BYO cluster mode, `existing_kubernetes: <kubeconfig>` (l.1327–1342), plus an `env/local/global_config.yaml # your laptop / dev box` convention. Open EAT PR #19 (2026-10-11) moves EAT onto it. | E4 not stated (its own path is Kubespray). Xeon is the stated target. |
| **EAT Docker Compose path** (`docker/README.md`, @0ecf67a) | A single-machine "development" deployment | E4 (no k8s). Requires 128 GB RAM and 96+ cores, so E3 is ruled out in practice. |
| **OPEA ChatQnA "Single Node environment (Minikube)"** (OPEA 1.1 docs; removed from GenAIExamples in #1373) | Intel's sister GenAI stack on a local single-node k8s | E1 (ChatQnA, not EAT). Xeon images. |
| **EAT docs** `agent-sandbox.md` l.206 (Kind/Minikube image-load hint) and `kuberay-config.yaml` ("dev / single-node laptop" sizing comment) | Intel mentions local clusters and laptops for parts of the stack | Whole-stack k3s path, E3 |

**Absence evidence (the lookups ran and came back empty):**

- `gh search issues k3s|laptop|kind --repo intel/enterprise-agent-toolkit`: 0 relevant hits. The only hit was #9, about auth.
- `gh search issues "single node"|k3s --repo intel/enterprise-inference`: 0.
- `gh search code "enterprise-agent-toolkit k3s"`: 0.
- No fork branch names a k3s, k3d, kind or laptop variant.
- `grep -rniE 'k3s|k3d|minikube|microk8s|laptop|core ultra'` over the three Intel clones matched only the hits listed above.

### C3 tests

- **Anticipated:** no.
- **Taught-as-combination:** **yes.**
  - The successor installer's BYO-kubeconfig mode *invites* any conformant cluster, and k3s is one.
  - OPEA has already shown its sister stack on minikube.
  - What the combination does not foresee are the concrete breakages found in the deep-dive: `+k3s1` gitVersion detection, k3s ServiceLB versus ingress-nginx hostPorts, the Istio `global.platform=k3d` CNI paths, the missing `ei-inference-eligible` label, and the balloons ownership collision. They are fixes, not an inventive step.

**Verdict (C3):** closest found: intel/enterprise-ai-solutions BYO
`existing_kubernetes` mode with an `env/local` "laptop / dev box" convention,
and OPEA ChatQnA on single-node minikube. They differ in this: neither runs the
EAT stack on k3s/k3d, and both assume Xeon. No anticipating reference in the
searched corpus (venues and dates in §1; gaps: Intel-internal forks, private
repos, CN web). **Certification level: asserted only.** We have not run it:
branch `claude/eat-on-k3d` is in flight, and no live probe is allowed in this
wave. Nothing here may be called "runs on k3d" until the probe in §7 passes.

---

## 5. C4: OVMS that picks iGPU/NPU/CPU from k8s allocatable resources

**What the code on `claude/agent-stack` (commit `8385dc2`, `agent_device()` in `scripts/k3d-gpu`) actually does:**

- It reads `.status.allocatable` of every node.
- It picks `gpu.intel.com/xe`, then `gpu.intel.com/i915`, else CPU.
- **It never picks the NPU on `auto`.** The NPU is used only when `K3D_GPU_AGENT_DEVICE=NPU` is set. `GPU` or `NPU` set explicitly fails if the cluster advertises no such device.
- It sets the resource limit and `--target_device` to match.

**Elements:**

- E1: OVMS with an OpenAI-compatible endpoint.
- E2: on k8s.
- E3: device chosen from node allocatable extended resources.
- E4: the order xe > i915 > CPU.
- E5: NPU (opt-in only).
- E6: the request is wired so the device gets mounted.

**Query families:**

- (a) names: `ovms kubernetes`, `openvino model server helm`, `openvino operator`, `openvino model server k3s`, `ovms openai`;
- (b) device jargon: `openvino model server npu`, `openvino kubernetes npu`, `ovms igpu`, `openvino npu device plugin`, `ovms auto device`, `openvino kubernetes gpu`;
- (c) code: `npu.intel.com/accel target_device`, `npu.intel.com/accel ovms`, `gpu.intel.com/i915 target_device`, `gpu.intel.com/xe openvino`, `allocatable npu.intel.com`, `MM_OVMS_TARGET_DEVICE`, `target_device AUTO kubernetes`;
- (d) OVMS issues `npu`, `kubernetes npu`;
- (e) arXiv `OpenVINO AND serving`, `"OpenVINO Model Server"`;
- (f) patents: `(OpenVINO OR "inference server") (NPU …) (kubernetes OR "extended resource") select* device`.

### C4 closest findings

| Source (date) | What it teaches | Difference |
|---|---|---|
| **OVMS v2026.3 release notes** (2026-08-04) | "automatic detection of … target device … Default target device is determined based on the priority: dGPU if present, iGPU if present, CPU." The NPU is not in the default order. | **The same policy as ours (E4, E5), inside the process.** It cannot see a device that k8s never mounted, so it lacks E3/E6. |
| **OpenVINO AUTO device** docs | In-process device selection; "NPU is currently excluded from the default priority list" | Same as above |
| **QualityMinds/AIppliance-Magic-Stick** `docs/reference/gpu-operators.md` (2026-09-23) | A model target is "selectable only after its provider is `Ready` and the corresponding allocatable resource exists". "Intel … runtime resolves `gpu.intel.com/xe` or `gpu.intel.com/i915`". | E3 + part of E4 + E6, but **the user picks** the target, the runtime is vLLM-XPU not OVMS, and there is no NPU. |
| **Intel edge-ai-libraries ChatQnA-Core / VSS Helm charts**, metro-ai-suite (2025–26) | OVMS on k8s with `device: CPU\|GPU\|NPU` and `key: gpu.intel.com/xe\|npu.intel.com/accel` | Manual. You "identify by running `kubectl describe node`", so no E3. |
| **intel/enterprise-inference** `model_manager/core/download.py:43,64` (@770870c) | OVMS `--target_device` from `MM_OVMS_TARGET_DEVICE`, defaulting to CPU | Manual env var, no E3 |
| **Intel "Inference Scaling with OVMS in Kubernetes"** (2021-05-07); **openvinotoolkit/operator** | OVMS pods with a `gpu.intel.com/i915: 1` limit | Manual |
| Homelab manifests (derio-net/frank iGPU DRA tier; kavishwar-khankari, maxjeffwell OVMS deployments) | OVMS pinned to an Intel GPU | Manual |

arXiv: AI-PC OpenVINO serving papers (2608.19147, 2610.07219) are about
distributing large models over iGPU fleets, not k8s device choice. Patents:
nothing on choosing an inference device from k8s extended resources.

### C4 tests

- **Anticipated:** no.
- **Taught-as-combination:** **yes, strongly.**
  - The canonical answer is OVMS's own detector. Its priority order is ours.
  - AIppliance-Magic-Stick already gates k8s targets on allocatable `xe`/`i915`.
  - Lifting OVMS's order to the scheduler, so the pod requests what the node advertises, is the obvious next step. An engineer whose OVMS pod sees no `/dev/dri` *would* do it.
- **Measured delta:** none yet. No benchmark exists in this wave.

**Verdict (C4):** closest found: OVMS v2026.3 in-process auto target device
(dGPU → iGPU → CPU, NPU excluded) and AIppliance-Magic-Stick's
allocatable-gated Intel `xe`/`i915` targets. They differ in this: OVMS chooses
after scheduling, so it cannot request the device, while AIppliance leaves the
choice to the user and serves vLLM, not OVMS. No anticipating reference in the
searched corpus (venues and dates in §1; gaps: Docker Hub, CN web). **The claim
as worded must change:** "auto-selects iGPU/NPU/CPU" is false for NPU. The
accurate wording is "Intel GPU (xe, then i915) else CPU, NPU on request: OVMS's
own default order, applied at scheduling time".

---

## 6. Web search log (Exa, WebSearch, SearXNG)

| # | Venue | Query | Reviewed | Closest |
|---|---|---|---|---|
| 1 | Exa | tool that creates a local k3d or kind Kubernetes cluster with Intel GPU and NVIDIA GPU device plugins automatically detected | 15 | Batteries Included, tracebloc, tilt-common k3d-gpu, kinder, nvkind (all NVIDIA-only); self excluded |
| 2 | Exa | Edge Microvisor Toolkit standalone k3s Intel GPU device plugin single node Core Ultra | 12 | EMT standalone; ENIB `install-intel-device-plugins.sh`; Edge Orchestrator 3.0 "does not differentiate P-cores and E-cores" |
| 3 | Exa | pin Kubernetes pods to P-cores or E-cores on Intel hybrid CPU … NRI balloons preferCoreType | 15 | nri-plugins balloons docs; optimization-zone; k8s#106157; CRI-RM balloons (no core type) |
| 4 | Exa | homelab k3s Intel E-cores P-cores kubernetes workloads cpuset pinning blog | 15 | proxmox-lxc-autoscale P/E groups (LXC); Qubes and Proxmox guides; no k8s |
| 5 | Exa | Intel Enterprise Agent Toolkit k3s single node laptop deployment | 15 | EAT single-node (Kubespray) and Compose docs; nothing on k3s |
| 6 | Exa | OpenVINO Model Server Kubernetes … target_device selected automatically from node resources | 15 | Intel 2021 OVMS-on-k8s article; OVMS accelerators doc; AUTO device doc |
| 7 | Exa | open-edge-platform … chat question and answer core helm OVMS GPU NPU device selection | 10 | ChatQnA-Core Helm `gpu.enabled` + manual `gpu.key` |
| 8 | Exa | OPEA GenAIExamples AgentQnA deploy on k3s or kind or minikube single node AI PC | 10 | OPEA 1.1 ChatQnA on minikube; AgentQnA Xeon/Gaudi only |
| 9 | Exa | minikube start --gpus flag supported values nvidia amd intel | 5 | minikube `--gpus` = `all,nvidia,amd` (source `start_flags.go`) |
| 10 | Exa fetch | optimization-zone balloons page; KubeCon EU 2024 "Beyond Default" abstract | 2 | Neither mentions hybrid cores |
| 11 | WebSearch | Kubernetes pod P-cores E-cores Intel hybrid NRI balloons preferCoreType k3s kind local cluster | 9 | nri-plugins docs and releases; no k3s/kind packaging |
| 12 | WebSearch | local Kubernetes k3d kind Intel GPU NPU Gaudi QAT DSA device plugins auto-detect one command | 10 | Self (PRs #5–#7), then Intel device-plugins operator |
| 13 | WebSearch | Intel Enterprise Agent Toolkit k3s OR k3d OR minikube laptop Core Ultra deployment | 9 | Nothing on k3s; Compose path; Intel AI Super Builder (desktop app, not k8s) |
| 14 | WebSearch | OpenVINO Model Server Kubernetes automatically choose GPU NPU CPU based on node allocatable | 20 | Led to the OVMS v2026.3 auto-device release note (then verified via `gh api`) |
| 15–22 | SearXNG | 8 variants of C1 (local k8s + Intel GPU/NPU/Gaudi/autodetect/nvkind/minikube) | 77 raw | **All irrelevant** (Bing returned restaurants, car dealers, k3s.io home). Venue counted as degraded. |

WebSearch answers include model-written summaries. I took no claim from a
summary without checking it at the primary source. The OVMS auto-device text
was checked against the v2026.3 release body, and the nri-plugins example
against the v0.7.1 release body.

## 7. Honest weak points

1. **The provenance results are mostly about packaging.** Every claim is taught as a combination. What is defensible is "first found to package X for a local cluster", plus a list of named k3d-specific fixes, not a new mechanism. Any README or pitch that says "novel" or "first" without "in the searched corpus, as packaging" over-claims.
2. **C4's wording is wrong.** The code never auto-picks the NPU. The device order is OVMS's own default order, applied one layer up.
3. **C3 is only asserted.** No run exists. The exact probe needed, once live work is allowed:
   - `k3d-gpu up` (with servicelb disabled and 80/443 mapped, per the deep-dive);
   - then the EAS installer with `existing_kubernetes: $(k3d kubeconfig write gpu)`;
   - pass condition: `curl -sk https://api.example.com/v1/models` returns the LiteLLM model list, **and** one chat completion streams to `finish_reason=stop`. A Ready node or a 200 on `/health` does not count.

   Also unverified: whether `vllm/vllm-openai-cpu` runs on an AVX2-only client CPU.
4. **C2 has not been checked against multi-node k3d.**
   - Each agent container on one host runs its own balloons plugin over the *same* physical CPUs, so allocations can overlap. Verifying it needs a two-agent cluster and a check that the cpusets do not collide.
   - Probe: `k3d-gpu up` with `--agents 1`, two labelled pods; `docker exec … cat /sys/fs/cgroup/…/cpuset.cpus.effective` per container.
   - Unverified in this wave.
5. **Gaps that could hide prior art:**
   - GitLab, Gitee and Codeberg were not searched.
   - Chinese-language engineering blogs were not searched. Several CN patents on core binding for cloud-native apps exist, and four candidate abstracts were unread: CN119938341B and KR20250175472A (HTTP 503), CN116820777A (no abstract, then 503) and CN121433917A (no abstract).
   - GitHub code search sees default branches only.
   - Docker Hub image READMEs were not searched.
   - Private Intel forks of EAT are invisible.
6. **The first GitHub batch was a methodology error.** Multi-word queries were sent as phrases. The batch was thrown away and re-run, and only the re-run is in the logs.
7. **Saturation is claimed for GitHub and Exa only.** A second backward and forward pass found nothing new:
   - backward from nri-plugins PR #354 to CRI-RM (no core type);
   - forward from the Intel device-plugins operator to EMT, ENIB, Edge Orchestrator and AIppliance;
   - forward from `preferCoreType` to code consumers (none).

   SearXNG and DuckDuckGo gave no usable signal, so web saturation rests on Exa and WebSearch alone.

## 8. Reopen tripwires

Re-run this search if any of these happen:

- A GitLab/Gitee search, or Docker Hub search, turns up a k3d/kind image with Intel NPU or Gaudi plugins.
- nri-plugins or k3s ships a "hybrid" quick start.
- Intel EAS/EAT adds a k3s or laptop profile (watch EAT PR #19 and enterprise-inference PR #22).
- OVMS starts requesting k8s resources itself, for example a KServe runtime that sets device limits from node allocatable.

## H23 prior-art search (2026-10-17)

Search behind [`EXPERIMENTS.md` H23](../EXPERIMENTS.md#h23-pre-registration-2026-10-17),
row P230 below. **Verdict: prior art exists for most of H23.** Watts as an
extended resource (GEOPM + Kueue), power-aware Kubernetes placement (Intel TAS,
Apple), predicted power/thermal slack placement (TAPAS, SmartOClock, ThermE) and
the closed-form PL1/PL2/Tau turbo model (Menra et al.) are all published.
Nothing was found for per-pod watts as DRA consumable capacity set from a
turbo-budget model on client hybrid parts, but the search was degraded
(web-search budget used up, GitHub code search rate-limited after 7 queries,
SearXNG returned only Bing noise). Re-run the DRA and code queries before any
"no prior art" wording.

| Query batch | Tool | Queries or URLs | Result |
|---|---|---|---|
| 1 | SearXNG search_many | "Kubernetes scheduler power budget watts extended resource"; "Kubernetes DRA consumable capacity KEP-5075"; "Intel telemetry aware scheduling thermal power Kubernetes"; "Kubernetes power manager Intel RAPL"; "power capping Kubernetes pods scheduling RAPL paper"; "power as a schedulable resource Kubernetes arXiv"; "thermal-aware container scheduling Kubernetes throttling prediction"; "Kepler kubernetes power-aware scheduling PEAKS" | Bing-only noise, nothing usable |
| 2 | Exa | "Kubernetes scheduling power budget as a resource, pods request watts, power capping" | GEOPM/Kueue `intel.com/power`; Apple plugins; Climatik; TNSM 2025; Compute Gardener; KEP-5517 |
| 3 | Exa | "Intel platform-aware-scheduling telemetry aware scheduling thermal power policy" | TAS power demo (RAPL, 80% of TDP filter) |
| 4 | Exa | "KEP-5075 DRA consumable capacity ResourceSlice shared device capacity" | KEP-5075; examples are bandwidth and memory only |
| 5 | Exa | "TAPAS thermal- and power-aware scheduling LLM inference cloud" | TAPAS, ASPLOS 2025 |
| 6 | Exa | "predict thermal throttling turbo budget PL1 PL2 tau model scheduler headroom" | Intel PL1/PL2/Tau definition; Menra Euro-Par 2026; Michaud CAL 2020; `intel_ips.c`; ThermE |
| 7 | Exa | "DRA ResourceSlice advertising power watts or thermal headroom as consumable capacity for pods" | none for power or watts; OKD GPU "power/thermal profiles" (attribute selection only) |
| 8 | gh search repos | "kubernetes power aware scheduler"; "dra power"; "kubernetes thermal scheduling"; "power capping kubernetes"; "rapl kubernetes scheduler"; "kubernetes extended resource watts"; "power-aware scheduling kubernetes"; "power capping"; "thermal aware kubernetes"; "power aware scheduler"; "kepler scheduler"; "energy aware scheduler kubernetes" | only sustainable-computing-io/power_capping_operator (a stub) |
| 9 | gh search code | "allowMultipleAllocations power watts"; "resourceslice power watts dra"; "dra driver power budget"; "consumable capacity power"; "ResourceSlice watts"; "resource.k8s.io power watts"; "deviceClassName power watts" | 0 hits; the next 6 queries were rate-limited (HTTP 403) |
| 10 | fetch | kubernetes/enhancements#5075; geopm kubecon-na-23 README; Kepler power_capping_operator; SmartOClock; intel/kubernetes-power-manager; Thunderbolt OSDI 2020; Dynamo ISCA 2016; Flex ISCA 2021; arXiv 2610.00267 | confirms the rows below; KEP-5075 beta target 1.36; kubernetes-power-manager discontinued |

Not searched: arXiv, MLSys, OSDI and EuroSys full-text listings; LKML;
wg-device-management notes.

## 9. H28 prior-art search: zero-copy model sharing through image volumes

Searched 2026-10-17. **Verdict:** the mechanism is prior art (image-volume model
delivery, mmap tensor sharing, overlay/EROFS page-cache sharing), so no "first" claim
is made for it. No prior art found (search log [provenance:P280]) that measures
per-pod PSS, RSS and memcg charge, plus time-to-ready, for N co-located agent pods
mmapping one model from an image volume versus a per-pod copy, on containerd 2.3 /
k3s v1.37. Only that measured delta ([ledger:H28], PENDING) may be claimed. Coverage
is moderate: the WebSearch budget ran out, arXiv's API returned HTTP 429 and SearXNG
returned only noise; Exa, GitHub search and direct fetches ran.

Closest neighbours: for measurement, Kliukovkin 2026 [provenance:P283] (time to
first prediction and node disk only, no memory); for memory, Tetris [provenance:P284]
(not OCI, not k8s-native, needs a modified TF Serving).

Search log (P280):

| # | Venue | Query | Closest |
|---|---|---|---|
| 1 | SearXNG search_many (10 queries) | KEP-4639 image volume AI model; image volume GA 1.35 model weights; KServe modelcar page cache; Red Hat image volume LLM mmap; CNCF ModelPack; llama.cpp mmap page cache shared; zero-copy model loading shared page cache pods; ServerlessLLM OSDI 2024; Docker Model Runner OCI GGUF mmap; KitOps ModelKit image volume | Bing noise only |
| 2 | Exa | Kubernetes image volumes OCI artifact to serve AI model weights to pods | P281, KubeAI, Plainsight |
| 3 | Exa | KServe modelcars OCI image model storage sidecar shared page cache memory | P282 |
| 4 | Exa | multiple containers share page cache for same model file mmap LLM memory deduplication | P285, P286 |
| 5 | Exa | Kubernetes v1.36 image volumes stable GA ImageVolume feature gate | P281 |
| 6 | Exa | Tetris memory-efficient serverless inference through tensor sharing USENIX ATC | P284 |
| 7 | Exa | benchmark image volume model pod startup time memory PSS shared page cache llama.cpp multiple replicas same node | P283, P287 |
| 8 | Exa | image volume pods share page cache model weights memory RSS PSS measured N replicas same node containerd | nothing that measures PSS |
| 9 | Exa | erofs page cache sharing across containers merged kernel | P286 |
| 10 | Exa | containerd CRI image volume implementation snapshot view mount per pod KEP-4639 | P288 |
| 11 | Exa | k3s v1.37 release containerd version | P288 |
| 12 | GitHub repo search | "image volume model", "oci model weights kubernetes", "page cache model sharing", "mmap model weights shared", "modelpack" | none relevant |
| 13 | GitHub issue search | "imagevolume memory shared pods model", "modelcar page cache memory", "llama.cpp mmap shared multiple instances memory" | P287, giantswarm#564 (mlock/memcg note) |
| 14 | arXiv API | "page cache" model inference sharing; "image volume" kubernetes; ServerlessLLM; mmap weights serverless | HTTP 429, not searched |

Not covered (reopen before publishing a novelty line): BlitzScale, λScale,
HydraServe, OpenVINO mmap docs, LKML beyond EROFS, MLSys/EuroSys proceedings,
Docker Model Runner internals.

## Quoted-number register

Numbers this project quotes from someone else: upstream benchmarks, vendor specs,
papers. Docs cite a row as `[provenance:Pn]`. Our own measurements go in
[`EXPERIMENTS.md`](../EXPERIMENTS.md) as `[ledger:Hn]` instead.

A row needs a Source a reader can open: a URL pinned to a commit, tag, release or
archived copy, or a document title with version and page. A row with an empty
Source does not back a claim. `tests/claims_check.sh` enforces both rules.

| # | Claim | Source | Retrieved |
|---|-------|--------|-----------|
| P210 | H21 search log (2026-10-17 UTC): SearXNG 16 queries (Bing only, 0 relevant), Exa 4 (2 returned, 2 rate-limited), WebSearch 0 (budget spent), arXiv API 0 (301 then HTTP 429), OpenAlex 18 queries, GitHub repo search 10 queries, repo reads (kubernetes-power-manager, platform-aware-scheduling, peaks, clever, nri-plugins balloons), torvalds/linux commit search, kubernetes/enhancements issue search. Coverage partial: ACM/USENIX, MLSys, OSDI, EuroSys and arXiv not searched directly. No prior art found for a Kubernetes admission/placement controller that packs LLM-agent sandbox pods across P-core/E-core pools in one node from measured per-pod RAPL energy under a p95 turn SLO; every part alone has prior art (P211-P219). | https://api.openalex.org/works?search=energy-aware%20Kubernetes%20scheduler%20Kepler ; https://github.com/sustainable-computing-io/kepler/blob/main/docs/developer/power-attribution-guide.md ; https://github.com/containers/nri-plugins/blob/main/docs/resource-policy/policy/balloons.md | 2026-10-17 |
| P211 | Kepler attributes node active power to a workload as (workload CPU time delta / node CPU time delta) x active power, over RAPL package/core/DRAM/uncore zones; CPU time is not weighted by core type. Verify against a pinned source revision before quoting a number. | https://github.com/sustainable-computing-io/kepler/blob/main/docs/developer/power-attribution-guide.md | 2026-10-17 |
| P212 | Octopus-Man (HPCA 2015) maps latency-sensitive work to the least power-hungry cores that meet QoS on an Intel heterogeneous prototype; up to 41% CPU-power saving within QoS. | https://doi.org/10.1109/HPCA.2015.7056037 | 2026-10-17 |
| P213 | Hipster (HPCA 2017) uses heuristics plus RL over big/little cores and DVFS for latency-critical work under a QoS target; up to 18% energy saved on ARM big.LITTLE. | https://doi.org/10.1109/HPCA.2017.13 | 2026-10-17 |
| P214 | Linux EAS on Intel hybrid (merged for 6.16): prefers E-cores with spare capacity, only on hybrid platforms without SMT, with schedutil (intel_pstate passive). | https://github.com/torvalds/linux/commit/7b010f9b9061 | 2026-10-17 |
| P215 | nri-plugins balloons: `preferCoreType` efficient or performance on hybrid parts, and `cpuClasses` (EPP, frequency, SST-CP) per balloon; static, no energy feedback. H21's baseline. | https://github.com/containers/nri-plugins/blob/main/docs/resource-policy/policy/balloons.md | 2026-10-17 |
| P216 | Energy-aware Kubernetes pod placement across nodes from power models or Kepler data: PEAKS, CLEVER, TNSM 2025 multi-objective scheduler, WattsApp (UCC 2020). | https://github.com/sustainable-computing-io/peaks ; https://doi.org/10.1109/TNSM.2025.3630045 ; https://doi.org/10.1109/UCC48980.2020.00027 | 2026-10-17 |
| P217 | Per-process/pod RAPL attribution with counter models: Wattmeter (eBPF), Nf-PEAK (CLOUD 2026, MAPE 6.6-10.9%), SmartWatts. Core-type-aware weighting is partly covered. | https://doi.org/10.1145/3727200.3727214 ; https://doi.org/10.1109/CLOUD72782.2026.00032 | 2026-10-17 |
| P218 | TokenWatt: E-cores at 2.31x the J/token of P-cores for CPU LLM inference on a Core Ultra 7 155H (equal power, slower cores). A falsification risk for any "E-cores win J/turn" claim. | https://github.com/etisamhaq/tokenwatt | 2026-10-17 |
| P219 | Agent energy measurement and agent core pooling without energy placement: A-LEMS/EpG (RAPL per-turn joules), Agora (role-pooled cores for agent tail latency). | https://arxiv.org/html/2605.22883 ; https://doi.org/10.48550/arxiv.2608.04458 | 2026-10-17 |
| P240 | H24 prior-art search log (2026-10-17): searxng 21 queries (Bing noise, engine filter ineffective), Exa 3 (2 rate-limited), arXiv export API 7 (rate-limited, not executed), arXiv abstracts read 2402.16363 2406.01698 2312.03134 2405.05465 2407.07304 2407.00029 2311.00502 2411.19542, GitHub repository search 6, primary code reads (LLM-Viewer `hardware_params.py`, GenZ `system_configs.py`, agent-sandbox `capacity.py` @43194cb9, quantprobe docs). Gaps: MLSys/OSDI/EuroSys proceedings not searched directly; no k8s KEP hit; Intel docs not reached. Verdict: every part of H24 has prior art (P241-P249); no prior art found for the combination. | EXPERIMENTS.md section "H24 pre-registration"; full query list in the H24 pull request description | 2026-10-17 |
| P241 | LLM-Viewer models LLM inference with a spec-sheet roofline and has an `intel_13900k` entry (89.6 GB/s); not calibrated, no P/E split, AMX, iGPU, NPU or agents-per-box. | https://arxiv.org/abs/2402.16363 ; https://github.com/hahnyuan/LLM-Viewer (`hardwares/hardware_params.py`) | 2026-10-17 |
| P242 | quantprobe: spec bandwidth x efficiency calibrated on one box, held-out external validation, "8.4% median error"; GPU cards only (Intel Arc rows spec-only). | https://github.com/FedericoTs/quantprobe (`docs/HARDWARE_TABLE.md`, `docs/HARDWARE.md`) | 2026-10-17 |
| P243 | Ridgeline: roofline-driven CPU/iGPU/NPU placement on Lunar Lake from on-device measurement of one machine; not cross-SKU. | https://github.com/Protonicwave/ridgeline | 2026-10-17 |
| P244 | Poolside capacity planner predicts concurrent agents per deployment, calibrated on measured rows, with a "Calibrated" vs extrapolated badge; GPU/HBM KV-cache model only. | https://docs.poolside.ai/deployment/capacity-planning | 2026-10-17 |
| P245 | Metrum AI measured 120 to >1,024 agents per box on 2x Xeon 6 6760P + 2x RTX PRO 6000; one SKU, no cross-SKU predictor. | https://metrum.ai/blog/agent-density-enterprise-scale | 2026-10-17 |
| P246 | agent-sandbox RL example computes `max_concurrent` sandboxes from a live cluster's allocatable CPU, disk and pod density (not from spec sheets); Daytona measures sandbox density per CPU on NVIDIA Vera. | https://github.com/kubernetes-sigs/agent-sandbox/blob/43194cb9/examples/agent-sandbox-rl/agent_sandbox_rl/capacity.py | 2026-10-17 |
| P247 | Analytical / simulated LLM performance from hardware parameters, validated on datacenter accelerators (GenZ, LLMCompass, Vidur); GenZ's only Intel system is Gaudi3. | https://arxiv.org/abs/2406.01698 ; https://arxiv.org/abs/2312.03134 ; https://arxiv.org/abs/2405.05465 | 2026-10-17 |
| P248 | edge-llm-memory-wall: transferable decode roofline, decode tok/s ~ effective bandwidth / model bytes, R^2 = 0.994 at 73-86% of peak, Raspberry Pi 5 only. | https://doi.org/10.5281/zenodo.21844855 | 2026-10-17 |
| P249 | Intel CPU / hybrid P/E LLM inference work (optimisation, not spec-to-density prediction): arXiv 2311.00502, 2407.07304, 2407.00029, 2411.19542; nn-Meter kernel-level cross-device latency prediction (MobiSys'21). | https://arxiv.org/abs/2311.00502 ; https://arxiv.org/abs/2411.19542 | 2026-10-17 |
| P250 | H25 prior-art summary: cross-agent shared-prefix KV reuse is prior art on GPU and on Intel CPU (P251-P259). Deploy-time detection of a shared prefix from agent manifests, before any request: no prior art found in a limited search (SearXNG degraded, Exa rate-limited); low confidence, not a world-first claim | Search log rows P251-P259 of this table; KVFlow https://arxiv.org/abs/2507.07400 | 2026-10-17 |
| P251 | SearXNG `search_many`, 8 queries (vLLM CPU APC, OpenVINO GenAI SchedulerConfig, OVMS enable_prefix_caching, multi-agent shared prefix arXiv, KVFlow, KVComm, Parrot OSDI'24, Intel EAT): Bing noise only, no usable hit | SearXNG instance, queries as listed (no raw log) | 2026-10-17 |
| P252 | Multi-agent prefix KV reuse is published: KVFlow (fixed agent prompts plus workflow-aware eviction), KVComm (cross-agent KV under diverging prefixes), DroidSpeak (KV across LLMs in agentic systems), zolotukhin.ai "eight agents share one system prompt" | https://arxiv.org/abs/2507.07400 ; https://arxiv.org/abs/2510.12872 ; https://arxiv.org/abs/2411.02820 ; https://zolotukhin.ai/blog/2026-08-13-eight-agents-share-one-system-prompt-store-the-prefix-kv-once/ | 2026-10-17 |
| P253 | OVMS prefix caching is on by default: the 2026 long-context guide says "It is enabled by default", and the pinned image (`openvino/model_server@sha256:a928903f…`, 2026.4.1) prints `--enable_prefix_caching ... (default: true)` and `--cache_size ... (default: 0)` in `--help` | https://docs.openvino.ai/2026/model-server/ovms_demo_long_context.html ; `docker run --rm openvino/model_server@sha256:a928903fe429f1e3e27107cc45932d30d95febfd2d1159dc50869c3eac76fc58 --help` | 2026-10-17 |
| P254 | Exa "vLLM CPU backend Intel Xeon automatic prefix caching": rate-limited, no result; whether vLLM V1 enables APC by default is unverified here | Exa (no raw log) | 2026-10-17 |
| P255 | SearXNG `search_many`, 8 queries (EAT Xeon, vLLM CPU prefix caching, llm-d prefix routing, inference-extension, SGLang CPU, Preble, Hydragen, Prompt Cache): only Prompt Cache and Preble returned | https://proceedings.mlsys.org/paper_files/paper/2024/hash/a66caa1703fe34705a4368c3014c1966-Abstract-Conference.html ; https://github.com/WukLab/preble | 2026-10-17 |
| P256 | vLLM APC is enabled with `enable_prefix_caching=True`; it helps "when many requests share a long system prompt and then diverge"; the default is not stated on the pages read | https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/ ; https://docs.vllm.ai/en/latest/design/prefix_caching/ | 2026-10-17 |
| P257 | Kubernetes prefix-cache-aware placement exists and decides per request at runtime (llm-d prefix-cache-aware routing and kv-indexer, KServe llmisvc, third-party routers); none found that reads agent manifests at deploy time | https://github.com/llm-d/llm-d (docs/architecture/kv-management/prefix-cache-aware-routing.md) | 2026-10-17 |
| P258 | SearXNG `search_many`, 4 queries (inference-extension prefix routing, llm-d precise prefix cache, deploy-time manifest prefix analysis, multi-agent CPU prefix cache Xeon): noise only | SearXNG instance, queries as listed (no raw log) | 2026-10-17 |
| P259 | SGLang RadixAttention runs on Intel Xeon with AMX: shared-prefix KV reuse on Intel CPU is prior art | https://lmsys.org/blog/2025-07-14-intel-xeon-optimization/ | 2026-10-17 |
| P260 | H26 local probe (i9-13900T): `/proc/cpuinfo` has none of `rdt_a`, `cat_l2`, `cat_l3`, `cdp_l3`, `mba`, `cqm*`; `CONFIG_X86_CPU_RESCTRL=y`; `/sys/fs/resctrl` missing. H26 prior-art search was partial (P261-P267): no prior art found for auto-selecting RDT classes or L2-cluster placement for agent sandboxes; each mechanism is prior art | Local read on the test bed, 2026-10-17; kernel ABI https://docs.kernel.org/6.6/arch/x86/resctrl.html | 2026-10-17 |
| P261 | SearXNG (bing backend), 6 queries on nri-plugins RDT, Koordinator resctrl, KEP-3008, Raptor Lake CAT, Heracles, agent noisy neighbour: null (engine noise) | | 2026-10-17 |
| P262 | nri-plugins documents class-based RDT (`control.rdt`, `rdtclass.resource-policy.nri.io` annotation) and balloons `loadClasses` with `level: l2cache` | https://github.com/containers/nri-plugins/blob/v0.13.1/pkg/apis/config/v1alpha1/resmgr/policy/balloons/config.go | 2026-10-17 |
| P263 | Agent-sandbox interference papers characterize memory as the bottleneck; none applies RDT or L2 placement to agent pods | https://www.arxiv.org/pdf/2602.09345 | 2026-10-17 |
| P264 | Exa queries for Koordinator and Intel 12th/13th gen client RDT were rate-limited, not run (coverage gap) | | 2026-10-17 |
| P265 | intel/goresctrl provides the rdt, sst and blockio packages under nri-plugins and containerd/CRI-O class-based RDT | https://github.com/intel/goresctrl | 2026-10-17 |
| P266 | Koordinator koordlet ships per-pod resctrl LLC/MB isolation (`ResctrlConfig{LLC, MB}`); KEP-3008 QoS-class resources issue is closed | https://github.com/koordinator-sh/koordinator/blob/c5afca711320cb6bbc8fe74a0c90af8d0e44dbcb/apis/extension/resctrl.go | 2026-10-17 |
| P267 | WebSearch not run (call budget exhausted): kernel lists, OSDI/EuroSys/MLSys full text and Intel TCC CAT on embedded Core are uncovered, so H26 novelty is "partial search" | | 2026-10-17 |
| P230 | H23 prior-art search log (10 query batches, 2026-10-17, degraded): watts-as-a-resource, power-aware k8s scheduling, predictive slack placement and the turbo model are prior art; nothing found for per-pod watts as DRA consumable capacity | Section "H23 prior-art search (2026-10-17)" above; primary hits https://static.sched.com/hosted_files/kccncna2023/bd/KubeCon23%20Power%20Capping.pdf , https://github.com/kubernetes/enhancements/blob/master/keps/sig-scheduling/5075-dra-consumable-capacity/README.md | 2026-10-17 |
| P231 | DRA consumable capacity (KEP-5075): `allowMultipleAllocations`, `capacity.requestPolicy`, `consumedCapacity`; alpha in 1.34 and 1.35, beta target 1.36; `DeviceCapacity.Value` is a fixed total | https://github.com/kubernetes/enhancements/blob/master/keps/sig-scheduling/5075-dra-consumable-capacity/README.md , https://github.com/kubernetes/enhancements/issues/5075 | 2026-10-17 |
| P232 | A node extended resource is advertised by a JSON PATCH of the node's `/status` (capacity); values are integers and the scheduler accounts requests against it | https://kubernetes.io/docs/tasks/administer-cluster/extended-resource-node/ | 2026-10-17 |
| P233 | Intel Telemetry Aware Scheduling power demo: RAPL package power via collectd/Prometheus, `dontschedule` above 80% of TDP, score prefers the lowest-power node (node-level filter, no per-pod accounting) | https://github.com/intel/platform-aware-scheduling/tree/master/telemetry-aware-scheduling/docs/power | 2026-10-17 |
| P234 | GEOPM + Kueue (KubeCon NA 2023): power as extended resource `intel.com/power`, 1 W = 1 device, Kueue `nominalQuota: 10000` W, per-job GEOPM cap; static admin budget | https://static.sched.com/hosted_files/kccncna2023/bd/KubeCon23%20Power%20Capping.pdf , https://github.com/geopm/geopm/tree/cloud/service/kubecon-na-23 | 2026-10-17 |
| P235 | RAPL PL1 is enforced on an exponentially weighted moving average with time window Tau; PL2 bounds short bursts. Turbo duration t_max = Tau * ln((PL2 - P0) / (PL2 - PL1)) (Menra et al., Euro-Par 2026, fitted on Xeon Skylake-SP to Sapphire Rapids) | https://edc.intel.com/content/www/us/en/design/ipla/software-development-platforms/client/platforms/alder-lake-desktop/12th-generation-intel-core-processors-datasheet-volume-1-of-2/004/package-power-control/ , https://menraromial.com/pdf/talk/RAPL-power-capping-and-turbo-boost.pdf | 2026-10-17 |
| P236 | TAPAS (ASPLOS 2025): predicts thermal and power slack for placement, cuts thermal throttling events by 97% | https://arxiv.org/abs/2501.02600 | 2026-10-17 |
| P237 | Apple, "Power-Aware Scheduling in Kubernetes" (KubeCon EU 2023): Filter and Score plugins enforce server and rack power caps from a power model | https://static.sched.com/hosted_files/kccnceu2023/ef/KubeCon%20%2B%20CloudNativeCon%20EU23_power-aware-scheduling.PPT.pdf | 2026-10-17 |
| P238 | coretemp exposes package temperature and TjMax as `tempN_input` / `tempN_crit` (millidegrees C); `tcc_offset_degree_celsius` (int340x processor thermal device) lowers the TCC activation point below TjMax | https://docs.kernel.org/hwmon/coretemp.html , https://docs.kernel.org/driver-api/thermal/intel_dptf.html | 2026-10-17 |
| P239 | stress-ng `--cpu-ops N` stops a stressor after N bogo operations; `--cpu-load P` loads the CPU P percent | https://github.com/ColinIanKing/stress-ng/blob/master/README.md , https://manpages.ubuntu.com/manpages/noble/man1/stress-ng.1.html | 2026-10-17 |
| P280 | H28 prior-art search log: image-volume model delivery, mmap tensor sharing and overlay page-cache sharing are prior art; no measurement of per-pod PSS/memcg for N pods mmapping one image-volume model found | This file, section 9 (search log, 2026-10-17) | 2026-10-17 |
| P281 | KEP-4639 OCI VolumeSource: read-only OCI image mounts, motivated by mounting model weights; GA (stable) in Kubernetes v1.36 | https://github.com/kubernetes/enhancements/blob/6ebe0a94771723b3f27cb4a757f0cc22f29e38a2/keps/sig-node/4639-oci-volume-source/README.md and https://kubernetes.io/blog/2026/04/22/kubernetes-v1-36-release/ | 2026-10-17 |
| P282 | KServe `oci+native://` image-volume model storage (PR 5558) and modelcars; docs state no benchmark data is available yet | https://github.com/kserve/kserve/pull/5558 and https://github.com/kserve/kserve/issues/4083 | 2026-10-17 |
| P283 | Kliukovkin 2026: oci-native vs modelcar vs s3 on kind 1.36.1 / containerd 2.3.1; warm oci-native start 11.5-11.7 s for 2-140 GB; time and disk only | https://arxiv.org/abs/2607.16596 and https://github.com/kliukovkin/oci-model-delivery-bench | 2026-10-17 |
| P284 | Tetris (USENIX ATC'22): tensor sharing across serverless inference instances, up to 93% memory saved, up to 91.6% faster startup | https://www.usenix.org/conference/atc22/presentation/li-jie | 2026-10-17 |
| P285 | UPM/USM: madvise-driven page merging for serverless ML, PSS per container down 26-55% at n=16 | https://arxiv.org/abs/2311.13588v1 | 2026-10-17 |
| P286 | EROFS page-cache sharing across containers (inode_share, domain_id) merged for Linux 7.0-rc1 | https://github.com/torvalds/linux/commit/3893854000a81897a1a332ec50931f74761fbf71 | 2026-10-17 |
| P287 | LLMKube: llama.cpp mmap sharing, each extra instance 0.19-0.36x model size CPU-only, about 1.03-1.38x with GPU offload | https://dev.to/defilan/one-gpu-four-ways-to-share-it-ten-scenarios-and-the-one-number-that-inverts-on-your-hardware-1bih | 2026-10-17 |
| P288 | containerd CRI image-volume mount path (unpack, snapshotter Prepare per pod sandbox, mount); k3s v1.37.1+k3s1 ships containerd v2.3.4-k3s1 | https://github.com/containerd/containerd/blob/v2.3.3/internal/cri/server/container_image_mount.go and https://github.com/k3s-io/k3s/releases/tag/v1.37.1%2Bk3s1 | 2026-10-17 |
| P289 | cgroup v2 memory ownership: page cache is charged to the cgroup that first touches it | https://github.com/torvalds/linux/blob/v6.6/Documentation/admin-guide/cgroup-v2.rst (section "Memory Ownership") | 2026-10-17 |
| P290 | H29 prior-art search log, 2026-10-17. Found: uncore DVFS for memory-bound HPC phases, phase-aware CPU/GPU/memory DVFS for mobile LLMs, phase-aware GPU DVFS for LLM serving, static k8s uncore control (P291-P299). Not found: prefill/decode phase-aware core + uncore split for LLM inference on Intel x86 CPUs measured in J/token against the UFS/ELC default. Incomplete: SearXNG/Bing results irrelevant, Exa rate-limited after 4 queries, WebSearch budget exhausted, arXiv API 429, GitHub code search 403; ACM/IEEE full text, OSDI/EuroSys, LKML and KEPs not searched; Splitwise, DynamoLLM, POLCA recalled, not fetched. Re-run arXiv `all:uncore AND all:LLM`, GitHub code `intel_uncore_frequency` with llama/vllm/ollama, and LKML before citing the novelty line. Amended before any run: the H29 probe holds a static core cap + uncore pin for the whole request, which is the setting of P295 (Sundriyal 2018, ReAPER 2023) and of FUSE per-phase DVFS (P293) in spirit; no novelty line is claimed for H29, only its measured delta | Exa queries "uncore frequency scaling for LLM inference energy efficiency decode phase memory-bound CPU", "Intel Kubernetes Power Manager uncore frequency CRD", "dynamic uncore frequency scaling paper HPC energy savings memory-bound phases", "LLM inference on Xeon CPU uncore frequency energy per token measurement", "intel_uncore_frequency efficiency latency control ELC TPMI kernel documentation"; GitHub repo search `uncore+llm`, `uncore+inference`, `uncore+frequency+transformer` (0/0/0 repos) | 2026-10-17 |
| P291 | Linux `intel_uncore_frequency`: `initial_min/max_freq_khz` (read-only), `min/max_freq_khz` (root write), `current_freq_khz`; TPMI `uncoreNN` per fabric cluster with ELC `elc_floor_freq_khz`, `elc_low/high_threshold_percent`, `elc_high_threshold_enable` | https://docs.kernel.org/admin-guide/pm/intel_uncore_frequency_scaling.html | 2026-10-17 |
| P292 | Intel Kubernetes Power Manager ships an `Uncore` CRD (`power.intel.com/v1`, sysMin/sysMax/dieSelector) since v2.1.0: static k8s uncore control, not phase- or LLM-aware | https://github.com/intel/kubernetes-power-manager ; https://github.com/intel/power-optimization-library | 2026-10-17 |
| P293 | FUSE / CORE: per-phase (prefill/decode) CPU, GPU and memory frequency search for mobile LLM inference; FUSE reports TPOT -25.4% to -36.8% at equal energy per token (Android, not Intel uncore) | https://arxiv.org/abs/2507.02135 ; https://proceedings.mlsys.org/paper_files/paper/2026/file/136b9a13861308c8948cd308ccd02658-Paper-Conference.pdf | 2026-10-17 |
| P294 | MNN-AECS: decode is memory-bound and dominates energy; selects low-power cores for decode without root; -23% energy without slowdown (core selection, not uncore frequency) | https://arxiv.org/abs/2506.19884 | 2026-10-17 |
| P295 | Uncore frequency scaling for memory-bound HPC phases: Uncore Power Scavenger (SC'19), duf (CCPE 2021, energy up to -18.2%), Sundriyal core+uncore joint scaling (JCC 2018), ReAPER core-down in memory-bound regions (SC-W 2023), U-PullUp (SC25 poster) | https://doi.org/10.1145/3295500.3356150 ; https://doi.org/10.1002/cpe.6580 ; https://doi.org/10.4236/jcc.2018.612018 ; https://dl.acm.org/doi/10.1145/3624062.3624275 ; https://sc25.supercomputing.org/proceedings/posters/poster_files/post294s2-file3.pdf | 2026-10-17 |
| P296 | Hardware ELC (efficiency latency control) on Xeon 6 and its `pepc uncore config` interface: the in-silicon uncore controller H29 must beat on TPMI parts | https://builders.intel.com/docs/networkbuilders/intel-xeon-6-processors-performance-and-power-profiles-1746785299.pdf ; https://github.com/intel/pepc/blob/main/docs/misc-uncore-elc.md | 2026-10-17 |
| P297 | Phase-aware GPU DVFS for LLM serving (GPU SM clock only, no host uncore): VoltanaLLM, BiScale, AFlex, EcoInfer | https://arxiv.org/abs/2509.04827 ; https://arxiv.org/abs/2602.18755 ; https://arxiv.org/abs/2608.01891 ; https://doi.org/10.3390/electronics15102139 | 2026-10-17 |
| P298 | Test bed read-only sysfs (i9-13900T, kernel 6.6.128): `intel_uncore_frequency/package_00_die_00` initial and current min/max 800000/4700000 kHz; no ELC attributes; min/max readable unprivileged, `current_freq_khz` EACCES without root; intel_pstate, EPP performance, cpu0 `cpuinfo_max_freq` 5100000 | Host sysfs read 2026-10-17, attributes per https://docs.kernel.org/admin-guide/pm/intel_uncore_frequency_scaling.html | 2026-10-17 |
| P299 | GEOPM: an admin can grant a user or group a frequency-range control with auto-revert at session end, the only unprivileged write path; its uncore agent was not verified (agents page 404) | https://geopm.github.io/ | 2026-10-17 |
| P270 | H27 amx-autoquant prior-art search log (2026-10-17). Strong prior art exists for measuring LLM quant speed and quality on the host and caching the per-machine result (P271-P275), for accuracy-gated precision tuning (P276) and for capability-based auto-bf16 on AMX (P277). No prior art was found for applying a host-fingerprint-cached measured winner as the precision of Kubernetes agent pods, in a search that is explicitly INCOMPLETE: SearXNG returned only junk for 8 queries, Exa rate-limited after 2 queries, WebSearch budget exhausted (0 queries); arXiv, MLSys/OSDI/EuroSys 2024-2026, IPEX-LLM/Optimum-Intel, Ollama/LM Studio and k8s KEPs were not searched. Not a world-first claim. Queries: SearXNG x8 ("Intel Neural Compressor accuracy-aware tuning automatic precision selection bf16 int8", "auto select quantization level based on hardware benchmark local LLM tool", "llmfit recommend model quantization hardware", "OpenVINO automatic inference precision hint bf16 AMX auto", "per-host benchmark cache best quantization llama.cpp automatic", "AMX INT8 vs BF16 LLM CPU inference throughput comparison Sapphire Rapids", "Kubernetes node feature discovery AMX label select inference image", "arXiv hardware-aware automatic quantization selection LLM deployment edge CPU microbenchmark") all junk; Exa x2 (local-hardware quant recommender; OpenVINO auto bf16 on AMX) gave P271-P275 and P277; fetched P276, P278, P279 and https://www.localscore.ai/about (speed-only per-hardware database, no picking). | https://pypi.org/project/vectorprime/ , https://github.com/AnkitTsj/qbench , https://github.com/back2matching/quant-sim , https://github.com/FedericoTs/quantprobe/blob/master/QUICKSTART.md , https://github.com/AlexsJones/llmfit , https://intel.github.io/neural-compressor/latest/docs/source/tuning_strategies.html , https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/cpu-device.html , https://github.com/ggml-org/llama.cpp/pull/8998 , https://kubernetes-sigs.github.io/node-feature-discovery/stable/usage/features.html | 2026-10-17 |
| P271 | VectorPrime v0.1.0 detects the SIMD level (AVX/AVX2/AVX512), benchmarks F16, Q8_0, Q4_K_M, Q4_0, Int8 and Int4 on the host and picks the fastest that fits in memory. Speed only: no quality proxy, no AMX/VNNI arms. | https://pypi.org/project/vectorprime/ (v0.1.0) | 2026-10-17 |
| P272 | QBench profiles the host, quantizes to q4_k_m/q6_k/q8_0, measures tok/s, memory and perplexity vs the original model, and recommends a format. GPU-oriented, no ISA-aware arms. | https://github.com/AnkitTsj/qbench | 2026-10-17 |
| P273 | quant-sim benchmarks every Ollama quant level for speed and quality (20-question graded set) and recommends the best tradeoff; NVIDIA only. | https://github.com/back2matching/quant-sim | 2026-10-17 |
| P274 | quantprobe `calibrate` measures the machine and caches the result in `~/.quantprobe/calibration.json`; `auto --tps` picks the bits. Prior art for a per-machine cache of measured results. | https://github.com/FedericoTs/quantprobe/blob/master/QUICKSTART.md | 2026-10-17 |
| P275 | llmfit detects hardware, does dynamic quantization selection, `llmfit bench` measures real tok/s that replaces estimates, and `recommend --json` serves agents and scripts. | https://github.com/AlexsJones/llmfit | 2026-10-17 |
| P276 | Intel Neural Compressor 3.8 tuning strategies auto-optimize low-precision recipes for performance under an accuracy criterion (offline, per model). | https://intel.github.io/neural-compressor/latest/docs/source/tuning_strategies.html (INC 3.8) | 2026-10-17 |
| P277 | The OpenVINO CPU plugin, with `execution_mode=PERFORMANCE` (default), uses bf16/f16 automatically on AMX or AVX512_BF16 hardware, and converts f16 to f32 compute when the CPU has no native half precision; static, no measurement, no quality check. The precision-control page documents bf16 accuracy loss above 0.5% on some LLMs. | https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/cpu-device.html , https://docs.openvino.ai/2026/openvino-workflow/running-inference/optimize-inference/precision-control.html , https://github.com/openvinotoolkit/openvino/releases/tag/2023.0.0 | 2026-10-17 |
| P278 | llama.cpp PR #8998 adds an AMX backend to ggml (build flags `-mamx-tile -mamx-int8 -mamx-bf16`). Which quant types take the AMX path was not verified in this pass. | https://github.com/ggml-org/llama.cpp/pull/8998 | 2026-10-17 |
| P279 | Node Feature Discovery exposes `feature.node.kubernetes.io/cpu-cpuid.<flag>` labels (AMX/VNNI capability), not measured best precision. | https://kubernetes-sigs.github.io/node-feature-discovery/stable/usage/features.html | 2026-10-17 |
| P200 | Search log (exa): "LLM inference on hybrid Intel CPU P-cores and E-cores, scheduling prefill and decode differently". Found Intel's phase-adaptive P/E work split, arXiv 2411.19542 (prefill 20-30% and decode 9-22% faster on Core Ultra 125H), plus OpenVINO `scheduling_core_type`, llama.cpp #27033, the oneMKL hybrid guide and oneDNN #1844. | https://arxiv.org/abs/2411.19542 | 2026-10-17 |
| P201 | llama.cpp server flags: `-C/--cpu-mask` and `-Cb/--cpu-mask-batch` take "arbitrarily long hex" masks, `-tb` sets batch and prompt threads, `--cpu-strict`/`--cpu-strict-batch` set strict placement, `-np` sets slots; `/completion` takes `n_predict`, `ignore_eos` and `cache_prompt`. Search log (exa): "llama.cpp --cpu-mask-batch --threads-batch separate CPU affinity for prompt processing vs generation". | https://github.com/ggml-org/llama.cpp/blob/b11707/tools/server/README.md | 2026-10-17 |
| P202 | llama.cpp separate prefill and decode threadpools: PR #7526 (2024-05-24) and "Threadpool: take 2" PR #8672 (2024-07-24). Search log (exa): "llama.cpp threadpool PR cpu-mask-batch". | https://github.com/ggml-org/llama.cpp/pull/8672 | 2026-10-17 |
| P203 | MNN-AECS: the memory-bound decode phase dominates energy; picking fewer and smaller cores for decode gave 23% less energy with no slowdown on 7 Android and iOS devices. Search log (exa): "mobile LLM inference big.LITTLE cores prefill on big cores decode on little cores energy paper". | https://arxiv.org/abs/2506.19884 | 2026-10-17 |
| P204 | nri-plugins balloons `preferCoreType: efficient \| performance` places containers on P- or E-cores, statically per balloon. Search log (exa): "containers nri-plugins balloons policy preferCoreType hybrid P-core E-core". Splitwise (arXiv 2311.18677) and DistServe (arXiv 2401.09670) split prefill and decode across machines. | https://github.com/containers/nri-plugins/blob/v0.13.1/pkg/apis/config/v1alpha1/resmgr/policy/balloons/config.go | 2026-10-17 |
| P205 | Linux has no per-task Thread Director class: Neri's "IPC classes" v4 (2023-06-13) and "hfi: Enable Intel Thread Director" (2024-02-03) are unmerged on LKML, and mainline `intel_hfi.c` reports only per-CPU perf and efficiency capability over thermal netlink. The test host lists `hfi` and no `itd` in `/proc/cpuinfo`. | https://lore.kernel.org/lkml/?q=%22IPC+classes%22+hfi | 2026-10-17 |
| P206 | Search gaps: the exa query "Intel Thread Director HFI LLM workload scheduling" was rate-limited and returned nothing. 14 searxng queries (ITD/IPC classes, scx_lavd core compaction, NRI UpdateContainers dynamic cpuset, Splitwise heterogeneous power) gave only irrelevant results. Intel APO, MLSys and EuroSys proceedings, and Kubernetes KEPs were not searched; search them before any public novelty claim. | https://github.com/sched-ext/scx/blob/main/scheds/rust/scx_lavd/README.md | 2026-10-17 |
| P207 | llama.cpp server image `ghcr.io/ggml-org/llama.cpp:server` resolved to index digest `sha256:f41d47eee678c02656eefc9ed8cce76cab584aa7032805eeef0e6639fcfda485` (`docker buildx imagetools inspect`); latest llama.cpp release then b11707 (2026-10-17T07:50:33Z). | https://github.com/ggml-org/llama.cpp/releases/tag/b11707 | 2026-10-17 |
| P208 | Model `Qwen/Qwen3-4B-GGUF`, file `Qwen3-4B-Q4_K_M.gguf`, at revision `bc640142c66e1fdd12af0bd68f40445458f3869b` (Hugging Face model API). | https://huggingface.co/Qwen/Qwen3-4B-GGUF/blob/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf | 2026-10-17 |
