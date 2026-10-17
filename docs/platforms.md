# Platforms

What `k3d-gpu` detects on each Intel platform, the configuration it picks with no
flags, and what that configuration is predicted to do. Every row is backed by a
sim fixture under `tests/fixtures/<platform>` whose transcripts are checked by
`tests/sim_matrix_test.sh`. "Predicted by" means read from the named source, not
measured on the hardware.

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

| Family | Fixture | Detected as | Auto config | Predicted behaviour | Source |
|---|---|---|---|---|---|
| Meteor Lake / Arrow Lake-H | `arrowlake-h` | `0x7d1d`, 37xx, kernel 6.3+ (CPU model tells MTL from ARL-H) | plugin + labels, `npu.gen=37xx` | kernel 6.14 loads `intel/vpu/vpu_37xx_v0.0.bin`, predicted by `fw_names[]` in ivpu_fw.c | [ivpu_fw.c v6.15](https://github.com/torvalds/linux/blob/v6.15/drivers/accel/ivpu/ivpu_fw.c) |
| Arrow Lake | `arrowlake-s` | `0xad1d`, 37xx, kernel 6.6+ | plugin + labels, `npu.gen=37xx` | kernel 6.12 loads `intel/vpu/vpu_37xx_v0.0.bin` (a linux-firmware link to `_v1`), predicted by ivpu_fw.c | [ivpu_fw.c v6.12](https://github.com/torvalds/linux/blob/v6.12/drivers/accel/ivpu/ivpu_fw.c) |
| Lunar Lake | `lunarlake` | `0x643e`, 40xx, kernel 6.6+ | plugin + labels, `npu.gen=40xx` | kernel 6.8 tries, in order, `vpu_40xx.bin` then `intel/vpu/vpu_40xx_v0.0.bin` and loads the first found, never `_v1`, predicted by ivpu_fw.c | [ivpu_fw.c v6.8](https://github.com/torvalds/linux/blob/v6.8/drivers/accel/ivpu/ivpu_fw.c) |
| Panther Lake | `pantherlake` | `0xb03e`, 50xx, kernel 6.13+ | plugin + labels, `npu.gen=50xx` | kernel 6.18 loads `intel/vpu/vpu_50xx_v1.bin`, predicted by ivpu_fw.c | [ivpu_fw.c v6.16](https://github.com/torvalds/linux/blob/v6.16/drivers/accel/ivpu/ivpu_fw.c) |
| Panther Lake on 6.13-6.15 | `pantherlake-k6.14` | `0xb03e`, 50xx | plugin + labels; doctor suggests (never applies) a `vpu_50xx_v0.0.bin` link to intel-fw-npu's `_v1`, with its revert | the kernel loads linux-firmware's `vpu_50xx_v0.0.bin`, not intel-fw-npu's `_v1`, predicted by ivpu_fw.c and intel-fw-npu's file list | [ivpu_fw.c v6.15](https://github.com/torvalds/linux/blob/v6.15/drivers/accel/ivpu/ivpu_fw.c), [linux-npu-driver firmware/bin](https://github.com/intel/linux-npu-driver/tree/v1.38.0/firmware/bin) |
| Wildcat Lake | `wildcatlake` | `0xfd3e`, 50xx, kernel 6.17+ | plugin + labels, `npu.gen=50xx` | loads `intel/vpu/vpu_50xx_v1.bin`, predicted by ivpu_fw.c | [ivpu_fw.c v6.17](https://github.com/torvalds/linux/blob/v6.17/drivers/accel/ivpu/ivpu_fw.c), [ivpu_drv.h v6.17](https://github.com/torvalds/linux/blob/v6.17/drivers/accel/ivpu/ivpu_drv.h) |
| Nova Lake | `novalake` | `0xd71d`, 60xx, kernel 6.19+; not detected while firmware is missing | none (no accel node); doctor says no 60xx firmware is published | intel_vpu's probe fails for want of `vpu_60xx_v1.bin`, which linux-firmware 20260916 and linux-npu-driver v1.38.0 do not ship, predicted by their file lists | [WHENCE](https://gitlab.com/kernel-firmware/linux-firmware/-/blob/20260916/WHENCE), [ivpu_fw.c v6.19](https://github.com/torvalds/linux/blob/v6.19/drivers/accel/ivpu/ivpu_fw.c) |

Doctor explains an NPU PCI function (class `0x1200`) with no driver by the first
cause that holds: a kernel older than the id's first tag; `intel_vpu` not loaded
(it suggests `sudo modprobe intel_vpu`, revert `sudo modprobe -r intel_vpu`); no
firmware under the names that kernel requests. Host changes are suggested only.
