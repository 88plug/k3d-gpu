# Platforms: newest ISA and kernel features

What `k3d-gpu doctor` and `doctor --perf` report for CPU families whose newest
features `/proc/cpuinfo` cannot show, and for kernel knobs that matter to agent
inference. Everything here is read-only detection. k3d-gpu changes no host,
kernel or BIOS setting; where a host change helps, doctor prints the exact
`apply` and `revert` commands.

## Why cpuinfo is not enough

The kernel prints a CPU flag only when its `cpufeatures.h` comment starts with a
quoted name ([mkcapflags.sh](https://github.com/torvalds/linux/blob/master/arch/x86/kernel/cpu/mkcapflags.sh),
[cpufeatures.h](https://github.com/torvalds/linux/blob/master/arch/x86/include/asm/cpufeatures.h)).
AMX-FP16 and APX are unquoted. AVX10, AMX-FP8 and AMX-COMPLEX have no
`X86_FEATURE` at all; they live only in KVM's
[reverse_cpuid.h](https://github.com/torvalds/linux/blob/master/arch/x86/kvm/reverse_cpuid.h).
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

Families 18 and 19 are Intel; no model table in k3d-gpu assumes family 6.
Simulation fixture: `tests/fixtures/xeon-dmr` (topology borrowed from
`xeon-gnr`; see its `SOURCES`).

## Never reported

- **AMX-TF32 and AMX-TRANSPOSE**: cut from the ISA (ISE rev 059 and rev 062;
  [ISE 319433-061](https://cdrdv2-public.intel.com/915637/319433-061-architecture-instruction-set-extensions-programming-reference.pdf),
  [Phoronix](https://www.phoronix.com/news/Intel-Kills-AMX-TF32)). An
  `amx_tf32` or `amx_transpose` flag is dropped before labeling.
- **UINTR**: Linux has no `X86_FEATURE_UINTR`, so no workload can use it.
- **ITD task classes**: not in mainline (no `X86_FEATURE_ITD`); doctor never
  claims Thread Director scheduling.

## doctor --perf additions

| Check | Reads (unprivileged) | Finding | Apply / revert |
|---|---|---|---|
| Uncore frequency | `/sys/devices/system/cpu/intel_uncore_frequency/*/{max_freq_khz,initial_max_freq_khz}` | `warn` when max is below the firmware maximum, else `ok`. `current_freq_khz` is root-only and is not read | `cat initial_max_freq_khz \| sudo tee max_freq_khz` / `echo <old> \| sudo tee max_freq_khz` ([uncore-frequency-common.c](https://github.com/torvalds/linux/blob/master/drivers/platform/x86/intel/uncore-frequency/uncore-frequency-common.c), [doc](https://docs.kernel.org/admin-guide/pm/intel_uncore_frequency_scaling.html)) |
| TDX host | `/sys/module/kvm_intel/parameters/tdx`, cpuinfo `tdx_host_platform` | `info` when `tdx=Y` (confidential microVM sandboxes possible); `note` when the CPU has TDX but kvm_intel runs without it | BIOS TDX on, then `sudo modprobe -r kvm_intel && sudo modprobe kvm_intel tdx=1` / same with `tdx=0` ([kvm/vmx/tdx.c](https://github.com/torvalds/linux/blob/master/arch/x86/kvm/vmx/tdx.c)) |
| TDX guest | cpuinfo `tdx_guest`, `/dev/tdx_guest` | `info`: the node is a confidential VM; attestation via configfs-tsm | none ([tdx-guest.c](https://github.com/torvalds/linux/blob/master/drivers/virt/coco/tdx-guest/tdx-guest.c)) |
