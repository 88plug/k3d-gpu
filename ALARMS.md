# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-09T12:07:27Z. 1 open.

## `stale:watchdog.yml`: no successful scheduled run within 7 h

Opened 2026-11-09T12:07:27Z, updated 2026-11-09T12:07:27Z.

`watchdog.yml` (cron `47 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/42553847042 at 2026-11-09T02:59:04Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

