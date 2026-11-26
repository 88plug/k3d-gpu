# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-26T22:09:57Z. 3 open.

## `bot-pr-stuck`: bot PRs open over 72 h without green CI

Opened 2026-11-22T21:49:57Z, updated 2026-11-26T22:09:57Z.

These autobump PRs are open over 72 h and their head SHA has no successful ci.yml run:

- https://github.com/88plug/k3d-gpu/pull/440 chore(examples): examples-crewai 1.15.45, examples-langgraph 1.2.24, examples-langchain-openai 1.8.5 and 8 more (`autobump/examples`, open since 2026-11-19T21:11:34Z): ci.yml at d7dcc1a814de: pull_request action_required, workflow_dispatch in_progress
- https://github.com/88plug/k3d-gpu/pull/443 chore(host-intel): compute-runtime 26.44.40734.8, level-zero 1.36.0, npu-driver 1.39.1 and 1 more (`autobump/host-intel`, open since 2026-11-19T21:13:04Z): ci.yml at 995dfba92bea: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/449 chore(vllm-cpu): vllm-cpu v0.34.0 (`autobump/vllm-cpu`, open since 2026-11-19T21:18:03Z): ci.yml at e57476f8f846: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/450 chore(vllm-recipes): vllm-recipes 1736d0812625149c303bf071641aef44532b9935 (`autobump/vllm-recipes`, open since 2026-11-19T21:18:51Z): ci.yml at c1f5608f5a4b: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/451 chore(vllm-xpu): vllm-xpu v0.34.0 (`autobump/vllm-xpu`, open since 2026-11-19T21:19:18Z): ci.yml at 969edfef1241: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/454 chore(gguf-convert-pip): gguf-transformers 5.24.1, gguf-huggingface-hub 2.5.0 (`autobump/gguf-convert-pip`, open since 2026-11-21T19:44:25Z): ci.yml at 6cc9b48bb13f: pull_request action_required

A PR that GITHUB_TOKEN opened or updated gets approval-held CI runs (action_required): approve them, or dispatch ci.yml on the branch.

## `ci-missing`: main's HEAD has no ci.yml run

Opened 2026-11-21T07:45:23Z, updated 2026-11-21T07:45:23Z.

main's HEAD `4d2f2e7209f39de6fcb39e2883860d252f41ae06` (committed 2026-11-21T03:24:33Z) has no ci.yml run from a push or workflow_dispatch, 2 h after the commit.

A push made with GITHUB_TOKEN starts no workflow; the watchdog dispatched ci.yml on main once when HEAD was 30 min old. Dispatch it: `gh workflow run ci.yml --ref main`.

## `stale:watchdog.yml`: no successful scheduled run within 7 h

Opened 2026-11-26T22:09:40Z, updated 2026-11-26T22:09:40Z.

`watchdog.yml` (cron `47 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/45826757944 at 2026-11-26T13:11:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

