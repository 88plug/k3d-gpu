# Failure modes: hybrid NRI balloons and device plugins

What breaks, what the user sees, and how `k3d-gpu doctor` detects it. Every
claim about k3s, containerd, NRI, nri-plugins, kubelet or k3d cites the source
line at the version this repo ships:

| Component | Version | Why this version |
| --- | --- | --- |
| k3s | `v1.34.1+k3s1` | `K3S_TAG` in [`Dockerfile`](../Dockerfile) |
| containerd | k3s-io fork `v2.1.4-k3s2` | [k3s go.mod:11](https://github.com/k3s-io/k3s/blob/v1.34.1%2Bk3s1/go.mod#L11) |
| NRI (runtime side) | `v0.8.0` | [k3s go.mod:226](https://github.com/k3s-io/k3s/blob/v1.34.1%2Bk3s1/go.mod#L226) |
| nri-plugins balloons | `v0.14.0`, built on NRI `v0.11.0` | [`share/nri-balloons.yml`](../share/nri-balloons.yml) image tag; [nri-plugins go.mod:7](https://github.com/containers/nri-plugins/blob/v0.14.0/go.mod#L7) |
| kubelet | `v1.34.1` | [k3s go.mod:172](https://github.com/k3s-io/k3s/blob/v1.34.1%2Bk3s1/go.mod#L172) |
| k3d | `v5.9.0` | version used to build and test the launcher |

Link prefixes used below:
`CTRD` = `https://github.com/k3s-io/containerd/blob/v2.1.4-k3s2/`,
`NRI` = `https://github.com/containerd/nri/blob/v0.8.0/`,
`NRIP` = `https://github.com/containers/nri-plugins/blob/v0.14.0/`,
`K8S` = `https://github.com/kubernetes/kubernetes/blob/v1.34.1/`,
`K3S` = `https://github.com/k3s-io/k3s/blob/v1.34.1%2Bk3s1/`,
`K3D` = `https://github.com/k3d-io/k3d/blob/v5.9.0/`.

## Baseline: how NRI is configured in this image

- k3s compiles containerd's NRI plugin in: `K3S pkg/containerd/builtins.go:31`.
- k3s rewrites containerd's `config.toml` from its v3 template on every agent
  start (`K3S pkg/agent/containerd/config.go:44-71`, called from
  `config_linux.go:109`). The template (`K3S pkg/agent/templates/templates.go:158-277`)
  has no `nri` table, so containerd's NRI defaults apply.
- containerd's NRI defaults: enabled, socket `/var/run/nri/nri.sock`,
  registration timeout 5 s, request timeout 2 s
  (`CTRD internal/nri/config.go:43-53`; values from `NRI pkg/api/timeouts.go:25,27`
  and `NRI pkg/api/plugin.go:26`).
- The only way to change them is a user template
  `config-v3.toml.tmpl` next to the generated config (`K3S pkg/agent/containerd/config.go:51-63`),
  which k3d-gpu does not ship.

## 1. The balloons plugin pod crashes, is evicted, or is slow

**Cited behaviour: fail-open for a missing or dead plugin; fail-closed for an
error the plugin returns.**

- containerd asks only the plugins connected right now. `CreateContainer` loops
  over `r.plugins` and, with none, returns an empty adjustment
  (`NRI pkg/adaptation/adaptation.go:201-224`).
- When the plugin's connection drops, the ttRPC on-close hook marks it closed
  (`NRI pkg/adaptation/plugin.go:204-210`) and the next request removes it from
  the list (`adaptation.go:396-414`). Pod creation keeps working.
- A request that hits the 2 s timeout is fatal for the plugin, not for the pod:
  `context.DeadlineExceeded` counts as fatal (`plugin.go:607-619`), the plugin is
  closed and `createContainer` returns `nil, nil` (`plugin.go:524-530`), so the
  container is created unadjusted.
- The balloons side exits when its NRI connection goes away
  (`NRIP pkg/resmgr/nri.go:121-124`, `os.Exit(1)`), so the DaemonSet restarts it.
- On reconnect, NRI synchronizes the plugin with every existing pod and container
  (`NRI pkg/adaptation/adaptation.go:466-480`); balloons re-runs its policy over
  them and returns updates (`NRIP pkg/resmgr/nri.go:287-337`), which containerd
  applies (`CTRD internal/nri/nri.go:471-480`). A failed post-sync update is only
  logged (`nri.go:477-480`).
- If balloons itself returns an error (for example no CPUs left for a balloon),
  `AllocateResources` fails (`NRIP pkg/resmgr/nri.go:488-491`), the error is not
  fatal-class, `createContainer` returns it (`NRI plugin.go:531`) and
  `Adaptation.CreateContainer` fails the container (`adaptation.go:209-211`).
- Eviction: the DaemonSet runs at `priorityClassName: system-node-critical`
  ([`share/nri-balloons.yml:1814`](../share/nri-balloons.yml)). kubelet's
  node-pressure eviction refuses critical pods
  (`K8S pkg/kubelet/eviction/eviction_manager.go:606-611`; `IsCriticalPod` at
  `pkg/kubelet/types/pod_update.go:160-171`, priority threshold at `188-190`).

**User-visible symptom.** While balloons is down, a pod labelled
`k3d-gpu.io/cores=performance|efficient` starts normally but on every CPU. No
event, no error. It is re-pinned once balloons reconnects and syncs. A policy
error instead shows as the pod stuck in `CreateContainerError` with
`failed to allocate resources` in its events.

**Detection.** `doctor` fails when any node carries `k3d-gpu.io/hybrid-cpu=true`
and the `kube-system/nri-resource-policy-balloons` DaemonSet is missing, scheduled
on no node, or has fewer Ready pods than scheduled (implemented). `k3d-gpu test`
already checks the real CPU list of a labelled pod.

## 2. The node restarts

**Cited behaviour: cpusets do not persist; they are recomputed and re-applied.**

- k3d starts nodes with restart policy `unless-stopped`
  (`K3D pkg/client/cluster.go:492`, `pkg/runtimes/docker/translate.go:95-99`), so
  a host reboot restarts the node container.
- `/run` and `/var/run` are tmpfs in every k3d node
  (`K3D pkg/types/defaults.go:59-63`, applied at `translate.go:101-105`). The NRI
  socket `/var/run/nri/nri.sock` and balloons' socket dir
  `/var/run/nri-resource-policy` ([`share/nri-balloons.yml`](../share/nri-balloons.yml) hostPaths)
  are gone; containerd recreates its socket at start (`NRI pkg/adaptation/adaptation.go:422-433`).
- A cpuset is part of the container's spec, set at create time through the NRI
  adjustment (`CTRD internal/cri/server/container_create.go:414`). Restarted
  containers are new containers, so they get whatever the plugins connected at
  that moment return.
- Pods created before balloons reconnects get no adjustment (scenario 1), then
  the sync on connect re-pins them (`NRI adaptation.go:466-480`,
  `CTRD internal/nri/nri.go:471-480`).
- balloons' state dir `/var/lib/nri-resource-policy` is neither a Docker `VOLUME`
  ([`Dockerfile`](../Dockerfile) lines 69-72) nor tmpfs, so it lives in the node
  container's writable layer: kept across a restart, lost when the cluster is
  recreated. Stale entries are reconciled by the same sync
  (`NRIP pkg/resmgr/nri.go:300-330`).

**User-visible symptom.** For a short window after boot, labelled pods can run on
every CPU, then converge. If balloons cannot start, they stay unpinned.

**Detection.** The scenario 1 check. When balloons is not Ready, `doctor` also
checks for `/var/run/nri/nri.sock` inside `k3d-<cluster>-server-0` and fails if it
is missing (implemented): no socket means containerd has NRI disabled
(`CTRD internal/nri/nri.go:113-116`) or refuses external plugins
(`NRI adaptation.go:417-420`), which no amount of restarting fixes.

## 3. Two NRI resource policies register at once

**Cited behaviour: both are called; a second cpuset claim fails the container.**

- NRI has no exclusivity: any number of external plugins connect and are kept,
  sorted by index (`NRI pkg/adaptation/adaptation.go:476-478`, `541-552`). There
  is no duplicate-name or duplicate-role check in `pkg/adaptation`.
- Each plugin's adjustment is merged with ownership tracking. The second plugin
  that sets `Cpu.Cpus` gets `plugins "<a>" and "<b>" both tried to set CPU pinning`
  (`NRI pkg/adaptation/result.go:646-651`, `1262-1268`, message at `1396-1399`),
  and `CreateContainer` returns that error (`adaptation.go:213-215`).
- balloons sets a cpuset for every container it places, and the `shared` balloon
  matches everything (`operator: AlwaysTrue`, [`share/nri-balloons.yml:1874-1875`](../share/nri-balloons.yml)).
  Upstream ships its policies as DaemonSets named `nri-resource-policy-<policy>`
  (`NRIP deployment/helm/balloons/templates/daemonset.yaml:6`,
  `topology-aware/templates/daemonset.yaml:6`, `template/templates/daemonset.yaml:6`).

**User-visible symptom.** New containers that both policies pin fail with
`CreateContainerError` naming both plugins. Pods already running are not touched
until they restart.

**Detection.** `doctor` fails when more than one DaemonSet named
`nri-resource-policy-*` exists in the cluster and lists them (implemented).

## 4. A k3s upgrade

**Cited behaviour: k3d-gpu has no in-place upgrade; an upgrade is a new cluster
on a new image, with a new containerd and NRI underneath an unchanged plugin.**

- The launcher's verbs are `up`, `down`, `test`, `detect`, `doctor`, `logs`
  ([`scripts/k3d-gpu`](../scripts/k3d-gpu) dispatch at the end of the file). An
  upgrade is `down` then `up` with a newer `K3D_GPU_IMAGE` (default
  `cryptoandcoffee/k3d-gpu:latest`).
- The new image brings its own containerd and NRI (`K3S go.mod:11,226` at that
  k3s tag) and a freshly generated containerd config (scenario Baseline). If a
  future containerd changes an NRI default, it reaches the node with no change in
  this repo.
- The balloons manifest is pinned (`nri-resource-policy-balloons:v0.14.0`,
  [`share/nri-balloons.yml:1788`](../share/nri-balloons.yml)) and does not move
  with k3s. It already runs a newer NRI stub (`v0.11.0`, `NRIP go.mod:7`) than
  the runtime (`v0.8.0`); compatibility across that skew is what `k3d-gpu test`
  exercises, not something this document proves.
- The manifest is baked into k3s's auto-deploy dir, which is a `VOLUME`
  ([`Dockerfile`](../Dockerfile) lines 61 and 70); a new cluster gets the copy
  from the new image.

**User-visible symptom.** Same as scenario 1 or 3: balloons crash-loops (labelled
pods unpinned) if the new runtime rejects it, or containers fail if the new image
ships a second policy.

**Detection.** The scenario 1, 2 and 3 checks, run after `up`, plus
`k3d-gpu test` with hybrid active, which fails when a labelled pod's
`Cpus_allowed_list` is outside its core type.

## 5. A device plugin socket disappears

**Cited behaviour: kubelet keeps the devices in capacity, drops them from
allocatable at once, and removes the resource after 5 minutes.**

- kubelet reads the plugin over a `ListAndWatch` stream. When the plugin dies or
  its socket goes, the stream errors and `Run` returns
  (`K8S pkg/kubelet/cm/devicemanager/plugin/v1beta1/client.go:80-97`); the
  handler disconnects the client (`handler.go:107-118`), which calls
  `PluginDisconnected` (`client.go:108`).
- `PluginDisconnected` marks every device of the resource unhealthy and stamps a
  stop time (`devicemanager/manager.go:243-252`, `411-422`).
- Node status: unhealthy devices count toward capacity but not allocatable; once
  the 5-minute grace period (`devicemanager/types.go:120`) passes, the resource is
  deleted from the node (`manager.go:436-483`).
- The scheduler fits extended resources against allocatable and reports
  `Insufficient <resource>` (`K8S pkg/scheduler/framework/plugins/noderesources/fit.go:646-649`).
- Running pods keep their devices; kubelet notes that after its own restart
  capacity can read zero until plugins re-register (`manager.go:431-436`). On
  start, kubelet wipes the plugin socket dir (`plugin/v1beta1/server.go:100-106`),
  so every plugin has to register again.

**User-visible symptom.** New pods that request the resource stay `Pending` with
`Insufficient nvidia.com/gpu` (or `habana.ai/gaudi`, `gpu.intel.com/xe`, ...).
`kubectl describe node` shows capacity N and allocatable 0. After 5 minutes the
resource vanishes from the node.

**Detection.** `doctor` fails when a node has an extended resource (a name with
`/`) whose allocatable is 0 while capacity is not, and warns when allocatable is
merely below capacity (some devices unhealthy). It also fails when the device
plugin DaemonSet of a detected accelerator is missing, scheduled on no node, or
not fully Ready (all implemented).

## Doctor checks added

All run only when `kubectl --context k3d-<cluster>` answers; otherwise `doctor`
keeps to its host checks. Unit tests in [`tests/detect_test.sh`](../tests/detect_test.sh)
drive them with a stubbed `kubectl` and `docker`.

| Check | Result | Scenario |
| --- | --- | --- |
| Device plugin DaemonSet of each detected accelerator missing, on no node, or not all Ready | fail | 5 |
| Hybrid-labelled node and `nri-resource-policy-balloons` missing, on no node, or not all Ready | fail | 1, 2, 4 |
| ...and no `/var/run/nri/nri.sock` in the server node | fail | 2, 4 |
| More than one `nri-resource-policy-*` DaemonSet | fail | 3, 4 |
| Extended resource with allocatable 0 and capacity > 0 | fail | 5 |
| Extended resource with 0 < allocatable < capacity | warn | 5 |
