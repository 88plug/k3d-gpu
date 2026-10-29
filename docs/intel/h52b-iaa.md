# H52b finding: no safe host-setup path for IAA on crescent

Recorded 2026-10-28 PDT, before any H52b step. Not a measurement and not a ledger
amendment. The [H52b pre-registration](../../EXPERIMENTS.md#h50-h52-pre-registration-the-xeon-6741p-2026-10-20)
and its [amendment D1](../../EXPERIMENTS.md#h52-amendment-d1-the-fixes-and-h52b-2026-10-20)
stay as written.

## Finding

On crescent (Xeon 6741P), `iaa_crypto`'s kernel work queue claims all 256 WQ entries
of each IAA device (iax1, iax3, iax5). No user work queue fits beside it.
`k3d-gpu host-setup --apply` leaves those devices alone, with or without
`--with-kernel-wqs`. It prints a shrink suggestion with its revert and runs nothing
for IAA. This is the design that main ships, and it is in policy. It is not a missing
path, so this change adds no code.

Consequence: after `host-setup --apply` on this host, `host-setup` reports 0 IAA
devices configured and the node advertises no IAA resource. H52b's IAA clause needs
IAA allocatable > 0 at the count host-setup reports configuring. That clause cannot
hold on crescent while host-setup ships as it is. DSA and QAT do have an `--apply`
path (below). H58 clause (d) excuses an engine that H52b records as unusable on this
host. That record belongs in the H52b run section, not here.

## Host state read

Read 2026-10-29 05:17 UTC (2026-10-28 22:17 PDT). The read used `ssh crescent` and
plain `cat` of `/sys/bus/dsa/devices`, `/sys/module/zswap/parameters` and
`/proc/crypto`. It made no write.

```text
kernel 7.0.0-31-generic
dsa0 dsa2 dsa4   state=disabled  max_work_queues_size=256  pasid_enabled=1  (no WQ configured)
iax1 iax3 iax5   state=enabled   max_work_queues_size=256  pasid_enabled=1
  wq1.0 wq3.0 wq5.0  size=256 state=enabled type=kernel name=iaa_crypto driver=crypto
zswap enabled=N compressor=lzo
/proc/crypto deflate-iaa: refcnt 1, selftest "passed" (no self-test ran, see below)
/dev/iax, /dev/dsa: absent
accel-config: not on PATH and not in /usr/bin, /usr/local/bin, /usr/sbin, /usr/local/sbin, /bin, /sbin
```

The `selftest : passed` field in `/proc/crypto` is not evidence that `deflate-iaa`
works. No self-test ran. crescent's kernel config reads
`# CONFIG_CRYPTO_SELFTESTS is not set` (`/boot/config-7.0.0-31-generic`, read-only
`ssh crescent`, 2026-10-29 05:39 UTC; the #198 pre-check records the same). In such a
build, `crypto_alloc_test_larval` returns no test larval, and `__crypto_register_alg`
then sets `CRYPTO_ALG_TESTED` on the algorithm without a test
([crypto/algapi.c, v7.0](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/crypto/algapi.c?h=v7.0),
lines 278-280 and 347). `c_show` prints `passed` for any algorithm that carries that
flag ([crypto/proc.c, v7.0](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/crypto/proc.c?h=v7.0),
lines 49-51). The line shows only that `deflate-iaa` is registered. Its compression
is unverified (item 4 below).

This matches the H52 inventory and the [#198 pre-check](../../bench/eatflow/results/2026-10-21/H52b/roundtrip-iax1/README.md).

## What host-setup does on that state

The check used a fake root shaped like the read above: three DSA PFs disabled, three
IAA devices at 256 of 256, three QAT 4xxx PFs with 0 of 16 VFs, Ubuntu 26.04. On it,
`host-setup --apply --with-kernel-wqs` ran on main `6d5cab69` with `sudo` stubbed.
The plan had 8 commands, and the stub log shows what each did:

- `apt-get install -y accel-config`, from the 26.04 archive.
- `modprobe vfio-pci`, and `sriov_numvfs` set to 16 on each QAT PF.
- On dsa0, dsa2 and dsa4: 1 engine, 1 group and 1 shared user WQ (threshold 15,
  size 16), then the device and the WQ enabled.
- On iax1, iax3 and iax5: a `suggest:` line, its apply and its revert. No `iax`
  command reached the stub.

`tests/host_setup_test.sh` already asserts the IAA half: "crescent --apply runs
nothing (the IAA shrink is a suggestion only)".

## Why the shrink is not applied

1. **The rule.** The launcher never changes host configuration. `host-setup --apply`
   runs the commands `doctor` prints, after a y/N prompt ([intel.md](../intel.md)).
   Its idxd steps only add. Class `new` configures a device nobody configured.
   Class `kernel` (#162, opt-in `--with-kernel-wqs`) puts a user WQ in unclaimed WQ
   entries. It writes the kernel WQs back exactly as read.
2. **The shrink takes, it does not add.** It cuts `iaa_crypto`'s WQ from 256 to 240
   entries to free room for a 16-entry user WQ. That changes another owner's
   configuration of a kernel service. #203 made it a suggestion that nothing runs,
   `--with-kernel-wqs` included. #203 merged 2026-10-21, before any H52b step.
3. **The driver's own procedure needs a reload.** The kernel's IAA crypto document
   says how to change the WQ attributes of `iaa_crypto`. First disable the devices and
   WQs. Then remove and reinsert the `iaa_crypto` module, so that the new config is
   applied to `deflate-iaa`
   ([iaa-crypto.rst, v7.0](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/Documentation/driver-api/crypto/iaa/iaa-crypto.rst?h=v7.0),
   "IAA Default Configuration"). The shrink path does not reload the module. A reload
   unregisters `deflate-iaa` for the whole host. The v7.0 driver source re-adds a WQ to
   its table on every bind (`iaa_crypto_probe`), so a shrunk WQ may serve without a
   reload. No one has tested that.
4. **No evidence that compression survives.** The #198 pre-check proved only that an
   identical config round-trips byte for byte. It left `iaa_crypto` compression
   UNVERIFIED. No host has run a shrink.

Applying the shrink would first need its own pre-registered test. That test would run
the shrink and its revert on a host. It would show `deflate-iaa` compressing and
decompressing on the shrunk WQ, and the inventory restored after the revert. That is
outside this note.
