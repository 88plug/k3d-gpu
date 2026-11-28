# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-28T12:56:17Z. 4 open.

## `bot-pr-stuck`: bot PRs open over 72 h without green CI

Opened 2026-11-22T21:49:57Z, updated 2026-11-28T12:56:17Z.

These autobump PRs are open over 72 h and their head SHA has no successful ci.yml run:

- https://github.com/88plug/k3d-gpu/pull/449 chore(vllm-cpu): vllm-cpu v0.34.0 (`autobump/vllm-cpu`, open since 2026-11-19T21:18:03Z): ci.yml at e57476f8f846: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/451 chore(vllm-xpu): vllm-xpu v0.34.0 (`autobump/vllm-xpu`, open since 2026-11-19T21:19:18Z): ci.yml at 969edfef1241: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/455 chore(nri): nri-balloons v0.15.2 (`autobump/nri`, open since 2026-11-24T00:22:01Z): ci.yml at e6621bce36f7: pull_request action_required, workflow_dispatch failure

A PR that GITHUB_TOKEN opened or updated gets approval-held CI runs (action_required): approve them, or dispatch ci.yml on the branch.

## `ci-missing`: main's HEAD has no ci.yml run

Opened 2026-11-21T07:45:23Z, updated 2026-11-21T07:45:23Z.

main's HEAD `4d2f2e7209f39de6fcb39e2883860d252f41ae06` (committed 2026-11-21T03:24:33Z) has no ci.yml run from a push or workflow_dispatch, 2 h after the commit.

A push made with GITHUB_TOKEN starts no workflow; the watchdog dispatched ci.yml on main once when HEAD was 30 min old. Dispatch it: `gh workflow run ci.yml --ref main`.

## `stale:bot-sweep.yml`: no successful scheduled run within 7 h

Opened 2026-11-28T12:55:46Z, updated 2026-11-28T12:55:46Z.

`bot-sweep.yml` (cron `13 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/46104999150 at 2026-11-28T03:53:46Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

## `stale:watchdog.yml`: no successful scheduled run within 7 h

Opened 2026-11-28T12:55:54Z, updated 2026-11-28T12:55:54Z.

`watchdog.yml` (cron `47 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/46109960983 at 2026-11-28T04:58:32Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

