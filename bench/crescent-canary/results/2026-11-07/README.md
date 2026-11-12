# Hardware canaries on crescent (2026-11-07)

Three bot bump PRs checked on real hardware, because the CI canary runs on a hosted runner
with no Intel GPU. Each run took `~/k3dgpu-bench.lock` and used a throwaway k3d cluster
that it deleted afterwards. The four Arc Pro B70s were free throughout: the owner's serving
containers had been stopped earlier that morning by a separate, owner-approved takeover,
and these runs neither stopped nor started them.

Host: crescent, Xeon 6741P (AMX, AVX10.1/512) with 4x Arc Pro B70 (`8086:e223`) on the `xe`
driver, kernel `7.0.0-31-generic`, k3d v5.9.0.

| Directory | PR | Verdict | Summary on GitHub |
|---|---|---|---|
| `pr321-busybox/` | [#321](https://github.com/88plug/k3d-gpu/pull/321), `busybox` 1.37 to 1.38 as the device test image | pass | [comment](https://github.com/88plug/k3d-gpu/pull/321#issuecomment-6703481783) |
| `pr322-k3s/` | [#322](https://github.com/88plug/k3d-gpu/pull/322), k3s v1.37.2-k3s1 in the node image | pass | [comment](https://github.com/88plug/k3d-gpu/pull/322#issuecomment-6702998906) |
| `pr326-vllm-xpu/` | [#326](https://github.com/88plug/k3d-gpu/pull/326), `vllm/vllm-openai-xpu` v0.33.0 | fail as the PR stood; the faults were the launcher's, fixed by [#339](https://github.com/88plug/k3d-gpu/pull/339) | [comment](https://github.com/88plug/k3d-gpu/pull/326#issuecomment-6703946623) |

All three PRs were closed unmerged; the changes landed through later bump PRs.

## `pr321-busybox/`

`k3d-gpu up`, `k3d-gpu test`, `k3d-gpu down` (cluster `k3dgpu-canary-images`, default
settings) from three detached checkouts, 06:31 to 06:38 UTC:

| Run | Checkout (`sha`) | Node image | `DEVICE_TEST_IMAGE` | `ls -l /dev/dri` in the test pod | up / test / down |
|---|---|---|---|---|---|
| `head/` | `c0234db0`, the PR head when the run was planned | `cryptoandcoffee/k3d-gpu:0.1.89` | `busybox:1.38@sha256:a46257e0...` | `card2`, `renderD129` | 0 / 0 / 0 |
| `main/` | `ae80bbd8`, main (control) | `cryptoandcoffee/k3d-gpu:0.1.89` | `busybox:1.37@sha256:a7dbc319...` | `card3`, `renderD130` | 0 / 0 / 0 |
| `r2/` | `56e979e6`, the change rebased onto 0.1.90 | `cryptoandcoffee/k3d-gpu:0.1.90` | `busybox:1.38@sha256:a46257e0...` | `card4`, `renderD131` | 0 / 0 / 0 |

Each test pod (`gpu.intel.com/xe: 1`) listed one B70's card node and its render node, and
the AMX probe ran `brg_matmul:avx10_1_512_amx` in every run (`test.log`). Per run: `sha`,
`device-test-image.txt`, `k3d-version.txt`, `df.txt`, `up.log`, `node-xe.txt`, `ds.txt` and
`ds-args.txt` (the device plugin), `pods.txt`, `test.log`, `events.txt`, `down.log`,
`clusters-after.txt` (empty), `rc.txt`, and the start and end times. `lock-taken` is the
lock time of the last run.

## `pr322-k3s/`

The PR arm is at the top of the directory, the control in `main/`, both under one lock
(`lock-taken` 05:39:24, `lock-released` 05:47:50 UTC), cluster `k3dgpu-canary-k3s`. The PR
arm's node image was built on crescent from the root Dockerfile at `ddf136f5`
(`k3d-gpu-canary:k3s-ddf136f5`, k3s v1.37.2+k3s1); the control used
`cryptoandcoffee/k3d-gpu:0.1.89` (k3s v1.37.1+k3s1). Each arm ran `k3d-gpu up`,
`k3d-gpu test`, `K3D_GPU_AGENT_DEVICE=CPU k3d-gpu agent up` (OVMS 2026.4.1 serving
`OpenVINO/Qwen3-4B-int4-ov` on the CPU), two chat requests through a port-forward ("Paris"
and "51", `request.log`), `agent down` and `down`; every step exited 0 in both arms. Files:
`up.log`, `test.log`, `agent.log`, `pf.log`, `request.log`, `agent-down.log`, `down.log`,
and the PR arm's `ovms.log`.

## `pr326-vllm-xpu/`

`K3D_GPU_AGENT_BACKEND=vllm k3d-gpu agent up` with the default `Qwen/Qwen3-4B` (revision
`1cfa9a72`) from a clean checkout of the PR head `38abe6d8` (`commit.txt`), node image
`cryptoandcoffee/k3d-gpu:0.1.89`, cluster `k3dgpu-canary-vllm-xpu`:

| Run | What it did | `result` |
|---|---|---|
| `20261107T0537Z/` | Waited for the lock and was stopped by its session to relaunch with a hold-off flag; it never ran | none |
| `20261107T0549Z/` | The PR as it stood: the pod crash-looped (`VLLM_PORT 'tcp://...' appears to be a URI`) until the 600 s rollout deadline | `agent-up-failed` |
| `20261107T0643Z/` | `a-*`: main's image v0.32.0 fails the same way. `b-*`: v0.33.0 with `enableServiceLinks: false` patched in; the engine starts and the launcher's warm-up gets HTTP 400 | `agent-up-failed` |
| `20261107T0656Z/` | `b-*` again, then the warm-up and a chat sent from inside the pod with `Content-Type: application/json`: "I am a large language model developed by Alibaba Cloud." and `391`, 27.28 GiB on one card, no `VLLM_PORT` in the pod | `served` |

Per run: `driver.log`, `events.log`, `pre.txt`, `up.txt`, `allocatable.json`,
`node-images.txt`, the pod's `deploy-vllm.yaml`, `describe-pods.txt`, `pods.txt`,
`events-k8s.txt` and vLLM logs (prefixed `a-` or `b-` where a run has two arms), the
requests' answers, VRAM before, during and after, `agent-down.txt`, `down.txt`,
`clusters-after.txt` and `containers-after.txt` (both empty).

## `scripts/`

Local copies of the scripts that were piped to `ssh crescent bash -s` (none of them is
stored on crescent): `pr321-run.sh` (one run, `head`, `main` or `r2`) and `pr321-acquire.sh`
(takes the lock for it); `pr326-launch.sh`, `pr326-b-launch.sh` and `pr326-c-launch.sh`,
which start the driver of the `0549Z`, `0643Z` and `0656Z` runs (each driver is embedded in
its launcher and logs as `canary-326`, `canary-326b` and `canary-326c`). The script behind
`pr322-k3s/` was not kept, and neither was the launcher of the `0537Z` attempt.

## Integrity

`SHA256SUMS` lists every file here. Each file outside `scripts/` has the same hash as the
file on crescent. Left on crescent: each `pr326-vllm-xpu` run's `kubeconfig` (empty after the
teardown) and `driver.pid`, and `pr321-busybox/holder.pid`.
