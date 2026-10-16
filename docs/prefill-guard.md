# Prefill guard (opt-in)

On an Intel hybrid CPU, P/E pinning gives inference its own cores, but not its own
power and thermal budget. P-cores and E-cores sit in one package, under one RAPL
limit and one thermal limit. A busy E-core neighbour heats that package, the
package hits PROCHOT, and every core clocks down, the P-cores serving inference
included. The wave-1 measurement on the i9-13900T test bed showed exactly this:
with disjoint cpusets, an E-core `stress-ng` neighbour slowed P-core prefill much
more than decode, and a register-only neighbour (no memory traffic) did nearly as
much damage as a cache-heavy one. So the coupling is power and clock, not cache.
The numbers and their status are in the falsification log of
[EXPERIMENTS.md](../EXPERIMENTS.md) (2026-10-16 row).

The prefill guard is the opt-in answer: it caps the maximum frequency of the
E-cores that run `k3d-gpu.io/cores=efficient` pods, so that work draws less of
the shared budget. It is off by default because it writes host cpufreq. Its
benefit is not measured yet; the test that decides it is pre-registered below as
hypothesis H5.

## Use

```bash
k3d-gpu doctor --perf                         # suggests it, with the evidence, when the package throttles
k3d-gpu down && K3D_GPU_PREFILL_GUARD=50% k3d-gpu up        # apply: cap at half the E-core max
k3d-gpu down && K3D_GPU_PREFILL_GUARD=2000000 k3d-gpu up    # or an absolute cap in kHz
k3d-gpu down && k3d-gpu up                    # revert: down writes the recorded values back
```

`K3D_GPU_PREFILL_GUARD` takes a frequency in kHz or `1%`-`100%` of the highest
E-core `cpuinfo_max_freq` (rounded down to a MHz). `up` refuses a cap below the
E-core `cpuinfo_min_freq`, a cap that is not below the E-core maximum (it would
cap nothing), any other form, and any host without both `cpu_core` and
`cpu_atom` CPUs. It refuses before it creates anything.

`doctor --perf` suggests the guard only on hybrid hosts whose
`package_throttle_count` is above zero, and prints the counters it read:

```text
==> Package power/thermal coupling on a hybrid CPU (prefill guard, opt-in)
  note: package throttled 22260897 times since boot (102219 s in total; P-core core_throttle_count 46996959)
        from:   /sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_{count,total_time_ms}
        apply:  k3d-gpu down && K3D_GPU_PREFILL_GUARD=50% k3d-gpu up
        revert: k3d-gpu down && k3d-gpu up
```

(This is the read-only output on the i9-13900T test bed, trimmed.) The counters
come from the kernel's `thermal_throttle` sysfs group:
`package_throttle_count` and `package_throttle_total_time_ms` exist when the CPU
has the PTS feature, and `package_power_limit_count` only when the kernel booted
with `int_pln_enable`, so doctor prints it only when it is there
([therm_throt.c](https://github.com/torvalds/linux/blob/v6.12/drivers/thermal/intel/therm_throt.c)).
The counters are cumulative since boot.

## How it works

`up` with the guard renders `share/nri-balloons-guard.yml` into
`$K3D_GPU_STATE_DIR/<cluster>/nri-balloons.yaml` and mounts it over the baked
`nri-balloons.yaml` in the server node's k3s manifests directory, so k3s deploys
the guarded policy from the first second, and re-deploys the same policy if k3s
restarts. Before that, it records every online CPU's `scaling_max_freq` in
`$K3D_GPU_STATE_DIR/<cluster>/scaling_max_freq` (once: an existing record holds
the pre-guard values and is kept). `down` feeds that record through
`docker exec` into the privileged server node, whose `/sys` is the host's, and
writes each value back before it deletes the cluster. If that fails, `down`
keeps the record and prints the same restore as a `sudo tee` loop to run by hand.

`scaling_max_freq` reads the resolved limit, the lowest of every frequency QoS
request on the CPU, while a write sets only the user request
([cpufreq.c](https://github.com/torvalds/linux/blob/v6.6/drivers/cpufreq/cpufreq.c),
`refresh_frequency_limits`/`store_scaling_max_freq`). ACPI processor passive
cooling adds its own QoS request
([processor_thermal.c](https://github.com/torvalds/linux/blob/v6.6/drivers/acpi/processor_thermal.c)).
A record taken while that cooling is active would hold the cooled value, and
`down` would write it back as the user cap, which stays after the cooling ends.
So `up` refuses to record while any `Processor` cooling device in
`/sys/class/thermal` has a nonzero `cur_state`; retry when the CPU is cooler.

The guard policy is the default policy plus two CPU classes. The guard file is
rendered with `helm template` of the same pinned chart
(nri-resource-policy-balloons v0.14.0) by `scripts/render-nri-balloons.sh`, from
the values in its header. The same script re-renders the default
`share/nri-balloons.yml` byte for byte, so the default deployment does not change
(`scripts/render-nri-balloons.sh --check`). `tests/prefill_guard_test.sh` checks
that every other object (CRDs, RBAC, DaemonSet) is identical between the two
files and that the guard policy is the default policy plus the class lines.

```yaml
balloonTypes:
- name: efficient
  cpuClass: prefill-guard          # only this balloon type gets a class
cpuClasses:
- name: default                    # implicit class for every other balloon and for idle CPUs
  maxFreq: <host max>kHz           # highest cpuinfo_max_freq on the host
- name: prefill-guard
  maxFreq: <cap>kHz
```

What nri-plugins v0.14.0 does with this, read from its source at the tag:

| Fact | Source (containers/nri-plugins v0.14.0) |
|---|---|
| `cpuClasses` (list) is the current form; `control.cpu.classes` (map, integer kHz) is deprecated. | [balloons.md, CPU Tuning](https://github.com/containers/nri-plugins/blob/v0.14.0/docs/resource-policy/policy/balloons.md#cpu-tuning) |
| `minFreq`/`maxFreq`/`uncoreMinFreq`/`uncoreMaxFreq` are strings: `"3.2GHz"`, `"2900MHz"`, `"2900000kHz"`, a plain kHz number, or `min`/`base`/`turbo`. | [pkg/apis/config/v1alpha1/resmgr/policy/frequency.go](https://github.com/containers/nri-plugins/blob/v0.14.0/pkg/apis/config/v1alpha1/resmgr/policy/frequency.go) |
| `min`/`base`/`turbo` resolve from the first online CPU only. On the i9-13900T cpu0 reports 5100 MHz while CPUs 8-11 report 5300 MHz, so `maxFreq: turbo` would cap the favoured cores. The guard writes numbers. | [pkg/resmgr/cpuclass/internal/cpufreq/platform.go](https://github.com/containers/nri-plugins/blob/v0.14.0/pkg/resmgr/cpuclass/internal/cpufreq/platform.go) |
| The cpufreq writer sets `scaling_min_freq`, `scaling_max_freq` and `scaling_governor` only. `energyPerformancePreference` is accepted by the CRD but never written, so the guard does not use EPP. | [pkg/resmgr/cpuclass/internal/cpufreq/sysfs.go](https://github.com/containers/nri-plugins/blob/v0.14.0/pkg/resmgr/cpuclass/internal/cpufreq/sysfs.go) |
| An unset `cpuClass`, and idle CPUs with no `idleCPUClass`, use a class named `default` when one exists. That is what un-caps a CPU that leaves the `efficient` balloon. | [cmd/plugins/balloons/policy/balloons-policy.go](https://github.com/containers/nri-plugins/blob/v0.14.0/cmd/plugins/balloons/policy/balloons-policy.go) (`resolveCpuClassName`) |
| Writes go to `<host-root>/sys/devices/system/cpu/cpuN/cpufreq/...`; the chart runs the plugin with `--host-root /host` and mounts the node's `/sys` at `/host/sys`. | [goresctrl v0.13.0 pkg/utils/sysfs.go](https://github.com/intel/goresctrl/blob/v0.13.0/pkg/utils/sysfs.go), [pkg/resmgr/rdt.go](https://github.com/containers/nri-plugins/blob/v0.14.0/pkg/resmgr/rdt.go) |

And from the kernel: a value written to `scaling_max_freq` becomes a frequency
QoS request, and `intel_pstate` clamps the policy to each CPU's own maximum
([cpufreq.c `store_one`](https://github.com/torvalds/linux/blob/v6.12/drivers/cpufreq/cpufreq.c),
[intel_pstate.c `intel_pstate_verify_cpu_policy`](https://github.com/torvalds/linux/blob/v6.12/drivers/cpufreq/intel_pstate.c)).
So one host-wide maximum in the `default` class restores every CPU to its own
ceiling, and a cap above an LP E-core's maximum leaves that core alone.

### What it does not touch, and why

- **The `shared` balloon.** It shares every idle CPU, P-cores included, so a cap
  there would cap unlabelled inference too. Label the neighbour `efficient`.
- **EPP.** v0.14.0 does not write it (above). A `freqGovernor` change would not
  set EPP either: with HWP active, `powersave` passes the sysfs EPP through
  ([intel_pstate docs](https://docs.kernel.org/admin-guide/pm/intel_pstate.html#energy-vs-performance-hints)).
- **Uncore.** On client parts the ring and LLC clock is one domain per package
  (`intel_uncore_frequency/package_00_die_00` on the test bed), shared with the
  P-cores. Capping it would slow inference itself
  ([uncore frequency docs](https://docs.kernel.org/admin-guide/pm/intel_uncore_frequency_scaling.html)).
- **Other CPUs, except for one case.** While the guard runs, the `default` class
  sets every CPU outside the `efficient` balloon to its own maximum. If you had
  capped P-cores by hand before `up`, that cap is lifted while the cluster runs;
  `down` writes your value back from the record.

## Intel families

| Family | `cpu_core` + `cpu_atom` | Guard |
|---|---|---|
| Alder Lake, Raptor Lake (12th-14th gen Core) | yes | applies |
| Meteor Lake (Core Ultra 100), Lunar Lake (200V), Panther Lake (300) | yes; LP E-cores sit inside `cpu_atom` | applies; a cap above an LP E-core's own maximum leaves it unchanged |
| Arrow Lake-S/H/U (Core Ultra 200S/H/U) | yes; on -H the LP E-cores are a separate `cpu_lowpower` PMU | applies to `cpu_atom`; `cpu_lowpower` CPUs are only ever reset to their own maximum |
| Alder Lake-N, Twin Lake (E-cores only) | no | refused: no P-cores to protect |
| Xeon (Sapphire/Emerald Rapids, Xeon 6 P-core and E-core) | no: one core type per SKU | refused, see below |

Sources: core-type PMUs and the LP E-core island per family are in
[intel-capability-map.md](intel-capability-map.md) (rows "Hybrid P/E split" and
"LP E-core island", which cite `arch/x86/events/intel/core.c`).

### Why Xeon is different

A Xeon has no E-cores next to P-cores to cap, so the guard does not apply, but
the coupling does not go away. The neighbour shares a socket's power budget and,
with Sub-NUMA Clustering off, its LLC and memory controllers. The Xeon tools are
different:

- **Priority by CPU, not by core type.** Intel Speed Select Core Power and Turbo
  Frequency give chosen cores priority for the socket's frequency budget
  ([intel-speed-select](https://docs.kernel.org/admin-guide/pm/intel-speed-select.html)).
  The same balloons version drives this per CPU class as Priority Core Turbo
  (`pctPriority: high|low`, or `sstClosID` when the BIOS set the CLOSes)
  ([balloons.md, PCT](https://github.com/containers/nri-plugins/blob/v0.14.0/docs/resource-policy/policy/balloons.md#priority-core-turbo-pct)).
  It is ignored with a warning on parts without SST.
- **Uncore per die.** Xeon exposes uncore frequency per die or per TPMI domain,
  so a cap there is local, unlike the one client package domain
  ([uncore frequency docs](https://docs.kernel.org/admin-guide/pm/intel_uncore_frequency_scaling.html)).
- **SNC.** With Sub-NUMA Clustering each socket appears as several NUMA nodes,
  each with its own LLC slice and memory controllers, so cache and bandwidth
  neighbours are separated by NUMA placement before frequency is the question.
  Xeon balloons are a separate gap (G11 in the capability map).

## Pre-registered A/B test (H5)

Written before any guarded run. Do not change it after the first run; record
deviations in the falsification log.

**Hypothesis H5.** On the hybrid test bed, capping the E-cores that run an
E-core-pinned CPU neighbour cuts the neighbour's P-core prefill penalty by at
least half at one or more cap levels.

**Stage 1: the mechanism, host level, no cluster.** The guard's only effect is
`scaling_max_freq` on E-cores, so stage 1 writes that value directly with the
wave-1 harness and the wave-1 placement. It needs no cluster and can run in the
wave-1 window.

- Inference: the wave-1 server, model and prompt (record their digests and the
  prompt's sha256), pinned to CPUs 0-14. Prefill latency is `ttft_s` from
  `bench/llm_load.py` (branch `claude/report-bench`) with `--max-tokens 16
  --concurrency 1 --requests 3 --warmup 1`; decode is `decode_tokens_per_s` of
  one extra `--max-tokens 128` request per run.
- Neighbour: `taskset -c 17-31 stress-ng --cpu 15 --cpu-method <m> --timeout 150s
  --metrics-brief`, started 15 s before the first request. Its bogo-ops/s (real
  time) is the neighbour throughput. Primary method `matrixprod` (largest wave-1
  effect), secondary `loop` (register-only: power and clock coupling alone).
- Agent tool latency proxy: wall time of one fixed single-threaded job on an
  E-core inside the capped set but outside the neighbour set, run once per run
  after the inference requests:
  `taskset -c 16 python3 -c 'import json; d=[{"k": i, "v": str(i) * 8} for i in range(200000)]; [json.loads(json.dumps(d)) for _ in range(3)]'`.
- Arms, caps written to CPUs 16-31 with
  `echo <kHz> | sudo tee /sys/devices/system/cpu/cpu{16..31}/cpufreq/scaling_max_freq`
  and restored to the recorded values after every run:

| Arm | Neighbour | E-core cap (i9-13900T: E max 3900 MHz, min 800 MHz) |
|---|---|---|
| Q | none | none |
| N0 | yes | none (guard off) |
| C75 | yes | `75%` = 2925000 kHz |
| C50 | yes | `50%` = 1950000 kHz (the doctor suggestion) |
| CMIN | yes | E-core `cpuinfo_min_freq` = 800000 kHz |

- Order: five rounds per neighbour method, each round runs every arm once, rotated
  (a Latin square, so every arm sits in every position once):

```text
round 1: Q    N0   C75  C50  CMIN
round 2: N0   C75  C50  CMIN Q
round 3: C75  C50  CMIN Q    N0
round 4: C50  CMIN Q    N0   C75
round 5: CMIN Q    N0   C75  C50
```

- Between runs: neighbour stopped, caps restored, 60 s idle. Record per run the
  package temperature at the start (`x86_pkg_temp` thermal zone), the
  `package_throttle_count` delta, RAPL package energy over the run, and a read
  of `scaling_max_freq` on CPUs 16-31 during the run (proves the cap was in
  force).
- Every run carries the EXPERIMENTS.md Reproduce header; n = 5 per arm per method.

**Statistics and verdict.** Penalty of an arm = (its median `ttft_s` / the Q
median `ttft_s`) - 1, from medians over all its requests; p90 penalty the same
with p90s.

- SUPPORTED: for one or more cap levels, the median penalty is at most half the
  N0 median penalty, and the p90 penalty is below the N0 p90 penalty.
- REFUTED: no cap level reaches half.
- INCONCLUSIVE: the N0 median penalty is below the run-to-run spread of Q (the
  neighbour did not reproduce the wave-1 effect), or the Q median of rounds 1-2
  and of rounds 4-5 differ by more than the Q spread (drift), or a cap read
  during a run did not show the cap.
- Always reported, pass or fail, per cap level: neighbour throughput loss
  (1 - bogo-ops/s / N0 bogo-ops/s), tool proxy latency ratio to N0, decode
  tok/s ratio to N0, package power and throttle-count delta.
- If SUPPORTED, the level with the smallest neighbour throughput loss among the
  passing levels replaces `50%` as the doctor suggestion, in its own commit.

**Stage 2: the delivery, through k3d-gpu.** Only after wave-1 is finished (it
creates clusters). It checks that the NRI path writes what stage 1 measured.

1. Record `cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_max_freq`.
2. `K3D_GPU_PREFILL_GUARD=<level from stage 1> k3d-gpu up`, then an inference
   server pod labelled `k3d-gpu.io/cores: performance` and the neighbour:
   `bench/noisy.sh start --cores efficient --workers 15 --cpu 15 --dry-run | sed 's/--timeout 0/--timeout 150s/' | kubectl apply -f -`
   (its logs then carry the `--metrics-brief` line once it completes).
3. While the neighbour runs: the CPUs in the neighbour's `Cpus_allowed_list`
   read the cap, the inference pod's CPUs read their own maximum. A CPU that
   does not is a failure of the write path, not of H5.
4. Prefill penalty against a guard-off cluster, ABAB over five recreate pairs
   (`down`/`up` between arms), measured with `bench/llm_load.py` against the
   pod. Pass: the penalty reduction lies within stage 1's range for that level.
5. `k3d-gpu down`, then the step 1 read again: it must match line for line.

Not verified yet: the NRI write path (step 3) and the `down` restore through the
node (step 5) have only run against stubs in `tests/prefill_guard_test.sh`.
