# H52b pre-check: accel-config save-config / load-config round-trip on a live iaa_crypto device

Reproduce: on crescent (Xeon 6741P, Ubuntu 26.04.1, kernel 7.0.0-31), k3d-gpu commit
`35b419ce` (deployed from main), under `~/k3dgpu-bench.lock`:

    bash bench/eatflow/crescent/idxd-roundtrip.sh iax1

accel-config: Ubuntu package `accel-config` / `libaccel-config1` 4.1.9-2 (`apt-get download` and
`dpkg-deb -x` under `~/k3dgpu-bench/opt`, no system install; sha256 in `debs.sha256`). The
build prints `4.1.8+` for `--version` (`accel-config-version.txt`). Latest upstream release is
v4.1.9, the version k3d-gpu's steps cite. zswap compressor `lzo`, zswap disabled.

This is not an H52b measurement. It is the check the coordinator asked for before using
`host-setup --with-kernel-wqs`: does the snapshot/revert path restore an enabled IAA device whose
kernel work queue serves `iaa_crypto`, byte for byte?

What ran (the revert half of `accel_enable_steps` class `kernel`, without adding a user WQ):
`save-config` of every device, keep `iax1`'s entry (`idxd-iax1.conf`), `disable-device iax1`,
`load-config`, `enable-device iax1`, `enable-wq iax1/wq1.0`.

Result (`result.txt`, `diff-*.txt`):

- `accel-config list -i` (2114 lines), 369 readable sysfs attributes of `iax1` and its WQs, groups
  and engines (wq driver binding `crypto` included), the `/proc/crypto` `deflate-iaa` entry and
  `lsmod` for `idxd` / `iaa_crypto`: **identical before and after** (every diff empty).
- The device was disabled for 0.053 s.
- dmesg added one line: `idxd iax1: Active wq 0 on disable wq1.0.`
- `iax1`, `iax3`, `iax5` and their kernel WQs `wq1.0`, `wq3.0`, `wq5.0` were enabled afterwards.

Not shown: that `iaa_crypto` still serves compression requests. Nothing in the kernel uses
`deflate-iaa` on this host (zswap off), and `CONFIG_CRYPTO_SELFTESTS` is not set, so only its
registration and the WQ's rebinding to the `crypto` driver are evidenced. **UNVERIFIED.**

Also found by `host-setup --plan --with-kernel-wqs` on this host: all three IAA devices are left
alone, because `iaa_crypto`'s kernel WQ claims all 256 of each device's WQ entries.
