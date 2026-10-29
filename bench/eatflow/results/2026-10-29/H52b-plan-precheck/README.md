# H52b pre-check: what `host-setup --plan --with-kernel-wqs` plans on crescent

Not an H52b step and not a measurement. A read-only dry run of k3d-gpu's host-setup on crescent
at main `6d5cab69`, taken 2026-10-29T06:24:52Z for H52 amendment D2 (EXPERIMENTS.md). Nothing
was changed: `--plan` prints the plan and exits, and the XDG state and cache went to a temp dir
that was removed. The sysfs, dpkg and df reads after it are read-only.

`plan.txt` holds the full output. In short:

- The Intel GPU userspace checks all pass, so the plan installs no GPU package.
- The plan has 8 commands: the Ubuntu 26.04 `accel-config` package, `modprobe vfio-pci`, one
  16-entry shared user work queue on each of dsa0, dsa2 and dsa4, and 16 VFs on each of the three
  QAT PFs. Each step after the package install prints its revert.
- IAA: iax1, iax3 and iax5 each have one enabled kernel work queue (`iaa_crypto`) holding all 256
  of the device's 256 work-queue entries. host-setup prints a shrink as a suggestion only, with
  or without `--with-kernel-wqs`, so the plan configures no IAA work queue.
- `accel-config` is not installed (`dpkg-query`), and the plan's package install has no revert.
- `/` was 43% used. The removal of `~/models/Qwen3.8-2.4T-A95B-FP8` (PR #219) was in progress;
  `/` read 95% on 2026-10-29T05:25Z and 68% at 06:15Z.
