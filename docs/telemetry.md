# Per-engine telemetry

Opt-in Prometheus metrics for every compute engine a k3d-gpu node exposes: CPU cores
split by P-core and E-core, CPU package power from RAPL, the Intel GPU engines and the
NVIDIA GPUs. The manifests live in `share/telemetry/`. They are **not** baked into the
node image; the k3s manifests directory never sees them.

```bash
kubectl apply -f share/telemetry/                 # namespace, exporters, Prometheus
kubectl apply -f share/telemetry/optional/        # xpumd, for Arc Pro B-series dGPUs only
kubectl -n k3d-gpu-telemetry port-forward svc/prometheus 9090
kubectl delete namespace k3d-gpu-telemetry        # remove everything
```

The AUR package installs the same files under `/usr/share/k3d-gpu/telemetry/`; use that
path instead of `share/telemetry/` outside a repo checkout.

`dcgm-exporter.yml` schedules only onto nodes without the `nvidia.com/gpu.present=false`
label, which the launcher sets when NVIDIA is not active. `intel-gpu-exporter.yml` has no
such gate: per the drm-exporter README the pod exits on a node with no GPU, so on a
host without an Intel GPU it goes into CrashLoopBackOff. Skip that file there, or
delete it afterwards with `kubectl -n k3d-gpu-telemetry delete ds/drm-exporter`.

A future `k3d-gpu telemetry up|down` wraps these commands. `up` should apply
`intel-gpu-exporter.yml` only when an Intel GPU is present, and `optional/intel-xpumd.yml`
only for a supported Intel dGPU. Everything in the stack is pinned:

| File | Component | Version (image) | Port | Runs as |
| ---- | --------- | --------------- | ---- | ------- |
| `00-namespace.yml` | namespace `k3d-gpu-telemetry` | - | - | PSA `privileged` |
| `node-exporter.yml` | Prometheus node_exporter | `quay.io/prometheus/node-exporter:v1.12.1` | 9100 (hostNetwork) | root, no capabilities |
| `intel-gpu-exporter.yml` | home-operations drm-exporter | chart 0.3.4, image `ghcr.io/home-operations/drm-exporter@sha256:9b54…5e60` | 8081 | privileged |
| `dcgm-exporter.yml` | NVIDIA dcgm-exporter | chart 4.8.4, `nvcr.io/nvidia/k8s/dcgm-exporter:4.6.1-4.8.4-distroless` | 9400 | root + `SYS_ADMIN`, RuntimeClass `nvidia` |
| `prometheus.yml` | Prometheus server and recording rules | `quay.io/prometheus/prometheus:v3.15.0` | 9090 | uid 65534 |
| `optional/intel-xpumd.yml` | Intel XPU Manager daemon | chart 2.3.0, `ghcr.io/intel/xpumanager/xpumd:v2.3.0` | 8080 | privileged |

Rendered charts carry their exact `helm template` values in their file header. Re-render
them the same way to bump a version.

## Design

- **One namespace.** `k3d-gpu-telemetry` holds the stack, so removing it is a single
  delete. The GPU exporters and node_exporter read host devices, sysfs and `/proc`; the
  namespace is labelled `pod-security.kubernetes.io/enforce: privileged` to make that
  explicit.
- **No GPU resource is consumed.** The Intel exporters run privileged rather than
  requesting `gpu.intel.com/i915`, and dcgm-exporter uses the `nvidia` RuntimeClass with
  the image's `NVIDIA_VISIBLE_DEVICES=all` rather than requesting `nvidia.com/gpu`.
  Workloads keep every device.
- **Core types come from the kernel.** An init container in the node_exporter pod reads
  the hybrid PMU CPU lists (`/sys/devices/cpu_core/cpus` and `cpu_atom/cpus`, the same
  lists `k3d-gpu doctor` reads). It writes `k3d_gpu_cpu_core_type{cpu,type}` for the
  textfile collector, where `type` is `performance` or `efficient`, matching the
  `k3d-gpu.io/cores` pod label values. A non-hybrid CPU gets no series. The init
  container runs once, at pod start: after a CPU is hotplugged or brought on or offline,
  the series stay stale until `kubectl -n k3d-gpu-telemetry rollout restart ds/node-exporter`.
- **Recording rules** (in `prometheus.yml`, unit-tested by `tests/telemetry_test.sh`):

  | Rule | Meaning |
  | ---- | ------- |
  | `k3d_gpu:cpu_utilisation:ratio{type}` | busy fraction (all modes but idle and iowait) of the P-cores or E-cores, over 1 m |
  | `k3d_gpu:cpu_frequency_hertz:avg{type}` | mean cpufreq scaling frequency per core type |
  | `k3d_gpu:rapl_power:watts{rapl_zone}` | RAPL power per zone; the duplicate MMIO `package` zone is dropped |

- **Balloons interplay.** With the NRI balloons policy (branch `claude/hybrid-core-balloons`),
  `kube-system` pods run on the 1-CPU reserve. Pods in `k3d-gpu-telemetry` are
  unlabelled, so they land in the `shared` balloon with every idle CPU. To keep exporters
  off the CPUs a benchmark uses, either add `k3d-gpu-telemetry` to
  `reservedPoolNamespaces` or label the exporter pods `k3d-gpu.io/cores=efficient`.
- **Per-balloon metrics.** The balloons policy serves `:8891/metrics` and its pod carries
  `prometheus.io/scrape: "true"`, but the baked BalloonsPolicy sets
  `instrumentation.prometheusExport: false`. With no metrics exporter configured,
  `metrics.Start` returns before registering `/metrics` (`pkg/instrumentation/metrics/metrics.go`
  at v0.14.0), while the HTTP server on `:8891` still runs with only `/healthz`. So
  `/metrics` answers 404, and the `kube-system-annotated` scrape job shows that target
  as down. Setting `prometheusExport: true` registers `/metrics` and exports a
  `balloons` gauge with `balloon_type`, `cpus`, `cpu_class`, `containers` and
  `tot_req_millicpu` attributes (`cmd/plugins/balloons/policy/metrics.go`). Read from
  source, unverified live. Probe:
  `curl -s -o /dev/null -w '%{http_code}\n' <balloons-pod-ip>:8891/metrics` should print `404`.
- **Multi-node clusters.** Every k3d node is a container on the same host. Each
  node_exporter, drm-exporter and dcgm-exporter pod therefore reports the same host CPUs
  and GPUs. Aggregate one node, or pin the DaemonSets to the server node.

## What each source reports on this host

Host: i9-13900T (P-cores CPUs 0-15 with HT siblings, E-cores 16-31), UHD 770 iGPU
(PCI `8086:a780`, driver `i915`), NVIDIA RTX 4000 SFF Ada and RTX 2000E Ada (driver
590.48.01), kernel 6.6. No AMX, NPU, DSA or QAT, so nothing here covers them.

**Verified** means observed on this host. **Unverified** means read from the source or
docs, with the probe that would confirm it.

### CPU and package power: node_exporter v1.12.1

Verified by running the pinned image under plain Docker on this host (root, host `/sys`
and `/proc` read-only, `--collector.rapl.enable-zone-label`). `node_scrape_collector_success`
was 1 for `rapl`, `cpufreq` and `hwmon`:

- `node_rapl_joules_total{rapl_zone}` with five series: `package` at
  `intel-rapl:0`, `package` again at `intel-rapl-mmio:0` (same counter value), `core`
  (`intel-rapl:0:0`, the cores), `uncore` (`intel-rapl:0:1`, mainly the iGPU) and `psys`
  (`intel-rapl:1`, platform). Each zone counter wraps at its own `max_energy_range_uj`,
  so compare rates, not raw values.
- `node_cpu_seconds_total{cpu,mode}`: 256 series (32 CPUs x 8 modes).
- `node_cpu_scaling_frequency_hertz{cpu}` for each CPU (sample: CPU 0 4.30 GHz, CPU 16 3.37 GHz).
- `node_hwmon_temp_celsius{chip="platform_coretemp_0"}`: package and per-core temperatures;
  also the NVMe drives and ACPI thermal zone.
- The rapl collector flag is `--[no-]collector.rapl` (default on). The zone-label flag is
  `--collector.rapl.enable-zone-label`, confirmed in `collector/rapl_linux.go` and
  `node_exporter --help` at v1.12.1. Without that flag the metric names are per zone,
  for example `node_rapl_package_joules_total`.
- `energy_uj` is mode 0400 root (CVE-2020-8694), so the pod runs as uid 0. Owner read
  needs no capability, so it keeps `drop: [ALL]`.

Verified offline: the core-type script, under the image's own busybox `sh`, writes
`cpu 0-15 -> performance` and `16-31 -> efficient` for this host's lists. Its output
passes `promtool check metrics`.

Unverified inside k3d. Probe:
`kubectl -n k3d-gpu-telemetry exec ds/node-exporter -- wget -qO- localhost:9100/metrics | grep -E '^node_rapl|^k3d_gpu_cpu_core_type' | head`.
The k3d node container is privileged and sees host sysfs, so the same five RAPL series
and 32 core-type series are expected.

### Per-core-type utilisation

From `node_cpu_seconds_total` joined with `k3d_gpu_cpu_core_type`. cAdvisor (the kubelet's
`/metrics/cadvisor`) reports CPU per container, not per core: it shows which pods burn
CPU, not on which core type. Per-pod placement comes from the balloons metrics above.
Scraping cAdvisor is not wired here; it needs `nodes/proxy` RBAC and the kubelet
scrape job.

Unverified end to end. Probe, after port-forwarding Prometheus:
`curl -s 'localhost:9090/api/v1/query?query=k3d_gpu:cpu_utilisation:ratio'` should return
one `performance` and one `efficient` series.

### Intel UHD 770: drm-exporter 0.3.4 (default iGPU exporter)

drm-exporter reads the i915 perf PMU and sysfs through qmlib, and lists `i915` Gen12
UHD/Iris Xe as supported. This host's i915 PMU exposes busy counters for engines `rcs0`,
`bcs0`, `vcs0`, `vcs1` and `vecs0`, plus `actual-frequency`, `requested-frequency` and
`rc6-residency`. The `power` PMU exposes `energy-gpu`. Expected series, **unverified**:

- `drm_engine_utilization_ratio{engine}` for render, copy, two video and one
  video-enhance engine.
- `drm_frequency_hertz{kind="actual"|"max"}`.
- `drm_power_watts{domain="gpu"}` from the `energy-gpu` PMU (the same energy as the RAPL
  `uncore` zone); `drm_power_watts{domain="package"}`.
- `drm_memory_used_bytes{pool="smem"}` (an iGPU has no VRAM pool).
- `drm_temperature_celsius` (package temperature via MSR; `/dev/cpu/*/msr` exists here).
- `drm_info{driver="i915",type=...}`.

Probe: `kubectl -n k3d-gpu-telemetry exec ds/drm-exporter -- wget -qO- localhost:8081/metrics`.
The image is distroless and may lack `wget`. If so, port-forward the pod instead:
`kubectl -n k3d-gpu-telemetry port-forward ds/drm-exporter 8081` and
`curl -s localhost:8081/metrics | grep ^drm_`.

### Intel dGPUs: xpumd v2.3.0 (optional)

Findings from the v2.3.0 release, the `xpumd/` tree at tag `v2.3.0`, and the chart
(`oci://ghcr.io/intel/xpumanager/charts/xpumd`, version 2.3.0):

- v2.3.0 (2026-10-14) is the latest release. The 2.x daemon is a Go rewrite on
  OpenTelemetry and Level Zero Sysman. The daemon and its Prometheus exporter run in one
  process, metric names follow OTel conventions (`hw_*`), and it only reads metrics and
  status (`docs/CHANGES.md`, "XPUM 2.x rewrite").
- **Supported devices per the release notes: Arc Pro B50, B60, B65 and B70 only.** The
  UHD 770 is not on the list.
- The image bundles compute-runtime 26.35.39758.10. Its release notes list Alder Lake
  and Raptor Lake as Production for Level Zero. The receiver also has an explicit
  `hw_gpu_type="integrated"` value (`receiver/intelxpu/sysman/device.go`). So it may
  enumerate the UHD 770, but iGPU metric coverage is not validated upstream. Integrated
  GPUs have no VRAM, hwmon temperature or ECC, so expect at most engine utilisation,
  frequency and possibly power.
- Metric names (from `docs/` and the dashboard JSON) include `hw_gpu_utilization_ratio{hw_gpu_task}`,
  `hw_frequency_hertz`, `hw_power_watts`, `hw_energy_joules_total`, `hw_temperature_celsius`,
  `hw_memory_usage_bytes`, `hw_gpu_info`, `hw_status` and `hw_errors_total`.
- **Benchmark caveat.** xpumd reads engine utilisation through Sysman `GetActivity`
  (`receiver/intelxpu/sysman/engine.go`). The xpu-smi v2.3.0 notes moved xpu-smi off
  those counters because they "keep the media GT awake and slowed running workloads (by
  up to ~25%)". Disable `hw.gpu.utilization` under `config.receivers.intel_xpu.metrics`
  in the chart values when benchmarking, or use drm-exporter.

Unverified on this host. Probe: apply `optional/intel-xpumd.yml`, then check
`kubectl -n k3d-gpu-telemetry logs ds/xpumd` for Sysman init and
`kubectl -n k3d-gpu-telemetry port-forward ds/xpumd 8080` with
`curl -s localhost:8080/metrics | grep -E '^hw_(gpu_info|gpu_utilization|frequency|power)'`.

### Intel GPU plugin `-enable-monitoring`

In intel-device-plugins-for-kubernetes v0.37.1 (`cmd/gpu_plugin/gpu_plugin.go`),
`-enable-monitoring` adds a resource that maps every Intel GPU into one container
without consuming the GPU resources. `-monitoring-mode=single`, the default, names it
`gpu.intel.com/monitoring`; `split` gives `gpu.intel.com/i915_monitoring` and
`gpu.intel.com/xe_monitoring`. It is the least-privilege path for xpumd
(`gpuAccess=plugin`, plugin v0.36.0 or newer) and drm-exporter. The baked
`share/intel-gpu-plugin.yml` does not pass the flag, and k3s re-applies baked manifests,
so this stack uses privileged pods instead. To switch, add `-enable-monitoring` to the
baked plugin args in an image release, then render xpumd with `gpuAccess=plugin`.
Unverified probe after that change:
`kubectl get node -o jsonpath='{.items[0].status.allocatable.gpu\.intel\.com/monitoring}'`
should print `1`.

### intel_gpu_top-based exporters (evaluated, not shipped)

- `onedr0p/intel-gpu-exporter` is archived. `home-operations/drm-exporter`, from the
  same maintainers, replaces it and reads the PMU directly, without intel_gpu_top.
- `clambin/intel-gpu-exporter` v0.7.4 (2026-08-31, MIT) is actively maintained. It runs
  `intel_gpu_top -J` and exports `gpumon_engine_usage{engine,attrib}`, `gpumon_power{type}`,
  `gpumon_frequency{type}`, `gpumon_clients_count` and `gpumon_imc_read/write`. Its
  image is `FROM alpine` and installs `igt-gpu-tools` from Alpine **edge/testing**,
  both unpinned, so every rebuild floats intel_gpu_top. Not shipped for that reason;
  pin it by digest if it is ever needed.

### NVIDIA RTX 4000 SFF Ada and RTX 2000E Ada: dcgm-exporter 4.8.4

4.8.4 (2026-09-18) is the latest release. The image tag scheme is
`<DCGM>-<exporter>-<flavour>`, so this is `4.6.1-4.8.4-distroless` (the chart default).
The registry has no `-ubuntu22.04`, `-ubuntu24.04` or `-ubi9` tag for 4.8.4. The chart's default counters (26 fields in
`etc/default-counters.csv`) are expected per GPU, **unverified**:

- `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_MEM_COPY_UTIL`, `DCGM_FI_DEV_ENC_UTIL`,
  `DCGM_FI_DEV_DEC_UTIL`: engine utilisation.
- `DCGM_FI_DEV_SM_CLOCK`, `DCGM_FI_DEV_MEM_CLOCK`, `DCGM_FI_DEV_GPU_TEMP`,
  `DCGM_FI_DEV_MEMORY_TEMP`.
- `DCGM_FI_DEV_POWER_USAGE` (W), `DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION` (mJ).
- `DCGM_FI_DEV_FB_USED`, `DCGM_FI_DEV_FB_FREE`, `DCGM_FI_DEV_FB_RESERVED`,
  `DCGM_FI_DEV_XID_ERRORS`, `DCGM_FI_DEV_PCIE_REPLAY_COUNTER`.
- `DCGM_FI_PROF_GR_ENGINE_ACTIVE`, `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE`,
  `DCGM_FI_PROF_DRAM_ACTIVE` and `DCGM_FI_PROF_PCIE_TX/RX_BYTES`: profiling (DCP)
  fields. They need `SYS_ADMIN`, which the chart grants. Whether DCGM serves them on RTX
  workstation Ada boards is **unverified**.
- NVLink and row-remap fields: not present on these boards.
- With `DCGM_EXPORTER_KUBERNETES=true`, series carry `pod`/`namespace`/`container`
  labels for pods that requested `nvidia.com/gpu`, read from the kubelet pod-resources
  socket at `/var/lib/kubelet/pod-resources` (the k3s default kubelet root).

Probe: `kubectl -n k3d-gpu-telemetry logs ds/dcgm-exporter | grep -i -E 'not supported|profil'`,
then `kubectl -n k3d-gpu-telemetry port-forward ds/dcgm-exporter 9400` and
`curl -s localhost:9400/metrics | grep -c ^DCGM_FI`.

## Validation

Run without a cluster:

- `tests/telemetry_test.sh`: core-type script against fake sysfs trees (hybrid, gaps,
  E-cores offline, non-hybrid), and the AUR `package()` function, which must install
  every manifest under `/usr/share/k3d-gpu/telemetry/`. When `promtool` is on PATH, it also runs
  `promtool check metrics` on the script output and `promtool test rules` on the
  recording rules with `tests/telemetry-rules.test.yml`. CI does not run it yet; the job that installs promtool 3.15.0 needs a push with the GitHub `workflow` scope.
- `kubeconform -kubernetes-version 1.34.1 -strict share/telemetry share/telemetry/optional`
  (the k3s version in the `Dockerfile`): 29 resources valid.
- `promtool check config` on the extracted `prometheus.yml`: valid, 3 rules.

Not validated without a cluster: API-server admission (`kubectl apply --dry-run=server`),
the Prometheus pod discovery relabelling, and every exporter's output inside k3d.
