# Canary

Every engine or node-image bump, whether a bot or a person opened it, is checked at
runtime before it merges. Engines move fast: a vLLM bug today is fixed tomorrow, and a
flag the launcher passes today is renamed tomorrow. Before this canary, no CI job built the
node image, started a cluster, or pulled and served an engine. Every engine path in the
tests was a stub.

`.github/workflows/canary.yml` (name `Canary`) runs at 03:29 UTC every night, on pull
requests that touch a pin or the launcher, and on dispatch (`engines`: ids or `auto`;
`attempt`). It runs only on GitHub-hosted runners. It has read-only permissions and no
secrets.

| Kind | Engines | What it proves |
|---|---|---|
| runtime | `vllm-cpu`, `ovms-cpu`, `llama-cpu`; `eat-vllm-cpu` (informational) | The pinned image serves a pinned Qwen3-0.6B the way the launcher runs it. It returns one completion (HTTP 200, `finish_reason` stop or length, tokens > 0, the text matches `/Paris/`) and one stream (at least 2 chunks, then `[DONE]`), and the server reports the version its tag names. |
| surface | `vllm-xpu`, `llm-scaler`, `gaudi-vllm`, `llama-sycl`, `llama-vulkan`, `llama-full`, `dlstreamer`, `sglang-xeon`, `npu-umd`, `ovms` (and the runtime engines first) | Every flag and env name the launcher and `console/model_opt.py` emit (`canary/flags.sh`) exists in the image, and the constants derived from it still match: `console/plan.py`'s vLLM versions, and the NPU driver and firmware date. No device is needed. |
| node | `node` | The root Dockerfile builds. `k3d cluster create --image` it with the k3d `packaging/install.sh` pins. Within 5 minutes the node is Ready, the kube-system pods are up, every baked manifest is in place and every DaemonSet they define exists. |

## Files

| File | Role |
|---|---|
| `engines.toml` | One table per engine. Its `locator` points at the pin in the tree (never a copy of it), plus the model and revision, the launcher-equivalent arguments, the golden, the version probe, the flag sources and the derived constants. |
| `plan.py` | Resolves the pins, plans the job matrix and merges the report. |
| `run.sh` | Runtime and surface: disk, CPU flags, model, size, pull, surface, serve, probe, ISA. |
| `probe.py` | The HTTP checks against a serving engine. |
| `surface.py` | The flag, env and derived-constant check. It reads the engine's own `--help`, or for vLLM statically parses `arg_utils.py`, `entrypoints/**/cli_args.py` and `envs.py` inside the image, without importing them. |
| `flags.sh` | Lists the names k3d-gpu emits, per engine family. |
| `node.sh` | The node canary. |
| `goldens/<id>.json` | `{prompt, regex, exact, recorded_with}` for each runtime engine. |

## How names and versions compare

- A flag matches exactly; for vLLM, `--a_b` and `--a-b` are one flag.
- sglang's parser is argparse with abbreviations allowed, and `bench/h54` passes `--tp`.
  So for sglang, a long flag also matches when it begins exactly one option (`--tp` is
  `--tp-size`). Once a second option begins with it, argparse calls it ambiguous, and so
  does the canary. vLLM flags never match by prefix, so a renamed flag cannot hide behind
  a longer one.
- A derived constant matches the image's version by release (`0.32.0` = `0.32.0+cpu`).
  A setuptools-scm build of a tag with patches applied, `X.Y.(Z+1).dev0+g<commit>.d<date>`,
  is release `X.Y.Z`: intel/llm-scaler-vllm 1.5 reports `0.26.1.dev0+g568afb3a1.d20260907.xpu`,
  which is vLLM v0.26.0's commit with Intel's patches. A build commits after a tag
  (`.dev1` and up) matches nothing.

## Which engines run

`schedule` runs every engine.

A pull request or `engines=auto` runs the engines that differ from
`merge-base(origin/main, HEAD)` in any of these:

- the resolved image reference;
- their `engines.toml` table;
- their golden;
- a derived constant;
- a flag `flags.sh` names for them.

The node engine also runs when the Dockerfile, `share/` or `packaging/install.sh` change. A
change to the harness itself runs every engine.

`engines=a,b` runs exactly those engines.

## Verdicts (`canary-result` artifact, `canary-result.json`)

```json
{"sha": "...", "ref": "...", "engines": {"<id>": {"kind": "runtime|surface|node",
  "pinned_ref": "...", "verdict": "pass|fail|isa-skip|too-big|unparseable",
  "hard": true, "exact": true, "cpu_flags": ["avx2"], "detail": "..."}}}
```

Only `verdict` pass with `hard` true is green. The report job fails unless every selected
non-informational engine is green.

| Value | Meaning |
|---|---|
| `exact` false | The text differs from the golden, but the hard checks passed. It is still green, but it is not evidence for an automatic merge. |
| `isa-skip` | The runner's CPU lacks an instruction set the engine needs: no `avx512f`, and the engine's `isa_signal` in its log, or SIGILL. The workflow dispatches itself again, up to `attempt` 3. Hosted runners vary in CPU, so a later attempt may land on one with AVX-512. |
| `too-big` | The image needs more disk than the runner has, even after the toolchains are deleted. |
| `unparseable` | A help output or a source the surface check could not read. It is red, never skipped. |

## Recording a golden

`exact` is the completion text the hosted runner produced with temperature 0, top_k 1 and
seed 0. Take it from the `detail` (`text '...'`) of a green run, and write it with the
image, model and CPU it was recorded on (the job log's `cpu:` line names the CPU). A `null`
`exact` means nothing is recorded yet. The vLLM goldens come from an AVX2-only runner; a
runner with AVX-512 runs other kernels and may produce other text. That clears `exact`,
which sends the bump to review, but it is never red.

## Adding an engine

1. Add an `[[engine]]` with a `locator` that matches exactly one reference.
   `tests/canary_spec_test.sh` checks it against the tree.
2. If the launcher emits flags for a new family, teach `flags.sh` its source. A flag
   literal in `console/model_opt.py` or `console/fit.py` that no rule attributes to an
   engine fails `flags.sh`.
