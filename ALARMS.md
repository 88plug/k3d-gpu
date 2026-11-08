# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-08T07:21:21Z. 1 open.

## `red:bot-sweep.yml`: scheduled run failure

Opened 2026-11-08T07:21:21Z, updated 2026-11-08T07:21:21Z.

The latest scheduled run of `bot-sweep.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/42458223055
Conclusion: **failure**, created 2026-11-08T07:18:08Z, commit 172655f6d8c852c1c5c8d5c27f30d3c3d2e64466
Failed jobs: Merge one auto-eligible bot PR, batch the release
Hint: no known signature matched

<details><summary>Last 8 lines of the failed log</summary>

```text
Merge one auto-eligible bot PR, batch the release Merge, then batch the release ﻿2026-11-08T07:20:57.4280977Z ##[group]Run hack/bot-merge
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-08T07:20:57.4281248Z ^[[36;1mhack/bot-merge^[[0m
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-08T07:20:57.4329325Z shell: /usr/bin/bash -e {0}
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-08T07:20:57.4329556Z env:
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-08T07:20:57.4331621Z   GH_TOKEN: ***
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-08T07:20:57.4331808Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-08T07:20:57.6937062Z gh pr merge lacks --match-head-commit; refusing to merge unpinned
Merge one auto-eligible bot PR, batch the release Merge, then batch the release 2026-11-08T07:20:57.6966867Z ##[error]Process completed with exit code 2.
```
</details>

Re-dispatched `bot-sweep.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=42458223055 -->

