# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-10T20:48:47Z. 1 open.

## `bot-pr-stuck`: bot PRs open over 72 h without green CI

Opened 2026-11-10T11:43:16Z, updated 2026-11-10T19:59:58Z, acknowledged.

These autobump PRs are open over 72 h and their head SHA has no successful ci.yml run:

- https://github.com/88plug/k3d-gpu/pull/346 chore(cuda-test): cuda-test-image 13.5.0 (`autobump/cuda-test`, open since 2026-11-07T11:15:39Z): ci.yml at 39da25b1ec3c: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/350 chore(hf-console-fallback): hf-gemma4-26b-int4-ov 7b3cbc98e24a9946e561f82c752ae26cd53fc660, hf-qwen3.8-27b 37561816e66b5b9153b1b64c10968c18c1e6f4a0, hf-qwen3.8-27b-int4-ov d15f4d91eccd11dd44f615ffdb539c61bf1f1e6e (`autobump/hf-console-fallback`, open since 2026-11-07T11:16:40Z): ci.yml at 48596f4552a6: pull_request action_required

A PR that GITHUB_TOKEN opened or updated gets approval-held CI runs (action_required): approve them, or dispatch ci.yml on the branch.

