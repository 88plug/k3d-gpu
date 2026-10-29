# H52b: DSA, IAA and QAT after `host-setup --apply` on crescent (2026-10-29)

The one H52b hold (EXPERIMENTS.md "H52 amendment D2", item 7; runner per amendment D3), scored in
EXPERIMENTS.md "H52b run (Xeon 6741P, 2026-10-29)". Verdict: **REFUTED**.

## Reproduce

```text
date:            2026-10-29T18:32:44Z to 18:43:38Z (one hold; lock free, no wait: load1 1.94, no aiperf process)
hypothesis:      H52b
host:            crescent, Xeon 6741P (Granite Rapids, family 6 model 173, microcode 0xa000133),
                 48C/96T, 1 NUMA node, 503 GiB, Ubuntu 26.04.1, kernel 7.0.0-31-generic
k3d-gpu commit:  607dcf23 (PR #232 head: h52b.sh, h52b.py, their tests and amendment D3) on main
                 7718ccd4 (k3d-gpu 0.1.79; main after #210 merged). Every file the hold ran other
                 than h52b.sh and h52b.py is main's at 7718ccd4.
                 Checkout ~/k3dgpu-bench/k3d-gpu-h52b, clean and detached:
                 98ae66d3 -> 16cf9a53 (about 14:05Z) -> 607dcf23 (18:30Z, after the rebase onto main)
node image:      cryptoandcoffee/k3d-gpu:0.1.79, index digest
                 sha256:6e27ff9817bf9398edb20f479dceb5f08273a04a01971b37628a7b2791bdfcfe
                 (the commit's default; pulled before the hold), k3s v1.37.1+k3s1, containerd 2.3.4-k3s1
tools:           k3d v5.9.0, kubectl v1.35.2, helm v4.1.1 (~/k3dgpu-bench/bin); GNU timeout 9.7;
                 accel-config / libaccel-config1 4.1.9-2 (installed by the plan, left installed per D2 item 6)
plugins:         intel-gpu-plugin, intel-dsa-plugin, intel-qat-plugin 0.37.1 (digests in share/)
runtime:         enumeration pods vllm/vllm-openai-xpu:v0.28.0@sha256:4756b66a...;
                 k3d-gpu test pods busybox:1.37@sha256:a7dbc319...
serving load:    both SGLang pairs up throughout; serving CPU 2.43-2.63 CPU-s/s (mean 2.53),
                 load1 1.25-13.85 (the hold's own cluster), aiperf idle (hold/serving.jsonl)
```

Command, from the checkout:

```text
bench/eatflow/crescent/hold.sh H52b 1800 bash bench/eatflow/crescent/h52b.sh ~/k3dgpu-bench/results/H52b/hold1
```

## Files

- `steps.txt`, `rc.txt`: the step log and each step's exit status. `commit.txt`: the commit;
  `deploy-diff.txt`: what it adds to main 7718ccd4 (D3 item 2).
- `inventory-1.txt`, `inventory-3.txt`, `inventory-diff.txt`: the first and third inventory reads
  and their diff without the first line (empty: equal).
- `plan.txt`, `plan-gate.txt`: `host-setup --plan --with-kernel-wqs` and D2 item 2's gate (pass, 8
  commands). `apply.txt`, `apply-gate.txt`: `--apply --with-kernel-wqs`, answered y.
- `h52b/`: `h52.sh`'s records (doctor, up, node object, allocatable, `k3d-gpu test`, the
  enumeration pods, down, its own inventory read, the second). The kubeconfig is left out.
- `reverts.txt`, `revert-out.txt`: the seven reverts `--apply` printed, as run, all rc 0.
- Not scored, before and after: `dpkg-*.txt`, `lsmod-*.txt`, `needrestart-*.txt`,
  `crypto-*.txt` (`deflate-iaa`), `iaa-crypto-*.txt` (the `crypto` driver's WQs and settings).
- `hold/`: hold.sh's records (pre/post snapshots, serving samples, dmesg, rc 0, no wait, nothing
  reaped).
- `serving-proof-pre.txt`, `serving-proof-post.txt`: a chat completion through the `:8100` front
  door counted on each pair (`:30000`, `:30001`) before and after the hold.
- `cleanup-vfio.txt`, `lsmod-cleanup.txt`: after the hold, outside it (below).

## Reading

| Clause | Expected | Read | Holds |
|---|---|---|---|
| DSA | allocatable = the 3 user WQs the plan configures; test pod sees `/dev/dsa` | `dsa.intel.com/wq-user-shared: 3`; `dsa-test` listed `/dev/dsa/wq2.0` | yes |
| IAA | allocatable = the IAA WQs the plan configures (0 on crescent, D2 item 4) > 0; test pod sees `/dev/iax` | no IAA work queue configured, no `iaa.intel.com` resource, IAA plugin not scheduled, no test pod | **no** (disclosed in D2 item 4) |
| QAT | allocatable = the 48 VFs the plan creates; test pod sees `/dev/vfio` | 48 VFs created; `intel-qat-plugin` in CrashLoopBackOff (6 restarts), no `qat.intel.com` resource; `k3d-gpu test` stopped with "cluster advertises no qat resources" | **no** |
| Intel GPU | `gpu.intel.com/xe` 4, no i915 resource, `xmx=true`, `media-vcs=2`, `media-vecs=2`, ASPEED not counted; four enumeration pods, each one distinct card | `xe` **2** (`up` left out card1 0000:12:00.0 and card3 0000:7c:00.0 as busy, 16.1 and 16.2 GiB in use); no i915; `xmx=true`; `media-vcs=2`, `media-vecs=2`; `device-id.0300-e223.count=4`; two enumeration pods, renderD129 (0000:48:00.0) and renderD131 (0000:96:00.0), one B70 each | **no** |
| AMX | AMXTILE, AMXBF16, AMXINT8, AMXFP16; no AMXCOMPLEX | exactly those four (the AMX test pod did not run: `k3d-gpu test` stopped at QAT first) | yes |
| AVX-512 and VNNI | the 14 labels | all 14 | yes |
| DLB | not scored; not advertised | not advertised | (not scored) |
| Core types, NUMA | no hybrid or NUMA label, no balloons pod, CPU 96 | none, none, 96 | yes |
| Other vendors | no `nvidia.com`, `habana.ai` or `npu.intel.com` resource | none | yes |
| Host restored | third inventory = first, apart from its first line | `inventory-diff.txt` empty | yes |

## Observations (not scored)

- QAT, cause inferred, not observed (the plugin's log was not captured, and the cluster is gone):
  the plugin's scan writes each VF's address to `/sys/bus/pci/drivers/vfio-pci/bind` with no
  `driver_override` (intel-device-plugins v0.37.1 `cmd/qat_plugin/dpdkdrv/dpdkdrv.go`, `scan`),
  and returns the write's error. This kernel has no driver for 4xxx VFs: its config has VF
  drivers for DH895xCC, C3xxx and C62x only, and no QAT VF module was loaded (`lsmod-after.txt`).
  `vfio-pci`'s only alias is the `driver_override` one (`modinfo vfio-pci`), so that bind fails. Upstream's init script (`demo/qat-init.sh`) writes
  `driver_override` first; k3d-gpu's manifest has no init container and host-setup's plan writes
  only `sriov_numvfs`.
- GPU: the busy-card filter (`K3D_GPU_INTEL_CARDS=auto`, half the VRAM or more held by host
  processes) is newer than H52's commit. It left out the first card of each serving pair and kept
  the second (0000:48:00.0, 0000:96:00.0), which the pairs also use; the enumeration pods landed
  there. `up` printed `Accelerators schedulable: intel=4` before the restarted GPU plugin
  re-registered; the node then read 2.
- `--apply`'s package install: needrestart deferred every restart (`apply.txt`); its list is the
  same before and after.
- `iaa_crypto`: no IAA step ran. `deflate-iaa`, the `crypto` driver's WQs (wq1.0, wq3.0, wq5.0)
  and its settings read the same before and after. Compression through it was not exercised.
- dmesg (xe/drm filter): the same 200 lines before and after; no GPU error.
- Modules: the revert's `modprobe -r vfio-pci` left `vfio`, `vfio_iommu_type1` and `iommufd`
  loaded, which the apply's `modprobe vfio-pci` had pulled in. `inventory.sh` lists only
  `vfio_pci`, so the inventory comparison does not see them.

## After the hold, outside it

- 18:43:45-18:44:07Z: both pairs answered through `:8100` (`serving-proof-post.txt`).
- 18:47:49Z, under the bench lock: `modprobe -r vfio_iommu_type1`, `vfio`, `iommufd` (each rc 0).
  The loaded module set then equals `lsmod-before.txt`'s (`lsmod-cleanup.txt`).
- Before the hold, two read-only `host-setup --plan --with-kernel-wqs` dry runs (at 16cf9a53 and at
  607dcf23, XDG state in a temp dir) passed the gate with the same 8 commands; not saved.
