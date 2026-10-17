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
