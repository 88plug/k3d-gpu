# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-07T00:20:52Z. 2 open.

## `red:bot-sweep.yml`: scheduled run failure

Opened 2026-11-07T00:20:52Z, updated 2026-11-07T00:20:52Z.

The latest scheduled run of `bot-sweep.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/42309600332
Conclusion: **failure**, created 2026-11-06T23:06:34Z, commit 8e10b0943d8f4c841de688ecfe977151f3c7ab2c
Failed jobs: Merge one auto-eligible bot PR, batch the release
Hint: no known signature matched

<details><summary>Last 49 lines of the failed log</summary>

```text
Merge one auto-eligible bot PR, batch the release Merge, then batch the release ﻿2026-11-06T23:27:01.3280314Z ##[group]Run hack/bot-merge
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:27:01.3280664Z ^[[36;1mhack/bot-merge^[[0m
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:27:01.3333801Z shell: /usr/bin/bash -e {0}
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:27:01.3334083Z env:
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:27:01.3336648Z   GH_TOKEN: ***
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:27:01.3336864Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:27:03.6701183Z the '88plug/k3d-gpu' repository has disabled issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:27:03.6741990Z circuit breaker open: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:27:03.6862631Z bot merges today (2026-11-06 UTC): 0 of 6
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:26.2376876Z PR#311 blocked: CI is pending at this head
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:26.2378240Z PR#311 blocked: needs review: policy: eat-stock: eat release-1.1.0 -> release-2.0.0 (major) (always review); an approval of 4b7c8eb84e7a9d6d1b8fba6b677fa6e267f1584f by a maintainer merges it
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:26.2379810Z PR#311 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:27.2201460Z PR#310 blocked: CI failed at this head
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:27.2203378Z PR#310 blocked: needs review: policy: minor: cuda-test-image 13.4.3 -> 13.5.0 (minor) is review for family base-image; an approval of a6f51937fac609debfa6ee281f7786f8b6b005d2 by a maintainer 
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:27.2205304Z PR#310 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:27.4602286Z PR#309 blocked: CI is pending at this head
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:27.4603773Z PR#309 blocked: needs review: policy: ledger-defaults: llama-cpp v0.8.0 -> v0.9.0 (minor) moves llama.cpp, which H57dm, H59 measured; an approval of a38920cd82a9596ba31e040836d58aaae49b121a b
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:27.4605546Z PR#309 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:28.4456080Z PR#308 blocked: CI is pending at this head
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:28.4456826Z PR#308 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:29.4708360Z PR#307 blocked: CI is pending at this head
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:29.4710606Z PR#307 blocked: needs review: policy: auto: image:packaging-images:debian:trixie-slim trixie-slim -> trixie-slim (digest), family base-image; auto: image:packaging-images:ghcr.io/homebrew/bre
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:29.4713676Z PR#307 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:30.5482169Z PR#306 blocked: canary run concluded failure (https://github.com/88plug/k3d-gpu/actions/runs/41401470053)
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:30.5482858Z PR#306 blocked: canary vllm-xpu: fail
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:30.5483155Z PR#306 blocked: CI failed at this head
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:30.5483870Z PR#306 blocked: needs review: policy: major: vllm-xpu v0.32.0 -> v0.33.0 (major); an approval of 7f9ea5e7e52f88db39496ec8a269be13da62474c by a maintainer merges it
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:30.5484665Z PR#306 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:30.8500809Z PR#305 blocked: needs review: policy: major: ci-chrome 156.0.8078.73 -> 157.0.8103.38 (major); an approval of 865ae4d15feb9c11a25b42147a9230ec650f2099 by a maintainer merges it
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:30.8501722Z PR#305 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:31.8894600Z PR#304 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:32.7787162Z PR#303 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:33.0097159Z PR#302 blocked: CI failed at this head
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:33.0098623Z PR#302 blocked: needs review: policy: auto: agent-sandbox v1.0.8 -> v1.0.9 (patch), family opt-in; major: open-webui v0.12.2 -> v0.13.0 (major); an approval of e0b3e74aea2d508b9b19d0b8e2304a0
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:33.0100789Z PR#302 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:33.3681803Z PR#301 blocked: CI failed at this head
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:33.3683778Z PR#301 blocked: needs review: policy: tag line: image:images:busybox:1.37 1.37 -> 1.38 (line) is review for family base-image; auto: image:images:ollama/ollama:0.40.13 0.40.13 -> 0.40.15 (pat
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:33.3685997Z PR#301 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:34.4914848Z PR#300 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:34.8127535Z PR#299 blocked: CI failed at this head
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:34.8128174Z PR#299 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:35.0124661Z PR#298 blocked: CI failed at this head
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:35.0125365Z PR#298 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:36.2072495Z PR#297 blocked: needs review: policy: revision: nixpkgs 08d3a97586f6b6343b3b5e799980773629432610 -> c56a87d8e41ce69bd2af57eb35dcc117a9dfca33 (revision) is review for family tools; an approval
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:36.2074994Z PR#297 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:37.3344266Z PR#296 blocked: needs review: policy: auto: examples-crewai 1.15.33 -> 1.15.36 (patch), family pypi; auto: examples-langchain-core 1.7.3 -> 1.7.6 (patch), family pypi; auto: examples-langchai
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:37.3347815Z PR#296 blocked: circuit breaker: cannot read ci-alarm issues
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:38.4239596Z release: no bot merge above the last aur-v* tag
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-06T23:30:38.4352099Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `bot-sweep.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=42309600332 -->

## `stale:bot-sweep.yml`: no successful scheduled run within 7 h

Opened 2026-11-07T00:20:46Z, updated 2026-11-07T00:20:46Z.

`bot-sweep.yml` (cron `13 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/41342760056 at 2026-11-01T19:38:34Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

