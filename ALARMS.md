# Open ci-alarms

This repository has Issues disabled, so hack/ci_alarm keeps its alarms on branch `ci-status`:
alarms.json, rendered here. A new alarm, or a new text, notifies @88plug by a commit comment
on main (again every 24 h while it stays open), and the `ci-alarm` commit status on main's newest
commit is red while any alarm is open. `hack/ci_alarm ack <key>` marks one read.

Updated 2026-11-08T10:45:49Z. 3 open.

## `red:bot-sweep.yml`: scheduled run failure

Opened 2026-11-08T07:21:21Z, updated 2026-11-08T10:45:39Z.

The latest scheduled run of `bot-sweep.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/42458223055
Conclusion: **failure**, created 2026-11-08T07:18:08Z, commit 172655f6d8c852c1c5c8d5c27f30d3c3d2e64466
Failed jobs: Merge one auto-eligible bot PR, batch the release
Hint: no known signature matched

<details><summary>Last 60 lines of the failed log</summary>

```text
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6458665Z  * [new tag]           aur-v0.1.91                 -> aur-v0.1.91
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6460168Z  * [new tag]           aur-v0.1.92                 -> aur-v0.1.92
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6535492Z  * [new tag]           aur-v0.1.93                 -> aur-v0.1.93
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6537575Z  * [new tag]           aur-v0.1.94                 -> aur-v0.1.94
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6539403Z  * [new tag]           aur-v0.1.95                 -> aur-v0.1.95
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6541520Z  * [new tag]           v1.34.1-k3s1-amd64-ndpv0.19.2 -> v1.34.1-k3s1-amd64-ndpv0.19.2
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6544162Z  * [new tag]           v1.34.1-k3s1-amd64-ndpv0.19.3 -> v1.34.1-k3s1-amd64-ndpv0.19.3
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6546711Z  * [new tag]           v1.34.1-k3s1-amd64-ndpv0.20.0 -> v1.34.1-k3s1-amd64-ndpv0.20.0
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6549241Z  * [new tag]           v1.34.1-k3s1-amd64-ndpv0.20.1 -> v1.34.1-k3s1-amd64-ndpv0.20.1
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6551626Z  * [new tag]           v1.37.1-k3s1-ndpv0.20.1     -> v1.37.1-k3s1-ndpv0.20.1
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6555132Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6556468Z ##[group]Determining the checkout info
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6557827Z [command]/usr/bin/git branch --list --remote origin/main
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6559268Z   origin/main
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6561518Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6562589Z [command]/usr/bin/git sparse-checkout disable
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6565321Z [command]/usr/bin/git config --local --unset-all extensions.worktreeConfig
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6568154Z ##[group]Checking out the ref
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:56.6569680Z [command]/usr/bin/git checkout --progress --force -B main refs/remotes/origin/main
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2812496Z Switched to a new branch 'main'
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2816049Z branch 'main' set up to track 'origin/main'.
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2839269Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2887597Z [command]/usr/bin/git log -1 --format=%H
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2921570Z 172655f6d8c852c1c5c8d5c27f30d3c3d2e64466
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2929404Z ##[group]Removing auth
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2929749Z Removing SSH command configuration
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2932624Z [command]/usr/bin/git config --local --name-only --get-regexp core\.sshCommand
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.2965354Z [command]/usr/bin/git submodule foreach --recursive sh -c "git config --local --name-only --get-regexp 'core\.sshCommand' && git config --local --unset-all 'core.sshCommand' || :"
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3225966Z Removing HTTP extra header
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3228941Z [command]/usr/bin/git config --local --name-only --get-regexp http\.https\:\/\/github\.com\/\.extraheader
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3260838Z [command]/usr/bin/git submodule foreach --recursive sh -c "git config --local --name-only --get-regexp 'http\.https\:\/\/github\.com\/\.extraheader' && git config --local --unset-all 'http.https://github.com/
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3518360Z Removing includeIf entries pointing to credentials config files
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3521037Z [command]/usr/bin/git config --local --name-only --get-regexp ^includeIf\.gitdir:
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3547801Z includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3548546Z includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git/worktrees/*.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3548939Z includeif.gitdir:/github/workspace/.git.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3549240Z includeif.gitdir:/github/workspace/.git/worktrees/*.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3555417Z [command]/usr/bin/git config --local --get-all includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3581487Z /home/runner/work/_temp/git-credentials-c49fd94f-2b91-4167-b56a-682ddfd240e1.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3588842Z [command]/usr/bin/git config --local --unset includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git.path \/home\/runner\/work\/_temp\/git\-credentials\-c49fd94f\-2b91\-4167\-b56a\-682ddfd240e1\.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3621752Z [command]/usr/bin/git config --local --get-all includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git/worktrees/*.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3645529Z /home/runner/work/_temp/git-credentials-c49fd94f-2b91-4167-b56a-682ddfd240e1.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3652435Z [command]/usr/bin/git config --local --unset includeif.gitdir:/home/runner/work/k3d-gpu/k3d-gpu/.git/worktrees/*.path \/home\/runner\/work\/_temp\/git\-credentials\-c49fd94f\-2b91\-4167\-b56a\-682ddfd240e1\.c
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3683975Z [command]/usr/bin/git config --local --get-all includeif.gitdir:/github/workspace/.git.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3710516Z /github/runner_temp/git-credentials-c49fd94f-2b91-4167-b56a-682ddfd240e1.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3716784Z [command]/usr/bin/git config --local --unset includeif.gitdir:/github/workspace/.git.path \/github\/runner_temp\/git\-credentials\-c49fd94f\-2b91\-4167\-b56a\-682ddfd240e1\.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3750399Z [command]/usr/bin/git config --local --get-all includeif.gitdir:/github/workspace/.git/worktrees/*.path
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3772980Z /github/runner_temp/git-credentials-c49fd94f-2b91-4167-b56a-682ddfd240e1.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3778579Z [command]/usr/bin/git config --local --unset includeif.gitdir:/github/workspace/.git/worktrees/*.path \/github\/runner_temp\/git\-credentials\-c49fd94f\-2b91\-4167\-b56a\-682ddfd240e1\.config
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.3811674Z [command]/usr/bin/git submodule foreach --recursive git config --local --show-origin --name-only --get-regexp remote.origin.url
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4074040Z Removing credentials config '/home/runner/work/_temp/git-credentials-c49fd94f-2b91-4167-b56a-682ddfd240e1.config'
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4078161Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4280987Z ##[group]Run hack/bot-merge
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4281250Z ^[[36;1mhack/bot-merge^[[0m
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4329342Z shell: /usr/bin/bash -e {0}
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4329558Z env:
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4331631Z   GH_TOKEN: ***
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.4331810Z ##[endgroup]
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.6937103Z gh pr merge lacks --match-head-commit; refusing to merge unpinned
Merge one auto-eligible bot PR, batch the release UNKNOWN STEP 2026-11-08T07:20:57.6966885Z ##[error]Process completed with exit code 2.
```
</details>

Re-dispatched `bot-sweep.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=42458223055 -->

## `red:canary.yml`: scheduled run failure

Opened 2026-11-08T10:45:49Z, updated 2026-11-08T10:45:49Z.

The latest scheduled run of `canary.yml` failed.

Run: https://github.com/88plug/k3d-gpu/actions/runs/42472404707
Conclusion: **failure**, created 2026-11-08T10:40:19Z, commit 172655f6d8c852c1c5c8d5c27f30d3c3d2e64466
Failed jobs: Node image, Report
Hint: no known signature matched

<details><summary>Last 16 lines of the failed log</summary>

```text
Node image canary/node.sh ﻿2026-11-08T10:40:32.7967645Z ##[group]Run canary/node.sh
Node image canary/node.sh 2026-11-08T10:40:32.7968019Z ^[[36;1mcanary/node.sh^[[0m
Node image canary/node.sh 2026-11-08T10:40:32.8016405Z shell: /usr/bin/bash -e {0}
Node image canary/node.sh 2026-11-08T10:40:32.8016639Z env:
Node image canary/node.sh 2026-11-08T10:40:32.8016857Z   CANARY_OUT_DIR: /home/runner/work/_temp/canary-out
Node image canary/node.sh 2026-11-08T10:40:32.8017136Z ##[endgroup]
Node image canary/node.sh 2026-11-08T10:40:32.8776783Z canary node: docker buildx build --load -t k3d-gpu-canary:node .
Node image canary/node.sh 2026-11-08T10:45:02.2654305Z canary node: fail: k3d v5.9.0 did not download
Node image canary/node.sh 2026-11-08T10:45:02.2707425Z ##[error]Process completed with exit code 1.
Report Green only when every selected engine passed ﻿2026-11-08T10:45:15.7777699Z ##[group]Run exit "${RC:-1}"
Report Green only when every selected engine passed 2026-11-08T10:45:15.7778050Z ^[[36;1mexit "${RC:-1}"^[[0m
Report Green only when every selected engine passed 2026-11-08T10:45:15.7822229Z shell: /usr/bin/bash -e {0}
Report Green only when every selected engine passed 2026-11-08T10:45:15.7822555Z env:
Report Green only when every selected engine passed 2026-11-08T10:45:15.7822801Z   RC: 1
Report Green only when every selected engine passed 2026-11-08T10:45:15.7823048Z ##[endgroup]
Report Green only when every selected engine passed 2026-11-08T10:45:15.7890088Z ##[error]Process completed with exit code 1.
```
</details>

Re-dispatched `canary.yml` on main once after this run; a further re-dispatch happens only after a new failed scheduled run.
<!-- watchdog:dispatched=42472404707 -->

## `stale:bot-sweep.yml`: no successful scheduled run within 7 h

Opened 2026-11-08T10:45:31Z, updated 2026-11-08T10:45:31Z.

`bot-sweep.yml` (cron `13 * * * *`, every 1 h) has no successful scheduled run within 7 h (period + 6 h).

Last successful scheduled run: https://github.com/88plug/k3d-gpu/actions/runs/42430348862 at 2026-11-08T00:34:19Z

A scheduled run that never starts leaves no red run: check the workflow is enabled and that its last runs were not skipped.

