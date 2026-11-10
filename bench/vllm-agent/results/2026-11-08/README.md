# The vLLM agent on an Arc Pro B70, unpatched (2026-11-08)

The end state of `K3D_GPU_AGENT_BACKEND=vllm k3d-gpu agent up` on one Arc Pro B70, through
the launcher alone: no `kubectl patch`, no warm-up sent by hand, the default image and
model. Two launchers ran in one hold:

- `main-0.1.94`: release 0.1.94, the first release with
  [#339](https://github.com/88plug/k3d-gpu/pull/339) (no Service links in the pod, and
  the warm-up sent from inside the pod as JSON).
- `pr357-0985b179`: the head of [#357](https://github.com/88plug/k3d-gpu/pull/357), which
  moves the image to `v0.33.0`. That head was regenerated onto main after #339 merged.

Both passed. Releases 0.1.21 through 0.1.93 do not serve with vLLM: their Service gives
the pod `VLLM_PORT=tcp://...`, on which vLLM exits, and their warm-up reaches vLLM with no
`Content-Type`, which it answers with 400.

This record covers vLLM XPU on Arc only. The Gaudi path and llm-scaler have not run on
hardware, and neither has tensor parallelism above 1.

## Host

crescent: Xeon 6741P, 4x Arc Pro B70 on the `xe` driver, kernel `7.0.0-31-generic`, Ubuntu
26.04.1 LTS. The hold took `~/k3dgpu-bench.lock`. It freed the cards by stopping
`qwen38-a-hr`, `qwen38-b-hr` and `aiperf010` with `docker stop -t 120`, after a snapshot,
a check that neither server had a request in the last 10 minutes, and each server's
baseline answer ("Paris"). Afterwards it started them again, and both servers gave the same
answer. All four cards read 0 GiB before each run and after each teardown.

## Launchers and versions

| | `main-0.1.94` | `pr357-0985b179` |
|---|---|---|
| Launcher | `/usr/bin/k3d-gpu` 0.1.94 from `k3d-gpu_0.1.94-1_all.deb` (sha256 `60a1dc5bbc5e6f2a439c34c9ec19dc2c916dcd35c5bec9d46752ee2ebcfd8dec`, the `aur-v0.1.94` release asset) | `scripts/k3d-gpu` 0.1.95 from a clean checkout of `0985b17902e507b13d8460970fbe2a15051ac5c9` (sha256 `e2d97e1960a04516d83ddc1c21c4142a406a2be6797ca1f102bd5feb1b495438`) |
| How installed | `packaging/install.sh` from main at `e5f55b29` (sha256 `62c30da91025f5af25557e5e49da8c496591d98f3fb0f9a393c673b65edbbc55`), which pins 0.1.94; it also put k3d v5.9.0 and kubectl v1.37.2 in `~/.local/bin` | `scripts/checkout.sh` (`git fetch --depth 1` of the commit, no changes) |
| Node image | `cryptoandcoffee/k3d-gpu:0.1.94` (`sha256:8630e14556e70111a097dcf22d15a7484bc94fa19a2d4b04b311ec60778d438e`) | `cryptoandcoffee/k3d-gpu:0.1.95` (`sha256:65499cbe202dca63c28029e03c4eb5139f5de31f5f369d915494f06e6f4dd3fa`) |
| Agent image | `vllm/vllm-openai-xpu:v0.32.0@sha256:20509fabe50dda62c09c0786d1a6effa299d3dbdddcc49331b93e65144652a8c`, vLLM 0.32.0 | `vllm/vllm-openai-xpu:v0.33.0@sha256:be3a9aea234be64ffc2816ac561c597e0f0b1f44a61a7f4912780e4d77450086`, vLLM 0.33.0 |
| Model | `Qwen/Qwen3-4B` at `1cfa9a7208912126459214e8b04321603b3df60c`, float16, `hermes` tool parser, `qwen3` reasoning parser | the same |

The release package and the k3d and kubectl binaries the installer added were removed after
the hold (`install-0.1.94/after-uninstall.txt`).

## Results

| | `main-0.1.94` | `pr357-0985b179` |
|---|---|---|
| `k3d-gpu up` | rc 0 in 48 s, allocatable `gpu.intel.com/xe: 4` | rc 0 in 49 s, the same |
| `agent up` | rc 0 in 255 s | rc 0 in 258 s |
| Pod | Ready, 0 restarts, limits `gpu.intel.com/xe: 1`, `enableServiceLinks: false`, no `VLLM_PORT` in its environment, one render node (`renderD129`) | the same, `renderD128` |
| VRAM while serving | 27.50 GiB on 0000:48:00.0, 0 on the other three cards | 27.30 GiB on 0000:12:00.0, 0 on the other three |
| KV cache (vLLM's log) | 137,472 tokens | 131,776 tokens |
| The launcher's warm-up line | `warm-up reply (2s): I am a large language model developed by Alibaba Cloud.` | the same |
| "What is 17 times 23?" | `391` (ends `\boxed{391}`), finish `stop`, 1,426 completion tokens (1,283 reasoning), 22.7 s | the same |
| Tool call | `get_weather` with arguments `{"city": "Paris"}`, finish `tool_calls`, 1.6 s | the same |
| Teardown | `agent down` and `down` rc 0, 0 GiB on all four cards, no k3d containers left | the same |
| `run.sh` verdict | PASS | PASS |

Both requests went through the endpoint `agent up` prints
(`kubectl -n k3d-gpu-agent port-forward svc/vllm 8000:8000`). The chat request had no
system prompt, thinking left on, temperature 0 and `max_tokens` 4096. The tool request
offered one function, `get_weather(city)`, with "What is the weather in Paris right now?"
and `tool_choice: auto`. `run.sh` passes a run only when the pod is Ready with one
`gpu.intel.com/xe`, the launcher printed its warm-up reply, the chat answer contains 391,
and the tool call is `get_weather` with `city` Paris.

## Files

| Path | What |
|---|---|
| `hold.log` | The hold, in order: the lock, the services stopped, each run's driver lines, the restart and the baseline answers |
| `takeover-restore.txt` | The stop and restart logs of the three services, and the names stopped |
| `queue` | The runs the hold took, as `<label>=<launcher>` |
| `install-0.1.94/` | The installer's hash and output, `~/.local/bin` and the package before and after, and the removal |
| `main-0.1.94/`, `pr357-0985b179/` | Per run: `driver.log` (its lines of `hold.log`), `up.txt`, `agent-up.txt` (and `.clean.txt`, colour codes stripped), `allocatable.json`, `pods.txt`, `deploy-vllm.yaml`, `describe-pods.txt`, `models.json`, `chat-req.json` and `chat.json`, `tool-req.json` and `tool.json`, `vllm.log`, `vllm-previous.log` (no previous container: 0 restarts), `events.txt`, `pf.log`, `tools.txt`, `agent-down.txt`, `down.txt`, `k3d-containers-after.txt` (empty) |
| `scripts/` | The scripts as they ran on crescent: `hold.sh` runs `takeover-stop.sh`, then `run.sh` for each queued launcher, then `restore.sh`. `install-release.sh`, `checkout.sh` and `uninstall-release.sh` set up and remove the launchers. The one change since the run is a shellcheck directive comment in `uninstall-release.sh` |

The takeover snapshot (`docker inspect` of the three services and their recent logs) stays
on crescent: it holds those services' own configuration.
