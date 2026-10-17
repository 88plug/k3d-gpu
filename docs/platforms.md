# Platforms

One row per Intel family that has a sim fixture under `tests/fixtures/`. Each row is
generated from that fixture's goldens, so it states what `k3d-gpu` does on the host
with no flags set:

- **Detected as**: the `k3d-gpu detect` lines (`detect.golden`).
- **Auto config**: the device plugins and CPU policy `k3d-gpu up` installs
  (`up.golden`). `K3D_GPU_*` flags only override these.
- **Predicted behaviour**: what the plan predicts (`plan.json`). Nothing here is a
  measurement. Every fixture plans `unmeasured` until `k3d-gpu prove` runs on a real
  box, and measured numbers live only in [EXPERIMENTS.md](../EXPERIMENTS.md).
- **Source**: the fixture's `SOURCES` file, which names the capture or datasheet
  every sysfs value comes from.

| Family | Fixture | Detected as | Auto config | Predicted behaviour | Source |
|---|---|---|---|---|---|
| Alder Lake-N (N100, E-cores only) | `alderlake-n` | `gpu.intel.com/i915` | Intel GPU plugin, shared iGPU (`-shared-dev-num=8`), NRI balloons | predicted by the plan: iGPU and CPU usable, unmeasured | [SOURCES](../tests/fixtures/alderlake-n/SOURCES) |
| Raptor Lake-S + NVIDIA (dev host) | `raptorlake-nvidia` | `nvidia.com/gpu`, `gpu.intel.com/i915`, hybrid | NVIDIA plugin, Intel GPU plugin (shared iGPU) | predicted by the plan matrix: both cells grey until a `k3d-gpu report` run is recorded | [SOURCES](../tests/fixtures/raptorlake-nvidia/SOURCES) |
| Raptor Lake-S, stale GuC | `raptorlake-stale-guc` | as above | as above | predicted by the plan: iGPU refused until linux-firmware ships the GuC i915 wants; doctor prints the fix | [SOURCES](../tests/fixtures/raptorlake-stale-guc/SOURCES) |
| Meteor Lake-H | `meteorlake` | hybrid (CPU topology only) | none beyond the hybrid labels | predicted by the plan: CPU usable, unmeasured | [SOURCES](../tests/fixtures/meteorlake/SOURCES) |
| Arrow Lake-H | `arrowlake-h` | `gpu.intel.com/i915`, `npu.intel.com/accel`, hybrid | Intel GPU plugin (shared iGPU), Intel NPU plugin | predicted by the plan: iGPU, NPU and CPU usable, unmeasured | [SOURCES](../tests/fixtures/arrowlake-h/SOURCES) |
| Arrow Lake-S | `arrowlake-s` | `gpu.intel.com/i915`, `npu.intel.com/accel`, hybrid | Intel GPU plugin (shared iGPU), Intel NPU plugin | predicted by the plan: iGPU, NPU and CPU usable, unmeasured | [SOURCES](../tests/fixtures/arrowlake-s/SOURCES) |
| Panther Lake | `pantherlake` | `gpu.intel.com/xe`, `npu.intel.com/accel`, `iaa.intel.com/*`, hybrid | Intel GPU plugin (shared iGPU), NPU plugin, IAA plugin | predicted by the plan: iGPU on xe, NPU and CPU usable, unmeasured | [SOURCES](../tests/fixtures/pantherlake/SOURCES) |
| Arc B580 (Battlemage) | `battlemage-b580` | `gpu.intel.com/xe` | Intel GPU plugin (dGPU, not shared), NRI balloons | predicted by the plan: dGPU usable, unmeasured | [SOURCES](../tests/fixtures/battlemage-b580/SOURCES) |
| Xeon Sapphire Rapids + accelerators | `xeon-spr-accel` | DSA, IAA, QAT, AMX, NUMA | DSA, IAA and QAT plugins, AMX and NUMA labels | predicted by the plan: CPU usable, unmeasured | [SOURCES](../tests/fixtures/xeon-spr-accel/SOURCES) |
| Xeon CPU Max (HBM, SNC4) | `xeon-max-hbm` | AMX, NUMA (HBM nodes) | AMX and NUMA labels | predicted by the plan: CPU usable, unmeasured | [SOURCES](../tests/fixtures/xeon-max-hbm/SOURCES) |
| Xeon 6 P-core (Granite Rapids, SNC3) | `xeon-gnr` | DSA, IAA, AMX, NUMA | DSA and IAA plugins, AMX and NUMA labels | predicted by the plan: CPU usable, unmeasured | [SOURCES](../tests/fixtures/xeon-gnr/SOURCES) |
| Xeon 6 SoC (Granite Rapids-D) | `xeon-gnrd` | DSA, AMX | DSA plugin, NRI balloons | predicted by the plan: CPU usable, unmeasured | [SOURCES](../tests/fixtures/xeon-gnrd/SOURCES) |
| Xeon 6 E-core (Sierra Forest) | `xeon-srf` | DSA, IAA (no AMX) | DSA and IAA plugins, NRI balloons | predicted by the plan: CPU usable, unmeasured | [SOURCES](../tests/fixtures/xeon-srf/SOURCES) |
| Xeon + QAT 420xx | `qat-420xx` | QAT, AMX | QAT plugin, NRI balloons | predicted by the plan: CPU usable, unmeasured | [SOURCES](../tests/fixtures/qat-420xx/SOURCES) |
| Gaudi 3 (HLS) | `gaudi3-hls` | `habana.ai/gaudi`, AMX | Habana device plugin, NRI balloons | predicted by the plan: HPU usable, unmeasured | [SOURCES](../tests/fixtures/gaudi3-hls/SOURCES) |
| Gaudi 3, no driver | `gaudi3-no-driver` | AMX; Gaudi skipped with a warning | NRI balloons; no Gaudi plugin | predicted by the plan: HPU refused until `habanalabs` loads; doctor prints the fix | [SOURCES](../tests/fixtures/gaudi3-no-driver/SOURCES) |

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
