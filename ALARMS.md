# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-10T18:21:11Z. 1 open.

## `bot-pr-stuck`: bot PRs open over 72 h without green CI

Opened 2026-11-10T11:43:16Z, updated 2026-11-10T13:00:42Z.

These autobump PRs are open over 72 h and their head SHA has no successful ci.yml run:

- https://github.com/88plug/k3d-gpu/pull/350 chore(hf-console-fallback): hf-gemma4-26b-int4-ov 7b3cbc98e24a9946e561f82c752ae26cd53fc660, hf-qwen3.8-27b 37561816e66b5b9153b1b64c10968c18c1e6f4a0, hf-qwen3.8-27b-int4-ov d15f4d91eccd11dd44f615ffdb539c61bf1f1e6e (`autobump/hf-console-fallback`, open since 2026-11-07T11:16:40Z): ci.yml at 278d1ea3890c: pull_request action_required, workflow_dispatch failure
- https://github.com/88plug/k3d-gpu/pull/351 chore(host-intel): compute-runtime 26.40.40196.8, level-zero 1.35.0, linux-tools 7.3.3 (`autobump/host-intel`, open since 2026-11-07T11:17:55Z): ci.yml at e073315b119b: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/352 chore(images): image:images:ollama/ollama:0.40.13 0.40.16, image:images:busybox:1.37 1.38 (`autobump/images`, open since 2026-11-07T11:18:32Z): ci.yml at c97ffcc58e64: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/353 chore(k3s): k3s v1.37.2-k3s1 (`autobump/k3s`, open since 2026-11-07T11:18:46Z): ci.yml at 176c27676cfc: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/354 chore(opt-in-manifests): open-webui v0.13.3, agent-sandbox v1.0.10 (`autobump/opt-in-manifests`, open since 2026-11-07T11:19:45Z): ci.yml at b75d42e37dfb: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/355 chore(packaging-images): packaging-fedora 45, image:packaging-images:debian:trixie-slim trixie-slim, image:packaging-images:ghcr.io/homebrew/brew:7.0.11 7.0.13 and 1 more (`autobump/packaging-images`, open since 2026-11-07T11:20:13Z): ci.yml at 0f906f41238b: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/357 chore(vllm-xpu): vllm-xpu v0.33.0 (`autobump/vllm-xpu`, open since 2026-11-07T11:20:36Z): ci.yml at 13bc808aceae: pull_request action_required
- https://github.com/88plug/k3d-gpu/pull/365 chore(nri): nri-balloons v0.15.1 (`autobump/nri`, open since 2026-11-07T12:30:50Z): ci.yml at d71288fc77d6: pull_request action_required

A PR that GITHUB_TOKEN opened or updated gets approval-held CI runs (action_required): approve them, or dispatch ci.yml on the branch.

