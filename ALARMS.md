# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-07T10:15:58Z. 3 open.

## `red:bot-sweep.yml`: scheduled run failure

Opened 2026-11-07T10:06:03Z, updated 2026-11-07T10:06:03Z.

The latest scheduled run of `bot-sweep.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/42362378174
Conclusion: **failure**, created 2026-11-07T10:04:27Z, commit 07448438459f7cc485de3958293311620acb35fd
Failed jobs: CI on bot PRs
Hint: no known signature matched

<details><summary>Last 17 lines of the failed log</summary>

```text
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines ﻿2026-11-07T10:04:51.2301671Z ##[group]Run hack/bot-ci-dispatch --workflow canary.yml
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:04:51.2302044Z ^[[36;1mhack/bot-ci-dispatch --workflow canary.yml^[[0m
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:04:51.2347510Z shell: /usr/bin/bash -e {0}
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:04:51.2347730Z env:
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:04:51.2349842Z   GH_TOKEN: ***
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:04:51.2350167Z ##[endgroup]
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:04:58.4379582Z #314 autobump/digests 723fa08: error: hack/pins policy failed: hack/pins: base c27d42a722aa2087bdcfc9db8f0fbd30ddbc0922: eas-release: site integrations/eas/pins.json ^    \{"profile": "release"
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:04:59.6334343Z #315 autobump/cuda-test 4e59d2e: error: hack/pins policy failed: hack/pins: base c27d42a722aa2087bdcfc9db8f0fbd30ddbc0922: eas-release: site integrations/eas/pins.json ^    \{"profile": "releas
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:05:00.7581521Z #317 autobump/examples 4a0ff4b: error: hack/pins policy failed: hack/pins: base db6de051cba794e3c86a3f030437b13c6c448c63: eas-release: site integrations/eas/pins.json ^    \{"profile": "release
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:05:01.9284297Z #318 autobump/hf-console-fallback f44153c: error: hack/pins policy failed: hack/pins: base db6de051cba794e3c86a3f030437b13c6c448c63: eas-release: site integrations/eas/pins.json ^    \{"profile
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:05:02.3420899Z #320 autobump/host-intel e7d9a95: canary.yml run 42349582916 at the head is completed/success; nothing to do
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:05:03.4828960Z #321 autobump/images 24353c7: error: hack/pins policy failed: hack/pins: base db6de051cba794e3c86a3f030437b13c6c448c63: eas-release: site integrations/eas/pins.json ^    \{"profile": "release",
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:05:03.8797017Z #322 autobump/k3s b71f0d5: canary.yml run 42350061542 at the head is completed/success; nothing to do
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:05:04.5076820Z #324 autobump/opt-in-manifests 7ba4b20: canary.yml run 42352213796 at the head is completed/success; nothing to do
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:05:05.6041775Z #325 autobump/packaging-images 32ad2af: error: hack/pins policy failed: hack/pins: base db6de051cba794e3c86a3f030437b13c6c448c63: eas-release: site integrations/eas/pins.json ^    \{"profile": 
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:05:06.1828694Z #326 autobump/vllm-xpu eb361c7: canary.yml run 42353148072 at the head is completed/success; nothing to do
CI on bot PRs Dispatch the canary on bot PRs whose policy maps canary engines 2026-11-07T10:05:06.1853792Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `bot-sweep.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=42362378174 -->

## `stale:bot-sweep.yml`: no successful scheduled run within 7 h

Opened 2026-11-07T09:55:45Z, updated 2026-11-07T09:55:45Z.

`bot-sweep.yml` (cron `13 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/42330940471 at 2026-11-07T02:47:20Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

## `stale:watchdog.yml`: no successful scheduled run within 7 h

Opened 2026-11-07T10:15:58Z, updated 2026-11-07T10:15:58Z.

`watchdog.yml` (cron `47 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/42333017954 at 2026-11-07T03:13:42Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

