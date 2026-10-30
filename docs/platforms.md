# Platforms

One row per Intel family that has a sim fixture under `tests/fixtures/`. Each row is
read from that fixture's goldens, so it states what `k3d-gpu` does on the host with no
flags set. `tests/platforms_doc_test.sh` checks the rows, the NRI balloons cells and
the plan decisions against the goldens:

- **Detected as**: the `k3d-gpu detect` lines (`detect.golden`).
- **Auto config**: the device plugins and CPU policy `k3d-gpu up` installs
  (`up.golden`). NRI balloons run on hybrid P/E and multi-NUMA hosts only; elsewhere
  `up` passes `--disable=nri-balloons` to k3s. `K3D_GPU_*` flags only override these.
- **Predicted behaviour**: the plan's targets and decision (`plan.json`). Nothing
  here is a measurement. Every fixture plans `unmeasured` until a cell measured on a real box turns green in
  `console/matrix.v1.json`, or `fix` where a target is refused (`raptorlake-stale-guc`,
  `gaudi3-no-driver`). Measured numbers live only in [EXPERIMENTS.md](../EXPERIMENTS.md).
- **Source**: the fixture's `SOURCES` file, which names the capture or datasheet
  every sysfs value comes from.

| Family | Fixture | Detected as | Auto config | Predicted behaviour | Source |
|---|---|---|---|---|---|
| Alder Lake-N (N100, E-cores only) | `alderlake-n` | `gpu.intel.com/i915` | Intel GPU plugin, shared iGPU (`-shared-dev-num=8`); no NRI balloons, as the host is neither hybrid nor multi-NUMA | plan targets: cpu, gpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/alderlake-n/SOURCES) |
| Raptor Lake-S + NVIDIA (dev host) | `raptorlake-nvidia` | `nvidia.com/gpu`, `gpu.intel.com/i915`, hybrid | NVIDIA plugin, Intel GPU plugin (shared iGPU), NRI balloons (hybrid P/E) | plan targets: cpu, iGPU, NVIDIA usable; both catalog cells grey until a `k3d-gpu report` run is recorded; `unmeasured` | [SOURCES](../tests/fixtures/raptorlake-nvidia/SOURCES) |
| Raptor Lake-S, stale GuC | `raptorlake-stale-guc` | as above | as above | plan refuses the iGPU until linux-firmware ships the GuC i915 wants; plan and doctor print the fix; `fix` | [SOURCES](../tests/fixtures/raptorlake-stale-guc/SOURCES) |
| Meteor Lake-H | `meteorlake` | hybrid (fixture covers CPU topology only; iGPU and NPU not in fixture) | NRI balloons (hybrid P/E) | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/meteorlake/SOURCES) |
| Arrow Lake-H | `arrowlake-h` | `gpu.intel.com/i915`, `npu.intel.com/accel`, hybrid | Intel GPU plugin (shared iGPU), Intel NPU plugin, NRI balloons (hybrid P/E) | plan targets: cpu, gpu, npu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/arrowlake-h/SOURCES) |
| Arrow Lake-S | `arrowlake-s` | `gpu.intel.com/i915`, `npu.intel.com/accel`, hybrid | Intel GPU plugin (shared iGPU), Intel NPU plugin, NRI balloons (hybrid P/E) | plan targets: cpu, gpu, npu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/arrowlake-s/SOURCES) |
| Panther Lake | `pantherlake` | `gpu.intel.com/xe`, `npu.intel.com/accel`, `iaa.intel.com/*`, hybrid | Intel GPU plugin (shared iGPU), NPU plugin, IAA plugin, NRI balloons (hybrid P/E) | plan targets: cpu, gpu (xe), npu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/pantherlake/SOURCES) |
| Arc B580 (Battlemage) | `battlemage-b580` | `gpu.intel.com/xe` | Intel GPU plugin (dGPU, not shared); no NRI balloons | plan targets: cpu, gpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/battlemage-b580/SOURCES) |
| Xeon Sapphire Rapids + accelerators | `xeon-spr-accel` | DSA, IAA, QAT, AMX, NUMA | DSA, IAA and QAT plugins, AMX and NUMA labels, NRI balloons (NUMA replica) | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-spr-accel/SOURCES) |
| Xeon CPU Max (HBM, SNC4) | `xeon-max-hbm` | AMX, NUMA (HBM nodes) | AMX and NUMA labels, NRI balloons (NUMA replica) | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-max-hbm/SOURCES) |
| Xeon 6 P-core (Granite Rapids, SNC3) | `xeon-gnr` | DSA, IAA, AMX, NUMA | DSA and IAA plugins, AMX and NUMA labels, NRI balloons (NUMA replica) | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-gnr/SOURCES) |
| Xeon 6 P-core 6741P (Granite Rapids-SP, 1 socket, SNC off; crescent's CPU) | `xeon-6741p` | AMX (DSA/IAA/QAT present without work queues or VFs; DLB, Speed Select, On Demand, In-Field Scan reported) | AMX labels; no NRI balloons; doctor prints the work-queue/VF, SNC2 and Speed Select steps with reverts | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-6741p/SOURCES) |
| Xeon 6 SoC (Granite Rapids-D) | `xeon-gnrd` | DSA, AMX | DSA plugin; no NRI balloons | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-gnrd/SOURCES) |
| Xeon 6 E-core (Sierra Forest) | `xeon-srf` | DSA, IAA (no AMX) | DSA and IAA plugins; no NRI balloons | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-srf/SOURCES) |
| Xeon + QAT 420xx | `qat-420xx` | QAT, AMX | QAT plugin; no NRI balloons | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/qat-420xx/SOURCES) |
| Gaudi 3 (HLS) | `gaudi3-hls` | `habana.ai/gaudi`, AMX | Habana device plugin; no NRI balloons | plan targets: cpu, hpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/gaudi3-hls/SOURCES) |
| Gaudi 3, no driver | `gaudi3-no-driver` | AMX; Gaudi skipped with a warning | no Gaudi plugin; no NRI balloons | plan refuses the hpu until `habanalabs` loads and prints the fix (install the Gaudi driver, then `modprobe habanalabs`); doctor only warns and skips Gaudi; `fix` | [SOURCES](../tests/fixtures/gaudi3-no-driver/SOURCES) |
| Arrow Lake-U (Core Ultra 200U) | `arrowlake-u` | `gpu.intel.com/i915`, hybrid | Intel GPU plugin (shared iGPU), NRI balloons (hybrid P/E) | plan targets: cpu, gpu (i915, no XMX) usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/arrowlake-u/SOURCES) |
| Lunar Lake (Core Ultra 200V) | `lunarlake` | `gpu.intel.com/xe`, `npu.intel.com/accel`, hybrid | Intel GPU plugin (shared iGPU), NPU plugin, NRI balloons (hybrid P/E) | plan targets: cpu, gpu (xe), npu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/lunarlake/SOURCES) |
| Xeon CPU Max (HBM cache mode, SNC4) | `xeon-max-hbm-cache` | AMX, NUMA | AMX and NUMA labels, NRI balloons (NUMA replica) | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-max-hbm-cache/SOURCES) |
| Xeon CPU Max (HBM as devdax, SNC4) | `xeon-max-hbm-devdax` | AMX, NUMA | AMX and NUMA labels, NRI balloons (NUMA replica) | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-max-hbm-devdax/SOURCES) |
| Arc Pro B60 (Battlemage E211, kernel 6.14 backport) | `battlemage-pro-b60` | `gpu.intel.com/xe` | Intel GPU plugin (dGPU, not shared); no NRI balloons | plan targets: cpu, gpu usable; doctor reports the 6.14 bind as a stable backport, not force_probe; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/battlemage-pro-b60/SOURCES) |
| Arc Pro B70 (Battlemage E223), kernel 6.16, unbound | `battlemage-pro-b70-k616` | nothing (no driver bound) | none: `up` exits 1 with no supported accelerator | plan targets: cpu usable; doctor asks for kernel 6.17+, as force_probe cannot add an id missing from xe's table; `unmeasured` | [SOURCES](../tests/fixtures/battlemage-pro-b70-k616/SOURCES) |
| Arc A770 (Alchemist DG2-G10) on i915 | `dg2-a770-i915` | `gpu.intel.com/i915` | Intel GPU plugin (dGPU, not shared); no NRI balloons | plan targets: cpu, gpu usable; doctor never suggests xe for it; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/dg2-a770-i915/SOURCES) |
| Data Center GPU Flex 140 (ATS-M), unbound | `flex140-unbound` | nothing (no driver bound) | none: `up` exits 1 with no supported accelerator | plan targets: cpu usable; doctor offers intel-i915-dkms LTS or the `xe-force-probe` experimental feature; `unmeasured` | [SOURCES](../tests/fixtures/flex140-unbound/SOURCES) |
| Data Center GPU Max 1550 (Ponte Vecchio), unbound | `pvc-max1550-unbound` | nothing (no driver bound) | none: `up` exits 1 with no supported accelerator | plan targets: cpu usable; doctor names intel-i915-dkms LTS only, no force_probe; `unmeasured` | [SOURCES](../tests/fixtures/pvc-max1550-unbound/SOURCES) |
| Lunar Lake NPU, kernel 6.8 (NPU only) | `lunarlake-k6.8` | `npu.intel.com/accel`, hybrid | NPU plugin, NRI balloons (hybrid P/E) | plan targets: cpu, npu usable; doctor dates the `vpu_40xx_v0.0.bin` that kernel 6.8 loads, not `_v1`; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/lunarlake-k6.8/SOURCES) |
| Panther Lake NPU, kernel 6.14 + intel-fw-npu (NPU only) | `pantherlake-k6.14` | `npu.intel.com/accel`, hybrid | NPU plugin, NRI balloons (hybrid P/E) | plan targets: cpu, npu usable; doctor suggests (never applies) a `vpu_50xx_v0.0.bin` link to intel-fw-npu's `_v1`, with its revert; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/pantherlake-k6.14/SOURCES) |
| Wildcat Lake NPU (NPU only) | `wildcatlake` | `npu.intel.com/accel`, hybrid | NPU plugin, NRI balloons (hybrid P/E) | plan targets: cpu, npu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/wildcatlake/SOURCES) |
| Nova Lake NPU, no firmware, unbound (NPU only) | `novalake` | hybrid; the unbound NPU is not detected | NRI balloons (hybrid P/E); no NPU plugin | plan targets: cpu usable; doctor says no 60xx NPU firmware is published; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/novalake/SOURCES) |
| NPU id the pinned plugin does not serve (Panther Lake, id 0x1234) | `npu-unserved` | `gpu.intel.com/xe`, `iaa.intel.com/*`, hybrid; the NPU is skipped with a warning | Intel GPU plugin (shared iGPU), IAA plugin, NRI balloons (hybrid P/E); no NPU plugin | plan targets: cpu, gpu (xe), npu usable; doctor warns that intel-npu-plugin 0.37.1 does not list the id; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/npu-unserved/SOURCES) |
| Xeon 6+ P-core (Diamond Rapids, SNC3, simulated) | `xeon-dmr` | DSA, IAA, AMX, NUMA | DSA and IAA plugins, AMX and NUMA labels, NRI balloons (NUMA replica) | plan targets: cpu usable; doctor names the family-19 CPU and its predicted ISA; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-dmr/SOURCES) |

## Client: Core Ultra

Detection keys: the iGPU and NPU by PCI id under `/sys/bus/pci/devices`; the
CPU split by the hybrid PMU cpulists (`/sys/devices/cpu_core`, `cpu_atom`,
`cpu_lowpower`) and each CPU's `cache/index*` and `topology/core_cpus_list`. An
atom CPU with no L3 is an LP E-core. The CPU family and model in
`/proc/cpuinfo` only name a platform; they never choose a device.

| Family | Detected as | Auto config chosen | Predicted behaviour | Source |
|---|---|---|---|---|
| Meteor Lake (Core Ultra 100H/U, CPU model 170/172) | iGPU 0x7d40/0x7d45/0x7d55/0x7d60/0x7dd5 on i915; NPU 0x7d1d (37xx); LP E-cores = `cpu_atom` CPUs without an L3 | `gpu.intel.com/i915`, `gpu.intel.com/xmx=false`, `npu.intel.com/accel`, `npu.gen=37xx`; balloons with a separate `lowpower` set | No XMX, so XMX-gated pods stay off the iGPU, predicted by Mesa `MTL_CONFIG` `has_systolic = false` | [intel_device_info.c](https://gitlab.freedesktop.org/mesa/mesa/-/blob/main/src/intel/dev/intel_device_info.c), [core.c](https://github.com/torvalds/linux/blob/master/arch/x86/events/intel/core.c), fixture `meteorlake` |
| Arrow Lake-S / HX (Core Ultra 200S/HX, model 198) | iGPU 0x7d67/0xb640 on i915; NPU 0xad1d (37xx); no LP E-cores (`hybrid_big_small`) | `gpu.intel.com/i915`, `xmx=false`, `npu.gen=37xx`; P/E balloons, no `lowpower` set | Agent work stays on the P-cores under the XMX gate, predicted by Mesa (0x7d67 uses the arl_u config, no systolic) | [iris_pci_ids.h](https://gitlab.freedesktop.org/mesa/mesa/-/blob/main/include/pci_ids/iris_pci_ids.h), [pciids.h](https://github.com/torvalds/linux/blob/master/include/drm/intel/pciids.h), fixture `arrowlake-s` |
| Arrow Lake-H (Core Ultra 200H, model 197) | iGPU 0x7d51/0x7dd1 on i915; NPU 0x7d1d named Arrow Lake-H from the CPU model; LP E-cores in `cpu_lowpower` | `gpu.intel.com/i915`, `xmx=true`, `npu.gen=37xx`; `lowpower` set from `cpu_lowpower` | iGPU eligible for XMX-gated pods, predicted by Mesa `intel_device_info_arl_h` `has_systolic = true` | [intel_device_info.c](https://gitlab.freedesktop.org/mesa/mesa/-/blob/main/src/intel/dev/intel_device_info.c), [core.c](https://github.com/torvalds/linux/blob/master/arch/x86/events/intel/core.c), fixture `arrowlake-h` |
| Arrow Lake-U (Core Ultra 200U, model 181) | iGPU 0x7d41 on i915; P-cores with SMT; LP E-cores = `cpu_atom` CPUs without an L3 | `gpu.intel.com/i915`, `xmx=false`; `pcore-physical` = one thread per P-core; `lowpower` set | P-core balloons pin whole sibling pairs, predicted by ARK 255U (12 cores, 14 threads); no XMX, predicted by Mesa arl_u and compute-runtime `MTL_U` | [ARK 255U](https://www.intel.com/content/www/us/en/products/sku/241860/intel-core-ultra-7-processor-255u-12m-cache-up-to-5-20-ghz/specifications.html), [iris_pci_ids.h](https://gitlab.freedesktop.org/mesa/mesa/-/blob/main/include/pci_ids/iris_pci_ids.h), fixture `arrowlake-u` |
| Lunar Lake (Core Ultra 200V, model 189) | iGPU 0x6420/0x64a0/0x64b0 on xe only (6.12+); NPU 0x643e (40xx); all 4 `cpu_atom` CPUs are LP E-cores | `gpu.intel.com/xe`, `xmx=true`, `npu.gen=40xx`; `lowpower` = all E-cores, no `efficient` E-cores; doctor notes any topology other than 4 P + 4 LP E | Memory is on-package and shared by CPU, iGPU and NPU, so `MemTotal` is the whole budget, predicted by the ARK and Wikipedia SKU tables (not yet acted on: no code budgets by it); the LP E-core split depends on the E-cores showing no L3 in sysfs, which no capture has confirmed yet | [xe_pci.c](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/xe/xe_pci.c), [ivpu_drv.h](https://github.com/torvalds/linux/blob/master/drivers/accel/ivpu/ivpu_drv.h), [core.c](https://github.com/torvalds/linux/blob/master/arch/x86/events/intel/core.c), [Lunar Lake](https://en.wikipedia.org/wiki/Lunar_Lake), fixture `lunarlake` |
| Panther Lake (Core Ultra Series 3, model 204) | iGPU 0xb080-0xb087/0xb08f/0xb090/0xb0a0/0xb0b0 on xe (6.17+); NPU 0xb03e (50xx, 6.13+); LP E-cores = `cpu_atom` CPUs without an L3 | `gpu.intel.com/xe`, `xmx=true`, `npu.gen=50xx`; doctor asks for kernel 6.17 below it, with no `force_probe` advice (no earlier `xe_uc_fw.c` has a PANTHERLAKE GuC entry) | XMX on every PTL id, the 4-Xe parts too, predicted by Mesa `XE3_CONFIG` | [xe_pci.c v6.17](https://github.com/torvalds/linux/blob/v6.17/drivers/gpu/drm/xe/xe_pci.c), [xe_uc_fw.c v6.17](https://github.com/torvalds/linux/blob/v6.17/drivers/gpu/drm/xe/xe_uc_fw.c), fixture `pantherlake` |
| Wildcat Lake (model 213) | iGPU 0xfd80/0xfd81 on xe (6.17+, `ptl_desc`); NPU 0xfd3e (50xx, 6.17+) | `gpu.intel.com/xe`, `xmx=true`, `npu.gen=50xx`; doctor asks for kernel 6.17 below it, with no `force_probe` advice (no earlier `pciids.h` lists either id); the iGPU is covered by detect_test only, the NPU by fixture `wildcatlake` | Same xe path as Panther Lake, predicted by v6.17 `INTEL_PTL_IDS` listing both ids | [pciids.h v6.17](https://github.com/torvalds/linux/blob/v6.17/include/drm/intel/pciids.h) |

Host, kernel and BIOS changes are never applied. `k3d-gpu doctor` prints them as
suggestions with the exact command and its revert.

## Xeon (P-core server)

| Family (cpu family/model) | Detected as | Auto config chosen | Predicted behaviour | Source |
|---|---|---|---|---|
| Sapphire Rapids (6/143, 0x8F) | `amx` from `amx_tile amx_bf16 amx_int8` in /proc/cpuinfo; `numa` + `k3d-gpu.io/snc` when SNC is on | AMX labels; NRI balloons `replica` type when more than one NUMA node | bf16 on AMX predicted by the OpenVINO CPU device doc (default execution mode picks bf16 on AMX parts) | [intel-family.h](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/arch/x86/include/asm/intel-family.h), [cpufeatures.h](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/arch/x86/include/asm/cpufeatures.h), [OpenVINO CPU device](https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/cpu-device.html) |
| Xeon CPU Max, HBM flat (6/143 + memory-only nodes) | as Sapphire Rapids; doctor lists the CPU-less nodes | replica pods keep memory on their CPU node; HBM is opt-in via a balloon with `memoryTypes: [HBM]` | none beyond Sapphire Rapids | [sysfs-devices-node](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/Documentation/ABI/stable/sysfs-devices-node), [Intel Max tuning guide 354227](https://cdrdv2-public.intel.com/787743/354227-intel-xeon-cpu-max-series-configuration-and-tuning-guide-rev3.pdf) |
| Xeon CPU Max, HBM flat not onlined (6/143 + `dax*` on `device_dax`) | doctor `info`: HBM left soft-reserved | nothing changes; only when the dax device's parent is an `hmem` platform device and the model name says CPU Max (PMem and CXL dax stay untouched), doctor suggests `sudo daxctl reconfigure-device --mode=system-ram <dev>` (revert `--mode=devdax --force`) or `efi=nosoftreserve` | HBM unused by pods until onlined, predicted by Intel Max tuning guide section 5.2 | [daxctl-reconfigure-device](https://github.com/pmem/ndctl/blob/main/Documentation/daxctl/daxctl-reconfigure-device.txt), [drivers/dax/Makefile](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/drivers/dax/Makefile), [sysfs-bus-dax](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/Documentation/ABI/testing/sysfs-bus-dax) |
| Xeon CPU Max, HBM cache (6/143 + `memory_side_cache`) | doctor `info`: memory-side cache size and indexing per node | no pod change; doctor suggests `page_alloc.shuffle=y` with grubby / update-grub apply and revert commands, or prints ok when `/sys/module/page_alloc/parameters/shuffle` reads `Y` (root-only file; needs `CONFIG_SHUFFLE_PAGE_ALLOCATOR=y`) | lower conflict-miss variance with shuffle, predicted by Intel Max tuning guide | [numaperf](https://www.kernel.org/doc/html/latest/admin-guide/mm/numaperf.html), [kernel-parameters.txt](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/Documentation/admin-guide/kernel-parameters.txt), [mm/shuffle.c](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/mm/shuffle.c), [Intel Max tuning guide 354227](https://cdrdv2-public.intel.com/787743/354227-intel-xeon-cpu-max-series-configuration-and-tuning-guide-rev3.pdf) |
| Granite Rapids 6900P/6700P (6/173, 0xAD) | as Sapphire Rapids, plus `AMXFP16` from the model on bare metal | AMX labels incl. `AMXFP16`; balloons per SNC3 node | no engine change from AMX-FP16: vLLM CPU builds only amx-bf16/amx-tile kernels, predicted by its cpu_extension.cmake | [X86.td](https://github.com/llvm/llvm-project/blob/main/llvm/lib/Target/X86/X86.td), [cpu_extension.cmake](https://github.com/vllm-project/vllm/blob/main/cmake/cpu_extension.cmake) |
| Granite Rapids-D (6/174, 0xAE) | as Granite Rapids, plus `AMXCOMPLEX` | AMX labels incl. `AMXFP16`, `AMXCOMPLEX` | as Granite Rapids | [X86.td](https://github.com/llvm/llvm-project/blob/main/llvm/lib/Target/X86/X86.td) |
| Diamond Rapids (19/1, 0x01) | as Granite Rapids-D | AMX labels incl. `AMXFP16`, `AMXCOMPLEX` (X86.td DMR includes the GNRD features) | as Granite Rapids | [intel-family.h](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/arch/x86/include/asm/intel-family.h), [X86.td](https://github.com/llvm/llvm-project/blob/main/llvm/lib/Target/X86/X86.td) |

Notes:

- The kernel never prints `amx_fp16` (no quoted name in cpufeatures.h) and has no
  `amx_complex` flag, so k3d-gpu adds those two labels from the CPU model, and
  only when `amx_tile` is present and the `hypervisor` flag is not (a VM may mask
  CPUID bits). Real NFD reads CPUID and agrees on bare metal.
- Host, kernel and BIOS changes are suggestions in `doctor`, never applied.

## Discrete Intel GPUs

How `k3d-gpu` detects each discrete Intel GPU family and what it does with no
flags. Detection reads the PCI vendor and device id from sysfs and the driver the
kernel bound. The device plugin resource is always `gpu.intel.com/<bound driver>`,
the name the plugin itself derives (`device_props.go` in
[intel-device-plugins v0.37.1](https://github.com/intel/intel-device-plugins-for-kubernetes/blob/v0.37.1/cmd/gpu_plugin/device_props.go)).
Kernel, boot option and driver package changes are never applied: `doctor` and
`host-setup` print them.

Nothing in this table was measured on the hardware. The "predicted behaviour"
column is predicted by the named kernel source or vendor doc. Each family has a
sim fixture under `tests/fixtures/` whose transcripts are the goldens.

| Family (ids) | Detected as | Auto config chosen | Predicted behaviour | Source |
|---|---|---|---|---|
| Arc B580/B570, Pro B50 (E20B E20C E212, plus E202 E20D) | `Battlemage`, kernel 6.12 | `gpu.intel.com/xe`, exclusive (shared-dev-num 1), `gpu.intel.com/xmx=true` | Predicted by xe_pci.c: binds xe without force_probe from 6.12. Fixture `battlemage-b580` | [pciids.h](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/include/drm/intel/pciids.h), [xe_pciids.h v6.12](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/include/drm/intel/xe_pciids.h?h=v6.12) |
| Arc Pro B60 (E211, plus E210 E216) | `Battlemage`, kernel 6.15 | as above | Predicted by pciids.h: the id joins xe's table in 6.15 (6.14.y has a backport). Bound on an older kernel, doctor reports a backport, not force_probe. Fixture `battlemage-pro-b60` (6.14) | [pciids.h 6.14.y](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/include/drm/intel/pciids.h?h=linux-6.14.y) |
| Arc Pro B65/B70 (E222 E223, plus E209 E220 E221) | `Battlemage`, kernel 6.17 | as above | Predicted by pciids.h: the id joins xe's table in 6.17. Unbound on an older kernel, doctor asks for a newer kernel; `xe.force_probe` cannot add a missing id. Fixture `battlemage-pro-b70-k616` | [pciids.h](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/include/drm/intel/pciids.h) |
| Arc A-series, Arc Pro A (56A0-56A6, 5690-5697, 56B0-56B3, 56BA-56BF) | `Alchemist DG2-G10/G11/G12`, kernel 6.2 (56BA-56BD 6.7, 56BE/56BF 6.10) | `gpu.intel.com/i915` (or `xe` if force-probed), exclusive, `gpu.intel.com/xmx=true` | Predicted by i915_pci.c: dg2_info drops require_force_probe in 6.2. xe still requires force_probe, so doctor never suggests xe for it. 56BA-56BF joined i915's id table late, so on an older kernel doctor treats them like late Battlemage ids: unbound asks for a newer kernel, bound is a backport, never force_probe. XMX predicted by Mesa has_systolic (XEHP_FEATURES). Fixture `dg2-a770-i915` | [i915_pci.c v6.2](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/drivers/gpu/drm/i915/i915_pci.c?h=v6.2), [intel_device_info.c](https://gitlab.freedesktop.org/mesa/mesa/-/raw/main/src/intel/dev/intel_device_info.c) |
| Data Center GPU Flex 170/140 (56C0 56C1, 56C2) | `Flex (ATS-M)`, no default kernel | resource follows the bound driver (i915 from intel-i915-dkms, xe if force-probed); `gpu.intel.com/xmx=true` | Predicted by i915_pci.c and xe_pci.c: ats_m keeps require_force_probe in both. Unbound, doctor offers intel-i915-dkms LTS, or on kernel 6.8+ (xe merged in 6.8) xe force-probe with the exact apply and revert of `doctor --experimental` `xe-force-probe`. Below 6.8 there is no xe, so it offers dkms only. Fixture `flex140-unbound` | [xe_pci.c](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/drivers/gpu/drm/xe/xe_pci.c), [xe_pci.c v6.8](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/drivers/gpu/drm/xe/xe_pci.c?h=v6.8), [Intel hardware table](https://dgpu-docs.intel.com/overview/hardware-table.html) |
| Data Center GPU Max (0B69 0B6E 0BD4-0BDB 0BE0 0BE1 0BE5) | `Max (Ponte Vecchio)`, no upstream driver | resource follows the bound driver; no xmx label (not in Mesa) | Predicted by i915_pci.c and xe_pci.c: pvc_info/pvc_desc are unused and no PVC id is in either table. Unbound, doctor names intel-i915-dkms LTS only, no force_probe, and `xe-force-probe` is not offered. 0BD0-0BD3 are pre-production ids missing from `INTEL_PVC_IDS`, so they are not treated as discrete. Fixture `pvc-max1550-unbound` | [support matrix](https://dgpu-docs.intel.com/overview/support-matrix/max-and-flex-support-matrix.html) |

E215 was in xe's table in 6.15 and 6.16 and was dropped in 6.17; it is left unnamed on purpose, so a bound E215 shows as `Intel GPU (0xe215)`.

SR-IOV stays suggest-only. The `xe-sriov` experimental feature (`k3d-gpu doctor --experimental`) lists an xe PF only
when its `sriov_totalvfs` is above zero. xe sets it to zero on Battlemage before
6.17, because `bmg_desc` gains `has_sriov` there
([xe_sriov.c](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/drivers/gpu/drm/xe/xe_sriov.c)).

## NPU

Detection: an accel node `/sys/class/accel/accelN` whose PCI vendor is `0x8086`,
whose bound driver is `intel_vpu`, and whose `/dev/accel/accelN` exists. The
device id maps to a name, the first kernel that knows it and a firmware
generation ([ivpu_fw.c v6.17](https://github.com/torvalds/linux/blob/v6.17/drivers/accel/ivpu/ivpu_fw.c), [ivpu_drv.h v6.17](https://github.com/torvalds/linux/blob/v6.17/drivers/accel/ivpu/ivpu_drv.h)).

Auto config on every NPU row: the intel-npu-plugin DaemonSet with upstream's
`-shared-dev-num` default of 1, i.e. one pod per NPU
([npu_plugin README](https://github.com/intel/intel-device-plugins-for-kubernetes/blob/v0.37.1/cmd/npu_plugin/README.md)),
resource `npu.intel.com/accel`; node labels
`intel.feature.node.kubernetes.io/npu=true`, `k3d-gpu.io/npu.gen=<gen>` and,
when the device node's group is not root, `k3d-gpu.io/npu-render-gid=<gid>`.
A node gets no NPU label when the pinned plugin
([npuIDs at v0.37.1](https://github.com/intel/intel-device-plugins-for-kubernetes/blob/v0.37.1/cmd/npu_plugin/npu_plugin.go))
does not list its id, because the plugin would advertise nothing there.

| Family | Detected as | Auto config | Predicted behaviour | Source |
|---|---|---|---|---|
| Meteor Lake / Arrow Lake-H | `0x7d1d`, 37xx, kernel 6.3+ (CPU model tells MTL from ARL-H) | plugin + labels, `npu.gen=37xx` | kernel 6.14 loads `intel/vpu/vpu_37xx_v0.0.bin`, predicted by `fw_names[]` in ivpu_fw.c | [ivpu_fw.c v6.15](https://github.com/torvalds/linux/blob/v6.15/drivers/accel/ivpu/ivpu_fw.c), fixture `arrowlake-h` |
| Arrow Lake | `0xad1d`, 37xx, kernel 6.6+ | plugin + labels, `npu.gen=37xx` | kernel 6.12 loads `intel/vpu/vpu_37xx_v0.0.bin` (a linux-firmware link to `_v1`), predicted by ivpu_fw.c | [ivpu_fw.c v6.12](https://github.com/torvalds/linux/blob/v6.12/drivers/accel/ivpu/ivpu_fw.c), fixture `arrowlake-s` |
| Lunar Lake | `0x643e`, 40xx, kernel 6.6+ | plugin + labels, `npu.gen=40xx` | kernel 6.8 tries, in order, `vpu_40xx.bin` then `intel/vpu/vpu_40xx_v0.0.bin` and loads the first found, never `_v1`, predicted by ivpu_fw.c | [ivpu_fw.c v6.8](https://github.com/torvalds/linux/blob/v6.8/drivers/accel/ivpu/ivpu_fw.c), fixture `lunarlake-k6.8` |
| Panther Lake | `0xb03e`, 50xx, kernel 6.13+ | plugin + labels, `npu.gen=50xx` | kernel 6.18 loads `intel/vpu/vpu_50xx_v1.bin`, predicted by ivpu_fw.c | [ivpu_fw.c v6.16](https://github.com/torvalds/linux/blob/v6.16/drivers/accel/ivpu/ivpu_fw.c), fixture `pantherlake` |
| Panther Lake on 6.13-6.15 | `0xb03e`, 50xx | plugin + labels; doctor suggests (never applies) a `vpu_50xx_v0.0.bin` link to intel-fw-npu's `_v1`, with its revert | the kernel loads linux-firmware's `vpu_50xx_v0.0.bin`, not intel-fw-npu's `_v1`, predicted by ivpu_fw.c and intel-fw-npu's file list | [ivpu_fw.c v6.15](https://github.com/torvalds/linux/blob/v6.15/drivers/accel/ivpu/ivpu_fw.c), [linux-npu-driver firmware/bin](https://github.com/intel/linux-npu-driver/tree/v1.38.0/firmware/bin), fixture `pantherlake-k6.14` |
| Wildcat Lake | `0xfd3e`, 50xx, kernel 6.17+ | plugin + labels, `npu.gen=50xx` | loads `intel/vpu/vpu_50xx_v1.bin`, predicted by ivpu_fw.c | [ivpu_fw.c v6.17](https://github.com/torvalds/linux/blob/v6.17/drivers/accel/ivpu/ivpu_fw.c), [ivpu_drv.h v6.17](https://github.com/torvalds/linux/blob/v6.17/drivers/accel/ivpu/ivpu_drv.h), fixture `wildcatlake` |
| Nova Lake | `0xd71d`, 60xx, kernel 6.19+; not detected while firmware is missing | none (no accel node); doctor says no 60xx firmware is published | intel_vpu's probe fails for want of `vpu_60xx_v1.bin`, which linux-firmware 20260916 and linux-npu-driver v1.38.0 do not ship, predicted by their file lists | [WHENCE](https://gitlab.com/kernel-firmware/linux-firmware/-/blob/20260916/WHENCE), [ivpu_fw.c v6.19](https://github.com/torvalds/linux/blob/v6.19/drivers/accel/ivpu/ivpu_fw.c), fixture `novalake` |

Doctor explains an NPU PCI function (class `0x1200`) with no driver by the first
cause that holds: a kernel older than the id's first tag; `intel_vpu` not loaded
(it suggests `sudo modprobe intel_vpu`, revert `sudo modprobe -r intel_vpu`); no
firmware under the names that kernel requests. Host changes are suggested only.

## Newest ISA and kernel features

What `k3d-gpu doctor` and `doctor --perf` report for CPU families whose newest
features `/proc/cpuinfo` may not show, and for kernel knobs that matter to agent
inference. Everything here is read-only detection. k3d-gpu changes no host,
kernel or BIOS setting; where a host change helps, doctor prints the exact
`apply` and `revert` commands.

### Why cpuinfo is not enough

The kernel prints a CPU flag only when its `cpufeatures.h` comment starts with a
quoted name ([mkcapflags.sh](https://github.com/torvalds/linux/blob/master/arch/x86/kernel/cpu/mkcapflags.sh),
[cpufeatures.h](https://github.com/torvalds/linux/blob/master/arch/x86/include/asm/cpufeatures.h)).
On current kernels AMX-FP16 and APX are unquoted (older kernels did print
`amx_fp16`, as the `xeon-gnr` fixture shows). AVX10, AMX-FP8 and AMX-COMPLEX
have no `cpufeatures.h` entry; only KVM's
[reverse_cpuid.h](https://github.com/torvalds/linux/blob/master/arch/x86/kvm/reverse_cpuid.h)
defines their `X86_FEATURE_*` macros, so cpuinfo never prints them.
So doctor names the CPU by family and model and reports the ISA that gcc's
`-march` mask predicts. NFD reads CPUID itself and labels what the CPU really
reports (`cpu-cpuid.AVX10_VERSION`, `APX_F`, `AMXFP8`;
[NFD features](https://github.com/kubernetes-sigs/node-feature-discovery/blob/master/docs/usage/features.md)).

### Families

| Family | Detected as (cpuinfo `cpu family` / `model`) | Auto config chosen | Predicted behaviour | Source |
|---|---|---|---|---|
| Granite Rapids-X | 6 / 173 (0xAD) | doctor `info` line; AMX vendor from `amx_*` flags as before | AVX10.1 and AMX-FP16, predicted by gcc `PTA_GRANITERAPIDS` | [intel-family.h](https://github.com/torvalds/linux/blob/master/arch/x86/include/asm/intel-family.h), [gcc i386.h](https://gcc.gnu.org/git/?p=gcc.git;a=blob_plain;f=gcc/config/i386/i386.h;hb=HEAD) |
| Granite Rapids-D | 6 / 174 (0xAE) | same | AVX10.1, AMX-FP16, AMX-COMPLEX, predicted by gcc `PTA_GRANITERAPIDS_D` | same |
| Diamond Rapids-X | 19 / 1 | same; DSA 0x1212 and IAA 0x1216 detected on the dsa bus | AVX10.2, APX_F, AMX-FP8, AMX-FP16, AMX-COMPLEX, AMX-AVX512, AMX-MOVRS, MOVRS, predicted by gcc `PTA_DIAMONDRAPIDS`; oneDNN's `avx10_2_512_amx_2` path, predicted by [dnnl_types.h](https://github.com/uxlfoundation/oneDNN/blob/main/include/oneapi/dnnl/dnnl_types.h) | intel-family.h, gcc i386.h, [idxd init.c](https://github.com/torvalds/linux/blob/master/drivers/dma/idxd/init.c) |
| Nova Lake | 18 / 1 | same; hybrid P/E pools from `cpu_core`/`cpu_atom` as on every hybrid part | AVX10.1, AVX10.2, APX_F, MOVRS, no AMX, predicted by gcc `PTA_NOVALAKE` | intel-family.h, gcc i386.h |
| Nova Lake-L | 18 / 3 | same | as Nova Lake, predicted by gcc `PTA_NOVALAKE` | same |

Families 18 and 19 are matched only when `vendor_id` is `GenuineIntel` (AMD
Llano also prints family 18 model 1). The family-6-only tables (Raptor Lake
DRAM POR, the MSR 0x64F decode) are skipped on families 18 and 19.
Simulation fixture: `tests/fixtures/xeon-dmr` (topology borrowed from
`xeon-gnr`; see its `SOURCES`).

### Never reported

- **AMX-TF32 and AMX-TRANSPOSE**: removed from the ISA. AMX-TRANSPOSE went in
  ISE rev 059 (revision history of
  [ISE 319433-061](https://cdrdv2-public.intel.com/915637/319433-061-architecture-instruction-set-extensions-programming-reference.pdf)).
  AMX-TF32 went in ISE rev 062
  ([current ISE](https://cdrdv2.intel.com/v1/dl/getContent/671368),
  [gcc patch citing ISE062](https://gcc.gnu.org/pipermail/gcc-patches/2026-June/722128.html)).
  cpufeatures.h has never defined either flag, so stock kernels never print
  them; an `amx_tf32` or `amx_transpose` flag from a custom kernel is dropped
  before labeling.
- **UINTR**: Linux has no `X86_FEATURE_UINTR`, so no workload can use it.
- **ITD task classes**: not in mainline (no `X86_FEATURE_ITD`); doctor never
  claims Thread Director scheduling.

### doctor --perf additions

| Check | Reads (unprivileged) | Finding | Apply / revert |
|---|---|---|---|
| Uncore frequency | `/sys/devices/system/cpu/intel_uncore_frequency/*/{max,initial_max,min,initial_min}_freq_khz`, `elc_*` | `warn` when max is below `initial_max_freq_khz`, `info` when above it (raised after boot) or when the floor is raised, `ok` at the full range; ELC settings as `info`; non-numeric max values are skipped and the floor is judged only when both min files read. On TPMI parts only the `uncoreNN` clusters are reported, not the `package_XX_die_YY` roots. `current_freq_khz` is root-only and is not read. A high floor for decode is pending as EXPERIMENTS.md H29 and is never suggested | `echo <initial_max> \| sudo tee max_freq_khz` / `echo <old> \| sudo tee max_freq_khz` ([uncore-frequency-common.c](https://github.com/torvalds/linux/blob/master/drivers/platform/x86/intel/uncore-frequency/uncore-frequency-common.c), [doc](https://docs.kernel.org/admin-guide/pm/intel_uncore_frequency_scaling.html)) |
| TDX host | `/sys/module/kvm_intel/parameters/tdx`, cpuinfo `tdx_host_platform` and `vmx`, osrelease | `info` when `tdx=Y`. With `tdx_host_platform` and `vmx`: `note` when `tdx=N` (stop VMs and sandboxes first) or kvm_intel is not loaded; `info` with no apply when the kernel is older than 6.16 or lacks `CONFIG_KVM_INTEL_TDX`. No BIOS step: the kernel sets `tdx_host_platform` only after TDX init finds the BIOS-enabled KeyID range ([virt/vmx/tdx/tdx.c](https://github.com/torvalds/linux/blob/master/arch/x86/virt/vmx/tdx/tdx.c)) | `sudo modprobe -r kvm_intel && sudo modprobe kvm_intel tdx=1` / same with `tdx=0`; not loaded: `sudo modprobe kvm_intel tdx=1` / `sudo modprobe -r kvm_intel` ([kvm/vmx/tdx.c](https://github.com/torvalds/linux/blob/v6.16/arch/x86/kvm/vmx/tdx.c), absent at v6.15) |
| TDX guest | cpuinfo `tdx_guest`, `/dev/tdx_guest` | `info`: the node is a confidential VM; attestation via configfs-tsm | none ([tdx-guest.c](https://github.com/torvalds/linux/blob/master/drivers/virt/coco/tdx-guest/tdx-guest.c)) |

## Intel Enterprise Agent Toolkit (EAT)

EAT is a software stack, not a hardware family, so it has no fixture. It reaches k3d-gpu
two ways, one per EAT generation. Its pins:

- **EAT 1.x:** `integrations/eat/eat-up.sh` deploys the pinned tag
  [`release-1.1.0`](https://github.com/intel/enterprise-agent-toolkit/releases/tag/release-1.1.0)
  ([`0c726f5`](https://github.com/intel/enterprise-agent-toolkit/tree/0c726f576595971c53cc45b94e08ba969beb4a4a),
  published 2026-10-22), still the latest tag on 2026-10-29
  ([tags](https://api.github.com/repos/intel/enterprise-agent-toolkit/tags)) and the merge of
  [PR #21](https://github.com/intel/enterprise-agent-toolkit/pull/21). Its tree, `04969afa`,
  is the same as that of #21's head `c86c42b`. Until 2026-10-29 the pin was
  `release-1.0.3-hotfix`
  ([`0d3438d`](https://github.com/intel/enterprise-agent-toolkit/tree/0d3438d7119de44cafc5903f66665c0b623e3333);
  [compare](https://github.com/intel/enterprise-agent-toolkit/compare/0d3438d7119de44cafc5903f66665c0b623e3333...0c726f576595971c53cc45b94e08ba969beb4a4a),
  204 files). `bench/eatflow` keeps its own EAT pins (`bench/eatflow/agent.lock`).
- **EAT 2.0:** `k3d-gpu eas up --agent-toolkit` installs EAT through Intel's EAS installer
  (`es_auto_installer.sh install agent-toolkit`) on the cluster. It pins EAS `main`
  `97f6157`, enterprise-inference `main` `3e8a244` and EAT `release-2.0.0` `036b662` in
  `integrations/eas/pins.json`, with EAS `v1.2.0-rc.1` as an opt-in profile. Choices,
  patches, refusals and limits: [intel/eas.md](intel/eas.md). It has not run end to end
  yet; F3 in [EXPERIMENTS.md](../EXPERIMENTS.md#f3-pre-registration-i9-13900t-2026-10-29)
  is PENDING.
- `docs/intel.md`, `contrib/eat/` and `integrations/eat/upstream/` read `main` at
  [`0ecf67a`](https://github.com/intel/enterprise-agent-toolkit/tree/0ecf67a8331e8b502ef41e08bf6d2881416178c5).

The `release-1.1.0` path has not run. It is pre-registered as F4 in
[EXPERIMENTS.md](../EXPERIMENTS.md#f4-pre-registration-i9-13900t-2026-10-29) and is
unverified until F4 is SUPPORTED; the run on `release-1.0.3-hotfix` passed [ledger:H1b].
The move makes the two changes described below: `brownfield.sh` hands the config's
`deploy_guardrails` (`off` in `agentic-config.cfg`) to EAT's `DEPLOY_GUARDRAILS`, and
`vllm-values.yaml` pins the vLLM CPU image `v0.28.0` by digest. `agentic-config.cfg` also
writes `deploy_vllm_sr`, `deploy_openshell`, `deploy_kata_containers`, `deploy_ceph` and
`rack_scale_mode` out as `off`. The ledger rows that pinned EAT stock at `0d3438d` use their
own EAT checkouts and set their own vLLM image tag (H50, H50o and H51 under crescent
amendment E1, H7x under its amendment V1, and `bench/apb/h7.sh` and `arm.sh` for H7 and H8),
so the move changes none of them.

`release-1.1.0` ships NVIDIA OpenShell as an opt-in component
(`deploy_openshell=off` by default, [PR #14](https://github.com/intel/enterprise-agent-toolkit/pull/14)).
It has not run on a k3d-gpu cluster, so `eat-up.sh` stops on a config that sets
`deploy_openshell=on` before it touches the cluster. Upstream lists a CNI
that enforces `NetworkPolicy` and best-effort Landlock as OpenShell requirements
([docs/openshell.md](https://github.com/intel/enterprise-agent-toolkit/blob/0c726f576595971c53cc45b94e08ba969beb4a4a/docs/openshell.md)).
k3d-gpu adds no OpenShell checks; enabling it needs its own ledger row first.

Upstream work that changes this integration:

- [EAT PR #19](https://github.com/intel/enterprise-agent-toolkit/pull/19) replaces
  `deploy-agentic-stack.sh` with `es_auto_installer.sh install agent-toolkit`. It merged
  on 2026-10-27 into the `release-2.0.0` branch, not `main`, and no EAT tag carries it yet;
  EAS's pre-release `v1.2.0-rc.1` pins it. `k3d-gpu eas` is the EAT 2.0 path.
  The status of each EAT PR is in the table below.
- [enterprise-inference PR #22](https://github.com/intel/enterprise-inference/pull/22)
  merged on 2026-10-22 as "Integrate Agentic toolkit" (`4bc7fed`), the inference-layer
  side of EAT: vLLM Semantic Router, a LiteLLM hybrid router, the Qwen3Guard guardrail
  and CPU models on vLLM 0.28.0. The `vllm-xpu` runtime this bullet described when it was
  written (2026-10-17) is not in the merged commit, and no intel/enterprise-inference
  branch has it. Arc vLLM now ships in EAT `release-1.1.0` (`compute_platform=xpu`, below),
  which requests `gpu.intel.com/xe` by default. The Intel GPU
  plugin defines both `xe` and `i915` resources
  ([gpu_plugin.go](https://github.com/intel/intel-device-plugins-for-kubernetes/blob/main/cmd/gpu_plugin/gpu_plugin.go)),
  and `k3d-gpu detect` already reports the one the bound driver gives.

### EAT pull requests (checked 2026-10-28)

Read with `gh`, `git ls-remote` and a read-only clone only. EAT `main` is `4a2e930`:
`release-1.1.0` plus the architecture diagram from
[#27](https://github.com/intel/enterprise-agent-toolkit/pull/27).
`release-1.0.3-hotfix` still points at `0d3438d`; `eat-up.sh` moved from it to
`release-1.1.0` on 2026-10-29, and `k3d-gpu eas` took over EAT 2.0. The #19 and #21 rows
were re-read that day; EAT `main` was still `4a2e930`.

| PR | Head | What it changes | Impact on k3d-gpu | Action |
|---|---|---|---|---|
| [#5](https://github.com/intel/enterprise-agent-toolkit/pull/5) Bitnami to SolDevelo images | `fcec7ef`, rebased onto `main` | `bitnami/kubectl:latest` becomes `soldevelo/kubectl:1.29` in the GenAI Gateway restore job, and `bitnamilegacy/minio:2024.12.18` becomes `soldevelo/minio:2024.12.18` in `docker/docker-compose.yml` | None. Neither line runs on our path: `restoreJob.enabled` is `false`, and `eat-up.sh` does not use EAT's Docker Compose mode. Every Bitnami image our path renders pulled on 2026-10-18 (`docker manifest inspect`, amd64 and arm64). See the note below | None. Recheck if a `bitnamilegacy` pull fails |
| [#19](https://github.com/intel/enterprise-agent-toolkit/pull/19) unified convention | Merged 2026-10-27 into `release-2.0.0`, not `main`: final head `fb59459`, merge `036b662`, no tag | Deletes `deploy-agentic-stack.sh`, `core/lib` (the brownfield path with it), `core/playbooks` and `core/roles`; installs through EAS `es_auto_installer.sh`. `release-2.0.0`'s README says Intel Arc (XPU) deployment is not in that release | `eat-up.sh` cannot deploy it: the brownfield path it drives is gone. `k3d-gpu eas up --agent-toolkit` installs it through the EAS bring-your-own-cluster path at `036b662`, the commit EAS `v1.2.0-rc.1` pins too; read from source, F3 PENDING. The drafted EAT docs patch in [upstream-eat.md](intel/upstream-eat.md) passes `git apply --check` on the draft head `9d842f5` but not on `fb59459` or `036b662`, so it needs a re-base before it is sent | EAT 2.0: `k3d-gpu eas up --agent-toolkit` ([intel/eas.md](intel/eas.md)). EAT 1.x stays on `eat-up.sh` |
| [#20](https://github.com/intel/enterprise-agent-toolkit/pull/20) OpenClaw operator | `10deb6d`, on #19's branch `agentic-toolkit-migration` (forked at `dab3a98`, before #19's final head) | Optional `openclaw_operator` role (`claw_enabled: false`): operator chart and image `0.38.3` pinned by digest, 3 CRDs (`openclaw.rocks/v1alpha1`). It also adds a `cosign verify` of the chart and the operator image when `cosign` is on the installer's `PATH` (`openclaw_operator_cosign_required: false` by default) | Not reachable from the pinned tag. The operator itself fits one k3d-gpu node: CPU only (100m / 128Mi request), no GPU, no webhook, `kubeVersion >=1.28`, and both images publish amd64. Its example instance is the gap (see below) | None until #19 and #20 reach a tag |
| [#21](https://github.com/intel/enterprise-agent-toolkit/pull/21) Release 1.1.0 | Merged 2026-10-22 as `0c726f5` and released as `release-1.1.0`; head `c86c42b`, 204 files against `release-1.0.3-hotfix` | Keeps the old layout (`deploy-agentic-stack.sh`, `core/lib`); adds Qwen3Guard guardrails (on by default), Kata sandboxes (`kata-fc` Firecracker and `kata-qemu`), vLLM Semantic Router, rack-scale mode, an NVIDIA B300 profile, and an Arc B60/B70 XPU path (`compute_platform=xpu`); OVMS as a Docker engine; vLLM CPU image `v0.24.0` to `v0.28.0`; NRI balloons role reworked into NUMA-aware per-node policies | `eat-up.sh` deploys it since 2026-10-29. `core/lib/brownfield/` and `deploy-genai-gateway.yml` are unchanged, every function `brownfield.sh` calls still exists, and `gateway-charts.py` runs clean on its charts. Two changes needed handling (the guardrail and the vLLM image), and the XPU path would overlap what k3d-gpu already installs; all read from source, not run (see below) | Done 2026-10-29: `eat-up.sh` deploys `release-1.1.0` with the guardrail and the other new components off and the vLLM CPU image re-pinned to `v0.28.0` by digest; the path is pre-registered as F4, not yet run. Validation on 4x Arc Pro B70 is in progress, no results yet |
| [#22](https://github.com/intel/enterprise-agent-toolkit/pull/22)-[#26](https://github.com/intel/enterprise-agent-toolkit/pull/26) agent toolkit stack | `a627fad`, `a32c5aa`, `9edf77d`, `6dfbcbc`, `7cc4344`, stacked on #20, base `agentic-toolkit-migration` | `Agent`, `AgentGrant` and `Tool` CRDs (`intel-stack.io`) with an agent operator (#22); per-agent memory (#23); sandbox, GitHub and web tools for agents (#24); an agent REST API (#25); a browser console and agent dashboards (#26). Its images are built into an in-cluster registry (`registry.kube-system.svc.cluster.local:5000`, the `kubernetes_registry_enabled` add-on) | Not reachable from the pinned tag. The stack claims sandboxes through `SandboxClaim` at `extensions.agents.x-k8s.io/v1beta1`, the version k3d-gpu's agent-sandbox v1.0.7 serves. Read from source, not run | None until the stack reaches a tag |

Bitnami images on the pinned path. `deploy-genai-gateway.yml` runs `helm dependency update`
on `genai-gateway`, whose `Chart.yaml` asks for `postgresql >=13.3.0` and `redis >=18.0.0`.
That ignores the vendored `Chart.lock` (16.7.4 / 21.1.3). On 2026-10-18, Helm v3.14.0 (the
bastion's version) resolved postgresql 18.12.4 and redis 28.3.1. Their default tag is
`latest`, so with EAT's repository overrides the gateway pulls
`bitnamilegacy/postgresql:latest` (PostgreSQL 17.5.0, built 2025-07-21) and
`bitnamilegacy/redis:latest` (8.0.3, built 2025-07-17). The Langfuse chart is pinned
(`1.5.30`) and pulls `bitnamilegacy/{postgresql:17.3.0,valkey:8.0.2,clickhouse:25.2.1,zookeeper:3.9.3,minio:2024.12.18}`
at fixed `-debian-12-rN` tags. The H1b run (2026-10-17) recorded pod names, not image
references, so which images it pulled is not on record. Bitnami calls `bitnamilegacy` a
temporary migration archive with no updates
([bitnami/containers#83267](https://github.com/bitnami/containers/issues/83267)).

Pinned on our side since 2026-10-18, with no EAT file edited. `brownfield.sh` hands
`deploy-genai-gateway.yml` copies of both gateway chart directories as extra vars
(`helm_charts_base_genai{,_trace}`), written by `integrations/eat/gateway-charts.py`.
`genai-gateway`'s `Chart.yaml` asks for exactly postgresql 18.12.4 and redis 28.3.1, the
versions that resolved that day. Every image the playbook renders is name:tag@sha256 at the
tag it resolved that day. `latest` became `bitnamilegacy/postgresql:17.5.0-debian-12-r20`
and `bitnamilegacy/redis:8.0.3-debian-12-r2`, the tags that share `latest`'s digest. The
pins are in `gateway-values.yaml` and `gateway-trace-values.yaml`, and listed in `images.lock`.
`scripts/images-lock pins` fails CI on any values `repository:` there without a digest.
Rendered with Helm v3.14.0, the pinned charts differ from EAT's only in the 11 `image:` lines.

What #21 (`c86c42b`) changes for `eat-up.sh`, compared with `release-1.0.3-hotfix`.
It applies verbatim to `release-1.1.0`, whose tree is `c86c42b`'s. Read from a read-only
clone; nothing here has run against a 1.1.0 stack. The guardrail and vLLM-image items are
the two the move of `eat-up.sh` to `release-1.1.0` had to handle.

- Unchanged: `core/lib/brownfield/` (so the `5 / 1 / yes` prompt answers),
  `core/playbooks/deploy-genai-gateway.yml` (so the `helm_charts_base_genai{,_trace}`
  extra vars), and `genai-gateway/Chart.yaml`. `_source_core_libs`, `brownfield_deployment`,
  `manage_kubeconfig`, `read_config_file`, `prompt_for_input`, `setup_initial_env`,
  `setup_bastion` and `deploy_inference_llm_models_playbook` are all still defined.
  `cpu-qwen3-1-7b` is still in `deploy-inference-models.yml`, and `Qwen/Qwen3-1.7B` is
  still a `modelConfigs` key in `xeon-values.yaml`.
- `gateway-charts.py` runs clean on #21's `core/helm-charts`: every pinned repository and
  tag still matches, and the dependencies resolve to postgresql 18.12.4 and redis 28.3.1.
  The gateway values add `customRouter.enabled: true` and a second LiteLLM callback
  (`custom_hybrid_router.proxy_handler_instance`), loaded from a ConfigMap, not a new image.
- Guardrail on by default. `deploy-agentic-stack.sh` sets `DEPLOY_GUARDRAILS="${DEPLOY_GUARDRAILS:-on}"`
  at top level (line 195), which runs when `brownfield.sh` sources it. The `deploy_guardrails`
  key in `agentic-config.cfg` is read only inside `main()` (line 1138), which a sourced run
  skips. `fresh-install.sh` (line 419) then deploys the `Qwen/Qwen3Guard-Gen-0.6B` guard.
  `brownfield.sh` now reads `deploy_guardrails` from the config, as `read_config_file` reads
  every other toggle, and exports `DEPLOY_GUARDRAILS` before it sources the script;
  `agentic-config.cfg` sets it `off`. With `deploy_guardrails=on`, `deploy-guardrails.yml`
  gets a copy of EAT's charts in which the guard's `guardrail-values.yaml` (line 30, `v0.28.0`
  by tag only) carries the digest `vllm-values.yaml` pins (`guard-values.py`); the guard has
  not run on a k3d-gpu cluster.
- vLLM image. #21's `xeon-values.yaml` (line 34) moves `vllm/vllm-openai-cpu` from
  `v0.24.0` to `v0.28.0`. `vllm-values.yaml` merges over it with no tag check, so it now
  pins `v0.28.0` by digest (`sha256:197354e6...df9647`, Docker Hub, read 2026-10-29).
- New `agentic-config.cfg` keys (`deploy_kata_containers`, `kata_runtime`, `deploy_ceph`,
  `rack_scale_mode`, `deploy_guardrails`, `deploy_vllm_sr`, `deploy_openshell`) are all
  opt-in except `deploy_guardrails`. k3d-gpu's `agentic-config.cfg` writes each toggle out
  as `off`. The NRI balloons role is reworked (`install_nri.yaml` removed); `eat-up.sh`
  keeps `deploy_nri_balloon_policy=off`, so k3d-gpu's policy stays the only one.
- The gateway's config moves to `/app/proxy_config.yaml` through a `subPath` mount, so an edit
  to the `genai-gateway-config` ConfigMap reaches LiteLLM only after the pod restarts;
  `eat-remote.sh` already restarts it. Every name the hybrid-router callback imports from or
  patches in LiteLLM is defined at the pinned `v1.83.14-stable.patch.2`
  ([`router.py`](https://github.com/BerriAI/litellm/blob/b36fb1dc191620e8ff2b3a43854c05f5189cd813/litellm/router.py):
  `_is_auto_router_deployment`, `_add_deployment`, `async_pre_routing_hook`).
- #21 does not contain #19: it keeps `deploy-agentic-stack.sh`, which #19 deletes.

#21's XPU path (`compute_platform=xpu`, `docs/intel-arc-xpu-deployment.md`), set against
a k3d-gpu cluster. Read from `c86c42b`; every overlap below is an expectation until the
B70 runs land:

- What it deploys: `vllm/vllm-openai-xpu:v0.28.0` from `core/helm-charts/vllm/xpu-values.yaml`,
  default model `google/gemma-4-31B-it` at tensor parallel 4, resource `gpu.intel.com/xe`
  (`i915` on older drivers), `supplementalGroups: [990, 44]`, node affinity on
  `ei-inference-eligible=true`, and the host's Level Zero and OpenCL libraries through
  `hostPath` (`/usr/lib/x86_64-linux-gnu`, `/etc/OpenCL/vendors`,
  `/usr/lib/x86_64-linux-gnu/intel-opencl`).
- What k3d-gpu already covers: B70 detection and the Intel GPU plugin `0.37.1`, baked
  and pinned; `gpu.intel.com/xe` advertised and asserted by `up`; the label through
  `K3D_GPU_NODE_LABELS`; vLLM XPU through `K3D_GPU_AGENT_BACKEND=vllm` (`v0.32.0`, by digest).
- Expected collision: a second GPU plugin. `deploy-intel-gpu-plugin.yml` applies
  `deployments/gpu_plugin/base/intel-gpu-plugin.yaml` from intel-device-plugins `main`
  (`intel_gpu_plugin_ref` defaults to `main`, unpinned) beside the baked DaemonSet.
  `eat-up.sh` therefore stops on `compute_platform=xpu` and points at
  `K3D_GPU_AGENT_BACKEND=vllm`.
- Expected collision: a second balloons policy. The reworked `nri_cpu_balloons` role
  renders one NUMA-sized `BalloonsPolicy` per node (`node.<name>`). #21 still installs
  it only on `deploy_nri_balloon_policy == "yes"` (`fresh-install.sh` line 247), so
  `eat-up.sh`'s `off` keeps it away, and `eat-up.sh` stops on any other value, an
  absent key included (`prompt_for_input`, which the stack path and `brownfield.sh model`
  both call, turns that on).
- Expected gap: the `hostPath` libraries resolve inside the k3d node container, not
  on the host. The node image would have to carry compatible Level Zero and OpenCL
  user space at those paths, or the pod would mount empty or mismatched libraries.
- Expected gap: Kata (`kata-fc`, `kata-qemu`) needs `/dev/kvm` and nested
  virtualization inside the k3d node, which k3d-gpu does not pass through today.
  `eat-up.sh` stops on `deploy_kata_containers=on`.

Gaps for the #20 example instance (`docs/examples/openclaw-instance.yaml`) on k3d-gpu:

- It calls the EAS inference layer's `ai-gateway` in `envoy-gateway-system`. The
  `release-1.1.0` stack `eat-up.sh` deploys has LiteLLM behind ingress-nginx instead, so `baseUrl`
  has to change.
- Its namespace joins the Istio ambient mesh, and its egress rule names HBONE port 15008.
  A k3d-gpu cluster has no Istio, and k3s enforces `NetworkPolicy` by default. Without the mesh, the
  rule must name the gateway's own port.
- It needs a model with a 32k+ context, because the agent prompt is about 19,500 tokens.
  The example uses `qwen2-5-coder-14b`, about 28 GiB of bf16 weights on CPU. Qwen3-1.7B
  (40960 tokens) fits the window; its agent quality is untested.
- The gateway's request buffer must exceed Envoy's 32 KiB default. That applies to the
  EAS gateway only, not to LiteLLM.
- Storage (a 10Gi PVC by default) and the `restricted` Pod Security labels work as-is
  on k3s with `local-path`.

k3d-gpu needs no change to run the operator. Running the example end to end needs the
EAS BYO path after #19, or a `baseUrl` and egress rule pointed at LiteLLM. Both are
UNVERIFIED.
