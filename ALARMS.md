# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-19T20:59:50Z. 6 open.

## `red:bot-sweep.yml`: scheduled run failure

Opened 2026-11-19T20:59:24Z, updated 2026-11-19T20:59:24Z.

The latest scheduled run of `bot-sweep.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/44529477567
Conclusion: **failure**, created 2026-11-19T13:02:11Z, commit 35326ee0ac3ef313e867b79847aa086ccb4e8355
Failed jobs: Dispatch Freshness for newly fixed known issues, CI on bot PRs, Merge one auto-eligible bot PR, batch the release
Failed log unavailable: gh run view 44529477567 -R 88plug/k3d-gpu --log-failed: exit 1: log not found: 135578680612

Re-dispatched `bot-sweep.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=44529477567 -->

## `red:canary.yml`: scheduled run failure

Opened 2026-11-19T20:59:37Z, updated 2026-11-19T20:59:37Z.

The latest scheduled run of `canary.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/44519729166
Conclusion: **failure**, created 2026-11-19T11:59:37Z, commit 35326ee0ac3ef313e867b79847aa086ccb4e8355
Failed jobs: Plan
Failed log unavailable: gh run view 44519729166 -R 88plug/k3d-gpu --log-failed: exit 1: log not found: 135544977538

Re-dispatched `canary.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=44519729166 -->

## `red:freshness.yml`: scheduled run failure

Opened 2026-11-12T07:37:06Z, updated 2026-11-19T20:59:50Z, acknowledged.

The latest scheduled run of `freshness.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/44481223810
Conclusion: **failure**, created 2026-11-19T07:38:51Z, commit 35326ee0ac3ef313e867b79847aa086ccb4e8355
Failed jobs: Plan, Dashboard
Failed log unavailable: gh run view 44481223810 -R 88plug/k3d-gpu --log-failed: exit 1: log not found: 135413647698

Re-dispatched `freshness.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=44481223810 -->

## `stale:bot-sweep.yml`: no successful scheduled run within 7 h

Opened 2026-11-19T20:59:17Z, updated 2026-11-19T20:59:17Z.

`bot-sweep.yml` (cron `13 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43205906938 at 2026-11-12T11:04:16Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

## `stale:canary.yml`: no successful scheduled run within 30 h

Opened 2026-11-19T20:59:30Z, updated 2026-11-19T20:59:30Z.

`canary.yml` (cron `29 3 * * *`, every 24 h) has no successful scheduled run within 30 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43211086595 at 2026-11-12T11:39:55Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

## `stale:freshness.yml`: no successful scheduled run within 12 h

Opened 2026-11-12T09:33:09Z, updated 2026-11-12T09:33:09Z, acknowledged.

`freshness.yml` (cron `23 */6 * * *`, every 6 h) has no successful scheduled run within 12 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/43089875836 at 2026-11-11T19:43:39Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

