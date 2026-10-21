# Intel capability map: what each Intel family offers agent serving, and how k3d-gpu should use it

Research date: **2026-10-16**. Scope: every on-die or platform ability that can raise
agents-per-box at a latency SLO, cut energy, or harden agent sandboxes, on the live host
and on the most widely deployed Intel families:

- **Client:** Alder Lake / Raptor Lake (12th-14th gen Core, S/H/P/U), Alder Lake-N and
  Twin Lake (N100/N150/N300/N305), Meteor Lake (Core Ultra 100), Lunar Lake (Core Ultra
  200V), Arrow Lake-S/H/U (Core Ultra 200S/H/U), Panther Lake (Core Ultra 300).
- **Server:** Xeon 4th/5th gen (Sapphire Rapids, Emerald Rapids), Xeon 6 P-core
  (Granite Rapids, incl. the Xeon 6 SoC Granite Rapids-D) and Xeon 6 E-core (Sierra Forest).

The Xeon generations before and after that window (E5 v2 to Diamond Rapids), and
the more obscure on-die engines and platform features, are in the
[on-die accelerator reference](intel-on-die-accelerators.md).

Contents:

1. [Evidence rules](#evidence-rules)
2. [Cross-SKU matrix](#1-cross-sku-matrix)
3. [Recommended agent-serving layout per family](#2-recommended-agent-serving-layout-per-family)
4. [k3d-gpu gaps: code changes and fixtures](#3-k3d-gpu-gaps-code-changes-and-fixtures)
5. [This host: quick wins and the needs-BIOS/owner list](#4-this-host-quick-wins-and-the-needs-biosowner-list)
6. [Live probe queue for after 17:00](#5-live-probe-queue-for-after-1700)
7. [Sources](#sources)

---

## Evidence rules

| Tag | Meaning |
|---|---|
| **LIVE** | Read on the host on 2026-10-16 between 14:35 and 14:45 PDT. Read-only only: `lscpu`, sysfs, procfs, `lspci -vv`, `dmidecode`, `rdmsr`, `perf list`, one 1 s `perf stat` of free-running counters, `nvidia-smi topo`. Each probe took under 5 s. Nothing was written, loaded or started. A benchmark was running at the time (package 91-96 C), so live clocks and temperatures are under load. |
| **SRC** | Read in primary source: Linux kernel tree (v6.6 or master), Mesa, intel media-driver, OpenVINO 2026.4.0, OVMS, intel-device-plugins. Linked in the row or in [Sources](#sources). |
| **ARK** | Intel ARK / datasheet value. ARK pages were reached through search summaries, not fetched as HTML, so per-SKU numbers carry **UNVERIFIED-FETCH** unless stated. |
| **VENDOR** | A vendor marketing claim (TOPS and similar), relayed and not reproduced. |
| **UNVERIFIED** | Not probed and not confirmed in a primary document. Do not build on it without a probe. |
| **COMPUTED** | Arithmetic from other values, e.g. peak DRAM bandwidth = channels x MT/s x 8 B. A theoretical ceiling, not a measurement. |

The host. The task brief calls it a Lenovo P360 Ultra; DMI reports **ThinkStation P3 Ultra
30HACTO1WW**, BIOS S0JKT1BA (2024-03-07) **LIVE**. CPU i9-13900T (Raptor Lake-S, family 6
model 0xB7), 8 P-cores with HT (CPUs 0-15) + 16 E-cores (CPUs 16-31), chipset W680, iGPU UHD
770 (PCI 8086:a780), RTX 4000 SFF Ada + RTX 2000E Ada, 4x32 GB DDR5, kernel 6.6.128, glibc 2.43.

Reconciled conflicts between research tracks:

- **TME MSR numbering.** One track labelled `0x981` as `IA32_TME_ACTIVATE`. Kernel source
  defines `MSR_IA32_TME_ACTIVATE 0x982` with `LOCKED = bit 0`, `ENABLED = bit 1`
  ([v6.6 intel.c](https://github.com/torvalds/linux/blob/v6.6/arch/x86/kernel/cpu/intel.c)).
  The host reads `0x982 = 0x1`, so TME is **locked off by BIOS**. Both tracks agree on that verdict.
- **`0x64F` readings differ between samples** (`0x19030002` and `0x19030100`). Both were
  taken under load at different instants. The sticky log bits match; the active bit moved
  between ThermStatus and Amps.
- **Intel's optimization reference manual (doc 248966)** could not be retrieved. Per-port
  VNNI throughput and SMT front-end partitioning numbers are therefore **UNVERIFIED**.

---

## 1. Cross-SKU matrix

How to read it. Each cell gives **presence** for that family. The last column says how
k3d-gpu should **detect and exploit** the ability; where the exploit differs by family, the
cell says so. Legend: **Y** present, **N** absent, **P** partial or SKU-dependent, **?**
unverified, **n/a** not applicable. "Host" cells are **LIVE** unless marked.

Column key: **Host** = i9-13900T live · **LNL** = Lunar Lake · **ARL** = Arrow Lake (S/H/U) ·
**MTL** = Meteor Lake · **PTL** = Panther Lake · **ADL/RPL** = Alder/Raptor Lake client
(desktop and mobile) · **N** = Alder Lake-N / Twin Lake · **SPR/EMR** = Xeon 4th/5th gen ·
**GNR** = Xeon 6 P-core · **SRF** = Xeon 6 E-core.

The table is wide; scroll sideways.

### 1a. CPU cores, ISA and scheduling

| Ability | Host | LNL | ARL | MTL | PTL | ADL/RPL | N | SPR/EMR | GNR | SRF | k3d-gpu: detect → exploit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Hybrid P/E split (`cpu_core`/`cpu_atom` PMUs) | Y: P 0-15, E 16-31 | Y ? | Y | Y | Y | Y (P-only on some i3/i5, ?) | N, E-only | N, P-only | N, P-only | N, E-only | **Today:** label `k3d-gpu.io/hybrid-cpu`, NRI balloons `performance`/`efficient`. Keep; add P/E counts as a node annotation. |
| LP E-core island (SoC tile, no L3) | n/a | Y ? | H: separate `cpu_lowpower` PMU (SRC); S: N ? | Y, inside `cpu_atom` (SRC) | Y, inside `cpu_atom` (SRC) | N | N | N | N | N | **Gap G3.** Read `cpu_lowpower`; split `cpu_atom` CPUs without `cache/index3`. New `lowpower` balloon for idle supervisors and heartbeats. |
| E-core 4-core shared-L2 clusters | Y: 16-19, 20-23, 24-27, 28-31, 4 MB each | ? | ? | ? | ? | Y | Y ? | n/a | n/a | Y ? (4-core modules) | **Gap G4.** Group by `cache/index2/shared_cpu_list`; `ecluster` balloon of 4 CPUs for embeddings and tools. |
| Favored cores (ITMT, CPPC `highest_perf`) | Y: CPUs 8-11 = 68, other P = 65, E = 39 | ? | ? | ? | ? | Y ? | ? | via SST-TF | via SST-TF | via SST-TF ? | **Gap G5.** argmax of `acpi_cppc/highest_perf`; 1-2 CPU `latency` balloon for sampler, tokenizer, event loop, GPU feeder. |
| HFI / Thread Director | Y (`hfi`, CPUID.06H bit19/23) | Y | Y | Y | Y | Y | ? | N | N | ? | Report only. Later: thermal-netlink listener that exports per-CPU capability. Linux 6.6 does not steer tasks by ITD class. |
| SMT on P-cores | Y | N (Lion Cove) | N | Y | ? | Y | n/a (no HT) | Y | Y | N | **Gap G4.** `pcore-physical` balloon (first CPU of each `core_cpus_list`) for decode; OpenVINO `ENABLE_HYPER_THREADING=false`. On no-SMT parts this is free. |
| AVX-VNNI (VEX int8 dot) + AVX2/FMA/F16C | Y, all 32 CPUs | Y | Y | Y | Y | Y | Y | Y + AVX512-VNNI | Y + AVX512-VNNI | Y | **Gap G6.** Label `feature.node.kubernetes.io/cpu-cpuid.AVXVNNI` etc. like AMX today; steer int8 CPU runtimes. |
| AVX-512 / AMX | N | N | N | N | N | N (fused off) | N | AMX-BF16/INT8 | + AMX-FP16 | N | **Today:** AMX labels. Add `AMXFP16` fixture (GNR) and doctor check that `arch_prctl(ARCH_REQ_XCOMP_PERM)` works in a pod. |
| AVX-VNNI-INT8 / AVX-IFMA / AVX-NE-CONVERT | N | ? | ? | N ? | ? | N | N | ? | ? | ? | **Gap G6.** Read flags `avx_vnni_int8`, `avx_ifma`, `avx_ne_convert`; never use a per-SKU table. |
| Crypto ISA (AES-NI, VAES, VPCLMULQDQ, GFNI, SHA-NI) | Y (256-bit VAES) | Y | Y | Y | Y | Y | AES/SHA Y, VAES ? | Y (512-bit) | Y | Y ? | Label only. Doctor: "no QAT, CPU crypto adequate for gateway TLS". |
| WAITPKG (UMWAIT/TPAUSE) | Y, `enable_c02=1` | Y ? | Y ? | Y ? | Y ? | Y | ? | Y | Y | ? | Report only. Tool pods: `OMP_WAIT_POLICY=passive` (gain UNVERIFIED). |

### 1b. Integrated engines

| Ability | Host | LNL | ARL | MTL | PTL | ADL/RPL | N | SPR/EMR | GNR | SRF | k3d-gpu: detect → exploit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| iGPU architecture | UHD 770, Xe-LP, 32 EU, 1650 MHz | Xe2 | H: Xe-LPG+; S/U: Xe-LPG | Xe-LPG | Xe3 | Xe-LP (UHD 730/770, Iris Xe) | Xe-LP, 24-32 EU (ARK ?) | none | none | none | **Gap G1.** ADL/RPL/ADL-N ids are missing from `intel_gpu_platform`, so the host prints an unnamed `Intel GPU (0xa780)`. |
| XMX (systolic) vs DP4a only | DP4a only (no `cl_intel_subgroup_matrix_multiply_accumulate`) | XMX (Mesa XEHP) | H: XMX (Mesa `arl_h`); S/U: N | N | XMX | N | N | n/a | n/a | n/a | **Gap G7.** Label `gpu.intel.com/xmx`. OpenVINO switches defaults on it: no XMX → i8 KV cache and dyn-quant group 32 (SRC). Route latency-SLO LLM pods to XMX only. |
| Media: AV1 decode / AV1 encode | dec Y / enc N; 2 VDBox (hevc,sfc) + 1 VEBox | Y / Y | Y / Y | Y / Y | Y / Y | Y / N | Y / N ? | n/a | n/a | n/a | **Gap G8.** Count `engine/vcs*`, read `capabilities`, `vainfo` entrypoints → labels `gpu.intel.com/media-vcs`, `av1-enc`. |
| USM zero-copy, shared LLC with CPU | USM Y; LLC shared (Mesa GFX12) | USM Y; LLC N | USM Y; LLC N | USM Y; LLC N | USM Y; LLC N | USM Y; LLC Y | Y ? | n/a | n/a | n/a | Label `gpu.intel.com/usm`. Doctor warning: iGPU and CPU share DRAM bandwidth. |
| iGPU SR-IOV | `sriov_totalvfs=7`, i915 6.6 has no PF provisioning (?) | xe ? | ? | ? | ? | capability Y, driver ? | ? | n/a | n/a | n/a | Report `igpu.sriov-capable=N`. Never write `sriov_numvfs` automatically. |
| NPU | N | NPU 4, `0x643e`, 48 TOPS (VENDOR) | S: `0xad1d` (`ivpu_drv.h` `PCI_DEVICE_ID_ARL`); H: `0x7d1d` (pci.ids "Core Ultra 200H/200V"); U: ?; ~13 TOPS (VENDOR) | NPU 3, `0x7d1d` | NPU 5, `0xb03e` | N | N | N | N | N | **Today:** detected. **Gap G2:** `0x7d1d` labelled "Meteor Lake" also on ARL-H/U. Add `npu.gen` label; NPU = 1 LLM stream (OVMS Stateful, sequential, 1024-token default prompt). |
| GNA | ARK says 3.0, not enumerated in `lspci`, no `intel_gna` module | n/a (NPU replaced it) | ARK 3.5 | ARK 3.5 | n/a | ARK 3.0 | ? | N | N | N | **Do nothing.** OpenVINO discontinued the GNA plugin in 2024. Log only. |
| DSA / IAA | N / N | N / N | N / N | N / N | N / IAA Y (`IAA_PTL` in idxd) | N | N | 1 / 1 default (8480+ ARK; ARK lists On Demand activation model "No" for this SKU) | 4 / 4 (6980P ARK) | 2 / 2 (6780E ARK) | **Today:** bus-based detect (gen-agnostic). Add PTL-IAA and GNR-D fixtures, doctor "N devices = SKU default" line. |
| QAT | N | N | N | N | N | N | N | 1 (8480+ ARK; 4xxx) | 4 (6980P ARK; 4xxx/420xx ?) | 2 (6780E ARK) | **Today:** PF ids `0x37c8 0x4940-0x4948`. Add 420xx fixture; Envoy mTLS offload. |
| DLB | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 1 | 4 | 2 | Unsupported (plugin removed in 0.37.0). Doctor "present, not exposed". |

### 1c. Memory

| Ability | Host | LNL | ARL | MTL | PTL | ADL/RPL | N | SPR/EMR | GNR | SRF | k3d-gpu: detect → exploit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Channels × speed → peak GB/s (COMPUTED) | 2ch DDR5 **3600** configured (rated 4800), 2DPC 2R → **57.6** | LPDDR5X-8533 on-package → ~136.5 (ARK ?) | S: DDR5-6400 → 102.4; H: DDR5-5600 → 89.6 (ARK ?) | DDR5-5600 → 89.6 / LPDDR5X-7467 → ~119 (ARK ?) | LPDDR5X-9600 → ~153.6 (VENDOR ?) | DDR5-4800/5600 → 76.8/89.6 (ARK ?) | 1ch DDR5-4800 → 38.4 (ARK ?) | SPR 8ch-4800 → 307; EMR 8ch-5600 → 358 | 6900P 12ch-6400 → 614; MRDIMM-8800 → 845 | 6780E 8ch DDR5-6400 (ARK) → 409.6 | **Gap G10.** Doctor parses `dmidecode -t17`: configured vs rated vs POR; label `k3d-gpu.io/dram.peak-gbps`. Use it as the agents-per-node cap for CPU/iGPU decode. |
| IMC bandwidth counters | Y: `uncore_imc_free_running_{0,1}` data_read/write; 1 s sample under load ≈ 14.2 GB/s | Y (`lnl_uncore_init`, SRC) | Y (`mtl_uncore_init`) | Y | Y (`ptl_uncore_init`) | Y (`adl_uncore_init`) | Y ? | `uncore_imc_N` `cas_count_*` | Y (`gnr_uncore_init`) | Y (`gnr_uncore_init`) | **Gap G14.** Exporter reads `.scale`/`.unit`; admission stops CPU-decode pods above a measured knee. Needs `CAP_PERFMON`. |
| ECC with EDAC reporting | ECC DIMMs (80/64-bit), **no EDAC** on 6.6 for `8086:a700`; master `ie31200_edac` has it | IBECC (igen6) ? | S on W880: Y ? | IBECC ? | ? | W680: Y; mobile IBECC ? | IBECC ? | Y | Y | Y | **Gap G10.** Label `dram.ecc=active|unmonitored|none`; taint on `ue_count > 0`. |
| THP for weights | Anon THP `always`; ext4 + 6.6: read-only weight mmap stays 4 KiB (SRC `file_thp_enabled` needs `VM_EXEC`) | same kernel rule | same | same | same | same | same | same | same | same | **Gap G16.** Huge-tmpfs model cache (`huge=within_size`), bind-mounted read-only. |
| Page-cache sharing of weights | Y (mechanism) | Y | Y | Y | Y | Y | Y | Y | Y | Y | **Gap G16.** One shared model path → one page-cache copy for N pods. Pre-warm in a dedicated cgroup (first-touch charging). |
| RDT CAT/MBA (resctrl) | N | N | N | N | N | N | N | Y | Y | Y | **Gap G11.** Map QoS classes to resctrl groups via containerd RDT. |
| NUMA / SNC / HBM / CXL tiers | 1 node | 1 | 1 | 1 | 1 | 1 | 1 | SNC; CXL 1.1; Xeon Max HBM | SNC; MRDIMM; CXL 2.0 | SNC ? | **Gap G11.** Biggest Xeon gap: balloons only deploy on hybrid today. |
| KSM | available, `run=0` | all | all | all | all | all | all | all | all | all | Recommend **against** for multi-tenant (cross-tenant timing side channel). Report only. |

### 1d. Power, thermal, telemetry

| Ability | Host | LNL | ARL | MTL | PTL | ADL/RPL | N | SPR/EMR | GNR | SRF | k3d-gpu: detect → exploit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| HWP + per-CPU EPP | Y; all EPP `performance`; **power-profiles-daemon active** (overrides writes) | Y | Y | Y | Y | Y | Y | Y | Y | Y | **Today:** doctor reads EPP. Add PPD-override note; opt-in E-core `balance_performance`. |
| RAPL package / psys / uncore domains | PL1=PL2=**117 W** (MSR `0x610`, unit 1/8 W from `0x606`) vs ARK base power **35 W** and ARK max turbo power **106 W**, so PL2 is set above Intel's spec by the OEM; PL4 230 W, unlocked | Y | Y | Y | Y | Y | Y | pkg + DRAM | TPMI RAPL ? | TPMI ? | **Gap G10.** `perf_rapl` warns only on PL1 < TDP; add the inverse: PL1 far above TDP **and** throttling. |
| Throttle telemetry (`0x19C`, `0x1B1`, `0x64F`, `thermal_throttle`) | **Active.** `package_throttle_count=22048145`; PROCHOT, ThermStatus, Amps, PL2 logged; TCC point 95 C | Y | Y | Y | Y | Y | Y | addresses differ ? | ? | ? | **Gap G10.** New `perf_thermal`: sysfs unprivileged; MSRs when root; never clear sticky log bits. |
| Uncore (ring/mesh) frequency | 0.8-4.7 GHz range, ring 2.9 GHz at sample | Y ? | Y | Y | Y ? | Y | ? | MSR | TPMI | TPMI | Report; opt-in `uncore-max` in sweeps only. |
| DPTF processor participant | `tcc_offset=5`, `workload_request` (sustained/bursty…), no `workload_hint` | `workload_hint` | `workload_hint` ? | `workload_hint` | ? | `workload_request` | ? | N | N | N | Report; opt-in `tune --workload sustained` with printed revert. Gain UNVERIFIED. |
| Intel SST (PP/BF/TF/CP) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | P (per SKU) | P (TPMI) | P ? | Detect `/dev/isst_interface`; map `latency` balloon to SST-CP high-priority CLOS. |
| Intel PMT telemetry | present (`0x305a000`, `0x1605b0d2`), GUIDs **not** in `pmt.xml` | decodable | not in `pmt.xml` | decodable | decodable | not in `pmt.xml` | ? | decodable | decodable | ? (CWF listed, SRF ?) | Decode only known GUIDs from a pinned `pmt.xml`; else `pmt=raw-only`. |
| Idle states | `intel_idle` with ACPI `_CST` (no native RPL table in 6.6 or master): C1 1 µs, C2 127 µs, C3 1048 µs | native ? | native ? | native | ? | ADL native, RPL ACPI | ? | native | native | native | **Today:** `perf_cstates` suggests global disable. Change to per-CPU `pm_qos_resume_latency_us` on latency CPUs only. |

### 1e. Isolation and sandbox hardening

| Ability | Host | LNL | ARL | MTL | PTL | ADL/RPL | N | SPR/EMR | GNR | SRF | k3d-gpu: detect → exploit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| VT-x + EPT + APICv, `/dev/kvm`, vsock | Y; `kvm_intel` nested=Y apicv=Y; `/dev/vhost-vsock` 0666 | Y | Y | Y | Y | Y | VT-x Y, VT-d per SKU ? | Y | Y | Y | **Gap G17.** Label `k3d-gpu.io/kvm`; opt-in `up --sandbox kata` RuntimeClass (k3d docker-in-docker shim UNVERIFIED). |
| VT-d + per-GPU IOMMU groups | Y; 15 groups; RTX 4000, RTX 2000E and iGPU each alone | Y | Y | Y | Y | Y | ? | Y | Y | Y | Label `gpu.<idx>.iommu-isolated`; prerequisite for GPU passthrough into microVMs. Never recommend `pcie_acs_override`. |
| CET: user shadow stack + kernel IBT | Y; kernel IBT active (`IA32_S_CET=0x4`); user SHSTK not enabled for ordinary processes | Y | Y | Y | Y | Y | ? | Y | Y | Y ? | Label `cpu-cet.shstk`; doctor checks `x86_Thread_features`. Never set `glibc.cpu.x86_shstk=on` blind: glibc's manual says `on` enables SHSTK even for unmarked executables and libraries. `permissive` turns SHSTK off on dlopen of a non-CET library instead of failing it. |
| PKU | Y (`pku`, `ospke`) | Y | Y | Y | Y | Y | ? | Y | Y | Y ? | Label only. |
| Split-lock detect / bus-lock detect | Y / N; `split_lock_mitigate=1` | Y / ? | Y / ? | Y / ? | Y / ? | Y / N | ? | Y / Y | Y / Y | ? | Doctor info; warn if `split_lock_detect=off`. |
| TME / TME-MK | TME capable (ARK: TME-MK Yes), **locked off** (`0x982=0x1`) | vPro ? | vPro ? | vPro ? | ? | vPro ? | ? | TME-MK | TME-MK | TME-MK ? | Label `mem-encryption=tme-active|tme-capable-off|none`. Enabling is BIOS-only. |
| SGX / TDX | N / N | N / N | N / N | N / N | N / N | N / N | N / N | SGX Y (8480+ ARK: "Yes with Intel SPS"); TDX not listed for 8480+, other SKUs ? | TDX Y (6980P ARK), other SKUs ? | SGX Y, TDX Y (6780E ARK) | Detect `/dev/sgx_enclave`, `kvm_intel` `tdx`; Xeon only: `kata-qemu-tdx` RuntimeClass. |
| AMT / CSME (MEI) | MEI Y, SOL function present (AMT-capable), provisioning UNVERIFIED | vPro only | vPro only | vPro only | vPro only | vPro only | N | BMC instead | BMC | BMC | Doctor reports AMT-capable. Never pass `/dev/mei0` into agent pods. |
| GPU context isolation (PPGTT, preempt timeout, OA paranoia) | rcs0 preempt 7500 ms; `perf_stream_paranoid=1`; `/dev/dri/renderD128` 0666 | xe | i915/xe | i915 | xe | i915 | i915 | n/a | n/a | n/a | Doctor warns on `perf_stream_paranoid=0`; render node only via device plugin, never hostPath. |

### 1f. Host I/O topology (host only, LIVE)

| Ability | Host finding | Exploit |
|---|---|---|
| GPU PCIe link | RTX 4000 SFF: Gen4 x16 device behind CPU port `00:01.0` (the port itself is Gen5 x16). **RTX 2000E: device x8, root port `00:06.0` is Gen4 x4 → capped at x4.** NVMe sits behind PCH ports, so model loads cross DMI 4.0 x8. Links read Gen1 at idle (ASPM); compare `max_*` only. | **Gap G10:** per-GPU `pcie.gen/width/bottleneck` labels from `max_link_*` of device and root port. |
| GPU peer-to-peer | `nvidia-smi topo -p2p r` = **NS** (not supported), path PHB; ACS redirect on both root ports. | Label `gpu-p2p=none`; one replica per GPU, no tensor parallel across the pair. |
| Resizable BAR | RTX 4000 BAR1 32 GB, RTX 2000E BAR1 16 GB (full). | Doctor: warn when BAR1 is 256 MB and VRAM is larger. |

---

## 2. Recommended agent-serving layout per family

The rule behind every row: single-stream decode is memory-bandwidth bound, so the engine
with the most bandwidth per model byte runs the LLM. Everything that is latency-critical but
single-threaded goes to the highest-clocked core. Everything bursty and parallel (tools, sandboxes,
embeddings) goes to the cores or engines the LLM does not need.

| Family | LLM (main) | Embeddings / rerank / ASR | Tools and agent sandboxes | Why |
|---|---|---|---|---|
| **This host** (RPL-S + 2x Ada) | RTX 4000 SFF (x16, 20 GB): main model replica. | RTX 2000E (x4, 16 GB): draft or small model and embeddings, all resident (no KV streaming over the x4 link, no P2P). Fallback: one `ECORE_ONLY` stream of 4 threads per E-cluster. | E-cores 16-31 (cluster-aligned); HT siblings 1,3,…,15 for light I/O. Favored CPUs 8 and 10 for the serving frontend / GPU feeder thread. UHD 770: media decode and small INT8 vision models. | Dual Ada carries decode. CPU decode is capped by 57.6 GB/s (COMPUTED) DRAM. The box is thermally and current-limited, so keep CPU load off P-cores that feed the GPUs. |
| **ADL/RPL desktop, no dGPU** | P-cores, physical only, pinned (OpenVINO `PCORE_ONLY`, HT off) with INT4 weights. | E-cluster streams (`ECORE_ONLY`, 4 threads each). | E-cores; iGPU for media and side models. | No XMX on Xe-LP; iGPU decode lags the CPU on these parts (see [C12](benchmark-landscape.md)). |
| **ADL/RPL mobile (Iris Xe)** | P-cores (INT4). H2 found the no-XMX UHD 770 slower than the P-cores [ledger:H2]; Iris Xe is unmeasured. | iGPU INT8 or E-cores. | E-cores. | Iris Xe has more EUs; still no XMX. The CPU and iGPU share one DRAM bus. |
| **N-series / Twin Lake** | Remote LLM. Local only ≤1.5B INT4 on the CPU. | CPU (AVX-VNNI). | **Primary role: sandbox host.** 10-40 sandboxed tool agents (estimate, UNVERIFIED). | Single channel, 38.4 GB/s (COMPUTED); no NPU; E-cores only. |
| **Meteor Lake** | iGPU (Xe-LPG, no XMX: non-systolic path) for ≤3B. | NPU (one stream), or CPU. | E-cores; **LP E-cores** for idle supervisors and heartbeats. | NPU is sequential under OVMS; iGPU lacks XMX. |
| **Arrow Lake-H** | iGPU with XMX (OVMS GPU, continuous batching). | NPU 3 (one stream) or E-cores. | E-cores; LP E-cores (`cpu_lowpower`) for idle loops. | XMX + 89.6 GB/s (COMPUTED). No HT, so sandboxes get whole cores. |
| **Arrow Lake-S / -U** | CPU P-cores (DDR5-6400), or a dGPU. | NPU or E-cores. | E-cores. | Small iGPU without XMX. |
| **Lunar Lake** | Xe2 iGPU with XMX (OVMS GPU, continuous batching). | NPU 4 (one stream, 1024-token default). | E-cores (LPE island). | Highest client bandwidth per watt before PTL; 16-32 GB soldered memory bounds KV caches, so cap pods by RAM. |
| **Panther Lake** | Xe3 iGPU with XMX. | NPU 5, or IAA-backed zswap to fit more sandboxes. | E-cores / LP E-cores. | XMX + highest client bandwidth; IAA present on the client die. |
| **SPR / EMR** | CPU with AMX-BF16/INT8, one replica per SNC/NUMA node (`pinMemory`). | AMX on a separate NUMA node or dGPU. | Cores on another node; QAT for mesh mTLS; DSA for copies; IAA for zswap. | 307-358 GB/s per socket (COMPUTED); AMX makes CPU prefill cheap. |
| **Granite Rapids** | CPU with AMX (incl. FP16), MRDIMM where fitted; one replica per SNC domain. | AMX on spare domains. | Cores + 4 DSA / 4 IAA / 4 QAT (6980P). TDX trust domains for untrusted agents where the SKU has it. | Up to 845 GB/s per socket with MRDIMM-8800 (COMPUTED). |
| **Sierra Forest** | Not an LLM engine (no AMX, no AVX-512). Remote LLM or a dGPU. | AVX-VNNI CPU embeddings. | **Sandbox density part:** many E-cores, no SMT (no sibling side channel), DSA/IAA/QAT. | Built for core count, not matrix math. |

---

## 3. k3d-gpu gaps: code changes and fixtures

Ordered by value. Line numbers refer to `scripts/k3d-gpu` on `main` at `1e73f47`.

Status on `main` at `a32a970` (2026-10-20), read from `scripts/k3d-gpu` and the fixtures:
G1-G8 and G10-G13 shipped (platform names, `npu.gen`, `lowpower`, `ecluster`,
`pcore-physical`, `latency` on favored CPUs, NFD ISA labels, `gpu.intel.com/xmx`, media
labels and the shared iGPU, NUMA-replica balloons, the Xeon fixtures, the SDSi note).
G9 shipped as the XMX gate in `agent up`. G14 is partial: `share/telemetry/` ships
Prometheus, node-exporter and the Intel GPU exporter, not the IMC/PSI admission signal.
G16 shipped differently, as `model-share` (one page-cache copy through an image volume,
[ledger:H28]). G15 and G17 are open: no `ov-config` annotation, and no Kata RuntimeClass
(`doctor --perf` only reports `/dev/kvm`).

| # | Gap | Concrete change | Fixture needed |
|---|---|---|---|
| **G1** | ADL/RPL/ADL-N iGPU ids missing (`intel_gpu_platform`, L259-272). Host doctor prints `Intel GPU (0xa780)` with no name and no GuC check. | Add cases bound by **i915** with firmware from `intel_uc_fw.c`: `0x468[028ab]\|0x469[023]` → `Alder Lake-S\|<k>\|i915/tgl_guc_`; `0x46a[0-368a]\|0x462[68a]\|0x46b[0-3]\|0x46c[0-3]` → `Alder Lake-P\|<k>\|i915/adlp_guc_`; `0xa78[0-38-9ab]` → `Raptor Lake-S\|<k>\|i915/tgl_guc_`; `0xa72[01]\|0xa7a[0189a-d]` → `Raptor Lake-P/U\|<k>\|i915/adlp_guc_`; `0x46d[0-4]` → `Alder Lake-N\|<k>\|i915/adlp_guc_`. **Do not leave `<k>` empty** (empty means "needs `xe.force_probe`"); fill it from `git log -S INTEL_RPLS_IDS` etc. before merging. | Update `raptorlake-nvidia/doctor.golden` to expect `Raptor Lake-S (0xa780)` + tgl_guc line. New `alderlake-n` fixture (no `cpu_core`/`cpu_atom`, iGPU `0x46d1`). |
| **G2** | `npu_platform` (L275) names `0x7d1d` "Meteor Lake"; pci.ids labels the id "Core Ultra 200H/200V Series Processors NPU", so it is also ARL-H. (The "200V" half of that label conflicts with `ivpu_drv.h`, which gives Lunar Lake `0x643e`; ARL-U is UNVERIFIED.) | Label `Meteor Lake / Arrow Lake-H`; optional disambiguation by CPU model (`INTEL_ARROWLAKE_H`). Add `npu.gen` label from the existing `gen` field. | `arrowlake-h` fixture with NPU `0x7d1d` and a cpuinfo model of ARL-H. |
| **G3** | LP E-cores lost or mixed: `hybrid_cpus` (L225) reads only `cpu_core`/`cpu_atom`. ARL-H puts LP E-cores in `cpu_lowpower` (dropped); MTL/PTL put them in `cpu_atom` without L3 (pollute `efficient`). | `hybrid_cpus lowpower`; plus `atom` CPUs whose `cache/index3` is absent move to `lowpower`. Third balloon type `lowpower` in `share/nri-balloons.yml`; pod label `k3d-gpu.io/cores=lowpower`. | `arrowlake-h` (with `cpu_lowpower/cpus`), new `meteorlake`, and add `cache/index3`-less LP CPUs to `pantherlake`. |
| **G4** | No cache-cluster or physical-core balloons. | Read `cache/index2/shared_cpu_list` for E-clusters and `topology/core_cpus_list` for physical P-cores. Balloon types `ecluster` (4 CPUs, cluster-aligned) and `pcore-physical`. | Add `cache/index{2,3}` and `topology/core_cpus_list` to `raptorlake-nvidia` sysroot (values from this host). |
| **G5** | Favored cores not detected. | `favored_cpus()`: argmax over `acpi_cppc/highest_perf`; fallback MSR `0x771` bits 7:0 when root. Annotation `k3d-gpu.io/favored-cpus=8-11`; `latency` balloon of 1-2 favored primaries. | `raptorlake-nvidia`: add `acpi_cppc/highest_perf` (68 on 8-11, 65 other P, 39 E). |
| **G6** | Only AMX gets CPU labels (`amx_flags` L207-221). | Generalise to `cpu_isa_labels` over a fixed flag list: `avx_vnni avx_vnni_int8 avx_vnni_int16 avx_ifma avx_ne_convert avx512f avx512_vnni avx512_bf16 amx_* user_shstk ibt pku vaes sha_ni gfni`. Confirm NFD label spellings against NFD source before shipping. | `raptorlake-nvidia` (AVXVNNI, no AMX); new `xeon-gnr` (adds `amx_fp16`); new `xeon-srf` (no AMX, accelerators present). |
| **G7** | No XMX label. | Static map from the platform name (G1 table) → `gpu.intel.com/xmx=true\|false`; cross-check in a one-shot pod via OpenVINO `OPTIMIZATION_CAPABILITIES` containing `GPU_HW_MATMUL` (mapping UNVERIFIED). | `arrowlake-h` (`0x7d51`, xmx=true) vs `arrowlake-s` (xmx=false); `pantherlake` xmx=true. |
| **G8** | No media labels; GPU plugin runs `-shared-dev-num` 1 (one pod owns the iGPU). | Count `engine/vcs*`/`vecs*`, read `capabilities`; labels `gpu.intel.com/media-vcs`, `av1-enc`, `av1-dec`. Add `-shared-dev-num=4 -allocation-policy=packed` to `share/intel-gpu-plugin.yml` behind a flag. | Add `sys/class/drm/card0/engine/{rcs0,vcs0,vcs1,vecs0}/capabilities` to `raptorlake-nvidia` and `arrowlake-s`. |
| **G9** | Model-to-device choice is manual (C4/H4). | Derive OVMS `target_device` from labels: XMX GPU → GPU; else CPU (AVX-VNNI/AMX); NPU only for one side stream. Render into the OVMS example values. | `sim_matrix_test.sh` cases per fixture asserting the chosen device. |
| **G10** | `doctor --perf` misses the facts that cap this host. | **Done.** New checks, each with `perf_section` source and printed revert: **`perf_thermal`** (throttle counters, TCC offset, MSR `0x19C/0x1B1/0x64F` decode when root); **`perf_rapl`** inverse case (PL1 > 2× TDP while throttling); **`perf_memory`** DRAM configured vs rated vs POR + peak GB/s, ECC/EDAC state, THP-on-ext4 note, KSM report; **`perf_pcie`** per-GPU max link vs root port, P2P, ReBAR; **`perf_isolation`** KVM/IOMMU groups/CET/split-lock/TME/`perf_stream_paranoid`; **`perf_cpu`** note when power-profiles-daemon overrides EPP; **`perf_igpu`** add RP1 (efficient freq) and waitboost count. | `raptorlake-nvidia` sysroot: add `thermal_throttle/*`, `powercap` with PL1 117 W / TDP 35 W, `dmi` type-17 dump, PCI link files, `iommu_groups`. **Done:** the fixture was recaptured as root with these inputs, and every fixture now has a `doctor-perf.golden`. |
| **G11** | No NUMA/SNC placement on Xeon (balloons only on hybrid, L226). | Vendor `numa` when `node*` count > 1; labels `k3d-gpu.io/numa-nodes`, `snc`. Balloon type `replica` with `pinMemory`, `preferCloseToDevices`, `allocatorTopologyBalancing`, `shareIdleCPUsInSame: numa` (all in the shipped CRD v0.14.0). resctrl classes where `rdt_a` exists. | New `xeon-gnr-snc` (2 sockets, SNC, 12 ch), `xeon-srf` (1 socket); add memory-only node to a Xeon Max variant. |
| **G12** | Only one Xeon fixture. | — | `xeon-gnr`, `xeon-srf`, `xeon-gnrd` (DSA `0x11fb`, 0 IAA), `qat-420xx` (PF `0x4946`), `pantherlake` + IAA (`IAA_PTL`); add `uncore_imc_N/events/cas_count_read` to `xeon-spr-accel`. |
| **G13** | Accelerator counts unexplained (8480+ ARK default is 1 of each; SPR XCC silicon is reported to carry up to 4, UNVERIFIED). | Doctor prints per-socket count vs ARK default; if `intel_vsec.sdsi.*` exists, note On Demand (SPR/EMR only, program discontinued per press). | `xeon-spr-accel`: add `sys/bus/auxiliary/devices/intel_vsec.sdsi.0`. |
| **G14** | No node telemetry on `main`. | Pinned exporter DaemonSet with `CAP_PERFMON`: IMC bytes, RAPL incl. `uncore` (iGPU), i915/xe PMU busy and RC6, throttle counters, PSI memory, DRM fdinfo per pod. Admission signal = PSI `some avg10` + IMC utilisation. | Unit test against fixture PMU dirs (`uncore_imc_free_running_*`, `i915`). |
| **G15** | OpenVINO knobs not emitted per balloon. | Annotation `k3d-gpu.io/ov-config` per balloon: decode `PCORE_ONLY` + `ENABLE_HYPER_THREADING=false` + pinning; embeddings `ECORE_ONLY` + `INFERENCE_NUM_THREADS=4` per E-cluster. OpenVINO reads the cgroup cpuset, so knobs must match the balloon. | Golden of rendered pod spec. |
| **G16** | No shared model cache. | Optional `--model-cache tmpfs-huge`: host tmpfs `huge=within_size`, bind-mounted read-only; warm-up pod owns the page-cache charge. | `up.golden` for the flag. |
| **G17** | No sandbox runtime. | Label `k3d-gpu.io/kvm`; opt-in `up --sandbox kata` → kata-deploy + RuntimeClass; CET tunable doc; `sandbox.k3d-gpu.io/smt=off` when `smt/active=0`. Spike first: Kata shim inside k3d (docker-in-docker) is UNVERIFIED. | Fixture with `/dev/kvm` + `vmx ept` flags vs one without. |

---

## 4. This host: quick wins and the needs-BIOS/owner list

**Do not run any write below before 17:00.** Live benchmarks own the host until then. Each
item is reversible and non-persistent across reboot, and each needs a measured A/B before it
becomes a default.

**These are not free on a shared host.** The host runs about 50 production containers.
Q4-Q8 are host-wide writes (package power limit, EPP on every E-core, stopping a system
daemon, firmware workload hint, per-CPU idle latency, uncore ceiling). Each one changes the
performance of every container on the box, not only the agent stack under test. Run them
only in an owner-approved window. Record each file's current value first and revert to that
recorded value, not to the value printed here. The revert values below match what the host
read on 2026-10-16: `workload_type=none`, `pm_qos_resume_latency_us=0` (0 means "no
constraint"), uncore `initial_max_freq_khz=4700000`, EPP `performance`, PL1 `117000000`. The diagnosis behind most of them: the host is **thermal- and
current-limited, not power-limited**. Throttle counts are in the tens of millions, PROCHOT
and current-limit bits are logged, and the package sat at the 95 C TCC point under load,
while PL1 is set to 117 W on a 35 W TDP part.

### Safe quick wins (suggestions, exact commands, revert)

| # | Change | Command | Revert |
|---|---|---|---|
| Q1 | Put decode threads on physical P-cores only, latency thread on a favored core. | Pin decode to `0,2,4,6,8,10,12,14`; frontend / GPU feeder to `10` (or `8`). OpenVINO: `SCHEDULING_CORE_TYPE=PCORE_ONLY`, `ENABLE_HYPER_THREADING=false`, `ENABLE_CPU_PINNING=true`. | Remove the pin. |
| Q2 | Embeddings as one stream per E-cluster. | `taskset -c 16-19 …` (and 20-23, 24-27, 28-31); `SCHEDULING_CORE_TYPE=ECORE_ONLY`, `INFERENCE_NUM_THREADS=4`. | Remove the pin. |
| Q3 | Big model on the x16 GPU, small/resident work on the x4 GPU. | `CUDA_VISIBLE_DEVICES` = the RTX 4000 for the main replica; RTX 2000E for draft, embeddings, reranker. No tensor parallel across the pair (P2P = NS). | — |
| Q4 | Lower PL1 toward what the cooler can remove (sweep 117/100/90/80 W). | `echo 90000000 \| sudo tee /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw` | `echo 117000000 \| sudo tee /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw` |
| Q5 | E-cores at `balance_performance` (frees package heat for P-cores). power-profiles-daemon overwrites EPP, so stop it for the run. | `sudo systemctl stop power-profiles-daemon; echo balance_performance \| sudo tee /sys/devices/system/cpu/cpu{16..31}/cpufreq/energy_performance_preference` | `echo performance \| sudo tee …/cpu{16..31}/cpufreq/energy_performance_preference; sudo systemctl start power-profiles-daemon` |
| Q6 | Tell firmware the load is sustained. | `echo sustained \| sudo tee /sys/bus/pci/devices/0000:00:04.0/workload_request/workload_type` | `echo none \| sudo tee …/workload_type` |
| Q7 | Per-CPU idle-exit cap on decode CPUs only (not a global C-state disable). | `echo 100 \| sudo tee /sys/devices/system/cpu/cpu{0..15}/power/pm_qos_resume_latency_us` | `echo 0 \| sudo tee …/pm_qos_resume_latency_us` |
| Q8 | Cap uncore when the run is GPU-bound (sweep only). | `echo 3500000 \| sudo tee /sys/devices/system/cpu/intel_uncore_frequency/package_00_die_00/max_freq_khz` | `echo 4700000 \| sudo tee …/max_freq_khz` |
| Q9 | Opt sandbox processes into CET shadow stack. | Not a host write. Test in one pod first with `GLIBC_TUNABLES=glibc.cpu.x86_shstk=permissive`. Do not use `on`: the glibc manual says `on` turns SHSTK on even when the executable and its libraries are not SHSTK-marked, so unmarked code can crash. Verify with `grep x86_Thread_features /proc/<pid>/status`. Whether this host's glibc 2.43 build enables SHSTK at all is UNVERIFIED (`x86_Thread_features` is empty for ordinary processes today). | Drop the env var. |
| Q10 | Read-only checks to run any time. | `sudo perf stat -a -e uncore_imc_free_running_0/data_total/,uncore_imc_free_running_1/data_total/ sleep 1`; `cat /sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_count` | — |

### Needs BIOS or the owner

| Item | Today | Action | Note |
|---|---|---|---|
| DIMM speed | 4x DDR5 SODIMM 2R at **3600** configured, rated 4800, 2DPC on 2 channels. | No BIOS speed gain at this population. Datasheet 743844 rev. 015 table 25 ([PDF](https://cdrdv2.intel.com/v1/dl/getContent/743844)): the 13th Gen S line lists no SODIMM 2DPC configuration (note 9), and its UDIMM 2DPC 2R POR is 3600 MT/s, which is what the host runs. Alternative: 2 DIMMs (1DPC 2R, POR 5200 MT/s) trades 64 GB capacity for the modules' rated 4800 MT/s. `doctor --perf` reports this as root. | COMPUTED peak: 3600 → 57.6 GB/s; 4800 → 76.8. Matters only for CPU/iGPU decode. |
| TME | Capable, locked off (`0x982=0x1`). | BIOS toggle + reboot, then A/B the SLO. | Protects DRAM contents at rest; no tenant isolation. |
| GNA | Listed on ARK, not enumerated. | **Nothing.** No supported software path (OpenVINO 2024 dropped GNA). | Do not chase. |
| Platform profile / cooling | PROCHOT and current-limit events under load. | Owner: chassis airflow, BIOS thermal / performance mode. Undervolting is out of scope. | The binding limit on agents-per-box for CPU work here. |
| Kernel | 6.6.128 | Owner: a newer kernel adds EDAC for `8086:a700` (`ie31200_edac` on master; first version UNVERIFIED) and large page-cache folios on more filesystems. | Load modules only outside the benchmark window. |
| AMT provisioning | AMT-capable, state unknown. | Check MEBx or an AMT host tool; unprovision if unused. | Attack surface on a multi-tenant node. |
| iGPU SR-IOV | `sriov_totalvfs=7` on i915. | Owner decision; needs a driver with PF provisioning. | Do not write `sriov_numvfs` blind. |

---

## 5. Live probe queue for after 17:00

Priority order. Every probe here **changes load or settings**, which is why it waits. Each
feeds one campaign: **APB** = agents-per-box at SLO; **D1-D10** = the dogma trials in
`probes/sci_d*` (working tree, not yet on `main`); **H3/H4** = [EXPERIMENTS.md](../EXPERIMENTS.md);
**DP** = `doctor --perf` thresholds.

| P | Probe | Measures | Feeds |
|---|---|---|---|
| 1 | Thermal baseline: run the standard decode load with `turbostat` + `thermal_throttle` deltas + `0x64F` sampling. | Which limiter fires (Therm / Amps / PL) at steady state. | APB, DP (`perf_thermal`), D10 |
| 2 | PL1 sweep 117/100/90/80 W (Q4) at fixed load. | Agents-at-SLO and J/token per PL1. | APB, DP (`perf_rapl` inverse rule) |
| 3 | IMC bandwidth knee: decode at batch 1..N on CPU while sampling `uncore_imc_free_running_*`. | GB/s at the TPOT knee vs 57.6 GB/s ceiling. | D1, D9, APB admission threshold (G14) |
| 4 | P-core decode 8 vs 16 threads (HT on/off), pinned. | tok/s and p99 inter-token latency. | D1, H3 |
| 5 | Embeddings per E-cluster vs straddling clusters. | Throughput and p99 per stream. | D7, APB |
| 6 | VPDPBUSD loop or `benchmark_app` int8, `PCORE_ONLY` vs `ECORE_ONLY`. | Per-core int8 rate (replaces UNVERIFIED manual numbers). | D1, G15 knob defaults |
| 7 | Noisy-neighbour arms (pinned vs shared) with `stress-ng` on E-cores. | Isolation per H3 protocol. | H3 |
| 8 | Device sweep CPU / UHD 770 / RTX 2000E / RTX 4000 for a fixed small model. | Best device vs auto choice. | H4, D3, G9 |
| 9 | iGPU net capacity with CPU decode running (shared DRAM). | Combined slots at the floor. | D3, D10 |
| 10 | Shared weights vs per-agent copies (N pods, same inode; PSS vs RSS). | Memory per extra agent. | D6, G16 |
| 11 | Huge-tmpfs model cache vs ext4 mmap; `dtlb_load_misses.walk_completed`. | Decode tok/s and TLB walks. | D2, G16 |
| 12 | E-core EPP `balance_performance` (Q5) and uncore cap (Q8). | P-core clock held, package W, SLO. | APB, DP |
| 13 | DPTF `workload_type=sustained` (Q6). | Any change in PL/turbo behaviour. | DP |
| 14 | Turn attribution: prefill / decode / tool split per agent turn. | Whether turns are LLM-bound. | D8, D4 |
| 15 | Kata RuntimeClass spike inside k3d. | Whether the shim runs in docker-in-docker; microVM start time. | G17 |

---

## Sources

Kernel (v6.6 unless master is named):

- Hybrid PMUs and LP E-core PMU (`cpu_lowpower`), master: https://github.com/torvalds/linux/blob/master/arch/x86/events/intel/core.c
- ITMT: https://github.com/torvalds/linux/blob/v6.6/arch/x86/kernel/itmt.c
- intel_pstate, HWP, EPP: https://docs.kernel.org/admin-guide/pm/intel_pstate.html
- HFI: https://github.com/torvalds/linux/blob/v6.6/drivers/thermal/intel/intel_hfi.c and https://docs.kernel.org/arch/x86/intel-hfi.html
- Uncore PMU model table, master: https://github.com/torvalds/linux/blob/master/arch/x86/events/intel/uncore.c
- THP and `file_thp_enabled`: https://docs.kernel.org/admin-guide/mm/transhuge.html and https://github.com/torvalds/linux/blob/v6.6/include/linux/huge_mm.h
- cgroup v2 memory ownership: https://docs.kernel.org/admin-guide/cgroup-v2.html
- EDAC `ie31200` RPL-S ids, master: https://github.com/torvalds/linux/blob/master/drivers/edac/ie31200_edac.c
- TME MSR: https://github.com/torvalds/linux/blob/v6.6/arch/x86/kernel/cpu/intel.c
- MSR numbers: https://github.com/torvalds/linux/blob/master/arch/x86/include/asm/msr-index.h
- turbostat limit-reason bit names: https://github.com/torvalds/linux/blob/v6.6/tools/power/x86/turbostat/turbostat.c
- powercap / RAPL: https://docs.kernel.org/power/powercap/powercap.html
- DPTF: https://docs.kernel.org/driver-api/thermal/intel_dptf.html
- Uncore frequency: https://docs.kernel.org/admin-guide/pm/intel_uncore_frequency_scaling.html
- intel_idle tables: https://github.com/torvalds/linux/blob/v6.6/drivers/idle/intel_idle.c
- PM QoS: https://docs.kernel.org/power/pm_qos_interface.html
- CET shadow stack: https://docs.kernel.org/arch/x86/shstk.html
- Split lock: https://docs.kernel.org/arch/x86/buslock.html
- resctrl: https://docs.kernel.org/arch/x86/resctrl.html
- TDX / SGX: https://docs.kernel.org/arch/x86/tdx.html and https://docs.kernel.org/arch/x86/sgx.html
- Speed Select: https://docs.kernel.org/admin-guide/pm/intel-speed-select.html
- PMT ABI and vsec: https://github.com/torvalds/linux/blob/v6.6/Documentation/ABI/testing/sysfs-class-intel_pmt and https://github.com/torvalds/linux/blob/v6.6/drivers/platform/x86/intel/vsec.c
- PMT GUID decode: https://github.com/intel/Intel-PMT/blob/main/xml/pmt.xml
- idxd (DSA/IAA ids incl. `IAA_PTL`), master: https://github.com/torvalds/linux/blob/master/drivers/dma/idxd/init.c
- QAT: https://github.com/torvalds/linux/blob/master/drivers/crypto/intel/qat/Kconfig
- Intel GPU ids and firmware, master: https://github.com/torvalds/linux/blob/master/include/drm/intel/pciids.h, https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/i915/gt/uc/intel_uc_fw.c, https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/xe/xe_pci.c
- NPU ids: https://github.com/torvalds/linux/blob/master/drivers/accel/ivpu/ivpu_drv.h
- i915 / xe PMU: https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/i915/i915_pmu.c and https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/xe/xe_pmu.c
- DRM fdinfo: https://docs.kernel.org/gpu/drm-usage-stats.html
- PCI sysfs: https://docs.kernel.org/PCI/sysfs-pci.html; VFIO: https://docs.kernel.org/driver-api/vfio.html
- KVM API: https://docs.kernel.org/virt/kvm/api.html
- MEI: https://docs.kernel.org/driver-api/mei/mei.html

Mesa, media, OpenVINO, OVMS, plugins:

- XMX/LLC per platform: https://gitlab.freedesktop.org/mesa/mesa/-/raw/main/src/intel/dev/intel_device_info.c and https://gitlab.freedesktop.org/mesa/mesa/-/raw/main/include/pci_ids/iris_pci_ids.h
- Media features: https://github.com/intel/media-driver/blob/master/docs/media_features.md
- OpenVINO CPU scheduling (2026.4.0): https://github.com/openvinotoolkit/openvino/blob/2026.4.0/src/plugins/intel_cpu/src/cpu_streams_calculation.cpp, https://github.com/openvinotoolkit/openvino/blob/2026.4.0/src/plugins/intel_cpu/src/cpu_map_scheduling.cpp, https://github.com/openvinotoolkit/openvino/blob/2026.4.0/src/inference/src/os/lin/lin_system_conf.cpp
- OpenVINO GPU defaults on `supports_immad`: https://github.com/openvinotoolkit/openvino/blob/master/src/plugins/intel_gpu/src/runtime/execution_config.cpp
- OpenVINO GNA discontinued: https://docs.openvino.ai/2024/about-openvino/release-notes-openvino.html
- OVMS NPU limits: https://github.com/openvinotoolkit/model_server/blob/main/docs/llm/reference.md and https://github.com/openvinotoolkit/model_server/blob/main/demos/vlm_npu/README.md
- GPU plugin flags: https://github.com/intel/intel-device-plugins-for-kubernetes/blob/main/cmd/gpu_plugin/README.md
- Kata: https://github.com/kata-containers/kata-containers/blob/main/docs/install/README.md

Intel product pages. The i9-13900T, 8480+, 6980P and 6780E pages were fetched on 2026-10-16 during review (base/turbo power, memory, accelerator default counts, SGX/TDX, TME-MK). The rest stay UNVERIFIED-FETCH (reached via search summaries):

- i9-13900T: https://www.intel.com/content/www/us/en/products/sku/230498/intel-core-i913900t-processor-36m-cache-up-to-5-30-ghz/specifications.html
- Xeon 8480+: https://www.intel.com/content/www/us/en/products/sku/231746/intel-xeon-platinum-8480-processor-105m-cache-2-00-ghz/specifications.html
- Xeon 8592+: https://www.intel.com/content/www/us/en/products/sku/237261/intel-xeon-platinum-8592-processor-320m-cache-1-90-ghz/specifications.html
- Xeon 6980P: https://www.intel.com/content/www/us/en/products/sku/240777/intel-xeon-6980p-processor-504m-cache-2-00-ghz/specifications.html
- Xeon 6780E: https://www.intel.com/content/www/us/en/products/sku/240362/intel-xeon-6780e-processor-108m-cache-2-20-ghz/specifications.html
- Xeon 6716P-B: https://www.intel.com/content/www/us/en/products/sku/242908/intel-xeon-6716pb-processor-160m-cache-2-50-ghz/specifications.html
- Intel SDM (CPUID leaves 06H/07H/1AH/19H, MSRs 19CH, 1A2H, 1B1H, 610H, 771H, 774H, 982H): https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html
- Vendor TOPS figures (Lunar Lake, Arrow Lake): https://en.wikipedia.org/wiki/Lunar_Lake and https://en.wikipedia.org/wiki/Arrow_Lake_(microprocessor)
