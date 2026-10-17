# Experimental features (`K3D_GPU_EXPERIMENTAL`)

Some features already exist upstream but are still alpha, beta, preview or hidden.
They sit behind a Kubernetes feature gate, an OpenVINO property that is off by
default, a kernel module parameter, or a sysfs knob. k3d-gpu can switch some of
them on for you, and it **auto-adapts**: with `K3D_GPU_EXPERIMENTAL` unset (or
`auto`), `up` enables every feature that this host supports, that is beta or GA
in the shipped k3s, and that needs no host change. On the shipped k3s 1.37 that
is `dra` and `image-volume` everywhere and `npu-turbo` on hosts with an Intel
NPU. Alpha features (`dra` before 1.36) and features that need a host change
(`scx`, `xe-force-probe`, `xe-sriov`) never turn on by themselves.

```bash
k3d-gpu up                                              # auto: safe, mature, detected features
K3D_GPU_EXPERIMENTAL=list k3d-gpu detect                # every feature and whether this host can use it
k3d-gpu doctor --experimental                           # the same, as a doctor section
K3D_GPU_EXPERIMENTAL=dra,image-volume k3d-gpu up        # exactly these two (overrides auto)
K3D_GPU_EXPERIMENTAL=off k3d-gpu up                     # none
```

Rules:

- **k3d-gpu only shapes the cluster.** A feature may add k3s feature gates
  (`--k3s-arg`), node labels, and a ConfigMap holding OpenVINO plugin config.
  It never touches the host kernel, a module, sysfs or the BIOS. A feature that needs a
  host change prints the exact command and its revert, and `up` labels them
  `NOT run by k3d-gpu`.
- **An unknown name is an error** for every command, before anything runs.
  `list` cannot be combined with feature names.
- **A requested feature must be usable.** If its prerequisite is missing, `up`
  stops before `k3d cluster create` and prints the exact reason. `doctor` shows
  that feature as `fail`.
- Every enabled feature labels the node `experimental.k3d-gpu.io/<name>=true`,
  so workloads can select on it.
- Undo: `k3d-gpu down && k3d-gpu up` without the feature. Feature gates are
  process flags, so nothing persists in the cluster. Host steps carry their own
  revert.

## The registry

Each feature is one `exp_<name>` function in `scripts/k3d-gpu`. It answers these
actions:

| Action | Contract |
|--------|----------|
| `status` | Upstream maturity, with the release it shipped in |
| `families` | The hardware it applies to |
| `value` | What it gives an agent workload |
| `detect` | Exits 0 when the host has what the feature acts on. Prints nothing. |
| `prereq` | Exits 0 when the feature can be enabled. Otherwise prints the exact reason. |
| `apply` | Appends to `EXP_GATES`, `EXP_LABELS`, `EXP_OVCFG` and `EXP_HOST`. No side effects. |
| `revert` | How to undo it |

`list` and `doctor` report a feature in one of three states:

- `available`: `prereq` passes.
- `unavailable`: `detect` finds hardware, but a prerequisite is missing.
- `absent`: there is nothing for the feature to act on.

`up` merges every feature's output:

- **Feature gates** become one `feature-gates=` value per component: the
  apiserver, scheduler and controller-manager on servers, and the kubelet on
  every node. k3s passes a repeated `--<component>-arg` key as repeated flags
  (k3s `pkg/util/args.go` `GetArgs`,
  https://github.com/k3s-io/k3s/blob/v1.37.1%2Bk3s1/pkg/util/args.go), and each
  `--feature-gates` flag adds to the earlier ones (component-base
  `featuregate.Set`), so a `feature-gates` value in `K3D_GPU_K3S_ARGS` is kept.
  One merged value keeps one flag per component in the process args. k3s sets no
  `feature-gates` of its own on a normal (non-rootless) node.
- **A gate that is already on by default in the shipped k3s is not passed.**
  `EXP_K3S_MINOR` in `scripts/k3d-gpu` is the k3s minor of the Dockerfile's
  `K3S_TAG` (the test suite fails when they differ). Setting a default-on gate
  changes nothing, and Kubernetes removes a GA gate a few releases later
  (`ImageVolume` in 1.39, `DRAExtendedResource` in 1.40); an unknown gate name
  stops k3s from starting. On 1.37 neither `dra` nor `image-volume` passes a
  gate: both features add only their node label, and the capability is already
  on.
- **Labels** become `--k3s-node-label` arguments.
- **OpenVINO keys** go into ConfigMap `default/k3d-gpu-openvino-experimental`.
  It has one key per device, `CPU.json`, `GPU.json` and `NPU.json`, each holding
  a JSON object for OVMS `--plugin_config`; only the devices a feature sets get
  a key (today `NPU.json`, from `npu-turbo`). Keys are kept per device because
  OpenVINO rejects a property the target device does not support. Workloads opt
  in by reading the ConfigMap; k3d-gpu edits no workload.

## Ranking

The candidates come from four read-only sweeps run on 2026-10-16:

- kernel 6.12 to 7.3-rc7 driver knobs
- hidden silicon features across the Intel client, Xeon and accelerator families
- OpenVINO, OVMS, Level Zero and the Intel k8s plugins and DRA drivers
- Kubernetes 1.34 to 1.37 feature gates

Each candidate is scored 1 to 3 on three axes:

- **Value**: capacity, latency, energy, isolation or observability for agent pods.
- **Readiness**: 1 = preview or out of tree, 2 = alpha or beta, or a merged knob
  that needs a host step, 3 = a shipped API that is merely off by default.
- **Risk**: 3 = cluster-only and undone by recreating the cluster, 1 = a host
  change that can wedge a device or taint the kernel.

The score is their product. A candidate is **buildable now** when it fits the
registry contract with no new component, such as an exporter image or an NRI
plugin. The built features are the highest-scoring buildable candidates that
survived review. Ties are broken
by how many families the feature covers.

| # | Feature | Value | Ready | Risk | Score | Families | Built |
|---|---------|------:|------:|-----:|------:|----------|-------|
| 1 | `dra`: DRA alpha/beta gates | 3 | 2 | 3 | 18 | all | yes |
| 2 | `image-volume`: models as OCI image volumes | 3 | 2 | 3 | 18 | all | yes |
| 3 | `ov-kv-cache`: u8 KV cache | - | - | - | - | CPU, Intel GPU | killed: on by default upstream (see below) |
| 4 | `npu-turbo`: NPU turbo + HW-scheduler guidance | 2 | 3 | 3 | 18 | Intel NPU | yes |
| 5 | `ov-dynamic-quant`: dynamic int8 activations | - | - | - | - | CPU, Intel GPU | killed: on by default upstream (see below) |
| 6 | `scx`: sched_ext scheduler guidance | 2 | 2 | 2 | 8 | x86 on Linux 6.12+ | yes |
| 7 | `xe-sriov`: GPU VFs guidance + label | 3 | 2 | 1 | 6 | PTL, WCL, BMG, NVL, CRI | yes |
| 8 | `xe-force-probe`: bring up unbound GPUs | 3 | 2 | 1 | 6 | NVL-P, CRI, MTL/ARL/ADL/DG2 on xe | yes |
| 9 | dmem cgroup VRAM limits per pod | 3 | 1 | 2 | 6 | discrete xe | no: needs an NRI plugin; xe dmem reclaim only in 7.3-rc |
| 10 | OVMS idle servable unload (Preview) | 2 | 1 | 2 | 4 | all | next: needs an OVMS arg in the runtime, not plugin config |
| 11 | GenAI KV eviction / sparse-attention prefill | 2 | 2 | 1 | 4 | CPU, Intel GPU | no: lives in graph.pbtxt, accuracy loss |
| 12 | kubelet CPU-manager uncore/strict-reservation options | 2 | 2 | 1 | 4 | Xeon, hybrid client | no: static policy conflicts with the NRI balloons policy |
| 13 | GenericWorkload / gang scheduling | 2 | 1 | 2 | 4 | multi-pod agents | no: ranked on k3s 1.34, which does not know the gate; alpha in 1.35, beta default off in 1.37 |
| 14 | NPU busy-time / memory telemetry | 2 | 3 | 3 | 18 | Intel NPU | no: needs an exporter image |
| 15 | Level Zero experimental metrics / fabric / VF sysman | 2 | 1 | 2 | 4 | Intel GPU | no: needs an exporter image; counters need `perf_stream_paranoid` (host) |
| 16 | IAA zswap compressor, TPMI uncore ELC, resctrl, IFS | 2 | 2 | 1 | 4 | Xeon | no: host-wide changes; doc only |
| - | Thread Director IPC classes | - | - | - | - | hybrid | not merged upstream (absent from 7.3-rc7 `init/Kconfig`) |
| - | GNA | - | - | - | - | ICL..MTL | retired: no mainline driver, use the NPU |

## The built features

### 1. `dra`: Dynamic Resource Allocation alpha/beta gates

- **Gates**: `DRAPartitionableDevices`, `DRAConsumableCapacity`,
  `DRADeviceBindingConditions`, `DRAExtendedResource`, `ResourceHealthStatus`.
  They are set on the apiserver, scheduler, controller-manager and kubelet.
- **Status, from `pkg/features/kube_features.go` at each tag**:
  - In v1.34.1 (released 2025-08-27 as v1.34.0) all five are alpha, default off.
  - In v1.35.0 (2025-12-17) they are still alpha.
  - In v1.37.1 the first four are beta and on by default from 1.36 (released
    2026-04-22), and `ResourceHealthStatus` is beta from 1.36.
  - `DRAExtendedResource` is GA and locked to default in 1.37. It is still
    accepted as `=true`, and is due for removal in 1.40.
  - All five names exist in 1.34.1, 1.35.0 and 1.37.1. A gate name the binary
    does not know stops the k3s process, so this check matters.
  - On the shipped k3s 1.37 all five are on by default, so `dra` passes no gate
    and auto mode enables it (label only). Below 1.36 it passes all five and
    auto leaves it off.
  - Sources: https://github.com/kubernetes/kubernetes/blob/v1.34.1/pkg/features/kube_features.go
    and https://github.com/kubernetes/kubernetes/blob/v1.37.1/pkg/features/kube_features.go
- **Value**: a DRA driver can publish partitions of one device, shared devices
  with capacity accounting, binding conditions, and device health that shows up
  in pod status. Existing `resources.limits` requests can also be served by DRA.
- **Note**: the gates do nothing until a DRA driver runs. The Intel GPU DRA
  driver gpu-v0.12.1 publishes no shared counters or multi-allocation, so on
  Intel today the gain is health status and extended-resource mapping.
  https://github.com/intel/intel-resource-drivers-for-kubernetes/blob/gpu-v0.12.1/doc/gpu/README.md
- **Intel GPU DRA driver**: `dra` never switches the Intel GPU path. The driver
  ships in the image and runs only with `K3D_GPU_DRA=1` (README, "Intel GPU DRA
  mode"). It stays opt-in when the driver is present: measured on a UHD 770 it
  hands a claim pod the iGPU, but a second claim pod stays Pending where the
  plugin's sharing default runs both (EXPERIMENTS.md H16, H17).
- **Detect / prereq**: none. It applies on every host.

### 2. `image-volume`: OCI images as read-only model volumes

- **Gate**: `ImageVolume` on every component.
- **Status**: beta and default off in 1.33 to 1.34, beta and default on in
  1.35, GA and locked to default from 1.36 (`LockToDefault: true`, "remove in
  1.39" in v1.37.1 `kube_features.go`). The gate is passed only below 1.35; on
  the shipped 1.37 image `image-volume` adds its label and nothing else.
  https://github.com/kubernetes/kubernetes/blob/v1.37.1/pkg/features/kube_features.go
- **Runtime**: the pinned k3s v1.37.1+k3s1 bundles containerd v2.3.4-k3s1.
  containerd added image volume support in v2.1.0.
  https://github.com/k3s-io/k3s/blob/v1.37.1%2Bk3s1/go.mod and
  https://github.com/containerd/containerd/releases/tag/v2.1.0
- **Value**: a pod can mount a model (OpenVINO IR, GGUF, safetensors) shipped as
  an OCI artifact. containerd pulls and deduplicates it, with no init-container
  copy and no PVC.

### 3. `npu-turbo`: NPU turbo, plus HW-scheduler guidance

- **Property**: `NPU_TURBO=true` in `NPU.json`. OpenVINO parses a bool from
  `1/true/on/yes`.
  https://github.com/openvinotoolkit/openvino/blob/2026.4.0/src/inference/include/openvino/runtime/intel_npu/properties.hpp
  and https://github.com/openvinotoolkit/openvino/blob/2026.4.0/src/core/src/any.cpp
  The upstream header warns that turbo "is not recommended for sustainable
  workloads due to higher power consumption".
- **Label**: `k3d-gpu.io/npu-sched=HW|OS`, read from the NPU's `sched_mode`
  sysfs attribute. The attribute has existed since Linux 6.13. `ivpu_sysfs.c`
  prints `HW` or `OS`.
  https://github.com/torvalds/linux/blob/master/drivers/accel/ivpu/ivpu_sysfs.c
- **Host step**: printed only when the NPU runs the OS scheduler. It is
  `options intel_vpu sched_mode=1` plus a module reload, and the revert deletes
  the file. The `sched_mode` parameter (0444) accepts -1, 0 or 1. The kernel
  documents OS mode as supported only on 27XX to 50XX.
  https://github.com/torvalds/linux/blob/master/drivers/accel/ivpu/ivpu_drv.c
- **Prereq**: an Intel NPU accel node bound to `intel_vpu`.

### 4. `scx`: sched_ext guidance

- **Status**: sched_ext was merged in Linux 6.12 (2024-11-17). The schedulers
  ship out of tree in sched-ext/scx; the release used here is v1.1.4
  (2026-10-15).
  https://github.com/sched-ext/scx/releases/tag/v1.1.4
- **Label**: `k3d-gpu.io/sched-ext=<state>`, from `/sys/kernel/sched_ext/state`.
  https://github.com/torvalds/linux/blob/master/Documentation/scheduler/sched-ext.rst
- **Host step**: printed, never run. Install scx v1.1.4, then `sudo scx_lavd`.
  To revert, stop the process or send `SysRq-S`; the kernel then falls back to its
  default scheduler, as described in `sched-ext.rst`.
- **Prereq**: `/sys/kernel/sched_ext/state` exists. That needs Linux 6.12+ built
  with `CONFIG_SCHED_CLASS_EXT`. The 6.6 dev host reports this feature `absent`.

### 5. `xe-sriov`: GPU virtual functions

- **Status**: `xe.max_vfs` is a module parameter. A per-device configfs
  `sriov/max_vfs` is present from v6.19 (2026-02-08).
  https://github.com/torvalds/linux/blob/v6.19/drivers/gpu/drm/xe/xe_configfs.c
  The descriptors in `xe_pci.c` mark PF support (`has_sriov`) on PTL, BMG, NVL
  and CRI, among others.
  https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/xe/xe_pci.c
- **Label**: `k3d-gpu.io/xe-sriov-totalvfs=<N>`.
- **Host step**: printed. `echo N > /sys/bus/pci/devices/<bdf>/sriov_numvfs`,
  revert with `echo 0`, then recreate the cluster. Each VF is a render node, so
  the baked Intel GPU plugin advertises it as one more `gpu.intel.com/xe`.
- **Prereq**: an Intel GPU bound to `xe` whose `sriov_totalvfs` is non-zero.
  That also requires `CONFIG_PCI_IOV`, `xe.max_vfs` not 0, and VT-d on.

### 6. `xe-force-probe`: bring up a GPU the kernel did not bind

- **Status**: `force_probe` is declared with `module_param_named_unsafe`, so
  setting it taints the kernel.
  https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/xe/xe_module.c
  In 7.3-rc7 (2026-10-12) these descriptors still set `require_force_probe`:
  `nvlp_desc` (Nova Lake-P), `cri_desc` (Crescent Island), and the TGL, RKL,
  ADL, DG1, ATS-M, DG2 and MTL descriptors.
  https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/xe/xe_pci.c
  The syntax is `<pci-id>[,<pci-id>]`, with `!` to block a device.
  https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/xe/Kconfig
- **Host step**: printed. It writes a `modprobe.d` file with
  `options xe force_probe=<ids>` and `options i915 force_probe=!<ids>`, then
  reboots. The revert deletes the file and reboots.
- **Prereq**: an Intel display-class PCI device with no driver bound. NPUs are
  excluded.

## Killed in review

`ov-kv-cache` (`KV_CACHE_PRECISION=u8`) and `ov-dynamic-quant`
(`DYNAMIC_QUANTIZATION_GROUP_SIZE=32`) were built and then removed. Both
properties are already on by default in OpenVINO 2026.4.0, so neither is a
hidden or off-by-default feature:

- CPU on x86_64 defaults `kvCachePrecision` to `u8` and
  `fcDynamicQuantizationGroupSize` to `32`, so both CPU keys were no-ops.
  https://github.com/openvinotoolkit/openvino/blob/2026.4.0/src/plugins/intel_cpu/src/config.h
- GPU picks a compressed KV cache when the user sets none: `u4` for a
  PagedAttention model with 4-bit weights, `i8` for other PagedAttention models
  and for non-systolic GPUs. Forcing `u8` doubles the KV cache of a 4-bit
  PagedAttention model, the OVMS LLM case. GPU also enables dynamic
  quantization by default: group size 32 without XMX, per-token with XMX.
  https://github.com/openvinotoolkit/openvino/blob/2026.4.0/src/plugins/intel_gpu/src/runtime/execution_config.cpp

## What was checked on the dev host

The i9-13900T dev host was in use for live benchmarks, so every check was
read-only. Nothing was loaded, written or created. The host runs kernel 6.6, so
it reports:

- `dra` and `image-volume`: available.
- `npu-turbo`, `scx`, `xe-force-probe` and `xe-sriov`: absent, each with its
  reason.

`K3D_GPU_EXPERIMENTAL=list k3d-gpu detect` printed this result.

## Tests

- `tests/experimental_test.sh` builds a sim sysroot for each feature.
  - **Where the feature is available**, it runs `up` with k3d and kubectl
    stubbed. It checks the exact k3d create arguments, the ConfigMap that
    `kubectl apply` receives, and the printed host steps.
  - **Where it is not**, it checks the exact reason and that `k3d cluster
    create` never runs.
  - It also checks the framework rules: auto mode (mature, detected, no host
    change), `off`, explicit lists overriding auto, unknown names, `list`,
    whitespace, duplicates, and the doctor section. A copy of the script with
    `EXP_K3S_MINOR=34` checks the gates passed on an older k3s, and a check
    fails when `EXP_K3S_MINOR` and the Dockerfile's k3s minor differ.
  - It runs `list` against every `tests/fixtures/<platform>` sysroot and
    compares the result with `experimental.golden`.
- `tests/fixtures/capture.sh` now also records `sriov_totalvfs`, the NPU
  `sched_mode` and `/sys/kernel/sched_ext/state`, so future captures cover these
  features. Fixtures captured before this change do not have those files. That
  is why the 6.12+ fixtures read `scx: absent`.
