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
  here is a measurement. Every fixture plans `unmeasured` until `k3d-gpu prove` runs
  on a real box, or `fix` where a target is refused (`raptorlake-stale-guc`,
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
| Xeon 6 SoC (Granite Rapids-D) | `xeon-gnrd` | DSA, AMX | DSA plugin; no NRI balloons | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-gnrd/SOURCES) |
| Xeon 6 E-core (Sierra Forest) | `xeon-srf` | DSA, IAA (no AMX) | DSA and IAA plugins; no NRI balloons | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-srf/SOURCES) |
| Xeon + QAT 420xx | `qat-420xx` | QAT, AMX | QAT plugin; no NRI balloons | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/qat-420xx/SOURCES) |
| Gaudi 3 (HLS) | `gaudi3-hls` | `habana.ai/gaudi`, AMX | Habana device plugin; no NRI balloons | plan targets: cpu, hpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/gaudi3-hls/SOURCES) |
| Gaudi 3, no driver | `gaudi3-no-driver` | AMX; Gaudi skipped with a warning | no Gaudi plugin; no NRI balloons | plan refuses the hpu until `habanalabs` loads and prints the fix (install the Gaudi driver, then `modprobe habanalabs`); doctor only warns and skips Gaudi; `fix` | [SOURCES](../tests/fixtures/gaudi3-no-driver/SOURCES) |
| Arrow Lake-U (Core Ultra 200U) | `arrowlake-u` | `gpu.intel.com/i915`, hybrid | Intel GPU plugin (shared iGPU), NRI balloons (hybrid P/E) | plan targets: cpu, gpu (i915, no XMX) usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/arrowlake-u/SOURCES) |
| Lunar Lake (Core Ultra 200V) | `lunarlake` | `gpu.intel.com/xe`, `npu.intel.com/accel`, hybrid | Intel GPU plugin (shared iGPU), NPU plugin, NRI balloons (hybrid P/E) | plan targets: cpu, gpu (xe), npu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/lunarlake/SOURCES) |
| Xeon CPU Max (HBM cache mode, SNC4) | `xeon-max-hbm-cache` | AMX, NUMA | AMX and NUMA labels, NRI balloons (NUMA replica) | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-max-hbm-cache/SOURCES) |
| Xeon CPU Max (HBM as devdax, SNC4) | `xeon-max-hbm-devdax` | AMX, NUMA | AMX and NUMA labels, NRI balloons (NUMA replica) | plan targets: cpu usable; no catalog cell on this host; `unmeasured` | [SOURCES](../tests/fixtures/xeon-max-hbm-devdax/SOURCES) |

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
| Wildcat Lake (model 213) | iGPU 0xfd80/0xfd81 on xe (6.17+, `ptl_desc`); NPU 0xfd3e (50xx, 6.17+) | `gpu.intel.com/xe`, `xmx=true`, `npu.gen=50xx`; doctor asks for kernel 6.17 below it, with no `force_probe` advice (no earlier `pciids.h` lists either id); detect_test only, no fixture | Same xe path as Panther Lake, predicted by v6.17 `INTEL_PTL_IDS` listing both ids | [pciids.h v6.17](https://github.com/torvalds/linux/blob/v6.17/include/drm/intel/pciids.h) |

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

## Intel Enterprise Agent Toolkit (EAT)

EAT is a software stack, not a hardware family, so it has no fixture. Its pins:

- `integrations/eat/eat-up.sh` deploys tag `release-1.0.3-hotfix`
  ([`0d3438d`](https://github.com/intel/enterprise-agent-toolkit/tree/0d3438d7119de44cafc5903f66665c0b623e3333)),
  the latest tag as of 2026-10-17 ([tags](https://api.github.com/repos/intel/enterprise-agent-toolkit/tags)).
- `docs/intel.md`, `contrib/eat/` and `integrations/eat/upstream/` read `main` at
  [`0ecf67a`](https://github.com/intel/enterprise-agent-toolkit/tree/0ecf67a8331e8b502ef41e08bf6d2881416178c5).

`main` adds one opt-in component after the tag: NVIDIA OpenShell
(`deploy_openshell=off` by default, [PR #14](https://github.com/intel/enterprise-agent-toolkit/pull/14)).
The tag has no OpenShell role, so `eat-up.sh` stops on a config that sets
`deploy_openshell=on` instead of letting the tag skip the key. Upstream lists a CNI
that enforces `NetworkPolicy` and best-effort Landlock as OpenShell requirements
([docs/openshell.md](https://github.com/intel/enterprise-agent-toolkit/blob/0ecf67a8331e8b502ef41e08bf6d2881416178c5/docs/openshell.md)).
k3d-gpu adds no OpenShell checks until a tagged EAT release ships it.

Open upstream work that changes this integration:

- [EAT PR #19](https://github.com/intel/enterprise-agent-toolkit/pull/19) replaces
  `deploy-agentic-stack.sh` with `es_auto_installer.sh install agent-toolkit`.
  `eat-up.sh` and `brownfield.sh` stay on `release-1.0.3-hotfix` until a tag after #19.
- [enterprise-inference PR #22](https://github.com/intel/enterprise-inference/pull/22)
  adds a `vllm-xpu` runtime that requests `gpu.intel.com/xe` by default. The Intel GPU
  plugin defines both `xe` and `i915` resources
  ([gpu_plugin.go](https://github.com/intel/intel-device-plugins-for-kubernetes/blob/main/cmd/gpu_plugin/gpu_plugin.go)),
  and `k3d-gpu detect` already reports the one the bound driver gives.
