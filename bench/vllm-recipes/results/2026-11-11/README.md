# vLLM recipes on an Arc Pro B70 (2026-11-11 and 2026-11-12)

The end state of [#406](https://github.com/88plug/k3d-gpu/pull/406) (PR2 of the vLLM recipes:
each model named to `agent up` takes its tool and reasoning parsers and model flags from
vLLM's recipes) on one Arc Pro B70, against main's launcher. It ran once at each of two heads
of the PR:

- `pr406-0ece1845`: head `0ece1845a780c7ab1da0f2f2b45867510c3bc977` against main
  `25b04b788be5533b00b006054c81525e80b2422b`, 2026-11-11 23:10 to 23:32 UTC.
- `pr406-27b535c1`: head `27b535c1ffebe15ee066761115f762fc0869723d` (main merged into
  `0ece1845`) against main `86f4910e7b641b5a1b7823e9ad01c4035e8304a3`, 2026-11-12 00:17 to
  00:39 UTC.

Both passed: every case served, and the responses on the PR head are identical in both runs.
The summaries posted on the PR are
[the 0ece1845 comment](https://github.com/88plug/k3d-gpu/pull/406#issuecomment-6809798035) and
[the 27b535c1 comment](https://github.com/88plug/k3d-gpu/pull/406#issuecomment-6810895134).

## Host

crescent: Xeon 6741P, 4x Arc Pro B70 (`0xe223`) on the `xe` driver, kernel
`7.0.0-31-generic`, Ubuntu 26.04.1. `scripts/driver.sh` took `~/k3dgpu-bench.lock`, froze the
owner's servers as in the other holds on this host (a snapshot, each server's baseline answer
"Paris", `docker stop -t 120` of `aiperf010`, `qwen38-b-hr` and `qwen38-a-hr`, leaving all
four cards at 0 GiB), ran every case in one throwaway cluster, deleted it, started the
servers again in reverse order and checked both answered "Paris" again.

## Cases and versions

One card per case. Each case is `K3D_GPU_AGENT_BACKEND=vllm [K3D_GPU_AGENT_MODEL=<id>]
scripts/k3d-gpu agent up` from a clean checkout of the named commit, three requests sent from
inside the pod to `127.0.0.1:8000/v1/chat/completions` at temperature 0, then `agent down`:

| Label | Launcher | Model |
|---|---|---|
| `P4B` / `M4B` | PR head / main | `Qwen/Qwen3.5-4B` (`main`) |
| `P9B` / `M9B` | PR head / main | `Qwen/Qwen3.5-9B` (`main`) |
| `PDEF` | PR head | the default, `Qwen/Qwen3-4B` at `1cfa9a7208912126459214e8b04321603b3df60c` |
| `DEF-manifest-*` | main and PR head | `agent manifest` of the default model only (no serving) |

| | `pr406-0ece1845` | `pr406-27b535c1` |
|---|---|---|
| Cluster (`k3d-gpu up` from the PR head) | `cryptoandcoffee/k3d-gpu:0.1.106` (`sha256:417e53f3b6c13e26fd084bb75f788ac0e5dedbae28b86413fcd628f329cdc331`) | `cryptoandcoffee/k3d-gpu:0.1.107` (`sha256:db1636ff0353aa3f3ee9c80399dc7aa85ea1dee1d0979c72bf76f002d04b617e`) |
| Engine | `vllm/vllm-openai-xpu:v0.33.0@sha256:be3a9aea234be64ffc2816ac561c597e0f0b1f44a61a7f4912780e4d77450086` (vllm 0.33.0+xpu, torch 2.14.0+xpu, transformers 5.20.2) | the same |
| Recipe ref in the pod annotation | `vllm@960f5c1bb145` | the same |

The node image digests were read on crescent with `docker image inspect`; the records name the
tag (`up.txt`).

## Results

The same in both runs (`<label>-*.json`, `timings.txt`, `cases.txt`):

| | `P4B` | `M4B` | `P9B` | `M9B` | `PDEF` |
|---|---|---|---|---|---|
| Parser arguments | `--enable-auto-tool-choice --tool-call-parser=qwen3_coder --reasoning-parser=qwen3` | none | the same as `P4B` | none | `hermes` / `qwen3` (k3d-gpu's default for the Qwen3 family) |
| `k3d-gpu.io/vllm-recipe` | `Qwen/Qwen3.5-4B@vllm-960f5c1bb145` | none | `Qwen/Qwen3.5-9B@vllm-960f5c1bb145` | none | none |
| `--enforce-eager` / `--trust-remote-code` | once / absent | once / absent | once / absent | once / absent | once / absent |
| Tool call, `tool_choice: auto` | `get_weather` `{"city": "Paris"}`, content null | HTTP 400, `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set` | `get_weather` `{"city": "Paris"}` | HTTP 400, the same | `get_weather` `{"city": "Paris"}` |
| "17 times 23", thinking on | `reasoning` 708 chars, content `\n\n391` | no `reasoning` field; the thinking and `</think>` inside content, which ends `391` | `reasoning` 730 chars, content `\n\n391` | the thinking inside content | `reasoning` 915 chars, content `\n\n391` |
| "Capital of France", thinking off | `Paris` | `Paris` | `Paris` | `Paris` | `Paris.` |

The default model's `agent manifest` from main and from the PR head gave a 0-byte diff in both
runs (`DEF-manifest.diff`). `python3 scripts/eval.py <run>` prints the per-case summary the
PR comments' tables came from: the served arguments, parsers, annotation, the `plan --explain`
recipe lines, the warm-up line and the three answers.

The PR comments carry two findings that the records back and that did not block the change:
`plan --explain` tags the sizing as `k3d-gpu default` (8192 / 0.9) while `agent up` serves
the fit's (`<label>-args.json`: 131072 / 0.93 / 5 seqs for the 4B, 65536 / 0.90 / 5 for the
9B), and `--dtype=float16` loads the bf16 Qwen3.5 checkpoints (`<label>-vllm.log`).

The owner's servers answered "Paris" again after each run (`takeover/verify-*.json`,
`takeover/RESTORED`). `takeover/vram-after-restore.txt` is the restore-time read, during the
servers' first load (card1 and card3 at 26.46 GiB); the PR comments record the per-card VRAM
settled at 15.67 / 14.97 / 15.67 / 14.97 GiB.

## Files

| Path | What |
|---|---|
| `pr406-*/driver.log`, `events.log` | The run in order: the lock, the takeover, each case, the teardown, the restore |
| `pr406-*/commits.txt`, `cases.txt`, `timings.txt`, `result` | The two commits, each case and whether it served, each step's rc and time, the driver's end state |
| `pr406-*/pre.txt`, `up.txt`, `allocatable.json`, `down.txt`, `agent-down-final.txt`, `clusters-after.txt`, `containers-after.txt` | The host before, `k3d-gpu up`, the node's allocatable, the teardown, the k3d clusters and containers left after it (none) |
| `pr406-*/<label>-*` | Per case: `agent-up.txt`, `manifest.yaml` and `.err`, `plan.txt` (`plan --explain`), `deploy-vllm.yaml`, `args.json`, `annotations.json`, `pods.txt`, `describe-pods.txt`, `events-k8s.txt`, `packages.txt`, the three requests and answers (`tool`, `reason`, `chat`, each `.req.json` and `.json`), `models.json`, `vllm.log` and `vllm-final.log`, `vllm-previous.log` (no previous container), `vram-serving.txt`, `agent-down.txt` |
| `pr406-*/DEF-manifest-main.yaml`, `DEF-manifest-pr.yaml`, `DEF-manifest.diff` | The default model's `agent manifest` from each launcher, and their diff (empty) |
| `pr406-*/vram-pre.txt`, `vram-samples.txt`, `vram-after-teardown.txt` | Per-card VRAM before, during and after the cases |
| `pr406-*/takeover/` | From the takeover snapshot: `state-before.txt`, `baseline.txt` and `baseline-*.json`, `vram-before.txt`, `stop-log.txt`, `FREED`, `RESTORE.txt`, `state-after.txt`, `verify-*.json`, `vram-after-restore.txt`, `RESTORED` |
| `scripts/driver.sh`, `scripts/eval.py` | The driver as it ran (one copy; both runs used the same file) and the evaluation the comments used |
| `dl-qwen35-4b.log`, `dl-qwen35-9b.log` | The host-side `hf download` of the two Qwen3.5 checkpoints earlier on 2026-11-11 (snapshots `851bf6e8` and `c2022362`). The runs did not read them: `agent up` downloaded the weights into the cluster's volume |
| `SHA256SUMS` | Every file above; each hash equals the file's hash on crescent |

Left on crescent: each run's `state/` directory (the fit's cache and the agent guard), its
`kubeconfig` (empty after the teardown) and `driver.pid`, and from the takeover snapshot
`docker ps -a` of the host and `docker inspect` of the three services, which hold the owner's
own configuration.
