# Platforms: discrete Intel GPUs

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
