# Canary fixtures

Inputs for `tests/canary_surface_test.py` and `tests/canary_test.sh`, taken from the
upstream releases k3d-gpu pins on 2026-10-29. They are copied verbatim; the tests read
them, nothing imports them.

`vllm-0.32.0/` is a site-packages tree of vLLM v0.32.0 (tag v0.32.0, commit
5df63a7203cdb9328d824a27b682e77c7b9928be, Apache-2.0, SPDX headers kept):

| File | Source |
|---|---|
| `vllm/engine/arg_utils.py` | `vllm/engine/arg_utils.py` at v0.32.0 |
| `vllm/envs.py` | `vllm/envs.py` at v0.32.0 |
| `vllm/entrypoints/launchers/cli_args.py` | `vllm/entrypoints/launchers/cli_args.py` at v0.32.0 (the frontend flags moved here from `entrypoints/openai/cli_args.py`) |
| `vllm/__init__.py` | empty: the package marker only |
| `vllm-0.32.0+cpu.dist-info/METADATA` | the two header lines of the `vllm-openai-cpu:v0.32.0` image's metadata |

`help/` holds the help output of the pinned images, run with no device:

| File | Command |
|---|---|
| `vllm-0.32.0-cpu-serve-help-all.txt` | `vllm/vllm-openai-cpu:v0.32.0` `vllm serve --help=all` |
| `llama-server-b11960-help.txt` | `ghcr.io/ggml-org/llama.cpp:server-b11960` `llama-server --help` (commit 8642541b255bfb5dc39cd351a178be09013a55d6) |
| `ovms-2026.4.1-help.txt` | `openvino/model_server:2026.4.1-gpu` `ovms --help` |
