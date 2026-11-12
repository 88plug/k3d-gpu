# `eas model add` on an Arc Pro B70 (2026-11-11)

The end state of `k3d-gpu eas model add`, `list` and `remove` from
[#395](https://github.com/88plug/k3d-gpu/pull/395) on one Arc Pro B70, with EAT 2.0 on EAS
v1.2.0 (the `release` profile) in the same cluster. It ran once at each of two heads of the
PR, each from a clean checkout with no local change:

- `pr395-f420655`: head `f420655584771a3814df441d38f9386412eb9209`, 17:41 to 18:24 UTC.
- `pr395-e6ad2a0`: head `e6ad2a0acaf3b1b48f7a23e48e1abb879f829b8b`, the head #395 merged at,
  19:24 to 20:06 UTC.

Both passed (`run.log`: `RESULT PASS`). The summaries posted on the PR are
[the f420655 comment](https://github.com/88plug/k3d-gpu/pull/395#issuecomment-6804114985) and
[the e6ad2a0 comment](https://github.com/88plug/k3d-gpu/pull/395#issuecomment-6806116502).

## Host

crescent: Xeon 6741P, 4x Arc Pro B70 on the `xe` driver, kernel `7.0.0-31-generic`, Docker
29.8.0, k3d v5.9.0, kubectl v1.37.2. Each hold took `~/k3dgpu-bench.lock`. It freed the cards
from the owner's servers: a snapshot, a check that neither server had a request in the last
10 minutes, each server's baseline answer ("Paris"), then `docker stop -t 120` of
`aiperf010`, `qwen38-b-hr` and `qwen38-a-hr`, which left all four cards at 0 GiB. After the
run it started them again in reverse order, and both servers gave the same answer.

## Versions

| | `pr395-f420655` | `pr395-e6ad2a0` |
|---|---|---|
| Launcher | `scripts/k3d-gpu` 0.1.102 | `scripts/k3d-gpu` 0.1.104 |
| Node image | `cryptoandcoffee/k3d-gpu:0.1.102` (`sha256:18a9c4f1827b1b8e92259321dd0e21786d7f3f6154912045338d01e023e2d300`) | `cryptoandcoffee/k3d-gpu:0.1.104` (`sha256:774d49838b31b7b1c62b503328dfc59b00fd753ca4091c00f737713d05cf07a9`) |
| EAS profile `release` (`pins.resolved.json`) | EAS v1.2.0 `a051f00`, enterprise-inference v1.1.0 `d035b1e`, EAT release-2.0.0 `7b827e2` | the same |
| Engine | `vllm/vllm-openai-xpu:v0.33.0@sha256:be3a9aea234be64ffc2816ac561c597e0f0b1f44a61a7f4912780e4d77450086` | the same |
| Model | `Qwen/Qwen3-4B` at `1cfa9a7208912126459214e8b04321603b3df60c`, served as `qwen3-4b`; the fit gave `--max-model-len=20480 --max-num-seqs=7 --gpu-memory-utilization=0.88`; `hermes` tool parser, `qwen3` reasoning parser | the same |

The node image digests were read on crescent with `docker image inspect`; the run records
name the tag (`steps/1-up.log`).

## Results

Each step ran once, with no retry and no hand edit (`steps.tsv`, wall seconds):

| Step | `pr395-f420655` | `pr395-e6ad2a0` |
|---|---|---|
| `k3d-gpu up` (`Accelerators schedulable: intel=4 amx=1`, allocatable `gpu.intel.com/xe: 4`) | rc 0, 44 s | rc 0, 43 s |
| `k3d-gpu eat up` (one PLAY RECAP, `ok=550 changed=168 failed=0`) | rc 0, 1555 s | rc 0, 1512 s |
| Ready wait | rc 0, 6 s | rc 0, 6 s |
| `eas model add Qwen/Qwen3-4B --dry-run` | rc 0, 3 s | rc 0, 1 s |
| `eas model add Qwen/Qwen3-4B` | rc 0, 261 s | rc 0, 261 s |
| `eas model list` (`qwen3-4b  k3d-gpu  Qwen/Qwen3-4B@1cfa9a720891  ready gpu 7x20480`) | rc 0 | rc 0 |
| `eas check --model qwen3-4b` | rc 0, 41 s | rc 0, 41 s |
| `eas model remove qwen3-4b` | rc 0, 9 s | rc 0, 9 s |
| `k3d-gpu eat down` | rc 0, 131 s | rc 0, 130 s |
| `k3d-gpu down` (0 k3d containers left) | rc 0, 9 s | rc 0, 24 s |

What each run shows:

- The model pod was Ready with 0 restarts and `gpu.intel.com/xe: 1` in its limits and
  requests; the node's allocation reads `gpu.intel.com/xe 1 1`, and the pod's `/dev/dri`
  held `card1` and `renderD128` only (`model-pod.json`, `node-allocated.txt`,
  `model-pod-devs.txt`). Per-card VRAM while serving: card1 26.89 GiB, the other three 0.00
  (`vram-serving.txt`). The engine logged the XPU platform, the V2 model runner, Flash
  Attention and an XPU KV cache of 132,096 tokens (`engine-xpu-lines.txt`).
- The EAS gateway's `/v1/models` answered HTTP 200 with `hybrid-router`, `qwen3guard`,
  `qwen2-5-coder-14b` and, after the add, `qwen3-4b` (`models-before.json`,
  `models-after.json`).
- "What is 17 times 23?" through the gateway, temperature 0, `max_tokens` 4096: HTTP 200,
  `finish_reason` `stop`, 1,426 completion tokens, the answer ends `\boxed{391}`
  (`chat-req.json`, `chat.json`).
- `eas check --model qwen3-4b` ran Intel's `validate agent-toolkit` (`ok=238 failed=0`), then,
  with a virtual key it minted and deleted, a chat, a `get_weather` tool call, the guard's
  block of an unsafe prompt and a sandbox run printing 385 (`steps/5-eas-check.log`).
- `eas model remove` put card1 back to 0.00 GiB; `eat down` and `down` exited 0.

The owner's servers both answered "Paris" after the restore (`takeover/restore-*.json`).
`takeover/vram-compare.txt` was read about 70 s after they started, during their first load
(card1 and card3 at 26.46 GiB). The PR comments record the per-card VRAM settled at
15.67 / 14.97 / 15.67 / 14.97 GiB, and crescent's Prometheus (`xe_vram_used_bytes`) read the
same on 2026-11-12 at 08:01 UTC.

## Files

| Path | What |
|---|---|
| `pr395-*/launch.log` | The hold's own lines: the lock, the services stopped, `run.sh` and the restore rc, the per-card VRAM compare, the lock released |
| `pr395-*/hold.log` | The whole hold in order: the takeover, `run.log`'s lines, the restore |
| `pr395-*/run.log`, `steps.tsv`, `results.txt`, `run.rc`, `restore.rc` | `run.sh`'s log, each step's rc and wall seconds, the checks it passed on, its exit code and the restore's |
| `pr395-*/steps/` | Each step's full output (`1-up.log` through `7-down.log`) |
| `pr395-*/header.txt`, `pins.resolved.json`, `overlay.txt`, `choices.txt`, `play-recap.txt` | The commit and versions, the EAS pins, the overlay decisions, the EAS choices `eat up` used, the PLAY RECAP |
| `pr395-*/model-pod.json`, `model-pod-describe.txt`, `model-pod-devs.txt`, `gpu-pods.txt`, `node-describe.txt`, `node-allocated.txt`, `allocatable.json`, `accelerators.txt` | The model pod, its devices, and the node's allocation |
| `pr395-*/engine-vllm.log`, `engine-after-check.log`, `engine-xpu-lines.txt` | The engine log after the add and after `eas check`, and its XPU lines |
| `pr395-*/models-*.json`, `models-*.status`, `chat-req.json`, `chat.json` | The gateway's model list before and after the add, and the chat through it |
| `pr395-*/pods-after-*.txt`, `events-after-model-add.txt`, `samples.txt`, `vram-serving.txt`, `k3d-containers-after.txt` | Every pod after `eat up` and after the add, the cluster's events, load and per-card VRAM each minute, VRAM while serving, k3d containers after `down` (empty) |
| `pr395-*/takeover/` | From the takeover snapshot: `stop-log.txt` (VRAM before, the 10-minute request check, the baseline answers, each stop), `stopped.txt`, `snapshot-time.txt`, `baseline-*.json`, `restore-log.txt`, `restore-*.json`, `vram-compare.txt` |
| `scripts/` | The scripts as they ran on crescent: `checkout.sh` makes the clean checkout; `hold.sh` takes the lock and runs `takeover-stop.sh`, then `run.sh` under its cap, then `restore.sh` |
| `SHA256SUMS` | Every file above; each hash equals the file's hash on crescent |

Left on crescent: each run's `state/` directory (25 MB: the cluster's kubeconfig, the EAS
checkout, its facts and logs), and from the takeover snapshot `docker inspect` of the three
services, their last hour of logs, `docker ps -a` of the host and the telemetry exporters'
full scrapes. Those hold the cluster's credentials or the owner's own configuration,
traffic and telemetry.
