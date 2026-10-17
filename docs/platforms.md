# Platforms: newest ISA and kernel features

What `k3d-gpu doctor` and `doctor --perf` report for CPU families whose newest
features `/proc/cpuinfo` may not show, and for kernel knobs that matter to agent
inference. Everything here is read-only detection. k3d-gpu changes no host,
kernel or BIOS setting; where a host change helps, doctor prints the exact
`apply` and `revert` commands.

## Why cpuinfo is not enough

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

## Families

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

## Never reported

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

## doctor --perf additions

| Check | Reads (unprivileged) | Finding | Apply / revert |
|---|---|---|---|
| Uncore frequency | `/sys/devices/system/cpu/intel_uncore_frequency/*/{max_freq_khz,initial_max_freq_khz}` | `warn` when max is below the firmware maximum, `ok` when equal, `info` when above; non-numeric values are skipped. On TPMI parts only the `uncoreNN` clusters are reported, not the `package_XX_die_YY` roots. `current_freq_khz` is root-only and is not read | `cat initial_max_freq_khz \| sudo tee max_freq_khz` / `echo <old> \| sudo tee max_freq_khz` ([uncore-frequency-common.c](https://github.com/torvalds/linux/blob/master/drivers/platform/x86/intel/uncore-frequency/uncore-frequency-common.c), [doc](https://docs.kernel.org/admin-guide/pm/intel_uncore_frequency_scaling.html)) |
| TDX host | `/sys/module/kvm_intel/parameters/tdx`, cpuinfo `tdx_host_platform` and `vmx`, osrelease | `info` when `tdx=Y`. With `tdx_host_platform` and `vmx`: `note` when `tdx=N` (stop VMs and sandboxes first) or kvm_intel is not loaded; `info` with no apply when the kernel is older than 6.16 or lacks `CONFIG_KVM_INTEL_TDX`. No BIOS step: the kernel sets `tdx_host_platform` only after TDX init finds the BIOS-enabled KeyID range ([virt/vmx/tdx/tdx.c](https://github.com/torvalds/linux/blob/master/arch/x86/virt/vmx/tdx/tdx.c)) | `sudo modprobe -r kvm_intel && sudo modprobe kvm_intel tdx=1` / same with `tdx=0`; not loaded: `sudo modprobe kvm_intel tdx=1` / `sudo modprobe -r kvm_intel` ([kvm/vmx/tdx.c](https://github.com/torvalds/linux/blob/v6.16/arch/x86/kvm/vmx/tdx.c), absent at v6.15) |
| TDX guest | cpuinfo `tdx_guest`, `/dev/tdx_guest` | `info`: the node is a confidential VM; attestation via configfs-tsm | none ([tdx-guest.c](https://github.com/torvalds/linux/blob/master/drivers/virt/coco/tdx-guest/tdx-guest.c)) |
