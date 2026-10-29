# Intel Xeon on-die accelerators and platform features, v2 to Diamond Rapids

Research date: **2026-10-20**. Scope: every on-die engine, ISA extension and platform
feature on Intel Xeon from E5 v2 (Ivy Bridge-EP) to Diamond Rapids that can matter for
LLM and agent serving, with how to detect it, how to turn it on, and what k3d-gpu does
with it today.

Method: 245 feature records written by parallel research tracks, mostly from primary
sources (Linux master, which docs.kernel.org reports as 7.3; Intel ISE rev 061; the RDT
Arch Spec rev 1.1; ARK; Intel tech articles), then a review pass that re-checked 21
claims and added 15 features. Two live read-only inventories back the LIVE cells:
**crescent** (Xeon 6741P, 4x Arc Pro B70, the host of the cross-host runs in
[EXPERIMENTS.md](https://github.com/88plug/k3d-gpu/blob/main/EXPERIMENTS.md)) and the
i9-13900T workstation. The k3d-gpu state is read from `scripts/k3d-gpu` at `a32a9701`.

This page complements the [Intel capability map](intel-capability-map.md), which covers
client parts and SPR/EMR/Xeon 6 from the agent-serving angle. The gap list in
[section 4](#4-k3d-gpu-gap-list) is a set of proposals, not promises; the ones queued
are in the [Intel roadmap](intel.md#roadmap).

## Evidence rules

- Every claim carries a source tag `[Kx]`, `[Ix]`, `[Rx]`, `[Px]` or `[Lx]`. The key is in [Sources](#sources).
- `believed` means the record could not check a kernel version against a git tag.
- `unverified` means no primary source was found.
- `LIVE` means read on the host. Every live read was read-only.
- `engineering inference` (short tag **(EI)**) marks an LLM or agent use that has no published measurement. The review pass tagged every such cell explicitly; an untagged use cell cites a measurement or a documented mechanism. The measured numbers that do exist are listed in the [hidden gems](#3-hidden-gems-for-llm-and-agent-serving) section.
- Review pass 2026-10-20: additions in [2.6](#26-additions-review-pass-2026-10-20), corrections in [6b](#6b-review-pass-corrections-2026-10-20). Kernel claims re-checked by grepping `cpufeatures.h` at each git tag; ISA claims against ISE rev 061 Table 1-2 and GCC `-march` docs; SKU claims against ARK.

Contents:

1. [Generation x feature matrix](#1-generation-x-feature-matrix)
2. [Per-feature reference](#2-per-feature-reference)
3. [Hidden gems](#3-hidden-gems-for-llm-and-agent-serving)
4. [k3d-gpu gap list](#4-k3d-gpu-gap-list)
5. [Live state on crescent and the i9-13900T vs the matrix](#5-live-state-on-crescent-and-the-i9-13900t-vs-the-matrix)
6. [Conflicts reconciled](#6-conflicts-reconciled) and [review-pass corrections](#6b-review-pass-corrections-2026-10-20)
7. [Sources](#sources)

---

## 1. Generation x feature matrix

Columns:
- **v2** = E5/E7 v2 Ivy Bridge-EP
- **v3** = Haswell-EP
- **v4** = Broadwell-EP
- **SKX** = Xeon Scalable gen1 Skylake-SP
- **CLX** = gen2 Cascade Lake
- **CPX** = gen3 Cooper Lake (4S/8S only)
- **ICX** = gen3 Ice Lake-SP
- **SPR** = gen4 Sapphire Rapids
- **EMR** = gen5 Emerald Rapids
- **GNR** = Xeon 6 P-core Granite Rapids (GNR-D noted)
- **SRF** = Xeon 6 E-core Sierra Forest
- **CWF** = Xeon 6+ Clearwater Forest, **shipped** (ARK: 6960E+/6970E+/6980E+/6990E+, launch Q2'26) [I-ARKlist][K-p2p]
- **DMR** = Diamond Rapids (CPUID 13_01H [I-ISE] Table 1-1), **announced**: kernel, GCC 15 and oneDNN support exist, launch not verified. QATlib names it "Intel Xeon 7" [R-qatlib-req]; a kernel commit dates it "2026" [K-p2p]. [K-cpuf][R-gcc15]

Cell codes:

| Code | Meaning |
|---|---|
| **S** | shipped on all SKUs |
| **s** | shipped on select SKUs only |
| **L** | in silicon; enabled count set per SKU, raisable by On Demand licence on SPR/EMR |
| **B** | in silicon, off by default (BIOS, kernel quirk or microcode) |
| **K** | in silicon, no usable mainline-kernel path |
| **P** | platform or chipset, not on the CPU die |
| **A** | announced (DMR) |
| **-** | absent |
| **?** | unverified |

### 1a. Vector and matrix ISA

| Feature | v2 | v3 | v4 | SKX | CLX | CPX | ICX | SPR | EMR | GNR | SRF | CWF | DMR | Src |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F16C (FP16<->FP32 convert) | S | S | S | S | S | S | S | S | S | S | S | S | A | [I-E5v2DS][K-cpuf] |
| AVX2 + FMA3 | - | S | S | S | S | S | S | S | S | S | S | S | A | [I-E5v3][K-cpuf] |
| BMI1/2, LZCNT, MOVBE | - | S | S | S | S | S | S | S | S | S | S | S | A | [K-cpuf] |
| AVX-512 F/CD/BW/DQ/VL (1 or 2 FMA units per SKU through ICX) | - | - | - | S | S | S | S | S | S | S | - | - | A | [I-SKXov][I-ISE] |
| AVX512_VNNI | - | - | - | - | S | S | S | S | S | S | - | - | A | [I-DLB][R-gcc] |
| AVX512_BF16 | - | - | - | - | - | S | **-** | S | S | S | - | - | A | [R-gcc][I-BF16] |
| VBMI/VBMI2/BITALG/VPOPCNTDQ/IFMA, VAES/VPCLMULQDQ/GFNI, SHA-NI | - | - | - | - | - | - | S | S | S | S | ? | ? | A | [R-gcc][I-ICXov]. SRF/CWF: no AVX-512 forms; GCC `-march=sierraforest/clearwaterforest` lists VEX VAES, VPCLMULQDQ and GFNI-SSE only [R-gcc] |
| AVX512_FP16 | - | - | - | - | - | - | - | S | S | S | - | - | A | [K-cpuf][I-ISE] |
| AVX-VNNI (VEX) | - | - | - | - | - | - | - | S | S | S | S | S | A | [K-cpuf][R-onednn] |
| AVX-VNNI-INT8, AVX-NE-CONVERT, AVX-IFMA, CMPccXADD | - | - | - | - | - | - | - | - | - | **-** | S | S | A | [I-ISE] (SRF, DMR); CWF from GCC `-march=clearwaterforest` [R-gcc] |
| AVX-VNNI-INT16, SHA512, SM3, SM4 (VEX) | - | - | - | - | - | - | - | - | - | - | - | S | A | [I-ISE] Table 1-2: Arrow Lake S, CWF, DMR. Client Lunar/Panther Lake too [R-gcc]. Kernel flags SHA512/SM3/SM4 unnamed in cpuinfo, present by 6.14 [K-cpuf] |
| SM4 (EVEX, 512-bit) | - | - | - | - | - | - | - | - | - | - | - | - | A | [I-ISE] (DMR, Nova Lake) |
| AVX10.1 (leaf 0x24) / AVX10.2 | - | - | - | - | - | - | - | - | - | S (10.1) | - | - | A (10.2) | [I-ISE][I-AVX10] |
| APX (32 GPRs) | - | - | - | - | - | - | - | - | - | - | - | - | A | [I-ISE][K-apx] |
| AMX-TILE/INT8/BF16 | - | - | - | - | - | - | - | S* | S | S | - | - | A | [K-xstate][I-ISE]; *some SPR -N SKUs ship without AMX [SM-SPR] |
| AMX-FP16 | - | - | - | - | - | - | - | - | - | S | - | - | A | [I-ISE] |
| AMX-COMPLEX | - | - | - | - | - | - | - | - | - | s (GNR-D 06_AEH only) | - | - | A | [I-ISE] Table 1-2 (GNR-D, DMR); GCC `-march=graniterapids-d` and `diamondrapids` [R-gcc] |
| AMX-FP8, AMX-AVX512, AMX-MOVRS; AMX-TF32 (?) | - | - | - | - | - | - | - | - | - | - | - | - | A (TF32 ?) | [I-ISE][R-onednn-rel]. ISE rev 061 Table 1-2 **still lists AMX-TF32 for DMR**; GCC `-march=diamondrapids` omits it [R-gcc]. See section 6 |
| PREFETCHIT0/1 | - | - | - | - | - | - | - | - | - | S | - | S | A | [I-ISE]; GNR LIVE [L-crescent] |
| MOVRS / PREFETCHRST2 | - | - | - | - | - | - | - | - | - | - | - | - | A | [I-ISE]; `X86_FEATURE_MOVRS` first in 7.0 [K-cpuf] |
| USER_MSR (URDMSR/UWRMSR) | - | - | - | - | - | - | - | - | - | - | - | S | - (not in GCC DMR list) | [I-ISE] (CWF only); [R-gcc] |
| RDMSRLIST/WRMSRLIST, WRMSRNS | - | - | - | - | - | - | - | - | - | ? | S | ? | A | [I-ISE] (SRF, GRR, PTL, DMR). Kernel uses WRMSRNS by 6.9 (FRED path) [K-msr] |
| RAO-INT (AADD/AAND/AOR/AXOR) | - | - | - | - | - | - | - | - | - | - | - | - | - | [I-ISE] Table 1-2: "Future processors", no named SKU |
| Key Locker (AES-KL, KL/WIDEKL) | - | - | - | - | - | - | - | - | - | - | s? (GCC lists KL/WIDEKL) | - (GCC drops KL) | - | [R-gcc]; Linux: **no mainline support**, series stalled at v9 (2024-03) [P-keylocker]; no `X86_FEATURE_KEYLOCKER` in 7.3 [K-cpuf] |
| PBNDKB / Total Storage Encryption | - | - | - | - | - | - | - | - | - | - | - | - | - | [I-ISE] Table 1-2: Panther Lake (client) only; not a Xeon feature |
| PCONFIG (MKTME key programming) | - | - | - | - | - | - | S | S | S | S | S | S | A | [R-gcc] (icelake-server on); `pconfig` in cpuinfo [K-cpuf]. Mainline MKTME API never merged; TME-MK keys are used by TDX via SEAMCALL, not PCONFIG from Linux (believed) |
| AI Compute Extensions (ACE, AMD+Intel) | - | - | - | - | - | - | - | - | - | - | - | - | spec only, no SKU | [R-ace][R-onednn-isa] |

### 1b. On-die engines and data movement

| Feature | v2 | v3 | v4 | SKX | CLX | CPX | ICX | SPR | EMR | GNR | SRF | CWF | DMR | Src |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CBDMA / I/OAT (ioatdma) | S | S | S | S | S | S | S | - | - | - | - | - | - | [K-ioat] |
| CBDMA RAID XOR/PQ | K (masked by Linux) | K | K | K | K | K | - | - | - | - | - | - | - | [K-ioat] |
| DDIO (PCIe writes into LLC) | S | S | S | S | S | S | S | S | S | S | S | S | A | [I-DDIO] |
| DSA (1.0 SPR/EMR, 2.0 Xeon 6, 3.0 DMR) | - | - | - | - | - | - | - | L | L | L | L | L | A | [K-idxd][I-X6brief][I-DSAspec] |
| IAA (1.0 SPR/EMR, 2.0 Xeon 6) | - | - | - | - | - | - | - | L | L | L | L | ? | A | [K-idxd][K-iaa][I-X6brief] |
| QAT | P (DH895xCC card) | P | P | P (C62x PCH) | P | P | P | L (Gen4 4xxx) | L | L (Gen4 402xx 8086:4944 LIVE; GNR-D = **Gen5 420xx** 8086:4946) | L (402xx) | L | A (**Gen6 6xxx** 8086:4948, QATlib 26.08) | [K-qat][L-crescent][K-qat6][R-qatlib-req] |
| QAT on Xeon D / SoC die or package | - | - | s (D-1500 NS SKUs, believed) | s (D-2100 NT, on-package PCH, believed) | - | - | s (D-1700/2700 NT; not in mainline) | - | - | s (Xeon 6 SoC P-B: Gen5 420xx, adds SNOW3G/ZUC) | - | - | - | [I-ARKD2796NT][I-X6SoCvRAN][R-qatlib-req][K-qat-kconfig] |
| vRAN Boost (LDPC/Turbo FEC, FFT, MLD-TS) | - | - | - | - | - | - | - | s (SPR-EE, VRB1 8086:57c0) | - | s (GNR-D, VRB2 8086:57c2) | - | - | ? | [R-dpdk-vrb1][R-dpdk-vrb2][I-ARK6716PB]; DPDK bbdev only, no kernel driver |
| Integrated Ethernet (Xeon D / SoC) | - | - | s (D-1500 X552, `ixgbe` X550EM_x IDs, believed) | s (D-2100 X722, `i40e`, believed) | - | - | s (D-1700/2700 E823, `ice`, up to 100 GbE) | - | - | s (Xeon 6 SoC, up to 200 Gb/s as 8x25G, PTP/SyncE) | - | - | - | [I-ICLD][I-X6SoCvRAN][K-ice][K-ixgbe][K-i40e] |
| DLB (2.0 SPR, 2.5 Xeon 6) | - | - | - | - | - | - | - | L/K | L/K | L/K | ? | L/K | ? | [R-dpdk-dlb][L-crescent]; no in-tree driver; Intel out-of-tree driver 8.13.0 [I-DLBdl] |
| ENQCMD / MOVDIR64B / MOVDIRI | - | - | - | - | - | - | - | S | S | S | S | S | A | [K-cpuf][K-sva] |
| UINTR (user IPIs) | - | - | - | - | - | - | - | K | K | K (CPUID set, LIVE) | K | K | K | [P-uintr][K-cpuf][L-crescent] |
| CLDEMOTE, WAITPKG, SERIALIZE | - | - | - | - | - | - | - | S | S | S | ? | ? | A | [K-cpuf][L-crescent] |
| VMD (NVMe domain) | - | - | - | S | S | S | S | S | S | S (ARK 6741P, 6980P) | ? | ? | ? | [K-vmd][I-ARK6741P][I-ARK6980P]; server IDs 0x201d (SKX/CLX), 0x28c0/0x28c1 |
| VROC (NVMe RAID on VMD, hardware key) | - | - | - | s (key) | s | s | s | s (Standard/Premium keys; "Intel SSD Only" key unsupported from SPR on) | s | s | ? | ? | ? | [I-VROCkey]; business and support moved to Graid Technology 2025-11-17 [I-VROCgraid]; Linux side is `mdadm` IMSM (believed) |
| On Demand (SDSi) | - | - | - | - | - | - | - | L | L | ? (interface LIVE, activation unverified) | ? | ? | ? | [K-sdsi][I-OD][L-crescent][R-sdsi-arch] |

### 1c. Memory, topology, tiering

| Feature | v2 | v3 | v4 | SKX | CLX | CPX | ICX | SPR | EMR | GNR | SRF | CWF | DMR | Src |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Cluster-on-Die | - | B | B | - | - | - | - | - | - | - | - | - | - | [K-mon] (breaks resctrl) |
| SNC2/3/4 | - | - | - | B | B | B | B | B (SNC4 on Max) | B | B (SNC3 default 6900P, SNC2 6700P) | B | B | ? | [I-SNC][I-MAX][K-mon] |
| Optane PMem | - | - | - | - | S (100) | s (200, App Direct, 4S) | S (200) | - (cancelled) | - | - | - | - | - | [I-PMEM][I-PMEMend] |
| HBM2e on package | - | - | - | - | - | - | - | s (Max 9400) | - | - | - | - | - | [I-MAX] |
| CXL | - | - | - | - | - | - | - | S (1.1) | S (1.1) | S (2.0) | S (2.0) | S (2.0) | A | [I-EMR][I-X6brief] |
| Flat Memory Mode (hardware DDR+CXL tiering) | - | - | - | - | - | - | - | - | - | B | B | B | ? | [I-FMM] |
| MRDIMM | - | - | - | - | - | - | - | - | - | s (8800 on 6900P; 8000 on 6700P/6500P) | - | - | ? | [I-ARK6980P][I-HPCdeck] |
| LA57 (5-level paging) | - | - | - | - | - | - | S | S | S | S | ? | ? | A | [K-la57] |

### 1d. Isolation and QoS (RDT)

| Feature | v2 | v3 | v4 | SKX | CLX | CPX | ICX | SPR | EMR | GNR | SRF | CWF | DMR | Src |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CMT (LLC occupancy) | - | S | S | B (stepping <=4) / S | S | S | S | S | S | S | S | S | ? | [K-resctrl-core][I-RDT] |
| MBM (total/local) | - | - | S | B (stepping <=4) / S | S | S | S | S | S | S | S | ? | ? | [K-mon][I-RDT] |
| L3 CAT | - | s (6 SKUs, MSR probe) | S | **B** (kernel forces off) | **B** | **B** | S | S | S | S | S | ? | ? | [K-resctrl-core][K-cat-skx] |
| L3 CDP | - | - | S | B | B | B | S | ? (absent from spec table) | S | S | S | ? | ? | [I-RDT] |
| L2 CAT / L2 CDP | - | - | - | - | - | - | - | - | S | S | S | ? | ? | [I-RDT][L-crescent] |
| MBA (gen1 SKX-CPX, gen2 ICX-EMR, gen3 per-thread Xeon 6) | - | - | - | S | S | S | S | S | S | S | S | ? | ? | [I-RDT][K-resctrl-doc] |
| MBA 4.0 (resource-aware) / I/O RDT | - | - | - | - | - | - | - | - | - | K | K | ? | ? | [I-RDT][R-pqos] |
| Region-aware MBA/MBM (DDR vs CXL) | - | - | - | - | - | - | - | - | - | - | - | - | A ("future", RFC only) | [I-RDT][P-ramba] |
| STLB QoS | - | - | - | - | - | - | - | K (MSR only) | ? | ? | ? | ? | ? | [I-RDT] |
| AET per-RMID energy/stalls | - | - | - | - | - | - | - | - | - | - | - | S (kernel 7.0) | A | [K-aet] |
| PKU | - | - | - | S | S | S | S | S | S | S | ? | ? | A | [K-pkeys] |
| Split-lock #AC / bus-lock #DB | - | - | - | - | - | - | S / - | S / S | S / S | S / S (LIVE) | ? | ? | A | [K-buslock][L-crescent] |
| CET IBT + shadow stack | - | - | - | - | - | - | - | S | S | S | ? | ? | A | [K-shstk] |
| LAM / LASS / FRED | - | - | - | - | - | - | - | - | - | **-** (LIVE) | LAM, LASS | FRED (LAM/LASS not listed for CWF: ?) | A (all three) | [I-ISE] Table 1-2; [L-crescent]. Kernel: LAM 6.4, FRED 6.9 (`CONFIG_X86_FRED`, on by default when present per `fred=`), LASS 6.19 [K-cpuf][K-kparams] |
| IPI virtualization | - | - | - | - | - | - | - | S | S | S | S | ? | ? | [I-ISE] (SPR, SRF, GRR); KVM `enable_ipiv` 6.0 [K-vmxcap] |
| SGX large EPC | - | - | - | - | - | - | B | B | B | B | ? | B | ? | [I-SGXicx][K-sgx] |
| TME / TME-MK | - | - | - | - | - | - | B | B | B | B | ? | ? | ? | [I-TMEMK][K-intelc] |
| TDX | - | - | - | - | - | - | - | s | B | B | B | ? | ? | [K-tdx][I-TDXavail] |
| TDX Connect (TDISP) | - | - | - | - | - | - | - | - | - | K | ? | ? | ? | [I-TDXC][K-tsm] |
| TSX RTM | - | B (microcode-disabled, HSE44) | B (errata, TAA) | B | B | B | B | B | B | B (kernel off, LIVE) | ? | ? | ? | [I-E5v3SU][K-tsx][L-crescent] |

### 1e. Power, frequency, telemetry, RAS

| Feature | v2 | v3 | v4 | SKX | CLX | CPX | ICX | SPR | EMR | GNR | SRF | CWF | DMR | Src |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| RAPL pkg + DRAM | S | S (fixed DRAM unit) | S | S | S | S | S | S | S | S | S | S | A | [K-rapl] |
| HWP (Speed Shift) | - | - | S | S | S | S | S | S | S | S | S | S | A | [K-pstate] |
| Uncore frequency driver | - | - (MSR 0x620 only) | S | S | S | S | S | S | S | S (TPMI + ELC) | S | S | A | [K-uncore] |
| SST-PP | - | - | - | - | s (Y SKUs, BIOS) | ? | s (runtime) | s | s | s (TPMI) | s | s (ARK) | ? | [K-sst][I-ARK6990E] |
| SST-BF | - | - | - | - | s (5218N, 6230N, 6252N) | ? | s | s | s | s | ? | ? | ? | [I-SSTBF][K-sst] |
| SST-CP / SST-TF | - | - | - | - | - | - | s | s | s | s | ? | ? | ? | [I-ICXov][K-sst] |
| Priority Core Turbo | - | - | - | - | - | - | - | - | - | s (PCT SKUs, e.g. 6776P, 6962P) | - | - | ? | [I-PCT] |
| TPMI (PM capsules incl. PLR, PEM) | - | - | - | - | - | - | - | - | - | S | S | S | A | [K-tpmi][R-pcm] |
| PMT telemetry (intel_vsec) | - | - | - | - | - | - | - | ? (believed) | ? | S (LIVE) | ? | S | A | [K-pmt][L-crescent] |
| HFI | - | - | - | - | - | - | - | ? | ? | S (flag LIVE) | ? | ? | ? | [K-hfi][L-crescent] |
| PPIN | S | S | S | S | S | S | S | S | S | S (LIVE) | ? | ? | ? | [K-common][K-cpuf] |
| Intel PT | - | - | S | S | S | S | S | S | S | S | ? | ? | ? | [K-pt] |
| Arch LBR | - | - | - | - | - | - | - | S | S | S (LIVE) | ? | ? | ? | [K-cpuf] |
| In-Field Scan (SAF/Array BIST/SBAF) | - | - | - | - | - | - | - | S | S | S | S | S | ? | [K-ifs][K-ifs-core] |
| EDAC (sb/skx/i10nm) | S | S | S | S | S | S | S | S | S | ? | ? | ? | ? | [K-sbedac][K-i10nm] |
| PECI (BMC) | S | S | S | S | S | S | S | S | S | ? | ? | ? | ? | [K-peci] (weak) |
| PFR (MAX 10 root of trust) | - | - | - | - | - | - | s (board) | s (ARK 8480+) | ? | s (ARK 6741P, 6980P) | ? | ? | ? | [I-ICXov][I-ARK8480][I-ARK6741P]; platform-level, needs the board CPLD |
| Platform extras listed for CWF alone: monitorless MWAIT, APIC-timer virtualization, PT trigger tracing, NMI-source reporting, Arch PEBS | - | - | - | - | - | - | - | - | - | - | - | S | Arch PEBS A | [I-ISE] Table 1-2 |
| Cache Bandwidth Allocation (RDT, L1/L2/L3 upstream b/w per CLOS) | - | - | - | - | - | - | - | - | - | - | - | - | ? | [I-ISE] ch. 4.1; no SKU named in Table 1-2 |

---

## 2. Per-feature reference

Columns:
- **Detect**: the exact flag, CPUID bit or sysfs path.
- **Enable**: the step that turns the feature on.
- **Min**: the kernel and userspace minimum.
- **LLM/agent use**: evidence quoted with numbers when it exists.
- **Obs**: obscurity. W = well-known, N = niche, O = obscure.

### 2.1 ISA

| Feature | What | Detect | Enable | Min | LLM/agent use | Obs | Src |
|---|---|---|---|---|---|---|---|
| F16C | FP16<->FP32 vector convert, no FP16 arithmetic | `f16c` | always on | AVX XSAVE kernel | Only FP16 hardware on v2. It halves memory traffic for fp16-stored tensors (engineering inference). | N | [I-E5v2DS][K-cpuf] |
| AVX2+FMA3 | 256-bit int SIMD, gathers, FMA (2x peak FLOPs over AVX1) | `avx2 fma` | always on | any | The practical floor for llama.cpp/oneDNN. "v3 is the oldest Xeon worth running Q4/Q8 decode on" is an opinion (EI). | W | [I-E5v3][R-onednn] |
| AVX-512 F/BW/DQ/VL | 512-bit SIMD, opmasks | `avx512f avx512bw avx512dq avx512vl` + XCR0 bits 5-7 | kernel enables in XCR0 | long-standing | vLLM-CPU lists avx512f as "Recommended" and avx2 as "Limited features". On 1-FMA SKUs, 512-bit FP throughput is about AVX2 x2, so check ARK "# of AVX-512 FMA Units". | W | [R-vllm-doc][I-SKXov][K-xstate] |
| AVX-512 licence downclock + kernel `zmm_exclusion_list` | L0/L1/L2 turbo licences. Grant up to 500 us, ~2 ms hysteresis. The kernel prefers YMM on SKX/ICX and allows ZMM from SPR on. | PMU `core_power.lvl2_turbo_license`; `/proc/<pid>/arch_status` `AVX512_elapsed_ms` | none (avoid) | n/a | On SKX-ICX, isolate ZMM-heavy GEMM threads from scalar tokenizer/HTTP threads. It is a non-issue from SPR on. | N | [I-OPTMAN][K-intelc][K-procdoc] |
| AVX512_VNNI | u8xs8 dot into s32 in one instruction | `avx512_vnni` | on | flag only | Main INT8 lever on CLX/ICX: embeddings, rerankers, Q8 decode (EI) | W | [I-DLB][R-onednn] |
| AVX512_BF16 | VDPBF16PS, FP32->BF16 converts | `avx512_bf16` (CPUID.(7,1):EAX[5]) | on | 5.3 (flag; verified by tag grep) | CPX only before SPR. Ice Lake has none and falls back to FP32/VNNI. | N | [I-BF16][R-gcc] |
| VBMI/VBMI2, BITALG/VPOPCNTDQ | 64-byte byte permutes; vector popcount | `avx512vbmi avx512_vbmi2 avx512_bitalg avx512_vpopcntdq` | on | flags | LUT dequant of 3/4-bit weights; Hamming distance for binary-quantized embeddings (engineering inference) | N | [R-gcc][K-cpuf] |
| IFMA, VAES, VPCLMULQDQ, SHA-NI, GFNI | 52-bit bignum FMA; vector AES and GHASH; SHA; GF(2^8) | `avx512ifma vaes vpclmulqdq sha_ni gfni` | on | flags | TLS handshakes and bulk TLS on agent gateways; SHA-256 prefix-cache keys; erasure coding (ISA-L) (EI) | N/O | [I-ICXov][I-IPPicl] |
| AVX512_FP16 | native FP16 arithmetic, 32 lanes | `avx512_fp16` | on | 5.11 (verified) | FP16 softmax/RoPE/KV dequant (EI). SPR/EMR AMX has no FP16 tile op. | N | [K-cpuf] |
| AVX-VNNI (VEX) | INT8 dot on YMM | `avx_vnni` | on | 5.12 (verified) | The only INT8 dot path on E-core Xeons (SRF/CWF); oneDNN `avx2_vnni` | N | [R-onednn][R-llama-cmake] |
| AVX-VNNI-INT8/INT16, NE-CONVERT, AVX-IFMA | s8xs8 dots, int16 dots, BF16/FP16 load-convert | CPUID.(7,1):EDX[4,5,10], EAX[23]. **Not in /proc/cpuinfo.** | on | KVM pass-through only | oneDNN `avx2_vnni_2` on SRF/CWF. Agents grepping cpuinfo get false negatives. | O | [I-ISE][K-kvmcpuid] |
| AVX10.1/10.2 | versioned AVX-512 umbrella, 512-bit mandatory (rev 3.0) | CPUID.(7,1):EDX[19], then leaf 0x24 EBX[7:0]. Not in cpuinfo. | on | KVM 6.12 | One version probe replaces ~20 bits. GCC 15 deprecates `-mavx10.x-256`. | N | [I-AVX10][R-gcc15ml] |
| APX | R16-R31, NDD, NF, PUSH2/POP2 | CPUID.(7,1):EDX[21]; `X86_FEATURE_APX` unnamed | kernel xstate | 6.16 (verified) | Fewer spills in tokenizer, sampler and grammar code after a recompile (EI) | N | [K-apx][I-ISE] |
| PREFETCHIT0/1 | software code prefetch | CPUID.(7,1):EDX[14]; not in cpuinfo | on | userspace | Warms i-cache for hot Python/sampler paths evicted by GEMM (speculative) | O | [I-ISE] |
| AMX TILE/INT8/BF16 | 8x1 KB tiles + TMUL. Intel peak: 2,048 INT8 ops/cycle/core vs 256 for AVX-512 VNNI (8x); 1,024 BF16 vs 64 FP32 (16x) [provenance:P311]. | `amx_tile amx_int8 amx_bf16`; XFD required | `arch_prctl(ARCH_REQ_XCOMP_PERM=0x1023, 18)` per process. **Cleared on exec.** Needs a sigaltstack of at least `AT_MINSIGSTKSZ`. VMM needs `ARCH_REQ_XCOMP_GUEST_PERM` before the first vCPU. | 5.16 (verified) | The prefill/GEMM lever. Silent failure: oneDNN falls back to AVX-512 if permission is missing. Verify with `ONEDNN_VERBOSE=1` showing `avx10_1_512_amx`. llama.cpp AMX kernels cover quantized types only (Q4_0, Q4_1, Q8_0, Q4_K, Q5_K, Q6_K, IQ4_XS; computed as INT8 tiles), not F16/BF16 [R-llama-amx-types]. vLLM-CPU builds `AVX512_AMX`. | W | [K-xstate][I-AMXbrief][R-llama-amx][R-vllm-cmake][R-onednn] |
| AMX-FP16 | TDPFP16PS | CPUID.(7,1):EAX[21]. **Unnamed in cpuinfo since the 6.11 flip.** | same permission | 6.2 flag | Runs FP16 checkpoints on AMX without a BF16 recast. oneDNN `avx10_1_512_amx_fp16`. | N | [I-ISE][K-flip] |
| AMX-COMPLEX | complex FP16 tile matmul | CPUID.(7,1):EDX[8]; KVM-only | same | KVM 6.6 | vRAN. No LLM value. GNR-D only, not 6700P/6900P. | O | [I-ISE] |
| AMX-2 (FP8, TF32, AVX512, MOVRS) | FP8 E4M3/E5M2 tiles; tile<->ZMM row moves | CPUID.1EH.01H:EAX[4,6,7,8] | same | KVM 7.0 | FP8 weights/KV on CPU (EI). vLLM already has `cpu_attn_amx_fp8.hpp` behind `-mamx-fp8`. AMX-TRANSPOSE was removed in ISE rev 059. | O | [I-ISE][R-vllm-cmake] |
| ENQCMD/MOVDIR64B | 64 B non-posted (status-returning) / posted descriptor writes to WQ portals | `enqcmd movdir64b movdiri` | needs `CONFIG_INTEL_IOMMU_SVM=y` (else the kernel force-disables ENQCMD), `intel_iommu=on,sm_on`, BIOS ENQCMDS | 5.18 user PASID (believed) | Syscall-free submission to shared DSA/IAA WQs from many workers | O | [K-sva][K-kconfig-cf] |
| WAITPKG | UMONITOR/UMWAIT/TPAUSE | `waitpkg`; `/sys/devices/system/cpu/umwait_control` | on | 5.3 (believed) | Low-power polling of DSA completion records (DTO `DTO_WAIT_METHOD=umwait`); UMWAIT for DSA completion is the documented use in [P-asplos] | N | [R-dto] |
| UINTR | ring-3 to ring-3 interrupts. RFC claimed ~9x lower latency than eventfd [provenance:P304]. | CPUID.(7,0):EDX[5]. No kernel flag. | not possible; the RFC was never merged and the repo is archived | none | Dead end. Use busy-poll or UMWAIT. | O | [P-uintr][R-uintr-arch] |
| TSX / TSXLDTRK | hardware transactional memory | `rtm hle`; `tsx_async_abort` vuln file | `tsx=on` | 5.4 | Treat as unavailable: errata HSE44/BDF100, TAA. | N | [K-tsx][I-E5v3SU][I-E5v4SU] |
| ERMS/FSRM/FZRM/FSRS | fast `rep movsb/stosb` | `erms fsrm` | auto | glibc ifunc | memcpy of token/KV buffers (minor) | O | [K-cpuf] |

### 2.2 On-die engines

| Feature | What | Detect | Enable | Min | LLM/agent use + evidence | Obs | Src |
|---|---|---|---|---|---|---|---|
| CBDMA / I/OAT | per-socket descriptor DMA, memcpy and fill. DCA on older parts. | `lspci -nn` 8086:0e2x (v2), 2f2x (v3), 6f2x (v4), 2021 (SKX-CPX), 0b00 (ICX); `/sys/class/dma` | BIOS I/OAT; `ioatdma` | long-standing | The only copy engine before SPR. DSA averages ~2.1x CBDMA throughput [provenance:P310]. Treat it as no LLM-path copy engine. | O | [K-ioat][P-asplos] |
| CBDMA XOR/PQ | RAID engine Intel marketed | not visible; `is_xeon_cb32()` clears the caps | masked by driver policy | — | Do not plan erasure offload on it | O | [K-ioat][I-E5brief] |
| DDIO + MSR 0xC8B IIO_LLC_WAYS | inbound PCIe writes allocate in LLC | `rdmsr 0xc8b`; IIO/CHA PMU | default on; way count by MSR/BIOS. v4 erratum BDF78 forces bits[1:0] on write. | none | NIC/GPU staging stays LLC-resident. Too many inbound writes thrash LLC ("leaky DMA"). | O | [I-DDIO][I-E5v4SU] |
| DSA 1.0/2.0/3.0 | memmove, fill, compare, CRC, DIF, delta, dualcast, cache flush. 2.0 adds inter-domain ops and an event log. 3.0 adds SGL, type conversion and reduce. | 8086:0b25 (SPR/EMR/GNR/SRF), 0x11fb (GNR-D), 0x1212 (DMR); `/sys/bus/dsa/devices/dsa*`; `op_cap`, `pasid_enabled` | BIOS VT-d + ENQCMDS; `intel_iommu=on,sm_on`; `accel-config load-config -c <json> -e` | idxd 5.6; DSA 2.0 caps 6.4; DSA 3.0 + SGL 7.0 | ASPLOS'24 (Intel co-authors): one DSA ~30 GB/s, four ~90 GB/s for >64 KB [provenance:P310]. Async offload beats the CPU from ~256 B, sync from ~4 KB. No published LLM-serving numbers. | N | [K-idxd][I-DSAspec][P-asplos] |
| INTEL-SA-01084 gate | WQ-portal mmap returns EPERM without CAP_SYS_RAWIO; non-privileged users go through `write()` | `user_submission_safe = false` in `idxd/init.c` | grant `CAP_SYS_RAWIO` to the pod | in 7.3 (backport version unverified) | Hidden reason DTO/DML pods silently fall back to the CPU | O | [K-idxd][K-idxd-cdev] |
| DTO | LD_PRELOAD memcpy/memset/memcmp to DSA with a CPU split, NUMA-aware, auto-tune | `DTO_COLLECT_STATS=1` | SWQs + `LD_PRELOAD=libdto.so`, `DTO_MIN_BYTES` | SVA kernel | Zero-code trial under Python/PyTorch serving (EI). Intel DML is **archived** (GitHub, last push 2025-03-27); intel/DTO is active. | O | [R-dto][R-dml] |
| IAA + iaa_crypto | DEFLATE (4 KB window), CRC64, scan/filter | 8086:0cfe (SPR-GNR), 0x1216 (DMR), 0xb02d (PTL), 0xfd2d (WCL); `/sys/bus/dsa/devices/iax*`; `grep deflate-iaa /proc/crypto` | `modprobe iaa_crypto`; `echo deflate-iaa > /sys/module/zswap/parameters/compressor`; driver knobs at `/sys/bus/dsa/drivers/crypto/{verify_compress,sync_mode}` | iaa_crypto 6.8 | IAA zswap = a compressed RAM tier for idle agent heaps (EI). **Not for pinned KV**: zswap only sees pages on their way to swap; page-locked host buffers used for GPU/XPU KV offload are never swapped [K-zswapdoc][M-mlock]. Defaults are `verify_compress=1` and `sync_mode=sync` [K-iaa]. Batching patch (unmerged): +49% to +55% throughput vs sequential IAA; v4 cover letter: +39% throughput and -41% sys time vs zstd (vendor) [provenance:P305]. | N/O | [K-iaa][P-zswapbatch][K-zswap] |
| QAT Gen4 (4xxx/401xx/402xx) + Gen5 (420xx) | sym/asym crypto, DEFLATE/LZ4. At most two services per device. Gen5 (GNR-D) adds wireless ciphers SNOW3G/ZUC [R-qatlib][I-X6SoCvRAN]. | 8086:4940 (4xxx, SPR/EMR), 4942 (401xx), 4944 (402xx, Xeon 6 SRF/GNR), 4946 (420xx = **Gen5**, GNR-D); `qat/state`, `cfg_services` | `echo down > state; echo 'sym;asym' > cfg_services; echo up`; VFs via `sriov_numvfs` + vfio-pci | driver 5.11; QATlib minimums: 4xxx 5.15.3, 401xx 5.19, 402xx 6.4, 420xx 6.8 [R-qatlib-req]; rate limiting 6.7; telemetry 6.8 | NGINX TLS 1.3 whitepaper: up to 6.3x handshake performance at 1C2T; ~40k CPS with 6 fewer cores and 20 W less (vendor) [provenance:P312]. Go proxies (Caddy, cloudflared): no QAT path found, because Go `crypto/tls` has no engine/provider hook (believed). | W | [K-qat-abi][I-QATnginx] |
| QAT Gen6 (6xxx) | three concurrent services, hardware zstd, dictionary compression, KPT | 8086:4948 (VF 4949); platform = DMR "Intel Xeon 7"; firmware "Pending" in QATlib table | qatlib 26.08 | 6.16; zstd 7.1; KPT 7.2 | Hardware zstd for checkpoint/KV snapshots (EI). Gen6 limits: sym only AES-CTR/GCM/XTS and ChaCha-Poly, no static deflate [R-qatlib]. SVN commit is irreversible. | O | [K-qat6][R-qatlib] |
| DLB 2.0/2.5 | hardware atomic/ordered queues; "~100M sched/s" **unverified** (not in DPDK dlb2 doc or Intel DLB pages read) | 8086:2710 / 2714 | out-of-tree driver (Intel `dlb_linux_src_release_8.13.0`) or DPDK PF PMD on vfio-pci | none upstream | Hardware MPMC dispatch for a C router (EI). **K8s DLB plugin removed 2026-09-01** (commit be6c943e4f). | O | [R-dpdk-dlb][R-dlbrm] |
| On Demand / SDSi | signed AKC+CAP raises DSA/IAA/QAT/DLB counts (up to 4 each per socket) | `/sys/bus/auxiliary/devices/intel_vsec.sdsi.*/{state_certificate,meter_current}` | OEM licence, `provision_akc`, `provision_cap`, cold reboot | 5.18; meter 6.2/6.10 | Silicon present does not mean enabled. ARK defaults: 8480+ 1 of each (and "On Demand Activation Model: No"); 6980P 4 of each; 6741P 3 of each (matches crescent LIVE); Xeon 6 SoC 6716P-B 1 QAT/DLB/DSA, **0 IAA**. `intel/intel-sdsi` was archived (last push 2025-11-10). | O | [K-sdsi][I-OD][I-ARK8480][I-ARK6980P][R-sdsi-arch] |
| P2PDMA to QAT/DSA/IAA | whitelists accelerator root buses | kernel >= 7.2 | auto | 7.2 | NVMe/NIC <-> accelerator without a host bounce | O | [K-p2p] |
| Page-migration DMA offload | migrate_pages batching to dmaengine (AMD RFC v6) | — | patched kernel | unmerged | Vendor: ~7.5x folio_copy, ~6.3x move_pages for 512x2 MB folios [provenance:P313] | O | [P-migrate] |

### 2.3 Memory and tiering

| Feature | What | Detect | Enable | Min | LLM/agent use + evidence | Obs | Src |
|---|---|---|---|---|---|---|---|
| SNC | splits a socket into NUMA domains | `numactl -H`; dmesg "Sub-NUMA Cluster mode detected" | BIOS | resctrl SNC 6.11. **Use 6.19+ on SNC3** (sched-domain fix). | One replica per SNC node gets local channels (EI). Recompute CAT masks per node. | N | [K-mon][K-snc3fix][I-SNC] |
| Xeon Max HBM | 64 GB HBM2e/socket: HBM-only, Flat (1LM) or Cache (2LM) | CPU-less nodes in `numactl -H` | BIOS; `daxctl reconfigure-device -m system-ram` | HMAT 5.2 | Weights/KV on HBM nodes with `numactl -p` for bandwidth-bound decode (EI) | N | [I-MAX] |
| CXL Type 3 | CPU-less NUMA node | `cxl list`, `/sys/bus/cxl/devices/mem*` | BIOS + region + dax kmem | 5.12 / CDAT 6.0 | Cheap capacity for KV spill and idle agent state (EI) | N | [I-HPCdeck][K-cxl] |
| Flat Memory Mode | hardware 64 B swap DDR<->CXL, 1:1 ratio | one node holding DDR+CXL | BIOS | OS-transparent | OSDI'24: 82% of 115 workloads <=5% slowdown in mixed mode, outliers up to 34% [provenance:P306] | O | [I-FMM][P-osdi] |
| Kernel tiering | demotion, `numa_balancing=2`, weighted interleave (+auto 6.16), DAMOS migrate_hot/cold | `/sys/kernel/mm/numa/demotion_enabled`, `/sys/kernel/mm/mempolicy/weighted_interleave/` | sysfs/sysctl writes | 5.15 / 5.18 / 6.9 / 6.11 / 6.16 | Keep hot KV in DDR, demote idle sessions to CXL (EI). Demotion ignores strict `cpuset.mems`. | N | [K-numa-abi][K-damon] |
| MRDIMM | dual-rank mux; Intel: peak bandwidth "almost 40%" above RDIMM (6,400 to 8,800 MT/s) [provenance:P307] | `dmidecode -t memory` speed 8800/8000 | populate 1DPC; **per SKU**: 6980P lists MRDIMM 8800, **6741P lists DDR5-6400 only** [I-ARK6980P][I-ARK6741P] | transparent | Intel: Llama-3-8B int8 summarization 1.20x at BS1 and 1.33x at BSX (MRDIMM-8800 vs DDR5-6400, 128-core Xeon 6) [provenance:P308] | N | [I-HPCdeck][I-MRDIMMnews] |
| Huge pages (1G, mTHP, HVO) | fewer TLB misses | `pdpe1gb`; `/proc/meminfo HugePages_*` | `hugepages=N`, THP per-size knobs | 3.16 / 5.14 / 6.8 | 1G pages for weights; no LLM numbers recorded | W | [K-thp] |
| Optane PMem | Memory Mode / App Direct | `ipmctl`, `ndctl` | ipmctl goal | libnvdimm | Legacy only. Product exited. | W | [I-PMEMend] |

### 2.4 RDT

| Feature | What | Detect | Enable | Min | LLM/agent use | Obs | Src |
|---|---|---|---|---|---|---|---|
| CMT | per-RMID LLC occupancy | `cqm_occup_llc`; `/sys/fs/resctrl/info/L3_MON/mon_features` | `mount -t resctrl resctrl /sys/fs/resctrl`, mon_groups | 4.14 | Finds the agent evicting the router/tokenizer L3 (EI) | W | [K-resctrl-doc] |
| MBM + `mba_MBps` | per-RMID bytes; kernel feedback loop to a MiB/s cap | `cqm_mbm_total/local`; `mbm_total_bytes` | `mount -o mba_MBps`; `MB:0=<MiBps>`; `mba_MBps_event` (6.14) | 4.14 / 4.18 / 6.14 | Bandwidth budgets per tenant for bandwidth-bound CPU decode (EI). On v4/SKX use the OS interface: pqos MSR mode skips the BDF102 correction factor. | N | [K-mon][I-E5v4SU] |
| L3 CAT | way masks per CLOS | `cat_l3 rdt_a`; `info/L3/{cbm_mask,num_closids}` | schemata `L3:0=ff` | 4.10 | Pin L3 ways for latency threads (EI). **SKX/CLX/CPX (model 0x55): kernel forces it off (erratum SKZ18); stepping <=4 also loses CMT/MBM. Boot with `rdt=l3cat`.** v3: 6 SKUs found by MSR probe (`cache_alloc_hsw_probe`). | O | [K-resctrl-core][K-cat-skx] |
| CDP L3/L2 | separate code/data masks | `cdp_l3 cdp_l2` | `mount -o cdp,cdpl2` | 4.10 / 4.16 | Keep a large Python/vLLM code footprint hot (EI) | N | [I-RDT] |
| L2 CAT | per-L2 way masks (per core on P-cores; per module on E-cores, believed) | `cat_l2`; `info/L2` | schemata `L2:` | 4.10 | Shields E-core module neighbours on SRF/CWF (unverified for LLM) | O | [I-RDT] |
| MBA gen1-3 | request-rate throttling per CLOS | `mba`; `info/MB/thread_throttle_mode` | `MB:0=50` | 4.12 | Cap batch agents (EI). Pre-GNR, SMT siblings take the lower % ("max" mode). | N | [I-RDT][K-resctrl-doc] |
| MBA 4.0, I/O RDT | resource-aware throttling; RMID/CLOS for PCIe/CXL device traffic and DDIO fill | MBA_CFG MSR; ACPI IRDT | intel-cmt-cat >= v23.11, MSR mode; not in resctrl | none | Limits how much L3 GPU/NIC DMA evicts from CPU agent threads (EI) | O | [I-RDT][R-pqos] |
| AET (`PERF_PKG_MON`) | per-RMID core Joules, activity, LLC stalls, uops | `/sys/fs/resctrl/info/PERF_PKG_MON` | resctrl; `rdt=` to force | 7.0 | Joules per agent / per tool-call with no perf multiplexing | O | [K-aet][K-resctrl-doc] |
| Region-aware MBA/MBM | per-region (DDR/CXL) counters and caps over MMIO | ACPI ERDT/MRRM | intel-cmt-cat v26.03 | RFC only (Aug 2026) | Throttle CXL KV-tier traffic separately (speculative) | O | [I-RDT][P-ramba] |
| STLB QoS | STLB way masks | IA32_CORE_CAPABILITIES | raw MSR | none | Speculative | O | [I-RDT] |

### 2.5 Power, frequency, telemetry, RAS

| Feature | What | Detect | Enable | Min | LLM/agent use + evidence | Obs | Src |
|---|---|---|---|---|---|---|---|
| SST-PP/BF/CP/TF | profiles; high-priority (HP) base cores; CLOS power priority; HP turbo | `/dev/isst_interface`; `intel-speed-select perf-profile info / base-freq info / turbo-freq info` | `intel-speed-select ... enable` (BIOS may lock) | 5.3; TPMI 6.4; SST-TF rev 2 6.16 | Put router, tokenizer, sampling and GPU-feeder threads on HP cores (EI). Example 6980P: 48 HP cores @2.2 vs 80 @1.8 GHz; crescent's 6741P: 12 HP @2.6 vs 36 LP @2.1 GHz, SST-TF yes, SST-PP no [I-ARK6741P]. | N | [K-sst][I-SSTBF][I-ARK6980P] |
| Priority Core Turbo | 4 or 8 HP cores per socket at P0max (e.g. 4.6 GHz); LP cores capped near P1. On by default. HP = CLOS0. | `intel-speed-select turbo-freq info -l 0` (bucket-0 HP count) | BIOS knobs; runtime remap via `core-power assoc` | TPMI SST 6.4 | Intel targets TTFT and tail latency on GPU-host CPUs | O | [I-PCT][R-oz-pct] |
| Uncore freq + ELC | mesh/LLC/IMC clock; ELC floor when utilization is low | `/sys/devices/system/cpu/intel_uncore_frequency/uncore*/elc_*` | sysfs writes | 5.6; TPMI 6.5; ELC 6.12 | Bursty agent traffic pays uncore ramp (EI). Intel: scaled uncore costs <5% on some latency-sensitive workloads [provenance:P309]. | O | [K-uncore][I-X6profiles] |
| TPMI PLR | per-die limit reason (POWER_PKG_PL1, THERMAL_PER_CORE, PROCHOT, ...) | `/sys/kernel/debug/tpmi-*/plr/domain*/status` | `CONFIG_INTEL_PLR_TPMI`, debugfs | 6.11; writable to clear 2026 | Could explain tokens/s dips during AMX prefill (EI) | O | [K-plr][K-tpmi-abi] |
| PMT | GUID-keyed MMIO telemetry | `/sys/class/intel_pmt/telem*/guid` | decode with intel/pmt XML | 5.11 | Fleet energy/residency without perf. The substrate of AET. | O | [K-pmt][R-pmt] |
| RAPL | package/DRAM/psys energy + PL1/PL2 | `/sys/class/powercap/intel-rapl*` | — | 3.14+ | Joules per token. HSX/BDX DRAM unit is fixed at 2^-16 J (kernel quirk). | W/O | [K-rapl] |
| HWP/EPP | CPU-chosen P-states | `hwp hwp_epp` | EPP=performance | long-standing | Cuts ramp for bursty requests (EI). v4 BDF79: turbo delayed after C6. | W | [K-pstate][I-E5v4SU] |
| IFS | scan/BIST on live cores for silent data corruption | `/sys/devices/virtual/misc/intel_ifs_*/status` | `intel_ifs` + image in `/lib/firmware/intel/ifs_<n>/` | 5.19 / 6.4 / 6.12 | A core with silent data corruption could corrupt logits (EI). Scan on drain. | O | [K-ifs] |
| PPIN | per-die ID | `/sys/devices/system/cpu/cpu0/topology/ppin` | BIOS may lock | sysfs ~5.18-6.0 (unverified) | Ties MCE and benchmark results to a physical die | O | [K-common] |
| HFI | per-CPU perf/efficiency table | `hfi` | `CONFIG_INTEL_HFI_THERMAL` | 5.18 | Present on GNR (LIVE). No server use found. | O | [K-hfi][L-crescent] |
| TDX / TDX module runtime update | confidential VMs; PAMT ~1/256 RAM | `tdx_host_platform`; `/sys/module/kvm_intel/parameters/tdx` | BIOS TME+TME-MK+TDX; `kvm_intel tdx=1` | host 6.8; KVM 6.16; runtime update 7.2 | Isolates untrusted agent sandboxes (EI) | N | [K-tdx] |
| SGX | enclaves, up to 512 GB EPC | `sgx sgx_lc`; `/dev/sgx_enclave` | BIOS (+TME on ICX) | 5.11 | Credential brokers for agent tools; not whole models (EI) | W | [K-sgx][I-SGXicx] |
| Split/bus-lock detect | #AC/#DB on bus-locking atomics | `split_lock_detect bus_lock_detect` | `split_lock_detect=` | 5.7 / 5.12 | Stops one tenant from stalling the socket | O | [K-buslock] |

### 2.6 Additions (review pass 2026-10-20)

Features the first pass omitted or covered only in the matrix. Same columns as 2.2.

| Feature | What | Detect | Enable | Min | LLM/agent use | Obs | Src |
|---|---|---|---|---|---|---|---|
| QAT generations (map) | Gen1 DH89xxCC (out of tree); DH895xCC card (`qat_dh895xcc`); Gen2 C62x PCH (`qat_c62x`), C3xxx Atom (`qat_c3xxx`); Gen4 4xxx/401xx/402xx (`qat_4xxx`); Gen5 420xx GNR-D (`qat_420xx`); Gen6 6xxx DMR (`qat_6xxx`). Ice Lake-D QAT: **no mainline driver** | `lspci -d 8086:` per ID; `/sys/bus/pci/drivers/qat_*` | per gen, see 2.2 | see 2.2 | TLS/compression offload only (EI) | N | [K-qat-kconfig][R-qatlib-req] |
| Xeon D / Xeon 6 SoC integrated QAT | QAT on die or package. ARK lists "Integrated Intel QuickAssist Technology" on D-2796NT and 6716P-B | ARK field; `lspci` | BIOS + driver (Ice Lake-D: Intel out-of-tree QAT 1.x package, believed) | GNR-D 420xx: kernel 6.8, QATlib 24.02 | IPsec/TLS at the edge; Intel quotes up to 200 Gb/s IPsec on Xeon 6 SoC (vendor) [provenance:P314] | N | [I-ARKD2796NT][I-ARK6716PB][I-X6SoCvRAN][R-qatlib-req] |
| vRAN Boost (VRB1/VRB2) | integrated 4G/5G FEC: LDPC enc/dec, Turbo, rate (de)match, HARQ in host DDR, FFT for SRS; VRB2 adds MLD-TS | 8086:57c0 (VRB1, SPR-EE), 8086:57c2 (VRB2, GNR-D); ARK "Intel vRAN Boost: Yes" | vfio-pci/UIO + DPDK `baseband/acc` PMD (`pf_bb_config` for PF setup, believed) | DPDK bbdev; no kernel driver | None for LLM serving (EI) | O | [R-dpdk-vrb1][R-dpdk-vrb2][I-ARK6716PB] |
| Integrated Intel Ethernet (Xeon D / SoC) | on-die MAC: D-1500 X552 (believed), D-2100 X722 with iWARP (believed), D-1700/2700 E823 up to 100 GbE, Xeon 6 SoC up to 200 Gb/s (8x25G) with IEEE 1588 PTP + SyncE | `lspci` Ethernet class; ARK "Integrated LAN" | mainline `ixgbe` (X550EM_x IDs), `i40e` (X722 IDs), `ice` (E823-C/L IDs; E825-C also present, platform mapping unverified) | long-standing; E823 in `ice` | Edge agent boxes without a discrete NIC (EI) | N | [I-ICLD][I-X6SoCvRAN][K-ice][K-ixgbe][K-i40e] |
| Media Transcode Accelerator | select Xeon 6 SoC SKUs only (e.g. 6553P-B, 6756P-B) | ARK/OEM SKU table | — | unverified | Video agent pipelines (EI) | O | [I-X6edge]; SKU list from Supermicro table (secondary) [SM-X6SoC] |
| VMD + VROC | VMD remaps NVMe root ports into a PCI domain (hot-plug, LED mgmt); VROC = RAID over VMD, gated by a hardware key | `lspci -d 8086:201d` / `8086:28c0` / `28c1`; `vmd` driver | BIOS VMD per root port; VROC key; `mdadm` IMSM | `vmd` long-standing | Checkpoint/weights RAID-0 without a HBA (EI). **Business transferred to Graid Technology 2025-11-17**; "Intel SSD Only" key unsupported from SPR on | N | [K-vmd][I-VROCgraid][I-VROCkey][I-ARK6741P] |
| PCONFIG | configures TME-MK keys | `pconfig` | firmware/TDX path | flag only | None (EI) | O | [K-cpuf][R-gcc] |
| Key Locker | AES with wrapped handles (AESENC128KL etc.), keys never in GPRs after LOADIWKEY | CPUID.19H; `aeskle` not in mainline | **not possible on mainline 7.3** | none (v9 series, 2024-03, unmerged) | None on Xeon P-cores; SRF per GCC only | O | [P-keylocker][R-gcc][K-cpuf] |
| USER_MSR (URDMSR/UWRMSR) | ring-3 MSR read/write gated by an OS-managed bitmap | CPUID.(7,1):EDX[15] (believed) | needs kernel support; none found in 7.3 cpufeatures | none | Low-overhead user-space telemetry reads on CWF (EI) | O | [I-ISE][K-cpuf] |
| RAO-INT | posted atomic AADD/AAND/AOR/AXOR executed near memory/LLC | CPUID.(7,1):EAX[3] (believed) | userspace | — | Shared counters/histograms across many agent threads (EI). **No shipping SKU** per ISE 061 | O | [I-ISE] ch. 5 |
| MSRLIST / WRMSRNS | batched and non-serializing MSR access | `wrmsrns` unnamed | kernel-internal | WRMSRNS used by 6.9 | Kernel entry cost only | O | [I-ISE][K-msr] |
| Monitorless MWAIT | MWAIT with ECX[2]=1 sleeps without a MONITOR range | CPUID.05H:ECX[3] | kernel idle code | unverified | Idle-state latency only (EI) | O | [I-ISE] ch. 9 |
| IPI virtualization | posted IPIs between vCPUs without VM exit | VMX tertiary controls | `kvm_intel enable_ipiv=1` (default on when supported, believed) | KVM 6.0 | Lower IPI cost for agent sandboxes in VMs (EI) | O | [I-ISE][K-vmxcap] |
| Cache Bandwidth Allocation | RDT throttle of L1->L2->L3 upstream bandwidth per CLOS | CPUID.10H.05H | MSRs; no resctrl support found | none | Finer noisy-neighbour control than MBA (EI). No SKU named | O | [I-ISE] ch. 4.1 |
| TXT / Boot Guard | measured launch and verified boot | ARK fields ("Yes" on 8480+, 6741P, 6980P) | BIOS + tboot | tboot | Host attestation for agent fleets (EI) | W | [I-ARK8480][I-ARK6741P][I-ARK6980P] |

---

## 3. Hidden gems for LLM and agent serving

Confidence is a probability that the lever gives a measurable gain on a k3d-gpu target host. Each experiment follows EXPERIMENTS.md rules: pre-registered, matched load, A/B.

| # | Gem | Why it has leverage | Confidence | Experiment that would prove it |
|---|---|---|---|---|
| G1 | **AMX permission and actual use** | Intel peak gives AMX 8x INT8 and 16x BF16 over AVX-512 [I-AMXbrief] [provenance:P311]. oneDNN drops silently to AVX-512 when `ARCH_REQ_XCOMP_PERM` is missing or the VM hides XFD [K-xstate][R-onednn]. | 0.8 that some current path does not use AMX on crescent | On crescent, in a pod: `ONEDNN_VERBOSE=1` OVMS/vLLM-CPU prefill. Control arm `ONEDNN_MAX_CPU_ISA=AVX10_1_512`. Expect prefill tok/s ratio >= 2. |
| G2 | **IAA-backed zswap** for idle agent state | IAA WQs are already bound to iaa_crypto on crescent, but zswap is off, so nothing consumes them [L-crescent]. Intel batching numbers (vendor, unmerged patches): +39% throughput, -41% sys vs zstd [P-zswapbatch] [provenance:P305]. Scope: swappable anonymous pages only; page-locked KV-offload buffers are out of reach [K-zswapdoc][M-mlock]. | 0.6 for density (agents per box under memory cap); 0.3 for latency | Memory-capped node (cgroup `memory.max`), N idle agents + 1 active. Arms: zswap off / lzo / deflate-iaa (`verify_compress` 1 vs 0). Metrics: max N at p95 SLO, `/sys/kernel/debug/zswap`, sys time. |
| G3 | **DSA for KV/weight copies** (DTO or an engine hook) | One DSA ~30 GB/s, async beats the CPU from 256 B [P-asplos] [provenance:P310]. Frees cores for tokenization. Blockers: CAP_SYS_RAWIO gate [K-idxd]; DML archived [R-dml]. | 0.35. No LLM numbers exist; copies may not be on the critical path. | `DTO_COLLECT_STATS=1` under vLLM-CPU with CPU KV swap. If more than 5% of time is memcpy above 64 KB, run DTO on/off at a matched batch. |
| G4 | **SST-TF / PCT HP cores** for GPU-feeder and latency threads | Intel targets TTFT and tail latency [I-PCT]. k3d-gpu already has a `latency` balloon fed from CPPC favored CPUs (client only). ARK: 6741P has SST-TF and SST-BF (12 HP @2.6 GHz vs 36 LP @2.1 GHz base) [I-ARK6741P]. | 0.5 on a PCT/SST-TF SKU; crescent's turbo table is flat (38/37, ARK 3.8/3.7), and PCT is not an ARK field, so PCT on 6741P is unverified [L-crescent] | Read `intel-speed-select turbo-freq info`. Pin the 4x Battlemage feeder threads to HP cores vs LP cores; measure TTFT p95. |
| G5 | **Uncore ELC floor** on bursty decode | Default ELC can downclock between bursts [K-uncore][I-X6profiles]. Crescent already sits at max with ELC unset [L-crescent]. | 0.25 on crescent (already at max), 0.5 on default-profile Xeon 6 | Bursty agent trace. Arms: ELC floor 0 vs 2.0 GHz. p95 ITL and energy/token (H29 template). |
| G6 | **RDT L3 CAT + MBA for noisy agents** | Decode is DRAM-bandwidth bound [I-HPCdeck]. MBA caps neighbours [I-RDT]. On SKX/CLX/CPX, CAT is hidden until `rdt=l3cat` [K-resctrl-core]. | 0.5 on Xeon (H26 was INCONCLUSIVE on a client without RDT) | Crescent: mount resctrl. Decode replica in CLOS1, a STREAM neighbour in CLOS2 at `MB:0=30`. Measure decode p95 inflation vs unthrottled. |
| G7 | **SNC replicas** | SNC3 on 6900P gives one node per compute die [I-SNC]. Crescent runs SNC off [L-crescent]. | 0.4 (Phoronix secondary results are mixed [P-phx]) | BIOS SNC2 on crescent (owner window). N replicas pinned per node vs SNC off, same total cores. Aggregate tok/s. |
| G8 | **TPMI PLR + PMT for auto-tuning** | Gives the exact limit reason per die [K-plr]. Turns `tune` from black-box into closed-loop. | 0.7 as a diagnostic; speed gain indirect | Clear PLR, run AMX prefill, read status. Correlate with tok/s dips. |
| G9 | **CXL tiering / Flat Memory Mode** | 82% of workloads <=5% slowdown with doubled capacity [P-osdi] [provenance:P306]. | 0.3; crescent has no CXL device | Needs hardware. Weighted-interleave decode A/B when a Type-3 device exists. |
| G10 | **AMX-FP16 / AVX512_FP16 for FP16 checkpoints** | Skips the BF16 recast on GNR [I-ISE]. Hidden from cpuinfo [K-flip]. | 0.4 | OpenVINO `inference_precision=f16` vs bf16 on crescent: tok/s and accuracy. |
| G11 | **DLB for request scheduling** | Hardware MPMC with atomic/ordered queues [R-dpdk-dlb]; throughput figure unverified | 0.05 for k3d-gpu (no in-tree driver, K8s plugin removed) [R-dlbrm] | Not worth a run. Report presence only. |
| G12 | **UINTR / ENQCMD low-latency queues** | UINTR ~9x vs eventfd (RFC) [P-uintr] [provenance:P304] | 0.0 UINTR (unmerged); ENQCMD only through DSA/IAA | No experiment. ENQCMD is covered by G3. |
| G13 | **AET per-RMID Joules** (CWF) | Per-agent energy without perf [K-aet]. Fits H21 joule-pack. | 0.6 as telemetry on CWF; no host available | On a CWF host: AET vs RAPL x cgroup-time split for the H21 arms. |
| G14 | **IFS screening** | Silent data corruption on serving cores [K-ifs] | correctness, not speed | Load an image, scan drained cores, record status. |
| G15 | **On Demand instance counts** | Idle silicon can be licensed instead of buying hosts [I-OD] | 0.2 on Xeon 6 (repo archived, activation unverified) [R-sdsi-arch] | Read `state_certificate` on crescent; ask Supermicro whether activation is offered. |
| G16 | **MRDIMM** | 1.20x/1.33x Llama-3-8B (Intel) [I-HPCdeck] [provenance:P308] | **0 on crescent**: ARK for 6741P lists DDR5-6400 only, no MRDIMM [I-ARK6741P]. 0.7 on an MRDIMM SKU (e.g. 6980P, MRDIMM 8800) | Needs a different CPU, not a DIMM swap. |

---

## 4. k3d-gpu gap list

State read from `scripts/k3d-gpu` at `a32a9701`, `docs/intel-capability-map.md` (G1-G17), `docs/intel.md` and fixtures (`xeon-gnr`, `xeon-srf`, `xeon-gnrd`, `xeon-dmr`, `xeon-spr-accel`, `xeon-max-*`, `qat-420xx`).

Since then (branch `claude/on-die-autodetect`, fixture `xeon-6741p`): `k3d-gpu test` runs an AMX probe pod
(`ARCH_REQ_XCOMP_PERM` plus one oneDNN BF16 matmul with `ONEDNN_VERBOSE=1`, failing unless an AMX
implementation ran); doctor reads AVX10/AMX-FP16/AVX-VNNI-INT8 from CPUID (`/dev/cpu/0/cpuid` as root, else the
CPUID instruction through python3; the model table is the labelled fallback), runs read-only `intel-speed-select
base-freq/turbo-freq info` (host copy, or the node image's pinned one), feeds SST-BF high-priority CPUs into
`k3d-gpu.io/favored-cpus` and the latency balloon, and prints SST-BF/TF, SNC2/SNC3 (Linux 6.19 for multi-socket
SNC3), `rdt=l3cat` on model 0x55 and CXL tiering as suggestions with reverts; it reports QAT `cfg_services`, DLB,
In-Field Scan, the On Demand `state_certificate` size and the 6741P ARK row. `doctor --perf` decodes TPMI PLR,
shows the uncore clock next to ELC (report only) and checks Xeon 6 channels and MRDIMM per SKU. IAA kernel WQs /
zswap and DSA accel-config are #162's. Later (branch `claude/ondie-followups`): doctor reports vRAN Boost
detect-only (PFs 8086:57c0 VRB1 and 8086:57c2 VRB2 with their driver, VFs not counted; no kernel driver, so
nothing is exposed) and keeps those ids out of the unbound-GPU `force_probe` hint; the DSA/IAA `ok` line names
the `CAP_SYS_RAWIO` gate (INTEL-SA-01084) and the RDT opt-in line carries its revert.

Status values:
- **A+E**: auto-detected and enabled (label, plugin or balloon).
- **D**: detected and reported by `doctor`, no auto-adapt.
- **M**: missing.

Auto-adapt rule (from docs/intel.md): the launcher never changes host config. Host changes are printed as suggestions with an exact apply and revert, the same way `perf_flag` and `exp_xe_sriov` do now.

| Feature | Status today (evidence) | Proposed auto-detect + safe default | Effort | Risk |
|---|---|---|---|---|
| AMX labels incl. AMX-FP16/COMPLEX by model | **A+E**: `amx_labels`, model table 6/173, 6/174, 19/1 (`scripts/k3d-gpu` L593-622) | Add a `test` AMX pod that calls `arch_prctl(ARCH_GET_XCOMP_SUPP)`/`REQ_XCOMP_PERM` and runs one oneDNN matmul with `ONEDNN_VERBOSE=1`. Fail if no `amx` impl. | S | low |
| ISA labels (VNNI, AVX-512, VAES...) | **A+E**: `cpu_isa_labels` | Read CPUID directly (`/dev/cpu/0/cpuid` as root, or a static helper in the image) for leaf 0x24 version, AVX-VNNI-INT8, AMX-FP16. Today these are model-predicted (L668-681, the "ISA predicted by gcc" info line). | S-M | low |
| DSA | **A+E** only when user WQs exist (`has_dsa`). Disabled devices produce a generic warn: "configure them on the host with accel-config" (L2068). | When `dsa*` state=disabled: print an exact `accel-config load-config -c <rendered json> -e` (1 shared user WQ per device) and revert `accel-config disable-device dsaN`. Check `pasid_enabled`, `sm_on`, CONFIG_INTEL_IOMMU_SVM. Warn that pods need `CAP_SYS_RAWIO` (INTEL-SA-01084). Note accel-config is absent. | M | low (suggest only) |
| IAA | **A+E** only for user WQs. Kernel WQs bound to iaa_crypto are **invisible** (`has_iaa` checks `/dev/iax/wq*`). | Detect `iaa_crypto` WQs + `/sys/module/zswap/parameters/{enabled,compressor}`. Suggest zswap `deflate-iaa` with exact revert to the recorded compressor and `enabled`. Report the `/proc/crypto` deflate priority (qat 4001 > iaa 300) and `verify_compress`. | S | medium (zswap changes reclaim host-wide; suggest only) |
| QAT | **A+E** with VFs. **D** with no VFs: warn plus an `sriov_numvfs` hint (L2086). No exact revert printed in that line. | Print run/revert like `exp_xe_sriov`. Report `cfg_services` per PF. Note in-kernel crypto already uses PFs. | S | low |
| DLB | **M** (0 mentions) | Detect 8086:2710/2714. Info line: "present, no in-tree driver, K8s plugin removed upstream". No action. | XS | none |
| vRAN Boost / QAT Gen5-6 / integrated Ethernet | **M** | Detect 8086:57c0/57c2 (info only), 8086:4946/4948 (QAT 420xx/6xxx; check the fixtures' `qat-420xx` labels say Gen5, not Gen4) | XS | none |
| On Demand / SDSi | **D**: info when `intel_vsec.sdsi.*` exists (L1107) | Add `state_certificate` size (32 B = no licence payload, base SKU) and the per-socket ARK comparison it already prints | XS | none |
| Per-socket accelerator count vs ARK | **D** (`doctor_accel_sockets`) | Add DLB. Add crescent's 6741P to `xeon_ark_accels`: ARK = 3 default devices each of QAT/DLB/DSA/IAA [I-ARK6741P]. Add Xeon 6 SoC (e.g. 6716P-B: 1/1/1, IAA 0) [I-ARK6716PB]. | XS | none |
| NUMA/SNC replicas | **A+E**: labels `numa-nodes`, `snc`; `replica` balloon | When GNR 6700P/6900P has SNC off: suggest the BIOS SNC2/SNC3 option (owner) and a kernel >= 6.19 check for SNC3 | XS | none |
| RDT CAT/MBA | **D**: info lines, policy ships `control.rdt.enable: false` (L1958-1996) | Detect the INTEL_SKYLAKE_X quirk (family 6, model 0x55, `cat_l3` in CPUID but no `info/L3`) and suggest `rdt=l3cat` with a revert. Detect `cat_l2` (GNR). Add MBM read-only per balloon to telemetry. | S | low |
| SST-PP/BF/TF/CP, PCT | **M** (0 mentions of isst / intel-speed-select) | Detect `/dev/isst_interface` + `intel_vsec.tpmi-sst`. Bake a pinned `intel-speed-select` into the image (kernel tools tree). Run read-only `turbo-freq info` / `base-freq info`. Feed HP CPUs into `k3d-gpu.io/favored-cpus` (reuse the G5 path) so the `latency` balloon picks them on Xeon. | M | low (read-only) |
| Uncore + ELC | **D**: `perf_uncore` reads range and ELC; the high floor is not suggested pending H29 | Keep it. When TPMI and the current freq floor reads low under load, show ELC values. Suggest only once the H29 run backs it; today H29 is INCONCLUSIVE. | XS | low |
| TPMI PLR | **M** for TPMI (only client MSR 0x64F, L3070) | Read `/sys/kernel/debug/tpmi-*/plr/domain*/status` when debugfs is readable (root) and decode coarse/fine reasons. Read-only. | S | low |
| PMT telemetry | **M** | List GUIDs and sizes; decode only with the pinned intel/pmt XML. Defer the decoder. | M | low |
| IFS | **M** | Info line: `intel_ifs_*` status, image presence. No auto-run (takes a core offline). | XS | none |
| CXL tiering knobs | **D** partial: memory-only nodes reported (L1951) | When a CXL `mem*`/region or CPU-less node exists: report `demotion_enabled`, `numa_balancing`, weighted interleave; suggest with revert | S | medium |
| Hugepages | **D**, but the only suggestion is to *release* the pool (L3214-3225) | No change until an H-test shows a 1G-weights gain | — | — |
| MRDIMM / Xeon DRAM POR | **D** partial: `perf_memory` computes peak GB/s from type-17 (client POR table) | Add Xeon 6 channel counts and MRDIMM support per SKU family to the POR check | S | none |
| TDX | **D** (L3775-3800, apply/revert printed) | Already compliant | — | — |
| TME | **D** (L3865-3874) | Compliant | — | — |
| SGX | **M** | Info only (BIOS off on crescent). Low value. | XS | none |
| Split-lock, CET | **D** | Compliant | — | — |
| UINTR | **M** (correct to stay missing) | Optional one-line "CPUID has UINTR, no kernel support". Low value. | XS | none |
| HFI on Xeon | **M** | No action | — | — |
| AET (CWF) | **M** | On kernel >= 7.0 with `info/PERF_PKG_MON`: expose per-balloon Joules (H21 input) | M | low |

---

## 5. Live state on crescent and the i9-13900T vs the matrix

### 5.1 crescent: Xeon 6741P (GNR-SP, family 6 model 0xAD stepping 1)

Platform [L-crescent]:
- 48C/96T, 1 socket, L3 288 MiB.
- Kernel 7.0.0-31 on Ubuntu 26.04.1.
- Supermicro SYS-212GB-FNR, BIOS 1.5.
- 8x64 GB DDR5-6400 in 8 of 16 slots, so all 8 channels run at 1DPC. Adding DIMMs would mean 2DPC, which is slower.
- 4x Arc Battlemage G31.

All reads were read-only.

| Matrix row (GNR column) | Present | Used | Present but unused: what is left on the table |
|---|---|---|---|
| AMX INT8/BF16/FP16 | yes (palette 1, 8 KB tiles; XFD armed) | unknown | No check that the serving pods actually engage AMX (G1) |
| AVX10.1 / AVX-512 FP16/BF16 | yes (leaf 0x24 = 0x70001) | NFD-style labels | — |
| PREFETCHI, CLDEMOTE, WAITPKG, SERIALIZE | yes | no | Compiler/runtime level only |
| DSA 2.0 x3 (8086:0b25, v0x200, 16 WQ / 4 engines each) | yes | **no: all disabled, no WQs, accel-config not installed** | Copy offload (G3); k3d-gpu plugin advertises 0 |
| IAA 2.0 x3 | yes; one kernel dedicated WQ each, bound to iaa_crypto | **no consumer: zswap enabled=N, compressor lzo** | IAA zswap tier (G2) |
| QAT 402xx x3 (8086:4944) | up: 2x `sym;asym`, 1x `dc` | **near-idle (146/0/4 requests), 0 VFs** | TLS/compression offload; k3d-gpu QAT plugin needs VFs |
| DLB 2.5 x3 (8086:2714) | yes | **no driver bound** | Out of reach on mainline |
| ENQCMD/SVA (scalable mode on, IDXD_SVM=y) | yes | no | Shared WQs never created |
| UINTR | CPUID yes | no kernel support | — (dead end) |
| RDT: L3 CAT 16-bit CBM / 15 CLOS, L2 CAT 8 CLOS, MBA, CMT/MBM, RMID 576 | yes, resctrl registered | **not mounted** | G6 |
| SST via TPMI (`isst_tpmi`, `/dev/isst_interface`) | yes | **tool absent; profile/HP cores unread** | G4 |
| Uncore TPMI 4 domains, ELC | yes, at max (2.2/2.5 GHz), ELC 0 | effectively pinned high already | — |
| HWP | min=max=3.8 GHz, EPP 0, governor performance | yes (latency-tuned) | — |
| TPMI PLR, PEM | debugfs present | no | G8 |
| PMT telemetry (14 regions, 7 GUIDs) | yes | no decoder | G8 |
| SDSi | bound; state_certificate 32 B (no AKC/CAP payload seen); meter ticking | no | G15 (ask vendor) |
| SNC | off (single node) | — | G7 |
| CXL 2.0 (10 windows, 12 reserved memoryless nodes) | root decoders only, **no memdev** | — | Needs a Type-3 device (G9) |
| Hugepages | 0 (2M and 1G); THP madvise | — | Unproven lever |
| TME / TDX / SGX | capable; **BIOS off** (TME_ACTIVATE=0x1 locked, SEAMRR 0, SGX disabled) | no | Sandbox isolation only; not a speed lever |
| IFS | intel_ifs_0/1 untested, no image | no | G14 |
| MRDIMM | **not supported** (ARK: DDR5-6400 only) | — | G16 does not apply to crescent |
| SST-BF / SST-TF (ARK) | yes: 12 HP @2.6 GHz, 36 LP @2.1 GHz; SST-PP no | no | G4 |
| TSX | kernel `TSX_MODE_OFF` | no | Correct |
| RAPL | PL1 300 W; DRAM domain disabled | package only | DRAM energy unread |
| HFI | enumerated | kernel only | — |
| FRED/LAM/LASS | not enumerated on GNR | — | Matches the matrix (CWF/SRF/DMR only) |

### 5.2 i9-13900T workstation: Raptor Lake (client, outside the Xeon matrix)

Platform [L-13900T]:
- Kernel 6.6.128 Manjaro.
- 8P+16E/32T.
- No AVX-512, AMX, RDT, DSA, IAA, QAT, DLB or ENQCMD.

Present but unused or unusable:
- **Key Locker**: enumerated, AESKLE off, no Linux 6.6 support.
- **TME**: capable, locked off by BIOS. This matches capability-map "Needs BIOS".
- **CET user shadow stack**: supported, processes not opted in (Q9 in the capability map).
- **HFI/Thread Director**: used by the kernel, not by k3d-gpu.
- **AVX-VNNI**: labelled, used by OVMS.
- **MOVDIR64B**: present, no device consumer.
- **PMT telem0/1**: from 8086:a77d, undecoded.
- **RAPL psys/uncore**: read by the joule-pack work (H21).

Contrast:
- The i9-13900T has none of the engines in table 1b.
- The isolation lever available on the i9-13900T is CPU caps / E-cluster balloons. H26 tested this and was INCONCLUSIVE (EXPERIMENTS.md).

---

## 6. Conflicts reconciled

Each row names the source that won and why.

| Conflict | Resolution | Source that won |
|---|---|---|
| MBA first gen: SKX (kernel, RDT spec) vs Cascade Lake (Intel 2nd-gen overview article) | **SKX.** The RDT Arch Spec is the architectural primary and the kernel enumerates it on SKX. | [I-RDT][K-resctrl-doc] over [I-CLXov] |
| TPMI on EMR (task premise) vs GNR/SRF | **GNR/SRF and later.** PCM ties TPMI to GNR/SRF and the LKML series names no EMR. SPR/EMR use mailbox/MSR. | [R-pcm][K-tpmi] |
| Clearwater Forest AMX (press) vs none (ARK 6990E+) | **No AMX.** ARK lists AVX2 + DL Boost only. | [I-ARK6990E] |
| AMX-TF32 on DMR: ISE rev 061 lists it; oneDNN removed the TF32 path and CPUID check | **Uncertain.** Re-read: ISE rev 061 Table 1-2 lists AMX-TF32 under Diamond Rapids. GCC `-march=diamondrapids` omits it. A rev 062 was not found by this pass; the k3d-gpu comment claiming removal is unverified. | [I-ISE][R-gcc][R-onednn-rel] |
| AMX-COMPLEX "on Xeon 6" vs GNR-D only | **GNR-D (06_AEH) only.** | [I-ISE] Table 1-2 |
| QAT 420xx vs 4xxx on Xeon 6 (unverified in records) | **GNR-SP 6741P carries 402xx 8086:4944** (driver 4xxx) | [L-crescent] |
| HFI on server (records: unverified) | **Enumerated on GNR** | [L-crescent] |
| SDSi on Xeon 6 (records: likely unused) | **Interface present and metering on GNR.** Licence activation remains unverified. | [L-crescent], [R-sdsi-arch] |
| L3 CDP on SPR | The RDT spec table omits gen4. **Verify on hardware.** | [I-RDT] |
| PPIN sysfs version (5.18 vs 6.0) | Unresolved; both marked believed | [K-common] |
| DSA PCI ID on GNR | **Reuses 0x0b25, version 0x200** | [L-crescent][K-idxd] |
| LASS on CWF (first pass: yes) | **Not listed** for CWF in ISE 061 Table 1-2 (LASS: SRF, GRR, ARL-H, DMR). Marked ? | [I-ISE] |
| QAT 420xx generation (first pass: Gen4) | **Gen5**, platform GNR-D | [R-qatlib-req] |
| MRDIMM on 6741P (first pass: MRDIMM-8000 via 6700P brief) | **No**: ARK SKU page lists DDR5-6400 only. Family brief does not imply per-SKU support | [I-ARK6741P] |

## 6b. Review-pass corrections (2026-10-20)

Spot-check of 15+ claims against primary sources. **OK** = confirmed as written; **FIXED** = changed in place.

| # | Claim (first pass) | Verdict | Evidence |
|---|---|---|---|
| 1 | AMX peak 2,048 INT8 / 1,024 BF16 ops/cycle/core vs 256 / 64 | OK | [I-AMXbrief] p.2 [provenance:P311] |
| 2 | QAT 420xx is Gen4 | **FIXED**: Gen5, GNR-D | [R-qatlib-req] |
| 3 | QAT Gen6 on DMR "believed" | **FIXED**: QATlib names DMR "Intel Xeon 7", kernel 6.16, QATlib 26.08, firmware pending | [R-qatlib-req] |
| 4 | crescent 6741P supports MRDIMM-8000 | **FIXED**: ARK lists DDR5-6400 only; G16 confidence set to 0 on crescent | [I-ARK6741P] |
| 5 | CWF has LASS | **FIXED** to ?: ISE 061 lists LASS for SRF/GRR/ARL-H/DMR, not CWF | [I-ISE] Table 1-2 |
| 6 | AMX-TF32 "likely absent" on DMR; "ISE rev 062 removed it" | **FIXED** to uncertain: ISE 061 lists it; GCC omits it; rev 062 not found | [I-ISE][R-gcc] |
| 7 | llama.cpp AMX kernels "INT8-quant only" | **FIXED**: Q4_0/Q4_1/Q8_0/Q4_K/Q5_K/Q6_K/IQ4_XS | [R-llama-amx-types] |
| 8 | DLB "~100M sched/s" | **FIXED** to unverified: not in DPDK dlb2 doc or Intel DLB pages read | [R-dpdk-dlb][I-DLBdl] |
| 9 | [R-pmt] at github.com/intel/pmt | **FIXED**: that URL is 404; repo is intel/Intel-PMT | GitHub API |
| 10 | AVX512_BF16 kernel "~5.3 (believed)" | **FIXED** to verified: flag absent in v5.2, present in v5.3 | [K-cpuf] at tags |
| 11 | AVX512_FP16 5.11, AVX-VNNI 5.12, AMX 5.16, AMX-FP16 6.2, LAM 6.4, FRED 6.9, APX 6.16, LASS 6.19, MOVRS 7.0 | OK (each flag first appears at that tag) | [K-cpuf] at tags |
| 12 | SKX/CLX/CPX L3 CAT forced off (SKZ18) | OK: `INTEL_SKYLAKE_X` quirk sets `!l3cat` (plus `!cmt,!mbm*` at stepping <=4) | [K-resctrl-core][K-cat-skx] |
| 13 | DSA ~30 GB/s per device [provenance:P310]; ~2.1x CBDMA; async beats CPU from ~256 B; 4 DSA ~90 GB/s beyond 64 KB | OK (SPR measurements) | [P-asplos] |
| 14 | INTEL-SA-01084: WQ mmap -EPERM without CAP_SYS_RAWIO | OK: `idxd_cdev_mmap()` check | [K-idxd-cdev] |
| 15 | 8480+ 1 / 6980P 4 default accelerators each | OK; plus 6741P = 3 each, 6716P-B = 1/1/1 and IAA 0 | [I-ARK8480][I-ARK6980P][I-ARK6741P][I-ARK6716PB] |
| 16 | DSA 0x11fb GNR-D, 0x1212 DMR; IAA 0x1216 DMR, 0xb02d PTL, 0xfd2d WCL; CBDMA 0x0b00 ICX | OK | [K-pciids][K-idxd] |
| 17 | AMX-COMPLEX GNR-D only | OK for Xeon 6; ISE 061 adds DMR | [I-ISE] |
| 18 | iaa_crypto defaults `verify_compress=1`, `sync_mode=sync` | OK | [K-iaa] |
| 19 | DLB K8s plugin removed 2026-09-01; intel-sdsi, DML, uintr-linux-kernel archived | OK (commit be6c943e4f; GitHub `archived=true`) | [R-dlbrm][R-sdsi-arch][R-dml][R-uintr-arch] |
| 20 | vLLM-CPU: avx512f "Recommended", avx2 "Limited features" | OK | [R-vllm-doc] |
| 21 | VMD on GNR "?" | **FIXED**: ARK 6741P/6980P list VMD | [I-ARK6741P][I-ARK6980P] |

Speculative LLM/agent claims that were stated as fact are now tagged **(EI)** in sections 2.1-2.5. The ones that changed meaning, not just got a tag:
- IAA zswap "for CPU-offloaded KV": page-locked offload buffers are not swappable, so zswap cannot compress them. Narrowed to idle agent heaps.
- IFS "bad cores silently corrupt logits": rewritten as a possibility.
- TPMI PLR "explains tokens/s dips": rewritten as "could explain".
- QAT "Go proxies cannot use it": downgraded to believed (no QAT hook in Go `crypto/tls` found).
- "v3 is the oldest Xeon worth running Q4/Q8 decode on": marked as opinion.

---

## Sources

Kernel (torvalds/linux master unless a commit is given):
- [K-cpuf] arch/x86/include/asm/cpufeatures.h — https://github.com/torvalds/linux/blob/master/arch/x86/include/asm/cpufeatures.h
- [K-flip] commit 78ce84b9e0a5 (cpuinfo appearance flip) — https://github.com/torvalds/linux/commit/78ce84b9e0a5
- [K-kvmcpuid] arch/x86/kvm/cpuid.c — https://github.com/torvalds/linux/blob/master/arch/x86/kvm/cpuid.c
- [K-xstate] Documentation/arch/x86/xstate.rst — https://docs.kernel.org/arch/x86/xstate.html
- [K-intelc] arch/x86/kernel/cpu/intel.c (zmm_exclusion_list, TME) — https://github.com/torvalds/linux/blob/master/arch/x86/kernel/cpu/intel.c
- [K-procdoc] Documentation/filesystems/proc.rst — https://github.com/torvalds/linux/blob/master/Documentation/filesystems/proc.rst
- [K-apx] commit b02dc185ee86 — https://github.com/torvalds/linux/commit/b02dc185ee86
- [K-ioat] drivers/dma/ioat/init.c — https://github.com/torvalds/linux/blob/master/drivers/dma/ioat/init.c
- [K-idxd] drivers/dma/idxd/init.c — https://raw.githubusercontent.com/torvalds/linux/master/drivers/dma/idxd/init.c
- [K-idxd-cdev] drivers/dma/idxd/cdev.c — https://raw.githubusercontent.com/torvalds/linux/master/drivers/dma/idxd/cdev.c
- [K-sva] SVA doc — https://docs.kernel.org/arch/x86/sva.html
- [K-kconfig-cf] arch/x86/Kconfig.cpufeatures — https://raw.githubusercontent.com/torvalds/linux/master/arch/x86/Kconfig.cpufeatures
- [K-iaa] IAA crypto doc — https://docs.kernel.org/driver-api/crypto/iaa/iaa-crypto.html
- [K-zswap] mm/zswap.c — https://raw.githubusercontent.com/torvalds/linux/master/mm/zswap.c
- [K-qat] drivers/crypto/intel/qat — https://github.com/torvalds/linux/tree/master/drivers/crypto/intel/qat
- [K-qat-abi] sysfs-driver-qat — https://raw.githubusercontent.com/torvalds/linux/master/Documentation/ABI/testing/sysfs-driver-qat
- [K-qat6] commit 17fd7514ae68 — https://github.com/torvalds/linux/commit/17fd7514ae68
- [K-sdsi] sysfs-driver-intel_sdsi — https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/sysfs-driver-intel_sdsi
- [K-p2p] commit 0ba76b19fd4c — https://github.com/torvalds/linux/commit/0ba76b19fd4c
- [K-vmd] drivers/pci/controller/vmd.c — https://github.com/torvalds/linux/blob/master/drivers/pci/controller/vmd.c
- [K-resctrl-core] arch/x86/kernel/cpu/resctrl/core.c — https://github.com/torvalds/linux/blob/master/arch/x86/kernel/cpu/resctrl/core.c
- [K-cat-skx] commit d56593eb5ed — https://github.com/torvalds/linux/commit/d56593eb5ed
- [K-mon] resctrl/monitor.c — https://github.com/torvalds/linux/blob/master/arch/x86/kernel/cpu/resctrl/monitor.c
- [K-resctrl-doc] resctrl doc — https://docs.kernel.org/filesystems/resctrl.html
- [K-aet] resctrl/intel_aet.c — https://github.com/torvalds/linux/blob/master/arch/x86/kernel/cpu/resctrl/intel_aet.c
- [K-snc3fix] commit 4d6dd05d07d — https://github.com/torvalds/linux/commit/4d6dd05d07d
- [K-numa-abi] sysfs-kernel-mm-numa — https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/sysfs-kernel-mm-numa
- [K-damon] DAMON usage — https://docs.kernel.org/admin-guide/mm/damon/usage.html
- [K-thp] transhuge doc — https://docs.kernel.org/admin-guide/mm/transhuge.html
- [K-cxl] commit 4cdadfd5e0a — https://github.com/torvalds/linux/commit/4cdadfd5e0a
- [K-la57] 5level-paging.rst — https://github.com/torvalds/linux/blob/master/Documentation/arch/x86/x86_64/5level-paging.rst
- [K-pkeys] protection-keys.rst — https://github.com/torvalds/linux/blob/master/Documentation/core-api/protection-keys.rst
- [K-buslock] buslock doc — https://docs.kernel.org/arch/x86/buslock.html
- [K-shstk] shstk doc — https://docs.kernel.org/arch/x86/shstk.html
- [K-sgx] SGX doc — https://docs.kernel.org/arch/x86/sgx.html
- [K-tdx] TDX doc — https://docs.kernel.org/arch/x86/tdx.html
- [K-tsm] commit 249872f53d6 — https://github.com/torvalds/linux/commit/249872f53d6
- [K-tsx] TAA doc — https://docs.kernel.org/arch/x86/tsx_async_abort.html
- [K-rapl] arch/x86/events/rapl.c — https://github.com/torvalds/linux/blob/master/arch/x86/events/rapl.c
- [K-pstate] drivers/cpufreq/intel_pstate.c — https://github.com/torvalds/linux/blob/master/drivers/cpufreq/intel_pstate.c
- [K-uncore] uncore frequency doc — https://docs.kernel.org/admin-guide/pm/intel_uncore_frequency_scaling.html
- [K-sst] Intel Speed Select doc — https://docs.kernel.org/admin-guide/pm/intel-speed-select.html
- [K-tpmi] include/linux/intel_tpmi.h — https://github.com/torvalds/linux/blob/master/include/linux/intel_tpmi.h
- [K-plr] plr_tpmi.c — https://github.com/torvalds/linux/blob/master/drivers/platform/x86/intel/plr_tpmi.c
- [K-tpmi-abi] debugfs-tpmi — https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/debugfs-tpmi
- [K-pmt] sysfs-class-intel_pmt — https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/sysfs-class-intel_pmt
- [K-hfi] intel-hfi doc — https://docs.kernel.org/arch/x86/intel-hfi.html
- [K-ifs] IFS doc — https://docs.kernel.org/arch/x86/ifs.html
- [K-ifs-core] ifs/core.c — https://raw.githubusercontent.com/torvalds/linux/master/drivers/platform/x86/intel/ifs/core.c
- [K-common] arch/x86/kernel/cpu/common.c (PPIN) — https://github.com/torvalds/linux/blob/master/arch/x86/kernel/cpu/common.c
- [K-pt] arch/x86/events/intel/pt.c — https://github.com/torvalds/linux/blob/master/arch/x86/events/intel/pt.c
- [K-sbedac] drivers/edac/sb_edac.c — https://github.com/torvalds/linux/blob/master/drivers/edac/sb_edac.c
- [K-i10nm] drivers/edac/i10nm_base.c — https://github.com/torvalds/linux/blob/master/drivers/edac/i10nm_base.c
- [K-peci] drivers/peci (in tree since 5.18, verified by tag) — https://github.com/torvalds/linux/tree/master/drivers/peci
- [K-pciids] include/linux/pci_ids.h — https://raw.githubusercontent.com/torvalds/linux/master/include/linux/pci_ids.h
- [K-qat-kconfig] drivers/crypto/intel/qat/Kconfig — https://raw.githubusercontent.com/torvalds/linux/master/drivers/crypto/intel/qat/Kconfig
- [K-ice] ice_devids.h — https://raw.githubusercontent.com/torvalds/linux/master/drivers/net/ethernet/intel/ice/ice_devids.h
- [K-ixgbe] ixgbe_type.h — https://raw.githubusercontent.com/torvalds/linux/master/drivers/net/ethernet/intel/ixgbe/ixgbe_type.h
- [K-i40e] i40e_devids.h — https://raw.githubusercontent.com/torvalds/linux/master/drivers/net/ethernet/intel/i40e/i40e_devids.h
- [K-kparams] kernel-parameters.txt (`fred=`) — https://raw.githubusercontent.com/torvalds/linux/master/Documentation/admin-guide/kernel-parameters.txt
- [K-msr] arch/x86/include/asm/msr.h (wrmsrns) — https://github.com/torvalds/linux/blob/v6.9/arch/x86/include/asm/msr.h
- [K-vmxcap] arch/x86/kvm/vmx/capabilities.h (IPIv, first at v6.0) — https://github.com/torvalds/linux/blob/v6.0/arch/x86/kvm/vmx/capabilities.h
- [K-zswapdoc] zswap doc — https://docs.kernel.org/admin-guide/mm/zswap.html
- [M-mlock] mlock(2) man page — https://man7.org/linux/man-pages/man2/mlock.2.html

Intel:
- [I-ISE] ISE rev 061 — https://cdrdv2-public.intel.com/915637/319433-061-architecture-instruction-set-extensions-programming-reference.pdf
- [I-AVX10] AVX10 paper rev 3.0 — https://cdrdv2-public.intel.com/849709/356368-003-intel-avx10-technical-paper.pdf
- [I-RDT] RDT Arch Spec rev 1.1 — https://cdrdv2-public.intel.com/789566/356688-intel-rdt-arch-spec.pdf
- [I-PCT] PCT tech article rev 1.5.1 — https://cdrdv2-public.intel.com/846906/846906_PCT_Technology_Technical_Article_Rev_1_5_1.pdf
- [I-AMXbrief] AMX brief — https://cdrdv2-public.intel.com/785250/Intel-AMXBrief-Final-3.17.pdf
- [I-DSAspec] DSA spec rev 3.0 — https://cdrdv2-public.intel.com/857060/341204-006-intel-data-streaming-accelerator-spec.pdf
- [I-X6brief] Xeon 6 product brief — https://www.intel.com/content/www/us/en/products/docs/xeon-6-product-brief.html
- [I-X6profiles] Xeon 6 performance and power profiles — https://builders.intel.com/docs/networkbuilders/intel-xeon-6-processors-performance-and-power-profiles-1746785299.pdf
- [I-HPCdeck] HPC and AI on Xeon 6 (MRDIMM, CXL, SNC) — https://cdrdv2.intel.com/v1/dl/getContent/852810?fileName=HPC-and-AI-Workloads-with-Intel-Xeon-6-Processors.pdf
- [I-MRDIMMnews] — https://www.intel.com/content/www/us/en/newsroom/news/new-ultrafast-memory-boosts-intel-xeon-chips.html
- [I-SNC] Xeon 6 SNC/clustering guide — https://cdrdv2-public.intel.com/845720/845720_1p0.pdf
- [I-FMM] Flat Memory Mode — https://www.intel.com/content/www/us/en/support/articles/000102525/processors/intel-xeon-processors.html
- [I-MAX] Xeon Max tuning guide — https://cdrdv2-public.intel.com/787743/354227-intel-xeon-cpu-max-series-configuration-and-tuning-guide-rev3.pdf
- [I-EMR] 5th Gen Xeon brief — https://www.intel.com/content/dam/www/central-libraries/us/en/documents/2023-11/5th-gen-xeon-processors-product-brief.pdf
- [I-OD] On Demand fact sheet — https://download.intel.com/newsroom/2023/data-center-hpc/4th-Gen-Xeon-On-Demand-Fact-Sheet.pdf
- [I-ARK6980P] — https://www.intel.com/content/www/us/en/products/sku/240777/intel-xeon-6980p-processor-504m-cache-2-00-ghz/specifications.html
- [I-ARK8480] — https://www.intel.com/content/www/us/en/products/sku/231746/intel-xeon-platinum-8480-processor-105m-cache-2-00-ghz/specifications.html
- [I-ARK6990E] — https://www.intel.de/content/www/de/de/products/sku/246074/intel-xeon-6990e-processor-576m-cache-2-20-ghz/specifications.html
- [I-QATnginx] QAT NGINX whitepaper — https://builders.intel.com/docs/networkbuilders/intel-quickassist-technology-nginx-performance-white-paper-1675662546.pdf
- [I-SSTBF] SST-BF with Kubernetes app note — https://builders.intel.com/docs/networkbuilders/intel-speed-select-technology-base-frequency-with-kubernetes-application-note.pdf
- [I-SKXov] Xeon Scalable tech overview — https://www.intel.com/content/www/us/en/developer/articles/technical/xeon-processor-scalable-family-technical-overview.html
- [I-CLXov] 2nd Gen overview — https://www.intel.com/content/www/us/en/developer/articles/news/second-generation-intel-xeon-processor-scalable-family-technical-overview.html
- [I-ICXov] 3rd Gen overview — https://www.intel.com/content/www/us/en/developer/articles/technical/third-generation-xeon-scalable-family-overview.html
- [I-IPPicl] IPP crypto on Ice Lake — https://www.intel.com/content/www/us/en/developer/articles/technical/ipp-crypto-acceleration-with-processor-code-named-ice-lake.html
- [I-DLB] DL Boost guide — https://www.intel.com/content/www/us/en/developer/articles/guide/deep-learning-with-avx512-and-dl-boost.html
- [I-BF16] BF16 article — https://www.intel.com/content/www/us/en/developer/articles/technical/intel-deep-learning-boost-new-instruction-bfloat16.html
- [I-OPTMAN] Optimization manual 048 — https://cdrdv2-public.intel.com/814201/355308-Software-Optimization-Manual-048-Changes-Doc.pdf
- [I-DDIO] DDIO — https://www.intel.com/content/www/us/en/io/data-direct-i-o-technology.html
- [I-E5v2DS] E5 v2 datasheet vol 1 — https://www.intel.com/content/dam/www/public/us/en/documents/datasheets/xeon-e5-v2-datasheet-vol-1.pdf
- [I-E5v3] E5 v3 brief — https://cdn.blueally.com/cpguard/datasheets/xeon-e5-brief.pdf
- [I-E5brief] E5 v2 brief (RAID claim) — https://www.broadberry.com.au/system_files/datasheets/xeon-e5-brief.pdf
- [I-E5v3SU] E5 v3 spec update — https://cdrdv2-public.intel.com/330785/330785_Intel%C2%AE%20Xeon%C2%AE%20Processor%20E5-1600%20and%20E5-2600%20v3%20Product%20Families%20Specification%20Update_Rev014US.pdf
- [I-E5v4SU] E5 v4 spec update — https://www.intel.com/content/dam/www/public/us/en/documents/specification-updates/xeon-e5-v4-spec-update.pdf
- [I-PMEM] Optane start-up guide — https://cdrdv2-public.intel.com/841964/Intel_Optane_Persistent_Memory_Start_Up_Guide.pdf
- [I-PMEMend] Optane wind-down — https://www.intel.com/content/www/us/en/support/articles/000093792/technologies/memory-and-storage.html
- [I-SGXicx] SGX on Xeon — https://www.intel.com/content/www/us/en/support/articles/000059614/processors/intel-xeon-processors.html
- [I-TMEMK] TME-MK article — https://www.intel.com/content/www/us/en/developer/articles/news/runtime-encryption-of-memory-with-intel-tme-mk.html
- [I-TDXavail] TDX availability — https://www.intel.com/content/www/us/en/support/articles/000101754/processors.html
- [I-TDXC] TDX Connect announcement — https://community.intel.com/t5/Blogs/Tech-Innovation/Data-Center/Announcing-Intel-TDX-Connect-Support-on-Intel-Xeon-6/post/1668423
- [SM-SPR] Supermicro 4th Gen SKU table ("Courtesy of Intel") — https://www.supermicro.com/en/support/resources/cpu-4th-gen-intel-xeon-scalable
- [SM-X6SoC] Supermicro Xeon 6 SoC SKU table (secondary) — https://www.supermicro.com/en/support/resources/cpu-intel-xeon-6-soc
- [I-ARK6741P] — https://www.intel.com/content/www/us/en/products/sku/241828/intel-xeon-6741p-processor-288m-cache-2-50-ghz/specifications.html
- [I-ARK6716PB] Xeon 6 SoC 6716P-B — https://www.intel.com/content/www/us/en/products/sku/242908/intel-xeon-6716pb-processor-160m-cache-2-50-ghz/specifications.html
- [I-ARKD2796NT] Xeon D-2796NT — https://www.intel.com/content/www/us/en/products/sku/226249/intel-xeon-d2796nt-processor-30m-cache-up-to-3-10-ghz/specifications.html
- [I-ARKlist] ARK Xeon series list (CWF 69xxE+ Q2'26) — https://www.intel.com/content/www/us/en/ark/products/series/595/intel-xeon-processors.html
- [I-ICLD] Ice Lake-D platform page — https://www.intel.com/content/www/us/en/products/platforms/details/ice-lake-d.html
- [I-X6SoCvRAN] Xeon 6 SoC vRAN brief — https://cdrdv2-public.intel.com/846187/Intel-Xeon-6-SoC-P-cores-vRAN-Brief.pdf
- [I-X6edge] Xeon 6 for networking and edge — https://www.intel.com/content/www/us/en/products/details/processors/xeon/edge.html
- [I-DLBdl] Intel DLB driver download (8.13.0) — https://www.intel.com/content/www/us/en/download/686372/intel-dynamic-load-balancer.html
- [I-VROCgraid] VROC support transitioned to Graid (article 000102186) — https://www.intel.com/content/www/us/en/support/articles/000102186.html
- [I-VROCkey] VROC "Intel SSD Only" key EOL (Intel article 000097294, mirrored) — https://docs.graidtech.com/vroc/Product_Support/000097294_Announcement_End-of-Life_EOL_for_the_Intel_Virtual_RAID_on_CPU_Intel_VROC_Hardware_Key_Intel_SSD_Only/

Repos and tools:
- [R-onednn] oneDNN dispatcher control — https://github.com/uxlfoundation/oneDNN/blob/main/doc/performance/dispatcher_control.md
- [R-onednn-isa] cpu_isa_traits.hpp — https://github.com/uxlfoundation/oneDNN/blob/main/src/cpu/x64/cpu_isa_traits.hpp
- [R-onednn-rel] oneDNN releases — https://github.com/uxlfoundation/oneDNN/releases
- [R-gcc] GCC x86 options — https://gcc.gnu.org/onlinedocs/gcc/x86-Options.html
- [R-gcc15] GCC 15 article — https://www.intel.com/content/www/us/en/developer/articles/technical/next-gen-performance-gcc-15.html
- [R-gcc15ml] GCC AVX10 patch mail — https://sourceware.org/pipermail/gcc-patches/2025-March/678195.html
- [R-llama-amx] ggml AMX — https://github.com/ggml-org/llama.cpp/blob/master/ggml/src/ggml-cpu/amx/amx.cpp
- [R-llama-cmake] ggml CMakeLists — https://github.com/ggml-org/llama.cpp/blob/master/ggml/src/CMakeLists.txt
- [R-vllm-cmake] vLLM cpu_extension.cmake — https://github.com/vllm-project/vllm/blob/main/cmake/cpu_extension.cmake
- [R-vllm-doc] vLLM CPU install — https://github.com/vllm-project/vllm/blob/main/docs/getting_started/installation/cpu.x86.inc.md
- [R-dto] intel/DTO — https://github.com/intel/DTO
- [R-dml] intel/DML (archived) — https://github.com/intel/DML
- [R-qatlib] intel/qatlib — https://github.com/intel/qatlib
- [R-qatlib-req] QATlib system requirements (platform -> device -> min kernel/QATlib) — https://intel.github.io/quickassist/qatlib/requirements.html
- [R-dpdk-vrb1] DPDK VRB1 bbdev — https://doc.dpdk.org/guides/bbdevs/vrb1.html
- [R-dpdk-vrb2] DPDK VRB2 bbdev — https://doc.dpdk.org/guides/bbdevs/vrb2.html
- [R-llama-amx-types] ggml AMX `qtype_has_amx_kernels` — https://github.com/ggml-org/llama.cpp/blob/master/ggml/src/ggml-cpu/amx/common.h
- [R-dpdk-dlb] DPDK dlb2 — https://doc.dpdk.org/guides/eventdevs/dlb2.html
- [R-dlbrm] device-plugins DLB removal history — https://github.com/intel/intel-device-plugins-for-kubernetes/commits/main/cmd/dlb_plugin
- [R-sdsi-arch] intel/intel-sdsi (archived) — https://github.com/intel/intel-sdsi
- [R-pqos] intel-cmt-cat ChangeLog — https://github.com/intel/intel-cmt-cat/blob/master/ChangeLog
- [R-pcm] Intel PCM 202502 release — https://github.com/intel/pcm/releases/tag/202502
- [R-pmt] intel/Intel-PMT — https://github.com/intel/Intel-PMT
- [R-oz-pct] optimization-zone PCT — https://intel.github.io/optimization-zone/hardware/priority_core_turbo/
- [R-ace] ACE v1 spec — https://x86ecosystem.org/wp-content/uploads/2026/06/ACE_v1_Specification_public_1_15.pdf
- [R-uintr-arch] intel/uintr-linux-kernel (archived) — https://github.com/intel/uintr-linux-kernel

Papers and patches:
- [P-asplos] Kuper et al., DSA characterization (ASPLOS'24) — https://arxiv.org/html/2305.02480v5
- [P-osdi] Zhong et al., Memstrata / Flat Memory Mode (OSDI'24) — https://www.usenix.org/system/files/osdi24-zhong-yuhong.pdf
- [P-zswapbatch] zswap IAA batching v12 / v4 — https://lwn.net/Articles/1039687/ , https://lwn.net/Articles/999608/
- [P-migrate] batch migrate offload RFC — https://lwn.net/Articles/1039253/
- [P-uintr] UINTR RFC — https://lkml.iu.edu/hypermail/linux/kernel/2109.1/07335.html
- [P-ramba] region-aware RDT RFC — https://lists.openwall.net/linux-kernel/2026/08/02/552
- [P-phx] Phoronix SNC3/HEX (secondary) — https://www.phoronix.com/review/intel-xeon-snc3-hex-benchmarks
- [P-keylocker] Key Locker v9 series (unmerged) — https://lwn.net/Articles/967144/

Live:
- [L-crescent] read-only inventory, 2026-10-20: CPUID/MSR pread, sysfs, lspci, dmesg
- [L-13900T] i9-13900T unprivileged inventory, 2026-10-20

k3d-gpu: `scripts/k3d-gpu` at `a32a9701` (line refs in section 4), `docs/intel-capability-map.md`, `docs/intel.md`, `EXPERIMENTS.md` (H21, H26, H29 INCONCLUSIVE; H28 SUPPORTED; H3, H5, H27 REFUTED).
