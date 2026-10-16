# k3d-gpu security audit

Audit date: 2026-10-16. Read-only review. This document fixes nothing. Each
finding below cites the line it is about, gives a severity and gives a concrete fix.

## Scope

| Target | Ref audited | State |
|---|---|---|
| `main` | `origin/main` @ `ba81920` (aur-v0.1.7) | pushed |
| `claude/agent-stack` | `0e0c141` (local branch, not on origin) | committed |
| `claude/report-bench` | working tree on `109154d`, files dated 04:44 PDT | uncommitted snapshot |
| `claude/nfd-k3s-bump` | working tree on `109154d`, files dated 04:42 PDT | uncommitted snapshot |
| `claude/eat-on-k3d` | working tree on `109154d`, files dated 04:42 PDT | uncommitted snapshot |
| `claude/igpu-sharing` | working tree on `ba81920` | uncommitted snapshot |
| EAT on k3d proposal | `/tmp/claude-1000/wave2/EAT_DEEPDIVE.md` | design doc |
| `origin/claude/doctor-nvidia-runtime-check`, `origin/claude/hybrid-core-balloons` | `4df5d0c`, `fb5c17d` | both already merged into main (#8, #9); nothing new |
| `claude/balloons-eat-compat`, `eat-upstream-pack`, `dataplane-accel-design`, `intel-showcase`, `intel-telemetry`, `provenance-log`, `agent-gaudi` | no commits past main | nothing to audit yet |
| `claude/autotune`, `claude/doctor-perf` | not found locally or on origin | not audited |

`git fetch origin` shows no wave branches pushed yet. The only `claude/*` refs
on origin are the two that are already merged. The in-flight branches were
therefore read from their worktrees. Snapshot line numbers will move as those
branches change, so recheck each cited line before acting on it.

Severity scale:

- **High**: remote or cross-user compromise with no unusual preconditions.
- **Medium**: exposure, or a supply-chain gap a third party can exploit.
- **Low**: needs local env control, an upstream compromise, or extra
  misconfiguration.
- **Info**: hardening or a confirmed-good observation.

No High findings.

## Summary

| ID | Sev | Where | Finding |
|---|---|---|---|
| M-1 | Medium | `scripts/k3d-gpu:698-701` | Falls back to `kubectl apply` of the NVIDIA plugin from the moving `main` branch |
| M-2 | Medium | `scripts/k3d-gpu:33` | Privileged node image defaults to `:latest` |
| M-3 | Medium | `scripts/k3d-gpu:596-600` | k3s API published on `0.0.0.0` (k3d default) |
| M-4 | Medium | `share/*.yml`, `Dockerfile:1-14`, `scripts/k3d-gpu:44-45` | Every image pinned by tag only, none by digest |
| M-5 | Medium | `.github/workflows/*.yml` | Actions pinned by mutable tag; shellcheck by `@master` |
| M-6 | Medium | `build-and-push.yml:68,139,160,173,193` | Unvalidated upstream release tag interpolated into `run:` scripts |
| M-7 | Info | `scripts/k3d-gpu:804,818,845,853` | Pod JSON built by string interpolation; `kubectl run --image` blocks the env-image vector |
| M-8 | Low | `scripts/k3d-gpu:782-797` | `test` creates and deletes pods in whatever kube-context is current |
| M-9 | Low | `share/nri-balloons.yml:1786-1787` | `ENABLE_TEST_APIS=1` shipped (upstream chart bug) |
| M-10 | Low | `share/nri-balloons.yml:1802-1805,1820-1827` | Balloons gets read-write host `/sys` and `/proc` as uid 0 |
| M-11 | Low | `share/gaudi-device-plugin.yml:32-44`, `share/nvidia-device-plugin.yml:24-61` | SA token automounted; Gaudi plugin privileged |
| M-12 | Low | `Dockerfile:18-25` | apt key fetched without a fingerprint check; toolkit version unpinned; `curl -s` without `-f` |
| M-13 | Low | `packaging/aur/PKGBUILD:16-17` | Source is a mutable tag with `sha256sums=('SKIP')` |
| M-14 | Low | `dependabot-auto-merge.yml:11-15` | Gate is `github.actor`, and every update type is merged |
| M-15 | Info | `share/nri-balloons.yml:1645-1666` | Balloons ClusterRole patches status on every node |
| M-16 | Info | `scripts/k3d-gpu:613-614` | k3d nodes are privileged, and no Pod Security admission is set |
| A-1 | Medium | agent-stack `scripts/k3d-gpu:1060-1068,1090-1102` | OVMS `/v3` unauthenticated on `0.0.0.0:8000`, reachable from every pod |
| A-2 | Medium | agent-stack `scripts/k3d-gpu:870,1047-1048` | Model pulled from the moving Hugging Face HEAD, with no revision pin |
| A-3 | Low | agent-stack `scripts/k3d-gpu:986-1102,1109` | Manifest YAML and warm-up JSON built from `K3D_GPU_AGENT_*` env (PoC below) |
| A-4 | Low | agent-stack `scripts/k3d-gpu:1034-1059` | OVMS pod lacks capability drop, no-privilege-escalation, seccomp and SA-token opt-out |
| R-1 | Low | report-bench `bench/energy.sh:59-63,81` | `sudo bash -c` reader runs as root on a caller-chosen path |
| N-1 | Low | nfd-k3s-bump `scripts/k3d-gpu:644-646` | All of host `/boot` mounted into the node, though NFD needs one file |
| E-1 | Medium | `EAT_DEEPDIVE.md:73` | Proposed `-p 80:80 -p 443:443` publishes the EAT ingress on all interfaces |
| E-2 | Low | eat-on-k3d `scripts/k3d-gpu:642-645` | `K3D_GPU_NODE_LABELS` validated only as `?*=*` |

Confirmed good: no secrets in any manifest, script or workflow; no
`curl | sh` anywhere; no `eval`. The OVMS image is pinned by digest, and that
digest is `openvino/model_server:2026.4.1-gpu`. NFD and k3s v1.37.1 are pinned
by digest in nfd-k3s-bump, and both digests resolve. The report JSON and HTML
encoding is correct. The igpu-sharing env validation is strict.

---

## main (`origin/main` @ `ba81920`)

### M-1 Medium: fallback applies a manifest from a moving branch

`scripts/k3d-gpu:698-701`:

```bash
    elif [ "${vendor}" = "nvidia" ]; then
        warn "${manifest} missing — applying upstream from GitHub"
        kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/main/deployments/static/nvidia-device-plugin.yml
```

This path runs when the image does not bake the plugin and the share file is
missing. It applies whatever is on NVIDIA's `main` branch at that moment. The
result is a kube-system DaemonSet with an unpinned image and unpinned content.
The other vendors already `err` on this path (line 703).

**Fix:** treat NVIDIA like the other vendors and `err` here. The AUR package
always installs `/usr/share/k3d-gpu/nvidia-device-plugin.yml`
(`packaging/aur/PKGBUILD:22`). If a network fallback must stay, pin it to the
tag the baked file uses (`.../k8s-device-plugin/v0.20.1/deployments/static/...`)
and bump it in the same `build-and-push.yml` step that bumps
`share/nvidia-device-plugin.yml` (line 160).

### M-2 Medium: privileged node image defaults to `:latest`

`scripts/k3d-gpu:33`:

```bash
IMAGE="${K3D_GPU_IMAGE:-cryptoandcoffee/k3d-gpu:latest}"
```

The node container runs privileged with the host `/dev` (M-16). The nightly
workflow rewrites `:latest` (`build-and-push.yml:228`) with whatever upstream
k3s and plugin versions it resolves that night. So the launcher version does
not fix the node contents. `:latest` resolved to
`sha256:71a21069f0669f53bf93657cff65e12e882ba17f6954cdc4cd56b413b48bb389` on
2026-10-16.

**Fix:** default to an immutable reference. The workflow already publishes
`cryptoandcoffee/k3d-gpu:<k3s>-ubuntu26.04` (`build-and-push.yml:230`).
Write that tag plus `@sha256:<digest from the build-push step's outputs.digest>`
into the `IMAGE=` line, in the commit the workflow already makes (line 189).
Keep `K3D_GPU_IMAGE` as the override.

### M-3 Medium: k3s API is published on all interfaces

`scripts/k3d-gpu:596-600`:

```bash
    local create_args=(
        --image="${IMAGE}"
        --no-lb
        --k3s-arg="--disable=traefik@server:0"
    )
```

`--api-port` is not passed. k3d v5.9.0 then binds the API on
`DefaultAPIHost = "0.0.0.0"` (`pkg/types/defaults.go:86`;
`cmd/util/ports.go:76-77` substitutes it when no host is given). Anonymous
auth is off: k3s sets `argsMap["anonymous-auth"] = "false"`
(`pkg/daemons/control/server.go:220` @ v1.34.1+k3s1). So the exposure is a
TLS endpoint gated by client certificates, reachable from the LAN.

**Fix:** add `--api-port="${K3D_GPU_API_PORT:-127.0.0.1:random}"`. k3d's
regex accepts `random` with a host IP (`ports.go:36,85-96`). Document
`K3D_GPU_API_PORT=0.0.0.0:6443` for users who want remote access.

UNVERIFIED (needs a live cluster): `docker port k3d-gpu-server-0 6443/tcp`
should print `0.0.0.0:<port>` today and `127.0.0.1:<port>` after the fix.

### M-4 Medium: images pinned by tag, not digest

| Line | Reference | Digest resolved 2026-10-16 |
|---|---|---|
| `Dockerfile:1,6` | `rancher/k3s:v1.34.1-k3s1-amd64` | `sha256:1b1b4dab0372b06c0cb49ea1259b3f56b07e00e4dd54928bb408139642cf9b83` |
| `Dockerfile:3,14` | `ubuntu:26.04` | `sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7` |
| `build-and-push.yml:243` | `ubuntu:24.04` | `sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55` |
| `share/nvidia-device-plugin.yml:45` | `nvcr.io/nvidia/k8s-device-plugin:v0.20.1` | `sha256:27c1b2553a690ca3d29889dcd61df97525d46f58272c0be8dc63c2b18f120075` |
| `share/intel-gpu-plugin.yml:32` | `intel/intel-gpu-plugin:0.37.1` | `sha256:004641b2547d58f1eadce58473fa160ad71377de1eb588cf1f82832387bf3e33` |
| `share/intel-npu-plugin.yml:34` | `intel/intel-npu-plugin:0.37.1` | `sha256:cdcf6f4c662c0d1ee4e3fc841a56e1a097706c880f8f98fd3ff51d7a8f02b547` |
| `share/intel-dsa-plugin.yml:38` | `intel/intel-dsa-plugin:0.37.1` | `sha256:e7673d0b4fa60d846b7be037953c81bb0c9805ab75a6f790cb68c486b99d3c93` |
| `share/intel-iaa-plugin.yml:38` | `intel/intel-iaa-plugin:0.37.1` | `sha256:6391dcd3b2f3ca487675503e850db5ec77ec3dd2054867e1de196d4986bc60bc` |
| `share/intel-qat-plugin.yml:38` | `intel/intel-qat-plugin:0.37.1` | `sha256:4751706168d6d0e100e6391e3bb288fc9386920c5c2f733f2073d04f830c7418` |
| `share/gaudi-device-plugin.yml:42` | `docker.io/intel/gaudi-device-plugin:1.24.2-481` | `sha256:386e6bec28b3f25a0e4e1c0fb726f026e8ed199bd74e4aa0fb226631359f7daf` |
| `share/nri-balloons.yml:1788` | `ghcr.io/containers/nri-plugins/nri-resource-policy-balloons:v0.14.0` | `sha256:a8c8297c72744f25dbcf70fdb4a64bb787f428a3f9ab4dfabb0eb1377da0070f` |
| `scripts/k3d-gpu:44` | `nvidia/cuda:13.4.2-base-ubuntu26.04` | `sha256:ac2289b83fba0b38cddf6513f3703d132135ae070cc1b7c63234b90dca4c60f2` |
| `scripts/k3d-gpu:45` | `busybox:1.37` | `sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e` |
| `ci.yml:40` | `archlinux:latest` | `sha256:3c64b538588416593f69a1aebd2f2ec0f2541b29159f32556fa7def2167147a0` |

Each digest is the multi-arch index digest returned by
`docker buildx imagetools inspect <ref>`. Every one of these runs either inside
the privileged node or as a DaemonSet with hostPath mounts. A re-pushed tag
changes cluster contents with no commit.

**Fix:** write `name:tag@sha256:...`, as nfd-k3s-bump already does for k3s and
NFD (`ARG K3S_DIGEST` with `FROM rancher/k3s:$K3S_TAG@$K3S_DIGEST`). In
`build-and-push.yml`, resolve each digest right after the tag (step 2/2b/2c)
and write both fields in the `sed` lines 159-167. The `grep -oE` readers at
lines 122-128 keep working, because they match only the tag part.

### M-5 Medium: workflow actions pinned by mutable tag

| Line | Action | Current commit |
|---|---|---|
| `ci.yml:15` | `ludeeus/action-shellcheck@master` (a branch) | `00b27aa7cb85167568cb48a3838b75f4265f2bca` |
| `aur-deploy.yml:39` | `KSXGitHub/github-actions-deploy-aur@v4.1.3`, which receives `secrets.AUR_SSH_PRIVATE_KEY` (line 45) | `da03e160361ce01bf087e790b6ffd196d7dccff7` |
| `build-and-push.yml:199` | `docker/setup-buildx-action@v3` | `8d2750c68a42422c14e847fe6c8ac0403b4cbd6f` |
| `build-and-push.yml:203,209` | `docker/login-action@v3`, which receives `DOCKERHUB_TOKEN` | `c94ce9fb468520275223c153574b00df6fe4bcc9` |
| `build-and-push.yml:220,237` | `docker/build-push-action@v6` | `10e90e3645eae34f1e60eeb005ba3a3d33f178e8` |
| `build-and-push.yml:252` | `softprops/action-gh-release@v2` | `3bb12739c298aeb8a4eeaf626c5b8d85266b0e65` |
| `ci.yml:32` | `hadolint/hadolint-action@v3.3.0` | `2332a7b74a6de0dda2e2221d575162eba76ba5e5` |
| all | `actions/checkout@v6` | `d23441a48e516b6c34aea4fa41551a30e30af803` |

Each commit was resolved with `gh api repos/<repo>/commits/<ref>` on 2026-10-16.
`build-and-push.yml` runs with `contents: write` and `packages: write`
(lines 24-26) and pushes to `main` (line 194). A moved tag would therefore run
with push rights to the repository, Docker Hub and GHCR.

**Fix:** use `uses: owner/repo@<sha> # vX.Y.Z`. Dependabot already watches
`github-actions` (`dependabot.yml:3`) and keeps SHA pins current. Replace
`@master` with the SHA of the action's newest release tag.

### M-6 Medium: unvalidated upstream release tag interpolated into `run:` scripts

`build-and-push.yml:64-68` accepts any non-null `tag_name` from
`NVIDIA/k8s-device-plugin`:

```bash
            | jq -r '.tag_name')
          [ -n "$latest" ] && [ "$latest" != "null" ] || { echo "::error::no device-plugin release resolved"; exit 1; }
```

The value then reaches shell text through `${{ }}` substitution at lines 139,
160, 173 and 193 (`git commit -m "chore: bump ... device-plugin→${{ steps.nvidia_plugin.outputs.latest }}"`).
Git ref names allow `$(` and backticks: `git check-ref-format 'refs/tags/v1;$(id)'`
succeeds. So a tag named `v0.21.0$(...)` on NVIDIA's repo would execute in a job
that holds `contents: write`, `packages: write` and the Docker Hub token.

The Intel step already validates its value (line 82, `^[0-9]+\.[0-9]+\.[0-9]+$`
after `ltrimstr("v")` strips the prefix at line 81). The k3s, Gaudi and CUDA
values are Docker Hub tags, whose charset (`[A-Za-z0-9_.-]`) cannot carry shell
syntax.

**Fix:** validate the same way the Intel step does,
`[[ "$latest" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 1`. Also pass every step
output into `run:` through `env:` (`env: NV: ${{ steps.nvidia_plugin.outputs.latest }}`
and then `"$NV"`) and not through inline `${{ }}`.

A related design point: the job bumps to the newest upstream, commits to `main`
and pushes `:latest` in one unattended run (lines 156-233). Nothing reviews or
tests the bump before it ships. Consider opening a PR for bumps and letting CI
gate them.

### M-7 Info: pod overrides JSON built by string interpolation

`scripts/k3d-gpu:804`:

```bash
        "{\"spec\":{\"containers\":[{\"name\":\"${name}\",\"image\":\"${DEVICE_TEST_IMAGE}\",\"resources\":{\"limits\":{\"${resource}\":1}},\"command\":[\"ls\",\"-l\",\"${path}\"]}]}}" \
```

Lines 818 (`TEST_IMAGE`), 845 (`DEVICE_TEST_IMAGE`, plus a label built from
`/proc/cpuinfo` flags) and 853 follow the same pattern.
`K3D_GPU_DEVICE_TEST_IMAGE`/`K3D_GPU_TEST_IMAGE` come from the environment
(lines 44-45) and are pasted into the JSON unescaped. With a stub `kubectl`
that records `--overrides`, a value that closes the string and reopens it in a
real container field (`imagePullPolicy`, so no unknown field is involved)
yields valid JSON with an added `securityContext`:

```
$ K3D_GPU_VENDORS=npu K3D_GPU_DEVICE_TEST_IMAGE='busybox:1.37","securityContext":{"privileged":true},"imagePullPolicy":"IfNotPresent' k3d-gpu test
--overrides={"spec":{"containers":[{"name":"npu-test","image":"busybox:1.37","securityContext":{"privileged":true},"imagePullPolicy":"IfNotPresent","resources":{"limits":{"npu.intel.com/accel":1}},"command":["ls","-l","/dev/accel"]}]}}
```

The real `kubectl` never sends it. `run_pod` (line 783) passes the same value
as `--image`, and `kubectl run` checks `--image` against the anchored
`distribution/reference` pattern in `Validate()` before it contacts the
apiserver (`kubectl/pkg/cmd/run/run.go:301` on master; line 280 at
`kubernetes-1.20.0`). Checked with kubectl v1.35.2 against a closed port, no
cluster:

```
$ kubectl --server=http://127.0.0.1:1 run npu-test --restart=Never --image='busybox:1.37","securityContext":{"privileged":true},"imagePullPolicy":"IfNotPresent' --overrides='{}'
Error: Invalid image name "busybox:1.37\",\"securityContext\":{\"privileged\":true},\"imagePullPolicy\":\"IfNotPresent": invalid reference format
```

A clean `--image=busybox:1.37` gets past that check and fails only on the
connection, so the rejection is the image check. The other interpolated values
are constants, resource names from `vendor_resources`, or label keys built from
`/proc/cpuinfo` flags; none carries a quote. So no env value reaches the pod
spec. This is Info: the safety rests on `kubectl run` validating `--image`, not
on the script.

**Fix (hardening):** validate image references once at startup, so a bad value
fails with a clear message and a future change to `run_pod` cannot reopen the
injection:

```bash
valid_image() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._/:@+-]*$ ]]; }
valid_image "${TEST_IMAGE}" || err "K3D_GPU_TEST_IMAGE '${TEST_IMAGE}' is not a valid image reference"
valid_image "${DEVICE_TEST_IMAGE}" || err "K3D_GPU_DEVICE_TEST_IMAGE '${DEVICE_TEST_IMAGE}' is not a valid image reference"
```

Add a case to `tests/detect_test.sh` that asserts `k3d-gpu test` errors on the
value above.

### M-8 Low: `test` acts on whatever kube-context is current

`scripts/k3d-gpu:782-783`:

```bash
    kubectl delete pod "${name}" --ignore-not-found >/dev/null 2>&1 || true
    kubectl run "${name}" --restart=Never --image="${image}" --overrides="${overrides}" >/dev/null || return 1
```

`cmd_test` (line 808 onward), `resource_counts` (line 709) and `verify_gpus`
(line 741) call bare `kubectl`. After `kubectl config use-context prod`,
`k3d-gpu test` deletes any pod named `nvidia-smi-test`, `npu-test` and so on
in that cluster's default namespace, then schedules test pods there.
report-bench already uses the safer form,
`kubectl --context "k3d-${CLUSTER}"` (report-bench `scripts/k3d-gpu:1115`).

**Fix:** add `KUBECTL=(kubectl --context "k3d-${CLUSTER}")` and use
`"${KUBECTL[@]}"` in every call. k3d names the context `k3d-<cluster>`.

### M-9 Low: `ENABLE_TEST_APIS=1` shipped in the balloons manifest

`share/nri-balloons.yml:1786-1787`:

```yaml
          - name: ENABLE_TEST_APIS
            value: "1"
```

The values never asked for this. Upstream chart v0.14.0
`deployment/helm/balloons/templates/daemonset.yaml:95` reads
`{{- if ".Values.plugin-test.enableAPIs" }}`. That condition is a quoted string
literal, so it is always true, even with the default
`plugin-test.enableAPIs: false` (`values.yaml:108-109`).

With the variable set, nri-plugins v0.14.0 does two things:

- It registers `/e2e-test-controller-state` on the instrumentation HTTP server
  (`pkg/resmgr/control/e2e-test/e2e-test.go:146-147`). That server listens on
  `httpEndpoint: :8891` (`share/nri-balloons.yml:1919`).
- It accepts fake topology hints from any pod's `test.` annotation
  (`pkg/resmgr/cache/container.go:197,278-297`).

The pod is not `hostNetwork`, so the endpoint is on the pod IP, open to every
pod.

**Fix:** delete lines 1786-1787. Note in the file header (line 3) that every
re-render must drop `ENABLE_TEST_APIS`. Report the template bug upstream:
`if ".Values..."` should be `if (index .Values "plugin-test" "enableAPIs")`.

UNVERIFIED (needs a live hybrid node):
`kubectl run probe --rm -i --image=busybox:1.37 --restart=Never -- wget -qO- http://<balloons-pod-ip>:8891/e2e-test-controller-state`.

### M-10 Low: balloons gets read-write host `/sys` and `/proc` as uid 0

`share/nri-balloons.yml:1802-1805` (mounts) and `1820-1827` (hostPaths) mount
`/sys` and `/proc` at `/host/sys` and `/host/proc` with no `readOnly`. In a k3d
node, `/sys` is the host's sysfs, because the node is privileged. The image runs
as `User: "0"` (from the image config of the pinned digest). The container drops
all capabilities (lines 1791-1794). Even so, uid 0 can still write root-owned
`0644` sysfs files such as `/sys/devices/system/cpu/cpuN/online`. This matches
upstream (`templates/daemonset.yaml:129-132`, no `readOnly`). Our policy turns
off every host-wide control that would need the write: RDT is off (line 1879),
and there is no cpufreq or uncore config.

**Fix:** add `readOnly: true` to the `hostsysfs` and `hostprocfs` mounts, and
add `runAsNonRoot` if the plugin tolerates it.

UNVERIFIED: whether balloons v0.14.0 still pins CPUs with read-only mounts.
Probe: apply the change, then run `k3d-gpu test` on a hybrid host. The
`cores-performance-test` and `cores-efficient-test` pods must still land
inside their core type (`scripts/k3d-gpu:857-859` checks this).

### M-11 Low: SA token automounted; Gaudi plugin privileged

`share/gaudi-device-plugin.yml:43-44`:

```yaml
          securityContext:
            privileged: true
```

The pod specs at `share/gaudi-device-plugin.yml:32` and
`share/nvidia-device-plugin.yml:24` lack `automountServiceAccountToken: false`.
Every Intel manifest already sets it (for example
`share/intel-gpu-plugin.yml:28`). Device plugins talk only to the kubelet
socket and need no API token. `privileged: true` matches Habana's published
`habana-k8s-device-plugin.yaml` (`privileged: true` under `securityContext`).

**Fix:** add `automountServiceAccountToken: false` to both pod specs. Keep
`privileged` until a live Gaudi test shows the plugin works without it.

UNVERIFIED: whether the Gaudi plugin enumerates `/dev/accel` when not
privileged. The cluster has no Gaudi.

### M-12 Low: build-time apt trust and pinning

`Dockerfile:19-23`:

```dockerfile
    && curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
    && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
...
    && apt-get update && apt-get install -y nvidia-container-toolkit-base nvidia-container-toolkit nvidia-container-runtime util-linux \
```

There are three problems:

- Every build trusts whatever key the URL serves.
- Line 20 uses `curl -s` without `-f`, so an HTTP error page is written into
  the apt sources list.
- The toolkit version floats between rebuilds.

**Fix:**

- Check the key fingerprint before dearmoring. The key served on 2026-10-16
  has fingerprint `C95B 321B 61E8 8C18 09C4 F759 DDCA E044 F796 ECB0`, from
  `gpg --show-keys` on the fetched key.
- Use `curl -fsSL` on line 20.
- Pin the packages, for example
  `nvidia-container-toolkit=1.20.1-1 nvidia-container-toolkit-base=1.20.1-1`.
  1.20.1-1 is the newest `nvidia-container-toolkit` in `stable/deb/amd64/Packages`.

### M-13 Low: AUR source is a mutable tag with no checksum

`packaging/aur/PKGBUILD:16-17`:

```bash
source=("git+https://github.com/88plug/k3d-gpu.git#tag=aur-v${pkgver}")
sha256sums=('SKIP')
```

Anyone with push rights can move the tag. AUR users would then build different
code under the same version. The launcher runs with the user's docker access,
which is root-equivalent.

**Fix:** have `auto-release.yml` (lines 52-57) also write
`#commit=<sha of the release commit>`, or pin with the `?signed` fragment
together with `validpgpkeys=()`.

### M-14 Low: dependabot auto-merge is gated on `github.actor`

`.github/workflows/dependabot-auto-merge.yml:11-15`:

```yaml
    if: github.actor == 'dependabot[bot]'
    ...
        run: gh pr merge --auto --squash "$PR_URL"
```

GitHub's own examples gate on `github.event.pull_request.user.login == 'dependabot[bot]'`
plus `dependabot/fetch-metadata`. See "Automating Dependabot with GitHub
Actions" on docs.github.com.

`github.actor` is whoever triggered the event, not the PR author. Every update
type is auto-merged, including major bumps of `docker/login-action`, which
holds the Docker Hub token.

**Fix:** gate on the PR author. Add `dependabot/fetch-metadata` pinned by SHA,
and merge only when `steps.metadata.outputs.update-type != 'version-update:semver-major'`.
Make sure branch protection requires CI, so `--auto` waits for CI to pass.

### M-15 Info: balloons ClusterRole breadth

`share/nri-balloons.yml:1645-1666` grants:

- `nodes`: `get`, `watch`;
- `nodes/status`: `get`, `patch`, `update`, on all nodes;
- `noderesourcetopologies`: `create`, `get`, `list`, `update`, `delete`.

This matches upstream `templates/clusterrole.yaml` v0.14.0 exactly. The
namespaced Role (lines 1702-1716) only reads `balloonspolicies` and updates
their status. On a single-node dev cluster the blast radius is that one node.
Nothing in the cluster consumes `NodeResourceTopology` (no topology-aware
scheduler).

**Fix (optional):** set `agent.nodeResourceTopology: false` in the render
values (line 1853 in the output) and drop the `topology.node.k8s.io` rule.
UNVERIFIED that balloons runs cleanly with NRT export off.

### M-16 Info: privileged nodes, no Pod Security admission

`scripts/k3d-gpu:613-614` states the design: "k3d node containers run
privileged, so the host /dev tree ... is visible". Any pod that may use
`hostPath` or `privileged: true` is host root. No namespace sets a
`pod-security.kubernetes.io/enforce` label. That is acceptable for a
single-user dev cluster, provided the API is not exposed (M-3) and workloads
are trusted.

**Fix (optional):** label workload namespaces that the launcher creates
(agent-stack `k3d-gpu-agent`) with
`pod-security.kubernetes.io/enforce: baseline`.

### Reviewed, no finding (main)

- **hostPaths for the Intel plugins:** each mounts only its device dir,
  `/var/lib/kubelet/device-plugins` and the matching sysfs subtree.
  `/dev/char` and `/sys/class/*` are `readOnly`. Capabilities are dropped,
  the root filesystem is read-only and seccomp is `RuntimeDefault`.
- **QAT plugin:** mounts `/sys/bus/pci` read-write, which upstream needs to
  bind VFs to vfio-pci. `/sys/kernel/debug` is read-only (lines 63-79).
- **`/dev/char` into the node:** read-only and only when DSA/IAA is active
  (`scripts/k3d-gpu:638-640`).
- **No secrets:** none in `share/`, `scripts/`, `Dockerfile`, `README.md` or
  the workflows; workflows read only `secrets.*`.

---

## claude/agent-stack (`0e0c141`, `scripts/k3d-gpu`)

The in-flight worktree differs from `0e0c141` only in the warm-up. It now uses
`kubectl exec ... curl -d "${body}"`, which passes the body as one argv item,
and in one log line. The findings apply to both versions.

### A-1 Medium: OVMS OpenAI endpoint unauthenticated and open to every pod

Lines 1060-1068 and 1090-1102:

```yaml
          args:
            - --rest_port=8000
...
          ports:
            - name: http
              containerPort: 8000
...
kind: Service
...
  ports:
    - name: http
      port: 8000
```

OVMS v2026.4.1 binds REST to `0.0.0.0` by default
(`src/cli_parser.cpp:97-100`, `default_value("0.0.0.0")`). It requires no auth
unless `--api_key_file` or the `API_KEY` env is set (`cli_parser.cpp:198-201`
and `660-662`; `docs/security_considerations.md:14,26-27`). The Service is
ClusterIP, so it is not reachable from outside the cluster. Any pod in any
namespace can still drive generation and read `/v1/config`. No NetworkPolicy
exists.

The suggested `kubectl port-forward` (line 1126) binds `127.0.0.1` by default,
which is fine.

**Fix:** generate a key once into a Secret, for example
`kubectl -n k3d-gpu-agent create secret generic ovms-api-key --from-literal=key=$(head -c 24 /dev/urandom | base64)`.
Pass it as `env: [{name: API_KEY, valueFrom: {secretKeyRef: {name: ovms-api-key, key: key}}}]`.
Print the retrieval command in `agent up` and send
`Authorization: Bearer` in the warm-up. `/v2/health/ready` (line 1072) is not
under `/v3`, so the readiness probe is unaffected.

Also add a NetworkPolicy that admits ingress to `app: ovms` only from its own
namespace. k3s enforces NetworkPolicy through its embedded controller.

UNVERIFIED: kubelet readiness probes still pass under that policy. Probe:
`kubectl -n k3d-gpu-agent get pod -l app=ovms -o wide` should show `READY 1/1`
after the policy is applied.

### A-2 Medium: model weights and chat template pulled from a moving HEAD

Line 870 sets `AGENT_DEFAULT_MODEL="OpenVINO/Qwen3-4B-int4-ov"`. Lines
1047-1048 run `--pull` / `--source_model=${AGENT_MODEL}` with no revision. Each
fresh PVC fetches whatever the Hugging Face repo serves that day, including the
tokenizer and the Jinja chat template the server executes. OVMS 2026.4.1
`docs/parameters.md` documents no revision option for `--pull`; it documents
only `HF_ENDPOINT` (line 139).

**Fix:**

- Record the expected commit as `AGENT_MODEL_REVISION` (from
  `https://huggingface.co/api/models/OpenVINO/Qwen3-4B-int4-ov/revision/main`,
  field `sha`) and check it after the pull. Fail the init container on a
  mismatch.
- Or pre-fetch the pinned revision into the PVC with a digest-pinned
  downloader init container, and run OVMS with `--model_path` only.

UNVERIFIED: whether `--pull` leaves git metadata in
`/models/OpenVINO/Qwen3-4B-int4-ov` that can be checked. Probe:
`kubectl -n k3d-gpu-agent exec deploy/ovms -c ovms -- ls -a /models/OpenVINO/Qwen3-4B-int4-ov`.

### A-3 Low: manifest YAML and warm-up JSON built from env

Lines 986-1102 render a heredoc that interpolates `K3D_GPU_AGENT_MODEL`,
`_MODEL_NAME`, `_STORAGE`, `_MEMORY`, `_CPUS`, `_TOOL_PARSER`,
`_REASONING_PARSER` and `_IMAGE` unescaped. Line 1109 builds JSON from
`AGENT_MODEL_NAME`. Verified with stubbed `kubectl` and
`k3d-gpu agent manifest`:

```
$ K3D_GPU_AGENT_DEVICE=CPU K3D_GPU_AGENT_MEMORY=$'6Gi\n          securityContext:\n            privileged: true' k3d-gpu agent manifest
...
          resources:
            requests:
              cpu: "1"
              memory: 6Gi
          securityContext:
            privileged: true
```

**Fix:** validate each value before rendering, and add one test per variable
to `tests/detect_test.sh` that asserts `agent manifest` errors.

| Variable | Pattern |
|---|---|
| model | `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$` |
| model name, parsers | `^[A-Za-z0-9._-]*$` |
| storage, memory | `^[0-9]+(\.[0-9]+)?(Ki\|Mi\|Gi\|Ti\|k\|M\|G\|T)?$` |
| CPUs | `^[0-9]+(\.[0-9]+\|m)?$` |
| image | the M-7 `valid_image` pattern |

### A-4 Low: OVMS pod hardening

Lines 1034-1038 set only `runAsUser: 5000`, `runAsGroup: 5000` and
`fsGroup: 5000`. Neither container (1043-1059) sets:

- `allowPrivilegeEscalation: false`;
- `capabilities: {drop: [ALL]}`;
- `seccompProfile: {type: RuntimeDefault}`.

The pod spec also lacks `automountServiceAccountToken: false`.

**Fix:** add all four fields. The Intel plugin manifests in `share/` already
use the same block.

### Confirmed good

`AGENT_IMAGE` (line 869) pins `openvino/model_server@sha256:a928903f...`.
`docker buildx imagetools inspect openvino/model_server:2026.4.1-gpu` returns
that exact digest.

---

## claude/report-bench (working tree, uncommitted)

### R-1 Low: root reader runs on a caller-chosen path

`bench/energy.sh:59-63` and `81`:

```bash
ELEVATE=()
if [ ! -r "${ZDIR}/energy_uj" ]; then
    ELEVATE=(sudo)
...
exec 4> >(${ELEVATE[@]+"${ELEVATE[@]}"} bash -c "${READER_SCRIPT}" _ "${ZDIR}/energy_uj" "${INTERVAL}" > "${SAMPLES}")
```

The script builds `ZDIR` from `--zone` (line 31) and `ENERGY_POWERCAP`
(line 25). The root `bash` then reads `$(< "$1")` and echoes the content into
a user-readable temp file (lines 54-55). A symlinked `energy_uj` therefore
copies any root-only file to the user. This only matters to someone who has
sudo for this script and not general root. Any sudoers rule written for it
would grant `bash -c`, which is full root.

`energy_uj` is root-only because of CVE-2020-8694, the RAPL power side channel
(PLATYPUS). Do not "fix" this with a chmod or udev rule on `energy_uj`.

**Fix:** when elevating:

- require `[[ "${ZONE}" =~ ^intel-rapl(:[0-9]+)+$ ]]`;
- ignore `ENERGY_POWERCAP`, which is for tests only;
- `realpath` the file and require it under `/sys/devices/virtual/powercap/`;
- run a fixed, non-shell reader: `sudo cat "${file}"` in a loop driven from
  the unprivileged side, or a small root-owned helper that takes only the zone
  name.

### Confirmed good

- **JSON encoding:** `json_str` escapes backslash, quote and control
  characters (`scripts/k3d-gpu:858`).
- **HTML embedding:** `report_html` escapes `</` before embedding the JSON
  (`1153-1154`), and the page renders through `textContent` only.
- **Context:** `report_cluster` pins `--context "k3d-${CLUSTER}"` (`1115`).

---

## claude/nfd-k3s-bump (working tree, uncommitted)

### N-1 Low: all of host `/boot` mounted into the node

`scripts/k3d-gpu:644-646`:

```bash
    for src in lib/modules boot etc/os-release; do
        if [ -e "${SYSROOT}/${src}" ]; then
            create_args+=(--volume="${SYSROOT}/${src}:${NFD_HOST_DIR}/${src}:ro@server:*;agent:*")
```

nfd-worker needs only `/boot/config-<release>`, per the manifest header
(`share/node-feature-discovery.yml:9`). The mount also exposes kernel images
and initramfs to the node, which may embed keys. The mount is read-only, and
the privileged node can already reach host block devices, so the added exposure
is small.

**Fix:** mount only
`/boot/config-$(kernel_release)` → `${NFD_HOST_DIR}/boot/config-$(kernel_release)`,
when that file exists.

### Confirmed good

- **Dockerfile pin:** `FROM rancher/k3s:$K3S_TAG@$K3S_DIGEST` with
  `v1.37.1-k3s1@sha256:ca7f37d9...`. The registry index digest matches.
- **NFD image:** pinned by digest (`node-feature-discovery.yml:24` and the
  DaemonSet/Deployment images).
- **NFD RBAC:** matches upstream v0.19.0.
  - The nfd-worker Role is namespaced: `nodefeatures` create/get/update/delete
    and `pods` get (lines 851-869).
  - The nfd-master ClusterRole patches `nodes` and `nodes/status`, which it
    needs to apply labels (lines 900-953).
- **NFD worker mounts:** all host mounts are `readOnly`. The container sets
  `runAsNonRoot`, `readOnlyRootFilesystem` and drops all capabilities.

---

## EAT on k3d (claude/eat-on-k3d and the deep-dive proposal)

### E-1 Medium: proposed port maps publish the EAT ingress on all interfaces

`/tmp/claude-1000/wave2/EAT_DEEPDIVE.md:73` proposes:

> maps `-p 80:80@server:0 -p 443:443@server:0`;

k3d's `-p` format is `[HOST:][HOSTPORT:]CONTAINERPORT[/PROTOCOL][@NODEFILTER]`
(`k3d cluster create --help`, v5.9.0). Without `HOST`, Docker publishes on
`0.0.0.0`. That exposes the toolkit's ingress-nginx (hostPort 80/443, per the
deep-dive at line 13) to the LAN, with the gateway and model endpoints behind
it.

**Fix:** use `-p 127.0.0.1:80:80@server:0 -p 127.0.0.1:443:443@server:0` and
make `0.0.0.0` an explicit opt-in.

### E-2 Low: node labels validated only as `?*=*`

eat-on-k3d `scripts/k3d-gpu:642-645`:

```bash
    IFS=',' read -r -a extra <<< "${NODE_LABELS// /}"
    for label in ${extra[@]+"${extra[@]}"}; do
        [[ "${label}" == ?*=* ]] || err "K3D_GPU_NODE_LABELS entry '${label}' is not key=value"
        create_args+=(--k3s-node-label="${label}@server:*;agent:*")
```

There is no shell injection: each label is a separate argv element. A value
containing `@` or `;` still rewrites the k3d node filter, though, and a value
can set the gate labels that schedule baked privileged DaemonSets, such as
`habana.ai/gaudi.present=true`.

**Fix:** require the Kubernetes label grammar,
`^([a-z0-9]([-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?=([A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?)?$`.

---

## claude/igpu-sharing (working tree, uncommitted)

No finding.

- `intel_config_error` validates `K3D_GPU_INTEL_SHARED` (a positive integer),
  `K3D_GPU_INTEL_ALLOCATION` (an enum) and `K3D_GPU_INTEL_MONITORING`
  (`0|1`) before any value reaches the manifest. `cmd_up` errors on any
  problem.
- The `docker exec ... sh -c` that rewrites the baked manifest interpolates
  only constant paths.
- The branch's own doctor text states the isolation trade-off: shared iGPU pods
  get the whole device, and `gpu.intel.com/monitoring` grants every GPU.

---

## Verification

How each finding was checked:

- **Line citations:** each was read from the ref named in Scope. main citations
  come from `origin/main` @ `ba81920`; agent-stack citations come from
  `git show claude/agent-stack:scripts/k3d-gpu`.
- **Image and action pins:** image digests came from
  `docker buildx imagetools inspect`. Action commits came from
  `gh api repos/<repo>/commits/<ref>`.
- **Upstream behaviour:** read from tagged source via `gh api .../contents/<path>?ref=<tag>`:
  - nri-plugins v0.14.0;
  - OVMS v2026.4.1;
  - k3d v5.9.0;
  - k3s v1.34.1+k3s1.
- **Injection PoCs (M-7, A-3):** run against the real scripts with a stub
  `kubectl` on `PATH`. No cluster and no network were involved. A stub skips
  `kubectl`'s own client-side checks, so M-7 was then re-run with the real
  `kubectl` v1.35.2 against a closed port; its `--image` check rejects the
  payload (see M-7).
- **Not run:** no cluster, k3d or inference server was started for this audit.
  Each item marked UNVERIFIED names its live probe.
